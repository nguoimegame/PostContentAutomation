"""Text pictures ("cards") for posts: a headline, a few points and a footer on a coloured background, drawn by the
browser this program already uses. The AI only chooses the words and a theme from a fixed list; the page that is drawn
comes from the template below, so nothing the AI writes can run as code.

    python ai_cli.py card --content-file bai.txt --out anh.png
"""
import html
import os

THEMES = {
    # name: (background gradient, text colour, accent colour)
    "sunrise": ("linear-gradient(135deg,#ff9a5a,#ff5e7e)", "#ffffff", "#fff3c4"),
    "ocean": ("linear-gradient(135deg,#1e6fd9,#12b5cb)", "#ffffff", "#d6f5ff"),
    "forest": ("linear-gradient(135deg,#1f7a4d,#6cc070)", "#ffffff", "#eaffd8"),
    "night": ("linear-gradient(135deg,#1b1f3b,#4b3f8f)", "#ffffff", "#ffd86b"),
    "paper": ("linear-gradient(135deg,#fbf6ea,#efe3c8)", "#2b2b2b", "#b3451f"),
    "berry": ("linear-gradient(135deg,#8e2de2,#e0457b)", "#ffffff", "#ffe3f0"),
}
DEFAULT_THEME = "ocean"
FONTS = '"Segoe UI","Be Vietnam Pro","Noto Sans","Helvetica Neue",Arial,sans-serif'


def build_html(spec, size=1080):
    """The page for a card. All text is escaped; the theme must be one of THEMES (anything else uses the default)."""
    background, colour, accent = THEMES.get(spec.get("theme"), THEMES[DEFAULT_THEME])
    esc = lambda value: html.escape(str(value or "").strip())  # noqa: E731
    points = "".join(f"<li>{esc(point)}</li>" for point in spec.get("points", []) if str(point).strip())
    subline = f'<p class="sub">{esc(spec.get("subline"))}</p>' if esc(spec.get("subline")) else ""
    footer = f'<div class="footer">{esc(spec.get("footer"))}</div>' if esc(spec.get("footer")) else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:{size}px;height:{size}px;overflow:hidden}}
.card{{box-sizing:border-box;width:{size}px;height:{size}px;padding:{size // 10}px;background:{background};color:{colour};
font-family:{FONTS};display:flex;flex-direction:column;justify-content:center;gap:{size // 30}px;position:relative}}
h1{{margin:0;font-size:{size // 11}px;line-height:1.15;font-weight:800;letter-spacing:-.01em}}
.sub{{margin:0;font-size:{size // 24}px;line-height:1.3;opacity:.92}}
ul{{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:{size // 50}px}}
li{{font-size:{size // 26}px;line-height:1.3;padding-left:{size // 22}px;position:relative}}
li:before{{content:"";position:absolute;left:0;top:.45em;width:{size // 60}px;height:{size // 60}px;border-radius:50%;background:{accent}}}
.bar{{width:{size // 8}px;height:{size // 90}px;border-radius:99px;background:{accent}}}
.footer{{position:absolute;left:{size // 10}px;right:{size // 10}px;bottom:{size // 16}px;font-size:{size // 34}px;opacity:.85;color:{accent}}}
</style></head><body><div class="card" id="card"><div class="bar"></div><h1 id="headline">{esc(spec.get("headline"))}</h1>{subline}
<ul>{points}</ul>{footer}</div>
<script>
// Long texts: shrink the text step by step until everything fits above the footer
const card = document.getElementById('card');
const limit = {size} - {size // 10} - {size // 8};
let scale = 1;
function used() {{ let h = 0; for (const el of card.children) {{ if (!el.classList.contains('footer')) h += el.getBoundingClientRect().height; }} return h + 40; }}
while (used() > limit && scale > 0.35) {{
  scale -= 0.05;
  document.getElementById('headline').style.fontSize = ({size // 11} * scale) + 'px';
  for (const el of card.querySelectorAll('.sub')) el.style.fontSize = ({size // 24} * scale) + 'px';
  for (const el of card.querySelectorAll('li')) el.style.fontSize = ({size // 26} * scale) + 'px';
}}
</script></body></html>"""


def render_card(spec, out_path, size=1080, browser=None):
    """Draw the card to a PNG file at `out_path` and return the path. Pass an open Playwright `browser` to reuse it."""
    folder = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(folder, exist_ok=True)

    def draw(open_browser):
        page = open_browser.new_page(viewport={"width": size, "height": size})
        try:
            page.set_content(build_html(spec, size))
            page.screenshot(path=out_path, clip={"x": 0, "y": 0, "width": size, "height": size})
        finally:
            page.close()

    if browser is not None:
        draw(browser)
        return out_path
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        own = playwright.chromium.launch(headless=True)
        try:
            draw(own)
        finally:
            own.close()
    return out_path
