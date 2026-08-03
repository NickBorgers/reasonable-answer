#!/usr/bin/env bash
# Clone -> open in devcontainer -> `make test` works. Nothing else required.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv sync --frozen 2>/dev/null || uv sync

# Local/devcontainer parity with the CI pipeline's merge steps: a human resolving a
# docs/decisions.md conflict here gets the same append-only fast path (D-decisions-merge-driver).
# The helper proves the driver runs before registering it — an unregistered driver is the
# plain-git behaviour, a broken registered one is a conflict with no markers to see.
./scripts/register_decisions_driver.sh "$(pwd)"

echo
echo "reasonable-answer is ready."
echo "  make test    # full offline suite — no proxy, no API keys"
echo "  make doctor  # check the LiteLLM proxy and roster health (needs network)"
echo
