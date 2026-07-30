"""Question refinement (D-question-refinement) at the web edge: the `/refine` route, the `/runs`
provenance claim, the audit record, and the rendering guardrails.

No network, fully offline: every test drives `RefinementService`'s public surface
through a hand-written stub rather than a real `LLMClient`, exactly like `fakes.py`
does for the LLM proxy elsewhere in this suite.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import pytest
from conftest import WEB_IDENTITY, web_client

from reasonable_answer.web.app import create_app
from reasonable_answer.web.refine import Offer, Refinement, Suggestion
from reasonable_answer.web.render import REFINE_JS, render_index
from reasonable_answer.web.worker import RateLimiter, RunWorker


class StubRefiner:
    """A hand-written stand-in for `RefinementService`'s public surface (`svc.enabled`,
    `svc.limiter`, `svc.suggest`, `svc.resolve`, `svc.start`/`shutdown`) -- exactly what
    `web/app.py` consumes, nothing else. `suggest_fn`/`resolve_fn` let each test pick
    canned behavior; calls are recorded so tests can assert what did or did not fire.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        suggest_fn: Callable[[str], Offer] | None = None,
        resolve_fn: Callable[[str | None, str | None, str], Refinement | None] | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.enabled = enabled
        self._suggest_fn = suggest_fn or (lambda q: Offer(offer_id=""))
        # Mirrors the real contract's cheapest case: no claim, nothing to resolve.
        self._resolve_fn = resolve_fn or (lambda offer_id, selected, q: None)
        self.limiter = limiter or RateLimiter(100, 60.0)
        self.suggest_calls: list[str] = []
        self.resolve_calls: list[tuple] = []

    def start(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def suggest(self, question: str) -> Offer:
        self.suggest_calls.append(question)
        return self._suggest_fn(question)

    def resolve(self, offer_id, selected, submitted_question):
        self.resolve_calls.append((offer_id, selected, submitted_question))
        return self._resolve_fn(offer_id, selected, submitted_question)


def _make_app(config, refiner, runner=None):
    worker = RunWorker(config, max_concurrent=1, runner=runner or (lambda *a, **k: None))
    app = create_app(config, worker=worker, refiner=refiner)
    return app, worker


def _wait_idle(worker: RunWorker, run_id: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while worker.status(run_id) and time.time() < deadline:
        time.sleep(0.02)


# ------------------------------------------------------------------- POST /refine


@pytest.mark.parametrize("fetch_site", ["cross-site", "none"])
def test_refine_cross_site_is_refused(config, fetch_site):
    app, worker = _make_app(config, StubRefiner(enabled=True))
    try:
        with web_client(app) as c:
            resp = c.post(
                "/refine",
                data={"question": "Does this get refused cross-site?"},
                headers={"sec-fetch-site": fetch_site},
            )
            assert resp.status_code == 403
    finally:
        worker.shutdown()


def test_refine_is_404_when_disabled(config):
    app, worker = _make_app(config, StubRefiner(enabled=False))
    try:
        with web_client(app) as c:
            resp = c.post("/refine", data={"question": "Anything at all?"})
            assert resp.status_code == 404
    finally:
        worker.shutdown()


def test_refine_oversized_question_is_rejected(config):
    app, worker = _make_app(config, StubRefiner(enabled=True))
    try:
        with web_client(app) as c:
            huge = "x" * (config.max_question_chars + 1)
            resp = c.post("/refine", data={"question": huge})
            assert resp.status_code == 400
    finally:
        worker.shutdown()


def test_refine_rate_limit_exceeded_degrades_to_an_empty_200(config):
    limiter = RateLimiter(1, 60.0, clock=lambda: 0.0)
    # Consume the one slot under the signed-in identity before the request arrives -- the
    # limiter key is the authenticated viewer now (D-identity-header), not a shared "global" bucket.
    limiter.check_and_record(WEB_IDENTITY)
    refiner = StubRefiner(enabled=True, limiter=limiter)
    app, worker = _make_app(config, refiner)
    try:
        with web_client(app) as c:
            resp = c.post("/refine", data={"question": "A question nobody gets to see?"})
            assert resp.status_code == 200
            assert resp.json() == {"offer_id": "", "suggestions": []}
            assert resp.headers["cache-control"] == "no-store"
        # Shed at the limiter -- the (expensive) LLM-backed call must never fire.
        assert refiner.suggest_calls == []
    finally:
        worker.shutdown()


def test_refine_happy_path_returns_the_offer_as_json_uncached(config):
    offer = Offer(
        offer_id="o" * 32,
        suggestions=(
            Suggestion(
                transform="check_the_premise_first",
                label="check the premise first",
                question="Is it actually illegal to relocate opossums in Texas?",
            ),
        ),
    )
    refiner = StubRefiner(enabled=True, suggest_fn=lambda q: offer)
    app, worker = _make_app(config, refiner)
    try:
        with web_client(app) as c:
            resp = c.post(
                "/refine", data={"question": "Why is it illegal to move an opossum in tx?"}
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("application/json")
            assert resp.headers["cache-control"] == "no-store"
            assert resp.json() == offer.as_json()
        assert refiner.suggest_calls == ["Why is it illegal to move an opossum in tx?"]
    finally:
        worker.shutdown()


def test_refine_response_carries_hostile_suggestion_text_inertly(config):
    """The route never renders HTML -- it returns JSON -- so an adversarial payload in a
    suggestion's label/question has no markup context to break out of. This is the
    server-side half of the XSS guardrail; the client-side half (textContent-only DOM
    construction) is covered by the inline-script assertions below."""
    hostile_label = "<img src=x onerror=alert(1)>"
    hostile_question = '<script>alert(1)</script> is that so?'
    hostile_suggestion = Suggestion(
        transform="ask_whats_answerable", label=hostile_label, question=hostile_question
    )
    offer = Offer(offer_id="o" * 32, suggestions=(hostile_suggestion,))
    refiner = StubRefiner(enabled=True, suggest_fn=lambda q: offer)
    app, worker = _make_app(config, refiner)
    try:
        with web_client(app) as c:
            resp = c.post("/refine", data={"question": "Is honesty better than niceness?"})
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("application/json")
            body = resp.json()
            # Round-tripped byte-for-byte as JSON string content, not interpreted as markup.
            assert body["suggestions"][0]["label"] == hostile_label
            assert body["suggestions"][0]["question"] == hostile_question
    finally:
        worker.shutdown()


# --------------------------------------------------------------- POST /runs claims


def test_runs_with_a_verified_offer_claim_writes_refinement_json_and_event(config):
    suggestion = Suggestion(
        transform="check_the_premise_first",
        label="check the premise first",
        question="Is it actually illegal to relocate opossums in Texas?",
    )
    refinement = Refinement(
        provenance="verified",
        offer_id="v" * 32,
        transform=suggestion.transform,
        selected_index=0,
        question_at_offer="Why is it illegal to move an opossum in tx?",
        suggestions=(suggestion,),
        question_sha256="a" * 64,
        original_sha256="b" * 64,
    )
    refiner = StubRefiner(enabled=True, resolve_fn=lambda o, s, q: refinement)
    app, worker = _make_app(config, refiner)
    try:
        with web_client(app) as c:
            resp = c.post(
                "/runs",
                data={
                    "question": suggestion.question,
                    "refine_offer_id": refinement.offer_id,
                    "refine_selected": "0",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            run_id = resp.headers["location"].rsplit("/", 1)[-1]
        _wait_idle(worker, run_id)

        assert refiner.resolve_calls == [(refinement.offer_id, "0", suggestion.question)]

        payload = json.loads(
            (config.runs_dir / run_id / "refinements" / "refinement.json").read_text()
        )
        assert payload["provenance"] == "verified"
        assert payload["offer_id"] == refinement.offer_id
        assert payload["question_at_offer"] == refinement.question_at_offer

        events_text = (config.runs_dir / run_id / "events.jsonl").read_text()
        refinement_lines = [
            line for line in events_text.splitlines() if json.loads(line)["kind"] == "refinement"
        ]
        assert len(refinement_lines) == 1
        line = refinement_lines[0]
        event = json.loads(line)
        assert event["provenance"] == "verified"
        assert event["question_sha256"] == "a" * 64
        assert event["original_sha256"] == "b" * 64
        # Non-content signal only: no question or suggestion text on the raw line.
        assert suggestion.question not in line
        assert refinement.question_at_offer not in line
        assert suggestion.label not in line
    finally:
        worker.shutdown()


def test_runs_with_an_unverifiable_offer_claim_still_starts_the_run(config):
    """Forged, expired, or simply unknown -- any offer id the service cannot verify
    downgrades to `unverified` provenance, but the run proceeds normally either way."""
    refiner = StubRefiner(
        enabled=True,
        resolve_fn=lambda o, s, q: Refinement(
            provenance="unverified",
            offer_id=o or "",
            transform=None,
            selected_index=None,
            question_at_offer=None,
            suggestions=(),
            question_sha256="c" * 64,
            original_sha256=None,
        ),
    )
    app, worker = _make_app(config, refiner)
    try:
        with web_client(app) as c:
            resp = c.post(
                "/runs",
                data={
                    "question": "A question with no matching offer on record?",
                    "refine_offer_id": "z" * 32,  # well-formed, but unknown to the service
                    "refine_selected": "0",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303  # the run still starts
            run_id = resp.headers["location"].rsplit("/", 1)[-1]
        _wait_idle(worker, run_id)

        payload = json.loads(
            (config.runs_dir / run_id / "refinements" / "refinement.json").read_text()
        )
        assert payload["provenance"] == "unverified"
    finally:
        worker.shutdown()


def test_runs_with_a_malformed_offer_id_never_persists_the_raw_bytes(config):
    """A malformed/oversized claim must never fail the submission, and the raw bytes the
    client sent must never be written anywhere -- the service's own contract is to
    short-circuit to a constant `unverified("")` before any lookup; this test verifies
    the web/worker/store wiring around that contract doesn't leak the raw field anyway
    (e.g. by echoing it into the audit record instead of the service's sanitized id)."""
    garbage = "not-a-real-offer-id;" + ("x" * 5000)
    refiner = StubRefiner(
        enabled=True,
        resolve_fn=lambda o, s, q: Refinement(
            provenance="unverified",
            offer_id="",  # the service's constant for a malformed claim
            transform=None,
            selected_index=None,
            question_at_offer=None,
            suggestions=(),
            question_sha256="d" * 64,
            original_sha256=None,
        ),
    )
    app, worker = _make_app(config, refiner)
    try:
        with web_client(app) as c:
            resp = c.post(
                "/runs",
                data={
                    "question": "Does a malformed claim still let the run start?",
                    "refine_offer_id": garbage,
                    "refine_selected": "0",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            run_id = resp.headers["location"].rsplit("/", 1)[-1]
        _wait_idle(worker, run_id)

        events_text = (config.runs_dir / run_id / "events.jsonl").read_text()
        refinement_json = (config.runs_dir / run_id / "refinements" / "refinement.json").read_text()
        assert garbage not in events_text
        assert garbage not in refinement_json
    finally:
        worker.shutdown()


def test_runs_with_no_refinement_fields_writes_no_refinement_record(config):
    """Byte-identical audit trail to today: nothing claimed, nothing recorded."""
    refiner = StubRefiner(enabled=True)  # default resolve_fn returns None for no claim
    app, worker = _make_app(config, refiner)
    try:
        with web_client(app) as c:
            resp = c.post(
                "/runs", data={"question": "A perfectly ordinary question?"}, follow_redirects=False
            )
            assert resp.status_code == 303
            run_id = resp.headers["location"].rsplit("/", 1)[-1]
        _wait_idle(worker, run_id)

        assert not (config.runs_dir / run_id / "refinements" / "refinement.json").exists()
        events_text = (config.runs_dir / run_id / "events.jsonl").read_text()
        assert '"kind": "refinement"' not in events_text
    finally:
        worker.shutdown()


def test_resuming_a_run_does_not_rewrite_the_refinement_record(config):
    """`resume()`/`recover()` replay work already owed and on disk; the refinement
    record was written once, at submit time, and must not be touched again."""
    suggestion = Suggestion(transform="ask_whats_answerable", label="ask what's answerable", question="Q?")
    refinement = Refinement(
        provenance="verified",
        offer_id="r" * 32,
        transform=suggestion.transform,
        selected_index=0,
        question_at_offer="Original Q?",
        suggestions=(suggestion,),
        question_sha256="e" * 64,
        original_sha256="f" * 64,
    )
    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    try:
        run_id = worker.submit("Q?", identity="viewer@example.com", refinement=refinement)
        _wait_idle(worker, run_id)

        refinement_path = config.runs_dir / run_id / "refinements" / "refinement.json"
        events_path = config.runs_dir / run_id / "events.jsonl"
        before_refinement = refinement_path.read_text()
        before_events = events_path.read_text()

        worker.resume(run_id, "Q?")
        _wait_idle(worker, run_id)

        assert refinement_path.read_text() == before_refinement
        events_after = events_path.read_text()
        assert events_after.startswith(before_events)  # only appended to, never rewritten
        assert sum(json.loads(line)["kind"] == "refinement" for line in events_after.splitlines()) == 1
    finally:
        worker.shutdown()


# --------------------------------------------------------------------- rendering


def test_disabled_refine_index_has_no_refine_markup_and_matches_a_bare_config(config):
    baseline = config.model_copy()  # refine.enabled is False by default on both
    assert config.refine.enabled is False
    page_a = render_index([], queue_depth=0, config=config)
    page_b = render_index([], queue_depth=0, config=baseline)
    assert page_a == page_b
    assert "refine" not in page_a.lower()


def test_enabled_refine_index_has_hidden_fields_chips_container_and_script(config):
    enabled = config.model_copy(update={"refine": config.refine.model_copy(update={"enabled": True})})
    page = render_index([], queue_depth=0, config=enabled)
    assert 'id="refine_offer_id"' in page
    assert 'name="refine_offer_id"' in page
    assert 'id="refine_selected"' in page
    assert 'name="refine_selected"' in page
    assert 'id="refine-chips"' in page
    assert "refine-chips" in page  # the CSS class
    # A distinctive symbol from the inline script, confirming it actually landed on the
    # page (and not just the markup) when the feature is on.
    assert "distanceAtLeast" in page


def test_refine_js_builds_suggestion_dom_with_textcontent_only(config):
    """The one place this DOM-free suite can cheaply pin the XSS guardrail: the inline
    script must never assign `innerHTML` (the CSP's `script-src 'unsafe-inline'` would
    make that exploitable for model- or user-derived text), and must use `textContent`
    to place suggestion/label text into the chip nodes."""
    assert "innerHTML" not in REFINE_JS
    assert "textContent" in REFINE_JS
    assert REFINE_JS.count("textContent") >= 2  # label and question both go through it


def test_refine_chip_buttons_are_type_button_so_they_never_submit_the_form():
    assert "btn.type = 'button'" in REFINE_JS
