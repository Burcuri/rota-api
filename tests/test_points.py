"""Puan ve ödül (1.15.0; kullanıcı kararları 26–27 Ekim 2026).

- Puan yalnız DOĞRULANMIŞ teslimden: alıcı "Paketimi aldım" dedi ya da kurye kapıda GPS ile ≤50 m. Doğrulanmış teslim = 10 puan; km puanı yok.
- Günde en çok 600 puan; aynı adrese aynı gün ikinci teslim puan getirmez; kullanılmayan puan 12 ayda düşer (ilk giren ilk çıkar).
- Ödül basamakları 1000 / 2000 / 3000 / 5000; 1 puan = 0,10 TL; ürün değeri basamağın TL karşılığının en az %80'i.
- Kurye alınca puan bloke, ödülü koyana bildirim; 7 gün içinde kargo yoksa iade; kargodan sonra 14 günde cevap yoksa teslim alındı sayılır;
  "gelmedi" yönetime düşer. Kargo adresini yalnız ödülü koyan görür; talep kapanınca silinir.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app, handoff_router, points_router
from app.models import DeliveryProof, Notice, PointEntry, Reward, RewardClaim, User, now
from app.points import tr_day

from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)
HOME = (36.5440, 31.9950)  # durak (kapı)
NEAR = (36.5442, 31.9951)  # ~25 m
FAR = (36.5480, 31.9950)  # ~450 m


@pytest.fixture(autouse=True)
def _clean():
    for lim in (*points_router.limiters,):
        lim.reset()
    yield
    for lim in (*points_router.limiters,):
        lim.reset()


def new(role, name="Kişi Deneme"):
    r = reg(role=role, name=name).json()
    return r["token"], r["user"]["id"]


def deliver(tok, ref, key=None, at=NEAR, acc=12.0, target=HOME):
    body = {"ref": ref, "addrKey": key or ("k" + ref.replace(":", "")).ljust(8, "x")}
    if at is not None:
        body.update({"lat": at[0], "lon": at[1], "acc": acc})
    if target is not None:
        body.update({"tlat": target[0], "tlon": target[1]})
    return client.post("/v1/points/delivery", json=body, headers=auth(tok))


def me(tok):
    r = client.get("/v1/points/me", headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def give_points(user_id, n, days_ago=0, kind="admin"):
    db = SessionLocal()
    try:
        db.add(PointEntry(user_id=user_id, delta=n, kind=kind, note="deneme", created_at=now() - timedelta(days=days_ago)))
        db.commit()
    finally:
        db.close()


def make_reward(tok, tier=1000, value=100, qty=3, name="Kurye yağmurluğu", brand="Akaryakıt A.Ş.", days=90):
    return client.post("/v1/rewards", json={"name": name, "tier": tier, "valueTl": value, "qty": qty, "brand": brand, "days": days}, headers=auth(tok))


SHIP = {"name": "Ayşe Kaya", "phone": "0532 123 45 67", "address": "Atatürk Mah. 12. Sk. No:5 D:3 Keçiören / Ankara"}


def claim(tok, rid, body=None):
    return client.post(f"/v1/rewards/{rid}/claim", json=body or SHIP, headers=auth(tok))


def notices(tok):
    return client.get("/v1/notices", headers=auth(tok)).json()["items"]


# ------------------------------------------------------------------ teslim → puan
def test_only_couriers_earn_and_gps_rule():
    etok, _ = new("employer")
    r = deliver(etok, "s1")
    assert r.status_code == 200 and r.json()["reason"] == "role" and r.json()["points"] == 0

    ctok, _ = new("courier")
    far = deliver(ctok, "s1", at=FAR).json()
    assert far["method"] == "none" and far["points"] == 0 and far["reason"] == "unverified"
    weak = deliver(ctok, "s2", acc=80).json()  # GPS doğruluğu kötü: doğrulanmaz
    assert weak["points"] == 0 and weak["reason"] == "unverified"
    ok = deliver(ctok, "s3").json()
    assert ok["method"] == "gps" and ok["points"] == 10 and ok["balance"] == 10 and ok["verified"] == 1 and ok["today"] == 10
    again = deliver(ctok, "s3").json()  # aynı durak ikinci kez puan almaz
    assert again["points"] == 0 and again["reason"] == "already" and again["balance"] == 10
    nogps = deliver(ctok, "s4", at=None).json()
    assert nogps["points"] == 0 and nogps["reason"] == "unverified"


def _handoff(tok, ref, lat=HOME[0], lon=HOME[1]):
    r = client.post("/v1/handoffs", json={"ref": ref, "phone": "05321234567", "lat": lat, "lon": lon, "street": "Barbaros Caddesi", "door": "5"}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _token_of(hid):
    db = SessionLocal()
    try:
        from app.models import Handoff
        return db.get(Handoff, hid).token
    finally:
        db.close()


def _recipient_confirms(tok, h):
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(tok))
    t = _token_of(h["id"])
    for lim in (getattr(handoff_router, "limiters", ()) or ()):
        lim.reset()
    r = client.post(f"/a/{t}/received", json={"checked": True})
    assert r.status_code == 200, r.text


def test_recipient_confirmation_verifies_even_far_away():
    ctok, _ = new("courier")
    h = _handoff(ctok, "r1")
    _recipient_confirms(ctok, h)
    res = deliver(ctok, "r1", at=FAR).json()
    assert res["method"] == "recipient" and res["points"] == 10


def test_recipient_confirming_later_awards_the_unverified_delivery():
    ctok, cid = new("courier")
    h = _handoff(ctok, "r2")
    res = deliver(ctok, "r2", at=FAR).json()  # önce teslim bildirildi, doğrulanamadı
    assert res["points"] == 0 and res["reason"] == "unverified"
    _recipient_confirms(ctok, h)  # sonra alıcı onayladı
    m = me(ctok)
    assert m["balance"] == 10 and m["verified"] == 1
    assert any(e["kind"] == "delivery" and "alıcı onayı" in e["note"] for e in m["history"])


def test_gps_target_prefers_recipient_pin():
    ctok, _ = new("courier")
    h = _handoff(ctok, "p1", lat=FAR[0], lon=FAR[1])  # kuryenin bağlantıdaki noktası uzakta
    t = _token_of(h["id"])
    client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(ctok))
    assert client.post(f"/a/{t}/pin", json={"lat": HOME[0], "lon": HOME[1], "accuracyM": 5}).status_code == 200  # alıcı kapıyı işaretledi
    res = deliver(ctok, "p1", target=FAR).json()  # telefonun hedefi de uzak ama alıcı pini esas
    assert res["method"] == "gps" and res["points"] == 10


def test_same_address_same_day_once_and_daily_cap():
    ctok, cid = new("courier")
    assert deliver(ctok, "a1", key="adres-ayni-1").json()["points"] == 10
    second = deliver(ctok, "a2", key="adres-ayni-1").json()
    assert second["points"] == 0 and second["reason"] == "same_addr"
    total = 10
    for i in range(70):
        res = deliver(ctok, f"c{i}", key=f"adres-farkli-{i}").json()
        total += res["points"]
    assert total == 600
    last = deliver(ctok, "c999", key="adres-farkli-999").json()
    assert last["points"] == 0 and last["reason"] == "cap" and last["today"] == 600 and last["dailyMax"] == 600


def test_points_expire_after_12_months_first_in_first_out():
    ctok, cid = new("courier")
    give_points(cid, 100, days_ago=400)  # 12 aydan eski
    give_points(cid, 30, days_ago=10)
    give_points(cid, -60, days_ago=5, kind="hold")  # harcama önce eski puandan sayılır
    m = me(ctok)
    assert m["balance"] == 30  # 100 + 30 − 60 − (100 − 60 = 40 düştü)
    assert any(e["kind"] == "expire" and e["delta"] == -40 for e in m["history"])
    assert me(ctok)["balance"] == 30  # ikinci okumada tekrar düşmez
    ctok2, cid2 = new("courier")
    give_points(cid2, 50, days_ago=350)
    assert me(ctok2)["expiringSoon"] == 50


# ------------------------------------------------------------------ ödül koyma
def test_reward_tiers_and_fair_value():
    ctok, _ = new("courier")
    assert make_reward(ctok).status_code == 403
    stok, _ = new("sponsor")
    assert make_reward(stok, tier=1500).status_code == 400
    low = make_reward(stok, tier=3000, value=200)
    assert low.status_code == 400 and "en az 240 TL" in low.json()["detail"]
    high = make_reward(stok, tier=3000, value=451)  # pahalı ürün ucuz puana konmaz ("her şey bedava verilmez")
    assert high.status_code == 400 and "en çok 450 TL" in high.json()["detail"]
    assert make_reward(stok, tier=3000, value=450, name="Sınırda ürün").status_code == 200
    assert make_reward(stok, tier=1000, value=80, name="Alt sınır ürün").status_code == 200
    ok = make_reward(stok, tier=3000, value=300)
    assert ok.status_code == 200, ok.text
    rw = ok.json()
    assert rw["cost"] == 3000 and rw["refTl"] == 300 and rw["deliveries"] == 300 and rw["owner"]["name"] == "Akaryakıt A.Ş." and rw["owner"]["code"].startswith("SPN-")
    bad = make_reward(stok, name="Ara 0532 123 45 67")
    assert bad.status_code == 400
    etok, _ = new("employer")
    assert make_reward(etok, tier=5000, value=500).status_code == 200  # işveren de ödül koyar


def test_catalog_hides_removed_out_of_stock_and_banned_owner():
    stok, sid = new("sponsor")
    a = make_reward(stok, name="Ödül A").json()
    b = make_reward(stok, name="Ödül B").json()
    ctok, _ = new("courier")
    ids = {x["id"] for x in client.get("/v1/rewards", headers=auth(ctok)).json()["items"]}
    assert {a["id"], b["id"]} <= ids
    assert client.delete(f"/v1/rewards/{a['id']}", headers=auth(stok)).status_code == 200
    ids = {x["id"] for x in client.get("/v1/rewards", headers=auth(ctok)).json()["items"]}
    assert a["id"] not in ids and b["id"] in ids
    db = SessionLocal()
    try:
        db.get(User, sid).banned = True
        db.commit()
    finally:
        db.close()
    ids = {x["id"] for x in client.get("/v1/rewards", headers=auth(ctok)).json()["items"]}
    assert b["id"] not in ids


# ------------------------------------------------------------------ talep süreci
def test_claim_holds_points_notifies_owner_and_validates_address():
    stok, sid = new("sponsor")
    rw = make_reward(stok, tier=1000, value=100, qty=2).json()
    ctok, cid = new("courier")
    r = claim(ctok, rw["id"])
    assert r.status_code == 400 and "1000 puan daha lazım" in r.json()["detail"]
    give_points(cid, 1200)
    assert claim(ctok, rw["id"], {**SHIP, "address": "kısa"}).status_code == 400
    assert claim(ctok, rw["id"], {**SHIP, "phone": "123"}).status_code == 400
    assert claim(ctok, rw["id"], {**SHIP, "name": "Ayşe"}).status_code == 400
    ok = claim(ctok, rw["id"])
    assert ok.status_code == 200, ok.text
    c = ok.json()
    assert c["status"] == "pending" and c["cost"] == 1000 and c["code"].startswith("ODL") and c["ship"] is None  # kurye kendi adresini geri almaz
    m = me(ctok)
    assert m["balance"] == 200 and m["held"] == 1000
    assert claim(ctok, rw["id"]).status_code == 409  # aynı ödül için açık talep
    mine = client.get("/v1/rewards/mine", headers=auth(stok)).json()["items"][0]
    assert mine["qtyLeft"] == 1 and mine["stats"]["pending"] == 1
    n = notices(stok)
    assert n and n[0]["kind"] == "reward.claimed" and "KRY-" in n[0]["text"] and "hak kazandı" in n[0]["text"]
    owner = client.get("/v1/rewards/claims/owner", headers=auth(stok)).json()["items"][0]
    assert owner["ship"] == {"name": "Ayşe Kaya", "phone": "05321234567", "address": SHIP["address"]} and owner["courier"]["code"].startswith("KRY-")
    other, _ = new("sponsor")
    assert client.post(f"/v1/rewards/claims/{c['id']}/ship", json={"carrier": "Yurtiçi", "tracking": "123456"}, headers=auth(other)).status_code == 404


def test_ship_answer_received_scrubs_address_and_feeds_scorecard():
    stok, sid = new("sponsor")
    rw = make_reward(stok).json()
    ctok, cid = new("courier")
    give_points(cid, 1000)
    c = claim(ctok, rw["id"]).json()
    assert client.post(f"/v1/rewards/claims/{c['id']}/ship", json={"carrier": "Yurtiçi", "tracking": "123456"}, headers=auth(ctok)).status_code == 403
    s = client.post(f"/v1/rewards/claims/{c['id']}/ship", json={"carrier": "Yurtiçi Kargo", "tracking": "YK123456"}, headers=auth(stok))
    assert s.status_code == 200 and s.json()["status"] == "shipped" and s.json()["onTime"] is True
    kn = notices(ctok)
    assert kn and kn[0]["kind"] == "reward.shipped" and "YK123456" in kn[0]["text"]
    a = client.post(f"/v1/rewards/claims/{c['id']}/answer", json={"received": True, "stars": 4}, headers=auth(ctok))
    assert a.status_code == 200 and a.json()["status"] == "received" and a.json()["stars"] == 4
    db = SessionLocal()
    try:
        row = db.get(RewardClaim, c["id"])
        assert row.ship_address == "" and row.ship_phone == "" and row.ship_name == "" and row.close_reason == "courier_received"
    finally:
        db.close()
    assert me(ctok)["held"] == 0 and me(ctok)["balance"] == 0
    assert notices(stok)[0]["kind"] == "reward.received"
    head, _ = admin_login()
    sc = {x["id"]: x for x in client.get("/v1/admin/rewards/scorecards", headers=head).json()["items"]}[sid]
    assert sc["claims"] == 1 and sc["onTimePct"] == 100 and sc["avgStars"] == 4.0


def test_not_received_goes_to_admin_who_can_refund():
    stok, sid = new("sponsor")
    rw = make_reward(stok).json()
    ctok, cid = new("courier")
    give_points(cid, 1000)
    c = claim(ctok, rw["id"]).json()
    early = client.post(f"/v1/rewards/claims/{c['id']}/answer", json={"received": False}, headers=auth(ctok))
    assert early.status_code == 409 and "kendiliğinden iade" in early.json()["detail"]
    client.post(f"/v1/rewards/claims/{c['id']}/ship", json={"carrier": "PTT", "tracking": "PT99887"}, headers=auth(stok))
    d = client.post(f"/v1/rewards/claims/{c['id']}/answer", json={"received": False, "note": "Kargo gelmedi"}, headers=auth(ctok))
    assert d.status_code == 200 and d.json()["status"] == "disputed"
    head, _ = admin_login()
    lst = client.get("/v1/admin/rewards/claims?status=disputed", headers=head).json()["items"]
    assert any(x["id"] == c["id"] and x["note"] == "Kargo gelmedi" and "address" not in x for x in lst)
    res = client.post(f"/v1/admin/rewards/claims/{c['id']}/resolve", json={"action": "refund", "note": "kargo kayıp"}, headers=head)
    assert res.status_code == 200 and res.json()["status"] == "refunded"
    assert me(ctok)["balance"] == 1000
    audit = client.get("/v1/admin/audit", headers=head).json()
    rows = audit["items"] if isinstance(audit, dict) else audit
    assert any(x.get("action") == "reward_resolve" for x in rows)


def test_cancel_before_shipping_refunds_courier_side_returns_stock():
    stok, _ = new("sponsor")
    rw = make_reward(stok, qty=1).json()
    ctok, cid = new("courier")
    give_points(cid, 2000)
    c = claim(ctok, rw["id"]).json()
    assert client.get("/v1/rewards/mine", headers=auth(stok)).json()["items"][0]["qtyLeft"] == 0
    x = client.post(f"/v1/rewards/claims/{c['id']}/cancel", headers=auth(ctok))
    assert x.status_code == 200 and x.json()["status"] == "cancelled"
    assert me(ctok)["balance"] == 2000
    assert client.get("/v1/rewards/mine", headers=auth(stok)).json()["items"][0]["qtyLeft"] == 1  # kurye vazgeçti: stok geri
    c2 = claim(ctok, rw["id"]).json()
    y = client.post(f"/v1/rewards/claims/{c2['id']}/cancel", headers=auth(stok))  # ödülü koyan gönderemiyor (stok bitti)
    assert y.status_code == 200 and y.json()["closeReason"] == "owner_cancel"
    assert me(ctok)["balance"] == 2000
    assert client.get("/v1/rewards/mine", headers=auth(stok)).json()["items"][0]["qtyLeft"] == 0


def test_sweep_refunds_late_shipping_and_auto_receives():
    stok, _ = new("sponsor")
    rw = make_reward(stok, qty=5).json()
    ctok, cid = new("courier")
    give_points(cid, 3000)
    late = claim(ctok, rw["id"]).json()
    ctok2, cid2 = new("courier")
    give_points(cid2, 1000)
    shipped = claim(ctok2, rw["id"]).json()
    client.post(f"/v1/rewards/claims/{shipped['id']}/ship", json={"carrier": "Aras", "tracking": "AR12345"}, headers=auth(stok))
    db = SessionLocal()
    try:
        db.get(RewardClaim, late["id"]).ship_due_at = now() - timedelta(minutes=1)
        db.get(RewardClaim, shipped["id"]).answer_due_at = now() - timedelta(minutes=1)
        db.commit()
        n = points_router.sweep(db)
        assert n["lateRefund"] == 1 and n["autoReceived"] == 1
        a, b = db.get(RewardClaim, late["id"]), db.get(RewardClaim, shipped["id"])
        assert a.status == "refunded" and a.close_reason == "late_ship" and a.ship_address == ""
        assert b.status == "received" and b.close_reason == "auto_received" and b.ship_address == ""
        assert db.get(Reward, rw["id"]).qty_left == 4  # 5 − 2 + 1 (geç kalan geri)
    finally:
        db.close()
    assert me(ctok)["balance"] == 3000
    assert any(x["kind"] == "reward.refunded" for x in notices(ctok))
    assert any(x["kind"] == "reward.late" for x in notices(stok))


def test_notices_ack_deletes_and_are_private():
    stok, _ = new("sponsor")
    rw = make_reward(stok).json()
    ctok, cid = new("courier")
    give_points(cid, 1000)
    claim(ctok, rw["id"])
    items = notices(stok)
    assert items
    other, _ = new("sponsor")
    assert client.post("/v1/notices/ack", json={"ids": [items[0]["id"]]}, headers=auth(other)).json()["deleted"] == 0
    assert client.post("/v1/notices/ack", json={"ids": [items[0]["id"]]}, headers=auth(stok)).json()["deleted"] == 1
    assert notices(stok) == []


def test_admin_watch_flags_clustered_deliveries_and_adjusts_points():
    ctok, cid = new("courier")
    for i in range(16):
        deliver(ctok, f"w{i}", key=f"kume-adres-{i}")  # hepsi aynı ~110 m hücresinde
    head, _ = admin_login()
    w = {x["id"]: x for x in client.get("/v1/admin/points/watch?days=7", headers=head).json()["items"]}[cid]
    assert w["verified"] == 16 and "aynı yerde toplanma" in w["flags"] and w["topCellShare"] == 1.0
    adj = client.post(f"/v1/admin/users/{cid}/points", json={"delta": -60, "note": "hileli teslimler"}, headers=head)
    assert adj.status_code == 200 and adj.json()["balance"] == 100
    assert any(x["kind"] == "points.admin" for x in notices(ctok))
    det = client.get(f"/v1/admin/users/{cid}/points", headers=head).json()
    assert det["balance"] == 100 and det["history"][0]["kind"] == "admin"


def test_proofs_store_no_address_and_are_deleted_after_30_days():
    ctok, cid = new("courier")
    deliver(ctok, "z1", key="ozet-anahtar-1")
    db = SessionLocal()
    try:
        p = db.query(DeliveryProof).filter(DeliveryProof.courier_id == cid).first()
        cols = {c.name for c in DeliveryProof.__table__.columns}
        assert not {"address", "street", "customer", "phone", "lat", "lon"} & cols  # adres / kişi / tam konum yok
        assert p.cell == "36.544,31.995" and p.day == tr_day()
        p.created_at = now() - timedelta(days=31)
        db.commit()
        assert points_router.sweep(db)["proofs"] >= 1
        assert db.query(DeliveryProof).filter(DeliveryProof.courier_id == cid).count() == 0
    finally:
        db.close()
    assert me(ctok)["verified"] == 1  # sayaç defterden gelir: kalıcı


def test_late_report_counts_on_delivery_day_within_72_hours():
    """İnternetsiz bekleyen teslim bildirimi sonradan gelirse TESLİM gününe yazılır (tavan atlatılmaz); 72 saatten eskisi 72 saate sıkışır."""
    import time as _t

    tok, cid = new("courier")
    yday_ms = int((_t.time() - 26 * 3600) * 1000)
    r = client.post("/v1/points/delivery", headers=auth(tok), json={"ref": "late1", "addrKey": "klate1xx", "lat": NEAR[0], "lon": NEAR[1], "acc": 10, "tlat": HOME[0], "tlon": HOME[1], "at": yday_ms})
    assert r.status_code == 200 and r.json()["points"] == 10, r.text
    assert r.json()["today"] == 0  # dünkü teslim bugünün tavanına sayılmaz
    old_ms = int((_t.time() - 10 * 86400) * 1000)
    future_ms = int((_t.time() + 5 * 86400) * 1000)
    r2 = client.post("/v1/points/delivery", headers=auth(tok), json={"ref": "late2", "addrKey": "klate2xx", "lat": NEAR[0], "lon": NEAR[1], "acc": 10, "tlat": HOME[0], "tlon": HOME[1], "at": old_ms})
    r3 = client.post("/v1/points/delivery", headers=auth(tok), json={"ref": "late3", "addrKey": "klate3xx", "lat": NEAR[0], "lon": NEAR[1], "acc": 10, "tlat": HOME[0], "tlon": HOME[1], "at": future_ms})
    assert r2.json()["points"] == 10 and r3.json()["points"] == 10
    assert r3.json()["today"] == 10  # ileri tarih = şimdi
    db = SessionLocal()
    try:
        days = {p.ref: p.day for p in db.query(DeliveryProof).filter(DeliveryProof.courier_id == cid).all()}
    finally:
        db.close()
    from datetime import datetime, timezone
    assert days["late1"] == tr_day(datetime.fromtimestamp(yday_ms / 1000, tz=timezone.utc))
    assert days["late2"] == tr_day(now() - timedelta(hours=72))
    assert days["late3"] == tr_day()
