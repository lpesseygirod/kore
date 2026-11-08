"""Pepe's icon on the home screen (Lea, 2026-11-08: no phone app, "add the icon")."""

import io
import json

import pytest
from PIL import Image
from starlette.testclient import TestClient

from kore.web import icons
from kore.web.app import create_app
from tests.test_web import log_in


@pytest.fixture
def app(tmp_path, conn, clock):
    return create_app(str(tmp_path / "kore.db"), clock, hosts=["testserver"])


def test_the_manifest_and_icons_load_before_anyone_logs_in(app):
    anon = TestClient(app, follow_redirects=False)
    r = anon.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = json.loads(r.text)
    assert (m["name"], m["start_url"], m["display"], m["lang"]) == ("Pepe", "/", "standalone", "es")
    for icon in m["icons"]:
        got = anon.get(icon["src"])
        assert got.status_code == 200 and got.headers["content-type"] == "image/png"
        w, h = Image.open(io.BytesIO(got.content)).size
        assert f"{w}x{h}" == icon["sizes"]
    assert {i["purpose"] for i in m["icons"]} == {"any", "maskable"}
    assert anon.get("/icono-180.png").status_code == 200  # the iPhone's
    assert anon.get("/icono-999.png").status_code == 404


def test_the_icon_is_wine_with_a_linen_speaker_in_the_safe_middle():
    img = Image.open(io.BytesIO(icons.png(512))).convert("RGB")
    assert img.getpixel((5, 5)) == icons.WINE and img.getpixel((256, 256)) == icons.LINEN
    # maskable: everything that isn't background lies within the middle 80 % circle
    for x in range(0, 512, 4):
        for y in range(0, 512, 4):
            if (x - 256) ** 2 + (y - 256) ** 2 > (0.4 * 512) ** 2:
                assert img.getpixel((x, y)) == icons.WINE


def test_every_page_links_the_icon_and_the_policy_allows_the_manifest(app, conn, home):
    anon = TestClient(app, follow_redirects=False)
    page = anon.get("/entrar")
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page.text
    assert '<link rel="apple-touch-icon" href="/icono-180.png">' in page.text
    assert "manifest-src 'self'" in page.headers["content-security-policy"]
    carmen = log_in(app, conn, home["carmen"])
    page = carmen.get("/ajustes").text
    assert "Añadir a pantalla de inicio" in page and "icono-180.png" in page


def test_icons_cache_but_pages_dont(app):
    anon = TestClient(app, follow_redirects=False)
    assert anon.get("/icono-192.png").headers["cache-control"] == "public, max-age=86400"
    assert anon.get("/entrar").headers["cache-control"] == "no-store"
