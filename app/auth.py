import os
import re
from datetime import datetime, timedelta, timezone

import hashlib
import hmac

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

SECRET = os.getenv("SECRET_KEY", "rota-dev-secret-change-me")
ALGO = "HS256"


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


def make_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, SECRET, algorithm=ALGO)


def current_user(
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
    user = db.get(User, data.get("sub"))
    if not user:
        raise HTTPException(401, "Hesap bulunamadı")
    return user
