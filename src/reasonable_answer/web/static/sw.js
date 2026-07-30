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
// Substituted alongside ASSETS so it always matches the precached entry, prefix and all:
// behind a stripping proxy the browser requests `/app/offline.html`, so the navigate
// fallback below has to look that URL up in the cache, not the bare `/offline.html`.
var OFFLINE = '__RA_OFFLINE__';
// Artwork for a push notification. Substituted like OFFLINE so it carries the base path,
// and chosen from the precache list so a notification shown offline still has its icon.
var ICON = '__RA_ICON__';

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

// ---------------------------------------------------------------- notifications

// A run stops and the server pushes here (D-stop-notification). Neither handler below touches `caches`,
// so the invariant at the top of this file is unaffected: `cache.put` still appears in
// exactly one branch, reachable only for URLs in ASSETS.

self.addEventListener('push', function (event) {
  // Chrome enforces `userVisibleOnly`: every push must result in a visible notification,
  // and if this handler throws the browser substitutes its own "site updated in the
  // background" notice. So the parse is defensive and there is always a fallback — a
  // vague notification is bad, the browser's generic one is worse.
  var payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    payload = {};
  }
  var title = payload.title || 'reasonable-answer';
  event.waitUntil(
    self.registration.showNotification(title, {
      body: payload.body || 'A run has finished.',
      // Reuses the installed app's icon rather than a notification-specific asset; the
      // monochrome status-bar glyph Android would also like is not shipped yet, and its
      // absence costs a generic dot, not a broken notification.
      icon: ICON,
      badge: ICON,
      // The run id, so a second push about the same run replaces the first instead of
      // stacking. Queue five questions and you want five notifications, not five per run.
      tag: payload.tag || 'ra-run',
      data: { url: payload.url || './' }
    })
  );
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var target = (event.notification.data && event.notification.data.url) || './';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      // Focus a tab already showing this run before opening another one: the common case
      // is the phone's installed app still parked on the page it was left on.
      for (var i = 0; i < list.length; i++) {
        if (list[i].url.indexOf(target) !== -1 && 'focus' in list[i]) {
          return list[i].focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
