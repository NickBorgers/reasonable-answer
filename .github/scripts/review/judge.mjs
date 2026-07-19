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
// NO FIXER IN THIS PIPELINE
//
// There is no automated fix stage: a NO-GO goes back to a human. aggregate() still
// requires a fix-result, because its epoch checks (the fixer must have started from the
// same SHA the reviewers reviewed) are part of the fail-closed contract and are worth
// keeping intact. So this driver synthesizes the no-op fix result that describes what
// actually happened — nothing was changed, nothing was addressed:
//
//   { input_sha: reviewedSha, new_sha: reviewedSha, addressed: [], skipped: [] }
//
// This is deliberately preferred over relaxing aggregate() to tolerate a missing fix
// result. The epoch validation stays live, and introducing a real fixer later is a
// one-line change back to reading the artifact from disk.

import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { aggregate } from "./aggregate.mjs";

const REVIEWER_DIR = process.env.REVIEWER_DIR;
const VERDICT_OUTPUT_PATH = process.env.VERDICT_OUTPUT_PATH;

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

const fixResult = {
  schema_version: "1",
  input_sha: reviewedSha,
  new_sha: reviewedSha,
  addressed: [],
  skipped: [],
};

const verdict = aggregate(reviewers, fixResult);

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
