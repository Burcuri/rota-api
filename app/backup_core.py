"""Yedek dosyasının çekirdeği (1.26.0): döküm, şifreleme, açma. FastAPI'ye ve yönetim koduna bağlı DEĞİL (geri yükleme betiği de kullanır).
Ayrıntı: app/backup.py."""
import base64
import gzip
import json
import os
import secrets
import tempfile
from datetime import date, datetime
from decimal import Decimal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import inspect, select

from .db import Base, engine
from .models import now

MAGIC = b"ROTAYDK1"
INFO = b"rota-yedek-v1"
MIN_KEY = 24
CHUNK = 1 << 20


def backup_key() -> str:
    return (os.getenv("BACKUP_KEY") or "").strip()


def enabled() -> bool:
    return len(backup_key()) >= MIN_KEY


def off_reason() -> str:
    """Yedek kapalıyken nedeni (8 Kasım 2026: kısa anahtar da "yok" diye görünüyordu, kullanıcı doğru yazdığını sanıyordu)."""
    if not backup_key():
        return "Yedek kapalı: sunucuda BACKUP_KEY yok (Render › Environment'a ekleyip deploy et)"
    return f"Yedek kapalı: BACKUP_KEY çok kısa (en az {MIN_KEY} karakter; Render'da Generate ile üret, sonra deploy et)"


def derive(key: str, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=INFO).derive(key.encode("utf-8"))


def enc_value(v):
    if isinstance(v, datetime):
        return {"$t": v.isoformat()}
    if isinstance(v, date):
        return {"$d": v.isoformat()}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$b": base64.b64encode(bytes(v)).decode("ascii")}
    if isinstance(v, Decimal):
        return float(v)
    return v


def dec_value(v):
    if isinstance(v, dict) and len(v) == 1:
        if "$t" in v:
            return datetime.fromisoformat(v["$t"])
        if "$d" in v:
            return date.fromisoformat(v["$d"])
        if "$b" in v:
            return base64.b64decode(v["$b"])
    return v


def write_dump(conn, out, server_version: str) -> tuple[int, int]:
    """Bütün tabloları `out`a (ikili akış) JSON olarak yazar. (tablo sayısı, satır sayısı)"""
    have = set(inspect(conn).get_table_names())
    w = lambda s: out.write(s.encode("utf-8"))  # noqa: E731
    w('{"v":1,"app":"rota","server":' + json.dumps(server_version) + ',"at":' + json.dumps(now().isoformat()) + ',"tables":{')
    tables = rows = 0
    for t in Base.metadata.sorted_tables:  # yabancı anahtar sırası (geri yüklerken de bu sırayla yazılır)
        if t.name not in have:
            continue
        cols = [c.name for c in t.columns]
        w(("," if tables else "") + json.dumps(t.name) + ':{"cols":' + json.dumps(cols) + ',"rows":[')
        n = 0
        res = conn.execution_options(stream_results=True, yield_per=500).execute(select(t))
        for part in res.partitions():
            for row in part:
                w(("," if n else "") + json.dumps([enc_value(v) for v in row], ensure_ascii=False, separators=(",", ":")))
                n += 1
        w("]}")
        tables += 1
        rows += n
    w("}}")
    return tables, rows


def seal(src, dst, key: str) -> None:
    """src (gzip) → dst: MAGIC + tuz + nonce + şifreli + etiket"""
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    enc = Cipher(algorithms.AES(derive(key, salt)), modes.GCM(nonce)).encryptor()
    enc.authenticate_additional_data(MAGIC)
    dst.write(MAGIC + salt + nonce)
    while True:
        chunk = src.read(CHUNK)
        if not chunk:
            break
        dst.write(enc.update(chunk))
    dst.write(enc.finalize())
    dst.write(enc.tag)


def open_backup(data: bytes, key: str) -> dict:
    """Şifreli yedeği açar → JSON sözlüğü. Anahtar yanlışsa / dosya bozuksa ValueError."""
    if data[:8] != MAGIC or len(data) < 8 + 16 + 12 + 16:
        raise ValueError("Bu bir Rota yedek dosyası değil")
    salt, nonce, body, tag = data[8:24], data[24:36], data[36:-16], data[-16:]
    dec = Cipher(algorithms.AES(derive(key, salt)), modes.GCM(nonce, tag)).decryptor()
    dec.authenticate_additional_data(MAGIC)
    try:
        raw = dec.update(body) + dec.finalize()
    except InvalidTag:
        raise ValueError("Anahtar yanlış ya da dosya bozuk") from None
    return json.loads(gzip.decompress(raw).decode("utf-8"))


def make_backup(server_version: str, key: str | None = None) -> tuple[str, int, int, int]:
    """Şifreli yedek dosyası üretir (geçici dosya; çağıran siler). (yol, bayt, tablo, satır)"""
    key = key or backup_key()
    gz_tmp = tempfile.TemporaryFile()
    try:
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                conn = conn.execution_options(isolation_level="REPEATABLE READ")  # bütün tablolar aynı anın görüntüsü
            with conn.begin():
                with gzip.GzipFile(fileobj=gz_tmp, mode="wb", compresslevel=6, mtime=0) as gz:
                    tables, rows = write_dump(conn, gz, server_version)
        gz_tmp.seek(0)
        out = tempfile.NamedTemporaryFile(prefix="rota-yedek-", suffix=".rotayedek", delete=False)
        try:
            seal(gz_tmp, out, key)
        finally:
            out.close()
    finally:
        gz_tmp.close()
    return out.name, os.path.getsize(out.name), tables, rows
