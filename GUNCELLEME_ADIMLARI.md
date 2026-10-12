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

## SMS ve e-posta (şu an test modu)

- **SMS:** Türkiye'de bir sağlayıcıdan hesap + onaylı gönderici başlığı gerekir; sağlayıcı seçilince `SMS_PROVIDER` ve anahtarları **yalnız Render ortamına** yazılır, bağdaştırıcı `app/notify.py › PROVIDERS` içine eklenir (küçük iş). **Şu an sağlayıcı yok → SMS gitmez.**
- **E-posta:** Render'a `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` yazılırsa açılır. Gerçek SMTP sunucusuyla **denenmedi** (sahte arka uçla denendi).
- **Telefon / e-posta doğrulaması (kayıtta SMS kodu) henüz yok.** Hesap yapısı buna hazır tasarlandı; sağlayıcı bağlanınca eklenir. Ona kadar sahte hesapla puan şişirmek mümkündür (yeni hesaplar düşük ağırlıkla sayılır, yönetim yorumu gizleyebilir).
- Hiçbiri tanımlı değilken kurye teslim bağlantısını kendi SMS / WhatsApp uygulamasıyla iletir ya da QR gösterir. **Anahtarlar kodda, zip'te, depoda, sohbette yazılmaz; yalnız Render → Environment'ta durur.**

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
| `SMS_PROVIDER` | boş | SMS sağlayıcısı adı (bağdaştırıcı eklendikten sonra) |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` `SMTP_FROM` `SMTP_STARTTLS` | boş / `587` / … / `1` | E-posta gönderimi (587'de STARTTLS, 465'te SSL) |
| `ERROR_KEEP_DAYS` / `ERROR_MAX_ROWS` / `ERROR_DAILY_MAX` | `14` / `5000` / `3000` | Hata kayıtları |
| `DOOR_DAILY_MAX` / `DOOR_TRUST_SCORE` / `DOOR_MERGE_M` | `400` / `2` / `35` | Ortak kapı haritası sınırları |
| `DOOR_REQUIRE_CONSENT` | `0` | `1` yapma (uygulama eski `kapi` rızasını artık sormaz) |
| `RELAY_TTL_DAYS` / `RELAY_MAX_BYTES` / `RELAY_USER_MAX_BYTES` | `7` / `262144` / `10485760` | Mesaj aktarıcısı sınırları |
| `GROUP_MAX` | `7` | Grup üst sınırı (kuran dahil) |
| `WEB_APP_DIR` | boş | Web ikizi klasörü (varsayılan `app/webapp`) |
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
- **Ücretsiz Render PostgreSQL oluşturulduktan 30 gün sonra sona erer; 14 günlük ek süreden sonra veritabanı ve içindeki her şey silinir.** Üyeler, değerlendirmeler ve kapı haritası orada birikiyor: asıl varlığın bu. Kesin tarih için Render → `hafiza`. Ücretli plana geçmedikçe (ya da düzenli yedek almadıkça) veriler kaybolur.
- Ortak haritada **üçüncü kişilerin adres bilgisi** (sokak + kapı no + site/bina adı + konum; ad, telefon, daire, kat yok) toplanır. Hukuki metinler **taslaktır**; yayından önce gözden geçirilmelidir.
- Hesabı silme ucu henüz yok. Sunucu mesajın **içine bakmaz**.
- Web ikizi ve alıcı sayfası harita karolarını OpenStreetMap'ten alır; çok yoğun kullanımda kendi karo sağlayıcına geçmek gerekir.
- **Gerçek bir telefonda denenmedi.** Denemeler gerçek tarayıcıda (Chromium) ve gerçek yerel sunucuda (SQLite ve PostgreSQL 16) yapıldı.

## Denemeleri çalıştırmak (isteğe bağlı)

```
pip install -r requirements-dev.txt
python -m pytest
```

**296 deneme** geçmelidir (SQLite'ta, PostgreSQL 16'da ve Python 3.12'de geçti). PostgreSQL için: `TEST_DATABASE_URL=postgresql://kullanici:sifre@localhost/bos_bir_veritabani` (veritabanı boş olmalı).
