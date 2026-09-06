## D-public-icons — the icons follow the reader's door; the rest of the shell keeps the gate

**The problem.** D-id-as-credential split the app into two doors: `RA_ROOT_PATH` for everything
that needs an identity, `RA_PUBLIC_ROOT_PATH` for the run page and everything linked from it, so
the URL in a reader's address bar is the URL they can send to someone. The `<head>` did not
follow. The three icon `<link>`s were emitted under `RA_ROOT_PATH` along with the manifest, so on
a deployment that gates the two prefixes separately — Cloudflare Access on `/app/`, nothing on
`/` — a stranger's browser resolves `/app/static/icons/favicon.svg` and is bounced by the edge.
The app would have refused it too: the icon route sat behind the same middleware as every other
shell asset, so moving the href alone would only have relocated the `403` from the edge into the
app.

[authentication.md](../authentication.md) recorded this as an accepted caveat ("you just get a
default tab icon"). It reads differently once a public page is the *normal* way a report is seen:
a shared run page is the artifact this system exists to hand to someone, and it arrives without
the mark that says whose it is. The deployment described in the originating issue showed the
default tab icon with no error in the page itself, and the workaround it provoked downstream was
an nginx `sub_filter` rewriting the hardcoded path back out of the HTML, which is a rule that
breaks quietly the day the asset is renamed.

**Decision.** Two halves, and neither works alone:

1. **The icon `<link>` hrefs are emitted from `public_base`**, the same base the run page's own
   links, its stream and the submit redirect already use. Unset — dev, the tailnet, any
   single-door deployment — `public_base` falls back to `base_path`, so every byte of every page
   is what it was; the "no prefix" and "one door" cases stay byte-identical assertions rather than
   accidents (D-base-path).
2. **`GET /static/icons/<name>` answers an anonymous caller.** A second public prefix beside
   `/runs/`, method-scoped exactly like it, matched against the same already-stripped path.

**Why the icons and not the shell.** The manifest and the service worker stay gated, and that is
not inertia — installability is a signed-in affordance. A manifest is fetched with credentials
omitted by default, which is why the link carries `crossorigin="use-credentials"`
(D-installable-pwa); a worker is persistent client-side execution on an interface whose only
authentication is a trusted header (D-identity-header). Neither is something to hand a stranger,
and neither is *visible* to one: the page renders identically without them. An icon is the
opposite on both counts — it is user-visible on every public page, and it is the artwork
committed in this repository. There is no run in it, no identity, no token cost, no request state
read by the handler, and a fixed five-entry table resolved at startup (`web/assets.py`) with
`Cache-Control: public, max-age=604800` already on it. What the exemption publishes is a picture
that ships in a public repository.

**What keeps it narrow.** `/static/icons/` is the whole of `/static/`; the prefix cannot acquire
a sibling route by accident, because the only handler under it indexes `ICON_TYPES` by name and
never builds a path from request data. `POST` to it is refused before routing, like every other
public read path. The enumeration guard D-id-as-credential put on the `/runs/` prefix gets a twin:
`tests/test_web.py::test_the_public_static_routes_are_the_expected_set` fails if a second route
ever appears under the icon prefix, so widening this is an edit with a test to update.

**The edge has to route it.** This is the one real cost, and it is a deployment change, not a code
one: on a two-door deployment the edge must route `/static/icons/` to the app path-preserving and
ungated, exactly as it already routes `/runs/`. Until it does, the icons 404 for *everyone*,
signed in or not, because the app now names one URL for them rather than two. One canonical URL
also avoids maintaining gated and public names for identical artwork, so the requirement is stated
in
[authentication.md](../authentication.md) and
[deployment-profile.md](../deployment-profile.md) beside the `/runs/` one it sits next to.

**What is deliberately left alone.** The service worker still precaches the icons under
`base_path`, not the public base. That preserves the existing installable shell as one gated unit:
the worker, its offline page, the manifest and the manifest's icon `src` values all remain under
`base_path`. The precache list is still the same fixed set with no run URL in it, so
D-installable-pwa's inclusion-allowlist property is untouched.

**Invariants.** No pipeline invariant is in reach: this is URL generation and one route's
authentication in the web layer, which is a window onto the audit trail and touches no model
context, no `OrchestratorView`, no author exclusion, no controller rule. The posture it does touch
is D-identity-header's "every route but `/healthz` needs an identity", already narrowed once by
D-id-as-credential, and it is narrowed the same way: by naming a surface that carries nothing
about anybody, not by relaxing the default for routes that do.
