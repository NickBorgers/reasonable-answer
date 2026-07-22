# SSRF egress isolation — the infra half of the fetch boundary

`fetch.py` is explicit that it is **not** an SSRF boundary: it resolves URLs a model
chose (the evidence critic reads cited pages, and the writer reads search results), which
is exposure by construction, and it defers egress control to the network layer. The
in-app bounds there — `http(s)`-only, no `file:`/`ftp:`/`data:` handlers, per-hop redirect
scheme re-checks, timeout/byte/redirect caps — exist so one page cannot stall or exhaust a
run, **not** as a security control.

This document is the other half: a reusable, host-agnostic way to give the deployment the
egress control `fetch.py` assumes. It is one worked deployment, not the only one — any
equivalent network-layer filter (a VLAN, a host firewall, a cloud egress policy) satisfies
the same contract. The requirement `fetch.py` places on *any* of them is the same:

> On every host that runs the graph, the process must be able to reach the public
> internet and the LLM proxy, and must **not** be able to reach the LAN, loopback,
> link-local, or the tailnet.

## Why the app needs this

The service has no authentication (tailnet-only posture) and its evidence critic fetches
web content **influenced by the user's question**. If that process can open arbitrary
outbound connections, a crafted question can steer a fetch at an internal-only service —
a classic SSRF pivot. Bounding it inside the app is not enough: the URL is chosen by a
model reacting to untrusted input, so the guarantee has to be physical, not
app-cooperative.

## The mechanism: internal network + filtering egress proxy

Container-native, no VLAN. Three parts:

```
        internet-only  (internal: true — a Docker network with NO route off itself)
  ┌────────────────────────────────────────────────────────────────────────┐
  │                                                                          │
  │  reasonable-answer ──HTTP(S)_PROXY──▶ egress-proxy (Squid) ──┐           │
  │      │              └──NO_PROXY, direct──▶ litellm-proxy      │           │
  │      ▲ :8080 (inbound only)                                   │           │
  │  ui-publisher (socat) ───────────────────────────────────────┼──┐        │
  └──────┼───────────────────────────────────────────────────────┼──┼───────┘
         │ publishes 127.0.0.1:<port>                             │  │ (also on)
         ▼                                              egress-proxy-out
     host loopback → your tailnet front-end             (normal bridge, NAT)
                                                          → PUBLIC internet only
                                                            (Squid denies private dsts)
```

1. **`internet-only` — an `internal: true` Docker network.** Containers on it have **no
   default route off the network**: no internet, no LAN, no tailnet, directly. This is the
   enforcement primitive. The app is attached to *only* this network. Even if the app
   ignored its proxy env vars entirely, it still physically cannot reach anything but its
   few peers on this network — a proxy-less `curl` to any address returns `000`.

2. **`egress-proxy` — a Squid forward proxy** (`squid.conf` below). Dual-homed: on
   `internet-only` (so isolated consumers reach it) and on a **normal** bridge that is its
   only path with real outbound NAT. Its ACL **denies** every private / loopback /
   link-local / tailnet destination and **allows all public hosts**. The app reaches it via
   `HTTP_PROXY`/`HTTPS_PROXY`. The proxy's outbound interface *could* route to the LAN — the
   ACL is the choke point that refuses to.

3. **`ui-publisher` — a `socat` shim** that exposes the web UI. An `internal` network
   cannot publish host ports (Docker wires no DNAT for it), so the app can't publish its
   own port. The publisher is dual-homed on `internet-only` (reaches the app by name) and
   the NAT bridge (non-internal → can publish), forwarding `host-loopback → app:8080`. It
   only accepts inbound and only forwards to the app, so it gives a compromised app **no
   new egress path**. A tailnet front-end (e.g. `tailscale serve`) fronts that loopback
   port unchanged.

The **LLM path is kept off the proxy**: dual-home `litellm-proxy` onto `internet-only` so
the app reaches it directly by name, and list it in `NO_PROXY` so that traffic is not sent
to (and denied by) Squid.

### Why the enforcement is real, not app-cooperative

The app sits on an `internal: true` network with no route off it. The proxy env vars are
how the app *uses* its one internet path; the network is what *guarantees* there is no
other path. Two independent facts hold the line — the network has no off-ramp, and the
proxy refuses private destinations — so neither a misconfigured app nor a prompt-injected
fetch can pivot inward.

## `squid.conf`

Generic and reusable — no domain allowlist (arbitrary public fetch is a feature here);
only private ranges are blocked. Reachability is already restricted to members of the
`internet-only` network, so there is no source ACL.

```squid
# egress-proxy — Squid forward proxy that permits the public internet but denies every
# internal/private/tailnet destination. A consumer joins the `internet-only` internal
# Docker network and points HTTP(S)_PROXY here.

http_port 3128
cache deny all                     # security gateway, not a cache

# The anti-SSRF choke point: the outbound interface CAN route to the LAN; it refuses to.
acl internal_networks dst 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16
acl loopback_dst      dst 127.0.0.0/8
acl link_local        dst 169.254.0.0/16
acl tailscale_dst     dst 100.64.0.0/10
http_access deny internal_networks
http_access deny loopback_dst
http_access deny link_local
http_access deny tailscale_dst

http_access allow all              # everything else (public internet)

forwarded_for delete
httpd_suppress_version_string on
access_log stdio:/var/log/squid/access.log
cache_log  stdio:/var/log/squid/cache.log
visible_hostname egress-proxy
```

> Note the deny list includes `100.64.0.0/10` (the tailnet / CGNAT range). Without it the
> "allow all public" rule would let a fetch reach tailnet peers, since those are the SSRF
> targets that matter most in a tailnet deployment.

## Compose sketch (illustrative)

Networks and the four services, with host-specific values removed. Adapt names, the
published loopback port, and the tailnet front-end to your host.

```yaml
networks:
  internet-only:      { internal: true }   # no route off it
  egress-proxy-out:   {}                    # normal bridge — the only NAT path

services:
  reasonable-answer:
    networks: [internet-only]               # ONLY this network
    environment:
      HTTP_PROXY:  http://egress-proxy:3128
      HTTPS_PROXY: http://egress-proxy:3128
      NO_PROXY:    litellm-proxy,localhost,127.0.0.1
      # ... LLM_PROXY_BASE_URL=http://litellm-proxy:4000, etc.

  egress-proxy:                             # Squid, dual-homed
    networks: [internet-only, egress-proxy-out]
    volumes: [./squid.conf:/etc/squid/squid.conf:ro]

  litellm-proxy:                            # dual-home so the LLM path is direct + off Squid
    networks: [internet-only, egress-proxy-out]

  ui-publisher:                             # socat: host loopback -> app:8080
    image: alpine/socat
    networks: [internet-only, egress-proxy-out]
    command: TCP-LISTEN:8080,fork,reuseaddr TCP:reasonable-answer:8080
    ports: ["127.0.0.1:8082:8080"]          # front this with your tailnet proxy
```

## Onboarding another SSRF-risk container

1. Attach it to **only** the `internet-only` network.
2. Set `HTTP_PROXY`/`HTTPS_PROXY` to the proxy and `NO_PROXY` to any internal peer it
   talks to directly, plus `localhost,127.0.0.1`.
3. Dual-home any such direct peer (like `litellm-proxy`) onto `internet-only`.
4. If it serves a UI, add a `socat`/reverse-proxy publisher — an `internal`-only container
   cannot publish host ports itself.

## Verifying isolation

From a throwaway sidecar on the same network, on the Docker host:

```bash
S="docker run --rm --network internet-only curlimages/curl:latest -s -m6 -o /dev/null -w %{http_code}"

$S http://<a-LAN-ip>/                                        # 000  LAN unreachable (no route)
$S https://1.1.1.1/                                          # 000  internet unreachable without the proxy
$S -x http://egress-proxy:3128 https://api.search.brave.com/ # 2xx/3xx  public via proxy OK
$S -x http://egress-proxy:3128 http://<a-LAN-ip>/            # 403  Squid denies internal
$S http://litellm-proxy:4000/                                # 200  LLM path direct, by name

# Proof the real app honours the proxy — its fetches appear in the proxy log, from its own IP:
docker logs egress-proxy 2>&1 | grep CONNECT | tail
```

`000` on the first two lines is the whole point: the app's only reachable destinations are
the egress proxy (public web, private denied), the LLM proxy (direct), and the UI
publisher (inbound only).

## Scope

This isolates the **serve host**, which is the only place the graph's fetch path runs. The
CI review agents review code and do not run the graph's fetch, so they are a different
threat (agent behaviour, not SSRF) with a different control (who may trigger a job). See
`docs/security-review.md`.
