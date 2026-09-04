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

import json
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


def _entry_to_article(entry, source):
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        published_dt = datetime(*published[:6], tzinfo=timezone.utc)
    else:
        published_dt = datetime.now(timezone.utc)
    return {
        "title": entry.get("title", "").strip(),
        "url": entry.get("link", ""),
        "excerpt": entry.get("summary", "").strip(),
        "source": source,
        "published": published_dt.isoformat(),
    }


def is_empty_excerpt(title, excerpt):
    """Google News-style excerpts that are just the headline + a bare
    domain carry zero information -- drop them. Anything with genuinely
    more text than that is kept, even if some of that text turns out to
    be junk (other clustered stories) -- that failure mode is unpredictable
    to fix generically, so don't try; just don't throw away real content."""
    if not excerpt:
        return True
    stripped = excerpt.replace(title, "").strip(" -\u2013\u2014")
    if len(stripped) < 3:
        return True
    # A leftover that's a single token with no spaces and a dot is a bare
    # source domain (bbc.co.uk, skysports.com), not real added content.
    return " " not in stripped and "." in stripped


SOURCE_ALIASES = {
    "bbc.com": "BBC", "bbc.co.uk": "BBC", "BBC Sport": "BBC",
}


def normalise_source(name):
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
            if is_empty_excerpt(a["title"], a["excerpt"]):
                a["excerpt"] = ""
            tagged = match_clubs(a["title"], a["excerpt"])
            # Belt-and-braces: only keep clubs that actually belong to this
            # division-tagged feed, in case a cross-division story slipped in.
            tagged_clubs = [s for s in tagged["clubs"] if division_of(s) == division]
            a["clubs"] = tagged_clubs
            a["division"] = division
            a["scope"] = "club" if tagged_clubs else "league"
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
            if is_empty_excerpt(a["title"], a["excerpt"]):
                a["excerpt"] = ""
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
        key = a["url"] or (a["title"], a["source"])
        if key in seen:
            continue
        seen.add(key)
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
