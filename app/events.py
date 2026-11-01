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
    "handoff.locreq": "Alıcıya konum talebi gönderildi",
    "handoff.received": "Alıcı 'Paketimi aldım' dedi",
    "reward.create": "Ödül koydu",
    "reward.remove": "Ödülü kaldırdı",
    "reward.claim": "Ödül aldı (puan bloke)",
    "reward.ship": "Ödülü kargoladı",
    "reward.received": "Ödülü teslim aldı",
    "reward.dispute": "Ödül gelmedi dedi",
    "reward.cancel": "Ödül talebi iptal",
    "handoff.pin": "Alıcı kapı noktası işaretledi",
    "handoff.closed": "Teslimi bitirdi",
    "handoff.depart": "📦 Yola çıktı dedi",
    "handoff.join": "İşverenin teslim bağlantısına bağlandı",
    "handoff.done": "Teslim edildi olarak işaretledi",
    "handoff.push": "Alıcı tarayıcı bildirimini açtı",
    "push.on": "Bu cihaza bildirimi açtı",
    "door.report": "Kapı noktası bildirdi",
    "review.create": "Değerlendirme yazıldı",
    "review.hidden": "Değerlendirme gizlendi",
    "follow.ask": "Takip isteği gönderdi",
    "map.visible": "Haritada görünmeyi açtı",
    "map.hidden": "Haritada görünmeyi kapattı",
    "map.nudge": "Haritada selam gönderdi",
    "map.block": "Birini engelledi",
    "follow.start": "Takibi kabul etti",
    "follow.end": "Takip bitti",
    "coin.buy": "Coin aldı (test)",
    "ad.create": "Harita reklamı verdi",
    "ad.remove": "Reklamını kaldırdı",
    "job.promote": "İlanını öne çıkardı",
    "guide.backup": "Rehberini 24 saat yedekledi",
    "guide.restore": "Rehber yedeğini geri aldı",
    # 1.20.0: hesap güvenliği
    "user.locked": "Çok fazla yanlış şifre: giriş kilitlendi",
    "user.forgot": "Şifremi unuttum: kod istedi",
    "user.pwreset": "Şifresini kodla yeniledi",
    "user.email_ok": "E-postasını doğruladı",
    "user.phone_ok": "Telefonunu doğruladı",
    "user.phone_new": "Telefon numarasını değiştirdi (kodla)",
    "user.sessions_out": "Diğer cihazlardan çıkış yaptı",
    "user.deleted": "Hesabını sildi",
    "user.blacklist_inh": "Silinen kara liste hesabının numarasıyla açıldı",
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
