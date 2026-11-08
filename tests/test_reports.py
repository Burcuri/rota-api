"""1.28.0 — Sohbette ŞİKÂYET + her yerde ENGEL (kullanıcı isteği 9 Kasım 2026: "Sohbete Şikâyet et + Engelle … lütfen bunu bitir";
App Store 1.2 ve Google Play UGC şartı). Denenenler: şikâyet kaydı (mesajlar şikâyet edenden gelir, kırpılır, en çok 6), aynı kişiyi 24 saatte
yeniden şikâyet etmek yeni kayıt açmaz, şikâyetle engel, engel iki yönde mesajı keser (birebir + grup, gönderene söylenmez), engelden önce
bekleyen mesaj gelmez, takip / görev isteği engelde reddedilir, engel açık takibi ve görevi kapatır, yönetim listesi ve üç karar
(işlem yok / uyar / hesabı kapat), duyurular, sayılar, 180 gün saklama, olay günlüğüne içerik yazılmaması."""
import json
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Event, Follow, MapBlock, Notice, Report, Task, User, now

from helpers import admin_login
from test_auth_consent import auth, reg
from test_follow import start

client = TestClient(app)


def mk(role="courier", name=None):
    r = reg(role=role, name=name or f"Deneme {uuid.uuid4().hex[:5]}").json()
    return {"tok": r["token"], "id": r["user"]["id"], "h": auth(r["token"])}


def dev(u):
    return "cihaz-" + u["id"][:12]


def send(u, to, text="merhaba"):
    body = json.dumps({"v": 1, "mid": uuid.uuid4().hex[:10], "thread": {"id": "dm", "kind": "dm", "members": []}, "text": text})
    return client.post("/v1/relay", json={"to": to, "body": body, "deviceId": dev(u)}, headers=u["h"])


def inbox(u):
    r = client.get(f"/v1/relay?deviceId={dev(u)}", headers=u["h"])
    assert r.status_code == 200, r.text
    got = r.json()
    if got:
        client.post("/v1/relay/ack", json={"deviceId": dev(u), "ids": [m["id"] for m in got]}, headers=u["h"])
    return [json.loads(m["body"])["text"] for m in got]


def report(u, target, **kw):
    body = {"targetId": target["id"], "reason": "abuse", "kind": "message", "place": "dm", "messages": [{"text": "kötü söz", "at": 1}]}
    body.update(kw)
    return client.post("/v1/reports", json=body, headers=u["h"])


def notices(u):
    return [n["text"] for n in client.get("/v1/notices", headers=u["h"]).json()["items"]]


def test_report_validation_trim_and_merge():
    a, b = mk(), mk()
    assert report(a, a).status_code == 400
    assert report(a, {"id": "olmayan-kisi"}).status_code == 404
    assert client.post("/v1/reports", json={"targetId": b["id"], "reason": "baska"}, headers=a["h"]).status_code == 422
    assert client.post("/v1/reports", json={"targetId": b["id"], "reason": "spam"}).status_code == 401
    msgs = [{"text": f"  mesaj {i}\x00\x07 ", "at": 1000 + i} for i in range(9)] + [{"text": "   ", "at": 5}]
    r = report(a, b, messages=msgs, note="x" * 400, group="Ekip " * 30, place="group")
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    db = SessionLocal()
    try:
        x = db.get(Report, rid)
        ctx = json.loads(x.context)
        assert [m["text"] for m in ctx["messages"]] == [f"mesaj {i}" for i in range(6)]  # en çok 6, kontrol karakteri yok
        assert len(x.note) == 300 and len(ctx["group"]) <= 80 and x.place == "group" and x.status == "open" and not x.blocked
        ev = db.query(Event).filter(Event.kind == "report.new", Event.user_id == a["id"]).first()
        assert ev is not None and "mesaj" not in (ev.meta or "")  # olay günlüğüne içerik yazılmaz
    finally:
        db.close()
    # 24 saat içinde aynı kişi: yeni kayıt açılmaz, yeni mesaj eklenir, aynısı iki kez eklenmez
    again = report(a, b, messages=[{"text": "mesaj 0", "at": 1000}, {"text": "yeni hakaret", "at": 3000}])
    assert again.status_code == 200 and again.json()["id"] == rid and again.json()["again"]
    db = SessionLocal()
    try:
        ctx = json.loads(db.get(Report, rid).context)
        assert [m["text"] for m in ctx["messages"]][-1] == "yeni hakaret" and len(ctx["messages"]) == 7
        assert db.query(Report).filter(Report.reporter_id == a["id"]).count() == 1
    finally:
        db.close()


def test_block_cuts_messages_both_ways_and_hides_it():
    a, b, c = mk(), mk(), mk()
    assert send(b, [a["id"]], "engelden önce").status_code == 200  # a'nın kutusunda bekliyor
    r = report(a, b, block=True)
    assert r.status_code == 200
    db = SessionLocal()
    try:
        assert db.query(MapBlock).filter(MapBlock.user_id == a["id"], MapBlock.blocked_id == b["id"]).count() == 1
        assert db.get(Report, r.json()["id"]).blocked
    finally:
        db.close()
    assert inbox(a) == []  # engelden önce bekleyen mesaj da gelmez
    s = send(b, [a["id"]], "engelden sonra")
    assert s.status_code == 200 and a["id"] in s.json()["recipients"]  # gönderene söylenmez
    assert inbox(a) == []
    assert send(a, [b["id"]], "ben de yazamam").status_code == 200
    assert inbox(b) == []  # iki yönde
    g = send(b, [a["id"], c["id"]], "grup mesajı")
    assert g.status_code == 200 and sorted(g.json()["recipients"]) == sorted([a["id"], c["id"]])
    assert inbox(a) == [] and inbox(c) == ["grup mesajı"]  # ortak grupta da yalnız engelsiz üye alır
    assert client.post("/v1/map/unblock", json={"id": b["id"]}, headers=a["h"]).status_code == 200
    send(b, [a["id"]], "engel kalktı")
    assert inbox(a) == ["engel kalktı"]


def test_block_stops_follow_and_task_and_refuses_new_ones():
    e, c = mk("employer"), mk("courier")
    fid = start(e["tok"], c["tok"], c["id"])
    assert client.post("/v1/map/block", json={"id": e["id"]}, headers=c["h"]).status_code == 200  # kurye işvereni engelledi
    db = SessionLocal()
    try:
        f = db.get(Follow, fid)
        assert f.status == "ended" and f.end_reason == "blocked" and f.last_lat is None  # canlı konum hemen silinir
        assert db.query(Task).filter(Task.employer_id == e["id"], Task.courier_id == c["id"], Task.status != "ended").count() == 0
        assert db.query(Event).filter(Event.kind == "user.block", Event.user_id == c["id"]).count() == 1
    finally:
        db.close()
    r = client.post("/v1/follow/request", json={"courierId": c["id"]}, headers=e["h"])
    assert r.status_code == 403 and "engel" not in r.json()["detail"].lower()  # kime engellendiği söylenmez
    t = client.post("/v1/tasks/share", json={"courierId": c["id"], "ref": "L:1", "title": "Liste", "count": 2}, headers=e["h"])
    assert t.status_code == 403
    # tek "engel" kaydı: ikinci basış yeni satır açmaz
    client.post("/v1/map/block", json={"id": e["id"]}, headers=c["h"])
    db = SessionLocal()
    try:
        assert db.query(MapBlock).filter(MapBlock.user_id == c["id"], MapBlock.blocked_id == e["id"]).count() == 1
    finally:
        db.close()


def test_admin_list_close_none_warn_ban_and_counts():
    h, name = admin_login()
    t = mk("courier", "Şikâyetli Kurye")
    r1, r2, r3 = mk("employer"), mk("employer"), mk("courier")
    id1 = report(r1, t, reason="abuse").json()["id"]
    id2 = report(r2, t, reason="threat", messages=[{"text": "tehdit mesajı", "at": 5}]).json()["id"]
    id3 = report(r3, t, reason="spam", kind="user", messages=[]).json()["id"]
    st = client.get("/v1/admin/stats", headers=h).json()
    assert st["reportsOpen"] >= 3 and st["reportsOldestH"] >= 0
    assert client.get("/v1/admin/reports").status_code == 401
    lst = client.get("/v1/admin/reports?status=open&limit=300", headers=h).json()
    mine = [x for x in lst["items"] if x["target"]["id"] == t["id"]]
    assert {x["id"] for x in mine} == {id1, id2, id3} and lst["openCount"] >= 3 and lst["keepDays"] == 180
    x2 = next(x for x in mine if x["id"] == id2)
    assert x2["reasonText"] == "Tehdit ya da şiddet" and x2["messages"][0]["text"] == "tehdit mesajı" and x2["targetReporters90d"] == 3
    assert x2["target"]["name"] == "Şikâyetli Kurye" and x2["reporter"]["id"] == r2["id"] and x2["target"]["code"].startswith("KRY-")
    # 1) işlem yok: şikâyet edene "incelendi", şikâyet edilene bir şey gitmez
    a1 = client.post(f"/v1/admin/reports/{id1}/close", json={"action": "none", "note": "hafif"}, headers=h)
    assert a1.status_code == 200 and a1.json()["status"] == "closed" and a1.json()["action"] == "none" and a1.json()["closedBy"] == name
    assert any("incelendi" in n for n in notices(r1)) and notices(t) == []
    assert client.post(f"/v1/admin/reports/{id1}/close", json={"action": "none"}, headers=h).status_code == 409
    # 2) uyar: şikâyet edilene uyarı duyurusu (kimin şikâyet ettiği yazmaz)
    a2 = client.post(f"/v1/admin/reports/{id2}/close", json={"action": "warn"}, headers=h)
    assert a2.status_code == 200 and a2.json()["action"] == "warn"
    w = notices(t)
    assert len(w) == 1 and "şikâyet" in w[0] and "Deneme" not in w[0]
    # 3) hesabı kapat: hesap men edilir, bu kişinin bütün açık şikâyetleri kapanır
    id4 = report(mk(), t, reason="fraud").json()["id"]
    a3 = client.post(f"/v1/admin/reports/{id3}/close", json={"action": "ban", "note": "tekrar"}, headers=h)
    assert a3.status_code == 200
    db = SessionLocal()
    try:
        u = db.get(User, t["id"])
        assert u.banned and "şikâyet" in (u.banned_reason or "")
        assert db.get(Report, id4).status == "closed" and db.get(Report, id4).action == "ban"
    finally:
        db.close()
    assert client.get("/v1/me", headers=t["h"]).status_code in (401, 403)  # oturumu kapandı
    closed = client.get("/v1/admin/reports?status=closed", headers=h).json()["items"]
    assert {id1, id2, id3, id4} <= {x["id"] for x in closed}
    audit = client.get("/v1/admin/audit?limit=50", headers=h)
    if audit.status_code == 200:
        assert any(x.get("action") == "report_close" for x in audit.json().get("items", []))


def test_closed_reports_deleted_after_180_days():
    h, _ = admin_login()
    a, b = mk(), mk()
    rid = report(a, b).json()["id"]
    client.post(f"/v1/admin/reports/{rid}/close", json={"action": "none"}, headers=h)
    db = SessionLocal()
    try:
        db.get(Report, rid).closed_at = now() - timedelta(days=181)
        db.commit()
    finally:
        db.close()
    client.get("/v1/admin/reports?status=closed", headers=h)
    db = SessionLocal()
    try:
        assert db.get(Report, rid) is None
        assert db.query(Notice).filter(Notice.user_id == a["id"]).count() >= 1  # duyuru kendi süresiyle silinir
    finally:
        db.close()
