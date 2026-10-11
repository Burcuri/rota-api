"""Web ikizi (/web): statik dosya sunumu, tek sayfa uygulama yönlendirmesi, önbellek, gzip, güvenlik başlıkları."""
import gzip

import pytest
from fastapi.testclient import TestClient

from app import webapp
from app.main import app

INDEX = "<!doctype html><html><body><div id='root'></div>" + ("x" * 2000) + "</body></html>"
JS = "console.log('rota');" + ("a" * 3000)


@pytest.fixture()
def site(tmp_path, monkeypatch):
    root = tmp_path / "webapp"
    (root / "_expo" / "static" / "js" / "web").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "metadata.json").write_text('{"version":0}', encoding="utf-8")
    (root / "_expo" / "static" / "js" / "web" / "index-abc123.js").write_text(JS, encoding="utf-8")
    (root / "assets" / "ikon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 50)
    (root / ".gizli").write_text("gizli", encoding="utf-8")
    (root / "yedek.js.gz").write_bytes(gzip.compress(b"x" * 3000))
    (tmp_path / "disarida.txt").write_text("kök dışı", encoding="utf-8")
    monkeypatch.setenv("WEB_APP_DIR", str(root))
    webapp._gz_cache.clear()
    return TestClient(app)


def test_root_redirects_to_slash(site):
    r = site.get("/web", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/web/"


def test_index_served_and_not_cached(site):
    r = site.get("/web/", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.text == INDEX
    assert r.headers["cache-control"] == "no-cache"
    assert "content-encoding" not in r.headers


def test_spa_fallback_for_unknown_paths_without_extension(site):
    for p in ("/web/gorevler", "/web/a/b/c", "/web/index.html"):
        r = site.get(p, headers={"Accept-Encoding": "identity"})
        assert r.status_code == 200 and r.text == INDEX, p
        assert r.headers["cache-control"] == "no-cache"


def test_missing_file_with_extension_is_404_not_index(site):
    assert site.get("/web/yok.js").status_code == 404
    assert site.get("/web/_expo/static/js/web/yok.js").status_code == 404
    assert site.get("/web/assets/yok.png").status_code == 404


def test_hashed_assets_are_immutable(site):
    r = site.get("/web/_expo/static/js/web/index-abc123.js", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200 and r.text == JS
    assert "immutable" in r.headers["cache-control"] and "max-age=31536000" in r.headers["cache-control"]
    r = site.get("/web/assets/ikon.png")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert r.headers["content-type"].startswith("image/png")


def test_root_files_are_revalidated(site):
    r = site.get("/web/metadata.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_gzip_when_accepted(site):
    r = site.get("/web/_expo/static/js/web/index-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip" and "accept-encoding" in r.headers["vary"].lower()
    assert r.text == JS  # istemci açtı
    assert int(r.headers["content-length"]) < len(JS)
    # ikinci istek bellekten
    r2 = site.get("/web/_expo/static/js/web/index-abc123.js", headers={"Accept-Encoding": "gzip, br"})
    assert r2.headers["content-encoding"] == "gzip" and r2.text == JS


def test_no_gzip_for_images_or_when_not_accepted(site):
    r = site.get("/web/assets/ikon.png", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in r.headers
    r = site.get("/web/_expo/static/js/web/index-abc123.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers


def test_gzip_cache_follows_file_changes(site, tmp_path):
    p = tmp_path / "webapp" / "index.html"
    r = site.get("/web/", headers={"Accept-Encoding": "gzip"})
    assert r.text == INDEX
    new = INDEX.replace("root", "kok")
    p.write_text(new, encoding="utf-8")
    import os
    os.utime(p, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    r = site.get("/web/", headers={"Accept-Encoding": "gzip"})
    assert r.text == new


def test_head_request(site):
    r = site.head("/web/", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.content == b""
    r = site.head("/web/_expo/static/js/web/index-abc123.js", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200


def test_path_traversal_and_hidden_files_blocked(site):
    for p in (
        "/web/../disarida.txt", "/web/%2e%2e/disarida.txt", "/web/..%2fdisarida.txt", "/web/assets/../../disarida.txt",
        "/web/%2e%2e%2f%2e%2e%2fdisarida.txt", "/web/.gizli", "/web/yedek.js.gz", "/web/\\..\\disarida.txt",
    ):
        r = site.get(p)
        assert "kök dışı" not in r.text and "gizli" not in r.text, p
        assert r.status_code in (200, 404), p
        if r.status_code == 200:  # uzantısız SPA yolu olabilir; içerik index olmalı
            assert r.text == INDEX or "<html" in r.text, p
    # .gz doğrudan istenemez
    assert site.get("/web/yedek.js.gz").status_code == 404
    assert site.get("/web/.gizli").status_code == 404


def test_security_headers_on_web_but_no_strict_csp(site):
    r = site.get("/web/")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    csp = h["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "script-src" not in csp  # uygulama betik / harita / dış servis çağırır
    assert "geolocation=(self)" in h["permissions-policy"]


def test_other_pages_keep_their_own_policies(site):
    r = site.get("/yonetim")
    assert r.status_code == 200 and "script-src 'self'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"
    r = site.get("/health")
    assert r.status_code == 200 and "content-security-policy" not in r.headers


def test_not_installed_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_APP_DIR", str(tmp_path / "yok"))
    c = TestClient(app)
    r = c.get("/web/")
    assert r.status_code == 404 and "yüklü değil" in r.json()["detail"]
    r = c.get("/web/herhangi")
    assert r.status_code == 404
    assert c.get("/health").status_code == 200  # sunucunun geri kalanı etkilenmez


def test_empty_dir_without_index_is_404(tmp_path, monkeypatch):
    (tmp_path / "bos").mkdir()
    monkeypatch.setenv("WEB_APP_DIR", str(tmp_path / "bos"))
    c = TestClient(app)
    assert c.get("/web/").status_code == 404
