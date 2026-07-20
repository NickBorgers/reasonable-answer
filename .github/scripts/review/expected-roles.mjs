// Verifies that every reviewer role the classifier selected actually produced an
// artifact.
//
// Without this the pipeline is fail-OPEN in its most dangerous direction. A reviewer
// whose job fails — crashed agent, invalid artifact, expired runner — publishes nothing,
// so it simply does not appear in the set handed to aggregate(). aggregate() then
// reports that "all reviewers cleared" based on whichever ones happened to survive, and
// the merge gate goes green. A reviewer failing would *reduce* scrutiny rather than
// block, which is exactly backwards.
//
// Observed in practice: the `test` reviewer emitted an over-length summary, failed
// schema validation, and the run still returned GO on the strength of the other two.

/**
 * @param {Array<{role: string}>} reviewers  artifacts that were present and parseable
 * @param {string[]} expected                roles the classifier selected
 * @returns {null | {verdict: "NO-GO", category: "pipeline_error", reasons: string[], unaddressed_blocker_ids: []}}
 *          null when every expected role is present (caller proceeds to aggregate)
 */
export function checkExpectedRoles(reviewers, expected) {
  if (!Array.isArray(expected) || expected.length === 0) return null;

  const present = new Set((reviewers ?? []).map((r) => r?.role));
  const missing = expected.filter((role) => !present.has(role));
  if (missing.length === 0) return null;

  return {
    verdict: "NO-GO",
    category: "pipeline_error",
    reasons: [
      `Selected reviewer role(s) produced no valid artifact: ${missing.join(", ")}. ` +
        `A reviewer that fails must block the merge, not drop out of the review set.`,
    ],
    unaddressed_blocker_ids: [],
  };
}
