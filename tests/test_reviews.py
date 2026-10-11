"""Değerlendirmeler (anketler): alıcı (bağlantı kodu ile), işveren → kurye, kurye → işveren. Hepsi 5 üzerinden.
Güven kuralları, ağırlık, gizlilik, yönetim görünümü ve gizleme."""
import itertools
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import notify
from app.db import SessionLocal
from app.main import app, handoff_router
from app.models import Handoff, Review, now

from helpers import admin_login
from test_auth_consent import auth, reg
from test_handoff import FakeBackend, PHONE, body, create, courier, row, token_of

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    for lim in handoff_router.limiters:
        lim.reset()
    notify.set_backend(None)
    yield
    notify.set_backend(None)
    for lim in handoff_router.limiters:
        lim.reset()


def arrive(tok, h):
    r = client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(tok))
    assert r.status_code == 200
    return token_of(h)


def post_review(t, **kw):
    d = {"stars": 5}
    d.update(kw)
    return client.post(f"/a/{t}/review", json=d)


def new(role):
    r = reg(role=role).json()
    return r["token"], r["user"]["id"]


# ------------------------------------------------------------------ alıcı anketi
def test_recipient_cannot_review_before_the_courier_arrives():
    tok, _ = courier()
    h = create(tok)
    t = token_of(h)
    r = post_review(t)
    assert r.status_code == 409
    st = client.get(f"/a/{t}/state").json()
    assert st["review"] == {"allowed": False, "done": False, "data": None}


def test_recipient_review_with_standard_questions_and_comment():
    tok, cid = courier()
    h = create(tok, send=True)
    t = arrive(tok, h)
    st = client.get(f"/a/{t}/state").json()
    assert st["review"]["allowed"] is True and st["review"]["done"] is False
    r = post_review(t, stars=4, appStars=5, tags=["hizli", "nazik", "uydurma"], addressOk=True, comment="Çok hızlı geldi, teşekkürler")
    assert r.status_code == 200 and r.json()["review"]["done"] is True
    with SessionLocal() as db:
        x = db.query(Review).filter(Review.handoff_id == h["id"]).one()
        assert (x.source, x.subject_id, x.subject_role, x.author_id) == ("recipient", cid, "courier", None)
        assert (x.stars, x.app_stars, x.comment) == (4, 5, "Çok hızlı geldi, teşekkürler")
        ans = json.loads(x.answers)
        assert ans == {"tags": ["hizli", "nazik"], "addressOk": True}  # bilinmeyen seçim atılır
    # sayfa içeriği geri verir (düzeltebilsin)
    assert client.get(f"/a/{t}/state").json()["review"]["data"] == {"stars": 4, "appStars": 5, "tags": ["hizli", "nazik"], "addressOk": True, "comment": "Çok hızlı geldi, teşekkürler"}


def test_resubmitting_updates_the_single_review():
    tok, _ = courier()
    h = create(tok)
    t = arrive(tok, h)
    post_review(t, stars=2, comment="Geç kaldı")
    post_review(t, stars=5, comment="Aslında sorun yokmuş", tags=["sorunsuz"])
    with SessionLocal() as db:
        rows = db.query(Review).filter(Review.handoff_id == h["id"]).all()
        assert len(rows) == 1 and rows[0].stars == 5 and rows[0].comment == "Aslında sorun yokmuş"


@pytest.mark.parametrize("bad", [{"stars": 0}, {"stars": 6}, {"stars": 3, "appStars": 0}, {"stars": 3, "appStars": 9}])
def test_stars_must_be_between_1_and_5(bad):
    tok, _ = courier()
    t = arrive(tok, create(tok))
    assert post_review(t, **bad).status_code == 400
    with SessionLocal() as db:
        assert db.query(Review).filter(Review.stars.notin_(range(1, 6))).count() == 0  # geçersiz puan hiçbir zaman yazılmaz


def test_comment_filter_rejects_profanity_and_personal_data_and_long_text():
    tok, _ = courier()
    t = arrive(tok, create(tok))
    r = post_review(t, comment="amk kurye")
    assert r.status_code == 400 and "uygunsuz" in r.json()["detail"].lower()
    r = post_review(t, comment="Beni arayın 0532 111 22 33")
    assert r.status_code == 400 and "Telefon" in r.json()["detail"]
    assert post_review(t, comment="x" * 601).status_code == 422
    assert post_review(t, comment="x" * 400).status_code == 200


def test_recipient_review_weight_follows_the_trust_of_the_link():
    # bağlantı alıcıya sunucudan ulaştı, kurye görmedi → tam ağırlık
    notify.set_backend(FakeBackend())
    tok, _ = courier()
    h1 = create(tok, send=True)
    post_review(arrive(tok, h1), stars=5)
    notify.set_backend(None)
    # test modu: kurye bağlantıyı gördü → düşük ağırlık
    h2 = create(tok, send=True)
    post_review(arrive(tok, h2), stars=5)
    with SessionLocal() as db:
        assert db.query(Review).filter(Review.handoff_id == h1["id"]).one().weight == 1.0
        assert db.query(Review).filter(Review.handoff_id == h2["id"]).one().weight == 0.3


def test_review_needs_a_real_link_token_not_a_phone_number():
    for t in ("kisa", "05321112233", "x" * 40, "AAAAAAAAAAAAAAAA"):
        assert post_review(t).status_code == 404
    # iki ayrı teslimin yorumları karışmaz
    tok, _ = courier()
    a, b = create(tok), create(tok)
    ta, tb = arrive(tok, a), arrive(tok, b)
    post_review(ta, stars=1, comment="A")
    post_review(tb, stars=5, comment="B")
    with SessionLocal() as db:
        assert db.query(Review).filter(Review.handoff_id == a["id"]).one().comment == "A"
        assert db.query(Review).filter(Review.handoff_id == b["id"]).one().comment == "B"


def test_review_write_limits_apply():
    tok, _ = courier()
    t = arrive(tok, create(tok))
    codes = [post_review(t, stars=3).status_code for _ in range(70)]
    assert 429 in codes


def test_review_page_has_no_personal_data_in_the_review_row():
    tok, _ = courier()
    h = create(tok, send=True, email="alici@example.com")
    t = arrive(tok, h)
    post_review(t, stars=5, comment="Güzel")
    with SessionLocal() as db:
        x = db.query(Review).filter(Review.handoff_id == h["id"]).one()
        blob = json.dumps([x.answers, x.comment, x.ref, x.source])
        assert PHONE not in blob and "alici@example.com" not in blob


# ------------------------------------------------------------------ teslim bitince kayıt kapanır, alıcı yorum yazabilir
def test_courier_delete_after_arrival_closes_the_record_and_wipes_contact_but_review_still_works():
    tok, _ = courier()
    h = create(tok, ref="kapat-1", send=True, email="alici@example.com")
    t = arrive(tok, h)
    client.post(f"/a/{t}/received", json={"checked": True})
    assert client.delete(f"/v1/handoffs/{h['id']}", headers=auth(tok)).json() == {"ok": True}
    x = row(h["id"])
    assert x is not None and x.closed_at is not None and x.phone == "" and x.email == ""
    # süre en çok 24 saate iner (48 saatlik üst sınır korunur)
    from app.handoff import utc

    assert utc(x.expires_at) <= now() + timedelta(hours=24, minutes=1)
    # kurye için görünmez, aynı durak yeni kayıt açar
    assert client.get("/v1/handoffs?refs=kapat-1", headers=auth(tok)).json()["items"] == []
    assert client.post(f"/v1/handoffs/{h['id']}/arrived", headers=auth(tok)).status_code == 404
    again = create(tok, ref="kapat-1")
    assert again["id"] != h["id"]
    # alıcı sayfası yaşıyor; yorum yazılabilir; telefon / e-posta geri yazılamaz
    st = client.get(f"/a/{t}/state").json()
    assert st["closed"] is True and st["received"] is True and st["review"]["allowed"] is True and st["phone"] == ""
    assert post_review(t, stars=5, comment="Teşekkürler").status_code == 200
    assert client.post(f"/a/{t}/contact", json={"phone": "05329998877"}).json()["phone"] == ""
    assert row(h["id"]).phone == ""


def test_courier_delete_before_arrival_removes_everything():
    tok, _ = courier()
    h = create(tok, ref="iptal-1")
    client.delete(f"/v1/handoffs/{h['id']}", headers=auth(tok))
    assert row(h["id"]) is None
    assert post_review("A" * 16).status_code == 404


def test_closed_record_disappears_after_its_short_window():
    tok, _ = courier()
    h = create(tok)
    t = arrive(tok, h)
    client.delete(f"/v1/handoffs/{h['id']}", headers=auth(tok))
    with SessionLocal() as db:
        x = db.get(Handoff, h["id"])
        x.expires_at = now() - timedelta(minutes=1)
        db.commit()
    assert client.get(f"/a/{t}/state").status_code == 404
    assert post_review(t).status_code == 404


# ------------------------------------------------------------------ işveren ↔ kurye
def test_employer_rates_courier_and_courier_rates_employer():
    etok, eid = new("employer")
    ctok, cid = new("courier")
    r = client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "ref": "ilan-1", "stars": 4, "tags": ["hizli", "gec", "yok"], "comment": "İyiydi"}, headers=auth(etok))
    assert r.status_code == 200
    r = client.post("/v1/reviews", json={"kind": "employer", "subjectId": eid, "ref": "ilan-1", "stars": 3, "onTime": False, "complete": True, "comment": "Geç ödedi"}, headers=auth(ctok))
    assert r.status_code == 200
    with SessionLocal() as db:
        a = db.query(Review).filter(Review.author_id == eid).one()
        b = db.query(Review).filter(Review.author_id == cid).one()
        assert (a.source, a.subject_role, a.stars, json.loads(a.answers)) == ("employer", "courier", 4, {"tags": ["hizli", "gec"]})
        assert (b.source, b.subject_role, b.stars, json.loads(b.answers)) == ("courier", "employer", 3, {"onTime": False, "complete": True})


def test_role_and_target_checks():
    etok, eid = new("employer")
    ctok, cid = new("courier")
    stok, sid = new("sponsor")
    # kuryeyi yalnız işveren, işvereni yalnız kurye puanlar
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 5}, headers=auth(ctok)).status_code == 403
    assert client.post("/v1/reviews", json={"kind": "employer", "subjectId": eid, "stars": 5}, headers=auth(etok)).status_code == 403
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 5}, headers=auth(stok)).status_code == 403
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 6}, headers=auth(etok)).status_code == 400
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": eid, "stars": 5}, headers=auth(etok)).status_code == 400  # kendine
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": "yok" * 5, "stars": 5}, headers=auth(etok)).status_code == 404
    assert client.post("/v1/reviews", json={"kind": "dost", "subjectId": cid, "stars": 5}, headers=auth(etok)).status_code == 400
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 5}).status_code == 401
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 3, "comment": "siktir"}, headers=auth(etok)).status_code == 400


def test_same_job_updates_and_without_job_only_once_a_day():
    etok, eid = new("employer")
    _, cid = new("courier")
    for stars in (2, 5):
        assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "ref": "ilan-7", "stars": stars}, headers=auth(etok)).status_code == 200
    with SessionLocal() as db:
        rows = db.query(Review).filter(Review.author_id == eid, Review.subject_id == cid).all()
        assert len(rows) == 1 and rows[0].stars == 5
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 3}, headers=auth(etok)).status_code == 200
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 1}, headers=auth(etok)).status_code == 429


def test_daily_cap_per_author():
    etok, eid = new("employer")
    for i in range(30):
        _, cid = new("courier")
        assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 3}, headers=auth(etok)).status_code == 200
    _, cid = new("courier")
    assert client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 3}, headers=auth(etok)).status_code == 429


def test_new_accounts_count_half():
    etok, eid = new("employer")
    _, cid = new("courier")
    client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 5, "ref": "a"}, headers=auth(etok))
    with SessionLocal() as db:
        assert db.query(Review).filter(Review.author_id == eid).one().weight == 0.5
        from app.models import User

        u = db.get(User, eid)
        u.created_at = now() - timedelta(days=3)
        db.commit()
    client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 5, "ref": "b"}, headers=auth(etok))
    with SessionLocal() as db:
        assert db.query(Review).filter(Review.author_id == eid, Review.ref == "b").one().weight == 1.0


# ------------------------------------------------------------------ özetler (5 üzerinden), kim ne görür
def test_courier_score_is_the_mean_of_employer_and_recipient_averages_out_of_five():
    ctok, cid = new("courier")
    # işveren ortalaması 4, alıcı ortalaması 5 → genel 4,5
    for stars in (3, 5):
        etok, _ = new("employer")
        client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": stars}, headers=auth(etok))
    with SessionLocal() as db:  # alıcı değerlendirmeleri (bağlantı sahibi bu kurye)
        for stars in (5, 5):
            db.add(Review(source="recipient", subject_id=cid, subject_role="courier", stars=stars, weight=1.0))
        db.commit()
    s = client.get("/v1/me/rating", headers=auth(ctok)).json()["courier"]
    assert s["employerAvg"] == 4.0 and s["recipientAvg"] == 5.0 and s["score"] == 4.5 and s["count"] == 4
    assert len(s["last"]) == 4 and all(1 <= x <= 5 for x in s["last"])
    assert "comment" not in json.dumps(s)  # kurye yorum metnini görmez


def test_only_one_source_uses_that_source_and_no_data_gives_nulls():
    ctok, cid = new("courier")
    s = client.get("/v1/me/rating", headers=auth(ctok)).json()["courier"]
    assert s == {"score": None, "employerAvg": None, "recipientAvg": None, "count": 0, "last": []}
    with SessionLocal() as db:
        db.add(Review(source="recipient", subject_id=cid, subject_role="courier", stars=4, weight=1.0))
        db.commit()
    s = client.get("/v1/me/rating", headers=auth(ctok)).json()["courier"]
    assert s["score"] == 4.0 and s["employerAvg"] is None


def test_untrusted_recipient_reviews_weigh_less():
    ctok, cid = new("courier")
    with SessionLocal() as db:
        db.add(Review(source="recipient", subject_id=cid, subject_role="courier", stars=5, weight=1.0))
        db.add(Review(source="recipient", subject_id=cid, subject_role="courier", stars=1, weight=0.3))
        db.commit()
    s = client.get("/v1/me/rating", headers=auth(ctok)).json()["courier"]
    assert s["recipientAvg"] == round((5 * 1.0 + 1 * 0.3) / 1.3, 1)  # 4,2: kuryenin kendi gördüğü bağlantıdan gelen düşük puan az etki eder


def test_employer_reputation_is_for_couriers_only_and_never_shows_comment_text():
    etok, eid = new("employer")
    ctok, cid = new("courier")
    ctok2, _ = new("courier")
    client.post("/v1/reviews", json={"kind": "employer", "subjectId": eid, "ref": "x", "stars": 5, "onTime": True, "complete": True, "comment": "Hemen ödedi"}, headers=auth(ctok))
    client.post("/v1/reviews", json={"kind": "employer", "subjectId": eid, "ref": "y", "stars": 2, "onTime": False, "complete": True, "comment": "Geç yattı"}, headers=auth(ctok2))
    rep = client.get(f"/v1/employers/{eid}/reputation", headers=auth(ctok)).json()
    assert rep["count"] == 2 and rep["avg"] == 3.5 and rep["onTimePct"] == 50 and rep["completePct"] == 100
    assert {x["stars"] for x in rep["reviews"]} == {5, 2} and len(rep["reviews"]) == 2  # yıldız + ödeme bilgisi görünür
    assert all(set(x) == {"id", "stars", "onTime", "complete", "at"} for x in rep["reviews"])  # yorum alanı hiç yok
    assert "Hemen ödedi" not in json.dumps(rep, ensure_ascii=False) and "Geç yattı" not in json.dumps(rep, ensure_ascii=False)  # yorum metnini yalnız yönetim okur
    assert cid not in json.dumps(rep)  # kimin yazdığı görünmez
    many = client.get(f"/v1/employers/reputation?ids={eid},bilinmeyen", headers=auth(ctok)).json()["items"]
    assert many[eid]["avg"] == 3.5 and many["bilinmeyen"] == {"avg": None, "onTimePct": None, "completePct": None, "count": 0}
    assert "reviews" not in many[eid]
    # işveren / sponsor / girişsiz göremez
    assert client.get(f"/v1/employers/{eid}/reputation", headers=auth(etok)).status_code == 403
    assert client.get(f"/v1/employers/reputation?ids={eid}", headers=auth(etok)).status_code == 403
    assert client.get(f"/v1/employers/{eid}/reputation").status_code == 401
    # işveren kendi itibarını sayı olarak görür (yorum metni olmadan)
    mine = client.get("/v1/me/rating", headers=auth(etok)).json()["employer"]
    assert mine["avg"] == 3.5 and "Hemen" not in json.dumps(mine)


# ------------------------------------------------------------------ yönetim: okuma, süzme, gizleme
def test_admin_reads_all_three_kinds_with_comments_and_can_filter():
    h, _ = admin_login()
    etok, eid = new("employer")
    ctok, cid = new("courier")
    tok, c2 = courier()
    t = arrive(tok, create(tok))
    post_review(t, stars=2, appStars=4, tags=["gec"], addressOk=False, comment="Alıcı yorumu BENZERSIZ1")
    client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 5, "comment": "İşveren yorumu BENZERSIZ2", "tags": ["nazik"]}, headers=auth(etok))
    client.post("/v1/reviews", json={"kind": "employer", "subjectId": eid, "stars": 1, "onTime": False, "complete": False, "comment": "Kurye yorumu BENZERSIZ3"}, headers=auth(ctok))
    items = client.get("/v1/admin/reviews?q=BENZERSIZ", headers=h).json()["items"]
    by = {x["source"]: x for x in items}
    assert set(by) == {"recipient", "employer", "courier"}
    assert by["recipient"]["comment"].endswith("BENZERSIZ1") and by["recipient"]["author"] is None and by["recipient"]["appStars"] == 4
    assert by["recipient"]["tags"] == ["Geç kaldı"] and by["recipient"]["addressOk"] is False and by["recipient"]["subject"]["id"] == c2
    assert by["employer"]["author"]["id"] == eid and by["employer"]["subject"]["id"] == cid and by["employer"]["tags"] == ["Nazikti"]
    assert by["courier"]["onTime"] is False and by["courier"]["complete"] is False
    assert [x["source"] for x in client.get("/v1/admin/reviews?q=BENZERSIZ&source=employer", headers=h).json()["items"]] == ["employer"]
    assert len(client.get("/v1/admin/reviews?q=BENZERSIZ&stars=1", headers=h).json()["items"]) == 1
    assert len(client.get(f"/v1/admin/reviews?subject={cid}", headers=h).json()["items"]) == 1
    assert len(client.get(f"/v1/admin/reviews?author={eid}", headers=h).json()["items"]) == 1


def test_hiding_removes_a_review_from_averages_and_listings_but_not_from_admin():
    h, name = admin_login()
    etok, _ = new("employer")
    ctok, cid = new("courier")
    client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 1, "comment": "Haksız puan"}, headers=auth(etok))
    rid = client.get(f"/v1/admin/reviews?subject={cid}", headers=h).json()["items"][0]["id"]
    assert client.get("/v1/me/rating", headers=auth(ctok)).json()["courier"]["score"] == 1.0
    r = client.post(f"/v1/admin/reviews/{rid}/hide", json={"hidden": True, "note": "Haksız"}, headers=h)
    assert r.status_code == 200 and r.json()["hidden"] is True and r.json()["hiddenNote"] == "Haksız"
    assert client.get("/v1/me/rating", headers=auth(ctok)).json()["courier"]["score"] is None
    assert client.get(f"/v1/admin/reviews?subject={cid}&hidden=1", headers=h).json()["items"][0]["id"] == rid
    assert client.get(f"/v1/admin/reviews?subject={cid}&hidden=0", headers=h).json()["items"] == []
    client.post(f"/v1/admin/reviews/{rid}/hide", json={"hidden": False}, headers=h)
    assert client.get("/v1/me/rating", headers=auth(ctok)).json()["courier"]["score"] == 1.0
    assert client.delete(f"/v1/admin/reviews/{rid}", headers=h).json() == {"ok": True}
    assert client.get("/v1/me/rating", headers=auth(ctok)).json()["courier"]["score"] is None
    acts = [x["action"] for x in client.get("/v1/admin/audit", headers=h).json()["items"] if x["admin"] == name]
    assert {"review_hide", "review_unhide", "review_delete"} <= set(acts)
    assert client.post("/v1/admin/reviews/yok/hide", json={}, headers=h).status_code == 404


def test_hidden_employer_comment_is_gone_from_courier_view():
    h, _ = admin_login()
    etok, eid = new("employer")
    ctok, _ = new("courier")
    client.post("/v1/reviews", json={"kind": "employer", "subjectId": eid, "stars": 1, "comment": "Hakaret içerir gibi"}, headers=auth(ctok))
    rid = client.get(f"/v1/admin/reviews?subject={eid}", headers=h).json()["items"][0]["id"]
    assert client.get(f"/v1/employers/{eid}/reputation", headers=auth(ctok)).json()["count"] == 1
    client.post(f"/v1/admin/reviews/{rid}/hide", json={"hidden": True}, headers=h)
    rep = client.get(f"/v1/employers/{eid}/reputation", headers=auth(ctok)).json()
    assert rep["count"] == 0 and rep["reviews"] == []


def test_review_events_are_in_the_activity_feed():
    h, _ = admin_login()
    etok, eid = new("employer")
    _, cid = new("courier")
    client.post("/v1/reviews", json={"kind": "courier", "subjectId": cid, "stars": 4}, headers=auth(etok))
    ev = client.get(f"/v1/admin/events?user_id={eid}&kind=review.create", headers=h).json()["items"]
    assert ev and ev[0]["meta"]["src"] == "employer" and ev[0]["meta"]["stars"] == 4
