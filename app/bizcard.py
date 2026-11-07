"""1.24.0 — İŞLETME KARTI. Kullanıcı kararı (6 Kasım 2026): "tabiki işletme konumunu asla saklayamaz bir anlamı yok; kuryeyle yazışmaya
başlar başlamaz kurye işletmenin bilgilerini, profilinde yazanları fotoğrafa tıklayarak görebilmeli."

GET /v1/businesses/{id}: işletme profilinde yazanlar (ad, kategori, çalışma saatleri, tabela adı, telefon, e-posta, işletme konumu) + logo
sürümü + itibar + kullanıcı no. Kim bakar: kurye hesapları ve işletmenin kendisi (önizleme).
- Vergi dairesi ve vergi / TC no GÖSTERİLMEZ (şahıs işletmesinde TC kimlik no olabilir: kişisel veri).
- Telefon, işletme "Telefonumu mesajlaştığım kişiler görebilsin" ayarını kapattıysa gösterilmez (varsayılan açık).
- Konum: işletme profilindeki konum; işaretlenmemişse son ilanın başlama noktası. İşletme konumu gizlenemez.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .db import get_db
from .logos import logo_version
from .models import AccountDoc, Job, User
from .reviews import employer_reps


def _doc(raw: str | None) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _txt(v: object, n: int) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def _coord(v: object, lim: float) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0 and abs(v) <= lim else None


def business_card(db: Session, u: User) -> dict:
    docs = {d.section: _doc(d.data) for d in db.query(AccountDoc).filter(AccountDoc.user_id == u.id, AccountDoc.section.in_(("business", "privacy"))).all()}
    b, pv = docs.get("business", {}), docs.get("privacy", {})
    lat, lon, src = _coord(b.get("lat"), 90), _coord(b.get("lon"), 180), "profil"
    if lat is None or lon is None:
        j = (
            db.query(Job)
            .filter(Job.employer_id == u.id, Job.employer_lat.isnot(None), Job.employer_lat != 0)
            .order_by(Job.created_at.desc())
            .first()
        )
        lat, lon, src = (j.employer_lat, j.employer_lon, "ilan") if j else (None, None, "")
    return {
        "id": u.id,
        "no": f"ISV-{u.public_no}" if u.public_no else "",
        "name": _txt(b.get("name"), 120) or u.name or "",
        "category": _txt(b.get("category"), 60),
        "hours": _txt(b.get("hours"), 60),
        "signboard": _txt(b.get("signboard"), 80),
        "phone": _txt(b.get("phone"), 30) if pv.get("showPhone", True) is not False else "",
        "email": _txt(b.get("email"), 120),
        "lat": lat,
        "lon": lon,
        "locFrom": src if lat is not None else "",
        "logo": logo_version(u),
        "rep": employer_reps(db, [u.id]).get(u.id),
    }


def make_router(current_user) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/businesses/{uid}")
    def card(uid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.id != uid and user.active_role != "courier":
            raise HTTPException(403, "İşletme kartını kuryeler görür")
        u = db.get(User, uid)
        if not u or u.deleted_at is not None or u.banned:
            raise HTTPException(404, "İşletme bulunamadı")
        if u.active_role != "employer":
            raise HTTPException(404, "Bu hesap bir işletme değil")
        return business_card(db, u)

    return r
