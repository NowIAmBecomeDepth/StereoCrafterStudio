"""Bundle the manual into ONE self-contained HTML file, to read anywhere --
a phone, away from the PC (user, 2026-10-01): the stylesheet inline, Studio's
fonts and the screenshots embedded (screenshots shrunk for a phone and saved
as WebP), and every page of the manual on one page, its links turned into
in-page jumps.

    python docs/manual/build_offline.py [out.html]
"""
from __future__ import annotations

import base64
import io
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PAGES = ["index.html", "part1.html", "part2.html", "part3.html", "part4.html", "part5.html", "appendices.html"]          # in reading order; add parts here as they're written
SHOT_WIDTH = 1600                              # px: sharp on a phone, a fraction of the PNGs' size
SHOT_QUALITY = 82
#: The manual's own copies of the shots (shots/*.webp, what the pages show and git keeps): the shot scripts'
#: PNGs made WebP at this quality -- a quarter of the size, the same to the eye (release check 2026-10-17).
#: The PNGs stay on the developer's PC (git ignores them) as the full-quality originals.
PAGE_SHOT_QUALITY = 92


def data_uri(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def webp(path: Path) -> str:
    img = Image.open(path).convert("RGB")
    if img.width > SHOT_WIDTH:
        img = img.resize((SHOT_WIDTH, round(img.height * SHOT_WIDTH / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=SHOT_QUALITY, method=6)
    return data_uri("image/webp", buf.getvalue())


def page_shots() -> int:
    """Each shots/*.png made shots/*.webp when the WebP is missing or older. Returns how many were made."""
    made = 0
    for png in sorted((HERE / "shots").glob("*.png")):
        out = png.with_suffix(".webp")
        if out.exists() and out.stat().st_mtime >= png.stat().st_mtime:
            continue
        Image.open(png).convert("RGB").save(out, "WEBP", quality=PAGE_SHOT_QUALITY, method=6)
        made += 1
    return made


def icon() -> str:
    img = Image.open(REPO / "assets" / "StereoCrafter.ico").convert("RGBA").resize((32, 32), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return data_uri("image/png", buf.getvalue())


def css() -> str:
    text = (HERE / "manual.css").read_text(encoding="utf-8")

    def font(m: re.Match) -> str:
        raw = (HERE / m.group(1)).resolve().read_bytes()
        return f'url("{data_uri("font/ttf", raw)}")'

    return re.sub(r'url\("(\.\./\.\./assets/fonts/[^"]+)"\)', font, text)


def page_main(name: str) -> str:
    html = (HERE / name).read_text(encoding="utf-8")
    main = re.search(r"<main class=\"page\">(.*?)</main>", html, re.S).group(1)
    main = re.sub(r"<nav class=\"foot\">.*?</nav>", "", main, flags=re.S)
    page_id = Path(name).stem
    return f'<div class="manual-page" id="{"contents" if page_id == "index" else page_id}">{main}</div>'


class _Sections(HTMLParser):
    """A page's sections for the search: each h2-h4 starts one, running to the next; it jumps to the
    heading's id, else the nearest id before it (its section)."""

    SKIP = {"nav", "script", "style"}

    def __init__(self, page: str):
        super().__init__(convert_charrefs=True)
        self.page, self.out, self.part = page, [], ""
        self.anchor, self.depth_skip, self.head, self.head_text, self.in_h1 = "", 0, None, "", False
        self.h2 = ""                       # the stage / chapter an h3 or h4 is in

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP or "anchor" in (a.get("class") or "").split():
            self.depth_skip += 1
            return
        if a.get("id"):
            self.anchor = a["id"]
        if tag == "h1":
            self.in_h1 = True
        elif tag in ("h2", "h3", "h4"):
            self.head, self.head_text = tag, ""

    def handle_endtag(self, tag):
        if self.depth_skip and (tag in self.SKIP or tag == "a"):
            self.depth_skip -= 1
            return
        if tag == "h1":
            self.in_h1 = False
        elif tag == self.head:
            href = self.page + (f"#{self.anchor}" if self.anchor else "")
            title = " ".join(self.head_text.split())
            if tag == "h2":
                self.h2 = title
            where = self.part + (f" · {self.h2}" if tag != "h2" and self.h2 else "")
            self.out.append({"part": where, "title": title, "href": href, "text": ""})
            self.head = None

    def handle_data(self, data):
        if self.depth_skip:
            return
        if self.in_h1:
            self.part += data.strip()
        elif self.head:
            self.head_text += data
        elif self.out:
            self.out[-1]["text"] += data


def search_index() -> Path:
    """search_index.js: every page's sections, for search.js on the manual's own pages."""
    sections = []
    for name in PAGES:
        html = (HERE / name).read_text(encoding="utf-8")
        main = re.search(r"<main class=\"page\">(.*?)</main>", html, re.S).group(1)
        main = re.sub(r"<nav class=\"foot\">.*?</nav>", "", main, flags=re.S)
        parser = _Sections(name)
        parser.feed(main)
        for sec in parser.out:
            sec["text"] = " ".join(sec["text"].split())
            sections.append(sec)
    path = HERE / "search_index.js"
    path.write_text("// written by build_offline.py -- the manual's sections, for search.js\n"
                    "window.MANUAL_INDEX = " + json.dumps(sections, ensure_ascii=False, indent=0) + ";\n",
                    encoding="utf-8")
    return path


def build(out: Path) -> Path:
    shots: dict[str, str] = {}

    def shot(m: re.Match) -> str:
        src = m.group(1)
        if src not in shots:
            shots[src] = webp(HERE / src)
        return f'src="{shots[src]}"'

    body = "\n".join(page_main(p) for p in PAGES)
    # links between pages become jumps within this one
    body = re.sub(r'href="index\.html"', 'href="#contents"', body)
    body = re.sub(r'href="(part\d+|appendices)\.html#([\w-]+)"', r'href="#\2"', body)
    body = re.sub(r'href="(part\d+|appendices)\.html"', r'href="#\1"', body)
    # a screenshot linked rather than shown (Beginner Mode's "with it off") opens embedded too
    body = re.sub(r'href="(shots/[^"]+\.(?:png|webp))"', lambda m: f'href="{shots.setdefault(m.group(1), webp(HERE / m.group(1)))}"', body)
    body = re.sub(r'src="(shots/[^"]+\.(?:png|webp))"', shot, body)

    tabs = ['<a href="#contents">Contents</a>'] + [
        f'<a href="#{Path(p).stem}"><span class="n">{i}.</span>{label}</a>'
        for i, (p, label) in enumerate([("part1.html", "Getting started"), ("part2.html", "The pipeline"),
                                        ("part3.html", "Depth Map Editor"), ("part4.html", "Running jobs"),
                                        ("part5.html", "Customising")], start=1)]
    tabs.append('<a href="#appendices">Appendices</a>')
    search_js = (HERE / "search.js").read_text(encoding="utf-8")   # the search box, reading this page
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>StereoCrafter Studio Manual</title>
<link rel="icon" href="{icon()}">
<style>
{css()}
.manual-page + .manual-page {{ margin-top: 40px; padding-top: 10px; border-top: 1px solid var(--border); }}
html {{ scroll-behavior: smooth; }}
.manual-page, .section {{ scroll-margin-top: 90px; }}
</style>
</head>
<body>
<div class="caption"><img src="{icon()}" alt=""><span>StereoCrafter Studio · Manual</span><span class="where">offline copy</span></div>
<nav class="tabs">{''.join(tabs)}</nav>
<main class="page">
{body}
</main>
<a class="to-top" href="#" aria-label="Back to top" title="Back to top"><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></a>
<script>(function () {{  // the back-to-top button: shown once the page is scrolled down
  var b = document.querySelector(".to-top");
  function f() {{ b.classList.toggle("show", window.scrollY > 400); }}
  addEventListener("scroll", f, {{ passive: true }}); f();
  b.addEventListener("click", function (e) {{ e.preventDefault(); window.scrollTo({{ top: 0, behavior: "smooth" }}); }});
}})();</script>
<script>
{search_js}
</script>
</body>
</html>
"""
    out.write_text(doc.replace("\r\n", "\n"), encoding="utf-8")
    search_index()                     # the manual's own pages search this
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "StereoCrafter-Studio-Manual.html"
    made = page_shots()
    if made:
        print(f"{made} shot(s) made WebP")
    path = build(target)
    print(f"{path}  {path.stat().st_size / 1e6:.1f} MB")
