## D-stop-notification — a run that stops says so, on a device that was not watching

**The problem.** A run is 10–25 minutes and the index makes it easy to start several. The only
mechanism that ever said a run had finished was `GET /runs/<id>/stream`, which pushes progress into
a page that is currently open and reloads it on `done`. Close the tab and nothing says anything;
background the installed app on a phone and iOS suspends it, so nothing *can*. The interface's own
affordance — queue several questions and go and do something else — was the one it could not
support, and the workaround was to come back and poll the index by hand.

**Decision.** Web Push, delivered through the service worker D-installable-pwa already ships, sent from the
worker thread the moment a run stops. Opt-in is per device, one tap, stored against the caller's
identity. Both a terminal completion and a stop-without-an-answer notify; a shutdown pause does not.

**Why Web Push and not the stream that already exists.** A local `Notification` fired from the SSE
`done` handler is thirty lines and no dependency, and it cannot do the job: it requires the page to
be open and foregrounded, which excludes every case worth notifying about. A suspended iOS PWA runs
no JavaScript at all. Only a server-sent push reaches a locked phone, and only a service worker can
receive one — which is why this decision is downstream of D-installable-pwa rather than independent of it. On iOS
push additionally exists *only* for a web app added to the Home Screen
([WebKit, 2023](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)), so the
install path D-installable-pwa built is the literal precondition; without it there is no iPhone notification to
have.

**The subscription endpoint is attacker-influenced, and that is the security core.** The browser
mints a URL and hands it to the server, which then POSTs to it — the same shape as the seed-URL
fetch that `ssrf-egress-isolation.md` exists for. `push.validate_endpoint` requires HTTPS, refuses
embedded credentials and an explicit port, and matches the host against an allowlist of push
services on *labels*: an exact match, or a dot-anchored suffix for a wildcard entry. A substring or
bare `endswith` test admits `evil-fcm.googleapis.com` and `fcm.googleapis.com.attacker.net`, both of
which are pinned as refusals. The check runs at subscribe time **and again before every send**, so
narrowing the allowlist takes effect on subscriptions already stored rather than only on new ones.

**The routes are top-level, and specifically not under `/runs/`.** D-id-as-credential opens every `GET` under
`/runs/` to anonymous callers, so that prefix is where reads of a finished run live. Subscribing is
the opposite: it attaches a device to an identity. `authenticate`'s method guard would refuse a
`POST` there anyway, but siting a write inside the public read prefix and relying on that guard
inverts the rule D-id-as-credential states — reads public, writes gated — into a coincidence. A test enumerates the
route table rather than these two handlers, so a future push route cannot drift into `/runs/`
either.

**The CSP is unchanged.** Subscribing is not a page fetch: the browser negotiates with the push
service out of band, and the only page-originated request is a POST to this origin, already covered
by `connect-src 'self'`. Nothing here needed a new directive, which is worth recording because D-installable-pwa
had to widen the policy and a reader may reasonably expect this to as well.

**The service worker's cache invariant survives by construction.** `push` and `notificationclick`
are additive and neither touches `caches`, so `cache.put` still appears in exactly one branch
reachable only for URLs in `ASSETS`. The `push` handler's payload parse is wrapped and always falls
back to a generic body, because Chrome's `userVisibleOnly` contract means a handler that throws gets
the browser's own "site updated in the background" notice — a vague notification is bad, that one is
worse.

**The contact address is an environment variable, and there is deliberately no roster field for
it.** [RFC 8292 §2.1](https://datatracker.ietf.org/doc/html/rfc8292#section-2.1) *recommends*
(`MAY` include the claim, `SHOULD` make its value a contact URI) a `sub` claim — a `mailto:` or
bare `https://host` a push service can use to reach the operator; the hard requirement is
`py_vapid`'s, whose `Vapid01._base_sign` raises `VapidException` when `sub` is absent or empty. So
an unset subject means every send raises before reaching the network. That exception would land in the notifier's best-effort
`except` and present as notifications that silently never arrive, so `push.enabled` with no subject
is a boot failure instead. It is an env var for the same reason `SourcesConfig.contact_email` is:
the value is somebody's personal address, the roster is committed to a public repository, and a
config field is an invitation to put it there. Only the *variable name* is configurable.

**The VAPID key is generated, not configured, and losing it is the sharp edge.** A keypair is
self-issued: there is no account anywhere to register it with, no Firebase project, no APNs
certificate. So making the operator produce one by hand adds a setup step that can be got wrong and
buys nothing, and the app mints it on first boot. The cost is state worth backing up — every
subscription is bound to the key it was minted under, so a lost key invalidates all of them at once,
silently, because a push to a stale subscription is simply refused and there is no channel left to
ask the device to re-subscribe. The generation path logs a warning saying exactly that.

**Both files, never a directory.** Subscriptions and the key live directly in `runs_dir`, which is
the mounted volume — anywhere else and they die with the container. They are files because
`Registry._run_dirs` skips anything without an `events.jsonl` *and* `store.expired_runs` filters on
`is_dir()` alone: a `push/` subdirectory would eventually be swept as an expired run, taking the key
and therefore every subscription with it. A file is invisible to both by construction, which is a
stronger guarantee than an exclusion rule a later refactor can drop.

**The send is inline on the worker thread, best-effort, and never fatal.** It runs from `_drain`'s
`finally` on a sentinel set by each branch, rather than from the branches themselves — `finally`
also runs on the `GracefulStop` `return`, and a shutdown pause resumes on the next boot, so
notifying about it is a false alarm. Leaving the sentinel unset is what suppresses that case, which
is harder to get wrong than remembering to omit a call from one branch in four. At
`RA_MAX_CONCURRENT_RUNS=1` a dead push service delays the next queued run by the timeout — seconds,
against a run measured in tens of minutes — which buys the absence of a second thread with its own
shutdown story. A `404` or `410` prunes the subscription; every other failure is a log line, because
the run is already finished and durable and a courtesy must not cost the result.

**The payload carries the question, which is a privacy decision.** Web Push bodies are encrypted
under the `aes128gcm` content coding to a key pair the user agent binds to the subscription
([RFC 8291](https://datatracker.ietf.org/doc/html/rfc8291)), so whichever push service relays the
message — Apple, Google, Mozilla or Microsoft — carries ciphertext it cannot
read. Truncated question text is what tells five concurrent runs apart on a lock screen, and without
it the notification says only that *something* finished. The deep link uses the reader-facing base
(D-id-as-credential), so it is the same URL every other run reference in the app emits; a finished run points at
the report, one that stopped without shipping an answer points at the run page.

**Permission is requested from a click and never on load.** iOS grants the prompt only in response
to direct user interaction
([WebKit, 2023](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)), and a
declined permission cannot be re-prompted — the only reset is deleting and reinstalling the
home-screen app — so an auto-prompt spends that single chance on a page view, before the person
knows what they are being asked. The
control also ships `hidden` and is revealed only after the script has established the browser can
deliver: an iOS Safari tab has `PushManager` and still cannot subscribe, so feature detection alone
would show a button that fails, and the standalone check turns that into an instruction instead.

> The last clause is superseded in part by **D-header-optin**, which moved this control into the
> layout shell. A header has no room for an inline sentence, so on iOS in a browser tab the control
> now stays hidden rather than rendering an instruction; the install affordance is the one the
> browser already offers. Everything else here — click-gated permission, `hidden` until the browser
> is known to deliver — stands.

**Off by default.** Like every feature needing egress or a secret. With `push.enabled: false` there
are no routes, no key on disk, and an index byte-identical to a build without any of this — the same
promise D-question-refinement makes for refinement, and asserted the same way.

**Invariants.** Untouched. This is entirely downstream of a finished run and moves no new data
toward any model context: nothing here reaches a critic, a writer or the arbiter. Ownership is read
from `owner.txt`, the single record D-identity-header established, so an owner-less run notifies nobody rather than
having an identity invented for it. Isolation, the dispute channel, the convergence controller and
the fail-closed lenses are not touched.
