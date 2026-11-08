"""1.26.0 VERİTABANI YEDEĞİ: şifreli döküm (gece GitHub Actions + yönetimden indirme), yanlış anahtar, sınırlar, geri yükleme betiği."""
import os
import subprocess
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

from app.backup_core import MAGIC, open_backup
from app.db import Base, SessionLocal, engine
from app.main import app, backup_router
from app.models import AdminAudit, BackupRun
from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)
KEY = "deneme-yedek-anahtari-0123456789abcdef"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _limits():
    for lim in backup_router.limiters:
        lim.reset()
    yield
    for lim in backup_router.limiters:
        lim.reset()


@pytest.fixture
def key_on(monkeypatch):
    monkeypatch.setenv("BACKUP_KEY", KEY)
    return KEY


def run(k=KEY):
    return client.post("/v1/backup/run", headers={"X-Backup-Key": k})


def test_off_without_key(monkeypatch):
    monkeypatch.delenv("BACKUP_KEY", raising=False)
    r = run()
    assert r.status_code == 503 and "BACKUP_KEY yok" in r.json()["detail"]
    monkeypatch.setenv("BACKUP_KEY", "kisa-anahtar")  # 24 karakterden kısa: kapalı sayılır
    r = run("kisa-anahtar")
    # 8 Kasım 2026: kısa anahtar artık "yok" değil "çok kısa" der (kullanıcı doğru yazdığını sanıyordu); uzunluğu söylemez
    assert r.status_code == 503 and "çok kısa" in r.json()["detail"] and "en az 24" in r.json()["detail"] and "12" not in r.json()["detail"]
    h, _ = admin_login()
    assert client.get("/v1/admin/backups", headers=h).json()["enabled"] is False
    r = client.post("/v1/admin/backups/download", headers=h)
    assert r.status_code == 503 and "çok kısa" in r.json()["detail"]


def test_scheduled_backup_is_encrypted_and_complete(key_on):
    me = reg(role="courier", name="Yedek Deneme Kuryesi").json()
    r = run()
    assert r.status_code == 200, r.text
    body = r.content
    assert body[:8] == MAGIC and r.headers["content-disposition"].startswith('attachment; filename="rota-yedek-')
    assert b"Yedek Deneme" not in body and me["user"]["id"].encode() not in body  # düz metin yok
    doc = open_backup(body, KEY)
    assert doc["v"] == 1 and doc["server"] == app.version  # sunucu sürümü dökümde
    names = set(doc["tables"])
    assert {"users", "door_points", "surveys", "ad_bookings", "backup_runs"} <= names
    users = doc["tables"]["users"]
    i = users["cols"].index("id")
    assert any(row[i] == me["user"]["id"] for row in users["rows"])
    at = users["cols"].index("created_at")
    assert all(isinstance(row[at], dict) and "$t" in row[at] for row in users["rows"] if row[at] is not None)
    with pytest.raises(ValueError):
        open_backup(body, KEY + "x")
    bad = bytearray(body)
    bad[len(bad) // 2] ^= 1
    with pytest.raises(ValueError):
        open_backup(bytes(bad), KEY)
    with pytest.raises(ValueError):
        open_backup(b"PK\x03\x04 zip degil", KEY)
    db = SessionLocal()
    try:
        last = db.query(BackupRun).order_by(BackupRun.at.desc()).first()
        assert last.by == "schedule" and last.size == len(body) and last.tables == len(names) and last.rows > 0
    finally:
        db.close()


def test_wrong_key_and_limits(key_on):
    for _ in range(10):
        assert run("yanlis-anahtar-yanlis-anahtar-123").status_code == 403
    assert run().status_code == 429  # bu IP'den çok yanlış deneme: doğru anahtar da bekler
    for lim in backup_router.limiters:
        lim.reset()
    codes = [run().status_code for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429  # saatte en çok 6 yedek


def test_admin_list_and_download(key_on):
    h, _ = admin_login()
    r = client.post("/v1/admin/backups/download", headers=h)
    assert r.status_code == 200 and r.content[:8] == MAGIC
    assert open_backup(r.content, KEY)["tables"]["admin_audit"] is not None
    lst = client.get("/v1/admin/backups", headers=h).json()
    assert lst["enabled"] is True and lst["items"][0]["by"] == "admin" and lst["items"][0]["size"] == len(r.content)
    db = SessionLocal()
    try:
        assert db.query(AdminAudit).filter(AdminAudit.action == "backup_download").count() >= 1
    finally:
        db.close()
    tok = reg(role="courier").json()["token"]
    assert client.post("/v1/admin/backups/download", headers=auth(tok)).status_code in (401, 403)


def restore(path, url, *extra, key=KEY):
    env = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "ADMIN_KEY")}  # betik bunlarsız çalışmalı
    env.update(DATABASE_URL=url, BACKUP_KEY=key, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "app.restore", path, *extra], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)


def counts(eng):
    with eng.connect() as c:
        return {t.name: c.execute(select(func.count()).select_from(t)).scalar() for t in Base.metadata.sorted_tables}


def test_restore_roundtrip_into_empty_sqlite(key_on):
    reg(role="employer", name="Geri Yükleme İşletmesi")
    body = run().content
    doc = open_backup(body, KEY)
    want = {n: len(t["rows"]) for n, t in doc["tables"].items()}
    d = tempfile.mkdtemp()
    path = os.path.join(d, "y.rotayedek")
    with open(path, "wb") as f:
        f.write(body)
    url = "sqlite:///" + os.path.join(d, "geri.db")
    chk = restore(path, url, "--check")
    assert chk.returncode == 0 and "tablo" in chk.stdout and "users:" in chk.stdout, chk.stderr
    assert restore(path, url, "--yes", key="yanlis-anahtar-yanlis-anahtar-0").returncode == 3
    p = restore(path, url, "--yes")
    assert p.returncode == 0, p.stderr + p.stdout
    got = counts(create_engine(url))
    assert all(got.get(n, 0) == c for n, c in want.items()), {n: (c, got.get(n)) for n, c in want.items() if got.get(n) != c}
    e2 = create_engine(url)
    with e2.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM users WHERE name = 'Geri Yükleme İşletmesi'")).scalar() == 1
    again = restore(path, url, "--yes")
    assert again.returncode == 4 and "boş değil" in again.stderr  # dolu veritabanına yazmaz


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="yalnız PostgreSQL'de (TEST_DATABASE_URL)")
def test_restore_roundtrip_into_empty_postgres(key_on):
    import psycopg2

    reg(role="courier", name="Pg Geri Yükleme")
    body = run().content
    want = {n: len(t["rows"]) for n, t in open_backup(body, KEY)["tables"].items()}
    base = engine.url
    admin_url = base.set(database="postgres").render_as_string(hide_password=False)
    conn = psycopg2.connect(admin_url.replace("postgresql+psycopg2://", "postgresql://"))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS rota_geri_test")
        cur.execute("CREATE DATABASE rota_geri_test")
    conn.close()
    url = base.set(database="rota_geri_test").render_as_string(hide_password=False)
    d = tempfile.mkdtemp()
    path = os.path.join(d, "y.rotayedek")
    with open(path, "wb") as f:
        f.write(body)
    p = restore(path, url, "--yes")
    assert p.returncode == 0, p.stderr + p.stdout
    e2 = create_engine(url)
    got = counts(e2)
    assert all(got.get(n, 0) == c for n, c in want.items())
    with e2.begin() as c:  # sayaçlı tablo yeni satırı çakışmadan alır
        fid = c.execute(text("SELECT id FROM follows LIMIT 1")).scalar()
        if fid:
            c.execute(text("INSERT INTO follow_points (follow_id, at, lat, lon) VALUES (:f, now(), 1, 1)"), {"f": fid})
    e2.dispose()
