"""1.30.0 — İngilizce (kullanıcı isteği 10 Kasım 2026: "bütün tercüme İngilizce Türkçe karşılığın tam yapıldığından emin ol").

Sunucunun kullanıcıya yazdığı metinler (hata, duyuru, bildirim, e-posta, coin / puan geçmişi, anket sorusu, karne nedenleri) Türkçe
yazılır ve Türkçe saklanır; uygulama `X-Lang: en` gönderiyorsa (Hesabım › Profil · Dil = English) çıkışta İngilizceye çevrilir:
  - `en(text)`: önce tam eşleşme, sonra kalıp ("Yetersiz coin: {n} Rota coin daha lazım (bakiye {bal})."), sonra cümle cümle.
    Kalıptaki değer (sayı, ad, kod) aynen geçer; `t_` ile başlayan yer tutucuların ve FRAGMENTS'teki kısa parçaların ("Bir kurye",
    "15 dakika", "İlçe") İngilizcesi de yazılır.
  - İstek dili: `X-Lang` başlığı (LangMiddleware → REQ_LANG). Arka plandaki bildirim gönderimi cihazın kayıtlı dilini kullanır.
Alıcıya giden SMS / e-posta / sayfa ve yönetim sayfası Türkçe kalır (bunlar `X-Lang` göndermez). Liste: i18n_en.py; eksiksizlik
denemesi: tests/test_i18n_en.py (kaynaktaki her Türkçe kullanıcı metninin İngilizcesi var mı).
"""
import contextvars
import re

from .i18n_en import EN, FRAGMENTS

REQ_LANG: contextvars.ContextVar[str] = contextvars.ContextVar("rota_lang", default="tr")
_PH = re.compile(r"\{(\w+)\}")


def norm_lang(v: object) -> str:
    return "en" if str(v or "").strip().lower().startswith("en") else "tr"


def lang_of(request) -> str:
    """İsteğin dili: `X-Lang` başlığı (yoksa Türkçe)."""
    try:
        return norm_lang(request.headers.get("x-lang"))
    except Exception:
        return "tr"


class LangMiddleware:
    """Saf ASGI ara katman: isteğin dilini REQ_LANG'e yazar (uç noktalar ve hata işleyicileri okur)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        lang = "tr"
        for k, v in scope.get("headers") or []:
            if k == b"x-lang":
                lang = norm_lang(v.decode("latin-1"))
                break
        token = REQ_LANG.set(lang)
        try:
            await self.app(scope, receive, send)
        finally:
            REQ_LANG.reset(token)


def _compile(pairs: dict[str, str]) -> tuple[dict[str, str], list[tuple[re.Pattern, str, int]]]:
    exact: dict[str, str] = {}
    tpls: list[tuple[re.Pattern, str, int]] = []
    for tr, en_ in pairs.items():
        if not _PH.search(tr):
            exact[tr] = en_
            continue
        rx, pos = "", 0
        for m in _PH.finditer(tr):
            rx += re.escape(tr[pos:m.start()]) + f"(?P<{m.group(1)}>.+?)"
            pos = m.end()
        rx += re.escape(tr[pos:])
        literal = len(_PH.sub("", tr))
        tpls.append((re.compile(rx, re.S), en_, literal))
    tpls.sort(key=lambda x: -x[2])  # en çok sabit metni olan (en belirgin) önce
    return exact, tpls


_EXACT, _TPLS = _compile(EN)
_FEXACT, _FTPLS = _compile(FRAGMENTS)


def _fill(m: re.Match, tpl: str, depth: int) -> str:
    def val(g: re.Match) -> str:
        name = g.group(1)
        v = m.group(name)
        return en(v, depth + 1) if name.startswith("t_") else fragment(v)
    return _PH.sub(val, tpl)


def fragment(v: str) -> str:
    """Kalıbın içindeki kısa parça ("Bir kurye", "15 dakika", "İlçe"): yalnız FRAGMENTS'ten; yoksa aynen (ad, sayı, kod)."""
    if not v:
        return v
    hit = _FEXACT.get(v)
    if hit is not None:
        return hit
    for rx, tpl, _n in _FTPLS:
        m = rx.fullmatch(v)
        if m:
            return _PH.sub(lambda g: m.group(g.group(1)), tpl)
    return v


def en(text: str, depth: int = 0) -> str:
    """Türkçe metnin İngilizcesi; bilinmiyorsa metnin kendisi."""
    if not isinstance(text, str) or not text or depth > 4:
        return text
    hit = _EXACT.get(text)
    if hit is not None:
        return hit
    stripped = text.strip()
    if stripped != text:
        inner = en(stripped, depth + 1)
        return text.replace(stripped, inner) if inner != stripped else text
    # Birleştirilmiş metin (cümleler ya da "; " ile ayrılmış liste): HER parça çevrilebiliyorsa parça parça — kalıbın {değer}i
    # ayırıcıyı yutmasın ("A; B" iki ayrı neden)
    splits = []
    for sep_rx, joiner in ((r";\s+", "; "), (r"(?<=[.!?])\s+", " ")):
        parts = re.split(sep_rx, text)
        if len(parts) > 1:
            out = [en(p, depth + 1) for p in parts]
            if all(o != p for o, p in zip(out, parts)):
                return joiner.join(out)
            splits.append((parts, out, joiner))
    for rx, tpl, _n in _TPLS:
        m = rx.fullmatch(text)
        if m:
            return _fill(m, tpl, depth)
    for parts, out, joiner in splits:  # kısmen çevrilen (ör. yönetimin yazdığı serbest neden Türkçe kalır)
        if out != parts:
            return joiner.join(out)
    return text


def loc(text: str, lang: str | None = None) -> str:
    """İstek (ya da verilen) dil İngilizceyse çevirir."""
    if (lang or REQ_LANG.get()) == "en":
        return en(text)
    return text


def loc_all(items, lang: str | None = None):
    return [loc(x, lang) for x in items]
