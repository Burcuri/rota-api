"""Yeni akış: ilan (adressiz) + geçici mesaj aktarıcısı + kullanım koşulları kaydı."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app, relay_router
from app.models import JobPackage, RelayDelivery, RelayMsg

from test_auth_consent import auth, phone, reg

client = TestClient(app)


def user(role="courier", name="Kişi"):
    r = reg(role=role, name=name).json()
    return r["token"], r["user"]["id"]


def dev(tok, did, platform="android"):
    r = client.post("/v1/devices", headers=auth(tok), json={"deviceId": did, "platform": platform})
    assert r.status_code == 200, r.text


def inbox(tok, did):
    r = client.get(f"/v1/relay?deviceId={did}", headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def ack(tok, did, ids):
    return client.post("/v1/relay/ack", headers=auth(tok), json={"deviceId": did, "ids": ids}).json()


def counts(mid):
    db = SessionLocal()
    try:
        return db.query(RelayMsg).filter(RelayMsg.id == mid).count(), db.query(RelayDelivery).filter(RelayDelivery.msg_id == mid).count()
    finally:
        db.close()


# ---------- ilan ----------
def test_job_without_addresses_and_no_phone_leak():
    emp, emp_id = user("employer", "Burger Ev")
    r = client.post("/v1/jobs", headers=auth(emp), json={"title": "3 adres · Mahmutlar", "lat": 36.5, "lon": 32.0, "packageCount": 3, "area": "Mahmutlar", "fee": 150})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["packageCount"] == 3 and j["area"] == "Mahmutlar" and j["fee"] == 150 and j["packages"] is None
    assert j["employer"] == {"id": emp_id, "name": "Burger Ev"}  # telefon YOK
    c, _ = user("courier")
    listed = [x for x in client.get("/v1/jobs/open", headers=auth(c)).json() if x["id"] == j["id"]]
    assert listed and "phone" not in listed[0]["employer"]


def test_coordinates_must_be_real_numbers_in_range():
    """NaN / sonsuz / alan dışı koordinat kabul edilmez: veritabanına girip ilan listesi yanıtını (JSON) çökertemez."""
    emp, _ = user("employer")
    h = {**auth(emp), "Content-Type": "application/json"}
    for bad in ('{"lat": NaN, "lon": 32}', '{"lat": 36.5, "lon": Infinity}', '{"lat": 91, "lon": 32}', '{"lat": 36.5, "lon": -181}'):
        assert client.post("/v1/me/location", content=bad, headers=h).status_code == 422, bad
        assert client.post("/v1/jobs", content=bad[:-1] + ', "packageCount": 1}', headers=h).status_code == 422, bad
    assert client.get("/v1/jobs/open", headers=auth(emp)).status_code == 200  # liste hâlâ sağlam
    assert client.post("/v1/me/location", json={"lat": 0, "lon": 0}, headers=auth(emp)).status_code == 200


def test_old_client_packages_are_counted_not_stored():
    emp, _ = user("employer")
    pk = [{"customer": "Ayşe", "phone": phone(), "address": "Gül Sk. 9", "lat": 36.5, "lon": 32.0}] * 2
    j = client.post("/v1/jobs", headers=auth(emp), json={"lat": 36.5, "lon": 32.0, "packages": pk}).json()
    assert j["packageCount"] == 2
    db = SessionLocal()
    try:
        assert db.query(JobPackage).filter(JobPackage.job_id == j["id"]).count() == 0  # alıcı adı/telefonu sunucuda yok
    finally:
        db.close()


def test_job_needs_at_least_one_address_and_close():
    emp, _ = user("employer")
    assert client.post("/v1/jobs", headers=auth(emp), json={"lat": 1, "lon": 1}).status_code == 400
    j = client.post("/v1/jobs", headers=auth(emp), json={"lat": 1, "lon": 1, "packageCount": 1}).json()
    other, _ = user("employer")
    assert client.delete(f"/v1/jobs/{j['id']}", headers=auth(other)).status_code == 403
    assert client.delete(f"/v1/jobs/{j['id']}", headers=auth(emp)).status_code == 200
    c, _ = user()
    assert all(x["id"] != j["id"] for x in client.get("/v1/jobs/open", headers=auth(c)).json())
    assert all(x["id"] != j["id"] for x in client.get("/v1/jobs/mine", headers=auth(emp)).json())


# ---------- kullanım koşulları ----------
def test_terms_acceptance_is_logged_with_version():
    tok, _ = user()
    body = {"version": 2, "voice": False, "ocr": True, "grupKonum": False, "aydinlatmaAt": 1, "acikRizaAt": 2, "sozlesmeV": 1, "sozlesmeAt": 3}
    r = client.post("/v1/me/consent", headers=auth(tok), json=body).json()
    assert r["sozlesmeV"] == 1 and r["sozlesmeAt"] == 3 and r["recordedAt"]
    # yeni sözleşme sürümü = yeni satır
    client.post("/v1/me/consent", headers=auth(tok), json={**body, "sozlesmeV": 2, "sozlesmeAt": 4})
    hist = client.get("/v1/me/consent/history", headers=auth(tok)).json()
    assert [h["sozlesmeV"] for h in hist] == [2, 1]


def test_old_client_consent_without_terms_still_works():
    tok, _ = user()
    r = client.post("/v1/me/consent", headers=auth(tok), json={"version": 1, "voice": True})
    assert r.status_code == 200 and r.json()["sozlesmeV"] == 0


# ---------- aktarıcı ----------
def test_relay_reaches_every_device_then_deletes():
    a, a_id = user("employer", "Burger Ev")
    b, b_id = user("courier", "Murat")
    dev(a, "a-telefon-1"); dev(a, "a-web-0001", "web"); dev(b, "b-telefon-1")
    s = client.post("/v1/relay", headers=auth(a), json={"to": [b_id], "body": '{"text":"merhaba"}', "deviceId": "a-telefon-1"})
    assert s.status_code == 200, s.text
    mid = s.json()["id"]
    assert counts(mid) == (1, 2)  # kuryenin telefonu + işletmenin web ikizi; gönderen telefon hariç
    got = inbox(b, "b-telefon-1")
    assert [m["id"] for m in got] == [mid] and got[0]["senderId"] == a_id and got[0]["senderName"] == "Burger Ev" and not got[0]["mine"]
    web = inbox(a, "a-web-0001")
    assert web[0]["mine"] is True
    assert inbox(a, "a-telefon-1") == []
    ack(b, "b-telefon-1", [mid])
    assert counts(mid) == (1, 1)
    assert inbox(b, "b-telefon-1") == []  # bir kez ulaşır
    assert ack(a, "a-web-0001", [mid])["deleted"] == 1
    assert counts(mid) == (0, 0)  # herkese ulaştı → sunucudan silindi


def test_recipient_without_device_gets_it_on_every_device_later():
    a, _ = user()
    b, b_id = user()
    mid = client.post("/v1/relay", headers=auth(a), json={"to": [b_id], "body": "x"}).json()["id"]
    assert counts(mid) == (1, 1)
    dev(b, "b-ikinci-01")
    assert [m["id"] for m in inbox(b, "b-ilk-0001")] == [mid]  # ilk açılan cihaz alır, diğer cihazına da dağıtılır
    assert [m["id"] for m in inbox(b, "b-ikinci-01")] == [mid]
    ack(b, "b-ilk-0001", [mid]); ack(b, "b-ikinci-01", [mid])
    assert counts(mid) == (0, 0)


def test_cannot_ack_for_someone_else():
    a, _ = user(); b, b_id = user(); x, _ = user()
    dev(b, "b-cihaz-001")
    mid = client.post("/v1/relay", headers=auth(a), json={"to": [b_id], "body": "x"}).json()["id"]
    ack(x, "b-cihaz-001", [mid])
    assert counts(mid) == (1, 1)


def test_relay_limits():
    a, a_id = user()
    b, b_id = user()
    assert client.post("/v1/relay", headers=auth(a), json={"to": [a_id], "body": "x"}).status_code == 400  # kendine
    assert client.post("/v1/relay", headers=auth(a), json={"to": ["yok" * 5], "body": "x"}).status_code == 404
    big = "x" * (256 * 1024 + 1)
    assert client.post("/v1/relay", headers=auth(a), json={"to": [b_id], "body": big}).status_code == 413
    many = [user()[1] for _ in range(7)]
    assert client.post("/v1/relay", headers=auth(a), json={"to": many, "body": "x"}).status_code == 400  # 7 + gönderen > 7
    assert client.post("/v1/relay", headers=auth(a), json={"to": many[:6], "body": "x"}).status_code == 200
    assert client.post("/v1/relay", json={"to": [b_id], "body": "x"}).status_code in (401, 403)
    assert client.get("/v1/relay?deviceId=kisa", headers=auth(a)).status_code == 400


def test_expired_messages_are_purged():
    a, _ = user(); b, b_id = user()
    mid = client.post("/v1/relay", headers=auth(a), json={"to": [b_id], "body": "x"}).json()["id"]
    db = SessionLocal()
    try:
        m = db.get(RelayMsg, mid)
        m.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        relay_router.purge(db)
    finally:
        db.close()
    assert counts(mid) == (0, 0)
    assert inbox(b, "b-gec-00001") == []


def test_forget_device_and_device_taken_over():
    a, _ = user(); b, b_id = user(); c, c_id = user()
    dev(b, "ortak-cihaz-1")
    mid = client.post("/v1/relay", headers=auth(a), json={"to": [b_id], "body": "x"}).json()["id"]
    # aynı cihazda başka hesaba girildi: b'nin bu cihazdaki bekleyeni c'ye GÖSTERİLMEZ
    dev(c, "ortak-cihaz-1")
    assert inbox(c, "ortak-cihaz-1") == []
    assert counts(mid) == (1, 1)  # b'nin başka cihazı yok: ilk açacağı cihaza bırakıldı
    assert [m["id"] for m in inbox(b, "b-yeni-cihaz")] == [mid]
    client.post("/v1/devices/forget", headers=auth(c), json={"deviceId": "ortak-cihaz-1"})
    m2 = client.post("/v1/relay", headers=auth(a), json={"to": [c_id], "body": "y"}).json()["id"]
    assert counts(m2) == (1, 1)  # c'nin cihazı kalmadı → ilk açılan cihazı bekler
