"""1.27.0 — yerli SMS sağlayıcı bağdaştırıcıları (kullanıcı: "kod olarak yapman gerekeni yap sonra sms olarak yerli bir şirketle
anlaşalım"). Ağa çıkılmaz: urllib.request.urlopen sahtesiyle istek (adres, yetki, gövde) yakalanır, sağlayıcının yanıtı taklit edilir.
Netgsm REST v2 · İletimerkezi v1 JSON · Verimor v2 JSON. Anahtar / numara / metin günlüğe yazılmaz."""
import base64
import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app import notify
from app.main import app

from test_auth_consent import auth, reg

client = TestClient(app)


def member():
    r = reg(role="courier")
    assert r.status_code == 200, r.text
    j = r.json()
    return j["token"], j["user"]
PHONE = "05321234567"
SECRETS = ("gizli-sifre-77", "anahtar-ABC123", "hash-XYZ789")
ALL_ENV = ("SMS_PROVIDER", "SMS_HEADER", "SMS_USER", "SMS_PASS", "SMS_API_KEY", "SMS_API_HASH", "SMS_LINKS")


class Resp:
    def __init__(self, status: int, body: str):
        self.status, self._b = status, body.encode("utf-8")

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    """Sahte ağ: gelen istekleri saklar, sıradaki yanıtı döner (int → HTTPError, Exception → fırlatılır)."""

    def __init__(self, *answers):
        self.reqs, self.answers = [], list(answers)

    def __call__(self, req, timeout=None):
        self.reqs.append(req)
        a = self.answers.pop(0) if self.answers else (200, "{}")
        if isinstance(a, Exception):
            raise a
        status, body = a
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(body.encode("utf-8")))
        return Resp(status, body)

    def body(self, i=-1) -> dict:
        return json.loads(self.reqs[i].data.decode("utf-8"))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ALL_ENV:
        monkeypatch.delenv(k, raising=False)
    notify.set_backend(None)  # GERÇEK arka uç (ortam değişkenlerine bakan)
    notify.reset_budget()
    yield
    notify.set_backend(None)


def use(monkeypatch, provider: str, links=False, **env):
    base = {"SMS_PROVIDER": provider, "SMS_HEADER": "ROTA ASSTN"}
    if provider.lower() in ("netgsm", "verimor"):
        base.update(SMS_USER="8503031234", SMS_PASS=SECRETS[0])
    if provider.lower() == "iletimerkezi":
        base.update(SMS_API_KEY=SECRETS[1], SMS_API_HASH=SECRETS[2])
    if links:
        base["SMS_LINKS"] = "1"
    base.update(env)
    for k, v in base.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def net(monkeypatch, *answers) -> Net:
    n = Net(*answers)
    monkeypatch.setattr(notify.urllib.request, "urlopen", n)
    return n


def no_secret_in(text: str):
    for s in SECRETS + (PHONE, "5321234567"):
        assert s not in text, "günlüğe anahtar / numara yazılmaz"


def test_off_by_default_and_when_a_key_is_missing(monkeypatch):
    n = net(monkeypatch)
    assert notify.sms_provider() == "" and notify.sms_enabled() is False
    assert notify.send_sms(PHONE, "deneme") is False and not n.reqs
    use(monkeypatch, "netgsm", SMS_PASS=None)
    assert notify.sms_enabled() is False, "şifre yoksa kapalı"
    use(monkeypatch, "iletimerkezi", SMS_HEADER=None)
    assert notify.sms_enabled() is False, "onaylı başlık yoksa kapalı"
    use(monkeypatch, "turkcell-falan")
    assert notify.sms_enabled() is False, "tanımadığımız sağlayıcı"
    use(monkeypatch, "Netgsm")
    assert notify.sms_provider() == "netgsm" and notify.sms_enabled() is True


def test_phone_numbers_go_as_ten_digit_mobile_only():
    assert notify.gsm10("05321234567") == "5321234567"
    assert notify.gsm10("+90 (532) 123 45 67") == "5321234567"
    assert notify.gsm10("905321234567") == "5321234567"
    assert notify.gsm10("02423334455") == "" and notify.gsm10("4441234") == "" and notify.gsm10("") == ""


def test_netgsm_request_and_answer_codes(monkeypatch, capsys):
    use(monkeypatch, "netgsm")
    n = net(monkeypatch, (200, '{"code":"00","jobid":"17377215342605050417149344","description":"queued"}'), (200, '{"code":"30","description":"Geçersiz kullanıcı adı"}'))
    assert notify.send_sms(PHONE, "Rota doğrulama kodun: 123456 (10 dk geçerli). Kimseyle paylaşma.") is True
    r = n.reqs[0]
    assert r.full_url == "https://api.netgsm.com.tr/sms/rest/v2/send" and r.get_method() == "POST"
    assert r.get_header("Authorization") == "Basic " + base64.b64encode(f"8503031234:{SECRETS[0]}".encode()).decode()
    b = n.body(0)
    assert b == {"msgheader": "ROTA ASSTN", "encoding": "TR", "iysfilter": "0",
                 "messages": [{"msg": "Rota doğrulama kodun: 123456 (10 dk geçerli). Kimseyle paylaşma.", "no": "5321234567"}]}
    assert notify.send_sms(PHONE, "ikinci") is False, "kod 00 değilse gönderilmedi"
    out = capsys.readouterr().out
    assert "Netgsm" in out and "kod 30" in out
    no_secret_in(out)


def test_iletimerkezi_request_and_answer_codes(monkeypatch, capsys):
    use(monkeypatch, "iletimerkezi")
    n = net(monkeypatch, (200, '{"response":{"status":{"code":"200","message":"İşlem başarılı"},"order":{"id":"1234"}}}'),
            (401, '{"response":{"status":{"code":"401","message":"Üyelik bilgileri hatalı"}}}'))
    assert notify.send_sms("0532 123 45 67", "Kod: 654321") is True
    assert n.reqs[0].full_url == "https://api.iletimerkezi.com/v1/send-sms/json"
    assert n.body(0) == {"request": {
        "authentication": {"key": SECRETS[1], "hash": SECRETS[2]},
        "order": {"sender": "ROTA ASSTN", "iys": "0", "message": {"text": "Kod: 654321", "receipents": {"number": ["5321234567"]}}},
    }}
    assert notify.send_sms(PHONE, "x") is False
    out = capsys.readouterr().out
    assert "İletimerkezi" in out and "HTTP 401" in out and "kod 401" in out
    no_secret_in(out)


def test_verimor_request_and_answer_codes(monkeypatch, capsys):
    use(monkeypatch, "verimor")
    n = net(monkeypatch, (200, "293844"), (400, "INVALID_SOURCE_ADDRESS"), urllib.error.URLError("ağ yok"))
    assert notify.send_sms(PHONE, "Kod: 111222") is True
    assert n.reqs[0].full_url == "https://sms.verimor.com.tr/v2/send.json"
    assert n.body(0) == {"username": "8503031234", "password": SECRETS[0], "source_addr": "ROTA ASSTN", "datacoding": 1,
                         "is_commercial": False, "messages": [{"msg": "Kod: 111222", "dest": "905321234567"}]}
    assert notify.send_sms(PHONE, "x") is False, "400: başlık onaysız"
    assert notify.send_sms(PHONE, "y") is False, "ağ hatası sunucuyu düşürmez"
    out = capsys.readouterr().out
    assert "INVALID_SOURCE_ADDRESS" in out
    no_secret_in(out)


def test_landline_is_not_sent(monkeypatch):
    use(monkeypatch, "netgsm")
    n = net(monkeypatch)
    assert notify.send_sms("02423334455", "x") is False and not n.reqs


def test_forgot_password_code_goes_by_sms_through_the_provider(monkeypatch):
    use(monkeypatch, "netgsm")
    n = net(monkeypatch, (200, '{"code":"00","jobid":"1"}'))
    _, u = member()
    j = client.post("/v1/auth/forgot", json={"login": u["phone"]}).json()
    assert j["channel"] == "sms" and j["delivered"] is True and j["provider"] is True
    msg = n.body(0)["messages"][0]
    assert msg["no"] == u["phone"][1:] and "Rota doğrulama kodun" in msg["msg"]
    code = next(w for w in msg["msg"].replace(".", " ").replace("(", " ").split() if w.isdigit() and len(w) == 6)
    r = client.post("/v1/auth/reset", json={"login": u["phone"], "code": code, "newPassword": "yeniSifre9"})
    assert r.status_code == 200 and r.json()["user"]["phoneVerified"] is True


def test_failed_provider_keeps_the_code_for_the_admin(monkeypatch):
    use(monkeypatch, "verimor")
    net(monkeypatch, (401, "Unauthorized"))
    _, u = member()
    j = client.post("/v1/auth/forgot", json={"login": u["phone"]}).json()
    assert j["channel"] == "sms" and j["delivered"] is False and j["provider"] is True, "gitmediyse kod yönetimde görünür"


def _handoff(tok, ref, **kw):
    b = {"ref": ref, "province": "Antalya", "district": "SMS İlçe", "neighborhood": "Deneme Mah.", "street": "Barbaros Cad.", "door": "12",
         "phone": PHONE, "lat": 36.4950, "lon": 32.0990, "send": True, **kw}
    r = client.post("/v1/handoffs", json=b, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def test_delivery_links_need_their_own_switch(monkeypatch):
    tok, _ = member()
    use(monkeypatch, "netgsm")
    n = net(monkeypatch, (200, '{"code":"00","jobid":"9"}'))
    h = _handoff(tok, "sms-durak-1")
    assert h["providers"]["sms"] is False and h["sent"]["sms"] is False and "link" in h, "SMS_LINKS yokken kurye kendi paylaşır"
    assert not n.reqs, "bağlantı için SMS harcanmadı"
    use(monkeypatch, "netgsm", links=True)
    h2 = _handoff(tok, "sms-durak-2")
    assert h2["providers"]["sms"] is True and h2["sent"]["sms"] is True and "link" not in h2
    assert "Paketimi aldım" in n.body(0)["messages"][0]["msg"]


def test_link_sms_daily_cap(monkeypatch):
    tok, _ = member()
    use(monkeypatch, "netgsm", links=True)
    monkeypatch.setattr(notify, "SMS_LINKS_DAILY_MAX", 1)
    n = net(monkeypatch, (200, '{"code":"00"}'), (200, '{"code":"00"}'))
    assert _handoff(tok, "tavan-1")["sent"]["sms"] is True
    h = _handoff(tok, "tavan-2")
    assert h["sent"]["sms"] is False and "link" in h and len(n.reqs) == 1, "tavan dolunca bağlantı kuryeye döner"


def test_admin_sees_provider_name_not_keys(monkeypatch):
    from helpers import admin_login
    use(monkeypatch, "iletimerkezi")
    hdr, _ = admin_login()
    r = client.get("/v1/admin/codes?hours=24", headers=hdr)
    assert r.status_code == 200
    caps = r.json()["caps"]
    assert caps == {"email": False, "sms": True, "smsProvider": "iletimerkezi", "smsLinks": False}
    no_secret_in(json.dumps(r.json()))
