"""Teslim onayı ("Paketimi aldım") ve alıcı sayfası.

Akış:
1. Kurye bir durak için bağlantı üretir (POST /v1/handoffs). Sağlayıcı bağlıysa bağlantı alıcıya SMS / e-postayla SUNUCUDAN gider ve kuryeye
   gösterilmez; sağlayıcı yoksa (test modu) bağlantı kuryeye verilir, kendi SMS / WhatsApp uygulamasıyla iletir ya da QR gösterir.
2. Alıcı bağlantıyı (uygulama kurulumu olmadan, tarayıcıda) açar: kapının yerini pinler, iletişim bilgisini düzeltir.
3. Kurye hedefe ulaşınca (POST /v1/handoffs/{id}/arrived) alıcı sayfasında "Paketimi aldım" kutusu açılır; alıcı işaretleyince kuryenin
   ekranında "Alıcı onayladı ✓" görünür. Bu bir UYARIDIR, kuryeyi kilitlemez.

Gizlilik / saklama (kullanıcı kararı):
- Alıcının ADI hiç saklanmaz. Telefon / e-posta yalnız bağlantıyı göndermek ve sayfada düzeltmek içindir; "Paketimi aldım" işaretlenince hemen silinir.
- Geçmiş tutulmaz: her kayıt en geç HANDOFF_TTL_HOURS (varsayılan 48) saat sonra tamamen silinir (istek anında + arka planda).
- Alıcının kapı pini ortak kapı haritasına yalnız halka açık adres parçalarıyla (sokak, kapı no, site adı) ve konumla yazılır; telefon / e-posta oraya girmez.
- Alıcı pininin ağırlığı: bağlantı alıcıya SUNUCUDAN ulaştıysa (kurye görmediyse) 2 (alıcının kendi sözü); kurye bağlantıyı gördüyse
  (test modu, QR, paylaşım) 1 — çünkü kurye kendi bağlantısıyla "alıcı" gibi pin koyabilir.
- Değerlendirme (Dilim 4): kurye hedefe ulaştıktan sonra alıcı aynı bağlantıdan kuryeyi 5 yıldızla puanlar, standart soruları yanıtlar,
  yorum yazar (POST /a/{kod}/review). Hesap gerekmez; yalnız gerçek bir teslim bağlantısının kodu ile. Kurye teslimi bitirince (DELETE)
  kayıt hemen silinmez: telefon / e-posta silinir, kayıt kuryeden gizlenir, alıcı yorum yazabilsin diye süresi dolana kadar (en çok
  HANDOFF_REVIEW_HOURS, varsayılan 24 saat) kalır. 48 saatlik üst sınır değişmez.

Süreç takibi (1.18.0, kullanıcı isteği 28 Ekim 2026 — Migros'taki gibi üç parçalı çubuk):
- Alıcı sayfasında Hazırlanıyor → Yolda → Teslim edildi. Kaydı İŞVEREN açtıysa (adres doğrulama bağlantısı ya da "📦 Yola çıktı")
  "Hazırlanıyor"la başlar; işveren "📦 Yola çıktı" deyince (ya da kurye hedefe ulaşınca) "Yolda" olur. Kuryenin kendi açtığı kayıt
  (paket zaten kuryede) doğrudan "Yolda"dır; liste bir işverenden geldiyse yola çıkışı yine işveren söyler. Alıcı "Paketimi aldım"
  deyince ya da kurye "Teslim edildi" deyince "Teslim edildi".
- İşverenin açtığı kayda, listeyi AÇIK GÖREVLE (GRV-…) alan kurye bağlanır: aynı bağlantı, aynı alıcı sayfası; işveren durumu görmeye
  devam eder. Görevi olmayan kurye başkasının kaydını göremez.
- Tarayıcı bildirimi: alıcı izin verirse "Kargonuz yolda" (yola çıkınca) ve "Kuryeniz kapıda" (kurye ulaşınca) bildirimi gider
  (webpush.py). Abonelik yalnız o teslim içindir; teslim edilince / kayıt kapanınca / en geç 48 saatte silinir.
"""
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import events, notify, reviews, webpush
from . import tasks as task_mod
from .pushes import queue as push_queue
from .auth import current_user, norm_email, norm_phone
from .db import SessionLocal, get_db
from .doors import dist_m, looks_personal, tidy
from .limits import Window, client_ip
from .models import Handoff, HandoffPush, Review, Task, User, now, uid

SMS_TEXT = 'Paketiniz teslim edilene kadar bu mesajı silmeyiniz. Teslim almadan önce bağlantıdan "Paketimi aldım" kutusunu işaretleyiniz; işaretlenmeden teslim edilmiş sayılmaz. {link}'
EMAIL_SUBJECT = 'Paketiniz yolda: "Paketimi aldım" bağlantısı'
EMAIL_TEXT = (
    "Merhaba,\n\nKuryeniz paketinizi getiriyor.\n\n"
    'Paketiniz teslim edilene kadar bu mesajı silmeyiniz. Teslim almadan önce bağlantıdan "Paketimi aldım" kutusunu işaretleyiniz; işaretlenmeden teslim edilmiş sayılmaz.\n\n'
    "{link}\n\nBu bağlantı ve girdiğiniz bilgiler en geç {hours} saat içinde silinir."
)

# Konum talebi (1.12.0): kurye kartındaki "✉️ Konum Talebi Gönder". Aynı alıcı bağlantısı, "Kapının yeri" sekmesi açık gelir (#kapi).
LOC_SMS_TEXT = 'Kuryeniz paketinizi doğru adrese getirmek için konumunuzu doğrulamanızı istiyor. Bağlantıdan kapınızın yerini haritada işaretleyin: {link}'
LOC_EMAIL_SUBJECT = 'Kuryeniz konumunuzu doğrulamanızı istiyor'
LOC_EMAIL_TEXT = (
    "Merhaba,\n\nKuryeniz paketinizi doğru adrese getirmek için konumunuzu doğrulamanızı istiyor.\n"
    "Aşağıdaki bağlantıdan kapınızın yerini haritada işaretleyin.\n\n"
    "{link}\n\nBu bağlantı ve girdiğiniz bilgiler en geç {hours} saat içinde silinir."
)
# 1.18.0: işverenin "📦 Yola çıktı"sı. Alıcının bildirim izni yoksa bağlantı bu metinle gider (SMS kapalıyken işveren WhatsApp ile iletir)
DEPART_SMS_TEXT = 'Kargonuz yolda. Süreci bağlantıdan takip edin; teslim almadan önce "Paketimi aldım" kutusunu işaretleyin: {link}'
DEPART_EMAIL_SUBJECT = "Kargonuz yolda"
DEPART_EMAIL_TEXT = (
    "Merhaba,\n\nKargonuz yola çıktı.\n\n"
    'Süreci aşağıdaki bağlantıdan takip edebilirsiniz. Teslim almadan önce "Paketimi aldım" kutusunu işaretleyiniz; işaretlenmeden teslim edilmiş sayılmaz.\n\n'
    "{link}\n\nBu bağlantı ve girdiğiniz bilgiler en geç {hours} saat içinde silinir."
)
# Tarayıcı bildirimleri (alıcı izin verdiyse): başlık, metin
PUSH_DEPART = ("Kargonuz yolda", "Kuryeniz paketinizi getiriyor. Takip için dokunun.")
PUSH_ARRIVED = ("Kuryeniz kapıda", 'Teslim almadan önce "Paketimi aldım" kutusunu işaretleyin.')
PURPOSES = ("handoff", "location", "depart")
MAX_PUSH = 3  # bir teslim için en çok 3 tarayıcı (ör. alıcının telefonu + bilgisayarı)

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{10,24}$")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_PIN_FROM_GUESS_M = 5000.0  # alıcı pini sistemin tahmininden en çok bu kadar uzak olabilir
MAX_SENDS = 3


def utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    d = utc(dt)
    return d.isoformat() if d else None


def try_phone(raw: str) -> str:
    if not (raw or "").strip():
        return ""
    try:
        return norm_phone(raw)
    except HTTPException:
        return ""


def try_email(raw: str) -> str:
    if not (raw or "").strip():
        return ""
    try:
        return norm_email(raw)
    except HTTPException:
        return ""


def public_base(request: Request) -> str:
    env = os.getenv("PUBLIC_BASE", "").strip().rstrip("/") or os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if env:
        return env
    host = (request.headers.get("host") or "").strip()
    if not host:
        return ""
    local = host.startswith(("localhost", "127.", "0.0.0.0", "[::1]"))
    return f"{'http' if local else 'https'}://{host}"


class HandoffIn(BaseModel):
    ref: str = Field(min_length=1, max_length=64)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=254)
    lat: float = Field(ge=34.0, le=43.0)
    lon: float = Field(ge=25.0, le=45.5)
    province: str = Field(default="", max_length=80)
    district: str = Field(default="", max_length=100)
    neighborhood: str = Field(default="", max_length=120)
    street: str = Field(default="", max_length=160)
    site: str = Field(default="", max_length=120)
    door: str = Field(default="", max_length=40)
    send: bool = False  # bağlantıyı şimdi alıcıya gönder (sağlayıcı varsa SMS / e-posta)
    # "handoff": teslim onayı ("Paketimi aldım") metni; "location": konum talebi metni, sayfa "Kapının yeri" sekmesiyle açılır
    purpose: str = Field(default="handoff", pattern="^(handoff|location)$")
    # 1.18.0: kuryenin durağı bir işverenin listesinden geldiyse o işverenin kimliği (yalnız açık görev varsa dikkate alınır)
    employer: str = Field(default="", max_length=64)

    model_config = {"populate_by_name": True}


class DoneIn(BaseModel):
    ref: str = Field(min_length=1, max_length=64)


class PushKeys(BaseModel):
    p256dh: str = Field(default="", max_length=128)
    auth: str = Field(default="", max_length=48)


class PushIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1024)
    keys: PushKeys = Field(default_factory=PushKeys)


class PushOffIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)


class PinIn(BaseModel):
    lat: float = Field(ge=34.0, le=43.0)
    lon: float = Field(ge=25.0, le=45.5)
    accuracy_m: float | None = Field(default=None, alias="accuracyM", ge=0, le=1_000_000)

    model_config = {"populate_by_name": True}


class ContactIn(BaseModel):
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)


class ReceivedIn(BaseModel):
    checked: bool = True


class ReviewIn(BaseModel):
    stars: int
    app_stars: int | None = Field(default=None, alias="appStars")
    tags: list[str] = Field(default_factory=list, max_length=12)
    address_ok: bool | None = Field(default=None, alias="addressOk")
    comment: str = Field(default="", max_length=600)

    model_config = {"populate_by_name": True}


def make_router(add_pin, ttl_hours: float = 48, daily_max: int = 300, review_hours: float = 24) -> APIRouter:
    """add_pin(db, item, recipient_key, trusted): ortak kapı haritasına alıcı pini ekler (doors.make_router(...).add_recipient_pin)."""
    r = APIRouter()
    ip_read = Window(240, 60)  # alıcı sayfası: IP başı dakikada 240 okuma
    ip_write = Window(40, 60)  # ... 40 yazma
    token_write = Window(60, 3600)  # bağlantı başı saatte 60 yazma
    last_purge = [0.0]

    # ------------------------------------------------------------------ yardımcılar
    def drop_pushes(db: Session, ids: list[str]) -> None:
        """Teslimin tarayıcı bildirimi aboneliklerini siler (teslim edildi / kapandı / kayıt silindi)."""
        if ids:
            db.query(HandoffPush).filter(HandoffPush.handoff_id.in_(ids)).delete(synchronize_session=False)

    def purge(db: Session) -> int:
        """Süresi dolan kayıtları SİLER (telefon, e-posta, pin, durum, bildirim aboneliği — hepsi)."""
        t = now()
        db.query(HandoffPush).filter(
            or_(HandoffPush.expires_at < t, HandoffPush.handoff_id.in_(select(Handoff.id).where(Handoff.expires_at < t)))
        ).delete(synchronize_session=False)
        n = db.query(Handoff).filter(Handoff.expires_at < t).delete(synchronize_session=False)
        db.commit()
        return n

    def maybe_purge(db: Session):
        if time.time() - last_purge[0] > 60:
            last_purge[0] = time.time()
            purge(db)

    def mine(db: Session, user: User, hid: str) -> Handoff:
        """Kaydın sahibi (kurye; kuryesi bağlanmamış işveren kaydında işverenin kendisi)."""
        h = db.get(Handoff, hid)
        if h is None or h.courier_id != user.id or utc(h.expires_at) < now() or h.closed_at is not None:
            raise HTTPException(404, "Bağlantı bulunamadı")
        return h

    def managed(db: Session, user: User, hid: str) -> Handoff:
        """Sahibi ya da kaydı açan işveren (bağlantıyı yeniden iletmek, "📦 Yola çıktı")."""
        h = db.get(Handoff, hid)
        if h is None or user.id not in (h.courier_id, h.employer_id) or utc(h.expires_at) < now() or h.closed_at is not None:
            raise HTTPException(404, "Bağlantı bulunamadı")
        return h

    def open_q(db: Session):
        return db.query(Handoff).filter(Handoff.expires_at > now(), Handoff.closed_at.is_(None))

    def task_employers(db: Session, user: User) -> set[str]:
        """Bu kuryeye AÇIK görevle (GRV-…) liste vermiş işverenler: yalnız onların kayıtlarına bağlanılır."""
        rows = db.query(Task).filter(Task.courier_id == user.id, Task.status.in_(task_mod.OPEN)).all()
        return {t.employer_id for t in rows if task_mod.check_task(db, t, task_mod.TASK_MAX_HOURS)}

    def open_task(db: Session, employer_id: str, courier_id: str) -> bool:
        return (
            db.query(Task.id).filter(Task.employer_id == employer_id, Task.courier_id == courier_id, Task.status.in_(task_mod.OPEN)).first()
            is not None
        )

    def join_open(db: Session, user: User, refs: list[str]) -> dict[str, Handoff]:
        """Kurye, açık görevle aldığı listedeki durağın İŞVERENCE açılmış kaydına bağlanır (aynı bağlantı, aynı alıcı sayfası).
        Kayıt başka bir kuryenin açık göreviyle bağlıysa dokunulmaz. Commit çağırana ait."""
        if not refs or user.active_role == "employer":
            return {}
        emps = task_employers(db, user)
        if not emps:
            return {}
        rows = open_q(db).filter(Handoff.ref.in_(refs), Handoff.employer_id.in_(emps)).order_by(Handoff.created_at.desc()).all()
        got: dict[str, Handoff] = {}
        for h in rows:
            if h.ref in got:
                continue
            if h.courier_id != user.id and h.courier_id != h.employer_id and open_task(db, h.employer_id, h.courier_id):
                continue
            if h.courier_id != user.id:
                h.courier_id = user.id
                events.log(db, "handoff.join", user.id, h.id)
            got[h.ref] = h
        return got

    def find_open(db: Session, user: User, ref: str, join: bool = True) -> Handoff | None:
        """Bu durağın açık kaydı: kendi kaydım, açtığım işveren kaydı ya da (kuryeysem) görevle bağlanabildiğim işveren kaydı."""
        h = (
            open_q(db)
            .filter(Handoff.ref == ref, or_(Handoff.courier_id == user.id, Handoff.employer_id == user.id))
            .order_by(Handoff.created_at.desc())
            .first()
        )
        if h is not None or not join:
            return h
        return join_open(db, user, [ref]).get(ref)

    def stage_of(h: Handoff) -> str:
        """Alıcı sayfasındaki çubuk: preparing (Hazırlanıyor) → onway (Yolda) → delivered (Teslim edildi)."""
        if h.status == "received" or h.delivered_at is not None:
            return "delivered"
        if h.departed_at is not None or h.status == "arrived":
            return "onway"
        return "preparing"

    def push_counts(db: Session, ids: list[str]) -> dict[str, int]:
        if not ids:
            return {}
        rows = db.query(HandoffPush.handoff_id, func.count(HandoffPush.id)).filter(HandoffPush.handoff_id.in_(ids)).group_by(HandoffPush.handoff_id).all()
        return {a: int(b) for a, b in rows}

    def push_items(db: Session, h: Handoff) -> list[tuple[str, str, str, str]]:
        rows = db.query(HandoffPush).filter(HandoffPush.handoff_id == h.id).all()
        return [(x.id, x.endpoint, x.p256dh, x.auth) for x in rows]

    def forget_pushes(ids: list[str]) -> None:
        """Bildirim servisi "bu abonelik yok" dedi (404 / 410): kaydı sil (arka plan iş parçacığından çağrılır)."""
        db = SessionLocal()
        try:
            db.query(HandoffPush).filter(HandoffPush.id.in_(ids)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def send_push(h: Handoff, items: list[tuple[str, str, str, str]], msg: tuple[str, str]) -> None:
        """Commit'ten SONRA çağrılır (gönderim arka planda; içerik şifreli, yalnız başlık + metin + sayfa adresi)."""
        if items:
            title, body = msg
            webpush.dispatch(items, {"title": title, "body": body, "url": f"/a/{h.token}", "tag": "rota-" + h.id}, on_gone=forget_pushes)

    def link_of(request: Request, h: Handoff, purpose: str = "handoff") -> str:
        base = f"{public_base(request)}/a/{h.token}"
        return base + "#kapi" if purpose == "location" else base

    def share_text(link: str, purpose: str = "handoff") -> str:
        t = LOC_SMS_TEXT if purpose == "location" else DEPART_SMS_TEXT if purpose == "depart" else SMS_TEXT
        return t.format(link=link)

    def out(request: Request, h: Handoff, link: str | None = None, sent: dict | None = None, purpose: str = "handoff", push_n: int = 0) -> dict:
        d = {
            "id": h.id,
            "ref": h.ref,
            "status": h.status,
            "mode": h.mode,
            "phoneOk": bool(h.phone),
            "emailOk": bool(h.email),
            "providers": {"sms": notify.sms_enabled(), "email": notify.email_enabled()},
            "sendCount": h.send_count,
            "expiresAt": iso(h.expires_at),
            "arrivedAt": iso(h.arrived_at),
            "receivedAt": iso(h.received_at),
            "pin": {"lat": h.pin_lat, "lon": h.pin_lon} if h.pin_at else None,
            # 1.18.0: süreç takibi
            "stage": stage_of(h),
            "departedAt": iso(h.departed_at),
            "deliveredAt": iso(h.delivered_at),
            "pushOn": push_n > 0,  # alıcı tarayıcı bildirimine izin verdi
            "closed": h.closed_at is not None,
        }
        if sent is not None:
            d["sent"] = sent
        if link:
            d["link"] = link
            d["shareText"] = share_text(link, purpose)
        return d

    def deliver(request: Request, h: Handoff, purpose: str = "handoff") -> dict:
        """Bağlantıyı alıcıya sunucudan gönderir (sağlayıcı varsa). Dönen: {'sms': bool, 'email': bool}."""
        link = link_of(request, h, purpose)
        if purpose == "location":
            subject, body = LOC_EMAIL_SUBJECT, LOC_EMAIL_TEXT
        elif purpose == "depart":
            subject, body = DEPART_EMAIL_SUBJECT, DEPART_EMAIL_TEXT
        else:
            subject, body = EMAIL_SUBJECT, EMAIL_TEXT
        sms = bool(h.phone.startswith("05") and notify.sms_enabled() and notify.send_sms(h.phone, share_text(link, purpose)))
        mail = bool(h.email and notify.email_enabled() and notify.send_email(h.email, subject, body.format(link=link, hours=int(ttl_hours))))
        h.send_count = (h.send_count or 0) + 1
        if sms or mail:
            h.mode = "sms" if sms else "email"
        return {"sms": sms, "email": mail}

    def scrub_contact(h: Handoff):
        h.phone = ""
        h.email = ""

    # ------------------------------------------------------------------ kurye uçları
    @r.post("/v1/handoffs")
    def create(b: HandoffIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Bir durak için "Paketimi aldım" bağlantısı üretir (aynı durak için tekrar çağrılırsa aynısı döner)."""
        maybe_purge(db)
        ref = tidy(b.ref, 64)
        if not ref:
            raise HTTPException(422, "Durak kimliği boş")
        # 1.18.0: kendi kaydım ya da açtığım işveren kaydı; kuryeysem açık görevle aldığım listenin işverence açılmış kaydına bağlanırım
        h = find_open(db, user, ref)
        parts = {
            "province": tidy(b.province, 60), "district": tidy(b.district, 80), "neighborhood": tidy(b.neighborhood, 100),
            "street": tidy(b.street, 120), "site": tidy(b.site, 60), "door": tidy(b.door, 40),
        }
        for k, v in list(parts.items()):
            if looks_personal(v):  # telefon / e-posta / bağlantı gibi görünen parça kapı haritasına hiç gitmesin
                parts[k] = ""
        phone, email = try_phone(b.phone), try_email(b.email)
        if h is None:
            since = now() - timedelta(hours=24)
            if db.query(Handoff).filter(Handoff.courier_id == user.id, Handoff.created_at > since).count() >= daily_max:
                raise HTTPException(429, "Günlük bağlantı sınırı doldu")
            # 1.18.0: işverenin açtığı kayıt "Hazırlanıyor"la başlar (yola çıkışı işveren söyler). Kuryenin kendi kaydı doğrudan "Yolda"dır
            # (paket kuryede); durak bir işverenin listesinden geldiyse (açık görevle) o işverene bağlanır ve yola çıkışı yine işveren söyler.
            emp = None
            if user.active_role == "employer":
                emp = user.id
            elif b.employer:
                hint = tidy(b.employer, 64)
                if hint and hint != user.id and hint in task_employers(db, user):
                    emp = hint
            h = Handoff(
                id=uid(), token=secrets.token_urlsafe(12), courier_id=user.id, employer_id=emp, ref=ref, phone=phone, email=email,
                lat=b.lat, lon=b.lon, expires_at=now() + timedelta(hours=ttl_hours), departed_at=None if emp else now(), **parts,
            )
            db.add(h)
            events.log(db, "handoff.create", user.id, h.id)
        elif h.status != "received":  # güncel bilgiyi yaz (alıcı onayladıktan sonra telefon / e-posta tekrar yazılmaz)
            h.lat, h.lon = b.lat, b.lon
            for k, v in parts.items():
                if v:
                    setattr(h, k, v)
            if phone:
                h.phone = phone
            if email:
                h.email = email
        db.flush()
        sent = None
        link = None
        if b.send and h.status != "received":
            if h.send_count >= MAX_SENDS:
                raise HTTPException(429, f"Bağlantı en çok {MAX_SENDS} kez gönderilebilir")
            sent = deliver(request, h, b.purpose)
            if not (sent["sms"] or sent["email"]):  # gönderilemedi / sağlayıcı yok: kurye kendisi iletsin
                link = link_of(request, h, b.purpose)
                h.link_seen = True
            if b.purpose == "location":
                events.log(db, "handoff.locreq", user.id, h.id)
        db.commit()
        db.refresh(h)
        return out(request, h, link=link, sent=sent, purpose=b.purpose, push_n=push_counts(db, [h.id]).get(h.id, 0))

    @r.get("/v1/handoffs/{hid}/link")
    def get_link(hid: str, request: Request, purpose: str = Query(default="handoff", pattern="^(handoff|location|depart)$"),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Bağlantıyı kuryeye / kaydı açan işverene gösterir (QR, paylaşım). Gördükleri için alıcı pininin ağırlığı 1'e düşer."""
        h = managed(db, user, hid)
        h.link_seen = True
        db.commit()
        link = link_of(request, h, purpose)
        return {"link": link, "shareText": share_text(link, purpose)}

    @r.post("/v1/handoffs/{hid}/send")
    def send_again(hid: str, request: Request, purpose: str = Query(default="handoff", pattern="^(handoff|location)$"),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
        h = managed(db, user, hid)
        n = push_counts(db, [h.id]).get(h.id, 0)
        if h.status == "received":
            return out(request, h, sent={"sms": False, "email": False}, push_n=n)
        if h.send_count >= MAX_SENDS:
            raise HTTPException(429, f"Bağlantı en çok {MAX_SENDS} kez gönderilebilir")
        sent = deliver(request, h, purpose)
        link = None
        if not (sent["sms"] or sent["email"]):
            link = link_of(request, h, purpose)
            h.link_seen = True
        db.commit()
        return out(request, h, link=link, sent=sent, purpose=purpose, push_n=n)

    @r.post("/v1/handoffs/{hid}/depart")
    def depart(hid: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """1.18.0 — "📦 Yola çıktı" (işveren; kurye de çağırabilir): alıcı sayfasındaki çubuk "Yolda"ya geçer. Alıcı bildirime izin
        verdiyse "Kargonuz yolda" tarayıcı bildirimi gider; vermediyse SMS / e-posta hizmeti varsa oradan, yoksa bağlantı geri döner
        (işveren WhatsApp ile iletir). Tekrar basılırsa bildirim tekrar GİTMEZ."""
        h = managed(db, user, hid)
        notified = {"push": 0, "sms": False, "email": False}
        items: list = []
        link = None
        if h.status != "received" and h.delivered_at is None:
            if h.departed_at is None:
                h.departed_at = now()
                events.log(db, "handoff.depart", user.id, h.id)
                items = push_items(db, h)
                notified["push"] = len(items)
                provider = (notify.sms_enabled() and h.phone.startswith("05")) or (notify.email_enabled() and bool(h.email))
                if not items and provider and h.send_count < MAX_SENDS:
                    sent = deliver(request, h, "depart")
                    notified["sms"], notified["email"] = sent["sms"], sent["email"]
            if not (notified["push"] or notified["sms"] or notified["email"]):  # hiçbir yoldan ulaşmadı: bağlantıyı işveren iletsin
                link = link_of(request, h)
                h.link_seen = True
        db.commit()
        db.refresh(h)
        send_push(h, items, PUSH_DEPART)
        d = out(request, h, link=link, purpose="depart", push_n=push_counts(db, [h.id]).get(h.id, 0))
        d["notified"] = notified
        return d

    @r.post("/v1/handoffs/{hid}/arrived")
    def arrived(hid: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        h = mine(db, user, hid)
        items: list = []
        if h.status == "created":
            h.status = "arrived"
            h.arrived_at = now()
            if h.departed_at is None:  # hedefe ulaşan paket yoldadır (işveren "Yola çıktı" demeyi unuttuysa)
                h.departed_at = h.arrived_at
            events.log(db, "handoff.arrived", user.id, h.id)
            items = push_items(db, h)
            db.commit()
            send_push(h, items, PUSH_ARRIVED)
        return out(request, h, push_n=push_counts(db, [h.id]).get(h.id, 0))

    @r.post("/v1/handoffs/done")
    def done(b: DoneIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """1.18.0 — kurye "Teslim edildi" dedi: alıcı sayfasındaki çubuk "Teslim edildi"ye varır. Alıcı kutuyu işaretlemediyse yine
        işaretleyebilir (puan için alıcı onayı sonradan da sayılır). Bildirim aboneliği artık gerekmez: silinir."""
        ref = tidy(b.ref, 64)
        h = find_open(db, user, ref) if (ref and user.active_role != "employer") else None
        if h is None or h.courier_id != user.id:
            db.commit()
            return {"ok": True, "found": False}
        if h.status != "received" and h.delivered_at is None:
            h.delivered_at = now()
            if h.departed_at is None:
                h.departed_at = h.delivered_at
            events.log(db, "handoff.done", user.id, h.id)
            if h.employer_id and h.employer_id != h.courier_id:  # 1.19.0: işverene "✅ Bir paket teslim edildi" (2 dk'da bir)
                push_queue(db, h.employer_id, "delivered", thread="d")
        drop_pushes(db, [h.id])
        db.commit()
        db.refresh(h)
        return {"ok": True, "found": True, "item": out(request, h)}

    @r.get("/v1/handoffs")
    def statuses(request: Request, refs: str = Query(default="", max_length=4000), user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Durakların durumu (virgülle ayrılmış `refs`, en çok 50): bağlantı var mı, aşama, alıcı onayladı mı, yeni pin var mı.
        Kurye: kendi kayıtları + açık görevle aldığı listenin işverence açılmış kayıtları (bağlanır). İşveren: açtığı kayıtlar (kurye
        bağlansa da); kurye teslimi bitirip kaydı kapatsa da "Teslim edildi"yi görsün diye kapanmış kayıtları da (süresi dolana kadar)."""
        maybe_purge(db)
        wanted = [tidy(x, 64) for x in refs.split(",") if x.strip()][:50]
        if not wanted:
            return {"items": []}
        rows = (
            db.query(Handoff)
            .filter(
                Handoff.ref.in_(wanted),
                Handoff.expires_at > now(),
                or_(
                    (Handoff.courier_id == user.id) & Handoff.closed_at.is_(None),
                    Handoff.employer_id == user.id,
                ),
            )
            .order_by(Handoff.created_at.desc())
            .all()
        )
        seen: set[str] = set()
        keep: list[Handoff] = []
        for h in rows:
            if h.ref in seen:
                continue
            seen.add(h.ref)
            keep.append(h)
        missing = [x for x in wanted if x not in seen]
        for ref, h in join_open(db, user, missing).items():
            seen.add(ref)
            keep.append(h)
        db.commit()
        counts = push_counts(db, [h.id for h in keep])
        return {"items": [out(request, h, push_n=counts.get(h.id, 0)) for h in keep]}

    @r.delete("/v1/handoffs/{hid}")
    def remove(hid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kurye teslimi bitirdi / durağı kaldırdı. Hedefe hiç ulaşılmadıysa kayıt tamamen silinir. Ulaşıldıysa kayıt KAPANIR:
        telefon / e-posta hemen silinir, kurye için görünmez olur, alıcı yorum yazabilsin diye en çok `review_hours` saat daha kalır.
        Bildirim aboneliği her iki durumda da hemen silinir."""
        h = db.get(Handoff, hid)
        if h is None or h.courier_id != user.id:
            return {"ok": True}
        drop_pushes(db, [h.id])
        if h.status == "created" or h.closed_at is not None:
            db.delete(h)
        else:
            scrub_contact(h)
            h.closed_at = now()
            h.expires_at = min(utc(h.expires_at), now() + timedelta(hours=review_hours))
            events.log(db, "handoff.closed", user.id, h.id)
        db.commit()
        return {"ok": True}

    # ------------------------------------------------------------------ alıcı (herkese açık, bağlantı kodu ile)
    def by_token(request: Request, token: str, db: Session, write: bool = False) -> Handoff:
        ip = client_ip(request)
        if not (ip_write if write else ip_read).allow(ip):
            raise HTTPException(429, "Çok sık deneme, biraz bekleyin")
        if not TOKEN_RE.match(token or ""):
            raise HTTPException(404, "Bağlantı geçersiz")
        if write and not token_write.allow(token):
            raise HTTPException(429, "Çok sık deneme, biraz bekleyin")
        maybe_purge(db)
        h = db.query(Handoff).filter(Handoff.token == token).first()
        if h is None:
            raise HTTPException(404, "Bağlantı geçersiz ya da süresi dolmuş")
        if utc(h.expires_at) < now():
            drop_pushes(db, [h.id])
            db.delete(h)
            db.commit()
            raise HTTPException(404, "Bağlantı geçersiz ya da süresi dolmuş")
        return h

    def street_label(h: Handoff) -> str:
        s = h.street or ""
        return f"{s} No:{h.door}".strip() if (s and h.door) else (s or (f"No:{h.door}" if h.door else ""))

    # 1.18.0: tek bir hizmet çalışanı (service worker) bütün alıcı sayfalarına (/a/) bakar: yalnız bildirimi gösterir, sayfa SAKLAMAZ.
    # "/a/{token}"dan ÖNCE tanımlanmalı (yoksa "sw.js" bir kod sanılır).
    @r.get("/a/sw.js")
    def service_worker():
        return FileResponse(
            os.path.join(STATIC_DIR, "alici-sw.js"), media_type="text/javascript; charset=utf-8", headers={"Service-Worker-Allowed": "/a/"}
        )

    @r.get("/a/{token}/manifest.webmanifest")
    def manifest(token: str):
        """Ana ekrana eklenince (iPhone'da bildirim için şart) açılacak sayfa bu teslimin sayfasıdır. Veritabanına bakılmaz."""
        if not TOKEN_RE.match(token or ""):
            raise HTTPException(404, "Bulunamadı")
        data = {
            "id": f"/a/{token}",
            "name": "Kargom · Rota",
            "short_name": "Kargom",
            "description": "Paket teslimi: süreç takibi ve “Paketimi aldım”.",
            "lang": "tr",
            "start_url": f"/a/{token}",
            "scope": "/a/",
            "display": "standalone",
            "background_color": "#FFFFFF",
            "theme_color": "#0D1B2A",
            "icons": [
                {"src": "/static/alici-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": "/static/alici-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": "/static/alici-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        }
        return JSONResponse(data, media_type="application/manifest+json")

    @r.get("/a/{token}")
    def page(token: str):
        """Alıcı sayfası (kurulum gerektirmez). Sayfa kendi içinde durumu sorar; geçersiz kod bu yanıttan anlaşılmaz
        (veritabanına bakılmaz; yalnız kod BİÇİMİ uygunsa ana ekran bilgisi eklenir)."""
        with open(os.path.join(STATIC_DIR, "alici.html"), encoding="utf-8") as f:
            html = f.read()
        tag = f'<link rel="manifest" href="/a/{token}/manifest.webmanifest">' if TOKEN_RE.match(token or "") else ""
        return HTMLResponse(html.replace("<!--MANIFEST-->", tag))

    def can_review(h: Handoff) -> bool:
        """Kurye hedefe ulaştıktan / teslim ettikten sonra; kuryesi bağlanmamış işveren kaydında değerlendirme yok."""
        if h.employer_id is not None and h.courier_id == h.employer_id:
            return False
        return h.status in ("arrived", "received") or h.delivered_at is not None

    def review_state(db: Session, h: Handoff) -> dict:
        """Değerlendirme sekmesi: kurye hedefe ulaştıktan sonra açılır. Daha önce yazılmışsa içeriği geri verilir (düzeltebilsin)."""
        rv = db.query(Review).filter(Review.handoff_id == h.id).first()
        data = None
        if rv is not None:
            a = reviews.answers_of(rv)
            data = {"stars": rv.stars, "appStars": rv.app_stars, "tags": a.get("tags") or [], "addressOk": a.get("addressOk"), "comment": rv.comment or ""}
        return {"allowed": can_review(h), "done": rv is not None, "data": data}

    @r.get("/a/{token}/state")
    def state(token: str, request: Request, db: Session = Depends(get_db)):
        h = by_token(request, token, db)
        st = stage_of(h)
        return {
            "status": h.status,
            # Kurye kapıda ya da (1.18.0) kurye "Teslim edildi" dedi: alıcı kutuyu işaretleyebilir
            "canConfirm": h.status != "received" and h.closed_at is None and (h.status == "arrived" or h.delivered_at is not None),
            "received": h.status == "received",
            "closed": h.closed_at is not None,
            "review": review_state(db, h),
            "address": street_label(h),
            "area": ", ".join(x for x in (h.neighborhood, h.district) if x),
            "site": h.site,
            "start": {"lat": h.lat, "lon": h.lon},
            "pin": {"lat": h.pin_lat, "lon": h.pin_lon} if h.pin_at else None,
            "phone": h.phone,
            "email": h.email,
            "expiresAt": iso(h.expires_at),
            # 1.18.0: süreç çubuğu + tarayıcı bildirimi
            "stage": st,
            "atDoor": h.status == "arrived" and st != "delivered",
            "departedAt": iso(h.departed_at),
            "deliveredAt": iso(h.received_at or h.delivered_at),
            "byCourier": h.status != "received" and h.delivered_at is not None,
            "push": {
                "key": webpush.public_key(),
                "on": push_counts(db, [h.id]).get(h.id, 0) > 0,
                "open": st != "delivered" and h.closed_at is None,  # teslimden sonra yeni abonelik alınmaz
            },
        }

    @r.post("/a/{token}/push")
    def push_on(token: str, b: PushIn, request: Request, db: Session = Depends(get_db)):
        """Alıcı "🔔 Bildirim al" dedi ve tarayıcı izin verdi: abonelik YALNIZ bu teslim için saklanır (en çok 3 tarayıcı)."""
        h = by_token(request, token, db, write=True)
        if h.status == "received" or h.closed_at is not None or h.delivered_at is not None:
            raise HTTPException(409, "Teslim tamamlandı; bildirime gerek yok.")
        ep, p256dh, auth = b.endpoint.strip(), b.keys.p256dh.strip(), b.keys.auth.strip()
        if not webpush.endpoint_ok(ep) or not webpush.keys_ok(p256dh, auth):
            raise HTTPException(400, "Bu tarayıcının bildirim adresi kabul edilmedi.")
        row = db.query(HandoffPush).filter(HandoffPush.handoff_id == h.id, HandoffPush.endpoint == ep).first()
        if row is None:
            old = db.query(HandoffPush).filter(HandoffPush.handoff_id == h.id).order_by(HandoffPush.created_at.asc()).all()
            for x in old[: max(0, len(old) - (MAX_PUSH - 1))]:
                db.delete(x)
            db.add(HandoffPush(id=uid(), handoff_id=h.id, endpoint=ep, p256dh=p256dh, auth=auth, expires_at=h.expires_at))
            events.log(db, "handoff.push", h.courier_id, h.id)
        else:
            row.p256dh, row.auth, row.expires_at = p256dh, auth, h.expires_at
        db.commit()
        return {"ok": True, "on": True}

    @r.post("/a/{token}/push/off")
    def push_off(token: str, b: PushOffIn, request: Request, db: Session = Depends(get_db)):
        """Alıcı bu teslimin bildirimini kapattı (tarayıcının izni ve başka teslimlerin bildirimi değişmez)."""
        h = by_token(request, token, db, write=True)
        db.query(HandoffPush).filter(HandoffPush.handoff_id == h.id, HandoffPush.endpoint == b.endpoint.strip()).delete(synchronize_session=False)
        db.commit()
        return {"ok": True, "on": push_counts(db, [h.id]).get(h.id, 0) > 0}

    @r.post("/a/{token}/received")
    def received(token: str, request: Request, b: ReceivedIn | None = None, db: Session = Depends(get_db)):
        h = by_token(request, token, db, write=True)
        if b is not None and not b.checked:
            return {"ok": True, "status": h.status}  # işaret geri alınmaz
        if h.status == "created" and h.delivered_at is None:
            raise HTTPException(409, "Kurye henüz hedefe ulaşmadı. Ulaşınca bu kutu açılır.")
        if h.status in ("arrived", "created"):  # created: kurye hedefe ulaştım demeden "Teslim edildi" dedi (1.18.0)
            h.status = "received"
            h.received_at = now()
            scrub_contact(h)  # artık gerekmiyor: telefon / e-posta hemen silinir
            drop_pushes(db, [h.id])  # 1.18.0: bildirim aboneliği de
            events.log(db, "handoff.received", h.courier_id, h.id)
            # 1.19.0: kuryenin telefonuna "✅ Alıcı “Paketimi aldım” dedi"; kaydı açan işverene "✅ Bir paket teslim edildi"
            if not (h.employer_id and h.courier_id == h.employer_id):
                push_queue(db, h.courier_id, "recipient_ok", thread=h.id)
            if h.employer_id and h.delivered_at is None:
                push_queue(db, h.employer_id, "delivered", thread="d")
            # 1.15.0: kurye bu durağı daha önce "teslim edildi" diye bildirip doğrulayamadıysa, alıcı onayıyla şimdi puan alır
            from .points import on_recipient_confirm  # döngüsel içe aktarma olmasın

            on_recipient_confirm(db, h)
            db.commit()
        return {"ok": True, "status": h.status}

    @r.post("/a/{token}/pin")
    def pin(token: str, b: PinIn, request: Request, db: Session = Depends(get_db)):
        h = by_token(request, token, db, write=True)
        if dist_m(h.lat, h.lon, b.lat, b.lon) > MAX_PIN_FROM_GUESS_M:
            raise HTTPException(400, "Bu nokta adresten çok uzak görünüyor. Haritada kapınızın önüne işaret koyun.")
        h.pin_lat, h.pin_lon, h.pin_at = b.lat, b.lon, now()
        layer = None
        if h.district and h.neighborhood and h.street and h.door:
            item = {
                "province": h.province, "district": h.district, "neighborhood": h.neighborhood, "street": h.street,
                "site": h.site, "door": h.door, "lat": b.lat, "lon": b.lon, "accuracyM": b.accuracy_m,
            }
            try:
                layer = add_pin(db, item, h.id, not h.link_seen)
            except (ValueError, ValidationError):
                db.rollback()
                h = db.query(Handoff).filter(Handoff.token == token).first()
                h.pin_lat, h.pin_lon, h.pin_at = b.lat, b.lon, now()
                layer = None
        events.log(db, "handoff.pin", h.courier_id, h.id, layer=bool(layer))
        # 1.19.0: "📍 Alıcı kapının yerini işaretledi" — kaydın sahibine (kurye ya da kuryesi bağlanmamış işveren) ve kaydı açan işverene
        push_queue(db, h.courier_id, "recipient_pin", thread=h.id)
        if h.employer_id and h.employer_id != h.courier_id:
            push_queue(db, h.employer_id, "recipient_pin", thread=h.id)
        db.commit()
        return {"ok": True, "layer": bool(layer)}

    @r.post("/a/{token}/review")
    def review(token: str, b: ReviewIn, request: Request, db: Session = Depends(get_db)):
        """Alıcının anketi: kurye puanı (zorunlu) + hızlı seçimler + adres doğru muydu + uygulama deneyimi + yorum. Kurye hedefe
        ulaşmadan açılmaz. Tekrar gönderilirse öncekini günceller."""
        h = by_token(request, token, db, write=True)
        if not can_review(h):
            raise HTTPException(409, "Kurye hedefe ulaşınca değerlendirebilirsiniz.")
        if not (1 <= b.stars <= 5):
            raise HTTPException(400, "Kurye puanı 1 ile 5 arasında olmalı")
        if b.app_stars is not None and not (1 <= b.app_stars <= 5):
            raise HTTPException(400, "Uygulama puanı 1 ile 5 arasında olmalı")
        reviews.save_recipient_review(db, h, b.stars, b.app_stars, b.tags, b.address_ok, b.comment)
        db.commit()
        return {"ok": True, "review": review_state(db, h)}

    @r.post("/a/{token}/contact")
    def contact(token: str, b: ContactIn, request: Request, db: Session = Depends(get_db)):
        h = by_token(request, token, db, write=True)
        if h.status == "received" or h.closed_at is not None:
            return {"ok": True, "phone": "", "email": ""}  # onaylandıktan / teslim bittikten sonra bilgi tutulmaz
        changed_mail = False
        if b.phone is not None:
            p = try_phone(b.phone)
            if b.phone.strip() and not p:
                raise HTTPException(400, "Telefon 05xx ile başlayan 11 hane olmalı")
            h.phone = p
        if b.email is not None:
            e = try_email(b.email)
            if b.email.strip() and not e:
                raise HTTPException(400, "E-posta biçimi geçersiz")
            changed_mail = bool(e) and e != h.email
            h.email = e
        if changed_mail and h.send_count < MAX_SENDS and notify.email_enabled():
            if notify.send_email(h.email, EMAIL_SUBJECT, EMAIL_TEXT.format(link=link_of(request, h), hours=int(ttl_hours))):
                h.send_count += 1
        db.commit()
        return {"ok": True, "phone": h.phone, "email": h.email}

    def loop():
        while True:
            time.sleep(600)
            try:
                db = SessionLocal()
                try:
                    purge(db)
                finally:
                    db.close()
            except Exception as e:
                print("[teslim onayı] temizlik hatası:", type(e).__name__, flush=True)

    r.purge = purge  # type: ignore[attr-defined]
    r.limiters = (ip_read, ip_write, token_write)  # type: ignore[attr-defined]
    r.loop = loop  # type: ignore[attr-defined]
    return r
