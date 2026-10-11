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
"""
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from . import events, notify, reviews
from .auth import current_user, norm_email, norm_phone
from .db import SessionLocal, get_db
from .doors import dist_m, looks_personal, tidy
from .limits import Window, client_ip
from .models import Handoff, Review, User, now, uid

SMS_TEXT = 'Paketiniz teslim edilene kadar bu mesajı silmeyiniz. Teslim almadan önce bağlantıdan "Paketimi aldım" kutusunu işaretleyiniz; işaretlenmeden teslim edilmiş sayılmaz. {link}'
EMAIL_SUBJECT = 'Paketiniz yolda: "Paketimi aldım" bağlantısı'
EMAIL_TEXT = (
    "Merhaba,\n\nKuryeniz paketinizi getiriyor.\n\n"
    'Paketiniz teslim edilene kadar bu mesajı silmeyiniz. Teslim almadan önce bağlantıdan "Paketimi aldım" kutusunu işaretleyiniz; işaretlenmeden teslim edilmiş sayılmaz.\n\n'
    "{link}\n\nBu bağlantı ve girdiğiniz bilgiler en geç {hours} saat içinde silinir."
)

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

    model_config = {"populate_by_name": True}


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
    def purge(db: Session) -> int:
        """Süresi dolan kayıtları SİLER (telefon, e-posta, pin, durum — hepsi)."""
        n = db.query(Handoff).filter(Handoff.expires_at < now()).delete(synchronize_session=False)
        db.commit()
        return n

    def maybe_purge(db: Session):
        if time.time() - last_purge[0] > 60:
            last_purge[0] = time.time()
            purge(db)

    def mine(db: Session, user: User, hid: str) -> Handoff:
        h = db.get(Handoff, hid)
        if h is None or h.courier_id != user.id or utc(h.expires_at) < now() or h.closed_at is not None:
            raise HTTPException(404, "Bağlantı bulunamadı")
        return h

    def link_of(request: Request, h: Handoff) -> str:
        return f"{public_base(request)}/a/{h.token}"

    def share_text(link: str) -> str:
        return SMS_TEXT.format(link=link)

    def out(request: Request, h: Handoff, link: str | None = None, sent: dict | None = None) -> dict:
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
        }
        if sent is not None:
            d["sent"] = sent
        if link:
            d["link"] = link
            d["shareText"] = share_text(link)
        return d

    def deliver(request: Request, h: Handoff) -> dict:
        """Bağlantıyı alıcıya sunucudan gönderir (sağlayıcı varsa). Dönen: {'sms': bool, 'email': bool}."""
        link = link_of(request, h)
        sms = bool(h.phone.startswith("05") and notify.sms_enabled() and notify.send_sms(h.phone, share_text(link)))
        mail = bool(h.email and notify.email_enabled() and notify.send_email(h.email, EMAIL_SUBJECT, EMAIL_TEXT.format(link=link, hours=int(ttl_hours))))
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
        h = (
            db.query(Handoff)
            .filter(Handoff.courier_id == user.id, Handoff.ref == ref, Handoff.expires_at > now(), Handoff.closed_at.is_(None))
            .order_by(Handoff.created_at.desc())
            .first()
        )
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
            h = Handoff(
                id=uid(), token=secrets.token_urlsafe(12), courier_id=user.id, ref=ref, phone=phone, email=email, lat=b.lat, lon=b.lon,
                expires_at=now() + timedelta(hours=ttl_hours), **parts,
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
            sent = deliver(request, h)
            if not (sent["sms"] or sent["email"]):  # gönderilemedi / sağlayıcı yok: kurye kendisi iletsin
                link = link_of(request, h)
                h.link_seen = True
        db.commit()
        db.refresh(h)
        return out(request, h, link=link, sent=sent)

    @r.get("/v1/handoffs/{hid}/link")
    def get_link(hid: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Bağlantıyı kuryeye gösterir (QR, paylaşım). Kurye gördüğü için alıcı pininin ağırlığı 1'e düşer."""
        h = mine(db, user, hid)
        h.link_seen = True
        db.commit()
        link = link_of(request, h)
        return {"link": link, "shareText": share_text(link)}

    @r.post("/v1/handoffs/{hid}/send")
    def send_again(hid: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        h = mine(db, user, hid)
        if h.status == "received":
            return out(request, h, sent={"sms": False, "email": False})
        if h.send_count >= MAX_SENDS:
            raise HTTPException(429, f"Bağlantı en çok {MAX_SENDS} kez gönderilebilir")
        sent = deliver(request, h)
        link = None
        if not (sent["sms"] or sent["email"]):
            link = link_of(request, h)
            h.link_seen = True
        db.commit()
        return out(request, h, link=link, sent=sent)

    @r.post("/v1/handoffs/{hid}/arrived")
    def arrived(hid: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        h = mine(db, user, hid)
        if h.status == "created":
            h.status = "arrived"
            h.arrived_at = now()
            events.log(db, "handoff.arrived", user.id, h.id)
            db.commit()
        return out(request, h)

    @r.get("/v1/handoffs")
    def statuses(request: Request, refs: str = Query(default="", max_length=4000), user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kuryenin duraklarının durumu (virgülle ayrılmış `refs`, en çok 50): bağlantı var mı, alıcı onayladı mı, yeni pin var mı."""
        maybe_purge(db)
        wanted = [tidy(x, 64) for x in refs.split(",") if x.strip()][:50]
        if not wanted:
            return {"items": []}
        rows = (
            db.query(Handoff)
            .filter(Handoff.courier_id == user.id, Handoff.ref.in_(wanted), Handoff.expires_at > now(), Handoff.closed_at.is_(None))
            .order_by(Handoff.created_at.desc())
            .all()
        )
        seen: set[str] = set()
        items = []
        for h in rows:
            if h.ref in seen:
                continue
            seen.add(h.ref)
            items.append(out(request, h))
        return {"items": items}

    @r.delete("/v1/handoffs/{hid}")
    def remove(hid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kurye teslimi bitirdi / durağı kaldırdı. Hedefe hiç ulaşılmadıysa kayıt tamamen silinir. Ulaşıldıysa kayıt KAPANIR:
        telefon / e-posta hemen silinir, kurye için görünmez olur, alıcı yorum yazabilsin diye en çok `review_hours` saat daha kalır."""
        h = db.get(Handoff, hid)
        if h is None or h.courier_id != user.id:
            return {"ok": True}
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
            db.delete(h)
            db.commit()
            raise HTTPException(404, "Bağlantı geçersiz ya da süresi dolmuş")
        return h

    def street_label(h: Handoff) -> str:
        s = h.street or ""
        return f"{s} No:{h.door}".strip() if (s and h.door) else (s or (f"No:{h.door}" if h.door else ""))

    @r.get("/a/{token}")
    def page(token: str):
        """Alıcı sayfası (kurulum gerektirmez). Sayfa kendi içinde durumu sorar; geçersiz kod bu yanıttan anlaşılmaz."""
        return FileResponse(os.path.join(STATIC_DIR, "alici.html"), media_type="text/html; charset=utf-8")

    def review_state(db: Session, h: Handoff) -> dict:
        """Değerlendirme sekmesi: kurye hedefe ulaştıktan sonra açılır. Daha önce yazılmışsa içeriği geri verilir (düzeltebilsin)."""
        rv = db.query(Review).filter(Review.handoff_id == h.id).first()
        data = None
        if rv is not None:
            a = reviews.answers_of(rv)
            data = {"stars": rv.stars, "appStars": rv.app_stars, "tags": a.get("tags") or [], "addressOk": a.get("addressOk"), "comment": rv.comment or ""}
        return {"allowed": h.status in ("arrived", "received"), "done": rv is not None, "data": data}

    @r.get("/a/{token}/state")
    def state(token: str, request: Request, db: Session = Depends(get_db)):
        h = by_token(request, token, db)
        return {
            "status": h.status,
            "canConfirm": h.status == "arrived" and h.closed_at is None,
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
        }

    @r.post("/a/{token}/received")
    def received(token: str, request: Request, b: ReceivedIn | None = None, db: Session = Depends(get_db)):
        h = by_token(request, token, db, write=True)
        if b is not None and not b.checked:
            return {"ok": True, "status": h.status}  # işaret geri alınmaz
        if h.status == "created":
            raise HTTPException(409, "Kurye henüz hedefe ulaşmadı. Ulaşınca bu kutu açılır.")
        if h.status == "arrived":
            h.status = "received"
            h.received_at = now()
            scrub_contact(h)  # artık gerekmiyor: telefon / e-posta hemen silinir
            events.log(db, "handoff.received", h.courier_id, h.id)
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
        db.commit()
        return {"ok": True, "layer": bool(layer)}

    @r.post("/a/{token}/review")
    def review(token: str, b: ReviewIn, request: Request, db: Session = Depends(get_db)):
        """Alıcının anketi: kurye puanı (zorunlu) + hızlı seçimler + adres doğru muydu + uygulama deneyimi + yorum. Kurye hedefe
        ulaşmadan açılmaz. Tekrar gönderilirse öncekini günceller."""
        h = by_token(request, token, db, write=True)
        if h.status not in ("arrived", "received"):
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
