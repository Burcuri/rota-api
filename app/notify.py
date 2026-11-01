"""SMS + e-posta gönderme katmanı (alıcıya teslim bağlantısı; 1.20.0'dan beri doğrulama kodları da).

Varsayılan: TEST MODU. Hiçbir şey gönderilmez; kurye bağlantıyı uygulamada görür ve kendi SMS / WhatsApp uygulamasıyla iletir;
doğrulama kodları yönetim panelinde görünür (app/security.py).
- E-posta, iki yoldan biri (anahtarlar yalnız ortam değişkeninde durur; kodda / zipte / depoda yazılmaz):
  1) BREVO (1.20.0; ÜCRETSİZ plan günde 300 e-posta, kart istemez): Render ortamına BREVO_API_KEY ve MAIL_FROM (Brevo'da doğrulanmış
     gönderen adresi; isteğe bağlı MAIL_FROM_NAME) yazılır. HTTPS ile gider — Render'ın ÜCRETSİZ planı SMTP kapılarını (25 / 465 / 587)
     26 Eylül 2025'ten beri kapattığı için ücretsiz planda çalışan yol budur.
  2) SMTP: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASS, SMTP_FROM (yalnız Render'ın ÜCRETLİ planında çalışır).
- SMS: bir SMS sağlayıcısı seçilip hesap açılınca SMS_PROVIDER ve sağlayıcının anahtarları ortama yazılır; sağlayıcı bağdaştırıcısı
  PROVIDERS içine eklenir (aşağıda örnek yeri). Sağlayıcı yokken sms_enabled() False döner.
Gönderim hatası kuryeyi bloklamaz: False döner, bağlantı kuryeye gösterilir.
"""
import html
import json
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from typing import Callable

BREVO_URL = "https://api.brevo.com/v3/smtp/email"

# SMS sağlayıcı bağdaştırıcıları: ad -> (to, text) -> bool. Henüz sağlayıcı seçilmedi: boş.
PROVIDERS: dict[str, Callable[[str, str], bool]] = {}


class _Default:
    """Gerçek ortam değişkenlerine bakan varsayılan arka uç. Testler `set_backend` ile sahtesini koyar."""

    def sms_enabled(self) -> bool:
        return os.getenv("SMS_PROVIDER", "").strip().lower() in PROVIDERS

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
        fn = PROVIDERS.get(os.getenv("SMS_PROVIDER", "").strip().lower())
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


def email_enabled() -> bool:
    return _backend.email_enabled()


def send_sms(to: str, text: str) -> bool:
    return _backend.send_sms(to, text)


def send_email(to: str, subject: str, text: str) -> bool:
    return _backend.send_email(to, subject, text)
