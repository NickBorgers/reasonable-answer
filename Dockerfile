# syntax=docker/dockerfile:1
#
# Two stages so the build tooling never ships. Stay on glibc (slim, not alpine):
# pydantic-core, jiter and orjson all publish manylinux wheels, so this image needs
# no compiler — on musl they would each need a Rust toolchain built from source.

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Build at the same path the venv will live at in the runtime stage: console
# scripts bake an absolute shebang, so a venv built at /src is unusable at /app.
WORKDIR /app

# Dependency layer first: it changes far less often than the source, so edits to
# src/ don't re-resolve the whole tree.
#
# `ingest` is here despite pyproject's note about keeping the image lean. It buys
# pypdf, which two paths need: a PDF seed, and — since sources.pdf — a cited PDF. A
# PDF is one of the commonest shapes an academic citation takes, and verification
# that cannot read one has a hole in it exactly where the good sources are.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra web --extra ingest

COPY src/ ./src/
COPY config/ ./config/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra web --extra ingest


FROM python:3.12-slim AS runtime

# ca-certificates is the only system dependency: everything else is stdlib
# (sqlite3 included) or a pure-python/manylinux wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# A fixed uid matters: run directories are created 0700, so a resumed run has to
# come back as the same user that wrote them.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin ra

# root:root deliberately — do NOT add --chown=ra:ra here, however natural it looks.
# The app runs as `ra`, so chowning its own code to `ra` would let a compromised process
# rewrite /app/src and the interpreter's own site-packages. Restarts are routine (compose
# sets restart: unless-stopped, and an interrupted run is re-enqueued at boot), so that
# turns a transient RCE into persistence. Root ownership costs nothing: the build stage
# leaves everything world-readable and the console scripts world-executable, which is all
# uid 10001 needs to run them. This holds even without a read-only rootfs, so it protects
# `docker run` by hand as much as it protects compose.
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY --from=build /app/config /app/config

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RA_CONFIG=/etc/ra/roster.yaml \
    RA_MAX_CONCURRENT_RUNS=1

# The audit trail and the SQLite checkpoints live here. Mount a volume over it or
# every run — and every chance of resuming one — dies with the container.
#
# roster.default.yaml, not roster.yaml: the baked copy has to boot with no network and
# no credential (scripts/smoke-test-image.sh), and roster.yaml carries this deployment's
# opt-ins — including refine, which makes the proxy a boot dependency. compose.yaml
# mounts roster.yaml over this path, so the deployment is unaffected.
RUN mkdir -p /data/runs /etc/ra \
    && cp /app/config/roster.default.yaml /etc/ra/roster.yaml \
    && chown -R ra:ra /data /etc/ra

WORKDIR /data
USER ra

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"

# Binds to 0.0.0.0 because a container has to. The app identifies callers by a header
# it trusts without verifying, so publish this port only where the proxy that sets that
# header is the sole way to reach it — loopback or a tailnet (docs/authentication.md).
ENTRYPOINT ["ra"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
