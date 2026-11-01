"""ÖDÜL KOYANIN KARNESİ, GÖRÜNÜRLÜĞÜ ve YÖNETİM İŞARETLERİ (1.16.0; kullanıcı kararı 27 Ekim 2026).

Reklam ile hediye birbirine BAĞLANMAZ (reklam her zaman peşin, coin ile). Sponsoru / işvereni ödüllendirmenin yolu GÖRÜNÜRLÜK:
- GÜVENİLİR (kendiliğinden): en az 3 kargolanmış ödül, %90+ zamanında, memnuniyet 4+ (ya da henüz puan yok), açık şikâyet yok,
  son 90 günde geç kargo yok → katalogda "✓ Güvenilir" rozeti ve üst sıra.
- DÜZENSİZ (kendiliğinden): son 90 günde 2+ geç kargo (7 günde kargolanmadı) YA DA 2+ "gelmedi" şikâyeti YA DA (3+ puanlamada)
  memnuniyet 2,5'in altı → ödülleri katalogdan iner, yeni ödül koyamaz. Yönetim "affet" derse verilen süre boyunca görünür.
- ÖNE ÇIKAN (yönetim verir, gün sayısıyla) → katalogda en üstte "⭐ Öne çıkan".
- KARA LİSTE (yönetim koyar; not zorunlu, notu YALNIZ yönetim görür): kurye ödül alamaz; sponsor / işveren ödül koyamaz ve ödülleri
  katalogda görünmez. Kargo bekleyen talepler yine gönderilmeli (kurye korunur). Hesap askıya alma (men) bundan ayrıdır.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import RewardClaim, UserMark, now

WINDOW_DAYS = 90
TRUST_MIN_SHIPPED = 3
TRUST_ON_TIME = 0.9
TRUST_STARS = 4.0
IRREG_LATE = 2
IRREG_DISPUTES = 2
IRREG_STARS = 2.5
IRREG_STARS_MIN_N = 3
LEVEL_ORDER = {"blacklisted": 0, "hidden": 1, "featured": 2, "trusted": 3, "normal": 4}


def utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    d = utc(dt)
    return d.isoformat() if d else None


def marks_of(db: Session, ids: set[str] | list[str]) -> dict[str, UserMark]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {m.user_id: m for m in db.query(UserMark).filter(UserMark.user_id.in_(ids)).all()}


def mark_of(db: Session, user_id: str) -> UserMark | None:
    return db.get(UserMark, user_id) if user_id else None


def mark_for_update(db: Session, user_id: str) -> UserMark:
    m = db.get(UserMark, user_id)
    if m is None:
        m = UserMark(user_id=user_id, blacklisted=False, note="", listed_by="", note_by="", featured_by="")
        db.add(m)
        db.flush()
    return m


def is_blacklisted(db: Session, user_id: str) -> bool:
    m = mark_of(db, user_id)
    return bool(m and m.blacklisted)


def _empty() -> dict:
    return {"claims": 0, "shipped": 0, "onTime": 0, "late": 0, "late90": 0, "disputed": 0, "disputed90": 0, "openDisputes": 0,
            "starsN": 0, "starsSum": 0, "starsN90": 0, "starsSum90": 0}


def owner_stats(db: Session, ids: set[str] | list[str]) -> dict[str, dict]:
    """Ödül koyanların karne sayıları (bütün talepler + son 90 gün)."""
    ids = {i for i in ids if i}
    out = {i: _empty() for i in ids}
    if not ids:
        return out
    since = now() - timedelta(days=WINDOW_DAYS)
    for c in db.query(RewardClaim).filter(RewardClaim.owner_id.in_(ids)).all():
        s = out[c.owner_id]
        recent = (utc(c.created_at) or now()) >= since
        s["claims"] += 1
        if c.shipped_at is not None:
            s["shipped"] += 1
            s["onTime"] += 1 if c.on_time else 0
        if c.close_reason == "late_ship":
            s["late"] += 1
            s["late90"] += 1 if recent else 0
        if c.status == "disputed" or c.close_reason == "admin_refund":
            s["disputed"] += 1
            s["disputed90"] += 1 if recent else 0
        if c.status == "disputed":
            s["openDisputes"] += 1
        if c.stars:
            s["starsN"] += 1
            s["starsSum"] += c.stars
            if recent:
                s["starsN90"] += 1
                s["starsSum90"] += c.stars
    return out


def standing(s: dict | None, m: UserMark | None, t: datetime | None = None) -> dict:
    """Karne → durum. `level`: blacklisted | hidden | featured | trusted | normal."""
    s = s or _empty()
    t = t or now()
    due = s["shipped"] + s["late"]
    on_time_pct = round(100 * s["onTime"] / due) if due else None
    avg = round(s["starsSum"] / s["starsN"], 1) if s["starsN"] else None
    avg90 = s["starsSum90"] / s["starsN90"] if s["starsN90"] else None
    reasons: list[str] = []
    if s["late90"] >= IRREG_LATE:
        reasons.append(f"Son 90 günde {s['late90']} ödül 7 gün içinde kargolanmadı")
    if s["disputed90"] >= IRREG_DISPUTES:
        reasons.append(f"Son 90 günde {s['disputed90']} “gelmedi” şikâyeti")
    if s["starsN90"] >= IRREG_STARS_MIN_N and avg90 is not None and avg90 < IRREG_STARS:
        reasons.append(f"Son 90 günde memnuniyet {avg90:.1f} / 5".replace(".", ","))
    irregular = bool(reasons)
    blacklisted = bool(m and m.blacklisted)
    featured = bool(m and m.featured_until and utc(m.featured_until) > t)
    pardoned = bool(m and m.pardon_until and utc(m.pardon_until) > t)
    trusted = (
        not irregular and s["shipped"] >= TRUST_MIN_SHIPPED and due > 0 and s["onTime"] / due >= TRUST_ON_TIME
        and (avg is None or avg >= TRUST_STARS) and s["openDisputes"] == 0 and s["late90"] == 0
    )
    hidden = blacklisted or (irregular and not pardoned)
    level = "blacklisted" if blacklisted else "hidden" if hidden else "featured" if featured else "trusted" if trusted else "normal"
    return {
        "level": level, "trusted": trusted, "featured": featured and not hidden, "featuredUntil": iso(m.featured_until) if featured else None,
        "irregular": irregular, "hidden": hidden, "pardoned": pardoned and irregular, "pardonUntil": iso(m.pardon_until) if pardoned else None,
        "blacklisted": blacklisted, "reasons": reasons,
        "claims": s["claims"], "shipped": s["shipped"], "onTimePct": on_time_pct, "late": s["late"], "late90": s["late90"],
        "disputed": s["disputed"], "disputed90": s["disputed90"], "openDisputes": s["openDisputes"], "avgStars": avg, "starsN": s["starsN"],
        "trustNeed": {"shipped": TRUST_MIN_SHIPPED, "onTimePct": round(TRUST_ON_TIME * 100), "stars": TRUST_STARS},
    }


def standings(db: Session, ids: set[str] | list[str]) -> dict[str, dict]:
    ids = {i for i in ids if i}
    stats = owner_stats(db, ids)
    marks = marks_of(db, ids)
    t = now()
    return {i: standing(stats.get(i), marks.get(i), t) for i in ids}


def public_standing(st: dict) -> dict:
    """Ödül koyanın kendisine gösterilen karne (yönetim notu YOK; kara listede nedeni genel)."""
    out = dict(st)
    if st["blacklisted"]:
        out["reasons"] = ["Yönetim ödül koymanı durdurdu"]
    return out


def mark_out(m: UserMark | None) -> dict:
    """Yönetim için işaret (not dahil)."""
    if m is None:
        return {"blacklisted": False, "note": "", "listedAt": None, "listedBy": "", "noteAt": None, "noteBy": "", "featuredUntil": None, "featuredBy": "", "pardonUntil": None}
    t = now()
    return {
        "blacklisted": bool(m.blacklisted), "note": m.note or "", "listedAt": iso(m.listed_at), "listedBy": m.listed_by or "",
        "noteAt": iso(m.note_at), "noteBy": m.note_by or "",
        "featuredUntil": iso(m.featured_until) if m.featured_until and utc(m.featured_until) > t else None, "featuredBy": m.featured_by or "",
        "pardonUntil": iso(m.pardon_until) if m.pardon_until and utc(m.pardon_until) > t else None,
    }
