"""VERİTABANI YEDEĞİ (1.26.0; kullanıcı kararı 6–7 Kasım 2026: "yedek betiği … yaz"; ücretli veritabanı planı en sona kaldı).

NASIL ÇALIŞIR
- Her gece GitHub Actions (`.github/workflows/yedek.yml`) sunucuya `POST /v1/backup/run` der; başlıkta BACKUP_KEY. Sunucu bütün tabloları
  tek bir tutarlı anda okur → JSON → gzip → AES-256-GCM ile ŞİFRELER ve dosyayı döner. GitHub dosyayı 30 gün saklar. Dosya şifreli:
  anahtarı bilmeyen açamaz (kapı arşivi dahil her şey içindedir).
- Yönetim › Özet › "Veritabanı yedeği": son yedekler + "Şimdi yedek indir" (aynı şifreli dosya, yöneticinin bilgisayarına).
- Geri yükleme: `python -m app.restore dosya.rotayedek` (BACKUP_KEY ve hedef DATABASE_URL ortamda). Yalnız BOŞ veritabanına yazar.
- Anahtar yalnız Render ortamında ve GitHub'ın Secrets bölümünde durur; koda, zip'e, sohbete yazılmaz. Anahtar yoksa yedek kapalı.

DOSYA: b"ROTAYDK1" + tuz (16 bayt) + nonce (12 bayt) + AES-GCM şifreli gzip(JSON) + etiket (16 bayt).
Anahtar = HKDF-SHA256(BACKUP_KEY, tuz, "rota-yedek-v1"). JSON: {"v": 1, "server": "…", "at": "…", "tables": {ad: {"cols": […], "rows": [[…]]}}};
tarih {"$t": iso}, gün {"$d": iso}, ikili veri {"$b": base64}.
"""
import hmac
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .admin import audit, current_admin
from .backup_core import MAGIC, backup_key, enabled, make_backup, open_backup  # noqa: F401 (open_backup: testler ve geri yükleme)
from .db import SessionLocal, get_db
from .limits import Window, client_ip
from .models import Admin, BackupRun, now, uid


def file_name(t: datetime | None = None) -> str:
    t = (t or now()).astimezone(timezone(timedelta(hours=3)))
    return "rota-yedek-" + t.strftime("%Y-%m-%d-%H%M") + ".rotayedek"


def stream_file(path: str, size: int, name: str) -> StreamingResponse:
    def it():
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1 << 16)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    return StreamingResponse(it(), media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Content-Length": str(size), "Cache-Control": "no-store",
    })


def run_out(x: BackupRun) -> dict:
    at = x.at if x.at is None or x.at.tzinfo else x.at.replace(tzinfo=timezone.utc)
    return {"id": x.id, "at": at.isoformat() if at else None, "by": x.by, "size": x.size, "tables": x.tables, "rows": x.rows}


def log_run(by: str, size: int, tables: int, rows: int) -> None:
    db = SessionLocal()
    try:
        db.add(BackupRun(id=uid(), by=by, size=size, tables=tables, rows=rows))
        db.query(BackupRun).filter(BackupRun.at < now() - timedelta(days=400)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def make_router(server_version: str) -> APIRouter:
    r = APIRouter()
    run_win = Window(6, 3600)  # saatte en çok 6 yedek (gece işi + yöneticinin indirmeleri)
    bad_win = Window(10, 3600)  # yanlış anahtar: IP başına saatte 10 deneme

    def produce(by: str, after=None) -> StreamingResponse:
        if not run_win.allow("run"):
            raise HTTPException(429, "Bu saat içinde yeterince yedek alındı; biraz sonra yeniden dene")
        path, size, tables, rows = make_backup(server_version)
        log_run(by, size, tables, rows)
        if after:
            after(size, tables, rows)
        return stream_file(path, size, file_name())

    @r.post("/v1/backup/run")
    def scheduled(request: Request, x_backup_key: str = Header(default="")):
        """Gece yedeği (GitHub Actions). Başlık: X-Backup-Key = BACKUP_KEY."""
        if not enabled():
            raise HTTPException(503, "Yedek kapalı: sunucuda BACKUP_KEY yok")
        ip = client_ip(request)
        if bad_win.count(ip) >= 10:
            raise HTTPException(429, "Çok fazla yanlış deneme")
        if not hmac.compare_digest(x_backup_key.strip().encode("utf-8"), backup_key().encode("utf-8")):
            bad_win.allow(ip)
            raise HTTPException(403, "Anahtar yanlış")
        return produce("schedule")

    @r.get("/v1/admin/backups")
    def admin_list(a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        rows = db.query(BackupRun).order_by(BackupRun.at.desc()).limit(30).all()
        return {"enabled": enabled(), "items": [run_out(x) for x in rows]}

    @r.post("/v1/admin/backups/download")
    def admin_download(request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Yönetici şimdi yedek indirir (şifreli; açmak için BACKUP_KEY gerekir)."""
        if not enabled():
            raise HTTPException(503, "Yedek kapalı: Render › Environment'a BACKUP_KEY eklenmeli")
        def done(size: int, tables: int, rows: int) -> None:
            audit(db, request, a, "backup_download", "backup", "", f"{tables} tablo · {rows} satır · {size} bayt")
            db.commit()

        return produce("admin", done)

    r.limiters = (run_win, bad_win)  # type: ignore[attr-defined]
    return r
