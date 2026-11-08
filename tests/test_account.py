"""Hesap verisi sunucuda tek kaynak (1.17.0; kullanıcı kararı 28 Ekim 2026).

- Bölümler (bildirim / gizlilik / sürüş ayarı / işletme / sponsor / şablonlar / ürün listesi) sunucuda; eşitleme saati yalnız değişenleri verir;
  çakışmada SON DÜZENLENEN geçerli olur.
- Coin defteri sunucuda: test satın alma, harita tabelası (1.27.0: nokta başına ayda 1 coin) ve öne çıkan ilan coin harcar; bakiye
  yetmezse işlem yapılmaz.
- Reklam deposu sunucuda: kuryeler yakındaki aktif reklamları görür; askıdaki hesabın reklamı görünmez; yönetim kaldırabilir.
- Rehber yedeği: hesap başına tek, yalnız sahibi okur, 24 saat sonra silinir.
"""
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.account import PROMO_COIN_PER_DAY, TABELA_COIN, TABELA_DAYS
from app.db import SessionLocal
from app.main import account_router, app
from app.models import AdBooking, AdminAudit, CoinEntry, GuideBackup, Notice, User, now

from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)
HERE = (36.5440, 31.9950)


@pytest.fixture(autouse=True)
def _clean():
    for lim in account_router.limiters:
        lim.reset()
    yield
    for lim in account_router.limiters:
        lim.reset()


def new(role, name="Hesap Deneme"):
    r = reg(role=role, name=name).json()
    return r["token"], r["user"]["id"]


def sync(tok, since=0):
    r = client.get(f"/v1/account?since={since}", headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def put(tok, section, data, edited=None):
    body = {"data": data}
    if edited is not None:
        body["editedAt"] = edited
    return client.put(f"/v1/account/docs/{section}", json=body, headers=auth(tok))


def buy(tok, n=500):
    r = client.post("/v1/coins/test-buy", json={"n": n}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()["coins"]


def balance(tok):
    return client.get("/v1/coins", headers=auth(tok)).json()["balance"]


# ------------------------------------------------------------------ bölümler ve eşitleme
def test_sections_sync_between_devices_and_only_changes():
    tok, _ = new("employer")
    s0 = sync(tok)
    assert s0["clock"] == 0 and s0["docs"] == {} and s0["coins"]["balance"] == 0 and s0["ads"]["items"] == [] and s0["rules"]["adPerMonth"] == TABELA_COIN
    assert s0["me"]["name"] and s0["guideBackup"] is None
    # telefon: işletme profili + bildirim tercihi
    r = put(tok, "business", {"name": "Burger Ev", "phone": "0242 000 00 00", "category": "Gıda / Restoran", "lat": 36.5, "lon": 32.0, "logo": "data:x", "iban": "TR..."})
    assert r.status_code == 200, r.text
    assert r.json()["doc"]["data"]["name"] == "Burger Ev" and "logo" not in r.json()["doc"]["data"] and "iban" not in r.json()["doc"]["data"]
    assert put(tok, "notif", {"newJob": False, "quietOn": True, "quietFrom": "22:30", "quietTo": "bozuk", "quietDay": 9}).status_code == 200
    # web ikizi (ilk açılış): hepsi gelir
    s1 = sync(tok)
    assert s1["clock"] == 2 and set(s1["docs"]) == {"business", "notif"}
    n = s1["docs"]["notif"]["data"]
    assert n["newJob"] is False and n["quietOn"] is True and n["quietFrom"] == "22:30" and n["quietTo"] == "07:00" and n["quietDay"] == -1 and n["accepted"] is True
    # web ikizi şablonu değiştirir → telefon yalnız değişeni alır
    assert put(tok, "templates", {"sms": "Merhaba\nKapıdayım", "jobNote": "Sıcak teslim"}).status_code == 200
    s2 = sync(tok, s1["clock"])
    assert set(s2["docs"]) == {"templates"} and s2["docs"]["templates"]["data"]["sms"] == "Merhaba\nKapıdayım"
    assert "coins" not in s2 and "ads" not in s2 and "rules" not in s2
    # değişiklik yoksa boş
    s3 = sync(tok, s2["clock"])
    assert s3["docs"] == {} and s3["clock"] == s2["clock"]
    # cihazın saati sunucudan ilerideyse (veritabanı değişti) hepsi yeniden gelir
    assert set(sync(tok, 10**9)["docs"]) == {"business", "notif", "templates"}


def test_last_edit_wins_and_stale_write_returns_current():
    tok, _ = new("sponsor")
    t = sync(tok)["now"] - 600_000  # 10 dakika önce
    assert put(tok, "sponsor", {"company": "Yeni Ad"}, edited=t).status_code == 200
    # internetsiz kalan eski cihaz daha ÖNCE düzenlemişti → yazılmaz, güncel hali döner
    r = put(tok, "sponsor", {"company": "Eski Ad"}, edited=t - 60_000)
    assert r.status_code == 200 and r.json()["stale"] is True and r.json()["doc"]["data"]["company"] == "Yeni Ad"
    assert sync(tok)["docs"]["sponsor"]["data"]["company"] == "Yeni Ad"
    # sonra düzenlenen geçer
    r = put(tok, "sponsor", {"company": "En Yeni"}, edited=t + 1000)
    assert r.json()["stale"] is False and sync(tok)["docs"]["sponsor"]["data"]["company"] == "En Yeni"


def test_future_clock_is_clamped():
    tok, _ = new("employer")
    far = 4_000_000_000_000  # yıllar ileride (yanlış saatli cihaz)
    assert put(tok, "settings", {"driveStart": True}, edited=far).status_code == 200
    # ileri saatli cihaz bölümü yıllarca kilitleyemez: en çok 5 dakika ileri yazılır
    s = sync(tok)
    assert s["docs"]["settings"]["editedAt"] <= s["now"] + 5 * 60 * 1000 + 2000
    assert put(tok, "settings", {"driveStart": False}, edited=s["now"] + 6 * 60 * 1000).json()["stale"] is False


def test_section_validation():
    tok, _ = new("employer")
    assert put(tok, "payment", {"iban": "TR33..."}).status_code == 404  # IBAN bölümü YOK
    assert put(tok, "vehicle", {"plate": "07 ABC 123"}).status_code == 404  # araç rızayla ayrı (plaka telefonda)
    r = put(tok, "bizItems", {"items": [{"id": "a1", "name": "Lahmacun", "price": "60 ₺", "desc": "Acılı", "photo": "data:..."}, {"name": ""}, "x"] + [{"name": f"Ü{i}"} for i in range(60)]})
    items = r.json()["doc"]["data"]["items"]
    assert len(items) == 50 and items[0] == {"id": "a1", "name": "Lahmacun", "price": "60 ₺", "desc": "Acılı", "at": 0}
    assert client.put("/v1/account/docs/notif", json={"data": "metin"}, headers=auth(tok)).status_code == 422
    big = {"sms": "a" * 5000, "jobNote": "b" * 5000}
    assert put(tok, "templates", big).json()["doc"]["data"]["sms"] == "a" * 300
    assert client.get("/v1/account").status_code == 401


def test_me_follows_profile_change_from_other_device():
    tok, _ = new("courier", "eski ad")
    client.post("/v1/me/profile", json={"name": "Yeni Ad Soyad"}, headers=auth(tok))
    assert sync(tok)["me"]["name"] == "Yeni Ad Soyad"


# ------------------------------------------------------------------ coin
def test_coins_test_buy_and_history():
    tok, _ = new("sponsor")
    c = buy(tok, 200)
    assert c["balance"] == 200 and c["history"][0]["label"] == "Rota coin satın alma (test) · 200" and c["history"][0]["delta"] == 200
    assert client.post("/v1/coins/test-buy", json={"n": 999}, headers=auth(tok)).status_code == 400
    s = sync(tok)
    assert s["coins"]["balance"] == 200
    # coin değişince eşitleme coin'i yeniden verir
    clock = s["clock"]
    buy(tok, 50)
    s2 = sync(tok, clock)
    assert s2["coins"]["balance"] == 250 and "ads" not in s2


def test_test_buy_daily_cap_and_switch_off(monkeypatch):
    tok, _ = new("employer")
    for _ in range(10):
        buy(tok, 50)
    assert client.post("/v1/coins/test-buy", json={"n": 50}, headers=auth(tok)).status_code == 429
    # yayında kapatılır (COIN_TEST_BUY=0): ayrı yönlendirici ile
    from app.account import make_router
    from fastapi import FastAPI

    off = FastAPI()
    off.include_router(make_router(lambda u: {}, test_buy=False))
    r = TestClient(off).post("/v1/coins/test-buy", json={"n": 50}, headers=auth(tok))
    assert r.status_code == 403 and "Google Play" in r.json()["detail"]


# ------------------------------------------------------------------ reklam deposu
def grid(k: int, n: int) -> list[dict]:
    """n nokta, birbirinden ~90 m; k her çağrıda başka bir sıra (aynı hesabın tabelaları 30 m'den yakın olmasın)"""
    return [{"lat": HERE[0] + k * 0.002, "lon": HERE[1] + i * 0.001} for i in range(n)]


def test_ad_booking_spends_coins_and_couriers_see_it():
    sp, sp_id = new("sponsor", "akaryakıt sahibi")
    put(sp, "sponsor", {"company": "Akaryakıt A.Ş."})
    buy(sp, 500)
    # 1.27.0 tek ürün: haritada 1 nokta = tabela, ayda 1 coin. Eski istemcinin "days" alanı yok sayılır.
    r = client.post("/v1/ads", json={"label": "1 lt hediye", "points": [{"lat": HERE[0], "lon": HERE[1]}], "days": 7}, headers=auth(sp))
    assert r.status_code == 200, r.text
    ad = r.json()["items"][0]
    assert ad["cost"] == TABELA_COIN and ad["active"] and ad["days"] == TABELA_DAYS and ad["kind"] == "tabela"
    assert r.json()["coins"]["balance"] == 500 - TABELA_COIN and r.json()["coins"]["history"][0]["label"] == "Harita tabelası · 1 lt hediye · 1 nokta · 30 gün"
    # web ikizinde aynı reklam (eşitleme)
    assert [a["id"] for a in sync(sp)["ads"]["items"]] == [ad["id"]]
    # kurye: yakında görür, sponsor adıyla
    ku, _ = new("courier")
    near = [a for a in client.get(f"/v1/ads/active?lat={HERE[0] + 0.01}&lon={HERE[1]}", headers=auth(ku)).json()["items"] if a["sponsorId"] == sp_id]
    assert [a["id"] for a in near] == [ad["id"]] and near[0]["sponsor"] == "Akaryakıt A.Ş." and near[0]["cost"] == 0
    far = client.get("/v1/ads/active?lat=41.0&lon=29.0", headers=auth(ku)).json()["items"]
    assert ad["id"] not in [a["id"] for a in far]
    # kaldırınca iner, coin iade edilmez
    bal = balance(sp)
    assert client.delete(f"/v1/ads/{ad['id']}", headers=auth(sp)).status_code == 200
    assert balance(sp) == bal and client.get("/v1/ads/mine", headers=auth(sp)).json()["items"] == []
    assert ad["id"] not in [a["id"] for a in client.get(f"/v1/ads/active?lat={HERE[0]}&lon={HERE[1]}", headers=auth(ku)).json()["items"]]
    # başkasının reklamı kaldırılamaz (kaldırılan tabelanın yeri yeniden kullanılabilir)
    r2 = client.post("/v1/ads", json={"label": "Kahve", "points": [{"lat": HERE[0], "lon": HERE[1]}], "days": 1}, headers=auth(sp)).json()["items"][0]
    assert client.delete(f"/v1/ads/{r2['id']}", headers=auth(ku)).status_code == 404


def test_signboard_points_and_rules():
    sp, _ = new("sponsor")
    put(sp, "sponsor", {"company": "Pide Evi Ltd."})
    buy(sp, 500)
    pts = grid(1, 3)
    # eski paket / gün alanları yok sayılır: nokta başına 1 coin, 30 gün, hepsi bir grup
    r = client.post("/v1/ads", json={"label": "Pide 2. si", "points": pts, "package": "p3-7", "days": 3}, headers=auth(sp))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 3 and [a["cost"] for a in items] == [1, 1, 1] and len({a["group"] for a in items}) == 1 and items[0]["group"]
    assert {a["days"] for a in items} == {30} and {a["kind"] for a in items} == {"tabela"}
    assert r.json()["coins"]["history"][0]["label"] == "Harita tabelası · Pide 2. si · 3 nokta · 30 gün" and r.json()["coins"]["balance"] == 497
    # metin isteğe bağlı: boşsa şirket adı
    one = client.post("/v1/ads", json={"points": grid(2, 1)}, headers=auth(sp))
    assert one.status_code == 200 and one.json()["items"][0]["label"] == "Pide Evi Ltd." and one.json()["items"][0]["group"] == ""
    # aynı yere ikinci tabela olmaz; tek seferde iki nokta çok yakın olmaz; en çok 10 nokta
    assert client.post("/v1/ads", json={"points": [pts[0]]}, headers=auth(sp)).status_code == 409
    close = [{"lat": HERE[0] + 0.03, "lon": HERE[1]}, {"lat": HERE[0] + 0.03 + 0.0001, "lon": HERE[1]}]
    assert client.post("/v1/ads", json={"points": close}, headers=auth(sp)).status_code == 400
    assert client.post("/v1/ads", json={"points": grid(3, 11)}, headers=auth(sp)).status_code == 422
    assert client.post("/v1/ads", json={"points": []}, headers=auth(sp)).status_code == 400
    p = grid(4, 1)
    assert client.post("/v1/ads", json={"label": "a", "points": p}, headers=auth(sp)).status_code == 400
    assert client.post("/v1/ads", json={"label": "siktir", "points": p}, headers=auth(sp)).status_code == 400
    assert client.post("/v1/ads", json={"label": "TR33 0006 1005 1978 6457 8413 26", "points": p}, headers=auth(sp)).status_code == 400
    assert client.post("/v1/ads", json={"label": "Pide", "points": [{"lat": 91, "lon": 0}]}, headers=auth(sp)).status_code == 422
    ku, _ = new("courier")
    assert client.post("/v1/ads", json={"label": "Pide", "points": p}, headers=auth(ku)).status_code == 403
    rules = sync(sp)["rules"]
    assert rules["adPerMonth"] == 1 and rules["tabela"] == {"cost": 1, "days": 30, "maxPoints": 10, "maxActive": 50, "gapM": 30}
    assert "adPerDay" not in rules and "packages" not in rules and "adDays" not in rules


def test_not_enough_coins_writes_nothing():
    sp, sp_id = new("sponsor")
    r = client.post("/v1/ads", json={"label": "Büyük kampanya", "points": grid(5, 3)}, headers=auth(sp))
    assert r.status_code == 400 and "Yetersiz coin: 3 Rota coin daha lazım (bakiye 0)" in r.json()["detail"]
    assert balance(sp) == 0 and client.get("/v1/ads/mine", headers=auth(sp)).json()["items"] == []


def test_parallel_spends_never_go_negative():
    sp, sp_id = new("sponsor")
    buy(sp, 50)
    for k in range(4):  # 4 × 10 nokta = 40 coin → 10 kalır: yalnız BİR 10 noktalı tabelaya daha yeter
        assert client.post("/v1/ads", json={"label": "Önce", "points": grid(10 + k, 10)}, headers=auth(sp)).status_code == 200
    assert balance(sp) == 10
    codes = []

    def go(k):
        c = TestClient(app)
        codes.append(c.post("/v1/ads", json={"label": "Aynı an", "points": grid(20 + k, 10)}, headers=auth(sp)).status_code)

    ts = [threading.Thread(target=go, args=(k,)) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(codes).count(200) == 1, codes
    assert balance(sp) == 0


def test_banned_owner_ads_hidden():
    sp, sp_id = new("sponsor")
    buy(sp, 200)
    ad = client.post("/v1/ads", json={"label": "Gizlenecek", "points": [{"lat": HERE[0], "lon": HERE[1]}], "days": 1}, headers=auth(sp)).json()["items"][0]
    ku, _ = new("courier")
    db = SessionLocal()
    try:
        db.get(User, sp_id).banned = True
        db.commit()
    finally:
        db.close()
    assert ad["id"] not in [a["id"] for a in client.get(f"/v1/ads/active?lat={HERE[0]}&lon={HERE[1]}", headers=auth(ku)).json()["items"]]


# ------------------------------------------------------------------ öne çıkan ilan
def test_promote_job_on_create_and_later():
    emp, _ = new("employer")
    body = {"title": "3 adres · Mahmutlar", "lat": 36.5, "lon": 32.0, "packageCount": 3, "promote": True}
    r = client.post("/v1/jobs", json=body, headers=auth(emp))
    assert r.status_code == 400 and "Yetersiz coin" in r.json()["detail"]
    assert client.get("/v1/jobs/mine", headers=auth(emp)).json() == []  # bakiye yetmeyince ilan açılmadı
    buy(emp, 200)
    j = client.post("/v1/jobs", json=body, headers=auth(emp)).json()
    assert j["promotedUntil"] and balance(emp) == 200 - PROMO_COIN_PER_DAY
    plain = client.post("/v1/jobs", json={**body, "title": "Sonra açılan", "promote": False}, headers=auth(emp)).json()
    assert plain["promotedUntil"] is None
    ku, _ = new("courier")
    ids = [x["id"] for x in client.get("/v1/jobs/open", headers=auth(ku)).json()]
    assert ids.index(j["id"]) < ids.index(plain["id"])  # öne çıkan, daha yeni ilandan önce
    # sonradan 7 gün: süre eklenir
    r = client.post(f"/v1/jobs/{plain['id']}/promote", json={"days": 7}, headers=auth(emp))
    assert r.status_code == 200 and r.json()["coins"]["balance"] == 200 - PROMO_COIN_PER_DAY * 8
    until = r.json()["promotedUntil"]
    r2 = client.post(f"/v1/jobs/{plain['id']}/promote", json={"days": 1}, headers=auth(emp))
    assert r2.json()["promotedUntil"] - until == 86400000
    assert client.post(f"/v1/jobs/{plain['id']}/promote", json={"days": 2}, headers=auth(emp)).status_code == 400
    assert client.post(f"/v1/jobs/{plain['id']}/promote", json={"days": 1}, headers=auth(ku)).status_code == 403
    client.delete(f"/v1/jobs/{plain['id']}", headers=auth(emp))
    assert client.post(f"/v1/jobs/{plain['id']}/promote", json={"days": 1}, headers=auth(emp)).status_code == 404


# ------------------------------------------------------------------ rehber yedeği
def _guide(n=3):
    return [{"label": f"Meltem Eczane {i}", "address": "Barbaros Cad. No:227", "street": "Barbaros Cad.", "doorNumber": "227", "neighborhood": "Mahmutlar",
             "lat": 36.49 + i / 1000, "lon": 32.09, "pinVerified": True, "hitCount": 4, "createdAt": 1_790_000_000_000, "phone": "0532 000 00 00"} for i in range(n)]


def test_guide_backup_24h_owner_only():
    tok, uid_ = new("courier")
    r = client.put("/v1/me/guide-backup", json={"items": _guide(3) + [{"label": "x"}, "bozuk", {"label": "Geçerli", "lat": "nan"}]}, headers=auth(tok))
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["n"] == 4 and info["expiresAt"] - info["createdAt"] == 24 * 3600 * 1000
    assert sync(tok)["guideBackup"]["n"] == 4
    got = client.get("/v1/me/guide-backup", headers=auth(tok)).json()
    assert len(got["items"]) == 4 and "phone" not in got["items"][0] and got["items"][0]["label"] == "Meltem Eczane 0"
    assert got["items"][3]["lat"] == 0.0
    # başkası göremez (kendi yedeği yok)
    other, _ = new("courier")
    assert client.get("/v1/me/guide-backup", headers=auth(other)).status_code == 404
    # süre dolunca okunamaz ve temizlikte silinir
    db = SessionLocal()
    try:
        db.get(GuideBackup, uid_).expires_at = now() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    assert client.get("/v1/me/guide-backup", headers=auth(tok)).status_code == 404 and sync(tok)["guideBackup"] is None
    account_router.sweep(SessionLocal())
    db = SessionLocal()
    try:
        assert db.get(GuideBackup, uid_) is None
    finally:
        db.close()


def test_guide_backup_replace_delete_and_limits():
    tok, _ = new("employer")
    client.put("/v1/me/guide-backup", json={"items": _guide(5)}, headers=auth(tok))
    assert client.put("/v1/me/guide-backup", json={"items": _guide(2)}, headers=auth(tok)).json()["n"] == 2  # yenisi eskisinin yerine
    assert client.delete("/v1/me/guide-backup", headers=auth(tok)).json() == {"deleted": True}
    assert client.get("/v1/me/guide-backup", headers=auth(tok)).status_code == 404
    assert client.put("/v1/me/guide-backup", json={"items": [{"label": ""}]}, headers=auth(tok)).status_code == 400
    assert client.put("/v1/me/guide-backup", json={"items": _guide(1) * 3001}, headers=auth(tok)).status_code == 422


# ------------------------------------------------------------------ yönetim
def test_admin_coins_and_ads():
    h, _ = admin_login()
    sp, sp_id = new("sponsor")
    buy(sp, 200)
    ad = client.post("/v1/ads", json={"label": "Uygunsuz değil", "points": [{"lat": HERE[0], "lon": HERE[1]}], "days": 1}, headers=auth(sp)).json()["items"][0]
    v = client.get(f"/v1/admin/users/{sp_id}/coins", headers=h).json()
    assert v["balance"] == 200 - TABELA_COIN and [a["id"] for a in v["ads"]] == [ad["id"]]
    assert client.post(f"/v1/admin/users/{sp_id}/coins", json={"delta": 50, "note": ""}, headers=h).status_code == 400
    assert client.post(f"/v1/admin/users/{sp_id}/coins", json={"delta": -1000, "note": "fazla"}, headers=h).status_code == 400
    r = client.post(f"/v1/admin/users/{sp_id}/coins", json={"delta": 50, "note": "Yüklenmeyen satın alma"}, headers=h)
    assert r.status_code == 200 and r.json()["balance"] == 200 - TABELA_COIN + 50
    lst = client.get("/v1/admin/ads", headers=h).json()["items"]
    mine = [x for x in lst if x["id"] == ad["id"]][0]
    assert mine["owner"]["code"].startswith("SPN-") and mine["active"]
    assert client.delete(f"/v1/admin/ads/{ad['id']}", headers=h).status_code == 200
    assert ad["id"] not in [x["id"] for x in client.get("/v1/admin/ads", headers=h).json()["items"]]
    allv = [x for x in client.get("/v1/admin/ads?view=all", headers=h).json()["items"] if x["id"] == ad["id"]][0]
    assert allv["removed"] and allv["removedBy"] == "admin"
    db = SessionLocal()
    try:
        texts = [n.text for n in db.query(Notice).filter(Notice.user_id == sp_id).all()]
        assert any("+50 düzeltildi" in t for t in texts) and any("reklamınız yönetim tarafından kaldırıldı" in t for t in texts)
        acts = {a.action for a in db.query(AdminAudit).filter(AdminAudit.target_id.in_([sp_id, ad["id"]])).all()}
        assert {"coins_adjust", "ad_remove"} <= acts
        assert db.query(CoinEntry).filter(CoinEntry.user_id == sp_id, CoinEntry.kind == "admin").count() == 1
        assert db.get(AdBooking, ad["id"]).removed_at is not None
    finally:
        db.close()
    assert client.get(f"/v1/admin/users/{sp_id}/coins").status_code == 401
    assert client.get("/v1/admin/ads").status_code == 401
