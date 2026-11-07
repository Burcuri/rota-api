"""YEDEKTEN GERİ YÜKLEME (1.26.0). Şifreli yedeği (`.rotayedek`) BOŞ bir veritabanına yazar.

Kullanım (sunucu klasöründe; BACKUP_KEY ve hedef DATABASE_URL ortamda):
    python -m app.restore rota-yedek-2026-11-07-0317.rotayedek --check     # yalnız açar: tablo ve satır sayıları
    python -m app.restore rota-yedek-2026-11-07-0317.rotayedek             # boş veritabanına yazar

- Hedefte veri varsa DURUR (var olan veriyle karışmasın). Yeni boş bir veritabanı açıp oraya yükle, sonra Render'daki DATABASE_URL'i değiştir.
- Yedekte olup bu sürümde olmayan tablo / sütun atlanır; bu sürümde yeni olan sütunlar varsayılan değeriyle dolar.
- Anahtar yanlışsa ya da dosya bozuksa hiçbir şey yazılmaz.
"""
import argparse
import os
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.restore", description="Rota şifreli yedeğini boş veritabanına geri yükler")
    ap.add_argument("file", help=".rotayedek dosyası")
    ap.add_argument("--check", action="store_true", help="yalnız aç ve özetle; veritabanına yazma")
    ap.add_argument("--yes", action="store_true", help="onay sorma")
    args = ap.parse_args(argv)
    key = (os.getenv("BACKUP_KEY") or "").strip()
    if not key:
        print("BACKUP_KEY ortamda yok (Render › Environment'taki değer).", file=sys.stderr)
        return 2
    from .backup_core import dec_value, open_backup  # yönetim / FastAPI kodu yüklenmez (SECRET_KEY gerekmez)

    with open(args.file, "rb") as f:
        data = f.read()
    try:
        doc = open_backup(data, key)
    except ValueError as e:
        print(f"Açılamadı: {e}", file=sys.stderr)
        return 3
    tables = doc.get("tables") or {}
    total = sum(len(t.get("rows") or []) for t in tables.values())
    print(f"Yedek: {doc.get('at')} · sunucu {doc.get('server')} · {len(tables)} tablo · {total} satır")
    if args.check:
        for name, t in tables.items():
            print(f"  {name}: {len(t.get('rows') or [])}")
        return 0

    from sqlalchemy import func, inspect, select, text

    from . import models  # noqa: F401 (tablolar kayda girsin)
    from .db import Base, engine

    url = engine.url.render_as_string(hide_password=True)
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        busy = [t.name for t in Base.metadata.sorted_tables if conn.execute(select(func.count()).select_from(t)).scalar()]
    if busy:
        print(f"Hedef veritabanı boş değil ({', '.join(busy[:6])}…). Boş bir veritabanına yükle.", file=sys.stderr)
        return 4
    if not args.yes:
        ok = input(f"{url} veritabanına {total} satır yazılsın mı? [evet/hayır] ").strip().lower()
        if ok not in ("e", "evet", "y", "yes"):
            print("Vazgeçildi.")
            return 1
    known = {t.name: t for t in Base.metadata.sorted_tables}
    skipped = [n for n in tables if n not in known]
    written = 0
    with engine.begin() as conn:
        for t in Base.metadata.sorted_tables:  # yabancı anahtar sırası
            src = tables.get(t.name)
            if not src:
                continue
            cols = list(src.get("cols") or [])
            keep = [i for i, c in enumerate(cols) if c in t.c]
            batch = []
            for row in src.get("rows") or []:
                batch.append({cols[i]: dec_value(row[i]) for i in keep})
                if len(batch) >= 500:
                    conn.execute(t.insert(), batch)
                    written += len(batch)
                    batch = []
            if batch:
                conn.execute(t.insert(), batch)
                written += len(batch)
        if engine.dialect.name == "postgresql":  # sayaçlı birincil anahtarların sayacı yedekteki en büyük değerden devam etsin
            insp = inspect(conn)
            for t in Base.metadata.sorted_tables:
                for c in t.primary_key.columns:
                    if c.autoincrement is True and insp.has_table(t.name):
                        conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', '{c.name}'), COALESCE((SELECT MAX({c.name}) FROM {t.name}), 0) + 1, false)"))
    print(f"Yazıldı: {written} satır.{(' Atlanan tablolar: ' + ', '.join(skipped)) if skipped else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
