"""1.20.0 — HESAP GÜVENLİĞİ (kullanıcı isteği 29 Ekim 2026: "hesap güvenliğini … yap"; iş listesi 7. madde).

1) GİRİŞ KİLİDİ: yanlış şifre sayacı hem girişte yazılan bilgiye (telefon / kullanıcı adı / e-posta; kayıtlı olmasa da) hem hesabın
   kendisine bağlıdır. 5 yanlış → 15 dk, 10 → 1 saat, 15 → 6 saat, 20 ve sonrası → 24 saat kilit. Kilitliyken DOĞRU şifre de kabul
   edilmez (deneme sürmesin). Doğru girişte ve kodla şifre yenileyince sayaç sıfırlanır; yönetim "Kilidi aç" der. Aynı ağdan (IP) 15
   dakikada 50 yanlış deneme de durdurulur. İlk kilitte hesabın sahibine 🔔 "Hesabına çok sayıda yanlış şifre denendi" gider.
2) ŞİFREMİ UNUTTUM: 6 haneli kod, 15 dakika geçerli, en çok 5 deneme. Kod DOĞRULANMIŞ e-postaya gider; yoksa telefona SMS. SMS / e-posta
   servisi bağlı değilken (deneme modu) kod kişiye ulaşmaz, YÖNETİM PANELİNDE görünür: yönetim kişinin kimliğini doğrulayıp kodu söyler.
   Kodla şifre yenilenince bütün cihazlardaki oturumlar kapanır, kişi bu cihazda girmiş olur.
3) E-POSTA / TELEFON DOĞRULAMA ve TELEFON DEĞİŞTİRME (yeni numaraya kod). Doğrulanmış e-posta girişte de kullanılır.
4) TÜM CİHAZLARDAN ÇIKIŞ (bu cihaz hariç).
5) HESAP SİLME (uygulamadan ve web sayfasından: /hesap-sil — Google Play'in istediği "uygulamayı kurmadan silme" yolu). Hesap satırı
   yabancı anahtarlar bozulmasın diye kalır ama kişisel bilgiler silinir; aşağıda `delete_account`. Kara listedeki hesap silinirse
   numaranın geri çevrilemez özeti kalır: aynı numarayla açılan yeni hesap kara listeyi devralır.
Kodun kendisi veritabanında saklanmaz (HMAC özeti); yalnız kişiye ulaşmamış kod yönetim için düz metin durur ve kullanılınca / süresi
dolunca silinir. Her yönetici bakışı işlem günlüğüne yazılır.
"""
import hashlib
import hmac
import math
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import events, notify
from .admin import audit, current_admin, mask_email, mask_phone
from .auth import SECRET, AccountBlocked, DUMMY_HASH, check_pw, current_user, hash_pw, is_phone_like, norm_phone, user_token
from .db import get_db
from .limits import Window, client_ip
from .models import (
    AccountDoc, AccountState, AdBooking, AdSeen, AdStat, AuthCode, AuthThrottle, CoinEntry, CourierProfile, DeliveryProof, Device, DoorReport, Follow,
    SurveyAnswer,
    FollowPoint, GuideBackup, Handoff, HandoffPush, Job, JobPackage, MapBlock, Notice, Nudge, PointEntry, RelayDelivery, RelayMsg, Review,
    Reward, RewardClaim, Task, User, UserMark, UserPush, now, uid,
)

# ------------------------------------------------------------------------------------------------ ayarlar (Render ortamından değişebilir)
LOCK_STEPS = [(5, 15), (10, 60), (15, 360), (20, 1440)]  # (yanlış sayısı, kilit dakikası); 20'den sonra her 5 yanlışta 24 saat
FAIL_WINDOW_H = 24  # sayaç bu kadar saat yanlış girilmezse sıfırlanır
IP_FAIL_MAX = int(os.getenv("LOGIN_IP_FAIL_MAX", "50"))  # aynı ağdan 15 dakikada en çok yanlış deneme
CODE_TTL_MIN = {"reset": 15, "email": 30, "phone": 10, "phone_change": 10}
CODE_TRIES = 5  # bir koda en çok yanlış deneme
RESEND_SEC = 60  # aynı iş için yeni kod en sık bu kadar saniyede bir
CODES_PER_HOUR = 5  # aynı iş için saatte en çok kod
SMS_DAILY_MAX = int(os.getenv("SMS_DAILY_MAX", "300"))  # SMS ücretli olunca: günlük toplam SMS tavanı (fatura kabarmasın)
SMS_PER_PHONE_DAY = 5  # bir numaraya günde en çok doğrulama SMS'i
PURPOSE_TR = {"reset": "Şifremi unuttum", "email": "E-posta doğrulama", "phone": "Telefon doğrulama", "phone_change": "Telefon değiştirme"}
DELETED_NAME = "Silinmiş üye"

ip_fail = Window(IP_FAIL_MAX, 900)
forgot_ip = Window(20, 3600)  # aynı ağdan saatte en çok 20 "şifremi unuttum"
verify_fail = Window(10, 3600)  # hesap başı saatte en çok 10 yanlış kod
delete_fail = Window(8, 900)  # hesap silerken yanlış şifre (hesap başı)
sms_phone = Window(SMS_PER_PHONE_DAY, 86400)
limiters = [ip_fail, forgot_ip, verify_fail, delete_fail, sms_phone]
_sms_day = {"day": "", "n": 0}
_sms_lock = threading.Lock()
TR = timezone(timedelta(hours=3))


def reset_limits(db: Session | None = None) -> None:
    """Testler için: bellek içi sayaçlar ve (verilirse) veritabanındaki kilitler sıfırlanır."""
    for w in limiters:
        w.reset()
    with _sms_lock:
        _sms_day.update(day="", n=0)
    if db is not None:
        db.query(AuthThrottle).delete(synchronize_session=False)
        db.commit()


def _utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _h(s: str) -> str:
    return hmac.new(SECRET.encode(), s.encode(), hashlib.sha256).hexdigest()


def phone_digest(phone: str) -> str:
    """Kara listedeki silinen hesabın numarasının geri çevrilemez özeti (numara tutulmaz)."""
    return _h("phone:" + phone)


def human_wait(seconds: float) -> str:
    m = max(1, math.ceil(seconds / 60))
    if m < 60:
        return f"{m} dakika"
    return f"{math.ceil(m / 60)} saat"


class LoginLocked(HTTPException):
    """Çok fazla yanlış şifre: 429 + `code: locked` + `retryAfter` (sn). Uygulama "Şifremi unuttum"u öne çıkarır."""

    code = "locked"

    def __init__(self, seconds: float, ip: bool = False):
        self.retry_after = int(max(1, seconds))
        wait = human_wait(seconds)
        if ip:
            msg = f"Bu ağdan çok fazla yanlış deneme yapıldı. {wait} sonra tekrar dene."
        else:
            msg = f"Çok fazla yanlış şifre denendi. Güvenliğin için giriş {wait} kapalı. Şifreni unuttuysan “Şifremi unuttum”u kullan."
        super().__init__(429, msg)


# ------------------------------------------------------------------------------------------------ giriş bilgisi → hesap
def ident_of(raw: str) -> tuple[str, str]:
    """Girişte yazılan bilgi: ('phone' | 'email' | 'username', düzgün biçim). Telefon gibi görünen ama hatalı bilgi 400 döner."""
    s = (raw or "").strip()
    if "@" in s:
        return "email", s.lower()[:254]
    if is_phone_like(s):
        return "phone", norm_phone(s)
    return "username", s.lower()[:64]


def find_user(db: Session, kind: str, norm: str) -> User | None:
    """Hesabı bulur. E-postayla yalnız DOĞRULANMIŞ e-posta bulunur. Silinmiş hesap bulunmaz."""
    q = db.query(User).filter(User.deleted_at.is_(None))
    if kind == "phone":
        return q.filter(User.phone == norm).first()
    if kind == "email":
        return q.filter(User.email == norm, User.email_verified_at.is_not(None)).order_by(User.email_verified_at.asc()).first()
    return q.filter(User.username == norm).first()


def _id_key(kind: str, norm: str) -> str:
    return "id:" + _h(f"login:{kind}:{norm}")[:40]


def login_keys(kind: str, norm: str, u: User | None) -> list[str]:
    keys = [_id_key(kind, norm)]
    if u is not None:
        keys.append("u:" + u.id)
    return keys


def keys_of_user(u: User) -> list[str]:
    """Bir hesabın bütün kilit anahtarları (telefon, kullanıcı adı, e-posta, hesap)."""
    keys = ["u:" + u.id, _id_key("phone", u.phone)]
    if u.username:
        keys.append(_id_key("username", u.username))
    if u.email:
        keys.append(_id_key("email", u.email))
    return keys


def lock_minutes(fails: int) -> int:
    if fails >= 20:
        return 1440 if (fails - 20) % 5 == 0 else 0
    for n, m in LOCK_STEPS:
        if fails == n:
            return m
    return 0


def lock_state(db: Session, keys: list[str]) -> tuple[datetime | None, int]:
    """(en geç biten kilit, en yüksek yanlış sayısı)"""
    rows = db.query(AuthThrottle).filter(AuthThrottle.key.in_(keys)).all() if keys else []
    t = now()
    until = None
    fails = 0
    for r in rows:
        lu = _utc(r.locked_until)
        if lu and lu > t and (until is None or lu > until):
            until = lu
        if t - _utc(r.first_at or t) <= timedelta(hours=FAIL_WINDOW_H):
            fails = max(fails, r.fails or 0)
    return until, fails


def ensure_login_open(db: Session, keys: list[str], request: Request | None) -> None:
    ip = client_ip(request) if request else ""
    if ip and ip_fail.count(ip) >= ip_fail.max_hits:
        raise LoginLocked(900, ip=True)
    until, _ = lock_state(db, keys)
    if until:
        raise LoginLocked((until - now()).total_seconds())


def note_login_fail(db: Session, keys: list[str], request: Request | None, u: User | None) -> None:
    """Yanlış şifre: sayaçlar artar; eşiğe gelince kilitlenir (ilk kilitte hesabın sahibine 🔔). Commit eder."""
    if request is not None:
        ip_fail.allow(client_ip(request))
    t = now()
    locked_now = 0
    first_lock = False
    for k in keys:
        r = db.get(AuthThrottle, k)
        if r is None:
            r = AuthThrottle(key=k, user_id=u.id if (u is not None and k.startswith("u:")) else None, fails=0, first_at=t)
            db.add(r)
        elif t - _utc(r.first_at or t) > timedelta(hours=FAIL_WINDOW_H):
            r.fails, r.first_at, r.locked_until = 0, t, None
        r.fails = (r.fails or 0) + 1
        r.last_at = t
        m = lock_minutes(r.fails)
        if m:
            r.locked_until = t + timedelta(minutes=m)
            locked_now = max(locked_now, m)
            if r.fails == LOCK_STEPS[0][0] and k.startswith("u:"):
                first_lock = True
    if locked_now and u is not None:
        events.log(db, "user.locked", u.id, min=locked_now)
        if first_lock:
            from .points import notify as add_notice  # döngüsel içe aktarma olmasın

            add_notice(db, u.id, "security", "🔐 Hesabına çok sayıda yanlış şifre denendi; giriş bir süre kapatıldı. Sen değilsen şifreni değiştir (Hesabım › Giriş ve Güvenlik).")
    db.commit()


def note_login_ok(db: Session, keys: list[str]) -> None:
    if keys:
        db.query(AuthThrottle).filter(AuthThrottle.key.in_(keys)).delete(synchronize_session=False)


def unlock_user(db: Session, u: User) -> int:
    return db.query(AuthThrottle).filter(AuthThrottle.key.in_(keys_of_user(u))).delete(synchronize_session=False)


# ------------------------------------------------------------------------------------------------ doğrulama kodları
def _code_hash(purpose: str, user_id: str, code: str) -> str:
    return _h(f"code:{purpose}:{user_id}:{code}")


def _sms_budget() -> bool:
    """Günlük SMS tavanı (Türkiye günü). Aşılırsa kod SMS'le gitmez, yönetim panelinde görünür."""
    day = now().astimezone(TR).date().isoformat()
    with _sms_lock:
        if _sms_day["day"] != day:
            _sms_day.update(day=day, n=0)
        if _sms_day["n"] >= SMS_DAILY_MAX:
            return False
        _sms_day["n"] += 1
        return True


def _rate(db: Session, user: User, purpose: str) -> None:
    """Aynı iş için yeni kod en sık 60 sn'de bir, saatte en çok 5."""
    t = now()
    last = db.query(AuthCode).filter(AuthCode.user_id == user.id, AuthCode.purpose == purpose).order_by(AuthCode.created_at.desc()).first()
    if last is not None:
        wait = RESEND_SEC - (t - _utc(last.created_at)).total_seconds()
        if wait > 0:
            raise HTTPException(429, f"Yeni kod için {math.ceil(wait)} sn bekle.")
    n = db.query(func.count(AuthCode.id)).filter(AuthCode.user_id == user.id, AuthCode.purpose == purpose, AuthCode.created_at >= t - timedelta(hours=1)).scalar() or 0
    if n >= CODES_PER_HOUR:
        raise HTTPException(429, "Bu iş için çok fazla kod istendi. Bir saat sonra tekrar dene.")


CODE_TEXT = {
    "reset": ("Rota şifre yenileme kodun", "Rota şifre yenileme kodun: {code}\n\nKod {min} dakika geçerli. Bu isteği sen yapmadıysan bu e-postayı yok say; şifren değişmez.\nKodu kimseyle paylaşma: Rota çalışanları kodunu sormaz."),
    "email": ("Rota e-posta doğrulama kodun", "Rota e-posta doğrulama kodun: {code}\n\nKod {min} dakika geçerli. Uygulamada Hesabım › Giriş ve Güvenlik'e yaz.\nBu isteği sen yapmadıysan bu e-postayı yok say."),
}
SMS_TEXT = "Rota doğrulama kodun: {code} ({min} dk geçerli). Kimseyle paylaşma."


def issue_code(db: Session, user: User, purpose: str, channel: str, target: str, request: Request | None) -> dict:
    """Yeni kod üretir ve gönderir (önceki kullanılmamış kod geçersiz olur). Gönderilemezse kod yönetim panelinde görünür. Commit eder."""
    _rate(db, user, purpose)
    t = now()
    db.query(AuthCode).filter(AuthCode.user_id == user.id, AuthCode.purpose == purpose, AuthCode.used_at.is_(None)).update(
        {AuthCode.used_at: t, AuthCode.code_plain: None}, synchronize_session=False)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    ttl = CODE_TTL_MIN[purpose]
    if channel == "email":
        provider = notify.email_enabled()
        subj, body = CODE_TEXT.get(purpose, CODE_TEXT["email"])
        ok = bool(provider and notify.send_email(target, subj, body.format(code=code, min=ttl)))
    else:
        provider = notify.sms_enabled()
        ok = False
        if provider and sms_phone.allow(target) and _sms_budget():
            ok = bool(notify.send_sms(target, SMS_TEXT.format(code=code, min=ttl)))
    row = AuthCode(
        id=uid(), user_id=user.id, purpose=purpose, channel=channel, target=target, code_hash=_code_hash(purpose, user.id, code),
        code_plain=None if ok else code, delivered=ok, provider=bool(provider), ip=(client_ip(request) if request else "")[:64],
        created_at=t, expires_at=t + timedelta(minutes=ttl),
    )
    db.add(row)
    db.commit()
    return {
        "ok": True, "channel": channel, "hint": mask_email(target) if channel == "email" else mask_phone(target),
        "delivered": ok, "provider": bool(provider), "expiresIn": ttl * 60, "resendIn": RESEND_SEC,
    }


def check_code(db: Session, user: User, purpose: str, code: str, target: str | None = None) -> AuthCode:
    """Kodu doğrular ve kullanılmış sayar (commit ÇAĞIRANA ait; yanlış kodda deneme sayısı yazılıp 400 döner)."""
    if verify_fail.count(user.id) >= verify_fail.max_hits:
        raise HTTPException(429, "Çok fazla yanlış kod denendi. Bir saat sonra tekrar dene.")
    c = (code or "").strip().replace(" ", "")
    row = (
        db.query(AuthCode)
        .filter(AuthCode.user_id == user.id, AuthCode.purpose == purpose, AuthCode.used_at.is_(None))
        .order_by(AuthCode.created_at.desc())
        .first()
    )
    t = now()
    if row is None or _utc(row.expires_at) < t or (target is not None and row.target != target):
        raise HTTPException(400, "Kodun süresi doldu ya da kod yok. Yeni kod iste.")
    if (row.attempts or 0) >= CODE_TRIES:
        raise HTTPException(400, "Bu koda çok fazla yanlış deneme yapıldı. Yeni kod iste.")
    if len(c) != 6 or not c.isdigit() or not hmac.compare_digest(row.code_hash, _code_hash(purpose, user.id, c)):
        row.attempts = (row.attempts or 0) + 1
        verify_fail.allow(user.id)
        left = CODE_TRIES - row.attempts
        if left <= 0:
            row.code_plain = None
        db.commit()
        raise HTTPException(400, f"Kod yanlış. {left} hakkın kaldı." if left > 0 else "Kod yanlış. Bu koda çok fazla yanlış deneme yapıldı; yeni kod iste.")
    row.used_at = t
    row.code_plain = None
    return row


# ------------------------------------------------------------------------------------------------ oturumlar
def drop_sessions(db: Session, u: User, keep_device: str = "") -> None:
    """Bütün oturumlar kapanır (oturum sürümü artar); bu cihaz hariç diğer cihazlara bildirim ve mesaj gitmez. Commit ETMEZ."""
    u.token_version = (u.token_version or 0) + 1
    q = db.query(UserPush).filter(UserPush.user_id == u.id)
    if keep_device:
        q = q.filter(UserPush.device_id != keep_device)
    q.delete(synchronize_session=False)
    if keep_device:
        # Diğer cihazların mesaj kaydı da kalkar (oturumları kapandı; yeniden girince yeniden kaydolurlar)
        others = [d for (d,) in db.query(Device.id).filter(Device.user_id == u.id, Device.id != keep_device).all()]
        if others:
            db.query(RelayDelivery).filter(RelayDelivery.user_id == u.id, RelayDelivery.device_id.in_(others)).delete(synchronize_session=False)
            db.query(Device).filter(Device.id.in_(others)).delete(synchronize_session=False)


# ------------------------------------------------------------------------------------------------ hesap silme
def _scrub_claim(c: RewardClaim, status: str | None = None, reason: str = "") -> None:
    if status:
        c.status = status
        c.close_reason = reason
        c.closed_at = now()
    c.ship_name, c.ship_phone, c.ship_address = "", "", ""


def delete_account(db: Session, u: User) -> dict:
    """Hesabı siler (commit ETMEZ). Kalan: hesap satırı (kişisel bilgisiz), rıza kayıtları (ne zaman neye onay verildiği; avukat
    kararına kadar), etkinlik akışı (60 gün) ve yönetim günlüğü, kara listedeyse yönetim notu, mağaza coin satın alma kaydı, ortak kapı
    haritasındaki oyları (kimseye bağlanamaz). Silinen her şeyin sayısı döner."""
    from .follow import end_follow
    from .points import add_entry, notify as add_notice
    from .tasks import end_task

    me = u.id
    n: dict[str, int] = {}
    # --- ödüller: koyduğu ödüller katalogdan iner; okutulmamış barkodlar (1.25.0; eskiden "kargo bekleyen") iptal + kuryeye puan iadesi
    n["rewards"] = db.query(Reward).filter(Reward.owner_id == me, Reward.status == "active").update({Reward.status: "removed"}, synchronize_session=False)
    for c in db.query(RewardClaim).filter(RewardClaim.owner_id == me, RewardClaim.status == "pending").all():
        _scrub_claim(c, "cancelled", "owner_deleted")
        add_entry(db, c.courier_id, c.cost, "refund", ref=c.id, note=f"İade: {c.reward_name} (ödülü koyan hesabını kapattı)")
        add_notice(db, c.courier_id, "reward.cancelled", f"“{c.reward_name}” verilemeyecek: ödülü koyan hesabını kapattı. Barkod iptal edildi ve {c.cost} puanın iade edildi.", ref=c.id)
    # kuryenin kendi talepleri: okutulmamış barkod iptal (stok geri), eski kayıtlarda kargo adresi de silinir
    for c in db.query(RewardClaim).filter(RewardClaim.courier_id == me).all():
        if c.status == "pending":
            _scrub_claim(c, "cancelled", "courier_deleted")
            rw = db.get(Reward, c.reward_id)
            if rw is not None:
                rw.qty_left += 1
            add_notice(db, c.owner_id, "reward.cancelled", f"Kurye hesabını kapattı; “{c.reward_name}” barkodu ({c.code}) iptal edildi, ürünü vermeniz gerekmiyor.", ref=c.id)
        else:
            _scrub_claim(c)
    # --- takip ve görevler (canlı konum, iz) — kayıtlar tamamen silinir
    fids = [f.id for f in db.query(Follow).filter((Follow.courier_id == me) | (Follow.employer_id == me)).all()]
    for f in db.query(Follow).filter(Follow.id.in_(fids), Follow.status.in_(("asked", "active"))).all() if fids else []:
        end_follow(db, f, "account_deleted", me)
    db.flush()  # bitiş güncellemesi silmeden ÖNCE yazılsın (oturum kendiliğinden yazmaz; sonra silinen satıra güncelleme gitmesin)
    if fids:
        db.query(FollowPoint).filter(FollowPoint.follow_id.in_(fids)).delete(synchronize_session=False)
        db.query(Follow).filter(Follow.id.in_(fids)).delete(synchronize_session=False)
    n["follows"] = len(fids)
    tasks = db.query(Task).filter((Task.courier_id == me) | (Task.employer_id == me)).all()
    for t in tasks:
        end_task(db, t, "account_deleted", me)
    n["tasks"] = len(tasks)
    db.flush()
    # --- ilanlar: açıklar kapanır, metinleri silinir (satır kalır: yabancı anahtar)
    for j in db.query(Job).filter(Job.employer_id == me).all():
        if j.status != "closed":
            j.status = "closed"
        j.note = ""
        db.query(JobPackage).filter(JobPackage.job_id == j.id).delete(synchronize_session=False)
    db.query(Job).filter(Job.courier_id == me).update({Job.courier_id: None}, synchronize_session=False)
    db.query(Job).filter(Job.applicant_id == me).update({Job.applicant_id: None}, synchronize_session=False)
    # --- teslim bağlantıları (alıcı sayfaları)
    hids = [h.id for h in db.query(Handoff.id).filter((Handoff.courier_id == me) | (Handoff.employer_id == me)).all()]
    if hids:
        db.query(HandoffPush).filter(HandoffPush.handoff_id.in_(hids)).delete(synchronize_session=False)
        db.query(Review).filter(Review.handoff_id.in_(hids)).update({Review.handoff_id: None}, synchronize_session=False)
        db.query(Handoff).filter(Handoff.id.in_(hids)).delete(synchronize_session=False)
    n["handoffs"] = len(hids)
    # --- değerlendirmeler: hakkında yazılanlar silinir; yazdıkları yazarsız kalır (puanlanan kişinin ortalaması bozulmasın)
    n["reviews"] = db.query(Review).filter(Review.subject_id == me).delete(synchronize_session=False)
    db.query(Review).filter(Review.author_id == me).update({Review.author_id: None}, synchronize_session=False)
    # --- aktarıcı: gönderdiği ama henüz karşı tarafa ULAŞMAMIŞ mesajlar silinir (ulaşanlar karşı tarafın telefonunda kalır)
    mids = [m for (m,) in db.query(RelayMsg.id).filter(RelayMsg.sender_id == me).all()]
    if mids:
        db.query(RelayDelivery).filter(RelayDelivery.msg_id.in_(mids)).delete(synchronize_session=False)
        db.query(RelayMsg).filter(RelayMsg.id.in_(mids)).delete(synchronize_session=False)
    n["relay_msgs"] = len(mids)
    # --- ortak kapı haritası: oyları puanıyla kalır ama artık kimseye bağlanamaz (her oya ayrı rastgele kod; doors.anonymize_reporter gibi,
    #     yalnız commit etmeden)
    from .doors import reporter_code
    dr = db.query(DoorReport).filter(DoorReport.reporter == reporter_code(me)).all()
    for r in dr:
        r.reporter = "x" + secrets.token_hex(11)
    n["door_reports"] = len(dr)
    # --- 1.26.0: reklam istatistiği (reklamlarının sayıları + kuryenin "gördü" kayıtları) ve anket yanıtları (arşivde anonim kalır)
    my_ad_ids = [x for (x,) in db.query(AdBooking.id).filter(AdBooking.owner_id == me).all()]
    if my_ad_ids:
        db.query(AdStat).filter(AdStat.ad_id.in_(my_ad_ids)).delete(synchronize_session=False)
        db.query(AdSeen).filter(AdSeen.ad_id.in_(my_ad_ids)).delete(synchronize_session=False)
    db.query(AdSeen).filter(AdSeen.user_id == me).delete(synchronize_session=False)
    n["survey_answers"] = db.query(SurveyAnswer).filter(SurveyAnswer.user_id == me).update({SurveyAnswer.user_id: None}, synchronize_session=False)
    # --- hesabın kendi kayıtları
    for model, col in (
        (UserPush, UserPush.user_id), (RelayDelivery, RelayDelivery.user_id), (Device, Device.user_id), (Notice, Notice.user_id),
        (PointEntry, PointEntry.user_id), (DeliveryProof, DeliveryProof.courier_id), (AccountDoc, AccountDoc.user_id),
        (AccountState, AccountState.user_id), (GuideBackup, GuideBackup.user_id), (AdBooking, AdBooking.owner_id),
        (CourierProfile, CourierProfile.user_id), (AuthCode, AuthCode.user_id),
    ):
        n[model.__tablename__] = db.query(model).filter(col == me).delete(synchronize_session=False)
    # Coin: mağaza satın alması (ileride) muhasebe için kalır; test / harcama / düzeltme satırları silinir
    n["coin_entries"] = db.query(CoinEntry).filter(CoinEntry.user_id == me, CoinEntry.kind != "store").delete(synchronize_session=False)
    db.query(MapBlock).filter((MapBlock.user_id == me) | (MapBlock.blocked_id == me)).delete(synchronize_session=False)
    db.query(Nudge).filter((Nudge.from_id == me) | (Nudge.to_id == me)).delete(synchronize_session=False)
    db.query(AuthThrottle).filter(AuthThrottle.key.in_(keys_of_user(u))).delete(synchronize_session=False)
    mark = db.get(UserMark, me)
    blacklisted = bool(mark and mark.blacklisted)
    if mark is not None and not blacklisted:
        db.delete(mark)
    # --- hesap satırı: kişisel bilgi silinir
    role = u.active_role
    u.phone_hash = phone_digest(u.phone) if blacklisted else None
    u.phone = ("D" + me)[:16]  # gerçek numara biçiminde değil: kimseyle çakışmaz, girişte bulunmaz
    u.name = DELETED_NAME
    u.username = None
    u.email = None
    u.email_verified_at = None
    u.phone_verified_at = None
    u.password_hash = "silindi"  # pbkdf2 biçiminde değil: hiçbir şifre tutmaz
    u.must_change_password = False
    u.lat = u.lon = None
    u.visible_map = False
    u.vis_lat = u.vis_lon = None
    u.vis_at = None
    u.findable = False
    u.map_icon = None
    u.work_area = None
    u.logo = None
    u.logo_type = None
    u.logo_at = None
    u.token_version = (u.token_version or 0) + 1
    u.deleted_at = now()
    events.log(db, "user.deleted", me, role=role)
    return n


def inherit_blacklist(db: Session, new_user: User) -> bool:
    """Aynı numarayla daha önce silinmiş KARA LİSTEDEKİ hesap varsa yeni hesap da kara listeye girer (not: eski hesabın notu). Commit ETMEZ."""
    h = phone_digest(new_user.phone)
    old = db.query(User).filter(User.phone_hash == h, User.deleted_at.is_not(None)).all()
    for o in old:
        m = db.get(UserMark, o.id)
        if m is not None and m.blacklisted:
            from .marks import code_of

            t = now()
            note = f"Silinen hesap ({code_of(o)}) kara listedeydi. Eski not: {m.note}"[:500]
            nm = db.get(UserMark, new_user.id) or UserMark(user_id=new_user.id)
            nm.blacklisted, nm.note, nm.listed_at, nm.listed_by, nm.note_at, nm.note_by, nm.updated_at = True, note, t, "sistem", t, "sistem", t
            db.add(nm)
            events.log(db, "user.blacklist_inh", new_user.id, old=o.id)
            return True
    return False


# ------------------------------------------------------------------------------------------------ istek gövdeleri
class LoginIdIn(BaseModel):
    login: str = Field(min_length=1, max_length=254)


class ResetIn(BaseModel):
    login: str = Field(min_length=1, max_length=254)
    code: str = Field(min_length=1, max_length=12)
    new_password: str = Field(alias="newPassword", max_length=200)

    model_config = {"populate_by_name": True}


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=12)


class PhoneIn(BaseModel):
    phone: str = Field(min_length=1, max_length=40)


class PhoneCodeIn(BaseModel):
    phone: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=1, max_length=12)


class RevokeIn(BaseModel):
    device_id: str = Field(default="", alias="deviceId", max_length=64)

    model_config = {"populate_by_name": True}


class DeleteIn(BaseModel):
    password: str = Field(default="", max_length=200)
    confirm: bool = False


def _pw_ok(p: str) -> str:
    p = p or ""
    if len(p) < 6:
        raise HTTPException(400, "Yeni şifre en az 6 karakter olmalı")
    if len(p) > 128:
        raise HTTPException(400, "Yeni şifre en fazla 128 karakter olmalı")
    return p


def _mail_after(to: str, subject: str, text: str) -> None:
    """Bilgi e-postası (şifre değişti / hesap silindi): servis bağlıysa arka planda; yoksa hiç."""
    if not to or not notify.email_enabled():
        return
    threading.Thread(target=lambda: notify.send_email(to, subject, text), name="mail", daemon=True).start()


def code_row(c: AuthCode, names: dict[str, tuple[str, str, str]]) -> dict:
    nm, role, no = names.get(c.user_id, ("", "", ""))
    t = now()
    live = c.used_at is None and _utc(c.expires_at) > t
    return {
        "id": c.id, "at": _utc(c.created_at).isoformat() if c.created_at else None, "userId": c.user_id, "userName": nm, "role": role, "userNo": no,
        "purpose": c.purpose, "purposeLabel": PURPOSE_TR.get(c.purpose, c.purpose), "channel": c.channel,
        "target": mask_email(c.target) if c.channel == "email" else mask_phone(c.target), "delivered": bool(c.delivered), "provider": bool(c.provider),
        "expiresAt": _utc(c.expires_at).isoformat(), "usedAt": _utc(c.used_at).isoformat() if c.used_at else None, "attempts": c.attempts or 0,
        # live: geçerli · used: kullanıldı ya da yenisi istendi · expired: süresi doldu
        "status": "live" if live else ("used" if c.used_at else "expired"),
        "canReveal": bool(live and c.code_plain and (c.attempts or 0) < CODE_TRIES),
    }


def purge(db: Session) -> int:
    """Saatte bir: süresi dolan kodların düz metni, 2 günden eski kodlar ve eski kilit satırları silinir."""
    t = now()
    db.query(AuthCode).filter(AuthCode.code_plain.is_not(None), (AuthCode.expires_at < t) | AuthCode.used_at.is_not(None)).update(
        {AuthCode.code_plain: None}, synchronize_session=False)
    n = db.query(AuthCode).filter(AuthCode.created_at < t - timedelta(days=2)).delete(synchronize_session=False)
    n += db.query(AuthThrottle).filter(AuthThrottle.last_at < t - timedelta(days=2), (AuthThrottle.locked_until.is_(None)) | (AuthThrottle.locked_until < t)).delete(synchronize_session=False)
    db.commit()
    return n


# ------------------------------------------------------------------------------------------------ uçlar
def make_router(user_out) -> APIRouter:
    r = APIRouter()

    def by_login(db: Session, raw: str) -> User:
        kind, norm = ident_of(raw)
        u = find_user(db, kind, norm)
        if u is None:
            raise HTTPException(404, "Bu bilgiyle kayıtlı hesap bulunamadı. Telefonunu 05xx… biçiminde, kullanıcı adını ya da doğrulanmış e-postanı yaz.")
        return u

    @r.get("/v1/auth/caps")
    def caps():
        """Kodlar hangi yoldan gidebilir (gizli bir şey vermez). false: deneme modu, kod yönetim panelinde görünür."""
        return {"email": notify.email_enabled(), "sms": notify.sms_enabled()}

    @r.post("/v1/auth/forgot")
    def forgot(b: LoginIdIn, request: Request, db: Session = Depends(get_db)):
        """Şifremi unuttum: kodu doğrulanmış e-postaya (servis bağlıysa), yoksa telefona (SMS) gönderir. Hiçbiri bağlı değilse kod yönetim
        panelinde görünür (deneme modu / destek)."""
        ip = client_ip(request)
        if not forgot_ip.allow(ip):
            raise HTTPException(429, "Bu ağdan çok fazla istek geldi. Bir saat sonra tekrar dene.")
        u = by_login(db, b.login)
        email_ok = bool(u.email and u.email_verified_at)
        if email_ok and notify.email_enabled():
            channel, target = "email", u.email
        elif notify.sms_enabled():
            channel, target = "sms", u.phone
        elif email_ok:
            channel, target = "email", u.email
        else:
            channel, target = "sms", u.phone
        out = issue_code(db, u, "reset", channel, target, request)
        events.log(db, "user.forgot", u.id, ch=channel, ok=int(out["delivered"]))
        db.commit()
        return out

    @r.post("/v1/auth/reset")
    def reset(b: ResetIn, request: Request, db: Session = Depends(get_db)):
        """Kodla yeni şifre: bütün cihazlardaki oturumlar kapanır, giriş kilidi açılır, kişi bu cihazda girmiş olur."""
        u = by_login(db, b.login)
        pw = _pw_ok(b.new_password)
        row = check_code(db, u, "reset", b.code)
        if u.banned:  # kod doğru: şifreyi değiştirmeden askı nedenini söyle
            db.commit()
            raise AccountBlocked(u.banned_reason or "")
        u.password_hash = hash_pw(pw)
        u.must_change_password = False
        if row.channel == "sms" and row.delivered and row.target == u.phone:
            u.phone_verified_at = u.phone_verified_at or now()  # kod telefona ulaştı: numara kişinin
        drop_sessions(db, u)
        unlock_user(db, u)
        u.last_login_at = now()
        events.log(db, "user.pwreset", u.id, ch=row.channel)
        mail = u.email if u.email_verified_at else ""
        db.commit()
        db.refresh(u)
        _mail_after(mail, "Rota şifren değiştirildi", "Rota hesabının şifresi az önce doğrulama koduyla değiştirildi ve bütün cihazlardaki oturumlar kapatıldı.\n\nBunu sen yapmadıysan hemen “Şifremi unuttum” ile yeni şifre belirle ve destekle iletişime geç.")
        return {"token": user_token(u), "user": user_out(u)}

    # --- e-posta
    @r.post("/v1/me/email/send")
    def email_send(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not user.email:
            raise HTTPException(400, "Önce Profil'den e-posta adresini yaz.")
        if user.email_verified_at:
            return {"ok": True, "verified": True}
        return issue_code(db, user, "email", "email", user.email, request)

    @r.post("/v1/me/email/verify")
    def email_verify(b: CodeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not user.email:
            raise HTTPException(400, "Önce Profil'den e-posta adresini yaz.")
        check_code(db, user, "email", b.code, target=user.email)
        other = db.query(User.id).filter(User.email == user.email, User.email_verified_at.is_not(None), User.id != user.id, User.deleted_at.is_(None)).first()
        if other is not None:
            db.commit()
            raise HTTPException(409, "Bu e-posta başka bir hesapta doğrulanmış. Başka bir adres yaz.")
        user.email_verified_at = now()
        events.log(db, "user.email_ok", user.id)
        db.commit()
        db.refresh(user)
        return user_out(user)

    # --- telefon
    @r.post("/v1/me/phone/send")
    def phone_send(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if user.phone_verified_at:
            return {"ok": True, "verified": True}
        return issue_code(db, user, "phone", "sms", user.phone, request)

    @r.post("/v1/me/phone/verify")
    def phone_verify(b: CodeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        check_code(db, user, "phone", b.code, target=user.phone)
        user.phone_verified_at = now()
        events.log(db, "user.phone_ok", user.id)
        db.commit()
        db.refresh(user)
        return user_out(user)

    @r.post("/v1/me/phone/change")
    def phone_change(b: PhoneIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Telefon değiştirme: kod YENİ numaraya gider. Numara kodla doğrulanmadan değişmez (hesabı kaçırmaya açık olmasın)."""
        ph = norm_phone(b.phone)
        if ph == user.phone:
            raise HTTPException(400, "Bu zaten senin numaran.")
        if not ph.startswith("05"):
            raise HTTPException(400, "Kod SMS ile gideceği için cep telefonu (05xx…) yaz.")
        if db.query(User.id).filter(User.phone == ph, User.id != user.id).first() is not None:
            raise HTTPException(409, "Bu telefon başka bir hesapta kayıtlı.")
        return issue_code(db, user, "phone_change", "sms", ph, request)

    @r.post("/v1/me/phone/change/verify")
    def phone_change_verify(b: PhoneCodeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        ph = norm_phone(b.phone)
        check_code(db, user, "phone_change", b.code, target=ph)
        if db.query(User.id).filter(User.phone == ph, User.id != user.id).first() is not None:
            db.commit()
            raise HTTPException(409, "Bu telefon başka bir hesapta kayıtlı.")
        old = user.phone
        unlock_user(db, user)  # eski numaranın kilit satırı kalmasın
        user.phone = ph
        user.phone_verified_at = now()
        events.log(db, "user.phone_new", user.id)
        mail = user.email if user.email_verified_at else ""
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Bu telefon başka bir hesapta kayıtlı.")
        db.refresh(user)
        _mail_after(mail, "Rota telefon numaran değişti", f"Rota hesabının telefon numarası {mask_phone(old)} → {mask_phone(ph)} olarak değiştirildi.\n\nBunu sen yapmadıysan hemen destekle iletişime geç.")
        return user_out(user)

    # --- oturumlar
    @r.post("/v1/me/sessions/revoke")
    def revoke(b: RevokeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Diğer bütün cihazlardan çıkış: bu cihaz yeni oturum anahtarıyla devam eder."""
        drop_sessions(db, user, keep_device=b.device_id or "")
        events.log(db, "user.sessions_out", user.id)
        db.commit()
        db.refresh(user)
        return {"token": user_token(user), "user": user_out(user)}

    # --- hesap silme
    @r.post("/v1/me/delete")
    def delete_me(b: DeleteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Hesabı KALICI siler (geri alınamaz). Şifre ve onay ister."""
        if delete_fail.count(user.id) >= delete_fail.max_hits:
            raise HTTPException(429, "Çok fazla yanlış şifre. Biraz bekle.")
        if not check_pw(b.password or "", user.password_hash or DUMMY_HASH):
            delete_fail.allow(user.id)
            raise HTTPException(400, "Şifre yanlış.")
        if not b.confirm:
            raise HTTPException(400, "Silmeyi onayla.")
        mail = user.email if user.email_verified_at else ""
        counts = delete_account(db, user)
        db.commit()
        _mail_after(mail, "Rota hesabın silindi", "Rota hesabın ve sunucudaki kişisel bilgilerin silindi. Telefonundaki adres listeleri, notlar ve yazışmalar telefonunda kalır; uygulamayı kaldırınca onlar da silinir.\n\nBunu sen yapmadıysan destekle iletişime geç.")
        return {"ok": True, "removed": {k: v for k, v in counts.items() if v}}

    # --- yönetim
    def names(db: Session, ids: set[str]) -> dict[str, tuple[str, str, str]]:
        ids = {i for i in ids if i}
        if not ids:
            return {}
        return {i: (nm, rl, no or "") for i, nm, rl, no in db.query(User.id, User.name, User.active_role, User.public_no).filter(User.id.in_(ids)).all()}

    @r.get("/v1/admin/codes")
    def admin_codes(hours: float = Query(default=24, ge=1, le=48), user_id: str = Query(default="", max_length=32),
                    a=Depends(current_admin), db: Session = Depends(get_db)):
        """Son kodlar (deneme modunda / gönderilemediğinde kişiye ulaşmayan kodu yönetim söyler). Kodun kendisi burada YOK: "Kodu göster"."""
        q = db.query(AuthCode).filter(AuthCode.created_at >= now() - timedelta(hours=hours))
        if user_id:
            q = q.filter(AuthCode.user_id == user_id)
        rows = q.order_by(AuthCode.created_at.desc()).limit(200).all()
        nm = names(db, {c.user_id for c in rows})
        return {"items": [code_row(c, nm) for c in rows], "caps": {"email": notify.email_enabled(), "sms": notify.sms_enabled()}}

    @r.post("/v1/admin/codes/{cid}/reveal")
    def admin_reveal(cid: str, request: Request, a=Depends(current_admin), db: Session = Depends(get_db)):
        c = db.get(AuthCode, cid)
        if c is None:
            raise HTTPException(404, "Kod bulunamadı")
        t = now()
        if c.used_at is not None or _utc(c.expires_at) < t or not c.code_plain or (c.attempts or 0) >= CODE_TRIES:
            raise HTTPException(409, "Bu kod artık geçerli değil (kullanıldı, süresi doldu ya da kişiye ulaştı). Kişi yeni kod istemeli.")
        audit(db, request, a, "code_reveal", "user", c.user_id, f"{PURPOSE_TR.get(c.purpose, c.purpose)} · {c.channel} · {mask_email(c.target) if c.channel == 'email' else mask_phone(c.target)}")
        db.commit()
        return {"code": c.code_plain, "expiresAt": _utc(c.expires_at).isoformat()}

    @r.get("/v1/admin/users/{uid_}/security")
    def admin_user_security(uid_: str, a=Depends(current_admin), db: Session = Depends(get_db)):
        u = db.get(User, uid_)
        if u is None:
            raise HTTPException(404, "Üye bulunamadı")
        until, fails = lock_state(db, keys_of_user(u)) if not u.deleted_at else (None, 0)
        rows = db.query(AuthCode).filter(AuthCode.user_id == u.id, AuthCode.created_at >= now() - timedelta(hours=24)).order_by(AuthCode.created_at.desc()).limit(20).all()
        nm = names(db, {u.id})
        return {
            "emailVerifiedAt": _utc(u.email_verified_at).isoformat() if u.email_verified_at else None,
            "phoneVerifiedAt": _utc(u.phone_verified_at).isoformat() if u.phone_verified_at else None,
            "deletedAt": _utc(u.deleted_at).isoformat() if u.deleted_at else None,
            "lock": {"locked": until is not None, "until": until.isoformat() if until else None, "fails": fails},
            "codes": [code_row(c, nm) for c in rows],
            "caps": {"email": notify.email_enabled(), "sms": notify.sms_enabled()},
        }

    @r.post("/v1/admin/users/{uid_}/unlock")
    def admin_unlock(uid_: str, request: Request, a=Depends(current_admin), db: Session = Depends(get_db)):
        u = db.get(User, uid_)
        if u is None:
            raise HTTPException(404, "Üye bulunamadı")
        n = unlock_user(db, u)
        audit(db, request, a, "user_unlock", "user", u.id, f"{n} kilit satırı")
        db.commit()
        return {"ok": True, "cleared": n}

    r.purge = purge  # type: ignore[attr-defined]
    return r
