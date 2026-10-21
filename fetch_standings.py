"""Fetch league standings and each club's last result, write standings.json.

Two sources, because neither is enough alone:

1. football-data.co.uk match-result CSVs (E1=Championship, E2=League One,
   E3=League Two). Free, no key, the whole season in one file per division.
   But it is only refreshed about twice a week. This repo's own history
   shows it: results arrived 1-3 days after the games, and the
   Championship file sat at 20 September until 13 October. On its own it
   left the form guide days out of date.
2. ESPN's public scoreboard, for the last ESPN_LOOKBACK_DAYS days only.
   Updates within minutes of full time and needs no key, but it isn't an
   official API, so it is only a top-up. Any failure or unexpected shape
   means a ::warning:: on the run and the page carries on with
   football-data alone.

The table itself isn't fetched from anywhere -- it's computed here from
the results, the same way anyone would work it out by hand. Raw results
plus arithmetic is auditable; a "standings" endpoint is one more thing
that can be wrong.

Team names: football-data uses its own short forms ("Peterboro",
"Bristol Rvs"). Before October 2026, seven of them never matched a club,
so those clubs had no form or position and every club below them was
ranked one place too high. That went unnoticed because the mismatch was
only printed in a log nobody reads. It now shows as a ::warning:: on the
workflow run, and test_standings.py checks every name the CSVs have
actually used.

Run with: python3 fetch_standings.py
Needs: requests (already a dependency via fetch_news.py)
"""

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from tag_clubs import _BY_SLUG

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/London")
except Exception:
    LOCAL_TZ = timezone.utc

OUT = Path(__file__).with_name("standings.json")
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; FootballLeagueNewsBot/1.0; +https://em1ten.github.io/football-league-news/)"

SEASON_CODE = "2627"  # 2026-27. Update each July when the new season starts.
DIVISION_CODES = {
    "championship": "E1",
    "league-one": "E2",
    "league-two": "E3",
}
CSV_URL_TMPL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
FORM_LENGTH = 5  # results shown in each club's form guide on the page

ESPN_LEAGUES = {
    "championship": "eng.2",
    "league-one": "eng.3",
    "league-two": "eng.4",
}
ESPN_URL_TMPL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={day}"
# Covers football-data's normal lag with room to spare. One request per
# day per division: 3 x 14 = 42 small requests a run.
ESPN_LOOKBACK_DAYS = 14

# Short forms that the generic suffix-stripping below can't reach. Keys
# are lowercased. The football-data entries are confirmed from the
# names its 2026-27 CSVs actually used (see test_standings.py).
TEAM_ALIASES = {
    "sheffield weds": "sheffield-wednesday",
    "qpr": "queens-park-rangers",
    "mk dons": "milton-keynes-dons",
    "wolves": "wolverhampton-wanderers",
    "west brom": "west-bromwich-albion",
    "peterboro": "peterborough-united",
    "bristol rvs": "bristol-rovers",
    "sheff wed": "sheffield-wednesday",
    "sheff utd": "sheffield-united",
    "sheffield utd": "sheffield-united",
    "wimbledon": "afc-wimbledon",
}

_SUFFIXES = (" city", " town", " united", " rovers", " wanderers", " albion",
             " athletic", " county", " orient", " north end", " argyle",
             " stanley", " alexandra", " fc", " afc")


def _normalise_name(name):
    """Strip punctuation and a generic suffix so 'Plymouth' matches
    'Plymouth Argyle' and 'Crewe' matches 'Crewe Alexandra' without an
    alias for every club."""
    n = name.lower().strip()
    n = n.replace("'", "").replace(".", "").replace("&", "and")
    n = re.sub(r"\s+", " ", n)
    for suffix in _SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)]
            break
    return n.strip()


def build_name_index():
    """Map every plausible spelling of a club to its slug: the exact
    clubs.json name, a suffix-stripped version, and the alias table.

    A stripped form two clubs share ("bristol" from Bristol City and
    Bristol Rovers, "sheffield") is dropped rather than handed to
    whichever club happened to come last -- an unmatched name raises a
    warning, a wrongly matched one silently corrupts the table."""
    index, owners = {}, {}
    for slug, club in _BY_SLUG.items():
        index[club["name"].lower()] = slug
        owners.setdefault(_normalise_name(club["name"]), set()).add(slug)
    for key, slugs in owners.items():
        if len(slugs) == 1 and key not in index:
            index[key] = next(iter(slugs))
    for alias, slug in TEAM_ALIASES.items():
        index[alias] = slug
    return index


NAME_INDEX = build_name_index()


def resolve_team(raw_name):
    key = (raw_name or "").lower().strip()
    if not key:
        return None
    if key in NAME_INDEX:
        return NAME_INDEX[key]
    stripped = _normalise_name(raw_name)
    return NAME_INDEX.get(stripped)


# ---------------------------------------------------------- football-data

def fetch_csv(division):
    code = DIVISION_CODES[division]
    url = CSV_URL_TMPL.format(season=SEASON_CODE, code=code)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    # football-data.co.uk CSVs are sometimes latin-1, not utf-8.
    text = resp.content.decode("latin-1")
    return list(csv.DictReader(io.StringIO(text)))


def parse_date(d):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(d, fmt)
        except ValueError:
            continue
    return None


def parse_csv_rows(rows):
    """football-data CSV rows -> match dicts. Returns (matches, unmatched names)."""
    matches, unmatched = [], set()
    for row in rows:
        home_raw = (row.get("HomeTeam") or "").strip()
        away_raw = (row.get("AwayTeam") or "").strip()
        if not home_raw or not away_raw:
            continue
        ftr = (row.get("FTR") or "").strip()
        if ftr not in ("H", "D", "A"):
            continue  # unplayed/incomplete row
        try:
            hg = int(row["FTHG"])
            ag = int(row["FTAG"])
        except (KeyError, ValueError, TypeError):
            continue
        home_slug = resolve_team(home_raw)
        away_slug = resolve_team(away_raw)
        if home_slug is None:
            unmatched.add(home_raw)
        if away_slug is None:
            unmatched.add(away_raw)
        matches.append({
            "date": parse_date((row.get("Date") or "").strip()),
            "home_slug": home_slug, "away_slug": away_slug,
            "home_raw": home_raw, "away_raw": away_raw,
            "hg": hg, "ag": ag, "ftr": ftr, "source": "football-data",
        })
    return matches, unmatched


# ------------------------------------------------------------------- ESPN

# A finished status whose name contains any of these was not a normal
# result -- never count it.
_ESPN_BAD_STATUS = ("ABANDON", "POSTPON", "CANCEL", "SUSPEND", "DELAY", "FORFEIT")


def _espn_score(value):
    # "2" on the scoreboard endpoint; {"value": 2.0, ...} on some others.
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    return int(float(value))


def _espn_team_slug(team):
    """Try each name ESPN gives a team, most specific first."""
    team = team or {}
    for key in ("displayName", "shortDisplayName", "name", "location"):
        slug = resolve_team(team.get(key) or "")
        if slug:
            return slug
    return None


def parse_espn_scoreboard(data):
    """ESPN scoreboard JSON -> (finished match dicts, unmatched names).

    Deliberately strict: only events marked completed, with exactly one
    home and one away competitor and whole-number scores. Anything else
    is skipped, not guessed at."""
    matches, unmatched = [], set()
    for ev in (data or {}).get("events") or []:
        comp = (ev.get("competitions") or [{}])[0] or {}
        status = (comp.get("status") or ev.get("status") or {}).get("type") or {}
        name = (status.get("name") or "").upper()
        if status.get("completed") is not True or any(bad in name for bad in _ESPN_BAD_STATUS):
            continue
        competitors = comp.get("competitors") or []
        home = [c for c in competitors if c.get("homeAway") == "home"]
        away = [c for c in competitors if c.get("homeAway") == "away"]
        if len(home) != 1 or len(away) != 1:
            continue
        home, away = home[0], away[0]
        try:
            hg, ag = _espn_score(home.get("score")), _espn_score(away.get("score"))
            kickoff = datetime.fromisoformat((ev.get("date") or comp.get("date") or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        # UK calendar day, stored like a CSV date (midnight, no timezone)
        # so the two sources sort and compare together.
        day = kickoff.astimezone(LOCAL_TZ).date()
        home_raw = (home.get("team") or {}).get("displayName") or ""
        away_raw = (away.get("team") or {}).get("displayName") or ""
        home_slug, away_slug = _espn_team_slug(home.get("team")), _espn_team_slug(away.get("team"))
        if home_slug is None:
            unmatched.add(home_raw)
        if away_slug is None:
            unmatched.add(away_raw)
        matches.append({
            "date": datetime(day.year, day.month, day.day),
            "home_slug": home_slug, "away_slug": away_slug,
            "home_raw": home_raw, "away_raw": away_raw,
            "hg": hg, "ag": ag, "ftr": "H" if hg > ag else ("A" if ag > hg else "D"),
            "source": "espn",
        })
    return matches, unmatched


def fetch_espn_recent(division, today=None):
    """Finished matches from the last ESPN_LOOKBACK_DAYS days. Never raises:
    on the first failure it warns once and returns what it has, so a
    broken or changed ESPN endpoint can't take the standings down."""
    league = ESPN_LEAGUES[division]
    today = today or datetime.now(LOCAL_TZ).date()
    matches, unmatched = [], set()
    for offset in range(ESPN_LOOKBACK_DAYS):
        day = today - timedelta(days=offset)
        url = ESPN_URL_TMPL.format(league=league, day=day.strftime("%Y%m%d"))
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or "events" not in data:
                raise ValueError("no 'events' in response -- ESPN's format may have changed")
        except Exception as e:
            print(f"::warning::ESPN results for {division} unavailable ({e}). "
                  f"Using football-data.co.uk only, which can be a few days behind.")
            break
        m, u = parse_espn_scoreboard(data)
        matches.extend(m)
        unmatched |= u
    return matches, unmatched


def merge_recent(base, recent):
    """Add matches from `recent` that `base` doesn't already have.

    In a league season each club hosts each other club exactly once, so
    (home, away) identifies a match regardless of date -- that catches a
    game whose date differs between sources. Separately, no club plays
    two league games on one day, so a recent match is also skipped if
    either club already has a base result that day; that stops a game
    being counted twice if one source ever has a name the other spells
    differently. Base (football-data) always wins: it's the full record."""
    pairs = {(m["home_slug"], m["away_slug"]) for m in base}
    busy = set()
    for m in base:
        if m["date"] is not None:
            busy.add((m["date"].date(), m["home_slug"]))
            busy.add((m["date"].date(), m["away_slug"]))
    added = []
    for m in sorted(recent, key=lambda m: m["date"]):
        if m["home_slug"] is None or m["away_slug"] is None:
            continue
        if (m["home_slug"], m["away_slug"]) in pairs:
            continue
        d = m["date"].date()
        if (d, m["home_slug"]) in busy or (d, m["away_slug"]) in busy:
            continue
        added.append(m)
        pairs.add((m["home_slug"], m["away_slug"]))
        busy.add((d, m["home_slug"]))
        busy.add((d, m["away_slug"]))
    return base + added, added


# ------------------------------------------------------------------ table

def compute_table(matches):
    """Matches -> (ranked table, last result per club, form per club)."""
    table = {}  # slug -> stats
    last_result = {}  # slug -> most recent match info

    def get(slug):
        if slug not in table:
            table[slug] = {"slug": slug, "played": 0, "won": 0, "drawn": 0,
                           "lost": 0, "gf": 0, "ga": 0}
        return table[slug]

    for r in matches:
        for slug, is_home, gf, ga, ftr in (
            (r["home_slug"], True, r["hg"], r["ag"], r["ftr"]),
            (r["away_slug"], False, r["ag"], r["hg"], r["ftr"]),
        ):
            if slug is None:
                continue
            t = get(slug)
            t["played"] += 1
            t["gf"] += gf
            t["ga"] += ga
            won = (is_home and ftr == "H") or (not is_home and ftr == "A")
            drawn = ftr == "D"
            if won:
                t["won"] += 1
            elif drawn:
                t["drawn"] += 1
            else:
                t["lost"] += 1

    for t in table.values():
        t["gd"] = t["gf"] - t["ga"]
        t["points"] = t["won"] * 3 + t["drawn"]

    ranked = sorted(table.values(), key=lambda t: (-t["points"], -t["gd"], -t["gf"]))
    for i, t in enumerate(ranked, start=1):
        t["position"] = i

    # Last result per club: latest dated match involving that club.
    # The same pass builds each club's form guide (W/D/L in date order);
    # trimmed to the last FORM_LENGTH once every match has been seen.
    dated = [r for r in matches if r["date"] is not None]
    dated.sort(key=lambda r: r["date"])
    form = {}
    for r in dated:
        for slug, opp_slug, opp_raw, gf, ga, is_home in (
            (r["home_slug"], r["away_slug"], r["away_raw"], r["hg"], r["ag"], True),
            (r["away_slug"], r["home_slug"], r["home_raw"], r["ag"], r["hg"], False),
        ):
            if slug is None:
                continue
            if gf > ga:
                result = "W"
            elif gf < ga:
                result = "L"
            else:
                result = "D"
            last_result[slug] = {
                "result": result, "gf": gf, "ga": ga,
                "opponent_slug": opp_slug, "opponent_name": opp_raw,
                "home_away": "H" if is_home else "A",
                "date": r["date"].strftime("%Y-%m-%d"),
            }
            form.setdefault(slug, []).append(result)

    # Oldest first, newest last -- the page draws them left to right.
    form = {slug: results[-FORM_LENGTH:] for slug, results in form.items()}
    return ranked, last_result, form


def compute_division(division, rows, recent=None):
    """CSV rows (+ optional recent ESPN matches) -> (table, last_result,
    form, info). info is a small dict for the run log."""
    matches, unmatched = parse_csv_rows(rows)
    if unmatched:
        print(f"::warning::{division}: football-data team name(s) not matched to a club -- "
              f"add to TEAM_ALIASES in fetch_standings.py: {sorted(unmatched)}")
    csv_latest = max((m["date"] for m in matches if m["date"]), default=None)
    added = []
    if recent:
        matches, added = merge_recent(matches, recent)
    table, last_result, form = compute_table(matches)
    info = {
        "csv_matches": len(matches) - len(added),
        "csv_latest": csv_latest.strftime("%Y-%m-%d") if csv_latest else None,
        "espn_added": len(added),
        "latest": max((m["date"] for m in matches if m["date"]), default=csv_latest),
    }
    expected = sum(1 for c in _BY_SLUG.values() if c.get("division") == division)
    if matches and len(table) < expected:
        missing = sorted(s for s, c in _BY_SLUG.items() if c.get("division") == division
                         and s not in {t["slug"] for t in table})
        print(f"::warning::{division}: only {len(table)} of {expected} clubs have results -- "
              f"positions will be off. Missing: {missing}")
    return table, last_result, form, info


def load_previous():
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    previous = load_previous()
    result = {}
    for division in DIVISION_CODES:
        try:
            rows = fetch_csv(division)
        except Exception as e:
            # Keep the last good table rather than wiping this division
            # off the page until the next successful run.
            print(f"::warning::{division}: football-data.co.uk download failed ({e}); "
                  f"keeping the previous table")
            if division in previous:
                result[division] = previous[division]
            continue
        recent, espn_unmatched = fetch_espn_recent(division)
        if espn_unmatched:
            print(f"::warning::{division}: ESPN team name(s) not matched to a club -- "
                  f"add to TEAM_ALIASES in fetch_standings.py: {sorted(espn_unmatched)}")
        table, last_result, form, info = compute_division(division, rows, recent)
        result[division] = {"table": table, "last_result": last_result, "form": form}
        latest = info["latest"].strftime("%Y-%m-%d") if info["latest"] else None
        print(f"[standings] {division}: {len(table)} clubs; football-data has "
              f"{info['csv_matches']} results up to {info['csv_latest']}; ESPN added "
              f"{info['espn_added']} newer (of {len(recent)} seen); results now up to {latest}")

    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("wrote standings.json")


if __name__ == "__main__":
    main()
