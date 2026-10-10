import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from .db import Base


def uid() -> str:
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(String(32), primary_key=True, default=uid)
    phone = Column(String(16), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(120), nullable=False, default="")
    active_role = Column(String(16), nullable=False, default="courier")  # courier | employer | sponsor
    # Giriş için telefon yerine kullanılabilir. Küçük harfle saklanır, benzersizdir, boş olabilir (NULL).
    username = Column(String(32), unique=True, index=True)
    # İsteğe bağlı. Henüz DOĞRULANMIYOR (benzersiz de değil): doğrulama gelince doğrulanmış e-posta benzersiz olacak.
    email = Column(String(254), index=True)
    lat = Column(Float)
    lon = Column(Float)
    created_at = Column(DateTime(timezone=True), default=now)

    jobs_posted = relationship("Job", back_populates="employer", foreign_keys="Job.employer_id")


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String(32), primary_key=True, default=uid)
    employer_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    courier_id = Column(String(32), ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(160), nullable=False, default="Teslimat işi")
    note = Column(Text, default="")
    employer_lat = Column(Float, nullable=False)
    employer_lon = Column(Float, nullable=False)
    # open -> applied -> accepted -> departed -> done | cancelled
    status = Column(String(20), nullable=False, default="open", index=True)
    # Yeni akışta adresler sunucuya YAZILMAZ (işletmenin telefonunda kalır, mesajla gönderilir): ilanda yalnız sayı ve bölge
    package_count = Column(Integer, default=0)
    area = Column(String(160), default="")
    fee = Column(Float)  # işletmenin yazdığı teslimat ücreti (₺) — bilgi amaçlı; Rota ödeme almaz/aktarmaz
    applicant_id = Column(String(32), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now)
    accepted_at = Column(DateTime(timezone=True))
    departed_at = Column(DateTime(timezone=True))

    employer = relationship("User", foreign_keys=[employer_id], back_populates="jobs_posted")
    packages = relationship("JobPackage", back_populates="job", cascade="all, delete-orphan")


class JobPackage(Base):
    __tablename__ = "job_packages"
    id = Column(String(32), primary_key=True, default=uid)
    job_id = Column(String(32), ForeignKey("jobs.id"), nullable=False, index=True)
    customer = Column(String(160), nullable=False)
    phone = Column(String(16), nullable=False, index=True)
    address = Column(Text, nullable=False)
    neighborhood = Column(String(120), default="")
    street = Column(String(160), default="")
    door_number = Column(String(40), default="")
    apartment = Column(String(40), default="")
    floor = Column(String(40), default="")
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    precision = Column(String(40), default="")
    token = Column(String(32), unique=True, nullable=False, default=uid, index=True)
    pin_updated = Column(Boolean, default=False)
    pin_updated_at = Column(DateTime(timezone=True))
    status = Column(String(20), default="pending")
    sort_m = Column(Float, default=0)

    job = relationship("Job", back_populates="packages")


class ConsentLog(Base):
    """Açık rıza kaydı: kim, hangi metin sürümüne, hangi seçeneklerle, NE ZAMAN (sunucu saati) rıza verdi / geri çekti.
    Satırlar yalnız eklenir, hiç güncellenmez; son satır geçerli durumdur. Geri çekme = hepsi False olan yeni satır."""

    __tablename__ = "consent_log"
    id = Column(String(32), primary_key=True, default=uid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=0)  # aydınlatma / açık rıza metin sürümü
    voice = Column(Boolean, nullable=False, default=False)  # sesle yazma (ses yurt dışı servise gider)
    ocr = Column(Boolean, nullable=False, default=False)  # adres okuma (görüntü OCR servisine gider)
    grup_konum = Column(Boolean, nullable=False, default=False)  # konumumu grup arkadaşlarım görsün
    # Ortak kapı noktası katmanına katkı (teslim ettiğim kapının sokak/no/konumu tüm kullanıcılara görünür). Eski satırlarda NULL = kapalı
    kapi = Column(Boolean, default=False)
    aydinlatma_ms = Column(BigInteger, default=0)  # istemci saati (bilgi amaçlı, epoch ms)
    acik_riza_ms = Column(BigInteger, default=0)  # istemci saati (bilgi amaçlı, epoch ms)
    created_at = Column(DateTime(timezone=True), default=now, index=True)  # SUNUCU saati: hukuken geçerli zaman
    # Kullanım koşulları (sözleşme) onayı: hangi sürüm, istemci saati. 0 = henüz onaylanmadı / eski istemci
    sozlesme_version = Column(Integer, default=0)
    sozlesme_ms = Column(BigInteger, default=0)


class Device(Base):
    """Bir hesabın cihazları (telefon, web ikizi). Kimliği istemci üretir ve saklar. Mesaj aktarıcısı her mesajı
    alıcının (ve gönderenin öbür cihazlarının) her cihazına bir kez ulaştırır."""

    __tablename__ = "devices"
    id = Column(String(64), primary_key=True)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    platform = Column(String(16), default="")  # android | ios | web
    created_at = Column(DateTime(timezone=True), default=now)
    last_seen = Column(DateTime(timezone=True), default=now, index=True)


class RelayMsg(Base):
    """Geçici mesaj aktarıcısı: mesaj içeriği (adres listesi dahil) yalnız ulaştırılana kadar burada durur.
    Tüm hedef cihazlara ulaşınca silinir; ulaşamayan en geç RELAY_TTL_DAYS (varsayılan 7) gün sonra silinir."""

    __tablename__ = "relay_msgs"
    id = Column(String(32), primary_key=True, default=uid)
    sender_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)  # istemcinin JSON zarfı; sunucu içine bakmaz
    size = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=now)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class RelayDelivery(Base):
    """Bir mesajın bir cihaza (ya da henüz cihazı olmayan kullanıcıya: device_id NULL) ulaştırılma kaydı.
    Cihaz aldığını bildirince satır silinir; mesajın satırı kalmayınca mesaj da silinir."""

    __tablename__ = "relay_delivery"
    id = Column(String(32), primary_key=True, default=uid)
    msg_id = Column(String(32), ForeignKey("relay_msgs.id"), nullable=False, index=True)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String(64), index=True)  # NULL: kullanıcının ilk açılan cihazı alır


class DoorPoint(Base):
    """Ortak kapı noktası katmanı: OpenStreetMap'te olmayan kapı numaralarını kuryelerin teslimatlarıyla tamamlar.
    Burada KİŞİ BİLGİSİ YOKTUR: yalnız il/ilçe/mahalle/sokak/kapı no + koordinat + güven puanı. Kim bildirdi bilgisi
    ayrı tabloda (door_reports) ve yalnız geri çevrilemez kısa bir koddur; bu tablodan okunamaz.
    Aynı anahtar (ilçe+mahalle+sokak+kapı) için birbirinden uzak birden çok aday satır olabilir; en yüksek puanlı gösterilir."""

    __tablename__ = "door_points"
    id = Column(String(32), primary_key=True, default=uid)
    # Eşleştirme anahtarları (küçük harf, Türkçe karakter düz Latin, ek sözcükler atılmış)
    province_k = Column(String(60), nullable=False, default="")
    district_k = Column(String(80), nullable=False, default="")
    neigh_k = Column(String(100), nullable=False, default="")
    street_k = Column(String(120), nullable=False, default="")
    street_kind = Column(String(10), nullable=False, default="")  # cadde | sokak | bulvar | meydan | ""
    door_k = Column(String(8), nullable=False)  # ör. 12 ya da 12A (daire/kat atılmış)
    door_n = Column(Integer, nullable=False)  # sayısal kısım
    # Görünen adlar (ilk bildirimden)
    province = Column(String(60), default="")
    district = Column(String(80), default="")
    neighborhood = Column(String(100), default="")
    street = Column(String(120), default="")
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    score = Column(Float, nullable=False, default=0)  # onaylayan oyların ağırlık toplamı
    wrong = Column(Float, nullable=False, default=0)  # "yanlış" oylarının ağırlık toplamı
    confirms = Column(Integer, nullable=False, default=0)  # onaylayan farklı kişi sayısı
    hidden = Column(Boolean, nullable=False, default=False)  # yanlış oyları doğrulardan çoksa gösterilmez
    created_at = Column(DateTime(timezone=True), default=now)
    updated_at = Column(DateTime(timezone=True), default=now, index=True)

    __table_args__ = (
        Index("ix_door_points_door", "district_k", "neigh_k", "door_k"),
        Index("ix_door_points_street", "district_k", "neigh_k", "street_k"),
    )


class DoorReport(Base):
    """Bir kişinin bir kapı noktası için tek oyu (onay ya da yanlış). Kişi KENDİ KİMLİĞİYLE tutulmaz: reporter, hesap
    kimliğinden türetilmiş geri çevrilemez kısa koddur; yalnız aynı kişinin aynı noktayı birden çok kez saymasını önler."""

    __tablename__ = "door_reports"
    id = Column(String(32), primary_key=True, default=uid)
    point_id = Column(String(32), ForeignKey("door_points.id"), nullable=False, index=True)
    reporter = Column(String(32), nullable=False, index=True)
    vote = Column(String(8), nullable=False, default="confirm")  # confirm | wrong
    source = Column(String(12), nullable=False, default="checkin")  # checkin | pin | recipient
    weight = Column(Float, nullable=False, default=1.0)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, index=True)

    __table_args__ = (UniqueConstraint("point_id", "reporter", name="uq_door_report_point_reporter"),)
