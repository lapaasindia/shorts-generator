// Bump this version whenever the cached shell should be invalidated.
const CACHE_NAME = 'shorts-console-v2';
const ASSETS_TO_CACHE = [
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/manifest.json'
];

// Install — pre-cache only static media (NOT html/js/css, which change often).
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(ASSETS_TO_CACHE))
      .then(() => self.skipWaiting())
  );
});

// Activate — drop any old caches so stale shells can't survive a deploy.
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.map((c) => (c !== CACHE_NAME ? caches.delete(c) : null)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Network-only for everything dynamic: APIs, auth, media, job status.
  const dynamic = url.pathname.startsWith('/api/')
    || url.pathname.startsWith('/oauth/')
    || url.pathname.startsWith('/media/')
    || url.pathname === '/login'
    || url.pathname === '/logout'
    || url.pathname === '/register';
  if (dynamic) {
    return; // let the browser handle it normally
  }

  // Network-first for the app shell (/, app.js, styles.css) so code updates
  // always load; fall back to cache only when offline.
  const isShell = url.pathname === '/'
    || url.pathname === '/static/app.js'
    || url.pathname === '/static/styles.css';
  if (isShell) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Cache-first for immutable static media (icons, manifest).
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
