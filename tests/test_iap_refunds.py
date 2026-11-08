"""1.28.0 — Mağaza İADESİ (kullanıcı isteği 9 Kasım 2026: "coin satışını mağazalara bağlamak … altyapısını lütfen hazırla").
Google Play: iptal edilen satın almalar listesi (Voided Purchases API) okunur → coin BİR KEZ düşülür (bakiye eksiye inebilir).
App Store: sunucu bildirimi (REFUND) yalnız ipucudur → işlem Apple'dan yeniden sorulur; iade tarihi yoksa (sahte bildirim) bir şey olmaz.
Yönetim: özet ve "şimdi denetle"."""
import base64
import json

import pytest

from app import iap
from app.db import SessionLocal
from app.models import StorePurchase

from helpers import admin_login
from test_auth_consent import auth
from test_iap import Store, balance, client, on_apple, on_google, user


class Store2(Store):
    def __init__(self):
        super().__init__()
        self.voided: list[dict] = []
        self.paged = False

    def __call__(self, method, url, headers, body):
        if "voidedpurchases" in url:
            self.calls.append((method, url, headers, body))
            if self.down:
                return 0, {}
            assert headers["Authorization"] == "Bearer erisim-belirteci" and "type=0" in url and "startTime=" in url
            if self.paged and "token=sayfa2" not in url:
                return 200, {"voidedPurchases": self.voided[:1], "tokenPagination": {"nextPageToken": "sayfa2"}}
            if self.paged:
                return 200, {"voidedPurchases": self.voided[1:]}
            return 200, {"voidedPurchases": self.voided}
        return super().__call__(method, url, headers, body)


@pytest.fixture()
def store(monkeypatch):
    s = Store2()
    monkeypatch.setattr(iap, "TRANSPORT", [s])
    iap._google_tok.update(v="", exp=0.0, who="")
    monkeypatch.delenv("GOOGLE_PLAY_SERVICE_ACCOUNT", raising=False)
    for k in ("APPLE_IAP_ISSUER_ID", "APPLE_IAP_KEY_ID", "APPLE_IAP_PRIVATE_KEY", "IAP_PRODUCTS"):
        monkeypatch.delenv(k, raising=False)
    return s


def jws(obj: dict) -> str:
    return "eyJhbGciOiJFUzI1NiJ9." + base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=") + ".imza"


def notices(tok):
    return [n["text"] for n in client.get("/v1/notices", headers=auth(tok)).json()["items"]]


def g_buy(store, tok, me, t, pid="rota_coin_50"):
    store.google[t] = {"purchaseState": 0, "productId": pid, "orderId": "GPA." + t[-6:], "quantity": 1, "obfuscatedExternalAccountId": iap.account_token(me)}
    r = client.post("/v1/coins/store/google", json={"productId": pid, "purchaseToken": t}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r


def test_google_voided_purchase_reversed_once_even_into_debt(store, monkeypatch):
    on_google(monkeypatch)
    tok, me = user("sponsor")
    t1, t2 = "iade-" + "a" * 40, "kalan-" + "b" * 40
    g_buy(store, tok, me, t1)
    g_buy(store, tok, me, t2)
    assert balance(tok) == 100
    pts = [{"lat": 36.55 + i * 0.002, "lon": 32.0} for i in range(10)]
    assert client.post("/v1/ads", json={"points": pts}, headers=auth(tok)).status_code == 200  # 10 coin harcandı
    assert balance(tok) == 90
    store.voided = [{"purchaseToken": t1, "orderId": "GPA.aaaaaa", "voidedTimeMillis": "1800000000000", "voidedReason": 1},
                    {"purchaseToken": "bizde-olmayan-" + "c" * 30}]
    db = SessionLocal()
    try:
        res = iap.google_voided_sync(db)
    finally:
        db.close()
    assert res == {"ok": True, "checked": 2, "reversed": 1, "why": ""}
    assert balance(tok) == 40
    db = SessionLocal()
    try:
        rows = {p.order_id: p for p in db.query(StorePurchase).filter(StorePurchase.user_id == me).all()}
        assert rows["GPA.aaaaaa"].status == "refunded" and rows["GPA.aaaaaa"].refunded_at is not None
        assert rows["GPA." + t2[-6:]].status == "credited"
        again = iap.google_voided_sync(db)
    finally:
        db.close()
    assert again["reversed"] == 0 and balance(tok) == 40  # BİR KEZ
    hist = client.get("/v1/coins", headers=auth(tok)).json()["history"]
    assert hist[0]["delta"] == -50 and hist[0]["label"].startswith("İade") and hist[0]["kind"] == "store_refund"
    assert any("iade etti" in n and "50 coin" in n for n in notices(tok))
    # Harcanmış coin iade edilirse bakiye eksiye iner (borç); yeni harcama yapılamaz
    store.voided = [{"purchaseToken": t2}]
    store.paged = False
    db = SessionLocal()
    try:
        assert iap.google_voided_sync(db)["reversed"] == 1
    finally:
        db.close()
    assert balance(tok) == -10
    assert client.post("/v1/ads", json={"points": [{"lat": 36.70, "lon": 32.0}]}, headers=auth(tok)).status_code == 400


def test_google_voided_pages_and_store_down(store, monkeypatch):
    on_google(monkeypatch)
    tok, me = user()
    t1, t2 = "sayfa-" + "d" * 40, "sayfa-" + "e" * 40
    g_buy(store, tok, me, t1)
    g_buy(store, tok, me, t2)
    store.voided = [{"purchaseToken": t1}, {"purchaseToken": t2}]
    store.paged = True
    db = SessionLocal()
    try:
        assert iap.google_voided_sync(db)["reversed"] == 2
        store.down = True
        down = iap.google_voided_sync(db)
    finally:
        db.close()
    assert balance(tok) == 0 and store.n("token=sayfa2") == 1
    assert down["ok"] is False and down["checked"] == 0


def test_apple_refund_notification_verified_by_refetch(store, monkeypatch):
    on_apple(monkeypatch)
    tok, me = user()
    tx = {"transactionId": "2000001111", "originalTransactionId": "2000001111", "bundleId": "com.rota.asistan", "productId": "rota_coin_200",
          "type": "Consumable", "quantity": 1, "environment": "Production", "appAccountToken": iap.app_account_token(me)}
    store.apple["prod"]["2000001111"] = dict(tx)
    assert client.post("/v1/coins/store/apple", json={"transactionId": "2000001111"}, headers=auth(tok)).status_code == 200
    assert balance(tok) == 200

    def note(kind="REFUND", txid="2000001111", bundle="com.rota.asistan"):
        payload = {"notificationType": kind, "data": {"bundleId": bundle, "signedTransactionInfo": jws({"transactionId": txid})}}
        return client.post("/v1/iap/apple/notify", json={"signedPayload": jws(payload)})

    # Sahte bildirim: Apple'da iade tarihi yok → coin düşülmez
    r = note()
    assert r.status_code == 200 and r.json() == {"ok": True, "done": False} and balance(tok) == 200
    # Başka tür / başka uygulama / bilinmeyen işlem / bozuk gövde → bir şey olmaz, yine 200
    assert note("DID_RENEW").json()["done"] is False
    assert note(bundle="com.baska.uygulama").json()["done"] is False
    assert note(txid="2000009999").json()["done"] is False
    assert client.post("/v1/iap/apple/notify", json={"signedPayload": "bozuk"}).json() == {"ok": True, "done": False}
    assert client.post("/v1/iap/apple/notify", json={}).status_code == 200
    # Gerçek iade: Apple'ın sunucusunda iade tarihi var
    store.apple["prod"]["2000001111"]["revocationDate"] = 1800000000000
    r = note()
    assert r.json() == {"ok": True, "done": True} and balance(tok) == 0
    assert note().json()["done"] is False and balance(tok) == 0  # ikinci bildirim: BİR KEZ
    assert any("App Store" in n and "iade etti" in n for n in notices(tok))


def test_notify_closed_without_apple_keys(store):
    payload = {"notificationType": "REFUND", "data": {"signedTransactionInfo": jws({"transactionId": "2000001111"})}}
    assert client.post("/v1/iap/apple/notify", json={"signedPayload": jws(payload)}).json() == {"ok": True, "done": False}
    assert store.calls == []


def test_admin_summary_and_sync(store, monkeypatch):
    h, _ = admin_login()
    assert client.get("/v1/admin/iap/summary").status_code == 401
    s0 = client.get("/v1/admin/iap/summary", headers=h).json()
    assert s0["google"] is False and s0["apple"] is False and s0["testBuy"] is True
    off = client.post("/v1/admin/iap/refunds/sync", headers=h).json()
    assert off["ok"] is False and "bağlı değil" in off["why"]
    on_google(monkeypatch)
    tok, me = user()
    t = "ozet-" + "f" * 40
    g_buy(store, tok, me, t, "rota_coin_200")
    store.voided = [{"purchaseToken": t}]
    r = client.post("/v1/admin/iap/refunds/sync", headers=h).json()
    assert r["ok"] and r["reversed"] == 1
    s = client.get("/v1/admin/iap/summary", headers=h).json()
    g = s["last30"]["google"]
    assert s["google"] is True and g["refunds"] >= 1 and g["refundCoins"] >= 200 and s["lastSync"]["reversed"] == 1
    monkeypatch.setenv("COIN_TEST_BUY", "0")
    assert client.get("/v1/admin/iap/summary", headers=h).json()["testBuy"] is False
