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

The scripts live in `.claude/skills/pr-screenshots/scripts/`. **Every one of them is run from a
trusted snapshot, never from whatever happens to be checked out** — the whole point of this
procedure is generating screenshots *for* a PR, so the working tree can be that PR's own branch,
and these scripts are exactly what a malicious UI PR would modify to abuse the credentials and
network access of whoever runs `/pr-screenshots` on it (security/sec-supply-chain-1: this covers
`render_pages.py`, `shoot.py` and `publish_assets.py` alike — none of the three is safe to invoke
from a PR-controlled checkout).

0. **Snapshot the trusted scripts, once per session**, from `main` — never from the PR branch or
   working tree: `git archive main .claude/skills/pr-screenshots/scripts | tar -x -C
   <scratch>/trusted-scripts`. Every `--build-context scripts=...` and every direct script
   invocation below points at `<scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts`,
   not the live checkout. *Bootstrap exception:* the one time this skill screenshots its own
   first-ever PR (before it exists on `main`), there is no trusted copy yet — use the working
   tree's copy for that one case only, and say so explicitly in the report.
1. **A snapshot of the base tree**, without touching the checkout:
   `git archive <base> src config pyproject.toml uv.lock README.md | tar -x -C <scratch>/base`.
   `pyproject.toml`/`uv.lock`/`README.md` are only needed if step 2's sandbox image has to be
   built from this snapshot (working tree or PR branch checked out); they cost nothing to
   include otherwise.
2. **Render pages from both trees inside the sandbox** — no server, no proxy, no network, and no
   credentials. `--src` and `--after`'s tree can both be a PR's own head, and `render_pages.py`
   imports whatever is under `--src`; running that in-process in the same shell that has `gh`
   auth would hand a malicious PR everything it needs to exfiltrate it. Instead:
   - Build the sandbox image **once, from `main`** (never from the PR branch — this is what
     keeps a malicious `pyproject.toml`/`uv.lock` from ever being installed), using the trusted
     scripts snapshot from step 0 for both the Dockerfile and `render_pages.py`:
     `docker build -t ra-pr-render -f <scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts/Dockerfile --build-context scripts=<scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts .`
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
3. **Screenshot and stitch, also inside a network-isolated sandbox.** The "after" HTML was
   produced by PR-controlled renderer code; a malicious page can emit a form, an `<img src>` or a
   script that reaches a public, loopback, or private-network target the moment a real browser
   with normal network loads it (security/sec-ssrf-1) — sandboxing only the render step does not
   stop that. Build once, from the trusted scripts snapshot, pinned to the locked Playwright
   version (`pyproject.toml`'s `screenshots` group / `uv.lock`):
   `docker build -t ra-pr-shoot -f <scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts/Dockerfile.shoot --build-context scripts=<scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts .`
   Then, one run per invocation, HTML mounted read-only, shots out read-write, network off:
   `docker run --rm --network none --user "$(id -u):$(id -g)" -v "<scratch>/html/before:/shoot/before:ro" -v "<scratch>/html/after:/shoot/after:ro" -v "<scratch>/shots:/shoot/out" ra-pr-shoot --before /shoot/before --after /shoot/after --out /shoot/out`.
   Produces one labelled before|after PNG per page per width (390 and 1100 by default).
4. **Look at every stitched image** before publishing. The point is a human-readable
   comparison; a broken fixture or an empty page is a finding, not an artifact to ship.
5. **Host the images** on the `pr-assets` orphan branch through the GitHub API. This step needs
   real network and `gh` credentials — it can't be sandboxed the way rendering and shooting are —
   so what makes it safe is that it never touches PR-controlled input at all: it only uploads
   already-rendered PNGs, and it runs the **trusted snapshot's** copy of the script, not the
   working tree's (a malicious PR could otherwise rewrite this file itself to do something else
   with those credentials): `python <scratch>/trusted-scripts/.claude/skills/pr-screenshots/scripts/publish_assets.py --repo <owner/name> --dir <scratch>/shots --prefix <slug>`.
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
- **Never run any of `render_pages.py`, `shoot.py` or `publish_assets.py` from a PR-controlled
  checkout.** Step 0's trusted snapshot from `main` is what stands between a malicious PR and the
  credentials, and network access of whoever is generating screenshots for it; invoking the
  working tree's copy of any of the three defeats the point (security/sec-supply-chain-1).
- **Never screenshot PR-controlled HTML with a browser that has network access.** Step 3's
  sandbox is what stops a malicious renderer from turning a screenshot into an SSRF probe
  (security/sec-ssrf-1).
