"""Serbest metin süzgeci (yorum, anket). İstemcideki `account/moderation.ts` ile aynı mantık: küfür kökleri (yazım numaraları,
tekrar eden harfler, harf arası boşluk / noktayla gizleme) ve kişisel veri (telefon, IBAN, kimlik numarası).
Liste kasıtlı kısadır; asıl denetim yönetimin gizleme yetkisidir."""
import re

STEMS = [
    "amk", "aq", "amq", "orospu", "orosp", "pic", "siktir", "sikik", "sikim", "sikeyim", "yarrak", "yarak", "ibne", "gavat", "pust", "got",
    "gotveren", "amina", "amini", "anani", "anasini", "ananı", "mal herif", "serefsiz", "itoglu", "it oglu", "kahpe", "fahise", "salak",
    "aptal", "gerizekali",
]
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
_TR = str.maketrans({"ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"})
_REPEAT = re.compile(r"(.)\1{1,}")
_NON_AZ = re.compile(r"[^a-z]+")
_PHONE = re.compile(r"\b0?5\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}\b")
_IBAN = re.compile(r"\bTR\d{2}[\s\d]{20,}\b", re.IGNORECASE)
_ID11 = re.compile(r"\b\d{11}\b")


def normalize(s: str) -> str:
    t = (s or "").replace("İ", "i").replace("I", "ı").lower().translate(_TR)
    return "".join(_LEET.get(c, c) for c in t)


def _squash(t: str) -> str:
    return _REPEAT.sub(r"\1", t)


_STEMS = [_squash(_NON_AZ.sub("", normalize(x))) for x in STEMS]


def contains_profanity(s: str) -> bool:
    base = normalize(s)
    words = [_squash(w) for w in _NON_AZ.split(base) if w]
    joined = _squash(re.sub(r"[^a-z]", "", base))
    for w in words:
        for st in _STEMS:
            if (w == st) if len(st) <= 3 else (w.startswith(st) or w == st):
                return True
    return any(len(st) >= 5 and st in joined for st in _STEMS)


def contains_personal_data(s: str) -> bool:
    t = s or ""
    return bool(_PHONE.search(t) or _IBAN.search(t) or _ID11.search(t))


def comment_error(s: str, max_len: int = 500) -> str:
    """Yorum kabul edilebilir mi? Uygun değilse kullanıcıya gösterilecek Türkçe neden, uygunsa boş metin."""
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) > max_len:
        return f"Yorum en fazla {max_len} karakter olabilir."
    if contains_profanity(t):
        return "Küfür ve uygunsuz ifade yazılamaz. Lütfen metni düzeltin."
    if contains_personal_data(t):
        return "Telefon, IBAN ya da kimlik numarası yazmayın. Kişileri değil durumu anlatın."
    return ""
