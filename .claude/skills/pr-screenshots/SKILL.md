---
name: pr-screenshots
description: Put before/after screenshots of the web UI in a PR body. Use whenever a PR touches what a page looks like — src/reasonable_answer/web/render.py, the stylesheet inside it, export.py's HTML, or copy on any page — before opening or updating the PR. Also on "add screenshots to the PR", "show the UI change", or /pr-screenshots.
---

# Before/after screenshots for UI pull requests

A reviewer of a UI change needs to see it, not reconstruct it from a diff of f-strings. Every PR
that changes what a page looks like carries a before/after pair per affected page, rendered from
the **same fixture data** through the base renderer and the head renderer, at desktop and phone
widths.

## Do it by delegating

Launch the `pr-screenshots` subagent (defined in `.claude/agents/pr-screenshots.md`, runs on
Sonnet) and hand it:

- the base ref to compare against (default `main`, or the merge base of the PR branch),
- the PR branch or the working tree as the "after" side,
- a short slug for the PR (`plain-front-door`, `wider-tables`) — it names the hosted images,
- the PR number if one exists, so it can update the body in place.

It returns a Markdown block of image links. Paste that block into the PR body under a
`### Before / after` heading inside **What and why**, and say which fixture pages are shown.
If you are opening the PR yourself, run the agent first so the body is complete on creation.

## What the agent does (for when you have to do it by hand)

The scripts live in `.claude/skills/pr-screenshots/scripts/`; every command below is invoked as
`<repo-root>/.claude/skills/pr-screenshots/scripts/<name>.py`, run from the repository root (or
`cd` into that directory first and drop the prefix).

1. **A snapshot of the base tree**, without touching the checkout:
   `git archive <base> src config pyproject.toml uv.lock README.md | tar -x -C <scratch>/base`.
   `pyproject.toml`/`uv.lock`/`README.md` are only needed if step 2's sandbox image has to be
   built from this snapshot (working tree or PR branch checked out); they cost nothing to
   include otherwise.
2. **Render pages from both trees inside the sandbox** — no server, no proxy, no network, and no
   credentials. `--src` and `--after`'s tree can both be a PR's own head, and `render_pages.py`
   imports whatever is under `--src`; running that in-process in the same shell that has `gh`
   auth would hand a malicious PR everything it needs to exfiltrate it
   (security/sec-supply-chain-1). Instead:
   - Build the sandbox image **once, from `main`** (never from the PR branch — this is what
     keeps a malicious `pyproject.toml`/`uv.lock` from ever being installed):
     `docker build -t ra-pr-render -f .claude/skills/pr-screenshots/scripts/Dockerfile --build-context scripts=.claude/skills/pr-screenshots/scripts .`
     (run from a `main` checkout, or `git worktree` a clean copy of it). Reuse the image across
     PRs; only rebuild when the project's own web dependencies change.
   - Render each side with that image, `src/` and `config/` bind-mounted **read-only**, network
     fully off, and nothing from the host environment passed through:
     `docker run --rm --network none --read-only --user "$(id -u):$(id -g)" -v "<tree>/src:/render/src:ro" -v "<tree>/config:/render/config:ro" -v "<scratch>/html/<side>:/render/out" ra-pr-render --src /render/src --config /render/config/roster.yaml --out /render/out`.
   `render_pages.py` calls `render_index`, `render_run` and `render_report` directly with fixed
   fixture data (four runs, a two-round live timeline, a framed report with a review record).
   If the change adds a page or a state, add a fixture there — that file is the list of what
   gets reviewed. If the PR's own web dependencies changed (new import `render_pages.py` needs),
   the sandbox run fails cleanly with an ImportError; rebuild the image from `main` after the PR
   merges, not from the PR branch.
3. **Screenshot and stitch** with a throwaway venv populated from the lock-backed `screenshots`
   dependency group (`uv export --frozen --only-group screenshots --no-emit-project`, then
   `uv pip install --require-hashes` into that venv). Install the Chromium build selected by the
   locked Playwright (`python -m playwright install chromium`), then run:
   `python .claude/skills/pr-screenshots/scripts/shoot.py --before <scratch>/html/before --after <scratch>/html/after --out <scratch>/shots`.
   Produces one labelled before|after PNG per page per width (390 and 1100 by default).
4. **Look at every stitched image** before publishing. The point is a human-readable
   comparison; a broken fixture or an empty page is a finding, not an artifact to ship.
5. **Host the images** on the `pr-assets` orphan branch through the GitHub API, never through
   the local checkout: `python .claude/skills/pr-screenshots/scripts/publish_assets.py --repo <owner/name> --dir <scratch>/shots --prefix <slug>`.
   It prints `raw.githubusercontent.com` URLs. The branch is never merged and triggers no
   workflow (every push-triggered workflow here is `branches: [main]`). Re-running with the
   same prefix replaces the files.
6. **Write the block**: one `**Page**` heading and one `![...](url)` per width, phone and
   desktop, and a closing line saying where the images live.

## Rules

- Never commit PNGs to the PR branch; they go to `pr-assets` only.
- Never run the app or reach the proxy to get a screenshot; the renderer is a pure function
  of its inputs and that is what makes before/after comparable.
- The "before" side is always the base ref, not a guess at what the page used to look like.
- Scratch files go in the session scratchpad, not in the repo.
- **Never import a PR's `src/` in-process.** Step 2's sandbox container is what stands between a
  malicious PR and the credentials/network of whatever session is generating screenshots; running
  `render_pages.py` directly against a PR's `--src` outside that container defeats the whole
  point (security/sec-supply-chain-1).
