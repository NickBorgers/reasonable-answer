#!/usr/bin/env bash
#
# actionlint validates workflow syntax but does not resolve local references. A
# `uses: ./.github/actions/foo` pointing at a directory that was renamed, or a
# `uses: ./.github/workflows/bar.yml` that was never committed, lints clean and then
# fails at runtime as a `startup_failure` with no logs and no indication of the cause.
#
# This checks that every local `uses:` target actually exists, and that every script
# invoked from a workflow is present and executable.

set -euo pipefail

cd "$(dirname "$0")/.."

fail=0

note() {
  echo "  $*" >&2
  fail=1
}

echo "Checking local 'uses:' references..."
while IFS= read -r line; do
  file="${line%%:*}"
  ref="$(echo "$line" | sed -E 's/.*uses:[[:space:]]*//' | tr -d '"'"'"'' | sed 's/[[:space:]]*$//')"

  # Composite actions are referenced by directory and resolve to action.yml inside it.
  if [[ "$ref" == *.yml || "$ref" == *.yaml ]]; then
    target="$ref"
  else
    target="$ref/action.yml"
  fi

  target="${target#./}"

  # A job may check the repo out into a subdirectory (`actions/checkout` with `path:`)
  # and then reference an action through that path — `./main-checkout/.github/...`.
  # That resolves at runtime but not on disk, so also try the path with its first
  # segment stripped before calling it missing.
  stripped="${target#*/}"

  if [[ ! -f "$target" && ! -f "$stripped" ]]; then
    note "$file references '$ref' but neither $target nor $stripped exists"
  fi
# Anchored to the start of the line so that prose in a `#` comment mentioning `uses: ./`
# is not mistaken for a real reference.
done < <(grep -rn --include='*.yml' --include='*.yaml' -E '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*\./' .github/ || true)

echo "Checking scripts invoked from workflows..."
while IFS= read -r line; do
  file="${line%%:*}"
  # Match the whole path token, not just from `scripts/` onward — otherwise
  # `./.github/scripts/review/x.sh` is truncated to `scripts/review/x.sh` and reported
  # missing even though it is right there.
  script="$(echo "$line" | grep -oE '\.?/?([A-Za-z0-9_.-]+/)*scripts/[A-Za-z0-9_./-]+\.sh' | head -1)"
  [[ -n "$script" ]] || continue
  script="${script#./}"

  if [[ ! -f "$script" ]]; then
    note "$file invokes '$script' but it does not exist"
  elif [[ ! -x "$script" ]]; then
    note "$script is invoked from $file but is not executable (chmod +x)"
  fi
done < <(grep -rn --include='*.yml' --include='*.yaml' -E '(\./)?scripts/[A-Za-z0-9_./-]+\.sh' .github/ \
  | grep -vE ':[[:space:]]*#' || true)

if [[ "$fail" -ne 0 ]]; then
  echo "Workflow reference validation FAILED." >&2
  exit 1
fi

echo "All local workflow references resolve."
