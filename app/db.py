import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

raw = os.getenv("DATABASE_URL", "sqlite:///./rota.db")
if raw.startswith("postgres://"):
    raw = raw.replace("postgres://", "postgresql://", 1)

if raw.startswith("sqlite"):
    engine = create_engine(raw, pool_pre_ping=True, connect_args={"check_same_thread": False})
else:
    # 1.21.1: veritabanı Supabase'de ("Session pooler" adresi; Render'ın ücretsiz veritabanının süresi 1 Kasım 2026'da doldu).
    # Ücretsiz planda havuzun bağlantı sınırı düşük (panel de bağlanır) → sunucu en çok 10 bağlantı tutar (DB_POOL_SIZE + DB_MAX_OVERFLOW
    # ile değişir). Aradaki sunucu boşta kalan bağlantıyı sessizce kapatabilir → bağlantılar 5 dakikada yenilenir, kullanmadan önce
    # yoklanır (pool_pre_ping), TCP canlı tutma açık.
    engine = create_engine(
        raw,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
        pool_recycle=300,
        connect_args={"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 5, "application_name": "rota-sunucu"},
    )
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
