"""1.19.0 — kurye ve işletme telefonuna bildirim (kullanıcı isteği 29 Ekim 2026). WhatsApp ücretli olduğu için ücretsiz yol: Expo bildirim
servisi (telefon) + Web Push (web ikizi). Denenenler: cihaz kaydı / silme / çıkış, metinde kişisel bilgi olmaması, tercihler ve sessiz zaman
(resmî tatil dahil), art arda gelenlerin birleşmesi, askıdaki hesap, Expo bilet ve sonuç denetimi, olay kancaları (mesaj, adres listesi,
canlı takip, alıcı onayı / pini / teslim, sistem duyurusu, selam, yeni ilan), deneme bildirimi, web ikizi hizmet çalışanı."""
import itertools
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient

from app import pushes, webpush
from app.db import SessionLocal
from app.main import app, handoff_router, push_router, tasks_router
from app.models import Handoff, Notice, User, UserPush, uid

from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)
_seq = itertools.count(1)
TR = timezone(timedelta(hours=3))


class Expo:
    """Expo bildirim servisinin yerine: gönderilenleri yakalar, istenen biletleri döner."""

    def __init__(self):
        self.sent, self.receipt_calls = [], []
        self.ticket = lambda msg: {"status": "ok", "id": "tk-" + uuid.uuid4().hex[:8]}
        self.receipts = {}

    def __call__(self, url, payload):
        if url == pushes.EXPO_RECEIPTS_URL:
            self.receipt_calls.append(payload)
            return {"data": {i: self.receipts.get(i, {"status": "ok"}) for i in payload["ids"]}}
        self.sent.extend(payload)
        return {"data": [self.ticket(m) for m in payload]}

    def to(self, token):
        return [m for m in self.sent if m["to"] == token]


class Wire:
    def __init__(self):
        self.calls = []

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        return 201


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    expo, wire = Expo(), Wire()
    monkeypatch.setattr(pushes, "EXPO_TRANSPORT", [expo])
    monkeypatch.setattr(pushes, "SYNC", [True])
    monkeypatch.setattr(webpush, "TRANSPORT", [wire])
    monkeypatch.setattr(webpush, "SYNC", [True])
    pushes.reset_memory()
    for lim in (*push_router.limiters, *tasks_router.limiters, *handoff_router.limiters):
        lim.reset()
    yield expo, wire
    pushes.reset_memory()


def user(role="courier", name=None):
    r = reg(role=role, name=name or f"Bildirim {role} {next(_seq)}").json()
    return r["token"], r["user"]["id"]


def token():
    return f"ExponentPushToken[{uuid.uuid4().hex[:22]}]"


def device():
    return "android-" + uuid.uuid4().hex[:16]


def on(tok, t=None, dev=None, platform="android"):
    t = t or token()
    r = client.post("/v1/me/push", json={"kind": "expo", "token": t, "platform": platform, "deviceId": dev or device()}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return t


def rows(user_id):
    db = SessionLocal()
    try:
        return db.query(UserPush).filter(UserPush.user_id == user_id).all()
    finally:
        db.close()


def msg(tok, to):
    r = client.post("/v1/relay", json={"to": [to], "body": json.dumps({"text": "gizli içerik Barbaros 227"}), "deviceId": device()}, headers=auth(tok))
    assert r.status_code == 200, r.text


def prefs(tok, **p):
    base = {"newJob": True, "accepted": True, "delivery": True, "quietOn": False, "quietFrom": "23:00", "quietTo": "07:00",
            "quietWeekends": False, "quietHolidays": False, "quietDay": -1}
    base.update(p)
    now_ms = client.get("/v1/account", headers=auth(tok)).json()["now"]
    r = client.put("/v1/account/docs/notif", json={"data": base, "editedAt": now_ms}, headers=auth(tok))
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ cihaz kaydı
def test_register_validates_and_moves_between_accounts():
    tok, me = user()
    for bad in ("", "ExponentPushToken[]", "abc", "ExponentPushToken[ab cd]", "https://fcm.googleapis.com/x"):
        assert client.post("/v1/me/push", json={"kind": "expo", "token": bad}, headers=auth(tok)).status_code == 400
    assert client.post("/v1/me/push", json={"kind": "expo", "token": token(), "deviceId": "../x"}, headers=auth(tok)).status_code == 400
    assert client.post("/v1/me/push", json={"kind": "sms", "token": token()}, headers=auth(tok)).status_code == 422
    assert client.post("/v1/me/push", json={"kind": "expo", "token": token()}).status_code == 401
    t = on(tok)
    assert [r.token for r in rows(me)] == [t]
    tok2, other = user()
    on(tok2, t)  # aynı telefonda başka hesaba girildi: bildirim yeni hesaba
    assert rows(me) == [] and [r.token for r in rows(other)] == [t]


def test_same_device_new_token_replaces_old_and_ten_devices_max():
    tok, me = user()
    dev = device()
    on(tok, dev=dev)
    t2 = on(tok, dev=dev)
    assert [r.token for r in rows(me)] == [t2]
    for _ in range(11):
        on(tok)
    assert len(rows(me)) == 10


def test_unregister_and_logout_forget_device_removes_it():
    tok, me = user()
    t, dev = token(), device()
    on(tok, t, dev)
    on(tok)
    assert client.post("/v1/me/push/off", json={"token": t}, headers=auth(tok)).json()["removed"] == 1
    on(tok, t, dev)
    client.post("/v1/devices", json={"deviceId": dev, "platform": "android"}, headers=auth(tok))
    assert client.post("/v1/devices/forget", json={"deviceId": dev}, headers=auth(tok)).status_code == 200
    assert t not in [r.token for r in rows(me)] and len(rows(me)) == 1
    assert client.post("/v1/me/push/off", json={}, headers=auth(tok)).status_code == 400


def test_web_subscription_needs_known_push_service_and_keys():
    tok, me = user("employer")
    k = ec.generate_private_key(ec.SECP256R1())
    keys = {"p256dh": webpush.b64u(k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
            "auth": webpush.b64u(os.urandom(16))}
    bad = client.post("/v1/me/push", json={"kind": "web", "token": "https://evil.example.com/x", "keys": keys}, headers=auth(tok))
    assert bad.status_code == 400
    ok = client.post("/v1/me/push", json={"kind": "web", "token": "https://fcm.googleapis.com/fcm/send/w1", "keys": keys, "deviceId": "web-" + uuid.uuid4().hex[:12]},
                     headers=auth(tok))
    assert ok.status_code == 200 and rows(me)[0].kind == "web" and rows(me)[0].platform == "web"
    assert client.get("/v1/push/key").json()["key"] == webpush.public_key()


# ------------------------------------------------------------------ gönderim kuralları
def test_message_push_has_no_personal_data_and_coalesces(_clean):
    expo, _ = _clean
    atok, a = user("employer", "Ayşe Gizliad")
    btok, b = user("courier")
    ctok, c = user("courier")
    t = on(btok)
    msg(atok, b)
    got = expo.to(t)
    assert len(got) == 1
    m = got[0]
    assert m["title"] == "Rota" and m["body"] == "📩 Yeni mesajınız var" and m["data"] == {"t": "msg", "k": "msg"}
    assert m["sound"] == "default" and m["priority"] == "high" and m["channelId"] == "default" and m["ttl"] > 0
    flat = json.dumps(got, ensure_ascii=False)
    assert "Gizliad" not in flat and "Barbaros" not in flat and a not in flat  # ad, içerik, kimlik yok
    msg(atok, b)  # aynı kişiden 60 sn içinde ikinci mesaj: tek bildirim
    assert len(expo.to(t)) == 1
    msg(ctok, b)  # başka kişiden: yeni bildirim
    assert len(expo.to(t)) == 2


def test_preferences_and_quiet_time(_clean):
    expo, _ = _clean
    atok, a = user("employer")
    btok, b = user("courier")
    t = on(btok)
    prefs(btok, accepted=False)
    msg(atok, b)
    assert expo.to(t) == []
    prefs(btok, accepted=True, quietOn=True, quietFrom="00:00", quietTo="23:59")
    msg(atok, b)
    assert expo.to(t) == []
    assert client.post("/v1/me/push/test", headers=auth(btok)).status_code == 200  # deneme bildirimi sessiz saatte de gider
    assert [m["body"] for m in expo.to(t)] == ["🔔 Bildirimler açık. Bu bir deneme bildirimidir."]


def test_quiet_rules_turkey_time_and_holidays():
    q = pushes.quiet_now
    at = lambda *a: datetime(*a, tzinfo=TR)
    night = {"quietOn": True, "quietFrom": "23:00", "quietTo": "07:00"}
    assert q(night, at(2026, 11, 3, 23, 30)) and q(night, at(2026, 11, 4, 6, 59)) and not q(night, at(2026, 11, 4, 7, 0))
    day = {"quietOn": True, "quietFrom": "12:00", "quietTo": "13:00"}
    assert q(day, at(2026, 11, 3, 12, 30)) and not q(day, at(2026, 11, 3, 13, 30))
    assert q({"quietWeekends": True}, at(2026, 10, 31, 10, 0)) and not q({"quietWeekends": True}, at(2026, 11, 2, 10, 0))  # cumartesi / pazartesi
    assert q({"quietDay": 2}, at(2026, 11, 4, 10, 0)) and not q({"quietDay": 2}, at(2026, 11, 5, 10, 0))  # çarşamba
    hol = {"quietHolidays": True}
    assert q(hol, at(2026, 10, 29, 10, 0)) and not q(hol, at(2026, 10, 28, 12, 59)) and q(hol, at(2026, 10, 28, 13, 0))  # Cumhuriyet + arife
    assert q(hol, at(2027, 3, 9, 9, 0)) and q(hol, at(2027, 5, 19, 9, 0)) and q(hol, at(2026, 5, 26, 15, 0)) and not q(hol, at(2026, 5, 26, 11, 0))
    assert not q(hol, at(2026, 11, 3, 10, 0)) and not q({}, at(2026, 10, 29, 10, 0))
    # UTC ile gelen zaman Türkiye saatine çevrilir (20:30 UTC = 23:30 TSİ)
    assert q(night, datetime(2026, 11, 3, 20, 30, tzinfo=timezone.utc))
    assert pushes.allowed({"delivery": False}, "delivery") is False and pushes.allowed({"delivery": False}, "msg") is True
    assert pushes.allowed({"newJob": False}, "job") is False and pushes.allowed({"quietOn": True, "quietFrom": "00:00", "quietTo": "23:59"}, "test")


def test_banned_user_gets_nothing(_clean):
    expo, _ = _clean
    atok, a = user("employer")
    btok, b = user("courier")
    t = on(btok)
    db = SessionLocal()
    try:
        db.get(User, b).banned = True
        db.commit()
    finally:
        db.close()
    msg(atok, b)
    assert expo.to(t) == []


def test_device_not_registered_ticket_and_receipt_remove_the_token(_clean):
    expo, _ = _clean
    atok, a = user("employer")
    btok, b = user("courier")
    t = on(btok)
    expo.ticket = lambda m: {"status": "error", "message": "x", "details": {"error": "DeviceNotRegistered"}}
    msg(atok, b)
    assert rows(b) == []
    # bilet "ok" ama 15 dk sonraki sonuç "DeviceNotRegistered"
    t2 = on(btok)
    expo.ticket = lambda m: {"status": "ok", "id": "tk-late"}
    ctok, c = user("courier")
    msg(ctok, b)
    expo.receipts["tk-late"] = {"status": "error", "details": {"error": "DeviceNotRegistered"}}
    assert pushes.check_receipts(max_age=0) == 1 and rows(b) == []
    assert expo.receipt_calls and "tk-late" in expo.receipt_calls[0]["ids"]
    assert t2 not in [r.token for r in rows(b)]


def test_web_twin_gets_an_encrypted_browser_push(_clean):
    _, wire = _clean
    atok, a = user("courier")
    etok, e = user("employer")
    k = ec.generate_private_key(ec.SECP256R1())
    p256 = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    secret = os.urandom(16)
    client.post("/v1/me/push", json={"kind": "web", "token": "https://fcm.googleapis.com/fcm/send/isletme1",
                                     "keys": {"p256dh": webpush.b64u(p256), "auth": webpush.b64u(secret)}}, headers=auth(etok))
    msg(atok, e)
    assert len(wire.calls) == 1
    url, body, headers = wire.calls[0]
    assert url.endswith("/isletme1") and headers["Content-Encoding"] == "aes128gcm"
    salt, idlen = body[:16], body[20]
    as_pub, ct = body[21:21 + idlen], body[21 + idlen:]
    shared = k.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = HKDF(hashes.SHA256(), 32, secret, b"WebPush: info\x00" + p256 + as_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    payload = json.loads(AESGCM(cek).decrypt(nonce, ct, None)[:-1])
    assert payload == {"title": "Rota", "body": "📩 Yeni mesajınız var", "url": "/web/?push=msg", "tag": "rota-msg", "t": "msg"}


def test_test_push_needs_a_device_and_is_rate_limited(_clean):
    expo, _ = _clean
    tok, me = user()
    assert client.post("/v1/me/push/test", headers=auth(tok)).status_code == 409
    t = on(tok)
    for _ in range(5):
        assert client.post("/v1/me/push/test", headers=auth(tok)).status_code == 200
    assert client.post("/v1/me/push/test", headers=auth(tok)).status_code == 429
    assert len(expo.to(t)) == 5


# ------------------------------------------------------------------ olaylar
def share(etok, cid):
    r = client.post("/v1/tasks/share", json={"courierId": cid, "ref": f"L:bildirim-{next(_seq)}", "title": "Mahmutlar", "count": 3}, headers=auth(etok))
    assert r.status_code == 200, r.text
    return r.json()["taskNo"]


def test_task_and_follow_events(_clean):
    expo, _ = _clean
    etok, e = user("employer")
    ctok, c = user("courier")
    te, tc = on(etok), on(ctok)
    no = share(etok, c)
    assert [m["data"]["k"] for m in expo.to(tc)] == ["task_shared"]
    msg(etok, c)  # listenin sohbet mesajı hemen arkasından: aynı kişi, 60 sn → tek bildirim
    assert len(expo.to(tc)) == 1
    client.post(f"/v1/tasks/{no}/accept", headers=auth(ctok))
    assert [m["body"] for m in expo.to(te)] == ["✅ Kurye adres listesini kabul etti"]
    pushes.reset_memory()
    fid = client.post("/v1/follow/request", json={"courierId": c}, headers=auth(etok)).json()["id"]
    assert expo.to(tc)[-1]["data"]["k"] == "follow_ask"
    client.post(f"/v1/follow/{fid}/accept", headers=auth(ctok))
    assert expo.to(te)[-1]["data"]["k"] == "follow_ok"
    # reddedilen liste
    etok2, e2 = user("employer")
    t2 = on(etok2)
    no2 = share(etok2, c)
    client.post(f"/v1/tasks/{no2}/decline", headers=auth(ctok))
    assert [m["data"]["k"] for m in expo.to(t2)] == ["task_declined"]


def handoff_body(ref, **kw):
    d = {"ref": ref, "phone": "05321112233", "lat": 36.495, "lon": 32.099, "province": "Antalya", "district": "Bildirim İlçe",
         "neighborhood": "Pin Mah.", "street": "Barbaros Cad.", "door": "5", "send": True}
    d.update(kw)
    return d


def token_of(hid):
    db = SessionLocal()
    try:
        return db.get(Handoff, hid).token
    finally:
        db.close()


def test_handoff_events_reach_courier_and_employer(_clean):
    expo, _ = _clean
    etok, e = user("employer")
    ctok, c = user("courier")
    te, tc = on(etok), on(ctok)
    no = share(etok, c)
    client.post(f"/v1/tasks/{no}/accept", headers=auth(ctok))
    pushes.reset_memory()
    expo.sent.clear()
    h = client.post("/v1/handoffs", json=handoff_body("stop-bildirim-1"), headers=auth(etok)).json()
    t = token_of(h["id"])
    client.post(f"/a/{t}/pin", json={"lat": 36.4951, "lon": 32.0991})
    assert [m["data"]["k"] for m in expo.to(te)] == ["recipient_pin"]  # kurye henüz bağlanmadı: kaydın sahibi işveren
    client.post("/v1/handoffs", json=handoff_body("stop-bildirim-1"), headers=auth(ctok))  # kurye bağlanır
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(ctok))
    client.post(f"/a/{t}/received", json={"checked": True})
    assert [m["body"] for m in expo.to(tc)] == ["✅ Alıcı “Paketimi aldım” dedi"]
    assert expo.to(te)[-1]["body"] == "✅ Bir paket teslim edildi"
    # kurye "Teslim edildi" (alıcı onaylamadan) → işverene teslim haberi
    pushes.reset_memory()
    h2 = client.post("/v1/handoffs", json=handoff_body("stop-bildirim-2"), headers=auth(etok)).json()
    client.post("/v1/handoffs", json=handoff_body("stop-bildirim-2"), headers=auth(ctok))
    n = len(expo.to(te))
    client.post("/v1/handoffs/done", json={"ref": "stop-bildirim-2"}, headers=auth(ctok))
    assert len(expo.to(te)) == n + 1 and expo.to(te)[-1]["data"]["k"] == "delivered"
    assert h2["id"]


def test_delivery_preference_off_stops_delivery_pushes(_clean):
    expo, _ = _clean
    ctok, c = user("courier")
    tc = on(ctok)
    prefs(ctok, delivery=False)
    h = client.post("/v1/handoffs", json=handoff_body("stop-bildirim-3"), headers=auth(ctok)).json()
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(ctok))
    client.post(f"/a/{token_of(h['id'])}/received", json={"checked": True})
    assert expo.to(tc) == []


def test_every_system_notice_becomes_a_push(_clean):
    expo, _ = _clean
    tok, me = user("sponsor")
    t = on(tok)
    h, _ = admin_login()
    r = client.post(f"/v1/admin/users/{me}/coins", json={"delta": 50, "note": "deneme düzeltmesi"}, headers=h)
    assert r.status_code == 200, r.text
    assert [m["body"] for m in expo.to(t)] == ["🔔 Yeni bir bildiriminiz var"]
    flat = json.dumps(expo.to(t), ensure_ascii=False)
    assert "düzeltme" not in flat  # duyurunun metni bildirime yazılmaz
    db = SessionLocal()  # doğrudan eklenen duyuru da (kanca her yerde)
    try:
        db.add(Notice(id=uid(), user_id=me, kind="x", text="y"))
        db.rollback()  # geri alınırsa bildirim gitmez
        pushes.reset_memory()
        db.add(Notice(id=uid(), user_id=me, kind="x", text="y"))
        db.commit()
    finally:
        db.close()
    assert len(expo.to(t)) == 2


def test_map_hello_push(_clean):
    expo, _ = _clean
    atok, a = user("courier")
    btok, b = user("courier")
    t = on(btok)
    for tok, lat in ((atok, 36.54), (btok, 36.542)):
        client.post("/v1/map/visibility", json={"on": True}, headers=auth(tok))
        client.post("/v1/map/ping", json={"lat": lat, "lon": 32.0}, headers=auth(tok))
    assert client.post("/v1/map/nudge", json={"to": b, "kind": "hello"}, headers=auth(atok)).status_code == 200
    assert [m["data"]["k"] for m in expo.to(t)] == ["nudge"]


def test_new_job_reaches_couriers_of_the_same_area(_clean):
    expo, _ = _clean
    etok, e = user("employer")
    client.post("/v1/me/work-area", json={"province": "Antalya", "district": "Alanya", "hoods": ["Mahmutlar"]}, headers=auth(etok))
    same = user("courier")
    hood_ok = user("courier")
    hood_other = user("courier")
    other_district = user("courier")
    no_area = user("courier")
    muted = user("courier")
    areas = {same: ("Antalya", "Alanya", []), hood_ok: ("antalya", "ALANYA", ["Mahmutlar Mah."]), hood_other: ("Antalya", "Alanya", ["Oba"]),
             other_district: ("Antalya", "Gazipaşa", []), muted: ("Antalya", "Alanya", [])}
    toks = {}
    for (tok, cid), (p, d, hs) in areas.items():
        client.post("/v1/me/work-area", json={"province": p, "district": d, "hoods": hs}, headers=auth(tok))
        toks[cid] = on(tok)
    toks[no_area[1]] = on(no_area[0])
    prefs(muted[0], newJob=False)
    r = client.post("/v1/jobs", json={"title": "4 adres", "lat": 36.5, "lon": 32.0, "packageCount": 4, "area": "Mahmutlar"}, headers=auth(etok))
    assert r.status_code == 200, r.text
    got = {cid for cid, t in toks.items() if expo.to(t)}
    assert got == {same[1], hood_ok[1]}
    assert expo.to(toks[same[1]])[0]["body"] == "📢 Çalışma bölgende yeni ilan var"


def test_web_twin_service_worker_route():
    r = client.get("/web/rota-sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"] and r.headers["service-worker-allowed"] == "/web/"
    assert "showNotification" in r.text and "fetch" not in r.text
