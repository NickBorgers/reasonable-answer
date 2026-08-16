# Run provenance: which build produced a run

A run records what it concluded. This page is about the other half — what *produced* it, and how
to use that to compare runs across a change.

The question this exists to answer is a real one and it recurs: *we changed how revisions are
scoped last week and we are still not converging — which of these runs already had that change,
and which predate it?* Without a stamp the only way to answer is to line up run timestamps against
`git log`, which is wrong whenever a deploy lags a merge, whenever one PR carries two fixes, and
whenever a run was resumed. So every run names the commit it ran on.

## What is stamped, and where

Three places, all written automatically (D-run-build-stamp):

| location | when | what it names |
|---|---|---|
| `events.jsonl`, `queued` events | a run is submitted or resumed | the build that accepted the work |
| `events.jsonl`, `startup` events | the graph begins an attempt | the build running **that attempt** |
| `final.json`, `build` key | the run finalizes | the build that produced the shipped report |

The record is the same shape everywhere:

```json
{"commit": "2a93494b045ca8117e87e7fadb24f53db106760e", "dirty": false, "source": "image"}
```

`source` says how confidently the commit is known, and it is recorded so a reader never has to
infer it:

- **`image`** — baked into the container by CI, which knew the commit exactly. This is the
  production path and it is authoritative.
- **`git`** — read from the checkout the code is running out of. Covers `uv run`, the devcontainer
  and the test suite. This is the only source that can report `dirty`.
- **`unknown`** — neither was available. Recorded honestly, never guessed. `ra doctor` warns about
  it, and so does the first run in the process.

`dirty` is `true` when the working tree had uncommitted changes, `false` when it did not, and
`null` when nothing checked — a `git` run whose `git status` failed, or an `image` build. A `dirty`
run's commit is a starting point, not an identity: the code that ran was that commit plus edits
nobody recorded.

## Attributing a run to a build

List what you have:

```bash
jq -r '[.run_id, .build.commit, .build.source, (.build.dirty|tostring)] | @tsv' runs/*/final.json
```

Then, for a given fix, bucket the runs. `git merge-base --is-ancestor A B` exits 0 when `A` is an
ancestor of `B` — that is, when the run at `B` **already contained** the fix at `A`:

```bash
FIX=$(git rev-parse c749b5e)      # the commit that introduced the change you are assessing
for f in runs/*/final.json; do
  sha=$(jq -r '.build.commit // empty' "$f")
  [ -n "$sha" ] || { echo "unstamped $(dirname "$f")"; continue; }
  if git merge-base --is-ancestor "$FIX" "$sha" 2>/dev/null; then
    echo "after   $(dirname "$f")"
  else
    echo "before  $(dirname "$f")"
  fi
done
```

`git merge-base` needs the commit to exist locally: `git fetch origin` first if a run was produced
by a build you have not pulled.

Once bucketed, the interesting comparison is usually `terminal_status` and `rounds` from the same
`final.json` — did the runs that had the fix converge more often, or in fewer rounds, than the ones
that did not.

## What the stamp does not tell you

**`final.json` names one build, not all of them.** A run that is interrupted and resumed can cross
a deploy: the resume fingerprint (`_run_fingerprint` in `graph.py`) deliberately pins the question,
seed, roster and budgets, but *not* the build, because refusing to resume a run after an unrelated
deploy would throw away work for no epistemic gain. So `final.json`'s `build` is the build that
finalized the run. When that distinction matters, the `startup` events are the full list:

```bash
jq -r 'select(.kind=="startup") | .build.commit' runs/<run_id>/events.jsonl
```

More than one line means the run spanned a deploy, and any comparison that treats it as a single
build's output is reading it wrong.

**Runs that predate this are unstamped, and stay that way.** There is no backfill. A commit could
be *inferred* from a run's start time, but an inferred value is indistinguishable from a measured
one once written, and a wrong attribution is worse than a missing one. Unstamped runs have no
`build` key, and every surface omits the field rather than showing "unknown".

**The roster is not separately versioned.** `config/roster.yaml` is tracked, so the commit covers
it — but production mounts its own copy over the baked one, so a roster edited on the host is
invisible to the stamp. The `startup` event records the resolved model `identities`, `budgets` and
enabled tiers alongside the build, which is what actually varies; check there before concluding two
runs on the same commit were configured identically.

**Two attempts of one run can have had different rosters.** `unreachable_aliases` on each `startup`
event lists the aliases that attempt could not probe and therefore ran without (D-degraded-roster).
It is empty on a healthy start. Because it is per attempt rather than per run — an outage ends, and
the next resume gets the full roster back — comparing runs means reading it on the attempt that
produced the rounds in question, exactly as with `build`:

```bash
jq -r 'select(.kind=="startup") | [.ts, (.unreachable_aliases | join(","))] | @tsv' \
  runs/<run_id>/events.jsonl
```

If the missing aliases leave any lens with fewer than two eligible critics, the run can reach at
best `converged_unconfirmed`, never `accepted` — it may equally end `exhausted_unresolved`,
`needs_human_review`, or `aborted` if it never comes clean — so a verdict short of `accepted`
carries that roster limitation too, but only the events say *which* models were missing. A reduced roster that still leaves every lens
with at least two eligible critics may still reach `accepted`.

## Keeping the stamp working

The production path depends on CI passing `RA_BUILD_SHA` as a build argument
(`.github/workflows/docker-release.yml`) and on the `Dockerfile` turning it into an environment
variable. Either one silently dropped would degrade production to `source: "unknown"` — visible in
`ra doctor` and in the first run's warning, and asserted by `tests/test_build.py`.

For a locally built image, pass it yourself:

```bash
RA_BUILD_SHA=$(git rev-parse HEAD) docker compose up -d --build
```

Left unset it stays empty, which the app reads as "unknown" rather than as an authoritative empty
commit — the reason the check is for a *non-blank* value rather than a set one.
