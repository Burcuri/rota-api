"""ŞİKÂYET (1.28.0; kullanıcı isteği 9 Kasım 2026: "Sohbete Şikâyet et + Engelle. Apple ve Google bunu şart koşuyor … lütfen bunu bitir").
Mağaza şartı: App Store 1.2 (kötü içerik süzgeci, şikâyet + zamanında cevap, engelleme, iletişim bilgisi) ve Google Play UGC (uygulama
içinden içerik ve kullanıcı şikâyeti, engelleme, denetim).

Kullanıcı: POST /v1/reports — sohbette mesaja uzun basınca, sohbet menüsünden (⋮) ya da grup üye listesinden. Mesajlar sunucuda TUTULMADIĞI
için şikâyet eden, şikâyet ettiği mesajı ve o kişinin bu sohbetteki son birkaç mesajını kendisi yollar (en çok 6; her biri ≤ 1000 karakter).
İsterse aynı anda engeller (blocks.py). Aynı kişiyi 24 saat içinde yeniden şikâyet ederse yeni kayıt açılmaz, mesajlar eklenir.
Yönetim (24 saat içinde): GET /v1/admin/reports · POST /v1/admin/reports/{id}/close {action: none | warn | ban}.
 - Şikâyet edene "🛡️ Şikâyetin incelendi" duyurusu gider (kim olduğu, ne yapıldığı söylenmez).
 - warn: şikâyet edilene uyarı duyurusu. ban: hesap men edilir ve o kişinin BÜTÜN açık şikâyetleri kapanır.
Saklama: kapatılan şikâyet REPORT_KEEP_DAYS (180) gün sonra silinir. Olay günlüğüne içerik yazılmaz.

1.29.0 İÇERİK ŞİKÂYETİ (kullanıcı isteği 9 Kasım 2026: "Diğer yerlere de Şikâyet et … tabelalara şikâyet edebilmek için tıklamak ve gelen bir
pop up olsun"): place = ad (harita tabelası) | job (iş ilanı) | card (işletme kartı), `ref` = tabela / ilan kimliği (kartta işletmenin kimliği).
İçeriğin o anki hâli SUNUCUNUN kendi kaydından alınır (`context.content`; istemcinin yazdığına güvenilmez). Aynı kişi aynı içeriği 24 saatte
bir kez açar (yeniden gönderirse birleşir). Şikâyet eden o tabelayı / ilanı artık görmez (/v1/ads/active, /v1/jobs/open). Yönetimde ek karar
remove: tabela (aynı paketin bütün noktaları) yayından iner · ilan kapanır (görevi de biter) · işletme kartında logo kaldırılır; sahibine
duyuru gider ve aynı içerik hakkındaki bütün açık şikâyetler kapanır.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import events, notify
from .admin import audit, ban_user, current_admin, iso, oldest_open_hours
from .auth import current_user
from .blocks import add_block
from .db import get_db
from .limits import Window
from .models import AdBooking, Admin, Job, Report, User, now
from .points import notify as notice, user_code
from .pushes import queue as push_queue

REASONS = ("spam", "abuse", "threat", "fraud", "sexual", "other")
REASON_TR = {
    "spam": "Spam / reklam", "abuse": "Hakaret, küfür ya da taciz", "threat": "Tehdit ya da şiddet", "fraud": "Dolandırıcılık",
    "sexual": "Cinsel ya da uygunsuz içerik", "other": "Diğer",
}
REPORT_KEEP_DAYS = 180
MSG_MAX = 6
CTX_MAX = 12
DAILY_MAX = 20
DONE_TEXT = "🛡️ Şikâyetin incelendi. ROTA'yı güvenli tuttuğun için teşekkürler."
WARN_TEXT = ("⚠️ Mesajların hakkında bir şikâyet aldık ve inceledik. Hakaret, taciz, tehdit, dolandırıcılık ve uygunsuz içerik ROTA'da "
             "yasaktır; tekrarlanırsa hesabın kapatılır.")
BAN_TEXT = "Topluluk kurallarına aykırı mesajlar (şikâyet üzerine)"
# 1.29.0 içerik şikâyeti: tabela, ilan, işletme kartı
CONTENT = ("ad", "job", "card")
_RULE = "Yanıltıcı, hakaret içeren ya da uygunsuz tabela, ilan, işletme adı ve logo ROTA'da yasaktır; tekrarlanırsa hesabın kapatılır."
WARN_CONTENT = {
    "ad": "⚠️ Tabelan hakkında bir şikâyet aldık ve inceledik. " + _RULE,
    "job": "⚠️ İlanın hakkında bir şikâyet aldık ve inceledik. " + _RULE,
    "card": "⚠️ İşletme kartın (ad, tabela adı ya da logo) hakkında bir şikâyet aldık ve inceledik. " + _RULE,
}
REMOVED_TEXT = {
    "ad": "Tabelan “{what}” topluluk kurallarına aykırı bulunduğu için yönetim tarafından yayından kaldırıldı.",
    "job": "İlanın “{what}” topluluk kurallarına aykırı bulunduğu için yönetim tarafından kaldırıldı.",
    "card": "İşletme logon topluluk kurallarına aykırı bulunduğu için yönetim tarafından kaldırıldı. Yeni logo yükleyebilirsin.",
}
BAN_TEXT_CONTENT = "Topluluk kurallarına aykırı içerik (şikâyet üzerine)"
_EMAIL_RE = re.compile(r"^[^\s@<>\"'()]+@[^\s@<>\"'()]+\.[^\s@<>\"'()]+$")


def clean(s: str | None, n: int) -> str:
    """Kontrol karakterleri atılır (satır sonu kalır), boşluk kırpılır, uzunluk sınırlanır"""
    t = "".join(ch for ch in (s or "") if ch == "\n" or ch >= " ")
    return t.strip()[:n]


def load_ctx(x: Report) -> dict:
    try:
        c = json.loads(x.context or "{}")
    except ValueError:
        c = {}
    msgs = c.get("messages") if isinstance(c.get("messages"), list) else []
    content = c.get("content") if isinstance(c.get("content"), dict) else None
    return {"group": str(c.get("group") or ""), "messages": [m for m in msgs if isinstance(m, dict)], "content": content}


def content_of(db: Session, place: str, ref: str, t: User) -> dict:
    """Şikâyet edilen içeriğin o anki hâli, sunucunun kendi kaydından. İçerik yoksa ya da şikâyet edilen kişinin değilse 404."""
    if place == "ad":
        a = db.get(AdBooking, ref) if ref else None
        if a is None or a.owner_id != t.id:
            raise HTTPException(404, "Tabela bulunamadı")
        return {"label": clean(a.label, 60), "lat": round(float(a.lat), 5), "lon": round(float(a.lon), 5), "groupId": a.group_id or "",
                "logo": bool(t.logo), "endsAt": iso(a.ends_at)}
    if place == "job":
        j = db.get(Job, ref) if ref else None
        if j is None or j.employer_id != t.id:
            raise HTTPException(404, "İlan bulunamadı")
        return {"title": clean(j.title, 160), "note": clean(j.note, 1000), "area": clean(j.area, 160), "fee": j.fee,
                "packages": int(j.package_count or 0)}
    if (ref and ref != t.id) or t.active_role != "employer":
        raise HTTPException(404, "İşletme bulunamadı")
    from .bizcard import business_card  # döngüsel içe aktarma olmasın
    c = business_card(db, t)
    return {"name": clean(c.get("name"), 120), "signboard": clean(c.get("signboard"), 80), "category": clean(c.get("category"), 60),
            "hours": clean(c.get("hours"), 60), "logo": bool(c.get("logo"))}


def reported_refs(db: Session, user_id: str, place: str) -> set[str]:
    """Bu kişinin şikâyet ettiği tabelalar / ilanlar (artık ona gösterilmez; şikâyet silinene dek, en çok 180 gün + inceleme)."""
    return {r for (r,) in db.query(Report.ref).filter(Report.reporter_id == user_id, Report.place == place, Report.ref != "").all() if r}


def alert_admins(reason: str) -> None:
    """Yönetime e-posta (DESTEK_EPOSTA; e-posta bağlıysa). İçerik ve kişi yazılmaz. İsteği bekletmesin diye arka planda."""
    to = (os.getenv("DESTEK_EPOSTA") or "").strip()
    if not to or not _EMAIL_RE.match(to) or not notify.email_enabled():
        return
    body = (f"Yeni şikâyet: {REASON_TR.get(reason, reason)}.\n"
            "Yönetim › Şikâyetler'den 24 saat içinde incele (mağaza şartı).\n"
            "Mesaj içeriği ve kişiler e-postaya yazılmaz.")
    threading.Thread(target=lambda: notify.send_email(to, "ROTA · yeni şikâyet", body), name="report-alert", daemon=True).start()


def purge(db: Session) -> int:
    cut = now() - timedelta(days=REPORT_KEEP_DAYS)
    n = db.query(Report).filter(Report.status == "closed", Report.closed_at < cut).delete(synchronize_session=False)
    if n:
        db.commit()
    return int(n or 0)


class MsgIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    at: int = Field(default=0, ge=0)


class ReportIn(BaseModel):
    target_id: str = Field(alias="targetId", min_length=1, max_length=40)
    kind: Literal["message", "user", "content"] = "user"
    reason: Literal["spam", "abuse", "threat", "fraud", "sexual", "other"]
    note: str = Field(default="", max_length=600)
    place: Literal["dm", "group", "map", "other", "ad", "job", "card"] = "dm"
    ref: str = Field(default="", max_length=40)  # 1.29.0: tabela / ilan kimliği (kartta işletmenin kimliği)
    group: str = Field(default="", max_length=200)
    messages: list[MsgIn] = Field(default_factory=list, max_length=12)
    block: bool = False

    model_config = {"populate_by_name": True}


class CloseIn(BaseModel):
    action: Literal["none", "warn", "ban", "remove"]
    note: str = Field(default="", max_length=600)


def take_down(db: Session, place: str, ref: str, owner: User) -> str:
    """Yönetim kararı "remove" (commit ETMEZ). Tabela: aynı paketin bütün noktaları yayından iner (coin iade edilmez) · ilan: kapanır, ona
    bağlı görev ve canlı takip biter · işletme kartı: logo kaldırılır (tabelalarda da logo görünmez). Duyuruda geçecek adı döner."""
    if place == "ad":
        from .account import tick  # döngüsel içe aktarma olmasın
        tick(db, owner.id, ads=True)  # hesabın eşitleme saati: isteğin ilk yazımı
        a = db.get(AdBooking, ref)
        if a is None:
            return ""
        rows = [a] if not a.group_id else db.query(AdBooking).filter(AdBooking.owner_id == owner.id, AdBooking.group_id == a.group_id).all()
        t = now()
        for y in rows:
            if y.removed_at is None:
                y.removed_at, y.removed_by = t, "admin"
        return a.label or "Tabela"
    if place == "job":
        j = db.get(Job, ref)
        if j is None:
            return ""
        if j.status != "closed":
            j.status = "closed"
            for p in list(j.packages):
                db.delete(p)
            from .tasks import end_tasks_for_job  # döngüsel içe aktarma olmasın
            end_tasks_for_job(db, j.id, "job_closed", None)
        return j.title or "İlan"
    owner.logo, owner.logo_type, owner.logo_at = None, None, None
    return "logo"


def make_router() -> APIRouter:
    r = APIRouter()
    lim = Window(DAILY_MAX, 86400)

    @r.post("/v1/reports")
    def create(b: ReportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if b.target_id == user.id:
            raise HTTPException(400, "Kendini şikâyet edemezsin")
        t = db.get(User, b.target_id)
        if t is None:
            raise HTTPException(404, "Kişi bulunamadı")
        is_content = b.place in CONTENT
        ref, content, kind = "", None, (b.kind if b.kind in ("message", "user") else "user")
        if is_content:
            ref = (b.ref or "").strip()[:40] or (t.id if b.place == "card" else "")
            content, kind = content_of(db, b.place, ref, t), "content"
        msgs = []
        for m in ([] if is_content else b.messages[:MSG_MAX]):
            txt = clean(m.text, 1000)
            if txt:
                msgs.append({"text": txt, "at": int(m.at or 0)})
        if b.block:
            add_block(db, user.id, t.id)  # şikâyet sınıra takılsa bile engel yazılır
        q = db.query(Report).filter(Report.reporter_id == user.id, Report.target_id == t.id, Report.status == "open",
                                    Report.created_at >= now() - timedelta(hours=24))
        # Sohbet şikâyetleri kendi aralarında, içerik şikâyeti yalnız AYNI içerikle birleşir
        q = q.filter(Report.place == b.place, Report.ref == ref) if is_content else q.filter(Report.place.notin_(CONTENT))
        old = q.first()
        if old is not None and is_content:
            old.context = json.dumps({"group": "", "messages": [], "content": content}, ensure_ascii=False)
            if not old.note and b.note:
                old.note = clean(b.note, 300)
            old.blocked = bool(old.blocked or b.block)
            db.commit()
            return {"ok": True, "id": old.id, "again": True}
        if old is not None:
            ctx = load_ctx(old)
            seen = {(m.get("text"), m.get("at")) for m in ctx["messages"]}
            ctx["messages"] = (ctx["messages"] + [m for m in msgs if (m["text"], m["at"]) not in seen])[-CTX_MAX:]
            old.context = json.dumps({"group": ctx["group"] or clean(b.group, 80), "messages": ctx["messages"]}, ensure_ascii=False)
            if not old.note and b.note:
                old.note = clean(b.note, 300)
            old.blocked = bool(old.blocked or b.block)
            db.commit()
            return {"ok": True, "id": old.id, "again": True}
        if not lim.allow(user.id):
            db.commit()
            raise HTTPException(429, "Bugün çok fazla şikâyet gönderdin; yarın yeniden dene")
        body = {"group": "", "messages": [], "content": content} if is_content else {"group": clean(b.group, 80), "messages": msgs}
        rep = Report(
            reporter_id=user.id, target_id=t.id, kind=kind, reason=b.reason, note=clean(b.note, 300), place=b.place, ref=ref,
            context=json.dumps(body, ensure_ascii=False), blocked=bool(b.block),
        )
        db.add(rep)
        db.flush()
        events.log(db, "report.new", user.id, rep.id, reason=b.reason, place=b.place, n=len(msgs))  # içerik ve kişi yazılmaz
        db.commit()
        alert_admins(b.reason)
        return {"ok": True, "id": rep.id}

    # ------------------------------------------------------------------ yönetim
    def person(u: User | None) -> dict:
        if u is None:
            return {"id": "", "name": "", "code": "", "role": "", "banned": False, "deleted": False}
        return {"id": u.id, "name": u.name or "", "code": user_code(u), "role": u.active_role or "", "banned": bool(u.banned), "deleted": u.deleted_at is not None}

    def row(x: Report, ppl: dict[str, User], counts: dict[str, int], live: dict[str, dict]) -> dict:
        ctx = load_ctx(x)
        return {
            "id": x.id, "kind": x.kind, "reason": x.reason, "reasonText": REASON_TR.get(x.reason, x.reason), "note": x.note, "place": x.place,
            "ref": x.ref or "", "content": ctx["content"], "live": live.get(x.id),
            "group": ctx["group"], "messages": ctx["messages"], "blocked": bool(x.blocked), "status": x.status, "action": x.action,
            "adminNote": x.admin_note, "closedBy": x.closed_by, "createdAt": iso(x.created_at), "closedAt": iso(x.closed_at),
            "reporter": person(ppl.get(x.reporter_id)), "target": person(ppl.get(x.target_id)), "targetReporters90d": counts.get(x.target_id, 0),
        }

    def rows_out(db: Session, rows: list[Report]) -> list[dict]:
        ids = {i for x in rows for i in (x.reporter_id, x.target_id)}
        ppl = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
        tids = {x.target_id for x in rows}
        counts: dict[str, int] = {}
        if tids:
            q = (
                db.query(Report.target_id, func.count(func.distinct(Report.reporter_id)))
                .filter(Report.target_id.in_(tids), Report.created_at >= now() - timedelta(days=90))
                .group_by(Report.target_id)
            )
            counts = {k: int(v) for k, v in q.all()}
        # 1.29.0: içerik şikâyetinde içeriğin ŞİMDİKİ durumu (kaldırıldı mı)
        ad_ids = [x.ref for x in rows if x.place == "ad" and x.ref]
        job_ids = [x.ref for x in rows if x.place == "job" and x.ref]
        ads = {y.id: y for y in db.query(AdBooking).filter(AdBooking.id.in_(ad_ids)).all()} if ad_ids else {}
        jobs = {y.id: y for y in db.query(Job).filter(Job.id.in_(job_ids)).all()} if job_ids else {}
        live: dict[str, dict] = {}
        for x in rows:
            if x.place == "ad":
                y = ads.get(x.ref)
                live[x.id] = {"removed": y is None or y.removed_at is not None, "by": (y.removed_by if y else "") or ""}
            elif x.place == "job":
                y = jobs.get(x.ref)
                live[x.id] = {"removed": y is None or y.status == "closed", "by": ""}
            elif x.place == "card":
                u = ppl.get(x.target_id)
                live[x.id] = {"removed": not (u is not None and u.logo), "by": ""}
        return [row(x, ppl, counts, live) for x in rows]

    @r.get("/v1/admin/reports")
    def admin_list(status: str = "open", limit: int = Query(default=100, ge=1, le=300), a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Açık şikâyetler en eskiden başlar (önce o incelenir); kapatılanlar en yeniden."""
        purge(db)
        q = db.query(Report)
        if status in ("open", "closed"):
            q = q.filter(Report.status == status)
        q = q.order_by(Report.created_at.asc()) if status == "open" else q.order_by(Report.created_at.desc())
        items = rows_out(db, q.limit(limit).all())
        n_open = db.query(func.count(Report.id)).filter(Report.status == "open").scalar() or 0
        return {"items": items, "openCount": int(n_open), "oldestH": oldest_open_hours(db), "keepDays": REPORT_KEEP_DAYS}

    @r.post("/v1/admin/reports/{rid}/close")
    def admin_close(rid: str, b: CloseIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        x = db.get(Report, rid)
        if x is None:
            raise HTTPException(404, "Şikâyet bulunamadı")
        if x.status != "open":
            raise HTTPException(409, "Bu şikâyet zaten kapatıldı")
        t = db.get(User, x.target_id)
        group = [x]
        is_content = x.place in CONTENT
        if b.action == "remove":
            if not is_content:
                raise HTTPException(400, "Bu şikâyette kaldırılacak içerik yok (sohbet şikâyeti)")
            if t is None:
                raise HTTPException(404, "Şikâyet edilen hesap bulunamadı")
            what = take_down(db, x.place, x.ref, t)
            x = db.get(Report, rid)
            notice(db, t.id, x.place + ".removed", REMOVED_TEXT[x.place].format(what=what[:60]), ref=x.ref)
            push_queue(db, t.id, "notice")
            # Aynı içerik hakkındaki bütün açık şikâyetler aynı kararla kapanır
            group = db.query(Report).filter(Report.place == x.place, Report.ref == x.ref, Report.status == "open").all()
        elif b.action == "ban":
            if t is None:
                raise HTTPException(404, "Şikâyet edilen hesap bulunamadı")
            if not t.banned:
                ban_user(db, t, BAN_TEXT_CONTENT if is_content else BAN_TEXT)
            # Hesap kapandı: bu kişi hakkındaki bütün açık şikâyetler aynı kararla kapanır
            group = db.query(Report).filter(Report.target_id == t.id, Report.status == "open").all()
        elif b.action == "warn" and t is not None:
            notice(db, t.id, "report.warn", WARN_CONTENT.get(x.place, WARN_TEXT), ref=x.id)
            push_queue(db, t.id, "notice")
        note = clean(b.note, 300)
        told: set[str] = set()
        for y in group:
            y.status, y.action, y.admin_note, y.closed_by, y.closed_at = "closed", b.action, note, a.username[:32], now()
            if y.reporter_id not in told:
                told.add(y.reporter_id)
                notice(db, y.reporter_id, "report.done", DONE_TEXT, ref=y.id)
                push_queue(db, y.reporter_id, "notice")
        audit(db, request, a, "report_close", "report", x.id, f"{b.action} · {len(group)} şikâyet · {note[:200]}")
        events.log(db, "report.close", x.target_id, x.id, action=b.action, by=a.username, n=len(group))
        db.commit()
        return rows_out(db, [x])[0]

    return r
