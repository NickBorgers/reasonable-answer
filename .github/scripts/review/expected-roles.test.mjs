// Run with: node --test .github/scripts/review/expected-roles.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { checkExpectedRoles } from "./expected-roles.mjs";

const reviewer = (role) => ({ role });

test("passes through when every selected role is present", () => {
  const got = checkExpectedRoles([reviewer("invariant"), reviewer("security")], ["invariant", "security"]);
  assert.equal(got, null);
});

test("a selected role with no artifact is a fail-closed pipeline_error", () => {
  const got = checkExpectedRoles([reviewer("invariant"), reviewer("security")], ["invariant", "security", "test"]);
  assert.equal(got.verdict, "NO-GO");
  assert.equal(got.category, "pipeline_error");
  assert.match(got.reasons[0], /test/);
});

test("names every missing role, not just the first", () => {
  const got = checkExpectedRoles([reviewer("invariant")], ["invariant", "security", "test"]);
  assert.match(got.reasons[0], /security/);
  assert.match(got.reasons[0], /test/);
});

test("an empty reviewer set with roles selected fails closed", () => {
  const got = checkExpectedRoles([], ["invariant"]);
  assert.equal(got.verdict, "NO-GO");
});

// The regression this module exists for: two reviewers cleared, a third failed and
// published nothing. Before the check, that returned GO.
test("surviving reviewers cannot clear a merge on behalf of a failed one", () => {
  const survivors = [reviewer("invariant"), reviewer("security")];
  assert.notEqual(checkExpectedRoles(survivors, ["invariant", "security", "test"]), null);
});

test("extra roles present but not selected are not an error", () => {
  assert.equal(checkExpectedRoles([reviewer("invariant"), reviewer("test")], ["invariant"]), null);
});

test("no expected roles supplied leaves the decision to aggregate()", () => {
  assert.equal(checkExpectedRoles([], []), null);
  assert.equal(checkExpectedRoles([], undefined), null);
});
