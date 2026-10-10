import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
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
    aydinlatma_ms = Column(BigInteger, default=0)  # istemci saati (bilgi amaçlı, epoch ms)
    acik_riza_ms = Column(BigInteger, default=0)  # istemci saati (bilgi amaçlı, epoch ms)
    created_at = Column(DateTime(timezone=True), default=now, index=True)  # SUNUCU saati: hukuken geçerli zaman
