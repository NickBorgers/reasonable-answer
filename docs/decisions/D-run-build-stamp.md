## D-run-build-stamp — a run names the commit that produced it, or says it does not know

**The problem.** Runs recorded what they concluded and nothing about what produced them.
`final.json` carried `artifact_hash`, but that hashes the *report text*, not the code; nothing in
the store, the schemas, the events, the web API, the Dockerfile or CI recorded a commit, a version
or an image tag. `pyproject.toml` pinned `version = "0.1.0"` and no runtime code read it.

That made the most useful question about this system unanswerable from its own output. "We changed
how revisions are scoped and we are still not converging — which of these runs already had that
change?" could only be attacked by lining run timestamps up against `git log`, which is wrong
whenever a deploy lags a merge, whenever one PR carries two fixes, and whenever a run was resumed.
The system was accumulating exactly the evidence needed to evaluate its own changes, in a form that
could not be sorted by change.

**Decision.** Every run stamps `{"commit", "dirty", "source"}` — on each `queued` and `startup`
event, and in the `final.json` summary, which carries it into the `finalize` event and
`/runs/{id}/audit.json` for free. `build_identity()` resolves it once per process from, in order:
`RA_BUILD_SHA` baked into the image by CI (`source: "image"`, the production path and the only
authoritative one); the checkout the package sits in (`source: "git"`, covering `uv run`, the
devcontainer and tests, and the only source that can report `dirty`); or nothing
(`source: "unknown"`).

**`unknown` is recorded, not guessed.** The alternative — inferring a commit, or defaulting to
something plausible — produces a value indistinguishable from a measurement once written, and a
confidently wrong attribution is worse than a missing one. `ra doctor` reports the source and warns
on `unknown`, and the first run in the process logs a warning, so a deployment that has lost its
stamp is visible immediately rather than as a month of unattributable runs. For the same reason
there is no backfill of runs that predate this: they have no `build` key, and every display surface
omits the row rather than printing "unknown".

**Non-blank, not merely set.** `ENV RA_BUILD_SHA=$RA_BUILD_SHA` leaves the variable *always*
defined — empty on any `docker build` without the argument. Testing for presence would have
recorded `""` as an authoritative commit, which is the one failure mode this decision cannot
tolerate, so the check is for a non-blank value after stripping.

**Shelling out to git, in `src/`, for the first time.** Reading `.git/HEAD` directly would be
cheaper and dependency-free, but it cannot answer `dirty`, and `dirty` is the entire value of the
non-production path: a modified tree's commit is a starting point, not an identity. The call is
anchored to the package location rather than the cwd (or the app would report the HEAD of whatever
repository it was launched from), uses `--no-optional-locks` (the production rootfs is read-only
and `git status` would otherwise refresh the index), and treats a missing binary, a non-zero exit
and a timeout identically as "we do not know".

**`final.json` names one build, not all of them.** `_run_fingerprint` deliberately does not pin the
build, so a resumed run may cross a deploy — refusing to resume after an unrelated deploy would
discard work for no epistemic gain. The summary therefore names the build that *finalized* the run,
and the `startup` events are the full list. [run-provenance.md](../run-provenance.md) states this and
gives the query.

**Deliberately not done.** No `builds_seen` list in the summary: `_finalize` never reads
`events.jsonl` today, and adding I/O plus a failure mode to the terminal write path is not worth
data already recoverable from the events. No separate hash of the roster or the prompts — the
roster is tracked, so the commit covers it, and the `startup` event already records the resolved
identities and budgets, which is what actually varies between runs on the same commit. No
invariant, no controller or isolation surface touched: `OrchestratorView` forbids extras and is
built field by field, so a key in the store cannot reach it, and a test asserts the stamp never
appears in `signals/views.jsonl`.
