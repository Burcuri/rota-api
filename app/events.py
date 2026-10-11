"""Etkinlik akışı: yönetimin "bütün aktiviteler" ekranı için kısa kayıtlar.

Kural: içerik YAZILMAZ. Mesaj metni, adres listesi, alıcı adı / telefonu burada asla bulunmaz; yalnız olayın türü, hesap kimliği ve
kısa bilgi (ör. "3 kişiye gönderdi", "2 adres"). Satırlar EVENT_KEEP_DAYS (varsayılan 60) gün sonra ve EVENT_MAX_ROWS aşılınca silinir.
"""
import json
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Event, now

# Yönetim ekranındaki süzgeç ve Türkçe karşılıklar (ekran bu listeyi sunucudan alır)
KINDS = {
    "user.register": "Hesap açtı",
    "user.login": "Giriş yaptı",
    "user.role": "Hesap türünü değiştirdi",
    "user.profile": "Profilini güncelledi",
    "user.password": "Şifresini değiştirdi",
    "user.banned": "Hesabı askıya alındı",
    "user.unbanned": "Askısı kaldırıldı",
    "user.reset": "Şifresi sıfırlandı",
    "user.edited": "Bilgileri yönetimce düzenlendi",
    "job.create": "İlan yayınladı",
    "job.close": "İlanı kaldırdı",
    "relay.send": "Mesaj gönderdi",
    "vehicle.share": "Araç profilini işletmelere açtı",
    "vehicle.unshare": "Araç profilini işletmelere kapattı",
    "handoff.create": "Teslim bağlantısı üretti",
    "handoff.arrived": "Hedefe ulaştı",
    "handoff.received": "Alıcı 'Paketimi aldım' dedi",
    "handoff.pin": "Alıcı kapı noktası işaretledi",
    "handoff.closed": "Teslimi bitirdi",
    "door.report": "Kapı noktası bildirdi",
    "review.create": "Değerlendirme yazıldı",
    "review.hidden": "Değerlendirme gizlendi",
    "follow.ask": "Takip isteği gönderdi",
    "follow.start": "Takibi kabul etti",
    "follow.end": "Takip bitti",
}


def log(db: Session, kind: str, user_id: str | None = None, target: str = "", **meta) -> None:
    """Etkinlik satırı ekler (commit ETMEZ: çağıranın işlemiyle birlikte yazılır). Hiçbir zaman ana işlemi bozmaz."""
    try:
        clean = {k: v for k, v in meta.items() if v not in (None, "")}
        m = json.dumps(clean, ensure_ascii=False, separators=(",", ":")) if clean else ""
        if len(m) > 500:
            m = '{"kisaltildi":1}'
        db.add(Event(kind=kind[:24], user_id=user_id, target_id=(target or "")[:40], meta=m))
    except Exception:
        pass


def purge(db: Session, keep_days: float = 60, max_rows: int = 50000) -> int:
    cut = now() - timedelta(days=keep_days)
    n = db.query(Event).filter(Event.created_at < cut).delete(synchronize_session=False)
    total = db.query(func.count(Event.id)).scalar() or 0
    if total > max_rows:
        ids = [i for (i,) in db.query(Event.id).order_by(Event.created_at.asc()).limit(total - max_rows).all()]
        if ids:
            n += db.query(Event).filter(Event.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return n
