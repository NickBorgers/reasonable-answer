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
#
# The last two checks are the exception and do take a volume: they assert the runtime
# immutability posture (root-owned /app, and a clean boot under --read-only), which cannot
# be observed from a container running with Docker's defaults.

set -euo pipefail

IMAGE="${1:?usage: smoke-test-image.sh <image-ref>}"
NAME="ra-smoke-$$"
PORT="${SMOKE_PORT:-18080}"

# The hardened run below gets its own container, port and volume so it can be asserted
# without weakening the bare run above it — see the read-only section for why both exist.
RO_NAME="ra-smoke-ro-$$"
RO_PORT="${SMOKE_RO_PORT:-$((PORT + 1))}"
RO_VOLUME="ra-smoke-runs-$$"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker rm -f "$RO_NAME" >/dev/null 2>&1 || true
  docker volume rm "$RO_VOLUME" >/dev/null 2>&1 || true
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

# Every route but /healthz needs an identity (D-identity-header), and this container is unfronted: the
# image ships no `auth.dev_identity`, so nothing here is reachable without a header. That
# is worth asserting on the image itself and not only in pytest — a Dockerfile that baked
# in a dev identity would open the whole app, and would look like a working image.
echo "==> Requests with no identity are refused"
# No -f: a 403 is the expected answer here, so the status code is the assertion.
code="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/")"
if [ "$code" != "403" ]; then
  echo "Expected 403 for an unauthenticated request to /, got ${code}." >&2
  exit 1
fi
echo "    ok"

# The icons, the manifest and the service worker are the only non-Python files the app
# serves, so they are the only thing here that can be lost by a packaging change. pytest
# runs from a checkout and would never notice; this is the check that would. Fetched with
# an identity header, which is what the tunnel or `tailscale serve` supplies in a real
# deployment — the check above is the one that proves the gate is still there.
echo "==> Installable-app assets shipped in the image"
AS_SMOKE=(-H "Tailscale-User-Login: smoke@example.invalid")
if ! curl -fsS "${AS_SMOKE[@]}" "http://127.0.0.1:${PORT}/manifest.webmanifest" |
  grep -q '"standalone"'; then
  echo "Manifest missing or not a standalone app manifest." >&2
  exit 1
fi
for path in /sw.js /offline.html /static/icons/icon-512.png /static/icons/apple-touch-icon.png; do
  if ! curl -fsS -o /dev/null "${AS_SMOKE[@]}" "http://127.0.0.1:${PORT}${path}"; then
    echo "Asset $path did not make it into the image." >&2
    exit 1
  fi
done
echo "    ok"

echo "==> Runs as a non-root user"
uid="$(docker run --rm --entrypoint id "$IMAGE" -u)"
if [ "$uid" = "0" ]; then
  echo "Image runs as root; expected the unprivileged ra user." >&2
  exit 1
fi
echo "    uid $uid"

# The app must not be able to rewrite its own code. /app is root-owned while the process
# runs as uid 10001, so a compromised run cannot edit src/ or the venv's site-packages and
# have the next restart execute it — and restarts are routine here, since an interrupted
# run is re-enqueued at boot. `test -w` runs as the image's USER, so this is the runtime
# uid's own view. Asserted on the image rather than in compose, because it holds for a
# plain `docker run` too.
echo "==> The app cannot write its own code"
if ! docker run --rm --entrypoint sh "$IMAGE" -c \
  'test ! -w /app/src && test ! -w /app/.venv && test ! -w /app/config'; then
  echo "Some of /app is writable by the runtime user." >&2
  echo "Almost certainly a '--chown=ra:ra' that came back on a COPY in the Dockerfile." >&2
  exit 1
fi
echo "    ok"

# Booting under the deployment's actual security posture. The container above runs with
# Docker's defaults and proves the image needs no volume and no config mount; this one
# proves the same image comes up with a read-only root filesystem, no capabilities and no
# privilege escalation. Both matter, so neither run is folded into the other.
#
# A named volume rather than `--tmpfs /data/runs`: a volume inherits the image's ownership
# of that directory (uid 10001), which is the deployed shape and what the app's startup
# writability probe checks. A bare tmpfs mounts root-owned and would fail startup for a
# reason that has nothing to do with what is being tested here.
echo "==> Boots with a read-only root filesystem"
docker run -d --name "$RO_NAME" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  -v "${RO_VOLUME}:/data/runs" \
  -p "127.0.0.1:${RO_PORT}:8080" "$IMAGE" >/dev/null

for i in $(seq 1 30); do
  if ! docker ps --format '{{.Names}}' | grep -qx "$RO_NAME"; then
    echo "Read-only container exited before becoming ready. Logs:" >&2
    docker logs "$RO_NAME" >&2 || true
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:${RO_PORT}/healthz" 2>/dev/null | grep -qi ok; then
    echo "    ok after ${i}s"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Timed out waiting for /healthz under --read-only. Logs:" >&2
    docker logs "$RO_NAME" >&2 || true
    exit 1
  fi
  sleep 1
done

echo
echo "Smoke test passed: $IMAGE"
