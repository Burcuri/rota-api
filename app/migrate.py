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
    # Dilim 10: rehberden bulunma
    ("users", "findable", "BOOLEAN DEFAULT TRUE"),
    # Dilim 11: harita ikonu
    ("users", "map_icon", "VARCHAR(24)"),
    # Dilim 12: haritada görünme
    ("users", "visible_map", "BOOLEAN DEFAULT FALSE"),
    ("users", "vis_lat", "FLOAT"),
    ("users", "vis_lon", "FLOAT"),
    ("users", "vis_at", "TIMESTAMP WITH TIME ZONE"),
    # Dilim 13 (0.12.9): benzersiz 7 haneli kullanıcı no
    ("users", "public_no", "VARCHAR(7)"),
    # 1.14.0: kalıcı çalışma bölgesi (il, ilçe, en çok 3 mahalle) ve takibin bağlı olduğu görev no
    ("users", "work_area", "TEXT"),
    ("follows", "task_no", "VARCHAR(7)"),
]

# create_all'un yeni kurulumda açtığı dizin adlarıyla aynı adlar (ix_<tablo>_<sütun>)
INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)",
    "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_public_no ON users (public_no)",
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
    n = backfill_public_no()
    if n:
        done.append(f"users.public_no: {n} eski hesaba numara verildi")
    return done


def backfill_public_no() -> int:
    """Kullanıcı no'su olmayan eski hesaplara rastgele, benzersiz 7 haneli numara verir. Her açılışta güvenle tekrar çalışır (yalnız boş olanlara dokunur)."""
    from .userno import unique_no

    insp = inspect(engine)
    if not insp.has_table("users") or "public_no" not in {c["name"] for c in insp.get_columns("users")}:
        return 0
    with engine.begin() as conn:
        ids = [r[0] for r in conn.execute(text("SELECT id FROM users WHERE public_no IS NULL ORDER BY id")).fetchall()]
        if not ids:
            return 0
        taken = {r[0] for r in conn.execute(text("SELECT public_no FROM users WHERE public_no IS NOT NULL")).fetchall()}
        for uid_ in ids:
            n = unique_no(taken)
            taken.add(n)
            conn.execute(text("UPDATE users SET public_no = :n WHERE id = :i"), {"n": n, "i": uid_})
    return len(ids)
