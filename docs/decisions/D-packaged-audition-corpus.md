## D-packaged-audition-corpus — the audition corpus is package data, not test data

**The problem.** `ra audition` could not run on the published image. `DEFAULT_FIXTURE_DIR` was
`Path(__file__).parent.parent.parent / "tests" / "fixtures" / "audition"` — a path one level *above*
the package, which exists only in a source checkout. The runtime image copies `/app/.venv`,
`/app/src` and `/app/config` and never `tests/` (`.dockerignore` excludes it deliberately, so that
even a future `COPY . .` would not reintroduce the suite), and an installed wheel has no sibling
`tests/` at all. So the command failed immediately, on the documented, supported way to deploy this
system:

```
fixtures: fixture corpus not found at /app/tests/fixtures/audition
```

`--fixtures` only redirects `load_fixtures` at a *different* directory; there was no bundled corpus
anywhere in the package or the image to fall back to. Nothing announced this. The README already
tells deployers how to keep `ra audition`'s *cache* writable inside the container, which is the
shape of a command everyone believed ran there.

D-critic-audition is the reason that matters. Its whole claim is that the demonstrated-capability
term is checkable by the deployer who is actually staffing the lenses — the failure it was built
after (run-d5934276fafd) was two silent critics turning *no eligible reviewer can find a material
defect* into a tautology that the run's counters could not distinguish from a real review. A
capability that only exists for people who clone the repository does not discharge that; it leaves
every packaged deployment with the same blind spot the decision exists to remove.

**The decision.** The corpus is **package data**. It lives at
`src/reasonable_answer/fixtures/audition/`, and `DEFAULT_FIXTURE_DIR` is
`Path(__file__).resolve().parent / "fixtures" / "audition"` — inside the package, beside the code
that reads it.

That is one location, not a search path, and the single location is the point: `corpus_hash` is the
identity of a measurement (D-audition-rubric-identity), so a fallback chain would be a way for two
different corpora to be measured under one command with nothing in the output saying which. The
override stays where it belongs, as the explicit `--fixtures` flag.

Every distribution shape then carries it with no second mechanism:

| shape | why it has the corpus |
|---|---|
| source checkout | the path is in the tree |
| editable install (`uv sync`) | `reasonable_answer.__file__` resolves into `src/`, where the corpus is |
| wheel | hatchling copies everything under `packages = ["src/reasonable_answer"]` — the same reason `web/static/`'s icons, manifest and service worker already ship |
| runtime image | `COPY --from=build /app/src /app/src`, already there for the code |

No `COPY` was added to the `Dockerfile` and no carve-out to `.dockerignore`, which keeps the
wholesale `tests` exclusion intact.

**The corpus identity is unchanged, and that was checked, not assumed.** `load_fixtures` hashes each
fixture *directory name* and the raw bytes of its `artifact.md` and `manifest.yaml` — never the
containing path — so the move leaves `corpus_hash` byte-identical at `9c248e1d249ad301`, the same
value the [2026-08-10/11 operator record](../model-evaluation-record-2026-08-10.md) names. Every
cached verdict therefore stays fresh and every recorded measurement stays comparable. `RUBRIC_VERSION`
is untouched, correctly: no grading code changed. A relocation that had moved the hash would have
silently read every slot back as *not audited* and charged the deployer a full re-measurement to
learn nothing new.

**Alternatives rejected.**

*`COPY tests/fixtures/audition` into the image, with a `!tests/fixtures/audition` negation in
`.dockerignore`.* Fixes the image and only the image: a `pip install` still has nothing. It also
leaves `DEFAULT_FIXTURE_DIR` resolving through `parent.parent.parent`, i.e. through the *layout of a
checkout* — which the image reproduces only because `uv sync` installs the project editable. Passing
`--no-editable`, a flag that has nothing to do with auditioning, would break the command again for a
reason no one would connect to it. And it spends the `.dockerignore` exclusion that is written down
as belt-and-suspenders.

*`force-include` into the wheel, as `config/roster.default.yaml` does.* This is the closest
precedent and it does not transfer, which was measured rather than reasoned about: hatchling places
a force-included file in `site-packages/reasonable_answer/`, while under an editable install
`reasonable_answer.__file__` resolves into the source tree — so `PACKAGED_CONFIG` points at
`src/reasonable_answer/_default_roster.yaml`, which does not exist. The roster survives that because
`$RA_CONFIG` and `/etc/ra/roster.yaml` reach a real file first in the image. The corpus has no second
path; force-include would have fixed the wheel and left the image, the deployment everyone actually
runs, exactly as broken.

*Fetch or mount the corpus at runtime.* Makes a measurement depend on egress, or on the operator
having mounted the right corpus. Since the hash *is* the identity, a stale or edited mount produces
verdicts that read identically and mean something else. A deployer who wants a different corpus —
including the held-out private one D-critic-audition defers — passes `--fixtures` and owns that
choice explicitly.

*Document a mount workaround and change no code.* Leaves the default a dead end and asks every
deployer to carry a checkout to use a command whose stated purpose is that they need not trust ours.

**The cost, stated plainly.** 44 files and ~364 KB of Markdown and YAML now sit inside a Python
package, read by exactly one command that most deployments never run. Against an image already
measured in hundreds of megabytes, and against the alternative of a verification capability that
only clones have, that is the cheaper half of the trade. Publishing them more widely changes no
exposure: the corpus is already public in this repository, which is the premise of D-critic-audition's
deferred held-out-corpus item, and shipping the same bytes in a wheel adds nothing to what a training
crawl can already read.

**Not fixed here.** `ra audition-refine` has the identical defect — `refine_audition.py`'s own
`DEFAULT_FIXTURE_DIR` points at `tests/fixtures/refine` (D-refine-audition) and is missing from the
image for the same reason. It is out of scope of the issue this decision answers and is left for a
change that can be reviewed on its own; the mechanism above applies to it unchanged.

**Invariants.** None of the six pipeline-core invariants (author exclusion, blind orchestrator,
fail-closed lenses, severity floors, termination, untrusted text) is in reach. This moves files and
one path expression; it changes no context a model is handed, no grading rule, and no stop decision.
Fixture artifacts remain untrusted data fenced by `prompts`, critiqued under the `AUDITION_AUTHOR`
sentinel exactly as before, and the audition still never feeds the controller.
