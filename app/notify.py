"""Alıcıya bildirim gönderme katmanı (SMS + e-posta).

Varsayılan: TEST MODU. Hiçbir şey gönderilmez; kurye bağlantıyı uygulamada görür ve kendi SMS / WhatsApp uygulamasıyla iletir.
- E-posta: Render ortamına SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASS, SMTP_FROM yazılırsa açılır (ücretsiz katmanı olan
  herhangi bir SMTP servisi olur). Anahtarlar yalnız ortam değişkeninde durur; kodda / zipte / depoda yazılmaz.
- SMS: bir SMS sağlayıcısı seçilip hesap açılınca SMS_PROVIDER ve sağlayıcının anahtarları ortama yazılır; sağlayıcı bağdaştırıcısı
  PROVIDERS içine eklenir (aşağıda örnek yeri). Sağlayıcı yokken sms_enabled() False döner.
Gönderim hatası kuryeyi bloklamaz: False döner, bağlantı kuryeye gösterilir.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Callable

# SMS sağlayıcı bağdaştırıcıları: ad -> (to, text) -> bool. Henüz sağlayıcı seçilmedi: boş.
PROVIDERS: dict[str, Callable[[str, str], bool]] = {}


class _Default:
    """Gerçek ortam değişkenlerine bakan varsayılan arka uç. Testler `set_backend` ile sahtesini koyar."""

    def sms_enabled(self) -> bool:
        return os.getenv("SMS_PROVIDER", "").strip().lower() in PROVIDERS

    def email_enabled(self) -> bool:
        return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_FROM", "").strip())

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
