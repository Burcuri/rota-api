"""Geçici mesaj aktarıcısı (WhatsApp Web mantığı).

- Mesajlar ve adres listeleri TELEFONDA saklanır. Sunucu yalnız ulaştırana kadar tutar.
- Bir mesaj; alıcının her cihazına ve gönderenin ÖBÜR cihazlarına (ör. web ikizi) bir kez ulaştırılır.
- Her cihaz aldığını bildirince (ack) o cihazın kaydı silinir; kayıt kalmayınca mesaj da silinir.
- Ulaşamayan mesaj en geç RELAY_TTL_DAYS gün sonra silinir (istek anında + arka plan iş parçacığında temizlik).
- Sunucu mesaj içeriğine bakmaz (istemcinin JSON zarfı); yalnız boyutu sınırlar.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import events
from .auth import current_user
from .db import SessionLocal, get_db
from .models import Device, RelayDelivery, RelayMsg, User


class DeviceIn(BaseModel):
    device_id: str = Field(alias="deviceId", min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    platform: str = Field(default="", max_length=16)

    model_config = {"populate_by_name": True}


class RelayIn(BaseModel):
    to: list[str] = Field(min_length=1, max_length=50)
    body: str = Field(min_length=1)
    device_id: str | None = Field(default=None, alias="deviceId", max_length=64)

    model_config = {"populate_by_name": True}


class AckIn(BaseModel):
    device_id: str = Field(alias="deviceId", min_length=8, max_length=64)
    ids: list[str] = Field(default_factory=list, max_length=500)

    model_config = {"populate_by_name": True}



def _utc(dt: datetime | None) -> datetime | None:
    """SQLite saat dilimini düşürür; karşılaştırmalar için her zamanı UTC'ye çevirir."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def make_router(ttl_days: float, max_bytes: int, user_max_bytes: int, group_max: int) -> APIRouter:
    r = APIRouter()

    def purge(db: Session) -> int:
        """Süresi dolan mesajları ve kaydı kalmayan mesajları siler. Silinen mesaj sayısını döner."""
        now = datetime.now(timezone.utc)
        old = [m.id for m in db.query(RelayMsg.id).filter(RelayMsg.expires_at < now).limit(2000).all()]
        if old:
            db.query(RelayDelivery).filter(RelayDelivery.msg_id.in_(old)).delete(synchronize_session=False)
            db.query(RelayMsg).filter(RelayMsg.id.in_(old)).delete(synchronize_session=False)
        # Bütün cihazlara ulaşmış (kaydı kalmamış) mesajlar
        orphan = (
            db.query(RelayMsg.id)
            .outerjoin(RelayDelivery, RelayDelivery.msg_id == RelayMsg.id)
            .filter(RelayDelivery.id.is_(None))
            .limit(2000)
            .all()
        )
        ids = [m.id for m in orphan]
        if ids:
            db.query(RelayMsg).filter(RelayMsg.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        return len(old) + len(ids)

    last_purge = [0.0]

    def maybe_purge(db: Session):
        # Render ücretsizde zamanlanmış iş yok: istek geldikçe en çok dakikada bir temizlik
        if time.time() - last_purge[0] > 60:
            last_purge[0] = time.time()
            purge(db)

    def release(db: Session, user_id: str, device_id: str):
        """Cihaz hesaptan ayrılıyor. Bekleyen mesajlar kaybolmasın: hesabın başka cihazı yoksa ilk açılacak cihazına bırakılır
        (device_id NULL); başka cihazı varsa onlar zaten kendi kopyasını alır."""
        q = db.query(RelayDelivery).filter(RelayDelivery.user_id == user_id, RelayDelivery.device_id == device_id)
        others = db.query(Device.id).filter(Device.user_id == user_id, Device.id != device_id).count()
        if others:
            q.delete(synchronize_session=False)
        else:
            for row in q.all():
                dup = db.query(RelayDelivery).filter(RelayDelivery.msg_id == row.msg_id, RelayDelivery.user_id == user_id, RelayDelivery.device_id.is_(None)).first()
                if dup:
                    db.delete(row)
                else:
                    row.device_id = None
        db.flush()

    def device_of(db: Session, user: User, device_id: str, platform: str = "") -> Device:
        d = db.get(Device, device_id)
        if d is None:
            d = Device(id=device_id, user_id=user.id, platform=platform[:16])
            db.add(d)
        elif d.user_id != user.id:
            # Aynı cihazda başka hesaba girildi: önceki hesabın bu cihazda bekleyenleri yeni hesaba GÖSTERİLMEZ
            release(db, d.user_id, device_id)
            d.user_id = user.id
            d.created_at = datetime.now(timezone.utc)
        if platform:
            d.platform = platform[:16]
        d.last_seen = datetime.now(timezone.utc)
        db.flush()
        return d

    @r.post("/v1/devices")
    def register_device(b: DeviceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        device_of(db, user, b.device_id, b.platform)
        db.commit()
        return {"deviceId": b.device_id}

    @r.post("/v1/devices/forget")
    def forget_device(b: DeviceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Çıkış yaparken: bu cihaz artık bu hesabın mesajlarını almaz."""
        d = db.get(Device, b.device_id)
        if d is not None and d.user_id == user.id:
            release(db, user.id, d.id)
            db.delete(d)
            db.commit()
            purge(db)
        return {"ok": True}

    @r.post("/v1/relay")
    def send(b: RelayIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        maybe_purge(db)
        size = len(b.body.encode("utf-8"))
        if size > max_bytes:
            raise HTTPException(413, f"Mesaj çok büyük (en çok {max_bytes // 1024} KB)")
        to = []
        for t in b.to:
            if t and t != user.id and t not in to:
                to.append(t)
        if not to:
            raise HTTPException(400, "Alıcı yok")
        if len(to) > group_max - 1:
            raise HTTPException(400, f"Bir mesaj en çok {group_max - 1} kişiye gider (grup üst sınırı {group_max})")
        users = db.query(User).filter(User.id.in_(to)).all()
        found = {u.id for u in users}
        if not found:
            raise HTTPException(404, "Alıcı bulunamadı")
        # Alıcının bekleyen mesajlarının toplam boyutu sınırı (kötüye kullanım ve dolmuş kutu)
        for uid_ in found:
            pending = (
                db.query(func.coalesce(func.sum(RelayMsg.size), 0))
                .join(RelayDelivery, RelayDelivery.msg_id == RelayMsg.id)
                .filter(RelayDelivery.user_id == uid_)
                .scalar()
            ) or 0
            if pending + size > user_max_bytes:
                raise HTTPException(413, "Alıcının bekleyen mesaj kutusu dolu; bir süre sonra yeniden dene")
        now = datetime.now(timezone.utc)
        msg = RelayMsg(sender_id=user.id, body=b.body, size=size, created_at=now, expires_at=now + timedelta(days=ttl_days))
        db.add(msg)
        db.flush()
        targets = 0
        for uid_ in found:
            devs = db.query(Device.id).filter(Device.user_id == uid_).all()
            if devs:
                for (dev,) in devs:
                    db.add(RelayDelivery(msg_id=msg.id, user_id=uid_, device_id=dev))
                    targets += 1
            else:
                db.add(RelayDelivery(msg_id=msg.id, user_id=uid_, device_id=None))
                targets += 1
        # Gönderenin öbür cihazları (web ikizi) da kendi gönderdiğini görsün
        if b.device_id:
            device_of(db, user, b.device_id)
        for (dev,) in db.query(Device.id).filter(Device.user_id == user.id, Device.id != (b.device_id or "")).all():
            db.add(RelayDelivery(msg_id=msg.id, user_id=user.id, device_id=dev))
        events.log(db, "relay.send", user.id, n=len(found))  # yalnız kaç kişiye gittiği; içerik ve alıcılar yazılmaz
        db.commit()
        return {"id": msg.id, "at": now.isoformat(), "recipients": sorted(found), "missing": [t for t in to if t not in found]}

    @r.get("/v1/relay")
    def fetch(deviceId: str, limit: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not (8 <= len(deviceId) <= 64):
            raise HTTPException(400, "Cihaz kimliği geçersiz")
        maybe_purge(db)
        dev = device_of(db, user, deviceId)
        # Gönderildiğinde hiç cihazı olmayan kullanıcıya bırakılan kayıtlar: şimdi bütün cihazlarına dağıtılır
        nulls = db.query(RelayDelivery).filter(RelayDelivery.user_id == user.id, RelayDelivery.device_id.is_(None)).all()
        if nulls:
            devs = [d for (d,) in db.query(Device.id).filter(Device.user_id == user.id).all()]
            for n in nulls:
                for d in devs:
                    db.add(RelayDelivery(msg_id=n.msg_id, user_id=user.id, device_id=d))
                db.delete(n)
        db.commit()
        now = datetime.now(timezone.utc)
        rows = (
            db.query(RelayMsg, User)
            .join(RelayDelivery, RelayDelivery.msg_id == RelayMsg.id)
            .join(User, User.id == RelayMsg.sender_id)
            .filter(RelayDelivery.user_id == user.id, RelayDelivery.device_id == dev.id)
            .order_by(RelayMsg.created_at.asc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
        out = []
        for m, s in rows:
            if _utc(m.expires_at) < now:
                continue
            out.append({
                "id": m.id,
                "senderId": s.id,
                "senderName": s.name,
                "senderRole": s.active_role,
                "mine": s.id == user.id,
                "body": m.body,
                "at": _utc(m.created_at).isoformat(),
            })
        return out

    @r.post("/v1/relay/ack")
    def ack(b: AckIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Cihaz mesajları kendi hafızasına yazdı: bu cihazın kayıtları silinir; kimsenin beklemediği mesaj da silinir."""
        if not b.ids:
            return {"deleted": 0}
        db.query(RelayDelivery).filter(
            RelayDelivery.user_id == user.id, RelayDelivery.device_id == b.device_id, RelayDelivery.msg_id.in_(b.ids)
        ).delete(synchronize_session=False)
        db.commit()
        left = {m for (m,) in db.query(RelayDelivery.msg_id).filter(RelayDelivery.msg_id.in_(b.ids)).distinct().all()}
        gone = [i for i in b.ids if i not in left]
        n = 0
        if gone:
            n = db.query(RelayMsg).filter(RelayMsg.id.in_(gone)).delete(synchronize_session=False)
            db.commit()
        return {"deleted": n}

    def loop():
        while True:
            time.sleep(600)
            try:
                db = SessionLocal()
                try:
                    purge(db)
                finally:
                    db.close()
            except Exception as e:  # temizlik hatası sunucuyu düşürmesin
                print("[aktarıcı] temizlik hatası:", e, flush=True)

    r.purge = purge  # testler için
    r.start_cleaner = lambda: threading.Thread(target=loop, name="relay-purge", daemon=True).start()
    return r
