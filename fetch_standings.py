"""Fetch league standings and each club's last result, write standings.json.

Source: football-data.co.uk match-result CSVs (E1=Championship, E2=League
One, E3=League Two). Free, no auth, no API key -- confirmed URL pattern and
column schema via football-data.co.uk documentation. The table itself isn't
fetched from anywhere -- it's computed here from the match results, same as
anyone would work it out by hand. That's deliberate: a "standings" endpoint
is one more thing that can be wrong or stale; raw results plus arithmetic
is auditable.

IMPORTANT -- team name matching is NOT verified against a live file. This
sandbox's network can't reach football-data.co.uk, so the alias table below
is built from well-documented historical naming conventions on that site,
not confirmed against the current 2026-27 file. On the first real run,
check the Actions log for "UNMATCHED team name" warnings -- if any appear,
add the real spelling to TEAM_ALIASES rather than guessing further.

Run with: python3 fetch_standings.py
Needs: requests (already a dependency via fetch_news.py)
"""

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path

import requests

from tag_clubs import _BY_SLUG

OUT = Path(__file__).with_name("standings.json")
REQUEST_TIMEOUT = 15
USER_AGENT = "EFLFeedBot/1.0 (+https://example.invalid)"

SEASON_CODE = "2627"  # 2026-27. Update each July when the new season starts.
DIVISION_CODES = {
    "championship": "E1",
    "league-one": "E2",
    "league-two": "E3",
}
CSV_URL_TMPL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"

# Known historical football-data.co.uk naming quirks -- NOT verified against
# the live 2026-27 file (see module docstring). Keys are lowercased. Only
# includes clubs actually in our clubs.json -- each division's CSV only
# ever contains that division's own fixtures, so no need for aliases
# covering clubs outside our 72.
TEAM_ALIASES = {
    "sheffield weds": "sheffield-wednesday",
    "qpr": "queens-park-rangers",
    "mk dons": "milton-keynes-dons",
    "wolves": "wolverhampton-wanderers",
}


def _normalise_name(name):
    """Strip common club-name suffixes and punctuation so 'Bristol City'
    and 'bristol-city' compare equal without needing an exact literal
    match for every one of the 72 clubs."""
    n = name.lower().strip()
    n = n.replace("'", "").replace(".", "")
    n = re.sub(r"\s+", " ", n)
    for suffix in (" city", " town", " united", " rovers", " wanderers",
                   " albion", " athletic", " county", " orient"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n.strip()


def build_name_index():
    """Map every plausible spelling of a club to its slug: the exact
    clubs.json name, a suffix-stripped version, and the known alias
    table above. Returns {normalised_or_alias_string: slug}."""
    index = {}
    for slug, club in _BY_SLUG.items():
        index[club["name"].lower()] = slug
        index[_normalise_name(club["name"])] = slug
    for alias, slug in TEAM_ALIASES.items():
        index[alias] = slug
    return index


NAME_INDEX = build_name_index()


def resolve_team(raw_name):
    key = raw_name.lower().strip()
    if key in NAME_INDEX:
        return NAME_INDEX[key]
    stripped = _normalise_name(raw_name)
    if stripped in NAME_INDEX:
        return NAME_INDEX[stripped]
    return None


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


def compute_division(division, rows):
    table = {}  # slug -> stats
    last_result = {}  # slug -> most recent match info
    unmatched = set()

    def get(slug):
        if slug not in table:
            table[slug] = {"slug": slug, "played": 0, "won": 0, "drawn": 0,
                            "lost": 0, "gf": 0, "ga": 0}
        return table[slug]

    parsed_rows = []
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
        except (KeyError, ValueError):
            continue
        date = parse_date((row.get("Date") or "").strip())
        home_slug = resolve_team(home_raw)
        away_slug = resolve_team(away_raw)
        if home_slug is None:
            unmatched.add(home_raw)
        if away_slug is None:
            unmatched.add(away_raw)
        parsed_rows.append({
            "date": date, "home_slug": home_slug, "away_slug": away_slug,
            "home_raw": home_raw, "away_raw": away_raw,
            "hg": hg, "ag": ag, "ftr": ftr,
        })

    for r in parsed_rows:
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
    dated = [r for r in parsed_rows if r["date"] is not None]
    dated.sort(key=lambda r: r["date"])
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

    if unmatched:
        print(f"[standings] {division}: UNMATCHED team name(s), add to "
              f"TEAM_ALIASES: {sorted(unmatched)}")

    return ranked, last_result


def main():
    result = {}
    for division in DIVISION_CODES:
        try:
            rows = fetch_csv(division)
        except Exception as e:
            print(f"[standings] {division} fetch failed: {e}")
            continue
        table, last_result = compute_division(division, rows)
        result[division] = {"table": table, "last_result": last_result}
        print(f"[standings] {division}: {len(table)} clubs in table, "
              f"{len(last_result)} with a last result")

    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote standings.json")


if __name__ == "__main__":
    main()
