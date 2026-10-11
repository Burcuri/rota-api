"""Teslim onayı ("Paketimi aldım"): bağlantı üretme, test/SMS modu, varış kapısı, alıcı sayfası uçları, 48 saat silme,
alıcı pini → ortak kapı haritası (ağırlık kuralı), gizlilik ve sınırlar."""
import itertools
import json
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import notify
from app.db import SessionLocal
from app.doors import make_router as make_doors_router
from app.handoff import make_router as make_handoff_router
from app.main import app, handoff_router
from app.models import DoorPoint, DoorReport, Handoff, now

from test_auth_consent import auth, reg

client = TestClient(app)
_seq = itertools.count(1)
PHONE = "05321112233"


class FakeBackend:
    """Sağlayıcıları açık gösterir ve gönderilenleri kaydeder."""

    def __init__(self, sms=True, email=True):
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


@pytest.fixture(autouse=True)
def _clean():
    for lim in handoff_router.limiters:
        lim.reset()
    notify.set_backend(None)
    yield
    notify.set_backend(None)
    for lim in handoff_router.limiters:
        lim.reset()


def courier():
    r = reg(role="courier").json()
    return r["token"], r["user"]["id"]


def area():
    n = next(_seq)
    return {"province": "Antalya", "district": f"Teslim İlçe {n}", "neighborhood": f"Onay Mah. {n}"}


def body(ref=None, a=None, **extra):
    a = a or area()
    d = dict(a, ref=ref or f"stop-{next(_seq)}", street="Barbaros Cad.", door="227", site="Güneş Sitesi", phone=PHONE, lat=36.4950, lon=32.0990)
    d.update(extra)
    return d


def create(tok, **kw):
    r = client.post("/v1/handoffs", headers=auth(tok), json=body(**kw))
    assert r.status_code == 200, r.text
    return r.json()


def token_of(h):
    with SessionLocal() as db:
        return db.get(Handoff, h["id"]).token


def row(hid):
    with SessionLocal() as db:
        x = db.get(Handoff, hid)
        if x is not None:
            db.expunge(x)
        return x


# ------------------------------------------------------------------ kurye uçları
def test_requires_login():
    assert client.post("/v1/handoffs", json=body()).status_code == 401
    assert client.get("/v1/handoffs?refs=a").status_code == 401


def test_create_is_idempotent_per_stop_and_private_per_courier():
    tok, _ = courier()
    other, _ = courier()
    a = create(tok, ref="durak-1")
    b = create(tok, ref="durak-1")
    assert a["id"] == b["id"]
    c = create(other, ref="durak-1")
    assert c["id"] != a["id"]  # aynı durak kimliği başka kuryede başka kayıt


def test_test_mode_returns_link_only_when_asked_to_send():
    tok, _ = courier()
    h = create(tok)
    assert "link" not in h and h["providers"] == {"sms": False, "email": False}  # yalnız oluşturma: bağlantı verilmez
    h2 = create(tok, ref="x-send", send=True)
    assert h2["sent"] == {"sms": False, "email": False}
    assert h2["link"].endswith("/a/" + token_of(h2)) and "Paketimi aldım" in h2["shareText"] and h2["link"] in h2["shareText"]
    # Alıcıya giden metin kullanıcının verdiği cümleyle aynıdır
    assert h2["shareText"].startswith("Paketiniz teslim edilene kadar bu mesajı silmeyiniz. Teslim almadan önce bağlantıdan \"Paketimi aldım\" kutusunu işaretleyiniz; işaretlenmeden teslim edilmiş sayılmaz.")
    assert row(h2["id"]).link_seen is True  # kurye gördü


def test_sms_mode_sends_from_server_and_hides_link_from_courier():
    fake = FakeBackend()
    notify.set_backend(fake)
    tok, _ = courier()
    h = create(tok, send=True)
    assert h["sent"]["sms"] is True and "link" not in h and h["mode"] == "sms"
    assert fake.sms and fake.sms[0][0] == PHONE and "Paketimi aldım" in fake.sms[0][1] and token_of(h) in fake.sms[0][1]
    assert row(h["id"]).link_seen is False
    # kurye bağlantıyı sonradan isterse (QR): görür ve kayıt buna göre işaretlenir
    lk = client.get(f"/v1/handoffs/{h['id']}/link", headers=auth(tok))
    assert lk.status_code == 200 and lk.json()["link"].endswith("/a/" + token_of(h))
    assert row(h["id"]).link_seen is True


def test_email_goes_out_when_provider_on():
    fake = FakeBackend(sms=False)
    notify.set_backend(fake)
    tok, _ = courier()
    h = create(tok, send=True, email="alici@example.com", phone="")
    assert h["sent"] == {"sms": False, "email": True} and h["mode"] == "email" and "link" not in h
    assert fake.mail[0][0] == "alici@example.com" and token_of(h) in fake.mail[0][2] and "48" in fake.mail[0][2]


def test_send_limit_is_three():
    tok, _ = courier()
    h = create(tok, ref="lim", send=True)
    for _ in range(2):
        assert client.post(f"/v1/handoffs/{h['id']}/send", headers=auth(tok)).status_code == 200
    assert client.post(f"/v1/handoffs/{h['id']}/send", headers=auth(tok)).status_code == 429


def test_invalid_phone_is_ignored_not_an_error():
    tok, _ = courier()
    h = create(tok, phone="123", email="bozuk")
    assert h["phoneOk"] is False and h["emailOk"] is False


def test_other_courier_cannot_touch_my_handoff():
    tok, _ = courier()
    other, _ = courier()
    h = create(tok, ref="mine", send=True)
    for method, path in (("get", f"/v1/handoffs/{h['id']}/link"), ("post", f"/v1/handoffs/{h['id']}/send"), ("post", f"/v1/handoffs/{h['id']}/arrived")):
        assert getattr(client, method)(path, headers=auth(other)).status_code == 404
    client.delete(f"/v1/handoffs/{h['id']}", headers=auth(other))
    assert row(h["id"]) is not None  # başkasının silme isteği işe yaramaz
    assert client.get("/v1/handoffs?refs=mine", headers=auth(other)).json()["items"] == []


def test_status_poll_lists_requested_stops_only():
    tok, _ = courier()
    create(tok, ref="p1")
    create(tok, ref="p2")
    items = client.get("/v1/handoffs?refs=p1,zzz", headers=auth(tok)).json()["items"]
    assert [i["ref"] for i in items] == ["p1"] and items[0]["status"] == "created"


def test_courier_can_delete_own():
    tok, _ = courier()
    h = create(tok)
    assert client.delete(f"/v1/handoffs/{h['id']}", headers=auth(tok)).json() == {"ok": True}
    assert row(h["id"]) is None


# ------------------------------------------------------------------ varış kapısı + "Paketimi aldım"
def test_package_received_only_after_courier_arrived_and_contact_is_wiped():
    tok, _ = courier()
    h = create(tok, ref="gate", send=True, email="alici@example.com")
    t = token_of(h)
    st = client.get(f"/a/{t}/state").json()
    assert st["status"] == "created" and st["canConfirm"] is False and st["phone"] == PHONE
    assert client.post(f"/a/{t}/received", json={"checked": True}).status_code == 409  # henüz ulaşmadı

    assert client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(tok)).json()["status"] == "arrived"
    st = client.get(f"/a/{t}/state").json()
    assert st["canConfirm"] is True
    assert client.post(f"/a/{t}/received", json={"checked": False}).json()["status"] == "arrived"  # işaret geri alınmaz / kaldırılamaz
    assert client.post(f"/a/{t}/received", json={"checked": True}).json()["status"] == "received"
    assert client.post(f"/a/{t}/received", json={"checked": True}).status_code == 200  # tekrar: sorun yok

    x = row(h["id"])
    assert x.status == "received" and x.received_at is not None
    assert x.phone == "" and x.email == ""  # onaydan sonra telefon / e-posta tutulmaz
    st = client.get(f"/a/{t}/state").json()
    assert st["received"] is True and st["phone"] == "" and st["email"] == ""
    # kurye ekranı
    it = client.get("/v1/handoffs?refs=gate", headers=auth(tok)).json()["items"][0]
    assert it["status"] == "received" and it["receivedAt"]
    # onaydan sonra iletişim güncelleme bilgi yazmaz
    r = client.post(f"/a/{t}/contact", json={"phone": "05329998877"})
    assert r.json()["phone"] == "" and row(h["id"]).phone == ""


def test_arrived_is_idempotent_and_cannot_go_back():
    tok, _ = courier()
    h = create(tok)
    a1 = client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(tok)).json()["arrivedAt"]
    a2 = client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(tok)).json()["arrivedAt"]
    assert a1 == a2


def test_state_shows_no_courier_or_personal_data():
    tok, uid_ = courier()
    h = create(tok, send=True)
    st = client.get(f"/a/{token_of(h)}/state").json()
    assert set(st) == {"status", "canConfirm", "received", "closed", "review", "address", "area", "site", "start", "pin", "phone", "email", "expiresAt"}
    assert st["review"] == {"allowed": False, "done": False, "data": None}  # kurye hedefe ulaşmadan anket açılmaz
    flat = json.dumps(st)
    assert uid_ not in flat and "Test" not in flat  # kuryenin kimliği / adı alıcı sayfasına çıkmaz
    assert st["address"] == "Barbaros Cad. No:227" and st["site"] == "Güneş Sitesi"
    assert "customer" not in flat and "name" not in flat.lower()


def test_unknown_or_malformed_tokens_do_not_reveal_anything():
    for t in ("kisa", "x" * 40, "../etc/passwd", "AAAAAAAAAAAAAAAA"):
        assert client.get(f"/a/{t}/state").status_code == 404
        assert client.post(f"/a/{t}/received", json={"checked": True}).status_code == 404
        assert client.post(f"/a/{t}/pin", json={"lat": 36.5, "lon": 32.1}).status_code == 404
    # sayfa her kod için aynı (kodun var olup olmadığı sayfadan anlaşılmaz)
    assert client.get("/a/AAAAAAAAAAAAAAAA").status_code == 200
    assert client.get("/a/AAAAAAAAAAAAAAAA").text == client.get("/a/BBBBBBBBBBBBBBBB").text


def test_page_and_static_files_served_with_secure_headers():
    r = client.get("/a/AAAAAAAAAAAAAAAA")
    assert r.status_code == 200 and "Paketimi aldım" in r.text
    h = r.headers
    assert h["cache-control"] == "no-store" and h["referrer-policy"] == "origin" and h["x-frame-options"] == "DENY"
    assert "script-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert client.get("/static/alici.js").status_code == 200
    assert client.get("/static/leaflet/leaflet.js").status_code == 200
    assert client.get("/static/leaflet/images/marker-icon.png").status_code == 200


# ------------------------------------------------------------------ 48 saat silme
def test_expired_handoff_is_gone_everywhere_and_purge_deletes_the_row():
    tok, _ = courier()
    h = create(tok, ref="old", send=True, email="alici@example.com")
    t = token_of(h)
    with SessionLocal() as db:
        x = db.get(Handoff, h["id"])
        x.expires_at = now() - timedelta(minutes=1)
        db.commit()
    assert client.get(f"/a/{t}/state").status_code == 404
    assert client.post(f"/a/{t}/received", json={"checked": True}).status_code == 404
    assert client.get(f"/v1/handoffs/{h['id']}/link", headers=auth(tok)).status_code == 404
    assert client.get("/v1/handoffs?refs=old", headers=auth(tok)).json()["items"] == []
    # aynı durak için yeni bağlantı = yeni kod
    again = create(tok, ref="old")
    assert again["id"] != h["id"]


def test_purge_deletes_all_recipient_data():
    tok, _ = courier()
    h = create(tok, ref="purge-me", send=True, email="alici@example.com")
    with SessionLocal() as db:
        x = db.get(Handoff, h["id"])
        x.expires_at = now() - timedelta(seconds=5)
        db.commit()
        n = handoff_router.purge(db)
    assert n >= 1 and row(h["id"]) is None


def test_default_ttl_is_48_hours():
    tok, _ = courier()
    h = create(tok)
    x = row(h["id"])
    exp = x.expires_at if x.expires_at.tzinfo else x.expires_at.replace(tzinfo=now().tzinfo)
    hours = (exp - now()).total_seconds() / 3600
    assert 47.9 < hours <= 48.01


# ------------------------------------------------------------------ alıcı pini → ortak kapı haritası
def street_doors(tok, a, street="Barbaros Caddesi"):
    r = client.get("/v1/doors/street", headers=auth(tok), params={"province": a["province"], "district": a["district"], "neighborhood": a["neighborhood"], "street": street})
    assert r.status_code == 200, r.text
    return r.json()["doors"]


def test_recipient_pin_is_worth_two_when_link_reached_recipient_from_server():
    notify.set_backend(FakeBackend())
    tok, _ = courier()
    a = area()
    h = create(tok, a=a, send=True)
    r = client.post(f"/a/{token_of(h)}/pin", json={"lat": 36.49512, "lon": 32.09903})
    assert r.status_code == 200 and r.json() == {"ok": True, "layer": True}
    doors = street_doors(tok, a)
    assert len(doors) == 1 and doors[0]["door"] == "227" and doors[0]["verified"] is True and doors[0]["site"] == "Güneş Sitesi"
    with SessionLocal() as db:
        rep = db.query(DoorReport).join(DoorPoint, DoorPoint.id == DoorReport.point_id).filter(DoorPoint.district == a["district"]).one()
        assert rep.source == "recipient" and rep.weight == 2.0
    # kuryenin ekranı yeni pini görür
    it = client.get(f"/v1/handoffs?refs={h['ref']}", headers=auth(tok)).json()["items"][0]
    assert it["pin"] == {"lat": 36.49512, "lon": 32.09903}


def test_recipient_pin_counts_only_one_when_courier_saw_the_link():
    tok, _ = courier()
    a = area()
    h = create(tok, a=a, send=True)  # test modu: bağlantı kuryeye verildi
    assert "link" in h
    assert client.post(f"/a/{token_of(h)}/pin", json={"lat": 36.4951, "lon": 32.0991}).json()["layer"] is True
    doors = street_doors(tok, a)
    assert len(doors) == 1 and doors[0]["verified"] is False  # tek başına doğrulama yapamaz (kurye kendine "alıcı" olamaz)
    with SessionLocal() as db:
        rep = db.query(DoorReport).join(DoorPoint, DoorPoint.id == DoorReport.point_id).filter(DoorPoint.district == a["district"]).one()
        assert rep.source == "pin" and rep.weight == 1.0


def test_qr_after_sms_downgrades_the_weight():
    notify.set_backend(FakeBackend())
    tok, _ = courier()
    a = area()
    h = create(tok, a=a, send=True)
    client.get(f"/v1/handoffs/{h['id']}/link", headers=auth(tok))  # kurye QR gösterdi
    client.post(f"/a/{token_of(h)}/pin", json={"lat": 36.4951, "lon": 32.0991})
    assert street_doors(tok, a)[0]["verified"] is False


def test_recipient_repin_moves_the_single_vote():
    notify.set_backend(FakeBackend())
    tok, _ = courier()
    a = area()
    h = create(tok, a=a, send=True)
    t = token_of(h)
    client.post(f"/a/{t}/pin", json={"lat": 36.4951, "lon": 32.0991})
    client.post(f"/a/{t}/pin", json={"lat": 36.4961, "lon": 32.1001})  # ≈ 150 m ötesi: düzeltme
    doors = street_doors(tok, a)
    assert len(doors) == 1 and abs(doors[0]["lat"] - 36.4961) < 1e-6  # aynı kişi tek yere oy verir


def test_pin_far_from_the_guess_or_outside_turkey_is_rejected():
    tok, _ = courier()
    h = create(tok)
    t = token_of(h)
    assert client.post(f"/a/{t}/pin", json={"lat": 36.60, "lon": 32.10}).status_code == 400  # ≈ 12 km
    assert client.post(f"/a/{t}/pin", json={"lat": 10.0, "lon": 32.10}).status_code == 422
    assert row(h["id"]).pin_lat is None


def test_pin_without_address_parts_is_kept_for_the_courier_only():
    tok, _ = courier()
    a = area()
    h = create(tok, a=a, street="", door="")
    assert client.post(f"/a/{token_of(h)}/pin", json={"lat": 36.4951, "lon": 32.0991}).json() == {"ok": True, "layer": False}
    assert street_doors(tok, a) == []
    it = client.get(f"/v1/handoffs?refs={h['ref']}", headers=auth(tok)).json()["items"][0]
    assert it["pin"] is not None


def test_personal_looking_address_parts_never_reach_the_door_layer():
    tok, _ = courier()
    a = area()
    h = create(tok, a=a, street="Gül Sk. 0532 111 22 33", site="mail@ornek.com")
    assert row(h["id"]).street == "" and row(h["id"]).site == ""
    assert client.post(f"/a/{token_of(h)}/pin", json={"lat": 36.4951, "lon": 32.0991}).json()["layer"] is False


def test_no_recipient_phone_or_email_ends_up_in_the_door_tables():
    notify.set_backend(FakeBackend())
    tok, _ = courier()
    a = area()
    h = create(tok, a=a, send=True, email="gizli.alici@example.com")
    t = token_of(h)
    client.post(f"/a/{t}/contact", json={"phone": "05329998877", "email": "baska@example.com"})
    client.post(f"/a/{t}/pin", json={"lat": 36.4951, "lon": 32.0991})
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(tok))
    client.post(f"/a/{t}/received", json={"checked": True})
    with SessionLocal() as db:
        blob = json.dumps([[getattr(p, c.name) for c in DoorPoint.__table__.columns] for p in db.query(DoorPoint).filter(DoorPoint.district == a["district"]).all()], default=str)
        blob += json.dumps([[getattr(p, c.name) for c in DoorReport.__table__.columns] for p in db.query(DoorReport).all()], default=str)
    for secret in (PHONE, "05329998877", "gizli.alici", "baska@example", "Test"):
        assert secret not in blob


# ------------------------------------------------------------------ iletişim sekmesi
def test_contact_validation_and_update():
    tok, _ = courier()
    h = create(tok, phone="")
    t = token_of(h)
    assert client.post(f"/a/{t}/contact", json={"phone": "123"}).status_code == 400
    assert client.post(f"/a/{t}/contact", json={"email": "bozuk"}).status_code == 400
    r = client.post(f"/a/{t}/contact", json={"phone": "0532 999 88 77", "email": "A@Example.com"})
    assert r.json() == {"ok": True, "phone": "05329998877", "email": "a@example.com"}
    assert client.get(f"/v1/handoffs?refs={h['ref']}", headers=auth(tok)).json()["items"][0]["phoneOk"] is True
    # boş yazınca silinir
    r = client.post(f"/a/{t}/contact", json={"phone": "", "email": ""})
    assert r.json()["phone"] == "" and r.json()["email"] == ""


def test_adding_an_email_sends_the_link_when_provider_is_on():
    fake = FakeBackend(sms=False)
    notify.set_backend(fake)
    tok, _ = courier()
    h = create(tok)
    t = token_of(h)
    client.post(f"/a/{t}/contact", json={"email": "alici@example.com"})
    assert len(fake.mail) == 1 and t in fake.mail[0][2]
    client.post(f"/a/{t}/contact", json={"email": "alici@example.com"})  # aynı adres: yeniden gönderilmez
    assert len(fake.mail) == 1


# ------------------------------------------------------------------ sınırlar
def test_public_write_rate_limit():
    tok, _ = courier()
    t = token_of(create(tok))
    codes = [client.post(f"/a/{t}/contact", json={"phone": ""}).status_code for _ in range(45)]
    assert 429 in codes and codes.count(200) <= 40


def test_daily_cap_per_courier_on_a_small_app():
    mini = FastAPI()
    doors = make_doors_router()
    mini.include_router(make_handoff_router(doors.add_recipient_pin, 48, 3))
    c = TestClient(mini)
    tok, _ = courier()
    codes = [c.post("/v1/handoffs", headers=auth(tok), json=body(ref=f"cap-{i}")).status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    # aynı durak için tekrar çağrı sayılmaz
    assert c.post("/v1/handoffs", headers=auth(tok), json=body(ref="cap-0")).status_code == 200


def test_short_ttl_on_a_small_app_expires_quickly():
    mini = FastAPI()
    doors = make_doors_router()
    mini.include_router(make_handoff_router(doors.add_recipient_pin, 0.00001, 50))  # ≈ 0,036 sn
    c = TestClient(mini)
    tok, _ = courier()
    h = c.post("/v1/handoffs", headers=auth(tok), json=body(ref="short")).json()
    import time

    time.sleep(0.2)
    t = row(h["id"]).token
    assert c.get(f"/a/{t}/state").status_code == 404
