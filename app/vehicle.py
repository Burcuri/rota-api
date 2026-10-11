"""Kurye araç profili: kurye; araç tipini, taşıma kapasitesini, çalışma bölgelerini ve müsaitliğini işletmelere gösterebilir.

Kurallar (sunucu uygular; hepsi testli):
- AYRI AÇIK RIZA şart: profil yalnız kuryenin son rıza satırında `arac` açıkken yazılır ve okunur. Rıza geri çekilince
  (POST /v1/me/consent, arac=false) profil SİLİNİR. Rıza kaydı: metin sürümü + SUNUCU saati (consent_log; satırlar silinmez).
- Plaka ve araç fotoğrafı sunucuya HİÇ gelmez (böyle bir alan yok): telefonda kalır.
- Yalnız işletme hesapları okur; kurye, sponsor ve ziyaretçi göremez. Okuma hız sınırlıdır (toplu tarama önlenir).
- Men yemiş, kurye modunda olmayan ya da rıza vermemiş kurye için aynı cevap döner ("paylaşmıyor", 404): sebep ayrıştırılmaz.
- Serbest metin alanları (kapasite, bölge adları) küfür ve kişisel veri (telefon, IBAN, kimlik no) süzgecinden geçer.
"""
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import events
from .auth import current_user
from .db import get_db
from .limits import Window
from .models import ConsentLog, CourierProfile, User
from .moderation import comment_error

VEHICLES = ("motor", "araba", "bisiklet", "yaya")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_AREAS = 8
MAX_TEXT = 40


class VehicleIn(BaseModel):
    vehicle: str = Field(max_length=12)
    capacity: str = Field(default="", max_length=200)  # asıl sınır aşağıda (Türkçe açıklamalı hata için)
    areas: list[str] = Field(default_factory=list, max_length=30)
    available: bool = True
    from_: str = Field(default="09:00", alias="from", max_length=5)
    to: str = Field(default="22:00", max_length=5)

    model_config = {"populate_by_name": True}


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _text(label: str, s: str) -> str:
    t = _squash(s)
    if len(t) > MAX_TEXT:
        raise HTTPException(400, f"{label} en fazla {MAX_TEXT} karakter olabilir")
    err = comment_error(t, MAX_TEXT)
    if err:
        raise HTTPException(400, err)
    return t


def clean(b: VehicleIn) -> dict:
    if b.vehicle not in VEHICLES:
        raise HTTPException(400, "Araç tipi geçersiz")
    cap = _text("Kapasite", b.capacity)
    areas: list[str] = []
    seen: set[str] = set()
    for a in b.areas:
        t = _text("Bölge adı", a)
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            areas.append(t)
    if len(areas) > MAX_AREAS:
        raise HTTPException(400, f"En çok {MAX_AREAS} çalışma bölgesi eklenebilir")
    if not (HHMM.match(b.from_) and HHMM.match(b.to)):
        raise HTTPException(400, "Çalışma saati SS:DD biçiminde olmalı (ör. 09:00)")
    return {"vehicle": b.vehicle, "capacity": cap, "areas": areas, "available": bool(b.available), "from": b.from_, "to": b.to}


def consent_on(db: Session, user_id: str) -> bool:
    """Son rıza satırında araç profili rızası açık mı?"""
    last = db.query(ConsentLog).filter(ConsentLog.user_id == user_id).order_by(ConsentLog.created_at.desc()).first()
    return bool(last is not None and last.arac)


def withdraw(db: Session, user_id: str) -> int:
    """Rıza geri çekildi: profil silinir (commit ETMEZ; çağıranın işlemiyle birlikte). Silinen satır sayısını döner."""
    n = db.query(CourierProfile).filter(CourierProfile.user_id == user_id).delete(synchronize_session=False)
    if n:
        events.log(db, "vehicle.unshare", user_id)
    return n


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def out(p: CourierProfile) -> dict:
    try:
        areas = json.loads(p.areas or "[]")
    except ValueError:
        areas = []
    return {
        "vehicle": p.vehicle,
        "capacity": p.capacity or "",
        "areas": [str(a) for a in areas if isinstance(a, str)][:MAX_AREAS],
        "available": bool(p.available),
        "from": p.from_hhmm,
        "to": p.to_hhmm,
        "updatedAt": _iso(p.updated_at),
    }


def make_router(view_per_min: int = 90) -> APIRouter:
    r = APIRouter()
    views = Window(view_per_min, 60)
    r.limiters = [views]

    @r.get("/v1/me/vehicle")
    def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kendi profilim: rıza durumu + sunucudaki kopya (rıza yoksa kopya da yoktur)."""
        on = consent_on(db, user.id)
        p = db.get(CourierProfile, user.id) if on else None
        return {"consent": on, "profile": out(p) if p else None}

    @r.put("/v1/me/vehicle")
    def save(b: VehicleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.active_role != "courier":
            raise HTTPException(403, "Araç profilini kurye modunda düzenlersin")
        if not consent_on(db, user.id):
            raise HTTPException(403, "Araç profilini işletmelere göstermek için ayrı açık rıza vermelisin")
        d = clean(b)
        p = db.get(CourierProfile, user.id)
        first = p is None
        if first:
            p = CourierProfile(user_id=user.id)
            db.add(p)
        p.vehicle = d["vehicle"]
        p.capacity = d["capacity"]
        p.areas = json.dumps(d["areas"], ensure_ascii=False)
        p.available = d["available"]
        p.from_hhmm = d["from"]
        p.to_hhmm = d["to"]
        if first:
            events.log(db, "vehicle.share", user.id)
        db.commit()
        db.refresh(p)
        return out(p)

    @r.delete("/v1/me/vehicle")
    def drop(user: User = Depends(current_user), db: Session = Depends(get_db)):
        n = withdraw(db, user.id)
        db.commit()
        return {"ok": True, "deleted": n}

    @r.get("/v1/couriers/{cid}/vehicle")
    def view(cid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """İşletme, kuryenin (paylaşmayı seçtiği) araç profilini okur."""
        if user.active_role != "employer":
            raise HTTPException(403, "Araç profilini yalnız işletme hesapları görür")
        if not views.allow(user.id):
            raise HTTPException(429, "Çok sık bakıyorsun; biraz bekle")
        t = db.get(User, cid) if 0 < len(cid) <= 40 else None
        p = db.get(CourierProfile, cid) if t is not None else None
        if t is None or p is None or t.banned or t.active_role != "courier" or not consent_on(db, t.id):
            raise HTTPException(404, "Bu kurye araç profilini paylaşmıyor")
        return out(p)

    return r
