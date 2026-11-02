(function () {
  'use strict';
  var TOKEN = (location.pathname.split('/')[2] || '').trim();
  // Kuryenin "Konum Talebi Gönder" bağlantısı (#kapi): sayfa "Kapının yeri" sekmesiyle açılır
  var LOC = location.hash === '#kapi';
  var BASE = '/a/' + encodeURIComponent(TOKEN);
  var $ = function (id) { return document.getElementById(id); };
  var state = null, map = null, marker = null, tab = 'aldim', pollTimer = null, mapReady = false;

  function say(el, text, cls) { el.textContent = text || ''; el.className = 'msg' + (cls ? ' ' + cls : ''); }

  // 2 Kasım 2026 (kullanıcı kararı): yer adı ve yazılan not küçük harfle başlamaz; uygulamadaki services/nameCase.ts ile aynı kural
  // (Türkçe i → İ, ı → I; "TOKİ" gibi büyük yazılmış kısaltmalar ve "iPhone" gibi markalar bozulmaz).
  var UP = { i: 'İ', 'ı': 'I' }, LOW = { I: 'ı', 'İ': 'i' };
  function up(c) { return UP[c] || c.toUpperCase(); }
  function low(t) { var o = ''; for (var i = 0; i < t.length; i++) { var c = t.charAt(i); o += LOW[c] || c.toLowerCase(); } return o; }
  function upAll(t) { var o = ''; for (var i = 0; i < t.length; i++) o += up(t.charAt(i)); return o; }
  function brandish(c, nx) { return /[a-zçğıöşü]/.test(c) && /[A-ZÇĞİIÖŞÜ]/.test(nx || ''); }
  function capWords(t) { return t ? t.replace(/(^|[\s\-(])([^\s\-(])([^\s\-(]?)/g, function (m, a, c, nx) { return a + (brandish(c, nx) ? c : up(c)) + nx; }) : t; }
  function capFirst(t) { return t ? t.replace(/^(\s*)(\S)(\S?)/, function (m, a, c, nx) { return a + (brandish(c, nx) ? c : up(c)) + nx; }) : t; }
  function place(v) {
    var t = String(v == null ? '' : v).trim().replace(/\s+/g, ' ');
    if (!t) return t;
    var hasUp = low(t) !== t, hasLow = upAll(t) !== t, n = t.replace(/[^A-Za-zÇĞİIÖŞÜçğıöşü]/g, '').length;
    return hasUp && !hasLow && (/\s/.test(t) || n >= 5) ? capWords(low(t)) : capWords(t);
  }
  /** Yazarken ilk harf büyür (alıcının notu). */
  function capsOn(el) {
    if (!el) return;
    el.setAttribute('autocapitalize', 'sentences');
    el.addEventListener('input', function () {
      var v = el.value, w = capFirst(v);
      if (w === v) return;
      var at = el.selectionStart;
      el.value = w;
      try { el.setSelectionRange(at, at); } catch (e) { /* */ }
    });
  }

  /** Sunucu düz yazı verirse onu, değilse (doğrulama hatası vb.) anlaşılır genel bir cümle gösterir. */
  function errText(status, detail) {
    if (typeof detail === 'string' && detail) return detail;
    if (status === 422) return 'Bilgiler okunamadı; kontrol edip tekrar deneyin.';
    if (status === 429) return 'Çok sık deneme, biraz bekleyin.';
    if (status >= 500) return 'Sunucuda geçici bir sorun var, biraz sonra tekrar deneyin.';
    return 'Hata ' + status;
  }

  function api(method, path, body) {
    return fetch(BASE + path, {
      method: method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store'
    }).then(function (r) {
      return r.text().then(function (t) {
        var j = null; try { j = t ? JSON.parse(t) : null; } catch (e) { j = null; }
        if (!r.ok) { var err = new Error(errText(r.status, j && j.detail)); err.status = r.status; throw err; }
        return j;
      });
    });
  }

  function setBanner(text, cls) { var b = $('banner'); b.textContent = text; b.className = 'banner' + (cls ? ' ' + cls : ''); }

  function render() {
    var s = state; if (!s) return;
    $('app').hidden = false;
    $('addr').textContent = place(s.address || '') + (s.site ? ' · ' + place(s.site) : '');
    renderTrack();
    renderPush();
    var take = $('take'), box = $('takeBox');
    if (s.received) {
      setBanner('Teşekkürler, paketinizi aldığınız kaydedildi ✓', 'received');
      take.checked = true; take.disabled = true; box.className = 'take';
      $('takeHint').textContent = 'Kuryenin ekranında “Alıcı onayladı ✓” görünüyor.';
    } else if (s.canConfirm) {
      setBanner(s.byCourier
        ? 'Kurye paketi teslim ettiğini bildirdi. Paketi aldıysanız “Paketimi aldım” kutusunu işaretleyin.'
        : 'Kuryeniz kapıda. Paketi teslim almadan önce “Paketimi aldım” kutusunu işaretleyin.', 'arrived');
      take.checked = false; take.disabled = false; box.className = 'take';
      $('takeHint').textContent = 'Paketi eline aldığında kutuyu işaretle.';
    } else if (s.closed) {
      setBanner('Kurye teslimi tamamladı. İsterseniz kuryeyi “Değerlendir” sekmesinden puanlayabilirsiniz.', '');
      take.checked = false; take.disabled = true; box.className = 'take off';
      $('takeHint').textContent = 'Teslim tamamlandı.';
    } else if (LOC && !s.pin) {
      setBanner('Kuryeniz konumunuzu doğrulamanızı istiyor: kapınızın yerini haritada işaretleyip “Kapının yeri burası”na basın.', 'arrived');
      take.checked = false; take.disabled = true; box.className = 'take off';
      $('takeHint').textContent = 'Kurye hedefe ulaşınca bu kutu açılır.';
    } else if (s.stage === 'preparing') {
      setBanner('Paket yola çıkınca burada “Yolda” görünür. Kurye hedefe ulaşınca “Paketimi aldım” kutusu açılır.', '');
      take.checked = false; take.disabled = true; box.className = 'take off';
      $('takeHint').textContent = 'Kurye hedefe ulaşınca bu kutu açılır.';
    } else {
      setBanner('Kuryeniz yolda. Kurye hedefe ulaşınca “Paketimi aldım” kutusu açılır.', '');
      take.checked = false; take.disabled = true; box.className = 'take off';
      $('takeHint').textContent = 'Kurye hedefe ulaşınca bu kutu açılır.';
    }
    if (document.activeElement !== $('phone')) $('phone').value = s.phone || '';
    if (document.activeElement !== $('email')) $('email').value = s.email || '';
    var done = !!s.received || !!s.closed;
    $('phone').disabled = done; $('email').disabled = done; $('saveContact').disabled = done;
    if (done) say($('contactMsg'), 'Telefon ve e-posta bilginiz silindi.', 'ok');
    if (s.pin && !$('pinMsg').textContent) say($('pinMsg'), 'Kapının yerini daha önce işaretlediniz; isterseniz değiştirebilirsiniz.', 'ok');
    renderReview();
  }

  // ---- süreç çubuğu (1.18.0): Hazırlanıyor → Yolda → Teslim edildi
  var STEPS = ['preparing', 'onway', 'delivered'];
  function hhmm(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }
  function renderTrack() {
    var s = state; if (!s) return;
    var st = STEPS.indexOf(s.stage) >= 0 ? s.stage : 'onway';
    var idx = STEPS.indexOf(st), fin = st === 'delivered';
    $('track').hidden = false;
    $('track').setAttribute('data-stage', s.atDoor && !fin ? 'door' : st);
    for (var i = 0; i < STEPS.length; i++) {
      var el = $('st-' + STEPS[i]);
      el.className = 'step' + (i < idx || fin ? ' done' : i === idx ? ' now' : '');
      if (i === idx && !fin) el.setAttribute('aria-current', 'step'); else el.removeAttribute('aria-current');
    }
    $('t-onway').textContent = hhmm(s.departedAt);
    $('t-delivered').textContent = hhmm(s.deliveredAt);
    $('trackTitle').textContent = fin ? 'Teslim edildi ✓' : s.atDoor ? 'Kuryeniz kapıda' : st === 'onway' ? 'Kargonuz yolda' : 'Siparişiniz hazırlanıyor';
  }

  // ---- tarayıcı bildirimi (1.18.0): yalnız alıcı isterse ve izin verirse; abonelik yalnız bu teslim için
  var PUSH_LS = 'rota.push.' + TOKEN, pushBusy = false;
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* gizli sekme */ } }
  var IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  var STANDALONE = window.navigator.standalone === true || !!(window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
  var PUSH_OK = !!(window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window);

  function b64uBytes(s) {
    s = String(s || '').replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var raw = atob(s), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function sameKey(sub, key) {
    try {
      var a = new Uint8Array(sub.options.applicationServerKey), b = b64uBytes(key);
      if (a.length !== b.length) return false;
      for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
      return true;
    } catch (e) { return true; }
  }
  function askPermission() {
    return new Promise(function (resolve) {
      var p = Notification.requestPermission(function (r) { resolve(r); });  // eski Safari geri çağırma kullanır
      if (p && p.then) p.then(resolve);
    });
  }
  function show1(id, text) { var el = $(id); el.hidden = false; if (text !== undefined) el.textContent = text; }

  function renderPush() {
    var s = state, box = $('pushBox'), p = s && s.push;
    ['pushOn', 'pushState', 'pushOff', 'iosHint'].forEach(function (id) { $(id).hidden = true; });
    if (!s || !p || !p.open) { box.hidden = true; return; }
    if (!p.on && lsGet(PUSH_LS)) lsSet(PUSH_LS, null);  // sunucuda artık yok (silindi / süresi doldu)
    var onway = s.stage === 'onway';
    box.hidden = false;
    if (p.on && lsGet(PUSH_LS)) {
      show1('pushState', onway ? '🔔 Bildirim açık: kurye kapıya gelince haber vereceğiz.' : '🔔 Bildirim açık: kargonuz yola çıkınca haber vereceğiz.');
      show1('pushOff');
      return;
    }
    if (!PUSH_OK) {
      if (IOS && !STANDALONE) { show1('iosHint'); return; }
      if (IOS) { show1('pushState', 'Bu iPhone\'da bildirim için iOS 16.4 veya üstü gerekir.'); return; }
      box.hidden = true;
      return;
    }
    if (Notification.permission === 'denied') {
      show1('pushState', 'Bildirimler bu tarayıcıda kapalı. Açmak için tarayıcı ayarlarından bu siteye bildirim izni verin.');
      return;
    }
    show1('pushOn', onway ? '🔔 Kurye gelince bildirim al' : '🔔 Yola çıkınca bildirim al');
  }

  $('pushOn').addEventListener('click', function () {
    if (pushBusy || !state || !state.push || !PUSH_OK) return;
    var key = state.push.key, btn = $('pushOn');
    pushBusy = true; btn.disabled = true; say($('pushMsg'), 'Bildirim açılıyor…', '');
    var perm = Notification.permission === 'granted' ? Promise.resolve('granted') : askPermission();
    perm.then(function (r) {
      if (r !== 'granted') throw new Error(r === 'denied' ? 'Bildirim izni verilmedi.' : 'Bildirim izni verilmedi; isterseniz tekrar deneyin.');
      return navigator.serviceWorker.register('/a/sw.js', { scope: '/a/' }).then(function () { return navigator.serviceWorker.ready; });
    }).then(function (reg) {
      return reg.pushManager.getSubscription().then(function (sub) {
        if (sub && !sameKey(sub, key)) return sub.unsubscribe().then(function () { return null; });
        return sub;
      }).then(function (sub) {
        return sub || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uBytes(key) });
      });
    }).then(function (sub) {
      var j = sub.toJSON ? sub.toJSON() : sub;
      return api('POST', '/push', { endpoint: j.endpoint, keys: j.keys || {} }).then(function () { lsSet(PUSH_LS, j.endpoint); });
    }).then(function () {
      say($('pushMsg'), '', '');
      return load();
    }).catch(function (e) {
      say($('pushMsg'), (e && e.message) || 'Bildirim açılamadı.', 'bad');
    }).then(function () { pushBusy = false; btn.disabled = false; renderPush(); });
  });

  $('pushOff').addEventListener('click', function () {
    var ep = lsGet(PUSH_LS);
    if (!ep) { renderPush(); return; }
    api('POST', '/push/off', { endpoint: ep }).then(function () {
      lsSet(PUSH_LS, null);
      say($('pushMsg'), 'Bu teslimin bildirimi kapatıldı.', 'ok');
      return load();
    }).catch(function (e) { say($('pushMsg'), e.message || 'Kapatılamadı.', 'bad'); });
  });

  // Bildirim geldiğinde sayfa açıksa durum hemen yenilenir
  if ('serviceWorker' in navigator && navigator.serviceWorker.addEventListener) {
    navigator.serviceWorker.addEventListener('message', function (ev) { if (ev.data && ev.data.type === 'rota-refresh') load(); });
  }

  function invalid(msg) {
    $('app').hidden = true; $('track').hidden = true; $('addr').textContent = '';
    setBanner(msg || 'Bu bağlantı geçersiz ya da süresi dolmuş. Kuryenizden yenisini isteyin.', 'bad');
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function load() {
    return api('GET', '/state').then(function (s) { state = s; render(); }).catch(function (e) {
      if (e.status === 404) invalid(); else if (!state) setBanner('Bağlantı kurulamadı. İnternetinizi kontrol edip sayfayı yenileyin.', 'bad');
    });
  }

  function poll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(function () {
      if (document.hidden) return;
      if (state && (state.received || state.closed)) { clearInterval(pollTimer); pollTimer = null; return; }
      load();
    }, 5000);
  }

  // ---- sekmeler
  function show(name) {
    tab = name;
    ['aldim', 'kapi', 'degerlendir', 'iletisim'].forEach(function (n) {
      $('tab-' + n).hidden = n !== name;
    });
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (b) {
      var on = b.getAttribute('data-tab') === name;
      b.className = 'tab' + (on ? ' on' : ''); b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    if (name === 'kapi') initMap();
  }
  Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (b) {
    b.addEventListener('click', function () { show(b.getAttribute('data-tab')); });
  });

  // ---- Paketimi aldım
  $('take').addEventListener('change', function () {
    var el = $('take');
    if (!el.checked) { el.checked = !!(state && state.received); return; }
    el.disabled = true;
    api('POST', '/received', { checked: true }).then(function () { return load(); }).catch(function (e) {
      el.checked = false; el.disabled = false;
      setBanner(e.message || 'İşaretlenemedi, tekrar deneyin.', 'bad');
      load();
    });
  });

  // ---- harita
  function setMarker(ll) {
    if (!marker) {
      var icon = L.divIcon({ className: 'pin', html: '<i></i>', iconSize: [34, 34], iconAnchor: [17, 34] });
      marker = L.marker(ll, { draggable: true, icon: icon, keyboard: true, title: 'Kapının yeri' }).addTo(map);
    } else { marker.setLatLng(ll); }
  }
  function initMap() {
    if (!state) return;
    if (mapReady) { setTimeout(function () { map.invalidateSize(); }, 50); return; }
    mapReady = true;
    var start = state.pin || state.start;
    var ll = [start.lat, start.lon];
    map = L.map('map', { zoomControl: true, attributionControl: true }).setView(ll, 18);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(map);
    setMarker(ll);
    map.on('click', function (e) { setMarker(e.latlng); say($('pinMsg'), '', ''); });
    setTimeout(function () { map.invalidateSize(); }, 80);
    window.__map = map; window.__marker = function () { return marker; };
  }
  $('locate').addEventListener('click', function () {
    if (!navigator.geolocation) { say($('pinMsg'), 'Bu cihaz konum vermiyor; haritaya dokunarak işaretleyin.', 'bad'); return; }
    say($('pinMsg'), 'Konum alınıyor…', '');
    navigator.geolocation.getCurrentPosition(function (p) {
      var ll = [p.coords.latitude, p.coords.longitude];
      initMap(); setMarker(ll); map.setView(ll, 19);
      say($('pinMsg'), 'Konumunuz işaretlendi. Gerekirse işareti kapının önüne sürükleyin.', 'ok');
    }, function () { say($('pinMsg'), 'Konum izni verilmedi; haritaya dokunarak işaretleyin.', 'bad'); }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 });
  });
  $('savePin').addEventListener('click', function () {
    if (!marker) return;
    var ll = marker.getLatLng(), b = $('savePin');
    b.disabled = true; say($('pinMsg'), 'Kaydediliyor…', '');
    api('POST', '/pin', { lat: ll.lat, lon: ll.lng }).then(function () {
      say($('pinMsg'), 'Kaydedildi, teşekkürler. Kurye yeni konumu görecek.', 'ok');
    }).catch(function (e) { say($('pinMsg'), e.message || 'Kaydedilemedi.', 'bad'); }).then(function () { b.disabled = false; });
  });

  // ---- değerlendirme (anket)
  var rvFilled = false;
  var STAR_TXT = ['', 'Çok kötü', 'Kötü', 'Orta', 'İyi', 'Çok iyi'];
  var STAR_SETS = { stc: ['stC', 'stCtxt'], sta: ['stA', 'stAtxt'] };

  function radioVal(name) {
    var e = document.querySelector('input[name="' + name + '"]:checked');
    return e ? parseInt(e.value, 10) : 0;
  }
  function paintStars(name) {
    var v = radioVal(name), ids = STAR_SETS[name], labels = $(ids[0]).querySelectorAll('.star');
    for (var i = 0; i < labels.length; i++) {
      var on = i < v;
      labels[i].className = 'star' + (on ? ' on' : '');
      labels[i].querySelector('.g').textContent = on ? '★' : '☆';
    }
    $(ids[1]).textContent = v ? (v + ' / 5 · ' + STAR_TXT[v]) : (name === 'stc' ? 'Yıldıza dokunun (5 üzerinden)' : '');
    if (name === 'sta') $('stAclear').hidden = !v;
  }
  function setStars(name, v) {
    var els = document.getElementsByName(name);
    for (var i = 0; i < els.length; i++) els[i].checked = parseInt(els[i].value, 10) === v;
    paintStars(name);
  }
  function chipUp(el) { while (el && el !== document) { if (el.className && /(^| )chip( |$)/.test(el.className)) return el; el = el.parentNode; } return null; }

  function fillForm(d) {
    setStars('stc', d ? d.stars : 0);
    setStars('sta', d && d.appStars ? d.appStars : 0);
    var tags = (d && d.tags) || [], chips = $('tags').querySelectorAll('.chip'), i;
    for (i = 0; i < chips.length; i++) chips[i].setAttribute('aria-pressed', tags.indexOf(chips[i].getAttribute('data-k')) >= 0 ? 'true' : 'false');
    var ac = $('addrOk').querySelectorAll('.chip'), has = d && d.addressOk !== null && d.addressOk !== undefined;
    for (i = 0; i < ac.length; i++) ac[i].setAttribute('aria-pressed', has && ac[i].getAttribute('data-v') === (d.addressOk ? '1' : '0') ? 'true' : 'false');
    $('comment').value = d ? (d.comment || '') : '';
    $('cnt').textContent = String($('comment').value.length);
  }

  /** Sekmenin görünümü: kurye ulaşmadan kilitli; ulaşınca form; gönderilmişse "alındı" notu ve "Güncelle". */
  function renderReview() {
    var r = (state && state.review) || { allowed: false, done: false, data: null };
    $('rvLocked').hidden = !!r.allowed;
    $('rvForm').hidden = !r.allowed;
    $('rvDone').hidden = !r.done;
    $('rvSend').textContent = r.done ? 'Güncelle' : 'Gönder';
    if (r.allowed && !rvFilled) { fillForm(r.data); rvFilled = true; }  // yoklama, yazılmakta olanı silmesin
    $('goReview').hidden = !(r.allowed && !r.done);
  }

  $('stC').addEventListener('change', function () { paintStars('stc'); });
  $('stA').addEventListener('change', function () { paintStars('sta'); });
  $('stAclear').addEventListener('click', function () { setStars('sta', 0); });
  $('goReview').addEventListener('click', function () { show('degerlendir'); window.scrollTo(0, 0); });
  $('tags').addEventListener('click', function (e) {
    var b = chipUp(e.target); if (!b) return;
    b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
  });
  $('addrOk').addEventListener('click', function (e) {
    var b = chipUp(e.target); if (!b) return;
    var was = b.getAttribute('aria-pressed') === 'true', all = $('addrOk').querySelectorAll('.chip');
    for (var i = 0; i < all.length; i++) all[i].setAttribute('aria-pressed', 'false');
    b.setAttribute('aria-pressed', was ? 'false' : 'true');
  });
  capsOn($('comment'));
  $('comment').addEventListener('input', function () { $('cnt').textContent = String($('comment').value.length); });
  $('rvForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var stars = radioVal('stc'), b = $('rvSend');
    if (!stars) { say($('rvMsg'), 'Kurye için 1 ile 5 arasında yıldız seçin.', 'bad'); $('stC').querySelector('input').focus(); return; }
    var body = { stars: stars, tags: [], comment: $('comment').value.replace(/^\s+|\s+$/g, '') };
    var app = radioVal('sta'); if (app) body.appStars = app;
    var chips = $('tags').querySelectorAll('.chip'), i;
    for (i = 0; i < chips.length; i++) if (chips[i].getAttribute('aria-pressed') === 'true') body.tags.push(chips[i].getAttribute('data-k'));
    var ac = $('addrOk').querySelectorAll('.chip');
    for (i = 0; i < ac.length; i++) if (ac[i].getAttribute('aria-pressed') === 'true') body.addressOk = ac[i].getAttribute('data-v') === '1';
    b.disabled = true; say($('rvMsg'), 'Gönderiliyor…', '');
    api('POST', '/review', body).then(function (r) {
      if (state) state.review = r.review;
      renderReview();
      say($('rvMsg'), 'Teşekkürler, değerlendirmeniz alındı ✓', 'ok');
    }).catch(function (er) {
      if (er.status === 404) { invalid(); return; }
      say($('rvMsg'), er.message || 'Gönderilemedi, tekrar deneyin.', 'bad');
    }).then(function () { b.disabled = false; });
  });

  // ---- iletişim
  $('saveContact').addEventListener('click', function () {
    var b = $('saveContact');
    b.disabled = true; say($('contactMsg'), 'Kaydediliyor…', '');
    api('POST', '/contact', { phone: $('phone').value, email: $('email').value }).then(function (r) {
      $('phone').value = r.phone || ''; $('email').value = r.email || '';
      say($('contactMsg'), 'Kaydedildi.', 'ok');
    }).catch(function (e) { say($('contactMsg'), e.message || 'Kaydedilemedi.', 'bad'); }).then(function () { b.disabled = false; });
  });

  document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });
  load().then(function () {
    if (LOC && state && !state.received && !state.closed) show('kapi');
    poll();
  });
})();
