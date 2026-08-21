/* Dependency-free canvas candlestick chart with EMA/VWAP overlays and trade levels */
function drawChart(canvas, data, opp) {
  if (!canvas || !data || !data.candles || data.candles.length < 10) return;
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth || 800, H = 360;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const candles = data.candles.map(c => ({ t: c[0], o: c[1], h: c[2], l: c[3], c: c[4], v: c[5] }));
  const ema20 = data.ema20 || [], ema50 = data.ema50 || [], vwap = data.vwap || [];
  const GREEN = '#16c784', RED = '#ea3943';
  const isLight = document.documentElement.dataset.theme === 'light';
  const GRID = isLight ? 'rgba(10,20,35,.08)' : 'rgba(255,255,255,.05)';
  const LABEL = isLight ? '#7a8694' : '#6b7886';
  const TIP_BG = isLight ? '#ffffff' : '#0b0e11';
  const TIP_BORDER = isLight ? '#c8d2de' : '#2c3744';
  const TIP_TEXT = isLight ? '#10151c' : '#e8edf2';

  let lo = Infinity, hi = -Infinity;
  candles.forEach(c => { lo = Math.min(lo, c.l); hi = Math.max(hi, c.h); });
  if (opp) {
    lo = Math.min(lo, opp.stop_loss); hi = Math.max(hi, opp.tp3, opp.tp2, opp.tp1);
  }
  const pad = (hi - lo) * 0.08 || 1;
  lo -= pad; hi += pad;

  const plotL = 8, plotR = 74, plotT = 12, plotB = 26;
  const pw = W - plotL - plotR, ph = H - plotT - plotB;
  const X = i => plotL + (i + 0.5) * (pw / candles.length);
  const Y = p => plotT + (hi - p) / (hi - lo) * ph;

  // grid + price labels
  ctx.font = '10px ui-monospace,monospace';
  const steps = 6;
  for (let s = 0; s <= steps; s++) {
    const p = lo + (hi - lo) * s / steps;
    const y = Y(p);
    ctx.strokeStyle = GRID;
    ctx.beginPath(); ctx.moveTo(plotL, y); ctx.lineTo(W - plotR, y); ctx.stroke();
    ctx.fillStyle = LABEL;
    ctx.textAlign = 'right';
    ctx.fillText(fmtNum(p), W - plotR + 66, y + 3);
  }
  // time labels
  ctx.textAlign = 'center';
  const n = candles.length;
  for (let i = 0; i < n; i += Math.ceil(n / 6)) {
    const d = new Date(candles[i].t);
    ctx.fillStyle = LABEL;
    ctx.fillText(`${d.getUTCMonth()+1}/${d.getUTCDate()}`, X(i), H - 8);
  }

  // levels: entry zone (shaded), SL, TPs
  if (opp) {
    const zone = [opp.entry_zone[0], opp.entry_zone[1]];
    ctx.fillStyle = 'rgba(240,185,11,.10)';
    ctx.fillRect(plotL, Y(zone[1]), pw, Y(zone[0]) - Y(zone[1]));
    const line = (p, color, label, dash) => {
      ctx.strokeStyle = color; ctx.setLineDash(dash || []);
      ctx.beginPath(); ctx.moveTo(plotL, Y(p)); ctx.lineTo(W - plotR, Y(p)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color; ctx.textAlign = 'right';
      ctx.font = 'bold 10px ui-monospace,monospace';
      ctx.fillText(`${label} ${fmtNum(p)}`, W - plotR + 66, Y(p) + 3);
    };
    line(zone[0], '#f0b90b', 'ENTRY');
    line(zone[1], '#f0b90b', '', );
    line(opp.stop_loss, RED, 'SL', [5, 4]);
    line(opp.tp1, GREEN, 'TP1', [5, 4]);
    if (opp.tp2) line(opp.tp2, GREEN, 'TP2', [5, 4]);
    if (opp.tp3) line(opp.tp3, GREEN, 'TP3', [5, 4]);
  }

  // EMA/VWAP lines
  const poly = (arr, color) => {
    if (!arr || arr.length !== n) return;
    ctx.strokeStyle = color; ctx.lineWidth = 1.2;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < n; i++) {
      if (arr[i] <= 0) continue;
      const y = Y(arr[i]);
      if (!started) { ctx.moveTo(X(i), y); started = true; } else ctx.lineTo(X(i), y);
    }
    ctx.stroke();
  };
  poly(ema20, 'rgba(59,130,246,.9)');
  poly(ema50, 'rgba(168,85,247,.9)');
  poly(vwap, 'rgba(240,185,11,.55)');

  // SuperTrend line: green segments while UP, red while DOWN
  const stLine = data.st_line || [], stDir = data.st_dir || [];
  if (stLine.length === n && stDir.length === n) {
    ctx.lineWidth = 1.7;
    for (let i = 0; i < n - 1; i++) {
      if (!stLine[i] || !stLine[i + 1]) continue;
      ctx.strokeStyle = stDir[i] === 1 ? 'rgba(22,199,132,.9)' : 'rgba(234,57,67,.9)';
      ctx.beginPath();
      ctx.moveTo(X(i), Y(stLine[i]));
      ctx.lineTo(X(i + 1), Y(stLine[i + 1]));
      ctx.stroke();
    }
    ctx.lineWidth = 1;
  }

  // candles
  const cw = Math.max(2, Math.min(11, pw / n * 0.7));
  for (let i = 0; i < n; i++) {
    const c = candles[i];
    const up = c.c >= c.o;
    const color = up ? GREEN : RED;
    ctx.strokeStyle = color; ctx.fillStyle = color;
    ctx.beginPath(); ctx.moveTo(X(i), Y(c.h)); ctx.lineTo(X(i), Y(c.l)); ctx.stroke();
    const yO = Y(c.o), yC = Y(c.c);
    const top = Math.min(yO, yC), hgt = Math.max(1, Math.abs(yO - yC));
    if (up) { ctx.fillRect(X(i) - cw / 2, top, cw, hgt); }
    else { ctx.fillRect(X(i) - cw / 2, top, cw, hgt); }
  }

  // crosshair + tooltip
  let tip = null;
  const mkTip = () => {
    tip = document.createElement('div');
    tip.style.cssText = 'position:absolute;pointer-events:none;background:' + TIP_BG + ';border:1px solid ' + TIP_BORDER + ';' +
      'border-radius:8px;padding:8px 10px;font:11px ui-monospace,monospace;color:' + TIP_TEXT + ';display:none;z-index:10;white-space:nowrap';
    canvas.parentElement.appendChild(tip);
  };
  mkTip();
  canvas.addEventListener('mousemove', e => {
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    if (x < plotL || x > W - plotR || y < plotT || y > H - plotB) { tip.style.display = 'none'; return; }
    const i = Math.min(n - 1, Math.max(0, Math.floor((x - plotL) / pw * n)));
    const c = candles[i];
    const d = new Date(c.t);
    tip.innerHTML = `<b>${d.toISOString().slice(0, 16).replace('T', ' ')} UTC</b><br>` +
      `O ${fmtNum(c.o)} H ${fmtNum(c.h)}<br>L ${fmtNum(c.l)} C <span style="color:${c.c >= c.o ? '#16c784' : '#ea3943'}">${fmtNum(c.c)}</span><br>Vol ${fmtVol(c.v)}`;
    tip.style.display = 'block';
    const wrap = canvas.parentElement.getBoundingClientRect();
    tip.style.left = Math.min(wrap.width - 200, x + 14) + 'px';
    tip.style.top = (y - 30) + 'px';
  });
  canvas.addEventListener('mouseleave', () => { tip.style.display = 'none'; });

  // legend
  ctx.font = 'bold 10px ui-monospace,monospace';
  ctx.textAlign = 'left';
  const lg = (color, label, x) => { ctx.fillStyle = color; ctx.fillText(label, x, plotT + 4); };
  lg('rgba(59,130,246,.9)', 'EMA20', plotL);
  lg('rgba(168,85,247,.9)', 'EMA50', plotL + 58);
  lg('rgba(240,185,11,.8)', 'VWAP', plotL + 118);
  lg('rgba(45,212,191,.95)', 'ST', plotL + 172);
}

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
