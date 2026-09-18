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
2. Snapshot the base tree: `git archive <base> src config | tar -x -C <scratch>/base`. If
   `after` is a branch rather than the working tree, snapshot it the same way into
   `<scratch>/after-tree`; otherwise the after tree is the repo root.
3. Make sure the project venv exists: `uv sync --frozen --extra web --group dev`.
4. Render both sides with the **project** interpreter, one command per side, using absolute
   paths (the worktree hook refuses shell variables in front of `python`):
   `.venv/bin/python .claude/skills/pr-screenshots/scripts/render_pages.py --src <tree>/src --config <tree>/config/roster.yaml --out <scratch>/html/<side>`.
   If the render fails on one side because a function signature changed, fix the fixture
   script for that side in the scratchpad — do not edit the repo copy unless the PR itself
   changed the signature, in which case the repo copy needs the same fix.
5. Make a throwaway venv for the browser: `uv venv <scratch>/venv`, then export the dedicated
   dependency group from the repository lock and install that exact set into the venv:
   `uv export --frozen --only-group screenshots --no-emit-project --output-file <scratch>/screenshots.txt`,
   `uv pip install --python <scratch>/venv/bin/python --require-hashes -r <scratch>/screenshots.txt`, then
   `<scratch>/venv/bin/python -m playwright install chromium`. Reuse it if it already exists.
6. Shoot and stitch:
   `<scratch>/venv/bin/python .claude/skills/pr-screenshots/scripts/shoot.py --before <scratch>/html/before --after <scratch>/html/after --out <scratch>/shots`.
7. **Open every stitched PNG with Read and look at it.** Confirm the "after" side shows the
   change the PR describes and nothing is blank, overlapping, or cut off at an unhelpful
   point. If something is wrong, say so in your report instead of publishing it.
8. Publish: `<scratch>/venv/bin/python .claude/skills/pr-screenshots/scripts/publish_assets.py --repo <owner/name> --dir <scratch>/shots --prefix <slug>`.
   Get `<owner/name>` from `gh repo view --json nameWithOwner`.
9. Write the Markdown block: `### Before / after`, a one-line note on what the fixtures are,
   then per page a bold page name and one image per width (desktop first, phone second), and
   the closing line "Images live on the `pr-assets` orphan branch (never merged, triggers no
   workflow)."
10. If `pr` was given: fetch the body with `gh pr view <pr> --json body -q .body`, replace an
    existing `### Before / after` section (up to the next `## ` heading) or insert the block at
    the end of **What and why**, and `gh pr edit <pr> --body-file <file>`. Otherwise return
    the block verbatim.

## Report back

State which pages and widths were rendered, one sentence per page on what changed visibly,
anything that looked wrong, and the Markdown block. Do not paste image bytes or HTML.
