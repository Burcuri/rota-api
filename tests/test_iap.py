"""1.25.0 — Rota coin MAĞAZA satın alması (kullanıcı kararı 6 Kasım 2026: "normal bir Google Play ya da App Store satışı nasıl yapılıyorsa").
Mağazaların sunucusu yerine sahte taşıyıcı: Google Play Developer API (hizmet hesabı JWT → erişim belirteci → satın alma → tüket) ve
App Store Server API (ES256 JWT, canlıda yoksa deneme ortamı). Denenenler: anahtar yoksa kapalı, coin BİR KEZ yazılır, başka hesaba
yazılmaz, bekleyen / iptal / iade / başka hesabın satın alması reddedilir, kurye satın alamaz, hesap işaretleri sabit ve kişisel bilgisiz."""
import base64
import itertools
import json
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from app import iap
from app.main import app

from test_auth_consent import auth, reg

client = TestClient(app)
_seq = itertools.count(1)

RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
RSA_PEM = RSA_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
EC_KEY = ec.generate_private_key(ec.SECP256R1())
EC_PEM = EC_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
SA = {"type": "service_account", "client_email": "rota-iap@proje.iam.gserviceaccount.com", "private_key": RSA_PEM, "token_uri": "https://oauth2.googleapis.com/token"}


def user(role="employer"):
    r = reg(role=role, name=f"Mağaza {role} {next(_seq)}").json()
    return r["token"], r["user"]["id"]


class Store:
    """Google ve Apple sunucularının yerine: gelen istekleri kaydeder, ayarlanan cevapları döner."""

    def __init__(self):
        self.calls = []
        self.google = {}  # jeton → satın alma cevabı
        self.apple = {"prod": {}, "sandbox": {}}  # işlem no → imzalı işlem içeriği
        self.down = False

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if self.down:
            return 0, {}
        if url == SA["token_uri"]:
            form = dict(x.split("=", 1) for x in body.decode().split("&"))
            claims = jwt.decode(form["assertion"], RSA_KEY.public_key(), algorithms=["RS256"], audience=SA["token_uri"])
            assert claims["iss"] == SA["client_email"] and "androidpublisher" in claims["scope"]
            return 200, {"access_token": "erisim-belirteci", "expires_in": 3600}
        if "androidpublisher" in url:
            assert headers["Authorization"] == "Bearer erisim-belirteci"
            if url.endswith(":consume"):
                return 204, {}
            tok = url.rsplit("/tokens/", 1)[1]
            g = self.google.get(tok)
            return (200, g) if g is not None else (404, {"error": {"code": 404}})
        if "storekit" in url:
            claims = jwt.decode(headers["Authorization"][7:], EC_KEY.public_key(), algorithms=["ES256"], audience="appstoreconnect-v1")
            assert claims["iss"] == "issuer-123" and claims["bid"] == "com.rota.asistan"
            assert jwt.get_unverified_header(headers["Authorization"][7:])["kid"] == "KEY123"
            env = "sandbox" if "sandbox" in url else "prod"
            tx = self.apple[env].get(url.rsplit("/", 1)[1])
            if tx is None:
                return 404, {"errorCode": 4040010}
            jws = "eyJhbGciOiJFUzI1NiJ9." + base64.urlsafe_b64encode(json.dumps(tx).encode()).decode().rstrip("=") + ".imza"
            return 200, {"signedTransactionInfo": jws}
        return 404, {}

    def n(self, part):
        return len([c for c in self.calls if part in c[1]])


@pytest.fixture()
def store(monkeypatch):
    s = Store()
    monkeypatch.setattr(iap, "TRANSPORT", [s])
    iap._google_tok.update(v="", exp=0.0, who="")
    monkeypatch.delenv("GOOGLE_PLAY_SERVICE_ACCOUNT", raising=False)
    for k in ("APPLE_IAP_ISSUER_ID", "APPLE_IAP_KEY_ID", "APPLE_IAP_PRIVATE_KEY", "IAP_PRODUCTS"):
        monkeypatch.delenv(k, raising=False)
    return s


def on_google(monkeypatch, b64=False):
    raw = json.dumps(SA)
    monkeypatch.setenv("GOOGLE_PLAY_SERVICE_ACCOUNT", base64.b64encode(raw.encode()).decode() if b64 else raw)


def on_apple(monkeypatch):
    monkeypatch.setenv("APPLE_IAP_ISSUER_ID", "issuer-123")
    monkeypatch.setenv("APPLE_IAP_KEY_ID", "KEY123")
    monkeypatch.setenv("APPLE_IAP_PRIVATE_KEY", EC_PEM.replace("\n", "\\n"))  # Render'a tek satır yapıştırılır


def balance(tok):
    return client.get("/v1/coins", headers=auth(tok)).json()["balance"]


def test_closed_without_keys_and_account_marks(store):
    tok, me = user()
    info = client.get("/v1/coins/store", headers=auth(tok)).json()
    assert info["google"] is False and info["apple"] is False
    assert [p["id"] for p in info["products"]] == ["rota_coin_50", "rota_coin_200", "rota_coin_500"]
    assert info["accountToken"] == iap.account_token(me) and len(info["accountToken"]) <= 64 and me not in info["accountToken"]
    assert str(uuid.UUID(info["appAccountToken"])) == info["appAccountToken"]
    assert client.get("/v1/coins/store", headers=auth(tok)).json()["appAccountToken"] == info["appAccountToken"]  # sabit
    assert client.post("/v1/coins/store/google", json={"productId": "rota_coin_50", "purchaseToken": "x" * 30}, headers=auth(tok)).status_code == 503
    assert client.post("/v1/coins/store/apple", json={"transactionId": "2000000123"}, headers=auth(tok)).status_code == 503
    assert store.calls == []  # anahtar yokken mağazaya hiç gidilmez


def test_google_purchase_credits_once_and_consumes(store, monkeypatch):
    on_google(monkeypatch, b64=True)
    tok, me = user("sponsor")
    assert client.get("/v1/coins/store", headers=auth(tok)).json()["google"] is True
    t = "jeton-" + "a" * 40
    store.google[t] = {"purchaseState": 0, "consumptionState": 0, "productId": "rota_coin_200", "orderId": "GPA.3300-1111", "quantity": 1,
                       "obfuscatedExternalAccountId": iap.account_token(me)}
    r = client.post("/v1/coins/store/google", json={"productId": "rota_coin_200", "purchaseToken": t}, headers=auth(tok))
    assert r.status_code == 200 and r.json()["credited"] == 200 and r.json()["coins"]["balance"] == 200, r.text
    h = r.json()["coins"]["history"][0]
    assert h["delta"] == 200 and "Google Play" in h["label"] and "deneme" not in h["label"]
    assert store.n(":consume") == 1
    again = client.post("/v1/coins/store/google", json={"productId": "rota_coin_200", "purchaseToken": t}, headers=auth(tok))
    assert again.status_code == 200 and again.json()["credited"] == 0 and again.json()["already"] and balance(tok) == 200  # BİR KEZ
    assert store.n("/tokens/") == 2 and store.n(":consume") == 1  # ikinci kez mağazaya sorulmadı
    other, _ = user()
    assert client.post("/v1/coins/store/google", json={"productId": "rota_coin_200", "purchaseToken": t}, headers=auth(other)).status_code == 409
    assert balance(other) == 0
    assert store.n("oauth2") == 1  # erişim belirteci önbellekte


def test_google_rejects_pending_cancelled_foreign_unknown_and_courier(store, monkeypatch):
    on_google(monkeypatch)
    tok, me = user()
    base = {"productId": "rota_coin_50", "orderId": "GPA.1", "obfuscatedExternalAccountId": iap.account_token(me)}
    store.google["bekleyen-" + "b" * 30] = {**base, "purchaseState": 2}
    store.google["iptal-" + "c" * 30] = {**base, "purchaseState": 1}
    store.google["baska-" + "d" * 30] = {**base, "purchaseState": 0, "obfuscatedExternalAccountId": iap.account_token("baska-hesap")}
    store.google["deneme-" + "e" * 30] = {**base, "purchaseState": 0, "purchaseType": 0}
    post = lambda t, pid="rota_coin_50", tk=tok: client.post("/v1/coins/store/google", json={"productId": pid, "purchaseToken": t}, headers=auth(tk))
    assert post("bekleyen-" + "b" * 30).status_code == 409
    assert post("iptal-" + "c" * 30).status_code == 400
    assert post("baska-" + "d" * 30).status_code == 409
    assert post("yok-" + "f" * 30).status_code == 400  # mağaza tanımıyor
    assert post("deneme-" + "e" * 30, pid="rota_coin_999").status_code == 400  # bilinmeyen paket
    ok = post("deneme-" + "e" * 30)
    assert ok.status_code == 200 and "deneme" in ok.json()["coins"]["history"][0]["label"]  # lisanslı deneme hesabı
    assert balance(tok) == 50
    ctok, _ = user("courier")
    assert post("deneme-" + "e" * 30, tk=ctok).status_code == 403
    store.down = True
    assert post("yeni-" + "g" * 30).status_code == 503


def test_apple_purchase_sandbox_fallback_once_and_checks(store, monkeypatch):
    on_apple(monkeypatch)
    tok, me = user()
    assert client.get("/v1/coins/store", headers=auth(tok)).json()["apple"] is True
    tx = {"transactionId": "2000000777", "originalTransactionId": "2000000777", "bundleId": "com.rota.asistan", "productId": "rota_coin_500",
          "type": "Consumable", "quantity": 1, "environment": "Sandbox", "appAccountToken": iap.app_account_token(me)}
    store.apple["sandbox"]["2000000777"] = tx
    r = client.post("/v1/coins/store/apple", json={"transactionId": "2000000777"}, headers=auth(tok))
    assert r.status_code == 200 and r.json()["credited"] == 500, r.text
    assert "App Store, deneme" in r.json()["coins"]["history"][0]["label"]
    assert store.n("api.storekit.itunes") == 1 and store.n("storekit-sandbox") == 1  # önce canlı, sonra deneme
    again = client.post("/v1/coins/store/apple", json={"transactionId": "2000000777"}, headers=auth(tok))
    assert again.json()["already"] and balance(tok) == 500
    store.apple["prod"]["2000000801"] = {**tx, "transactionId": "2000000801", "bundleId": "com.baska.uygulama", "environment": "Production"}
    store.apple["prod"]["2000000802"] = {**tx, "transactionId": "2000000802", "revocationDate": 1800000000000, "environment": "Production"}
    store.apple["prod"]["2000000803"] = {**tx, "transactionId": "2000000803", "appAccountToken": str(uuid.uuid4()), "environment": "Production"}
    store.apple["prod"]["2000000804"] = {**tx, "transactionId": "2000000804", "productId": "baska_urun", "environment": "Production"}
    post = lambda n: client.post("/v1/coins/store/apple", json={"transactionId": n}, headers=auth(tok)).status_code
    assert [post("2000000801"), post("2000000802"), post("2000000803"), post("2000000804"), post("2000000999")] == [400, 400, 409, 400, 400]
    assert client.post("/v1/coins/store/apple", json={"transactionId": "abc"}, headers=auth(tok)).status_code == 422
    store.apple["prod"]["2000000900"] = {**tx, "transactionId": "2000000900", "environment": "Production"}
    p = client.post("/v1/coins/store/apple", json={"transactionId": "2000000900"}, headers=auth(tok))
    assert p.status_code == 200 and p.json()["coins"]["history"][0]["label"].endswith("(App Store)") and balance(tok) == 1000
    other, _ = user()
    assert client.post("/v1/coins/store/apple", json={"transactionId": "2000000900"}, headers=auth(other)).status_code == 409


def test_products_from_env_and_test_buy_still_works(store, monkeypatch):
    monkeypatch.setenv("IAP_PRODUCTS", "coin_kucuk:50, coin_buyuk:500,bozuk, sifir:0")
    assert iap.products() == {"coin_kucuk": 50, "coin_buyuk": 500}
    tok, _ = user()
    assert client.post("/v1/coins/test-buy", json={"n": 50}, headers=auth(tok)).status_code == 200  # mağaza bağlanana kadar test sürer
    assert balance(tok) == 50
