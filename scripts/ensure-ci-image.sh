#!/usr/bin/env bash
#
# Pull the CI agent image, with retries.
#
# A transient GHCR blip partway through a review cycle would otherwise fail one reviewer
# job, which fails the judge, which leaves the PR with a NO-GO that says nothing about
# the PR. Retrying here is much cheaper than re-running a whole cycle.
#
# Usage: scripts/ensure-ci-image.sh <image-ref>

set -euo pipefail

IMAGE="${1:?usage: ensure-ci-image.sh <image-ref>}"
ATTEMPTS="${PULL_ATTEMPTS:-4}"

for attempt in $(seq 1 "$ATTEMPTS"); do
  if docker pull --quiet "$IMAGE"; then
    echo "Pulled $IMAGE"
    exit 0
  fi
  if [ "$attempt" -lt "$ATTEMPTS" ]; then
    delay=$((attempt * 5))
    echo "Pull failed (attempt ${attempt}/${ATTEMPTS}); retrying in ${delay}s..." >&2
    sleep "$delay"
  fi
done

cat >&2 <<EOF
Failed to pull $IMAGE after ${ATTEMPTS} attempts.

If this is the first agent run in this repository, the image may not exist yet.
Dispatch the "CI Image" workflow once to build and publish it:

  gh workflow run ci-image.yml
EOF
exit 1
