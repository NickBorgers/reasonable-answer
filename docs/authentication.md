# Authentication — Cloudflare Access in front, a trusted header behind

The app does not authenticate anyone. It reads a header, believes it, and treats the
value as both the rate-limit key and the run's owner (D32). Everything that makes that
safe is deployment: the port must not be reachable except through a proxy that sets the
header and strips any the client supplied.

Two proxies are supported, checked in this order:

| header | set by | who arrives this way |
|---|---|---|
| `Cf-Access-Authenticated-User-Email` | Cloudflare Access | invited users, over the internet |
| `Tailscale-User-Login` | `tailscale serve` | the operator, over the tailnet |

A request carrying neither is refused with `403` on every route but two: `/healthz`,
which is exempt because the container healthcheck runs inside the container with nothing
in front of it to attach a header; and `GET /runs/<id>/export.html`, the anonymous report
share (D35, [below](#sharing-a-report-publicly)). `Tailscale-User-Name` sits beside the
login header and is deliberately **not** read: it carries a display name, which is a
different namespace from the address Access reports.

**Installing the app needs the manifest fetch to carry your session.** The app shell —
`manifest.webmanifest`, `sw.js`, `offline.html`, the icons — is gated like every other
route, and a browser fetches a *manifest* with credentials omitted by default even
same-origin. So the `<link rel="manifest">` this app emits carries
`crossorigin="use-credentials"`; without it the fetch arrives at Access with no
`CF_Authorization` cookie, gets bounced at the edge, and the only symptom is that the
install prompt quietly never appears. If you front this with something other than Access
and installation stops working, that link attribute is the first thing to check.

**The two doors are one identity only if they report the same address.** Every source is
lower-cased and compared for equality, so case never splits you — but if your tailnet's
identity provider reports something other than the address your Access policy lists, the
same person becomes two owners, each seeing half their runs. Worth checking once, on the
index: sign in each way and confirm *signed in as* reads identically.

## What "reachable only through the proxy" means

`compose.yaml` publishes `127.0.0.1:8080` — the port is not on any external interface,
and `cloudflared` reaches it over loopback from the same host. That is the property to
preserve. **Anyone who can open a TCP connection to that port can set the header to
anything** and read or submit as any user; there is no signature to check.

The tailnet path is deliberately left open, which means every tailnet peer can do exactly
that. This is an accepted trade for a small invited group on a tailnet the operator
controls, not a boundary. Closing it means verifying the `Cf-Access-Jwt-Assertion` JWT
that Access already sends alongside the email — see the open items in
[decisions.md](./decisions.md).

## Setting it up

**1. A tunnel to the app.** In the Cloudflare dashboard, Zero Trust → Networks → Tunnels,
create a tunnel and add a public hostname pointing at `http://127.0.0.1:8080`. Run the
connector on the same host as the container:

```yaml
# compose.yaml — alongside the `ra` service
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: ${TUNNEL_TOKEN}
    network_mode: "service:ra"   # so 127.0.0.1:8080 is the app
```

`network_mode: "service:ra"` shares the app's network namespace, so the tunnel reaches
the app over loopback and the port never needs publishing at all. If you publish it
instead, keep the `127.0.0.1:` prefix.

**2. An Access application over that hostname.** Zero Trust → Access → Applications →
Add a self-hosted application on the tunnel hostname. Add a policy of action *Allow* with
an *Emails* rule listing the people you are inviting. Access sends each of them a one-time
PIN or an identity-provider login, and adds
`Cf-Access-Authenticated-User-Email` to every request it forwards. Nothing is configured
in this app — the header is all it wants.

**3. Check it.** With the tunnel up, the index should say *signed in as &lt;your
email&gt;*. Then, from the host:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/            # 403
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz     # 200
```

A `403` on the first is the app confirming it will not serve an unidentified caller. If
it returns `200`, `auth.dev_identity` or `$RA_DEV_IDENTITY` is set — see below.

## Ownership

A run belongs to whoever submitted it, recorded in `runs/<run-id>/owner.txt`.

- The index lists **only your own** runs.
- **Anyone signed in** who has a run id can open that run, its report and its
  `audit.json`. Sharing a link is the intended way to show someone a report.
- **Only the owner** can resume a run: reading costs nothing, resuming spends tokens.
- Runs with **no owner** are served to nobody — 404 on every route, absent from every
  index. That is every run created before this feature, and every `ra run` without
  `--owner`. They are untouched on disk and still reachable through the CLI, and boot
  recovery still finishes them.

To attribute a CLI run so it shows up in the web interface:

```bash
ra run -q "your question" --owner you@example.com
```

## Sharing a report publicly

`GET /runs/<id>/export.html` is the one route served to an **unauthenticated** caller
(D35), so a finished report can be shared by URL to anyone, no sign-in. It is the ideal
public artifact: the export is a single self-contained file (inline CSS, `default-src
'none'`, nothing fetched), and the handler is a pure disk read of `final.md` — no worker,
no graph, no LLM, no token cost. Every other route — the index, the interactive run page,
`audit.json`, the `.md` exports, and every token-spending POST — stays gated. The
exemption is `GET`-only and pinned to that exact path; an owner-less run still 404s, so
this shares nothing that a signed-in caller could not already read by holding the id.

The app serves this off its own (identity-gated) root prefix like everything else; the
deployment exposes it publicly at the edge — see the `host-config-as-code` repo, which
maps `https://reasonable-answer.nickborgers.net/runs/<id>` to it off the Access-gated
`/app` prefix. To surface a **Copy public link** button on the run page, tell the app that
public edge base:

```bash
RA_PUBLIC_SHARE_BASE=https://reasonable-answer.nickborgers.net/runs
```

Unset (the default, and the whole tailnet/dev case, which has no public edge) the button
is not rendered — core sharing does not depend on it; the link is just `<base>/<run-id>`.

## Local development

With no proxy in front, every request is anonymous and therefore refused. Set an identity
for unauthenticated requests:

```bash
RA_DEV_IDENTITY=you@example.com make serve
```

or `auth.dev_identity` in `roster.yaml`. It applies **only** when no identity header is
present, so it can never override a real user. The server logs a warning whenever it is
set — it turns "no identity" from a refusal into a login, which is right on a laptop and
wrong anywhere else.
