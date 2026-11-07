"""PUAN VE ÖDÜL (1.15.0; kullanıcı kararları 26–27 Ekim 2026). Hepsi sunucuda; telefon yalnız gösterir.

PUAN
- Puan yalnız DOĞRULANMIŞ TESLİMDEN: alıcı "Paketimi aldım" dedi (alıcı sayfası) YA DA kurye kapıda GPS ile ≤50 m (doğruluk ≤50 m).
  Doğrulanmış her teslim 10 puan. Km puanı YOK (kaldırıldı: telefonda ölçülen yol doğrulanamaz).
- Hile sınırı: kurye günde en çok 600 puan (60 teslim) kazanır; aynı adrese aynı gün ikinci teslim puan getirmez. Alıcı sonradan onaylarsa
  doğrulanamamış teslim yine puan alır (kurallar o teslimin gününe göre).
- Kullanılmayan puan 12 ayda düşer (en eski puan önce harcanmış sayılır).
- Seviye ve rozetler doğrulanmış teslim SAYISINA göre (defterdeki +10 satırları; kalıcı).

ÖDÜL
- Sponsor / işveren ödül koyar: ürün, adet, son tarih, basamak (1000 / 2000 / 3000 / 5000 puan) ve ürünün değeri (TL).
  Referans: 1 puan = 0,10 TL → 3000 puan ≈ 300 TL'lik ürün (kurye ~300 teslim yapar). Ürünün değeri basamağın TL karşılığının
  %80'i ile %150'si arasında olmalı (3000 puan → 240–450 TL): ucuz ürün pahalı puana konmaz (kurye için adil), pahalı ürün ucuz puana
  konmaz ("her şey bedava verilmez", puanın değeri dengeli kalır). Ürün + kargo parasını ödülü koyan öder; Rota para tutmaz.
- Kurye "Al" der ve kargo adresini yazar → puan BLOKE edilir, ödülü koyana sistem bildirimi gider ("KRY-… kuryesi ürününüzü almaya hak
  kazandı"). Ödülü koyan 7 gün içinde kargolar (firma + takip no) → kuryeye "Ürünü aldın mı?" sorulur: evet → kapanır (1–5 yıldız
  memnuniyet), hayır → yönetime düşer. 7 günde kargolanmazsa puan iade; kargodan sonra 14 günde cevap yoksa "teslim alındı" sayılır.
- Kargo adresi YALNIZ ödülü koyan görür ve talep kapanınca silinir (en geç 60 gün).
- Yönetim: bekleyen / geciken / şikâyetli talepler, sponsor karnesi (zamanında kargo oranı, ortalama memnuniyet), şüpheli puan listesi,
  elle puan düzeltmesi (işlem günlüğüne yazılır).
- 1.16.0 GÖRÜNÜRLÜK (app/standing.py): Güvenilir ödül koyan üstte ve rozetli, öne çıkarılan en üstte; düzensiz (90 günde 2 geç kargo /
  2 şikâyet / düşük memnuniyet) ve kara listedeki ödül koyanın ödülleri katalogda GÖRÜNMEZ ve yeni ödül koyamaz; kara listedeki kurye ödül alamaz.
"""
import math
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import events
from .admin import audit, current_admin
from .auth import current_user, norm_phone
from .db import SessionLocal, get_db
from .limits import Window
from .models import Admin, DeliveryProof, Handoff, Notice, PointEntry, Reward, RewardClaim, RewardPhoto, User, now, uid
from .logos import sniff
import base64
import binascii
from fastapi.responses import Response

PHOTO_MAX_KB = 300  # ürün fotoğrafı (uygulama 1080 px'e küçültüp yollar)
PHOTO_SLOTS = 3
from .standing import is_blacklisted, marks_of, public_standing, standings

POINTS_PER_DELIVERY = 10
TIERS = (1000, 2000, 3000, 5000)
OPEN_CLAIM = ("pending", "shipped", "disputed")
TR_TZ = timezone(timedelta(hours=3))  # Türkiye: UTC+3 (yaz saati uygulanmıyor)

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_URL_RE = re.compile(r"https?:|www\.|@", re.IGNORECASE)
_PHONEISH_RE = re.compile(r"(?:\d[\s().+-]*){10,}")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 0/O, 1/I karışmasın


def utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    d = utc(dt)
    return d.isoformat() if d else None


def ms(dt: datetime | None) -> int:
    d = utc(dt)
    return int(d.timestamp() * 1000) if d else 0


def tr_day(dt: datetime | None = None) -> str:
    return utc(dt or now()).astimezone(TR_TZ).strftime("%Y-%m-%d")


def dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def cell_of(lat: float | None, lon: float | None) -> str:
    """~110 m'lik yaklaşık hücre (tam konum saklanmaz)."""
    if lat is None or lon is None:
        return ""
    return f"{round(lat, 3):.3f},{round(lon, 3):.3f}"


def clean_text(raw: str, limit: int) -> str:
    return re.sub(r"\s+", " ", _CTRL_RE.sub(" ", raw or "")).strip()[:limit]


def ref_tl(tier: int, point_tl: float) -> float:
    return round(tier * point_tl, 2)


def min_value_tl(tier: int, point_tl: float, ratio: float) -> int:
    return int(math.ceil(tier * point_tl * ratio))


def max_value_tl(tier: int, point_tl: float, ratio: float) -> int:
    return int(math.floor(tier * point_tl * ratio))


def user_code(u: User | None) -> str:
    if u is None or not u.public_no:
        return ""
    return {"courier": "KRY-", "employer": "ISV-", "sponsor": "SPN-"}.get(u.active_role or "", "") + u.public_no


# ------------------------------------------------------------------ defter
def add_entry(db: Session, user_id: str, delta: int, kind: str, ref: str = "", note: str = "") -> PointEntry:
    e = PointEntry(id=uid(), user_id=user_id, delta=int(delta), kind=kind, ref=ref[:40], note=note[:120])
    db.add(e)
    db.flush()
    return e


def balance_of(db: Session, user_id: str) -> int:
    return int(db.query(func.coalesce(func.sum(PointEntry.delta), 0)).filter(PointEntry.user_id == user_id).scalar() or 0)


def verified_count(db: Session, user_id: str) -> int:
    return int(db.query(func.count(PointEntry.id)).filter(PointEntry.user_id == user_id, PointEntry.kind == "delivery", PointEntry.delta > 0).scalar() or 0)


def held_of(db: Session, user_id: str) -> int:
    return int(db.query(func.coalesce(func.sum(RewardClaim.cost), 0)).filter(RewardClaim.courier_id == user_id, RewardClaim.status.in_(OPEN_CLAIM)).scalar() or 0)


def _old_positive(db: Session, user_id: str, cutoff: datetime) -> int:
    return int(db.query(func.coalesce(func.sum(PointEntry.delta), 0)).filter(PointEntry.user_id == user_id, PointEntry.delta > 0, PointEntry.created_at < cutoff).scalar() or 0)


def _all_negative(db: Session, user_id: str) -> int:
    return -int(db.query(func.coalesce(func.sum(PointEntry.delta), 0)).filter(PointEntry.user_id == user_id, PointEntry.delta < 0).scalar() or 0)


def expire_points(db: Session, user_id: str, days: float) -> int:
    """12 ay (days) kullanılmayan puanı düşer. İlk giren ilk çıkar: harcamalar en eski puandan sayılır. Düşen miktarı döner (commit ETMEZ)."""
    cutoff = now() - timedelta(days=days)
    left_old = _old_positive(db, user_id, cutoff) - _all_negative(db, user_id)
    if left_old > 0:
        add_entry(db, user_id, -left_old, "expire", note="12 ay kullanılmayan puan düştü")
        return left_old
    return 0


def expiring_soon(db: Session, user_id: str, days: float, window_days: float = 30) -> int:
    """Önümüzdeki window_days içinde düşecek puan."""
    cutoff = now() - timedelta(days=days - window_days)
    return max(0, _old_positive(db, user_id, cutoff) - _all_negative(db, user_id))


def today_points(db: Session, courier_id: str, day: str) -> int:
    return int(db.query(func.coalesce(func.sum(DeliveryProof.points), 0)).filter(DeliveryProof.courier_id == courier_id, DeliveryProof.day == day).scalar() or 0)


def notify(db: Session, user_id: str, kind: str, text: str, ref: str = "") -> None:
    db.add(Notice(id=uid(), user_id=user_id, kind=kind[:24], text=text[:300], ref=ref[:40]))


# ------------------------------------------------------------------ teslim doğrulama
DAILY_MAX = 600  # make_router ortam değişkeniyle değiştirir


def award(db: Session, p: DeliveryProof, method: str) -> int:
    """Doğrulanmış teslime puan yazar (kurallar: aynı adres aynı gün / günlük tavan). Verilen puanı döner (commit ETMEZ)."""
    p.method = method
    if p.points > 0:
        return 0
    same = (
        db.query(DeliveryProof.id)
        .filter(DeliveryProof.courier_id == p.courier_id, DeliveryProof.day == p.day, DeliveryProof.addr_key == p.addr_key,
                DeliveryProof.points > 0, DeliveryProof.id != p.id)
        .first()
    )
    if p.addr_key and same is not None:
        p.reason = "same_addr"
        return 0
    if today_points(db, p.courier_id, p.day) + POINTS_PER_DELIVERY > DAILY_MAX:
        p.reason = "cap"
        return 0
    p.points = POINTS_PER_DELIVERY
    p.reason = ""
    p.awarded_at = now()
    add_entry(db, p.courier_id, POINTS_PER_DELIVERY, "delivery", ref=p.id, note="Doğrulanmış teslim" + (" (alıcı onayı)" if method == "recipient" else " (kapıda konum)"))
    return POINTS_PER_DELIVERY


def on_recipient_confirm(db: Session, h: Handoff) -> int:
    """Alıcı "Paketimi aldım" dedi: kurye bu durağı daha önce teslim edildi diye bildirdiyse ve doğrulanamamışsa şimdi puan alır."""
    p = db.query(DeliveryProof).filter(DeliveryProof.courier_id == h.courier_id, DeliveryProof.ref == h.ref).first()
    if p is None or p.points > 0 or p.reason not in ("unverified", ""):
        return 0
    return award(db, p, "recipient")


def summary(db: Session, user: User, expire_days: float) -> dict:
    day = tr_day()
    return {
        "balance": balance_of(db, user.id),
        "held": held_of(db, user.id),
        "verified": verified_count(db, user.id),
        "today": today_points(db, user.id, day),
        "dailyMax": DAILY_MAX,
        "expiringSoon": expiring_soon(db, user.id, expire_days),
    }


class DeliveryIn(BaseModel):
    # Telefondaki durak kimliği — teslim bağlantısının (Handoff.ref) aynısı olmalı ki alıcı onayı eşleşsin; o da yalnız 1–64 karakter.
    ref: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    addr_key: str = Field(alias="addrKey", min_length=6, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    acc: float | None = Field(default=None, ge=0, le=100000)
    tlat: float | None = Field(default=None, ge=-90, le=90)
    tlon: float | None = Field(default=None, ge=-180, le=180)
    # Teslim anı (ms). İnternetsizken bekleyen bildirim sonradan gelirse puan TESLİM gününe yazılsın (günlük tavan atlatılmasın);
    # son 72 saate sıkıştırılır, ileri tarih kabul edilmez.
    at: int | None = Field(default=None, ge=0)

    model_config = {"populate_by_name": True}


class RewardIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=400)
    brand: str = Field(default="", max_length=120)
    tier: int
    value_tl: int = Field(alias="valueTl", ge=1, le=1_000_000)
    qty: int = Field(ge=1, le=1000)
    days: int = Field(default=90, ge=1, le=365)

    model_config = {"populate_by_name": True}


class ClaimIn(BaseModel):
    # 1.25.0 (kullanıcı kararı 6 Kasım 2026: "yalnız barkod", kargo kalktı): ad / telefon / adres İSTENMEZ, gelse de saklanmaz
    name: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=600)


class RedeemIn(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    # True: yalnız göster (hangi ürün, hangi kurye, geçerli mi) — kasada "Ürünü verdim" demeden önce; hiçbir şey değişmez
    check: bool = False


class PhotoIn(BaseModel):
    data: str = Field(min_length=8, max_length=PHOTO_MAX_KB * 1024 * 4 // 3 + 64)


class ShipIn(BaseModel):
    carrier: str = Field(min_length=1, max_length=80)
    tracking: str = Field(min_length=1, max_length=80)


class AnswerIn(BaseModel):
    received: bool
    stars: int | None = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=400)


class AckIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=200)


class ResolveIn(BaseModel):
    action: str = Field(pattern=r"^(refund|received)$")
    note: str = Field(default="", max_length=300)


class AdjustIn(BaseModel):
    delta: int = Field(ge=-100000, le=100000)
    note: str = Field(min_length=3, max_length=120)


def make_router(daily_max: int = 600, point_tl: float = 0.10, value_min_ratio: float = 0.8, expire_days: float = 365,
                ship_days: float = 7, answer_days: float = 14, proof_keep_days: float = 30, notice_keep_days: float = 30,
                gps_max_m: float = 50, gps_acc_max_m: float = 50, value_max_ratio: float = 1.5, barcode_days: float = 30) -> APIRouter:
    """1.25.0 ÖDÜL BARKODU (kullanıcı kararları 6 Kasım 2026): kurye "Bu ürünü al" der → puan düşer, BARKOD oluşur (talep kodu) →
    kurye işletmede barkodu gösterir → işletme / sponsor Rota'da okutur ("verildi"; tek kullanımlık) → kurye "Teslim aldım" + 1–5 yıldız.
    Kargo kalktı. Barkod `barcode_days` gün içinde okutulmazsa puan iade edilir. Durumlar: pending = barkod hazır, shipped = okutuldu
    (verildi), received = teslim alındı (eski adlar korunuyor: yönetim ve eski kayıtlar bozulmasın)."""
    global DAILY_MAX
    DAILY_MAX = daily_max
    r = APIRouter()
    deliver_win = Window(300, 86400)  # kurye başı günde en çok 300 teslim bildirimi (puan tavanı ayrı)
    claim_win = Window(20, 86400)  # kurye başı günde en çok 20 ödül talebi
    reward_win = Window(60, 86400)  # işletme başı günde en çok 60 yeni ödül

    rules = {
        "perDelivery": POINTS_PER_DELIVERY, "dailyMax": daily_max, "pointTl": point_tl, "tiers": list(TIERS),
        "valueMinRatio": value_min_ratio, "valueMaxRatio": value_max_ratio, "expireDays": expire_days, "shipDays": barcode_days, "answerDays": answer_days,
        "barcodeDays": barcode_days,
        "gpsMaxM": gps_max_m,
    }

    def people(db: Session, ids: set[str]) -> dict[str, User]:
        ids = {i for i in ids if i}
        return {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}

    def logo_v(u: User | None) -> str:
        if u is None or not u.logo:
            return ""
        return str(int(utc(u.logo_at).timestamp())) if u.logo_at else "1"

    def photo_map(db: Session, ids: list[str]) -> dict[str, list[str]]:
        """Ödül başına 3 fotoğraf yuvasının sürümü ('' = boş); katalogda <img> adresine eklenir (önbellek kırılsın)"""
        out: dict[str, list[str]] = {}
        if not ids:
            return out
        for p in db.query(RewardPhoto.reward_id, RewardPhoto.idx, RewardPhoto.created_at).filter(RewardPhoto.reward_id.in_(ids)).all():
            row = out.setdefault(p[0], ["", "", ""])
            if 0 <= p[1] < PHOTO_SLOTS:
                row[p[1]] = str(ms(p[2]))
        return out

    def reward_out(rw: Reward, owner: User | None, stats: dict | None = None, st: dict | None = None, photos: list[str] | None = None) -> dict:
        out = {
            "photos": photos or ["", "", ""],
            "id": rw.id, "name": rw.name, "note": rw.note, "tier": rw.tier, "cost": rw.tier, "valueTl": rw.value_tl,
            "refTl": ref_tl(rw.tier, point_tl), "deliveries": int(math.ceil(rw.tier / POINTS_PER_DELIVERY)),
            "qtyLeft": rw.qty_left, "qtyTotal": rw.qty_total, "endsAt": iso(rw.ends_at), "status": rw.status,
            "createdAt": iso(rw.created_at),
            "owner": {"id": rw.owner_id, "name": rw.brand or (owner.name if owner else ""), "code": user_code(owner), "role": rw.owner_role, "logo": logo_v(owner),
                      "trusted": bool(st and st["trusted"]), "featured": bool(st and st["featured"])},
        }
        if stats is not None:
            out["stats"] = stats
        return out

    def claim_out(c: RewardClaim, viewer: User, ppl: dict[str, User]) -> dict:
        owner_side = viewer.id == c.owner_id
        show_ship = owner_side and c.status in OPEN_CLAIM and bool(c.ship_address)  # 1.25.0: barkodlu taleplerde adres yok
        courier = ppl.get(c.courier_id)
        return {
            "id": c.id, "code": c.code, "rewardId": c.reward_id, "reward": c.reward_name, "brand": c.brand, "cost": c.cost,
            "status": c.status, "closeReason": c.close_reason, "side": "owner" if owner_side else "courier",
            "courier": {"code": user_code(courier)} if owner_side else None,
            "ship": {"name": c.ship_name, "phone": c.ship_phone, "address": c.ship_address} if show_ship else None,
            "carrier": c.carrier, "tracking": c.tracking, "stars": c.stars, "note": c.courier_note if owner_side else "",
            "createdAt": iso(c.created_at), "shipDueAt": iso(c.ship_due_at), "shippedAt": iso(c.shipped_at), "onTime": c.on_time,
            "answerDueAt": iso(c.answer_due_at), "receivedAt": iso(c.received_at), "closedAt": iso(c.closed_at),
            "expiresAt": iso(c.ship_due_at), "givenAt": iso(c.shipped_at),
        }

    def scrub(c: RewardClaim) -> None:
        c.ship_name, c.ship_phone, c.ship_address = "", "", ""

    def close(c: RewardClaim, status: str, reason: str) -> None:
        c.status = status
        c.close_reason = reason
        c.closed_at = now()
        scrub(c)

    def refund(db: Session, c: RewardClaim, note: str) -> None:
        add_entry(db, c.courier_id, c.cost, "refund", ref=c.id, note=note)

    def new_code(db: Session) -> str:
        for _ in range(50):
            code = "ODL" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            if db.query(RewardClaim.id).filter(RewardClaim.code == code).first() is None:
                return code
        raise RuntimeError("Boş ödül kodu bulunamadı")

    def require_owner_role(user: User) -> None:
        if user.active_role not in ("sponsor", "employer"):
            raise HTTPException(403, "Ödülü yalnız sponsor ya da işveren koyar")

    def claim_for(db: Session, cid: str, user: User) -> RewardClaim:
        c = db.get(RewardClaim, cid)
        if c is None or user.id not in (c.owner_id, c.courier_id):
            raise HTTPException(404, "Talep bulunamadı")
        return c

    # ------------------------------------------------------------------ kurye: teslim → puan
    @r.post("/v1/points/delivery")
    def delivery(b: DeliveryIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kurye bir durağı teslim etti. Doğrulanırsa (alıcı onayı ya da kapıda GPS ≤50 m) 10 puan; aynı durak ikinci kez puan almaz."""
        if user.active_role != "courier":
            return {"method": "none", "points": 0, "reason": "role", **summary(db, user, expire_days)}
        p = db.query(DeliveryProof).filter(DeliveryProof.courier_id == user.id, DeliveryProof.ref == b.ref).first()
        if p is not None and p.points > 0:
            return {"method": p.method, "points": 0, "reason": "already", **summary(db, user, expire_days)}
        if p is None and not deliver_win.allow(user.id):
            raise HTTPException(429, "Bugün çok fazla teslim bildirimi yapıldı")
        h = (
            db.query(Handoff)
            .filter(Handoff.courier_id == user.id, Handoff.ref == b.ref)
            .order_by(Handoff.created_at.desc())
            .first()
        )
        method, dist = "none", None
        if h is not None and h.status == "received":
            method = "recipient"
        elif b.lat is not None and b.lon is not None and b.acc is not None and b.acc <= gps_acc_max_m:
            # Hedef: alıcının işaretlediği kapı > kuryenin bağlantıdaki noktası > telefondaki durak noktası
            if h is not None and h.pin_lat is not None and h.pin_lon is not None:
                t = (h.pin_lat, h.pin_lon)
            elif h is not None:
                t = (h.lat, h.lon)
            elif b.tlat is not None and b.tlon is not None:
                t = (b.tlat, b.tlon)
            else:
                t = None
            if t is not None:
                dist = dist_m(b.lat, b.lon, t[0], t[1])
                if dist <= gps_max_m:
                    method = "gps"
        if p is None:
            t = now()
            if b.at is not None:
                t = min(t, max(t - timedelta(hours=72), datetime.fromtimestamp(b.at / 1000, tz=timezone.utc)))
            p = DeliveryProof(id=uid(), courier_id=user.id, ref=b.ref, addr_key=b.addr_key, day=tr_day(t), cell=cell_of(b.lat, b.lon), method="none", points=0, reason="")
            db.add(p)
        p.dist_m = round(dist, 1) if dist is not None else p.dist_m
        p.acc_m = b.acc if b.acc is not None else p.acc_m
        if b.lat is not None and not p.cell:
            p.cell = cell_of(b.lat, b.lon)
        got = 0
        if method == "none":
            p.method, p.reason = "none", "unverified"
        else:
            got = award(db, p, method)
        try:
            db.commit()
        except IntegrityError:  # aynı durak aynı anda iki kez bildirildi
            db.rollback()
            raise HTTPException(409, "Bu teslim az önce bildirildi")
        return {"method": p.method, "points": got, "reason": p.reason, **summary(db, user, expire_days)}

    @r.get("/v1/points/me")
    def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Puan özeti + son hareketler + kurallar. 12 ayı dolan puan burada düşer."""
        if expire_points(db, user.id, expire_days):
            db.commit()
        rows = db.query(PointEntry).filter(PointEntry.user_id == user.id).order_by(PointEntry.created_at.desc()).limit(50).all()
        return {
            **summary(db, user, expire_days),
            "history": [{"id": e.id, "delta": e.delta, "kind": e.kind, "note": e.note, "at": ms(e.created_at)} for e in rows],
            "rules": rules,
            "rewardsBlocked": is_blacklisted(db, user.id),  # 1.16.0: kara listedeki kurye ödül alamaz
        }

    # ------------------------------------------------------------------ ödüller (katalog)
    @r.get("/v1/rewards")
    def catalog(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kuryelerin alabileceği ödüller (stokta, süresi dolmamış, sahibi askıda değil)."""
        rows = (
            db.query(Reward)
            .filter(Reward.status == "active", Reward.qty_left > 0, or_(Reward.ends_at.is_(None), Reward.ends_at > now()))
            .order_by(Reward.tier.asc(), Reward.created_at.desc())
            .limit(200)
            .all()
        )
        ppl = people(db, {x.owner_id for x in rows})
        sts = standings(db, {x.owner_id for x in rows})
        # 1.16.0: düzensiz / kara listedeki ödül koyanın ödülleri görünmez; öne çıkan en üstte, güvenilir sonra (aynı grupta eski sıra)
        visible = [x for x in rows if not (ppl.get(x.owner_id) and ppl[x.owner_id].banned) and not sts[x.owner_id]["hidden"]]
        visible.sort(key=lambda x: (0 if sts[x.owner_id]["featured"] else 1 if sts[x.owner_id]["trusted"] else 2))
        pm = photo_map(db, [x.id for x in visible])
        items = [reward_out(x, ppl.get(x.owner_id), st=sts[x.owner_id], photos=pm.get(x.id)) for x in visible]
        return {"items": items, "rules": rules}

    @r.post("/v1/rewards")
    def create_reward(b: RewardIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        require_owner_role(user)
        if b.tier not in TIERS:
            raise HTTPException(400, "Puan bedeli 1000, 2000, 3000 ya da 5000 olmalı")
        name, note, brand = clean_text(b.name, 60), clean_text(b.note, 200), clean_text(b.brand, 60)
        if len(name) < 2:
            raise HTTPException(400, "Ürün adını yaz")
        for v in (name, note, brand):
            if _URL_RE.search(v) or _PHONEISH_RE.search(v):
                raise HTTPException(400, "Ürün bilgisinde telefon, e-posta ya da bağlantı olmasın")
        tl_text = f"{point_tl:.2f}".replace(".", ",")  # Türkçe: 0,10
        low = min_value_tl(b.tier, point_tl, value_min_ratio)
        if b.value_tl < low:
            raise HTTPException(
                400,
                f"{b.tier} puanlık ödülün değeri en az {low} TL olmalı (1 puan = {tl_text} TL → {ref_tl(b.tier, point_tl):.0f} TL). "
                "Daha düşük bir basamak seç ya da daha değerli bir ürün koy.",
            )
        high = max_value_tl(b.tier, point_tl, value_max_ratio)
        if b.value_tl > high:
            raise HTTPException(
                400,
                f"{b.tier} puanlık ödülün değeri en çok {high} TL olabilir (1 puan = {tl_text} TL → {ref_tl(b.tier, point_tl):.0f} TL). "
                "Daha yüksek bir basamak seç.",
            )
        st = standings(db, {user.id})[user.id]
        if st["blacklisted"]:
            raise HTTPException(403, "Ödül koyman yönetim tarafından durduruldu. Yardım: Hesabım › Yardım ve Destek.")
        if st["hidden"]:
            raise HTTPException(
                409,
                "Ödüllerin şu an katalogda gösterilmiyor (" + "; ".join(st["reasons"]) + "). Yeni ödül de eklenemez. "
                "Durum son 90 gündeki kayıtlar düzelince kendiliğinden kalkar; yönetimle de görüşebilirsin.",
            )
        if db.query(func.count(Reward.id)).filter(Reward.owner_id == user.id, Reward.status == "active").scalar() >= 30:
            raise HTTPException(409, "En çok 30 etkin ödülün olabilir")
        if not reward_win.allow(user.id):
            raise HTTPException(429, "Bugün çok fazla ödül eklendi")
        rw = Reward(
            id=uid(), owner_id=user.id, owner_role=user.active_role, brand=brand, name=name, note=note, tier=b.tier, value_tl=b.value_tl,
            qty_total=b.qty, qty_left=b.qty, ends_at=now() + timedelta(days=b.days), status="active",
        )
        db.add(rw)
        events.log(db, "reward.create", user.id, rw.id, tier=b.tier, qty=b.qty)
        db.commit()
        return reward_out(rw, user, {"pending": 0, "shipped": 0, "received": 0, "disputed": 0, "given": 0, "avgStars": None}, st)

    def owner_stats(db: Session, ids: list[str]) -> dict[str, dict]:
        out = {i: {"pending": 0, "shipped": 0, "received": 0, "disputed": 0, "given": 0, "avgStars": None} for i in ids}
        if not ids:
            return out
        for rid, st, n in db.query(RewardClaim.reward_id, RewardClaim.status, func.count(RewardClaim.id)).filter(RewardClaim.reward_id.in_(ids)).group_by(RewardClaim.reward_id, RewardClaim.status).all():
            if st in out[rid]:
                out[rid][st] = int(n)
            if st in ("pending", "shipped", "received", "disputed"):
                out[rid]["given"] += int(n)
        for rid, avg in db.query(RewardClaim.reward_id, func.avg(RewardClaim.stars)).filter(RewardClaim.reward_id.in_(ids), RewardClaim.stars.is_not(None)).group_by(RewardClaim.reward_id).all():
            out[rid]["avgStars"] = round(float(avg), 1) if avg is not None else None
        return out

    @r.get("/v1/rewards/mine")
    def my_rewards(user: User = Depends(current_user), db: Session = Depends(get_db)):
        rows = db.query(Reward).filter(Reward.owner_id == user.id, Reward.status == "active").order_by(Reward.created_at.desc()).limit(100).all()
        stats = owner_stats(db, [x.id for x in rows])
        mine = standings(db, {user.id})[user.id]
        pm = photo_map(db, [x.id for x in rows])
        return {"items": [reward_out(x, user, stats.get(x.id), mine, pm.get(x.id)) for x in rows], "rules": rules, "standing": public_standing(mine)}

    @r.delete("/v1/rewards/{rid}")
    def remove_reward(rid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Ödülü katalogdan kaldırır. Önceden alınmış (kargo bekleyen) talepler geçerli kalır: yine kargolanmalı."""
        rw = db.get(Reward, rid)
        if rw is None or rw.owner_id != user.id:
            raise HTTPException(404, "Ödül bulunamadı")
        if rw.status != "removed":
            rw.status = "removed"
            events.log(db, "reward.remove", user.id, rw.id)
            db.commit()
        return {"ok": True}

    # ------------------------------------------------------------------ talep (kurye)
    @r.post("/v1/rewards/{rid}/claim")
    def claim(rid: str, b: ClaimIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.active_role != "courier":
            raise HTTPException(403, "Ödülü kurye alır")
        rw = db.get(Reward, rid)
        if rw is None or rw.status != "active" or (rw.ends_at is not None and utc(rw.ends_at) <= now()):
            raise HTTPException(404, "Bu ödül artık yok")
        if rw.qty_left <= 0:
            raise HTTPException(409, "Bu ödülün stoğu bitti")
        if rw.owner_id == user.id:
            raise HTTPException(400, "Kendi ödülünü alamazsın")
        owner = db.get(User, rw.owner_id)
        if owner is None or owner.banned or standings(db, {rw.owner_id})[rw.owner_id]["hidden"]:
            raise HTTPException(404, "Bu ödül artık yok")
        if is_blacklisted(db, user.id):
            raise HTTPException(403, "Ödül alma şu an durduruldu. Yardım: Hesabım › Yardım ve Destek.")
        if db.query(RewardClaim.id).filter(RewardClaim.courier_id == user.id, RewardClaim.reward_id == rw.id, RewardClaim.status.in_(OPEN_CLAIM)).first() is not None:
            raise HTTPException(409, "Bu ödül için açık bir talebin var")
        if db.query(func.count(RewardClaim.id)).filter(RewardClaim.courier_id == user.id, RewardClaim.status.in_(OPEN_CLAIM)).scalar() >= 3:
            raise HTTPException(409, "Aynı anda en çok 3 barkodlu ürünün olabilir; önce birini teslim al")
        expire_points(db, user.id, expire_days)
        bal = balance_of(db, user.id)
        if bal < rw.tier:
            db.commit()
            raise HTTPException(400, f"Yetersiz puan: {rw.tier - bal} puan daha lazım")
        if not claim_win.allow(user.id):
            raise HTTPException(429, "Bugün çok fazla ödül talebi yapıldı")
        c = RewardClaim(
            id=uid(), code=new_code(db), reward_id=rw.id, owner_id=rw.owner_id, courier_id=user.id, reward_name=rw.name,
            brand=rw.brand or owner.name, cost=rw.tier, status="pending", ship_name="", ship_phone="", ship_address="",
            ship_due_at=now() + timedelta(days=barcode_days),  # barkodun son günü
        )
        db.add(c)
        db.flush()
        add_entry(db, user.id, -rw.tier, "hold", ref=c.id, note=f"Ödül: {rw.name}")
        rw.qty_left -= 1
        notify(
            db, rw.owner_id, "reward.claimed",
            f"{user_code(user) or 'Bir kurye'} kuryesi “{rw.name}” ürününüzü almaya hak kazandı; barkodu {c.code}. "
            f"Kurye gelince Ürünlerim › 📷 Barkod okut ile okutun (barkod {int(barcode_days)} gün geçerli).",
            ref=c.id,
        )
        events.log(db, "reward.claim", user.id, c.id, cost=rw.tier)
        db.commit()
        return claim_out(c, user, people(db, {c.courier_id}))

    @r.get("/v1/rewards/claims/mine")
    def my_claims(user: User = Depends(current_user), db: Session = Depends(get_db)):
        rows = db.query(RewardClaim).filter(RewardClaim.courier_id == user.id).order_by(RewardClaim.created_at.desc()).limit(100).all()
        return {"items": [claim_out(c, user, {}) for c in rows]}

    @r.get("/v1/rewards/claims/owner")
    def owner_claims(user: User = Depends(current_user), db: Session = Depends(get_db)):
        rows = db.query(RewardClaim).filter(RewardClaim.owner_id == user.id).order_by(RewardClaim.created_at.desc()).limit(300).all()
        ppl = people(db, {c.courier_id for c in rows})
        order = {"pending": 0, "disputed": 1, "shipped": 2}
        rows.sort(key=lambda c: (order.get(c.status, 3), -(ms(c.created_at))))
        return {"items": [claim_out(c, user, ppl) for c in rows]}

    @r.post("/v1/rewards/claims/{cid}/ship")
    def ship(cid: str, b: ShipIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        raise HTTPException(410, "Kargo kalktı: ödül barkodla verilir. Kurye gelince Ürünlerim › 📷 Barkod okut.")

    @r.post("/v1/rewards/redeem")
    def redeem(b: RedeemIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """İşletme / sponsor kuryenin barkodunu okutur → "verildi" (TEK KULLANIMLIK). Başkasının ürününün barkodu bulunamaz sayılır."""
        require_owner_role(user)
        code = re.sub(r"[^A-Z0-9]", "", (b.code or "").upper())[:12]
        c = db.query(RewardClaim).filter(RewardClaim.code == code).first() if code else None
        if c is None or c.owner_id != user.id:
            raise HTTPException(404, "Bu barkod sizin ürününüze ait değil ya da yanlış okundu")
        if c.status in ("shipped", "received"):
            raise HTTPException(409, f"Bu barkod daha önce okutuldu ({tr_day(utc(c.shipped_at))}); ikinci kez ürün verilmez")
        if c.status != "pending":
            raise HTTPException(409, "Bu barkod geçersiz (iptal edildi ya da süresi doldu)")
        if utc(c.ship_due_at) < now():
            raise HTTPException(409, "Bu barkodun süresi dolmuş; ürün verilmez (kuryenin puanı iade edilir)")
        if b.check:  # önce göster: ürün + kurye kodu; "Ürünü verdim" ikinci istekle
            return claim_out(c, user, people(db, {c.courier_id}))
        c.status = "shipped"
        c.shipped_at = now()
        c.on_time = True
        c.carrier, c.tracking = "Barkod", c.code
        c.answer_due_at = c.shipped_at + timedelta(days=answer_days)
        notify(
            db, c.courier_id, "reward.given",
            f"“{c.reward_name}” ({c.brand}) barkodun okutuldu. Ürünü aldıysan Ödül kataloğu › Tanımlanmış ürünlerim'den “Teslim aldım” de "
            f"ve 5 üzerinden puan ver.",
            ref=c.id,
        )
        events.log(db, "reward.redeem", user.id, c.id)
        db.commit()
        return claim_out(c, user, people(db, {c.courier_id}))

    # ------------------------------------------------------------------ ürün fotoğrafları (en çok 3)
    def own_reward(db: Session, rid: str, user: User) -> Reward:
        rw = db.get(Reward, rid)
        if rw is None or rw.owner_id != user.id or rw.status != "active":
            raise HTTPException(404, "Ürün bulunamadı")
        return rw

    @r.put("/v1/rewards/{rid}/photos/{idx}")
    def put_photo(rid: str, idx: int, b: PhotoIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        require_owner_role(user)
        rw = own_reward(db, rid, user)
        if not 0 <= idx < PHOTO_SLOTS:
            raise HTTPException(400, "Fotoğraf yeri 1, 2 ya da 3 olmalı")
        s = re.sub(r"^data:image/[a-z]+;base64,", "", b.data.strip(), count=1, flags=re.IGNORECASE)
        try:
            raw = base64.b64decode(s, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "Fotoğraf okunamadı")
        if len(raw) > PHOTO_MAX_KB * 1024:
            raise HTTPException(413, f"Fotoğraf çok büyük (en çok {PHOTO_MAX_KB} KB)")
        if not sniff(raw):
            raise HTTPException(415, "Fotoğraf JPEG, PNG ya da WEBP olmalı")
        db.query(RewardPhoto).filter(RewardPhoto.reward_id == rw.id, RewardPhoto.idx == idx).delete(synchronize_session=False)
        db.add(RewardPhoto(reward_id=rw.id, idx=idx, data=base64.b64encode(raw).decode("ascii")))
        events.log(db, "reward.photo", user.id, rw.id, idx=idx)
        db.commit()
        return {"photos": photo_map(db, [rw.id]).get(rw.id, ["", "", ""])}

    @r.delete("/v1/rewards/{rid}/photos/{idx}")
    def del_photo(rid: str, idx: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        rw = own_reward(db, rid, user)
        db.query(RewardPhoto).filter(RewardPhoto.reward_id == rw.id, RewardPhoto.idx == idx).delete(synchronize_session=False)
        db.commit()
        return {"photos": photo_map(db, [rw.id]).get(rw.id, ["", "", ""])}

    @r.get("/v1/rewards/{rid}/photos/{idx}")
    def get_photo(rid: str, idx: int, db: Session = Depends(get_db)):
        """Herkese açık (katalogda oturumsuz <img>); kaldırılmış ürünün fotoğrafı verilmez."""
        rw = db.get(Reward, rid)
        p = db.query(RewardPhoto).filter(RewardPhoto.reward_id == rid, RewardPhoto.idx == idx).first() if rw is not None and rw.status == "active" else None
        if p is None:
            raise HTTPException(404, "Fotoğraf yok")
        raw = base64.b64decode(p.data)
        return Response(content=raw, media_type=sniff(raw) or "application/octet-stream",
                        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"})

    @r.post("/v1/rewards/claims/{cid}/answer")
    def answer(cid: str, b: AnswerIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kurye: "Ürünü aldın mı?" — evet (+ memnuniyet yıldızı) ya da hayır (yönetime düşer)."""
        c = claim_for(db, cid, user)
        if c.courier_id != user.id:
            raise HTTPException(403, "Bu soruyu kurye cevaplar")
        note = clean_text(b.note, 200)
        if b.received:
            if c.status == "pending":
                raise HTTPException(409, "Önce işletme barkodunu okutmalı; okutunca “Teslim aldım” açılır.")
            if c.status not in ("shipped", "disputed"):
                raise HTTPException(409, "Bu talep kapandı")
            c.stars = b.stars
            c.courier_note = note
            c.received_at = now()
            close(c, "received", "courier_received")
            notify(db, c.owner_id, "reward.received", f"Kurye “{c.reward_name}” ürününü aldığını bildirdi" + (f" (memnuniyet {b.stars}/5)." if b.stars else "."), ref=c.id)
            events.log(db, "reward.received", user.id, c.id, stars=b.stars or 0)
        else:
            if c.status == "pending":
                raise HTTPException(409, f"Barkod henüz okutulmadı. {int(barcode_days)} gün içinde okutulmazsa puanın kendiliğinden iade edilir.")
            if c.status != "shipped":
                raise HTTPException(409, "Bu talep kapandı ya da incelemede")
            c.status = "disputed"
            c.courier_note = note
            notify(db, c.owner_id, "reward.disputed", f"Kurye, “{c.reward_name}” barkodu okutulduğu hâlde ürünü almadığını bildirdi (barkod {c.code}). Yönetim inceleyecek.", ref=c.id)
            events.log(db, "reward.dispute", user.id, c.id)
        db.commit()
        return claim_out(c, user, people(db, {c.courier_id}))

    @r.post("/v1/rewards/claims/{cid}/cancel")
    def cancel(cid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Barkod okutulmadan iptal: kurye vazgeçer (stok geri döner) ya da ödülü koyan veremez (stok bitti). İki durumda da puan iade."""
        c = claim_for(db, cid, user)
        if c.status != "pending":
            raise HTTPException(409, "Yalnız okutulmamış barkod iptal edilir")
        rw = db.get(Reward, c.reward_id)
        if user.id == c.courier_id:
            close(c, "cancelled", "courier_cancel")
            if rw is not None:
                rw.qty_left += 1
            refund(db, c, f"İade: {c.reward_name} (vazgeçtin)")
            notify(db, c.owner_id, "reward.cancelled", f"Kurye “{c.reward_name}” ürününden vazgeçti; {c.code} barkodu artık geçersiz.", ref=c.id)
        else:
            close(c, "cancelled", "owner_cancel")
            refund(db, c, f"İade: {c.reward_name} (verilemedi)")
            notify(db, c.courier_id, "reward.cancelled", f"“{c.reward_name}” verilemedi; barkod iptal edildi ve {c.cost} puanın iade edildi.", ref=c.id)
        events.log(db, "reward.cancel", user.id, c.id)
        db.commit()
        return claim_out(c, user, people(db, {c.courier_id}))

    # ------------------------------------------------------------------ bildirimler
    @r.get("/v1/notices")
    def notices(user: User = Depends(current_user), db: Session = Depends(get_db)):
        rows = db.query(Notice).filter(Notice.user_id == user.id).order_by(Notice.created_at.desc()).limit(50).all()
        return {"items": [{"id": n.id, "kind": n.kind, "text": n.text, "ref": n.ref, "at": ms(n.created_at)} for n in rows]}

    @r.post("/v1/notices/ack")
    def notices_ack(b: AckIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        ids = [i for i in b.ids if isinstance(i, str) and i][:200]
        n = db.query(Notice).filter(Notice.user_id == user.id, Notice.id.in_(ids)).delete(synchronize_session=False) if ids else 0
        db.commit()
        return {"deleted": int(n)}

    # ------------------------------------------------------------------ yönetim
    def admin_claim(c: RewardClaim, ppl: dict[str, User]) -> dict:
        o, k = ppl.get(c.owner_id), ppl.get(c.courier_id)
        late = c.status == "pending" and utc(c.ship_due_at) < now() + timedelta(days=1)
        return {
            "id": c.id, "code": c.code, "reward": c.reward_name, "brand": c.brand, "cost": c.cost, "status": c.status,
            "closeReason": c.close_reason, "late": late,
            "owner": {"id": c.owner_id, "name": o.name if o else "", "code": user_code(o)},
            "courier": {"id": c.courier_id, "name": k.name if k else "", "code": user_code(k)},
            "carrier": c.carrier, "tracking": c.tracking, "stars": c.stars, "note": c.courier_note, "onTime": c.on_time,
            "createdAt": iso(c.created_at), "shipDueAt": iso(c.ship_due_at), "shippedAt": iso(c.shipped_at),
            "answerDueAt": iso(c.answer_due_at), "closedAt": iso(c.closed_at),
        }

    @r.get("/v1/admin/rewards/claims")
    def admin_claims(status: str = Query(default="open", pattern=r"^(open|disputed|late|all)$"), limit: int = Query(default=100, ge=1, le=300),
                     a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Ödül talepleri (kargo adresi yönetime gösterilmez)."""
        q = db.query(RewardClaim)
        if status == "open":
            q = q.filter(RewardClaim.status.in_(OPEN_CLAIM))
        elif status == "disputed":
            q = q.filter(RewardClaim.status == "disputed")
        elif status == "late":
            q = q.filter(or_(
                (RewardClaim.status == "pending") & (RewardClaim.ship_due_at < now() + timedelta(days=1)),
                RewardClaim.close_reason == "late_ship",
            ))
        rows = q.order_by(RewardClaim.created_at.desc()).limit(limit).all()
        ppl = people(db, {x for c in rows for x in (c.owner_id, c.courier_id)})
        return {"items": [admin_claim(c, ppl) for c in rows]}

    @r.post("/v1/admin/rewards/claims/{cid}/resolve")
    def admin_resolve(cid: str, b: ResolveIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        c = db.get(RewardClaim, cid)
        if c is None:
            raise HTTPException(404, "Talep bulunamadı")
        if c.status not in OPEN_CLAIM:
            raise HTTPException(409, "Bu talep zaten kapandı")
        if b.action == "refund":
            close(c, "refunded", "admin_refund")
            refund(db, c, f"İade: {c.reward_name} (yönetim)")
            notify(db, c.courier_id, "reward.refunded", f"“{c.reward_name}” için yönetim kararıyla {c.cost} puanın iade edildi.", ref=c.id)
            notify(db, c.owner_id, "reward.refunded", f"“{c.reward_name}” talebi (talep {c.code}) yönetim kararıyla kapatıldı; kuryeye puanı iade edildi.", ref=c.id)
        else:
            c.received_at = c.received_at or now()
            close(c, "received", "admin_received")
            notify(db, c.courier_id, "reward.closed", f"“{c.reward_name}” talebi yönetim kararıyla teslim edilmiş sayıldı.", ref=c.id)
        audit(db, request, a, "reward_resolve", "claim", c.id, detail=f"{b.action} {c.code} {clean_text(b.note, 200)}".strip())
        db.commit()
        return admin_claim(c, people(db, {c.owner_id, c.courier_id}))

    # 1.25.0: ürün fotoğrafı denetimi (yüklerken uyarı: "yalnız ürünün fotoğrafı; ilgisiz resim yönetim tarafından silinir")
    @r.get("/v1/admin/rewards/photos")
    def admin_photos(limit: int = Query(default=60, ge=1, le=200), before: str = "", a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Katalogdaki (etkin) ödüllerin fotoğrafları, en yeni önce. Resmin kendisi herkese açık adresten: /v1/rewards/{id}/photos/{yer}"""
        q = db.query(RewardPhoto.id, RewardPhoto.reward_id, RewardPhoto.idx, RewardPhoto.created_at, func.length(RewardPhoto.data)).join(
            Reward, Reward.id == RewardPhoto.reward_id).filter(Reward.status == "active")
        if before:
            try:  # adres satırında kodlanmamış "+" boşluğa dönüşür: geri çevrilir (admin.parse_before ile aynı)
                q = q.filter(RewardPhoto.created_at < datetime.fromisoformat(before.strip().replace(" ", "+").replace("Z", "+00:00")))
            except ValueError:
                raise HTTPException(400, "Tarih biçimi geçersiz")
        rows = q.order_by(RewardPhoto.created_at.desc()).limit(limit).all()
        rws = {x.id: x for x in db.query(Reward).filter(Reward.id.in_({r_[1] for r_ in rows})).all()} if rows else {}
        ppl = people(db, {x.owner_id for x in rws.values()})
        out = []
        for pid, rid, idx, at, n in rows:
            rw = rws.get(rid)
            o = ppl.get(rw.owner_id) if rw else None
            out.append({"id": pid, "rewardId": rid, "idx": idx, "v": str(ms(at)), "at": iso(at), "kb": round((n or 0) * 3 / 4 / 1024, 1),
                        "reward": rw.name if rw else "", "brand": (rw.brand if rw else "") or (o.name if o else ""),
                        "owner": {"id": o.id if o else "", "code": user_code(o), "role": rw.owner_role if rw else ""}})
        return {"items": out}

    @r.delete("/v1/admin/rewards/{rid}/photos/{idx}")
    def admin_photo_delete(rid: str, idx: int, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        rw = db.get(Reward, rid)
        n = db.query(RewardPhoto).filter(RewardPhoto.reward_id == rid, RewardPhoto.idx == idx).delete(synchronize_session=False)
        if n and rw is not None:
            audit(db, request, a, "reward_photo_delete", "reward", rid, detail=f"{rw.name} · fotoğraf {idx + 1}")
            notify(db, rw.owner_id, "reward.photo", f"“{rw.name}” ürününün {idx + 1}. fotoğrafı yönetim tarafından silindi (yalnız ürünün kendi fotoğrafı yüklenebilir).", ref=rid)
        db.commit()
        return {"ok": True}

    @r.get("/v1/admin/rewards/scorecards")
    def scorecards(a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Sponsor / işveren karnesi: ödül sayısı, talep, zamanında kargo oranı, ortalama memnuniyet, şikâyet."""
        stats: dict[str, dict] = {}
        for c in db.query(RewardClaim).order_by(RewardClaim.created_at.desc()).limit(5000).all():
            s = stats.setdefault(c.owner_id, {"claims": 0, "shipped": 0, "onTime": 0, "late": 0, "disputed": 0, "starsN": 0, "starsSum": 0})
            s["claims"] += 1
            if c.shipped_at is not None:
                s["shipped"] += 1
                s["onTime"] += 1 if c.on_time else 0
            if c.close_reason == "late_ship":
                s["late"] += 1
            if c.status == "disputed" or c.close_reason == "admin_refund":
                s["disputed"] += 1
            if c.stars:
                s["starsN"] += 1
                s["starsSum"] += c.stars
        active = dict(db.query(Reward.owner_id, func.count(Reward.id)).filter(Reward.status == "active").group_by(Reward.owner_id).all())
        ppl = people(db, set(stats) | set(active))
        items = []
        for oid in set(stats) | set(active):
            s = stats.get(oid) or {"claims": 0, "shipped": 0, "onTime": 0, "late": 0, "disputed": 0, "starsN": 0, "starsSum": 0}
            due = s["shipped"] + s["late"]
            u = ppl.get(oid)
            items.append({
                "id": oid, "name": u.name if u else "", "code": user_code(u), "rewards": int(active.get(oid, 0)), "claims": s["claims"],
                "shipped": s["shipped"], "onTimePct": round(100 * s["onTime"] / due) if due else None, "late": s["late"],
                "disputed": s["disputed"], "avgStars": round(s["starsSum"] / s["starsN"], 1) if s["starsN"] else None,
            })
        items.sort(key=lambda x: (-x["claims"], x["name"]))
        return {"items": items}

    @r.get("/v1/admin/points/watch")
    def watch(days: int = Query(default=7, ge=1, le=30), a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Şüpheli puan listesi (son `days` gün): tavana sık vuran, teslimleri hep aynı küçük yerde toplanan, yalnız GPS'le doğrulanan kuryeler."""
        since = tr_day(now() - timedelta(days=days - 1))
        rows = db.query(DeliveryProof).filter(DeliveryProof.day >= since).all()
        per: dict[str, dict] = {}
        for p in rows:
            s = per.setdefault(p.courier_id, {"verified": 0, "gps": 0, "recipient": 0, "cap": 0, "same": 0, "cells": {}, "days": {}})
            if p.points > 0:
                s["verified"] += 1
                s[p.method] = s.get(p.method, 0) + 1
                s["days"][p.day] = s["days"].get(p.day, 0) + p.points
                if p.cell:
                    s["cells"][p.cell] = s["cells"].get(p.cell, 0) + 1
            if p.reason == "cap":
                s["cap"] += 1
            if p.reason == "same_addr":
                s["same"] += 1
        ppl = people(db, set(per))
        marks = marks_of(db, set(per))
        items = []
        for cid, s in per.items():
            top = max(s["cells"].values()) if s["cells"] else 0
            cap_days = sum(1 for v in s["days"].values() if v >= DAILY_MAX)
            flags = []
            if s["verified"] >= 15 and top / max(1, s["verified"]) >= 0.6:
                flags.append("aynı yerde toplanma")
            if cap_days >= 2:
                flags.append(f"{cap_days} gün tavan")
            if s["verified"] >= 20 and s["recipient"] == 0:
                flags.append("hiç alıcı onayı yok")
            if s["same"] >= 10:
                flags.append("aynı adrese tekrar")
            u = ppl.get(cid)
            items.append({
                "id": cid, "name": u.name if u else "", "code": user_code(u), "verified": s["verified"], "gps": s["gps"],
                "recipient": s["recipient"], "capHits": s["cap"], "sameAddr": s["same"], "topCellShare": round(top / s["verified"], 2) if s["verified"] else 0,
                "flags": flags, "blacklisted": bool(marks.get(cid) and marks[cid].blacklisted),
            })
        items.sort(key=lambda x: (-len(x["flags"]), -x["verified"]))
        return {"items": items[:100], "days": days}

    @r.get("/v1/admin/users/{uid_}/points")
    def admin_user_points(uid_: str, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        u = db.get(User, uid_)
        if u is None:
            raise HTTPException(404, "Üye bulunamadı")
        rows = db.query(PointEntry).filter(PointEntry.user_id == u.id).order_by(PointEntry.created_at.desc()).limit(100).all()
        return {**summary(db, u, expire_days), "history": [{"id": e.id, "delta": e.delta, "kind": e.kind, "note": e.note, "at": iso(e.created_at)} for e in rows]}

    @r.post("/v1/admin/users/{uid_}/points")
    def admin_adjust(uid_: str, b: AdjustIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Elle puan düzeltmesi (ör. hileli puanın geri alınması). İşlem günlüğüne yazılır; kullanıcıya bildirim gider."""
        u = db.get(User, uid_)
        if u is None:
            raise HTTPException(404, "Üye bulunamadı")
        if b.delta == 0:
            raise HTTPException(400, "Düzeltme 0 olamaz")
        note = clean_text(b.note, 120)
        add_entry(db, u.id, b.delta, "admin", note=f"Yönetim düzeltmesi: {note}")
        notify(db, u.id, "points.admin", f"Puanın yönetim tarafından {'+' if b.delta > 0 else ''}{b.delta} düzeltildi: {note}")
        audit(db, request, a, "points_adjust", "user", u.id, detail=f"{b.delta} {note}", name=u.name)
        db.commit()
        return {"ok": True, "balance": balance_of(db, u.id)}

    # ------------------------------------------------------------------ temizlik
    def sweep(db: Session) -> dict:
        n = {"lateRefund": 0, "autoReceived": 0, "scrubbed": 0, "proofs": 0, "notices": 0, "expired": 0, "claims": 0}
        t = now()
        for c in db.query(RewardClaim).filter(RewardClaim.status == "pending", RewardClaim.ship_due_at < t).all():
            # 1.25.0: barkod okutulmadı — kuryenin gelmemesi de olabilir, ödülü koyanın karnesine "geç" yazılmaz
            close(c, "refunded", "barcode_expired")
            refund(db, c, f"İade: {c.reward_name} (barkod okutulmadı)")
            rw = db.get(Reward, c.reward_id)
            if rw is not None:
                rw.qty_left += 1
            notify(db, c.courier_id, "reward.refunded", f"“{c.reward_name}” barkodu {int(barcode_days)} gün içinde okutulmadı; {c.cost} puanın iade edildi.", ref=c.id)
            notify(db, c.owner_id, "reward.late", f"“{c.reward_name}” barkodu ({c.code}) {int(barcode_days)} gün içinde okutulmadı; talep kapandı, puan kuryeye iade edildi.", ref=c.id)
            n["lateRefund"] += 1
        for c in db.query(RewardClaim).filter(RewardClaim.status == "shipped", RewardClaim.answer_due_at < t).all():
            c.received_at = t
            close(c, "received", "auto_received")
            n["autoReceived"] += 1
        old = t - timedelta(days=60)
        for c in db.query(RewardClaim).filter(RewardClaim.ship_address != "", RewardClaim.created_at < old).all():
            scrub(c)
            n["scrubbed"] += 1
        n["proofs"] = db.query(DeliveryProof).filter(DeliveryProof.created_at < t - timedelta(days=proof_keep_days)).delete(synchronize_session=False)
        n["notices"] = db.query(Notice).filter(Notice.created_at < t - timedelta(days=notice_keep_days)).delete(synchronize_session=False)
        n["claims"] = db.query(RewardClaim).filter(RewardClaim.closed_at.is_not(None), RewardClaim.closed_at < t - timedelta(days=365)).delete(synchronize_session=False)
        cutoff = t - timedelta(days=expire_days)
        for (user_id,) in db.query(PointEntry.user_id).filter(PointEntry.delta > 0, PointEntry.created_at < cutoff).distinct().all():
            n["expired"] += expire_points(db, user_id, expire_days)
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
                print("[puan] temizlik hatası:", type(e).__name__, flush=True)

    r.sweep = sweep  # type: ignore[attr-defined]
    r.limiters = (deliver_win, claim_win, reward_win)  # type: ignore[attr-defined]
    r.start_cleaner = lambda: threading.Thread(target=loop, name="points-sweep", daemon=True).start()  # type: ignore[attr-defined]
    return r
