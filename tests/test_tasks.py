"""Görev eşleşmesi — "Adres Listesi Paylaş" (1.14.0; kullanıcı kararları 22 Ekim 2026).

- Komutun adı "Adres Listesi Paylaş": işveren paylaşınca ilan / liste kuryeye BAĞLANIR, benzersiz 7 haneli GÖREV NO verilir.
- Kurye kabul edince aynı numarayla EŞLEŞİR; iki tarafta aynı numara görünür.
- Birebir: bir ilan aynı anda tek kuryeye; bir kurye aynı işverenden tek açık görev.
- Önce paylaş, sonra takip: canlı takip isteği yalnız eşleşmiş görevi olan kuryeye gider; takip görevin numarasını taşır.
"""
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app, follow_router, tasks_router
from app.models import Follow, Task, User, now

from test_auth_consent import auth, reg

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    for lim in (*follow_router.limiters, *tasks_router.limiters):
        lim.reset()
    yield
    for lim in (*follow_router.limiters, *tasks_router.limiters):
        lim.reset()


def new(role, name="Kişi"):
    r = reg(role=role, name=name).json()
    return r["token"], r["user"]["id"]


def share(tok, cid, ref="L:sekme-1", job=None, title="Mahmutlar", count=12):
    body = {"courierId": cid, "ref": ref, "title": title, "count": count}
    if job:
        body["jobId"] = job
    return client.post("/v1/tasks/share", json=body, headers=auth(tok))


def accept(tok, no):
    return client.post(f"/v1/tasks/{no}/accept", headers=auth(tok))


def get(tok, no):
    return client.get(f"/v1/tasks/{no}", headers=auth(tok))


def follow(tok, cid):
    return client.post("/v1/follow/request", json={"courierId": cid}, headers=auth(tok))


def make_job(tok, title="Mahmutlar teslimatı", n=12):
    r = client.post("/v1/jobs", json={"title": title, "lat": 36.5, "lon": 32.0, "packageCount": n, "area": "Mahmutlar"}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def matched(e, c, cid, **kw):
    """Paylaş + kabul: eşleşmiş görevin numarasını döner."""
    r = share(e, cid, **kw)
    assert r.status_code == 200, r.text
    no = r.json()["taskNo"]
    a = accept(c, no)
    assert a.status_code == 200, a.text
    assert a.json()["status"] == "matched"
    return no


def row(no) -> Task:
    db = SessionLocal()
    try:
        t = db.query(Task).filter(Task.task_no == no).first()
        if t is not None:
            db.expunge(t)
        return t
    finally:
        db.close()


def edit(no, **kw):
    db = SessionLocal()
    try:
        t = db.query(Task).filter(Task.task_no == no).first()
        for k, v in kw.items():
            setattr(t, k, v)
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ paylaş + eşleş
def test_share_gives_a_unique_task_no_and_the_courier_matches_the_same_no():
    e, eid = new("employer", "Ayşe Lojistik")
    c, cid = new("courier", "Burcu Yıldırım")
    r = share(e, cid)
    assert r.status_code == 200, r.text
    t = r.json()
    no = t["taskNo"]
    assert re.fullmatch(r"[1-9]\d{6}", no)  # 7 hane, ilk hane 0 değil
    assert t["status"] == "shared" and t["side"] == "employer" and t["title"] == "Mahmutlar" and t["count"] == 12
    assert t["courier"]["id"] == cid and t["courier"]["name"] == "Burcu Yıldırım" and re.fullmatch(r"\d{7}", t["courier"]["userNo"])
    # Kurye aynı numarayı görür (henüz kabul etmedi)
    mine = client.get("/v1/tasks/mine", headers=auth(c)).json()["items"]
    assert [x["taskNo"] for x in mine] == [no]
    assert mine[0]["side"] == "courier" and mine[0]["status"] == "shared" and mine[0]["employer"]["name"] == "Ayşe Lojistik"
    # Kabul: aynı numarayla eşleşir, iki tarafta da "matched"
    a = accept(c, no).json()
    assert a["taskNo"] == no and a["status"] == "matched" and a["matchedAt"]
    assert get(e, no).json()["status"] == "matched"
    assert accept(c, no).json()["status"] == "matched"  # tekrar kabul: aynı görev
    # Başka bir paylaşım başka numara alır
    c2, cid2 = new("courier")
    no2 = share(e, cid2, ref="L:sekme-2").json()["taskNo"]
    assert no2 != no


def test_only_the_employer_shares_and_only_the_bound_courier_accepts():
    e, eid = new("employer")
    c, cid = new("courier")
    s, _ = new("sponsor")
    other_c, ocid = new("courier")
    assert share(c, ocid).status_code == 403  # kurye paylaşamaz
    assert share(s, cid).status_code == 403  # sponsor da
    assert share(e, eid).status_code == 400  # kendine
    assert share(e, "yok-boyle-biri").status_code == 404
    e2, eid2 = new("employer")
    assert share(e, eid2).status_code == 400  # karşıdaki kurye modunda değil
    assert client.post("/v1/tasks/share", json={"courierId": cid, "ref": "a b"}, headers=auth(e)).status_code == 422  # anahtar biçimi
    assert client.post("/v1/tasks/share", json={"courierId": cid, "ref": "L:1"}).status_code == 401
    no = share(e, cid).json()["taskNo"]
    assert accept(other_c, no).status_code == 404  # başkasının görevi: varlığı bile açıklanmaz
    assert get(other_c, no).status_code == 404
    assert get(e2, no).status_code == 404
    assert accept(e, no).status_code == 403  # işveren kabul edemez
    assert accept(c, "1234567").status_code in (404,)  # olmayan numara


def test_one_ad_is_bound_to_one_courier_at_a_time():
    e, _ = new("employer")
    c1, cid1 = new("courier", "Kurye Bir")
    c2, cid2 = new("courier", "Kurye İki")
    j = make_job(e)
    no = share(e, cid1, ref=j, job=j).json()["taskNo"]
    r = share(e, cid2, ref=j, job=j)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert "Kurye Bir" in d and f"GRV-{no}" in d and "tek kuryeye" in d
    # başka anahtarla aynı ilan da verilemez
    assert share(e, cid2, ref="L:baska-anahtar", job=j).status_code == 409
    # aynı kuryeye yeniden: aynı görev (yeni numara yok)
    again = share(e, cid1, ref=j, job=j).json()
    assert again["taskNo"] == no and again["existing"] is True
    # kurye reddedince ilan boşa çıkar: başka kuryeye verilebilir (yeni numara)
    d1 = client.post(f"/v1/tasks/{no}/decline", headers=auth(c1)).json()
    assert d1["status"] == "ended" and d1["endReason"] == "declined"
    r2 = share(e, cid2, ref=j, job=j)
    assert r2.status_code == 200 and r2.json()["taskNo"] != no
    assert accept(c1, no).status_code == 410  # reddedilen görev kabul edilemez


def test_a_courier_has_one_open_task_per_employer():
    e, _ = new("employer")
    e2, _ = new("employer")
    c, cid = new("courier")
    no = matched(e, c, cid, ref="L:a")
    r = share(e, cid, ref="L:b")
    assert r.status_code == 409 and "bitmemiş bir görevi var" in r.json()["detail"] and f"GRV-{no}" in r.json()["detail"]
    assert share(e2, cid, ref="L:b").status_code == 200  # başka işletme etkilenmez
    done = client.post(f"/v1/tasks/{no}/end", json={"reason": "done"}, headers=auth(c)).json()
    assert done["status"] == "ended" and done["endReason"] == "done"
    assert share(e, cid, ref="L:b").status_code == 200  # teslimatlar bitti: yeni liste paylaşılır


def test_employer_and_courier_can_end_an_open_task():
    e, _ = new("employer")
    c, cid = new("courier")
    no = matched(e, c, cid)
    j = client.post(f"/v1/tasks/{no}/end", json={"reason": "done"}, headers=auth(e)).json()
    assert j["status"] == "ended" and j["endReason"] == "cancelled"  # işveren "done" diyemez: vazgeçer
    no2 = matched(e, c, cid, ref="L:2")
    j2 = client.post(f"/v1/tasks/{no2}/end", headers=auth(c)).json()
    assert j2["endReason"] == "stopped_courier"
    assert accept(c, no2).status_code == 410


# ------------------------------------------------------------------ önce paylaş, sonra takip
def test_live_follow_needs_a_matched_task():
    e, _ = new("employer")
    c, cid = new("courier")
    r = follow(e, cid)
    assert r.status_code == 409 and "Önce adres listesini paylaş" in r.json()["detail"]
    no = share(e, cid).json()["taskNo"]
    assert follow(e, cid).status_code == 409  # paylaşıldı ama kurye henüz kabul etmedi
    accept(c, no)
    f = follow(e, cid)
    assert f.status_code == 200, f.text
    assert f.json()["taskNo"] == no
    fid = f.json()["id"]
    # kurye tarafında da aynı görev numarası
    assert client.get(f"/v1/follow/{fid}", headers=auth(c)).json()["taskNo"] == no
    db = SessionLocal()
    try:
        assert db.get(Follow, fid).task_no == no
    finally:
        db.close()


def test_task_of_another_employer_does_not_open_the_follow():
    a, _ = new("employer")
    b, _ = new("employer")
    c, cid = new("courier")
    matched(a, c, cid)
    assert follow(b, cid).status_code == 409


def test_courier_finishing_all_deliveries_ends_the_task_too():
    e, _ = new("employer")
    c, cid = new("courier")
    no = matched(e, c, cid)
    fid = follow(e, cid).json()["id"]
    assert client.post(f"/v1/follow/{fid}/accept", headers=auth(c)).json()["status"] == "active"
    s = client.post(f"/v1/follow/{fid}/stop", json={"reason": "done"}, headers=auth(c)).json()
    assert s["endReason"] == "done"
    t = get(e, no).json()
    assert t["status"] == "ended" and t["endReason"] == "done"


def test_courier_stopping_location_sharing_keeps_the_task():
    e, _ = new("employer")
    c, cid = new("courier")
    no = matched(e, c, cid)
    fid = follow(e, cid).json()["id"]
    client.post(f"/v1/follow/{fid}/accept", headers=auth(c))
    client.post(f"/v1/follow/{fid}/stop", headers=auth(c))  # yalnız konum paylaşımı durdu
    assert get(e, no).json()["status"] == "matched"
    client.post(f"/v1/follow/{fid}/stop", headers=auth(e))
    assert get(e, no).json()["status"] == "matched"


def test_ending_the_task_ends_its_live_follow():
    e, _ = new("employer")
    c, cid = new("courier")
    no = matched(e, c, cid)
    fid = follow(e, cid).json()["id"]
    client.post(f"/v1/follow/{fid}/accept", headers=auth(c))
    p = client.post(f"/v1/follow/{fid}/ping", json={"lat": 36.6, "lon": 32.0}, headers=auth(c)).json()
    assert p["ok"] is True
    client.post(f"/v1/tasks/{no}/end", headers=auth(e))
    f = client.get(f"/v1/follow/{fid}", headers=auth(e)).json()
    assert f["status"] == "ended" and f["endReason"] == "stopped_employer" and "location" not in f
    assert client.post(f"/v1/follow/{fid}/ping", json={"lat": 36.6, "lon": 32.0}, headers=auth(c)).json()["ok"] is False
    # bekleyen istek de kapanır (kurye görevi bırakınca)
    no2 = matched(e, c, cid, ref="L:2")
    fid2 = follow(e, cid).json()["id"]
    client.post(f"/v1/tasks/{no2}/end", headers=auth(c))
    f2 = client.get(f"/v1/follow/{fid2}", headers=auth(e)).json()
    assert f2["status"] == "ended" and f2["endReason"] == "declined"


def test_closing_the_ad_ends_its_task_and_follow():
    e, _ = new("employer")
    c, cid = new("courier")
    j = make_job(e)
    no = matched(e, c, cid, ref=j, job=j)
    fid = follow(e, cid).json()["id"]
    client.post(f"/v1/follow/{fid}/accept", headers=auth(c))
    r = client.delete(f"/v1/jobs/{j}", headers=auth(e))
    assert r.status_code == 200 and r.json()["tasksEnded"] == 1
    t = get(c, no).json()
    assert t["status"] == "ended" and t["endReason"] == "job_closed"
    assert client.get(f"/v1/follow/{fid}", headers=auth(c)).json()["status"] == "ended"


def test_share_with_a_closed_or_foreign_ad_is_refused():
    e, _ = new("employer")
    e2, _ = new("employer")
    c, cid = new("courier")
    j = make_job(e)
    assert share(e2, cid, ref=j, job=j).status_code == 404  # başkasının ilanı
    client.delete(f"/v1/jobs/{j}", headers=auth(e))
    r = share(e, cid, ref=j, job=j)
    assert r.status_code == 400 and "yayında değil" in r.json()["detail"]


# ------------------------------------------------------------------ ilanlar
def test_bound_ad_is_not_offered_to_other_couriers():
    e, _ = new("employer")
    c, cid = new("courier")
    other, _ = new("courier")
    j = make_job(e)
    ids = lambda: {x["id"] for x in client.get("/v1/jobs/open", headers=auth(other)).json()}  # noqa: E731
    assert j in ids()
    no = share(e, cid, ref=j, job=j).json()["taskNo"]
    assert j not in ids()  # paylaşıldığı anda bağlı: başka kuryeye sunulmaz
    client.post(f"/v1/tasks/{no}/decline", headers=auth(c))
    assert j in ids()  # kurye reddetti: ilan yeniden açık


def test_employer_jobs_show_the_bound_courier_and_task_no():
    e, _ = new("employer")
    c, cid = new("courier", "Burcu Yıldırım")
    j = make_job(e)
    mine = lambda: {x["id"]: x for x in client.get("/v1/jobs/mine", headers=auth(e)).json()}  # noqa: E731
    assert mine()[j]["task"] is None
    no = share(e, cid, ref=j, job=j).json()["taskNo"]
    t = mine()[j]["task"]
    assert t["taskNo"] == no and t["status"] == "shared" and t["courier"]["name"] == "Burcu Yıldırım" and t["courier"]["userNo"]
    accept(c, no)
    assert mine()[j]["task"]["status"] == "matched"


# ------------------------------------------------------------------ süre, men, temizlik, veri
def test_open_task_expires_after_its_time_limit():
    e, _ = new("employer")
    c, cid = new("courier")
    c2, cid2 = new("courier")
    no = share(e, cid, ref="L:x").json()["taskNo"]
    edit(no, created_at=now() - timedelta(hours=25))
    assert accept(c, no).status_code == 410
    assert row(no).end_reason == "expired"
    assert share(e, cid2, ref="L:x").status_code == 200  # süresi dolan görev ilanı kilitlemez
    no3 = matched(e, c, cid, ref="L:y")
    edit(no3, matched_at=now() - timedelta(hours=25))
    assert follow(e, cid).status_code == 409  # süresi dolan görevle takip açılmaz
    assert row(no3).status == "ended" and row(no3).end_reason == "expired"


def test_banned_party_ends_the_task():
    e, _ = new("employer")
    c, cid = new("courier")
    no = matched(e, c, cid)
    db = SessionLocal()
    try:
        db.get(User, cid).banned = True
        db.commit()
    finally:
        db.close()
    t = get(e, no).json()
    assert t["status"] == "ended" and t["endReason"] == "ban"


def test_ended_tasks_are_deleted_after_48_hours():
    e, _ = new("employer")
    c, cid = new("courier")
    no = matched(e, c, cid)
    client.post(f"/v1/tasks/{no}/end", headers=auth(e))
    db = SessionLocal()
    try:
        tasks_router.sweep(db)
    finally:
        db.close()
    assert row(no) is not None and [x["taskNo"] for x in client.get("/v1/tasks/mine", headers=auth(c)).json()["items"]] == [no]
    edit(no, ended_at=now() - timedelta(hours=49))
    db = SessionLocal()
    try:
        tasks_router.sweep(db)
    finally:
        db.close()
    assert row(no) is None
    assert get(e, no).status_code == 404
    assert client.get("/v1/tasks/mine", headers=auth(c)).json()["items"] == []


def test_task_carries_no_addresses_or_phone_numbers():
    e, _ = new("employer")
    c, cid = new("courier")
    r = client.post(
        "/v1/tasks/share",
        json={"courierId": cid, "ref": "L:z", "title": "Ali Veli 0532 123 45 67", "count": 3, "stops": [{"address": "Barbaros Cad. 11", "phone": "05321234567"}]},
        headers=auth(e),
    )
    assert r.status_code == 200
    t = r.json()
    assert t["title"] == "Adres listesi"  # telefon gibi görünen başlık atılır
    blob = r.text + client.get("/v1/tasks/mine", headers=auth(c)).text
    assert "Barbaros" not in blob and "0532" not in blob and "5321234567" not in blob
    assert not hasattr(row(t["taskNo"]), "stops")


def test_share_limit_per_employer():
    e, _ = new("employer")
    ids = [new("courier")[1] for _ in range(61)]
    codes = [share(e, cid, ref=f"L:{i}").status_code for i, cid in enumerate(ids)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


def test_conflict_message_shows_the_courier_name_title_cased():
    e, _ = new("employer")
    c1, cid1 = new("courier", "burcu yıldırım")
    c2, cid2 = new("courier", "ahmet kaya")
    share(e, cid1, ref="L:ad")
    d = share(e, cid2, ref="L:ad").json()["detail"]
    assert "Burcu Yıldırım kuryesine bağlı" in d
