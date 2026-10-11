"""Üye yönetimi: liste / arama / ayrıntı / düzenleme, men (ban), şifre sıfırlama, oturum kapatma, kullanıcının kendi şifre değişikliği,
etkinlik akışı."""
import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth import ALGO, SECRET, make_token
from app.db import SessionLocal
from app.main import app, pw_fail
from app.models import Event, Job, User

from helpers import admin_login
from test_auth_consent import auth, phone, reg

client = TestClient(app)
PW = "sifre123"


def new_user(role="courier", **kw):
    body = {"role": role, "name": kw.pop("name", "Test Üye"), **kw}
    r = reg(**body).json()
    return r["token"], r["user"]


def login(user, password=PW):
    return client.post("/v1/auth/login", json={"phone": user["phone"], "password": password})


# ------------------------------------------------------------------ kullanıcının kendi şifresi
def test_user_changes_own_password_and_other_sessions_end():
    tok, u = new_user()
    other = login(u).json()["token"]  # ikinci cihaz
    bad = client.post("/v1/me/password", json={"oldPassword": "yanlis", "newPassword": "yeni-sifre-1"}, headers=auth(tok))
    assert bad.status_code == 400 and "Eski" in bad.json()["detail"]
    assert client.post("/v1/me/password", json={"oldPassword": PW, "newPassword": "123"}, headers=auth(tok)).status_code == 400
    assert client.post("/v1/me/password", json={"oldPassword": PW, "newPassword": PW}, headers=auth(tok)).status_code == 400
    ok = client.post("/v1/me/password", json={"oldPassword": PW, "newPassword": "yeni-sifre-1"}, headers=auth(tok))
    assert ok.status_code == 200 and ok.json()["user"]["id"] == u["id"]
    assert client.get("/v1/me", headers=auth(tok)).status_code == 401  # eski oturum kapandı
    assert client.get("/v1/me", headers=auth(other)).status_code == 401
    assert client.get("/v1/me", headers=auth(ok.json()["token"])).status_code == 200  # bu cihaza yeni anahtar verildi
    assert login(u).status_code == 401 and login(u, "yeni-sifre-1").status_code == 200


def test_wrong_old_password_is_rate_limited():
    tok, u = new_user()
    for _ in range(8):
        assert client.post("/v1/me/password", json={"oldPassword": "yanlis", "newPassword": "yeni-sifre-1"}, headers=auth(tok)).status_code == 400
    assert client.post("/v1/me/password", json={"oldPassword": "yanlis", "newPassword": "yeni-sifre-1"}, headers=auth(tok)).status_code == 429
    assert client.post("/v1/me/password", json={"oldPassword": PW, "newPassword": "yeni-sifre-1"}, headers=auth(tok)).status_code == 429
    pw_fail.reset()


def test_old_tokens_without_version_still_work_until_the_password_changes():
    tok, u = new_user()
    legacy = jwt.encode({"sub": u["id"], "exp": 4102444800}, SECRET, algorithm=ALGO)  # Dilim 4 öncesi biçim (tv ve typ yok)
    assert client.get("/v1/me", headers=auth(legacy)).status_code == 200
    assert client.get("/v1/me", headers=auth(make_token(u["id"]))).status_code == 200
    client.post("/v1/me/password", json={"oldPassword": PW, "newPassword": "yeni-sifre-1"}, headers=auth(tok))
    assert client.get("/v1/me", headers=auth(legacy)).status_code == 401


# ------------------------------------------------------------------ liste / arama / ayrıntı
def test_user_list_search_filter_and_pagination():
    h, _ = admin_login()
    tag = "Arama" + phone()[-5:]
    _, a = new_user("courier", name=f"{tag} Ayşe", username=f"ay_{tag.lower()}"[:20])
    _, b = new_user("employer", name=f"{tag} Burak")
    _, c = new_user("sponsor", name=f"{tag} Can", email=f"{tag.lower()}@ornek.com")
    r = client.get(f"/v1/admin/users?q={tag}", headers=h).json()
    assert r["total"] == 3 and {x["id"] for x in r["items"]} == {a["id"], b["id"], c["id"]}
    assert client.get(f"/v1/admin/users?q={tag}&role=employer", headers=h).json()["items"][0]["id"] == b["id"]
    assert client.get(f"/v1/admin/users?q={a['phone'][-7:]}", headers=h).json()["items"][0]["id"] == a["id"]  # telefonun bir parçası
    assert client.get(f"/v1/admin/users?q={c['email'][:8]}", headers=h).json()["items"][0]["id"] == c["id"]
    assert client.get(f"/v1/admin/users?q={b['id'][:10]}", headers=h).json()["items"][0]["id"] == b["id"]  # kimlik başı
    p1 = client.get(f"/v1/admin/users?q={tag}&limit=2&sort=name", headers=h).json()
    p2 = client.get(f"/v1/admin/users?q={tag}&limit=2&offset=2&sort=name", headers=h).json()
    assert p1["total"] == p2["total"] == 3 and len(p1["items"]) == 2 and len(p2["items"]) == 1
    assert client.get(f"/v1/admin/users?q={tag}&banned=1", headers=h).json()["total"] == 0
    # % ve _ özel karakter değildir
    assert client.get("/v1/admin/users?q=%25", headers=h).json()["total"] == 0


def test_user_detail_shows_what_admin_needs_and_never_secrets():
    h, _ = admin_login()
    tok, u = new_user("employer", name="Ayrıntı İşletme")
    client.post("/v1/me/consent", json={"version": 3, "voice": True, "kapi": True}, headers=auth(tok))
    client.post("/v1/jobs", json={"title": "Test ilanı", "lat": 36.5, "lon": 32.0, "packageCount": 2}, headers=auth(tok))
    client.post("/v1/devices", json={"deviceId": "cihaz-" + u["id"][:10], "platform": "android"}, headers=auth(tok))
    d = client.get(f"/v1/admin/users/{u['id']}", headers=h).json()
    assert d["name"] == "Ayrıntı İşletme" and d["role"] == "employer" and d["phone"] == u["phone"]
    assert d["consent"]["voice"] is True and d["consent"]["kapi"] is True and d["consentCount"] >= 1
    assert d["jobs"][0]["title"] == "Test ilanı" and d["devices"][0]["platform"] == "android"
    assert {e["kind"] for e in d["events"]} >= {"user.register", "job.create"}
    assert "pbkdf2" not in str(d) and "password_hash" not in str(d) and "passwordHash" not in str(d)
    assert client.get("/v1/admin/users/yok", headers=h).status_code == 404


# ------------------------------------------------------------------ düzenleme
def test_admin_can_edit_account_fields_with_checks_and_masked_audit():
    h, name = admin_login()
    tok, u = new_user("courier", name="Eski Ad")
    _, other = new_user("courier", name="Başka", username="baska_kisi")
    new_phone = phone()
    r = client.patch(f"/v1/admin/users/{u['id']}", json={"name": "Yeni Ad", "phone": new_phone, "username": "Yeni_Kullanici", "email": "Yeni@Ornek.com", "role": "employer"}, headers=h)
    assert r.status_code == 200
    got = r.json()
    assert (got["name"], got["phone"], got["username"], got["email"], got["role"]) == ("Yeni Ad", new_phone, "yeni_kullanici", "yeni@ornek.com", "employer")
    assert client.get("/v1/me", headers=auth(tok)).json()["name"] == "Yeni Ad"  # oturum açık kalır
    # çakışmalar ve hatalar
    assert client.patch(f"/v1/admin/users/{u['id']}", json={"phone": other["phone"]}, headers=h).status_code == 400
    assert client.patch(f"/v1/admin/users/{u['id']}", json={"username": "baska_kisi"}, headers=h).status_code == 400
    assert client.patch(f"/v1/admin/users/{u['id']}", json={"role": "yonetici"}, headers=h).status_code == 400
    assert client.patch(f"/v1/admin/users/{u['id']}", json={"name": "   "}, headers=h).status_code == 400
    assert client.patch(f"/v1/admin/users/{u['id']}", json={"phone": "123"}, headers=h).status_code == 400
    assert client.patch("/v1/admin/users/yok", json={"name": "x"}, headers=h).status_code == 404
    # gönderilmeyen alana dokunulmaz; kullanıcı adı "" olursa kalkar
    again = client.patch(f"/v1/admin/users/{u['id']}", json={"username": ""}, headers=h).json()
    assert again["username"] is None and again["name"] == "Yeni Ad"
    # işlem günlüğü: telefon / e-posta maskeli
    audit = [x for x in client.get("/v1/admin/audit", headers=h).json()["items"] if x["action"] == "user_edit" and x["target"] == u["id"]]
    assert audit and new_phone not in str(audit) and u["phone"] not in str(audit) and "yeni@ornek.com" not in str(audit)
    assert "***" in audit[-1]["detail"]
    ev = client.get(f"/v1/admin/events?user_id={u['id']}&kind=user.edited", headers=h).json()["items"]
    assert ev and ev[0]["meta"]["by"] == name


# ------------------------------------------------------------------ men
def test_ban_blocks_login_and_every_request_shows_reason_and_hides_jobs():
    h, _ = admin_login()
    tok, u = new_user("employer", name="Kural Dışı")
    job = client.post("/v1/jobs", json={"title": "Şüpheli ilan", "lat": 36.5, "lon": 32.0, "packageCount": 1}, headers=auth(tok)).json()
    viewer, _ = new_user("courier")
    assert any(j["id"] == job["id"] for j in client.get("/v1/jobs/open", headers=auth(viewer)).json())
    assert client.post(f"/v1/admin/users/{u['id']}/ban", json={"reason": ""}, headers=h).status_code == 400  # neden şart
    r = client.post(f"/v1/admin/users/{u['id']}/ban", json={"reason": "Sahte ilan verdi"}, headers=h)
    assert r.status_code == 200 and r.json()["banned"] is True and r.json()["bannedReason"] == "Sahte ilan verdi"
    # açık oturum: 403 + kod + neden
    me = client.get("/v1/me", headers=auth(tok))
    assert me.status_code == 403 and me.json()["code"] == "banned" and "Sahte ilan verdi" in me.json()["detail"] and me.json()["reason"] == "Sahte ilan verdi"
    for path in ("/v1/jobs/mine", "/v1/doors/stats", "/v1/handoffs?refs=a"):
        assert client.get(path, headers=auth(tok)).status_code == 403
    assert client.post("/v1/relay", json={"to": [viewer and "x"], "body": "merhaba"}, headers=auth(tok)).status_code == 403
    # giriş: doğru şifrede 403 (neden ile), yanlış şifrede 401 (hesabın durumu açıklanmaz)
    ok_pw = login(u)
    assert ok_pw.status_code == 403 and ok_pw.json()["code"] == "banned"
    assert login(u, "yanlis-sifre").status_code == 401
    # ilan listeden kalktı
    assert not any(j["id"] == job["id"] for j in client.get("/v1/jobs/open", headers=auth(viewer)).json())
    with SessionLocal() as db:
        assert db.get(Job, job["id"]).status == "closed"
    # liste süzgeci
    assert client.get(f"/v1/admin/users?q={u['phone'][-8:]}&banned=1", headers=h).json()["total"] == 1
    # askıyı kaldır: giriş döner (eski oturum kapalı kalır, yeniden giriş gerekir)
    assert client.post(f"/v1/admin/users/{u['id']}/unban", headers=h).json()["banned"] is False
    assert client.get("/v1/me", headers=auth(tok)).status_code == 401
    again = login(u)
    assert again.status_code == 200 and client.get("/v1/me", headers=auth(again.json()["token"])).status_code == 200
    kinds = [e["kind"] for e in client.get(f"/v1/admin/events?user_id={u['id']}", headers=h).json()["items"]]
    assert "user.banned" in kinds and "user.unbanned" in kinds


def test_ban_works_for_every_account_type():
    h, _ = admin_login()
    for role in ("courier", "employer", "sponsor"):
        tok, u = new_user(role)
        assert client.post(f"/v1/admin/users/{u['id']}/ban", json={"reason": "Test"}, headers=h).status_code == 200
        assert client.get("/v1/me", headers=auth(tok)).status_code == 403


# ------------------------------------------------------------------ şifre sıfırlama
def test_admin_generates_a_temporary_password_and_user_must_change_it():
    h, name = admin_login()
    tok, u = new_user("courier", name="Şifresini Unuttu")
    r = client.post(f"/v1/admin/users/{u['id']}/password", json={}, headers=h)
    temp = r.json()["temporaryPassword"]
    assert r.status_code == 200 and len(temp) >= 10 and temp != PW
    # eski şifre ve eski oturum geçersiz
    assert login(u).status_code == 401
    assert client.get("/v1/me", headers=auth(tok)).status_code == 401
    # geçici şifreyle giriş: yalnız /v1/me ve şifre değiştirme çalışır
    got = login(u, temp)
    assert got.status_code == 200 and got.json()["user"]["mustChangePassword"] is True
    t2 = got.json()["token"]
    assert client.get("/v1/me", headers=auth(t2)).json()["mustChangePassword"] is True
    blocked = client.get("/v1/jobs/open", headers=auth(t2))
    assert blocked.status_code == 403 and blocked.json()["code"] == "must_change_password"
    assert client.post("/v1/relay", json={"to": ["x"], "body": "a"}, headers=auth(t2)).status_code == 403
    assert client.post("/v1/devices/forget", json={"deviceId": "cihaz-yok-0001"}, headers=auth(t2)).status_code == 200  # çıkış yapabilir
    ch = client.post("/v1/me/password", json={"oldPassword": temp, "newPassword": "kendi-yeni-sifrem"}, headers=auth(t2))
    assert ch.status_code == 200 and ch.json()["user"]["mustChangePassword"] is False
    assert client.get("/v1/jobs/open", headers=auth(ch.json()["token"])).status_code == 200
    # geçici şifre yalnız o yanıtta görünür: sunucuda ve günlükte yok
    blob = str(client.get("/v1/admin/audit", headers=h).json()) + str(client.get(f"/v1/admin/users/{u['id']}", headers=h).json())
    assert temp not in blob
    with SessionLocal() as db:
        assert temp not in db.get(User, u["id"]).password_hash


def test_admin_can_set_a_chosen_password_and_it_is_not_echoed():
    h, _ = admin_login()
    _, u = new_user()
    assert client.post(f"/v1/admin/users/{u['id']}/password", json={"password": "123"}, headers=h).status_code == 400
    r = client.post(f"/v1/admin/users/{u['id']}/password", json={"password": "belirledigim-sifre"}, headers=h)
    assert r.status_code == 200 and r.json()["temporaryPassword"] is None
    assert login(u, "belirledigim-sifre").json()["user"]["mustChangePassword"] is True


def test_force_logout_ends_sessions_but_keeps_the_password():
    h, _ = admin_login()
    tok, u = new_user()
    assert client.post(f"/v1/admin/users/{u['id']}/logout", headers=h).status_code == 200
    assert client.get("/v1/me", headers=auth(tok)).status_code == 401
    assert login(u).status_code == 200  # şifre değişmedi


# ------------------------------------------------------------------ etkinlik akışı
def test_activity_feed_records_actions_without_content_and_pages_by_time():
    h, _ = admin_login()
    tok, u = new_user("courier", name="Etkinlik Kuryesi")
    _, peer = new_user("employer")
    client.post("/v1/relay", json={"to": [peer["id"]], "body": "GİZLİ MESAJ İÇERİĞİ 0532 111 22 33"}, headers=auth(tok))
    client.post("/v1/me/role", json={"role": "employer"}, headers=auth(tok))
    client.post("/v1/me/profile", json={"name": "Yeni Ad"}, headers=auth(tok))
    client.post("/v1/doors/report", json={"items": []}, headers=auth(tok))
    feed = client.get(f"/v1/admin/events?user_id={u['id']}", headers=h).json()
    kinds = [e["kind"] for e in feed["items"]]
    assert {"user.register", "relay.send", "user.role", "user.profile"} <= set(kinds)
    assert feed["kinds"]["relay.send"] and all(e["userName"] in ("Etkinlik Kuryesi", "Yeni Ad") for e in feed["items"])
    send = next(e for e in feed["items"] if e["kind"] == "relay.send")
    assert send["meta"] == {"n": 1}  # yalnız kaç kişiye gittiği
    blob = str(feed)
    assert "GİZLİ MESAJ" not in blob and "0532" not in blob and peer["id"] not in blob
    # zaman sayfalama
    page1 = client.get(f"/v1/admin/events?user_id={u['id']}&limit=2", headers=h).json()["items"]
    before = page1[-1]["at"]
    page2 = client.get(f"/v1/admin/events?user_id={u['id']}&limit=50&before={before}", headers=h).json()["items"]
    assert page2 and all(e["at"] < before for e in page2)
    assert client.get("/v1/admin/events?before=bozuk", headers=h).status_code == 400
    assert client.get("/v1/admin/events?kind=user.register&limit=5", headers=h).json()["items"][0]["kind"] == "user.register"


def test_login_creates_event_and_updates_last_login():
    h, _ = admin_login()
    _, u = new_user()
    login(u)
    d = client.get(f"/v1/admin/users/{u['id']}", headers=h).json()
    assert d["lastLoginAt"] and any(e["kind"] == "user.login" for e in d["events"])


def test_stats_summarise_the_system():
    h, _ = admin_login()
    new_user("courier")
    s = client.get("/v1/admin/stats", headers=h).json()
    assert s["users"] >= 1 and set(s["byRole"]) == {"courier", "employer", "sponsor"} and s["admins"] >= 1
    assert {"banned", "newUsers7d", "logins24h", "openJobs", "activeHandoffs", "doorPoints", "reviews", "reviewsHidden"} <= set(s)
