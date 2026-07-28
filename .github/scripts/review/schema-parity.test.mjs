// Guards the one schema rule that has broken the pipeline four times.
//
// Run with: node --test .github/scripts/review/schema-parity.test.mjs
//
// `blocking_issues[]` and `non_blocking_notes[]` describe the SAME findings at different
// confidence — the prompts' 0.7 ladder is literally "same finding, other array". Both items
// set `additionalProperties: false`, so a field a reviewer puts on a note that only the
// blocker admits fails validation, and a failed artifact loses EVERY finding that reviewer
// had, not just the offending one. It then reads downstream as "no reviewer artifact", which
// the judge fails closed on as `pipeline could not trust its inputs` — a diagnostic that
// points nowhere near the actual cause.
//
// That has now happened with `id`, `decision_ref` (#29), `category` (#35), and `confidence`
// (#75, twice). Each was fixed by admitting one field, which is why it kept recurring: the
// fix closed an instance, not the class. This test closes the class by asserting the two
// property sets stay equal, so admitting a field to one is a build error until it is on both.
//
// Adding a genuinely blocker-only field is still allowed — put it in ASYMMETRIC below with
// the reason. That makes the exception a deliberate, reviewed act instead of an oversight.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const schema = JSON.parse(
  readFileSync(new URL("./schema/reviewer-v1.json", import.meta.url), "utf8"),
);

const blocking = schema.properties.blocking_issues.items;
const notes = schema.properties.non_blocking_notes.items;

// Fields that legitimately exist on only one of the two arrays, and why.
// Keep this empty unless there is a real reason; every entry is a place a reviewer can
// still lose its whole artifact.
const ASYMMETRIC = new Map([]);

test("blocking_issues and non_blocking_notes admit the same fields", () => {
  const b = new Set(Object.keys(blocking.properties));
  const n = new Set(Object.keys(notes.properties));

  const onlyBlocking = [...b].filter((k) => !n.has(k) && !ASYMMETRIC.has(k));
  const onlyNotes = [...n].filter((k) => !b.has(k) && !ASYMMETRIC.has(k));

  assert.deepEqual(
    onlyBlocking,
    [],
    `admitted on blocking_issues but not non_blocking_notes: ${onlyBlocking.join(", ")}. ` +
      `The 0.7 confidence ladder moves a finding between these arrays, so a reviewer will ` +
      `eventually emit these on a note and lose its entire artifact to additionalProperties. ` +
      `Add them to non_blocking_notes, or list them in ASYMMETRIC with a reason.`,
  );
  assert.deepEqual(
    onlyNotes,
    [],
    `admitted on non_blocking_notes but not blocking_issues: ${onlyNotes.join(", ")}. ` +
      `Same reasoning in the other direction — a note promoted to a blocker carries its fields.`,
  );
});

// The leak-prone set is wider than "fields a blocker has": `confidence` reached notes because
// it is *required* on every fix_suggestions[] entry and named in all four prompts' ladders.
// A reviewer with that field in its output contract attaches it to the finding too.
test("fields required elsewhere in the contract are admitted on findings", () => {
  const required = schema.properties.fix_suggestions.items.required ?? [];
  const descriptive = required.filter((k) => k !== "id" && k !== "applicable" && k !== "patch_hint");

  for (const field of descriptive) {
    assert.ok(
      field in blocking.properties,
      `\`${field}\` is required on fix_suggestions[] but not admitted on blocking_issues[]. ` +
        `A reviewer reasoning in terms of it will attach it to the finding; see #75.`,
    );
    assert.ok(
      field in notes.properties,
      `\`${field}\` is required on fix_suggestions[] but not admitted on non_blocking_notes[].`,
    );
  }
});

// Both arrays must keep rejecting unknown fields. If a future fix "solves" this class by
// setting additionalProperties: true, the parity test above passes while silently accepting
// hallucinated fields — losing the guarantee that an artifact means what the schema says.
test("both finding arrays still reject unknown fields", () => {
  assert.equal(blocking.additionalProperties, false);
  assert.equal(notes.additionalProperties, false);
});

// `confidence` specifically: the regression this file was added for.
test("confidence is accepted on both finding arrays, bounded to [0, 1]", () => {
  for (const [name, items] of [
    ["blocking_issues", blocking],
    ["non_blocking_notes", notes],
  ]) {
    const c = items.properties.confidence;
    assert.ok(c, `confidence missing from ${name}`);
    assert.equal(c.minimum, 0, `${name}.confidence lost its lower bound`);
    assert.equal(c.maximum, 1, `${name}.confidence lost its upper bound`);
  }
});
