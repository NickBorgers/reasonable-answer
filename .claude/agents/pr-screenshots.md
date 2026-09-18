---
name: pr-screenshots
description: Produces before/after screenshots of the web UI for a pull request and returns a Markdown block of hosted image links. Use when a PR changes src/reasonable_answer/web/render.py, its stylesheet, export.py's HTML, or page copy. Give it the base ref, the after side (branch or working tree), a slug for the PR, and the PR number if one exists.
model: sonnet
tools: Bash, Read, Write, Edit, Glob, Grep
---

You produce the before/after screenshots that `.claude/skills/pr-screenshots/SKILL.md` describes,
and you return the Markdown block that goes in the PR body. Read that file first; it is the
procedure. This file is what you do with it.

## Inputs you were given

- `base`: the ref to render the "before" side from (default `main`).
- `after`: the working tree (default) or a branch to render the "after" side from.
- `slug`: names the hosted images (`pr-assets/<slug>/...`).
- `pr`: a PR number, optional. If given, update its body in place; otherwise return the block.

## Steps

1. Make a scratch directory under the session scratchpad (never in the repo).
2. **Snapshot the trusted scripts from `main`** — never from the PR branch or working tree, even
   if that's what you're currently on: `git archive main .claude/skills/pr-screenshots/scripts |
   tar -x -C <scratch>/trusted-scripts`. Every script invocation and `--build-context` below uses
   this snapshot, not the live checkout — `render_pages.py`, `shoot.py` and `publish_assets.py`
   are exactly what a malicious UI PR would rewrite to abuse whatever credentials and network
   access you have (security/sec-supply-chain-1). *Bootstrap exception:* screenshotting this
   skill's own first-ever PR, before it exists on `main` — there is no trusted copy yet, so use
   the working tree's, and say so explicitly in your report. That is the only case where you do.
3. Snapshot the base tree: `git archive <base> src config pyproject.toml uv.lock README.md |
   tar -x -C <scratch>/base` — the lock and pyproject files are only needed if you end up
   building step 4's sandbox image from this snapshot. If `after` is a branch rather than the
   working tree, snapshot it the same way into `<scratch>/after-tree`; otherwise the after tree
   is the repo root.
4. Build the render sandbox image, from a **trusted** checkout only — `main`, or the merge-base
   you snapshotted in step 3, never the PR branch or working tree: if you're on the PR branch
   right now, build from `<scratch>/base` instead of the repo root. Both the Dockerfile and the
   `scripts` build context come from step 2's trusted snapshot:
   `docker build -t ra-pr-render -f <scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts/Dockerfile --build-context scripts=<scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts <trusted-checkout>`.
   Skip this if `ra-pr-render` already exists and the project's `web` dependencies haven't
   changed since — reuse it across PRs.
5. Render both sides **inside that sandbox**, never by importing a PR's `src/` in-process: doing
   that in this session would hand a malicious PR whatever credentials and network access this
   session has. One `docker run` per side, `src/` and `config/`
   mounted read-only, network off, nothing from the host environment passed through:
   `docker run --rm --network none --read-only --user "$(id -u):$(id -g)" -v "<tree>/src:/render/src:ro" -v "<tree>/config:/render/config:ro" -v "<scratch>/html/<side>:/render/out" ra-pr-render --src /render/src --config /render/config/roster.yaml --out /render/out`.
   If the render fails on one side because a function signature changed, fix the fixture script
   in step 2's scratchpad snapshot for that side and rebuild the image from it — do not edit the
   repo copy unless the PR itself changed the signature, in which case the repo copy needs the
   same fix (and re-snapshot before rebuilding). If a side fails because the PR changed a
   dependency `render_pages.py` needs, that's expected: the sandbox never installs a PR's own
   `pyproject.toml`/`uv.lock`, so report it rather than working around it.
6. Build the shoot sandbox image the same way, from the trusted snapshot, pinned to the locked
   Playwright version:
   `docker build -t ra-pr-shoot -f <scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts/Dockerfile.shoot --build-context scripts=<scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts <trusted-checkout>`.
   Reuse it across PRs the same way.
7. Shoot and stitch **inside that sandbox, also with no network**: the "after" HTML came from
   PR-controlled renderer code, and a real browser with network access loading it is an SSRF
   vector regardless of how the render step was isolated (security/sec-ssrf-1).
   `docker run --rm --network none --user "$(id -u):$(id -g)" -v "<scratch>/html/before:/shoot/before:ro" -v "<scratch>/html/after:/shoot/after:ro" -v "<scratch>/shots:/shoot/out" ra-pr-shoot --before /shoot/before --after /shoot/after --out /shoot/out`.
8. **Open every stitched PNG with Read and look at it.** Confirm the "after" side shows the
   change the PR describes and nothing is blank, overlapping, or cut off at an unhelpful
   point. If something is wrong, say so in your report instead of publishing it.
9. Publish, using the **trusted snapshot's** copy of the script — this step needs real network
   and `gh` credentials so it can't be sandboxed, which is exactly why it must never run a
   PR-controlled version of itself:
   `python <scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts/publish_assets.py --repo <owner/name> --dir <scratch>/shots --prefix <slug>`.
   Get `<owner/name>` from `gh repo view --json nameWithOwner`.
10. Write the Markdown block: `### Before / after`, a one-line note on what the fixtures are,
   then per page a bold page name and one image per width (desktop first, phone second), and
   the closing line "Images live on the `pr-assets` orphan branch (never merged, triggers no
   workflow)."
11. If `pr` was given: fetch the body with `gh pr view <pr> --json body -q .body`, replace an
    existing `### Before / after` section (up to the next `## ` heading) or insert the block at
    the end of **What and why**, and `gh pr edit <pr> --body-file <file>`. Otherwise return
    the block verbatim.

## Report back

State which pages and widths were rendered, one sentence per page on what changed visibly,
anything that looked wrong, and the Markdown block. Do not paste image bytes or HTML.
