"""The registry layout that makes a decision-bearing PR conflict-free (D-decision-per-file).

`scripts/validate-decision-numbers.sh` gates the registry's *shape* — one decision per file,
named for the slug its heading defines — and `tests/test_decision_numbers.py` drives that script.
What is left, and what this module covers, is the wiring that has to agree with the shape for the
guarantee to hold. Each assertion below corresponds to a way the collision could come back
without any single file looking wrong:

* a per-decision entry appearing in a shared surface (`mkdocs.yml`'s `nav`, the `is_spec_critical`
  allowlist, the index) — one line per decision is still a shared insertion point, and a merge
  queue ejects a PR for a one-line conflict as readily as a large one;
* the shared surfaces failing to cover the directory *as a whole*, which is what lets a decision
  file be added with no other edit — the property that makes adds conflict-free;
* `.gitattributes` re-declaring the retired `decisions-append` merge driver, which is what a
  merge queue cannot register and what fails open by dropping the base side silently.

Fully offline: it only reads files already in the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

from decision_registry import DECISIONS_DIR, INDEX, decision_files

REPO_ROOT = Path(__file__).resolve().parents[1]
MKDOCS = REPO_ROOT / "mkdocs.yml"
CLASSIFY = REPO_ROOT / ".github" / "actions" / "review-classify" / "action.yml"
GITATTRIBUTES = REPO_ROOT / ".gitattributes"
PR_VALIDATION = REPO_ROOT / ".github" / "workflows" / "pr-validation.yml"


def test_every_decision_file_is_named_for_its_heading() -> None:
    """The filename is the identifier, so a citation resolves to a path without searching."""
    files = decision_files()
    assert len(files) >= 40, f"only {len(files)} decision files found — directory moved?"
    for path in files:
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        m = re.match(r"^## (D-[a-z0-9-]+) — \S", first)
        assert m, f"{path.name} does not open with '## D-<slug> — <title>': {first!r}"
        assert m.group(1) == path.stem, f"{path.name} defines {m.group(1)}"


def test_the_index_holds_no_decision_prose() -> None:
    """The index carries doctrine, finding tables and open items — never a decision section."""
    stray = re.findall(r"^## D-[a-z0-9-]+.*$", INDEX.read_text(encoding="utf-8"), re.M)
    assert not stray, f"decision prose left in the index: {stray}"


def _prose_only(text: str) -> str:
    """Drop fenced blocks and inline code spans.

    Decision prose quotes markdown syntax as an example — `[…](./decisions.md)` naming the form
    a link takes — and a scan that counted those would flag the documentation of a rule as a
    violation of it.
    """
    text = re.sub(r"^(```|~~~).*?^\1", "", text, flags=re.M | re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def test_relative_links_out_of_a_decision_resolve() -> None:
    """A decision page sits one level below docs/, so `./page.md` would 404.

    `mkdocs build --strict` catches this, but only in the `Docs Build` job; catching it in the
    offline suite is what makes it cheap to find when writing a decision.
    """
    offenders: list[str] = []
    for path in decision_files():
        prose = _prose_only(path.read_text(encoding="utf-8"))
        for target in re.findall(r"\]\((\.{1,2}/[^)\s]+)\)", prose):
            resolved = (DECISIONS_DIR / target.split("#", 1)[0]).resolve()
            if not resolved.exists():
                offenders.append(f"{path.name} -> {target}")
    assert not offenders, "relative links that do not resolve (use ../, not ./):\n" + "\n".join(
        offenders
    )


def test_mkdocs_covers_the_directory_without_enumerating_it() -> None:
    text = MKDOCS.read_text(encoding="utf-8")
    assert "not_in_nav" in text and "/decisions/*.md" in text, (
        "mkdocs.yml must exempt docs/decisions/*.md via not_in_nav, or --strict fails on every "
        "new decision for being absent from nav"
    )
    nav = text.split("\nnav:", 1)[1]
    named = [f"decisions/{p.name}" for p in decision_files() if f"decisions/{p.name}" in nav]
    assert not named, (
        f"mkdocs.yml nav names individual decisions ({named[:3]}); that is a shared insertion "
        "point every decision-bearing PR would have to edit"
    )


def test_classify_allowlist_covers_the_directory_as_a_glob() -> None:
    text = CLASSIFY.read_text(encoding="utf-8")
    assert "docs/decisions/*.md" in text, (
        "is_spec_critical must match docs/decisions/*.md, or a decision edit is classified "
        "'docs only' and skips the full review path (D-spec-critical-coverage)"
    )
    named = [p.name for p in decision_files() if f"docs/decisions/{p.name}" in text]
    assert not named, f"the allowlist enumerates decisions ({named[:3]}) instead of globbing them"


def test_pr_validation_gates_the_directory() -> None:
    assert "docs/decisions/**" in PR_VALIDATION.read_text(encoding="utf-8"), (
        "the 'decisions' path filter must include docs/decisions/**, or a PR that adds only a "
        "decision file gets no registry check"
    )


def test_no_merge_driver_is_declared_for_the_registry() -> None:
    """The retired driver stays retired: a merge queue cannot register a repo-local driver, and
    a declared-but-unstartable one fails open by staging 'ours' with no conflict markers."""
    for line in GITATTRIBUTES.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "merge=" not in stripped, f".gitattributes declares a merge driver again: {line!r}"
