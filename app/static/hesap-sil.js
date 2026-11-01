(function () {
  'use strict';
  /* Hesap silme sayfası (1.20.0). Oturum anahtarı yalnız bu sayfanın belleğinde durur (depoya yazılmaz). Veri yalnız textContent ile yazılır. */
  var token = '';
  var pw = '';
  var $ = function (id) { return document.getElementById(id); };
  var msg = $('msg');
  /** Ad: her kelimenin ilk harfi büyük (Türkçe i → İ), uygulamadaki gibi */
  function title(n) {
    return String(n).trim().split(/\s+/).map(function (w) { return w ? w.charAt(0).toLocaleUpperCase('tr-TR') + w.slice(1).toLocaleLowerCase('tr-TR') : w; }).join(' ');
  }
  function say(t, cls) { msg.textContent = t || ''; msg.className = 'msg' + (cls ? ' ' + cls : ''); }
  function show(step) {
    ['step-login', 'step-confirm', 'step-done'].forEach(function (s) { $(s).hidden = s !== step; });
  }
  function call(path, body, auth) {
    var headers = { 'Content-Type': 'application/json' };
    if (auth) headers.Authorization = 'Bearer ' + auth;
    return fetch(path, { method: 'POST', headers: headers, body: JSON.stringify(body), cache: 'no-store' }).then(function (r) {
      return r.text().then(function (t) {
        var j = null;
        try { j = t ? JSON.parse(t) : null; } catch (e) { j = null; }
        if (!r.ok) {
          var e = new Error(j && typeof j.detail === 'string' ? j.detail : 'Bir sorun oldu (' + r.status + ').');
          e.status = r.status; e.code = j && j.code;
          throw e;
        }
        return j;
      });
    });
  }
  $('loginForm').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var login = $('login').value.trim();
    pw = $('password').value;
    if (!login || !pw) { say('Bilgini ve şifreni yaz.', 'bad'); return; }
    $('loginBtn').disabled = true;
    say('Bakılıyor…');
    call('/v1/auth/login', { login: login, password: pw }).then(function (r) {
      token = r.token;
      $('hello').textContent = 'Merhaba ' + title((r.user && r.user.name) || '') + ', silmeden önce oku:';
      say('');
      show('step-confirm');
      $('agree').focus();
    }).catch(function (e) {
      if (e.code === 'banned') say('Hesabın askıda. Silme isteğin için destekle iletişime geç. ' + e.message, 'bad');
      else if (e.status === 401) say('Bilgi ya da şifre yanlış.', 'bad');
      else say(e.message, 'bad');
    }).then(function () { $('loginBtn').disabled = false; });
  });
  $('agree').addEventListener('change', function () { $('deleteBtn').disabled = !$('agree').checked; });
  $('backBtn').addEventListener('click', function () { token = ''; pw = ''; $('password').value = ''; $('agree').checked = false; $('deleteBtn').disabled = true; say(''); show('step-login'); });
  $('deleteBtn').addEventListener('click', function () {
    if (!token || !$('agree').checked) return;
    $('deleteBtn').disabled = true;
    say('Siliniyor…');
    call('/v1/me/delete', { password: pw, confirm: true }, token).then(function () {
      token = ''; pw = '';
      say('');
      show('step-done');
    }).catch(function (e) {
      say(e.message, 'bad');
      $('deleteBtn').disabled = false;
    });
  });
})();
