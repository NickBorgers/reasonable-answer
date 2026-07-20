#!/usr/bin/env node
// Judge driver for the review pipeline.
//
// Reads reviewer artifacts from REVIEWER_DIR, calls aggregate(), writes the verdict to
// VERDICT_OUTPUT_PATH, and emits `verdict=<GO|NO-GO>` as a step output.
//
// Pure file IO plus one aggregate() call. No git, no API, no model. Designed to run in a
// job with `permissions: contents: read`, checked out from `main`, so the judge
// structurally cannot push and a PR cannot modify the code that judges it.
//
// THE FIXER
//
// review-fixer.yml runs before this stage and may have pushed a commit. Its artifact is
// read from FIX_RESULT_PATH when present. When it is absent — no blockers to fix, the
// cycle cap forbade fixing, or the fixer failed — this driver synthesizes the no-op fix
// result that describes what actually happened:
//
//   { input_sha: reviewedSha, new_sha: reviewedSha, addressed: [], skipped: [] }
//
// Synthesizing is deliberately preferred over relaxing aggregate() to tolerate a missing
// fix result: the epoch checks (the fixer must have started from the same SHA the
// reviewers reviewed) are part of the fail-closed contract and stay live either way.
//
// Note what this judges. The reviewers read the PRE-fix tree, and so does this verdict —
// `addressed[]` only records which of their blockers the fixer claims to have closed. The
// fixed SHA is reviewed by its own cycle. Nothing here inspects the fixer's diff, which
// is why the fixer cannot clear its own work.

import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { aggregate } from "./aggregate.mjs";
import { checkExpectedRoles } from "./expected-roles.mjs";

const REVIEWER_DIR = process.env.REVIEWER_DIR;
const VERDICT_OUTPUT_PATH = process.env.VERDICT_OUTPUT_PATH;
// JSON array of the roles the classifier selected for this diff.
const EXPECTED_ROLES = process.env.EXPECTED_ROLES;

if (!REVIEWER_DIR || !VERDICT_OUTPUT_PATH) {
  console.error("judge.mjs: REVIEWER_DIR and VERDICT_OUTPUT_PATH must be set");
  process.exit(2);
}

// Reviewer artifacts arrive as `reviewer-<role>-<sha>/<role>-result.json`.
const reviewers = [];
for (const entry of readdirSync(REVIEWER_DIR, { withFileTypes: true })) {
  if (!entry.isDirectory()) continue;
  const dir = join(REVIEWER_DIR, entry.name);
  const candidate = readdirSync(dir).find((f) => f.endsWith("-result.json"));
  if (!candidate) continue;
  try {
    reviewers.push(JSON.parse(readFileSync(join(dir, candidate), "utf8")));
  } catch (err) {
    // A malformed artifact must not be silently dropped — dropping it could turn a
    // reviewer's blockers into a GO. Fail the job instead.
    console.error(`judge.mjs: could not parse ${join(dir, candidate)}: ${err.message}`);
    process.exit(1);
  }
}

// The SHA every reviewer claims to have reviewed. If they disagree, aggregate() rejects
// the set as a mixed epoch; deriving the synthetic fix result from the first artifact is
// safe because a disagreement is caught there rather than papered over here.
const reviewedSha = reviewers[0]?.reviewed_sha ?? "";

// The fixer's artifact, when there is one. FIX_RESULT_PATH is set only on runs where the
// fix stage ran and produced output; it legitimately does not exist when the reviewers
// raised no blockers, when the cycle cap forbade fixing, or when the fixer failed.
//
// Absence is NOT an error. It means "nothing was fixed", which is exactly the no-op
// result below. Every blocker then reads as unaddressed and the verdict is NO-GO — the
// fail-closed direction.
//
// A present-but-unparseable artifact is different, and does fail: silently falling back
// to the no-op there would let a broken fixer look identical to an idle one.
let fixResult = {
  schema_version: "1",
  input_sha: reviewedSha,
  new_sha: reviewedSha,
  addressed: [],
  skipped: [],
};

const FIX_RESULT_PATH = process.env.FIX_RESULT_PATH;
if (FIX_RESULT_PATH && existsSync(FIX_RESULT_PATH)) {
  try {
    fixResult = JSON.parse(readFileSync(FIX_RESULT_PATH, "utf8"));
    console.error(
      `judge.mjs: read fix result from ${FIX_RESULT_PATH} ` +
        `(mode=${fixResult.mode}, addressed=${(fixResult.addressed ?? []).length})`
    );
  } catch (err) {
    console.error(`judge.mjs: could not parse ${FIX_RESULT_PATH}: ${err.message}`);
    process.exit(1);
  }
}

// A reviewer that failed publishes no artifact, so it would simply be absent from the
// set and aggregate() would report that "all reviewers cleared" on the strength of the
// survivors. See expected-roles.mjs for why that is the wrong direction to fail in.
const missingRoles = checkExpectedRoles(reviewers, EXPECTED_ROLES ? JSON.parse(EXPECTED_ROLES) : []);
const verdict = missingRoles ?? aggregate(reviewers, fixResult);

console.log(JSON.stringify(verdict, null, 2));
writeFileSync(VERDICT_OUTPUT_PATH, JSON.stringify(verdict, null, 2) + "\n");

const ghOut = process.env.GITHUB_OUTPUT;
if (ghOut) {
  // A step output, not hashFiles() on the path: hashFiles silently returns empty for
  // anything outside the workspace, which would skip downstream steps without a word.
  writeFileSync(ghOut, `verdict=${verdict.verdict}\ncategory=${verdict.category}\n`, {
    flag: "a",
  });
}
