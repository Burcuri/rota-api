import os
import re
from datetime import datetime, timedelta, timezone

import hashlib
import hmac

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

ALGO = "HS256"

# Oturum anahtarı. Bu repo herkese açık olduğu için koddaki yedek değer GİZLİ DEĞİLDİR: onunla imzalanmış oturumu herkes
# üretebilir. Bu yüzden gerçek (PostgreSQL) veritabanıyla çalışırken SECRET_KEY zorunlu; yalnız yerel geliştirmede
# (SQLite) yedek değer kullanılır.
SECRET = os.getenv("SECRET_KEY", "").strip()
if not SECRET:
    if (os.getenv("DATABASE_URL") or "").startswith("postgres"):
        raise RuntimeError(
            "SECRET_KEY ortam değişkeni tanımlı değil. Render > servis > Environment bölümüne uzun, rastgele bir "
            "SECRET_KEY ekle (en az 32 karakter), sonra yeniden dağıt."
        )
    SECRET = "rota-dev-secret-change-me"
elif len(SECRET) < 16:
    raise RuntimeError("SECRET_KEY çok kısa: en az 16, tercihen 32+ rastgele karakter olmalı.")


def norm_phone(raw: str) -> str:
    s = re.sub(r"\D", "", raw or "")
    if s.startswith("00"):
        s = s[2:]
    if s.startswith("90") and len(s) == 12:
        s = s[2:]
    if len(s) == 10 and s[0] in "2345":
        s = "0" + s
    if not re.fullmatch(r"0[2-5]\d{9}", s):
        raise HTTPException(400, "Telefon 05xx veya sabit hat gibi 11 hane olmalı")
    return s


USERNAME_RE = re.compile(r"^(?=.*[a-z])[a-z0-9_.]{3,20}$")
# Taklit edilebilecek ya da ileride sistem için ayrılacak adlar
RESERVED_USERNAMES = {
    "admin", "administrator", "root", "rota", "rotaasistan", "rota_asistan", "destek", "support", "sistem", "system",
    "moderator", "mod", "yonetici", "info", "iletisim", "help",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
PHONE_LIKE_RE = re.compile(r"^[\d\s()+\-]{7,}$")


def is_phone_like(raw: str) -> bool:
    """Rakam ağırlıklı yazılan giriş telefon sayılır; harf içeriyorsa kullanıcı adıdır."""
    return bool(PHONE_LIKE_RE.match((raw or "").strip()))


def norm_username(raw: str) -> str:
    """Kullanıcı adını küçük harfe çevirip denetler. Hatalıysa 400 döner. Boşsa '' döner (kullanıcı adı yok)."""
    u = (raw or "").strip().lower()
    if not u:
        return ""
    if not USERNAME_RE.match(u):
        raise HTTPException(400, "Kullanıcı adı 3–20 karakter olmalı: harf, rakam, _ ve nokta (en az bir harf)")
    if u in RESERVED_USERNAMES:
        raise HTTPException(400, "Bu kullanıcı adı kullanılamaz, başka bir ad seç")
    return u


def norm_email(raw: str) -> str:
    """E-postayı küçük harfe çevirip biçimini denetler. Boşsa '' döner."""
    e = (raw or "").strip().lower()
    if not e:
        return ""
    if len(e) > 254 or not EMAIL_RE.match(e):
        raise HTTPException(400, "E-posta biçimi geçersiz")
    return e


def hash_pw(p: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 120000).hex()
    return f"pbkdf2${salt}${dk}"


def check_pw(p: str, h: str) -> bool:
    try:
        _, salt, dk = h.split("$", 2)
        got = hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 120000).hex()
        return hmac.compare_digest(got, dk)
    except Exception:
        return False


# Hesap yokken de aynı süre harcansın (giriş denemesiyle "bu telefon kayıtlı mı" öğrenilmesin)
DUMMY_HASH = hash_pw("zaman-esitleme")


class AccountBlocked(HTTPException):
    """Yönetim hesabı askıya aldı. Uygulama `code` alanına bakıp kullanıcıyı çıkış yaptırır ve nedeni gösterir."""

    code = "banned"

    def __init__(self, reason: str = ""):
        reason = (reason or "").strip()
        msg = "Hesabınız askıya alındı."
        if reason:
            msg += f" Neden: {reason.rstrip('.')}."
        super().__init__(403, msg + " İtiraz için destek ile iletişime geçin.")
        self.reason = reason


class PasswordChangeRequired(HTTPException):
    """Yönetim şifreyi sıfırladı: kullanıcı yeni şifre koyana kadar yalnız MUST_CHANGE_OK yolları çalışır."""

    code = "must_change_password"

    def __init__(self):
        super().__init__(403, "Yönetim şifrenizi sıfırladı. Devam etmek için yeni bir şifre belirleyin.")


# Şifresi sıfırlanan kullanıcının kullanabildiği tek yollar: kendi bilgisi, şifre değiştirme, cihazı bırakma (çıkış)
MUST_CHANGE_OK = {"/v1/me", "/v1/me/password", "/v1/devices/forget"}


def make_token(user_id: str, tv: int = 0) -> str:
    """Kullanıcı oturumu (30 gün). `tv` kullanıcının oturum sürümüdür: şifre değişince / yönetim oturumları kapatınca artar."""
    payload = {
        "sub": user_id,
        "typ": "usr",
        "tv": int(tv or 0),
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, SECRET, algorithm=ALGO)


def user_token(user: User) -> str:
    return make_token(user.id, user.token_version or 0)


def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Giriş yap")
    token = authorization.split(" ", 1)[1].strip()
    try:
        data = jwt.decode(token, SECRET, algorithms=[ALGO])
    except Exception:
        raise HTTPException(401, "Oturum geçersiz, tekrar giriş yap")
    if data.get("typ", "usr") != "usr":  # yönetici oturumu kullanıcı ucu açamaz
        raise HTTPException(401, "Oturum geçersiz, tekrar giriş yap")
    user = db.get(User, data.get("sub"))
    if not user:
        raise HTTPException(401, "Hesap bulunamadı")
    if user.banned:
        raise AccountBlocked(user.banned_reason or "")
    if int(data.get("tv") or 0) != int(user.token_version or 0):
        raise HTTPException(401, "Oturum sona erdi, tekrar giriş yap")
    if user.must_change_password and request.url.path.rstrip("/") not in MUST_CHANGE_OK:
        raise PasswordChangeRequired()
    return user
