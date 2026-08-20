/* Browser-like smoke test for the dashboard frontend (jsdom) */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const ROOT = '/home/user/binance-trading-dashboard';
const html = fs.readFileSync(`${ROOT}/frontend/index.html`, 'utf-8');
const i18n = fs.readFileSync(`${ROOT}/frontend/js/i18n.js`, 'utf-8');
const chart = fs.readFileSync(`${ROOT}/frontend/js/chart.js`, 'utf-8');
const app = fs.readFileSync(`${ROOT}/frontend/js/app.js`, 'utf-8');
const data = {
  opportunities: JSON.parse(fs.readFileSync(`${ROOT}/data/opportunities.json`, 'utf-8')),
  meta: JSON.parse(fs.readFileSync(`${ROOT}/data/meta.json`, 'utf-8')),
  market: JSON.parse(fs.readFileSync(`${ROOT}/data/market.json`, 'utf-8')),
  performance: JSON.parse(fs.readFileSync(`${ROOT}/data/performance.json`, 'utf-8')),
  history: JSON.parse(fs.readFileSync(`${ROOT}/data/history.json`, 'utf-8')),
};
const klines = {};
for (const o of data.opportunities) {
  klines[`${o.symbol}_4h`] = JSON.parse(fs.readFileSync(`${ROOT}/data/klines/${o.symbol}_4h.json`, 'utf-8'));
}

const errors = [];
const vcErrors = [];
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
    // canvas stub so chart.js executes fully
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
window.eval(app);
window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

const wait = ms => new Promise(r => setTimeout(r, ms));
const assert = (cond, msg) => { if (!cond) { console.error('FAIL:', msg); process.exitCode = 1; } else console.log('PASS:', msg); };

(async () => {
  await wait(300); // let init() run (loadAll is async)

  // 1. header rendered
  assert(window.document.getElementById('last-update').textContent !== '—', 'header: last update rendered');
  assert(window.document.getElementById('market-status-text').textContent.length > 1, 'header: market status rendered');
  assert(window.document.getElementById('btc-price').textContent !== '—', 'header: BTC price rendered');
  const banner = window.document.getElementById('error-banner');
  console.log('  banner hidden (fresh data):', banner.classList.contains('hidden'));

  // 2. cards rendered
  const cards = window.document.querySelectorAll('#opps-grid .card');
  assert(cards.length === data.opportunities.length, `cards rendered: ${cards.length}`);
  assert(cards[0].textContent.includes('Entry'), 'card contains Entry field');
  assert(cards[0].textContent.includes('Stop'), 'card contains Stop field');
  assert(cards[0].textContent.includes('TP1') && cards[0].textContent.includes('TP3'), 'card contains TPs');
  assert(cards[0].textContent.includes('R:R'), 'card contains R:R');
  assert(!window.document.getElementById('empty-state').classList.contains('hidden') === false, 'empty state hidden when data exists');

  // 3. open modal
  const btn = window.document.querySelector('[data-open]');
  btn.click();
  await wait(200);
  const modal = window.document.getElementById('modal');
  assert(!modal.classList.contains('hidden'), 'modal opens');
  const mbody = window.document.getElementById('m-body');
  assert(mbody.textContent.includes('Score Breakdown'), 'modal: score breakdown');
  assert(mbody.textContent.includes('Timeframe Analysis'), 'modal: timeframe analysis');
  assert(mbody.textContent.includes('Support'), 'modal: support/resistance');
  assert(mbody.textContent.includes('Why this'), 'modal: reasons');
  const chartCanvas = window.document.getElementById('m-chart');
  assert(!!chartCanvas && chartCanvas.width > 0, 'modal: chart canvas drawn');
  window.document.getElementById('m-close').click();
  assert(modal.classList.contains('hidden'), 'modal closes');

  // 4. search filter
  const search = window.document.getElementById('search');
  search.value = data.opportunities[0].symbol.slice(0, 3);
  search.dispatchEvent(new window.Event('input', { bubbles: true }));
  assert(window.document.querySelectorAll('#opps-grid .card').length >= 1, 'search filter works');

  // 5. sort + status filter
  const sort = window.document.getElementById('f-sort');
  sort.value = 'rr'; sort.dispatchEvent(new window.Event('change', { bubbles: true }));
  assert(window.document.querySelectorAll('#opps-grid .card').length >= 1, 'sort change works');
  const st = window.document.getElementById('f-status');
  st.value = 'READY'; st.dispatchEvent(new window.Event('change', { bubbles: true }));
  const ready = data.opportunities.filter(o => o.status === 'READY').length;
  assert(window.document.querySelectorAll('#opps-grid .card').length === ready, `status filter (READY=${ready})`);

  // 6. language toggle → RTL
  console.log('typeof setLang:', typeof window.setLang, '| typeof renderAll:', typeof window.renderAll);
  window.setLang('ar');
  console.log('dir after direct setLang(ar):', window.document.documentElement.dir);
  window.setLang('en');
  const lb = window.document.getElementById('lang-btn');
  lb.addEventListener('click', () => console.log('harness saw click on lang-btn'));
  console.log('LANG before click:', window.LANG);
  lb.click();
  await wait(50);
  console.log('LANG after click:', window.LANG);
  console.log('btn label:', lb.textContent);
  console.log('dir after AR click:', window.document.documentElement.dir);
  console.log('h1 after AR click:', window.document.querySelector('h1').textContent);
  assert(window.document.documentElement.dir === 'rtl', 'AR toggle switches to RTL');
  assert(window.document.querySelector('h1').textContent.includes('باينانس') || window.document.querySelector('h1').textContent.includes('لوحة'), 'AR title rendered');
  window.document.getElementById('lang-btn').click();

  // 7. performance tab
  window.document.querySelector('[data-tab="performance"]').click();
  await wait(50);
  assert(window.document.getElementById('perf-grid').textContent.length > 0 || window.document.getElementById('history-empty').textContent.length > 0, 'performance tab renders');

  console.log('\nJS console errors captured:', errors.length);
  errors.slice(0, 5).forEach(e => console.log('  ERR:', e.slice(0, 200)));
  assert(errors.length === 0, 'no JavaScript errors');
  console.log('\nSMOKE TEST DONE');
})().catch(e => { console.error('HARNESS ERROR:', e); process.exitCode = 1; });
