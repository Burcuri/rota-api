"""1.20.1: herkese açık yasal ve destek sayfaları — /gizlilik, /kosullar, /destek — ve uygulamanın okuduğu /v1/support.
Mağazalar gizlilik politikasını ve destek adresini PDF olmayan, herkese açık bir adreste ister."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _secure(r):
    csp = r.headers.get("content-security-policy", "")
    assert "script-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers.get("x-frame-options") == "DENY" and r.headers.get("x-content-type-options") == "nosniff"


def test_privacy_page_tr_en_from_app_texts():
    r = client.get("/gizlilik")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    t = r.text
    # TR + EN tek sayfada; metin uygulamanın aydınlatma metninden (sürüm ve taslak işareti dahil)
    assert 'id="tr"' in t and 'id="en"' in t and "Aydınlatma Metni" in t and "Privacy Notice" in t
    assert "VERİ SORUMLUSU" in t and "CANLI TAKİP" in t and "REHBERDEN BULMA" in t and "LIVE TRACKING" in t
    assert "Metin sürümü: 8" in t and "TASLAK" in t and "DRAFT" in t
    assert '<a href="/hesap-sil">/hesap-sil</a>' in t and "<script" not in t
    _secure(r)
    assert client.head("/gizlilik").status_code == 200


def test_terms_page():
    r = client.get("/kosullar")
    assert r.status_code == 200
    t = r.text
    assert "Kullanım Koşulları" in t and "Terms of Use" in t and "3. ÜCRET VE ÖDEME" in t and "Metin sürümü: 2" in t
    # uygulama içi onay satırları sayfaya girmez
    assert "okudum, anladım ve kabul ediyorum" not in t and "Devam etmek için" not in t
    _secure(r)


def test_support_page_without_email(monkeypatch):
    monkeypatch.delenv("DESTEK_EPOSTA", raising=False)
    r = client.get("/destek")
    assert r.status_code == 200 and "Destek e-postası yakında" in r.text and "support e-mail will appear here soon" in r.text
    assert "mailto:" not in r.text and "<!--EPOSTA_" not in r.text and "<script" not in r.text
    assert 'href="/hesap-sil"' in r.text and 'href="/gizlilik"' in r.text and 'href="/kosullar"' in r.text
    _secure(r)
    assert client.get("/v1/support").json() == {"email": None}


def test_support_page_with_email(monkeypatch):
    monkeypatch.setenv("DESTEK_EPOSTA", "  rota.destek@ornek.com ")
    r = client.get("/destek")
    assert r.text.count('href="mailto:rota.destek@ornek.com"') == 2 and "yakında" not in r.text and "<!--" not in r.text
    assert client.get("/v1/support").json() == {"email": "rota.destek@ornek.com"}


def test_support_email_rejects_non_email_and_markup(monkeypatch):
    for bad in ["destek", "a@b", '"><script>alert(1)</script>@x.com', "a b@c.com", "x@" + "y" * 130 + ".com"]:
        monkeypatch.setenv("DESTEK_EPOSTA", bad)
        r = client.get("/destek")
        assert "mailto:" not in r.text and "<script>alert" not in r.text and "yakında" in r.text
        assert client.get("/v1/support").json() == {"email": None}
