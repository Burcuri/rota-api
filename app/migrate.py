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
    ("jobs", "package_count", "INTEGER"),
    ("jobs", "area", "VARCHAR(160)"),
    ("jobs", "fee", "FLOAT"),
    ("consent_log", "sozlesme_version", "INTEGER"),
    ("consent_log", "sozlesme_ms", "BIGINT"),
    ("consent_log", "kapi", "BOOLEAN"),
    ("door_points", "site", "VARCHAR(60)"),
    # Dilim 4: yönetim (men, oturum sürümü, zorunlu şifre değişikliği) ve teslim kaydının kapanışı
    ("users", "banned", "BOOLEAN DEFAULT FALSE"),
    ("users", "banned_reason", "VARCHAR(300)"),
    ("users", "banned_at", "TIMESTAMP WITH TIME ZONE"),
    ("users", "token_version", "INTEGER DEFAULT 0"),
    ("users", "must_change_password", "BOOLEAN DEFAULT FALSE"),
    ("users", "last_login_at", "TIMESTAMP WITH TIME ZONE"),
    ("handoffs", "closed_at", "TIMESTAMP WITH TIME ZONE"),
    # Dilim 5: kurye araç profili için ayrı açık rıza
    ("consent_log", "arac", "BOOLEAN"),
    # Dilim 6: takip izi (48 saat saklanan seyreltilmiş geçmiş noktalar)
    ("follows", "trail_at", "TIMESTAMP WITH TIME ZONE"),
    ("follows", "trail_n", "INTEGER DEFAULT 0"),
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
            fresh = inspect(engine)
            if not fresh.has_table(table) or col in {c["name"] for c in fresh.get_columns(table)}:
                continue
            raise
        done.append(f"{table}.{col} eklendi")
    with engine.begin() as conn:
        for ddl in INDEXES:
            conn.execute(text(ddl))
    return done
