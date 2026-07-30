# Deployment profile

This page records **how the production instance is actually configured**, as distinct from what
the repository ships. It is descriptive, not normative: the committed defaults in
`config/roster.default.yaml` remain the contract, and nothing here licenses changing them. It
exists because the shipped defaults leave every credentialled affordance off, so an agent
reasoning about runtime behavior from the repository alone will conclude that source verification
is inert — and in production it is not.

Mechanism lives elsewhere. [authentication.md](./authentication.md) explains how identity works;
[ssrf-egress-isolation.md](./ssrf-egress-isolation.md) explains the network boundary this profile
depends on. This page only states which switches are thrown.

## Shape

One host, one Docker Compose service (`compose.yaml`), built from the checkout rather than pulled
by digest — the running code is the working tree. The container is `read_only: true` with
`cap_drop: ALL`, `no-new-privileges`, a `tmpfs` at `/tmp`, and exactly one writable volume at
`/data/runs` holding the audit trail and the SQLite checkpoints resumability depends on. The port
is published on `127.0.0.1:8080` only; reachability comes from the edge in front of it.

Two doors, split by prefix: `RA_ROOT_PATH` gates the application, `RA_PUBLIC_ROOT_PATH` serves the
shareable `/runs/` reads. The edge must route `/runs/` path-preserving for that split to hold.

## Inbound authentication

The application performs **no cryptographic verification of the caller**. It trusts a header set
by the edge, in this order:

| header | set by |
|---|---|
| `Cf-Access-Authenticated-User-Email` | Cloudflare Access, via a `cloudflared` tunnel |
| `Tailscale-User-Login` | `tailscale serve` on the tailnet |

`resolve_identity()` lowercases the value, bounds it, rejects control characters, and returns it as
both the run owner and the rate-limit key. Enforcement is HTTP middleware, so a request with no
identity gets a bare `403` before routing. Two exemptions: `/healthz`, and **every `GET` under
`/runs/`**, which is anonymous by design (D35 — holding the run id is the credential). `POST` to a
public read path is still refused.

The consequence worth internalizing: **the security boundary is the deployment, not the code.**
Anyone who can open a TCP connection to the published port can claim any identity. Binding to
loopback and putting Access in front is what makes header trust safe. `auth.dev_identity` /
`RA_DEV_IDENTITY` is a local-development escape hatch and is unset in production.

## Outbound authentication

All inference goes through a single OpenAI-compatible **LiteLLM proxy** on the tailnet. There are
no provider SDKs and no per-provider keys in the application.

| variable | purpose |
|---|---|
| `RA_PROXY_BASE_URL` | overrides `proxy.base_url`; env beats file beats built-in default |
| `LITELLM_API_KEY` | bearer token for the proxy; falls back to `fake-key`, because the proxy is protected by tailnet ACL rather than by key |
| `RA_CONTACT_EMAIL` | the address Crossref and Unpaywall want for their polite pool; unset is a warning, not an error, and skips Unpaywall |
| `BRAVE_SEARCH_API_KEY` | web search |
| `CORE_API_KEY` | the CORE open-access provider |
| `FIRECRAWL_API_KEY` | the extraction provider |

Startup is fail-closed: the proxy's `/model/info` resolves each alias to a concrete
`provider/model` (author exclusion is enforced at that resolved identity, never at the alias), and
a credential missing for an enabled tier aborts the boot rather than failing twenty minutes into a
run. For every credential, an environment variable beats the on-disk token file — and in the
container the token files are unreadable anyway, since the filesystem is read-only.

Credentialled requests are hardened the same way anonymous ones are: the same opener, zero
redirects, a single allowed host, and an allowlist that strips `Authorization` and friends if a
redirect is ever followed. The outbound user agent is fixed and is not configurable.

One outbound destination is not a provider and not configured by a credential: with `push.enabled`
the server POSTs a notification to the push service named by each subscription (D43). The default
`push.endpoint_hosts` admits four — Apple, Google, Mozilla and Microsoft (`web.push.apple.com`,
`fcm.googleapis.com`, `*.push.services.mozilla.com`, `*.notify.windows.com`) — so an egress
allowlist that names only Apple and Google will silently break Firefox and Windows subscriptions
the app itself accepts; size the egress policy to the configured allowlist, not to a subset. That
URL comes from the *browser*, so it is the one outbound request whose host is attacker-influenceable,
and `web/push.validate_endpoint` is the boundary: HTTPS only, no credentials, no explicit port, and a
label-anchored match against `push.endpoint_hosts`. It is checked when the subscription is stored and
again before every send.

### What the proxy must not do

Two requirements on the LiteLLM configuration itself. Neither is checkable from this repository —
the application can only detect the first, after the fact, and pay for it. Both are failure modes RA
guards against in code (RA-017, and the `_unparsed_tool_call` net in `llm.py`); see D42.

**No fallback routing on any alias the roster names.** A LiteLLM fallback that quietly serves
`gemma4` from `meta-llama/llama-4-scout` breaks every downstream identity claim at once: author
exclusion, distinct-reviewer counting, and the family-decorrelation warning all reason over the
resolved `provider/model` that `/model/info` reported at startup. RA fails closed when the served
model disagrees with the pinned alias (RA-017) — which is the right outcome, but the price is a
burnt lens attempt and, if it lands on the writer, a burnt writer attempt. Configure fallbacks for
aliases outside the roster if you want them; never for one inside it.

**Tool-call parsing must actually be configured for the served model.** DeepSeek emits tool calls in
its own fullwidth-token syntax (`<｜tool▁calls▁begin｜>`); a proxy that does not parse it hands the
raw markup back as message *content*, where it reads as a successful prose answer. `llm.py` carries
a guard (`_unparsed_tool_call`) that catches this and retries — a final answer that is nothing but a
tool-call block is exactly what it is built for — but the guard is a net, not a fix, and every catch
spends an attempt from the call budget.

## Source verification is on in production

The committed roster ships `search.verify_sources: false` with the whole `sources:` block commented
out, and says why: fetching URLs a model chose is SSRF exposure by construction, constrained at the
network layer rather than in the application. That default is a statement about *unknown*
deployments. This one has the egress boundary from
[ssrf-egress-isolation.md](./ssrf-egress-isolation.md) in place, so verification runs:

```yaml
search:
  enabled: true
  verify_sources: true

sources:
  enabled: true          # master switch; each tier still opts in separately
  identifiers:           # D39 — ask a registry whether the cited source exists
    enabled: true
  pdf:                   # D39 — read a cited/mirrored PDF instead of failing on it
    enabled: true        # required alongside open_access: a free copy is usually a PDF
  open_access:           # D39 — fetch a free copy of the body and read it once
    enabled: true
    providers: [openalex, unpaywall, europe_pmc, arxiv, core]
  extraction:            # D40 — a rendering service reads the cited URL
    enabled: true
    provider: firecrawl
  delivery:              # D40 — licensed document delivery
    enabled: false       # off; the tier is a validated seam with no runtime wiring
```

Tiers not named above are left at their shipped defaults. Three things about this profile are
load-bearing rather than incidental:

- **`core` is deliberately absent from the shipped `open_access` provider list**, so that enabling
  the tier does not silently become "and also supply a CORE key or fail to start." Naming it here
  is an operator opt-in that comes with the key.
- **`delivery` is inert, not merely disabled.** It has no runtime wiring at all; it is validated
  strictly (enabled without a provider is fatal) so that it stays inert rather than half-built.
- **Extraction has no stealth knob.** The provider's proxy mode is pinned to `basic`, and `auto` is
  forbidden because it silently escalates. This is doctrine, and tests assert against the
  serialized request body so a new code path is caught too.

## Observability

There is no metrics or log-shipping stack in this repository — the application logs to stdout via
the standard library and the container's log driver takes it from there. The real audit surface is
on disk: per run, `events.jsonl`, `audit.json`, and `owner.txt` under the runs volume, with a
startup event recording identities, modes, budgets, and which resolve tiers were enabled. A
background sweeper enforces `retention_days`.

`compose.yaml` sets **`RA_LOG_LEVEL: INFO`** (D42). The shipped code default is WARNING, and the
container's CMD is fixed so `--verbose` cannot be passed; at WARNING a deployment records no run
starts, no controller decisions and no search results, which leaves a failure reconstructable only
from code. The level is safe to raise because no INFO site emits run material: search logs query
*lengths* and counts (RA-016), controller decisions derive only from the blind `OrchestratorView`,
and `structured()`'s schema-violation log names the exception class, never the rejected value.

Two things stdout is still **not** a substitute for. The per-run `events.jsonl` remains the audit
trail — logs are lossy, unowned, and outside the mode-0700 run tree. And a `MalformedOutputError`
message still embeds the validator's own error text, which reaches container logs at WARNING via
`critique`; that predates D42 and is unchanged by it, but it means the run tree is the only place
whose privacy posture is actually enforced.

## Keeping this page true

This page describes a live system, which means it goes stale in a way the rest of `docs/` does
not. A change to what production runs — a tier enabled, a provider swapped, an edge moved — belongs
in the same PR as the change itself. If you are reading it to decide whether some code path is
reachable in practice, and the answer matters, confirm against the deployed roster rather than
trusting this page alone.
