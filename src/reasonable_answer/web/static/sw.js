// The service worker. Served from `/sw.js` so its scope is the whole origin; the version
// and the precache list below are substituted by `web/assets.py` as it is served.
//
// The rule this file exists to keep: a run page, the progress fragment and the event
// stream are live data and must never come out of a cache. That is enforced structurally
// rather than by pattern-matching — `caches.put` appears in exactly one branch, and that
// branch is reachable only for URLs in ASSETS, which is a fixed list of icons, the
// manifest and the offline page. There is no path through this file that can store a
// response for anything else, so no future edit to a URL scheme can accidentally start
// caching run state.
//
// Everything here is ES5-shaped on purpose: it matches the inline scripts in render.py,
// and a service worker that fails to parse fails silently.

var VERSION = '__RA_CACHE_VERSION__';
var CACHE = 'ra-' + VERSION;
var ASSETS = __RA_PRECACHE__;
var OFFLINE = '/offline.html';

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(ASSETS);
    }).then(function () {
      // Take over immediately. The alternative — waiting for every tab to close — means a
      // broken worker can outlive the deploy that fixed it.
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (key) {
        return key === CACHE ? null : caches.delete(key);
      }));
    }).then(function () {
      return self.clients.claim();
    })
  );
});

// Network first, always. The cache is a fallback for being offline, never a shortcut.
function assetFirstFromNetwork(request) {
  return fetch(request).then(function (response) {
    if (response && response.ok) {
      var copy = response.clone();
      caches.open(CACHE).then(function (cache) {
        cache.put(request, copy);
      });
    }
    return response;
  }).catch(function () {
    return caches.match(request);
  });
}

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;

  var url;
  try {
    url = new URL(request.url);
  } catch (e) {
    return;
  }
  if (url.origin !== self.location.origin) return;

  if (ASSETS.indexOf(url.pathname) !== -1) {
    event.respondWith(assetFirstFromNetwork(request));
    return;
  }

  if (request.mode === 'navigate') {
    // The original Request is passed through untouched so its redirect mode still applies
    // — submitting a question answers with a 303 that the browser has to follow itself.
    // The response is never cached: a page showing a finished run as still running is the
    // one output this interface must not produce.
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(OFFLINE);
      })
    );
    return;
  }

  // Everything else — including the event stream — is left to the browser. Returning
  // without calling respondWith is what keeps this worker out of the middle of a
  // long-lived `text/event-stream` response, which it would otherwise risk buffering.
});
