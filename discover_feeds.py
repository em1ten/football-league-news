"""Find which clubs actually publish a working RSS feed, and print a
ready-to-paste CLUB_FEEDS block for fetch_news.py.

WHY THIS EXISTS AS A SEPARATE SCRIPT
------------------------------------
Club RSS URLs cannot be reliably guessed. There is no published list of
EFL club feeds, most clubs run different CMS platforms, and many killed
their RSS feeds years ago. Putting a guessed URL into CLUB_FEEDS fails
SILENTLY -- fetch_news.py catches the exception, logs one line, and moves
on -- so a broken feed looks identical to a club that simply had no news.
That is the exact class of bug LEARNINGS.md warns about.

So: verify first, configure second. This script probes candidate URLs,
checks the response is genuinely a parseable feed with real entries, and
only reports the ones that pass.

Run it manually (needs outbound network -- won't work in restricted
sandboxes):

    pip install requests feedparser
    python3 discover_feeds.py

Or trigger it once via a GitHub Actions run, which has open network.

It is DELIBERATELY not part of the scheduled build: it makes hundreds of
requests, and club feed URLs change rarely. Run it now, paste the output,
re-run it once or twice a season.
"""

import json
import sys
from pathlib import Path

try:
    import requests
    import feedparser
except ImportError:
    sys.exit("Needs requests + feedparser:  pip install requests feedparser")

CLUBS = Path(__file__).with_name("clubs.json")
TIMEOUT = 8
USER_AGENT = "FootballLeagueNewsBot/1.0 (feed discovery; contact via site)"

# Paths commonly used by UK football club sites. Ordered roughly by how
# often they turn up, so the likely hit is found before the long tail.
CANDIDATE_PATHS = [
    "/rss",
    "/rss.xml",
    "/feed",
    "/feed/",
    "/news/rss",
    "/news/rss.xml",
    "/news/feed",
    "/api/rss",
    "/rss/news",
    "/feeds/news.xml",
    "/news.rss",
    "/rss/news.xml",
]

# BBC has historically published per-team feeds. Worth probing since it
# would cover clubs whose own site has no feed, and BBC content is
# already high quality and exactly club-scoped.
BBC_TEMPLATE = "https://feeds.bbci.co.uk/sport/football/teams/{slug}/rss.xml"


def looks_like_real_feed(resp):
    """A 200 response proves nothing -- plenty of sites return their
    homepage (or a soft-404 page) for an unknown path. Require that it
    actually parses as a feed AND has entries with titles."""
    ctype = resp.headers.get("content-type", "").lower()
    body = resp.content
    if b"<rss" not in body[:2000] and b"<feed" not in body[:2000]:
        return False, "not xml"
    parsed = feedparser.parse(body)
    if parsed.bozo and not parsed.entries:
        return False, "unparseable"
    if not parsed.entries:
        return False, "no entries"
    titled = [e for e in parsed.entries if e.get("title")]
    if not titled:
        return False, "entries have no titles"
    return True, f"{len(titled)} entries, e.g. {titled[0]['title'][:60]!r}"


def probe(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=TIMEOUT, allow_redirects=True)
    except Exception as e:
        return False, f"error: {type(e).__name__}"
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    return looks_like_real_feed(resp)


def main():
    clubs = json.loads(CLUBS.read_text(encoding="utf-8"))["clubs"]
    found = {}
    no_feed = []
    no_domain = []

    for club in clubs:
        slug = club["slug"]
        site = club.get("site")
        hit = None

        if site:
            base = site.rstrip("/")
            for path in CANDIDATE_PATHS:
                ok, detail = probe(base + path)
                if ok:
                    hit = (base + path, detail)
                    break
        else:
            no_domain.append(slug)

        if hit is None:
            ok, detail = probe(BBC_TEMPLATE.format(slug=slug))
            if ok:
                hit = (BBC_TEMPLATE.format(slug=slug), detail)

        if hit:
            found[slug] = hit[0]
            print(f"  FOUND  {slug:26} {hit[0]}")
            print(f"         -> {hit[1]}")
        else:
            no_feed.append(slug)
            print(f"  none   {slug}")

    print()
    print("=" * 70)
    print(f"{len(found)} clubs with a verified working feed")
    print(f"{len(no_feed)} with no feed found (they'll keep using Google News)")
    if no_domain:
        print(f"{len(no_domain)} have no official domain in clubs.json yet:")
        print(f"   {', '.join(no_domain)}")
    print("=" * 70)
    print()
    print("Paste this into fetch_news.py, replacing the existing CLUB_FEEDS:")
    print()
    print("CLUB_FEEDS = {")
    for slug, url in sorted(found.items()):
        print(f'    "{slug}": ["{url}"],')
    print("}")


if __name__ == "__main__":
    main()
