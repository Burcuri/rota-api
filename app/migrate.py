"""Hafif şema güncelleyici.

`Base.metadata.create_all` yeni TABLOLARI açar ama var olan tabloya yeni SÜTUN eklemez. Render'daki veritabanında `users`
tablosu zaten var; bu yüzden yeni sütunlar burada elle eklenir. Her açılışta güvenle tekrar çalışır (varsa dokunmaz).
PostgreSQL ve SQLite'ta aynı SQL çalışır. Veri silinmez, yalnız eklenir.
"""
from sqlalchemy import inspect, text

from .db import engine

# (tablo, sütun, tip) — yeni sütun eklerken buraya satır ekle, models.py'ye de aynısını yaz
ADD_COLUMNS = [
    ("users", "username", "VARCHAR(32)"),
    ("users", "email", "VARCHAR(254)"),
]

# create_all'un yeni kurulumda açtığı dizin adlarıyla aynı adlar (ix_<tablo>_<sütun>)
INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)",
    "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)",
]


def ensure_schema() -> list[str]:
    """Eksik sütunları ve dizinleri ekler; yapılan işlerin listesini döner (günlük için)."""
    done: list[str] = []
    insp = inspect(engine)
    for table, col, ddl in ADD_COLUMNS:
        if not insp.has_table(table):
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        if col in have:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        except Exception:
            # İki süreç aynı anda açılırsa diğeri sütunu bizden önce eklemiş olabilir: artık varsa sorun yok, yoksa gerçek hata
            if col not in {c["name"] for c in inspect(engine).get_columns(table)}:
                raise
            continue
        done.append(f"{table}.{col} eklendi")
    with engine.begin() as conn:
        for ddl in INDEXES:
            conn.execute(text(ddl))
    return done
