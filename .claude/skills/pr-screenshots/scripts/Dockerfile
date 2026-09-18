# syntax=docker/dockerfile:1
#
# Sandbox for step 2 of SKILL.md: rendering a PR's own `src/reasonable_answer` against fixed
# fixture data. That step imports whatever `--src` points at, so a malicious UI PR could ship
# module-level code in `src/` that reads credentials or reaches the network from inside the
# process running it (security/sec-supply-chain-1). This image never installs from a PR's own
# `pyproject.toml`/`uv.lock` — only from the **base** ref's, baked in at build time — and `src/`
# and `config/` are bind-mounted read-only at *run* time (`docker run --network none`), never
# copied in here. So neither a malicious dependency nor a malicious import can do anything: the
# container gets no credentials (nothing is passed through from the host environment) and no
# network (`--network none`), and the only thing that varies per render is the read-only mount.
#
# Build once from a trusted checkout (`main`, or the PR's merge-base — never the PR branch
# itself) and reuse the resulting image for both the "before" and "after" render. The repo's
# root `.dockerignore` excludes `.claude/` from the main build context (deliberately — the
# runtime image should not ship agent tooling), so `render_pages.py` comes from a second,
# explicit build context instead of the default one:
#   docker build -t ra-pr-render -f .claude/skills/pr-screenshots/scripts/Dockerfile \
#     --build-context scripts=.claude/skills/pr-screenshots/scripts .
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra web

FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10001 render
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1

# Trusted script, baked in from the `scripts` build context (see the `docker build` command
# above) — never from the mounted, PR-controlled tree below.
COPY --from=scripts render_pages.py /render/render_pages.py

# Default to a fixed non-root user so a forgotten `--user` at run time fails closed (it can't
# write the bind-mounted --out directory unless that directory is world-writable) rather than
# running as root; the documented `docker run` passes `--user "$(id -u):$(id -g)"` to make
# output ownership match the host instead.
USER render
WORKDIR /render
ENTRYPOINT ["python", "/render/render_pages.py"]
