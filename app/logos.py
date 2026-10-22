"""
İşletme logosu (1.14.1; kullanıcı kararı 22 Ekim 2026):
  - Profil fotoğrafı YOK: kişiler avatar seçer (hazır kütüphane). Uygulamada başka resim yükleme yeri yok.
  - Yalnız İŞLETME (işveren / sponsor modu) logo yükleyebilir. Logo kuryelere ilan kartında görünür.
  - Logo dışında resim (kişi fotoğrafı vb.) yüklenirse YÖNETİM SİLER: uygulamada yükleme anında bu uyarı yazar.

Güvenlik: yalnız JPEG / PNG / WEBP (dosyanın ilk baytlarına bakılır; SVG yok), en çok LOGO_MAX_KB. Logo herkese açık bir işarettir
(ilan kartında oturumsuz <img> ile açılabilsin diye `GET /v1/logo/{id}` oturum istemez); askıdaki hesabın logosu verilmez.
"""
import base64
import binascii
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import events
from .db import get_db
from .models import User, now

LOGO_MAX_KB = int(os.environ.get("LOGO_MAX_KB", "150"))
LOGO_ROLES = ("employer", "sponsor")
_DATA_RE = re.compile(r"^data:image/(?:jpeg|jpg|png|webp);base64,", re.IGNORECASE)


def sniff(raw: bytes) -> str:
    """Dosyanın gerçek türü (uzantıya / bildirilen türe güvenilmez). Bilinmeyen: ''."""
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""


def decode_logo(s: str) -> tuple[bytes, str]:
    s = (s or "").strip()
    s = _DATA_RE.sub("", s, count=1)
    if len(s) > LOGO_MAX_KB * 1024 * 4 // 3 + 8:
        raise HTTPException(413, f"Logo çok büyük (en çok {LOGO_MAX_KB} KB)")
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Logo okunamadı")
    if not raw:
        raise HTTPException(400, "Logo boş")
    if len(raw) > LOGO_MAX_KB * 1024:
        raise HTTPException(413, f"Logo çok büyük (en çok {LOGO_MAX_KB} KB)")
    kind = sniff(raw)
    if not kind:
        raise HTTPException(415, "Logo JPEG, PNG ya da WEBP olmalı")
    return raw, kind


def logo_version(u: User | None) -> str:
    """İlan kartı / hesap çıktısı için: logo varsa sürüm (değişince önbellek yenilenir), yoksa ''."""
    if u is None or not u.logo or u.banned:
        return ""
    return str(int(u.logo_at.timestamp())) if u.logo_at else "1"


class LogoIn(BaseModel):
    # data:image/...;base64,... ya da yalnız base64. Boş = logoyu kaldır.
    logo: str = Field(default="", max_length=LOGO_MAX_KB * 1024 * 2)


def make_router(current_user) -> APIRouter:
    r = APIRouter()

    @r.post("/v1/me/logo")
    def set_logo(body: LogoIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """İşletme logosu yükle / kaldır. Yalnız işveren ve sponsor modunda. Logo dışı resim yönetimce silinir (uygulamada uyarı)."""
        if not body.logo.strip():
            user.logo, user.logo_type, user.logo_at = None, None, None
            events.log(db, "user.logo", user.id, on=False)
            db.commit()
            return {"logo": ""}
        if user.active_role not in LOGO_ROLES:
            raise HTTPException(403, "Logo yalnız işletme hesabında yüklenir (kişiler avatar seçer)")
        raw, kind = decode_logo(body.logo)
        user.logo = base64.b64encode(raw).decode("ascii")
        user.logo_type = kind
        user.logo_at = now()
        events.log(db, "user.logo", user.id, on=True, kb=round(len(raw) / 1024))
        db.commit()
        return {"logo": logo_version(user)}

    @r.get("/v1/logo/{uid_}")
    def get_logo(uid_: str, db: Session = Depends(get_db)):
        u = db.get(User, uid_) if re.fullmatch(r"[0-9a-zA-Z_-]{1,32}", uid_ or "") else None
        if u is None or not u.logo or u.banned:
            raise HTTPException(404, "Logo yok")
        try:
            raw = base64.b64decode(u.logo)
        except (binascii.Error, ValueError):
            raise HTTPException(404, "Logo yok")
        return Response(
            content=raw,
            media_type=sniff(raw) or "application/octet-stream",
            headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"},
        )

    return r
