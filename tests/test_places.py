"""1.22.0: Kapı katmanında "düzgün yazım" — Türkiye'nin 81 ili, 973 ilçesi ve bütün mahalle / köyleri (TürkiyeAPI 2025, kaynak TÜİK).
Kullanıcı kararı (3 Kasım 2026): "Türkiye'nin her yerinden herkes … koordinat kaydettiğinde adres, kapı numarası, mahalle, sokak, il,
ilçe, varsa eğer site ismi düzgün yazılmış olmalı. Bunu kontrol et ve düzgün çalıştığından %100 emin ol."
"""
import itertools
import json

import pytest
from fastapi.testclient import TestClient

from app import places
from app.main import app

from helpers import admin_login
from test_doors import auth, person, post

client = TestClient(app)
D = places.data()
RAW = json.load(open(places.DATA_FILE, encoding="utf-8"))
_seq = itertools.count(1)


def ascii_tr(s):
    return s.translate(str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU"))


def upper_tr(s):
    return "".join({"i": "İ", "ı": "I"}.get(c) or c.upper() for c in s)


def variants(name):
    """Kuryenin yazabileceği biçimler: küçük, BÜYÜK, Türkçe harfsiz, Türkçe harfsiz BÜYÜK."""
    return {name, places.lower_tr(name), upper_tr(name), ascii_tr(places.lower_tr(name)), ascii_tr(upper_tr(name)).upper()}


def center(pid):
    _, lat, lon = D.provinces[pid]
    return lat, lon


# ------------------------------------------------------------------ il / ilçe: hepsi
def test_dataset_complete():
    assert len(D.provinces) == 81 and len(D.districts) == 973
    assert sum(len(v) for v in RAW["mahalleler"].values()) > 31000 and sum(len(v) for v in RAW["koyler"].values()) > 18000
    assert all(lat and lon and 35 < lat < 43 and 25 < lon < 45 for _, lat, lon in D.provinces.values())


def test_all_81_provinces_any_spelling():
    for pid, (name, _, _) in D.provinces.items():
        for v in variants(name) | {name + " ili", upper_tr(name) + " İLİ", name + " Merkez"}:
            assert places.province_id(v) == pid, (v, name)
            assert places.canon_area(v, "", "").province == name


@pytest.mark.parametrize("raw,want", [
    ("K.Maraş", "Kahramanmaraş"), ("Maraş", "Kahramanmaraş"), ("Urfa", "Şanlıurfa"), ("Antep", "Gaziantep"), ("Afyon", "Afyonkarahisar"),
    ("İçel", "Mersin"), ("Istanbul", "İstanbul"), ("ISTANBUL", "İstanbul"), ("Eskişehr", "Eskişehir"), ("Diyarbakir", "Diyarbakır"),
])
def test_province_aliases_and_typos(raw, want):
    assert places.canon_area(raw, "", "").province == want


def test_all_973_districts_with_province():
    for did, (name, pid) in D.districts.items():
        pname = D.provinces[pid][0]
        for v in variants(name) | {name + " İlçesi", upper_tr(name) + " İLÇESİ"}:
            a = places.canon_area(pname, v, "", *center(pid))
            assert (a.province, a.district, a.did) == (pname, name, did), (pname, v, a)


def test_all_973_districts_without_province_find_their_province():
    """İl yazılmamış: ilçe adından bulunur; aynı adlı ilçelerde (51 Merkez, Gölbaşı, Kemer, Edremit …) noktaya en yakın il."""
    for did, (name, pid) in D.districts.items():
        for v in variants(name):
            a = places.canon_area("", v, "", *center(pid))
            assert (a.province, a.district) == (D.provinces[pid][0], name), (v, a)


@pytest.mark.parametrize("prov,dist,want", [
    ("Antalya", "Alnya", ("Antalya", "Alanya")), ("Ankara", "Kecioren", ("Ankara", "Keçiören")), ("İstanbul", "Sisli", ("İstanbul", "Şişli")),
    ("", "Alanya/Antalya", ("Antalya", "Alanya")), ("", "Keçiören, Ankara", ("Ankara", "Keçiören")), ("", "Alanya - Antalya", ("Antalya", "Alanya")),
    ("İstanbul", "Eyüp", ("İstanbul", "Eyüpsultan")), ("Ankara", "Kazan", ("Ankara", "Kahramankazan")), ("Alanya", "", ("Antalya", "Alanya")),
    ("Antalya", "Alanya Merkez", ("Antalya", "Alanya")),
])
def test_district_spellings(prov, dist, want):
    a = places.canon_area(prov, dist, "", 36.6, 31.9)
    assert (a.province, a.district) == want


def test_wrong_province_fixed_only_when_the_point_agrees():
    # "Keçiören, Antalya" ama nokta Ankara'da → il Ankara olur; nokta Antalya'daysa il değişmez, ilçe "resmi listede yok" kalır
    assert (lambda a: (a.province, a.did is not None))(places.canon_area("Antalya", "Keçiören", "", 39.97, 32.86)) == ("Ankara", True)
    a = places.canon_area("Antalya", "Keçiören", "", 36.89, 30.70)
    assert a.province == "Antalya" and a.did is None


# ------------------------------------------------------------------ mahalle / köy: hepsi
def test_every_neighborhood_and_village_in_turkey():
    """32 bin mahalle + 18 bin köy: "X Mah.", "X MAHALLESİ", "x" → resmi ad + "Mahallesi" (köyse "Köyü"); kurye nasıl yazarsa yazsın."""
    checked = 0
    for kind, table in (("mah", RAW["mahalleler"]), ("koy", RAW["koyler"])):
        for did_s, names in table.items():
            did = int(did_s)
            pid = D.districts[did][1]
            pname, dname = D.provinces[pid][0], D.districts[did][0]
            for name in names:
                hit = D.nb[did][places.neigh_core(places._nb_label(name, kind))]  # aynı anahtarda mahalle köyden önce gelir
                want = places._nb_label(*hit)
                suffix = " Mah." if kind == "mah" else " Köyü"
                for v in (name + suffix, upper_tr(name) + (" MAHALLESİ" if kind == "mah" else " KÖYÜ"), places.lower_tr(name)):
                    a = places.canon_area(pname, dname, v, *center(pid))
                    assert a.neighborhood == want and a.official_neighborhood, (pname, dname, v, a.neighborhood, want)
                    checked += 1
    assert checked > 150000


@pytest.mark.parametrize("raw,want", [
    ("kestel mah.", "Kestel Mahallesi"), ("KESTEL MAHALLESİ", "Kestel Mahallesi"), ("Kestel Mh.", "Kestel Mahallesi"), ("kestel", "Kestel Mahallesi"),
    ("Mahmutlar Mahallesi", "Mahmutlar Mahallesi"), ("MAHMUTLAR", "Mahmutlar Mahallesi"),
])
def test_neighborhood_alanya(raw, want):
    a = places.canon_area("Antalya", "Alanya", raw, 36.5, 32.1)
    assert a.neighborhood == want and a.official_neighborhood


def test_parenthesized_official_names_found_by_either_name():
    a1 = places.canon_area("İstanbul", "Sarıyer", "Kumköy Mah.", 41.2, 29.0)
    a2 = places.canon_area("İstanbul", "Sarıyer", "kilyos", 41.2, 29.0)
    assert a1.neighborhood == a2.neighborhood == "Kumköy (Kilyos) Mahallesi"


def test_unknown_neighborhood_kept_but_neat():
    a = places.canon_area("Antalya", "Alanya", "yeni kurulan mah.", 36.5, 32.1)
    assert a.neighborhood == "Yeni Kurulan Mahallesi" and a.official_neighborhood is False


# ------------------------------------------------------------------ yol / site / kapı no
@pytest.mark.parametrize("raw,want", [
    ("atatürk cad.", "Atatürk Caddesi"), ("ATATÜRK CADDESİ", "Atatürk Caddesi"), ("Atatürk Cd.", "Atatürk Caddesi"),
    ("1234 sk.", "1234. Sokak"), ("1234. Sk. No:5", "1234. Sokak"), ("107 Nolu Sokak", "107. Sokak"), ("5. cad", "5. Cadde"),
    ("Barbaros Cad., Gül Sk.", "Gül Sokak"), ("Kestel Mah. Barbaros Cad.", "Barbaros Caddesi"), ("menderes blv.", "Menderes Bulvarı"),
    ("cumhuriyet meydanı", "Cumhuriyet Meydanı"), ("75. yıl caddesi", "75. Yıl Caddesi"), ("100.yıl sk", "100. Yıl Sokak"),
    ("HACI BAYRAM-I VELİ CADDESİ", "Hacı Bayram-ı Veli Caddesi"), ("TOKİ konutları sk.", "TOKİ Konutları Sokak"), ("ankara yolu", "Ankara Yolu"),
    ("Gül", "Gül"), ("mahalle sokak", "Mahalle Sokak"),
])
def test_street(raw, want):
    assert places.canon_street(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("güneş sit.", "Güneş Sitesi"), ("GÜNEŞ SİTESİ", "Güneş Sitesi"), ("yıldız apt", "Yıldız Apartmanı"), ("Yıldız Apartmanı", "Yıldız Apartmanı"),
    ("Ata Rezidans", "Ata Rezidans"), ("toki konutları", "Toki Konutları"),
])
def test_site(raw, want):
    assert places.canon_site(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("12", "12"), ("12A", "12A"), ("12/A", "12A"), ("12-a", "12A"), ("12/3", "12"), ("12 D:3", "12"), ("12 d.3", "12"), ("12 K:2", "12"),
    ("12 d 3", "12"), ("No:5", "5"), ("Bina No: 12", "12"), ("no 12-14", "12"), ("A Blok No:5", "5"), ("Güneş Sitesi No 12 D:4", "12"),
])
def test_door_number(raw, want):
    assert places.door_parts(raw)[0] == want


# ------------------------------------------------------------------ uçtan uca: kuryeler → kayıt → yönetim kırılımı
def report(tok, **kw):
    n = kw.pop("n", 0)
    d = {"province": "", "district": "", "neighborhood": "", "street": "", "door": "1", "lat": 36.5180 + n * 0.00001, "lon": 32.0640,
         "accuracyM": 8, "source": "checkin", "vote": "confirm"}
    d.update(kw)
    r = post(tok, d)
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def test_two_couriers_messy_and_neat_become_one_verified_point():
    s = next(_seq)
    t1, _ = person()
    t2, _ = person()
    a = report(t1, district="ALANYA", neighborhood="kestel mah.", street=f"zeytin {s} cad.", door="12 D:3", site="güneş sit.")
    assert a["status"] == "new" and a["street"] == f"Zeytin {s} Caddesi" and a["door"] == "12" and a["site"] == "Güneş Sitesi"
    assert (a["province"], a["district"], a["neighborhood"]) == ("Antalya", "Alanya", "Kestel Mahallesi")
    b = report(t2, province="Antalya", district="Alanya", neighborhood="Kestel Mahallesi", street=f"Zeytin {s} Caddesi", door="12", n=1)
    assert b["status"] == "confirmed" and b["verified"] is True and b["id"] == a["id"] and b["confirms"] == 2


def test_slash_form_and_joined_streets_merge():
    s = next(_seq)
    t1, _ = person()
    t2, _ = person()
    a = report(t1, district="Alanya/Antalya", neighborhood="Mahmutlar", street=f"Barbaros Cad., Nar {s} Sk.", door="7")
    assert a["street"] == f"Nar {s} Sokak" and a["province"] == "Antalya" and a["district"] == "Alanya"
    b = report(t2, province="antalya", district="alanya", neighborhood="MAHMUTLAR MAHALLESİ", street=f"nar {s} sokak", door="No:7", n=1)
    assert b["id"] == a["id"] and b["verified"] is True


def test_admin_summary_turkey_province_district_neighborhood_street_with_flags():
    s = next(_seq)
    t1, _ = person()
    # İstanbul / Sarıyer, Ankara / Keçiören (il yazılmadı), il yazılmamış ve ilçe resmi listede olmayan kayıt, ilinden çok uzak nokta
    report(t1, province="istanbul", district="sarıyer", neighborhood="kumköy", street=f"deniz {s} sk.", door="3", lat=41.24, lon=29.03)
    report(t1, district="Keçiören", neighborhood="ovacık mah.", street=f"lale {s} cad.", door="9", lat=39.99, lon=32.86)
    report(t1, district=f"Bilinmez {s}", neighborhood="Orta Mah.", street=f"Su {s} Sokak", door="4", lat=38.4, lon=27.1)
    report(t1, province="Antalya", district="Alanya", neighborhood="Kestel", street=f"Uzak {s} Sokak", door="5", lat=41.67, lon=26.56)
    hd, _ = admin_login()
    top = client.get("/v1/admin/doors/summary", headers=hd).json()
    assert top["level"] == "province"
    names = {x["name"]: x for x in top["items"]}
    assert {"İstanbul", "Ankara", "Antalya", "(il belirsiz)"} <= set(names)
    assert names["İstanbul"]["official"] and names["Ankara"]["official"] and names["(il belirsiz)"]["official"] is False
    ist = client.get("/v1/admin/doors/summary", params={"p": names["İstanbul"]["p"]}, headers=hd).json()
    sar = next(x for x in ist["items"] if x["name"] == "Sarıyer")
    assert ist["level"] == "district" and sar["official"] is True and sar["far"] is False
    nb = client.get("/v1/admin/doors/summary", params={"p": sar["p"], "d": sar["d"]}, headers=hd).json()
    kum = next(x for x in nb["items"] if x["name"] == "Kumköy (Kilyos) Mahallesi")
    assert nb["level"] == "neighborhood" and kum["official"] is True
    st = client.get("/v1/admin/doors/summary", params={"p": sar["p"], "d": sar["d"], "n": kum["n"]}, headers=hd).json()
    assert st["level"] == "street" and any(x["name"] == f"Deniz {s} Sokak" for x in st["items"])
    pts = client.get("/v1/admin/doors", params={"p": sar["p"], "d": sar["d"], "n": kum["n"]}, headers=hd).json()["items"]
    p3 = next(x for x in pts if x["street"] == f"Deniz {s} Sokak")
    assert (p3["door"], p3["lat"], p3["lon"], p3["province"], p3["district"]) == ("3", 41.24, 29.03, "İstanbul", "Sarıyer")
    # il belirsiz: "-" ile açılır; ilçe resmi listede yok
    unk = client.get("/v1/admin/doors/summary", params={"p": "-"}, headers=hd).json()
    row = next(x for x in unk["items"] if x["name"] == f"Bilinmez {s}")
    assert row["official"] is False and row["p"] == "-"
    # Antalya / Alanya ama nokta Edirne'de: "konum ilden uzak"
    ant = client.get("/v1/admin/doors/summary", params={"p": "antalya"}, headers=hd).json()
    al = next(x for x in ant["items"] if x["name"] == "Alanya")
    kes = client.get("/v1/admin/doors/summary", params={"p": "antalya", "d": al["d"]}, headers=hd).json()
    assert any(x["name"] == "Kestel Mahallesi" and x["far"] for x in kes["items"])


def test_street_lookup_uses_the_same_neat_names():
    """Okuma da aynı kuralla: "alnya" / "KESTEL MAH." / "ZEYTİN CAD." sorusu düzgün yazılmış kaydı bulur."""
    s = next(_seq)
    t1, _ = person()
    report(t1, province="Antalya", district="Alanya", neighborhood="Kestel", street=f"Ayva {s} Caddesi", door="21")
    r = client.get("/v1/doors/street", headers=auth(t1), params={"district": "alnya", "neighborhood": "KESTEL MAH.", "street": f"AYVA {s} CAD."})
    assert r.status_code == 200 and [d["door"] for d in r.json()["doors"]] == ["21"]
