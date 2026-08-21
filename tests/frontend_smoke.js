/* Browser-like smoke test for the dashboard frontend (jsdom).
   Run: node tests/frontend_smoke.js  (requires: npm i jsdom)                 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'frontend', 'index.html'), 'utf-8');
const i18n = fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'i18n.js'), 'utf-8');
const chart = fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'chart.js'), 'utf-8');
const live = fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'live.js'), 'utf-8');
const alerts = fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'alerts.js'), 'utf-8');
const app = fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'app.js'), 'utf-8');
const data = {
  opportunities: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'opportunities.json'), 'utf-8')),
  meta: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'meta.json'), 'utf-8')),
  market: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'market.json'), 'utf-8')),
  performance: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'performance.json'), 'utf-8')),
  history: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'history.json'), 'utf-8')),
  backtest: (() => { const p = path.join(ROOT, 'data', 'performance_backtest.json');
    try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch (e) { return null; } })(),
};
const klines = {};
for (const o of data.opportunities) {
  const p = path.join(ROOT, 'data', 'klines', `${o.symbol}_4h.json`);
  if (fs.existsSync(p)) klines[`${o.symbol}_4h`] = JSON.parse(fs.readFileSync(p, 'utf-8'));
}

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  url: 'https://example.test/',
  beforeParse(window) {
    window.__EMBEDDED__ = { ...data, klines };
    window.localStorage.clear();
    window.fetch = () => Promise.reject(new Error('no network (embedded mode)'));
    window.addEventListener('error', e => errors.push('window error: ' + e.message));
    window.addEventListener('unhandledrejection', e => errors.push('unhandled rejection: ' + e.reason));
    window.console.error = (...a) => errors.push(a.map(String).join(' '));
    // Notification stub (jsdom has no Notification API)
    window.__notifs = [];
    window.Notification = function (title, opts) { window.__notifs.push({ title: String(title), opts: opts || {} }); };
    window.Notification.permission = 'granted';
    window.Notification.requestPermission = () => Promise.resolve('granted');
    // canvas stubs so chart.js executes fully without a real canvas
    const noop = () => {};
    const ctxStub = new Proxy({}, {
      get: (t, k) => (k === 'canvas' ? {} : typeof k === 'string' ? noop : undefined),
      set: () => true,
    });
    Object.defineProperty(window.HTMLCanvasElement.prototype, 'clientWidth', { get: () => 800 });
    window.HTMLCanvasElement.prototype.getContext = () => ctxStub;
    window.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 360 });
    window.devicePixelRatio = 1;
  },
});
const { window } = dom;
window.eval(i18n);
window.eval(chart);
window.eval(live);
window.eval(alerts);
window.eval(app);
window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

const wait = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const assert = (cond, msg) => {
  if (!cond) { console.error('FAIL:', msg); failures++; }
  else console.log('PASS:', msg);
};

(async () => {
  await wait(350); // let init() + loadAll() finish

  // 1. header / status bar
  assert(window.document.getElementById('last-update').textContent !== '—', 'header: last update rendered');
  assert(window.document.getElementById('market-status-text').textContent.length > 1, 'header: market status rendered');
  assert(window.document.getElementById('btc-price').textContent !== '—', 'header: BTC price rendered');

  // 2. cards
  const cards = window.document.querySelectorAll('#opps-grid .card');
  assert(cards.length === data.opportunities.length, `cards rendered: ${cards.length}`);
  if (cards.length) {
    assert(cards[0].textContent.includes('Entry'), 'card contains Entry');
    assert(cards[0].textContent.includes('Stop'), 'card contains Stop');
    assert(cards[0].textContent.includes('TP1') && cards[0].textContent.includes('TP3'), 'card contains TPs');
    assert(cards[0].textContent.includes('R:R'), 'card contains R:R');
  }

  // 3. modal + chart
  const btn = window.document.querySelector('[data-open]');
  if (btn) {
    btn.click();
    await wait(150);
    const modal = window.document.getElementById('modal');
    assert(!modal.classList.contains('hidden'), 'modal opens');
    const mbody = window.document.getElementById('m-body');
    assert(mbody.textContent.includes('Score Breakdown'), 'modal: score breakdown');
    assert(mbody.textContent.includes('Timeframe Analysis'), 'modal: timeframe analysis');
    assert(mbody.textContent.includes('SuperTrend') || mbody.textContent.includes('سوبر ترند'), 'modal: SuperTrend column');
    assert(mbody.textContent.includes('Support'), 'modal: support/resistance');
    const cvs = window.document.getElementById('m-chart');
    assert(!!cvs && cvs.width > 0, 'modal: chart canvas drawn');
    window.document.getElementById('m-close').click();
    assert(modal.classList.contains('hidden'), 'modal closes');
  }

  // 4. search + filters
  const search = window.document.getElementById('search');
  search.value = data.opportunities[0] ? data.opportunities[0].symbol.slice(0, 3) : 'BTC';
  search.dispatchEvent(new window.Event('input', { bubbles: true }));
  assert(window.document.querySelectorAll('#opps-grid .card').length >= 1, 'search filter works');
  search.value = '';
  search.dispatchEvent(new window.Event('input', { bubbles: true }));
  const sort = window.document.getElementById('f-sort');
  sort.value = 'rr'; sort.dispatchEvent(new window.Event('change', { bubbles: true }));
  const st = window.document.getElementById('f-status');
  st.value = 'READY'; st.dispatchEvent(new window.Event('change', { bubbles: true }));
  const ready = data.opportunities.filter(o => o.status === 'READY').length;
  assert(window.document.querySelectorAll('#opps-grid .card').length === ready, `status filter (READY=${ready})`);
  st.value = 'ALL'; st.dispatchEvent(new window.Event('change', { bubbles: true })); // reset for later checks

  // 5. language toggle (AR + RTL)
  window.document.getElementById('lang-btn').click();
  await wait(50);
  assert(window.document.documentElement.dir === 'rtl', 'AR toggle switches to RTL');
  assert(window.document.querySelector('h1').textContent.includes('لوحة'), 'AR title rendered');
  window.document.getElementById('lang-btn').click();

  // 6. theme toggle (dark <-> light)
  const themeBtn = window.document.getElementById('theme-btn');
  themeBtn.click();
  await wait(50);
  assert(window.document.documentElement.dataset.theme === 'light', 'theme toggles to light');
  assert(themeBtn.textContent.includes('☀️'), 'theme button label updates (light)');
  themeBtn.click();
  await wait(50);
  assert(window.document.documentElement.dataset.theme === 'dark', 'theme toggles back to dark');

  // 7. countdown + live badge (wait for the 1s tick to run at least once)
  await wait(1400);
  const cdText = window.document.getElementById('countdown-text').textContent;
  assert(/^\d{1,2}:\d{2}$/.test(cdText) || cdText === 'SYNC', `countdown displays (got "${cdText}")`);
  const badge = window.document.getElementById('live-badge');
  assert(/live|stale|updating/.test(badge.className), 'live badge has a valid state class');
  const ring = window.document.getElementById('countdown-ring');
  assert(ring.style.strokeDashoffset !== '' && !isNaN(parseFloat(ring.style.strokeDashoffset)),
         'countdown ring progress is being updated');
  const nu = window.document.getElementById('next-update').textContent;
  assert(/\(\d{1,2}:\d{2}\)$/.test(nu) || /^\d{1,2}:\d{2}$/.test(nu) || nu === 'SYNC' || nu === 'مزامنة',
         `next-update stat is dynamic (got "${nu}")`);

  // 7b. live prices (WebSocket) — graceful in jsdom (no WS): elements must exist
  assert(typeof window.LivePrices === 'object' && typeof window.LivePrices.subscribe === 'function',
         'LivePrices module loaded');
  assert(window.document.querySelectorAll('[data-live-sym]').length >= 1, 'live price elements rendered');
  if (window.document.querySelector('.badge.st-up, .badge.st-down')) {
    assert(true, 'SuperTrend badge on card');
  } else {
    assert(data.opportunities.every(o => !o.analysis || !o.analysis['4h']), 'no ST badge only when no 4h analysis');
  }

  // 7c. alerts: panel, diff detection, emit pipeline
  assert(typeof window.Alerts === 'object' && typeof window.Alerts.diffEvents === 'function', 'Alerts module loaded');
  const alertBtn = window.document.getElementById('alert-btn');
  const alertPanel = window.document.getElementById('alert-panel');
  assert(!!alertBtn && !!alertPanel, 'alert button + panel exist');
  alertBtn.click();
  await wait(40);
  assert(!alertPanel.classList.contains('hidden'), 'alert panel opens');
  assert(alertPanel.querySelectorAll('input[type="checkbox"]').length >= 8, 'alert toggles present');
  // diff: TP hit + new setup + stopped
  const prevOpps = [
    { id: 'a', pair: 'T/USDT', status: 'TRIGGERED' },
    { id: 'b', pair: 'X/USDT', status: 'READY' },
  ];
  const nextOpps = [
    { id: 'a', pair: 'T/USDT', status: 'TP1_HIT' },
    { id: 'b', pair: 'X/USDT', status: 'STOPPED' },
    { id: 'c', pair: 'Y/USDT', status: 'READY' },
  ];
  const evs = window.Alerts.diffEvents(prevOpps, nextOpps);
  const types = evs.map(e => e.type).sort();
  assert(JSON.stringify(types) === JSON.stringify(['new_setup', 'stopped', 'tp_hit']),
         'diffEvents detects lifecycle changes: ' + types.join(','));
  // emit -> toast + notification (permission granted stub)
  const beforeToasts = window.document.querySelectorAll('.toast').length;
  const beforeNotifs = window.__notifs.length;
  window.Alerts.emit({ type: 'tp_hit', opp: { pair: 'T/USDT' } });
  await wait(30);
  assert(window.document.querySelectorAll('.toast').length === beforeToasts + 1, 'emit creates a toast');
  assert(window.__notifs.length === beforeNotifs + 1, 'emit creates a browser notification');
  assert(window.__notifs[beforeNotifs].title.includes('TP'), 'notification title mentions TP');
  // close panel
  window.document.body.click();
  assert(alertPanel.classList.contains('hidden'), 'alert panel closes on outside click');

  // 8. performance tab
  window.document.querySelector('[data-tab="performance"]').click();
  await wait(50);
  assert(window.document.getElementById('perf-grid').textContent.length > 0 ||
         window.document.getElementById('history-empty').textContent.length > 0, 'performance tab renders');
  const btBox = window.document.getElementById('bt-section');
  assert(btBox && btBox.textContent.length > 20, 'backtest section rendered');
  if (btBox.textContent.includes('Score Calibration') || btBox.textContent.includes('معايرة')) {
    assert(true, 'calibration table present');
  } else {
    assert(!data.backtest, 'backtest section empty only when no data');
  }

  console.log('\nJS console errors captured:', errors.length);
  errors.slice(0, 5).forEach(e => console.log('  ERR:', String(e).slice(0, 200)));
  assert(errors.length === 0, 'no JavaScript errors');
  console.log(failures === 0 ? '\nSMOKE TEST: ALL PASS' : `\nSMOKE TEST: ${failures} FAILURES`);
  window.close();
  process.exitCode = failures > 0 ? 1 : 0;
})().catch(e => { console.error('HARNESS ERROR:', e); process.exitCode = 1; window.close(); });
