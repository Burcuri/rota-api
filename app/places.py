"""Türkiye'nin resmi il / ilçe / mahalle / köy adları ve kapı katmanının "düzgün yazım" kuralları (1.22.0).

Kullanıcı kararı (3 Kasım 2026): "Türkiye'nin her yerinden herkes bu uygulamayı açıp telefonuyla koordinat kaydettiğinde adres, kapı
numarası, mahalle, sokak, il, ilçe, varsa eğer site ismi düzgün yazılmış olmalı … bu uygulamanın en önemli özelliği bu."

Kaynak: TürkiyeAPI 2025 veri seti (MIT; kaynak TÜİK MEDAS, son güncelleme 2026-05-21) — uygulama il / ilçe / mahalle listelerini
zaten buradan alıyor (services/addressHierarchy.ts). Dosya: data/turkiye_yerler.json (81 il, 973 ilçe, ~32 bin mahalle, ~18 bin köy).

Kurallar (kayıt yazılırken; okurken aynı işlevle anahtar üretilir):
- İl: resmi ad ("antalya", "ANTALYA", "Antalya ili" → "Antalya"; "K.Maraş" → "Kahramanmaraş"). Boşsa ilçeden bulunur; aynı adlı
  ilçe birden çok ildeyse (Merkez, Gölbaşı, Kemer …) noktaya en yakın il seçilir.
- İlçe: resmi ad, ilin içinden ("alanya ilçesi" → "Alanya", "Alnya" → "Alanya"); "Alanya/Antalya" gibi birleşik yazım ayrılır.
- Mahalle / köy: ilçenin resmi listesinden ("kestel mah." → "Kestel Mahallesi"); listede yoksa yazıldığı gibi, baş harfler büyük.
- Cadde / sokak: kısaltmalar açılır, baş harfler büyük ("atatürk cad." → "Atatürk Caddesi", "1234 sk." → "1234. Sokak"); aynı satırda
  birden çok yol varsa kapı en özel yola aittir ("Barbaros Cad., Gül Sk." → "Gül Sokak").
- Site / bina: "güneş sit." → "Güneş Sitesi", "yıldız apt." → "Yıldız Apartmanı".
Eşleşmeyen (resmi listede olmayan) ad reddedilmez; yönetimdeki Kapı haritasında "resmi listede yok" diye işaretlenir.
"""
from __future__ import annotations

import difflib
import json
import math
import os
import re
import threading
import unicodedata
from dataclasses import dataclass

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "turkiye_yerler.json")

# ---------------------------------------------------------------- metin
_TR = str.maketrans({"İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g", "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c"})


def fold(s: str) -> str:
    """Türkçe harfleri düz Latin'e indirir, küçültür (İ/ı tuzağı yok: önce eşlenir, sonra küçültülür)."""
    s = (s or "").translate(_TR)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", fold(s)).split()


def key(s: str) -> str:
    return " ".join(words(s))


def tidy(raw: str, maxlen: int) -> str:
    s = re.sub(r"\s+", " ", "".join(ch for ch in (raw or "") if ch.isprintable())).strip()
    return s[:maxlen]


NEIGH_NOISE = {"mahallesi", "mahalle", "mah", "mh", "mahalesi", "koyu"}
DISTRICT_NOISE = {"ilcesi", "ilce"}
PROVINCE_NOISE = {"ili", "il", "province", "buyuksehir", "belediyesi"}


def neigh_core(raw: str) -> str:
    return " ".join(w for w in words(raw) if w not in NEIGH_NOISE)[:100]


def district_core(raw: str) -> str:
    return " ".join(w for w in words(raw) if w not in DISTRICT_NOISE)[:80]


def province_core(raw: str) -> str:
    return " ".join(w for w in words(raw) if w not in PROVINCE_NOISE)[:60]


# ---------------------------------------------------------------- büyük / küçük harf (uygulamadaki services/nameCase.ts ile aynı kural)
_UP = {"i": "İ", "ı": "I"}
_LOW = {"I": "ı", "İ": "i"}
_LETTER = re.compile(r"[A-Za-zÇĞİIÖŞÜçğıöşüÂâÎîÛû]")


def lower_tr(s: str) -> str:
    return "".join(_LOW.get(c) or c.lower() for c in s)


def _up1(c: str) -> str:
    return _UP.get(c) or c.upper()


def _cap_word(w: str) -> str:
    if len(w) > 1 and w[0].islower() and w[1].isupper():  # "iPhone", "eBay": marka yazımı bozulmaz
        return w
    parts = w.split("-")
    out = []
    for i, p in enumerate(parts):
        m = _LETTER.search(p)
        if not m or (i > 0 and len(p) == 1):  # "Bayram-ı" gibi tamlama eki küçük kalır
            out.append(p)
            continue
        j = m.start()
        out.append(p[:j] + _up1(p[j]) + p[j + 1:])
    return "-".join(out)


def title_tr(s: str) -> str:
    """Hepsi küçük ya da HEPSİ BÜYÜK yazılmışsa baştan düzgün yazılır; karışıksa yalnız kelime başları büyür (TOKİ, ABC korunur)."""
    s = re.sub(r"\s+", " ", s or "").strip()
    if not s:
        return s
    letters = [c for c in s if c.isalpha()]
    has_up = any(c.isupper() for c in letters)
    has_low = any(c.islower() for c in letters)
    if not has_up or (not has_low and (" " in s or len(letters) >= 5)):
        s = lower_tr(s)
    return " ".join(_cap_word(w) for w in s.split(" "))


# ---------------------------------------------------------------- cadde / sokak
KIND_WORDS = {
    "cadde": "cadde", "caddesi": "cadde", "cad": "cadde", "cd": "cadde", "cadesi": "cadde",
    "sokak": "sokak", "sokagi": "sokak", "sok": "sokak", "sk": "sokak", "sokaki": "sokak",
    "bulvar": "bulvar", "bulvari": "bulvar", "blv": "bulvar", "bul": "bulvar", "bulv": "bulvar",
    "meydan": "meydan", "meydani": "meydan", "mey": "meydan",
}
KIND_LABEL = {"cadde": "Caddesi", "sokak": "Sokak", "bulvar": "Bulvarı", "meydan": "Meydanı"}
KIND_BARE = {"cadde": "Cadde", "sokak": "Sokak", "bulvar": "Bulvar", "meydan": "Meydan"}
KIND_RANK = {"sokak": 0, "cadde": 1, "bulvar": 2, "meydan": 3}
NO_TAIL = re.compile(r"\b(no|numara)\b\s*[:.\-]?\s*\d.*$", re.IGNORECASE)
_NUM_NOISE = {"nolu", "numarali", "no"}


def _kind(tok: str) -> str:
    return KIND_WORDS.get(re.sub(r"[^a-z]", "", fold(tok)), "")


def pick_street(raw: str) -> str:
    """Tek satırdaki birden çok yoldan kapının ait olduğu yolu seçer: "Barbaros Cad., Gül Sk. No:5" → "Gül Sk."
    (sokak > cadde > bulvar > meydan). Tür kelimesi yoksa satır olduğu gibi döner (kapı kuyruğu atılır)."""
    base = NO_TAIL.sub("", raw or "")
    toks = re.sub(r"[,;]+", " ", base).split()
    segs: list[tuple[str, str]] = []
    cur: list[str] = []
    for t in toks:
        cur.append(t)
        k = _kind(t)
        if k and len(cur) > 1:
            segs.append((" ".join(cur), k))
            cur = []
        elif k:
            cur = []  # başta yalnız "Sokak": ad yok
    if not segs:
        return base.strip(" ,.;")
    segs.sort(key=lambda x: KIND_RANK[x[1]])
    return segs[0][0]


def canon_street(raw: str) -> str:
    """Düzgün yol adı: "atatürk cad." → "Atatürk Caddesi"; "1234 sk." / "107 Nolu Sokak" → "1234. Sokak" / "107. Sokak";
    "5. cad" → "5. Cadde"; tür yoksa yalnız baş harfler ("Gül" → "Gül")."""
    s = pick_street(tidy(raw, 200))
    toks = s.replace(",", " ").split()
    # Başa yazılmış mahalle ("Kestel Mah. Barbaros Cad.") yol adına karışmaz ("Mahalle Sokak" gibi bir yol adı bozulmaz)
    for i, t in enumerate(toks[:-1]):
        if re.sub(r"[^a-z]", "", fold(t)) in ("mah", "mh", "mahallesi", "mahalle"):
            if any(not _kind(x) for x in toks[i + 1:]):
                toks = toks[i + 1:]
            break
    kind = ""
    core: list[str] = []
    for t in toks:
        k = _kind(t)
        if k:
            kind = k
            continue
        core.append(t)
    core = [t for t in core if re.sub(r"[^a-z]", "", fold(t)) not in _NUM_NOISE or re.search(r"\d", t)]
    if not core:
        return ""
    if len(core) == 1 and re.fullmatch(r"\d{1,5}\.?", core[0]) and kind:
        return f"{core[0].rstrip('.')}. {KIND_BARE[kind]}"
    name = title_tr(" ".join(core))
    name = re.sub(r"(\d)\.(?=[A-Za-zÇĞİÖŞÜçğıöşü])", r"\1. ", name)  # "100.Yıl" → "100. Yıl"
    return f"{name} {KIND_LABEL[kind]}" if kind else name


# ---------------------------------------------------------------- site / bina
SITE_LABEL = {
    "sitesi": "Sitesi", "site": "Sitesi", "sit": "Sitesi",
    "apartmani": "Apartmanı", "apartman": "Apartmanı", "apt": "Apartmanı", "ap": "Apartmanı",
    "konutlari": "Konutları", "rezidans": "Rezidans", "residence": "Residence", "plaza": "Plaza", "evleri": "Evleri",
}


def canon_site(raw: str) -> str:
    """"güneş sit." → "Güneş Sitesi", "YILDIZ APT." → "Yıldız Apartmanı" (yalnız son kelimedeki kısaltma açılır)."""
    s = tidy(raw, 80).strip(" ,.;")
    toks = s.split()
    if not toks:
        return ""
    last = re.sub(r"[^a-z]", "", fold(toks[-1]))
    if last in SITE_LABEL and len(toks) > 1:
        return f"{title_tr(' '.join(toks[:-1]))} {SITE_LABEL[last]}"
    return title_tr(s)


# ---------------------------------------------------------------- kapı numarası
# Bina numarası: "12" · "12A" · "12/A" · "12-A" → 12A · "12/3" (daire) → 12 · "12 D:3" / "12 K:2" (daire / kat) → 12 · "Bina No: 12" → 12
_DOOR_RE = re.compile(
    r"^\s*(?:(?:bina|dis\s*kapi|kapi)\s*)?(?:no|numara|numarasi)?\s*[:.\-]?\s*(\d{1,5})(?:\s*[/\-]?\s*([a-z])(?![a-z0-9]|\s*[:.]?\s*\d))?(?![a-z0-9])"
)


_DOOR_ANY = re.compile(r"\b(?:no|numara|numarasi)\s*[:.]?\s*(\d{1,5})(?:\s*[/\-]?\s*([a-z])(?![a-z0-9]|\s*[:.]?\s*\d))?(?![a-z0-9])")


def door_parts(raw: str) -> tuple[str, int] | None:
    f = fold(raw)
    m = _DOOR_RE.match(f) or _DOOR_ANY.search(f)  # "A Blok No:5" → 5
    if not m:
        return None
    n = int(m.group(1))
    if n <= 0 or n > 99999:
        return None
    return f"{n}{(m.group(2) or '').upper()}", n


# ---------------------------------------------------------------- resmi listeler
PROVINCE_ALIAS = {
    "afyon": "afyonkarahisar", "maras": "kahramanmaras", "k maras": "kahramanmaras", "kmaras": "kahramanmaras",
    "kahraman maras": "kahramanmaras", "urfa": "sanliurfa", "s urfa": "sanliurfa", "surfa": "sanliurfa", "sanli urfa": "sanliurfa",
    "antep": "gaziantep", "g antep": "gaziantep", "gantep": "gaziantep", "gazi antep": "gaziantep", "icel": "mersin",
    "izmit": "kocaeli", "adapazari": "sakarya", "antakya": "hatay",
}
DISTRICT_ALIAS = {("istanbul", "eyup"): "eyupsultan", ("ankara", "kazan"): "kahramankazan"}
FAR_KM = 250.0  # nokta ilin merkezinden bundan uzaksa yönetimde "konum ilden uzak" işareti


@dataclass
class _Data:
    provinces: dict[int, tuple[str, float, float]]
    prov_by_key: dict[str, int]
    districts: dict[int, tuple[str, int]]
    dist_by_pk: dict[tuple[int, str], int]
    dist_by_key: dict[str, list[int]]
    nb: dict[int, dict[str, tuple[str, str]]]  # ilçe → anahtar → (resmi ad, "mah" | "koy")
    official_keys: set  # (il_k, ilçe_k, mahalle_k) — yönetimdeki "resmi listede" işareti için
    official_dk: set  # (il_k, ilçe_k)
    pk_to_pid: dict[str, int]


_data: _Data | None = None
_lock = threading.Lock()


def _nb_label(name: str, kind: str) -> str:
    f = fold(name)
    if kind == "koy":
        return name if f.endswith("koyu") else f"{name} Köyü"
    return name if f.endswith("mahallesi") else f"{name} Mahallesi"


def data() -> _Data:
    global _data
    if _data is not None:
        return _data
    with _lock:
        if _data is not None:
            return _data
        with open(DATA_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        provinces = {pid: (name, lat, lon) for pid, name, lat, lon in raw["iller"]}
        prov_by_key = {province_core(name): pid for pid, (name, _, _) in provinces.items()}
        for a, b in PROVINCE_ALIAS.items():
            prov_by_key.setdefault(a, prov_by_key[b])
        districts = {did: (name, pid) for did, name, pid in raw["ilceler"]}
        dist_by_pk: dict[tuple[int, str], int] = {}
        dist_by_key: dict[str, list[int]] = {}
        for did, (name, pid) in districts.items():
            k = district_core(name)
            dist_by_pk[(pid, k)] = did
            dist_by_key.setdefault(k, []).append(did)
        nb: dict[int, dict[str, tuple[str, str]]] = {}
        official_keys = set()
        for kind, table in (("mah", raw["mahalleler"]), ("koy", raw["koyler"])):
            for did_s, names in table.items():
                did = int(did_s)
                if did not in districts:
                    continue
                pid = districts[did][1]
                pk, dk = province_core(provinces[pid][0]), district_core(districts[did][0])
                m = nb.setdefault(did, {})
                for name in names:
                    label = _nb_label(name, kind)
                    keys = {neigh_core(label), neigh_core(name)}
                    inside = re.findall(r"\(([^)]*)\)", name)  # "Kumköy (Kilyos)": iki ad da bulunur
                    outside = re.sub(r"\([^)]*\)", " ", name)
                    keys.update(neigh_core(x) for x in inside + [outside])
                    for k in keys:
                        if k and (k not in m or m[k][1] == "koy" and kind == "mah"):  # aynı adda mahalle varsa mahalle önce
                            m[k] = (name, kind)
                    official_keys.add((pk, dk, neigh_core(label)))
        official_dk = {(province_core(provinces[pid][0]), district_core(name)) for did, (name, pid) in districts.items()}
        pk_to_pid = {province_core(name): pid for pid, (name, _, _) in provinces.items()}
        _data = _Data(provinces, prov_by_key, districts, dist_by_pk, dist_by_key, nb, official_keys, official_dk, pk_to_pid)
        return _data


def dist_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def _near_pid(pids: list[int], lat: float | None, lon: float | None) -> int:
    d = data()
    if lat is None or lon is None:
        return pids[0]
    return min(pids, key=lambda p: dist_km(lat, lon, d.provinces[p][1], d.provinces[p][2]))


def _fuzzy(k: str, choices: dict[str, object], cutoff: float) -> str | None:
    """En yakın tek aday (yazım hatası: "Alnya" → "Alanya"). İki aday birbirine çok yakınsa hiçbiri seçilmez."""
    if len(k) < 5:
        return None
    scored = sorted(((difflib.SequenceMatcher(None, k, c).ratio(), c) for c in choices if abs(len(c) - len(k)) <= 3), reverse=True)
    if not scored or scored[0][0] < cutoff:
        return None
    if len(scored) > 1 and scored[1][0] > scored[0][0] - 0.04 and scored[1][1] != scored[0][1]:
        return None
    return scored[0][1]


def province_id(raw: str) -> int | None:
    d = data()
    k = province_core(raw)
    k = " ".join(w for w in k.split() if w not in ("merkez", "merkezi"))
    if not k:
        return None
    if k in d.prov_by_key:
        return d.prov_by_key[k]
    best = _fuzzy(k, d.prov_by_key, 0.85)
    return d.prov_by_key[best] if best else None


def district_id(raw: str, pid: int | None, lat: float | None = None, lon: float | None = None) -> tuple[int | None, int | None]:
    """(ilçe, il). İl verilmişse önce o ilin içinde; yoksa bütün Türkiye'de (aynı adlı ilçelerden noktaya en yakın il)."""
    d = data()
    full = district_core(raw)
    if not full:
        return None, pid
    tries = [full]
    ws = full.split()
    trimmed = [w for w in ws if w not in ("merkez", "merkezi", "belediyesi")]
    if trimmed and trimmed != ws:
        tries.append(" ".join(trimmed))
    for t in tries:
        if pid is not None:
            pk = province_core(d.provinces[pid][0])
            alias = DISTRICT_ALIAS.get((pk, t))
            if alias:
                t = alias
            if (pid, t) in d.dist_by_pk:
                return d.dist_by_pk[(pid, t)], pid
    for t in tries:
        cands = d.dist_by_key.get(t)
        if cands:
            pids = [d.districts[c][1] for c in cands]
            if pid is not None and pid not in pids:
                # İl yanlış yazılmış olabilir ("Keçiören, Antalya"): ancak nokta o ilçenin iline daha yakınsa düzeltilir
                alt = _near_pid(pids, lat, lon)
                if lat is not None and lon is not None:
                    pl, ql = d.provinces[pid], d.provinces[alt]
                    if dist_km(lat, lon, ql[1], ql[2]) < dist_km(lat, lon, pl[1], pl[2]):
                        return d.dist_by_pk[(alt, t)], alt
                return None, pid
            p2 = pid if pid is not None else _near_pid(pids, lat, lon)
            return d.dist_by_pk[(p2, t)], p2
    # yazım hatası
    pool = {k: v for (p, k), v in d.dist_by_pk.items() if pid is None or p == pid}
    best = _fuzzy(full, pool, 0.86)
    if best:
        cands = d.dist_by_key.get(best, [])
        pids = [d.districts[c][1] for c in cands]
        p2 = pid if pid is not None and pid in pids else _near_pid(pids, lat, lon)
        return d.dist_by_pk[(p2, best)], p2
    return None, pid


def neighborhood(raw: str, did: int | None) -> tuple[str, bool]:
    """(düzgün ad, resmi listede mi). "kestel mah." → ("Kestel Mahallesi", True)."""
    t = tidy(raw, 120)
    if not t:
        return "", False
    if did is not None:
        d = data()
        m = d.nb.get(did, {})
        k = neigh_core(t)
        hit = m.get(k)
        if hit is None:
            best = _fuzzy(k, m, 0.88)
            hit = m.get(best) if best else None
        if hit is not None:
            return _nb_label(*hit), True
    out = title_tr(t)
    toks = words(out)
    if toks and toks[-1] in ("mah", "mh", "mahalle", "mahalesi"):
        out = re.sub(r"\s*\S+$", "", out).strip() + " Mahallesi"
    elif not any(w in NEIGH_NOISE for w in toks):
        out = f"{out} Mahallesi"
    return out, False


@dataclass
class Area:
    province: str
    district: str
    neighborhood: str
    pid: int | None
    did: int | None
    official_neighborhood: bool


def canon_area(province: str, district: str, neighborhood_raw: str, lat: float | None = None, lon: float | None = None) -> Area:
    """İl / ilçe / mahalle üçlüsünü resmi adlarla düzgün yazar; il boşsa ilçeden bulur."""
    province, district = tidy(province, 80), tidy(district, 100)
    # "Alanya/Antalya", "Alanya, Antalya", "Alanya - Antalya" ilçe kutusuna birlikte yazılmışsa ayrılır
    m = re.match(r"^(.+?)\s*(?:/|,|\s-\s)\s*(.+)$", district)
    if m and province_id(m.group(2)) is not None and (not province or province_id(province) == province_id(m.group(2))):
        district, province = m.group(1), m.group(2)
    else:
        # "Alanya Antalya" (ayraçsız): son kelime il, öncesi o ilin bir ilçesiyse ayrılır
        m2 = re.match(r"^(.+?)\s+(\S+)$", district)
        if m2 and len(fold(m2.group(2))) >= 3:
            p2 = province_id(m2.group(2))
            if p2 is not None and (not province or province_id(province) == p2) and district_id(m2.group(1), p2)[0] is not None:
                district, province = m2.group(1), m2.group(2)
    d = data()
    pid = province_id(province) if province else None
    if pid is None and province and not district:
        district = province  # ilçe il kutusuna yazılmış ("Alanya")
    did, pid = district_id(district, pid, lat, lon) if district else (None, pid)
    # İl 81 resmi adın biri ya da boştur (resmi olmayan yazım ilçeden bulunamazsa boş kalır, yönetimde "il belirsiz" görünür)
    p_name = d.provinces[pid][0] if pid is not None else ""
    d_name = d.districts[did][0] if did is not None else title_tr(district)
    n_name, official = neighborhood(neighborhood_raw, did)
    return Area(p_name, d_name, n_name, pid, did, official)


# ---------------------------------------------------------------- yönetim için işaretler
def province_center(province_k: str) -> tuple[float, float] | None:
    d = data()
    pid = d.pk_to_pid.get(province_k)
    if pid is None:
        return None
    _, lat, lon = d.provinces[pid]
    return lat, lon


def flags(level: str, pk: str, dk: str = "", nk: str = "", lat: float | None = None, lon: float | None = None) -> dict:
    """Kırılım satırı için: resmi listede mi, konum ilin merkezinden çok uzak mı (yalnız işaret; kayıt silinmez)."""
    d = data()
    if level == "province":
        official = pk in d.pk_to_pid
    elif level == "district":
        official = (pk, dk) in d.official_dk
    elif level == "neighborhood":
        official = (pk, dk, nk) in d.official_keys
    else:
        official = True
    far = False
    c = province_center(pk)
    if c and lat is not None and lon is not None:
        far = dist_km(lat, lon, c[0], c[1]) > FAR_KM
    return {"official": official, "far": far}
