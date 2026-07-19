# reasonable-answer CI agent image.
#
# Used exclusively by the agent workflows (resolve-issue, review-reviewer). This is NOT
# the devcontainer image — `.devcontainer/devcontainer.json` serves human IDE workflows
# and is unrelated.
#
# Design:
#   * Credentials arrive as env vars, never baked in and never bind-mounted from a host.
#   * Runs as uid 1000 `ci`, with PATH reinstated in /etc/profile.d/ci-path.sh because
#     login shells re-source /etc/profile and reset PATH to the Debian default.
#   * Ships uv and Python. This is the point of having our own image rather than reusing
#     a sibling repo's: an agent working on this codebase must be able to run
#     `uv sync --frozen && uv run pytest -m "not live"` to check its own work before
#     pushing. The suite is offline, so that is a complete self-check.
#
# Built and pushed by `.github/workflows/ci-image.yml`.
#
# amd64 only — the homelab runners are X64, and the pinned actionlint tarball below is
# an amd64 artifact.

FROM ubuntu:24.04

ARG CLAUDE_CODE_VERSION
ARG CODEX_CLI_VERSION
ARG ACTIONLINT_VERSION
ARG NODE_MAJOR=20
ARG UV_VERSION=0.5
ARG PYTHON_VERSION=3.12

ENV DEBIAN_FRONTEND=noninteractive

# Core utilities only. gettext-base supplies envsubst, which the workflows use to
# substitute values into the prompt files.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git bash gnupg jq sudo gettext-base \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI from GitHub's own apt repo.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# actionlint — pinned release tarball, checksum verified. Agents edit workflows, so they
# need the same validator CI runs.
RUN set -eux; \
    archive="actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz"; \
    checksums="actionlint_${ACTIONLINT_VERSION}_checksums.txt"; \
    base_url="https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}"; \
    tmp_dir="$(mktemp -d)"; \
    curl -fsSL "${base_url}/${archive}" -o "${tmp_dir}/${archive}"; \
    curl -fsSL "${base_url}/${checksums}" -o "${tmp_dir}/${checksums}"; \
    (cd "${tmp_dir}" && grep " ${archive}$" "${checksums}" | sha256sum -c -); \
    tar -xzf "${tmp_dir}/${archive}" -C /usr/local/bin actionlint; \
    chmod +x /usr/local/bin/actionlint; \
    rm -rf "${tmp_dir}"

# Node 20 — the review pipeline's aggregation and its unit tests are plain .mjs run with
# `node --test`, and reviewer artifacts are schema-validated with npx ajv-cli.
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# uv, from the same published image the runtime Dockerfile uses.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# ubuntu:24.04 ships a pre-existing `ubuntu` user at uid 1000. Remove it to claim that
# uid: the self-hosted runner bind-mounts the workspace, and matching uids is what keeps
# file ownership sane across the container boundary.
RUN userdel -r ubuntu 2>/dev/null || true \
    && groupadd --gid 1000 ci \
    && useradd --uid 1000 --gid ci --shell /bin/bash --create-home ci \
    && echo 'ci ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ci \
    && chmod 0440 /etc/sudoers.d/ci

# Login shells re-source /etc/profile and reset PATH to the minimal Debian default,
# losing the agent binaries. Reinstate it for both login and non-login invocations.
RUN printf '%s\n' \
        'export PATH="/home/ci/.local/bin:$PATH"' \
        'export TMPDIR="/home/ci/.claude/tmp"' \
        > /etc/profile.d/ci-path.sh \
    && chmod 0644 /etc/profile.d/ci-path.sh

USER ci
WORKDIR /home/ci

ENV PATH=/home/ci/.local/bin:$PATH
# A TMPDIR on the same filesystem as the agent's working files: the default /tmp is a
# separate mount, and cross-device renames fail with EXDEV.
ENV TMPDIR=/home/ci/.claude/tmp
ENV UV_LINK_MODE=copy

RUN mkdir -p /home/ci/.claude/tmp /home/ci/.codex

# Without this, `claude -p` tries to run the interactive onboarding wizard and hangs
# forever in a non-interactive CI job.
RUN printf '{"hasCompletedOnboarding": true}\n' > /home/ci/.claude.json

# Pre-install the interpreter so the first `uv run` in a job doesn't pay for a download.
RUN uv python install "${PYTHON_VERSION}"

RUN curl -fsSL --retry 5 --retry-delay 2 --retry-connrefused https://claude.ai/install.sh \
        | bash -s -- "${CLAUDE_CODE_VERSION}"

RUN curl -fsSL --retry 5 --retry-delay 2 --retry-connrefused \
        "https://github.com/openai/codex/releases/download/rust-v${CODEX_CLI_VERSION}/install.sh" \
    | sh -s -- --release "${CODEX_CLI_VERSION}"

# Callers pass `-lc '...'`, so a login shell is the entrypoint.
ENTRYPOINT ["/bin/bash"]
