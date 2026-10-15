"""Haritada yakındaki ROTA kullanıcıları + kısıtlı selam (Dilim 12).

- **Görünme AYRI açık rıza ister ve varsayılan KAPALI.** Açıkken konum yaklaşık (≈100 m) ve kısa süreli (VISIBLE_MIN dk) tutulur; kapatınca hemen silinir.
- **Karşılıklılık:** yakındakileri yalnız kendisi de görünür olan görür.
- Başkasına yalnız: ilk ad + soyad baş harfi, rol, ikon kimliği, yaklaşık konum.
- **Selam:** serbest metin yok; wave | nudge | hello. Gönderen görünür olmalı, hedef yakında ve görünür olmalı, engel yok, hız sınırı:
  saatte NUDGE_HOURLY, aynı kişiye NUDGE_PAIR_MIN dakikada bir.
- Engelleme çift yönlüdür (ikisi de birbirini görmez).
"""
import math
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import events
from .auth import current_user
from .db import SessionLocal, get_db
from .limits import Window
from .models import MapBlock, Nudge, User

KINDS = ("wave", "nudge", "hello")


class OnIn(BaseModel):
    on: bool


class PingIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class NudgeIn(BaseModel):
    to: str = Field(min_length=1, max_length=40)
    kind: str


class IdIn(BaseModel):
    id: str = Field(min_length=1, max_length=40)


class IdsIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=200)


def _utc(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dist_m(a_lat, a_lon, b_lat, b_lon) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def short_name(full: str) -> str:
    parts = [x for x in (full or "").split() if x]
    if not parts:
        return "ROTA kullanıcısı"
    if len(parts) == 1:
        return parts[0][:20]
    return f"{parts[0][:20]} {parts[-1][0]}."


def make_router(visible_min: int = 10, nudge_hourly: int = 12, nudge_pair_min: int = 10, nudge_keep_hours: int = 48, radius_max_m: int = 5000) -> APIRouter:
    r = APIRouter()
    ping_lim = Window(180, 3600)
    nearby_lim = Window(240, 3600)
    nudge_lim = Window(nudge_hourly, 3600)

    def blocked_ids(db: Session, uid: str) -> set[str]:
        rows = db.query(MapBlock).filter(or_(MapBlock.user_id == uid, MapBlock.blocked_id == uid)).all()
        return {b.blocked_id if b.user_id == uid else b.user_id for b in rows}

    def fresh(u: User, now: datetime) -> bool:
        return bool(u.visible_map) and u.vis_at is not None and u.vis_lat is not None and (now - _utc(u.vis_at)) <= timedelta(minutes=visible_min)

    def purge(db: Session) -> None:
        cut = datetime.now(timezone.utc) - timedelta(hours=nudge_keep_hours)
        db.query(Nudge).filter(Nudge.created_at < cut).delete(synchronize_session=False)
        stale = datetime.now(timezone.utc) - timedelta(minutes=visible_min * 3)
        db.query(User).filter(User.vis_at != None, User.vis_at < stale).update({User.vis_lat: None, User.vis_lon: None}, synchronize_session=False)  # noqa: E711
        db.commit()

    @r.get("/v1/map/visibility")
    def get_vis(user: User = Depends(current_user)):
        return {"on": bool(user.visible_map)}

    @r.post("/v1/map/visibility")
    def set_vis(body: OnIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        user.visible_map = bool(body.on)
        if not body.on:
            user.vis_lat = user.vis_lon = user.vis_at = None
        events.log(db, "map.visible" if body.on else "map.hidden", user.id)
        db.commit()
        return {"on": bool(user.visible_map)}

    @r.post("/v1/map/ping")
    def ping(body: PingIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not user.visible_map:
            raise HTTPException(403, "Haritada görünme kapalı")
        if not ping_lim.allow(user.id):
            raise HTTPException(429, "Çok sık konum bildirimi")
        user.vis_lat = round(body.lat, 3)  # ≈110 m: tam konum saklanmaz
        user.vis_lon = round(body.lon, 3)
        user.vis_at = datetime.now(timezone.utc)
        db.commit()
        return {"ok": True}

    @r.get("/v1/map/nearby")
    def nearby(lat: float, lon: float, radius: int = 3000, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not user.visible_map:
            raise HTTPException(403, "Yakındakileri görmek için sen de haritada görünür olmalısın")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(400, "Konum geçersiz")
        if not nearby_lim.allow(user.id):
            raise HTTPException(429, "Çok sık sorgu")
        radius = max(200, min(int(radius), radius_max_m))
        now = datetime.now(timezone.utc)
        dlat = radius / 111000.0
        dlon = radius / (111000.0 * max(0.2, math.cos(math.radians(lat))))
        rows = (
            db.query(User)
            .filter(User.visible_map == True, User.id != user.id, or_(User.banned == False, User.banned.is_(None)))  # noqa: E712
            .filter(User.vis_lat.between(lat - dlat, lat + dlat), User.vis_lon.between(lon - dlon, lon + dlon))
            .limit(300)
            .all()
        )
        blocked = blocked_ids(db, user.id)
        out = []
        for u in rows:
            if u.id in blocked or not fresh(u, now):
                continue
            d = _dist_m(lat, lon, u.vis_lat, u.vis_lon)
            if d > radius:
                continue
            out.append({"id": u.id, "name": short_name(u.name), "role": u.active_role, "icon": u.map_icon or "", "lat": u.vis_lat, "lon": u.vis_lon, "mins": int((now - _utc(u.vis_at)).total_seconds() // 60), "d": int(d)})
        out.sort(key=lambda x: x["d"])
        return {"users": out[:40]}

    @r.post("/v1/map/nudge")
    def send_nudge(body: NudgeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if body.kind not in KINDS:
            raise HTTPException(400, "Geçersiz selam türü")
        if body.to == user.id:
            raise HTTPException(400, "Kendine gönderemezsin")
        now = datetime.now(timezone.utc)
        if not fresh(user, now):
            raise HTTPException(403, "Selam göndermek için haritada görünür olmalısın")
        to = db.get(User, body.to)
        generic = "Bu kişiye gönderilemedi"
        if to is None or to.banned or not fresh(to, now) or to.id in blocked_ids(db, user.id):
            raise HTTPException(403, generic)
        if _dist_m(user.vis_lat, user.vis_lon, to.vis_lat, to.vis_lon) > radius_max_m:
            raise HTTPException(403, generic)
        since = now - timedelta(minutes=nudge_pair_min)
        if db.query(Nudge).filter(Nudge.from_id == user.id, Nudge.to_id == to.id, Nudge.created_at > since).first():
            raise HTTPException(429, f"Aynı kişiye {nudge_pair_min} dakikada bir gönderebilirsin")
        if not nudge_lim.allow(user.id):
            raise HTTPException(429, "Saatlik selam sınırına ulaştın")
        n = Nudge(from_id=user.id, to_id=to.id, kind=body.kind)
        db.add(n)
        events.log(db, "map.nudge", user.id, target=to.id, tur=body.kind)
        db.commit()
        return {"id": n.id}

    @r.get("/v1/map/nudges")
    def my_nudges(user: User = Depends(current_user), db: Session = Depends(get_db)):
        purge(db)
        blocked = blocked_ids(db, user.id)
        rows = db.query(Nudge).filter(Nudge.to_id == user.id).order_by(Nudge.created_at.desc()).limit(50).all()
        out = []
        for n in rows:
            if n.from_id in blocked:
                continue
            f = db.get(User, n.from_id)
            if f is None:
                continue
            out.append({"id": n.id, "kind": n.kind, "fromId": f.id, "fromName": short_name(f.name), "fromIcon": f.map_icon or "", "at": int(_utc(n.created_at).timestamp() * 1000)})
        return {"nudges": out}

    @r.post("/v1/map/nudges/ack")
    def ack(body: IdsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        n = db.query(Nudge).filter(Nudge.to_id == user.id, Nudge.id.in_(body.ids)).delete(synchronize_session=False) if body.ids else 0
        db.commit()
        return {"deleted": n}

    @r.post("/v1/map/block")
    def block(body: IdIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if body.id == user.id or db.get(User, body.id) is None:
            raise HTTPException(400, "Geçersiz kişi")
        if not db.query(MapBlock).filter(MapBlock.user_id == user.id, MapBlock.blocked_id == body.id).first():
            db.add(MapBlock(user_id=user.id, blocked_id=body.id))
            events.log(db, "map.block", user.id, target=body.id)
        db.query(Nudge).filter(Nudge.from_id == body.id, Nudge.to_id == user.id).delete(synchronize_session=False)
        db.commit()
        return {"ok": True}

    @r.post("/v1/map/unblock")
    def unblock(body: IdIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        db.query(MapBlock).filter(MapBlock.user_id == user.id, MapBlock.blocked_id == body.id).delete(synchronize_session=False)
        db.commit()
        return {"ok": True}

    @r.get("/v1/map/blocks")
    def blocks(user: User = Depends(current_user), db: Session = Depends(get_db)):
        rows = db.query(MapBlock).filter(MapBlock.user_id == user.id).all()
        out = []
        for b in rows:
            u = db.get(User, b.blocked_id)
            out.append({"id": b.blocked_id, "name": short_name(u.name) if u else "ROTA kullanıcısı"})
        return {"blocks": out}

    return r
