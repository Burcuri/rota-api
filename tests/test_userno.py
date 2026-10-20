"""Kullanıcı no: her hesaba verilen benzersiz, DEĞİŞMEYEN 7 haneli numara (ilk hane 0 olmaz).
Uygulamada rol önekiyle yazılır (KRY- / ISV- / SPN-); sunucuda yalnız 7 hane tutulur. Sistemin genel kimliği 0000000'dır, gerçek hesaba verilmez."""
import os
import sqlite3
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
import app.userno as un
from app.db import SessionLocal
from app.main import app
from app.migrate import backfill_public_no, ensure_schema
from app.models import User
from app.userno import SYSTEM_NO, is_public_no, new_no, unique_no

from helpers import admin_login
from test_auth_consent import auth, phone, reg, run_py

client = TestClient(app)


def number_of(token) -> str:
    return client.get("/v1/me", headers=auth(token)).json()["userNo"]


def raw_user(public_no=None, **kw) -> str:
    """Kayıt ucunu atlayarak doğrudan veritabanına kullanıcı ekler (eski / elle eklenmiş hesap gibi). Kimliğini döner."""
    uid_ = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(User(id=uid_, phone=phone(), password_hash="pbkdf2$s$d", name=kw.pop("name", "Elle Eklenen"), active_role="courier", public_no=public_no, **kw))
        db.commit()
    return uid_


def db_no(uid_: str):
    with SessionLocal() as db:
        return db.get(User, uid_).public_no


# ------------------------------------------------------------------ numara biçimi
@pytest.mark.parametrize("s", ["1000000", "8472019", "9999999", "1234567"])
def test_valid_numbers(s):
    assert is_public_no(s)


@pytest.mark.parametrize("s", ["0000000", "0123456", "847201", "84720199", "84720a9", "", " 8472019", "8472019 ", "8472019\n", "٨٤٧٢٠١٩", None, 8472019, ["8472019"]])
def test_invalid_numbers(s):
    assert not is_public_no(s)


def test_generated_numbers_are_always_valid_and_never_the_system_id():
    seen = {new_no() for _ in range(600)}
    assert all(is_public_no(n) for n in seen)
    assert SYSTEM_NO not in seen and not is_public_no(SYSTEM_NO)  # 0000000 = Rota Asistan / sistem; gerçek hesaba verilmez
    assert len(seen) > 590  # tahmin edilemez, sıralı değil


def test_unique_no_skips_taken_ones_and_gives_up_when_none_is_free(monkeypatch):
    seq = iter(["1111111", "1111111", "2222222"])
    monkeypatch.setattr(un, "new_no", lambda: next(seq))
    assert unique_no({"1111111"}) == "2222222"
    monkeypatch.setattr(un, "new_no", lambda: "1111111")
    with pytest.raises(RuntimeError):
        unique_no({"1111111"})


# ------------------------------------------------------------------ kayıt / giriş / ben
def test_every_new_account_gets_its_own_valid_number():
    nums = []
    for role in ("courier", "employer", "sponsor") * 4:
        r = reg(role=role)
        assert r.status_code == 200, r.text
        n = r.json()["user"]["userNo"]
        assert is_public_no(n), n
        nums.append(n)
    assert len(set(nums)) == len(nums)


def test_login_and_me_return_the_same_number_and_role_switch_keeps_it():
    r = reg(role="employer", username="no_deneme_1").json()
    tok, n = r["token"], r["user"]["userNo"]
    assert is_public_no(n)
    u = r["user"]
    assert client.post("/v1/auth/login", json={"phone": u["phone"], "password": "sifre123"}).json()["user"]["userNo"] == n
    assert client.post("/v1/auth/login", json={"login": "no_deneme_1", "password": "sifre123"}).json()["user"]["userNo"] == n
    assert number_of(tok) == n
    for role in ("sponsor", "courier", "employer"):  # rol değişse numara aynı kalır; önek uygulamada role göre değişir
        out = client.post("/v1/me/role", json={"role": role}, headers=auth(tok)).json()
        assert out["active_role"] == role and out["userNo"] == n
    assert number_of(tok) == n


def test_number_cannot_be_changed_by_the_user():
    r = reg().json()
    tok, n = r["token"], r["user"]["userNo"]
    other = "1234567" if n != "1234567" else "7654321"
    out = client.post("/v1/me/profile", json={"name": "Yeni Ad", "userNo": other, "public_no": other, "publicNo": other}, headers=auth(tok))
    assert out.status_code == 200, out.text
    assert out.json()["name"] == "Yeni Ad" and out.json()["userNo"] == n
    assert number_of(tok) == n


def test_password_change_keeps_the_number():
    r = reg().json()
    tok, n = r["token"], r["user"]["userNo"]
    ok = client.post("/v1/me/password", json={"oldPassword": "sifre123", "newPassword": "yeni-sifre-1"}, headers=auth(tok))
    assert ok.status_code == 200 and ok.json()["user"]["userNo"] == n


# ------------------------------------------------------------------ eski / elle eklenmiş hesaplar
def test_account_without_number_gets_one_at_me_and_keeps_it():
    r = reg().json()
    with SessionLocal() as db:
        db.get(User, r["user"]["id"]).public_no = None
        db.commit()
    n = number_of(r["token"])
    assert is_public_no(n) and db_no(r["user"]["id"]) == n
    assert number_of(r["token"]) == n  # ikinci çağrıda değişmez


def test_account_without_number_gets_one_at_login():
    r = reg().json()
    with SessionLocal() as db:
        db.get(User, r["user"]["id"]).public_no = None
        db.commit()
    out = client.post("/v1/auth/login", json={"phone": r["user"]["phone"], "password": "sifre123"})
    assert out.status_code == 200, out.text
    n = out.json()["user"]["userNo"]
    assert is_public_no(n) and db_no(r["user"]["id"]) == n
    assert client.post("/v1/auth/login", json={"phone": r["user"]["phone"], "password": "sifre123"}).json()["user"]["userNo"] == n


def test_backfill_fills_only_the_empty_ones_with_unique_numbers_and_is_repeatable():
    kept = reg().json()["user"]
    ids = [raw_user() for _ in range(6)]
    assert all(db_no(i) is None for i in ids)
    n = backfill_public_no()
    assert n >= 6
    got = [db_no(i) for i in ids]
    assert all(is_public_no(x) for x in got) and len(set(got)) == 6
    assert db_no(kept["id"]) == kept["userNo"]  # numarası olan hesaba dokunulmaz
    with SessionLocal() as db:
        every = [x[0] for x in db.query(User.public_no).all()]
    assert None not in every and len(every) == len(set(every))  # tabloda hiç boş ya da çift numara yok
    assert backfill_public_no() == 0  # tekrar çalıştırmak güvenli
    assert [db_no(i) for i in ids] == got  # numaralar değişmedi


def test_ensure_schema_is_idempotent_on_the_current_database():
    assert ensure_schema() == []


# ------------------------------------------------------------------ çakışma
def test_register_retries_with_a_new_number_when_the_number_collides(monkeypatch):
    first = reg().json()["user"]["userNo"]
    free = "1234567" if first != "1234567" else "7654321"
    seq = iter([first, first, free])  # iki kez çakışan numara, sonra boş numara
    monkeypatch.setattr(main_mod, "pick_no", lambda db: next(seq))
    r = reg()
    assert r.status_code == 200, r.text
    assert r.json()["user"]["userNo"] == free


def test_register_gives_up_cleanly_when_the_number_always_collides(monkeypatch):
    first = reg().json()["user"]["userNo"]
    monkeypatch.setattr(main_mod, "pick_no", lambda db: first)
    p = phone()
    r = reg(phone=p)
    assert r.status_code == 503
    monkeypatch.undo()
    with SessionLocal() as db:
        assert db.query(User).filter(User.phone == p).first() is None  # yarım hesap kalmadı
    assert reg(phone=p).status_code == 200  # aynı telefonla sonra kayıt olunabilir


def test_database_refuses_two_accounts_with_the_same_number():
    n = reg().json()["user"]["userNo"]
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        raw_user(public_no=n)


# ------------------------------------------------------------------ yönetim
def test_admin_list_shows_the_number_and_finds_by_number_or_code():
    h, _ = admin_login()
    u = reg(role="sponsor", name="Numara Arama").json()["user"]
    n = u["userNo"]
    for q in (n, f"KRY-{n}", f"isv-{n}", f"SPN-{n}", f"SPN {n}"):
        items = client.get("/v1/admin/users", params={"q": q}, headers=h).json()["items"]
        hit = [x for x in items if x["id"] == u["id"]]
        assert hit and hit[0]["userNo"] == n, q
    absent = next(x for x in (new_no() for _ in range(50)) if db_no_exists(x) is False)
    items = client.get("/v1/admin/users", params={"q": absent}, headers=h).json()["items"]
    assert not [x for x in items if x["userNo"] == absent]
    assert client.get(f"/v1/admin/users/{u['id']}", headers=h).json()["userNo"] == n  # ayrıntıda da var


def db_no_exists(n: str) -> bool:
    with SessionLocal() as db:
        return db.query(User.id).filter(User.public_no == n).first() is not None


def test_admin_list_marks_old_accounts_without_number_as_empty_text():
    h, _ = admin_login()
    uid_ = raw_user(name="Numarasiz Eski Hesap")
    row = next(x for x in client.get("/v1/admin/users", params={"q": "Numarasiz Eski"}, headers=h).json()["items"] if x["id"] == uid_)
    assert row["userNo"] == ""  # liste patlamaz; ilk girişte ya da açılışta numara verilir
    backfill_public_no()
    assert is_public_no(db_no(uid_))


def test_admin_panel_script_prefixes_the_number_by_role():
    js = client.get("/static/yonetim.js").text
    assert "KRY" in js and "ISV" in js and "SPN" in js and "userNo" in js


# ------------------------------------------------------------------ eski veritabanından geçiş
def test_old_database_gets_the_column_and_numbers_at_start_up():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "old.db")
    con = sqlite3.connect(path)
    # Kullanıcı no'dan önceki sunucunun users tablosu (public_no yok)
    con.execute(
        "CREATE TABLE users (id VARCHAR(32) PRIMARY KEY, phone VARCHAR(16) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL,"
        " name VARCHAR(120) NOT NULL, active_role VARCHAR(16) NOT NULL, lat FLOAT, lon FLOAT, created_at DATETIME)"
    )
    for i in (1, 2, 3):
        con.execute("INSERT INTO users (id, phone, password_hash, name, active_role) VALUES (?, ?, 'pbkdf2$s$d', ?, 'courier')", (f"old{i}", f"0532000000{i}", f"Eski {i}"))
    con.commit()
    con.close()
    r = run_py("import app.main", {"DATABASE_URL": f"sqlite:///{path}"})
    assert r.returncode == 0, r.stderr
    assert "users.public_no eklendi" in r.stdout and "3 eski hesaba numara verildi" in r.stdout
    con = sqlite3.connect(path)
    rows = con.execute("SELECT id, public_no, name FROM users ORDER BY id").fetchall()
    assert [x[2] for x in rows] == ["Eski 1", "Eski 2", "Eski 3"]  # eski veri durdu
    nums = [x[1] for x in rows]
    assert all(is_public_no(n) for n in nums) and len(set(nums)) == 3
    idx = {row[1] for row in con.execute("PRAGMA index_list(users)")}
    assert "ix_users_public_no" in idx
    con.close()
    # ikinci açılış hiçbir şey eklemez ve numaralar değişmez
    again = run_py("import app.main", {"DATABASE_URL": f"sqlite:///{path}"})
    assert again.returncode == 0 and "eklendi" not in again.stdout and "numara verildi" not in again.stdout
    con = sqlite3.connect(path)
    assert [x[0] for x in con.execute("SELECT public_no FROM users ORDER BY id")] == nums
    con.close()
