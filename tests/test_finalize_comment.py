"""Tests for .github/scripts/review/render-finalize-comment.sh.

This comment is what a human actually reads to learn whether a PR is clear to merge, and it
said two contradictory things at once. On a GO where the fixer had resolved every blocker in
the same cycle, it printed `✅ GO — cleared at cycle 1` and then a bare **Blocking issues**
heading listing those same, already-fixed findings. The only signal that they were fixed was
a count inside a Why bullet ("2 blocker(s) addressed by fixer"), several lines above. Read
top-down it says the merge gate passed a PR with outstanding blockers
(D-addressed-blockers-visible).

The split is driven by the judge's own `addressed_blocker_ids`, so the comment cannot
disagree with the gate about which blockers still stand. These pin that, both directions,
and the namespacing that keeps two reviewers' identical bare ids apart.

Offline: real `jq`, a fake `gh` capturing the rendered body, nothing outside tmp_path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "review" / "render-finalize-comment.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="the renderer shells out to jq")

SHA = "c472634aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FIX_SHA = "f2ea525bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _blocker(bid: str, severity: str = "high", **extra: str) -> dict:
    return {"id": bid, "severity": severity, "message": f"{bid} needs attention", **extra}


def _render(
    tmp_path: Path,
    *,
    reviewers: list[dict],
    verdict: dict,
    verdict_value: str = "GO",
    category: str = "go",
    post_fix_sha: str = FIX_SHA,
) -> str:
    """Run the renderer over a synthetic artifact set; return the comment body it posted."""
    reviewer_dir = tmp_path / "reviewer-artifacts"
    reviewer_dir.mkdir()
    for r in reviewers:
        (reviewer_dir / f"reviewer-{r['role']}-result.json").write_text(json.dumps(r))

    verdict_dir = tmp_path / "verdict"
    verdict_dir.mkdir()
    (verdict_dir / f"verdict-{SHA}.json").write_text(json.dumps(verdict))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    captured = tmp_path / "posted.md"
    gh = bin_dir / "gh"
    # `gh pr comment <n> --repo <r> --body-file <path>`: keep the body, discard the rest.
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'while [ $# -gt 0 ]; do case "$1" in --body-file) cp "$2" "$GH_CAPTURE"; shift 2 ;;'
        ' *) shift ;; esac; done\n'
    )
    gh.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "REPO": "owner/repo",
            "PR_NUMBER": "153",
            "REVIEWED_SHA": SHA,
            "CYCLE": "1",
            "VERDICT": verdict_value,
            "CATEGORY": category,
            "POST_FIX_SHA": post_fix_sha,
            "VERDICT_DIR": "verdict",
            "REVIEWER_DIR": "reviewer-artifacts",
            "GH_CAPTURE": str(captured),
        },
    )
    assert proc.returncode == 0, proc.stderr
    return captured.read_text()


def _reviewer(role: str, decision: str = "approve", blockers: list[dict] | None = None) -> dict:
    return {
        "role": role,
        "decision": decision,
        "reviewed_sha": SHA,
        "cycle": 1,
        "summary": f"{role} summary",
        "blocking_issues": blockers or [],
        "non_blocking_notes": [],
    }


# ── the reported bug: a GO whose blockers were all fixed ─────────────────────


def test_fixed_blockers_are_not_listed_as_blocking(tmp_path: Path) -> None:
    body = _render(
        tmp_path,
        reviewers=[
            _reviewer("quality", "request_changes", [_blocker("qual-drift-1", decision_ref="QP12")]),
            _reviewer("security"),
        ],
        verdict={
            "verdict": "GO",
            "category": "go",
            "reasons": ["All 5 non-abstaining reviewer(s) cleared; 1 blocker(s) addressed by fixer."],
            "unaddressed_blocker_ids": [],
            "addressed_blocker_ids": ["quality/qual-drift-1"],
        },
    )
    # The heading that made a cleared PR look unclear must not appear on its own.
    assert "### Blocking issues — still outstanding" not in body
    assert "fixed by the fixer" in body
    assert "not** outstanding" in body
    # The finding is still shown — suppressing it would hide what the reviewer actually said.
    assert "qual-drift-1" in body
    assert "✅" in body


def test_the_table_counts_outstanding_not_raised(tmp_path: Path) -> None:
    """The table is read before anything else. `request_changes` beside a bare `2` on a GO
    comment reproduces the same contradiction one line higher up."""
    body = _render(
        tmp_path,
        reviewers=[
            _reviewer(
                "quality",
                "request_changes",
                [_blocker("qual-drift-1"), _blocker("qual-claim-1", "medium")],
            )
        ],
        verdict={
            "verdict": "GO",
            "category": "go",
            "reasons": [],
            "unaddressed_blocker_ids": [],
            "addressed_blocker_ids": ["quality/qual-drift-1", "quality/qual-claim-1"],
        },
    )
    row = next(ln for ln in body.splitlines() if ln.startswith("| `quality`"))
    assert "| 0 (2 fixed) |" in row


def test_the_table_still_shows_a_plain_count_when_nothing_was_fixed(tmp_path: Path) -> None:
    body = _render(
        tmp_path,
        reviewers=[_reviewer("security", "request_changes", [_blocker("sec-1")])],
        verdict={
            "verdict": "NO-GO",
            "category": "reviewer_blockers",
            "reasons": [],
            "unaddressed_blocker_ids": ["security/sec-1"],
            "addressed_blocker_ids": [],
        },
        verdict_value="NO-GO",
        category="reviewer_blockers",
    )
    row = next(ln for ln in body.splitlines() if ln.startswith("| `security`"))
    assert "| 1 |" in row


def test_the_fix_commit_is_named(tmp_path: Path) -> None:
    body = _render(
        tmp_path,
        reviewers=[_reviewer("quality", "request_changes", [_blocker("qual-drift-1")])],
        verdict={
            "verdict": "GO",
            "category": "go",
            "reasons": [],
            "unaddressed_blocker_ids": [],
            "addressed_blocker_ids": ["quality/qual-drift-1"],
        },
    )
    assert FIX_SHA[:7] in body


def test_without_a_fix_commit_no_sha_is_invented(tmp_path: Path) -> None:
    """`post_fix_sha` equals the reviewed SHA on every path where nothing was pushed."""
    body = _render(
        tmp_path,
        reviewers=[_reviewer("quality", "request_changes", [_blocker("qual-drift-1")])],
        verdict={
            "verdict": "GO",
            "category": "go",
            "reasons": [],
            "unaddressed_blocker_ids": [],
            "addressed_blocker_ids": ["quality/qual-drift-1"],
        },
        post_fix_sha=SHA,
    )
    assert "resolved in this cycle" in body


# ── and the direction that must never soften ─────────────────────────────────


def test_unaddressed_blockers_stay_under_an_outstanding_heading(tmp_path: Path) -> None:
    body = _render(
        tmp_path,
        reviewers=[_reviewer("security", "request_changes", [_blocker("sec-1")])],
        verdict={
            "verdict": "NO-GO",
            "category": "reviewer_blockers",
            "reasons": ["1 blocking issue(s) not addressed by fixer: security/sec-1"],
            "unaddressed_blocker_ids": ["security/sec-1"],
            "addressed_blocker_ids": [],
        },
        verdict_value="NO-GO",
        category="reviewer_blockers",
    )
    assert "### Blocking issues — still outstanding" in body
    assert "⛔" in body
    assert "fixed by the fixer" not in body


def test_a_mixed_cycle_separates_the_two(tmp_path: Path) -> None:
    body = _render(
        tmp_path,
        reviewers=[
            _reviewer("security", "request_changes", [_blocker("sec-1")]),
            _reviewer("quality", "request_changes", [_blocker("qual-1")]),
        ],
        verdict={
            "verdict": "NO-GO",
            "category": "reviewer_blockers",
            "reasons": [],
            "unaddressed_blocker_ids": ["security/sec-1"],
            "addressed_blocker_ids": ["quality/qual-1"],
        },
        verdict_value="NO-GO",
        category="reviewer_blockers",
    )
    outstanding, fixed = body.split("fixed by the fixer")
    assert "security/sec-1" in outstanding
    assert "quality/qual-1" not in outstanding
    assert "quality/qual-1" in fixed


def test_ids_are_matched_namespaced_not_bare(tmp_path: Path) -> None:
    """Two reviewers can raise the same bare id. Crediting on the bare one would clear both
    when the fixer only addressed one — a blocker silently reported as fixed."""
    body = _render(
        tmp_path,
        reviewers=[
            _reviewer("security", "request_changes", [_blocker("issue-1")]),
            _reviewer("quality", "request_changes", [_blocker("issue-1")]),
        ],
        verdict={
            "verdict": "NO-GO",
            "category": "reviewer_blockers",
            "reasons": [],
            "unaddressed_blocker_ids": ["security/issue-1"],
            "addressed_blocker_ids": ["quality/issue-1"],
        },
        verdict_value="NO-GO",
        category="reviewer_blockers",
    )
    outstanding, fixed = body.split("fixed by the fixer")
    assert "security/issue-1" in outstanding
    assert "quality/issue-1" in fixed
    assert "quality/issue-1" not in outstanding


# ── degraded inputs must not break the comment ───────────────────────────────


def test_a_verdict_without_the_field_treats_everything_as_outstanding(tmp_path: Path) -> None:
    """An older verdict artifact, or a path that writes none, must fail toward *showing* a
    blocker rather than quietly filing it under "fixed"."""
    body = _render(
        tmp_path,
        reviewers=[_reviewer("security", "request_changes", [_blocker("sec-1")])],
        verdict={
            "verdict": "NO-GO",
            "category": "reviewer_blockers",
            "reasons": [],
            "unaddressed_blocker_ids": ["security/sec-1"],
        },
        verdict_value="NO-GO",
        category="reviewer_blockers",
    )
    assert "### Blocking issues — still outstanding" in body
    assert "security/sec-1" in body


def test_a_clean_panel_renders_no_blocker_section_at_all(tmp_path: Path) -> None:
    body = _render(
        tmp_path,
        reviewers=[_reviewer("security"), _reviewer("docs")],
        verdict={
            "verdict": "GO",
            "category": "go",
            "reasons": ["All 2 non-abstaining reviewer(s) cleared; 0 blocker(s) addressed by fixer."],
            "unaddressed_blocker_ids": [],
            "addressed_blocker_ids": [],
        },
    )
    assert "Blocking issues" not in body
    assert "✅ **GO**" in body


def test_a_multiline_message_stays_one_bullet(tmp_path: Path) -> None:
    """A newline inside a reviewer's message used to split the line the loop reads, emitting
    an unowned bullet that no id could be matched against."""
    body = _render(
        tmp_path,
        reviewers=[
            _reviewer(
                "security",
                "request_changes",
                [{"id": "sec-1", "severity": "high", "message": "first line\nsecond line"}],
            )
        ],
        verdict={
            "verdict": "NO-GO",
            "category": "reviewer_blockers",
            "reasons": [],
            "unaddressed_blocker_ids": ["security/sec-1"],
            "addressed_blocker_ids": [],
        },
        verdict_value="NO-GO",
        category="reviewer_blockers",
    )
    bullets = [ln for ln in body.splitlines() if ln.startswith("- ⛔")]
    assert len(bullets) == 1
    assert "first line second line" in bullets[0]
