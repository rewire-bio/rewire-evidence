// Record what the TypeScript record schema and vocabulary loader do, so the Python port is
// compared with the code it replaces and not with a reading of it.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_schema_fixture.mjs
//
// It runs inside the data repository, because the schema needs zod, the vocabulary loader
// needs n3, and both read data/ontology and data/vocab from the working directory. CI does
// that in the `fixtures` job and fails if any of the three files changes. It is not run on a
// developer machine: nothing here installs the data repository's dependencies.
//
//   tests/fixtures/record-schema.json   recordSchema.safeParse on accepted and rejected input,
//                                       with the bytes or the issues zod gives; the kinds,
//                                       statuses and relation rules; relationAllows,
//                                       currentRelation, normalizeRecords, publicRecords and
//                                       the link rules of validateRecords.
//   tests/fixtures/vocabularies.json    loadSchemes and validateVocabularies on small
//                                       vocabularies written for the purpose.
//   scripts/expected/store-schema.json  What the TypeScript measures on the pinned store: the
//                                       records loadRecords accepts, their links, the public
//                                       records, every scheme's concepts, every controlled
//                                       value, and the refusal of a seeded bad concept in each
//                                       controlled field. The schema gate holds Python to it.
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(path.join(store, "package.json"));

// A TypeScript module of the data repository. tsx may hand back its exports directly or
// under `default`, depending on how it compiled the file, so both are merged.
async function load(file) {
  const module = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(module.default ?? {}), ...module };
}

const { recordSchema, recordIdPattern, statuses, validateRecords, publicRecords } = await load(
  "scripts/omics/schema.ts",
);
const { entityKinds, modelSubjectKinds, benchmarkSubjectKinds, datasetSubjectKinds } = await load(
  "shared/omics/entity-kinds.ts",
);
const { relationRules, relations, relationAllows, currentRelation, normalizeRecords } = await load(
  "shared/omics/relations.ts",
);
const { loadSchemes, validateVocabularies, controlledValues, schemeIri } = await load("scripts/omics/vocab.ts");
const { loadRecords, kindFiles, evidenceKinds, shardedKinds } = await load("scripts/omics/records.ts");
const { Parser } = require("n3");

const sha256 = (text) => crypto.createHash("sha256").update(text).digest("hex");
const version = (name) =>
  JSON.parse(fs.readFileSync(path.join(store, "node_modules", name, "package.json"), "utf8")).version;
const recordedFrom = {
  script: "scripts/fixtures/record_schema_fixture.mjs",
  store_ref: execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim(),
  zod: version("zod"),
  n3: version("n3"),
};
const write = (file, value) => fs.writeFileSync(path.join(repo, file), JSON.stringify(value, null, 1) + "\n");
const thrown = (run) => {
  try {
    run();
    return null;
  } catch (error) {
    return error.message;
  }
};

// A small generator with a stated seed, so the shuffled cases are the same on every run.
function generator(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function shuffled(items, next) {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(next() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  // A shuffle that lands on the order it started from proves nothing, so it is rotated.
  if (out.length > 1 && out.every((item, index) => item === items[index])) out.push(out.shift());
  return out;
}
const reorder = (object, next) => Object.fromEntries(shuffled(Object.entries(object), next));
function reorderDeep(value, next) {
  if (Array.isArray(value)) return value.map((item) => reorderDeep(item, next));
  if (value && typeof value === "object")
    return Object.fromEntries(shuffled(Object.entries(value), next).map(([key, held]) => [key, reorderDeep(held, next)]));
  return value;
}

// ---------------------------------------------------------------------------------------
// 1. recordSchema
// ---------------------------------------------------------------------------------------

// Input is kept as JSON text, because that is what a batch file holds, and because key order,
// repeated keys and spellings such as 1.0 and -0 do not survive being held as a value.
function parseCase(name, input) {
  const result = recordSchema.safeParse(JSON.parse(input));
  if (result.success) {
    const output = JSON.stringify(result.data);
    return { name, input, accepted: true, output, sha256: sha256(output) };
  }
  return {
    name,
    input,
    accepted: false,
    issues: result.error.issues.map((issue) => ({ code: issue.code, path: issue.path, message: issue.message })),
  };
}

const base = {
  id: "model-a",
  kind: "model",
  name: "Model A",
  description: "A model.",
  status: "discovered",
  facets: { areas: ["dna-genomes"], tasks: ["t"] },
  source_ids: ["source-a"],
  links: [{ relation: "source", target_id: "source-a" }],
  attributes: { version: "1", nested: { b: 1, a: 2 } },
};
const keys = Object.keys(base);
const text = (value) => JSON.stringify(value);
const withField = (field, value) => text({ ...base, [field]: value });
const without = (field) => text(Object.fromEntries(Object.entries(base).filter(([key]) => key !== field)));
// The base record with one member written as given text, where the spelling is the point.
const rawField = (field, raw) =>
  `{${keys.map((key) => `${JSON.stringify(key)}:${key === field ? raw : JSON.stringify(base[key])}`).join(",")}}`;

const cases = [];
const add = (name, input) => cases.push(parseCase(name, input));

add("the base record", text(base));
add("top-level keys reversed", text(Object.fromEntries(Object.entries(base).reverse())));
for (const seed of [1, 2, 3]) add(`top-level keys shuffled, seed ${seed}`, text(reorder(base, generator(seed))));
add("attribute keys keep the order they were written in", withField("attributes", { z: 1, a: { y: 1, b: 2 }, m: [{ q: 1, c: 2 }] }));
add("facet keys keep the order they were written in", withField("facets", { z: ["b", "a"], a: [] }));
add("integer-like facet keys", rawField("facets", '{"b":[],"10":[],"2":[],"a":[],"01":[]}'));
add("integer-like attribute keys", rawField("attributes", '{"b":1,"10":2,"2":3,"a":{"z":1,"7":2,"0":3},"01":5}'));
add("a link with its keys reversed", withField("links", [{ target_id: "source-a", relation: "source" }]));
add("a link with an extra key", withField("links", [{ relation: "source", target_id: "source-a", note: "x" }]));
add("a link with an extra key first", withField("links", [{ note: "x", target_id: "source-a", relation: "source" }]));
add("a link with a __proto__ key", rawField("links", '[{"relation":"source","__proto__":{"a":1},"target_id":"source-a"}]'));
add("two links the same", withField("links", [base.links[0], base.links[0]]));
add("no links, no sources, no facets, no attributes", text({ ...base, facets: {}, source_ids: [], links: [], attributes: {} }));
add("an empty description", withField("description", ""));
add("a name that is one space", withField("name", " "));
add("text outside ASCII", text({ ...base, name: "Mod\u00e8le \u{1f9ec} \u2028", description: "\u0000\u001f\"\\" }));
add("a lone surrogate in the name", rawField("name", '"a\\ud800b"'));
for (const id of ["a", "0", "a-0-b", "0-0", "abc123-456def"]) add(`the ID ${id}`, withField("id", id));
for (const kind of entityKinds) add(`the kind ${kind}`, withField("kind", kind));
for (const status of statuses) add(`the status ${status}`, withField("status", status));
for (const relation of relations) add(`the relation ${relation}`, withField("links", [{ relation, target_id: "x" }]));
add("a source ID that is empty text", withField("source_ids", [""]));
add("a source ID given twice", withField("source_ids", ["a", "a"]));
add("a facet with an empty value and an empty item", withField("facets", { a: [""], "": [] }));
add("a link target that is one space", withField("links", [{ relation: "source", target_id: " " }]));
add("null attribute values", withField("attributes", { numeric_value: null, other: null, deep: { a: null }, list: [null] }));
add("attribute values of every JSON type", withField("attributes", { a: true, b: false, c: "", d: [], e: {}, f: [[], {}], g: 0 }));
add("numbers as written", rawField("attributes", '{"a":1.0,"b":-0,"c":1e21,"d":1e-7,"e":0.00001,"f":12345678901234567890,"g":1E5,"h":5e-324,"i":1.7976931348623157e308,"j":-1.50,"k":100,"l":0.1e1,"m":9007199254740993}'));
add("a number beyond any double", rawField("attributes", '{"a":1e400,"b":[-1e400]}'));
add("a repeated top-level key", `{"id":"first",${text(base).slice(1)}`);
add("a repeated attribute key", rawField("attributes", '{"a":1,"b":2,"a":3}'));
add("__proto__ as an attribute key", rawField("attributes", '{"a":1,"__proto__":{"x":1},"b":2}'));
add("__proto__ as the only attribute key", rawField("attributes", '{"__proto__":1}'));
add("__proto__ as a facet key", rawField("facets", '{"a":[],"__proto__":["x"],"b":[]}'));
add("__proto__ as a facet key with a value that is no list", rawField("facets", '{"__proto__":"x"}'));
add("__proto__ nested inside an attribute", rawField("attributes", '{"a":{"__proto__":{"x":1},"b":2}}'));
add("__proto__ as a top-level key", `${text(base).slice(0, -1)},"__proto__":{"x":1}}`);
add("names of Object.prototype as attribute keys", withField("attributes", { constructor: 1, toString: 2, hasOwnProperty: 3, valueOf: 4 }));
add("names of Object.prototype as facet keys", withField("facets", { constructor: [], toString: ["a"] }));
add("deeply nested attributes", withField("attributes", { a: [[[{ b: [{ c: { d: [1, [2, { e: null }]] } }] }]]] }));

// Rejected: the whole value, a missing member, a member of the wrong type.
for (const [name, input] of [["null", "null"], ["a list", "[]"], ["text", '"x"'], ["a number", "1"], ["true", "true"], ["an empty object", "{}"], ["a list holding the record", `[${text(base)}]`]])
  add(`the record is ${name}`, input);
for (const key of keys) add(`no ${key}`, without(key));
add("no id and no attributes", text(Object.fromEntries(Object.entries(base).filter(([key]) => key !== "id" && key !== "attributes"))));
const wrong = { null: null, "a number": 1, text: "x", true: true, "a list": [], "an object": {} };
const right = {
  id: ["text"], kind: ["text"], name: ["text"], description: ["text"], status: ["text"],
  facets: ["an object"], source_ids: ["a list"], links: ["a list"], attributes: ["an object"],
};
for (const key of keys)
  for (const [name, value] of Object.entries(wrong))
    if (!right[key].includes(name)) add(`${key} is ${name}`, withField(key, value));
for (const id of ["", "A", "a-", "-a", "a--b", "a b", "a\n", "a_b", "\u00e9", "a-B", "\uff11", "a.b", " a", "a\r"])
  add(`the ID ${JSON.stringify(id)}`, withField("id", id));
for (const kind of ["Model", "unknown", "", "models", "toString", "model "]) add(`the kind ${JSON.stringify(kind)}`, withField("kind", kind));
add("an empty name", withField("name", ""));
for (const status of ["x", "", "Discovered", "constructor"]) add(`the status ${JSON.stringify(status)}`, withField("status", status));
add("a facet that is text", withField("facets", { a: "x" }));
add("a facet that is null", withField("facets", { a: null }));
add("a facet holding a number", withField("facets", { a: [1] }));
add("a facet holding null", withField("facets", { a: [null] }));
add("a facet holding a list", withField("facets", { a: [["x"]] }));
add("several bad facets", withField("facets", { ok: ["x"], b: 1, a: ["x", 2, "y", null], c: {} }));
add("integer-like facet keys with bad values", rawField("facets", '{"b":1,"10":2,"2":3}'));
add("a source ID that is a number", withField("source_ids", [1]));
add("a source ID that is null", withField("source_ids", [null]));
add("several bad source IDs", withField("source_ids", ["a", 2, "b", {}, []]));
add("a link that is a number", withField("links", [1]));
add("a link that is null", withField("links", [null]));
add("a link that is a list", withField("links", [[]]));
add("a link that is text", withField("links", ["source"]));
add("an empty link", withField("links", [{}]));
add("a link with no target", withField("links", [{ relation: "source" }]));
add("a link with no relation", withField("links", [{ target_id: "x" }]));
add("a link with an unknown relation", withField("links", [{ relation: "nope", target_id: "x" }]));
add("a link with an empty relation", withField("links", [{ relation: "", target_id: "x" }]));
add("a link with an empty target", withField("links", [{ relation: "source", target_id: "" }]));
add("a link with numbers for both", withField("links", [{ relation: 1, target_id: 1 }]));
add("a link with null for both", withField("links", [{ relation: null, target_id: null }]));
add("a link whose relation is a name on Object.prototype", withField("links", [{ relation: "toString", target_id: "x" }]));
add("a link whose relation is a kind", withField("links", [{ relation: "model", target_id: "x" }]));
add("a link with an unknown relation and an empty target", withField("links", [{ relation: "nope", target_id: "" }]));
add("a link with an unknown relation, an empty target and an extra key", withField("links", [{ relation: "nope", target_id: "", note: 1 }]));
add("bad links at several positions", withField("links", [base.links[0], { relation: "nope", target_id: "x" }, base.links[0], 3, { relation: "source", target_id: "" }]));
add("one unknown key", text({ ...base, extra: 1 }));
add("one unknown key first", text({ extra: 1, ...base }));
add("two unknown keys", text({ ...base, zeta: 1, alpha: 2 }));
add("two unknown keys around the record", text({ zeta: 1, ...base, alpha: 2 }));
add("integer-like unknown keys", `${text(base).slice(0, -1)},"b":1,"10":2,"2":3}`);
add("an unknown key that is a name on Object.prototype", text({ ...base, toString: 1 }));
add("an unknown key whose value is null", text({ ...base, extra: null }));
add("an unknown key and a missing one", `${without("name").slice(0, -1)},"extra":1}`);
add("an unknown key and a bad status", text({ ...base, status: "x", extra: 1 }));
add("an unknown key with an empty name", text({ ...base, "": 1 }));
add("a key that differs only in case", text({ ...Object.fromEntries(Object.entries(base).filter(([key]) => key !== "id")), ID: "model-a" }));
add("everything wrong at once", text({ id: "A", kind: "x", name: "", description: 1, status: "y", facets: { a: 1 }, source_ids: [1], links: [{ relation: "z" }], attributes: [], extra: 1 }));
// zod's length check looks at anything that has a `length`, whatever its type, and compares
// it as JavaScript compares: these settle what it reports beside the wrong type.
const lengths = ['0', '1', '2', '0.5', '-1', '1e400', 'null', 'true', 'false', '"0"', '"1"', '" 2 "', '"abc"', '""', '" "', '"0x10"', '"0X1f"', '"-0x10"', '"0b11"', '"0o7"', '"1e3"', '"1E0"', '".5"', '"5."', '"."', '"+1"', '"-1"', '"1_000"', '"Infinity"', '"-Infinity"', '"+Infinity"', '"infinity"', '"NaN"', '"1n"', '"1 1"', '"\\t1\\n"', '"\\u00a01\\ufeff"', '"\\u200b1"', '"0.9999999999999999"', '[]', '[5]', '[1,2]', '[[3]]', '[null]', '["2"]', '[true]', '[1.5]', '[[]]', '[{}]', '{}', '{"a":1}', '[false]', '[1e400]', '[-1e400]', '-1e400', `"0x${"f".repeat(300)}"`, '"0x"', '[1e21]', '[1e-7]', '["a",null,[null,"b"]]'];
for (const raw of lengths) add(`name has the length ${raw}`, rawField("name", `{"length":${raw}}`));
for (const raw of ['["x"]', '[1,2]', '[[]]']) add(`name is the list ${raw}`, rawField("name", raw));
for (const raw of ['[]', '["x"]', '{"length":0}', '{"length":3}', '{}']) add(`a link target is ${raw}`, rawField("links", `[{"relation":"source","target_id":${raw}}]`));
for (const raw of ['[]', '{"length":0}']) add(`a link relation is ${raw}`, rawField("links", `[{"relation":${raw},"target_id":"x"}]`));
for (const raw of ['{"length":0}', '1e400', '-1e400', '1.5', '-0']) add(`id is ${raw}`, rawField("id", raw));
add("source_ids is an object with a length", rawField("source_ids", '{"length":0}'));
add("kind is a number beyond any double", rawField("kind", "1e400"));
add("the record is a number beyond any double", "1e400");
add("unknown keys holding quotes, a backslash, a line break and other scripts", `${text(base).slice(0, -1)},"a\\"b":1,"a\\\\b":2,"line\\nbreak":3,"\u00e9\u{1f9ec}":4}`);
add("a facet key holding a quote with a bad value", rawField("facets", '{"a\\"b":1}'));
add("every member null", text(Object.fromEntries(keys.map((key) => [key, null]))));
add("every member a number, in reverse order", text(Object.fromEntries([...keys].reverse().map((key) => [key, 1]))));

// Stored records with their keys moved: the first and the last of each kind by ID, and the
// record the handover names. `top` moves the top-level keys and the keys of each link, which
// the schema puts back, so the digest is the committed one. `nested` also moves the keys
// inside facets and attributes, which the schema leaves alone, so the digest is whatever
// those bytes give.
const readLines = (file) => fs.readFileSync(file, "utf8").split("\n").filter(Boolean);
const stored = new Map();
for (const kind of entityKinds)
  for (const file of kindFiles(kind))
    for (const line of readLines(file)) stored.set(JSON.parse(line).id, { kind, line });
const chosen = new Set(["2ome-lm-2025"]);
for (const kind of entityKinds) {
  const ids = [...stored].filter(([, held]) => held.kind === kind).map(([id]) => id).sort();
  chosen.add(ids[0]);
  chosen.add(ids[ids.length - 1]);
}
const moved = [];
let seed = 100;
for (const id of [...chosen].sort()) {
  const { line } = stored.get(id);
  const record = JSON.parse(line);
  const committed = sha256(line);
  for (const depth of ["top", "nested"]) {
    seed += 1;
    const next = generator(seed);
    const top = reorder({ ...record, links: record.links.map((link) => reorder(link, next)) }, next);
    const input = JSON.stringify(depth === "top" ? top : { ...top, facets: reorderDeep(top.facets, next), attributes: reorderDeep(top.attributes, next) });
    const output = JSON.stringify(recordSchema.parse(JSON.parse(input)));
    if (depth === "top" && output !== line) throw new Error(`${id}: the schema did not restore the committed line`);
    moved.push({ name: `${id}, ${depth} keys moved`, input, sha256: sha256(output), committed_sha256: committed });
  }
}

// ---------------------------------------------------------------------------------------
// 2. Relations
// ---------------------------------------------------------------------------------------

const fromKinds = [...entityKinds, "nonsense"];
const allows = Object.fromEntries(
  [...relations, "nonsense"].map((relation) => [
    relation,
    fromKinds.map((from) => fromKinds.map((to) => (relationAllows(relation, from, to) ? "1" : "0")).join("")).join(" "),
  ]),
);
// A name that is no relation but is a member of every JavaScript object. relationAllows looks
// the name up on a plain object, so what it does with these is recorded and not assumed.
const inherited = Object.fromEntries(
  ["toString", "constructor", "__proto__", "hasOwnProperty"].map((relation) => {
    let allowed = null;
    const error = thrown(() => {
      allowed = relationAllows(relation, "model", "model");
    });
    return [relation, { allowed, error }];
  }),
);
const olderNames = [...new Set([...relations, ...entityKinds, "nonsense", ""])];
const renamed = {};
for (const from of fromKinds)
  for (const relation of olderNames) {
    const current = currentRelation(from, relation);
    if (current !== relation) (renamed[from] ??= {})[relation] = current;
  }

const entry = (id, kind, extra = {}) => ({
  id, kind, name: id, description: "", status: "discovered", facets: {}, source_ids: [], links: [], attributes: {}, ...extra,
});
const link = (relation, target_id) => ({ relation, target_id });
function normalizeCase(name, records) {
  const input = JSON.stringify(records);
  const held = JSON.parse(input);
  const out = normalizeRecords(held);
  // The function promises not to change what it was given, so that is recorded as well.
  return { name, input, output: JSON.stringify(out), same_list: out === held, input_untouched: JSON.stringify(held) === input };
}
const normalize = [
  normalizeCase("nothing to rename", [entry("e", "evaluation", { links: [link("system", "m"), link("assessment", "t"), link("data", "d")] }), entry("m", "model")]),
  normalizeCase("no records", []),
  normalizeCase("an evaluation with the older link names", [
    entry("e", "evaluation", { links: [link("model", "m"), link("benchmark", "b"), link("dataset", "d"), link("configuration", "c"), link("protocol", "p"), link("dataset_subset", "s"), link("source", "x"), link("evaluation", "y")] }),
    entry("m", "model"),
  ]),
  normalizeCase("a baseline with the older link names", [entry("b", "baseline", { links: [link("model", "m"), link("pipeline", "p"), link("evaluation", "e"), link("dataset", "d"), link("benchmark", "x")] })]),
  normalizeCase("data, assessment and configuration records", [
    entry("d", "dataset", { links: [link("benchmark", "b"), link("protocol", "p"), link("dataset", "x")] }),
    entry("s", "dataset_subset", { links: [link("protocol", "p")] }),
    entry("t", "task", { links: [link("dataset", "d"), link("benchmark", "b")] }),
    entry("c", "configuration", { links: [link("variant_of", "m"), link("family", "m")] }),
    entry("m", "model", { links: [link("variant_of", "m2")] }),
  ]),
  normalizeCase("a link with extra keys keeps them and their order", [entry("e", "evaluation", { links: [{ note: 1, relation: "model", target_id: "m", last: 2 }] })]),
  normalizeCase("claims that cite a link field", [
    entry("e", "evaluation"),
    entry("b", "baseline"),
    entry("c1", "claim", { links: [link("subject", "e")], attributes: { before: 1, field: "links:model:m", after: 2 } }),
    entry("c2", "claim", { links: [link("subject", "e")], attributes: { field: "links:model" } }),
    entry("c3", "claim", { links: [link("subject", "e")], attributes: { field: "links:benchmark:a:b" } }),
    entry("c4", "claim", { links: [link("subject", "e")], attributes: { field: "links:" } }),
    entry("c5", "claim", { links: [link("subject", "e")], attributes: { field: "links:system:m" } }),
    entry("c6", "claim", { links: [link("subject", "e"), link("subject", "b")], attributes: { field: "links:model:m" } }),
    entry("c7", "claim", { links: [link("subject", "missing")], attributes: { field: "links:model:m" } }),
    entry("c8", "claim", { links: [], attributes: { field: "links:model:m" } }),
    entry("c9", "claim", { links: [link("subject", "b")], attributes: { field: "links:evaluation:e" } }),
    entry("c10", "claim", { links: [link("subject", "e")], attributes: { field: "attributes.links:model:m" } }),
    entry("c11", "claim", { links: [link("subject", "e")], attributes: { field: 7 } }),
    entry("c12", "claim", { links: [link("subject", "e")], attributes: {} }),
    entry("m1", "model", { links: [link("subject", "e")], attributes: { field: "links:model:m" } }),
  ]),
  normalizeCase("the same ID twice: the later kind is the one a claim sees", [
    entry("x", "evaluation"),
    entry("x", "baseline"),
    entry("c", "claim", { links: [link("subject", "x")], attributes: { field: "links:model:m" } }),
  ]),
];

function publicCase(name, records) {
  return { name, input: JSON.stringify(records), public_ids: publicRecords(records).map((record) => record.id) };
}
const publicCases = [
  publicCase("no records", []),
  publicCase("every status on a model", statuses.map((status) => entry(`m-${status.replace(/_/g, "-")}`, "model", { status }))),
  publicCase("every status on a result", statuses.map((status) => entry(`r-${status.replace(/_/g, "-")}`, "result", { status }))),
  publicCase("a record citing a blocked source is blocked, and so is what links to it", [
    entry("s", "source", { status: "excluded" }),
    entry("m", "model", { source_ids: ["s"] }),
    entry("c", "configuration", { links: [link("configuration_of", "m")] }),
    entry("e", "evaluation", { links: [link("system", "c")] }),
    entry("r", "result", { status: "source_checked", links: [link("evaluation", "e")] }),
    entry("free", "model"),
    entry("s2", "source"),
    entry("m2", "model", { source_ids: ["s2", "missing"], links: [link("family", "missing")] }),
  ]),
  publicCase("blocking runs against the order of the list", [
    entry("a", "model", { links: [link("family", "b")] }),
    entry("b", "model", { links: [link("family", "c")] }),
    entry("c", "model", { links: [link("family", "d")] }),
    entry("d", "model", { status: "disputed" }),
    entry("z", "model"),
  ]),
  publicCase("a record that links to itself, and a loop", [
    entry("a", "model", { links: [link("alias_of", "a")] }),
    entry("b", "model", { links: [link("alias_of", "c")] }),
    entry("c", "model", { links: [link("alias_of", "b")] }),
    entry("d", "model", { status: "excluded", links: [link("alias_of", "d")] }),
  ]),
  publicCase("an unreviewed result blocks a claim about it", [
    entry("e", "evaluation"),
    entry("r", "result", { status: "needs_review", links: [link("evaluation", "e")] }),
    entry("c", "claim", { links: [link("subject", "r")] }),
    entry("c2", "claim", { links: [link("subject", "e")] }),
  ]),
];

// The checks validateRecords makes of IDs, sources and links, each on a small store that is
// otherwise valid, so the message recorded is the one for the fault named.
const source = (id) => entry(id, "source", { attributes: { url: "https://example.org/a", retrieved_at: "2026-01-01", version: "1" } });
const linkCase = (name, records) => ({ name, input: JSON.stringify(records), error: thrown(() => validateRecords(JSON.parse(JSON.stringify(records)))) });
const linkCases = [
  linkCase("no records", []),
  linkCase("a model, its source and a family link", [entry("m", "model", { source_ids: ["s"], links: [link("family", "f"), link("source", "s")] }), entry("f", "model"), source("s")]),
  linkCase("the same ID twice", [entry("m", "model"), entry("m", "model")]),
  linkCase("the same ID twice under two kinds", [entry("m", "model"), entry("m", "dataset")]),
  linkCase("a source that does not exist", [entry("m", "model", { source_ids: ["s"] })]),
  linkCase("a source ID naming a model", [entry("m", "model", { source_ids: ["f"] }), entry("f", "model")]),
  linkCase("a source ID naming the record itself", [entry("m", "model", { source_ids: ["m"] })]),
  linkCase("the second of two sources missing", [entry("m", "model", { source_ids: ["s", "t"] }), source("s")]),
  linkCase("a link to nothing", [entry("m", "model", { links: [link("family", "f")] })]),
  linkCase("a missing source is reported before a link to nothing", [entry("m", "model", { source_ids: ["s"], links: [link("family", "f")] })]),
  linkCase("a relation that cannot leave this kind", [entry("m", "model", { links: [link("system", "f")] }), entry("f", "model")]),
  linkCase("a relation that cannot reach this kind", [entry("m", "model", { links: [link("family", "d")] }), entry("d", "dataset")]),
  linkCase("a source link to a model", [entry("m", "model", { links: [link("source", "f")] }), entry("f", "model")]),
  linkCase("data reuse between two datasets", [entry("d", "dataset", { links: [link("same_data_as", "e")] }), entry("e", "dataset_subset")]),
  linkCase("data reuse from a model", [entry("m", "model", { links: [link("same_data_as", "d")] }), entry("d", "dataset")]),
  linkCase("data reuse to a model", [entry("d", "dataset", { links: [link("same_data_as", "m")] }), entry("m", "model")]),
  linkCase("data reuse with itself", [entry("d", "dataset", { links: [link("same_data_as", "d")] })]),
  linkCase("the first bad link of several is the one reported", [entry("m", "model", { links: [link("family", "f"), link("system", "f"), link("family", "gone")] }), entry("f", "model")]),
  linkCase("the first bad record of several is the one reported", [entry("a", "model"), entry("b", "model", { links: [link("family", "gone")] }), entry("c", "model", { source_ids: ["gone"] })]),
  linkCase("a supersedes link between any two kinds", [entry("m", "model", { links: [link("supersedes", "d")] }), entry("d", "dataset")]),
];

write("tests/fixtures/record-schema.json", {
  recorded_from: recordedFrom,
  contract: {
    id_pattern: recordIdPattern.source,
    id_pattern_flags: recordIdPattern.flags,
    shape: Object.keys(recordSchema.shape),
    kinds: [...entityKinds],
    statuses: [...statuses],
    evidence_kinds: [...evidenceKinds],
    sharded_kinds: [...shardedKinds],
    model_subject_kinds: [...modelSubjectKinds],
    benchmark_subject_kinds: [...benchmarkSubjectKinds],
    dataset_subject_kinds: [...datasetSubjectKinds],
    relations: [...relations],
    relation_rules: Object.fromEntries(Object.entries(relationRules).map(([name, rule]) => [name, { from: [...rule.from], to: [...rule.to] }])),
  },
  parse: cases,
  moved_keys: moved,
  // One row per kind a link leaves from, one character per kind it arrives at, in
  // `relation_kinds` order: 1 where relationAllows says yes.
  relation_kinds: fromKinds,
  relation_allows: allows,
  relation_allows_inherited_names: inherited,
  // currentRelation over every kind and every name, listing only the names it changes.
  older_names: olderNames,
  current_relation: renamed,
  normalize_records: normalize,
  public_records: publicCases,
  link_rules: linkCases,
});

// ---------------------------------------------------------------------------------------
// 3. Vocabularies written for the purpose
// ---------------------------------------------------------------------------------------

const head = (name) =>
  `@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n@prefix rb: <https://benchmarks.rewire.it/vocab#> .\n@prefix : <${schemeIri(name)}> .\n<${schemeIri(name)}> a skos:ConceptScheme .\n`;
const concept = (name, key, extra = "") =>
  `:${key} a skos:Concept ; skos:inScheme <${schemeIri(name)}> ; skos:prefLabel "${key} label"@en${extra} .\n`;
const scheme = (name, body) => head(name) + body;

function vocabularyRoot(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "vocab-"));
  if (files === null) return root;
  fs.mkdirSync(path.join(root, "data/vocab"), { recursive: true });
  for (const [name, body] of Object.entries(files)) {
    const file = path.join(root, "data/vocab", name);
    if (body === null) fs.mkdirSync(file);
    else fs.writeFileSync(file, body);
  }
  return root;
}
const dumpSchemes = (schemes) =>
  Object.fromEntries([...schemes].map(([name, held]) => [name, { iri: held.iri, concepts: [...held.concepts.values()].map((c) => [c.key, c.iri, c.label]) }]));
// `files` maps a file name in data/vocab to its text, or to null for a folder of that name.
// `files` itself is null where there is no data/vocab at all.
function loadCase(name, files, comparable = true) {
  const root = vocabularyRoot(files);
  let schemes = null;
  const error = thrown(() => {
    schemes = dumpSchemes(loadSchemes(root));
  });
  fs.rmSync(root, { recursive: true });
  // `comparable` is false where the message is the Turtle parser's own, which no other
  // parser words the same way. The refusal is what is compared there.
  return { name, files, error, schemes, message_comparable: comparable };
}
const direction = { "direction.ttl": scheme("direction", concept("direction", "higher") + concept("direction", "lower")) };
const metric = (body) => ({ ...direction, "metric.ttl": scheme("metric", body) });
const many = Array.from({ length: 25 }, (_, index) => `:a skos:related :missing-${index} .\n`).join("");
const loadCases = [
  loadCase("no data/vocab folder", null),
  loadCase("an empty data/vocab folder", {}),
  loadCase("two schemes that refer to each other", {
    ...direction,
    "metric.ttl": scheme(
      "metric",
      concept("metric", "a") +
        concept("metric", "b", ` ; skos:broader :a ; skos:related <${schemeIri("direction")}higher> ; rb:defaultDirection <${schemeIri("direction")}lower>`),
    ),
  }),
  loadCase("files are read in name order, and only .ttl files in the folder itself", {
    "zeta.ttl": scheme("zeta", concept("zeta", "z")),
    "alpha.ttl": scheme("alpha", concept("alpha", "a")),
    "Beta.ttl": scheme("Beta", ""),
    "notes.txt": "not turtle",
    "alpha.ttl.bak": "not turtle",
    migration: null,
    "folder.ttl-not": null,
  }),
  loadCase("concepts keep the order of the file, and a concept named twice keeps its first place", metric(concept("metric", "zeta") + concept("metric", "alpha") + concept("metric", "mid") + `:zeta skos:inScheme <${schemeIri("metric")}> .\n`)),
  loadCase("keys with digits, hyphens and underscores", metric(concept("metric", "source_checked") + concept("metric", "f1-score") + concept("metric", "0") + concept("metric", "a-b_c-9"))),
  loadCase("the first label in the file is the one kept", metric(`:a a skos:Concept ; skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel "premier"@fr , "first"@en , "plain" .\n`)),
  loadCase("a label given before the concept is declared", metric(`:a skos:prefLabel "early" .\n:a skos:inScheme <${schemeIri("metric")}> .\n:a skos:prefLabel "late"@en .\n`)),
  loadCase("labels with quotes, escapes, line breaks and other scripts", metric(`:a skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel "a \\"quoted\\" \\\\ label\\twith\\ttabs \\u00e9 \u03b1 \u{1f9ec}"@en .\n:b skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel """two\nlines""" .\n:c skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel 7 .\n:d skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel <https://example.org/label> .\n`)),
  loadCase("a file that declares no scheme", { "metric.ttl": `@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n<${schemeIri("metric")}a> skos:prefLabel "a" .\n` }),
  loadCase("an empty file", { "metric.ttl": "" }),
  loadCase("a file that declares another scheme", { "metric.ttl": scheme("direction", concept("direction", "higher")) }),
  loadCase("a scheme declared as something else", { "metric.ttl": `@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n<${schemeIri("metric")}> a skos:Concept .\n` }),
  loadCase("a scheme with no concepts", { "metric.ttl": scheme("metric", "") }),
  loadCase("a key with a capital letter", metric(concept("metric", "Accuracy"))),
  loadCase("a key ending in a hyphen", metric(`<${schemeIri("metric")}a-> skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel "a" .\n`)),
  loadCase("a key with two hyphens together", metric(`<${schemeIri("metric")}a--b> skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel "a" .\n`)),
  loadCase("a key with a slash", metric(`<${schemeIri("metric")}a/b> skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel "a" .\n`)),
  loadCase("an empty key: the scheme in itself", metric(`<${schemeIri("metric")}> skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel "a" .\n`)),
  loadCase("a concept from another namespace", metric(`<https://example.org/a> skos:inScheme <${schemeIri("metric")}> ; skos:prefLabel "a" .\n`)),
  loadCase("a concept with no label", metric(concept("metric", "a") + `:b a skos:Concept ; skos:inScheme <${schemeIri("metric")}> ; skos:altLabel "b" .\n`)),
  loadCase("a broader concept that does not exist", metric(concept("metric", "b", " ; skos:broader :missing"))),
  loadCase("a related concept that does not exist in another scheme", metric(concept("metric", "b", ` ; skos:related <${schemeIri("direction")}sideways>`))),
  loadCase("a reference into a scheme that has no file", metric(concept("metric", "b", ` ; skos:related <${schemeIri("unit")}percent>`))),
  loadCase("a reference under a key with a slash", metric(concept("metric", "b", ` ; skos:related <${schemeIri("metric")}b/c>`))),
  loadCase("references that are not checked: other sites, the scheme itself, text", metric(concept("metric", "b", ` ; skos:closeMatch <http://purl.obolibrary.org/obo/STATO_0000415> ; skos:related <${schemeIri("metric")}> ; skos:note "${schemeIri("metric")}missing" ; skos:related <https://benchmarks.rewire.it/vocab#Thing> ; skos:related <https://benchmarks.rewire.it/vocab/Upper/x>`))),
  loadCase("a concept placed in another scheme than its IRI", metric(concept("metric", "a") + `:x skos:inScheme <${schemeIri("direction")}> .\n`)),
  loadCase("a concept placed in a scheme that is not one", metric(concept("metric", "a") + `:x skos:inScheme <https://example.org/scheme> ; skos:prefLabel "x" .\n`)),
  loadCase("a scheme whose name the reference pattern does not read", { "my_scheme.ttl": scheme("my_scheme", concept("my_scheme", "a", " ; skos:broader :missing") + `:x skos:inScheme <${schemeIri("other")}> .\n`) }),
  loadCase("broader and narrower said from both ends", metric(concept("metric", "a", " ; skos:broader :b") + concept("metric", "b", " ; skos:narrower :a"))),
  loadCase("a loop of three", metric(concept("metric", "a", " ; skos:broader :b") + concept("metric", "b", " ; skos:broader :c") + concept("metric", "c") + ":a skos:narrower :c .\n")),
  loadCase("a concept broader than itself", metric(concept("metric", "a", " ; skos:broader :a"))),
  loadCase("two loops, and a tail leading into one", metric(
    concept("metric", "a", " ; skos:broader :b") + concept("metric", "b", " ; skos:broader :a") +
      concept("metric", "c", " ; skos:broader :d") + concept("metric", "d", " ; skos:broader :c") +
      concept("metric", "t", " ; skos:broader :a") + concept("metric", "u", " ; skos:broader :t , :c"),
  )),
  loadCase("a loop across two schemes", {
    "direction.ttl": scheme("direction", concept("direction", "higher", ` ; skos:broader <${schemeIri("metric")}a>`)),
    "metric.ttl": scheme("metric", concept("metric", "a", ` ; skos:broader <${schemeIri("direction")}higher>`)),
  }),
  loadCase("a diamond is not a loop", metric(concept("metric", "top") + concept("metric", "l", " ; skos:broader :top") + concept("metric", "r", " ; skos:broader :top") + concept("metric", "bottom", " ; skos:broader :l , :r"))),
  loadCase("more than twenty problems", metric(concept("metric", "a") + many)),
  loadCase("a missing reference and a loop together", metric(concept("metric", "a", " ; skos:broader :a ; skos:related :gone"))),
  loadCase("the first file's fault is the one reported", { "alpha.ttl": scheme("alpha", concept("alpha", "Bad")), "beta.ttl": "" }),
  loadCase("text that is not Turtle", { "metric.ttl": "this is not turtle" }, false),
  loadCase("a statement left unfinished", { "metric.ttl": `${scheme("metric", "")}:a skos:prefLabel "a" ;\n` }, false),
];

// validateVocabularies against a mapping and a vocabulary small enough to read here.
const smallMapping = {
  fields: { name: { property: "p" }, status: { property: "p", scheme: "status" } },
  facets: { tasks: { property: "p" }, areas: { property: "p", scheme: "area" } },
  attributes: { url: { property: "p" }, metric: { property: "p", scheme: "metric" }, unit: { property: "p", scheme: "unit" }, ghost: { property: "p", scheme: "ghost" } },
  review: { date: { property: "p" }, method: { property: "p", scheme: "review-method" }, reviewer: { property: "p", scheme: "agent" } },
};
const smallVocabulary = {
  "status.ttl": scheme("status", concept("status", "discovered") + concept("status", "source_checked")),
  "area.ttl": scheme("area", concept("area", "dna-genomes") + concept("area", "proteins")),
  "metric.ttl": scheme("metric", concept("metric", "auprc") + concept("metric", "constructor") + concept("metric", "1")),
  "unit.ttl": scheme("unit", concept("unit", "percent")),
  "review-method.ttl": scheme("review-method", concept("review-method", "span-check")),
  "agent.ttl": scheme("agent", concept("agent", "intake-agent")),
  "derivation-method.ttl": scheme("derivation-method", concept("derivation-method", "difference")),
};
const smallRoot = vocabularyRoot(smallVocabulary);
const smallSchemes = loadSchemes(smallRoot);
fs.rmSync(smallRoot, { recursive: true });
const held = (id, extra = {}) => ({ id, facets: {}, attributes: {}, ...extra });
// `raw` gives the records as JSON text where the spelling of a number is the point.
const controlledCase = (name, records, mapping = smallMapping, raw = null) => {
  const input = raw ?? JSON.stringify(records);
  return {
    name,
    input,
    // The mapping the case is held to, where it is not the one recorded below as `mapping`.
    mapping: mapping === smallMapping ? null : mapping,
    values: JSON.parse(input).map((record) => controlledValues(mapping, record).map((found) => [found.field, found.scheme, JSON.stringify(found.value)])),
    error: thrown(() => validateVocabularies(mapping, JSON.parse(input), smallSchemes)),
  };
};
const good = held("good", {
  status: "source_checked",
  name: "anything",
  facets: { areas: ["dna-genomes", "proteins"], tasks: ["anything"] },
  attributes: { url: "anything", metric: "auprc", unit: "percent", review: { method: "span-check", reviewer: "intake-agent", date: "anything" }, derivation: { method: "difference", other: "anything" } },
});
const controlledCases = [
  controlledCase("no records", []),
  controlledCase("every controlled field holding a concept", [good]),
  controlledCase("a record with nothing controlled", [held("bare")]),
  controlledCase("a bad status", [held("r", { status: "Source checked" })]),
  controlledCase("a bad facet value among good ones", [held("r", { facets: { areas: ["dna-genomes", "genomics", "proteins"] } })]),
  controlledCase("a facet that is one text and not a list", [held("r", { facets: { areas: "genomics" } })]),
  controlledCase("an uncontrolled facet holds anything", [held("r", { facets: { tasks: ["x"], other: ["y"] } })]),
  controlledCase("a bad metric", [held("r", { attributes: { metric: "AUPRC" } })]),
  controlledCase("a metric that is a list with one bad item", [held("r", { attributes: { metric: ["auprc", "nope"] } })]),
  controlledCase("a metric that is a list inside a list", [held("r", { attributes: { metric: [["auprc"]] } })]),
  controlledCase("an empty list holds nothing to check", [held("r", { attributes: { metric: [] } })]),
  controlledCase("null and absent are not checked", [held("r", { status: null, attributes: { metric: null }, facets: { areas: null } })]),
  controlledCase("null inside a list is checked", [held("r", { attributes: { metric: [null] } })]),
  controlledCase("a metric that is empty text", [held("r", { attributes: { metric: "" } })]),
  controlledCase("values that are not text", [
    held("n", { attributes: { metric: 1 } }),
    held("f", { attributes: { metric: 1.5 } }),
    held("big", { attributes: { metric: 1e21 } }),
    held("small", { attributes: { metric: 1e-7 } }),
    held("t", { attributes: { metric: true } }),
    held("z", { attributes: { metric: false } }),
    held("zero", { attributes: { metric: 0 } }),
    held("o", { attributes: { metric: { b: 1, a: [2] } } }),
    held("u", { attributes: { metric: "\u00e9 \"quoted\" \u2028" } }),
  ]),
  controlledCase("a key that is a name on Object.prototype is only a concept if the scheme has it", [held("r", { attributes: { metric: "constructor", unit: "constructor" } })]),
  controlledCase("the number 1 is not the concept 1", [held("r", { attributes: { metric: 1 } }), held("s", { attributes: { metric: "1" } })]),
  controlledCase("a bad review method and reviewer", [held("r", { attributes: { review: { method: "looked", reviewer: "a person", actor: "anyone", date: "x" } } })]),
  controlledCase("a review that is a list, text, a number or null is not looked into", [
    held("a", { attributes: { review: [{ method: "looked" }] } }),
    held("b", { attributes: { review: "looked" } }),
    held("c", { attributes: { review: 3 } }),
    held("d", { attributes: { review: null } }),
    held("e", { attributes: { review: {} } }),
  ]),
  controlledCase("a bad derivation method", [held("r", { attributes: { derivation: { method: "guess" } } })]),
  controlledCase("a derivation method that is a list", [held("r", { attributes: { derivation: { method: ["difference", "guess"] } } })]),
  controlledCase("a derivation that is a list, text or null is not looked into", [
    held("a", { attributes: { derivation: [{ method: "guess" }] } }),
    held("b", { attributes: { derivation: "guess" } }),
    held("c", { attributes: { derivation: null } }),
    held("d", { attributes: { derivation: {} } }),
  ]),
  controlledCase("a field whose scheme has no file", [held("r", { attributes: { ghost: "x" } }), held("s", { attributes: { ghost: ["y", "z"] } })]),
  controlledCase("the same fault twice in one record is reported once", [held("r", { attributes: { metric: ["nope", "nope"] } })]),
  controlledCase("faults in several fields of several records, in order", [
    held("b", { status: "x", facets: { areas: ["y"] }, attributes: { unit: "u", metric: "m", review: { reviewer: "w", method: "v" }, derivation: { method: "d" } } }),
    held("a", { attributes: { metric: "m" } }),
  ]),
  controlledCase("twenty-five faults in one record stop at twenty", [held("r", { attributes: { metric: Array.from({ length: 25 }, (_, index) => `bad-${index}`) } })]),
  controlledCase("thirty records with two faults each", Array.from({ length: 30 }, (_, index) => held(`r-${String(index).padStart(2, "0")}`, { attributes: { metric: "m", unit: "u" } }))),
  controlledCase("after twenty faults a record with a good value first still reports its bad one", [
    held("first", { attributes: { metric: Array.from({ length: 20 }, (_, index) => `bad-${index}`) } }),
    held("second", { attributes: { metric: "auprc", unit: "bad-unit" } }),
    held("third", { attributes: { metric: "auprc", unit: "percent" } }),
  ]),
  controlledCase("numbers as JavaScript prints them, and one beyond any double", null, smallMapping, '[{"id":"r","facets":{},"attributes":{"metric":[1.0,-0,1e21,0.00001,12345678901234567890,1e400,[-1e400],{"a":1e400}]}}]'),
  controlledCase("integer-like keys inside a value are listed first", null, smallMapping, '[{"id":"r","facets":{},"attributes":{"metric":{"b":1,"10":2,"2":3}}}]'),
  controlledCase("a mapping with no controlled field", [held("r", { status: "x", attributes: { metric: "x" } })], { fields: { status: { property: "p" } }, facets: {}, attributes: {}, review: {} }),
];

write("tests/fixtures/vocabularies.json", {
  recorded_from: recordedFrom,
  // Each concept is its key, its IRI and its label, in the order loadSchemes holds them.
  load_schemes: loadCases,
  mapping: smallMapping,
  vocabulary: smallVocabulary,
  schemes: dumpSchemes(smallSchemes),
  // `values` is what controlledValues finds on each record: the field, the scheme and the
  // value as JSON. `error` is what validateVocabularies throws, or null.
  controlled: controlledCases,
});

// ---------------------------------------------------------------------------------------
// 4. The pinned store, as the TypeScript measures it
// ---------------------------------------------------------------------------------------

const records = loadRecords();
const mapping = JSON.parse(fs.readFileSync("data/ontology/mapping.json", "utf8"));
const schemes = loadSchemes();

const tally = (map, key, by = 1) => map.set(key, (map.get(key) ?? 0) + by);
const sorted = (map) => Object.fromEntries([...map].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
const linksByRelation = new Map();
const recordsByKind = new Map();
let sourceIds = 0;
for (const record of records) {
  tally(recordsByKind, record.kind);
  sourceIds += record.source_ids.length;
  for (const held of record.links) tally(linksByRelation, held.relation);
}
const controlledByField = new Map();
for (const record of records) for (const found of controlledValues(mapping, record)) tally(controlledByField, `${found.field} ${found.scheme}`);
const visible = publicRecords(records);

// One line per quad, in the order n3 reads them, so the digest fails on a parser that reads
// the files differently or in another order. A literal's language and datatype are part of
// the line; xsd:string and rdf:langString are what every plain literal has and are left out.
const plainTypes = new Set(["http://www.w3.org/2001/XMLSchema#string", "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"]);
const term = (node) =>
  node.termType === "Literal"
    ? `Literal ${JSON.stringify(node.value)} ${node.language} ${plainTypes.has(node.datatype.value) ? "" : node.datatype.value}`
    : `${node.termType} ${node.value}`;
const schemeFacts = {};
for (const [name, held] of schemes) {
  const quads = new Parser({ format: "Turtle" }).parse(fs.readFileSync(path.join("data/vocab", `${name}.ttl`), "utf8"));
  const lines = quads.map((quad) => `${term(quad.subject)}\t${quad.predicate.value}\t${term(quad.object)}\n`);
  schemeFacts[name] = {
    iri: held.iri,
    quads: quads.length,
    quads_sha256: sha256(lines.join("")),
    quads_sorted_sha256: sha256([...lines].sort().join("")),
    blank_nodes: quads.filter((quad) => quad.subject.termType === "BlankNode" || quad.object.termType === "BlankNode").length,
    concepts: [...held.concepts.values()].map((c) => [c.key, c.label]),
  };
}

// A value that is no concept, seeded into each controlled field in turn, on a record that
// holds nothing else. The gate seeds the same records and must refuse each in the same words.
const seededValue = "not-a-concept";
const seeds = [];
const seed_ = (field, record) => seeds.push({ field, record: JSON.stringify(record), error: thrown(() => validateVocabularies(mapping, [record], schemes)) });
for (const [name, held] of Object.entries(mapping.fields)) if (held.scheme) seed_(name, { id: "seeded", facets: {}, attributes: {}, [name]: seededValue });
for (const [name, held] of Object.entries(mapping.facets)) if (held.scheme) seed_(`facets.${name}`, { id: "seeded", facets: { [name]: [seededValue] }, attributes: {} });
for (const [name, held] of Object.entries(mapping.attributes)) if (held.scheme) seed_(`attributes.${name}`, { id: "seeded", facets: {}, attributes: { [name]: seededValue } });
for (const [name, held] of Object.entries(mapping.review)) if (held.scheme) seed_(`attributes.review.${name}`, { id: "seeded", facets: {}, attributes: { review: { [name]: seededValue } } });
seed_("attributes.derivation.method", { id: "seeded", facets: {}, attributes: { derivation: { method: seededValue } } });

write("scripts/expected/store-schema.json", {
  recorded_from: recordedFrom,
  // loadRecords returned, so the TypeScript accepts every record of the pinned store.
  records: records.length,
  records_by_kind: sorted(recordsByKind),
  source_ids: sourceIds,
  links: [...linksByRelation.values()].reduce((sum, count) => sum + count, 0),
  links_by_relation: sorted(linksByRelation),
  public_records: visible.length,
  public_ids_sha256: sha256(visible.map((record) => `${record.id}\n`).join("")),
  controlled_values: [...controlledByField.values()].reduce((sum, count) => sum + count, 0),
  // Keyed by the field and the scheme it is held to.
  controlled_values_by_field: sorted(controlledByField),
  schemes: schemeFacts,
  seeded: seeds,
});

console.log(`recorded ${cases.length} parse cases, ${moved.length} moved-key cases, ${loadCases.length} vocabulary loads, ${controlledCases.length} controlled-value cases, and ${records.length} store records`);
