"""1.18.0 — alıcıya süreç takibi (kullanıcı isteği 28 Ekim 2026): alıcı sayfasında Hazırlanıyor → Yolda → Teslim edildi çubuğu,
işverenin "📦 Yola çıktı" düğmesi, alıcı izin verirse tarayıcı bildirimi ("Kargonuz yolda", "Kuryeniz kapıda").

Denenenler: şifreleme (RFC 8291 test vektörü), VAPID imzası, bildirim adresi izin listesi (SSRF), aşamalar, yola çıkış (bir kez
bildirim; abonelik yoksa bağlantı / SMS), kuryenin işveren kaydına AÇIK GÖREVLE bağlanması, "Teslim edildi", aboneliğin silinmesi,
ana ekran bilgisi (manifest) ve hizmet çalışanı."""
import itertools
import json
import os
from datetime import timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from fastapi.testclient import TestClient

from app import notify, webpush
from app.db import SessionLocal
from app.main import app, handoff_router, tasks_router
from app.models import Handoff, HandoffPush, now

from test_auth_consent import auth, reg

client = TestClient(app)
_seq = itertools.count(1)
FCM = "https://fcm.googleapis.com/fcm/send/"


class FakeBackend:
    def __init__(self, sms=True, email=False):
        self.sms_on, self.email_on = sms, email
        self.sms, self.mail = [], []

    def sms_enabled(self):
        return self.sms_on

    def email_enabled(self):
        return self.email_on

    def send_sms(self, to, text):
        self.sms.append((to, text))
        return True

    def send_email(self, to, subject, text):
        self.mail.append((to, subject, text))
        return True


class Wire:
    """Bildirim servisinin yerine geçer: gönderilen istekleri yakalar (gerçek ağa çıkılmaz)."""

    def __init__(self, status=201):
        self.calls, self.status = [], status

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        return self.status


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for lim in (*handoff_router.limiters, *tasks_router.limiters):
        lim.reset()
    notify.set_backend(None)
    wire = Wire()
    monkeypatch.setattr(webpush, "TRANSPORT", [wire])
    monkeypatch.setattr(webpush, "SYNC", [True])
    yield wire
    notify.set_backend(None)
    for lim in (*handoff_router.limiters, *tasks_router.limiters):
        lim.reset()


def user(role):
    r = reg(role=role).json()
    return r["token"], r["user"]["id"]


def body(ref=None, **extra):
    n = next(_seq)
    d = {"ref": ref or f"stop-t{n}", "province": "Antalya", "district": f"Takip İlçe {n}", "neighborhood": f"Süreç Mah. {n}",
         "street": "Barbaros Cad.", "door": "227", "phone": "05321112233", "lat": 36.4950, "lon": 32.0990}
    d.update(extra)
    return d


def create(tok, **kw):
    r = client.post("/v1/handoffs", headers=auth(tok), json=body(**kw))
    assert r.status_code == 200, r.text
    return r.json()


def row(hid):
    db = SessionLocal()
    try:
        return db.get(Handoff, hid)
    finally:
        db.close()


def token_of(h):
    return row(h["id"]).token


def state(tok_or_h):
    t = tok_or_h if isinstance(tok_or_h, str) else token_of(tok_or_h)
    r = client.get(f"/a/{t}/state")
    assert r.status_code == 200, r.text
    return r.json()


def browser():
    """Gerçek bir tarayıcı aboneliği gibi: P-256 anahtar çifti + 16 baytlık auth sırrı."""
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth_secret = os.urandom(16)
    return k, {"p256dh": webpush.b64u(pub), "auth": webpush.b64u(auth_secret)}


def subscribe(t, endpoint=None, keys=None):
    k, ks = browser()
    ep = endpoint or FCM + f"abc{next(_seq)}"
    r = client.post(f"/a/{t}/push", json={"endpoint": ep, "keys": keys or ks})
    return r, k, ks, ep


def decrypt(body_bytes, ua_key, keys):
    """Alıcı tarafı (tarayıcının yaptığı): RFC 8291 / 8188 çözümü. Sunucunun şifrelemesinden BAĞIMSIZ yazıldı."""
    salt, rs, idlen = body_bytes[:16], int.from_bytes(body_bytes[16:20], "big"), body_bytes[20]
    as_pub = body_bytes[21:21 + idlen]
    ct = body_bytes[21 + idlen:]
    assert rs == 4096 and idlen == 65 and len(ct) <= rs
    ua_pub = webpush.unb64u(keys["p256dh"])
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = HKDF(hashes.SHA256(), 32, webpush.unb64u(keys["auth"]), b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    assert plain.endswith(b"\x02")
    return json.loads(plain[:-1].decode("utf-8"))


def matched_task(etok, ctok, cid):
    r = client.post("/v1/tasks/share", json={"courierId": cid, "ref": f"L:takip-{next(_seq)}", "title": "Mahmutlar", "count": 3}, headers=auth(etok))
    assert r.status_code == 200, r.text
    no = r.json()["taskNo"]
    assert client.post(f"/v1/tasks/{no}/accept", headers=auth(ctok)).status_code == 200
    return no


# ------------------------------------------------------------------ şifreleme ve imza
def test_encryption_matches_rfc8291_test_vector():
    sender = ec.derive_private_key(int.from_bytes(webpush.unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    out = webpush.encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg",
        salt=webpush.unb64u("DGv6ra1nlYgDCS1FRnbzlw"),
        sender=sender,
    )
    assert webpush.b64u(out) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4"
        "Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_vapid_header_is_a_valid_es256_token_for_the_push_service():
    h = webpush.vapid_header("https://fcm.googleapis.com/fcm/send/xyz")
    assert h.startswith("vapid t=") and ", k=" in h
    tok, k = h[len("vapid t="):].split(", k=")
    assert k == webpush.public_key() and len(webpush.unb64u(k)) == 65
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64u(k))
    claims = jwt.decode(tok, pub, algorithms=["ES256"], audience="https://fcm.googleapis.com")
    assert claims["sub"].startswith(("mailto:", "https://")) and 0 < claims["exp"] - now().timestamp() <= 3600 + 5
    assert jwt.get_unverified_header(tok)["typ"] == "JWT"


def test_vapid_key_is_derived_once_and_can_be_overridden(monkeypatch):
    a = webpush.public_key()
    assert webpush.public_key() == a  # aynı sunucu anahtarı → aynı kimlik (abonelikler geçerli kalır)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", webpush.b64u(b"\x07" * 32))
    webpush.reset_key()
    try:
        assert webpush.public_key() != a
    finally:
        monkeypatch.delenv("VAPID_PRIVATE_KEY")
        webpush.reset_key()
    assert webpush.public_key() == a


@pytest.mark.parametrize("url,ok", [
    ("https://fcm.googleapis.com/fcm/send/abc:def", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/gAAAA", True),
    ("https://web.push.apple.com/QGuQyavXutnMH", True),
    ("https://wns2-par02p.notify.windows.com/w/?token=BQYAAA", True),
    ("http://fcm.googleapis.com/fcm/send/abc", False),  # şifresiz
    ("https://evil.example.com/fcm.googleapis.com", False),
    ("https://fcm.googleapis.com.evil.com/x", False),
    ("https://169.254.169.254/latest/meta-data", False),
    ("https://127.0.0.1:8443/x", False),
    ("https://user:pw@fcm.googleapis.com/x", False),
    ("https://fcm.googleapis.com:8443/x", False),
    ("https://notify.windows.com/x", False),
    ("https://fcm.googleapis.com/a b", False),
    ("https://fcm.googleapis.com/" + "a" * 1100, False),
])
def test_only_known_push_services_are_contacted(url, ok):
    assert webpush.endpoint_ok(url) is ok


def test_test_prefix_is_off_unless_set(monkeypatch):
    assert not webpush.endpoint_ok("http://127.0.0.1:8131/push/x")
    monkeypatch.setenv("PUSH_TEST_ENDPOINT_PREFIX", "http://127.0.0.1:8131/push/")
    assert webpush.endpoint_ok("http://127.0.0.1:8131/push/x")
    assert not webpush.endpoint_ok("http://127.0.0.1:8132/push/x")


def test_subscription_keys_are_checked():
    _, ks = browser()
    assert webpush.keys_ok(ks["p256dh"], ks["auth"])
    assert not webpush.keys_ok(ks["p256dh"], webpush.b64u(b"\x01" * 15))
    assert not webpush.keys_ok(webpush.b64u(b"\x04" + b"\x01" * 64), ks["auth"])  # eğri üzerinde değil
    assert not webpush.keys_ok("??", ks["auth"])


# ------------------------------------------------------------------ aşamalar
def test_employer_record_starts_preparing_courier_record_is_on_the_way():
    etok, _ = user("employer")
    ctok, _ = user("courier")
    he, hc = create(etok), create(ctok)
    assert he["stage"] == "preparing" and he["departedAt"] is None
    assert hc["stage"] == "onway" and hc["departedAt"]
    se, sc = state(he), state(hc)
    assert (se["stage"], se["atDoor"], se["push"]["open"]) == ("preparing", False, True)
    assert sc["stage"] == "onway" and sc["push"]["key"] == webpush.public_key()
    flat = json.dumps(se)
    assert row(he["id"]).employer_id not in flat  # işverenin kimliği alıcıya çıkmaz


def test_depart_moves_to_onway_and_pushes_once(_clean):
    wire = _clean
    etok, _ = user("employer")
    h = create(etok)
    t = token_of(h)
    r, key, ks, ep = subscribe(t)
    assert r.status_code == 200 and r.json()["on"] is True
    assert state(t)["push"]["on"] is True
    d = client.post(f"/v1/handoffs/{h['id']}/depart", headers=auth(etok)).json()
    assert d["stage"] == "onway" and d["departedAt"] and d["notified"] == {"push": 1, "sms": False, "email": False}
    assert "link" not in d and d["pushOn"] is True  # bildirim ulaştı: işverenin bağlantı iletmesine gerek yok
    assert len(wire.calls) == 1
    url, raw, headers = wire.calls[0]
    assert url == ep and headers["Content-Encoding"] == "aes128gcm" and headers["Authorization"].startswith("vapid t=")
    assert int(headers["TTL"]) > 0 and headers["Urgency"] == "high" and len(headers["Topic"]) <= 32
    msg = decrypt(raw, key, ks)
    assert msg == {"title": "Kargonuz yolda", "body": "Kuryeniz paketinizi getiriyor. Takip için dokunun.", "url": f"/a/{t}", "tag": "rota-" + h["id"]}
    # tekrar basılırsa bildirim tekrar gitmez
    d2 = client.post(f"/v1/handoffs/{h['id']}/depart", headers=auth(etok)).json()
    assert d2["notified"]["push"] == 0 and len(wire.calls) == 1
    st = state(t)
    assert st["stage"] == "onway" and st["departedAt"] == d["departedAt"]


def test_depart_without_subscriber_gives_the_link_to_forward():
    etok, _ = user("employer")
    h = create(etok)
    d = client.post(f"/v1/handoffs/{h['id']}/depart", headers=auth(etok)).json()
    assert d["notified"] == {"push": 0, "sms": False, "email": False}
    assert d["link"].endswith("/a/" + token_of(h)) and d["shareText"].startswith("Kargonuz yolda.") and d["link"] in d["shareText"]
    assert row(h["id"]).link_seen is True and row(h["id"]).send_count == 0  # test modunda gönderim hakkı harcanmaz


def test_depart_uses_sms_when_provider_is_on_and_no_subscriber():
    fake = FakeBackend(sms=True)
    notify.set_backend(fake)
    etok, _ = user("employer")
    h = create(etok)
    d = client.post(f"/v1/handoffs/{h['id']}/depart", headers=auth(etok)).json()
    assert d["notified"]["sms"] is True and "link" not in d and d["mode"] == "sms"
    assert len(fake.sms) == 1 and fake.sms[0][1].startswith("Kargonuz yolda.")


def test_only_the_owner_or_its_employer_can_depart():
    etok, _ = user("employer")
    otok, _ = user("employer")
    h = create(etok)
    assert client.post(f"/v1/handoffs/{h['id']}/depart", headers=auth(otok)).status_code == 404
    assert client.post(f"/v1/handoffs/{h['id']}/depart").status_code == 401


def test_arrival_sets_on_the_way_and_pushes_kuryeniz_kapida(_clean):
    wire = _clean
    ctok, _ = user("courier")
    h = create(ctok, employer="")
    t = token_of(h)
    _, key, ks, _ = subscribe(t)
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(ctok))
    st = state(t)
    assert st["stage"] == "onway" and st["atDoor"] is True and st["canConfirm"] is True
    assert decrypt(wire.calls[-1][1], key, ks)["title"] == "Kuryeniz kapıda"
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(ctok))
    assert len(wire.calls) == 1  # ikinci "ulaştım" yeni bildirim göndermez


def test_gone_subscription_is_forgotten(_clean):
    wire = _clean
    wire.status = 410
    etok, _ = user("employer")
    h = create(etok)
    subscribe(token_of(h))
    client.post(f"/v1/handoffs/{h['id']}/depart", headers=auth(etok))
    db = SessionLocal()
    try:
        assert db.query(HandoffPush).filter(HandoffPush.handoff_id == h["id"]).count() == 0
    finally:
        db.close()


# ------------------------------------------------------------------ abonelik kuralları
def test_subscription_rules():
    etok, _ = user("employer")
    h = create(etok)
    t = token_of(h)
    for ep in ("http://fcm.googleapis.com/x/y/z", "https://evil.example.com/push", "https://169.254.169.254/x"):
        assert subscribe(t, endpoint=ep)[0].status_code == 400
    assert subscribe(t, keys={"p256dh": "kisa", "auth": "kisa"})[0].status_code == 400
    eps = [subscribe(t)[3] for _ in range(4)]  # en çok 3: dördüncü en eskisinin yerine geçer
    db = SessionLocal()
    try:
        have = {x.endpoint for x in db.query(HandoffPush).filter(HandoffPush.handoff_id == h["id"]).all()}
    finally:
        db.close()
    assert have == set(eps[1:])
    assert subscribe(t, endpoint=eps[3])[0].status_code == 200  # aynı adres tekrar: güncellenir, çoğalmaz
    r = client.post(f"/a/{t}/push/off", json={"endpoint": eps[3]})
    assert r.status_code == 200 and r.json()["on"] is True  # diğer ikisi duruyor
    for ep in eps[1:3]:
        client.post(f"/a/{t}/push/off", json={"endpoint": ep})
    assert state(t)["push"]["on"] is False


def test_no_subscription_after_delivery_and_received_drops_them():
    ctok, _ = user("courier")
    h = create(ctok)
    t = token_of(h)
    subscribe(t)
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(ctok))
    assert client.post(f"/a/{t}/received", json={"checked": True}).status_code == 200
    st = state(t)
    assert st["stage"] == "delivered" and st["push"] == {"key": webpush.public_key(), "on": False, "open": False}
    assert subscribe(t)[0].status_code == 409


def test_courier_closing_and_purge_drop_subscriptions():
    ctok, _ = user("courier")
    h1, h2 = create(ctok), create(ctok)
    subscribe(token_of(h1))
    subscribe(token_of(h2))
    client.post(f"/v1/handoffs/{h1['id']}/arrived", headers=auth(ctok))
    client.delete(f"/v1/handoffs/{h1['id']}", headers=auth(ctok))
    db = SessionLocal()
    try:
        assert db.query(HandoffPush).filter(HandoffPush.handoff_id == h1["id"]).count() == 0
        r = db.get(Handoff, h2["id"])
        r.expires_at = now() - timedelta(minutes=1)
        db.commit()
        handoff_router.purge(db)
        assert db.get(Handoff, h2["id"]) is None
        assert db.query(HandoffPush).filter(HandoffPush.handoff_id == h2["id"]).count() == 0
    finally:
        db.close()


# ------------------------------------------------------------------ işveren ↔ kurye (aynı bağlantı)
def test_courier_with_open_task_joins_the_employers_record():
    etok, eid = user("employer")
    ctok, cid = user("courier")
    xtok, _ = user("courier")  # görevi olmayan başka kurye
    h = create(etok, ref="stop-ortak-1")
    t = token_of(h)
    # görevi olmayan kurye göremez, bağlanamaz: kendi ayrı kaydını açar
    assert client.get("/v1/handoffs", params={"refs": "stop-ortak-1"}, headers=auth(xtok)).json()["items"] == []
    other = create(xtok, ref="stop-ortak-1")
    assert other["id"] != h["id"] and row(h["id"]).courier_id == eid
    # görevle listeyi alan kurye yoklayınca bağlanır: aynı kayıt, aynı alıcı sayfası
    matched_task(etok, ctok, cid)
    items = client.get("/v1/handoffs", params={"refs": "stop-ortak-1"}, headers=auth(ctok)).json()["items"]
    assert [x["id"] for x in items] == [h["id"]] and row(h["id"]).courier_id == cid
    again = create(ctok, ref="stop-ortak-1")
    assert again["id"] == h["id"]
    # işveren durumu görmeye devam eder
    client.post(f"/v1/handoffs/{h['id']}/depart", headers=auth(etok))
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(ctok))
    ei = client.get("/v1/handoffs", params={"refs": "stop-ortak-1"}, headers=auth(etok)).json()["items"]
    assert ei[0]["id"] == h["id"] and ei[0]["status"] == "arrived" and ei[0]["stage"] == "onway"
    assert state(t)["atDoor"] is True
    # işveren "ulaştım" diyemez (kaydın sahibi artık kurye)
    assert client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(etok)).status_code == 404


def test_courier_record_from_an_employer_list_waits_for_the_employers_departure():
    etok, eid = user("employer")
    ctok, cid = user("courier")
    matched_task(etok, ctok, cid)
    h = create(ctok, ref="stop-listeden-1", employer=eid)
    assert h["stage"] == "preparing" and row(h["id"]).employer_id == eid
    ei = client.get("/v1/handoffs", params={"refs": "stop-listeden-1"}, headers=auth(etok)).json()["items"]
    assert [x["id"] for x in ei] == [h["id"]]
    d = client.post(f"/v1/handoffs/{h['id']}/depart", headers=auth(etok)).json()
    assert d["stage"] == "onway"
    # görev yoksa ipucu yok sayılır: kuryenin kendi kaydı, doğrudan "Yolda"
    otok, oid = user("employer")
    h2 = create(ctok, ref="stop-listeden-2", employer=oid)
    assert h2["stage"] == "onway" and row(h2["id"]).employer_id is None


def test_recipient_confirmation_after_join_goes_to_the_courier():
    etok, _ = user("employer")
    ctok, cid = user("courier")
    h = create(etok, ref="stop-puan-1")
    matched_task(etok, ctok, cid)
    create(ctok, ref="stop-puan-1")  # bağlanır
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(ctok))
    t = token_of(h)
    assert client.post(f"/a/{t}/received", json={"checked": True}).status_code == 200
    rv = client.post(f"/a/{t}/review", json={"stars": 5})
    assert rv.status_code == 200
    db = SessionLocal()
    try:
        from app.models import Review
        assert db.query(Review).filter(Review.handoff_id == h["id"]).first().subject_id == cid
    finally:
        db.close()


def test_employer_record_without_courier_cannot_be_reviewed():
    etok, _ = user("employer")
    h = create(etok)
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(etok))  # kurye bağlanmamış: işveren kendi kaydında
    t = token_of(h)
    st = state(t)
    assert st["review"]["allowed"] is False
    assert client.post(f"/a/{t}/review", json={"stars": 5}).status_code == 409


# ------------------------------------------------------------------ teslim edildi
def test_done_finishes_the_bar_but_recipient_can_still_confirm():
    ctok, _ = user("courier")
    h = create(ctok, ref="stop-bitti-1")
    t = token_of(h)
    subscribe(t)
    r = client.post("/v1/handoffs/done", json={"ref": "stop-bitti-1"}, headers=auth(ctok)).json()
    assert r["found"] is True and r["item"]["stage"] == "delivered" and r["item"]["deliveredAt"]
    st = state(t)
    assert st["stage"] == "delivered" and st["byCourier"] is True and st["canConfirm"] is True and st["push"]["on"] is False
    assert st["review"]["allowed"] is True
    assert client.post(f"/a/{t}/received", json={"checked": True}).status_code == 200  # kurye "ulaştım" demeden de
    st = state(t)
    assert st["received"] is True and st["byCourier"] is False and st["stage"] == "delivered"
    # kaydı olmayan durak: sessizce geçer; işveren "teslim edildi" diyemez
    assert client.post("/v1/handoffs/done", json={"ref": "yok-boyle"}, headers=auth(ctok)).json() == {"ok": True, "found": False}
    etok, _ = user("employer")
    he = create(etok, ref="stop-bitti-2")
    assert client.post("/v1/handoffs/done", json={"ref": "stop-bitti-2"}, headers=auth(etok)).json()["found"] is False
    assert state(he)["stage"] == "preparing"


def test_employer_still_sees_delivered_after_the_courier_closes_the_record():
    etok, _ = user("employer")
    ctok, cid = user("courier")
    h = create(etok, ref="stop-kapanan-1")
    matched_task(etok, ctok, cid)
    create(ctok, ref="stop-kapanan-1")
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(ctok))
    client.post(f"/a/{token_of(h)}/received", json={"checked": True})
    client.delete(f"/v1/handoffs/{h['id']}", headers=auth(ctok))  # kurye teslimi bitirdi: kayıt kapandı
    assert client.get("/v1/handoffs", params={"refs": "stop-kapanan-1"}, headers=auth(ctok)).json()["items"] == []
    ei = client.get("/v1/handoffs", params={"refs": "stop-kapanan-1"}, headers=auth(etok)).json()["items"]
    assert len(ei) == 1 and ei[0]["stage"] == "delivered" and ei[0]["closed"] is True
    assert client.post(f"/v1/handoffs/{h['id']}/depart", headers=auth(etok)).status_code == 404


# ------------------------------------------------------------------ ana ekran (iPhone) ve hizmet çalışanı
def test_manifest_service_worker_and_page_link():
    etok, _ = user("employer")
    t = token_of(create(etok))
    m = client.get(f"/a/{t}/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    j = m.json()
    assert j["start_url"] == f"/a/{t}" and j["scope"] == "/a/" and j["display"] == "standalone" and j["short_name"] == "Kargom"
    for ic in j["icons"]:
        assert client.get(ic["src"]).status_code == 200
    assert client.get("/a/kisa/manifest.webmanifest").status_code == 404
    sw = client.get("/a/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and sw.headers["service-worker-allowed"] == "/a/"
    assert "showNotification" in sw.text and "notificationclick" in sw.text and "fetch" not in sw.text  # sayfa önbelleğe alınmaz
    assert sw.headers["cache-control"] == "no-store" and "script-src 'self'" in sw.headers["content-security-policy"]
    page = client.get(f"/a/{t}").text
    assert f'<link rel="manifest" href="/a/{t}/manifest.webmanifest">' in page and "apple-touch-icon" in page
    assert '<link rel="manifest"' not in client.get("/a/..%2Fx").text
    for f in ("alici-180.png", "alici-badge.png", "alici-sw.js"):
        assert client.get(f"/static/{f}").status_code == 200
