"""1.30.0 — sunucunun İngilizcesi eksiksiz mi (kullanıcı isteği 10 Kasım 2026: "bütün tercüme İngilizce Türkçe karşılığın tam yapıldığından
emin ol").

1. Kaynak taraması: app/*.py içindeki her Türkçe metin (f-string ise değişkenleri örnek değerle doldurulmuş hâli) app/i18n.py › en() ile
   İngilizceye çevriliyor; çevrilmeyenler BİLEREK Türkçe kalanlardır ve tests/i18n_tr_only.json'da tek tek yazılıdır (alıcıya giden
   SMS / e-posta / sayfa, yönetim sayfası, adres kalıpları, günlük satırları, birleştirilen metnin parçaları …). Listede olmayan yeni bir
   Türkçe metin ya da listede kalıp kodda olmayan satır denemeyi düşürür. Listeyi yenilemek: I18N_WRITE=1 pytest tests/test_i18n_en.py
2. İngilizce kolda Türkçe harf yok (özel adlar hariç).
3. Uç uca: X-Lang: en ile hata İngilizce, X-Lang yokken Türkçe; duyuru ve coin geçmişi okunurken İngilizce; bildirim cihazın dilinde.
"""
import ast
import json
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.i18n import en
from app.i18n_en import EN, FRAGMENTS

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
ALLOW = Path(__file__).with_name("i18n_tr_only.json")
TRL = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
WORDS = {"adres", "teslim", "kurye", "ilan", "hesap", "puan", "kod", "mesaj", "bildirim", "tabela", "reklam", "yok", "geçersiz", "bulunamadı",
         "gerekli", "olmalı", "değil", "yanlış", "bekle", "dene", "tekrar", "lütfen", "görev", "takip", "şifre", "giriş", "kayıt", "rehber"}
# Bu çağrıların içindeki metin kullanıcıya gitmez (günlük, yönetim kaydı, kalıp, ortam değişkeni)
SKIP_CALLS = {"print", "audit", "join", "compile", "sub", "_env", "getenv", "quote", "input", "ArgumentParser", "NamedTemporaryFile",
              "RuntimeError", "_key", "log", "startswith", "endswith", "replace", "split", "get"}


def is_tr(s: str) -> bool:
    if TRL.search(s):
        return True
    ws = re.findall(r"[a-zçğıöşü]+", s.lower())
    return sum(1 for w in ws if w in WORDS) >= 1 and len(ws) >= 2


def sample_of(n: ast.AST) -> tuple[str, str] | None:
    """(kaynak metin, örnek) — f-string'de değişken yerleri {ifade} / "7" """
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value, n.value
    if isinstance(n, ast.JoinedStr):
        src, smp = [], []
        for v in n.values:
            if isinstance(v, ast.Constant):
                src.append(str(v.value))
                smp.append(str(v.value))
            else:
                src.append("{" + ast.unparse(v.value) + "}")
                smp.append("7")
        return "".join(src), "".join(smp)
    return None


def scan() -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for f in sorted(APP.glob("*.py")):
        # i18n kendisi + yalnız yönetimin gördüğü / komut satırı dosyaları (yönetim sayfası Türkçe)
        if f.name in ("i18n.py", "i18n_en.py", "admin.py", "backup.py", "backup_core.py", "restore.py", "events.py", "doormap.py", "migrate.py"):
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        docs = {n.body[0].value for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for n in ast.walk(tree):
            if n in docs or (isinstance(n, ast.Constant) and isinstance(parents.get(n), ast.JoinedStr)):
                continue
            got = sample_of(n)
            if got is None or not is_tr(got[0]) or len(got[0].strip()) <= 2:
                continue
            q, call = parents.get(n), ""
            while q is not None and not isinstance(q, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                if isinstance(q, ast.Call):
                    fn = q.func
                    call = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
                    break
                q = parents.get(q)
            if call in SKIP_CALLS:
                continue
            out.setdefault(f.name, []).append(got)
    return out


def en_has_turkish(s: str) -> bool:
    t = re.sub(r"[A-ZÇĞİÖŞÜ][A-Za-zçğıöşüÇĞİÖŞÜ]*", "", s)  # özel adlar (Türkiye, Kestel, GRV …)
    return bool(TRL.search(t))


def test_every_turkish_text_has_english():
    found = scan()
    if os.environ.get("I18N_WRITE"):
        keep = {f: sorted({src for src, smp in items if en(smp) == smp}) for f, items in found.items()}
        ALLOW.write_text(json.dumps({f: v for f, v in keep.items() if v}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    allow = json.loads(ALLOW.read_text(encoding="utf-8"))
    missing, stale, bad_en = [], [], []
    for f, items in found.items():
        ok = set(allow.get(f, []))
        for src, smp in items:
            tr = en(smp)
            if src in ok:
                continue
            if tr == smp:
                missing.append(f"{f}: {src[:100]!r}")
            elif en_has_turkish(tr):
                bad_en.append(f"{f}: {tr[:100]!r}")
    for f, texts in allow.items():
        now = {src for src, _ in found.get(f, [])}
        stale += [f"{f}: {t[:80]!r}" for t in texts if t not in now]
    assert not missing, "İngilizcesi olmayan Türkçe metin:\n" + "\n".join(missing)
    assert not bad_en, "İngilizcede Türkçe kalmış:\n" + "\n".join(bad_en)
    assert not stale, "i18n_tr_only.json'da olup kodda olmayan (listeden sil):\n" + "\n".join(stale)
    assert sum(len(v) for v in allow.values()) > 50  # alıcı / yönetim / kalıp metinleri gerçekten listede


def test_english_table_is_clean():
    for tr, e in {**EN, **FRAGMENTS}.items():
        assert e.strip(), tr
        assert not en_has_turkish(e), (tr, e)
        assert set(re.findall(r"\{(\w+)\}", tr)) == set(re.findall(r"\{(\w+)\}", e)), (tr, e)


def test_composed_messages():
    # Birleştirilerek kurulan metinler (parçaları ayrı ayrı listede)
    assert en("Hesabınız askıya alındı. İtiraz için destek ile iletişime geçin.") == "Your account has been suspended. To appeal, contact support."
    assert en("Hesabınız askıya alındı. Neden: Topluluk kurallarına aykırı içerik (şikâyet üzerine). İtiraz için destek ile iletişime geçin.") == (
        "Your account has been suspended. Reason: Content against the community rules (after a report). To appeal, contact support.")
    assert en("Hesabınız askıya alındı. Neden: Sahte ilan. İtiraz için destek ile iletişime geçin.").startswith("Your account has been suspended. Reason: Sahte ilan.")
    r = en("Ödüllerin şu an katalogda gösterilmiyor (Son 90 günde 2 ödül 7 gün içinde kargolanmadı; Son 90 günde 3 “almadım” şikâyeti). "
           "Yeni ödül de eklenemez. Durum son 90 gündeki kayıtlar düzelince kendiliğinden kalkar; yönetimle de görüşebilirsin.")
    assert r.startswith("Your rewards aren’t shown in the catalog right now (2 rewards weren’t shipped") and "3 “not received” complaints" in r
    assert en("Çok fazla yanlış şifre denendi. Güvenliğin için giriş 2 saat kapalı. Şifreni unuttuysan “Şifremi unuttum”u kullan.").endswith(
        "sign-in is closed for 2 hours. If you forgot your password, use “Forgot password”.")
    assert en("Rota coin satın alma · 200 (App Store, deneme)") == "Rota coin purchase · 200 (App Store, test)"
    assert en("Doğrulanmış teslim (alıcı onayı)") == "Verified delivery (recipient confirmation)"
    assert en("Kurye “Kahve” ürününü aldığını bildirdi (memnuniyet 5/5).") == "The courier reported receiving “Kahve” (satisfaction 5/5)."
    for k in ("ad", "job", "card"):
        from app.reports import WARN_CONTENT
        assert not en_has_turkish(en(WARN_CONTENT[k])) and en(WARN_CONTENT[k]) != WARN_CONTENT[k]
    assert en("Bu ilan zaten Ali kuryesine bağlı (GRV-12). Bir ilan aynı anda tek kuryeye verilir.").startswith("This listing is already assigned to courier Ali (GRV-12).")
    assert en("İlçe en fazla 60 karakter") == "District can be at most 60 characters"
    assert en("Kimsenin bilmediği metin") == "Kimsenin bilmediği metin"


def _register(client, phone, role="courier", name="Deneme Kişi", lang=None):
    h = {"X-Lang": lang} if lang else {}
    r = client.post("/v1/auth/register", json={"phone": phone, "password": "gizli-sifre-1", "name": name, "role": role, "consent": True}, headers=h)
    return r


def test_end_to_end_language(tmp_path, monkeypatch):
    from app.main import app
    with TestClient(app) as c:
        # Türkçe (başlık yok) ve İngilizce hata
        r_tr = c.post("/v1/auth/login", json={"login": "", "password": "x"})
        r_en = c.post("/v1/auth/login", json={"login": "", "password": "x"}, headers={"X-Lang": "en"})
        assert r_tr.status_code == r_en.status_code
        assert r_tr.json()["detail"] == "Telefon veya kullanıcı adını yaz"
        assert r_en.json()["detail"] == "Enter your phone or username"
        r = c.post("/v1/auth/login", json={"login": "05000000000", "password": "yanlis-sifre"}, headers={"X-Lang": "en"})
        assert r.json()["detail"] == "Wrong phone / username or password"
        # 422 doğrulama hatası biçimi bozulmadı
        assert c.post("/v1/auth/login", json={}, headers={"X-Lang": "en"}).status_code == 422


def test_stored_texts_and_push_follow_language(monkeypatch):
    """Duyuru ve coin geçmişi Türkçe saklanır, okunurken uygulamanın dilinde döner; bildirim cihazın kayıtlı dilinde gider."""
    import uuid

    from app import pushes
    from app.account import add_coins
    from app.db import SessionLocal
    from app.main import app, push_router
    from app.models import Notice, uid
    from test_auth_consent import auth, reg

    c = TestClient(app)
    r = reg(role="employer", name="Dil Deneme")
    tok, me = r.json()["token"], r.json()["user"]["id"]
    db = SessionLocal()
    try:
        db.add(Notice(id=uid(), user_id=me, kind="report.done", text="🛡️ Şikâyetin incelendi. ROTA'yı güvenli tuttuğun için teşekkürler.", ref=""))
        add_coins(db, me, 50, "buy", "Rota coin satın alma · 50 (Google Play)")
        db.commit()
    finally:
        db.close()
    tr = c.get("/v1/notices", headers=auth(tok)).json()["items"]
    en_ = c.get("/v1/notices", headers={**auth(tok), "X-Lang": "en"}).json()["items"]
    assert tr[0]["text"].startswith("🛡️ Şikâyetin incelendi.")
    assert en_[0]["text"] == "🛡️ Your report was reviewed. Thank you for keeping ROTA safe."
    hist = c.get("/v1/coins", headers={**auth(tok), "X-Lang": "en"}).json()["history"]
    assert any(h["label"] == "Rota coin purchase · 50 (Google Play)" for h in hist)
    assert any(h["label"] == "Rota coin satın alma · 50 (Google Play)" for h in c.get("/v1/coins", headers=auth(tok)).json()["history"])

    # Bildirim: iki cihaz, biri English
    sent = []
    monkeypatch.setattr(pushes, "EXPO_TRANSPORT", [lambda url, payload: (sent.extend(payload) if url != pushes.EXPO_RECEIPTS_URL else None) or {"data": [{"status": "ok", "id": "t"} for _ in payload]}])
    monkeypatch.setattr(pushes, "SYNC", [True])
    pushes.reset_memory()
    for lim in push_router.limiters:
        lim.reset()
    t_tr, t_en = f"ExponentPushToken[{uuid.uuid4().hex[:22]}]", f"ExponentPushToken[{uuid.uuid4().hex[:22]}]"
    assert c.post("/v1/me/push", json={"kind": "expo", "token": t_tr, "deviceId": "dev-" + uuid.uuid4().hex[:12]}, headers=auth(tok)).status_code == 200
    assert c.post("/v1/me/push", json={"kind": "expo", "token": t_en, "deviceId": "dev-" + uuid.uuid4().hex[:12]}, headers={**auth(tok), "X-Lang": "en"}).status_code == 200
    r = c.post("/v1/me/push/test", headers={**auth(tok), "X-Lang": "en"})
    assert r.status_code == 200, r.text
    body = {m["to"]: m["body"] for m in sent}
    assert body[t_tr] == "🔔 Bildirimler açık. Bu bir deneme bildirimidir."
    assert body[t_en] == "🔔 Notifications are on. This is a test notification."
    pushes.reset_memory()
