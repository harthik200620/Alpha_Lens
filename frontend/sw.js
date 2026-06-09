/*
 * Alpha Lens service worker
 *
 * Strategy:
 *   - Static assets (stocks.js, fonts, icons)  -> cache-first
 *   - Navigation (HTML)                         -> network-first, cached fallback
 *   - API calls                                 -> network-first with cache fallback
 *                                                  (stale-while-revalidate flavor)
 *   - Anything else                             -> network passthrough
 *
 * Cache versioning: bump CACHE_VERSION on every deploy so old assets get
 * purged. The page registers the SW with `updateViaCache: 'none'` so the
 * browser always fetches a fresh /sw.js, picking up version bumps.
 *
 * Offline shell: if the user is offline and visits /, we serve the cached
 * homepage HTML so the UI shell still renders. Then individual API calls
 * may fail with cached responses where available.
 */

// Bump on every deploy that changes static assets. Activate handler purges
// any cache whose key doesn't start with this version, so stale CSS/JS from
// the previous deploy are evicted automatically.
const CACHE_VERSION = 'al-v38-2026-06-10-fnoout';
const STATIC_CACHE  = `${CACHE_VERSION}-static`;
const API_CACHE     = `${CACHE_VERSION}-api`;
const HTML_CACHE    = `${CACHE_VERSION}-html`;

// Assets we want available offline. /stocks.js intentionally NOT pre-cached
// because T2.7 made it lazy — pre-caching it here would defeat that win.
// app-core.js (chunk 1/9) IS pre-cached: it's tiny, runs on every page load,
// and pre-caching it means repeat visits skip the network for the bootstrap
// chunk entirely. The other 8 chunks fall back to cache-first via
// isStaticAsset() so they still get cached on first visit, just not eagerly.
const STATIC_PRECACHE = [
  '/manifest.json',
  '/app-core.js',
  // App-shell: precache the homepage HTML so a repeat visit on a slow/flaky
  // network (or a just-woken Render instance) paints the nav + skeleton
  // INSTANTLY from cache while htmlNetworkFirst() still revalidates against the
  // network in the background (network-first semantics unchanged — fresh
  // content wins whenever the network responds in time).
  '/',
];

// ── Install: warm the static cache ────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(STATIC_CACHE);
    try { await cache.addAll(STATIC_PRECACHE); } catch (e) { /* not fatal */ }
    // Activate this SW immediately on first install
    await self.skipWaiting();
  })());
});

// ── Activate: clean up old version caches ─────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys
        .filter((k) => !k.startsWith(CACHE_VERSION))
        .map((k) => caches.delete(k))
    );
    // Take control of every open page right away (so the user gets the new
    // SW behavior without needing to refresh twice).
    await self.clients.claim();
  })());
});

// ── Helpers ───────────────────────────────────────────────────────────────
function isStaticAsset(url) {
  return (
    url.pathname === '/stocks.js' ||
    // Extracted-from-index frontend chunks — cache-first since they change
    // only when we redeploy (HTML revalidates first, which would pull a new
    // version reference if these ever get versioned filenames). app.js was
    // split into ordered app-*.js chunks; match all of them.
    url.pathname === '/app.js' ||
    /^\/app-(core|news|stocks|market|premium|terminal|earnings|ripple|macro|fno|calendar|filings|glossary)\.js$/.test(url.pathname) ||
    url.pathname === '/styles.css' ||
    url.pathname === '/tailwind.built.css' ||
    url.pathname === '/manifest.json' ||
    url.pathname.endsWith('.svg') ||
    url.pathname.endsWith('.png') ||
    url.pathname.endsWith('.jpg') ||
    url.hostname === 'fonts.gstatic.com' ||
    url.hostname === 'fonts.googleapis.com' ||
    url.hostname === 'cdnjs.cloudflare.com'
  );
}

function isApiCall(url) {
  return url.pathname.startsWith('/api/') &&
         // Never cache the WhatsApp webhook or debug-* endpoints
         !url.pathname.startsWith('/api/whatsapp/') &&
         !url.pathname.startsWith('/api/debug-');
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) {
    // Refresh in the background — stale-while-revalidate
    fetch(request).then((res) => {
      if (res && res.ok) cache.put(request, res.clone());
    }).catch(() => {});
    return cached;
  }
  const fresh = await fetch(request);
  if (fresh && fresh.ok) {
    try { cache.put(request, fresh.clone()); } catch (e) { /* opaque responses can throw */ }
  }
  return fresh;
}

async function networkFirstWithFallback(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const fresh = await fetch(request);
    if (fresh && fresh.ok) {
      try { cache.put(request, fresh.clone()); } catch (e) {}
    }
    return fresh;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw err;
  }
}

async function htmlNetworkFirst(request) {
  const cache = await caches.open(HTML_CACHE);
  try {
    const fresh = await fetch(request);
    if (fresh && fresh.ok) {
      try { cache.put(request, fresh.clone()); } catch (e) {}
    }
    return fresh;
  } catch (err) {
    const cached = await cache.match(request) || await cache.match('/');
    if (cached) return cached;
    // Bare-bones offline shell
    return new Response(
      '<!doctype html><meta charset=utf-8><title>Offline · Alpha Lens</title>'
      + '<body style="background:#050507;color:#ECEAE4;font-family:system-ui;padding:48px;text-align:center">'
      + '<h1 style="font-weight:900;letter-spacing:-0.03em">Offline</h1>'
      + '<p>Alpha Lens is offline. Reconnect and refresh.</p></body>',
      { headers: { 'Content-Type': 'text/html' } }
    );
  }
}

// ── Fetch: route every request through the strategy that fits ─────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;

  // Skip non-GET (POSTs to /api/portfolio-assistant, etc. should never cache)
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Skip cross-origin requests we don't recognize
  if (url.origin !== self.location.origin && !isStaticAsset(url)) return;

  if (request.mode === 'navigate' || (request.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(htmlNetworkFirst(request));
    return;
  }

  if (isStaticAsset(url)) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  if (isApiCall(url)) {
    event.respondWith(networkFirstWithFallback(request, API_CACHE));
    return;
  }

  // Default: passthrough (browser handles normally)
});

// ── Optional: respond to a "skip waiting" message from the page ───────────
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
