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
from .i18n import loc
from .admin import audit, current_admin
from .auth import current_user
from .db import SessionLocal, get_db
from .limits import Window
from .marks import code_of
from .moderation import contains_profanity
from .logos import logo_version
from .models import AccountDoc, AccountState, AdBooking, AdSeen, AdStat, Admin, CoinEntry, GuideBackup, Job, Notice, User, now, uid

SECTIONS = ("notif", "privacy", "settings", "business", "sponsor", "templates", "bizItems")
DOC_MAX_BYTES = 32 * 1024
FUTURE_SKEW_MS = 5 * 60 * 1000
COIN_PACKS = (50, 200, 500)
# 1.26.0 (kullanıcı kararları 7 Kasım 2026): "bir coin 10 TL gibi görünsün başlangıç ölçütü" → eski örnek fiyatlar 10'a bölündü
# ("10'a bölelim"); tabela "10 TL yazılsın şimdilik, aylık ödeme". Coin'in gerçek fiyatını mağaza belirler (Play Console / App Store Connect).
COIN_TL = 10  # 1 Rota coin'in gösterim değeri (TL)
PROMO_COIN_PER_DAY = 2  # öne çıkan ilan, gün başına = 20 TL (eskiden 20 coin; uygulamadaki store.tsx ile aynı)
PROMO_DAYS = (1, 7, 30)
# 1.27.0 TEK ÜRÜN — HARİTA TABELASI (kullanıcı kararı 7 Kasım 2026: "O noktada 1 gün durması da 1 ay durmasıda 1 coin (≈ 10 TL)";
# "bu zaten yukardaki haritada 1 noktada tabela görünmesi dediğim şey"): haritada 1 nokta = logolu tabela, nokta başına AYDA 1 coin.
# ~~Harita reklamı nokta·gün 3 coin, 1 / 7 / 30 gün, paketler (1.26.0)~~ → kaldırıldı. Eski kayıtlar süresi bitene kadar yayında;
# uzatılınca onlar da aylık tabela gibi (nokta başına 1 coin, 30 gün).
TABELA_COIN = 1  # 1 nokta · 1 ay = 1 coin ≈ 10 TL
TABELA_DAYS = 30  # tabela bir ay sürer; "Uzat" ile bir ay daha
TABELA_MAX_POINTS = 10  # sponsor bir seferde en çok 10 nokta seçer
TABELA_MAX_ACTIVE = 50  # hesap başına aynı anda en çok 50 tabela (kuryenin haritası dolmasın)
TABELA_GAP_M = 30  # aynı hesabın iki tabelası arasında en az 30 m (aynı yere üst üste konmasın)
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
        "history": [{"id": e.id, "delta": e.delta, "label": loc(e.label), "at": ms(e.created_at), "kind": e.kind} for e in rows],
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
def gap_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """İki nokta arası metre (küçük mesafeler için yeterli)"""
    k = math.cos(math.radians((a_lat + b_lat) / 2))
    return math.hypot((a_lat - b_lat) * 111320.0, (a_lon - b_lon) * 111320.0 * k)


def active_signs(db: Session, owner_id: str, t: datetime | None = None) -> list[AdBooking]:
    """Hesabın yayındaki bütün tabelaları (eski harita reklamları dahil)"""
    t = t or now()
    return (db.query(AdBooking).filter(AdBooking.owner_id == owner_id, AdBooking.removed_at.is_(None), AdBooking.ends_at > t)
            .limit(TABELA_MAX_ACTIVE + 50).all())


def ad_out(a: AdBooking, t: datetime, stats: dict | None = None) -> dict:
    out = {
        "id": a.id, "label": a.label, "lat": a.lat, "lon": a.lon, "days": a.days, "cost": a.cost, "package": a.package_id or "",
        "group": a.group_id or "", "at": ms(a.starts_at), "endsAt": ms(a.ends_at),
        "active": a.removed_at is None and (utc(a.ends_at) or t) > t,
        # 1.26.0: harita reklamı ya da TABELA. 1.27.0: yeni kayıtların hepsi tabela; "harita" yalnız eski (1.26.0 ve öncesi) kayıtlarda
        "kind": "tabela" if a.package_id == "tabela" else "harita",
    }
    if stats is not None:
        out["stats"] = stats.get(a.id) or {"viewers": 0, "shows": 0, "taps": 0, "todayViewers": 0, "todayShows": 0, "todayTaps": 0}
    return out


def tr_day(t: datetime | None = None) -> str:
    """Türkiye tarihi (UTC+3; yaz saati yok): reklam istatistiği günü"""
    return ((t or now()) + timedelta(hours=3)).strftime("%Y-%m-%d")


def ad_stats(db: Session, ids: list[str]) -> dict:
    """Reklam başına toplam ve bugünkü sayılar (yalnız sayı; kuryenin kimliği yok)"""
    if not ids:
        return {}
    out: dict = {i: {"viewers": 0, "shows": 0, "taps": 0, "todayViewers": 0, "todayShows": 0, "todayTaps": 0} for i in ids}
    for ad_id, sh, tp in db.query(AdStat.ad_id, func.sum(AdStat.shows), func.sum(AdStat.taps)).filter(AdStat.ad_id.in_(ids)).group_by(AdStat.ad_id).all():
        out[ad_id]["shows"], out[ad_id]["taps"] = int(sh or 0), int(tp or 0)
    for ad_id, n in db.query(AdSeen.ad_id, func.count(AdSeen.user_id)).filter(AdSeen.ad_id.in_(ids)).group_by(AdSeen.ad_id).all():
        out[ad_id]["viewers"] = int(n or 0)
    day = tr_day()
    for x in db.query(AdStat).filter(AdStat.ad_id.in_(ids), AdStat.day == day).all():
        out[x.ad_id].update(todayViewers=int(x.viewers or 0), todayShows=int(x.shows or 0), todayTaps=int(x.taps or 0))
    return out


def active_tabela(db: Session, owner_id: str, t: datetime | None = None) -> AdBooking | None:
    t = t or now()
    return (db.query(AdBooking).filter(AdBooking.owner_id == owner_id, AdBooking.package_id == "tabela", AdBooking.removed_at.is_(None),
                                       AdBooking.ends_at > t).order_by(AdBooking.ends_at.desc()).first())


def tabela_owners(db: Session, ids: set[str]) -> set[str]:
    """Tabelası yayında olan hesaplar (ilan listesinde 🪧 işareti)"""
    ids = {i for i in ids if i}
    if not ids:
        return set()
    t = now()
    return {o for (o,) in db.query(AdBooking.owner_id).filter(AdBooking.owner_id.in_(ids), AdBooking.package_id == "tabela",
                                                              AdBooking.removed_at.is_(None), AdBooking.ends_at > t).distinct().all()}


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
    label: str = Field(default="", max_length=200)  # 1.27.0: isteğe bağlı (boşsa şirket / tabela adı)
    points: list[PointIn] = Field(default_factory=list, max_length=TABELA_MAX_POINTS)
    days: int = 0  # 1.27.0: yok sayılır (eski istemci); süre her zaman TABELA_DAYS
    package: str = Field(default="", max_length=16)  # 1.27.0: yok sayılır


class ExtendIn(BaseModel):
    days: int = 0  # 1.27.0: yok sayılır; her uzatma bir ay (TABELA_DAYS), nokta başına TABELA_COIN


class SeenItem(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    shows: int = Field(default=0, ge=0, le=1000)
    taps: int = Field(default=0, ge=0, le=1000)


class SeenIn(BaseModel):
    items: list[SeenItem] = Field(default_factory=list, max_length=60)


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

    seen_win = Window(240, 3600)  # 1.26.0: kurye başına saatte en çok 240 reklam görüntüleme bildirimi
    rules = {
        "packs": list(COIN_PACKS), "promoPerDay": PROMO_COIN_PER_DAY, "promoDays": list(PROMO_DAYS), "testBuy": test_buy,
        "guideHours": GUIDE_HOURS, "coinTl": COIN_TL, "adPerMonth": TABELA_COIN,
        # 1.27.0 tek ürün: harita tabelası (nokta başına ayda 1 coin)
        "tabela": {"cost": TABELA_COIN, "days": TABELA_DAYS, "maxPoints": TABELA_MAX_POINTS, "maxActive": TABELA_MAX_ACTIVE, "gapM": TABELA_GAP_M},
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
            rows = my_ads(db, user.id)
            out["ads"] = {"items": [ad_out(a, t, ad_stats(db, [a.id for a in rows])) for a in rows] if rows else []}
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
        rows = my_ads(db, user.id)
        st = ad_stats(db, [a.id for a in rows])
        return {"items": [ad_out(a, t, st) for a in rows]}

    @r.post("/v1/ads")
    def ad_create(b: AdIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """1.27.0 HARİTA TABELASI (tek ürün): haritada 1 nokta = logolu tabela; nokta başına 1 ay = TABELA_COIN coin (≈ 10 TL).
        - İşletme: İşletme Profili'nde işaretlenen konumda TEK tabela (yer seçilmez; kullanıcı kararı 7 Kasım 2026). Yayındayken yenisi
          alınmaz: Reklamlarım › Uzat.
        - Sponsor: haritada 1–TABELA_MAX_POINTS nokta (hepsi bir grup); metin isteğe bağlı (boşsa şirket adı). Aynı anda en çok
          TABELA_MAX_ACTIVE tabela; kendi tabelalarından en az TABELA_GAP_M metre uzak.
        Eski istemcinin `days` / `package` alanları yok sayılır: bedel her zaman nokta × TABELA_COIN, süre TABELA_DAYS. Coin sunucuda düşer."""
        if user.active_role not in ("sponsor", "employer"):
            raise HTTPException(403, "Reklamı sponsor ya da işletme verir")
        t = now()
        if user.active_role == "employer":
            if active_tabela(db, user.id, t) is not None:
                raise HTTPException(409, "Tabelan zaten yayında; Reklamlarım'dan uzatabilirsin")
            from .bizcard import business_card  # döngüsel içe aktarma olmasın
            c = business_card(db, user)
            # yalnız İşletme Profili'nde işaretlenen konum (son ilanın başlama noktası değil): kullanıcı kararı 7 Kasım 2026
            if c.get("lat") is None or c.get("lon") is None or c.get("locFrom") != "profil":
                raise HTTPException(400, "Önce İşletme Profili'nde işletmenin konumunu işaretle; tabela orada görünür")
            pts = [(float(c["lat"]), float(c["lon"]))]
            label = _s(c.get("signboard") or "", 40) or _s(display_names(db, {user.id}).get(user.id) or user.name, 40) or "Tabela"
        else:
            pts = [(p.lat, p.lon) for p in b.points]
            if not pts:
                raise HTTPException(400, "Haritada tabelanın yerini seç (dokun)")
            for i in range(len(pts)):
                for j in range(i):
                    if gap_m(*pts[i], *pts[j]) < TABELA_GAP_M:
                        raise HTTPException(400, f"İki nokta çok yakın: aralarında en az {TABELA_GAP_M} m olsun")
            mine = active_signs(db, user.id, t)
            if len(mine) + len(pts) > TABELA_MAX_ACTIVE:
                raise HTTPException(400, f"Aynı anda en çok {TABELA_MAX_ACTIVE} tabela olabilir (şu an {len(mine)} yayında)")
            if any(gap_m(la, lo, a.lat, a.lon) < TABELA_GAP_M for la, lo in pts for a in mine):
                raise HTTPException(409, "Bu yerde zaten tabelan var; Reklamlarım'dan uzatabilirsin")
            label = _s(b.label, 200)
            if label:
                err = ad_label_error(label)
                if err:
                    raise HTTPException(400, err)
            else:
                label = _s(display_names(db, {user.id}).get(user.id) or user.name, 40) or "Tabela"
        if not ad_win.allow(user.id):
            raise HTTPException(429, "Bugün çok fazla reklam rezervasyonu yapıldı")
        n = len(pts)
        cost = n * TABELA_COIN
        group = uid() if n > 1 else ""
        charge(db, user.id, cost, "ad", f"Harita tabelası · {label} · {n} nokta · {TABELA_DAYS} gün", ref=group or "tabela", ads=True)
        rows = []
        for la, lo in pts:
            a = AdBooking(id=uid(), owner_id=user.id, label=label, lat=la, lon=lo, days=TABELA_DAYS, cost=TABELA_COIN, package_id="tabela",
                          group_id=group, starts_at=t, ends_at=t + timedelta(days=TABELA_DAYS), created_at=t)
            db.add(a)
            rows.append(a)
        events.log(db, "ad.tabela", user.id, group or rows[0].id, n=n, days=TABELA_DAYS, cost=cost)
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

    @r.post("/v1/ads/{ad_id}/extend")
    def ad_extend(ad_id: str, b: ExtendIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """TEK DOKUNUŞLA UZATMA. 1.27.0: her tabela (eski harita reklamı da) bir ay daha = TABELA_COIN coin. Süresi dolmuş tabela aynı
        yerde yeniden açılır: bitiş = (bitiş geçmediyse bitiş, geçtiyse şimdi) + TABELA_DAYS. Coin sunucuda düşer."""
        a = db.get(AdBooking, ad_id)
        if a is None or a.owner_id != user.id or a.removed_at is not None:
            raise HTTPException(404, "Reklam bulunamadı")
        t = now()
        if (utc(a.ends_at) or t) <= t:  # yeniden yayın: yayındaki tabelalarla çakışmasın
            mine = [x for x in active_signs(db, user.id, t) if x.id != a.id]
            if user.active_role == "employer" and any(x.package_id == "tabela" for x in mine):
                raise HTTPException(409, "Başka bir tabelan zaten yayında")
            if len(mine) + 1 > TABELA_MAX_ACTIVE:
                raise HTTPException(400, f"Aynı anda en çok {TABELA_MAX_ACTIVE} tabela olabilir")
            if any(gap_m(a.lat, a.lon, x.lat, x.lon) < TABELA_GAP_M for x in mine):
                raise HTTPException(409, "Bu yerde başka bir tabelan yayında")
        days, cost = TABELA_DAYS, TABELA_COIN
        charge(db, user.id, cost, "ad", f"Tabela uzatma · {a.label} · {days} gün", ref=a.id, ads=True)
        a = db.get(AdBooking, ad_id)
        t = now()
        end = utc(a.ends_at) or t
        if end <= t:
            a.starts_at = t
        a.ends_at = (end if end > t else t) + timedelta(days=days)
        a.days, a.cost, a.reminded_at = int(a.days or 0) + days, int(a.cost or 0) + cost, None
        events.log(db, "ad.extend", user.id, a.id, days=days, cost=cost)
        db.commit()
        return {"item": ad_out(a, t, ad_stats(db, [a.id])), "coins": coins_out(db, user.id)}

    @r.post("/v1/ads/seen")
    def ads_seen(b: SeenIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """1.26.0 REKLAM İSTATİSTİĞİ: kuryenin teslim haritasında görünen (shows) ve dokunulan (taps) reklamlar, toplu. Yalnız kurye;
        sayılar reklam verene gider, kuryenin kimliği gitmez. Aynı kurye aynı gün aynı reklamı ilk gördüğünde "gören kurye" 1 artar."""
        if user.active_role != "courier":
            return {"ok": True, "n": 0}
        if not seen_win.allow(user.id):
            raise HTTPException(429, "Çok sık")
        want = {}
        for x in b.items:
            if x.shows or x.taps:
                w = want.setdefault(x.id, [0, 0])
                w[0], w[1] = min(50, w[0] + x.shows), min(20, w[1] + x.taps)  # bir bildirimde reklam başına sınır (şişirme olmasın)
        if not want:
            return {"ok": True, "n": 0}
        t = now()
        day = tr_day(t)
        ads = {a.id: a for a in db.query(AdBooking).filter(AdBooking.id.in_(list(want)), AdBooking.removed_at.is_(None), AdBooking.ends_at > t).all()}
        n = 0
        for ad_id, (sh, tp) in want.items():
            a = ads.get(ad_id)
            if a is None or a.owner_id == user.id:
                continue
            seen = db.get(AdSeen, (ad_id, user.id))
            new_today = seen is None or seen.last_day != day
            if seen is None:
                db.add(AdSeen(ad_id=ad_id, user_id=user.id, last_day=day))
            else:
                seen.last_day = day
            row = db.get(AdStat, (ad_id, day))
            if row is None:
                row = AdStat(ad_id=ad_id, day=day, shows=0, taps=0, viewers=0)
                db.add(row)
            row.shows = int(row.shows or 0) + max(sh, 1 if new_today else 0)
            row.taps = int(row.taps or 0) + tp
            row.viewers = int(row.viewers or 0) + (1 if new_today else 0)
            n += 1
        try:
            db.commit()
        except IntegrityError:  # aynı anda iki bildirim: biri yazar, öbürü bir sonraki bildirimde sayılır
            db.rollback()
        return {"ok": True, "n": n}

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
        # 1.29.0: engelli (iki yönde) sahiplerin tabelaları ve bu kişinin şikâyet ettiği tabelalar ona gösterilmez
        from .blocks import blocked_with  # döngüsel içe aktarma olmasın
        from .reports import reported_refs
        bl, rp = blocked_with(db, user.id), reported_refs(db, user.id, "ad")
        if bl or rp:
            rows = [a for a in rows if a.owner_id not in bl and a.id not in rp]
        names = display_names(db, {a.owner_id for a in rows})
        owners = {u.id: u for u in db.query(User).filter(User.id.in_({a.owner_id for a in rows})).all()} if rows else {}
        # 1.26.0: reklam pininde logo (sürüm; resim /v1/logo/{id}) ve tabela
        return {"items": [{**ad_out(a, t), "cost": 0, "sponsorId": a.owner_id, "sponsor": names.get(a.owner_id, ""), "logo": logo_version(owners.get(a.owner_id))}
                          for a in rows]}

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
        st = ad_stats(db, [x.id for x in rows])
        items = []
        for x in rows:
            u = ppl.get(x.owner_id)
            items.append({**ad_out(x, t, st), "removed": x.removed_at is not None, "removedBy": x.removed_by or "",
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
    def remind(db: Session, t: datetime) -> int:
        """1.26.0 "Reklamın yarın bitiyor": bitişine 24 saatten az kalan reklam (1 günlük reklam hariç) için sahibine bir kez duyuru
        (telefonuna da bildirim gider). Paket noktaları tek duyuru. Uzatılınca yeniden hatırlatılır."""
        rows = (db.query(AdBooking).filter(AdBooking.removed_at.is_(None), AdBooking.reminded_at.is_(None), AdBooking.ends_at > t,
                                           AdBooking.ends_at <= t + timedelta(hours=24)).limit(500).all())
        done: set[tuple[str, str]] = set()
        n = 0
        for a in rows:
            a.reminded_at = t
            if (a.days or 0) <= 1:
                continue  # 1 günlük reklam: hatırlatma anlamsız (uzatılınca gün sayısı birikir)
            key = (a.owner_id, a.group_id or a.id)
            if key in done:
                continue
            done.add(key)
            what = "Tabelan" if a.package_id == "tabela" else "Reklamın"
            db.add(Notice(id=uid(), user_id=a.owner_id, kind="ad.ending", ref=a.id,
                          text=f"{what} yarın bitiyor: “{a.label}”. Tek dokunuşla uzatmak için Reklamlarım › Uzat."[:300]))
            n += 1
        return n

    def sweep(db: Session) -> dict:
        t = now()
        n = {
            "guide": db.query(GuideBackup).filter(GuideBackup.expires_at <= t).delete(synchronize_session=False),
            "remind": remind(db, t),
        }
        old = [x for (x,) in db.query(AdBooking.id).filter(AdBooking.ends_at < t - timedelta(days=400)).limit(1000).all()]
        if old:  # istatistik satırları önce (yabancı anahtar)
            db.query(AdStat).filter(AdStat.ad_id.in_(old)).delete(synchronize_session=False)
            db.query(AdSeen).filter(AdSeen.ad_id.in_(old)).delete(synchronize_session=False)
        n["ads"] = db.query(AdBooking).filter(AdBooking.id.in_(old)).delete(synchronize_session=False) if old else 0
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
    r.limiters = (doc_win, buy_win, ad_win, guide_win, seen_win)  # type: ignore[attr-defined]
    r.rules = rules  # type: ignore[attr-defined]
    r.start_cleaner = lambda: threading.Thread(target=loop, name="account-sweep", daemon=True).start()  # type: ignore[attr-defined]
    return r
