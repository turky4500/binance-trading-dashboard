/* Main dashboard logic: data loading, rendering, filters, modal, auto-refresh */
'use strict';

const state = {
  meta: null, market: null, opps: [], perf: null, history: [],
  filter: { q: '', dir: 'ALL', status: 'ALL', sort: 'score', high: false },
  usingEmbedded: false,
};

/* ---------------- data loading ---------------- */
async function fetchJSON(path) {
  const r = await fetch(path, { cache: 'no-store' });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function loadAll() {
  try {
    const [meta, market, opps, perf, history] = await Promise.all([
      fetchJSON('data/meta.json'),
      fetchJSON('data/market.json'),
      fetchJSON('data/opportunities.json'),
      fetchJSON('data/performance.json').catch(() => null),
      fetchJSON('data/history.json').catch(() => []),
    ]);
    state.meta = meta; state.market = market; state.opps = opps;
    state.perf = perf; state.history = history;
    state.usingEmbedded = false;
  } catch (e) {
    if (window.__EMBEDDED__) {
      const em = window.__EMBEDDED__;
      state.meta = em.meta; state.market = em.market; state.opps = em.opportunities;
      state.perf = em.performance; state.history = em.history || [];
      state.usingEmbedded = true;
    } else {
      state.meta = null; state.opps = []; state.market = null; state.perf = null; state.history = [];
    }
  }
  renderAll();
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

/* ---------------- header / status bar ---------------- */
function renderHeader() {
  const meta = state.meta;
  const el = (id, v) => { const n = document.getElementById(id); if (n) n.textContent = v; };
  if (meta) {
    el('last-update', locTime(meta.data_timestamp));
    if (meta.next_update_at) {
      el('next-update', locTime(meta.next_update_at));
    } else {
      el('next-update', '—');
    }
    el('data-age', relTime(meta.data_timestamp));
    el('source', (meta.source || '').replace('https://', ''));
    document.getElementById('footer-version').textContent = `v${meta.engine_version || '—'} · ${t('data_from')}: ${(meta.source || '').replace('https://', '')}`;
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
  // countdown
  window._nextAt = state.meta && state.meta.next_update_at ? new Date(state.meta.next_update_at).getTime() : null;
  if (state.meta && !state.meta.next_update_at && state.meta.update_interval_minutes) {
    window._nextAt = new Date(state.meta.data_timestamp).getTime() + state.meta.update_interval_minutes * 60000;
  }
  tickCountdown();
}

function tickCountdown() {
  const el = document.getElementById('next-update');
  if (!el || !window._nextAt) return;
  const diff = (window._nextAt - Date.now()) / 1000;
  if (diff <= 0) {
    el.textContent = t('now');
    return;
  }
  const mm = Math.floor(diff / 60), ss = Math.floor(diff % 60);
  el.textContent = `${mm}:${String(ss).padStart(2, '0')}`;
}

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
  const sorters = {
    score: (a, b) => b.score - a.score,
    rr: (a, b) => b.rr_tp2 - a.rr_tp2,
    profit: (a, b) => b.profit_pct_tp2 - a.profit_pct_tp2,
    volume: (a, b) => b.quote_volume_24h - a.quote_volume_24h,
    updated: (a, b) => new Date(b.updated_at) - new Date(a.updated_at),
  };
  list.sort(sorters[f.sort] || sorters.score);
  return list;
}

function cardHTML(o, rank) {
  const d = o.direction;
  return `
  <article class="card ${d.toLowerCase()}" data-sym="${esc(o.symbol)}">
    <div class="card-head">
      <div>
        <div class="rank">#${rank}</div>
        <div class="pair-name">${esc(o.pair)}</div>
      </div>
      <div class="score-badge"><span class="score-num ${scoreClass(o.score)}">${o.score}</span><span class="score-label">/100</span></div>
    </div>
    <div class="badge-row">
      <span class="badge dir-${d.toLowerCase()}">${d}</span>
      <span class="badge setup">${esc(o.setup_label)}</span>
      <span class="badge tf">${esc(o.primary_timeframe)}</span>
      <span class="badge status-${statusKey(o.status)}">${statusLabel(o.status)}</span>
    </div>
    <div class="card-body">
      <div class="kv"><span class="k">${t('entry_zone')}</span><span class="v">${fmtPrice(o.entry_zone[0])} – ${fmtPrice(o.entry_zone[1])}</span></div>
      <div class="kv"><span class="k">${t('stop')}</span><span class="v neg">${fmtPrice(o.stop_loss)}</span></div>
      <div class="kv"><span class="k">TP1</span><span class="v pos">${fmtPrice(o.tp1)}</span></div>
      <div class="kv"><span class="k">TP2</span><span class="v pos">${fmtPrice(o.tp2)}</span></div>
      <div class="kv"><span class="k">TP3</span><span class="v pos">${fmtPrice(o.tp3)}</span></div>
      <div class="kv"><span class="k">${t('rr')} (TP1/TP2)</span><span class="v mut">1:${o.rr_tp1} / 1:${o.rr_tp2}</span></div>
      <div class="kv"><span class="k">${t('profit_potential')}</span><span class="v pos">${pct(o.profit_pct_tp1)} / ${pct(o.profit_pct_tp2)} / ${pct(o.profit_pct_tp3)}</span></div>
      <div class="kv"><span class="k">${t('sl_distance')}</span><span class="v mut">${o.sl_distance_pct}%</span></div>
      <div class="kv"><span class="k">${t('current_price')}</span><span class="v">${fmtPrice(o.current_price)} <small style="color:${o.change_24h >= 0 ? '#16c784' : '#ea3943'}">${pct(o.change_24h)}</small></span></div>
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
    return `<tr><td>${tf}</td><td><span class="tag ${tr}">${t(a.trend.toLowerCase())}</span></td>
      <td>${a.rsi}</td><td>${a.macd}</td><td>${emaTags}</td><td>${a.atr_pct}%</td><td>${a.vol_ratio}x</td></tr>`;
  }).join('');
  const lv = (arr, cls) => (arr || []).map(([p, name]) =>
    `<div class="level ${cls}"><b>${fmtPrice(p)}</b><span>${esc(name)}</span></div>`).join('');
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
        <div class="m-cell"><div class="k">${t('current_price')}</div><div class="v">${fmtPrice(o.current_price)}</div></div>
        <div class="m-cell"><div class="k">${t('direction')}</div><div class="v ${dirClass(o.direction)}">${o.direction}</div></div>
        <div class="m-cell"><div class="k">${t('entry_zone')}</div><div class="v">${fmtPrice(o.entry_zone[0])} – ${fmtPrice(o.entry_zone[1])}</div></div>
        <div class="m-cell"><div class="k">${t('stop')}</div><div class="v" style="color:var(--red)">${fmtPrice(o.stop_loss)}</div></div>
        <div class="m-cell"><div class="k">TP1</div><div class="v pos">${fmtPrice(o.tp1)}</div></div>
        <div class="m-cell"><div class="k">TP2</div><div class="v pos">${fmtPrice(o.tp2)}</div></div>
        <div class="m-cell"><div class="k">TP3</div><div class="v pos">${fmtPrice(o.tp3)}</div></div>
        <div class="m-cell"><div class="k">${t('rr')} TP1/TP2/TP3</div><div class="v">1:${o.rr_tp1} / 1:${o.rr_tp2} / 1:${o.rr_tp3}</div></div>
        <div class="m-cell"><div class="k">${t('sl_distance')}</div><div class="v">${o.sl_distance_pct}%</div></div>
        <div class="m-cell"><div class="k">${t('invalidation')}</div><div class="v" style="color:var(--purple)">${fmtPrice(o.invalidation_level)}</div></div>
        <div class="m-cell"><div class="k">${t('change_24h')}</div><div class="v ${dirClass(o.change_24h >= 0 ? 'LONG' : 'SHORT')}">${pct(o.change_24h)}</div></div>
        <div class="m-cell"><div class="k">${t('volume_24h')}</div><div class="v">$${(o.quote_volume_24h / 1e6).toFixed(1)}M</div></div>
        <div class="m-cell"><div class="k">${t('created')}</div><div class="v" style="font-size:12px">${locTime(o.created_at)}</div></div>
      </div>
    </div>
    <div class="m-section">
      <h3>${t('score_breakdown')} — ${o.score}/100 (${o.grade})</h3>
      <div class="scorebars">${sbs}</div>
    </div>
    <div class="m-section">
      <h3>${t('tf_analysis')}</h3>
      <div class="table-wrap"><table class="tf-table">
        <thead><tr><th>${t('tf')}</th><th>${t('trend')}</th><th>RSI</th><th>MACD</th><th>EMA</th><th>${t('atr_pct')}</th><th>${t('vol')}</th></tr></thead>
        <tbody>${tfRows}</tbody></table></div>
    </div>
    <div class="m-section">
      <h3>${t('chart_title')}</h3>
      <div class="chart-wrap"><canvas id="m-chart" style="width:100%"></canvas></div>
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
  // load chart (network first, embedded snapshot as fallback)
  let cd = null;
  try {
    cd = await fetchJSON(`data/klines/${o.symbol}_4h.json`);
  } catch (e) {
    const em = window.__EMBEDDED__;
    cd = em && em.klines ? (em.klines[`${o.symbol}_4h`] || null) : null;
  }
  if (cd) {
    const canvas = document.getElementById('m-chart');
    if (canvas && canvas.clientWidth > 0) drawChart(canvas, cd, o);
  }
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
  const chipReady = document.getElementById('chip-ready');
  chipReady.addEventListener('click', () => { state.filter.status = state.filter.status === 'READY' ? 'ALL' : 'READY'; document.getElementById('f-status').value = state.filter.status; chipReady.classList.toggle('active', state.filter.status === 'READY'); renderCards(); });
  const chipLong = document.getElementById('chip-long');
  chipLong.addEventListener('click', () => { state.filter.dir = state.filter.dir === 'LONG' ? 'ALL' : 'LONG'; document.getElementById('f-direction').value = state.filter.dir; chipLong.classList.toggle('active', state.filter.dir === 'LONG'); renderCards(); });
  document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById('tab-' + b.dataset.tab).classList.add('active');
  }));
  document.getElementById('m-close').addEventListener('click', closeModal);
  document.getElementById('modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
  document.getElementById('lang-btn').addEventListener('click', () => setLang(LANG === 'ar' ? 'en' : 'ar'));
}

/* ---------------- render all ---------------- */
window.renderAll = function () {
  renderHeader();
  renderCards();
  renderPerformance();
  renderAbout();
};

/* ---------------- boot ---------------- */
async function init() {
  if (window.__dashInit) return; // guard against double initialization
  window.__dashInit = true;
  bindControls();
  setLang(LANG); // applies i18n + triggers first renderAll
  await loadAll();
  setInterval(async () => {
    // cheap refresh of data age + countdown
    renderHeader();
    if (!state.usingEmbedded && document.getElementById('tab-opportunities').classList.contains('active')) {
      try {
        const meta = await fetchJSON('data/meta.json');
        if (!state.meta || meta.data_timestamp !== state.meta.data_timestamp) await loadAll();
      } catch (e) { /* transient network error: keep showing last known state */ }
    }
  }, 60000);
  setInterval(tickCountdown, 1000);
}
document.addEventListener('DOMContentLoaded', init);
if (document.readyState !== 'loading') init(); // script loaded after DOM is ready
