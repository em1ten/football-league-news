"""Fetch EFL news and write articles.json.

Two paths, deliberately kept separate:

1. CLUB_FEEDS -- official club RSS / BBC team feeds. These are already
   scoped by URL, so the club tag comes from the feed config, not from
   text matching. Exact by construction. Fetched every run.

2. Rotation -- league-level and general feeds (Google News per division,
   BBC EFL). These need tag_clubs.match_clubs() because the club isn't
   known ahead of time. Only this run's slice is fetched (see
   tag_clubs.clubs_for_run), so the whole set still refreshes within
   ~45 minutes on a 15-minute cadence without blowing the request budget.

Run with: python3 fetch_news.py
Needs: feedparser, requests  (both -- see note in tag_clubs.py's sibling
README / the KT doc: feedparser alone silently breaks the fixtures job)
"""

import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

from tag_clubs import match_clubs, clubs_for_run, division_of, _BY_SLUG

OUT = Path(__file__).with_name("articles.json")
MAX_AGE_DAYS = 4
REQUEST_TIMEOUT = 10
USER_AGENT = "EFLFeedBot/1.0 (+https://example.invalid)"

# Fill in as you find real feed URLs. Anything not listed here for a club
# just relies on the rotation + text-matching path instead -- that's fine,
# it's the fallback the rotation path exists for.
CLUB_FEEDS = {
    # "sheffield-wednesday": ["https://www.swfc.co.uk/news/rss"],
    # "leicester-city": ["https://www.lcfc.com/rss/news"],
}

# One Google News query per division, cheap and league-wide.
DIVISION_FEEDS = {
    "championship": "https://news.google.com/rss/search?q=EFL+Championship+when:1d&hl=en-GB&gl=GB",
    "league-one": "https://news.google.com/rss/search?q=EFL+League+One+when:1d&hl=en-GB&gl=GB",
    "league-two": "https://news.google.com/rss/search?q=EFL+League+Two+when:1d&hl=en-GB&gl=GB",
}

# General per-club query, only fetched for this run's rotation slice.
CLUB_QUERY_TMPL = "https://news.google.com/rss/search?q=%22{name}%22+when:1d&hl=en-GB&gl=GB"


def _parse(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


_TAG_RE = re.compile(r"<[^>]+>")


def clean_excerpt(raw):
    """Google News RSS summaries embed raw HTML -- an <a> link back to the
    article plus a <font> tag naming the source. Strip tags and decode
    entities so what's stored is plain text, not markup. Doing this once
    here (not at render time) means every downstream consumer -- build_site,
    feed.xml, any future client -- gets clean data automatically."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def split_google_news_title(title):
    """Google News RSS item titles are formatted "{headline} - {publisher}"
    per article -- e.g. "Barnsley v Stevenage: stats - BBC". The channel
    title (used elsewhere for the search query itself) is NOT this; it's
    the same for every item in the feed. This extracts the real per-article
    publisher, which matters for two reasons: (1) it's what the person
    actually sees as the source on each card, rather than a blanket
    "Google News" for every single article regardless of who wrote it,
    and (2) every source-based quality filter (gambling-tip sites,
    stream-spam domains, homonym-noise outlets) keys off this -- without
    it those filters compare against the literal string "Google News" on
    every item and never fire. Falls back to (title, None) if no plausible
    " - Publisher" suffix is found."""
    if " - " not in title:
        return title, None
    headline, publisher = title.rsplit(" - ", 1)
    publisher = publisher.strip()
    # A plausible publisher name: short, no sentence-ending punctuation
    # that would suggest the split just landed inside a real headline.
    if not publisher or len(publisher) > 40 or publisher.endswith((".", "!", "?")):
        return title, None
    return headline.strip(), publisher


def _entry_to_article(entry, source):
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        published_dt = datetime(*published[:6], tzinfo=timezone.utc)
    else:
        published_dt = datetime.now(timezone.utc)
    # Google News summaries are just the headline + a link + the source
    # name, wrapped in HTML -- zero real content beyond what the "title"
    # and "source" fields already show. Drop the excerpt entirely for that
    # source rather than showing leftover junk. Official feeds (BBC, club
    # RSS) write genuine standalone descriptions, so those are kept.
    if source == "Google News":
        excerpt = ""
    else:
        excerpt = clean_excerpt(entry.get("summary", ""))
    return {
        "title": clean_excerpt(entry.get("title", "")),
        "url": entry.get("link", ""),
        "excerpt": excerpt,
        "source": source,
        "published": published_dt.isoformat(),
    }





def is_empty_excerpt(title, excerpt):
    """For non-Google-News sources (official/BBC feeds), a summary that's
    just the headline repeated carries zero information -- drop it. Kept
    separate from the Google-News-specific blanking above because these
    feeds sometimes write genuine standalone descriptions worth keeping."""
    if not excerpt:
        return True
    stripped = excerpt.replace(title, "").strip(" -\u2013\u2014")
    if len(stripped) < 3:
        return True
    return " " not in stripped and "." in stripped


SOURCE_ALIASES = {
    "bbc.com": "BBC", "bbc.co.uk": "BBC", "BBC Sport": "BBC", "BBC News": "BBC",
    "Sky Sports": "Sky Sports", "skysports.com": "Sky Sports",
    "The Athletic": "The Athletic", "theathletic.com": "The Athletic",
}


# ---------------------------------------------------------- quality filters
#
# Real production output turned up three distinct problems, not one, and
# each needs its own rule rather than one fuzzy "quality" score:
#
#   1. Outright scam/spam sites (illegal-stream link farms) -- block by
#      domain, and by a garbled-unicode heuristic as a backstop for the
#      next site that does the same thing under a different domain.
#   2. Gambling-tip content -- every prediction/odds/betting-tips site
#      posts near-identical filler for every single fixture. Blocked by
#      source name (precise) and by title pattern (catches the rest).
#   3. Homonym false positives -- a club marker like "Middlesbrough" or
#      "Portsmouth" is also a place name, and Google News' per-club query
#      has no football context to disambiguate it. A genuine A66-crash
#      funeral story is not football news just because the town shares a
#      club's name. Only applied to Google-News-sourced content -- official
#      club/BBC feeds are already on-topic by construction.

STREAM_SPAM_DOMAINS = {"rikkyo.ac.jp"}

# Mathematical/fullwidth unicode block used to dodge basic keyword filters
# -- e.g. "𝐋𝐈𝐕𝐄", "Ｌｉｖｅ". Legitimate club/publisher names don't use these.
_GARBLED_UNICODE_RE = re.compile(
    "[\U0001D400-\U0001D7FF\uFF00-\uFFEF]"
)


def is_stream_spam(title, url, source=""):
    # NOTE: Google News RSS <link> is always a news.google.com redirect,
    # never the real publisher's domain -- so a URL-domain check alone
    # never fires for Google-News-sourced items, which is where every
    # real instance of this actually showed up. The source string (parsed
    # from the "- rikkyo.ac.jp" suffix on the title) is the real signal.
    # URL-domain check kept too, in case a future feed ever links direct.
    try:
        domain = url.split("/")[2].lower() if "//" in url else url.lower()
    except Exception:
        domain = ""
    src = (source or "").lower()
    if any(domain == d or domain.endswith("." + d) for d in STREAM_SPAM_DOMAINS):
        return True
    if any(d in src for d in STREAM_SPAM_DOMAINS):
        return True
    if _GARBLED_UNICODE_RE.search(title):
        return True
    return False


GAMBLING_SOURCES = {
    "Sportsgambler", "Oddschecker", "WhoScored.com", "Betshoot",
    "Wincomparator", "Livetipsportal.com", "Dailysports", "APWin",
    "BettingTips4you.com", "William Hill News", "news.bet365.com",
    "Odds Scanner", "TheLines.com", "FootballPredictions.NET",
    "Sporting Life", "Racing Post",
}

_GAMBLING_TITLE_RE = re.compile(
    r"(?i)\bbetting tips?\b|\bbet builder\b|\bprediction[s]?\s*[,&]\s*(betting|odds)|"
    r"\bfree bets?\b|\bacca\b"
)
# "tips" and "odds" co-occurring anywhere in the title is a reliable
# gambling-content signal regardless of word order -- "tips and odds",
# "odds & tips", etc. Found via a real Telegraph headline that the
# order-specific pattern above missed ("...Championship tips and odds").
_TIPS_WORD_RE = re.compile(r"(?i)\btips?\b")
_ODDS_WORD_RE = re.compile(r"(?i)\bodds\b")


def is_gambling_content(title, source):
    if source in GAMBLING_SOURCES:
        return True
    if _GAMBLING_TITLE_RE.search(title):
        return True
    if _TIPS_WORD_RE.search(title) and _ODDS_WORD_RE.search(title):
        return True
    return False


# Words that show up in genuine local/crime/civic news about a place that
# happens to share a club's name, essentially never in football coverage.
# Seeded from real false positives found in production output -- extend
# this list the same way (real example -> add the word) rather than
# guessing ahead of time.
_NON_FOOTBALL_NOISE_RE = re.compile(
    r"(?i)\bobituary\b|\bfuneral\b|\bcrash\b|\bpolice\b|\bshooting\b|"
    r"\bindictment\b|\bgrand jury\b|\bcouncil\b|\bcooling center\b|"
    r"\barrested\b|\bcourt\b|\broadwork\b|\blibrary\b|\bsales tax\b|"
    r"\btrauma unit\b|\bcycling accident\b|\bdead\b|\bdied\b|\bfatal\b|"
    r"\btragedy\b|\bvictims\b|\bgang violence\b|\bmystery\b|"
    r"\bhigh school\b|\bvarsity\b|\bjunior varsity\b|\bprep football\b|"
    r"\bengineer\b|\breservoir\b|\bcivil service\b|\bparking garage\b|"
    r"\btwin city\b|\bheadquarters\b|\bin memory of\b"
)

# Known non-UK local-news outlets that repeatedly surface for homonym
# clubs (Portsmouth OH, Lincoln City OR, Watford City ND, etc.), plus
# general-interest/political outlets that occasionally use a club's town
# name with zero football content. Keyword matching alone misses these
# because they either use genuine "football" vocabulary for the wrong
# sport/place, or use no football vocabulary at all. Seeded the same way
# as the regex above -- from real false positives, extended as new ones
# appear.
HOMONYM_NOISE_SOURCES = {
    "Portsmouth Daily Times", "seacoastonline.com", "WAVY.com", "13newsnow.com",
    "The Columbus Dispatch", "Chillicothe Gazette", "MaxPreps", "News Dakota",
    "Lincoln City Homepage", "newportnewstimes.com", "The Spectator",
}

# Any of these present means it's genuinely about the club, even if a
# noise word also appears (e.g. "boss" news that mentions a "court" case).
_FOOTBALL_CONTEXT_RE = re.compile(
    r"(?i)\bfc\b|\befl\b|championship|league one|league two|\bmatch\b|"
    r"\bboss\b|\bmanager\b|\bstriker\b|\bgoal\b|\btransfer\b|\bsquad\b|"
    r"\bkick-off\b|\bfixture\b|\blineup\b|\bline-up\b|\bstarting xi\b|"
    r"\bderby\b.*\b(win|loss|draw|beat)|\bstadium\b|\bloan\b|\bsigning\b|"
    r"\bwinger\b|\bmidfielder\b|\bdefender\b|\bgoalkeeper\b|\bpromotion\b|"
    r"\brelegation\b|play-?off|\bvs\b|\bv\b\s|\bwednesday\b.*\bfc\b"
)


def is_homonym_noise(title, source=""):
    if source in HOMONYM_NOISE_SOURCES:
        return True
    if not _NON_FOOTBALL_NOISE_RE.search(title):
        return False
    return not _FOOTBALL_CONTEXT_RE.search(title)


def passes_quality_filters(article, from_google_news):
    title, url, source = article["title"], article["url"], article["source"]
    if is_stream_spam(title, url, source):
        return False
    if is_gambling_content(title, source):
        return False
    # Homonym check only applies to text-matched Google News content --
    # official feeds are already scoped by URL so can't be homonym noise.
    if from_google_news and is_homonym_noise(title, source):
        return False
    return True


def dedupe_key(a):
    # URL alone isn't enough: Google News wraps each crawl of the same
    # story in a fresh redirect URL, so the exact same headline from the
    # exact same source can slip past URL-only dedupe repeatedly.
    norm_title = re.sub(r"\s+", " ", a["title"].strip().lower())
    return (norm_title, a["source"])


def normalise_source(name):
    if not name:
        return name
    # Google News RSS channel titles are the raw search query, e.g.
    # '"Wolverhampton Wanderers" when:1d - Google News', not the literal
    # string "Google News". Collapsing them is what makes the excerpt-
    # blanking check below actually fire, and stops every distinct query
    # fragmenting the source label shown on each card.
    if "google news" in name.lower():
        return "Google News"
    return SOURCE_ALIASES.get(name, name)


def fetch_club_feeds():
    articles = []
    for slug, urls in CLUB_FEEDS.items():
        for url in urls:
            try:
                feed = _parse(url)
            except Exception as e:
                print(f"[club-feed] {slug} {url} failed: {e}")
                continue
            for entry in feed.entries:
                a = _entry_to_article(entry, normalise_source(feed.feed.get("title", slug)))
                if is_empty_excerpt(a["title"], a["excerpt"]):
                    a["excerpt"] = ""
                if not passes_quality_filters(a, from_google_news=False):
                    continue
                a["clubs"] = [slug]
                a["division"] = division_of(slug)
                a["scope"] = "club"
                articles.append(a)
    return articles


def fetch_division_feeds():
    articles = []
    for division, url in DIVISION_FEEDS.items():
        try:
            feed = _parse(url)
        except Exception as e:
            print(f"[division-feed] {division} failed: {e}")
            continue
        for entry in feed.entries:
            a = _entry_to_article(entry, normalise_source(feed.feed.get("title", "Google News")))
            # Extract the real per-article publisher from the title suffix
            # BEFORE matching clubs or running quality filters -- both need
            # the real source, and match_clubs needs the publisher name
            # stripped out so a publisher whose own name happens to contain
            # a club marker (e.g. a site literally called "...City...")
            # can't falsely tag the story.
            clean_title, publisher = split_google_news_title(a["title"])
            a["title"] = clean_title
            if publisher:
                a["source"] = normalise_source(publisher)
            if is_empty_excerpt(a["title"], a["excerpt"]):
                a["excerpt"] = ""
            if not passes_quality_filters(a, from_google_news=True):
                continue
            tagged = match_clubs(a["title"], a["excerpt"])
            # Belt-and-braces: only keep clubs that actually belong to this
            # division-tagged feed, in case a cross-division story slipped in.
            tagged_clubs = [s for s in tagged["clubs"] if division_of(s) == division]
            # Untagged (no specific club matched) Google News division
            # content is exactly where the unrelated noise piles up --
            # other leagues, general football-industry chatter, gambling
            # filler that slipped the pattern check. Drop rather than show
            # under a bare "League" label.
            if not tagged_clubs:
                continue
            a["clubs"] = tagged_clubs
            a["division"] = division
            a["scope"] = "club"
            articles.append(a)
    return articles


def fetch_rotation_club_queries(n_slices=3):
    articles = []
    for slug in clubs_for_run(n_slices):
        club = _BY_SLUG[slug]
        if slug in CLUB_FEEDS:
            continue  # already covered directly, no need to double-fetch
        url = CLUB_QUERY_TMPL.format(name=club["name"])
        try:
            feed = _parse(url)
        except Exception as e:
            print(f"[rotation] {slug} failed: {e}")
            continue
        for entry in feed.entries:
            a = _entry_to_article(entry, normalise_source(feed.feed.get("title", "Google News")))
            clean_title, publisher = split_google_news_title(a["title"])
            a["title"] = clean_title
            if publisher:
                a["source"] = normalise_source(publisher)
            if is_empty_excerpt(a["title"], a["excerpt"]):
                a["excerpt"] = ""
            if not passes_quality_filters(a, from_google_news=True):
                continue
            tagged = match_clubs(a["title"], a["excerpt"])
            if slug not in tagged["clubs"]:
                # The per-club query still needs the positive-marker check --
                # a query for "Derby County" can surface an unrelated story
                # that merely contains the word "derby".
                continue
            a["clubs"] = tagged["clubs"]
            a["division"] = division_of(slug)
            a["scope"] = "club"
            articles.append(a)
    return articles


def dedupe(articles):
    seen = set()
    out = []
    for a in articles:
        # URL alone isn't reliable for Google News: it wraps every crawl of
        # the same story in a fresh redirect URL, so the exact same
        # headline from the exact same source repeats past URL-only dedupe.
        # Key on both so a genuine URL match still catches official-feed
        # duplicates, while the title+source key catches Google News repeats.
        keys = {a["url"]} if a["url"] else set()
        keys.add(dedupe_key(a))
        if keys & seen:
            continue
        seen |= keys
        out.append(a)
    return out


def load_previous():
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return []
    now = time.time()
    kept = []
    for a in old:
        try:
            ts = datetime.fromisoformat(a["published"]).timestamp()
        except Exception:
            continue
        if (now - ts) <= MAX_AGE_DAYS * 86400:
            kept.append(a)
    return kept


def main():
    fresh = []
    fresh += fetch_club_feeds()
    fresh += fetch_division_feeds()
    fresh += fetch_rotation_club_queries()

    # Merge-with-previous, not full-replace: a feed going temporarily empty
    # (rate-limited/blocked) shouldn't make stories visibly vanish.
    merged = dedupe(fresh + load_previous())
    merged.sort(key=lambda a: a["published"], reverse=True)

    OUT.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(merged)} articles ({len(fresh)} fresh this run)")


if __name__ == "__main__":
    main()
