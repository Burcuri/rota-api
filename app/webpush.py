"""Tarayıcı bildirimi (Web Push) — alıcı sayfası için (1.18.0, kullanıcı isteği 28 Ekim 2026: "alıcı izin verirse tarayıcı bildirimi
gelir: Kargonuz yolda").

- Alıcı sayfada "🔔 Bildirim al" deyip izin verirse tarayıcının bildirim adresi (abonelik) yalnız O TESLİM için saklanır; teslim
  edilince, kurye teslimi bitirince ya da en geç 48 saatte silinir (handoff.py). Hesap, ad, telefon gerekmez.
- İçerik uçtan uca şifrelenir (RFC 8291, aes128gcm): aradaki bildirim servisi (Google / Apple / Mozilla / Microsoft) içeriği okuyamaz.
- Sunucu kimliği (VAPID, RFC 8292): anahtar SECRET_KEY'den türetilir. Ayrı anahtar GEREKMEZ; kodda / zipte anahtar yok. İstenirse Render
  ortamına VAPID_PRIVATE_KEY (base64url, 32 bayt) yazılarak değiştirilir. SECRET_KEY değişirse eski abonelikler geçersiz olur — zaten en
  çok 48 saatlik oldukları için sorun değil.
- İstek yalnız bilinen bildirim servislerine gider (FCM, Mozilla, Apple, Microsoft): alıcının verdiği adres sunucuyu başka yerlere
  istek attırmak için kullanılamaz.
- Gönderim arka planda yapılır (istek beklemez); servis "abonelik yok" derse (404 / 410) kayıt silinir.
"""
import base64
import hashlib
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Bilinen bildirim servisleri (tarayıcıların kendi servisleri). Başka adrese istek gitmez.
ALLOWED_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com", "web.push.apple.com")
ALLOWED_SUFFIXES = (".notify.windows.com", ".push.apple.com")
MAX_ENDPOINT = 1024
TTL_SECONDS = 6 * 3600  # telefon kapalıysa bildirim servisi en çok 6 saat bekletir ("Kargonuz yolda" sonra anlamsız)
_N = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)  # P-256 grup mertebesi


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    s = (s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------------------------------------------- anahtar
_key: list = [None]


def _private_key() -> ec.EllipticCurvePrivateKey:
    if _key[0] is None:
        raw = os.getenv("VAPID_PRIVATE_KEY", "").strip()
        if raw:
            d = int.from_bytes(unb64u(raw), "big")
        else:
            from .auth import SECRET  # oturum anahtarından ayrı amaçlı türetme (HKDF, "rota-vapid-v1")

            okm = HKDF(algorithm=hashes.SHA256(), length=48, salt=b"rota-vapid-v1", info=b"web-push vapid p-256").derive(SECRET.encode())
            d = int.from_bytes(okm, "big")
        d = d % (_N - 1) + 1
        _key[0] = ec.derive_private_key(d, ec.SECP256R1())
    return _key[0]


def reset_key() -> None:
    """Testler: ortam değişkeni değişince anahtar yeniden türetilsin."""
    _key[0] = None


def public_key() -> str:
    """Tarayıcının `applicationServerKey`i: sıkıştırılmamış P-256 noktası (65 bayt), base64url."""
    pub = _private_key().public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64u(pub)


# ---------------------------------------------------------------------------------------------------- doğrulama
def _test_prefix() -> str:
    # YALNIZ yerel denemeler içindir (tarayıcı testi sahte bildirim servisine yollar). Render'da TANIMLANMAZ.
    return os.getenv("PUSH_TEST_ENDPOINT_PREFIX", "").strip()


def endpoint_ok(url: str) -> bool:
    if not isinstance(url, str) or not url or len(url) > MAX_ENDPOINT or any(c.isspace() for c in url):
        return False
    pre = _test_prefix()
    if pre and url.startswith(pre):
        return True
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    if u.scheme != "https" or u.username or u.password or (u.port not in (None, 443)):
        return False
    host = (u.hostname or "").lower().rstrip(".")
    return host in ALLOWED_HOSTS or any(host.endswith(s) and len(host) > len(s) for s in ALLOWED_SUFFIXES)


def keys_ok(p256dh: str, auth: str) -> bool:
    try:
        pub = unb64u(p256dh)
        sec = unb64u(auth)
    except (ValueError, TypeError):
        return False
    if len(pub) != 65 or pub[0] != 4 or len(sec) != 16:
        return False
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------------------------------- şifreleme (RFC 8291 + RFC 8188)
def encrypt(plaintext: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, sender: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """aes128gcm gövdesi: tuz(16) | kayıt boyu(4) | anahtar boyu(1) | gönderen açık anahtarı(65) | şifreli kayıt.
    `salt` / `sender` yalnız RFC test vektörü için verilir; normalde her bildirimde yeni üretilir."""
    ua_pub = unb64u(p256dh)
    auth_secret = unb64u(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    as_key = sender or ec.generate_private_key(ec.SECP256R1())
    as_pub = as_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)  # tek (son) kayıt: ayraç 0x02
    rs = 4096
    return salt + struct.pack("!I", rs) + bytes([len(as_pub)]) + as_pub + record


# ---------------------------------------------------------------------------------------------------- VAPID
def subject() -> str:
    s = os.getenv("VAPID_SUBJECT", "").strip()
    if s:
        return s
    base = (os.getenv("PUBLIC_BASE", "") or os.getenv("RENDER_EXTERNAL_URL", "")).strip().rstrip("/")
    return base if base.startswith("https://") else "mailto:bildirim@rota.invalid"


def vapid_header(endpoint: str, now_s: int | None = None) -> str:
    u = urlsplit(endpoint)
    aud = f"{u.scheme}://{u.netloc}"
    exp = int(now_s if now_s is not None else time.time()) + 3600
    tok = jwt.encode({"aud": aud, "exp": exp, "sub": subject()}, _private_key(), algorithm="ES256", headers={"typ": "JWT"})
    return f"vapid t={tok}, k={public_key()}"


# ---------------------------------------------------------------------------------------------------- gönderim
def _post(url: str, body: bytes, headers: dict, timeout: float = 8.0) -> int:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # ağ hatası: sessiz geç, sunucu düşmesin
        print("[bildirim] web push gönderilemedi:", type(e).__name__, flush=True)
        return 0


# Testler bunu değiştirir (gerçek ağa çıkmadan yakalar)
TRANSPORT: list[Callable[[str, bytes, dict], int]] = [lambda url, body, headers: _post(url, body, headers)]
# Testlerde True: gönderim istek içinde, sırayla yapılır (sonuç hemen denetlenebilsin)
SYNC = [False]


def send_one(endpoint: str, p256dh: str, auth: str, payload: dict, ttl: int = TTL_SECONDS) -> int:
    if not endpoint_ok(endpoint) or not keys_ok(p256dh, auth):
        return 0
    body = encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"), p256dh, auth)
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Encoding": "aes128gcm",
        "TTL": str(int(ttl)),
        "Urgency": "high",
        "Topic": hashlib.sha256((payload.get("tag") or "rota").encode()).hexdigest()[:32],  # aynı teslimin eski bildirimi yenisiyle değişir
        "Authorization": vapid_header(endpoint),
    }
    return TRANSPORT[0](endpoint, body, headers)


def dispatch(items: list[tuple[str, str, str, str]], payload: dict, on_gone: Callable[[list[str]], None] | None = None) -> None:
    """items: (abonelik kimliği, adres, p256dh, auth). Arka planda gönderir; servis 404 / 410 derse `on_gone(kimlikler)`."""
    if not items:
        return

    def run():
        gone: list[str] = []
        for sid, endpoint, p256dh, auth in items:
            try:
                st = send_one(endpoint, p256dh, auth, payload)
            except Exception as e:  # tek abonelik hatası diğerlerini durdurmasın
                print("[bildirim] web push hatası:", type(e).__name__, flush=True)
                st = 0
            if st in (404, 410):
                gone.append(sid)
        if gone and on_gone:
            try:
                on_gone(gone)
            except Exception as e:
                print("[bildirim] abonelik silinemedi:", type(e).__name__, flush=True)

    if SYNC[0]:
        run()
    else:
        threading.Thread(target=run, name="web-push", daemon=True).start()
