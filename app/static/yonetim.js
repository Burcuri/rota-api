(function () {
  'use strict';
  /* Rota yönetim sayfası. Veri hiçbir zaman innerHTML ile yazılmaz (yalnız metin düğümü); betik yalnız kendi sunucumuzdan yüklenir (CSP). */
  const KEY = 'rota.adm';
  const ROLE = { courier: 'Kurye', employer: 'İşyeri', sponsor: 'Sponsor' };
  const SRC = { recipient: 'Alıcı', employer: 'İşyeri', courier: 'Kurye' };
  const FIELD = { name: 'ad', username: 'kullanıcı adı', email: 'e-posta' };
  const ACT = {
    login: 'Giriş yaptı', login_fail: 'Başarısız giriş denemesi', logout: 'Çıkış yaptı', password: 'Kendi şifresini değiştirdi',
    '2fa_on': 'İki adımlı doğrulamayı açtı', '2fa_off': 'İki adımlı doğrulamayı kapattı',
    admin_create: 'Yönetici hesabı oluşturdu', admin_reset: 'Yönetici şifresini anahtarla sıfırladı', admin_disable: 'Yöneticiyi kapattı', admin_enable: 'Yöneticiyi yeniden açtı',
    user_edit: 'Üye bilgilerini düzenledi', user_ban: 'Üyeyi uygulamadan men etti', user_unban: 'Üyenin menini kaldırdı', user_password: 'Üyeye yeni şifre verdi', user_logout: 'Üyenin oturumlarını kapattı',
    review_hide: 'Değerlendirmeyi gizledi', review_unhide: 'Değerlendirmeyi yeniden gösterdi', review_delete: 'Değerlendirmeyi kalıcı sildi',
    follow_view: 'Takip kaydının konum izini açtı',
  };
  const TARGET = { user: 'üye', admin: 'yönetici', review: 'değerlendirme', follow: 'takip' };
  const FSTATUS = { asked: 'Yanıt bekliyor', active: 'Sürüyor', ended: 'Bitti' };
  const FREASON = {
    declined: 'kurye reddetti', cancelled: 'işveren vazgeçti', stopped_employer: 'işveren bitirdi', stopped_courier: 'kurye durdurdu',
    done: 'kurye bütün teslimatları bitirdi', expired: 'süre doldu', role: 'kurye, kurye modundan çıktı', ban: 'hesap askıya alındı',
  };
  const root = document.getElementById('root');
  let token = '';
  try { token = sessionStorage.getItem(KEY) || ''; } catch (e) { /* özel pencere */ }
  const ctx = { admin: null, status: null, limited: false, tab: 'ozet', kinds: null, content: null };
  const uv = { q: '', role: '', banned: '', sort: 'new', items: [], total: 0 };
  const ev = { kind: '', userId: '' };
  const rv = { source: '', stars: '', hidden: '', q: '', subject: '' };
  const fl = { status: '', userId: '' };

  // ------------------------------------------------------------------ yardımcılar
  function add(n, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) { c.forEach((x) => add(n, x)); return; }
    n.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  }
  function h(tag, props, ...kids) {
    const n = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach((k) => {
        const v = props[k];
        if (v == null || v === false) return;
        if (k === 'class') n.className = v;
        else if (k === 'text') n.textContent = v;
        else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), v);
        else if (k === 'value') n.value = v;
        else if (k === 'checked' || k === 'disabled' || k === 'hidden' || k === 'selected') n[k] = true;
        else n.setAttribute(k, v === true ? '' : v);
      });
    }
    kids.forEach((c) => add(n, c));
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function qs(o) {
    return Object.keys(o).filter((k) => o[k] !== '' && o[k] != null).map((k) => encodeURIComponent(k) + '=' + encodeURIComponent(o[k])).join('&');
  }
  function when(iso) { if (!iso) return '—'; try { return new Date(iso).toLocaleString('tr-TR'); } catch (e) { return iso; } }
  function stars(n) { n = Math.max(0, Math.min(5, Math.round(n || 0))); return '★'.repeat(n) + '☆'.repeat(5 - n); }
  function say(node, text, cls) { node.textContent = text || ''; node.className = 'msg' + (cls ? ' ' + cls : ''); }

  function api(method, path, body, opts) {
    opts = opts || {};
    const headers = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (token && !opts.anon) headers.Authorization = 'Bearer ' + token;
    if (opts.headers) Object.assign(headers, opts.headers);
    return fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, cache: 'no-store' }).then((r) =>
      r.text().then((t) => {
        let j = null;
        try { j = t ? JSON.parse(t) : null; } catch (e) { j = null; }
        if (!r.ok) {
          const d = j && j.detail;
          const err = new Error(typeof d === 'string' ? d : 'Hata ' + r.status);
          err.status = r.status;
          if (r.status === 401 && token && !opts.anon) { dropSession(); showLogin('Oturum sona erdi, tekrar giriş yapın.'); }
          throw err;
        }
        return j;
      }),
    );
  }
  function setSession(t) { token = t; try { sessionStorage.setItem(KEY, t); } catch (e) { /* */ } }
  function dropSession() { token = ''; ctx.admin = null; try { sessionStorage.removeItem(KEY); } catch (e) { /* */ } document.getElementById('who').hidden = true; }

  /** İki basamaklı onay: ilk tıklamada "Emin misiniz?" sorar. */
  function confirmBtn(label, cls, ask, action) {
    const wrap = h('span', { class: 'row' });
    const reset = () => { clear(wrap); wrap.appendChild(h('button', { type: 'button', class: 'btn small ' + cls, text: label, onclick: askNow })); };
    function askNow() {
      clear(wrap);
      wrap.appendChild(h('span', { class: 'small', text: ask }));
      wrap.appendChild(h('button', { type: 'button', class: 'btn small danger', text: 'Evet', onclick: () => { reset(); action(); } }));
      wrap.appendChild(h('button', { type: 'button', class: 'btn small ghost', text: 'Vazgeç', onclick: reset }));
    }
    reset();
    return wrap;
  }

  // ------------------------------------------------------------------ giriş
  function showLogin(note) {
    clear(root);
    document.getElementById('who').hidden = true;
    const msg = h('div', { class: 'msg bad', role: 'alert', text: note || '' });
    const user = h('input', { id: 'u', type: 'text', autocomplete: 'username', autocapitalize: 'none', spellcheck: 'false', required: true });
    const pass = h('input', { id: 'p', type: 'password', autocomplete: 'current-password', required: true });
    const code = h('input', { id: 'c', type: 'text', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: '8', placeholder: '6 haneli kod' });
    const codeBox = h('label', { class: 'f', hidden: true }, 'İki adımlı doğrulama kodu', code);
    const go = h('button', { type: 'submit', class: 'btn', text: 'Giriş' });
    const form = h('form', { class: 'login', onsubmit: (e) => {
      e.preventDefault();
      go.disabled = true; say(msg, 'Giriş yapılıyor…', '');
      api('POST', '/v1/admin/login', { username: user.value.trim(), password: pass.value, code: code.value.trim() }, { anon: true }).then((r) => {
        if (r.need2fa) { codeBox.hidden = false; code.focus(); say(msg, 'Kimlik doğrulayıcı uygulamadaki 6 haneli kodu yazın.', ''); return; }
        setSession(r.token); ctx.admin = r.admin; ctx.limited = !!r.limited; ctx.tab = r.limited ? 'hesap' : 'ozet';
        showApp();
      }).catch((er) => say(msg, er.message, 'bad')).then(() => { go.disabled = false; });
    } },
      h('h2', { text: 'Yönetici girişi' }),
      h('label', { class: 'f' }, 'Yönetici adı', user),
      h('label', { class: 'f' }, 'Şifre', pass),
      codeBox, go, msg);
    root.appendChild(form);
    api('GET', '/v1/admin/status', undefined, { anon: true }).then((s) => {
      ctx.status = s;
      if (!s.setupOpen) {
        if (!s.hasAdmin) form.appendChild(h('div', { class: 'box warn', text: 'Henüz yönetici yok. Sunucu ortamına ADMIN_KEY (en az 12 karakter) yazılırsa ilk yönetici burada oluşturulabilir.' }));
        return;
      }
      form.appendChild(setupBox(s));
    }).catch(() => {});
  }

  function setupBox(s) {
    const msg = h('div', { class: 'msg', role: 'status' });
    const key = h('input', { type: 'password', autocomplete: 'off', required: true });
    const name = h('input', { type: 'text', autocomplete: 'off', autocapitalize: 'none', required: true });
    const pw = h('input', { type: 'password', autocomplete: 'new-password', required: true, minlength: '10' });
    const reset = h('input', { type: 'checkbox' });
    const f = h('form', { onsubmit: (e) => {
      e.preventDefault();
      say(msg, 'Gönderiliyor…', '');
      api('POST', '/v1/admin/setup', { username: name.value.trim(), password: pw.value, reset: reset.checked }, { anon: true, headers: { 'X-Admin-Key': key.value } })
        .then((r) => { say(msg, r.created ? 'Yönetici oluşturuldu. Yukarıdan giriş yapabilirsiniz.' : 'Şifre sıfırlandı. Yukarıdan giriş yapabilirsiniz.', 'ok'); key.value = ''; pw.value = ''; })
        .catch((er) => say(msg, er.message, 'bad'));
    } },
      h('p', { class: 'small muted', text: 'Render ortamındaki ADMIN_KEY ile ilk yöneticiyi oluşturur ya da unutulan şifreyi sıfırlar. Günlük girişte kullanılmaz.' }),
      h('label', { class: 'f' }, 'Yönetici anahtarı (ADMIN_KEY)', key),
      h('label', { class: 'f' }, 'Yönetici adı', name),
      h('label', { class: 'f' }, 'Şifre (en az 10 karakter)', pw),
      h('label', { class: 'f' }, reset, ' Bu ad varsa şifresini sıfırla (iki adımlı doğrulaması da kapanır)'),
      h('button', { type: 'submit', class: 'btn ghost', text: s.hasAdmin ? 'Oluştur / sıfırla' : 'İlk yöneticiyi oluştur' }), msg);
    return h('details', { class: 'box', open: !s.hasAdmin }, h('summary', { text: s.hasAdmin ? 'Kurulum / şifremi unuttum' : 'İlk kurulum' }), f);
  }

  // ------------------------------------------------------------------ uygulama kabuğu
  const TABS = [['ozet', 'Özet'], ['uyeler', 'Üyeler'], ['etkinlik', 'Etkinlik'], ['takipler', 'Takipler'], ['yorumlar', 'Yorumlar'], ['hatalar', 'Hatalar'], ['hesap', 'Hesabım']];

  function showApp() {
    document.getElementById('who').hidden = false;
    document.getElementById('whoName').textContent = ctx.admin ? ctx.admin.username : '';
    clear(root);
    const tabs = h('div', { class: 'tabs', role: 'tablist' });
    ctx.content = h('div', { id: 'content' });
    TABS.forEach(([id, label]) => {
      if (ctx.limited && id !== 'hesap') return;
      tabs.appendChild(h('button', { type: 'button', role: 'tab', class: 'tab', 'data-tab': id, 'aria-selected': id === ctx.tab ? 'true' : 'false', text: label, onclick: () => go(id) }));
    });
    root.appendChild(tabs);
    if (ctx.limited) root.appendChild(h('div', { class: 'box warn', role: 'alert', text: 'Bu sunucuda iki adımlı doğrulama zorunlu. Devam etmek için aşağıdan kurun.' }));
    root.appendChild(ctx.content);
    go(ctx.tab);
  }

  function go(tab) {
    ctx.tab = tab;
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), (b) => b.setAttribute('aria-selected', b.getAttribute('data-tab') === tab ? 'true' : 'false'));
    clear(ctx.content);
    ({ ozet: tabOzet, uyeler: tabUyeler, etkinlik: tabEtkinlik, takipler: tabTakipler, yorumlar: tabYorumlar, hatalar: tabHatalar, hesap: tabHesap })[tab]();
  }

  function loading(node) { clear(node); node.appendChild(h('p', { class: 'muted', text: 'Yükleniyor…' })); }
  function failed(node, er) { clear(node); node.appendChild(h('p', { class: 'msg bad', role: 'alert', text: er.message || 'Yüklenemedi' })); }

  // ------------------------------------------------------------------ Özet
  function tabOzet() {
    const c = ctx.content; loading(c);
    api('GET', '/v1/admin/stats').then((s) => {
      clear(c);
      const stat = (n, label, onclick) => h('div', { class: 'stat' }, h('b', { text: String(n) }), onclick ? h('button', { type: 'button', class: 'lnk', text: label + ' ›', onclick }) : h('span', { class: 'muted', text: label }));
      c.appendChild(h('h2', { text: 'Özet' }));
      c.appendChild(h('div', { class: 'cards' },
        stat(s.users, 'Üye', () => { Object.assign(uv, { q: '', role: '', banned: '' }); go('uyeler'); }),
        stat(s.byRole.courier, 'Kurye', () => { Object.assign(uv, { q: '', role: 'courier', banned: '' }); go('uyeler'); }),
        stat(s.byRole.employer, 'İşyeri', () => { Object.assign(uv, { q: '', role: 'employer', banned: '' }); go('uyeler'); }),
        stat(s.byRole.sponsor, 'Sponsor', () => { Object.assign(uv, { q: '', role: 'sponsor', banned: '' }); go('uyeler'); }),
        stat(s.banned, 'Askıdaki üye', () => { Object.assign(uv, { q: '', role: '', banned: '1' }); go('uyeler'); }),
        stat(s.newUsers7d, 'Son 7 gün yeni üye'),
        stat(s.logins24h, 'Son 24 saatte giriş yapan'),
        stat(s.openJobs, 'Açık ilan'),
        stat(s.activeHandoffs, 'Açık teslim bağlantısı'),
        stat(s.doorPoints, 'Ortak harita kapı noktası'),
        stat(s.reviews, 'Değerlendirme', () => go('yorumlar')),
        stat(s.reviewsHidden, 'Gizlenmiş değerlendirme', () => { Object.assign(rv, { hidden: '1' }); go('yorumlar'); }),
        stat(s.admins, 'Yönetici')));
      c.appendChild(h('p', { class: 'small muted', text: 'Sunucudaki kayıtların hepsine buradan bakılır. Mesaj içerikleri, adres listeleri ve alıcı bilgileri telefonlarda durduğu, sunucuda tutulmadığı için burada yoktur.' }));
    }).catch((er) => failed(c, er));
  }

  // ------------------------------------------------------------------ Üyeler
  function tabUyeler() {
    const c = ctx.content;
    const q = h('input', { type: 'search', placeholder: 'Ad, telefon, kullanıcı adı, e-posta ya da kimlik', value: uv.q, 'aria-label': 'Üye ara', class: 'grow' });
    const role = h('select', { 'aria-label': 'Hesap türü' }, h('option', { value: '', text: 'Tüm türler' }), ...Object.keys(ROLE).map((k) => h('option', { value: k, text: ROLE[k], selected: uv.role === k })));
    const banned = h('select', { 'aria-label': 'Durum' }, h('option', { value: '', text: 'Tüm durumlar' }), h('option', { value: '1', text: 'Askıdakiler', selected: uv.banned === '1' }), h('option', { value: '0', text: 'Aktifler', selected: uv.banned === '0' }));
    const sort = h('select', { 'aria-label': 'Sıra' }, ...[['new', 'En yeni'], ['old', 'En eski'], ['login', 'Son giriş'], ['name', 'Ada göre']].map(([v, t]) => h('option', { value: v, text: t, selected: uv.sort === v })));
    const list = h('div', { id: 'ulist' });
    const info = h('div', { class: 'muted small', id: 'uinfo' });
    const more = h('button', { type: 'button', class: 'btn ghost', text: 'Daha fazla göster', hidden: true });
    let seq = 0; // üst üste yapılan aramalarda yalnız sonuncunun yanıtı yazılır
    function load(reset) {
      if (reset) { uv.items = []; clear(list); }
      uv.q = q.value.trim(); uv.role = role.value; uv.banned = banned.value; uv.sort = sort.value;
      const my = ++seq;
      api('GET', '/v1/admin/users?' + qs({ q: uv.q, role: uv.role, banned: uv.banned, sort: uv.sort, offset: uv.items.length, limit: 50 })).then((r) => {
        if (my !== seq) return;
        uv.total = r.total; uv.items = uv.items.concat(r.items); paint();
      }).catch((er) => { if (my === seq) say(info, er.message, 'bad'); });
    }
    function paint() {
      clear(list);
      uv.items.forEach((u) => list.appendChild(userRow(u)));
      info.textContent = uv.total + ' üye' + (uv.items.length < uv.total ? ' (' + uv.items.length + ' gösteriliyor)' : '');
      more.hidden = uv.items.length >= uv.total;
    }
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter') load(true); });
    [role, banned, sort].forEach((s) => s.addEventListener('change', () => load(true)));
    more.addEventListener('click', () => load(false));
    c.appendChild(h('h2', { text: 'Üyeler' }));
    c.appendChild(h('div', { class: 'row' }, q, role, banned, sort, h('button', { type: 'button', class: 'btn', text: 'Ara', onclick: () => load(true) })));
    c.appendChild(info); c.appendChild(list); c.appendChild(h('div', { class: 'row' }, more));
    load(true); // her girişte sunucudan taze liste (men / düzenleme sonrası eski görüntü kalmasın)
  }

  function userRow(u) {
    return h('button', { type: 'button', class: 'item', onclick: () => openUser(u.id) },
      h('span', null, h('span', { class: 'nm', text: u.name || '(adsız)' }), ' ', h('span', { class: 'pill', text: ROLE[u.role] || u.role }), ' ',
        u.banned ? h('span', { class: 'pill bad', text: 'Askıda' }) : null, u.mustChangePassword ? h('span', { class: 'pill warn', text: 'Şifre bekliyor' }) : null),
      h('span', { class: 'muted small', text: [u.phone, u.username ? '@' + u.username : '', 'son giriş: ' + when(u.lastLoginAt)].filter(Boolean).join(' · ') }));
  }

  function openUser(id) {
    const c = ctx.content; loading(c);
    api('GET', '/v1/admin/users/' + encodeURIComponent(id)).then((d) => paintUser(d)).catch((er) => failed(c, er));
  }

  function kv(pairs) {
    const dl = h('dl', { class: 'kv' });
    pairs.forEach(([k, v]) => { dl.appendChild(h('dt', { text: k })); dl.appendChild(h('dd', null, v == null || v === '' ? '—' : v)); });
    return dl;
  }

  function paintUser(d) {
    const c = clear(ctx.content);
    c.appendChild(h('button', { type: 'button', class: 'btn small ghost', text: '‹ Listeye dön', onclick: () => go('uyeler') }));
    c.appendChild(h('h2', { text: d.name || '(adsız)' }));
    c.appendChild(h('div', { class: 'row' }, h('span', { class: 'pill', text: ROLE[d.role] || d.role }),
      d.banned ? h('span', { class: 'pill bad', text: 'Askıda' }) : h('span', { class: 'pill ok', text: 'Aktif' }),
      d.mustChangePassword ? h('span', { class: 'pill warn', text: 'Yeni şifre belirlemesi bekleniyor' }) : null,
      h('span', { class: 'muted small', text: 'Kimlik: ' + d.id })));

    // --- bilgileri düzenle
    const f = { name: h('input', { value: d.name || '', maxlength: '120' }), phone: h('input', { value: d.phone || '', inputmode: 'tel' }), username: h('input', { value: d.username || '', autocapitalize: 'none' }), email: h('input', { value: d.email || '', type: 'email' }),
      role: h('select', null, ...Object.keys(ROLE).map((k) => h('option', { value: k, text: ROLE[k], selected: d.role === k }))) };
    const emsg = h('div', { class: 'msg', role: 'status' });
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Hesap bilgileri' }),
      h('label', { class: 'f' }, 'Ad', f.name), h('label', { class: 'f' }, 'Telefon', f.phone), h('label', { class: 'f' }, 'Kullanıcı adı (boş bırakılırsa kalkar)', f.username),
      h('label', { class: 'f' }, 'E-posta', f.email), h('label', { class: 'f' }, 'Hesap türü', f.role),
      h('button', { type: 'button', class: 'btn', text: 'Kaydet', onclick: () => {
        say(emsg, 'Kaydediliyor…', '');
        api('PATCH', '/v1/admin/users/' + d.id, { name: f.name.value, phone: f.phone.value, username: f.username.value, email: f.email.value, role: f.role.value })
          .then(() => { say(emsg, 'Kaydedildi.', 'ok'); openUser(d.id); }).catch((er) => say(emsg, er.message, 'bad'));
      } }), emsg));

    // --- men
    const bmsg = h('div', { class: 'msg', role: 'status' });
    if (d.banned) {
      c.appendChild(h('div', { class: 'box bad' }, h('h3', { text: 'Hesap askıda' }),
        kv([['Neden', d.bannedReason], ['Ne zaman', when(d.bannedAt)]]),
        h('button', { type: 'button', class: 'btn', text: 'Askıyı kaldır', onclick: () => api('POST', '/v1/admin/users/' + d.id + '/unban').then(() => openUser(d.id)).catch((er) => say(bmsg, er.message, 'bad')) }), bmsg));
    } else {
      const reason = h('textarea', { maxlength: '300', placeholder: 'Örn. sahte ilan verdi' });
      c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Uygulamadan men et' }),
        h('p', { class: 'small muted', text: 'Üye giremez, açık oturumları kapanır, açık ilanları kaldırılır. Aşağıdaki neden üyeye gösterilir.' }),
        h('label', { class: 'f' }, 'Men nedeni (üyeye gösterilir)', reason),
        confirmBtn('Uygulamadan men et', 'danger', 'Bu üye uygulamadan men edilsin mi?', () => {
          api('POST', '/v1/admin/users/' + d.id + '/ban', { reason: reason.value }).then(() => openUser(d.id)).catch((er) => say(bmsg, er.message, 'bad'));
        }), bmsg));
    }

    // --- şifre sıfırlama ve oturumlar
    const pmsg = h('div', { class: 'msg', role: 'status' });
    const out = h('div');
    const own = h('input', { type: 'text', autocomplete: 'off', placeholder: 'En az 6 karakter', 'aria-label': 'Kendi belirleyeceğiniz şifre' });
    function showTemp(t) {
      clear(out);
      const copyBtn = h('button', { type: 'button', class: 'btn small ghost', text: 'Kopyala', onclick: () => { if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => { copyBtn.textContent = 'Kopyalandı ✓'; }); } });
      out.appendChild(h('div', { class: 'box warn', role: 'alert' }, h('div', { text: 'Geçici şifre (yalnız şimdi görünür, sunucuda saklanmaz):' }), h('div', { class: 'big', id: 'temp', text: t }), copyBtn,
        h('p', { class: 'small', text: 'Üyeye güvenli bir yoldan iletin. Bu şifreyle girince yeni şifre belirlemesi istenir.' })));
    }
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Şifre ve oturum' }),
      h('div', { class: 'row' },
        confirmBtn('Geçici şifre üret', 'ghost', 'Mevcut şifre geçersiz olacak. Devam edilsin mi?', () => {
          api('POST', '/v1/admin/users/' + d.id + '/password', {}).then((r) => { showTemp(r.temporaryPassword); say(pmsg, '', ''); }).catch((er) => say(pmsg, er.message, 'bad'));
        }),
        confirmBtn('Tüm oturumlarını kapat', 'ghost', 'Üye her cihazdan çıkış yapsın mı?', () => {
          api('POST', '/v1/admin/users/' + d.id + '/logout').then(() => say(pmsg, 'Oturumlar kapatıldı.', 'ok')).catch((er) => say(pmsg, er.message, 'bad'));
        })),
      h('details', null, h('summary', { text: 'Şifreyi kendim belirleyeceğim' }), h('div', { class: 'row' }, own,
        h('button', { type: 'button', class: 'btn small', text: 'Bu şifreyi ver', onclick: () => {
          api('POST', '/v1/admin/users/' + d.id + '/password', { password: own.value }).then(() => { own.value = ''; say(pmsg, 'Şifre belirlendi; üye girişte yenisini seçecek.', 'ok'); }).catch((er) => say(pmsg, er.message, 'bad'));
        } }))),
      out, pmsg));

    // --- puan
    const cr = d.rating.courier, er = d.rating.employer;
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Puanlar (5 üzerinden)' }),
      kv([['Kurye olarak genel başarı', cr.score == null ? 'puan yok' : cr.score + ' / 5  (' + cr.count + ' değerlendirme)'], ['İşyeri puanı ortalaması', cr.employerAvg == null ? '—' : String(cr.employerAvg)], ['Alıcı puanı ortalaması', cr.recipientAvg == null ? '—' : String(cr.recipientAvg)],
        ['İşyeri olarak itibar', er.count ? er.avg + ' / 5 · %' + er.onTimePct + ' vaktinde · %' + er.completePct + ' eksiksiz ödeme (' + er.count + ')' : 'puan yok']])));

    // --- rıza, ilanlar, cihazlar
    const cs = d.consent;
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'İzinler ve kayıt' }),
      cs ? kv([['Metin sürümü', cs.version], ['Sesle yazma', cs.voice ? 'açık' : 'kapalı'], ['Adres okuma (OCR)', cs.ocr ? 'açık' : 'kapalı'], ['Grup konumu', cs.grupKonum ? 'açık' : 'kapalı'], ['Ortak kapı haritası', cs.kapi ? 'açık' : 'kapalı'], ['Kullanım koşulları', cs.sozlesmeV ? 'sürüm ' + cs.sozlesmeV : 'onaylanmamış'], ['Son kayıt', when(cs.recordedAt)]]) : h('p', { class: 'muted', text: 'Henüz rıza kaydı yok.' }),
      kv([['Kayıt tarihi', when(d.createdAt)], ['Son giriş', when(d.lastLoginAt)], ['Teslim bağlantısı sayısı', d.handoffs], ['Cihazlar', d.devices.length ? d.devices.map((x) => x.platform + ' (' + when(x.lastSeen) + ')').join('; ') : '—']])));
    if (d.jobs.length) {
      c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'İlanları' }), ...d.jobs.map((j) => h('div', { class: 'ev' }, h('span', { class: 't', text: when(j.at) }), h('span', null, j.title + ' · ' + j.count + ' adres · ', h('span', { class: 'pill', text: j.status }))))));
    }

    // --- etkinlik ve yorumlar
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Son etkinlikler' }), d.events.length ? d.events.map(evRow) : h('p', { class: 'muted', text: 'Kayıt yok.' }),
      h('button', { type: 'button', class: 'btn small ghost', text: 'Tüm etkinlikleri aç', onclick: () => { ev.userId = d.id; ev.kind = ''; go('etkinlik'); } })));
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Hakkında yazılan değerlendirmeler' }), d.reviewsReceived.length ? d.reviewsReceived.map((r) => reviewCard(r, () => openUser(d.id))) : h('p', { class: 'muted', text: 'Yok.' })));
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Yazdığı değerlendirmeler' }), d.reviewsWritten.length ? d.reviewsWritten.map((r) => reviewCard(r, () => openUser(d.id))) : h('p', { class: 'muted', text: 'Yok.' })));
  }

  // ------------------------------------------------------------------ Etkinlik
  function metaText(e) {
    const m = e.meta || {};
    switch (e.kind) {
      case 'relay.send': return m.n + ' kişiye';
      case 'job.create': return m.n + ' adres';
      case 'door.report': return m.n + ' kapı noktası';
      case 'handoff.pin': return m.layer ? 'ortak haritaya yazıldı' : 'yalnız kuryeye';
      case 'review.create': return (SRC[m.src] || m.src) + ' · ' + m.stars + '★';
      case 'user.banned': return m.reason ? 'neden: ' + m.reason + (m.by ? ' · ' + m.by : '') : (m.by || '');
      case 'user.edited': case 'user.unbanned': case 'user.reset': case 'review.hidden': return m.by ? 'yönetici: ' + m.by : '';
      case 'follow.end': return FREASON[m.reason] || '';
      case 'user.register': case 'user.role': return ROLE[m.role] || '';
      case 'user.profile': return (m.fields || []).map((x) => FIELD[x] || x).join(', ');
      default: return '';
    }
  }
  function evRow(e) {
    const who = e.userName ? h('button', { type: 'button', class: 'btn small ghost', text: e.userName, onclick: () => openUser(e.userId) }) : null;
    return h('div', { class: 'ev' }, h('span', { class: 't', text: when(e.at) }), h('span', null, h('b', { text: e.label }), ' ', metaText(e) ? h('span', { class: 'muted', text: '· ' + metaText(e) }) : null, ' ', who));
  }

  function tabEtkinlik() {
    const c = ctx.content;
    const kind = h('select', { 'aria-label': 'Etkinlik türü' }, h('option', { value: '', text: 'Tüm etkinlikler' }));
    const user = h('input', { placeholder: 'Üye kimliği (isteğe bağlı)', value: ev.userId, 'aria-label': 'Üye kimliği', class: 'grow' });
    const list = h('div'); const info = h('div', { class: 'msg', role: 'status' });
    const more = h('button', { type: 'button', class: 'btn ghost', text: 'Daha eski olanlar', hidden: true });
    let last = '';
    function fillKinds(kinds) {
      if (kind.options.length > 1) return;
      Object.keys(kinds).forEach((k) => kind.appendChild(h('option', { value: k, text: kinds[k], selected: ev.kind === k })));
    }
    let seq = 0;
    function load(reset) {
      if (reset) { clear(list); last = ''; }
      ev.kind = kind.value; ev.userId = user.value.trim();
      const my = ++seq;
      api('GET', '/v1/admin/events?' + qs({ kind: ev.kind, user_id: ev.userId, before: last, limit: 100 })).then((r) => {
        if (my !== seq) return;
        ctx.kinds = r.kinds; fillKinds(r.kinds);
        r.items.forEach((e) => list.appendChild(evRow(e)));
        if (r.items.length) last = r.items[r.items.length - 1].at;
        more.hidden = r.items.length < 100;
        say(info, !list.firstChild ? 'Kayıt yok.' : '', '');
      }).catch((er) => { if (my === seq) say(info, er.message, 'bad'); });
    }
    kind.addEventListener('change', () => load(true));
    user.addEventListener('keydown', (e) => { if (e.key === 'Enter') load(true); });
    more.addEventListener('click', () => load(false));
    c.appendChild(h('h2', { text: 'Etkinlik akışı' }));
    c.appendChild(h('p', { class: 'small muted', text: 'Kim ne zaman ne yaptı. Mesaj ve adres içeriği yazılmaz; yalnız olay ve kısa bilgi. 60 gün saklanır.' }));
    c.appendChild(h('div', { class: 'row' }, kind, user, h('button', { type: 'button', class: 'btn', text: 'Göster', onclick: () => load(true) })));
    c.appendChild(info); c.appendChild(list); c.appendChild(h('div', { class: 'row' }, more));
    load(true);
  }


  // ------------------------------------------------------------------ Takipler (son 48 saat: iz + son ilerleme)
  function dur(a, b) {
    if (!a) return '';
    const ms = (b ? new Date(b) : new Date()) - new Date(a);
    if (!(ms >= 0)) return '';
    const m = Math.round(ms / 60000);
    return m < 90 ? m + ' dk' : Math.round(m / 6) / 10 + ' sa';
  }
  function followRow(f) {
    const pill = f.status === 'active' ? 'pill ok' : f.status === 'asked' ? 'pill warn' : 'pill';
    return h('button', { type: 'button', class: 'item', 'data-follow': f.id, onclick: () => openFollow(f.id) },
      h('span', null, h('span', { class: 'nm', text: (f.employer.name || '(adsız)') + ' → ' + (f.courier.name || '(adsız)') }), ' ',
        h('span', { class: pill, text: FSTATUS[f.status] || f.status }), ' ', f.status === 'ended' && FREASON[f.endReason] ? h('span', { class: 'muted small', text: FREASON[f.endReason] }) : null),
      h('span', { class: 'muted small', text: [when(f.createdAt), f.total ? f.done + '/' + f.total + ' teslim' + (f.failed ? ', ' + f.failed + ' verilemedi' : '') : '', f.trailN ? f.trailN + ' iz noktası' : 'iz yok'].filter(Boolean).join(' · ') }));
  }

  function tabTakipler() {
    const c = ctx.content;
    const status = h('select', { 'aria-label': 'Durum' }, h('option', { value: '', text: 'Tüm takipler' }), ...Object.keys(FSTATUS).map((k) => h('option', { value: k, text: FSTATUS[k], selected: fl.status === k })));
    const user = h('input', { placeholder: 'Üye kimliği (isteğe bağlı)', value: fl.userId, 'aria-label': 'Üye kimliği', class: 'grow' });
    const list = h('div', { id: 'flist' }); const info = h('div', { class: 'msg', role: 'status' });
    const more = h('button', { type: 'button', class: 'btn ghost', text: 'Daha eski olanlar', hidden: true });
    let last = '';
    let seq = 0;
    function load(reset) {
      if (reset) { clear(list); last = ''; }
      fl.status = status.value; fl.userId = user.value.trim();
      const my = ++seq;
      api('GET', '/v1/admin/follows?' + qs({ status: fl.status, user_id: fl.userId, before: last, limit: 60 })).then((r) => {
        if (my !== seq) return;
        r.items.forEach((f) => list.appendChild(followRow(f)));
        if (r.items.length) last = r.items[r.items.length - 1].createdAt;
        more.hidden = r.items.length < 60;
        say(info, !list.firstChild ? 'Takip kaydı yok.' : '', '');
      }).catch((er) => { if (my === seq) say(info, er.message, 'bad'); });
    }
    status.addEventListener('change', () => load(true));
    user.addEventListener('keydown', (e) => { if (e.key === 'Enter') load(true); });
    more.addEventListener('click', () => load(false));
    c.appendChild(h('h2', { text: 'Takipler' }));
    c.appendChild(h('p', { class: 'small muted', text: 'İşverenin kuryeyi haritada izlediği takipler. Biten takibin konum izi, teslim edilen adreslerin sokak adı + kapı numarası ve saatleri 48 saat saklanır, sonra silinir. Bir takibi açmak işlem günlüğüne yazılır.' }));
    c.appendChild(h('div', { class: 'row' }, status, user, h('button', { type: 'button', class: 'btn', text: 'Göster', onclick: () => load(true) })));
    c.appendChild(info); c.appendChild(list); c.appendChild(h('div', { class: 'row' }, more));
    load(true);
  }

  /** Konum izini düz SVG çizgisi olarak çizer (harici harita yüklenmez). */
  function trailSvg(points) {
    const NS = 'http://www.w3.org/2000/svg';
    const W = 600; const H = 340; const PAD = 22;
    const el = (tag, attrs) => { const n = document.createElementNS(NS, tag); Object.keys(attrs).forEach((k) => n.setAttribute(k, attrs[k])); return n; };
    const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'trail', role: 'img', 'aria-label': 'Konum izi çizimi' });
    if (!points.length) return svg;
    const lats = points.map((p) => p[1]); const lons = points.map((p) => p[2]);
    const minLat = Math.min.apply(null, lats); const maxLat = Math.max.apply(null, lats);
    const minLon = Math.min.apply(null, lons); const maxLon = Math.max.apply(null, lons);
    const k = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
    const w = Math.max((maxLon - minLon) * k, 1e-6); const hh = Math.max(maxLat - minLat, 1e-6);
    const sc = Math.min((W - 2 * PAD) / w, (H - 2 * PAD) / hh);
    const ox = (W - w * sc) / 2; const oy = (H - hh * sc) / 2;
    const xy = (p) => [ox + (p[2] - minLon) * k * sc, H - oy - (p[1] - minLat) * sc];
    const pts = points.map(xy);
    if (pts.length > 1) svg.appendChild(el('polyline', { points: pts.map((q) => q[0].toFixed(1) + ',' + q[1].toFixed(1)).join(' '), class: 'line' }));
    const a = pts[0]; const b = pts[pts.length - 1];
    svg.appendChild(el('circle', { cx: a[0].toFixed(1), cy: a[1].toFixed(1), r: '6', class: 's' }));
    svg.appendChild(el('circle', { cx: b[0].toFixed(1), cy: b[1].toFixed(1), r: '6', class: 'e' }));
    return svg;
  }

  function extentText(points) {
    if (points.length < 2) return '';
    const lats = points.map((p) => p[1]); const lons = points.map((p) => p[2]);
    const mLat = (Math.max.apply(null, lats) - Math.min.apply(null, lats)) * 111320;
    const mLon = (Math.max.apply(null, lons) - Math.min.apply(null, lons)) * 111320 * Math.cos(((Math.max.apply(null, lats) + Math.min.apply(null, lats)) / 2) * Math.PI / 180);
    const fmt = (m) => (m >= 1000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m');
    return 'Kapsam: yaklaşık ' + fmt(mLon) + ' × ' + fmt(mLat);
  }

  function hhmm(sec) { try { return new Date(sec * 1000).toLocaleString('tr-TR'); } catch (e) { return String(sec); } }

  function openFollow(id) {
    const c = ctx.content; loading(c);
    api('GET', '/v1/admin/follows/' + encodeURIComponent(id)).then((d) => {
      clear(c);
      c.appendChild(h('button', { type: 'button', class: 'btn small ghost', text: '‹ Listeye dön', onclick: () => go('takipler') }));
      c.appendChild(h('h2', { text: (d.employer.name || '(adsız)') + ' → ' + (d.courier.name || '(adsız)') }));
      c.appendChild(h('div', { class: 'row' }, h('span', { class: 'pill', text: FSTATUS[d.status] || d.status }), d.status === 'ended' && FREASON[d.endReason] ? h('span', { class: 'muted small', text: FREASON[d.endReason] }) : null,
        h('span', { class: 'muted small', text: 'Takip kimliği: ' + d.id })));
      c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Zamanlar' }), kv([
        ['İstek', when(d.createdAt)], ['Kurye kabul etti', when(d.acceptedAt)], ['Bitti', d.endedAt ? when(d.endedAt) + (d.acceptedAt ? ' (' + dur(d.acceptedAt, d.endedAt) + ' sürdü)' : '') : '—'],
        ['İşveren', h('button', { type: 'button', class: 'btn small ghost', text: d.employer.name || d.employer.id, onclick: () => openUser(d.employer.id) })],
        ['Kurye', h('button', { type: 'button', class: 'btn small ghost', text: d.courier.name || d.courier.id, onclick: () => openUser(d.courier.id) })]])));
      const stopLine = (s, mark) => h('div', { class: 'ev', 'data-stop': s.id }, h('span', { class: 't', text: s.at ? hhmm(s.at) : '—' }), h('span', null, mark + ' ', h('span', { class: 'addr', text: s.label || '(adres bilgisi gelmemiş)' }), ' ', h('span', { class: 'muted small', text: s.id })));
      c.appendChild(h('div', { class: 'box', 'data-box': 'stops' }, h('h3', { text: 'Teslimatlar (adres ve saat; kuryenin telefonundan gelen son ilerleme)' }),
        d.total ? h('p', { class: 'small', text: d.done + ' teslim edildi, ' + d.failed + ' verilemedi, toplam ' + d.total + ' durak' }) : h('p', { class: 'muted small', text: 'İlerleme bilgisi gelmemiş.' }),
        d.doneStops.map((s) => stopLine(s, '✅')), d.failedStops.map((s) => stopLine(s, '❌')),
        h('p', { class: 'small muted', text: 'Adres: yalnız sokak adı, kapı numarası ve mahalle (müşteri adı, telefon, daire yok). Durak kodları telefonun ürettiği rastgele kodlardır. Saatler sunucunun ilk gördüğü andır.' })));
      const pts = d.points || [];
      const first = pts[0]; const lastp = pts[pts.length - 1];
      const osm = (p) => 'https://www.openstreetmap.org/?mlat=' + p[1] + '&mlon=' + p[2] + '#map=17/' + p[1] + '/' + p[2];
      c.appendChild(h('div', { class: 'box', 'data-box': 'trail' }, h('h3', { text: 'Konum izi' }),
        pts.length ? [
          h('p', { class: 'small', text: pts.length + ' nokta · ' + hhmm(first[0]) + ' → ' + hhmm(lastp[0]) + (extentText(pts) ? ' · ' + extentText(pts) : '') }),
          trailSvg(pts),
          h('p', { class: 'small' }, h('span', { class: 'dot s' }), ' başlangıç  ', h('span', { class: 'dot e' }), ' son nokta   ',
            h('a', { href: osm(first), target: '_blank', rel: 'noopener noreferrer', text: 'Başlangıcı haritada aç' }), '  ·  ', h('a', { href: osm(lastp), target: '_blank', rel: 'noopener noreferrer', text: 'Son noktayı haritada aç' })),
          h('details', null, h('summary', { text: 'Nokta listesi' }), h('div', { class: 'pts' }, pts.map((p) => h('div', { class: 'ev' }, h('span', { class: 't', text: hhmm(p[0]) }), h('span', { text: p[1].toFixed(6) + ', ' + p[2].toFixed(6) }))))),
        ] : h('p', { class: 'muted', text: 'Bu takipte iz noktası yok (kurye hiç konum paylaşmamış olabilir).' }),
        h('p', { class: 'small muted', text: 'Bu kayıt takip bittikten 48 saat sonra silinir.' })));
    }).catch((er) => failed(c, er));
  }

  // ------------------------------------------------------------------ Yorumlar
  function reviewCard(r, changed) {
    const msg = h('span', { class: 'small bad' });
    const note = h('input', { placeholder: 'Gizleme notu (isteğe bağlı)', maxlength: '200', 'aria-label': 'Gizleme notu' });
    const rows = [];
    if (r.tags && r.tags.length) rows.push(h('p', { class: 'small', text: 'Seçimler: ' + r.tags.join(', ') }));
    if (r.addressOk != null) rows.push(h('p', { class: 'small', text: 'Adres doğru muydu: ' + (r.addressOk ? 'evet' : 'hayır') }));
    if (r.onTime != null) rows.push(h('p', { class: 'small', text: 'Parayı vaktinde aldı: ' + (r.onTime ? 'evet' : 'hayır') + ' · eksiksiz: ' + (r.complete ? 'evet' : 'hayır') }));
    if (r.appStars) rows.push(h('p', { class: 'small', text: 'Uygulama deneyimi: ' + r.appStars + ' / 5' }));
    return h('div', { class: 'rv' + (r.hidden ? ' hid' : '') },
      h('div', { class: 'row' }, h('span', { class: 'st', role: 'img', 'aria-label': r.stars + ' yıldız', text: stars(r.stars) }), h('span', { class: 'pill', text: SRC[r.source] + ' değerlendirmesi' }),
        r.hidden ? h('span', { class: 'pill bad', text: 'Gizli' }) : null, h('span', { class: 'muted small', text: when(r.at) + (r.weight < 1 ? ' · ağırlık ' + r.weight : '') })),
      h('p', { class: 'small' }, 'Puanlanan: ', h('b', { text: (r.subject.name || r.subject.id) + ' (' + (ROLE[r.subject.role] || r.subject.role) + ')' }),
        r.author ? ['  ·  Yazan: ', h('b', { text: r.author.name || r.author.id })] : '  ·  Yazan: alıcı (hesapsız)'),
      rows, r.comment ? h('p', { text: '“' + r.comment + '”' }) : h('p', { class: 'muted small', text: 'Yorum yazılmamış.' }),
      r.hidden && r.hiddenNote ? h('p', { class: 'small muted', text: 'Gizleme notu: ' + r.hiddenNote }) : null,
      h('div', { class: 'row' },
        r.hidden ? h('button', { type: 'button', class: 'btn small ghost', text: 'Yeniden göster', onclick: () => api('POST', '/v1/admin/reviews/' + r.id + '/hide', { hidden: false }).then(changed).catch((er) => { msg.textContent = er.message; }) })
          : [note, h('button', { type: 'button', class: 'btn small ghost', text: 'Gizle', onclick: () => api('POST', '/v1/admin/reviews/' + r.id + '/hide', { hidden: true, note: note.value }).then(changed).catch((er) => { msg.textContent = er.message; }) })],
        confirmBtn('Kalıcı sil', 'ghost', 'Kalıcı olarak silinsin mi?', () => api('DELETE', '/v1/admin/reviews/' + r.id).then(changed).catch((er) => { msg.textContent = er.message; })), msg));
  }

  function tabYorumlar() {
    const c = ctx.content;
    const source = h('select', { 'aria-label': 'Kaynak' }, h('option', { value: '', text: 'Tüm kaynaklar' }), ...Object.keys(SRC).map((k) => h('option', { value: k, text: SRC[k] + ' yazdı', selected: rv.source === k })));
    const st = h('select', { 'aria-label': 'Puan' }, h('option', { value: '', text: 'Tüm puanlar' }), ...[1, 2, 3, 4, 5].map((n) => h('option', { value: String(n), text: n + ' yıldız', selected: rv.stars === String(n) })));
    const hid = h('select', { 'aria-label': 'Gizlilik' }, h('option', { value: '', text: 'Gizli olanlar dahil' }), h('option', { value: '0', text: 'Yalnız görünenler', selected: rv.hidden === '0' }), h('option', { value: '1', text: 'Yalnız gizlenenler', selected: rv.hidden === '1' }));
    const q = h('input', { type: 'search', placeholder: 'Yorumda ara', value: rv.q, 'aria-label': 'Yorumda ara', class: 'grow' });
    const list = h('div'); const info = h('div', { class: 'msg', role: 'status' });
    const more = h('button', { type: 'button', class: 'btn ghost', text: 'Daha eski olanlar', hidden: true });
    let last = '';
    let seq = 0;
    function load(reset) {
      if (reset) { clear(list); last = ''; }
      rv.source = source.value; rv.stars = st.value; rv.hidden = hid.value; rv.q = q.value.trim();
      const my = ++seq;
      api('GET', '/v1/admin/reviews?' + qs({ source: rv.source, stars: rv.stars, hidden: rv.hidden, q: rv.q, subject: rv.subject, before: last, limit: 40 })).then((r) => {
        if (my !== seq) return;
        r.items.forEach((x) => list.appendChild(reviewCard(x, () => load(true))));
        if (r.items.length) last = r.items[r.items.length - 1].at;
        more.hidden = r.items.length < 40;
        say(info, !list.firstChild ? 'Değerlendirme yok.' : '', '');
      }).catch((er) => { if (my === seq) say(info, er.message, 'bad'); });
    }
    [source, st, hid].forEach((s) => s.addEventListener('change', () => load(true)));
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter') load(true); });
    more.addEventListener('click', () => load(false));
    c.appendChild(h('h2', { text: 'Değerlendirmeler ve anket yorumları' }));
    c.appendChild(h('p', { class: 'small muted', text: 'Alıcı, işyeri ve kurye anketleri (5 yıldız üzerinden). Yorum metnini yalnız yönetim okur. Gizlenen değerlendirme ortalamaya girmez.' }));
    c.appendChild(h('div', { class: 'row' }, source, st, hid, q, h('button', { type: 'button', class: 'btn', text: 'Ara', onclick: () => load(true) })));
    c.appendChild(info); c.appendChild(list); c.appendChild(h('div', { class: 'row' }, more));
    load(true);
  }

  // ------------------------------------------------------------------ Hatalar
  function tabHatalar() {
    const c = ctx.content;
    const days = h('select', { 'aria-label': 'Aralık' }, h('option', { value: '1', text: 'Son 1 gün' }), h('option', { value: '7', text: 'Son 7 gün', selected: true }), h('option', { value: '14', text: 'Son 14 gün' }));
    const list = h('div');
    function load() {
      loading(list);
      api('GET', '/v1/admin/errors?days=' + encodeURIComponent(days.value)).then((d) => {
        clear(list);
        if (!d.errors.length) { list.appendChild(h('p', { text: 'Bu aralıkta hata raporu yok. 🎉' })); return; }
        d.errors.forEach((g) => {
          const pre = h('pre', { text: g.stack, hidden: true });
          list.appendChild(h('div', { class: 'box' + (g.fatal ? ' bad' : '') },
            h('h3', { text: (g.fatal ? '💥 ' : '') + (g.message || '(mesaj yok)') }),
            h('div', { class: 'muted small', text: g.count + ' kez · ' + g.installs + ' telefon · ' + g.kind + ' · ' + (g.screen || 'ekran ?') + ' · ' + g.platform + ' ' + (g.osVersion || '') + ' · sürüm ' + (g.appVersion || '?') }),
            h('div', { class: 'muted small', text: 'İlk: ' + when(g.firstSeen) + ' · Son: ' + when(g.lastSeen) }),
            g.stack ? [h('button', { type: 'button', class: 'btn small ghost', text: 'Ayrıntı', onclick: () => { pre.hidden = !pre.hidden; } }), pre] : null));
        });
      }).catch((er) => failed(list, er));
    }
    days.addEventListener('change', load);
    c.appendChild(h('h2', { text: 'Uygulama hataları' }));
    c.appendChild(h('div', { class: 'row' }, days));
    c.appendChild(list);
    c.appendChild(h('p', { class: 'small muted', text: 'Hata raporlarında kişisel veri ayıklanır; hesap kimliği tutulmaz. Raporlar 14 gün saklanır.' }));
    load();
  }

  // ------------------------------------------------------------------ Hesabım (yönetici)
  function tabHesap() {
    const c = ctx.content;
    c.appendChild(h('h2', { text: 'Yönetici hesabım: ' + (ctx.admin ? ctx.admin.username : '') }));

    // şifre
    const o = h('input', { type: 'password', autocomplete: 'current-password' });
    const n = h('input', { type: 'password', autocomplete: 'new-password', minlength: '10' });
    const pm = h('div', { class: 'msg', role: 'status' });
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Şifreyi değiştir' }),
      h('label', { class: 'f' }, 'Eski şifre', o), h('label', { class: 'f' }, 'Yeni şifre (en az 10 karakter)', n),
      h('button', { type: 'button', class: 'btn', text: 'Güncelle', onclick: () => {
        api('POST', '/v1/admin/password', { oldPassword: o.value, newPassword: n.value }).then((r) => { setSession(r.token); o.value = ''; n.value = ''; say(pm, 'Şifre güncellendi. Diğer oturumlar kapandı.', 'ok'); }).catch((er) => say(pm, er.message, 'bad'));
      } }), pm));

    // 2FA
    const box = h('div', { class: 'box' });
    c.appendChild(box);
    function paint2fa() {
      clear(box);
      box.appendChild(h('h3', { text: 'İki adımlı doğrulama (TOTP)' }));
      const m = h('div', { class: 'msg', role: 'status' });
      if (ctx.admin.totp) {
        const pw = h('input', { type: 'password', autocomplete: 'current-password' });
        const code = h('input', { inputmode: 'numeric', maxlength: '8', placeholder: '6 haneli kod' });
        box.appendChild(h('p', null, h('span', { class: 'pill ok', text: 'Açık' }), ' Girişte kimlik doğrulayıcı uygulamanızdaki kod da istenir.'));
        box.appendChild(h('details', null, h('summary', { text: 'Kapat' }), h('label', { class: 'f' }, 'Şifre', pw), h('label', { class: 'f' }, 'Güncel kod', code),
          h('button', { type: 'button', class: 'btn danger', text: 'İki adımlı doğrulamayı kapat', onclick: () => {
            api('POST', '/v1/admin/2fa/disable', { password: pw.value, code: code.value }).then(() => { ctx.admin.totp = false; paint2fa(); }).catch((er) => say(m, er.message, 'bad'));
          } })));
      } else {
        box.appendChild(h('p', null, h('span', { class: 'pill warn', text: 'Kapalı' }), ' Açmanız önerilir: şifreniz çalınsa bile giriş yapılamaz.'));
        box.appendChild(h('button', { type: 'button', class: 'btn', text: 'Kurulumu başlat', onclick: () => {
          api('POST', '/v1/admin/2fa/start').then((r) => {
            clear(box); box.appendChild(h('h3', { text: 'İki adımlı doğrulama kurulumu' }));
            box.appendChild(h('p', { text: '1) Telefonunuzda bir kimlik doğrulayıcı uygulama açın (Google Authenticator, Microsoft Authenticator, Aegis…). 2) Aşağıdaki QR kodu okutun ya da anahtarı elle girin. 3) Uygulamanın gösterdiği 6 haneli kodu yazıp onaylayın.' }));
            try { const qr = window.qrcode(0, 'M'); qr.addData(r.uri); qr.make(); box.appendChild(h('img', { class: 'qr', alt: 'İki adımlı doğrulama QR kodu', src: qr.createDataURL(5, 2) })); } catch (e) { /* QR çizilemezse anahtar yeter */ }
            box.appendChild(h('p', { class: 'small' }, 'Elle giriş anahtarı: ', h('span', { class: 'big', text: r.secret })));
            const code = h('input', { inputmode: 'numeric', maxlength: '8', placeholder: '6 haneli kod', autocomplete: 'one-time-code' });
            box.appendChild(h('label', { class: 'f' }, 'Uygulamadaki kod', code));
            box.appendChild(h('button', { type: 'button', class: 'btn', text: 'Onayla ve aç', onclick: () => {
              api('POST', '/v1/admin/2fa/enable', { code: code.value }).then((x) => { const was = ctx.limited; setSession(x.token); ctx.admin.totp = true; ctx.limited = false; if (was) ctx.tab = 'ozet'; showApp(); }).catch((er) => say(m, er.message, 'bad'));
            } }));
            box.appendChild(m);
          }).catch((er) => say(m, er.message, 'bad'));
        } }));
      }
      box.appendChild(m);
    }
    paint2fa();
    if (ctx.limited) return;

    // diğer yöneticiler
    const list = h('div'); const am = h('div', { class: 'msg', role: 'status' });
    const nn = h('input', { autocomplete: 'off', autocapitalize: 'none' });
    const np = h('input', { type: 'password', autocomplete: 'new-password' });
    function loadAdmins() {
      api('GET', '/v1/admin/admins').then((r) => {
        clear(list);
        r.items.forEach((a) => list.appendChild(h('div', { class: 'ev' }, h('span', { class: 't', text: 'son giriş: ' + when(a.lastLoginAt) }),
          h('span', null, h('b', { text: a.username }), ' ', a.totp ? h('span', { class: 'pill ok', text: '2FA' }) : null, a.disabled ? h('span', { class: 'pill bad', text: 'Kapalı' }) : null, ' ',
            a.username !== ctx.admin.username ? h('button', { type: 'button', class: 'btn small ghost', text: a.disabled ? 'Aç' : 'Kapat', onclick: () => api('POST', '/v1/admin/admins/' + a.id + '/disable?on=' + (a.disabled ? 'false' : 'true')).then(loadAdmins).catch((er) => say(am, er.message, 'bad')) }) : null))));
      }).catch((er) => say(am, er.message, 'bad'));
    }
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'Yöneticiler' }), list,
      h('details', null, h('summary', { text: 'Yeni yönetici ekle' }), h('label', { class: 'f' }, 'Yönetici adı', nn), h('label', { class: 'f' }, 'Şifre (en az 10 karakter)', np),
        h('button', { type: 'button', class: 'btn', text: 'Ekle', onclick: () => api('POST', '/v1/admin/admins', { username: nn.value, password: np.value }).then(() => { nn.value = ''; np.value = ''; say(am, 'Eklendi.', 'ok'); loadAdmins(); }).catch((er) => say(am, er.message, 'bad')) })), am));
    loadAdmins();

    // işlem günlüğü
    const alist = h('div'); const amore = h('button', { type: 'button', class: 'btn ghost', text: 'Daha eski olanlar', hidden: true });
    let last = '';
    function loadAudit(reset) {
      if (reset) { clear(alist); last = ''; }
      api('GET', '/v1/admin/audit?' + qs({ before: last, limit: 60 })).then((r) => {
        r.items.forEach((x) => alist.appendChild(h('div', { class: 'ev' }, h('span', { class: 't', text: when(x.at) }),
          h('span', null, h('b', { text: x.admin || '—' }), ' · ', ACT[x.action] || x.action, x.targetType ? ' · ' + (TARGET[x.targetType] || x.targetType) : '', x.detail ? h('span', { class: 'muted', text: ' · ' + x.detail }) : null, x.ip ? h('span', { class: 'muted small', text: ' · ' + x.ip }) : null))));
        if (r.items.length) last = r.items[r.items.length - 1].at;
        amore.hidden = r.items.length < 60;
      }).catch(() => {});
    }
    amore.addEventListener('click', () => loadAudit(false));
    c.appendChild(h('div', { class: 'box' }, h('h3', { text: 'İşlem günlüğü' }), h('p', { class: 'small muted', text: 'Yöneticilerin yaptığı her işlem. Şifre ve kod yazılmaz; telefon / e-posta maskelidir. 1 yıl saklanır.' }), alist, h('div', { class: 'row' }, amore)));
    loadAudit(true);
  }

  // ------------------------------------------------------------------ başlangıç
  document.getElementById('logout').addEventListener('click', () => {
    api('POST', '/v1/admin/logout').catch(() => {}).then(() => { dropSession(); showLogin(''); });
  });
  if (token) {
    api('GET', '/v1/admin/me').then((r) => {
      ctx.admin = r.admin;
      return api('GET', '/v1/admin/stats').then(() => { ctx.limited = false; }).catch((er) => { if (er.status === 403) ctx.limited = true; else throw er; });
    }).then(() => { ctx.tab = ctx.limited ? 'hesap' : 'ozet'; showApp(); }).catch(() => { dropSession(); showLogin(''); });
  } else showLogin('');
})();
