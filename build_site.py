"""Build the static Football League News site from articles.json.

Emits index.html, feed.xml, sitemap.xml, version.json, favicon.svg,
manifest.webmanifest and the PWA icons into site/.

Look: "Matchday app" -- chosen from three mockups in October 2026. Modelled
on native phone apps rather than a web page:
  - System font stack only (SF on Apple, Segoe on Windows, Roboto on
    Android). No Google Fonts request, so nothing to load before text draws.
  - Grouped inset lists: rounded panels with hairline separators, the way
    iOS Settings / Apple News lay out rows.
  - Dark and light palettes as CSS custom properties, swapped via
    [data-theme]. Follows the phone's setting until the visitor taps the
    sun/moon button, then remembers that choice.
  - Club identity comes from small kit-colour swatches (CLUB_KITS below),
    not division colours.

Behaviour that carried over unchanged from the previous design:
  - Day-bucket grouping via date().toordinal() -- NOT timestamp() // 86400,
    which collides on negative floor division at midnight boundaries.
  - Update banner lives in normal document flow (never position:fixed) and
    fades via a dedicated class -- not the `hidden` attribute, which can't
    transition.
  - Global [hidden]{display:none !important} -- see the comment in the CSS.
"""

import json
import hashlib
import re
from datetime import datetime, date, timezone
from email.utils import format_datetime
from pathlib import Path

# Every club is English (Wrexham and the Welsh sides keep UK time too), so
# "today" and "yesterday" mean UK days, not UTC ones. Without this, a story
# published at 00:30 on a summer morning (23:30 UTC) landed under
# "Yesterday". Falls back to UTC if the tz database is missing.
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/London")
except Exception:
    LOCAL_TZ = timezone.utc

HERE = Path(__file__).parent
ARTICLES = HERE / "articles.json"
CLUBS = HERE / "clubs.json"
STANDINGS = HERE / "standings.json"
SITE_DIR = HERE / "site"
# Update this if you move to a custom domain -- it feeds the RSS <link>,
# the sitemap, and the Open Graph tags used for link previews.
SITE_URL = "https://em1ten.github.io/football-league-news"
SITE_TITLE = "Football League News"
SITE_TAGLINE = "All 72 clubs. One feed. No ads."
SITE_ABOUT = "Made by a fan, for fans. Independent and unofficial, not affiliated with the EFL or any club."
KOFI_URL = "https://ko-fi.com/footballnewsfeed"

DIVISION_ORDER = ["championship", "league-one", "league-two"]
DIVISION_LABEL = {"championship": "Championship", "league-one": "League One", "league-two": "League Two"}


def load():
    articles = json.loads(ARTICLES.read_text(encoding="utf-8")) if ARTICLES.exists() else []
    clubs = json.loads(CLUBS.read_text(encoding="utf-8"))["clubs"]
    standings = json.loads(STANDINGS.read_text(encoding="utf-8")) if STANDINGS.exists() else {}
    return articles, clubs, standings


def esc(s):
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------- day buckets

def local_date(iso):
    """UK calendar date of an ISO timestamp (naive timestamps are UTC)."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).date()


def long_date(d):
    # "Sunday 4 October" -- built by hand because strftime's %d zero-pads
    # ("04") and the no-pad flag (%-d) doesn't exist on Windows.
    return f"{d:%A} {d.day} {d:%B}"


def day_label(d, today):
    # Both are date objects -- .toordinal() is exact and side-steps the
    # negative-floor-division bug that hit timestamp()//86400 grouping.
    # The page's script relabels these against the reader's own clock, so
    # a page left open past midnight doesn't keep saying "Latest" about
    # yesterday; this server-side label is the no-JavaScript fallback.
    diff = d.toordinal() - today.toordinal()
    if diff == 0:
        return "Latest"
    if diff == -1:
        return "Yesterday"
    return long_date(d)


CLUSTER_WINDOW_HOURS = 6

# Words that carry no story-distinguishing signal: generic English function
# words plus football-journalism boilerplate ("TV channel", "kick-off time",
# "live stream" template headlines all reduce to nothing once these are
# stripped). Club names are stripped separately per-article since they vary.
_CLUSTER_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "to", "of", "in", "on", "at", "for",
    "with", "as", "after", "before", "from", "by", "is", "are", "was", "were",
    "be", "been", "it", "its", "this", "that", "has", "have", "had", "will",
    "not", "no", "vs", "v", "up", "out", "over", "his", "her", "their", "our",
    "your", "who", "what", "when", "how", "why", "until", "end", "season",
    "tv", "channel", "live", "stream", "streaming", "watch", "where", "kick",
    "off", "kickoff", "time", "score", "scores", "report", "highlights",
    "preview", "lineup", "lineups", "stats", "head", "odds", "tips",
    "prediction", "predictions", "betting", "match", "game", "news", "latest",
    "update", "updates", "confirmed", "official", "breaking",
}
_WORD_RE = re.compile(r"[a-z0-9']+")

# Built once at module load from clubs.json's own curated markers (official
# name, common nickname, ground name) -- e.g. Wolverhampton Wanderers'
# markers include "wolves". Using only the slug's own words (wolverhampton,
# wanderers) missed this entirely: found live when a Bertrand Traore
# transfer-rumour story and 14 completely unrelated Birmingham-Wolves
# match-report stories all "matched" on the single surviving word "wolves",
# which is how virtually every headline actually refers to the club.
def _build_club_marker_words():
    try:
        data = json.loads(CLUBS.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for c in data.get("clubs", []):
        words = set()
        for marker in c.get("markers", []):
            words.update(_WORD_RE.findall(marker.lower()))
        out[c["slug"]] = words
    return out


CLUB_MARKER_WORDS = _build_club_marker_words()


def _significant_tokens(title, clubs):
    club_words = set()
    for slug in clubs:
        club_words.update(slug.split("-"))
        club_words.update(CLUB_MARKER_WORDS.get(slug, set()))
    words = _WORD_RE.findall(title.lower())
    return {w for w in words if len(w) > 2 and w not in _CLUSTER_STOPWORDS and w not in club_words}


def _same_story(a, b):
    """Second gate on clustering, alongside club-set and time window:
    same club and close in time still isn't necessarily the same STORY.
    Confirmed live: a Jamie Vardy transfer story and an unrelated
    "Burnley make an approach for Broja" story merged into one cluster
    purely on club tag + timing. Strip club names (including nicknames
    from clubs.json markers) and generic/boilerplate words from both
    titles; if nothing distinctive is left in common, treat them as
    different stories.

    A template headline like "Where to watch: TV channel, kick-off time"
    reduces to nothing and can't be judged either way -- default to
    merging rather than wrongly splitting genuine same-match coverage
    that just uses different phrasing. Safe here because the shared club
    tag is already strong evidence; this was NOT safe when the removed
    top-story feature reused this across the whole day's feed.

    A heuristic, not real story-matching."""
    tokens_a = _significant_tokens(a.get("title", ""), a.get("clubs", []))
    tokens_b = _significant_tokens(b.get("title", ""), b.get("clubs", []))
    if not tokens_a or not tokens_b:
        return True
    return bool(tokens_a & tokens_b)


def cluster_by_clubs(articles):
    """Group same-club-set stories together within a day. Two independent
    gates must both pass to merge a story into an existing cluster:

    1. Time window, anchored to that cluster's PRIMARY time (not a rolling
       "gap since the last item added" -- a rolling window drifts: a dense
       chain of small gaps, e.g. a viral transfer story picked up by dozens
       of outlets over several hours, keeps re-extending its own reach and
       can end up sweeping in a much later, unrelated story. Confirmed
       live: a Jamie Vardy transfer story generated 45 pickups across ~6
       hours, and a rolling window chained all the way out to sweep in two
       unrelated stories about the same club 12h and 16h later.
    2. Story similarity (_same_story) -- same club and close in time still
       isn't necessarily the same STORY. Confirmed live: a genuinely
       different transfer story ("Burnley make an approach for Broja")
       landed in the middle of the Vardy burst and got merged in purely on
       club+time. Stripped of club names and generic/boilerplate words, if
       nothing overlaps between two titles, they're treated as different
       stories.

    A new cluster opened for a key doesn't retire the earlier ones for that
    same key -- every article checks against ALL previously-opened clusters
    sharing its club-set (each still gated by its own time window), not
    just whichever opened most recently. Without this, one interloper story
    (like Broja) permanently splits the real burst in two: everything after
    it would compare only against the interloper, fail the similarity
    check, and each spin off as its own singleton instead of rejoining the
    original cluster.

    All of this is a heuristic, not real story-matching: two genuinely
    unrelated stories close together in time can still share a distinctive
    word and merge; two paraphrases of the same story with no shared
    vocabulary can still split. Nothing is ever dropped either way --
    everything stays reachable via the <details> disclosure, just possibly
    split across more than one card instead of one."""
    clusters = []
    clusters_by_key = {}
    for a in articles:
        key = tuple(sorted(a.get("clubs", [])))
        try:
            t = datetime.fromisoformat(a["published"])
        except Exception:
            t = None

        matched_idx = None
        for idx in clusters_by_key.get(key, []):
            primary = clusters[idx]["primary"]
            try:
                pt = datetime.fromisoformat(primary["published"])
            except Exception:
                pt = None
            within_window = (
                t is not None and pt is not None
                and abs((pt - t).total_seconds()) <= CLUSTER_WINDOW_HOURS * 3600
            )
            if within_window and _same_story(primary, a):
                matched_idx = idx
                break

        if matched_idx is not None:
            clusters[matched_idx]["more"].append(a)
        else:
            new_idx = len(clusters)
            clusters.append({"primary": a, "more": []})
            clusters_by_key.setdefault(key, []).append(new_idx)
    for c in clusters:
        _promote_best_primary(c)
    return clusters


_TIER_RANK = {"trusted": 0, "normal": 1, "low": 2}


def _promote_best_primary(cluster):
    """Feature the best-written report as a cluster's headline, not
    necessarily whichever happened to be newest. This only reorders
    WITHIN an already-formed cluster (same club, same time window,
    already confirmed to be the same story by _same_story) -- it never
    changes which stories are grouped, or where a cluster sits in the
    day's chronological order. That distinction is deliberate: the
    removed top-story feature broke by inferring importance ACROSS the
    whole feed from headline text; picking a lead article INSIDE a
    cluster whose identity is already established is a much smaller,
    safer claim.

    Ties within a tier keep the newest first, same as before this
    existed."""
    members = [cluster["primary"]] + cluster["more"]
    # Two-pass stable sort: sort by the tiebreak (recency, newest first)
    # first, then by the primary key (tier, best first). Python's sort
    # is stable, so within each tier the newest-first order from the
    # first pass survives.
    members.sort(key=lambda a: a.get("published", ""), reverse=True)
    members.sort(key=lambda a: _TIER_RANK.get(a.get("tier", "normal"), 1))
    cluster["primary"] = members[0]
    cluster["more"] = members[1:]


def group_by_day(articles_sorted, today):
    """articles_sorted must already be newest-first. Returns an ordered
    list of (label, date, [articles]) preserving that order. Dates are UK
    calendar days (see LOCAL_TZ)."""
    groups = []
    current_key = None
    for a in articles_sorted:
        try:
            d = local_date(a["published"])
        except Exception:
            d = today
        if d != current_key:
            groups.append((day_label(d, today), d, []))
            current_key = d
        groups[-1][2].append(a)
    return groups


# ------------------------------------------------------------------ club kits

# (pattern, main colour, second colour) for the small kit swatch beside each
# club's name. Traditional home colours rather than any one season's shirt:
# a 12px square only has to be recognisable, and these outlast kit launches.
# Patterns: solid (shirt over shorts), stripes (vertical), hoops
# (horizontal), halves, quarters. A club missing from here -- e.g. one just
# promoted into the EFL -- gets a neutral grey swatch rather than breaking
# the build; add it here when that happens.
CLUB_KITS = {
    # Championship
    "birmingham-city": ("solid", "#2B4FA8", "#FFFFFF"),
    "blackburn-rovers": ("halves", "#1F5FAD", "#FFFFFF"),
    "bolton-wanderers": ("solid", "#FFFFFF", "#1B2A5B"),
    "bristol-city": ("solid", "#E21A23", "#FFFFFF"),
    "burnley": ("solid", "#6C1D45", "#99D6EA"),
    "cardiff-city": ("solid", "#0070B5", "#FFFFFF"),
    "charlton-athletic": ("solid", "#D4021D", "#FFFFFF"),
    "derby-county": ("solid", "#FFFFFF", "#111111"),
    "lincoln-city": ("stripes", "#E0202C", "#FFFFFF"),
    "middlesbrough": ("solid", "#E11B22", "#FFFFFF"),
    "millwall": ("solid", "#0B1F4F", "#FFFFFF"),
    "norwich-city": ("solid", "#FFF200", "#00A650"),
    "portsmouth": ("solid", "#1A3D96", "#FFFFFF"),
    "preston-north-end": ("solid", "#FFFFFF", "#0A2240"),
    "queens-park-rangers": ("hoops", "#1D5BA4", "#FFFFFF"),
    "sheffield-united": ("stripes", "#EE2737", "#FFFFFF"),
    "southampton": ("stripes", "#D71920", "#FFFFFF"),
    "stoke-city": ("stripes", "#E03A3E", "#FFFFFF"),
    "swansea-city": ("solid", "#FFFFFF", "#121212"),
    "watford": ("solid", "#FBEE23", "#111111"),
    "west-bromwich-albion": ("stripes", "#122F67", "#FFFFFF"),
    "west-ham-united": ("solid", "#7A263A", "#1BB1E7"),
    "wolverhampton-wanderers": ("solid", "#FDB913", "#231F20"),
    "wrexham": ("solid", "#D71920", "#FFFFFF"),
    # League One
    "afc-wimbledon": ("solid", "#1B3C8C", "#FFE600"),
    "barnsley": ("solid", "#E21A23", "#FFFFFF"),
    "blackpool": ("solid", "#F68712", "#FFFFFF"),
    "bradford-city": ("stripes", "#7A1E3C", "#F9B22B"),
    "bromley": ("solid", "#FFFFFF", "#111111"),
    "burton-albion": ("solid", "#FDE500", "#111111"),
    "cambridge-united": ("solid", "#FDB913", "#111111"),
    "doncaster-rovers": ("hoops", "#E2001A", "#FFFFFF"),
    "huddersfield-town": ("stripes", "#0E63AD", "#FFFFFF"),
    "leicester-city": ("solid", "#003090", "#FFFFFF"),
    "leyton-orient": ("solid", "#E30613", "#FFFFFF"),
    "luton-town": ("solid", "#F78F1E", "#002D62"),
    "mansfield-town": ("solid", "#F5A12D", "#0055A5"),
    "milton-keynes-dons": ("solid", "#FFFFFF", "#111111"),
    "notts-county": ("stripes", "#111111", "#FFFFFF"),
    "oxford-united": ("solid", "#FFE100", "#002147"),
    "peterborough-united": ("solid", "#005BAC", "#FFFFFF"),
    "plymouth-argyle": ("solid", "#00573F", "#FFFFFF"),
    "reading": ("hoops", "#004494", "#FFFFFF"),
    "sheffield-wednesday": ("stripes", "#2F5DB0", "#FFFFFF"),
    "stevenage": ("solid", "#E2001A", "#FFFFFF"),
    "stockport-county": ("solid", "#1F3E8E", "#FFFFFF"),
    "wigan-athletic": ("stripes", "#1D59AF", "#FFFFFF"),
    "wycombe-wanderers": ("quarters", "#6CB4EE", "#0A2240"),
    # League Two
    "accrington-stanley": ("solid", "#E2001A", "#FFFFFF"),
    "barnet": ("solid", "#F7A800", "#111111"),
    "bristol-rovers": ("quarters", "#1A4A9C", "#FFFFFF"),
    "cheltenham-town": ("stripes", "#E2001A", "#FFFFFF"),
    "chesterfield": ("solid", "#0A3A8C", "#FFFFFF"),
    "colchester-united": ("stripes", "#005BAA", "#FFFFFF"),
    "crawley-town": ("solid", "#CF102D", "#FFFFFF"),
    "crewe-alexandra": ("solid", "#E2001A", "#FFFFFF"),
    "exeter-city": ("stripes", "#D71920", "#FFFFFF"),
    "fleetwood-town": ("solid", "#E2001A", "#FFFFFF"),
    "gillingham": ("solid", "#003B8E", "#FFFFFF"),
    "grimsby-town": ("stripes", "#111111", "#FFFFFF"),
    "newport-county": ("solid", "#F7A800", "#111111"),
    "northampton-town": ("solid", "#8A1538", "#FFFFFF"),
    "oldham-athletic": ("solid", "#004A9F", "#FFFFFF"),
    "port-vale": ("solid", "#FFFFFF", "#111111"),
    "rochdale": ("solid", "#0057B8", "#111111"),
    "rotherham-united": ("solid", "#E2001A", "#FFFFFF"),
    "salford-city": ("solid", "#E2001A", "#FFFFFF"),
    "shrewsbury-town": ("solid", "#003DA5", "#F4B223"),
    "swindon-town": ("solid", "#DA291C", "#FFFFFF"),
    "tranmere-rovers": ("solid", "#FFFFFF", "#0A3A8C"),
    "walsall": ("solid", "#E2001A", "#FFFFFF"),
    "york-city": ("solid", "#D71920", "#0A2240"),
}
DEFAULT_KIT = ("solid", "#8E8E93", "#C7C7CC")


def swatch(slug, small=False):
    kit, c1, c2 = CLUB_KITS.get(slug, DEFAULT_KIT)
    size = " sw-sm" if small else ""
    return f'<span class="sw{size}" data-kit="{kit}" style="--c1:{c1};--c2:{c2}" aria-hidden="true"></span>'


# Short forms for tight spots: the meta line of a story tagged with two
# clubs, the opponent in "Won 2-1 at Peterborough", and extra search terms
# in the club picker. Anything not listed just drops its generic suffix
# ("Stockport County" -> "Stockport").
SHORT_NAME_OVERRIDES = {
    "wolverhampton-wanderers": "Wolves",
    "west-bromwich-albion": "West Brom",
    "queens-park-rangers": "QPR",
    "milton-keynes-dons": "MK Dons",
    "preston-north-end": "Preston",
    "sheffield-wednesday": "Sheff Wed",
    "sheffield-united": "Sheff Utd",
    "west-ham-united": "West Ham",
    "bristol-city": "Bristol City",
    "bristol-rovers": "Bristol Rovers",
    "notts-county": "Notts County",
    "accrington-stanley": "Accrington",
    "crewe-alexandra": "Crewe",
    "plymouth-argyle": "Plymouth",
}
_SHORT_SUFFIX_RE = re.compile(r"\s+(City|Town|United|Rovers|Wanderers|Athletic|County|Albion)$")


def short_name(slug, names):
    if slug in SHORT_NAME_OVERRIDES:
        return SHORT_NAME_OVERRIDES[slug]
    return _SHORT_SUFFIX_RE.sub("", names.get(slug, slug.replace("-", " ").title()))


# ---------------------------------------------------------------- feed rows

def timestamp(iso):
    # Two spans: "22m" for the eye, "22 minutes ago" for screen readers
    # (which would otherwise read "22m" as "22 metres"). Filled in by the
    # page script so they stay current on a page left open.
    return (f'<time class="ts" datetime="{esc(iso)}">'
            f'<span class="ts-short" aria-hidden="true"></span><span class="ts-long sr"></span></time>')


def club_label(slugs, names):
    if not slugs:
        return "EFL"
    if len(slugs) == 1:
        return names.get(slugs[0], short_name(slugs[0], names))
    return ", ".join(short_name(s, names) for s in slugs)


def more_item(a):
    return f"""<div class="more-item">
  <a class="more-link" href="{esc(a.get('url',''))}" rel="noopener" target="_blank">{esc(a.get('title',''))}</a>
  <div class="more-meta">{esc(a.get('source',''))} &middot; {timestamp(a.get('published',''))}</div>
</div>"""


def story_row(cluster, names):
    """One story in a day's grouped list. The headline link is stretched
    over the whole row (see .row-title a::after) so the row is one big tap
    target, the way list rows behave in phone apps; the "N more reports"
    disclosure sits above that layer so it stays separately tappable."""
    primary = cluster["primary"]
    more = cluster["more"]
    slugs = primary.get("clubs", [])
    title = primary.get("title", "")
    excerpt = (primary.get("excerpt") or "").strip()
    excerpt_html = ""
    if excerpt and excerpt.lower() != title.strip().lower():
        excerpt_html = f'<p class="row-excerpt">{esc(excerpt)}</p>'
    more_html = ""
    if more:
        items = "\n".join(more_item(a) for a in more)
        noun = "report" if len(more) == 1 else "reports"
        more_html = f"""<details class="more-stories">
  <summary>{len(more)} more {noun}</summary>
  <div class="more-list">{items}</div>
</details>"""
    swatches = "".join(swatch(s, small=True) for s in slugs[:2])
    return f"""<article class="cluster" data-clubs="{esc(' '.join(slugs))}" data-division="{esc(primary.get('division',''))}" data-category="{esc(primary.get('category','news'))}">
  <div class="row-meta"><span class="sw-pair">{swatches}</span><span class="row-club">{esc(club_label(slugs, names))}</span>{timestamp(primary.get('published',''))}</div>
  <h3 class="row-title"><a href="{esc(primary.get('url',''))}" rel="noopener" target="_blank">{esc(title)}</a></h3>
  {excerpt_html}
  <div class="row-source">{esc(primary.get('source',''))}</div>
  {more_html}
</article>"""


# ------------------------------------------------------------- your clubs

ORDINALS = {1: "st", 2: "nd", 3: "rd"}


def ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ORDINALS.get(n % 10, 'th')}"


# football-data.co.uk abbreviates a few EFL names in its CSVs. Only used
# as a fallback now: the opponent is normally named from its slug via
# short_name(), and these cover a result whose opponent didn't resolve.
OPPONENT_DISPLAY = {
    "Peterboro": "Peterborough",
    "Bristol Rvs": "Bristol Rovers",
    "Sheffield Weds": "Sheff Wed",
}
FORM_WORDS = {"W": "won", "D": "drew", "L": "lost"}
RESULT_WORDS = {"W": "Won", "D": "Drew", "L": "Lost"}


def your_clubs_row(club, div_standings, names):
    """One row in "Your clubs": kit swatch, name, league position, points,
    form guide and last result. Rendered for all 72 clubs, every one
    hidden; the page script un-hides the ones being followed. Degrades
    piece by piece: no standings.json yet still gives a row with the club
    name and division, rather than no row at all."""
    slug = club["slug"]
    div_standings = div_standings or {}
    table_row = next((t for t in div_standings.get("table", []) if t.get("slug") == slug), None)
    last = div_standings.get("last_result", {}).get(slug)
    form = [r for r in div_standings.get("form", {}).get(slug, []) if r in FORM_WORDS]

    sub = esc(DIVISION_LABEL.get(club["division"], ""))
    pos_html = ""
    if table_row:
        pos_html = f'<span class="yc-pos"><span class="sr">Position </span>{ordinal(table_row["position"])}</span>'
        pts = table_row["points"]
        played = table_row["played"]
        sub += (f' &middot; {pts} {"pt" if pts == 1 else "pts"} from {played} '
                f'{"game" if played == 1 else "games"}')

    form_html = ""
    if form:
        dots = "".join(f'<span class="form-dot" data-r="{r}">{r}</span>' for r in form)
        label = f"Last {len(form)} results, oldest first: " + ", ".join(FORM_WORDS[r] for r in form)
        form_html = f'<span class="form" role="img" aria-label="{esc(label)}">{dots}</span>'

    last_html = ""
    if last and last.get("result") in RESULT_WORDS:
        if last.get("opponent_slug"):
            opp = short_name(last["opponent_slug"], names)
        else:
            raw = last.get("opponent_name", "")
            opp = OPPONENT_DISPLAY.get(raw, raw)
        venue = "v" if last.get("home_away") == "H" else "at"
        last_html = (f'<span class="yc-last">{RESULT_WORDS[last["result"]]} '
                     f'{last["gf"]}&ndash;{last["ga"]} {venue} {esc(opp)}</span>')

    form_row = f'<div class="yc-form-row">{form_html}{last_html}</div>' if (form_html or last_html) else ""
    return f"""<div class="yc-row" data-slug="{esc(slug)}" hidden>
  <div class="yc-top">{swatch(slug)}<span class="yc-name">{esc(club['name'])}</span>{pos_html}</div>
  <div class="yc-sub">{sub}</div>
  {form_row}
</div>"""


TICK_SVG = ('<svg class="tick" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<path d="M5 12.5l4.5 4.5L19 7" fill="none" stroke="currentColor" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def club_pill(c, names):
    # data-search adds the short form, so "wolves", "qpr", "west brom" or
    # "sheff wed" all find their club.
    search = f"{c['name']} {short_name(c['slug'], names)}".lower()
    return (f'<button class="pill" type="button" aria-pressed="false" data-slug="{esc(c["slug"])}" '
            f'data-search="{esc(search)}">{swatch(c["slug"], small=True)}'
            f'<span class="pill-name">{esc(c["name"])}</span>{TICK_SVG}</button>')


# ------------------------------------------------------------------ icons

# Blocky "fln" lettering as rectangles on a 64-unit grid. Shared by
# favicon.svg and the PNG home-screen icons, so the tab icon and the app
# icon are the same mark -- and neither depends on a font being installed.
FLN_GLYPH_RECTS = [
    (15, 20, 21, 46), (15, 17, 27, 23), (10, 27, 26, 32),  # f: stem, top, crossbar
    (30, 17, 36, 46),                                      # l
    (40, 27, 46, 46), (40, 27, 54, 32), (48, 27, 54, 46),  # n: left, arch, right
]
# Shrunk toward the centre so the lettering sits inside the "maskable" safe
# zone (a circle of 40% radius) when Android crops the icon to a shape.
FLN_GLYPH_SCALE = 0.85


def build_favicon_svg():
    k = FLN_GLYPH_SCALE
    rects = "".join(
        f'<rect x="{32 + (x0 - 32) * k:.2f}" y="{32 + (y0 - 32) * k:.2f}" '
        f'width="{(x1 - x0) * k:.2f}" height="{(y1 - y0) * k:.2f}"/>'
        for x0, y0, x1, y1 in FLN_GLYPH_RECTS
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#000000"/>
  <g fill="#ffffff">{rects}</g>
</svg>"""


def build_manifest():
    """PWA manifest -- makes the site installable to a phone home screen
    ("Add to Home Screen" on iOS, install prompt on Android) with its own
    icon and no browser chrome. Costs nothing, and is the cheapest possible
    step toward "make it an app later": if a real app ever happens, this
    is already the fallback for everyone who doesn't install it."""
    return {
        "name": SITE_TITLE,
        "short_name": "Football News",  # what shows under the home-screen icon -- the full title truncates awkwardly there
        "description": f"News from all 72 English Football League clubs. {SITE_ABOUT}",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#000000",
        "theme_color": "#000000",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }


def write_png_icon(path, size):
    """Generate the PWA icon as a real PNG. iOS in particular ignores SVG
    for home-screen icons, so favicon.svg isn't enough on its own. Written
    with zlib+struct rather than Pillow so the build has no extra
    dependency to install in CI (see the missing-dependency lesson in
    LEARNINGS.md -- fewer deps, fewer silent CI crashes).

    White "fln" on black, drawn from the same rectangles as favicon.svg.
    Full-bleed square on purpose: iOS and Android round the corners
    themselves."""
    import zlib
    import struct

    bg = (0, 0, 0)
    fg = (255, 255, 255)
    k = FLN_GLYPH_SCALE
    rects = [(32 + (x0 - 32) * k, 32 + (y0 - 32) * k, 32 + (x1 - 32) * k, 32 + (y1 - 32) * k)
             for x0, y0, x1, y1 in FLN_GLYPH_RECTS]
    unit = 64 / size

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter type 0 for each scanline
        uy = (y + 0.5) * unit
        for x in range(size):
            ux = (x + 0.5) * unit
            on = any(x0 <= ux < x1 and y0 <= uy < y1 for x0, y0, x1, y1 in rects)
            rows.extend(fg if on else bg)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


SEARCH_SVG = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
              '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>')
SUN_SVG = ('<svg class="icon-sun" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
           'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
           '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
           'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')
MOON_SVG = ('<svg class="icon-moon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
            '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>')


# ------------------------------------------------------------------- page

# Runs in <head>, before first paint, so a dark-mode visitor never sees a
# white flash while the main script (end of <body>) loads. Duplicates a
# little of the main script on purpose: it MUST run here, inline.
HEAD_JS = r"""
(function () {
  try {
    var saved = localStorage.getItem("eflfeed.theme");
    var dark = saved ? saved === "dark"
      : !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (dark) document.documentElement.setAttribute("data-theme", "dark");
    var meta = document.getElementById("theme-color");
    if (meta) meta.setAttribute("content", dark ? "#000000" : "#F2F2F7");
  } catch (e) {}
})();
"""

PAGE_CSS = r"""
  /* `hidden` must ALWAYS win. The browser's own [hidden]{display:none}
     lives in the UA stylesheet, and ANY author rule that sets `display`
     on the same element beats it -- so `.yc-row{display:flex}` drew all
     72 "Your clubs" rows even while JS had correctly set `hidden` on 71
     of them. This bit three times (.cluster, .day-group, .yc-row); this
     one rule ends the whole class of bug for every component. jsdom can't
     catch it (it applies no CSS) -- test filtering in a real browser.
     The <dialog> is the one exception: it opens via its `open` attribute,
     so its display rule below is scoped to [open] for the same reason. */
  [hidden] { display: none !important; }

  :root {
    color-scheme: light;
    --bg: #F2F2F7;
    --group: #FFFFFF;
    --raised: #E5E5EA;
    --chip: #E5E5EA;
    --seg-track: #E3E3E8;
    --seg-on: #FFFFFF;
    --seg-off: #3C3C43;
    --seg-shadow: 0 1px 3px rgba(0, 0, 0, 0.14);
    --text: #000000;
    --muted: #6C6C70;
    --sep: #C6C6C8;
    --pill-line: #D1D1D6;
    --link: #0066CC;
    --on-bg: #000000;
    --on-fg: #FFFFFF;
    --hover: #F7F7F9;
    --mark-bg: #000000;
    --mark-fg: #FFFFFF;
    --backdrop: rgba(0, 0, 0, 0.4);
    /* Form colours: each pair checked at 4.5:1 or better for the 12px
       letters, in both themes. Light needs darker fills than dark. */
    --win: #1F7A35;  --win-fg: #FFFFFF;
    --draw: #6C6C70; --draw-fg: #FFFFFF;
    --loss: #D70015; --loss-fg: #FFFFFF;
  }
  [data-theme="dark"] {
    color-scheme: dark;
    --bg: #000000;
    --group: #1C1C1E;
    --raised: #2C2C2E;
    --chip: #1C1C1E;
    --seg-track: #1C1C1E;
    --seg-on: #636366;
    --seg-off: #D1D1D6;
    --seg-shadow: none;
    --text: #FFFFFF;
    --muted: #A1A1A6;
    --sep: #38383A;
    --pill-line: transparent;
    --link: #0A84FF;
    --on-bg: #FFFFFF;
    --on-fg: #000000;
    --hover: #242426;
    --mark-bg: #FFFFFF;
    --mark-fg: #000000;
    --backdrop: rgba(0, 0, 0, 0.6);
    --win: #30D158;  --win-fg: #000000;
    --draw: #636366; --draw-fg: #FFFFFF;
    --loss: #FF453A; --loss-fg: #000000;
  }

  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
  html:has(#picker[open]) { overflow: hidden; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto,
      "Helvetica Neue", Arial, system-ui, sans-serif;
    font-size: 17px; line-height: 1.35;
    -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
    -webkit-tap-highlight-color: transparent;
  }
  button { font: inherit; color: inherit; }
  :focus-visible { outline: 2px solid var(--link); outline-offset: 2px; }
  .group :focus-visible { outline-offset: -2px; }
  .sr {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }

  .app {
    max-width: 680px; margin: 0 auto; padding: 8px 16px 48px;
    display: flex; flex-direction: column; gap: 16px;
  }

  /* ---- update banner: in normal flow, fades in via .show (never via
     `hidden`, which can't transition). visibility keeps the collapsed
     button out of the keyboard tab order. */
  .banner-wrap { max-width: 680px; margin: 0 auto; padding: 0 16px; }
  #update-banner {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    background: var(--group); border-radius: 14px; font-size: 15px; font-weight: 600;
    max-height: 0; opacity: 0; overflow: hidden; padding: 0 16px; margin-top: 0;
    visibility: hidden;
    transition: max-height 0.25s ease, opacity 0.2s ease, margin-top 0.25s ease,
      padding 0.25s ease, visibility 0s linear 0.25s;
  }
  #update-banner.show {
    max-height: 80px; opacity: 1; margin-top: 8px; padding: 10px 16px;
    visibility: visible; transition-delay: 0s;
  }
  #update-btn {
    flex: none; height: 36px; padding: 0 14px; border: none; border-radius: 18px;
    background: var(--on-bg); color: var(--on-fg); font-size: 15px; font-weight: 600; cursor: pointer;
  }

  /* ---- top bar */
  .topbar { display: flex; align-items: center; justify-content: space-between; min-height: 44px; }
  .brand {
    display: flex; align-items: center; gap: 8px; min-width: 0;
    color: var(--muted); font-size: 15px; font-weight: 600; text-decoration: none;
  }
  .brand-mark {
    flex: none; background: var(--mark-bg); color: var(--mark-fg); font-weight: 800;
    font-size: 15px; line-height: 1; padding: 5px 8px; border-radius: 8px;
  }
  .brand-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .topbar-actions { display: flex; gap: 4px; flex: none; }
  .icon-btn {
    width: 44px; height: 44px; border: none; border-radius: 22px; background: transparent;
    color: var(--text); display: flex; align-items: center; justify-content: center; cursor: pointer;
  }
  .icon-btn:hover { background: var(--group); }
  .icon-moon { display: none; }
  :root:not([data-theme="dark"]) .icon-moon { display: block; }
  :root:not([data-theme="dark"]) .icon-sun { display: none; }

  /* ---- large title */
  .title-block { display: flex; flex-direction: column; gap: 2px; }
  .title-block h1 { margin: 0; font-size: 34px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.2; }
  .title-date { font-size: 15px; color: var(--muted); }

  /* ---- My clubs / All 72 clubs */
  .segmented {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 3px; padding: 3px;
    background: var(--seg-track); border-radius: 12px;
  }
  .segmented button {
    height: 40px; border: none; border-radius: 9px; background: transparent;
    color: var(--seg-off); font-size: 15px; font-weight: 500; cursor: pointer;
  }
  .segmented button[aria-pressed="true"] {
    background: var(--seg-on); color: var(--text); font-weight: 600; box-shadow: var(--seg-shadow);
  }

  /* ---- sections and grouped panels */
  .section { display: flex; flex-direction: column; gap: 6px; }
  .section-head { display: flex; align-items: center; justify-content: space-between; min-height: 44px; }
  .section-title { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.01em; line-height: 1.25; }
  .text-btn {
    background: none; border: none; padding: 0 4px; min-height: 44px;
    color: var(--link); font-size: 17px; cursor: pointer;
  }
  .text-btn:disabled { color: var(--muted); cursor: default; }
  .group { background: var(--group); border-radius: 14px; overflow: hidden; }
  .btn-row { display: flex; flex-wrap: wrap; gap: 8px; }
  .btn {
    height: 44px; padding: 0 18px; border: none; border-radius: 22px;
    background: var(--raised); color: var(--text); font-size: 15px; font-weight: 600; cursor: pointer;
  }
  .btn-primary { background: var(--on-bg); color: var(--on-fg); }

  /* Hairline between rows, inset like iOS lists. Drawn on each VISIBLE row
     that has a visible row somewhere before it, so filtering never leaves
     a stray line at the top of a panel or a double line in the middle. */
  .yc-row:not([hidden]) ~ .yc-row:not([hidden])::before,
  .cluster:not([hidden]) ~ .cluster:not([hidden])::before {
    content: ""; position: absolute; top: 0; right: 0; height: 1px; background: var(--sep);
  }
  .yc-row:not([hidden]) ~ .yc-row:not([hidden])::before { left: 56px; }
  .cluster:not([hidden]) ~ .cluster:not([hidden])::before { left: 16px; }

  /* ---- kit swatches */
  .sw {
    flex: none; display: inline-block; width: 28px; height: 28px; border-radius: 8px;
    background: var(--c1); box-shadow: inset 0 0 0 1px rgba(128, 128, 128, 0.35);
  }
  .sw-sm { width: 12px; height: 12px; border-radius: 4px; }
  .sw[data-kit="solid"] { background: linear-gradient(180deg, var(--c1) 68%, var(--c2) 68%); }
  .sw[data-kit="stripes"] { background: repeating-linear-gradient(90deg, var(--c1) 0 16.667%, var(--c2) 16.667% 33.333%); }
  .sw[data-kit="hoops"] { background: repeating-linear-gradient(180deg, var(--c1) 0 16.667%, var(--c2) 16.667% 33.333%); }
  .sw[data-kit="halves"] { background: linear-gradient(90deg, var(--c1) 50%, var(--c2) 50%); }
  .sw[data-kit="quarters"] { background: conic-gradient(var(--c2) 0 25%, var(--c1) 0 50%, var(--c2) 0 75%, var(--c1) 0); }
  .sw-pair { display: flex; gap: 3px; flex: none; }
  .sw-pair:empty { display: none; }

  /* ---- your clubs */
  .yc-row { position: relative; display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; }
  .yc-top { display: flex; align-items: center; gap: 12px; }
  .yc-name { flex: 1; min-width: 0; font-size: 17px; font-weight: 600; }
  .yc-pos { flex: none; font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .yc-sub, .yc-form-row { padding-left: 40px; }
  .yc-sub { font-size: 13px; color: var(--muted); }
  .yc-form-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
  .form { display: flex; gap: 4px; }
  .form-dot {
    width: 24px; height: 24px; border-radius: 12px; display: flex; align-items: center;
    justify-content: center; font-size: 12px; font-weight: 700; line-height: 1;
  }
  .form-dot[data-r="W"] { background: var(--win); color: var(--win-fg); }
  .form-dot[data-r="D"] { background: var(--draw); color: var(--draw-fg); }
  .form-dot[data-r="L"] { background: var(--loss); color: var(--loss-fg); }
  .yc-last { font-size: 13px; color: var(--muted); }
  .yc-empty { display: flex; flex-direction: column; align-items: flex-start; gap: 12px; padding: 16px; }
  .yc-empty p, .empty p { margin: 0; font-size: 15px; color: var(--muted); }

  /* ---- category chips: one scrolling row, edge to edge on phones */
  .chips {
    display: flex; gap: 8px; overflow-x: auto; scrollbar-width: none;
    margin: -3px -16px; padding: 3px 16px;
  }
  .chips::-webkit-scrollbar { display: none; }
  .chip {
    flex: none; height: 44px; padding: 0 16px; border: none; border-radius: 22px;
    background: var(--chip); color: var(--text); font-size: 15px; cursor: pointer;
  }
  .chip[aria-pressed="true"] { background: var(--on-bg); color: var(--on-fg); font-weight: 600; }

  /* ---- feed */
  #feed { display: flex; flex-direction: column; gap: 24px; }
  .day-group { display: flex; flex-direction: column; gap: 10px; }
  .cluster { position: relative; display: flex; flex-direction: column; gap: 6px; padding: 14px 16px; }
  @media (hover: hover) { .cluster:hover { background: var(--hover); } }
  .row-meta { display: flex; align-items: center; gap: 8px; min-width: 0; font-size: 13px; color: var(--muted); }
  .row-club { flex: 1; min-width: 0; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ts { flex: none; font-variant-numeric: tabular-nums; }
  .row-title { margin: 0; font-size: 17px; font-weight: 600; line-height: 1.3; }
  .row-title a { color: inherit; text-decoration: none; }
  .row-title a::after { content: ""; position: absolute; inset: 0; }
  .row-title a:focus-visible { outline: none; }
  .row-title a:focus-visible::after { outline: 2px solid var(--link); outline-offset: -3px; border-radius: 12px; }
  .row-excerpt {
    margin: 0; font-size: 15px; line-height: 1.35; color: var(--muted);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .row-source { font-size: 13px; color: var(--muted); }

  .more-stories { position: relative; z-index: 1; }
  .more-stories summary {
    display: inline-flex; align-items: center; gap: 8px; height: 36px; margin-top: 4px;
    padding: 0 14px; border-radius: 18px; background: var(--raised);
    font-size: 13px; font-weight: 600; cursor: pointer; list-style: none; user-select: none;
  }
  .more-stories summary::-webkit-details-marker { display: none; }
  .more-stories summary::after {
    content: ""; width: 6px; height: 6px; border-right: 2px solid currentColor;
    border-bottom: 2px solid currentColor; transform: translateY(-2px) rotate(45deg); transition: transform 0.15s;
  }
  .more-stories[open] summary::after { transform: translateY(1px) rotate(-135deg); }
  .more-list { display: flex; flex-direction: column; margin-top: 10px; }
  .more-item { display: flex; flex-direction: column; gap: 4px; padding: 10px 0; border-top: 1px solid var(--sep); }
  .more-link { color: var(--text); text-decoration: none; font-size: 15px; font-weight: 600; line-height: 1.3; }
  .more-link:hover { text-decoration: underline; }
  .more-meta { font-size: 13px; color: var(--muted); }

  .empty { display: flex; flex-direction: column; align-items: flex-start; gap: 12px; padding: 20px 16px; }

  /* ---- footer */
  footer {
    display: flex; flex-direction: column; gap: 8px; padding-top: 8px;
    font-size: 13px; line-height: 1.5; color: var(--muted);
  }
  footer p { margin: 0; }
  footer .tagline { color: var(--text); font-size: 15px; font-weight: 600; }
  .footer-links { display: flex; flex-wrap: wrap; gap: 4px 16px; }
  footer a { color: var(--link); text-decoration: none; }
  footer a:hover { text-decoration: underline; }

  /* ---- club picker: a full-screen sheet on phones, a centred panel on
     wider screens. display:flex ONLY while [open] -- an unscoped
     display rule would beat the browser's own dialog:not([open]) hiding,
     the same trap as [hidden] above. */
  #picker {
    padding: 0; border: none; background: var(--bg); color: var(--text);
    width: 100%; max-width: 100%; height: 100%; max-height: 100%; margin: 0;
  }
  #picker[open] { display: flex; flex-direction: column; }
  #picker::backdrop { background: var(--backdrop); }
  @media (min-width: 720px) {
    #picker {
      width: 640px; height: min(86vh, 860px); margin: auto; border-radius: 16px;
      box-shadow: 0 0 0 1px var(--sep), 0 24px 64px rgba(0, 0, 0, 0.45);
    }
  }
  .sheet-head {
    display: grid; grid-template-columns: 1fr auto 1fr; align-items: center;
    padding: 4px 12px; border-bottom: 1px solid var(--sep);
  }
  .sheet-head h2 { margin: 0; font-size: 17px; font-weight: 600; text-align: center; }
  #picker-clear { justify-self: start; }
  #picker-done { justify-self: end; font-weight: 600; }
  .sheet-search { position: relative; margin: 12px 16px 0; }
  .sheet-search svg {
    position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
    width: 18px; height: 18px; color: var(--muted); pointer-events: none;
  }
  /* 17px, not smaller: iOS zooms the whole page into any input under 16px. */
  #club-search {
    width: 100%; height: 40px; padding: 0 12px 0 36px; border: none; border-radius: 10px;
    background: var(--chip); color: var(--text); font: inherit; font-size: 17px;
    -webkit-appearance: none; appearance: none;
  }
  #club-search::placeholder { color: var(--muted); }
  .sheet-body { flex: 1; overflow-y: auto; overscroll-behavior: contain; padding: 4px 16px 32px; }
  .division { margin-top: 12px; }
  .division-head { display: flex; align-items: center; justify-content: space-between; }
  .division-head h3 {
    margin: 0; font-size: 13px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.04em; color: var(--muted);
  }
  .league-pill { font-size: 15px; }
  .pills { display: flex; flex-wrap: wrap; gap: 8px; }
  .pill {
    display: inline-flex; align-items: center; gap: 8px; min-height: 40px; padding: 0 14px 0 12px;
    border: none; border-radius: 20px; background: var(--group); color: var(--text);
    font-size: 15px; cursor: pointer; box-shadow: inset 0 0 0 1px var(--pill-line);
  }
  .pill .tick { display: none; }
  .pill[aria-pressed="true"] { background: var(--on-bg); color: var(--on-fg); font-weight: 600; box-shadow: none; }
  .pill[aria-pressed="true"] .tick { display: block; }
  #search-empty { margin: 16px 0 0; font-size: 15px; color: var(--muted); }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { transition: none !important; }
  }
"""

PAGE_JS = r"""
(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }
  function all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  // Storage can throw (Safari private browsing, blocked site data). Every
  // read and write goes through these, so the page still works -- it just
  // can't remember choices -- instead of dying on the first line.
  function readStr(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function writeStr(key, val) { try { localStorage.setItem(key, val); } catch (e) {} }
  function readList(key) {
    try { var v = JSON.parse(localStorage.getItem(key) || "[]"); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }

  // ---- theme: follows the phone's setting until the sun/moon button is
  // used, then remembers that choice.
  var THEME_KEY = "eflfeed.theme";
  var root = document.documentElement;
  var themeBtn = $("theme-toggle");
  var themeMeta = $("theme-color");
  function applyTheme(t) {
    var dark = t === "dark";
    if (dark) root.setAttribute("data-theme", "dark"); else root.removeAttribute("data-theme");
    themeBtn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    if (themeMeta) themeMeta.setAttribute("content", dark ? "#000000" : "#F2F2F7");
  }
  var systemDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  applyTheme(readStr(THEME_KEY) || (systemDark && systemDark.matches ? "dark" : "light"));
  if (systemDark) {
    var followSystem = function (e) { if (!readStr(THEME_KEY)) applyTheme(e.matches ? "dark" : "light"); };
    if (systemDark.addEventListener) systemDark.addEventListener("change", followSystem);
    else if (systemDark.addListener) systemDark.addListener(followSystem);
  }
  themeBtn.addEventListener("click", function () {
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    writeStr(THEME_KEY, next);
  });

  // ---- state. Same storage keys as the previous design, so returning
  // visitors keep their clubs and categories. "mode" is new: My clubs
  // vs All 72 clubs. Someone who already follows clubs starts in My clubs,
  // which matches how the old page behaved for them.
  var CLUBS_KEY = "eflfeed.clubs", CATS_KEY = "eflfeed.categories", MODE_KEY = "eflfeed.mode";
  var clubPills = all("#picker .pill");
  var knownSlugs = clubPills.map(function (p) { return p.dataset.slug; });
  var state = {
    // Drops clubs no longer in the EFL (relegated to the National League).
    clubs: readList(CLUBS_KEY).filter(function (s) { return knownSlugs.indexOf(s) !== -1; }),
    cats: readList(CATS_KEY),
    mode: readStr(MODE_KEY)
  };
  if (state.mode !== "mine" && state.mode !== "all") state.mode = state.clubs.length ? "mine" : "all";
  function save() {
    writeStr(CLUBS_KEY, JSON.stringify(state.clubs));
    writeStr(CATS_KEY, JSON.stringify(state.cats));
    writeStr(MODE_KEY, state.mode);
  }
  function setClubs(next) {
    // Picking a first club means "show me my club" -- switch to My clubs.
    if (state.clubs.length === 0 && next.length > 0) state.mode = "mine";
    state.clubs = next;
    save();
    render();
  }

  var feed = $("feed");
  var clusters = all("#feed .cluster");
  var dayGroups = all("#feed .day-group");
  var modeBtns = all(".segmented button");
  var chipsRow = $("chips");
  var chipAll = $("chip-all");
  var catChips = all(".chip[data-category]");
  var ycList = $("yc-list");
  var ycRows = all(".yc-row");
  var ycEmpty = $("yc-empty");
  var ycBrowse = $("yc-browse-all");
  var editBtn = $("edit-clubs");
  var emptyState = $("empty-state");
  var emptyText = $("empty-text");
  var emptyAll = $("empty-all");
  var emptyCats = $("empty-cats");
  var leaguePills = all(".league-pill");
  var clearBtn = $("picker-clear");
  var rowBySlug = {};
  ycRows.forEach(function (r) { rowBySlug[r.dataset.slug] = r; });
  function divisionSlugs(div) {
    return all('.division[data-division="' + div + '"] .pill').map(function (p) { return p.dataset.slug; });
  }

  // ---- dates. Server labels are UK days at build time; these relabel
  // against the reader's clock so a page left open overnight stays right.
  function longDate(d) { return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }); }
  function dayLabel(iso) {
    var p = iso.split("-");
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var diff = Math.round((d - today) / 86400000);  // round: DST days are 23h/25h
    if (diff === 0) return state.mode === "mine" ? "Latest for your clubs" : "Latest";
    if (diff === -1) return "Yesterday";
    return longDate(d);
  }

  function render() {
    var sel = state.clubs, cats = state.cats, mine = state.mode === "mine";
    var noClubs = mine && sel.length === 0;

    modeBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)); });
    chipAll.setAttribute("aria-pressed", String(cats.length === 0));
    catChips.forEach(function (c) { c.setAttribute("aria-pressed", String(cats.indexOf(c.dataset.category) !== -1)); });
    clubPills.forEach(function (p) { p.setAttribute("aria-pressed", String(sel.indexOf(p.dataset.slug) !== -1)); });

    // Club AND category: a story shows only if it passes both.
    var anyVisible = false;
    clusters.forEach(function (c) {
      var clubs = (c.dataset.clubs || "").split(" ");
      var clubOk = !mine || clubs.some(function (s) { return sel.indexOf(s) !== -1; });
      var catOk = cats.length === 0 || cats.indexOf(c.dataset.category) !== -1;
      c.hidden = !(clubOk && catOk);
      if (!c.hidden) anyVisible = true;
    });
    dayGroups.forEach(function (g) {
      g.hidden = !all(".cluster", g).some(function (c) { return !c.hidden; });
      var h = g.querySelector(".day-heading");
      if (h && g.dataset.date) h.textContent = dayLabel(g.dataset.date);
    });

    // Your clubs: only the clubs actually followed, in the order picked.
    ycRows.forEach(function (r) { r.hidden = sel.indexOf(r.dataset.slug) === -1; });
    sel.forEach(function (s) { if (rowBySlug[s]) ycList.appendChild(rowBySlug[s]); });
    ycEmpty.hidden = sel.length > 0;
    ycBrowse.hidden = !mine;
    editBtn.hidden = sel.length === 0;

    // My clubs with nothing picked: the prompt in Your clubs says it all;
    // no filter chips or "nothing matches" box underneath it.
    chipsRow.hidden = noClubs;
    emptyState.hidden = noClubs || anyVisible;
    if (!emptyState.hidden) {
      var what = cats.length ? "in these categories " : "";
      emptyText.textContent = clusters.length === 0 ? "No stories yet. Check back soon."
        : mine ? "No stories " + what + "for your clubs right now."
        : "No stories " + what + "right now.";
      emptyCats.hidden = cats.length === 0;
      emptyAll.hidden = !mine;
    }

    // Picker: "Select all" per division doubles as a status indicator.
    leaguePills.forEach(function (lp) {
      var slugs = divisionSlugs(lp.dataset.division);
      var allOn = slugs.length > 0 && slugs.every(function (s) { return sel.indexOf(s) !== -1; });
      lp.textContent = allOn ? "Remove all" : "Select all";
      lp.setAttribute("aria-label", (allOn ? "Remove all " : "Select all ") + lp.dataset.label + " clubs");
    });
    clearBtn.disabled = sel.length === 0;
  }

  modeBtns.forEach(function (b) {
    b.addEventListener("click", function () { state.mode = b.dataset.mode; save(); render(); });
  });
  chipAll.addEventListener("click", function () { state.cats = []; save(); render(); });
  catChips.forEach(function (c) {
    c.addEventListener("click", function () {
      var i = state.cats.indexOf(c.dataset.category);
      if (i === -1) state.cats.push(c.dataset.category); else state.cats.splice(i, 1);
      // Every category ticked is the same as Everything -- say so.
      if (state.cats.length === catChips.length) state.cats = [];
      save();
      render();
    });
  });
  clubPills.forEach(function (p) {
    p.addEventListener("click", function () {
      var next = state.clubs.slice();
      var i = next.indexOf(p.dataset.slug);
      if (i === -1) next.push(p.dataset.slug); else next.splice(i, 1);
      setClubs(next);
    });
  });
  leaguePills.forEach(function (lp) {
    lp.addEventListener("click", function () {
      var slugs = divisionSlugs(lp.dataset.division);
      var next = state.clubs.slice();
      var allOn = slugs.every(function (s) { return next.indexOf(s) !== -1; });
      if (allOn) next = next.filter(function (s) { return slugs.indexOf(s) === -1; });
      else slugs.forEach(function (s) { if (next.indexOf(s) === -1) next.push(s); });
      setClubs(next);
    });
  });
  clearBtn.addEventListener("click", function () { setClubs([]); });
  function showAllClubs() { state.mode = "all"; save(); render(); }
  ycBrowse.addEventListener("click", showAllClubs);
  emptyAll.addEventListener("click", showAllClubs);
  emptyCats.addEventListener("click", function () { state.cats = []; save(); render(); });

  // ---- club picker sheet (<dialog>: Esc closes it, focus is trapped
  // inside while open, and focus returns to the button that opened it).
  var picker = $("picker");
  var clubSearch = $("club-search");
  var searchEmpty = $("search-empty");
  var doneBtn = $("picker-done");
  var hasDialog = typeof picker.showModal === "function";
  function openPicker(focusSearch) {
    if (hasDialog) { if (!picker.open) picker.showModal(); }
    else picker.setAttribute("open", "");
    (focusSearch ? clubSearch : doneBtn).focus();
  }
  function resetSearch() { clubSearch.value = ""; filterPills(""); }
  function closePicker() {
    if (hasDialog) picker.close();
    else { picker.removeAttribute("open"); resetSearch(); }
  }
  picker.addEventListener("close", resetSearch);
  doneBtn.addEventListener("click", closePicker);
  // A click landing on the dialog element itself is a click on the dimmed
  // backdrop around the panel (wide screens) -- treat it as Done.
  picker.addEventListener("click", function (e) { if (e.target === picker) closePicker(); });
  $("search-btn").addEventListener("click", function () { openPicker(true); });
  editBtn.addEventListener("click", function () { openPicker(false); });
  $("yc-choose").addEventListener("click", function () { openPicker(false); });

  function filterPills(q) {
    q = q.trim().toLowerCase();
    clubPills.forEach(function (p) {
      p.hidden = q !== "" && (p.dataset.search || "").indexOf(q) === -1;
    });
    var anyMatch = false;
    all("#picker .division").forEach(function (d) {
      var visible = all(".pill", d).some(function (p) { return !p.hidden; });
      d.hidden = !visible;
      if (visible) anyMatch = true;
    });
    // "Select all" means the whole division -- confusing while the list is
    // filtered down to one or two clubs, so it steps aside during a search.
    leaguePills.forEach(function (lp) { lp.hidden = q !== ""; });
    searchEmpty.hidden = anyMatch;
  }
  clubSearch.addEventListener("input", function () { filterPills(clubSearch.value); });
  // Esc and the little clear (x) in a search box empty it without always
  // sending "input"; "search" covers those.
  clubSearch.addEventListener("search", function () { filterPills(clubSearch.value); });
  clubSearch.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    var visible = clubPills.filter(function (p) { return !p.hidden; });
    if (visible.length === 1) { e.preventDefault(); visible[0].click(); }
  });

  // ---- relative times, kept fresh on a page left open
  function ago(iso, long) {
    var then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    var mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 1) return long ? "just now" : "now";
    if (mins < 60) return long ? mins + (mins === 1 ? " minute ago" : " minutes ago") : mins + "m";
    var hrs = Math.round(mins / 60);
    if (hrs < 24) return long ? hrs + (hrs === 1 ? " hour ago" : " hours ago") : hrs + "h";
    var days = Math.round(hrs / 24);
    return long ? days + (days === 1 ? " day ago" : " days ago") : days + "d";
  }
  var timeEls = all("#feed .ts");
  // Footer "Updated 4 minutes ago": the build stamps its own time into the
  // page, so this shows even if version.json can't be fetched.
  var builtTime = $("built-time");
  function refreshTimestamps() {
    timeEls.forEach(function (el) {
      var iso = el.getAttribute("datetime");
      el.firstChild.textContent = ago(iso, false);
      el.lastChild.textContent = ago(iso, true);
    });
    var built = builtTime.getAttribute("datetime");
    if (built) builtTime.textContent = ago(built, true);
  }
  var todayEl = $("today-date");
  function refreshDate() {
    var label = longDate(new Date());
    if (todayEl.textContent !== label) { todayEl.textContent = label; return true; }
    return false;
  }

  render();
  refreshDate();
  refreshTimestamps();
  setInterval(function () {
    refreshTimestamps();
    if (refreshDate()) render();  // just went past midnight: relabel days
  }, 60 * 1000);

  // ---- update checking: on load, on tab focus, and every 5 minutes.
  // Auto-reload silently once per session (sessionStorage guard against
  // reload loops); after that, a banner instead of yanking the page
  // mid-read.
  var banner = $("update-banner");
  var currentVersion = null;
  function checkVersion() {
    fetch("version.json", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (v) {
        // Trust signal: shows the feed is actually live, not abandoned.
        if (v.built) {
          builtTime.setAttribute("datetime", v.built);
          builtTime.textContent = ago(v.built, true);
        }
        if (currentVersion === null) { currentVersion = v.version; return; }
        if (v.version === currentVersion) return;
        var reloaded = false;
        try { reloaded = !!sessionStorage.getItem("eflfeed.autoReloaded"); } catch (e) {}
        if (!reloaded) {
          try { sessionStorage.setItem("eflfeed.autoReloaded", "1"); } catch (e) {}
          location.reload();
        } else {
          banner.classList.add("show");
        }
      })
      .catch(function () { /* offline or blocked -- try again later */ });
  }
  $("update-btn").addEventListener("click", function () { location.reload(); });
  checkVersion();
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") checkVersion();
  });
  setInterval(checkVersion, 5 * 60 * 1000);
})();
"""


def build_html(articles, clubs, standings):
    today = datetime.now(LOCAL_TZ).date()
    names = {c["slug"]: c["name"] for c in clubs}
    articles_sorted = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)

    feed_sections = []
    for i, (label, d, group) in enumerate(group_by_day(articles_sorted, today)):
        rows = "\n".join(story_row(c, names) for c in cluster_by_clubs(group))
        feed_sections.append(f"""<section class="day-group" data-date="{d.isoformat()}" aria-labelledby="day-{i}">
  <h2 class="section-title day-heading" id="day-{i}">{esc(label)}</h2>
  <div class="group">
{rows}
  </div>
</section>""")
    feed_html = "\n".join(feed_sections)

    your_clubs_html = "\n".join(your_clubs_row(c, standings.get(c["division"]), names) for c in clubs)

    picker_sections = []
    for d in DIVISION_ORDER:
        div_clubs = [c for c in clubs if c["division"] == d]
        pills = "\n".join(club_pill(c, names) for c in div_clubs)
        picker_sections.append(f"""<div class="division" data-division="{esc(d)}">
  <div class="division-head"><h3>{DIVISION_LABEL[d]}</h3><button class="text-btn league-pill" type="button" data-division="{esc(d)}" data-label="{DIVISION_LABEL[d]}">Select all</button></div>
  <div class="pills">{pills}</div>
</div>""")
    picker_html = "\n".join(picker_sections)
    club_count = len(clubs)

    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(SITE_TITLE)} &mdash; {esc(SITE_TAGLINE)}</title>
<meta name="description" content="News from all 72 English Football League clubs, in one feed. {esc(SITE_ABOUT)}">
<link rel="canonical" href="{esc(SITE_URL)}/">
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<link rel="apple-touch-icon" href="icon-192.png">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" id="theme-color" content="#F2F2F7">
<meta name="color-scheme" content="light dark">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE_TITLE)}" href="feed.xml">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{esc(SITE_TITLE)}">
<meta property="og:title" content="{esc(SITE_TITLE)} &mdash; {esc(SITE_TAGLINE)}">
<meta property="og:description" content="Championship, League One and League Two news in one place. Pick your clubs and it remembers.">
<meta property="og:url" content="{esc(SITE_URL)}/">
<meta name="twitter:card" content="summary">
<script>{HEAD_JS}</script>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="banner-wrap">
  <div id="update-banner" role="status"><span>New stories are in.</span><button id="update-btn" type="button">Refresh</button></div>
</div>
<div class="app">
  <header class="topbar">
    <a class="brand" href="./"><span class="brand-mark" aria-hidden="true">fln</span><span class="brand-name">{esc(SITE_TITLE)}</span></a>
    <span class="topbar-actions">
      <button id="search-btn" class="icon-btn" type="button" aria-label="Find a club">{SEARCH_SVG}</button>
      <button id="theme-toggle" class="icon-btn" type="button" aria-label="Switch to dark mode">{SUN_SVG}{MOON_SVG}</button>
    </span>
  </header>

  <div class="title-block">
    <h1>Today</h1>
    <span class="title-date" id="today-date">{esc(long_date(today))}</span>
  </div>

  <div class="segmented" role="group" aria-label="Which stories to show">
    <button type="button" data-mode="mine" aria-pressed="false">My clubs</button>
    <button type="button" data-mode="all" aria-pressed="true">All {club_count} clubs</button>
  </div>

  <section id="your-clubs" class="section" aria-labelledby="yc-title">
    <div class="section-head">
      <h2 class="section-title" id="yc-title">Your clubs</h2>
      <button id="edit-clubs" class="text-btn" type="button" hidden>Edit</button>
    </div>
    <div class="group" id="yc-list">
      <div id="yc-empty" class="yc-empty">
        <p>Pick the clubs you follow to see their league position, recent form and news first.</p>
        <div class="btn-row">
          <button id="yc-choose" class="btn btn-primary" type="button">Choose clubs</button>
          <button id="yc-browse-all" class="btn" type="button" hidden>Browse all {club_count} clubs</button>
        </div>
      </div>
{your_clubs_html}
    </div>
  </section>

  <div class="chips" id="chips" role="group" aria-label="Story types">
    <button class="chip" id="chip-all" type="button" aria-pressed="true">Everything</button>
    <button class="chip" type="button" aria-pressed="false" data-category="transfer">Transfers</button>
    <button class="chip" type="button" aria-pressed="false" data-category="match">Matches</button>
    <button class="chip" type="button" aria-pressed="false" data-category="opinion">Opinion</button>
    <button class="chip" type="button" aria-pressed="false" data-category="news">News</button>
  </div>

  <main id="feed">
{feed_html}
  </main>

  <section id="empty-state" class="group empty" aria-live="polite" hidden>
    <p id="empty-text">No stories right now.</p>
    <div class="btn-row">
      <button id="empty-cats" class="btn btn-primary" type="button">Show every type</button>
      <button id="empty-all" class="btn" type="button">Show all {club_count} clubs</button>
    </div>
  </section>

  <footer>
    <p class="tagline">{esc(SITE_TAGLINE)}</p>
    <p>{esc(SITE_ABOUT)}</p>
    <p class="footer-links"><a href="feed.xml">RSS feed</a><a href="{esc(KOFI_URL)}" rel="noopener" target="_blank">Support on Ko-fi</a></p>
    <p>Updated <time id="built-time" datetime="{datetime.now(timezone.utc).isoformat()}">&nbsp;</time></p>
  </footer>
</div>

<dialog id="picker" aria-labelledby="picker-title">
  <div class="sheet-head">
    <button id="picker-clear" class="text-btn" type="button">Clear</button>
    <h2 id="picker-title">Your clubs</h2>
    <button id="picker-done" class="text-btn" type="button">Done</button>
  </div>
  <div class="sheet-search">{SEARCH_SVG}<input id="club-search" type="search" placeholder="Search {club_count} clubs" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="done" aria-label="Search clubs"></div>
  <div class="sheet-body">
{picker_html}
    <p id="search-empty" hidden>No clubs match that.</p>
  </div>
</dialog>

<script>{PAGE_JS}</script>
</body>
</html>"""


def rfc822(iso_str):
    """RSS 2.0 requires RFC-822 dates in <pubDate>, NOT ISO 8601. Emitting
    ISO here made feed readers show wrong dates or silently drop items --
    a real spec violation, not a cosmetic one. Falls back to empty rather
    than emitting a malformed date if parsing fails."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return format_datetime(dt)
    except Exception:
        return ""


def build_feed_xml(articles, names=None):
    # names: slug -> proper club name. The old .title() fallback turned
    # "afc-wimbledon" into "Afc Wimbledon".
    names = names or {}
    articles_sorted = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)[:100]
    items = []
    for a in articles_sorted:
        clubs = ", ".join(names.get(c, c.replace("-", " ").title()) for c in a.get("clubs", []))
        items.append(f"""  <item>
    <title>{esc(a.get('title',''))}</title>
    <link>{esc(a.get('url',''))}</link>
    <guid isPermaLink="true">{esc(a.get('url',''))}</guid>
    <pubDate>{esc(rfc822(a.get('published','')))}</pubDate>
    {f'<category>{esc(clubs)}</category>' if clubs else ''}
    <description>{esc(a.get('excerpt','') or a.get('title',''))}</description>
  </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{esc(SITE_TITLE)}</title>
  <link>{esc(SITE_URL)}</link>
  <atom:link href="{esc(SITE_URL)}/feed.xml" rel="self" type="application/rss+xml"/>
  <description>{esc(SITE_TAGLINE)} {esc(SITE_ABOUT)}</description>
  <language>en-gb</language>
  <lastBuildDate>{esc(format_datetime(datetime.now(timezone.utc)))}</lastBuildDate>
{chr(10).join(items)}
</channel>
</rss>"""


def build_sitemap():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{esc(SITE_URL)}/</loc></url>
</urlset>"""


def build_version(articles):
    key = "|".join(sorted(f"{a.get('url','')}{a.get('published','')}" for a in articles))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return {"version": digest, "built": datetime.now(timezone.utc).isoformat(), "count": len(articles)}


def main():
    articles, clubs, standings = load()
    SITE_DIR.mkdir(exist_ok=True)

    (SITE_DIR / "index.html").write_text(build_html(articles, clubs, standings), encoding="utf-8")
    (SITE_DIR / "feed.xml").write_text(
        build_feed_xml(articles, {c["slug"]: c["name"] for c in clubs}), encoding="utf-8")
    (SITE_DIR / "sitemap.xml").write_text(build_sitemap(), encoding="utf-8")
    (SITE_DIR / "version.json").write_text(json.dumps(build_version(articles)), encoding="utf-8")
    (SITE_DIR / "favicon.svg").write_text(build_favicon_svg(), encoding="utf-8")
    (SITE_DIR / "manifest.webmanifest").write_text(json.dumps(build_manifest(), indent=2), encoding="utf-8")
    write_png_icon(SITE_DIR / "icon-192.png", 192)
    write_png_icon(SITE_DIR / "icon-512.png", 512)

    print(f"built site: {len(articles)} articles, {len(clubs)} clubs")


if __name__ == "__main__":
    main()
