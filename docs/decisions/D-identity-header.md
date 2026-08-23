## D-identity-header — the interface has users: a trusted identity header, and runs that belong to someone

Every prior version of this document says there is no authentication and that Tailscale ACLs are
the access control. That was true and deliberate for a single operator. Opening the interface to
friends makes it false in a way that matters: without a user concept, everyone who reaches the
app shares one index onto everyone's questions, seed material and audit trails.

**Decision.** Identity comes from a request header set by whatever fronts the app —
`Cf-Access-Authenticated-User-Email` from Cloudflare Access, or the `Tailscale-User-*` headers
D-bounded-submission already read — and every route but `/healthz` refuses a request that carries none. Runs
record their submitter in `owner.txt`. The index is owner-scoped.

**The header is trusted, not verified — and that is the accepted risk.** Cloudflare Access also
sends a signed `Cf-Access-Jwt-Assertion` that could be checked against the team's JWKS with an
`aud` claim, which is the real boundary. It is not implemented here. Cloudflare strips and
rewrites `Cf-Access-*` on everything it proxies, so *through the tunnel* the email header is
authoritative; the exposure is that the tailnet path is deliberately kept open, so any tailnet
peer that can reach the port can set the header to any value and read or submit as that person.
At the scale this serves — a handful of invited people, on a tailnet the operator controls —
that is a trade taken knowingly, and revisiting it is the stated condition for exposing the
service more broadly. All of it is confined to `web/identity.py:resolve_identity`, so verifying
the JWT is a change to one function.

**Access is preferred over Tailscale, and every source is normalized identically.** Both
headers are trusted equally; Access is checked first because it is how friends arrive. The
operator reaches the app by both doors, so the same person must resolve to one identity either
way — every source is lower-cased, and a value that is blank, over 320 characters, or carries
control characters is treated as absent rather than truncated into an ownership key its own
submitter could never match. Only `Tailscale-User-Login` is read; `Tailscale-User-Name` was
fine as D-bounded-submission's rate-limit key, where any *stable* string worked, but an ownership key must be
the *same* string the other door produces, and a display name is a different namespace from an
address. What normalization cannot fix is a tailnet whose identity provider reports a different
address than the Access policy lists — that is two people as far as this system can tell, and
the check is to sign in each way and compare the *signed in as* line.

**Enforcement is middleware, not a call per route.** `_reject_cross_site` is invoked by hand at
the top of each mutating handler, and that idiom is right for CSRF — it is a property of two
specific routes. Authentication is a property of the app, and the failure mode of an opt-in
check is a future route that forgets it. The middleware is the only fail-closed shape.

**`/healthz` stays the only exemption, including for D-installable-pwa's app shell.** The manifest, service
worker, offline page and icons are static files that hold nothing private, so exempting them
would have been defensible — and it is still declined, because an exemption list is a thing that
grows and every future entry is argued against a precedent rather than against this decision. The
price is paid in the `<head>` instead: a manifest is the one subresource a browser fetches with
credentials *omitted* by default, even same-origin, so the link carries
`crossorigin="use-credentials"`. Without it the fetch reaches Access with no `CF_Authorization`
cookie and is bounced at the edge — where an app-level exemption could not have helped anyway —
and the only symptom is that the app quietly stops being installable. The container smoke test
asserts both halves: `/` with no header is a 403, and the shell is there once a header is set.

> Superseded in part by **D-id-as-credential**, which serves every `GET` under `/runs/` without an identity. The
> reasoning above is why that is a method-scoped rule with a route-table test rather than a second
> entry in `_UNAUTHENTICATED_PATHS` — which still holds `/healthz` alone. The app shell stays gated
> exactly as argued here.

**`auth.dev_identity` is the single knob, and its unset state is the safe one.** Set (via the
roster or `$RA_DEV_IDENTITY`), it supplies an identity to requests with no header, which is what
local development needs; unset, such a request is refused. A boolean `require_auth` alongside it
would have been two settings that can disagree, and the disagreeing combination fails open.

**Ownership scopes the index; it does not scope reads.** You see your own runs listed. Anyone
signed in who holds a run id can read that run — sharing a link is the intended way to show
someone a report, with export/publish to follow. Resume is the one exception: reading costs
nothing, but resuming spends the owner's tokens for another 10–25 minutes, so it stays with the
person who started it.

> **D-id-as-credential** kept this and dropped the "signed in": holding the id is the whole credential. Resume
> stays owner-only but loses its button, since a page served without an identity cannot tell an
> owner from a stranger.

**A run with no owner is served to nobody.** Runs written before this decision, and CLI runs
started without `ra run --owner`, have no identity to attribute and none can be invented for
them. They are 404 over HTTP — not listed, not readable, not resumable by hand — while remaining
untouched on disk and through the CLI. There is deliberately no backfill: guessing an owner is
how a stranger's run ends up in someone's index. Boot recovery is unaffected, because an
interrupted run is work already owed and whether anyone can currently *see* it has no bearing on
whether it should finish. `owner.txt` sits outside `CONTENT_DIRS` so that a retention sweep
cannot silently retire a run from its owner's index.

**D-bounded-submission's rate limiter is unchanged in mechanism and stronger in effect.** Its key was already the
identity header; the difference is that there is no longer a shared `global` bucket to spill
into, because an unauthenticated request never reaches the queue. The CSRF guard also matters
more than it did: Access sets a `CF_Authorization` cookie, so a cross-site form POST would now
ride an authenticated session, and `Sec-Fetch-Site` is what refuses it.

**Isolation is untouched.** This is entirely upstream of run creation and moves no new data
toward any model context. `owner` deliberately stays out of `_run_fingerprint`: the fingerprint
guards against a run resuming under changed *inputs*, and attributing a run must never cost it
its checkpoint. The `seed.allow_url` rationale changes slightly — authentication narrows *who*
can make the server fetch a URL, but not what the host can reach, so the egress boundary in
[ssrf-egress-isolation.md](../ssrf-egress-isolation.md) remains the prerequisite it was.

Deployment is documented in [authentication.md](../authentication.md).
