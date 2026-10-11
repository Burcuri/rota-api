"""Küçük bellek içi hız sınırı (tek süreçlik ücretsiz Render için yeterli; yeniden başlayınca sıfırlanır).
Kimlik doğrulaması olmayan uçlar (alıcı sayfası, hata raporu, yönetici anahtarı denemesi) kötüye kullanılmasın diye."""
import threading
import time
from collections import deque

from fastapi import Request


class Window:
    """`seconds` saniye içinde bir anahtar (IP, bağlantı kodu…) için en çok `max_hits` istek."""

    def __init__(self, max_hits: int, seconds: float):
        self.max_hits = max_hits
        self.seconds = seconds
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._last_gc = time.monotonic()

    def allow(self, key: str) -> bool:
        t = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and t - q[0] > self.seconds:
                q.popleft()
            ok = len(q) < self.max_hits
            if ok:
                q.append(t)
            if t - self._last_gc > 300:  # arada boşalan anahtarları at (bellek şişmesin)
                self._last_gc = t
                for k in [k for k, v in self._hits.items() if not v or t - v[-1] > self.seconds]:
                    self._hits.pop(k, None)
            return ok

    def count(self, key: str) -> int:
        t = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0
            while q and t - q[0] > self.seconds:
                q.popleft()
            return len(q)

    def reset_key(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(request: Request) -> str:
    """Render ters vekilinin gönderdiği ilk X-Forwarded-For adresi; yoksa doğrudan bağlanan."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64] or "?"
    return (request.client.host if request.client else "?")[:64]
