## D-id-as-credential — reading a run is public; holding the id is the credential

D-identity-header made every route but `/healthz` refuse a caller with no identity, which is the right default:
the failure mode of an opt-in check is a new route that forgets it, and seed material, questions
and audit trails are exactly what must not leak. But it also closed the one thing the interface
most wants to do — hand a finished report to someone who is not invited. Under D-identity-header sharing works
only *between signed-in callers* (reads are share-by-id, not owner-scoped); a link sent to anyone
outside the Access policy 403s at the app.

**Decision.** Every `GET` under `/runs/` is served without an identity. Every write stays gated
exactly as D-identity-header left it. Holding the run id *is* the credential for reading that run — which is
what D-identity-header already said, minus the sign-in.

**Why the line is read/write and not one filename.** The first shape of this change made a single
route public, `GET /runs/<id>/export.html`, on the argument that the export is the safest possible
artifact. It is — but it does not do the job. That route is served
`Content-Disposition: attachment`, so the "public link" *downloads a file* instead of rendering a
page; and the URL a person is actually looking at after a run is `/runs/<id>`, which stayed 403.
The result was a share affordance that required copying a *second*, different URL out of a button
— exactly the friction the change existed to remove. A person shares the URL in their address bar.
If that URL does not work for the recipient, nothing else about the design matters.

So the exemption is the read surface, whole: the run page, the report page, `progress`, `stream`,
`report.md`, `export.md`, `export.html`, `audit.json`. "Reads of a run are public, writes are
gated" is a rule a person can hold in their head and apply to a route that does not exist yet,
which a list of blessed paths is not.

**What keeps it narrow is the method, not the path.** Every route that spends tokens or changes
state is a `POST`: `POST /runs` (submit — no trailing slash, so it does not match the prefix),
`/runs/<id>/resume`, `/runs/<id>/again`, `/refine`. All still hit `resolve_identity` and 403
without a header, as do `/` — a per-viewer list, which needs a viewer — and the app-shell assets.
A `POST` to a public *read* path is refused before it reaches routing.

A prefix does mean a future `GET` under `/runs/` is public the day it is written. That is a real
cost, accepted with a guard rather than argued away:
`tests/test_web.py::test_public_run_get_routes_are_the_expected_set` enumerates the route table and
fails when the set changes, so widening the public surface is a deliberate edit with a test to
update, not a side effect of adding a handler.

**An owner-less run stays a 404, anonymously as much as before.** `_require` 404s a run with no
owner (D-identity-header) and every route under `/runs/` passes through it, so this shares nothing that was
unshareable: legacy and `ra run`-without-`--owner` runs remain served to nobody. `viewer` may now
be `None` on these handlers, and none of them scope a read by it — the identity is still resolved
when a header happens to be present, because the same pages are reachable through the gated door
too, but `None` is an ordinary value here rather than a refusal.

**The URL in the address bar has to be the shareable one, so the app emits two bases.** The edge
gates `/app/` with Cloudflare Access and leaves `/runs/` open. A run page emitted under
`/app/runs/<id>` is therefore a link only a signed-in person can open, no matter what the
middleware allows. `RA_ROOT_PATH` keeps the gated surface — the index, form actions, the app shell;
`RA_PUBLIC_ROOT_PATH` carries the reader-facing surface — the run page, everything linked from it,
the SSE stream, and the `303` a submission lands on. Setting `RA_PUBLIC_ROOT_PATH=/` in production
puts every run URL at the origin root. Unset, it falls back to `RA_ROOT_PATH`, so a single-door
deployment — dev, the tailnet — emits byte-identical URLs to before (D-base-path is unchanged; this adds a
second base, it does not alter how either is joined).

**One page renders the report, and the run page points at it.** Making `/runs/<id>` public solved
the 403 but left two pages showing the same report: the run page rendered it in full *and* linked
to `/runs/<id>/report`, so whichever URL a person copied, the recipient got the report — plus, on
the run page, six buttons and a pipeline trail they have no use for. The report body now lives at
`/runs/<id>/report` only. The run page keeps what belongs to the *run* — the verdict, the
round-by-round trail (no longer folded, since there is nothing above it to outrank it),
`audit.json`, `Ask this again` — and a single `Read the report` link. Every way of *taking* the
report away (copy, `.md`, `.html`) moved to the page that renders it, where a reader who has
decided they want it is already standing, and `audit.json` is offered there too: `/report` is the
page that gets shared, and a verdict a recipient cannot check is not much of a claim.

**A status is a marker until it is labelled.** `exhausted unresolved` as a bare badge is a word in
a vocabulary the recipient of a shared link has never seen. Both pages now show `Run status`, the
badge, and the `STATUS_MEANING` sentence — the same words the export carries (D-verdict-attached), from the same
table, so the page and the file explain the verdict identically. The label is print-hidden along
with the badge; on paper the print header already states it.

**Nothing public names a person.** A shared link reaches strangers, so the owner's address is kept
off every public route. The run page's byline ("submitted by …") is gone: it existed to attribute a
run reached by a link *within* the org, and on a page anyone can open it publishes the sender's
address to whoever received the link. `audit.json` drops its `summary.owner` field for the same
reason — an email address is not evidence about a run. Nothing else under `/runs/` carries an
identity: ownership lives in `owner.txt`, never in the event log.

**Resume becomes "Ask this again".** The resume button was shown only to a run's owner, a
distinction a page with no identity cannot draw, and a resume offered to everyone is an invitation
to a 404. Rather than reintroduce an authenticated twin of the run page to host one button, the
page now offers `POST /runs/<id>/again` on any stopped run: a *new* run of the same question,
owned by whoever clicks, rate-limited as their own submission, leaving the original untouched. It
needs no knowledge of who is reading, because it grants nothing a reader could not get by retyping
the question on the index. An anonymous reader who presses it meets the identity gate, which is the
honest failure. The question and the seed are read off disk from the run id — nothing is taken from
the request — so no client-supplied text reaches a model context and the seed does not have to ride
into the DOM in a hidden field. `POST /runs/<id>/resume` remains as a route for the operator; it
just no longer has a button.

This is a deliberate trade: a run that crashed at minute 18 is now re-run from scratch rather than
resumed from its checkpoint, at full token cost. It is narrow. Automatic recovery already handles
the common interruptions (deploy, restart) with no human involved (`worker.recover`), a run
`abandoned` by a roster change *cannot* be resumed anyway (`ResumeMismatch` is why it is abandoned),
and what remains is a crash that leaves the process alive. Paying tokens in that case is cheaper
than an owner-aware second surface.

**The progress stream is public, and that is a resource question, not a data one.** Nothing on this
path writes: `Registry` has no write method at all, and the only worker call is `active()`, a
lock-guarded dict copy. It spends no tokens — it polls the run's own `events.jsonl` once a second.
What changes is that an open connection is now something a stranger can start, so two bounds were
added with it. `RA_MAX_LIVE_STREAMS` (default 32) caps how many run at once and answers `503` past
that rather than queueing, since a parked caller is an open connection too. And the loop now exits
on `not is_live` alone: it previously also required a `final.json`, which meant a run that stopped
*without* one — a crash, or `abandoned` — polled forever, and those are exactly the states the page
most needs to repaint into.

**The forgery caveat is the same one D-identity-header already carries, no wider.** The tailnet path lets any peer
set the identity header and read or submit as anyone; that is the accepted risk whose fix is the
deferred JWT check below. This decision removes the identity *requirement* for reads that every
signed-in caller could already perform by holding the id, so the set of things an anonymous tailnet
peer can reach does not grow by anything they could not reach by claiming an identity.

**Isolation is untouched.** This is entirely in the web layer, upstream of nothing that enters a
model's context and downstream of every run. Author exclusion, the blind orchestrator
(`OrchestratorView`), fail-closed lenses, severity-floor clamping, controller termination and the
untrusted-text boundary all live in the Python review core and the convergence controller, none of
which this changes. Showing a report to an anonymous human is the same act D-identity-header already sanctioned
for a signed-in one; blindness is about what a *model* may read, and this moves no data toward any
model.

Deployment and the route table are documented in [authentication.md](../authentication.md).
