// Truncate over-long string fields in an agent artifact so a length overflow cannot
// fail a pipeline run.
//
// Why this exists: an invariant reviewer emitted a 510-character `summary` against a
// 500-character cap. Three minutes of agent time and a full review cycle were lost to a
// ten-character overshoot on a field that is only ever rendered into a PR comment.
//
// Prompts already state every cap, and the reviewer that failed had been told. That is
// the point: a model cannot count the characters it is about to emit, so `maxLength` is
// a hard cliff on a quantity the producer cannot measure. Instructions reduce the
// overshoot rate; they cannot make it zero. Something downstream has to be tolerant.
//
// Deliberately narrow: this ONLY shortens strings that exceed a `maxLength` the schema
// already declares. It never adds a missing field, coerces a type, or drops an unknown
// property. Everything structural still reaches ajv and still fails the run closed — a
// reviewer that emits the wrong SHA, an invalid decision, or a blocker with no message
// is a real failure and must stay one. Length is the sole exception because it is the
// sole constraint the producer is being asked to satisfy blind.
//
// Schema-driven rather than a hardcoded field list, so a `maxLength` added to the schema
// later is covered the day it lands and cannot reintroduce this failure mode.

import { readFileSync, writeFileSync } from "node:fs";

// `maxLength` counts Unicode code points, matching ajv's ucs2length. Slicing by
// `String.prototype.slice` would count UTF-16 units and leave an artifact that still
// fails validation whenever the summary contains an emoji or other astral character.
const codePoints = (value) => [...value];

const MARKER = "...";

function truncate(value, limit) {
  const chars = codePoints(value);
  if (chars.length <= limit) return value;
  // A cap too small to hold the marker means keeping the marker would cost more signal
  // than it conveys, so hard-cut instead.
  if (limit <= MARKER.length) return chars.slice(0, limit).join("");
  return chars.slice(0, limit - MARKER.length).join("").trimEnd() + MARKER;
}

const isPlainObject = (v) => typeof v === "object" && v !== null && !Array.isArray(v);

// Walks schema and data in parallel, collecting every string that exceeds a declared
// `maxLength`. Handles the two shapes this schema family uses: object `properties` and
// array `items`. A schema node with neither is a leaf and terminates the walk.
function collect(schema, data, path, out) {
  if (!isPlainObject(schema)) return;

  if (typeof data === "string" && typeof schema.maxLength === "number") {
    const from = codePoints(data).length;
    if (from > schema.maxLength) {
      out.push({ path, from, to: schema.maxLength });
    }
    return;
  }

  if (isPlainObject(schema.properties) && isPlainObject(data)) {
    for (const [key, sub] of Object.entries(schema.properties)) {
      if (key in data) collect(sub, data[key], `${path}/${key}`, out);
    }
    return;
  }

  if (isPlainObject(schema.items) && Array.isArray(data)) {
    data.forEach((entry, i) => collect(schema.items, entry, `${path}/${i}`, out));
  }
}

function setAt(data, path, value) {
  const keys = path.split("/").slice(1);
  let node = data;
  for (const key of keys.slice(0, -1)) node = node[key];
  node[keys.at(-1)] = value;
}

/**
 * @param {object} schema  JSON Schema to read `maxLength` constraints from.
 * @param {object} data    Artifact to normalize. Mutated in place.
 * @returns {{ data: object, truncations: Array<{path: string, from: number, to: number}> }}
 */
export function normalizeArtifact(schema, data) {
  const truncations = [];
  collect(schema, data, "", truncations);
  for (const t of truncations) {
    const current = t.path === "" ? data : t.path.split("/").slice(1).reduce((n, k) => n[k], data);
    const next = truncate(current, t.to);
    if (t.path === "") return { data: next, truncations };
    setAt(data, t.path, next);
  }
  return { data, truncations };
}

function main(argv) {
  const arg = (name) => {
    const i = argv.indexOf(name);
    return i === -1 ? null : argv[i + 1];
  };
  const schemaPath = arg("--schema");
  const dataPath = arg("--data");
  if (!schemaPath || !dataPath) {
    console.error("usage: normalize-artifact.mjs --schema <schema.json> --data <artifact.json>");
    return 2;
  }

  let schema;
  let data;
  try {
    schema = JSON.parse(readFileSync(schemaPath, "utf8"));
  } catch (err) {
    console.error(`::error::normalize-artifact: cannot read schema ${schemaPath}: ${err.message}`);
    return 1;
  }
  try {
    data = JSON.parse(readFileSync(dataPath, "utf8"));
  } catch (err) {
    // Not this script's failure to report on: ajv runs next and says the same thing with
    // better detail. Exit non-zero anyway so the cause is not attributed to validation.
    console.error(`::error::normalize-artifact: ${dataPath} is not valid JSON: ${err.message}`);
    return 1;
  }

  const { data: normalized, truncations } = normalizeArtifact(schema, data);
  if (truncations.length === 0) return 0;

  for (const t of truncations) {
    // A warning, not a notice: the reviewer overshot a cap it was told about, and the
    // truncated tail is lost from the published comment. Visible in the run summary so a
    // prompt that routinely overshoots gets noticed and fixed at the source.
    console.log(`::warning::normalize-artifact: ${t.path} was ${t.from} chars, truncated to ${t.to}`);
  }
  writeFileSync(dataPath, `${JSON.stringify(normalized, null, 2)}\n`);
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main(process.argv.slice(2)));
}
