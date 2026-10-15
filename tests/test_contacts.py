"""Dilim 10: numaradan / rehberden ROTA üyesi bulma."""
import uuid

from helpers import client


def mk(name, phone=None):
    phone = phone or "0538" + str(uuid.uuid4().int)[:7]
    r = client.post("/v1/auth/register", json={"phone": phone, "password": "deneme123", "name": name, "role": "courier"})
    assert r.status_code == 200, r.text
    return {"phone": phone, "id": r.json()["user"]["id"], "h": {"Authorization": "Bearer " + r.json()["token"]}}


def test_match_finds_user_and_hides_self_and_junk():
    a, b = mk("Ayşe"), mk("Berk")
    r = client.post("/v1/contacts/match", json={"phones": [b["phone"], a["phone"], "123", "0500 000 00 00"]}, headers=a["h"])
    assert r.status_code == 200
    m = r.json()["matches"]
    assert [x["id"] for x in m] == [b["id"]] and m[0]["name"] == "Berk" and m[0]["phone"] == b["phone"]
    assert "password" not in str(m)


def test_match_accepts_formats():
    a, b = mk("Can"), mk("Deniz")
    p = b["phone"]
    spaced = f"+90 {p[1:4]} {p[4:7]} {p[7:9]} {p[9:]}"
    r = client.post("/v1/contacts/match", json={"phones": [spaced]}, headers=a["h"])
    assert [x["id"] for x in r.json()["matches"]] == [b["id"]]


def test_not_findable_is_hidden_and_toggle():
    a, b = mk("Ece"), mk("Fuat")
    assert client.get("/v1/me/findable", headers=b["h"]).json() == {"on": True}
    assert client.post("/v1/me/findable", json={"on": False}, headers=b["h"]).json() == {"on": False}
    r = client.post("/v1/contacts/match", json={"phones": [b["phone"]]}, headers=a["h"])
    assert r.json()["matches"] == []
    client.post("/v1/me/findable", json={"on": True}, headers=b["h"])
    r = client.post("/v1/contacts/match", json={"phones": [b["phone"]]}, headers=a["h"])
    assert len(r.json()["matches"]) == 1


def test_requires_login_and_rate_limit():
    assert client.post("/v1/contacts/match", json={"phones": ["05380000000"]}).status_code in (401, 403)
    a = mk("Gül")
    codes = [client.post("/v1/contacts/match", json={"phones": ["05381111111"]}, headers=a["h"]).status_code for _ in range(14)]
    assert codes[:12] == [200] * 12 and codes[12] == 429


def test_map_icon_set_clear_and_validate():
    a = mk("Hale")
    assert client.get("/v1/me", headers=a["h"]).json()["mapIcon"] == ""
    r = client.post("/v1/me/map-icon", json={"icon": "kedi"}, headers=a["h"])
    assert r.status_code == 200 and r.json() == {"icon": "kedi"}
    assert client.get("/v1/me", headers=a["h"]).json()["mapIcon"] == "kedi"
    assert client.post("/v1/me/map-icon", json={"icon": "<script>"}, headers=a["h"]).status_code == 422
    assert client.post("/v1/me/map-icon", json={"icon": "x" * 25}, headers=a["h"]).status_code == 422
    assert client.post("/v1/me/map-icon", json={"icon": ""}, headers=a["h"]).json() == {"icon": ""}
    assert client.post("/v1/me/map-icon", json={"icon": "kedi"}).status_code in (401, 403)
