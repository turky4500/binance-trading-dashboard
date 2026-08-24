/* Main dashboard logic: data loading, rendering, filters, modal, auto-refresh */
'use strict';

const state = {
  meta: null, market: null, opps: [], agent: null, perf: null, history: [], bt: null,
  bh: null, ul: null, symbols: [], engineCfg: null,
  filter: { q: '', dir: 'ALL', status: 'ALL', sort: 'score', high: false, watch: false },
  prefs: loadPrefs(),
  analyzer: { busy: false, result: null, frames4h: null },
  usingEmbedded: false, chartData: null,
};

function loadPrefs() {
  const d = { minScore: 0, maxCards: 20, live: true };
  try {
    const s = localStorage.getItem('dash-prefs');
    if (s) {
      const p = JSON.parse(s);
      d.minScore = isFinite(+p.minScore) ? Math.max(0, Math.min(100, +p.minScore)) : 0;
      d.maxCards = isFinite(+p.maxCards) ? Math.max(1, Math.min(50, +p.maxCards)) : 20;
      d.live = p.live !== false;
    }
  } catch (e) { /* defaults */ }
  return d;
}
function savePrefs() {
  try { localStorage.setItem('dash-prefs', JSON.stringify(state.prefs)); } catch (e) {}
}

/* ---------------- data loading ---------------- */
async function fetchJSON(path) {
  // cache-busting query param: always pulls the freshest file from the CDN
  const sep = path.includes('?') ? '&' : '?';
  const r = await fetch(`${path}${sep}v=${Date.now()}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function loadAll() {
  // Resilient loading: fetch each file independently; on partial failure keep
  // the last good data instead of blanking the page. Never falls back to the
  // embedded snapshot unless there is no data at all.
  const hadMeta = !!state.meta;
  const prevOpps = window.__dashDataSeeded ? state.opps.slice() : null;
  const [m, mk, o, qa, p, h, bt, bh, ul, syms, ecfg] = await Promise.allSettled([
    fetchJSON('data/meta.json'),
    fetchJSON('data/market.json'),
    fetchJSON('data/opportunities.json'),
    fetchJSON('data/agent_scan.json'),
    fetchJSON('data/performance.json'),
    fetchJSON('data/history.json'),
    fetchJSON('data/performance_backtest.json'),
    fetchJSON('data/breadth_history.json'),
    fetchJSON('data/update_log.json'),
    fetchJSON('data/symbols.json'),
    fetchJSON('data/config.json'),
  ]);
  if (m.status === 'fulfilled') {
    state.meta = m.value;
    state.usingEmbedded = false;
  } else if (!hadMeta && window.__EMBEDDED__) {
    const em = window.__EMBEDDED__;
    state.meta = em.meta; state.market = em.market; state.opps = em.opportunities;
    state.agent = em.agent_scan || null;
    state.perf = em.performance; state.history = em.history || [];
    state.bt = em.backtest || null;
    state.bh = em.breadth_history || null;
    state.ul = em.update_log || null;
    if (em.symbols && em.symbols.symbols) state.symbols = em.symbols.symbols;
    if (em.config) state.engineCfg = em.config;
    state.usingEmbedded = true;
  } else if (!hadMeta) {
    state.meta = null;
  }
  if (mk.status === 'fulfilled') state.market = mk.value;
  if (o.status === 'fulfilled') state.opps = o.value;
  if (qa.status === 'fulfilled') state.agent = qa.value;
  if (p.status === 'fulfilled') state.perf = p.value;
  if (h.status === 'fulfilled') state.history = h.value;
  if (bt.status === 'fulfilled') state.bt = bt.value;
  if (bh.status === 'fulfilled') state.bh = bh.value;
  if (ul.status === 'fulfilled') state.ul = ul.value;
  if (syms.status === 'fulfilled' && syms.value && syms.value.symbols) state.symbols = syms.value.symbols;
  if (ecfg.status === 'fulfilled' && ecfg.value) state.engineCfg = ecfg.value;
  // lifecycle alerts: diff vs previously displayed data (skipped on first seed)
  if (window.Alerts && prevOpps && prevOpps.length && o.status === 'fulfilled') {
    window.Alerts.diffEvents(prevOpps, state.opps).forEach(ev => window.Alerts.emit(ev));
  }
  window.__dashDataSeeded = true;
  renderAll();
  syncDirectionFilter();
  // (re)subscribe the live price feed to the currently displayed symbols
  if (window.LivePrices) {
    const agentSymbols = state.agent && Array.isArray(state.agent.signals) ? state.agent.signals.map(s => s.symbol) : [];
    LivePrices.subscribe(state.opps.map(o => o.symbol).concat(agentSymbols, window.Watchlist ? window.Watchlist.list() : []));
  }
}

function syncDirectionFilter() {
  // SHORT setups can be disabled by engine config (user trades spot only)
  const sel = document.getElementById('f-direction');
  if (!sel) return;
  const allow = !state.meta || !state.meta.config || state.meta.config.allow_shorts !== false;
  const opt = sel.querySelector('option[value="SHORT"]');
  if (!allow && opt) {
    sel.removeChild(opt);
    if (state.filter.dir === 'SHORT') { state.filter.dir = 'ALL'; sel.value = 'ALL'; }
  }
}

/* ---------------- helpers ---------------- */
function relTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return t('now');
  if (diff < 3600) return `${Math.floor(diff / 60)} ${t('mins_ago')}`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ${t('mins_ago')}`;
  return `${Math.floor(diff / 86400)}d ${t('mins_ago')}`;
}
function locTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? '—' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function statusKey(s) {
  const m = { WAITING_CONFIRMATION: 'waiting', TRIGGERED: 'triggered', TP1_HIT: 'tp', TP2_HIT: 'tp', TP3_HIT: 'tp', STOPPED: 'stopped', EXPIRED: 'expired', INVALIDATED: 'invalidated', READY: 'ready' };
  return m[s] || 'waiting';
}
function statusLabel(s) {
  const m = { READY: 'ready', WAITING_CONFIRMATION: 'waiting', TRIGGERED: 'triggered', TP1_HIT: 'tp1_hit', TP2_HIT: 'tp2_hit', TP3_HIT: 'tp3_hit', STOPPED: 'stopped', EXPIRED: 'expired', INVALIDATED: 'invalidated' };
  return t(m[s] || 'waiting');
}
function dirClass(d) { return d === 'LONG' ? 'pos' : 'neg'; }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function fmtPrice(p) {
  if (p == null) return '—';
  if (p >= 1000) return p.toLocaleString('en-US', { maximumFractionDigits: 1 });
  if (p >= 100) return p.toFixed(2);
  if (p >= 1) return p.toFixed(3);
  if (p >= 0.1) return p.toFixed(4);
  if (p >= 0.01) return p.toFixed(5);
  return p.toFixed(7);
}
function pct(x) { return (x >= 0 ? '+' : '') + Number(x).toFixed(2) + '%'; }
function scoreClass(s) { return s >= 90 ? 's90' : s >= 80 ? 's80' : s >= 70 ? 's70' : 's0'; }
function langText(o) { return LANG === 'ar' ? (o?.ar || o?.en) : (o?.en || o?.ar); }
/* freshness: distance from the (pipeline) current price to TP1 */
function distToTp1(o) {
  if (!o || o.tp1 == null || o.current_price == null || !(o.current_price > 0)) return null;
  const d = o.direction === 'LONG' ? (o.tp1 - o.current_price) : (o.current_price - o.tp1);
  return { pct: d / o.current_price * 100, reached: d <= 0 };
}
function fmtQty(q) {
  if (q >= 1000) return q.toLocaleString('en-US', { maximumFractionDigits: 2 });
  if (q >= 1) return q.toFixed(3).replace(/\.?0+$/, '');
  if (q >= 0.01) return q.toFixed(5).replace(/\.?0+$/, '');
  return q.toFixed(8).replace(/\.?0+$/, '');
}

/* recommendation time: 12-hour clock + date, in the user's local timezone */
function fmtRecTime(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  const loc = LANG === 'ar' ? 'ar-SA' : 'en-US';
  return d.toLocaleString(loc, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/* compact age: 3h / 12m / 2d */
function ageShort(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return t('now');
  if (diff < 3600) return Math.floor(diff / 60) + t('m_short');
  if (diff < 86400) return Math.floor(diff / 3600) + t('h_short');
  return Math.floor(diff / 86400) + t('d_short');
}

/* age bracket: fresh <6h, aging 6-18h, old >18h (within the 48h expiry window) */
function ageInfo(iso) {
  const ageH = (Date.now() - new Date(iso).getTime()) / 3600000;
  if (!isFinite(ageH)) return { cls: 'old', label: t('old'), ageH: 0 };
  const cls = ageH < 6 ? 'fresh' : ageH < 18 ? 'aging' : 'old';
  return { cls, label: t(cls), ageH };
}

/* remaining validity of the setup (engine auto-expires after expiry_hours) */
function validityInfo(iso) {
  const expiryH = (state.meta && state.meta.config && state.meta.config.expiry_hours) || 48;
  const remainH = expiryH - (Date.now() - new Date(iso).getTime()) / 3600000;
  const pct = Math.max(0, Math.min(100, remainH / expiryH * 100));
  const cls = pct > 60 ? 'fresh' : pct > 25 ? 'aging' : 'old';
  const label = remainH >= 1
    ? Math.round(remainH) + t('h_short')
    : Math.max(0, Math.round(remainH * 60)) + t('m_short');
  return { pct, cls, label };
}

/* Risk-based position sizing — deterministic, no leverage (spot only) */
function calcPosition(capital, riskPct, entry, sl, tps) {
  const cap = parseFloat(capital), rp = parseFloat(riskPct);
  if (!isFinite(cap) || !isFinite(rp) || cap <= 0 || rp <= 0 || rp > 100) return null;
  const risk = cap * rp / 100;
  const dist = Math.abs(entry - sl);
  if (!isFinite(dist) || dist <= 0) return null;
  const qty = risk / dist;
  const notional = qty * entry;
  const gains = (tps || []).map(tp => (tp - entry) * qty);
  const rrs = (tps || []).map(tp => Math.abs(tp - entry) / dist);
  return { risk, qty, notional, gains, rrs };
}
window.calcPosition = calcPosition;

async function loadChartData(symbol, tf) {
  try {
    return await fetchJSON(`data/klines/${symbol}_${tf}.json`);
  } catch (e) {
    const em = window.__EMBEDDED__;
    return em && em.klines ? (em.klines[`${symbol}_${tf}`] || null) : null;
  }
}

/* ---------------- header / status bar ---------------- */
function renderHeader() {
  const meta = state.meta;
  const el = (id, v) => { const n = document.getElementById(id); if (n) n.textContent = v; };
  if (meta) {
    el('last-update', locTime(meta.data_timestamp));
    el('data-age', relTime(meta.data_timestamp));
    el('source', (meta.source || '').replace('https://', ''));
    document.getElementById('footer-version').textContent = `v${meta.engine_version || '—'} · ${t('data_from')}: ${(meta.source || '').replace('https://', '')}`;
    updateNextStat(); // live countdown — refreshed every second by tick()
  } else {
    el('last-update', '—'); el('next-update', '—'); el('data-age', '—'); el('source', '—');
    document.getElementById('footer-version').textContent = t('no_data');
  }
  if (state.market) {
    const m = state.market;
    const ms = document.getElementById('market-status');
    ms.className = 'market-status ' + (m.status || 'NEUTRAL').toLowerCase();
    document.getElementById('market-status-text').textContent =
      m.status === 'BULLISH' ? t('market_bullish') : m.status === 'BEARISH' ? t('market_bearish') : t('market_neutral');
    document.getElementById('btc-price').textContent = m.btc ? fmtPrice(m.btc.price) : '—';
    document.getElementById('breadth').textContent = m.breadth_pct_above_ema50 != null ? m.breadth_pct_above_ema50 + '%' : '—';
  }
  // error / stale banner
  const banner = document.getElementById('error-banner');
  const btext = document.getElementById('error-banner-text');
  const bmeta = document.getElementById('error-meta');
  if (!state.meta) {
    banner.classList.remove('hidden');
    btext.textContent = state.usingEmbedded ? t('snapshot_note') : t('no_data');
    bmeta.textContent = '';
  } else {
    const staleMin = (state.meta.config && state.meta.config.stale_after_minutes) || 45;
    const ageMin = (Date.now() - new Date(state.meta.data_timestamp).getTime()) / 60000;
    if (ageMin > staleMin) {
      banner.classList.remove('hidden');
      btext.textContent = `${t('stale_warn')}: ${locTime(state.meta.data_timestamp)} (${relTime(state.meta.data_timestamp)})`;
      bmeta.textContent = state.meta.errors && state.meta.errors.length ? 'Last errors: ' + state.meta.errors.join(' | ') : '';
    } else {
      banner.classList.add('hidden');
    }
  }
}

/* ---------------- live badge / countdown / auto-refresh ---------------- */
const RING_C = 2 * Math.PI * 15.5; // circumference of the countdown ring (r=15.5)

function scheduleTarget() {
  // countdown target: retry timer first, then the pipeline's next update time
  if (window.__retryAt && window.__retryAt > Date.now()) return window.__retryAt;
  if (state.meta && state.meta.next_update_at) return new Date(state.meta.next_update_at).getTime();
  if (state.meta && state.meta.data_timestamp && state.meta.update_interval_minutes) {
    return new Date(state.meta.data_timestamp).getTime() + state.meta.update_interval_minutes * 60000;
  }
  return null;
}

function setLive(mode) {
  const el = document.getElementById('live-badge');
  if (!el) return;
  el.className = 'live-badge ' + mode;
  const txt = document.getElementById('live-text');
  if (txt) txt.textContent = mode === 'live' ? t('live') : mode === 'stale' ? t('stale') : t('sync');
}

function toast(msg, kind) {
  const box = document.getElementById('toasts');
  if (!box) return;
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || 'ok');
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .4s'; el.style.opacity = '0';
    setTimeout(() => el.remove(), 420);
  }, 4200);
}

async function refreshCycle() {
  // pulls the freshest data; re-renders everything when the pipeline published new data
  if (window.__refreshing) return;
  window.__refreshing = true;
  try {
    const meta = await fetchJSON('data/meta.json');
    if (!state.meta || meta.data_timestamp !== state.meta.data_timestamp) {
      window.__retryAt = 0;
      window.__failStreak = 0;
      await loadAll();
      if (state.meta && state.meta.data_timestamp === meta.data_timestamp) {
        toast(t('data_updated'), 'ok');
        const sb = document.querySelector('.statusbar');
        if (sb) { sb.classList.remove('flash'); void sb.offsetWidth; sb.classList.add('flash'); }
      }
    } else {
      // the pipeline hasn't published yet — retry shortly (silently)
      window.__failStreak = 0;
      window.__retryAt = Date.now() + 30000;
    }
  } catch (e) {
    window.__failStreak = (window.__failStreak || 0) + 1;
    if (window.__failStreak === 1) toast(t('offline'), 'warn'); // no toast spam on repeated failures
    window.__retryAt = Date.now() + (window.__failStreak > 4 ? 120000 : 60000);
  } finally {
    window.__refreshing = false;
  }
}

function updateNextStat() {
  // live "Next Update" stat: countdown while waiting, SYNC (yellow) when overdue
  const el = document.getElementById('next-update');
  if (!el) return;
  const target = scheduleTarget();
  if (!target) { el.textContent = '—'; el.style.color = ''; return; }
  const diff = target - Date.now();
  if (diff <= 0) {
    el.textContent = t('sync');
    el.style.color = 'var(--yellow)';
    return;
  }
  el.style.color = '';
  const mm = Math.floor(diff / 60000), ss = Math.floor((diff % 60000) / 1000);
  el.textContent = `${locTime(new Date(target).toISOString())} (${mm}:${String(ss).padStart(2, '0')})`;
}

function tick() {
  // runs every second: data age, live badge, countdown ring + trigger refresh at zero
  if (state.meta) document.getElementById('data-age').textContent = relTime(state.meta.data_timestamp);
  updateNextStat();
  // live freshness: recompute "distance to TP1" from the WebSocket price
  document.querySelectorAll('[data-tp1-dist]').forEach(el => {
    const sym = el.dataset.tp1Dist;
    const live = window.LivePrices ? window.LivePrices.currentPrice(sym) : null;
    if (live == null || !(live > 0)) return; // WS off — keep the pipeline value
    const tp1 = parseFloat(el.dataset.tp1);
    if (!isFinite(tp1)) return;
    const d = el.dataset.dir === 'LONG' ? tp1 - live : live - tp1;
    const pv = d / live * 100;
    el.textContent = d <= 0 ? '✓ ' + t('tp1_reached') : pv.toFixed(2) + '%';
    el.classList.toggle('pos', d <= 0);
    el.classList.toggle('mut', d > 0);
  });
  const staleMin = (state.meta && state.meta.config && state.meta.config.stale_after_minutes) || 45;
  const ageMs = state.meta ? Date.now() - new Date(state.meta.data_timestamp).getTime() : Infinity;
  if (!state.meta) setLive('stale');
  else if (window.__refreshing) setLive('updating');
  else if (ageMs > staleMin * 60000) setLive('stale');
  else setLive('live');

  const cd = document.getElementById('countdown');
  if (!cd) return;
  const txtEl = document.getElementById('countdown-text');
  const ringEl = document.getElementById('countdown-ring');
  const target = scheduleTarget();
  if (!target) {
    txtEl.textContent = '--:--';
    ringEl.style.strokeDashoffset = RING_C;
    if (!state.meta && !window.__retryAt) window.__retryAt = Date.now() + 60000;
    return;
  }
  const diff = target - Date.now();
  if (diff <= 0) {
    txtEl.textContent = t('sync');
    ringEl.style.strokeDashoffset = 0;
    cd.classList.add('done');
    refreshCycle();
    return;
  }
  cd.classList.remove('done');
  const mm = Math.floor(diff / 60000), ss = Math.floor((diff % 60000) / 1000);
  txtEl.textContent = `${mm}:${String(ss).padStart(2, '0')}`;
  const total = (state.meta && state.meta.update_interval_minutes) ? state.meta.update_interval_minutes * 60000 : 900000;
  const frac = Math.max(0, Math.min(1, diff / total));
  ringEl.style.strokeDashoffset = RING_C * (1 - frac);
}

/* ---------------- theme (dark / light) ---------------- */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('dash-theme', theme); } catch (e) {}
  updateChromeButtons();
  // redraw chart (if a modal is open) with theme colors
  if (state.chartData) {
    const c = document.getElementById('m-chart');
    if (c) drawChart(c, state.chartData.data, state.chartData.opp, state.chartData.opts);
  }
  // redraw the analyzer chart with theme colors
  if (state.analyzer.result && state.analyzer.frames4h) {
    const ac = document.getElementById('ana-chart');
    if (ac) renderAnalyzerChart();
  }
}

function renderAnalyzerChart() {
  const canvas = document.getElementById('ana-chart');
  const res = state.analyzer.result;
  if (!canvas || !res || !state.analyzer.frames4h || !window.Engine) return;
  const stp = (res.cfg && res.cfg.supertrend) || { period: 10, multiplier: 3.0 };
  const bars = state.analyzer.frames4h;
  const closes = bars.map(b => b.c);
  const st = window.Engine.supertrend(bars, stp.period, stp.multiplier);
  const chartData = {
    candles: bars.map(b => [b.t, b.o, b.h, b.l, b.c, b.v]),
    ema20: window.Engine.emaArr(closes, 20),
    ema50: window.Engine.emaArr(closes, 50),
    vwap: [],
    st_line: st.line, st_dir: st.dir,
  };
  const plan = res.best;
  const planLike = plan ? { entry_zone: plan.entry_zone, stop_loss: plan.stop_loss, tp1: plan.tp1, tp2: plan.tp2, tp3: plan.tp3 } : null;
  if (canvas.clientWidth > 0) drawChart(canvas, chartData, planLike, { volume: true, rsi: true });
}

function updateChromeButtons() {
  const tb = document.getElementById('theme-btn');
  if (tb) {
    const isLight = document.documentElement.dataset.theme === 'light';
    tb.textContent = (isLight ? '☀️ ' : '🌙 ') + t(isLight ? 'theme_light' : 'theme_dark');
    tb.title = t('theme_' + (isLight ? 'dark' : 'light'));
  }
}
window.updateChromeButtons = updateChromeButtons;

/* ---------------- cards ---------------- */
function filteredOpps() {
  let list = state.opps.slice();
  const f = state.filter;
  if (f.high) list = list.filter(o => o.score >= 85);
  if (f.q) {
    const q = f.q.toLowerCase();
    list = list.filter(o => o.symbol.toLowerCase().includes(q));
  }
  if (f.dir !== 'ALL') list = list.filter(o => o.direction === f.dir);
  if (f.status !== 'ALL') list = list.filter(o => o.status === f.status);
  if (f.watch) list = list.filter(o => window.Watchlist && window.Watchlist.has(o.symbol));
  const sorters = {
    score: (a, b) => b.score - a.score,
    rr: (a, b) => b.rr_tp2 - a.rr_tp2,
    profit: (a, b) => b.profit_pct_tp2 - a.profit_pct_tp2,
    volume: (a, b) => b.quote_volume_24h - a.quote_volume_24h,
    updated: (a, b) => new Date(b.updated_at) - new Date(a.updated_at),
  };
  list.sort(sorters[f.sort] || sorters.score);
  // display preferences: min score + max cards (client-side, local only)
  if (state.prefs.minScore > 0) list = list.filter(o => o.score >= state.prefs.minScore);
  list = list.slice(0, state.prefs.maxCards);
  return list;
}

function cardHTML(o, rank) {
  const d = o.direction;
  const dtp = distToTp1(o);
  const dtpLabel = dtp ? (dtp.reached ? '✓ ' + t('tp1_reached') : dtp.pct.toFixed(2) + '%') : '—';
  const dtpCls = dtp && dtp.reached ? 'pos' : 'mut';
  const st4 = o.analysis && o.analysis['4h'] && o.analysis['4h'].supertrend;
  const st1d = o.analysis && o.analysis['1d'] && o.analysis['1d'].supertrend;
  const stUp = st4 === 'UP' || st1d === 'UP';
  const stBadge = (st4 || st1d) ? `<span class="badge ${stUp ? 'st-up' : 'st-down'}" title="${esc(t('st_badge_tip'))}">ST ${st4 === 'UP' ? '↑' : st4 === 'DOWN' ? '↓' : '·'}4h ${st1d === 'UP' ? '↑' : st1d === 'DOWN' ? '↓' : '·'}1d</span>` : '';
  const ai = ageInfo(o.created_at);
  const canEnter = o.status === 'READY' || o.status === 'WAITING_CONFIRMATION';
  const val = validityInfo(o.created_at);
  const recBlock = `
    <div class="rec-time ${ai.cls}" title="${t('rec_time_tip')}">
      <span class="rt-label">⏱ ${t('rec_time')}</span>
      <span class="rt-value">${fmtRecTime(o.created_at)}</span>
      <span class="rt-age">${ai.label} · ${ageShort(o.created_at)}</span>
    </div>
    ${canEnter ? `<div class="validity">
      <div class="validity-bar"><div class="validity-fill ${val.cls}" style="width:${val.pct.toFixed(0)}%"></div></div>
      <span class="validity-label">${t('validity_left')} ${val.label}</span>
    </div>` : ''}`;
  return `
  <article class="card ${d.toLowerCase()}" data-sym="${esc(o.symbol)}">
    <div class="card-head">
      <div>
        <div class="rank">#${rank}</div>
        <div class="pair-name">${esc(o.pair)} <button class="star ${window.Watchlist && window.Watchlist.has(o.symbol) ? 'on' : ''}" data-star="${esc(o.symbol)}" title="${t('watchlist')}">${window.Watchlist && window.Watchlist.has(o.symbol) ? '★' : '☆'}</button></div>
      </div>
      <div class="score-badge"><span class="score-num ${scoreClass(o.score)}">${o.score}</span><span class="score-label">/100</span></div>
    </div>
    <div class="badge-row">
      <span class="badge dir-${d.toLowerCase()}">${d}</span>
      <span class="badge setup">${esc(o.setup_label)}</span>
      <span class="badge tf">${esc(o.primary_timeframe)}</span>
      <span class="badge status-${statusKey(o.status)}">${statusLabel(o.status)}</span>
      ${stBadge}
    </div>
    <div class="card-body">
      ${recBlock}
      <div class="kv"><span class="k">${t('entry_zone')}</span><span class="v">${fmtPrice(o.entry_zone[0])} – ${fmtPrice(o.entry_zone[1])}</span></div>
      <div class="kv"><span class="k">${t('stop')}</span><span class="v neg">${fmtPrice(o.stop_loss)}</span></div>
      <div class="kv"><span class="k">TP1</span><span class="v pos">${fmtPrice(o.tp1)}</span></div>
      <div class="kv"><span class="k">TP2</span><span class="v pos">${fmtPrice(o.tp2)}</span></div>
      <div class="kv"><span class="k">TP3</span><span class="v pos">${fmtPrice(o.tp3)}</span></div>
      <div class="kv"><span class="k">${t('rr')} (TP1/TP2)</span><span class="v mut">1:${o.rr_tp1} / 1:${o.rr_tp2}</span></div>
      <div class="kv"><span class="k">${t('profit_potential')}</span><span class="v pos">${pct(o.profit_pct_tp1)} / ${pct(o.profit_pct_tp2)} / ${pct(o.profit_pct_tp3)}</span></div>
      <div class="kv"><span class="k">${t('sl_distance')}</span><span class="v mut">${o.sl_distance_pct}%</span></div>
      <div class="kv"><span class="k">${t('to_tp1')}</span><span class="v ${dtpCls}" data-tp1-dist="${esc(o.symbol)}" data-tp1="${o.tp1}" data-dir="${d}">${dtpLabel}</span></div>
      <div class="kv"><span class="k">${t('live_price')}</span><span class="v"><span class="lp-dot"></span><span data-live-sym="${esc(o.symbol)}">${fmtPrice(o.current_price)}</span> <small data-live-chg="${esc(o.symbol)}" style="color:${o.change_24h >= 0 ? 'var(--green)' : 'var(--red)'}">${pct(o.change_24h)}</small></span></div>
    </div>
    <div class="card-foot">
      <span class="card-updated">${t('updated')}: ${relTime(o.updated_at)}</span>
      <button class="btn" data-open="${esc(o.id)}">${t('view_analysis')}</button>
    </div>
  </article>`;
}

function renderCards() {
  const grid = document.getElementById('opps-grid');
  const empty = document.getElementById('empty-state');
  const list = filteredOpps();
  document.getElementById('opp-count').textContent = state.opps.length;
  grid.innerHTML = list.map((o, i) => cardHTML(o, i + 1)).join('');
  empty.classList.toggle('hidden', list.length > 0);
  grid.querySelectorAll('[data-open]').forEach(b => {
    b.addEventListener('click', () => openModal(b.dataset.open));
  });
  grid.querySelectorAll('.star').forEach(s => {
    s.addEventListener('click', () => {
      const sym = s.dataset.star;
      const on = window.Watchlist ? window.Watchlist.toggle(sym) : false;
      s.classList.toggle('on', on);
      s.textContent = on ? '★' : '☆';
      renderWatchBar();
      if (state.filter.watch) renderCards();
    });
  });
}

/* ---------------- deterministic quantitative agent ---------------- */
function agentText(value) {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  return LANG === 'ar' ? (value.ar || value.en || '') : (value.en || value.ar || '');
}

function agentCardHTML(s, rank) {
  const risksObj = s.risk_notes || {};
  const risks = LANG === 'ar' ? (risksObj.ar || risksObj.en || []) : (risksObj.en || risksObj.ar || []);
  const riskItems = (Array.isArray(risks) ? risks : [risks]).filter(Boolean)
    .map(x => `<li>${esc(x)}</li>`).join('');
  const st = s.supertrend_status || {};
  return `<article class="card long agent-card">
    <div class="card-head">
      <div><div class="rank">#${rank} · QUANT</div><div class="pair-name">${esc(s.pair || s.symbol)}</div></div>
      <div class="score-badge"><span class="score-num ${scoreClass(s.score)}">${Number(s.score || 0)}</span><span class="score-label">/100</span></div>
    </div>
    <div class="badge-row">
      <span class="badge dir-long">LONG</span>
      <span class="badge setup">${esc(s.setup_label || 'SCALP_SUPERTREND')}</span>
      <span class="badge tf">15m</span>
      <span class="badge status-ready">${t('agent_ready')}</span>
      <span class="badge st-up">ST ${st['15m'] === 'UP' ? '↑' : '·'}15m ${st['1h'] === 'UP' ? '↑' : '·'}1h ${st['4h'] === 'UP' ? '↑' : '·'}4h</span>
    </div>
    <div class="card-body">
      <div class="kv"><span class="k">${t('live_price')}</span><span class="v"><span class="lp-dot"></span><span data-live-sym="${esc(s.symbol)}">${fmtPrice(s.current_price)}</span></span></div>
      <div class="kv"><span class="k">${t('entry_zone')}</span><span class="v">${fmtPrice(s.entry_zone && s.entry_zone[0])} – ${fmtPrice(s.entry_zone && s.entry_zone[1])}</span></div>
      <div class="kv"><span class="k">${t('stop')}</span><span class="v neg">${fmtPrice(s.stop_loss)} (${Number(s.sl_distance_pct || 0).toFixed(2)}%)</span></div>
      <div class="kv"><span class="k">TP1</span><span class="v pos">${fmtPrice(s.tp1)} (+${Number(s.profit_pct_tp1 || 0).toFixed(2)}%)</span></div>
      <div class="kv"><span class="k">TP2</span><span class="v pos">${fmtPrice(s.tp2)} (+${Number(s.profit_pct_tp2 || 0).toFixed(2)}%)</span></div>
      <div class="kv"><span class="k">TP3</span><span class="v pos">${fmtPrice(s.tp3)} (+${Number(s.profit_pct_tp3 || 0).toFixed(2)}%)</span></div>
      <div class="kv"><span class="k">${t('rr')}</span><span class="v mut">1:${Number(s.rr_tp1 || 0).toFixed(2)} / 1:${Number(s.rr_tp2 || 0).toFixed(2)} / 1:${Number(s.rr_tp3 || 0).toFixed(2)}</span></div>
      <div class="agent-copy"><b>${esc(t('agent_reason'))}</b><p>${esc(agentText(s.reason))}</p></div>
      ${riskItems ? `<div class="agent-copy risk"><b>${esc(t('agent_risks'))}</b><ul>${riskItems}</ul></div>` : ''}
      <div class="agent-copy"><b>${esc(t('agent_exec'))}</b><p>${esc(agentText(s.execution_note))}</p></div>
    </div>
    <div class="card-foot"><span class="card-updated">${t('updated')}: ${locTime(s.data_timestamp)}</span><span class="badge tf">${esc(s.decision || 'FAVORABLE')}</span></div>
  </article>`;
}

function renderQuantAgent() {
  const doc = state.agent;
  const grid = document.getElementById('agent-grid');
  const empty = document.getElementById('agent-empty');
  const count = document.getElementById('agent-count');
  const status = document.getElementById('agent-status');
  const summary = document.getElementById('agent-summary');
  if (!grid || !empty || !count || !status || !summary) return;
  const signals = doc && Array.isArray(doc.signals) ? doc.signals : [];
  count.textContent = signals.length;
  grid.innerHTML = signals.map((s, i) => agentCardHTML(s, i + 1)).join('');
  empty.classList.toggle('hidden', signals.length > 0);

  const sourceTime = doc && doc.source_data_timestamp;
  const stale = !!(sourceTime && state.meta && state.meta.data_timestamp && sourceTime !== state.meta.data_timestamp);
  status.className = 'agent-status ' + (!sourceTime || stale || (doc && doc.status === 'error') ? 'bad' : 'good');
  status.textContent = !sourceTime ? 'NO DATA' : (stale ? t('agent_data_stale') : String(doc.status || 'ok').toUpperCase());
  summary.innerHTML = `
    <div class="perf"><div class="k">${esc(t('agent_scanned'))}</div><div class="v">${Number(doc && doc.total_scanned || 0)}</div></div>
    <div class="perf"><div class="k">${esc(t('agent_signals'))}</div><div class="v good">${signals.length}</div></div>
    <div class="perf"><div class="k">${esc(t('agent_updated'))}</div><div class="v agent-time">${sourceTime ? esc(locTime(sourceTime)) : '—'}</div></div>`;

  const reasonEl = document.getElementById('agent-empty-reason');
  const noReason = doc && doc.no_opportunity_reason ? agentText(doc.no_opportunity_reason) : t('agent_no_signals_sub');
  if (reasonEl) reasonEl.textContent = noReason || t('agent_no_signals_sub');

  const details = document.getElementById('agent-rejections');
  const list = document.getElementById('agent-rejection-list');
  const rejections = doc && Array.isArray(doc.rejections) ? doc.rejections : [];
  if (details && list) {
    details.classList.toggle('hidden', rejections.length === 0);
    list.innerHTML = rejections.slice(0, 50).map(r => `<div class="agent-rejection-row"><b>${esc(r.symbol || '—')}</b><span>${esc((r.codes || []).join(', '))}</span><small>${esc(LANG === 'ar' ? r.reason_ar : r.reason_en)}</small></div>`).join('');
  }
}

function renderWatchBar() {
  const bar = document.getElementById('watch-bar');
  if (!bar) return;
  const list = window.Watchlist ? window.Watchlist.list() : [];
  if (!list.length) { bar.classList.add('hidden'); bar.innerHTML = ''; return; }
  bar.classList.remove('hidden');
  bar.innerHTML = `<span class="watch-title">⭐ ${t('watchlist')}</span>` + list.map(sym => {
    const opp = state.opps.find(o => o.symbol === sym);
    const chg = opp ? opp.change_24h : null;
    return `<button class="watch-chip ${opp ? '' : 'inactive'}" data-wsym="${esc(sym)}">
      <b>${esc(sym.replace('USDT', '/USDT'))}</b>
      <span class="v" data-live-sym="${esc(sym)}">${opp ? fmtPrice(opp.current_price) : '—'}</span>
      ${chg != null ? `<small data-live-chg="${esc(sym)}" style="color:${chg >= 0 ? 'var(--green)' : 'var(--red)'}">${pct(chg)}</small>` : ''}
    </button>`;
  }).join('');
  bar.querySelectorAll('.watch-chip').forEach(b => b.addEventListener('click', () => {
    const opp = state.opps.find(o => o.symbol === b.dataset.wsym);
    if (opp) openModal(opp.id);
    else openAnalyzerFor(b.dataset.wsym); // analyzed coins open the Coin Analyzer
  }));
}

/* ---------------- modal ---------------- */
async function openModal(id) {
  const o = state.opps.find(x => x.id === id);
  if (!o) return;
  const modal = document.getElementById('modal');
  modal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  document.getElementById('m-title').textContent = `${o.pair} — ${o.direction}`;
  document.getElementById('m-sub').innerHTML = `
    <span class="badge setup">${esc(o.setup_label)}</span>
    <span class="badge status-${statusKey(o.status)}">${statusLabel(o.status)}</span>
    <span class="badge tf">${esc(o.primary_timeframe)}</span>
    <span class="badge tf">${t('score')}: ${o.score}/100</span>`;
  const b = document.getElementById('m-body');
  const an = o.analysis || {};
  const tfRows = o.timeframes.map(tf => {
    const a = an[tf]; if (!a) return '';
    const tr = a.trend === 'Bullish' ? 'bull' : a.trend === 'Bearish' ? 'bear' : 'mixed';
    const emaTags = [['20', a.above_ema20], ['50', a.above_ema50]].map(([n, ab]) =>
      `<span class="tag ${ab ? 'bull' : 'bear'}">EMA${n} ${ab ? t('above') : t('below')}</span>`).join(' ');
    const stTag = a.supertrend
      ? `<span class="tag ${a.supertrend === 'UP' ? 'bull' : 'bear'}">${a.supertrend === 'UP' ? '▲' : '▼'} ${fmtPrice(a.supertrend_value)}</span>`
      : '—';
    return `<tr><td>${tf}</td><td><span class="tag ${tr}">${t(a.trend.toLowerCase())}</span></td>
      <td>${a.rsi}</td><td>${a.macd}</td><td>${emaTags}</td><td>${stTag}</td><td>${a.atr_pct}%</td><td>${a.vol_ratio}x</td></tr>`;
  }).join('');
  const lv = (arr, cls) => (arr || []).map(([p, name]) =>
    `<div class="level ${cls}"><b>${fmtPrice(p)}</b><span>${esc(name)}</span></div>`).join('');
  const dtpM = distToTp1(o);
  const dtpMLabel = dtpM ? (dtpM.reached ? '✓ ' + t('tp1_reached') : dtpM.pct.toFixed(2) + '%') : '—';
  const dtpMCls = dtpM && dtpM.reached ? 'pos' : 'mut';
  const sbs = Object.entries(o.score_breakdown || {}).map(([k, v]) => {
    const label = (o.score_breakdown_labels && o.score_breakdown_labels[k]) || k;
    return `<div class="sb-row"><span class="sb-label">${esc(label)}</span>
      <span class="sb-track"><span class="sb-fill" style="width:${Math.min(100, v)}%"></span></span>
      <span class="sb-val">${v}</span></div>`;
  }).join('');
  const evs = (o.events || []).map(e =>
    `<div class="tl"><time>${locTime(e.at)}</time><span class="t">${e.from ? statusLabel(e.from) : '—'} →</span><span class="to">${statusLabel(e.to)}</span>${e.note ? `<span class="t">(${esc(e.note)})</span>` : ''}</div>`).join('');
  b.innerHTML = `
    <div class="m-section">
      <h3>Overview</h3>
      <div class="m-grid">
        <div class="m-cell"><div class="k">${t('current_price')}</div><div class="v"><span class="lp-dot"></span><span data-live-sym="${esc(o.symbol)}">${fmtPrice(o.current_price)}</span></div></div>
        <div class="m-cell"><div class="k">${t('direction')}</div><div class="v ${dirClass(o.direction)}">${o.direction}</div></div>
        <div class="m-cell"><div class="k">${t('entry_zone')}</div><div class="v">${fmtPrice(o.entry_zone[0])} – ${fmtPrice(o.entry_zone[1])}</div></div>
        <div class="m-cell"><div class="k">${t('stop')}</div><div class="v" style="color:var(--red)">${fmtPrice(o.stop_loss)}</div></div>
        <div class="m-cell"><div class="k">TP1</div><div class="v pos">${fmtPrice(o.tp1)}</div></div>
        <div class="m-cell"><div class="k">TP2</div><div class="v pos">${fmtPrice(o.tp2)}</div></div>
        <div class="m-cell"><div class="k">TP3</div><div class="v pos">${fmtPrice(o.tp3)}</div></div>
        <div class="m-cell"><div class="k">${t('rr')} TP1/TP2/TP3</div><div class="v">1:${o.rr_tp1} / 1:${o.rr_tp2} / 1:${o.rr_tp3}</div></div>
        <div class="m-cell"><div class="k">${t('sl_distance')}</div><div class="v">${o.sl_distance_pct}%</div></div>
        <div class="m-cell"><div class="k">${t('to_tp1')}</div><div class="v ${dtpMCls}" data-tp1-dist="${esc(o.symbol)}" data-tp1="${o.tp1}" data-dir="${o.direction}">${dtpMLabel}</div></div>
        <div class="m-cell"><div class="k">${t('invalidation')}</div><div class="v" style="color:var(--purple)">${fmtPrice(o.invalidation_level)}</div></div>
        <div class="m-cell"><div class="k">${t('change_24h')}</div><div class="v ${dirClass(o.change_24h >= 0 ? 'LONG' : 'SHORT')}">${pct(o.change_24h)}</div></div>
        <div class="m-cell"><div class="k">${t('volume_24h')}</div><div class="v">$${(o.quote_volume_24h / 1e6).toFixed(1)}M</div></div>
        <div class="m-cell"><div class="k">${t('rec_time')}</div><div class="v" style="font-size:13px">${fmtRecTime(o.created_at)}</div></div>
        <div class="m-cell"><div class="k">${t('age_word')}</div><div class="v">${ageShort(o.created_at)}</div></div>
        ${(o.status === 'READY' || o.status === 'WAITING_CONFIRMATION') ? `<div class="m-cell"><div class="k">${t('validity_left')}</div><div class="v">${validityInfo(o.created_at).label}</div></div>` : ''}
        <div class="m-cell"><div class="k">${t('updated')}</div><div class="v" style="font-size:12px">${locTime(o.updated_at)}</div></div>
      </div>
    </div>
    <div class="m-section">
      <h3>${t('score_breakdown')} — ${o.score}/100 (${o.grade})</h3>
      <div class="scorebars">${sbs}</div>
    </div>
    <div class="m-section">
      <h3>${t('tf_analysis')}</h3>
      <div class="table-wrap"><table class="tf-table">
        <thead><tr><th>${t('tf')}</th><th>${t('trend')}</th><th>RSI</th><th>MACD</th><th>EMA</th><th>${t('st')}</th><th>${t('atr_pct')}</th><th>${t('vol')}</th></tr></thead>
        <tbody>${tfRows}</tbody></table></div>
      <p class="alert-note" style="margin-top:8px">${t('st_note')}</p>
    </div>
    <div class="m-section">
      <h3>${t('chart_title')}</h3>
      <div class="tf-switch">
        <button class="tf-btn active" data-tf="4h">4H</button>
        <button class="tf-btn" data-tf="1h">1H</button>
      </div>
      <div class="chart-wrap"><canvas id="m-chart" style="width:100%"></canvas></div>
    </div>
    <div class="m-section">
      <h3>${t('calculator_title')}</h3>
      <div class="calc-grid">
        <label class="calc-field">${t('capital')}<input type="number" id="calc-capital" min="1" step="100" value="1000"></label>
        <label class="calc-field">${t('risk_pct')}<input type="number" id="calc-risk" min="0.1" max="100" step="0.1" value="1"></label>
      </div>
      <div class="calc-results" id="calc-results"></div>
      <p class="calc-note">${t('calculator_note')}</p>
    </div>
    <div class="m-section">
      <h3>${t('support')} / ${t('resistance')}</h3>
      <div class="level-list">${lv(o.supports, 's')}${lv(o.resistances, 'r')}</div>
    </div>
    <div class="m-section">
      <h3>${t('volume_analysis')} & ${t('momentum')}</h3>
      <div class="note">${esc(langText(o.volume_note))}</div>
      <div class="note">${esc(langText(o.momentum_note))}</div>
    </div>
    <div class="m-section">
      <h3>${t('reason_entry')}</h3><div class="note">${esc(langText(o.reason))}</div>
      <h3>${t('reason_sl')}</h3><div class="note">${esc(langText(o.sl_reason))}</div>
      <h3>${t('reason_tp')}</h3><div class="note">${esc(langText(o.tp_reason))}</div>
      <h3>${t('reason_inv')}</h3><div class="note">${esc(langText(o.invalidation_reason))}</div>
    </div>
    <div class="m-section">
      <h3>${t('confirmation')}</h3><div class="note">${esc(o.confirmation || '—')}</div>
      <h3>${t('events')}</h3><div class="timeline">${evs}</div>
    </div>`;
  // load chart (default 4H) with volume + RSI panels
  const canvas = document.getElementById('m-chart');
  if (canvas) {
    const cd = await loadChartData(o.symbol, '4h');
    if (cd) {
      state.chartData = { symbol: o.symbol, tf: '4h', data: cd, opp: o, opts: { volume: true, rsi: true } };
      if (canvas.clientWidth > 0) drawChart(canvas, cd, o, state.chartData.opts);
    }
    document.querySelectorAll('.tf-btn').forEach(b => b.addEventListener('click', async () => {
      if (!state.chartData) return;
      document.querySelectorAll('.tf-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      const tf = b.dataset.tf;
      const nd = await loadChartData(state.chartData.symbol, tf);
      if (nd) {
        state.chartData.tf = tf; state.chartData.data = nd;
        const cv = document.getElementById('m-chart');
        if (cv) drawChart(cv, nd, state.chartData.opp, state.chartData.opts);
      }
    }));
  }
  bindCalculator(o);
}

function bindCalculator(o) {
  const capEl = document.getElementById('calc-capital');
  const riskEl = document.getElementById('calc-risk');
  if (!capEl || !riskEl) return;
  try {
    const saved = JSON.parse(localStorage.getItem('dash-calc') || '{}');
    if (saved.capital) capEl.value = saved.capital;
    if (saved.risk) riskEl.value = saved.risk;
  } catch (e) { /* defaults */ }
  const update = () => {
    const res = calcPosition(capEl.value, riskEl.value, o.entry_mid, o.stop_loss, [o.tp1, o.tp2, o.tp3]);
    try { localStorage.setItem('dash-calc', JSON.stringify({ capital: capEl.value, risk: riskEl.value })); } catch (e) {}
    const box = document.getElementById('calc-results');
    if (!box) return;
    if (!res) { box.innerHTML = '<div class="m-cell"><div class="k">—</div><div class="v">—</div></div>'; return; }
    const cell = (k, v, cls) => `<div class="m-cell"><div class="k">${k}</div><div class="v ${cls || ''}">${v}</div></div>`;
    box.innerHTML =
      cell(t('position_size'), fmtQty(res.qty)) +
      cell(t('notional'), '$' + res.notional.toLocaleString('en-US', { maximumFractionDigits: 0 })) +
      cell(t('max_loss'), '-$' + res.risk.toFixed(2), 'neg') +
      cell('TP1', '+$' + res.gains[0].toFixed(2) + ` <small class="mut">(${res.rrs[0].toFixed(1)}R)</small>`, 'pos') +
      cell('TP2', '+$' + res.gains[1].toFixed(2) + ` <small class="mut">(${res.rrs[1].toFixed(1)}R)</small>`, 'pos') +
      cell('TP3', '+$' + res.gains[2].toFixed(2) + ` <small class="mut">(${res.rrs[2].toFixed(1)}R)</small>`, 'pos');
  };
  capEl.addEventListener('input', update);
  riskEl.addEventListener('input', update);
  update();
}

function closeModal() {
  document.getElementById('modal').classList.add('hidden');
  document.body.style.overflow = '';
}

/* ---------------- performance ---------------- */
function renderPerformance() {
  const grid = document.getElementById('perf-grid');
  const p = state.perf;
  if (!p || p.total === 0) {
    grid.innerHTML = '';
    document.getElementById('history-empty').classList.remove('hidden');
  } else {
    document.getElementById('history-empty').classList.add('hidden');
    const cell = (k, v, cls) => `<div class="perf"><div class="k">${t(k)}</div><div class="v ${cls || ''}">${v == null ? '—' : v}${k === 'win_rate' || k === 'tp1_rate' || k === 'tp2_rate' || k === 'tp3_rate' ? '%' : ''}</div></div>`;
    grid.innerHTML =
      cell('total', p.total) + cell('successful', p.successful, 'good') + cell('stopped_n', p.stopped, 'bad') +
      cell('expired_n', p.expired) + cell('invalidated_n', p.invalidated) + cell('win_rate', p.win_rate, p.win_rate >= 50 ? 'good' : 'bad') +
      cell('tp1_rate', p.tp1_hit_rate, 'good') + cell('tp2_rate', p.tp2_hit_rate, 'good') + cell('tp3_rate', p.tp3_hit_rate, 'good') +
      cell('avg_score', p.avg_score) + cell('avg_rr', p.avg_rr_tp2) + cell('avg_hold', p.avg_hold_hours);
  }
  const tbody = document.getElementById('history-body');
  const hist = state.history.slice().sort((a, b) => new Date(b.closed_at) - new Date(a.closed_at)).slice(0, 100);
  const cls = { WIN: 'result-win', LOSS: 'result-loss', EXPIRED: 'result-exp', INVALIDATED: 'result-inv' };
  tbody.innerHTML = hist.map(h => `<tr>
    <td>${esc(h.pair)}</td><td class="${h.direction === 'LONG' ? 'result-win' : 'result-loss'}">${h.direction}</td>
    <td>${esc(h.setup_label || '')}</td><td>${fmtPrice(h.entry_mid)}</td>
    <td class="${cls[h.result] || ''}">${h.result}</td><td>${h.score}</td>
    <td>${h.hold_hours != null ? h.hold_hours + 'h' : '—'}</td><td>${locTime(h.closed_at)}</td></tr>`).join('');
  renderBacktest();
}

function renderBacktest() {
  const box = document.getElementById('bt-section');
  if (!box) return;
  const bt = state.bt;
  if (!bt) {
    box.innerHTML = `<div class="empty"><p>${t('no_backtest')}</p></div>`;
    return;
  }
  const st = bt.stats || {};
  const cell = (k, v, cls) => `<div class="perf"><div class="k">${t(k)}</div><div class="v ${cls || ''}">${v == null ? '—' : v}</div></div>`;
  const calRows = (bt.calibration || []).map(r => `<tr>
    <td>${r.band}</td><td>${r.count}</td><td>${r.decided}</td>
    <td class="${r.win_rate == null ? '' : r.win_rate >= 50 ? 'result-win' : 'result-loss'}">${r.win_rate == null ? '—' : r.win_rate + '%'}</td>
    <td>${r.tp1_rate == null ? '—' : r.tp1_rate + '%'}</td></tr>`).join('');
  const setupRows = (bt.setup_stats || []).map(r => `<tr>
    <td>${esc(r.label)}</td><td>${r.count}</td>
    <td class="${r.win_rate == null ? '' : r.win_rate >= 50 ? 'result-win' : 'result-loss'}">${r.win_rate == null ? '—' : r.win_rate + '%'}</td>
    <td>${r.avg_score}</td></tr>`).join('');
  const approx = (bt.approximations || []).map(a => `<li>${esc(a)}</li>`).join('');
  const total = st.total || 0;
  box.innerHTML = `
    <div class="bt-head">
      <h3>${t('bt_title')}</h3>
      <span class="bt-meta">${t('bt_months')}: ${bt.months} · ${total} ${t('signals_word')} · ${locTime(bt.updated_at)}</span>
    </div>
    <div class="bt-warn">⚠ ${t('bt_disclaimer')}</div>
    <div class="perf-grid">
      ${cell('win_rate', st.win_rate != null ? st.win_rate + '%' : '—', st.win_rate >= 50 ? 'good' : 'bad')}
      ${cell('tp1_rate', st.tp1_hit_rate != null ? st.tp1_hit_rate + '%' : '—', 'good')}
      ${cell('tp2_rate', st.tp2_hit_rate != null ? st.tp2_hit_rate + '%' : '—', 'good')}
      ${cell('avg_score', st.avg_score)}
      ${cell('avg_rr', st.avg_rr_tp2)}
      ${cell('avg_hold', st.avg_hold_hours)}
      ${cell('successful', st.successful, 'good')}
      ${cell('stopped_n', st.stopped, 'bad')}
    </div>
    <h4 class="bt-sub">${t('bt_calib')}</h4>
    <div class="table-wrap"><table>
      <thead><tr><th>${t('bt_band')}</th><th>${t('bt_signals')}</th><th>${t('bt_decided')}</th><th>${t('bt_win')}</th><th>${t('bt_tp1')}</th></tr></thead>
      <tbody>${calRows}</tbody></table></div>
    <h4 class="bt-sub">${t('bt_setups')}</h4>
    <div class="table-wrap"><table>
      <thead><tr><th>${t('setup')}</th><th>${t('bt_signals')}</th><th>${t('bt_win')}</th><th>${t('bt_avg_score')}</th></tr></thead>
      <tbody>${setupRows}</tbody></table></div>
    <details class="bt-approx"><summary>${t('bt_approx')}</summary><ul>${approx}</ul></details>`;
}

/* ---------------- market tab (breadth + pipeline health) ---------------- */
function renderMarketTab() {
  const stats = document.getElementById('market-stats');
  if (stats) {
    const m = state.market;
    stats.innerHTML = m ? `
      <div class="perf"><div class="k">${t('market_bullish')} / ${t('market_bearish')} / ${t('market_neutral')}</div>
        <div class="v" style="color:${m.status === 'BULLISH' ? 'var(--green)' : m.status === 'BEARISH' ? 'var(--red)' : 'var(--yellow)'}">${m.status}</div></div>
      <div class="perf"><div class="k">${t('breadth')}</div><div class="v">${m.breadth_pct_above_ema50}%</div></div>
      <div class="perf"><div class="k">${t('btc_price')}</div><div class="v">${m.btc ? fmtPrice(m.btc.price) : '—'}</div></div>
      <div class="perf"><div class="k">${t('coins_analyzed')}</div><div class="v">${m.coins_analyzed}</div></div>
      <div class="perf"><div class="k">${t('volume_24h')} (top30)</div><div class="v">$${m.top_quote_volume_24h}M</div></div>` : '';
  }
  const bh = state.bh;
  const bChart = document.getElementById('breadth-chart');
  const btcChart = document.getElementById('btc-chart');
  if (bh && bh.length >= 2) {
    document.getElementById('breadth-now').textContent = bh[bh.length - 1].breadth + '%';
    const labels = bh.map(x => ({ label: locTime(x.t) }));
    if (bChart) drawLineChart(bChart, {
      values: bh.map(x => x.breadth), labels, color: '#f0b90b',
      band: [35, 60], yFmt: v => v.toFixed(0) + '%',
    });
    const btcVals = bh.map(x => x.btc).filter(v => isFinite(v));
    if (btcChart && btcVals.length >= 2) {
      document.getElementById('btc-now').textContent = fmtPrice(btcVals[btcVals.length - 1]);
      drawLineChart(btcChart, {
        values: bh.map(x => x.btc).map(v => isFinite(v) ? v : null), labels, color: '#16c784',
        yFmt: v => fmtPrice(v),
      });
    }
  } else if (bChart) {
    document.getElementById('breadth-now').textContent = '—';
    document.getElementById('btc-now').textContent = '—';
  }
  // pipeline health (last 24h) from update log
  const ul = state.ul || [];
  const dayAgo = Date.now() - 24 * 3600 * 1000;
  const recent = ul.filter(x => new Date(x.t).getTime() >= dayAgo);
  let maxGap = 0;
  for (let i = 1; i < recent.length; i++) {
    maxGap = Math.max(maxGap, (new Date(recent[i].t) - new Date(recent[i - 1].t)) / 60000);
  }
  const durs = recent.filter(x => isFinite(x.duration)).map(x => x.duration);
  const avgDur = durs.length ? durs.reduce((a, b) => a + b, 0) / durs.length : null;
  const hg = document.getElementById('health-grid');
  if (hg) {
    const cell = (k, v, cls) => `<div class="perf"><div class="k">${k}</div><div class="v ${cls || ''}">${v}</div></div>`;
    hg.innerHTML =
      cell(t('cycles_24h'), recent.length, recent.length >= 10 ? 'good' : recent.length > 0 ? 'bad' : '') +
      cell(t('max_gap'), maxGap > 0 ? Math.round(maxGap) + ' min' : '—') +
      cell(t('avg_duration'), avgDur != null ? avgDur.toFixed(0) + ' s' : '—');
    const hs = document.getElementById('health-summary');
    if (hs) {
      hs.textContent = recent.length === 0 ? t('no_market_history')
        : maxGap > 90 ? '⚠ ' + Math.round(maxGap) + ' min' : 'OK';
      hs.style.color = maxGap > 90 ? 'var(--yellow)' : 'var(--green)';
    }
  }
  const log = document.getElementById('update-log');
  if (log) {
    const rows = ul.slice(-12).reverse().map(x => `<div class="ul-row">
      <time>${locTime(x.t)}</time><span class="ok">${x.ok ? '✓' : '✗'}</span>
      <span class="dur">${x.duration != null ? x.duration + 's' : ''}</span></div>`).join('');
    log.innerHTML = rows || `<p class="alert-note">${t('no_market_history')}</p>`;
  }
}

/* ---------------- about ---------------- */
function renderAbout() {
  const m = state.meta;
  const box = document.getElementById('about-stats');
  if (!m) { box.innerHTML = `<div class="m-cell"><div class="k">—</div><div class="v">${t('no_data')}</div></div>`; return; }
  const cell = (k, v) => `<div class="m-cell"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  box.innerHTML =
    cell(t('engine'), `v${m.engine_version}`) +
    cell(t('last_success'), locTime(m.data_timestamp)) +
    cell(t('data_from'), (m.source || '').replace('https://', '')) +
    cell(t('filters_min'), m.config.min_score_to_show) +
    cell(t('update_interval'), m.update_interval_minutes + ' min') +
    cell(t('max_opps'), m.config.max_opportunities);
}

/* ---------------- filters / events ---------------- */
function bindControls() {
  document.getElementById('search').addEventListener('input', e => { state.filter.q = e.target.value; renderCards(); });
  document.getElementById('f-direction').addEventListener('change', e => { state.filter.dir = e.target.value; renderCards(); });
  document.getElementById('f-status').addEventListener('change', e => { state.filter.status = e.target.value; renderCards(); });
  document.getElementById('f-sort').addEventListener('change', e => { state.filter.sort = e.target.value; renderCards(); });
  const chipHigh = document.getElementById('chip-high');
  chipHigh.addEventListener('click', () => { state.filter.high = !state.filter.high; chipHigh.classList.toggle('active', state.filter.high); renderCards(); });
  const chipWatch = document.getElementById('chip-watch');
  chipWatch.addEventListener('click', () => { state.filter.watch = !state.filter.watch; chipWatch.classList.toggle('active', state.filter.watch); renderCards(); });
  const chipReady = document.getElementById('chip-ready');
  chipReady.addEventListener('click', () => { state.filter.status = state.filter.status === 'READY' ? 'ALL' : 'READY'; document.getElementById('f-status').value = state.filter.status; chipReady.classList.toggle('active', state.filter.status === 'READY'); renderCards(); });
  const chipLong = document.getElementById('chip-long');
  chipLong.addEventListener('click', () => { state.filter.dir = state.filter.dir === 'LONG' ? 'ALL' : 'LONG'; document.getElementById('f-direction').value = state.filter.dir; chipLong.classList.toggle('active', state.filter.dir === 'LONG'); renderCards(); });
  document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById('tab-' + b.dataset.tab).classList.add('active');
    if (b.dataset.tab === 'market') renderMarketTab(); // canvases need visible width
  }));
  document.getElementById('m-close').addEventListener('click', closeModal);
  document.getElementById('modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
  document.getElementById('lang-btn').addEventListener('click', () => setLang(LANG === 'ar' ? 'en' : 'ar'));
  document.getElementById('theme-btn').addEventListener('click', () => {
    applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
  });
  bindSettingsPanel();
  bindAnalyzer();
}

function bindSettingsPanel() {
  const btn = document.getElementById('settings-btn');
  const panel = document.getElementById('settings-panel');
  if (!btn || !panel) return;
  const sync = () => {
    document.getElementById('set-theme').value = document.documentElement.dataset.theme || 'dark';
    document.getElementById('set-lang').value = LANG;
    document.getElementById('set-live').checked = state.prefs.live;
    document.getElementById('set-minscore').value = state.prefs.minScore;
    document.getElementById('set-maxcards').value = state.prefs.maxCards;
    const ap = window.Alerts ? window.Alerts.prefs() : null;
    if (ap) {
      document.getElementById('set-sound').checked = ap.sound;
      document.getElementById('set-notif').checked = ap.notif;
    }
  };
  btn.addEventListener('click', e => { e.stopPropagation(); panel.classList.toggle('hidden'); if (!panel.classList.contains('hidden')) sync(); });
  document.addEventListener('click', e => {
    if (!panel.classList.contains('hidden') && !panel.contains(e.target) && e.target !== btn) panel.classList.add('hidden');
  });
  document.getElementById('set-theme').addEventListener('change', e => applyTheme(e.target.value));
  document.getElementById('set-lang').addEventListener('change', e => setLang(e.target.value));
  document.getElementById('set-live').addEventListener('change', e => {
    state.prefs.live = e.target.checked; savePrefs();
    if (window.LivePrices) {
      if (state.prefs.live) window.LivePrices.resume();
      else window.LivePrices.pause();
    }
  });
  document.getElementById('set-sound').addEventListener('change', e => {
    if (window.Alerts) { const p = window.Alerts.prefs(); p.sound = e.target.checked; window.Alerts.setPrefs(p); }
  });
  document.getElementById('set-notif').addEventListener('change', e => {
    if (window.Alerts) { const p = window.Alerts.prefs(); p.notif = e.target.checked; window.Alerts.setPrefs(p); }
  });
  document.getElementById('set-minscore').addEventListener('change', e => {
    state.prefs.minScore = Math.max(0, Math.min(100, +e.target.value || 0)); savePrefs(); renderCards();
  });
  document.getElementById('set-maxcards').addEventListener('change', e => {
    state.prefs.maxCards = Math.max(1, Math.min(50, +e.target.value || 20)); savePrefs(); renderCards();
  });
  document.getElementById('set-reset').addEventListener('click', () => {
    try { localStorage.removeItem('dash-prefs'); } catch (e) {}
    state.prefs = loadPrefs();
    applyTheme('dark'); setLang('en');
    if (window.Alerts) window.Alerts.setPrefs({ sound: true, notif: true });
    sync(); renderCards();
    toast(t('reset_prefs'), 'ok');
  });
}

/* ================= Coin Analyzer (on-demand, in-browser engine) ================= */
const BINANCE_REST = 'https://data-api.binance.vision';

async function fetchBinance(path) {
  const r = await fetch(BINANCE_REST + path);
  if (!r.ok) throw new Error('binance ' + r.status);
  return r.json();
}

function normalizeSymbol(input) {
  let s = String(input || '').toUpperCase().replace(/\//g, '').trim();
  if (!s) return null;
  if (!s.endsWith('USDT') && !s.endsWith('BTC') && !s.endsWith('ETH') && !s.endsWith('BNB')) {
    s += 'USDT';
  }
  return s;
}

function renderSuggestions(query) {
  const box = document.getElementById('ana-suggest');
  if (!box) return;
  const q = String(query || '').toUpperCase().trim();
  if (!q || !state.symbols.length) { box.classList.add('hidden'); return; }
  const usdt = [], other = [];
  for (const s of state.symbols) {
    if (s.s.startsWith(q)) { (s.q === 'USDT' ? usdt : other).push(s); }
    else if (s.b.startsWith(q) && s.q === 'USDT') usdt.push(s);
  }
  const picks = usdt.slice(0, 8).concat(other.slice(0, 2));
  if (!picks.length) { box.classList.add('hidden'); return; }
  box.innerHTML = `<div class="ana-sugg-row" style="font-size:10.5px;color:var(--text3);cursor:default">${t('ana_sugg_note')}</div>` +
    picks.map(s => `<div class="ana-sugg-row" data-sym="${esc(s.s)}"><b>${esc(s.s)}</b><span>${esc(s.b)}</span></div>`).join('');
  box.classList.remove('hidden');
  box.querySelectorAll('.ana-sugg-row[data-sym]').forEach(r => r.addEventListener('click', () => {
    box.classList.add('hidden');
    document.getElementById('ana-input').value = r.dataset.sym;
    runAnalyzer(r.dataset.sym);
  }));
}

async function runAnalyzer(rawSymbol) {
  if (state.analyzer.busy) return;
  const sym = normalizeSymbol(rawSymbol);
  const status = document.getElementById('ana-status');
  const resultBox = document.getElementById('ana-result');
  if (!sym) { if (status) { status.classList.remove('hidden'); status.className = 'analyzer-note error'; status.textContent = t('ana_error_symbol'); } return; }
  if (!window.Engine) { if (status) { status.textContent = 'Engine unavailable'; status.className = 'analyzer-note error'; status.classList.remove('hidden'); } return; }
  state.analyzer.busy = true;
  document.getElementById('ana-run').disabled = true;
  status.classList.remove('hidden');
  status.className = 'analyzer-note';
  status.textContent = '⏳ ' + t('ana_fetching') + ' ' + sym;
  resultBox.innerHTML = '';
  try {
    const cfg = state.engineCfg || {
      min_score_to_show: 70, min_rr_tp1: 1.0, allow_shorts: false, disabled_setups: [],
      scoring: { trend_alignment: 20, structure: 15, support_resistance: 15, volume: 15, momentum: 10, entry_quality: 10, risk_reward: 10, liquidity: 5 },
      risk: { atr_sl_min: 0.8, atr_sl_max: 2.0, pullback_zone_atr: 0.6 },
      supertrend: { period: 10, multiplier: 3.0 },
    };
    const [k15, k1h, k4h, k1d, tick, book] = await Promise.all([
      fetchBinance(`/api/v3/klines?symbol=${sym}&interval=15m&limit=500`),
      fetchBinance(`/api/v3/klines?symbol=${sym}&interval=1h&limit=400`),
      fetchBinance(`/api/v3/klines?symbol=${sym}&interval=4h&limit=400`),
      fetchBinance(`/api/v3/klines?symbol=${sym}&interval=1d&limit=400`),
      fetchBinance(`/api/v3/ticker/24hr?symbol=${sym}`),
      fetchBinance(`/api/v3/ticker/bookTicker?symbol=${sym}`),
    ]);
    const last = parseFloat(tick.lastPrice);
    const meta24 = {
      quoteVol: parseFloat(tick.quoteVolume),
      spread: last > 0 ? (parseFloat(book.askPrice) - parseFloat(book.bidPrice)) / last * 100 : 0,
      trades: parseInt(tick.count, 10),
      chg24: parseFloat(tick.priceChangePercent),
      currentPrice: last, // freshness guard compares live price vs TP1
    };
    const res = window.Engine.analyze({ symbol: sym, klines: { '15m': k15, '1h': k1h, '4h': k4h, '1d': k1d }, meta24, cfg });
    if (res.error) throw new Error(res.error === 'insufficient_data' ? 'insufficient' : res.error);
    res.meta24 = meta24;
    res.cfg = cfg;
    state.analyzer.result = res;
    state.analyzer.frames4h = k4h.map(r => ({ t: +r[0], o: +r[1], h: +r[2], l: +r[3], c: +r[4], v: +r[5] }));
    status.classList.add('hidden');
    renderAnalyzer(res);
  } catch (e) {
    status.className = 'analyzer-note error';
    status.textContent = e.message === 'insufficient' ? t('ana_error_insufficient')
      : t('ana_error_network') + ' (' + e.message + ')';
    status.classList.remove('hidden');
  } finally {
    state.analyzer.busy = false;
    document.getElementById('ana-run').disabled = false;
  }
}

function renderAnalyzer(res) {
  const box = document.getElementById('ana-result');
  if (!box) return;
  const plan = res.best;
  const stp = (res.cfg && res.cfg.supertrend) || { period: 10, multiplier: 3.0 };
  const note = t('ana_live_note').replace('{time}', locTime(res.analyzed_at));
  const verdict = plan
    ? `<div class="ana-verdict has-setup">
        <div><div class="pair">${esc(res.symbol.replace('USDT', '/USDT'))} <span class="badge dir-${plan.direction.toLowerCase()}">${plan.direction}</span></div>
        <div class="badge-row" style="padding:8px 0 0">${`<span class="badge setup">${esc(plan.setup_label)}</span> <span class="badge status-${statusKey(plan.status)}">${statusLabel(plan.status)}</span>`}</div></div>
        <div class="score ${scoreClass(plan.score)}" style="color:var(--text)">${plan.score}<span style="font-size:14px;color:var(--text3)">/100 · ${plan.grade}</span></div>
      </div>`
    : `<div class="ana-verdict no-setup">
        <div><div class="pair">${esc(res.symbol.replace('USDT', '/USDT'))}</div>
        <div style="color:var(--yellow);font-weight:700;margin-top:6px">${t('ana_no_setup')}</div></div>
      </div>`;
  const tfTable = ['1d', '4h', '1h', '15m'].map(tf => {
    const s = res.tf[tf];
    const tr = s.above20 && s.e20_gt_e50 ? 'bull' : (!s.above20 && !s.e20_gt_e50 ? 'bear' : 'mixed');
    return `<tr><td>${tf}</td><td><span class="tag ${tr}">${t(s.above20 && s.e20_gt_e50 ? 'bullish' : (!s.above20 && !s.e20_gt_e50 ? 'bearish' : 'mixed'))}</span></td>
      <td>${s.rsi.toFixed(1)}</td><td>${s.macd_h >= 0 ? '+' : ''}${s.macd_h.toFixed(6)}</td>
      <td>${s.above20 ? '<span class="tag bull">✓</span>' : '<span class="tag bear">✗</span>'} ${s.above50 ? '<span class="tag bull">✓</span>' : '<span class="tag bear">✗</span>'}</td>
      <td><span class="tag ${s.st_dir === 1 ? 'bull' : 'bear'}">${s.st_dir === 1 ? '▲' : '▼'} ${fmtPrice(s.st_line)}</span></td>
      <td>${(s.atr / s.close * 100).toFixed(2)}%</td><td>${s.vol_ratio3.toFixed(2)}x</td>
      <td>${isFinite(s.vwap) ? fmtPrice(s.vwap) : '—'}</td></tr>`;
  }).join('');
  const diag = res.diagnostics.map(d =>
    `<div class="ana-diag-row ${d.ok ? 'ok' : 'no'}"><span class="mark">${d.ok ? '✓' : '✗'}</span><span>${esc(LANG === 'ar' ? d.ar : d.en)}</span></div>`).join('');
  const planGrid = plan ? `
    <h3 style="font-size:13px;color:var(--text3);text-transform:uppercase;letter-spacing:.1em;margin:16px 0 10px">${t('ana_plan')}</h3>
    <div class="ana-plan-grid">
      <div class="m-cell"><div class="k">${t('entry_zone')}</div><div class="v">${fmtPrice(plan.entry_zone[0])} – ${fmtPrice(plan.entry_zone[1])}</div></div>
      <div class="m-cell"><div class="k">${t('stop')}</div><div class="v neg">${fmtPrice(plan.stop_loss)}</div></div>
      <div class="m-cell"><div class="k">TP1</div><div class="v pos">${fmtPrice(plan.tp1)}</div></div>
      <div class="m-cell"><div class="k">TP2</div><div class="v pos">${fmtPrice(plan.tp2)}</div></div>
      <div class="m-cell"><div class="k">TP3</div><div class="v pos">${fmtPrice(plan.tp3)}</div></div>
      <div class="m-cell"><div class="k">${t('rr')} TP1/TP2/TP3</div><div class="v">1:${plan.rr_tp1} / 1:${plan.rr_tp2} / 1:${plan.rr_tp3}</div></div>
      <div class="m-cell"><div class="k">${t('sl_distance')}</div><div class="v">${plan.sl_distance_pct}%</div></div>
      <div class="m-cell"><div class="k">${t('invalidation')}</div><div class="v" style="color:var(--purple)">${fmtPrice(plan.invalidation_level)}</div></div>
    </div>
    <div class="m-section"><h3>${t('score_breakdown')}</h3><div class="scorebars">${Object.entries(plan.score_breakdown).map(([k, v]) => `<div class="sb-row"><span class="sb-label">${esc(k.replace(/_/g, ' '))}</span><span class="sb-track"><span class="sb-fill" style="width:${Math.min(100, v)}%"></span></span><span class="sb-val">${v}</span></div>`).join('')}</div></div>` : '';
  const chartBlock = `<div class="m-section ana-chart"><h3>${t('chart_title')}</h3><div class="chart-wrap"><canvas id="ana-chart" style="width:100%"></canvas></div></div>`;
  const wOn = window.Watchlist && window.Watchlist.has(res.symbol);
  box.innerHTML = `
    <div class="analyzer-note" style="background:var(--blue-bg);border-inline-start-color:var(--blue)">${esc(note)}</div>
    ${verdict}
    <div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn small" id="ana-watch">${wOn ? '★' : '⭐'} ${t(wOn ? 'ana_watch_on' : 'ana_watch_add')}</button>
    </div>
    <div class="m-section"><h3>${t('ana_indicators')}</h3>
      <div class="table-wrap"><table class="tf-table">
        <thead><tr><th>${t('tf')}</th><th>${t('trend')}</th><th>RSI</th><th>MACD</th><th>EMA20/50</th><th>${t('st')}</th><th>${t('atr_pct')}</th><th>${t('vol')}</th><th>VWAP</th></tr></thead>
        <tbody>${tfTable}</tbody></table></div>
      <p class="alert-note" style="margin-top:8px">${t('st_note')}</p></div>
    ${planGrid}
    <div class="m-section"><h3>${t('ana_why')}</h3><div class="ana-diag">${diag}</div></div>
    <div class="m-section"><h3>${t('support')} / ${t('resistance')}</h3><div class="level-list">${plan ? (plan.supports || []).map(([p, n]) => `<div class="level s"><b>${fmtPrice(p)}</b><span>${esc(n)}</span></div>`).join('') + (plan.resistances || []).map(([p, n]) => `<div class="level r"><b>${fmtPrice(p)}</b><span>${esc(n)}</span></div>`).join('') : '<span class="alert-note">—</span>'}</div></div>
    ${chartBlock}`;
  document.getElementById('ana-watch').addEventListener('click', () => {
    if (!window.Watchlist) return;
    window.Watchlist.toggle(res.symbol);
    renderAnalyzer(res);
    renderWatchBar();
    if (window.LivePrices) {
      const agentSymbols = state.agent && Array.isArray(state.agent.signals) ? state.agent.signals.map(s => s.symbol) : [];
      window.LivePrices.subscribe(state.opps.map(o => o.symbol).concat(agentSymbols, window.Watchlist.list()));
    }
  });
  // chart (4H with volume + RSI panels and plan levels)
  renderAnalyzerChart();
}

function openAnalyzerFor(symbol) {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(x => x.classList.remove('active'));
  document.querySelector('[data-tab="analyzer"]').classList.add('active');
  document.getElementById('tab-analyzer').classList.add('active');
  document.getElementById('ana-input').value = symbol;
  runAnalyzer(symbol);
}

function bindAnalyzer() {
  const input = document.getElementById('ana-input');
  const btn = document.getElementById('ana-run');
  if (!input || !btn) return;
  input.addEventListener('input', () => renderSuggestions(input.value));
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { document.getElementById('ana-suggest').classList.add('hidden'); runAnalyzer(input.value); } });
  input.addEventListener('blur', () => setTimeout(() => document.getElementById('ana-suggest').classList.add('hidden'), 180));
  btn.addEventListener('click', () => { document.getElementById('ana-suggest').classList.add('hidden'); runAnalyzer(input.value); });
}

/* ---------------- render all ---------------- */
window.renderAll = function () {
  renderHeader();
  renderCards();
  renderQuantAgent();
  renderWatchBar();
  renderMarketTab();
  renderPerformance();
  renderAbout();
};

/* ---------------- boot ---------------- */
/* explicit exports for cross-module use (alerts, live prices) */
window.toast = toast;
window.fmtPrice = fmtPrice;
window.fmtRecTime = fmtRecTime;
window.ageShort = ageShort;
window.ageInfo = ageInfo;
window.validityInfo = validityInfo;

async function init() {
  if (window.__dashInit) return; // guard against double initialization
  window.__dashInit = true;
  applyTheme((function () { try { return localStorage.getItem('dash-theme') || 'dark'; } catch (e) { return 'dark'; } })());
  bindControls();
  if (window.Alerts) window.Alerts.initPanel();
  setLang(LANG); // applies i18n + triggers first renderAll
  await loadAll();
  if (window.LivePrices && !state.prefs.live) window.LivePrices.pause();
  // PWA: register the service worker (http/https only; harmless elsewhere)
  try {
    if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
      navigator.serviceWorker.register('sw.js').catch(() => {});
    }
  } catch (e) { /* unsupported context */ }
  // tick every second: data age, live badge, countdown ring, auto-refresh at zero
  setInterval(tick, 1000);
  // safety net: background check every minute (silent unless new data arrives)
  setInterval(() => refreshCycle(), 60000);
}
document.addEventListener('DOMContentLoaded', init);
if (document.readyState !== 'loading') init(); // script loaded after DOM is ready
