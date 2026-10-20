"""Builds Studio's app icons (user, 2026-10-08): the two interwoven waves with red / cyan
anaglyph edges over a depth map's contour rings, in fifteen colourways, each matte with
film grain or glossy. The theme popout's App Icon section picks one (Shell.appIcon).

    python -m studio.tools.build_app_icons [--set-default navy_blue_matte]

Writes into assets/icons/:
    <id>.ico   every size Windows asks for, each drawn at its own size (never scaled
               down from 256, so the thin edges stay crisp); grain only from 48 up --
               below that it is noise, not texture
    <id>.png   128 px, the picker's thumbnail
    <id>.svg   the artwork (browsers draw its grain as an SVG noise filter; Qt's SVG
               renderer has no filters, so the PNG / ICO grain is studio/grain.py's tile)
    icons.json the list, in picker order
The icon Studio had until 2026-10-08 is kept as "classic" (copied once, never rebuilt).
--set-default copies that icon over assets/StereoCrafter.ico, which the launcher .exe
(tools/launcher/build_launcher.bat), notifications and the manual use.
"""
import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "assets" / "icons"
DEFAULT_ICON = "navy_blue_matte"
ICO_SIZES = [256, 128, 64, 48, 40, 32, 24, 20, 16]
GRAIN = 0.11            # the dark grains' opacity at one sd, as studio/grain.py takes it

LAM, AMP, MID, SW = 185.0, 52.0, 128.0, 24.0
PHB = 0.8 * math.pi     # the second wave a little off the first's mirror, as in the original

NAVY = ("#3a3f58", "#23263a")
BLUE = ("#2f6fe0", "#5aa2ff")
WHITE = ("#e8eef8", "#b9c2d4")
RC = ("#ff2a3a", "#1fe0ff")       # the red / cyan edges, on every one

#: id, name, artwork. Picker order.
COMBOS = [
    ("navy_blue", "Navy Blue", dict(bg=NAVY, a=BLUE, b=WHITE, pattern="contours", po=0.10)),
    ("charcoal_coral", "Charcoal Coral", dict(bg=("#2b2b2f", "#141416"), a=("#ff5a5f", "#ff8a80"), b=("#ffffff", "#dcdcdc"), pattern="contours", po=0.10)),
    ("navy_periwinkle", "Navy Periwinkle", dict(bg=NAVY, a=("#8b93ff", "#b3b8ff"), b=("#ffffff", "#dcdcdc"), pattern="contours", po=0.10)),
    ("teal_slate", "Teal Slate", dict(bg=("#1f3634", "#0f1c1b"), a=("#7fe3d2", "#2fb8a6"), b=("#eef7f5", "#b7cbc7"), pattern="contours", po=0.10)),
    ("plum", "Plum", dict(bg=("#3a2742", "#1c1222"), a=("#c08cff", "#e9b3ff"), b=("#f5eaf8", "#cdb8d6"), pattern="contours", po=0.10)),
    ("true_black_red", "True Black Red", dict(bg=("#161616", "#000000"), a=("#e0313a", "#ff6a5c"), b=("#ffffff", "#e6e6e6"), pattern="contours", po=0.12)),
    ("ember", "Ember", dict(bg=("#33281f", "#17110c"), a=("#ffc35a", "#ff8a1f"), b=("#fbf1e4", "#d6c4ad"), pattern="contours", po=0.10)),
    ("ocean_cyan", "Ocean Cyan", dict(bg=("#0f3b5c", "#071a2c"), a=("#00c6ff", "#4fd8ff"), b=("#ffffff", "#dcefff"), pattern="contours", po=0.12)),
    ("paper_blue", "Paper Blue", dict(bg=("#f6f3ec", "#dcd6ca"), a=BLUE, b=("#4a5070", "#2c3046"), pattern="contours", pc="#23263a", po=0.10, rim=0.5, shadow=False)),
    ("graphite_gold", "Graphite Gold", dict(bg=("#2d2d30", "#151517"), a=("#f3d27a", "#d9a441"), b=("#ffffff", "#e3e3e3"), pattern="contours", pc="#f3d27a", po=0.10)),
    ("charcoal_cyan", "Charcoal Cyan", dict(bg=("#2b2b2f", "#141416"), a=("#2fd6e8", "#8cf1ff"), b=("#ffffff", "#dcdcdc"), pattern="contours_ana", po=0.16)),
    ("navy_coral", "Navy Coral", dict(bg=NAVY, a=("#ff6b6b", "#ff9a8a"), b=("#ffffff", "#dcdcdc"), pattern="contours_ana", po=0.16)),
    ("black_amber", "Black Amber", dict(bg=("#161616", "#000000"), a=("#ffb347", "#ffd27f"), b=("#ffffff", "#e6e6e6"), pattern="contours_ana", po=0.20)),
    ("midnight_cyan", "Midnight Cyan", dict(bg=("#14182a", "#080a14"), a=("#1fe0ff", "#7ff0ff"), b=("#ffffff", "#dcdcdc"), pattern="contours", pc="#1fe0ff", po=0.16)),
    ("wine_red", "Wine Red", dict(bg=("#2e1418", "#14080a"), a=("#ff3b4a", "#ff7a80"), b=("#ffffff", "#dcdcdc"), pattern="contours", pc="#ff2a3a", po=0.16)),
]


def _y(x, phase, amp=AMP):
    return MID - amp * math.sin(2 * math.pi * (x - 10) / LAM + phase)


def _wave(phase, amp=AMP, x0=-30, x1=290, step=2):
    return "M" + " L".join(f"{x:.1f},{_y(x, phase, amp):.1f}" for x in range(x0, x1 + 1, step))


def _crossings():
    xs, prev = [], None
    for x in range(-30, 291):
        d = _y(x, 0) - _y(x, PHB)
        if prev is not None and (d > 0) != (prev > 0):
            xs.append(x)
        prev = d
    return xs


def svg(v: dict) -> str:
    """The icon as a 256 x 256 SVG."""
    (bg1, bg2), (a1, a2), (b1, b2) = v["bg"], v["a"], v["b"]
    sw = v.get("sw", SW)
    p, po = v.get("pc", "#ffffff"), v.get("po", 0.07)
    defs = (f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{bg1}"/><stop offset="1" stop-color="{bg2}"/></linearGradient>'
            f'<linearGradient id="ga" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="256" y2="0"><stop offset="0" stop-color="{a1}"/><stop offset="1" stop-color="{a2}"/></linearGradient>'
            f'<linearGradient id="gb" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="256" y2="0"><stop offset="0" stop-color="{b1}"/><stop offset="1" stop-color="{b2}"/></linearGradient>'
            '<clipPath id="sq"><rect x="8" y="8" width="240" height="240" rx="56"/></clipPath>'
            f'<radialGradient id="shine" cx="0.3" cy="0.15" r="0.9"><stop offset="0" stop-color="#ffffff" stop-opacity="{v.get("shine", 0.16)}"/>'
            '<stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>')
    rings = ""
    for i in range(1, 16):       # a depth map's iso-lines, nearer toward the bottom left
        if v.get("pattern") == "contours_ana":
            for dx, col in ((-2.5, RC[0]), (2.5, RC[1])):
                rings += f'<circle cx="{40 + dx}" cy="250" r="{i * 20}" fill="none" stroke="{col}" stroke-opacity="{po}" stroke-width="2"/>'
            rings += f'<circle cx="40" cy="250" r="{i * 20}" fill="none" stroke="{p}" stroke-opacity="{po * 0.6}" stroke-width="1.4"/>'
        else:
            rings += f'<circle cx="40" cy="250" r="{i * 20}" fill="none" stroke="{p}" stroke-opacity="{po}" stroke-width="2"/>'
    wa, wb = _wave(0), _wave(PHB)
    ghost = ""
    for dx, col in ((-5, RC[0]), (5, RC[1])):    # the anaglyph fringe
        ghost += (f'<g transform="translate({dx},0)" opacity="0.55"><path d="{wa}" stroke="{col}" stroke-width="{sw}" fill="none" stroke-linecap="round"/>'
                  f'<path d="{wb}" stroke="{col}" stroke-width="{sw}" fill="none" stroke-linecap="round"/></g>')
    shadow = ""
    if v.get("shadow", True):
        shadow = f'<path d="{wb}" stroke="#000000" stroke-opacity="{v.get("shadow_op", 0.28)}" stroke-width="{sw + 6}" fill="none" transform="translate(0,3)"/>'
    # the weave: A under B, then that stretch of A again over B at every other crossing
    # (explicit pieces: clip paths aren't in every SVG renderer)
    over = "".join(f'<path d="{_wave(0, x0=int(cx - sw * 1.6), x1=int(cx + sw * 1.6), step=1)}" stroke="url(#ga)" stroke-width="{sw}" fill="none"/>'
                   for k, cx in enumerate(_crossings()) if k % 2 == 0)
    grain = ""
    if v.get("grain"):
        defs += ('<filter id="gr" x="0" y="0" width="1" height="1"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="3"/>'
                 '<feColorMatrix type="matrix" values="0 0 0 0 0.5  0 0 0 0 0.5  0 0 0 0 0.5  0 0 0 0 0 0 0 0 1.6 -0.55"/></filter>')
        grain = '<!--grain--><rect width="256" height="256" fill-opacity="0" filter="url(#gr)" opacity="0.5"/><!--/grain-->'
    rim = (f'<rect x="8.75" y="8.75" width="238.5" height="238.5" rx="55.5" fill="none" stroke="#ffffff" '
           f'stroke-opacity="{v.get("rim", 0.10)}" stroke-width="1.5"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256"><defs>{defs}</defs>'
            f'<g clip-path="url(#sq)"><rect width="256" height="256" fill="url(#bg)"/>{rings}<rect width="256" height="256" fill="url(#shine)"/>'
            f'{ghost}<path d="{wa}" stroke="url(#ga)" stroke-width="{sw}" fill="none" stroke-linecap="round"/>{shadow}'
            f'<path d="{wb}" stroke="url(#gb)" stroke-width="{sw}" fill="none" stroke-linecap="round"/>{over}{grain}</g>{rim}</svg>')


def _rgb(h):
    return [int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def _hex(c):
    return "#" + "".join(f"{int(round(max(0.0, min(1.0, x)) * 255)):02x}" for x in c)


def _mix(a, b, t):
    return _hex([x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))])


def _desat(h, t):
    c = _rgb(h)
    g = sum(c) / 3
    return _hex([x + (g - x) * t for x in c])


def matte(v: dict) -> dict:
    """No sheen: a flatter ground, near-flat and slightly greyer waves, softer shadow and rim, grain."""
    v = dict(v)
    b1, b2 = v["bg"]
    v["bg"] = (_mix(b1, b2, 0.35), _mix(b2, b1, 0.25))
    for k in ("a", "b"):
        c1, c2 = (_desat(c, 0.15) for c in v[k])
        v[k] = (c1, _mix(c2, c1, 0.6))
    v.update(shine=0.0, shadow_op=0.16, grain=True,
             rim=min(v.get("rim", 0.10), 0.05) if v.get("rim", 0.10) < 0.3 else 0.25)
    return v


def render(art: str, size: int, grain: float = 0.0) -> QImage:
    """The SVG at `size`, its rounded square clipped here (QtSvg ignores <clipPath>),
    with studio/grain.py's grain over it."""
    art = re.sub(r"<!--grain-->.*?<!--/grain-->", "", art)
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    k = size / 256
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(8 * k, 8 * k, 240 * k, 240 * k), 56 * k, 56 * k)
    p.setClipPath(clip)
    QSvgRenderer(QByteArray(art.encode())).render(p, QRectF(0, 0, size, size))
    if grain > 0:
        import cv2
        from studio.grain import TILE, grain_tile
        t = grain_tile(grain, 0.8, max(1.0, 1.7 * size / 512), seed=7)
        if size != TILE:
            t = cv2.resize(t, (size, size), interpolation=cv2.INTER_AREA if size < TILE else cv2.INTER_LINEAR)
        g = np.ascontiguousarray(t)
        p.drawImage(0, 0, QImage(g.data, size, size, size * 4, QImage.Format_ARGB32_Premultiplied).copy())
    p.end()
    return img


def _pil(img: QImage) -> Image.Image:
    img = img.convertToFormat(QImage.Format_RGBA8888)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, :img.width()]
    return Image.fromarray(a.copy())          # 4 channels: RGBA


def _grain_at(size: int, matte_finish: bool) -> float:
    if not matte_finish or size < 48:
        return 0.0
    return GRAIN if size >= 64 else GRAIN * 0.6


def build(out: Path = OUT) -> list:
    out.mkdir(parents=True, exist_ok=True)
    classic = out / "classic.ico"
    if not classic.exists():      # the icon Studio had until now, kept as it was
        shutil.copyfile(REPO / "assets" / "StereoCrafter.ico", classic)
    im = Image.open(classic)
    im.size = max(im.info.get("sizes", {im.size}))
    im.convert("RGBA").resize((128, 128), Image.LANCZOS).save(out / "classic.png")
    listing = []
    for key, name, art in COMBOS:
        for finish in ("matte", "glossy"):
            v = matte(art) if finish == "matte" else art
            ident = f"{key}_{finish}"
            text = svg(v)
            (out / f"{ident}.svg").write_text(text, encoding="utf-8")
            imgs = [_pil(render(text, s, _grain_at(s, finish == "matte"))) for s in ICO_SIZES]
            imgs[0].save(out / f"{ident}.ico", format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=imgs[1:])
            _pil(render(text, 128, _grain_at(128, finish == "matte"))).save(out / f"{ident}.png")
            listing.append({"id": ident, "name": name, "finish": finish})
    listing.append({"id": "classic", "name": "Classic", "finish": ""})
    (out / "icons.json").write_text(json.dumps(listing, indent=1), encoding="utf-8")
    return listing


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set-default", metavar="ID", help="also copy that icon over assets/StereoCrafter.ico")
    args = ap.parse_args(argv)
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841 -- QSvgRenderer wants one
    listing = build()
    print(f"wrote {len(listing)} icons to {OUT}")
    if args.set_default:
        src = OUT / f"{args.set_default}.ico"
        if not src.exists():
            print(f"no such icon: {args.set_default}")
            return 1
        shutil.copyfile(src, REPO / "assets" / "StereoCrafter.ico")
        print(f"assets/StereoCrafter.ico is now {args.set_default}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
