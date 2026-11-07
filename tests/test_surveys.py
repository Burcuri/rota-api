"""1.26.0 ANKETLER sunucuda (kullanıcı kararı 7 Kasım 2026: "anketler ekranını sunucuya arşivlemeliyiz ve veriyi tutmalıyız").
Yönetim yazar, kurye yanıtlar (tek yanıt, değiştirilebilir), herkes yalnız ortalamayı görür (en az 3 yanıt), ilçe ortalaması, arşiv silinmez,
CSV'de kişi yok, hesap silinince yanıt anonim kalır."""
import itertools

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app, surveys_router
from app.models import AdminAudit, SurveyAnswer
from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)
_seq = itertools.count(1)


def new(role, province="Antalya", district="Alanya"):
    r = reg(role=role, name=f"Anket {role} {next(_seq)}").json()
    tok = r["token"]
    if district:
        assert client.post("/v1/me/work-area", json={"province": province, "district": district}, headers=auth(tok)).status_code == 200
    return tok, r["user"]["id"]


def items(tok):
    return {x["id"]: x for x in client.get("/v1/surveys", headers=auth(tok)).json()["items"]}


def ans(tok, sid, v):
    return client.post(f"/v1/surveys/{sid}/answer", json={"value": v}, headers=auth(tok))


def fresh_survey(h, q="Bu hafta kaç saat çalışmak istersin?", kind="sayi", **kw):
    r = client.post("/v1/admin/surveys", json={"q": q, "kind": kind, **kw}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def setup_function(_):
    for lim in surveys_router.limiters:
        lim.reset()


def test_default_surveys_exist_and_only_couriers_answer():
    k, _ = new("courier")
    got = items(k)
    assert {"sv-ucret", "sv-saat"} <= set(got)
    assert got["sv-ucret"]["unit"] == "₺" and got["sv-saat"]["unit"] == "₺/saat" and got["sv-ucret"]["kind"] == "ucret"
    body = client.get("/v1/surveys", headers=auth(k)).json()
    assert body["canAnswer"] is True and body["minShow"] == 3
    e, _ = new("employer")
    assert client.get("/v1/surveys", headers=auth(e)).json()["canAnswer"] is False
    assert ans(e, "sv-ucret", 150).status_code == 403
    s, _ = new("sponsor", district="")
    assert ans(s, "sv-ucret", 150).status_code == 403


def test_average_needs_three_answers_and_district_average_for_business():
    h, _ = admin_login()
    sv = fresh_survey(h, "Bu bölgede paket başı ücret ne olmalı (deneme)?", "ucret", min=10, max=5000)
    sid = sv["id"]
    k1, _ = new("courier", "Antalya", "Manavgat")
    k2, _ = new("courier", "Antalya", "Manavgat")
    k3, _ = new("courier", "Antalya", "Manavgat")
    k4, _ = new("courier", "Mersin", "Erdemli")
    assert ans(k1, sid, 5).status_code == 400 and ans(k1, sid, 6000).status_code == 400  # anketin sınırları
    assert ans(k1, sid, 0).status_code == 422
    r = ans(k1, sid, 100)
    assert r.status_code == 200 and r.json()["item"]["mine"] == 100 and r.json()["item"]["avg"] is None  # tek yanıt: ortalama yok
    ans(k2, sid, 150)
    x = items(k2)[sid]
    assert x["n"] == 2 and x["avg"] is None
    ans(k3, sid, 200)
    ans(k4, sid, 400)
    x = items(k4)[sid]
    assert x["n"] == 4 and x["avg"] == round((100 + 150 + 200 + 400) / 4) and x["mine"] == 400
    assert x["local"] == {"province": "Mersin", "district": "Erdemli", "n": 1, "avg": None}  # ilçede tek yanıt: gizli
    emp, _ = new("employer", "Antalya", "Manavgat")
    y = items(emp)[sid]
    assert y["local"]["n"] == 3 and y["local"]["avg"] == 150 and y["mine"] is None
    # yanıt değiştirilebilir; sayı artmaz
    assert ans(k1, sid, 130).json()["item"]["mine"] == 130
    z = items(emp)[sid]
    assert z["n"] == 4 and z["local"]["avg"] == 160
    # bölgesi ayarlı olmayan işletme: yalnız genel ortalama
    emp2, _ = new("employer", district="")
    assert items(emp2)[sid]["local"] is None and items(emp2)[sid]["avg"] == round((130 + 150 + 200 + 400) / 4)
    # kimin ne yazdığı hiçbir yanıtta yok
    flat = str(client.get("/v1/surveys", headers=auth(emp)).json())
    assert "130" not in flat.replace("'avg': 160", "")


def test_admin_create_archive_restore_export_keeps_data():
    h, _ = admin_login()
    assert client.post("/v1/admin/surveys", json={"q": "kısa"}, headers=h).status_code == 400
    assert client.post("/v1/admin/surveys", json={"q": "Ziyaret edin www.ornek.com lütfen", "kind": "sayi"}, headers=h).status_code == 400
    assert client.post("/v1/admin/surveys", json={"q": "Uygun aralık sorusu nedir?", "min": 10, "max": 5}, headers=h).status_code == 400
    assert client.post("/v1/admin/surveys", json={"q": "Uygun tür sorusu nedir?", "kind": "kotu"}, headers=h).status_code == 422
    sv = fresh_survey(h, "Günde kaç paket taşıyabilirsin (deneme)?", "sayi", unit="paket", min=1, max=300)
    sid = sv["id"]
    assert sv["unit"] == "paket" and sv["status"] == "active" and sv["n"] == 0
    k, kid = new("courier", "Antalya", "Gazipaşa")
    k2, _ = new("courier", "Antalya", "Gazipaşa")
    ans(k, sid, 40)
    ans(k2, sid, 25.5)
    lst = {x["id"]: x for x in client.get("/v1/admin/surveys", headers=h).json()["items"]}
    assert lst[sid]["n"] == 2 and lst[sid]["avg"] == round((40 + 25.5) / 2)  # yönetim ortalamayı az yanıtta da görür
    assert lst[sid]["areas"][0] == {"province": "Antalya", "district": "Gazipaşa", "n": 2, "avg": round((40 + 25.5) / 2)}
    # arşiv: uygulamadan kalkar, yanıtlar durur
    a = client.post(f"/v1/admin/surveys/{sid}/archive", headers=h)
    assert a.status_code == 200 and a.json()["status"] == "archived" and a.json()["archivedAt"] and a.json()["n"] == 2
    assert sid not in items(k)
    assert ans(k, sid, 41).status_code == 404
    arch = {x["id"] for x in client.get("/v1/admin/surveys", params={"status": "archived"}, headers=h).json()["items"]}
    assert sid in arch
    # CSV: kişi yok
    csvr = client.get(f"/v1/admin/surveys/{sid}/export", headers=h)
    assert csvr.status_code == 200 and csvr.headers["content-type"].startswith("text/csv")
    text = csvr.content.decode("utf-8-sig")
    assert "Gün;İl;İlçe;Yanıt" in text and ";Antalya;Gazipaşa;40" in text and ";Antalya;Gazipaşa;25,5" in text
    assert kid not in text and "Anket courier" not in text
    # geri aç: eski yanıtlar yerinde
    rs = client.post(f"/v1/admin/surveys/{sid}/restore", headers=h)
    assert rs.status_code == 200 and rs.json()["status"] == "active" and rs.json()["archivedAt"] is None
    assert items(k)[sid]["mine"] == 40 and items(k)[sid]["n"] == 2
    db = SessionLocal()
    try:
        acts = [x.action for x in db.query(AdminAudit).filter(AdminAudit.target_id == sid).all()]
    finally:
        db.close()
    assert {"survey_create", "survey_archive", "survey_export", "survey_restore"} <= set(acts)
    # yönetici olmayan erişemez
    assert client.get("/v1/admin/surveys", headers=auth(k)).status_code in (401, 403)


def test_deleted_account_answer_stays_anonymous():
    h, _ = admin_login()
    sid = fresh_survey(h, "Silme denemesi için soru nedir?", "sayi")["id"]
    k, kid = new("courier", "Antalya", "Kemer")
    ans(k, sid, 7)
    assert client.post("/v1/me/delete", json={"password": "sifre123", "confirm": True}, headers=auth(k)).status_code == 200
    db = SessionLocal()
    try:
        rows = db.query(SurveyAnswer).filter(SurveyAnswer.survey_id == sid).all()
    finally:
        db.close()
    assert len(rows) == 1 and rows[0].user_id is None and rows[0].value == 7 and rows[0].district == "Kemer"
    k2, _ = new("courier", "Antalya", "Kemer")
    assert ans(k2, sid, 9).status_code == 200  # boşalan kayıt yeni yanıtı engellemez
    assert {x["id"]: x for x in client.get("/v1/admin/surveys", headers=h).json()["items"]}[sid]["n"] == 2


def test_answer_rate_limit():
    k, _ = new("courier")
    codes = [ans(k, "sv-saat", 200 + i).status_code for i in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
