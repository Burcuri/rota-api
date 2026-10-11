"""Değerlendirmeler (anketler): hepsi 5 yıldız üzerinden.

Üç kaynak:
- recipient: alıcı → kurye. Hesap gerekmez; yalnız gerçek bir teslim bağlantısının kodu ile (POST /a/{kod}/review). Bir bağlantıya
  bir değerlendirme (tekrar gönderirse güncellenir). Alıcının adı / telefonu hiçbir yerde tutulmaz.
- employer: işveren → kurye (POST /v1/reviews, kind=courier). Hızlı seçimler + not.
- courier: kurye → işveren (POST /v1/reviews, kind=employer). "Parayı vaktinde / eksiksiz aldım" + not.

Kim ne görür:
- Kurye kendi ortalamasını (işveren ve alıcı ortalamasının ortalaması) ve son 7 yıldızı görür; YORUM METNİNİ görmez.
- Kuryeler işverenin ortalamasını, ödeme oranlarını ve son yıldızlarını görür; YORUM METNİNİ görmez (yorumları yalnız yönetim okur).
- Yönetim hepsini okur, uygunsuz olanı gizler (gizlenen ortalamaya girmez).
Güven: alıcı değerlendirmesinin ağırlığı, bağlantı alıcıya sunucudan ulaştıysa ve kurye görmediyse 1; kurye bağlantıyı gördüyse 0,3
(kurye kendi kendini "alıcı" gibi puanlayamasın diye). Yeni açılmış (24 saatten genç) hesabın değerlendirmesi 0,5.
"""
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import events
from .auth import current_user
from .db import get_db
from .moderation import comment_error
from .models import Handoff, Review, User, now, uid

# Hızlı seçim anahtarları (uygulama ve alıcı sayfası Türkçe karşılığını kendi gösterir)
COURIER_TAGS = ("hizli", "nazik", "sorunsuz", "saglam", "gec", "ozensiz")
TAG_LABELS = {
    "hizli": "Hızlıydı", "nazik": "Nazikti", "sorunsuz": "Sorunsuz teslim", "saglam": "Paket sağlamdı",
    "gec": "Geç kaldı", "ozensiz": "Özensizdi",
}
LOW_WEIGHT = 0.3
NEW_ACCOUNT_WEIGHT = 0.5
DAILY_MAX = 30  # hesap başı 24 saatte en çok değerlendirme


def answers_of(r: Review) -> dict:
    try:
        d = json.loads(r.answers or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _avg(pairs: list[tuple[int, float]]) -> float | None:
    tw = sum(w for _, w in pairs)
    return (sum(s * w for s, w in pairs) / tw) if tw > 0 else None


def r1(x: float | None) -> float | None:
    return None if x is None else round(x + 1e-9, 1)


def _utc(dt):
    from datetime import timezone

    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def courier_summary(db: Session, courier_id: str) -> dict:
    """Kuryenin genel başarısı (5 üzerinden): işveren ortalaması ile alıcı ortalamasının ortalaması. Yalnız bir kaynak varsa o."""
    rows = (
        db.query(Review.source, Review.stars, Review.weight)
        .filter(Review.subject_id == courier_id, Review.subject_role == "courier", Review.hidden.is_not(True))
        .order_by(Review.created_at.desc())
        .limit(2000)
        .all()
    )
    emp = _avg([(s, w or 1.0) for src, s, w in rows if src == "employer"])
    rec = _avg([(s, w or 1.0) for src, s, w in rows if src == "recipient"])
    parts = [x for x in (emp, rec) if x is not None]
    score = (sum(parts) / len(parts)) if parts else None
    return {
        "score": r1(score), "employerAvg": r1(emp), "recipientAvg": r1(rec), "count": len(rows),
        "last": [s for _, s, _ in rows[:7]],
    }


def employer_reps(db: Session, ids: list[str]) -> dict[str, dict]:
    """İşverenlerin itibarı (kuryelere görünür): ortalama yıldız, vaktinde / eksiksiz ödeme yüzdesi, değerlendirme sayısı."""
    out = {i: {"avg": None, "onTimePct": None, "completePct": None, "count": 0} for i in ids}
    if not ids:
        return out
    rows = (
        db.query(Review)
        .filter(Review.subject_id.in_(ids), Review.subject_role == "employer", Review.hidden.is_not(True))
        .order_by(Review.created_at.desc())
        .limit(6000)
        .all()
    )
    by: dict[str, list[Review]] = {}
    for rv in rows:
        by.setdefault(rv.subject_id, []).append(rv)
    for eid, lst in by.items():
        n = len(lst)
        ans = [answers_of(x) for x in lst]
        out[eid] = {
            "avg": r1(_avg([(x.stars, x.weight or 1.0) for x in lst])),
            "onTimePct": round(100 * sum(1 for a in ans if a.get("onTime")) / n),
            "completePct": round(100 * sum(1 for a in ans if a.get("complete")) / n),
            "count": n,
        }
    return out


def _check_comment(text: str):
    err = comment_error(text, 500)
    if err:
        raise HTTPException(400, err)


def _clean_tags(tags: list[str]) -> list[str]:
    return [t for t in dict.fromkeys(tags or []) if t in COURIER_TAGS]


def save_recipient_review(db: Session, h: Handoff, stars: int, app_stars: int | None, tags: list[str], address_ok: bool | None, comment: str) -> Review:
    """Alıcı değerlendirmesi (bağlantı başına bir tane; ikinci gönderim günceller). commit ETMEZ."""
    comment = (comment or "").strip()
    _check_comment(comment)
    answers = {"tags": _clean_tags(tags)}
    if address_ok is not None:
        answers["addressOk"] = bool(address_ok)
    weight = 1.0 if (h.mode in ("sms", "email") and not h.link_seen) else LOW_WEIGHT
    rv = db.query(Review).filter(Review.handoff_id == h.id).first()
    if rv is None:
        rv = Review(
            id=uid(), source="recipient", subject_id=h.courier_id, subject_role="courier", handoff_id=h.id, ref=(h.ref or "")[:64],
            stars=stars, app_stars=app_stars, answers=json.dumps(answers, ensure_ascii=False), comment=comment, weight=weight,
        )
        db.add(rv)
        events.log(db, "review.create", h.courier_id, rv.id, src="recipient", stars=stars)
    else:
        rv.stars, rv.app_stars, rv.comment, rv.weight, rv.updated_at = stars, app_stars, comment, weight, now()
        rv.answers = json.dumps(answers, ensure_ascii=False)
    return rv


class ReviewIn(BaseModel):
    kind: str = Field(max_length=10)  # puanlanan kişi: "courier" (işveren yazar) | "employer" (kurye yazar)
    subject_id: str = Field(alias="subjectId", min_length=1, max_length=40)
    ref: str = Field(default="", max_length=64)
    stars: int
    tags: list[str] = Field(default_factory=list, max_length=12)
    on_time: bool | None = Field(default=None, alias="onTime")
    complete: bool | None = None
    comment: str = Field(default="", max_length=600)

    model_config = {"populate_by_name": True}


def make_router() -> APIRouter:
    r = APIRouter()

    @r.post("/v1/reviews")
    def create(b: ReviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """İşveren kuryeyi, kurye işvereni puanlar. Aynı (kişi, ilan) için tekrar gönderilirse güncellenir."""
        if b.kind not in ("courier", "employer"):
            raise HTTPException(400, "Kimi değerlendirdiğini belirt (kurye ya da işveren)")
        need = "employer" if b.kind == "courier" else "courier"
        if user.active_role != need:
            raise HTTPException(403, "Kuryeyi işveren, işvereni kurye değerlendirir")
        if not (1 <= b.stars <= 5):
            raise HTTPException(400, "Puan 1 ile 5 arasında olmalı")
        if b.subject_id == user.id:
            raise HTTPException(400, "Kendini değerlendiremezsin")
        subject = db.get(User, b.subject_id)
        if subject is None:
            raise HTTPException(404, "Değerlendirilecek kişi bulunamadı")
        comment = (b.comment or "").strip()
        _check_comment(comment)
        since = now() - timedelta(hours=24)
        used = db.query(func.count(Review.id)).filter(Review.author_id == user.id, Review.created_at >= since).scalar() or 0
        ref = (b.ref or "").strip()[:64]
        existing = (
            db.query(Review)
            .filter(Review.author_id == user.id, Review.subject_id == subject.id, Review.ref == ref, Review.source == need)
            .order_by(Review.created_at.desc())
            .first()
        )
        rv = None
        if existing is not None:
            if ref:  # aynı ilan / durak için tekrar gönderim: öncekini günceller
                rv = existing
            elif _utc(existing.created_at) > since:
                raise HTTPException(429, "Bu kişiyi bugün zaten değerlendirdin")
        if rv is None and used >= DAILY_MAX:
            raise HTTPException(429, "Günlük değerlendirme sınırı doldu")
        answers: dict = {}
        if b.kind == "courier":
            answers["tags"] = _clean_tags(b.tags)
        else:
            answers["onTime"] = True if b.on_time is None else bool(b.on_time)
            answers["complete"] = True if b.complete is None else bool(b.complete)
        young = (now() - (_utc(user.created_at) or now())) < timedelta(hours=24)
        weight = NEW_ACCOUNT_WEIGHT if young else 1.0
        if rv is None:
            rv = Review(
                id=uid(), source=need, subject_id=subject.id, subject_role=b.kind, author_id=user.id, ref=ref, stars=b.stars,
                answers=json.dumps(answers, ensure_ascii=False), comment=comment, weight=weight,
            )
            db.add(rv)
            events.log(db, "review.create", user.id, rv.id, src=need, stars=b.stars, to=subject.id)
        else:
            rv.stars, rv.comment, rv.weight, rv.updated_at = b.stars, comment, weight, now()
            rv.answers = json.dumps(answers, ensure_ascii=False)
        db.commit()
        return {"ok": True, "id": rv.id}

    @r.get("/v1/me/rating")
    def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kendi puanın. Kurye olarak: genel başarı (5 üzerinden) ve son yıldızlar; işveren olarak: itibar. YORUM METNİ yok."""
        return {"courier": courier_summary(db, user.id), "employer": employer_reps(db, [user.id])[user.id]}

    def need_courier(user: User):
        if user.active_role != "courier":
            raise HTTPException(403, "İşveren itibarını yalnız kuryeler görür")

    @r.get("/v1/employers/reputation")
    def reps(ids: str = Query(default="", max_length=2000), user: User = Depends(current_user), db: Session = Depends(get_db)):
        need_courier(user)
        want = [x for x in dict.fromkeys(i.strip() for i in ids.split(",")) if x][:40]
        return {"items": employer_reps(db, want)}

    @r.get("/v1/employers/{eid}/reputation")
    def one(eid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """İşveren itibarı + son 20 değerlendirmenin yıldızı ve ödeme bilgisi (isimsiz). YORUM METNİ YOK: yalnız yönetim okur. Yalnız kuryelere."""
        need_courier(user)
        rep = employer_reps(db, [eid])[eid]
        rows = (
            db.query(Review)
            .filter(Review.subject_id == eid, Review.subject_role == "employer", Review.hidden.is_not(True))
            .order_by(Review.created_at.desc())
            .limit(20)
            .all()
        )
        items = []
        for x in rows:
            a = answers_of(x)
            items.append({
                "id": x.id, "stars": x.stars, "onTime": bool(a.get("onTime")), "complete": bool(a.get("complete")),
                "at": int(_utc(x.created_at).timestamp() * 1000),
            })
        return {**rep, "reviews": items}

    return r
