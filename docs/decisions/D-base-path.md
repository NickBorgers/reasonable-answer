## D-base-path — servable under a URL base path, without relaxing the same-origin posture

**The problem.** RA was a root-origin app: every URL it emitted was root-absolute
(`/static/*`, `/manifest.webmanifest`, `href="/runs/<id>"`, `action="/runs"`, the brand
`/`, the `/runs/<id>/stream` event source) and the service worker registered at `/sw.js`
with scope `/`. A reverse proxy that relocates the app under a stripped prefix — the
Cloudflare Access shape, `location /app/ { proxy_pass http://ra:8080/; }`, where the bare
`/` has to be a real public page and the auth-gated app lives deeper — therefore could not
hold the app under that prefix. Every link and the live stream escaped back to the origin
root, past the Access policy scoped to `/app`.

**The mechanism.** One env, `RA_ROOT_PATH`, normalized once at startup by
`normalize_base_path` to `''` or `'/seg[/seg…]'` (no trailing slash), and joined to every
emitted URL as `base + "/…"`. **The empty string is the join identity** — `"" + "/runs"` is
`/runs` — so an unset env leaves every byte of every page identical to the root-origin
build, which is what lets the whole existing web test suite stand unchanged and pins the
"no prefix" case as a real assertion rather than an accident. The prefix is prepended in
exactly the places a browser resolves against the origin: the server-rendered links and
form actions, the `303` `Location` after a submit or resume, the manifest's `id`,
`start_url`, `scope` and icon `src`s, the worker's precache list, its `OFFLINE` fallback and
its registration scope, the `Service-Worker-Allowed` header, and — when refinement is
enabled (D-question-refinement) — the `fetch()` the inline refinement script issues to `/refine`. That last
one is a browser-origin URL like the rest and carries the prefix for the same reason; it was
missed when D-question-refinement and D-base-path landed in separate PRs and is corrected in PR #66.

**A stripping proxy, so the routes do not move.** The proxy removes `/app/` before the
request arrives, so the app still serves at `/runs`, `/sw.js`, `/manifest.webmanifest`. The
base path shapes only what the app *emits*, never what its router *matches*. This is the
ASGI `root_path` convention, but `FastAPI(root_path=…)` is deliberately **not** set: nothing
here reads `scope["root_path"]`, routing matches the already-stripped path, and URL
generation is explicit, so setting it would add a second, silent mechanism that could
disagree with the explicit one.

**Why an env and not `X-Forwarded-Prefix`.** The manifest and the service worker are
resolved to bytes once at startup (D-installable-pwa: "read once, at startup"), with the worker's cache
version hashed over the precached URLs. Reading the prefix from a per-request header would
force those to be rebuilt per request, or cached per distinct header value — turning a
static, hashed artifact into a request-varying one. A single startup value keeps D-installable-pwa's
"these files do not change while the process runs" true. One process serves one prefix;
that is the residual, and it matches the one-deployment-one-mount reality.

**The CSP does not change, and that is the point.** Every URL the app *fetches from or
submits to* stays same-origin, so `connect-src 'self'` / `form-action 'self'` /
`base-uri 'none'` are exactly as D-installable-pwa pinned them, and that test stays green. (There is a
single off-origin URL the app emits — the static "how this works" navigation link to the
published docs site, added later. It is an anchor `href`, not a subresource fetch or a form
submit, so no CSP directive here governs it; it carries `rel="noreferrer"` so following it
from a per-run page hands no run id to that host. The same-origin guarantee this section
makes is therefore about the URLs the browser *resolves against the origin* — links back
into the app, redirects, streams, the manifest and the worker — not this one outbound
navigation link.) `base-uri 'none'` also forecloses the obvious shortcut — a
single `<base href="/app/">` tag — so each URL is prefixed individually instead. The prefix
is the application naming its own same-origin paths, which is what `'self'` already permits;
it opens nothing.

**What D-installable-pwa's three service-worker properties cost.** All three hold under a prefix. The
cache is still an inclusion allowlist: the precache list is the same fixed set of icons,
manifest and offline page, now carrying the prefix, and still contains no run URL — the
`no /runs in the worker` assertion is unchanged. The cache key is still a hash of the asset
bytes, now with the prefixed URL as each entry's name, so two deployments at different
prefixes key their caches distinctly, which is correct. Registration is still
`isSecureContext`-guarded. `Service-Worker-Allowed` becomes `/app/` because a worker served
(as the browser sees it) from `/app/sw.js` must be allowed to claim `/app/`.

**Invariants.** None of the pipeline invariants are in reach: this is URL generation in the
web layer, which is a window onto the audit trail and touches no model context, no
`OrchestratorView`, no author-exclusion, no controller rule. The web-layer posture this
does touch is D-installable-pwa's, and it is generalized, not relaxed: "served from the root so its scope
is the whole origin" becomes "served from the mount point so its scope is the app," with the
root case as `base = ''`.
