"""Exports: the report leaving the tailnet with its verdict still attached.

The property under test throughout is that an export cannot quietly become a clean-
looking document. A `needs_human_review` report shared as a file has to say so.
"""

from __future__ import annotations

import html
import json

import pytest
import yaml
from conftest import WEB_IDENTITY, web_client
from typer.testing import CliRunner

from reasonable_answer import cli, export
from reasonable_answer.store import CorruptRun, RunStore, purge
from reasonable_answer.web.app import create_app
from reasonable_answer.web.registry import Registry
from reasonable_answer.web.worker import RunWorker

runner = CliRunner()

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""

FINAL = {
    "run_id": "run-shared",
    "terminal_status": "needs_human_review",
    "rounds": 4,
    "chosen_round": 3,
    "artifact_hash": "aaaabbbbccccdddd",
    "label": "consensus-reviewed with in-artifact sourcing (no external retrieval)",
    "clean_records": [
        {
            "artifact_hash": "aaaabbbbccccdddd",
            "lens": "logic",
            "critic_identity": "vendor-c/logic",
            "artifact_author_identity": "vendor-a/model-a",
        },
        {
            "artifact_hash": "0000stale0000",
            "lens": "evidence",
            "critic_identity": "vendor-d/evidence",
            "artifact_author_identity": "vendor-a/model-a",
        },
    ],
    "outstanding_defects": [
        {
            "severity": "blocking",
            "category": "unsupported_claim",
            "instruction": "Cite or drop the 40% figure.",
            # Keyed to the shipped artifact, exactly as `finalize` stamps it (issue #93).
            "artifact_hash": "aaaabbbbccccdddd",
        }
    ],
    "warnings": ["the seed carried no headings"],
    "note": "",
    "build": {"commit": "c749b5e" + "0" * 33, "dirty": False, "source": "image"},
}


@pytest.fixture
def finished_run(config):
    """A run directory as `finalize` would leave it — no graph, no models."""
    store = RunStore(config.runs_dir, "run-shared")
    store.question("Does a four-day week work?")
    # Owned, because an owner-less run is a 404 on every route these tests exercise.
    store.owner(WEB_IDENTITY)
    store.event("intake", path="question")
    store.final(REPORT, FINAL)
    return "run-shared"


@pytest.fixture
def client(config, finished_run):
    """No runner is needed: every route under test reads a run that already finished."""
    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    with web_client(create_app(config, worker=worker)) as c:
        yield c
    worker.shutdown()


# ------------------------------------------------------------------- markdown


def test_the_markdown_export_carries_the_verdict_not_just_the_prose():
    """The whole reason this route exists: `final.md` alone reads as an answer."""
    document = export.export_markdown("Q?", REPORT, FINAL, "run-shared", exported_on="2026-07-25")

    assert "A claim that is fully supported [1]." in document  # the report itself
    assert "needs human review" in document
    assert "NOT consensus-clean" in document
    assert "Cite or drop the 40% figure." in document
    assert "the seed carried no headings" in document
    assert "consensus-reviewed with in-artifact sourcing" in document
    assert "run-shared" in document and "aaaabbbbcccc" in document
    assert "2026-07-25" in document


def test_an_accepted_export_says_so_without_a_defect_list():
    final = dict(FINAL, terminal_status="accepted", outstanding_defects=[], warnings=[])
    document = export.export_markdown("Q?", REPORT, final, "run-shared")

    assert "No eligible reviewer could find a material defect" in document
    assert "Outstanding defects" not in document
    assert "NOT consensus-clean" not in document


def test_only_reviewers_of_the_shipped_artifact_are_credited():
    """A clean record is keyed to one artifact hash. Crediting a critic that cleared an
    earlier draft would claim review coverage the shipped text never had."""
    document = export.export_markdown("Q?", REPORT, FINAL, "run-shared")

    assert "vendor-c/logic" in document
    assert "vendor-d/evidence" not in document


def test_nobody_is_credited_when_there_is_no_hash_to_key_against():
    """The dangerous direction. Without an artifact hash the records cannot be matched
    to the shipped draft, and crediting all of them would claim review coverage on
    exactly the runs whose record is least trustworthy."""
    for missing in (None, "", 0):
        final = dict(FINAL, artifact_hash=missing)
        document = export.export_markdown("Q?", REPORT, final, "run-shared")

        assert "Reviewed clean by" not in document
        assert "vendor-c/logic" not in document
        assert "vendor-d/evidence" not in document


def test_a_defect_raised_against_another_draft_is_not_charged_to_this_report():
    """The twin of `_reviewers` (issue #93). On a non-accepted terminal the shipped
    draft can be an earlier round than the one the loop stopped on; a defect keyed to
    that later draft quotes text this report never contained, so it must not render
    under a heading asserting the defect belongs to the artifact in hand."""
    final = dict(
        FINAL,
        outstanding_defects=[
            {
                "severity": "blocking",
                "category": "unsupported_claim",
                "instruction": "Cite or drop the 40% figure.",
                "artifact_hash": "0000-round-8-draft",  # a different draft
            }
        ],
    )
    document = export.export_markdown("Q?", REPORT, final, "run-shared")

    assert "Cite or drop the 40% figure." not in document
    # And the empty list is not passed off as a clean result.
    assert "### Outstanding defects in this report" in document
    assert "not read the absence of a list as clean" in document


def test_the_withheld_note_is_absent_when_the_defects_do_key_to_this_report():
    """The note only fires when defects exist but belong elsewhere; a report whose own
    defect set is on record renders it, and a genuinely clean one renders nothing."""
    document = export.export_markdown("Q?", REPORT, FINAL, "run-shared")
    assert "Cite or drop the 40% figure." in document
    assert "not read the absence of a list as clean" not in document

    clean = dict(FINAL, terminal_status="accepted", outstanding_defects=[], warnings=[])
    clean_doc = export.export_markdown("Q?", REPORT, clean, "run-shared")
    assert "Outstanding defects" not in clean_doc


def test_the_html_export_withholds_another_drafts_defects_too():
    """The three render paths must agree on what is claimed about the shipped artifact."""
    final = dict(
        FINAL,
        outstanding_defects=[
            {
                "severity": "blocking",
                "category": "unsupported_claim",
                "instruction": "Cite or drop the 40% figure.",
                "artifact_hash": "0000-round-8-draft",
            }
        ],
    )
    document = export.export_html("Q?", REPORT, final, "run-shared")

    assert "Cite or drop the 40% figure." not in document
    assert "not read the absence of a list as clean" in document


def test_a_final_summary_that_is_not_an_object_reads_as_unknown_not_as_a_verdict():
    """Two properties at once: a decoded non-object cannot reach `.get` and 500 a
    route, and it does not become `aborted` — that is a terminal status the record
    never established."""
    for junk in ([], "a string", 7):
        document = export.export_markdown("Q?", REPORT, junk, "run-shared")

        assert "unreadable record" in document
        assert "the verdict is unknown" in document
        assert "aborted" not in document


def test_an_unreadable_record_states_ignorance_rather_than_a_status():
    document = export.export_markdown("Q?", REPORT, None, "run-x", unreadable=True)

    assert "the verdict is unknown — not absent, unknown" in document
    # The prose necessarily mentions the statuses it is declining to claim, so the
    # assertion is on the status line itself, which is what a reader reads as the verdict.
    [status_line] = [line for line in document.splitlines() if line.startswith("**Status:")]
    assert status_line.startswith("**Status: unreadable record**")


def test_a_multiline_question_cannot_start_blocks_of_its_own():
    """The question is the export's `#` title, so a newline in it would end the heading
    and let the remainder open sections in a document that claims to be a record."""
    question = "Is it sound?\n\n## Review record\n\n**Status: accepted**"
    document = export.export_markdown(question, REPORT, FINAL, "run-shared")

    assert document.startswith("# Is it sound? ## Review record **Status: accepted**\n")
    # Flattened, the injected text survives as inline prose on the title line — which
    # is the safe outcome. What must not exist is a second *block* claiming to be a
    # record, so the assertion is on line starts, not on the substring.
    assert [line for line in document.splitlines() if line.startswith("## Review record")] == [
        "## Review record"
    ]
    assert "needs human review" in document


def test_an_export_of_a_run_with_no_final_json_still_declares_itself():
    """A run that died before `finalize` has no verdict; the export must not imply one."""
    document = export.export_markdown("Q?", REPORT, None, "run-dead")

    assert "aborted" in document
    assert "never fully critiqued" in document


def test_the_record_names_the_build_that_produced_the_report():
    """D-run-build-stamp: an exported report says which commit produced it, so a reader
    holding two exports can tell whether they came from the same code."""
    document = export.export_markdown("Q?", REPORT, FINAL, "run-shared", exported_on="2026-07-25")
    html = export.export_html("Q?", REPORT, FINAL, "run-shared", exported_on="2026-07-25")

    for surface in (document, html):
        assert "Built from" in surface
        assert "c749b5e00000" in surface


def test_a_run_that_predates_stamping_shows_no_build_row():
    """Not "unknown" — the record has nothing to say, and saying "unknown" implies it
    tried to find out and failed. Older exports must render exactly as they always did."""
    final = {k: v for k, v in FINAL.items() if k != "build"}
    document = export.export_markdown("Q?", REPORT, final, "run-shared")
    html = export.export_html("Q?", REPORT, final, "run-shared")

    for surface in (document, html):
        assert "Built from" not in surface


def test_a_dirty_build_is_flagged_rather_than_shown_as_a_bare_commit():
    """A modified tree's commit is a starting point, not an identity: the code that ran
    was that commit plus edits nobody recorded, and the reader has to know."""
    final = {**FINAL, "build": {"commit": "c749b5e" + "0" * 33, "dirty": True, "source": "git"}}
    assert "(modified)" in export.export_markdown("Q?", REPORT, final, "run-shared")


def test_the_review_record_is_the_same_list_the_page_shows(client, finished_run):
    """One source for the defect list, so a screen reader and a file reader agree."""
    page = client.get(f"/runs/{finished_run}/report").text
    document = client.get(f"/runs/{finished_run}/export.md").text

    for surface in (page, document):
        assert "Cite or drop the 40% figure." in surface
        assert "the seed carried no headings" in surface


def test_copy_markdown_copies_the_export_document_not_just_the_report(client, finished_run):
    """Copy markdown must place the export — report *and* review record — on the
    clipboard, the same text `export.md`/`Download .md` serve (D-verdict-attached). The source is the
    off-screen textarea the copy button selects, so the record has to be *in* it, not
    merely elsewhere on the page."""
    page = client.get(f"/runs/{finished_run}/report").text
    textarea = html.unescape(
        page.split('id="copy-src"')[1].split(">", 1)[1].split("</textarea>")[0]
    )
    document = client.get(f"/runs/{finished_run}/export.md").text

    assert "## Review record" in textarea
    assert "needs human review" in textarea
    assert "Cite or drop the 40% figure." in textarea
    assert textarea.strip() == document.strip()


# ----------------------------------------------------------------------- html


def test_the_html_export_loads_no_subresources_when_opened():
    """The guarantee is about *automatic* requests, not about links.

    `web/markdown.py` disables images so that opening a report never emits a request on
    the reader's behalf, and a shared file that pulled a webfont would hand that back.
    A source link is still a link: it is inert until a human clicks it, and clicking it
    is the point of a `## Sources` list — so this asserts nothing loads on open, not
    that the document can never reach the network.
    """
    document = export.export_html("Q?", REPORT, FINAL, "run-shared")

    assert "<style>" in document
    assert "<script" not in document
    for attribute in ("src=", "<link", "@import", "url("):
        assert attribute not in document
    assert "default-src 'none'" in document
    assert "img-src 'none'" in document


def test_the_html_export_renders_model_text_as_text():
    """Report bodies are model-written and travel to people who did not run the tool."""
    hostile = "# Answer\n\n<script>alert(1)</script> and <img src=x>\n"
    document = export.export_html("Q?", hostile, FINAL, "run-shared")

    assert "<script>alert(1)</script>" not in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document


def test_the_html_export_carries_the_verdict_too():
    document = export.export_html("Q?", REPORT, FINAL, "run-shared")

    assert "needs human review" in document
    assert "Cite or drop the 40% figure." in document


# ---------------------------------------------------------------------- print


def test_the_printed_page_keeps_the_verdict_and_drops_the_chrome(client, finished_run):
    """iOS `Save as PDF` renders this page with its print stylesheet — so the rules that
    make a shareable PDF are asserted here rather than left to be noticed on paper."""
    page = client.get(f"/runs/{finished_run}/report").text

    assert "@media print" in page
    # A phone in dark mode would otherwise print white-on-black.
    assert '--bg: #fff' in page.split("@media print")[1]
    printed = page.split("@media print")[1]
    assert ".print-only { display: block !important; }" in printed
    assert "header, footer, form, .run-actions, .share" in printed
    # The record is the block that must survive printing.
    assert ".record { break-before: page" in printed

    # And the printed page reintroduces what the browser chrome was carrying.
    assert 'class="print-only print-header"' in page
    assert "Does a four-day week work?" in page


def test_printing_undoes_the_phone_layout_it_would_otherwise_match(client, finished_run):
    """A width media query in print is evaluated against the page box, not a viewport.
    A4 less the print margins is about 42rem, so the 48rem phone rules apply on paper —
    where a table sized to `max-content` inside `overflow-x: auto` loses columns with
    no scrollbar to reveal them."""
    printed = client.get(f"/runs/{finished_run}/report").text.split("@media print")[1]

    assert ".report .table-scroll { overflow: visible; max-width: none; }" in printed
    assert ".report .table-scroll > table { width: 100%; min-width: 0; }" in printed
    assert ".panel > .report {" in printed


def test_the_exported_file_and_the_printed_page_share_one_stylesheet(client, finished_run):
    page = client.get(f"/runs/{finished_run}/report").text
    document = client.get(f"/runs/{finished_run}/export.html").text

    marker = "@media print"
    assert page.split(marker)[1][:400] == document.split(marker)[1][:400]


# --------------------------------------------------------------------- routes


def test_the_export_routes_download_rather_than_render(client, finished_run):
    for suffix, media in (("md", "text/markdown"), ("html", "text/html")):
        response = client.get(f"/runs/{finished_run}/export.{suffix}")
        assert response.status_code == 200
        assert media in response.headers["content-type"]
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment; ")
        assert disposition.endswith(f'.{suffix}"')


def test_report_md_stays_the_raw_artifact(client, finished_run, config):
    """Anything that hashes or diffs a report reads this route; the review record goes
    on `export.md` precisely so these bytes do not move."""
    served = client.get(f"/runs/{finished_run}/report.md").text
    assert served == (config.runs_dir / finished_run / "final.md").read_text()
    assert "Review record" not in served


def test_exporting_a_run_with_nothing_to_export_is_a_404(client, config):
    store = RunStore(config.runs_dir, "run-early")
    store.question("Too soon?")
    store.event("intake", path="question")

    for suffix in ("md", "html"):
        assert client.get(f"/runs/run-early/export.{suffix}").status_code == 404
        assert client.get(f"/runs/run-nosuch/export.{suffix}").status_code == 404


def test_the_export_routes_refuse_a_run_whose_record_cannot_be_read(client, config, finished_run):
    """An export is durable and has to state a verdict. With an unreadable record there
    is no verdict to state, so the honest response is to refuse — not to ship a file
    that says `aborted`, which is a status no controller rule produced."""
    (config.runs_dir / finished_run / "final.json").write_text("{ not json")

    for suffix in ("md", "html"):
        response = client.get(f"/runs/{finished_run}/export.{suffix}")

        assert response.status_code == 409
        assert "cannot be read" in response.json()["detail"]
        assert "aborted" not in response.text
        # The exception names the file it could not parse; that is for the operator's
        # log, not for a response body served on a trusted header.
        assert str(config.runs_dir) not in response.text
        assert "final.json" not in response.text


def test_the_report_page_survives_an_unreadable_record_and_says_so(client, config, finished_run):
    """A page is not a durable artifact, so it renders — but it is what gets printed to
    PDF, so it must not print a verdict either."""
    (config.runs_dir / finished_run / "final.json").write_text("{ not json")

    page = client.get(f"/runs/{finished_run}/report")

    assert page.status_code == 200
    assert "A claim that is fully supported" in page.text  # the report still reads
    assert "unreadable record" in page.text
    assert "needs human review" not in page.text
    assert "Cite or drop the 40% figure." not in page.text  # no defects can be claimed


def test_the_registry_tells_corrupt_apart_from_absent_only_when_asked(config, finished_run):
    """`final` keeps its lenient contract for the pages that already depend on it;
    `final_strict` is the one the export paths ask."""
    registry = Registry(config.runs_dir)
    (config.runs_dir / finished_run / "final.json").write_text("{ not json")

    assert registry.final(finished_run) is None
    with pytest.raises(CorruptRun):
        registry.final_strict(finished_run)

    assert Registry(config.runs_dir).final_strict("run-never-existed") is None


@pytest.mark.parametrize(
    "question",
    ['a "quoted" question', "line\r\nbreak: injected", "../../etc/passwd", "?????"],
)
def test_a_download_filename_cannot_carry_anything_into_the_header(question):
    """Question text is request-derived and lands in `Content-Disposition`."""
    name = export.export_filename(question, "run-shared", "md")

    assert name.replace("-", "").replace(".", "").isalnum()
    assert name.endswith(".md")
    for bad in ('"', "\r", "\n", "/", "\\", ";"):
        assert bad not in name


# ------------------------------------------------------------------------ cli


@pytest.fixture
def cli_config(config, finished_run, tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "roster": {
                    "writers": ["writer-a", "writer-b"],
                    "critics": {
                        "logic": ["logic-spec", "writer-a"],
                        "evidence": ["evidence-spec", "writer-a"],
                        "completeness": ["completeness-spec", "writer-a"],
                    },
                },
                "runs_dir": str(config.runs_dir),
            }
        )
    )
    return path


def test_ra_export_writes_a_shareable_document(cli_config):
    result = runner.invoke(cli.app, ["export", "run-shared", "--config", str(cli_config)])

    assert result.exit_code == 0
    assert "A claim that is fully supported" in result.stdout
    assert "needs human review" in result.stdout


def test_ra_export_html_to_a_file(cli_config, tmp_path):
    out = tmp_path / "share.html"
    result = runner.invoke(
        cli.app,
        ["export", "run-shared", "-f", "html", "-o", str(out), "--config", str(cli_config)],
    )

    assert result.exit_code == 0
    assert "<style>" in out.read_text()
    assert "Cite or drop the 40% figure." in out.read_text()


def test_ra_export_refuses_unknown_runs_and_formats(cli_config):
    unknown = runner.invoke(cli.app, ["export", "run-nope", "--config", str(cli_config)])
    assert unknown.exit_code == 1

    bad_format = runner.invoke(
        cli.app, ["export", "run-shared", "-f", "pdf", "--config", str(cli_config)]
    )
    assert bad_format.exit_code == 2


def test_ra_export_rejects_a_run_id_that_is_a_path(cli_config):
    """`read_run` validates through `safe_run_dir`, which raises `UnsafeRunId` — a
    different exception from the missing-run case, and one the command must not
    let reach the user as a traceback."""
    for bad in ("../etc", "..", "a/b", "/etc/passwd", "x" * 80):
        result = runner.invoke(cli.app, ["export", bad, "--config", str(cli_config)])

        assert result.exit_code == 2, bad
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "invalid run id" in result.stdout


def test_ra_export_refuses_a_run_whose_record_cannot_be_read(cli_config, config):
    """A corrupt `final.json` means the terminal status is *unknown*. Reading it as
    absent would export the run as `aborted` — a verdict no rule produced."""
    (config.runs_dir / "run-shared" / "final.json").write_text("{not json")

    result = runner.invoke(cli.app, ["export", "run-shared", "--config", str(cli_config)])

    assert result.exit_code == 1
    assert "cannot describe this run" in result.stdout
    assert "aborted" not in result.stdout


def test_ra_export_after_a_content_purge_says_what_happened(cli_config, config):
    """`purge --content-only` drops `final.md` and keeps the decision record, so this is
    a normal end state for an old run rather than a corrupt one."""
    purge(config.runs_dir, "run-shared", content_only=True)

    result = runner.invoke(cli.app, ["export", "run-shared", "--config", str(cli_config)])

    assert result.exit_code == 1
    assert "purged" in result.stdout
    assert json.loads((config.runs_dir / "run-shared" / "final.json").read_text())
