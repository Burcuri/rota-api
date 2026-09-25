import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

raw = os.getenv("DATABASE_URL", "sqlite:///./rota.db")
if raw.startswith("postgres://"):
    raw = raw.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if raw.startswith("sqlite") else {}
engine = create_engine(raw, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
