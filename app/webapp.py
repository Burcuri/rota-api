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
import json
import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

DEFAULT_DIR = Path(__file__).resolve().parent / "webapp"
# 1.19.0: web ikizinin tarayıcı bildirimi hizmet çalışanı (web paketinden bağımsız; sunucunun static klasöründe durur)
SW_FILE = Path(__file__).resolve().parent / "static" / "web-sw.js"
IMMUTABLE = "public, max-age=31536000, immutable"
GZ_TYPES = {".js", ".css", ".html", ".json", ".svg", ".ttf", ".map", ".txt", ".xml"}
GZ_MIN = 1024
GZ_MAX_FILE = 8 * 1024 * 1024  # bundan büyük dosya sıkıştırılıp bellekte tutulmaz

# 1.19.0: web ikizi ana ekrana eklenebilsin (iPhone'da tarayıcı bildirimi YALNIZ ana ekrana eklenmiş uygulamada çalışır, iOS 16.4+).
# index.html'e (</head>'den önce) ana ekran bilgisi eklenir; paket değişmez. Simge: alıcı sayfasıyla aynı "R." işareti.
HEAD_TAGS = (
    '<link rel="manifest" href="/web/manifest.webmanifest">'
    '<link rel="apple-touch-icon" href="/static/alici-180.png">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-title" content="Rota">'
)
MANIFEST = {
    "id": "/web/",
    "name": "Rota Asistan",
    "short_name": "Rota",
    "lang": "tr",
    "start_url": "/web/",
    "scope": "/web/",
    "display": "standalone",
    "background_color": "#0D1B2A",
    "theme_color": "#0D1B2A",
    "icons": [
        {"src": "/static/alici-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/alici-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/static/alici-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

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


# {yol: (mtime_ns, boyut, ana ekran bilgisi eklenmiş index.html)}
_index_cache: dict[str, tuple[int, int, bytes]] = {}


def _index_bytes(path: Path) -> bytes:
    """index.html + ana ekran bilgisi (</head> varsa ve sayfada manifest yoksa). Dosya değişince yeniden okunur."""
    st = path.stat()
    key = str(path)
    hit = _index_cache.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    txt = path.read_bytes().decode("utf-8", "replace")
    if "</head>" in txt and 'rel="manifest"' not in txt:
        txt = txt.replace("</head>", HEAD_TAGS + "</head>", 1)
    data = txt.encode("utf-8")
    if len(_index_cache) > 8:
        _index_cache.clear()
    _index_cache[key] = (st.st_mtime_ns, st.st_size, data)
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

    @r.get("/web/rota-sw.js", include_in_schema=False)
    def web_service_worker():
        """Web ikizinin bildirim hizmet çalışanı: yalnız bildirimi gösterir, sayfa saklamaz. Kapsam /web/."""
        return FileResponse(SW_FILE, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/web/"})

    @r.get("/web/manifest.webmanifest", include_in_schema=False)
    def web_manifest():
        """Ana ekrana ekleme bilgisi (iPhone'da web ikizine bildirim için ana ekrana eklemek şart)."""
        return Response(json.dumps(MANIFEST, ensure_ascii=False), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})

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
        if target.name == "index.html" and target.parent.resolve() == root.resolve():
            try:
                body = _index_bytes(target)
            except OSError:
                return JSONResponse({"detail": "Web ikizi bu sunucuda yüklü değil"}, status_code=404)
            if len(body) >= GZ_MIN and "gzip" in (request.headers.get("accept-encoding") or "").lower():
                body = gzip.compress(body, compresslevel=6, mtime=0)
                headers["Content-Encoding"] = "gzip"
            headers["Content-Length"] = str(len(body))
            return Response(content=body if request.method != "HEAD" else b"", media_type="text/html; charset=utf-8", headers=headers)
        if target.suffix.lower() in GZ_TYPES and "gzip" in (request.headers.get("accept-encoding") or "").lower():
            data = _gzipped(target)
            if data is not None:
                headers["Content-Encoding"] = "gzip"
                headers["Content-Length"] = str(len(data))
                return Response(content=data if request.method != "HEAD" else b"", media_type=ctype, headers=headers)
        return FileResponse(target, media_type=ctype, headers=headers)

    return r
