/*
 * Service Worker for SPANZA Journal Watch
 *
 * Strategies:
 *   - Cache-first for immutable static assets (content-hashed by webpack/WhiteNoise)
 *   - Stale-while-revalidate for non-hashed static assets
 *   - Network-first for HTML pages (offline fallback to cache)
 *   - Network-only for backend, auth, HTMX, and non-GET requests
 */

const CACHE_VERSION = 'v1';
const STATIC_CACHE = `jw-static-${CACHE_VERSION}`;
const PAGES_CACHE = `jw-pages-${CACHE_VERSION}`;
const OFFLINE_URL = '/offline.html';
const PAGES_CACHE_LIMIT = 50;

// Matches WhiteNoise's WHITENOISE_IMMUTABLE_FILE_TEST
const IMMUTABLE_RE = /^\/static\/.+(?:[.-][0-9a-f]{8,64})\..+$/;

// Paths that must never be cached
const BYPASS_PREFIXES = [
  '/editorial/',
  '/admin/',
  '/accounts/',
  '/o/',
  '/tinymce/',
  '/markdownx/',
];

// ── Install ────────────────────────────────────────────────────────

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(PAGES_CACHE)
      .then((cache) => cache.add(OFFLINE_URL))
      .then(() => self.skipWaiting()),
  );
});

// ── Activate ───────────────────────────────────────────────────────

self.addEventListener('activate', (event) => {
  const keep = new Set([STATIC_CACHE, PAGES_CACHE]);
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names.filter((n) => !keep.has(n)).map((n) => caches.delete(n)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

// ── Fetch ──────────────────────────────────────────────────────────

self.addEventListener('fetch', (event) => {
  const { request } = event;

  // Only handle GET requests
  if (request.method !== 'GET') return;

  // Skip cross-origin requests (S3 media, CDN, external)
  if (new URL(request.url).origin !== self.location.origin) return;

  // Skip HTMX partial requests
  if (request.headers.get('HX-Request')) return;

  const path = new URL(request.url).pathname;

  // Skip backend/auth paths
  if (BYPASS_PREFIXES.some((prefix) => path.startsWith(prefix))) return;

  // Immutable static assets — cache-first
  if (IMMUTABLE_RE.test(path)) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  // Non-hashed static assets — stale-while-revalidate
  if (path.startsWith('/static/')) {
    event.respondWith(staleWhileRevalidate(request, STATIC_CACHE));
    return;
  }

  // HTML navigations — network-first with offline fallback
  if (
    request.headers.get('Accept') &&
    request.headers.get('Accept').includes('text/html')
  ) {
    event.respondWith(networkFirstWithOffline(request));
    return;
  }

  // Everything else — network-only (no respondWith, browser handles it)
});

// ── Strategies ─────────────────────────────────────────────────────

function cacheFirst(request, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response.ok) cache.put(request, response.clone());
        return response;
      });
    }),
  );
}

function staleWhileRevalidate(request, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(request).then((cached) => {
      const fetchPromise = fetch(request).then((response) => {
        if (response.ok) cache.put(request, response.clone());
        return response;
      });
      return cached || fetchPromise;
    }),
  );
}

function networkFirstWithOffline(request) {
  return fetch(request)
    .then((response) => {
      if (response.ok) {
        const clone = response.clone();
        caches.open(PAGES_CACHE).then((cache) => {
          cache.put(request, clone);
          trimCache(PAGES_CACHE, PAGES_CACHE_LIMIT);
        });
      }
      return response;
    })
    .catch(() =>
      caches
        .match(request)
        .then((cached) => cached || caches.match(OFFLINE_URL)),
    );
}

// ── Helpers ─────────────────────────────────────────────────────────

function trimCache(cacheName, maxItems) {
  caches.open(cacheName).then((cache) =>
    cache.keys().then((keys) => {
      if (keys.length > maxItems) {
        cache.delete(keys[0]).then(() => trimCache(cacheName, maxItems));
      }
    }),
  );
}
