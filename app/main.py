import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import events
from .auth import (
    DUMMY_HASH,
    AccountBlocked,
    PasswordChangeRequired,
    check_pw,
    current_user,
    hash_pw,
    is_phone_like,
    norm_email,
    norm_phone,
    norm_username,
    user_token,
)
from .db import Base, engine, get_db
from .limits import Window
from .geo import sort_packages
from .doors import make_router as make_doors_router
from .admin import make_router as make_admin_router
from .errors import make_router as make_errors_router
from .follow import make_router as make_follow_router
from .vehicle import make_router as make_vehicle_router, withdraw as vehicle_withdraw
from .handoff import make_router as make_handoff_router
from .migrate import ensure_schema
from .relay import make_router
from .reviews import make_router as make_reviews_router
from .webapp import make_web_router
from .models import ConsentLog, Job, JobPackage, User, now, uid

Base.metadata.create_all(bind=engine)
# create_all var olan tabloya sütun eklemez: eski veritabanına yeni sütunlar burada eklenir
for _msg in ensure_schema():
    print("[şema]", _msg, flush=True)

ROLES = ("courier", "employer", "sponsor")

# ESKİ akış (Başvur → Kabul → Yola çık; adresler sunucuda). Yeni tanımda KAPALI: ilan + mesajlaşma.
# Yalnız sunum/deneme için Render ortamına ESKI_AKIS=1 yazılırsa açılır.
ESKI_AKIS = os.getenv("ESKI_AKIS", "") == "1"

# Mesaj aktarıcısı sınırları (Render ortam değişkeniyle değiştirilebilir)
RELAY_TTL_DAYS = float(os.getenv("RELAY_TTL_DAYS", "7"))  # ulaşmayan mesaj en geç bu kadar gün sonra silinir
RELAY_MAX_BYTES = int(os.getenv("RELAY_MAX_BYTES", str(256 * 1024)))  # tek mesaj (adres listesi dahil)
RELAY_USER_MAX_BYTES = int(os.getenv("RELAY_USER_MAX_BYTES", str(10 * 1024 * 1024)))  # bir kişiyi bekleyen toplam
GROUP_MAX = int(os.getenv("GROUP_MAX", "7"))  # grup üst sınırı (gönderen dahil)

# Ortak kapı noktası katmanı (Render ortam değişkeniyle değiştirilebilir)
DOOR_DAILY_MAX = int(os.getenv("DOOR_DAILY_MAX", "400"))  # kişi başı 24 saatte en çok yeni oy
DOOR_TRUST_SCORE = float(os.getenv("DOOR_TRUST_SCORE", "2"))  # "doğrulanmış" sayılmak için gereken puan (her kişi 1, alıcı 2)
DOOR_MERGE_M = float(os.getenv("DOOR_MERGE_M", "35"))  # bu mesafe içindeki raporlar aynı nokta sayılır
DOOR_REQUIRE_CONSENT = os.getenv("DOOR_REQUIRE_CONSENT", "0") == "1"  # 1 yazılırsa katkı için consent_log.kapi şartı aranır (varsayılan: şart yok, kişi verisi tutulmaz)

# Teslim onayı ve hata takibi (Render ortam değişkeniyle değiştirilebilir)
HANDOFF_TTL_HOURS = float(os.getenv("HANDOFF_TTL_HOURS", "48"))  # alıcıyla ilgili kayıtlar en geç bu kadar saat sonra SİLİNİR
HANDOFF_DAILY_MAX = int(os.getenv("HANDOFF_DAILY_MAX", "300"))  # kurye başı 24 saatte en çok yeni bağlantı
HANDOFF_REVIEW_HOURS = float(os.getenv("HANDOFF_REVIEW_HOURS", "24"))  # kurye teslimi bitirdikten sonra alıcı en çok bu kadar saat daha yorum yazabilir
ERROR_KEEP_DAYS = float(os.getenv("ERROR_KEEP_DAYS", "14"))
ERROR_MAX_ROWS = int(os.getenv("ERROR_MAX_ROWS", "5000"))
ERROR_DAILY_MAX = int(os.getenv("ERROR_DAILY_MAX", "3000"))

# Yönetim kayıtları (Render ortam değişkeniyle değiştirilebilir)
EVENT_KEEP_DAYS = float(os.getenv("EVENT_KEEP_DAYS", "60"))  # etkinlik akışı bu kadar gün tutulur
EVENT_MAX_ROWS = int(os.getenv("EVENT_MAX_ROWS", "50000"))
REVIEW_KEEP_DAYS = float(os.getenv("REVIEW_KEEP_DAYS", "365"))  # değerlendirmeler (yorum metni dahil) bu kadar gün tutulur
AUDIT_KEEP_DAYS = float(os.getenv("AUDIT_KEEP_DAYS", "365"))  # yönetici işlem günlüğü

# Takip ("Follow": işverenin kuryeyi haritada canlı izlemesi; izin kuryeden; canlı konum bitince silinir, iz + son ilerleme 48 saat saklanır)
FOLLOW_ASK_MIN = float(os.getenv("FOLLOW_ASK_MIN", "30"))  # kurye takip isteğine en çok bu kadar dakika içinde yanıt verir
FOLLOW_MAX_HOURS = float(os.getenv("FOLLOW_MAX_HOURS", "12"))  # bir takip en çok bu kadar saat sürer (sonra yeniden izin gerekir)
FOLLOW_KEEP_HOURS = float(os.getenv("FOLLOW_KEEP_HOURS", "48"))  # biten takip kaydı (iz + son ilerleme) bu kadar saat saklanır, sonra silinir
FOLLOW_STALE_MIN = float(os.getenv("FOLLOW_STALE_MIN", "30"))  # bu kadar dakikadır güncellenmeyen konum silinir
FOLLOW_COOLDOWN_MIN = float(os.getenv("FOLLOW_COOLDOWN_MIN", "10"))  # reddedilen isteğin aynı işverenden yeniden gelmesi için bekleme
FOLLOW_TRAIL_SEC = float(os.getenv("FOLLOW_TRAIL_SEC", "15"))  # iz noktaları arasındaki en kısa süre (saniye)
FOLLOW_TRAIL_MAX = int(os.getenv("FOLLOW_TRAIL_MAX", "4000"))  # bir takip için saklanan en çok iz noktası
VEHICLE_VIEW_PER_MIN = int(os.getenv("VEHICLE_VIEW_PER_MIN", "90"))  # bir işletmenin dakikada bakabileceği kurye araç profili sayısı


@asynccontextmanager
async def lifespan(_app):
    relay_router.start_cleaner()  # ulaşmayan mesajları arka planda siler (Render ücretsizde zamanlanmış iş yok)
    threading.Thread(target=handoff_router.loop, name="handoff-purge", daemon=True).start()  # süresi dolan teslim bağlantılarını siler
    follow_router.start_cleaner()  # süresi dolan takipleri kapatır, eski konum noktalarını siler
    threading.Thread(target=_housekeeping_loop, name="housekeeping", daemon=True).start()
    yield


def _housekeeping_loop():
    """Saatte bir: eski hata raporlarını, etkinlik akışını, yönetici günlüğünü ve değerlendirmeleri saklama süresine göre siler."""
    from .db import SessionLocal

    while True:
        time.sleep(3600)
        try:
            db = SessionLocal()
            try:
                errors_router.purge(db)
                admin_router.purge(db, EVENT_KEEP_DAYS, AUDIT_KEEP_DAYS, REVIEW_KEEP_DAYS, EVENT_MAX_ROWS)
            finally:
                db.close()
        except Exception as e:
            print("[temizlik] hata:", type(e).__name__, flush=True)


app = FastAPI(title="Rota Asistan", version="1.7.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_BASE = os.getenv("PUBLIC_BASE", "").rstrip("/")


@app.exception_handler(AccountBlocked)
async def _blocked_handler(_request: Request, exc: AccountBlocked):
    """Askıya alınmış hesap: uygulama `code: banned` görünce oturumu kapatır ve nedeni gösterir."""
    return JSONResponse(status_code=403, content={"detail": exc.detail, "code": exc.code, "reason": exc.reason})


@app.exception_handler(RequestValidationError)
async def _validation_handler(_request: Request, exc: RequestValidationError):
    """422: hangi alanın neden reddedildiği döner; gönderilen DEĞER geri yazılmaz (şifre vb. yankılanmaz). Ayrıca NaN gibi JSON'a
    sığmayan değerler yanıtı çökertmez (varsayılan işleyici girdiyi olduğu gibi geri yazar)."""
    items = [{"loc": [x for x in e.get("loc", ()) if isinstance(x, (str, int))], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": items})


@app.exception_handler(PasswordChangeRequired)
async def _must_change_handler(_request: Request, exc: PasswordChangeRequired):
    return JSONResponse(status_code=403, content={"detail": exc.detail, "code": exc.code})


def drop_stored_addresses() -> int:
    """Yeni akışta sunucuda adres arşivi tutulmaz. Eski akıştan kalan adres satırları (alıcı adı, telefon, adres)
    açılışta silinir; ilanın adres SAYISI korunur. ESKI_AKIS=1 iken dokunulmaz."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE jobs SET package_count = (SELECT COUNT(*) FROM job_packages p WHERE p.job_id = jobs.id) "
            "WHERE (package_count IS NULL OR package_count = 0) AND EXISTS (SELECT 1 FROM job_packages p WHERE p.job_id = jobs.id)"
        ))
        return conn.execute(text("DELETE FROM job_packages")).rowcount or 0


if not ESKI_AKIS:
    _n = drop_stored_addresses()
    if _n:
        print(f"[şema] eski akıştan kalan {_n} adres satırı silindi (adresler artık yalnız telefonda)", flush=True)

relay_router = make_router(RELAY_TTL_DAYS, RELAY_MAX_BYTES, RELAY_USER_MAX_BYTES, GROUP_MAX)
app.include_router(relay_router)
doors_router = make_doors_router(DOOR_DAILY_MAX, DOOR_REQUIRE_CONSENT, DOOR_TRUST_SCORE, DOOR_MERGE_M)
app.include_router(doors_router)
handoff_router = make_handoff_router(doors_router.add_recipient_pin, HANDOFF_TTL_HOURS, HANDOFF_DAILY_MAX, HANDOFF_REVIEW_HOURS)
app.include_router(handoff_router)
app.include_router(make_reviews_router())
follow_router = make_follow_router(FOLLOW_ASK_MIN, FOLLOW_MAX_HOURS, FOLLOW_KEEP_HOURS, FOLLOW_STALE_MIN, FOLLOW_COOLDOWN_MIN, FOLLOW_TRAIL_SEC, FOLLOW_TRAIL_MAX)
app.include_router(follow_router)
vehicle_router = make_vehicle_router(VEHICLE_VIEW_PER_MIN)
app.include_router(vehicle_router)
errors_router = make_errors_router(ERROR_KEEP_DAYS, ERROR_MAX_ROWS, ERROR_DAILY_MAX)
app.include_router(errors_router)
admin_router = make_admin_router(ERROR_KEEP_DAYS, FOLLOW_KEEP_HOURS)
app.include_router(admin_router)
app.include_router(make_web_router())  # /web: uygulamanın tarayıcı sürümü (web ikizi)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Alıcı sayfası ve yönetim sayfası: kod / anahtar içeren adresler önbelleğe alınmaz, adres başka sitelere sızdırılmaz,
# çerçeve içine alınamaz; betikler yalnız kendi sunucumuzdan yüklenir (harita karoları OpenStreetMap'ten).
_SECURE_PREFIXES = ("/a/", "/yonetim")
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://tile.openstreetmap.org https://*.tile.openstreetmap.org; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


@app.middleware("http")
async def secure_headers(request: Request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p.startswith(_SECURE_PREFIXES):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "origin"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = _CSP
    elif p.startswith("/static/"):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-cache"
    elif p == "/web" or p.startswith("/web/"):
        # Web ikizi (uygulamanın kendisi): katı CSP YOK (harita karoları ve başka servisler çağrılır); çerçeveye alınamaz, adres yalnız kök olarak iletilir
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        resp.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    return resp


class RegisterIn(BaseModel):
    phone: str
    password: str
    name: str = ""
    username: str | None = None  # isteğe bağlı; telefon yerine girişte kullanılır
    email: str | None = None  # isteğe bağlı; henüz doğrulanmıyor
    role: str = "courier"  # courier | employer | sponsor (eski istemciler göndermez → kurye)


class LoginIn(BaseModel):
    # Üçünden biri yeterli: `phone` ya da `username` ya da ikisini de karşılayan `login`.
    login: str | None = None
    phone: str | None = None
    username: str | None = None
    password: str


class RoleIn(BaseModel):
    role: str


class ProfileIn(BaseModel):
    """Gönderilmeyen alana dokunulmaz. Boş metin ("") kullanıcı adını / e-postayı kaldırır."""

    name: str | None = None
    username: str | None = None
    email: str | None = None


class LocIn(BaseModel):
    lat: float = Field(ge=-90, le=90)  # NaN / Infinity / alan dışı değer kabul edilmez (yanıtı ve veritabanını bozmasın)
    lon: float = Field(ge=-180, le=180)


class PasswordIn(BaseModel):
    old_password: str = Field(default="", alias="oldPassword", max_length=200)
    new_password: str = Field(default="", alias="newPassword", max_length=200)

    model_config = {"populate_by_name": True}


class PkgIn(BaseModel):
    customer: str
    phone: str
    address: str
    neighborhood: str = ""
    street: str = ""
    door_number: str = ""
    apartment: str = ""
    floor: str = ""
    lat: float = Field(ge=-90, le=90)  # NaN / Infinity / alan dışı değer kabul edilmez (yanıtı ve veritabanını bozmasın)
    lon: float = Field(ge=-180, le=180)
    precision: str = ""


class JobIn(BaseModel):
    title: str = "Teslimat işi"
    note: str = ""
    lat: float = Field(ge=-90, le=90)  # NaN / Infinity / alan dışı değer kabul edilmez (yanıtı ve veritabanını bozmasın)
    lon: float = Field(ge=-180, le=180)
    # Yeni akışta adresler GÖNDERİLMEZ: yalnız sayı + bölge. (Eski istemci packages gönderirse saklanmaz, yalnız sayılır.)
    package_count: int = Field(default=0, ge=0, le=500, alias="packageCount")
    area: str = Field(default="", max_length=160)
    fee: float | None = Field(default=None, ge=0, le=1_000_000)
    packages: list[PkgIn] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ConsentIn(BaseModel):
    """Açık rıza ekranındaki seçimler. Zamanı sunucu kendi saatiyle yazar; istemci saati yalnız bilgi içindir."""

    version: int = Field(ge=0, le=1000)
    voice: bool = False
    ocr: bool = False
    grup_konum: bool = Field(default=False, alias="grupKonum")
    kapi: bool = False  # ortak kapı noktası katmanına katkı
    # Araç profilimi işletmeler görsün (AYRI açık rıza; araç ekranında verilir / geri çekilir).
    # Alan yoksa (eski istemci, ya da bu cihaz araç kararı vermedi) ÖNCEKİ karar korunur: başka cihazdaki seçimi sessizce bozmaz.
    arac: bool | None = None
    aydinlatma_at: int = Field(default=0, ge=0, le=4102444800000, alias="aydinlatmaAt")
    acik_riza_at: int = Field(default=0, ge=0, le=4102444800000, alias="acikRizaAt")
    # Kullanım koşulları onayı (eski istemci göndermez → 0)
    sozlesme_version: int = Field(default=0, ge=0, le=1000, alias="sozlesmeV")
    sozlesme_at: int = Field(default=0, ge=0, le=4102444800000, alias="sozlesmeAt")

    model_config = {"populate_by_name": True}


class PinIn(BaseModel):
    phone: str
    lat: float = Field(ge=-90, le=90)  # NaN / Infinity / alan dışı değer kabul edilmez (yanıtı ve veritabanını bozmasın)
    lon: float = Field(ge=-180, le=180)


def user_out(u: User) -> dict:
    return {
        "id": u.id,
        "phone": u.phone,
        "name": u.name,
        "username": u.username,
        "email": u.email,
        "active_role": u.active_role,
        "lat": u.lat,
        "lon": u.lon,
        "mustChangePassword": bool(u.must_change_password),
    }


def consent_out(c: ConsentLog | None) -> dict:
    if c is None:  # hiç rıza kaydı yok
        return {"version": 0, "voice": False, "ocr": False, "grupKonum": False, "kapi": False, "arac": False, "aydinlatmaAt": 0, "acikRizaAt": 0, "sozlesmeV": 0, "sozlesmeAt": 0, "recordedAt": None}
    return {
        "version": c.version,
        "voice": bool(c.voice),
        "ocr": bool(c.ocr),
        "grupKonum": bool(c.grup_konum),
        "kapi": bool(c.kapi),
        "arac": bool(c.arac),
        "aydinlatmaAt": c.aydinlatma_ms or 0,
        "acikRizaAt": c.acik_riza_ms or 0,
        "sozlesmeV": c.sozlesme_version or 0,
        "sozlesmeAt": c.sozlesme_ms or 0,
        "recordedAt": c.created_at.isoformat() if c.created_at else None,
    }


def latest_consent(db: Session, user_id: str) -> ConsentLog | None:
    return db.query(ConsentLog).filter(ConsentLog.user_id == user_id).order_by(ConsentLog.created_at.desc()).first()


def pkg_out(p: JobPackage, base: str = "") -> dict:
    link = f"{base}/p/{p.token}" if base else f"/p/{p.token}"
    return {
        "id": p.id,
        "customer": p.customer,
        "phone": p.phone,
        "address": p.address,
        "neighborhood": p.neighborhood,
        "street": p.street,
        "doorNumber": p.door_number,
        "apartment": p.apartment,
        "floor": p.floor,
        "lat": p.lat,
        "lon": p.lon,
        "precision": p.precision,
        "pinUpdated": bool(p.pin_updated),
        "pinUpdatedAt": p.pin_updated_at.isoformat() if p.pin_updated_at else None,
        "status": p.status,
        "sortM": p.sort_m,
        "token": p.token,
        "pinUrl": link,
    }


def job_out(j: Job, db: Session, include_packages=False) -> dict:
    base = PUBLIC_BASE
    pkgs = list(j.packages)
    if include_packages:
        pkgs = sort_packages(pkgs, j.employer_lat, j.employer_lon)
        db.commit()
    applicant = db.get(User, j.applicant_id) if j.applicant_id else None
    courier = db.get(User, j.courier_id) if j.courier_id else None
    employer = db.get(User, j.employer_id)

    # Telefon numaraları ilanda GÖSTERİLMEZ (giriş yapmış herkes ilanları görür). Taraflar mesajlaşarak paylaşır.
    def person(u: User | None) -> dict | None:
        return {"id": u.id, "name": u.name} if u else None

    return {
        "id": j.id,
        "title": j.title,
        "note": j.note,
        "status": j.status,
        "employerLat": j.employer_lat,
        "employerLon": j.employer_lon,
        "packageCount": j.package_count or len(j.packages),
        "area": j.area or "",
        "fee": j.fee,
        "createdAt": j.created_at.isoformat() if j.created_at else None,
        "employer": person(employer),
        "applicant": person(applicant),
        "courier": person(courier),
        "packages": [pkg_out(p, base) for p in pkgs] if include_packages else None,
    }


def old_flow_only():
    if not ESKI_AKIS:
        raise HTTPException(410, "Bu akış kapatıldı: ilan sahibine mesaj gönderin")


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok", "service": "rota-asistan"}


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    """Uyandırma / izleme ucu: veritabanına dokunmaz, anında döner (Render ücretsiz servis 15 dk kullanılmayınca uyur;
    dışarıdan düzenli çağrılırsa uyumaz). Ayrıntı: GUNCELLEME_ADIMLARI.md › "Sunucuyu uyanık tut"."""
    return {"ok": True}


@app.get("/yonetim", include_in_schema=False)
def admin_page():
    return FileResponse(os.path.join(STATIC_DIR, "yonetim.html"), media_type="text/html; charset=utf-8")


@app.post("/v1/auth/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    phone = norm_phone(body.phone)
    if len(body.password or "") < 6:
        raise HTTPException(400, "Şifre en az 6 karakter")
    if len(body.password) > 128:
        raise HTTPException(400, "Şifre en fazla 128 karakter")
    name = (body.name or "").strip()
    if len(name) > 120:
        raise HTTPException(400, "Ad en fazla 120 karakter")
    role = (body.role or "courier").strip().lower()
    if role not in ROLES:
        raise HTTPException(400, "Hesap türü kurye, işveren veya sponsor olmalı")
    username = norm_username(body.username or "")
    email = norm_email(body.email or "")
    if db.query(User).filter(User.phone == phone).first():
        raise HTTPException(400, "Bu telefon zaten kayıtlı — giriş yap")
    if username and db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "Bu kullanıcı adı alınmış, başka bir ad seç")
    u = User(
        id=uid(),
        phone=phone,
        password_hash=hash_pw(body.password),
        name=name or phone,
        active_role=role,
        username=username or None,
        email=email or None,
        last_login_at=now(),
    )
    db.add(u)
    events.log(db, "user.register", u.id, role=role)
    try:
        db.commit()
    except IntegrityError:  # aynı anda başka biri aynı telefonu / kullanıcı adını aldı
        db.rollback()
        raise HTTPException(400, "Bu telefon ya da kullanıcı adı zaten kayıtlı")
    db.refresh(u)
    return {"token": user_token(u), "user": user_out(u)}


@app.post("/v1/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    ident = (body.login or body.phone or body.username or "").strip()
    if not ident:
        raise HTTPException(400, "Telefon veya kullanıcı adını yaz")
    if is_phone_like(ident):
        u = db.query(User).filter(User.phone == norm_phone(ident)).first()
    else:
        u = db.query(User).filter(User.username == ident.lower()).first()
    # Hesap bulunamasa da şifre denetimi yapılır: yanıt süresi "bu hesap var mı" sorusunu ele vermesin
    ok = check_pw(body.password or "", u.password_hash if u else DUMMY_HASH)
    if not u or not ok:
        raise HTTPException(401, "Telefon / kullanıcı adı veya şifre yanlış")
    if u.banned:  # şifreyi bilen kişiye söylenir (bilmeyene hesabın varlığı / durumu açıklanmaz)
        raise AccountBlocked(u.banned_reason or "")
    u.last_login_at = now()
    events.log(db, "user.login", u.id)
    db.commit()
    return {"token": user_token(u), "user": user_out(u)}


pw_fail = Window(8, 900)  # şifre değiştirirken yanlış "eski şifre": kullanıcı başı 15 dakikada 8


@app.post("/v1/me/password")
def change_password(body: PasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Kullanıcı kendi şifresini değiştirir. Diğer cihazlardaki oturumlar kapanır; bu cihaza yeni oturum anahtarı döner.
    Yönetim şifreyi sıfırladıysa (must_change_password) bu uç çalışır, diğerleri çalışmaz."""
    if pw_fail.count(user.id) >= pw_fail.max_hits:
        raise HTTPException(429, "Çok fazla yanlış deneme, biraz bekleyin")
    if not check_pw(body.old_password or "", user.password_hash):
        pw_fail.allow(user.id)
        raise HTTPException(400, "Eski şifre yanlış")
    new = body.new_password or ""
    if len(new) < 6:
        raise HTTPException(400, "Yeni şifre en az 6 karakter")
    if len(new) > 128:
        raise HTTPException(400, "Yeni şifre en fazla 128 karakter")
    if new == body.old_password:
        raise HTTPException(400, "Yeni şifre eskisiyle aynı olamaz")
    pw_fail.reset_key(user.id)
    user.password_hash = hash_pw(new)
    user.must_change_password = False
    user.token_version = (user.token_version or 0) + 1
    events.log(db, "user.password", user.id)
    db.commit()
    db.refresh(user)
    return {"token": user_token(user), "user": user_out(user)}


@app.get("/v1/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


@app.post("/v1/me/role")
def set_role(body: RoleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(400, "Rol kurye, işveren veya sponsor olmalı")
    if user.active_role != body.role:
        events.log(db, "user.role", user.id, role=body.role)
    user.active_role = body.role
    db.commit()
    return user_out(user)


@app.post("/v1/me/profile")
def update_profile(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Ad, kullanıcı adı, e-posta güncelle. Telefon burada DEĞİŞMEZ (doğrulama olmadan değiştirilmesi hesabı kaçırmaya açıktır)."""
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Ad boş olamaz")
        if len(name) > 120:
            raise HTTPException(400, "Ad en fazla 120 karakter")
        user.name = name
    if body.username is not None:
        username = norm_username(body.username)
        if username and db.query(User).filter(User.username == username, User.id != user.id).first():
            raise HTTPException(400, "Bu kullanıcı adı alınmış, başka bir ad seç")
        user.username = username or None
    if body.email is not None:
        user.email = norm_email(body.email) or None
    events.log(db, "user.profile", user.id, fields=[k for k in ("name", "username", "email") if getattr(body, k) is not None])
    try:
        db.commit()
    except IntegrityError:  # aynı anda başka biri aynı kullanıcı adını aldı
        db.rollback()
        raise HTTPException(400, "Bu kullanıcı adı alınmış, başka bir ad seç")
    db.refresh(user)
    return user_out(user)


@app.post("/v1/me/location")
def set_loc(body: LocIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.lat = body.lat
    user.lon = body.lon
    db.commit()
    return user_out(user)


@app.post("/v1/me/consent")
def save_consent(body: ConsentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Rıza ver / değiştir / geri çek. Her çağrı yeni satır ekler (geçmiş silinmez); aynı içerik tekrar gelirse eklenmez."""
    last = latest_consent(db, user.id)
    arac = bool(last.arac) if (body.arac is None and last is not None) else bool(body.arac)
    same = (
        last is not None
        and last.version == body.version
        and bool(last.voice) == body.voice
        and bool(last.ocr) == body.ocr
        and bool(last.grup_konum) == body.grup_konum
        and bool(last.kapi) == body.kapi
        and bool(last.arac) == arac
        and (last.acik_riza_ms or 0) == body.acik_riza_at
        and (last.sozlesme_version or 0) == body.sozlesme_version
    )
    if same:
        return consent_out(last)
    row = ConsentLog(
        user_id=user.id,
        version=body.version,
        voice=body.voice,
        ocr=body.ocr,
        grup_konum=body.grup_konum,
        kapi=body.kapi,
        arac=arac,
        aydinlatma_ms=body.aydinlatma_at,
        acik_riza_ms=body.acik_riza_at,
        sozlesme_version=body.sozlesme_version,
        sozlesme_ms=body.sozlesme_at,
    )
    db.add(row)
    if not arac:
        vehicle_withdraw(db, user.id)  # araç profili rızası kapalı: sunucudaki profil de silinir (rıza ile aynı işlemde)
    db.commit()
    db.refresh(row)
    return consent_out(row)


@app.get("/v1/me/consent")
def get_consent(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return consent_out(latest_consent(db, user.id))


@app.get("/v1/me/consent/history")
def consent_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Kullanıcının kendi rıza geçmişi (en yeni başta). KVKK: verilerime erişim hakkı."""
    rows = db.query(ConsentLog).filter(ConsentLog.user_id == user.id).order_by(ConsentLog.created_at.desc()).limit(100).all()
    return [consent_out(r) for r in rows]


@app.post("/v1/jobs")
def create_job(body: JobIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.active_role == "sponsor":
        raise HTTPException(403, "Sponsor hesabı ilan veremez")
    count = body.package_count or len(body.packages)
    if count < 1:
        raise HTTPException(400, "İlanda en az bir adres olmalı")
    rows = []
    # Yeni akışta adresler (alıcı adı, telefon, adres) sunucuya YAZILMAZ; işletmenin telefonunda kalır.
    for p in body.packages if ESKI_AKIS else []:
        phone = norm_phone(p.phone)
        if not p.customer.strip():
            raise HTTPException(400, "Her pakette müşteri adı zorunlu")
        rows.append(
            JobPackage(
                customer=p.customer.strip(),
                phone=phone,
                address=p.address.strip(),
                neighborhood=p.neighborhood,
                street=p.street,
                door_number=p.door_number,
                apartment=p.apartment,
                floor=p.floor,
                lat=p.lat,
                lon=p.lon,
                precision=p.precision,
                token=uid(),
            )
        )
    job = Job(
        id=uid(),
        employer_id=user.id,
        title=(body.title or "Teslimat işi").strip(),
        note=body.note or "",
        employer_lat=body.lat,
        employer_lon=body.lon,
        status="open",
        package_count=count,
        area=(body.area or "").strip(),
        fee=body.fee,
        packages=rows,
    )
    user.active_role = "employer"
    user.lat = body.lat
    user.lon = body.lon
    db.add(job)
    events.log(db, "job.create", user.id, job.id, n=count)
    db.commit()
    db.refresh(job)
    return job_out(job, db, include_packages=ESKI_AKIS)


@app.delete("/v1/jobs/{job_id}")
def close_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """İşletme ilanını yayından kaldırır. Varsa (eski akıştan kalma) adres satırları da silinir."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "İlan yok")
    if job.employer_id != user.id:
        raise HTTPException(403, "Yalnız ilanı veren kaldırabilir")
    job.status = "closed"
    for p in list(job.packages):
        db.delete(p)
    events.log(db, "job.close", user.id, job.id)
    db.commit()
    return {"ok": True}


@app.get("/v1/jobs/open")
def open_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Job)
        .join(User, User.id == Job.employer_id)
        .filter(Job.status.in_(["open", "applied"] if ESKI_AKIS else ["open"]))
        .filter(User.banned.is_not(True))  # askıya alınmış işverenin ilanı listelenmez
        .order_by(Job.created_at.desc())
        .limit(80)
        .all()
    )
    return [job_out(j, db) for j in rows]


@app.get("/v1/jobs/mine")
def my_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.active_role == "employer":
        rows = db.query(Job).filter(Job.employer_id == user.id, Job.status != "closed").order_by(Job.created_at.desc()).all()
    else:
        rows = (
            db.query(Job)
            .filter((Job.courier_id == user.id) | (Job.applicant_id == user.id))
            .order_by(Job.created_at.desc())
            .all()
        )
    return [job_out(j, db) for j in rows]


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "İlan yok")
    # Paket listesi sadece işveren, başvuran veya kabul edilen kurye görür
    detail = user.id in (job.employer_id, job.courier_id, job.applicant_id) or job.status == "open"
    show_pkgs = user.id in (job.employer_id, job.courier_id) and job.status in ("accepted", "departed", "done")
    if user.id == job.employer_id:
        show_pkgs = True
    out = job_out(job, db, include_packages=show_pkgs)
    if not detail:
        out["note"] = ""
    return out


@app.post("/v1/jobs/{job_id}/apply")
def apply_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    old_flow_only()
    if user.active_role == "sponsor":
        raise HTTPException(403, "Sponsor hesabı ilana başvuramaz")
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "İlan yok")
    if job.status not in ("open", "applied"):
        raise HTTPException(400, "Bu ilan artık başvuruya kapalı")
    job.applicant_id = user.id
    job.status = "applied"
    user.active_role = "courier"
    db.commit()
    return job_out(job, db)


@app.post("/v1/jobs/{job_id}/accept")
def accept_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    old_flow_only()
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "İlan yok")
    if job.employer_id != user.id:
        raise HTTPException(403, "Sadece işveren kabul edebilir")
    if not job.applicant_id:
        raise HTTPException(400, "Henüz başvuran kurye yok")
    job.courier_id = job.applicant_id
    job.status = "accepted"
    job.accepted_at = datetime.now(timezone.utc)
    sort_packages(list(job.packages), job.employer_lat, job.employer_lon)
    db.commit()
    return job_out(job, db, include_packages=True)


@app.post("/v1/jobs/{job_id}/depart")
def depart_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Kurye yola çıktı — SMS bir sonraki adım; şimdilik pin linkleri döner."""
    old_flow_only()
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "İlan yok")
    if job.courier_id != user.id and job.employer_id != user.id:
        raise HTTPException(403, "Bu iş sana ait değil")
    if job.status not in ("accepted", "departed"):
        raise HTTPException(400, "Önce işveren kabul etmeli")
    job.status = "departed"
    job.departed_at = datetime.now(timezone.utc)
    db.commit()
    base = PUBLIC_BASE
    links = [pkg_out(p, base) for p in sort_packages(list(job.packages), job.employer_lat, job.employer_lon)]
    return {"job": job_out(job, db, include_packages=True), "smsReady": False, "links": links}


@app.get("/v1/me/pin-updates")
def pin_updates(since: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Kuryenin aktif işlerindeki alıcı pin düzeltmeleri."""
    q = (
        db.query(JobPackage)
        .join(Job, Job.id == JobPackage.job_id)
        .filter(Job.courier_id == user.id, JobPackage.pin_updated.is_(True))
    )
    rows = q.order_by(JobPackage.pin_updated_at.desc()).limit(50).all()
    if since:
        try:
            t = datetime.fromisoformat(since.replace("Z", "+00:00"))
            rows = [p for p in rows if p.pin_updated_at and p.pin_updated_at > t]
        except Exception:
            pass
    return [
        {
            "packageId": p.id,
            "jobId": p.job_id,
            "customer": p.customer,
            "phone": p.phone,
            "lat": p.lat,
            "lon": p.lon,
            "pinUpdatedAt": p.pin_updated_at.isoformat() if p.pin_updated_at else None,
        }
        for p in rows
    ]


@app.post("/v1/pin/{token}")
def save_pin(token: str, body: PinIn, db: Session = Depends(get_db)):
    pkg = db.query(JobPackage).filter(JobPackage.token == token).first()
    if not pkg:
        raise HTTPException(404, "Link geçersiz")
    try:
        phone = norm_phone(body.phone)
    except HTTPException:
        raise HTTPException(400, "Telefonu paket üzerindekiyle aynı yaz")
    if phone != pkg.phone:
        raise HTTPException(403, "Telefon numarası bu paketle eşleşmiyor")
    pkg.lat = body.lat
    pkg.lon = body.lon
    pkg.precision = "alici pin"
    pkg.pin_updated = True
    pkg.pin_updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "customer": pkg.customer}


PIN_HTML = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1"/>
<title>Rota — Konum doğrula</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  body{margin:0;font-family:system-ui,sans-serif;background:#070F1C;color:#EAF2FB}
  #map{height:58vh}
  .box{padding:16px}
  h1{font-size:20px;margin:0 0 8px}
  p{color:#7C93B3;font-size:14px}
  input{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #234;background:#132844;color:#fff;font-size:16px}
  button{width:100%;margin-top:10px;padding:14px;border:0;border-radius:12px;background:#00E5D4;color:#04211E;font-weight:800;font-size:16px}
  .msg{margin-top:10px;font-weight:700}
</style>
</head>
<body>
<div id="map"></div>
<div class="box">
  <h1>Evinizin pinini doğrulayın</h1>
  <p>Pakette yazan telefon numaranızı girin, pini kapıya kaydırın, kaydedin. Bu sayfa sadece numara ile konumu bağlar.</p>
  <input id="phone" inputmode="tel" placeholder="05xxxxxxxxx"/>
  <button id="save">Konumu kaydet</button>
  <div class="msg" id="msg"></div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const TOKEN = location.pathname.split('/').pop();
const start = [36.55, 32.00];
const map = L.map('map').setView(start, 16);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 20, attribution: '© OSM'}).addTo(map);
const marker = L.marker(start, {draggable: true}).addTo(map);
if (navigator.geolocation) {
  navigator.geolocation.getCurrentPosition((p) => {
    const ll = [p.coords.latitude, p.coords.longitude];
    marker.setLatLng(ll); map.setView(ll, 18);
  });
}
document.getElementById('save').onclick = async () => {
  const phone = document.getElementById('phone').value;
  const ll = marker.getLatLng();
  const msg = document.getElementById('msg');
  msg.textContent = 'Kaydediliyor…';
  try {
    const r = await fetch('/v1/pin/' + TOKEN, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({phone, lat: ll.lat, lon: ll.lng})
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || 'Hata');
    msg.textContent = 'Kaydedildi. Kurye yeni konumu alacak.';
  } catch (e) { msg.textContent = e.message; }
};
</script>
</body>
</html>
"""


@app.get("/p/{token}", response_class=HTMLResponse)
def pin_page(token: str, db: Session = Depends(get_db)):
    pkg = db.query(JobPackage).filter(JobPackage.token == token).first()
    if not pkg:
        return HTMLResponse("<h1>Link geçersiz</h1>", status_code=404)
    return HTMLResponse(PIN_HTML)
