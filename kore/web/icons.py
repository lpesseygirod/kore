"""Pepe's icon, for the phone's home screen and a Windows computer's Start menu (Lea, 2026-11-08: no phone app,
"add the icon").

Drawn here with Pillow (already a dependency) when the server starts, in the colours of Lea's design: a linen speaker
on wine. No font, no image files in the repo. Full-bleed and centred inside the middle 60 %, so the same picture works
where the phone rounds or crops it (iPhone, Android's "maskable" icons).

The manifest makes "Add to home screen" open Pepe as an app of its own: its own window, no browser bars. On an iPhone
that app keeps its own login, so the person enters a code once more inside it (Pepe says it out loud, as usual).
"""

from __future__ import annotations

import io
import json
from functools import lru_cache

WINE = (0x6E, 0x1E, 0x35)
LINEN = (0xFB, 0xF7, 0xF5)
SIZES = (180, 192, 512)  # 180: iPhone; 192 and 512: Android, Chrome and Edge on Windows


@lru_cache(maxsize=None)
def png(size: int) -> bytes:
    from PIL import Image, ImageDraw

    if size not in SIZES:
        raise ValueError(size)
    big = size * 4  # drawn large, then scaled down: smooth edges
    img = Image.new("RGB", (big, big), WINE)
    d = ImageDraw.Draw(img)
    c = big / 2
    outer, ring = big * 0.30, big * 0.075
    d.ellipse((c - outer, c - outer, c + outer, c + outer), fill=LINEN)
    inner = outer - ring
    d.ellipse((c - inner, c - inner, c + inner, c + inner), fill=WINE)
    dot = big * 0.11
    d.ellipse((c - dot, c - dot, c + dot, c + dot), fill=LINEN)
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
        "background_color": "#FBF7F5", "theme_color": "#6E1E35", "icons": icons,
    }, ensure_ascii=False).encode()


HEAD = ('<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" type="image/png" href="/icono-192.png">'
        '<link rel="apple-touch-icon" href="/icono-180.png"><meta name="apple-mobile-web-app-title" content="Pepe">'
        '<meta name="theme-color" content="#6E1E35">')
