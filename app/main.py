import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import (
    DUMMY_HASH,
    check_pw,
    current_user,
    hash_pw,
    is_phone_like,
    make_token,
    norm_email,
    norm_phone,
    norm_username,
)
from .db import Base, engine, get_db
from .geo import sort_packages
from .migrate import ensure_schema
from .models import ConsentLog, Job, JobPackage, User, now, uid

Base.metadata.create_all(bind=engine)
# create_all var olan tabloya sütun eklemez: eski veritabanına yeni sütunlar burada eklenir
for _msg in ensure_schema():
    print("[şema]", _msg, flush=True)

ROLES = ("courier", "employer", "sponsor")

app = FastAPI(title="Rota Asistan", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_BASE = os.getenv("PUBLIC_BASE", "").rstrip("/")


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
    lat: float
    lon: float


class PkgIn(BaseModel):
    customer: str
    phone: str
    address: str
    neighborhood: str = ""
    street: str = ""
    door_number: str = ""
    apartment: str = ""
    floor: str = ""
    lat: float
    lon: float
    precision: str = ""


class JobIn(BaseModel):
    title: str = "Teslimat işi"
    note: str = ""
    lat: float
    lon: float
    packages: list[PkgIn] = Field(default_factory=list)


class ConsentIn(BaseModel):
    """Açık rıza ekranındaki seçimler. Zamanı sunucu kendi saatiyle yazar; istemci saati yalnız bilgi içindir."""

    version: int = Field(ge=0, le=1000)
    voice: bool = False
    ocr: bool = False
    grup_konum: bool = Field(default=False, alias="grupKonum")
    aydinlatma_at: int = Field(default=0, ge=0, le=4102444800000, alias="aydinlatmaAt")
    acik_riza_at: int = Field(default=0, ge=0, le=4102444800000, alias="acikRizaAt")

    model_config = {"populate_by_name": True}


class PinIn(BaseModel):
    phone: str
    lat: float
    lon: float


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
    }


def consent_out(c: ConsentLog | None) -> dict:
    if c is None:  # hiç rıza kaydı yok
        return {"version": 0, "voice": False, "ocr": False, "grupKonum": False, "aydinlatmaAt": 0, "acikRizaAt": 0, "recordedAt": None}
    return {
        "version": c.version,
        "voice": bool(c.voice),
        "ocr": bool(c.ocr),
        "grupKonum": bool(c.grup_konum),
        "aydinlatmaAt": c.aydinlatma_ms or 0,
        "acikRizaAt": c.acik_riza_ms or 0,
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
    return {
        "id": j.id,
        "title": j.title,
        "note": j.note,
        "status": j.status,
        "employerLat": j.employer_lat,
        "employerLon": j.employer_lon,
        "packageCount": len(j.packages),
        "createdAt": j.created_at.isoformat() if j.created_at else None,
        "employer": {"id": employer.id, "name": employer.name, "phone": employer.phone} if employer else None,
        "applicant": {"id": applicant.id, "name": applicant.name, "phone": applicant.phone} if applicant else None,
        "courier": {"id": courier.id, "name": courier.name, "phone": courier.phone} if courier else None,
        "packages": [pkg_out(p, base) for p in pkgs] if include_packages else None,
    }


@app.get("/")
def root():
    return {"status": "ok", "service": "rota-asistan"}


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
        phone=phone,
        password_hash=hash_pw(body.password),
        name=name or phone,
        active_role=role,
        username=username or None,
        email=email or None,
    )
    db.add(u)
    try:
        db.commit()
    except IntegrityError:  # aynı anda başka biri aynı telefonu / kullanıcı adını aldı
        db.rollback()
        raise HTTPException(400, "Bu telefon ya da kullanıcı adı zaten kayıtlı")
    db.refresh(u)
    return {"token": make_token(u.id), "user": user_out(u)}


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
    return {"token": make_token(u.id), "user": user_out(u)}


@app.get("/v1/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


@app.post("/v1/me/role")
def set_role(body: RoleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(400, "Rol kurye, işveren veya sponsor olmalı")
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
    same = (
        last is not None
        and last.version == body.version
        and bool(last.voice) == body.voice
        and bool(last.ocr) == body.ocr
        and bool(last.grup_konum) == body.grup_konum
        and (last.acik_riza_ms or 0) == body.acik_riza_at
    )
    if same:
        return consent_out(last)
    row = ConsentLog(
        user_id=user.id,
        version=body.version,
        voice=body.voice,
        ocr=body.ocr,
        grup_konum=body.grup_konum,
        aydinlatma_ms=body.aydinlatma_at,
        acik_riza_ms=body.acik_riza_at,
    )
    db.add(row)
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
    if not body.packages:
        raise HTTPException(400, "İlanda en az bir paket olmalı")
    rows = []
    for p in body.packages:
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
        employer_id=user.id,
        title=(body.title or "Teslimat işi").strip(),
        note=body.note or "",
        employer_lat=body.lat,
        employer_lon=body.lon,
        status="open",
        packages=rows,
    )
    user.active_role = "employer"
    user.lat = body.lat
    user.lon = body.lon
    db.add(job)
    db.commit()
    db.refresh(job)
    return job_out(job, db, include_packages=True)


@app.get("/v1/jobs/open")
def open_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Job)
        .filter(Job.status.in_(["open", "applied"]))
        .order_by(Job.created_at.desc())
        .limit(80)
        .all()
    )
    return [job_out(j, db) for j in rows]


@app.get("/v1/jobs/mine")
def my_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.active_role == "employer":
        rows = db.query(Job).filter(Job.employer_id == user.id).order_by(Job.created_at.desc()).all()
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
