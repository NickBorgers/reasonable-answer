"""Reading runs back off disk.

There is deliberately no database here. ``RunStore`` already writes everything a
run produces — ``events.jsonl`` as the loop progresses, ``final.json`` when it
terminates — so the web layer is a *reader* of state the pipeline already keeps.
That also means the audit trail and the UI can never disagree.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..store import load_final
from ..taxonomy import LENSES

Status = Literal["queued", "running", "interrupted", "abandoned", "accepted",
                 "converged_unconfirmed", "exhausted_unresolved", "needs_human_review",
                 "aborted"]

TERMINAL_STATUSES = {
    "accepted",
    "converged_unconfirmed",
    "exhausted_unresolved",
    "needs_human_review",
    "aborted",
    # Not a verdict the controller ever issued — the run gave up before reaching one.
    # Terminal all the same, so the UI stops offering an automatic resume.
    "abandoned",
}

#: Events that mean a node actually ran. `startup`, `resume` and `queued` are written on
#: every attempt whether or not anything progressed, so they cannot count as progress —
#: see `consecutive_auto_resumes`.
PROGRESS_EVENTS = {
    "intake",
    "generate",
    "fetch_sources",
    "critique",
    "triage",
    "orchestrate",
    "control",
    "finalize",
}


@dataclass
class LensSnapshot:
    lens: str
    critic: str | None = None
    issues: int = 0
    failed: bool = False
    failure_reason: str | None = None


@dataclass
class RoundSnapshot:
    """One tick, as reconstructed from the event stream."""

    round: int
    writer: str | None = None
    artifact_hash: str | None = None
    polish: bool = False
    lenses: dict[str, LensSnapshot] = field(default_factory=dict)
    blocking: int = 0
    major: int = 0
    minor: int = 0
    cleared: dict[str, int] = field(default_factory=dict)
    rule: int | None = None
    action: str | None = None
    note: str = ""


@dataclass
class RunSummary:
    run_id: str
    status: Status
    question: str
    rounds: int
    started_at: float | None
    finished_at: float | None
    terminal_note: str = ""
    #: The identity that submitted the run, or None for a run that predates ownership
    #: or was started from the CLI without `--owner`. None means the web layer will
    #: not serve it at all (D-question-refinement); the run itself is untouched and still resumes.
    owner: str | None = None
    #: The build this run ran on — ``{"commit", "dirty", "source"}`` — or None for a run
    #: that predates stamping (D-run-build-stamp). Read from the final summary once the run
    #: has one, and from its latest stamped event before that, so a run still in flight is
    #: attributable too.
    build: dict[str, Any] | None = None

    @property
    def is_live(self) -> bool:
        return self.status in ("queued", "running")

    @property
    def ok(self) -> bool:
        return self.status in ("accepted", "converged_unconfirmed")


class Registry:
    """Filesystem-backed view of every run, live or finished."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)

    # ---------------------------------------------------------------- listing

    def list(
        self, active: dict[str, str] | None = None, owner: str | None = None
    ) -> list[RunSummary]:
        """Every run, newest first — or just `owner`'s, when one is given.

        The filter is opt-in because the two callers want opposite things. The web
        index is a per-user view and always passes a viewer. `RunWorker.recover()`
        must not: an interrupted run is work already owed, and whether anyone can
        currently *see* it has no bearing on whether it should finish.
        """
        active = active or {}
        out = [self.summary(d.name, active) for d in self._run_dirs()]
        if owner is not None:
            out = [r for r in out if r.owner == owner]
        return sorted(out, key=lambda r: (r.started_at or 0), reverse=True)

    def _run_dirs(self) -> Iterator[Path]:
        if not self.runs_dir.exists():
            return iter(())
        return (d for d in self.runs_dir.iterdir() if d.is_dir() and (d / "events.jsonl").exists())

    def summary(self, run_id: str, active: dict[str, str] | None = None) -> RunSummary:
        active = active or {}
        events = list(self.events(run_id))
        final = self.final(run_id)

        started = events[0]["ts"] if events else None
        question = self.question(run_id)
        rounds = max((e.get("round", 0) for e in events if e.get("kind") == "control"), default=0)
        rounds = rounds or sum(1 for e in events if e.get("kind") == "generate")

        if final:
            status: Status = final.get("terminal_status", "aborted")
            finished = events[-1]["ts"] if events else None
            note = final.get("note", "")
        elif run_id in active:
            status = active[run_id]  # type: ignore[assignment]
            finished = None
            note = ""
        else:
            # No final.json and nobody working on it. How it stopped is recorded in the
            # last event, and the three cases are genuinely different: a run that was
            # never picked up, one parked deliberately by a deploy, and one that died.
            status, note = self._stopped_state(events)
            finished = None

        return RunSummary(
            run_id=run_id,
            status=status,
            question=question,
            rounds=rounds,
            started_at=started,
            finished_at=finished,
            terminal_note=note,
            owner=self.owner(run_id),
            build=self._build(final, events),
        )

    @staticmethod
    def _build(final: dict[str, Any] | None, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Which build to show for this run.

        The finalized summary wins, because that is the build that produced the report on
        offer. Before there is one, the latest stamped event answers for the attempt
        currently in flight. A run started before stamping existed has neither, and gets
        None rather than a fabricated answer.
        """
        if final and isinstance(final.get("build"), dict):
            return final["build"]
        for event in reversed(events):
            if isinstance(event.get("build"), dict):
                return event["build"]
        return None

    @staticmethod
    def _stopped_state(events: list[dict[str, Any]]) -> tuple[Status, str]:
        """Why a run without a final result is not running, read off its last event."""
        last = events[-1].get("kind") if events else None
        if last == "abandoned":
            reason = events[-1].get("reason", "")
            return "abandoned", f"gave up without a verdict: {reason}" if reason else "gave up"
        if last == "pause":
            return "interrupted", "paused for a restart; it resumes automatically"
        if last == "queued":
            return "queued", "waiting for a worker"
        if last == "deferred":
            # Distinguished from the crash below because the cause is outside the run and
            # outside the user's reach: nothing about this run is wrong, the models it
            # needs were unreachable (D-deferred-not-abandoned).
            return "interrupted", "the model roster was unreachable; it retries automatically"
        # Anything else means the process vanished mid-node. The checkpointer makes that
        # resumable rather than lost.
        return "interrupted", "no final result; the run can be resumed"

    def consecutive_auto_resumes(self, run_id: str) -> int:
        """How many automatic resumes in a row have failed to get anywhere.

        Counting *consecutive* attempts, rather than every attempt ever, is what keeps a
        restart storm from spending the budget. A container that boots, re-enqueues a
        run, and is redeployed thirty seconds later should not burn an attempt on a run
        it never touched — but one that genuinely made progress should start over from
        zero. Any progress event resets the count.

        A `deferred` attempt cancels itself rather than resetting the count
        (D-deferred-not-abandoned). The cap is there to bound a run that fails
        *deterministically*, and startup validation refusing because no model was
        reachable is not that run failing — every queued run fails there identically, so
        counting it would let one provider outage abandon the entire backlog. Cancelling
        rather than resetting keeps the cap honest across a mixed history: three real
        crashes still abandon a run even if an outage happened to fall between two of them.
        Deferrals are not thereby free: `consecutive_deferrals` bounds them separately.
        """
        count = 0
        for event in self.events(run_id):
            kind = event.get("kind")
            if kind == "queued" and event.get("auto"):
                count += 1
            elif kind == "deferred":
                count = max(0, count - 1)
            elif kind in PROGRESS_EVENTS:
                count = 0
        return count

    def consecutive_deferrals(self, run_id: str) -> int:
        """How many times in a row startup validation refused before this run could start.

        The second of the two budgets recovery spends, and it answers a different
        question from `consecutive_auto_resumes` (D-deferred-not-abandoned): not "is this
        run broken" but "is the deployment still coming back". Both are capped, because
        QP7 wants every loop capped and an uncapped deferral would let a permanently
        misconfigured roster accumulate runs that never reach a terminal state — the
        silent-backlog failure, which is worse than a terminal one because nobody is
        told. Progress resets it for the same reason it resets the other: a run that got
        somewhere is no longer waiting on the deployment.
        """
        count = 0
        for event in self.events(run_id):
            kind = event.get("kind")
            if kind == "deferred":
                count += 1
            elif kind in PROGRESS_EVENTS:
                count = 0
        return count

    # ------------------------------------------------------------------ parts

    def dir(self, run_id: str) -> Path:
        from ..store import safe_run_dir

        return safe_run_dir(self.runs_dir, run_id)

    def exists(self, run_id: str) -> bool:
        try:
            return (self.dir(run_id) / "events.jsonl").exists()
        except Exception:
            return False

    def events(self, run_id: str, offset: int = 0) -> Iterator[dict[str, Any]]:
        path = self.dir(run_id) / "events.jsonl"
        if not path.exists():
            return
        with path.open() as fh:
            for index, line in enumerate(fh):
                if index < offset:
                    continue
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a partially-flushed final line; it'll be read next poll

    def final(self, run_id: str) -> dict[str, Any] | None:
        path = self.dir(run_id) / "final.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None

    def final_strict(self, run_id: str) -> dict[str, Any] | None:
        """`final`, but corrupt and absent are told apart — raises `CorruptRun`.

        A *page* can degrade gracefully when the record will not parse: it shows the
        run as unfinished and the reader can see the directory for themselves. A
        durable export cannot. It has to state a verdict, and reading an unreadable
        record as an absent one would make it state `aborted` — a terminal status no
        controller rule produced. So the export paths ask this question strictly.
        """
        return load_final(self.dir(run_id) / "final.json")

    def report(self, run_id: str) -> str | None:
        path = self.dir(run_id) / "final.md"
        return path.read_text() if path.exists() else None

    def seed(self, run_id: str) -> str | None:
        """The converted markdown the run was seeded with, or None if it was not.

        Read back on resume because the graph fingerprints `question + seed + roster +
        budgets` and refuses a checkpoint whose inputs have drifted — a resume that
        forgets the seed looks exactly like someone changing the question mid-run.
        This is the same text `graph` hashed — `ingest` converts at the edge, before
        the store is written — so no re-fetch and no re-conversion is involved.
        """
        path = self.dir(run_id) / "seed.md"
        return path.read_text() if path.exists() else None

    def question(self, run_id: str) -> str:
        path = self.dir(run_id) / "question.txt"
        return path.read_text().strip() if path.exists() else "(question not recorded)"

    def owner(self, run_id: str) -> str | None:
        """The identity that submitted the run, or None if it has none.

        None is the honest answer for a run written before ownership existed or by
        `ra run` without `--owner`, and there is no safe identity to invent for it —
        so the web layer refuses to serve it rather than handing it to whoever asks.
        """
        path = self.dir(run_id) / "owner.txt"
        return path.read_text().strip() or None if path.exists() else None

    def drafts(self, run_id: str) -> list[tuple[str, str]]:
        """(filename, body) for every draft, oldest first."""
        reports = self.dir(run_id) / "reports"
        if not reports.exists():
            return []
        return [(p.name, p.read_text()) for p in sorted(reports.iterdir())]

    # -------------------------------------------------------------- timeline

    def timeline(self, run_id: str) -> list[RoundSnapshot]:
        """Fold the event stream into per-round snapshots — what the UI renders.

        Critique events carry the lens and the critic that drew it, which is the
        detail worth watching: it shows the roster actually rotating and no model
        reviewing its own draft.
        """
        rounds: dict[int, RoundSnapshot] = {}
        current = 0

        for event in self.events(run_id):
            kind = event.get("kind")
            if kind == "intake" and event.get("path") == "seed":
                current = 1
                fmt = event.get("seed_format")
                label = f"(seed: {fmt})" if fmt and fmt != "markdown" else "(seed)"
                rounds.setdefault(1, RoundSnapshot(round=1, writer=label))
                rounds[1].artifact_hash = event.get("artifact_hash")
            elif kind == "generate":
                current += 1
                snapshot = rounds.setdefault(current, RoundSnapshot(round=current))
                snapshot.writer = event.get("author")
                snapshot.artifact_hash = event.get("artifact_hash")
                snapshot.polish = bool(event.get("polish"))
            elif kind == "critique":
                snapshot = rounds.setdefault(current, RoundSnapshot(round=current))
                snapshot.lenses[event.get("lens", "?")] = LensSnapshot(
                    lens=event.get("lens", "?"),
                    critic=event.get("critic"),
                    issues=int(event.get("issues") or 0),
                    failed=bool(event.get("failed")),
                    failure_reason=event.get("failure_reason"),
                )
            elif kind == "triage":
                snapshot = rounds.setdefault(current, RoundSnapshot(round=current))
                snapshot.cleared = event.get("cleared", {}) or {}
            elif kind == "control":
                snapshot = rounds.setdefault(current, RoundSnapshot(round=current))
                snapshot.rule = event.get("rule")
                snapshot.action = event.get("action")
                snapshot.note = event.get("note", "")

        # counts live in the views stream, keyed by round
        for entry in self._views(run_id):
            snapshot = rounds.get(entry.get("round", 0))
            if snapshot:
                totals = entry.get("view", {}).get("totals", {})
                snapshot.blocking = totals.get("blocking", 0)
                snapshot.major = totals.get("major", 0)
                snapshot.minor = totals.get("minor", 0)

        return [rounds[k] for k in sorted(rounds)]

    def _views(self, run_id: str) -> Iterator[dict[str, Any]]:
        path = self.dir(run_id) / "signals" / "views.jsonl"
        if not path.exists():
            return
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def lens_names(self) -> list[str]:
        return [lens.value for lens in LENSES]
