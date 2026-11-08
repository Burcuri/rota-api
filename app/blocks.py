"""ENGELLEME — her yerde geçerli (1.28.0; kullanıcı isteği 9 Kasım 2026: "Sohbete Şikâyet et + Engelle … bunu bitir"; App Store 1.2 ve
Google Play UGC şartı).

Tek liste: `map_blocks` (Dilim 12'de haritadaki "🚫 Engelle" için açılmıştı; artık genel engel listesi). A, B'yi engelleyince İKİ YÖNDE:
 - birbirinize mesaj ulaşmaz (birebir ve ortak grup; sunucu iletmez — relay.py),
 - haritada birbirinizi görmezsiniz, selam gönderilemez (nearby.py),
 - canlı takip isteği ve adres listesi (görev) gönderilemez; aradaki açık canlı takip ve görevler kapanır (konum paylaşımı hemen biter).
Engellenen kişiye "engellendin" denmez: mesajı gitmiş gibi görünür, kapanan takip / görev "başka bir nedenle kapandı" der.
"""
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .models import MapBlock


def blocked_with(db: Session, uid: str) -> set[str]:
    """Bu kişinin engellediği ya da onu engelleyen herkes"""
    rows = db.query(MapBlock.user_id, MapBlock.blocked_id).filter(or_(MapBlock.user_id == uid, MapBlock.blocked_id == uid)).all()
    return {b if a == uid else a for a, b in rows}


def i_blocked(db: Session, uid: str) -> set[str]:
    """Yalnız bu kişinin engelledikleri"""
    return {b for (b,) in db.query(MapBlock.blocked_id).filter(MapBlock.user_id == uid).all()}


def pair_blocked(db: Session, a: str, b: str) -> bool:
    """İkisinden biri öbürünü engellemiş mi"""
    q = db.query(MapBlock.id).filter(or_(and_(MapBlock.user_id == a, MapBlock.blocked_id == b), and_(MapBlock.user_id == b, MapBlock.blocked_id == a)))
    return q.first() is not None


def cut_for(db: Session, sender_id: str, ids: set[str]) -> set[str]:
    """Gönderenle arasında (iki yönde) engel olan alıcılar — bunlara mesaj iletilmez"""
    if not ids:
        return set()
    rows = db.query(MapBlock.user_id, MapBlock.blocked_id).filter(
        or_(and_(MapBlock.user_id == sender_id, MapBlock.blocked_id.in_(ids)), and_(MapBlock.blocked_id == sender_id, MapBlock.user_id.in_(ids)))
    ).all()
    return {b if a == sender_id else a for a, b in rows}


def add_block(db: Session, user_id: str, blocked_id: str) -> bool:
    """Engeli yazar (zaten varsa dokunmaz) ve aradaki açık işleri kapatır: ondan gelen selamlar silinir, canlı takip ve görevler biter.
    commit ETMEZ. Yeni engelse True."""
    from .follow import end_follow  # döngüsel içe almayı önlemek için burada
    from .models import Follow, Nudge
    from .tasks import end_tasks_for_pair

    new = False
    if not db.query(MapBlock.id).filter(MapBlock.user_id == user_id, MapBlock.blocked_id == blocked_id).first():
        db.add(MapBlock(user_id=user_id, blocked_id=blocked_id))
        new = True
    db.query(Nudge).filter(Nudge.from_id == blocked_id, Nudge.to_id == user_id).delete(synchronize_session=False)
    pair = or_(and_(Follow.employer_id == user_id, Follow.courier_id == blocked_id), and_(Follow.employer_id == blocked_id, Follow.courier_id == user_id))
    for f in db.query(Follow).filter(pair, Follow.status.in_(("asked", "active"))).all():
        end_follow(db, f, "blocked", user_id)
    end_tasks_for_pair(db, user_id, blocked_id, "blocked", user_id)
    end_tasks_for_pair(db, blocked_id, user_id, "blocked", user_id)
    db.flush()
    return new
