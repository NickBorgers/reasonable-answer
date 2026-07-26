# Authentication — Cloudflare Access in front, a trusted header behind

The app does not authenticate anyone. It reads a header, believes it, and treats the
value as both the rate-limit key and the run's owner (D26). Everything that makes that
safe is deployment: the port must not be reachable except through a proxy that sets the
header and strips any the client supplied.

Two proxies are supported, checked in this order:

| header | set by | who arrives this way |
|---|---|---|
| `Cf-Access-Authenticated-User-Email` | Cloudflare Access | invited users, over the internet |
| `Tailscale-User-Login` / `Tailscale-User-Name` | `tailscale serve` | the operator, over the tailnet |

A request carrying neither is refused with `403` on every route but `/healthz`, which is
exempt because the container healthcheck runs inside the container with nothing in front
of it to attach a header.

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
