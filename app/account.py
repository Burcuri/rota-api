"""HESAP VERİSİ SUNUCUDA TEK KAYNAK (1.17.0; kullanıcı kararı 28 Ekim 2026).

Kullanıcı: "Sunucuyu hesap başına tek kaynak yapalım; çevrimiçi olunca telefonda ya da web ikizinde güncellensin." "Reklam deposu kesinlikle
online olmalı." "IBAN / ödeme bilgisi hiç kimse için yok." "Rehber telefonda kalır; isteyen 24 saatlik yedek bırakır, 24 saat sonra silinir."

- HESAP BÖLÜMLERİ (account_docs): bildirim ve gizlilik tercihleri, sürüş ayarı, işletme / sponsor profili, şablonlar, ürün / hizmet listesi.
  Her yazımda hesabın eşitleme saati (account_state.clock) 1 artar; cihaz `GET /v1/account?since=<saat>` ile yalnız değişenleri alır.
  Çakışma: SON DÜZENLENEN geçerli olur (düzenleme anı cihazdan gelir; ileri tarihli saat 5 dakikaya kırpılır). Ad, kullanıcı adı, e-posta,
  telefon, avatar, çalışma bölgesi ve logo zaten `users` satırındadır; her eşitleme yanıtında `me` olarak gelir.
- COIN DEFTERİ (coin_entries): bakiye = hareketlerin toplamı. Harcama (harita reklamı, öne çıkan ilan) sunucuda yapılır; aynı hesabın
  yazımları saat satırında sıraya girer → iki cihazdan aynı anda harcama bakiyeyi eksiye düşürmez. Mağaza (Google Play / App Store)
  satın alması bağlanana kadar "test satın alma" açıktır (Render ortamında COIN_TEST_BUY=0 ile kapanır).
- REKLAM DEPOSU (ad_bookings): harita reklamları sunucuda; kuryeler yakındaki aktif reklamları sunucudan alır. Kaldırılan reklamın coini
  iade edilmez. Yönetim uygunsuz reklamı kaldırabilir.
- REHBER YEDEĞİ (guide_backups): isteğe bağlı, hesap başına tek yedek, 24 SAAT sonra kendiliğinden silinir (süre kullanıcı kararı; ortam
  değişkeniyle uzatılamaz). Rehberin asıl yeri telefondur.
"""
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import events
from .admin import audit, current_admin
from .auth import current_user
from .db import SessionLocal, get_db
from .limits import Window
from .marks import code_of
from .moderation import contains_profanity
from .models import AccountDoc, AccountState, AdBooking, Admin, CoinEntry, GuideBackup, Job, Notice, User, now, uid

SECTIONS = ("notif", "privacy", "settings", "business", "sponsor", "templates", "bizItems")
DOC_MAX_BYTES = 32 * 1024
FUTURE_SKEW_MS = 5 * 60 * 1000
COIN_PACKS = (50, 200, 500)
AD_COIN_PER_DAY = 30  # haritada bir noktada bir günlük reklam (örnek fiyat; uygulamadaki adPackages.ts ile aynı)
PROMO_COIN_PER_DAY = 20  # öne çıkan ilan, gün başına (örnek fiyat; uygulamadaki store.tsx ile aynı)
AD_DAYS = (1, 7, 30)
PROMO_DAYS = (1, 7, 30)
AD_PACKAGES = (("p1-1", 1, 1), ("p1-7", 1, 7), ("p1-30", 1, 30), ("p3-7", 3, 7), ("p3-30", 3, 30), ("p5-7", 5, 7))
SYSTEM_CODE = "SYS-0000000"
GUIDE_HOURS = 24  # kullanıcı kararı: rehber yedeği 24 saat sonra silinir (uzatılmaz)
GUIDE_MAX_ITEMS = 3000
GUIDE_MAX_BYTES = 2 * 1024 * 1024

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_CTRL_ML_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]+")  # satır sonu kalır
_IBAN_RE = re.compile(r"\bTR\d{2}[\s\d]{20,}\b", re.IGNORECASE)
_HHMM_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ms(dt: datetime | None) -> int:
    d = utc(dt)
    return int(d.timestamp() * 1000) if d else 0


# ------------------------------------------------------------------ bölümleri temizleme (bilinmeyen alan atılır, tipler zorlanır)
def _s(v: object, n: int) -> str:
    return re.sub(r"[ \t]+", " ", _CTRL_RE.sub(" ", v)).strip()[:n] if isinstance(v, str) else ""


def _ml(v: object, n: int) -> str:
    if not isinstance(v, str):
        return ""
    t = _CTRL_ML_RE.sub(" ", v.replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\n{3,}", "\n\n", t).strip()[:n]


def _b(v: object, d: bool) -> bool:
    return v if isinstance(v, bool) else d


def _num(v: object, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0.0
    x = float(v)
    return x if math.isfinite(x) and lo <= x <= hi else 0.0


def _int(v: object, lo: int, hi: int, d: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return d
    x = int(v)
    return x if lo <= x <= hi else d


def _hhmm(v: object, d: str) -> str:
    return v if isinstance(v, str) and _HHMM_RE.fullmatch(v) else d


def clean_section(section: str, raw: object) -> dict:
    r = raw if isinstance(raw, dict) else {}
    g = r.get
    if section == "notif":
        return {
            "newJob": _b(g("newJob"), True), "accepted": _b(g("accepted"), True), "delivery": _b(g("delivery"), True),
            "quietOn": _b(g("quietOn"), False), "quietFrom": _hhmm(g("quietFrom"), "23:00"), "quietTo": _hhmm(g("quietTo"), "07:00"),
            "quietWeekends": _b(g("quietWeekends"), False), "quietHolidays": _b(g("quietHolidays"), False), "quietDay": _int(g("quietDay"), -1, 6, -1),
        }
    if section == "privacy":
        return {"showPhone": _b(g("showPhone"), True), "showPhoto": _b(g("showPhoto"), True)}
    if section == "settings":
        return {"driveStart": _b(g("driveStart"), False)}
    if section == "business":
        return {
            "name": _s(g("name"), 80), "phone": _s(g("phone"), 20), "email": _s(g("email"), 120), "category": _s(g("category"), 40),
            "hours": _s(g("hours"), 60), "taxNo": _s(g("taxNo"), 20), "taxOffice": _s(g("taxOffice"), 60), "signboard": _s(g("signboard"), 80),
            "lat": _num(g("lat"), -90, 90), "lon": _num(g("lon"), -180, 180),
        }
    if section == "sponsor":
        return {"company": _s(g("company"), 80), "taxNo": _s(g("taxNo"), 20), "phone": _s(g("phone"), 20), "email": _s(g("email"), 120)}
    if section == "templates":
        return {"sms": _ml(g("sms"), 300), "jobNote": _ml(g("jobNote"), 300)}
    if section == "bizItems":
        items = g("items") if isinstance(g("items"), list) else []
        out = []
        for it in items[:200]:
            if len(out) >= 50:
                break
            if not isinstance(it, dict):
                continue
            name = _s(it.get("name"), 60)
            if not name:
                continue
            out.append({"id": _s(it.get("id"), 24) or uid()[:12], "name": name, "price": _s(it.get("price"), 20), "desc": _ml(it.get("desc"), 200),
                        "at": _int(it.get("at"), 0, 4102444800000, 0)})
        return {"items": out}
    raise HTTPException(404, "Böyle bir hesap bölümü yok")


def clean_guide_item(it: object) -> dict | None:
    if not isinstance(it, dict):
        return None
    label = _s(it.get("label"), 80)
    if len(label) < 2:
        return None
    return {
        "label": label, "address": _s(it.get("address"), 200), "street": _s(it.get("street"), 120), "doorNumber": _s(it.get("doorNumber"), 20),
        "neighborhood": _s(it.get("neighborhood"), 80), "district": _s(it.get("district"), 80),
        "lat": _num(it.get("lat"), -90, 90), "lon": _num(it.get("lon"), -180, 180), "pinVerified": _b(it.get("pinVerified"), False),
        "hitCount": _int(it.get("hitCount"), 1, 1_000_000, 1), "createdAt": _int(it.get("createdAt"), 0, 4102444800000, 0),
    }


# ------------------------------------------------------------------ eşitleme saati (aynı hesabın yazımlarını sıraya dizer)
def tick(db: Session, user_id: str, coins: bool = False, ads: bool = False) -> int:
    """Hesabın eşitleme saatini 1 artırır ve yeni değeri döner. İsteğin İLK yazımı olmalı: satır güncellemesi aynı hesabın eş zamanlı
    isteklerini burada sıraya sokar (PostgreSQL satır kilidi; SQLite yazma kilidi)."""
    vals = {AccountState.clock: AccountState.clock + 1, AccountState.updated_at: now()}
    n = db.query(AccountState).filter(AccountState.user_id == user_id).update(vals, synchronize_session=False)
    if not n:
        try:
            db.add(AccountState(user_id=user_id, clock=1, coins_rev=0, ads_rev=0, updated_at=now()))
            db.flush()
        except IntegrityError:  # aynı anda ilk yazım: satırı öbür istek açtı
            db.rollback()
            db.query(AccountState).filter(AccountState.user_id == user_id).update(vals, synchronize_session=False)
    st = db.get(AccountState, user_id)
    db.refresh(st)
    if coins:
        st.coins_rev = st.clock
    if ads:
        st.ads_rev = st.clock
    return int(st.clock)


def doc_out(d: AccountDoc) -> dict:
    try:
        data = json.loads(d.data or "{}")
    except Exception:
        data = {}
    return {"data": data, "rev": int(d.rev or 0), "editedAt": int(d.edited_ms or 0)}


# ------------------------------------------------------------------ coin defteri
def coin_balance(db: Session, user_id: str) -> int:
    return int(db.query(func.coalesce(func.sum(CoinEntry.delta), 0)).filter(CoinEntry.user_id == user_id).scalar() or 0)


def add_coins(db: Session, user_id: str, delta: int, kind: str, label: str, ref: str = "") -> CoinEntry:
    e = CoinEntry(id=uid(), user_id=user_id, delta=int(delta), kind=kind[:16], label=label[:160], ref=(ref or "")[:40], created_at=now())
    db.add(e)
    db.flush()
    return e


def coins_out(db: Session, user_id: str, limit: int = 100) -> dict:
    rows = db.query(CoinEntry).filter(CoinEntry.user_id == user_id).order_by(CoinEntry.created_at.desc()).limit(limit).all()
    return {
        "balance": coin_balance(db, user_id),
        "history": [{"id": e.id, "delta": e.delta, "label": e.label, "at": ms(e.created_at), "kind": e.kind} for e in rows],
    }


def charge(db: Session, user_id: str, cost: int, kind: str, label: str, ref: str = "", ads: bool = False) -> int:
    """Coin harcar. Önce hesabın saati güncellenir (kilit), sonra bakiye okunur: aynı hesaptan aynı anda iki harcama bakiyeyi eksiye
    düşürmez. Yetersizse 400 (işlem yazılmaz). Kalan bakiyeyi döner."""
    tick(db, user_id, coins=True, ads=ads)
    bal = coin_balance(db, user_id)
    if bal < cost:
        raise HTTPException(400, f"Yetersiz coin: {cost - bal} Rota coin daha lazım (bakiye {bal}).")
    add_coins(db, user_id, -int(cost), kind, label, ref)
    return bal - int(cost)


# ------------------------------------------------------------------ reklam
def package(pid: str) -> dict | None:
    for i, p, d in AD_PACKAGES:
        if i == pid:
            return {"id": i, "points": p, "days": d, "cost": p * d * AD_COIN_PER_DAY,
                    "title": f"Tek Nokta · {d} gün" if p == 1 else f"{p} Nokta Paketi · {d} gün"}
    return None


def split_cost(total: int, n: int) -> list[int]:
    """Paket bedeli noktalara EŞİT bölünür, kalan ilk noktaya biner (uygulamadaki splitPackageCost ile aynı)."""
    n = max(1, int(n))
    base = total // n
    return [base + (total - base * n if i == 0 else 0) for i in range(n)]


def ad_out(a: AdBooking, t: datetime) -> dict:
    return {
        "id": a.id, "label": a.label, "lat": a.lat, "lon": a.lon, "days": a.days, "cost": a.cost, "package": a.package_id or "",
        "group": a.group_id or "", "at": ms(a.starts_at), "endsAt": ms(a.ends_at),
        "active": a.removed_at is None and (utc(a.ends_at) or t) > t,
    }


def my_ads(db: Session, user_id: str) -> list[AdBooking]:
    since = now() - timedelta(days=180)
    return (
        db.query(AdBooking)
        .filter(AdBooking.owner_id == user_id, AdBooking.removed_at.is_(None), AdBooking.created_at >= since)
        .order_by(AdBooking.created_at.desc())
        .limit(300)
        .all()
    )


def display_names(db: Session, ids: set[str]) -> dict[str, str]:
    """Reklamda görünen ad: sponsor şirket adı > işletme adı > hesap adı (uygulamadaki sırayla aynı)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()}
    docs: dict[tuple[str, str], dict] = {}
    for d in db.query(AccountDoc).filter(AccountDoc.user_id.in_(ids), AccountDoc.section.in_(("sponsor", "business"))).all():
        try:
            docs[(d.user_id, d.section)] = json.loads(d.data or "{}")
        except Exception:
            pass
    out = {}
    for i, u in users.items():
        out[i] = (docs.get((i, "sponsor")) or {}).get("company") or (docs.get((i, "business")) or {}).get("name") or u.name or ""
    return out


def ad_label_error(t: str) -> str:
    if len(t) < 2:
        return "Reklam metnini yaz"
    if len(t) > 40:
        return "Reklam metni en çok 40 karakter olabilir"
    if contains_profanity(t):
        return "Küfür ve uygunsuz ifade yazılamaz"
    if _IBAN_RE.search(t):
        return "Reklam metninde IBAN olmasın"
    return ""


# ------------------------------------------------------------------ istek gövdeleri
class DocIn(BaseModel):
    data: dict
    edited_at: int = Field(default=0, ge=0, le=4102444800000, alias="editedAt")

    model_config = {"populate_by_name": True}


class BuyIn(BaseModel):
    n: int


class PointIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class AdIn(BaseModel):
    label: str = Field(max_length=200)
    points: list[PointIn] = Field(min_length=1, max_length=5)
    days: int = 0
    package: str = Field(default="", max_length=16)


class PromoteIn(BaseModel):
    days: int


class GuideIn(BaseModel):
    items: list = Field(max_length=GUIDE_MAX_ITEMS)


class CoinAdjustIn(BaseModel):
    delta: int = Field(ge=-100000, le=100000)
    note: str = Field(default="", max_length=200)


def make_router(user_out: Callable[[User], dict], test_buy: bool = True) -> APIRouter:
    r = APIRouter()
    doc_win = Window(240, 3600)  # hesap başına saatte en çok 240 bölüm yazımı (uygulama değişiklikleri biriktirip yollar)
    buy_win = Window(10, 86400)  # günde en çok 10 test satın alma
    ad_win = Window(30, 86400)  # günde en çok 30 reklam rezervasyonu
    guide_win = Window(12, 86400)  # günde en çok 12 rehber yedeği

    rules = {
        "packs": list(COIN_PACKS), "adPerDay": AD_COIN_PER_DAY, "promoPerDay": PROMO_COIN_PER_DAY, "adDays": list(AD_DAYS),
        "promoDays": list(PROMO_DAYS), "packages": [package(i) for i, _, _ in AD_PACKAGES], "testBuy": test_buy, "guideHours": GUIDE_HOURS,
    }

    def backup_info(db: Session, user_id: str) -> dict | None:
        g = db.get(GuideBackup, user_id)
        if g is None or (utc(g.expires_at) or now()) <= now():
            return None
        return {"n": int(g.n or 0), "createdAt": ms(g.created_at), "expiresAt": ms(g.expires_at)}

    # ------------------------------------------------------------------ eşitleme
    @r.get("/v1/account")
    def account(since: int = Query(default=0, ge=0), user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Hesabın sunucudaki hali. `since` (cihazın son gördüğü saat) verilirse yalnız sonra değişen bölümler, coin ve reklamlar gelir."""
        st = db.get(AccountState, user.id)
        clock = int(st.clock) if st else 0
        if since > clock:  # veritabanı değişmiş / başka sunucu: hepsini yeniden ver
            since = 0
        t = now()
        out: dict = {"clock": clock, "me": user_out(user), "now": ms(t), "guideBackup": backup_info(db, user.id), "docs": {}}
        if since == 0:
            out["rules"] = rules
        if since == 0 or since < clock:
            for d in db.query(AccountDoc).filter(AccountDoc.user_id == user.id, AccountDoc.rev > since).all():
                out["docs"][d.section] = doc_out(d)
        if since == 0 or (st is not None and int(st.coins_rev or 0) > since):
            out["coins"] = coins_out(db, user.id)
        if since == 0 or (st is not None and int(st.ads_rev or 0) > since):
            out["ads"] = {"items": [ad_out(a, t) for a in my_ads(db, user.id)]}
        return out

    @r.put("/v1/account/docs/{section}")
    def doc_put(section: str, b: DocIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Bir hesap bölümünü yazar. Sunucudaki bölüm daha SONRA düzenlenmişse yazılmaz (`stale`), güncel hali döner."""
        if section not in SECTIONS:
            raise HTTPException(404, "Böyle bir hesap bölümü yok")
        data = clean_section(section, b.data)
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > DOC_MAX_BYTES:
            raise HTTPException(413, "Bu bölüm çok büyük")
        if not doc_win.allow(user.id):
            raise HTTPException(429, "Çok sık kayıt; biraz sonra yeniden denenecek")
        t_ms = ms(now())
        edited = min(b.edited_at or t_ms, t_ms + FUTURE_SKEW_MS)
        clock = tick(db, user.id)
        d = db.get(AccountDoc, (user.id, section))
        if d is not None and int(d.edited_ms or 0) > edited:
            db.commit()
            return {"stale": True, "doc": doc_out(d), "clock": clock}
        if d is None:
            d = AccountDoc(user_id=user.id, section=section)
            db.add(d)
        d.data, d.rev, d.edited_ms, d.updated_at = raw, clock, edited, now()
        db.commit()
        return {"stale": False, "doc": doc_out(d), "clock": clock}

    # ------------------------------------------------------------------ coin
    @r.get("/v1/coins")
    def coins(user: User = Depends(current_user), db: Session = Depends(get_db)):
        return {**coins_out(db, user.id), "rules": rules}

    @r.post("/v1/coins/test-buy")
    def test_buy_ep(b: BuyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Mağaza satın alması bağlanana kadar TEST: ödeme alınmaz. Yayında yerini Google Play / App Store makbuz doğrulaması alır."""
        if not test_buy:
            raise HTTPException(403, "Coin yalnız Google Play / App Store'dan alınır")
        if b.n not in COIN_PACKS:
            raise HTTPException(400, "Paket 50, 200 ya da 500 coin olmalı")
        if not buy_win.allow(user.id):
            raise HTTPException(429, "Bugün çok fazla test satın alması yapıldı")
        tick(db, user.id, coins=True)
        add_coins(db, user.id, b.n, "test", f"Rota coin satın alma (test) · {b.n}")
        events.log(db, "coin.buy", user.id, "", n=b.n)
        db.commit()
        return {"coins": coins_out(db, user.id)}

    # ------------------------------------------------------------------ reklam deposu
    @r.get("/v1/ads/mine")
    def ads_mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
        t = now()
        return {"items": [ad_out(a, t) for a in my_ads(db, user.id)]}

    @r.post("/v1/ads")
    def ad_create(b: AdIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Haritada reklam: serbest (1 nokta · 1 / 7 / 30 gün) ya da paket (N nokta × D gün tek bedel). Coin sunucuda düşer."""
        if user.active_role not in ("sponsor", "employer"):
            raise HTTPException(403, "Reklamı sponsor ya da işletme verir")
        label = _s(b.label, 200)
        err = ad_label_error(label)
        if err:
            raise HTTPException(400, err)
        pkg = None
        if b.package:
            pkg = package(b.package)
            if pkg is None:
                raise HTTPException(400, "Böyle bir reklam paketi yok")
            if len(b.points) != pkg["points"]:
                raise HTTPException(400, f"Bu pakette haritada {pkg['points']} nokta seçilmeli")
            days, cost = pkg["days"], pkg["cost"]
            tx = f"Reklam paketi · {pkg['title']} · {SYSTEM_CODE}"
        else:
            if b.days not in AD_DAYS:
                raise HTTPException(400, "Süre 1, 7 ya da 30 gün olmalı")
            if len(b.points) != 1:
                raise HTTPException(400, "Serbest reklam tek noktadır")
            days, cost = b.days, b.days * AD_COIN_PER_DAY
            tx = f"Reklam rezervasyonu · {label} · {days} gün"
        if not ad_win.allow(user.id):
            raise HTTPException(429, "Bugün çok fazla reklam rezervasyonu yapıldı")
        group = uid() if pkg else ""
        charge(db, user.id, cost, "ad", tx, ref=group, ads=True)
        t = now()
        parts = split_cost(cost, len(b.points))
        rows = []
        for i, p in enumerate(b.points):
            a = AdBooking(id=uid(), owner_id=user.id, label=label, lat=p.lat, lon=p.lon, days=days, cost=parts[i], package_id=pkg["id"] if pkg else "",
                          group_id=group, starts_at=t, ends_at=t + timedelta(days=days), created_at=t)
            db.add(a)
            rows.append(a)
        events.log(db, "ad.create", user.id, group or rows[0].id, n=len(rows), days=days, cost=cost)
        db.commit()
        return {"items": [ad_out(a, t) for a in rows], "coins": coins_out(db, user.id)}

    @r.delete("/v1/ads/{ad_id}")
    def ad_remove(ad_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Reklamı kaldırır (coin iade edilmez)."""
        a = db.get(AdBooking, ad_id)
        if a is None or a.owner_id != user.id:
            raise HTTPException(404, "Reklam bulunamadı")
        if a.removed_at is None:
            tick(db, user.id, ads=True)
            a = db.get(AdBooking, ad_id)
            a.removed_at, a.removed_by = now(), "owner"
            events.log(db, "ad.remove", user.id, a.id)
            db.commit()
        return {"ok": True}

    @r.get("/v1/ads/active")
    def ads_active(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), radius: float = Query(default=30000, ge=100, le=100000),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Bir noktanın çevresindeki aktif reklamlar (kuryenin haritası). Sahibi askıdaysa gösterilmez."""
        t = now()
        dlat = radius / 111320.0
        dlon = radius / (111320.0 * max(0.2, math.cos(math.radians(lat))))
        rows = (
            db.query(AdBooking)
            .join(User, User.id == AdBooking.owner_id)
            .filter(AdBooking.removed_at.is_(None), AdBooking.ends_at > t, User.banned.is_not(True))
            .filter(AdBooking.lat.between(lat - dlat, lat + dlat), AdBooking.lon.between(lon - dlon, lon + dlon))
            .order_by(AdBooking.created_at.desc())
            .limit(300)
            .all()
        )
        names = display_names(db, {a.owner_id for a in rows})
        return {"items": [{**ad_out(a, t), "cost": 0, "sponsorId": a.owner_id, "sponsor": names.get(a.owner_id, "")} for a in rows]}

    # ------------------------------------------------------------------ öne çıkan ilan
    @r.post("/v1/jobs/{job_id}/promote")
    def promote(job_id: str, b: PromoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """İlanı Rota coin ile öne çıkarır (gün başına PROMO_COIN_PER_DAY). Zaten öne çıkansa süre eklenir."""
        if b.days not in PROMO_DAYS:
            raise HTTPException(400, "Süre 1, 7 ya da 30 gün olmalı")
        job = db.get(Job, job_id)
        if job is None or job.status == "closed":
            raise HTTPException(404, "İlan yok")
        if job.employer_id != user.id:
            raise HTTPException(403, "Yalnız ilanı veren öne çıkarır")
        if job.status not in ("open", "applied"):
            raise HTTPException(409, "Yalnız açık ilan öne çıkarılır")
        title = _s(job.title, 60)
        charge(db, user.id, b.days * PROMO_COIN_PER_DAY, "promote", f"Öne çıkan ilan · {title} · {b.days} gün", ref=job.id)
        job = db.get(Job, job_id)
        t = now()
        cur = utc(job.promoted_until)
        job.promoted_until = (cur if cur and cur > t else t) + timedelta(days=b.days)
        events.log(db, "job.promote", user.id, job.id, days=b.days)
        db.commit()
        return {"promotedUntil": ms(job.promoted_until), "coins": coins_out(db, user.id)}

    # ------------------------------------------------------------------ rehber yedeği (24 saat)
    @r.put("/v1/me/guide-backup")
    def guide_put(b: GuideIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Rehberi 24 saatliğine sunucuya bırakır (telefon değiştirirken). Hesap başına tek yedek: yenisi eskisinin yerine geçer."""
        items = [x for x in (clean_guide_item(i) for i in b.items) if x]
        if not items:
            raise HTTPException(400, "Rehberinde yedeklenecek kayıt yok")
        raw = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > GUIDE_MAX_BYTES:
            raise HTTPException(413, "Rehber yedeği çok büyük")
        if not guide_win.allow(user.id):
            raise HTTPException(429, "Bugün çok fazla yedek alındı")
        t = now()
        g = db.get(GuideBackup, user.id)
        if g is None:
            g = GuideBackup(user_id=user.id)
            db.add(g)
        g.data, g.n, g.created_at, g.expires_at = raw, len(items), t, t + timedelta(hours=GUIDE_HOURS)
        events.log(db, "guide.backup", user.id, "", n=len(items))
        db.commit()
        return backup_info(db, user.id)

    @r.get("/v1/me/guide-backup")
    def guide_get(user: User = Depends(current_user), db: Session = Depends(get_db)):
        g = db.get(GuideBackup, user.id)
        if g is None or (utc(g.expires_at) or now()) <= now():
            raise HTTPException(404, "Rehber yedeği yok (24 saat dolunca silinir)")
        try:
            items = json.loads(g.data or "[]")
        except Exception:
            items = []
        events.log(db, "guide.restore", user.id, "", n=len(items))
        db.commit()
        return {**(backup_info(db, user.id) or {}), "items": items}

    @r.delete("/v1/me/guide-backup")
    def guide_delete(user: User = Depends(current_user), db: Session = Depends(get_db)):
        n = db.query(GuideBackup).filter(GuideBackup.user_id == user.id).delete(synchronize_session=False)
        db.commit()
        return {"deleted": bool(n)}

    # ------------------------------------------------------------------ yönetim: coin ve reklamlar
    def target(db: Session, uid_: str) -> User:
        u = db.get(User, uid_)
        if u is None:
            raise HTTPException(404, "Üye bulunamadı")
        return u

    @r.get("/v1/admin/users/{uid_}/coins")
    def admin_coins(uid_: str, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        u = target(db, uid_)
        t = now()
        return {**coins_out(db, u.id, 50), "ads": [ad_out(x, t) for x in my_ads(db, u.id)]}

    @r.post("/v1/admin/users/{uid_}/coins")
    def admin_coins_adjust(uid_: str, b: CoinAdjustIn, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Coin düzeltmesi (destek: yüklenmeyen satın alma vb.). Kullanıcıya bildirim gider, işlem günlüğüne yazılır."""
        u = target(db, uid_)
        if b.delta == 0:
            raise HTTPException(400, "Düzeltme 0 olamaz")
        note = _s(b.note, 120)
        if len(note) < 3:
            raise HTTPException(400, "Düzeltmenin nedenini yazın")
        tick(db, u.id, coins=True)
        if b.delta < 0 and coin_balance(db, u.id) + b.delta < 0:
            raise HTTPException(400, "Bakiye eksiye düşemez")
        add_coins(db, u.id, b.delta, "admin", f"Yönetim düzeltmesi: {note}")
        db.add(Notice(id=uid(), user_id=u.id, kind="coins.admin", text=f"Rota coin bakiyen yönetim tarafından {b.delta:+d} düzeltildi: {note}"[:300], ref=""))
        audit(db, request, a, "coins_adjust", "user", u.id, f"{b.delta:+d} {note}", name=u.name)
        db.commit()
        return {"ok": True, "balance": coin_balance(db, u.id)}

    @r.get("/v1/admin/ads")
    def admin_ads(view: str = Query(default="active", pattern=r"^(active|all)$"), a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        t = now()
        q = db.query(AdBooking)
        if view == "active":
            q = q.filter(AdBooking.removed_at.is_(None), AdBooking.ends_at > t)
        else:
            q = q.filter(AdBooking.created_at >= t - timedelta(days=90))
        rows = q.order_by(AdBooking.created_at.desc()).limit(300).all()
        ppl = {u.id: u for u in db.query(User).filter(User.id.in_({x.owner_id for x in rows})).all()} if rows else {}
        names = display_names(db, set(ppl))
        items = []
        for x in rows:
            u = ppl.get(x.owner_id)
            items.append({**ad_out(x, t), "removed": x.removed_at is not None, "removedBy": x.removed_by or "",
                          "owner": {"id": x.owner_id, "name": u.name if u else "", "shown": names.get(x.owner_id, ""), "code": code_of(u),
                                    "role": u.active_role if u else "", "banned": bool(u and u.banned)}})
        return {"items": items}

    @r.delete("/v1/admin/ads/{ad_id}")
    def admin_ad_remove(ad_id: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Uygunsuz reklamı kaldırır: reklam kuryelerin haritasından iner, sahibine bildirim gider (coin iadesi gerekiyorsa ayrıca yapılır)."""
        x = db.get(AdBooking, ad_id)
        if x is None:
            raise HTTPException(404, "Reklam bulunamadı")
        if x.removed_at is None:
            owner = x.owner_id
            tick(db, owner, ads=True)
            x = db.get(AdBooking, ad_id)
            x.removed_at, x.removed_by = now(), "admin"
            db.add(Notice(id=uid(), user_id=owner, kind="ad.removed", text=f"“{x.label}” reklamınız yönetim tarafından kaldırıldı."[:300], ref=x.id))
            audit(db, request, a, "ad_remove", "ad", x.id, x.label)
            db.commit()
        return {"ok": True}

    # ------------------------------------------------------------------ temizlik
    def sweep(db: Session) -> dict:
        t = now()
        n = {
            "guide": db.query(GuideBackup).filter(GuideBackup.expires_at <= t).delete(synchronize_session=False),
            "ads": db.query(AdBooking).filter(AdBooking.ends_at < t - timedelta(days=400)).delete(synchronize_session=False),
        }
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
                print("[hesap] temizlik hatası:", type(e).__name__, flush=True)

    r.sweep = sweep  # type: ignore[attr-defined]
    r.limiters = (doc_win, buy_win, ad_win, guide_win)  # type: ignore[attr-defined]
    r.rules = rules  # type: ignore[attr-defined]
    r.start_cleaner = lambda: threading.Thread(target=loop, name="account-sweep", daemon=True).start()  # type: ignore[attr-defined]
    return r
