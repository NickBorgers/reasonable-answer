## D-access-log-identity — every HTTP access line names the identity that made the request

**The problem.** Creating a report requires an identity (D-identity-header), but nothing in the
container log said whose request it was. The only HTTP log was uvicorn's access line —
`127.0.0.1:48102 - "POST /runs HTTP/1.1" 303 See Other` — and behind `cloudflared` the client
address is always loopback, so the line identified nobody. Reconstructing who submitted, refined,
resumed, or subscribed meant correlating timestamps against `owner.txt` on the runs volume, and
for requests that write nothing to disk (a refused resume, a push unsubscribe) there was nothing
to correlate against (issue #221).

**Decision.** The app writes the access log itself, one line per HTTP request, and `ra serve`
passes `access_log=False` so uvicorn does not write a second, anonymous copy.

1. **The line keeps uvicorn's shape** — `<client> - "<METHOD> <target> HTTP/<v>" <status>` — so
   Loki line filters on the request text still match (the trailing reason phrase, `OK`, is gone), and appends `user=<identity>` when the
   request has one.
2. **The identity is `request.state.viewer`, read after `authenticate` has run** — never a header
   read by the logger. `AccessLogMiddleware` (`web/accesslog.py`) is a pure ASGI middleware added
   outside `authenticate` and reads the scope's state when the response starts. A refused request
   has no viewer, so a header the app rejected (an over-long or control-character value, or any
   header on a route where the request was refused) is never written to the log as a user.
3. **No identity means no field**, not a placeholder. `/healthz`, a refused request and an
   anonymous public read under `/runs/` or `/static/icons/` log without `user=`. A placeholder
   such as `-` would be indistinguishable from an identity that happened to be `-`.
4. **The target is the raw request-line bytes**, not the percent-decoded path, so a `%0A` in a
   URL cannot forge a second log line. An identity cannot either: `_clean` already rejects
   control characters.
5. **A streamed response is logged when it starts**, so an open `/stream` shows up immediately; an
   exception that escapes before any response started is logged as `500` and re-raised.

**Why the app and not a filter on uvicorn's logger.** uvicorn logs from inside the server, after
the app has returned its response start, where the resolved identity is reachable only through a
context variable leaking out of the request task. That works by accident of task layout. It is also invisible to the test suite, which drives the app through `TestClient` with no
uvicorn in the loop. Owning the line puts it where the identity lives and where tests can assert it.

**What this does not change.** Logs are not a public route, so "no public route names a person"
(D-id-as-credential) is untouched. The logged identity carries the header's trust level — it is
who the proxy said the caller was, not a verified principal (D-identity-header). The request
target was already logged by uvicorn, run ids included; a run id is a read credential
(D-id-as-credential), and that exposure is exactly what it was. Non-HTTP log lines emitted during a
request (for example a worker's `queued <run-id>`) do not carry the identity; the access line for
the same request does, and is adjacent in time.

**Tests.** `tests/test_web.py`: a signed-in request logs `user=<identity>`; `/healthz`, a refused
request with a rejected header, and an anonymous public read log no `user=`; a signed-in public
read logs its identity; `ra serve` passes `access_log=False`.
