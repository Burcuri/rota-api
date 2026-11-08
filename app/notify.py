"""SMS + e-posta gönderme katmanı (alıcıya teslim bağlantısı; 1.20.0'dan beri doğrulama kodları da).

Varsayılan: TEST MODU. Hiçbir şey gönderilmez; kurye bağlantıyı uygulamada görür ve kendi SMS / WhatsApp uygulamasıyla iletir;
doğrulama kodları yönetim panelinde görünür (app/security.py).
- E-posta, iki yoldan biri (anahtarlar yalnız ortam değişkeninde durur; kodda / zipte / depoda yazılmaz):
  1) BREVO (1.20.0; ÜCRETSİZ plan günde 300 e-posta, kart istemez): Render ortamına BREVO_API_KEY ve MAIL_FROM (Brevo'da doğrulanmış
     gönderen adresi; isteğe bağlı MAIL_FROM_NAME) yazılır. HTTPS ile gider — Render'ın ÜCRETSİZ planı SMTP kapılarını (25 / 465 / 587)
     26 Eylül 2025'ten beri kapattığı için ücretsiz planda çalışan yol budur.
  2) SMTP: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASS, SMTP_FROM (yalnız Render'ın ÜCRETLİ planında çalışır).
- SMS (1.27.0; kullanıcı: "kod olarak yapman gerekeni yap sonra sms olarak yerli bir şirketle anlaşalım"): üç yerli sağlayıcının
  bağdaştırıcısı HAZIR — Netgsm, İletimerkezi, Verimor. Hesap açılıp gönderici başlığı onaylanınca YALNIZ Render ortamına yazılır:
    SMS_PROVIDER = netgsm | iletimerkezi | verimor
    SMS_HEADER   = onaylı gönderici başlığı (ör. ROTA ASISTAN; sağlayıcı panelindeki gibi, en çok 11 karakter)
    netgsm:        SMS_USER (abone no / kullanıcı kodu), SMS_PASS (API yetkili alt kullanıcının şifresi)
    iletimerkezi:  SMS_API_KEY, SMS_API_HASH (panel › Ayarlar › Güvenlik › API Erişimi; hash panelden kopyalanır, hesaplanmaz)
    verimor:       SMS_USER (API kullanıcı adı, 90850… gibi), SMS_PASS
  Biri eksikse sms_enabled() False döner (deneme modu sürer: kodlar yönetim › Kodlar'da). Bütün mesajlar BİLGİLENDİRME (doğrulama
  kodu, teslim bağlantısı): İYS sorgusu kapalı gönderilir (ticari ileti değildir). Yalnız cep numarasına (05…) gider.
  Sağlayıcı bağlanınca DOĞRULAMA KODLARI SMS'le gider. Teslim bağlantıları (her durak 1 SMS, metin uzun: 2 SMS kredisi) ayrıca
  SMS_LINKS=1 yazılırsa sunucudan gider; yazılmazsa kurye bugünkü gibi kendi telefonundan paylaşır. Günlük tavan: SMS_LINKS_DAILY_MAX (300).
Gönderim hatası kuryeyi bloklamaz: False döner, bağlantı kuryeye gösterilir.
"""
import base64
import html
import json
import os
import re
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Callable

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
NETGSM_URL = "https://api.netgsm.com.tr/sms/rest/v2/send"
ILETIMERKEZI_URL = "https://api.iletimerkezi.com/v1/send-sms/json"
VERIMOR_URL = "https://sms.verimor.com.tr/v2/send.json"


def _env(k: str) -> str:
    return os.getenv(k, "").strip()


def gsm10(phone: str) -> str:
    """Cep numarası 10 hane (5xxxxxxxxx); cep değilse ''. Sabit hatta SMS gitmez."""
    s = re.sub(r"\D", "", phone or "")
    if s.startswith("90") and len(s) == 12:
        s = s[2:]
    if s.startswith("0") and len(s) == 11:
        s = s[1:]
    return s if re.fullmatch(r"5\d{9}", s) else ""


def _post_json(url: str, body: dict, headers: dict | None = None, timeout: int = 12) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(65536).decode("utf-8", "replace")
        except Exception:
            raw = ""
        return int(e.code), raw


def _log_fail(name: str, status: int, code: str) -> None:
    # numara, metin ve anahtar günlüğe YAZILMAZ
    print(f"[bildirim] SMS ({name}) gönderilemedi: HTTP {status} · kod {code or '?'}", flush=True)


def _netgsm(to: str, text: str) -> bool:
    """Netgsm REST v2: Basic yetki (kullanıcı kodu:şifre), başarı kodu "00"."""
    no = gsm10(to)
    if not no:
        return False
    auth = base64.b64encode(f"{_env('SMS_USER')}:{_env('SMS_PASS')}".encode("utf-8")).decode("ascii")
    body = {"msgheader": _env("SMS_HEADER"), "encoding": "TR", "iysfilter": "0", "messages": [{"msg": text, "no": no}]}
    status, raw = _post_json(NETGSM_URL, body, {"Authorization": "Basic " + auth})
    try:
        code = str(json.loads(raw).get("code", ""))
    except Exception:
        code = ""
    ok = status == 200 and code == "00"
    if not ok:
        _log_fail("Netgsm", status, code)
    return ok


def _iletimerkezi(to: str, text: str) -> bool:
    """İletimerkezi v1 JSON: anahtar + panelin verdiği hash; başarı response.status.code "200"."""
    no = gsm10(to)
    if not no:
        return False
    body = {"request": {
        "authentication": {"key": _env("SMS_API_KEY"), "hash": _env("SMS_API_HASH")},
        "order": {"sender": _env("SMS_HEADER"), "iys": "0", "message": {"text": text, "receipents": {"number": [no]}}},
    }}
    status, raw = _post_json(ILETIMERKEZI_URL, body)
    try:
        code = str(json.loads(raw)["response"]["status"]["code"])
    except Exception:
        code = ""
    ok = status == 200 and code == "200"
    if not ok:
        _log_fail("İletimerkezi", status, code)
    return ok


def _verimor(to: str, text: str) -> bool:
    """Verimor v2 JSON: kullanıcı / şifre gövdede; 200 = kampanya numarası (düz metin)."""
    no = gsm10(to)
    if not no:
        return False
    body = {"username": _env("SMS_USER"), "password": _env("SMS_PASS"), "source_addr": _env("SMS_HEADER"), "datacoding": 1,
            "is_commercial": False, "messages": [{"msg": text, "dest": "90" + no}]}
    status, raw = _post_json(VERIMOR_URL, body)
    ok = status == 200 and raw.strip().isdigit()
    if not ok:
        _log_fail("Verimor", status, raw.strip()[:40] if status != 200 else "")
    return ok


# SMS sağlayıcı bağdaştırıcıları: ad -> (to, text) -> bool
PROVIDERS: dict[str, Callable[[str, str], bool]] = {"netgsm": _netgsm, "iletimerkezi": _iletimerkezi, "verimor": _verimor}
# Sağlayıcının çalışması için gereken ortam değişkenleri (biri eksikse SMS kapalı sayılır)
NEEDS: dict[str, tuple[str, ...]] = {
    "netgsm": ("SMS_HEADER", "SMS_USER", "SMS_PASS"),
    "iletimerkezi": ("SMS_HEADER", "SMS_API_KEY", "SMS_API_HASH"),
    "verimor": ("SMS_HEADER", "SMS_USER", "SMS_PASS"),
}


def sms_provider() -> str:
    """Kullanılabilir sağlayıcının adı ya da '' (anahtarlar eksik / tanımsız)."""
    name = _env("SMS_PROVIDER").lower()
    if name not in PROVIDERS:
        return ""
    return name if all(_env(k) for k in NEEDS.get(name, ())) else ""


def _on(k: str) -> bool:
    return _env(k).lower() in ("1", "true", "yes", "on", "evet", "açık")


SMS_LINKS_DAILY_MAX = int(os.getenv("SMS_LINKS_DAILY_MAX", "300") or "300")
_TR = timezone(timedelta(hours=3))
_budget_lock = threading.Lock()
_budget: dict = {"day": "", "n": {}}


def day_budget(kind: str, limit: int) -> bool:
    """Türkiye günü başına SMS tavanı (bellek içi; sunucu yeniden başlayınca sıfırlanır — asıl sınır sağlayıcıdaki ön ödemeli kredi)."""
    day = datetime.now(_TR).date().isoformat()
    with _budget_lock:
        if _budget["day"] != day:
            _budget.update(day=day, n={})
        n = _budget["n"].get(kind, 0)
        if n >= limit:
            return False
        _budget["n"][kind] = n + 1
        return True


def reset_budget() -> None:  # testler için
    with _budget_lock:
        _budget.update(day="", n={})


class _Default:
    """Gerçek ortam değişkenlerine bakan varsayılan arka uç. Testler `set_backend` ile sahtesini koyar."""

    def sms_enabled(self) -> bool:
        return bool(sms_provider())

    def sms_links_enabled(self) -> bool:
        return self.sms_enabled() and _on("SMS_LINKS")

    def email_enabled(self) -> bool:
        return self._brevo() or bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_FROM", "").strip())

    def _brevo(self) -> bool:
        return bool(os.getenv("BREVO_API_KEY", "").strip() and os.getenv("MAIL_FROM", "").strip())

    def _send_brevo(self, to: str, subject: str, text: str) -> bool:
        body = {
            "sender": {"email": os.getenv("MAIL_FROM", "").strip(), "name": os.getenv("MAIL_FROM_NAME", "").strip() or "Rota"},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": text,
            # Brevo şablonsuz gönderimde HTML gövdesini zorunlu tutar: düz metin, kaçışlanarak sarılır (bağlantı / kod aynen görünür)
            "htmlContent": "<div style=\"font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#0D1B2A\">"
            + html.escape(text).replace("\n", "<br>") + "</div>",
        }
        req = urllib.request.Request(
            BREVO_URL, data=json.dumps(body).encode(), method="POST",
            headers={"api-key": os.getenv("BREVO_API_KEY", "").strip(), "Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:  # 201: gönderildi
            return 200 <= r.status < 300

    def send_sms(self, to: str, text: str) -> bool:
        fn = PROVIDERS.get(sms_provider())
        if not fn:
            return False
        try:
            return bool(fn(to, text))
        except Exception as e:  # sağlayıcı hatası sunucuyu düşürmesin
            print("[bildirim] SMS gönderilemedi:", type(e).__name__, flush=True)
            return False

    def send_email(self, to: str, subject: str, text: str) -> bool:
        if self._brevo():
            try:
                return self._send_brevo(to, subject, text)
            except Exception as e:  # servis hatası sunucuyu düşürmesin
                print("[bildirim] e-posta (Brevo) gönderilemedi:", type(e).__name__, flush=True)
                return False
        host = os.getenv("SMTP_HOST", "").strip()
        sender = os.getenv("SMTP_FROM", "").strip()
        if not host or not sender:
            return False
        try:
            port = int(os.getenv("SMTP_PORT", "587") or "587")
            msg = EmailMessage()
            msg["From"] = sender
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(text)
            user, pw = os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", "")
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=12, context=ssl.create_default_context()) as s:
                    if user:
                        s.login(user, pw)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=12) as s:
                    if os.getenv("SMTP_STARTTLS", "1") != "0":
                        s.starttls(context=ssl.create_default_context())
                    if user:
                        s.login(user, pw)
                    s.send_message(msg)
            return True
        except Exception as e:
            print("[bildirim] e-posta gönderilemedi:", type(e).__name__, flush=True)
            return False


_backend = _Default()


def set_backend(b) -> None:  # testler için
    global _backend
    _backend = b if b is not None else _Default()


def sms_enabled() -> bool:
    return _backend.sms_enabled()


def sms_links_enabled() -> bool:
    """Teslim / konum / yola çıktı bağlantıları sunucudan SMS'le gitsin mi (SMS_LINKS=1). Sahte arka uçta sms_enabled() ile aynı."""
    fn = getattr(_backend, "sms_links_enabled", None)
    return bool(fn() if fn else _backend.sms_enabled())


def email_enabled() -> bool:
    return _backend.email_enabled()


def send_sms(to: str, text: str) -> bool:
    return _backend.send_sms(to, text)


def send_email(to: str, subject: str, text: str) -> bool:
    return _backend.send_email(to, subject, text)
