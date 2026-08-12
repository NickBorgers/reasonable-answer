#!/usr/bin/env python3
"""Screen one alias's upstream hosts on the real critique path.

**Which question this answers:** for an alias whose LiteLLM route can be served by more than
one upstream (an OpenRouter-style router that re-rolls the host per request, or any alias with
more than one entry in `provider.order`), which of those hosts actually deliver the closed
critique schema, and which mangle it?

**When to reach for it:** before pinning `provider.order` for a candidate model, and before
trusting any `ra audition` verdict for an alias that might route through more than one host. A
model that "fails" an audition because its router intermittently lands on a host that cannot
deliver the schema is not a model-quality finding — see the `nemotron-3-ultra` incident in
docs/model-evaluation.md, where the identical alias measured 4/4 clean pinned to one host and 4/4
broken pinned to another. Screening on a toy schema or a tool-calling loop is not a substitute:
critics never hold tools, and a toy schema can pass on a channel that still breaks under the
audition's actual, larger schema. This script drives the real `critique.critique_once` call
against real audition fixtures, so `result.failed` here is exactly what `run_assignment` counts
as a `schema_failure` and exactly what `judge()` compares against `max_schema_failure_rate`.

**This is an operator tool, not application code.** It is not imported by, or invoked from, the
package, the CLI, or any test — it is run by hand against a live paid proxy, the same posture as
`ra audition` itself, and it spends real proxy calls. Point `RA_CONFIG` at a roster (a scratch
copy is fine — see the "add to a scratch roster" step in docs/model-evaluation.md) that resolves
the alias under test before running it.

Usage::

    RA_CONFIG=/path/to/scratch-roster.yaml uv run python scripts/host_probe.py \\
        candidate-alias logic --hosts venice,together --repetitions 4

Provider pinning is injected at the SDK boundary, by monkeypatching `LLMClient._create` to add
`extra_body.provider.only` to every outgoing call — `LLMClient` itself has no notion of upstream
host, by design, so pinning cannot live inside it without teaching every caller about routing.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import os
import sys
from collections.abc import Callable

from reasonable_answer import audition as aud
from reasonable_answer.config import Config, ConfigError
from reasonable_answer.critique import critique_once
from reasonable_answer.llm import LLMClient
from reasonable_answer.taxonomy import Lens


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("alias", help="LiteLLM alias to probe (must resolve via $RA_CONFIG's roster).")
    parser.add_argument("lens", choices=[lens.value for lens in Lens], help="Which lens's fixtures to use.")
    parser.add_argument(
        "--hosts",
        required=True,
        help="Comma-separated candidate upstream hosts to pin one at a time, e.g. 'venice,together'.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=4,
        help="Calls per fixture per host (default: 4). Two fixtures are sampled, so a host's "
        "total call count is 2x this value.",
    )
    return parser.parse_args(argv)


def _pin_provider(real_create: Callable, host: str) -> Callable:
    """Wrap `LLMClient._create` so every call it makes is pinned to exactly one upstream host."""

    def create(alias: str, kwargs: dict, *, timeout: float | None = None):
        pinned_kwargs = {**kwargs, "extra_body": {"provider": {"only": [host]}}}
        return real_create(alias, pinned_kwargs, timeout=timeout)

    return create


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    lens = Lens(args.lens)
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    if not hosts:
        print("--hosts must name at least one host", file=sys.stderr)
        return 2

    config_path = os.environ.get("RA_CONFIG")
    if not config_path:
        print("set $RA_CONFIG to a roster that resolves this alias (a scratch copy is fine)", file=sys.stderr)
        return 2

    config = Config.load(config_path)
    fixtures = aud.load_fixtures(aud.DEFAULT_FIXTURE_DIR).for_lens(lens)
    # Two fixtures only — one planted, one control — enough to exercise both prompt shapes
    # without paying for a full audition's worth of calls per host.
    planted = next(f for f in fixtures if not f.is_control)
    control = next(f for f in fixtures if f.is_control)
    sample = (planted, control)

    client = LLMClient(config)
    identity = client.resolve_identities([args.alias])[args.alias]
    real_create = client._create
    exit_code = 0

    for host in hosts:
        # Pin BEFORE probing, not after. The probe is what pins the extraction mode for the
        # whole process, and a mode chosen through the unpinned router is a mode some other
        # host answered for — which would undercut the claim that each host is screened on
        # its own path. Re-probing per host also means a host that cannot produce structured
        # output at all is reported as such, rather than silently borrowing a neighbour's mode.
        client._create = _pin_provider(real_create, host)
        client._modes.pop(args.alias, None)
        try:
            mode = client.probe_structured_output(args.alias)
        except ConfigError as exc:
            print(f"{host:14s} could not probe structured output: {str(exc)[:100]}", flush=True)
            exit_code = 1
            continue
        outcomes: collections.Counter = collections.Counter()
        for fixture in sample:
            for _ in range(args.repetitions):
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
                    # Read from the loaded config rather than taking the parameter default,
                    # so this screen exercises the same critique contract `run_assignment`
                    # will. `require_verbatim_spans` is part of the audition cache identity
                    # (D-audition-rubric-identity) precisely because it changes what counts
                    # as a valid issue; a screen measuring the other regime would be
                    # screening for a different thing than the audition it precedes.
                    require_verbatim_spans=config.require_verbatim_spans,
                )
                if result.failed:
                    outcomes["FAILED: " + (result.failure_reason or "")[:60]] += 1
                else:
                    outcomes["ok"] += 1
        calls = sum(outcomes.values())
        failures = calls - outcomes["ok"]
        if failures:
            exit_code = 1
        # The mode is printed per host because it is per host: two hosts serving the same
        # alias can pin to different extraction modes, and that alone is worth seeing.
        print(f"{host:20s} mode={mode:12s} {failures}/{calls} failed  {dict(outcomes)}", flush=True)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
