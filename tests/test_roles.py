"""1.23.0 (kullanıcı kararları 3 Kasım 2026): rolüne göre kurallar.
- Sponsor kurye kullanmaz: grup sohbeti ve yazışmalar sponsora kapalı (gönderemez, ona gönderilemez, kişilerden bulunamaz, kişi aramaz).
- 1.24.0 (kullanıcı kararı 6 Kasım 2026: "işletme konumunu asla saklayamaz"): ilanın başlama noktası HER KURYEYE gider; sahibi de görür,
  başka işletme ve sponsor görmez. İşletme kartı: kurye, işletmenin profilinde yazanları görür (vergi bilgileri hariç).
"""
from fastapi.testclient import TestClient

from app.main import app

from test_auth_consent import auth, phone, reg

client = TestClient(app)


def user(role="courier", name="Kişi", ph=None):
    r = reg(role=role, name=name, **({"phone": ph} if ph else {})).json()
    return r["token"], r["user"]["id"]


def consent(tok, loc: bool):
    body = {"version": 8, "voice": True, "ocr": False, "grupKonum": loc, "aydinlatmaAt": 1, "acikRizaAt": 2, "sozlesmeV": 2, "sozlesmeAt": 3}
    r = client.post("/v1/me/consent", headers=auth(tok), json=body)
    assert r.status_code == 200, r.text


def job(tok, lat=36.55, lon=32.01):
    r = client.post("/v1/jobs", headers=auth(tok), json={"title": "2 adres", "lat": lat, "lon": lon, "packageCount": 2, "area": "Oba", "fee": 100})
    assert r.status_code == 200, r.text
    return r.json()


def listed(tok, jid):
    rows = [x for x in client.get("/v1/jobs/open", headers=auth(tok)).json() if x["id"] == jid]
    assert rows, "ilan listede yok"
    return rows[0]


# ---------- sponsor: mesajlaşma yok ----------
def test_sponsor_cannot_send_messages():
    sp, _ = user("sponsor", "Akaryakıt")
    _, cid = user("courier", "Ali")
    r = client.post("/v1/relay", headers=auth(sp), json={"to": [cid], "body": "{}"})
    assert r.status_code == 403 and "Sponsor" in r.json()["detail"]


def test_nobody_can_message_a_sponsor_and_groups_drop_the_sponsor():
    _, spid = user("sponsor", "Akaryakıt")
    c, _ = user("courier", "Ali")
    _, c2 = user("courier", "Veli")
    r = client.post("/v1/relay", headers=auth(c), json={"to": [spid], "body": "{}"})
    assert r.status_code == 403 and "Sponsor" in r.json()["detail"]
    r = client.post("/v1/relay", headers=auth(c), json={"to": [spid, c2], "body": "{}"})
    assert r.status_code == 200, r.text
    assert r.json()["recipients"] == [c2] and r.json()["missing"] == [spid]


def test_sponsor_is_not_found_by_contacts_and_cannot_search():
    ph = phone()
    sp, _ = user("sponsor", "Akaryakıt", ph)
    c, _ = user("courier", "Ali")
    r = client.post("/v1/contacts/match", headers=auth(c), json={"phones": [ph]})
    assert r.status_code == 200 and r.json()["matches"] == []
    r = client.post("/v1/contacts/match", headers=auth(sp), json={"phones": [phone()]})
    assert r.status_code == 403


def test_courier_and_employer_still_message_each_other():
    e, eid = user("employer", "Burger Ev")
    c, cid = user("courier", "Ali")
    assert client.post("/v1/relay", headers=auth(e), json={"to": [cid], "body": "{}"}).status_code == 200
    assert client.post("/v1/relay", headers=auth(c), json={"to": [eid], "body": "{}"}).status_code == 200


# ---------- işletme: başlama noktası (her kuryeye) ----------
def test_start_point_always_shown_to_couriers():
    e, _ = user("employer", "Burger Ev")
    consent(e, False)  # eski "Konumumu kuryeler görsün" kapalı olsa da
    j = job(e, 36.51, 32.02)
    assert j["startShared"] is True and j["employerLat"] == 36.51  # sahibi görür
    c, _ = user("courier", "Ali")
    x = listed(c, j["id"])
    assert x["startShared"] is True and (x["employerLat"], x["employerLon"]) == (36.51, 32.02)
    assert client.get(f"/v1/jobs/{j['id']}", headers=auth(c)).json()["employerLat"] == 36.51


def test_start_point_not_sent_to_other_businesses_or_sponsors():
    e, _ = user("employer", "Burger Ev")
    j = job(e)
    e2, _ = user("employer", "Pizza")
    assert listed(e2, j["id"])["employerLat"] is None


# ---------- işletme kartı ----------
def put_doc(tok, section, data):
    r = client.put(f"/v1/account/docs/{section}", headers=auth(tok), json={"data": data, "editedAt": 1700000000000})
    assert r.status_code == 200, r.text


BIZ = {"name": "burger ev", "phone": "05321112233", "email": "info@burger.test", "category": "Gıda / Restoran", "hours": "10:00–23:00",
       "taxNo": "12345678901", "taxOffice": "Alanya", "signboard": "Burger Ev Oba", "lat": 36.551, "lon": 32.011}


def test_courier_sees_business_profile_without_tax_info():
    e, eid = user("employer", "Burger Ev")
    put_doc(e, "business", BIZ)
    c, _ = user("courier", "Ali")
    r = client.get(f"/v1/businesses/{eid}", headers=auth(c))
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["category"], d["hours"], d["signboard"], d["phone"], d["email"]) == ("Gıda / Restoran", "10:00–23:00", "Burger Ev Oba", "05321112233", "info@burger.test")
    assert (d["lat"], d["lon"], d["locFrom"]) == (36.551, 32.011, "profil")
    assert d["no"].startswith("ISV-") and d["name"]
    assert "taxNo" not in d and "taxOffice" not in d and "12345678901" not in r.text


def test_phone_hidden_when_business_turned_it_off():
    e, eid = user("employer", "Burger Ev")
    put_doc(e, "business", BIZ)
    put_doc(e, "privacy", {"showPhone": False, "showPhoto": True})
    c, _ = user("courier", "Ali")
    d = client.get(f"/v1/businesses/{eid}", headers=auth(c)).json()
    assert d["phone"] == "" and d["email"] == "info@burger.test"


def test_location_falls_back_to_latest_listing():
    e, eid = user("employer", "Burger Ev")
    job(e, 36.6, 32.1)
    c, _ = user("courier", "Ali")
    d = client.get(f"/v1/businesses/{eid}", headers=auth(c)).json()
    assert (d["lat"], d["lon"], d["locFrom"]) == (36.6, 32.1, "ilan")


def test_card_rules_who_and_what():
    e, eid = user("employer", "Burger Ev")
    assert client.get(f"/v1/businesses/{eid}", headers=auth(e)).status_code == 200  # kendisi önizler
    e2, _ = user("employer", "Pizza")
    assert client.get(f"/v1/businesses/{eid}", headers=auth(e2)).status_code == 403  # başka işletme göremez
    sp, spid = user("sponsor", "Akaryakıt")
    assert client.get(f"/v1/businesses/{eid}", headers=auth(sp)).status_code == 403
    c, cid = user("courier", "Ali")
    assert client.get(f"/v1/businesses/{cid}", headers=auth(c)).status_code == 404  # kurye hesabı işletme değil
    assert client.get("/v1/businesses/yok-boyle-biri", headers=auth(c)).status_code == 404
