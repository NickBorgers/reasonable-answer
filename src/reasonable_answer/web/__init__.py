"""Web interface: submit a question, watch the loop converge, browse the audit trail.

Callers are identified by a header set by a fronting proxy — Cloudflare Access or
`tailscale serve` — and every route but `/healthz` refuses a request without one.
The header is trusted, not verified, so the app's port must not be reachable except
through that proxy (see app.py, docs/authentication.md).
"""

from .app import create_app

__all__ = ["create_app"]
