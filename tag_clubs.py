"""Tag an article with EFL clubs.

Provenance beats text matching. If an article came from a club-scoped feed
(official club RSS, a BBC team feed), set the club from the feed config and
do not call match_clubs() at all -- feed scoping is exact, text matching is not.

match_clubs() is only for league-level and general feeds (Google News queries,
BBC EFL, aggregate sites) where the club is unknown.
"""

import json
import re
import time
from pathlib import Path

CLUBS_PATH = Path(__file__).with_name("clubs.json")

# Above this many clubs in one story it is a round-up or a league-wide piece,
# not a club story. Tagging it to 8 clubs would spam eight followed feeds.
MAX_TAGS = 3


def _load():
    return json.loads(CLUBS_PATH.read_text(encoding="utf-8"))["clubs"]


def _compile(clubs):
    pats = []
    for c in clubs:
        excl = [re.compile(r"\b" + re.escape(e) + r"\b") for e in c.get("exclude", [])]
        for m in c["markers"]:
            pats.append({
                "len": len(m),
                "slug": c["slug"],
                "re": re.compile(r"\b" + re.escape(m) + r"\b"),
                "exclude": excl,
            })
    # Longest marker first, so "sheffield united" is consumed before any
    # shorter marker can claim part of the same span.
    pats.sort(key=lambda p: -p["len"])
    return pats


_CLUBS = _load()
_PATTERNS = _compile(_CLUBS)
_BY_SLUG = {c["slug"]: c for c in _CLUBS}


def match_clubs(title, excerpt=""):
    """Return {'clubs': [slug, ...], 'scope': 'club'|'league'|'unmatched'}."""
    text = f"{title} {excerpt}".lower()
    hits = []
    spans = []

    for p in _PATTERNS:
        excl_spans = [xm.span() for x in p["exclude"] for xm in x.finditer(text)]
        for m in p["re"].finditer(text):
            # Skip a marker sitting inside a longer marker already matched,
            # e.g. "wimbledon" inside "afc wimbledon".
            if any(s <= m.start() and m.end() <= e for s, e in spans):
                continue
            # Exclusions are positional. Suppressing document-wide is too
            # blunt: "New York City sign a York City striker" is a real
            # York City story, so keep checking later occurrences.
            if any(not (xe <= m.start() or xs >= m.end()) for xs, xe in excl_spans):
                continue
            spans.append((m.start(), m.end()))
            if p["slug"] not in hits:
                hits.append(p["slug"])
            break

    if len(hits) > MAX_TAGS:
        return {"clubs": [], "scope": "league"}
    return {"clubs": hits, "scope": "club" if hits else "unmatched"}


def division_of(slug):
    return _BY_SLUG[slug]["division"]


def rotation_slice(n_slices=3, now=None):
    """Which slice of clubs to fetch this run.

    Derived from the clock, so no state file and no committed counter.
    With n_slices=3 on a 15-minute cadence every club refreshes within 45 min.
    """
    minutes = int((now if now is not None else time.time()) // 60)
    return (minutes // 15) % n_slices


def clubs_for_run(n_slices=3, now=None):
    s = rotation_slice(n_slices, now)
    ordered = sorted(_BY_SLUG)
    return [slug for i, slug in enumerate(ordered) if i % n_slices == s]
