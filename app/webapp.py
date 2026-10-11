"""Web ikizi: uygulamanın tarayıcı sürümü (Expo web paketi) sunucunun `/web` yolundan yayınlanır.

Paket `app/webapp/` klasöründe durur (uygulama deposunda `npm run web:build` ile üretilir, bu klasöre kopyalanır). Klasör yoksa `/web` 404 verir;
sunucunun geri kalanı etkilenmez. Sunucu ile web ikizi AYNI adreste olduğu için tarayıcı API'yi sayfanın kendi adresinden çağırır (CORS sorunu yok).

- Adı özlü (içeriğe göre değişen) dosyalar (`/web/_expo/...`, `/web/assets/...`): bir yıl önbellek, değişmez.
- `index.html` ve diğer kök dosyalar: her seferinde yeniden sorulur (yeni sürüm hemen görünür).
- Uzantısız bilinmeyen adresler (`/web/herhangi/bir/yer`) tek sayfa uygulama olduğu için `index.html` döner; uzantılı olup bulunmayan dosya 404.
- Tarayıcı gzip kabul ediyorsa metin dosyaları (js, css, html, json, svg, ttf) sıkıştırılmış gider; sıkıştırma ilk istekte bir kez yapılır ve bellekte tutulur.
- Katı CSP (alıcı / yönetim sayfaları gibi) BU yola uygulanmaz: uygulama harita karoları ve başka açık servisler çağırır. Çerçeve içine alınmaz.
"""
import gzip
import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

DEFAULT_DIR = Path(__file__).resolve().parent / "webapp"
IMMUTABLE = "public, max-age=31536000, immutable"
GZ_TYPES = {".js", ".css", ".html", ".json", ".svg", ".ttf", ".map", ".txt", ".xml"}
GZ_MIN = 1024
GZ_MAX_FILE = 8 * 1024 * 1024  # bundan büyük dosya sıkıştırılıp bellekte tutulmaz

# Ek MIME kayıtları (bazı sistemlerde mimetypes bilmez)
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("font/ttf", ".ttf")


def web_dir() -> Path:
    return Path(os.getenv("WEB_APP_DIR") or DEFAULT_DIR)


# {yol: (mtime_ns, boyut, sıkıştırılmış bayt)}
_gz_cache: dict[str, tuple[int, int, bytes]] = {}


def _gzipped(path: Path) -> bytes | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if st.st_size < GZ_MIN or st.st_size > GZ_MAX_FILE:
        return None
    key = str(path)
    hit = _gz_cache.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        data = gzip.compress(path.read_bytes(), compresslevel=6, mtime=0)
    except OSError:
        return None
    if len(_gz_cache) > 64:
        _gz_cache.clear()
    _gz_cache[key] = (st.st_mtime_ns, st.st_size, data)
    return data


def _safe_file(root: Path, rel: str) -> Path | None:
    """`rel` kök klasörün içinde var olan bir dosya mı? Üst klasöre çıkma / gizli dosya / .gz doğrudan isteği reddedilir."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if any(p in (".", "..") or p.startswith(".") for p in parts):
        return None
    if parts and parts[-1].endswith((".gz", ".br")):
        return None
    try:
        base = root.resolve()
        target = (base.joinpath(*parts) if parts else base).resolve()
        target.relative_to(base)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def make_web_router() -> APIRouter:
    r = APIRouter()

    @r.get("/web", include_in_schema=False)
    def web_slash():
        return RedirectResponse("/web/", status_code=307)

    @r.api_route("/web/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web_files(path: str, request: Request):
        root = web_dir()
        if not root.is_dir():
            return JSONResponse({"detail": "Web ikizi bu sunucuda yüklü değil"}, status_code=404)
        target = _safe_file(root, path)
        spa = False
        if target is None:
            last = path.rstrip("/").rsplit("/", 1)[-1]
            if "." in last:  # uzantılı ve bulunamayan dosya: gerçek 404 (index.html ile karıştırılmaz)
                return JSONResponse({"detail": "Yok"}, status_code=404)
            target = _safe_file(root, "index.html")
            spa = True
            if target is None:
                return JSONResponse({"detail": "Web ikizi bu sunucuda yüklü değil"}, status_code=404)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        hashed = (not spa) and path.split("/", 1)[0] in ("_expo", "assets")
        cache = IMMUTABLE if hashed else "no-cache"
        headers = {"Cache-Control": cache, "Vary": "Accept-Encoding"}
        if target.suffix.lower() in GZ_TYPES and "gzip" in (request.headers.get("accept-encoding") or "").lower():
            data = _gzipped(target)
            if data is not None:
                headers["Content-Encoding"] = "gzip"
                headers["Content-Length"] = str(len(data))
                return Response(content=data if request.method != "HEAD" else b"", media_type=ctype, headers=headers)
        return FileResponse(target, media_type=ctype, headers=headers)

    return r
