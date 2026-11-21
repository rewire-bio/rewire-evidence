// The cases the validation fixture is recorded over. Nothing here needs the data repository's
// code: it reads records, picks a small connected store out of them, and writes down what to
// change. record_validation_fixture.mjs runs the TypeScript validators over each case.
//
// A case is a list of operations on a list of records, and tests/test_validation.py applies
// the same operations with rewire_evidence.conformance.apply_ops:
//
//   ["set", id, path, value]   put value at path inside record id; the last key is created
//   ["del", id, path]          delete the member, or remove the list item, at path
//   ["add", record]            append a record
//   ["drop", id]               remove record id
//
// A path is member names and list positions from the record itself.

export const clone = (value) => JSON.parse(JSON.stringify(value));

export function applyOps(records, ops) {
  const out = [...records];
  const held = (id) => {
    const index = out.findIndex((record) => record.id === id);
    if (index < 0) throw new Error(`no record ${id}`);
    out[index] = clone(out[index]);
    return index;
  };
  for (const [op, ...args] of ops) {
    if (op === "add") out.push(clone(args[0]));
    else if (op === "drop") out.splice(held(args[0]), 1);
    else {
      const [id, path, value] = args;
      let node = out[held(id)];
      for (const key of path.slice(0, -1)) {
        node = node[key];
        if (node === null || typeof node !== "object") throw new Error(`no ${path.join(".")} in ${id}`);
      }
      const last = path[path.length - 1];
      if (op === "set") node[last] = clone(value);
      else if (Array.isArray(node)) node.splice(last, 1);
      else delete node[last];
    }
  }
  return out;
}

// ---------------------------------------------------------------------------------------
// The base store: real records, chosen so every optional block and every rule has a record
// to stand on, closed over everything they cite so the store is valid on its own.
// ---------------------------------------------------------------------------------------

const idKeys = new Set([
  "source_ids", "summary_source_ids", "result_ids", "protocol_id", "dataset_id", "recipe_owner_id",
  "aggregation_source_id", "source_id", "applies_to", "record_id", "primary_sources", "method_ids", "resolved_by",
]);

function citedIds(value, found = []) {
  if (!value || typeof value !== "object") return found;
  for (const [key, child] of Object.entries(value)) {
    if (idKeys.has(key)) for (const id of Array.isArray(child) ? child : [child]) if (typeof id === "string") found.push(id);
    citedIds(child, found);
  }
  return found;
}

/** The records named and everything they cite, in the order they were given. */
export function closed(all, ids) {
  const byId = new Map(all.map((record) => [record.id, record]));
  const chosen = new Set();
  const pending = [...ids];
  // Claims that back a link from an assessment record, which is what lets a panel reach its owner.
  const backing = new Map();
  for (const record of all)
    if (record.kind === "claim" && typeof record.attributes.field === "string" && /^links:(part_of|evaluates_task):/.test(record.attributes.field))
      for (const link of record.links)
        if (link.relation === "subject") backing.set(link.target_id, [...(backing.get(link.target_id) ?? []), record.id]);
  while (pending.length) {
    const id = pending.pop();
    const record = byId.get(id);
    if (!record || chosen.has(id)) continue;
    chosen.add(id);
    pending.push(...record.source_ids, ...record.links.map((link) => link.target_id), ...citedIds(record.attributes), ...(backing.get(id) ?? []));
  }
  return chosen;
}

export function selectBase(all) {
  const byId = new Map(all.map((record) => [record.id, record]));
  const first = (test) => all.find(test);
  const has = (key) => (record) => record.attributes[key] !== undefined;
  const panels = (record) => (Array.isArray(record.attributes.comparison_panels) ? record.attributes.comparison_panels : []);
  const panelSize = (record) => panels(record).reduce((sum, panel) => sum + panel.result_ids.length, 0);
  const smallest = (test) => all.filter((record) => panels(record).length && test(record)).sort((a, b) => panelSize(a) - panelSize(b) || (a.id < b.id ? -1 : 1))[0];

  const roles = {
    derived: first((r) => r.kind === "result" && has("derivation")(r)),
    reproduction: first(has("reproduction")),
    recipes: first(has("run_recipes")),
    guide: first(has("run_guide")),
    documentation: first(has("run_documentation")),
    research: first(has("benchmark_research")),
    profile: first((r) => has("profile")(r) && r.attributes.profile.diagram) ?? first(has("profile")),
    profile_model: first((r) => r.kind === "model" && has("profile")(r)),
    panel_self: smallest((r) => panels(r).every((panel) => panel.protocol_id === r.id)),
    panel_parent: smallest((r) => panels(r).some((panel) => panel.protocol_id !== r.id)),
    concern_scoped: first((r) => Array.isArray(r.attributes.evidence_concerns) && r.attributes.evidence_concerns.some((e) => e && typeof e === "object" && e.applies_to)),
    concern_plain: first((r) => Array.isArray(r.attributes.evidence_concerns) && r.attributes.evidence_concerns.some((e) => typeof e === "string")),
    concern_whole: first((r) => Array.isArray(r.attributes.evidence_concerns) && r.attributes.evidence_concerns.some((e) => e && typeof e === "object" && !e.applies_to)),
    reproduced: first((r) => r.kind === "result" && r.status === "reproduced"),
    unchecked: first((r) => r.kind === "result" && r.status === "needs_review"),
    excluded: first((r) => r.kind === "model" && r.status === "excluded"),
    disputed: first((r) => r.kind === "result" && r.status === "disputed"),
    superseded: first((r) => r.kind === "claim" && r.status === "superseded"),
    covered: first((r) => r.kind === "result" && has("coverage")(r)),
    populated: first((r) => r.kind === "dataset" && has("reported_population")(r)),
    reuse: first((r) => r.links.some((link) => link.relation === "same_data_as")),
  };

  const chosen = closed(all, Object.values(roles).filter(Boolean).map((record) => record.id));
  // Roles any record can fill are filled from what is already chosen, so they add nothing.
  const records = all.filter((record) => chosen.has(record.id)).map(clone);
  const held = (test) => records.find(test);
  Object.assign(roles, {
    checked: held((r) => r.kind === "result" && r.status === "source_checked" && typeof r.attributes.numeric_value === "string"),
    uncertain: held((r) => r.kind === "result" && has("uncertainty")(r)),
    described: held((r) => r.kind === "model" && r.status !== "excluded" && r.description.trim()),
    described_other: held((r) => r.kind === "method" && r.status !== "excluded" && r.description.trim()),
    configured: held((r) => r.kind === "configuration" && r.status !== "excluded" && r.description.trim()),
  });
  for (const kind of ["model", "method", "configuration", "pipeline", "service", "benchmark", "task", "protocol", "evaluator", "dataset", "dataset_subset", "baseline", "evaluation", "result", "source", "claim", "use_case"])
    roles[`kind_${kind}`] = held((r) => r.kind === kind);
  return {
    records,
    roles: Object.fromEntries(Object.entries(roles).map(([name, record]) => [name, record ? record.id : null])),
  };
}

// ---------------------------------------------------------------------------------------
// Values put where another value was.
// ---------------------------------------------------------------------------------------

const hex = "a".repeat(64);
export const palette = [
  null, true, false, 0, 1, -1, 1.5, 3, 4, 0.5, 1e21, -2, 9007199254740993,
  "", " ", "x", " x ", "1", "1.5", "-1e3", ".5", "5.", "1e", "+", "Infinity", "0x10", "\uff11", "1\n",
  "2026-01-01", "2026-02-30", "2026-1-1", "2026-01-01\n", "2026-11-18", "2026-11-17",
  "2026-01-01T00:00:00Z", "2026-01-01T00:00:00+01:00", "2026-01-01T00:00:00", "2026-01-01T00:00Z", "2026-01-01T00:00:00.123456Z", "2026-01-01T24:00:00Z", "2026-01-01t00:00:00z",
  "https://example.org/a", "http://example.org", " https://example.org ", "https://exa mple.org", "ftp://example.org/a", "mailto:a@example.org", "example.org", "/a/b.txt", "/a b", "//example.org", "https://", "http:example.org", "c:/x",
  "10.1000/x", "10.1/x", "10.1000/", "doi:10.1000/x", "10.1000/a b",
  hex, hex.toUpperCase(), hex.slice(1), "abcdef1", "abcdef", "g".repeat(7),
  "a", "a-b", "-a", "A", "a_b", "a".repeat(255), "a".repeat(256),
  "model", "use_case", "rendered-source-visual-check",
  [], [""], ["x"], ["x", "x"], [" "], [1], [null], ["a-b", "A"], ["model"], ["model", "nope"], [[]], [{}], [1, 2, 3], [1, 2], [1, 2, 3, 4], [1.5, 2, 3], ["1", 2, 3],
  {}, { reason: "unreported" }, { reason: "nope" }, { a: { reason: "unreported" } }, { a: { reason: "unreported", note: "" } }, { a: { reason: "unreported", note: "n", extra: 1 } }, { a: null }, { length: 0 }, { length: 2 },
];

// ---------------------------------------------------------------------------------------
// Every way of damaging one valid value: each node replaced, each member removed, a member
// added, and then pairs and triples of those chosen by a generator with a stated seed, so the
// order zod reports several faults in is recorded too.
// ---------------------------------------------------------------------------------------

export function generator(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const swaps = [null, true, 0, 1, -1, 1.5, "", " ", "x", "2026-11-18", "2026-11-18T00:00:00Z", [], ["x"], [""], {}, { x: 1 }];

const deepSwaps = [null, 0, "", {}];

function paths(value, at = [], found = []) {
  if (value && typeof value === "object")
    for (const key of Array.isArray(value) ? value.keys() : Object.keys(value)) {
      found.push([...at, key]);
      paths(value[key], [...at, key], found);
    }
  return found;
}

/** Operations on a bare value: ["set", path, value], ["del", path]. An empty path is the value.
 * An operation aimed at something an earlier one removed does nothing. A list is reached by
 * position and an object by name, and a list grows only by one, at its end. */
export function applyValueOps(value, ops) {
  let root = clone(value);
  const isList = Array.isArray;
  const isObject = (node) => node !== null && typeof node === "object" && !isList(node);
  for (const [op, path, held] of ops) {
    if (!path.length) {
      root = clone(held);
      continue;
    }
    let node = root;
    for (const key of path.slice(0, -1)) {
      if (isList(node) && Number.isInteger(key) && key >= 0 && key < node.length) node = node[key];
      else if (isObject(node) && typeof key === "string" && Object.hasOwn(node, key)) node = node[key];
      else {
        node = undefined;
        break;
      }
    }
    const last = path[path.length - 1];
    if (isList(node) && Number.isInteger(last) && last >= 0) {
      if (op === "set" && last <= node.length) node[last] = clone(held);
      if (op === "del" && last < node.length) node.splice(last, 1);
    } else if (isObject(node) && typeof last === "string") {
      if (op === "set") node[last] = clone(held);
      else delete node[last];
    }
  }
  return root;
}

export function damage(example, seed, combined = 60) {
  const single = [];
  for (const swap of swaps) single.push([["set", [], swap]]);
  for (const path of paths(example)) {
    // Every value at the top two levels, and a few below, where the schemas repeat themselves.
    for (const swap of path.length <= 2 ? swaps : deepSwaps) single.push([["set", path, swap]]);
    single.push([["del", path]]);
  }
  for (const path of [[], ...paths(example)]) {
    const node = path.reduce((held, key) => held[key], example);
    if (node && typeof node === "object" && !Array.isArray(node)) {
      single.push([["set", [...path, "unknown_member"], 1]]);
      single.push([["set", [...path, "unknown_member"], 1], ["set", [...path, "another"], 2]]);
      single.push([["set", [...path, "__proto__x"], 1]]);
      single.push([["set", [...path, "7"], 1], ["set", [...path, "zz"], 1], ["set", [...path, "3"], 1]]);
    }
    if (Array.isArray(node)) {
      single.push([["set", [...path, node.length], node.length ? node[0] : "x"]]);
      single.push([["set", path, [...node, ...node, ...node, ...node, ...node].slice(0, 9)]]);
    }
  }
  const next = generator(seed);
  const several = [];
  for (let i = 0; i < combined; i += 1) {
    const count = 2 + Math.floor(next() * 3);
    const ops = [];
    for (let j = 0; j < count; j += 1) ops.push(...single[Math.floor(next() * single.length)]);
    several.push(ops);
  }
  return [...single, ...several];
}

// ---------------------------------------------------------------------------------------
// Faults seeded into the base store, one or more for each rule of each validator.
// ---------------------------------------------------------------------------------------

const A = "attributes";

export function validatorCases({ records, roles }) {
  const byId = new Map(records.map((record) => [record.id, record]));
  const at = (id) => byId.get(id);
  const linked = (id, relation) => at(id).links.find((link) => link.relation === relation).target_id;
  const cases = [];
  const add = (name, ...ops) => cases.push({ name, ops });
  const set = (id, path, value) => ["set", id, path, value];
  const del = (id, path) => ["del", id, path];
  const copy = (id, changes = {}) => ["add", { ...clone(at(id)), ...changes }];

  const source = roles.kind_source;
  const model = roles.described;
  const method = roles.described_other;
  const dataset = roles.kind_dataset;
  const subset = roles.reuse;
  const result = roles.checked;
  const evaluation = linked(result, "evaluation");
  const benchmark = roles.recipes;
  const claim = roles.kind_claim;
  const unpinned = records.find((r) => r.kind === "source" && r.attributes.artifact_sha256 === undefined).id;

  add("the base store, unchanged");

  // ---- validateRecords: the schema, IDs, private fields, sources and links ----
  add("a record that is not a record", set(model, ["name"], ""));
  add("an unknown top-level member", set(model, ["extra"], 1));
  add("two records with one ID", copy(model));
  for (const [name, path] of [
    ["attributes", [A, "email"]], ["attributes, in capitals", [A, "Email_Address"]], ["nested", [A, "provenance_note"]],
    ["facets", ["facets", "password"]],
  ])
    add(`a private field in ${name}`, set(model, path, name === "nested" ? { a: [{ Private_Notes: 1 }] } : name === "facets" ? [] : "x"));
  add("a private field name as a value is nothing", set(model, [A, "provenance_note"], "email token password"));
  add("a private field inside a list of text is nothing", set(model, [A, "provenance_note"], ["email", { ok: "token" }]));
  add("a source ID that names nothing", set(model, ["source_ids"], ["no-such-record"]));
  add("a source ID that names a model", set(model, ["source_ids"], [method]));
  add("an empty source ID", set(model, ["source_ids"], [""]));
  add("a link to nothing", set(model, ["links"], [{ relation: "family", target_id: "no-such-record" }]));
  add("a dataset said to be the same data as itself", set(dataset, ["links"], [{ relation: "same_data_as", target_id: dataset }]));
  add("a model said to be the same data as a dataset", set(model, ["links"], [{ relation: "same_data_as", target_id: dataset }]));
  add("a dataset said to be the same data as a model", set(dataset, ["links"], [{ relation: "same_data_as", target_id: model }]));
  add("a relation that cannot leave a model", set(model, ["links"], [{ relation: "system", target_id: method }]));
  add("a relation that cannot reach a source", set(model, ["links"], [{ relation: "family", target_id: source }]));

  // ---- benchmark_research ----
  const research = roles.research;
  const researchOf = clone(at(research).attributes.benchmark_research);
  add("a literature audit on a model", set(model, [A, "benchmark_research"], researchOf));
  add("a literature audit that is not one, on a model", set(model, [A, "benchmark_research"], 5));
  add("a literature audit with a member missing", del(research, [A, "benchmark_research", "status"]));
  add("a literature audit with two faults", set(research, [A, "benchmark_research", "review_date"], "2026"), set(research, [A, "benchmark_research", "gaps"], [""]));
  add("a literature audit citing a paper the record does not", set(research, [A, "benchmark_research", "primary_sources"], [unpinned]));
  add("a literature audit citing a record that is no source", set(research, [A, "benchmark_research", "primary_sources"], [research]));
  add("a literature audit citing nothing that exists", set(research, [A, "benchmark_research", "primary_sources"], ["no-such-record"]));
  add("a literature audit that is null", set(research, [A, "benchmark_research"], null));

  // ---- run_documentation and run_guide ----
  const documented = roles.documentation;
  const documentationOf = clone(at(documented).attributes.run_documentation);
  add("run documentation on a model", set(model, [A, "run_documentation"], { ...documentationOf, record_id: model }));
  add("run documentation naming another record", set(documented, [A, "run_documentation", "record_id"], model));
  add("run documentation that is not that", set(documented, [A, "run_documentation"], "see the README"));
  add("run documentation with an unknown status", set(documented, [A, "run_documentation", "status"], "executed"));
  add("run documentation citing a source the record does not", set(documented, [A, "run_documentation", "source_ids"], [source === documentationOf.source_ids[0] ? unpinned : source]));
  add("run documentation citing a source with no artifact hash", set(documented, [A, "run_documentation", "source_ids"], [unpinned]), set(documented, ["source_ids"], [...at(documented).source_ids, unpinned]));
  add("run documentation citing a record that is no source", set(documented, [A, "run_documentation", "source_ids"], [documented]));
  add("run documentation whose source has a hash in capitals", set(documentationOf.source_ids[0], [A, "artifact_sha256"], "A".repeat(64)));
  add("run documentation whose source has a number for a hash", set(documentationOf.source_ids[0], [A, "artifact_sha256"], 5));
  add("run documentation whose source has its hash in a list", set(documentationOf.source_ids[0], [A, "artifact_sha256"], [["c".repeat(64)]]));
  add("run documentation whose source has two hashes in a list", set(documentationOf.source_ids[0], [A, "artifact_sha256"], ["c".repeat(64), "c".repeat(64)]));
  add("run documentation whose source has a hash with a member named toString", set(documentationOf.source_ids[0], [A, "artifact_sha256"], { toString: 1 }));
  const guided = roles.guide;
  const guideOf = clone(at(guided).attributes.run_guide);
  add("a run guide on a model", set(model, [A, "run_guide"], { ...guideOf, record_id: model }));
  add("a run guide that is not one, on a model", set(model, [A, "run_guide"], null));
  add("a run guide naming another record", set(guided, [A, "run_guide", "record_id"], model));
  add("a run guide that claims it was executed", set(guided, [A, "run_guide", "status"], "executed"));
  add("a run guide with no steps", set(guided, [A, "run_guide", "steps"], []));
  add("a run guide with an unknown member in a step", set(guided, [A, "run_guide", "steps", 0, "note"], "x"));
  add("a run guide citing a source the record does not", set(guided, [A, "run_guide", "source_ids"], [unpinned]));
  add("a run guide with a step citing a source the record does not", set(guided, [A, "run_guide", "steps", 0, "source_ids"], [unpinned]));
  add("a run guide whose source lost its artifact hash", del(guideOf.source_ids[0], [A, "artifact_sha256"]));
  add("a run guide and run documentation both wrong", set(guided, [A, "run_guide", "record_id"], model), set(guided, [A, "run_documentation"], { ...documentationOf, record_id: model }));

  // ---- run_recipes and reproduction ----
  const recipes = [A, "run_recipes"];
  const recipeOf = clone(at(benchmark).attributes.run_recipes);
  add("recipes on a model", set(model, recipes, recipeOf));
  add("recipes on a dataset, and not recipes", set(dataset, recipes, "x"));
  add("no recipes in the list", set(benchmark, recipes, []));
  add("recipes that are not a list", set(benchmark, recipes, { id: "x" }));
  add("a recipe with an unknown purpose", set(benchmark, [...recipes, 0, "purpose"], "train"));
  add("a recipe with several faults", set(benchmark, [...recipes, 0, "inputs"], []), set(benchmark, [...recipes, 0, "requirements", "gpu"], "one"), del(benchmark, [...recipes, 0, "title"]));
  add("two recipes with one ID", set(benchmark, recipes, [recipeOf[0], recipeOf[0]]));
  add("a smoke-tested instruction with no receipt", set(benchmark, [...recipes, 0, "instructions", 0, "status"], "smoke_tested"));
  add("an executed instruction with no receipt", set(benchmark, [...recipes, 0, "instructions", 0, "status"], "executed"));
  const receipt = { source_id: recipeOf[0].source_ids[0], source_locator: "log", scope: "full_protocol", platform: "linux" };
  add("a smoke-tested instruction claiming the full protocol", set(benchmark, [...recipes, 0, "instructions", 0, "status"], "smoke_tested"), set(benchmark, [...recipes, 0, "instructions", 0, "validation_receipt"], receipt));
  add("an executed instruction with a full receipt", set(benchmark, [...recipes, 0, "instructions", 0, "status"], "executed"), set(benchmark, [...recipes, 0, "instructions", 0, "validation_receipt"], receipt));
  add("a smoke-tested instruction with a smoke receipt", set(benchmark, [...recipes, 0, "instructions", 0, "status"], "smoke_tested"), set(benchmark, [...recipes, 0, "instructions", 0, "validation_receipt"], { ...receipt, scope: "synthetic_smoke" }));
  add("a receipt citing a source the record does not", set(benchmark, [...recipes, 0, "instructions", 0, "status"], "executed"), set(benchmark, [...recipes, 0, "instructions", 0, "validation_receipt"], { ...receipt, source_id: unpinned }));
  add("a reviewed instruction whose receipt cites a source the record does not", set(benchmark, [...recipes, 0, "instructions", 0, "validation_receipt"], { ...receipt, source_id: unpinned }));
  add("a recipe citing a source the record does not", set(benchmark, [...recipes, 0, "source_ids"], [unpinned]));
  add("an instruction citing a record that is no source", set(benchmark, [...recipes, 0, "instructions", 0, "source_ids"], [benchmark]));
  add("a recipe whose source lost its artifact hash", del(recipeOf[0].source_ids[0], [A, "artifact_sha256"]));

  const repeated = roles.reproduction;
  const reproduction = [A, "reproduction"];
  const reproductionOf = clone(at(repeated).attributes.reproduction);
  const owner = reproductionOf.recipe_owner_id;
  add("a reproduction on a result", set(result, reproduction, reproductionOf));
  add("a reproduction that is not one", set(repeated, reproduction, []));
  add("a reproduction with a member missing and one unknown", del(repeated, [...reproduction, "explanation"]), set(repeated, [...reproduction, "extra"], 1));
  add("a reproduction naming a recipe its owner does not hold", set(repeated, [...reproduction, "recipe_id"], "no-such-recipe"));
  add("a reproduction of another purpose than the recipe", set(repeated, [...reproduction, "applicability"], "rescore_predictions"));
  add("a reproduction whose owner does not exist", set(repeated, [...reproduction, "recipe_owner_id"], "no-such-record"));
  add("a reproduction whose owner holds no recipes", del(owner, recipes));
  add("a reproduction whose owner's recipes are not valid", set(owner, [...recipes, 0, "purpose"], "train"));
  add("a reproduction whose owner's recipes are an empty list", set(owner, recipes, []));
  add("a reproduction owned by a record the evaluation is not assessed on", set(repeated, [...reproduction, "recipe_owner_id"], benchmark), set(repeated, [...reproduction, "recipe_id"], recipeOf[0].id), set(repeated, [...reproduction, "applicability"], recipeOf[0].purpose));
  add("a reproduction citing a source the record does not", set(repeated, [...reproduction, "source_ids"], [unpinned]));

  // ---- profile and extensions ----
  const profiled = roles.profile;
  const profile = [A, "profile"];
  add("a profile that is not one", set(profiled, profile, "a paragraph"));
  add("a profile that is null", set(profiled, profile, null));
  add("a profile with a section missing its sources", del(profiled, [...profile, "sections", 0, "source_ids"]));
  add("a profile citing nothing that exists", set(profiled, [...profile, "sections", 0, "source_ids"], ["no-such-record"]));
  add("a profile whose summary cites a model", set(profiled, [...profile, "summary_source_ids"], [model]));
  add("a profile whose fact cites a model", set(profiled, [...profile, "facts", 0, "source_ids"], [model]));
  add("a profile with summary sources and no locator", del(profiled, [...profile, "summary_source_locator"]));
  add("a limited profile with no gaps", set(profiled, [...profile, "coverage"], "limited"), set(profiled, [...profile, "gaps"], []));
  add("a reviewed profile with no sections", set(profiled, [...profile, "coverage"], "reviewed"), set(profiled, [...profile, "sections"], []));
  add("a profile whose locator is only spaces", set(profiled, [...profile, "sections", 0, "source_locator"], "   "));
  add("a profile whose locator has spaces around it", set(profiled, [...profile, "sections", 0, "source_locator"], "  Table 1  "));
  add("a profile on a source", set(source, profile, clone(at(profiled).attributes.profile)));
  add("a profile with no summary sources", del(profiled, [...profile, "summary_source_ids"]), del(profiled, [...profile, "summary_source_locator"]));
  const extensions = [A, "extensions"];
  const genomics = { assembly: "GRCh38", chromosome: "chr1", coordinate_system: "0-based-half-open", start: 10, end: 20, strand: "+", window_length: 100, variant_offset: 50, orientation: "transcript", window_placement: null };
  add("extensions, valid", set(result, extensions, { genomics, protein: { sequence_identity_threshold: 0.3 }, cellular: { dose: { value: 1.5, unit: "uM" }, time: null, biological_replicates: 3 }, molecular_measurement: {}, microbial: { taxonomy: "x" }, mechanistic: { solver: null } }));
  add("extensions, empty", set(result, extensions, {}));
  add("extensions that are null", set(result, extensions, null));
  add("extensions with an unknown block", set(result, extensions, { plants: {} }));
  add("extensions whose end is before the start", set(result, extensions, { genomics: { ...genomics, start: 30 } }));
  add("extensions whose variant is outside the window", set(result, extensions, { genomics: { ...genomics, variant_offset: 100 } }));
  add("extensions with both of those and a bad strand", set(result, extensions, { genomics: { ...genomics, start: 30, variant_offset: 100, strand: "x" } }));
  add("extensions with a threshold above one and a negative count", set(result, extensions, { protein: { sequence_identity_threshold: 1.5 }, cellular: { biological_replicates: -1, dose: { value: 1 } } }));

  // ---- source ----
  add("a source with no URL", del(source, [A, "url"]));
  for (const url of ["not a url", "", "ftp://example.org/a.pdf", "mailto:someone@example.org", "HTTP://EXAMPLE.ORG", " https://example.org ", "https://exa mple.org", "file:///tmp/a.pdf", "https://[::1]/a", "https://example.org:99999/", "//example.org/a", "https:example.org"])
    add(`a source whose URL is ${JSON.stringify(url)}`, set(source, [A, "url"], url));
  add("a source whose URL is a number", set(source, [A, "url"], 5));
  add("a source whose URL is a list", set(source, [A, "url"], ["https://example.org/a", "b"]));
  add("a source whose URL is an object", set(source, [A, "url"], { href: "https://example.org" }));
  add("a source whose URL is null", set(source, [A, "url"], null));
  add("a source whose URL has a member named toString", set(source, [A, "url"], { toString: "https://example.org" }));
  add("a source with no retrieval date", del(source, [A, "retrieved_at"]));
  add("a source with an empty retrieval date", set(source, [A, "retrieved_at"], ""));
  add("a source with zero for a retrieval date", set(source, [A, "retrieved_at"], 0));
  add("a source with no version", del(source, [A, "version"]), del(source, [A, "missing_metadata"]));
  add("a source with no version and a reason for it", del(source, [A, "version"]), set(source, [A, "missing_metadata"], { version: { reason: "unreported" } }));
  add("a source with no version and missing metadata that is text", del(source, [A, "version"]), set(source, [A, "missing_metadata"], "version"));
  add("a source with no version and missing metadata that is null", del(source, [A, "version"]), set(source, [A, "missing_metadata"], null));
  add("a source with an empty version and an empty reason", set(source, [A, "version"], ""), set(source, [A, "missing_metadata"], { version: "" }));

  // ---- result, evaluation, claim ----
  add("a result with no evaluation", set(result, ["links"], []));
  add("a result with two evaluations", set(result, ["links"], [...at(result).links, ...at(result).links]));
  add("a result with no printed value", del(result, [A, "printed_value"]));
  add("a result whose printed value is spaces", set(result, [A, "printed_value"], " \t\u00a0\ufeff"));
  add("a result whose printed value is a number", set(result, [A, "printed_value"], 0.5));
  add("a result with no metric", del(result, [A, "metric"]));
  add("a result with an empty unit", set(result, [A, "unit"], ""));
  for (const value of [0.5, "", "  ", "abc", "Infinity", "-Infinity", "NaN", "0x10", " 1e3 ", "1,5", "1e400", true, [], ["5"], {}, "0b101", "1_000", ".5", "5.", "+.5e-3", "\u00a01\u2028"])
    add(`a result whose numeric value is ${JSON.stringify(value)}`, set(result, [A, "numeric_value"], value));
  add("a result with no numeric value at all", del(result, [A, "numeric_value"]));
  add("a result whose numeric value is null", set(result, [A, "numeric_value"], null));
  add("a checked result with no sources", set(result, ["source_ids"], []));
  add("a checked result with no locator", del(result, [A, "source_locator"]));
  add("a checked result with no review", del(result, [A, "review"]));
  add("an unchecked result with no review", del(roles.unchecked, [A, "review"]), del(roles.unchecked, [A, "source_locator"]));
  add("a reproduced result of an evaluation that was not our run", set(result, ["status"], "reproduced"));
  add("a reproduced result of an evaluation with no origin", set(roles.reproduced, ["status"], "reproduced"), del(linked(roles.reproduced, "evaluation"), [A, "origin"]));
  for (const role of ["system", "assessment", "data"]) {
    add(`an evaluation with no ${role}`, set(evaluation, ["links"], at(evaluation).links.filter((link) => link.relation !== role)));
    add(`an evaluation with two of ${role}`, set(evaluation, ["links"], [...at(evaluation).links, at(evaluation).links.find((link) => link.relation === role)]));
  }
  add("an evaluation with no links", set(evaluation, ["links"], []));
  add("a claim with no subject", set(claim, ["links"], []));
  add("a claim whose only link is a source", set(claim, ["links"], [{ relation: "source", target_id: source }]));

  // ---- comparison panels ----
  const owner1 = roles.panel_self;
  const owner2 = roles.panel_parent;
  const panels = [A, "comparison_panels"];
  const panel1 = clone(at(owner1).attributes.comparison_panels[0]);
  const panel2 = clone(at(owner2).attributes.comparison_panels[0]);
  const p1 = [...panels, 0];
  const p2 = [...panels, 0];
  add("panels on a model", set(model, panels, [panel1]));
  add("panels that are not a list", set(owner1, panels, { id: "x" }));
  add("panels that are an empty list", set(owner1, panels, []));
  for (const value of [0, "", false, null]) add(`panels that are ${JSON.stringify(value)}`, set(owner1, panels, value));
  add("panels that are text", set(owner1, panels, "x"));
  add("a panel that is null", set(owner1, panels, [null]));
  add("a panel that is text", set(owner1, panels, ["x"]));
  for (const key of ["id", "title", "protocol_id", "dataset_id", "metric", "unit", "source_locator", "context"]) {
    add(`a panel with no ${key}`, del(owner1, [...p1, key]));
    add(`a panel whose ${key} is spaces`, set(owner1, [...p1, key], "  "));
  }
  add("a panel whose title is a number", set(owner1, [...p1, "title"], 5));
  add("two panels with one ID", set(owner1, panels, [panel1, panel1]));
  add("a panel with an unknown direction", set(owner1, [...p1, "direction"], "up"));
  add("a panel with no direction", del(owner1, [...p1, "direction"]));
  for (const key of ["result_ids", "source_ids", "caveats"]) {
    add(`a panel whose ${key} are empty`, set(owner1, [...p1, key], []));
    add(`a panel whose ${key} are text`, set(owner1, [...p1, key], "x"));
    add(`a panel whose ${key} hold a blank`, set(owner1, [...p1, key], [...panel1[key], " "]));
    add(`a panel whose ${key} hold a number`, set(owner1, [...p1, key], [...panel1[key], 5]));
  }
  add("a panel with one result", set(owner1, [...p1, "result_ids"], [panel1.result_ids[0]]));
  add("a panel with one result twice", set(owner1, [...p1, "result_ids"], [panel1.result_ids[0], panel1.result_ids[0]]));
  add("a panel with no review", del(owner1, [...p1, "review"]));
  add("a panel reviewed by another method", set(owner1, [...p1, "review", "method"], "manual"));
  add("a panel with a review date that is not a date", set(owner1, [...p1, "review", "date"], "2026-9-17"));
  add("a panel with a review date followed by a line break", set(owner1, [...p1, "review", "date"], "2026-09-17\n"));
  add("a panel with a review date that is a number", set(owner1, [...p1, "review", "date"], 20260917));
  add("a panel with no review date", del(owner1, [...p1, "review", "date"]));
  add("a panel with members nothing declares", set(owner1, [...p1, "note"], "x"), set(owner1, [...p1, "review", "note"], "x"));
  add("a panel whose protocol does not exist", set(owner1, [...p1, "protocol_id"], "no-such-record"));
  add("a panel whose protocol is a dataset", set(owner1, [...p1, "protocol_id"], panel1.dataset_id));
  add("a panel whose dataset does not exist", set(owner1, [...p1, "dataset_id"], "no-such-record"));
  add("a panel whose dataset is a protocol", set(owner1, [...p1, "dataset_id"], owner1));
  for (const status of ["superseded", "disputed", "excluded"]) {
    add(`a panel whose dataset is ${status}`, set(panel1.dataset_id, ["status"], status));
    add(`a panel whose protocol is ${status}`, set(panel2.protocol_id, ["status"], status));
    add(`a panel whose owner is ${status}`, set(owner2, ["status"], status));
  }
  add("an excluded owner with panels that are not panels", set(owner2, ["status"], "excluded"), set(owner2, panels, "x"));
  const backing = records.find((r) => r.kind === "claim" && r.attributes.field === `links:evaluates_task:${owner2}` && r.links.some((link) => link.relation === "subject" && link.target_id === panel2.protocol_id)).id;
  add("a panel on another record with no claim backing the link", ["drop", backing]);
  add("a panel on another record whose backing claim is unchecked", set(backing, ["status"], "needs_review"));
  add("a panel on another record whose backing claim is reproduced", set(backing, ["status"], "reproduced"));
  add("a panel on another record whose backing claim has no sources", set(backing, ["source_ids"], []));
  add("a panel on another record whose backing claim has no locator", del(backing, [A, "source_locator"]));
  add("a panel on another record whose backing claim has an empty locator", set(backing, [A, "source_locator"], ""));
  add("a panel on another record whose backing claim names another link", set(backing, [A, "field"], `links:part_of:${owner2}`));
  add("a panel on another record whose backing claim has another subject", set(backing, ["links"], [{ relation: "subject", target_id: owner2 }]));
  add("a panel on another record whose protocol no longer links to it", set(panel2.protocol_id, ["links"], []));
  add("a panel citing a source that does not exist", set(owner1, [...p1, "source_ids"], ["no-such-record"]));
  add("a panel citing a model", set(owner1, [...p1, "source_ids"], [model]));
  add("a panel citing a source with no artifact hash", set(owner1, [...p1, "source_ids"], [unpinned]));
  add("a panel citing a source whose hash is under its old name", del(panel1.source_ids[0], [A, "artifact_sha256"]), set(panel1.source_ids[0], [A, "sha256"], "b".repeat(64)));
  const row = panel1.result_ids[0];
  const rowEvaluation = linked(row, "evaluation");
  add("a panel result that does not exist", set(owner1, [...p1, "result_ids"], [panel1.result_ids[1], "no-such-record"]));
  add("a panel result that is an evaluation", set(owner1, [...p1, "result_ids"], [panel1.result_ids[1], rowEvaluation]));
  for (const status of ["needs_review", "superseded", "disputed", "excluded", "discovered", "reproduced"])
    add(`a panel result that is ${status}`, set(row, ["status"], status));
  for (const status of ["superseded", "disputed", "excluded", "needs_review"])
    add(`a panel result whose evaluation is ${status}`, set(rowEvaluation, ["status"], status));
  add("a panel result whose source carries a concern over everything", set(at(row).source_ids[0], [A, "evidence_concerns"], [{ message: "x" }]));
  add("a panel result whose source carries a plain-text concern", set(at(row).source_ids[0], [A, "evidence_concerns"], ["x"]));
  add("a panel result whose source carries a blank plain-text concern", set(at(row).source_ids[0], [A, "evidence_concerns"], ["  ", 5, null]));
  add("a panel result whose source carries a concern with an empty scope", set(at(row).source_ids[0], [A, "evidence_concerns"], [{ message: "x", applies_to: [] }]));
  add("a panel whose own source carries a concern", set(panel1.source_ids[0], [A, "evidence_concerns"], [{ message: "x" }]));
  add("a panel result named by a scoped concern on another source", set(unpinned, [A, "evidence_concerns"], [{ message: "x", applies_to: [row] }]));
  add("a panel result whose evaluation is named by a scoped concern", set(unpinned, [A, "evidence_concerns"], [{ message: "x", applies_to: [rowEvaluation] }]));
  add("a panel result whose system is named by a scoped concern", set(unpinned, [A, "evidence_concerns"], [{ message: "x", applies_to: [linked(rowEvaluation, "system")] }]));
  add("a panel result whose protocol's parent is named by a scoped concern", set(unpinned, [A, "evidence_concerns"], [{ message: "x", applies_to: [linked(owner1, "part_of")] }]));
  add("a scoped concern naming something off the path", set(unpinned, [A, "evidence_concerns"], [{ message: "x", applies_to: [model] }]));
  add("a scoped concern on a record that is no source", set(model, [A, "evidence_concerns"], [{ message: "x", applies_to: [row] }]));
  add("a panel result with a numeric value that is a number", set(row, [A, "numeric_value"], 0.5));
  add("a panel result with a numeric value that is not one", set(row, [A, "numeric_value"], "abc"));
  add("panel results with no numeric value", set(row, [A, "numeric_value"], null));
  add("a panel result of another metric", set(row, [A, "metric"], "auroc"));
  add("a panel result of another unit", set(row, [A, "unit"], "percent"));
  add("a panel result of another direction", set(row, [A, "metric_direction"], "lower"));
  add("a panel result with a qualifier the panel has not", set(row, [A, "metric_qualifier"], "per class"));
  add("a panel result with an empty qualifier", set(row, [A, "metric_qualifier"], ""));
  add("a panel result with a null qualifier", set(row, [A, "metric_qualifier"], null));
  add("a panel and its results with one qualifier", set(owner1, [...p1, "metric_qualifier"], "per class"), ...panel1.result_ids.map((id) => set(id, [A, "metric_qualifier"], "per class")));
  add("a panel result assessed on another protocol", set(rowEvaluation, ["links"], at(rowEvaluation).links.map((link) => (link.relation === "assessment" ? { ...link, target_id: panel2.protocol_id } : link))));
  add("a panel result on another dataset", set(rowEvaluation, ["links"], at(rowEvaluation).links.map((link) => (link.relation === "data" ? { ...link, target_id: panel2.dataset_id } : link))));
  add("a panel result that cites none of the panel's sources", set(row, ["source_ids"], [unpinned]));
  add("a panel result with no locator", del(row, [A, "source_locator"]));
  add("a panel result with a null locator", set(row, [A, "source_locator"], null));
  add("panel results that all test one system", ...panel1.result_ids.slice(1).map((id) => set(linked(id, "evaluation"), ["links"], at(linked(id, "evaluation")).links.map((link) => (link.relation === "system" ? { ...link, target_id: linked(rowEvaluation, "system") } : link)))));
  add("panel results of which only one has a number", ...panel1.result_ids.slice(1).map((id) => set(id, [A, "numeric_value"], null)));
  for (const field of ["split", "subset", "population", "aggregation"])
    add(`panel results that differ in ${field}`, set(rowEvaluation, [A, "comparison"], { ...(at(rowEvaluation).attributes.comparison ?? {}), [field]: "something else" }));
  add("a panel result whose evaluation has no comparison", del(rowEvaluation, [A, "comparison"]));
  add("panel results whose evaluations have comparisons that are text", ...panel1.result_ids.map((id) => set(linked(id, "evaluation"), [A, "comparison"], "split")));
  // A record the query reads in its older shape before any panel is resolved.
  add("a source with a licence under both names, while panels exist", set(source, [A, "license"], "MIT"), set(source, [A, "licence"], "Apache-2.0"));
  add("a source with a licence under both names, and no panels anywhere", set(source, [A, "license"], "MIT"), set(source, [A, "licence"], "Apache-2.0"), ...records.filter((r) => r.attributes.comparison_panels !== undefined).map((r) => del(r.id, panels)));
  // A panel shown on the record above it too, and one that disagrees with itself there.
  const child = panel2.protocol_id;
  add("the same panel on a protocol and on its task", set(child, panels, [panel2]));
  add("a panel on a protocol that differs from its task's of the same ID", set(child, panels, [{ ...panel2, title: "Another title" }]));
  add("a panel on a protocol that differs from its task's only in key order", set(child, panels, [Object.fromEntries(Object.entries(panel2).reverse())]));
  add("another panel on a protocol under a task", set(child, panels, [{ ...panel2, id: "another-panel" }]));
  add("a panel on a protocol under a task that is not a panel", set(child, panels, [{ ...panel2, direction: "up" }]));
  add("a panel on a protocol under a task with panels removed from the task", set(child, panels, [panel2]), del(owner2, panels));

  // What the query reads without being asked to: a profile on an evaluation, a claim with two
  // links, a field that is not text, and panels on a record below the owner.
  const profileOf = clone(at(roles.profile).attributes.profile);
  add("a panel result whose evaluation carries a profile", set(rowEvaluation, [A, "profile"], profileOf));
  add("a panel result whose evaluation's profile cites a source with a concern", set(rowEvaluation, [A, "profile"], profileOf), set(profileOf.sections[0].source_ids[0], [A, "evidence_concerns"], ["x"]));
  add("a panel result whose source carries a list for a concern", set(at(row).source_ids[0], [A, "evidence_concerns"], [["x"]]));
  add("a panel on another record whose backing claim has a second link", set(backing, ["links"], [...at(backing).links, { relation: "source", target_id: source }]));
  add("a panel on another record whose backing claim gives its field as a list", set(backing, [A, "field"], [`links:evaluates_task:${owner2}`]));
  add("the same panel on a protocol and on its task, with the backing claim's field as a list", set(panel2.protocol_id, panels, [panel2]), set(backing, [A, "field"], [`links:evaluates_task:${owner2}`]));
  add("a checked claim whose field has a member named toString", set(backing, [A, "field"], { toString: 1 }));
  add("an unchecked claim whose field has a member named toString", set(backing, [A, "field"], { toString: 1 }), set(backing, ["status"], "needs_review"));
  add("a protocol under a task with panels that are zero", set(panel2.protocol_id, panels, 0));
  // Results roll up one evaluates_task link from where they were measured and no further, so
  // a panel is not shown on a task two such links away, where it would clash with that task's own.
  const claimFor = (id, subject, field) => ["add", { ...clone(at(backing)), id, links: [{ relation: "subject", target_id: subject }], attributes: { ...clone(at(backing).attributes), field } }];
  const further = [
    ["add", { ...clone(at(owner2)), id: "another-task", name: "Another task", attributes: { ...clone(at(owner2).attributes), comparison_panels: [{ ...panel2, title: "Another title" }] } }],
    set(owner2, ["links"], [...at(owner2).links, { relation: "evaluates_task", target_id: "another-task" }]),
    claimFor("another-claim", owner2, "links:evaluates_task:another-task"),
  ];
  add("a task two evaluates_task links from a protocol, with its own panel of the protocol's panel's ID", set(panel2.protocol_id, panels, [panel2]), del(owner2, panels), ...further);
  add("a task one part_of link and one evaluates_task link from a protocol, with a clashing panel", set(panel2.protocol_id, panels, [panel2]), del(owner2, panels), further[0], set(owner2, ["links"], [...at(owner2).links, { relation: "part_of", target_id: "another-task" }]), claimFor("another-claim", owner2, "links:part_of:another-task"));
  add("a task one evaluates_task link from a task with panels, with a clashing panel", ...further);
  add("a protocol linked to its task twice", set(panel2.protocol_id, ["links"], [...at(panel2.protocol_id).links, { relation: "part_of", target_id: owner2 }]), claimFor("another-claim", panel2.protocol_id, `links:part_of:${owner2}`), set(panel2.protocol_id, panels, [panel2]));
  add("an excluded protocol under a task with panels that are not panels", set(panel2.protocol_id, panels, "x"), set(panel2.protocol_id, ["status"], "excluded"));

  // ---- validateConcerns ----
  const concerned = roles.concern_scoped;
  const concerns = [A, "evidence_concerns"];
  add("a scoped concern on a model", set(model, concerns, [{ message: "x", applies_to: [result] }]));
  add("an unscoped concern on a model", set(model, concerns, [{ message: "x" }, "plain"]));
  add("a concern whose scope is empty", set(concerned, concerns, [{ message: "x", applies_to: [] }]));
  add("a concern whose scope is text", set(concerned, concerns, [{ message: "x", applies_to: result }]));
  add("a concern whose scope holds a number", set(concerned, concerns, [{ message: "x", applies_to: [result, 5] }]));
  add("a concern whose scope is null", set(concerned, concerns, [{ message: "x", applies_to: null }]));
  add("a concern naming nothing that exists", set(concerned, concerns, [{ message: "x", applies_to: ["no-such-record", result, "nor-this"] }]));
  add("a concern naming a model and a source", set(concerned, concerns, [{ message: "x", applies_to: [model, source, result] }]));
  add("concerns that are not a list", set(concerned, concerns, { applies_to: ["no-such-record"] }));
  add("concerns of every other shape", set(concerned, concerns, [null, 5, true, "", "  ", ["x"], { applies_to: [result] }]));
  add("a list for a concern, which has no scope", set(concerned, concerns, [[{ applies_to: ["no-such-record"] }]]));
  add("more than twenty bad concerns", set(model, concerns, Array.from({ length: 12 }, (_, i) => ({ applies_to: [`no-such-record-${i}`] }))));

  // ---- validateDerivations ----
  const derived = roles.derived;
  const derivation = [A, "derivation"];
  const derivationOf = clone(at(derived).attributes.derivation);
  const input = derivationOf.inputs[0].source_id;
  add("a derivation whose script does not exist", set(derived, [...derivation, "script"], "scripts/omics/derive/missing.py"));
  add("a derivation whose script has another hash", set(derived, [...derivation, "script_sha256"], "0".repeat(64)));
  add("a derivation whose script is a folder", set(derived, [...derivation, "script"], "scripts"));
  add("a derivation whose script starts with a slash", set(derived, [...derivation, "script"], `/${derivationOf.script}`));
  add("a derivation whose script goes out of the repository and back", set(derived, [...derivation, "script"], `scripts/../${derivationOf.script}`));
  add("two results derived by one script", copy(derived, { id: "another-derived-result" }));
  add("two results derived by one script that does not exist", set(derived, [...derivation, "script"], "missing.py"), ["add", { ...clone(at(derived)), id: "another-derived-result", attributes: { ...clone(at(derived).attributes), derivation: { ...derivationOf, script: "missing.py" } } }]);
  add("a derivation whose aggregation source has a member named toString", set(derived, [...derivation, "aggregation_source_id"], { toString: 1 }));
  add("a derivation with a null numeric value", set(derived, [A, "numeric_value"], null));
  add("a derivation with no numeric value", del(derived, [A, "numeric_value"]));
  add("a derivation whose aggregation source does not exist", set(derived, [...derivation, "aggregation_source_id"], "no-such-record"));
  add("a derivation whose aggregation source is a model", set(derived, [...derivation, "aggregation_source_id"], model));
  add("a derivation whose result does not cite its sources", set(derived, ["source_ids"], [unpinned]));
  add("a derivation whose input does not exist", set(derived, [...derivation, "inputs", 0, "source_id"], "no-such-record"));
  add("a derivation whose input is a model", set(derived, [...derivation, "inputs", 0, "source_id"], model));
  add("a derivation whose input pins another hash", set(derived, [...derivation, "inputs", 0, "artifact_sha256"], "0".repeat(64)));
  add("a derivation whose input source has no hash", del(input, [A, "artifact_sha256"]));
  add("a derivation whose input source has a null hash", set(input, [A, "artifact_sha256"], null));
  add("a derivation whose input is from another publication", set(input, [A, "doi"], "10.1000/other"));
  add("a derivation whose aggregation source has no DOI", del(derivationOf.aggregation_source_id, [A, "doi"]));
  add("a derivation whose sources both have no DOI", del(derivationOf.aggregation_source_id, [A, "doi"]), del(input, [A, "doi"]));
  for (const origin of ["rewire_run", "quoted_from_paper", "author_reported", "independent_paper", "unreported", 5, null])
    add(`a derivation on an evaluation of origin ${JSON.stringify(origin)}`, set(linked(derived, "evaluation"), [A, "origin"], origin));
  add("a derivation on an evaluation with no origin", del(linked(derived, "evaluation"), [A, "origin"]));
  add("a derivation on a result with no evaluation", set(derived, ["links"], []));
  add("a derivation on a model", set(model, derivation, derivationOf));
  add("a derivation that is text", set(derived, derivation, "computed"));
  add("a derivation that is null", set(derived, derivation, null));
  add("a derivation that is zero", set(derived, derivation, 0));
  add("a derivation with everything wrong", set(derived, [...derivation, "script_sha256"], "0".repeat(64)), set(derived, [A, "numeric_value"], null), set(derived, ["source_ids"], []), set(input, [A, "doi"], "10.1000/other"), set(linked(derived, "evaluation"), [A, "origin"], "rewire_run"));
  for (const [name, value] of [
    ["an empty object", {}], ["a list", []], ["true", true], ["a number", 5],
    ["without inputs", { ...derivationOf, inputs: undefined }], ["with inputs that are text", { ...derivationOf, inputs: "x" }], ["with inputs that are an object", { ...derivationOf, inputs: {} }],
    ["with no inputs in the list", { ...derivationOf, inputs: [] }], ["with a null input", { ...derivationOf, inputs: [null] }], ["with a number for an input", { ...derivationOf, inputs: [5] }], ["with an empty input", { ...derivationOf, inputs: [{}] }],
    ["with a number for a script", { ...derivationOf, script: 5 }], ["with no script", { ...derivationOf, script: undefined }], ["with an empty script", { ...derivationOf, script: "" }], ["with a script outside the repository", { ...derivationOf, script: "../outside.py" }],
    ["with no script hash", { ...derivationOf, script_sha256: undefined }], ["with no aggregation source", { ...derivationOf, aggregation_source_id: undefined }], ["with a list for an aggregation source", { ...derivationOf, aggregation_source_id: [input] }],
  ])
    add(`a derivation that is ${name}`, set(derived, derivation, value));
  add("a derivation with two inputs, one of them bad", set(derived, [...derivation, "inputs"], [derivationOf.inputs[0], { ...derivationOf.inputs[0], source_id: model }, derivationOf.inputs[0]]));

  // ---- validateDescriptions ----
  add("a placeholder description", set(model, ["description"], "Named prediction model family from the source."));
  add("a description that matches three rules", set(model, ["description"], "Source-reported configuration as evaluated in the cited study (Table 2)."));
  add("an excluded record with a placeholder description", set(model, ["description"], "candidate catalogue entry"), set(model, ["status"], "excluded"));
  add("a dataset with a placeholder description", set(dataset, ["description"], "candidate catalogue entry"));
  add("two models with one description and two names", set(model, ["description"], "Predicts splicing from sequence."), copy(model, { id: "another-model", name: "Another", description: " predicts  SPLICING from\nsequence. " }));
  add("two models with one description and one name", set(model, ["description"], "Predicts splicing from sequence."), copy(model, { id: "another-model", name: `  ${at(model).name.toUpperCase()} `, description: "Predicts splicing from sequence." }));
  add("a model and a method with one description", set(model, ["description"], "Predicts splicing from sequence."), set(method, ["description"], "Predicts splicing from sequence."));
  add("two configurations with one description", copy(roles.configured, { id: "another-configuration", name: "Another" }));
  add("three models with one description", set(model, ["description"], "Predicts splicing."), copy(model, { id: "model-two", name: "Two", description: "Predicts splicing." }), copy(model, { id: "model-three", name: "Three", description: "Predicts splicing." }));
  add("two models with empty descriptions", set(model, ["description"], " "), copy(model, { id: "another-model", name: "Another", description: "" }));
  add("more than twenty bad descriptions", ...Array.from({ length: 22 }, (_, i) => copy(model, { id: `placeholder-${String(i).padStart(2, "0")}`, name: `Placeholder ${i}`, description: `Candidate catalogue entry ${i}` })));
  add("exactly twenty bad descriptions", ...Array.from({ length: 20 }, (_, i) => copy(model, { id: `placeholder-${String(i).padStart(2, "0")}`, name: `Placeholder ${i}`, description: `Candidate catalogue entry ${i}` })));

  // ---- validateAttributes, as loadRecords reaches it ----
  add("an attribute nothing declares", set(model, [A, "nothing_declares_this"], 1));
  add("an attribute of the wrong type", set(result, [A, "printed_value"], 5));
  add("more than twenty attribute problems", ...Array.from({ length: 21 }, (_, i) => set(model, [A, `undeclared_${i}`], i)));
  add("exactly twenty attribute problems", ...Array.from({ length: 20 }, (_, i) => set(model, [A, `undeclared_${i}`], i)));
  add("a review that carries the retired visual check after it was retired", set(result, [A, "review"], { method: ["rendered-source-visual-check"], date: "2026-11-18" }));

  // ---- validateVocabularies ----
  add("a metric that is no concept", set(result, [A, "metric"], "not-a-concept"));
  add("a status that is no concept, which the schema refuses first", set(result, ["status"], "not-a-status"));

  // ---- several validators at once: the first in loadRecords' order is the one reported ----
  add("a bad concept, a bad attribute, a bad description and a dangling link", set(result, [A, "metric"], "not-a-concept"), set(model, [A, "nothing_declares_this"], 1), set(model, ["description"], "candidate catalogue entry"), set(model, ["links"], [{ relation: "family", target_id: "no-such-record" }]));
  add("a bad description, a bad concern and a dangling link", set(model, ["description"], "candidate catalogue entry"), set(concerned, concerns, [{ applies_to: [] }]), set(model, ["links"], [{ relation: "family", target_id: "no-such-record" }]));
  add("a bad derivation and a bad description", set(derived, [...derivation, "script_sha256"], "0".repeat(64)), set(model, ["description"], "candidate catalogue entry"));
  // validateRecords stops at the first record in ID order, and at the first rule within it.
  add("two records with faults, the later in ID order listed first", set(result, [A, "printed_value"], ""), set(model, ["source_ids"], ["no-such-record"]));
  add("one record with a private field, a missing source and a dangling link", set(model, [A, "provenance_note"], { token: 1 }), set(model, ["source_ids"], ["no-such-record"]), set(model, ["links"], [{ relation: "family", target_id: "no-such-record" }]));

  // ---- values JavaScript reads in a way that is easy to miss ----
  // Text has a method named split, so a comparison that is text has a split and no subset.
  const rowEvaluations = [...new Set(panel1.result_ids.map((id) => linked(id, "evaluation")))];
  for (const text of ["x", ""])
    add(`one panel result whose evaluation has the comparison ${JSON.stringify(text)}`, set(rowEvaluation, [A, "comparison"], text));
  add("panel results whose evaluations all have the comparison \"x\"", ...rowEvaluations.map((id) => set(id, [A, "comparison"], "x")));
  add("panel results whose comparisons are text, but for one that is a number", ...rowEvaluations.map((id, index) => set(id, [A, "comparison"], index ? "x" : 5)));
  add("panel results whose comparisons are text, but for one that is a list", ...rowEvaluations.map((id, index) => set(id, [A, "comparison"], index ? "x" : ["x"])));
  // An aggregation source of the wrong kind is reported, and its DOI is still the one compared.
  const derivedEvaluation = linked(derived, "evaluation");
  const inputDoi = at(input).attributes.doi;
  add("a derivation whose aggregation source is its evaluation, which has the input's DOI", set(derived, [...derivation, "aggregation_source_id"], derivedEvaluation), set(derivedEvaluation, [A, "doi"], inputDoi));
  add("a derivation whose aggregation source is its evaluation, with the input's DOI, among the result's sources", set(derived, [...derivation, "aggregation_source_id"], derivedEvaluation), set(derivedEvaluation, [A, "doi"], inputDoi), set(derived, ["source_ids"], [...at(derived).source_ids, derivedEvaluation]));
  add("a derivation whose aggregation source is a model with another DOI", set(derived, [...derivation, "aggregation_source_id"], model), set(model, [A, "doi"], "10.1000/other"));
  add("a derivation whose aggregation source is a model with an empty DOI", set(derived, [...derivation, "aggregation_source_id"], model), set(model, [A, "doi"], ""));
  // A script no file system can hold is a script that does not exist.
  add("a derivation whose script has a name too long for a file", set(derived, [...derivation, "script"], `scripts/${"a".repeat(300)}.py`));
  add("a derivation whose script has a path too long for a file", set(derived, [...derivation, "script"], `${"scripts/".repeat(900)}a.py`));
  add("a derivation whose script name holds a null character", set(derived, [...derivation, "script"], "scripts/a\u0000b.py"));
  add("a derivation whose script name holds half a character", set(derived, [...derivation, "script"], "scripts/a\ud800b.py"));
  // A port and an address are numbers however many digits write them.
  for (const [what, url] of [
    ["a port of 4,400 zeros and 80", `http://example.org:${"0".repeat(4400)}80/`], ["a port of 4,400 ones", `http://example.org:${"1".repeat(4400)}/`],
    ["an address that ends in 4,400 ones", `http://1.2.3.${"1".repeat(4400)}/`], ["an address that ends in 4,400 zeros and 4", `http://1.2.3.${"0".repeat(4400)}4/`],
  ])
    add(`a source whose URL has ${what}`, set(source, [A, "url"], url));
  // Lowercase follows the Unicode version of the runtime. U+1C89 and U+1C8A came with Unicode 16.
  add("two descriptions that differ in the case of a letter Unicode 16 added", set(model, ["description"], "Scores a sequence \u1c89."), set(method, ["description"], "Scores a sequence \u1c8a."));
  add("two descriptions that differ in the form of a sigma", set(model, ["description"], "\u0391\u03a3 scores."), set(method, ["description"], "\u03b1\u03c2 scores."));
  add("two descriptions with a sigma that is final in one and not in the other", set(model, ["description"], "\u0391\u03a3 scores."), set(method, ["description"], "\u03b1\u03c3 scores."));
  return cases;
}

// ---------------------------------------------------------------------------------------
// attributeIssues on one record at a time.
// ---------------------------------------------------------------------------------------

/** Names every JavaScript object answers to, which the TypeScript looks up without asking
 * whether the registry or the record holds them itself. */
export const inherited = [
  "constructor", "toString", "valueOf", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable", "toLocaleString",
  "__proto__", "__defineGetter__", "__defineSetter__", "__lookupGetter__", "__lookupSetter__",
];

export function attributeCases() {
  const record = (kind, attributes) => ({ id: "r", kind, name: "R", description: "", status: "needs_review", facets: {}, source_ids: [], links: [], attributes });
  const reason = { reason: "unreported" };
  const cases = [
    ["no attributes", record("model", {})],
    ["an undeclared attribute", record("model", { nope: 1 })],
    ["an undeclared attribute that is null", record("model", { nope: null })],
    ["a null where text belongs", record("result", { printed_value: null })],
    ["a null numeric value", record("result", { numeric_value: null })],
    ["a null missing_metadata", record("result", { missing_metadata: null })],
    ["a null review", record("result", { review: null })],
    ["several faults in the order the keys were written", record("result", { unit: 5, nope: 1, printed_value: null, metric: "", numeric_value: "abc" })],
    ["a field with a value and a reason", record("result", { unit: "percent", missing_metadata: { unit: reason } })],
    ["a reason for an undeclared field", record("result", { missing_metadata: { nope: reason } })],
    ["a reason for a member of a declared field", record("result", { missing_metadata: { "coverage.scored": reason, "coverage.": reason, ".coverage": reason, "nope.scored": reason } })],
    ["a reason for missing_metadata itself", record("result", { missing_metadata: { missing_metadata: reason } })],
    ["a reason for the empty name", record("result", { missing_metadata: { "": reason } })],
    ["a field with a value and a reason, and the reason is malformed", record("result", { unit: "percent", missing_metadata: { unit: "unreported", nope: 5 } })],
    ["an empty missing_metadata", record("result", { missing_metadata: {} })],
    ["missing_metadata that is a list", record("result", { missing_metadata: [reason], unit: "x" })],
    ["missing_metadata that is a list, on a record with an attribute named 0", record("result", { missing_metadata: [reason, reason], 0: "x" })],
    ["missing_metadata that is text", record("result", { missing_metadata: "unit" })],
    ["missing_metadata that is a number", record("result", { missing_metadata: 5 })],
    ["missing_metadata that is false", record("result", { missing_metadata: false })],
    ["a kind with reasons for integer-like names", record("result", { missing_metadata: { 10: reason, 9: reason, unit: reason }, unit: "x" })],
    ["a concept that is empty", record("result", { metric: "" })],
    ["a list of concepts with a blank", record("model", { limitations: ["a", ""] })],
  ];
  for (const name of inherited) {
    cases.push([`an attribute named ${name}`, record("model", { [name]: "x" })]);
    cases.push([`a null attribute named ${name}`, record("model", { [name]: null })]);
    cases.push([`a reason for a field named ${name}`, record("model", { missing_metadata: { [name]: reason } })]);
    cases.push([`a reason for a member of a field named ${name}`, record("model", { missing_metadata: { [`${name}.x`]: reason } })]);
  }
  return cases.map(([name, held]) => ({ name, record: JSON.stringify(held) }));
}

// ---------------------------------------------------------------------------------------
// Valid values of each structured schema, to be damaged. Real ones come from the base store.
// ---------------------------------------------------------------------------------------

export function examples({ records, roles }) {
  const byId = new Map(records.map((record) => [record.id, record]));
  const attribute = (role, key) => clone(byId.get(roles[role]).attributes[key]);
  const receipt = { source_id: "s", source_locator: "log", scope: "selected_assay", platform: "linux" };
  const recipes = attribute("recipes", "run_recipes");
  recipes[0].instructions[0] = { ...recipes[0].instructions[0], status: "executed", validation_receipt: receipt };
  return {
    "missing-metadata": [{ a: { reason: "unreported" }, "b.c": { reason: "conflicting", note: "two values" } }],
    uncertainty: [
      attribute("covered", "uncertainty"),
      { type: "standard_deviation", printed: "\u00b1 0.1", value: "0.1", center: "0.5", n: 3, scope: "seeds", unit: "percent", source_column: "sd", note: "n" },
      { type: "confidence_interval", half_width: "0.05", level: 0.9, method: "analytic" },
      { type: "credible_interval", lower: "-1", upper: "+1.5e3", method: "propagated", resamples: 100 },
    ],
    coverage: [{ scored: 5, eligible: 10, unit: "variants", generated_per_run: 1, repeats: 3, note: "n" }],
    review: [
      byId.get(roles.derived).attributes.review,
      { method: ["transcription"], reviewer: ["claude"], date: "2026-10-01", reviewed_at: "2026-10-01T10:00:00Z", scope: "s", note: "n", method_note: "m", reviewer_note: "r", evidence: "e", source_id: "a-source", receipt: "r", artifact_sha256: hex, retrieval_url: "https://example.org/a" },
      { method: ["rendered-source-visual-check"], date: "2026-11-17" },
      { method: ["rendered-source-visual-check", "transcription"], reviewed_at: "2026-11-17T23:59:59Z" },
    ],
    derivation: [attribute("derived", "derivation")],
    "reported-population": [attribute("populated", "reported_population"), { count: 5, unit: "cells", train_validation_test: [1, 2, 3], note: "n" }],
    benchmark_research: [attribute("research", "benchmark_research")],
    run_documentation: [attribute("documentation", "run_documentation")],
    run_guide: [attribute("guide", "run_guide")],
    run_recipes: [recipes],
    reproduction: [attribute("reproduction", "reproduction")],
    profile: [attribute("profile", "profile"), attribute("profile_model", "profile")],
    extensions: [
      {
        genomics: { assembly: "GRCh38", chromosome: "chr1", coordinate_system: "0-based-half-open", start: 10, end: 20, strand: "+", window_length: 100, variant_offset: 50, orientation: "transcript", window_placement: "centred" },
        protein: { sequence_identity_threshold: 0.3, identity_algorithm: "mmseqs", msa_source: "uniref", msa_version: "1", templates: "none", template_cutoff: "2021", structure_availability: "pdb" },
        cellular: { donor: "d", cell_type: "t", perturbation: "p", dose: { value: 1.5, unit: "uM" }, time: { value: 24, unit: "h" }, biological_replicates: 3, batch: "b" },
        molecular_measurement: { molecule_identifier: "m", identifier_namespace: "n", assay_platform: "a", preprocessing: "p", measurement_unit: "u", ion_adduct: "i", identification_resolution: "r" },
        microbial: { taxonomy: "t", taxonomy_version: "v", reference_database: "d", reference_version: "v", sample_context: "c" },
        mechanistic: { solver: "s", solver_version: "v", constraints: "c", experimental_conditions: "e" },
      },
      { genomics: { assembly: null, start: null, end: 5, strand: null, coordinate_system: null, orientation: null, window_length: null, variant_offset: 0 }, cellular: { dose: null, biological_replicates: null } },
    ],
  };
}

// ---------------------------------------------------------------------------------------
// Descriptions.
// ---------------------------------------------------------------------------------------

export const descriptionTexts = [
  "Predicts the effect of a variant on splicing.",
  "Named prediction model family", "named prediction model family.", "A named prediction model family", "Named prediction model familyx", "NAMED PREDICTION MODEL FAMILY ", "\nNamed prediction model family",
  "Model identity as reported.", "identity as reportedly", "Identity  as reported", "identity\tas reported", "\u0131dent\u0131ty as reported", "identitY aS reporteD", "Identity as reported",
  "Source-reported configuration", "source-labelled configuration", "source-label configuration", "source-labeled configuration", "a source-reported configuration or submission label.", "xsource-reported configuration", "source-reported configurations",
  "Candidate catalogue entry", "candidate catalogue entry for review", "candidate catalog entry",
  "The model as run in the paper.", "As evaluated by the authors.", "as reported to us", "as scored for the leaderboard", "as listed on the page", "as used\nin the paper", "as used\u2028in the paper", "as used\rin the paper",
  `as printed ${"x".repeat(60)} in the table`, `as printed ${"x".repeat(59)} in the table`, `as printed ${"x".repeat(58)} in the table`, "as selected in", "as submitted byx", "as implementedin", "as benchmarked in", "as quoted for", "as trained and evaluated in the study", "as evaluated and reported on the site", "has run in", "as runs in", "as run within", "As Run In", "as run. In the paper",
  "Scores quoted in the paper.", "quoted from", "quotedin", "unquoted in",
  "not a new execution", "Not a newly executed run", "not a newly-executed run", "not a new-execution", "not a new executed", "not a newly execution",
  "in the cited study", "from the cited paper", "In The Cited Benchmark", "in the cited sources", "in the cited", "from the cited evaluations", "in the cited evaluation",
  "(this paper)", "(this source)", "( this paper )", "in this paper", "In This Source", "in this study", "(this study)", "within this paper",
  "source-reported", "Source-Reported label", "sourcereported", "source reported", "re-source-reported",
  "comparator reported by X", "evaluated by the Foo et al authors", "evaluated by the authors", "evaluated by the  authors", `evaluated by the ${"x".repeat(60)} authors`, `evaluated by the ${"x".repeat(61)} authors`, "evaluated by the Foo. Bar authors", "is the configuration evaluated in", "This is the configuration evaluated in the paper.", "evaluated by the Foo authorship",
  "cited in the source as X", "the method record names it", "Cited In The Source As",
  "A model (Table 2).", "A model (see Table S3 for details).", "A model (table 2).", "A model (Fig. 3).", "A model (Figure 2a).", "A model (Supplementary Note).", "A model (Section 3.1).", "A model (Introduction).", "A model (Appendix B).", "A model (Tables 2 and 3).", "A model (Fig 3).", "A model (Tablet 2).", "A model (see (Table 2)).", "A model (a (b) Table 2).", "A model Table 2.", "(Table\n2)", "(Supplementary\u00a0S1)", "(Figure S12, right)", "(xTable 2)", "(Table S)", "(TABLE 2)", "(Section)", "(SectionS1)",
  "A model (Smith et al. 2020).", "A model (Smith et al 2020).", "A model (et al)", "A model (Smith ET AL.)", "A model Smith et al. 2020", "(Smith et alia)", "(et al.)", "(Smith et al., 2020; Jones et al., 2021)", "(Smith (2020) et al.)", "(setal)", "(x et\nal.)",
  "", " ", "\u00a0",
  // Lowercase as the runtime's Unicode version has it, and the sigma that depends on its neighbours.
  "\u1c89", "\ua7cb \ua7cc \ua7da \ua7dc", "\ud803\udd50\ud803\udd65", "\u0391\u03a3", "\u0391\u03a3.\u0391", "\u0391\u03a3\u02b0", "\u0391\u03a3\u0301 \u03a3", "\u0130stanbul", "\ud801\udc00 \ud83a\udd00", "\u1e9e \u212a \u2126",
];

// ---------------------------------------------------------------------------------------
// Stores for `records -- descriptions`, each as its files. The command reads the lines as
// they are and asks the record schema nothing, so most of these rows are not records.
// ---------------------------------------------------------------------------------------

const rowLines = (...rows) => rows.map((row) => `${JSON.stringify(row)}\n`).join("");
const entity = (id, kind, description, more = {}) => ({ id, kind, name: `Name of ${id}`, description, status: "needs_review", ...more });
export const descriptionStores = [
  ["no files", {}],
  ["nothing to report", { "data/entities/models.jsonl": rowLines(entity("m1", "model", "Predicts the effect of a variant on splicing.")) }],
  ["problems of several rules in several kinds", {
    "data/entities/configurations.jsonl": rowLines(entity("c1", "configuration", "source-reported configuration"), entity("c2", "configuration", "Shared text."), entity("c3", "configuration", "Shared text.")),
    "data/entities/methods.jsonl": rowLines(entity("t1", "method", "Shared  TEXT."), entity("t2", "method", "As run in the paper (Table 2).")),
    "data/entities/models.jsonl": rowLines(entity("m1", "model", "candidate catalogue entry"), entity("m2", "model", "Shared text."), entity("m3", "model", "  shared text.  ", { name: "name of M2" }), entity("m4", "model", "candidate catalogue entry", { status: "excluded" })),
    "data/entities/notes.txt": "not a store file\n",
    "data/entities/pipelines.jsonl": rowLines(entity("p1", "pipeline", "A model (Smith et al. 2020), quoted in the paper.")),
    "data/entities/services.jsonl": rowLines(entity("s1", "service", "not a new execution")),
    "data/entities/sources.jsonl": rowLines(entity("x1", "source", "candidate catalogue entry")),
    "data/evidence/results/0.jsonl": rowLines(entity("r1", "result", "candidate catalogue entry")),
    "data/evidence/results/1.jsonl": rowLines(entity("m9", "model", "identity as reported")),
    "data/evidence/results/notes.txt": "not a store file\n",
    "data/provenance/records/0.jsonl": rowLines(entity("m8", "model", "candidate catalogue entry")),
  }],
  ["half a character in a description", { "data/entities/models.jsonl": rowLines(entity("m1", "model", "candidate catalogue entry \ud800"), entity("m2", "model", "\udc00 candidate catalogue entry \ud83e\uddec")) }],
  ["rows the record schema refuses", {
    "data/entities/models.jsonl": rowLines(
      entity("m1", "model", "candidate catalogue entry", { status: "nonsense", extra: 1 }), entity("m2", "model", 5), { id: "m3", kind: "model" }, entity("m4", 5, "candidate catalogue entry"), entity("m5", ["model"], "candidate catalogue entry"),
      entity("m6", "model", "candidate catalogue entry", { status: 5 }), { id: "m7", kind: "model", description: "candidate catalogue entry" }, entity("m8", "model", "candidate catalogue entry", { name: 5 }), entity("m9", "model", ""), entity("m10", "model", "   "),
    ),
  }],
  ["rows whose IDs are not text", {
    "data/entities/models.jsonl": rowLines(
      entity(7, "model", "candidate catalogue entry"), entity("7", "model", "identity as reported"), entity(7.0, "model", "quoted in"), entity(null, "model", "candidate catalogue entry"), entity(true, "model", "candidate catalogue entry"), entity(1, "model", "candidate catalogue entry"),
      entity(["a", "b"], "model", "candidate catalogue entry"), entity(["a", "b"], "model", "candidate catalogue entry"), entity({ a: 1 }, "model", "candidate catalogue entry"), entity([], "model", "candidate catalogue entry"), entity(1.5e21, "model", "candidate catalogue entry"), entity("a\tb\nc", "model", "candidate catalogue entry"),
      { kind: "model", name: "No ID", description: "candidate catalogue entry" },
    ),
  }],
  ["a shared description on rows whose IDs are not text", { "data/entities/models.jsonl": rowLines(entity(7, "model", "Shared text."), entity([1], "model", "Shared text."), entity({ a: 1 }, "model", "Shared text."), entity("m4", "model", "Shared text."), entity(7, "model", "Shared text.")) }],
  ["rows that are not objects", { "data/entities/models.jsonl": `5\n[]\n"x"\ntrue\n${rowLines(entity("m1", "model", "candidate catalogue entry"))}` }],
  ["a row that is null", { "data/entities/models.jsonl": `null\n${rowLines(entity("m1", "model", "candidate catalogue entry"))}` }],
  ["a line that is not JSON", { "data/entities/models.jsonl": `{\n${rowLines(entity("m1", "model", "candidate catalogue entry"))}` }],
  ["a shared description on a row with no name", { "data/entities/models.jsonl": rowLines(entity("m1", "model", "Shared text."), { id: "m2", kind: "model", description: "Shared text." }) }],
  ["a shared description on a row whose name is a number", { "data/entities/models.jsonl": rowLines(entity("m1", "model", "Shared text."), entity("m2", "model", "Shared text.", { name: 5 })) }],
  ["a shared description on two rows with one ID and no names", { "data/entities/models.jsonl": rowLines({ id: "m1", kind: "model", description: "Shared text." }, { id: "m1", kind: "model", description: "Shared text." }) }],
  ["a description shared with a configuration, and one of spaces", { "data/entities/configurations.jsonl": rowLines(entity("c1", "configuration", "Shared text.")), "data/entities/models.jsonl": rowLines(entity("m1", "model", "Shared text."), entity("m2", "model", " "), entity("m3", "model", " ")) }],
  ["a blank line, a line of spaces and no final line break", { "data/entities/models.jsonl": `${rowLines(entity("m1", "model", "candidate catalogue entry"))}\n  \n${JSON.stringify(entity("m2", "model", "quoted in"))}` }],
  ["more problems than a check would list", { "data/entities/models.jsonl": rowLines(...Array.from({ length: 25 }, (_, index) => entity(`m${index}`, "model", "candidate catalogue entry (this paper)"))) }],
];

// ---------------------------------------------------------------------------------------
// Records in the shapes written before October 2026, for normalizeAttributes.
// ---------------------------------------------------------------------------------------

export const uncertaintyValues = [
  null, "", "x", 5, true, [], ["x"],
  "95% CI 0.1-0.2", "95% CI 10%\u201320%", "95% CI 0.1-0.2 from 1,000 bootstrap replicates", "95% CI 0.1 - 0.2", "95% CI .1-.2", "95% CI 1.2.3-4", "95% ci 0.1-0.2", " 95% CI 0.1-0.2", "95% CI 0.1-0.2\n12 bootstrap replicates", "95% CI 1-2; 1,2,,3 bootstrap replicates",
  "\u00b1 0.05", "\u00b10.05 standard deviation", "\u00b1  0.05 over seeds ", "\u00b1 0.05standard deviation over 3 seeds", "\u00b1", "\u00b1 x", "\u00b10.05\nacross runs", "\u00b1 1.2.3",
  {}, { status: "unreported" }, { status: "unreported", note: "n" }, { status: "unreported", note: 5 }, { type: "unreported" }, { type: "unreported", value: 1 }, { type: "unreported", reported_spread: "x" }, { type: "unreported", printed_spread: "\u00b11" }, { type: "unreported", note: "n", printed: "p" },
  { type: "standard_deviation", value: "0.1" }, { type: "standard_deviation", value: 0.1 }, { type: "standard_deviation", value: " 0.1 " }, { type: "standard_deviation", value: "abc" }, { type: "standard_deviation" },
  { type: "standard_deviation", reported_spread: 1e21 }, { type: "standard_deviation", printed_value: "0.3", note: "n" }, { kind: "standard_deviation", value: 1e-7 },
  { type: "bootstrap_summary", printed_sd: 0.1, printed_mean: "0.5" }, { type: "bootstrap_summary_standard_deviation", printed_sd: "x" },
  { type: "standard_error", value: 2 }, { type: "propagated_standard_error", reported_spread: "0.2" }, { type: "standard_error_of_the_mean", printed_value: 3, n: 5, scope: "s", unit: "percent", source_column: "c" },
  { type: "confidence_interval", lower: 1, upper: 2 }, { type: "confidence interval", low: "1", high: "2", level: "95%" }, { type: "confidence-interval", level: 0.9, reported_half_width: 0.5 }, { type: "bootstrap_confidence_interval", lower: "1", upper: "2", replicates: 100 }, { type: "bootstrap_percentile_confidence_interval", lower: "1", upper: "2", resamples: 100, confidence_level: 0.95 },
  { type: "confidence_interval", level: "x%" }, { type: "confidence_interval", level: "95", lower: "a", upper: "b" }, { type: "confidenceinterval", value: 1 },
  { type: "range", value: "0.1" }, { type: "range", printed_spread: "\u00b10.1" }, { type: "range", printed_spread: "0.1" }, { type: "range" }, { type: "range", printed: "x" }, { type: "range", printed: 5, printed_spread: "y" },
  { type: "reported_plus_minus_type_unresolved", value: "0.1", note: "n" }, { type: "iqr", value: "0.1", note: "n" }, { type: "iqr", value: "0.1" },
  { value: "0.1" }, { type: null, value: "0.1" }, { type: 5, value: "0.1" }, { type: ["standard_deviation"], value: "0.1" }, { type: {}, value: "0.1" },
  { type: "standard_deviation", value: "0.1", n: "5", scope: 5, aggregation: "a", unit: "same_as_metric", source_column: 5 }, { type: "standard_deviation", value: "0.1", scope: "s", aggregation: "a" }, { type: "standard_deviation", value: "0.1", aggregation: "a" },
  { type: "standard_deviation", value: "0.1", extra: 1 }, { type: "standard_deviation", value: "0.1", printed: "p", printed_spread: "q" },
  { type: "confidence_interval", lower: "1", upper: "2", level: 0.95 }, { type: "confidence_interval", lower: "1", upper: "2", level: 95 },
];

export function normalizeCases() {
  const record = (id, kind, attributes, links = []) => ({ id, kind, name: id, description: "", status: "needs_review", facets: {}, source_ids: [], links, attributes });
  const one = (name, kind, attributes, lookup) => ({ name, records: [record("r", kind, attributes)], ...(lookup ? { lookup } : {}) });
  const cases = [
    one("nothing to change", "model", { a: 1, b: "x" }),
    one("moved and dropped attributes", "configuration", { acquisition_candidate_id: 1, legacy_row: 2, legacy_paper: 3, legacy_import_source_id: 4, local_cache_path: 5, historical_entity_links: 6, historical_missing_metadata: 7, metadata_review_scope: 8, benchmark_applicability: 9, published_score_reproduction: 10, suite_complete: 11, reference_kind: 12, entity_level: 13, kept: 14 }),
    one("entity_level stays on a benchmark", "benchmark", { entity_level: "suite", publication_status: "x" }),
    one("an evaluation loses its group note", "evaluation", { evaluation_group_note: "x", publication_status: "y", entity_level: "z" }),
    one("a renamed attribute", "source", { license: "MIT", locator: "l", sha256: "s", article_license: "a", code_license: "c" }),
    one("a renamed attribute already under both names", "source", { license: { a: 1, b: 2 }, licence: { a: 1, b: 2 }, version: "1" }),
    one("a renamed attribute under both names, differing in key order", "source", { license: { a: 1, b: 2 }, licence: { b: 2, a: 1 } }),
    one("a renamed attribute under both names, differing", "source", { license: "MIT", licence: "BSD" }),
    one("a renamed attribute keeps its place among new keys", "result", { z: 1, source_printed_cell: "c", a: 2 }),
    one("renames by kind", "dataset", { scope: "s", family: "f" }),
    one("renames on a subset", "dataset_subset", { dataset_id: "d", scope: "s" }),
    one("renames on a configuration", "configuration", { family: "f", foundation_model: true }),
    one("renames on a method", "method", { family: "f", method_type: "t", foundation_model: true }),
    one("method_type on a dataset stays", "dataset", { method_type: "t" }),
    one("missing reasons of every shape", "model", { missing_metadata: { a: { reason: "unreported" }, b: { reason: "nonsense", note: 5, extra: 1 }, c: { reason: 5 }, d: "unreported", e: "inapplicable", f: "", g: "  ", h: null, i: 5, j: ["x"], k: "Not applicable to this model", l: "the checkpoint is inaccessible", m: "not yet extracted", n: "legacy import", o: "nobody said", p: "NOT_APPLICABLE", q: "unpublished and not established", r: "toString", s: "constructor" } }),
    { ...one("missing reasons from a reviewed table", "model", { missing_metadata: { a: "really there", b: "kept note", c: "dropped note", d: "not in the table", e: "unreported" } }), lookup: [["really there", { reason: "not_missing", keepNote: false }], ["kept note", { reason: "conflicting", keepNote: true }], ["dropped note", { reason: "unavailable", keepNote: false }], ["unreported", { reason: "conflicting", keepNote: true }]] },
    one("missing_metadata that is not an object", "model", { missing_metadata: "x", a: 1 }),
    one("missing_metadata that is a list", "model", { missing_metadata: ["x"], a: 1 }),
    one("missing reasons come out sorted, after the keys that were there", "model", { z: 1, missing_metadata: { b: "x", a: "y", 10: "z", 9: "w", B: "v" }, y: null }),
    one("a reason for a field that has a value is dropped", "model", { a: 1, missing_metadata: { a: "unreported", b: "unreported" } }),
    one("a reason for the only field, which has a value", "model", { a: 1, missing_metadata: { a: "unreported" } }),
    one("bare nulls become reasons", "result", { a: null, numeric_value: null, source_discrepancy: null, undefined_reason: null, b: 1, missing_metadata: { a: "inapplicable" } }),
    one("a protocol's caveats join its limitations", "protocol", { caveats: ["a", 5, "b"], important_limitation: "c", limitations: ["z"] }),
    one("a protocol's caveats with text for limitations", "protocol", { caveats: "a", limitations: "z" }),
    one("a protocol's caveats with no limitations", "protocol", { important_limitation: "c" }),
    one("a protocol's caveats with empty limitations", "protocol", { caveats: ["a"], limitations: "" }),
    one("a protocol's caveats with nothing usable", "protocol", { caveats: [5, null, ["nested"]], important_limitation: null, limitations: ["z"] }),
    one("a protocol's nested caveats", "protocol", { caveats: [["a", "b"], "c"], important_limitation: ["d"] }),
    one("a protocol's uncertainty notes", "protocol", { uncertainty_definition: "d", uncertainty_note: "n", uncertainty: "not reported", procedure: { steps: 1 } }),
    one("a protocol's one uncertainty note, and a procedure that is text", "protocol", { uncertainty_mechanism: null, procedure: "p", uncertainty: { type: "x" } }),
    one("a protocol's uncertainty with a reason already", "protocol", { uncertainty: "not reported", missing_metadata: { uncertainty: "inapplicable" } }),
    one("an evaluation's protocol object", "evaluation", { protocol: { name: "P", version: "1", proposed_id: "p" }, run_url: "/runs/1" }),
    one("an evaluation's protocol object with a name only", "evaluation", { protocol: { name: "P" }, run_url: "https://x", reproduction_url: "https://x" }),
    one("an evaluation's protocol object with no name", "evaluation", { protocol: { version: null }, run_url: "https://x", reproduction_url: "https://y" }),
    one("an evaluation's run_url that is not text", "evaluation", { run_url: 5, protocol: "P" }),
    one("a dataset's population", "dataset", { population: { n: 5 }, reported_population: { count: null, note: "n", unit: null } }),
    one("a dataset's population that is text", "dataset", { population: "p", reported_population: { count: 5, note: null } }),
    one("a reported population with a split only", "result", { reported_population: { train_validation_test: [1, 2, 3], count: null } }),
    one("a reported population with nothing and a note that is not text", "result", { reported_population: { note: 5 } }),
    one("a reported population with a reason already", "result", { reported_population: {}, missing_metadata: { reported_population: "inapplicable" } }),
    one("a reported population that is text", "result", { reported_population: "many" }),
    one("a derived value that is a number", "result", { derived_normalized_value: 1e-7 }),
    one("a derived value that is a large number", "model", { derived_normalized_value: 123456789012345680000 }),
    one("a derived value that is text", "result", { derived_normalized_value: "0.5" }),
    one("a result's uncertainty note with an uncertainty", "result", { source_uncertainty_note: "n", uncertainty: { type: "standard_deviation", value: "0.1", note: "m" } }),
    one("a result's uncertainty note with an uncertainty that has no note", "result", { source_uncertainty_note: "n", uncertainty: { type: "standard_deviation", value: "0.1" } }),
    one("a result's uncertainty note alone", "result", { source_uncertainty_note: "n" }),
    one("a result's uncertainty note with a reason already", "result", { source_uncertainty_note: "n", missing_metadata: { uncertainty: { reason: "inapplicable", note: "old" } } }),
    one("a result's uncertainty note that is null", "result", { source_uncertainty_note: null, uncertainty: "\u00b1 0.1" }),
    one("a result's uncertainty that is null", "result", { uncertainty: null }),
    one("a result's uncertainty that is null with a reason already", "result", { uncertainty: null, missing_metadata: { uncertainty: "inapplicable" } }),
    one("a result's stated uncertainty beats a stale reason", "result", { uncertainty: "\u00b1 0.1", missing_metadata: { uncertainty: "unreported", other: "unreported" } }),
    one("a result's uncertainty that is a number", "result", { uncertainty: 5, a: 1 }),
    one("an evaluation's uncertainty is left alone", "evaluation", { uncertainty: "\u00b1 0.1" }),
    one("coverage as a fraction", "result", { coverage: "5 / 10" }),
    one("coverage as other text", "result", { coverage: "most" }),
    one("coverage with counts as text", "result", { coverage: { scored: "5", eligible: "unreported", note: "n" } }),
    one("coverage with counts that are not counts", "result", { coverage: { scored: "5.5", eligible: "" }, missing_metadata: { "coverage.scored": "inapplicable" } }),
    one("coverage that empties", "result", { coverage: { scored: "x" } }),
    one("coverage that is empty", "result", { coverage: {} }),
    one("coverage that is a number", "result", { coverage: 5 }),
    one("coverage that is a list", "result", { coverage: [1, 2] }),
    one("coverage that is null", "result", { coverage: null }),
    one("a review with empty members", "result", { review: { method: "transcription", reviewer: "a", actor: ["b", "a"], notes: "more", note: "first", date: "", scope: null } }),
    one("a review whose notes repeat its note", "result", { review: { method: ["m"], note: "same", notes: "same" } }),
    one("a review with notes only", "result", { review: { notes: "only", actor: "a" } }),
    one("a review with notes that are not text", "result", { review: { method: ["m"], notes: 5, note: 6 } }),
    one("a review reviewed on a date", "result", { review: { method: ["m"], reviewed_at: "2026-09-01" } }),
    one("a review reviewed on its own date", "result", { review: { method: ["m"], reviewed_at: "2026-09-01", date: "2026-09-01" } }),
    one("a review reviewed on another date", "result", { review: { method: ["m"], reviewed_at: "2026-09-01", date: "2026-09-02" } }),
    one("a review reviewed at a time on its date", "result", { review: { method: ["m"], reviewed_at: "2026-09-01T10:00:00Z", date: "2026-09-01" } }),
    one("a review reviewed at a time on another date", "result", { review: { method: ["m"], reviewed_at: "2026-09-01T10:00:00Z", date: "2026-09-02" } }),
    one("a review that is text", "result", { review: "checked", a: 1 }),
    one("a review that is null", "result", { review: null }),
    one("a review with no reviewers", "result", { review: { method: ["m"], reviewer: [], actor: [] } }),
    one("a source's review method", "source", { review_method: "automated_source_review", review_scope: "s" }),
    one("a source's review method by a person", "source", { review_method: "AI-assisted review by X", review: { method: ["transcription"], method_note: "earlier" } }),
    one("a source's other review method", "source", { review_method: "manual", review: { method: "automated-source-review" } }),
    one("a source's review scope only", "source", { review_scope: "s" }),
    one("a source's review scope with a review that is text", "source", { review_scope: "s", review: "x" }),
    one("a source's review method that is a number", "source", { review_method: 5 }),
    one("a result's review method is left alone", "result", { review_method: "automated_source_review" }),
    one("a review whose reviewers are not text", "result", { review: { method: ["m"], reviewer: [{ a: 1 }, ["x"], "a", 1, true, null], actor: [{ a: 1 }, ["x"], "a", 1, 1, true, null, "1", false, true] } }),
    one("a reason that is the name of the prototype", "model", { missing_metadata: { a: "__proto__", b: "hasOwnProperty" } }),
    one("integer-like attribute names", "model", { b: 1, 10: null, 2: "x", a: null }),
    one("a name every object answers to", "source", { toString: null, constructor: 1, missing_metadata: { valueOf: "x" } }),
  ];
  // Records that are read against each other.
  const system = { relation: "system", target_id: "c" };
  cases.push(
    { name: "an evaluation that repeats its configuration", records: [record("c", "configuration", { configuration: { a: 1, b: [2] }, execution: { gpu: 1 } }), record("e", "evaluation", { model_configuration: { a: 1, b: [2] }, execution: { gpu: 1 } }, [system])] },
    { name: "an evaluation that differs from its configuration in key order", records: [record("c", "configuration", { configuration: { a: 1, b: 2 }, execution: { gpu: 1, cpu: 2 } }), record("e", "evaluation", { model_configuration: { b: 2, a: 1 }, execution: { cpu: 2, gpu: 1 } }, [system])] },
    { name: "an evaluation and a configuration with neither member", records: [record("c", "configuration", {}), record("e", "evaluation", { other: 1 }, [system])] },
    { name: "a configuration whose evaluations do not all agree", records: [record("c", "configuration", { execution: 1 }), record("e", "evaluation", { execution: 1 }, [system]), record("f", "evaluation", { execution: 2 }, [system])] },
    { name: "a configuration with no evaluations", records: [record("c", "configuration", { execution: 1 })] },
    { name: "an evaluation whose system does not exist", records: [record("e", "evaluation", { model_configuration: 1 }, [system])] },
    { name: "an evaluation with two systems, the second of which repeats it", records: [record("c", "configuration", { configuration: 1 }), record("d", "configuration", { configuration: 2 }), record("e", "evaluation", { model_configuration: 2 }, [system, { relation: "system", target_id: "d" }])] },
    { name: "a result that repeats its evaluation", records: [record("e", "evaluation", { same_paper_experiment_set: "s", total_targets: 5, missing_count: 0, evidence_overlap: { a: 1 }, evidence_experiment_set_id: "x" }), record("r", "result", { same_paper_experiment_set: "s", total_targets: 6, missing_count: 0, evidence_overlap: { a: 1 }, evidence_experiment_set_id: "x" }, [{ relation: "evaluation", target_id: "e" }])] },
    { name: "a result whose evaluation does not exist", records: [record("r", "result", { total_targets: 5 }, [{ relation: "evaluation", target_id: "e" }])] },
    { name: "a result with no evaluation", records: [record("r", "result", { total_targets: 5 })] },
    { name: "two records with one ID, the later of which is read", records: [record("c", "configuration", { configuration: 1 }), record("c", "configuration", { configuration: 2 }), record("e", "evaluation", { model_configuration: 2 }, [system])] },
    { name: "no records", records: [] },
  );
  return cases;
}

// ---------------------------------------------------------------------------------------
// Values searched for a private field's name.
// ---------------------------------------------------------------------------------------

export const privateValues = [
  null, 0, "", "email", ["email"], {}, { name: "x" }, { email: null }, { EMAIL: 1 }, { Token_Hash: 1 }, { a: { b: [{ c: { password: 1 } }] } }, [{ id_token: 1 }], { "e-mail": 1 }, { emails: 1 }, { " email": 1 },
  { "\u0130d_token": 1 }, { "PR\u0130VATE_NOTES": 1 }, { private_correspondence: {} }, { owner_uid: 1, a: 1 }, { session_token: 1 }, { access_token: 1 }, { verification_token: 1 }, { contact_email: 1 }, { ok: "x", nested: { ok: ["token", { Token: 1 }] } },
];

// ---------------------------------------------------------------------------------------
// Stores on disk, each one fault away from loading.
// ---------------------------------------------------------------------------------------

/** Each case edits the files of a small store: {file: lines} with operations on them. */
export const loadCases = [
  ["the store as it is", []],
  ["two records out of order", [["swap", "record", 0, 1]]],
  ["one record twice", [["repeat", "record", 0]]],
  ["a record in another kind's file", [["move", "record", "other"]]],
  ["an evidence record in the wrong shard", [["move", "evidence", "wrong-shard"]]],
  ["a record with no provenance row", [["drop", "provenance", 0]]],
  ["a provenance row with no record", [["drop", "record", 0]]],
  ["a provenance row in the wrong shard", [["move", "provenance", "wrong-shard"]]],
  ["a record changed without its provenance row", [["edit", "record", 0]]],
  ["a record with its top-level keys out of order", [["reverse", "record", 0]]],
  ["a line that is not a record", [["damage", "record", 0]]],
  ["a blank line and no final line break", [["blank", "record"]]],
  ["no records and no provenance", [["empty"]]],
  ["a number beyond every double, with the digest of the null it is written as", [["beyond", "record", 0]]],
];

// ---------------------------------------------------------------------------------------
// Values written for the rules a schema states across its members, which damaging one
// member of a valid value seldom breaks. Each is JSON text, so a member named __proto__ is
// a member and not the object's prototype.
// ---------------------------------------------------------------------------------------

export function handwritten(held) {
  const text = (value) => JSON.stringify(value);
  const profile = held.profile[0];
  const steps = (count) => text({ ...profile, diagram: { title: "t", steps: Array(count).fill("s"), caption: "c", source_ids: ["s"], source_locator: "l" } });
  return {
    "missing-metadata": ['{"__proto__":{"reason":"unreported"}}', '{"__proto__":{"reason":"unreported"},"a":{"reason":"unreported"}}', '{"a":{"__proto__":1,"reason":"unreported"}}', "{}", "[]"],
    uncertainty: [
      { type: "standard_deviation", value: "1", lower: "0" }, { type: "standard_deviation", value: "1", half_width: "1" }, { type: "standard_deviation", value: "1", level: 0.5 },
      { type: "standard_deviation", value: "1", lower: "0", upper: "1" }, { type: "standard_deviation", value: "1", upper: "1" }, { type: "standard_error" }, { type: "unresolved_spread", lower: "0" },
      { type: "confidence_interval" }, { type: "confidence_interval", lower: "0" }, { type: "confidence_interval", upper: "1" }, { type: "confidence_interval", half_width: "1", upper: "2" },
      { type: "confidence_interval", lower: "x", upper: "y" }, { type: "confidence_interval", level: 1.5, half_width: "1" }, { type: "confidence_interval", level: 0, half_width: "1" },
      { type: "confidence_interval", level: 1, half_width: "1" }, { type: "confidence_interval", level: 0.999, half_width: "1" }, { type: "confidence_interval", level: "0.95", half_width: "1" },
      { type: "confidence_interval", n: 0, half_width: "1" }, { type: "confidence_interval", n: 1.5, half_width: "1" }, { type: "confidence_interval", n: 9007199254740992, half_width: "1" },
      { type: "confidence_interval", n: -9007199254740992, half_width: "1" }, { type: "confidence_interval", resamples: -1, half_width: "1" }, { type: "credible_interval", lower: "0", upper: "1", value: "0.5" },
      { type: "nope", value: "1" }, { type: "standard_deviation", value: 5 }, { type: "standard_deviation", value: "x" }, { type: "standard_deviation", extra: 1 }, { type: "standard_deviation", value: "1", lower: 5 },
      { type: "standard_deviation", value: "" }, { value: "1" }, { type: "standard_deviation", note: "" }, { type: "standard_deviation", value: "1", n: 1e21 },
    ].map(text),
    coverage: [{ scored: -1 }, { scored: 1.5 }, { eligible: 9007199254740992 }, { generated_per_run: 0 }, { repeats: 0, note: "" }, { scored: 0, eligible: 0 }, { scored: "5" }, {}].map(text),
    review: [
      { method: ["rendered-source-visual-check"], date: "2026-11-18" }, { method: ["rendered-source-visual-check"], date: "2026-11-17" }, { method: ["rendered-source-visual-check"] },
      { method: ["transcription", "rendered-source-visual-check"], reviewed_at: "2026-11-18T00:00:00+14:00" }, { method: ["rendered-source-visual-check"], reviewed_at: "2026-11-17T23:59:59-12:00" },
      { method: ["rendered-source-visual-check"], date: "2026-11-17", reviewed_at: "2026-11-18T00:00:00Z" }, { method: ["rendered-source-visual-check"], date: "2026-11-18", reviewed_at: "2026-11-01T00:00:00Z" },
      { method: ["rendered-source-visual-check"], date: "2027" }, { method: ["rendered-source-visual-check"], date: "2025" }, { method: ["rendered-source-visual-check"], reviewed_at: "2026-12" },
      { method: ["rendered-source-visual-check"], reviewed_at: "\ud83e\uddec2026-11-18T00:00:00Z" }, { method: ["rendered-source-visual-check"], reviewed_at: "9" }, { method: ["rendered-source-visual-check"], reviewed_at: "" },
      { method: ["transcription"], date: "2026-11-18" }, { method: [], date: "2026-11-18" }, { method: ["rendered-source-visual-check", "rendered-source-visual-check"], date: "2026-12-01" },
      { method: ["rendered-source-visual-check"], date: null }, { method: ["rendered-source-visual-check"], date: "2026-11-18", extra: 1 }, { method: ["Rendered-Source-Visual-Check"], date: "2026-11-18" },
      { method: "rendered-source-visual-check", date: "2026-11-18" }, { method: ["rendered-source-visual-check", ""], date: "2026-11-18" }, { method: ["m"], retrieval_url: " https://example.org " }, { method: ["m"], retrieval_url: "example.org" },
    ].map(text),
    "reported-population": [{ train_validation_test: [1, 2] }, { train_validation_test: [1, 2, 3, 4] }, { train_validation_test: ["1", 2, 3] }, { train_validation_test: [1.5, 2, 3] }, { train_validation_test: [1, 2, 3, "4"] }, { train_validation_test: [] }, { train_validation_test: "123" }, { train_validation_test: { length: 3 } }, { count: -1 }, { count: 0 }, {}].map(text),
    profile: [steps(0), steps(1), steps(2), steps(8), steps(9), text({ ...profile, summary_source_ids: [] }), text({ ...profile, summary_source_locator: "  " }), text({ ...profile, coverage: "limited", gaps: [] }), text({ ...profile, coverage: "limited", gaps: [""] }), text({ ...profile, coverage: "reviewed", sections: [] }), text({ ...profile, coverage: "reviewed", sections: [], gaps: [], summary_source_ids: undefined })],
    extensions: [
      { genomics: { start: 5, end: 5 } }, { genomics: { start: 6, end: 5 } }, { genomics: { variant_offset: 5, window_length: 5 } }, { genomics: { variant_offset: 4, window_length: 5 } },
      { genomics: { start: -1, end: -2 } }, { genomics: { start: 1.5, end: 1 } }, { genomics: { start: 6, end: 5, variant_offset: 9, window_length: 1 } }, { genomics: { start: 6, end: 5, extra: 1 } }, { genomics: { start: 6, end: null } },
      { genomics: { window_length: 0 } }, { protein: { sequence_identity_threshold: 0 } }, { protein: { sequence_identity_threshold: 1 } }, { protein: { sequence_identity_threshold: -0.1 } }, { protein: { sequence_identity_threshold: 1.1 } },
      { cellular: { dose: { value: "1", unit: "" } } }, { cellular: { dose: { value: 1 } } }, { cellular: { dose: { value: 1, unit: "uM", extra: 1 } } }, { genomics: null }, { genomics: { strand: "" } },
    ].map(text),
  };
}
