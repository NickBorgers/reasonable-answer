#!/usr/bin/env bash
#
# Prints the proxy alias a CI agent runs on, given the agent name.
#
# Two stages pick their agent at *runtime* rather than in the workflow text: the issue
# resolver (an `/autoresolve` comment or an `agent:*` label chooses it) and the fixer (which
# follows whichever agent authored the PR, so it can resume that session). Neither can carry
# an inline `model:` the way the five reviewer roles in review-pipeline.yml do, because
# neither knows its agent until the job is already running.
#
# The mapping lives here, in one file, rather than being copied into both workflows. Two
# copies of a two-line map is exactly the kind of thing that drifts: the copy in the workflow
# nobody edited keeps pointing at a retired alias, and the failure surfaces as a proxy 404
# inside a container, a long way from the cause.
#
# Reviewer roles are deliberately NOT resolved here. Their pins are per-*role*, sized to the
# job each one does, and they belong in review-pipeline.yml next to the `agent:` they qualify
# — that adjacency is what makes the panel's composition reviewable in one place (QP3,
# D-ci-model-pinning). This script answers only "what does an agent default to", which is a
# different question from "what should this role run".
#
# Pure and offline: no git, no network, no token. Unit-tested by tests/test_ci_model_pins.py.

set -euo pipefail

AGENT="${1:-}"

case "$AGENT" in
  # Sol, not the cheaper Luna: both callers do open-ended work against a whole repository —
  # implementing an issue end to end, or reading a review panel's blockers and repairing the
  # branch. Neither is the bounded, check-the-text job that Luna is pinned for.
  codex)  echo "gpt-5.6-sol" ;;
  claude) echo "claude-opus-5" ;;
  *)
    echo "ci-agent-model: unknown agent '${AGENT}' (expected claude | codex)" >&2
    exit 1
    ;;
esac
