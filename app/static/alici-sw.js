/* Alıcı sayfası hizmet çalışanı (1.18.0). Yalnız tarayıcı bildirimini gösterir ("Kargonuz yolda", "Kuryeniz kapıda").
   Sayfa / veri SAKLAMAZ (çevrimdışı önbellek yok): alıcı sayfası her açılışta sunucudan güncel durumu okur.
   Bildirim içeriği sunucuda şifrelenir; burada çözülmüş hâli gelir: { title, body, url, tag }. */
'use strict';

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : '' }; }
  var url = typeof d.url === 'string' && d.url.indexOf('/a/') === 0 ? d.url : '/a/';
  var title = String(d.title || 'Kargonuz');
  var opts = {
    body: String(d.body || ''),
    tag: String(d.tag || 'rota-alici'),
    renotify: true,
    icon: '/static/alici-192.png',
    badge: '/static/alici-badge.png',
    lang: 'tr',
    data: { url: url }
  };
  e.waitUntil(
    self.registration.showNotification(title, opts).then(function () {
      // Sayfa açıksa durumu hemen yenilesin (5 sn'lik yoklamayı beklemeden)
      return self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
        list.forEach(function (c) { try { c.postMessage({ type: 'rota-refresh', url: url }); } catch (err) { /* sekme kapanmış */ } });
      });
    })
  );
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/a/';
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        var c = list[i];
        try {
          if (new URL(c.url).pathname === url && 'focus' in c) return c.focus();
        } catch (err) { /* geçersiz adres: atla */ }
      }
      return self.clients.openWindow ? self.clients.openWindow(url) : null;
    })
  );
});
