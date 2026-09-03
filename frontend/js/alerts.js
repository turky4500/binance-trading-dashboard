/* Alerts module — lifecycle notifications & sounds for the dashboard.
   - Browser notifications (Notification API, permission-based)
   - Distinctive WebAudio beeps per event type (no external files)
   - Detects lifecycle diffs when fresh data arrives (emit via Alerts.emit)
   - Preferences persisted in localStorage; control panel in the top bar. */
'use strict';

const Alerts = (function () {
  const DEFAULT_PREFS = {
    notif: true,
    sound: true,
    events: {
      new_setup: true, ready: true, triggered: true, tp_hit: true,
      stopped: true, expired: false, invalidated: false,
    },
  };

  const TPL = {
    en: {
      new_setup: '🆕 New setup: {pair}',
      ready: '👌 READY — {pair}',
      triggered: '🎯 Triggered — {pair}',
      tp_hit: '✅ TP hit — {pair}',
      stopped: '🛑 Stop hit — {pair}',
      expired: '⏳ Expired — {pair}',
      invalidated: '❌ Invalidated — {pair}',
    },
    ar: {
      new_setup: '🆕 فرصة جديدة: {pair}',
      ready: '👌 جاهزة — {pair}',
      triggered: '🎯 مُفعّلة — {pair}',
      tp_hit: '✅ تحقق هدف — {pair}',
      stopped: '🛑 ضُرب الوقف — {pair}',
      expired: '⏳ انتهت — {pair}',
      invalidated: '❌ أُلغيت — {pair}',
    },
  };

  let prefs = load();
  let audioCtx = null;
  let panelBound = false;

  function load() {
    try {
      const s = localStorage.getItem('dash-alerts');
      if (!s) return JSON.parse(JSON.stringify(DEFAULT_PREFS));
      const p = JSON.parse(s);
      return {
        notif: p.notif !== false,
        sound: p.sound !== false,
        events: Object.assign({}, DEFAULT_PREFS.events, p.events || {}),
      };
    } catch (e) {
      return JSON.parse(JSON.stringify(DEFAULT_PREFS));
    }
  }
  function save() { try { localStorage.setItem('dash-alerts', JSON.stringify(prefs)); } catch (e) {} }

  /* ---------------- sound (WebAudio) ---------------- */
  function tone(freq, startOffset, dur, vol) {
    if (!audioCtx) return;
    try {
      const osc = audioCtx.createOscillator();
      const g = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      const t0 = audioCtx.currentTime + startOffset;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(vol, t0 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      osc.connect(g); g.connect(audioCtx.destination);
      osc.start(t0); osc.stop(t0 + dur + 0.05);
    } catch (e) { /* audio unavailable — stay silent */ }
  }

  function ensureAudio() {
    try {
      if (!audioCtx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        audioCtx = new AC();
      }
      if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
    } catch (e) {}
  }

  function beep(kind) {
    if (!prefs.sound) return;
    ensureAudio();
    if (!audioCtx || audioCtx.state === 'suspended') return; // blocked until a user gesture
    switch (kind) {
      case 'new_setup': tone(880, 0, .18, .07); break;
      case 'ready': tone(587, 0, .16, .07); break;
      case 'triggered': tone(660, 0, .12, .07); tone(660, .18, .12, .07); break;
      case 'tp_hit': tone(523, 0, .14, .07); tone(784, .16, .24, .08); break;
      case 'stopped': tone(784, 0, .14, .08); tone(392, .16, .28, .08); break;
      case 'expired':
      case 'invalidated': tone(330, 0, .25, .06); break;
    }
  }

  /* ---------------- browser notifications ---------------- */
  function notifSupported() { return typeof window !== 'undefined' && 'Notification' in window; }
  function notifPermission() { return notifSupported() ? Notification.permission : 'unsupported'; }

  function browserNotify(title, body) {
    if (!prefs.notif || !notifSupported()) return;
    if (Notification.permission !== 'granted') return;
    try { new Notification(title, { body: body || '', tag: 'dash-alert' }); } catch (e) {}
  }

  function requestPermission(cb) {
    if (!notifSupported()) { cb('unsupported'); return; }
    try { Notification.requestPermission().then(cb).catch(() => cb('denied')); }
    catch (e) { cb('denied'); }
  }

  /* ---------------- event detection & emission ---------------- */
  function eventLabel(type, pair) {
    const lang = (typeof window !== 'undefined' && window.LANG === 'ar') ? 'ar' : 'en';
    return (TPL[lang][type] || type).replace('{pair}', pair);
  }

  function diffEvents(prevOpps, nextOpps) {
    // returns [{type, opp, from}] for status changes + brand-new opportunities
    const out = [];
    const prevById = {};
    (prevOpps || []).forEach(o => { prevById[o.id] = o; });
    (nextOpps || []).forEach(o => {
      const p = prevById[o.id];
      if (!p) { out.push({ type: 'new_setup', opp: o, from: null }); return; }
      if (p.status !== o.status) {
        const map = {
          READY: 'ready', TRIGGERED: 'triggered',
          TP1_HIT: 'tp_hit', TP2_HIT: 'tp_hit', TP3_HIT: 'tp_hit',
          STOPPED: 'stopped', EXPIRED: 'expired', INVALIDATED: 'invalidated',
        };
        const t = map[o.status];
        if (t) out.push({ type: t, opp: o, from: p.status });
      }
    });
    return out;
  }

  function emit(ev) {
    if (!prefs.events[ev.type]) return;
    const pair = ev.opp.pair || ev.opp.symbol;
    const label = eventLabel(ev.type, pair);
    beep(ev.type);
    browserNotify(label);
    if (typeof window !== 'undefined' && window.toast) window.toast(label, 'alert');
  }

  /* ---------------- preferences panel ---------------- */
  function syncPanel() {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
    set('al-notif', prefs.notif);
    set('al-sound', prefs.sound);
    Object.keys(DEFAULT_PREFS.events).forEach(k => set('al-ev-' + k, prefs.events[k]));
    const btn = document.getElementById('alert-btn');
    if (btn) btn.textContent = (prefs.notif || prefs.sound) ? '🔔' : '🔕';
  }

  function initPanel() {
    if (panelBound || typeof document === 'undefined') return;
    panelBound = true;
    const btn = document.getElementById('alert-btn');
    const panel = document.getElementById('alert-panel');
    if (!btn || !panel) return;
    syncPanel();
    btn.addEventListener('click', e => { e.stopPropagation(); panel.classList.toggle('hidden'); });
    document.addEventListener('click', e => {
      if (!panel.classList.contains('hidden') && !panel.contains(e.target) && e.target !== btn) {
        panel.classList.add('hidden');
      }
    });
    const notif = document.getElementById('al-notif');
    notif.addEventListener('change', () => {
      if (notif.checked) {
        if (!notifSupported()) {
          if (window.toast) window.toast(window.t ? window.t('notif_unsupported') : 'Notifications unsupported', 'warn');
          notif.checked = false; return;
        }
        const perm = notifPermission();
        if (perm === 'granted') { prefs.notif = true; save(); syncPanel(); }
        else if (perm === 'denied') {
          if (window.toast) window.toast(window.t ? window.t('notif_blocked') : 'Blocked', 'warn');
          notif.checked = false;
        } else {
          requestPermission(p => {
            prefs.notif = p === 'granted';
            save(); syncPanel();
            if (p !== 'granted' && window.toast) window.toast(window.t ? window.t('notif_blocked') : 'Blocked', 'warn');
          });
        }
      } else { prefs.notif = false; save(); syncPanel(); }
    });
    document.getElementById('al-sound').addEventListener('change', e => {
      prefs.sound = e.target.checked; save(); syncPanel();
      if (e.target.checked) beep('new_setup'); // instant feedback
    });
    Object.keys(DEFAULT_PREFS.events).forEach(k => {
      const el = document.getElementById('al-ev-' + k);
      if (el) el.addEventListener('change', e => { prefs.events[k] = e.target.checked; save(); });
    });
    const test = document.getElementById('al-test');
    if (test) test.addEventListener('click', () => { ensureAudio(); beep('tp_hit'); });
  }

  function testSound() { ensureAudio(); beep('tp_hit'); }

  return {
    initPanel, syncPanel, diffEvents, emit, testSound,
    prefs: () => prefs,
    setPrefs: (p) => { prefs = Object.assign(prefs, p); save(); syncPanel(); },
    eventLabel,
    notify: (title, body) => { beep('ready'); browserNotify(title, body); },
  };
})();
window.Alerts = Alerts;
