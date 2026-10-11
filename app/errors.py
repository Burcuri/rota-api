"""Hata takibi: uygulamanın çöktüğü / hata verdiği yerleri sizin görebileceğiniz biçimde toplar. Üçüncü taraf hesabı gerekmez.

- Uygulama hata raporunu POST /v1/errors ile gönderir (giriş şart değil: çökme girişten önce de olabilir).
- Kişisel veri sunucuda bir kez daha ayıklanır: e-posta, telefon benzeri uzun rakamlar, oturum / bağlantı kodları, adres parametreleri.
- Hesap kimliği tutulmaz; yalnız kuruluma özel rastgele kodun özeti (kaç farklı telefonun etkilendiğini saymak için).
- Saklama: ERROR_KEEP_DAYS (14) gün, en çok ERROR_MAX_ROWS (5000) satır. Günlük ve IP başı sınır var (kötüye kullanım).
- Görüntüleme: yönetim sayfasının (/yonetim) "Hatalar" sekmesi; yalnız giriş yapmış yönetici görür (admin.py).
"""
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import SECRET
from .db import get_db
from .limits import Window, client_ip
from .models import ClientError, now

KINDS = {"js", "promise", "render", "api"}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*")
_BEARER = re.compile(r"(?i)bearer\s+[\w.~+/=-]{8,}")
_QUERY = re.compile(r"\?[^\s)\"']*")
_HANDOFF_PATH = re.compile(r"(/a/)[A-Za-z0-9_-]{8,}")
_LONG_DIGITS = re.compile(r"(?<![\w.])\+?\d[\d \-()]{5,}\d(?![\w.])")
_LONG_CODE = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")
_NUMS = re.compile(r"\d+")


def scrub(text: str, limit: int) -> str:
    """Kişisel veri ve gizli kod benzeri parçaları ayıklar, uzunluğu sınırlar."""
    t = str(text or "")
    t = _JWT.sub("<oturum>", t)
    t = _BEARER.sub("Bearer <oturum>", t)
    t = _EMAIL.sub("<e-posta>", t)
    t = _HANDOFF_PATH.sub(r"\1<kod>", t)
    t = _QUERY.sub("", t)  # adres satırındaki ?parametreler (anahtar, adres, konum…) atılır
    t = _LONG_DIGITS.sub("<no>", t)
    t = _LONG_CODE.sub("<kod>", t)
    return t[:limit]


def fingerprint(kind: str, message: str, stack: str) -> str:
    """Aynı hatayı gruplamak için: sayılar silinir ("satır 12", "id 99" fark yaratmasın) + yığının ilk anlamlı satırı."""
    first = next((ln.strip() for ln in (stack or "").splitlines() if ln.strip()), "")
    base = f"{kind}|{_NUMS.sub('#', message)[:160]}|{_NUMS.sub('#', first)[:160]}"
    return hashlib.sha1(base.encode("utf-8", "ignore")).hexdigest()[:20]


class ErrorIn(BaseModel):
    message: str = Field(default="", max_length=20000)
    stack: str = Field(default="", max_length=100000)
    kind: str = Field(default="js", max_length=12)
    fatal: bool = False
    screen: str = Field(default="", max_length=200)
    platform: str = Field(default="", max_length=16)
    os_version: str = Field(default="", max_length=40, alias="osVersion")
    app_version: str = Field(default="", max_length=40, alias="appVersion")
    install: str = Field(default="", max_length=80)

    model_config = {"populate_by_name": True}


def summarize(db: Session, days: float, keep_days: float = 14) -> dict:
    """Son `days` günün hata raporları, aynı hatalar gruplanmış (kaç kez, kaç farklı telefon, ilk / son görülme)."""
    since = now() - timedelta(days=max(0.1, min(days, keep_days)))
    rows = db.query(ClientError).filter(ClientError.created_at >= since).order_by(ClientError.created_at.desc()).limit(3000).all()
    groups: dict[str, dict] = {}
    for e in rows:
        g = groups.get(e.fingerprint)
        ts = e.created_at if e.created_at.tzinfo else e.created_at.replace(tzinfo=timezone.utc)
        if g is None:
            groups[e.fingerprint] = g = {
                "fingerprint": e.fingerprint, "kind": e.kind, "message": e.message, "stack": e.stack, "screen": e.screen,
                "platform": e.platform, "osVersion": e.os_version, "appVersion": e.app_version, "fatal": bool(e.fatal),
                "count": 0, "installs": set(), "lastSeen": ts, "firstSeen": ts,
            }
        g["count"] += 1
        g["fatal"] = g["fatal"] or bool(e.fatal)
        if e.install:
            g["installs"].add(e.install)
        if ts < g["firstSeen"]:
            g["firstSeen"] = ts
        if ts > g["lastSeen"]:
            g["lastSeen"] = ts
            g["screen"], g["appVersion"], g["platform"] = e.screen, e.app_version, e.platform
    out = sorted(groups.values(), key=lambda g: g["lastSeen"], reverse=True)
    for g in out:
        g["installs"] = len(g["installs"])
        g["lastSeen"] = g["lastSeen"].isoformat()
        g["firstSeen"] = g["firstSeen"].isoformat()
    return {"days": days, "errors": out, "errorRows": db.query(func.count(ClientError.id)).scalar() or 0}


def make_router(keep_days: float = 14, max_rows: int = 5000, daily_max: int = 3000) -> APIRouter:
    r = APIRouter()
    per_ip = Window(40, 3600)  # IP başı saatte 40 rapor
    day = {"d": "", "n": 0}

    def purge(db: Session) -> int:
        cut = now() - timedelta(days=keep_days)
        n = db.query(ClientError).filter(ClientError.created_at < cut).delete(synchronize_session=False)
        total = db.query(func.count(ClientError.id)).scalar() or 0
        if total > max_rows:  # en eskileri at
            ids = [i for (i,) in db.query(ClientError.id).order_by(ClientError.created_at.asc()).limit(total - max_rows).all()]
            if ids:
                n += db.query(ClientError).filter(ClientError.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        return n

    @r.post("/v1/errors")
    def report(b: ErrorIn, request: Request, db: Session = Depends(get_db)):
        if not per_ip.allow(client_ip(request)):
            raise HTTPException(429, "Çok fazla rapor")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day["d"] != today:
            day["d"], day["n"] = today, 0
        if day["n"] >= daily_max:
            raise HTTPException(429, "Günlük rapor sınırı doldu")
        day["n"] += 1
        kind = b.kind if b.kind in KINDS else "js"
        msg = scrub(b.message, 300)
        stack = scrub(b.stack, 1500)
        if not msg and not stack:
            return {"ok": True}
        inst = hmac.new(SECRET.encode(), ("install:" + b.install).encode(), hashlib.sha256).hexdigest()[:16] if b.install else ""
        db.add(ClientError(
            fingerprint=fingerprint(kind, msg, stack), kind=kind, fatal=bool(b.fatal), message=msg, stack=stack,
            screen=scrub(b.screen, 60), platform=scrub(b.platform, 12), os_version=scrub(b.os_version, 20),
            app_version=scrub(b.app_version, 20), install=inst,
        ))
        db.commit()
        return {"ok": True}

    r.purge = purge  # type: ignore[attr-defined]
    r.limiters = (per_ip,)  # type: ignore[attr-defined]
    r.state = day  # type: ignore[attr-defined]
    return r
