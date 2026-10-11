"""Yönetim ve değerlendirme testlerinin ortak yardımcıları."""
import itertools
import os
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
KEY = os.environ["ADMIN_KEY"]
ADMIN_PW = "yonetici-sifresi-1"
_n = itertools.count(1)


def make_admin(name: str | None = None, pw: str = ADMIN_PW) -> str:
    """ADMIN_KEY ile yeni yönetici açar (kurulum ucu)."""
    name = name or f"yon{next(_n)}{uuid.uuid4().hex[:5]}"
    r = client.post("/v1/admin/setup", json={"username": name, "password": pw}, headers={"X-Admin-Key": KEY})
    assert r.status_code == 200, r.text
    return name


def admin_login(name: str | None = None, pw: str = ADMIN_PW):
    """(Authorization başlığı, yönetici adı) döner. `name` verilmezse yeni yönetici açılır; verilirse var olan ile girilir."""
    if name is None:
        name = make_admin(None, pw)
    r = client.post("/v1/admin/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}, name
