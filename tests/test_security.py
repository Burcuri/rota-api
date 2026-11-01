"""1.20.0 — hesap güvenliği (kullanıcı isteği 29 Ekim 2026; iş listesi 7. madde): giriş kilidi (hesap + girişte yazılan bilgi + ağ),
şifremi unuttum / kodla yeni şifre (deneme modunda kod yönetim panelinde), e-posta ve telefon doğrulama, telefon değiştirme, doğrulanmış
e-postayla giriş, diğer cihazlardan çıkış, hesap silme (anonimleştirme, ödül talepleri, kara liste devri), yönetim › kodlar / kilit aç,
/hesap-sil sayfası, Brevo e-posta (ücretsiz plan; Render ücretsizde SMTP kapalı)."""
import itertools
import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import notify, pushes, security as sec
from app.db import SessionLocal
from app.main import app
from app.models import (
    AccountDoc, AdBooking, AdminAudit, AuthCode, AuthThrottle, CoinEntry, ConsentLog, CourierProfile, Device, GuideBackup, MapBlock,
    Notice, Nudge, PointEntry, Review, Reward, RewardClaim, User, UserMark, UserPush, now, uid,
)

from helpers import admin_login
from test_auth_consent import auth, phone, reg

client = TestClient(app)
_seq = itertools.count(1)


class Box:
    """SMS / e-posta servisinin yerine: açık / kapalı + gönderilenleri yakalar."""

    def __init__(self, sms=False, email=False):
        self.sms, self.email = sms, email
        self.smss, self.mails = [], []

    def sms_enabled(self):
        return self.sms

    def email_enabled(self):
        return self.email

    def send_sms(self, to, text):
        self.smss.append((to, text))
        return True

    def send_email(self, to, subject, text):
        self.mails.append((to, subject, text))
        return True


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    box = Box()
    notify.set_backend(box)
    sent = []
    monkeypatch.setattr(pushes, "EXPO_TRANSPORT", [lambda url, payload: sent.extend(payload) or {"data": [{"status": "ok", "id": "t"} for _ in payload]}])
    monkeypatch.setattr(pushes, "SYNC", [True])
    pushes.reset_memory()
    yield box
    notify.set_backend(None)
    pushes.reset_memory()


def user(role="courier", **kw):
    r = reg(role=role, name=kw.pop("name", f"Güvenlik {role} {next(_seq)}"), **kw)
    assert r.status_code == 200, r.text
    j = r.json()
    return j["token"], j["user"]


def login(ident, pw="sifre123"):
    return client.post("/v1/auth/login", json={"login": ident, "password": pw})


def db_run(fn):
    db = SessionLocal(expire_on_commit=False)  # dönen satırlar oturum kapanınca da okunabilsin
    try:
        out = fn(db)
        db.commit()
        return out
    finally:
        db.close()


def get_user(i):
    """Kullanıcı satırının o anki değerleri (oturum kapanınca da okunabilsin)."""
    from types import SimpleNamespace

    def f(db):
        u = db.get(User, i)
        return SimpleNamespace(**{c.name: getattr(u, c.name) for c in User.__table__.columns})
    return db_run(f)


def latest_code(user_id, purpose):
    """Deneme modunda kod kişiye ulaşmaz: yönetim panelinden okunur (bu test doğrudan yönetim ucunu çağırır)."""
    head, _ = admin_login()
    items = client.get(f"/v1/admin/codes?user_id={user_id}", headers=head).json()["items"]
    row = next(x for x in items if x["purpose"] == purpose and x["status"] == "live")
    r = client.post(f"/v1/admin/codes/{row['id']}/reveal", headers=head)
    assert r.status_code == 200, r.text
    return r.json()["code"], row


def later(monkeypatch, minutes):
    t = now() + timedelta(minutes=minutes)
    monkeypatch.setattr(sec, "now", lambda: t)


def push_row(user_id, dev):
    def f(db):
        db.add(UserPush(user_id=user_id, kind="expo", token=f"ExponentPushToken[{uuid.uuid4().hex[:22]}]", platform="android", device_id=dev))
    db_run(f)


def push_count(user_id):
    return db_run(lambda db: db.query(UserPush).filter(UserPush.user_id == user_id).count())


# ================================================================== giriş kilidi
def test_five_wrong_passwords_lock_even_the_right_one_and_owner_is_told():
    tok, u = user()
    for i in range(5):
        r = login(u["phone"], "yanlis-" + str(i))
        assert r.status_code == 401, r.text
    r = login(u["phone"])  # doğru şifre de kabul edilmez
    assert r.status_code == 429 and r.json()["code"] == "locked" and 800 <= r.json()["retryAfter"] <= 900
    assert "15 dakika" in r.json()["detail"] and "Şifremi unuttum" in r.json()["detail"] and r.headers.get("retry-after")
    notes = db_run(lambda db: [n.text for n in db.query(Notice).filter(Notice.user_id == u["id"]).all()])
    assert any("yanlış şifre" in t for t in notes), notes
    assert db_run(lambda db: db.query(AuthThrottle).filter(AuthThrottle.key == "u:" + u["id"]).one().fails) == 5


def test_lock_follows_the_account_not_only_what_was_typed(monkeypatch):
    uname = f"kilit{next(_seq)}x"
    tok, u = user(username=uname)
    for _ in range(5):
        assert login(u["phone"], "yanlis").status_code == 401
    assert login(uname).status_code == 429, "kullanıcı adıyla denemek kilidi aşmaz"
    later(monkeypatch, 16)
    r = login(uname)
    assert r.status_code == 200, r.text
    assert db_run(lambda db: db.query(AuthThrottle).filter(AuthThrottle.key == "u:" + u["id"]).count()) == 0, "doğru giriş sayacı sıfırlar"


def test_unknown_login_locks_the_same_way_and_lock_grows(monkeypatch):
    ghost = phone()
    for _ in range(5):
        assert login(ghost, "x").status_code == 401
    r = login(ghost, "x")
    assert r.status_code == 429 and r.json()["code"] == "locked", "kayıtlı olmayan numarada da aynı davranış (numara kayıtlı mı anlaşılmasın)"
    tok, u = user()
    for _ in range(5):
        login(u["phone"], "x")
    later(monkeypatch, 16)
    for _ in range(4):
        assert login(u["phone"], "x").status_code == 401
    r = login(u["phone"], "x")  # 10. yanlış → 1 saat
    assert r.status_code == 401
    r = login(u["phone"])
    assert r.status_code == 429 and 3000 < r.json()["retryAfter"] <= 3600 and "1 saat" in r.json()["detail"]


def test_ip_limit_and_admin_unlock(monkeypatch):
    monkeypatch.setattr(sec.ip_fail, "max_hits", 3)
    for _ in range(3):
        login(phone(), "x")
    r = login(phone(), "x")
    assert r.status_code == 429 and "ağdan" in r.json()["detail"]
    sec.ip_fail.reset()
    monkeypatch.setattr(sec.ip_fail, "max_hits", 50)
    tok, u = user()
    for _ in range(5):
        login(u["phone"], "x")
    head, aname = admin_login()
    info = client.get(f"/v1/admin/users/{u['id']}/security", headers=head).json()
    assert info["lock"]["locked"] and info["lock"]["fails"] == 5
    assert client.post(f"/v1/admin/users/{u['id']}/unlock", headers=head).json()["ok"]
    assert login(u["phone"]).status_code == 200
    assert db_run(lambda db: db.query(AdminAudit).filter(AdminAudit.action == "user_unlock", AdminAudit.target_id == u["id"]).count()) == 1


# ================================================================== şifremi unuttum
def test_forgot_in_test_mode_code_is_in_admin_panel_and_reset_logs_in(_env):
    tok, u = user()
    push_row(u["id"], "dev-eski-telefon")
    r = client.post("/v1/auth/forgot", json={"login": u["phone"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["channel"] == "sms" and j["delivered"] is False and j["provider"] is False and j["hint"].startswith("05") and "***" in j["hint"]
    assert j["expiresIn"] == 900 and not _env.smss
    code, row = latest_code(u["id"], "reset")
    assert row["canReveal"] and row["purposeLabel"] == "Şifremi unuttum" and "***" in row["target"]
    bad = client.post("/v1/auth/reset", json={"login": u["phone"], "code": "000000" if code != "000000" else "111111", "newPassword": "yeniSifre9"})
    assert bad.status_code == 400 and "4 hakkın kaldı" in bad.json()["detail"]
    ok = client.post("/v1/auth/reset", json={"login": u["phone"], "code": code, "newPassword": "yeniSifre9"})
    assert ok.status_code == 200, ok.text
    new_tok = ok.json()["token"]
    assert client.get("/v1/me", headers=auth(tok)).status_code == 401, "eski oturumlar kapandı"
    assert client.get("/v1/me", headers=auth(new_tok)).status_code == 200
    assert push_count(u["id"]) == 0, "oturumu kapanan cihaza bildirim gitmez"
    assert login(u["phone"]).status_code == 401 and login(u["phone"], "yeniSifre9").status_code == 200
    again = client.post("/v1/auth/reset", json={"login": u["phone"], "code": code, "newPassword": "baskaSifre1"})
    assert again.status_code == 400, "kod bir kez kullanılır"
    head, _ = admin_login()
    assert db_run(lambda db: db.query(AdminAudit).filter(AdminAudit.action == "code_reveal", AdminAudit.target_id == u["id"]).count()) >= 1
    assert code not in json.dumps(client.get(f"/v1/admin/codes?user_id={u['id']}", headers=head).json()), "listede kodun kendisi yok"


def test_code_dies_after_five_wrong_tries_and_expires(monkeypatch):
    tok, u = user()
    client.post("/v1/auth/forgot", json={"login": u["phone"]})
    code, _ = latest_code(u["id"], "reset")
    wrong = "123456" if code != "123456" else "654321"
    for _ in range(5):
        client.post("/v1/auth/reset", json={"login": u["phone"], "code": wrong, "newPassword": "yeniSifre9"})
    r = client.post("/v1/auth/reset", json={"login": u["phone"], "code": code, "newPassword": "yeniSifre9"})
    assert r.status_code == 400 and "yeni kod iste" in r.json()["detail"].lower()
    later(monkeypatch, 2)
    tok2, u2 = user()
    client.post("/v1/auth/forgot", json={"login": u2["phone"]})
    code2, _ = latest_code(u2["id"], "reset")
    later(monkeypatch, 20)  # 15 dakika geçti
    r = client.post("/v1/auth/reset", json={"login": u2["phone"], "code": code2, "newPassword": "yeniSifre9"})
    assert r.status_code == 400 and "süresi doldu" in r.json()["detail"]


def test_resend_cooldown_and_hourly_cap(monkeypatch):
    tok, u = user()
    assert client.post("/v1/auth/forgot", json={"login": u["phone"]}).status_code == 200
    r = client.post("/v1/auth/forgot", json={"login": u["phone"]})
    assert r.status_code == 429 and "sn bekle" in r.json()["detail"]
    for i in range(1, 5):
        later(monkeypatch, i * 2)
        assert client.post("/v1/auth/forgot", json={"login": u["phone"]}).status_code == 200
    later(monkeypatch, 11)
    r = client.post("/v1/auth/forgot", json={"login": u["phone"]})
    assert r.status_code == 429 and "bir saat" in r.json()["detail"].lower()
    assert client.post("/v1/auth/forgot", json={"login": "yok_boyle_biri"}).status_code == 404


def test_forgot_prefers_verified_email_when_mail_service_on(_env, monkeypatch):
    _env.email = True
    tok, u = user()
    mail = f"guvenlik{next(_seq)}@ornek.com"
    client.post("/v1/me/profile", json={"email": mail}, headers=auth(tok))
    s = client.post("/v1/me/email/send", headers=auth(tok)).json()
    assert s["channel"] == "email" and s["delivered"] and s["hint"].startswith(mail[0]) and "***@ornek.com" in s["hint"]
    to, subject, text = _env.mails[-1]
    code = next(w for w in text.split() if w.isdigit() and len(w) == 6)
    v = client.post("/v1/me/email/verify", json={"code": code}, headers=auth(tok))
    assert v.status_code == 200 and v.json()["emailVerified"] is True
    later(monkeypatch, 2)
    f = client.post("/v1/auth/forgot", json={"login": mail})  # doğrulanmış e-postayla da bulunur
    assert f.status_code == 200 and f.json()["channel"] == "email" and f.json()["delivered"] is True
    rcode = next(w for w in _env.mails[-1][2].split() if w.isdigit() and len(w) == 6)
    head, _ = admin_login()
    item = next(x for x in client.get(f"/v1/admin/codes?user_id={u['id']}", headers=head).json()["items"] if x["purpose"] == "reset")
    assert item["delivered"] and not item["canReveal"], "kişiye ulaşan kod yönetimde görünmez"
    assert client.post(f"/v1/admin/codes/{item['id']}/reveal", headers=head).status_code == 409
    ok = client.post("/v1/auth/reset", json={"login": mail, "code": rcode, "newPassword": "yeniSifre9"})
    assert ok.status_code == 200 and login(mail, "yeniSifre9").status_code == 200
    import time
    time.sleep(0.3)
    assert any("şifren değiştirildi" in m[1] for m in _env.mails), "şifre değişti bilgisi e-postayla gider"


def test_sms_route_marks_phone_verified_and_unlocks(_env):
    _env.sms = True
    tok, u = user()
    for _ in range(5):
        login(u["phone"], "x")
    assert login(u["phone"]).status_code == 429
    j = client.post("/v1/auth/forgot", json={"login": u["phone"]}).json()
    assert j["channel"] == "sms" and j["delivered"] and _env.smss[-1][0] == u["phone"] and "Rota doğrulama kodun" in _env.smss[-1][1]
    code = next(w for w in _env.smss[-1][1].replace(".", " ").split() if w.isdigit() and len(w) == 6)
    r = client.post("/v1/auth/reset", json={"login": u["phone"], "code": code, "newPassword": "yeniSifre9"})
    assert r.status_code == 200 and r.json()["user"]["phoneVerified"] is True
    assert login(u["phone"], "yeniSifre9").status_code == 200, "kodla yenileyince kilit açılır"


def test_banned_user_gets_ban_reason_and_password_stays():
    tok, u = user()
    head, _ = admin_login()
    client.post(f"/v1/admin/users/{u['id']}/ban", json={"reason": "sahte ilan"}, headers=head)
    client.post("/v1/auth/forgot", json={"login": u["phone"]})
    code, _ = latest_code(u["id"], "reset")
    r = client.post("/v1/auth/reset", json={"login": u["phone"], "code": code, "newPassword": "yeniSifre9"})
    assert r.status_code == 403 and r.json()["code"] == "banned"
    client.post(f"/v1/admin/users/{u['id']}/unban", headers=head)
    assert login(u["phone"]).status_code == 200 and login(u["phone"], "yeniSifre9").status_code == 401


def test_admin_temp_password_is_cleared_by_self_reset():
    tok, u = user()
    head, _ = admin_login()
    temp = client.post(f"/v1/admin/users/{u['id']}/password", json={}, headers=head).json()["temporaryPassword"]
    assert login(u["phone"], temp).json()["user"]["mustChangePassword"] is True
    client.post("/v1/auth/forgot", json={"login": u["phone"]})
    code, _ = latest_code(u["id"], "reset")
    r = client.post("/v1/auth/reset", json={"login": u["phone"], "code": code, "newPassword": "yeniSifre9"})
    assert r.status_code == 200 and r.json()["user"]["mustChangePassword"] is False


# ================================================================== e-posta / telefon doğrulama
def test_email_verify_login_and_change_drops_it(monkeypatch):
    tok, u = user()
    assert client.post("/v1/me/email/send", headers=auth(tok)).status_code == 400, "önce e-posta yazılmalı"
    mail = f"dogrula{next(_seq)}@ornek.com"
    client.post("/v1/me/profile", json={"email": mail}, headers=auth(tok))
    assert login(mail).status_code == 401, "doğrulanmamış e-postayla giriş yok"
    s = client.post("/v1/me/email/send", headers=auth(tok)).json()
    assert s["channel"] == "email" and s["delivered"] is False and s["provider"] is False
    code, _ = latest_code(u["id"], "email")
    v = client.post("/v1/me/email/verify", json={"code": code}, headers=auth(tok))
    assert v.status_code == 200 and v.json()["emailVerified"] is True
    assert client.post("/v1/me/email/send", headers=auth(tok)).json().get("verified") is True
    assert login(mail).status_code == 200 and login(mail.upper()).status_code == 200
    # başka hesap aynı adresi doğrulayamaz
    tok2, u2 = user()
    client.post("/v1/me/profile", json={"email": mail}, headers=auth(tok2))
    client.post("/v1/me/email/send", headers=auth(tok2))
    code2, _ = latest_code(u2["id"], "email")
    assert client.post("/v1/me/email/verify", json={"code": code2}, headers=auth(tok2)).status_code == 409
    # adres değişince doğrulama düşer
    me = client.post("/v1/me/profile", json={"email": "yeni" + mail}, headers=auth(tok)).json()
    assert me["emailVerified"] is False and login(mail).status_code == 401


def test_phone_verify_and_change_with_code():
    tok, u = user()
    assert client.get("/v1/me", headers=auth(tok)).json()["phoneVerified"] is False
    client.post("/v1/me/phone/send", headers=auth(tok))
    code, _ = latest_code(u["id"], "phone")
    assert client.post("/v1/me/phone/verify", json={"code": code}, headers=auth(tok)).json()["phoneVerified"] is True
    tok2, u2 = user()
    assert client.post("/v1/me/phone/change", json={"phone": u2["phone"]}, headers=auth(tok)).status_code == 409
    assert client.post("/v1/me/phone/change", json={"phone": "02124445566"}, headers=auth(tok)).status_code == 400
    assert client.post("/v1/me/phone/change", json={"phone": u["phone"]}, headers=auth(tok)).status_code == 400
    new = phone()
    r = client.post("/v1/me/phone/change", json={"phone": new}, headers=auth(tok))
    assert r.status_code == 200 and r.json()["channel"] == "sms"
    code, row = latest_code(u["id"], "phone_change")
    assert row["target"].startswith(new[:4]), "kod YENİ numaraya"
    other = phone()
    assert client.post("/v1/me/phone/change/verify", json={"phone": other, "code": code}, headers=auth(tok)).status_code == 400
    ok = client.post("/v1/me/phone/change/verify", json={"phone": new, "code": code}, headers=auth(tok))
    assert ok.status_code == 200 and ok.json()["phone"] == new and ok.json()["phoneVerified"] is True
    assert login(new).status_code == 200 and login(u["phone"]).status_code == 401
    head, _ = admin_login()
    edited = client.patch(f"/v1/admin/users/{u['id']}", json={"phone": phone()}, headers=head).json()
    assert edited["phoneVerified"] is False, "yönetim numarayı değiştirince doğrulama düşer"


def test_revoke_other_devices_keeps_this_one():
    tok, u = user()
    for d in ("cihaz-bu-001", "cihaz-oteki-002"):
        assert client.post("/v1/devices", json={"deviceId": d, "platform": "android"}, headers=auth(tok)).status_code == 200
        push_row(u["id"], d)
    r = client.post("/v1/me/sessions/revoke", json={"deviceId": "cihaz-bu-001"}, headers=auth(tok))
    assert r.status_code == 200
    assert client.get("/v1/me", headers=auth(tok)).status_code == 401 and client.get("/v1/me", headers=auth(r.json()["token"])).status_code == 200
    left = db_run(lambda db: [p.device_id for p in db.query(UserPush).filter(UserPush.user_id == u["id"]).all()])
    devs = db_run(lambda db: [d.id for d in db.query(Device).filter(Device.user_id == u["id"]).all()])
    assert left == ["cihaz-bu-001"] and devs == ["cihaz-bu-001"]


def test_password_change_and_admin_logout_drop_push_rows():
    tok, u = user()
    push_row(u["id"], "cihaz-a-0001")
    r = client.post("/v1/me/password", json={"oldPassword": "sifre123", "newPassword": "baska123"}, headers=auth(tok))
    assert r.status_code == 200 and push_count(u["id"]) == 0
    push_row(u["id"], "cihaz-a-0001")
    head, _ = admin_login()
    client.post(f"/v1/admin/users/{u['id']}/logout", headers=head)
    assert push_count(u["id"]) == 0


# ================================================================== hesap silme
def _seed_everything(cid, sid, other):
    def f(db):
        db.add(PointEntry(user_id=cid, delta=40, kind="delivery"))
        db.add(Notice(user_id=cid, kind="x", text="merhaba"))
        db.add(AccountDoc(user_id=cid, section="notif", data="{}", rev=1, edited_ms=1))
        db.add(GuideBackup(user_id=cid, data="[]", n=0, expires_at=now() + timedelta(hours=24)))
        db.add(CoinEntry(user_id=cid, delta=100, kind="test", label="test"))
        db.add(CoinEntry(user_id=cid, delta=50, kind="store", label="mağaza"))
        db.add(CourierProfile(user_id=cid, vehicle="motor"))
        db.add(MapBlock(user_id=cid, blocked_id=other))
        db.add(Nudge(from_id=other, to_id=cid, kind="wave"))
        db.add(Review(source="employer", subject_id=cid, author_id=other, stars=4))
        db.add(Review(source="courier", subject_id=other, author_id=cid, stars=5))
        db.add(ConsentLog(user_id=cid, version=8))
        db.add(AdBooking(owner_id=sid, label="Kampanya", lat=36.5, lon=32.0, days=7, cost=210, ends_at=now() + timedelta(days=7)))
        rw = Reward(id=uid(), owner_id=sid, brand="Akaryakıt", name="Mont", tier=1000, value_tl=120, qty_total=3, qty_left=1)
        db.add(rw)
        db.flush()
        # sponsorun kargo bekleyen talebi (başka kurye) + bu kuryenin talebi
        db.add(RewardClaim(code="ODL" + uuid.uuid4().hex[:6].upper(), reward_id=rw.id, owner_id=sid, courier_id=other, reward_name="Mont", cost=1000,
                           ship_name="Ali", ship_phone="05001112233", ship_address="Kestel Mah.", ship_due_at=now() + timedelta(days=7)))
        db.add(RewardClaim(code="ODL" + uuid.uuid4().hex[:6].upper(), reward_id=rw.id, owner_id=sid, courier_id=cid, reward_name="Mont", cost=1000,
                           ship_name="Burcu", ship_phone="05009998877", ship_address="Oba Mah.", ship_due_at=now() + timedelta(days=7)))
        return rw.id
    return db_run(f)


def test_delete_needs_password_and_confirm():
    tok, u = user()
    assert client.post("/v1/me/delete", json={"password": "yanlis", "confirm": True}, headers=auth(tok)).status_code == 400
    assert client.post("/v1/me/delete", json={"password": "sifre123", "confirm": False}, headers=auth(tok)).status_code == 400
    assert client.get("/v1/me", headers=auth(tok)).status_code == 200


def test_courier_delete_wipes_personal_data_and_cancels_claim():
    tok, u = user("courier", username=f"silinecek{next(_seq)}")
    stok, s = user("sponsor")
    otok, o = user("courier")
    rid = _seed_everything(u["id"], s["id"], o["id"])
    client.post("/v1/devices", json={"deviceId": "silinecek-cihaz-01"}, headers=auth(tok))
    push_row(u["id"], "silinecek-cihaz-01")
    r = client.post("/v1/me/delete", json={"password": "sifre123", "confirm": True}, headers=auth(tok))
    assert r.status_code == 200, r.text
    me = client.get("/v1/me", headers=auth(tok))
    assert me.status_code == 401 and "silindi" in me.json()["detail"]
    assert login(u["phone"]).status_code == 401 and login(u["username"]).status_code == 401
    row = get_user(u["id"])
    assert row.deleted_at and row.name == "Silinmiş üye" and row.phone != u["phone"] and row.username is None and row.email is None
    assert row.phone_hash is None and row.public_no == u["userNo"], "kullanıcı no saklanır ama yeniden verilmez; kara listede değilse numara özeti yok"
    def counts(db):
        q = lambda m, c: db.query(m).filter(c == u["id"]).count()
        return {
            "points": q(PointEntry, PointEntry.user_id), "notice": q(Notice, Notice.user_id), "docs": q(AccountDoc, AccountDoc.user_id),
            "guide": q(GuideBackup, GuideBackup.user_id), "push": q(UserPush, UserPush.user_id), "dev": q(Device, Device.user_id),
            "vehicle": q(CourierProfile, CourierProfile.user_id), "blocks": db.query(MapBlock).filter(MapBlock.user_id == u["id"]).count(),
            "nudges": db.query(Nudge).filter(Nudge.to_id == u["id"]).count(), "about": q(Review, Review.subject_id),
            "wrote": q(Review, Review.author_id), "coins": [c.kind for c in db.query(CoinEntry).filter(CoinEntry.user_id == u["id"]).all()],
            "consent": q(ConsentLog, ConsentLog.user_id),
        }
    c = db_run(counts)
    assert c == {"points": 0, "notice": 0, "docs": 0, "guide": 0, "push": 0, "dev": 0, "vehicle": 0, "blocks": 0, "nudges": 0, "about": 0,
                 "wrote": 0, "coins": ["store"], "consent": 1}, c
    claim = db_run(lambda db: db.query(RewardClaim).filter(RewardClaim.courier_id == u["id"]).one())
    assert claim.status == "cancelled" and claim.close_reason == "courier_deleted" and claim.ship_address == "" and claim.ship_phone == ""
    assert db_run(lambda db: db.get(Reward, rid).qty_left) == 2, "stok geri döndü"
    assert db_run(lambda db: db.query(Notice).filter(Notice.user_id == s["id"], Notice.kind == "reward.cancelled").count()) == 1
    # silinen hesaba mesaj gitmez
    m = client.post("/v1/relay", json={"to": [u["id"]], "body": "{}"}, headers=auth(otok))
    assert m.status_code == 404 or u["id"] in m.json().get("missing", [])
    head, _ = admin_login()
    lst = client.get("/v1/admin/users?banned=del", headers=head).json()["items"]
    assert any(x["id"] == u["id"] and x["deleted"] for x in lst)
    assert client.patch(f"/v1/admin/users/{u['id']}", json={"name": "x"}, headers=head).status_code == 409


def test_sponsor_delete_refunds_waiting_courier_and_removes_rewards():
    tok, u = user("courier")
    stok, s = user("sponsor")
    otok, o = user("courier")
    rid = _seed_everything(u["id"], s["id"], o["id"])
    assert client.post("/v1/me/delete", json={"password": "sifre123", "confirm": True}, headers=auth(stok)).status_code == 200
    assert db_run(lambda db: db.get(Reward, rid).status) == "removed"
    waiting = db_run(lambda db: db.query(RewardClaim).filter(RewardClaim.courier_id == o["id"]).one())
    assert waiting.status == "cancelled" and waiting.close_reason == "owner_deleted" and waiting.ship_address == ""
    refund = db_run(lambda db: db.query(PointEntry).filter(PointEntry.user_id == o["id"], PointEntry.kind == "refund").one())
    assert refund.delta == 1000
    assert db_run(lambda db: db.query(Notice).filter(Notice.user_id == o["id"], Notice.kind == "reward.cancelled").count()) == 1
    assert db_run(lambda db: db.query(AdBooking).filter(AdBooking.owner_id == s["id"]).count()) == 0


def test_blacklisted_account_deleted_then_same_phone_inherits_blacklist():
    tok, u = user("sponsor")
    head, _ = admin_login()
    add = client.post("/v1/admin/blacklist", json={"code": "SPN-" + u["userNo"], "note": "Sahte ürün şikâyeti"}, headers=head)
    assert add.status_code == 200, add.text
    assert client.post("/v1/me/delete", json={"password": "sifre123", "confirm": True}, headers=auth(tok)).status_code == 200
    old = get_user(u["id"])
    assert old.phone_hash and db_run(lambda db: db.get(UserMark, u["id"]).blacklisted)
    r = client.post("/v1/auth/register", json={"phone": u["phone"], "password": "sifre123", "name": "Yeni", "role": "sponsor"})
    assert r.status_code == 200, r.text
    m = db_run(lambda db: db.get(UserMark, r.json()["user"]["id"]))
    assert m is not None and m.blacklisted and "Silinen hesap" in m.note and "Sahte ürün şikâyeti" in m.note and m.listed_by == "sistem"
    # kara listede olmayan silinen numarayla açılan hesap temiz
    tok2, u2 = user()
    client.post("/v1/me/delete", json={"password": "sifre123", "confirm": True}, headers=auth(tok2))
    r2 = client.post("/v1/auth/register", json={"phone": u2["phone"], "password": "sifre123", "name": "Temiz"})
    assert r2.status_code == 200 and db_run(lambda db: db.get(UserMark, r2.json()["user"]["id"])) is None


def test_delete_page_and_caps_and_user_flags(_env):
    r = client.get("/hesap-sil")
    assert r.status_code == 200 and "Hesabını sil" in r.text and "script-src 'self'" in r.headers.get("content-security-policy", "")
    assert r.headers.get("x-frame-options") == "DENY"
    js = client.get("/static/hesap-sil.js")
    assert js.status_code == 200 and "/v1/me/delete" in js.text
    assert client.get("/v1/auth/caps").json() == {"email": False, "sms": False}
    _env.email = True
    assert client.get("/v1/auth/caps").json()["email"] is True
    tok, u = user()
    assert u["emailVerified"] is False and u["phoneVerified"] is False


def test_brevo_email_sends_over_https(monkeypatch):
    notify.set_backend(None)
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-deneme")
    monkeypatch.setenv("MAIL_FROM", "rota@ornek.com")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    seen = {}

    class Resp:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=0):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        return Resp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    assert notify.email_enabled() is True
    assert notify.send_email("kisi@ornek.com", "Konu", "Kod: 123456\n<b>") is True
    assert seen["url"] == "https://api.brevo.com/v3/smtp/email" and seen["headers"].get("Api-key") == "xkeysib-deneme"
    b = seen["body"]
    assert b["sender"]["email"] == "rota@ornek.com" and b["to"] == [{"email": "kisi@ornek.com"}] and b["textContent"].startswith("Kod: 123456")
    assert "&lt;b&gt;" in b["htmlContent"] and "<br>" in b["htmlContent"], "HTML gövdesinde metin kaçışlanır"
    monkeypatch.delenv("BREVO_API_KEY")
    assert notify.email_enabled() is False


def test_delete_removes_live_tracking_tasks_and_recipient_links():
    from app.models import Follow, FollowPoint, Handoff, HandoffPush, Task

    tok, u = user("courier")
    etok, e = user("employer")

    def f(db):
        fl = Follow(employer_id=e["id"], courier_id=u["id"], status="active", expires_at=now() + timedelta(hours=2), last_lat=36.5, last_lon=32.0, task_no="1234567")
        db.add(fl)
        db.flush()
        db.add(FollowPoint(follow_id=fl.id, lat=36.5, lon=32.0))
        db.add(Task(task_no="1234567", employer_id=e["id"], courier_id=u["id"], ref="L:x", title="Liste", count=3, status="matched"))
        h = Handoff(token=uuid.uuid4().hex[:24], courier_id=u["id"], ref="s1", phone="05001112233", lat=36.5, lon=32.0, expires_at=now() + timedelta(hours=48))
        db.add(h)
        db.flush()
        db.add(HandoffPush(handoff_id=h.id, endpoint="https://fcm.googleapis.com/x", p256dh="k", auth="a", expires_at=now() + timedelta(hours=48)))
    db_run(f)
    r = client.post("/v1/me/delete", json={"password": "sifre123", "confirm": True}, headers=auth(tok))
    assert r.status_code == 200, r.text

    def check(db):
        return (db.query(Follow).filter(Follow.courier_id == u["id"]).count(), db.query(FollowPoint).count() >= 0,
                db.query(Task).filter(Task.courier_id == u["id"]).one().status, db.query(Handoff).filter(Handoff.courier_id == u["id"]).count())
    follows, _, task_status, handoffs = db_run(check)
    assert follows == 0 and task_status == "ended" and handoffs == 0
    # işveren tarafında görev "bitti" görünür (neden: hesap silindi)
    assert db_run(lambda db: db.query(Task).filter(Task.employer_id == e["id"]).one().end_reason) == "account_deleted"


def test_delete_anonymizes_door_votes_and_drops_undelivered_messages():
    """Ortak kapı haritasındaki oyları puanıyla kalır ama kişiye bağlanamaz; gönderdiği ama karşı tarafa ulaşmamış mesajlar silinir;
    başkalarının birbirine gönderdiği mesajlara dokunulmaz."""
    from app.doors import reporter_code
    from app.models import DoorPoint, DoorReport, RelayDelivery, RelayMsg

    tok, u = user("courier")
    _, e = user("employer")
    _, z = user("courier")

    def f(db):
        pt = DoorPoint(district_k="alanya", neigh_k="kestel", street_k="barbaros", door_k="12", door_n=12, lat=36.5, lon=32.0, score=2, confirms=2)
        db.add(pt)
        db.flush()
        db.add(DoorReport(point_id=pt.id, reporter=reporter_code(u["id"]), lat=36.5, lon=32.0))
        db.add(DoorReport(point_id=pt.id, reporter=reporter_code(z["id"]), lat=36.5, lon=32.0))
        mine = RelayMsg(sender_id=u["id"], body='{"t":"x"}', size=9, expires_at=now() + timedelta(days=7))
        other = RelayMsg(sender_id=e["id"], body='{"t":"y"}', size=9, expires_at=now() + timedelta(days=7))
        to_me = RelayMsg(sender_id=e["id"], body='{"t":"z"}', size=9, expires_at=now() + timedelta(days=7))
        db.add_all([mine, other, to_me])
        db.flush()
        db.add(RelayDelivery(msg_id=mine.id, user_id=e["id"]))
        db.add(RelayDelivery(msg_id=other.id, user_id=z["id"]))
        db.add(RelayDelivery(msg_id=to_me.id, user_id=u["id"]))
        return pt.id, mine.id, other.id
    pid, mine_id, other_id = db_run(f)
    r = client.post("/v1/me/delete", json={"password": "sifre123", "confirm": True}, headers=auth(tok))
    assert r.status_code == 200, r.text

    def check(db):
        reps = db.query(DoorReport).filter(DoorReport.point_id == pid).all()
        return (sorted(x.reporter.startswith("x") for x in reps), any(x.reporter == reporter_code(u["id"]) for x in reps),
                any(x.reporter == reporter_code(z["id"]) for x in reps), db.get(DoorPoint, pid).score,
                db.get(RelayMsg, mine_id) is None, db.query(RelayDelivery).filter(RelayDelivery.msg_id == mine_id).count(),
                db.get(RelayMsg, other_id) is not None, db.query(RelayDelivery).filter(RelayDelivery.user_id == u["id"]).count())
    anon, still_mine, z_kept, score, mine_gone, mine_dl, other_kept, my_inbox = db_run(check)
    assert anon == [False, True] and not still_mine and z_kept and score == 2  # oy ve puan kaldı, kişiye bağ koptu; başkasınınki aynı
    assert mine_gone and mine_dl == 0 and other_kept and my_inbox == 0
