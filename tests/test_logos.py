"""İşletme logosu (1.14.1; kullanıcı kararı 22 Ekim 2026): profil fotoğrafı yok (avatar var); yalnız işletme logo yükler; logo dışı resmi
yönetim siler. Yalnız JPEG / PNG / WEBP (gerçek baytlara bakılır), boyut sınırı, askıdaki hesabın logosu verilmez."""
import base64

from fastapi.testclient import TestClient

from app.main import app
from app.models import AdminAudit
from app.db import SessionLocal

from helpers import admin_login
from test_auth_consent import auth, reg

client = TestClient(app)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def data_url(raw: bytes, mime="image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def put(tok, logo):
    return client.post("/v1/me/logo", json={"logo": logo}, headers=auth(tok))


def emp():
    j = reg(role="employer", name="Mavi Pide").json()
    return j["token"], j["user"]["id"]


def test_employer_uploads_logo_and_couriers_see_it_on_the_job_card():
    tok, uid_ = emp()
    r = put(tok, data_url(PNG))
    assert r.status_code == 200, r.text
    v = r.json()["logo"]
    assert v
    assert client.get("/v1/me", headers=auth(tok)).json()["logo"] == v
    g = client.get(f"/v1/logo/{uid_}")  # oturumsuz <img>
    assert g.status_code == 200 and g.content == PNG and g.headers["content-type"] == "image/png"
    assert "nosniff" in g.headers["x-content-type-options"]
    j = client.post("/v1/jobs", json={"title": "Pide", "lat": 36.5, "lon": 32.0, "packageCount": 3}, headers=auth(tok)).json()
    courier = reg().json()["token"]
    seen = client.get(f"/v1/jobs/{j['id']}", headers=auth(courier)).json()
    assert seen["employer"] == {"id": uid_, "name": "Mavi Pide", "logo": v}


def test_jpeg_and_webp_are_accepted_plain_base64_too():
    tok, uid_ = emp()
    assert put(tok, data_url(JPG, "image/jpeg")).status_code == 200
    assert client.get(f"/v1/logo/{uid_}").headers["content-type"] == "image/jpeg"
    assert put(tok, base64.b64encode(WEBP).decode()).status_code == 200
    assert client.get(f"/v1/logo/{uid_}").headers["content-type"] == "image/webp"


def test_people_cannot_upload_pictures_only_businesses():
    courier = reg().json()["token"]
    r = put(courier, data_url(PNG))
    assert r.status_code == 403 and "avatar" in r.json()["detail"]


def test_only_real_images_svg_and_fakes_are_refused():
    tok, _ = emp()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert put(tok, data_url(svg, "image/png")).status_code == 415  # tür bildirimi değil, baytlar sayılır
    assert put(tok, data_url(b"GIF89a" + b"\x00" * 20, "image/png")).status_code == 415
    assert put(tok, "data:image/png;base64,@@@").status_code == 400
    big = PNG + b"\x00" * (151 * 1024)
    assert put(tok, data_url(big)).status_code in (413, 422)


def test_logo_can_be_removed_by_owner():
    tok, uid_ = emp()
    put(tok, data_url(PNG))
    r = put(tok, "")
    assert r.status_code == 200 and r.json()["logo"] == ""
    assert client.get(f"/v1/logo/{uid_}").status_code == 404
    assert client.get("/v1/me", headers=auth(tok)).json()["logo"] == ""


def test_admin_lists_and_deletes_a_non_logo_picture():
    tok, uid_ = emp()
    put(tok, data_url(PNG))
    h, name = admin_login()
    items = client.get("/v1/admin/logos", headers=h).json()["items"]
    row = next(x for x in items if x["id"] == uid_)
    assert row["name"] == "Mavi Pide" and row["role"] == "employer" and row["v"]
    assert client.get("/v1/admin/logos").status_code == 401
    d = client.delete(f"/v1/admin/users/{uid_}/logo", headers=h)
    assert d.status_code == 200
    assert client.get(f"/v1/logo/{uid_}").status_code == 404
    assert client.get("/v1/me", headers=auth(tok)).json()["logo"] == ""  # uygulama bunu görüp telefondaki logoyu kaldırır
    assert all(x["id"] != uid_ for x in client.get("/v1/admin/logos", headers=h).json()["items"])
    db = SessionLocal()
    try:
        a = db.query(AdminAudit).filter(AdminAudit.action == "logo_delete", AdminAudit.target_id == uid_).one()
        assert a.admin_name == name
    finally:
        db.close()
    assert client.delete(f"/v1/admin/users/{uid_}/logo", headers=h).status_code == 200  # ikinci kez: sorun yok


def test_banned_business_logo_is_not_served():
    tok, uid_ = emp()
    put(tok, data_url(PNG))
    h, _ = admin_login()
    assert client.post(f"/v1/admin/users/{uid_}/ban", json={"reason": "deneme"}, headers=h).status_code == 200
    assert client.get(f"/v1/logo/{uid_}").status_code == 404
    assert client.get("/v1/logo/yok-boyle-biri").status_code == 404
