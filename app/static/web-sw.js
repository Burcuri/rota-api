/* Web ikizi hizmet çalışanı (1.19.0). Yalnız tarayıcı bildirimini gösterir ("📩 Yeni mesajınız var" gibi; kişisel bilgi yok).
   Sayfa / veri SAKLAMAZ (çevrimdışı önbellek yok). İçerik sunucuda şifrelenir; burada çözülmüş hâli gelir: { title, body, url, tag, t, k? } (k: 1.26.0 alt tür, ör. ad_ending).
   Web ikizi açık ve önündeyse bildirim yerine sayfaya haber verilir (aynı şey iki kez görünmesin). */
'use strict';

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

function safeUrl(u) {
  return typeof u === 'string' && u.indexOf('/web/') === 0 ? u : '/web/';
}

function isWeb(c) {
  try { return new URL(c.url).pathname.indexOf('/web/') === 0; } catch (err) { return false; }
}

self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : '' }; }
  var url = safeUrl(d.url);
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (all) {
      // Yalnız web ikizi sekmeleri sayılır (aynı sitedeki alıcı sayfası /a/… ya da yönetim paneli bildirimi bastırmasın)
      var list = all.filter(isWeb);
      var front = list.filter(function (c) { return c.visibilityState === 'visible' && c.focused; });
      list.forEach(function (c) { try { c.postMessage({ type: 'rota-push', t: d.t || '', k: d.k || '', url: url }); } catch (err) { /* sekme kapanmış */ } });
      if (front.length) return null;
      return self.registration.showNotification(String(d.title || 'Rota'), {
        body: String(d.body || ''),
        tag: String(d.tag || 'rota'),
        renotify: true,
        icon: '/static/alici-192.png',
        badge: '/static/alici-badge.png',
        lang: 'tr',
        data: { url: url, t: d.t || '', k: d.k || '' }
      });
    })
  );
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var data = e.notification.data || {};
  var url = safeUrl(data.url);
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        var c = list[i];
        if (isWeb(c) && 'focus' in c) {
          try { c.postMessage({ type: 'rota-push-tap', t: data.t || '', k: data.k || '' }); } catch (err) { /* sekme kapanmış: sıradakine */ continue; }
          return c.focus();
        }
      }
      return self.clients.openWindow ? self.clients.openWindow(url) : null;
    })
  );
});
