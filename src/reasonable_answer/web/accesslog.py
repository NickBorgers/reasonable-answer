"""One log line per HTTP request, naming who made it (D-access-log-identity).

This replaces uvicorn's access log rather than decorating it. uvicorn writes its line from
inside the server, where the identity the auth middleware resolved is out of reach; the
app is the only layer that knows both what was asked and who asked it.

The identity logged is the one `authenticate` stored in `request.state.viewer` — never a
header read here. A request the middleware refused has no identity, and a public read
by an anonymous caller has none either, so both lines simply carry no `user=` field.
Re-reading the header for them would put an attacker-chosen string in the log under
the name of a user.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class AccessLogMiddleware:
    """Pure ASGI, so a streamed response is logged when it starts, not when it ends.

    A `/stream` connection can stay open for a whole run; logging at completion would
    hide that someone is watching until they leave.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        logged = False

        async def send_and_log(message: dict) -> None:
            nonlocal logged
            if message["type"] == "http.response.start" and not logged:
                logged = True
                _log_request(scope, message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_and_log)
        except BaseException:
            # The server turns an unhandled exception into a 500 outside this middleware,
            # so the request would otherwise leave no access line at all.
            if not logged:
                _log_request(scope, 500)
            raise


def _log_request(scope: dict, status: int) -> None:
    client = scope.get("client")
    client_addr = f"{client[0]}:{client[1]}" if client else "-"
    # `raw_path` is the bytes from the request line, which cannot hold a CR or LF; the
    # decoded `path` can, and would let a caller forge a second log line.
    target = (scope.get("raw_path") or scope["path"].encode()).decode("latin-1")
    if scope.get("query_string"):
        target += "?" + scope["query_string"].decode("latin-1")
    viewer = scope.get("state", {}).get("viewer")
    line = '%s - "%s %s HTTP/%s" %d'
    args: tuple = (client_addr, scope["method"], target, scope.get("http_version", "1.1"), status)
    if viewer:
        line += " user=%s"
        args += (viewer,)
    log.info(line, *args)
