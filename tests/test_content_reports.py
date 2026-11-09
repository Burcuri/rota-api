"""1.29.0 — İÇERİK ŞİKÂYETİ (kullanıcı isteği 9 Kasım 2026: "Diğer yerlere de Şikâyet et … tabelalara şikâyet edebilmek için tıklamak ve
gelen bir pop up olsun"): harita tabelası (ad), iş ilanı (job), işletme kartı (card). Denenenler: içeriğin hâli sunucunun kendi kaydından
alınır (istemcinin yazdığına güvenilmez), içerik şikâyet edilen kişinin değilse 404, aynı içerik 24 saatte birleşir ama sohbet şikâyetiyle
karışmaz, şikâyet eden o tabelayı / ilanı artık görmez (başkası görür), engelli sahibin tabelası / ilanı / kartı görünmez, yönetim kararları
(uyar → içerik yerinde; kaldır → tabela paketinin bütün noktaları iner / ilan kapanır / logo silinir; aynı içerik hakkındaki bütün açık
şikâyetler kapanır; sahibine ve şikâyet edenlere duyuru), sohbet şikâyetinde "kaldır" yok."""
import base64
import itertools
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import account_router, app
from app.models import AdBooking, Job, Report, User

from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)
_spot = itertools.count(1)
HERE = (36.6100, 31.8800)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _clean():
    for lim in account_router.limiters:
        lim.reset()
    yield


def mk(role="courier", name=None):
    r = reg(role=role, name=name or f"İçerik {role} {uuid.uuid4().hex[:5]}").json()
    return {"tok": r["token"], "id": r["user"]["id"], "h": auth(r["token"])}


def sponsor_ad(sp, label="Pide 1 lt hediye", n=1):
    assert client.post("/v1/coins/test-buy", json={"n": 50}, headers=sp["h"]).status_code == 200
    k = next(_spot)
    pts = [{"lat": HERE[0] + k * 0.01 + i * 0.003, "lon": HERE[1]} for i in range(n)]
    r = client.post("/v1/ads", json={"label": label, "points": pts}, headers=sp["h"])
    assert r.status_code == 200, r.text
    return r.json()["items"]


def visible_ads(u):
    r = client.get(f"/v1/ads/active?lat={HERE[0]}&lon={HERE[1]}&radius=60000", headers=u["h"])
    assert r.status_code == 200, r.text
    return {a["id"] for a in r.json()["items"]}


def post_job(e, title="Akşam 3 adres"):
    r = client.post("/v1/jobs", json={"title": title, "lat": 36.55, "lon": 32.01, "packageCount": 3, "area": "Oba", "fee": 150,
                                      "note": "Pide teslimi"}, headers=e["h"])
    assert r.status_code == 200, r.text
    return r.json()


def open_jobs(u):
    return {j["id"] for j in client.get("/v1/jobs/open", headers=u["h"]).json()}


def report(u, target, place, ref="", **kw):
    body = {"targetId": target["id"], "reason": "abuse", "place": place, "ref": ref, "kind": "content"}
    body.update(kw)
    return client.post("/v1/reports", json=body, headers=u["h"])


def notices(u):
    return [n["text"] for n in client.get("/v1/notices", headers=u["h"]).json()["items"]]


def admin_item(h, rid, status="open"):
    items = client.get(f"/v1/admin/reports?status={status}&limit=300", headers=h).json()["items"]
    return next(x for x in items if x["id"] == rid)


def test_signboard_report_hides_it_for_reporter_and_admin_takes_down_whole_package():
    h, _ = admin_login()
    sp, other_sp = mk("sponsor", "Mavi Pide Sponsor"), mk("sponsor")
    ads = sponsor_ad(sp, label="Pide 1 lt hediye", n=2)  # tek paket, iki nokta
    a0 = ads[0]["id"]
    foreign = sponsor_ad(other_sp, label="Başka tabela")[0]["id"]
    c, c2 = mk("courier"), mk("courier")
    assert {a0, ads[1]["id"]} <= visible_ads(c)
    # yanlış içerik: başka sponsorun tabelası / olmayan tabela → 404; içerik şikâyetinde istemcinin "mesajları" yok sayılır
    assert report(c, sp, "ad", foreign).status_code == 404
    assert report(c, sp, "ad", "olmayan-tabela").status_code == 404
    r = report(c, sp, "ad", a0, reason="sexual", note="uygunsuz logo", messages=[{"text": "uydurma", "at": 1}])
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    db = SessionLocal()
    try:
        x = db.get(Report, rid)
        ctx = json.loads(x.context)
        assert (x.kind, x.place, x.ref, x.status) == ("content", "ad", a0, "open")
        assert ctx["messages"] == [] and ctx["content"]["label"] == "Pide 1 lt hediye" and ctx["content"]["groupId"]
    finally:
        db.close()
    # şikâyet eden o tabelayı artık görmez; aynı paketin öbür noktası ve başkası için yayında
    assert a0 not in visible_ads(c) and ads[1]["id"] in visible_ads(c)
    assert a0 in visible_ads(c2)
    # 24 saatte aynı içerik: birleşir · sohbet şikâyeti ayrı kayıt
    again = report(c, sp, "ad", a0)
    assert again.json()["id"] == rid and again.json()["again"]
    chat = client.post("/v1/reports", json={"targetId": sp["id"], "reason": "spam", "kind": "user", "place": "dm"}, headers=c["h"])
    assert chat.status_code == 200 and chat.json()["id"] != rid
    assert client.post(f"/v1/admin/reports/{chat.json()['id']}/close", json={"action": "remove"}, headers=h).status_code == 400
    rid2 = report(c2, sp, "ad", ads[1]["id"], reason="spam").json()["id"]
    # yönetim: içerik + şimdiki durum
    it = admin_item(h, rid)
    assert it["kind"] == "content" and it["place"] == "ad" and it["ref"] == a0 and it["content"]["label"] == "Pide 1 lt hediye"
    assert it["live"] == {"removed": False, "by": ""} and it["note"] == "uygunsuz logo"
    done = client.post(f"/v1/admin/reports/{rid}/close", json={"action": "remove", "note": "kurala aykırı"}, headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["action"] == "remove" and done.json()["live"]["removed"] and done.json()["live"]["by"] == "admin"
    db = SessionLocal()
    try:
        rows = db.query(AdBooking).filter(AdBooking.owner_id == sp["id"]).all()
        assert rows and all(a.removed_at is not None and a.removed_by == "admin" for a in rows)  # paketin bütün noktaları
        assert db.get(Report, rid2).status == "open"  # başka içerik (öbür nokta) kendi kararını bekler
    finally:
        db.close()
    assert not ({a0, ads[1]["id"]} & visible_ads(c2))
    assert any("Tabelan" in n and "kaldırıldı" in n for n in notices(sp))
    assert any("incelendi" in n for n in notices(c))
    assert foreign in visible_ads(c)  # başkasının tabelasına dokunulmaz


def test_job_report_warn_keeps_it_remove_closes_it():
    h, _ = admin_login()
    e = mk("employer", "Burger Ev")
    j = post_job(e)
    c, c2, c3 = mk(), mk(), mk()
    assert j["id"] in open_jobs(c)
    r = report(c, e, "job", j["id"], reason="fraud")
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert j["id"] not in open_jobs(c) and j["id"] in open_jobs(c2)
    it = admin_item(h, rid)
    assert it["content"]["title"] == "Akşam 3 adres" and it["content"]["fee"] == 150 and it["content"]["packages"] == 3
    w = client.post(f"/v1/admin/reports/{rid}/close", json={"action": "warn"}, headers=h)
    assert w.status_code == 200 and w.json()["action"] == "warn"
    assert any("İlanın hakkında" in n for n in notices(e))
    assert j["id"] in open_jobs(c2)  # uyarıda ilan yerinde
    r2 = report(c2, e, "job", j["id"]).json()["id"]
    r3 = report(c3, e, "job", j["id"], reason="spam").json()["id"]
    assert client.post(f"/v1/admin/reports/{r2}/close", json={"action": "remove"}, headers=h).status_code == 200
    db = SessionLocal()
    try:
        assert db.get(Job, j["id"]).status == "closed"
        assert db.get(Report, r3).status == "closed" and db.get(Report, r3).action == "remove"  # aynı ilan: hepsi kapandı
    finally:
        db.close()
    assert j["id"] not in open_jobs(mk())
    assert any("İlanın" in n and "kaldırıldı" in n for n in notices(e))
    assert any("incelendi" in n for n in notices(c3))


def test_business_card_report_and_logo_removal():
    h, _ = admin_login()
    e = mk("employer", "Mavi Pide")
    r = client.put("/v1/account/docs/business", json={"data": {"name": "Mavi Pide", "signboard": "Mavi Pide Oba", "category": "Gıda / Restoran",
                                                               "hours": "10:00–23:00"}, "editedAt": 1700000000000}, headers=e["h"])
    assert r.status_code == 200, r.text
    assert client.post("/v1/me/logo", json={"logo": "data:image/png;base64," + base64.b64encode(PNG).decode()}, headers=e["h"]).status_code == 200
    c = mk()
    assert report(c, mk(), "card").status_code == 404  # kurye hesabının işletme kartı yok
    rr = report(c, e, "card", "baska-kimlik")
    assert rr.status_code == 404
    rep = report(c, e, "card", reason="sexual")
    assert rep.status_code == 200, rep.text
    rid = rep.json()["id"]
    it = admin_item(h, rid)
    assert it["ref"] == e["id"] and it["content"]["signboard"] == "Mavi Pide Oba" and it["content"]["logo"] is True
    assert it["live"]["removed"] is False
    assert client.get(f"/v1/businesses/{e['id']}", headers=c["h"]).status_code == 200  # kart görünmeye devam eder (engel yok)
    done = client.post(f"/v1/admin/reports/{rid}/close", json={"action": "remove"}, headers=h)
    assert done.status_code == 200 and done.json()["live"]["removed"] is True
    assert client.get(f"/v1/logo/{e['id']}").status_code == 404
    assert any("logon" in n for n in notices(e))


def test_blocked_owner_content_is_hidden_both_ways():
    sp, e = mk("sponsor"), mk("employer", "Engelli İşletme")
    ad_id = sponsor_ad(sp, label="Engel tabelası")[0]["id"]
    j = post_job(e, "Engel ilanı")
    c, c2 = mk(), mk()
    assert ad_id in visible_ads(c) and j["id"] in open_jobs(c)
    # şikâyetle birlikte engel: sahibin bütün içeriği bu kuryeye görünmez
    assert report(c, sp, "ad", ad_id, block=True).status_code == 200
    assert report(c, e, "job", j["id"], block=True).status_code == 200
    second = sponsor_ad(sp, label="Engel tabelası 2")[0]["id"]
    j2 = post_job(e, "Engel ilanı 2")
    assert second not in visible_ads(c) and j2["id"] not in open_jobs(c)
    assert client.get(f"/v1/businesses/{e['id']}", headers=c["h"]).status_code == 404
    assert second in visible_ads(c2) and j2["id"] in open_jobs(c2)  # başkası görür
    db = SessionLocal()
    try:
        assert not db.get(User, e["id"]).banned
    finally:
        db.close()
