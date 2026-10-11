"""Ortak kapı noktası katmanı.

OpenStreetMap'te olmayan kapı numaralarını, kuryelerin teslimatlarıyla yavaş yavaş tamamlayan ortak bir harita katmanıdır.
Bir kurye kapıyı teslim ettiğinde (ya da pini elle koyduğunda) o kapının "il/ilçe/mahalle/sokak/kapı no + enlem-boylam" bilgisi
buraya yazılır; giriş yapmış herkes aynı kapıyı sorgulayınca bu noktayı görür.

Kurallar:
- Kayıtta KİŞİ BİLGİSİ yoktur: ad, telefon, daire/kat, hesap kimliği yazılmaz. Kimin bildirdiği ayrı tabloda (door_reports) yalnız
  hesap kimliğinden türetilmiş geri çevrilemez kısa bir kod olarak tutulur; amaç aynı kişinin aynı noktayı birden çok sayması.
- Katkı için kullanıcının açık rızası (consent_log.kapi) gerekir; rıza yoksa yazma 403 verir. Okuma için yalnız giriş yeterli.
- Tek rapor "tahmini" nokta olur; birden çok kişinin aynı yeri (≈35 m içinde) bildirmesi puanı yükseltir ve nokta "doğrulanmış"
  sayılır. Alıcının kendi pini (ileride) tek başına doğrulama puanı taşır.
- "Yanlış" oyları (en az 2) doğru oylarından çoksa nokta gizlenir; eşitlikte gizlenmez ama doğrulanmış da sayılmaz. Birbirinden uzak çelişen raporlar ayrı aday olur, en yüksek puanlı gösterilir.
- Günlük sınır: kişi başı 24 saatte en çok DOOR_DAILY_MAX yeni oy.
"""
import difflib
import hashlib
import hmac
import math
import re
import secrets
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import events
from .auth import SECRET, current_user
from .db import get_db
from .models import ConsentLog, DoorPoint, DoorReport, User, now

# ---------------------------------------------------------------- metin normalleştirme
_TR = str.maketrans({"İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g", "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c"})


def fold(s: str) -> str:
    """Türkçe harfleri düz Latin'e indirir, küçültür (İ/ı tuzağı yok: önce eşlenir, sonra küçültülür)."""
    s = (s or "").translate(_TR)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", fold(s)).split()


KIND_WORDS = {
    "cadde": "cadde", "caddesi": "cadde", "cad": "cadde", "cd": "cadde",
    "sokak": "sokak", "sokagi": "sokak", "sok": "sokak", "sk": "sokak",
    "bulvar": "bulvar", "bulvari": "bulvar", "blv": "bulvar", "bul": "bulvar", "bulv": "bulvar",
    "meydan": "meydan", "meydani": "meydan",
}
STREET_NOISE = {"no", "nolu", "numara", "numarali"}
NEIGH_NOISE = {"mahallesi", "mahalle", "mah", "mh"}
DISTRICT_NOISE = {"ilcesi", "ilce"}
PROVINCE_NOISE = {"ili", "il", "province", "buyuksehir", "belediyesi"}

_NO_TAIL = re.compile(r"\b(no|numara)\b\s*[:.\-]?\s*\d.*$", re.IGNORECASE)


def street_parts(raw: str) -> tuple[str, str]:
    """('çekirdek', 'tür'): "Atatürk Cad." → ('ataturk', 'cadde'); "107 Nolu Sokak" → ('107', 'sokak')."""
    text = _NO_TAIL.sub("", raw or "")
    kind, core = "", []
    for w in words(text):
        if w in KIND_WORDS:
            kind = KIND_WORDS[w]
        elif w not in STREET_NOISE:
            core.append(w)
    return " ".join(core)[:120], kind


# Anahtarlar sütun genişliğine kırpılır (hem yazarken hem sorgularken aynı işlev kullanılır). Böylece alışılmadık Unicode
# karakterler (ör. "㎦" → "km3" olarak genişler) PostgreSQL'de VARCHAR taşmasına ve tüm paketin 500 vermesine yol açamaz.
def neigh_core(raw: str) -> str:
    return " ".join(w for w in words(raw) if w not in NEIGH_NOISE)[:100]


def district_core(raw: str) -> str:
    return " ".join(w for w in words(raw) if w not in DISTRICT_NOISE)[:80]


def province_core(raw: str) -> str:
    return " ".join(w for w in words(raw) if w not in PROVINCE_NOISE)[:60]


_DOOR_RE = re.compile(r"^\s*(?:no|numara)?\s*[:.\-]?\s*(\d{1,5})(?:\s*[/\-]?\s*([A-Za-z]))?(?![A-Za-z0-9])", re.IGNORECASE)


def door_parts(raw: str) -> tuple[str, int] | None:
    """Bina numarası: '12' · '12A' · '12/A' → 12A · '12/3' (daire) → 12. Çözülemezse None."""
    m = _DOOR_RE.match(fold(raw))
    if not m:
        return None
    n = int(m.group(1))
    if n <= 0 or n > 99999:
        return None
    return f"{n}{(m.group(2) or '').upper()}", n


def tidy(raw: str, maxlen: int) -> str:
    s = re.sub(r"\s+", " ", "".join(ch for ch in (raw or "") if ch.isprintable())).strip()
    return s[:maxlen]


def looks_personal(s: str) -> bool:
    """Sokak/mahalle alanına yanlışlıkla yazılmış telefon, e-posta ya da bağlantı ortak katmana girmesin."""
    return bool(re.search(r"@|https?:|www\.", s, re.IGNORECASE)) or len(re.sub(r"\D", "", s)) >= 7


# ---------------------------------------------------------------- eşleştirme
def street_close(a: str, b: str) -> bool:
    """Aynı sokak mı? Birebir; numaralı sokaklar yalnız birebir; ad bir diğerinin içinde geçiyorsa
    ("Atatürk" ⊂ "Mustafa Kemal Atatürk"); ya da küçük yazım farkı."""
    if a == b:
        return True
    if not a or not b:
        return False
    if re.search(r"\d", a) or re.search(r"\d", b):
        return False
    ta, tb = a.split(), b.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) < len(long_) and len(" ".join(short)) >= 4:
        if any(long_[i : i + len(short)] == short for i in range(len(long_) - len(short) + 1)):
            return True
    if min(len(a), len(b)) >= 6:
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85
    return False


def same_street(a_core: str, a_kind: str, b_core: str, b_kind: str) -> bool:
    if a_kind and b_kind and a_kind != b_kind:  # "Gül Sokak" ≠ "Gül Caddesi"
        return False
    return street_close(a_core, b_core)


def province_ok(a: str, b: str) -> bool:
    return not a or not b or a == b


def dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def reporter_code(user_id: str) -> str:
    """Hesap kimliğinden türetilmiş, geri çevrilemez kısa kod (yalnız aynı kişiyi iki kez saymamak için)."""
    return hmac.new(SECRET.encode(), ("door-reporter:" + user_id).encode(), hashlib.sha256).hexdigest()[:24]


def anonymize_reporter(db: Session, user_id: str) -> int:
    """Hesap silinirken: bu kişinin oyları puanlarıyla kalır ama artık kimseye bağlanamaz (kod rastgele değiştirilir)."""
    rows = db.query(DoorReport).filter(DoorReport.reporter == reporter_code(user_id)).all()
    for r in rows:
        r.reporter = "x" + secrets.token_hex(11)
    db.commit()
    return len(rows)


SOURCE_WEIGHT = {"checkin": 1.0, "pin": 1.0, "recipient": 2.0}
SOURCES_FROM_APP = {"checkin", "pin"}  # "recipient" yalnız sunucu içinden (alıcı sayfası) gelir


class ItemIn(BaseModel):
    province: str = Field(default="", max_length=80)
    district: str = Field(max_length=100)
    neighborhood: str = Field(max_length=120)
    street: str = Field(max_length=160)
    site: str = Field(default="", max_length=120)
    door: str = Field(max_length=40)
    lat: float = Field(ge=34.0, le=43.0)  # Türkiye ve KKTC çevresi
    lon: float = Field(ge=25.0, le=45.5)
    accuracy_m: float | None = Field(default=None, alias="accuracyM", ge=0, le=1_000_000)
    source: str = Field(default="checkin", max_length=12)
    vote: str = Field(default="confirm", max_length=8)

    model_config = {"populate_by_name": True}


class ReportIn(BaseModel):
    items: list[dict] = Field(min_length=1, max_length=50)


class Clean:
    """Doğrulanmış, normalleştirilmiş tek rapor."""

    def __init__(self, it: ItemIn):
        self.province = tidy(it.province, 60)
        self.district = tidy(it.district, 80)
        self.neighborhood = tidy(it.neighborhood, 100)
        self.street = tidy(_NO_TAIL.sub("", it.street), 120)
        self.site = tidy(it.site, 60)
        for label, v in (("il", self.province), ("ilçe", self.district), ("mahalle", self.neighborhood), ("sokak", self.street), ("site", self.site), ("kapı", tidy(it.door, 40))):
            if looks_personal(v):
                raise ValueError(f"kisisel_veri:{label}")
        self.province_k = province_core(self.province)
        self.district_k = district_core(self.district)
        self.neigh_k = neigh_core(self.neighborhood)
        self.street_k, self.street_kind = street_parts(self.street)
        if not self.district_k:
            raise ValueError("ilce_yok")
        if not self.neigh_k:
            raise ValueError("mahalle_yok")  # mahallesiz sokak/kapı ortak katmana girmez (aynı ad başka mahallede de var)
        if not self.street_k:
            raise ValueError("sokak_yok")
        d = door_parts(it.door)
        if d is None:
            raise ValueError("kapi_no_gecersiz")
        self.door_k, self.door_n = d
        self.lat, self.lon = it.lat, it.lon
        self.accuracy_m = it.accuracy_m
        self.vote = it.vote if it.vote in ("confirm", "wrong") else "confirm"
        self.source = it.source if it.source in SOURCES_FROM_APP else "pin"


def make_router(daily_max: int = 400, require_consent: bool = False, trust_score: float = 2.0, merge_m: float = 35.0, max_accuracy_m: float = 60.0) -> APIRouter:
    r = APIRouter()
    wrong_radius_m = 90.0
    hide_wrong = 2.0

    def has_consent(db: Session, user_id: str) -> bool:
        c = db.query(ConsentLog).filter(ConsentLog.user_id == user_id).order_by(ConsentLog.created_at.desc()).first()
        return bool(c and c.kapi)

    def verified(p: DoorPoint) -> bool:
        return (not p.hidden) and (p.score or 0) >= trust_score and (p.score or 0) > (p.wrong or 0)

    def out(p: DoorPoint) -> dict:
        return {
            "id": p.id,
            "door": p.door_k,
            "n": p.door_n,
            "lat": round(p.lat, 6),
            "lon": round(p.lon, 6),
            "street": p.street,
            "site": p.site or "",
            "verified": verified(p),
            "confirms": p.confirms or 0,
        }

    def recompute(db: Session, p: DoorPoint) -> DoorPoint | None:
        votes = db.query(DoorReport).filter(DoorReport.point_id == p.id).all()
        conf = [v for v in votes if v.vote == "confirm"]
        if not conf:  # onaylayan kalmadı: nokta ve (varsa) yanlış oyları anlamsız
            for v in votes:
                db.delete(v)
            # Oylar ÖNCE silinmeli: modeller arasında relationship() yok, ORM silme sırasını bilmez; PostgreSQL yabancı anahtarı
            # zorlar (SQLite zorlamaz, bu yüzden yalnız gerçek veritabanında "FK ihlali" olurdu).
            db.flush()
            db.delete(p)
            return None
        base = [v for v in conf if v.source == "recipient"] or conf  # alıcının kendi pini varsa konumu o belirler
        w = sum(v.weight for v in base)
        p.lat = sum(v.lat * v.weight for v in base) / w
        p.lon = sum(v.lon * v.weight for v in base) / w
        p.score = sum(v.weight for v in conf)
        p.confirms = len(conf)
        p.wrong = sum(v.weight for v in votes if v.vote == "wrong")
        p.hidden = bool(p.wrong >= hide_wrong and p.wrong > p.score)  # eşitlikte gizlenmez ama "doğrulanmış" da sayılmaz
        p.updated_at = now()
        return p

    def candidates(db: Session, c: Clean) -> list[DoorPoint]:
        rows = db.query(DoorPoint).filter(DoorPoint.district_k == c.district_k, DoorPoint.neigh_k == c.neigh_k, DoorPoint.door_k == c.door_k).all()
        return [p for p in rows if province_ok(p.province_k, c.province_k) and same_street(p.street_k, p.street_kind, c.street_k, c.street_kind)]

    def vote_on(db: Session, c: Clean, reporter: str, weight: float, source: str) -> dict:
        cands = candidates(db, c)
        touched: dict[str, DoorPoint] = {}
        created = False
        if c.vote == "wrong":
            near = [p for p in cands if dist_m(p.lat, p.lon, c.lat, c.lon) <= wrong_radius_m]
            if not near:
                return {"status": "no_point"}
            p = min(near, key=lambda x: dist_m(x.lat, x.lon, c.lat, c.lon))
        else:
            near = [p for p in cands if dist_m(p.lat, p.lon, c.lat, c.lon) <= merge_m]
            if near:
                p = max(near, key=lambda x: (x.score or 0, -dist_m(x.lat, x.lon, c.lat, c.lon)))
            else:
                t = now()
                p = DoorPoint(
                    province_k=c.province_k, district_k=c.district_k, neigh_k=c.neigh_k, street_k=c.street_k, street_kind=c.street_kind,
                    door_k=c.door_k, door_n=c.door_n, province=c.province, district=c.district, neighborhood=c.neighborhood, street=c.street, site=c.site,
                    lat=c.lat, lon=c.lon, score=0.0, wrong=0.0, confirms=0, hidden=False, created_at=t, updated_at=t,
                )
                db.add(p)
                db.flush()
                created = True
        if c.vote == "confirm" and c.site and not p.site:
            p.site = c.site  # ilk bildirilen site/bina adı kalır
        existing = db.query(DoorReport).filter(DoorReport.point_id == p.id, DoorReport.reporter == reporter).first()
        if existing is not None:
            if existing.vote == c.vote:
                return {"status": "duplicate", **out(p)}
            existing.vote, existing.weight, existing.source, existing.lat, existing.lon = c.vote, weight, source, c.lat, c.lon
            status = "changed"
        else:
            db.add(DoorReport(point_id=p.id, reporter=reporter, vote=c.vote, source=source, weight=weight, lat=c.lat, lon=c.lon, created_at=now()))
            status = "new" if created else ("flagged" if c.vote == "wrong" else "confirmed")
        touched[p.id] = p
        db.flush()
        if c.vote == "confirm":
            # Aynı kişi aynı kapı için başka bir yer gösteriyorsa (düzeltme) eski oyu çekilmiş sayılır: kişi tek yere oy verir
            others = [x for x in cands if x.id != p.id]
            if others:
                old = db.query(DoorReport).filter(DoorReport.reporter == reporter, DoorReport.vote == "confirm", DoorReport.point_id.in_([x.id for x in others])).all()
                byid = {x.id: x for x in others}
                for o in old:
                    touched[o.point_id] = byid[o.point_id]
                    db.delete(o)
                db.flush()
        res_point = None
        for tp in touched.values():
            kept = recompute(db, tp)
            if tp.id == p.id:
                res_point = kept
        db.flush()
        if res_point is None:  # kişi kendi tek oyunu "yanlış"a çevirdi: nokta silindi
            return {"status": status, "id": None, "verified": False}
        return {"status": status, **out(res_point)}

    @r.post("/v1/doors/report")
    def report(body: ReportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kapı noktası katkısı (toplu, çevrimdışı kuyruktan). Her öğe ayrı değerlendirilir; biri bozuksa diğerleri etkilenmez."""
        if require_consent and not has_consent(db, user.id):
            raise HTTPException(403, "Kapı noktası katkısı için açık rıza gerekli (Hesabım › Gizlilik ve izinler)")
        reporter = reporter_code(user.id)
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        used = db.query(func.count(DoorReport.id)).filter(DoorReport.reporter == reporter, DoorReport.created_at >= since).scalar() or 0
        results, accepted = [], 0
        for raw in body.items:
            try:
                c = Clean(ItemIn.model_validate(raw))
            except ValidationError:
                results.append({"status": "rejected", "reason": "alan_gecersiz"})
                continue
            except ValueError as e:
                results.append({"status": "rejected", "reason": str(e)})
                continue
            if c.accuracy_m is not None and c.accuracy_m > max_accuracy_m:
                results.append({"status": "rejected", "reason": "gps_zayif"})
                continue
            if used >= daily_max:
                results.append({"status": "rejected", "reason": "gunluk_sinir"})
                continue
            weight = SOURCE_WEIGHT[c.source]
            res = vote_on(db, c, reporter, weight, c.source)
            if res["status"] in ("new", "confirmed", "flagged"):
                used += 1
            if res["status"] in ("new", "confirmed", "changed", "flagged"):
                accepted += 1
            results.append(res)
        if accepted:
            events.log(db, "door.report", user.id, n=accepted)
        db.commit()
        return {"results": results, "accepted": accepted}

    def area_points(db: Session, district: str, neighborhood: str) -> tuple[str, str, list[DoorPoint]]:
        dk, nk = district_core(district), neigh_core(neighborhood)
        if not dk or not nk:
            raise HTTPException(400, "İlçe ve mahalle gerekli")
        rows = db.query(DoorPoint).filter(DoorPoint.district_k == dk, DoorPoint.neigh_k == nk, DoorPoint.hidden.is_(False)).limit(6000).all()
        return dk, nk, rows

    @r.get("/v1/doors/street")
    def street_doors(
        district: str,
        neighborhood: str,
        street: str,
        province: str = "",
        lat: float | None = None,
        lon: float | None = None,
        limit: int = 400,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        """Bir sokağın bilinen kapı noktaları (kapı numarası başına en iyi aday), numaraya göre sıralı."""
        _, _, rows = area_points(db, district, neighborhood)
        sc, kind = street_parts(_NO_TAIL.sub("", street))
        if not sc:
            raise HTTPException(400, "Sokak gerekli")
        pk = province_core(province)
        best: dict[str, DoorPoint] = {}

        def rank(p: DoorPoint):
            d = dist_m(p.lat, p.lon, lat, lon) if lat is not None and lon is not None else 0.0
            return (-(p.score or 0), d, -(p.updated_at.timestamp() if p.updated_at else 0))

        for p in rows:
            if not province_ok(p.province_k, pk) or not same_street(p.street_k, p.street_kind, sc, kind):
                continue
            if lat is not None and lon is not None and dist_m(p.lat, p.lon, lat, lon) > 60000:
                continue  # başka ilin aynı adlı ilçe/mahallesi
            cur = best.get(p.door_k)
            if cur is None or rank(p) < rank(cur):
                best[p.door_k] = p
        doors = sorted(best.values(), key=lambda p: (p.door_n, p.door_k))[: max(1, min(limit, 800))]
        return {"count": len(doors), "doors": [out(p) for p in doors]}

    @r.get("/v1/doors/streets")
    def streets(
        district: str,
        neighborhood: str,
        q: str = "",
        province: str = "",
        limit: int = 20,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        """Mahallede ortak katmanda bilinen sokak adları (öneri için). q: yazılan harfler."""
        _, _, rows = area_points(db, district, neighborhood)
        pk = province_core(province)
        qk = " ".join(words(q))
        groups: dict[tuple[str, str], list[DoorPoint]] = {}
        for p in rows:
            if province_ok(p.province_k, pk):
                groups.setdefault((p.street_k, p.street_kind), []).append(p)
        found = []
        for (sk, kind), pts in groups.items():
            if qk and not (sk.startswith(qk) or any(w.startswith(qk) for w in sk.split()) or qk in sk):
                continue
            name = Counter(p.street for p in pts).most_common(1)[0][0]
            found.append({
                "name": name,
                "kind": kind,
                "doors": len({p.door_k for p in pts}),
                "lat": round(sum(p.lat for p in pts) / len(pts), 6),
                "lon": round(sum(p.lon for p in pts) / len(pts), 6),
                "_prefix": 0 if sk.startswith(qk) else 1,
            })
        found.sort(key=lambda x: (x["_prefix"], -x["doors"], x["name"]))
        for x in found:
            x.pop("_prefix")
        return {"streets": found[: max(1, min(limit, 50))]}

    @r.get("/v1/doors/near")
    def near(lat: float = Query(ge=34.0, le=43.0), lon: float = Query(ge=25.0, le=45.5), radius: float = 150, limit: int = 120, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Bir noktanın çevresindeki kapı noktaları (harita katmanı için). Yarıçap en çok 500 m."""
        rad = max(20.0, min(radius, 500.0))
        dlat = rad / 111320.0
        dlon = rad / (111320.0 * max(0.2, math.cos(math.radians(lat))))
        rows = (
            db.query(DoorPoint)
            .filter(DoorPoint.hidden.is_(False), DoorPoint.lat.between(lat - dlat, lat + dlat), DoorPoint.lon.between(lon - dlon, lon + dlon))
            .limit(1000)
            .all()
        )
        hits = sorted(((dist_m(p.lat, p.lon, lat, lon), p) for p in rows), key=lambda x: x[0])
        return {"doors": [out(p) for d, p in hits if d <= rad][: max(1, min(limit, 300))]}

    @r.get("/v1/doors/stats")
    def stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Katmanın büyüklüğü ve bu kişinin katkısı (kaç noktayı onayladı)."""
        total = db.query(func.count(DoorPoint.id)).filter(DoorPoint.hidden.is_(False)).scalar() or 0
        ok = db.query(func.count(DoorPoint.id)).filter(DoorPoint.hidden.is_(False), DoorPoint.score >= trust_score, DoorPoint.score > DoorPoint.wrong).scalar() or 0
        mine = db.query(func.count(DoorReport.id)).filter(DoorReport.reporter == reporter_code(user.id), DoorReport.vote == "confirm").scalar() or 0
        return {"points": total, "verified": ok, "mine": mine}

    def add_recipient_pin(db: Session, it: dict, recipient_key: str, trusted: bool = True) -> dict:
        """Alıcı sayfasından (teslim onayı) gelen kapı pini. recipient_key: bağlantıya özel kod (kişiyi ele vermez; her bağlantı tek oy).
        trusted=True: bağlantı alıcıya SUNUCUDAN ulaştı, kurye görmedi → alıcının kendi sözü, puan 2, konumu o belirler.
        trusted=False: kurye bağlantıyı gördü (test modu / QR / paylaşım) → kurye "alıcı" gibi pin koyabilir; sıradan oy gibi puan 1."""
        source = "recipient" if trusted else "pin"
        c = Clean(ItemIn.model_validate({**it, "source": "pin", "vote": "confirm"}))
        c.source = source
        code = hmac.new(SECRET.encode(), ("door-recipient:" + recipient_key).encode(), hashlib.sha256).hexdigest()[:24]
        res = vote_on(db, c, code, SOURCE_WEIGHT[source], source)
        db.commit()
        return res

    r.add_recipient_pin = add_recipient_pin  # type: ignore[attr-defined]
    r.reporter_code = reporter_code  # type: ignore[attr-defined]
    return r
