"""1.26.0 — reklam: TABELA (işletme profil konumunda / sponsor seçtiği yerde, ayda 1 coin), TEK DOKUNUŞLA UZATMA, İSTATİSTİK (kaç kurye
gördü, kaç kez göründü, kaç dokunma; yalnız sayı), PİNDE LOGO, "yarın bitiyor" HATIRLATMASI, fiyatlar (1 coin = 10 TL; eski örnekler ÷10).
Kullanıcı kararları 7 Kasım 2026."""
import itertools
from datetime import timedelta

from fastapi.testclient import TestClient

from app.account import AD_COIN_PER_DAY, COIN_TL, PROMO_COIN_PER_DAY, TABELA_COIN, TABELA_DAYS, tr_day
from app.db import SessionLocal
from app.main import account_router, app
from app.models import AdBooking, AdSeen, AdStat, Notice, now

from test_auth_consent import auth, reg

client = TestClient(app)
_seq = itertools.count(1)
HERE = (36.5440, 31.9950)


def new(role, name=None):
    r = reg(role=role, name=name or f"Reklam {role} {next(_seq)}").json()
    return r["token"], r["user"]["id"]


def buy(tok, n=200):
    assert client.post("/v1/coins/test-buy", json={"n": n}, headers=auth(tok)).status_code == 200


def balance(tok):
    return client.get("/v1/coins", headers=auth(tok)).json()["balance"]


def ad(tok, days=7, lat=HERE[0], lon=HERE[1], label="Pide 1 lt hediye"):
    r = client.post("/v1/ads", json={"label": label, "points": [{"lat": lat, "lon": lon}], "days": days}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()["items"][0]


def mine(tok):
    return {a["id"]: a for a in client.get("/v1/ads/mine", headers=auth(tok)).json()["items"]}


def test_prices_one_coin_ten_tl():
    assert (COIN_TL, AD_COIN_PER_DAY, PROMO_COIN_PER_DAY, TABELA_COIN, TABELA_DAYS) == (10, 3, 2, 1, 30)
    tok, _ = new("sponsor")
    rules = client.get("/v1/coins", headers=auth(tok)).json()["rules"]
    assert rules["coinTl"] == 10 and rules["adPerDay"] == 3 and rules["promoPerDay"] == 2 and rules["tabela"] == {"cost": 1, "days": 30}
    assert {p["id"]: p["cost"] for p in rules["packages"]}["p1-30"] == 90  # 1 nokta 30 gün = 900 TL


def test_tabela_business_at_profile_location_once_and_courier_sees_logo():
    tok, me = new("employer", "Burger Ev Tabela")
    buy(tok, 50)
    r = client.post("/v1/ads", json={"package": "tabela"}, headers=auth(tok))
    assert r.status_code == 400 and "İşletme Profili" in r.json()["detail"]  # konum işaretlenmeden olmaz
    # son ilanın başlama noktası tabela yeri SAYILMAZ (yalnız İşletme Profili'ndeki konum)
    assert client.post("/v1/jobs", json={"title": "Önce ilan", "lat": 36.50, "lon": 31.90, "packageCount": 1}, headers=auth(tok)).status_code == 200
    assert client.post("/v1/ads", json={"package": "tabela"}, headers=auth(tok)).status_code == 400
    assert client.put("/v1/account/docs/business", json={"data": {"name": "Burger Ev", "lat": 36.551, "lon": 32.002}}, headers=auth(tok)).status_code == 200
    r = client.post("/v1/ads", json={"package": "tabela", "points": [{"lat": 40.0, "lon": 30.0}], "label": "başka yazı"}, headers=auth(tok))
    assert r.status_code == 200, r.text
    t = r.json()["items"][0]
    assert t["kind"] == "tabela" and t["label"] == "Burger Ev" and (t["lat"], t["lon"]) == (36.551, 32.002) and t["cost"] == 1 and t["days"] == 30
    assert r.json()["coins"]["balance"] == 49 and "Tabela reklamı" in r.json()["coins"]["history"][0]["label"]
    # profilde "tabela adı" yazan işletmede tabelada o yazar
    tok3, _ = new("employer", "Kısa Ad")
    buy(tok3, 50)
    client.put("/v1/account/docs/business", json={"data": {"name": "Kısa Ad", "signboard": "Kısa Ad Döner Salonu", "lat": 36.56, "lon": 32.01}}, headers=auth(tok3))
    assert client.post("/v1/ads", json={"package": "tabela"}, headers=auth(tok3)).json()["items"][0]["label"] == "Kısa Ad Döner Salonu"
    assert client.post("/v1/ads", json={"package": "tabela"}, headers=auth(tok)).status_code == 409  # aynı anda tek tabela
    ctok, _ = new("courier")
    items = client.get("/v1/ads/active", params={"lat": 36.55, "lon": 32.0, "radius": 3000}, headers=auth(ctok)).json()["items"]
    mine_t = [x for x in items if x["id"] == t["id"]][0]
    assert mine_t["kind"] == "tabela" and mine_t["sponsor"] == "Burger Ev" and "logo" in mine_t and mine_t["cost"] == 0
    # ilan listesinde 🪧 işareti
    j = client.post("/v1/jobs", json={"title": "Akşam paketleri", "lat": 36.55, "lon": 32.0, "packageCount": 3}, headers=auth(tok))
    assert j.status_code == 200, j.text
    rows = [x for x in client.get("/v1/jobs/open", headers=auth(ctok)).json() if x["id"] == j.json()["id"]]
    assert rows and rows[0]["employer"]["tabela"] is True
    # tabelası olmayan işletme: işaret yok
    tok2, _ = new("employer", "Tabelasız Dükkan")
    j2 = client.post("/v1/jobs", json={"title": "Öğle", "lat": 36.55, "lon": 32.0, "packageCount": 1}, headers=auth(tok2)).json()
    rows2 = [x for x in client.get("/v1/jobs/open", headers=auth(ctok)).json() if x["id"] == j2["id"]]
    assert rows2 and rows2[0]["employer"]["tabela"] is False


def test_tabela_sponsor_picks_spot_once():
    tok, me = new("sponsor", "Akaryakıt Tabela")
    buy(tok, 50)
    assert client.post("/v1/ads", json={"package": "tabela"}, headers=auth(tok)).status_code == 400  # yer seçilmeli
    r = client.post("/v1/ads", json={"package": "tabela", "points": [{"lat": 36.6, "lon": 32.1}]}, headers=auth(tok))
    assert r.status_code == 200 and r.json()["items"][0]["kind"] == "tabela" and (r.json()["items"][0]["lat"], r.json()["items"][0]["lon"]) == (36.6, 32.1)


def test_extend_map_ad_and_tabela_one_tap():
    tok, me = new("sponsor")
    buy(tok, 200)
    a = ad(tok, 7)
    b0 = balance(tok)
    r = client.post(f"/v1/ads/{a['id']}/extend", json={"days": 7}, headers=auth(tok))
    assert r.status_code == 200, r.text
    x = r.json()["item"]
    assert x["days"] == 14 and x["cost"] == 2 * 7 * AD_COIN_PER_DAY and abs(x["endsAt"] - a["endsAt"] - 7 * 86400000) < 5000
    assert balance(tok) == b0 - 7 * AD_COIN_PER_DAY and "Reklam uzatma" in r.json()["coins"]["history"][0]["label"]
    assert client.post(f"/v1/ads/{a['id']}/extend", json={"days": 5}, headers=auth(tok)).status_code == 400
    other, _ = new("sponsor")
    assert client.post(f"/v1/ads/{a['id']}/extend", json={"days": 7}, headers=auth(other)).status_code == 404
    # süresi dolmuş reklam yeniden açılır: bitiş = şimdi + gün
    db = SessionLocal()
    try:
        row = db.get(AdBooking, a["id"])
        row.ends_at = now() - timedelta(days=2)
        db.commit()
    finally:
        db.close()
    assert not mine(tok)[a["id"]]["active"]
    y = client.post(f"/v1/ads/{a['id']}/extend", json={"days": 1}, headers=auth(tok)).json()["item"]
    assert y["active"] and abs(y["endsAt"] - (y["at"] + 86400000)) < 5000
    # tabela: bir ay daha, 1 coin
    t = client.post("/v1/ads", json={"package": "tabela", "points": [{"lat": 36.6, "lon": 32.1}]}, headers=auth(tok)).json()["items"][0]
    z = client.post(f"/v1/ads/{t['id']}/extend", json={}, headers=auth(tok)).json()["item"]
    assert z["days"] == 60 and z["cost"] == 2
    # kaldırılan reklam uzatılamaz
    assert client.delete(f"/v1/ads/{a['id']}", headers=auth(tok)).status_code == 200
    assert client.post(f"/v1/ads/{a['id']}/extend", json={"days": 7}, headers=auth(tok)).status_code == 404


def test_stats_counts_couriers_shows_taps_numbers_only():
    tok, me = new("sponsor")
    buy(tok, 200)
    a = ad(tok, 7)
    k1, k1_id = new("courier")
    k2, _ = new("courier")
    seen = lambda t, sh, tp, i=a["id"]: client.post("/v1/ads/seen", json={"items": [{"id": i, "shows": sh, "taps": tp}]}, headers=auth(t))  # noqa: E731
    assert seen(k1, 1, 0).json()["n"] == 1
    assert seen(k1, 2, 1).status_code == 200  # aynı kurye aynı gün: gören kurye sayısı artmaz
    assert seen(k2, 1, 1).status_code == 200
    assert seen(k2, 1001, 0).status_code == 422  # sınır
    assert seen(k2, 999, 999).status_code == 200  # bir bildirimde reklam başına en çok 50 gösterim / 20 dokunma
    s = mine(tok)[a["id"]]["stats"]
    assert s["viewers"] == 2 and s["shows"] == 1 + 2 + 1 + 50 and s["taps"] == 1 + 1 + 20 and s["todayViewers"] == 2
    assert seen(tok, 5, 5).json()["n"] == 0  # reklam veren (kurye değil) sayılmaz
    k3, _ = new("courier")
    assert client.post("/v1/ads/seen", json={"items": [{"id": "yok", "shows": 1, "taps": 0}]}, headers=auth(k3)).json()["n"] == 0
    flat = str(client.get("/v1/ads/mine", headers=auth(tok)).json())
    assert k1_id not in flat  # kuryenin kimliği reklam verene gitmez
    acc = client.get("/v1/account", headers=auth(tok)).json()
    assert [x for x in acc["ads"]["items"] if x["id"] == a["id"]][0]["stats"]["viewers"] == 2
    from helpers import admin_login
    h, _ = admin_login()
    adm = [x for x in client.get("/v1/admin/ads", headers=h).json()["items"] if x["id"] == a["id"]][0]
    assert adm["stats"]["viewers"] == 2 and adm["stats"]["taps"] == 22 and adm["kind"] == "harita"


def test_reminder_day_before_once_per_group_not_for_one_day_ads():
    tok, me = new("sponsor")
    buy(tok, 500)
    a = ad(tok, 7, label="Haftalık")
    one = ad(tok, 1, label="Bir gün")
    p = client.post("/v1/ads", json={"label": "Paket", "points": [{"lat": 36.54, "lon": 31.99}, {"lat": 36.55, "lon": 31.98}, {"lat": 36.56, "lon": 31.97}], "package": "p3-7"},
                    headers=auth(tok))
    assert p.status_code == 200, p.text
    db = SessionLocal()
    try:
        for row in db.query(AdBooking).filter(AdBooking.owner_id == me).all():
            row.ends_at = now() + timedelta(hours=10)
        db.commit()
        account_router.sweep(db)
        account_router.sweep(db)  # ikinci tur yeniden yazmaz
        texts = [n.text for n in db.query(Notice).filter(Notice.user_id == me, Notice.kind == "ad.ending").all()]
    finally:
        db.close()
    assert len(texts) == 2 and any("Haftalık" in t for t in texts) and any("Paket" in t for t in texts) and not any("Bir gün" in t for t in texts)
    assert all("Reklamlarım › Uzat" in t for t in texts)
    # uzatılınca hatırlatma yeniden kurulur
    client.post(f"/v1/ads/{a['id']}/extend", json={"days": 1}, headers=auth(tok))
    db = SessionLocal()
    try:
        assert db.get(AdBooking, a["id"]).reminded_at is None
    finally:
        db.close()


def test_delete_account_with_ad_stats():
    tok, me = new("sponsor")
    buy(tok, 200)
    a = ad(tok, 7)
    k, k_id = new("courier")
    client.post("/v1/ads/seen", json={"items": [{"id": a["id"], "shows": 1, "taps": 1}]}, headers=auth(k))
    def delete_account(t):
        return client.post("/v1/me/delete", json={"password": "sifre123", "confirm": True}, headers=auth(t))
    assert delete_account(k).status_code == 200  # kuryenin "gördü" kaydı silinir, sayılar kalır
    db = SessionLocal()
    try:
        assert db.query(AdSeen).filter(AdSeen.user_id == k_id).count() == 0 and db.get(AdStat, (a["id"], tr_day(now()))).shows == 1
    finally:
        db.close()
    assert delete_account(tok).status_code == 200  # reklamı + istatistiği silinir (yabancı anahtar hatası yok)
    db = SessionLocal()
    try:
        assert db.query(AdStat).filter(AdStat.ad_id == a["id"]).count() == 0 and db.get(AdBooking, a["id"]) is None
    finally:
        db.close()
