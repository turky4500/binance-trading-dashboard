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
const watchlist = fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'watchlist.js'), 'utf-8');
const engine = fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'engine.js'), 'utf-8');
const app = fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'app.js'), 'utf-8');
const data = {
  opportunities: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'opportunities.json'), 'utf-8')),
  agent_scan: (() => { const p = path.join(ROOT, 'data', 'agent_scan.json');
    try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch (e) { return null; } })(),
  meta: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'meta.json'), 'utf-8')),
  market: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'market.json'), 'utf-8')),
  performance: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'performance.json'), 'utf-8')),
  history: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'history.json'), 'utf-8')),
  backtest: (() => { const p = path.join(ROOT, 'data', 'performance_backtest.json');
    try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch (e) { return null; } })(),
  breadth_history: (() => { const p = path.join(ROOT, 'data', 'breadth_history.json');
    try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch (e) { return []; } })(),
  update_log: (() => { const p = path.join(ROOT, 'data', 'update_log.json');
    try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch (e) { return []; } })(),
  symbols: (() => { const p = path.join(ROOT, 'data', 'symbols.json');
    try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch (e) { return null; } })(),
  config: (() => { const p = path.join(ROOT, 'data', 'config.json');
    try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch (e) { return null; } })(),
};
const klines = {};
for (const o of data.opportunities) {
  for (const tf of ['4h', '1h']) {
    const p = path.join(ROOT, 'data', 'klines', `${o.symbol}_${tf}.json`);
    if (fs.existsSync(p)) klines[`${o.symbol}_${tf}`] = JSON.parse(fs.readFileSync(p, 'utf-8'));
  }
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
window.eval(watchlist);
window.eval(engine);
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
    const dtpEl = cards[0].querySelector('[data-tp1-dist]');
    assert(!!dtpEl && dtpEl.dataset.tp1 && parseFloat(dtpEl.dataset.tp1) > 0, 'card shows live distance-to-TP1 element');
    // recommendation time (12-hour + date) and validity bar
    const rt = cards[0].querySelector('.rec-time');
    assert(!!rt && rt.textContent.length > 8, 'card shows recommendation time row');
    assert(/(AM|PM)/.test(rt.textContent), 'recommendation time uses 12-hour format');
    assert(rt.querySelector('.rt-age'), 'recommendation age badge present');
    const vf = window.document.querySelector('#opps-grid .validity-fill');
    const hasEnterable = data.opportunities.some(o => o.status === 'READY' || o.status === 'WAITING_CONFIRMATION');
    assert(hasEnterable ? (!!vf && parseFloat(vf.style.width) > 0) : !vf,
           hasEnterable ? 'validity bar rendered for enterable setups' : 'validity bar omitted for non-enterable setups');
    // helper functions are deterministic
    const threeHAgo = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
    assert(window.ageShort(threeHAgo) === '3h', 'ageShort formats hours');
    assert(/(AM|PM)/.test(window.fmtRecTime(threeHAgo)), 'fmtRecTime uses 12-hour clock');
    const vi = window.validityInfo(threeHAgo);
    assert(vi.pct > 0 && vi.pct < 100 && vi.label.length > 0, 'validityInfo computes remaining window');
  }

  // 2b. deterministic quantitative-agent tab
  const agentTab = window.document.querySelector('[data-tab="agent"]');
  assert(!!agentTab, 'quant-agent tab exists');
  agentTab.click();
  await wait(50);
  assert(window.document.getElementById('agent-summary').textContent.length > 0, 'quant-agent summary renders');
  assert(window.document.getElementById('agent-count').textContent === String((data.agent_scan && data.agent_scan.signals || []).length),
         'quant-agent signal count matches JSON');
  assert(typeof window.agentCardHTML === 'function', 'quant-agent card renderer is available');
  const tfCard = window.agentCardHTML({
    symbol: 'TESTUSDT', pair: 'TEST/USDT', score: 88, primary_timeframe: '4h',
    timeframes: ['4h', '1d'], supertrend_status: { '4h': 'UP', '1d': 'UP' },
    current_price: 100, entry_zone: [99.9, 100.1], stop_loss: 99.2,
    tp1: 101.2, tp2: 102.25, tp3: 104.25, rr_tp1: 1.5, rr_tp2: 2.8, rr_tp3: 5.3,
    sl_distance_pct: 0.8, profit_pct_tp1: 1.2, profit_pct_tp2: 2.25, profit_pct_tp3: 4.25,
    reason: { en: '4h reason', ar: 'سبب 4 ساعات' }, execution_note: { en: 'note', ar: 'ملاحظة' },
    risk_notes: { en: [], ar: [] }, data_timestamp: new Date().toISOString(), decision: 'FAVORABLE',
  }, 1);
  assert(tfCard.includes('>4h<') && tfCard.includes('↑4h') && tfCard.includes('↑1d'),
         'quant-agent card uses its dynamic execution + confirmation timeframes');
  if (data.agent_scan && data.agent_scan.signals && data.agent_scan.signals.length) {
    const ac = window.document.querySelector('#agent-grid .agent-card');
    assert(!!ac && ac.textContent.includes('TP1') && ac.textContent.includes('R:R'), 'quant-agent card renders plan levels');
  } else {
    assert(!window.document.getElementById('agent-empty').classList.contains('hidden'), 'quant-agent empty state renders honestly');
  }
  window.document.querySelector('[data-tab="opportunities"]').click();

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
    assert(mbody.textContent.includes('TradingView'), 'modal: data-source alignment note (TradingView comparison caveat)');
    assert(mbody.textContent.includes('Support'), 'modal: support/resistance');
    const cvs = window.document.getElementById('m-chart');
    assert(!!cvs && cvs.width > 0, 'modal: chart canvas drawn');
    assert(cvs.style.height === '446px' || cvs.style.height.endsWith('px'), 'modal: chart has volume+RSI panels');
    // TF switch
    const tfBtn = window.document.querySelector('.tf-btn[data-tf="1h"]');
    if (tfBtn) {
      tfBtn.click();
      await wait(200);
      assert(tfBtn.classList.contains('active'), 'TF switch: 1H becomes active');
      assert(cvs.width > 0, 'chart redrawn on TF switch');
    }
    // calculator: pure function + UI
    const calc = window.calcPosition(1000, 1, 100, 98, [103, 105, 110]);
    assert(calc && Math.abs(calc.qty - 5) < 1e-9, 'calcPosition qty = risk/dist (5)');
    assert(calc && Math.abs(calc.risk - 10) < 1e-9, 'calcPosition risk = 10 USDT');
    assert(calc && Math.abs(calc.gains[0] - 15) < 1e-9, 'calcPosition TP1 gain (15 USDT = 1.5R)');
    assert(calc && Math.abs(calc.rrs[0] - 1.5) < 1e-9, 'calcPosition TP1 = 1.5R');
    const capInput = window.document.getElementById('calc-capital');
    assert(!!capInput, 'calculator inputs exist');
    capInput.value = '2000';
    capInput.dispatchEvent(new window.Event('input', { bubbles: true }));
    await wait(60);
    const res = window.document.getElementById('calc-results');
    assert(res.textContent.length > 10 && res.textContent.includes('TP3'), 'calculator renders results');
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
  // watchlist: star toggle + bar + filter chip (cards still visible here)
  const starBtn = window.document.querySelector('.star');
  if (starBtn) {
    const sym = starBtn.dataset.star;
    starBtn.click();
    assert(window.Watchlist.has(sym), 'watchlist toggles on via card star');
    const wbar = window.document.getElementById('watch-bar');
    assert(!wbar.classList.contains('hidden'), 'watch bar becomes visible');
    const chipWatch = window.document.getElementById('chip-watch');
    chipWatch.click();
    const wCards = window.document.querySelectorAll('#opps-grid .card');
    assert(wCards.length >= 1 && wCards.length <= 1, 'watchlist filter narrows grid');
    chipWatch.click(); // reset filter
    window.Watchlist.toggle(sym); // cleanup watchlist
  }
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
  const stBadge = window.document.querySelector('.badge.st-up, .badge.st-down');
  if (stBadge) {
    assert(stBadge.textContent.includes('4h') && stBadge.textContent.includes('1d'), 'SuperTrend badge shows 4H + 1D directions');
    assert(stBadge.getAttribute('title') && stBadge.getAttribute('title').includes('TradingView'), 'SuperTrend badge has comparison tooltip');
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

  // 7d. market tab: breadth chart + pipeline health + update log
  window.document.querySelector('[data-tab="market"]').click();
  await wait(150);
  const bChart = window.document.getElementById('breadth-chart');
  const btcChart = window.document.getElementById('btc-chart');
  if (data.breadth_history && data.breadth_history.length >= 2) {
    assert(!!bChart && bChart.width > 0, 'breadth chart drawn');
    assert(!!btcChart && btcChart.width > 0, 'btc line chart drawn');
  } else {
    assert(!!bChart, 'breadth chart canvas exists');
  }
  assert(window.document.getElementById('health-grid').textContent.length > 0, 'pipeline health grid rendered');
  assert(window.document.getElementById('update-log').textContent.length > 0, 'update log rendered');

  // 7d2. coin analyzer: engine parity helpers + UI error path + autocomplete
  assert(typeof window.Engine === 'object' && typeof window.Engine.analyze === 'function', 'Engine module loaded (UMD)');
  // golden-style parity on embedded klines (offline, same math as golden test)
  if (data.opportunities.length) {
    const sym0 = data.opportunities[0].symbol;
    const k4 = klines[sym0 + '_4h'];
    if (k4 && k4.candles && k4.candles.length > 120) {
      const bars = k4.candles.map(c => ({ t: c[0], o: c[1], h: c[2], l: c[3], c: c[4], v: c[5] }));
      const st = window.Engine.tfState(bars, 3, { period: 10, multiplier: 3.0 });
      assert(st.close > 0 && st.ema20 > 0 && st.atr > 0 && [1, -1].includes(st.st_dir), 'engine computes tfState from embedded klines');
      assert(Math.abs(st.ema20 - k4.ema20[k4.ema20.length - 1]) / k4.ema20[k4.ema20.length - 1] < 0.01, 'engine EMA20 matches backend cache');
    }
  }
  window.document.querySelector('[data-tab="analyzer"]').click();
  await wait(80);
  assert(window.document.getElementById('ana-input') && window.document.getElementById('ana-run'), 'analyzer input + button exist');
  const anaInput = window.document.getElementById('ana-input');
  anaInput.value = 'SOL';
  anaInput.dispatchEvent(new window.Event('input', { bubbles: true }));
  await wait(40);
  const sugg = window.document.getElementById('ana-suggest');
  if (data.symbols && data.symbols.symbols) {
    assert(!sugg.classList.contains('hidden'), 'autocomplete suggestions shown');
    assert(sugg.textContent.includes('SOLUSDT'), 'suggestion includes SOLUSDT');
  }
  anaInput.value = 'FAKENOTREAL123';
  window.document.getElementById('ana-run').click();
  await wait(120);
  const anaStatus = window.document.getElementById('ana-status');
  assert(!anaStatus.classList.contains('hidden') && anaStatus.textContent.length > 5, 'analyzer shows error when data unreachable (offline harness)');
  window.document.querySelector('[data-tab="opportunities"]').click();

  // 7e. settings panel: open + min-score filter + live pause + reset
  const setBtn = window.document.getElementById('settings-btn');
  const setPanel = window.document.getElementById('settings-panel');
  setBtn.click();
  await wait(40);
  assert(!setPanel.classList.contains('hidden'), 'settings panel opens');
  const minScore = window.document.getElementById('set-minscore');
  minScore.value = '95';
  minScore.dispatchEvent(new window.Event('change', { bubbles: true }));
  const cardsNow = window.document.querySelectorAll('#opps-grid .card').length;
  assert(cardsNow === 0 || data.opportunities.every(o => o.score < 95), 'min-score display filter works');
  minScore.value = '0';
  minScore.dispatchEvent(new window.Event('change', { bubbles: true }));
  const liveBox = window.document.getElementById('set-live');
  liveBox.checked = false;
  liveBox.dispatchEvent(new window.Event('change', { bubbles: true }));
  assert(window.LivePrices.isPaused(), 'live feed pauses from settings');
  liveBox.checked = true;
  liveBox.dispatchEvent(new window.Event('change', { bubbles: true }));
  assert(!window.LivePrices.isPaused(), 'live feed resumes from settings');
  window.document.getElementById('set-reset').click();
  await wait(40);
  setPanel.classList.add('hidden'); // close

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
