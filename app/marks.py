"""YÖNETİM: ödül koyanların karnesi, ÖNE ÇIKARMA, AF ve KARA LİSTE (+ not) (1.16.0; kullanıcı kararı 27 Ekim 2026).

Kurallar app/standing.py'de. Bütün işlemler yönetici adıyla işlem günlüğüne (admin_audit) yazılır. Kara liste notu yalnız yönetime görünür.
"""
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .admin import audit, current_admin
from .auth import norm_phone
from .db import get_db
from .models import Admin, Notice, Reward, RewardClaim, User, UserMark, now, uid
from .standing import LEVEL_ORDER, mark_for_update, mark_out, marks_of, standings

PFX = {"courier": "KRY-", "employer": "ISV-", "sponsor": "SPN-"}
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def clean_note(raw: str) -> str:
    return re.sub(r"[ \t]+", " ", _CTRL_RE.sub(" ", raw or "")).strip()[:500]


def code_of(u: User | None) -> str:
    if u is None or not u.public_no:
        return ""
    return PFX.get(u.active_role or "", "") + u.public_no


class DaysIn(BaseModel):
    days: int = Field(ge=0, le=90)


class ListIn(BaseModel):
    user_id: str = Field(default="", alias="userId", max_length=40)
    # Kullanıcı no (KRY-8472019 / SPN-… / yalnız rakam), telefon ya da kimlik
    code: str = Field(default="", max_length=60)
    note: str = Field(default="", max_length=500)

    model_config = {"populate_by_name": True}


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=500)


def make_router() -> APIRouter:
    r = APIRouter()

    def find_user(db: Session, user_id: str, code: str) -> User:
        u = db.get(User, user_id) if user_id else None
        if u is None and code:
            q = code.strip()
            digits = re.sub(r"\D", "", q)
            if re.fullmatch(r"[0-9a-f]{32}", q.lower()):
                u = db.get(User, q.lower())
            if u is None and len(digits) == 7:
                u = db.query(User).filter(User.public_no == digits).first()
            if u is None and len(digits) >= 10:
                try:
                    u = db.query(User).filter(User.phone == norm_phone(q)).first()
                except HTTPException:
                    u = None
        if u is None:
            raise HTTPException(404, "Üye bulunamadı (kimlik, kullanıcı no ya da telefon)")
        return u

    def user_brief(u: User) -> dict:
        return {"id": u.id, "name": u.name, "code": code_of(u), "role": u.active_role, "banned": bool(u.banned)}

    # ------------------------------------------------------------------ ödül koyanlar (karne + durum)
    @r.get("/v1/admin/rewards/owners")
    def owners(view: str = Query(default="all", pattern=r"^(all|irregular|trusted|featured|hidden|blacklisted)$"),
               a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Ödül koyan bütün sponsor / işyerleri: karne, durum (öne çıkan / güvenilir / normal / gizli / kara liste), nedenler, yönetim notu."""
        owner_ids = {x for (x,) in db.query(Reward.owner_id).distinct().all()} | {x for (x,) in db.query(RewardClaim.owner_id).distinct().all()}
        listed = {m.user_id for m in db.query(UserMark).filter(UserMark.blacklisted.is_(True)).all()}
        cand = {i for i in owner_ids | listed if i}
        ppl = {u.id: u for u in db.query(User).filter(User.id.in_(cand)).all()} if cand else {}
        # kara listedeki KURYE burada değil (Kara liste sekmesinde); ödülü olan ya da sponsor / işyeri hesabı olanlar
        ids = {i for i in cand if i in ppl and (i in owner_ids or ppl[i].active_role in ("sponsor", "employer"))}
        sts = standings(db, ids)
        marks = marks_of(db, ids)
        active = {}
        for (oid,) in db.query(Reward.owner_id).filter(Reward.status == "active").all():
            active[oid] = active.get(oid, 0) + 1
        items = []
        for i in ids:
            st = sts[i]
            if view == "irregular" and not st["irregular"]:
                continue
            if view in ("trusted", "featured", "hidden", "blacklisted") and not st[view]:
                continue
            items.append({**user_brief(ppl[i]), "standing": st, "mark": mark_out(marks.get(i)), "rewards": active.get(i, 0)})
        items.sort(key=lambda x: (LEVEL_ORDER.get(x["standing"]["level"], 9), 0 if x["standing"]["irregular"] else 1, -x["standing"]["claims"], x["name"].lower()))
        return {"items": items[:300]}

    @r.post("/v1/admin/users/{uid_}/feature")
    def feature(uid_: str, b: DaysIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Öne çıkar (gün sayısı; 0 = kaldır). Ödül koyana bildirim gider."""
        u = find_user(db, uid_, "")
        m = mark_for_update(db, u.id)
        if b.days:
            m.featured_until = now() + timedelta(days=b.days)
            m.featured_by = a.username[:32]
            until = (m.featured_until + timedelta(hours=3)).strftime("%d.%m.%Y")
            db.add(Notice(id=uid(), user_id=u.id, kind="reward.featured", text=f"⭐ Ödülleriniz {until} tarihine kadar katalogda öne çıkarıldı. Zamanında kargo ve memnuniyet için teşekkürler.", ref=""))
        else:
            m.featured_until = None
        m.updated_at = now()
        audit(db, request, a, "sponsor_feature", "user", u.id, f"{b.days} gün" if b.days else "kaldırıldı")
        db.commit()
        return {**user_brief(u), "standing": standings(db, {u.id})[u.id], "mark": mark_out(m)}

    @r.post("/v1/admin/users/{uid_}/pardon")
    def pardon(uid_: str, b: DaysIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Düzensiz sayılıp katalogdan inen ödül koyanı affet (gün sayısı; 0 = affı kaldır): ödülleri bu süre görünür."""
        u = find_user(db, uid_, "")
        m = mark_for_update(db, u.id)
        m.pardon_until = now() + timedelta(days=b.days) if b.days else None
        m.updated_at = now()
        audit(db, request, a, "sponsor_pardon", "user", u.id, f"{b.days} gün" if b.days else "kaldırıldı")
        db.commit()
        return {**user_brief(u), "standing": standings(db, {u.id})[u.id], "mark": mark_out(m)}

    # ------------------------------------------------------------------ kara liste
    def list_row(u: User, m: UserMark) -> dict:
        # 1.20.0: hesabını silen kara liste üyesinin numarası silindi (yalnız geri çevrilemez özeti kaldı): numara yerine boş + işaret
        return {**user_brief(u), "phone": "" if u.deleted_at else u.phone, "deleted": bool(u.deleted_at), **mark_out(m)}

    @r.get("/v1/admin/blacklist")
    def blacklist(a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        rows = db.query(UserMark).filter(UserMark.blacklisted.is_(True)).order_by(UserMark.listed_at.desc()).limit(500).all()
        ppl = {u.id: u for u in db.query(User).filter(User.id.in_([m.user_id for m in rows])).all()} if rows else {}
        return {"items": [list_row(ppl[m.user_id], m) for m in rows if m.user_id in ppl]}

    @r.post("/v1/admin/blacklist")
    def blacklist_add(b: ListIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Kara listeye al (not zorunlu). Zaten listedeyse not güncellenir."""
        u = find_user(db, b.user_id, b.code)
        note = clean_note(b.note)
        if len(note) < 3:
            raise HTTPException(400, "Kara liste notunu yazın (neden; yalnız yönetim görür)")
        m = mark_for_update(db, u.id)
        was = bool(m.blacklisted)
        m.blacklisted = True
        if not was:
            m.listed_at, m.listed_by = now(), a.username[:32]
        m.note, m.note_at, m.note_by = note, now(), a.username[:32]
        m.updated_at = now()
        audit(db, request, a, "blacklist_note" if was else "blacklist_add", "user", u.id, note[:200])
        db.commit()
        return list_row(u, m)

    @r.delete("/v1/admin/blacklist/{uid_}")
    def blacklist_remove(uid_: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        u = find_user(db, uid_, "")
        m = mark_for_update(db, u.id)
        if m.blacklisted:
            m.blacklisted = False
            m.updated_at = now()
            audit(db, request, a, "blacklist_remove", "user", u.id)
        db.commit()
        return list_row(u, m)

    @r.put("/v1/admin/users/{uid_}/note")
    def note_set(uid_: str, b: NoteIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Yönetim notu (kara listede olsun olmasın; yalnız yönetim görür). Kara listedeyken not boşaltılamaz."""
        u = find_user(db, uid_, "")
        note = clean_note(b.note)
        m = mark_for_update(db, u.id)
        if m.blacklisted and len(note) < 3:
            raise HTTPException(400, "Kara listedeki üyenin notu boş bırakılamaz")
        if note != (m.note or ""):
            m.note, m.note_at, m.note_by = note, now(), a.username[:32]
            m.updated_at = now()
            audit(db, request, a, "user_note", "user", u.id, note[:200] or "not silindi")
        db.commit()
        return {**user_brief(u), **mark_out(m)}

    return r

