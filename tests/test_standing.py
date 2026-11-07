"""Ödül koyanın karnesi ve görünürlüğü + yönetim işaretleri (1.16.0; kullanıcı kararı 27 Ekim 2026).

- Reklam ile hediye bağlanmaz; sponsoru ödüllendirmenin yolu GÖRÜNÜRLÜK: güvenilir (kendiliğinden) üstte + rozet, öne çıkan (yönetim) en üstte.
- Düzensiz (90 günde 2 geç kargo / 2 "gelmedi" / düşük memnuniyet): ödülleri katalogdan iner, yeni ödül koyamaz; yönetim affedebilir.
- Kara liste (not zorunlu, yalnız yönetim görür): kurye ödül alamaz; sponsor / işveren ödül koyamaz, ödülleri görünmez.
"""
import secrets
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app, points_router
from app.models import AdminAudit, Notice, RewardClaim, User, UserMark, now

from helpers import admin_login
from test_auth_consent import auth
from test_points import SHIP, claim, give_points, make_reward, me, new

client = TestClient(app)


def _reset():
    for lim in points_router.limiters:
        lim.reset()


def add_claims(owner_id, courier_id, reward_id, n, **kw):
    """Karne için doğrudan talep satırı yazar (status, close_reason, on_time, stars, shipped, days_ago)."""
    db = SessionLocal()
    try:
        for _ in range(n):
            t = now() - timedelta(days=kw.get("days_ago", 1))
            db.add(RewardClaim(
                code="T" + secrets.token_hex(4).upper(), reward_id=reward_id, owner_id=owner_id, courier_id=courier_id, reward_name="Ürün",
                brand="Marka", cost=1000, status=kw.get("status", "received"), close_reason=kw.get("close_reason", "courier_received"),
                created_at=t, ship_due_at=t + timedelta(days=7), shipped_at=(t + timedelta(days=1)) if kw.get("shipped", True) else None,
                on_time=kw.get("on_time", True), stars=kw.get("stars"), closed_at=t + timedelta(days=3),
            ))
        db.commit()
    finally:
        db.close()


def catalog(tok):
    r = client.get("/v1/rewards", headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def owner_of(items, oid):
    return [x for x in items if x["owner"]["id"] == oid]


def code_of(uid_):
    db = SessionLocal()
    try:
        return db.get(User, uid_).public_no
    finally:
        db.close()


def audits(target):
    db = SessionLocal()
    try:
        return [a.action for a in db.query(AdminAudit).filter(AdminAudit.target_id == target).all()]
    finally:
        db.close()


def test_trusted_owner_gets_badge_and_ranks_above_normal():
    _reset()
    ctok, cid = new("courier")
    s1, s1id = new("sponsor", "Normal Firma")
    s2, s2id = new("sponsor", "Özenli Firma")
    r1 = make_reward(s1, tier=1000, value=100, name="Normal ödül").json()
    r2 = make_reward(s2, tier=3000, value=300, name="Özenli ödül").json()  # pahalı basamak: eski sırada sonra gelirdi
    add_claims(s2id, cid, r2["id"], 3, stars=5)
    items = catalog(ctok)
    mine = [x["id"] for x in items if x["id"] in (r1["id"], r2["id"])]
    assert mine == [r2["id"], r1["id"]], "güvenilir ödül koyan üstte"
    assert owner_of(items, s2id)[0]["owner"]["trusted"] is True and owner_of(items, s1id)[0]["owner"]["trusted"] is False
    st = client.get("/v1/rewards/mine", headers=auth(s2)).json()["standing"]
    assert st["level"] == "trusted" and st["shipped"] == 3 and st["onTimePct"] == 100 and st["avgStars"] == 5.0
    st1 = client.get("/v1/rewards/mine", headers=auth(s1)).json()["standing"]
    assert st1["level"] == "normal" and st1["trustNeed"]["shipped"] == 3


def test_low_stars_or_late_history_is_not_trusted():
    _reset()
    ctok, cid = new("courier")
    s, sid = new("sponsor")
    rw = make_reward(s).json()
    add_claims(sid, cid, rw["id"], 3, stars=3)  # memnuniyet 3 < 4
    st = client.get("/v1/rewards/mine", headers=auth(s)).json()["standing"]
    assert st["level"] == "normal" and not st["trusted"] and not st["irregular"]


def test_irregular_owner_is_hidden_cannot_add_and_admin_can_pardon():
    _reset()
    ctok, cid = new("courier")
    give_points(cid, 5000)
    s, sid = new("sponsor", "Geciken Firma")
    rw = make_reward(s, name="Geciken ödül").json()
    add_claims(sid, cid, rw["id"], 2, status="refunded", close_reason="late_ship", shipped=False, on_time=False, days_ago=20)
    assert owner_of(catalog(ctok), sid) == [], "düzensiz: katalogdan iner"
    st = client.get("/v1/rewards/mine", headers=auth(s)).json()["standing"]
    assert st["level"] == "hidden" and st["irregular"] and "Son 90 günde 2 ödül 7 gün içinde kargolanmadı" in st["reasons"]
    again = make_reward(s, name="Yeni ödül")
    assert again.status_code == 409 and "katalogda gösterilmiyor" in again.json()["detail"]
    assert claim(ctok, rw["id"]).status_code == 404
    head, _ = admin_login()
    lst = client.get("/v1/admin/rewards/owners?view=irregular", headers=head).json()["items"]
    row = next(x for x in lst if x["id"] == sid)
    assert row["standing"]["hidden"] and row["code"].startswith("SPN-") and row["rewards"] == 1
    p = client.post(f"/v1/admin/users/{sid}/pardon", json={"days": 30}, headers=head)
    assert p.status_code == 200 and p.json()["standing"]["level"] == "normal" and p.json()["standing"]["pardoned"]
    assert len(owner_of(catalog(ctok), sid)) == 1, "affedilince görünür"
    assert claim(ctok, rw["id"]).status_code == 200
    client.post(f"/v1/admin/users/{sid}/pardon", json={"days": 0}, headers=head)
    assert owner_of(catalog(ctok), sid) == []
    assert audits(sid).count("sponsor_pardon") == 2


def test_old_late_shipments_age_out_after_90_days():
    _reset()
    ctok, cid = new("courier")
    s, sid = new("sponsor")
    rw = make_reward(s).json()
    add_claims(sid, cid, rw["id"], 2, status="refunded", close_reason="late_ship", shipped=False, on_time=False, days_ago=95)
    st = client.get("/v1/rewards/mine", headers=auth(s)).json()["standing"]
    assert not st["irregular"] and st["late"] == 2 and st["late90"] == 0
    assert len(owner_of(catalog(ctok), sid)) == 1


def test_disputes_make_owner_irregular():
    _reset()
    ctok, cid = new("courier")
    s, sid = new("employer")
    rw = make_reward(s).json()
    add_claims(sid, cid, rw["id"], 2, status="refunded", close_reason="admin_refund")
    st = client.get("/v1/rewards/mine", headers=auth(s)).json()["standing"]
    assert st["hidden"] and any("almadım" in x for x in st["reasons"])


def test_admin_feature_puts_owner_first_with_notice_and_audit():
    _reset()
    ctok, _ = new("courier")
    s1, s1id = new("sponsor")
    s2, s2id = new("sponsor")
    make_reward(s1, tier=1000, value=100)
    r2 = make_reward(s2, tier=5000, value=500).json()
    head, aname = admin_login()
    f = client.post(f"/v1/admin/users/{s2id}/feature", json={"days": 7}, headers=head)
    assert f.status_code == 200 and f.json()["standing"]["featured"] and f.json()["mark"]["featuredBy"] == aname
    items = catalog(ctok)
    assert items[0]["id"] == r2["id"] and items[0]["owner"]["featured"] is True
    db = SessionLocal()
    try:
        assert any(n.kind == "reward.featured" and "öne çıkarıldı" in n.text for n in db.query(Notice).filter(Notice.user_id == s2id).all())
    finally:
        db.close()
    assert client.post(f"/v1/admin/users/{s2id}/feature", json={"days": 91}, headers=head).status_code == 422
    client.post(f"/v1/admin/users/{s2id}/feature", json={"days": 0}, headers=head)
    assert not owner_of(catalog(ctok), s2id)[0]["owner"]["featured"]
    assert audits(s2id).count("sponsor_feature") == 2


def test_blacklist_owner_hidden_cannot_add_note_private_and_removable():
    _reset()
    ctok, cid = new("courier")
    s, sid = new("sponsor", "Sorunlu Firma")
    rw = make_reward(s).json()
    head, aname = admin_login()
    assert client.post("/v1/admin/blacklist", json={"code": "SPN-" + code_of(sid), "note": ""}, headers=head).status_code == 400
    add = client.post("/v1/admin/blacklist", json={"code": "SPN-" + code_of(sid), "note": "Sahte ürün ilanı şikâyeti"}, headers=head)
    assert add.status_code == 200 and add.json()["blacklisted"] and add.json()["note"] == "Sahte ürün ilanı şikâyeti" and add.json()["listedBy"] == aname
    assert owner_of(catalog(ctok), sid) == []
    blocked = make_reward(s, name="Yeni")
    assert blocked.status_code == 403 and "yönetim tarafından durduruldu" in blocked.json()["detail"]
    mine = client.get("/v1/rewards/mine", headers=auth(s)).json()["standing"]
    assert mine["level"] == "blacklisted" and mine["reasons"] == ["Yönetim ödül koymanı durdurdu"], "not ödül koyana gösterilmez"
    lst = client.get("/v1/admin/blacklist", headers=head).json()["items"]
    assert any(x["id"] == sid and x["note"] == "Sahte ürün ilanı şikâyeti" for x in lst)
    users = client.get("/v1/admin/users?banned=bl", headers=head).json()
    assert any(u["id"] == sid and u["blacklisted"] for u in users["items"])
    det = client.get(f"/v1/admin/users/{sid}", headers=head).json()
    assert det["mark"]["blacklisted"] and det["mark"]["note"] == "Sahte ürün ilanı şikâyeti" and det["standing"]["level"] == "blacklisted"
    n2 = client.put(f"/v1/admin/users/{sid}/note", json={"note": "Görüşüldü, ürün değişti"}, headers=head)
    assert n2.status_code == 200 and n2.json()["note"] == "Görüşüldü, ürün değişti"
    assert client.put(f"/v1/admin/users/{sid}/note", json={"note": ""}, headers=head).status_code == 400
    rm = client.delete(f"/v1/admin/blacklist/{sid}", headers=head)
    assert rm.status_code == 200 and not rm.json()["blacklisted"] and rm.json()["note"] == "Görüşüldü, ürün değişti", "not geçmiş olarak kalır"
    assert len(owner_of(catalog(ctok), sid)) == 1 and rw["id"] in [x["id"] for x in catalog(ctok)]
    assert not any(x["id"] == sid for x in client.get("/v1/admin/blacklist", headers=head).json()["items"])
    assert audits(sid) == ["blacklist_add", "user_note", "blacklist_remove"] or sorted(audits(sid)) == sorted(["blacklist_add", "user_note", "blacklist_remove"])


def test_blacklisted_courier_cannot_claim_but_still_earns():
    _reset()
    ctok, cid = new("courier")
    give_points(cid, 3000)
    s, _ = new("sponsor")
    rw = make_reward(s).json()
    head, _ = admin_login()
    client.post("/v1/admin/blacklist", json={"userId": cid, "note": "Aynı yerde toplanma"}, headers=head)
    assert me(ctok)["rewardsBlocked"] is True
    r = claim(ctok, rw["id"])
    assert r.status_code == 403 and "Ödül alma şu an durduruldu" in r.json()["detail"]
    w = client.get("/v1/admin/rewards/owners?view=blacklisted", headers=head).json()["items"]
    assert not any(x["id"] == cid for x in w), "kara listedeki kurye ödül koyanlar listesinde değil"
    client.delete(f"/v1/admin/blacklist/{cid}", headers=head)
    assert me(ctok)["rewardsBlocked"] is False and claim(ctok, rw["id"]).status_code == 200


def test_blacklist_by_phone_and_unknown_and_requires_admin():
    _reset()
    tok, uid_ = new("employer")
    db = SessionLocal()
    try:
        phone = db.get(User, uid_).phone
    finally:
        db.close()
    head, _ = admin_login()
    assert client.post("/v1/admin/blacklist", json={"code": phone, "note": "Ödeme yapmadı"}, headers=head).json()["id"] == uid_
    assert client.post("/v1/admin/blacklist", json={"code": "KRY-0000000", "note": "yok"}, headers=head).status_code == 404
    assert client.get("/v1/admin/blacklist").status_code == 401
    assert client.post("/v1/admin/blacklist", json={"userId": uid_, "note": "x y z"}, headers=auth(tok)).status_code == 401
    db = SessionLocal()
    try:
        m = db.get(UserMark, uid_)
        assert m.blacklisted and m.note == "Ödeme yapmadı"
    finally:
        db.close()


def test_owner_list_views_and_order():
    _reset()
    ctok, cid = new("courier")
    good, gid = new("sponsor", "İyi Firma")
    rw = make_reward(good).json()
    add_claims(gid, cid, rw["id"], 3, stars=5)
    head, _ = admin_login()
    trusted = client.get("/v1/admin/rewards/owners?view=trusted", headers=head).json()["items"]
    assert any(x["id"] == gid and x["standing"]["level"] == "trusted" for x in trusted)
    allv = client.get("/v1/admin/rewards/owners", headers=head).json()["items"]
    levels = [x["standing"]["level"] for x in allv]
    order = {"blacklisted": 0, "hidden": 1, "featured": 2, "trusted": 3, "normal": 4}
    assert levels == sorted(levels, key=lambda l: order[l]), "sıra: kara liste, gizli, öne çıkan, güvenilir, normal"
    assert client.get("/v1/admin/rewards/owners?view=xyz", headers=head).status_code == 422
