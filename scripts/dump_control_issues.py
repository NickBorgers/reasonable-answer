#!/usr/bin/env python3
"""Print every material issue a critic files against sound control fixtures.

**Which question this answers:** `ra audition` reports `control_material_rate` as a single
number — mean invented material issues per sound control — but a number cannot tell you whether
the invented issues are the model's fault or the corpus's. This script prints what the critic
actually said: `claim_span`, `related_span`, and rationale for every material issue raised against
a fixture that has no planted defect. Reading those spans is the spot-check that turned a
marginal-looking `control_material_rate` into a confident retirement in D-minimax-retirement and
D-completeness-pool-noise — both cite a manual pass over exactly this output.

**When to reach for it:** whenever a candidate's `control_material_rate` clusters close to
`max_control_material_rate` and the temptation is to suspect the fixtures rather than the model.
Read a batch of issues before concluding either way. The decisive check is structural, not a
matter of opinion on any one issue: run this against a critic that is independently known to
score low noise on the identical corpus (`mistral-large-3` on the logic lens, as of this
writing) — if it stays quiet where the candidate does not, the corpus is not the problem.

**This is an operator tool, not application code.** It is not imported by, or invoked from, the
package, the CLI, or any test — it is run by hand against a live paid proxy, the same posture as
`ra audition` itself, and it spends real proxy calls. Point `RA_CONFIG` at a roster that resolves
the alias under test before running it.

Usage::

    RA_CONFIG=/path/to/roster.yaml uv run python scripts/dump_control_issues.py \\
        candidate-alias logic --repetitions 3
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys

from reasonable_answer import audition as aud
from reasonable_answer.config import Config
from reasonable_answer.critique import critique_once
from reasonable_answer.llm import LLMClient
from reasonable_answer.taxonomy import Lens
from reasonable_answer.triage import counts_for_convergence


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("alias", help="LiteLLM alias to probe (must resolve via $RA_CONFIG's roster).")
    parser.add_argument("lens", choices=[lens.value for lens in Lens], help="Which lens's controls to read.")
    parser.add_argument(
        "--repetitions",
        type=int,
        default=3,
        help="Calls per control fixture (default: 3, matching AuditionConfig.repetitions' shipped default).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    lens = Lens(args.lens)

    config_path = os.environ.get("RA_CONFIG")
    if not config_path:
        print("set $RA_CONFIG to a roster that resolves this alias", file=sys.stderr)
        return 2

    config = Config.load(config_path)
    controls = [f for f in aud.load_fixtures(aud.DEFAULT_FIXTURE_DIR).for_lens(lens) if f.is_control]
    if not controls:
        print(f"no control fixtures found for lens {lens.value!r}", file=sys.stderr)
        return 2

    client = LLMClient(config)
    identity = client.resolve_identities([args.alias])[args.alias]
    client.probe_structured_output(args.alias)

    total_material = 0
    for fixture in controls:
        for rep in range(args.repetitions):
            result = critique_once(
                client,
                args.alias,
                identity,
                lens,
                fixture.question,
                fixture.artifact,
                hashlib.sha256(fixture.artifact.encode()).hexdigest(),
                aud.AUDITION_AUTHOR,
                sources=None,
            )
            if result.failed:
                print(f"\n### {fixture.id} rep{rep}: LENS FAILED — {result.failure_reason}", flush=True)
                continue
            material = [i for i in result.issues if counts_for_convergence(i.category, i.severity)]
            total_material += len(material)
            print(f"\n### {fixture.id} rep{rep}: {len(material)} material", flush=True)
            for issue in material:
                print(f"  [{issue.category.value}/{issue.severity.value}] locus={issue.locus}", flush=True)
                print(f"    claim_span:   {issue.claim_span!r}", flush=True)
                if issue.related_span:
                    print(f"    related_span: {issue.related_span!r}", flush=True)
                print(f"    rationale:    {issue.rationale[:500]}", flush=True)

    print(f"\n{total_material} material issue(s) total across {len(controls)} control(s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
