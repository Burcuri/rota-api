"""Yönetici hesapları: kurulum (ADMIN_KEY), giriş sınırları, iki adımlı doğrulama (TOTP), oturum ayrımı, işlem günlüğü."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.admin import totp_code, verify_totp
from app.db import SessionLocal
from app.main import app
from app.models import Admin, AdminAudit, now

from helpers import ADMIN_PW, KEY, admin_login, make_admin
from test_auth_consent import auth, reg

client = TestClient(app)


def current_code(secret):
    return totp_code(secret, int(time.time() // 30))


# ------------------------------------------------------------------ TOTP
def test_totp_matches_the_rfc6238_test_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890"
    assert totp_code(secret, 1) == "287082"  # RFC 6238: T=59 sn → 94287082 (son 6 hane)
    assert totp_code(secret, 37037036) == "081804"  # T=1111111108
    assert verify_totp(secret, "287082", 0, at=59) == 1
    assert verify_totp(secret, "287082", 0, at=59 + 30) == 1  # bir adım gecikme kabul
    assert verify_totp(secret, "287082", 0, at=59 + 90) is None  # çok eski
    assert verify_totp(secret, "287082", 1, at=59) is None  # aynı adım ikinci kez kullanılamaz
    assert verify_totp(secret, "12345", 0, at=59) is None and verify_totp(secret, "abcdef", 0, at=59) is None


# ------------------------------------------------------------------ kurulum (ADMIN_KEY)
def test_status_tells_the_login_page_what_to_show(monkeypatch):
    s = client.get("/v1/admin/status").json()
    assert s["setupOpen"] is True and set(s) == {"setupOpen", "hasAdmin", "require2fa"}
    monkeypatch.delenv("ADMIN_KEY")
    assert client.get("/v1/admin/status").json()["setupOpen"] is False


def test_setup_needs_the_key_and_a_strong_password(monkeypatch):
    body = {"username": "kurucu", "password": ADMIN_PW}
    assert client.post("/v1/admin/setup", json=body).status_code == 401
    assert client.post("/v1/admin/setup", json=body, headers={"X-Admin-Key": "yanlis"}).status_code == 401
    ok = client.post("/v1/admin/setup", json={"username": "Kısa!", "password": ADMIN_PW}, headers={"X-Admin-Key": KEY})
    assert ok.status_code == 400  # geçersiz ad
    weak = client.post("/v1/admin/setup", json={"username": "kurucu", "password": "kisa"}, headers={"X-Admin-Key": KEY})
    assert weak.status_code == 400 and "10" in weak.json()["detail"]
    monkeypatch.delenv("ADMIN_KEY")
    assert client.post("/v1/admin/setup", json=body, headers={"X-Admin-Key": KEY}).status_code == 404  # anahtar tanımlı değilse uç yok
    monkeypatch.setenv("ADMIN_KEY", "kisa")
    assert client.post("/v1/admin/setup", json=body, headers={"X-Admin-Key": "kisa"}).status_code == 404  # 12 karakterden kısa anahtar geçersiz


def test_setup_locks_out_after_ten_wrong_keys_even_for_the_right_one():
    body = {"username": "kurucu2", "password": ADMIN_PW}
    for _ in range(10):
        assert client.post("/v1/admin/setup", json=body, headers={"X-Admin-Key": "yanlis"}).status_code == 401
    assert client.post("/v1/admin/setup", json=body, headers={"X-Admin-Key": "yanlis"}).status_code == 429
    assert client.post("/v1/admin/setup", json=body, headers={"X-Admin-Key": KEY}).status_code == 429


def test_setup_creates_then_refuses_duplicates_unless_reset_and_reset_kills_sessions():
    name = make_admin()
    again = client.post("/v1/admin/setup", json={"username": name, "password": "baska-sifre-123"}, headers={"X-Admin-Key": KEY})
    assert again.status_code == 409
    h, _ = admin_login(name)
    assert client.get("/v1/admin/stats", headers=h).status_code == 200
    rs = client.post("/v1/admin/setup", json={"username": name, "password": "yeni-sifre-12345", "reset": True}, headers={"X-Admin-Key": KEY})
    assert rs.status_code == 200 and rs.json()["created"] is False
    assert client.get("/v1/admin/stats", headers=h).status_code == 401  # eski oturum kapandı
    assert client.post("/v1/admin/login", json={"username": name, "password": ADMIN_PW}).status_code == 401
    assert client.post("/v1/admin/login", json={"username": name, "password": "yeni-sifre-12345"}).status_code == 200


# ------------------------------------------------------------------ giriş
def test_login_errors_are_generic_and_protected_by_per_user_lockout():
    name = make_admin()
    wrong = client.post("/v1/admin/login", json={"username": name, "password": "yanlis-sifre-1"})
    unknown = client.post("/v1/admin/login", json={"username": "yok_boyle_biri", "password": "yanlis-sifre-1"})
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()  # hesap var mı bilinmez
    for _ in range(7):
        client.post("/v1/admin/login", json={"username": name, "password": "yanlis-sifre-1"})
    assert client.post("/v1/admin/login", json={"username": name, "password": "yanlis-sifre-1"}).status_code == 429
    assert client.post("/v1/admin/login", json={"username": name, "password": ADMIN_PW}).status_code == 429  # kilitliyken doğru şifre de denenmez


def test_failed_logins_are_in_the_audit_log_without_the_password():
    name = make_admin()
    client.post("/v1/admin/login", json={"username": name, "password": "cok-gizli-yanlis-1"})
    h, _ = admin_login()
    items = client.get("/v1/admin/audit", headers=h).json()["items"]
    fail = [x for x in items if x["action"] == "login_fail" and x["admin"] == name]
    assert fail
    assert "cok-gizli-yanlis-1" not in json.dumps(items)


def test_sessions_are_separate_for_admins_and_app_users():
    h, name = admin_login()
    user = reg(role="courier").json()
    # yönetici oturumu uygulama uçlarını açmaz, kullanıcı oturumu yönetim uçlarını açmaz
    assert client.get("/v1/me", headers=h).status_code == 401
    assert client.get("/v1/admin/stats", headers=auth(user["token"])).status_code == 401
    assert client.get("/v1/admin/stats").status_code == 401
    assert client.get("/v1/admin/me", headers=h).json()["admin"]["username"] == name


def test_logout_closes_all_sessions_of_that_admin():
    name = make_admin()
    t1 = client.post("/v1/admin/login", json={"username": name, "password": ADMIN_PW}).json()["token"]
    t2 = client.post("/v1/admin/login", json={"username": name, "password": ADMIN_PW}).json()["token"]
    assert client.post("/v1/admin/logout", headers={"Authorization": "Bearer " + t1}).status_code == 200
    assert client.get("/v1/admin/stats", headers={"Authorization": "Bearer " + t2}).status_code == 401


def test_admin_password_change_needs_old_password_and_ends_old_sessions():
    h, name = admin_login()
    assert client.post("/v1/admin/password", json={"oldPassword": "yanlis", "newPassword": "yeni-sifre-12345"}, headers=h).status_code == 400
    assert client.post("/v1/admin/password", json={"oldPassword": ADMIN_PW, "newPassword": "kisa"}, headers=h).status_code == 400
    assert client.post("/v1/admin/password", json={"oldPassword": ADMIN_PW, "newPassword": ADMIN_PW}, headers=h).status_code == 400
    r = client.post("/v1/admin/password", json={"oldPassword": ADMIN_PW, "newPassword": "yeni-sifre-12345"}, headers=h)
    assert r.status_code == 200
    assert client.get("/v1/admin/stats", headers=h).status_code == 401
    assert client.get("/v1/admin/stats", headers={"Authorization": "Bearer " + r.json()["token"]}).status_code == 200
    assert client.post("/v1/admin/login", json={"username": name, "password": "yeni-sifre-12345"}).status_code == 200


# ------------------------------------------------------------------ iki adımlı doğrulama
def test_two_factor_setup_login_and_replay_protection(monkeypatch):
    import types

    import app.admin as adm

    t0 = 1_700_000_010
    monkeypatch.setattr(adm, "time", types.SimpleNamespace(time=lambda: t0))  # sınır dalgalanması olmasın: sunucu saati sabit
    s0 = t0 // 30
    h, name = admin_login()
    st = client.post("/v1/admin/2fa/start", headers=h).json()
    assert st["uri"].startswith("otpauth://totp/") and st["secret"] in st["uri"] and len(st["secret"]) >= 32
    assert client.post("/v1/admin/2fa/enable", json={"code": "000000"}, headers=h).status_code == 400
    assert client.post("/v1/admin/2fa/enable", json={"code": totp_code(st["secret"], s0)}, headers=h).status_code == 200
    assert client.get("/v1/admin/me", headers=h).json()["admin"]["totp"] is True
    # şifre doğru, kod yok → kod istenir (token verilmez)
    r = client.post("/v1/admin/login", json={"username": name, "password": ADMIN_PW})
    assert r.status_code == 200 and r.json() == {"need2fa": True}
    # şifre yanlışsa 2FA durumu açıklanmaz
    assert client.post("/v1/admin/login", json={"username": name, "password": "yanlis-sifre-1"}).status_code == 401
    login = lambda code: client.post("/v1/admin/login", json={"username": name, "password": ADMIN_PW, "code": code})  # noqa: E731
    assert login("123456").status_code == 401  # yanlış kod
    assert login(totp_code(st["secret"], s0)).status_code == 401  # kurulumda kullanılan kod tekrar kullanılamaz
    assert login(totp_code(st["secret"], s0 - 1)).status_code == 401  # daha eski adım da olmaz
    ok = login(totp_code(st["secret"], s0 + 1))  # bir sonraki adım (pencere içinde) geçer
    assert ok.status_code == 200 and "token" in ok.json()
    assert login(totp_code(st["secret"], s0 + 1)).status_code == 401  # aynı kod ikinci kez olmaz (tekrar saldırısı)


def test_two_factor_can_be_turned_off_with_password_and_code():
    h, name = admin_login()
    st = client.post("/v1/admin/2fa/start", headers=h).json()
    client.post("/v1/admin/2fa/enable", json={"code": current_code(st["secret"])}, headers=h)
    assert client.post("/v1/admin/2fa/start", headers=h).status_code == 409  # açıkken yeniden başlatılamaz
    bad = client.post("/v1/admin/2fa/disable", json={"password": "yanlis", "code": current_code(st["secret"])}, headers=h)
    assert bad.status_code == 400
    with SessionLocal() as db:  # aynı adım yeniden kullanılamaz: bir sonraki adımın kodunu ver
        a = db.query(Admin).filter(Admin.username == name).one()
        a.totp_last_step = 0
        db.commit()
    ok = client.post("/v1/admin/2fa/disable", json={"password": ADMIN_PW, "code": current_code(st["secret"])}, headers=h)
    assert ok.status_code == 200
    assert client.get("/v1/admin/me", headers=h).json()["admin"]["totp"] is False


def test_require_2fa_limits_the_session_until_it_is_set_up(monkeypatch):
    monkeypatch.setenv("ADMIN_REQUIRE_2FA", "1")
    name = make_admin()
    r = client.post("/v1/admin/login", json={"username": name, "password": ADMIN_PW}).json()
    assert r["limited"] is True
    lim = {"Authorization": "Bearer " + r["token"]}
    assert client.get("/v1/admin/users", headers=lim).status_code == 403  # sınırlı oturum yönetim uçlarını açmaz
    assert client.get("/v1/admin/stats", headers=lim).status_code == 403
    assert client.get("/v1/admin/me", headers=lim).status_code == 200
    st = client.post("/v1/admin/2fa/start", headers=lim).json()
    en = client.post("/v1/admin/2fa/enable", json={"code": current_code(st["secret"])}, headers=lim)
    assert en.status_code == 200
    full = {"Authorization": "Bearer " + en.json()["token"]}
    assert client.get("/v1/admin/stats", headers=full).status_code == 200
    # zorunluyken kapatılamaz
    assert client.post("/v1/admin/2fa/disable", json={"password": ADMIN_PW, "code": "000000"}, headers=full).status_code == 400


# ------------------------------------------------------------------ yöneticileri yönetme
def test_admins_can_add_and_disable_other_admins_but_not_themselves():
    h, me = admin_login()
    r = client.post("/v1/admin/admins", json={"username": "yardimci1", "password": "yardimci-sifre-1"}, headers=h)
    assert r.status_code == 200 and r.json()["createdBy"] == me
    assert client.post("/v1/admin/admins", json={"username": "yardimci1", "password": "yardimci-sifre-1"}, headers=h).status_code == 409
    assert client.post("/v1/admin/admins", json={"username": "z", "password": "yardimci-sifre-1"}, headers=h).status_code == 400
    other = client.post("/v1/admin/login", json={"username": "yardimci1", "password": "yardimci-sifre-1"}).json()
    oh = {"Authorization": "Bearer " + other["token"]}
    assert client.get("/v1/admin/stats", headers=oh).status_code == 200
    names = [a["username"] for a in client.get("/v1/admin/admins", headers=h).json()["items"]]
    assert "yardimci1" in names and me in names
    assert client.post(f"/v1/admin/admins/{other['admin']['id']}/disable", headers=h).status_code == 200
    assert client.get("/v1/admin/stats", headers=oh).status_code == 401  # kapatılınca oturumu da düşer
    assert client.post("/v1/admin/login", json={"username": "yardimci1", "password": "yardimci-sifre-1"}).status_code == 401
    me_id = client.get("/v1/admin/me", headers=h).json()["admin"]["id"]
    assert client.post(f"/v1/admin/admins/{me_id}/disable", headers=h).status_code == 400
    assert client.post(f"/v1/admin/admins/{other['admin']['id']}/disable?on=false", headers=h).status_code == 200
    assert client.post("/v1/admin/login", json={"username": "yardimci1", "password": "yardimci-sifre-1"}).status_code == 200


def test_every_admin_endpoint_requires_a_session():
    for method, path in [
        ("get", "/v1/admin/stats"), ("get", "/v1/admin/errors"), ("get", "/v1/admin/users"), ("get", "/v1/admin/users/abc"),
        ("patch", "/v1/admin/users/abc"), ("post", "/v1/admin/users/abc/ban"), ("post", "/v1/admin/users/abc/unban"),
        ("post", "/v1/admin/users/abc/password"), ("post", "/v1/admin/users/abc/logout"), ("get", "/v1/admin/events"),
        ("get", "/v1/admin/reviews"), ("post", "/v1/admin/reviews/abc/hide"), ("delete", "/v1/admin/reviews/abc"),
        ("get", "/v1/admin/audit"), ("get", "/v1/admin/admins"), ("post", "/v1/admin/admins"), ("get", "/v1/admin/me"),
        ("post", "/v1/admin/password"), ("post", "/v1/admin/2fa/start"), ("post", "/v1/admin/logout"),
    ]:
        r = getattr(client, method)(path)
        assert r.status_code in (401, 422), (method, path, r.status_code)
        if r.status_code == 422:  # gövde şart koşan uçlar: boş gövdeyle de oturum şartı önce sorulur
            r = getattr(client, method)(path, json={})
            assert r.status_code == 401, (method, path, r.status_code)


def test_expired_admin_token_is_rejected():
    import jwt

    from app.auth import ALGO, SECRET

    h, name = admin_login()
    with SessionLocal() as db:
        a = db.query(Admin).filter(Admin.username == name).one()
        old = jwt.encode({"sub": a.id, "typ": "adm", "tv": 0, "exp": int(time.time()) - 5}, SECRET, algorithm=ALGO)
    assert client.get("/v1/admin/stats", headers={"Authorization": "Bearer " + old}).status_code == 401
    forged = jwt.encode({"sub": "x", "typ": "adm", "tv": 0, "exp": int(time.time()) + 500}, "baska-anahtar-0123456789abcdef", algorithm=ALGO)
    assert client.get("/v1/admin/stats", headers={"Authorization": "Bearer " + forged}).status_code == 401


def test_audit_log_keeps_actions_and_never_secrets():
    h, name = admin_login()
    r = reg(role="courier", name="Denetim Kullanıcı").json()
    uid_ = r["user"]["id"]
    client.post(f"/v1/admin/users/{uid_}/password", json={"password": "benim-gizli-sifrem-9"}, headers=h)
    client.post(f"/v1/admin/users/{uid_}/ban", json={"reason": "Test nedeni"}, headers=h)
    items = client.get("/v1/admin/audit", headers=h).json()["items"]
    acts = [x["action"] for x in items if x["admin"] == name]
    assert "user_password" in acts and "user_ban" in acts and "login" in acts
    blob = json.dumps(items)
    assert "benim-gizli-sifrem-9" not in blob
    with SessionLocal() as db:
        assert db.query(AdminAudit).filter(AdminAudit.admin_name == name).count() >= 3


def test_purge_removes_old_events_audit_and_reviews():
    from datetime import timedelta

    from app.main import admin_router
    from app.models import Event, Review

    with SessionLocal() as db:
        db.add(Event(kind="user.login", created_at=now() - timedelta(days=100)))
        db.add(AdminAudit(admin_name="eski", action="login", created_at=now() - timedelta(days=500)))
        db.add(Review(source="recipient", subject_id="x", stars=5, created_at=now() - timedelta(days=500)))
        db.add(Event(kind="user.login", created_at=now()))
        db.commit()
        res = admin_router.purge(db, 60, 365, 365, 50000)
        assert res["events"] >= 1 and res["audit"] >= 1 and res["reviews"] >= 1
        assert db.query(Event).filter(Event.created_at < now() - timedelta(days=90)).count() == 0
