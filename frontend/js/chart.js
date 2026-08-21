/* Dependency-free multi-panel candlestick chart:
   price panel + volume bars + RSI(14) panel, with EMA/VWAP/SuperTrend overlays
   and trade levels. Pure canvas — no external libraries. */
'use strict';

function fmtNum(p) {
  if (p >= 1000) return p.toLocaleString('en-US', { maximumFractionDigits: 1 });
  if (p >= 100) return p.toFixed(2);
  if (p >= 1) return p.toFixed(3);
  if (p >= 0.1) return p.toFixed(4);
  if (p >= 0.01) return p.toFixed(5);
  return p.toFixed(7);
}
function fmtVol(v) {
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return String(Math.round(v));
}

/* Wilder's RSI(14) — deterministic, mirrors the backend */
function calcRSI(closes, period) {
  period = period || 14;
  const out = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return out;
  let g = 0, l = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) g += d; else l -= d;
  }
  let ag = g / period, al = l / period;
  out[period] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    ag = (ag * (period - 1) + Math.max(d, 0)) / period;
    al = (al * (period - 1) + Math.max(-d, 0)) / period;
    out[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
  }
  return out;
}
window.calcRSI = calcRSI;

function ensureTip(canvas) {
  if (canvas._tip) return canvas._tip;
  const tip = document.createElement('div');
  tip.style.cssText = 'position:absolute;pointer-events:none;background:#0b0e11;border:1px solid #2c3744;' +
    'border-radius:8px;padding:8px 10px;font:11px ui-monospace,monospace;color:#e8edf2;display:none;z-index:10;white-space:nowrap';
  canvas.parentElement.appendChild(tip);
  canvas._tip = tip;
  return tip;
}

function drawChart(canvas, data, opp, opts) {
  opts = opts || {};
  const showVol = opts.volume !== false;
  const showRsi = opts.rsi !== false;
  if (!canvas || !data || !data.candles || data.candles.length < 10) return;
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth || 800;

  const PAD_T = 10, PAD_B = 6, LBL_H = 16, PRICE_H = 268;
  const VOL_H = showVol ? 52 : 0, RSI_H = showRsi ? 62 : 0;
  const G1 = showVol ? 6 : 0, G2 = showRsi ? 8 : 0;
  const H = PAD_T + PRICE_H + G1 + VOL_H + G2 + RSI_H + LBL_H + PAD_B;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const candles = data.candles.map(c => ({ t: c[0], o: c[1], h: c[2], l: c[3], c: c[4], v: c[5] }));
  const ema20 = data.ema20 || [], ema50 = data.ema50 || [], vwap = data.vwap || [];
  const stLine = data.st_line || [], stDir = data.st_dir || [];
  const rsi = calcRSI(candles.map(c => c.c), 14);
  const GREEN = '#16c784', RED = '#ea3943';
  const isLight = document.documentElement.dataset.theme === 'light';
  const GRID = isLight ? 'rgba(10,20,35,.08)' : 'rgba(255,255,255,.05)';
  const LABEL = isLight ? '#7a8694' : '#6b7886';
  const TIP_BG = isLight ? '#ffffff' : '#0b0e11';
  const TIP_BORDER = isLight ? '#c8d2de' : '#2c3744';
  const TIP_TEXT = isLight ? '#10151c' : '#e8edf2';
  const n = candles.length;

  let lo = Infinity, hi = -Infinity;
  candles.forEach(c => { lo = Math.min(lo, c.l); hi = Math.max(hi, c.h); });
  if (opp) {
    lo = Math.min(lo, opp.stop_loss); hi = Math.max(hi, opp.tp3, opp.tp2, opp.tp1);
  }
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const maxVol = Math.max.apply(null, candles.map(c => c.v)) || 1;

  const plotL = 8, plotR = 74, pw = W - plotL - plotR;
  const X = i => plotL + (i + 0.5) * pw / n;
  const YPrice = p => PAD_T + (hi - p) / (hi - lo) * PRICE_H;
  const volTop = PAD_T + PRICE_H + G1;
  const YVol = v => volTop + VOL_H * (1 - v / maxVol);
  const rsiTop = volTop + VOL_H + G2;
  const YRsi = v => rsiTop + RSI_H * (1 - v / 100);
  const bottomLblY = rsiTop + RSI_H + LBL_H - 2;

  let hoverIdx = null;

  function paint() {
    ctx.clearRect(0, 0, W, H);
    ctx.font = '10px ui-monospace,monospace';

    // ---- price grid + labels ----
    const steps = 5;
    for (let s = 0; s <= steps; s++) {
      const p = lo + (hi - lo) * s / steps;
      ctx.strokeStyle = GRID;
      ctx.beginPath(); ctx.moveTo(plotL, YPrice(p)); ctx.lineTo(W - plotR, YPrice(p)); ctx.stroke();
      ctx.fillStyle = LABEL; ctx.textAlign = 'right';
      ctx.fillText(fmtNum(p), W - plotR + 66, YPrice(p) + 3);
    }

    // ---- trade levels (price panel) ----
    if (opp) {
      const zone = [opp.entry_zone[0], opp.entry_zone[1]];
      ctx.fillStyle = 'rgba(240,185,11,.10)';
      ctx.fillRect(plotL, YPrice(zone[1]), pw, YPrice(zone[0]) - YPrice(zone[1]));
      const line = (p, color, label, dash) => {
        ctx.strokeStyle = color; ctx.setLineDash(dash || []);
        ctx.beginPath(); ctx.moveTo(plotL, YPrice(p)); ctx.lineTo(W - plotR, YPrice(p)); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color; ctx.textAlign = 'right';
        ctx.font = 'bold 10px ui-monospace,monospace';
        ctx.fillText(`${label} ${fmtNum(p)}`, W - plotR + 66, YPrice(p) + 3);
        ctx.font = '10px ui-monospace,monospace';
      };
      line(zone[0], '#f0b90b', 'ENTRY');
      line(zone[1], '#f0b90b', '');
      line(opp.stop_loss, RED, 'SL', [5, 4]);
      line(opp.tp1, GREEN, 'TP1', [5, 4]);
      if (opp.tp2) line(opp.tp2, GREEN, 'TP2', [5, 4]);
      if (opp.tp3) line(opp.tp3, GREEN, 'TP3', [5, 4]);
    }

    // ---- EMA / VWAP / SuperTrend (price panel) ----
    const poly = (arr, color) => {
      if (!arr || arr.length !== n) return;
      ctx.strokeStyle = color; ctx.lineWidth = 1.2;
      ctx.beginPath(); let started = false;
      for (let i = 0; i < n; i++) {
        if (arr[i] <= 0) continue;
        const y = YPrice(arr[i]);
        if (!started) { ctx.moveTo(X(i), y); started = true; } else ctx.lineTo(X(i), y);
      }
      ctx.stroke(); ctx.lineWidth = 1;
    };
    poly(ema20, 'rgba(59,130,246,.9)');
    poly(ema50, 'rgba(168,85,247,.9)');
    poly(vwap, 'rgba(240,185,11,.55)');
    if (stLine.length === n && stDir.length === n) {
      ctx.lineWidth = 1.7;
      for (let i = 0; i < n - 1; i++) {
        if (!stLine[i] || !stLine[i + 1]) continue;
        ctx.strokeStyle = stDir[i] === 1 ? 'rgba(22,199,132,.9)' : 'rgba(234,57,67,.9)';
        ctx.beginPath(); ctx.moveTo(X(i), YPrice(stLine[i])); ctx.lineTo(X(i + 1), YPrice(stLine[i + 1])); ctx.stroke();
      }
      ctx.lineWidth = 1;
    }

    // ---- candles (price panel) ----
    const cw = Math.max(2, Math.min(11, pw / n * 0.7));
    for (let i = 0; i < n; i++) {
      const c = candles[i];
      const up = c.c >= c.o;
      const color = up ? GREEN : RED;
      ctx.strokeStyle = color; ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(X(i), YPrice(c.h)); ctx.lineTo(X(i), YPrice(c.l)); ctx.stroke();
      const yO = YPrice(c.o), yC = YPrice(c.c);
      ctx.fillRect(X(i) - cw / 2, Math.min(yO, yC), cw, Math.max(1, Math.abs(yO - yC)));
    }

    // ---- volume bars ----
    if (showVol) {
      const bw = Math.max(1, pw / n * 0.6);
      for (let i = 0; i < n; i++) {
        const c = candles[i];
        ctx.fillStyle = c.c >= c.o ? 'rgba(22,199,132,.55)' : 'rgba(234,57,67,.55)';
        ctx.fillRect(X(i) - bw / 2, YVol(c.v), bw, volTop + VOL_H - YVol(c.v));
      }
      ctx.fillStyle = LABEL; ctx.textAlign = 'right';
      ctx.fillText('Vol ' + fmtVol(candles[n - 1].v), W - plotR + 66, volTop + 10);
      ctx.textAlign = 'left';
      ctx.fillText('Volume', plotL, volTop + 10);
    }

    // ---- RSI panel ----
    if (showRsi) {
      // overbought/oversold shading + 30/70 lines
      ctx.fillStyle = 'rgba(234,57,67,.07)';
      ctx.fillRect(plotL, rsiTop, pw, RSI_H * 0.3);
      ctx.fillStyle = 'rgba(22,199,132,.07)';
      ctx.fillRect(plotL, rsiTop + RSI_H * 0.7, pw, RSI_H * 0.3);
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = 'rgba(234,57,67,.5)';
      ctx.beginPath(); ctx.moveTo(plotL, YRsi(70)); ctx.lineTo(W - plotR, YRsi(70)); ctx.stroke();
      ctx.strokeStyle = 'rgba(22,199,132,.5)';
      ctx.beginPath(); ctx.moveTo(plotL, YRsi(30)); ctx.lineTo(W - plotR, YRsi(30)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = LABEL; ctx.textAlign = 'right';
      ctx.fillText('70', W - plotR + 66, YRsi(70) + 3);
      ctx.fillText('30', W - plotR + 66, YRsi(30) + 3);
      ctx.textAlign = 'left';
      ctx.fillText('RSI(14)', plotL, rsiTop + 10);
      // RSI line
      ctx.strokeStyle = '#f0b90b'; ctx.lineWidth = 1.4;
      ctx.beginPath(); let started = false;
      for (let i = 0; i < n; i++) {
        if (rsi[i] == null) continue;
        const y = YRsi(rsi[i]);
        if (!started) { ctx.moveTo(X(i), y); started = true; } else ctx.lineTo(X(i), y);
      }
      ctx.stroke(); ctx.lineWidth = 1;
      const lastRsi = rsi[n - 1];
      if (lastRsi != null) {
        ctx.fillStyle = lastRsi > 70 ? RED : lastRsi < 30 ? GREEN : '#f0b90b';
        ctx.textAlign = 'right';
        ctx.font = 'bold 10px ui-monospace,monospace';
        ctx.fillText(lastRsi.toFixed(1), W - plotR + 66, YRsi(lastRsi) + 3);
        ctx.font = '10px ui-monospace,monospace';
      }
    }

    // ---- time labels (bottom) ----
    ctx.textAlign = 'center';
    for (let i = 0; i < n; i += Math.ceil(n / 6)) {
      const d = new Date(candles[i].t);
      ctx.fillStyle = LABEL;
      ctx.fillText(`${d.getUTCMonth() + 1}/${d.getUTCDate()}`, X(i), bottomLblY);
    }

    // ---- crosshair ----
    if (hoverIdx != null && hoverIdx >= 0 && hoverIdx < n) {
      const x = X(hoverIdx);
      ctx.strokeStyle = 'rgba(155,167,180,.45)';
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, rsiTop + RSI_H); ctx.stroke();
      ctx.setLineDash([]);
    }

    // ---- legend (price panel, top-left) ----
    ctx.font = 'bold 10px ui-monospace,monospace';
    ctx.textAlign = 'left';
    const lg = (color, label, x) => { ctx.fillStyle = color; ctx.fillText(label, x, PAD_T + 4); };
    lg('rgba(59,130,246,.9)', 'EMA20', plotL);
    lg('rgba(168,85,247,.9)', 'EMA50', plotL + 58);
    lg('rgba(240,185,11,.8)', 'VWAP', plotL + 118);
    lg('rgba(45,212,191,.95)', 'ST', plotL + 172);
  }

  paint();

  // ---- interaction (tooltip + crosshair) ----
  const tip = ensureTip(canvas);
  function onMove(e) {
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    if (x < plotL || x > W - plotR || y < PAD_T || y > rsiTop + RSI_H) {
      hoverIdx = null; tip.style.display = 'none'; paint(); return;
    }
    const i = Math.min(n - 1, Math.max(0, Math.floor((x - plotL) / pw * n)));
    if (i !== hoverIdx) { hoverIdx = i; paint(); }
    const c = candles[i];
    const d = new Date(c.t);
    const rv = rsi[i];
    tip.style.background = TIP_BG; tip.style.borderColor = TIP_BORDER; tip.style.color = TIP_TEXT;
    tip.innerHTML = `<b>${d.toISOString().slice(0, 16).replace('T', ' ')} UTC</b><br>` +
      `O ${fmtNum(c.o)} H ${fmtNum(c.h)}<br>L ${fmtNum(c.l)} C <span style="color:${c.c >= c.o ? '#16c784' : '#ea3943'}">${fmtNum(c.c)}</span><br>` +
      `Vol ${fmtVol(c.v)}` + (rv != null ? ` · RSI ${rv.toFixed(1)}` : '');
    tip.style.display = 'block';
    const wrap = canvas.parentElement.getBoundingClientRect();
    tip.style.left = Math.min(wrap.width - 210, x + 14) + 'px';
    tip.style.top = (y - 40) + 'px';
  }
  function onLeave() { hoverIdx = null; tip.style.display = 'none'; paint(); }
  // always rebind to the CURRENT data (e.g. after a timeframe switch)
  if (canvas._onMove) canvas.removeEventListener('mousemove', canvas._onMove);
  if (canvas._onLeave) canvas.removeEventListener('mouseleave', canvas._onLeave);
  canvas.addEventListener('mousemove', onMove);
  canvas.addEventListener('mouseleave', onLeave);
  canvas._onMove = onMove; canvas._onLeave = onLeave;
}

window.drawChart = drawChart;
window.fmtNum = fmtNum;
window.fmtVol = fmtVol;
