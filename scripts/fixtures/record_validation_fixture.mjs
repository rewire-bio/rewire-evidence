// Record what the TypeScript validators do, so the Python port is compared with the code it
// replaces and not with a reading of it.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_validation_fixture.mjs
//
// It runs inside the data repository, in the `fixtures` job in CI, and nowhere else. The
// cases come from validation_cases.mjs, which needs none of the data repository's code.
//
//   src/rewire_evidence/data/attribute-registry.json
//       attributeRegistry as the TypeScript declares it, with the commit it was read at and
//       the digest of its JSON.
//   tests/fixtures/validation.json
//       A small store of real records and several hundred faults seeded into it, each with
//       what every validator of loadRecords makes of it; every attribute type against a
//       palette of values; every structured schema against a valid value damaged in every
//       place; attributeIssues, normalizeAttributes, descriptionProblems and
//       assertNoPrivateFields on cases written for them; loadRecords on stores on disk.
//   scripts/expected/store-validation.json
//       What the TypeScript measures on the pinned store: how much each validator looked at,
//       every comparison panel as the query resolves it, and the refusal of a fault seeded
//       into the real store for each family of rule. The validation gate holds Python to it.
import { execFileSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  applyOps, applyValueOps, attributeCases, closed, damage, descriptionStores, descriptionTexts, examples, inherited, loadCases,
  normalizeCases, palette, privateValues, selectBase, uncertaintyValues, validatorCases, handwritten,
} from "./validation_cases.mjs";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

async function load(file) {
  const module = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(module.default ?? {}), ...module };
}

const { recordSchema, validateRecords } = await load("scripts/omics/schema.ts");
const records$ = await load("scripts/omics/records.ts");
const { loadRecords, recordFile, provenanceFileFor, recordSha256 } = records$;
const { loadSchemes, validateVocabularies } = await load("scripts/omics/vocab.ts");
const attributes$ = await load("shared/omics/attributes.ts");
const { attributeTypes, attributeIssues, validateAttributes, normalizeAttributes, convertUncertainty } = attributes$;
const { attributeRegistry } = await load("shared/omics/attribute-registry.ts");
const { validateDerivations, derivationIssues } = await load("scripts/omics/derivations.ts");
const { descriptionProblems, validateDescriptions, descriptionRules, normaliseDescription, describedKinds, distinctKinds } = await load("shared/omics/descriptions.ts");
const { validateConcerns, concernTargets, concernTargetKinds } = await load("shared/omics/concerns.ts");
const { assertNoPrivateFields, privateFieldNames } = await load("shared/omics/private-fields.ts");
const { runRecipeSchema, reproductionSchema } = await load("shared/omics/run-recipe.ts");
const { runGuideSchema, runDocumentationSchema } = await load("shared/omics/run-guide.ts");
const { profileSchema } = await load("shared/omics/profile-schema.ts");
const { extensionsSchema } = await load("scripts/omics/extensions.ts");
const { benchmarkResearchSchema } = await load("shared/omics/benchmark-research.ts");
const { createCatalogueQuery } = await load("shared/omics/catalogue-query.ts");
const { currentRecords } = await load("shared/omics/current.ts");

const sha256 = (text) => crypto.createHash("sha256").update(text).digest("hex");
const version = (name) => JSON.parse(fs.readFileSync(path.join(store, "node_modules", name, "package.json"), "utf8")).version;
const recordedFrom = {
  script: "scripts/fixtures/record_validation_fixture.mjs",
  cases: "scripts/fixtures/validation_cases.mjs",
  store_ref: execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim(),
  zod: version("zod"),
  node: process.version.split(".")[0],
};

// One member or item per line down to `levels`, and one line each below that, so a file of
// ten thousand cases is ten thousand lines and not a million.
function written(value, levels, indent = "") {
  if (levels === 0 || value === null || typeof value !== "object") return JSON.stringify(value);
  const inner = `${indent} `;
  if (Array.isArray(value)) return value.length ? `[\n${value.map((item) => inner + written(item, levels - 1, inner)).join(",\n")}\n${indent}]` : "[]";
  const members = Object.entries(value).filter(([, held]) => held !== undefined);
  return members.length ? `{\n${members.map(([key, held]) => `${inner}${JSON.stringify(key)}: ${written(held, levels - 1, inner)}`).join(",\n")}\n${indent}}` : "{}";
}
const write = (file, value, levels) => {
  fs.mkdirSync(path.dirname(path.join(repo, file)), { recursive: true });
  fs.writeFileSync(path.join(repo, file), `${written(value, levels)}\n`);
};

const issuesOf = (issues) => issues.map((issue) => [issue.code, issue.path, issue.message]);
/** What a call does: null if it returns, zod's issues if it throws them, or the error's name
 * and message. A system error's message holds a path, so its code is kept in its place. */
function outcome(run) {
  try {
    run();
    return null;
  } catch (error) {
    if (Array.isArray(error?.issues)) return { zod: issuesOf(error.issues) };
    if (error?.code && error?.syscall) return { name: error.name, code: error.code };
    return { name: error?.name ?? typeof error, message: String(error?.message) };
  }
}
const byId = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

// ---------------------------------------------------------------------------------------
// 1. The attribute registry
// ---------------------------------------------------------------------------------------

write(
  "src/rewire_evidence/data/attribute-registry.json",
  {
    recorded_from: { script: recordedFrom.script, store_ref: recordedFrom.store_ref, source: "shared/omics/attribute-registry.ts" },
    // Of JSON.stringify(attributeRegistry), which is the members below in this order.
    sha256: sha256(JSON.stringify(attributeRegistry)),
    types: Object.keys(attributeTypes),
    kinds: attributeRegistry,
  },
  3,
);

// ---------------------------------------------------------------------------------------
// 2. The pinned store
// ---------------------------------------------------------------------------------------

const all = loadRecords();
const mapping = JSON.parse(fs.readFileSync("data/ontology/mapping.json", "utf8"));
const schemes = loadSchemes();
const fileOrder = records$.recordFiles().flatMap((file) => fs.readFileSync(file, "utf8").split("\n").filter(Boolean).map((line) => recordSchema.parse(JSON.parse(line))));

/** What each validator of loadRecords makes of some records, each asked on its own. The
 * first four see the records in the order given, and validateRecords sees them by ID, as
 * loadRecords hands them over. The first that refuses is what loadRecords would throw. */
function stages(input, root) {
  const parsed = [];
  const schema = outcome(() => {
    for (const record of input) parsed.push(recordSchema.parse(record));
  });
  if (schema) return { schema };
  return {
    vocabulary: outcome(() => validateVocabularies(mapping, parsed, schemes)),
    attributes: outcome(() => validateAttributes(parsed)),
    derivations: outcome(() => validateDerivations(parsed, root)),
    descriptions: outcome(() => validateDescriptions(parsed)),
    concerns: outcome(() => validateConcerns(parsed)),
    records: outcome(() => validateRecords([...parsed].sort(byId))),
  };
}

// ---------------------------------------------------------------------------------------
// 3. The base store and the faults seeded into it
// ---------------------------------------------------------------------------------------

const base = selectBase(fileOrder);
// A derivation pins a script in the data repository by hash. The base store pins a small
// file written for the purpose, so the cases need nothing of that repository to run.
const scriptText = "# A stand-in for a derivation script.\nprint('derived')\n";
const files = {};
for (const record of base.records) {
  const derivation = record.attributes.derivation;
  if (derivation) {
    files[derivation.script] = scriptText;
    derivation.script_sha256 = sha256(scriptText);
  }
}
function scratch(held) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "rewire-evidence-validation-"));
  for (const [file, text] of Object.entries(held)) {
    fs.mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    fs.writeFileSync(path.join(root, file), text);
  }
  return root;
}
const baseRoot = scratch(files);
const cases = validatorCases(base).map(({ name, ops }) => ({ name, ops, stages: stages(applyOps(base.records, ops), baseRoot) }));
const firstFault = (held) => Object.entries(held).find(([, value]) => value !== null)?.[0] ?? null;
if (firstFault(cases[0].stages) !== null) throw new Error(`the base store is refused: ${JSON.stringify(cases[0].stages)}`);

// ---------------------------------------------------------------------------------------
// 4. Schemas
// ---------------------------------------------------------------------------------------

const parsedBy = (schema, value) => {
  const result = schema.safeParse(value);
  return result.success ? [] : issuesOf(result.error.issues);
};
// Every attribute type against every value of the palette. `concept:<scheme>` and
// `concepts:<scheme>` are the types `concept` and `concepts`.
const typeOutcomes = Object.fromEntries(Object.entries(attributeTypes).map(([name, schema]) => [name, palette.map((value) => parsedBy(schema, value))]));

const structured = {
  "missing-metadata": attributeTypes["missing-metadata"],
  uncertainty: attributeTypes.uncertainty,
  coverage: attributeTypes.coverage,
  review: attributeTypes.review,
  derivation: attributeTypes.derivation,
  "reported-population": attributeTypes["reported-population"],
  benchmark_research: benchmarkResearchSchema,
  run_documentation: runDocumentationSchema,
  run_guide: runGuideSchema,
  // As validateRunRecipes parses the attribute.
  run_recipes: runRecipeSchema.array().min(1),
  reproduction: reproductionSchema,
  profile: profileSchema,
  extensions: extensionsSchema,
};
const schemaCases = {};
let schemaCount = 0;
const exampleValues = examples(base);
for (const [name, list] of Object.entries(exampleValues)) {
  const schema = structured[name];
  schemaCases[name] = list.map((example, index) => {
    const whole = parsedBy(schema, example);
    if (whole.length) throw new Error(`example ${index} of ${name} is refused: ${JSON.stringify(whole)}`);
    const damaged = damage(example, 1000 + index).map((ops) => [ops, parsedBy(schema, applyValueOps(example, ops))]);
    schemaCount += damaged.length;
    return { example, cases: damaged };
  });
}

const writtenCases = Object.fromEntries(Object.entries(handwritten(exampleValues)).map(([name, texts]) => [name, texts.map((text) => [text, parsedBy(structured[name], JSON.parse(text))])]));

// ---------------------------------------------------------------------------------------
// 5. attributeIssues, normalizeAttributes, descriptions, private fields
// ---------------------------------------------------------------------------------------

const issueCases = attributeCases().map(({ name, record }) => {
  const parsed = recordSchema.parse(JSON.parse(record));
  let issues = null;
  const error = outcome(() => {
    issues = attributeIssues(parsed);
  });
  return { name, record, issues, error, thrown: outcome(() => validateAttributes([parsed])) };
});
// Every record of the base store with one attribute given each value of a short palette, so
// every declared type is asked through attributeIssues as well as on its own.
const short = [null, 0, "", "x", [], {}];
const baseIssues = [];
const asked = new Set();
for (const record of base.records) {
  if (attributeIssues(record).length) throw new Error(`${record.id} of the base store has attribute issues`);
  for (const key of Object.keys(record.attributes)) {
    if (asked.has(`${record.kind} ${key}`)) continue;
    asked.add(`${record.kind} ${key}`);
    baseIssues.push([record.id, key, short.map((value) => {
      let issues = null;
      const error = outcome(() => {
        issues = attributeIssues({ ...record, attributes: { ...record.attributes, [key]: value } });
      });
      return error ?? issues;
    })]);
  }
}

const compatibility = JSON.parse(fs.readFileSync("tests/fixtures/release-compatibility-2026-09-22.json", "utf8")).records;
function normalized(input, lookup) {
  let output = null;
  const error = outcome(() => {
    output = normalizeAttributes(input, lookup ? new Map(lookup) : undefined);
  });
  return {
    error,
    // Each record as it is written afterwards, and whether it is the record that went in.
    output: output && output.map((record, index) => [JSON.stringify(record), record === input[index]]),
  };
}
const normalizeRecorded = normalizeCases().map(({ name, records, lookup }) => ({ name, records: records.map((record) => JSON.stringify(record)), lookup, ...normalized(records, lookup) }));
let current = null;
const currentError = outcome(() => {
  current = currentRecords(compatibility);
});
const uncertaintyRecorded = uncertaintyValues.map((value) => {
  let converted = null;
  const error = outcome(() => {
    converted = convertUncertainty(value);
  });
  return [JSON.stringify(value), error ?? JSON.stringify(converted)];
});

const described = (text, kind = "model") => ({ id: "d", kind, name: "D", description: text, status: "needs_review" });
const descriptionRecorded = descriptionTexts.map((text) => [text, descriptionProblems([described(text)]).map((problem) => problem.rule), normaliseDescription(text)]);

const privateRecorded = privateValues.map((value) => [JSON.stringify(value), outcome(() => assertNoPrivateFields(value))]);

// ---------------------------------------------------------------------------------------
// 6. loadRecords on stores on disk
// ---------------------------------------------------------------------------------------

// A store small enough to write out for every case: one unchecked result and all it cites.
const tinyIds = closed(fileOrder, [base.roles.unchecked]);
const tiny = fileOrder.filter((record) => tinyIds.has(record.id));
function storeFiles(records) {
  const out = {};
  const put = (file, line) => {
    out[file] = (out[file] ?? "") + line;
  };
  for (const record of [...records].sort(byId)) {
    put(path.relative(".", recordFile(record)), `${JSON.stringify(record)}\n`);
    put(path.relative(".", provenanceFileFor(record.id)), `${JSON.stringify({ id: record.id, sha256: recordSha256(record), added_in: "a-batch", added_by: "records:add", changed_by: [] })}\n`);
  }
  return out;
}
const lines = (text) => text.split("\n").filter(Boolean);
const joined = (held) => held.map((line) => `${line}\n`).join("");
function faulted(held, [op, family, arg, other]) {
  const out = { ...held };
  const names = Object.keys(out).sort();
  const records = names.filter((name) => !name.startsWith("data/provenance/"));
  const evidence = names.filter((name) => name.startsWith("data/evidence/"));
  const provenance = names.filter((name) => name.startsWith("data/provenance/"));
  // The first file of the family that holds enough lines to be edited.
  const pick = (list, least = 1) => list.find((name) => lines(out[name]).length >= least);
  const entities = records.filter((name) => name.startsWith("data/entities/"));
  if (op === "empty") return {};
  if (op === "swap") {
    const file = pick(records, 2);
    const held_ = lines(out[file]);
    [held_[arg], held_[other]] = [held_[other], held_[arg]];
    out[file] = joined(held_);
  } else if (op === "repeat") {
    const file = pick(records);
    const held_ = lines(out[file]);
    out[file] = joined([held_[arg], ...held_]);
  } else if (op === "move" && arg === "other") {
    const [from, to] = entities;
    const held_ = lines(out[from]);
    out[to] = joined([...lines(out[to]), held_[0]].sort((a, b) => byId(JSON.parse(a), JSON.parse(b))));
    out[from] = joined(held_.slice(1));
    if (!out[from]) delete out[from];
  } else if (op === "move") {
    const list = family === "evidence" ? evidence : provenance;
    const from = list[0];
    const folder = path.dirname(from);
    const to = path.join(folder, ["0", "1"].map((digit) => `${digit}.jsonl`).find((name) => path.join(folder, name) !== from));
    const held_ = lines(out[from]);
    out[to] = joined([...lines(out[to] ?? ""), held_[0]].sort((a, b) => byId(JSON.parse(a), JSON.parse(b))));
    out[from] = joined(held_.slice(1));
    if (!out[from]) delete out[from];
  } else if (op === "drop") {
    const file = pick(family === "provenance" ? provenance : records);
    out[file] = joined(lines(out[file]).filter((_, index) => index !== arg));
    if (!out[file]) delete out[file];
  } else if (op === "edit") {
    const file = pick(records);
    const held_ = lines(out[file]);
    held_[arg] = JSON.stringify({ ...JSON.parse(held_[arg]), name: "Another name" });
    out[file] = joined(held_);
  } else if (op === "reverse") {
    const file = pick(records);
    const held_ = lines(out[file]);
    held_[arg] = JSON.stringify(Object.fromEntries(Object.entries(JSON.parse(held_[arg])).reverse()));
    out[file] = joined(held_);
  } else if (op === "damage") {
    const file = pick(records);
    const held_ = lines(out[file]);
    held_[arg] = JSON.stringify({ ...JSON.parse(held_[arg]), status: "nonsense" });
    out[file] = joined(held_);
  } else if (op === "beyond") {
    // A number no double holds, in an attribute that takes any JSON. JSON.parse reads it as
    // Infinity and JSON.stringify writes that as null, so the digest of the record is the
    // digest of a line that says null, and the provenance row is given that one.
    const file = pick(records);
    const held_ = lines(out[file]);
    const row = JSON.parse(held_[arg]);
    held_[arg] = JSON.stringify({ ...row, attributes: { ...row.attributes, entity_classification: { beyond: "@@" } } }).replace('"@@"', "1e400");
    out[file] = joined(held_);
    const digest = recordSha256(JSON.parse(held_[arg]));
    for (const name of provenance)
      out[name] = joined(lines(out[name]).map((line) => (JSON.parse(line).id === row.id ? JSON.stringify({ ...JSON.parse(line), sha256: digest }) : line)));
  } else if (op === "blank") {
    const file = pick(records, 2);
    const held_ = lines(out[file]);
    out[file] = `${held_[0]}\n\n${held_.slice(1).join("\n")}`;
  } else throw new Error(`unknown fault ${op}`);
  return out;
}
const tinyFiles = storeFiles(tiny);
const loadRecorded = loadCases.map(([name, ops]) => {
  const held = ops.reduce(faulted, tinyFiles);
  const root = scratch(held);
  let count = null;
  const error = outcome(() => {
    count = loadRecords(root).length;
  });
  fs.rmSync(root, { recursive: true });
  // The message names files under the scratch folder, which is written here as <root>.
  if (error?.message) error.message = error.message.split(root).join("<root>");
  return { name, files: held, count, error };
});

// ---------------------------------------------------------------------------------------
// 6b. `records -- descriptions` as a command, on stores on disk
// ---------------------------------------------------------------------------------------

// The command itself, run in a folder that holds the store, as `npm run records` runs it.
// What it wrote is kept as text: Node writes half a character as U+FFFD, so it is all UTF-8.
// Where it fails it leaves with a stack trace, and the name of the error is what is kept.
const tsx = path.join(store, "node_modules/.bin/tsx");
function described$(root, args) {
  const done = spawnSync(tsx, [path.join(store, "scripts/omics/records.ts"), "descriptions", ...args], {
    cwd: root,
    env: { ...process.env, TSX_TSCONFIG_PATH: path.join(store, "tsconfig.json") },
  });
  if (done.error) throw done.error;
  const failure = done.status === 0 ? null : (/\b([A-Za-z]*Error)\b/.exec(done.stderr.toString("utf8"))?.[1] ?? "unnamed");
  return { status: done.status, stdout: done.stdout.toString("utf8"), sha256: crypto.createHash("sha256").update(done.stdout).digest("hex"), error: failure };
}
const commandRecorded = descriptionStores.map(([name, held]) => {
  const root = scratch(held);
  const out = { name, files: held, text: described$(root, []), json: described$(root, ["--json"]) };
  fs.rmSync(root, { recursive: true });
  return out;
});
const quiet = commandRecorded.find((held) => held.name === "nothing to report");
if (quiet.text.status !== 0 || quiet.json.stdout !== "[]\n") throw new Error(`records -- descriptions did not run: ${JSON.stringify(quiet)}`);

write(
  "tests/fixtures/validation.json",
  {
    recorded_from: recordedFrom,
    contract: {
      described_kinds: describedKinds,
      distinct_kinds: distinctKinds,
      description_rules: descriptionRules.map((rule) => [rule.id, rule.kind, rule.pattern.source, rule.pattern.flags, rule.message]),
      concern_target_kinds: concernTargetKinds,
      private_field_names: privateFieldNames,
      inherited,
    },
    mapping,
    // Each concept is its key, its IRI and its label, as loadSchemes holds the pinned vocabularies.
    schemes: Object.fromEntries([...schemes].map(([name, held]) => [name, { iri: held.iri, concepts: [...held.concepts.values()].map((c) => [c.key, c.iri, c.label]) }])),
    base: { roles: base.roles, files, records: base.records.map((record) => JSON.stringify(record)) },
    // `stages` is each validator's answer on its own: null, zod's issues, or an error.
    validators: cases,
    attribute_types: { palette: palette.map((value) => JSON.stringify(value)), outcomes: typeOutcomes },
    // Each case is the operations on the example and the issues zod reports, none if it parses.
    schemas: schemaCases,
    // Values written for the rules a schema states across its members, each as JSON text.
    written: writtenCases,
    attribute_issues: issueCases,
    base_attribute_issues: { values: short, records: baseIssues },
    normalize: normalizeRecorded,
    current: {
      source: "tests/fixtures/release-compatibility-2026-09-22.json",
      records: compatibility.map((record) => JSON.stringify(record)),
      error: currentError,
      output: current && current.map((record, index) => [JSON.stringify(record), record === compatibility[index]]),
    },
    uncertainty: uncertaintyRecorded,
    descriptions: descriptionRecorded,
    private: privateRecorded,
    load: loadRecorded,
    // `records -- descriptions` and `-- descriptions --json` on each store: status and output.
    descriptions_command: commandRecorded,
  },
  4,
);

// ---------------------------------------------------------------------------------------
// 7. The pinned store, as the validators measure it
// ---------------------------------------------------------------------------------------

const tally = (map, key, by = 1) => map.set(key, (map.get(key) ?? 0) + by);
const sorted = (map) => Object.fromEntries([...map].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));

// How many attribute values the registry gives each type, and how many reasons for absence.
const valuesByType = new Map();
let attributeValues = 0;
let missingReasons = 0;
for (const record of all) {
  for (const key of Object.keys(record.attributes)) {
    tally(valuesByType, attributeRegistry[record.kind][key]);
    attributeValues += 1;
  }
  if (record.attributes.missing_metadata) missingReasons += Object.keys(record.attributes.missing_metadata).length;
}
const blocks = new Map();
for (const record of all)
  for (const key of ["benchmark_research", "run_documentation", "run_guide", "run_recipes", "reproduction", "profile", "extensions", "comparison_panels", "derivation", "evidence_concerns"])
    if (record.attributes[key] !== undefined) tally(blocks, key);
const describedRecords = all.filter((record) => describedKinds.includes(record.kind) && record.status !== "excluded");

// Every comparison panel as the query resolves it for the record that owns it: its ID and
// the results it shows, in order. An excluded owner resolves to nothing.
const query = createCatalogueQuery({ schema_version: "1.0", release_id: "validation", released_at: "", coverage: {}, records: all });
const owners = all.filter((record) => record.attributes.comparison_panels);
let panelCount = 0;
let panelRows = 0;
const panelLines = owners.map((record) => {
  const got = query.get({ id: record.id });
  if (!got) return `${record.id}\tnull\n`;
  panelCount += got.published_comparisons.length;
  for (const panel of got.published_comparisons) panelRows += panel.rows.length;
  return `${record.id}\t${got.published_comparisons.map((panel) => `${panel.id}:${panel.rows.map((row) => row.result.id).join(",")}`).join("|")}\n`;
});

const unchanged = normalizeAttributes(all).filter((record, index) => record === all[index]).length;

// A fault from the cases above, seeded into the whole store, for each family of rule. Each
// is asked of the one validator that owns the rule, over every record.
const seedNames = {
  attributes: ["an attribute nothing declares"],
  derivations: ["a derivation whose script has another hash"],
  descriptions: ["a placeholder description"],
  concerns: ["a concern naming nothing that exists"],
  records: [
    "a private field in nested",
    "a source ID that names nothing",
    "a link to nothing",
    "a literature audit citing a paper the record does not",
    "a run guide naming another record",
    "two recipes with one ID",
    "a reproduction of another purpose than the recipe",
    "a profile citing nothing that exists",
    'a source whose URL is "ftp://example.org/a.pdf"',
    "a result whose printed value is spaces",
    "an evaluation with no system",
    "a claim with no subject",
    "a panel result of another metric",
    "a panel on another record with no claim backing the link",
    "a panel on a protocol that differs from its task's of the same ID",
    // Rules a review removed from the port one at a time, each leaving this gate green.
    "a source with no retrieval date",
    "a reproduced result of an evaluation that was not our run",
    "a panel result that cites none of the panel's sources",
    "panel results that all test one system",
  ],
};
const validators = {
  attributes: (held) => validateAttributes(held),
  derivations: (held) => validateDerivations(held, "."),
  descriptions: (held) => validateDescriptions(held),
  concerns: (held) => validateConcerns(held),
  records: (held) => validateRecords([...held].sort(byId)),
};
const named = new Map(cases.map((held) => [held.name, held]));
const seeds = [];
for (const [stage, names] of Object.entries(seedNames))
  for (const name of names) {
    const held = named.get(name);
    if (!held) throw new Error(`no case named ${name}`);
    const error = outcome(() => validators[stage](applyOps(fileOrder, held.ops)));
    seeds.push({ name, stage, ops: held.ops, error });
  }

write(
  "scripts/expected/store-validation.json",
  {
    recorded_from: recordedFrom,
    // loadRecords returned, so every validator accepts the pinned store. This is the line
    // `npm run records -- check` prints.
    records: all.length,
    summary: `${all.length} records match their provenance.`,
    registry_sha256: sha256(JSON.stringify(attributeRegistry)),
    attribute_values: attributeValues,
    attribute_values_by_type: sorted(valuesByType),
    missing_reasons: missingReasons,
    blocks: sorted(blocks),
    described_records: describedRecords.length,
    description_problems: descriptionProblems(fileOrder),
    derivation_problems: derivationIssues(all, "."),
    concern_targets: [...concernTargets(all)].sort(),
    sources: all.filter((record) => record.kind === "source").length,
    normalize_unchanged: unchanged,
    panel_owners: owners.length,
    panels: panelCount,
    panel_rows: panelRows,
    panels_sha256: sha256(panelLines.join("")),
    seeded: seeds,
  },
  3,
);

fs.rmSync(baseRoot, { recursive: true });
console.log(
  `recorded ${cases.length} seeded faults, ${Object.keys(typeOutcomes).length} attribute types against ${palette.length} values, ${schemaCount} damaged values of ${Object.keys(schemaCases).length} schemas, ${issueCases.length} attributeIssues cases, ${normalizeRecorded.length} normalizeAttributes cases, ${loadRecorded.length} stores, ${seeds.length} faults seeded into the ${all.length} records of the store`,
);
