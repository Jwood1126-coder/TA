// Service Worker for offline support
const CACHE_NAME = 'japan-trip-v114';
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

// Handle notification clicks — open the Gmail sync page
self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
            // Focus existing tab if open
            for (const client of windowClients) {
                if (client.url.includes('/gmail-sync') && 'focus' in client) {
                    return client.focus();
                }
            }
            // Otherwise open new tab
            return clients.openWindow('/gmail-sync');
        })
    );
});

// Handle messages from the page (for showing notifications)
self.addEventListener('message', event => {
    if (event.data && event.data.type === 'GMAIL_CHANGES') {
        const count = event.data.count || 0;
        if (count > 0) {
            self.registration.showNotification('Japan Trip — Gmail Sync', {
                body: `${count} new change${count > 1 ? 's' : ''} from email — tap to review`,
                icon: '/static/icons/icon-192.png',
                badge: '/static/icons/icon-192.png',
                tag: 'gmail-sync',
                renotify: true,
            });
        }
    }
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

    // Gmail sync status polling — let through to network
    if (url.pathname === '/api/gmail/status') return;

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
