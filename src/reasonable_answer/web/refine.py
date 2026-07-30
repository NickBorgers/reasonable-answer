"""Pre-run question refinement (D-question-refinement, docs/question-refinement.md).

`RefinementService` is the whole engine: the LLM call, deterministic validation, the
TTL cache, offer records, and the concurrency semaphore. It lives entirely at the web
edge — the route (`web/app.py`, added separately) stays thin, and the graph never
knows refinement exists. Constructed with injected dependencies (an `LLMClient`, a
clock) so it runs fully offline in tests, exactly like `web.worker.RateLimiter`.

Two honesty notes worth keeping in mind while reading this file (both from the
design doc, restated here because the code enforces them):

* The provider-level `timeout` passed to `LLMClient.structured` bounds **client
  occupancy** — the connection closes and this service's bookkeeping moves on — not
  upstream generation. Nothing here can prove the backend actually stopped computing
  on disconnect; `orphan_linger_seconds` is a best-effort damper on the resulting
  orphaned permits, never a guarantee.
* `concurrency` is a hard ceiling on *this service's* live calls, acquired
  non-blocking: saturation sheds load immediately rather than queuing. It says
  nothing about contention with run traffic on a shared LiteLLM proxy — that
  isolation is a deployment property (a dedicated alias/backend), not something this
  module can provide by itself.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..config import Config, ConfigError
from ..llm import LLMClient
from ..prompts import refine_system, refine_user
from ..schemas import (
    MAX_REFINE_LABEL,
    MAX_REFINE_QUESTION,
    RefinementSuggestion,
    RefinementSuggestions,
)
from .worker import RateLimiter

log = logging.getLogger(__name__)

#: Bump whenever `refine_system`'s text or `RefinementSuggestions` changes. The
#: cache key includes this, so a stale suggestion set produced under an old prompt
#: or schema can never outlive the prompt/schema that produced it.
PROMPT_VERSION = 2

#: Exactly `secrets.token_urlsafe(24)`'s output shape (24 bytes -> 32 base64url
#: chars, no padding since 24 is a multiple of 3). Validated before any map lookup so
#: a malformed or oversized claim never reaches the offer store at all.
_OFFER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")

_WHITESPACE_RE = re.compile(r"\s+")

#: Chips are one line; a control character (tabs/newlines/CR included) in a label or
#: question is grounds for silently dropping that entry.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

#: Aliased so tests can substitute a fake timer without patching the `threading`
#: module globally (which would affect every other thread in the process).
_Timer = threading.Timer


# --------------------------------------------------------------------- data model


@dataclass(frozen=True)
class Suggestion:
    """One reframe chip that survived deterministic validation."""

    transform: str
    label: str
    question: str


@dataclass(frozen=True)
class Offer:
    """The result of one `suggest()` call. `offer_id == ""` means there is nothing
    to show — the well-posed-question case, and every failure mode degrades to it."""

    offer_id: str
    suggestions: tuple[Suggestion, ...] = ()

    def as_json(self) -> dict:
        return {
            "offer_id": self.offer_id,
            "suggestions": [
                {"transform": s.transform, "label": s.label, "question": s.question}
                for s in self.suggestions
            ],
        }


_EMPTY_OFFER = Offer(offer_id="")


@dataclass(frozen=True)
class Refinement:
    """The result of `resolve()`: what actually happened at submit time, derived
    from the server's own offer record — never from client-claimed content."""

    provenance: str  # "verified" | "unverified"
    offer_id: str  # "" when the claim was malformed — never the client's raw bytes
    transform: str | None
    selected_index: int | None
    question_at_offer: str | None  # None unless verified
    suggestions: tuple[Suggestion, ...]  # () unless verified
    question_sha256: str
    original_sha256: str | None

    def content(self) -> dict:
        """The full `runs/<run_id>/refinements/refinement.json` payload."""
        return {
            "provenance": self.provenance,
            "offer_id": self.offer_id,
            "transform": self.transform,
            "selected_index": self.selected_index,
            "question_at_offer": self.question_at_offer,
            "suggestions": [
                {"transform": s.transform, "label": s.label, "question": s.question}
                for s in self.suggestions
            ],
            "question_sha256": self.question_sha256,
            "original_sha256": self.original_sha256,
        }

    def event_fields(self) -> dict:
        """Non-content signal only, for `events.jsonl` (which survives purges):
        hashes and bookkeeping, never question or suggestion text."""
        return {
            "offer_id": self.offer_id,
            "transform": self.transform,
            "selected_index": self.selected_index,
            "question_sha256": self.question_sha256,
            "original_sha256": self.original_sha256,
            "provenance": self.provenance,
        }


# ------------------------------------------------------------------- internal state


@dataclass
class _CacheEntry:
    suggestions: tuple[Suggestion, ...]
    expires_at: float


@dataclass
class _OfferRecord:
    question_at_offer: str
    suggestions: tuple[Suggestion, ...]
    expires_at: float


class _Pending:
    """One in-flight computation for a cache key. The owner (first caller in) runs
    the actual completion; every later caller for the same key waits on `event` and
    reads back the same result — one completion serves all of them."""

    __slots__ = ("event", "suggestions")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.suggestions: tuple[Suggestion, ...] = ()


def _normalize(text: str) -> str:
    """Trimmed + runs of whitespace collapsed — the cache-key and dedup basis."""
    return _WHITESPACE_RE.sub(" ", text.strip())


def _dedup_key(text: str) -> str:
    return _normalize(text).casefold()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_timeout(exc: BaseException) -> bool:
    """Best-effort classification driving the orphan-linger policy.

    A real deployment's provider-level timeout surfaces as `openai.APITimeoutError`,
    which `LLMClient._create` wraps into a `ModelCallError` chained via `__cause__`
    (`raise ... from last`). Walk that chain by name (no hard `openai` import here)
    and fall back to the message text, so a test double can signal a timeout with a
    plain `TimeoutError` or a message containing the word, without depending on the
    openai package's exception hierarchy.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError) or type(current).__name__ == "APITimeoutError":
            return True
        text = str(current).lower()
        if "timeout" in text or "timed out" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _filter_suggestions(
    raw: Sequence[RefinementSuggestion],
    *,
    submitted_question: str,
    enabled_transforms: Sequence[str],
    max_suggestions: int,
) -> tuple[Suggestion, ...]:
    """Deterministic post-validation, on top of (never a substitute for) schema
    validation (docs/question-refinement.md's enforced guardrail 1). An
    in-schema-bounds entry can still fail one of these and is silently dropped —
    one bad chip must never cost the user the other two, so this never raises and
    never fails the whole batch.

    Order matters: entries are considered in model order, invalid ones are skipped
    (not counted against `max_suggestions`), and collection stops once
    `max_suggestions` valid entries have been kept.
    """
    enabled = set(enabled_transforms)
    seen = {_dedup_key(submitted_question)}
    out: list[Suggestion] = []
    for item in raw:
        if len(out) >= max_suggestions:
            break
        if item.transform not in enabled:
            continue
        label, question = item.label, item.question
        if not (1 <= len(label) <= MAX_REFINE_LABEL):
            continue
        if not (1 <= len(question) <= MAX_REFINE_QUESTION):
            continue
        if not question.endswith("?"):
            continue
        if _CONTROL_CHAR_RE.search(label) or _CONTROL_CHAR_RE.search(question):
            continue
        key = _dedup_key(question)
        if key in seen:
            continue
        seen.add(key)
        out.append(Suggestion(transform=item.transform, label=label, question=question))
    return tuple(out)


# ------------------------------------------------------------------------- service


class RefinementService:
    """Owns the LLM call, validation, cache, offer records, and concurrency
    semaphore for `POST /refine`. See the module docstring for the two guarantees
    this class does and does not make.
    """

    def __init__(
        self,
        config: Config,
        client: LLMClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._refine = config.refine
        self._client = client
        self._clock = clock
        self._alias = self._refine.effective_alias(config.roster)
        # Sorted once so the cache key (and the prompt built from it) are stable
        # regardless of the set's iteration order.
        self._enabled_transforms: tuple[str, ...] = tuple(sorted(self._refine.enabled_transforms))
        self._system = refine_system(self._enabled_transforms)

        #: Built here so the route (part 2) has one to enforce with — this service
        #: does not rate-limit its own calls, only sheds them past `concurrency`.
        self.limiter = RateLimiter(self._refine.rate_max, self._refine.rate_window_seconds, clock=clock)

        self._semaphore = threading.Semaphore(self._refine.concurrency)
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple, _CacheEntry] = OrderedDict()
        self._pending: dict[tuple, _Pending] = {}
        self._offers: OrderedDict[str, _OfferRecord] = OrderedDict()
        #: Handles for orphan-linger timers, so `shutdown()` can cancel them.
        self._timers: set[threading.Timer] = set()

    @property
    def enabled(self) -> bool:
        return self._refine.enabled

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Startup hook, mirroring the roster's own identity resolution and
        structured-output probing (`graph.build_runtime`) — a bad or
        schema-incapable refine alias fails here, not on a user's first pause.

        No-op when refinement is disabled, matching the D-retrieval-opt-in/D-writer-disputes opt-in pattern.

        Caution for whoever wires this into `web/app.py` (part 2): the real
        `LLMClient.resolve_identities` *replaces* its whole identity map rather than
        merging into it, so calling this after some other component has already
        resolved the roster's aliases on the same client would blank those out
        again — harmless if every run re-resolves its own aliases via
        `graph.build_runtime` anyway (as it does today), but worth knowing before
        assuming the client's identity map is a durable, shared cache.
        """
        if not self.enabled:
            return
        if self._client is None:
            raise ConfigError("refine.enabled is true but RefinementService has no LLMClient")
        identities = self._client.resolve_identities([self._alias])
        self._client.probe_structured_output(self._alias)
        self._warn_if_unfit(identities[self._alias])

    def _warn_if_unfit(self, identity: str) -> None:
        """Warn — never block — when the refine model's cached audition verdict is
        `unfit` (D-refine-audition). Blocking would invert this feature's own doctrine: every
        refine failure degrades to silence, and a chip-suggester's fitness must not
        gate serving runs. Under `audition.enforce` the warning is the whole
        enforcement; auto-disabling refinement was rejected in D-refine-audition because a stale
        cache could silently turn a feature off."""
        if not self._config.audition.enforce:
            return
        # Local import: refine_audition imports this module for the filter, so a
        # module-level import here would be a cycle.
        from ..audition import Verdict
        from ..refine_audition import refine_cached_judgement

        judgement = refine_cached_judgement(
            self._config.audition.refine,
            identity,
            frozenset(self._enabled_transforms),
        )
        if judgement is not None and judgement.verdict is Verdict.UNFIT:
            log.warning(
                "refine model '%s' (%s) graded unfit on the refine audition: %s — "
                "refinement stays enabled (warn-only, D-refine-audition); re-roster refine.alias "
                "or re-measure with `ra audition-refine --force`",
                self._alias,
                identity,
                "; ".join(judgement.reasons),
            )

    def shutdown(self) -> None:
        """Cancel any pending orphan-linger timers. A timer that has already fired
        is unaffected (`Timer.cancel()` on a finished timer is a no-op); one still
        pending never releases its semaphore permit — acceptable at shutdown, where
        nothing will acquire it again."""
        with self._lock:
            timers = list(self._timers)
            self._timers.clear()
        for timer in timers:
            timer.cancel()

    # ------------------------------------------------------------------- suggest

    def suggest(self, question: str) -> Offer:
        """Never raises. Degrades to an empty `Offer` on every failure mode:
        disabled config, a blank question, a cold/rejected semaphore, a timeout, a
        `ModelCallError`, a `MalformedOutputError`, or any other exception."""
        try:
            return self._suggest(question)
        except Exception:  # belt-and-suspenders: this method's whole contract is
            # "never raises", so any bug in the machinery below still degrades to
            # silence rather than surfacing a 500 to the caller.
            log.exception("refine.suggest(): unexpected failure; degrading to no suggestions")
            return _EMPTY_OFFER

    def _suggest(self, question: str) -> Offer:
        if not self.enabled:
            return _EMPTY_OFFER
        normalized = _normalize(question)
        if not normalized:
            return _EMPTY_OFFER
        key = (
            normalized,
            PROMPT_VERSION,
            self._alias,
            self._refine.max_suggestions,
            self._enabled_transforms,
        )
        suggestions = self._get_or_compute(key, normalized)
        if not suggestions:
            return _EMPTY_OFFER
        return self._mint_offer(suggestions, question_at_offer=question.strip())

    def _get_or_compute(self, key: tuple, question: str) -> tuple[Suggestion, ...]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                if entry.expires_at > self._clock():
                    self._cache.move_to_end(key)
                    return entry.suggestions
                del self._cache[key]  # expired; falls through to recompute

            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = _Pending()
                self._pending[key] = pending

        assert pending is not None
        if not owner:
            # Coalesced miss: wait for the owner's completion instead of costing a
            # second one. A cache hit still mints a fresh offer record — that
            # happens in the caller, not here.
            pending.event.wait()
            return pending.suggestions

        # The owner must clear `_pending` and set the event on *every* exit path.
        # A waiter parked on `event.wait()` has no timeout and no other way out, so
        # an exception escaping here would hang that request thread for good — a
        # worse failure than the silence this whole module degrades to.
        try:
            result = self._compute(question)
        except Exception:
            log.exception("refine: computing suggestions failed unexpectedly")
            result = None
        with self._lock:
            self._pending.pop(key, None)
            if result is not None:
                # Validated successes are cached *including empty results* —
                # failures (result is None) are never cached, so a transient
                # timeout or saturation event does not get remembered as "nothing
                # to suggest" for the rest of the TTL.
                self._cache[key] = _CacheEntry(
                    suggestions=result, expires_at=self._clock() + self._refine.cache_ttl_seconds
                )
                self._cache.move_to_end(key)
                while len(self._cache) > self._refine.cache_entries:
                    self._cache.popitem(last=False)
            pending.suggestions = result if result is not None else ()
        pending.event.set()
        return pending.suggestions

    def _compute(self, question: str) -> tuple[Suggestion, ...] | None:
        """One real completion attempt. Returns `None` on any failure (never
        cached), else the deterministically-filtered suggestion tuple (possibly
        empty, and cacheable)."""
        if not self._semaphore.acquire(blocking=False):
            # Shed load, never queue: `concurrency` is a hard ceiling on live calls.
            return None
        timed_out = False
        try:
            result: RefinementSuggestions = self._client.structured(  # type: ignore[union-attr]
                self._alias,
                system=self._system,
                user=refine_user(question),
                schema=RefinementSuggestions,
                max_tokens=700,
                repair_retries=1,
                timeout=self._refine.timeout_seconds,
            )
        except Exception as exc:
            timed_out = _is_timeout(exc)
            log.info("refine suggest() call to %s failed: %s", self._alias, exc)
            return None
        else:
            return _filter_suggestions(
                result.suggestions,
                submitted_question=question,
                enabled_transforms=self._enabled_transforms,
                max_suggestions=self._refine.max_suggestions,
            )
        finally:
            self._release_permit(timed_out)

    def _release_permit(self, timed_out: bool) -> None:
        """Non-timeout outcomes release immediately. A timeout instead schedules
        the release `orphan_linger_seconds` later (inline when that is 0) — a
        best-effort damper on orphan accumulation, not a guarantee (module
        docstring)."""
        linger = self._refine.orphan_linger_seconds
        if not timed_out or linger <= 0:
            self._semaphore.release()
            return

        def _fire() -> None:
            self._semaphore.release()
            with self._lock:
                self._timers.discard(timer)

        timer = _Timer(linger, _fire)
        timer.daemon = True
        with self._lock:
            self._timers.add(timer)
        timer.start()

    def _mint_offer(self, suggestions: tuple[Suggestion, ...], *, question_at_offer: str) -> Offer:
        """Only non-empty results get an offer record — an empty result has no
        `offer_id` to validate anything against later."""
        offer_id = secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock:
            self._offers[offer_id] = _OfferRecord(
                question_at_offer=question_at_offer,
                suggestions=suggestions,
                expires_at=now + self._refine.offer_ttl_seconds,
            )
            self._offers.move_to_end(offer_id)
            while len(self._offers) > self._refine.offer_entries:
                self._offers.popitem(last=False)
        return Offer(offer_id=offer_id, suggestions=suggestions)

    # ------------------------------------------------------------------- resolve

    def resolve(
        self, offer_id: str | None, selected: str | None, submitted_question: str
    ) -> Refinement | None:
        """Validate a submit-time refinement claim against the server's own offer
        record. Returns `None` only when nothing was claimed at all (no offer id
        and no selection) — the caller then records nothing. Every other case
        returns a `Refinement`, `unverified` unless every check passes.
        """
        if not offer_id and not selected:
            return None

        submitted = submitted_question.strip()
        question_hash = _sha256(submitted)

        def unverified(recorded_offer_id: str) -> Refinement:
            return Refinement(
                provenance="unverified",
                offer_id=recorded_offer_id,
                transform=None,
                selected_index=None,
                question_at_offer=None,
                suggestions=(),
                question_sha256=question_hash,
                original_sha256=None,
            )

        # Format validated *before* any lookup — a malformed or oversized id must
        # never be persisted or echoed anywhere (docs/question-refinement.md).
        if not offer_id or not _OFFER_ID_RE.fullmatch(offer_id):
            return unverified("")

        selected_index: int | None = None
        if selected:
            try:
                selected_index = int(selected)
            except ValueError:
                selected_index = None

        with self._lock:
            record = self._offers.get(offer_id)
            live = record is not None and record.expires_at > self._clock()
            if live:
                self._offers.move_to_end(offer_id)

        if not live:
            return unverified(offer_id)
        assert record is not None
        if selected_index is None or not (0 <= selected_index < len(record.suggestions)):
            return unverified(offer_id)

        suggestion = record.suggestions[selected_index]
        if submitted != suggestion.question:
            # A valid offer id proves nothing about the question that actually ran.
            return unverified(offer_id)

        return Refinement(
            provenance="verified",
            offer_id=offer_id,
            transform=suggestion.transform,
            selected_index=selected_index,
            question_at_offer=record.question_at_offer,
            suggestions=record.suggestions,
            question_sha256=question_hash,
            original_sha256=_sha256(record.question_at_offer),
        )
