#!/usr/bin/env bash
#
# Prove a built image actually runs. Used both pre-merge (on the locally built image)
# and post-push (on the published image, pulled back by digest).
#
# Usage: scripts/smoke-test-image.sh <image-ref>
#
# Deliberately needs no volume, no config mount, and no network: /healthz is a static
# response, the roster is baked into the image at /etc/ra/roster.yaml via RA_CONFIG, and
# the image creates and chowns /data/runs itself. If any of those stop being true, this
# script starts failing — which is the point.

set -euo pipefail

IMAGE="${1:?usage: smoke-test-image.sh <image-ref>}"
NAME="ra-smoke-$$"
PORT="${SMOKE_PORT:-18080}"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Console script is wired"
# Catches a broken entrypoint or a wheel that shipped without its console script — a
# failure the HTTP check below would never reach, because the container would not start.
docker run --rm --entrypoint ra "$IMAGE" --help >/dev/null
echo "    ok"

echo "==> Starting container"
docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:8080" "$IMAGE" >/dev/null

echo "==> Waiting for /healthz"
for i in $(seq 1 30); do
  if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "Container exited before becoming ready. Logs:" >&2
    docker logs "$NAME" >&2 || true
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" 2>/dev/null | grep -qi ok; then
    echo "    ok after ${i}s"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Timed out waiting for /healthz. Logs:" >&2
    docker logs "$NAME" >&2 || true
    exit 1
  fi
  sleep 1
done

# The image declares a HEALTHCHECK. Nothing else in CI would ever notice if that
# instruction were broken, because a broken healthcheck does not stop the container —
# it just leaves orchestrators unable to tell whether the service is up.
echo "==> Waiting for the declared HEALTHCHECK to report healthy"
for i in $(seq 1 60); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NAME")"
  case "$status" in
    healthy)
      echo "    ok after ${i}s"
      break
      ;;
    unhealthy)
      echo "HEALTHCHECK reported unhealthy. Probe output:" >&2
      docker inspect --format '{{range .State.Health.Log}}{{.Output}}{{end}}' "$NAME" >&2
      exit 1
      ;;
    none)
      echo "Image declares no HEALTHCHECK — expected one." >&2
      exit 1
      ;;
  esac
  if [ "$i" -eq 60 ]; then
    echo "Timed out waiting for healthy (last status: $status)." >&2
    docker inspect --format '{{range .State.Health.Log}}{{.Output}}{{end}}' "$NAME" >&2
    exit 1
  fi
  sleep 1
done

echo "==> Runs as a non-root user"
uid="$(docker run --rm --entrypoint id "$IMAGE" -u)"
if [ "$uid" = "0" ]; then
  echo "Image runs as root; expected the unprivileged ra user." >&2
  exit 1
fi
echo "    uid $uid"

echo
echo "Smoke test passed: $IMAGE"
