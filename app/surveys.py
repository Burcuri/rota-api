"""ANKETLER (1.26.0; kullanıcı kararı 7 Kasım 2026: "anketler ekranını sunucuya arşivlemeliyiz ve veriyi tutmalıyız").

- Soruyu YÖNETİM yazar (yönetim › Anketler). Kuryeler yanıtlar: anket başına tek yanıt, sonradan değiştirilebilir.
- Herkes yalnız ORTALAMAYI görür; 3'ten az yanıt varken ortalama gösterilmez (tek kişinin yazdığı belli olmasın). Kimin ne yazdığı hiçbir
  ekranda yok; yönetimin CSV'sinde de kişi yok (yalnız gün, il, ilçe, yanıt).
- Bölge: yanıt anındaki çalışma bölgesi (il / ilçe; hesabın "Çalışma bölgesi"). İşletme kendi ilçesinin ortalamasını görür (en az 3 yanıt),
  yoksa genel ortalamayı.
- Kapanan anket SİLİNMEZ, ARŞİVE geçer (yanıtlarıyla); yönetim geri açabilir. Hesap silinince yanıtı ANONİM kalır (kişi bağı kopar).
- İlk açılışta iki anket kendiliğinden kurulur (teslimat ücreti, saat ücreti): uygulamadaki eski örneklerle aynı sorular.
"""
import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import events
from .admin import audit, current_admin
from .auth import current_user
from .db import get_db
from .limits import Window
from .models import Admin, Survey, SurveyAnswer, User, now, uid

MIN_SHOW = 3  # bundan az yanıtta ortalama gösterilmez
KINDS = {"ucret": "₺", "saat": "₺/saat", "sayi": ""}
DEFAULTS = (
    ("sv-ucret", "Bu bölgede bir teslimat için en az ne kadar ücret istersin?", "ucret", "₺"),
    ("sv-saat", "Saatlik çalışırsan saat ücretin ne olmalı?", "saat", "₺/saat"),
)
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_LINK_RE = re.compile(r"https?:|www\.", re.IGNORECASE)


def utc(dt: datetime | None) -> datetime | None:
    """SQLite saat dilimini düşürür; her şey UTC sayılır."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def ms(t) -> int | None:
    return None if t is None else int(utc(t).timestamp() * 1000)


def tr_day(t) -> str:
    """Türkiye günü (UTC+3)"""
    return (utc(t) + timedelta(hours=3)).strftime("%Y-%m-%d") if t is not None else ""


def area_of(u: User) -> tuple[str, str]:
    """Hesabın çalışma bölgesi (il, ilçe). Ayarlı değilse boş."""
    try:
        d = json.loads(u.work_area or "{}")
    except Exception:
        d = {}
    d = d if isinstance(d, dict) else {}
    return str(d.get("p") or "")[:60], str(d.get("d") or "")[:80]


def seed(db: Session) -> None:
    """Hiç anket yoksa iki varsayılan anket (sabit kimlik: iki istek aynı anda gelse de bir kez)."""
    if db.query(Survey.id).first() is not None:
        return
    for sid, q, kind, unit in DEFAULTS:
        db.add(Survey(id=sid, q=q, kind=kind, unit=unit, min_v=1, max_v=99999, status="active", created_by="sistem"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def stats(db: Session, ids: list[str], province: str = "", district: str = "") -> dict:
    """{anket: {"n", "avg", "localN", "localAvg"}} — yerel = verilen il + ilçe."""
    out: dict = {i: {"n": 0, "avg": None, "localN": 0, "localAvg": None} for i in ids}
    if not ids:
        return out
    for sid, n, avg in (db.query(SurveyAnswer.survey_id, func.count(SurveyAnswer.id), func.avg(SurveyAnswer.value))
                        .filter(SurveyAnswer.survey_id.in_(ids)).group_by(SurveyAnswer.survey_id).all()):
        out[sid].update(n=int(n or 0), avg=float(avg) if avg is not None else None)
    if district:
        for sid, n, avg in (db.query(SurveyAnswer.survey_id, func.count(SurveyAnswer.id), func.avg(SurveyAnswer.value))
                            .filter(SurveyAnswer.survey_id.in_(ids), SurveyAnswer.district == district, SurveyAnswer.province == province)
                            .group_by(SurveyAnswer.survey_id).all()):
            out[sid].update(localN=int(n or 0), localAvg=float(avg) if avg is not None else None)
    return out


def shown(n: int, avg: float | None) -> int | None:
    return round(avg) if (avg is not None and n >= MIN_SHOW) else None


def survey_out(s: Survey, st: dict, mine: float | None = None, area: tuple[str, str] = ("", "")) -> dict:
    province, district = area
    local = None
    if district:
        local = {"province": province, "district": district, "n": st["localN"], "avg": shown(st["localN"], st["localAvg"])}
    return {
        "id": s.id, "q": s.q, "kind": s.kind, "unit": s.unit, "min": s.min_v, "max": s.max_v, "status": s.status,
        "n": st["n"], "avg": shown(st["n"], st["avg"]), "local": local,
        "mine": (int(mine) if mine is not None and float(mine).is_integer() else mine), "at": ms(s.created_at),
    }


def clean_q(t: str) -> str:
    t = _CTRL_RE.sub(" ", t or "").strip()
    t = re.sub(r"\s{2,}", " ", t)
    if len(t) < 8:
        raise HTTPException(400, "Soru çok kısa")
    if _LINK_RE.search(t):
        raise HTTPException(400, "Soruda bağlantı olmaz")
    return t[:200]


class AnswerIn(BaseModel):
    value: float = Field(gt=0, le=1_000_000)


class SurveyIn(BaseModel):
    q: str = Field(max_length=200)
    kind: str = Field(default="ucret", pattern="^(ucret|saat|sayi)$")
    unit: str | None = Field(default=None, max_length=16)
    min: float = Field(default=1, ge=0, le=1_000_000)
    max: float = Field(default=99999, gt=0, le=1_000_000)


def make_router() -> APIRouter:
    r = APIRouter()
    answer_win = Window(20, 3600)  # kişi başına saatte 20 yanıt / değişiklik

    def mine_of(db: Session, user: User, ids: list[str]) -> dict:
        if not ids:
            return {}
        return {sid: v for sid, v in db.query(SurveyAnswer.survey_id, SurveyAnswer.value)
                .filter(SurveyAnswer.user_id == user.id, SurveyAnswer.survey_id.in_(ids)).all()}

    @r.get("/v1/surveys")
    def surveys(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Açık anketler: ortalama (en az 3 yanıt), kendi ilçenin ortalaması ve (kuryede) kendi yanıtın."""
        seed(db)
        rows = db.query(Survey).filter(Survey.status == "active").order_by(Survey.created_at.desc()).limit(30).all()
        ids = [s.id for s in rows]
        area = area_of(user)
        st = stats(db, ids, *area)
        mine = mine_of(db, user, ids)
        return {"items": [survey_out(s, st[s.id], mine.get(s.id), area) for s in rows], "minShow": MIN_SHOW,
                "canAnswer": user.active_role == "courier"}

    @r.post("/v1/surveys/{sid}/answer")
    def answer(sid: str, b: AnswerIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.active_role != "courier":
            raise HTTPException(403, "Anketleri kuryeler yanıtlar")
        s = db.get(Survey, sid)
        if s is None or s.status != "active":
            raise HTTPException(404, "Bu anket kapandı")
        if not (s.min_v <= b.value <= s.max_v):
            raise HTTPException(400, f"Yanıt {s.min_v:g} ile {s.max_v:g} arasında olmalı")
        if not answer_win.allow(user.id):
            raise HTTPException(429, "Çok sık yanıt verdin; biraz sonra yeniden dene")
        province, district = area_of(user)
        v = round(float(b.value), 2)
        row = db.query(SurveyAnswer).filter(SurveyAnswer.survey_id == sid, SurveyAnswer.user_id == user.id).first()
        if row is None:
            db.add(SurveyAnswer(id=uid(), survey_id=sid, user_id=user.id, value=v, province=province, district=district))
            try:
                db.commit()
            except IntegrityError:  # aynı anda iki yanıt: ikincisi güncelleme olur
                db.rollback()
                row = db.query(SurveyAnswer).filter(SurveyAnswer.survey_id == sid, SurveyAnswer.user_id == user.id).first()
        if row is not None:
            row.value, row.province, row.district, row.updated_at = v, province, district, now()
            db.commit()
        events.log(db, "survey.answer", user.id, survey=sid)
        db.commit()
        st = stats(db, [sid], province, district)
        return {"item": survey_out(s, st[sid], v, (province, district))}

    # ------------------------------------------------------------------ yönetim
    def admin_out(db: Session, s: Survey, st: dict) -> dict:
        top = (db.query(SurveyAnswer.province, SurveyAnswer.district, func.count(SurveyAnswer.id), func.avg(SurveyAnswer.value))
               .filter(SurveyAnswer.survey_id == s.id).group_by(SurveyAnswer.province, SurveyAnswer.district)
               .order_by(func.count(SurveyAnswer.id).desc()).limit(12).all())
        last = db.query(func.max(SurveyAnswer.updated_at)).filter(SurveyAnswer.survey_id == s.id).scalar()
        return {
            "id": s.id, "q": s.q, "kind": s.kind, "unit": s.unit, "min": s.min_v, "max": s.max_v, "status": s.status,
            "by": s.created_by, "at": ms(s.created_at), "archivedAt": ms(s.archived_at), "lastAnswerAt": ms(last),
            "n": st["n"], "avg": round(st["avg"]) if st["avg"] is not None else None,
            "areas": [{"province": p or "", "district": d or "", "n": int(n), "avg": round(float(a)) if a is not None else None} for p, d, n, a in top],
        }

    @r.get("/v1/admin/surveys")
    def admin_list(status: str = Query(default="all", pattern="^(all|active|archived)$"), a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        seed(db)
        q = db.query(Survey)
        if status != "all":
            q = q.filter(Survey.status == status)
        rows = q.order_by(Survey.status.asc(), Survey.created_at.desc()).limit(200).all()
        st = stats(db, [s.id for s in rows])
        return {"items": [admin_out(db, s, st[s.id]) for s in rows], "minShow": MIN_SHOW}

    @r.post("/v1/admin/surveys")
    def admin_create(b: SurveyIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        q = clean_q(b.q)
        if b.min >= b.max:
            raise HTTPException(400, "En küçük değer en büyükten küçük olmalı")
        unit = _CTRL_RE.sub("", b.unit if b.unit is not None else KINDS[b.kind]).strip()[:16]
        if db.query(Survey.id).filter(Survey.status == "active").count() >= 20:
            raise HTTPException(409, "Aynı anda en çok 20 açık anket olur; önce birini arşive alın")
        s = Survey(id=uid(), q=q, kind=b.kind, unit=unit, min_v=b.min, max_v=b.max, status="active", created_by=a.username[:64])
        db.add(s)
        audit(db, request, a, "survey_create", "survey", s.id, q[:200])
        db.commit()
        return admin_out(db, s, stats(db, [s.id])[s.id])

    def set_status(sid: str, to: str, request: Request, a: Admin, db: Session) -> dict:
        s = db.get(Survey, sid)
        if s is None:
            raise HTTPException(404, "Anket yok")
        if s.status != to:
            s.status = to
            s.archived_at = now() if to == "archived" else None
            audit(db, request, a, "survey_archive" if to == "archived" else "survey_restore", "survey", s.id, s.q[:200])
            db.commit()
        return admin_out(db, s, stats(db, [s.id])[s.id])

    @r.post("/v1/admin/surveys/{sid}/archive")
    def admin_archive(sid: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Anket kapanır: uygulamada görünmez, yanıtlar SİLİNMEZ (arşiv)."""
        return set_status(sid, "archived", request, a, db)

    @r.post("/v1/admin/surveys/{sid}/restore")
    def admin_restore(sid: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        return set_status(sid, "active", request, a, db)

    @r.get("/v1/admin/surveys/{sid}/export")
    def admin_export(sid: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Yanıtlar CSV (Excel için ; ayraç, UTF-8 BOM). Kişi YOK: yalnız gün, il, ilçe, yanıt."""
        s = db.get(Survey, sid)
        if s is None:
            raise HTTPException(404, "Anket yok")
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        w.writerow(["Soru", s.q])
        w.writerow(["Birim", s.unit])
        w.writerow([])
        w.writerow(["Gün", "İl", "İlçe", "Yanıt"])
        rows = db.query(SurveyAnswer).filter(SurveyAnswer.survey_id == sid).order_by(SurveyAnswer.updated_at.asc()).limit(100000).all()
        for x in rows:
            w.writerow([tr_day(x.updated_at or x.created_at), x.province or "", x.district or "", f"{x.value:g}".replace(".", ",")])
        audit(db, request, a, "survey_export", "survey", s.id, f"{len(rows)} yanıt")
        db.commit()
        name = f"rota-anket-{s.id[:12]}.csv"
        return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                         headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    r.limiters = (answer_win,)  # type: ignore[attr-defined]
    return r
