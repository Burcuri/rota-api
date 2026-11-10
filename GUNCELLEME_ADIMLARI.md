# Sunucu güncellemesi — Dilim 40 · TAM İNGİLİZCE (hata, duyuru, bildirim, e-posta, geçmiş) (10 Kasım 2026) — sunucu 1.30.0

**Dilim 39'u yükleyip yüklemediğin fark etmez:** aşağıdaki liste **Dilim 37'den bu yana değişen BÜTÜN dosyaları** içerir (Dilim 38 + 39 + 40);
hepsini yükle, aynı adlı dosyanın üzerine yazılır. Yeni paket YOK, yeni ortam değişkeni YOK, Render'da değişiklik YOK. Yeni sütun
`user_push.lang` (bildirimin dili) sunucu açılırken kendiliğinden eklenir. Ayrıntı: uygulama paketindeki `docs/DILIM-40.md`.
- **`app/i18n.py` (YENİ)**, **`app/i18n_en.py` (YENİ)**: TAM İNGİLİZCE (kullanıcı isteği 10 Kasım: "bütün tercüme İngilizce Türkçe karşılığın
  tam yapıldığından emin ol"). Sunucu metinleri Türkçe yazar ve Türkçe saklar; uygulamanın dili English ise (uygulama her istekte `X-Lang`
  gönderir) çıkışta İngilizceye çevrilir: **hata mesajları** (~330), **duyurular** (eskiler de), **coin ve puan geçmişi**, **anket soruları ve
  birimleri**, **ödül karnesi nedenleri**, **e-postalar** (şifre kodu, şifre / telefon değişti, hesap silindi) ve **SMS kodu**.
  **Telefon / tarayıcı bildirimi** cihazın diline göre gider (`app/pushes.py`; dil değişince uygulama cihazı yeniden kaydeder).
  Türkçe KALANLAR (bilerek): alıcıya giden SMS / e-posta / sayfa, yönetim sayfası, yönetimin elle yazdığı neden ve notlar, kişilerin
  yazdığı içerik. Yeni deneme `tests/test_i18n_en.py`: kaynaktaki her Türkçe kullanıcı metninin İngilizcesi var mı (bilerek Türkçe
  kalanlar `tests/i18n_tr_only.json`'da tek tek).
- **`app/main.py`** (sürüm 1.30.0; dil ara katmanı, hata metni İngilizce), **`app/account.py`**, **`app/points.py`**, **`app/security.py`**,
  **`app/standing.py`**, **`app/surveys.py`**, **`app/tasks.py`**, **`app/models.py`**, **`app/migrate.py`**, **`app/pushes.py`**,
  **`app/webapp/`** (web ikizi 0.36.0: bütün ekranlar İngilizce).
- **Dilim 39'dan:** `app/reports.py` (içerik şikâyeti: tabela, ilan, işletme kartı), `app/bizcard.py`, `app/static/yonetim.js`.
- **Dilim 38'den:** `app/blocks.py` (YENİ), `app/admin.py`, `app/backup.py`, `app/backup_core.py`, `app/events.py`, `app/follow.py`,
  `app/iap.py`, `app/nearby.py`, `app/relay.py`, `app/static/yonetim.css`, `.github/workflows/yedek.yml`.

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` ve `app/static` **klasörlerini** sürükle → Commit.
   (79 dosya; bir seferde en çok 100 dosya yüklenebildiği için .py dosyaları ayrı adımda.)
3. Yine `app` klasöründe **Upload files** → zip'teki şu **23 dosyayı** birlikte sürükle: `account.py`, `admin.py`, `backup.py`,
   `backup_core.py`, `bizcard.py`, `blocks.py`, `events.py`, `follow.py`, `i18n.py`, `i18n_en.py`, `iap.py`, `main.py`, `migrate.py`,
   `models.py`, `nearby.py`, `points.py`, `pushes.py`, `relay.py`, `reports.py`, `security.py`, `standing.py`, `surveys.py`, `tasks.py` → Commit.
4. `tests` klasörüne gir → **Upload files** → zip'teki `tests/i18n_tr_only.json`, `tests/test_backup.py`, `tests/test_content_reports.py`,
   `tests/test_i18n_en.py`, `tests/test_iap_refunds.py`, `tests/test_reports.py` → Commit.
5. Ana klasöre dön → **Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit. `.github` → `workflows` klasörüne gir → **Upload files** →
   zip'teki `.github/workflows/yedek.yml` → Commit (Dilim 38'de yaptıysan atla).
6. Render › **Manual Deploy → Deploy latest commit** → **Live**. Deneme: `https://rota-sunucu.onrender.com/openapi.json` → en başta
   `"version":"1.30.0"`. Uygulamada Hesabım › Profil · Dil → English: yanlış şifreyle giriş "Wrong phone / username or password" der.
   "Deploy failed" olursa: Render › **Logs** → kırmızı satırların sonuncusunda geçen `app/….py` dosyasını yeniden yükle.

---

# Sunucu güncellemesi — Dilim 39 · İÇERİK ŞİKÂYETİ (harita tabelası, iş ilanı, işletme kartı) + Dilim 38'in eksik dosyaları (9 Kasım 2026) — sunucu 1.29.0

**Render'daki "Deploy failed" (`app/main.py`, 50. satır `from .reports import …`) bununla düzelir.** GitHub'a Dilim 38'in bazı dosyaları
(en az `app/reports.py`) gitmemiş; `main.py` onları arıyor. Aşağıdaki liste **Dilim 37'den bu yana değişen BÜTÜN dosyaları** içerir
(Dilim 38 + 39) — hepsini yükle; aynı adlı dosyanın üzerine yazılır, zararı yok. Yeni paket YOK, yeni ortam değişkeni YOK, Render'da
değişiklik YOK. `reports` tablosu ve `reports.ref` sütunu sunucu açılırken kendiliğinden kurulur. Ayrıntı: uygulama paketindeki `docs/DILIM-39.md`.
- **`app/reports.py`**: İÇERİK ŞİKÂYETİ (kullanıcı isteği 9 Kasım: "Diğer yerlere de Şikâyet et … tabelalara şikâyet edebilmek için tıklamak
  ve gelen bir pop up olsun"): harita tabelası, iş ilanı, işletme kartı. İçeriğin şikâyet anındaki hâli sunucunun kendi kaydından alınır.
  Yönetim › Şikâyetler'de yeni karar: **Tabelayı kaldır** (aynı paketin bütün noktaları; coin iade edilmez) / **İlanı kaldır** (bağlı görev
  ve canlı takip de biter) / **Logoyu kaldır** (işletme kartı) — sahibine duyuru gider, aynı içerik hakkındaki bütün açık şikâyetler kapanır.
- **`app/account.py`** (`/v1/ads/active`), **`app/main.py`** (`/v1/jobs/open`; sürüm 1.29.0), **`app/bizcard.py`**: engellediğin (ya da seni
  engelleyen) işletmenin / sponsorun tabelaları, ilanları ve kartı sana gösterilmez; şikâyet ettiğin tabela ve ilan da.
- **`app/models.py`**, **`app/migrate.py`** (`reports.ref`), **`app/static/yonetim.js`** (içerik kartı: yazı, logo, yayında mı; kaldır
  düğmesi), **`app/webapp/`** (web ikizi 0.35.0: tabela penceresi, İngilizce Yardım).
- **Dilim 38'den (GitHub'da eksik):** `app/blocks.py` (YENİ), `app/reports.py` (YENİ), `app/admin.py`, `app/backup.py`, `app/backup_core.py`,
  `app/events.py`, `app/follow.py`, `app/iap.py`, `app/nearby.py`, `app/relay.py`, `app/tasks.py`, `app/static/yonetim.css`.

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` ve `app/static` klasörlerini ve şu 16 dosyayı birlikte
   sürükle: `account.py`, `admin.py`, `backup.py`, `backup_core.py`, `bizcard.py`, `blocks.py`, `events.py`, `follow.py`, `iap.py`, `main.py`,
   `migrate.py`, `models.py`, `nearby.py`, `relay.py`, `reports.py`, `tasks.py` → Commit. (70 dosya, sınır 100.)
3. `tests` klasörüne gir → **Upload files** → zip'teki `tests/test_backup.py`, `tests/test_content_reports.py`, `tests/test_iap_refunds.py`,
   `tests/test_reports.py` → Commit.
4. Ana klasöre dön → **Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit.
5. (İsteğe bağlı; gece yedeğindeki sarı "Node.js 20" uyarısı için) Kalemle düzeltmek yerine dosyayı yükle: GitHub'da `.github` → `workflows`
   klasörüne gir → **Add file → Upload files** → zip'teki `.github/workflows/yedek.yml` → Commit. (Atlarsan da yedek çalışır.)
6. Render › **Manual Deploy → Deploy latest commit** → **Live**. Deneme: `https://rota-sunucu.onrender.com/openapi.json` → en başta
   `"version":"1.29.0"`; yönetim panelinde **Şikâyetler** sekmesi. Yine "Deploy failed" olursa: Render › **Logs** → kırmızı satırların
   sonuncusunda geçen `app/….py` dosyasını yeniden yükle.

---

# Sunucu güncellemesi — Dilim 38 · sohbette ŞİKÂYET + ENGEL (mağaza şartı), mağaza İADESİ, yedek işi Node 24 (9 Kasım 2026) — sunucu 1.28.0

**Dilim 37'yi yüklediysen aşağıdakiler yeter.** Yeni `reports` tablosu ve `store_purchases.refunded_at` sütunu sunucu açılırken kendiliğinden
kurulur. Yeni paket YOK, yeni ZORUNLU ortam değişkeni YOK. Ayrıntı: uygulama paketindeki `docs/DILIM-38.md`.
- **`app/reports.py` (YENİ)**: şikâyet (kullanıcı isteği 9 Kasım: "Sohbete Şikâyet et + Engelle … lütfen bunu bitir"; App Store 1.2 ve
  Google Play UGC şartı). Uygulama şikâyet ettiğin mesajı ve o kişinin bu sohbetteki son mesajlarını (en çok 6) yollar — mesajlar sunucuda
  tutulmadığı için. Yönetim › **Şikâyetler** sekmesi: İşlem yok / Uyar / Hesabı kapat. Şikâyet edene "incelendi", şikâyet edilene (uyarıda)
  uyarı duyurusu gider; kimin şikâyet ettiği söylenmez. Kapatılan şikâyet 180 gün sonra silinir. `DESTEK_EPOSTA` + e-posta bağlıysa
  yeni şikâyet o adrese haber verir (içerik yazmaz).
- **`app/blocks.py` (YENİ)**: engel HER YERDE: engellediğin kişiyle birbirinize mesaj ulaşmaz (birebir ve ortak grup), haritada görünmezsiniz,
  selam / canlı takip isteği / adres listesi gönderilemez, açık takip ve görev kapanır. Engellenene söylenmez. (`app/relay.py`,
  `app/nearby.py`, `app/follow.py`, `app/tasks.py`)
- **`app/iap.py`**: mağaza İADESİ — Google Play'in iptal ettiği / iade ettiği satın alma 6 saatte bir okunur (sunucu uyanıkken; yönetimde
  "şimdi denetle" düğmesi), App Store'un iade bildirimi `/v1/iap/apple/notify`'a gelir ve Apple'dan yeniden doğrulanır → coin BİR KEZ düşülür,
  kişiye duyuru gider. Mağaza anahtarları yokken bir şey yapmaz. Yönetim › Özet › **Mağaza satışları** kutusu.
- **`app/backup.py`, `app/backup_core.py`**: yedek kapalıysa nedeni yazar: anahtar yok mu, yoksa 24 karakterden kısa mı (8 Kasım'daki
  "BACKUP_KEY yok" karışıklığı).
- **`app/admin.py`** (Özet'te açık şikâyet ve en eskisinin yaşı), **`app/events.py`**, **`app/main.py`** (sürüm 1.28.0), **`app/migrate.py`**,
  **`app/models.py`**, **`app/static/yonetim.js`**, **`app/static/yonetim.css`**, **`app/webapp/`** (web ikizi 0.34.0).
- **`.github/workflows/yedek.yml`**: `actions/upload-artifact@v4` → `@v6` ("Node.js 20 is deprecated" uyarısı gider).

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` ve `app/static` klasörlerini ve `app/admin.py`,
   `app/backup.py`, `app/backup_core.py`, `app/blocks.py`, `app/events.py`, `app/follow.py`, `app/iap.py`, `app/main.py`, `app/migrate.py`,
   `app/models.py`, `app/nearby.py`, `app/relay.py`, `app/reports.py`, `app/tasks.py` dosyalarını birlikte sürükle → Commit. (69 dosya, sınır 100.)
3. `tests` klasörüne gir → **Upload files** → zip'teki `tests/test_backup.py`, `tests/test_iap_refunds.py`, `tests/test_reports.py` → Commit.
4. Ana klasöre dön → **Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit.
5. `.github/workflows/yedek.yml` dosyasını aç → ✏️ (kalem) → `upload-artifact@v4` yazısını `upload-artifact@v6` yap → **Commit changes**.
   (İstersen atla: eski hâli de çalışır, yalnız sarı uyarı verir.)
6. Render › **Manual Deploy → Deploy latest commit** → **Live**. Deneme: `https://rota-sunucu.onrender.com/openapi.json` → en başta
   `"version":"1.28.0"`; yönetim panelinde **Şikâyetler** sekmesi ve Özet'te **Mağaza satışları** kutusu ("bağlı değil", "Test satın alma:
   AÇIK"). Render'da değişiklik YOK.

---

# Sunucu güncellemesi — Dilim 37 · reklam TEK ÜRÜN (harita tabelası, nokta başına ayda 1 coin), SMS kodu hazır (Netgsm / İletimerkezi / Verimor) (7 Kasım 2026) — sunucu 1.27.0

**Dilim 35'i yüklediysen aşağıdakiler yeter — Dilim 36'yı yüklemediysen de** (bu zip öncekilerin hepsini içerir; 36'nın dosyaları listede).
Dilim 36'nın beş YENİ tablosu ve `ad_bookings.reminded_at` sütunu (yoksa) sunucu açılırken kendiliğinden kurulur; Dilim 37'de yeni tablo /
sütun YOK. Yeni paket YOK. Ayrıntı: uygulama paketindeki `docs/DILIM-37.md`.
- **`app/account.py`**: TEK ÜRÜN (kullanıcı kararı: "O noktada 1 gün durması da 1 ay durmasıda 1 coin (≈ 10 TL)") — haritada 1 nokta = logolu
  tabela, nokta başına ayda 1 coin. İşletme: İşletme Profili'ndeki konumda tek tabela. Sponsor: bir seferde 1–10 nokta (hepsi bir grup),
  yazı isteğe bağlı; aynı anda en çok 50 tabela; kendi tabelalarından en az 30 m uzak. "Uzat": her tabela bir ay daha, nokta başına 1 coin.
  ~~nokta·gün 3 coin, 1 / 7 / 30 gün, paketler~~ kalktı; eski kayıtlar süresi bitene kadar yayında, uzatılınca onlar da aylık.
- **`app/notify.py`**: SMS bağlantıları HAZIR (Netgsm, İletimerkezi, Verimor) — anahtar yazılmadıkça KAPALI (bugünkü gibi deneme modu).
  Kurulum: aşağıda **"SMS ve e-posta"** bölümü. **`app/handoff.py`**: teslim bağlantısı SMS'i ayrı anahtarla (`SMS_LINKS=1`) ve günlük tavanla.
  **`app/security.py`**: yönetimde hangi SMS sağlayıcısının bağlı olduğu yazar (anahtar değil, ad).
- **`app/pushes.py`** ("🪧 Tabelan yarın bitiyor"), **`app/main.py`** (sürüm 1.27.0), **`app/static/yonetim.js`** (Kodlar'da SMS satırı,
  Reklamlar'da "Tabela"), **`app/webapp/`** (web ikizi 0.33.0).
- Dilim 36'dan (yüklemediysen): `app/backup.py`, `app/backup_core.py`, `app/restore.py`, `app/surveys.py` (YENİ), `app/models.py`, `app/migrate.py`.

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` ve `app/static` klasörlerini ve `app/account.py`,
   `app/backup.py`, `app/backup_core.py`, `app/handoff.py`, `app/main.py`, `app/migrate.py`, `app/models.py`, `app/notify.py`,
   `app/pushes.py`, `app/restore.py`, `app/security.py`, `app/surveys.py` dosyalarını birlikte sürükle → Commit. (67 dosya, sınır 100.)
3. `tests` klasörüne gir → **Upload files** → zip'teki `tests/test_account.py`, `tests/test_ads_extra.py`, `tests/test_backup.py`,
   `tests/test_logos.py`, `tests/test_pushes.py`, `tests/test_relay_jobs.py`, `tests/test_sms.py`, `tests/test_surveys.py` → Commit.
4. Ana klasöre dön → **Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit.
5. **Render › rota-sunucu › Environment:** `BACKUP_KEY`'i eklediysen (Generate) → **Save Changes**. Eklemediysen şimdi ekle:
   *Add Environment Variable* → Key `BACKUP_KEY` → **Generate** → *Save Changes*. Değeri ayrıca parola yöneticine not et (anahtarsız yedek
   açılmaz). Zip'e, GitHub'a, sohbete yazılmaz.
6. Render › **Manual Deploy → Deploy latest commit** → **Live**. Deneme: tarayıcıda `https://rota-sunucu.onrender.com/openapi.json` → en
   başta `"version":"1.27.0"`; uygulamada Katalog'da tek ürün
   "Harita tabelası · 1 nokta · 1 ay"; yönetim › Kodlar'ın altında "SMS: bağlı değil (deneme modu)" (sağlayıcı yazılana kadar böyle).
- **ERTELENDİ (kullanıcı kararı 7 Kasım: "o uzun iş en son bakılsın"):** gece yedeğinin GitHub tarafı — `.github/workflows/yedek.yml`
  dosyası ve GitHub'daki `BACKUP_KEY` gizli değeri. İkisi birlikte en sonda eklenir (yalnız biri eklenirse gece işi hata verir). O zamana
  kadar yedek yönetim › Özet › **Şimdi yedek indir** ile elle alınır.

---

# Sunucu güncellemesi — Dilim 36 · reklam (tabela, istatistik, uzat, logolu işaret, fiyatlar ÷10), anketler sunucuda, veritabanı yedeği (7 Kasım 2026) — sunucu 1.26.0

**Dilim 35'i yüklediysen aşağıdakiler yeter** (bu zip öncekilerin hepsini içerir). **Beş YENİ tablo** (`ad_stats`, `ad_seen`, `surveys`,
`survey_answers`, `backup_runs`) ve `ad_bookings` tablosuna bir YENİ sütun (`reminded_at`) sunucu açılırken kendiliğinden kurulur; eski
kayıtlara dokunulmaz. Yeni paket YOK. Ayrıntı: uygulama paketindeki `docs/DILIM-36.md`.
- **`app/account.py`**: FİYATLAR (1 coin ≈ 10 TL; harita reklamı nokta·gün 3 coin, öne çıkan ilan günü 2 coin, TABELA ayda 1 coin) · TABELA
  (işletme: İşletme Profili'ndeki konum, sponsor: bir kez seçilen yer; aynı anda tek) · **Uzat** (`POST /v1/ads/{id}/extend`) · **istatistik**
  (`POST /v1/ads/seen`; yalnız sayı) · bitişe 1 gün kala **hatırlatma** · kuryenin haritasında **logo** · ilan listesinde 🪧.
- **`app/surveys.py`** (YENİ): anketler sunucuda — yönetim açar / arşive alır (silinmez), kurye yanıtlar, ortalama en az 3 yanıtla, ilçe
  ortalaması, CSV'de kişi yok. **`app/backup.py`, `app/backup_core.py`, `app/restore.py`** (YENİ): şifreli veritabanı yedeği + geri yükleme.
- **`app/pushes.py`** ("📍 Reklamın yarın bitiyor"), **`app/models.py`**, **`app/migrate.py`**, **`app/main.py`** (sürüm 1.26.0),
  **`app/security.py`** (hesap silinince reklam sayıları / anket yanıtı adsız kalır).
- **`app/static/yonetim.js`**: **Anketler** sekmesi, Özet'te **Veritabanı yedeği** kutusu, Reklamlar'da tür + görülme sayısı.
  **`app/static/web-sw.js`**: bildirime dokununca doğru ekran. **`app/webapp/`** (web ikizi 0.32.0).

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` ve `app/static` klasörlerini ve `app/account.py`,
   `app/backup.py`, `app/backup_core.py`, `app/main.py`, `app/migrate.py`, `app/models.py`, `app/pushes.py`, `app/restore.py`,
   `app/security.py`, `app/surveys.py` dosyalarını birlikte sürükle → Commit. (Bu yükleme 65 dosya, sınır 100.)
3. `tests` klasörüne gir → **Upload files** → zip'teki `tests/test_account.py`, `tests/test_ads_extra.py`, `tests/test_backup.py`,
   `tests/test_logos.py`, `tests/test_pushes.py`, `tests/test_relay_jobs.py`, `tests/test_surveys.py` → Commit.
4. **Gece yedeği işi:** ana klasörde **Add file → Create new file** → ad kutusuna `.github/workflows/yedek.yml` yaz → zip'teki aynı
   dosyanın içeriğini yapıştır → Commit. (Windows'ta `.github` gizli görünebilir: Gezgin › Görünüm › Göster › Gizli öğeler.)
5. Ana klasöre dön → **Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit.
6. **Yedek anahtarı (bir kez):** Render › **rota-sunucu** › **Environment** › *Add Environment Variable* → Key: `BACKUP_KEY` → **Generate**
   → *Save Changes*. Sonra değeri göz simgesiyle aç, kopyala → GitHub › rota-api › **Settings › Secrets and variables › Actions** →
   **New repository secret** → Name: `BACKUP_KEY`, Secret: yapıştır → *Add secret*. Anahtar zip'e, koda, sohbete yazılmaz. Anahtarsız yedek
   açılamaz: değeri ayrıca güvenli bir yere (parola yöneticisi) de not et.
7. Render › **Manual Deploy → Deploy latest commit** → **Live** olunca:
   - yönetim › Özet › **Veritabanı yedeği** › *Şimdi yedek indir* → `rota-yedek-….rotayedek` iner (şifreli);
   - GitHub › **Actions** › **Veritabanı yedeği** › *Run workflow* → yeşil ✓ → işin sayfasında altta **Artifacts**: yedek dosyası (30 gün durur).
     Sonra her gece 03:17'de kendiliğinden alınır; yönetimde "Son gece yedeği" yazar (36 saati geçerse uyarır).
- **Geri yükleme (gerekirse; bugün gerekmez):** yeni BOŞ bir veritabanı aç (ör. yeni Supabase projesi). Bilgisayarda sunucu klasöründe:
  `pip install -r requirements.txt`, sonra ortamda `BACKUP_KEY` (Render'daki değer) ve `DATABASE_URL` (YENİ veritabanının adresi) varken
  `python -m app.restore rota-yedek-….rotayedek --check` (yalnız açar, sayıları yazar) → `python -m app.restore rota-yedek-….rotayedek`
  (boş veritabanına yazar; dolu veritabanına yazmaz) → Render'daki `DATABASE_URL`'i yenisiyle değiştir. Kapı arşivi dahil her şey içindedir.
- **İsteğe bağlı:** sunucu adresi farklıysa GitHub › Settings › Secrets and variables › Actions › **Variables** › `SERVER_URL` (keepalive ile aynı).

---

# Sunucu güncellemesi — Dilim 35 · ödül kataloğu + barkod, grup haritası, sponsora bildirim, mağaza satın alması (6 Kasım 2026) — sunucu 1.25.0

**Dilim 34'ü yüklediysen aşağıdakiler yeter** (bu zip öncekilerin hepsini içerir). **İki YENİ tablo** (`reward_photos`, `store_purchases`)
sunucu açılırken kendiliğinden kurulur; eski tablolara dokunulmaz. Yeni paket YOK. Ayrıntı: uygulama paketindeki `docs/DILIM-35.md`.
- **`app/points.py`**: KARGO KALKTI (kullanıcı kararı "Yalnız barkod"). Kurye ürünü alınca barkod (30 gün geçerli) → ödülü koyana bildirim →
  `POST /v1/rewards/redeem` ile okutulur (önce `check` ile gösterir, sonra verir; TEK KULLANIMLIK) → kurye "Teslim aldım" + yıldız.
  Okutulmayan barkodda puan iade. Ürün başına 3 fotoğraf (`/v1/rewards/{id}/photos/{0-2}`); yönetim fotoğrafı silebilir.
- **`app/iap.py`** (YENİ): Rota coin Google Play / App Store satın alması — makbuz mağazanın sunucusundan doğrulanır, coin BİR KEZ yazılır.
  Mağaza anahtarları yoksa kapalı (bugünkü durum); test satın alma sürer.
- **`app/pushes.py`**: sponsorun telefonuna "🎁 Bir kurye ödül ürününüzü hak etti"; kuryeye "🎁 Barkodun okutuldu". **`app/relay.py`**: grup
  haritası noktası sessiz gider (bildirim yok). **`app/models.py`**: iki yeni tablo. **`app/main.py`**: sürüm 1.25.0.
  **`app/security.py`**, **`app/standing.py`**: kargo yerine barkod metinleri.
- **`app/static/yonetim.js`**: talep durumları barkodla; **Logolar ve fotoğraflar** sekmesinde ödül fotoğrafları + "Fotoğrafı sil"; üye
  sayfasında kuryeye **Kurye puanı** + "Puan düzelt" (destek ve ödül kataloğunu denemek için).
- **`app/webapp/`** (web ikizi 0.31.0): ödül kataloğu (sarı / lacivert, barkod), 📷 Barkod okut, grup haritası, sponsora bildirim.

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` ve `app/static` klasörlerini ve `app/iap.py`, `app/main.py`,
   `app/models.py`, `app/points.py`, `app/pushes.py`, `app/relay.py`, `app/security.py`, `app/standing.py` dosyalarını birlikte sürükle →
   Commit. (Bu yükleme 62 dosya, sınır 100.)
3. `tests` klasörüne gir → **Upload files** → zip'teki `tests/test_iap.py`, `tests/test_points.py`, `tests/test_pushes.py`,
   `tests/test_standing.py` → Commit.
4. Ana klasöre dön → **Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit.
5. Render › **rota-sunucu** › **Manual Deploy → Deploy latest commit** → **Live** olunca: yönetim › Üyeler › bir kurye › **Puan düzelt**
   +1000 → o kuryeyle Hesabım › Puan ve rozetler › 🎁 kartı: sarı ürün görünüyor mu.
- **İsteğe bağlı ortam değişkeni:** `REWARD_BARCODE_DAYS` (barkodun geçerlilik günü; yoksa 30).
- **Mağaza hesapları açılınca (bugün YOK):** Render › Environment: `GOOGLE_PLAY_SERVICE_ACCOUNT` (hizmet hesabının JSON anahtarı) ·
  `APPLE_IAP_ISSUER_ID`, `APPLE_IAP_KEY_ID`, `APPLE_IAP_PRIVATE_KEY` (.p8 içeriği). Ürün kimlikleri: `rota_coin_50`, `rota_coin_200`,
  `rota_coin_500` (tüketilebilir). Gerçek satışa geçince `COIN_TEST_BUY=0`. Bu anahtarlar zip'e, GitHub'a, sohbete konmaz.

---

# Sunucu güncellemesi — Dilim 34 · işletme kartı, işletme konumu her kuryeye (6 Kasım 2026) — sunucu 1.24.0

**Dilim 33'ü yüklediysen aşağıdakiler yeter** (bu zip öncekilerin hepsini içerir). Yeni tablo, sütun, paket, anahtar YOK; veritabanına
dokunulmaz. Ayrıntı: uygulama paketindeki `docs/DILIM-34.md`.
- **`app/bizcard.py`** (YENİ): `GET /v1/businesses/{id}` — işletme kartı (İşletme Profili'nde yazanlar; vergi bilgileri YOK; telefon,
  işletme kapattıysa yok). Kuryeler ve işletmenin kendisi görür. Kullanıcı kararı: "kuryeyle yazışmaya başlar başlamaz kurye işletmenin
  bilgilerini profilinde yazanları fotoğrafa tıklayarak görebilmeli."
- **`app/main.py`** (sürüm 1.24.0): ilanın başlama noktası her kuryeye gider ("işletme konumunu asla saklayamaz"); kart ucu bağlandı.
- **`app/static/yonetim.js`**: rıza satırı "Grup konumu (yalnız kurye; işletme konumu her zaman görünür)".
- **`app/webapp/`** (web ikizi 0.30.0): sohbette işletmenin fotoğrafı → işletme kartı; işletmede konum seçimi yerine bilgi notu.

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` ve `app/static` klasörlerini ve `app/bizcard.py`,
   `app/main.py` dosyalarını birlikte sürükle → Commit. (Bu yükleme 56 dosya, sınır 100.)
3. `tests` klasörüne gir → **Upload files** → zip'teki `tests/test_roles.py` → Commit.
4. Ana klasöre dön → **Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit.
5. Render › **rota-sunucu** › **Manual Deploy → Deploy latest commit** → **Live** olunca kurye hesabıyla bir işletmeye "Mesaj gönder" de,
   sohbetin üstündeki fotoğrafa dokun: işletme kartı açılıyor mu.

---

# Sunucu güncellemesi — Dilim 33 · işletme ve sponsor kuralları (3 Kasım 2026) — sunucu 1.23.0

**Dilim 32'yi yüklediysen aşağıdakiler yeter** (bu zip öncekilerin hepsini içerir). Yeni tablo, sütun, paket, anahtar YOK; veritabanına
dokunulmaz. Ayrıntı: uygulama paketindeki `docs/DILIM-33.md`.
- **`app/main.py`** (sürüm 1.23.0): ilanın **başlama noktası** (ilanın verildiği yer) kuryeye yalnız işletme "Konumumu kuryeler görsün"
  dediyse gider (rıza günlüğündeki son kayıt); ilan sahibi hep görür, başka işletme ve sponsor görmez. Eskiden her ilanda herkese
  gidiyordu. Sponsor kişilerden numarayla bulunamaz ve kişi araması yapamaz.
- **`app/relay.py`**: sponsor mesaj gönderemez, sponsora mesaj gönderilemez (grupta sponsor varsa o kişi atlanır). Kullanıcı kararı:
  "Grup chat ve yazışmaları da sponsora kapatalım."
- **`app/static/yonetim.js`**: üye ayrıntısında rıza satırı "Konum paylaşımı (kuryede grup arkadaşları, işletmede kuryeler)".
- **`app/webapp/`** (web ikizi 0.29.0): işletmede 📍 Reklamlarım + ROTA COİN sayacı → coin ekranı, rolüne göre metinler, sponsorda yalnız
  sesle yazma.

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` ve `app/static` klasörlerini ve `app/main.py`,
   `app/relay.py` dosyalarını birlikte sürükle → Commit. (Bu yükleme 56 dosya, sınır 100.)
3. `tests` klasörüne gir → **Upload files** → zip'teki `tests/test_roles.py` → Commit.
4. Ana klasöre dön → **Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit.
5. Render › **rota-sunucu** › **Manual Deploy → Deploy latest commit** → **Live** olunca web ikizinde işletme hesabıyla Hesabım'a bak:
   üstte 📍 Reklamlarım, ROTA COİN sayacına dokununca coin ekranı açılıyor mu.

---

# Sunucu güncellemesi — Dilim 32 · kapı arşivi Türkiye geneli (3 Kasım 2026) — sunucu 1.22.0

**Dilim 31'i yüklediysen aşağıdakiler yeter** (bu zip öncekilerin hepsini içerir). Yeni tablo, sütun, paket, anahtar YOK; veritabanına
dokunulmaz. Ayrıntı: uygulama paketindeki `docs/DILIM-32.md`.
- **Yeni `app/places.py` + `app/data/`** (resmi liste: 81 il, 973 ilçe, ~32 bin mahalle, ~18 bin köy — TürkiyeAPI 2025, kaynak TÜİK;
  MIT lisansı `app/data/TURKIYEAPI-LICENSE.txt`). Kapı haritasına gelen her kayıt düzgün yazılır: il / ilçe / mahalle RESMİ adla
  ("alanya" → "Alanya", "kestel mah." → "Kestel Mahallesi"; il yazılmamışsa ilçeden bulunur; "Alanya/Antalya" ayrılır), yol / site /
  kapı no düzgün ("atatürk cad." → "Atatürk Caddesi", "güneş sit." → "Güneş Sitesi", "12 D:3" → 12). Alıcı pininde iki yol birden
  yazılmışsa ("Barbaros Cad., Gül Sk.") kapı en özel yola yazılır.
- **`app/doors.py`, `app/doormap.py`, `app/main.py`** (sürüm 1.22.0). Yönetim › Kapı haritası: **Türkiye › İl › İlçe › Mahalle › Sokak**;
  resmi listede olmayan il / ilçe / mahalle ve ilinden çok uzak konum "⚠" ile işaretli.
- **`app/static/yonetim.js`, `yonetim.css`**: il düzeyi, işaretler, yakınlaşınca kapı numaraları haritada yazılı.
- **`app/webapp/`** (web ikizi 0.28.0): teslimde "Kapı haritasına yazıldı: … " satırı.

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp`, `app/static`, `app/data` klasörlerini ve `app/places.py`,
   `app/doors.py`, `app/doormap.py`, `app/main.py` dosyalarını birlikte sürükle → Commit. (Bu yükleme 60 dosya, sınır 100.)
3. `tests` klasörüne gir → **Upload files** → zip'teki `tests/test_places.py` ve `tests/test_doormap.py` → Commit.
4. Ana klasöre dön → **Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit.
5. Render › **rota-sunucu** › **Manual Deploy → Deploy latest commit** → **Live** olunca `https://rota-sunucu.onrender.com/yonetim` ›
   **Kapı haritası**: üstte "Türkiye", altında "İller" listesi görünüyor mu bak (kapı noktası yoksa liste boş, "henüz kapı noktası yok" yazar).

---

# Sunucu güncellemesi — Dilim 31 · büyük harf kuralı, web ikizi 0.27.1 (2 Kasım 2026) — sunucu 1.21.1 (Python aynı)

**Dilim 30'u yüklediysen yalnız iki klasör yeter** (bu zip öncekilerin hepsini içerir). Python, tablo, paket, anahtar değişmedi.
Ayrıntı: uygulama paketindeki `docs/DILIM-31.md`.
- **`app/webapp/`** (web ikizi) **0.27.1**: hiçbir ad, soyad, yer adı, not ya da adres tarifi küçük harfle başlamaz — yazarken
  kendiliğinden, ekranda da (eski / küçük harfli kayıt dahil). Girişteki kart artık "Merhaba Burcu Yıldırım".
- **`app/static/`**: yalnız `yonetim.js` (yönetimde adlar / yerler / notlar düzgün; "Ad" ve not kutularında yazarken büyük harf) ve
  `alici.js` (alıcı sayfasında adres düzgün; yorumda ilk harf büyük) değişti. Klasördeki öbür dosyalar aynı.

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` ve `app/static` klasörlerini birlikte sürükle → Commit.
   (Aynı adlı dosyaların üstüne yazılır; bu yükleme 54 dosya, sınır 100.)
3. Ana klasöre dön → **Add file → Upload files** → zip'teki `GUNCELLEME_ADIMLARI.md` → Commit.
4. Render › **rota-sunucu** › **Manual Deploy → Deploy latest commit** (kendiliğinden kurmuyor) → **Live** olunca
   `https://rota-sunucu.onrender.com/web/` aç: girişteki kartta ad büyük harfle mi bak.

---

# Sunucu güncellemesi — Dilim 30 · web ikizi 0.27.0 + Supabase ayarı (2 Kasım 2026) — sunucu 1.21.1

**Dilim 27'yi yüklediysen yalnız aşağıdaki 4 parça yeter** (bu zip öncekilerin hepsini içerir). Yeni tablo, sütun, paket, anahtar YOK.
Ayrıntı: uygulama paketindeki `docs/DILIM-30.md`.
- **`app/webapp/`** (web ikizi) 0.24.0'dan **0.27.0**'a yenilendi: Expo SDK 58 derlemesi; girişte "ROTA" altında küçük "Asistan";
  Yardım'daki izin ve bildirim SSS'leri yeni sıraya göre; tekrar eden cümleler çıktı (web ses paketi sorusu, "Adres okuma kapalı" uyarısı).
  Harita aynı (Leaflet + OpenStreetMap).
- **`app/db.py`**: veritabanı artık **Supabase** ("Session pooler" adresi; Render'ın ücretsiz veritabanının süresi 1 Kasım'da doldu).
  Ücretsiz Supabase havuzunun bağlantı sınırı düşük → sunucu en çok 10 bağlantı tutar; boşta kalan bağlantı 5 dakikada yenilenir,
  kullanmadan önce yoklanır. İstersen Render › Environment'ta `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` ile değişir (gerekmez).
- **`app/main.py`**: yalnız sürüm (1.21.1).
- **`render.yaml`**: Render veritabanı satırları çıktı (servis elle kurulduğu için zaten kullanılmıyordu; yanlışlıkla yeni ücretsiz
  veritabanı açılmasın).

**Yapılacak (GitHub `Burcuri/rota-api`):**
1. `app` klasörüne gir → `webapp` klasörünü sil (⋯ → *Delete directory*) → Commit.
2. Yine `app` klasöründeyken **Add file → Upload files** → zip'teki `app/webapp` klasörünü (23 dosya) ve `app/db.py`, `app/main.py`
   dosyalarını sürükle → Commit. (GitHub bir seferde en çok 100 dosya alır; bu yükleme 25 dosya.)
3. Ana klasöre dön → **Add file → Upload files** → zip'teki `render.yaml` ve `GUNCELLEME_ADIMLARI.md` → Commit.
4. Render kendisi kurar (2–3 dk) → **Live** → `https://rota-sunucu.onrender.com/web/` açılıyor mu, girişte "ROTA" altında "Asistan"
   görünüyor mu bak.

---

# Sunucu güncellemesi — Dilim 27 · 0.24.0 konum "Kullanırken", kapı haritası (1 Kasım 2026) — sunucu 1.21.0

**Dilim 26'yı henüz yüklemediysen doğrudan bunu yükle** (bu zip öncekilerin hepsini içerir). Python değişti: sürüm 1.21.0; yeni tablo,
sütun, paket, anahtar YOK. Ayrıntı: uygulama paketindeki `docs/DILIM-27.md`.
- **Yönetim › Kapı haritası** (kullanıcı isteği 1 Kasım): ortak kapı katmanının önizlemesi — il › ilçe › mahalle › sokak kırılımı,
  haritada doğrulanmış / bekleyen / tartışmalı noktalar, nokta ayrıntısı, yanlış noktayı silme, CSV indirme. Yeni dosya `app/doormap.py`;
  uçlar yalnız yönetici oturumuyla. Silme ve indirme işlem günlüğüne yazılır (adres yazılmaz).
- `/gizlilik` metni "CANLI TAKİP" maddesindeki değişiklikle yenilendi; `/destek` SSS'si "Rota Her zaman izni istemez" diyor.
- `app/webapp/` (web ikizi 0.24.0) yenilendi.

**Yapılacak:** GitHub `Burcuri/rota-api` → zip'in tamamını yükle (değişenler: `app/main.py`, yeni `app/doormap.py`, `app/static/`
içinde `yonetim.html`, `yonetim.js`, `yonetim.css`, `gizlilik.html`, `kosullar.html`, `destek.html`, `yasal.css`, `app/webapp/`
klasörü (önce eskisini sil), `tests/`) → Render'ın kurmasını bekle → `/yonetim` › **Kapı haritası** açılıyor mu, `/gizlilik` ve
`/destek` açılıyor mu bak.

---

# Sunucu güncellemesi — Dilim 26 · 0.23.1 mağaza hazırlığı (31 Ekim 2026) — sunucu 1.20.1

**Python değişti (az): sürüm 1.20.1.** Yeni tablo, sütun, paket YOK. Ayrıntı: uygulama paketindeki `docs/DILIM-26.md`.
- **Yeni herkese açık sayfalar** (mağaza formlarındaki adresler):
  - `/gizlilik` — gizlilik politikası (aydınlatma metni, TR + EN). Play Console ve App Store Connect › gizlilik politikası adresi.
  - `/kosullar` — kullanım koşulları (TR + EN).
  - `/destek` — destek sayfası (App Store › destek adresi). E-posta satırı aşağıdaki ayardan gelir.
  - Metinler uygulamadakiyle aynı (taslak işaretiyle); avukat onaylı metin gelince birlikte yenilenir.
- **Yeni uç:** `GET /v1/support` → `{"email": …}`. Uygulamanın Yardım ekranı destek e-postasını buradan okur (adres değişince uygulama
  yeniden derlenmez).
- **İsteğe bağlı yeni ayar — `DESTEK_EPOSTA`** (Render › Environment): herkese görünecek destek e-postası. Gizli DEĞİL ama koda / zip'e
  yazılmaz; yalnız Render'da durur. Yoksa sayfada "Destek e-postası yakında" yazar. App Store'a göndermeden önce yaz.
- `app/webapp/` (web ikizi 0.23.1) yenilendi.

**Yapılacak:** GitHub `Burcuri/rota-api` → zip'teki **`app/main.py`**, **`app/static/`** içindeki yeni dosyalar (`gizlilik.html`,
`kosullar.html`, `destek.html`, `yasal.css`), **`app/webapp/`** klasörü (önce eskisini sil) ve **`tests/test_public_pages.py`** →
Render'ın kurmasını bekle → tarayıcıda `https://<render-adresin>/gizlilik` ve `/destek` açılıyor mu bak. (İstersen zip'in tamamını
yükleyebilirsin.)

---

# Sunucu güncellemesi — Dilim 25 · 0.23.0 açılış izinleri ve arka planda canlı takip (30 Ekim 2026) — sunucu 1.20.0 (AYNI)

**Python DEĞİŞMEDİ** (sürüm 1.20.0 kaldı; yeni tablo, sütun, paket, anahtar YOK). Yalnız **`app/webapp/`** (web ikizi 0.23.0) yenilendi.
Ayrıntı: uygulama paketindeki `docs/DILIM-25.md`.
- Arka planda canlı takip var olan uçla çalışır (`POST /v1/follow/{id}/ping`, takip başına dakikada 40 sınırı; uygulama en sık 20 sn'de bir
  gönderir). Dilim 24'ü (1.20.0) yüklemediysen önce onu yükle.

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'teki
`app/webapp` klasörünü yükle (başka dosya değişmedi; istersen zip'in tamamını yükleyebilirsin) → Render'ın kurmasını bekle.

---

# Sunucu güncellemesi — Dilim 24 · 0.22.0 hesap güvenliği ve kilit ekranı (29–30 Ekim 2026) — sunucu 1.20.0

**Python değişti: sürüm 1.20.0.** Ayrıntı: uygulama paketindeki `docs/DILIM-24.md`.
- **Kullanıcı isteği (29 Ekim):** "hesap güvenliğini ve kilit ekranı yap" (iş listesi 7. ve 6. madde). Kilit ekranı tamamen TELEFONDA
  (sunucuya bir şey gelmez); sunucudaki iş hesap güvenliği:
  - **Giriş kilidi:** aynı hesaba 5 yanlış şifrede 15 dk, 10'da 1 sa, 15'te 6 sa, 20'de 24 sa; aynı ağdan (IP) 15 dakikada en çok 50 yanlış.
    Kilitlenince kişiye uygulama içi duyuru, yönetimde "KİLİTLİ" + "Kilidi aç".
  - **Şifremi unuttum** (`POST /v1/auth/forgot`, `POST /v1/auth/reset`): 6 haneli kod DOĞRULANMIŞ e-postaya, yoksa telefona (SMS). Kod
    15 dk geçerli, 5 deneme, 60 sn'de bir yeniden, saatte 5.
  - **E-posta / telefon doğrulama, telefon numarası değiştirme** (`/v1/me/email/send|verify`, `/v1/me/phone/send|verify`,
    `/v1/me/phone/change`, `/v1/me/phone/change/verify`), **diğer oturumları kapat** (`POST /v1/me/sessions/revoke`).
  - **Hesabı silme** (`POST /v1/me/delete`, şifre + onay): kişisel bilgiler silinir, hesap satırı "Silinmiş üye" olarak anonimleşir;
    bekleyen ödül talepleri iptal (kuryeye puan iadesi), ilanlar kapanır, henüz ulaşmamış mesajları silinir, ortak kapı haritasındaki
    oyları kişiye bağlanamaz olur (doors.anonymize_reporter'ın yaptığı). Kara listedeki hesapta telefonun geri çevrilemez özeti kalır
    (aynı numarayla yeni hesap açılınca kara liste notu geri gelir).
  - **Tarayıcıda hesap silme sayfası `/hesap-sil`** — Google Play "hesap silme bağlantısı" zorunluluğu için: Play Console › Uygulama
    içeriği › Veri güvenliği › hesap silme URL'si = `https://<render-adresin>/hesap-sil`.
  - **SMS / e-posta servisi bağlı değilken (DENEME MODU):** kod kişiye gitmez; **Yönetim › Kodlar**'da görünür. Kişi destekten ister,
    kimliğini doğruladıktan sonra kodu söylersin ("Kodu göster" → "Evet"; her açış işlem günlüğüne yazılır).
- **Yeni dosyalar:** `app/security.py`, `app/static/hesap-sil.html`, `hesap-sil.css`, `hesap-sil.js`, `tests/test_security.py`.
- **Değişen:** `app/main.py`, `app/auth.py`, `app/admin.py`, `app/models.py`, `app/migrate.py`, `app/notify.py` (Brevo), `app/events.py`,
  `app/pushes.py`, `app/relay.py`, `app/marks.py`, `app/static/yonetim.html|js|css` (Kodlar sekmesi, üye güvenliği), `tests/conftest.py`
  ve `app/webapp/` (uygulama 0.22.0).
- **Yeni tablolar (kendiliğinden açılır):** `auth_codes`, `auth_throttle`. **Yeni sütunlar** — Render logunda dört satır görmelisin:
  `[şema] users.email_verified_at eklendi`, `[şema] users.phone_verified_at eklendi`, `[şema] users.deleted_at eklendi`,
  `[şema] users.phone_hash eklendi`. **Yeni Python paketi YOK.**
- **İsteğe bağlı ortam değişkenleri (yalnız Render › Environment; zip'e / GitHub'a / sohbete KONMAZ):**
  - `BREVO_API_KEY` + `MAIL_FROM` (+ `MAIL_FROM_NAME`, yoksa "Rota"): kodlar e-postayla gider. Brevo ücretsiz planı günde 300 e-posta;
    Render'ın ücretsiz planında da çalışır (HTTPS). Kurulum: brevo.com'da hesap aç → *Senders* bölümünde gönderici adresini ekle ve
    doğrula → *SMTP & API › API keys* → yeni anahtar → Render'a `BREVO_API_KEY` ve `MAIL_FROM` (doğruladığın adres) olarak yaz.
  - `LOGIN_IP_FAIL_MAX` (varsayılan 50; aynı ağdan 15 dakikada yanlış şifre tavanı), `SMS_DAILY_MAX` (varsayılan 300; SMS ücretli
    olunca günlük toplam tavan). Hiçbiri yazılmazsa varsayılanlar geçerli.
  - SMS servisi hâlâ bağlı değil (kullanıcı kararı: para gerektiriyor) → telefon kodları Yönetim › Kodlar'da.
- **Uyumluluk:** önce SUNUCUYU güncelle. 0.22.0 uygulaması eski sunucuda bu ekranları açar ama "Bu özellik sunucu güncellenince (1.20.0)
  çalışır" der; giriş ve diğer her şey çalışır. Eski uygulama yeni sunucuda eskisi gibi çalışır (giriş kilidi orada da geçerli).

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle (`app/security.py`, `app/static/hesap-sil.*` dahil) → Render'ın kurmasını bekle → logda yukarıdaki dört `[şema]` satırı.
**Deneme (hemen, telefonda Expo Go ile de):** giriş ekranında "Şifremi unuttum" → telefonunu yaz → kod Yönetim › Kodlar'da → kodu ve
yeni şifreyi yaz. Yanlış şifreyi 5 kez dene → "Giriş kapalı — Çok fazla yanlış şifre denendi…" → Yönetim › Üyeler › üye › "Kilidi aç".
Tarayıcıda `https://<render-adresin>/hesap-sil` sayfasını aç (bir deneme hesabıyla sil).

---

# Sunucu güncellemesi — Dilim 23 · 0.21.0 kurye ve işletme telefonuna bildirim (29 Ekim 2026) — sunucu 1.19.0

**Python değişti: sürüm 1.19.0.** Ayrıntı: uygulama paketindeki `docs/DILIM-23.md`.
- **Kullanıcı isteği (29 Ekim 00:39):** "kurye ve işletme telefonuna bildirim (uygulamaya push) … WhatsApp'la ücretsiz yapılabilir mi".
  WhatsApp'tan otomatik bildirim ÜCRETLİ (Meta mesaj başı ücret alıyor) → ÜCRETSİZ yol: telefonda Expo bildirim servisi (Android: Google
  FCM, iPhone: Apple APNs), web ikizinde tarayıcı bildirimi (1.18.0'daki `webpush.py`).
- **Yeni dosya `app/pushes.py`:** `POST /v1/me/push` (cihazı kaydet), `POST /v1/me/push/off` (bu cihazda kapat), `POST /v1/me/push/test`
  (deneme, saatte 5), `GET /v1/push/key`. Olaylar: yeni mesaj, adres listesi paylaşıldı / kabul / ret, canlı takip isteği / kabul, haritada
  selam, çalışma bölgesinde yeni ilan (kurye), alıcı "Paketimi aldım" / kapıyı işaretledi, paket teslim edildi (işletme), ödül / coin /
  yönetim duyuruları. **Bildirim metninde kişisel bilgi YOK** ("📩 Yeni mesajınız var"). Tercihler, sessiz saatler, hafta sonu, resmî
  tatil sunucuda (Türkiye saati); aynı kaynaktan art arda gelenler birleşir.
- **Değişen:** `app/relay.py` (mesaj → bildirim; çıkışta cihazın bildirim kaydı da silinir), `app/tasks.py`, `app/follow.py`,
  `app/handoff.py`, `app/nearby.py`, `app/main.py` (yeni ilan → bölgedeki kuryeler), `app/models.py`, `app/events.py`, `app/webapp.py`
  (`GET /web/rota-sw.js`, `GET /web/manifest.webmanifest`; index.html'e ana ekran bilgisi — iPhone'da web ikizine bildirim için ana ekrana
  eklemek şart), yeni `app/static/web-sw.js`, `tests/test_pushes.py` (yeni), `tests/test_webapp.py` ve `app/webapp/` (uygulama 0.21.0).
- **Yeni tablo (kendiliğinden açılır):** `user_push`. **Yeni sütun YOK** (logda `[şema]` satırı çıkmaz). **Yeni Python paketi YOK.**
- **Anahtar GEREKMEZ.** İsteğe bağlı: `EXPO_ACCESS_TOKEN` — yalnız expo.dev'de "Enhanced push security" açılırsa (yalnız Render ortamına).
- **Uyumluluk:** önce SUNUCUYU güncelle. 0.21.0 uygulaması eski sunucuda bildirimi açamaz ("Sunucuya ulaşılamadı"), başka her şey çalışır.
  Eski uygulama yeni sunucuda eskisi gibi çalışır (bildirim yok).

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle (`app/pushes.py`, `app/static/web-sw.js` dahil) → Render'ın kurmasını bekle.
**Deneme (web ikizi, hemen):** bilgisayarda Chrome'da web ikizine gir → "🔔 Bildirimler" penceresi → İzin ver → Hesabım › Bildirimler ›
"Deneme bildirimi gönder". Başka hesaptan mesaj at, web ikizi sekmesi arkadayken → "📩 Yeni mesajınız var".
**Telefon için (bir kez):** `docs/DILIM-23.md` › "Telefonda bildirim kurulumu" (EAS proje kimliği, Firebase `google-services.json`, FCM V1
anahtarı yalnız EAS'a, iPhone'da ilk derlemede "Push Notifications" evet). Expo Go'da bildirim çalışmaz.

---

# Sunucu güncellemesi — Dilim 22 · 0.20.0 alıcıya süreç takibi (28 Ekim 2026) — sunucu 1.18.0

**Python değişti: sürüm 1.18.0.** Ayrıntı: uygulama paketindeki `docs/DILIM-22.md`.
- **Kullanıcı isteği (28 Ekim 20:15, iş listesi 4. madde):** alıcı sayfasında Migros'taki gibi üç parçalı çubuk (Hazırlanıyor → Yolda →
  Teslim edildi); web ikizinde "📦 Yola çıktı"; alıcı izin verirse tarayıcı bildirimi "Kargonuz yolda" (Android'de hemen, iPhone'da sayfa
  ana ekrana eklenince).
- **Yeni dosya `app/webpush.py`:** tarayıcı bildirimi (RFC 8291 şifreleme + RFC 8292 VAPID). Sunucu kimliği SECRET_KEY'den türetilir:
  **yeni anahtar / ortam değişkeni GEREKMEZ** (isteğe bağlı: `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`). İstek yalnız Google FCM, Mozilla, Apple,
  Microsoft bildirim servislerine gider. `PUSH_TEST_ENDPOINT_PREFIX` YALNIZ yerel deneme içindir, Render'a YAZILMAZ.
- **Değişen:** `app/handoff.py` (aşama, işveren kaydı, açık görevle kuryenin kayda bağlanması, `POST /v1/handoffs/{id}/depart`,
  `POST /v1/handoffs/done`, `GET /v1/handoffs/{id}/link?purpose=depart`; alıcı: `POST /a/{kod}/push`, `POST /a/{kod}/push/off`,
  `GET /a/{kod}/manifest.webmanifest`, `GET /a/sw.js`), `app/models.py`, `app/migrate.py`, `app/events.py`, `app/main.py`,
  `app/static/alici.html` + `alici.js?v=4` + `alici.css?v=5`, yeni `app/static/alici-sw.js` ve simgeler (`alici-180.png`, `alici-192.png`,
  `alici-512.png`, `alici-maskable-512.png`, `alici-badge.png`), `tests/test_handoff_track.py` (yeni), `tests/test_handoff.py` ve
  `app/webapp/` (uygulama 0.20.0).
- **Yeni Python paketi:** `cryptography==50.0.1` (`requirements.txt`'e eklendi; Render kurulumda kendisi yükler).
- **Yeni tablo (kendiliğinden açılır):** `handoff_push`. **Yeni sütunlar:** logda bir kez **`[şema] handoffs.employer_id eklendi`**,
  **`[şema] handoffs.departed_at eklendi`**, **`[şema] handoffs.delivered_at eklendi`** görünür.
- **Uyumluluk:** önce SUNUCUYU güncelle. 0.20.0 uygulaması eski sunucuda "📦 Yola çıktı"da hata verir. Eski uygulama yeni sunucuda
  eskisi gibi çalışır (alıcı sayfası çubuğu kuryenin açtığı bağlantıda "Yolda"dan başlar).

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle (`app/webpush.py`, `app/static/alici-sw.js` ve yeni `.png` simgeler, `requirements.txt` dahil) → Render'ın kurmasını
bekle → logda üç `[şema] handoffs.… eklendi` satırı.
**Deneme:** Web ikizinde İş Listesi › Sırada › bir kartta "📦 Yola çıktı" → "Bağlantıyı alıcıya ilet" → WhatsApp. Bağlantıyı Android
telefonda aç → "🔔 Yola çıkınca bildirim al" → izin ver. Başka bir adreste önce bağlantıyı aç + bildirimi aç, sonra web ikizinde
"📦 Yola çıktı" → telefona "Kargonuz yolda" bildirimi. iPhone: Safari › Paylaş › Ana Ekrana Ekle → "Kargom" simgesinden aç → bildirim al.

---

# Sunucu güncellemesi — Dilim 21 · 0.19.0 hesap verisi sunucuda tek kaynak (28 Ekim 2026) — sunucu 1.17.0

**Python değişti: sürüm 1.17.0.** Ayrıntı: uygulama paketindeki `docs/DILIM-21.md`.
- **Kullanıcı kararları (28 Ekim 01:07, 01:35):** sunucu hesap başına TEK KAYNAK (online olunca telefon ve web ikizi kendiliğinden güncellenir);
  reklam deposu kesinlikle online; IBAN / ödeme bilgisi yok; rehber telefonda + isteğe bağlı 24 saatlik yedek (24 saat sonra silinir).
- **Yeni dosya `app/account.py`:** `GET /v1/account?since=…` (eşitleme saati: yalnız değişenler + `me`), `PUT /v1/account/docs/{bölüm}`
  (son düzenlenen geçerli), `GET /v1/coins`, `POST /v1/coins/test-buy`, `GET /v1/ads/mine`, `POST /v1/ads`, `DELETE /v1/ads/{id}`,
  `GET /v1/ads/active` (kuryenin haritası), `POST /v1/jobs/{id}/promote`, `GET|PUT|DELETE /v1/me/guide-backup`. Yönetim:
  `GET|POST /v1/admin/users/{id}/coins`, `GET /v1/admin/ads`, `DELETE /v1/admin/ads/{id}`. 10 dakikada bir temizlik (24 saati dolan
  rehber yedekleri, çok eski reklam kayıtları).
- **Değişen:** `app/main.py` (`POST /v1/jobs` › `promote`, ilanda `promotedUntil`, öne çıkan ilan listede başta), `app/models.py`,
  `app/migrate.py`, `app/events.py`, `app/static/yonetim.js` + `yonetim.html` (`yonetim.js?v=11`: yeni **Reklamlar** sekmesi, üye
  ayrıntısında **Rota coin ve reklamlar** kutusu), `tests/test_account.py` (yeni) ve `app/webapp/` (uygulama 0.19.0).
- **Yeni tablolar (kendiliğinden açılır):** `account_state`, `account_docs`, `coin_entries`, `ad_bookings`, `guide_backups`.
  **Yeni sütun:** `jobs.promoted_until` → logda bir kez **`[şema] jobs.promoted_until eklendi`** görünür.
- **İsteğe bağlı ortam değişkeni:** `COIN_TEST_BUY` (varsayılan açık; `0` yazılırsa test satın alma kapanır — yayında mağaza bağlanınca).
  Rehber yedeğinin 24 saati sabittir (ortam değişkeni yok). Yeni Python paketi YOK.
- **Uyumluluk:** önce SUNUCUYU güncelle. 0.19.0 uygulaması eski sunucuda eşitleyemez (coin / reklam "sunucuya ulaşılamadı" der). Eski
  uygulama yeni sunucuda eskisi gibi çalışır (coin ve reklamı telefonda tutar).

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle (`app/account.py` dahil) → Render'ın kurmasını bekle → logda `[şema] jobs.promoted_until eklendi`.
**Deneme:** `/yonetim` › yeni **Reklamlar** sekmesi açılır. Telefonda işletme profilini kaydet → bilgisayarda web ikizinde aynı profil.
Rehberim › "24 saat yedekle" → başka cihazda girişte "Rehber yedeğin var" sorusu.

---

# Sunucu güncellemesi — Dilim 20 · 0.18.0 görünürlük ödülü ve kara liste (27 Ekim 2026) — sunucu 1.16.0

**Python değişti: sürüm 1.16.0.** Ayrıntı: uygulama paketindeki `docs/DILIM-20.md`.
- **Kullanıcı kararı (27 Ekim 19:42):** reklam ile hediye bağlanmaz (reklam her zaman peşin coin). Ödül koyanı ödüllendirmenin yolu
  GÖRÜNÜRLÜK: "✓ Güvenilir" (kendiliğinden: 3+ kargo, %90+ zamanında, memnuniyet 4+) ve "⭐ Öne çıkan" (yönetim verir) üst sırada, rozetli.
  Düzensiz (90 günde 2 geç kargo ya da 2 "gelmedi" ya da memnuniyet 2,5 altı) → ödülleri katalogdan iner, yenisi konamaz; yönetim "Affet"
  ile geri açar. **Kara liste** (not zorunlu, yalnız yönetim görür): kurye ödül alamaz; sponsor / işveren ödül koyamaz, ödülleri görünmez.
- **Yeni dosyalar:** `app/standing.py` (karne ve kurallar), `app/marks.py` — `GET /v1/admin/rewards/owners?view=…`,
  `POST /v1/admin/users/{id}/feature`, `POST /v1/admin/users/{id}/pardon`, `GET|POST /v1/admin/blacklist`, `DELETE /v1/admin/blacklist/{id}`,
  `PUT /v1/admin/users/{id}/note`.
- **Değişen:** `app/points.py` (katalog sırası + gizleme, rozet, karne `GET /v1/rewards/mine` › `standing`, kara liste engelleri,
  `GET /v1/points/me` › `rewardsBlocked`), `app/admin.py` (Üyeler: `banned=bl` süzgeci, ayrıntıda `mark` + `standing`), `app/models.py`,
  `app/main.py`, `app/static/yonetim.js` + `yonetim.html` (`yonetim.js?v=10`: yeni **Kara liste** sekmesi, Ödüller'de karne süzgeci ve
  düğmeler), `tests/test_standing.py` (yeni) ve `app/webapp/` (uygulama 0.18.0).
- **Yeni tablo (kendiliğinden açılır):** `user_marks`. Yeni sütun YOK → logda `[şema]` satırı beklenmez. Yeni ortam değişkeni ve Python
  paketi YOK.
- **Uyumluluk:** önce SUNUCUYU güncelle. 0.18.0 uygulaması eski sunucuda çalışır ama rozet / karne görünmez. Eski uygulama yeni sunucuda
  eskisi gibi çalışır (kara listedeki kurye "Al" deyince sunucu "durduruldu" der).

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle (`app/standing.py` ve `app/marks.py` dahil) → Render'ın kurmasını bekle. Logda yeni satır beklenmez.
**Deneme:** `/yonetim` › yeni **Kara liste** sekmesi açılır; bir üyeyi kullanıcı no (ör. `SPN-1234567`) ya da telefonla, not yazarak
ekle → Üyeler'de "Kara liste" rozeti. Ödüller › "Sponsor / işyeri karnesi" süzgecinde "⭐ Öne çıkar 7 gün" → o sponsorun ödülü kuryenin
kataloğunda en üstte "⭐ Öne çıkan".

---

# Sunucu güncellemesi — Dilim 19 · 0.17.0 puan ve ödül sunucuda (27 Ekim 2026) — sunucu 1.15.0

**Python değişti: sürüm 1.15.0.** Ayrıntı: uygulama paketindeki `docs/DILIM-19.md`.
- **Kullanıcı kararları (26 Ekim 21:12, 27 Ekim 00:13):** puan, seviye, rozet ve ödül SUNUCUDA. Doğrulanmış her teslim 10 puan (alıcı
  "Paketimi aldım" ya da kapıda GPS ≤50 m); günde en çok 600; aynı adrese aynı gün tek puan; km puanı yok; kullanılmayan puan 12 ayda düşer.
  1 puan = 0,10 TL; basamaklar 1000 / 2000 / 3000 / 5000; ürün değeri basamağın TL karşılığının %80–%150'si (3000 → 240–450 TL). Ödül kargoyla: 7 gün içinde kargo (yoksa puan iade),
  kurye "Aldım" (1–5 yıldız) ya da "Gelmedi" (yönetime), 14 gün cevap yoksa alındı. Kargo adresi yalnız ödülü koyana; talep kapanınca silinir.
- **Yeni dosya `app/points.py`:** `POST /v1/points/delivery`, `GET /v1/points/me`, `GET|POST /v1/rewards`, `GET /v1/rewards/mine`,
  `DELETE /v1/rewards/{id}`, `POST /v1/rewards/{id}/claim`, `GET /v1/rewards/claims/mine|owner`, `POST /v1/rewards/claims/{id}/ship|answer|cancel`,
  `GET /v1/notices`, `POST /v1/notices/ack`. Yönetim: `GET /v1/admin/rewards/claims`, `POST /v1/admin/rewards/claims/{id}/resolve`,
  `GET /v1/admin/rewards/scorecards`, `GET /v1/admin/points/watch`, `GET|POST /v1/admin/users/{id}/points`. 10 dakikada bir temizlik
  (geciken kargo iadesi, 14 gün sonra alındı sayma, adres silme, 30 günlük teslim kayıtları ve bildirimler, 12 ay puan düşmesi).
- **Alıcı onayı:** `app/handoff.py` — alıcı "Paketimi aldım" deyince kurye o durağı daha önce doğrulanamadan bildirmişse şimdi puan alır.
- **Yönetim paneli:** yeni **Ödüller** sekmesi (talepler + "Puanı iade et" / "Teslim alındı say", sponsor / işyeri karnesi, şüpheli puanlar +
  "Puanı düzelt"); işlem günlüğünde "Ödül talebini sonuçlandırdı", "Puan düzeltmesi yaptı". (`yonetim.js?v=9`)
- **Yeni tablolar (kendiliğinden açılır):** `point_entries`, `delivery_proofs`, `rewards`, `reward_claims`, `notices`. Yeni sütun YOK →
  logda `[şema]` satırı beklenmez.
- **İsteğe bağlı ortam değişkenleri (girmesen de olur):** `POINTS_DAILY_MAX` (600), `POINT_TL` (0.10), `REWARD_VALUE_MIN_RATIO` (0.8), `REWARD_VALUE_MAX_RATIO` (1.5),
  `POINTS_EXPIRE_DAYS` (365), `REWARD_SHIP_DAYS` (7), `REWARD_ANSWER_DAYS` (14), `DELIVERY_PROOF_KEEP_DAYS` (30), `NOTICE_KEEP_DAYS` (30).
  Yeni Python paketi YOK.
- **Değişen dosyalar:** `app/points.py` (yeni), `app/models.py`, `app/main.py`, `app/handoff.py`, `app/events.py`, `app/static/yonetim.js`,
  `app/static/yonetim.html`, `tests/test_points.py` (yeni) ve `app/webapp/` (uygulama 0.17.0).
- **Uyumluluk:** önce SUNUCUYU güncelle. 0.17.0 uygulaması eski sunucuda puan / ödül ekranlarında "sunucuya ulaşılamadı" der (teslim
  bildirimi sırada bekler, 72 saat). Eski uygulama yeni sunucuda eskisi gibi çalışır (puanları telefonda sayar).

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle (`app/points.py` dahil) → Render'ın kurmasını bekle. Logda yeni satır beklenmez.
**Deneme:** `/yonetim` › **Ödüller** sekmesi açılır. Sponsor hesabıyla Ürünlerim › yeni ödül (1000 puan, değer en az 80 TL) → kurye
Puanlarım › Ödül kataloğunda görür (450 TL'den pahalı ürün 3000 puana konamaz). Kurye teslim edince (kapıda GPS ya da alıcı onayı) Puanlarım'da +10 görünür.

---

# Sunucu güncellemesi — Dilim 18 · 0.16.0 kamerayla adres okuma telefonun içinde (24 Ekim 2026)

**Sunucu Python'u DEĞİŞMEDİ (sürüm yine 1.14.1).** Yeni tablo / sütun / ortam değişkeni / Python paketi YOK; logda yeni satır beklenmez.
Değişen yalnız `app/webapp/` (web ikizi, uygulama 0.16.0: kişisel veri metni ve ayar açıklamaları güncellendi). Ayrıntı: uygulama
paketindeki `docs/DILIM-18.md`.
- **Kamerayla adres okuma artık telefonun içinde** (Android: Google ML Kit, iPhone: Apple Vision); etiket fotoğrafı hiçbir servise gitmez.
  OCR.space kaldırıldı. Sunucuya fotoğraf zaten hiç gelmiyordu; burada değişen bir şey yok.
- Web ikizinde kamerayla tarama yine YOK (kural aynı).

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle → Render'ın kurmasını bekle. Logda yeni satır beklenmez.
**Deneme:** `/web` açılır; Hesabım › Gizlilik ve izinler › "Adres okuma (kamera → OCR)" açıklaması "telefonunun içinde okunur" der;
Aydınlatma Metni'nde "KAMERAYLA ADRES OKUMA" maddesi var, "OCR.space" geçmez.

---

# Sunucu güncellemesi — Dilim 17 · 0.15.0 sesle yazma yalnız cihazda (23 Ekim 2026)

**Sunucu Python'u DEĞİŞMEDİ (sürüm yine 1.14.1).** Yeni tablo / sütun / ortam değişkeni / Python paketi YOK; logda yeni satır beklenmez.
Değişen yalnız `app/webapp/` (web ikizi, uygulama 0.15.0). Ayrıntı: uygulama paketindeki `docs/DILIM-17.md`.
- **Web ikizinde sesle yazma yalnız Google Chrome 139+ ile**, ses bilgisayarda (Chrome'un cihaz içi Türkçe paketi) yazıya çevrilir; buluta gitmez.
  Başka tarayıcıda uygulama normal açılır, 🎤'a dokununca nedenini söyler. "Sesle yazma" izni hâlâ varsayılan KAPALI (Hesabım › Gizlilik ve izinler).
- Pakette ses anahtarı kalmadı (Groq / `EXPO_PUBLIC_STT_KEY` kalktı). Kamerayla adres okuma (OCR) bulutta aynen devam eder (telefonda).
- Sunucuda yapılacak başka bir şey yok: eski uygulama / yeni uygulama ve sunucu sürümleri birbiriyle uyumlu.

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle → Render'ın kurmasını bekle. Logda yeni satır beklenmez.
**Deneme:** bilgisayarda Chrome (139+) ile `/web` aç → Hesabım › Gizlilik ve izinler › Sesle yazma AÇ → yeni adres formunda 🎤'a dokun; ilk seferde
"Türkçe ses paketi indirilsin mi?" sorulur (~60 MB, bir kez) → konuş, tekrar dokun, söylediğin yazıya düşer. Edge / Firefox'ta 🎤'a dokununca
"yalnızca Google Chrome'da çalışır" yazar.

---

# Sunucu güncellemesi — Dilim 16 · 0.14.1 (22 Ekim 2026) — sunucu 1.14.1

**Python değişti (küçük): sürüm 1.14.1.** Ayrıntı: uygulama paketindeki `docs/DILIM-16.md`.
- **Kullanıcı kararı:** profil fotoğrafı YOK (kişiler avatar seçer); yalnız **işletme logo yükler**; logo dışı resmi **yönetim siler**.
- **Yeni dosya `app/logos.py`:** `POST /v1/me/logo` (yalnız işveren / sponsor modunda; JPEG / PNG / WEBP, gerçek baytlara bakılır, SVG yok,
  en çok 150 KB; boş = kaldır), `GET /v1/logo/{id}` (oturumsuz resim; askıdaki hesabın logosu verilmez).
  `/v1/me` ve giriş yanıtında `logo` (sürüm; '' = yok). İlanlarda `employer.logo` (kurye ilan kartında logoyu görür).
- **Yönetim:** yeni **Logolar** sekmesi (`GET /v1/admin/logos`, `DELETE /v1/admin/users/{id}/logo`; işlem günlüğüne "İşletme logosunu sildi").
  Silinen logo uygulamada da kalkar (kullanıcıya bildirim düşer).
- **Yeni sütunlar:** `users.logo`, `users.logo_type`, `users.logo_at`.
  **Beklenen log (yalnız ilk açılışta):** `[şema] users.logo eklendi`, `[şema] users.logo_type eklendi`, `[şema] users.logo_at eklendi`.
- İsteğe bağlı ortam değişkeni: `LOGO_MAX_KB` (varsayılan 150). Yeni Python paketi YOK.
- **Değişen dosyalar:** `app/logos.py` (yeni), `app/main.py`, `app/models.py`, `app/migrate.py`, `app/admin.py`, `app/static/yonetim.*`,
  `tests/test_logos.py` (yeni), `tests/test_relay_jobs.py` ve `app/webapp/` (uygulama 0.14.1).
- **Uyumluluk:** eski uygulama yeni alanları yok sayar; 0.14.1 uygulaması eski sunucuda logoyu yükleyemez (hata yazar), geri kalanı çalışır.

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle (`app/logos.py` dahil) → Render'ın kurmasını bekle → logda yukarıdaki üç `[şema]` satırı.
**Deneme:** işveren Hesabım › İşletme Profili'nden logo yükler (turuncu uyarı kutusu görünür) → kurye İlanlar'da ilan kartında logoyu görür →
`/yonetim` › **Logolar**'da logo listelenir; "Logoyu sil" → işveren uygulamayı yeniden açınca logo kalkar ve bildirim düşer.

---

# Sunucu güncellemesi — Dilim 15 · 0.14.0 (22 Ekim 2026) — sunucu 1.14.0

**Python değişti: sürüm 1.14.0.** Ayrıntı: uygulama paketindeki `docs/DILIM-15.md`.
- **"Adres Listesi Paylaş" — görev eşleşmesi (yeni `app/tasks.py`):** işveren listeyi / ilanı kuryeye paylaşınca görev açılır ve 7 haneli
  **Görev No** verilir; kurye kabul edince aynı numarayla eşleşir. Bir ilan aynı anda TEK kuryeye; bir kuryenin aynı işletmeden tek açık
  görevi olur. Uçlar: `POST /v1/tasks/share`, `POST /v1/tasks/{no}/accept|decline|end`, `GET /v1/tasks/mine`, `GET /v1/tasks/{no}`.
  Görevde adres yok (yalnız başlık + adres sayısı). Açık görev 24 saatte kapanır; biten görev 48 saat sonra silinir.
- **Önce paylaş, sonra takip (kullanıcı kararı):** `POST /v1/follow/request` artık yalnız EŞLEŞMİŞ görevi olan kuryeye gider (yoksa 409
  "Önce adres listesini paylaş…"). Takip görev no'yu taşır (`taskNo`); görev biterse takip de biter; kurye "bitirdim" deyince görev de biter.
- **İlanlar:** ilan kaldırılınca bağlı görev biter; bağlı ilan `GET /v1/jobs/open`'da başka kuryelere listelenmez; `GET /v1/jobs/mine`
  (işveren) her ilanın görevini verir (`task`: kurye + Görev No + durum).
- **Kalıcı çalışma bölgesi:** `POST /v1/me/work-area` (il, ilçe, en çok 3 mahalle); `/v1/me` ve giriş yanıtında `workArea`.
- **Yeni tablo:** `tasks` (kendiliğinden açılır). **Yeni sütunlar:** `users.work_area`, `follows.task_no`.
  **Beklenen log (yalnız ilk açılışta):** `[şema] users.work_area eklendi`, `[şema] follows.task_no eklendi`.
- İsteğe bağlı ortam değişkenleri: `TASK_MAX_HOURS` (varsayılan 24), `TASK_KEEP_HOURS` (48). Yeni Python paketi YOK.
- **Değişen dosyalar:** `app/tasks.py` (yeni), `app/follow.py`, `app/main.py`, `app/models.py`, `app/migrate.py`, `app/admin.py`
  (takip satırında görev no), `tests/test_tasks.py`, `tests/test_work_area.py` (yeni), `tests/test_follow.py` ve `app/webapp/` (uygulama 0.14.0).
- **Uyumluluk:** önce SUNUCUYU güncelle. 0.14.0 uygulaması eski sunucuda görevsiz eski akışla çalışır. Eski uygulama yeni sunucuda listeyi
  görevsiz gönderir ama **canlı takip isteği eşleşmiş görev ister** → işletmeler de 0.14.0'a geçmeli.

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle (`app/tasks.py` dahil) → Render'ın kurmasını bekle → logda yukarıdaki iki `[şema]` satırı.
**Deneme:** işveren Görevler'den bir listeyi kuryeye "📋 Adres Listesi Paylaş" ile verir → çıkan pencerede `GRV-xxxxxxx`; kurye sohbette aynı
numarayı görür, "Adresleri kabul et" der → işverenin kartında "🔒 Eşleşti" ve "📍 Canlı takibe al".

---

# Sunucu güncellemesi — Dilim 14 · 0.13.1 (22 Ekim 2026) — sunucu 1.13.0

**Python değişti (küçük): sürüm 1.13.0.** Takip yanıtında (`GET /v1/follow/mine`, `GET /v1/follow/{id}`) karşı tarafın `peer` nesnesine
`icon` (harita ikonu kimliği) ve `userNo` (7 haneli kullanıcı no) eklendi: işverenin "CANLI DAĞITIM PANELİ" kartı kuryenin avatarını ve
KRY numarasını gösterir. Fotoğraf sunucuya gelmez (sütun yok). Yeni tablo / sütun / ortam değişkeni / Python paketi YOK; logda yeni satır yok.
**Değişen dosyalar:** `app/follow.py`, `app/main.py` (sürüm), `tests/test_follow.py` (+1 deneme) ve `app/webapp/` (uygulama 0.13.1).
Ayrıntı: uygulama paketindeki `docs/DILIM-14.md` › 0.13.1. Eski uygulama yeni alanları yok sayar.

**Yapılacak:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in
içindekileri yükle → Render'ın kurmasını bekle. **Deneme:** kurye Hesabım › Avatar'dan bir harita ikonu seçer; işveren onu takibe alınca
ana sayfadaki kartta halkanın içinde o ikon ve adın altında `KRY-xxxxxxx` görünür.

---

# Sunucu güncellemesi — Dilim 14 · 0.13.0 oyunvari tasarım (22 Ekim 2026)

**Sunucu Python'u DEĞİŞMEDİ (sürüm yine 1.12.0).** Yeni tablo / sütun / ortam değişkeni / Python paketi YOK. Değişen yalnız görünüm:
- `app/webapp/` — yeni web ikizi derlemesi (uygulama 0.13.0: yalnız marka renkleri, Poppins + Inter yazı tipi, 3B düğmeler). Ayrıntı: uygulama paketindeki `docs/DILIM-14.md`.
- `app/static/alici.css`, `alici.html` — alıcı sayfası marka renklerinde (lacivert başlık, turkuaz sekme, pembe pin). İşlev aynı.
- `app/static/yonetim.css`, `yonetim.html` — yönetim paneli marka renklerinde (açık ve koyu). İşlev aynı; girişten önce sağ üstte görünen "Çıkış" düğmesi artık gizli.
- `app/static/fonts/` (yeni) — `Poppins-ExtraBold.ttf` (24 KB, OFL lisansı yanında).

**1.12.0 paketini zaten yüklediysen:** GitHub `Burcuri/rota-api` → `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in içindekileri yükle (`app/webapp`, `app/static` dahil) → Render'ın kurmasını bekle. Logda yeni satır beklenmez.
**Deneme:** `/web` açılır, yazılar Poppins / Inter, renkler panodaki 8 renk. Bir alıcı bağlantısı (`/a/…`) lacivert başlıkla açılır. `/yonetim` turkuaz sekmelerle açılır.

---

# Sunucu güncellemesi — Dilim 13 · 0.12.12 (21 Ekim 2026)

**Sunucu sürümü 1.12.0 (0.12.11'de 1.11.0 idi). Yenilik: kuryenin "✉️ Konum Talebi Gönder" düğmesi için alıcıya giden konum talebi metni (`app/handoff.py`: `purpose` = handoff | location) ve alıcı sayfasının "Kapının yeri" sekmesiyle açılması (`app/static/alici.js`, `#kapi`).** Yeni tablo / sütun / ortam değişkeni YOK; yeni log satırı yok. `app/webapp/` yeni web ikizi derlemesi (uygulama 0.12.12; değişiklikler uygulama paketindeki `docs/DILIM-13.md`'de). 1.11.0'daki kullanıcı no (users.public_no) aynen duruyor.
**0.12.11 paketine göre fark (0.12.12): `app/handoff.py`, `app/events.py`, `app/main.py` (sürüm), `app/static/alici.js`, `app/static/alici.html`, `tests/test_handoff.py` ve `app/webapp/`.** Eski uygulama sürümleri etkilenmez (alan isteğe bağlı; gönderilmezse eski teslim metni gider).
GitHub'daki depo hâlâ **Dilim 11**'de (`app/nearby.py` yok). Bu zip'i yüklemek Dilim 12'yi de (haritada görünme + kısıtlı selam) getirir.

**Yapılacak:** zip'i aç → GitHub `Burcuri/rota-api` → önce depodaki `app/webapp` klasörünü sil (⋯ → *Delete directory*) → **Add file → Upload files** ile zip'in **içindekileri** yükle (`app/webapp` dahil) → Render'ın kurmasını bekle.
**Beklenen log (Dilim 12'den, yalnız ilk açılışta):** `[şema] users.visible_map eklendi`, `users.vis_lat`, `users.vis_lon`, `users.vis_at`; yeni tablolar `map_blocks`, `nudges` sessizce açılır. Yeni Python paketi ve ortam değişkeni yok.
**Beklenen log (1.11.0, yalnız ilk açılışta):** `[şema] users.public_no eklendi` ve `[şema] users.public_no: N eski hesaba numara verildi` (N = o güne kadar açılmış hesap sayısı). İkinci açılışta bu satırlar çıkmaz.

## 1.12.0 — Konum talebi (sunucu)
- `POST /v1/handoffs` gövdesinde isteğe bağlı `purpose`: `handoff` (varsayılan, eski metin) | `location` (konum talebi metni; bağlantı `…/a/<kod>#kapi`). `POST /v1/handoffs/{id}/send?purpose=location` de aynı. Başka değer 422.
- SMS: "Kuryeniz paketinizi doğru adrese getirmek için konumunuzu doğrulamanızı istiyor. Bağlantıdan kapınızın yerini haritada işaretleyin: <bağlantı>"; e-posta konusu "Kuryeniz konumunuzu doğrulamanızı istiyor".
- Gönderim sınırı aynı: bir durak için en çok 3 gönderim (teslim + konum talebi birlikte sayılır).
- Yönetim etkinlik akışında yeni satır: "Alıcıya konum talebi gönderildi" (`handoff.locreq`).
- Denemeler: pytest 343/343.

## 1.11.0 — Kullanıcı no (sunucu)
- **Yeni sütun:** `users.public_no` (7 hane, benzersiz dizin `ix_users_public_no`). Kendiliğinden eklenir; veri silinmez.
- **Kim nasıl alır:** yeni kayıtta otomatik. Eski hesaplara ilk açılışta toplu verilir; ayrıca numarası boş bir hesap giriş yapınca ya da `/v1/me` çağırınca da alır. Numara rastgeledir (sıralı değil, tahmin edilemez), ilk hane 0 olmaz.
- **Değişmez:** kullanıcı değiştiremez (profil ucu yok sayar); rol değişince, şifre değişince aynı kalır. Uygulamada role göre önekle yazılır: Kurye `KRY-8472019`, İşveren `ISV-8472019`, Sponsor `SPN-8472019`. `0000000` sistem (Rota Asistan) için ayrılmıştır, gerçek hesaba verilmez.
- **Uç değişikliği:** kullanıcı nesnesine `userNo` eklendi (kayıt, giriş, `/v1/me`, rol, şifre yanıtları). Eski uygulama bu alanı yok sayar.
- **Yönetim paneli:** üye listesinde ve ayrıntıda `No KRY-8472019`; arama kutusuna `8472019` ya da `KRY-8472019` yazınca hesap bulunur.
- **Deneme:** `/web` → yeni hesap aç → Hesabım üstünde `KRY-xxxxxxx` görünür; çıkıp yeniden girince aynı numara gelir. `/yonetim` › Üyeler › numarayı arat.

## Dilim 13'te ne değişti (yalnız uygulama / web ikizi)
- "Açık Rıza" sayfası → **"Ayarlar ve özellikler"**. Sesle yazma, adres okuma (kamera → OCR) ve grup konumu **isteğe bağlı**; hepsi kapalıyken de uygulamaya girilir. "Kabul etmiyorum" düğmesi ve "üçü de gerekir" yazıları kalktı; Gizlilik ve izinler'de anahtarı kapatmak **oturumu kapatmaz**.
- Adres okuma açıklaması ayrıntılı: etiket fotoğrafı yurt dışındaki OCR.space'e gider; kapalıyken kamerayla etiket/adres okunmaz (barkod okunur); adres elle yazılarak uygulama kullanılır.
- Rıza sürümü **7 → 8**: herkes koşullar + aydınlatma + ayarlar ekranını bir kez daha görür. Zorunluyken verilmiş (sürüm 7) seçimler önceden açık gösterilmez.
- Sunucu rıza günlüğüne seçimler (açık ya da kapalı) yine yazılır; sunucuda kod değişikliği yok.
- **Deneme:** `/web` → yeni hesap aç → "Ayarlar ve özellikler" sayfası çıkar; hepsini kapalı bırakıp **Kaydet ve devam** → uygulama açılır.

---

# Sunucu güncellemesi (Dilim 6: 48 saatlik teslimat kaydı · Dilim 5: Takip, araç profili) — adım adım

Bu zip **önceki dilimlerin hepsini de içerir**; aynı adlı dosyaların üzerine yazılır. `SECRET_KEY` zaten ayarlı, yeniden yazmak gerekmez. Sunucu sürümü **1.7.0**. Yeni Python paketi yok.

**Senin yapacağın:** (1) dosyaları GitHub'a yükle, (2) Render'ın yeniden kurmasını bekle, (3) iki adres aç ve bak. `ADMIN_KEY`'i daha önce yazıp yöneticini oluşturduysan başka iş yok.

## Ne değişti (Dilim 6 — 48 saatlik teslimat kaydı)

- **Takip kaydı 48 saat saklanır.** Takip bitince (kurye "bitirdim" / Durdur, işveren bitirir, süre) **canlı konum silinir**; ama **yol izi** (seyreltilmiş noktalar, en sık 15 sn'de bir), **hangi durak ne zaman bitti** ve her biten durak için **sokak adı + kapı no + mahalle** ("Barbaros Cad. No:11 · Mahmutlar") sunucuda **48 saat** kalır, sonra kendiliğinden silinir (5 dakikada bir süpürülür). Müşteri adı, telefon, daire, kat ve not **hiç gelmez**; telefon / e-posta / bağlantı gibi görünen etiket atılır. Şikâyette "kurye oradaydı mı, hangi kapıya ne zaman gitti" bakılabilsin diye. Kayda **işveren, kurye ve yönetim** bakar.
- **Yönetim paneli › Takipler** (yeni sekme): liste (konumsuz) + ayrıntı (zamanlar, duraklar etiket ve saatiyle, iz çizimi, nokta listesi, haritada aç). İzi / ayrıntıyı açmak **işlem günlüğüne** yazılır (yalnız sayı, koordinat yok).
- **Düzeltme:** kurye "bitirdim" derken son ilerlemesi artık kapanış isteğiyle birlikte gider (eskiden konum sinyaliyle yarışıp son teslim kayıtta eksik kalabiliyordu).
- **Rıza sürümü 5 → 6:** uygulamayı güncelleyen herkes rıza ekranlarını bir kez daha görür (metinler taslaktır).

**Yeni uçlar:** `GET /v1/follow/{id}/trail` (işveren / kurye); `GET /v1/admin/follows`, `GET /v1/admin/follows/{id}` (yönetici). `POST /v1/follow/{id}/stop` artık isteğe bağlı `progress` alır (yalnız kuryeden kabul edilir).
**Yeni tablo:** `follow_points`; **yeni sütunlar:** `follows.trail_at`, `follows.trail_n` (hepsi kendiliğinden eklenir; veri silinmez; logda `[şema] follows.trail_at eklendi` ve `[şema] follows.trail_n eklendi` satırları normaldir). Etiketler `follows.progress` içinde (`a`) durur. `FOLLOW_KEEP_HOURS` varsayılanı **24 → 48**.

## Dilim 5'ten kalanlar (bu zip hepsini içerir)

- **Takip (Follow):** işveren, yazıştığı kuryeden haritada izleme izni ister; kurye **her seferinde** onaylar. Canlı konumu yalnız o işveren, yalnız takip sürerken görür; konum yalnız kurye uygulaması **açıkken** gelir; en çok 12 saat sürer.
- **Kurye araç profili:** araç tipi, kapasite, çalışma bölgeleri, müsaitlik. Kurye **ayrı rızayla** paylaşır; işletmeler sohbet başlığından görür. **Plaka ve fotoğraf sunucuya gitmez.** Rıza geri çekilince profil silinir.
- **Web ikizinde sesle yazma:** artık var (izin verilirse; tarayıcının kendi konuşma tanıması, pakette anahtar yok). Kamera, ödeme ve adres listesi gönderme web'de **yok**.

**Dilim 5 uçları:** `POST /v1/follow/request`, `GET /v1/follow/mine`, `GET /v1/follow/{id}`, `POST /v1/follow/{id}/accept|decline|stop|ping`; `GET|PUT|DELETE /v1/me/vehicle`; `GET /v1/couriers/{id}/vehicle` (yalnız işletme).
**Dilim 5 tabloları:** `follows`, `courier_profiles`; sütun `consent_log.arac` (kendiliğinden eklenir; logda `[şema] consent_log.arac eklendi` normaldir).

## 1) Dosyaları GitHub'a yükle

1. Zip'i bilgisayarında bir klasöre aç.
2. `github.com/Burcuri/rota-api` → **Add file → Upload files**.
3. Klasörün **içindekileri** (`app`, `tests`, `.github` klasörleri + diğer dosyalar) pencereye sürükle. **`app/webapp` klasörü (içinde `_expo`, `assets`, `index.html`, `metadata.json`, `LICENSE-leaflet.txt`) mutlaka gelmeli**; yoksa `/web` açılmaz.
4. En altta **Commit changes**.

> GitHub tek seferde en çok 100 dosya kabul eder; bu zip'te ~75 dosya var. "too many files" derse önce `app/webapp`'i, sonra kalanları ayrı yükle.
> **`.github` gizli olabilir:** Windows Gezgini'nde *Görünüm → Göster → Gizli öğeler*'i aç. Yine gelmezse GitHub'da **Add file → Create new file**, ad kutusuna `.github/workflows/keepalive.yml` yaz, zip'teki dosyanın içeriğini yapıştır. (Önceden yüklendiyse gerekmez.)
> **Eski web dosyaları:** `app/webapp` içindeki eski özetli dosyalar kalırsa zararsızdır, yalnız yer kaplar. Temiz olsun istersen önce GitHub'da `app/webapp` klasörünü sil (klasörün ⋯ menüsü → *Delete directory*), sonra yenisini yükle.

## 2) Render yeniden kurar

1. Render → `rota-sunucu` → **Events**: "Deploy live" görünene kadar bekle (2–5 dk). Başlamazsa **Manual Deploy → Deploy latest commit**.
2. Veritabanı kendiliğinden yükselir (yeni tablo `follow_points`).
3. `https://rota-sunucu.onrender.com/health` → `{"ok":true}`; `https://rota-sunucu.onrender.com/web` → giriş ekranı.

## 3) Dene (iki tarayıcı: biri işveren, biri kurye hesabıyla)

1. İkisi de `/web`'e girer; ilk girişte rıza ekranları bir kez gelir.
2. **Takip:** işveren, kuryeyle yazışma ekranında **Takip** → kurye istek penceresinde **Kabul et** (tarayıcı konum izni sorar) → işveren **Haritada gör**. Durdurunca canlı konum silinir; **Hesabım › Takip kayıtları**ndan (ve yönetim paneli › Takipler'den) iz + teslim saatleri 48 saat görünür.
3. **Araç profili:** kurye › Hesabım › **Araç ve çalışma bölgesi** › **İşletmelere göster** (ayrı onay penceresi) → işveren, sohbet başlığındaki kurye adına (**›**) dokunur → **ARAÇ PROFİLİ** kartı.

## İlk yöneticiyi oluşturmak (ADMIN_KEY) — yapmadıysan

1. Kendine **uzun, rastgele bir anahtar** üret (en az 12 karakter). **Sohbete, zip'e, depoya yazma.**
2. Render → `rota-sunucu` → **Environment** → `ADMIN_KEY` = anahtarın → **Save** (sunucu yeniden başlar).
3. `https://rota-sunucu.onrender.com/yonetim` → **İlk kurulum**: anahtar, bir **yönetici adı** (3–32 karakter: küçük harf, rakam, `_` `.` `-`) ve **şifre** (en az 10 karakter) → **İlk yöneticiyi oluştur**; sonra yukarıdaki forma yönetici adı + şifreyle gir.
4. `ADMIN_KEY` günlük giriş için kullanılmaz; yalnız ilk kurulum ve **unutulan şifrenin kurtarılması** içindir. En güvenlisi kurulumdan sonra Render'dan silmek (giriş çalışmaya devam eder; gerekirse yeniden yazarsın).
5. **İki adımlı doğrulama (önerilir):** Hesabım → *İki adımlı doğrulama* → *Kurulumu başlat* → bir kimlik doğrulayıcı uygulamayla QR'ı okut → 6 haneli kodu yaz. Hepsi için **zorunlu** yapmak istersen Render'a `ADMIN_REQUIRE_2FA` = `1` yaz. Yönetici paneli her hesaba erişebildiği için **önerilir**.

Yönetim **göremez:** mesajlar, adres listeleri (telefonlarda durur), alıcının adı / telefonu, araç plakası / fotoğrafı (sunucuda yoktur). **Bakabilir:** sunucudaki kayıtların hepsine, takip kaydı dahil (yol izi, biten durakların sokak + kapı no etiketi, saatler; 48 saat). Her bakış işlem günlüğüne yazılır.

## Sunucuyu uyanık tut (ping)

Ücretsiz Render 15 dakika istek almazsa uyur; ilk istek ~1 dakika sürer. İkisi birlikte de olur:

- **A) GitHub işi (zip'te hazır, `.github/workflows/keepalive.yml`):** dosya depoya girince çalışır. Deneme: depo → **Actions** → **keepalive** → **Run workflow** (`HTTP 200` görmelisin). Depo özelse ücretsiz dakikalar (ayda 2000) yetmeyebilir; o zaman B'yi kullan ya da dosyadaki `*/10`'u `*/14` yap. 60 gün işlem görmeyen depoda GitHub zamanlanmış işleri durdurur. Adres farklıysa: Settings → Secrets and variables → Actions → **Variables** → `SERVER_URL`.
- **B) Dış pinger (daha güvenilir):** `cron-job.org` ya da `UptimeRobot` ile `https://rota-sunucu.onrender.com/health` adresini 10 dakikada bir çağırt (14 dakikadan kısa olsun; GET ve HEAD çalışır).

> Render'ın ücretsiz planında ayda 750 saat vardır; tek servisi 7/24 uyanık tutmak ~744 saat eder (sınıra yakın). Güncel şartlar için Render'ın fiyat sayfasına bak.

## SMS ve e-posta

- **SMS (1.27.0: kod hazır, sağlayıcı bekliyor).** Üç yerli sağlayıcının bağlantısı yazıldı: **Netgsm**, **İletimerkezi**, **Verimor**.
  Biriyle anlaşınca:
  1. Sağlayıcıda kurumsal hesap aç → **gönderici başlığı** iste (SMS'te gönderen olarak görünen ad, ör. `ROTA ASISTAN`, en çok 11 karakter).
     Başlık onayı için şirket belgeleri istenir; onaylanınca panelde görünür.
  2. Panelde API erişimini aç:
     - **Netgsm:** API yetkili alt kullanıcı aç → kullanıcı kodu + şifre.
     - **İletimerkezi:** *Ayarlar › Güvenlik › API Erişimi* → API anahtarı (key) + hash (hash'i panelden kopyala).
     - **Verimor:** API kullanıcısı aç → kullanıcı adı + şifre.
     - Sağlayıcı "izinli IP" isterse: Render › rota-sunucu › sağ üstte **Connect** › **Outbound** sekmesindeki adresleri yaz.
  3. Render › rota-sunucu › **Environment** (YALNIZ burada; zip'e, koda, GitHub'a, sohbete yazılmaz):
     - `SMS_PROVIDER` = `netgsm` ya da `iletimerkezi` ya da `verimor`
     - `SMS_HEADER` = onaylı başlık (paneldeki gibi)
     - Netgsm / Verimor: `SMS_USER` + `SMS_PASS` · İletimerkezi: `SMS_API_KEY` + `SMS_API_HASH`
     → *Save Changes* → Deploy. **Yönetim › Kodlar**'ın altında "SMS (Netgsm): bağlı ✓" gibi yazar. Biri eksikse "bağlı değil" kalır.
  4. Dene: kendi numaranla uygulamada **Şifremi unuttum** → SMS gelmeli. Gelmezse Render › **Logs**'ta
     `[bildirim] SMS (…) gönderilemedi: HTTP … · kod …` satırına bak (numara, metin ve anahtar yazılmaz); kodu sağlayıcıya sor.
  - Bağlanınca **doğrulama kodları** (şifremi unuttum, telefon doğrulama / değiştirme) SMS'le gider. **Teslim bağlantıları** (her durak
    bir mesaj; metin uzun olduğu için 2 SMS kredisi) yalnız `SMS_LINKS=1` de yazılırsa sunucudan gider; yazılmazsa kurye bugünkü gibi kendi
    telefonundan paylaşır. Günlük tavan: kodlar `SMS_DAILY_MAX` (300), bağlantılar `SMS_LINKS_DAILY_MAX` (300).
  - Giden mesajların hepsi bilgilendirme (kod, teslim bağlantısı); reklam SMS'i yok. Sistem İYS sorgusunu kapalı gönderir
    (avukat notlarına eklendi). Yalnız cep numarasına (05…) gider.
- **E-posta:** **Brevo** (ücretsiz; Render'ın ücretsiz planında çalışır): `BREVO_API_KEY` + `MAIL_FROM`. Ya da SMTP (`SMTP_HOST`,
  `SMTP_PORT` 587, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`; yalnız Render'ın ücretli planında).
- Hiçbiri tanımlı değilken: kodlar **Yönetim › Kodlar**'da (kişinin kimliğini doğrulayıp sen söylersin); kurye teslim bağlantısını kendi
  SMS / WhatsApp uygulamasıyla iletir ya da QR gösterir.

## Mağaza satışı (Rota coin) — Google Play / App Store hesapları açılınca

Kod hazır (sunucu 1.25.0 + 1.28.0 iade; uygulama 0.34.0'da `expo-iap` pakette). Fiyatı mağaza belirler ve gösterir (başlangıç ölçütü 1 coin ≈
10 TL). Ürün kimlikleri iki mağazada da AYNI: `rota_coin_50`, `rota_coin_200`, `rota_coin_500` (tüketilebilir). Bütün anahtarlar GİZLİDİR:
yalnız Render › Environment'a yazılır; zip'e, GitHub'a, sohbete konmaz.

**Google Play**
1. Play Console › uygulama › **Para kazanma › Ürünler › Uygulama içi ürünler** → üç ürün (kimlikler yukarıda) → fiyat → **Etkinleştir**.
2. Google Cloud Console › **IAM ve Yönetici › Hizmet hesapları** → yeni hizmet hesabı → **Anahtarlar › Anahtar ekle › JSON** (iner; GİZLİ).
   Aynı projede **Google Play Android Developer API**'yi etkinleştir.
3. Play Console › **Kullanıcılar ve izinler › Yeni kullanıcı davet et** → hizmet hesabının e-postası → uygulama izinleri: **Finansal verileri,
   siparişleri ve iptal anketi yanıtlarını görüntüleme** + **Siparişleri ve abonelikleri yönetme**.
4. Render › Environment: `GOOGLE_PLAY_SERVICE_ACCOUNT` = indirdiğin JSON'un tamamı (tek satıra yapıştırılabilir). İadeler bununla kendiliğinden
   denetlenir; ek kurulum yok.
5. Deneme: Play Console › **Ayarlar › Lisans testi** → kendi Google hesabın → deneme satın almaları ücretsizdir, hesapta "deneme" diye yazar.

**App Store** (Apple geliştirici hesabı açılınca)
1. App Store Connect › uygulama › **Uygulama İçi Satın Alımlar** → üç ürün, tür **Tüketilebilir (Consumable)**, kimlikler yukarıda.
2. **Kullanıcılar ve Erişim › Entegrasyonlar › Uygulama İçi Satın Alma** → anahtar oluştur → `.p8` iner (GİZLİ); **Key ID** ve **Issuer ID**'yi not et.
3. Render › Environment: `APPLE_IAP_ISSUER_ID`, `APPLE_IAP_KEY_ID`, `APPLE_IAP_PRIVATE_KEY` (`.p8`'in içeriği).
4. App Store Connect › uygulama › **Uygulama Bilgileri › App Store Sunucu Bildirimleri** → üretim ve sandbox adresi:
   `https://rota-sunucu.onrender.com/v1/iap/apple/notify`, **Sürüm 2** (iadeler için).

**Gerçek satışa geçerken:** Render'da `COIN_TEST_BUY=0` (test satın alma kapanır). Yönetim › Özet › **Mağaza satışları**: "bağlı ✓", son 30 gün
satış / iade ve son iade denetimi görünür. Uygulamanın mağaza sürümü EAS derlemesiyle (Expo Go'da satın alma yoktur).

## İsteğe bağlı ayarlar (Render → Environment) — hiçbiri zorunlu değil

| Anahtar | Varsayılan | Anlamı |
|---|---|---|
| `FOLLOW_ASK_MIN` | `30` | Kurye takip isteğine en çok kaç dakika içinde yanıt verir |
| `FOLLOW_MAX_HOURS` | `12` | Bir takip en çok kaç saat sürer (sonra yeniden izin gerekir) |
| `FOLLOW_STALE_MIN` | `30` | Bu kadar dakikadır güncellenmeyen konum silinir |
| `FOLLOW_KEEP_HOURS` | `48` | Biten takip kaydı (yol izi + son ilerleme + adres etiketleri) kaç saat sonra silinir |
| `FOLLOW_TRAIL_SEC` / `FOLLOW_TRAIL_MAX` | `15` / `4000` | İz noktaları arası en kısa süre (sn) / bir takip için en çok nokta |
| `FOLLOW_COOLDOWN_MIN` | `10` | Reddedilen istek aynı işverenden kaç dakika sonra yeniden gelebilir |
| `VEHICLE_VIEW_PER_MIN` | `90` | Bir işletmenin dakikada bakabileceği kurye araç profili sayısı |
| `ADMIN_KEY` | boş | Yönetici kurulum / kurtarma anahtarı (en az 12 karakter). Boşsa kurulum kutusu yok; giriş çalışır |
| `ADMIN_REQUIRE_2FA` | boş | `1` → tüm yöneticiler için iki adımlı doğrulama zorunlu |
| `EVENT_KEEP_DAYS` / `EVENT_MAX_ROWS` | `60` / `50000` | Etkinlik akışı (içeriksiz) kaç gün / satır |
| `REVIEW_KEEP_DAYS` | `365` | Değerlendirmeler kaç gün tutulur |
| `AUDIT_KEEP_DAYS` | `365` | Yönetici işlem günlüğü kaç gün tutulur |
| `HANDOFF_REVIEW_HOURS` | `24` | Teslim bitince alıcı en çok kaç saat daha yorum yazabilir |
| `HANDOFF_TTL_HOURS` / `HANDOFF_DAILY_MAX` | `48` / `300` | Alıcı kayıtları en geç kaç saatte silinir / kurye başı günlük bağlantı |
| `PUBLIC_BASE` | boş | Bağlantı adresi başlangıcı; boşsa Render'ın `RENDER_EXTERNAL_URL`'i |
| `SMS_PROVIDER` / `SMS_HEADER` | boş | SMS sağlayıcısı (`netgsm` / `iletimerkezi` / `verimor`) / onaylı gönderici başlığı (yukarıdaki SMS bölümü) |
| `SMS_USER` `SMS_PASS` / `SMS_API_KEY` `SMS_API_HASH` | boş | Netgsm, Verimor / İletimerkezi giriş bilgileri — yalnız Render'da |
| `SMS_LINKS` / `SMS_LINKS_DAILY_MAX` | boş / `300` | `1` → teslim bağlantıları da sunucudan SMS'le gider / günlük tavan |
| `SMS_DAILY_MAX` | `300` | Doğrulama kodu SMS'i günlük tavanı |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` `SMTP_FROM` `SMTP_STARTTLS` | boş / `587` / … / `1` | E-posta gönderimi (587'de STARTTLS, 465'te SSL) |
| `ERROR_KEEP_DAYS` / `ERROR_MAX_ROWS` / `ERROR_DAILY_MAX` | `14` / `5000` / `3000` | Hata kayıtları |
| `DOOR_DAILY_MAX` / `DOOR_TRUST_SCORE` / `DOOR_MERGE_M` | `400` / `2` / `35` | Ortak kapı haritası sınırları |
| `DOOR_REQUIRE_CONSENT` | `0` | `1` yapma (uygulama eski `kapi` rızasını artık sormaz) |
| `RELAY_TTL_DAYS` / `RELAY_MAX_BYTES` / `RELAY_USER_MAX_BYTES` | `7` / `262144` / `10485760` | Mesaj aktarıcısı sınırları |
| `GROUP_MAX` | `7` | Grup üst sınırı (kuran dahil) |
| `WEB_APP_DIR` | boş | Web ikizi klasörü (varsayılan `app/webapp`) |
| `DESTEK_EPOSTA` | boş | 1.20.1: `/destek` sayfasında ve uygulamanın Yardım ekranında görünen destek e-postası (herkese açık; gizli değil) |
| `ESKI_AKIS` | boş | `1` → eski Başvur/Kabul uçları açılır (önerilmez) |

## Önceki dilimlerden (bu zip hepsini içerir)

- **Dilim 4:** web ikizi `/web` (kurye, işveren, sponsor; hesap telefonla ortak, mesajlar her cihaza gelir); yönetici paneli `/yonetim` (üyeler, men, geçici şifre, etkinlik, yorumlar, işlem günlüğü, hatalar, 2FA); 5 üzerinden değerlendirmeler ve alıcı anketi (yorum metnini **yalnız yönetim** okur); üyenin şifre değiştirmesi.
- **Dilim 3:** `GET /health`; çökme raporu `POST /v1/errors` (kişisel veri ayıklanır); teslim onayı, alıcı sayfası `/a/<kod>` (Paketimi aldım · Kapının yeri · Değerlendir).
- **Dilim 2B / 2:** ortak kapı haritası (kişi bilgisi yok); geçici mesaj aktarıcısı (mesajlar ve adres listeleri **cihazlarda** durur, sunucu en çok 7 gün bekletir).

## Sorun olursa

- Deploy **Failed** → Render → **Events** → önceki başarılı deploy → **Rollback**. Yeni tablolar / sütun kalır; eski kod onlara dokunmaz.
- "Sunucuya ulaşılamadı" → servis uyuyor olabilir (ilk istek ~1 dk); biraz bekleyip dene. Takip isteği de uyuyan sunucuda ilk anda gecikebilir.
- `/web` "Web ikizi bu sunucuda yüklü değil" → `app/webapp` klasörü GitHub'a yüklenmemiş.
- `/yonetim`'de kurulum kutusu yok → `ADMIN_KEY` yazılmamış ya da 12 karakterden kısa. "Anahtar yanlış" → Render'dakiyle aynı değil (başında / sonunda boşluk olmasın). "Çok fazla deneme" → 10–15 dk bekle.
- **Şifreni unuttun / 2FA telefonunu kaybettin:** `ADMIN_KEY` yaz → `/yonetim` → kurulum kutusu → aynı yönetici adı + yeni şifre + "Bu ad varsa şifresini sıfırla" (2FA kapanır; yeniden kur).
- Alıcı bağlantısı "bulunamadı / süresi doldu" → kayıt 48 saati geçmiş ya da silinmiş; kurye yeni bağlantı üretir.
- Kurye takip isteğini görmüyorsa: uygulaması açık mı, hesabı **kurye** modunda mı (işveren moduna geçen kuryede takip kapanır) bak.

## Bilmen gerekenler

- **Yönetici her üyenin hesap bilgisine ve yorum metinlerine erişir** (aydınlatma metninde bildirilir); bu yüzden güçlü şifre + 2FA önemlidir. Mesajları ve adres listelerini göremez (telefonlarda durur); takip kayıtlarına (yol izi, biten durakların sokak + kapı no etiketi, saatler; 48 saat) bakabilir, her bakış işlem günlüğüne yazılır.
- **Üye girişinde (telefon + şifre) hız sınırı yok**; yönetici girişinde var. Hız sınırları IP'ye göredir (`X-Forwarded-For` taklit edilebilir); güvenlik duvarı değil, yavaşlatıcıdır.
- ~~Ücretsiz Render PostgreSQL oluşturulduktan 30 gün sonra sona erer …~~ → 1 Kasım 2026'da süresi doldu; **veritabanı artık Supabase'de** (Rota › rota-api, Frankfurt, ücretsiz). Ücretsiz Supabase projesi süreyle silinmez; bir hafta hiç kullanılmazsa **uyur** (veri kalır, panelden "Resume project" ile açılır; sunucu uyanıkken düzenli temizlik sorguları yaptığından normalde uyumaz). Yayında günlük yedek için ücretli plan (yayın öncesi son hazırlık). Üyeler, değerlendirmeler ve kapı haritası orada birikiyor: asıl varlığın bu.
- Ortak haritada **üçüncü kişilerin adres bilgisi** (sokak + kapı no + site/bina adı + konum; ad, telefon, daire, kat yok) toplanır. Hukuki metinler **taslaktır**; yayından önce gözden geçirilmelidir.
- Hesabı silme ucu henüz yok. Sunucu mesajın **içine bakmaz**.
- Web ikizi ve alıcı sayfası harita karolarını OpenStreetMap'ten alır; çok yoğun kullanımda kendi karo sağlayıcına geçmek gerekir.
- **Gerçek bir telefonda denenmedi.** Denemeler gerçek tarayıcıda (Chromium) ve gerçek yerel sunucuda (SQLite ve PostgreSQL 16) yapıldı.

## Denemeleri çalıştırmak (isteğe bağlı)

```
pip install -r requirements-dev.txt
python -m pytest
```

**508 deneme** geçmelidir (1.21.1: SQLite'ta ve PostgreSQL 16'da geçti). PostgreSQL için: `TEST_DATABASE_URL=postgresql://kullanici:sifre@localhost/bos_bir_veritabani` (veritabanı boş olmalı).
