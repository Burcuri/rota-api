"""Kalıcı çalışma bölgesi (1.14.0; kullanıcı kararı 22 Ekim 2026): il, ilçe, EN ÇOK 3 mahalle. İşveren de kurye de ayarlar;
sunucuda durur, her girişte ve her telefonda aynı gelir (her seferinde yeniden girilmez)."""
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import User

from test_auth_consent import auth, reg

client = TestClient(app)


def put(tok, **body):
    return client.post("/v1/me/work-area", json=body, headers=auth(tok))


@pytest.mark.parametrize("role", ["courier", "employer"])
def test_work_area_is_kept_on_the_account_and_comes_back_at_every_login(role):
    r = reg(role=role)
    j = r.json()
    tok, phone = j["token"], j["user"]["phone"]
    assert j["user"]["workArea"] == {"province": "", "district": "", "hoods": []}
    w = put(tok, province="Antalya", district="Alanya", hoods=["Mahmutlar", "Kestel", "Oba"])
    assert w.status_code == 200, w.text
    want = {"province": "Antalya", "district": "Alanya", "hoods": ["Mahmutlar", "Kestel", "Oba"]}
    assert w.json()["workArea"] == want
    assert client.get("/v1/me", headers=auth(tok)).json()["workArea"] == want
    # yeniden giriş (başka telefon): aynı bölge
    again = client.post("/v1/auth/login", json={"login": phone, "password": "sifre123"}).json()
    assert again["user"]["workArea"] == want


def test_at_most_three_neighbourhoods():
    tok = reg().json()["token"]
    r = put(tok, province="Antalya", district="Alanya", hoods=["Mahmutlar", "Kestel", "Oba", "Cikcilli"])
    assert r.status_code == 400 and "En çok 3 mahalle" in r.json()["detail"]
    # aynı mahalle iki kez sayılmaz (Türkçe büyük/küçük harf: İ/i, I/ı)
    r2 = put(tok, province="Antalya", district="Alanya", hoods=["Oba", "OBA", " oba ", "İncekum", "incekum", "Kestel"])
    assert r2.status_code == 200 and r2.json()["workArea"]["hoods"] == ["Oba", "İncekum", "Kestel"]


def test_neighbourhood_needs_a_district_and_bad_text_is_refused():
    tok = reg().json()["token"]
    assert put(tok, hoods=["Mahmutlar"]).status_code == 400
    for bad in ("0532 123 45 67", "ali@ornek.com", "https://x.y", "M" * 61):
        r = put(tok, province="Antalya", district="Alanya", hoods=[bad])
        assert r.status_code == 400, bad
    assert put(tok, province="Antalya", district="A" * 61).status_code == 400
    assert put(tok, province="Antalya", district="Alanya", hoods=["100. Yıl", "19 Mayıs"]).json()["workArea"]["hoods"] == ["100. Yıl", "19 Mayıs"]


def test_work_area_can_be_cleared():
    j = reg().json()
    tok, uid_ = j["token"], j["user"]["id"]
    put(tok, province="Antalya", district="Alanya", hoods=["Oba"])
    r = put(tok, province="", district="", hoods=[])
    assert r.json()["workArea"] == {"province": "", "district": "", "hoods": []}
    db = SessionLocal()
    try:
        assert db.get(User, uid_).work_area is None
    finally:
        db.close()


def test_work_area_needs_a_session():
    assert client.post("/v1/me/work-area", json={"province": "Antalya"}).status_code == 401
