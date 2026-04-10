/*
 * Service Worker for SPANZA Journal Watch
 *
 * This worker intentionally avoids HTML/document navigations and any
 * session-sensitive traffic. Django keeps full control of authenticated page
 * requests; the worker only accelerates same-origin static assets.
 *
 * Strategies:
 *   - Cache-first for immutable static assets (content-hashed by webpack/WhiteNoise)
 *   - Stale-while-revalidate for non-hashed same-origin /static/ assets
 *   - Network-only for everything else
 */

const CACHE_VERSION = 'v3';
const STATIC_CACHE = `jw-static-${CACHE_VERSION}`;

// Matches WhiteNoise's WHITENOISE_IMMUTABLE_FILE_TEST
const IMMUTABLE_RE = /^\/static\/.+(?:[.-][0-9a-f]{8,64})\..+$/;

self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  const keep = new Set([STATIC_CACHE]);
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => !keep.has(name))
            .map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;

  // Never interfere with navigations, non-GETs, or cross-origin traffic.
  if (request.method !== 'GET') return;
  if (request.mode === 'navigate') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  const path = url.pathname;
  if (!path.startsWith('/static/')) return;

  if (IMMUTABLE_RE.test(path)) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  event.respondWith(staleWhileRevalidate(request, STATIC_CACHE));
});

/**
 * Clone a response for caching, stripping Set-Cookie headers.
 *
 * The Cache API spec says Set-Cookie should be filtered on match, but Firefox
 * has had bugs where cached Set-Cookie headers are replayed to the browser,
 * overwriting the live cookie jar (e.g. the authenticated session cookie after
 * login).  Stripping before storage is defence-in-depth.
 */
function stripCookiesAndCache(cache, request, response) {
  const headers = new Headers(response.headers);
  headers.delete('Set-Cookie');
  const cleaned = new Response(response.clone().body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
  return cache.put(request, cleaned);
}

function cacheFirst(request, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response.ok) stripCookiesAndCache(cache, request, response);
        return response;
      });
    }),
  );
}

function staleWhileRevalidate(request, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(request).then((cached) => {
      const fetchPromise = fetch(request).then((response) => {
        if (response.ok) stripCookiesAndCache(cache, request, response);
        return response;
      });
      return cached || fetchPromise;
    }),
  );
}
