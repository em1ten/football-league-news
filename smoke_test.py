from types import SimpleNamespace
import fetch_news as fn

# is_empty_excerpt
assert fn.is_empty_excerpt("Some title", "") is True
assert fn.is_empty_excerpt("Some title", "Some title - bbc.co.uk") is True
assert fn.is_empty_excerpt("Some title", "Some title, plus extra detail about the match") is False
print("is_empty_excerpt OK")

# dedupe
arts = [
    {"url": "http://a", "title": "A", "source": "X"},
    {"url": "http://a", "title": "A dup", "source": "X"},
    {"url": "", "title": "B", "source": "Y"},
    {"url": "", "title": "B", "source": "Y"},
]
d = fn.dedupe(arts)
assert len(d) == 2, d
print("dedupe OK")

# fetch_division_feeds belt-and-braces: fake a cross-division false positive
orig_parse = fn._parse
def fake_parse(url):
    if "League+One" in url or "League One" in url:
        entries = [
            {"title": "Sheffield Wednesday held by Bromley", "summary": "League One clash", "link": "http://x/1", "published_parsed": (2026, 9, 6, 12, 0, 0, 0, 0, 0)},
        ]
    elif "Championship" in url:
        entries = [
            {"title": "Random Championship round-up mentions nothing specific", "summary": "", "link": "http://x/2", "published_parsed": (2026, 9, 6, 12, 0, 0, 0, 0, 0)},
        ]
    else:
        entries = []
    return SimpleNamespace(feed={"title": "Google News"}, entries=entries)
fn._parse = fake_parse
try:
    res = fn.fetch_division_feeds()
finally:
    fn._parse = orig_parse

# fetch_division_feeds now drops untagged (no specific club) content
# entirely -- that's precisely where cross-division noise, other leagues,
# and generic chatter piled up in production. Confirm the tagged story
# survives and the untagged one is gone, not relabelled "league".
by_url = {a["url"]: a for a in res}
assert by_url["http://x/1"]["clubs"] == ["sheffield-wednesday", "bromley"], by_url["http://x/1"]
assert by_url["http://x/1"]["scope"] == "club"
assert "http://x/2" not in by_url, "untagged division content should be dropped, not kept as scope=league"
print("fetch_division_feeds tagging OK")

print("ALL SMOKE TESTS PASSED")

# --- quality filters, tested against real examples from production output
# rather than assumptions (see LEARNINGS.md #9 and #16) ---
print()
print("--- quality filter regression tests ---")

# The bug this guards against: Google News RSS embeds the real per-article
# publisher as a title suffix ("Headline - Publisher"), NOT in a separate
# per-item source field -- the channel-level source is the same for every
# item and always collapses to "Google News". Every source-based filter
# (gambling sites, stream-spam domains, homonym-noise outlets) is useless
# if it runs before that suffix is extracted.
title, publisher = fn.split_google_news_title(
    "Bristol Rovers vs Rotherham Prediction, Betting Tips, Lineups & Odds | 05 Sep 2026 - Sportsgambler"
)
assert publisher == "Sportsgambler", publisher
assert not title.endswith("Sportsgambler")
print("split_google_news_title extracts real publisher OK")

article = {"title": title, "url": "https://news.google.com/rss/articles/x", "source": publisher}
assert fn.is_gambling_content(article["title"], article["source"]) is True
print("gambling-source filter fires on real extracted publisher OK")

stream_title, stream_pub = fn.split_google_news_title(
    "[=LIVE=!TV!]**Hull City vs Aston Villa LIVE Stream - rikkyo.ac.jp"
)
assert stream_pub == "rikkyo.ac.jp"
assert fn.is_stream_spam(stream_title, "https://news.google.com/rss/articles/y", stream_pub) is True
print("stream-spam filter fires on real extracted publisher OK")

# Literal repeated headline from production (Google News re-wraps the same
# story in a new redirect URL each crawl -- URL-only dedupe misses this).
dupes = [
    {"title": "U18s Highlights: Middlesbrough H 26/27", "url": "https://news.google.com/rss/articles/AAA", "source": "TVGuide.co.uk", "published": "2026-09-05T14:00:00+00:00"},
    {"title": "U18s Highlights: Middlesbrough H 26/27", "url": "https://news.google.com/rss/articles/BBB", "source": "TVGuide.co.uk", "published": "2026-09-05T13:00:00+00:00"},
    {"title": "U18s Highlights: Middlesbrough H 26/27", "url": "https://news.google.com/rss/articles/CCC", "source": "TVGuide.co.uk", "published": "2026-09-05T12:00:00+00:00"},
]
assert len(fn.dedupe(dupes)) == 1, "identical Google-News reposts under different redirect URLs should collapse"
print("dedupe collapses repeated Google-News redirect URLs OK")

print()
print("ALL QUALITY FILTER TESTS PASSED")

# --- women's football scope check (this site is men's football only) ---
womens_examples = [
    "Sunderland vs Bristol City: WSL 2 stats & head-to-head",
    "Preview | Swansea City Women v Cardiff City Women",
    "Sunderland vs Bristol City | BWSL2",
]
assert all(fn.is_womens_football(t) for t in womens_examples), "real WSL/Women examples should be filtered"
assert not fn.is_womens_football("Barnsley boss under fire after fifth straight defeat")
print("women's-football scope filter OK (real WSL examples dropped, men's football kept)")

youth_examples = [
    "U18s Highlights: Middlesbrough H 26/27",
    "U21 Report | Wednesday 4-1 Wigan",
    "Bolton Wanderers U21 v Southampton U21",
]
assert all(fn.is_youth_football(t) for t in youth_examples), "real U18s/U21s examples should be filtered"
assert not fn.is_youth_football("Sheffield Wednesday 7-2 Bromley: Owls hit seven at Hillsborough")
print("youth-team scope filter OK (real U18s/U21s examples dropped, first-team football kept)")

# High-risk homonym clubs (bare marker doubles as a real place): must
# require a positive football signal, not just the absence of a
# known-bad word. Real bug: a Portsmouth immigration-protest story with
# no crash/police/obituary-type word reached 63 outlets and became the
# site's top story.
high_risk_should_drop = [
    ("Hundreds of protesters gather in Portsmouth after small boat arrival", "Sky News"),
    ("HMS Duncan returns to Portsmouth to begin upgrade refit", "Navy Lookout"),
]
assert all(fn.is_homonym_noise(t, s) for t, s in high_risk_should_drop)
high_risk_should_keep = [
    ("Middlesbrough boss relishing prospect of working with late transfer window additions", "Yorkshire Post"),
    ("Pompey 2 Cardiff 0: Neil Allen's verdict", "portsmouth.co.uk"),
    ("Portsmouth reaction | Brian Barry-Murphy", "Cardiff City"),
]
assert not any(fn.is_homonym_noise(t, s) for t, s in high_risk_should_keep)
print("high-risk homonym club filter OK (real Portsmouth protest story dropped, real football content kept)")


# --- junk content-farm source (Mshale) ---
mshale_title, mshale_pub = fn.split_google_news_title(
    "Weekend Weather With Fire Wrap Grimsby Town Vs Salford City (YqXudjCzX3) - Mshale"
)
assert fn.is_junk_source(mshale_pub), "Mshale should be blocked as a junk source"
print("junk-source filter OK (Mshale blocked)")

# --- Lincoln City homonym (Lincoln City, Indiana/Oregon) ---
assert fn.is_homonym_noise(
    "Jacob Schmidt - Heritage Hills Patriots Boys Basketball (Lincoln City, IN)",
    "Sports Illustrated",
)
assert not fn.is_homonym_noise(
    "Adam Jackson's Lincoln City Farewell And a Look At His Imps' Career",
    "The Stacey West",
)
print("Lincoln City homonym filter OK (US Lincoln City dropped, real Imps content kept)")

# --- strict-mode homonym check (per-club rotation query path) ---
# Real bug: only 4 clubs were curated as "high risk", but 49 of 72 clubs
# have a bare place-name marker -- dozens of civic/crime/politics stories
# for OTHER clubs were surfacing the same way as the original Portsmouth
# bug. strict=True applies the positive-football-signal requirement to
# every matched club, not just the curated few.
strict_should_drop = [
    ("Politicians fear Southampton could be next after Portsmouth migrant arrival", "Daily Echo"),
    ("Emergency services close York city centre road following incident", "The York Press"),
    ("Two affectionate rescue cats looking for their purr-fect matches in Barnet", "companionlife.co.uk"),
]
assert all(fn.is_homonym_noise(t, s, strict=True) for t, s in strict_should_drop)

strict_should_keep = [
    ("Bradford City 1 Mansfield Town 0: Normal service is resumed", "Yorkshire Post"),
    ("Rochdale AFC Sleep Out Returns", "Rochdale AFC"),
    ("Ollie Banks praises York City's character after 'horrendous' start in Crewe draw", "The York Press"),
    ("Everything you need to know as Northampton Town takeover nears with Alexandre Pato among investors", "The72"),
    ("Barnet: Boss Dean Brennan gutted at late equaliser in fantastic 3-3 draw at Chesterfield", "BBC"),
]
assert not any(fn.is_homonym_noise(t, s, strict=True) for t, s in strict_should_keep)
print("strict-mode homonym filter OK (real civic/crime stories dropped across many clubs, real football kept)")

# --- excerpt length cap ---
# Real bug: official club RSS feeds (confirmed once CLUB_FEEDS was wired
# in) hand over the ENTIRE article body as the description, not a short
# teaser -- a full match report was showing whole under a headline card.
_long = "Wednesday maintained their 100 per cent away record with a hard-fought 2-1 win. " * 6
_result = fn.clean_excerpt(_long)
assert len(_result) <= fn.MAX_EXCERPT_CHARS + 3, "excerpt not being truncated"
assert _result.endswith("..."), "truncated excerpt should end with ellipsis"
assert not _result.rstrip(".").endswith(("wa", "ha", "wi"))  # sanity: didn't cut mid-word
_short = "A short teaser under the limit."
assert fn.clean_excerpt(_short) == _short, "short excerpts should pass through unchanged"
print("excerpt length cap OK (long official-feed articles truncated to a teaser, short ones untouched)")

# --- undated entries must be skipped, never stamped with today's date ---
# Real bug: once official club feeds went live, entries with no
# parseable published_parsed/updated_parsed (inconsistent date formats
# across different club CMS platforms) were silently defaulted to
# datetime.now() -- making genuinely old content (June stories, in one
# case) show up under "Today".
good_entry = {"title": "Owls beat Posh 2-1", "link": "https://x/1", "summary": "Match report",
              "published_parsed": (2026, 9, 6, 12, 0, 0, 0, 0, 0)}
assert fn._entry_to_article(good_entry, "Sheffield Wednesday FC") is not None
undated_entry = {"title": "Some old story with no date", "link": "https://x/2", "summary": "text"}
assert fn._entry_to_article(undated_entry, "Some Club Site") is None, \
    "undated entries must be skipped, not stamped with today's date"
print("undated-entry skip OK (no more stale content appearing as today)")

# --- "how to watch" template filler: deprioritized, not dropped ---
# Real find: athlonsports.com mass-produces near-identical "Lineups, Live
# Streaming, How and Where to Watch ... on TV" articles per fixture, zero
# real reporting. First built as a hard drop, then changed to a "low"
# tier demotion instead -- still reachable via "+N more stories", just
# never featured as a cluster's headline. Deliberately narrow so it
# doesn't also catch genuinely useful broadcast-info headlines already
# tested and kept elsewhere.
watch_guide_examples = [
    "Wrexham vs. Burnley Lineups, Live Streaming, How and Where to Watch EFL Championship on TV",
    "Bolton vs. West Ham Lineups, Live Streams, How and Where to Watch on TV",
]
assert all(fn.source_tier("athlonsports.com", t) == "low" for t in watch_guide_examples)
assert all(fn.passes_quality_filters({"title": t, "url": "https://x", "source": "athlonsports.com"}, from_google_news=True) for t in watch_guide_examples), \
    "should be kept in the feed (demoted), not hard-dropped"
watch_guide_should_stay_normal = [
    "Doncaster Rovers vs Huddersfield Town: TV channel, live stream and kick-off time",
    "Stoke vs Charlton: Where to watch, TV channel, live stream and kick-off time",
]
assert not any(fn.source_tier("Goal.com", t) == "low" for t in watch_guide_should_stay_normal)
print("watch-guide template filter OK (real athlonsports.com filler demoted not dropped, real broadcast-info untouched)")

# --- age window applies to FRESH articles, not just previous ones ---
# Real bug: MAX_AGE_DAYS only filtered last run's articles. Club/BBC feeds
# return weeks of history, so 27-day-old Birmingham stories hit the page.
import time as _t
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
_now = _dt.now(_tz.utc)
_aged = [{"title": "fresh", "published": (_now - _td(hours=2)).isoformat()},
         {"title": "27 days old", "published": (_now - _td(days=27)).isoformat()},
         {"title": "future-dated", "published": (_now + _td(days=3)).isoformat()}]
assert [a["title"] for a in fn.within_age_window(_aged)] == ["fresh"]
print("age window OK (old and future-dated fresh articles dropped)")

# --- junk-code suffix: catches the content-farm pattern, not bracketed words ---
assert fn.is_junk_source("Unisba Media", "Birmingham City Vs Southampton | Live Scoreboard Mary Simon (7h1uith2Dm)")
assert fn.is_junk_source("Some New Farm", "Rochdale AFC 1-2 Crawley Town Highlights (TSALTLsRfn)")
assert not fn.is_junk_source("BBC", "Club statement (Interviews)")
assert not fn.is_junk_source("Sports Illustrated", "Birmingham 2-2 Wolves (6 Sep, 2026) Team Stats")
print("junk-code suffix filter OK (any source using the pattern dropped, real bracketed titles kept)")

# --- a title that is only a club name is a profile page, not a story ---
for t in ["Wrexham AFC", "Barnet", "Walsall FC", "Portsmouth Football Club",
          "Wrexham Association Football Club", "Birmingham City F.C."]:
    assert fn.is_bare_club_title(t), t
for t in ["Barnet extend unbeaten run", "Wrexham AFC announce new sporting director"]:
    assert not fn.is_bare_club_title(t), t
print("bare club-name titles OK (profile pages dropped, real headlines kept)")

# --- official club feed: clean source label, trusted tier, old/junk entries dropped ---
def _entry(title, hours, summary="text"):
    return {"title": title, "link": f"https://x/{title}", "summary": summary,
            "published_parsed": (_now - _td(hours=hours)).utctimetuple()}
_orig_feeds, _orig_parse = fn.CLUB_FEEDS, fn._parse
fn.CLUB_FEEDS = {"sheffield-wednesday": ["https://www.swfc.co.uk/rss.xml"],
                 "wrexham": ["https://feeds.bbci.co.uk/sport/football/teams/wrexham/rss.xml"]}
def _fake_club_parse(url):
    if "swfc" in url:
        return SimpleNamespace(feed={"title": "Latest News - Sheffield Wednesday FC"}, entries=[
            _entry("Report | Peterborough 1-2 Wednesday", 3)])
    return SimpleNamespace(feed={"title": "BBC Sport - Wrexham"}, entries=[
        _entry("Wrexham AFC", 2), _entry("Parkinson hails fans", 4), _entry("Old story", 24 * 27)])
fn._parse = _fake_club_parse
try:
    _club = fn.fetch_club_feeds()
finally:
    fn.CLUB_FEEDS, fn._parse = _orig_feeds, _orig_parse
_by_title = {a["title"]: a for a in fn.within_age_window(_club)}
assert set(_by_title) == {"Report | Peterborough 1-2 Wednesday", "Parkinson hails fans"}, set(_by_title)
assert _by_title["Report | Peterborough 1-2 Wednesday"]["source"] == "Official site"
assert _by_title["Parkinson hails fans"]["source"] == "BBC"
assert all(a["tier"] == "trusted" for a in _by_title.values())
print("official club feeds OK (clean labels, trusted tier, profile + 27-day-old entries dropped)")


# --- November 2026: 92 clubs, top stories, fan media, junk fixes
import build_site as bs

assert fn.tidy_title("CESAY STRIKES AS SALFORD EXTEND UNBEATEN RUN") == "Cesay Strikes As Salford Extend Unbeaten Run"
assert fn.tidy_title("HIGHLIGHTS: QPR 2 MK DONS 1 (H)") == "Highlights: QPR 2 MK Dons 1 (H)"
assert fn.tidy_title("MK Dons 6-2 Burton | Football News") == "MK Dons 6-2 Burton"
assert fn.tidy_title("QPR's Poppy Appeal") == "QPR's Poppy Appeal" and fn.tidy_title("VAR drama") == "VAR drama"
print("headline tidy OK (shouting calmed, publisher tails removed, normal headlines untouched)")

for _t in ["Fleetwood Town vs. Tranmere (14 Nov, 2026) Team Stats", "Exeter vs Cheltenham (14 Nov, 2026) Live Score",
           "Clearances per match - Bristol Rovers stats for League Two 2026/2027",
           "How to watch Swindon Town v Grimsby Town: Live stream, TV channel, kick-off time"]:
    assert fn.is_stat_page(_t), _t
for _t in ["TV channel, live stream and kick-off time for Derby v Stoke", "Sheffield Wednesday 3-0 Plymouth Argyle",
           "Team news: Owls make three changes"]:
    assert not fn.is_stat_page(_t), _t
assert fn.is_gambling_content("Next Oldham manager odds", "bet365") and not fn.is_gambling_content("Owls win", "Coral Gables News")
assert fn.is_club_admin("Tickets - Walsall (H)") and fn.is_club_admin("Legends Party Night")
assert not fn.is_club_admin("Report: Rooney's late header earns Town victory") and not fn.is_club_admin("Coach Dave Challinor reacts")
assert fn.normalise_source("Fleetwood Town Football Club") == "Fleetwood Town"
print("junk rules OK (stat pages and bookmakers dropped, club admin demoted, real stories kept)")

_d = fn.dedupe([
    {"title": "Danny Cowley celebrates rare Colchester league win", "url": "u1", "source": "Gazette", "clubs": ["colchester-united"]},
    {"title": "Danny Cowley celebrates rare Colchester league win", "url": "u2", "source": "The Bury Times", "clubs": ["colchester-united"]},
    {"title": "Preview", "url": "u3", "source": "A", "clubs": ["barnet"]},
    {"title": "Preview", "url": "u4", "source": "B", "clubs": ["barnet"]},
])
assert [a["url"] for a in _d] == ["u1", "u3", "u4"], _d
print("syndication dedupe OK (same headline from a second outlet dropped, short titles left alone)")

_orig_fan, _orig_parse2 = fn.FAN_FEEDS, fn._parse
fn.FAN_FEEDS = {"sheffield-wednesday": [{"name": "The Wednesday Week", "kind": "podcast", "url": "https://fan/feed"}],
                "not-a-club": [{"name": "X", "kind": "blog", "url": "https://x"}]}
fn._parse = lambda url: SimpleNamespace(feed={"title": "x"}, entries=[_entry(f"Episode {i}", i + 1) for i in range(9)])
try:
    _fan = fn.fetch_fan_feeds()
finally:
    fn.FAN_FEEDS, fn._parse = _orig_fan, _orig_parse2
assert len(_fan) == fn.FAN_FEED_MAX_ENTRIES and all(
    a["tier"] == "fan" and a["category"] == "fan" and a["clubs"] == ["sheffield-wednesday"]
    and a["source"] == "The Wednesday Week" and a["fan_kind"] == "podcast" for a in _fan), _fan
print("fan media OK (labelled, newest few only, unknown club ignored)")


def _c(title, clubs, source, tier, hours, more=()):
    mk = lambda s: {"title": title, "clubs": clubs, "source": s, "tier": tier,
                    "published": f"2026-11-15T{23 - hours:02d}:00:00+00:00"}
    return {"primary": mk(source), "more": [mk(s) for s in more]}

_cl = bs.mark_top_stories([
    _c("farm rumour", ["arsenal"], "Football League World", "normal", 1),                    # lone aggregator: never
    _c("club ticket news", ["arsenal"], "Official site", "low", 2),                            # admin: never
    _c("official report", ["arsenal"], "Official site", "trusted", 3),
    _c("bbc story", ["arsenal"], "BBC", "trusted", 4),                                         # wins Arsenal's slot
    _c("local paper", ["barnet"], "Barnet Times", "normal", 5),                                # local press alone is enough
    _c("two outlets", ["bromley"], "The72", "normal", 6, more=["Football League World"]),
    _c("derby report", ["burnley", "wrexham"], "Sky Sports", "trusted", 7),                    # uses both clubs' slots
    _c("burnley extra", ["burnley"], "BBC", "trusted", 8),
    _c("pod 1", ["arsenal"], "Fan Pod", "fan", 1), _c("pod 2", ["arsenal"], "Fan Pod", "fan", 2),
])
_top = [c["primary"]["title"] for c in _cl if c["top"]]
assert _top == ["bbc story", "local paper", "two outlets", "derby report", "pod 1"], _top
assert bs.is_local_press("Yorkshire Post") and bs.is_local_press("Plymouth Live") and not bs.is_local_press("Daily Star") \
    and not bs.is_local_press("Football League World")
print("top stories OK (one per club per day by source and coverage, one fan slot, no lone aggregator pieces)")
