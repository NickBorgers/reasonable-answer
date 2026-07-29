"""Run configuration: the role-structured roster, budgets, and startup validation.

The roster is a **writer pool** plus **per-lens critic pools**, and an optional
**orchestrator** entry (D15/D16/D19). Critic-only specialists are allowed and are the
clean way to satisfy author-exclusion: a model that never writes can review every tick.
That is how the strongest model in the roster earns its keep — as a writer it would be
barred from reviewing its own drafts.

Startup validation is **fail closed** (RA-015): an empty writer pool, a lens with no
eligible non-author model, or a bad `min_ticks`/`hard_cap` pair aborts the run before
a single token is spent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import REFINE_TRANSFORMS
from .taxonomy import LENSES, Lens

#: Leading alphabetic run of a model name — 'gemma-4-31b-it' and 'gemma4' both -> 'gemma'.
_FAMILY_STEM = re.compile(r"[a-z]+")

#: Shipped inside the wheel (see pyproject force-include), so an installed package
#: has a working default even with no source tree around it.
PACKAGED_CONFIG = Path(__file__).resolve().parent / "_default_roster.yaml"

#: Searched in order. The source-tree path keeps `uv run ra ...` working from a
#: checkout; `RA_CONFIG` and /etc/ra are how a container gets its roster mounted in.
CONFIG_SEARCH_PATH: tuple[Path, ...] = (
    Path("config/roster.yaml"),
    Path("/etc/ra/roster.yaml"),
    PACKAGED_CONFIG,
)


def default_config_path() -> Path:
    """First existing candidate, honouring $RA_CONFIG. Never a path that only
    resolves inside a source checkout — that broke every containerized run."""
    override = os.environ.get("RA_CONFIG")
    if override:
        return Path(override)
    for candidate in CONFIG_SEARCH_PATH:
        if candidate.exists():
            return candidate
    return PACKAGED_CONFIG


class ConfigError(RuntimeError):
    """Raised for any fail-closed startup violation."""


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bounds are part of failing closed: a zero concurrency crashes the executor, a
    # negative stagnation limit terminates the run on tick one, and a negative budget
    # silently reads as "exhausted" — all of which change the state machine without
    # anyone saying so.
    min_ticks: int = Field(default=2, ge=1, le=100)
    hard_cap: int = Field(default=8, ge=2, le=200)
    polish_cap: int = Field(default=1, ge=0, le=20)
    critique_attempts: int = Field(default=12, ge=0, le=100)
    confirmation_attempts: int = Field(default=6, ge=0, le=100)
    stagnation_limit: int = Field(default=3, ge=1, le=100)
    cycle_period: int = Field(default=4, ge=1, le=100)
    repair_retries: int = Field(default=1, ge=0, le=10)
    # A critic's rejections are mostly quoting slips — a `claim_span` retyped instead of
    # copied — and they are correctable once the model is shown the paragraph it should
    # have quoted. Given a wider budget than the generic `repair_retries` because the
    # alternative is controller rule 2, which throws away every issue in the response
    # and spends a whole `critique_attempts` slot to ask a fresh model the same thing.
    critic_repair_retries: int = Field(default=2, ge=0, le=10)
    call_retries: int = Field(default=2, ge=0, le=10)
    # How many *distinct* writers a single draft may be asked of before the run dies.
    # Bounded by the pool: rotating past its end would re-ask the model that failed.
    writer_attempts: int = Field(default=3, ge=1, le=10)
    timeout_seconds: float = Field(default=300.0, gt=0, le=7200)
    max_concurrency: int = Field(default=3, ge=1, le=16)

    @model_validator(mode="after")
    def _check(self) -> Budgets:
        # RI-001: guarantees no generating rule can fire at or beyond the cap.
        if not (0 < self.min_ticks < self.hard_cap):
            raise ConfigError(
                f"config invariant violated: 0 < min_ticks < hard_cap "
                f"(got min_ticks={self.min_ticks}, hard_cap={self.hard_cap})"
            )
        return self


class ProxyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://llm.featherback-mermaid.ts.net/v1"
    #: Env var whose value, when set and non-empty, overrides `base_url`. Mirrors
    #: `api_key_env` (D21): a containerized deployment can inject only the proxy URL
    #: — e.g. a Docker-bridge DNS name the baked Tailscale URL cannot resolve — and
    #: leave the baked roster authoritative for models, critics, search, and budgets.
    #: Precedence: env value > roster file value > built-in default.
    base_url_env: str = "RA_PROXY_BASE_URL"
    api_key_env: str = "LITELLM_API_KEY"
    api_key_fallback: str = "fake-key"

    @model_validator(mode="after")
    def _apply_base_url_env(self) -> ProxyConfig:
        # Resolved once at load, unlike `api_key` (a property): the file value is the
        # fallback, never the effective value, so nothing downstream reads a base_url
        # the env was meant to override. Unset, empty, or whitespace-only (the shape an
        # exported-but-blank var takes in a .env/compose file) leaves the file value; a
        # URL never carries surrounding whitespace, so a set value is trimmed.
        override = os.environ.get(self.base_url_env, "").strip()
        if override:
            self.base_url = override
        return self

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env) or self.api_key_fallback


class SearchConfig(BaseModel):
    """Web search for writers (D17, amending D5 / resolving RA-011's deferral).

    Off by default: a roster with no credential must keep working exactly as before.
    When it is on, startup is fail-closed on both halves — a missing credential and a
    writer that cannot actually emit a tool call are each fatal before any tokens are
    spent, because a writer that silently does not search still produces a
    '## Sources' section and the invented citations are indistinguishable from real
    ones downstream.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    api_key_env: str = "BRAVE_SEARCH_API_KEY"
    #: local dev convenience; gitignored via *.token. Env var wins when both exist.
    token_file: str | None = "brave.token"
    max_results: int = Field(default=5, ge=1, le=20)
    #: whole-run cap. The free Brave tier is 2,000 queries/month, and an agentic loop
    #: across writers and revisions will spend that without one.
    query_budget: int = Field(default=60, ge=1, le=5000)
    #: how many times one writer call may go round the search loop before it is made
    #: to answer with what it has.
    max_tool_rounds: int = Field(default=6, ge=1, le=20)
    #: seconds between requests; the free tier is 1 req/sec.
    min_interval_seconds: float = Field(default=1.1, ge=0.0, le=60.0)

    #: Fetch the pages the report cites and hand them to the evidence critic, so
    #: `misrepresented_source` and `fabricated_citation` become checkable rather than
    #: judgements about plausibility. Independent of `enabled`: a report with real
    #: citations can be verified whether or not this system retrieved them.
    verify_sources: bool = False
    max_sources: int = Field(default=12, ge=1, le=50)
    fetch_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    #: bytes read off the wire per page
    fetch_max_bytes: int = Field(default=400_000, ge=1_000, le=10_000_000)
    #: characters of extracted text shown to the critic per page
    fetch_max_chars: int = Field(default=6_000, ge=500, le=100_000)


class PdfSourceConfig(BaseModel):
    """Reading a cited PDF, as opposed to reporting it as an unreadable content type.

    A PDF is one of the commonest shapes an academic citation takes, and until this
    existed every one of them failed — `fetch` refused the content type outright even
    though the converter had been in the tree since D24. No new host is involved: this
    re-fetches the URL the report already cited.
    """

    model_config = ConfigDict(extra="forbid")

    #: Off by default like every other retrieval affordance, and additionally because it
    #: needs the optional `ingest` extra. Enabling it without `pypdf` installed is fatal
    #: at config load, not at the first citation twenty minutes into a run.
    enabled: bool = False
    #: Deliberately far above `SearchConfig.fetch_max_bytes` (400 KB) and above
    #: `SeedConfig`'s 4 MB. A truncated PDF is not a shorter document, it is a mangled
    #: file that the parser must refuse — so the cap is a hard refusal threshold, and a
    #: scanned or figure-heavy paper routinely clears 10 MB. Peak memory is bounded by
    #: critic concurrency rather than by source count: fetches are sequential per
    #: critic, and only the extracted text is retained.
    max_bytes: int = Field(default=25_000_000, ge=100_000, le=100_000_000)
    #: Pages read before the rest is dropped. A thousand-page appendix would otherwise
    #: spend real time producing text that `fetch_max_chars` throws away anyway.
    max_pages: int = Field(default=40, ge=1, le=2_000)


class IdentifierTierConfig(BaseModel):
    """Tier 0 (D39): ask a bibliographic registry whether the cited source exists.

    The cheapest and largest win in the ladder. A paywalled journal refuses the fetch and
    is then indistinguishable from a citation nobody published — until a registry
    confirms the DOI, at which point it is a real source whose body simply could not be
    read. That answer costs one keyless GET and never needs the paywalled body.

    It is also the only tier that can *raise* a defect: an identifier every authoritative
    registry denies is `NOT_FOUND`, which D38 mints as a blocking `fabricated_citation`.
    Hence the budget and the deliberate conservatism in `resolve/`.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    #: Order matters only for which record is kept when two registries both answer; every
    #: listed provider that covers the identifier is asked, because the second answer is
    #: what makes a denial worth acting on.
    providers: list[str] = Field(default_factory=lambda: ["crossref", "openalex"])
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    #: Whole-run call cap, enforced by `search.QueryBudget`. A twelve-source report
    #: re-verified every round would otherwise scale with the round count.
    max_calls_per_run: int = Field(default=60, ge=1, le=2000)


class OpenAccessTierConfig(BaseModel):
    """Tier 1 (D39): find a free copy of the body, and read it exactly once.

    Separate switch from tier 0 because it is a materially different act: tier 0 asks a
    registry a question, tier 1 fetches a *different document* and hands its text to a
    critic. The result is marked with `body_source_url` all the way through, and it can
    never settle a dispute about the cited URL — a preprint is not the version of record.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    #: Tried in order, stopping at the first that names a copy: they answer the same
    #: question and every extra call costs budget for an answer already in hand.
    #: `core` is deliberately absent. It is the one keyed provider on this tier, and a
    #: default that includes it would turn "enable open access" into "and also supply a
    #: CORE key or fail to start". Add it explicitly, and only where the keyless four
    #: leave a gap worth closing.
    providers: list[str] = Field(
        default_factory=lambda: ["openalex", "unpaywall", "europe_pmc", "arxiv"]
    )
    core_api_key_env: str = "CORE_API_KEY"
    #: local dev convenience; gitignored via *.token. Env var wins when both exist.
    core_token_file: str | None = "core.token"
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    max_calls_per_run: int = Field(default=40, ge=1, le=2000)


class ExtractionTierConfig(BaseModel):
    """Tier 2 (D40): a rendering service reads the cited URL when this process cannot.

    The first tier that costs money, and the first that reaches a host chosen by neither
    the report nor a registry. It renders JavaScript and gets past the bot walls that
    refuse an unknown client — it does **not** get past a hard paywall, and no
    configuration here pretends otherwise.

    There is deliberately no stealth switch. A rendering provider's stealth mode rotates
    residential IPs to defeat bot detection, which is the industrial form of the browser
    impersonation `fetch.py` refuses and D39 records as doctrine. Rendering a page is not
    disguising who is asking for it, and only the first is in scope.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    #: No default. An empty name with the tier on is fatal at load rather than a silent
    #: fallback to whichever provider happens to be first in the registry — a paid call
    #: should never be made to a vendor nobody named.
    provider: str = ""
    api_key_env: str = "FIRECRAWL_API_KEY"
    #: local dev convenience; gitignored via *.token. Env var wins when both exist.
    token_file: str | None = "firecrawl.token"
    #: Rendering a page in a real browser is slower than fetching one.
    timeout_seconds: float = Field(default=45.0, gt=0, le=180)
    #: None means the structural ceiling: `search.max_sources * budgets.hard_cap`, the
    #: most distinct URLs a run could ever cite. Derived rather than guessed so that
    #: raising `hard_cap` does not silently start starving the tier. This is a guard
    #: against a fetch loop, not a spending limit — the per-run cache already means a
    #: URL is resolved once however many rounds and critics re-verify it.
    max_calls_per_run: int | None = Field(default=None, ge=1, le=5_000)


class DeliveryTierConfig(BaseModel):
    """Tier 3 (D40): licensed document delivery. A seam, with nothing behind it.

    Services like CCC RightFind and Reprints Desk Article Galaxy do lawfully deliver
    paywalled bodies, per article, under copyright-cleared single-use terms. Those terms
    are the reason no provider ships here: a system that splices a delivered document
    into a model's context has to reason about redistribution explicitly, and that is a
    licensing question rather than an engineering one.

    So the shape exists and the registry is open. `provider: ""` with the tier enabled is
    fatal, which makes this inert rather than half-built.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = ""
    api_key_env: str = ""
    token_file: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    max_calls_per_run: int | None = Field(default=None, ge=1, le=5_000)


class SourcesConfig(BaseModel):
    """Tiers tried when a plain fetch does not yield the cited document.

    Two switches per tier — this master one and the tier's own — is deliberate. An
    operator turning the resolver on for one tier must not silently acquire another
    from a roster they inherited, which matters most for the tiers that cost money.

    Independent of `search.verify_sources` only in the sense that it does nothing
    without it: no tier runs if nothing fetches in the first place.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    pdf: PdfSourceConfig = Field(default_factory=PdfSourceConfig)
    identifiers: IdentifierTierConfig = Field(default_factory=IdentifierTierConfig)
    open_access: OpenAccessTierConfig = Field(default_factory=OpenAccessTierConfig)
    extraction: ExtractionTierConfig = Field(default_factory=ExtractionTierConfig)
    delivery: DeliveryTierConfig = Field(default_factory=DeliveryTierConfig)
    #: Env var naming the address Crossref and Unpaywall want for their polite pool. Its
    #: absence is a WARNING, never fatal, and the difference from a missing credential is
    #: the point: an anonymous request still works, it is only served from a busier
    #: rate-limit pool. Compare `SearchConfig.api_key_env`, where absence means the
    #: feature cannot function and startup fails closed. Unpaywall is the one exception
    #: — it refuses anonymous requests outright — and is skipped with its own warning
    #: rather than dragging the whole tier down.
    contact_email_env: str = "RA_CONTACT_EMAIL"

    @property
    def contact_email(self) -> str:
        """Resolved at read time, like `ProxyConfig.api_key`: the value belongs to the
        environment, never to the roster file, so a checked-in config cannot carry
        somebody's address into a container image."""
        return os.environ.get(self.contact_email_env, "").strip()

    @model_validator(mode="after")
    def _delivery_fails_closed(self) -> SourcesConfig:
        # D40: `delivery` ships as a seam with no provider behind it, so enabling it
        # without naming one can never make a call — and unlike `extraction`, there is not
        # even a registry entry to fall back to by mistake. That is what makes it inert
        # rather than half-built: the config refuses to boot instead of silently ignoring
        # the tier. Gated on the master switch like every other tier (see
        # `graph._enabled_tiers`): the whole subsystem does nothing with it off, so a
        # delivery stanza sitting in a roster with `sources.enabled: false` is not yet a
        # claim to enforce.
        if self.enabled and self.delivery.enabled and not self.delivery.provider:
            raise ConfigError(
                "fail closed: sources.delivery.enabled is on but no provider is named. "
                "The delivery tier ships as a seam with no provider behind it (D40), so "
                "enabling it without one makes no call — inert rather than half-built."
            )
        return self


class SeedConfig(BaseModel):
    """Bounds on ingesting a seed report the user supplied.

    Deliberately separate from `SearchConfig.fetch_*`: those bound twelve cited pages
    *per round*, where a seed is one document that is allowed to be report-sized. No
    character cap lives here — `Config.max_report_chars` already owns that, and it is
    enforced on the converted markdown, which is the text that reaches a model.
    """

    model_config = ConfigDict(extra="forbid")

    #: Allow `--seed <url>` and the web seed-URL field. Turning this off removes the
    #: field from the form as well as rejecting the parameter.
    #:
    #: Off by default, like `search.enabled` and `search.verify_sources` (D17/D18):
    #: a URL seed makes the server fetch a caller-chosen URL and hand the body back
    #: as the run's first report — a read proxy into whatever the host can reach, for
    #: anyone who can submit. Authentication (D32) narrows *who* that is; it does not
    #: shrink what the host can reach, and the people invited in are not the threat
    #: model this guards against. The network-layer egress boundary that makes it
    #: acceptable is still a deployment concern outside this repo
    #: (docs/ssrf-egress-isolation.md) — enable this only behind one.
    allow_url: bool = False
    fetch_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    #: A real PDF runs to megabytes; the citation fetcher's 400 KB would truncate most.
    fetch_max_bytes: int = Field(default=4_000_000, ge=10_000, le=50_000_000)
    #: Uncompressed size cap on a .docx archive, checked before anything is read, so a
    #: zip bomb arriving from an arbitrary URL cannot be expanded.
    docx_max_uncompressed_bytes: int = Field(default=50_000_000, ge=100_000)


class Roster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    writers: list[str] = Field(min_length=1)
    critics: dict[str, list[str]]
    #: The blind orchestrator's model. Its whole job is bounded ints in, one boolean
    #: out (schemas.OrchestratorView), so it needs neither reach nor a writer's
    #: capability. Defaults to writers[0] only because that alias is guaranteed to
    #: exist and to have been probed.
    orchestrator: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Roster:
        missing = {lens.value for lens in LENSES} - set(self.critics)
        if missing:
            raise ConfigError(f"roster is missing critic pools for lenses: {sorted(missing)}")
        extra = set(self.critics) - {lens.value for lens in LENSES}
        if extra:
            raise ConfigError(f"roster declares unknown lenses: {sorted(extra)}")
        for lens, pool in self.critics.items():
            if not pool:
                raise ConfigError(f"critic pool for lens '{lens}' is empty")
            if len(set(pool)) != len(pool):
                raise ConfigError(f"critic pool for lens '{lens}' has duplicate aliases")
        if len(set(self.writers)) != len(self.writers):
            raise ConfigError("writer pool has duplicate aliases")
        return self

    def critics_for(self, lens: Lens) -> list[str]:
        return list(self.critics[lens.value])

    @property
    def orchestrator_alias(self) -> str:
        """The explicit entry if set, else writers[0]. Total: writers has min_length=1."""
        return self.orchestrator or self.writers[0]

    @property
    def all_aliases(self) -> list[str]:
        # The orchestrator belongs here even when it is just writers[0]: `all_aliases`
        # is what startup resolves identities for and probes for structured output
        # (graph.build_runtime). An alias missing from it would skip both — the
        # identity guard would silently degrade to accepting the bare alias, and a
        # structured-output failure would surface mid-run instead of at startup.
        pools = (a for pool in self.critics.values() for a in pool)
        seen: list[str] = []
        for alias in [*self.writers, *pools, self.orchestrator_alias]:
            if alias not in seen:
                seen.append(alias)
        return seen


class AuditionThresholds(BaseModel):
    """Where `fit` / `marginal` / `unfit` fall. Tunable because the right line depends
    on the corpus and on how much a deployment is willing to spend on false alarms.

    No threshold can rescue a model that finds zero obvious defects; that case is
    hardcoded in `audition.judge`. Everything here is a judgement call about degrees.
    """

    model_config = ConfigDict(extra="forbid")

    #: Fail closed below this, measured on `tier: obvious` fixtures only.
    min_obvious_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    warn_lens_sensitivity: float = Field(default=0.6, ge=0.0, le=1.0)
    #: Mean material issues invented per sound control report. Above this a critic
    #: cannot be reasoned with: every round it manufactures work the next writer must
    #: "fix", and the run stagnates instead of converging.
    max_control_material_rate: float = Field(default=1.0, ge=0.0)
    warn_control_material_rate: float = Field(default=0.34, ge=0.0)
    max_schema_failure_rate: float = Field(default=0.2, ge=0.0, le=1.0)


class RefineAuditionThresholds(BaseModel):
    """Where the refine audition's verdicts fall (docs/question-refinement.md, D33).

    The asymmetry relative to `AuditionThresholds` is deliberate: for a critic,
    silence is the failure being measured; for refinement, silence is the designed
    default (D26), so a low fire rate only warns while a *violation* — a suggestion
    that narrows scope, fires a disallowed transform, or drops the subject — gates.
    """

    model_config = ConfigDict(extra="forbid")

    #: Violation rate tolerated on `tier: obvious` fixtures. Zero: an obvious fixture
    #: is the pinned regression class (the fluoride down-scoping), silence is always
    #: a safe out, and a model that narrows even once in the sample is doing the one
    #: thing the guardrails exist to prevent.
    max_obvious_violation_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    warn_violation_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    #: Mean suggestions offered per control (well-posed) question. Above this the
    #: model manufactures chips for questions that needed none — the noise direction.
    max_control_suggestion_rate: float = Field(default=0.5, ge=0.0)
    warn_control_suggestion_rate: float = Field(default=0.2, ge=0.0)
    #: Below this share of expected-transform fixtures actually drawing their
    #: transform, the feature is mostly dormant — degraded, not dangerous, so warn.
    warn_fire_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    max_schema_failure_rate: float = Field(default=0.2, ge=0.0, le=1.0)


class RefineAuditionConfig(BaseModel):
    """Fixture audition for the refine prompt surface (D33). Same doctrine as
    `AuditionConfig`: no `enabled` flag — measuring costs proxy calls, only the
    explicit `ra audition-refine` command spends them, and `ra doctor` only reads
    the cache left behind."""

    model_config = ConfigDict(extra="forbid")

    #: Separate file from the critic audition cache: the entries have a different
    #: shape and a different key scheme, and a shared file would let one command's
    #: rewrite clobber the other's verdicts.
    cache_path: Path = Path(".ra-refine-audition.json")
    max_age_days: int = Field(default=30, ge=1, le=365)
    #: Higher than the critic default: refine calls are short and cheap, and the
    #: silence-vs-fire outcome is stochastic enough that small samples mislead.
    repetitions: int = Field(default=5, ge=1, le=20)
    max_concurrency: int = Field(default=3, ge=1, le=16)
    thresholds: RefineAuditionThresholds = Field(default_factory=RefineAuditionThresholds)


class AuditionConfig(BaseModel):
    """Auditioning is opt-in by being a separate command, not by a flag.

    There is deliberately no `enabled` here. An audition costs |models| x |fixtures| x
    repetitions calls against a paid proxy, so it must never happen implicitly — and
    nothing implicit invokes it: `ra audition` is the only thing that measures, while
    `ra doctor` and the `enforce` gate below only *read* the cache it leaves behind.
    A flag would have gated nothing, and a config knob that cannot change behaviour
    reads as a safety control while being inert.
    """

    model_config = ConfigDict(extra="forbid")

    #: Warn-by-default rather than fail-closed. The guarantee is genuinely void
    #: without capable reviewers, so the fail-closed instinct is right in principle —
    #: but coupling every run to a cache whose freshness depends on a rate-limited
    #: proxy means an operator blocked by an expired audition disables the harness
    #: outright, which is strictly worse than a loud warning. Opt in deliberately.
    #: When on, `graph.build_runtime` refuses to start if an assigned critic graded
    #: `unfit` (`audition.enforce_fitness`). Only `unfit` blocks: `marginal`, `stale`
    #: and `not audited` stay warnings even here, because they are absences of
    #: evidence rather than evidence of incapacity.
    enforce: bool = False
    cache_path: Path = Path(".ra-audition.json")
    max_age_days: int = Field(default=30, ge=1, le=365)
    #: Critics are non-deterministic; docs/decisions.md already records minimax-m3
    #: probing differently across `ra doctor` runs. Single-shot auditioning would
    #: inherit exactly that flakiness and call it a capability measurement.
    repetitions: int = Field(default=3, ge=1, le=20)
    max_concurrency: int = Field(default=3, ge=1, le=16)
    thresholds: AuditionThresholds = Field(default_factory=AuditionThresholds)
    #: The refine prompt surface's own audition (D33) — separate corpus, separate
    #: cache, separate command (`ra audition-refine`).
    refine: RefineAuditionConfig = Field(default_factory=RefineAuditionConfig)


class DisputeConfig(BaseModel):
    """The writer dispute channel (D25). Off by default: with `enabled: false`
    every prompt and every state transition is byte-identical to a build without
    the feature — the D17 offline-when-off pattern."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    #: Whole-run adjudication budget. A dispute raised past it is dismissed
    #: unadjudicated (the defect stands) — the budget bounds spend, not termination.
    budget: int = Field(default=6, ge=0, le=50)
    #: Disputes accepted from a single revision pass; the rest are dropped.
    max_per_pass: int = Field(default=3, ge=1, le=10)
    arbiter_max_tokens: int = Field(default=4000, ge=500, le=16000)


class AuthConfig(BaseModel):
    """Who the web layer believes is asking.

    Identity arrives in a header, either from Cloudflare Access
    (`Cf-Access-Authenticated-User-Email`) or from `tailscale serve`. Neither is
    verified cryptographically, so both are only trustworthy when the app's port is
    unreachable except through the fronting proxy — see docs/authentication.md.

    There is exactly one knob, and its unset state is the safe one: with no
    `dev_identity`, a request carrying neither header is refused. A boolean
    `require_auth` beside a `dev_identity` string would have two settings that can
    disagree, and the disagreeing combination is the one that fails open.
    """

    model_config = ConfigDict(extra="forbid")

    #: Identity granted to requests that carry no identity header at all. Set it for
    #: local development (`make serve`, `$RA_DEV_IDENTITY`); leave it unset anywhere
    #: the app is reachable by anyone else, where it would be a public login.
    dev_identity: str | None = None


#: All transforms except the ideologically riskiest one (docs/question-refinement.md
#: "the reframe taxonomy"). Computed once from the schema's canonical tuple so the two
#: never drift apart.
_DEFAULT_REFINE_TRANSFORMS = frozenset(REFINE_TRANSFORMS) - {"question_behind_the_question"}


class RefineConfig(BaseModel):
    """Pre-run reframing suggestions (D26, docs/question-refinement.md). Off by
    default: with `enabled: false` the web edge behaves byte-identically to a build
    without the feature, matching the D17/D25 opt-in pattern.

    Deliberately **excluded** from `graph._run_fingerprint`: refinement lives entirely
    at the web edge and never reaches the graph (the fingerprint hashes `question`,
    `seed`, `roster`, and `budgets` only — see graph.py), so a config change here
    cannot invalidate a resumed run. `question.txt` always holds exactly the text that
    ran, whether or not it was ever offered a suggestion.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    #: Empty means "use `roster.orchestrator_alias`" — see `effective_alias`. A
    #: dedicated alias lets a deployment route refinement at a different backend/
    #: resource pool than run traffic (see the design's isolation accounting).
    alias: str = ""
    max_suggestions: int = Field(default=3, ge=1, le=3)
    #: Provider-level request timeout for the refine call specifically — NOT
    #: `budgets.timeout_seconds`, which is the roster-wide default baked into the
    #: shared `OpenAI` client. See `LLMClient`'s per-call `timeout` kwarg.
    timeout_seconds: float = Field(default=5.0, ge=0.5, le=15)
    cache_entries: int = Field(default=256, ge=16, le=4096)
    cache_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    #: Refinement-specific rate limit, separate from `submit_rate_max` — a person
    #: pausing to type triggers many more refine calls than run submissions.
    rate_max: int = Field(default=10, ge=1, le=100)
    rate_window_seconds: int = Field(default=60, ge=1, le=3600)
    concurrency: int = Field(default=2, ge=1, le=4)
    offer_entries: int = Field(default=512, ge=64, le=8192)
    offer_ttl_seconds: int = Field(default=1800, ge=300, le=7200)
    #: Best-effort damper on orphaned semaphore permits after a timed-out call — not
    #: a guarantee (see `web/refine.py`'s docstring on the honesty of this layering).
    orphan_linger_seconds: int = Field(default=30, ge=0, le=300)
    #: Which of the six taxonomy transforms the model is even told about.
    #: `question_behind_the_question` is excluded by default — see the module-level
    #: docstring above and docs/question-refinement.md.
    enabled_transforms: set[str] = Field(default_factory=lambda: set(_DEFAULT_REFINE_TRANSFORMS))

    @model_validator(mode="after")
    def _check_transforms(self) -> RefineConfig:
        unknown = self.enabled_transforms - set(REFINE_TRANSFORMS)
        if unknown:
            raise ConfigError(f"refine.enabled_transforms names unknown transforms: {sorted(unknown)}")
        return self

    def effective_alias(self, roster: Roster) -> str:
        """The alias the refine call actually uses: the explicit override, else the
        orchestrator's — refinement wants an orchestrator-class model (bounded,
        cheap judgment), not writer-class reach."""
        return self.alias or roster.orchestrator_alias


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    roster: Roster
    budgets: Budgets = Field(default_factory=Budgets)
    search: SearchConfig = Field(default_factory=SearchConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    audition: AuditionConfig = Field(default_factory=AuditionConfig)
    seed: SeedConfig = Field(default_factory=SeedConfig)
    disputes: DisputeConfig = Field(default_factory=DisputeConfig)
    refine: RefineConfig = Field(default_factory=RefineConfig)
    runs_dir: Path = Path("runs")
    retention_days: int = 14
    #: How often the web server's background sweep content-purges runs past
    #: `retention_days`, so reclaiming disk does not depend on someone running
    #: `ra purge` by hand. The sweep drops reports/critiques only (the bulk), keeping
    #: the decision record longer, exactly like `purge --content-only`. Set to 0 to
    #: turn the automatic sweep off and go back to a manual/cron `purge`.
    retention_sweep_interval_seconds: float = 3600.0
    #: How many times in a row the process may auto-resume a run on startup without it
    #: making progress. Bounds a run that fails deterministically: without a cap, every
    #: restart would pick it up, fail the same way, and restart again forever.
    max_resume_attempts: int = 3
    #: Backpressure on submission (RC-007). Concurrency already bounds token *spend*,
    #: but not how many runs may pile up waiting, nor the run directories each one
    #: writes on the way in. A cap on the queue's waiting depth turns a burst into
    #: HTTP 429s instead of unbounded memory and disk. Set to 0 to leave it unbounded.
    max_queue_depth: int = 32
    #: Submissions allowed per authenticated identity inside
    #: `submit_rate_window_seconds`. Set to 0 to disable the limit. Every submission
    #: now carries an identity — an unauthenticated request never reaches the queue —
    #: so there is no shared fallback bucket left to spill into.
    submit_rate_max: int = 20
    submit_rate_window_seconds: float = 60.0
    max_report_chars: int = 60_000
    max_question_chars: int = 4_000
    #: anchor every critic quote to the paragraph it cites, closing the last
    #: free-text channel from critic to writer
    require_verbatim_spans: bool = True

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        p = Path(path) if path else default_config_path()
        if not p.exists():
            raise ConfigError(
                f"config file not found: {p}\n"
                f"searched: $RA_CONFIG, {', '.join(str(c) for c in CONFIG_SEARCH_PATH)}"
            )
        data = yaml.safe_load(p.read_text()) or {}
        return cls.model_validate(data)


def validate_roster_health(config: Config, identities: dict[str, str]) -> list[str]:
    """Fail-closed structural checks + soft warnings. Returns the warning list.

    `identities` maps alias -> resolved provider/model/version string (RA-017).
    Distinctness is enforced at the *resolved* level, not the alias level: two
    aliases pointing at the same underlying model do not count as two reviewers.
    """
    roster = config.roster
    if not roster.writers:
        raise ConfigError("fail closed: writer pool is empty")

    warnings: list[str] = []

    unresolved = [a for a in roster.all_aliases if a not in identities]
    if unresolved:
        raise ConfigError(f"fail closed: could not resolve identities for {unresolved}")

    # A lens must always have at least one model that can review *any* writer's
    # output; otherwise some tick would have zero eligible non-author critics.
    for lens in LENSES:
        pool_ids = {identities[a] for a in roster.critics_for(lens)}
        for writer in roster.writers:
            eligible = pool_ids - {identities[writer]}
            if not eligible:
                raise ConfigError(
                    f"fail closed: lens '{lens.value}' has no eligible non-author critic "
                    f"when '{writer}' is the author"
                )
            if len(eligible) < 2:
                warnings.append(
                    f"lens '{lens.value}' is roster_limited when '{writer}' authors "
                    f"(only {len(eligible)} eligible model) — acceptance will degrade to "
                    f"converged_unconfirmed"
                )
        if len(pool_ids) < len(roster.critics_for(lens)):
            warnings.append(
                f"lens '{lens.value}' has aliases resolving to the same underlying model; "
                f"they do not count as distinct reviewers"
            )
        families = {_family(identities[a]) for a in roster.critics_for(lens)}
        if len(families) < 2:
            warnings.append(
                f"lens '{lens.value}' critic pool shares one model family {sorted(families)} — "
                f"weak independence (correlated blind spots)"
            )

    # Dispute arbiters (D25) must be neither the disputing writer nor the critic
    # that raised the finding. Fail OPEN with a warning, not closed: a dispute with
    # no eligible arbiter is dismissed at runtime and the defect stands — the status
    # quo ante — so an uncoverable pair costs a privilege, never a safety property.
    if config.disputes.enabled:
        all_ids = set(identities.values())
        for lens in LENSES:
            for writer in roster.writers:
                for critic in roster.critics_for(lens):
                    pair = {identities[writer], identities[critic]}
                    if not (all_ids - pair):
                        warnings.append(
                            f"disputes: no arbiter identity exists when '{writer}' disputes a "
                            f"'{lens.value}' finding raised by '{critic}' — such disputes will "
                            f"be dismissed unadjudicated"
                        )
    return warnings


def _family(identity: str) -> str:
    """Coarse model-family key taken from the model *name*, ignoring the provider or
    serving-backend prefix: 'openrouter/google/gemma-4-31b-it' and
    'ollama_chat/gemma4:26b-a4b-it-q8_0' are both 'gemma'.

    Keying on the prefix instead would read two namespaces at once — the org for a
    three-segment identity, the backend for a two-segment one — and so call those two
    Gemma checkpoints different families.
    """
    stem = identity.split("/")[-1].split(":")[0].lower()
    match = _FAMILY_STEM.match(stem)
    return match.group(0) if match else stem
