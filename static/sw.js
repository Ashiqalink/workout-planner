// Offline cache for FitTrack, controlled by the advanced.offline_cache setting.
//
// Two strategies, chosen by what the request is for:
//
//   assets (CSS, JS, fonts, the icon CDN)  cache-first — they are versioned by
//        URL, so a hit is always correct and costs no network round trip.
//   pages and API reads                    network-first with a cache fallback,
//        because a stale dashboard is worse than a slow one, but an offline
//        dashboard is better than an error page.
//
// Nothing that changes state is ever cached: POSTs pass straight through, so
// the worker can never replay or swallow a saved session.

const VERSION = 'fittrack-v2';
const SHELL = [
    '/static/css/style.css',
    '/static/js/app.js',
    '/static/js/palette.js',
    '/static/manifest.json'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(VERSION)
            // addAll rejects the whole install if any single URL fails; add
            // them individually so one missing file cannot break the worker.
            .then(cache => Promise.all(SHELL.map(url => cache.add(url).catch(() => null))))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

function isAsset(url) {
    return url.pathname.startsWith('/static/') ||
           url.hostname === 'fonts.gstatic.com' ||
           url.hostname === 'fonts.googleapis.com' ||
           url.hostname === 'unpkg.com';
}

self.addEventListener('fetch', event => {
    const request = event.request;
    if (request.method !== 'GET') return;               // never touch writes

    const url = new URL(request.url);

    // Downloads and the health check should always hit the network.
    if (url.pathname.startsWith('/api/data/export') || url.pathname === '/api/health') return;

    if (isAsset(url)) {
        event.respondWith(
            caches.match(request).then(hit => hit || fetch(request).then(response => {
                if (response && response.status === 200) {
                    const copy = response.clone();
                    caches.open(VERSION).then(cache => cache.put(request, copy));
                }
                return response;
            }))
        );
        return;
    }

    if (url.origin !== self.location.origin) return;

    event.respondWith(
        fetch(request)
            .then(response => {
                if (response && response.status === 200 && response.type === 'basic') {
                    const copy = response.clone();
                    caches.open(VERSION).then(cache => cache.put(request, copy));
                }
                return response;
            })
            .catch(() => caches.match(request).then(hit => hit || caches.match('/dashboard')))
    );
});
