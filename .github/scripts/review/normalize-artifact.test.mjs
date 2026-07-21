// Run with: node --test .github/scripts/review/normalize-artifact.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { normalizeArtifact } from "./normalize-artifact.mjs";

const reviewerSchema = JSON.parse(
  readFileSync(new URL("./schema/reviewer-v1.json", import.meta.url), "utf8"),
);

const artifact = (over) => ({
  schema_version: "1",
  role: "invariant",
  reviewed_sha: "a".repeat(40),
  cycle: 1,
  decision: "approve",
  summary: "s",
  blocking_issues: [],
  non_blocking_notes: [],
  fix_suggestions: [],
  followup_issues: [],
  ...over,
});

test("a summary within the cap is returned untouched", () => {
  const data = artifact({ summary: "x".repeat(500) });
  const { truncations } = normalizeArtifact(reviewerSchema, data);
  assert.deepEqual(truncations, []);
  assert.equal(data.summary.length, 500);
});

// The exact regression: the invariant reviewer emitted 510 characters and the run died.
test("a summary over the cap is truncated to the cap", () => {
  const data = artifact({ summary: "x".repeat(510) });
  const { truncations } = normalizeArtifact(reviewerSchema, data);
  assert.equal(data.summary.length, 500);
  assert.deepEqual(truncations, [{ path: "/summary", from: 510, to: 500 }]);
});

test("truncation marks the cut so a reader knows text is missing", () => {
  const data = artifact({ summary: "x".repeat(600) });
  normalizeArtifact(reviewerSchema, data);
  assert.ok(data.summary.endsWith("..."));
});

// maxLength counts code points; slicing by UTF-16 units would leave 500 units but 500+
// code points and still fail ajv, or split a surrogate pair into a replacement char.
test("astral characters are counted as single characters", () => {
  const data = artifact({ summary: "🙂".repeat(600) });
  normalizeArtifact(reviewerSchema, data);
  assert.equal([...data.summary].length, 500);
  assert.ok(!data.summary.includes("�"));
});

test("caps inside arrays are enforced per entry", () => {
  const data = artifact({
    followup_issues: [
      { title: "short", body: "b" },
      { title: "t".repeat(120), body: "b" },
    ],
  });
  const { truncations } = normalizeArtifact(reviewerSchema, data);
  assert.equal(data.followup_issues[0].title, "short");
  assert.equal([...data.followup_issues[1].title].length, 80);
  assert.deepEqual(truncations.map((t) => t.path), ["/followup_issues/1/title"]);
});

// The whole point of keeping this narrow: everything that is not a length overflow must
// still reach ajv and still fail the run closed.
test("structural violations are left for the validator to reject", () => {
  const data = artifact({ decision: "not-a-decision", reviewed_sha: "nope", cycle: 0 });
  delete data.blocking_issues;
  normalizeArtifact(reviewerSchema, data);
  assert.equal(data.decision, "not-a-decision");
  assert.equal(data.reviewed_sha, "nope");
  assert.equal(data.cycle, 0);
  assert.ok(!("blocking_issues" in data));
});

test("a field the schema does not cap is never shortened", () => {
  const long = "m".repeat(5000);
  const data = artifact({
    blocking_issues: [{ id: "inv-1", severity: "high", message: long }],
  });
  const { truncations } = normalizeArtifact(reviewerSchema, data);
  assert.equal(data.blocking_issues[0].message, long);
  assert.deepEqual(truncations, []);
});

test("the fixer schema is covered by the same walk", () => {
  const fixerSchema = JSON.parse(
    readFileSync(new URL("./schema/fix-result-v1.json", import.meta.url), "utf8"),
  );
  const data = { summary: "x".repeat(900) };
  const { truncations } = normalizeArtifact(fixerSchema, data);
  assert.equal([...data.summary].length, 500);
  assert.equal(truncations.length, 1);
});

test("a missing optional field is not invented", () => {
  const data = artifact();
  delete data.followup_issues;
  normalizeArtifact(reviewerSchema, data);
  assert.ok(!("followup_issues" in data));
});
