/* Live price feed — Binance PUBLIC WebSocket streams (read-only, no keys).
   Prices update in real time and flash green/red on each change.
   Degrades gracefully: if WebSocket is unavailable or blocked, the cards keep
   showing the pipeline prices (updated every analysis cycle). */
'use strict';

const LivePrices = (function () {
  // data-stream.binance.vision is Binance's official public market-data host
  // for restricted regions; stream.binance.com is the global endpoint.
  const ENDPOINTS = ['wss://data-stream.binance.vision', 'wss://stream.binance.com'];
  let ws = null;
  let endpointIdx = 0;
  let active = false;
  let symbols = [];
  let prices = {};
  let timers = {};
  let retryDelay = 5000;
  let retryTimer = null;

  function setActive(v) {
    active = v;
    document.body.classList.toggle('ws-on', v);
  }

  function fmt(p) {
    return (typeof window.fmtPrice === 'function') ? window.fmtPrice(p) : String(p);
  }

  function updateDOM(sym, price, chgPct) {
    const prev = prices[sym];
    const dir = prev == null ? 0 : (price > prev ? 1 : price < prev ? -1 : 0);
    prices[sym] = price;
    const arrow = dir > 0 ? ' ▲' : dir < 0 ? ' ▼' : '';
    document.querySelectorAll('[data-live-sym="' + sym + '"]').forEach(el => {
      el.textContent = fmt(price) + arrow;
      if (dir !== 0) {
        el.classList.remove('lp-up', 'lp-down');
        void el.offsetWidth; // restart the flash animation
        el.classList.add(dir > 0 ? 'lp-up' : 'lp-down');
        clearTimeout(timers[sym]);
        timers[sym] = setTimeout(() => el.classList.remove('lp-up', 'lp-down'), 600);
      }
    });
    if (chgPct != null && isFinite(chgPct)) {
      document.querySelectorAll('[data-live-chg="' + sym + '"]').forEach(el => {
        el.textContent = (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + '%';
        el.style.color = chgPct >= 0 ? 'var(--green)' : 'var(--red)';
      });
    }
  }

  function connect() {
    if (typeof WebSocket === 'undefined') return; // no WS support -> stay passive
    if (!symbols.length) return;
    if (active) return; // already connected
    clearTimeout(retryTimer);
    const streams = symbols.map(s => s.toLowerCase() + '@miniTicker').join('/');
    const url = ENDPOINTS[endpointIdx % ENDPOINTS.length] + '/stream?streams=' + streams;
    let opened = false;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      scheduleRetry();
      return;
    }
    ws.onopen = () => { opened = true; setActive(true); retryDelay = 5000; };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      const d = msg && msg.data;
      if (!d || !d.s) return;
      const price = parseFloat(d.c);
      const open = parseFloat(d.o);
      if (!isFinite(price)) return;
      updateDOM(d.s, price, open > 0 ? (price - open) / open * 100 : null);
    };
    ws.onerror = () => { /* onclose handles retry */ };
    ws.onclose = () => {
      setActive(false);
      if (!opened) endpointIdx = (endpointIdx + 1) % ENDPOINTS.length; // try the other host
      scheduleRetry();
    };
  }

  function scheduleRetry() {
    if (retryTimer) return;
    setActive(false);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      retryDelay = Math.min(retryDelay * 1.7, 30000);
      connect();
    }, retryDelay);
  }

  function subscribe(syms) {
    const next = Array.from(new Set(['BTCUSDT'].concat(syms || [])));
    const changed = next.length !== symbols.length || next.some((s, i) => s !== symbols[i]);
    if (!changed) return;
    symbols = next;
    if (ws) {
      try { ws.close(); } catch (e) {}
      ws = null;
      retryTimer = null;
      retryDelay = 5000;
    }
    connect();
  }

  return {
    subscribe,
    isActive: () => active,
    currentPrice: sym => prices[sym],
  };
})();
window.LivePrices = LivePrices;
