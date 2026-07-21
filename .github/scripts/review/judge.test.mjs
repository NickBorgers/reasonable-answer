// Unit tests for the judge driver's IO layer (judge.mjs).
//
// aggregate.test.mjs covers the pure verdict function. This file covers the thin driver
// around it: how it reads reviewer artifacts off disk and, in particular, what it does
// when the reviewer-artifacts directory does not exist at all — the "every reviewer was
// skipped" case that previously crashed the judge with a raw ENOENT (issue #37).
//
// The driver is a script, not an exported function, so these tests run it as a
// subprocess with a scripted environment. Fully offline: no git, no network, no model.
//
// Run with: node --test .github/scripts/review/judge.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const JUDGE = fileURLToPath(new URL("./judge.mjs", import.meta.url));
const SHA = "a".repeat(40);

/** Fresh temp workspace per test. */
function workspace() {
  return mkdtempSync(join(tmpdir(), "judge-test-"));
}

/**
 * Run judge.mjs with the given env overlaid on a per-run verdict/output path.
 * Returns { status, verdict, ghOutput } where verdict is the parsed verdict JSON
 * (or null if the file was never written) and ghOutput is the GITHUB_OUTPUT contents.
 */
function runJudge(ws, env = {}) {
  const verdictPath = join(ws, "verdict.json");
  const ghOutputPath = join(ws, "gh-output");
  writeFileSync(ghOutputPath, "");
  const res = spawnSync("node", [JUDGE], {
    cwd: ws,
    encoding: "utf8",
    env: {
      ...process.env,
      REVIEWER_DIR: join(ws, "reviewer-artifacts"),
      VERDICT_OUTPUT_PATH: verdictPath,
      GITHUB_OUTPUT: ghOutputPath,
      ...env,
    },
  });
  return {
    status: res.status,
    stderr: res.stderr,
    verdict: existsSync(verdictPath)
      ? JSON.parse(readFileSync(verdictPath, "utf8"))
      : null,
    ghOutput: readFileSync(ghOutputPath, "utf8"),
  };
}

/** Write a reviewer artifact under reviewer-artifacts/reviewer-<role>-<sha>/. */
function writeReviewer(ws, role, artifact) {
  const dir = join(ws, "reviewer-artifacts", `reviewer-${role}-${SHA}`);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${role}-result.json`), JSON.stringify(artifact));
}

function reviewer(role, decision, blocking_issues = []) {
  return {
    schema_version: "1",
    role,
    reviewed_sha: SHA,
    cycle: 1,
    decision,
    summary: "",
    blocking_issues,
    non_blocking_notes: [],
    fix_suggestions: [],
    followup_issues: [],
  };
}

// ───────────────────── issue #37: the reviewer directory never appeared ─────────────────────

test("absent reviewer directory -> NO-GO pipeline_error, does not crash (issue #37)", () => {
  const ws = workspace();
  // Note: reviewer-artifacts is deliberately NOT created — this is the every-reviewer-skipped case.
  const r = runJudge(ws, { EXPECTED_ROLES: JSON.stringify(["invariant", "security", "test"]) });

  assert.equal(r.status, 0, `judge should exit 0, not crash. stderr:\n${r.stderr}`);
  assert.ok(r.verdict, "a verdict file must be written");
  assert.equal(r.verdict.verdict, "NO-GO");
  assert.equal(r.verdict.category, "pipeline_error");
  assert.match(r.verdict.reasons.join("\n"), /no reviewer artifacts/i);
  assert.match(r.verdict.reasons.join("\n"), /skipped/i);
  assert.deepEqual(r.verdict.unaddressed_blocker_ids, []);
  // finalize.yml branches on these step outputs, so they must be populated.
  assert.match(r.ghOutput, /verdict=NO-GO/);
  assert.match(r.ghOutput, /category=pipeline_error/);
});

// A present-but-empty directory is a distinct path (the dir exists, it just holds no
// artifacts). It must also fail closed, via aggregate()'s empty-set rule — confirming the
// absent-directory guard did not accidentally swallow the empty case too.
test("present-but-empty reviewer directory -> NO-GO pipeline_error", () => {
  const ws = workspace();
  mkdirSync(join(ws, "reviewer-artifacts"), { recursive: true });
  const r = runJudge(ws, { EXPECTED_ROLES: "[]" });

  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.ok(r.verdict);
  assert.equal(r.verdict.verdict, "NO-GO");
  assert.equal(r.verdict.category, "pipeline_error");
  assert.match(r.verdict.reasons.join("\n"), /No reviewer artifacts present/);
});

// ───────────────────── the normal paths still work ─────────────────────

test("all expected reviewers present and clear -> GO", () => {
  const ws = workspace();
  writeReviewer(ws, "invariant", reviewer("invariant", "approve"));
  writeReviewer(ws, "security", reviewer("security", "approve"));
  const r = runJudge(ws, { EXPECTED_ROLES: JSON.stringify(["invariant", "security"]) });

  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.equal(r.verdict.verdict, "GO");
  assert.equal(r.verdict.category, "go");
  assert.match(r.ghOutput, /verdict=GO/);
});

test("an expected role that produced no artifact -> NO-GO pipeline_error", () => {
  const ws = workspace();
  writeReviewer(ws, "invariant", reviewer("invariant", "approve"));
  // security was expected but never published.
  const r = runJudge(ws, { EXPECTED_ROLES: JSON.stringify(["invariant", "security"]) });

  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.equal(r.verdict.verdict, "NO-GO");
  assert.equal(r.verdict.category, "pipeline_error");
  assert.match(r.verdict.reasons.join("\n"), /security/);
});
