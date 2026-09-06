"""Build the static EFL Feed site from articles.json.

Emits index.html, feed.xml, sitemap.xml, version.json, favicon.svg into site/.

Design system carried over from The Wednesday Times (proven, documented in
LEARNINGS.md / the KT doc), adapted for EFL Feed:
  - Fonts: Archivo (display), Source Sans 3 (body), Space Grotesk (UI chrome)
  - Colour: CSS custom properties, swapped via [data-theme]. EFL Feed uses a
    green/pitch palette -- deliberately different from the Wednesday Times
    blue, since this isn't a single-club reskin.
  - Day-bucket article grouping (Today / Yesterday / weekday date), grouped
    via date().toordinal() -- NOT timestamp() // 86400, which collides on
    negative floor division at midnight UTC boundaries. See LEARNINGS.md #.
  - Club picker follows the same collapsible/chip/multi-select pattern as
    the Wednesday Times' source panel, adapted for clubs-by-division instead
    of publishers.
  - Update banner lives in normal document flow (never position:fixed), and
    fades via a dedicated max-height/opacity class -- not the shared
    `hidden` attribute, which can't transition, and not a shared `.hidden`
    utility class, which caused a real specificity bug last time.
  - Favicon is an inline SVG lettermark, not a bitmap asset.
"""

import json
import hashlib
from datetime import datetime, date, timezone
from email.utils import format_datetime
from pathlib import Path

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
SITE_ABOUT = "Made by a fan, for fans. Independent and unofficial -- not affiliated with the EFL or any club."
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

def day_label(d, today):
    # Both are date objects -- .toordinal() is exact and side-steps the
    # negative-floor-division bug that hit timestamp()//86400 grouping.
    diff = d.toordinal() - today.toordinal()
    if diff == 0:
        return "Today"
    if diff == -1:
        return "Yesterday"
    return d.strftime("%A %d %B")


def cluster_by_clubs(articles):
    """Group same-club-set stories together within a day. Real production
    output showed the same fixture covered 5-10 times (preview, lineups,
    live score, highlights, report) each rendering as its own card -- that's
    not noise, it's genuine coverage, but it overwhelms the page. Clustering
    by the exact club-set means both "same match covered repeatedly" and
    "same club covered by several different stories" collapse into one
    primary card plus an expandable list, in first-seen (i.e. newest-first,
    since input is already sorted) order. Nothing is dropped -- everything
    stays reachable via the <details> disclosure."""
    clusters = []
    index_by_key = {}
    for a in articles:
        key = tuple(sorted(a.get("clubs", [])))
        if key not in index_by_key:
            index_by_key[key] = len(clusters)
            clusters.append({"primary": a, "more": []})
        else:
            clusters[index_by_key[key]]["more"].append(a)
    return clusters


def group_by_day(articles_sorted, today):
    """articles_sorted must already be newest-first. Returns an ordered
    list of (label, [articles]) preserving that order."""
    groups = []
    current_key = None
    for a in articles_sorted:
        try:
            d = datetime.fromisoformat(a["published"]).date()
        except Exception:
            d = today
        if d != current_key:
            groups.append((day_label(d, today), []))
            current_key = d
        groups[-1][1].append(a)
    return groups


# -------------------------------------------------------------------- pieces

def card_body(a, compact=False):
    club_label = ", ".join(c.replace("-", " ").title() for c in a.get("clubs", [])) or "League"
    if compact:
        return f"""<div class="more-item">
  <div class="card-meta">{esc(club_label)} &middot; {esc(a.get('source',''))} &middot; <time class="ts" data-published="{esc(a.get('published',''))}">&nbsp;</time></div>
  <a class="more-link" href="{esc(a.get('url',''))}" rel="noopener" target="_blank">{esc(a.get('title',''))}</a>
</div>"""
    return f"""<div class="card-meta">{esc(club_label)} &middot; {esc(a.get('source',''))} &middot; <time class="ts" data-published="{esc(a.get('published',''))}">&nbsp;</time></div>
  <h3><a href="{esc(a.get('url',''))}" rel="noopener" target="_blank">{esc(a.get('title',''))}</a></h3>
  {f'<p>{esc(a.get("excerpt",""))}</p>' if a.get("excerpt") else ""}"""


def cluster_card(cluster):
    primary = cluster["primary"]
    more = cluster["more"]
    clubs_attr = " ".join(primary.get("clubs", []))
    division = primary.get("division", "")
    more_html = ""
    if more:
        items = "\n".join(card_body(a, compact=True) for a in more)
        noun = "story" if len(more) == 1 else "stories"
        more_html = f"""<details class="more-stories">
  <summary>+{len(more)} more {noun}</summary>
  <div class="more-list">{items}</div>
</details>"""
    return f"""<div class="cluster" data-clubs="{esc(clubs_attr)}" data-division="{esc(division)}">
  <article class="card" data-division="{esc(division)}">
    {card_body(primary)}
  </article>
  {more_html}
</div>"""


def club_pill(c):
    return f'<button class="pill" type="button" aria-pressed="false" data-slug="{esc(c["slug"])}">{esc(c["name"])}</button>'


ORDINALS = {1: "st", 2: "nd", 3: "rd"}


def ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ORDINALS.get(n % 10, 'th')}"


def your_clubs_card(club, standings_for_division):
    """One row in the 'Your clubs' strip: last result + league position.
    standings_for_division is the {'table':[...], 'last_result':{...}}
    dict for this club's division, or None if standings.json has nothing
    for that division yet (e.g. first run before fetch_standings.py has
    ever succeeded) -- degrades to just showing the club name, not a
    broken row."""
    slug = club["slug"]
    if not standings_for_division:
        return None
    last = standings_for_division.get("last_result", {}).get(slug)
    table_row = next((t for t in standings_for_division.get("table", []) if t["slug"] == slug), None)
    if not last and not table_row:
        return None
    result_html = ""
    if last:
        opp_name = last["opponent_name"]
        venue = "vs" if last["home_away"] == "H" else "@"
        result_html = f'<div class="yc-result">{esc(last["result"])} {last["gf"]}&ndash;{last["ga"]} {venue} {esc(opp_name)}</div>'
    position_html = ""
    if table_row:
        position_html = f"""<div class="yc-position">
      <div class="yc-pos-num">{ordinal(table_row['position'])}</div>
      <div class="yc-pts">{table_row['points']} pts &middot; P{table_row['played']}</div>
    </div>"""
    return f"""<div class="yc-row" data-slug="{esc(slug)}" data-division="{esc(club['division'])}">
  <div>
    <div class="yc-name">{esc(club['name'])}</div>
    {result_html}
  </div>
  {position_html}
</div>"""


def build_favicon_svg():
    # Teletext-inspired lettermark: black background, bright-green outline
    # frame, white blocky monospace text. Deliberately its own colour
    # combination (not the classic multicolour teletext page palette) and
    # no page number / service name -- evokes the era's chunky low-res
    # text-service look without referencing any specific real service.
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" fill="#0a0a0a"/>
  <rect x="2.5" y="2.5" width="59" height="59" fill="none" stroke="#4ade80" stroke-width="3"/>
  <text x="32" y="41" font-family="'Courier New', monospace" font-weight="700"
        font-size="22" letter-spacing="1" fill="#ffffff" text-anchor="middle">fln</text>
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
        "background_color": "#0d100e",
        "theme_color": "#1a7f4b",
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

    Matches favicon.svg's palette (black background, green frame) but
    can't reproduce the actual "fln" lettering here -- there's no font
    renderer available without adding a dependency, and hand-mapping
    three letterforms pixel-by-pixel at icon scale would likely look
    worse than a clean geometric mark. Uses the same goalposts motif as
    before, recoloured, rather than a blurry attempt at real letters."""
    import zlib
    import struct

    bg = (10, 10, 10)
    frame = (74, 222, 128)
    fg = (255, 255, 255)
    border = max(2, size // 32)
    inset = size // 4
    bar = max(1, size // 12)

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter type 0 for each scanline
        for x in range(size):
            on_frame = (x < border or x >= size - border or y < border or y >= size - border)
            in_left = inset <= x < inset + bar and y >= inset
            in_right = size - inset - bar <= x < size - inset and y >= inset
            in_top = inset <= x < size - inset and inset <= y < inset + bar
            if on_frame:
                pixel = frame
            elif in_left or in_right or in_top:
                pixel = fg
            else:
                pixel = bg
            rows.extend(pixel)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def build_html(articles, clubs, standings):
    today = datetime.now(timezone.utc).date()
    articles_sorted = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)
    day_groups = group_by_day(articles_sorted, today)

    feed_sections = []
    for label, group in day_groups:
        clusters = cluster_by_clubs(group)
        cards = "\n".join(cluster_card(c) for c in clusters)
        feed_sections.append(f'<section class="day-group"><h2 class="day-heading">{esc(label)}</h2>{cards}</section>')
    feed_html = "\n".join(feed_sections)

    your_clubs_rows = []
    for c in clubs:
        row = your_clubs_card(c, standings.get(c["division"]))
        if row:
            your_clubs_rows.append((c["slug"], row))
    your_clubs_html = "\n".join(html for _, html in your_clubs_rows)

    clubs_by_div = {d: [c for c in clubs if c["division"] == d] for d in DIVISION_ORDER}
    picker_sections = []
    for d in DIVISION_ORDER:
        pills = "\n".join(club_pill(c) for c in clubs_by_div[d])
        picker_sections.append(f'''<div class="division" data-division="{esc(d)}">
  <h4>{DIVISION_LABEL[d]}</h4>
  <div class="pills">{pills}</div>
</div>''')
    picker_html = "\n".join(picker_sections)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(SITE_TITLE)} &mdash; {esc(SITE_TAGLINE)}</title>
<meta name="description" content="News from all 72 English Football League clubs, in one feed. {esc(SITE_ABOUT)}">
<link rel="canonical" href="{esc(SITE_URL)}/">
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<link rel="apple-touch-icon" href="icon-192.png">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#1a7f4b">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE_TITLE)}" href="feed.xml">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{esc(SITE_TITLE)}">
<meta property="og:title" content="{esc(SITE_TITLE)} &mdash; {esc(SITE_TAGLINE)}">
<meta property="og:description" content="Championship, League One and League Two news in one place. Pick your clubs and it remembers.">
<meta property="og:url" content="{esc(SITE_URL)}/">
<meta name="twitter:card" content="summary">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@700;800&family=Source+Sans+3:wght@400;600&family=Space+Grotesk:wght@400;500;600&display=swap" rel="stylesheet">
<script>
  // Applied BEFORE first paint, deliberately. The main script runs at the
  // end of <body>, so a dark-mode visitor got a white flash on every single
  // load while waiting for it -- looks broken and cheap. This tiny blocking
  // script sets the attribute before anything renders. Kept duplicated
  // rather than shared because it MUST run here, inline, in <head>.
  (function() {{
    try {{
      var saved = localStorage.getItem("eflfeed.theme");
      var dark = saved ? saved === "dark"
        : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
      if (dark) document.documentElement.setAttribute("data-theme", "dark");
    }} catch (e) {{}}
  }})();
</script>
<style>
  :root {{
    --accent: #1a7f4b;
    --accent-soft: #2fae6b;
    --ink: #14181c;
    --bg: #f7f8f5;
    --card: #ffffff;
    --line: #dde2dc;
    --muted: #5b6660;
    --head-bg: #101613;
    --head-fg: #f2f5f0;
    --badge-fg: #ffffff;
    --championship: #b8860b;
    --league-one: #2563eb;
    --league-two: #c2377f;
  }}
  [data-theme="dark"] {{
    --accent: #4ade80;
    --accent-soft: #22c55e;
    --ink: #e8ece7;
    --bg: #0d100e;
    --card: #161b18;
    --line: #2a322c;
    --muted: #8b968f;
    --head-bg: #0d100e;
    --head-fg: #e8ece7;
    --badge-fg: #052e13;
    --championship: #e0b23a;
    --league-one: #60a5fa;
    --league-two: #f472b6;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Source Sans 3", -apple-system, "Segoe UI", sans-serif;
    max-width: 700px; margin: 0 auto; padding: 1.25rem 1rem 4rem;
    background: var(--bg); color: var(--ink); line-height: 1.45;
    transition: background 0.15s, color 0.15s;
  }}
  header {{ position: relative; text-align: center; margin-bottom: 1.25rem; padding-top: 0.25rem; }}
  header h1 {{
    margin: 0 0 0.3rem; font-family: "Archivo", sans-serif; font-weight: 800;
    font-size: 1.9rem; letter-spacing: -0.01em; padding: 0 2.6rem;
  }}
  .tagline {{ font-family: "Space Grotesk", monospace; color: var(--muted); font-size: 0.8rem; padding: 0 2.6rem; }}
  .kofi-link {{
    display: inline-block; margin-top: 0.6rem; font-family: "Space Grotesk", monospace;
    font-size: 0.78rem; color: var(--muted); text-decoration: none; border: 1px solid var(--line);
    border-radius: 999px; padding: 0.3rem 0.8rem;
  }}
  .kofi-link:hover {{ color: var(--ink); border-color: var(--accent); }}

  #theme-toggle {{
    position: absolute; top: 0.1rem; right: 0; background: transparent; border: 1px solid var(--line);
    border-radius: 999px; width: 2.1rem; height: 2.1rem; cursor: pointer; font-size: 1rem;
    color: var(--ink); display: flex; align-items: center; justify-content: center;
  }}
  #theme-toggle:hover {{ border-color: var(--accent); }}

  @media (max-width: 380px) {{
    header h1 {{ font-size: 1.5rem; padding: 0 2.4rem; }}
  }}

  #update-banner {{
    max-height: 0; opacity: 0; overflow: hidden; margin-bottom: 0;
    background: var(--accent); color: var(--badge-fg); border-radius: 8px;
    text-align: center; font-family: "Space Grotesk", monospace; font-size: 0.85rem;
    transition: max-height 0.25s ease, opacity 0.2s ease, margin-bottom 0.25s ease;
  }}
  #update-banner.show {{ max-height: 4rem; opacity: 1; margin-bottom: 1rem; padding: 0.6rem 1rem; }}
  #update-banner button {{
    background: var(--badge-fg); color: var(--accent); border: none; border-radius: 6px;
    padding: 0.25rem 0.7rem; margin-left: 0.6rem; font-weight: 600; cursor: pointer;
    font-family: "Space Grotesk", monospace;
  }}

  #your-clubs:not([hidden]) {{ margin-bottom: 1.5rem; }}
  .yc-heading {{
    font-family: "Space Grotesk", monospace; font-size: 0.68rem; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--muted); margin: 0 0 0.5rem;
  }}
  .yc-row {{
    background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--line);
    border-radius: 8px; padding: 0.7rem 0.9rem; margin-bottom: 0.5rem;
    display: flex; justify-content: space-between; align-items: center;
  }}
  .yc-row[data-division="championship"] {{ border-left-color: var(--championship); }}
  .yc-row[data-division="league-one"] {{ border-left-color: var(--league-one); }}
  .yc-row[data-division="league-two"] {{ border-left-color: var(--league-two); }}
  .yc-name {{ font-weight: 600; font-size: 0.92rem; }}
  .yc-result {{ font-family: "Space Grotesk", monospace; font-size: 0.72rem; color: var(--muted); margin-top: 0.15rem; }}
  .yc-position {{ text-align: right; }}
  .yc-pos-num {{ font-family: "Space Grotesk", monospace; font-weight: 700; font-size: 1rem; color: var(--accent); }}
  .yc-pts {{ font-family: "Space Grotesk", monospace; font-size: 0.62rem; color: var(--muted); }}

  .picker-wrap {{ margin-bottom: 1.75rem; }}

  #picker-toggle {{
    background: var(--card); color: var(--ink); border: 1px solid var(--line);
    border-radius: 8px; padding: 0.55rem 1rem; font-family: "Space Grotesk", monospace;
    font-size: 0.85rem; cursor: pointer; width: 100%; text-align: left;
    display: flex; justify-content: space-between; align-items: center;
  }}
  #picker-toggle:hover {{ border-color: var(--accent); }}
  #picker-toggle .chev {{ opacity: 0.6; transition: transform 0.15s; }}
  #picker-toggle[aria-expanded="true"] .chev {{ transform: rotate(180deg); }}

  #picker {{
    background: var(--card); border: 1px solid var(--line); border-top: none;
    padding: 1rem; border-radius: 0 0 8px 8px;
  }}
  #club-search {{
    width: 100%; padding: 0.5rem 0.75rem; margin-bottom: 0.6rem;
    font-family: "Space Grotesk", monospace; font-size: 0.85rem;
    background: var(--bg); color: var(--ink);
    border: 1px solid var(--line); border-radius: 8px;
  }}
  #club-search:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; border-color: var(--accent); }}
  #search-empty {{
    font-family: "Space Grotesk", monospace; font-size: 0.8rem;
    color: var(--muted); margin: 0.5rem 0 0;
  }}
  .picker-actions {{
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem;
  }}

  #empty-state:not([hidden]) {{
    display: block; text-align: center; padding: 2.5rem 1rem;
    color: var(--muted); font-family: "Space Grotesk", monospace; font-size: 0.9rem;
  }}
  #empty-state p {{ margin: 0 0 0.9rem; }}
  #empty-clear {{
    font-family: "Space Grotesk", monospace; font-size: 0.82rem; color: var(--badge-fg);
    background: var(--accent); border: none; border-radius: 999px;
    padding: 0.45rem 1.1rem; font-weight: 600; cursor: pointer;
  }}
  #empty-clear:hover {{ background: var(--accent-soft); }}

  footer {{
    margin-top: 2.5rem; padding-top: 1.2rem; border-top: 1px solid var(--line);
    font-family: "Space Grotesk", monospace; font-size: 0.75rem;
    color: var(--muted); text-align: center; line-height: 1.6;
  }}
  footer p {{ margin: 0 0 0.4rem; }}
  footer a {{ color: var(--muted); text-decoration: underline; }}
  footer a:hover {{ color: var(--accent); }}
  footer .built {{ opacity: 0.7; }}
  #picker-clear {{
    font-family: "Space Grotesk", monospace; font-size: 0.75rem; color: var(--muted);
    background: none; border: none; cursor: pointer; text-decoration: underline;
    padding: 0;
  }}
  #picker-done {{
    font-family: "Space Grotesk", monospace; font-size: 0.78rem; color: var(--badge-fg);
    background: var(--accent); border: none; border-radius: 999px; cursor: pointer;
    padding: 0.3rem 0.9rem; font-weight: 600;
  }}
  #picker-done:hover {{ background: var(--accent-soft); }}
  .division {{ margin-bottom: 0.9rem; }}
  .division:last-child {{ margin-bottom: 0; }}
  .division h4 {{
    margin: 0 0 0.5rem; font-family: "Space Grotesk", monospace; font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted);
  }}
  .division[data-division="championship"] h4 {{ color: var(--championship); }}
  .division[data-division="league-one"] h4 {{ color: var(--league-one); }}
  .division[data-division="league-two"] h4 {{ color: var(--league-two); }}

  .league-pills {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 1rem; }}
  .league-pill {{ font-weight: 600; }}
  .league-pill[data-division="championship"] {{ border-color: var(--championship); color: var(--championship); }}
  .league-pill[data-division="league-one"] {{ border-color: var(--league-one); color: var(--league-one); }}
  .league-pill[data-division="league-two"] {{ border-color: var(--league-two); color: var(--league-two); }}
  /* Pressed-state text colour is checked per division per theme (see
     LEARNINGS.md badge-fg note) -- light-mode league-one/two are darker,
     saturated colours that need white text; championship and everything
     in dark mode (brighter pastels) need black. Not a single value that
     works everywhere. */
  .league-pill[aria-pressed="true"] {{ color: #ffffff; }}
  .league-pill[data-division="championship"][aria-pressed="true"] {{ color: #000000; background: var(--championship); }}
  .league-pill[data-division="league-one"][aria-pressed="true"] {{ background: var(--league-one); }}
  .league-pill[data-division="league-two"][aria-pressed="true"] {{ background: var(--league-two); }}
  [data-theme="dark"] .league-pill[aria-pressed="true"] {{ color: #000000; }}
  .pills {{ display: flex; flex-wrap: wrap; gap: 0.4rem; }}
  .pill {{
    font-family: "Space Grotesk", monospace; border: 1px solid var(--line); border-radius: 999px;
    padding: 0.3rem 0.75rem; background: transparent; color: var(--ink); font-size: 0.8rem;
    cursor: pointer; transition: background 0.1s, border-color 0.1s;
  }}
  .pill:hover {{ border-color: var(--accent); }}
  .pill[aria-pressed="true"] {{ background: var(--accent); border-color: var(--accent); color: var(--badge-fg); font-weight: 600; }}

  .day-heading {{
    font-family: "Space Grotesk", monospace; font-size: 0.75rem; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--muted); margin: 1.3rem 0 0.6rem;
    border-bottom: 1px solid var(--line); padding-bottom: 0.3rem;
  }}
  .day-group:first-child .day-heading {{ margin-top: 0; }}

  #feed {{ display: flex; flex-direction: column; }}
  /* :not([hidden]) matters here: JS toggles the `hidden` attribute on
     .day-group and .cluster to filter content, but the browser's default
     [hidden]{{display:none}} rule is a UA-stylesheet rule -- any author
     rule that sets `display` on the same element overrides it regardless
     of selector specificity. An unscoped `.day-group{{display:flex}}`
     silently defeats `hidden` entirely: the attribute gets set, nothing
     visually hides. Scoping the flex display to :not([hidden]) means the
     layout rule only applies while visible, and hidden elements fall
     through to the real UA default. */
  .day-group:not([hidden]) {{ display: flex; flex-direction: column; gap: 0.8rem; }}
  .card {{
    background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--line);
    border-radius: 8px; padding: 0.9rem 1rem;
  }}
  .card[data-division="championship"] {{ border-left-color: var(--championship); }}
  .card[data-division="league-one"] {{ border-left-color: var(--league-one); }}
  .card[data-division="league-two"] {{ border-left-color: var(--league-two); }}
  .card-meta {{
    font-family: "Space Grotesk", monospace; font-size: 0.72rem; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 0.35rem;
  }}
  .card h3 {{ margin: 0; font-size: 1.02rem; line-height: 1.35; font-weight: 600; }}
  .card h3 a {{ color: var(--ink); text-decoration: none; }}
  .card h3 a:hover {{ color: var(--accent); text-decoration: underline; }}
  .card p {{ font-size: 0.88rem; color: var(--muted); margin: 0.4rem 0 0; }}

  .cluster:not([hidden]) {{ display: flex; flex-direction: column; }}
  .more-stories {{ margin-top: 0.4rem; }}
  .more-stories summary {{
    font-family: "Space Grotesk", monospace; font-size: 0.78rem; color: var(--muted);
    cursor: pointer; padding: 0.3rem 0.2rem; list-style: none;
  }}
  .more-stories summary::-webkit-details-marker {{ display: none; }}
  .more-stories summary:before {{ content: "\\25B8"; display: inline-block; margin-right: 0.4rem; transition: transform 0.15s; }}
  .more-stories[open] summary:before {{ transform: rotate(90deg); }}
  .more-stories summary:hover {{ color: var(--accent); }}
  .more-list {{
    display: flex; flex-direction: column; gap: 0.6rem; margin-top: 0.4rem;
    padding-left: 0.9rem; border-left: 2px solid var(--line);
  }}
  .more-item .card-meta {{ margin-bottom: 0.15rem; }}
  .more-link {{ font-size: 0.88rem; color: var(--ink); text-decoration: none; }}
  .more-link:hover {{ color: var(--accent); text-decoration: underline; }}
</style>
</head>
<body>
<div id="update-banner">New stories are available.<button id="update-btn">Refresh</button></div>
<header>
  <button id="theme-toggle" aria-label="Toggle dark mode" type="button">&#9788;</button>
  <h1>{esc(SITE_TITLE)}</h1>
  <div class="tagline">{esc(SITE_TAGLINE)}</div>
  <a class="kofi-link" href="{esc(KOFI_URL)}" rel="noopener" target="_blank">&#9749; Support this site on Ko-fi</a>
</header>

<div id="your-clubs" hidden>
  <h2 class="yc-heading">Your clubs</h2>
  {your_clubs_html}
</div>

<div class="picker-wrap">
  <button id="picker-toggle" aria-expanded="false" type="button">
    <span id="picker-toggle-label">Choose your clubs</span>
    <span class="chev">&#9662;</span>
  </button>
  <div id="picker" hidden>
    <input id="club-search" type="search" placeholder="Search clubs&hellip;" autocomplete="off" aria-label="Search clubs">
    <div class="picker-actions">
      <button id="picker-clear" type="button">Clear selection</button>
      <button id="picker-done" type="button">Done</button>
    </div>
    <div class="league-pills">
      <button class="pill league-pill" type="button" aria-pressed="false" data-division="championship">Championship</button>
      <button class="pill league-pill" type="button" aria-pressed="false" data-division="league-one">League One</button>
      <button class="pill league-pill" type="button" aria-pressed="false" data-division="league-two">League Two</button>
    </div>
    {picker_html}
    <p id="search-empty" hidden>No clubs match that.</p>
  </div>
</div>

<main id="feed">
{feed_html}
</main>

<div id="empty-state" hidden>
  <p>No stories yet for the clubs you follow.</p>
  <button id="empty-clear" type="button">Show all clubs</button>
</div>

<footer>
  <p>{esc(SITE_ABOUT)}</p>
  <p><a href="feed.xml">RSS</a> &middot; <a href="{esc(KOFI_URL)}" rel="noopener" target="_blank">Support on Ko-fi</a></p>
  <p class="built">Updated <time id="built-time" datetime="">&nbsp;</time></p>
</footer>

<script>
(function() {{
  // --- theme: default to system preference, remember explicit choice
  var THEME_KEY = "eflfeed.theme";
  var root = document.documentElement;
  var toggle = document.getElementById("theme-toggle");
  function applyTheme(t) {{
    if (t === "dark") {{ root.setAttribute("data-theme", "dark"); toggle.textContent = "\u2600"; }}
    else {{ root.removeAttribute("data-theme"); toggle.textContent = "\u263D"; }}
  }}
  var saved = localStorage.getItem(THEME_KEY);
  var systemDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (systemDark ? "dark" : "light"));
  toggle.addEventListener("click", function() {{
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem(THEME_KEY, next);
  }});

  // --- club picker (multi-select per division, empty = show all)
  var STORAGE_KEY = "eflfeed.clubs";
  var picker = document.getElementById("picker");
  var pickerToggle = document.getElementById("picker-toggle");
  var pickerLabel = document.getElementById("picker-toggle-label");
  var clearBtn = document.getElementById("picker-clear");
  var doneBtn = document.getElementById("picker-done");
  var clubSearch = document.getElementById("club-search");
  var searchEmpty = document.getElementById("search-empty");
  var emptyState = document.getElementById("empty-state");
  var emptyClearBtn = document.getElementById("empty-clear");
  var yourClubs = document.getElementById("your-clubs");
  var feed = document.getElementById("feed");
  // Scoped to exclude .league-pill: those share the .pill class purely
  // for visual styling, but are a different control (toggles a whole
  // division) with their own click handler below. Without this
  // exclusion, clicking a league pill would ALSO fire the club-pill
  // handler and push the literal division name into the selection as
  // if it were a club slug.
  var pills = Array.prototype.slice.call(document.querySelectorAll(".pill:not(.league-pill)"));

  function getSelection() {{
    try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); }}
    catch (e) {{ return []; }}
  }}
  function setSelection(sel) {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(sel)); }}

  function applyFilter() {{
    var sel = getSelection();
    pills.forEach(function(p) {{
      p.setAttribute("aria-pressed", sel.indexOf(p.dataset.slug) !== -1 ? "true" : "false");
    }});
    var cards = feed.querySelectorAll(".cluster");
    cards.forEach(function(c) {{
      if (sel.length === 0) {{ c.hidden = false; return; }}
      var clubs = (c.dataset.clubs || "").split(" ");
      c.hidden = !clubs.some(function(s) {{ return sel.indexOf(s) !== -1; }});
    }});
    // Hide a day heading if every cluster under it is now hidden.
    document.querySelectorAll(".day-group").forEach(function(g) {{
      var anyVisible = Array.prototype.slice.call(g.querySelectorAll(".cluster")).some(function(c) {{ return !c.hidden; }});
      g.hidden = !anyVisible;
    }});
    // Follow a club with no coverage today and every day-group hides,
    // leaving a totally blank page with no explanation -- looks broken.
    // Show an explicit empty state with a way back out instead.
    var anyClusterVisible = Array.prototype.slice.call(feed.querySelectorAll(".cluster"))
      .some(function(c) {{ return !c.hidden; }});
    emptyState.hidden = anyClusterVisible;
    // "Your clubs" strip: only ever shown for clubs actually followed --
    // an empty selection means nothing to show, not "show everyone".
    var ycRows = Array.prototype.slice.call(document.querySelectorAll(".yc-row"));
    var anyYcVisible = false;
    ycRows.forEach(function(row) {{
      var show = sel.indexOf(row.dataset.slug) !== -1;
      row.hidden = !show;
      if (show) anyYcVisible = true;
    }});
    yourClubs.hidden = !anyYcVisible;

    // League pill reflects whether every club in that division is
    // currently followed -- lets it double as both an action and a
    // status indicator, rather than a static label.
    document.querySelectorAll(".league-pill").forEach(function(lp) {{
      var divisionPills = Array.prototype.slice.call(
        document.querySelectorAll('.division[data-division="' + lp.dataset.division + '"] .pill')
      );
      var allSelected = divisionPills.length > 0 && divisionPills.every(function(p) {{
        return sel.indexOf(p.dataset.slug) !== -1;
      }});
      lp.setAttribute("aria-pressed", allSelected ? "true" : "false");
    }});

    pickerLabel.textContent = sel.length ? "Your clubs (" + sel.length + ")" : "Choose your clubs";
  }}

  pills.forEach(function(p) {{
    p.addEventListener("click", function() {{
      var sel = getSelection();
      var i = sel.indexOf(p.dataset.slug);
      if (i === -1) sel.push(p.dataset.slug); else sel.splice(i, 1);
      setSelection(sel);
      applyFilter();
    }});
  }});
  clearBtn.addEventListener("click", function() {{ setSelection([]); applyFilter(); }});
  emptyClearBtn.addEventListener("click", function() {{ setSelection([]); applyFilter(); }});

  document.querySelectorAll(".league-pill").forEach(function(lp) {{
    lp.addEventListener("click", function() {{
      var divisionSlugs = Array.prototype.slice.call(
        document.querySelectorAll('.division[data-division="' + lp.dataset.division + '"] .pill')
      ).map(function(p) {{ return p.dataset.slug; }});
      var sel = getSelection();
      var allSelected = divisionSlugs.every(function(s) {{ return sel.indexOf(s) !== -1; }});
      if (allSelected) {{
        // Toggle off: remove this division's clubs, leave any others untouched.
        sel = sel.filter(function(s) {{ return divisionSlugs.indexOf(s) === -1; }});
      }} else {{
        // Toggle on: add any not already selected, no duplicates.
        divisionSlugs.forEach(function(s) {{
          if (sel.indexOf(s) === -1) sel.push(s);
        }});
      }}
      setSelection(sel);
      applyFilter();
    }});
  }});

  // Scanning 72 pills to find your club is the main friction in the picker.
  // Filters pills live; hides a whole division heading when nothing in it
  // matches, so the list collapses down instead of leaving empty headings.
  clubSearch.addEventListener("input", function() {{
    var q = clubSearch.value.trim().toLowerCase();
    pills.forEach(function(p) {{
      p.hidden = q !== "" && p.textContent.toLowerCase().indexOf(q) === -1;
    }});
    var anyMatch = false;
    document.querySelectorAll(".division").forEach(function(d) {{
      var visible = Array.prototype.slice.call(d.querySelectorAll(".pill"))
        .some(function(p) {{ return !p.hidden; }});
      d.hidden = !visible;
      if (visible) anyMatch = true;
    }});
    searchEmpty.hidden = anyMatch;
  }});

  function closePicker() {{
    picker.hidden = true;
    pickerToggle.setAttribute("aria-expanded", "false");
  }}
  doneBtn.addEventListener("click", closePicker);
  pickerToggle.addEventListener("click", function() {{
    var open = picker.hidden;
    picker.hidden = !open;
    pickerToggle.setAttribute("aria-expanded", open ? "true" : "false");
  }});
  // Click outside the picker (and outside its own toggle button) closes it.
  // Selecting pills stays multi-select -- this only fires for clicks that
  // land outside the picker entirely, so picking several clubs in a row
  // still works before the picker closes.
  document.addEventListener("click", function(e) {{
    if (picker.hidden) return;
    if (picker.contains(e.target) || pickerToggle.contains(e.target)) return;
    closePicker();
  }});
  applyFilter();

  // --- relative timestamps, recalculated so they don't go stale on a page
  // left open. Computed client-side; server only emits the raw ISO time.
  function relativeTime(iso) {{
    var then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    var mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    var hrs = Math.round(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    var days = Math.round(hrs / 24);
    return days + "d ago";
  }}
  function refreshTimestamps() {{
    document.querySelectorAll(".ts").forEach(function(el) {{
      el.textContent = relativeTime(el.dataset.published);
    }});
  }}
  refreshTimestamps();
  setInterval(refreshTimestamps, 60 * 1000);

  // --- update checking: on load, on tab focus, and periodically.
  // Auto-correct silently on the very first load (session-storage guard
  // against reload loops); after that, a dismissible banner instead of
  // yanking the page mid-read. Banner lives in normal flow and fades via
  // a dedicated class, never `display`/shared `.hidden`.
  var VERSION_URL = "version.json";
  var currentVersion = null;
  var banner = document.getElementById("update-banner");
  var updateBtn = document.getElementById("update-btn");

  var builtTime = document.getElementById("built-time");

  function checkVersion() {{
    fetch(VERSION_URL, {{ cache: "no-store" }})
      .then(function(r) {{ return r.json(); }})
      .then(function(v) {{
        // Trust signal: shows the feed is actually live, not abandoned.
        if (v.built && builtTime) {{
          builtTime.dateTime = v.built;
          builtTime.textContent = relativeTime(v.built);
        }}
        if (currentVersion === null) {{ currentVersion = v.version; return; }}
        if (v.version === currentVersion) return;
        if (!sessionStorage.getItem("eflfeed.autoReloaded")) {{
          sessionStorage.setItem("eflfeed.autoReloaded", "1");
          location.reload();
        }} else {{
          banner.classList.add("show");
        }}
      }})
      .catch(function() {{ /* offline or blocked -- ignore, try again later */ }});
  }}
  updateBtn.addEventListener("click", function() {{ location.reload(); }});
  checkVersion();
  document.addEventListener("visibilitychange", function() {{
    if (document.visibilityState === "visible") checkVersion();
  }});
  setInterval(checkVersion, 5 * 60 * 1000);
}})();
</script>
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


def build_feed_xml(articles):
    articles_sorted = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)[:100]
    items = []
    for a in articles_sorted:
        clubs = ", ".join(c.replace("-", " ").title() for c in a.get("clubs", []))
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
    (SITE_DIR / "feed.xml").write_text(build_feed_xml(articles), encoding="utf-8")
    (SITE_DIR / "sitemap.xml").write_text(build_sitemap(), encoding="utf-8")
    (SITE_DIR / "version.json").write_text(json.dumps(build_version(articles)), encoding="utf-8")
    (SITE_DIR / "favicon.svg").write_text(build_favicon_svg(), encoding="utf-8")
    (SITE_DIR / "manifest.webmanifest").write_text(json.dumps(build_manifest(), indent=2), encoding="utf-8")
    write_png_icon(SITE_DIR / "icon-192.png", 192)
    write_png_icon(SITE_DIR / "icon-512.png", 512)

    print(f"built site: {len(articles)} articles, {len(clubs)} clubs")


if __name__ == "__main__":
    main()
