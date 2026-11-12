"""Pepe's icon, for the phone's home screen and a Windows computer's Start menu (no phone app, just
the icon).

Drawn here with Pillow (already a dependency) when the server starts, in the colours of the Persephone design: a
pomegranate on marble. No font, no image files in the repo. Full-bleed and centred inside the middle 60 %, so the same picture works
where the phone rounds or crops it (iPhone, Android's "maskable" icons).

The manifest makes "Add to home screen" open Pepe as an app of its own: its own window, no browser bars. On an iPhone
that app keeps its own login, so the person enters a code once more inside it (Pepe says it out loud, as usual).
"""

from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path

MARBLE = (0xF7, 0xF2, 0xEA)  # "Persephone" (docs/design/persephone/)
POMEGRANATE = (0x8C, 0x1C, 0x2B)
SEED = (0xE0, 0x4A, 0x5F)
LEAF = (0x9D, 0xB8, 0x9A)
SIZES = (180, 192, 512)  # 180: iPhone; 192 and 512: Android, Chrome and Edge on Windows
FONTS = {"fraunces.woff2", "figtree.woff2"}  # SIL Open Font Licence; the licences are next to them
FONT_DIR = Path(__file__).parent / "fonts"


@lru_cache(maxsize=None)
def font(name: str) -> bytes:
    if name not in FONTS:
        raise ValueError(name)
    return (FONT_DIR / name).read_bytes()


@lru_cache(maxsize=None)
def png(size: int) -> bytes:
    """The pomegranate of the mark (pages.POMEGRANATE, a 54 x 58 drawing) on marble, inside the middle 60 %."""
    from PIL import Image, ImageDraw

    if size not in SIZES:
        raise ValueError(size)
    big = size * 4  # drawn large, then scaled down: smooth edges
    img = Image.new("RGB", (big, big), MARBLE)
    d = ImageDraw.Draw(img)
    k = big * 0.60 / 58
    ox, oy = (big - 54 * k) / 2, (big - 58 * k) / 2

    def pt(x, y):
        return (ox + x * k, oy + y * k)

    def dot(x, y, r, fill):
        d.ellipse((*pt(x - r, y - r), *pt(x + r, y + r)), fill=fill)

    d.polygon([pt(31, 3), pt(35, 1.2), pt(39.5, 4), pt(39, 8), pt(35, 8.6), pt(32, 6.5)], fill=LEAF)
    d.polygon([pt(17, 12), pt(20, 18), pt(23.5, 13), pt(27, 18), pt(30.5, 13), pt(34, 18), pt(37, 12), pt(37, 20),
               pt(17, 20)], fill=POMEGRANATE)
    dot(27, 36, 20, POMEGRANATE)
    for x, y, r in ((21, 32, 2.6), (29, 40, 2.6), (33, 31, 2.2)):
        dot(x, y, r, SEED)
    out = io.BytesIO()
    img.resize((size, size), Image.Resampling.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def manifest() -> bytes:
    icons = [{"src": f"/icono-{s}.png", "sizes": f"{s}x{s}", "type": "image/png", "purpose": "any"}
             for s in (192, 512)]
    icons.append({"src": "/icono-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"})
    return json.dumps({
        "id": "/", "name": "Pepe", "short_name": "Pepe", "lang": "es", "dir": "ltr",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#F7F2EA", "theme_color": "#8C1C2B", "icons": icons,
    }, ensure_ascii=False).encode()


HEAD = ('<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" type="image/png" href="/icono-192.png">'
        '<link rel="apple-touch-icon" href="/icono-180.png"><meta name="apple-mobile-web-app-title" content="Pepe">'
        '<meta name="theme-color" content="#8C1C2B">')
