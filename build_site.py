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
from pathlib import Path

HERE = Path(__file__).parent
ARTICLES = HERE / "articles.json"
CLUBS = HERE / "clubs.json"
SITE_DIR = HERE / "site"
SITE_URL = "https://example.invalid"  # replace once the domain is live
SITE_TITLE = "EFL Feed"
SITE_TAGLINE = "Independent and unofficial. Not affiliated with the EFL."
KOFI_URL = "https://ko-fi.com/footballnewsfeed"

DIVISION_ORDER = ["championship", "league-one", "league-two"]
DIVISION_LABEL = {"championship": "Championship", "league-one": "League One", "league-two": "League Two"}


def load():
    articles = json.loads(ARTICLES.read_text(encoding="utf-8")) if ARTICLES.exists() else []
    clubs = json.loads(CLUBS.read_text(encoding="utf-8"))["clubs"]
    return articles, clubs


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

def article_card(a):
    clubs_attr = " ".join(a.get("clubs", []))
    club_label = ", ".join(c.replace("-", " ").title() for c in a.get("clubs", [])) or "League"
    return f"""<article class="card" data-clubs="{esc(clubs_attr)}" data-division="{esc(a.get('division',''))}">
  <div class="card-meta">{esc(club_label)} &middot; {esc(a.get('source',''))} &middot; <time class="ts" data-published="{esc(a.get('published',''))}">&nbsp;</time></div>
  <h3><a href="{esc(a.get('url',''))}" rel="noopener" target="_blank">{esc(a.get('title',''))}</a></h3>
  {f'<p>{esc(a.get("excerpt",""))}</p>' if a.get("excerpt") else ""}
</article>"""


def club_pill(c):
    return f'<button class="pill" type="button" aria-pressed="false" data-slug="{esc(c["slug"])}">{esc(c["name"])}</button>'


def build_favicon_svg():
    # Inline lettermark: rounded square, accent fill, bold initials. No
    # bitmap asset to manage or fail to upload.
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#1a7f4b"/>
  <text x="32" y="42" font-family="Archivo, sans-serif" font-weight="800"
        font-size="26" fill="#ffffff" text-anchor="middle">EF</text>
</svg>"""


def build_html(articles, clubs):
    today = datetime.now(timezone.utc).date()
    articles_sorted = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)
    day_groups = group_by_day(articles_sorted, today)

    feed_sections = []
    for label, group in day_groups:
        cards = "\n".join(article_card(a) for a in group)
        feed_sections.append(f'<section class="day-group"><h2 class="day-heading">{esc(label)}</h2>{cards}</section>')
    feed_html = "\n".join(feed_sections)

    clubs_by_div = {d: [c for c in clubs if c["division"] == d] for d in DIVISION_ORDER}
    picker_sections = []
    for d in DIVISION_ORDER:
        pills = "\n".join(club_pill(c) for c in clubs_by_div[d])
        picker_sections.append(f'<div class="division" data-division="{esc(d)}"><h4>{DIVISION_LABEL[d]}</h4><div class="pills">{pills}</div></div>')
    picker_html = "\n".join(picker_sections)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(SITE_TITLE)}</title>
<meta name="description" content="{esc(SITE_TAGLINE)} News from all 72 English Football League clubs.">
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<link rel="alternate" type="application/rss+xml" href="feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@700;800&family=Source+Sans+3:wght@400;600&family=Space+Grotesk:wght@400;500;600&display=swap" rel="stylesheet">
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
    font-size: 1.9rem; letter-spacing: -0.01em;
  }}
  .tagline {{ font-family: "Space Grotesk", monospace; color: var(--muted); font-size: 0.8rem; }}
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
    padding: 1rem; border-radius: 0 0 8px 8px; margin-bottom: 1.5rem;
  }}
  #picker-clear {{
    font-family: "Space Grotesk", monospace; font-size: 0.75rem; color: var(--muted);
    background: none; border: none; cursor: pointer; text-decoration: underline;
    padding: 0; margin-bottom: 0.75rem;
  }}
  .division {{ margin-bottom: 0.9rem; }}
  .division:last-child {{ margin-bottom: 0; }}
  .division h4 {{
    margin: 0 0 0.5rem; font-family: "Space Grotesk", monospace; font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted);
  }}
  .division[data-division="championship"] h4 {{ color: var(--championship); }}
  .division[data-division="league-one"] h4 {{ color: var(--league-one); }}
  .division[data-division="league-two"] h4 {{ color: var(--league-two); }}
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
  .day-group {{ display: flex; flex-direction: column; gap: 0.8rem; }}
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

<button id="picker-toggle" aria-expanded="false" type="button">
  <span id="picker-toggle-label">Choose your clubs</span>
  <span class="chev">&#9662;</span>
</button>
<div id="picker" hidden>
  <button id="picker-clear" type="button">Clear selection</button>
  {picker_html}
</div>

<main id="feed">
{feed_html}
</main>

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
  var feed = document.getElementById("feed");
  var pills = Array.prototype.slice.call(document.querySelectorAll(".pill"));

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
    var cards = feed.querySelectorAll(".card");
    cards.forEach(function(c) {{
      if (sel.length === 0) {{ c.hidden = false; return; }}
      var clubs = (c.dataset.clubs || "").split(" ");
      c.hidden = !clubs.some(function(s) {{ return sel.indexOf(s) !== -1; }});
    }});
    // Hide a day heading if every card under it is now hidden.
    document.querySelectorAll(".day-group").forEach(function(g) {{
      var anyVisible = Array.prototype.slice.call(g.querySelectorAll(".card")).some(function(c) {{ return !c.hidden; }});
      g.hidden = !anyVisible;
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
  pickerToggle.addEventListener("click", function() {{
    var open = picker.hidden;
    picker.hidden = !open;
    pickerToggle.setAttribute("aria-expanded", open ? "true" : "false");
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

  function checkVersion() {{
    fetch(VERSION_URL, {{ cache: "no-store" }})
      .then(function(r) {{ return r.json(); }})
      .then(function(v) {{
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


def build_feed_xml(articles):
    articles_sorted = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)[:100]
    items = []
    for a in articles_sorted:
        items.append(f"""  <item>
    <title>{esc(a.get('title',''))}</title>
    <link>{esc(a.get('url',''))}</link>
    <guid>{esc(a.get('url',''))}</guid>
    <pubDate>{esc(a.get('published',''))}</pubDate>
    <description>{esc(a.get('excerpt',''))}</description>
  </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{esc(SITE_TITLE)}</title>
  <link>{esc(SITE_URL)}</link>
  <description>{esc(SITE_TAGLINE)} Support: {esc(KOFI_URL)}</description>
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
    articles, clubs = load()
    SITE_DIR.mkdir(exist_ok=True)

    (SITE_DIR / "index.html").write_text(build_html(articles, clubs), encoding="utf-8")
    (SITE_DIR / "feed.xml").write_text(build_feed_xml(articles), encoding="utf-8")
    (SITE_DIR / "sitemap.xml").write_text(build_sitemap(), encoding="utf-8")
    (SITE_DIR / "version.json").write_text(json.dumps(build_version(articles)), encoding="utf-8")
    (SITE_DIR / "favicon.svg").write_text(build_favicon_svg(), encoding="utf-8")

    print(f"built site: {len(articles)} articles, {len(clubs)} clubs")


if __name__ == "__main__":
    main()
