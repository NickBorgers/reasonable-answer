"""Reading runs back off disk.

There is deliberately no database here. ``RunStore`` already writes everything a
run produces — ``events.jsonl`` as the loop progresses, ``final.json`` when it
terminates — so the web layer is a *reader* of state the pipeline already keeps.
That also means the audit trail and the UI can never disagree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal

from ..taxonomy import LENSES

Status = Literal["queued", "running", "interrupted", "accepted", "converged_unconfirmed",
                 "exhausted_unresolved", "needs_human_review", "aborted"]

TERMINAL_STATUSES = {
    "accepted",
    "converged_unconfirmed",
    "exhausted_unresolved",
    "needs_human_review",
    "aborted",
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

    def list(self, active: dict[str, str] | None = None) -> list[RunSummary]:
        active = active or {}
        out = [self.summary(d.name, active) for d in self._run_dirs()]
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
            # Events but no final.json and nobody working on it: the process died.
            # The checkpointer means this is resumable rather than lost.
            status = "interrupted"
            finished = None
            note = "no final result; the run can be resumed"

        return RunSummary(
            run_id=run_id,
            status=status,
            question=question,
            rounds=rounds,
            started_at=started,
            finished_at=finished,
            terminal_note=note,
        )

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

    def report(self, run_id: str) -> str | None:
        path = self.dir(run_id) / "final.md"
        return path.read_text() if path.exists() else None

    def question(self, run_id: str) -> str:
        path = self.dir(run_id) / "question.txt"
        return path.read_text().strip() if path.exists() else "(question not recorded)"

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
                rounds.setdefault(1, RoundSnapshot(round=1, writer="(seed)"))
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
