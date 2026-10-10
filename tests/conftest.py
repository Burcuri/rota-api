import os
import sys
import tempfile

# Testler gerçek veritabanına dokunmaz. Varsayılan: her çalıştırmada geçici SQLite dosyası.
# PostgreSQL'e karşı denemek için:  TEST_DATABASE_URL=postgresql://kullanici@localhost/deneme_db pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["SECRET_KEY"] = "test-anahtari-yalnizca-testler-icin-0123456789"
