## D-audition-source-mode — the audition measures the source-less floor, and the verdict says only that

`audition.run_assignment` calls `critique_once` with `sources=None`, on every fixture, for every
lens. The production deployment runs `verify_sources` always-on
([deployment-profile.md](../deployment-profile.md)), so its evidence critic reads a prompt this
harness never builds: `misrepresented_source` sharpened from *"the cited source plainly does not
support the claim"* into *"the fetched page does not contain the claim"* (`prompts.critic_user`),
the pages themselves in a `fetched_sources_block` with its three entry shapes (D-existence-vs-body),
and the standing instruction not to re-raise a definitive not-found that
`triage.mechanical_citation_issues` has already minted (D-notfound-fabrication). The verdicts are
named `fit` and `unfit`, which read as unconditional. They are not, and until now the gap was
recorded nowhere.

**Decision. The audition measures the capability floor a critic brings with no source access —
deliberately — and that scope is now stated in the code, carried in the cache identity, and
written here.** Four reasons, in the order they carry weight.

**The floor is real, not hypothetical — and it sits strictly below production's failed-fetch
case, not level with it.** `sources=None` matches exactly one production state: a report with no
citations to check at all. It does not match a paywalled, blocked or offline citation, because
fetching is best-effort by construction — sites block automated clients, paywall bodies, serve
formats the extractor cannot read, or go offline, and this system refuses the tricks that would
get around that (D-existence-vs-body) — but a failed fetch is still a *fetch attempt*, and
`fetched_sources_block` still renders an entry for it, telling the critic to judge that citation
*on its face*. A critic under `sources=None` never sees that instruction, or the fact that a
citation was attempted at all. A critic that cannot find a defect with no source scaffolding
whatsoever is therefore failing a strictly harder bar than any real evidence critic runs against
— which is why an `unfit` here is trustworthy evidence of a problem, even though a `fit` cannot
promise the model would also succeed once handed even a failed-fetch entry.

**One definition of "the prompt", across all three lenses.** The logic and completeness lenses
receive no sources under any configuration. A harness that fed a packet to evidence alone would be
taking two different measurements and printing both as `fit`, and the position-aware roster
warnings compare verdicts across lenses.

**Determinism.** `corpus_hash` keys every cached verdict to the exact bytes of the corpus. A
measurement whose inputs depended on what the network returned that day would be keyed to nothing,
would differ between machines, and would rot as cited URLs die — the same reason the whole test
suite is offline.

**The direction the gate actually uses survives the narrowing.** `audition.enforce` blocks only on
a positive `unfit`, and `unfit` here means the model found nothing obvious in text handed to it
directly, with no source scaffolding at all. In principle that is over-strict — a model could be
blind with nothing and sharp once handed even a failed-fetch entry — and that risk is accepted
because it fails toward re-rostering, which is this project's posture: a model that cannot pass
the harder bar is not thereby known to fail the easier one, but nothing here is claiming it does.

**What a `fit` verdict certifies.** That the model raises material, correctly-anchored, in-scope
findings against the artifact text alone, and does not invent them against a sound control, with
no fetched-source scaffolding of any kind in its context. For the evidence lens specifically, that
is a floor strictly below the on-its-face standard production actually runs when a citation is
attempted and its body does not arrive — that case still gets a `fetched_sources_block` entry
naming the failure, which this measurement never exercises.

**What it does not certify, and no threshold change would.** Three things, all of them real:

- **Use of fetched page text.** The sharpened `misrepresented_source` — the strongest check the
  production evidence lens has — is never exercised. (#118 covers the unfetched form of that
  category, which the corpus also lacks; the fetched form needs the packets below.)
- **The discipline of the fetched-sources block.** Not re-raising the `NOT FOUND` case triage has
  already recorded (a duplicate at the blocking floor), not reading `BLOCKED` as fabrication, not
  reading a metadata-only entry as a body. Each is a failure mode D-notfound-fabrication and
  D-existence-vs-body exist to prevent, and this harness can see none of them.
- **The noise direction with a page in context.** Sensitivity plausibly only improves when a critic
  is handed evidence. Over-flagging does not: a fetched page is more surface to over-read, and
  `control_material_rate` is measured without one.

**`fabricated-citation-01` stays `tier: obvious`, and is not measuring a superseded capability.**
Only an HTTP-definitive not-found is settled mechanically (D-notfound-fabrication); a fabricated
citation whose URL is blocked, paywalled, or resolves to an unrelated live page leaves the
judgment exactly where this fixture puts it — with the critic, on the face of the text. What the
fixture cannot measure is the duplicate case in the list above.

**`prompt_hash()` now describes what it covers.** Its docstring claimed "every prompt surface a
critic sees" while hashing `critic_user(lens, "q", "body", None)` and nothing else. The claim is
corrected rather than the coverage widened: the hash covers the surface the harness measures, plus
`AUDITION_SOURCE_MODE` as an explicit component of the identity.

*Rejected: hashing the sources-present surface too.* It would invalidate every cached verdict
whenever anyone edited a prompt fragment no measurement had ever used — discarding results that
remain exactly as true as the day they were recorded — and would advertise a coverage the corpus
does not have. The mode tag is what makes the narrower hash safe: a sources-present mode cannot
inherit these verdicts, because it will not key to them.

*Blast radius.* Introducing the tag changes the hash once, so every existing cached verdict stops
matching and reads *not audited* until `ra audition` is re-run — never `unfit`. The gate blocks
only on a positive `unfit`, so this is safe to land in a deployment running with enforcement on.

**Rejected: mirroring deployment by fetching the fixtures' own citations.** The planted citations
are fabricated by construction, so fetching them would measure how today's internet answers a
made-up URL — a 404 from a dead domain one week, a parked page the next — and the control
fixtures' real citations would rot on their own schedule. `ra audition` is a live command and may
spend proxy calls, but the corpus it grades against has to stay a fixed, hashable artifact that is
identical on every machine.

**Not done, deliberately: offline source packets.** Closing the gap for real needs no network — a
`sources.yaml` beside a fixture's `artifact.md`, deserialized into `FetchedSource` values covering
the outcomes that matter (a body that supports the claim, a body that does not, a `BLOCKED`, a
`NOT FOUND`, a metadata-only record), fed through the same `prompts.critic_user` call, and keyed in
the cache under a different `AUDITION_SOURCE_MODE`. It is a corpus change that belongs next to the
fixture work in #118 rather than bolted onto a scoping decision, so it is an open item below. The
mode tag is the seam it plugs into.
