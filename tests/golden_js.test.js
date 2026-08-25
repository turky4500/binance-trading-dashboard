/* Golden parity test: JavaScript Coin-Analyzer engine vs Python reference.
   Run: node tests/golden_js.test.js
   Fetches the same historical bars the Python engine used (historical bars are
   immutable), computes with the JS mirror, and asserts parity within tight
   tolerances. Requires network (available in CI and locally). */
'use strict';
const fs = require('fs');
const path = require('path');
const Engine = require('../frontend/js/engine.js');

const ROOT = path.resolve(__dirname, '..');
const ref = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'golden_reference.json'), 'utf-8'));
const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'config', 'settings.json'), 'utf-8'));

const BASE = 'https://data-api.binance.vision';
let failures = 0;
const assert = (cond, msg) => {
  if (!cond) { console.error('FAIL:', msg); failures++; }
  else console.log('PASS:', msg);
};
const near = (a, b, tolAbs, tolRel, msg) => {
  const ok = Math.abs(a - b) <= Math.max(tolAbs, Math.abs(b) * tolRel);
  if (!ok) console.error(`FAIL: ${msg}: js=${a} py=${b} (Δ=${(a - b).toFixed(6)})`);
  else console.log(`PASS: ${msg} (js=${a} py=${b})`);
  if (!ok) failures++;
  return ok;
};

async function klines(symbol, interval, limit) {
  const url = `${BASE}/api/v3/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`fetch ${url}: ${r.status}`);
  return r.json();
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const stp = cfg.supertrend || { period: 10, multiplier: 3.0 };
  for (const symbol of Object.keys(ref.symbols)) {
    const entry = ref.symbols[symbol];
    console.log(`\n=== ${symbol} ===`);
    const frames = {};
    for (const tf of ['15m', '1h', '4h', '1d']) {
      const t = entry.tfs[tf];
      const raw = await klines(symbol, tf, t.limit);
      const bars = raw
        .filter(r => r[0] <= t.last_ts)
        .map(r => ({ t: r[0], o: +r[1], h: +r[2], l: +r[3], c: +r[4], v: +r[5] }));
      if (bars.length < 200) {
        assert(false, `${symbol} ${tf}: only ${bars.length} bars after truncation`);
        continue;
      }
      const k = (tf === '15m' || tf === '1h') ? 2 : 3;
      const st = Engine.tfState(bars, k, stp);
      const py = t.state;
      near(st.close, py.close, 1e-6, 0.00001, `${tf} close`);
      near(st.ema20, py.ema20, 1e-9, 0.002, `${tf} ema20`);
      near(st.ema50, py.ema50, 1e-9, 0.004, `${tf} ema50`);
      near(st.ema200, py.ema200, 1e-9, 0.008, `${tf} ema200`);
      near(st.rsi, py.rsi, 0.8, 0.01, `${tf} rsi`);
      near(st.macd_h, py.macd_h, 1e-9, 0.004, `${tf} macd_h`);
      near(st.atr, py.atr, 1e-9, 0.008, `${tf} atr`);
      if (py.vwap != null && isFinite(st.vwap)) near(st.vwap, py.vwap, 1e-9, 0.003, `${tf} vwap`);
      near(st.vol_ratio3, py.vol_ratio3, 0.04, 0.05, `${tf} vol_ratio3`);
      // above flags: tolerate disagreement when close is within 0.05% of an EMA
      // (tiny float differences in EMA can flip the boolean at the boundary)
      const closeApproxEmas = [Math.abs(st.close - st.ema20) / st.close < 0.0005,
        Math.abs(st.close - st.ema50) / st.close < 0.0005,
        Math.abs(st.close - st.ema200) / st.close < 0.0005];
      const aboveOk = (st.above20 === py.above20 || closeApproxEmas[0]) &&
        (st.above50 === py.above50 || closeApproxEmas[1]) &&
        (st.above200 === py.above200 || closeApproxEmas[2]);
      assert(aboveOk, `${tf} above flags`);
      assert(st.e20_gt_e50 === py.e20_gt_e50, `${tf} e20>e50 flag`);
      near(st.last_high, py.last_high, 1e-9, 0.004, `${tf} last_high`);
      near(st.last_low, py.last_low, 1e-9, 0.004, `${tf} last_low`);
      near(st.hi6, py.hi6, 1e-9, 0.0001, `${tf} hi6`);
      near(st.lo6, py.lo6, 1e-9, 0.0001, `${tf} lo6`);
      near(st.hi20, py.hi20, 1e-9, 0.0001, `${tf} hi20`);
      near(st.lo20, py.lo20, 1e-9, 0.0001, `${tf} lo20`);
      near(st.st_line, py.st_line, 1e-9, 0.006, `${tf} supertrend line`);
      assert(st.st_dir === py.st_dir, `${tf} supertrend dir (js=${st.st_dir} py=${py.st_dir})`);
      frames[tf] = raw.filter(r => r[0] <= t.last_ts);
    }
    // plan parity
    const result = Engine.analyze({
      symbol,
      klines: frames,
      meta24: entry.meta24,
      cfg: Object.assign({}, cfg, { risk: cfg.risk, scoring: cfg.scoring,
        min_score_to_show: cfg.min_score_to_show, min_rr_tp1: cfg.min_rr_tp1,
        allow_shorts: cfg.strategy.allow_shorts,
        disabled_setups: (cfg.strategy && cfg.strategy.disabled_setups) || [],
        supertrend: stp }),
    });
    const pyPlan = entry.plan;
    const jsPlan = result.best;
    if (!pyPlan) {
      assert(!jsPlan, `${symbol} plan: python none, js none`);
    } else {
      if (!jsPlan) { assert(false, `${symbol} plan: python has ${pyPlan.setup_type}, js has none`); }
      else {
        assert(jsPlan.setup_type === pyPlan.setup_type, `${symbol} setup_type (js=${jsPlan.setup_type} py=${pyPlan.setup_type})`);
        assert(jsPlan.direction === pyPlan.direction, `${symbol} direction`);
        assert(jsPlan.status === pyPlan.status, `${symbol} status (js=${jsPlan.status} py=${pyPlan.status})`);
        near(jsPlan.entry_zone[0], pyPlan.entry_zone[0], 1e-9, 0.004, `${symbol} zone lo`);
        near(jsPlan.entry_zone[1], pyPlan.entry_zone[1], 1e-9, 0.004, `${symbol} zone hi`);
        near(jsPlan.stop_loss, pyPlan.stop_loss, 1e-9, 0.004, `${symbol} stop_loss`);
        near(jsPlan.tp1, pyPlan.tp1, 1e-9, 0.004, `${symbol} tp1`);
        near(jsPlan.tp2, pyPlan.tp2, 1e-9, 0.004, `${symbol} tp2`);
        near(jsPlan.tp3, pyPlan.tp3, 1e-9, 0.004, `${symbol} tp3`);
        assert(Math.abs(jsPlan.score - pyPlan.score) <= 2, `${symbol} score (js=${jsPlan.score} py=${pyPlan.score})`);
        near(jsPlan.rr_tp2, pyPlan.rr_tp2, 0.03, 0.02, `${symbol} rr_tp2`);
      }
    }
    await sleep(300); // be gentle with the public API
  }
  console.log(failures === 0 ? '\nGOLDEN TEST: ALL PASS' : `\nGOLDEN TEST: ${failures} FAILURES`);
  process.exitCode = failures > 0 ? 1 : 0;
})().catch(e => { console.error('HARNESS ERROR:', e.message); process.exitCode = 1; });
