"""Kurye ve işletme telefonuna bildirim (1.19.0; kullanıcı isteği 29 Ekim 2026: "kurye ve işletme telefonuna bildirim (uygulamaya push)").

WhatsApp ile otomatik bildirim ÜCRETLİ (Meta sistem bildirimlerini mesaj başı ücretlendiriyor), bu yüzden ÜCRETSİZ yol seçildi:
- **Telefon (uygulama):** Expo bildirim servisi (ücretsiz) → Android'de Google FCM, iPhone'da Apple APNs. Sunucuda anahtar YOK: uygulama
  izin alınca Expo jetonunu (ExponentPushToken[…]) gönderir; FCM / APNs kimlik bilgileri EAS'ta (expo.dev) durur.
- **Web ikizi (bilgisayar):** tarayıcı bildirimi (Web Push, webpush.py; içerik şifreli).

Kurallar:
- Bildirim METNİ KİŞİSEL BİLGİ İÇERMEZ (ad, adres, telefon, mesaj içeriği yok): "📩 Yeni mesajınız var" gibi. Ayrıntı, uygulama açılınca bizim
  sunucumuzdan gelir.
- Tercihler (Hesabım › Bildirimler; sunucuda `account_docs.notif`): "Yeni ilan" (kurye), "Yeni mesaj / adres listesi", "Teslimat
  güncellemeleri"; sessiz saatler, hafta sonu, resmî tatil, seçilen gün (Türkiye saati). Sistem duyuruları (ödül, coin, yönetim) her zaman
  gider (sessiz zaman hariç). Askıya alınmış hesaba bildirim gitmez.
- Aynı kaynaktan art arda gelenler tek bildirim: aynı kişiden mesaj 60 sn, yeni ilan 10 dk, teslim haberi 2 dk, duyuru 30 sn içinde bir kez.
- Cihaz kaydı mesaj cihazına (relay deviceId) bağlıdır: çıkışta (/v1/devices/forget) silinir. Hesap başına en çok 10 cihaz.
- Gönderim isteği BEKLETMEZ: veritabanı kaydı tamamlanınca (commit) arka planda gider. Servis "cihaz yok" derse (Expo DeviceNotRegistered,
  tarayıcı 404 / 410) kayıt silinir.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import event
from sqlalchemy.orm import Session, object_session

from . import events, webpush
from .i18n import REQ_LANG, en
from .auth import current_user
from .db import SessionLocal, get_db
from .limits import Window
from .models import AccountDoc, Notice, User, UserPush, now, uid

EXPO_SEND_URL = "https://exp.host/--/api/v2/push/send"
EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
EXPO_TOKEN_RE = re.compile(r"^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_\-]{8,200}\]$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_DEVICES = 10
TR = timezone(timedelta(hours=3))  # Türkiye saati (2016'dan beri yaz-kış aynı)

# ---------------------------------------------------------------------------------------------------- metinler (KİŞİSEL BİLGİ YOK)
# tür: (kategori, metin, art arda gelenleri birleştirme süresi sn)
#   kategori → tercih: msg = "Yeni mesaj / adres listesi", job = "Yeni ilan", delivery = "Teslimat güncellemeleri", notice / test = her zaman
KINDS: dict[str, tuple[str, str, int]] = {
    "msg": ("msg", "📩 Yeni mesajınız var", 60),
    "nudge": ("msg", "👋 Haritada biri size selam gönderdi", 600),
    "task_shared": ("msg", "📋 Size yeni bir adres listesi paylaşıldı", 60),
    "task_accepted": ("msg", "✅ Kurye adres listesini kabul etti", 60),
    "task_declined": ("msg", "Kurye adres listesini kabul etmedi", 60),
    "follow_ask": ("msg", "📍 Canlı takip isteği geldi", 60),
    "follow_ok": ("msg", "📍 Kurye canlı takibi kabul etti", 60),
    "job_new": ("job", "📢 Çalışma bölgende yeni ilan var", 600),
    "recipient_ok": ("delivery", "✅ Alıcı “Paketimi aldım” dedi", 30),
    "recipient_pin": ("delivery", "📍 Alıcı kapının yerini işaretledi", 120),
    "delivered": ("delivery", "✅ Bir paket teslim edildi", 120),
    "notice": ("notice", "🔔 Yeni bir bildiriminiz var", 30),
    # 1.25.0 (kullanıcı isteği 6 Kasım 2026: "sponsorun telefonuna bildirim gitmeli"): ödül duyuruları kendi metniyle (kişisel bilgi yok)
    "reward_won": ("notice", "🎁 Bir kurye ödül ürününüzü hak etti — gelince barkodunu okutun", 30),
    "reward_given": ("notice", "🎁 Barkodun okutuldu — ürünü aldıysan “Teslim aldım” de", 30),
    # 1.26.0: reklam / tabela bitişine 1 gün kala (reklam adı yazmaz; dokununca Reklamlarım)
    "ad_ending": ("notice", "🪧 Tabelan yarın bitiyor — Reklamlarım'dan tek dokunuşla uzatabilirsin", 30),  # 1.27.0: tek ürün tabela
    "test": ("test", "🔔 Bildirimler açık. Bu bir deneme bildirimidir.", 0),
}
TITLE = "Rota"
# Tıklanınca uygulamada açılacak yer (istemci karar verir; burada yalnız tür gider)
GROUP_OF = {"msg": "msg", "nudge": "msg", "task_shared": "msg", "task_accepted": "msg", "task_declined": "msg", "follow_ask": "msg",
            "follow_ok": "msg", "job_new": "job", "recipient_ok": "delivery", "recipient_pin": "delivery", "delivered": "delivery",
            "notice": "notice", "reward_won": "notice", "reward_given": "notice", "ad_ending": "notice", "test": "test"}
# Duyuru türü → bildirim türü (yoksa genel "🔔 Yeni bir bildiriminiz var"). Dokununca yine duyuru gibi açılır (t = "notice").
NOTICE_PUSH = {"reward.claimed": "reward_won", "reward.given": "reward_given", "ad.ending": "ad_ending"}
# Uygulamada t'nin yanında k'ye (alt tür) göre AYRI ekran açan bildirimler (web ikizinin adresine de &k= yazılır)
SUB_ROUTED = {"ad_ending"}

# ---------------------------------------------------------------------------------------------------- resmî tatiller (Türkiye)
# Sabit günler her yıl; dini bayramlar Diyanet takvimine göre yıl yıl (arife: öğleden sonra, 13.00'ten).
# TODO: 2028 ve sonrasının dini bayram tarihleri açıklanınca eklenmeli (eklenmezse yalnız sabit günler sessiz olur).
FIXED_HOLIDAYS = {(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)}
FIXED_HALF = {(10, 28)}  # Cumhuriyet Bayramı arifesi, öğleden sonra
RELIGIOUS = {
    2026: {"full": ["2026-03-20", "2026-03-21", "2026-03-22", "2026-05-27", "2026-05-28", "2026-05-29", "2026-05-30"],
           "half": ["2026-03-19", "2026-05-26"]},
    2027: {"full": ["2027-03-09", "2027-03-10", "2027-03-11", "2027-05-16", "2027-05-17", "2027-05-18", "2027-05-19"],
           "half": ["2027-03-08", "2027-05-15"]},
}


def is_holiday(t: datetime) -> bool:
    """`t` Türkiye saatinde mi resmî tatilde (arifelerde 13.00'ten sonra)?"""
    d: date = t.date()
    if (d.month, d.day) in FIXED_HOLIDAYS:
        return True
    rel = RELIGIOUS.get(d.year, {})
    iso = d.isoformat()
    if iso in rel.get("full", []):
        return True
    half = (d.month, d.day) in FIXED_HALF or iso in rel.get("half", [])
    return half and t.hour >= 13


def _mins(s: str) -> int | None:
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", s or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def quiet_now(prefs: dict, at: datetime | None = None) -> bool:
    """Kullanıcının sessiz zamanı mı (Türkiye saati)? Sessiz saatler gece yarısını aşabilir (23:00 → 07:00)."""
    t = (at or now()).astimezone(TR)
    if prefs.get("quietWeekends") and t.weekday() >= 5:
        return True
    qd = prefs.get("quietDay")
    if isinstance(qd, int) and 0 <= qd <= 6 and t.weekday() == qd:
        return True
    if prefs.get("quietHolidays") and is_holiday(t):
        return True
    if prefs.get("quietOn"):
        a, b, m = _mins(prefs.get("quietFrom", "")), _mins(prefs.get("quietTo", "")), t.hour * 60 + t.minute
        if a is not None and b is not None and a != b:
            return (a <= m < b) if a < b else (m >= a or m < b)
    return False


def allowed(prefs: dict, category: str, at: datetime | None = None) -> bool:
    """Tercihe göre bu bildirim gitsin mi? (test her zaman; duyuru yalnız sessiz zamanda gitmez)"""
    if category == "test":
        return True
    if quiet_now(prefs, at):
        return False
    if category == "msg":
        return bool(prefs.get("accepted", True))
    if category == "job":
        return bool(prefs.get("newJob", True))
    if category == "delivery":
        return bool(prefs.get("delivery", True))
    return True


def prefs_of(db: Session, user_id: str) -> dict:
    from .account import clean_section  # döngüsel içe aktarma olmasın

    row = db.get(AccountDoc, (user_id, "notif"))
    raw = {}
    if row is not None:
        try:
            raw = json.loads(row.data or "{}")
        except ValueError:
            raw = {}
    return clean_section("notif", raw)


# ---------------------------------------------------------------------------------------------------- kuyruk (commit'ten SONRA)
def queue(db: Session, user_id: str | None, kind: str, thread: str = "") -> None:
    """Bu isteğin veritabanı kaydı tamamlanınca (commit) `user_id`ye bildirim gitsin. Geri alınırsa (rollback) gitmez."""
    if not user_id or kind not in KINDS:
        return
    db.info.setdefault("push_queue", []).append((user_id, kind, thread))


@event.listens_for(SessionLocal, "after_commit")
def _after_commit(session):
    q = session.info.pop("push_queue", None)
    if q:
        dispatch(q)


@event.listens_for(SessionLocal, "after_rollback")
def _after_rollback(session):
    session.info.pop("push_queue", None)


@event.listens_for(Notice, "after_insert")
def _notice_added(_mapper, _conn, target):
    """Her sistem duyurusu (🔔: ödül, coin, reklam, yönetim) telefona da bildirim olarak gider."""
    s = object_session(target)
    if s is not None:
        queue(s, target.user_id, NOTICE_PUSH.get(target.kind or "", "notice"), "notice")


# ---------------------------------------------------------------------------------------------------- gönderim
_last: dict[tuple[str, str, str], float] = {}
_lock = threading.Lock()
SYNC = [False]  # testlerde True: istek içinde sırayla gönderilir
_receipts: list[tuple[str, str, float]] = []  # (bilet, jeton, zaman) — 15 dk sonra Expo'ya "ulaştı mı" diye sorulur


def _post_json(url: str, payload, timeout: float = 10.0) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    tok = os.getenv("EXPO_ACCESS_TOKEN", "").strip()  # isteğe bağlı: Expo'da "gelişmiş güvenlik" açıldıysa
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")
        except ValueError:
            return {"errors": [{"code": str(e.code)}]}
    except Exception as e:  # ağ hatası: sessiz geç, sunucu düşmesin
        print("[bildirim] Expo'ya ulaşılamadı:", type(e).__name__, flush=True)
        return {}


# Testler bunları değiştirir (gerçek ağa çıkmadan yakalar)
EXPO_TRANSPORT: list[Callable[[str, object], dict]] = [lambda url, payload: _post_json(url, payload)]


def _coalesce(user_id: str, kind: str, thread: str, window: int) -> bool:
    """True: gönder; False: aynı kaynaktan az önce gitti (birleştir)."""
    if window <= 0:
        return True
    key = (user_id, GROUP_OF.get(kind, kind), thread)
    t = time.time()
    with _lock:
        if t - _last.get(key, 0.0) < window:
            return False
        _last[key] = t
        if len(_last) > 20000:  # bellek sınırı
            for k in [k for k, v in _last.items() if t - v > 3600]:
                _last.pop(k, None)
    return True


def reset_memory() -> None:
    """Testler: birleştirme belleğini sıfırla."""
    with _lock:
        _last.clear()
    _receipts.clear()


def _forget(ids: list[str]) -> None:
    if not ids:
        return
    db = SessionLocal()
    try:
        db.query(UserPush).filter(UserPush.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _send_batch(items: list[tuple[str, str, str]]) -> None:
    """items: (kullanıcı, tür, iş parçacığı). Tercihleri, sessiz saatleri, birleştirmeyi uygular; Expo ve tarayıcıya gönderir."""
    db = SessionLocal()
    gone: list[str] = []
    expo_msgs: list[dict] = []
    expo_rows: list[UserPush] = []
    web_jobs: list[tuple[UserPush, dict]] = []
    try:
        seen: set[tuple[str, str, str]] = set()
        for user_id, kind, thread in items:
            if (user_id, kind, thread) in seen:
                continue
            seen.add((user_id, kind, thread))
            category, text, window = KINDS[kind]
            u = db.get(User, user_id)
            if u is None or u.banned:
                continue
            rows = db.query(UserPush).filter(UserPush.user_id == user_id).all()
            if not rows:
                continue
            if not allowed(prefs_of(db, user_id), category):
                continue
            if not _coalesce(user_id, kind, thread, window):
                continue
            data = {"t": GROUP_OF.get(kind, kind), "k": kind}
            for row in rows:
                body = en(text) if (getattr(row, "lang", "") or "tr") == "en" else text  # 1.30.0: cihazın dilinde
                if row.kind == "expo":
                    expo_msgs.append({"to": row.token, "title": TITLE, "body": body, "data": data, "sound": "default", "priority": "high",
                                      "channelId": "default", "ttl": 6 * 3600})
                    expo_rows.append(row)
                elif row.kind == "web":
                    payload = {"title": TITLE, "body": body, "url": "/web/?push=" + data["t"], "tag": "rota-" + data["t"], "t": data["t"]}
                    if kind in SUB_ROUTED:  # 1.26.0: dokununca kendi ekranı açılsın ("Reklamın yarın bitiyor" → Reklamlarım)
                        payload["url"] += "&k=" + kind
                        payload["k"] = kind
                    web_jobs.append((row, payload))
        # Expo: en çok 100'lük paketler; bilet hatası "DeviceNotRegistered" ise kayıt silinir
        for i in range(0, len(expo_msgs), 100):
            chunk, rows = expo_msgs[i:i + 100], expo_rows[i:i + 100]
            res = EXPO_TRANSPORT[0](EXPO_SEND_URL, chunk) or {}
            tickets = res.get("data") if isinstance(res.get("data"), list) else []
            for row, tk in zip(rows, tickets):
                if not isinstance(tk, dict):
                    continue
                if tk.get("status") == "error" and (tk.get("details") or {}).get("error") == "DeviceNotRegistered":
                    gone.append(row.id)
                elif tk.get("status") == "ok" and tk.get("id"):
                    _receipts.append((str(tk["id"]), row.token, time.time()))
            if len(_receipts) > 5000:
                del _receipts[: len(_receipts) - 5000]
        # Web ikizi (tarayıcı): içerik şifreli; 404 / 410 → kayıt silinir
        for row, payload in web_jobs:
            try:
                st = webpush.send_one(row.token, row.p256dh, row.auth, payload)
            except Exception as e:
                print("[bildirim] tarayıcıya gönderilemedi:", type(e).__name__, flush=True)
                st = 0
            if st in (404, 410):
                gone.append(row.id)
    finally:
        db.close()
    _forget(gone)


def dispatch(items: list[tuple[str, str, str]]) -> None:
    if not items:
        return
    if SYNC[0]:
        _send_batch(items)
        return

    def run():
        try:
            _send_batch(items)
        except Exception as e:
            print("[bildirim] gönderim hatası:", type(e).__name__, flush=True)

    threading.Thread(target=run, name="push-send", daemon=True).start()


def check_receipts(max_age: float = 15 * 60) -> int:
    """Expo bilet sonuçları: 15 dk'yı geçen biletleri sorar; "DeviceNotRegistered" olan jetonların kaydı silinir. Silinen sayısı."""
    t = time.time()
    due = [x for x in _receipts if t - x[2] >= max_age]
    if not due:
        return 0
    keep = [x for x in _receipts if t - x[2] < max_age]
    _receipts[:] = keep
    tok_of = {tid: tok for tid, tok, _ in due}
    bad_tokens: set[str] = set()
    ids = list(tok_of)
    for i in range(0, len(ids), 300):
        res = EXPO_TRANSPORT[0](EXPO_RECEIPTS_URL, {"ids": ids[i:i + 300]}) or {}
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        for tid, rc in data.items():
            if isinstance(rc, dict) and rc.get("status") == "error" and (rc.get("details") or {}).get("error") == "DeviceNotRegistered":
                if tid in tok_of:
                    bad_tokens.add(tok_of[tid])
    if not bad_tokens:
        return 0
    db = SessionLocal()
    try:
        n = db.query(UserPush).filter(UserPush.kind == "expo", UserPush.token.in_(list(bad_tokens))).delete(synchronize_session=False)
        db.commit()
        return n
    finally:
        db.close()


# ---------------------------------------------------------------------------------------------------- yeni ilan: bölgedeki kuryeler
def _fold(s: str) -> str:
    s = (s or "").strip().replace("İ", "i").replace("I", "ı").lower()
    for a, b in (("ç", "c"), ("ğ", "g"), ("ı", "i"), ("ö", "o"), ("ş", "s"), ("ü", "u"), ("â", "a"), ("î", "i"), ("û", "u")):
        s = s.replace(a, b)
    s = re.sub(r"\b(mahallesi|mah\.?|mh\.?)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _area(raw: str | None) -> tuple[str, str, list[str]]:
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    hoods = d.get("h") if isinstance(d.get("h"), list) else []
    return _fold(str(d.get("p") or "")), _fold(str(d.get("d") or "")), [_fold(str(h)) for h in hoods if h]


def queue_job_area(db: Session, employer: User, job_area: str, limit: int = 200) -> int:
    """Yeni ilan: işverenin çalışma bölgesiyle (il + ilçe) aynı bölgedeki kuryelere; kurye mahalle seçtiyse ilanın mahallesi de tutmalı.
    İşverenin bölgesi yoksa kimseye gitmez. Kaç kuryeye sıraya alındığını döner."""
    p, d, _ = _area(employer.work_area)
    if not p or not d:
        return 0
    want = _fold(job_area)
    n = 0
    rows = (
        db.query(User.id, User.work_area)
        .filter(User.active_role == "courier", User.banned.is_not(True), User.work_area.is_not(None), User.id != employer.id)
        .limit(5000)
        .all()
    )
    for cid, wa in rows:
        cp, cd, hoods = _area(wa)
        if cp != p or cd != d:
            continue
        if hoods and want and want not in hoods:
            continue
        queue(db, cid, "job_new", "job")
        n += 1
        if n >= limit:
            break
    return n


# ---------------------------------------------------------------------------------------------------- uçlar
class PushKeysIn(BaseModel):
    p256dh: str = Field(default="", max_length=128)
    auth: str = Field(default="", max_length=48)


class PushRegIn(BaseModel):
    kind: str = Field(pattern="^(expo|web)$")
    token: str = Field(default="", max_length=1024)  # expo: ExponentPushToken[…] · web: tarayıcının bildirim adresi
    keys: PushKeysIn = Field(default_factory=PushKeysIn)  # yalnız web
    platform: str = Field(default="", max_length=16)
    device_id: str = Field(default="", alias="deviceId", max_length=64)

    model_config = {"populate_by_name": True}


class PushOffIn(BaseModel):
    token: str = Field(default="", max_length=1024)
    device_id: str = Field(default="", alias="deviceId", max_length=64)

    model_config = {"populate_by_name": True}


def drop_device(db: Session, user_id: str, device_id: str) -> int:
    """Çıkışta (mesaj cihazı unutulurken) o cihazın bildirim kaydı da silinir. Commit çağırana ait."""
    if not device_id:
        return 0
    return db.query(UserPush).filter(UserPush.user_id == user_id, UserPush.device_id == device_id).delete(synchronize_session=False)


def drop_user(db: Session, user_id: str, keep_device: str = "") -> int:
    """1.20.0: hesabın bütün oturumları kapanınca (şifre değişti / yönetim kapattı / askı) bildirim kayıtları da silinir: çıkış yapmış
    cihaza "Yeni mesajınız var" gitmesin. Açık kalan cihaz bir sonraki açılışta kendiliğinden yeniden kaydolur. Commit çağırana ait."""
    q = db.query(UserPush).filter(UserPush.user_id == user_id)
    if keep_device:
        q = q.filter(UserPush.device_id != keep_device)
    return q.delete(synchronize_session=False)


def make_router() -> APIRouter:
    r = APIRouter()
    reg_win = Window(60, 3600)  # kullanıcı başı saatte 60 kayıt
    test_win = Window(5, 3600)  # saatte 5 deneme bildirimi

    @r.get("/v1/push/key")
    def web_key():
        """Web ikizinin tarayıcı bildirimi için sunucu kimliği (VAPID açık anahtarı)."""
        return {"key": webpush.public_key()}

    @r.post("/v1/me/push")
    def register(b: PushRegIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Bu cihaza bildirim gelsin: Expo jetonu (telefon) ya da tarayıcı aboneliği (web ikizi)."""
        if not reg_win.allow(user.id):
            raise HTTPException(429, "Çok sık deneme, biraz bekleyin")
        tok = b.token.strip()
        dev = b.device_id.strip()
        if dev and not DEVICE_RE.fullmatch(dev):
            raise HTTPException(400, "Cihaz kimliği geçersiz")
        if b.kind == "expo":
            if not EXPO_TOKEN_RE.fullmatch(tok):
                raise HTTPException(400, "Bildirim jetonu geçersiz")
            p256dh = auth = ""
        else:
            p256dh, auth = b.keys.p256dh.strip(), b.keys.auth.strip()
            if not webpush.endpoint_ok(tok) or not webpush.keys_ok(p256dh, auth):
                raise HTTPException(400, "Bu tarayıcının bildirim adresi kabul edilmedi")
        row = db.query(UserPush).filter(UserPush.token == tok).first()
        if row is None:
            row = UserPush(id=uid(), user_id=user.id, kind=b.kind, token=tok)
            db.add(row)
            events.log(db, "push.on", user.id, row.id, tur=b.kind)
        elif row.user_id != user.id:  # aynı cihazda başka hesaba girildi: bildirim yeni hesaba gelir
            row.user_id = user.id
        row.kind, row.p256dh, row.auth = b.kind, p256dh, auth
        row.platform = (b.platform or ("web" if b.kind == "web" else ""))[:16]
        row.device_id = dev or row.device_id or ""
        row.lang = REQ_LANG.get()  # 1.30.0: uygulamanın dili (X-Lang); dil değişince uygulama yeniden kaydeder
        row.seen_at = now()
        db.flush()
        # Aynı cihazın eski jetonu (jeton yenilendi) ve en eski fazlalıklar silinir
        if dev:
            db.query(UserPush).filter(UserPush.user_id == user.id, UserPush.device_id == dev, UserPush.id != row.id).delete(synchronize_session=False)
        mine = db.query(UserPush).filter(UserPush.user_id == user.id).order_by(UserPush.seen_at.desc()).all()
        for x in mine[MAX_DEVICES:]:
            db.delete(x)
        db.commit()
        return {"ok": True, "devices": min(len(mine), MAX_DEVICES)}

    @r.post("/v1/me/push/off")
    def unregister(b: PushOffIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Bu cihazda bildirimi kapat (jetonla ya da cihaz kimliğiyle)."""
        q = db.query(UserPush).filter(UserPush.user_id == user.id)
        tok, dev = b.token.strip(), b.device_id.strip()
        if tok:
            n = q.filter(UserPush.token == tok).delete(synchronize_session=False)
        elif dev:
            n = q.filter(UserPush.device_id == dev).delete(synchronize_session=False)
        else:
            raise HTTPException(400, "Jeton ya da cihaz kimliği gerekli")
        db.commit()
        return {"ok": True, "removed": n}

    @r.post("/v1/me/push/test")
    def test(user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Kendi cihazlarına deneme bildirimi (kurulumu denemek için; saatte en çok 5)."""
        n = db.query(UserPush).filter(UserPush.user_id == user.id).count()
        if not n:
            raise HTTPException(409, "Bu hesapta bildirim açık cihaz yok. Önce bildirimlere izin ver.")
        if not test_win.allow(user.id):
            raise HTTPException(429, "Saatte en çok 5 deneme bildirimi gönderilebilir")
        queue(db, user.id, "test", str(time.time()))
        db.commit()
        return {"ok": True, "devices": n}

    def loop():
        while True:
            time.sleep(300)
            try:
                check_receipts()
            except Exception as e:
                print("[bildirim] bilet denetimi hatası:", type(e).__name__, flush=True)

    def start_cleaner():
        threading.Thread(target=loop, name="push-receipts", daemon=True).start()

    r.limiters = (reg_win, test_win)  # type: ignore[attr-defined]
    r.start_cleaner = start_cleaner  # type: ignore[attr-defined]
    return r
