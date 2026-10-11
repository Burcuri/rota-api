(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  try { $('key').value = sessionStorage.getItem('rota.admin') || ''; } catch (e) { /* */ }

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function when(iso) { try { return new Date(iso).toLocaleString('tr-TR'); } catch (e) { return iso; } }

  function render(data) {
    var s = $('stats'); s.textContent = '';
    [['Kullanıcı', data.stats.users], ['Kapı noktası', data.stats.doorPoints], ['Açık teslim bağlantısı', data.stats.activeHandoffs], ['Hata satırı', data.stats.errorRows]].forEach(function (p) {
      var d = el('div', 'stat'); d.appendChild(el('b', '', String(p[1]))); d.appendChild(el('span', 'meta', p[0])); s.appendChild(d);
    });
    var list = $('list'); list.textContent = '';
    if (!data.errors.length) { list.appendChild(el('p', '', 'Bu aralıkta hata raporu yok. 🎉')); return; }
    data.errors.forEach(function (g) {
      var box = el('div', 'err' + (g.fatal ? ' fatal' : ''));
      box.appendChild(el('h3', '', (g.fatal ? '💥 ' : '') + (g.message || '(mesaj yok)')));
      box.appendChild(el('div', 'meta', g.count + ' kez · ' + g.installs + ' telefon · ' + g.kind + ' · ' + (g.screen || 'ekran ?') + ' · ' + g.platform + ' ' + (g.osVersion || '') + ' · sürüm ' + (g.appVersion || '?')));
      box.appendChild(el('div', 'meta', 'İlk: ' + when(g.firstSeen) + ' · Son: ' + when(g.lastSeen)));
      if (g.stack) {
        var b = el('button', '', 'Ayrıntı'); b.type = 'button'; b.style.marginTop = '8px';
        var pre = el('pre', '', g.stack); pre.hidden = true;
        b.addEventListener('click', function () { pre.hidden = !pre.hidden; });
        box.appendChild(b); box.appendChild(pre);
      }
      list.appendChild(box);
    });
  }

  function go() {
    var key = $('key').value.trim(), msg = $('msg');
    msg.className = ''; msg.textContent = 'Yükleniyor…';
    fetch('/v1/admin/summary?days=' + encodeURIComponent($('days').value), { headers: { 'X-Admin-Key': key }, cache: 'no-store' })
      .then(function (r) { return r.json().then(function (j) { return { r: r, j: j }; }); })
      .then(function (x) {
        if (!x.r.ok) {
          msg.className = 'bad';
          msg.textContent = x.r.status === 404 ? 'Yönetim kapalı: Render ortamına ADMIN_KEY (en az 12 karakter) yazılmamış.' : (x.j.detail || 'Hata');
          return;
        }
        try { sessionStorage.setItem('rota.admin', key); } catch (e) { /* */ }
        msg.textContent = ''; render(x.j);
      }).catch(function () { msg.className = 'bad'; msg.textContent = 'Sunucuya ulaşılamadı.'; });
  }
  $('go').addEventListener('click', go);
  $('key').addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
  if ($('key').value) go();
})();
