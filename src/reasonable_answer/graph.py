"""The LangGraph loop: intake → generate ⇄ critique → triage → orchestrate → control.

LangGraph state is *shared* across nodes, so isolation here is deliberate rather
than automatic (docs/isolation.md). Two things make it structural:

* every model call is built from an explicit, minimal argument list — nodes never
  hand a model the state object;
* the orchestrator node is invoked through :func:`_orchestrate` which accepts an
  ``OrchestratorView`` and nothing else, so artifact-bearing state has no path in.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import audition as audition_mod
from . import critique as critique_mod
from . import dispute as dispute_mod
from . import fetch, prompts, reading, resolve, roles, search, support, triage
from . import report as report_mod
from .build import build_identity
from .config import Config, ConfigError, Roster, validate_roster_health
from .controller import acceptance_state, decide, detect_cycle
from .llm import LLMClient, MalformedOutputError, ModelCallError, ProbeIncomplete
from .schemas import (
    AdjudicationRecord,
    CleanRecord,
    ControllerInput,
    Decision,
    Defect,
    Dispute,
    LensResult,
    OrchestratorRecommendation,
    OrchestratorView,
    SeverityCounts,
    SupportManifest,
    WriterDisputes,
)
from .store import RunStore
from .taxonomy import LENSES, Lens

log = logging.getLogger(__name__)


class State(TypedDict, total=False):
    run_id: str
    question: str
    seed: str | None
    #: What ingest made of the seed at the edge — the format it came from, where it
    #: came from, and anything it wants the user told. Provenance only: no node routes
    #: on these, and they are outside the resume fingerprint (see `_run_fingerprint`).
    seed_format: str | None
    seed_source: str | None
    seed_warnings: list[str]
    fingerprint: str
    #: Captured once at intake so every prompt in the run carries the same date
    #: (RB-010: confirmation critiques stay byte-identical across midnight).
    run_date: str

    report: str
    artifact_hash: str
    author_alias: str
    author_identity: str
    writer_rotation: int

    round: int
    hash_history: list[str]

    pending_lenses: list[str]
    #: Set by the controller alongside `pending_lenses` whenever it routes to
    #: `_critique`: True only for a rule-8 confirmation top-up, False for a rule-2
    #: discovery re-critique or a fresh draft's first pass. Consumed by `_critique` to
    #: stamp `LensResult.confirm_state` *after* the model call returns — the prompt
    #: built from `pending_lenses` never reads this flag, so a confirming critic sees
    #: the identical interface a discovering one does (RB-010).
    confirming: bool
    #: lens -> every review of the CURRENT artifact for that lens, in the order they
    #: completed. A list rather than a single entry because review depth runs several
    #: independent critics per lens per pass (D-front-loaded-depth); a failed review stays on
    #: the list and is skipped by every consumer, so it can neither add an issue nor
    #: mint a clean record. Reset on every generation, like the other per-artifact
    #: accumulators below.
    lens_results: dict[str, list[dict]]
    used_critics: dict[str, list[str]]
    #: Per-lens count of critique attempts made on THIS artifact. `used_critics` is a
    #: set of distinct identities and so stops growing once the eligible pool is
    #: exhausted; this counter keeps climbing, so `pick_critic`'s rotation advances on
    #: every rule-2 retry instead of freezing on one fallback model (mirrors the
    #: `writer_rotation` idiom).
    critique_rounds: dict[str, int]
    clean_records: list[dict]
    defects: list[dict]
    #: Observed source-verification coverage, keyed by artifact hash — what the evidence
    #: lens could actually reach in *that* draft's bibliography (D-observed-source-coverage).
    #: Keyed rather than latest-wins because `_finalize` may ship an earlier round.
    source_coverage: dict[str, dict]

    # Dispute channel (D-writer-disputes). The registry lives here — checkpointed state — so it
    # survives resume, and a content purge cannot break a live run.
    pending_disputes: list[dict]
    adjudications: list[dict]
    dispute_budget_remaining: int
    defect_provenance: dict[str, list[str]]

    view: dict
    decision: dict
    polish_next: bool
    polish_used: int
    #: Rule 13's escape valve (D-scoped-revision), shaped exactly like the polish pair
    #: above: a one-shot flag the generate node consumes, and a whole-run counter the
    #: controller reads back.
    full_rewrite_next: bool
    rewrites_used: int
    critique_attempts_remaining: int
    confirmation_attempts_remaining: int

    prev_material: int
    prev_signature: list
    stagnation_count: int

    fatal: bool
    fatal_reason: str | None
    terminal_status: str | None
    scoreboard: list[dict]
    warnings: list[str]


@dataclass
class Runtime:
    """Everything the nodes need that is not run state."""

    config: Config
    client: LLMClient
    identities: dict[str, str]
    store: RunStore
    warnings: list[str] = field(default_factory=list)
    #: None when search is disabled; writers then run exactly as they did before.
    searcher: Any | None = None
    #: None when source verification is off; the evidence lens then judges citations
    #: on their face, exactly as it did before. Set **only** for verification — a
    #: deployment that turned on writer source reads alone must not silently acquire
    #: fetched pages in a critic's context or mechanical dispute adjudication, so the
    #: reader below holds its own reference to the shared fetcher rather than reusing
    #: this field as a "something fetches" flag.
    fetcher: Any | None = None
    #: None when writers may not read sources (D-writer-source-reads); they then work
    #: from search snippets exactly as they did before.
    reader: Any | None = None

    @property
    def search_enabled(self) -> bool:
        return self.searcher is not None

    @property
    def verify_sources(self) -> bool:
        return self.fetcher is not None

    @property
    def read_sources(self) -> bool:
        return self.reader is not None

    @property
    def support_manifest(self) -> bool:
        return self.read_sources and self.config.search.support_manifest

    @property
    def disputes_enabled(self) -> bool:
        return self.config.disputes.enabled


class StartupRefused(ConfigError):
    """Startup validation refused, before anything about the run itself was read.

    The distinction that earns this its own type is structural, not a taxonomy of
    causes: nothing `build_runtime` does looks at the question or the seed, so whatever
    it refuses, it refuses identically for *every* queued run — an unreachable proxy, a
    roster no longer viable once unreachable aliases are dropped, a missing credential,
    a critic graded unfit. That makes it a fact about the deployment, which is what lets
    a worker defer the run rather than spend one of its resume attempts on it
    (D-deferred-not-abandoned).

    Intake's own `ConfigError`s — a question over `max_question_chars`, a seed over
    `max_report_chars`, a missing question — deliberately stay outside this type. They
    depend on the run's inputs, they will fail the same way on every retry, and the
    resume cap is exactly the right thing to spend on them.

    `code` is a closed, content-free token from `REFUSAL_CODES`, carried separately from
    the message because the two have different audiences and different exposure. The
    message is for the container log, which is reachable only by someone who can already
    read the deployment; it names the proxy URL, the provider's own wording, and which
    aliases failed. The code is what may be persisted to `events.jsonl`, which
    `/runs/<run_id>/audit.json` serves to anyone holding a run id (D-id-as-credential) —
    so it must say what happened without saying anything about the deployment it
    happened to.
    """

    def __init__(self, message: str, code: str = "startup_refused") -> None:
        super().__init__(message)
        self.code = code


#: Every value `StartupRefused.code` may take. Closed on purpose: a reader of the audit
#: trail can enumerate the possibilities, and no provider- or deployment-authored string
#: can reach it by accident.
REFUSAL_CODES = ("startup_refused", "roster_unreachable")


def build_runtime(
    config: Config, run_id: str | None = None, client: LLMClient | None = None
) -> Runtime:
    """Startup validation, fail closed before a single token is spent (RA-015).

    Every fail-closed refusal in here is re-raised as `StartupRefused`, which is a
    `ConfigError`, so callers that only ever caught the base type are unaffected —
    including `search.SearchConfigError`, which is not one but is raised from here for
    the same reason, and which `ra run` consequently now reports fail-closed instead of
    letting escape as a traceback.
    """
    try:
        return _build_runtime(config, run_id, client)
    except StartupRefused:
        # Already classified — re-wrapping would flatten its code back to the default.
        raise
    except (ConfigError, search.SearchConfigError) as exc:
        # `SearchConfigError` is not a `ConfigError` but is raised from the same place
        # for the same reason — a missing Brave credential is deployment state, refused
        # identically for every queued run — so it belongs on this side of the line too.
        raise StartupRefused(str(exc)) from exc


def _build_runtime(
    config: Config, run_id: str | None, client: LLMClient | None
) -> Runtime:
    client = client or LLMClient(config)
    identities = client.resolve_identities(config.roster.all_aliases)
    warnings = validate_roster_health(config, identities)
    # Structural eligibility says a lens *has* a reviewer; this says the reviewer can
    # actually find a defect (D-critic-audition). Cache-read only, so it costs nothing and stays put
    # ahead of the probes below — a roster with an unfit critic should not get as far
    # as spending tokens on structured-output detection.
    audition_mod.enforce_fitness(
        config.audition, config.roster, identities, config.require_verbatim_spans
    )

    unreachable: dict[str, str] = {}
    for alias in config.roster.all_aliases:
        try:
            mode = client.probe_structured_output(alias)
        except ProbeIncomplete as exc:
            # An availability failure, not a capability finding (D-probe-capability-evidence).
            # Collected rather than raised so the whole roster is probed before anything is
            # decided: which aliases are reachable is what says whether the run can go on,
            # and giving up on the first one cannot know that.
            unreachable[alias] = str(exc)
            continue
        log.info("structured-output mode for %s (%s): %s", alias, identities[alias], mode)
    if unreachable:
        config, warnings = _degrade_roster(config, identities, unreachable, warnings)

    searcher = _build_searcher(config, client)
    read_pdfs = _pdf_reading_enabled(config)
    resolver = _build_resolver(config, warnings)
    # One fetcher for both consumers, so the run-lifetime cache is shared: a page a
    # writer read through `read_source` is not downloaded again when the evidence lens
    # verifies the citation, and the two see the same bytes (D-writer-source-reads).
    # Which consumers actually get it is decided below, separately, because the two
    # switches are independent and each is opt-in on its own.
    source_fetcher = (
        fetch.SourceFetcher(
            timeout=config.search.fetch_timeout_seconds,
            max_bytes=config.search.fetch_max_bytes,
            max_chars=_cache_max_chars(config),
            read_pdfs=read_pdfs,
            pdf_max_bytes=config.sources.pdf.max_bytes,
            pdf_max_pages=config.sources.pdf.max_pages,
            resolver=resolver,
        )
        if (config.search.verify_sources or config.search.read_sources)
        else None
    )
    # Verification sees `fetch_max_chars` and nothing more, whatever the cache holds.
    # Without the cap travelling with the handle, raising `read_max_chars` would widen
    # both the evidence lens's page text and `dispute.adjudicate_mechanical`'s
    # containment window — and a dispute upheld there suppresses a defect, so
    # `search.read_sources` would have a path into the stop decision it must not have.
    fetcher = (
        fetch.CappedFetcher(source_fetcher, max_chars=config.search.fetch_max_chars)
        if config.search.verify_sources and source_fetcher is not None
        else None
    )
    reader = _build_reader(config, source_fetcher)

    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    store = RunStore(config.runs_dir, run_id)
    store.event(
        "startup",
        identities=identities,
        modes={a: client.mode_for(a) for a in config.roster.all_aliases},
        warnings=warnings,
        budgets=config.budgets.model_dump(),
        search_enabled=searcher is not None,
        search_query_budget=config.search.query_budget if searcher else 0,
        verify_sources=config.search.verify_sources,
        read_sources=reader is not None,
        read_budget=config.search.read_budget if reader else 0,
        support_manifest=reader is not None and config.search.support_manifest,
        read_pdfs=read_pdfs,
        resolve_tiers=sorted(_enabled_tiers(config)),
        audition_enforced=config.audition.enforce,
        # Which aliases this attempt could not probe, and so ran without
        # (D-degraded-roster). Empty on a healthy start. Recorded per attempt because it
        # is a fact about the moment, not about the run: the same run resumed an hour
        # later may well have the full roster back, and the audit trail has to be able
        # to say which rounds were reviewed by whom.
        unreachable_aliases=sorted(unreachable),
        # Per attempt, not per run: a resumed run can cross a deploy, and the set of
        # startup events is the only place that shows it (docs/run-provenance.md).
        build=build_identity().as_dict(),
    )
    for warning in warnings:
        log.warning("roster: %s", warning)
    return Runtime(config=config, client=client, identities=identities, store=store,
                   warnings=warnings, searcher=searcher, fetcher=fetcher, reader=reader)


def _degrade_roster(
    config: Config,
    identities: dict[str, str],
    unreachable: dict[str, str],
    warnings: list[str],
) -> tuple[Config, list[str]]:
    """Run without the aliases that could not be probed — if what remains is a roster
    that still satisfies every structural rule (D-degraded-roster).

    An unprobeable alias is a fact about the moment, not about the model
    (D-probe-capability-evidence). Treating the *whole* roster as a precondition made
    every alias a single point of failure for the run: on 2026-08-15 one writer behind
    an overloaded provider aborted three runs before a token was spent, twice, while
    the other five aliases answered normally. That is the correct outcome when the
    survivors cannot staff the game and the wrong one when they can — and
    `validate_roster_health` is already the function that knows the difference, so this
    asks it rather than inventing a second, weaker notion of "enough roster".

    Silence is not what makes this safe; the verdict is. `LensStatus.roster_limited` is
    computed per round from the critics that actually turn out to be eligible, so a lens
    thinned to one reaches `weak_met` and the run terminates `converged_unconfirmed`
    rather than `accepted`. A degraded run therefore cannot claim a strength it did not
    earn even if nobody reads the warnings below.
    """
    dropped = set(unreachable)
    named = ", ".join(sorted(dropped))
    for alias, reason in sorted(unreachable.items()):
        log.warning("alias %s could not be probed on this attempt: %s", alias, reason)

    try:
        degraded = config.model_copy(update={"roster": _roster_without(config.roster, dropped)})
        # The reduced roster's warnings, not the configured roster's: the pools this run
        # will actually draw from are the ones a reader needs named.
        reduced_warnings = validate_roster_health(degraded, identities)
    except ConfigError as exc:
        raise StartupRefused(
            f"fail closed: could not probe {named}, and the roster left without "
            f"{'them' if len(dropped) > 1 else 'it'} cannot staff a run — {exc}",
            code="roster_unreachable",
        ) from exc

    note = (
        f"degraded roster: {named} could not be probed on this attempt and the run "
        f"proceeds without {'them' if len(dropped) > 1 else 'it'}; any lens this leaves "
        f"with fewer than two eligible critics can reach only converged_unconfirmed"
    )
    return degraded, [note, *reduced_warnings]


def _roster_without(roster: Roster, dropped: set[str]) -> Roster:
    """The roster minus `dropped`, or a `ConfigError` naming what removing them emptied.

    The two structural emptinesses are checked here rather than left to `Roster`'s own
    validators only so the failure reads as "this outage cost you the logic lens"
    instead of a field-constraint error about a list length.
    """
    writers = [a for a in roster.writers if a not in dropped]
    if not writers:
        raise ConfigError("no writer is reachable")
    critics = {
        lens: [a for a in pool if a not in dropped] for lens, pool in roster.critics.items()
    }
    if empty := sorted(lens for lens, pool in critics.items() if not pool):
        raise ConfigError(f"no critic is reachable for {', '.join(empty)}")
    # An unreachable orchestrator costs a polish pass, never the run: its whole job is
    # bounded ints in and one boolean out, so any probed alias can do it, and clearing
    # the field selects the documented default of `writers[0]`
    # (D-orchestrator-roster-entry). Keeping the unreachable alias instead would leave
    # `_orchestrate_call` swallowing a failure every round and disable rule 9 silently
    # for the whole run — precisely the permanent, invisible loss that decision exists
    # to prevent.
    orchestrator = None if roster.orchestrator in dropped else roster.orchestrator
    return Roster(writers=writers, critics=critics, orchestrator=orchestrator)


def _build_reader(config: Config, fetcher) -> reading.SourceReader | None:
    """Construct the writer-facing source reader, or return None when it is off.

    `SearchConfig` has already refused the incoherent combinations at load time
    (reading without search, a manifest without reading), so there is nothing to fail
    closed on here — this is composition, in the manner of `_build_searcher` and
    `_build_resolver`, keeping network I/O out of the nodes and the suite offline.
    """
    if not config.search.read_sources or fetcher is None:
        return None
    log.info(
        "writer source reads enabled: %s reads and %d characters for this run",
        "unbounded" if config.search.read_budget is None else config.search.read_budget,
        config.search.read_char_budget,
    )
    return reading.SourceReader(
        fetcher,
        budget=reading.ReadBudget(
            max_calls=config.search.read_budget,
            max_chars=config.search.read_char_budget,
        ),
        max_chars=config.search.read_max_chars,
    )


def _pdf_reading_enabled(config: Config) -> bool:
    """Both switches on, and `pypdf` actually importable. Fatal if not.

    Checked here rather than at the first cited PDF, for the same reason
    `_build_searcher` checks a credential at startup: a dependency failure that
    surfaces mid-run costs a run's worth of tokens to discover, and arrives disguised
    as a per-source `unreadable` that looks like the site's fault rather than ours.
    """
    if not (config.sources.enabled and config.sources.pdf.enabled):
        return False
    try:
        import pypdf  # noqa: F401
    except ImportError as exc:
        raise ConfigError(
            "sources.pdf.enabled is on but pypdf is not installed. Install the extra "
            "(uv sync --extra ingest) or set sources.pdf.enabled: false."
        ) from exc
    return True


def _enabled_tiers(config: Config) -> set[str]:
    """The resolver tiers actually switched on, master switch included."""
    if not config.sources.enabled:
        return set()
    tiers = set()
    if config.sources.identifiers.enabled:
        tiers.add(fetch.ResolutionTier.IDENTIFIER.value)
    if config.sources.open_access.enabled:
        tiers.add(fetch.ResolutionTier.OPEN_ACCESS.value)
    if config.sources.extraction.enabled:
        tiers.add(fetch.ResolutionTier.EXTRACTION.value)
    return tiers


def _extraction_call_ceiling(config: Config) -> int:
    """The configured cap, or the structural one when none is set (D-paid-tier-page).

    `max_source_urls * hard_cap` is the most distinct URLs a run could ever cite — every
    citation replaced in every round. Writer reads may additionally resolve candidate
    URLs (D-writer-resolver-budget); with `read_budget` unbounded (D-unbounded-evidence)
    there is no number to add, so the derivation falls back to the configured cap alone
    and an operator who wants this tier bounded sets `max_calls_per_run` explicitly.
    Derived rather than written down so raising either structural budget cannot silently
    starve the tier at the old number.

    This bounds a *bug*, not a bill. `SourceFetcher` caches per URL for the whole run, so
    three critics re-verifying one '## Sources' list across eight rounds cost one call per
    URL, not twenty-four; what this catches is a fetch loop that ignores that cache.
    """
    configured = config.sources.extraction.max_calls_per_run
    if configured is not None:
        return configured
    reads = config.search.read_budget if config.search.read_sources else 0
    writer_reads = reads or 0
    return max(1, config.search.max_source_urls * config.budgets.hard_cap + writer_reads)


def _cache_max_chars(config: Config) -> int:
    """The cap the shared fetch cache stores a body at.

    The larger of the two consumers' caps, so neither is clipped to the other's — each
    is then handed a view that applies its own (`fetch.CappedFetcher`). The resolver
    ladder uses the same number, or a body reached through an open-access mirror would
    be bounded differently from one fetched directly (D-writer-source-reads).
    """
    if config.search.read_sources:
        return max(config.search.fetch_max_chars, config.search.read_max_chars)
    return config.search.fetch_max_chars


def _build_resolver(config: Config, warnings: list[str]):
    """Construct the resolver ladder (D-existence-vs-body), or return None when no tier is on.

    A sibling of `_build_searcher` and `_pdf_reading_enabled` for the same reason both of
    those live here: network clients are assembled at startup and injected, so the graph
    itself performs no I/O and the test suite stays offline (D-seed-conversion).

    Two failure modes, deliberately graded differently. An unrecognised provider name is
    **fatal**: it silently disables a tier the operator believes they enabled, which is
    the same class of failure `_build_searcher` refuses to start with. A missing contact
    email is a **warning**: the polite pool is a courtesy, and demotion to the anonymous
    pool is degraded service rather than a broken configuration.
    """
    tiers = _enabled_tiers(config)
    if not tiers:
        return None
    # `read_sources` also builds a fetcher (D-writer-source-reads), so the ladder runs
    # for a writer's reads even with verification off. The warning is about a
    # configuration in which nothing fetches at all, which is now both switches off.
    if not (config.search.verify_sources or config.search.read_sources):
        warnings.append(
            "sources: resolver tiers are enabled but search.verify_sources and "
            "search.read_sources are both off, so nothing fetches and no tier will "
            "ever run"
        )

    sources = config.sources
    contact_email = sources.contact_email
    if not contact_email:
        warnings.append(
            f"sources: ${sources.contact_email_env} is unset, so registry requests go to "
            f"the anonymous rate-limit pool instead of Crossref/Unpaywall's polite pool "
            f"— expect slower answers and more throttling under load"
        )

    identifiers_on = fetch.ResolutionTier.IDENTIFIER.value in tiers
    open_access_on = fetch.ResolutionTier.OPEN_ACCESS.value in tiers
    extraction_on = fetch.ResolutionTier.EXTRACTION.value in tiers

    # Credentials resolved here, before a single call: the same fail-closed posture
    # `_build_searcher` applies to Brave. A tier that starts without its key spends its
    # whole budget on 401s and reports them as coverage.
    extraction_key = ""
    if extraction_on:
        if not sources.extraction.provider:
            raise ConfigError(
                "fail closed: sources.extraction.enabled is on but no provider is named. "
                "Defaulting to whichever provider happens to be first would send a paid "
                "call to a vendor nobody chose."
            )
        extraction_key = search.resolve_token(
            sources.extraction.api_key_env, sources.extraction.token_file
        )
    core_key = ""
    if open_access_on and fetch.Provider.CORE.value in sources.open_access.providers:
        core_key = search.resolve_token(
            sources.open_access.core_api_key_env, sources.open_access.core_token_file
        )
    if open_access_on and not config.sources.pdf.enabled:
        # Most free copies are PDFs (arXiv's only form is one), and without PDF reading
        # each of those mirrors fetches, comes back as an unreadable content type, and
        # falls through to metadata. The tier still helps — it just helps far less than
        # its call budget suggests.
        warnings.append(
            "sources: the open-access tier is on but sources.pdf.enabled is off, so most "
            "free copies (arXiv has no other form) will fetch and then be unreadable"
        )
    try:
        resolver, resolver_warnings = resolve.build(
            # A disabled tier is handed no provider names, so nothing is constructed for
            # it at all — enabling one tier can never turn the other on.
            identifier_providers=sources.identifiers.providers if identifiers_on else [],
            identifier_timeout=sources.identifiers.timeout_seconds,
            identifier_budget=sources.identifiers.max_calls_per_run,
            open_access_providers=sources.open_access.providers if open_access_on else [],
            open_access_timeout=sources.open_access.timeout_seconds,
            open_access_budget=sources.open_access.max_calls_per_run,
            contact_email=contact_email,
            core_api_key=core_key,
            extraction_provider=sources.extraction.provider if extraction_on else "",
            extraction_api_key=extraction_key,
            extraction_timeout=sources.extraction.timeout_seconds,
            extraction_budget=_extraction_call_ceiling(config),
            max_chars=_cache_max_chars(config),
        )
    except resolve.UnknownProvider as exc:
        raise ConfigError(f"fail closed: {exc}") from exc

    warnings.extend(resolver_warnings)
    log.info("source resolver enabled: tiers %s", sorted(tiers))
    return resolver


def _build_searcher(config: Config, client: LLMClient) -> search.BraveSearch | None:
    """Construct the search client, or fail closed. Returns None when search is off.

    Both checks here are fatal by design. A missing credential is obvious. The
    tool-calling probe is the subtle one: several small models accept a `tools`
    parameter and never emit a call. That writer would still be told to produce a
    '## Sources' section, and would fill it from memory — citations that look
    identical to retrieved ones but were never looked up. Refusing to start is the
    only honest response, since nothing downstream can tell the two apart.
    """
    if not config.search.enabled:
        return None

    token = search.resolve_token(config.search.api_key_env, config.search.token_file)

    incapable = [a for a in config.roster.writers if not client.probe_tool_calling(a)]
    if incapable:
        raise ConfigError(
            f"fail closed: web search is enabled but these writers cannot emit tool "
            f"calls: {incapable}. They would produce unsourced reports that still "
            f"claim citations. Remove them from the writer pool or disable search."
        )

    log.info(
        "web search enabled: %s queries for this run, %d results per query",
        "unbounded" if config.search.query_budget is None else config.search.query_budget,
        config.search.max_results,
    )
    return search.BraveSearch(
        token,
        budget=search.QueryBudget(config.search.query_budget),
        max_results=config.search.max_results,
        timeout=config.budgets.timeout_seconds,
        min_interval=config.search.min_interval_seconds,
    )


# --------------------------------------------------------------------- intake


def _today() -> str:
    """Monkeypatch point for tests; UTC so a run's date is host-timezone-independent."""
    from datetime import datetime

    return datetime.now(UTC).date().isoformat()


def _intake(state: State, rt: Runtime) -> dict:
    question = (state.get("question") or "").strip()
    seed = (state.get("seed") or "").strip() or None
    cfg = rt.config

    # RA-018: v1 requires an explicit question on every path, including seed-only.
    if not question:
        raise ConfigError(
            "intake rejected: a question is required (question inference from a bare "
            "seed is deferred behind an opt-in flag)"
        )
    if len(question) > cfg.max_question_chars:
        raise ConfigError(f"intake rejected: question exceeds {cfg.max_question_chars} chars")
    if seed and len(seed) > cfg.max_report_chars:
        raise ConfigError(f"intake rejected: seed exceeds {cfg.max_report_chars} chars")

    base: dict = {
        "question": question,
        "seed": seed,
        "run_date": _today(),
        "round": 0,
        "hash_history": [],
        "writer_rotation": 0,
        "lens_results": {},
        "used_critics": {},
        "critique_rounds": {},
        "clean_records": [],
        "defects": [],
        "polish_used": 0,
        "polish_next": False,
        "rewrites_used": 0,
        "full_rewrite_next": False,
        "critique_attempts_remaining": cfg.budgets.critique_attempts,
        "confirmation_attempts_remaining": cfg.budgets.confirmation_attempts,
        "prev_material": -1,
        "prev_signature": [],
        "stagnation_count": 0,
        "fatal": False,
        "fatal_reason": None,
        "terminal_status": None,
        "scoreboard": [],
        "pending_lenses": [lens.value for lens in LENSES],
        "confirming": False,
        # Ingest runs at the edge, so anything it noticed about the seed — a format
        # that carried no headings, a truncated page — arrives here as text and joins
        # the run's own warnings rather than getting its own channel.
        "warnings": [*rt.warnings, *(state.get("seed_warnings") or [])],
        "pending_disputes": [],
        "adjudications": [],
        "dispute_budget_remaining": cfg.disputes.budget if cfg.disputes.enabled else 0,
        "defect_provenance": {},
    }

    if seed:
        # The seed *is* R1. It has no model author, so every critic is eligible.
        h = report_mod.artifact_hash(seed)
        base |= {
            "report": seed,
            "artifact_hash": h,
            "author_alias": "seed",
            "author_identity": "external/seed",
            "hash_history": [h],
            # the seed is R1, so it occupies the first tick — and min_ticks > 1
            # guarantees it is never accepted on its first critique
            "round": 1,
        }
        # Written here, not only by the web worker, so every entry path leaves the
        # exact hashed bytes on disk and any seeded run is resumable.
        rt.store.seed(seed)
        rt.store.report(1, h, seed, "seed")
        rt.store.event(
            "intake",
            path="seed",
            artifact_hash=h,
            run_date=base["run_date"],
            # Provenance belongs in the audit trail, not in the run state: it answers
            # "where did R1 come from?" without changing what any node reads.
            seed_format=state.get("seed_format"),
            seed_source=state.get("seed_source"),
        )
    else:
        rt.store.event("intake", path="question", run_date=base["run_date"])
    return base


def _route_intake(state: State) -> str:
    return "critique" if state.get("report") else "generate"


# ------------------------------------------------------------------- generate


def _scope_fields(
    cfg: Config,
    previous: str | None,
    text: str,
    defects: list[Defect],
    *,
    polish: bool,
    full_rewrite: bool,
) -> dict[str, int]:
    """Warn-only scope measurement for the `generate` event (D-scoped-revision).

    Silent for the three generations that legitimately touch everything — the first
    draft, a rule-9 polish pass, and a rule-13 rewrite — so an absent field means "not
    applicable" rather than "in scope", and the A/B never averages those rounds in.
    """
    if cfg.revision.scope_check == "off" or not previous or polish or full_rewrite:
        return {}
    scope = report_mod.revision_scope(previous, text, [d.locus for d in defects])
    if scope.out_of_scope:
        log.info(
            "revision touched %d paragraph(s) no fix task named (of %d changed)",
            len(scope.out_of_scope),
            len(scope.changed),
        )
    return scope.as_event_fields()


def _retrieval_kwargs(rt: Runtime, session: reading.ReadSession) -> dict[str, Any]:
    """The tool half of a writer call: `web_search`, and `read_source` when it is on.

    Returns `{}` when retrieval is off, so a search-less build hands `complete` exactly
    the argument list it always did.

    Both tools go through one handler because `LLMClient.complete` drives a single
    `(name, arguments) -> text` callback. Dispatch lives here rather than in either
    module so neither has to import the other: `search` stays a Brave client and
    `reading` stays a page reader, and the composition root — this module, which already
    builds both — is what knows they are offered together.
    """
    if not rt.search_enabled:
        return {}

    searcher = rt.searcher
    handlers: dict[str, Any] = {
        "web_search": search.make_tool_handler(searcher, on_results=session.record_results)
    }
    tools = [search.SEARCH_TOOL]
    reader = rt.reader
    if reader is not None:
        handlers["read_source"] = reading.make_tool_handler(reader, session)
        tools.append(reading.READ_SOURCE_TOOL)

    def route(name: str, raw_arguments: str) -> str:
        handler = handlers.get(name)
        if handler is None:
            return prompts.search_error_block(f"unknown tool {name!r}")
        return handler(name, raw_arguments)

    def offering() -> bool:
        # Withdraw the tools the moment their budgets are gone. Otherwise the handlers
        # keep answering "budget exhausted" and a determined writer spends every
        # remaining round asking again instead of writing (D-provider-retry). Both must
        # be spent: a writer with reads left can still use a round well after search is
        # finished, and vice versa.
        if not searcher.budget.exhausted:
            return True
        return reader is not None and not reader.budget.exhausted

    return {
        "tools": tools,
        "tool_handler": route,
        "max_tool_rounds": rt.config.search.max_tool_rounds,
        "should_offer_tools": offering,
    }


def _generate(state: State, rt: Runtime) -> dict:
    cfg = rt.config
    last_author = state.get("author_identity")
    if last_author == "external/seed":
        last_author = None  # a human seed excludes nobody from writing

    rotation = state.get("writer_rotation", 0)
    try:
        pool = roles.writer_pool(cfg.roster, rt.identities, last_author)
    except roles.RosterExhausted as exc:
        return {"fatal": True, "fatal_reason": str(exc)}

    polish = state.get("polish_next", False)
    defects = [Defect.model_validate(d) for d in state.get("defects", [])]

    # Rule 13 spent a rewrite to break a stalled patch chain: this one generation asks
    # for the whole document however `revision.mode` is set (D-scoped-revision).
    full_rewrite = state.get("full_rewrite_next", False)
    mode = "rewrite" if full_rewrite else cfg.revision.mode

    # .get(): a checkpoint from before run_date existed resumes dateless, which is
    # exactly the prior behavior.
    run_date = state.get("run_date")
    previous = state.get("report")
    if previous:
        user = prompts.writer_revision(
            state["question"],
            previous,
            defects,
            polish,
            rt.disputes_enabled,
            current_date=run_date,
            mode=mode,
        )
    else:
        user = prompts.writer_first_draft(state["question"], current_date=run_date)

    # One flaky response must not cost the run. Attempts rotate through the eligible
    # pool and wrap, so a model that is down, rate-limited, or answering with nothing
    # is routed around when there is somewhere to route to — and simply given another,
    # spaced, chance when there is not. On a revision round a two-writer roster leaves
    # exactly one eligible model (author exclusion already removed the other), so
    # bounding these attempts by the pool size made the whole budget 1 and one empty
    # completion aborted the run (D-provider-retry). Re-asking a pool member never re-asks the
    # previous author: `writer_pool` excluded them before this ran.
    attempts = cfg.budgets.writer_attempts
    alias = ""
    completion = None
    last_failure = ""
    for offset in range(attempts):
        if offset > 0:
            rt.client.backoff_between_writer_attempts(offset)
        alias = pool[(rotation + offset) % len(pool)]
        # A fresh session per `complete()` call, inside the loop rather than outside it,
        # because each attempt is a *different model* from the pool. Hoisting this would
        # let a retry read a page the failed attempt's search found, and would let the
        # support manifest be checked against bodies the drafting model never saw — the
        # cross-context affordance the per-call allowlist exists to refuse
        # (D-writer-source-reads, principle #6).
        session = reading.ReadSession()
        search_kwargs = _retrieval_kwargs(rt, session)
        try:
            reply = rt.client.complete(
                alias,
                system=prompts.writer_system(rt.search_enabled, rt.read_sources),
                user=user,
                max_tokens=32000,
                **search_kwargs,
            )
        except ModelCallError as exc:
            last_failure = f"generator {alias} failed: {exc}"
            failure_class = getattr(exc, "failure_class", "call_failed")
        else:
            if reply.text.strip():
                completion = reply
                rotation += offset
                break
            last_failure = f"generator {alias} returned an empty report"
            # Not a `ModelCallError`: the call succeeded and the model answered with
            # whitespace, which is a different defect from any `llm` raises.
            failure_class = "empty_report"
        # Recorded per attempt: a run that silently changed authors mid-draft is
        # unauditable, and the roster's weak models are only visible from here.
        # `failure_class` is what makes them *countable*: `reason` carries the alias
        # and the provider's own words, so it is unique per attempt and cannot be
        # grouped. Without a stable token, "this writer keeps failing" stays an
        # impression — which is how a routing defect was once read as a broken model.
        rt.store.event(
            "generate_failed",
            author=rt.identities[alias],
            reason=last_failure,
            failure_class=failure_class,
        )
        log.warning("writer attempt %d/%d: %s", offset + 1, attempts, last_failure)

    if completion is None:
        return {"fatal": True, "fatal_reason": f"every eligible writer failed; last: {last_failure}"}

    identity = rt.identities[alias]
    text = completion.text.strip()
    if len(text) > cfg.max_report_chars:
        text = text[: cfg.max_report_chars]

    h = report_mod.artifact_hash(text)
    history = [*state.get("hash_history", []), h]
    # A tick is one *draft*, counted here rather than at triage: a writer that
    # returns byte-identical output must still advance the clock, or the loop
    # would stall below min_ticks forever. Re-critiques (rules 2 and 8) never
    # pass through this node, so they correctly leave `round` alone (RG-001).
    round_no = state.get("round", 0) + 1
    rt.store.report(round_no, h, text, identity)
    rt.store.event(
        "generate",
        author=identity,
        artifact_hash=h,
        polish=polish,
        full_rewrite=full_rewrite,
        defects_applied=len(defects),
        tokens=completion.completion_tokens,
        # Auditable after the fact: did this draft's citations come from a lookup?
        searches=completion.tool_calls,
        # And the deeper version of the same question: did it read any of them?
        # Counts only — a URL is content and events.jsonl outlives a content purge.
        **_read_fields(session),
        **_scope_fields(cfg, previous, text, defects, polish=polish, full_rewrite=full_rewrite),
    )

    _record_support(rt, alias, state["question"], text, round_no, session)
    pending_disputes = _elicit_disputes(state, rt, alias, text, defects, polish)

    return {
        "report": text,
        "artifact_hash": h,
        "author_alias": alias,
        "author_identity": identity,
        "writer_rotation": rotation + 1,
        "hash_history": history,
        "round": round_no,
        # Every generation resets every per-artifact accumulator — unconditionally,
        # even when the writer happened to reproduce byte-identical text. Keying the
        # reset on "the hash changed" would let a clean record earned under a
        # *different* author satisfy acceptance for this one (RC-002).
        "clean_records": [],
        "lens_results": {},
        "used_critics": {},
        "critique_rounds": {},
        "defects": [],
        "polish_next": False,
        "full_rewrite_next": False,
        "pending_lenses": [lens.value for lens in LENSES],
        "confirming": False,
        "pending_disputes": pending_disputes,
    }


def _read_fields(session: reading.ReadSession) -> dict[str, Any]:
    """What this draft's writer read, as counts and a closed-vocabulary tally.

    Never a URL and never a character of page text: `events.jsonl` survives
    `purge --content-only` and carries signal only (RA-016). `SourceOutcome` is the same
    closed vocabulary `_failure_reasons` relies on for the verification path, so an
    operator can read the two tallies side by side.
    """
    reads = session.reads
    if not reads:
        return {"read_attempts": 0, "bodies_read": 0}
    outcomes: dict[str, int] = {}
    for source in reads.values():
        key = source.outcome.value
        outcomes[key] = outcomes.get(key, 0) + 1
    return {
        # `read_attempts`, not `sources_read`: a blocked page, a 404 and a spent budget
        # are all recorded here, and an operator scanning one number must not read
        # "the writer opened nine pages" off a column of refusals. `bodies_read` is the
        # number that means what the longer name would have implied.
        "read_attempts": len(reads),
        "bodies_read": outcomes.get(fetch.SourceOutcome.FULL_TEXT.value, 0),
        "sources_offered": session.offered_count,
        "read_outcomes": outcomes,
    }


def _record_support(
    rt: Runtime,
    alias: str,
    question: str,
    report_text: str,
    round_no: int,
    session: reading.ReadSession,
) -> None:
    """The traceability pass (D-writer-source-reads): ask the writer where each cited
    claim's support sits, then check it mechanically against the bodies it read.

    Audit-side and nothing else. The manifest is written to `support/`, the verdict
    counts to `events.jsonl`, and neither reaches another model's context, the defect
    list, `OrchestratorView` or the controller — so a writer authoring its own manifest
    has no lever on its own review.

    Never fatal, in the manner of `_elicit_disputes`: any failure degrades to no
    manifest and the run proceeds exactly as it would with the channel off.
    """
    if not rt.support_manifest:
        return
    bodies = session.reads
    readable = [s for s in bodies.values() if s.ok]
    if not readable:
        # Nothing was read, so nothing can be checked, and asking anyway would collect
        # spans no body can falsify — the failure the `support_manifest` config guard
        # refuses at load time, arriving by another road.
        return

    # The length is weighed *before* the body is added, so no page after the first can
    # carry the pass past `support_max_chars`. The one documented exception is the first
    # readable body, which is shown whole however long it is — up to `read_max_chars`, so
    # the ceiling on this pass is `support_max_chars + read_max_chars` rather than
    # `support_max_chars`. Deliberate: a manifest pass with no source text in front of it
    # would collect spans quoted from memory, which is the failure this check exists to
    # catch, and showing nothing is worse than showing one page over budget.
    budget = rt.config.search.support_max_chars
    shown, used = [], 0
    for source in readable:
        if shown and used + len(source.text) > budget:
            break
        shown.append(source)
        used += len(source.text)

    try:
        manifest = rt.client.structured(
            alias,
            system=prompts.writer_system(False),
            user=prompts.writer_support(question, report_text, shown),
            schema=SupportManifest,
            max_tokens=8000,
        )
    except (ModelCallError, MalformedOutputError) as exc:
        # Only the exception TYPE, never `str(exc)` — `MalformedOutputError`'s message is
        # a sanitized validator summary as of D-validator-error-hygiene, but a
        # `ModelCallError` alongside it can still carry raw provider/model text, which
        # here is verbatim report and page material (RA-016), the same rule
        # `_elicit_disputes` follows.
        log.warning("support manifest failed (%s); continuing", type(exc).__name__)
        rt.store.event("support_manifest_failed", round=round_no, reason=type(exc).__name__)
        return

    checked = support.check(manifest, report_text, bodies)
    rt.store.support(
        round_no,
        {
            "round": round_no,
            "artifact_author": rt.identities[alias],
            "entries": support.record(checked),
        },
    )
    rt.store.event(
        "support_manifest",
        round=round_no,
        entries=len(checked),
        verdicts=support.tally(checked),
        with_locator=support.locator_coverage(checked),
        bodies_shown=len(shown),
    )


def _elicit_disputes(
    state: State, rt: Runtime, alias: str, revised: str, defects: list[Defect], polish: bool
) -> list[dict]:
    """The dispute-elicitation pass (D-writer-disputes): one separate structured call to the
    writer that just revised. Self-contained entries (full Defect + Dispute) so a
    resume between generate and adjudicate loses nothing when `defects` resets.

    Never fatal: any failure degrades to 'no disputes' and the run proceeds
    exactly as it would with the channel off."""
    if (
        not rt.disputes_enabled
        or polish
        or not defects
        or state.get("dispute_budget_remaining", 0) <= 0
    ):
        return []
    try:
        raw = rt.client.structured(
            alias,
            system=prompts.writer_system(False),
            user=prompts.writer_dispute(state["question"], revised, defects),
            schema=WriterDisputes,
            max_tokens=8000,
        )
    except (ModelCallError, MalformedOutputError) as exc:
        # Record only the exception TYPE — never `str(exc)`. `MalformedOutputError`'s
        # message is a sanitized validator summary as of D-validator-error-hygiene, but a
        # ModelCallError message can still carry raw model I/O (the writer's dispute
        # grounds and evidence quotes), which is report-derived (private) content.
        # events.jsonl is RETAINED by `ra purge --content-only` (D-writer-disputes), so any
        # exception-derived string here would leak artifact text past a content purge.
        rt.store.event("dispute_pass_failed", error_type=type(exc).__name__)
        return []
    accepted = dispute_mod.validate_disputes(raw, defects, rt.config.disputes.max_per_pass)
    return [
        {"defect": d.model_dump(mode="json"), "dispute": dis.model_dump(mode="json")}
        for dis, d in accepted
    ]


# ----------------------------------------------------------------- adjudicate


def _paragraph_containing(structure, span: str) -> str:
    """The defect's locus indexes the *previous* draft, but the revised draft is
    what the run now holds — so the arbiter's context paragraph is found by span
    (the writer was told to leave disputed text intact). Falls back to the span
    itself when the text moved."""
    needle = triage._normalize(span)
    if needle:
        for p in structure.paragraphs:
            if needle in triage._normalize(p.text):
                return p.text
    return span


def _adjudicate(state: State, rt: Runtime) -> dict:
    """Rule on the writer's disputes (D-writer-disputes). A passthrough when there are none.

    Every path that is not an explicit `upheld` leaves the finding standing; only
    upheld records ever suppress anything downstream. This node re-runs cleanly on
    resume: `pending_disputes` is self-contained and the once-per-key registry
    makes replayed entries free duplicates."""
    pending = state.get("pending_disputes") or []
    if not pending:
        return {}

    records = [AdjudicationRecord.model_validate(r) for r in state.get("adjudications", [])]
    ruled_keys = {dispute_mod.registry_key(r.category, r.claim_span) for r in records}
    budget = state.get("dispute_budget_remaining", 0)
    provenance = state.get("defect_provenance", {})
    report_text = state["report"]
    structure = report_mod.parse(report_text)
    round_no = state.get("round", 0)
    disputer = state.get("author_identity", "(none)")

    for seq, entry in enumerate(pending, 1):
        defect = Defect.model_validate(entry["defect"])
        challenge = Dispute.model_validate(entry["dispute"])
        key = dispute_mod.registry_key(defect.category, defect.claim_span)
        # Content record first (purgeable dir): the full grounds are auditable
        # while the run is retained, and droppable with the other content.
        rt.store.dispute(
            round_no, seq, {"defect": entry["defect"], "dispute": entry["dispute"]}
        )

        if key in ruled_keys:
            verdict, method = "dismissed", "duplicate"
        elif budget <= 0:
            verdict, method = "dismissed", "budget_exhausted"
        else:
            budget -= 1
            mechanical = dispute_mod.adjudicate_mechanical(
                challenge, defect, report_text, rt.fetcher
            )
            if mechanical is True:
                verdict, method = "upheld", "mechanical"
            else:
                raisers = set(provenance.get(f"{key[0]}|{key[1]}", []))
                arbiters = dispute_mod.eligible_arbiters(
                    rt.config.roster, rt.identities, disputer, raisers
                )
                if not arbiters:
                    verdict, method = "dismissed", "no_eligible_arbiter"
                else:
                    page = None
                    if (
                        rt.fetcher is not None
                        and challenge.evidence_url
                        and challenge.evidence_url in fetch.extract_source_urls(report_text)
                    ):
                        page = rt.fetcher.fetch(challenge.evidence_url)
                    try:
                        ruling = dispute_mod.adjudicate_one(
                            rt.client,
                            arbiters[0],
                            defect,
                            challenge,
                            _paragraph_containing(structure, defect.claim_span),
                            state["question"],
                            evidence_page=page,
                            max_tokens=rt.config.disputes.arbiter_max_tokens,
                        )
                    except (ModelCallError, MalformedOutputError):
                        verdict, method = "dismissed", "arbiter_failed"
                    else:
                        verdict = "upheld" if ruling.dispute_upheld else "overruled"
                        method = "arbiter"

        ruled_keys.add(key)
        records.append(
            AdjudicationRecord(
                category=defect.category,
                claim_span=defect.claim_span,
                verdict=verdict,  # type: ignore[arg-type]
                method=method,  # type: ignore[arg-type]
                round=round_no,
            )
        )
        # Signal-only: no spans, no grounds — events.jsonl outlives a content purge.
        rt.store.event(
            "adjudication",
            category=defect.category.value,
            verdict=verdict,
            method=method,
            round=round_no,
        )

    return {
        "adjudications": [r.model_dump(mode="json") for r in records],
        "dispute_budget_remaining": budget,
        "pending_disputes": [],
    }


# ------------------------------------------------------------------- critique

#: Outcomes whose HTTP status tells an operator something the outcome alone does not.
_STATUS_BEARING_OUTCOMES = frozenset(
    {fetch.SourceOutcome.BLOCKED, fetch.SourceOutcome.NOT_FOUND}
)


def _failure_reasons(failed: list) -> dict[str, int]:
    """Tally why fetches failed, from the closed outcome vocabulary.

    This used to slice `FetchedSource.error` — free text that can be
    `"ConnectionResetError: <detail>"` or `"unreadable content type (application/pdf)"`,
    whose tail may carry a URL or page detail that does not belong in the audit trail
    (RA-016). `SourceOutcome` removes the hazard rather than filtering it: every key
    here is now a member of a fixed enum, optionally suffixed with the HTTP status,
    which is a number and not private run material.

    The status suffix is kept only where it changes what an operator would do —
    `blocked:403` (add a provider) reads differently from `blocked:429` (slow down).
    """
    counts: dict[str, int] = {}
    for source in failed:
        reason = source.outcome.value
        if source.status and source.outcome in _STATUS_BEARING_OUTCOMES:
            reason = f"{reason}:{source.status}"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _resolution_tiers(sources: list) -> dict[str, int]:
    """Tally which rung of the ladder produced each source — **all** of them, not just
    the failures (D-existence-vs-body).

    A tier that never fires costs calls and buys nothing, and the failure tally cannot
    show that: a source the open-access tier rescued is a *success* and leaves no trace
    there. `{"direct": 5, "open_access": 2, "identifier": 4}` is what tells an operator
    whether a tier is earning its keep, and is closed-vocabulary throughout (RA-016).
    """
    counts: dict[str, int] = {}
    for source in sources:
        tier = source.tier.value
        counts[tier] = counts.get(tier, 0) + 1
    return counts


#: Serialises the read-modify-write in `_record_coverage`. A module-level lock rather
#: than one per sink because the sink is a plain dict owned by `_critique`, and the
#: contention is one comparison per evidence critic per pass.
_COVERAGE_LOCK = threading.Lock()


def _coverage_rank(observed: dict) -> tuple[int | str, ...]:
    """How far verification actually got, as a total key two tallies can be ranked on.

    Entries *independently checked* comes first — `cited` minus the ones nothing
    reached, which counts a definitive not-found as checked, because it is
    (D-notfound-fabrication). Distinct bodies read, body-backed entries, registry
    confirmations and definitive not-founds then order equal-reach observations by how
    much direct evidence they contain (D-existence-vs-body). The canonical record is a
    final tie-breaker so future fields cannot quietly reintroduce arrival-order behavior.
    """

    def count(key: str) -> int:
        value = observed.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    checked = max(0, count("cited") - count("not_independently_checked"))
    numeric = (
        checked,
        count("bodies_read"),
        count("body_backed_entries"),
        count("metadata_only"),
        count("not_found"),
        count("attempted"),
        -count("blocked_or_unreadable"),
        -count("budget_exhausted"),
        -count("not_attempted"),
        -count("not_addressable"),
    )
    canonical = json.dumps(observed, sort_keys=True, separators=(",", ":"))
    return (*numeric, canonical)


def _record_coverage(
    sink: dict[str, dict],
    artifact_hash: str,
    observed: dict,
    *,
    on_recorded: Callable[[dict], None] | None = None,
) -> bool:
    """Put `observed` on the record for this artifact if it reached furthest; say whether
    it did.

    At review depth above 1 an artifact's bibliography is tallied once per evidence
    critic, concurrently, against a fetch cache that is monotone but last-write-wins
    (`fetch.SourceFetcher.fetch`). Two critics that both miss the cache on the same URL
    can therefore observe different outcomes, and there is no aggregate to read back
    afterwards — so the run keeps the observation that reached furthest rather than
    whichever thread happened to finish first. The total rank makes the record independent
    of scheduling even when two observations reached equally far but ended differently.

    `on_recorded` runs while the same lock is held. That is what keeps the audit trail
    honest: updates and their events cannot interleave, so the last `source_coverage` event
    for an artifact is always the one `final.json` will carry.
    """
    with _COVERAGE_LOCK:
        current = sink.get(artifact_hash)
        if current is not None and _coverage_rank(current) >= _coverage_rank(observed):
            return False
        sink[artifact_hash] = observed
        if on_recorded is not None:
            on_recorded(observed)
        return True


def _critique_one(
    rt: Runtime,
    lens: Lens,
    alias: str,
    question: str,
    report_text: str,
    artifact_hash: str,
    author_identity: str,
    attempt: int,
    run_date: str | None = None,
    coverage_sink: dict[str, dict] | None = None,
) -> LensResult:
    """One lens, one critic, one fresh context. Failure is recorded as a *failed lens*,
    never as 'no issues found' — a failed review can never manufacture a clean record.

    The critic is chosen by the caller (`_critic_slots`), because at review depth above
    1 the slate for a lens has to be drawn as a whole: picking one model at a time from
    the same `used` set would return the same alias every time.

    `coverage_sink` is the one thing this reports back other than its `LensResult`: the
    evidence lens is where a bibliography is read against real fetch outcomes, and that
    tally has to reach `_finalize` for the artifact it was taken on, not for whichever
    draft the loop stopped at (D-observed-source-coverage). It is an out-parameter rather
    than a second return value so the direct callers in the test suite keep their shape.
    At review depth above 1 the lens has several critics, so the sink has several writers
    per key and `_record_coverage` — not this function — decides which tally is kept.
    """
    identity = rt.identities[alias]
    try:
        roles.assert_author_exclusion(identity, author_identity, lens)
    except roles.RosterExhausted as exc:
        # A lens with no eligible non-author is fatal, but it must reach that verdict
        # through the controller (rule 1/3), not by crashing out of a worker thread.
        return LensResult(
            lens=lens,
            artifact_hash=artifact_hash,
            critic_alias="(none)",
            critic_identity="(none)",
            artifact_author_identity=author_identity,
            failed=True,
            failure_reason=str(exc)[:400],
            attempt=attempt,
        )

    # Only the evidence lens. Handing fetched pages to logic or completeness would
    # widen what those lenses can see without widening what they may raise, and the
    # extra context is a channel for smuggling material into a scope that has no use
    # for it (docs/isolation.md).
    # Kept per critic rather than hoisted to the pass, so each critic's prompt is built
    # from exactly what that critic was shown. `SourceFetcher` caches per URL under a
    # lock for the run's lifetime, so a depth-2 evidence slate costs one fetch per URL —
    # except in the narrow window where both critics miss the cache simultaneously on
    # the first pass, which costs a duplicate GET and is why the cache is monotone.
    sources = None
    if lens is Lens.EVIDENCE:
        by_url: dict[str, fetch.FetchedSource] = {}
        if rt.verify_sources:
            # Every cited URL, not a prefix of them (D-unbounded-evidence). The limit here
            # is the anti-pathological ceiling and must never bind on a real bibliography:
            # a citation the fetcher never sees carries no `SourceOutcome`, so it cannot
            # appear in `fetched_sources_block` at all, and the evidence critic judges it
            # on its face — which is how a 12-source cap turned a growing bibliography
            # into a self-sustaining supply of `fabricated_citation`.
            urls = fetch.extract_source_urls(
                report_text, limit=rt.config.search.max_source_urls
            )
            sources = rt.fetcher.fetch_all(urls) if urls else None
            if sources:
                # `FetchedSource.url` is the URL that was *asked for*, preserved by the
                # resolver ladder even when the body came from a mirror (which lands in
                # `body_source_url`), so this keys coverage to what the report cited.
                by_url = {source.url: source for source in sources}
                failed = [s for s in sources if not s.ok]
                # Counts alone said "10 of 12 failed" for a whole production run without
                # saying why, and a failure is cached for the run's lifetime, so the evidence
                # lens works from whatever survived. `_failure_reasons` keeps only the reason
                # *class* — a status code or an exception type — never a URL or page text
                # (RA-016).
                reasons = _failure_reasons(failed)
                rt.store.event(
                    "fetch_sources",
                    artifact_hash=artifact_hash,
                    fetched=len(sources),
                    failed=len(failed),
                    failure_reasons=reasons,
                    tiers=_resolution_tiers(sources),
                )
                if failed:
                    log.warning(
                        "source fetch: %d of %d failed (%s)",
                        len(failed),
                        len(sources),
                        ", ".join(f"{reason}x{count}" for reason, count in sorted(reasons.items())),
                    )
        # Taken on every artifact whose evidence lens runs, verification on or off: the
        # bibliography is what the report stands on either way, and "none of it was
        # independently checked" is a fact about a retrieval-only run that its
        # configuration label cannot state (D-observed-source-coverage).
        if coverage_sink is not None:
            observed = fetch.coverage(
                report_text, by_url, verification_enabled=rt.verify_sources
            )
            coverage_record = observed.as_dict()
            _record_coverage(
                coverage_sink,
                artifact_hash,
                coverage_record,
                on_recorded=lambda record: rt.store.event(
                    "source_coverage", artifact_hash=artifact_hash, **record
                ),
            )

    result = critique_mod.critique_once(
        rt.client,
        alias,
        identity,
        lens,
        question,
        report_text,
        artifact_hash,
        author_identity,
        sources=sources,
        require_verbatim_spans=rt.config.require_verbatim_spans,
        attempt=attempt,
        current_date=run_date,
        source_char_budget=rt.config.search.source_char_budget,
    )

    # A cited URL that a definitive not-found (404/410) does not resolve is a
    # `fabricated_citation` as a matter of fact, not a critic's judgement — so raise it
    # mechanically here, in the fetch path, rather than trusting a critic model to elect
    # to make the finding. This closes the launder that let a wholly-404 bibliography
    # clear the evidence lens (issue #92, D-notfound-fabrication). Attached only to a *completed* review: a
    # failed lens is discarded and re-critiqued (rule 2), and because the fetch is cached
    # the finding is simply re-derived on the next attempt, so nothing is lost.
    if sources and not result.failed:
        mechanical = triage.mechanical_citation_issues(sources, report_mod.parse(report_text))
        if mechanical:
            result = result.model_copy(update={"issues": [*mechanical, *result.issues]})
    return result


def _lens_results(state: State) -> dict[str, list[dict]]:
    """`lens_results` as a list per lens, tolerating the one-result-per-lens shape a
    run checkpointed before review depth existed (D-front-loaded-depth) still carries."""
    out: dict[str, list[dict]] = {}
    for lens, value in (state.get("lens_results") or {}).items():
        out[lens] = list(value) if isinstance(value, list) else [value]
    return out


def _flat_results(state: State) -> list[LensResult]:
    return [
        LensResult.model_validate(r) for group in _lens_results(state).values() for r in group
    ]


@dataclass(frozen=True)
class _CritiqueSlot:
    """One critic's turn at one lens: everything `_critique_one` needs, resolved before
    any call is made so the whole pass fans out over a flat work list."""

    lens: Lens
    alias: str
    attempt: int
    #: Set when selection itself failed, so the slot becomes a failed `LensResult`
    #: instead of a call. A lens with no eligible non-author is fatal, but it must
    #: reach that verdict through the controller (rule 1/3), not by raising here.
    unstaffed_reason: str | None = None


def _critic_slots(state: State, rt: Runtime, pending: list[Lens]) -> list[_CritiqueSlot]:
    """The full slate this pass will run, across every pending lens.

    Two things are decided here, sequentially and without I/O, because both need the
    whole picture: *which* models (a slate has to be drawn at once, or every draw
    returns the same first-eligible alias) and *how many* — enough to bring the lens up
    to its configured review depth on this artifact, and never fewer than one, since a
    pass the controller routed here must make a call or the loop would spin.

    Depth counts **completed distinct reviewers**, so a rule-2 retry after a total lens
    failure restores the full depth while a rule-8 top-up asks only for the reviewers
    the lens is actually short of.
    """
    used = {k: set(v) for k, v in (state.get("used_critics") or {}).items()}
    rounds: dict[str, int] = dict(state.get("critique_rounds") or {})
    completed: dict[str, set[str]] = {}
    for lens_value, group in _lens_results(state).items():
        completed[lens_value] = {
            roles.model_family(r["critic_identity"]) for r in group if not r["failed"]
        }

    author_identity = state["author_identity"]
    slots: list[_CritiqueSlot] = []
    for lens in pending:
        attempt = rounds.get(lens.value, 0)
        depth = rt.config.review.depth_for(lens)
        wanted = max(1, depth - len(completed.get(lens.value, set())))
        try:
            slate = roles.critic_slate(
                rt.config.roster,
                rt.identities,
                lens,
                author_identity,
                used.get(lens.value, set()),
                depth=wanted,
                # `attempt` counts this lens's tries on this artifact, so once the pool
                # is exhausted each further attempt lands on a different model.
                rotation=attempt,
            )
        except roles.RosterExhausted as exc:
            log.warning("lens %s has no eligible critic: %s", lens.value, exc)
            slots.append(
                _CritiqueSlot(
                    lens=lens,
                    alias="(none)",
                    attempt=attempt + 1,
                    unstaffed_reason=str(exc)[:400],
                )
            )
            continue
        for offset, alias in enumerate(slate):
            slots.append(_CritiqueSlot(lens=lens, alias=alias, attempt=attempt + offset + 1))
    return slots


def _critique(state: State, rt: Runtime) -> dict:
    pending = [Lens(v) for v in state.get("pending_lenses") or [lens.value for lens in LENSES]]
    question = state["question"]
    report_text = state["report"]
    artifact_hash = state["artifact_hash"]
    author_identity = state["author_identity"]

    used = {k: set(v) for k, v in (state.get("used_critics") or {}).items()}
    # Count of tries per lens on this artifact. Unlike `used`, this keeps climbing after
    # the eligible pool is exhausted, so rule-2 retries keep rotating (docs/convergence.md)
    # rather than freezing `attempt` — and so the rotation index passed to `critic_slate`.
    rounds: dict[str, int] = dict(state.get("critique_rounds") or {})
    results = _lens_results(state)

    run_date = state.get("run_date")
    # RB-010: this pass's `LensResult`s are stamped `confirm_state` below, once each
    # result already exists — a label the controller attaches to what came back, never
    # an input the critic's prompt (built above from `pending_lenses` alone) can see.
    confirming = bool(state.get("confirming", False))
    slots = _critic_slots(state, rt, pending)
    # Keyed by artifact hash and merged rather than replaced, exactly as `used_critics`
    # is: the shipped draft on a non-accepted terminal need not be the last one written,
    # so an earlier round's coverage has to survive the rounds after it
    # (D-observed-source-coverage).
    coverage_by_artifact: dict[str, dict] = dict(state.get("source_coverage", {}))

    def work(slot: _CritiqueSlot) -> LensResult:
        if slot.unstaffed_reason is not None:
            result = LensResult(
                lens=slot.lens,
                artifact_hash=artifact_hash,
                critic_alias="(none)",
                critic_identity="(none)",
                artifact_author_identity=author_identity,
                failed=True,
                failure_reason=slot.unstaffed_reason,
                attempt=slot.attempt,
            )
            return result.model_copy(update={"confirm_state": True}) if confirming else result
        result = _critique_one(
            rt,
            slot.lens,
            slot.alias,
            question,
            report_text,
            artifact_hash,
            author_identity,
            slot.attempt,
            run_date=run_date,
            coverage_sink=coverage_by_artifact,
        )
        return result.model_copy(update={"confirm_state": True}) if confirming else result

    # Bounded by `max_concurrency` exactly as before — review depth multiplies the
    # number of calls a pass makes, not the load it puts on the proxy at any instant.
    with ThreadPoolExecutor(max_workers=rt.config.budgets.max_concurrency) as pool:
        for result in pool.map(work, slots):
            # Appended, never replaced: at depth > 1 a lens holds several independent
            # reviews of the same artifact, and a failed one stays on the record so the
            # audit trail shows what was attempted. Only completed reviews are counted
            # (triage skips failures), so a lingering failure can neither add an issue
            # nor mint a clean record.
            results.setdefault(result.lens.value, []).append(result.model_dump(mode="json"))
            used.setdefault(result.lens.value, set()).add(result.critic_identity)
            rounds[result.lens.value] = rounds.get(result.lens.value, 0) + 1
            rt.store.critique(artifact_hash, result.lens.value, result.attempt, result)
            rt.store.event(
                "critique",
                lens=result.lens.value,
                critic=result.critic_identity,
                artifact_hash=artifact_hash,
                failed=result.failed,
                failure_reason=result.failure_reason,
                issues=len(result.issues),
            )

    return {
        "lens_results": results,
        "used_critics": {k: sorted(v) for k, v in used.items()},
        "critique_rounds": rounds,
        "source_coverage": coverage_by_artifact,
    }


# --------------------------------------------------------------------- triage


def _triage(state: State, rt: Runtime) -> dict:
    cfg = rt.config
    results = _flat_results(state)
    artifact_hash = state["artifact_hash"]

    # Suppression (D-writer-disputes) is applied ONCE, here, before anything is counted — so
    # tally, clean records, defects and the stagnation signature all see the same
    # filtered stream. Only `upheld` adjudications suppress.
    adjudications = [
        AdjudicationRecord.model_validate(r) for r in state.get("adjudications", [])
    ]
    upheld = dispute_mod.suppression_keys(adjudications)
    results, suppressed = triage.suppress(results, upheld)
    for entry in suppressed:
        rt.store.event("suppression", artifact_hash=artifact_hash, **entry)

    # A lens is incomplete when it has no *completed* review of this artifact — not
    # when one of several reviews failed (D-front-loaded-depth). At review depth 1 the two
    # readings coincide, which is the behaviour rules 2/3 were written against.
    lenses_failed = len(triage.unreviewed_lenses(results))
    per_category, totals = triage.tally(results)
    material = triage.material_count(totals)
    round_no = state.get("round", 0)

    prev_material = state.get("prev_material", -1)
    delta = 0 if prev_material < 0 else material - prev_material

    signature = list(triage.signal_signature(per_category))
    prev_signature = [tuple(x) for x in state.get("prev_signature", [])]
    stagnation = (
        state.get("stagnation_count", 0) + 1
        if signature and [tuple(s) for s in signature] == prev_signature
        else 0
    )

    # Clean records accumulate across re-critiques of the SAME hash; a new hash
    # resets them in `_generate`.
    existing = [CleanRecord.model_validate(r) for r in state.get("clean_records", [])]
    fresh = [r for r in triage.clean_records(results) if r not in existing]
    records = existing + fresh

    status = roles.lens_statuses(
        cfg.roster,
        rt.identities,
        state["author_identity"],
        artifact_hash,
        records,
        {k: set(v) for k, v in state.get("used_critics", {}).items()},
    )
    acceptance = acceptance_state(status, material)

    view = triage.build_view(
        per_category=per_category,
        totals=totals,
        delta_material_vs_prev=delta,
        lenses_failed=lenses_failed,
        round_no=round_no,
        min_ticks=cfg.budgets.min_ticks,
        hard_cap=cfg.budgets.hard_cap,
        roster_size=len(set(rt.identities.values())),
        lens_cleared={s.lens: s.cleared_count for s in status},
        acceptance=acceptance,
        polish_used=state.get("polish_used", 0),
        polish_cap=cfg.budgets.polish_cap,
        stagnation_count=stagnation,
        cycle_detected=detect_cycle(state.get("hash_history", []), cfg.budgets.cycle_period),
    )

    overruled = dispute_mod.overruled_keys(adjudications)
    defects = [d.model_dump(mode="json") for d in triage.to_defects(results, overruled)]
    rt.store.view(round_no, view)
    rt.store.event(
        "triage",
        artifact_hash=artifact_hash,
        material=material,
        lenses_failed=lenses_failed,
        cleared={s.lens.value: s.cleared_count for s in status},
        acceptance=acceptance,
    )

    scoreboard = [
        *state.get("scoreboard", []),
        {
            "round": round_no,
            "artifact_hash": artifact_hash,
            "blocking": totals.blocking,
            "major": totals.major,
            "minor": totals.minor,
            "report": state["report"],
            # The defect list belongs to *this* artifact. Carried on the row so that
            # when finalize ships an earlier draft than the terminal one, it can hand
            # the export that draft's own defects rather than the terminal round's —
            # the same per-artifact keying the clean records already have (issue #93).
            "defects": defects,
        },
    ]

    return {
        "round": round_no,
        "view": view.model_dump(mode="json"),
        "defects": defects,
        "clean_records": [r.model_dump(mode="json") for r in records],
        "prev_material": material,
        "prev_signature": signature,
        "stagnation_count": stagnation,
        "scoreboard": scoreboard,
        # Audit-side raiser identities per surviving material issue: consumed only
        # by arbiter *eligibility* (deterministic code), never by any prompt (D-writer-disputes).
        "defect_provenance": triage.defect_provenance(results),
    }


# --------------------------------------------------------------- orchestrate


def _orchestrate_call(client: LLMClient, alias: str, view: OrchestratorView) -> bool:
    """The blind LLM's entire interface. It takes an OrchestratorView and returns a
    boolean. There is deliberately no parameter through which content could arrive."""
    try:
        rec = client.structured(
            alias,
            system=prompts.ORCHESTRATOR_SYSTEM,
            user=prompts.orchestrator_user(view.model_dump_json(indent=2)),
            schema=OrchestratorRecommendation,
            max_tokens=4000,
        )
    except (ModelCallError, MalformedOutputError):
        return False  # no recommendation ⇒ no polish; the LLM can only *enable* rule 9
    return rec.polish_recommended


def _orchestrate(state: State, rt: Runtime) -> dict:
    view = OrchestratorView.model_validate(state["view"])
    alias = rt.config.roster.orchestrator_alias
    recommended = _orchestrate_call(rt.client, alias, view)
    rt.store.event("orchestrate", polish_recommended=recommended)
    return {"polish_next": recommended}


# ----------------------------------------------------------------- controller


def _empty_view(cfg: Config, round_no: int) -> OrchestratorView:
    """A zeroed view for the one case where the controller must decide before any
    critique has run: a generator that died on the first draft."""
    return OrchestratorView(
        counts={},
        totals=SeverityCounts(),
        delta_material_vs_prev=0,
        lenses_failed=0,
        round=round_no,
        min_ticks=cfg.budgets.min_ticks,
        hard_cap=cfg.budgets.hard_cap,
        roster_size=0,
        lens_cleared={lens.value: 0 for lens in LENSES},
        acceptance="none",
        polish_used=0,
        polish_cap=cfg.budgets.polish_cap,
        stagnation_count=0,
        cycle_detected=False,
    )


def _control(state: State, rt: Runtime) -> dict:
    cfg = rt.config
    # The controller owns *every* termination, including a dead generator — so the
    # fatal path routes through here rather than jumping to finalize (RA-020).
    view = (
        OrchestratorView.model_validate(state["view"])
        if state.get("view")
        else _empty_view(cfg, state.get("round", 0))
    )
    results = _flat_results(state)
    records = [CleanRecord.model_validate(r) for r in state.get("clean_records", [])]

    # These defaults only matter on the fatal-before-any-draft path; every other
    # path has a real artifact by the time control runs.
    author_identity = state.get("author_identity", "(none)")
    artifact_hash = state.get("artifact_hash", "")

    status = roles.lens_statuses(
        cfg.roster,
        rt.identities,
        author_identity,
        artifact_hash,
        records,
        {k: set(v) for k, v in state.get("used_critics", {}).items()},
    )

    ci = ControllerInput(
        view=view,
        fatal=state.get("fatal", False),
        fatal_reason=state.get("fatal_reason"),
        run_id=state["run_id"],
        artifact_hash=artifact_hash,
        artifact_hash_history=state.get("hash_history", []),
        author_identity=author_identity,
        lens_status=status,
        critique_attempts_remaining=state.get("critique_attempts_remaining", 0),
        confirmation_attempts_remaining=state.get("confirmation_attempts_remaining", 0),
        polish_recommended=state.get("polish_next", False),
        stagnation_limit=cfg.budgets.stagnation_limit,
        cycle_period=cfg.budgets.cycle_period,
        rewrites_used=state.get("rewrites_used", 0),
        rewrite_cap=cfg.budgets.rewrite_cap,
    )

    decision = decide(ci)
    if decision.rule == 2:
        # the concrete failed lenses are operational detail the table abstracts over
        decision = decision.model_copy(
            update={"recritique_lenses": triage.unreviewed_lenses(results) or list(LENSES)}
        )

    rt.store.decision(view.round, decision)
    rt.store.event(
        "control",
        rule=decision.rule,
        action=decision.action,
        terminal=decision.terminal_status,
        note=decision.note,
    )
    # The audit trail is per-run and stays on disk; these lines are the only account of
    # a run's shape that reaches container stdout, and therefore log aggregation. A run
    # that aborts after exhausting its critique budget used to be indistinguishable, from
    # outside, from one that shipped an answer.
    if decision.action == "terminal" and decision.terminal_status != "accepted":
        log.warning(
            "run %s terminal=%s at round %d (rule %d): %s",
            rt.store.run_id,
            decision.terminal_status,
            view.round,
            decision.rule,
            decision.note,
        )
    else:
        log.info(
            "run %s round %d: rule %d -> %s (%s)",
            rt.store.run_id,
            view.round,
            decision.rule,
            decision.action,
            decision.note,
        )

    out: dict = {"decision": decision.model_dump(mode="json")}
    if decision.action == "recritique":
        out["pending_lenses"] = [lens.value for lens in decision.recritique_lenses]
        if decision.rule == 2:
            out["critique_attempts_remaining"] = state["critique_attempts_remaining"] - 1
            out["confirming"] = False
        else:
            out["confirmation_attempts_remaining"] = (
                state["confirmation_attempts_remaining"] - 1
            )
            # Rule 8 only: the controller-side label RB-010 describes, set here — after
            # the decision is made, never before or during the model call that follows —
            # so `_critique` can stamp `LensResult.confirm_state` post-hoc without the
            # prompt it builds from `pending_lenses` ever reflecting it.
            out["confirming"] = True
    elif decision.action == "generate":
        out["polish_next"] = decision.polish
        if decision.polish:
            out["polish_used"] = state.get("polish_used", 0) + 1
        out["full_rewrite_next"] = decision.full_rewrite
        if decision.full_rewrite:
            out["rewrites_used"] = state.get("rewrites_used", 0) + 1
            # Load-bearing. `stagnation_count` is what selected this rule; leaving it at
            # the limit means the very next tick re-fires rule 13 and spends the whole
            # rewrite budget in consecutive ticks without ever letting a rewritten draft
            # be judged on its own signal.
            out["stagnation_count"] = 0
    else:
        out["terminal_status"] = decision.terminal_status
    return out


def _route_control(state: State) -> str:
    decision = Decision.model_validate(state["decision"])
    return {"generate": "generate", "recritique": "critique", "terminal": "finalize"}[
        decision.action
    ]


# -------------------------------------------------------------------- finalize


def _sourcing_label(rt: Runtime, observed: dict | None) -> str:
    """What this run may claim about its citations — measured, not configured.

    `search.verify_sources: true` used to produce *consensus-reviewed with verified
    sourcing* on every run that had it switched on, whatever the fetches actually
    returned. The label now states the observed coverage of the *shipped* draft, so an
    enabled feature can no longer imply a completeness the run did not reach
    (D-observed-source-coverage).

    The two non-verification arms are unchanged: neither claims verification, so neither
    was overstating anything. Their coverage is still recorded and still rendered — the
    export says how many entries went unchecked — it just does not need a new label to
    stop being false.
    """
    if not rt.verify_sources:
        return (
            "consensus-reviewed with retrieved sourcing"
            if rt.search_enabled
            else "consensus-reviewed with in-artifact sourcing (no external retrieval)"
        )
    if not observed:
        # Verification was on and no evidence lens ever completed on this draft. Saying
        # so is the honest answer; falling back to the old label would make the absence
        # of a measurement read as a passing one.
        return (
            "consensus-reviewed; source verification was enabled but no coverage was "
            "recorded for the shipped draft"
        )
    return f"consensus-reviewed — {fetch.coverage_sentence(observed)}"


def _finalize(state: State, rt: Runtime) -> dict:
    status = state.get("terminal_status") or "aborted"
    board = state.get("scoreboard", [])

    if status in ("accepted", "converged_unconfirmed"):
        text = state.get("report", "")
        chosen_round = state.get("round", 0)
        defects = state.get("defects", [])
    elif board:
        # Never ship the last draft just because it is last — ship the best-scoring one,
        # scored on each artifact's most-critiqued triage rather than its first.
        from .controller import best_scoring_index, latest_scores_per_artifact

        rows = latest_scores_per_artifact(board)
        idx = best_scoring_index([(b["blocking"], b["major"], b["minor"]) for b in rows])
        text = rows[idx]["report"]
        chosen_round = rows[idx]["round"]
        # The defects that annotate the shipped report must be the ones raised against
        # *it*, not against whatever draft the loop stopped on — on a non-accepted
        # terminal those differ (issue #93). The chosen row carries its own defect set;
        # a row written before this field existed has none, and the export keys on hash
        # and says so rather than substituting the terminal round's.
        defects = rows[idx].get("defects", [])
    else:
        text = state.get("report", "")
        chosen_round = state.get("round", 0)
        defects = state.get("defects", [])

    artifact_hash = report_mod.artifact_hash(text) if text else None
    # Stamp each outstanding defect with the artifact it belongs to, so an export can
    # filter to the shipped draft exactly as `_reviewers` filters the clean records —
    # one keying discipline for both annotation surfaces (issue #93).
    outstanding = [{**d, "artifact_hash": artifact_hash} for d in defects]

    # Keyed on the *shipped* artifact, for the same reason the defect list is: a
    # non-accepted terminal can ship an earlier round, and that round's bibliography is
    # the one the reader is holding (D-observed-source-coverage). No entry means no
    # evidence lens ever ran on this draft, which is reported as "not recorded" rather
    # than as zero coverage or as none needed.
    observed = (state.get("source_coverage") or {}).get(artifact_hash or "")

    view = state.get("view", {})
    summary = {
        "run_id": state["run_id"],
        "terminal_status": status,
        "rounds": state.get("round", 0),
        "chosen_round": chosen_round,
        "artifact_hash": artifact_hash,
        "final_view": view,
        "clean_records": state.get("clean_records", []),
        "outstanding_defects": outstanding,
        "source_coverage": observed,
        "warnings": state.get("warnings", []),
        "note": Decision.model_validate(state["decision"]).note if state.get("decision") else "",
        # The build that *finalized* this run. A resume may legitimately cross a deploy —
        # `_run_fingerprint` deliberately does not pin the build — so this names one build,
        # not all of them. The `startup` events in events.jsonl are the full list
        # (docs/run-provenance.md).
        "build": build_identity().as_dict(),
        "label": _sourcing_label(rt, observed),
    }
    rt.store.final(text, summary)
    rt.store.event("finalize", **{k: v for k, v in summary.items() if k != "final_view"})
    return {"terminal_status": status, "report": text}


# ----------------------------------------------------------------------- graph


def build_graph(rt: Runtime):
    graph = StateGraph(State)
    graph.add_node("intake", lambda s: _intake(s, rt))
    graph.add_node("generate", lambda s: _generate(s, rt))
    graph.add_node("adjudicate", lambda s: _adjudicate(s, rt))
    graph.add_node("critique", lambda s: _critique(s, rt))
    graph.add_node("triage", lambda s: _triage(s, rt))
    graph.add_node("orchestrate", lambda s: _orchestrate(s, rt))
    graph.add_node("control", lambda s: _control(s, rt))
    graph.add_node("finalize", lambda s: _finalize(s, rt))

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake", _route_intake, {"generate": "generate", "critique": "critique"}
    )
    # A dead generator still terminates *through* the controller, so the run gets a
    # recorded rule-1 decision and a normal audit trail rather than a silent exit.
    # The adjudicate node sits on the one-way generate→critique edge (D-writer-disputes): it
    # introduces no new cycle, and a shutdown between the two nodes resumes here
    # with `pending_disputes` intact.
    graph.add_conditional_edges(
        "generate",
        lambda s: "control" if s.get("fatal") else "adjudicate",
        {"adjudicate": "adjudicate", "control": "control"},
    )
    graph.add_edge("adjudicate", "critique")
    graph.add_edge("critique", "triage")
    graph.add_edge("triage", "orchestrate")
    graph.add_edge("orchestrate", "control")
    graph.add_conditional_edges(
        "control",
        _route_control,
        {"generate": "generate", "critique": "critique", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph


def _recursion_limit(config: Config) -> int:
    """Size the graph's step budget from the *loop's* own bounded transitions.

    A flat limit can bite before the controller's budgets do: rule 2 and rule 8 each
    re-enter critique → triage → orchestrate → control without generating, so a
    small `hard_cap` with large retry budgets could hit LangGraph's ceiling and raise
    instead of terminating at rule 3 or 11.
    """
    b = config.budgets
    laps = b.hard_cap + b.polish_cap + b.critique_attempts + b.confirmation_attempts + 2
    return max(100, laps * 6 + 10)


def _run_fingerprint(config: Config, question: str, seed: str | None) -> str:
    """What a resumed run must still be: the same question, the same seed, the same
    roster and the same budgets. Resuming a checkpoint under different inputs would
    answer a question nobody asked."""
    import hashlib

    payload = json.dumps(
        {
            "question": question,
            "seed_hash": report_mod.artifact_hash(seed) if seed else None,
            "roster": config.roster.model_dump(),
            "budgets": config.budgets.model_dump(),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ResumeMismatch(RuntimeError):
    """The stored run was started with different inputs than this invocation."""


class GracefulStop(RuntimeError):
    """The process was asked to shut down and the run stopped at a node boundary.

    Not a failure: the checkpoint is durable and the run is resumable from exactly
    where it paused. Carries the run id so callers can say how to pick it back up.
    """

    def __init__(self, message: str, run_id: str) -> None:
        super().__init__(message)
        self.run_id = run_id


def _checkpointer(rt: Runtime):
    """A per-run SQLite checkpoint next to the audit trail. Best-effort: an
    unavailable checkpointer costs resumability, never correctness."""
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:  # pragma: no cover - optional dependency
        log.warning("langgraph-checkpoint-sqlite is unavailable; this run is not resumable")
        return None
    conn = sqlite3.connect(rt.store.dir / "state.sqlite", check_same_thread=False)
    return SqliteSaver(conn)


def run(
    config: Config,
    question: str,
    seed: str | None = None,
    run_id: str | None = None,
    checkpointer: Any | None = None,
    client: LLMClient | None = None,
    stop: threading.Event | None = None,
    seed_format: str | None = None,
    seed_source: str | None = None,
    seed_warnings: list[str] | None = None,
    owner: str | None = None,
) -> dict:
    """`seed` is markdown. Callers holding a PDF, a .docx or a URL convert it first
    with `ingest`, at the edge, and pass the provenance it returns through the
    `seed_*` parameters.

    `owner` is the identity this run belongs to, and is what makes a CLI run visible
    in the web interface. It stays out of `_run_fingerprint` on purpose: the
    fingerprint guards against a run resuming under changed *inputs*, and who owns a
    run is not one — attributing an existing run must never cost it its checkpoint.
    """
    rt = build_runtime(config, run_id, client)
    if owner:
        rt.store.owner(owner)
    checkpointer = checkpointer if checkpointer is not None else _checkpointer(rt)
    compiled = build_graph(rt).compile(checkpointer=checkpointer)
    invoke_config = {
        "recursion_limit": _recursion_limit(config),
        "configurable": {"thread_id": rt.store.run_id},
    }

    # Resumability is the point of the checkpointer: this is a slow local-model
    # system, and losing an hour of critique to a dropped connection is the failure
    # mode that matters. An unfinished thread continues from its last completed node;
    # a fresh one starts at intake.
    #
    # `config`, deliberately, and never `rt.config`: a roster degraded by an unreachable
    # provider (D-degraded-roster) is a property of this attempt, not of the run's
    # identity. Fingerprinting what the attempt settled for would make the *recovery*
    # look like changed inputs — the run would resume fine while the outage lasted and
    # then hit `ResumeMismatch` the moment the provider came back, which is the one
    # moment it should have been able to finish properly.
    fingerprint = _run_fingerprint(config, question, seed)
    initial: State | None = {
        "run_id": rt.store.run_id,
        "question": question,
        "seed": seed,
        "seed_format": seed_format,
        "seed_source": seed_source,
        "seed_warnings": list(seed_warnings or []),
        "fingerprint": fingerprint,
    }
    if checkpointer is not None:
        snapshot = compiled.get_state(invoke_config)
        stored = snapshot.values.get("fingerprint")
        if stored and stored != fingerprint:
            raise ResumeMismatch(
                f"run '{rt.store.run_id}' was started with a different question, seed, "
                f"roster or budget set; refusing to resume it under new inputs"
            )
        if snapshot.next:
            log.info("resuming run %s at %s", rt.store.run_id, snapshot.next)
            rt.store.event("resume", resumed_at=list(snapshot.next))
            initial = None
        elif snapshot.values.get("terminal_status"):
            log.info("run %s already terminated", rt.store.run_id)
            return {**snapshot.values, "run_dir": str(rt.store.dir), "run_id": rt.store.run_id}

    final = _drive(compiled, initial, invoke_config, rt, stop)
    final["run_dir"] = str(rt.store.dir)
    final["run_id"] = rt.store.run_id
    return final


def _drive(compiled: Any, initial: State | None, invoke_config: dict, rt: Runtime,
           stop: threading.Event | None) -> dict:
    """Run the graph, stopping at a node boundary if asked to shut down.

    This is `invoke()` unrolled. Streaming is the only way to observe "a node just
    completed and its result is checkpointed", which is the one instant where stopping
    is free — the node's model calls are paid for and persisted, and the resume picks up
    at the next node. Stopping anywhere else either discards work already paid for or
    persists a half-finished node as though it were complete.

    `durability="sync"` is deliberate but not a bug fix. The default is `"async"`, which
    hands the checkpoint write to a background executor awaited as the *next* step runs;
    closing the stream cleanly still flushes it, because the executor is shut down as the
    loop's ExitStack unwinds. Both modes were measured here and both keep the work.
    What `"sync"` removes is the dependency on that teardown ordering — the checkpoint is
    already durable when we observe the node completing, rather than durable because
    something else cleans up correctly afterwards. At minutes per node, an inline sqlite
    write is free, so the weaker guarantee buys nothing.

    Deliberately not finer-grained: `_critique` fans out across lenses, and
    short-circuiting the ones that have not started yet would checkpoint a partially
    critiqued round as if it were whole.
    """
    final: dict | None = None
    stream = compiled.stream(initial, config=invoke_config, stream_mode="values",
                             durability="sync")
    with closing(stream):
        for values in stream:
            final = values
            if stop is not None and stop.is_set():
                pending = list(compiled.get_state(invoke_config).next)
                log.info("pausing run %s at %s for shutdown", rt.store.run_id, pending)
                rt.store.event("pause", reason="shutdown", next=pending)
                raise GracefulStop(
                    f"run '{rt.store.run_id}' paused for shutdown before {pending or 'the end'}; "
                    f"it resumes from this point",
                    rt.store.run_id,
                )

    if final is None:  # pragma: no cover - stream always yields at least the input
        raise RuntimeError(f"run '{rt.store.run_id}' produced no state")
    return final
