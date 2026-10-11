"""Sunucuyu uyanık tutma ucu (/health) ve hata takibi (/v1/errors; görüntüleme: yönetici oturumuyla /v1/admin/errors)."""
import itertools
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.errors import fingerprint, make_router, scrub
from app.main import app, errors_router
from app.models import ClientError, now

from helpers import admin_login

client = TestClient(app)
_n = itertools.count(1)
_hdr: dict = {}


@pytest.fixture(autouse=True)
def _clean():
    for lim in errors_router.limiters:
        lim.reset()
    errors_router.state["n"] = 0
    yield
    for lim in errors_router.limiters:
        lim.reset()


def rep(**kw):
    d = {"message": f"Boom {next(_n)}", "stack": "at foo (App.tsx:10:5)", "kind": "js", "platform": "android", "appVersion": "0.2.0", "screen": "Teslimat", "install": "abc123def456"}
    d.update(kw)
    return client.post("/v1/errors", json=d)


def summary(days=7):
    if not _hdr:  # bir kez yönetici oturumu aç
        _hdr.update(admin_login()[0])
    return client.get(f"/v1/admin/errors?days={days}", headers=_hdr)


# ------------------------------------------------------------------ /health
def test_health_get_head_and_root_head():
    assert client.get("/health").json() == {"ok": True}
    assert client.head("/health").status_code == 200
    assert client.head("/").status_code == 200
    assert client.get("/").json()["service"] == "rota-asistan"


def test_health_does_not_touch_the_database(monkeypatch):
    import app.db as dbm

    def boom():
        raise AssertionError("health veritabanına dokunmamalı")

    monkeypatch.setattr(dbm, "SessionLocal", boom)
    assert client.get("/health").status_code == 200


# ------------------------------------------------------------------ gizlilik: ayıklama
def test_scrub_removes_personal_and_secret_looking_parts():
    t = scrub("Hata: ali@example.com 0532 111 22 33 +90 532 111 22 33 Bearer abcdefghijklmnop eyJhbGciOiJI.eyJzdWIiOiIx.abcdef /a/AbCdEfGhIjKlMnOp https://x.com/y?token=SIRRI&a=1 " + "A" * 40, 1000)
    for bad in ("ali@example.com", "111 22 33", "abcdefghijklmnop", "eyJhbGci", "AbCdEfGhIjKlMnOp", "SIRRI", "A" * 30):
        assert bad not in t
    assert "<e-posta>" in t and "<oturum>" in t and "/a/<kod>" in t


def test_scrub_keeps_ordinary_error_text_and_short_numbers():
    t = "TypeError: undefined is not a function (at App.tsx:123:45) kapı 12"
    assert scrub(t, 300) == t


def test_fingerprint_groups_same_error_with_different_numbers():
    a = fingerprint("js", "Satır 12 bozuk id 99", "at x (A.tsx:10:5)")
    b = fingerprint("js", "Satır 77 bozuk id 4", "at x (A.tsx:11:9)")
    c = fingerprint("js", "Başka hata", "at x (A.tsx:10:5)")
    assert a == b and a != c


# ------------------------------------------------------------------ rapor alma
def test_report_works_without_login_and_is_scrubbed_in_storage():
    r = rep(message="Giriş hatası ali@example.com 05321112233", stack="at login (https://rota.app/x.js?key=ABC:1:2)")
    assert r.status_code == 200 and r.json() == {"ok": True}
    with SessionLocal() as db:
        e = db.query(ClientError).order_by(ClientError.created_at.desc()).first()
        assert "ali@example.com" not in e.message and "05321112233" not in e.message and "key=ABC" not in e.stack
        assert e.install != "abc123def456" and len(e.install) == 16  # ham kod saklanmaz, özeti saklanır


def test_empty_report_is_ignored_and_bad_kind_becomes_js():
    assert rep(message="", stack="").json() == {"ok": True}
    rep(message="Kind testi", kind="sunucu")
    with SessionLocal() as db:
        assert db.query(ClientError).filter(ClientError.message == "Kind testi").one().kind == "js"


def test_sizes_are_clipped():
    rep(message="uzun mesaj " * 400, stack="at satir_bir (A.tsx:1:1)\n" * 800)
    with SessionLocal() as db:
        e = db.query(ClientError).filter(ClientError.message.like("uzun mesaj%")).order_by(ClientError.created_at.desc()).first()
        assert len(e.message) == 300 and len(e.stack) == 1500


def test_ip_rate_limit():
    codes = [rep(message=f"hız {i}").status_code for i in range(45)]
    assert 429 in codes and codes.count(200) == 40


def test_daily_cap_on_a_small_app():
    mini = FastAPI()
    mini.include_router(make_router(14, 5000, 3))
    c = TestClient(mini)
    codes = [c.post("/v1/errors", json={"message": f"x{i}"}).status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]


# ------------------------------------------------------------------ yönetici görünümü
def test_errors_view_needs_an_admin_session_and_the_old_key_header_no_longer_works():
    assert client.get("/v1/admin/errors").status_code == 401
    assert client.get("/v1/admin/errors", headers={"X-Admin-Key": "test-yonetici-anahtari-123456"}).status_code == 401
    assert client.get("/v1/admin/summary", headers={"X-Admin-Key": "test-yonetici-anahtari-123456"}).status_code in (404, 405)
    assert summary().status_code == 200


def test_summary_groups_counts_installs_and_fatal():
    tag = f"Grup{next(_n)}"
    rep(message=f"{tag} satır 1", install="telefon-1", fatal=False)
    rep(message=f"{tag} satır 2", install="telefon-2", fatal=True)
    rep(message=f"{tag} satır 3", install="telefon-2")
    j = summary().json()
    g = [x for x in j["errors"] if x["message"].startswith(tag)]
    assert len(g) == 1 and g[0]["count"] == 3 and g[0]["installs"] == 2 and g[0]["fatal"] is True
    assert j["errorRows"] >= 3
    assert "telefon-1" not in str(j)


def test_summary_days_filter():
    rep(message="Eski hata özel")
    with SessionLocal() as db:
        e = db.query(ClientError).filter(ClientError.message == "Eski hata özel").one()
        e.created_at = now() - timedelta(days=5)
        db.commit()
    assert any(x["message"] == "Eski hata özel" for x in summary(days=7).json()["errors"])
    assert not any(x["message"] == "Eski hata özel" for x in summary(days=1).json()["errors"])


def test_purge_by_age_and_row_cap():
    mini = FastAPI()
    r = make_router(1, 3, 100000)
    mini.include_router(r)
    with SessionLocal() as db:
        db.query(ClientError).delete()
        for i in range(6):
            db.add(ClientError(fingerprint=f"f{i}", message=f"m{i}", created_at=now() - timedelta(minutes=i)))
        db.add(ClientError(fingerprint="old", message="eski", created_at=now() - timedelta(days=3)))
        db.commit()
        r.purge(db)
        left = [e.message for e in db.query(ClientError).order_by(ClientError.created_at.desc()).all()]
    assert left == ["m0", "m1", "m2"]  # eski silindi, sınır 3'e indi (en yeniler kaldı)


def test_admin_page_served_with_secure_headers():
    r = client.get("/yonetim")
    assert r.status_code == 200 and "yönetim" in r.text.lower()
    assert r.headers["cache-control"] == "no-store" and "script-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/static/yonetim.js").status_code == 200


def test_validation_errors_name_the_field_but_never_echo_the_value():
    from test_auth_consent import auth, reg

    tok = reg().json()["token"]
    secret = "çok-gizli-" + "x" * 200
    r = client.post("/v1/me/password", json={"oldPassword": secret, "newPassword": "yeni-sifre-1"}, headers=auth(tok))
    assert r.status_code == 422
    assert "çok-gizli" not in r.text and "xxxxxxxx" not in r.text
    d = r.json()["detail"]
    assert isinstance(d, list) and d[0]["loc"][-1] in ("oldPassword", "old_password") and d[0]["msg"]
    # JSON'a sığmayan sayı yanıtı çökertmez (varsayılan işleyici 500 verirdi)
    r2 = client.post("/v1/me/location", content='{"lat": NaN, "lon": 1}', headers={**auth(tok), "Content-Type": "application/json"})
    assert r2.status_code == 422 and "NaN" not in r2.text
