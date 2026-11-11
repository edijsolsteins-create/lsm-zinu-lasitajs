'use strict';
/*
 * Ziņu lasītājs — client.
 * One big button: a short tap/press and a long hold (~1 s). On a phone the whole
 * screen is the button; on a laptop it is the spacebar (or a click).
 */

const $ = id => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem('zl_' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('zl_' + k, JSON.stringify(v)); } catch (e) {} },
};
const TOUCH = matchMedia('(pointer: coarse)').matches;

// Access key comes in the link (?k=…). It stays in the URL so "Add to Home screen" keeps it.
const urlKey = new URLSearchParams(location.search).get('k');
if (urlKey) store.set('key', urlKey);
const KEY = urlKey || store.get('key', '');
const api = (path, opts = {}) => {
  opts.headers = Object.assign({ 'X-Key': KEY }, opts.headers || {});
  return fetch(path, opts);
};

const CFG = { waitSeconds: 6, longPressMs: 1000, rate: 0.9, photo: false, azure: false, azureVoices: [], version: '' };

const S = {
  view: 'home', mode: 'home', session: null,        // session: 'lsm' | 'photo'
  items: [], idx: 0,
  lsm: { items: [], loadedAt: 0, idx: 0, resumable: false },
  art: null, pos: 0, token: 0,
  pending: null, waitTimer: null, waitResolve: null, afterIntro: null,
  voices: [], voice: null, utter: null,
  rate: store.get('rate', null), teaser: store.get('teaser', false),
};
const ttsEl = $('ttsAudio');
const newsEl = $('newsAudio');

// ---------------------------------------------------------------- words --
const PRESS = TOUCH ? 'pieskarieties ekrānam' : 'nospiediet pogu';
const HOLD = TOUCH ? 'Ja turat pirkstu uz ekrāna ilgāk' : 'Ja pogu turat nospiestu ilgāk';
const TAP = TOUCH ? 'Pieskarieties' : 'Spiediet';
const HOLD_SHORT = TOUCH ? 'Turiet pirkstu' : 'Turiet';

function lsmIntro() {
  return [
    greeting(),
    'Šī ir ziņu lasītāja programma.',
    'Tā skaļi nolasa jaunākās ziņas no Latvijas Sabiedrisko mediju portāla, lai jums tās nebūtu jālasa no ekrāna.',
    TOUCH ? 'Viss notiek, pieskaroties ekrānam. Pieskarties var jebkurā vietā.'
          : 'Viss notiek ar vienu pogu. Šajā datorā poga ir garais taustiņš klaviatūras apakšā.',
    'Es nolasīšu ziņu virsrakstus pa vienam.',
    'Pēc katra virsraksta atskanēs šāda skaņa.',
    '#BEEP',
    'Tad būs īsa pauze.',
    `Ja ziņa jūs interesē, ${PRESS}, un es nolasīšu visu rakstu.`,
    'Ja neko nedarīsiet, es pāriešu pie nākamās ziņas.',
    `Kamēr lasu rakstu, ar īsu ${TOUCH ? 'pieskārienu' : 'spiedienu'} varat to apturēt un pēc tam turpināt.`,
    `${HOLD}, es pārtraucu lasīt un atgriežos pie virsrakstiem.`,
    'Sāksim!',
  ];
}
const photoIntro = () => [
  'Kamēr gaidām, īsi paskaidrošu.',
  'Es atradīšu virsrakstus šajā avīzes lapā un nolasīšu tos pa vienam.',
  `Ja raksts jūs interesē, ${PRESS} pēc virsraksta, un es nolasīšu visu rakstu.`,
  `${HOLD}, es pārtraucu lasīt.`,
];

function homeHint() {
  if (!CFG.photo) return TOUCH ? 'Lai klausītos vēlreiz, pieskarieties ekrānam.' : 'Lai klausītos vēlreiz, nospiediet pogu.';
  if (!TOUCH) return 'Lai klausītos ziņas, nospiediet pogu. Lai lasītu avīzi, nospiediet burtu A.';
  return matchMedia('(min-aspect-ratio: 4/3)').matches
    ? 'Ekrāna kreisajā pusē ir interneta ziņas, labajā pusē avīzes lasīšana.'
    : 'Ekrāna augšdaļā ir interneta ziņas, apakšdaļā avīzes lasīšana.';
}

const ORD = ['', 'pirmais', 'otrais', 'trešais', 'ceturtais', 'piektais', 'sestais', 'septītais', 'astotais',
  'devītais', 'desmitais', 'vienpadsmitais', 'divpadsmitais', 'trīspadsmitais', 'četrpadsmitais',
  'piecpadsmitais', 'sešpadsmitais', 'septiņpadsmitais', 'astoņpadsmitais', 'deviņpadsmitais',
  'divdesmitais', 'divdesmit pirmais', 'divdesmit otrais', 'divdesmit trešais', 'divdesmit ceturtais',
  'divdesmit piektais', 'divdesmit sestais', 'divdesmit septītais', 'divdesmit astotais',
  'divdesmit devītais', 'trīsdesmitais', 'trīsdesmit pirmais'];
const MONTHS = ['janvāris', 'februāris', 'marts', 'aprīlis', 'maijs', 'jūnijs', 'jūlijs', 'augusts',
  'septembris', 'oktobris', 'novembris', 'decembris'];
const DAYS = ['svētdiena', 'pirmdiena', 'otrdiena', 'trešdiena', 'ceturtdiena', 'piektdiena', 'sestdiena'];
// accusative, for "Atradu … rakstus"
const ACC = ['nulle', 'vienu', 'divus', 'trīs', 'četrus', 'piecus', 'sešus', 'septiņus', 'astoņus', 'deviņus',
  'desmit', 'vienpadsmit', 'divpadsmit', 'trīspadsmit', 'četrpadsmit', 'piecpadsmit', 'sešpadsmit',
  'septiņpadsmit', 'astoņpadsmit', 'deviņpadsmit', 'divdesmit'];
const dateWords = d => `${DAYS[d.getDay()]}, ${ORD[d.getDate()]} ${MONTHS[d.getMonth()]}`;
const greeting = () => { const h = new Date().getHours(); return h < 11 ? 'Labrīt!' : h < 18 ? 'Labdien!' : 'Labvakar!'; };
const dot = s => (s && !/[.!?:…]$/.test(s.trim())) ? s.trim() + '.' : (s || '').trim();
const foundWords = n => `Atradu ${ACC[n] || n} ${n % 10 === 1 && n % 100 !== 11 ? 'rakstu' : 'rakstus'}.`;

function easyIntro(it) {
  let day = '';
  if (it.date) {
    const d = new Date(it.date), t = new Date(), y = new Date(Date.now() - 864e5);
    if (d.toDateString() === t.toDateString()) day = 'Šodienas z';
    else if (d.toDateString() === y.toDateString()) day = 'Vakardienas z';
  }
  return (day || 'Z') + 'iņas vieglajā valodā. Latvijas Radio ieraksts.';
}

function headlineSpeech(it) {
  if (!it) return '';
  if (it.type === 'weather') return 'Laika ziņas. ' + dot(it.say_title) + ' ' + (it.say_desc || '');
  if (it.type === 'easy') return easyIntro(it);
  return dot(it.say_title) + (it.type === 'news' && S.teaser && it.say_desc ? ' ' + it.say_desc : '');
}

// --------------------------------------------------------------- voices --
const shortName = n => n.replace(/^Microsoft\s+/i, '').replace(/\s+Online.*$/i, '')
  .replace(/\s*[-(]\s*Latvian.*$/i, '').replace(/^lv-LV-/, '').replace(/Neural$/, '').trim();

function buildVoices() {
  const list = [];
  if ('speechSynthesis' in window) {
    for (const v of speechSynthesis.getVoices()) {
      if (!/^lv/i.test(v.lang) || /undefined/i.test(v.name)) continue;
      const natural = /natural/i.test(v.name);
      list.push({ id: 'b:' + v.name, kind: 'browser', v, rank: natural ? 0 : 2,
                  label: shortName(v.name) + (natural ? '' : ' · ierīces balss') });
    }
  }
  for (const n of CFG.azureVoices || []) list.push({ id: 'a:' + n, kind: 'azure', name: n, rank: 1, label: shortName(n) + ' · Azure' });
  list.sort((a, b) => a.rank - b.rank);
  S.voices = list;
  const want = store.get('voice', null);
  S.voice = list.find(x => x.id === want) || list[0] || null;
  if (list.length) hideWarn('voice');
  renderSettings();
}
function checkVoiceWarning() {
  if (S.voices.length) return;
  warn('voice', TOUCH
    ? 'Šajā telefonā nav atrasta latviešu balss. Android: Iestatījumi → Teksta pārveide runā → instalējiet latviešu valodu. Vai arī serverī ieslēdziet Azure balsis.'
    : 'Šajā pārlūkā nav atrasta latviešu balss. Atveriet šo adresi pārlūkā Microsoft Edge (tajā ir balsis Everita un Nils).');
}

// ------------------------------------------------------------- speaking --
function say(text) {
  const my = S.token;
  if (!text || my !== S.token) return Promise.resolve(my === S.token);
  return S.voice && S.voice.kind === 'azure' ? sayAzure(text, my) : sayBrowser(text, my, S.voice && S.voice.v);
}

function sayBrowser(text, my, voice) {
  return new Promise(resolve => {
    if (!('speechSynthesis' in window)) return resolve(my === S.token);
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'lv-LV';
    if (voice) u.voice = voice;
    u.rate = S.rate;
    let done = false, guard = null;
    const finish = () => {
      if (done) return;
      done = true; clearTimeout(guard);
      if (S.pending === finish) S.pending = null;
      resolve(my === S.token);
    };
    S.pending = finish;
    u.onend = finish; u.onerror = finish;
    S.utter = u;                       // keep a reference, or some browsers drop onend
    guard = setTimeout(finish, 6000 + text.length * 110 / S.rate);
    setTimeout(() => { if (my === S.token) speechSynthesis.speak(u); else finish(); }, 40);
  });
}

const ttsUrl = text => `/api/tts?v=${encodeURIComponent(S.voice.name)}&r=${S.rate}&k=${encodeURIComponent(KEY)}&t=${encodeURIComponent(text)}`;
function prefetch(text) {
  if (text && S.voice && S.voice.kind === 'azure') fetch(ttsUrl(text)).catch(() => {});
}
function sayAzure(text, my) {
  return new Promise(resolve => {
    let done = false, guard = null;
    const finish = () => {
      if (done) return;
      done = true; clearTimeout(guard);
      ttsEl.onended = ttsEl.onerror = null;
      if (S.pending === finish) S.pending = null;
      resolve(my === S.token);
    };
    S.pending = finish;
    ttsEl.onended = finish;
    ttsEl.onerror = () => { console.warn('TTS audio error'); finish(); };
    guard = setTimeout(finish, 20000 + text.length * 120 / S.rate);
    ttsEl.src = ttsUrl(text);
    const p = ttsEl.play();
    if (p) p.catch(e => { console.warn(e); finish(); });
  });
}

function hush() {
  S.token++;
  clearTimeout(S.waitTimer);
  if (S.waitResolve) { const r = S.waitResolve; S.waitResolve = null; r(false); }
  try { speechSynthesis.cancel(); } catch (e) {}
  ttsEl.pause();
  newsEl.pause();
  if (S.pending) S.pending();
  if (S.art) wake(S.art);
  stopBar();
  stopTicking();
}

function waitWindow(ms) {
  const my = S.token;
  return new Promise(resolve => {
    S.waitResolve = resolve;
    S.waitTimer = setTimeout(() => { S.waitResolve = null; resolve(my === S.token); }, ms);
  });
}

// --------------------------------------------------------------- sounds --
let actx = null;
function tone(freq, ms, vol = 0.15) {
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    if (actx.state === 'suspended') actx.resume();
    const o = actx.createOscillator(), g = actx.createGain(), t = actx.currentTime;
    o.type = 'sine'; o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + ms / 1000);
    o.connect(g); g.connect(actx.destination);
    o.start(t); o.stop(t + ms / 1000 + 0.05);
  } catch (e) {}
}
const beepReady = () => { tone(660, 170); setTimeout(() => tone(880, 230), 190); };
const pressSound = () => tone(1000, 70, 0.1);
const longSound = () => { tone(440, 140); setTimeout(() => tone(300, 220), 150); };
let tickTimer = null;
function startTicking() { stopTicking(); tickTimer = setInterval(() => tone(520, 60, 0.05), 1500); }
function stopTicking() { clearInterval(tickTimer); tickTimer = null; }

function silentWav(ms = 150) {
  const rate = 8000, n = Math.floor(rate * ms / 1000), buf = new ArrayBuffer(44 + n * 2), d = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) d.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); d.setUint32(4, 36 + n * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  d.setUint32(16, 16, true); d.setUint16(20, 1, true); d.setUint16(22, 1, true);
  d.setUint32(24, rate, true); d.setUint32(28, rate * 2, true); d.setUint16(32, 2, true); d.setUint16(34, 16, true);
  w(36, 'data'); d.setUint32(40, n * 2, true);
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}
let unlocked = false;
function unlockAudio() {           // phones only allow sound that starts from a tap: unlock everything on the first one
  if (unlocked) return;
  unlocked = true;
  const url = silentWav();
  for (const el of [ttsEl, newsEl]) {
    try { el.src = url; const p = el.play(); if (p) p.then(() => el.pause()).catch(() => {}); } catch (e) {}
  }
  tone(20, 10, 0.0002);
  if ('speechSynthesis' in window) {
    try { const u = new SpeechSynthesisUtterance(' '); u.volume = 0; speechSynthesis.speak(u); } catch (e) {}
  }
}

// --------------------------------------------------------------- screen --
function setMode(m) {
  S.mode = m;
  const cls = { wait: 'wait', article: 'article', waitText: 'article', audio: 'article', paused: 'paused', audioPaused: 'paused' }[m] || '';
  document.body.classList.remove('wait', 'article', 'paused');
  if (cls) document.body.classList.add(cls);
}
function show(status, main, hint) {
  if (status != null) $('status').textContent = status;
  if (main != null) $('mainText').textContent = main;
  if (hint != null) $('hint').textContent = hint;
  fit();
}
function fit() {                    // largest font that fits the free space (short headlines get huge)
  const box = $('main'), span = $('mainText');
  if (S.view !== 'reader' || !box.clientHeight) return;
  let lo = 18, hi = Math.max(24, Math.min(box.clientWidth * 0.16, 140));
  while (hi - lo > 1) {
    const mid = (lo + hi) / 2;
    span.style.fontSize = mid + 'px';
    if (span.offsetHeight <= box.clientHeight && span.scrollWidth <= box.clientWidth + 1) lo = mid; else hi = mid;
  }
  span.style.fontSize = Math.floor(lo) + 'px';
}
let barAnim = null;
function startBar(ms) {
  stopBar();
  barAnim = $('bar').firstElementChild.animate(
    [{ transform: 'scaleX(1)' }, { transform: 'scaleX(0)' }], { duration: ms, fill: 'forwards' });
}
function stopBar() { if (barAnim) { barAnim.cancel(); barAnim = null; } }

const warnings = {};
function warn(id, text) { warnings[id] = text; renderWarn(); }
function hideWarn(id) { delete warnings[id]; renderWarn(); }
function renderWarn() {
  const t = Object.values(warnings).join(' ');
  $('warn').textContent = t;
  $('warn').style.display = t ? 'block' : 'none';
}

let wakeLock = null;
async function keepAwake(on) {
  try {
    if (on && !wakeLock && 'wakeLock' in navigator) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } else if (!on && wakeLock) {
      await wakeLock.release();
      wakeLock = null;
    }
  } catch (e) {}
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && S.view === 'reader') keepAwake(true);
});

function enterReader(session) {
  S.view = 'reader';
  S.session = session;
  $('home').hidden = true;
  $('stage').hidden = false;
  document.body.classList.remove('home');
  show('', '', '');
  keepAwake(true);
}
function goHome() {
  S.view = 'home';
  S.session = null;
  S.afterIntro = null;
  setMode('home');
  $('stage').hidden = true;
  $('home').hidden = false;
  document.body.classList.add('home');
  $('btnPhoto').hidden = !CFG.photo;
  keepAwake(false);
}
async function failHome(msg) {
  hush();
  goHome();
  await say(msg);
}

// ----------------------------------------------------------------- data --
async function loadConfig() {
  try {
    const r = await api('/api/config');
    if (r.status === 401) {
      warn('key', 'Trūkst piekļuves atslēgas. Atveriet pilno saiti, ko rāda serveris (tā beidzas ar ?k=…).');
    } else {
      Object.assign(CFG, await r.json());
      hideWarn('server');
    }
  } catch (e) {
    warn('server', 'Nevar sazināties ar serveri. Pārbaudiet, vai tas darbojas un vai telefons ir tajā pašā Wi-Fi.');
  }
  if (S.rate === null) S.rate = CFG.rate;
  buildVoices();
  $('btnPhoto').hidden = !CFG.photo;
}

async function loadLsm() {
  try {
    const r = await api('/api/headlines');
    const j = await r.json();
    if (j.errors && j.errors.length) console.warn('Ielādes kļūdas:', j.errors);
    S.lsm.items = j.items || [];
    S.lsm.loadedAt = Date.now();
    return S.lsm.items.length > 0;
  } catch (e) {
    console.error(e);
    return false;
  }
}

// An article "source" is a growing list of sentences; readers wait while it is still arriving.
const newSource = () => ({ sentences: [], done: false, failed: false, waiters: [] });
function wake(src) { const w = src.waiters; src.waiters = []; w.forEach(f => f()); }
function waitMore(src) {
  const my = S.token;
  return new Promise(res => src.waiters.push(() => res(my === S.token)));
}

function lsmSource(it) {
  const src = newSource();
  api('/api/article?u=' + encodeURIComponent(it.link)).then(r => r.json()).then(j => {
    src.sentences = (j && j.sentences) || [];
  }).catch(e => console.warn(e)).finally(() => {
    if (!src.sentences.length && it.say_desc) src.sentences = [it.say_desc];
    if (!src.sentences.length) src.failed = true;
    src.done = true;
    wake(src);
  });
  return src;
}

function streamSource(url) {        // newspaper article: sentences arrive while Claude is still reading the photo
  const src = newSource();
  const take = line => {
    line = line.trim();
    if (!line) return;
    try {
      const m = JSON.parse(line);
      if (m.s) { src.sentences.push(m.s); wake(src); }
      if (m.error) src.failed = true;
    } catch (e) {}
  };
  (async () => {
    try {
      const r = await api(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      if (r.body && r.body.getReader) {
        const reader = r.body.getReader(), dec = new TextDecoder();
        let buf = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let nl;
          while ((nl = buf.indexOf('\n')) >= 0) { take(buf.slice(0, nl)); buf = buf.slice(nl + 1); }
        }
        take(buf);
      } else {
        (await r.text()).split('\n').forEach(take);
      }
    } catch (e) {
      console.warn(e);
      src.failed = true;
    }
    src.done = true;
    wake(src);
  })();
  return src;
}

// ---------------------------------------------------------------- flows --
async function startLsm() {
  hush();
  enterReader('lsm');
  const L = S.lsm;
  const stale = !L.items.length || Date.now() - L.loadedAt > 15 * 60e3;
  if (L.resumable && !stale) {
    L.resumable = false;
    S.items = L.items;
    S.idx = L.idx;
    setMode('busy');
    show('Ziņu lasītājs', 'Turpinu', '');
    if (!await say('Turpinu.')) return;
    return runHeadlines();
  }
  L.resumable = false;
  const loading = stale ? loadLsm() : Promise.resolve(true);
  const go = () => lsmAfterIntro(loading);
  if (!store.get('introHeard', false)) {
    return playIntro(lsmIntro(), () => { store.set('introHeard', true); go(); });
  }
  setMode('busy');
  show('Ziņu lasītājs', greeting(), '');
  if (!await say(greeting())) return;
  go();
}

async function lsmAfterIntro(loading) {
  const my = S.token;
  setMode('busy');
  show('Ziņu lasītājs', 'Ielādēju ziņas…', '');
  const ok = await loading;
  if (my !== S.token) return;
  if (!ok) return failHome('Diemžēl neizdevās ielādēt ziņas. Pārbaudiet interneta savienojumu un mēģiniet vēlreiz.');
  S.items = S.lsm.items;
  S.idx = 0;
  const today = 'Šodien ir ' + dateWords(new Date()) + '.';
  show('Ziņu lasītājs', today, '');
  if (!await say(today + ' Jaunākās ziņas.')) return;
  runHeadlines();
}

async function playIntro(lines, then) {
  S.afterIntro = then;
  setMode('intro');
  show('Kā tas darbojas', '', `${TAP}, lai izlaistu`);
  for (const s of lines) {
    if (s === '#BEEP') { beepReady(); if (!await waitWindow(900)) return; continue; }
    show(null, s, null);
    if (!await say(s)) return;
  }
  const f = S.afterIntro;
  S.afterIntro = null;
  if (f) f();
}

async function runHeadlines() {
  const photo = S.session === 'photo';
  while (S.idx < S.items.length) {
    const it = S.items[S.idx];
    setMode('headline');
    show(`${photo ? 'Raksts' : 'Ziņa'} ${S.idx + 1} no ${S.items.length}`,
         it.type === 'easy' ? easyIntro(it) : it.title,
         it.type === 'easy' ? `${TAP}, lai klausītos ierakstu` : `${TAP}, ja ${photo ? 'raksts' : 'ziņa'} interesē`);
    prefetch(headlineSpeech(S.items[S.idx + 1]));
    if (!await say(headlineSpeech(it))) return;
    setMode('wait');
    beepReady();
    startBar(CFG.waitSeconds * 1000);
    if (!await waitWindow(CFG.waitSeconds * 1000)) return;
    stopBar();
    S.idx++;
  }
  setMode('busy');
  const msg = photo ? 'Tie bija visi raksti šajā lapā.' : 'Tās bija visas jaunākās ziņas.';
  show('Beigas', msg, '');
  if (!await say(msg)) return;
  S.lsm.resumable = false;
  goHome();
  await say(homeHint());
}

async function openItem() {
  const it = S.items[S.idx];
  if (!it) return;
  hush();
  if (it.type === 'easy') return playEasy(it);
  setMode('busy');
  show('Lasu rakstu', it.title, 'Lūdzu, uzgaidiet');
  S.art = it.type === 'photo'
    ? streamSource(`/api/photo/article?pid=${encodeURIComponent(it.pid)}&id=${it.id}`)
    : lsmSource(it);
  S.pos = 0;
  if (!await say(it.type === 'photo' ? 'Lasu rakstu. Lūdzu, uzgaidiet.' : 'Lasu rakstu.')) return;
  readArticle();
}

async function readArticle() {
  const src = S.art;
  const hint = `${TAP} — pauze · ${HOLD_SHORT} — atpakaļ`;
  setMode('article');
  for (;;) {
    if (S.pos < src.sentences.length) {
      setMode('article');
      show(`Lasu rakstu · ${S.pos + 1}${src.done ? ' / ' + src.sentences.length : ''}`, src.sentences[S.pos], hint);
      prefetch(src.sentences[S.pos + 1]);
      if (!await say(src.sentences[S.pos])) return;
      S.pos++;
      continue;
    }
    if (src.done) break;
    setMode('waitText');
    show('Lūdzu, uzgaidiet', S.pos ? null : 'Atšifrēju rakstu…', hint);
    startTicking();
    const ok = await waitMore(src);
    stopTicking();
    if (!ok) return;
  }
  setMode('busy');
  const next = S.session === 'photo' ? 'Turpinu ar nākamo rakstu.' : 'Turpinu ar nākamo ziņu.';
  let msg = 'Raksts beidzās. ' + next;
  if (!src.sentences.length) msg = 'Diemžēl šo rakstu neizdevās nolasīt. ' + next;
  else if (src.failed) msg = 'Diemžēl raksta turpinājumu neizdevās nolasīt. ' + next;
  show('Raksts beidzās', S.items[S.idx] ? S.items[S.idx].title : '', '');
  if (!await say(msg)) return;
  S.idx++;
  runHeadlines();
}

function pauseArticle() {
  hush();
  setMode('paused');
  show('Pauze', null, `${TAP}, lai turpinātu · ${HOLD_SHORT} — atpakaļ`);
  say('Pauze.');
}

async function playEasy(it) {
  const my = S.token;
  setMode('busy');
  show('Ziņas vieglajā valodā', 'Latvijas Radio ieraksts', 'Lūdzu, uzgaidiet');
  let url = it.audio, art = null;
  const req = url ? null : api('/api/article?u=' + encodeURIComponent(it.link)).then(r => r.json()).catch(() => null);
  if (!await say('Atskaņoju ierakstu.')) return;
  if (!url) {
    art = await req;
    if (my !== S.token) return;
    url = art && art.audio;
  }
  if (!url) {
    if (art && art.sentences && art.sentences.length) {
      S.art = Object.assign(newSource(), { sentences: art.sentences, done: true });
      S.pos = 0;
      return readArticle();
    }
    if (!await say('Diemžēl ierakstu neizdevās atrast. Turpinu ar nākamo ziņu.')) return;
    S.idx++;
    return runHeadlines();
  }
  const next = async msg => {
    if (my !== S.token) return;
    setMode('busy');
    if (!await say(msg)) return;
    S.idx++;
    runHeadlines();
  };
  newsEl.onended = () => next('Ieraksts beidzās. Turpinu ar nākamo ziņu.');
  newsEl.onerror = () => next('Diemžēl ierakstu neizdevās atskaņot. Turpinu ar nākamo ziņu.');
  newsEl.src = url;
  setMode('audio');
  show('Ziņas vieglajā valodā', 'Latvijas Radio ieraksts', `${TAP} — pauze · ${HOLD_SHORT} — atpakaļ`);
  try { await newsEl.play(); } catch (e) { newsEl.onerror(); }
}

async function backToHeadlines() {
  hush();
  setMode('busy');
  const words = S.session === 'photo' ? 'Atpakaļ pie virsrakstiem.' : 'Atpakaļ pie ziņām.';
  show('Atpakaļ', words, '');
  if (!await say(words)) return;
  S.idx++;
  runHeadlines();
}

function stopToHome(silent) {
  const resumable = S.session === 'lsm' && S.items.length > 0 && ['headline', 'wait', 'busy'].includes(S.mode);
  hush();
  if (resumable) { S.lsm.resumable = true; S.lsm.idx = S.idx; }
  goHome();
  if (!silent) say('Apturēts. ' + homeHint());
}

// ---- newspaper photo
function startPhoto() {
  if (!CFG.photo) return;
  hush();
  const inp = $('camera');
  inp.value = '';
  inp.click();                      // must happen inside the tap, or the phone blocks the camera
}
$('camera').addEventListener('change', () => {
  const f = $('camera').files && $('camera').files[0];
  if (f) readPhoto(f);
});

async function readPhoto(file) {
  hush();
  enterReader('photo');
  const my = S.token;
  setMode('photoBusy');
  show('Avīzes lapa', 'Lasu avīzes lapu…', 'Lūdzu, uzgaidiet');
  const job = sendPhoto(file);
  const lines = ['Paldies. Lasu avīzes lapu, tas aizņems dažas sekundes.'];
  if (!store.get('photoIntroHeard', false)) lines.push(...photoIntro());
  for (const s of lines) {
    show(null, s, null);
    if (!await say(s)) return;
  }
  store.set('photoIntroHeard', true);
  show(null, 'Lasu avīzes lapu…', null);
  startTicking();
  const res = await job;
  stopTicking();
  if (my !== S.token) return;
  if (!res || res.error) {
    return failHome(res && res.error === 'limit'
      ? 'Šodien avīzes lasīšanas limits ir sasniegts. Mēģiniet rīt.'
      : 'Diemžēl foto neizdevās apstrādāt. Pārbaudiet internetu un mēģiniet vēlreiz.');
  }
  if (!res.items || !res.items.length) {
    return failHome('Šajā foto neizdevās atrast virsrakstus. Mēģiniet vēlreiz: novietojiet avīzi labā gaismā ' +
      'un turiet telefonu tieši virs lapas, lai visa lapa ir redzama.');
  }
  S.items = res.items.map(x => ({ type: 'photo', title: x.headline, say_title: x.say_headline || x.headline, pid: res.pid, id: x.id }));
  S.idx = 0;
  if (!await say(foundWords(S.items.length))) return;
  runHeadlines();
}

async function sendPhoto(file) {
  try {
    const images = await prepareImages(file);
    const r = await api('/api/photo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ images }),
    });
    return await r.json();
  } catch (e) {
    console.error(e);
    return { error: String(e) };
  }
}

// Whole page (for layout) + four overlapping quarters (so small print stays readable).
async function prepareImages(file) {
  let src;
  try {
    src = await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch (e) {
    src = await new Promise((res, rej) => {
      const img = new Image();
      img.onload = () => res(img);
      img.onerror = rej;
      img.src = URL.createObjectURL(file);
    });
  }
  const W = src.width, H = src.height;
  const out = [toJpeg(src, 0, 0, W, H, 2400)];
  if (Math.max(W, H) > 2600) {
    const ox = W * 0.06, oy = H * 0.06, hw = W / 2, hh = H / 2;
    for (const [cx, cy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
      const sx = Math.max(0, cx * hw - ox), sy = Math.max(0, cy * hh - oy);
      out.push(toJpeg(src, sx, sy, Math.min(W - sx, hw + 2 * ox), Math.min(H - sy, hh + 2 * oy), 2400));
    }
  }
  return out;
}
function toJpeg(src, sx, sy, sw, sh, maxLong) {
  const k = Math.min(1, maxLong / Math.max(sw, sh));
  const c = document.createElement('canvas');
  c.width = Math.round(sw * k);
  c.height = Math.round(sh * k);
  const g = c.getContext('2d');
  g.imageSmoothingQuality = 'high';
  g.drawImage(src, sx, sy, sw, sh, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.85).split(',')[1];
}

// ------------------------------------------------------ the one button --
function shortPress() {
  pressSound();
  switch (S.mode) {
    case 'home': return startLsm();
    case 'intro': { hush(); const f = S.afterIntro; S.afterIntro = null; return f && f(); }
    case 'headline': case 'wait': return openItem();
    case 'article': case 'waitText': return pauseArticle();
    case 'paused': hush(); return readArticle();
    case 'audio':
      newsEl.pause(); setMode('audioPaused');
      show('Pauze', null, `${TAP}, lai turpinātu · ${HOLD_SHORT} — atpakaļ`);
      return;
    case 'audioPaused':
      newsEl.play(); setMode('audio');
      show('Ziņas vieglajā valodā', null, `${TAP} — pauze · ${HOLD_SHORT} — atpakaļ`);
      return;
    default: return;                 // busy / photoBusy: ignore
  }
}

function longPress() {
  longSound();
  switch (S.mode) {
    case 'home': return;
    case 'article': case 'waitText': case 'paused': case 'audio': case 'audioPaused': return backToHeadlines();
    default: return stopToHome(false);
  }
}

let downAt = 0, longTimer = null, longFired = false;
function pressDown() {
  if (downAt) return;
  unlockAudio();
  downAt = performance.now();
  longFired = false;
  document.body.classList.add('flash');
  longTimer = setTimeout(() => {
    longFired = true;
    document.body.classList.remove('flash');
    longPress();
  }, CFG.longPressMs);
}
function pressUp() {
  if (!downAt) return;
  downAt = 0;
  clearTimeout(longTimer);
  document.body.classList.remove('flash');
  if (!longFired) shortPress();
}
function cancelPress() {
  downAt = 0;
  clearTimeout(longTimer);
  document.body.classList.remove('flash');
}
const settingsOpen = () => !$('settings').hidden;

$('stage').addEventListener('pointerdown', e => {
  if (settingsOpen()) return;
  e.preventDefault();
  pressDown();
});
window.addEventListener('pointerup', pressUp);
window.addEventListener('pointercancel', cancelPress);
window.addEventListener('blur', cancelPress);
window.addEventListener('contextmenu', e => { if (!settingsOpen()) e.preventDefault(); });
window.addEventListener('resize', fit);

$('btnLsm').addEventListener('click', e => {
  e.currentTarget.blur();
  unlockAudio(); pressSound();
  startLsm();
});
$('btnPhoto').addEventListener('click', e => {
  e.currentTarget.blur();
  unlockAudio(); pressSound();
  startPhoto();
});

const isButtonKey = e => e.code === 'Space' || e.key === ' ' || e.key === 'Enter';
window.addEventListener('keydown', e => {
  if (settingsOpen()) { if (e.key === 'Escape') closeSettings(); return; }
  if (isButtonKey(e)) { e.preventDefault(); if (!e.repeat) pressDown(); return; }
  if (!e.repeat) helperKey(e);
});
window.addEventListener('keyup', e => {
  if (settingsOpen()) return;
  if (isButtonKey(e)) { e.preventDefault(); pressUp(); }
});

// ------------------------------------------- settings / helper controls --
function setRate(r) {
  S.rate = Math.round(Math.min(1.5, Math.max(0.5, r)) * 100) / 100;
  store.set('rate', S.rate);
  renderSettings();
  if (S.mode === 'home') { hush(); say('Šāds ir runas ātrums.'); }
}
function pickVoice(i) {
  const v = S.voices[i];
  if (!v) return;
  S.voice = v;
  store.set('voice', v.id);
  renderSettings();
  if (S.mode === 'home') { hush(); say('Šī ir balss ' + v.label.split(' ·')[0] + '.'); }
}
function renderSettings() {
  const list = $('voiceList');
  list.innerHTML = '';
  if (!S.voices.length) list.innerHTML = '<p class="small">Latviešu balss nav atrasta.</p>';
  S.voices.forEach((v, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'pill' + (v === S.voice ? ' on' : '');
    b.textContent = `${i + 1}. ${v.label}`;
    b.onclick = () => { unlockAudio(); pickVoice(i); };
    list.appendChild(b);
  });
  $('rateVal').textContent = (S.rate == null ? CFG.rate : S.rate).toFixed(2);
  $('teaserBtn').textContent = S.teaser ? 'Ieslēgts' : 'Izslēgts';
  $('teaserBtn').classList.toggle('on', !!S.teaser);
  $('about').textContent = `Versija ${CFG.version || '–'} · Avīzes foto: ${CFG.photo ? 'ieslēgts' : 'izslēgts'} · ` +
    `Azure balsis: ${CFG.azure ? 'ieslēgtas' : 'izslēgtas'}`;
}
function openSettings() {
  unlockAudio();
  if (S.view === 'reader') stopToHome(true);   // settings are for the helper: pause the session first
  renderSettings();
  $('settings').hidden = false;
}
function closeSettings() { $('settings').hidden = true; hush(); }

$('gear').addEventListener('click', openSettings);
$('closeSettings').addEventListener('click', closeSettings);
$('slower').addEventListener('click', () => setRate(S.rate - 0.05));
$('faster').addEventListener('click', () => setRate(S.rate + 0.05));
$('teaserBtn').addEventListener('click', () => { S.teaser = !S.teaser; store.set('teaser', S.teaser); renderSettings(); });
$('introBtn').addEventListener('click', () => {
  store.set('introHeard', false);
  store.set('photoIntroHeard', false);
  hush();
  say('Paskaidrojums tiks atskaņots nākamreiz.');
});
$('reloadBtn').addEventListener('click', () => {
  Object.assign(S.lsm, { items: [], loadedAt: 0, resumable: false });
  hush();
  say('Ziņas tiks ielādētas no jauna.');
});

async function jumpEasy() {
  hush();
  enterReader('lsm');
  const my = S.token;
  setMode('busy');
  show('Ziņu lasītājs', 'Ielādēju ziņas…', '');
  if (!S.lsm.items.length || Date.now() - S.lsm.loadedAt > 15 * 60e3) await loadLsm();
  if (my !== S.token) return;
  const i = S.lsm.items.findIndex(x => x.type === 'easy');
  if (i < 0) return failHome('Ziņas vieglajā valodā šobrīd neizdevās atrast.');
  S.items = S.lsm.items;
  S.idx = i;
  openItem();
}

function helperKey(e) {
  const k = e.key;
  if (/^[1-9]$/.test(k)) return pickVoice(+k - 1);
  if (k === '+' || k === '=' || k === 'ArrowUp') return setRate(S.rate + 0.05);
  if (k === '-' || k === '_' || k === 'ArrowDown') return setRate(S.rate - 0.05);
  switch (k.toLowerCase()) {
    case 'a': unlockAudio(); return startPhoto();
    case 't': S.teaser = !S.teaser; store.set('teaser', S.teaser); renderSettings(); return;
    case 'i': store.set('introHeard', false); unlockAudio(); return startLsm();
    case 'v': unlockAudio(); return jumpEasy();
    case 'r': Object.assign(S.lsm, { items: [], loadedAt: 0, resumable: false }); if (S.view === 'reader') stopToHome(true); return;
    case 'escape': if (S.view === 'reader') stopToHome(true); return;
  }
}

// ---------------------------------------------------------------- start --
if ('speechSynthesis' in window) speechSynthesis.onvoiceschanged = buildVoices;
loadConfig();
setTimeout(checkVoiceWarning, 3500);
