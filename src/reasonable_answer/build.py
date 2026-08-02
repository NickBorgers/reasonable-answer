"""Which build produced this run.

A run records what it concluded — terminal status, clean records, the artifact hash of the
report text — but until now nothing recorded what *produced* it. That makes the most
useful question about this system unanswerable from its own output: "we changed the
revision prompt last week and we are still not converging — which runs already had that
change?" Correlating a run's timestamp against ``git log`` is the only alternative, and it
breaks the moment a deploy lags a merge or one PR carries two fixes.

So every run stamps the commit it ran on. Three sources, in order, and the one that fired
is recorded alongside the commit so a reader never has to guess:

* ``image`` — ``RA_BUILD_SHA`` is baked into the container at build time by
  ``docker-release.yml``, which knows the commit exactly. This is the production path, and
  it is authoritative: there is no operator step to forget.
* ``git`` — no baked value, but the package sits in a checkout. Covers ``uv run``, the
  devcontainer and the test suite, and is the only path that can report ``dirty``.
* ``unknown`` — neither. Recorded honestly rather than guessed, and warned about once, so
  a misconfigured deployment is visible in the first run rather than after a month of
  unattributable ones.

Two traps this module exists to avoid. ``ENV RA_BUILD_SHA=$RA_BUILD_SHA`` in the Dockerfile
leaves the variable *always set* — empty on a plain ``docker build``, as ``pr-validation.yml``
does — so the first rule tests non-blank, not merely present. And the commit alone cannot
say whether the tree was modified, which is why the git path shells out to ``git status``
instead of reading ``.git/HEAD`` directly: this is the only ``subprocess`` use in ``src/``,
and ``dirty`` is what buys it.
"""

from __future__ import annotations

import functools
import logging
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

# build.py -> reasonable_answer -> src -> the checkout root. Anchored to the package
# rather than the cwd deliberately: the container's WORKDIR is /data, and an installed
# wheel has no .git anywhere near it. Keying off the cwd would let the app report the
# HEAD of whatever unrelated repository it happened to be launched from.
_ROOT = Path(__file__).resolve().parents[2]

_GIT_TIMEOUT_SECONDS = 5.0

Source = Literal["image", "git", "unknown"]


@dataclass(frozen=True)
class BuildIdentity:
    """The commit a run ran on, and how confidently we know it."""

    commit: str | None
    dirty: bool | None
    source: Source

    def as_dict(self) -> dict[str, object]:
        """Always call this before handing the identity to the store.

        ``RunStore`` serialises with ``json.dumps(..., default=str)``, which would turn the
        dataclass into the string ``"BuildIdentity(commit=...)"`` without raising — an
        unparseable record that looks fine until someone tries to query it.
        """
        return {"commit": self.commit, "dirty": self.dirty, "source": self.source}

    def describe(self) -> str:
        """One line for a human: `ra doctor`, the run page, an exported report."""
        return describe_build(self.as_dict())


UNKNOWN = BuildIdentity(commit=None, dirty=None, source="unknown")


def describe_build(raw: Mapping[str, Any] | None) -> str:
    """Render a stored build record, or "" when it does not name a commit.

    Deliberately tolerant: these come back from JSON written by whatever version of this
    code produced the run, which by construction may be older than the one reading it.
    Callers that display a record omit the line entirely on "" — a run that predates
    stamping should look like a run with nothing to say, not like a run stamped "unknown".
    """
    if not isinstance(raw, Mapping):
        return ""
    commit = raw.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        return ""
    text = commit.strip()[:12]
    dirty = raw.get("dirty")
    if dirty is True:
        text += " (modified)"
    elif dirty is None:
        text += " (modification unknown)"
    return text


def _git(root: Path, *args: str) -> str | None:
    """Run a read-only git command, or return None if git cannot answer.

    ``--no-optional-locks`` because the production container runs on a read-only rootfs and
    ``git status`` would otherwise try to refresh the index. Never raises: a missing git
    binary (the slim runtime image has none), a hung filesystem and a non-zero exit are all
    the same answer here — we do not know.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "--no-optional-locks", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("git %s: %s", " ".join(args), exc)
        return None
    if result.returncode != 0:
        log.debug("git %s exited %d", " ".join(args), result.returncode)
        return None
    # Not `or None`: `status --porcelain` returns empty stdout for a clean tree, which is
    # a successful answer and must not be confused with a failure to answer.
    return result.stdout


@functools.cache
def _compute(root: Path) -> BuildIdentity:
    """Resolve once per process. Tests call this directly and clear the cache between them."""
    baked = os.environ.get("RA_BUILD_SHA", "").strip()
    if baked:
        return BuildIdentity(commit=baked, dirty=False, source="image")

    # .exists() rather than .is_dir(): in a git worktree, .git is a file pointing elsewhere.
    if (root / ".git").exists():
        head = _git(root, "rev-parse", "HEAD")
        if head and head.strip():
            status = _git(root, "status", "--porcelain")
            return BuildIdentity(
                commit=head.strip(),
                # None, not False, when git answered HEAD but not status: claiming a clean
                # tree we never checked is the one lie this module must not tell.
                dirty=None if status is None else bool(status.strip()),
                source="git",
            )

    log.warning(
        "build identity unavailable: runs will record source=unknown and cannot be attributed "
        "to a commit. Set RA_BUILD_SHA at image build time (see docs/run-provenance.md)."
    )
    return UNKNOWN


def build_identity() -> BuildIdentity:
    """The build this process is running. Cached; safe to call per run."""
    return _compute(_ROOT)
