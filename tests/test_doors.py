"""Ortak kapı noktası katmanı: normalleştirme, rıza şartı, oylama/doğrulama, çelişki, gizleme, sınırlar, gizlilik."""
import itertools
import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.doors import anonymize_reporter, district_core, door_parts, fold, make_router, neigh_core, reporter_code, same_street, street_close, street_parts
from app.main import app, doors_router
from app.models import DoorPoint, DoorReport

from test_auth_consent import ROOT, auth, phone, reg, run_py

client = TestClient(app)
_seq = itertools.count(1)

BASE = (36.4950, 32.0990)  # Alanya civarı
M20 = 0.00018  # ≈ 20 m (enlem)
M500 = 0.0045  # ≈ 500 m


def person(role="courier", kapi=True):
    r = reg(role=role).json()
    tok = r["token"]
    if kapi:
        give_consent(tok, True)
    return tok, r["user"]["id"]


def give_consent(tok, kapi, at=1760000000000):
    body = {"version": 3, "voice": False, "ocr": False, "grupKonum": False, "kapi": kapi, "aydinlatmaAt": at, "acikRizaAt": at + (0 if kapi else 7)}
    r = client.post("/v1/me/consent", headers=auth(tok), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def area():
    """Her test kendi ilçe/mahalle adını kullanır: testler birbirinin noktalarına karışmaz."""
    n = next(_seq)
    return {"province": "Antalya", "district": f"Deneme İlçe {n}", "neighborhood": f"Prova Mah. {n}"}


def item(a, door, dlat=0.0, dlon=0.0, street="Atatürk Cad.", **extra):
    d = dict(a, street=street, door=str(door), lat=BASE[0] + dlat, lon=BASE[1] + dlon, accuracyM=8, source="checkin", vote="confirm")
    d.update(extra)
    return d


def post(tok, *items):
    r = client.post("/v1/doors/report", headers=auth(tok), json={"items": list(items)})
    return r


def one(tok, it):
    r = post(tok, it)
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def street(tok, a, st="Atatürk Cad.", **extra):
    params = {"district": a["district"], "neighborhood": a["neighborhood"], "street": st, "province": a["province"], **extra}
    r = client.get("/v1/doors/street", headers=auth(tok), params=params)
    assert r.status_code == 200, r.text
    return r.json()["doors"]


# ---------- normalleştirme ----------
@pytest.mark.parametrize(
    "raw,want",
    [
        ("Atatürk Cad.", ("ataturk", "cadde")),
        ("ATATÜRK CADDESİ", ("ataturk", "cadde")),
        ("İnönü Cd.", ("inonu", "cadde")),
        ("Barbaros Bulvarı", ("barbaros", "bulvar")),
        ("Gül Sokağı", ("gul", "sokak")),
        ("107 Nolu Sokak", ("107", "sokak")),
        ("107. Sk.", ("107", "sokak")),
        ("1234 Sk. No:45", ("1234", "sokak")),  # kapı numarası sokak adına karışmaz
        ("75. Yıl Caddesi", ("75 yil", "cadde")),
        ("Işık Sokak", ("isik", "sokak")),
        ("Atatürk", ("ataturk", "")),
    ],
)
def test_street_parts(raw, want):
    assert street_parts(raw) == want


def test_neighborhood_and_fold():
    assert neigh_core("Mahmutlar Mah.") == "mahmutlar"
    assert neigh_core("ÇIKCILLI MAHALLESİ") == "cikcilli"
    assert neigh_core("  Oba  ") == "oba"
    assert fold("İSTANBUL ığüşöç") == "istanbul igusoc"


@pytest.mark.parametrize(
    "raw,want",
    [
        ("12", ("12", 12)),
        ("No:12", ("12", 12)),
        ("no 7", ("7", 7)),
        ("12a", ("12A", 12)),
        ("12/A", ("12A", 12)),
        ("12/3", ("12", 12)),  # 3 = daire
        ("12-14", ("12", 12)),
        ("12 daire 3", ("12", 12)),
        ("227", ("227", 227)),
        ("A12", None),
        ("0", None),
        ("abc", None),
        ("", None),
        ("123456", None),
    ],
)
def test_door_parts(raw, want):
    assert door_parts(raw) == want


def test_street_matching_rules():
    assert street_close("ataturk", "mustafa kemal ataturk")
    assert street_close("barbaros", "barbarus")  # küçük yazım hatası
    assert not street_close("107", "108")  # numaralı sokaklar birebir
    assert not street_close("gul", "gun")
    assert not street_close("lale", "lala")
    assert same_street("gul", "sokak", "gul", "")  # tür yazılmadıysa eşleşir
    assert not same_street("gul", "sokak", "gul", "cadde")  # Gül Sokak ≠ Gül Caddesi


# ---------- rıza ve giriş ----------
def test_everything_needs_login():
    for method, path in [("post", "/v1/doors/report"), ("get", "/v1/doors/street"), ("get", "/v1/doors/streets"), ("get", "/v1/doors/near"), ("get", "/v1/doors/stats")]:
        assert getattr(client, method)(path).status_code == 401, path


def test_reporting_needs_no_consent_by_default_only_login():
    """Katman kişi verisi tutmaz: giriş yapmış herkes yazar ve okur (rıza şartı yalnız DOOR_REQUIRE_CONSENT=1 ile)."""
    a = area()
    tok, _ = person(kapi=False)
    assert one(tok, item(a, 5))["status"] == "new"
    other, _ = person(kapi=False)
    assert [d["door"] for d in street(other, a)] == ["5"]


def test_strict_mode_still_requires_consent():
    strict = FastAPI()
    strict.include_router(make_router(require_consent=True))
    sc = TestClient(strict)
    a = area()
    tok, _ = person(kapi=False)
    assert sc.post("/v1/doors/report", headers=auth(tok), json={"items": [item(a, 5)]}).status_code == 403
    give_consent(tok, True)
    assert sc.post("/v1/doors/report", headers=auth(tok), json={"items": [item(a, 5)]}).status_code == 200
    give_consent(tok, False, at=1760000100000)
    assert sc.post("/v1/doors/report", headers=auth(tok), json={"items": [item(a, 6)]}).status_code == 403


def test_site_or_building_name_is_stored_and_shown():
    a = area()
    tok, _ = person()
    one(tok, item(a, 40, site="Güneş Sitesi"))
    tok2, _ = person()
    one(tok2, item(a, 40, dlat=M20 / 4, site="Başka Ad"))  # ilk ad kalır
    d = street(tok, a)[0]
    assert d["site"] == "Güneş Sitesi" and d["confirms"] == 2
    assert one(tok, item(a, 41, site="0532 123 45 67"))["reason"] == "kisisel_veri:site"


def test_consent_kapi_is_part_of_the_dedupe_check():
    tok = reg().json()["token"]
    h = auth(tok)
    base = {"version": 3, "voice": False, "ocr": False, "grupKonum": False, "aydinlatmaAt": 1, "acikRizaAt": 5}
    client.post("/v1/me/consent", headers=h, json=base)
    client.post("/v1/me/consent", headers=h, json=base)  # aynı içerik: yeni satır yok
    client.post("/v1/me/consent", headers=h, json={**base, "kapi": True})  # yalnız kapı rızası değişti: yeni satır
    assert len(client.get("/v1/me/consent/history", headers=h).json()) == 2


# ---------- oylama ----------
def test_one_report_is_estimate_second_person_verifies():
    a = area()
    c1, _ = person()
    c2, _ = person()
    r1 = one(c1, item(a, 12))
    assert r1["status"] == "new" and r1["verified"] is False
    d = street(c2, a)
    assert [(x["door"], x["verified"], x["confirms"]) for x in d] == [("12", False, 1)]

    r2 = one(c2, item(a, 12, dlat=M20))  # ≈20 m ötede, aynı kapı → aynı nokta
    assert r2["status"] == "confirmed" and r2["verified"] is True
    d = street(c1, a)
    assert len(d) == 1 and d[0]["verified"] is True and d[0]["confirms"] == 2
    assert abs(d[0]["lat"] - (BASE[0] + M20 / 2)) < 1e-5  # iki konumun ortası

    again = one(c1, item(a, 12))  # aynı kişi tekrar bildirirse puan artmaz
    assert again["status"] == "duplicate"
    assert street(c1, a)[0]["confirms"] == 2


def test_conflicting_far_reports_majority_wins_and_correction_moves_vote():
    a = area()
    c1, _ = person()
    c2, _ = person()
    c3, _ = person()
    one(c1, item(a, 40))  # P1
    far = one(c2, item(a, 40, dlat=M500))  # P2 (≈500 m): ayrı aday
    assert far["status"] == "new"
    d = street(c3, a)
    assert len(d) == 1  # kapı başına tek (en iyi) aday gösterilir
    one(c3, item(a, 40, dlat=M500 + M20 / 2))  # P2'yi onaylar → P2 puanı 2
    d = street(c3, a)
    assert d[0]["verified"] is True and d[0]["lat"] > BASE[0] + M500 / 2
    # c1 yanıldığını anlayıp P2'yi bildirir: eski oyu çekilir, P1 (onaysız kaldığı için) silinir
    moved = one(c1, item(a, 40, dlat=M500))
    assert moved["status"] == "confirmed" and moved["confirms"] == 3
    db = SessionLocal()
    try:
        pts = db.query(DoorPoint).filter(DoorPoint.district_k == district_core(a["district"]), DoorPoint.neigh_k == neigh_core(a["neighborhood"])).all()
    finally:
        db.close()
    assert len(pts) == 1 and pts[0].confirms == 3  # eski aday (P1) onaysız kalıp silindi


def test_wrong_votes_hide_point_only_when_they_outweigh():
    a = area()
    c1, _ = person()
    c2, _ = person()
    c3, _ = person()
    assert one(c1, item(a, 9))["status"] == "new"
    f = one(c2, item(a, 9, vote="wrong"))
    assert f["status"] == "flagged"
    assert [x["door"] for x in street(c3, a)] == ["9"]  # 1 doğru, 1 yanlış: hâlâ görünür
    one(c3, item(a, 9, vote="wrong"))
    assert street(c3, a) == []  # 1 doğru, 2 yanlış: gizlendi
    # çok uzaktaki "yanlış" hiçbir noktaya denk gelmez
    assert one(c3, item(a, 9, dlat=M500, vote="wrong"))["status"] == "no_point"


def test_tie_is_not_verified_and_flip_removes_own_point():
    a = area()
    p = [person()[0] for _ in range(4)]
    one(p[0], item(a, 3))
    one(p[1], item(a, 3, dlat=M20))  # score 2 → doğrulanmış
    assert street(p[2], a)[0]["verified"] is True
    one(p[2], item(a, 3, vote="wrong"))
    one(p[3], item(a, 3, vote="wrong"))  # 2 doğru, 2 yanlış: eşit
    d = street(p[2], a)
    assert len(d) == 1 and d[0]["verified"] is False  # gizli değil ama doğrulanmış da değil
    # tek kişilik nokta: sahibi "yanlış" derse nokta tamamen silinir
    solo = p[0]
    a2 = area()
    one(solo, item(a2, 8))
    assert one(solo, item(a2, 8, vote="wrong"))["status"] == "changed"
    assert street(solo, a2) == []


def test_recipient_pin_is_verified_on_its_own_and_sets_the_position():
    a = area()
    c1, _ = person()
    c2, _ = person(kapi=False)
    one(c1, item(a, 21))
    db = SessionLocal()
    try:
        res = doors_router.add_recipient_pin(db, item(a, 21, dlat=M20 * 1.5), "alici-anahtari-1")
    finally:
        db.close()
    assert res["status"] == "confirmed" and res["verified"] is True and res["confirms"] == 2
    d = street(c2, a)[0]
    assert abs(d["lat"] - (BASE[0] + M20 * 1.5)) < 1e-6  # konumu alıcının pini belirler (kurye GPS'iyle karışmaz)


# ---------- doğrulama ve sınırlar ----------
def test_bad_items_are_rejected_one_by_one():
    a = area()
    tok, _ = person()
    good = item(a, 14)
    bad = [
        item(a, 15, neighborhood=""),  # mahallesiz giremez
        item(a, 16, street="Gül Sk. 05321234567"),  # sokak alanına yazılmış telefon
        item(a, 17, street="mail@ornek.com"),
        item(a, "abc"),
        item(a, 18, lat=60.0),  # Türkiye dışı
        item(a, 19, accuracyM=300),  # GPS çok zayıf
        {"district": "x"},  # alanlar eksik
        item(a, 20, district=""),
    ]
    res = post(tok, good, *bad).json()
    assert res["accepted"] == 1
    reasons = [r.get("reason") for r in res["results"]]
    assert res["results"][0]["status"] == "new"
    assert reasons[1:] == ["mahalle_yok", "kisisel_veri:sokak", "kisisel_veri:sokak", "kapi_no_gecersiz", "alan_gecersiz", "gps_zayif", "alan_gecersiz", "ilce_yok"]


def test_batch_size_limits():
    tok, _ = person()
    assert client.post("/v1/doors/report", headers=auth(tok), json={"items": []}).status_code == 422
    a = area()
    assert post(tok, *[item(a, i + 1) for i in range(51)]).status_code == 422
    assert post(tok, *[item(a, i + 1) for i in range(50)]).json()["accepted"] == 50


def test_overlong_or_expanding_text_is_clipped_not_a_500():
    """Alışılmadık Unicode anahtarı genişletir ('㎦' → 'km3'); PostgreSQL'de sütun taşıp tüm paketi 500'e düşürürdü."""
    tok, _ = person()
    a = {"province": "Ant" * 20, "district": "㎦" * 30 + " Deneme", "neighborhood": "Ş㎦" * 50}
    st = "Ç㎦ Sokak " + "x" * 100
    r = one(tok, item(a, 9, street=st))
    assert r["status"] == "new", r
    assert [d["door"] for d in street(tok, a, st=st)] == ["9"]
    # ikinci kişi aynı (uzun) adresi bildirince yine aynı noktaya oy verir
    tok2, _ = person()
    assert one(tok2, item(a, 9, dlat=M20 / 4, street=st))["status"] == "confirmed"


def test_unknown_source_cannot_fake_a_recipient():
    a = area()
    tok, _ = person()
    one(tok, item(a, 30, source="recipient"))  # istemci "alıcı" diyemez → normal oy sayılır
    assert street(tok, a)[0]["verified"] is False


def test_daily_limit(monkeypatch):
    small = FastAPI()
    small.include_router(make_router(daily_max=3))
    sc = TestClient(small)
    a = area()
    tok, _ = person()
    items = [item(a, i + 1) for i in range(5)]
    res = sc.post("/v1/doors/report", headers=auth(tok), json={"items": items}).json()
    assert [r["status"] for r in res["results"]] == ["new", "new", "new", "rejected", "rejected"]
    assert res["results"][3]["reason"] == "gunluk_sinir" and res["accepted"] == 3
    # var olan noktayı yeniden oylamak yeni satır açmaz ama sınır dolduysa yine de yeni nokta açılamaz
    assert sc.post("/v1/doors/report", headers=auth(tok), json={"items": [item(a, 1)]}).json()["results"][0]["status"] in ("duplicate", "rejected")


# ---------- okuma ----------
def test_street_listing_is_sorted_fuzzy_and_scoped():
    a = area()
    tok, _ = person()
    other = person()[0]
    post(tok, item(a, 12, street="Atatürk Caddesi"), item(a, 5, street="ATATÜRK CD."), item(a, "7/A", street="Mustafa Kemal Atatürk Cad."), item(a, 8, street="Gül Sokak"))
    d = street(other, a, "Ataturk Cad")
    assert [x["door"] for x in d] == ["5", "7A", "12"]
    assert [x["n"] for x in d] == [5, 7, 12]
    assert [x["door"] for x in street(other, a, "Gül Sk.")] == ["8"]
    assert street(other, a, "Gül Cad.") == []  # tür çelişiyor
    b = dict(a, neighborhood="Başka Mah.")
    assert street(other, b) == []  # başka mahalle
    far = street(other, a, lat=39.9, lon=32.8)  # ipucu konumu 400 km uzakta (başka ilin aynı adlı mahallesi olabilir)
    assert far == []
    assert len(street(other, a, lat=BASE[0], lon=BASE[1])) == 3
    assert client.get("/v1/doors/street", headers=auth(other), params={"district": a["district"], "neighborhood": "", "street": "x"}).status_code == 400


def test_street_name_suggestions():
    a = area()
    tok, _ = person()
    post(tok, item(a, 1, street="Barbaros Bulvarı"), item(a, 2, street="Barbaros Bulvarı"), item(a, 3, street="Menekşe Sokak"), item(a, 4, street="Atatürk Cad."))
    r = client.get("/v1/doors/streets", headers=auth(tok), params={"district": a["district"], "neighborhood": a["neighborhood"], "q": "bar"}).json()["streets"]
    assert [(x["name"], x["kind"], x["doors"]) for x in r] == [("Barbaros Bulvarı", "bulvar", 2)]
    allr = client.get("/v1/doors/streets", headers=auth(tok), params={"district": a["district"], "neighborhood": a["neighborhood"]}).json()["streets"]
    assert allr[0]["name"] == "Barbaros Bulvarı" and len(allr) == 3  # en çok kapısı olan başta


def test_near_and_stats():
    a = area()
    o = 1.0 + next(_seq) * 0.02  # bu test kendi köşesinde çalışır: öteki testlerin noktalarıyla karışmaz
    tok, _ = person()
    helper, _ = person()
    post(tok, item(a, 1, dlat=o), item(a, 2, dlat=o + M20), item(a, 3, dlat=o + 0.004))  # 3 numara ≈445 m ötede
    one(helper, item(a, 1, dlat=o))  # 1 numara doğrulandı
    q = {"lat": BASE[0] + o, "lon": BASE[1]}
    near = client.get("/v1/doors/near", headers=auth(tok), params={**q, "radius": 100}).json()["doors"]
    assert [x["door"] for x in near] == ["1", "2"]  # en yakından uzağa; ≈445 m uzaktaki 100 m'lik daireye girmez
    assert near[0]["verified"] is True and near[1]["verified"] is False
    far = client.get("/v1/doors/near", headers=auth(tok), params={**q, "radius": 5000}).json()["doors"]  # yarıçap en çok 500 m'ye kısılır
    assert [x["door"] for x in far] == ["1", "2", "3"]
    s = client.get("/v1/doors/stats", headers=auth(tok)).json()
    assert s["points"] >= 3 and s["verified"] >= 1 and s["mine"] == 3
    assert client.get("/v1/doors/stats", headers=auth(helper)).json()["mine"] == 1
    assert client.get("/v1/doors/near", headers=auth(tok), params={"lat": 10, "lon": 10}).status_code == 422  # Türkiye dışı


# ---------- gizlilik ----------
def test_no_identity_is_stored_with_points_and_anonymize_keeps_scores():
    a = area()
    tok, uid = person()
    tok2, uid2 = person()
    one(tok, item(a, 77))
    one(tok2, item(a, 77))
    db = SessionLocal()
    try:
        cols = {c.name for c in DoorPoint.__table__.columns}
        assert not ({"user_id", "reporter", "phone", "name"} & cols)  # nokta tablosunda kişi alanı yok
        reps = db.query(DoorReport).all()
        assert all(r.reporter not in (uid, uid2) and len(r.reporter) <= 32 for r in reps)
        mine = reporter_code(uid)
        assert mine != uid and any(r.reporter == mine for r in reps)
        n = anonymize_reporter(db, uid)
        assert n >= 1
        assert not any(r.reporter == mine for r in db.query(DoorReport).all())
    finally:
        db.close()
    d = street(tok2, a)[0]
    assert d["confirms"] == 2 and d["verified"] is True  # puan yerinde kaldı
    assert client.get("/v1/doors/stats", headers=auth(tok)).json()["mine"] == 0  # ama artık bu kişiye bağlanamaz
    # anonimleştirilen kişi aynı noktayı yeniden bildirirse kendi oyunu (yeni kodla) bir kez daha sayabilir: bilinen, kabul edilen sınır
    assert one(tok, item(a, 77))["status"] in ("confirmed", "duplicate")


def test_old_database_gets_kapi_column_and_door_tables():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "old2.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id VARCHAR(32) PRIMARY KEY, phone VARCHAR(16) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL, name VARCHAR(120) NOT NULL, active_role VARCHAR(16) NOT NULL, username VARCHAR(32), email VARCHAR(254), lat FLOAT, lon FLOAT, created_at DATETIME)")
    con.execute(
        "CREATE TABLE consent_log (id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32) NOT NULL, version INTEGER NOT NULL, voice BOOLEAN NOT NULL, ocr BOOLEAN NOT NULL, grup_konum BOOLEAN NOT NULL,"
        " aydinlatma_ms BIGINT, acik_riza_ms BIGINT, created_at DATETIME, sozlesme_version INTEGER, sozlesme_ms BIGINT)"
    )
    con.execute("INSERT INTO users (id, phone, password_hash, name, active_role) VALUES ('u1', '05320000009', 'pbkdf2$s$d', 'Eski', 'courier')")
    con.execute("INSERT INTO consent_log (id, user_id, version, voice, ocr, grup_konum, created_at) VALUES ('c1', 'u1', 2, 1, 0, 0, '2026-10-01 10:00:00')")
    con.commit()
    con.close()
    r = run_py("import app.main", {"DATABASE_URL": f"sqlite:///{path}"})
    assert r.returncode == 0, r.stderr
    assert "consent_log.kapi eklendi" in r.stdout
    con = sqlite3.connect(path)
    assert "kapi" in {row[1] for row in con.execute("PRAGMA table_info(consent_log)")}
    assert {"door_points", "door_reports"} <= {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert con.execute("SELECT voice FROM consent_log WHERE id='c1'").fetchone()[0] == 1  # eski rıza durdu
    con.close()
    # eski satırda kapi NULL → kapalı sayılır
    code = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c=TestClient(app)\n"
        "r=c.post('/v1/auth/login',json={'phone':'05320000009','password':'x'})\n"
        "print(r.status_code)\n"
    )
    assert run_py(code, {"DATABASE_URL": f"sqlite:///{path}"}).returncode == 0
