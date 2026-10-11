"""Takip ("Follow"): işveren, kuryeyi harita üzerinde canlı izler. İzin kuryeden gelir; konum geçmişi tutulmaz;
konumu yalnız o takibin işvereni, yalnız takip sürerken görür; bitince silinir."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import events
from app.db import SessionLocal
from app.main import app, follow_router
from app.models import Event, Follow, User, now

from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)

LAT, LON = 36.5432101, 32.1234509  # aranabilir, başka yerde geçmeyen sayılar


@pytest.fixture(autouse=True)
def _clean():
    for lim in follow_router.limiters:
        lim.reset()
    yield
    for lim in follow_router.limiters:
        lim.reset()


def new(role, name="Kişi"):
    r = reg(role=role, name=name).json()
    return r["token"], r["user"]["id"]


def row(fid) -> Follow:
    db = SessionLocal()
    try:
        f = db.get(Follow, fid)
        if f is not None:
            db.expunge(f)
        return f
    finally:
        db.close()


def edit(fid, **kw):
    db = SessionLocal()
    try:
        f = db.get(Follow, fid)
        for k, v in kw.items():
            setattr(f, k, v)
        db.commit()
    finally:
        db.close()


def ask(tok, cid):
    return client.post("/v1/follow/request", json={"courierId": cid}, headers=auth(tok))


def start(emp_tok, cour_tok, cid):
    """İstek + kabul: aktif takibin kimliğini döner."""
    r = ask(emp_tok, cid)
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    a = client.post(f"/v1/follow/{fid}/accept", headers=auth(cour_tok))
    assert a.status_code == 200, a.text
    assert a.json()["status"] == "active"
    return fid


def ping(tok, fid, lat=LAT, lon=LON, **kw):
    return client.post(f"/v1/follow/{fid}/ping", json={"lat": lat, "lon": lon, **kw}, headers=auth(tok))


def get(tok, fid):
    return client.get(f"/v1/follow/{fid}", headers=auth(tok))


# ------------------------------------------------------------------ istek: kim, kime
def test_only_an_employer_can_ask_and_only_a_courier_can_be_asked():
    e, eid = new("employer")
    c, cid = new("courier")
    s, _ = new("sponsor")
    o, oid = new("employer")
    assert ask(c, eid).status_code == 403  # kurye işvereni izleyemez
    assert ask(s, cid).status_code == 403  # sponsor da
    assert ask(e, eid).status_code == 400  # kendini
    assert ask(e, "yok-boyle-biri").status_code == 404
    assert ask(e, oid).status_code == 400  # karşıdaki kurye modunda değil
    assert client.post("/v1/follow/request", headers=auth(e), json={}).status_code == 422
    assert client.post("/v1/follow/request", json={"courierId": cid}).status_code == 401
    r = ask(e, cid)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "asked" and j["side"] == "employer" and j["peer"]["id"] == cid and j["existing"] is False
    assert "location" not in j


def test_no_location_is_taken_before_the_courier_accepts():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = ask(e, cid).json()["id"]
    r = ping(c, fid)
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["status"] == "asked"
    f = row(fid)
    assert f.last_lat is None and f.last_lon is None and f.last_at is None
    assert "location" not in get(e, fid).json()


def test_asking_again_while_open_does_not_open_a_second_request():
    e, _ = new("employer")
    c, cid = new("courier")
    a = ask(e, cid).json()
    b = ask(e, cid).json()
    assert a["id"] == b["id"] and b["existing"] is True
    db = SessionLocal()
    try:
        assert db.query(Follow).filter(Follow.courier_id == cid).count() == 1
    finally:
        db.close()


def test_database_refuses_a_duplicate_open_pair_and_a_second_active_follow():
    from sqlalchemy.exc import IntegrityError

    e, eid = new("employer")
    e2, e2id = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    db = SessionLocal()
    try:
        db.add(Follow(employer_id=eid, courier_id=cid, status="asked", expires_at=now() + timedelta(minutes=5)))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(Follow(employer_id=e2id, courier_id=cid, status="active", expires_at=now() + timedelta(minutes=5)))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # farklı kurye için açık kayıt serbest
        _, c2 = new("courier")
        db.add(Follow(employer_id=eid, courier_id=c2, status="asked", expires_at=now() + timedelta(minutes=5)))
        db.commit()
    finally:
        db.close()
    assert row(fid).status == "active"


# ------------------------------------------------------------------ kabul ve konum
def test_accepted_follow_shows_the_latest_point_only_to_that_employer():
    e, _ = new("employer")
    other, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    assert get(e, fid).json().get("location") is None  # henüz nokta yok
    r = ping(c, fid, acc=12.5)
    assert r.status_code == 200 and r.json()["ok"] is True
    loc = get(e, fid).json()["location"]
    assert (loc["lat"], loc["lon"], loc["acc"]) == (LAT, LON, 12.5) and 0 <= loc["ageSec"] < 30
    # kurye kendi takibinde konum alanını görmez; başkası takibi görmez (404)
    assert "location" not in get(c, fid).json()
    assert get(other, fid).status_code == 404
    assert client.post(f"/v1/follow/{fid}/stop", headers=auth(other)).status_code == 404
    assert ping(other, fid).status_code == 404
    assert ping(e, fid).status_code == 404  # işveren konum gönderemez
    mine_e = client.get("/v1/follow/mine", headers=auth(e)).json()["items"]
    mine_c = client.get("/v1/follow/mine", headers=auth(c)).json()["items"]
    mine_o = client.get("/v1/follow/mine", headers=auth(other)).json()["items"]
    assert [x["id"] for x in mine_e] == [fid] and "location" in mine_e[0] and mine_e[0]["side"] == "employer"
    assert [x["id"] for x in mine_c] == [fid] and "location" not in mine_c[0] and mine_c[0]["side"] == "courier"
    assert mine_o == []


def test_only_the_courier_of_the_follow_can_accept_or_decline():
    e, _ = new("employer")
    c, cid = new("courier")
    c2, _ = new("courier")
    fid = ask(e, cid).json()["id"]
    assert client.post(f"/v1/follow/{fid}/accept", headers=auth(e)).status_code == 403  # işveren kendi isteğini kabul edemez
    assert client.post(f"/v1/follow/{fid}/decline", headers=auth(e)).status_code == 403
    assert client.post(f"/v1/follow/{fid}/accept", headers=auth(c2)).status_code == 404  # başka kurye
    assert row(fid).status == "asked"


def test_only_the_latest_point_is_kept_no_history():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    for i in range(5):
        assert ping(c, fid, lat=36.0 + i / 1000, lon=32.0 + i / 1000).status_code == 200
    f = row(fid)
    assert (f.last_lat, f.last_lon) == (36.004, 32.004)
    db = SessionLocal()
    try:
        assert db.query(Follow).filter(Follow.courier_id == cid).count() == 1  # her nokta yeni satır açmaz
        # koordinat başka hiçbir tabloya yazılmaz: etkinlik akışında yok
        blob = " ".join((e_.meta or "") for e_ in db.query(Event).all())
        assert "36.0" not in blob and "32.0" not in blob and str(LAT) not in blob
    finally:
        db.close()


def test_ping_validation_and_rate_limit():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    for bad in ({"lat": 91, "lon": 0}, {"lat": 0, "lon": 181}, {"lat": "x", "lon": 0}, {"lat": 36}, {"lat": 36, "lon": 32, "acc": -1}):
        assert client.post(f"/v1/follow/{fid}/ping", json=bad, headers=auth(c)).status_code == 422, bad
    assert client.post(f"/v1/follow/{fid}/ping", content='{"lat": NaN, "lon": 1}', headers={**auth(c), "Content-Type": "application/json"}).status_code == 422
    assert row(fid).last_lat is None  # hatalı nokta yazılmadı
    ok = [ping(c, fid).status_code for _ in range(40)]
    assert set(ok) == {200}
    assert ping(c, fid).status_code == 429


def test_progress_carries_only_safe_ids_never_addresses():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    prog = {"done": ["local-1700000000000-ab12", "Barbaros Cad. No:227 Daire 4", "stop-1700000000001"], "failed": ["x y", "venue-1"], "total": 7}
    assert ping(c, fid, progress=prog).status_code == 200
    p = get(e, fid).json()["progress"]
    assert p == {"done": ["local-1700000000000-ab12", "stop-1700000000001"], "failed": ["venue-1"], "total": 7}
    assert "Barbaros" not in json.dumps(p) and "Barbaros" not in (row(fid).progress or "")
    # ilerleme göndermeyen konum, öncekini silmez
    assert ping(c, fid).status_code == 200
    assert get(e, fid).json()["progress"]["total"] == 7
    big = {"done": [f"a{i}" for i in range(501)], "failed": [], "total": 0}
    assert client.post(f"/v1/follow/{fid}/ping", json={"lat": 1, "lon": 1, "progress": big}, headers=auth(c)).status_code == 422


# ------------------------------------------------------------------ bitirme: konum silinir
@pytest.mark.parametrize("who,reason,body", [("c", "stopped_courier", None), ("e", "stopped_employer", None), ("c", "done", {"reason": "done"}), ("e", "stopped_employer", {"reason": "done"})])
def test_stopping_deletes_the_location_for_good(who, reason, body):
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid, progress={"done": ["a1"], "failed": [], "total": 2})
    r = client.post(f"/v1/follow/{fid}/stop", json=body, headers=auth(e if who == "e" else c)) if body else client.post(f"/v1/follow/{fid}/stop", headers=auth(e if who == "e" else c))
    assert r.status_code == 200 and r.json()["status"] == "ended" and r.json()["endReason"] == reason
    f = row(fid)
    assert f.status == "ended" and f.last_lat is None and f.last_lon is None and f.last_acc is None and f.last_at is None and f.progress == ""
    j = get(e, fid).json()
    assert j["status"] == "ended" and "location" not in j and "progress" not in j
    # sonrasında kurye konum gönderse de alınmaz
    pr = ping(c, fid)
    assert pr.json() == {"ok": False, "status": "ended", "endReason": reason}
    assert row(fid).last_lat is None
    # ikinci durdurma zararsız
    assert client.post(f"/v1/follow/{fid}/stop", headers=auth(c)).status_code == 200


def other_request(fid, **values):
    """Başka bir isteğin (ayrı oturum) bu satırı değiştirip kaydetmesi — yarış durumlarını sırayla denemek için."""
    db = SessionLocal()
    try:
        db.query(Follow).filter(Follow.id == fid).update(values, synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_a_point_that_arrives_while_the_follow_is_ending_is_not_kept(monkeypatch):
    """Yarış: konum isteği "takip aktif mi" kontrolünü geçti; yazmadan hemen önce başka bir istek takibi bitirdi.
    Nokta yazılmaz, istemciye "bitti" denir ve satırda konum / ilerleme kalmaz."""
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping_win = follow_router.limiters[1]
    real = ping_win.allow

    def allow_after_the_follow_was_ended(key):
        other_request(fid, status="ended", end_reason="stopped_employer", ended_at=now(), last_lat=None, last_lon=None, last_acc=None, last_at=None, progress="")
        return real(key)

    monkeypatch.setattr(ping_win, "allow", allow_after_the_follow_was_ended)
    r = ping(c, fid, progress={"done": ["a1"], "failed": [], "total": 2})
    assert r.status_code == 200 and r.json() == {"ok": False, "status": "ended", "endReason": "stopped_employer"}
    f = row(fid)
    assert f.status == "ended" and f.last_lat is None and f.last_lon is None and f.last_acc is None and f.last_at is None and f.progress == ""


def test_a_point_written_while_the_follow_is_being_ended_is_still_deleted(monkeypatch):
    """Ters yön: bitirme isteği satırı "henüz nokta yok" diye yükledi; kapanmadan hemen önce bir konum isteği nokta yazdı.
    Bitirme yine de noktayı ve ilerlemeyi siler (boş sanılan alanlar da yazılır)."""
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)  # henüz nokta yok
    assert row(fid).last_lat is None
    real = events.log

    def log_after_a_point_landed(db, kind, *a, **k):
        if kind == "follow.end":
            other_request(fid, last_lat=LAT, last_lon=LON, last_acc=8.0, last_at=now(), progress='{"done":["a1"],"failed":[],"total":2}')
        return real(db, kind, *a, **k)

    monkeypatch.setattr(events, "log", log_after_a_point_landed)
    r = client.post(f"/v1/follow/{fid}/stop", headers=auth(e))
    assert r.status_code == 200 and r.json()["status"] == "ended"
    f = row(fid)
    assert f.status == "ended" and f.last_lat is None and f.last_lon is None and f.last_acc is None and f.last_at is None and f.progress == ""


def test_employer_can_cancel_a_waiting_request_and_courier_can_decline_it():
    e, _ = new("employer")
    c, cid = new("courier")
    f1 = ask(e, cid).json()["id"]
    assert client.post(f"/v1/follow/{f1}/stop", headers=auth(e)).json()["endReason"] == "cancelled"
    f2 = ask(e, cid).json()["id"]
    assert f2 != f1
    d = client.post(f"/v1/follow/{f2}/decline", headers=auth(c)).json()
    assert d["status"] == "ended" and d["endReason"] == "declined"
    assert client.post(f"/v1/follow/{f2}/accept", headers=auth(c)).status_code == 410  # bitmiş isteği kabul edemez


def test_declined_request_cannot_be_repeated_at_once():
    e, _ = new("employer")
    e2, _ = new("employer")
    c, cid = new("courier")
    f = ask(e, cid).json()["id"]
    client.post(f"/v1/follow/{f}/decline", headers=auth(c))
    r = ask(e, cid)
    assert r.status_code == 429 and "reddetti" in r.json()["detail"]
    assert ask(e2, cid).status_code == 200  # başka işveren etkilenmez
    edit(f, ended_at=now() - timedelta(minutes=11))
    assert ask(e, cid).status_code == 200  # bekleme geçti


def test_a_courier_has_one_active_follow_at_a_time():
    a, _ = new("employer")
    b, _ = new("employer")
    c, cid = new("courier")
    fa = start(a, c, cid)
    r = ask(b, cid)
    assert r.status_code == 409 and "başka bir takipte" in r.json()["detail"]
    # B önce istek attı, A sonra aktif oldu: B'nin isteği kabul edilemez
    c2, cid2 = new("courier")
    fb = ask(b, cid2).json()["id"]
    fa2 = ask(a, cid2).json()["id"]
    assert client.post(f"/v1/follow/{fa2}/accept", headers=auth(c2)).status_code == 200
    r2 = client.post(f"/v1/follow/{fb}/accept", headers=auth(c2))
    assert r2.status_code == 409 and "önce onu durdur" in r2.json()["detail"]
    # A bitirince B'nin isteği kabul edilebilir
    client.post(f"/v1/follow/{fa2}/stop", headers=auth(a))
    assert client.post(f"/v1/follow/{fb}/accept", headers=auth(c2)).status_code == 200
    assert row(fa).status == "active"


def test_one_employer_can_follow_several_couriers():
    a, _ = new("employer")
    c1, id1 = new("courier")
    c2, id2 = new("courier")
    f1, f2 = start(a, c1, id1), start(a, c2, id2)
    ping(c1, f1, lat=36.1, lon=32.1)
    ping(c2, f2, lat=36.2, lon=32.2)
    items = client.get("/v1/follow/mine", headers=auth(a)).json()["items"]
    got = {x["peer"]["id"]: x["location"]["lat"] for x in items}
    assert got == {id1: 36.1, id2: 36.2}


# ------------------------------------------------------------------ otomatik kapanma
def test_request_expires_and_cannot_be_accepted():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = ask(e, cid).json()["id"]
    edit(fid, expires_at=now() - timedelta(seconds=1))
    r = client.post(f"/v1/follow/{fid}/accept", headers=auth(c))
    assert r.status_code == 410
    assert row(fid).status == "ended" and row(fid).end_reason == "expired"
    assert ask(e, cid).status_code == 200  # süresi dolan istek yenisini engellemez


def test_active_follow_ends_at_its_time_limit_and_location_is_deleted():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid)
    edit(fid, expires_at=now() - timedelta(seconds=1))
    j = get(e, fid).json()
    assert j["status"] == "ended" and j["endReason"] == "expired" and "location" not in j
    assert row(fid).last_lat is None
    assert ping(c, fid).json()["ok"] is False


def test_acceptance_sets_the_time_limit_from_now():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = ask(e, cid).json()["id"]
    edit(fid, expires_at=now() + timedelta(minutes=1))
    j = client.post(f"/v1/follow/{fid}/accept", headers=auth(c)).json()
    exp = row(fid).expires_at
    left = (exp.replace(tzinfo=exp.tzinfo or now().tzinfo) - now()).total_seconds()
    assert 11 * 3600 < left <= 12 * 3600 + 5 and j["acceptedAt"]


def test_stale_point_is_deleted_but_the_follow_continues():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid)
    edit(fid, last_at=now() - timedelta(minutes=31))
    j = get(e, fid).json()
    assert j["status"] == "active" and "location" not in j
    f = row(fid)
    assert f.last_lat is None and f.last_lon is None and f.last_at is None
    ping(c, fid, lat=40.0, lon=30.0)  # uygulama yeniden açıldı
    assert get(e, fid).json()["location"]["lat"] == 40.0


def test_a_point_just_inside_the_limit_is_still_shown():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid)
    edit(fid, last_at=now() - timedelta(minutes=29))
    loc = get(e, fid).json()["location"]
    assert 28 * 60 < loc["ageSec"] < 30 * 60


def test_switching_to_employer_mode_ends_location_sharing():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid)
    assert client.post("/v1/me/role", json={"role": "employer"}, headers=auth(c)).status_code == 200
    r = ping(c, fid)
    assert r.json() == {"ok": False, "status": "ended", "endReason": "role"}
    j = get(e, fid).json()
    assert j["status"] == "ended" and j["endReason"] == "role" and "location" not in j
    assert row(fid).last_lat is None


def test_ban_ends_the_follow_and_deletes_the_point():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid)
    h, _ = admin_login()
    assert client.post(f"/v1/admin/users/{cid}/ban", json={"reason": "deneme"}, headers=h).status_code == 200
    follow_router.sweep(_db := SessionLocal())
    _db.close()
    f = row(fid)
    assert f.status == "ended" and f.end_reason == "ban" and f.last_lat is None
    assert get(e, fid).json()["status"] == "ended"
    # men edilen kurye zaten hiçbir uca erişemez
    assert ping(c, fid).status_code == 403


def test_sweep_closes_expired_and_removes_old_ended_rows():
    e, _ = new("employer")
    c, cid = new("courier")
    c2, cid2 = new("courier")
    f1 = start(e, c, cid)
    f2 = ask(e, cid2).json()["id"]
    edit(f1, expires_at=now() - timedelta(seconds=1))
    edit(f2, expires_at=now() - timedelta(seconds=1))
    db = SessionLocal()
    try:
        n = follow_router.sweep(db)
    finally:
        db.close()
    assert n == 2
    assert row(f1).status == "ended" and row(f2).status == "ended"
    edit(f1, ended_at=now() - timedelta(hours=25))
    db = SessionLocal()
    try:
        assert follow_router.sweep(db) == 1
    finally:
        db.close()
    assert row(f1) is None and row(f2) is not None
    assert get(e, f1).status_code == 404


# ------------------------------------------------------------------ yönetim ve kayıtlar
def test_admin_never_sees_the_location_and_events_carry_no_coordinates():
    e, eid = new("employer", "İşletme Bir")
    c, cid = new("courier", "Kurye Bir")
    fid = start(e, c, cid)
    ping(c, fid)
    client.post(f"/v1/follow/{fid}/stop", headers=auth(c))
    fid2 = start(e, c, cid)
    ping(c, fid2, lat=LAT + 0.0001, lon=LON + 0.0001)
    h, _ = admin_login()
    seen = []
    for path in ("/v1/admin/events?limit=300", f"/v1/admin/users/{cid}", f"/v1/admin/users/{eid}", "/v1/admin/stats", "/v1/admin/users", "/v1/admin/audit", "/v1/admin/reviews"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, path
        seen.append(r.text)
    blob = " ".join(seen)
    assert "36.5432" not in blob and "32.1234" not in blob and "36.5433" not in blob and "last_lat" not in blob
    # yönetim akışında takip olayları var (kim kimden istedi / başladı / bitti), konumsuz
    ev = client.get("/v1/admin/events?limit=300", headers=h).json()
    kinds = {x["kind"] for x in ev["items"]}
    assert {"follow.ask", "follow.start", "follow.end"} <= kinds
    assert ev["kinds"]["follow.ask"] and ev["kinds"]["follow.start"] and ev["kinds"]["follow.end"]
    mine = [x for x in ev["items"] if x["kind"].startswith("follow.") and x["userId"] in (eid, cid)]
    assert any(x["kind"] == "follow.end" and x["meta"].get("reason") == "stopped_courier" for x in mine)
    for x in mine:
        assert set(x["meta"]) <= {"reason", "was", "to"}


def test_no_admin_route_exposes_follow_data():
    paths = [r.path for r in app.routes if getattr(r, "path", "").startswith("/v1/admin")]
    assert not [p for p in paths if "follow" in p.lower() or "takip" in p.lower() or "location" in p.lower()]


def test_follow_needs_a_session_and_an_old_token_is_refused():
    assert client.get("/v1/follow/mine").status_code == 401
    assert client.get("/v1/follow/abc").status_code == 401
    assert client.post("/v1/follow/abc/accept").status_code == 401
    assert client.post("/v1/follow/abc/ping", json={"lat": 1, "lon": 1}).status_code == 401


def test_request_limit_per_employer():
    e, _ = new("employer")
    ids = [new("courier")[1] for _ in range(31)]
    codes = [ask(e, i).status_code for i in ids]
    assert codes[:30] == [200] * 30 and codes[30] == 429
