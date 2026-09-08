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
# Verified via discover_feeds.py against the real, live URLs -- each one
# was checked to actually parse as a feed with real entries, not just
# guessed. 72/72 clubs found a working feed on the first run. A club
# whose only feed here has very few entries (some BBC per-team feeds
# showed just 4) hasn't been individually content-checked beyond that --
# worth watching once real data flows through, same as any other new
# source in this pipeline.
CLUB_FEEDS = {
    "accrington-stanley": ["https://feeds.bbci.co.uk/sport/football/teams/accrington-stanley/rss.xml"],
    "afc-wimbledon": ["https://feeds.bbci.co.uk/sport/football/teams/afc-wimbledon/rss.xml"],
    "barnet": ["https://feeds.bbci.co.uk/sport/football/teams/barnet/rss.xml"],
    "barnsley": ["https://www.barnsleyfc.co.uk/rss.xml"],
    "birmingham-city": ["https://feeds.bbci.co.uk/sport/football/teams/birmingham-city/rss.xml"],
    "blackburn-rovers": ["https://www.rovers.co.uk/rss.xml"],
    "blackpool": ["https://feeds.bbci.co.uk/sport/football/teams/blackpool/rss.xml"],
    "bolton-wanderers": ["https://feeds.bbci.co.uk/sport/football/teams/bolton-wanderers/rss.xml"],
    "bradford-city": ["https://feeds.bbci.co.uk/sport/football/teams/bradford-city/rss.xml"],
    "bristol-city": ["https://feeds.bbci.co.uk/sport/football/teams/bristol-city/rss.xml"],
    "bristol-rovers": ["https://feeds.bbci.co.uk/sport/football/teams/bristol-rovers/rss.xml"],
    "bromley": ["https://feeds.bbci.co.uk/sport/football/teams/bromley/rss.xml"],
    "burnley": ["https://www.burnleyfootballclub.com/rss"],
    "burton-albion": ["https://feeds.bbci.co.uk/sport/football/teams/burton-albion/rss.xml"],
    "cambridge-united": ["https://feeds.bbci.co.uk/sport/football/teams/cambridge-united/rss.xml"],
    "cardiff-city": ["https://feeds.bbci.co.uk/sport/football/teams/cardiff-city/rss.xml"],
    "charlton-athletic": ["https://feeds.bbci.co.uk/sport/football/teams/charlton-athletic/rss.xml"],
    "cheltenham-town": ["https://feeds.bbci.co.uk/sport/football/teams/cheltenham-town/rss.xml"],
    "chesterfield": ["https://feeds.bbci.co.uk/sport/football/teams/chesterfield/rss.xml"],
    "colchester-united": ["https://feeds.bbci.co.uk/sport/football/teams/colchester-united/rss.xml"],
    "crawley-town": ["https://feeds.bbci.co.uk/sport/football/teams/crawley-town/rss.xml"],
    "crewe-alexandra": ["https://feeds.bbci.co.uk/sport/football/teams/crewe-alexandra/rss.xml"],
    "derby-county": ["https://feeds.bbci.co.uk/sport/football/teams/derby-county/rss.xml"],
    "doncaster-rovers": ["https://feeds.bbci.co.uk/sport/football/teams/doncaster-rovers/rss.xml"],
    "exeter-city": ["https://feeds.bbci.co.uk/sport/football/teams/exeter-city/rss.xml"],
    "fleetwood-town": ["https://feeds.bbci.co.uk/sport/football/teams/fleetwood-town/rss.xml"],
    "gillingham": ["https://feeds.bbci.co.uk/sport/football/teams/gillingham/rss.xml"],
    "grimsby-town": ["https://feeds.bbci.co.uk/sport/football/teams/grimsby-town/rss.xml"],
    "huddersfield-town": ["https://www.htafc.com/rss.xml"],
    "leicester-city": ["https://www.lcfc.com/rss"],
    "leyton-orient": ["https://feeds.bbci.co.uk/sport/football/teams/leyton-orient/rss.xml"],
    "lincoln-city": ["https://feeds.bbci.co.uk/sport/football/teams/lincoln-city/rss.xml"],
    "luton-town": ["https://feeds.bbci.co.uk/sport/football/teams/luton-town/rss.xml"],
    "mansfield-town": ["https://www.mansfieldtown.net/rss.xml"],
    "middlesbrough": ["https://www.mfc.co.uk/rss.xml"],
    "millwall": ["https://www.millwallfc.co.uk/rss.xml"],
    "milton-keynes-dons": ["https://feeds.bbci.co.uk/sport/football/teams/milton-keynes-dons/rss.xml"],
    "newport-county": ["https://feeds.bbci.co.uk/sport/football/teams/newport-county/rss.xml"],
    "northampton-town": ["https://www.ntfc.co.uk/rss.xml"],
    "norwich-city": ["https://feeds.bbci.co.uk/sport/football/teams/norwich-city/rss.xml"],
    "notts-county": ["https://www.nottscountyfc.co.uk/rss.xml"],
    "oldham-athletic": ["https://feeds.bbci.co.uk/sport/football/teams/oldham-athletic/rss.xml"],
    "oxford-united": ["https://feeds.bbci.co.uk/sport/football/teams/oxford-united/rss.xml"],
    "peterborough-united": ["https://feeds.bbci.co.uk/sport/football/teams/peterborough-united/rss.xml"],
    "plymouth-argyle": ["https://feeds.bbci.co.uk/sport/football/teams/plymouth-argyle/rss.xml"],
    "port-vale": ["https://feeds.bbci.co.uk/sport/football/teams/port-vale/rss.xml"],
    "portsmouth": ["https://feeds.bbci.co.uk/sport/football/teams/portsmouth/rss.xml"],
    "preston-north-end": ["https://www.pnefc.net/rss.xml"],
    "queens-park-rangers": ["https://www.qpr.co.uk/rss.xml"],
    "reading": ["https://feeds.bbci.co.uk/sport/football/teams/reading/rss.xml"],
    "rochdale": ["https://rochdaleafc.co.uk/rss"],
    "rotherham-united": ["https://www.themillers.co.uk/rss.xml"],
    "salford-city": ["https://www.salfordcityfc.co.uk/rss.xml"],
    "sheffield-united": ["https://www.sufc.co.uk/rss.xml"],
    "sheffield-wednesday": ["https://www.swfc.co.uk/rss.xml"],
    "shrewsbury-town": ["https://feeds.bbci.co.uk/sport/football/teams/shrewsbury-town/rss.xml"],
    "southampton": ["https://www.southamptonfc.com/news/rss"],
    "stevenage": ["https://www.stevenagefc.com/rss.xml"],
    "stockport-county": ["https://www.stockportcounty.com/rss.xml"],
    "stoke-city": ["https://feeds.bbci.co.uk/sport/football/teams/stoke-city/rss.xml"],
    "swansea-city": ["https://feeds.bbci.co.uk/sport/football/teams/swansea-city/rss.xml"],
    "swindon-town": ["https://feeds.bbci.co.uk/sport/football/teams/swindon-town/rss.xml"],
    "tranmere-rovers": ["https://www.tranmererovers.co.uk/rss.xml"],
    "walsall": ["https://www.saddlers.co.uk/rss.xml"],
    "watford": ["https://www.watfordfc.com/rss.xml"],
    "west-bromwich-albion": ["https://feeds.bbci.co.uk/sport/football/teams/west-bromwich-albion/rss.xml"],
    "west-ham-united": ["https://feeds.bbci.co.uk/sport/football/teams/west-ham-united/rss.xml"],
    "wigan-athletic": ["https://wiganathletic.com/rss.xml"],
    "wolverhampton-wanderers": ["https://www.wolves.co.uk/news/rss"],
    "wrexham": ["https://feeds.bbci.co.uk/sport/football/teams/wrexham/rss.xml"],
    "wycombe-wanderers": ["https://www.wwfc.com/rss.xml"],
    "york-city": ["https://yorkcityfootballclub.co.uk/rss.xml"],
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


MAX_EXCERPT_CHARS = 220


def clean_excerpt(raw):
    """Google News RSS summaries embed raw HTML -- an <a> link back to the
    article plus a <font> tag naming the source. Strip tags and decode
    entities so what's stored is plain text, not markup. Doing this once
    here (not at render time) means every downstream consumer -- build_site,
    feed.xml, any future client -- gets clean data automatically.

    Also truncates to a short teaser. Official club RSS feeds (confirmed
    live once CLUB_FEEDS was wired in) commonly hand over the ENTIRE
    article body as the description, not a short summary -- a full
    800-word match report showing up whole under a headline card is
    exactly the "too much" a reader doesn't want. Truncates at the last
    word boundary before the limit rather than mid-word, and only adds
    the ellipsis when text was actually cut."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    truncated = text[:MAX_EXCERPT_CHARS]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated.rstrip(".,;:-") + "..."


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
    """Returns None if the entry has no parseable publish date -- rather
    than guessing "now". Confirmed live: once official club feeds went
    live, some entries lacked a parseable published_parsed/updated_parsed
    (inconsistent date formats across the many different CMS platforms
    club sites run on), and defaulting those to datetime.now() made
    genuinely old content -- June stories, in one case -- show up under
    "Today". An undated article is far more likely to be stale/legacy
    content than something breaking right now; showing nothing is safer
    than showing a lie about recency."""
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if not published:
        return None
    published_dt = datetime(*published[:6], tzinfo=timezone.utc)
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

# Content-farm sources that publish auto-generated/misattributed junk
# regardless of topic -- distinct from stream-spam (illegal-stream link
# farms) and gambling sites, so kept as its own blocklist. Confirmed live:
# "Mshale" repeatedly posts titles combining unrelated phrases with real
# club names and a trailing junk code, e.g. "Weekend Weather With Fire
# Wrap Grimsby Town Vs Salford City (YqXudjCzX3)" -- these pass every
# other filter since the club names are real and there's no gambling or
# stream-spam vocabulary, so this needs a dedicated source-level block.
JUNK_SOURCES = {"Mshale"}


def is_junk_source(source):
    return source in JUNK_SOURCES

# Mathematical/fullwidth/CJK-decorative unicode blocks used to dodge basic
# keyword filters -- e.g. "𝐋𝐈𝐕𝐄", "Ｌｉｖｅ", "【LIVESTREAMS】". Legitimate
# club/publisher names don't use these. The CJK Symbols/Punctuation block
# (U+3000-303F, covers 【】) was a real gap found live: it only got caught
# via the rikkyo.ac.jp domain blocklist, so a new spam site using the same
# bracket trick under a different domain would have slipped straight through.
_GARBLED_UNICODE_RE = re.compile(
    "[\U0001D400-\U0001D7FF\uFF00-\uFFEF\u3000-\u303F]"
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
    r"\btwin city\b|\bheadquarters\b|\bin memory of\b|\bfirefighters\b|\bblaze\b|"
    r"\bflames engulf\b|\bgarage fire\b|\bscrapyard fire\b|\bhouse fire\b|"
    r"\bwildfire\b|\barson\b|\brough sleeping\b|\bhomeless(?:ness)?\b|"
    r"\bmigrants?\b|\basylum\b|\bsmall boats?\b|\bchannel crossings?\b"
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
    "WRIC ABC 8News",
}

# Any of these present means it's genuinely about the club, even if a
# noise word also appears (e.g. "boss" news that mentions a "court" case).
_FOOTBALL_CONTEXT_RE = re.compile(
    r"(?i)\bfc\b|\befl\b|championship|league one|league two|\bmatch\b|"
    r"\bboss\b|\bmanager\b|\bstriker\b|\bgoal\b|\btransfer\b|\bsquad\b|"
    r"\bkick-off\b|\bfixtures?\b|\blineup\b|\bline-up\b|\bstarting xi\b|"
    r"\bderby\b.*\b(win|loss|draw|beat)|\bstadium\b|\bloan\b|\bsigning\b|"
    r"\bwinger\b|\bmidfielder\b|\bdefender\b|\bgoalkeeper\b|\bpromotion\b|"
    r"\brelegation\b|play-?off|\bvs\b|\bv\b\s|\bwednesday\b.*\bfc\b|"
    r"\bforward\b|\bcentre-back\b|\bright-back\b|\bleft-back\b|\bfull-back\b|"
    r"\bwing-back\b|\btakeover\b"
)
# A football scoreline is a strong, fairly unambiguous signal on its own.
# Covers both the compact style ("2-0", "4 0") and the common "Team 1
# Team 0" style with words between the digits (found live: "Bradford
# City 1 Mansfield Town 0" wasn't caught by the compact-only pattern).
_SCORELINE_RE = re.compile(r"\b\d{1,2}\s*-\s*\d{1,2}\b|\b\d{1,2}\s\d{1,2}\b")
_SCORELINE_WORDY_RE = re.compile(r"\b\d{1,2}\s+[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3}\s+\d{1,2}\b")


# This aggregator is scoped to men's football only -- a separate women's
# football site is a possible future project, not this one. WSL/BWSL
# (Women's Super League), "Ladies" (legacy team-name suffix still used by
# some clubs), and bare "Women"/"Women's" (as in "Cardiff City Women")
# all reliably signal women's-team content in practice. No word-boundary
# on the wsl/bwsl variants deliberately, since both "WSL2" and "BWSL2"
# need to match and "wsl" essentially never appears inside an unrelated
# English word.
_WOMENS_FOOTBALL_RE = re.compile(r"(?i)\bwomen'?s?\b|\bladies\b|wsl\d?\b")


# Also out of scope: youth/development-squad football (U18s, U21s, U23s,
# academy, PDL). Same reasoning as the women's-football scope -- this site
# is first-team men's football specifically.
_YOUTH_FOOTBALL_RE = re.compile(
    r"(?i)\bu1[89]s?\b|\bu2[13]s?\b|\bunder-?1[89]s?\b|\bunder-?2[13]s?\b|"
    r"\byouth\b|\bacademy\b|\bdevelopment squad\b|\bprofessional development league\b|\bpdl\b"
)


def is_youth_football(title, excerpt=""):
    return bool(_YOUTH_FOOTBALL_RE.search(f"{title} {excerpt}"))


# Lightweight category tag for the reader's own filter chips (News /
# Transfers / Matches / Opinion). Betting/prediction content needs no
# category here since is_gambling_content already removes it entirely --
# this is purely about letting someone hide, say, opinion pieces while
# skimming. Order matters: checked most-specific-first, first match wins,
# so a transfer story that also reports a score still files as "transfer"
# rather than "match".
_CATEGORY_PATTERNS = [
    ("transfer", re.compile(
        r"(?i)\btransfer\b|\bloan\b|\bsigns?\b|\bsigning\b|\bsigned\b|"
        r"\bdeal\b|\bcontract\b|\bswoop\b|\bagreement\b|\bjoins?\b|"
        r"\bjoined\b|\bjoining\b|\bapproach\b"
    )),
    ("match", re.compile(
        r"(?i)\b\d{1,2}[-\s]\d{1,2}\b|\bmatch report\b|\bhighlights\b|"
        r"\bfull-?time\b|\bkick-off\b|\blive score\b|\bvs\.?\b|\bv\b"
    )),
    ("opinion", re.compile(
        r"(?i)\bopinion\b|\bverdict\b|\banalysis\b|\bcolumn\b|\btakeaways?\b"
    )),
]


def categorise(title, excerpt=""):
    text = f"{title} {excerpt}"
    for name, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return name
    return "news"


# Source-quality tier, used ONLY to pick which article leads a cluster
# and how the "+N more" list orders within it -- never to re-rank the
# day's overall feed. That distinction matters: the removed top-story
# feature broke four times trying to infer "the biggest story" across
# the whole feed from headline text, which is a genuinely hard problem.
# Picking the best-written report to feature INSIDE an already-formed
# cluster (same club, same time window, same real event) is a much
# smaller, safer claim -- the cluster's identity is already established
# by the existing club+time+story-similarity checks.
#
# "Trusted" is deliberately a short, curated list of national
# broadcasters/wire-quality sources plus official club sites (reused
# from OFFICIAL_CLUB_NAMES, already built for the homonym check) --
# not an attempt to rank every outlet. "Low" catches template/directory
# pages (squad lists, box scores, live-score stat pages) that provide
# no real reporting, regardless of source -- confirmed live: these
# were crowding out genuine journalism as cluster primaries.
TRUSTED_SOURCES = {"bbc", "sky sports", "efl", "efl.com", "itv"}

LOW_QUALITY_SOURCES = {
    "transfermarkt", "vavel.com", "fotmob", "flashscore.com",
    "sofascore", "besoccer livescore",
}

_BOILERPLATE_TITLE_RE = re.compile(
    r"(?i)- news, schedule, scores, roster, and stats|"
    r"schedule\s*&\s*fixtures\s*-\s*20\d\d-\d\d|"
    r"box score - \w+ \d{1,2}, \d{4}|"
    r"\(\d{1,2} \w+,? \d{4}\) (team|player) stats|"
    r"\u00b7 (results|squad|fixtures) 20\d\d-\d\d|"
    r"live score$"
)


def source_tier(source, title):
    src = (source or "").strip().lower()
    if _BOILERPLATE_TITLE_RE.search(title or ""):
        return "low"
    if src in LOW_QUALITY_SOURCES:
        return "low"
    # Official club sources commonly appear as "X FC" or "X Football
    # Club" in the wild, but clubs.json stores bare names ("Burnley",
    # not "Burnley FC") -- strip the common suffixes before matching so
    # both forms count.
    src_bare = re.sub(r"\s+(fc|f\.c\.|football club)$", "", src)
    if src in TRUSTED_SOURCES or src in OFFICIAL_CLUB_NAMES or src_bare in OFFICIAL_CLUB_NAMES:
        return "trusted"
    return "normal"


def is_womens_football(title, excerpt=""):
    return bool(_WOMENS_FOOTBALL_RE.search(f"{title} {excerpt}"))


# Some clubs' bare marker doubles as a real, substantial place name --
# Portsmouth and Middlesbrough are real cities, Watford a real town, all
# of which generate constant non-football news (Portsmouth is also a
# major Royal Navy base). The keyword-blocklist approach below can never
# keep up with every non-football topic a real city generates -- confirmed
# live: an immigration-protest story reached 63 outlets and became the
# site's TOP STORY purely because "Portsmouth" appeared in every headline,
# with no crash/obituary/police-type word for the blocklist to catch.
# For these clubs specifically, flip the logic: require a POSITIVE
# football signal rather than just the absence of a known-bad word.
# Deliberately conservative about which generic words count as that
# signal here -- words like "captain", "training", "crew", or "mission"
# are exactly as likely to appear in genuine Royal Navy Portsmouth
# content as in football content, so they're left out of
# _FOOTBALL_CONTEXT_RE specifically because of this club.
HIGH_RISK_HOMONYM_CLUBS = {
    "portsmouth": "portsmouth",
    "middlesbrough": "middlesbrough",
    "watford": "watford",
    "lincoln city": "lincoln-city",
}
_HIGH_RISK_CLUB_RE = re.compile(
    r"(?i)\b(" + "|".join(HIGH_RISK_HOMONYM_CLUBS) + r")\b"
)
# The club's OWN other markers (nickname, ground) are stronger evidence
# than generic football vocabulary -- e.g. "Imps" (Lincoln City) or
# "Pompey" (Portsmouth) essentially never appear outside football
# content. Built for EVERY club, not just the curated high-risk list --
# needed once "strict" mode (below) treats any matched club as needing
# positive signal, since a short headline like "VARDY JOINS THE CLARETS"
# has no generic football-context word but does use Burnley's own
# distinguishing nickname.
ALL_CLUB_DISTINGUISHING_MARKERS = {}
for _slug, _club in _BY_SLUG.items():
    _own_name = _club.get("name", "").lower()
    _bare_words = {m.lower() for m in _club.get("markers", []) if len(m.split()) == 1}
    # Exclude both single bare-word markers AND the club's own full name --
    # the latter matters for clubs like York City, where the "risky"
    # marker is the two-word phrase itself ("York city centre" uses
    # "city" generically, not as part of the club name). Without this,
    # the same text that triggered the homonym check was also being
    # counted as evidence disproving it -- circular.
    _exclude = _bare_words | {_own_name}
    _markers = [m for m in _club.get("markers", []) if m.lower() not in _exclude]
    for _m in list(_markers):
        if _m.lower().startswith("the "):
            _markers.append(_m[4:])
    ALL_CLUB_DISTINGUISHING_MARKERS[_slug] = _markers


def _mentions_distinguishing_marker(title, excerpt):
    text = f"{title} {excerpt}".lower()
    for markers in ALL_CLUB_DISTINGUISHING_MARKERS.values():
        for m in markers:
            if re.search(r"\b" + re.escape(m.lower()) + r"\b", text):
                return True
    return False
OFFICIAL_CLUB_NAMES = {c["name"].lower() for c in _BY_SLUG.values()}


def _has_positive_football_signal(title, excerpt, source=""):
    text = f"{title} {excerpt}"
    if _FOOTBALL_CONTEXT_RE.search(text) or _SCORELINE_RE.search(text) or _SCORELINE_WORDY_RE.search(text):
        return True
    if _mentions_distinguishing_marker(title, excerpt):
        return True
    src = source.strip().lower()
    # A national broadcaster/wire-quality source filing something as
    # thin as a bare club name is overwhelmingly likely to be their own
    # sports pages (team profile, results page) -- reuses the same
    # trusted-source list built for the ranking feature.
    if src in TRUSTED_SOURCES:
        return True
    # An EXACT match against a club's own official name (not a fuzzy
    # marker match) means the source IS that club's own site -- e.g.
    # "Cardiff City" as a source. Deliberately exact-match only: a fuzzy
    # marker match would also catch "Watford Observer" (a local paper
    # named after the town, not the club), which proves nothing about
    # whether the story is football content. Also strips a trailing
    # "FC"/"Football Club" the same way source_tier() does, since
    # official sources commonly appear as "Rochdale AFC" not "Rochdale".
    src_bare = re.sub(r"\s+(fc|afc|f\.c\.|football club)$", "", src)
    if src in OFFICIAL_CLUB_NAMES or src_bare in OFFICIAL_CLUB_NAMES:
        return True
    # Last resort: does the headline also name another real EFL club?
    # Two clubs mentioned together is normally strong evidence of a
    # genuine match/transfer story. Deliberately unrestricted -- an
    # earlier version required at least one non-risky club, but that
    # broke the very common real case of two homonym-risk clubs playing
    # each other (e.g. Crewe vs York City, both real towns). The
    # Southampton+Portsmouth migrant story that motivated the
    # restriction is instead caught below, by adding "migrant" etc. to
    # the general noise-word list -- a more targeted fix than
    # penalising every two-risky-club pairing.
    matched = match_clubs(title, excerpt)["clubs"]
    return len(matched) >= 2


def is_homonym_noise(title, source="", excerpt="", strict=False):
    if source in HOMONYM_NOISE_SOURCES:
        return True
    if strict:
        # Applied only to the per-club rotation query path. Found live
        # at scale: 49 of 72 clubs have a bare place-name marker (their
        # actual club name IS a real town), so a curated "high risk"
        # list of just 4 clubs never had a chance of keeping up -- a
        # bare Google News search for e.g. "Southampton" or "Rochdale"
        # surfaces every civic/crime/politics story mentioning that
        # place, not just football. Every matched club is treated as
        # needing positive evidence here, not just the curated few.
        matched = match_clubs(title, excerpt)["clubs"]
        if matched and not _has_positive_football_signal(title, excerpt, source):
            return True
    elif _HIGH_RISK_CLUB_RE.search(title) and not _has_positive_football_signal(title, excerpt, source):
        return True
    if not _NON_FOOTBALL_NOISE_RE.search(title):
        return False
    return not _FOOTBALL_CONTEXT_RE.search(title)


def passes_quality_filters(article, from_google_news, strict_homonym=False):
    title, url, source = article["title"], article["url"], article["source"]
    if is_stream_spam(title, url, source):
        return False
    if is_junk_source(source):
        return False
    if is_gambling_content(title, source):
        return False
    if is_womens_football(title, article.get("excerpt", "")):
        return False
    if is_youth_football(title, article.get("excerpt", "")):
        return False
    # Homonym check only applies to text-matched Google News content --
    # official feeds are already scoped by URL so can't be homonym noise.
    if from_google_news and is_homonym_noise(
        title, source, article.get("excerpt", ""), strict=strict_homonym
    ):
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
                if a is None:
                    continue
                if is_empty_excerpt(a["title"], a["excerpt"]):
                    a["excerpt"] = ""
                if not passes_quality_filters(a, from_google_news=False):
                    continue
                a["clubs"] = [slug]
                a["category"] = categorise(a["title"], a.get("excerpt", ""))
                a["tier"] = source_tier(a["source"], a["title"])
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
            if a is None:
                continue
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
            a["category"] = categorise(a["title"], a.get("excerpt", ""))
            a["tier"] = source_tier(a["source"], a["title"])
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
            if a is None:
                continue
            clean_title, publisher = split_google_news_title(a["title"])
            a["title"] = clean_title
            if publisher:
                a["source"] = normalise_source(publisher)
            if is_empty_excerpt(a["title"], a["excerpt"]):
                a["excerpt"] = ""
            if not passes_quality_filters(a, from_google_news=True, strict_homonym=True):
                continue
            tagged = match_clubs(a["title"], a["excerpt"])
            if slug not in tagged["clubs"]:
                # The per-club query still needs the positive-marker check --
                # a query for "Derby County" can surface an unrelated story
                # that merely contains the word "derby".
                continue
            a["clubs"] = tagged["clubs"]
            a["category"] = categorise(a["title"], a.get("excerpt", ""))
            a["tier"] = source_tier(a["source"], a["title"])
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
