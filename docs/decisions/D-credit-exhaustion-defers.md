## D-credit-exhaustion-defers — a provider account that cannot pay defers the run, it does not end it

**The finding.** On 2026-09-09 `run-81212fcbf68f` reached round 5 when OpenRouter began answering
every alias on the deployment's account with `402` — "This request would exceed your available
credits given your current in-flight requests", and for one alias "requires more credits, or fewer
max_tokens". Each writer in the walk spent its three call attempts inside about five seconds, rule
1 fired, and the run was recorded `aborted` after 5,766 seconds of work. Two hours later the
account was topped up; a run *queued* behind it — refused at startup by the same 402s and deferred
under D-deferred-not-abandoned — resumed and ended `accepted`. The two runs hit the same fact about
the deployment and got opposite fates, decided only by whether the refusal arrived before or after
`build_runtime`.

Three defects compound:

* **402 was retried as transient.** It is in neither the permanent set nor treated as the account
  signal it is, so `_create` spent the backoff budget re-sending — and re-reserving credit for —
  requests a balance measured in dollars was refusing.
* **The graph had no way to say "not now".** A writer walk that fails returns `fatal`, which the
  controller can only turn into `aborted`. A critic refused by the account fails its lens in
  seconds, rule 2 re-asks a model on the same account, and after `critique_attempts` rule 3 aborts.
* **Only startup refusals defer.** `StartupRefused` is caught by the worker precisely because it
  is about the deployment; the same fact discovered one node later was treated as a verdict.

**The decision.**

1. **A 402 is not retried.** `_create` raises `ProviderAccountError` — a `ModelCallError` subclass
   with `failure_class` `http_402` (`ACCOUNT_FAILURE_CLASS`) — on the first 402. Every existing
   `except ModelCallError` keeps catching it; at startup a probe that meets one still becomes
   `ProbeIncomplete`, degrades the roster or defers exactly as before, only sooner.
2. **The graph raises `ProviderAccountExhausted` instead of reaching a verdict.** In `_generate`,
   when every writer attempt failed and *any* of them was an account refusal. In `_critique`, when
   an account refusal is why a lens has no completed review of the current artifact; a lens whose
   other critic completed is reviewed, and the run goes on. The exception leaves the node, so the
   controller never sees it and the checkpoint stays at the last completed node: the resume re-runs
   exactly the node that was refused. `_critique` checks before recording the pass, so a resumed
   pass does not appear twice in the audit trail.
3. **The worker defers it** in the same branch as `StartupRefused`: a `deferred` event, no
   notification, the run left `interrupted`, the resume attempt cancelled, `max_deferred_attempts`
   still capping it. The event carries the closed token `provider_account` (`DEFERRAL_CODES`
   extends `REFUSAL_CODES`), never the message, which quotes the provider
   (D-id-as-credential). The registry says "the model provider account ran out of credit; it
   retries automatically". `ra run` prints the refusal and how to resume, and exits 75
   (`EX_TEMPFAIL`).

**Why "any" refusal in the writer walk, not "every".** Once the account has said it cannot pay,
the other failures in the same walk are as likely to be its side effects — a request that timed out
while in-flight credit was contested — as independent defects. The costs are asymmetric: an
unnecessary deferral costs a restart, and an abort that should have been a deferral costs the run.

**Why not a new `StartupRefused`.** That type's claim is structural: nothing about the run was read
yet. A mid-run refusal has read the run, so it gets its own type and joins only the vocabulary and
the worker branch, which are about what the refusal says — something about the deployment — rather
than when it happened.

**What this deliberately does not do.** It does not wait or retry inside the process: like a
startup deferral, the next boot is the retry, because topping up an account is an operator action
and the operator can restart. It does not lower the `max_tokens` a request reserves, which is what
turns a low balance into a 402 "given your in-flight requests" early — that is a roster and proxy
setting. It does not treat a 402 as evidence about the alias: `http_402` is excluded from critic
strikes (D-failing-critic-sidelined), and the writer walk does not stop rotating on one.

**Invariants.** *Termination*: touched. The graph can now leave without a terminal status in one
more way, and that exit is bounded the way every deferral is — `max_deferred_attempts`, then
`abandoned`, never a `final.json` — so no run loops unbounded and none is recorded as a verdict
the controller never issued. No controller rule is added, removed or reordered, and rules 1 and 3
still fire for every failure that is not an account refusal. *Fail-closed lenses*: unchanged — a
refused review contributes nothing, and a deferral is stricter than continuing, not looser.
*Author exclusion*, *blind orchestrator*, *severity floors*, *untrusted text*: untouched.
