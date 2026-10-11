import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
client = TestClient(app)
_n = [0]


def phone() -> str:
    """Her testte farklı geçerli cep numarası."""
    _n[0] += 1
    return f"05{300000000 + _n[0]:09d}"[:11]


def reg(**kw):
    body = {"phone": phone(), "password": "sifre123", "name": "Test"}
    body.update(kw)
    return client.post("/v1/auth/register", json=body)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- kayıt ----------
def test_old_client_register_still_works():
    r = client.post("/v1/auth/register", json={"phone": phone(), "password": "sifre123", "name": "Eski İstemci"})
    assert r.status_code == 200, r.text
    u = r.json()["user"]
    assert u["active_role"] == "courier" and u["username"] is None and u["email"] is None


@pytest.mark.parametrize("role", ["courier", "employer", "sponsor"])
def test_register_with_each_role(role):
    r = reg(role=role)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["active_role"] == role


def test_register_username_and_email_are_normalised():
    r = reg(username="Spor_Giyim", email="  Reklam@Ornek.COM ", role="sponsor")
    assert r.status_code == 200, r.text
    u = r.json()["user"]
    assert u["username"] == "spor_giyim" and u["email"] == "reklam@ornek.com"


def test_duplicate_username_case_insensitive():
    assert reg(username="murat_kurye").status_code == 200
    r = reg(username="MURAT_KURYE")
    assert r.status_code == 400 and "kullanıcı adı" in r.json()["detail"].lower()


def test_duplicate_phone():
    p = phone()
    assert reg(phone=p).status_code == 200
    assert reg(phone=p).status_code == 400


@pytest.mark.parametrize("bad", ["ab", "12345678", "admin", "Rota", "a b c", "çalış", "x" * 21, "a-b-c"])
def test_bad_usernames_rejected(bad):
    assert reg(username=bad).status_code == 400


@pytest.mark.parametrize(
    "extra",
    [{"role": "admin"}, {"email": "yanlis"}, {"password": "123"}, {"password": "x" * 129}, {"name": "x" * 121}, {"phone": "123"}],
)
def test_other_bad_inputs_rejected(extra):
    assert reg(**extra).status_code == 400


# ---------- giriş ----------
def test_login_by_phone_username_and_login_field():
    p = phone()
    reg(phone=p, username="giris_testi", password="sifre123")
    for body in (
        {"phone": p, "password": "sifre123"},
        {"username": "giris_testi", "password": "sifre123"},
        {"username": "GIRIS_TESTI", "password": "sifre123"},
        {"login": "giris_testi", "password": "sifre123"},
        {"login": p, "password": "sifre123"},
        {"phone": "giris_testi", "password": "sifre123"},  # eski alan adıyla kullanıcı adı yazılırsa da çalışır
    ):
        r = client.post("/v1/auth/login", json=body)
        assert r.status_code == 200, (body, r.text)
        assert r.json()["user"]["username"] == "giris_testi"


def test_login_failures_look_the_same():
    p = phone()
    reg(phone=p, username="kimse_bilmesin")
    wrong_pw = client.post("/v1/auth/login", json={"phone": p, "password": "yanlis-sifre"})
    no_user = client.post("/v1/auth/login", json={"username": "olmayan_kisi", "password": "yanlis-sifre"})
    assert wrong_pw.status_code == 401 and no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()
    assert client.post("/v1/auth/login", json={"password": "x"}).status_code == 400


# ---------- roller ----------
def test_set_role_sponsor_and_invalid():
    t = reg().json()["token"]
    r = client.post("/v1/me/role", json={"role": "sponsor"}, headers=auth(t))
    assert r.status_code == 200 and r.json()["active_role"] == "sponsor"
    assert client.post("/v1/me/role", json={"role": "admin"}, headers=auth(t)).status_code == 400
    assert client.get("/v1/me", headers=auth(t)).json()["active_role"] == "sponsor"


def test_sponsor_cannot_post_jobs_or_apply():
    emp = reg(role="employer").json()["token"]
    job = client.post(
        "/v1/jobs",
        headers=auth(emp),
        json={"lat": 36.5, "lon": 32.0, "packages": [{"customer": "A", "phone": phone(), "address": "x", "lat": 36.5, "lon": 32.0}]},
    )
    assert job.status_code == 200, job.text
    sp = reg(role="sponsor").json()["token"]
    r = client.post("/v1/jobs", headers=auth(sp), json={"lat": 1, "lon": 1, "packages": [{"customer": "A", "phone": phone(), "address": "x", "lat": 1, "lon": 1}]})
    assert r.status_code == 403
    assert client.get("/v1/me", headers=auth(sp)).json()["active_role"] == "sponsor"  # rol sessizce değişmedi
    # Başvur / kabul / yola çık akışı kapalı (ESKI_AKIS yok): herkese 410
    c = reg(role="courier").json()["token"]
    jid = job.json()["id"]
    assert client.post(f"/v1/jobs/{jid}/apply", headers=auth(c)).status_code == 410
    assert client.post(f"/v1/jobs/{jid}/accept", headers=auth(emp)).status_code == 410
    assert client.post(f"/v1/jobs/{jid}/depart", headers=auth(emp)).status_code == 410


# ---------- profil ----------
def test_update_profile_name_username_email():
    r0 = reg(name="Ali Veli")
    tok = r0.json()["token"]
    r = client.post("/v1/me/profile", headers=auth(tok), json={"name": "  Ali Yılmaz ", "username": "Ali.Yilmaz", "email": "ALI@Ornek.com"})
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["name"] == "Ali Yılmaz" and u["username"] == "ali.yilmaz" and u["email"] == "ali@ornek.com"
    assert client.get("/v1/me", headers=auth(tok)).json()["username"] == "ali.yilmaz"
    # yeni kullanıcı adıyla giriş çalışır
    assert client.post("/v1/auth/login", json={"login": "ali.yilmaz", "password": "sifre123"}).status_code == 200


def test_update_profile_partial_and_clear():
    tok = reg(username="silinecek_ad", email="x@y.com").json()["token"]
    # yalnız ad: diğerlerine dokunulmaz
    u = client.post("/v1/me/profile", headers=auth(tok), json={"name": "Yeni Ad"}).json()
    assert u["name"] == "Yeni Ad" and u["username"] == "silinecek_ad" and u["email"] == "x@y.com"
    # boş metin: kaldırır
    u = client.post("/v1/me/profile", headers=auth(tok), json={"username": "", "email": ""}).json()
    assert u["username"] is None and u["email"] is None
    # boşaltılan kullanıcı adı başkası tarafından alınabilir
    assert reg(username="silinecek_ad").status_code == 200


def test_update_profile_rejects_bad_and_taken_values():
    a = reg(username="ilk_sahip").json()["token"]
    b = reg().json()["token"]
    assert client.post("/v1/me/profile", headers=auth(b), json={"username": "ILK_SAHIP"}).status_code == 400  # alınmış
    # kendi kullanıcı adını tekrar yollamak hata değildir
    assert client.post("/v1/me/profile", headers=auth(a), json={"username": "ilk_sahip"}).status_code == 200
    for bad in ({"username": "admin"}, {"username": "ab"}, {"username": "12345678"}, {"email": "yanlis"}, {"name": "   "}, {"name": "x" * 121}):
        assert client.post("/v1/me/profile", headers=auth(b), json=bad).status_code == 400, bad
    assert client.get("/v1/me", headers=auth(b)).json()["username"] is None  # hatalı istekler hiçbir şeyi değiştirmedi


def test_update_profile_cannot_change_phone_and_needs_login():
    r0 = reg()
    tok, old_phone = r0.json()["token"], r0.json()["user"]["phone"]
    u = client.post("/v1/me/profile", headers=auth(tok), json={"phone": "05329990000", "name": "Aynı"}).json()
    assert u["phone"] == old_phone
    assert client.post("/v1/me/profile", json={"name": "x"}).status_code == 401


# ---------- rıza ----------
def test_consent_flow_and_withdrawal():
    t = reg().json()["token"]
    h = auth(t)
    first = client.get("/v1/me/consent", headers=h).json()
    assert first["version"] == 0 and first["voice"] is False and first["recordedAt"] is None

    body = {"version": 1, "voice": True, "ocr": False, "grupKonum": True, "aydinlatmaAt": 1760000000000, "acikRizaAt": 1760000005000}
    r = client.post("/v1/me/consent", json=body, headers=h)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["voice"] is True and got["ocr"] is False and got["grupKonum"] is True and got["recordedAt"]

    # aynı içerik tekrar gelirse (istemci yeniden denedi) yeni satır eklenmez
    client.post("/v1/me/consent", json=body, headers=h)
    assert len(client.get("/v1/me/consent/history", headers=h).json()) == 1

    # geri çekme: yeni satır, hepsi kapalı; geçmiş korunur
    out = {"version": 1, "voice": False, "ocr": False, "grupKonum": False, "aydinlatmaAt": 1760000000000, "acikRizaAt": 1760000999000}
    assert client.post("/v1/me/consent", json=out, headers=h).status_code == 200
    now_state = client.get("/v1/me/consent", headers=h).json()
    assert now_state["voice"] is False and now_state["grupKonum"] is False
    hist = client.get("/v1/me/consent/history", headers=h).json()
    assert len(hist) == 2 and hist[0]["voice"] is False and hist[1]["voice"] is True


def test_consent_accepts_snake_case_and_validates():
    h = auth(reg().json()["token"])
    r = client.post("/v1/me/consent", json={"version": 2, "grup_konum": True}, headers=h)
    assert r.status_code == 200 and r.json()["grupKonum"] is True
    assert client.post("/v1/me/consent", json={"version": -1}, headers=h).status_code == 422
    assert client.post("/v1/me/consent", json={"version": 1, "acikRizaAt": 99999999999999999}, headers=h).status_code == 422


def test_consent_is_per_user_and_needs_login():
    a, b = auth(reg().json()["token"]), auth(reg().json()["token"])
    client.post("/v1/me/consent", json={"version": 1, "voice": True}, headers=a)
    assert client.get("/v1/me/consent", headers=b).json()["version"] == 0
    assert client.get("/v1/me/consent").status_code == 401
    assert client.post("/v1/me/consent", json={"version": 1}).status_code == 401


# ---------- açılış güvenliği ve eski veritabanından geçiş ----------
def run_py(code: str, env_extra: dict):
    env = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "DATABASE_URL")}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_postgres_without_secret_key_refuses_to_start():
    r = run_py("import app.auth", {"DATABASE_URL": "postgresql://u:p@localhost/db"})
    assert r.returncode != 0 and "SECRET_KEY" in r.stderr
    ok = run_py("import app.auth", {"DATABASE_URL": "postgresql://u:p@localhost/db", "SECRET_KEY": "x" * 40})
    assert ok.returncode == 0, ok.stderr
    short = run_py("import app.auth", {"SECRET_KEY": "kisa"})
    assert short.returncode != 0 and "kısa" in short.stderr


def test_old_database_is_upgraded_in_place():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "old.db")
    con = sqlite3.connect(path)
    # Dilim 1 öncesi sunucunun oluşturduğu eski users tablosu (username / email yok)
    con.execute(
        "CREATE TABLE users (id VARCHAR(32) PRIMARY KEY, phone VARCHAR(16) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL,"
        " name VARCHAR(120) NOT NULL, active_role VARCHAR(16) NOT NULL, lat FLOAT, lon FLOAT, created_at DATETIME)"
    )
    con.execute("INSERT INTO users (id, phone, password_hash, name, active_role) VALUES ('old1', '05320000001', 'pbkdf2$s$d', 'Eski Kullanıcı', 'courier')")
    con.commit()
    con.close()
    r = run_py("import app.main", {"DATABASE_URL": f"sqlite:///{path}"})
    assert r.returncode == 0, r.stderr
    assert "users.username eklendi" in r.stdout and "users.email eklendi" in r.stdout
    con = sqlite3.connect(path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(users)")}
    assert {"username", "email"} <= cols
    assert con.execute("SELECT name FROM users WHERE id='old1'").fetchone()[0] == "Eski Kullanıcı"  # eski veri durdu
    idx = {row[1] for row in con.execute("PRAGMA index_list(users)")}
    assert "ix_users_username" in idx
    assert con.execute("SELECT name FROM sqlite_master WHERE name='consent_log'").fetchone()
    con.close()
    # ikinci açılış hiçbir şey eklemez (tekrar çalıştırmak güvenli)
    again = run_py("import app.main", {"DATABASE_URL": f"sqlite:///{path}"})
    assert again.returncode == 0 and "eklendi" not in again.stdout


def test_migration_survives_a_start_up_race(monkeypatch, tmp_path):
    """İki süreç aynı anda açılırsa ikincisi 'sütun zaten var' hatasıyla çökmemeli."""
    import app.migrate as mg
    from sqlalchemy import create_engine, text

    eng = create_engine(f"sqlite:///{tmp_path / 'race.db'}")
    with eng.begin() as c:
        # diğer süreç users sütunlarının hepsini çoktan eklemiş (yeni sütun eklenince bu test kendiliğinden güncel kalır)
        cols = ", ".join(f"{col} {ddl}" for t, col, ddl in mg.ADD_COLUMNS if t == "users")
        c.execute(text(f"CREATE TABLE users (id VARCHAR(32) PRIMARY KEY, {cols})"))
    monkeypatch.setattr(mg, "engine", eng)
    real_inspect = mg.inspect
    calls = {"n": 0}

    class Stale:  # ilk bakışta "sütunlar yok" der: diğer süreç henüz eklememişti
        def has_table(self, t):
            return True

        def get_columns(self, t):
            return [{"name": "id"}]

    def lying_inspect(e):
        calls["n"] += 1
        return Stale() if calls["n"] == 1 else real_inspect(e)

    monkeypatch.setattr(mg, "inspect", lying_inspect)
    assert mg.ensure_schema() == []  # ALTER 'zaten var' diye patlar, sütun mevcut olduğundan sessizce geçilir
