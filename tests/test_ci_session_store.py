"""Tests for scripts/ci-session-store.sh.

The script moves a coding agent's session between workflow runs. Two of its
responsibilities are security-relevant enough to pin down here rather than leave to
manual checking:

`parse-trailer` reads an `Author-Session:` line out of a **PR body** — user-editable text
on a public repository — and its output selects which artifact gets downloaded and mounted
into the fixer container, the one stage holding a write-capable PAT. A permissive parser
there is a supply-chain problem, not a formatting bug.

`validate` decides whether a downloaded session is real. Saying yes to an empty one
produces an agent that believes it has context and does not, which is worse than the cold
path it would otherwise fall back to.

These run offline and touch nothing outside tmp_path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci-session-store.sh"


def run(*args: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke the script with an isolated session root."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(root or Path("/tmp"))}
    if root is not None:
        env["CI_SESSION_ROOT"] = str(root)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ── parse-trailer ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Author-Session: claude/12345", "claude\t12345"),
        ("Author-Session: codex/99", "codex\t99"),
        ("Resolves #1\n\nsome text\n\nAuthor-Session: claude/7\n", "claude\t7"),
        # Trailing whitespace is tolerated; editors add it.
        ("Author-Session: codex/1   \n", "codex\t1"),
    ],
)
def test_parse_trailer_accepts_well_formed(body: str, expected: str) -> None:
    result = run("parse-trailer", body)
    assert result.returncode == 0
    assert result.stdout.strip("\n") == expected


@pytest.mark.parametrize(
    "body",
    [
        # Unknown agent — the value picks a container mount point.
        "Author-Session: evil/1",
        # Path traversal: this value is interpolated into a directory path.
        "Author-Session: claude/../../etc",
        "Author-Session: ../claude/1",
        # Command-injection shapes.
        "Author-Session: claude/1 ; rm -rf /",
        "Author-Session: claude/$(id)",
        "Author-Session: claude/`id`",
        # Not anchored to line start — prose merely mentioning a trailer must not count.
        "see Author-Session: claude/1 for details",
        "  Author-Session: claude/1",
        # Run id must be numeric; it addresses a workflow run.
        "Author-Session: claude/abc",
        "Author-Session: claude/1a",
        # Structurally wrong.
        "Author-Session: claude",
        "Author-Session:claude/1",
        "Author-Session: /1",
        "",
        "no trailer here at all",
    ],
)
def test_parse_trailer_rejects_malformed(body: str) -> None:
    result = run("parse-trailer", body)
    assert result.returncode != 0, f"should have rejected: {body!r}"
    assert result.stdout.strip() == ""


def test_parse_trailer_takes_the_first_match_only() -> None:
    """A second trailer must not silently override the first.

    Appending one is the cheapest way to try to redirect the fixer at another run's
    artifact, so the parser commits to the first and ignores the rest.
    """
    body = "Author-Session: claude/1\nAuthor-Session: codex/2\n"
    result = run("parse-trailer", body)
    assert result.returncode == 0
    assert result.stdout.strip("\n") == "claude\t1"


# ── home-path ────────────────────────────────────────────────────────────────


def test_home_path_scopes_codex_to_sessions() -> None:
    """Codex must mount at .codex/sessions, not .codex.

    Mounting over the whole of ~/.codex shadows the standalone runtime under
    .codex/packages/, and `command -v codex` then fails inside the container with an
    error that looks nothing like a mount problem.
    """
    assert run("home-path", "codex").stdout.strip() == "/home/ci/.codex/sessions"
    assert run("home-path", "claude").stdout.strip() == "/home/ci/.claude"


def test_home_path_rejects_unknown_agent() -> None:
    assert run("home-path", "evil").returncode != 0


# ── pack / unpack / validate ─────────────────────────────────────────────────


def _populate_claude_session(session_dir: Path) -> None:
    transcript = session_dir / "projects" / "-workspace"
    transcript.mkdir(parents=True, exist_ok=True)
    (transcript / "conv.jsonl").write_text('{"type":"user"}\n')


def test_pack_unpack_validate_roundtrip(tmp_path: Path) -> None:
    prepared = run("prepare", "claude", "42", "777", root=tmp_path)
    assert prepared.returncode == 0
    session_dir = Path(prepared.stdout.strip())
    _populate_claude_session(session_dir)

    assert run("validate", "claude", "42", "777", root=tmp_path).returncode == 0

    tar = tmp_path / "session.tgz"
    assert run("pack", "claude", "42", "777", str(tar), root=tmp_path).returncode == 0
    assert tar.exists()

    # Destroy the original: unpack must reconstitute it from the tarball alone, which is
    # the only thing that crosses between runners.
    import shutil

    shutil.rmtree(session_dir)

    unpacked = run("unpack", "claude", "42", "777", str(tar), root=tmp_path)
    assert unpacked.returncode == 0
    assert run("validate", "claude", "42", "777", root=tmp_path).returncode == 0
    assert (Path(unpacked.stdout.strip()) / "projects" / "-workspace" / "conv.jsonl").exists()


def test_pack_refuses_an_empty_session(tmp_path: Path) -> None:
    """An empty dir packs to a tarball that unpacks to nothing.

    The resulting failure would surface two workflow runs later as an unexplained fall
    back to the cold fixer, so it fails here where the cause is still visible.
    """
    run("prepare", "claude", "42", "777", root=tmp_path)
    result = run("pack", "claude", "42", "777", str(tmp_path / "x.tgz"), root=tmp_path)
    assert result.returncode != 0


def test_validate_rejects_an_empty_session(tmp_path: Path) -> None:
    run("prepare", "claude", "42", "777", root=tmp_path)
    assert run("validate", "claude", "42", "777", root=tmp_path).returncode != 0


def test_validate_rejects_a_zero_byte_transcript(tmp_path: Path) -> None:
    """Scaffolding without content is what an author run that died early leaves behind."""
    prepared = run("prepare", "claude", "42", "777", root=tmp_path)
    session_dir = Path(prepared.stdout.strip())
    transcript = session_dir / "projects" / "-workspace"
    transcript.mkdir(parents=True)
    (transcript / "conv.jsonl").write_text("")

    assert run("validate", "claude", "42", "777", root=tmp_path).returncode != 0


def test_validate_rejects_a_missing_session(tmp_path: Path) -> None:
    assert run("validate", "claude", "999", "999", root=tmp_path).returncode != 0


def test_sessions_are_keyed_per_run(tmp_path: Path) -> None:
    """Two attempts on the same issue must not share a directory.

    Both CLIs resume "the most recent session here" — `claude --continue`,
    `codex exec resume --last`. A shared directory would make that pick an arbitrary
    earlier attempt.
    """
    first = Path(run("prepare", "claude", "42", "100", root=tmp_path).stdout.strip())
    second = Path(run("prepare", "claude", "42", "200", root=tmp_path).stdout.strip())
    assert first != second

    _populate_claude_session(first)
    # The second run's directory is still empty, so it must not validate on the strength
    # of the first run's transcript.
    assert run("validate", "claude", "42", "200", root=tmp_path).returncode != 0
