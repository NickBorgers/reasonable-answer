"""Who is asking.

Identity is read from a request header and trusted as presented. That is sound only
when the app's port is unreachable except through the proxy that sets the header:
Cloudflare overwrites `Cf-Access-*` on everything it proxies, so through the tunnel
the value is authoritative, and `tailscale serve` does the same for its own headers.
A caller reaching the port directly can set either header to anything (D26,
docs/authentication.md).

Everything that turns a request into an identity lives in this one function, so
replacing header trust with a verified `Cf-Access-Jwt-Assertion` — the JWT Access
already sends alongside the email, checked against the team's JWKS and `aud` — is a
change to `_from_access` and nothing else.
"""

from __future__ import annotations

from starlette.requests import Request

from ..config import AuthConfig

#: Set by Cloudflare Access on every request it proxies, and stripped from inbound
#: requests that try to supply their own. Preferred over the Tailscale headers: it is
#: the path friends arrive by, and the tailnet path is the fallback for the operator.
ACCESS_EMAIL_HEADER = "cf-access-authenticated-user-email"

#: Set by `tailscale serve` when it fronts the app; carries the calling node's user.
TAILSCALE_HEADERS = ("tailscale-user-login", "tailscale-user-name")

#: An identity is an ownership key that is written to disk and compared for equality,
#: so it is bounded and kept to printable characters. 320 is the maximum length of an
#: email address (RFC 3696 errata), which is what the Access header carries.
MAX_IDENTITY_CHARS = 320


def resolve_identity(request: Request, auth: AuthConfig) -> str | None:
    """The caller's identity, or None when the request carries none.

    None is the refusal case, not a shared bucket: an unauthenticated request owns
    nothing and may see nothing, so there is no longer anywhere for it to go.
    """
    access = _clean(request.headers.get(ACCESS_EMAIL_HEADER))
    if access:
        # Addresses are case-insensitive in the part that matters here, and an identity
        # that varies by case would split one person's runs across two owners.
        return access.lower()
    for header in TAILSCALE_HEADERS:
        tailnet = _clean(request.headers.get(header))
        if tailnet:
            return tailnet
    return _clean(auth.dev_identity)


def _clean(value: str | None) -> str | None:
    """Trim, and reject anything that would be an awkward ownership key.

    Control characters would land in `owner.txt` and in the JSON of `audit.json`; an
    unbounded header would land there at whatever length the caller chose. Neither is
    an identity, so treat both as absent rather than truncating into a value that
    silently is not the one presented.
    """
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > MAX_IDENTITY_CHARS:
        return None
    if any(ch < " " or ch == "\x7f" for ch in value):
        return None
    return value
