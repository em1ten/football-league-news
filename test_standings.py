"""Tests for fetch_standings.py. No network: run with  python3 test_standings.py

The first test is the important one. Every team name below is one
football-data.co.uk's 2026-27 CSVs have actually used, taken from this
repo's standings.json history. Seven of them (marked) never matched a
club before October 2026, which left those clubs without form or a
position and pushed every club below them up a place.
"""
import json
from datetime import date, datetime
from unittest import mock

import fetch_standings as fs

FOOTBALL_DATA_NAMES = {
    "AFC Wimbledon": "afc-wimbledon", "Accrington": "accrington-stanley",  # was unmatched
    "Barnet": "barnet", "Barnsley": "barnsley", "Birmingham": "birmingham-city",
    "Blackburn": "blackburn-rovers", "Blackpool": "blackpool", "Bolton": "bolton-wanderers",
    "Bradford": "bradford-city", "Bristol City": "bristol-city",
    "Bristol Rvs": "bristol-rovers",  # was unmatched
    "Bromley": "bromley", "Burnley": "burnley", "Burton": "burton-albion",
    "Cambridge": "cambridge-united", "Cardiff": "cardiff-city", "Charlton": "charlton-athletic",
    "Cheltenham": "cheltenham-town", "Chesterfield": "chesterfield", "Colchester": "colchester-united",
    "Crawley Town": "crawley-town", "Crewe": "crewe-alexandra",  # was unmatched
    "Derby": "derby-county", "Doncaster": "doncaster-rovers", "Exeter": "exeter-city",
    "Fleetwood Town": "fleetwood-town", "Gillingham": "gillingham", "Grimsby": "grimsby-town",
    "Huddersfield": "huddersfield-town", "Leicester": "leicester-city", "Leyton Orient": "leyton-orient",
    "Lincoln": "lincoln-city", "Luton": "luton-town", "Mansfield": "mansfield-town",
    "Middlesbrough": "middlesbrough", "Millwall": "millwall", "Milton Keynes Dons": "milton-keynes-dons",
    "Newport County": "newport-county", "Northampton": "northampton-town", "Norwich": "norwich-city",
    "Notts County": "notts-county", "Oldham": "oldham-athletic", "Oxford": "oxford-united",
    "Peterboro": "peterborough-united",  # was unmatched
    "Plymouth": "plymouth-argyle",  # was unmatched
    "Port Vale": "port-vale", "Portsmouth": "portsmouth",
    "Preston": "preston-north-end",  # was unmatched
    "QPR": "queens-park-rangers", "Reading": "reading", "Rochdale": "rochdale",
    "Rotherham": "rotherham-united", "Salford": "salford-city", "Sheffield United": "sheffield-united",
    "Sheffield Weds": "sheffield-wednesday", "Shrewsbury": "shrewsbury-town", "Southampton": "southampton",
    "Stevenage": "stevenage", "Stockport": "stockport-county", "Stoke": "stoke-city",
    "Swansea": "swansea-city", "Swindon": "swindon-town", "Tranmere": "tranmere-rovers",
    "Walsall": "walsall", "Watford": "watford",
    "West Brom": "west-bromwich-albion",  # was unmatched
    "West Ham": "west-ham-united", "Wigan": "wigan-athletic", "Wolves": "wolverhampton-wanderers",
    "Wrexham": "wrexham", "Wycombe": "wycombe-wanderers", "York": "york-city",
}

# Premier League, added November 2026. These are football-data.co.uk's
# long-standing spellings, NOT yet confirmed against the 2026-27 file the
# way the 72 above were. If one is wrong the workflow run shows a warning.
PREMIER_LEAGUE_NAMES = {
    "Arsenal": "arsenal", "Aston Villa": "aston-villa", "Bournemouth": "bournemouth",
    "Brentford": "brentford", "Brighton": "brighton-and-hove-albion", "Chelsea": "chelsea",
    "Coventry": "coventry-city", "Crystal Palace": "crystal-palace", "Everton": "everton",
    "Fulham": "fulham", "Hull": "hull-city", "Ipswich": "ipswich-town", "Leeds": "leeds-united",
    "Liverpool": "liverpool", "Man City": "manchester-city", "Man United": "manchester-united",
    "Newcastle": "newcastle-united", "Nott'm Forest": "nottingham-forest",
    "Sunderland": "sunderland", "Tottenham": "tottenham-hotspur",
}
ESPN_STYLE_NAMES = {
    "AFC Bournemouth": "bournemouth", "Brighton & Hove Albion": "brighton-and-hove-albion",
    "Tottenham Hotspur": "tottenham-hotspur", "Manchester United": "manchester-united",
    "Manchester City": "manchester-city", "Nottingham Forest": "nottingham-forest",
    "Newcastle United": "newcastle-united", "Leeds United": "leeds-united",
    "Wolverhampton Wanderers": "wolverhampton-wanderers", "West Ham United": "west-ham-united",
}


def test_every_football_data_name_resolves():
    wrong = {n: (fs.resolve_team(n), want) for n, want in FOOTBALL_DATA_NAMES.items()
             if fs.resolve_team(n) != want}
    assert not wrong, wrong
    for names in (PREMIER_LEAGUE_NAMES, ESPN_STYLE_NAMES):
        wrong = {n: (fs.resolve_team(n), want) for n, want in names.items() if fs.resolve_team(n) != want}
        assert not wrong, wrong
    covered = set(FOOTBALL_DATA_NAMES.values()) | set(PREMIER_LEAGUE_NAMES.values())
    assert covered == set(fs._BY_SLUG), set(fs._BY_SLUG) ^ covered
    assert fs.resolve_team("Manchester") is None, "shared by City and United: must not guess"
    print(f"team names OK ({len(FOOTBALL_DATA_NAMES)} confirmed EFL names, {len(PREMIER_LEAGUE_NAMES)} Premier League, all 92 clubs covered)")


def test_full_names_and_ambiguity():
    for slug, club in fs._BY_SLUG.items():
        assert fs.resolve_team(club["name"]) == slug, club["name"]
    for n, want in {"MK Dons": "milton-keynes-dons", "Sheffield Wednesday": "sheffield-wednesday",
                    "Bristol Rovers": "bristol-rovers", "Preston North End FC": "preston-north-end",
                    "Plymouth Argyle": "plymouth-argyle"}.items():
        assert fs.resolve_team(n) == want, n
    # Shared by Bristol City and Bristol Rovers: must NOT guess.
    assert fs.resolve_team("Bristol") is None
    assert fs.resolve_team("") is None and fs.resolve_team(None) is None
    print("full names OK, ambiguous 'Bristol' left unmatched rather than guessed")


def espn_event(home, away, hs, as_, when, completed=True, status="STATUS_FULL_TIME"):
    return {"id": "1", "date": when, "name": f"{away} at {home}",
            "competitions": [{"status": {"type": {"name": status, "state": "post" if completed else "in",
                                                  "completed": completed}},
                              "competitors": [
                                  {"homeAway": "home", "score": hs, "team": {"displayName": home}},
                                  {"homeAway": "away", "score": as_, "team": {"displayName": away}}]}]}


def test_espn_parser():
    data = {"events": [
        espn_event("Sheffield Wednesday", "Plymouth Argyle", "2", "1", "2026-10-17T14:00Z"),
        espn_event("Peterborough United", "Reading", {"value": 0.0, "displayValue": "0"}, "3", "2026-10-17T14:00Z"),
        espn_event("Wigan Athletic", "Barnsley", "1", "1", "2026-10-17T14:00Z", completed=False),       # live
        espn_event("Bolton Wanderers", "Stevenage", "0", "0", "2026-10-17T14:00Z", status="STATUS_POSTPONED", completed=False),
        espn_event("Luton Town", "Exeter City", "1", "0", "2026-10-17T14:00Z", status="STATUS_ABANDONED"),  # completed but abandoned
        espn_event("Hartlepool United", "Reading", "1", "0", "2026-10-17T14:00Z"),                         # not an EFL club
        espn_event("Burton Albion", "Mansfield Town", "3", "2", "2026-10-17T23:30Z"),                      # 00:30 UK on the 18th
        {"competitions": []}, {"weird": True},                                                               # junk
    ]}
    matches, unmatched = fs.parse_espn_scoreboard(data)
    got = [(m["home_slug"], m["away_slug"], m["hg"], m["ag"], m["ftr"], m["date"].date().isoformat()) for m in matches]
    assert ("sheffield-wednesday", "plymouth-argyle", 2, 1, "H", "2026-10-17") in got, got
    assert ("peterborough-united", "reading", 0, 3, "A", "2026-10-17") in got, got
    assert ("burton-albion", "mansfield-town", 3, 2, "H", "2026-10-18") in got, "UK date, not UTC"
    assert not any(g[0] in ("wigan-athletic", "bolton-wanderers", "luton-town") for g in got), "live/postponed/abandoned skipped"
    assert unmatched == {"Hartlepool United"}, unmatched
    assert fs.parse_espn_scoreboard({}) == ([], set()) and fs.parse_espn_scoreboard(None) == ([], set())
    print("ESPN parser OK (finished only, UK dates, both score shapes, junk ignored)")


def csv_row(d, home, away, hg, ag):
    return {"Date": d, "HomeTeam": home, "AwayTeam": away, "FTHG": str(hg), "FTAG": str(ag),
            "FTR": "H" if hg > ag else ("A" if ag > hg else "D")}


def test_merge_and_table():
    rows = [csv_row("10/10/2026", "Sheffield Weds", "Peterboro", 1, 0),
            csv_row("14/10/2026", "Plymouth", "Sheffield Weds", 2, 2)]
    recent = [
        # already in the CSV (same fixture) -- must not double count
        {"date": datetime(2026, 10, 14), "home_slug": "plymouth-argyle", "away_slug": "sheffield-wednesday",
         "home_raw": "Plymouth Argyle", "away_raw": "Sheffield Wednesday", "hg": 2, "ag": 2, "ftr": "D", "source": "espn"},
        # new Saturday result football-data hasn't published yet
        {"date": datetime(2026, 10, 17), "home_slug": "stockport-county", "away_slug": "sheffield-wednesday",
         "home_raw": "Stockport County", "away_raw": "Sheffield Wednesday", "hg": 0, "ag": 3, "ftr": "A", "source": "espn"},
        # same day as a CSV game for Peterborough -> a duplicate under another name, skip
        {"date": datetime(2026, 10, 10), "home_slug": "reading", "away_slug": "peterborough-united",
         "home_raw": "Reading", "away_raw": "Peterborough United", "hg": 1, "ag": 0, "ftr": "H", "source": "espn"},
        # unresolved team -> skip
        {"date": datetime(2026, 10, 17), "home_slug": None, "away_slug": "reading",
         "home_raw": "Mystery FC", "away_raw": "Reading", "hg": 1, "ag": 0, "ftr": "H", "source": "espn"},
    ]
    with mock.patch("builtins.print"):
        table, last, form, info = fs.compute_division("league-one", rows, recent)
    assert info["espn_added"] == 1 and info["csv_latest"] == "2026-10-14", info
    assert form["sheffield-wednesday"] == ["W", "D", "W"], form["sheffield-wednesday"]
    assert last["sheffield-wednesday"]["date"] == "2026-10-17"
    assert last["sheffield-wednesday"]["opponent_slug"] == "stockport-county"
    assert last["sheffield-wednesday"]["home_away"] == "A"
    swfc = next(t for t in table if t["slug"] == "sheffield-wednesday")
    assert (swfc["played"], swfc["points"]) == (3, 7), swfc
    assert {t["slug"] for t in table} >= {"peterborough-united", "plymouth-argyle"}, "previously-missing clubs now in the table"
    print("merge OK (new Saturday result added, duplicates and unknown teams skipped, form/last result current)")


def test_espn_failure_is_harmless():
    printed = []
    with mock.patch.object(fs.requests, "get", side_effect=OSError("network down")), \
         mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
        matches, unmatched = fs.fetch_espn_recent("league-one", today=date(2026, 10, 18))
    assert matches == [] and unmatched == set()
    assert len(printed) == 1 and printed[0].startswith("::warning::"), printed

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"something": "else"}
    printed.clear()
    with mock.patch.object(fs.requests, "get", return_value=Resp()), \
         mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
        matches, _ = fs.fetch_espn_recent("league-one", today=date(2026, 10, 18))
    assert matches == [] and len(printed) == 1 and "format may have changed" in printed[0], printed
    print("ESPN failure OK (one ::warning::, no crash, falls back to football-data)")


def test_download_failure_keeps_previous_table(tmp_out):
    previous = {"championship": {"table": [{"slug": "birmingham-city", "position": 1}], "last_result": {}, "form": {}}}
    tmp_out.write_text(json.dumps(previous))

    def fake_csv(division):
        if division == "championship":
            raise OSError("503")
        return [csv_row("17/10/2026", "Preston", "West Brom", 1, 0)] if division == "league-one" else []

    with mock.patch.object(fs, "OUT", tmp_out), mock.patch.object(fs, "fetch_csv", side_effect=fake_csv), \
         mock.patch.object(fs, "fetch_espn_recent", return_value=([], set())), mock.patch("builtins.print"):
        fs.main()
    out = json.loads(tmp_out.read_text())
    assert out["championship"] == previous["championship"], "failed download keeps last good table"
    assert "league-one" in out and "league-two" in out and "premier-league" in out
    print("download failure OK (previous table kept instead of vanishing)")


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    test_every_football_data_name_resolves()
    test_full_names_and_ambiguity()
    test_espn_parser()
    test_merge_and_table()
    test_espn_failure_is_harmless()
    with tempfile.TemporaryDirectory() as d:
        test_download_failure_keeps_previous_table(Path(d) / "standings.json")
    print("\nall standings tests passed")
