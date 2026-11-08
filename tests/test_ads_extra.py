"""1.26.0 — reklam: TABELA, TEK DOKUNUŞLA UZATMA, İSTATİSTİK (kaç kurye gördü, kaç kez göründü, kaç dokunma; yalnız sayı), PİNDE LOGO,
"yarın bitiyor" HATIRLATMASI, fiyatlar (1 coin = 10 TL). Kullanıcı kararları 7 Kasım 2026.
1.27.0 — TEK ÜRÜN (kullanıcı: "O noktada 1 gün durması da 1 ay durmasıda 1 coin (≈ 10 TL)"; "bu zaten yukardaki haritada 1 noktada tabela
görünmesi dediğim şey"): haritada 1 nokta = logolu tabela, nokta başına ayda 1 coin. İşletme: profil konumunda tek; sponsor: 1–10 nokta."""
import itertools
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import account
from app.account import COIN_TL, PROMO_COIN_PER_DAY, TABELA_COIN, TABELA_DAYS, TABELA_MAX_ACTIVE, TABELA_MAX_POINTS, tr_day
from app.db import SessionLocal
from app.main import account_router, app
from app.models import AdBooking, AdSeen, AdStat, Notice, now, uid

from test_auth_consent import auth, reg

client = TestClient(app)
_seq = itertools.count(1)
_spot = itertools.count(1)
HERE = (36.5440, 31.9950)
DAY = 86400000


@pytest.fixture(autouse=True)
def _clean():
    for lim in account_router.limiters:
        lim.reset()
    yield


def new(role, name=None):
    r = reg(role=role, name=name or f"Reklam {role} {next(_seq)}").json()
    return r["token"], r["user"]["id"]


def buy(tok, n=200):
    assert client.post("/v1/coins/test-buy", json={"n": n}, headers=auth(tok)).status_code == 200


def balance(tok):
    return client.get("/v1/coins", headers=auth(tok)).json()["balance"]


def ad(tok, label="Pide 1 lt hediye", lat=None, lon=None):
    """Sponsorun tek noktalı tabelası; yer verilmezse her çağrıda başka bir yer (kendi tabelalarından 30 m uzak olsun)"""
    k = next(_spot)
    lat = HERE[0] + k * 0.002 if lat is None else lat
    lon = HERE[1] if lon is None else lon
    r = client.post("/v1/ads", json={"label": label, "points": [{"lat": lat, "lon": lon}]}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()["items"][0]


def mine(tok):
    return {a["id"]: a for a in client.get("/v1/ads/mine", headers=auth(tok)).json()["items"]}


def old_map_ad(owner_id: str, days=7, cost=21, ends_in_h: float = 24 * 5, lat=36.60, lon=32.20, label="Eski reklam") -> str:
    """1.26.0'dan kalan "harita" reklamı (nokta·gün) — yeni kayıt artık açılmıyor; eskisi süresi bitene kadar yayında"""
    t = now()
    db = SessionLocal()
    try:
        a = AdBooking(id=uid(), owner_id=owner_id, label=label, lat=lat, lon=lon, days=days, cost=cost, package_id="", group_id="",
                      starts_at=t - timedelta(days=1), ends_at=t + timedelta(hours=ends_in_h), created_at=t - timedelta(days=1))
        db.add(a)
        db.commit()
        return a.id
    finally:
        db.close()


def test_prices_one_coin_ten_tl_one_product():
    assert (COIN_TL, PROMO_COIN_PER_DAY, TABELA_COIN, TABELA_DAYS, TABELA_MAX_POINTS, TABELA_MAX_ACTIVE) == (10, 2, 1, 30, 10, 50)
    assert not hasattr(account, "AD_COIN_PER_DAY") and not hasattr(account, "AD_PACKAGES")  # ~~nokta·gün 3 coin, paketler~~
    tok, _ = new("sponsor")
    rules = client.get("/v1/coins", headers=auth(tok)).json()["rules"]
    assert rules["coinTl"] == 10 and rules["adPerMonth"] == 1 and rules["promoPerDay"] == 2
    assert rules["tabela"] == {"cost": 1, "days": 30, "maxPoints": 10, "maxActive": 50, "gapM": 30}


def test_tabela_business_at_profile_location_once_and_courier_sees_logo():
    tok, me = new("employer", "Burger Ev Tabela")
    buy(tok, 50)
    r = client.post("/v1/ads", json={"package": "tabela"}, headers=auth(tok))
    assert r.status_code == 400 and "İşletme Profili" in r.json()["detail"]  # konum işaretlenmeden olmaz
    # son ilanın başlama noktası tabela yeri SAYILMAZ (yalnız İşletme Profili'ndeki konum)
    assert client.post("/v1/jobs", json={"title": "Önce ilan", "lat": 36.50, "lon": 31.90, "packageCount": 1}, headers=auth(tok)).status_code == 200
    assert client.post("/v1/ads", json={}, headers=auth(tok)).status_code == 400
    assert client.put("/v1/account/docs/business", json={"data": {"name": "Burger Ev", "lat": 36.551, "lon": 32.002}}, headers=auth(tok)).status_code == 200
    # işletmede yer ve metin seçilmez: gönderilen noktalar / metin yok sayılır
    r = client.post("/v1/ads", json={"points": [{"lat": 40.0, "lon": 30.0}, {"lat": 40.1, "lon": 30.1}], "label": "başka yazı"}, headers=auth(tok))
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1
    t = r.json()["items"][0]
    assert t["kind"] == "tabela" and t["label"] == "Burger Ev" and (t["lat"], t["lon"]) == (36.551, 32.002) and t["cost"] == 1 and t["days"] == 30
    assert r.json()["coins"]["balance"] == 49 and r.json()["coins"]["history"][0]["label"] == "Harita tabelası · Burger Ev · 1 nokta · 30 gün"
    # profilde "tabela adı" yazan işletmede tabelada o yazar
    tok3, _ = new("employer", "Kısa Ad")
    buy(tok3, 50)
    client.put("/v1/account/docs/business", json={"data": {"name": "Kısa Ad", "signboard": "Kısa Ad Döner Salonu", "lat": 36.56, "lon": 32.01}}, headers=auth(tok3))
    assert client.post("/v1/ads", json={"package": "tabela"}, headers=auth(tok3)).json()["items"][0]["label"] == "Kısa Ad Döner Salonu"
    assert client.post("/v1/ads", json={"package": "tabela"}, headers=auth(tok)).status_code == 409  # işletmede aynı anda tek tabela
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


def test_sponsor_many_points_each_one_coin_a_month(monkeypatch):
    tok, me = new("sponsor", "Akaryakıt Tabela")
    buy(tok, 50)
    assert client.post("/v1/ads", json={"package": "tabela"}, headers=auth(tok)).status_code == 400  # yer seçilmeli
    pts = [{"lat": 36.60 + i * 0.001, "lon": 32.10} for i in range(4)]
    r = client.post("/v1/ads", json={"points": pts}, headers=auth(tok))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 4 and {x["kind"] for x in items} == {"tabela"} and [(x["lat"], x["lon"]) for x in items] == [(p["lat"], p["lon"]) for p in pts]
    assert balance(tok) == 46 and {x["label"] for x in items} == {"Akaryakıt Tabela"}
    # en çok TABELA_MAX_ACTIVE yayında (kuryenin haritası dolmasın)
    monkeypatch.setattr(account, "TABELA_MAX_ACTIVE", 5)
    more = [{"lat": 36.70 + i * 0.001, "lon": 32.10} for i in range(2)]
    over = client.post("/v1/ads", json={"points": more}, headers=auth(tok))
    assert over.status_code == 400 and "en çok 5" in over.json()["detail"] and "4 yayında" in over.json()["detail"]
    assert client.post("/v1/ads", json={"points": more[:1]}, headers=auth(tok)).status_code == 200
    assert balance(tok) == 45


def test_extend_one_month_one_coin_and_reopen():
    tok, me = new("sponsor")
    buy(tok, 200)
    a = ad(tok)
    b0 = balance(tok)
    r = client.post(f"/v1/ads/{a['id']}/extend", json={"days": 7}, headers=auth(tok))  # gün yok sayılır: her uzatma bir ay
    assert r.status_code == 200, r.text
    x = r.json()["item"]
    assert x["days"] == 60 and x["cost"] == 2 and abs(x["endsAt"] - a["endsAt"] - 30 * DAY) < 5000
    assert balance(tok) == b0 - 1 and r.json()["coins"]["history"][0]["label"].startswith("Tabela uzatma · ")
    other, _ = new("sponsor")
    assert client.post(f"/v1/ads/{a['id']}/extend", json={}, headers=auth(other)).status_code == 404
    # süresi dolmuş tabela aynı yerde yeniden açılır: bitiş = şimdi + 30 gün
    db = SessionLocal()
    try:
        db.get(AdBooking, a["id"]).ends_at = now() - timedelta(days=2)
        db.commit()
    finally:
        db.close()
    assert not mine(tok)[a["id"]]["active"]
    # süresi dolarken aynı yere başka tabela konduysa yeniden açılmaz (üst üste olmasın)
    near = ad(tok, lat=a["lat"], lon=a["lon"] + 0.0001)
    assert client.post(f"/v1/ads/{a['id']}/extend", json={}, headers=auth(tok)).status_code == 409
    assert client.delete(f"/v1/ads/{near['id']}", headers=auth(tok)).status_code == 200
    y = client.post(f"/v1/ads/{a['id']}/extend", json={}, headers=auth(tok)).json()["item"]
    assert y["active"] and abs(y["endsAt"] - (y["at"] + 30 * DAY)) < 5000
    # 1.26.0'dan kalan harita reklamı: uzatınca o da bir ay, 1 coin
    old = old_map_ad(me)
    z = client.post(f"/v1/ads/{old}/extend", json={"days": 1}, headers=auth(tok)).json()["item"]
    assert z["kind"] == "harita" and z["days"] == 7 + 30 and z["cost"] == 21 + 1
    # kaldırılan reklam uzatılamaz
    assert client.delete(f"/v1/ads/{a['id']}", headers=auth(tok)).status_code == 200
    assert client.post(f"/v1/ads/{a['id']}/extend", json={}, headers=auth(tok)).status_code == 404


def test_business_reopen_blocked_while_another_tabela_is_live():
    tok, me = new("employer", "Yeniden Açılış")
    buy(tok, 50)
    client.put("/v1/account/docs/business", json={"data": {"name": "Yeniden Açılış", "lat": 36.58, "lon": 32.03}}, headers=auth(tok))
    first = client.post("/v1/ads", json={}, headers=auth(tok)).json()["items"][0]
    db = SessionLocal()
    try:
        db.get(AdBooking, first["id"]).ends_at = now() - timedelta(days=1)
        db.commit()
    finally:
        db.close()
    second = client.post("/v1/ads", json={}, headers=auth(tok))  # eski tabelanın süresi doldu: yenisi alınır
    assert second.status_code == 200, second.text
    r = client.post(f"/v1/ads/{first['id']}/extend", json={}, headers=auth(tok))
    assert r.status_code == 409 and "zaten yayında" in r.json()["detail"]
    assert client.post(f"/v1/ads/{second.json()['items'][0]['id']}/extend", json={}, headers=auth(tok)).status_code == 200


def test_stats_counts_couriers_shows_taps_numbers_only():
    tok, me = new("sponsor")
    buy(tok, 200)
    a = ad(tok)
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
    assert adm["stats"]["viewers"] == 2 and adm["stats"]["taps"] == 22 and adm["kind"] == "tabela"


def test_reminder_day_before_once_per_group_not_for_one_day_ads():
    tok, me = new("sponsor")
    buy(tok, 500)
    a = ad(tok, label="Aylık")
    p = client.post("/v1/ads", json={"label": "Paket", "points": [{"lat": 36.54, "lon": 31.99}, {"lat": 36.55, "lon": 31.98}, {"lat": 36.56, "lon": 31.97}]},
                    headers=auth(tok))
    assert p.status_code == 200, p.text
    one = old_map_ad(me, days=1, cost=3, label="Bir gün")  # 1.26.0'dan kalan 1 günlük reklam: hatırlatma yok
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
    assert len(texts) == 2 and any("Aylık" in t for t in texts) and any("Paket" in t for t in texts) and not any("Bir gün" in t for t in texts)
    assert all(t.startswith("Tabelan yarın bitiyor") and "Reklamlarım › Uzat" in t for t in texts)
    assert one
    # uzatılınca hatırlatma yeniden kurulur
    client.post(f"/v1/ads/{a['id']}/extend", json={}, headers=auth(tok))
    db = SessionLocal()
    try:
        assert db.get(AdBooking, a["id"]).reminded_at is None
    finally:
        db.close()


def test_delete_account_with_ad_stats():
    tok, me = new("sponsor")
    buy(tok, 200)
    a = ad(tok)
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
