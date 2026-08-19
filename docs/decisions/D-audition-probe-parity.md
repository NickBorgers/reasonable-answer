## D-audition-probe-parity — the audition measures a critic in the extraction regime a run would pin it to

**The problem.** `ra audition` built an `LLMClient` and went straight to `run_audition` without ever
calling `client.probe_structured_output`. Both of the other paths that use the client do probe:
`ra doctor` fills a whole column with the results, and `graph.build_runtime` probes every alias at
startup and logs the pinned mode. Unprobed, `LLMClient.structured` falls through to
`mode = mode or self.mode_for(alias)`, and `mode_for` answers the default `"prompt"` for any alias
it has never probed — the weakest rung of the extraction ladder.

So every audition call was made under prompt-mode extraction, for every model, whatever mode a run
would pin that model to. The harness certified critics in a regime production does not run them in.
A model reliable under `json_schema` but flaky under prompt extraction is graded on failures it will
never have in production; a model that is the reverse gets a pass it has not earned. Neither
direction was visible in the verdict, because the verdict did not record which regime produced it.
`schema_failures` is the counter most directly affected — it is a count *of* an extraction path —
and it feeds a hardcoded `unfit` gate, not a tunable threshold.

The gap was found by adversarial review of the 2026-08-02 audition run and its 30-call spot check,
which observed no schema failures attributable to it in the sampled pairs. So this is a fidelity
gap, not the cause of the noise findings that run reported. `ra audition-refine` had the identical
gap against `RefinementService.preflight`, which does probe before serving.

**The fix, part one: probe before measuring.** Both audition commands now probe every alias they
will call, before any measurement and before any cached verdict is read. The probe memoises, so the
harness's own calls cost nothing extra.

An alias that cannot be pinned to any mode **fails the command closed** (exit 2), rather than being
auditioned under the fallback. That is parity too: `build_runtime` refuses to start a run staffed by
such an alias, so measuring it under a mode no run would use would be this same defect in a new
place. The cost is that one unprobeable model blocks the whole command; `--alias` and `--lens` are
the escape, and the operator's fix — re-roster it — is the same either way.

**The fix, part two: the verdict names the regime.** `CacheEntry.structured_output_mode` is a
required field with no default, and a term in `matches()`. The precedent is
D-audition-rubric-identity: an entry that cannot say what regime produced it fails
`model_validate`, `load_cache` drops it, and the pre-probe cache reads as *not audited* — never as a
pass carried across a regime change. The same field, for the same reason, is on `RefineCacheEntry`.
`ra audition --json` and `ra audition-refine --json` both emit the mode, and the table prints it
beneath itself, so a report that does not name its regime cannot be mistaken for one taken in the
right regime.

**The decision the issue asked to be made explicitly: the mode is compared on the measuring path and
only reported on the free ones.** `matches()` takes `structured_output_mode` as a required keyword
that accepts `None` to mean *deliberately not compared*. `ra audition` and `ra audition-refine` pass
the probed mode and re-measure on a mismatch. `cached_judgements` — and through it `ra doctor`'s
table and the `audition.enforce` startup gate — and `refine_cached_judgement` pass `None`.

That asymmetry is the whole of this decision, and it is not the obvious symmetric answer, so:

- **Every other term in the identity is free to compute; this one is not.** The corpus hash, the
  prompt hash, the rubric hash and `require_verbatim_spans` come from disk, from code, or from
  config. The mode comes from probing a paid proxy. `cached_judgements` promises never to spend —
  `test_the_gate_takes_no_client_and_so_can_never_spend` pins it, because the gate runs on every
  `ra run` and every web boot, and a keyless checkout must still boot. Making the mode a term in the
  free read would mean either handing the gate a client (forbidden) or threading a probed map into
  it from `build_runtime`, which would move the probes *ahead* of the gate, so a roster with a known
  `unfit` critic would start spending before being refused.
- **A non-deterministic prober would silently disarm enforcement.** The roster in force when this
  decision landed documented `minimax-m3` as probing non-deterministically across `json_schema`,
  `json_object` and `prompt`.
  If the free read dropped a mode-mismatched entry, that model's `unfit` verdict would stop blocking
  on most boots — not because anything was re-measured, but because the probe landed elsewhere. The
  gate blocks only on a *positive* `unfit`, so every invalidation is a step toward not blocking.
  Trading a real block for a mode-fidelity scruple is the wrong direction for a fail-closed gate.
- **Reading across the difference is only safe if the difference is visible.** So `mode_drift()`
  reports every slot whose cached verdict was measured under a mode the alias no longer probes to,
  naming both modes and the re-measure command. `ra doctor` prints it beside the other roster
  warnings — free there, because doctor probes every alias anyway. It takes the probed modes as
  data, never a client, and the no-client test now covers it too.

The net effect is that a verdict is *measured* under the production regime and *invalidated* the
moment a paying caller sees a different one, while a free reader keeps whatever measurement exists
and is told when it disagrees with today's probe.

**Cost.** `ra audition` now spends one probe call per distinct alias in the filtered slot set, even
when every verdict is cached, because the mode has to be known before the freshness check. That is
bounded by the roster size and is a rounding error against |models| × |fixtures| × repetitions.
Existing `.ra-audition.json` and refine cache files are dropped as unvalidatable, so the first
audition after this lands re-measures the roster — the same one-off cost D-audition-rubric-identity
accepted, and safe to land under enforcement for the same reason: it degrades to *not audited*.

**Deliberately not done.** The mode is not hashed into `prompt_hash()`. That hash is about the
prompt *surface* — what text a critic is shown — and D-audition-source-mode already fixed its
meaning to the source-less surface; folding a per-alias, probe-dependent value into a corpus-wide
hash would invalidate every model's verdict whenever any one model's probe moved. The mode is not
added to `Metrics` either: it is a condition the measurement was taken under, which is what
`CacheEntry` holds, and putting it there would change `rubric_hash`'s field set for a value that is
not a grading rule. `graph.build_runtime`'s ordering is unchanged — the cache-read gate still runs
before the probes. And nothing here changes what the audition *grades*: `prompt_hash`,
`rubric_hash`, the thresholds and the fixture corpus are untouched, so a verdict's meaning changes
only in that it now says which extraction path produced it.
