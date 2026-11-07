"""MAĞAZA SATIN ALMASI — Rota coin (1.25.0; kullanıcı kararı 6 Kasım 2026: "Normal bir Google Play ya da App Store satışı nasıl
yapılıyorsa coin satışı öyle yapılacak"). Mağaza hesapları HENÜZ YOK: anahtarlar gelene kadar kapalı; test satın alma (COIN_TEST_BUY) sürer.

Akış (coin paketleri mağazada "tüketilebilir ürün" / consumable):
 1) Uygulama mağazanın KENDİ ödeme penceresini açar (expo-iap). Ödemeyi, fiyatı, faturayı, iadeyi mağaza yönetir; Rota kart bilgisi görmez.
    Satın almaya hesaba bağlı gizli bir işaret konur: Android'de obfuscatedAccountId, iPhone'da appAccountToken (UUID). İkisi de
    kullanıcı kimliğinden SECRET_KEY ile türetilir; kişisel bilgi içermez. Başka hesabın makbuzu bu hesaba yazılamaz.
 2) Ödeme bitince uygulama makbuzu buraya yollar: POST /v1/coins/store/google {productId, purchaseToken} ya da
    POST /v1/coins/store/apple {transactionId}.
 3) Sunucu makbuzu MAĞAZANIN SUNUCUSUNDAN doğrular (Google Play Developer API / App Store Server API) ve coin'i BİR KEZ yazar (aynı
    makbuz ikinci kez gelirse yeniden yazmaz). Google'da ürün sunucudan da "tüketilir"; uygulama ayrıca finishTransaction ile kapatır.
Ortam değişkenleri (YALNIZ Render > Environment; zip'e, GitHub'a, sohbete konmaz):
  GOOGLE_PLAY_PACKAGE (varsayılan com.rota.asistan), GOOGLE_PLAY_SERVICE_ACCOUNT (hizmet hesabı JSON'u; düz ya da base64),
  APPLE_BUNDLE_ID (varsayılan com.rota.asistan), APPLE_IAP_ISSUER_ID, APPLE_IAP_KEY_ID, APPLE_IAP_PRIVATE_KEY (.p8 içeriği; satır sonları
  \\n olabilir), IAP_PRODUCTS (isteğe bağlı; varsayılan "rota_coin_50:50,rota_coin_200:200,rota_coin_500:500").
İADE: Google RTDN / Apple sunucu bildirimleri henüz bağlı değil — iade edilen satın almada yönetim coin düzeltmesi yapar (Üyeler › coin).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Callable

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import events
from .account import add_coins, coins_out, tick
from .auth import SECRET, current_user
from .db import get_db
from .limits import Window
from .models import StorePurchase, User, now

GOOGLE_API = "https://androidpublisher.googleapis.com/androidpublisher/v3"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
APPLE_PROD = "https://api.storekit.itunes.apple.com"
APPLE_SANDBOX = "https://api.storekit-sandbox.itunes.apple.com"
DEFAULT_PRODUCTS = "rota_coin_50:50,rota_coin_200:200,rota_coin_500:500"


def products() -> dict[str, int]:
    """Mağaza ürün kimliği → coin. Uygulamadaki paketlerle (50 / 200 / 500) aynı olmalı."""
    out: dict[str, int] = {}
    for part in (os.getenv("IAP_PRODUCTS", "") or DEFAULT_PRODUCTS).split(","):
        pid, _, n = part.strip().partition(":")
        if pid and n.strip().isdigit() and int(n) > 0:
            out[pid.strip()] = int(n)
    return out


def _env(k: str, default: str = "") -> str:
    return (os.getenv(k, "") or default).strip()


def google_conf() -> dict | None:
    raw = _env("GOOGLE_PLAY_SERVICE_ACCOUNT")
    if not raw:
        return None
    try:
        sa = json.loads(raw if raw.lstrip().startswith("{") else base64.b64decode(raw).decode("utf-8"))
    except Exception:
        print("[mağaza] GOOGLE_PLAY_SERVICE_ACCOUNT okunamadı (JSON ya da base64 JSON olmalı)", flush=True)
        return None
    if not sa.get("client_email") or not sa.get("private_key"):
        return None
    return {"package": _env("GOOGLE_PLAY_PACKAGE", "com.rota.asistan"), "sa": sa}


def apple_conf() -> dict | None:
    iss, kid, key = _env("APPLE_IAP_ISSUER_ID"), _env("APPLE_IAP_KEY_ID"), _env("APPLE_IAP_PRIVATE_KEY").replace("\\n", "\n")
    if not (iss and kid and "PRIVATE KEY" in key):
        return None
    return {"bundle": _env("APPLE_BUNDLE_ID", "com.rota.asistan"), "iss": iss, "kid": kid, "key": key}


def account_token(user_id: str) -> str:
    """Android obfuscatedAccountId (en çok 64): kullanıcıdan türetilir, geri çevrilemez"""
    return hmac.new(SECRET.encode() or b"rota", ("iap:" + user_id).encode(), hashlib.sha256).hexdigest()[:64]


def app_account_token(user_id: str) -> str:
    """iPhone appAccountToken (UUID biçimi şart)"""
    h = hmac.new(SECRET.encode() or b"rota", ("iap-apple:" + user_id).encode(), hashlib.sha256).digest()
    return str(uuid.UUID(bytes=h[:16], version=4))


# ---------------------------------------------------------------------------------------------------- ağ (testler değiştirir)
def _http(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read() or b"{}"
            return r.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except Exception as e:  # ağ hatası
        print("[mağaza] ulaşılamadı:", type(e).__name__, flush=True)
        return 0, {}


TRANSPORT: list[Callable[[str, str, dict, bytes | None], tuple[int, dict]]] = [_http]

_tok_lock = threading.Lock()
_google_tok: dict = {"v": "", "exp": 0.0, "who": ""}


def google_access_token(conf: dict) -> str:
    """Hizmet hesabıyla OAuth erişim belirteci (JWT bearer; ~1 saat, önbellekte)"""
    sa = conf["sa"]
    with _tok_lock:
        if _google_tok["v"] and _google_tok["who"] == sa["client_email"] and time.time() < _google_tok["exp"] - 60:
            return _google_tok["v"]
    t = int(time.time())
    aud = sa.get("token_uri") or "https://oauth2.googleapis.com/token"
    assertion = jwt.encode({"iss": sa["client_email"], "scope": GOOGLE_SCOPE, "aud": aud, "iat": t, "exp": t + 3600}, sa["private_key"], algorithm="RS256")
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}).encode()
    st, js = TRANSPORT[0]("POST", aud, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    if st != 200 or not js.get("access_token"):
        raise HTTPException(503, "Google Play'e bağlanılamadı; biraz sonra yeniden dene (ödemen güvende, coin yüklenince gelir)")
    with _tok_lock:
        _google_tok.update(v=js["access_token"], exp=time.time() + float(js.get("expires_in") or 3600), who=sa["client_email"])
    return js["access_token"]


def apple_jwt(conf: dict) -> str:
    t = int(time.time())
    return jwt.encode({"iss": conf["iss"], "iat": t, "exp": t + 1200, "aud": "appstoreconnect-v1", "bid": conf["bundle"]}, conf["key"],
                      algorithm="ES256", headers={"kid": conf["kid"], "typ": "JWT"})


def _jws_payload(s: str) -> dict:
    """App Store'un imzalı işlem bilgisinin içeriği. İmza zinciri ayrıca denetlenmez: bilgi Apple'ın sunucusundan, kendi anahtarımızla
    ve TLS ile doğrudan alındı (uygulamadan gelen JWS'e güvenilmez, yalnız işlem numarası alınır)."""
    try:
        part = s.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


# ---------------------------------------------------------------------------------------------------- uç noktalar
class GoogleIn(BaseModel):
    productId: str = Field(min_length=1, max_length=100)
    purchaseToken: str = Field(min_length=10, max_length=4096)


class AppleIn(BaseModel):
    transactionId: str = Field(min_length=1, max_length=64, pattern=r"^\d+$")


def make_router() -> APIRouter:
    r = APIRouter()
    win = Window(60, 3600)  # hesap başına saatte en çok 60 doğrulama

    def credit(db: Session, user: User, store: str, key: str, product: str, n: int, order: str, env: str) -> dict:
        """Coin'i BİR KEZ yazar. Aynı makbuz ikinci kez gelirse (aynı hesap) yeniden yazmaz; başka hesaba hiç yazmaz."""
        old = db.query(StorePurchase).filter(StorePurchase.store == store, StorePurchase.txn_key == key).first()
        if old is not None:
            if old.user_id != user.id:
                raise HTTPException(409, "Bu satın alma başka bir hesaba yüklendi")
            return {"coins": coins_out(db, user.id), "credited": 0, "already": True}
        p = StorePurchase(user_id=user.id, store=store, txn_key=key, order_id=order[:120], product_id=product[:100], coins=n, env=env[:16], status="credited")
        db.add(p)
        try:
            db.flush()
        except IntegrityError:  # aynı anda iki istek: diğeri yazdı
            db.rollback()
            return {"coins": coins_out(db, user.id), "credited": 0, "already": True}
        tick(db, user.id, coins=True)
        where = "Google Play" if store == "google" else "App Store"
        add_coins(db, user.id, n, "store", f"Rota coin satın alma · {n} ({where}{', deneme' if env != 'Production' else ''})", ref=p.id)
        events.log(db, "coin.store", user.id, p.id, n=n, store=store, env=env)
        db.commit()
        return {"coins": coins_out(db, user.id), "credited": n, "already": False}

    def guard(user: User) -> None:
        if user.active_role not in ("employer", "sponsor"):
            raise HTTPException(403, "Rota coin işletme ve sponsor hesaplarında satın alınır")
        if not win.allow(user.id):
            raise HTTPException(429, "Çok fazla deneme; biraz sonra yeniden dene")

    @r.get("/v1/coins/store")
    def store_info(user: User = Depends(current_user)):
        """Uygulama hangi mağazanın açık olduğunu, ürün kimliklerini ve satın almaya konacak hesap işaretlerini buradan öğrenir."""
        return {
            "google": google_conf() is not None, "apple": apple_conf() is not None,
            "products": [{"id": k, "coins": v} for k, v in sorted(products().items(), key=lambda x: x[1])],
            "accountToken": account_token(user.id), "appAccountToken": app_account_token(user.id),
        }

    @r.post("/v1/coins/store/google")
    def google_buy(b: GoogleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        guard(user)
        conf = google_conf()
        if conf is None:
            raise HTTPException(503, "Google Play satın alması henüz açık değil")
        n = products().get(b.productId)
        if not n:
            raise HTTPException(400, "Bilinmeyen coin paketi")
        key = hashlib.sha256(b.purchaseToken.encode()).hexdigest()
        old = db.query(StorePurchase).filter(StorePurchase.store == "google", StorePurchase.txn_key == key).first()
        if old is not None:  # mağazaya sormadan: zaten yazıldı
            return credit(db, user, "google", key, b.productId, n, old.order_id, old.env)
        tok = google_access_token(conf)
        q = lambda s: urllib.parse.quote(s, safe="")
        url = f"{GOOGLE_API}/applications/{q(conf['package'])}/purchases/products/{q(b.productId)}/tokens/{q(b.purchaseToken)}"
        st, js = TRANSPORT[0]("GET", url, {"Authorization": "Bearer " + tok, "Accept": "application/json"}, None)
        if st in (400, 404, 410):
            raise HTTPException(400, "Mağaza bu satın almayı tanımıyor")
        if st != 200:
            raise HTTPException(503, "Google Play'e bağlanılamadı; biraz sonra yeniden dene (ödemen güvende, coin yüklenince gelir)")
        state = int(js.get("purchaseState", -1))
        if state == 2:
            raise HTTPException(409, "Ödeme henüz tamamlanmadı (bekliyor); tamamlanınca coin yüklenir")
        if state != 0:
            raise HTTPException(400, "Bu satın alma iptal edilmiş")
        if (js.get("productId") or b.productId) != b.productId:
            raise HTTPException(400, "Ürün eşleşmiyor")
        acct = js.get("obfuscatedExternalAccountId") or ""
        if acct and acct != account_token(user.id):
            raise HTTPException(409, "Bu satın alma başka bir hesaba ait")
        env = "Test" if js.get("purchaseType") == 0 else "Production"  # 0 = lisanslı deneme hesabı
        out = credit(db, user, "google", key, b.productId, n * max(1, int(js.get("quantity") or 1)), str(js.get("orderId") or ""), env)
        if out["credited"]:  # tüketilebilir: yeniden alınabilsin (uygulama da finishTransaction ile tüketir; hata önemli değil)
            TRANSPORT[0]("POST", url + ":consume", {"Authorization": "Bearer " + tok, "Content-Length": "0"}, b"")
        return out

    @r.post("/v1/coins/store/apple")
    def apple_buy(b: AppleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        guard(user)
        conf = apple_conf()
        if conf is None:
            raise HTTPException(503, "App Store satın alması henüz açık değil")
        old = db.query(StorePurchase).filter(StorePurchase.store == "apple", StorePurchase.txn_key == b.transactionId).first()
        if old is not None:
            return credit(db, user, "apple", b.transactionId, old.product_id, old.coins, old.order_id, old.env)
        auth = {"Authorization": "Bearer " + apple_jwt(conf), "Accept": "application/json"}
        st, js = TRANSPORT[0]("GET", f"{APPLE_PROD}/inApps/v1/transactions/{b.transactionId}", auth, None)
        if st == 404:  # TestFlight / sandbox satın alması: önce canlı, bulunmazsa deneme ortamı (Apple'ın önerisi)
            st, js = TRANSPORT[0]("GET", f"{APPLE_SANDBOX}/inApps/v1/transactions/{b.transactionId}", auth, None)
        if st == 404:
            raise HTTPException(400, "Mağaza bu satın almayı tanımıyor")
        if st != 200 or not js.get("signedTransactionInfo"):
            raise HTTPException(503, "App Store'a bağlanılamadı; biraz sonra yeniden dene (ödemen güvende, coin yüklenince gelir)")
        tx = _jws_payload(js["signedTransactionInfo"])
        if tx.get("bundleId") != conf["bundle"]:
            raise HTTPException(400, "Bu satın alma bu uygulamaya ait değil")
        if str(tx.get("transactionId") or "") != b.transactionId:
            raise HTTPException(400, "İşlem numarası eşleşmiyor")
        n = products().get(str(tx.get("productId") or ""))
        if not n:
            raise HTTPException(400, "Bilinmeyen coin paketi")
        if tx.get("revocationDate"):
            raise HTTPException(400, "Bu satın alma iade edilmiş")
        acct = str(tx.get("appAccountToken") or "")
        if acct and acct.lower() != app_account_token(user.id):
            raise HTTPException(409, "Bu satın alma başka bir hesaba ait")
        env = str(tx.get("environment") or "Production")
        return credit(db, user, "apple", b.transactionId, str(tx["productId"]), n * max(1, int(tx.get("quantity") or 1)), str(tx.get("originalTransactionId") or ""), env)

    return r
