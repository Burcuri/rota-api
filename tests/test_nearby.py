"""Dilim 12: haritada görünme, yakındakiler, kısıtlı selam, engelleme."""
import uuid

from helpers import client

LAT, LON = 36.5400, 32.0000


def mk(name):
    phone = "0539" + str(uuid.uuid4().int)[:7]
    r = client.post("/v1/auth/register", json={"phone": phone, "password": "deneme123", "name": name, "role": "courier"})
    assert r.status_code == 200, r.text
    return {"id": r.json()["user"]["id"], "h": {"Authorization": "Bearer " + r.json()["token"]}}


def vis(u, lat=LAT, lon=LON, on=True):
    assert client.post("/v1/map/visibility", json={"on": on}, headers=u["h"]).status_code == 200
    if on:
        assert client.post("/v1/map/ping", json={"lat": lat, "lon": lon}, headers=u["h"]).status_code == 200


def near(u, lat=LAT, lon=LON, radius=3000):
    return client.get(f"/v1/map/nearby?lat={lat}&lon={lon}&radius={radius}", headers=u["h"])


def test_default_off_and_ping_requires_consent():
    a = mk("Ali Veli")
    assert client.get("/v1/map/visibility", headers=a["h"]).json() == {"on": False}
    assert client.post("/v1/map/ping", json={"lat": LAT, "lon": LON}, headers=a["h"]).status_code == 403
    assert near(a).status_code == 403


def test_nearby_reciprocal_fuzzed_and_short_name():
    a, b, c = mk("Ayşe Yılmaz Kaya"), mk("Berk Bulut"), mk("Cem Çelik")
    client.post("/v1/me/map-icon", json={"icon": "kedi"}, headers=b["h"])
    vis(a); vis(b, LAT + 0.004, LON + 0.0041)
    vis(c, 37.5, 33.5)  # uzak
    r = near(a).json()["users"]
    ids = [x["id"] for x in r]
    assert b["id"] in ids and c["id"] not in ids and a["id"] not in ids
    x = [x for x in r if x["id"] == b["id"]][0]
    assert x["name"] == "Berk B." and x["icon"] == "kedi" and x["lat"] == round(LAT + 0.004, 3)
    assert set(x) == {"id", "name", "role", "icon", "lat", "lon", "mins", "d"}  # telefon / tam ad / e-posta YOK
    # görünmeyen kişi kimseyi göremez ve görünmez
    d = mk("Deniz Demir")
    assert near(d).status_code == 403
    vis(b, on=False)
    assert b["id"] not in [x["id"] for x in near(a).json()["users"]]


def test_nudge_rules():
    a, b, c = mk("Alp A"), mk("Bora B"), mk("Can C")
    vis(a); vis(b, LAT + 0.002, LON)
    assert client.post("/v1/map/nudge", json={"to": b["id"], "kind": "evil"}, headers=a["h"]).status_code == 400
    assert client.post("/v1/map/nudge", json={"to": a["id"], "kind": "wave"}, headers=a["h"]).status_code == 400
    r = client.post("/v1/map/nudge", json={"to": b["id"], "kind": "hello"}, headers=a["h"])
    assert r.status_code == 200
    assert client.post("/v1/map/nudge", json={"to": b["id"], "kind": "wave"}, headers=a["h"]).status_code == 429  # aynı kişiye sık
    n = client.get("/v1/map/nudges", headers=b["h"]).json()["nudges"]
    assert len(n) == 1 and n[0]["kind"] == "hello" and n[0]["fromId"] == a["id"] and n[0]["fromName"] == "Alp A."
    assert client.post("/v1/map/nudges/ack", json={"ids": [n[0]["id"]]}, headers=b["h"]).json() == {"deleted": 1}
    assert client.get("/v1/map/nudges", headers=b["h"]).json()["nudges"] == []
    # görünmeyen gönderemez / görünmeyene gönderilemez
    assert client.post("/v1/map/nudge", json={"to": b["id"], "kind": "wave"}, headers=c["h"]).status_code == 403
    vis(c, LAT, LON + 0.001)
    vis(b, on=False)
    assert client.post("/v1/map/nudge", json={"to": b["id"], "kind": "wave"}, headers=c["h"]).status_code == 403


def test_block_is_two_way():
    a, b = mk("Ece E"), mk("Fatih F")
    vis(a); vis(b, LAT + 0.001, LON)
    assert b["id"] in [x["id"] for x in near(a).json()["users"]]
    assert client.post("/v1/map/block", json={"id": b["id"]}, headers=a["h"]).status_code == 200
    assert b["id"] not in [x["id"] for x in near(a).json()["users"]]
    assert a["id"] not in [x["id"] for x in near(b).json()["users"]]
    assert client.post("/v1/map/nudge", json={"to": a["id"], "kind": "wave"}, headers=b["h"]).status_code == 403
    assert client.get("/v1/map/blocks", headers=a["h"]).json()["blocks"][0]["id"] == b["id"]
    client.post("/v1/map/unblock", json={"id": b["id"]}, headers=a["h"])
    assert b["id"] in [x["id"] for x in near(a).json()["users"]]


def test_hourly_limit():
    a = mk("Gül G"); vis(a)
    others = [mk(f"Kişi {i}") for i in range(14)]
    for o in others:
        vis(o, LAT + 0.001, LON)
    codes = [client.post("/v1/map/nudge", json={"to": o["id"], "kind": "wave"}, headers=a["h"]).status_code for o in others]
    assert codes[:12] == [200] * 12 and codes[12] == 429
