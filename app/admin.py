"""Yönetim (yalnız web): yönetici hesapları, üye yönetimi, etkinlik akışı, değerlendirmeler, hata özeti, işlem günlüğü.

Giriş: yönetici adı + şifre (+ isteğe bağlı iki adımlı doğrulama / TOTP; ADMIN_REQUIRE_2FA=1 ile zorunlu). Yönetici hesapları uygulama
kullanıcılarından AYRIDIR; uygulamada yönetici girişi yoktur. İlk yönetici (ve unutulan şifrenin kurtarılması) Render ortamındaki
ADMIN_KEY ile yapılır: anahtar yalnız kurulum / kurtarma içindir, günlük işlerde kullanılmaz.

Yönetim sunucudaki kayıtların hepsine bakabilir: üyeler, etkinlikler, değerlendirmeler, hata özeti ve takip kayıtları (son 48 saatin
takipleri, konum izi ve son ilerleme dahil). Mesaj içerikleri, adres listeleri ve alıcı adı / telefonu sunucuda TUTULMADIĞI (telefonlarda
kalır) için panelde de yoktur.
Her yönetici işlemi `admin_audit` tablosuna yazılır (şifre / kod gibi gizli değerler hariç; telefon ve e-posta maskeli). Bir takibin konum
izini açmak da kayda geçer.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from . import events, reviews as rv
from .auth import ALGO, DUMMY_HASH, SECRET, check_pw, hash_pw, norm_email, norm_phone, norm_username
from .db import get_db
from .errors import summarize as errors_summary
from .follow import trail_of
from .limits import Window, client_ip
from .models import Admin, AdminAudit, ConsentLog, Device, DoorPoint, Event, Follow, Handoff, Job, Review, User, now

ROLES = ("courier", "employer", "sponsor")
ADMIN_TOKEN_HOURS = 12
ADMIN_NAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
MIN_ADMIN_PW = 10
TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 0/O, 1/l/I gibi karışanlar yok


# ------------------------------------------------------------------ TOTP (RFC 6238; harici kütüphane gerekmez)
def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def totp_code(secret: str, step: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def verify_totp(secret: str, code: str, last_step: int = 0, at: float | None = None) -> int | None:
    """Kod doğruysa eşleşen 30 sn'lik adımı, değilse None döner. Daha önce kullanılmış (ya da eski) adım kabul edilmez."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", code or "") or not secret:
        return None
    cur = int((time.time() if at is None else at) // 30)
    for st in (cur - 1, cur, cur + 1):
        if st > (last_step or 0) and hmac.compare_digest(totp_code(secret, st), code):
            return st
    return None


# ------------------------------------------------------------------ yardımcılar
def _utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt) -> str | None:
    d = _utc(dt)
    return d.isoformat() if d else None


def mask_phone(p: str | None) -> str:
    p = p or ""
    return f"{p[:4]}***{p[-2:]}" if len(p) >= 8 else ("***" if p else "")


def mask_email(e: str | None) -> str:
    e = e or ""
    if "@" not in e:
        return "***" if e else ""
    a, b = e.split("@", 1)
    return f"{a[:1]}***@{b}"


def like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def admin_key() -> str:
    k = os.getenv("ADMIN_KEY", "").strip()
    return k if len(k) >= 12 else ""


def require_2fa() -> bool:
    return os.getenv("ADMIN_REQUIRE_2FA", "").strip() == "1"


def admin_out(a: Admin) -> dict:
    return {
        "id": a.id, "username": a.username, "totp": bool(a.totp_on), "disabled": bool(a.disabled),
        "createdAt": iso(a.created_at), "lastLoginAt": iso(a.last_login_at), "createdBy": a.created_by or "",
    }


def make_admin_token(a: Admin, limited: bool = False) -> tuple[str, datetime]:
    exp = datetime.now(timezone.utc) + timedelta(hours=ADMIN_TOKEN_HOURS)
    payload = {"sub": a.id, "typ": "adm", "tv": int(a.token_version or 0), "exp": exp}
    if limited:
        payload["lim"] = "2fa"  # iki adımlı doğrulamayı kurana kadar yalnız kurulum uçları çalışır
    return jwt.encode(payload, SECRET, algorithm=ALGO), exp


def _load_admin(authorization: str | None, db: Session, allow_limited: bool) -> Admin:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Yönetici girişi gerekli")
    try:
        data = jwt.decode(authorization.split(" ", 1)[1].strip(), SECRET, algorithms=[ALGO])
    except Exception:
        raise HTTPException(401, "Oturum geçersiz ya da süresi doldu, tekrar giriş yapın")
    if data.get("typ") != "adm":
        raise HTTPException(401, "Oturum geçersiz")
    a = db.get(Admin, data.get("sub"))
    if a is None or a.disabled or int(data.get("tv") or 0) != int(a.token_version or 0):
        raise HTTPException(401, "Oturum geçersiz ya da süresi doldu, tekrar giriş yapın")
    if data.get("lim") and not allow_limited:
        raise HTTPException(403, "Önce iki adımlı doğrulamayı kurun (Hesabım sekmesi)")
    return a


def current_admin(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Admin:
    return _load_admin(authorization, db, False)


def current_admin_any(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Admin:
    return _load_admin(authorization, db, True)


def audit(db: Session, request: Request | None, a: Admin | None, action: str, target_type: str = "", target_id: str = "", detail: str = "", name: str = "") -> None:
    """İşlem günlüğü satırı ekler (commit ETMEZ)."""
    db.add(AdminAudit(
        admin_id=a.id if a else None, admin_name=(a.username if a else name)[:32], action=action[:40], target_type=target_type[:12],
        target_id=target_id[:40], detail=detail[:500], ip=client_ip(request)[:64] if request else "",
    ))


# ------------------------------------------------------------------ istek gövdeleri
class LoginIn(BaseModel):
    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=200)
    code: str = Field(default="", max_length=16)


class SetupIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)
    reset: bool = False  # var olan yöneticinin şifresini sıfırlar (ve iki adımlı doğrulamasını kapatır)


class PasswordIn(BaseModel):
    old_password: str = Field(default="", alias="oldPassword", max_length=200)
    new_password: str = Field(default="", alias="newPassword", max_length=200)

    model_config = {"populate_by_name": True}


class CodeIn(BaseModel):
    code: str = Field(default="", max_length=16)
    password: str = Field(default="", max_length=200)


class NewAdminIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class UserEditIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    username: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    role: str | None = Field(default=None, max_length=16)


class BanIn(BaseModel):
    reason: str = Field(default="", max_length=300)


class ResetIn(BaseModel):
    password: str | None = Field(default=None, max_length=200)


class HideIn(BaseModel):
    hidden: bool = True
    note: str = Field(default="", max_length=200)


def make_router(keep_days_errors: float = 14, follow_keep_hours: float = 48) -> APIRouter:
    r = APIRouter()
    login_ip = Window(30, 900)  # IP başı 15 dakikada 30 yanlış deneme (X-Forwarded-For taklit edilebilir; asıl koruma kullanıcı sınırı)
    login_user = Window(8, 900)  # yönetici adı başı 15 dakikada 8 yanlış deneme
    bad_keys = Window(10, 600)  # yanlış ADMIN_KEY denemesi (IP başı)
    pw_fail = Window(8, 900)

    # ------------------------------------------------------------------ giriş / kurulum
    @r.get("/v1/admin/status")
    def status(db: Session = Depends(get_db)):
        """Giriş sayfası hangi bölümleri göstereceğini buradan öğrenir (gizli bir şey vermez)."""
        return {"setupOpen": bool(admin_key()), "hasAdmin": (db.query(func.count(Admin.id)).scalar() or 0) > 0, "require2fa": require_2fa()}

    def fail_login(request: Request, uname: str, db: Session, why: str):
        login_ip.allow(client_ip(request))
        login_user.allow(uname)
        audit(db, request, None, "login_fail", detail=why, name=uname)
        db.commit()
        raise HTTPException(401, "Yönetici adı, şifre ya da kod yanlış")

    @r.post("/v1/admin/login")
    def login(b: LoginIn, request: Request, db: Session = Depends(get_db)):
        ip = client_ip(request)
        uname = (b.username or "").strip().lower()[:32]
        if login_ip.count(ip) >= login_ip.max_hits or login_user.count(uname) >= login_user.max_hits:
            raise HTTPException(429, "Çok fazla deneme, biraz bekleyin")  # kilitliyken doğru şifre de denenmez
        a = db.query(Admin).filter(Admin.username == uname).first() if uname else None
        ok = check_pw(b.password or "", a.password_hash if a else DUMMY_HASH)  # hesap yokken de aynı süre harcanır
        if not a or not ok or a.disabled:
            fail_login(request, uname, db, "şifre")
        if a.totp_on:
            if not (b.code or "").strip():
                return {"need2fa": True}  # şifre doğru: kod isteniyor (şifreyi bilmeyene 2FA durumu açıklanmaz)
            step = verify_totp(a.totp_secret or "", b.code, a.totp_last_step or 0)
            if step is None:
                fail_login(request, uname, db, "kod")
            a.totp_last_step = step
        login_user.reset_key(uname)
        limited = require_2fa() and not a.totp_on
        a.last_login_at = now()
        audit(db, request, a, "login", detail="2fa kurulumu bekleniyor" if limited else "")
        token, exp = make_admin_token(a, limited)
        db.commit()
        return {"token": token, "expiresAt": iso(exp), "limited": limited, "admin": admin_out(a)}

    def check_setup_key(request: Request, key: str | None):
        ak = admin_key()
        if not ak:
            raise HTTPException(404, "Yok")  # ADMIN_KEY tanımlı değilse uç hiç yokmuş gibi davranır
        ip = client_ip(request)
        if bad_keys.count("bad:" + ip) >= bad_keys.max_hits:
            raise HTTPException(429, "Çok fazla deneme, biraz bekleyin")
        if not key or not hmac.compare_digest(key.encode(), ak.encode()):
            bad_keys.allow("bad:" + ip)
            raise HTTPException(401, "Anahtar yanlış")
        bad_keys.reset_key("bad:" + ip)

    @r.post("/v1/admin/setup")
    def setup(b: SetupIn, request: Request, x_admin_key: str | None = Header(default=None), db: Session = Depends(get_db)):
        """Kurulum / kurtarma: ADMIN_KEY ile yönetici oluşturur ya da (reset=true) var olanın şifresini sıfırlar."""
        check_setup_key(request, x_admin_key)
        uname = (b.username or "").strip().lower()
        if not ADMIN_NAME_RE.match(uname):
            raise HTTPException(400, "Yönetici adı 3–32 karakter: küçük harf, rakam, _ . -")
        if len(b.password or "") < MIN_ADMIN_PW:
            raise HTTPException(400, f"Şifre en az {MIN_ADMIN_PW} karakter olmalı")
        a = db.query(Admin).filter(Admin.username == uname).first()
        if a is not None:
            if not b.reset:
                raise HTTPException(409, "Bu yönetici adı var. Şifresini sıfırlamak için 'sıfırla' kutusunu işaretleyin.")
            a.password_hash = hash_pw(b.password)
            a.totp_on, a.totp_secret, a.totp_last_step = False, None, 0
            a.disabled = False
            a.token_version = (a.token_version or 0) + 1
            audit(db, request, None, "admin_reset", "admin", a.id, "anahtarla şifre sıfırlandı", name=uname)
            db.commit()
            return {"ok": True, "created": False}
        a = Admin(username=uname, password_hash=hash_pw(b.password), created_by="anahtar")
        db.add(a)
        db.flush()
        audit(db, request, None, "admin_create", "admin", a.id, "anahtarla oluşturuldu", name=uname)
        db.commit()
        return {"ok": True, "created": True}

    # ------------------------------------------------------------------ yöneticinin kendi hesabı
    @r.get("/v1/admin/me")
    def me(a: Admin = Depends(current_admin_any)):
        return {"admin": admin_out(a), "require2fa": require_2fa()}

    @r.post("/v1/admin/logout")
    def logout(request: Request, a: Admin = Depends(current_admin_any), db: Session = Depends(get_db)):
        a.token_version = (a.token_version or 0) + 1  # bu yöneticinin bütün oturumları kapanır
        audit(db, request, a, "logout")
        db.commit()
        return {"ok": True}

    @r.post("/v1/admin/password")
    def change_password(b: PasswordIn, request: Request, a: Admin = Depends(current_admin_any), db: Session = Depends(get_db)):
        if pw_fail.count(a.id) >= pw_fail.max_hits:
            raise HTTPException(429, "Çok fazla yanlış deneme, biraz bekleyin")
        if not check_pw(b.old_password or "", a.password_hash):
            pw_fail.allow(a.id)
            raise HTTPException(400, "Eski şifre yanlış")
        if len(b.new_password or "") < MIN_ADMIN_PW:
            raise HTTPException(400, f"Yeni şifre en az {MIN_ADMIN_PW} karakter olmalı")
        if b.new_password == b.old_password:
            raise HTTPException(400, "Yeni şifre eskisiyle aynı olamaz")
        pw_fail.reset_key(a.id)
        a.password_hash = hash_pw(b.new_password)
        a.token_version = (a.token_version or 0) + 1
        audit(db, request, a, "password")
        token, exp = make_admin_token(a, limited=require_2fa() and not a.totp_on)
        db.commit()
        return {"ok": True, "token": token, "expiresAt": iso(exp)}

    @r.post("/v1/admin/2fa/start")
    def totp_start(request: Request, a: Admin = Depends(current_admin_any), db: Session = Depends(get_db)):
        """Yeni gizli anahtar üretir (henüz açık değil). Kimlik doğrulayıcı uygulamaya (Google/Microsoft Authenticator, Aegis…) eklenir."""
        if a.totp_on:
            raise HTTPException(409, "İki adımlı doğrulama zaten açık. Yeniden kurmak için önce kapatın.")
        a.totp_secret = new_totp_secret()
        db.commit()
        uri = f"otpauth://totp/{quote('Rota Yönetim')}:{quote(a.username)}?secret={a.totp_secret}&issuer={quote('Rota Yönetim')}&digits=6&period=30"
        return {"secret": a.totp_secret, "uri": uri}

    @r.post("/v1/admin/2fa/enable")
    def totp_enable(b: CodeIn, request: Request, a: Admin = Depends(current_admin_any), db: Session = Depends(get_db)):
        if a.totp_on or not a.totp_secret:
            raise HTTPException(409, "Önce kurulumu başlatın")
        if login_user.count("2fa:" + a.id) >= login_user.max_hits:
            raise HTTPException(429, "Çok fazla yanlış deneme, biraz bekleyin")
        step = verify_totp(a.totp_secret, b.code, a.totp_last_step or 0)
        if step is None:
            login_user.allow("2fa:" + a.id)
            raise HTTPException(400, "Kod yanlış. Uygulamadaki güncel 6 haneli kodu yazın.")
        a.totp_on, a.totp_last_step = True, step
        audit(db, request, a, "2fa_on")
        token, exp = make_admin_token(a)  # sınırlı oturumdan tam oturuma geçilir
        db.commit()
        return {"ok": True, "token": token, "expiresAt": iso(exp)}

    @r.post("/v1/admin/2fa/disable")
    def totp_disable(b: CodeIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        if require_2fa():
            raise HTTPException(400, "İki adımlı doğrulama zorunlu (ADMIN_REQUIRE_2FA); kapatılamaz")
        if not a.totp_on:
            return {"ok": True}
        if not check_pw(b.password or "", a.password_hash):
            raise HTTPException(400, "Şifre yanlış")
        step = verify_totp(a.totp_secret or "", b.code, a.totp_last_step or 0)
        if step is None:
            raise HTTPException(400, "Kod yanlış")
        a.totp_on, a.totp_secret, a.totp_last_step = False, None, 0
        audit(db, request, a, "2fa_off")
        db.commit()
        return {"ok": True}

    @r.get("/v1/admin/admins")
    def admins(a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        return {"items": [admin_out(x) for x in db.query(Admin).order_by(Admin.created_at.asc()).all()]}

    @r.post("/v1/admin/admins")
    def admin_create(b: NewAdminIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        uname = (b.username or "").strip().lower()
        if not ADMIN_NAME_RE.match(uname):
            raise HTTPException(400, "Yönetici adı 3–32 karakter: küçük harf, rakam, _ . -")
        if len(b.password or "") < MIN_ADMIN_PW:
            raise HTTPException(400, f"Şifre en az {MIN_ADMIN_PW} karakter olmalı")
        if db.query(Admin).filter(Admin.username == uname).first():
            raise HTTPException(409, "Bu yönetici adı var")
        n = Admin(username=uname, password_hash=hash_pw(b.password), created_by=a.username)
        db.add(n)
        db.flush()
        audit(db, request, a, "admin_create", "admin", n.id, uname)
        db.commit()
        return admin_out(n)

    @r.post("/v1/admin/admins/{aid}/disable")
    def admin_disable(aid: str, request: Request, on: bool = True, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        t = db.get(Admin, aid)
        if t is None:
            raise HTTPException(404, "Yönetici yok")
        if t.id == a.id:
            raise HTTPException(400, "Kendi hesabınızı kapatamazsınız")
        t.disabled = bool(on)
        t.token_version = (t.token_version or 0) + 1
        audit(db, request, a, "admin_disable" if on else "admin_enable", "admin", t.id, t.username)
        db.commit()
        return admin_out(t)

    # ------------------------------------------------------------------ özet
    @r.get("/v1/admin/stats")
    def stats(a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        t = now()
        by_role = dict(db.query(User.active_role, func.count(User.id)).group_by(User.active_role).all())
        return {
            "users": db.query(func.count(User.id)).scalar() or 0,
            "byRole": {k: by_role.get(k, 0) for k in ROLES},
            "banned": db.query(func.count(User.id)).filter(User.banned.is_(True)).scalar() or 0,
            "newUsers7d": db.query(func.count(User.id)).filter(User.created_at >= t - timedelta(days=7)).scalar() or 0,
            "logins24h": db.query(func.count(func.distinct(Event.user_id))).filter(Event.kind == "user.login", Event.created_at >= t - timedelta(hours=24)).scalar() or 0,
            "openJobs": db.query(func.count(Job.id)).filter(Job.status == "open").scalar() or 0,
            "activeHandoffs": db.query(func.count(Handoff.id)).filter(Handoff.closed_at.is_(None)).scalar() or 0,
            "doorPoints": db.query(func.count(DoorPoint.id)).scalar() or 0,
            "reviews": db.query(func.count(Review.id)).scalar() or 0,
            "reviewsHidden": db.query(func.count(Review.id)).filter(Review.hidden.is_(True)).scalar() or 0,
            "admins": db.query(func.count(Admin.id)).scalar() or 0,
        }

    @r.get("/v1/admin/errors")
    def errors(days: float = 7, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        return errors_summary(db, days, keep_days_errors)

    # ------------------------------------------------------------------ üyeler
    def user_row(u: User) -> dict:
        return {
            "id": u.id, "name": u.name, "phone": u.phone, "username": u.username, "email": u.email, "role": u.active_role,
            "banned": bool(u.banned), "bannedReason": u.banned_reason or "", "bannedAt": iso(u.banned_at),
            "mustChangePassword": bool(u.must_change_password), "createdAt": iso(u.created_at), "lastLoginAt": iso(u.last_login_at),
        }

    def get_user(db: Session, uid_: str) -> User:
        u = db.get(User, uid_)
        if u is None:
            raise HTTPException(404, "Üye bulunamadı")
        return u

    @r.get("/v1/admin/users")
    def users(
        q: str = Query(default="", max_length=80), role: str = "", banned: str = "", sort: str = "new",
        offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200),
        a: Admin = Depends(current_admin), db: Session = Depends(get_db),
    ):
        qs = db.query(User)
        q = (q or "").strip()
        if q:
            digits = re.sub(r"\D", "", q)
            conds = [func.lower(User.name).like(like(q.lower()), escape="\\"), func.lower(User.username).like(like(q.lower()), escape="\\"),
                     func.lower(User.email).like(like(q.lower()), escape="\\")]
            if len(digits) >= 4:
                conds.append(User.phone.like(like(digits), escape="\\"))
            if re.fullmatch(r"[0-9a-f]{6,32}", q.lower()):
                conds.append(User.id.like(q.lower() + "%"))
            qs = qs.filter(or_(*conds))
        if role in ROLES:
            qs = qs.filter(User.active_role == role)
        if banned == "1":
            qs = qs.filter(User.banned.is_(True))
        elif banned == "0":
            qs = qs.filter(User.banned.is_not(True))
        total = qs.count()
        order = {"new": User.created_at.desc(), "old": User.created_at.asc(), "login": User.last_login_at.desc(), "name": func.lower(User.name).asc()}.get(sort, User.created_at.desc())
        rows = qs.order_by(order).offset(offset).limit(limit).all()
        return {"total": total, "items": [user_row(u) for u in rows]}

    @r.get("/v1/admin/users/{uid_}")
    def user_detail(uid_: str, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        u = get_user(db, uid_)
        last = db.query(ConsentLog).filter(ConsentLog.user_id == u.id).order_by(ConsentLog.created_at.desc()).first()
        consent = None
        if last is not None:
            consent = {
                "version": last.version, "voice": bool(last.voice), "ocr": bool(last.ocr), "grupKonum": bool(last.grup_konum),
                "kapi": bool(last.kapi), "arac": bool(last.arac), "sozlesmeV": last.sozlesme_version or 0, "recordedAt": iso(last.created_at),
            }
        jobs = db.query(Job).filter(Job.employer_id == u.id).order_by(Job.created_at.desc()).limit(10).all()
        devs = db.query(Device).filter(Device.user_id == u.id).order_by(Device.last_seen.desc()).limit(10).all()
        evs = db.query(Event).filter(Event.user_id == u.id).order_by(Event.created_at.desc()).limit(30).all()
        got = db.query(Review).filter(Review.subject_id == u.id).order_by(Review.created_at.desc()).limit(30).all()
        wrote = db.query(Review).filter(Review.author_id == u.id).order_by(Review.created_at.desc()).limit(30).all()
        d = user_row(u)
        d.update({
            "lat": u.lat, "lon": u.lon,
            "consent": consent,
            "consentCount": db.query(func.count(ConsentLog.id)).filter(ConsentLog.user_id == u.id).scalar() or 0,
            "jobs": [{"id": j.id, "title": j.title, "status": j.status, "count": j.package_count or 0, "at": iso(j.created_at)} for j in jobs],
            "devices": [{"id": x.id[:8], "platform": x.platform, "lastSeen": iso(x.last_seen)} for x in devs],
            "handoffs": db.query(func.count(Handoff.id)).filter(Handoff.courier_id == u.id).scalar() or 0,
            "events": [event_row(e, {u.id: u.name}) for e in evs],
            "rating": {"courier": rv.courier_summary(db, u.id), "employer": rv.employer_reps(db, [u.id])[u.id]},
            "reviewsReceived": [review_row(x, db) for x in got],
            "reviewsWritten": [review_row(x, db) for x in wrote],
        })
        return d

    @r.patch("/v1/admin/users/{uid_}")
    def user_edit(uid_: str, b: UserEditIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Üyenin hesap bilgilerini düzenler (ad, telefon, kullanıcı adı, e-posta, hesap türü). Gönderilmeyen alana dokunulmaz."""
        u = get_user(db, uid_)
        changed: list[str] = []
        if b.name is not None:
            name = b.name.strip()
            if not name or len(name) > 120:
                raise HTTPException(400, "Ad 1–120 karakter olmalı")
            if name != u.name:
                u.name = name
                changed.append("ad")
        if b.phone is not None:
            ph = norm_phone(b.phone)
            if ph != u.phone:
                if db.query(User).filter(User.phone == ph, User.id != u.id).first():
                    raise HTTPException(400, "Bu telefon başka bir hesapta kayıtlı")
                changed.append(f"telefon {mask_phone(u.phone)}→{mask_phone(ph)}")
                u.phone = ph
        if b.username is not None:
            un = norm_username(b.username)
            if (un or None) != u.username:
                if un and db.query(User).filter(User.username == un, User.id != u.id).first():
                    raise HTTPException(400, "Bu kullanıcı adı alınmış")
                u.username = un or None
                changed.append("kullanıcı adı")
        if b.email is not None:
            em = norm_email(b.email)
            if (em or None) != u.email:
                changed.append(f"e-posta {mask_email(u.email)}→{mask_email(em)}")
                u.email = em or None
        if b.role is not None:
            if b.role not in ROLES:
                raise HTTPException(400, "Hesap türü kurye, işveren veya sponsor olmalı")
            if b.role != u.active_role:
                changed.append(f"tür {u.active_role}→{b.role}")
                u.active_role = b.role
        if changed:
            audit(db, request, a, "user_edit", "user", u.id, "; ".join(changed))
            events.log(db, "user.edited", u.id, by=a.username)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(400, "Kaydedilemedi: telefon ya da kullanıcı adı başka hesapta kayıtlı olabilir")
        return user_row(u)

    @r.post("/v1/admin/users/{uid_}/ban")
    def user_ban(uid_: str, b: BanIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Üyeyi uygulamadan men eder: giriş yapamaz, açık oturumları kapanır, açık ilanları kaldırılır. Neden kullanıcıya gösterilir."""
        u = get_user(db, uid_)
        reason = (b.reason or "").strip()
        if not reason:
            raise HTTPException(400, "Men nedenini yazın (kullanıcıya gösterilir)")
        u.banned, u.banned_reason, u.banned_at = True, reason[:300], now()
        u.token_version = (u.token_version or 0) + 1
        closed = db.query(Job).filter(Job.employer_id == u.id, Job.status.in_(("open", "applied"))).update({"status": "closed"}, synchronize_session=False)
        audit(db, request, a, "user_ban", "user", u.id, f"{reason[:200]} · kapanan ilan: {closed}")
        events.log(db, "user.banned", u.id, by=a.username, reason=reason[:100])
        db.commit()
        return user_row(u)

    @r.post("/v1/admin/users/{uid_}/unban")
    def user_unban(uid_: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        u = get_user(db, uid_)
        u.banned, u.banned_reason, u.banned_at = False, None, None
        audit(db, request, a, "user_unban", "user", u.id)
        events.log(db, "user.unbanned", u.id, by=a.username)
        db.commit()
        return user_row(u)

    @r.post("/v1/admin/users/{uid_}/password")
    def user_reset_password(uid_: str, b: ResetIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Üyeye sıfırdan şifre verir. Şifre boş bırakılırsa güçlü rastgele bir geçici şifre üretilir ve YALNIZ BU YANITTA gösterilir
        (sunucuda yalnız özeti durur). Üye bununla girince yeni şifre belirlemeye zorlanır; eski oturumları kapanır."""
        u = get_user(db, uid_)
        generated = b.password is None or not b.password.strip()
        if generated:
            temp = "".join(secrets.choice(TEMP_ALPHABET) for _ in range(10))
        else:
            temp = b.password
            if len(temp) < 6 or len(temp) > 128:
                raise HTTPException(400, "Şifre 6–128 karakter olmalı")
        u.password_hash = hash_pw(temp)
        u.must_change_password = True
        u.token_version = (u.token_version or 0) + 1
        audit(db, request, a, "user_password", "user", u.id, "geçici şifre üretildi" if generated else "şifre belirlendi")
        events.log(db, "user.reset", u.id, by=a.username)
        db.commit()
        return {"ok": True, "temporaryPassword": temp if generated else None}

    @r.post("/v1/admin/users/{uid_}/logout")
    def user_logout(uid_: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        u = get_user(db, uid_)
        u.token_version = (u.token_version or 0) + 1
        audit(db, request, a, "user_logout", "user", u.id)
        db.commit()
        return {"ok": True}

    # ------------------------------------------------------------------ etkinlik akışı
    def names_of(db: Session, ids: set[str]) -> dict[str, str]:
        ids = {i for i in ids if i}
        if not ids:
            return {}
        return {i: n for i, n in db.query(User.id, User.name).filter(User.id.in_(ids)).all()}

    def event_row(e: Event, names: dict[str, str]) -> dict:
        try:
            meta = json.loads(e.meta) if e.meta else {}
        except Exception:
            meta = {}
        return {
            "id": e.id, "at": iso(e.created_at), "kind": e.kind, "label": events.KINDS.get(e.kind, e.kind), "userId": e.user_id,
            "userName": names.get(e.user_id or "", ""), "target": e.target_id, "meta": meta,
        }

    def parse_before(before: str):
        if not before:
            return None
        try:
            # adres satırında kodlanmamış "+" boşluğa dönüşür: geri çevrilir
            return datetime.fromisoformat(before.strip().replace(" ", "+").replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(400, "Tarih biçimi geçersiz")

    @r.get("/v1/admin/events")
    def event_list(
        kind: str = "", user_id: str = "", before: str = "", limit: int = Query(default=100, ge=1, le=300),
        a: Admin = Depends(current_admin), db: Session = Depends(get_db),
    ):
        qs = db.query(Event)
        if kind:
            qs = qs.filter(Event.kind == kind)
        if user_id:
            qs = qs.filter(Event.user_id == user_id)
        b = parse_before(before)
        if b is not None:
            qs = qs.filter(Event.created_at < b)
        rows = qs.order_by(Event.created_at.desc()).limit(limit).all()
        names = names_of(db, {e.user_id for e in rows if e.user_id})
        return {"items": [event_row(e, names) for e in rows], "kinds": events.KINDS}

    # ------------------------------------------------------------------ değerlendirmeler
    def review_row(x: Review, db: Session, names: dict[str, str] | None = None) -> dict:
        if names is None:
            names = names_of(db, {x.subject_id, x.author_id or ""})
        ans = rv.answers_of(x)
        return {
            "id": x.id, "at": iso(x.created_at), "source": x.source, "stars": x.stars, "appStars": x.app_stars,
            "tags": [rv.TAG_LABELS.get(t, t) for t in (ans.get("tags") or [])], "addressOk": ans.get("addressOk"),
            "onTime": ans.get("onTime"), "complete": ans.get("complete"), "comment": x.comment or "", "weight": x.weight,
            "hidden": bool(x.hidden), "hiddenNote": x.hidden_note or "",
            "subject": {"id": x.subject_id, "name": names.get(x.subject_id, ""), "role": x.subject_role},
            "author": {"id": x.author_id, "name": names.get(x.author_id, "")} if x.author_id else None,
        }

    @r.get("/v1/admin/reviews")
    def review_list(
        source: str = "", stars: int = Query(default=0, ge=0, le=5), hidden: str = "", q: str = Query(default="", max_length=80),
        subject: str = "", author: str = "", before: str = "", limit: int = Query(default=60, ge=1, le=200),
        a: Admin = Depends(current_admin), db: Session = Depends(get_db),
    ):
        qs = db.query(Review)
        if source in ("recipient", "employer", "courier"):
            qs = qs.filter(Review.source == source)
        if stars:
            qs = qs.filter(Review.stars == stars)
        if hidden == "1":
            qs = qs.filter(Review.hidden.is_(True))
        elif hidden == "0":
            qs = qs.filter(Review.hidden.is_not(True))
        if (q or "").strip():
            qs = qs.filter(func.lower(Review.comment).like(like(q.strip().lower()), escape="\\"))
        if subject:
            qs = qs.filter(Review.subject_id == subject)
        if author:
            qs = qs.filter(Review.author_id == author)
        b = parse_before(before)
        if b is not None:
            qs = qs.filter(Review.created_at < b)
        rows = qs.order_by(Review.created_at.desc()).limit(limit).all()
        names = names_of(db, {x.subject_id for x in rows} | {x.author_id for x in rows if x.author_id})
        return {"items": [review_row(x, db, names) for x in rows]}

    @r.post("/v1/admin/reviews/{rid}/hide")
    def review_hide(rid: str, b: HideIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        x = db.get(Review, rid)
        if x is None:
            raise HTTPException(404, "Değerlendirme yok")
        x.hidden, x.hidden_note = bool(b.hidden), (b.note or "")[:200] if b.hidden else ""
        audit(db, request, a, "review_hide" if b.hidden else "review_unhide", "review", x.id, (b.note or "")[:200])
        if b.hidden:
            events.log(db, "review.hidden", x.subject_id, x.id, by=a.username)
        db.commit()
        return review_row(x, db)

    @r.delete("/v1/admin/reviews/{rid}")
    def review_delete(rid: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        x = db.get(Review, rid)
        if x is None:
            return {"ok": True}
        audit(db, request, a, "review_delete", "review", x.id, f"{x.source} · {x.stars}★")
        db.delete(x)
        db.commit()
        return {"ok": True}

    # ------------------------------------------------------------------ takipler (son 48 saat: iz + son ilerleme)
    def follow_row(f: Follow, names: dict[str, str]) -> dict:
        try:
            pr = json.loads(f.progress) if f.progress else {}
        except Exception:
            pr = {}
        pr = pr if isinstance(pr, dict) else {}
        return {
            "id": f.id, "status": f.status, "endReason": f.end_reason or "",
            "employer": {"id": f.employer_id, "name": names.get(f.employer_id, "")},
            "courier": {"id": f.courier_id, "name": names.get(f.courier_id, "")},
            "createdAt": iso(f.created_at), "acceptedAt": iso(f.accepted_at), "endedAt": iso(f.ended_at),
            "done": len(pr.get("done") or []), "failed": len(pr.get("failed") or []), "total": int(pr.get("total") or 0),
            "trailN": int(f.trail_n or 0),
        }

    @r.get("/v1/admin/follows")
    def follow_list(
        status: str = "", user_id: str = "", before: str = "", limit: int = Query(default=60, ge=1, le=200),
        a: Admin = Depends(current_admin), db: Session = Depends(get_db),
    ):
        """Takip kayıtları (bekleyen, aktif ve bitişinden sonra 48 saat içindekiler). Listede konum yoktur."""
        qs = db.query(Follow)
        if status in ("asked", "active", "ended"):
            qs = qs.filter(Follow.status == status)
        if user_id:
            qs = qs.filter(or_(Follow.employer_id == user_id, Follow.courier_id == user_id))
        b = parse_before(before)
        if b is not None:
            qs = qs.filter(Follow.created_at < b)
        rows = qs.order_by(Follow.created_at.desc()).limit(limit).all()
        names = names_of(db, {x for f in rows for x in (f.employer_id, f.courier_id)})
        return {"items": [follow_row(f, names) for f in rows], "keepHours": follow_keep_hours}

    @r.get("/v1/admin/follows/{fid}")
    def follow_detail(fid: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Bir takibin ayrıntısı: kimler, ne zaman, hangi durak ne zaman bitti ve konum izi. Konum izini açmak işlem günlüğüne yazılır."""
        f = db.get(Follow, fid)
        if f is None:
            raise HTTPException(404, "Takip kaydı yok (biten kayıtlar 48 saat sonra silinir)")
        names = names_of(db, {f.employer_id, f.courier_id})
        try:
            pr = json.loads(f.progress) if f.progress else {}
        except Exception:
            pr = {}
        pr = pr if isinstance(pr, dict) else {}
        t = pr.get("t") if isinstance(pr.get("t"), dict) else {}
        lab = pr.get("a") if isinstance(pr.get("a"), dict) else {}
        def stops(key: str) -> list[dict]:
            return sorted(({"id": i, "at": int(t.get(i) or 0), "label": str(lab.get(i) or "")} for i in (pr.get(key) or [])), key=lambda x: x["at"])
        pts = trail_of(db, f.id)
        audit(db, request, a, "follow_view", "follow", f.id, f"{len(pts)} iz noktası")
        db.commit()
        return {**follow_row(f, names), "doneStops": stops("done"), "failedStops": stops("failed"), "points": pts}

    # ------------------------------------------------------------------ işlem günlüğü
    @r.get("/v1/admin/audit")
    def audit_list(before: str = "", limit: int = Query(default=100, ge=1, le=300), a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        qs = db.query(AdminAudit)
        b = parse_before(before)
        if b is not None:
            qs = qs.filter(AdminAudit.created_at < b)
        rows = qs.order_by(AdminAudit.created_at.desc()).limit(limit).all()
        return {"items": [
            {"id": x.id, "at": iso(x.created_at), "admin": x.admin_name, "action": x.action, "targetType": x.target_type, "target": x.target_id, "detail": x.detail, "ip": x.ip}
            for x in rows
        ]}

    def purge(db: Session, event_days: float, audit_days: float, review_days: float, event_max: int) -> dict:
        """Süresi dolan kayıtları siler (saatte bir arka planda çalışır)."""
        n_ev = events.purge(db, event_days, event_max)
        n_au = db.query(AdminAudit).filter(AdminAudit.created_at < now() - timedelta(days=audit_days)).delete(synchronize_session=False)
        n_rv = db.query(Review).filter(Review.created_at < now() - timedelta(days=review_days)).delete(synchronize_session=False)
        db.commit()
        return {"events": n_ev, "audit": n_au, "reviews": n_rv}

    r.purge = purge  # type: ignore[attr-defined]
    r.limiters = (login_ip, login_user, bad_keys, pw_fail)  # type: ignore[attr-defined]
    return r
