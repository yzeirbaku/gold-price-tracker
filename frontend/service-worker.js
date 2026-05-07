// Minimal service worker — required for iOS PWA install but does no caching.
// The tool is online-only by design (live scrapes), so caching API responses is undesirable.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => { /* let browser handle every request */ });
