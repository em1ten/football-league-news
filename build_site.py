"""Build the static EFL Feed site from articles.json.

Emits index.html, feed.xml, sitemap.xml, version.json into site/.
Follow-your-clubs selection is stored client-side (localStorage) -- this
script just needs to emit every article tagged with its clubs/division so
the client can filter without another fetch.
"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
ARTICLES = HERE / "articles.json"
CLUBS = HERE / "clubs.json"
SITE_DIR = HERE / "site"
SITE_URL = "https://example.invalid"  # replace once the domain is live
SITE_TITLE = "EFL Feed"
SITE_TAGLINE = "Independent and unofficial. Not affiliated with the EFL."


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


def article_card(a):
    clubs_attr = " ".join(a.get("clubs", []))
    club_label = ", ".join(c.replace("-", " ").title() for c in a.get("clubs", [])) or "League"
    return f"""<article class="card" data-clubs="{esc(clubs_attr)}" data-division="{esc(a.get('division',''))}">
  <div class="card-meta">{esc(club_label)} &middot; {esc(a.get('source',''))}</div>
  <h3><a href="{esc(a.get('url',''))}" rel="noopener" target="_blank">{esc(a.get('title',''))}</a></h3>
  {f'<p>{esc(a.get("excerpt",""))}</p>' if a.get("excerpt") else ""}
</article>"""


def club_pill(c):
    return f'<button class="pill" data-slug="{esc(c["slug"])}" data-division="{esc(c["division"])}">{esc(c["name"])}</button>'


DIVISION_ORDER = ["championship", "league-one", "league-two"]
DIVISION_LABEL = {"championship": "Championship", "league-one": "League One", "league-two": "League Two"}


def build_html(articles, clubs):
    articles_sorted = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)
    cards_html = "\n".join(article_card(a) for a in articles_sorted)

    clubs_by_div = {d: [c for c in clubs if c["division"] == d] for d in DIVISION_ORDER}
    picker_sections = []
    for d in DIVISION_ORDER:
        pills = "\n".join(club_pill(c) for c in clubs_by_div[d])
        picker_sections.append(f'<section class="division"><h4>{DIVISION_LABEL[d]}</h4><div class="pills">{pills}</div></section>')
    picker_html = "\n".join(picker_sections)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(SITE_TITLE)}</title>
<meta name="description" content="{esc(SITE_TAGLINE)} News from all 72 English Football League clubs.">
<link rel="alternate" type="application/rss+xml" href="feed.xml">
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 0 auto; padding: 1rem; background: Canvas; color: CanvasText; }}
  header {{ margin-bottom: 1rem; }}
  header h1 {{ margin: 0 0 0.2rem; }}
  .tagline {{ opacity: 0.7; font-size: 0.85rem; }}
  #picker-toggle {{ margin: 0.5rem 0; }}
  #picker {{ border: 1px solid; padding: 0.75rem; border-radius: 8px; margin-bottom: 1rem; }}
  .division h4 {{ margin: 0.5rem 0 0.25rem; }}
  .pills {{ display: flex; flex-wrap: wrap; gap: 0.4rem; }}
  .pill {{ border: 1px solid; border-radius: 999px; padding: 0.25rem 0.7rem; background: transparent; color: inherit; font-size: 0.85rem; cursor: pointer; }}
  .pill.selected {{ background: currentColor; }}
  .pill.selected {{ color: Canvas; }}
  .card {{ border-bottom: 1px solid; padding: 0.75rem 0; }}
  .card h3 {{ margin: 0.2rem 0; font-size: 1rem; }}
  .card-meta {{ font-size: 0.75rem; opacity: 0.65; }}
  .card p {{ font-size: 0.9rem; opacity: 0.85; margin: 0.2rem 0 0; }}
  #update-banner {{ display:none; position: sticky; top: 0; background: Highlight; color: HighlightText; padding: 0.5rem; text-align: center; border-radius: 6px; margin-bottom: 0.5rem; }}
</style>
</head>
<body>
<div id="update-banner">New stories are available. <button id="update-btn">Refresh</button></div>
<header>
  <h1>{esc(SITE_TITLE)}</h1>
  <div class="tagline">{esc(SITE_TAGLINE)}</div>
</header>

<button id="picker-toggle">Choose your clubs</button>
<div id="picker" hidden>
{picker_html}
</div>

<main id="feed">
{cards_html}
</main>

<script>
(function() {{
  var STORAGE_KEY = "eflfeed.clubs";
  var picker = document.getElementById("picker");
  var toggle = document.getElementById("picker-toggle");
  var feed = document.getElementById("feed");
  var pills = Array.prototype.slice.call(document.querySelectorAll(".pill"));

  function getSelection() {{
    try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); }}
    catch (e) {{ return []; }}
  }}
  function setSelection(sel) {{
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sel));
  }}
  function applyFilter() {{
    var sel = getSelection();
    pills.forEach(function(p) {{
      p.classList.toggle("selected", sel.indexOf(p.dataset.slug) !== -1);
    }});
    var cards = feed.querySelectorAll(".card");
    cards.forEach(function(c) {{
      if (sel.length === 0) {{ c.hidden = false; return; }}
      var clubs = (c.dataset.clubs || "").split(" ");
      c.hidden = !clubs.some(function(s) {{ return sel.indexOf(s) !== -1; }});
    }});
    toggle.textContent = sel.length ? "Edit your clubs (" + sel.length + ")" : "Choose your clubs";
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

  toggle.addEventListener("click", function() {{
    picker.hidden = !picker.hidden;
  }});

  // Keep the picker open by default until a first selection exists.
  if (getSelection().length === 0) {{ picker.hidden = false; }}
  applyFilter();

  // --- update checking: on load, on tab focus, and periodically.
  // Auto-correct silently on the very first load (session-storage guard
  // against reload loops); after that, show a dismissible banner instead
  // of yanking the page from under someone mid-read.
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
          banner.style.display = "block";
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
  <description>{esc(SITE_TAGLINE)}</description>
{chr(10).join(items)}
</channel>
</rss>"""


def build_sitemap():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{esc(SITE_URL)}/</loc></url>
</urlset>"""


def build_version(articles):
    # Hash of article URLs+published so the client can detect real content
    # changes (not just a rebuild with identical content).
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

    print(f"built site: {len(articles)} articles, {len(clubs)} clubs")


if __name__ == "__main__":
    main()
