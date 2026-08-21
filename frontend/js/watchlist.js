/* Watchlist — persisted locally (localStorage). Star any opportunity to follow
   it; a chip bar with live prices appears above the grid, and the ⭐ filter
   narrows the grid to watched coins only. */
'use strict';

const Watchlist = (function () {
  const KEY = 'dash-watch';
  let items = load();

  function load() {
    try {
      const s = localStorage.getItem(KEY);
      const a = s ? JSON.parse(s) : [];
      return Array.isArray(a) ? a.filter(x => typeof x === 'string') : [];
    } catch (e) { return []; }
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(items)); } catch (e) {} }
  function has(sym) { return items.indexOf(sym) !== -1; }
  function toggle(sym) {
    if (has(sym)) items = items.filter(x => x !== sym);
    else items.push(sym);
    save();
    return has(sym);
  }
  function list() { return items.slice(); }

  return { has, toggle, list };
})();
window.Watchlist = Watchlist;
