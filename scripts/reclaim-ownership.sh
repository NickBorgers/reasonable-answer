#!/usr/bin/env bash
#
# Give a directory back to the runner user after the CI agent container wrote into it.
#
# The container writes as its own uid, so the runner cannot later delete or overwrite
# those files. This used to be `sudo chown`, which needs a runner with sudo. The
# isolated homelab runners (host-config-as-code, services/ci/tenant/) have no sudo at
# all, so the chown runs in a throwaway container instead. Root in that container may
# chown a bind-mounted directory to the runner's uid on rootful Docker and on the
# tenants' rootless Docker alike, so this works on either kind of runner.
#
# If the image is not present locally, no agent container ever ran from it, so there
# is nothing to reclaim. That case exits 0 rather than pulling, so a failed pull does
# not add a second, misleading failure to the job.
#
# Usage: scripts/reclaim-ownership.sh <image-ref> <dir>

set -euo pipefail

IMAGE="${1:?usage: reclaim-ownership.sh <image-ref> <dir>}"
DIR="${2:?usage: reclaim-ownership.sh <image-ref> <dir>}"

if [ ! -d "$DIR" ]; then
  exit 0
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "No local ${IMAGE}; nothing ran from it, so nothing to reclaim."
  exit 0
fi

docker run --rm --pull never --network none --user 0:0 --entrypoint chown \
  -v "${DIR}:/reclaim" "$IMAGE" -R "$(id -u):$(id -g)" /reclaim
