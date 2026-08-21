/* Service Worker — installable PWA with honest caching.
   DATA files (data/*.json) are ALWAYS network-first: the dashboard never
   silently serves stale market data. The cache is only a fallback for
   offline use. Static assets use stale-while-revalidate. */
'use strict';

const CACHE = 'dash-shell-v1.6.0';
const SHELL = [
  './',
  './css/style.css',
  './js/app.js',
  './js/i18n.js',
  './js/chart.js',
  './js/live.js',
  './js/alerts.js',
  './js/watchlist.js',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  const isData = url.pathname.includes('/data/');
  const isNav = event.request.mode === 'navigate';

  if (isData || isNav) {
    // network-first with cache fallback: never show stale data as live
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(event.request, copy)).catch(() => {});
          return res;
        })
        .catch(() =>
          caches.match(event.request).then((hit) => hit || (isNav ? caches.match('./') : Response.error()))
        )
    );
    return;
  }
  // static assets: stale-while-revalidate
  event.respondWith(
    caches.match(event.request).then((hit) => {
      const network = fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(event.request, copy)).catch(() => {});
          return res;
        })
        .catch(() => hit);
      return hit || network;
    })
  );
});
