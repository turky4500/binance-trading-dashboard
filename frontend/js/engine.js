/* Coin Analyzer engine — JavaScript mirror of the Python analyzer.
   Every indicator, gate, level and score formula matches analyzer/*.py.
   Golden tests (tests/golden_js.test.js) verify parity against Python
   reference outputs; any drift fails CI.

   UMD: works in the browser (window.Engine) and in Node (module.exports). */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Engine = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ================= helpers ================= */
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function round1(x) { return Math.round(x * 10) / 10; }

  function roundTo(price) {
    if (price >= 5000) return Math.round(price / 50) * 50;
    if (price >= 500) return Math.round(price / 5) * 5;
    if (price >= 50) return Math.round(price);
    if (price >= 5) return Math.round(price * 2) / 2;
    if (price >= 1) return Math.round(price * 10) / 10;
    if (price >= 0.1) return Math.round(price * 1000) / 1000;
    if (price >= 0.01) return Math.round(price * 10000) / 10000;
    return Math.round(price * 1e6) / 1e6;
  }

  /* ================= indicators ================= */
  function emaArr(vals, n) {
    const a = 2 / (n + 1), out = new Array(vals.length);
    out[0] = vals[0];
    for (let i = 1; i < vals.length; i++) out[i] = vals[i] * a + out[i - 1] * (1 - a);
    return out;
  }
  function emaLast(vals, n) { return emaArr(vals, n)[vals.length - 1]; }

  function rsiArr(vals, n) {
    n = n || 14;
    const out = new Array(vals.length).fill(null);
    if (vals.length < n + 1) return out;
    // seed = first diff (mirrors pandas ewm with NaN at index 0)
    let ag = Math.max(0, vals[1] - vals[0]);
    let al = Math.max(0, vals[0] - vals[1]);
    out[1] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
    for (let i = 2; i < vals.length; i++) {
      const d = vals[i] - vals[i - 1];
      ag = ag + (Math.max(d, 0) - ag) / n;
      al = al + (Math.max(-d, 0) - al) / n;
      out[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
    }
    return out;
  }

  function macdLast(vals, f, sl, sg) {
    f = f || 12; sl = sl || 26; sg = sg || 9;
    const ef = emaArr(vals, f), es = emaArr(vals, sl);
    const m = new Array(vals.length);
    for (let i = 0; i < vals.length; i++) m[i] = ef[i] - es[i];
    const s = emaArr(m, sg);
    return m[m.length - 1] - s[s.length - 1]; // histogram
  }
  function macdHistPrev(vals, f, sl, sg) {
    f = f || 12; sl = sl || 26; sg = sg || 9;
    const ef = emaArr(vals, f), es = emaArr(vals, sl);
    const m = new Array(vals.length);
    for (let i = 0; i < vals.length; i++) m[i] = ef[i] - es[i];
    const s = emaArr(m, sg);
    return m[m.length - 2] - s[s.length - 2];
  }

  function atrLast(bars, n) {
    n = n || 14;
    const tr = new Array(bars.length);
    tr[0] = bars[0].h - bars[0].l;
    for (let i = 1; i < bars.length; i++) {
      const pc = bars[i - 1].c;
      tr[i] = Math.max(bars[i].h - bars[i].l, Math.abs(bars[i].h - pc), Math.abs(bars[i].l - pc));
    }
    let a = tr[0];
    for (let i = 1; i < tr.length; i++) a = a + (tr[i] - a) / n;
    return a;
  }

  function supertrend(bars, period, mult) {
    period = period || 10; mult = mult || 3;
    const n = bars.length;
    // ATR (Wilder)
    const tr = new Array(n);
    tr[0] = bars[0].h - bars[0].l;
    for (let i = 1; i < n; i++) {
      const pc = bars[i - 1].c;
      tr[i] = Math.max(bars[i].h - bars[i].l, Math.abs(bars[i].h - pc), Math.abs(bars[i].l - pc));
    }
    const atr = new Array(n); atr[0] = tr[0];
    for (let i = 1; i < n; i++) atr[i] = atr[i - 1] + (tr[i] - atr[i - 1]) / period;
    const ub = new Array(n), lb = new Array(n);
    for (let i = 0; i < n; i++) {
      const hl2 = (bars[i].h + bars[i].l) / 2;
      ub[i] = hl2 + mult * atr[i];
      lb[i] = hl2 - mult * atr[i];
    }
    const fu = new Array(n), fl = new Array(n), trend = new Array(n);
    fu[0] = ub[0]; fl[0] = lb[0]; trend[0] = 1;
    for (let i = 1; i < n; i++) {
      fu[i] = (ub[i] < fu[i - 1] || bars[i - 1].c > fu[i - 1]) ? ub[i] : fu[i - 1];
      fl[i] = (lb[i] > fl[i - 1] || bars[i - 1].c < fl[i - 1]) ? lb[i] : fl[i - 1];
      if (bars[i].c > fu[i - 1]) trend[i] = 1;
      else if (bars[i].c < fl[i - 1]) trend[i] = -1;
      else trend[i] = trend[i - 1];
    }
    const line = new Array(n);
    for (let i = 0; i < n; i++) line[i] = trend[i] === 1 ? fl[i] : fu[i];
    return { line, dir: trend };
  }

  function vwapLast(bars) {
    // session VWAP = cumulative since the UTC day start
    let cumV = 0, cumPV = 0;
    let dayStart = -1;
    for (let i = bars.length - 1; i >= 0; i--) {
      const day = Math.floor(bars[i].t / 86400000);
      if (dayStart === -1) dayStart = day;
      else if (day !== dayStart) break;
      const tp = (bars[i].h + bars[i].l + bars[i].c) / 3;
      cumV += bars[i].v;
      cumPV += tp * bars[i].v;
    }
    return cumV > 0 ? cumPV / cumV : NaN;
  }

  function volRatio(bars) {
    const n = bars.length;
    // python: vol_ratio3 = mean over the last 3 bars of (v_i / vma20_i) where
    // vma20_i is the 20-bar average INCLUDING bar i — mean of ratios, not ratio of means
    let acc = 0;
    for (let i = n - 3; i < n; i++) {
      let sum20 = 0, cnt = 0;
      for (let j = i - 19; j <= i; j++) {
        if (j >= 0) { sum20 += bars[j].v; cnt++; }
      }
      if (cnt && sum20 > 0) acc += bars[i].v / (sum20 / cnt);
    }
    return acc / 3;
  }

  function swings(bars, k) {
    const highs = [], lows = [];
    for (let i = k; i < bars.length - k; i++) {
      let isH = true, isL = true;
      for (let j = i - k; j <= i + k; j++) {
        if (j === i) continue;
        // mirrors python: h[i] == max(window) — ties also register
        if (bars[j].h > bars[i].h) isH = false;
        if (bars[j].l < bars[i].l) isL = false;
      }
      if (isH) highs.push({ p: bars[i].h, t: bars[i].t });
      if (isL) lows.push({ p: bars[i].l, t: bars[i].t });
    }
    return { highs, lows };
  }

  /* ================= timeframe state (mirrors tf_state) ================= */
  function tfState(bars, k, stp) {
    stp = stp || { period: 10, multiplier: 3.0 };
    const c = bars[bars.length - 1].c;
    const closes = bars.map(b => b.c);
    const n = bars.length;
    let hi20 = -Infinity, lo20 = Infinity, hi50 = -Infinity, lo50 = Infinity, hi6 = -Infinity, lo6 = Infinity;
    for (let i = 0; i < n; i++) {
      const h = bars[i].h, l = bars[i].l;
      if (i >= n - 20) { hi20 = Math.max(hi20, h); lo20 = Math.min(lo20, l); }
      if (i >= n - 50) { hi50 = Math.max(hi50, h); lo50 = Math.min(lo50, l); }
      if (i >= n - 6) { hi6 = Math.max(hi6, h); lo6 = Math.min(lo6, l); }
    }
    // breakout reference: extremes of the window BEFORE the last 8 bars
    let res = hi50, sup = lo50;
    if (n >= 64) {
      res = -Infinity; sup = Infinity;
      for (let i = n - 60; i < n - 8; i++) {
        res = Math.max(res, bars[i].h); sup = Math.min(sup, bars[i].l);
      }
    }
    const ema20 = emaLast(closes, 20), ema50 = emaLast(closes, 50), ema200 = emaLast(closes, 200);
    const rsi = rsiArr(closes)[n - 1];
    const mh = macdLast(closes), mhp = macdHistPrev(closes);
    const atr = atrLast(bars);
    const st = supertrend(bars, stp.period, stp.multiplier);
    const sw = swings(bars, k);
    return {
      close: c,
      ema20, ema50, ema200,
      rsi: rsi == null ? 50 : rsi,
      macd_h: mh, macd_h_prev: mhp,
      atr, vwap: vwapLast(bars),
      vol_ratio3: volRatio(bars),
      above20: c > ema20, above50: c > ema50, above200: c > ema200,
      e20_gt_e50: ema20 > ema50,
      sw_highs: sw.highs, sw_lows: sw.lows,
      last_high: sw.highs.length ? sw.highs[sw.highs.length - 1].p : c,
      last_low: sw.lows.length ? sw.lows[sw.lows.length - 1].p : c,
      hi20, lo20, hi50, lo50, hi6, lo6,
      st_dir: st.dir[n - 1], st_line: st.line[n - 1],
      resistance: res, support: sup,
    };
  }

  function detectBreakout(bars) {
    const n = bars.length;
    if (n < 78) return null;
    let baseHi = -Infinity, baseLo = Infinity;
    for (let i = n - 60; i < n - 8; i++) {
      baseHi = Math.max(baseHi, bars[i].h);
      baseLo = Math.min(baseLo, bars[i].l);
    }
    for (let i = n - 8; i < n; i++) {
      const vr = barVolRatio(bars, i);
      if (bars[i].c > baseHi && vr >= 1.5) return { level: baseHi, dir: 'UP', at: bars[i].t };
      if (bars[i].c < baseLo && vr >= 1.5) return { level: baseLo, dir: 'DOWN', at: bars[i].t };
    }
    return null;
  }
  function barVolRatio(bars, idx) {
    let sum20 = 0, cnt = 0;
    for (let i = idx - 19; i <= idx; i++) {
      if (i >= 0) { sum20 += bars[i].v; cnt++; }
    }
    return cnt ? bars[idx].v / (sum20 / cnt) : 1;
  }

  /* ================= plans (mirror analyzer/signal.py) ================= */
  function targets(entry, R, t4, td, direction, tol) {
    tol = tol || 0.08;
    const struct = direction === 'LONG'
      ? [t4.last_high, t4.hi20, t4.hi50, td.last_high]
      : [t4.last_low, t4.lo20, t4.lo50, td.last_low];
    function target(mult) {
      const raw = direction === 'LONG' ? entry + mult * R : entry - mult * R;
      let best = raw;
      for (const lv of struct) {
        if (!lv || lv <= 0) continue;
        const d = (lv - raw) / raw;
        if (d >= -tol && d <= 0.05 && Math.abs(lv - raw) < Math.abs(best - raw)) best = lv;
      }
      return direction === 'LONG'
        ? Math.max(best, entry + (mult - 0.2) * R)
        : Math.min(best, entry - (mult - 0.2) * R);
    }
    return [target(1.5), target(2.5), target(4.0)];
  }

  function slClamp(entry, rawSl, atr, direction, risk) {
    const lo = risk.atr_sl_min || 0.8, hi = risk.atr_sl_max || 2.0;
    if (direction === 'LONG') {
      const dist = clamp(entry - rawSl, lo * atr, hi * atr);
      return entry - dist;
    }
    const dist = clamp(rawSl - entry, lo * atr, hi * atr);
    return entry + dist;
  }

  function pullbackLong(tf, risk, minRR) {
    const td = tf['1d'], t4 = tf['4h'], t1 = tf['1h'];
    const atr = t4.atr;
    if (!(atr > 0)) return null;
    if (!(td.above20 && td.e20_gt_e50 && td.rsi >= 46 && td.rsi <= 78 && td.macd_h > 0)) return null;
    if (!(t4.close > t4.ema50 && t4.close >= t4.ema20 - 0.25 * atr)) return null;
    if (t4.rsi > 78) return null;
    if (!(t1.rsi >= 38 && t1.rsi <= 68)) return null;
    const anchor = t4.ema20;
    const zone = [anchor, anchor + (risk.pullback_zone_atr || 0.6) * atr];
    const entryMid = (zone[0] + zone[1]) / 2;
    const price = t1.close;
    const inZone = zone[0] - 0.05 * atr <= price && price <= zone[1] + 0.15 * atr;
    const lows = t1.sw_lows.slice(-3).map(x => x.p);
    const swLow = lows.length ? Math.min.apply(null, lows) : t1.lo20;
    const rawSl = Math.min(swLow, t4.ema50) - 0.2 * atr;
    let sl = slClamp(entryMid, rawSl, atr, 'LONG', risk);
    if (sl >= zone[0]) sl = entryMid - 1.2 * atr;
    const R = entryMid - sl;
    const [tp1, tp2, tp3] = targets(entryMid, R, t4, td, 'LONG');
    if ((tp1 - entryMid) / R < minRR) return null;
    const confluences = ['4H EMA20'];
    if (isFinite(t4.vwap) && Math.abs(t4.vwap - anchor) < 0.6 * atr) confluences.push('Session VWAP');
    if (Math.abs(t4.ema50 - anchor) < 0.5 * atr) confluences.push('4H EMA50');
    if (Math.abs(swLow - zone[0]) < 0.8 * atr) confluences.push('1H swing low');
    return {
      direction: 'LONG', setup_type: 'PULLBACK', setup_label: 'Pullback to EMA20 (4H)',
      status: inZone ? 'READY' : 'WAITING_CONFIRMATION',
      entry_mid: entryMid, entry_zone: zone, stop_loss: sl, tp1, tp2, tp3,
      invalidation_level: t4.ema50, primary_timeframe: '4H', confluences,
      supports: [[t1.sw_lows.length ? t1.sw_lows[t1.sw_lows.length - 1].p : t1.lo20, '1H swing low'], [t4.ema50, '4H EMA50'], [t4.ema20, '4H EMA20']],
      resistances: [[t4.last_high, '4H swing high'], [t4.hi20, '4H 20-bar high'], [td.last_high, 'Daily swing high']],
    };
  }

  function vwapHoldLong(tf, risk, minRR) {
    const td = tf['1d'], t4 = tf['4h'], t1 = tf['1h'];
    const atr = t4.atr;
    if (!(atr > 0)) return null;
    if (!(td.above20 && td.e20_gt_e50 && td.rsi >= 48 && td.rsi <= 80 && td.macd_h > 0)) return null;
    if (!(t4.above20 && t4.e20_gt_e50)) return null;
    if (t4.rsi > 82) return null;
    if (!(t1.rsi >= 42 && t1.rsi <= 70)) return null;
    const vwap = t4.vwap;
    if (!isFinite(vwap)) return null;
    if (vwap < t4.ema20 + 0.4 * atr) return null;
    const zone = [vwap - 0.3 * atr, vwap + 0.35 * atr];
    const entryMid = (zone[0] + zone[1]) / 2;
    const price = t1.close;
    const inZone = zone[0] - 0.1 * atr <= price && price <= zone[1] + 0.1 * atr;
    const lows = t1.sw_lows.slice(-3).map(x => x.p);
    const swLow = lows.length ? Math.min.apply(null, lows) : t1.lo20;
    const rawSl = Math.min(swLow - 0.25 * atr, vwap - 1.4 * atr);
    let sl = slClamp(entryMid, rawSl, atr, 'LONG', risk);
    if (sl >= zone[0]) sl = entryMid - 1.3 * atr;
    const R = entryMid - sl;
    const [tp1, tp2, tp3] = targets(entryMid, R, t4, td, 'LONG');
    if ((tp1 - entryMid) / R < minRR) return null;
    const confluences = ['Session VWAP', '4H EMA20 (below)'];
    if (t1.sw_lows.length && Math.abs(t1.sw_lows[t1.sw_lows.length - 1].p - zone[0]) < 0.9 * atr) confluences.push('1H swing low');
    return {
      direction: 'LONG', setup_type: 'VWAP_HOLD', setup_label: 'Momentum continuation at VWAP (4H)',
      status: inZone ? 'READY' : 'WAITING_CONFIRMATION',
      entry_mid: entryMid, entry_zone: zone, stop_loss: sl, tp1, tp2, tp3,
      invalidation_level: vwap - 1.2 * atr, primary_timeframe: '4H', confluences,
      supports: [[vwap, 'Session VWAP'], [t4.ema20, '4H EMA20'], [t1.sw_lows.length ? t1.sw_lows[t1.sw_lows.length - 1].p : t1.lo20, '1H swing low']],
      resistances: [[t4.last_high, '4H swing high'], [t4.hi20, '4H 20-bar high'], [td.last_high, 'Daily swing high']],
    };
  }

  function breakoutLong(tf, brk, risk, minRR) {
    const td = tf['1d'], t4 = tf['4h'], t1 = tf['1h'];
    const atr = t4.atr;
    if (!(atr > 0) || !brk || brk.dir !== 'UP') return null;
    const R = brk.level;
    if (!(td.above20 || (td.above50 && td.rsi > 50))) return null;
    if (td.macd_h < 0 && t4.macd_h < 0) return null;
    if (t1.rsi > 78) return null;
    if (t4.rsi > 84) return null;
    const price = t1.close;
    if (price < R - 0.9 * atr) return null;
    const zone = [R - 0.4 * atr, R + 0.35 * atr];
    const entryMid = (zone[0] + zone[1]) / 2;
    const inZone = zone[0] <= price && price <= zone[1];
    if (!(price <= R + 3.0 * atr)) return null;
    const rawSl = Math.min(R - 1.0 * atr, t4.lo6 - 0.25 * atr);
    const sl = slClamp(entryMid, rawSl, atr, 'LONG', risk);
    const Rr = entryMid - sl;
    const [tp1, tp2, tp3] = targets(entryMid, Rr, t4, td, 'LONG');
    if ((tp1 - entryMid) / Rr < minRR) return null;
    const status = (inZone && t4.rsi <= 80) ? 'READY' : 'WAITING_CONFIRMATION';
    const confluences = ['Breakout level ' + R];
    if (Math.abs(t4.ema20 - zone[0]) < 0.8 * atr) confluences.push('4H EMA20');
    if (isFinite(t4.vwap) && Math.abs(t4.vwap - zone[0]) < 0.8 * atr) confluences.push('Session VWAP');
    return {
      direction: 'LONG', setup_type: 'BREAKOUT_RETEST', setup_label: 'Breakout + Retest (4H)',
      status, entry_mid: entryMid, entry_zone: zone, stop_loss: sl, tp1, tp2, tp3,
      invalidation_level: R - 0.9 * atr, primary_timeframe: '4H', confluences,
      supports: [[R, 'Breakout level (now support)'], [t4.lo6, '4H retest low'], [t4.ema20, '4H EMA20']],
      resistances: [[t4.last_high, '4H swing high'], [t4.hi20, '4H 20-bar high'], [td.last_high, 'Daily swing high']],
    };
  }

  function breakdownShort(tf, brk, risk, minRR) {
    const td = tf['1d'], t4 = tf['4h'], t1 = tf['1h'];
    const atr = t4.atr;
    if (!(atr > 0) || !brk || brk.dir !== 'DOWN') return null;
    const S = brk.level;
    if (!(td.close < td.ema20 || (td.close < td.ema50 && td.rsi < 50))) return null;
    if (td.macd_h > 0 && t4.macd_h > 0) return null;
    if (t1.rsi < 25) return null;
    if (t4.rsi < 16) return null;
    const price = t1.close;
    if (price > S + 0.9 * atr) return null;
    const zone = [S - 0.35 * atr, S + 0.4 * atr];
    const entryMid = (zone[0] + zone[1]) / 2;
    const inZone = zone[0] <= price && price <= zone[1];
    if (!(price >= S - 3.0 * atr)) return null;
    const rawSl = Math.max(S + 1.0 * atr, t4.hi6 + 0.25 * atr);
    const sl = slClamp(entryMid, rawSl, atr, 'SHORT', risk);
    const Rr = sl - entryMid;
    const [tp1, tp2, tp3] = targets(entryMid, Rr, t4, td, 'SHORT');
    if ((entryMid - tp1) / Rr < minRR) return null;
    const status = (inZone && t4.rsi >= 20) ? 'READY' : 'WAITING_CONFIRMATION';
    return {
      direction: 'SHORT', setup_type: 'BREAKDOWN_RETEST', setup_label: 'Breakdown + Retest (4H)',
      status, entry_mid: entryMid, entry_zone: zone, stop_loss: sl, tp1, tp2, tp3,
      invalidation_level: S + 0.9 * atr, primary_timeframe: '4H',
      confluences: ['Breakdown level ' + S],
      supports: [[t4.lo20, '4H 20-bar low'], [td.last_low, 'Daily swing low']],
      resistances: [[S, 'Breakdown level (now resistance)'], [t4.hi6, '4H retest high'], [t4.ema20, '4H EMA20']],
    };
  }

  function trendShort(tf, risk, minRR) {
    const td = tf['1d'], t4 = tf['4h'], t1 = tf['1h'];
    const atr = t4.atr;
    if (!(atr > 0)) return null;
    if (!(td.close < td.ema20 && td.close < td.ema50 && td.rsi < 58)) return null;
    if (!(t4.close < t4.ema20 && t4.close < t4.ema50)) return null;
    if (!(t1.rsi >= 44 && t1.rsi <= 68)) return null;
    const anchor = t4.ema20;
    const zone = [anchor - 0.3 * atr, anchor + 0.35 * atr];
    const entryMid = (zone[0] + zone[1]) / 2;
    const price = t1.close;
    const inZone = zone[0] - 0.15 * atr <= price && price <= zone[1] + 0.05 * atr;
    const highs = t1.sw_highs.slice(-3).map(x => x.p);
    const swHigh = highs.length ? Math.max.apply(null, highs) : t1.hi20;
    const rawSl = Math.max(swHigh, t4.ema50) + 0.2 * atr;
    let sl = slClamp(entryMid, rawSl, atr, 'SHORT', risk);
    if (sl <= zone[1]) sl = entryMid + 1.2 * atr;
    const Rr = sl - entryMid;
    const [tp1, tp2, tp3] = targets(entryMid, Rr, t4, td, 'SHORT');
    if ((entryMid - tp1) / Rr < minRR) return null;
    return {
      direction: 'SHORT', setup_type: 'TREND_SHORT', setup_label: 'Trend-follow short at EMA20 (4H)',
      status: inZone ? 'READY' : 'WAITING_CONFIRMATION',
      entry_mid: entryMid, entry_zone: zone, stop_loss: sl, tp1, tp2, tp3,
      invalidation_level: t4.ema20 + 1.0 * atr, primary_timeframe: '4H',
      confluences: ['4H EMA20 rejection'],
      supports: [[t4.lo20, '4H 20-bar low'], [td.last_low, 'Daily swing low']],
      resistances: [[anchor, '4H EMA20'], [swHigh, '1H swing high'], [t4.hi20, '4H 20-bar high']],
    };
  }

  function generatePlans(tf, brk, risk, minRR, allowShorts) {
    const plans = [];
    const p = pullbackLong(tf, risk, minRR); if (p) plans.push(p);
    const v = vwapHoldLong(tf, risk, minRR); if (v) plans.push(v);
    const b = breakoutLong(tf, brk, risk, minRR); if (b) plans.push(b);
    if (allowShorts) {
      const bs = breakdownShort(tf, brk, risk, minRR); if (bs) plans.push(bs);
      const ts = trendShort(tf, risk, minRR); if (ts) plans.push(ts);
    }
    const best = {};
    for (const pl of plans) {
      const cur = best[pl.direction];
      if (!cur) { best[pl.direction] = pl; continue; }
      if (pl.status === 'READY' && cur.status !== 'READY') { best[pl.direction] = pl; continue; }
      if (pl.status === cur.status) {
        const mid = (pl.entry_zone[0] + pl.entry_zone[1]) / 2;
        const curMid = (cur.entry_zone[0] + cur.entry_zone[1]) / 2;
        if (Math.abs(mid - tf['1h'].close) < Math.abs(curMid - tf['1h'].close)) best[pl.direction] = pl;
      }
    }
    return Object.keys(best).map(k => best[k]);
  }

  /* ================= scoring (mirror analyzer/scoring.py) ================= */
  function scorePlan(tf, plan, meta24, w) {
    const d = plan.direction;
    function trend() {
      let pts = 0;
      const tfs = ['1h', '4h', '1d'];
      if (d === 'LONG') {
        for (const k of tfs) { if (tf[k].above20) pts++; if (tf[k].e20_gt_e50) pts++; }
        if (tf['1d'].above200) pts += 2;
        if (tf['1d'].macd_h > 0) pts++;
        if (tf['4h'].above200) pts++;
        if (tf['4h'].st_dir === 1) pts++;
        if (tf['1h'].st_dir === 1) pts++;
      } else {
        for (const k of tfs) { if (!tf[k].above20) pts++; if (!tf[k].e20_gt_e50) pts++; }
        if (!tf['1d'].above200) pts += 2;
        if (tf['1d'].macd_h < 0) pts++;
        if (!tf['4h'].above200) pts++;
        if (tf['4h'].st_dir === -1) pts++;
        if (tf['1h'].st_dir === -1) pts++;
      }
      return round1(w.trend_alignment * clamp(pts / 12, 0, 1));
    }
    function structure() {
      const t4 = tf['4h'];
      if (plan.setup_type === 'BREAKOUT_RETEST' || plan.setup_type === 'BREAKDOWN_RETEST') {
        const atr = t4.atr || 1e-9;
        let pts = 0;
        if (d === 'LONG') {
          const R = plan.invalidation_level + 0.9 * atr;
          if (t4.close > R) pts += 3;
          if (t4.lo6 >= R - 0.6 * atr) pts += 2;
          if (t4.above20) pts += 1;
        } else {
          const S = plan.invalidation_level - 0.9 * atr;
          if (t4.close < S) pts += 3;
          if (t4.hi6 <= S + 0.6 * atr) pts += 2;
          if (!t4.above20) pts += 1;
        }
        return round1(w.structure * clamp(pts / 6, 0, 1));
      }
      let pts = 0;
      const hs = t4.sw_highs.map(x => x.p), ls = t4.sw_lows.map(x => x.p);
      const seqs = d === 'LONG' ? [ls.slice(-3), hs.slice(-3)] : [hs.slice(-3), ls.slice(-3)];
      for (const seq of seqs) {
        for (let i = 1; i < seq.length; i++) {
          if (d === 'LONG' ? seq[i] > seq[i - 1] : seq[i] < seq[i - 1]) pts++;
        }
      }
      return round1(w.structure * clamp(pts / 6, 0, 1));
    }
    function sr() {
      const t4 = tf['4h'];
      const atr = t4.atr || 1e-9;
      const lo = plan.entry_zone[0], hi = plan.entry_zone[1];
      let pts = 0;
      const checks = [t4.ema20, t4.ema50, t4.vwap, t4.last_low, t4.last_high];
      for (const lv of checks) {
        if (lv && lo - 0.5 * atr <= lv && lv <= hi + 0.5 * atr) pts++;
      }
      pts += 0.5 * plan.confluences.length;
      return round1(w.support_resistance * clamp(pts / 4, 0, 1));
    }
    function volume() {
      const vr = tf['4h'].vol_ratio3;
      const b = vr >= 2.5 ? 10 : vr >= 1.8 ? 8 : vr >= 1.3 ? 6 : vr >= 1.0 ? 4 : 2;
      const qv = meta24.quoteVol || 0;
      const l = qv >= 50e6 ? 5 : qv >= 25e6 ? 4 : qv >= 10e6 ? 3 : qv >= 5e6 ? 2 : 1;
      return round1(w.volume * clamp((b + l) / 15, 0, 1));
    }
    function momentum() {
      const t4 = tf['4h'], t1 = tf['1h'];
      let pts = 0;
      if (d === 'LONG') {
        if (t4.macd_h > 0 && t4.macd_h > t4.macd_h_prev) pts += 3; else if (t4.macd_h > 0) pts += 1;
        if (t1.macd_h > 0) pts += 2;
        if (t4.rsi >= 50 && t4.rsi <= 78) pts += 2;
        if (t1.rsi >= 40 && t1.rsi <= 65) pts += 3;
      } else {
        if (t4.macd_h < 0 && t4.macd_h < t4.macd_h_prev) pts += 3; else if (t4.macd_h < 0) pts += 1;
        if (t1.macd_h < 0) pts += 2;
        if (t4.rsi >= 22 && t4.rsi <= 50) pts += 2;
        if (t1.rsi >= 35 && t1.rsi <= 60) pts += 3;
      }
      return round1(w.momentum * clamp(pts / 10, 0, 1));
    }
    function entryQuality() {
      const t4 = tf['4h'];
      const atr = t4.atr || 1e-9;
      const price = tf['1h'].close;
      const lo = plan.entry_zone[0], hi = plan.entry_zone[1];
      const dist = (price >= lo && price <= hi) ? 0 : Math.min(Math.abs(price - lo), Math.abs(price - hi)) / atr;
      let pts = dist <= 0.2 ? 10 : dist <= 0.5 ? 8 : dist <= 1.2 ? 6 : dist <= 2.5 ? 4 : 2;
      const slDist = Math.abs(plan.entry_mid - plan.stop_loss) / atr;
      if (slDist >= 1.0 && slDist <= 1.7) pts = Math.min(10, pts + 1);
      return round1(w.entry_quality * clamp(pts / 10, 0, 1));
    }
    function riskReward() {
      const R = Math.abs(plan.entry_mid - plan.stop_loss);
      if (!(R > 0)) return 0;
      const rr2 = Math.abs(plan.tp2 - plan.entry_mid) / R;
      const pts = rr2 >= 2.5 ? 10 : rr2 >= 2.0 ? 8 : rr2 >= 1.6 ? 6 : rr2 >= 1.2 ? 4 : 2;
      return round1(w.risk_reward * pts / 10);
    }
    function liquidity() {
      let pts = 0;
      pts += (meta24.spread <= 0.03) ? 2.5 : (meta24.spread <= 0.10 ? 2 : 1);
      pts += (meta24.trades >= 50000) ? 2.5 : (meta24.trades >= 10000 ? 2 : 1);
      return round1(w.liquidity * clamp(pts / 5, 0, 1));
    }
    const parts = {
      trend_alignment: trend(), structure: structure(), support_resistance: sr(),
      volume: volume(), momentum: momentum(), entry_quality: entryQuality(),
      risk_reward: riskReward(), liquidity: liquidity(),
    };
    let total = 0;
    for (const k in parts) total += parts[k];
    return { score: Math.round(total), parts };
  }

  /* ================= diagnostics (why no setup?) ================= */
  function diagnostics(tf, plans, brk) {
    const td = tf['1d'], t4 = tf['4h'], t1 = tf['1h'];
    const list = [];
    const add = (en, ar, ok) => list.push({ en, ar, ok });
    add('Daily: close above EMA20 & EMA50 (trend up)', 'يومي: إغلاق فوق EMA20 و EMA50 (اتجاه صاعد)', td.above20 && td.e20_gt_e50);
    add('Daily MACD positive', 'MACD اليومي موجب', td.macd_h > 0);
    add('Daily RSI in healthy zone (46-78)', 'RSI اليومي في المنطقة الصحية (46-78)', td.rsi >= 46 && td.rsi <= 78);
    add('4H: close above EMA50 (structure)', '4س: إغلاق فوق EMA50 (هيكل)', t4.close > t4.ema50);
    add('4H RSI not overbought (≤82)', 'RSI على 4س غير مشبع شرائيًا (≤82)', t4.rsi <= 82);
    add('1H RSI cooled (38-70)', 'RSI على الساعة مهدأ (38-70)', t1.rsi >= 38 && t1.rsi <= 70);
    add('SuperTrend 4H bullish', 'سوبر ترند 4س صاعد', t4.st_dir === 1);
    if (brk) add('Fresh breakout detected on 4H', 'اختراق حديث مكتشف على 4س', true);
    return list;
  }

  /* ================= main entry ================= */
  function analyze(opts) {
    // opts: { symbol, klines:{'15m'|'1h'|'4h'|'1d': [[t,o,h,l,c,v],...]},
    //         meta24:{quoteVol,spread,trades,chg24}, cfg }
    const cfg = opts.cfg || {};
    const risk = Object.assign({ atr_sl_min: 0.8, atr_sl_max: 2.0, pullback_zone_atr: 0.6 }, cfg.risk || {});
    const weights = cfg.scoring || {
      trend_alignment: 20, structure: 15, support_resistance: 15, volume: 15,
      momentum: 10, entry_quality: 10, risk_reward: 10, liquidity: 5,
    };
    const minScore = cfg.min_score_to_show != null ? cfg.min_score_to_show : 70;
    const minRR = cfg.min_rr_tp1 != null ? cfg.min_rr_tp1 : 1.0;
    const allowShorts = cfg.allow_shorts !== false;
    const stp = cfg.supertrend || { period: 10, multiplier: 3.0 };

    const toBars = rows => rows.map(r => ({ t: +r[0], o: +r[1], h: +r[2], l: +r[3], c: +r[4], v: +r[5] }));
    const frames = {};
    for (const tf of ['15m', '1h', '4h', '1d']) {
      if (!opts.klines[tf] || opts.klines[tf].length < 60) {
        return { error: 'insufficient_data', message: 'Not enough candles for ' + tf };
      }
      frames[tf] = toBars(opts.klines[tf]);
    }
    const tf = {
      '15m': tfState(frames['15m'], 2, stp),
      '1h': tfState(frames['1h'], 2, stp),
      '4h': tfState(frames['4h'], 3, stp),
      '1d': tfState(frames['1d'], 3, stp),
    };
    const brk = detectBreakout(frames['4h']);
    const plans = generatePlans(tf, brk, risk, minRR, allowShorts);
    const results = plans.map(p => {
      const s = scorePlan(tf, p, opts.meta24 || {}, weights);
      const R = Math.abs(p.entry_mid - p.stop_loss);
      return Object.assign({}, p, {
        score: s.score, score_breakdown: s.parts,
        rr_tp1: R > 0 ? Math.round(Math.abs(p.tp1 - p.entry_mid) / R * 100) / 100 : 0,
        rr_tp2: R > 0 ? Math.round(Math.abs(p.tp2 - p.entry_mid) / R * 100) / 100 : 0,
        rr_tp3: R > 0 ? Math.round(Math.abs(p.tp3 - p.entry_mid) / R * 100) / 100 : 0,
        sl_distance_pct: Math.round(Math.abs(p.entry_mid - p.stop_loss) / p.entry_mid * 10000) / 100,
        grade: s.score >= 90 ? 'EXCELLENT' : s.score >= 80 ? 'STRONG' : s.score >= 70 ? 'GOOD' : 'WEAK',
      });
    }).filter(r => r.score >= minScore);
    results.sort((a, b) => b.score - a.score);
    return {
      symbol: opts.symbol,
      tf,
      plans: results,
      best: results[0] || null,
      diagnostics: diagnostics(tf, plans, brk),
      breakout: brk,
      analyzed_at: new Date().toISOString(),
      meta24: opts.meta24 || {},
    };
  }

  return { analyze, tfState, supertrend, emaLast, emaArr, rsiArr, macdLast, atrLast, vwapLast,
           detectBreakout, generatePlans, scorePlan };
}));
