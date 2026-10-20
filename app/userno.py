"""Kullanıcı no: her hesaba verilen, benzersiz, DEĞİŞMEYEN 7 haneli numara (örn. 8472019; ilk hane 0 olmaz).

Uygulamada rolün önekiyle yazılır: Kurye KRY-8472019, İşveren ISV-8472019, Sponsor SPN-8472019 (rol değişse numara aynı kalır).
Sistemin (Rota Asistan) genel kimliği ayrıdır: SYS-0000000 — gerçek hesaplara hiç verilmez (ilk hane 0).
Hediye, hak ediş, ürün teslimi ve Rota Coin kayıtlarında kişi adı yerine bu numara esas alınır.
"""
import re
import secrets

SYSTEM_NO = "0000000"
_RE = re.compile(r"[1-9][0-9]{6}")


def is_public_no(s) -> bool:
    return isinstance(s, str) and _RE.fullmatch(s) is not None  # fullmatch: sondaki satır sonu ya da Unicode rakamlar geçmesin


def new_no() -> str:
    """1000000–9999999 arası rastgele numara (tahmin edilemesin diye sıralı değil)."""
    return str(secrets.randbelow(9_000_000) + 1_000_000)


def unique_no(taken: set[str]) -> str:
    """`taken` içinde olmayan bir numara üretir (toplu doldurma için)."""
    for _ in range(200):
        n = new_no()
        if n not in taken:
            return n
    raise RuntimeError("Boş kullanıcı no bulunamadı")


def pick_no(db) -> str:
    """Veritabanında olmayan bir numara seçer (çok nadir yarış durumunu kaydetme sırasındaki benzersizlik dizini yakalar)."""
    from .models import User

    for _ in range(50):
        n = new_no()
        if db.query(User.id).filter(User.public_no == n).first() is None:
            return n
    raise RuntimeError("Boş kullanıcı no bulunamadı")


def ensure_public_no(db, u) -> bool:
    """Numarası olmayan (eski / elle eklenmiş) kullanıcıya numara verir ve kaydeder. Yeni numara verildiyse True."""
    if is_public_no(u.public_no):
        return False
    from sqlalchemy.exc import IntegrityError

    for _ in range(5):
        u.public_no = pick_no(db)
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
    return False
