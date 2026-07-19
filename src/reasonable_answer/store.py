"""Run store — the audit trail, and the privacy posture around it.

`runs/<run_id>/` holds seed material, drafts and critiques, i.e. potentially
sensitive content. So: the run directory is 0700, artifact-bearing files are split
from signal-only files, and retention differs between them — `purge` drops reports
and critiques while keeping the decision record, which is what you actually want to
keep for auditing a run's convergence (docs/architecture.md).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from itertools import count
from pathlib import Path
from typing import Any

from pydantic import BaseModel

#: purged by `ra purge --content`; retained longer than the signal record
CONTENT_DIRS = ("reports", "critiques")

#: A run id becomes a filesystem path and, via `purge`, an rmtree target. Anything
#: outside this alphabet — separators, `..`, absolute paths — is rejected outright.
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class UnsafeRunId(ValueError):
    """The run id could escape the runs directory."""


def safe_run_dir(root: Path, run_id: str, must_exist_root: bool = True) -> Path:
    if not RUN_ID.match(run_id or "") or ".." in run_id:
        raise UnsafeRunId(f"invalid run id: {run_id!r}")
    # `strict=False` so validation works before the root exists; the id alphabet
    # already excludes separators, so no symlink can be traversed here.
    root = Path(root).resolve(strict=False)
    target = (root / run_id).resolve(strict=False)
    if root not in target.parents:
        raise UnsafeRunId(f"run id escapes the runs directory: {run_id!r}")
    return target


def _next_sequence(critiques: Path) -> int:
    if not critiques.exists():
        return 1
    highest = 0
    for path in critiques.iterdir():
        prefix = path.name.split("-", 1)[0]
        if prefix.isdigit():
            highest = max(highest, int(prefix))
    return highest + 1


class RunStore:
    def __init__(self, root: Path, run_id: str) -> None:
        # Validate before touching the filesystem at all — a rejected run id must
        # not leave a directory behind.
        self.run_id = run_id
        self.dir = safe_run_dir(root, run_id, must_exist_root=False)
        self.dir.mkdir(parents=True, exist_ok=True)
        # Continue the critique sequence where a previous process left off, so a
        # resumed run appends to the audit trail instead of overwriting it.
        self._seq = count(_next_sequence(self.dir / "critiques"))
        os.chmod(self.dir, 0o700)
        for sub in (*CONTENT_DIRS, "signals"):
            (self.dir / sub).mkdir(exist_ok=True)
            os.chmod(self.dir / sub, 0o700)

    # ------------------------------------------------------------------ writing

    def event(self, kind: str, **fields: Any) -> None:
        self._append("events.jsonl", {"ts": time.time(), "kind": kind, **fields})

    def question(self, question: str, seed: str | None = None) -> None:
        """Recorded up front so a queued run is identifiable before the graph has
        produced anything."""
        self._write(Path("question.txt"), question)
        if seed:
            self._write(Path("seed.md"), seed)

    def report(self, round_no: int, artifact_hash: str, text: str, author: str) -> None:
        name = f"r{round_no:02d}-{artifact_hash[:12]}.md"
        self._write(Path("reports") / name, f"<!-- author: {author} -->\n\n{text}")

    def critique(self, artifact_hash: str, lens: str, attempt: int, payload: BaseModel) -> None:
        # The sequence number keeps every critique on the record: attempts can repeat
        # (a fallback retry reuses a critic) and artifacts can repeat byte-for-byte,
        # so hash+lens+attempt alone would let a later write erase an earlier one.
        name = f"{next(self._seq):03d}-{artifact_hash[:12]}-{lens}-a{attempt}.json"
        self._write(
            Path("critiques") / name,
            json.dumps(payload.model_dump(mode="json"), indent=2),
        )

    def view(self, round_no: int, view: BaseModel) -> None:
        self._append(
            "signals/views.jsonl", {"round": round_no, "view": view.model_dump(mode="json")}
        )

    def decision(self, round_no: int, decision: BaseModel) -> None:
        self._append(
            "signals/decisions.jsonl",
            {"round": round_no, "decision": decision.model_dump(mode="json")},
        )

    def final(self, text: str, summary: dict[str, Any]) -> None:
        self._write(Path("final.md"), text)
        self._write(Path("final.json"), json.dumps(summary, indent=2, default=str))

    # ------------------------------------------------------------------ helpers

    def _write(self, rel: Path, content: str) -> None:
        path = self.dir / rel
        path.write_text(content)
        os.chmod(path, 0o600)

    def _append(self, rel: str, obj: dict[str, Any]) -> None:
        path = self.dir / rel
        with path.open("a") as fh:
            fh.write(json.dumps(obj, default=str) + "\n")
        os.chmod(path, 0o600)


def purge(root: Path, run_id: str, content_only: bool = False) -> list[str]:
    """Delete a run, or just its artifact-bearing content."""
    target = safe_run_dir(root, run_id)
    if not target.exists():
        raise FileNotFoundError(f"no such run: {target}")
    removed: list[str] = []
    if content_only:
        for sub in CONTENT_DIRS:
            path = target / sub
            if path.exists():
                shutil.rmtree(path)
                path.mkdir()
                os.chmod(path, 0o700)
                removed.append(str(path))
        final = target / "final.md"
        if final.exists():
            final.unlink()
            removed.append(str(final))
    else:
        shutil.rmtree(target)
        removed.append(str(target))
    return removed


def expired_runs(root: Path, retention_days: int) -> list[str]:
    """Runs whose content is older than the retention window."""
    root = Path(root)
    if not root.exists():
        return []
    cutoff = time.time() - retention_days * 86400
    return sorted(
        p.name for p in root.iterdir() if p.is_dir() and p.stat().st_mtime < cutoff
    )
