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
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra web

COPY src/ ./src/
COPY config/ ./config/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra web


FROM python:3.12-slim AS runtime

# ca-certificates is the only system dependency: everything else is stdlib
# (sqlite3 included) or a pure-python/manylinux wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# A fixed uid matters: run directories are created 0700, so a resumed run has to
# come back as the same user that wrote them.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin ra

COPY --from=build --chown=ra:ra /app/.venv /app/.venv
COPY --from=build --chown=ra:ra /app/src /app/src
COPY --from=build --chown=ra:ra /app/config /app/config

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RA_CONFIG=/etc/ra/roster.yaml \
    RA_MAX_CONCURRENT_RUNS=1

# The audit trail and the SQLite checkpoints live here. Mount a volume over it or
# every run — and every chance of resuming one — dies with the container.
RUN mkdir -p /data/runs /etc/ra \
    && cp /app/config/roster.yaml /etc/ra/roster.yaml \
    && chown -R ra:ra /data /etc/ra

WORKDIR /data
USER ra

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"

# Binds to 0.0.0.0 because a container has to. There is NO authentication in the
# app: publish this port only onto a tailnet or a trusted network.
ENTRYPOINT ["ra"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
