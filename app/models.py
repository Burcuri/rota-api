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

    __table_args__ = (Index("ix_handoffs_courier_ref", "courier_id", "ref"),)


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

    __table_args__ = (
        # Bir kuryenin aynı anda tek aktif takibi (iki işletme aynı kuryeyi aynı anda izleyemez)
        Index("uq_follow_courier_active", "courier_id", unique=True, sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),
        # Aynı işveren-kurye çifti için tek açık (bekleyen / aktif) kayıt: tekrarlanan istek yeni satır açmaz
        Index("uq_follow_open_pair", "employer_id", "courier_id", unique=True, sqlite_where=text("status IN ('asked','active')"), postgresql_where=text("status IN ('asked','active')")),
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
