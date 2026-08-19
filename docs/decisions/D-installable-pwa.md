## D-installable-pwa — installable on a phone, without letting anything cacheable be wrong

**The problem.** The web interface was a page, not an app: no manifest, no icons, no way to keep
it on a home screen. Making it installable is mostly additive, but two parts of it are not, and
both touch a documented posture rather than just adding a route.

**The CSP had to change**, from

```
default-src 'none'; img-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'
```

to

```
default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; form-action 'self'; base-uri 'none'
```

`img-src 'none'` blocked the entire icon set: browsers enforce `img-src` on favicon and
manifest-icon fetches, so there is no version of this feature that keeps it. The property that
directive was protecting is nevertheless unchanged. It exists so that model-written report text
cannot trigger an outbound GET from the reader's browser on a tailnet — and that ban does not
live in the CSP. It lives one layer earlier, in `web/markdown.py`, which disables the `image`
rule outright and sets `html=False`, so a report *cannot express* an image in the first place.
The CSP was belt to that braces. What `'self'` newly permits is same-origin images the
*application* names in its own template, from an origin the reader's browser is already loaded
from. Off-origin fetches remain impossible. The renderer-level ban is now the load-bearing one,
so it carries its own assertion in `tests/test_web.py`; if images are ever re-enabled there, this
decision has to be revisited in the same change.

`manifest-src 'self'` and `worker-src 'self'` are additions rather than relaxations — both were
blocked by `default-src 'none'`, and `script-src 'unsafe-inline'` does not cover them, because
`'unsafe-inline'` permits inline blocks and not URLs. `script-src` gains `'self'` because Safari
has historically resolved worker scripts through `script-src`/`child-src` rather than
`worker-src`; adding a host-source does not disable `'unsafe-inline'`, which is dropped only in
the presence of a nonce or a hash. The whole policy is now pinned by an exact-match test, so
widening it further fails a test rather than passing quietly.

**A service worker is the first persistent client-side execution surface this project ships** —
code that keeps running after the tab closes, on an interface whose only authentication is a
trusted header (D-identity-header). Three
properties bound it:

1. **Its cache is an inclusion allowlist, not an exclusion list.** It precaches the icons, the
   manifest and one static offline page. `cache.put` appears in exactly one branch, reachable
   only for URLs in that fixed list. A run page, the `/runs/<id>/progress` fragment and the
   `/runs/<id>/stream` event log therefore cannot be cached *by construction* — not by a pattern
   that a later URL change could slip past. The stream is additionally never passed to
   `respondWith`, so the worker never sits between the browser and an open `text/event-stream`.
   `/runs/<id>` and `/runs/<id>/progress` also carry `Cache-Control: no-store`, because an
   installed standalone app leans on the HTTP cache and the back-forward cache far harder than a
   tab does and the rule has to hold at both layers. A finished run displayed as still running is
   the one output this interface must not produce.
2. **The cache key is a hash of the asset bytes**, not the package version — which has never been
   bumped and would therefore never invalidate anything. Replacing a placeholder icon changes the
   served `sw.js`, which is what makes every installed client fetch a new worker and drop the old
   cache. That is what makes "swap in your own artwork" a two-step operation rather than a
   support question.
3. **Registration is guarded by `isSecureContext`.** Reached over plain HTTP on a tailnet address
   the guard returns before anything can throw, and the page is byte-identical to a build without
   the feature. Installation is available only behind `tailscale serve`'s HTTPS, which is the
   intended posture anyway.

To withdraw the worker later, ship a `sw.js` whose body is `self.registration.unregister()`. Do
not simply delete the route: a worker already installed on a device outlives the deploy that
removed its source.

**Static files are served by an explicit URL→filename allowlist**, not a `StaticFiles` mount.
The mount would resolve a request string against a directory, which contradicts the rule recorded
at `web/app.py` that no code path in the web layer constructs a `Path` from request data. Here
the request string is only ever a dictionary key and every filesystem path is the static
directory joined with a literal, so traversal attempts are ordinary misses. It is also the only
way to set `Service-Worker-Allowed`, and it avoids depending on `mimetypes` knowing
`.webmanifest` — which a bare `python:3.12-slim` does not, and a manifest served as
`application/octet-stream` is ignored silently.

**Known residual:** the manifest's `background_color` is the light palette's. A manifest has no
media-query mechanism and the OS caches it at install time, so the Android splash frame is light
even in dark mode. Serving the manifest dynamically would fix one frame at the cost of making it
non-static; not worth it.
