"""Render the web UI's pages to static HTML from one source tree, with fixture data.

    python render_pages.py --src <tree>/src --config <tree>/config/roster.yaml --out <dir>

Run once against the base tree and once against the working tree; the same fixtures go
through both renderers, so any difference between the two output sets is the change under
review. No server, no proxy, no network: the renderer is called directly, the way
`tests/test_answer_card.py` does.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sys.path.insert(0, str(Path(args.src).resolve()))
    # Everything below is imported *after* the path insert so it comes from `--src`.
    from reasonable_answer import export
    from reasonable_answer.config import Config
    from reasonable_answer.web.registry import LensSnapshot, RoundSnapshot, RunSummary
    from reasonable_answer.web.render import render_index, render_report, render_run

    cfg = Config.load(args.config)
    lens_names = list(cfg.roster.critics)
    writers = cfg.roster.writers
    critics = {lens: pool for lens, pool in cfg.roster.critics.items()}
    now = time.time()
    question = "Is remote work better for software team productivity?"

    def critic(lens: str, i: int) -> str:
        pool = critics[lens]
        return pool[i % len(pool)]

    runs = [
        RunSummary("run-9f3c2a7b", "accepted", question, 3, now - 5400, now - 4300),
        RunSummary(
            "run-1d8e44c0", "running", "What does the evidence say about four-day work weeks?",
            1, now - 600, None,
        ),
        RunSummary(
            "run-77ab19e2", "exhausted_unresolved",
            "Do open-plan offices reduce face-to-face collaboration?", 6, now - 90000, now - 88000,
            terminal_note="stagnated after round 6",
        ),
        RunSummary(
            "run-c02be511", "needs_human_review",
            "Are microservices worth it for a ten-person engineering team?", 6,
            now - 260000, now - 258000,
        ),
    ]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1. The front door.
    (out / "index.html").write_text(
        render_index(runs, 0, cfg, viewer="nick@example.org")
    )

    # 2. A run in flight, one round reviewed and a second being written.
    def lens_snaps(i: int, issues: dict[str, int]) -> dict[str, LensSnapshot]:
        return {
            lens: LensSnapshot(lens, critic(lens, i), issues.get(lens, 0)) for lens in lens_names
        }

    timeline = [
        RoundSnapshot(
            1, writers[0], "a1b2c3d4e5f60718", lenses=lens_snaps(0, {lens_names[0]: 2, lens_names[-1]: 1}),
            blocking=0, major=2, minor=1, triaged=True, rule=9, action="revise",
            note="material issues remain; revising",
        ),
        RoundSnapshot(2, writers[1 % len(writers)], None),
    ]
    live = RunSummary("run-1d8e44c0", "running", runs[1].question, 2, now - 600, None)
    (out / "run-live.html").write_text(
        render_run(live, timeline, None, lens_names)
    )

    # 3. The finished report someone came back for.
    report = (
        "## Conclusion\n\n"
        "On the evidence available, remote work neither raises nor lowers software team "
        "productivity in a consistent direction; the outcome depends far more on how the team "
        "coordinates than on where it sits [1][3]. Fully remote teams with deliberate written "
        "communication match co-located output, and teams that moved remote without changing "
        "their coordination practices lost ground [2].\n\n"
        "## Key findings\n\n"
        "- Controlled studies of knowledge workers show output within a few percent of "
        "office baselines once teams settle into a routine [1].\n"
        "- The largest measured costs are in onboarding and in cross-team work, not in "
        "individual output [3].\n"
        "- Self-reported productivity rises under remote work; measured output does not "
        "follow it consistently [2].\n\n"
        "## The strongest counterargument\n\n"
        "Longitudinal data from one large firm found a measurable decline in the rate at which "
        "engineers received feedback after going remote, concentrated among junior staff [4]. "
        "That effect is real, but it is a mentoring effect rather than a productivity one, and "
        "the same study found no drop in delivered output over the period [4].\n\n"
        "## Background\n\n"
        "The question is usually asked as if it had one answer, and most of the evidence base "
        "predates the 2020 shift, when remote work was self-selected. Post-2020 studies are "
        "better controlled but shorter. The definition of productivity also differs across "
        "them: commits, closed tickets, manager ratings and self-reports do not move together [1][2].\n\n"
        "## Sources\n\n"
        "1. [Remote work and productivity: a review](https://example.org/review)\n"
        "2. [Self-reported versus measured output](https://example.org/self-report)\n"
        "3. [Coordination costs in distributed teams](https://example.org/coordination)\n"
        "4. [Feedback and mentoring after the shift to remote](https://example.org/feedback)\n"
    )
    artifact_hash = "5e8d1c0f2a4b6d7e9f01234567890abcdef0123456789abcdef0123456789abcd"
    final = {
        "terminal_status": "accepted",
        "label": "consensus-reviewed with verified sourcing",
        "rounds": 3,
        "chosen_round": 3,
        "artifact_hash": artifact_hash,
        "clean_records": [
            {"lens": lens, "critic_identity": critic(lens, i), "artifact_hash": artifact_hash}
            for lens in lens_names
            for i in range(2)
        ],
        "source_coverage": {
            "cited": 4, "addressable": 4, "bodies_read": 4, "body_backed_entries": 4,
            "existence_confirmed": 4, "verification_enabled": True,
        },
    }
    done = RunSummary("run-9f3c2a7b", "accepted", question, 3, now - 5400, now - 4300)
    prov = export.provenance(question, final, done.run_id, exported_on="2026-09-18")
    (out / "report.html").write_text(
        render_report(
            done, report, final,
            record=export.provenance_html(prov),
            copy_markdown=report,
        )
    )
    print(f"rendered {sorted(p.name for p in out.glob('*.html'))} -> {out}")


if __name__ == "__main__":
    main()
