// Service Worker for offline support
const CACHE_NAME = 'japan-trip-v81';
const STATIC_ASSETS = [
    '/static/css/base.css',
    '/static/css/layout.css',
    '/static/css/components.css',
    '/static/css/dark.css',
    '/static/css/dashboard.css',
    '/static/css/day.css',
    '/static/css/calendar.css',
    '/static/css/accommodations.css',
    '/static/css/checklists.css',
    '/static/css/activities.css',
    '/static/css/chat.css',
    '/static/css/documents.css',
    '/static/js/app.js',
    '/static/js/itinerary.js',
    '/static/js/checklists.js',
    '/static/js/touch.js',
    '/static/js/celebrate.js',
    '/static/js/accommodations.js',
    '/static/js/documents.js',
    '/static/js/calendar.js',
    '/static/manifest.json',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    'https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css',
];

// Pages to pre-cache for offline access
const PAGE_URLS = [
    '/',
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache =>
            cache.addAll([...STATIC_ASSETS, ...PAGE_URLS])
        )
    );
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // Skip non-GET requests and API calls
    if (event.request.method !== 'GET') return;
    if (url.pathname.startsWith('/api/')) return;
    if (url.pathname.startsWith('/chat')) return; // Chat needs live connection

    // Static assets: network-first (ensures fresh files after deploys)
    if (url.pathname.startsWith('/static/') || url.hostname !== location.hostname) {
        event.respondWith(
            fetch(event.request).then(response => {
                const clone = response.clone();
                caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
                return response;
            }).catch(() => caches.match(event.request))
        );
        return;
    }

    // HTML pages: network-first, fall back to cache
    event.respondWith(
        fetch(event.request).then(response => {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
            return response;
        }).catch(() =>
            caches.match(event.request).then(cached => {
                if (cached) return cached;
                // Final fallback: show cached homepage
                return caches.match('/');
            })
        )
    );
});
