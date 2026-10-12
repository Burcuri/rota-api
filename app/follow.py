"""Takip ("Follow"): işveren, mesajlaştığı bir kuryeyi harita üzerinde canlı izler.

Kurallar (sunucu uygular; hepsi testli):
- İZİN KURYEDEN gelir, HER SEFERİNDE: işveren istek gönderir (asked); kurye KABUL ETMEZSE hiçbir konum alınmaz. Kurye istediği an durdurur.
- İKİ KAYIT TÜRÜ vardır:
  (1) CANLI nokta: satırda yalnız SON nokta durur (yenisi gelince üstüne yazılır). Takip bitince (durdurma, ret, süre, men, rol değişimi)
      ya da nokta FOLLOW_STALE_MIN (30 dk) dakikadan eskiyse silinir. Haritadaki canlı konumu yalnız işveren, yalnız takip sürerken görür.
  (2) İZ + SON İLERLEME ("son yapılan işler"): takip sürerken en sık ~15 sn'de bir seyreltilmiş noktalar (follow_points) ve kuryenin son
      ilerlemesi (hangi durak ne zaman bitti) takip BİTTİKTEN SONRA da FOLLOW_KEEP_HOURS (48 saat) saklanır; şikayet / itiraz olursa
      "kurye oradaydı mı, teslim ne zaman yapıldı" bakılabilsin diye. Bu kaydı işveren, kurye ve yönetim görür; 48 saat sonra silinir.
- Yönetim sunucudaki bu kayıtların hepsine bakabilir (yönetim panelinde "Takipler"; her bakış işlem günlüğüne yazılır). Etkinlik akışına
  (events) koordinat yazılmaz (yalnız "istek / başladı / bitti (neden)").
- Bir kuryenin aynı anda tek aktif takibi olur (bir iş, bir işletme). Bir işletme birden çok kuryeyi izleyebilir.
- Takip en çok FOLLOW_MAX_HOURS (12 saat) sürer; dolunca kapanır, yeniden izin gerekir. Konum yalnız kuryenin uygulaması AÇIKKEN gelir
  (arka plan konumu yok); uygulama kapanınca canlı nokta yaşlanır ve 30 dakika sonra silinir. Takip bitince iz yeni nokta ALMAZ.
- Kurye ilerlemesi: bitirilen / başarısız durak KİMLİKLERİ (telefonun ürettiği rastgele kodlar), toplam, ilk görülme anları ve her biten
  durak için KISA ADRES ETİKETİ ("Barbaros Cad. No:11 · Mahmutlar": yalnız sokak adı, kapı no, mahalle). Müşteri adı, telefon, daire, kat
  ve not ASLA gelmez; telefon / e-posta / bağlantı gibi görünen etiket atılır. Kullanıcı kararı (12 Ekim 2026): şikayette "hangi kapıya
  ne zaman gidildi" bakılabilsin diye etiket de 48 saat saklanır; kimlikler ayrı, etiketler `progress.a` içinde durur.
- Reddedilen istek FOLLOW_COOLDOWN_MIN (10 dk) içinde aynı işverenden yeniden gelmez; açık istek varken tekrar istek yeni satır açmaz.
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from . import events
from .auth import current_user
from .db import SessionLocal, get_db
from .limits import Window
from .models import Follow, FollowPoint, User, now, uid

ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
MAX_IDS = 500
LABEL_MAX = 90  # bir durağın adres etiketi en çok bu kadar karakter
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_PHONEISH_RE = re.compile(r"(?:\d[\s().+-]*){10,}")  # peş peşe 10+ rakam: telefon numarası gibi
_LINKISH_RE = re.compile(r"@|https?:|www\.", re.IGNORECASE)


def clean_label(s: object) -> str:
    """Durağın kısa adres etiketini temizler: boşlukları sadeleştirir, LABEL_MAX'ta keser. Telefon / e-posta / bağlantı gibi görünen
    metin boş döner (etiket atılır). Sokak numarası gibi kısa rakam dizileri serbesttir."""
    if not isinstance(s, str):
        return ""
    t = re.sub(r"\s+", " ", _CTRL_RE.sub(" ", s)).strip()
    if not t or _LINKISH_RE.search(t) or _PHONEISH_RE.search(t):
        return ""
    return t[:LABEL_MAX].rstrip()


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    d = _utc(dt)
    return d.isoformat() if d else None


class RequestIn(BaseModel):
    courier_id: str = Field(alias="courierId", min_length=1, max_length=40)

    model_config = {"populate_by_name": True}


class ProgressIn(BaseModel):
    done: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    failed: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    total: int = Field(default=0, ge=0, le=1000)
    # Biten durakların kısa adres etiketleri: durak kimliği → "Sokak No:N · Mahalle" (ayrıntı: modül açıklaması)
    a: dict[Annotated[str, StringConstraints(max_length=40)], Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=dict, max_length=2 * MAX_IDS)


class PingIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    acc: float | None = Field(default=None, ge=0, le=100000)
    progress: ProgressIn | None = None


class StopIn(BaseModel):
    reason: str = Field(default="", max_length=12)  # "done": kurye bütün teslimatları bitirdi
    # Kuryenin SON ilerlemesi: bitirirken (ya da durdururken) kayda girer; konum yoklamasıyla yarışıp son teslim eksik kalmasın diye
    progress: ProgressIn | None = None


def _clean_progress(p: ProgressIn, prev: dict | None = None, at=None) -> dict:
    """İlerlemeyi temizler ve her kimliğin SUNUCUDA ilk görüldüğü anı ("t") ekler. Zaten bilinen kimliğin anı korunur;
    bir kimlik durum değiştirirse (başarısız → teslim) an yenilenir. Telefonun saati değil sunucu saati yazılır."""

    def ok(xs: list[str]) -> list[str]:
        return [x for x in dict.fromkeys(xs) if ID_RE.match(x)][:MAX_IDS]

    done, failed = ok(p.done), ok(p.failed)
    prev = prev or {}
    prev_t = prev.get("t") if isinstance(prev.get("t"), dict) else {}
    prev_a = prev.get("a") if isinstance(prev.get("a"), dict) else {}
    same_done, same_failed = set(prev.get("done") or []), set(prev.get("failed") or [])
    stamp = int((at or now()).timestamp())
    t: dict[str, int] = {}
    for ids, same in ((done, same_done), (failed, same_failed)):
        for i in ids:
            old_t = prev_t.get(i)
            t[i] = int(old_t) if (i in same and isinstance(old_t, (int, float))) else stamp
    # Adres etiketi yalnız BİTEN duraklar için tutulur. Yeni etiket gelmediyse (ya da geçersizse) öncekisi korunur.
    a: dict[str, str] = {}
    for i in (*done, *failed):
        lab = clean_label(p.a.get(i)) or clean_label(prev_a.get(i))
        if lab:
            a[i] = lab
    return {"done": done, "failed": failed, "total": p.total, "t": t, "a": a}


def _progress_of(f: Follow) -> dict:
    try:
        d = json.loads(f.progress or "{}")
        if isinstance(d, dict) and isinstance(d.get("done"), list):
            t = d.get("t") if isinstance(d.get("t"), dict) else {}
            a = d.get("a") if isinstance(d.get("a"), dict) else {}
            return {
                "done": d.get("done", []), "failed": d.get("failed", []), "total": int(d.get("total") or 0),
                "t": {k: int(v) for k, v in t.items() if isinstance(v, (int, float))},
                "a": {k: clean_label(v) for k, v in a.items() if isinstance(k, str) and clean_label(v)},
            }
    except Exception:
        pass
    return {"done": [], "failed": [], "total": 0, "t": {}, "a": {}}


TRAIL_OUT_MAX = 1500  # bir yanıtta en çok bu kadar iz noktası (fazlası eşit aralıkla seyreltilir; ilk ve son nokta kalır)


def trail_of(db: Session, fid: str, limit: int = TRAIL_OUT_MAX) -> list[list]:
    """Takip izi: zaman sırasıyla [[saniye, enlem, boylam], ...]."""
    rows = db.query(FollowPoint.at, FollowPoint.lat, FollowPoint.lon).filter(FollowPoint.follow_id == fid).order_by(FollowPoint.at.asc(), FollowPoint.id.asc()).all()
    if limit > 1 and len(rows) > limit:
        step = (len(rows) - 1) / (limit - 1)
        rows = [rows[round(i * step)] for i in range(limit)]
    return [[int(_utc(a).timestamp()), round(la, 6), round(lo, 6)] for a, la, lo in rows]


def drop_trail(db: Session, ids: list[str]) -> None:
    """Verilen takiplerin iz noktalarını siler (commit ETMEZ)."""
    for i in range(0, len(ids), 500):
        db.query(FollowPoint).filter(FollowPoint.follow_id.in_(ids[i:i + 500])).delete(synchronize_session=False)


def make_router(
    ask_min: float, max_hours: float, keep_hours: float, stale_min: float, cooldown_min: float, trail_sec: float = 15, trail_max: int = 4000
) -> APIRouter:
    r = APIRouter()
    req_win = Window(30, 3600)  # işveren başına saatte en çok 30 yeni istek
    ping_win = Window(40, 60)  # takip başına dakikada en çok 40 konum (uygulama 10–30 sn'de bir gönderir)
    trail_win = Window(60, 60)  # kullanıcı başına dakikada en çok 60 iz isteği

    # ------------------------------------------------------------------ iç işler
    def end(db: Session, f: Follow, reason: str, actor: str | None = None) -> None:
        """Takibi kapatır; CANLI konumu SİLER (iz ve son ilerleme FOLLOW_KEEP_HOURS boyunca kalır). commit ETMEZ."""
        if f.status == "ended":
            return
        was = f.status
        f.status = "ended"
        f.end_reason = reason
        f.ended_at = now()
        f.last_lat = f.last_lon = f.last_acc = None
        f.last_at = None
        # Bu satır tam bitirme anında yüklenmiş olabilir: o sırada başka bir istek yeni canlı nokta yazdıysa, "zaten boş" sanılan alanlar
        # güncellemeye girmez ve nokta kalır. Bu yüzden silinen alanlar HER ZAMAN yazılır. (Son ilerleme ve iz silinmez.)
        for col in ("last_lat", "last_lon", "last_acc", "last_at"):
            flag_modified(f, col)
        events.log(db, "follow.end", actor, f.id, reason=reason, was=was)

    def check(db: Session, f: Follow) -> bool:
        """Açık (bekleyen / aktif) takip hâlâ geçerli mi? Geçersizse kapatır (commit çağırana ait). True: geçerli."""
        if f.status not in ("asked", "active"):
            return False
        t = now()
        if _utc(f.expires_at) < t:
            end(db, f, "expired")
            return False
        courier = db.get(User, f.courier_id)
        employer = db.get(User, f.employer_id)
        if courier is None or courier.banned or employer is None or employer.banned:
            end(db, f, "ban")
            return False
        if f.status == "active" and courier.active_role != "courier":  # kurye modundan çıktı: konum paylaşımı sona erer
            end(db, f, "role")
            return False
        if f.status == "active" and f.last_at is not None and (t - _utc(f.last_at)) > timedelta(minutes=stale_min):
            f.last_lat = f.last_lon = f.last_acc = None  # eski nokta silinir; takip sürer (kurye uygulamayı açınca yenisi gelir)
            f.last_at = None
        return True

    def names_of(db: Session, rows: list[Follow]) -> dict[str, str]:
        ids = {x for f in rows for x in (f.employer_id, f.courier_id)}
        if not ids:
            return {}
        return {i: n for i, n in db.query(User.id, User.name).filter(User.id.in_(ids)).all()}

    def out(f: Follow, viewer_id: str, names: dict[str, str]) -> dict:
        side = "employer" if f.employer_id == viewer_id else "courier"
        peer_id = f.courier_id if side == "employer" else f.employer_id
        o = {
            "id": f.id,
            "side": side,
            "status": f.status,
            "endReason": f.end_reason or "",
            "peer": {"id": peer_id, "name": names.get(peer_id, "")},
            "createdAt": _iso(f.created_at),
            "acceptedAt": _iso(f.accepted_at),
            "endedAt": _iso(f.ended_at),
            "expiresAt": _iso(f.expires_at),
            "trailN": int(f.trail_n or 0),
        }
        if f.status == "active" or (f.status == "ended" and (f.progress or "")):
            o["progress"] = _progress_of(f)  # biten takibin SON ilerlemesi de 48 saat görünür
        if f.status == "active":
            # CANLI konumu YALNIZ işveren ve YALNIZ takip sürerken görür (biten takibin izi ayrı uçtan, /trail)
            if side == "employer" and f.last_lat is not None and f.last_lon is not None and f.last_at is not None:
                age = (now() - _utc(f.last_at)).total_seconds()
                if age <= stale_min * 60:
                    o["location"] = {"lat": f.last_lat, "lon": f.last_lon, "acc": f.last_acc, "at": _iso(f.last_at), "ageSec": int(max(0, age))}
        return o

    def participant(db: Session, fid: str, user: User) -> Follow:
        f = db.get(Follow, fid)
        if f is None or user.id not in (f.employer_id, f.courier_id):
            raise HTTPException(404, "Takip bulunamadı")  # başkasının takibinin varlığı açıklanmaz
        return f

    def view(db: Session, f: Follow, user: User, **extra) -> dict:
        return {**out(f, user.id, names_of(db, [f])), **extra}

    # ------------------------------------------------------------------ uçlar
    @r.post("/v1/follow/request")
    def request_follow(b: RequestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """İşveren, mesajlaştığı kuryeden takip izni ister. Kurye kabul edene kadar hiçbir konum alınmaz."""
        if user.active_role != "employer":
            raise HTTPException(403, "Takibi yalnız işveren başlatır")
        if b.courier_id == user.id:
            raise HTTPException(400, "Kendini takip edemezsin")
        c = db.get(User, b.courier_id)
        if c is None or c.banned:
            raise HTTPException(404, "Kurye bulunamadı")
        if c.active_role != "courier":
            raise HTTPException(400, "Bu kişi şu an kurye modunda değil")
        open_ = db.query(Follow).filter(Follow.employer_id == user.id, Follow.courier_id == c.id, Follow.status.in_(("asked", "active"))).first()
        if open_ is not None and check(db, open_):  # aynı kurye için açık kayıt: yeni satır açılmaz
            db.commit()
            return view(db, open_, user, existing=True)
        cut = now() - timedelta(minutes=cooldown_min)
        if db.query(Follow.id).filter(Follow.employer_id == user.id, Follow.courier_id == c.id, Follow.end_reason == "declined", Follow.ended_at >= cut).first():
            db.commit()
            raise HTTPException(429, "Kurye isteği az önce reddetti; biraz sonra yeniden dene")
        busy = db.query(Follow).filter(Follow.courier_id == c.id, Follow.status == "active").first()
        if busy is not None and check(db, busy):
            db.commit()
            raise HTTPException(409, "Kurye şu an başka bir takipte; teslimatlarını bitirince yeni iş verebilirsin")
        if not req_win.allow(user.id):
            db.commit()
            raise HTTPException(429, "Çok fazla takip isteği gönderdin; biraz bekle")
        f = Follow(id=uid(), employer_id=user.id, courier_id=c.id, status="asked", expires_at=now() + timedelta(minutes=ask_min))
        db.add(f)
        events.log(db, "follow.ask", user.id, f.id, to=c.id)
        try:
            db.commit()
        except IntegrityError:  # aynı anda başka istek açıldı
            db.rollback()
            raise HTTPException(409, "Bu kurye için zaten açık bir istek var")
        return view(db, f, user, existing=False)

    @r.get("/v1/follow/mine")
    def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Benim takiplerim (işveren ya da kurye tarafı): bekleyen, aktif ve son FOLLOW_KEEP_HOURS (48 saat) içinde biten. Canlı konum yalnız işverene, yalnız aktifken."""
        rows = (
            db.query(Follow)
            .filter(or_(Follow.employer_id == user.id, Follow.courier_id == user.id))
            .order_by(Follow.created_at.desc())
            .limit(60)
            .all()
        )
        for f in rows:
            if f.status in ("asked", "active"):
                check(db, f)
        db.commit()
        names = names_of(db, rows)
        return {"items": [out(f, user.id, names) for f in rows]}

    @r.get("/v1/follow/{fid}")
    def get_one(fid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        f = participant(db, fid, user)
        check(db, f)
        db.commit()
        return view(db, f, user)

    @r.post("/v1/follow/{fid}/accept")
    def accept(fid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kurye izin verir: takip başlar. Başka bir takibi sürüyorsa önce onu durdurmalıdır."""
        f = participant(db, fid, user)
        if f.courier_id != user.id:
            raise HTTPException(403, "Takibi yalnız kurye kabul eder")
        if user.active_role != "courier":
            raise HTTPException(403, "Takibi kurye modunda kabul edebilirsin")
        if f.status == "active" and check(db, f):
            db.commit()
            return view(db, f, user)
        if f.status != "asked" or not check(db, f):
            db.commit()
            raise HTTPException(410, "Bu takip isteği artık geçerli değil")
        busy = db.query(Follow).filter(Follow.courier_id == user.id, Follow.status == "active", Follow.id != f.id).first()
        if busy is not None and check(db, busy):
            db.commit()
            raise HTTPException(409, "Zaten başka bir takip sürüyor; önce onu durdur")
        t = now()
        f.status = "active"
        f.accepted_at = t
        f.expires_at = t + timedelta(hours=max_hours)
        events.log(db, "follow.start", user.id, f.id)
        try:
            db.commit()
        except IntegrityError:  # aynı anda başka takip aktif oldu
            db.rollback()
            raise HTTPException(409, "Zaten başka bir takip sürüyor; önce onu durdur")
        return view(db, f, user)

    @r.post("/v1/follow/{fid}/decline")
    def decline(fid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        f = participant(db, fid, user)
        if f.courier_id != user.id:
            raise HTTPException(403, "İsteği yalnız kurye reddeder")
        if f.status == "asked":
            end(db, f, "declined", user.id)
        elif f.status == "active":
            end(db, f, "stopped_courier", user.id)
        db.commit()
        return view(db, f, user)

    @r.post("/v1/follow/{fid}/stop")
    def stop(fid: str, b: StopIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """İşveren de kurye de takibi her an bitirir. Kurye `reason: "done"` ile "bütün teslimatları bitirdim" der. Konum silinir.
        Kurye bitirirken son ilerlemesini de gönderebilir (yalnız KURYENİN gönderdiği ilerleme kabul edilir)."""
        f = participant(db, fid, user)
        if f.status in ("asked", "active"):
            if user.id == f.employer_id:
                end(db, f, "cancelled" if f.status == "asked" else "stopped_employer", user.id)
            elif f.status == "asked":
                end(db, f, "declined", user.id)
            else:
                if b and b.progress is not None:
                    f.progress = json.dumps(_clean_progress(b.progress, _progress_of(f), now()), separators=(",", ":"))
                end(db, f, "done" if (b and b.reason == "done") else "stopped_courier", user.id)
            db.commit()
        return view(db, f, user)

    @r.post("/v1/follow/{fid}/ping")
    def ping(fid: str, b: PingIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kuryenin uygulaması (açıkken) son konumunu gönderir; canlı nokta üstüne yazılır, iz seyreltilerek eklenir. Takip sürmüyorsa konum ALINMAZ."""
        f = db.get(Follow, fid)
        if f is None or f.courier_id != user.id:
            raise HTTPException(404, "Takip bulunamadı")
        if not check(db, f) or f.status != "active":
            db.commit()
            return {"ok": False, "status": f.status, "endReason": f.end_reason or ""}
        if not ping_win.allow(f.id):
            raise HTTPException(429, "Konum çok sık gönderiliyor")
        t = now()
        vals: dict = {"last_lat": b.lat, "last_lon": b.lon, "last_acc": b.acc, "last_at": t}
        if b.progress is not None:
            vals["progress"] = json.dumps(_clean_progress(b.progress, _progress_of(f), t), separators=(",", ":"))
        take = (f.trail_n or 0) < trail_max and (f.trail_at is None or (t - _utc(f.trail_at)).total_seconds() >= trail_sec)
        if take:
            vals["trail_at"] = t
            vals["trail_n"] = func.coalesce(Follow.trail_n, 0) + 1
        # Yazma "takip hâlâ aktifse" koşuluyla TEK adımda yapılır: kontrol ile yazma arasında takip bitmişse (kurye "bitirdim" dedi,
        # işveren durdurdu) nokta veritabanına HİÇ girmez. Koşul veritabanında değerlendirilir; iki istek aynı anda gelse de geçerlidir.
        # İz noktası aynı işlemde eklenir: güncelleme tuttuysa (takip aktifti) nokta da girer, tutmadıysa girmez.
        n = db.query(Follow).filter(Follow.id == f.id, Follow.status == "active").update(vals, synchronize_session=False)
        if n and take:
            db.add(FollowPoint(follow_id=f.id, at=t, lat=b.lat, lon=b.lon))
        db.commit()
        if n == 0:
            db.refresh(f)
            return {"ok": False, "status": f.status, "endReason": f.end_reason or ""}
        return {"ok": True, "status": "active", "expiresAt": _iso(f.expires_at)}

    @r.get("/v1/follow/{fid}/trail")
    def trail(fid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Takip izi (seyreltilmiş geçmiş noktalar): bu takibin işvereni ve kuryesi, takip sürerken ve bittikten sonra FOLLOW_KEEP_HOURS boyunca görür."""
        f = participant(db, fid, user)
        if not trail_win.allow(user.id):
            raise HTTPException(429, "Çok sık istendi; biraz bekle")
        return {"id": f.id, "n": int(f.trail_n or 0), "points": trail_of(db, f.id)}

    # ------------------------------------------------------------------ temizlik
    def sweep(db: Session) -> int:
        """Süresi dolan / men edilen / kurye modundan çıkan takipleri kapatır; eski canlı noktaları siler; biten kayıtları (iz ve son
        ilerleme dahil) saklama süresinden (FOLLOW_KEEP_HOURS, 48 saat) sonra siler. Dönen: kapatılan + silinen sayısı."""
        n = 0
        for f in db.query(Follow).filter(Follow.status.in_(("asked", "active"))).all():
            if not check(db, f):
                n += 1
        cut = now() - timedelta(hours=keep_hours)
        old = [i for (i,) in db.query(Follow.id).filter(Follow.status == "ended", Follow.ended_at < cut).all()]
        for i in range(0, len(old), 500):
            chunk = old[i:i + 500]
            drop_trail(db, chunk)
            n += db.query(Follow).filter(Follow.id.in_(chunk)).delete(synchronize_session=False)
        db.commit()
        return n

    def loop():
        while True:
            time.sleep(300)
            try:
                db = SessionLocal()
                try:
                    sweep(db)
                finally:
                    db.close()
            except Exception as e:  # temizlik hatası sunucuyu düşürmesin
                print("[takip] temizlik hatası:", type(e).__name__, flush=True)

    r.sweep = sweep  # type: ignore[attr-defined]
    r.limiters = (req_win, ping_win, trail_win)  # type: ignore[attr-defined]
    r.start_cleaner = lambda: threading.Thread(target=loop, name="follow-sweep", daemon=True).start()  # type: ignore[attr-defined]
    return r
