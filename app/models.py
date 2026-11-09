import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import relationship

from .db import Base


def uid() -> str:
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(String(32), primary_key=True, default=uid)
    phone = Column(String(16), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(120), nullable=False, default="")
    active_role = Column(String(16), nullable=False, default="courier")  # courier | employer | sponsor
    # Giriş için telefon yerine kullanılabilir. Küçük harfle saklanır, benzersizdir, boş olabilir (NULL).
    username = Column(String(32), unique=True, index=True)
    # İsteğe bağlı. Henüz DOĞRULANMIYOR (benzersiz de değil): doğrulama gelince doğrulanmış e-posta benzersiz olacak.
    email = Column(String(254), index=True)
    lat = Column(Float)
    lon = Column(Float)
    created_at = Column(DateTime(timezone=True), default=now)
    # --- Yönetim (Dilim 4). Eski satırlarda NULL = yok / 0 / kapalı sayılır ---
    banned = Column(Boolean, default=False)  # yönetim hesabı askıya aldı: giriş ve tüm istekler 403
    banned_reason = Column(String(300))
    banned_at = Column(DateTime(timezone=True))
    # Oturum sürümü: şifre değişince / yönetim "tüm oturumları kapat" deyince artar; eski sürümle imzalı oturumlar geçersiz olur
    token_version = Column(Integer, default=0)
    # Yönetim şifreyi sıfırladı: kullanıcı yeni şifre koyana kadar yalnız /v1/me ve /v1/me/password çalışır
    must_change_password = Column(Boolean, default=False)
    last_login_at = Column(DateTime(timezone=True))
    # Dilim 10: rehberden / numaradan bulunabilirim. NULL = açık sayılır (varsayılan açık; Gizlilik'ten kapatılır)
    findable = Column(Boolean, default=True)
    # Dilim 11: haritada görünecek ikonun KİMLİĞİ (çizim uygulamada; örn. "kedi"). Boş = seçilmedi
    map_icon = Column(String(24))
    # Dilim 12: haritada ROTA kullanıcılarına görünme (AYRI açık rıza; varsayılan KAPALI). Konum yalnız görünürken, yaklaşık, kısa süreli tutulur.
    visible_map = Column(Boolean, default=False)
    vis_lat = Column(Float)
    vis_lon = Column(Float)
    vis_at = Column(DateTime(timezone=True))
    # Dilim 13 (0.12.9): benzersiz, değişmeyen 7 haneli kullanıcı no (örn. 8472019; ilk hane 0 olmaz). Eski satırlar açılışta doldurulur (migrate.py).
    public_no = Column(String(7), unique=True, index=True)
    # 1.14.0: kalıcı ÇALIŞMA BÖLGESİ (kullanıcı kararı 22 Ekim 2026) — JSON {"p": il, "d": ilçe, "h": [en çok 3 mahalle]}. Her girişte
    # yeniden yazılmasın, telefon ↔ web aynı olsun diye sunucuda. Kişinin kendi tercihidir; başkasına gösterilmez.
    work_area = Column(Text)
    # 1.14.1: işletme LOGOSU (yalnız işveren / sponsor; kişilerde profil fotoğrafı yok, avatar var). base64 + gerçek tür + yükleme anı.
    # Logo dışı resim yönetimce silinir (yönetim › Logolar).
    logo = Column(Text)
    logo_type = Column(String(16))
    logo_at = Column(DateTime(timezone=True))
    # 1.20.0: hesap güvenliği (kullanıcı isteği 29 Ekim 2026). Doğrulanmış e-posta "şifremi unuttum" kodunun gideceği yerdir ve girişte
    # kullanılabilir (doğrulanmamış e-posta hiçbir işte kullanılmaz). E-posta / telefon değişince doğrulama düşer.
    email_verified_at = Column(DateTime(timezone=True))
    phone_verified_at = Column(DateTime(timezone=True))
    # Hesap silindi (kullanıcı kendisi sildi): satır yabancı anahtarlar bozulmasın diye kalır ama kişisel bilgi silinir (ad, telefon,
    # kullanıcı adı, e-posta, konum, logo…); oturum açılamaz. `phone_hash` yalnız KARA LİSTEDEKİ hesap silinirse yazılır: aynı numarayla
    # yeniden açılan hesap kara listeyi devralsın diye (geri çevrilemez özet; numaranın kendisi tutulmaz).
    deleted_at = Column(DateTime(timezone=True))
    phone_hash = Column(String(64), index=True)

    jobs_posted = relationship("Job", back_populates="employer", foreign_keys="Job.employer_id")


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String(32), primary_key=True, default=uid)
    employer_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    courier_id = Column(String(32), ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(160), nullable=False, default="Teslimat işi")
    note = Column(Text, default="")
    employer_lat = Column(Float, nullable=False)
    employer_lon = Column(Float, nullable=False)
    # open -> applied -> accepted -> departed -> done | cancelled
    status = Column(String(20), nullable=False, default="open", index=True)
    # Yeni akışta adresler sunucuya YAZILMAZ (işletmenin telefonunda kalır, mesajla gönderilir): ilanda yalnız sayı ve bölge
    package_count = Column(Integer, default=0)
    area = Column(String(160), default="")
    fee = Column(Float)  # işletmenin yazdığı teslimat ücreti (₺) — bilgi amaçlı; Rota ödeme almaz/aktarmaz
    applicant_id = Column(String(32), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now)
    accepted_at = Column(DateTime(timezone=True))
    departed_at = Column(DateTime(timezone=True))
    # 1.17.0: Rota coin ile ÖNE ÇIKAN ilan bu zamana kadar listenin başında ("ÖNE ÇIKAN İLAN" etiketiyle). Coin sunucuda harcanır.
    promoted_until = Column(DateTime(timezone=True))

    employer = relationship("User", foreign_keys=[employer_id], back_populates="jobs_posted")
    packages = relationship("JobPackage", back_populates="job", cascade="all, delete-orphan")


class JobPackage(Base):
    __tablename__ = "job_packages"
    id = Column(String(32), primary_key=True, default=uid)
    job_id = Column(String(32), ForeignKey("jobs.id"), nullable=False, index=True)
    customer = Column(String(160), nullable=False)
    phone = Column(String(16), nullable=False, index=True)
    address = Column(Text, nullable=False)
    neighborhood = Column(String(120), default="")
    street = Column(String(160), default="")
    door_number = Column(String(40), default="")
    apartment = Column(String(40), default="")
    floor = Column(String(40), default="")
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    precision = Column(String(40), default="")
    token = Column(String(32), unique=True, nullable=False, default=uid, index=True)
    pin_updated = Column(Boolean, default=False)
    pin_updated_at = Column(DateTime(timezone=True))
    status = Column(String(20), default="pending")
    sort_m = Column(Float, default=0)

    job = relationship("Job", back_populates="packages")


class ConsentLog(Base):
    """Açık rıza kaydı: kim, hangi metin sürümüne, hangi seçeneklerle, NE ZAMAN (sunucu saati) rıza verdi / geri çekti.
    Satırlar yalnız eklenir, hiç güncellenmez; son satır geçerli durumdur. Geri çekme = hepsi False olan yeni satır."""

    __tablename__ = "consent_log"
    id = Column(String(32), primary_key=True, default=uid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=0)  # aydınlatma / açık rıza metin sürümü
    voice = Column(Boolean, nullable=False, default=False)  # sesle yazma (ses yurt dışı servise gider)
    ocr = Column(Boolean, nullable=False, default=False)  # adres okuma (görüntü OCR servisine gider)
    grup_konum = Column(Boolean, nullable=False, default=False)  # konumumu grup arkadaşlarım görsün
    # Ortak kapı noktası katmanına katkı (teslim ettiğim kapının sokak/no/konumu tüm kullanıcılara görünür). Eski satırlarda NULL = kapalı
    kapi = Column(Boolean, default=False)
    # Araç profilimi işletmeler görsün (AYRI açık rıza; kurye araç ekranında verir / geri çeker). Eski satırlarda NULL = kapalı
    arac = Column(Boolean, default=False)
    aydinlatma_ms = Column(BigInteger, default=0)  # istemci saati (bilgi amaçlı, epoch ms)
    acik_riza_ms = Column(BigInteger, default=0)  # istemci saati (bilgi amaçlı, epoch ms)
    created_at = Column(DateTime(timezone=True), default=now, index=True)  # SUNUCU saati: hukuken geçerli zaman
    # Kullanım koşulları (sözleşme) onayı: hangi sürüm, istemci saati. 0 = henüz onaylanmadı / eski istemci
    sozlesme_version = Column(Integer, default=0)
    sozlesme_ms = Column(BigInteger, default=0)


class Device(Base):
    """Bir hesabın cihazları (telefon, web ikizi). Kimliği istemci üretir ve saklar. Mesaj aktarıcısı her mesajı
    alıcının (ve gönderenin öbür cihazlarının) her cihazına bir kez ulaştırır."""

    __tablename__ = "devices"
    id = Column(String(64), primary_key=True)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    platform = Column(String(16), default="")  # android | ios | web
    created_at = Column(DateTime(timezone=True), default=now)
    last_seen = Column(DateTime(timezone=True), default=now, index=True)


class RelayMsg(Base):
    """Geçici mesaj aktarıcısı: mesaj içeriği (adres listesi dahil) yalnız ulaştırılana kadar burada durur.
    Tüm hedef cihazlara ulaşınca silinir; ulaşamayan en geç RELAY_TTL_DAYS (varsayılan 7) gün sonra silinir."""

    __tablename__ = "relay_msgs"
    id = Column(String(32), primary_key=True, default=uid)
    sender_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)  # istemcinin JSON zarfı; sunucu içine bakmaz
    size = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=now)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class RelayDelivery(Base):
    """Bir mesajın bir cihaza (ya da henüz cihazı olmayan kullanıcıya: device_id NULL) ulaştırılma kaydı.
    Cihaz aldığını bildirince satır silinir; mesajın satırı kalmayınca mesaj da silinir."""

    __tablename__ = "relay_delivery"
    id = Column(String(32), primary_key=True, default=uid)
    msg_id = Column(String(32), ForeignKey("relay_msgs.id"), nullable=False, index=True)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String(64), index=True)  # NULL: kullanıcının ilk açılan cihazı alır


class DoorPoint(Base):
    """Ortak kapı noktası katmanı: OpenStreetMap'te olmayan kapı numaralarını kuryelerin teslimatlarıyla tamamlar.
    Burada KİŞİ BİLGİSİ YOKTUR: yalnız il/ilçe/mahalle/sokak/kapı no + koordinat + güven puanı. Kim bildirdi bilgisi
    ayrı tabloda (door_reports) ve yalnız geri çevrilemez kısa bir koddur; bu tablodan okunamaz.
    Aynı anahtar (ilçe+mahalle+sokak+kapı) için birbirinden uzak birden çok aday satır olabilir; en yüksek puanlı gösterilir."""

    __tablename__ = "door_points"
    id = Column(String(32), primary_key=True, default=uid)
    # Eşleştirme anahtarları (küçük harf, Türkçe karakter düz Latin, ek sözcükler atılmış)
    province_k = Column(String(60), nullable=False, default="")
    district_k = Column(String(80), nullable=False, default="")
    neigh_k = Column(String(100), nullable=False, default="")
    street_k = Column(String(120), nullable=False, default="")
    street_kind = Column(String(10), nullable=False, default="")  # cadde | sokak | bulvar | meydan | ""
    door_k = Column(String(8), nullable=False)  # ör. 12 ya da 12A (daire/kat atılmış)
    door_n = Column(Integer, nullable=False)  # sayısal kısım
    # Görünen adlar (ilk bildirimden)
    province = Column(String(60), default="")
    district = Column(String(80), default="")
    neighborhood = Column(String(100), default="")
    street = Column(String(120), default="")
    site = Column(String(60), default="")  # site / bina / yapı adı (halka açık; ör. "Güneş Sitesi"); kişi adı değil
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    score = Column(Float, nullable=False, default=0)  # onaylayan oyların ağırlık toplamı
    wrong = Column(Float, nullable=False, default=0)  # "yanlış" oylarının ağırlık toplamı
    confirms = Column(Integer, nullable=False, default=0)  # onaylayan farklı kişi sayısı
    hidden = Column(Boolean, nullable=False, default=False)  # yanlış oyları doğrulardan çoksa gösterilmez
    created_at = Column(DateTime(timezone=True), default=now)
    updated_at = Column(DateTime(timezone=True), default=now, index=True)

    __table_args__ = (
        Index("ix_door_points_door", "district_k", "neigh_k", "door_k"),
        Index("ix_door_points_street", "district_k", "neigh_k", "street_k"),
    )


class DoorReport(Base):
    """Bir kişinin bir kapı noktası için tek oyu (onay ya da yanlış). Kişi KENDİ KİMLİĞİYLE tutulmaz: reporter, hesap
    kimliğinden türetilmiş geri çevrilemez kısa koddur; yalnız aynı kişinin aynı noktayı birden çok kez saymasını önler."""

    __tablename__ = "door_reports"
    id = Column(String(32), primary_key=True, default=uid)
    point_id = Column(String(32), ForeignKey("door_points.id"), nullable=False, index=True)
    reporter = Column(String(32), nullable=False, index=True)
    vote = Column(String(8), nullable=False, default="confirm")  # confirm | wrong
    source = Column(String(12), nullable=False, default="checkin")  # checkin | pin | recipient
    weight = Column(Float, nullable=False, default=1.0)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, index=True)

    __table_args__ = (UniqueConstraint("point_id", "reporter", name="uq_door_report_point_reporter"),)


class Handoff(Base):
    """Teslim onayı ("Paketimi aldım"): kurye bir durak için alıcıya bağlantı üretir; alıcı bağlantıdan kapının yerini pinler,
    iletişim bilgisini düzeltir ve kurye hedefe ulaştıktan sonra "Paketimi aldım" kutusunu işaretler.
    GEÇMİŞ TUTULMAZ: alıcıyla ilgili her şey (telefon, e-posta, pin, durum) en geç expires_at'te (varsayılan 48 saat) silinir;
    "Paketimi aldım" işaretlenince telefon ve e-posta hemen silinir. Alıcının adı HİÇ saklanmaz."""

    __tablename__ = "handoffs"
    id = Column(String(32), primary_key=True, default=uid)
    token = Column(String(24), unique=True, nullable=False, index=True)  # alıcı bağlantısındaki tahmin edilemez kod
    courier_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    ref = Column(String(64), nullable=False, default="")  # kurye uygulamasındaki durak kimliği (yalnız eşleştirme için)
    status = Column(String(10), nullable=False, default="created")  # created | arrived | received
    phone = Column(String(16), nullable=False, default="")
    email = Column(String(254), nullable=False, default="")
    # Kapının halka açık adres parçaları (alıcı pini ortak haritaya bununla yazılır; daire/kat/ad YOK)
    province = Column(String(60), nullable=False, default="")
    district = Column(String(80), nullable=False, default="")
    neighborhood = Column(String(100), nullable=False, default="")
    street = Column(String(120), nullable=False, default="")
    site = Column(String(60), nullable=False, default="")
    door = Column(String(40), nullable=False, default="")
    lat = Column(Float, nullable=False)  # sistemin / kuryenin koyduğu pin (alıcı sayfasında başlangıç noktası)
    lon = Column(Float, nullable=False)
    pin_lat = Column(Float)
    pin_lon = Column(Float)
    pin_at = Column(DateTime(timezone=True))
    mode = Column(String(8), nullable=False, default="test")  # test | sms | email: bağlantı alıcıya nasıl ulaştı
    link_seen = Column(Boolean, nullable=False, default=False)  # kurye bağlantıyı gördü / paylaştı (alıcı pininin ağırlığı düşer)
    send_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    arrived_at = Column(DateTime(timezone=True))
    received_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    # Kurye teslimi bitirdi (uygulamadan sildi): telefon / e-posta silinir, kayıt kurye için görünmez olur; alıcı değerlendirme
    # yazabilsin diye süresi dolana kadar (en çok HANDOFF_REVIEW_HOURS) kalır
    closed_at = Column(DateTime(timezone=True))
    # 1.18.0 — alıcıya süreç takibi (Hazırlanıyor → Yolda → Teslim edildi):
    # Kaydı işveren açtıysa (adres doğrulama / "📦 Yola çıktı") işverenin kimliği. Listeyi alan kurye (açık görevle) kayda BAĞLANIR:
    # courier_id kuryeye geçer, işveren durumu görmeye devam eder. Kuryenin kendi açtığı kayıtta boş (ya da listenin işvereni).
    employer_id = Column(String(32), ForeignKey("users.id"), nullable=True, index=True)
    departed_at = Column(DateTime(timezone=True))  # paket yola çıktı (işveren "📦 Yola çıktı" ya da kurye hedefe ulaştı)
    delivered_at = Column(DateTime(timezone=True))  # kurye "Teslim edildi" dedi (alıcı kutuyu işaretlemese de çubuk sona varır)

    __table_args__ = (Index("ix_handoffs_courier_ref", "courier_id", "ref"),)


class HandoffPush(Base):
    """Alıcının tarayıcı bildirimi aboneliği (1.18.0). Alıcı sayfada "🔔 Bildirim al" deyip izin verirse yazılır; YALNIZ o teslim
    içindir. Teslim edilince / kurye teslimi bitirince / kayıt silinince (en geç 48 saat) silinir. Ad, telefon, hesap YOK."""

    __tablename__ = "handoff_push"
    id = Column(String(32), primary_key=True, default=uid)
    handoff_id = Column(String(32), ForeignKey("handoffs.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint = Column(String(1024), nullable=False)  # tarayıcının bildirim servisindeki adresi
    p256dh = Column(String(128), nullable=False)
    auth = Column(String(48), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)  # teslim kaydıyla aynı (ya da daha erken)


class ClientError(Base):
    """Uygulamadan gelen hata raporu (kişisel veri ayıklanmış). Hesap kimliği YOK; yalnız kuruluma özel rastgele kodun özeti."""

    __tablename__ = "client_errors"
    id = Column(String(32), primary_key=True, default=uid)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    fingerprint = Column(String(24), nullable=False, index=True)
    kind = Column(String(10), nullable=False, default="js")  # js | promise | render | api
    fatal = Column(Boolean, nullable=False, default=False)
    message = Column(String(300), nullable=False, default="")
    stack = Column(Text, nullable=False, default="")
    screen = Column(String(60), nullable=False, default="")
    platform = Column(String(12), nullable=False, default="")
    os_version = Column(String(20), nullable=False, default="")
    app_version = Column(String(20), nullable=False, default="")
    install = Column(String(16), nullable=False, default="")


class Admin(Base):
    """Yönetici hesabı (yalnız web yönetim sayfasından girilir; uygulama kullanıcılarından AYRIDIR). İlk yönetici Render'daki
    ADMIN_KEY ile oluşturulur. İki adımlı doğrulama (TOTP) isteğe bağlıdır; ADMIN_REQUIRE_2FA=1 ile zorunlu olur."""

    __tablename__ = "admins"
    id = Column(String(32), primary_key=True, default=uid)
    username = Column(String(32), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    totp_secret = Column(String(64))  # base32; boş = kurulmamış
    totp_on = Column(Boolean, default=False)
    totp_last_step = Column(BigInteger, default=0)  # aynı kodun ikinci kez kullanılmasını önler
    token_version = Column(Integer, default=0)
    disabled = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=now)
    created_by = Column(String(40), default="")
    last_login_at = Column(DateTime(timezone=True))


class AdminAudit(Base):
    """Yönetici işlem günlüğü: kim, ne zaman, kime/neye ne yaptı. Yalnız eklenir. Şifre / kod gibi gizli değerler YAZILMAZ;
    telefon ve e-posta maskeli yazılır."""

    __tablename__ = "admin_audit"
    id = Column(String(32), primary_key=True, default=uid)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    admin_id = Column(String(32), index=True)
    admin_name = Column(String(32), nullable=False, default="")
    action = Column(String(40), nullable=False, index=True)
    target_type = Column(String(12), nullable=False, default="")
    target_id = Column(String(40), nullable=False, default="")
    detail = Column(String(500), nullable=False, default="")
    ip = Column(String(64), nullable=False, default="")


class Event(Base):
    """Etkinlik akışı (yönetim görür): kim ne zaman ne yaptı. Mesaj / adres İÇERİĞİ yazılmaz; yalnız olayın türü ve kısa bilgi
    (ör. 'kaç kişiye gönderdi'). EVENT_KEEP_DAYS (varsayılan 60) gün sonra silinir."""

    __tablename__ = "events"
    id = Column(String(32), primary_key=True, default=uid)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    kind = Column(String(24), nullable=False, index=True)
    user_id = Column(String(32), index=True)  # eylemi yapan hesap (alıcı gibi hesapsızlarda boş)
    target_id = Column(String(40), nullable=False, default="")
    meta = Column(String(500), nullable=False, default="")  # kısa JSON


class Review(Base):
    """Değerlendirme (anket): alıcı → kurye (bağlantı kodu ile, hesapsız), işveren → kurye, kurye → işveren. Hepsi 5 yıldız üzerinden.
    Alıcının kimliği (ad, telefon) HİÇ tutulmaz; tek bağ, bağlantının kimliğidir (bir bağlantıya bir değerlendirme).
    Yorum metnini yalnız yönetim okur; puanlanan taraf ve kuryeler yalnız yıldız ortalamasını (ve işletmenin ödeme oranlarını) görür."""

    __tablename__ = "reviews"
    id = Column(String(32), primary_key=True, default=uid)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    updated_at = Column(DateTime(timezone=True), default=now)
    source = Column(String(10), nullable=False, index=True)  # recipient | employer | courier
    subject_id = Column(String(32), nullable=False, index=True)  # puanlanan kurye / işveren
    subject_role = Column(String(10), nullable=False, default="courier")
    author_id = Column(String(32), index=True)  # yazan hesap; alıcıda boş
    handoff_id = Column(String(32), unique=True)  # yalnız alıcı yorumu: bir bağlantıya bir değerlendirme
    ref = Column(String(64), nullable=False, default="")  # ilan / durak kimliği (isteğe bağlı)
    stars = Column(Integer, nullable=False)  # 1–5
    app_stars = Column(Integer)  # yalnız alıcı: uygulama / teslim deneyimi (1–5)
    answers = Column(Text, nullable=False, default="{}")  # JSON: tags, addressOk, onTime, complete
    comment = Column(String(500), nullable=False, default="")
    weight = Column(Float, nullable=False, default=1.0)  # güvenilir yoldan gelen 1; kuryenin görebildiği bağlantıdan gelen daha düşük
    hidden = Column(Boolean, default=False)  # yönetim gizledi: ortalamaya girmez, kimseye gösterilmez
    hidden_note = Column(String(200), nullable=False, default="")


class Follow(Base):
    """Takip ("Follow"): işveren, mesajlaştığı bir kuryeyi harita üzerinde canlı izler. İzin KURYEDEN gelir, her seferinde:
    işveren istek gönderir (asked), kurye kabul edince (active) konumunu paylaşmaya başlar; kurye istediği an durdurabilir.
    Satırda iki şey durur: (1) CANLI nokta (last_*): yenisi gelince üstüne yazılır, takip bitince ya da 30 dakikadan eskiyse silinir;
    (2) İZ (follow_points): seyreltilmiş geçmiş noktalar + son ilerleme, takip bittikten sonra FOLLOW_KEEP_HOURS (48 saat) boyunca
    işveren, kurye ve yönetim tarafından görülebilir (şikayet / itiraz durumunda "son yapılan işler" kaydı), sonra satırla birlikte silinir.
    Etkinlik akışına koordinat yazılmaz. Bir kuryenin aynı anda tek aktif takibi olur."""

    __tablename__ = "follows"
    id = Column(String(32), primary_key=True, default=uid)
    employer_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    courier_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(10), nullable=False, default="asked", index=True)  # asked | active | ended
    # declined | cancelled | stopped_employer | stopped_courier | done | expired | role | ban
    end_reason = Column(String(20), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now)
    accepted_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True), index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)  # istek: yanıt süresi; aktif: en uzun takip süresi
    # CANLI nokta: yalnız sonuncusu (üstüne yazılır). Takip bitince hepsi NULL. Seyreltilmiş iz ayrı tabloda (follow_points) 48 saat saklanır.
    last_lat = Column(Float)
    last_lon = Column(Float)
    last_acc = Column(Float)
    last_at = Column(DateTime(timezone=True))
    # Kuryenin ilerlemesi: bitirilen / başarısız durakların kimlikleri (telefonun ürettiği rastgele kodlar), toplam, her kimliğin sunucuda ilk
    # görüldüğü an ("t": {kimlik: saniye}) ve her biten durak için KISA ADRES ETİKETİ ("a": {kimlik: "Sokak No:N · Mahalle"}; müşteri adı /
    # telefon / daire / kat YOK). Takip bitince de 48 saat saklanır.
    progress = Column(Text, nullable=False, default="")
    # İz seyreltme: son kaydedilen iz noktasının zamanı ve kayıtlı nokta sayısı
    trail_at = Column(DateTime(timezone=True))
    trail_n = Column(Integer, default=0)
    # 1.14.0: takip, eşleşmiş bir GÖREVE bağlı başlar (önce "Adres Listesi Paylaş", sonra takip). Görev no burada da durur (iki tarafta aynı).
    task_no = Column(String(7))

    __table_args__ = (
        # Bir kuryenin aynı anda tek aktif takibi (iki işletme aynı kuryeyi aynı anda izleyemez)
        Index("uq_follow_courier_active", "courier_id", unique=True, sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),
        # Aynı işveren-kurye çifti için tek açık (bekleyen / aktif) kayıt: tekrarlanan istek yeni satır açmaz
        Index("uq_follow_open_pair", "employer_id", "courier_id", unique=True, sqlite_where=text("status IN ('asked','active')"), postgresql_where=text("status IN ('asked','active')")),
    )


class Task(Base):
    """GÖREV EŞLEŞMESİ (1.14.0, kullanıcı kararı 22 Ekim 2026). İşveren "Adres Listesi Paylaş" dediği anda ilan (ya da liste) bir kuryeye
    BAĞLANIR ve göreve özel benzersiz 7 haneli GÖREV NO verilir (uygulamada GRV-xxxxxxx). Kurye listeyi kabul edince aynı numarayla EŞLEŞİR
    (matched): iki tarafta aynı numara, veri tutarlılığı. Kurallar: bir ilan / liste (`ref`) aynı anda TEK kuryeye bağlanır; bir kuryenin
    aynı işverenden TEK açık görevi olur (teslimatlar bitmeden ikinci liste verilmez); canlı takip yalnız eşleşmiş görevle başlar.
    Adres, alıcı adı, telefon YOK (adresler telefonlarda kalır): yalnız başlık ve adres sayısı. Biten görev 48 saat sonra silinir."""

    __tablename__ = "tasks"
    id = Column(String(32), primary_key=True, default=uid)
    task_no = Column(String(7), unique=True, nullable=False, index=True)
    employer_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    courier_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(String(32), ForeignKey("jobs.id"), nullable=True, index=True)  # ilan üzerinden paylaşıldıysa
    ref = Column(String(64), nullable=False)  # ilan kimliği ya da telefondaki listenin anahtarı ("L:<sekme>", "F:<dosya listesi>")
    title = Column(String(160), nullable=False, default="")
    count = Column(Integer, nullable=False, default=0)
    status = Column(String(10), nullable=False, default="shared", index=True)  # shared | matched | ended
    # declined | cancelled | stopped_courier | done | job_closed | ban
    end_reason = Column(String(20), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now)
    matched_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True), index=True)

    __table_args__ = (
        # Bir ilan / liste aynı anda tek kuryeye bağlı
        Index("uq_task_open_ref", "employer_id", "ref", unique=True, sqlite_where=text("status IN ('shared','matched')"), postgresql_where=text("status IN ('shared','matched')")),
        # Bir kuryenin aynı işverenden tek açık görevi
        Index("uq_task_open_pair", "employer_id", "courier_id", unique=True, sqlite_where=text("status IN ('shared','matched')"), postgresql_where=text("status IN ('shared','matched')")),
    )


class FollowPoint(Base):
    """Takip izi: kuryenin takip sürerken paylaştığı konumlardan seyreltilmiş (en sık ~15 sn'de bir) geçmiş noktalar.
    Yalnız takip AKTİFKEN yazılır; takip satırıyla birlikte (bitişten FOLLOW_KEEP_HOURS sonra) silinir."""

    __tablename__ = "follow_points"
    id = Column(Integer, primary_key=True, autoincrement=True)
    follow_id = Column(String(32), ForeignKey("follows.id"), nullable=False, index=True)
    at = Column(DateTime(timezone=True), nullable=False, default=now)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)


class CourierProfile(Base):
    """Kuryenin işletmelere GÖSTERDİĞİ araç profili. Yalnız kuryenin AYRI açık rızası (consent_log.arac) varken yazılır ve okunur;
    rıza geri çekilince satır silinir. Plaka ve araç fotoğrafı BURADA YOK: telefondan çıkmaz."""

    __tablename__ = "courier_profiles"
    user_id = Column(String(32), ForeignKey("users.id"), primary_key=True)
    vehicle = Column(String(12), nullable=False, default="motor")  # motor | araba | bisiklet | yaya
    capacity = Column(String(40), nullable=False, default="")  # serbest metin, ör. "2 koli, 1 sipariş çantası"
    areas = Column(Text, nullable=False, default="[]")  # JSON liste: çalışma bölgeleri (en çok 8)
    available = Column(Boolean, nullable=False, default=True)
    from_hhmm = Column(String(5), nullable=False, default="09:00")
    to_hhmm = Column(String(5), nullable=False, default="22:00")
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)


class MapBlock(Base):
    """Engelleme: engelleyen engellenenin haritasında görünmez, engellenen onu göremez / ona dürtme gönderemez."""

    __tablename__ = "map_blocks"
    id = Column(String(32), primary_key=True, default=uid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    blocked_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("user_id", "blocked_id", name="uq_map_block"),)


class Nudge(Base):
    """Kısıtlı selam: serbest metin YOK; yalnız wave (el sallama) | nudge (dürtme) | hello ("Merhaba, ben de ROTA kullanıcısıyım").
    Alıcı okuyunca ya da NUDGE_KEEP_HOURS sonra silinir."""

    __tablename__ = "nudges"
    id = Column(String(32), primary_key=True, default=uid)
    from_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    to_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(8), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, index=True)


# ------------------------------------------------------------------ 1.15.0: PUAN VE ÖDÜL (kullanıcı kararları 26–27 Ekim 2026)
class PointEntry(Base):
    """PUAN DEFTERİ: puan yalnız burada tutulur (telefon yalnız gösterir). Satırlar yalnız eklenir; bakiye = delta toplamı.
    kind: delivery (+10, doğrulanmış teslim) | hold (−bedel, ödül alındı) | refund (+bedel, ödül iade) | expire (−, 12 ay kullanılmayan)
    | admin (yönetim düzeltmesi). Kişi verisi yok: adres, alıcı, konum burada tutulmaz."""

    __tablename__ = "point_entries"
    id = Column(String(32), primary_key=True, default=uid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    delta = Column(Integer, nullable=False)
    kind = Column(String(16), nullable=False, index=True)
    ref = Column(String(40), nullable=False, default="")  # teslim kaydı / ödül talebi kimliği
    note = Column(String(120), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now, index=True)


class DeliveryProof(Base):
    """TESLİM DOĞRULAMA KAYDI (bir durak için bir satır). Puan yalnız doğrulanmış teslime: alıcı "Paketimi aldım" dedi (recipient) ya da
    kurye kapıda GPS ile ≤50 m (gps). Adres YOK: yalnız telefonda üretilmiş geri çevrilemez özet (addr_key; "aynı adrese aynı gün tekrar
    puan yok" kuralı için) ve ~110 m'lik yaklaşık konum hücresi (cell; yönetimin şüpheli toplanma denetimi için). DELIVERY_PROOF_KEEP_DAYS
    (30) gün sonra silinir; kalıcı olan yalnız puan defterindeki +10 satırıdır."""

    __tablename__ = "delivery_proofs"
    id = Column(String(32), primary_key=True, default=uid)
    courier_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    ref = Column(String(64), nullable=False)  # telefondaki durak kimliği
    addr_key = Column(String(40), nullable=False, default="")
    day = Column(String(10), nullable=False, index=True)  # Türkiye günü (YYYY-AA-GG)
    method = Column(String(10), nullable=False, default="none")  # recipient | gps | none
    points = Column(Integer, nullable=False, default=0)
    reason = Column(String(12), nullable=False, default="")  # '' | unverified | cap | same_addr
    dist_m = Column(Float)
    acc_m = Column(Float)
    cell = Column(String(16), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    awarded_at = Column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("courier_id", "ref", name="uq_delivery_proof_ref"),)


class Reward(Base):
    """Sponsorun / işverenin kuryelere PUANLA verdiği ödül. Bedel basamaklıdır (1000 / 2000 / 3000 / 5000 puan); 1 puan = 0,10 TL referans
    değer: ürünün beyan edilen değeri basamağın TL karşılığının en az %80'i olmalı (kurye için adil). Ürün ve kargo parasını ödülü koyan öder;
    Rota para tutmaz, puan nakde çevrilmez."""

    __tablename__ = "rewards"
    id = Column(String(32), primary_key=True, default=uid)
    owner_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    owner_role = Column(String(10), nullable=False, default="sponsor")  # sponsor | employer
    brand = Column(String(60), nullable=False, default="")  # kuryelerin gördüğü işletme / firma adı
    name = Column(String(60), nullable=False)
    note = Column(String(200), nullable=False, default="")
    tier = Column(Integer, nullable=False)  # puan bedeli
    value_tl = Column(Integer, nullable=False)  # ürünün beyan edilen değeri (TL)
    qty_total = Column(Integer, nullable=False, default=1)
    qty_left = Column(Integer, nullable=False, default=1)
    ends_at = Column(DateTime(timezone=True), index=True)
    status = Column(String(10), nullable=False, default="active", index=True)  # active | removed
    created_at = Column(DateTime(timezone=True), default=now, index=True)


class RewardPhoto(Base):
    """1.25.0: ödül ürününün fotoğrafları (en çok 3; kullanıcı kararı 6 Kasım 2026: "3 resimlik foto galeri"). Yalnız ürün fotoğrafı;
    kişi fotoğrafı yüklenirse yönetim siler. Herkese açık (katalogda oturumsuz <img>), logo gibi base64 saklanır."""

    __tablename__ = "reward_photos"
    id = Column(String(32), primary_key=True, default=uid)
    reward_id = Column(String(32), ForeignKey("rewards.id"), nullable=False, index=True)
    idx = Column(Integer, nullable=False, default=0)  # 0, 1, 2
    data = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, index=True)


class RewardClaim(Base):
    """Ödül talebi. Kurye "Al" der → puan BLOKE (defterde −bedel), ödülü koyana bildirim → 7 gün içinde kargolar (firma + takip no) →
    kuryeye "Ürünü aldın mı?" → evet: kapanır (+ 1–5 yıldız memnuniyet) · hayır: yönetime düşer. Süreler: 7 günde kargolanmazsa puan iade;
    kargodan sonra 14 günde cevap yoksa "teslim alındı" sayılır. Kargo adresi YALNIZ ödülü koyan görür ve talep kapanınca silinir
    (en geç 60 gün)."""

    __tablename__ = "reward_claims"
    id = Column(String(32), primary_key=True, default=uid)
    code = Column(String(12), unique=True, nullable=False, index=True)
    reward_id = Column(String(32), ForeignKey("rewards.id"), nullable=False, index=True)
    owner_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    courier_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    reward_name = Column(String(60), nullable=False, default="")
    brand = Column(String(60), nullable=False, default="")
    cost = Column(Integer, nullable=False)
    # pending (kargo bekliyor) | shipped | received | disputed | refunded | cancelled
    status = Column(String(10), nullable=False, default="pending", index=True)
    ship_name = Column(String(80), nullable=False, default="")
    ship_phone = Column(String(16), nullable=False, default="")
    ship_address = Column(String(300), nullable=False, default="")
    carrier = Column(String(40), nullable=False, default="")
    tracking = Column(String(40), nullable=False, default="")
    stars = Column(Integer)
    courier_note = Column(String(200), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    ship_due_at = Column(DateTime(timezone=True), nullable=False, index=True)
    shipped_at = Column(DateTime(timezone=True))
    on_time = Column(Boolean)  # 7 gün içinde kargolandı mı (sponsor karnesi)
    answer_due_at = Column(DateTime(timezone=True), index=True)
    received_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True), index=True)
    # '' | courier_received | auto_received | late_ship | owner_cancel | courier_cancel | admin_refund | admin_received
    close_reason = Column(String(16), nullable=False, default="")


class Notice(Base):
    """Sistem bildirimi (uygulamada 🔔 Uyarılar ve duyurular): ör. "KRY-… kuryesi ürününüzü almaya hak kazandı". Uygulama alınca siler
    (ack); alınmayan NOTICE_KEEP_DAYS (30) gün sonra silinir."""

    __tablename__ = "notices"
    id = Column(String(32), primary_key=True, default=uid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(24), nullable=False, default="")
    text = Column(String(300), nullable=False, default="")
    ref = Column(String(40), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now, index=True)


class UserMark(Base):
    """Yönetimin bir hesaba koyduğu işaretler (1.16.0; kullanıcı kararı 27 Ekim 2026). Kayıt yalnız işaret konunca açılır.
    - KARA LİSTE (`blacklisted` + `note`): not zorunlu, YALNIZ yönetim görür. Kurye ödül alamaz; sponsor / işveren ödül koyamaz ve ödülleri
      katalogda görünmez (kargo bekleyen talepleri yine gönderir). Hesap askıya alma (men) bundan ayrıdır.
    - ÖNE ÇIKARMA (`featured_until`): ödülleri katalogda en üstte "⭐ Öne çıkan".
    - AF (`pardon_until`): düzensiz sayılıp katalogdan inen ödül koyanın ödülleri bu tarihe dek yine görünür.
    Not kara listeden çıkınca da durur (geçmiş); her değişiklik işlem günlüğüne (admin_audit) yazılır."""

    __tablename__ = "user_marks"
    user_id = Column(String(32), ForeignKey("users.id"), primary_key=True)
    blacklisted = Column(Boolean, nullable=False, default=False, index=True)
    note = Column(String(500), nullable=False, default="")
    listed_at = Column(DateTime(timezone=True))
    listed_by = Column(String(32), nullable=False, default="")
    note_at = Column(DateTime(timezone=True))
    note_by = Column(String(32), nullable=False, default="")
    featured_until = Column(DateTime(timezone=True), index=True)
    featured_by = Column(String(32), nullable=False, default="")
    pardon_until = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=now)
    updated_at = Column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------------------------------------------------------- 1.17.0: hesap verisi sunucuda
# Kullanıcı kararı (28 Ekim 2026): "Sunucuyu hesap başına tek kaynak yapalım; çevrimiçi olunca telefonda ya da web ikizinde güncellensin.
# Reklam deposu kesinlikle online olmalı. IBAN yok. Rehber telefonda kalır; isteyen 24 saatlik yedek bırakır." Kurallar: app/account.py.
class AccountState(Base):
    """Hesabın EŞİTLEME SAATİ: hesaptaki her değişiklikte 1 artar. Cihaz `GET /v1/account?since=<saat>` ile yalnız değişenleri alır.
    Aynı hesabın yazımlarını (coin harcaması dahil) sıraya dizer: her yazım önce bu satırı günceller (satır kilidi)."""

    __tablename__ = "account_state"
    user_id = Column(String(32), ForeignKey("users.id"), primary_key=True)
    clock = Column(BigInteger, nullable=False, default=0)
    coins_rev = Column(BigInteger, nullable=False, default=0)  # coin defteri en son hangi saatte değişti
    ads_rev = Column(BigInteger, nullable=False, default=0)  # reklam kayıtları en son hangi saatte değişti
    updated_at = Column(DateTime(timezone=True), default=now)


class AccountDoc(Base):
    """Hesabın bir BÖLÜMÜ (bildirim / gizlilik tercihleri, sürüş ayarı, işletme / sponsor profili, şablonlar, ürün listesi), JSON.
    `rev`: yazıldığı andaki eşitleme saati. `edited_ms`: kullanıcının düzenlediği an (cihaz saati) — çakışmada SON DÜZENLENEN geçerli olur."""

    __tablename__ = "account_docs"
    user_id = Column(String(32), ForeignKey("users.id"), primary_key=True)
    section = Column(String(24), primary_key=True)
    data = Column(Text, nullable=False, default="{}")
    rev = Column(BigInteger, nullable=False, default=0, index=True)
    edited_ms = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=now)


class StorePurchase(Base):
    """1.25.0: mağaza (Google Play / App Store) coin satın alması — makbuz BİR KEZ yazılır (aynı makbuz ikinci kez gelirse coin eklenmez).
    txn_key: Google'da satın alma jetonunun SHA-256 özeti, Apple'da işlem numarası. Hesap silinince de kalır (muhasebe; kişisel bilgi yok)."""

    __tablename__ = "store_purchases"
    id = Column(String(32), primary_key=True, default=uid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    store = Column(String(8), nullable=False)  # google | apple
    txn_key = Column(String(64), nullable=False)
    order_id = Column(String(120), nullable=False, default="")
    product_id = Column(String(100), nullable=False, default="")
    coins = Column(Integer, nullable=False)
    env = Column(String(16), nullable=False, default="Production")  # Production | Sandbox | Test
    status = Column(String(16), nullable=False, default="credited")  # credited | refunded (1.28.0: mağaza iade etti → coin düşüldü)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    refunded_at = Column(DateTime(timezone=True))  # 1.28.0
    __table_args__ = (UniqueConstraint("store", "txn_key", name="uq_store_txn"),)


class CoinEntry(Base):
    """Rota coin defteri: bakiye = hareketlerin toplamı. Coin nakde çevrilmez, aktarılmaz, süresi dolmaz (kullanım koşulları › 4. COIN).
    kind: test (mağaza bağlanana kadar test satın alma) | store (mağaza satın alması, 1.25.0: app/iap.py) | ad (harita reklamı) | promote (öne çıkan ilan)
    | admin (yönetim düzeltmesi)."""

    __tablename__ = "coin_entries"
    id = Column(String(32), primary_key=True, default=uid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    delta = Column(Integer, nullable=False)
    kind = Column(String(16), nullable=False)
    label = Column(String(160), nullable=False, default="")
    ref = Column(String(40), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now, index=True)


class AdBooking(Base):
    """REKLAM DEPOSU: haritada bir noktada N gün reklam (sponsor ve işveren; Rota coin ile). Paketle alınan reklamın her noktası ayrı satırdır
    (aynı `group_id`). Kaldırılan reklam silinmez, `removed_at` yazılır (coin iade edilmez); kuryelere yalnız süresi dolmamış, kaldırılmamış
    ve sahibi askıda olmayan reklamlar gösterilir."""

    __tablename__ = "ad_bookings"
    id = Column(String(32), primary_key=True, default=uid)
    owner_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    label = Column(String(60), nullable=False, default="")
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    days = Column(Integer, nullable=False)
    cost = Column(Integer, nullable=False)
    package_id = Column(String(16), nullable=False, default="")
    group_id = Column(String(32), nullable=False, default="")
    starts_at = Column(DateTime(timezone=True), default=now)
    ends_at = Column(DateTime(timezone=True), nullable=False, index=True)
    removed_at = Column(DateTime(timezone=True))
    removed_by = Column(String(16), nullable=False, default="")  # owner | admin
    created_at = Column(DateTime(timezone=True), default=now)
    # 1.26.0: "reklamın yarın bitiyor" hatırlatması gönderildi (uzatılınca boşalır, yeni bitişten önce yine gider)
    reminded_at = Column(DateTime(timezone=True))


class AdStat(Base):
    """1.26.0 REKLAM İSTATİSTİĞİ (gün başına): kuryenin teslim haritasında reklam kaç kez göründü (shows), kaç kez dokunuldu (taps), o gün
    kaç ayrı kurye gördü (viewers). Reklam verene yalnız SAYILAR gösterilir; hangi kuryenin gördüğü gösterilmez. Gün = Türkiye tarihi."""

    __tablename__ = "ad_stats"
    ad_id = Column(String(32), ForeignKey("ad_bookings.id"), primary_key=True)
    day = Column(String(10), primary_key=True)  # 2026-11-07
    shows = Column(Integer, nullable=False, default=0)
    taps = Column(Integer, nullable=False, default=0)
    viewers = Column(Integer, nullable=False, default=0)


class AdSeen(Base):
    """1.26.0: reklamı gören kurye (toplam kaç AYRI kurye gördü sayılsın diye; reklam verene kimlik gösterilmez)"""

    __tablename__ = "ad_seen"
    ad_id = Column(String(32), ForeignKey("ad_bookings.id"), primary_key=True)
    user_id = Column(String(32), ForeignKey("users.id"), primary_key=True)
    last_day = Column(String(10), nullable=False, default="")


class Survey(Base):
    """1.26.0 ANKET (kullanıcı kararı 7 Kasım 2026: "anketler ekranını sunucuya arşivlemeliyiz ve veriyi tutmalıyız"). Soruyu yönetim yazar;
    kuryeler yanıtlar; işletmeler yalnız ORTALAMAYI görür (en az 3 yanıt). Kapanan anket silinmez, ARŞİVE geçer (yanıtlarıyla)."""

    __tablename__ = "surveys"
    id = Column(String(32), primary_key=True, default=uid)
    q = Column(String(200), nullable=False)
    kind = Column(String(12), nullable=False, default="ucret")  # ucret | saat | sayi
    unit = Column(String(16), nullable=False, default="₺")
    min_v = Column(Float, nullable=False, default=1)
    max_v = Column(Float, nullable=False, default=100000)
    status = Column(String(10), nullable=False, default="active", index=True)  # active | archived
    created_by = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now)
    archived_at = Column(DateTime(timezone=True))


class SurveyAnswer(Base):
    """1.26.0: kuryenin anket yanıtı (anket başına tek yanıt; değiştirilebilir). Bölge = yanıt anındaki çalışma bölgesi (il / ilçe)."""

    __tablename__ = "survey_answers"
    id = Column(String(32), primary_key=True, default=uid)
    survey_id = Column(String(32), ForeignKey("surveys.id"), nullable=False, index=True)
    # hesap silinince boşalır: yanıt arşivde ANONİM kalır (ortalama bozulmasın)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=True, index=True)
    value = Column(Float, nullable=False)
    province = Column(String(60), nullable=False, default="")
    district = Column(String(80), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now)
    updated_at = Column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("survey_id", "user_id", name="uq_survey_answer"),)


class BackupRun(Base):
    """1.26.0 VERİTABANI YEDEĞİ: her yedek alınışının kaydı (dosyanın kendisi burada DEĞİL: GitHub'da ya da yöneticinin bilgisayarında)."""

    __tablename__ = "backup_runs"
    id = Column(String(32), primary_key=True, default=uid)
    at = Column(DateTime(timezone=True), default=now, index=True)
    by = Column(String(16), nullable=False, default="")  # schedule | admin
    size = Column(Integer, nullable=False, default=0)
    tables = Column(Integer, nullable=False, default=0)
    rows = Column(Integer, nullable=False, default=0)


class GuideBackup(Base):
    """REHBER YEDEĞİ (isteğe bağlı): rehberin asıl yeri telefondur. Telefon değiştiren kullanıcı rehberini sunucuya bırakır, yeni telefonda
    girişte geri alır. Hesap başına tek yedek; `expires_at` (24 saat) dolunca kendiliğinden silinir. Yalnız hesabın sahibi okur."""

    __tablename__ = "guide_backups"
    user_id = Column(String(32), ForeignKey("users.id"), primary_key=True)
    data = Column(Text, nullable=False, default="[]")
    n = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=now)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


# ---------------------------------------------------------------------------------------------------------------- 1.19.0: telefona bildirim
class UserPush(Base):
    """Bildirim gelecek cihaz (1.19.0; kullanıcı isteği 29 Ekim 2026). `kind`: expo (telefon uygulaması: ExponentPushToken[…]) | web (web
    ikizi: tarayıcının bildirim adresi + şifreleme anahtarları). Mesaj cihazına (`device_id` = relay cihaz kimliği) bağlıdır: çıkışta silinir.
    Bildirim metni kişisel bilgi içermez; burada yalnız cihazın bildirim adresi durur. Servis "cihaz yok" derse silinir."""

    __tablename__ = "user_push"
    id = Column(String(32), primary_key=True, default=uid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(8), nullable=False, default="expo")
    token = Column(String(1024), nullable=False, unique=True)
    p256dh = Column(String(128), nullable=False, default="")
    auth = Column(String(48), nullable=False, default="")
    platform = Column(String(16), nullable=False, default="")
    device_id = Column(String(64), nullable=False, default="", index=True)
    created_at = Column(DateTime(timezone=True), default=now)
    seen_at = Column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------------------------------------------------------- 1.20.0: hesap güvenliği
class AuthCode(Base):
    """Tek kullanımlık 6 haneli DOĞRULAMA KODU (1.20.0): şifremi unuttum (reset), e-posta doğrulama (email), telefon doğrulama (phone),
    telefon değiştirme (phone_change). Kodun kendisi saklanmaz: yalnız HMAC özeti (`code_hash`). SMS / e-posta servisi bağlı değilken
    (deneme modu) ya da gönderim başarısızsa kod kişiye ULAŞMAZ: o zaman kod (`code_plain`) yönetim panelinde görünür — yönetim kişinin
    kimliğini doğrulayıp kodu söyler (her bakış işlem günlüğüne yazılır). Kullanılınca / süresi dolunca `code_plain` silinir; satır 2 gün sonra."""

    __tablename__ = "auth_codes"
    id = Column(String(32), primary_key=True, default=uid)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    purpose = Column(String(16), nullable=False, index=True)  # reset | email | phone | phone_change
    channel = Column(String(8), nullable=False, default="sms")  # email | sms
    target = Column(String(254), nullable=False, default="")  # kodun gittiği e-posta / telefon (telefon değişiminde YENİ numara)
    code_hash = Column(String(64), nullable=False)
    code_plain = Column(String(8))  # yalnız kişiye ulaşmamış kodda (yönetim panelinde gösterilir)
    delivered = Column(Boolean, nullable=False, default=False)
    provider = Column(Boolean, nullable=False, default=False)  # gönderim servisi bağlı mıydı (False = deneme modu)
    attempts = Column(Integer, nullable=False, default=0)
    ip = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    used_at = Column(DateTime(timezone=True))


class AuthThrottle(Base):
    """GİRİŞ KİLİDİ (1.20.0): yanlış şifre sayacı. Anahtar: girişte yazılan bilginin özeti (`id:…`; kayıtlı olmayan bilgi için de, "bu
    numara kayıtlı mı" anlaşılmasın diye) ve hesabın kendisi (`u:<hesap>`; telefon / kullanıcı adı / e-posta değiştirilerek sınır
    aşılmasın). 5 yanlış → 15 dk, 10 → 1 saat, 15 → 6 saat, 20+ → 24 saat kilit; doğru girişte sıfırlanır. Yönetim "Kilidi aç" der."""

    __tablename__ = "auth_throttle"
    key = Column(String(80), primary_key=True)
    user_id = Column(String(32), index=True)
    fails = Column(Integer, nullable=False, default=0)
    first_at = Column(DateTime(timezone=True), default=now)
    last_at = Column(DateTime(timezone=True), default=now, index=True)
    locked_until = Column(DateTime(timezone=True), index=True)


class Report(Base):
    """ŞİKÂYET (1.28.0; kullanıcı isteği 9 Kasım 2026: "Sohbete Şikâyet et + Engelle … bunu bitir" — App Store 1.2 ve Google Play UGC
    şartı). Mesajlar sunucuda TUTULMADIĞI için şikâyet eden, şikâyet ettiği mesajı ve o kişinin bu sohbetteki son birkaç mesajını kendisi
    yollar (`context`, JSON). Yönetim 24 saat içinde inceler: işlem yok / uyar / hesabı kapat. Kapatılan şikâyet REPORT_KEEP_DAYS (180) gün
    sonra silinir."""

    __tablename__ = "reports"
    id = Column(String(32), primary_key=True, default=uid)
    reporter_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    target_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(12), nullable=False, default="user")  # message | user | content (1.29.0)
    reason = Column(String(16), nullable=False, default="other")  # spam | abuse | threat | fraud | sexual | other
    note = Column(String(300), nullable=False, default="")
    place = Column(String(12), nullable=False, default="dm")  # dm | group | map | other · 1.29.0: ad | job | card (içerik)
    ref = Column(String(40), nullable=False, default="", index=True)  # 1.29.0: tabela / ilan kimliği (kartta işletmenin kimliği)
    context = Column(Text, nullable=False, default="")
    blocked = Column(Boolean, nullable=False, default=False)  # şikâyet eden aynı anda engelledi mi
    status = Column(String(8), nullable=False, default="open", index=True)  # open | closed
    action = Column(String(8), nullable=False, default="")  # none | warn | ban | remove (1.29.0: içerik kaldırıldı)
    admin_note = Column(String(300), nullable=False, default="")
    closed_by = Column(String(32), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    closed_at = Column(DateTime(timezone=True))
