// Run with: node --test .github/scripts/review/reviewer-guard.test.mjs
//
// Tests the reviewer guard in `.github/workflows/review-reviewer.yml` — the step that
// decides whether a role is cleared to read a SHA. Its PR Validation wait was the subject
// of issue #157: the budget (10 polls × 15s = 2m30s) was shorter than PR Validation itself
// (2m15s–3m12s measured), and the loop slept after its final poll and then gave up without
// looking again. PR #156 went green six seconds inside that dead interval, every reviewer
// skipped, and the pipeline finalized a `pipeline_error` NO-GO on a healthy PR.
//
// The script is inline `github-script`, so it is not importable. Rather than restate its
// logic here — which would let the deployed copy drift away from the tested one — this
// extracts the block out of the workflow and RUNS it, the same approach
// tests/test_ci_model_pins.py takes with the composite's inline shell guard.
//
// `setTimeout` is passed in as a parameter, so the body's own
// `new Promise((r) => setTimeout(r, ...))` binds to the stub instead of the global: the full
// ten-minute budget is exercised in milliseconds, and every sleep it asks for is recorded.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const WORKFLOW = new URL("../../workflows/review-reviewer.yml", import.meta.url);
const SHA = "a735e39a735e39a735e39a735e39a735e39a735e";

/** The guard's `script: |` block, dedented, exactly as GitHub would evaluate it. */
function guardScript() {
  const lines = readFileSync(WORKFLOW, "utf8").split("\n");
  const starts = lines.flatMap((line, i) => (/^\s*script: \|\s*$/.test(line) ? [i] : []));
  assert.equal(
    starts.length,
    1,
    "review-reviewer.yml should hold exactly one inline github-script block; " +
      "this extractor picks the wrong one otherwise",
  );
  const start = starts[0];
  const indent = lines[start + 1].match(/^\s*/)[0];
  assert.ok(indent.length > 0, "the script block should be indented under `script: |`");

  const body = [];
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i].trim() === "") {
      body.push("");
      continue;
    }
    if (!lines[i].startsWith(indent)) break;
    body.push(lines[i].slice(indent.length));
  }
  return body.join("\n");
}

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const GUARD = new AsyncFunction("github", "context", "core", "process", "setTimeout", guardScript());

/**
 * Drive the guard once.
 *
 * `gate(poll)` returns the `PR Validation Required` check run as the API would report it on
 * that poll (1-based), or null for "the check is not there yet".
 */
async function runGuard({ gate, headSha = SHA, headRepo = "NickBorgers/reasonable-answer" } = {}) {
  const polls = [];
  const sleeps = [];
  const logs = { info: [], warning: [], failed: [] };
  let ok;

  const github = {
    rest: {
      pulls: {
        get: async () => ({
          data: {
            head: { sha: headSha, repo: { full_name: headRepo } },
            author_association: "OWNER",
          },
        }),
      },
      checks: {
        listForRef: async () => {
          polls.push(polls.length + 1);
          const run = gate(polls.length);
          return { data: { check_runs: run ? [run] : [] } };
        },
      },
    },
  };

  await GUARD(
    github,
    { repo: { owner: "NickBorgers", repo: "reasonable-answer" } },
    {
      info: (m) => logs.info.push(m),
      warning: (m) => logs.warning.push(m),
      setFailed: (m) => logs.failed.push(m),
      setOutput: (name, value) => {
        if (name === "ok") ok = value;
      },
    },
    { env: { PR_NUMBER: "156", REVIEWED_SHA: SHA } },
    (resume, ms) => {
      sleeps.push(ms);
      resume();
    },
  );

  return { ok, polls: polls.length, sleeps, logs };
}

const passing = { name: "PR Validation Required", status: "completed", conclusion: "success" };
const running = { name: "PR Validation Required", status: "in_progress", conclusion: null };

/** in_progress for the first `n` polls, then `after`. */
const settlesAfter = (n, after = passing) => (poll) => (poll <= n ? running : after);

// The budget the deployed script actually carries, learned by driving it rather than copied
// out of the source — so every assertion below is about the shipped arithmetic.
const timedOut = await runGuard({ gate: () => running });

test("the wait budget clears PR Validation's measured range", () => {
  const budgetMs = timedOut.sleeps.reduce((a, b) => a + b, 0);
  // The four consecutive runs recorded in issue #157 took 2m15s, 3m12s, 2m57s and 3m03s.
  // Anything under about eight minutes is back inside the range where a slow-but-healthy
  // validation run loses the race, which is the whole defect.
  assert.ok(
    budgetMs >= 8 * 60_000,
    `guard waits only ${budgetMs / 60_000} minutes; PR Validation has been measured at 3m12s`,
  );
});

test("validation that finishes past the old 2m30s budget still clears the guard", async () => {
  // Twelve polls at 15s is 3m00s — inside the observed range, past the old ten-poll budget.
  const { ok, polls } = await runGuard({ gate: settlesAfter(12) });
  assert.equal(ok, "true");
  assert.equal(polls, 13);
  assert.ok(polls > 10, "the old budget would have given up before this poll");
});

test("the guard polls after its last sleep, so no interval is dead time", async () => {
  // The exact PR #156 shape: the gate goes green during what used to be the final,
  // unobserved sleep. One sleep fewer than polls means every interval is followed by a look.
  assert.equal(
    timedOut.sleeps.length,
    timedOut.polls - 1,
    "a sleep with no poll after it is an interval the gate can go green in unseen",
  );

  const { ok, polls } = await runGuard({ gate: settlesAfter(timedOut.polls - 1) });
  assert.equal(ok, "true", "validation that completes on the last permitted poll must be seen");
  assert.equal(polls, timedOut.polls);
});

test("a genuine timeout still refuses, and says what it waited", () => {
  assert.equal(timedOut.ok, "false");
  assert.equal(timedOut.logs.failed.length, 0, "a timeout is a skip, not a job failure");
  const warning = timedOut.logs.warning.join("\n");
  assert.match(warning, /did not complete/);
  // Derived from the budget, not hardcoded: the old message said "/10" in a loop that no
  // longer had to run ten times, and a stale count is how the arithmetic hides.
  assert.match(warning, new RegExp(String(timedOut.polls)));
});

test("a red gate refuses immediately, without spending the wait", async () => {
  const failed = { name: "PR Validation Required", status: "completed", conclusion: "failure" };
  const { ok, polls, sleeps, logs } = await runGuard({ gate: () => failed });
  assert.equal(ok, "false");
  assert.equal(polls, 1);
  assert.deepEqual(sleeps, []);
  assert.match(logs.warning.join("\n"), /concluded 'failure'/);
});

test("a gate that concludes non-success after a wait refuses too", async () => {
  const cancelled = { name: "PR Validation Required", status: "completed", conclusion: "cancelled" };
  const { ok } = await runGuard({ gate: settlesAfter(3, cancelled) });
  assert.equal(ok, "false");
});

test("an absent gate is never mistaken for a passing one", async () => {
  // A PR that conflicts with its base fires no `pull_request` event, so the check never
  // appears at all. Waiting out the budget and refusing is correct; clearing is not.
  const { ok, polls } = await runGuard({ gate: () => null });
  assert.equal(ok, "false");
  assert.equal(polls, timedOut.polls);
});

test("unrelated check runs do not stand in for the gate", async () => {
  const { ok } = await runGuard({
    gate: () => ({ name: "Tests", status: "completed", conclusion: "success" }),
  });
  assert.equal(ok, "false");
});

test("a superseded SHA is refused before the wait begins", async () => {
  // The cheap refusals must stay in front of the poll loop: there is no point waiting ten
  // minutes for validation on a commit that is no longer the PR head.
  const { ok, polls } = await runGuard({ gate: () => passing, headSha: "b".repeat(40) });
  assert.equal(ok, "false");
  assert.equal(polls, 0);
});

test("a fork PR fails the job before the wait begins", async () => {
  const { ok, polls, logs } = await runGuard({ gate: () => passing, headRepo: "someone/fork" });
  assert.equal(ok, "false");
  assert.equal(polls, 0);
  assert.match(logs.failed.join("\n"), /fork PR/);
});
