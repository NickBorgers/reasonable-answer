<p align="center">
  <img src="docs/assets/logo.png" alt="reasonable-answer" width="140">
</p>

# reasonable-answer

[![PR Validation](https://github.com/NickBorgers/reasonable-answer/actions/workflows/pr-validation.yml/badge.svg)](https://github.com/NickBorgers/reasonable-answer/actions/workflows/pr-validation.yml)
[![Docker Release](https://github.com/NickBorgers/reasonable-answer/actions/workflows/docker-release.yml/badge.svg)](https://github.com/NickBorgers/reasonable-answer/actions/workflows/docker-release.yml)

Takes a question (and optionally a seed report) and produces a higher-quality report whose
argument is *sound* — where "sound" means **no eligible reviewer can find a material defect**,
not that anyone asserted it was good.

New here? Start with [docs/concepts.md](./docs/concepts.md) — the approachable tour of *why* the
system is shaped this way. The design specs are in [docs/DESIGN.md](./docs/DESIGN.md), and the
whole set is published at <https://nickborgers.github.io/reasonable-answer/>. This README is
about running it.

## How it works, in one paragraph

Models take turns **writing** and **critiquing** a report, and a report is never critiqued — on any
dimension — by the model that wrote it. By default, three lenses (logic / evidence / completeness)
read every draft, each through two cross-family critics; `review.depth` and `review.per_lens`
configure that depth. Each critic runs in a fresh, authorship-blind context and emits issues against
a closed schema. A mechanical
triage step clamps severities to category floors, turns the issues into depersonalized fix-tasks for
the next writer, and projects a content-free count summary for a **blind referee**. The referee — a
deterministic controller, assisted by an LLM whose only authority is a cosmetic-polish judgment —
decides continue / finalize / abort, and it never sees the report.

## Quick start

The repo is a devcontainer: clone, open, run the tests.

```bash
make test      # full offline suite — no network, no API keys
make doctor    # resolve every roster alias against the LiteLLM proxy, report health
make audition  # measure whether each rostered critic can actually perform its lens
make serve     # web interface on http://127.0.0.1:8080
make run Q="Does a four-day work week increase productivity?"
```

With a seed artifact to improve. The seed does not have to be markdown — a PDF,
a Word document, a web page, `.txt` or `.html` is converted at ingest:

```bash
make run Q="Is this analysis sound?" SEED=draft.md
make run Q="Is this analysis sound?" SEED=q3-report.pdf
make run Q="Is this analysis sound?" SEED=https://example.org/whitepaper
```

Conversion is best-effort and aims at one thing: recovering the `#` headings that
critics cite loci against, and the `## Sources` list the evidence lens verifies. Where a
format carries no heading structure — a bare `.txt`, most PDFs — the seed is accepted
with a warning rather than rejected, and critics fall back to paragraph-level loci.
PDF support needs the optional extra: `uv sync --extra ingest`.

Or directly:

```bash
uv run ra run -q "your question" --seed draft.md --config config/roster.yaml -v
uv run ra run -q "your question" --seed https://example.org/report.pdf
uv run ra doctor
uv run ra audition
uv run ra purge <run_id> [--content-only]
uv run ra expired
uv run ra export <run_id> [--format md|html] [-o out.html]
```

## Web interface

`make serve`, or `ra serve --host 0.0.0.0 --port 8080` in a container. Submit a question, watch
the loop converge live, and browse your own past runs — the index is scoped to whoever is asking,
though anyone signed in can open a run they hold the id for.

The run page streams the pipeline's own event log over server-sent events, so you see each round
as it happens — which model wrote the draft, which critic drew which lens, what each one found,
and which controller rule fired:

```
round 2   writer deepseek-v4-flash
  logic         glm-5.2          2 issues
  evidence      glm-5.2          clean
  completeness  gemma4           clean
  1 major  ->  rule 14  generate  material issues remain
```

**Callers are identified by a header, and it is trusted rather than verified.** It comes from
whatever fronts the app — `Cf-Access-Authenticated-User-Email` from Cloudflare Access, or the
`Tailscale-User-*` headers from `tailscale serve` — and a request carrying neither is refused on
every route but `/healthz` and the `GET`s under `/runs/`, which are public: holding a run id is
the credential for reading that run, so a finished report can be shared with anyone (D-id-as-credential). Every
write still needs an identity. Runs belong to whoever submitted them: your index shows only your
own runs, and only its owner can resume a run.

Because the header is not verified, anyone who can reach the port directly can claim to be any
user. `ra serve` binds `127.0.0.1` by default and `compose.yaml` publishes only to loopback for
that reason: keep the proxy the only way in. See [docs/authentication.md](docs/authentication.md)
for the Cloudflare Access setup, and D-identity-header in [docs/decisions.md](docs/decisions.md) for what that
trade does and does not buy.

Showing reports and critiques to a *human* does not weaken the isolation design — blindness is
about what enters a *model's* context. The UI is a window onto the audit trail, which is the
reason the pipeline keeps one.

### Installing it on a phone

Behind `tailscale serve` the app is served over HTTPS, which makes it a *secure context* — so
Chrome offers **Install app** and iOS Safari's **Share → Add to Home Screen** gives a standalone
window with its own icon, no browser chrome, and the theme colour following light or dark mode.

Reaching it as plain `http://<tailnet-ip>:8080` will not offer installation. Service workers and
installability require a secure context; that is a browser rule, not a missing feature here, and
the page behaves identically apart from the missing offer. (`http://localhost` counts as secure,
so `make serve` does exercise the whole path locally.)

The service worker caches the icons, the manifest and a small offline page — **and nothing else**.
Runs are live data, so offline you get the offline page rather than a stale run status, and a run
page is never stored on the device at all. See [D-installable-pwa](docs/decisions.md) for why that is a
structural property rather than a rule someone has to remember.

The icons are the project logo. To use your own, replace the PNGs in
`src/reasonable_answer/web/static/icons/` keeping the same filenames and pixel sizes, and restart
— no Python to touch, and nothing to clear on already-installed devices. See
[the README in that directory](src/reasonable_answer/web/static/icons/README.md) for what each
file has to be, and `scripts/make-icons.py` if you want its generated placeholder set — a plain
check on the accent-blue plate — instead.

## Sharing a result

Anyone who holds a run id can open that run — signed in or not — because every `GET` under
`/runs/` answers an unauthenticated caller (D-id-as-credential). So the URL a reader is looking at is the one
they can hand to someone, inside the tailnet or Access audience or outside it: sharing is handing
over a **link**. A **file** is the durable alternative — for a recipient who cannot reach the
host, or a copy that must outlive the run's retention sweep. Every export carries
the report *and* its review record — status, sourcing label, which round shipped, the reviewers
whose clean records key to that exact artifact, and any outstanding defects. As prose, an
`accepted` report and a `needs_human_review` one look identical; that difference is the whole
product, so it travels with the text (D-verdict-attached).

The report is rendered on exactly one page — `/runs/<id>/report` — and that is where all of this
lives. `/runs/<id>` is the run itself: the verdict, the round-by-round trail, `audit.json`, `Ask
this again`, and a link to the report.

| from the report page | what you get |
|---|---|
| **Copy markdown** | report + record on the clipboard, for a message or a doc |
| **Download .md** | the same thing as a file |
| **Download .html** | one self-contained page — no font, script, stylesheet or image is fetched when it is opened |
| **audit.json** | the whole trail behind the verdict: rounds, reviewers, every event |
| **Print → Save as PDF** | the same page with a print stylesheet: no nav, no buttons, serif body, forced light colours, the record as a final page |

PDF is the browser's own print path rather than a server-side renderer — no extra dependency, and
the printed page cannot drift from the page you printed it from, because it is one stylesheet.
Dark mode is explicitly reset for print, so a phone in dark mode does not produce black pages.

`GET /runs/<id>/report.md` is untouched and remains the raw shipped artifact, for anything that
hashes or diffs a report — a route rather than a button, since beside **Download .md** it offered
the same text minus the review record. Note that `purge --content-only` deletes `final.md`, so an
export is what outlives the retention sweep.

## Docker

```bash
docker compose up -d
```

~236 MB, `python:3.12-slim`, runs as uid 10001. Three things it needs:

| | why |
|---|---|
| the host can reach the LiteLLM proxy | assumed, not configured here — on this network that means the host is on the tailnet |
| a volume at `/data/runs` | holds the audit trail *and* the SQLite checkpoints; resumability dies without it |
| `roster.yaml` at `/etc/ra/roster.yaml` | change models without rebuilding |

The image bakes [config/roster.default.yaml](./config/roster.default.yaml) at that path, so it starts
with no mount at all — that copy leaves every opt-in that needs a reachable proxy or a credential
(search, refinement) off. `docker compose` mounts [config/roster.yaml](./config/roster.yaml) over it,
which is where this deployment's opt-ins live.

Use a **named volume** if you can. A bind-mounted host directory arrives owned by root while the
container runs unprivileged; the app detects this at startup and tells you what to chown rather
than failing on your first submission.

### The container is immutable at runtime

`compose.yaml` runs it `read_only`, with `cap_drop: ALL` and `no-new-privileges`, and the image
owns `/app` as **root** while the process runs as uid 10001. So the code cannot rewrite itself:
a compromised run has nowhere to leave anything that the next restart would execute — which
matters here more than usual, because restarts are routine (an interrupted run is re-enqueued at
boot). Root ownership of `/app` is not conditional on compose; it holds for a plain `docker run`
too.

Exactly two paths are writable: the `/data/runs` volume — the audit trail and the SQLite
checkpoints, which is all the app ever writes — and a 64 MB `noexec` `/tmp`. `/tmp` is needed
because Starlette spools an oversized multipart part to a temp file before FastAPI can reject it,
and because SQLite spills there.

`scripts/smoke-test-image.sh` asserts both properties against every built image, so neither can
regress quietly. Three consequences worth knowing:

- **`ra audition` cannot save its cache inside the container.** It writes `.ra-audition.json`
  relative to the working directory (`/data`), which is read-only. Run it from a checkout with
  `make audition`, or point `audition.cache_path` at `/data/runs/.ra-audition.json` in the mounted
  roster. *Reading* the cache — all that `ra doctor` and the roster eligibility checks do — is
  unaffected.
- **`ra export -o <path>`** must write somewhere under `/data/runs`.
- **`search.token_file`** is likewise `/data`-relative, so it is unreadable here. That was already
  true (the file is not in the image) and nothing depends on it: the env var wins by design, and
  `compose.yaml` supplies `BRAVE_SEARCH_API_KEY`.

What this does *not* cover is the image's provenance. `compose.yaml` builds from the checkout, so
the running code is whatever the host's working tree held — not the digest-verified image
`Docker Release` publishes to GHCR.

No database, no broker, no GPU, no model weights — all inference goes through the proxy.

**Behind a reverse proxy that strips a path prefix** (e.g. Cloudflare Access serving the app
under `/app` while `/` stays a public landing page), set `RA_ROOT_PATH=/app`. Every
app-internal URL the app emits — links, redirects, form actions, the PWA manifest, the live
stream and the service worker — then carries the prefix and stays same-origin, so nothing
escapes back to the root. The one deliberate exception is the static "how this works" link in
the header, which points off-origin at the published docs site; it is navigation only, carries
`rel="noreferrer"` so following it never hands a run id to that host, and fetches nothing, so
the CSP is unaffected. The app
still receives the stripped path, so the proxy is the ordinary
`location /app/ { proxy_pass http://ra:8080/; }` (the trailing slashes strip `/app/`). Unset,
it serves at the origin root exactly as before. The CSP is unchanged: this is purely
path-prefixing, not a relaxation. See D-base-path.

**To let a finished run be shared with anyone**, add `RA_PUBLIC_ROOT_PATH=/` and route
`/runs/` to the app path-preserving, without Access in front. Every `GET` under `/runs/` —
the run page, the report, the exports, `audit.json`, the live stream — answers an
unauthenticated caller, so the URL a reader is looking at is the one they can send to
someone; every write stays behind the gate. Unset, it falls back to `RA_ROOT_PATH` and
nothing changes. See [D-id-as-credential](./docs/decisions.md) and
[authentication.md](./docs/authentication.md).

**To be told when a run finishes**, turn it on in the roster and supply a contact address in
the environment:

```yaml
push:
  enabled: true
```

```bash
RA_PUSH_SUBJECT=mailto:you@example.com   # or a bare https://your.site
```

The address is the VAPID `sub` claim ([RFC 8292 §2.1](https://datatracker.ietf.org/doc/html/rfc8292#section-2.1))
— how a push service reaches whoever operates this server. The RFC *recommends* it (`SHOULD`); the
**hard requirement is `py_vapid`'s**, which refuses to sign a token whose `sub` is missing, so
startup fails closed rather than letting notifications silently never arrive. It is an env var and
not a roster key for the same reason `RA_CONTACT_EMAIL` is: the roster is committed, and this is a
personal address. A `mailto:` needs the scheme; an `https://` value must be a bare host with no
path.

On the next boot the app generates a VAPID keypair at `<runs_dir>/.vapid-private.pem` and the
header grows a **notify me** button — on every page, so it is there on the run page you land on
after starting something. Tap it once per device and a run that
stops — finished, or dead — pushes a notification naming the question and its status, which
opens the report. The button disappears once that device is subscribed: there is nothing left
to do, and turning notifications back off belongs to the OS, which owns the permission. There is nothing to register with anyone: no Firebase project, no APNs
certificate, no app store. The server needs outbound HTTPS to whichever push services your
`push.endpoint_hosts` allows; the default list covers Apple, Google, Mozilla and Microsoft
(`web.push.apple.com`, `fcm.googleapis.com`, `*.push.services.mozilla.com`,
`*.notify.windows.com`), so an egress policy that omits Firefox or Windows will break
subscriptions from those browsers even though the app accepts them. The page needs HTTPS,
which installation already required.

Two things to know. **Back up `.vapid-private.pem`** — every subscription is bound to the key
it was minted under, so losing it invalidates all of them silently, with no channel left to
ask the devices to re-subscribe. And **on an iPhone the app has to be on the home screen
first**: iOS gives push only to Home Screen web apps and only prompts in response to a direct tap
([WebKit, 2023](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)), and a
declined permission prompt can only be reset by deleting and reinstalling, so the button appears
only once it can actually work. See [D-stop-notification](./docs/decisions.md).

## Configuration

Everything lives in [config/roster.yaml](./config/roster.yaml). The roster is **role-structured**:

```yaml
roster:
  writers: [mistral-large-3, deepseek-v4-flash, nemotron-3-ultra]   # models that author reports
  orchestrator: gemma4-small                                       # blind referee (optional; default writers[0])
  critics:
    logic:        [glm-5.2, minimax-m3, mistral-large-3]
    evidence:     [glm-5.2, minimax-m3, gemma4]
    completeness: [gemma4, glm-5.2]
```

Every entry is **open-weight** and small enough to load on the target local box (see
[docs/DESIGN.md](./docs/DESIGN.md) for the footprint table). `glm-5.2` is deliberately
*critic-only*: as a writer it would be barred from reviewing its own drafts, which would cost the
roster its best reviewer on half of all rounds.

The `orchestrator` decides only whether a cosmetic polish pass is worth running. It sees bounded
counts and returns one boolean, so it runs on the cheapest local model in the roster; if it fails,
the run simply skips polish.

Models are addressed as **LiteLLM proxy aliases**; the proxy is one OpenAI-compatible endpoint for
cloud and local models alike. At startup each alias is resolved to its underlying
`provider/model`, and **distinctness is enforced at that level** — two aliases pointing at one
model do not count as two independent reviewers.

Every lens wants **≥2 eligible non-author model families**. `make doctor` tells you whether you
have them:

| roster shape | strongest possible outcome |
|---|---|
| ≥2 eligible non-author families on every lens | `accepted` |
| some lens has only one family | `converged_unconfirmed`, naming the under-reviewed dimension |
| some lens has none | fails closed at startup |

Resolved identity still prevents aliases from duplicating one model, while the acceptance count is
over distinct model families: two checkpoints of the same base model remain one witness. `make
doctor` warns when author exclusion leaves a lens with only one eligible family.

Structural eligibility is necessary but not sufficient: a model can be non-author and distinct yet
still unable to perform its lens. `make doctor` therefore also reports each critic's **cached
audition status** alongside the structural check — but it never measures. `make audition` is what
measures, grading every rostered critic `fit` / `marginal` / `unfit` against fixtures and caching
the verdict. With `audition.enforce: true`, a critic assigned to a lens it holds a usable cached
**`unfit`** verdict on fails startup closed, before any tokens are spent; `marginal`, stale, and
not-yet-audited verdicts stay warnings, since they are absences of evidence rather than evidence of
incapacity. `make doctor` also warns when `enforce` is on but no assigned critic has a usable
verdict — an enforcement gate with nothing to read blocks nothing. (`audition.enabled` is not a
valid key; with `extra="forbid"` a roster still carrying it fails to load.)

Point `proxy.base_url` at any OpenAI-compatible endpoint. If yours needs a key, set
`LITELLM_API_KEY` (or change `api_key_env`).

## What the terminal statuses mean

| status | meaning | exit code |
|---|---|---|
| `accepted` | every lens cleared by ≥2 distinct non-author models on the identical final artifact | 0 |
| `converged_unconfirmed` | every lens cleared, but ≥1 lens had only one eligible reviewer | 0 |
| `exhausted_unresolved` | cap/stagnation reached with only non-blocking issues, or clean-but-unconfirmed | 1 |
| `needs_human_review` | cap/stagnation/cycle reached with **blocking** issues outstanding | 1 |
| `aborted` | fatal: model unavailable, or a review could not be completed at all | 1 |

A known-unacceptable artifact is never labelled `accepted` or `converged_unconfirmed` — that is a
tested property, not a convention.

**What to expect in practice.** With a strict roster, `accepted` is uncommon: a second reviewer on
a lens usually finds something the first did not, and each rewrite gives the next round new text to
object to. Runs that reach the cap ship the *best-scoring* draft, not the last one, with the
outstanding defects listed in `final.json`. Raise `hard_cap`, or narrow the question, if you want
more convergence pressure.

**Retrieval (optional, off by default).** Set `search.enabled: true` in the roster and writers get a
`web_search` tool backed by the Brave Search API, so the URLs in `## Sources` are ones a search
actually returned rather than ones the model remembered. Credential: `$BRAVE_SEARCH_API_KEY`, or a
gitignored `brave.token` for local work. Startup fails closed if the key is missing *or* if any
writer cannot emit tool calls — that writer would still be told to produce a `## Sources` section
and would fill it from memory, and no downstream check can tell a remembered citation from a
retrieved one. Each run carries a query budget (default 60) because the free tier is 2,000
queries/month; when it runs out the writer is told so explicitly rather than being handed silence.

**Source verification (optional, off by default).** Set `search.verify_sources: true` and the pages
the report cites are fetched and handed to the **evidence lens only**, as untrusted data. That turns
`fabricated_citation` and `misrepresented_source` from judgements about plausibility into checks
against the page. A cited URL that returns a definitive not-found — HTTP 404 or 410 Gone — is
treated as a `fabricated_citation`, because that status establishes the URL does not resolve rather
than that it could not be read. Every other failed fetch is explicitly *not* treated as evidence of
fabrication — a 403, a timeout, a paywall, an unreadable content type, or an empty body means the
fetch failed, not that the source is fake, because sites block automated clients, paywall, and go
offline. Reading a cited **PDF** rather than reporting it as an unreadable content type is a separate
opt-in tier (`sources.enabled` and `sources.pdf.enabled`, both off by default and needing the
`ingest` extra); `search.verify_sources` alone does not read PDF bodies. This fetches URLs a model
chose, which is SSRF exposure by construction; it is expected to be constrained at the network layer,
not here.

**Registry tiers (optional, off by default).** A direct fetch can fail for reasons that say nothing
about whether the source is real: a paywalled journal or a newspaper refusing an automated client
returns `blocked`, not proof of fabrication. Set `sources.enabled: true` together with
`sources.identifiers.enabled: true`, and a citation carrying a DOI or PMID is looked up at
Crossref/OpenAlex, which answers the question that actually matters for fabrication — does this
source exist — without needing the paywalled body, and stops a real paywalled paper looking like an
invented one. (arXiv ids and PMCIDs are covered when arXiv and Europe PMC are added to
`sources.identifiers.providers`, or through the open-access tier below.) Set
`sources.open_access.enabled: true` as well and a free copy is read where one exists, labelled as a
mirror rather than the version of record. Neither tier sharpens `misrepresented_source`: an
abstract is not the source's text. See D-existence-vs-body.

**Known limitations.** Output is labelled *consensus-reviewed with in-artifact sourcing* by default,
*…with retrieved sourcing* when `search.enabled: true`, and *…with verified sourcing* when
`verify_sources` is also on. **None of the three is fact-checked.** Verification establishes that a
cited source exists and, when a body can be read, that the page says something compatible with the
claim — not that the page is correct, and not that the roster chose good sources. A
registry-confirmed source whose body cannot be read proves existence only, and an open-access
mirror is disclosed as a different document from the cited page. With verification off, whether a
source supports the claim attached to it is unverified entirely. (See D-in-artifact-citations/D-retrieval-opt-in/D-source-verification/D-existence-vs-body in
[decisions.md](docs/decisions.md) and the evidence section of [convergence.md](docs/convergence.md).)

**Writer disputes (optional, off by default).** Set `disputes.enabled: true` and a writer that
believes a fix-task is factually wrong can dispute it with evidence instead of falsifying the
report to satisfy it. A citation dispute whose quote checks out against the cited page (with
`verify_sources` on) is upheld mechanically; anything else goes to a fresh-context arbiter model
that is neither the writer nor the critic that raised the finding, and that defaults to the
finding when uncertain. Upheld disputes suppress the re-raised finding for the rest of the run
(auditable in `events.jsonl`); everything else leaves the finding standing. See D-writer-disputes in
[decisions.md](docs/decisions.md).

A critic's quote fields (`claim_span`, `related_span`) are verified to be verbatim text from
the artifact, so a critic cannot smuggle invented text to the next writer that way. Its
`rationale` and `instruction` are still critic-authored prose; they are length-bounded, carry no
provenance, and reach the writer inside an explicit untrusted-data fence, but they are not
mechanically derived. Replacing them with generated text from the structured fields would close
that channel completely at some cost in fix quality.

## Output

Each run writes `runs/<run_id>/` (mode 0700):

```
final.md              the report that shipped
final.json            terminal status, clean records, outstanding defects, warnings, build
owner.txt             who submitted it; absent means the web interface will not serve it
events.jsonl          every stage: startup, intake, generate, critique, triage, control
reports/              every draft, with its author
critiques/            every lens result, with provenance
disputes/             every writer dispute, with its grounds (when enabled)
signals/views.jsonl   what the blind orchestrator saw, per round
signals/decisions.jsonl  which rule fired, per round
```

`reports/` and `critiques/` hold the sensitive material; `ra purge <id> --content-only` drops them
and keeps the decision record — and `owner.txt`, so a purged run stays in its owner's index.

Every run also stamps the commit it ran on, in `final.json` and on each `startup` event, so runs
can be sorted into before and after a given change — see
[docs/run-provenance.md](./docs/run-provenance.md) for the query.

`final.md` is the report on its own, which says nothing about how it ended. `ra export <run_id>`
joins it to `final.json` and writes the document you would actually give someone — see
[Sharing a result](#sharing-a-result).

## Speed is an anti-goal

The intended deployment is a slow local model. A run is many sequential model calls by design —
the three lenses parallelise, nothing else does. Resumability and the audit trail matter more than
latency.

## Development

```
src/reasonable_answer/
  taxonomy.py    categories, lenses, mechanical severity floors
  schemas.py     every boundary type; OrchestratorView is the isolation-critical one
  config.py      roster + budgets + fail-closed startup validation
  roles.py       who writes, who critiques, and the author-exclusion invariant
  llm.py         LiteLLM proxy client, identity resolution, structured-output ladder
  prompts.py     all prompts; untrusted data is fenced, roles never leak
  report.py      structural loci and artifact hashing
  triage.py      mechanical: floors, counts, defect list, clean records
  dispute.py     writer disputes: mechanical adjudication, arbiter eligibility
  controller.py  the 14-rule ordered stop decision — pure, deterministic, total
  graph.py       the LangGraph loop
  store.py       audit trail and retention
  export.py      report + review record, for markdown, self-contained HTML and print
```

The test suite is offline: a scriptable fake proxy drives the whole graph, so the loop's safety
properties (author exclusion, fail-closed lenses, termination, orchestrator blindness) are tested
without a network. `tests/test_controller.py` sweeps the controller's input space for totality and
for the property that no rule generates at or beyond the hard cap.

Lint with `uv run ruff check src/ tests/`.

## CI and agentic review

Every PR gets a secret-free validation run (ruff, the offline suite on 3.11 and 3.12, a lockfile
check, `actionlint`, and a container build with a health-check smoke test), plus an agent review
whose panel the diff selects: **invariant** and **docs** run on every non-empty PR, and **security**,
**test**, and **quality** are added by path rules (see [docs/ci-pipeline.md](./docs/ci-pipeline.md)).
A deterministic judge aggregates their structured verdicts and writes the merge gate; it runs from
`main` with read-only permissions, so a PR cannot modify the code that judges it. Nothing in the
pipeline can push.

File an issue and an agent opens a PR for it; `/review` forces a fresh review
cycle on a PR.

The `invariant` reviewer is the one that earns its keep here: `docs/` is normative spec, so it
checks that a change preserves author exclusion, orchestrator blindness, fail-closed lenses,
severity floors, and termination — and blocks a change that alters one of those behaviors without
updating the spec and recording the decision.

- [docs/ci-pipeline.md](./docs/ci-pipeline.md) — what runs, and which properties are load-bearing
- [docs/ci-setup.md](./docs/ci-setup.md) — runner registration, secrets, branch protection
