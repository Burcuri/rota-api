import os
import sys
import tempfile

# Testler gerçek veritabanına dokunmaz. Varsayılan: her çalıştırmada geçici SQLite dosyası.
# PostgreSQL'e karşı denemek için:  TEST_DATABASE_URL=postgresql://kullanici@localhost/deneme_db pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["SECRET_KEY"] = "test-anahtari-yalnizca-testler-icin-0123456789"
# Yönetim testleri: ilk yöneticiyi oluşturmak / kurtarmak için kullanılan anahtar (yalnız testlerde; gerçek anahtar Render'da durur)
os.environ["ADMIN_KEY"] = "test-yonetici-anahtari-123456"
os.environ.pop("ADMIN_REQUIRE_2FA", None)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_admin_limits():
    """Yönetici giriş sınırları testler arasında sıfırlanır (bellek içi sayaçlar)."""
    from app.main import admin_router, pw_fail

    for lim in admin_router.limiters:
        lim.reset()
    pw_fail.reset()
    yield
    for lim in admin_router.limiters:
        lim.reset()
    pw_fail.reset()
