/**
 * AlgoVibe Service Worker
 *
 * Strategy:
 *  - Static assets (JS/CSS/HTML bundles): Cache-first for performance.
 *  - API calls (/api/*): Network-first; fall back to cache when offline.
 *  - Socket.IO (/socket.io/*): Always network — never cache WS handshakes.
 */

const CACHE_VERSION = 'av-v6'
const STATIC_CACHE  = `${CACHE_VERSION}-static`
const API_CACHE     = `${CACHE_VERSION}-api`

const STATIC_EXTENSIONS = ['.js', '.css', '.woff2', '.woff', '.ttf', '.png', '.ico', '.svg', '.webp']

// ── Install ───────────────────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  // Pre-cache the shell immediately
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      cache.addAll(['/', '/manifest.json'])
    ).then(() => self.skipWaiting())
  )
})

// ── Activate (clear stale caches) ────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k.startsWith('av-') && k !== STATIC_CACHE && k !== API_CACHE)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  )
})

// Allow the page to force immediate activation for new SW versions.
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting()
  }
})

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)

  // Never intercept socket.io, non-GET, or cross-origin requests —
  // return early without calling respondWith so the browser handles them natively.
  if (
    request.method !== 'GET' ||
    url.pathname.startsWith('/socket.io') ||
    url.origin !== self.location.origin
  ) {
    // Explicitly fall through to browser default fetch (no respondWith)
    return
  }

  // API routes — network first, short-lived cache fallback
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirstApi(request))
    return
  }

  // Static assets — cache first
  const isStatic = STATIC_EXTENSIONS.some((ext) => url.pathname.endsWith(ext))
  if (isStatic) {
    event.respondWith(cacheFirstStatic(request))
    return
  }

  // HTML navigation — network first (SPA shell)
  event.respondWith(networkFirstShell(request))
})

// ── Strategies ────────────────────────────────────────────────────────────────

async function cacheFirstStatic(request) {
  const cached = await caches.match(request)
  if (cached) return cached
  const response = await fetch(request)
  if (response && response.ok) {
    const cache = await caches.open(STATIC_CACHE)
    cache.put(request, response.clone())
  }
  return response
}

async function networkFirstApi(request) {
  try {
    const response = await fetch(request)
    if (response && response.ok) {
      const cache = await caches.open(API_CACHE)
      cache.put(request, response.clone())
    }
    return response
  } catch {
    const cached = await caches.match(request)
    return cached || new Response(JSON.stringify({ error: 'offline' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}

async function networkFirstShell(request) {
  try {
    const response = await fetch(request)
    if (response && response.ok) {
      const cache = await caches.open(STATIC_CACHE)
      cache.put(request, response.clone())
    }
    return response
  } catch {
    return caches.match('/') || caches.match(request)
  }
}
