"""Kurye araç profili: AYRI açık rıza şart; rıza geri çekilince silinir; yalnız işletme okur; plaka / fotoğraf sunucuya gelmez."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app, vehicle_router
from app.models import ConsentLog, CourierProfile, Event

from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)

BODY = {"vehicle": "motor", "capacity": "2 koli, 1 sipariş çantası", "areas": ["Mahmutlar", "Oba"], "available": True, "from": "09:00", "to": "22:00"}


@pytest.fixture(autouse=True)
def _clean():
    for lim in vehicle_router.limiters:
        lim.reset()
    yield
    for lim in vehicle_router.limiters:
        lim.reset()


def new(role, name="Kişi"):
    r = reg(role=role, name=name).json()
    return r["token"], r["user"]["id"]


def consent(tok, arac=True, version=5, **kw):
    body = {"version": version, "voice": False, "ocr": False, "grupKonum": False, "aydinlatmaAt": 1, "acikRizaAt": 2, "arac": arac}
    body.update(kw)
    r = client.post("/v1/me/consent", json=body, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def put(tok, **kw):
    b = dict(BODY)
    b.update(kw)
    return client.put("/v1/me/vehicle", json=b, headers=auth(tok))


def view(tok, cid):
    return client.get(f"/v1/couriers/{cid}/vehicle", headers=auth(tok))


def db_profile(uid_):
    db = SessionLocal()
    try:
        p = db.get(CourierProfile, uid_)
        if p is not None:
            db.expunge(p)
        return p
    finally:
        db.close()


# ---------- rıza ----------
def test_profile_needs_separate_consent():
    ct, cid = new("courier")
    r = put(ct)
    assert r.status_code == 403 and "açık rıza" in r.json()["detail"]
    assert db_profile(cid) is None
    # genel rıza ekranındaki öteki onaylar araç profili için YETMEZ
    consent(ct, arac=False, voice=True, ocr=True, grupKonum=True, kapi=True)
    assert put(ct).status_code == 403
    consent(ct, arac=True)
    r = put(ct)
    assert r.status_code == 200, r.text
    assert r.json()["vehicle"] == "motor" and r.json()["areas"] == ["Mahmutlar", "Oba"]


def test_consent_is_logged_with_version_and_server_time():
    ct, cid = new("courier")
    c = consent(ct, arac=True, version=5)
    assert c["arac"] is True and c["version"] == 5 and c["recordedAt"]
    hist = client.get("/v1/me/consent/history", headers=auth(ct)).json()
    assert hist[0]["arac"] is True and hist[0]["version"] == 5
    db = SessionLocal()
    try:
        row = db.query(ConsentLog).filter(ConsentLog.user_id == cid).order_by(ConsentLog.created_at.desc()).first()
        assert row.arac is True and row.version == 5 and row.created_at is not None
    finally:
        db.close()


def test_same_consent_twice_adds_no_row_but_a_change_does():
    ct, cid = new("courier")
    consent(ct, arac=False)
    consent(ct, arac=False)
    assert len(client.get("/v1/me/consent/history", headers=auth(ct)).json()) == 1
    consent(ct, arac=True)
    consent(ct, arac=True)
    h = client.get("/v1/me/consent/history", headers=auth(ct)).json()
    assert len(h) == 2 and h[0]["arac"] is True and h[1]["arac"] is False


def test_old_client_consent_without_arac_is_off():
    ct, _ = new("courier")
    r = client.post("/v1/me/consent", json={"version": 4, "voice": True, "ocr": False, "grupKonum": False, "aydinlatmaAt": 1, "acikRizaAt": 2}, headers=auth(ct))
    assert r.status_code == 200 and r.json()["arac"] is False
    assert put(ct).status_code == 403


def test_consent_without_arac_keeps_the_previous_decision():
    """Başka cihaz (ya da eski istemci) araç kararı göndermezse mevcut karar ve profil bozulmaz; yalnız AÇIK `false` geri çeker."""
    ct, cid = new("courier")
    et, _ = new("employer")
    consent(ct)
    assert put(ct).status_code == 200
    other = {"version": 5, "voice": True, "ocr": False, "grupKonum": False, "aydinlatmaAt": 1, "acikRizaAt": 3}
    r = client.post("/v1/me/consent", json=other, headers=auth(ct))
    assert r.status_code == 200 and r.json()["arac"] is True and r.json()["voice"] is True
    assert view(et, cid).status_code == 200 and db_profile(cid) is not None
    assert client.get("/v1/me/vehicle", headers=auth(ct)).json()["consent"] is True
    # aynı içerik (araç alanı yok) tekrar gelirse yeni satır açılmaz
    n = len(client.get("/v1/me/consent/history", headers=auth(ct)).json())
    client.post("/v1/me/consent", json=other, headers=auth(ct))
    assert len(client.get("/v1/me/consent/history", headers=auth(ct)).json()) == n
    # açık geri çekme profili siler; sonra araç alanı olmayan bir istek onu yeniden AÇMAZ
    consent(ct, arac=False, voice=True, acikRizaAt=4)
    assert view(et, cid).status_code == 404 and db_profile(cid) is None
    r = client.post("/v1/me/consent", json={**other, "acikRizaAt": 5}, headers=auth(ct))
    assert r.json()["arac"] is False
    assert put(ct).status_code == 403


def test_withdrawing_consent_deletes_profile_and_hides_it():
    ct, cid = new("courier")
    et, _ = new("employer")
    consent(ct)
    assert put(ct).status_code == 200
    assert view(et, cid).status_code == 200
    consent(ct, arac=False)  # geri çekme
    assert db_profile(cid) is None
    assert view(et, cid).status_code == 404
    assert client.get("/v1/me/vehicle", headers=auth(ct)).json() == {"consent": False, "profile": None}
    assert put(ct).status_code == 403
    kinds = [e.kind for e in _events(cid)]
    assert "vehicle.share" in kinds and "vehicle.unshare" in kinds


def test_consent_withdrawn_without_any_profile_is_harmless():
    ct, cid = new("courier")
    consent(ct, arac=True)
    consent(ct, arac=False)
    assert db_profile(cid) is None


def test_delete_endpoint_removes_profile_but_keeps_consent_record():
    ct, cid = new("courier")
    consent(ct)
    put(ct)
    r = client.delete("/v1/me/vehicle", headers=auth(ct))
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert db_profile(cid) is None
    assert client.get("/v1/me/vehicle", headers=auth(ct)).json() == {"consent": True, "profile": None}
    assert client.delete("/v1/me/vehicle", headers=auth(ct)).json()["deleted"] == 0


def _events(uid_):
    db = SessionLocal()
    try:
        return db.query(Event).filter(Event.user_id == uid_).all()
    finally:
        db.close()


# ---------- kim görür ----------
def test_only_employers_read_and_only_shared_profiles():
    ct, cid = new("courier")
    et, _ = new("employer")
    other_c, _ = new("courier")
    st, _ = new("sponsor")
    consent(ct)
    put(ct)
    r = view(et, cid)
    assert r.status_code == 200
    d = r.json()
    assert d["vehicle"] == "motor" and d["capacity"] == "2 koli, 1 sipariş çantası" and d["areas"] == ["Mahmutlar", "Oba"]
    assert d["available"] is True and d["from"] == "09:00" and d["to"] == "22:00" and d["updatedAt"]
    assert view(other_c, cid).status_code == 403  # başka kurye göremez
    assert view(st, cid).status_code == 403  # sponsor göremez
    assert client.get(f"/v1/couriers/{cid}/vehicle").status_code in (401, 403)  # ziyaretçi göremez
    # kendi hesabı da kurye modunda bu uçla okumaz (kendi kopyası için /v1/me/vehicle var)
    assert view(ct, cid).status_code == 403


def test_not_sharing_unknown_and_wrong_ids_all_look_the_same():
    et, _ = new("employer")
    ct, cid = new("courier")  # rıza yok, profil yok
    a = view(et, cid)
    b = view(et, "0" * 32)
    c = view(et, "x" * 41)
    assert a.status_code == b.status_code == c.status_code == 404
    assert a.json() == b.json() == c.json()


def test_banned_courier_is_hidden():
    ct, cid = new("courier")
    et, _ = new("employer")
    consent(ct)
    put(ct)
    adm, _ = admin_login()
    assert client.post(f"/v1/admin/users/{cid}/ban", json={"reason": "deneme"}, headers=adm).status_code == 200
    assert view(et, cid).status_code == 404
    client.post(f"/v1/admin/users/{cid}/unban", json={}, headers=adm)
    assert view(et, cid).status_code == 200


def test_courier_who_switched_to_employer_is_hidden_and_cannot_edit():
    ct, cid = new("courier")
    et, _ = new("employer")
    consent(ct)
    put(ct)
    assert client.post("/v1/me/role", json={"role": "employer"}, headers=auth(ct)).status_code == 200
    assert view(et, cid).status_code == 404
    assert put(ct).status_code == 403
    assert client.post("/v1/me/role", json={"role": "courier"}, headers=auth(ct)).status_code == 200
    assert view(et, cid).status_code == 200


def test_view_is_rate_limited():
    ct, cid = new("courier")
    et, _ = new("employer")
    consent(ct)
    put(ct)
    ok = 0
    for _ in range(vehicle_router.limiters[0].max_hits + 5):
        ok += view(et, cid).status_code == 200
    assert ok == vehicle_router.limiters[0].max_hits
    assert view(et, cid).status_code == 429


# ---------- yazma ----------
def test_only_courier_mode_writes():
    et, _ = new("employer")
    consent(et)
    assert put(et).status_code == 403


def test_update_replaces_and_creates_one_row():
    ct, cid = new("courier")
    consent(ct)
    assert put(ct).status_code == 200
    assert put(ct, vehicle="araba", capacity="5 koli", areas=["Kestel"], available=False, **{"from": "10:30", "to": "23:15"}).status_code == 200
    d = client.get("/v1/me/vehicle", headers=auth(ct)).json()
    assert d["consent"] is True and d["profile"]["vehicle"] == "araba" and d["profile"]["areas"] == ["Kestel"] and d["profile"]["available"] is False
    assert d["profile"]["from"] == "10:30" and d["profile"]["to"] == "23:15"
    db = SessionLocal()
    try:
        assert db.query(CourierProfile).filter(CourierProfile.user_id == cid).count() == 1
    finally:
        db.close()
    # yalnız ilk yayında bir etkinlik satırı
    assert [e.kind for e in _events(cid)].count("vehicle.share") == 1


@pytest.mark.parametrize("patch,word", [
    ({"vehicle": "uçak"}, "Araç tipi"),
    ({"vehicle": ""}, "Araç tipi"),
    ({"capacity": "x" * 41}, "en fazla 40"),
    ({"capacity": "beni ara 0532 111 22 33"}, "Telefon"),
    ({"capacity": "amk koli"}, "Küfür"),
    ({"areas": ["a%d" % i for i in range(9)]}, "En çok 8"),
    ({"areas": ["y" * 41]}, "en fazla 40"),
    ({"areas": ["0532 111 22 33"]}, "Telefon"),
    ({"from": "25:00"}, "SS:DD"),
    ({"to": "9:00"}, "SS:DD"),
    ({"from": "09:60"}, "SS:DD"),
])
def test_validation(patch, word):
    ct, cid = new("courier")
    consent(ct)
    r = put(ct, **patch)
    assert r.status_code == 400 and word in r.json()["detail"], r.text
    assert db_profile(cid) is None


def test_areas_are_cleaned_and_deduplicated():
    ct, _ = new("courier")
    consent(ct)
    r = put(ct, areas=["  Mahmutlar ", "mahmutlar", "", "Oba   Mah."], capacity="  2   koli ")
    assert r.status_code == 200
    assert r.json()["areas"] == ["Mahmutlar", "Oba Mah."] and r.json()["capacity"] == "2 koli"


def test_plate_and_photo_never_reach_the_server():
    ct, cid = new("courier")
    et, _ = new("employer")
    consent(ct)
    r = client.put("/v1/me/vehicle", json=dict(BODY, plate="07 ABC 123", photo="data:image/png;base64,AAAA", phone="05321112233"), headers=auth(ct))
    assert r.status_code == 200
    for out in (r.json(), view(et, cid).json(), client.get("/v1/me/vehicle", headers=auth(ct)).json()):
        s = json.dumps(out, ensure_ascii=False)
        assert "ABC" not in s and "base64" not in s and "0532" not in s and "plate" not in s and "photo" not in s
    cols = {c.name for c in CourierProfile.__table__.columns}
    assert cols == {"user_id", "vehicle", "capacity", "areas", "available", "from_hhmm", "to_hhmm", "updated_at"}


def test_admin_sees_the_consent_flag_but_the_activity_log_has_no_content():
    ct, cid = new("courier")
    consent(ct)
    put(ct, capacity="gizli kapasite metni")
    adm, _ = admin_login()
    d = client.get(f"/v1/admin/users/{cid}", headers=adm).json()
    assert d["consent"]["arac"] is True
    evs = [e for e in _events(cid) if e.kind.startswith("vehicle.")]
    assert evs and all("gizli kapasite" not in (e.meta or "") and "Mahmutlar" not in (e.meta or "") for e in evs)


def test_unauthenticated_requests_are_refused():
    assert client.get("/v1/me/vehicle").status_code in (401, 403)
    assert client.put("/v1/me/vehicle", json=BODY).status_code in (401, 403)
    assert client.delete("/v1/me/vehicle").status_code in (401, 403)


def test_bad_json_body_is_a_422_without_echo():
    ct, _ = new("courier")
    consent(ct)
    r = client.put("/v1/me/vehicle", json={"vehicle": "motor", "areas": "Mahmutlar"}, headers=auth(ct))
    assert r.status_code == 422 and "Mahmutlar" not in r.text


def test_old_consent_log_gets_the_arac_column_and_old_rows_mean_off():
    """Dilim 5 öncesi veritabanı: `consent_log.arac` sütunu sessizce eklenir; eski rıza satırları 'kapalı' sayılır; yeni tablo oluşur."""
    import sqlite3
    import tempfile

    from test_auth_consent import run_py

    path = os.path.join(tempfile.mkdtemp(), "old.db")
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE consent_log (id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32) NOT NULL, version INTEGER NOT NULL, voice BOOLEAN NOT NULL, ocr BOOLEAN NOT NULL,"
        " grup_konum BOOLEAN NOT NULL, kapi BOOLEAN, aydinlatma_ms BIGINT, acik_riza_ms BIGINT, created_at DATETIME, sozlesme_version INTEGER, sozlesme_ms BIGINT)"
    )
    con.execute("INSERT INTO consent_log (id, user_id, version, voice, ocr, grup_konum) VALUES ('c1', 'u1', 4, 1, 0, 0)")
    con.commit()
    con.close()
    r = run_py("import app.main", {"DATABASE_URL": f"sqlite:///{path}"})
    assert r.returncode == 0, r.stderr
    assert "consent_log.arac eklendi" in r.stdout
    con = sqlite3.connect(path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(consent_log)")}
    assert "arac" in cols
    assert con.execute("SELECT voice, arac FROM consent_log WHERE id='c1'").fetchone() in ((1, None), (1, 0))  # eski satır bozulmadı, araç kapalı
    assert con.execute("SELECT name FROM sqlite_master WHERE name='courier_profiles'").fetchone()
    con.close()
    again = run_py("import app.main", {"DATABASE_URL": f"sqlite:///{path}"})
    assert again.returncode == 0 and "eklendi" not in again.stdout  # ikinci açılış hiçbir şey eklemez
