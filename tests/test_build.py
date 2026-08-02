"""The build stamp (D-run-build-stamp).

These tests are about honesty more than about correctness: every branch here has a
plausible-looking wrong answer that would be worse than no answer at all, because a
recorded commit is indistinguishable from a measured one once it is in `final.json`.
"""

from __future__ import annotations

import subprocess

import pytest

from reasonable_answer.build import BuildIdentity, _compute, build_identity, describe_build


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    """A real repository with one commit — the git branch cannot be faked usefully."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("one\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-qm", "one")
    return tmp_path


# ------------------------------------------------------------------ the baked value


def test_a_baked_sha_is_authoritative(monkeypatch, checkout):
    """CI knows the commit exactly, so it wins over the checkout even inside one."""
    monkeypatch.setenv("RA_BUILD_SHA", "deadbeefcafe")
    identity = _compute(checkout)
    assert identity == BuildIdentity(commit="deadbeefcafe", dirty=False, source="image")


@pytest.mark.parametrize("raw", ["", "   ", "\n"])
def test_a_blank_sha_is_not_a_commit(monkeypatch, checkout, raw):
    """`ENV RA_BUILD_SHA=$RA_BUILD_SHA` leaves the variable set-but-empty on any build
    that did not pass the argument, which `pr-validation.yml` used to do. Testing for
    presence rather than content would record "" as an authoritative commit."""
    monkeypatch.setenv("RA_BUILD_SHA", raw)
    identity = _compute(checkout)
    assert identity.source == "git"
    assert identity.commit and identity.commit != raw


# ------------------------------------------------------------------ the checkout


def test_a_clean_checkout_reports_its_head(monkeypatch, checkout):
    monkeypatch.delenv("RA_BUILD_SHA", raising=False)
    identity = _compute(checkout)
    head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert identity == BuildIdentity(commit=head, dirty=False, source="git")


def test_an_edited_checkout_reports_dirty(monkeypatch, checkout):
    """The whole reason this path shells out to git rather than reading .git/HEAD."""
    monkeypatch.delenv("RA_BUILD_SHA", raising=False)
    assert _compute(checkout).dirty is False
    _compute.cache_clear()

    (checkout / "a.txt").write_text("two\n")
    edited = _compute(checkout)
    assert edited.dirty is True
    assert edited.commit == _compute.__wrapped__(checkout).commit  # same commit, modified tree


def test_an_untracked_file_counts_as_dirty(monkeypatch, checkout):
    """`--porcelain` lists untracked files, and it should: code that is not committed
    is still code that ran."""
    monkeypatch.delenv("RA_BUILD_SHA", raising=False)
    (checkout / "extra.py").write_text("x = 1\n")
    assert _compute(checkout).dirty is True


# ------------------------------------------------------------------ knowing nothing


def test_no_checkout_and_no_env_is_unknown(monkeypatch, tmp_path):
    """The production container has no .git. Recording `unknown` is the point: the
    alternative is a value that reads like a measurement and is not one."""
    monkeypatch.delenv("RA_BUILD_SHA", raising=False)
    identity = _compute(tmp_path)
    assert identity == BuildIdentity(commit=None, dirty=None, source="unknown")


def test_no_git_binary_is_unknown_not_a_crash(monkeypatch, checkout):
    """`python:3.12-slim` ships no git. A run must not die because it cannot name itself."""
    monkeypatch.delenv("RA_BUILD_SHA", raising=False)
    monkeypatch.setenv("PATH", "")
    assert _compute(checkout).source == "unknown"


def test_an_unknown_build_warns_once(monkeypatch, tmp_path, caplog):
    """Silent `unknown` is the failure this whole decision exists to make visible."""
    monkeypatch.delenv("RA_BUILD_SHA", raising=False)
    with caplog.at_level("WARNING"):
        _compute(tmp_path)
        _compute(tmp_path)  # cached: still one warning
    assert sum("build identity unavailable" in r.message for r in caplog.records) == 1


# ------------------------------------------------------------------ caching


def test_resolution_is_cached(monkeypatch, checkout):
    """A run must not pay for two subprocesses, and the answer cannot change mid-process."""
    monkeypatch.delenv("RA_BUILD_SHA", raising=False)
    calls = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or real(*a, **k))
    _compute(checkout)
    before = len(calls)
    _compute(checkout)
    assert len(calls) == before


def test_build_identity_reads_this_checkout():
    """The public entry point is anchored to the package, not the cwd — so it answers
    for the repository this code was installed from, whatever directory it runs in."""
    identity = build_identity()
    assert identity.source in ("image", "git", "unknown")
    if identity.source == "git":
        assert identity.commit and len(identity.commit) == 40


# ------------------------------------------------------------------ display


def test_serialization_is_a_dict_not_a_dataclass():
    """`RunStore` writes with `json.dumps(default=str)`, which would silently persist
    "BuildIdentity(commit=...)" — valid JSON, unqueryable record."""
    raw = BuildIdentity(commit="a" * 40, dirty=False, source="git").as_dict()
    assert raw == {"commit": "a" * 40, "dirty": False, "source": "git"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"commit": "a" * 40, "dirty": False, "source": "git"}, "aaaaaaaaaaaa"),
        ({"commit": "a" * 40, "dirty": True, "source": "git"}, "aaaaaaaaaaaa (modified)"),
        ({"commit": "a" * 40, "dirty": None, "source": "git"}, "aaaaaaaaaaaa (modification unknown)"),
        # A run that predates stamping, and every shape a stored record could rot into.
        (None, ""),
        ({}, ""),
        ({"commit": None, "dirty": None, "source": "unknown"}, ""),
        ({"commit": "", "dirty": False, "source": "image"}, ""),
        ({"commit": 7}, ""),
        ("not-a-record", ""),
    ],
)
def test_describe_build_is_tolerant_of_anything_on_disk(raw, expected):
    """These records are read back from JSON written by whatever version produced the
    run, which by construction may be older than the code reading it."""
    assert describe_build(raw) == expected
