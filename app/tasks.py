"""GÖREV EŞLEŞMESİ — "Adres Listesi Paylaş" (1.14.0; kullanıcı kararları 22 Ekim 2026).

Kurallar (sunucu uygular; hepsi testli):
- Kuryeye iş verme komutunun adı "İşi Ver" değil, "ADRES LİSTESİ PAYLAŞ"tır. İşveren paylaştığı anda ilan (ya da liste) kuryeye
  BAĞLANIR ve göreve özel benzersiz 7 haneli GÖREV NO verilir (uygulamada GRV-xxxxxxx; ilk hane 0 olmaz).
- Kurye listeyi kabul edince görev aynı numarayla EŞLEŞİR (matched, "secure edildi"): iki tarafta aynı numara durur.
- BİREBİR: bir ilan / liste aynı anda TEK kuryeye bağlanır. Bir kuryenin aynı işverenden TEK açık görevi olur (teslimatlar bitmeden
  ikinci liste verilmez — eski "takip sürerken ikinci liste verilmez" kuralının sunucudaki karşılığı).
- CANLI TAKİP ŞARTI: işveren yalnız eşleşmiş görevi olan kuryeden takip izni ister (follow.py). Sıra: önce paylaş, sonra takip.
- Görev biter: kurye reddeder (declined), işveren vazgeçer (cancelled), kurye bırakır (stopped_courier), teslimatlar biter (done),
  ilan kaldırılır (job_closed), süre dolar (expired: kabulden — kabul edilmediyse paylaşımdan — TASK_MAX_HOURS, 24 saat sonra; askıda
  kalan görev çifti kilitlemesin diye). Biten görevin canlı takibi de kapanır. Biten görev TASK_KEEP_HOURS (48) saat sonra silinir.
- Görevde adres, alıcı adı, telefon YOK: adresler telefonlarda kalır (mesajla gider); burada yalnız başlık ve adres sayısı.
"""
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import events
from .auth import current_user
from .db import SessionLocal, get_db
from .limits import Window
from .models import Follow, Job, Task, User, now, uid

OPEN = ("shared", "matched")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    d = _utc(dt)
    return d.isoformat() if d else None


_BAD_RE = re.compile(r"@|https?:|www\.|(?:\d[\s().+-]*){10,}", re.IGNORECASE)  # e-posta, bağlantı, telefon gibi görünen


def tr_title(s: str) -> str:
    """Ekranda ad: her kelimenin baş harfi büyük, kalanı küçük (Türkçe i/İ, ı/I). Kayıtta küçük harfle yazılmış ad mesajda düzgün görünsün."""
    def word(w: str) -> str:
        if not w:
            return w
        head, rest = w[0], w[1:]
        head = {"i": "İ", "ı": "I"}.get(head, head.upper())
        rest = rest.replace("I", "ı").replace("İ", "i").lower()
        return head + rest
    return " ".join(word(w) for w in (s or "").split(" "))


def clean_title(s: str) -> str:
    """Görev başlığı (liste / ilan adı, çoğunlukla bölge): telefon, e-posta, bağlantı gibi görünen başlık atılır (yerine "Adres listesi")."""
    t = re.sub(r"\s+", " ", _CTRL_RE.sub(" ", s or "")).strip()
    return "" if _BAD_RE.search(t) else t[:160]


def new_task_no(db: Session) -> str:
    """Görevlerde olmayan 7 haneli numara (ilk hane 0 olmaz; sıralı değil, tahmin edilemez)."""
    for _ in range(50):
        n = str(secrets.randbelow(9_000_000) + 1_000_000)
        if db.query(Task.id).filter(Task.task_no == n).first() is None:
            return n
    raise RuntimeError("Boş görev no bulunamadı")


def end_task(db: Session, t: Task, reason: str, actor: str | None = None) -> None:
    """Görevi kapatır ve ona bağlı açık takibi de kapatır (commit ETMEZ)."""
    if t.status == "ended":
        return
    was = t.status
    t.status = "ended"
    t.end_reason = reason
    t.ended_at = now()
    events.log(db, "task.end", actor, t.id, reason=reason, was=was)
    from .follow import end_follow  # döngüsel içe aktarma olmasın (follow bu modülü içe aktarır)

    # Görev biterse ona bağlı canlı takip de biter. Takibin bitiş nedeni uygulamanın zaten tanıdığı adlardan seçilir.
    for f in db.query(Follow).filter(Follow.task_no == t.task_no, Follow.status.in_(("asked", "active"))).all():
        asked = f.status == "asked"
        why = {
            "done": "done",
            "expired": "expired",
            "ban": "ban",
            "stopped_courier": "declined" if asked else "stopped_courier",
            "declined": "declined" if asked else "stopped_courier",
        }.get(reason, "cancelled" if asked else "stopped_employer")  # işveren vazgeçti / ilan kaldırıldı
        end_follow(db, f, why, actor)


def check_task(db: Session, t: Task, max_hours: float) -> bool:
    """Açık görev hâlâ geçerli mi? Süresi dolduysa ya da taraflardan biri men edildiyse kapatır (commit çağırana ait). True: geçerli."""
    if t.status not in OPEN:
        return False
    start = _utc(t.matched_at or t.created_at)
    if start is not None and now() - start > timedelta(hours=max_hours):
        end_task(db, t, "expired")
        return False
    e, c = db.get(User, t.employer_id), db.get(User, t.courier_id)
    if e is None or c is None or e.banned or c.banned:
        end_task(db, t, "ban")
        return False
    return True


def end_tasks_for_job(db: Session, job_id: str, reason: str, actor: str | None = None) -> int:
    rows = db.query(Task).filter(Task.job_id == job_id, Task.status.in_(OPEN)).all()
    for t in rows:
        end_task(db, t, reason, actor)
    return len(rows)


def end_tasks_for_pair(db: Session, employer_id: str, courier_id: str, reason: str, actor: str | None = None) -> int:
    rows = db.query(Task).filter(Task.employer_id == employer_id, Task.courier_id == courier_id, Task.status.in_(OPEN)).all()
    for t in rows:
        end_task(db, t, reason, actor)
    return len(rows)


TASK_MAX_HOURS = 24.0  # main.py ortam değişkeniyle değiştirir (make_router)


def matched_task(db: Session, employer_id: str, courier_id: str) -> Task | None:
    """Canlı takip şartı: bu işveren ile kurye arasında EŞLEŞMİŞ (kuryenin kabul ettiği), süresi dolmamış görev."""
    t = (
        db.query(Task)
        .filter(Task.employer_id == employer_id, Task.courier_id == courier_id, Task.status == "matched")
        .order_by(Task.matched_at.desc())
        .first()
    )
    return t if (t is not None and check_task(db, t, TASK_MAX_HOURS)) else None


def people_of(db: Session, rows: list[Task]) -> dict[str, dict]:
    ids = {x for t in rows for x in (t.employer_id, t.courier_id)}
    if not ids:
        return {}
    q = db.query(User.id, User.name, User.public_no, User.map_icon).filter(User.id.in_(ids)).all()
    return {i: {"id": i, "name": n or "", "userNo": no or "", "icon": ic or ""} for i, n, no, ic in q}


def task_out(t: Task, viewer_id: str, people: dict[str, dict]) -> dict:
    blank = lambda i: {"id": i, "name": "", "userNo": "", "icon": ""}  # noqa: E731
    return {
        "taskNo": t.task_no,
        "status": t.status,
        "endReason": t.end_reason or "",
        "side": "employer" if viewer_id == t.employer_id else "courier",
        "ref": t.ref,
        "jobId": t.job_id or "",
        "title": t.title,
        "count": int(t.count or 0),
        "employer": people.get(t.employer_id) or blank(t.employer_id),
        "courier": people.get(t.courier_id) or blank(t.courier_id),
        "createdAt": _iso(t.created_at),
        "matchedAt": _iso(t.matched_at),
        "endedAt": _iso(t.ended_at),
    }


class ShareIn(BaseModel):
    courier_id: str = Field(alias="courierId", min_length=1, max_length=40)
    # İlan kimliği ya da telefondaki listenin anahtarı (aynı liste aynı anda iki kuryeye bağlanmasın diye)
    ref: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    job_id: str | None = Field(default=None, alias="jobId", max_length=40)
    title: str = Field(default="", max_length=160)
    count: int = Field(default=0, ge=0, le=5000)

    model_config = {"populate_by_name": True}


class EndIn(BaseModel):
    reason: str = Field(default="cancel", pattern=r"^(cancel|done)$")


def make_router(keep_hours: float = 48, max_hours: float = 24) -> APIRouter:
    global TASK_MAX_HOURS
    TASK_MAX_HOURS = max_hours
    r = APIRouter()
    share_win = Window(60, 3600)  # işveren başına saatte en çok 60 paylaşım

    def live(db: Session, t: Task | None) -> Task | None:
        return t if (t is not None and check_task(db, t, max_hours)) else None

    def view(db: Session, t: Task, user: User, **extra) -> dict:
        return {**task_out(t, user.id, people_of(db, [t])), **extra}

    def by_no(db: Session, no: str, user: User) -> Task:
        t = db.query(Task).filter(Task.task_no == no).first()
        if t is None or user.id not in (t.employer_id, t.courier_id):
            raise HTTPException(404, "Görev bulunamadı")  # başkasının görevinin varlığı açıklanmaz
        return t

    @r.post("/v1/tasks/share")
    def share(b: ShareIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """İşveren "Adres Listesi Paylaş": ilan / liste kuryeye bağlanır, görev no verilir. Adresler mesajla gider (sunucuya gelmez)."""
        if user.active_role != "employer":
            raise HTTPException(403, "Adres listesini yalnız işveren paylaşır")
        if b.courier_id == user.id:
            raise HTTPException(400, "Kendine liste paylaşamazsın")
        c = db.get(User, b.courier_id)
        if c is None or c.banned:
            raise HTTPException(404, "Kurye bulunamadı")
        if c.active_role != "courier":
            raise HTTPException(400, "Bu kişi şu an kurye modunda değil")
        job = None
        if b.job_id:
            job = db.get(Job, b.job_id)
            if job is None or job.employer_id != user.id:
                raise HTTPException(404, "İlan bulunamadı")
            if job.status != "open":
                raise HTTPException(400, "Bu ilan yayında değil")
        # Aynı ilan / liste: aynı anahtar ya da (ilansa) aynı ilan kimliği
        key = Task.ref == b.ref if job is None else or_(Task.ref == b.ref, Task.job_id == job.id)
        same = live(db, db.query(Task).filter(Task.employer_id == user.id, key, Task.status.in_(OPEN)).first())
        if same is not None:
            if same.courier_id == c.id:  # aynı kuryeye yeniden: aynı görev (yeni numara verilmez)
                return view(db, same, user, existing=True)
            other = people_of(db, [same]).get(same.courier_id, {})
            raise HTTPException(
                409,
                f"Bu ilan zaten {tr_title(other.get('name') or '') or 'başka bir'} kuryesine bağlı (GRV-{same.task_no}). Bir ilan aynı anda tek kuryeye verilir.",
            )
        busy = live(db, db.query(Task).filter(Task.employer_id == user.id, Task.courier_id == c.id, Task.status.in_(OPEN)).first())
        if busy is not None:
            raise HTTPException(
                409,
                f"Bu kuryenin bitmemiş bir görevi var (GRV-{busy.task_no}): teslimatlar bitince yeni liste paylaşılır.",
            )
        if not share_win.allow(user.id):
            db.commit()
            raise HTTPException(429, "Çok fazla paylaşım yaptın; biraz bekle")
        t = Task(
            id=uid(),
            task_no=new_task_no(db),
            employer_id=user.id,
            courier_id=c.id,
            job_id=job.id if job else None,
            ref=b.ref,
            title=clean_title(b.title) or clean_title(job.title if job else "") or "Adres listesi",
            count=b.count,
            status="shared",
        )
        db.add(t)
        events.log(db, "task.share", user.id, t.id, to=c.id, job=bool(job))
        try:
            db.commit()
        except IntegrityError:  # aynı anda aynı ilan / kurye için başka paylaşım açıldı
            db.rollback()
            raise HTTPException(409, "Bu ilan ya da kurye için az önce başka bir paylaşım yapıldı; yeniden dene")
        return view(db, t, user, existing=False)

    @r.post("/v1/tasks/{no}/accept")
    def accept(no: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kurye listeyi kabul eder: görev aynı numarayla EŞLEŞİR (iki tarafta GRV-xxxxxxx)."""
        t = by_no(db, no, user)
        if t.courier_id != user.id:
            raise HTTPException(403, "Görevi yalnız kurye kabul eder")
        if user.active_role != "courier":
            raise HTTPException(403, "Görevi kurye modunda kabul edebilirsin")
        ok = check_task(db, t, max_hours)
        if t.status == "matched" and ok:
            db.commit()
            return view(db, t, user)
        if t.status != "shared" or not ok:
            db.commit()
            raise HTTPException(410, "Bu görev artık geçerli değil")
        t.status = "matched"
        t.matched_at = now()
        events.log(db, "task.match", user.id, t.id)
        db.commit()
        return view(db, t, user)

    @r.post("/v1/tasks/{no}/decline")
    def decline(no: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        t = by_no(db, no, user)
        if t.courier_id != user.id:
            raise HTTPException(403, "Görevi yalnız kurye reddeder")
        if t.status in OPEN:
            end_task(db, t, "declined" if t.status == "shared" else "stopped_courier", user.id)
            db.commit()
        return view(db, t, user)

    @r.post("/v1/tasks/{no}/end")
    def end(no: str, b: EndIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Görevi bitir: kurye "done" (teslimatlar bitti) ya da vazgeçer; işveren vazgeçer. Bağlı canlı takip de kapanır."""
        t = by_no(db, no, user)
        if t.status in OPEN:
            done = bool(b and b.reason == "done")
            if user.id == t.courier_id:
                reason = "done" if done else "stopped_courier"
            else:
                reason = "cancelled"
            end_task(db, t, reason, user.id)
            db.commit()
        return view(db, t, user)

    @r.get("/v1/tasks/mine")
    def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Benim görevlerim (işveren ya da kurye): açık olanlar ve son TASK_KEEP_HOURS içinde bitenler."""
        cut = now() - timedelta(hours=keep_hours)
        rows = (
            db.query(Task)
            .filter(or_(Task.employer_id == user.id, Task.courier_id == user.id))
            .filter(or_(Task.status.in_(OPEN), Task.ended_at >= cut))
            .order_by(Task.created_at.desc())
            .limit(100)
            .all()
        )
        for t in rows:
            check_task(db, t, max_hours)
        db.commit()
        people = people_of(db, rows)
        return {"items": [task_out(t, user.id, people) for t in rows]}

    @r.get("/v1/tasks/{no}")
    def get_one(no: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        t = by_no(db, no, user)
        check_task(db, t, max_hours)
        db.commit()
        return view(db, t, user)

    # ------------------------------------------------------------------ temizlik
    def sweep(db: Session) -> int:
        """Süresi dolan ya da tarafı men edilen açık görevi kapatır; biten görevleri saklama süresinden sonra siler."""
        n = 0
        for t in db.query(Task).filter(Task.status.in_(OPEN)).all():
            if not check_task(db, t, max_hours):
                n += 1
        cut = now() - timedelta(hours=keep_hours)
        n += db.query(Task).filter(Task.status == "ended", Task.ended_at < cut).delete(synchronize_session=False)
        db.commit()
        return n

    def loop():
        while True:
            time.sleep(600)
            try:
                db = SessionLocal()
                try:
                    sweep(db)
                finally:
                    db.close()
            except Exception as e:  # temizlik hatası sunucuyu düşürmesin
                print("[görev] temizlik hatası:", type(e).__name__, flush=True)

    r.sweep = sweep  # type: ignore[attr-defined]
    r.limiters = (share_win,)  # type: ignore[attr-defined]
    r.start_cleaner = lambda: threading.Thread(target=loop, name="task-sweep", daemon=True).start()  # type: ignore[attr-defined]
    return r
