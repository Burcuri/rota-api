"""1.21.0: Yönetim › Kapı haritası — ortak kapı katmanının yönetim önizlemesi (özet kırılımı, noktalar, ayrıntı, silme, CSV).
Kullanıcı isteği (1 Kasım 2026): "adminin bu oluşan haritadaki yoğun doğrulanmış sokak adlarını bina numaralarını görebildiği bir harita
data base önizleme ekranı olmalı"."""
import csv
import io

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import AdminAudit, DoorPoint, DoorReport

from helpers import admin_login
from test_doors import M20, M500, area, item, person, post

client = TestClient(app)


def seed():
    """Bir mahallede: 2 kişinin onayladığı (doğrulanmış) kapı 1, tek bildirimli kapı 2, yanlış oy almış kapı 3; başka mahallede kapı 9."""
    a = area()
    t1, _ = person()
    t2, _ = person()
    t3, _ = person()
    assert post(t1, item(a, 1), item(a, 2, dlat=M500), item(a, 3, dlat=2 * M500)).status_code == 200
    assert post(t2, item(a, 1, dlat=M20)).status_code == 200
    # üçüncü kişi kapı 3'ü uzakta görür: "yanlış" oyu (tek yanlış gizlemez)
    assert post(t3, item(a, 3, dlat=2 * M500, vote="wrong")).status_code == 200
    b = dict(a, neighborhood=a["neighborhood"] + " Öbür")
    assert post(t1, item(b, 9, dlon=M500, street="Gül Sokak")).status_code == 200
    return a, b


def test_admin_only():
    for path in ("/v1/admin/doors/summary", "/v1/admin/doors", "/v1/admin/doors/export", "/v1/admin/doors/x"):
        assert client.get(path).status_code == 401
    tok, _ = person()
    assert client.get("/v1/admin/doors", headers={"Authorization": "Bearer " + tok}).status_code == 401
    assert client.delete("/v1/admin/doors/x").status_code == 401


def test_summary_drilldown_and_statuses():
    a, b = seed()
    hd, _ = admin_login()
    s = client.get("/v1/admin/doors/summary", headers=hd).json()
    assert s["level"] == "district" and s["totals"]["total"] >= 4 and s["totals"]["new7d"] >= 4
    dist = [x for x in s["items"] if x["district"] == a["district"]]
    assert len(dist) == 1 and dist[0]["total"] == 4 and dist[0]["verified"] == 1 and dist[0]["pending"] == 2 and dist[0]["disputed"] == 1
    d = dist[0]
    s2 = client.get("/v1/admin/doors/summary", params={"p": d["p"], "d": d["d"]}, headers=hd).json()
    assert s2["level"] == "neighborhood" and {x["neighborhood"] for x in s2["items"]} == {a["neighborhood"], b["neighborhood"]}
    nb = next(x for x in s2["items"] if x["neighborhood"] == a["neighborhood"])
    assert nb["total"] == 3 and nb["lat"] and nb["lon"]
    s3 = client.get("/v1/admin/doors/summary", params={"p": d["p"], "d": d["d"], "n": nb["n"]}, headers=hd).json()
    assert s3["level"] == "street" and len(s3["items"]) == 1
    st = s3["items"][0]
    assert st["street"] == "Atatürk Cad." and st["k"] == "cadde" and st["doors"] == 3 and st["verified"] == 1
    # doğrulanmış sokak / mahalle önce sıralanır
    assert s2["items"][0]["neighborhood"] == a["neighborhood"]


def test_points_filters_bbox_and_detail():
    a, b = seed()
    hd, _ = admin_login()
    d = next(x for x in client.get("/v1/admin/doors/summary", headers=hd).json()["items"] if x["district"] == a["district"])
    r = client.get("/v1/admin/doors", params={"p": d["p"], "d": d["d"]}, headers=hd).json()
    assert r["total"] == 4 and {x["door"] for x in r["items"]} == {"1", "2", "3", "9"}
    by = {x["door"]: x for x in r["items"]}
    assert by["1"]["status"] == "verified" and by["1"]["confirms"] == 2 and by["2"]["status"] == "pending" and by["3"]["status"] == "disputed"
    # kişi bilgisi yok
    assert not any(k in by["1"] for k in ("phone", "name", "reporter", "user", "flat"))
    v = client.get("/v1/admin/doors", params={"p": d["p"], "d": d["d"], "status": "verified"}, headers=hd).json()
    assert [x["door"] for x in v["items"]] == ["1"]
    # harita penceresi: yalnız kapı 1 ve 2 (kapı 3 kuzeyde, kapı 9 doğuda)
    p1 = by["1"]
    box = f"{p1['lon'] - 0.001},{p1['lat'] - 0.001},{p1['lon'] + 0.001},{p1['lat'] + 0.006}"
    w = client.get("/v1/admin/doors", params={"bbox": box, "d": d["d"]}, headers=hd).json()
    assert {x["door"] for x in w["items"]} == {"1", "2"}
    assert client.get("/v1/admin/doors", params={"bbox": "1,2,3"}, headers=hd).status_code == 400
    assert client.get("/v1/admin/doors", params={"status": "nope"}, headers=hd).status_code == 400
    assert client.get("/v1/admin/doors", params={"d": "DROP TABLE;"}, headers=hd).status_code == 400
    det = client.get(f"/v1/admin/doors/{by['3']['id']}", headers=hd).json()
    votes = {(x["vote"], x["source"]): x["count"] for x in det["votes"]}
    assert votes == {("confirm", "checkin"): 1, ("wrong", "checkin"): 1} and a["neighborhood"] in det["place"]
    assert "reporter" not in str(det)
    assert client.get("/v1/admin/doors/yok", headers=hd).status_code == 404


def test_delete_point_with_votes_and_audit():
    a, _ = seed()
    hd, name = admin_login()
    d = next(x for x in client.get("/v1/admin/doors/summary", headers=hd).json()["items"] if x["district"] == a["district"])
    pts = client.get("/v1/admin/doors", params={"p": d["p"], "d": d["d"], "status": "disputed"}, headers=hd).json()["items"]
    pid = pts[0]["id"]
    assert client.delete(f"/v1/admin/doors/{pid}", headers=hd).json() == {"ok": True}
    db = SessionLocal()
    try:
        assert db.get(DoorPoint, pid) is None and db.query(DoorReport).filter(DoorReport.point_id == pid).count() == 0
        au = db.query(AdminAudit).filter(AdminAudit.action == "door_delete", AdminAudit.target_id == pid).one()
        # işlem günlüğünde adres (sokak / kapı no) yok; yalnız mahalle ve ilçe
        assert au.admin_name == name and a["neighborhood"] in au.detail and "Atatürk" not in au.detail and "No 3" not in au.detail
    finally:
        db.close()
    assert client.delete(f"/v1/admin/doors/{pid}", headers=hd).json() == {"ok": True}  # ikinci silme zararsız


def test_csv_export_excel_friendly_and_logged():
    a, _ = seed()
    hd, _ = admin_login()
    d = next(x for x in client.get("/v1/admin/doors/summary", headers=hd).json()["items"] if x["district"] == a["district"])
    r = client.get("/v1/admin/doors/export", params={"p": d["p"], "d": d["d"]}, headers=hd)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "rota-kapi-haritasi-" in r.headers["content-disposition"]
    assert r.content.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][:6] == ["il", "ilçe", "mahalle", "sokak", "tür", "kapı no"] and len(rows) == 5
    doors = {row[5]: row for row in rows[1:]}
    assert doors["1"][9] == "doğrulanmış" and doors["2"][9] == "bekliyor" and doors["3"][9] == "tartışmalı" and doors["1"][1] == a["district"]
    db = SessionLocal()
    try:
        assert db.query(AdminAudit).filter(AdminAudit.action == "door_export", AdminAudit.detail.like("4 nokta%")).count() >= 1
    finally:
        db.close()
