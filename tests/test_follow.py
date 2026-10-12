"""Takip ("Follow"): işveren, kuryeyi harita üzerinde canlı izler. İzin kuryeden gelir. CANLI konumu yalnız o takibin işvereni,
yalnız takip sürerken görür; bitince canlı nokta silinir. İZ (seyreltilmiş geçmiş noktalar) ve SON İLERLEME takip bittikten sonra
48 saat saklanır (işveren, kurye ve yönetim görür), sonra silinir."""
import json
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import events
from app.db import SessionLocal
from app.main import app, follow_router
from app.models import Event, Follow, FollowPoint, User, now

from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)

LAT, LON = 36.5432101, 32.1234509  # aranabilir, başka yerde geçmeyen sayılar


def no_ts(s: str) -> str:
    """ISO zaman damgalarını (mikrosaniyeli) çıkarır: kısa sayı aramaları ("36.7") tesadüfen saniye + mikrosaniye parçasına denk gelmesin."""
    return re.sub(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(?:[+-]\d{2}:\d{2}|Z)?", "", s)


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


def trail(tok, fid):
    return client.get(f"/v1/follow/{fid}/trail", headers=auth(tok))


def points_in_db(fid) -> int:
    db = SessionLocal()
    try:
        return db.query(FollowPoint).filter(FollowPoint.follow_id == fid).count()
    finally:
        db.close()


def sweep_now() -> int:
    db = SessionLocal()
    try:
        return follow_router.sweep(db)
    finally:
        db.close()


def ping_trail(tok, fid, lat=LAT, lon=LON, **kw):
    """İz seyreltmesini beklemeden bir iz noktası yazdırır (bir önceki iz noktasını 20 sn geriye çeker)."""
    f = row(fid)
    if f.trail_at is not None:
        edit(fid, trail_at=now() - timedelta(seconds=20))
    return ping(tok, fid, lat=lat, lon=lon, **kw)


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


def test_live_point_keeps_only_the_latest_and_the_trail_is_thinned():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    for i in range(5):
        assert ping(c, fid, lat=36.0 + i / 1000, lon=32.0 + i / 1000).status_code == 200
    f = row(fid)
    assert (f.last_lat, f.last_lon) == (36.004, 32.004)  # canlı nokta: yalnız sonuncusu
    assert f.trail_n == 1 and points_in_db(fid) == 1  # iz: art arda gelen 5 konumdan yalnız ilki (15 sn aralık)
    db = SessionLocal()
    try:
        assert db.query(Follow).filter(Follow.courier_id == cid).count() == 1  # her nokta yeni satır açmaz
        # koordinat başka hiçbir tabloya yazılmaz: etkinlik akışında yok
        blob = " ".join((e_.meta or "") for e_ in db.query(Event).all())
        assert "36.0" not in blob and "32.0" not in blob and str(LAT) not in blob
    finally:
        db.close()


def test_trail_gets_a_point_every_15_seconds_and_stops_at_the_cap():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    for i in range(4):
        assert ping_trail(c, fid, lat=36.0 + i / 100, lon=32.0).status_code == 200
    assert row(fid).trail_n == 4 and points_in_db(fid) == 4
    # aynı saniyedeki ikinci konum canlı noktayı günceller ama iz noktası eklemez
    assert ping(c, fid, lat=36.5, lon=32.5).status_code == 200
    assert row(fid).trail_n == 4 and points_in_db(fid) == 4 and row(fid).last_lat == 36.5
    # sınır: 4000 iz noktası dolunca yenisi yazılmaz (canlı nokta yine güncellenir)
    edit(fid, trail_n=4000, trail_at=now() - timedelta(seconds=60))
    assert ping(c, fid, lat=36.6, lon=32.6).status_code == 200
    assert points_in_db(fid) == 4 and row(fid).last_lat == 36.6


def test_trail_is_visible_to_the_employer_and_the_courier_during_and_after_the_follow():
    e, _ = new("employer")
    c, cid = new("courier")
    stranger, _ = new("employer")
    fid = start(e, c, cid)
    ping_trail(c, fid, lat=36.1, lon=32.1)
    ping_trail(c, fid, lat=36.2, lon=32.2)
    for who in (e, c):
        j = trail(who, fid).json()
        assert j["id"] == fid and j["n"] == 2 and [p[1:] for p in j["points"]] == [[36.1, 32.1], [36.2, 32.2]]
        assert all(isinstance(p[0], int) for p in j["points"])
    assert trail(stranger, fid).status_code == 404  # başkasının takibinin varlığı açıklanmaz
    assert client.get(f"/v1/follow/{fid}/trail").status_code == 401
    client.post(f"/v1/follow/{fid}/stop", headers=auth(c))
    for who in (e, c):  # bittikten sonra da 48 saat görünür
        assert trail(who, fid).json()["n"] == 2
    assert trail(stranger, fid).status_code == 404


def test_trail_is_not_extended_after_the_follow_ended():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping_trail(c, fid)
    client.post(f"/v1/follow/{fid}/stop", headers=auth(e))
    n = points_in_db(fid)
    edit(fid, trail_at=now() - timedelta(seconds=60))
    assert ping(c, fid).json()["ok"] is False
    assert points_in_db(fid) == n and row(fid).last_lat is None


def test_long_trails_are_thinned_but_keep_the_first_and_last_point():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    db = SessionLocal()
    try:
        t0 = now() - timedelta(hours=3)
        for i in range(2400):
            db.add(FollowPoint(follow_id=fid, at=t0 + timedelta(seconds=i), lat=36.0 + i / 100000, lon=32.0))
        db.commit()
    finally:
        db.close()
    pts = trail(e, fid).json()["points"]
    assert len(pts) == 1500 and pts[0][1] == 36.0 and pts[-1][1] == round(36.0 + 2399 / 100000, 6)
    assert [p[0] for p in pts] == sorted(p[0] for p in pts)


def test_trail_requests_are_rate_limited():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    assert {trail(e, fid).status_code for _ in range(60)} == {200}
    assert trail(e, fid).status_code == 429


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


def test_progress_ids_stay_random_codes_and_nothing_else_is_stored_in_them():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    prog = {"done": ["local-1700000000000-ab12", "Barbaros Cad. No:227 Daire 4", "stop-1700000000001"], "failed": ["x y", "venue-1"], "total": 7}
    assert ping(c, fid, progress=prog).status_code == 200
    p = get(e, fid).json()["progress"]
    t = p.pop("t")
    assert p.pop("a") == {}  # etiket gönderilmedi: boş
    assert p == {"done": ["local-1700000000000-ab12", "stop-1700000000001"], "failed": ["venue-1"], "total": 7}
    assert set(t) == {"local-1700000000000-ab12", "stop-1700000000001", "venue-1"}
    # kimlik alanına yazılan adres metni kabul edilmez (kimlikler yalnız rastgele kodlardır)
    assert "Barbaros" not in json.dumps(p) and "Barbaros" not in (row(fid).progress or "")
    # ilerleme göndermeyen konum, öncekini silmez
    assert ping(c, fid).status_code == 200
    assert get(e, fid).json()["progress"]["total"] == 7
    big = {"done": [f"a{i}" for i in range(501)], "failed": [], "total": 0}
    assert client.post(f"/v1/follow/{fid}/ping", json={"lat": 1, "lon": 1, "progress": big}, headers=auth(c)).status_code == 422


def test_progress_keeps_a_short_address_label_for_each_finished_stop():
    """Kullanıcı kararı (12 Ekim 2026): şikayette "hangi kapıya ne zaman gidildi" bakılabilsin: biten her durağın sokak adı + kapı no."""
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    labels = {
        "s1": "Barbaros Cad. No:11 · Mahmutlar",
        "s2": "  Gül   Sk.\tNo:9 \n· Oba ",
        "s3": "Atatürk Cad. No:227/B · Cumhuriyet",
        "p9": "Henüz bitmemiş durağın adresi",  # biten değil: saklanmaz
        "zz": "Listede olmayan durak",             # done/failed'de yok: saklanmaz
    }
    assert ping(c, fid, progress={"done": ["s1", "s2"], "failed": ["s3"], "total": 6, "a": labels}).status_code == 200
    expect = {"s1": "Barbaros Cad. No:11 · Mahmutlar", "s2": "Gül Sk. No:9 · Oba", "s3": "Atatürk Cad. No:227/B · Cumhuriyet"}
    # işveren, kurye: canlı görünümde de, listede de
    for tok in (e, c):
        assert get(tok, fid).json()["progress"]["a"] == expect
        it = [x for x in client.get("/v1/follow/mine", headers=auth(tok)).json()["items"] if x["id"] == fid][0]
        assert it["progress"]["a"] == expect
    # üçüncü kişi göremez
    o, _ = new("courier", "Başkası")
    assert get(o, fid).status_code == 404
    # sonraki konum etiket göndermese de eskiler kalır; yeni biten durak için yeni etiket eklenir; düzeltilen etiket yenilenir
    assert ping(c, fid, progress={"done": ["s1", "s2", "s4"], "failed": ["s3"], "total": 6, "a": {"s4": "Menderes Bulvarı No:3 · Kestel", "s1": "Barbaros Cad. No:13 · Mahmutlar"}}).status_code == 200
    a = get(e, fid).json()["progress"]["a"]
    assert a == {**expect, "s1": "Barbaros Cad. No:13 · Mahmutlar", "s4": "Menderes Bulvarı No:3 · Kestel"}
    # durak listeden çıkınca (biten sayılmaz) etiketi de gider
    assert ping(c, fid, progress={"done": ["s1"], "failed": [], "total": 6}).status_code == 200
    assert get(e, fid).json()["progress"]["a"] == {"s1": "Barbaros Cad. No:13 · Mahmutlar"}
    # takip bitince de (48 saat) görünür
    client.post(f"/v1/follow/{fid}/stop", json={"reason": "done"}, headers=auth(c))
    for tok in (e, c):
        it = [x for x in client.get("/v1/follow/mine", headers=auth(tok)).json()["items"] if x["id"] == fid][0]
        assert it["status"] == "ended" and it["progress"]["a"] == {"s1": "Barbaros Cad. No:13 · Mahmutlar"}


def test_address_labels_that_look_like_phone_email_or_link_are_dropped_and_long_ones_cut():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    bad = {
        "a1": "Ayşe Hanım 0532 111 22 33",
        "a2": "ayse@ornek.com",
        "a3": "https://ornek.com/x",
        "a4": "www.ornek.com",
        "a5": "05321112233",
        "a6": "+90 (532) 111-22-33",
        "a7": "   ",
        "ok1": "2001. Sokak No:125 · Kestel",   # birkaç rakam serbest (sokak adı + numara)
        "long": "Çok Uzun Cadde " * 20,
    }
    assert ping(c, fid, progress={"done": list(bad), "failed": [], "total": 9, "a": bad}).status_code == 200
    a = get(e, fid).json()["progress"]["a"]
    assert set(a) == {"ok1", "long"}
    assert a["ok1"] == "2001. Sokak No:125 · Kestel"
    assert len(a["long"]) <= 90 and a["long"].startswith("Çok Uzun Cadde")
    # kötü etiket, önceki iyi etiketi silmez
    assert ping(c, fid, progress={"done": ["ok1"], "failed": [], "total": 9, "a": {"ok1": "0532 111 22 33"}}).status_code == 200
    assert get(e, fid).json()["progress"]["a"]["ok1"] == "2001. Sokak No:125 · Kestel"
    # aşırı uzun etiket ya da kimlik isteği reddeder (kötüye kullanım)
    for body in ({"done": ["x"], "total": 1, "a": {"x": "y" * 301}}, {"done": ["x"], "total": 1, "a": {"k" * 41: "y"}}, {"done": [], "total": 0, "a": {f"k{i}": "y" for i in range(1001)}}):
        assert client.post(f"/v1/follow/{fid}/ping", json={"lat": 1, "lon": 1, "progress": body}, headers=auth(c)).status_code == 422


# ------------------------------------------------------------------ bitirme: konum silinir
@pytest.mark.parametrize("who,reason,body", [("c", "stopped_courier", None), ("e", "stopped_employer", None), ("c", "done", {"reason": "done"}), ("e", "stopped_employer", {"reason": "done"})])
def test_stopping_deletes_the_live_location_but_keeps_the_trail_and_last_progress(who, reason, body):
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid, progress={"done": ["a1"], "failed": [], "total": 2})
    r = client.post(f"/v1/follow/{fid}/stop", json=body, headers=auth(e if who == "e" else c)) if body else client.post(f"/v1/follow/{fid}/stop", headers=auth(e if who == "e" else c))
    assert r.status_code == 200 and r.json()["status"] == "ended" and r.json()["endReason"] == reason
    f = row(fid)
    # CANLI nokta silinir; son ilerleme ve iz kalır (48 saat)
    assert f.status == "ended" and f.last_lat is None and f.last_lon is None and f.last_acc is None and f.last_at is None
    assert json.loads(f.progress)["done"] == ["a1"] and f.trail_n == 1 and points_in_db(fid) == 1
    for who_tok in (e, c):
        j = get(who_tok, fid).json()
        assert j["status"] == "ended" and "location" not in j
        assert j["progress"]["done"] == ["a1"] and j["progress"]["total"] == 2 and j["trailN"] == 1
    # sonrasında kurye konum gönderse de alınmaz
    pr = ping(c, fid)
    assert pr.json() == {"ok": False, "status": "ended", "endReason": reason}
    assert row(fid).last_lat is None and points_in_db(fid) == 1
    # ikinci durdurma zararsız
    assert client.post(f"/v1/follow/{fid}/stop", headers=auth(c)).status_code == 200


def test_courier_sends_the_final_progress_with_the_stop_request_so_the_last_delivery_is_never_missing():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid, progress={"done": ["a1"], "failed": [], "total": 3, "a": {"a1": "Gül Sk. No:1 · Oba"}})
    # son teslim konum yoklaması gitmeden takip "bitirdim" ile kapanırsa kayıtta eksik kalmamalı
    body = {"reason": "done", "progress": {"done": ["a1", "a2"], "failed": ["a3"], "total": 3, "a": {"a2": "Atatürk Cad. No:227 · Cumhuriyet", "a3": "Menderes Bulvarı No:3 · Kestel"}}}
    r = client.post(f"/v1/follow/{fid}/stop", json=body, headers=auth(c))
    assert r.status_code == 200 and r.json()["endReason"] == "done"
    for tok in (e, c):
        p = get(tok, fid).json()["progress"]
        assert p["done"] == ["a1", "a2"] and p["failed"] == ["a3"] and p["total"] == 3
        assert p["a"] == {"a1": "Gül Sk. No:1 · Oba", "a2": "Atatürk Cad. No:227 · Cumhuriyet", "a3": "Menderes Bulvarı No:3 · Kestel"}
        assert set(p["t"]) == {"a1", "a2", "a3"}  # saatleri sunucu damgalar
    f = row(fid)
    assert f.last_lat is None and f.last_lon is None
    # kurye kendisi durdurunca da (Durdur düğmesi) son ilerleme kayda girer
    e2, _ = new("employer")
    c2, cid2 = new("courier")
    fid2 = start(e2, c2, cid2)
    r = client.post(f"/v1/follow/{fid2}/stop", json={"progress": {"done": ["z1"], "failed": [], "total": 1, "a": {"z1": "Gül Sk. No:5 · Oba"}}}, headers=auth(c2))
    assert r.status_code == 200 and r.json()["endReason"] == "stopped_courier"
    assert get(e2, fid2).json()["progress"]["a"] == {"z1": "Gül Sk. No:5 · Oba"}


def test_only_the_couriers_own_progress_is_accepted_and_only_while_the_follow_is_active():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid, progress={"done": ["a1"], "failed": [], "total": 3})
    # işveren durdururken gönderdiği "ilerleme" yok sayılır
    r = client.post(f"/v1/follow/{fid}/stop", json={"progress": {"done": ["a1", "x9"], "failed": [], "total": 3, "a": {"x9": "Sahte Sk. No:1"}}}, headers=auth(e))
    assert r.status_code == 200 and r.json()["endReason"] == "stopped_employer"
    p = json.loads(row(fid).progress)
    assert p["done"] == ["a1"] and p["a"] == {}
    # bittikten sonra kurye kaydı yeniden yazamaz
    r = client.post(f"/v1/follow/{fid}/stop", json={"reason": "done", "progress": {"done": ["a1", "b2"], "failed": [], "total": 3}}, headers=auth(c))
    assert r.status_code == 200 and r.json()["endReason"] == "stopped_employer"
    assert json.loads(row(fid).progress)["done"] == ["a1"]
    # bekleyen istekte (henüz kabul yok) gönderilen ilerleme kaydedilmez
    e3, _ = new("employer")
    c3, cid3 = new("courier")
    fid3 = ask(e3, cid3).json()["id"]
    r = client.post(f"/v1/follow/{fid3}/stop", json={"progress": {"done": ["q1"], "failed": [], "total": 1}}, headers=auth(c3))
    assert r.status_code == 200 and r.json()["endReason"] == "declined"
    assert not (row(fid3).progress or "")


def test_stop_progress_is_validated_like_a_ping():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    for bad in ({"done": ["x"], "total": -1}, {"done": ["x"], "total": 1, "a": {"x": "y" * 301}}, {"done": [str(i) for i in range(501)], "total": 1}):
        assert client.post(f"/v1/follow/{fid}/stop", json={"progress": bad}, headers=auth(c)).status_code == 422
    assert get(c, fid).json()["status"] == "active"  # geçersiz istek takibi kapatmadı
    # telefon benzeri etiket atılır
    r = client.post(f"/v1/follow/{fid}/stop", json={"reason": "done", "progress": {"done": ["k1"], "failed": [], "total": 1, "a": {"k1": "0532 111 22 33"}}}, headers=auth(c))
    assert r.status_code == 200
    assert get(e, fid).json()["progress"]["a"] == {}


def test_progress_times_are_stamped_by_the_server_and_kept_when_seen_again():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping(c, fid, progress={"done": ["a1"], "failed": [], "total": 3})
    t1 = get(e, fid).json()["progress"]["t"]["a1"]
    assert abs(t1 - int(now().timestamp())) <= 5
    # eski bir an yazıp tekrar gönder: bilinen kimliğin anı değişmez, yeni kimlik yeni an alır
    db = SessionLocal()
    try:
        f = db.get(Follow, fid)
        d = json.loads(f.progress)
        d["t"]["a1"] = t1 - 600
        f.progress = json.dumps(d)
        db.commit()
    finally:
        db.close()
    ping(c, fid, progress={"done": ["a1", "a2"], "failed": [], "total": 3})
    t = get(e, fid).json()["progress"]["t"]
    assert t["a1"] == t1 - 600 and abs(t["a2"] - int(now().timestamp())) <= 5
    # başarısız → teslim: an yenilenir
    ping(c, fid, progress={"done": ["a1"], "failed": ["a2"], "total": 3})
    assert get(e, fid).json()["progress"]["t"]["a2"] >= t["a2"]
    edit(fid, progress=json.dumps({"done": ["a1"], "failed": ["a2"], "total": 3, "t": {"a1": 1, "a2": 5}}))
    ping(c, fid, progress={"done": ["a1", "a2"], "failed": [], "total": 3})
    assert get(e, fid).json()["progress"]["t"]["a2"] > 5
    # artık listede olmayan kimliğin anı tutulmaz
    ping(c, fid, progress={"done": ["a1"], "failed": [], "total": 3})
    assert set(get(e, fid).json()["progress"]["t"]) == {"a1"}


def test_my_follows_list_keeps_ended_follows_with_progress_for_both_sides():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping_trail(c, fid, progress={"done": ["a1", "a2"], "failed": ["a3"], "total": 4})
    client.post(f"/v1/follow/{fid}/stop", json={"reason": "done"}, headers=auth(c))
    for tok, side in ((e, "employer"), (c, "courier")):
        items = client.get("/v1/follow/mine", headers=auth(tok)).json()["items"]
        it = [x for x in items if x["id"] == fid][0]
        assert it["side"] == side and it["status"] == "ended" and it["endReason"] == "done" and it["trailN"] == 1
        assert it["progress"]["done"] == ["a1", "a2"] and it["progress"]["failed"] == ["a3"] and it["progress"]["total"] == 4 and it["progress"]["a"] == {}
        assert "location" not in it
    # bitmiş ama ilerlemesiz kayıtlarda (ör. reddedilen istek) ilerleme alanı yoktur
    f2 = ask(e, cid).json()["id"]
    client.post(f"/v1/follow/{f2}/decline", headers=auth(c))
    it2 = [x for x in client.get("/v1/follow/mine", headers=auth(e)).json()["items"] if x["id"] == f2][0]
    assert it2["status"] == "ended" and "progress" not in it2 and it2["trailN"] == 0


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
    Nokta (canlı de, iz de) yazılmaz, istemcinin ilerlemesi bitmiş kaydın üstüne yazılmaz, istemciye "bitti" denir."""
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    FINAL = '{"done":["a0"],"failed":[],"total":2,"t":{"a0":1}}'
    ping_win = follow_router.limiters[1]
    real = ping_win.allow

    def allow_after_the_follow_was_ended(key):
        other_request(fid, status="ended", end_reason="stopped_employer", ended_at=now(), last_lat=None, last_lon=None, last_acc=None, last_at=None, progress=FINAL)
        return real(key)

    monkeypatch.setattr(ping_win, "allow", allow_after_the_follow_was_ended)
    r = ping(c, fid, progress={"done": ["a1"], "failed": [], "total": 2})
    assert r.status_code == 200 and r.json() == {"ok": False, "status": "ended", "endReason": "stopped_employer"}
    f = row(fid)
    assert f.status == "ended" and f.last_lat is None and f.last_lon is None and f.last_acc is None and f.last_at is None
    assert f.progress == FINAL and (f.trail_n or 0) == 0 and points_in_db(fid) == 0


def test_a_point_written_while_the_follow_is_being_ended_is_still_deleted(monkeypatch):
    """Ters yön: bitirme isteği satırı "henüz nokta yok" diye yükledi; kapanmadan hemen önce bir konum isteği nokta yazdı.
    Bitirme yine de canlı noktayı siler (boş sanılan alanlar da yazılır); son ilerleme (48 saat saklanır) olduğu gibi kalır."""
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)  # henüz nokta yok
    assert row(fid).last_lat is None
    real = events.log

    def log_after_a_point_landed(db, kind, *a, **k):
        if kind == "follow.end":
            other_request(fid, last_lat=LAT, last_lon=LON, last_acc=8.0, last_at=now(), progress='{"done":["a1"],"failed":[],"total":2,"t":{"a1":5}}')
        return real(db, kind, *a, **k)

    monkeypatch.setattr(events, "log", log_after_a_point_landed)
    r = client.post(f"/v1/follow/{fid}/stop", headers=auth(e))
    assert r.status_code == 200 and r.json()["status"] == "ended"
    f = row(fid)
    assert f.status == "ended" and f.last_lat is None and f.last_lon is None and f.last_acc is None and f.last_at is None
    assert json.loads(f.progress)["done"] == ["a1"]


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


def test_sweep_closes_expired_and_removes_ended_rows_with_their_trail_after_48_hours():
    e, _ = new("employer")
    c, cid = new("courier")
    c2, cid2 = new("courier")
    f1 = start(e, c, cid)
    ping_trail(c, f1, progress={"done": ["a1"], "failed": [], "total": 1})
    f2 = ask(e, cid2).json()["id"]
    edit(f1, expires_at=now() - timedelta(seconds=1))
    edit(f2, expires_at=now() - timedelta(seconds=1))
    assert sweep_now() == 2
    assert row(f1).status == "ended" and row(f2).status == "ended"
    assert points_in_db(f1) == 1  # süre dolunca da iz kalır
    edit(f1, ended_at=now() - timedelta(hours=47))
    assert sweep_now() == 0  # 47 saat: hâlâ saklanır (iz ve son ilerleme dahil)
    assert row(f1) is not None and points_in_db(f1) == 1 and trail(e, f1).json()["n"] == 1
    edit(f1, ended_at=now() - timedelta(hours=49))
    assert sweep_now() == 1
    assert row(f1) is None and row(f2) is not None and points_in_db(f1) == 0
    assert get(e, f1).status_code == 404 and trail(e, f1).status_code == 404


def test_sweep_deletes_many_old_follows_and_all_their_points():
    e, _ = new("employer")
    ids = []
    for _i in range(3):
        c, cid = new("courier")
        fid = start(e, c, cid)
        ping_trail(c, fid)
        ping_trail(c, fid)
        client.post(f"/v1/follow/{fid}/stop", headers=auth(c))
        edit(fid, ended_at=now() - timedelta(hours=60))
        ids.append(fid)
    assert all(points_in_db(i) == 2 for i in ids)
    assert sweep_now() == 3
    assert all(row(i) is None and points_in_db(i) == 0 for i in ids)


def test_trail_survives_expiry_ban_and_role_change_endings():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping_trail(c, fid)
    edit(fid, expires_at=now() - timedelta(seconds=1))
    assert get(e, fid).json()["endReason"] == "expired"
    assert trail(e, fid).json()["n"] == 1 and row(fid).last_lat is None


# ------------------------------------------------------------------ yönetim ve kayıtlar
def test_events_and_lists_carry_no_coordinates():
    e, eid = new("employer", "İşletme Bir")
    c, cid = new("courier", "Kurye Bir")
    fid = start(e, c, cid)
    ping(c, fid)
    client.post(f"/v1/follow/{fid}/stop", headers=auth(c))
    fid2 = start(e, c, cid)
    ping(c, fid2, lat=LAT + 0.0001, lon=LON + 0.0001)
    h, _ = admin_login()
    seen = []
    for path in ("/v1/admin/events?limit=300", f"/v1/admin/users/{cid}", f"/v1/admin/users/{eid}", "/v1/admin/stats", "/v1/admin/users", "/v1/admin/audit", "/v1/admin/reviews", "/v1/admin/follows"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, path
        seen.append(r.text)
    blob = " ".join(seen)
    blob = no_ts(blob)
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


def test_admin_lists_the_follows_and_opens_the_trail_with_an_audit_entry():
    e, eid = new("employer", "İşletme Yönetim")
    c, cid = new("courier", "Kurye Yönetim")
    fid = start(e, c, cid)
    ping_trail(c, fid, lat=36.7001, lon=32.7001, progress={"done": ["s1", "s2"], "failed": ["s3"], "total": 5})
    ping_trail(c, fid, lat=36.7002, lon=32.7002, progress={"done": ["s1", "s2"], "failed": ["s3"], "total": 5})
    client.post(f"/v1/follow/{fid}/stop", headers=auth(c))
    h, name = admin_login()
    lst = client.get("/v1/admin/follows", headers=h).json()
    assert lst["keepHours"] == 48
    it = [x for x in lst["items"] if x["id"] == fid][0]
    assert it["status"] == "ended" and it["employer"] == {"id": eid, "name": "İşletme Yönetim"} and it["courier"] == {"id": cid, "name": "Kurye Yönetim"}
    assert (it["done"], it["failed"], it["total"], it["trailN"]) == (2, 1, 5, 2)
    assert "36.7001" not in no_ts(json.dumps(lst))  # listede konum yok
    # süzgeçler
    assert [x["id"] for x in client.get(f"/v1/admin/follows?user_id={cid}", headers=h).json()["items"]] == [fid]
    act = client.get("/v1/admin/follows?status=active", headers=h).json()["items"]
    assert fid not in [x["id"] for x in act] and all(x["status"] == "active" for x in act)
    # ayrıntı: iz ve durak anları; bakmak işlem günlüğüne yazılır
    d = client.get(f"/v1/admin/follows/{fid}", headers=h).json()
    assert [p[1:] for p in d["points"]] == [[36.7001, 32.7001], [36.7002, 32.7002]]
    assert [s["id"] for s in d["doneStops"]] == ["s1", "s2"] and d["failedStops"][0]["id"] == "s3" and all(s["at"] > 0 for s in d["doneStops"])
    aud = client.get("/v1/admin/audit?limit=20", headers=h).json()["items"]
    row_ = [x for x in aud if x["action"] == "follow_view" and x["target"] == fid]
    assert row_ and row_[0]["admin"] == name and row_[0]["detail"] == "2 iz noktası" and "36.7" not in no_ts(json.dumps(aud))
    assert client.get("/v1/admin/follows/yok", headers=h).status_code == 404


def test_admin_sees_address_labels_in_the_follow_detail_but_not_in_lists_events_or_audit():
    e, eid = new("employer", "İşletme Adres")
    c, cid = new("courier", "Kurye Adres")
    fid = start(e, c, cid)
    a = {"s1": "Barbaros Cad. No:11 · Mahmutlar", "s2": "Gül Sk. No:9 · Oba", "s3": "Atatürk Cad. No:227 · Cumhuriyet"}
    ping_trail(c, fid, progress={"done": ["s1", "s2"], "failed": ["s3"], "total": 4, "a": a})
    client.post(f"/v1/follow/{fid}/stop", json={"reason": "done"}, headers=auth(c))
    h, _ = admin_login()
    d = client.get(f"/v1/admin/follows/{fid}", headers=h).json()
    assert {s["id"]: s["label"] for s in d["doneStops"] + d["failedStops"]} == a
    # liste, etkinlik akışı, işlem günlüğü, üye sayfaları adres taşımaz
    blob = " ".join(client.get(path, headers=h).text for path in ("/v1/admin/follows", "/v1/admin/events?limit=300", "/v1/admin/audit", f"/v1/admin/users/{cid}", f"/v1/admin/users/{eid}"))
    assert "Barbaros" not in blob and "Atatürk Cad" not in blob
    # etiket gelmemiş (eski telefon sürümü) durakta boş etiket döner, bozulmaz
    e2, _ = new("employer")
    c2, cid2 = new("courier")
    f2 = start(e2, c2, cid2)
    ping_trail(c2, f2, progress={"done": ["z1"], "failed": [], "total": 1})
    d2 = client.get(f"/v1/admin/follows/{f2}", headers=h).json()
    assert d2["doneStops"][0]["id"] == "z1" and d2["doneStops"][0]["label"] == ""


def test_address_labels_are_deleted_with_the_record_after_48_hours():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    ping_trail(c, fid, progress={"done": ["s1"], "failed": [], "total": 1, "a": {"s1": "Barbaros Cad. No:11 · Mahmutlar"}})
    client.post(f"/v1/follow/{fid}/stop", json={"reason": "done"}, headers=auth(c))
    assert "Barbaros" in (row(fid).progress or "")
    edit(fid, ended_at=now() - timedelta(hours=47))
    sweep_now()
    assert row(fid) is not None and get(e, fid).json()["progress"]["a"]["s1"].startswith("Barbaros")
    edit(fid, ended_at=now() - timedelta(hours=49))
    sweep_now()
    assert row(fid) is None
    assert get(e, fid).status_code == 404


def test_follow_records_are_for_admins_only():
    e, _ = new("employer")
    c, cid = new("courier")
    fid = start(e, c, cid)
    assert client.get("/v1/admin/follows").status_code == 401
    assert client.get(f"/v1/admin/follows/{fid}").status_code == 401
    for tok in (e, c):  # uygulama oturumu yönetici değildir
        assert client.get("/v1/admin/follows", headers=auth(tok)).status_code == 401
        assert client.get(f"/v1/admin/follows/{fid}", headers=auth(tok)).status_code == 401


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
