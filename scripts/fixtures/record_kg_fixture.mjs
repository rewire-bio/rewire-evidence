// Record what the TypeScript KG export, attribute-terms, draft-block and subset/shapes check
// do at STORE_REF, so the Python port is compared with the code it replaces.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_kg_fixture.mjs
//
// Writes:
//   src/rewire_evidence/data/attribute-type-schemas.json
//   scripts/expected/store-kg.json
//   tests/fixtures/kg.json
import { execFileSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { applyOps } from "./validation_cases.mjs";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(path.join(store, "package.json"));
const { z } = require("zod");

const load = async (file) => {
  const loaded = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(loaded.default ?? {}), ...loaded };
};

const { loadRecords } = await load("scripts/omics/records.ts");
const { publicRecords } = await load("scripts/omics/schema.ts");
const { attributeTypes } = await load("shared/omics/attributes.ts");
const { declarationsTurtle, shapesTurtle } = await load("scripts/kg/attribute-terms.ts");
const { draftBlock } = await load("scripts/kg/draft-block.ts");
const {
  readMapping,
  buildContext,
  canonicalDecimal,
  canonicalJson,
  exportQuadLines,
  subsetQuadLines,
  writeQuads,
} = await load("scripts/kg/export.ts");

const storeRef = execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim();
const version = (name) => JSON.parse(fs.readFileSync(path.join(store, "node_modules", name, "package.json"), "utf8")).version;
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const writeJson = (rel, value) => {
  const target = path.join(repo, rel);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, JSON.stringify(value, null, 2) + "\n");
};

const MODULES = [
  "scripts/kg/export.ts",
  "scripts/kg/attribute-terms.ts",
  "scripts/kg/draft-block.ts",
  "scripts/kg/publish.ts",
];
const moduleHashes = Object.fromEntries(MODULES.map((m) => [m, sha256(fs.readFileSync(path.join(store, m)))]));

// 1. attribute-type-schemas.json for draft-block
const schemas = Object.fromEntries(
  Object.entries(attributeTypes).map(([name, schema]) => {
    const { $schema: _, ...rest } = z.toJSONSchema(schema, { io: "input", unrepresentable: "any" });
    return [name, rest];
  }),
);
writeJson("src/rewire_evidence/data/attribute-type-schemas.json", {
  recorded_from: {
    script: "scripts/fixtures/record_kg_fixture.mjs",
    store_ref: storeRef,
    source: "shared/omics/attributes.ts",
    zod: version("zod"),
  },
  sha256: sha256(JSON.stringify(schemas)),
  schemas,
});

// 2. Full store KG outputs & chunk digests
const mapping = readMapping();
const records = loadRecords();
const pub = publicRecords(records);
const lines = exportQuadLines(mapping, records);
const CHUNK = 50000;
const fullHash = crypto.createHash("sha256");
const chunkHashes = [];
for (let i = 0; i < lines.length; i += CHUNK) {
  const chunk = lines.slice(i, i + CHUNK).join("\n") + "\n";
  fullHash.update(chunk);
  chunkHashes.push(sha256(chunk));
}

const rbText = fs.readFileSync(path.join(store, "data/ontology/rb.ttl"), "utf8");
const declaredInRb = new Set([...rbText.matchAll(/^(rb:[A-Za-z0-9]+) a /gm)].map((m) => m[1]));
const contextText = JSON.stringify(buildContext(mapping), null, 2) + "\n";
const declText = declarationsTurtle(mapping, declaredInRb);
const shapesText = shapesTurtle(mapping);
const draftObj = draftBlock(mapping);
const draftText = JSON.stringify(draftObj, null, 2) + "\n";

// Sample real subset at STORE_REF
const sampleIds = [records[0].id, records[Math.floor(records.length / 2)].id, records[records.length - 1].id];
const sampleSubset = subsetQuadLines(mapping, records, sampleIds);

writeJson("scripts/expected/store-kg.json", {
  recorded_from: {
    script: "scripts/fixtures/record_kg_fixture.mjs",
    store_ref: storeRef,
  },
  modules: moduleHashes,
  records: records.length,
  public_records: pub.length,
  asserted_quads: lines.length,
  asserted_sha256: fullHash.digest("hex"),
  chunk_size: CHUNK,
  chunk_sha256s: chunkHashes,
  context_sha256: sha256(contextText),
  declarations_sha256: sha256(declText),
  attribute_shapes_sha256: sha256(shapesText),
  draft_block_sha256: sha256(draftText),
  sample_subset: {
    ids: sampleIds,
    quads: sampleSubset.lines.length,
    checked: sampleSubset.checked.length,
    context: sampleSubset.context.length,
    sha256: sha256(sampleSubset.lines.join("\n") + "\n"),
  },
});

// 3. Unit fixtures & P5/P6 subset+shapes oracle
const valFixture = JSON.parse(fs.readFileSync(path.join(repo, "tests/fixtures/validation.json"), "utf8"));
const writeFixture = JSON.parse(fs.readFileSync(path.join(repo, "tests/fixtures/write-path.json"), "utf8"));
const baseRecords = valFixture.base.records.map((line) => JSON.parse(line));

const decodeURISafe = (value) => {
  try {
    return decodeURI(value);
  } catch {
    return value;
  }
};
const tsIri = (value) =>
  `<${encodeURI(decodeURISafe(value)).replace(/[<>"{}|^`\\]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase())}>`;

const uriInputs = [
  "https://benchmarks.rewire.it/id/abc-123",
  "https://example.org/path/with space?a=1&b=2#frag",
  "https://example.org/already%2Fencoded%20ok/%ZZ",
  "https://example.org/nonascii-\u00e9-\u03c4-\ud83d\ude00",
  'https://example.org/quotes<>"`^{}|\\end',
];
const decimalInputs = [
  "0",
  "-0",
  "+12.3400",
  "1.39e-16",
  "-2.5E+4",
  "007.50",
  "not-a-decimal",
  " .5 ",
];

const jsonInputs = [
  { b: 1, a: 2, "10": "ten", "2": "two" },
  [{ z: null, a: [true, false, 1e-5] }],
];

// Pick subset+shapes check cases from P5 seeded faults & P6 accepted/faulty cases using small stores
const runShapesOnly = (subset) => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kg-rec-"));
  try {
    writeQuads(path.join(tmp, "asserted.nq"), subset.lines);
    fs.writeFileSync(path.join(tmp, "context.txt"), subset.context.map((x) => x + "\n").join(""));
    const py = path.join(repo, ".venv/bin/python");
    const cmd = fs.existsSync(py) ? py : "python3";
    const res = spawnSync(cmd, ["scripts/kg/build.py", "--shapes-only", "--kg-dir", tmp, "--context", path.join(tmp, "context.txt")], {
      cwd: store,
      encoding: "utf8",
    });
    if (res.error || (res.status !== 0 && res.status !== 1)) {
      throw new Error(`build.py --shapes-only failed unexpectedly: status=${res.status} err=${res.error} stderr=${res.stderr}`);
    }
    return { status: res.status, conforms: res.status === 0 };
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
};

const ontologyFiles = {
  "data/ontology/mapping.json": fs.readFileSync(path.join(store, "data/ontology/mapping.json"), "utf8"),
  "data/ontology/rb.ttl": rbText,
  "data/ontology/shapes.ttl": fs.readFileSync(path.join(store, "data/ontology/shapes.ttl"), "utf8"),
  "data/ontology/attribute-shapes.ttl": fs.readFileSync(path.join(store, "data/ontology/attribute-shapes.ttl"), "utf8"),
  "data/ontology/rb-attributes.ttl": fs.readFileSync(path.join(store, "data/ontology/rb-attributes.ttl"), "utf8"),
};

const safeSubsetAndShapes = (recs, ids, withShapes) => {
  try {
    const sub = subsetQuadLines(mapping, recs, ids);
    return {
      ok: true,
      lines_count: sub.lines.length,
      lines_sha256: sha256(sub.lines.join("\n") + "\n"),
      checked: sub.checked,
      context: sub.context,
      ...(withShapes ? { shapes_result: runShapesOnly(sub) } : {}),
    };
  } catch (error) {
    return { ok: false, error: String(error?.message ?? error) };
  }
};

const baseSubset = subsetQuadLines(mapping, baseRecords, [baseRecords[0].id]);
const applyEdits = (baseFiles, edits) => {
  const files = { ...baseFiles };
  for (const [file, text] of Object.entries(edits ?? {})) {
    if (text === null) delete files[file];
    else files[file] = text;
  }
  return files;
};

const P5_INDICES = [0, 10, 11, 13, 15, 17, 18, 98, 100, 116, 124, 125, 126, 129, 134, 152, 153, 154, 160, 161, 162, 164, 167, 168];
const p5SubsetCases = P5_INDICES.map((idx) => {
  const c = valFixture.validators[idx];
  const cloned = applyOps(baseRecords, c.ops ?? []);
  const ids = cloned.map((r) => r.id);
  const runShapes = cloned.every((r) => r && typeof r.id === "string" && typeof r.kind === "string");
  return {
    name: c.name,
    ops: c.ops ?? [],
    ids,
    ...safeSubsetAndShapes(cloned, ids, runShapes),
  };
});

const p6Selected = [
  ...writeFixture.cases
    .filter(
      (c) =>
        c.error === null &&
        Object.keys(c.after ?? {}).length > 0 &&
        !c.name.includes("rules refuse") &&
        !c.name.includes("beyond every double"),
    )
    .slice(0, 6),
  ...writeFixture.cases.filter((c) => c.name.includes("rules refuse")),
  ...writeFixture.cases.filter((c) => c.error !== null && c.input && c.command === "add").slice(0, 4),
];
const p6Cases = p6Selected.map((c) => {
  const baseFiles = applyEdits(writeFixture.stores[c.store], c.edits);
  const files = c.error === null ? applyEdits(baseFiles, c.after) : baseFiles;
  const storeRecords = Object.entries(files)
    .filter(([k]) => !k.startsWith("data/provenance/") && typeof files[k] === "string")
    .flatMap(([, body]) => body.split("\n").filter(Boolean).map((l) => JSON.parse(l)));
  if (c.error !== null && c.command === "add" && typeof c.input === "string") {
    for (const line of c.input.split("\n").filter(Boolean)) {
      try {
        storeRecords.push(JSON.parse(line));
      } catch {}
    }
  }
  const safeRecords = storeRecords.filter((r) => r && typeof r === "object" && typeof r.id === "string" && typeof r.kind === "string" && Array.isArray(r.source_ids) && Array.isArray(r.links) && r.facets && typeof r.facets === "object" && r.attributes && typeof r.attributes === "object" && typeof r.status === "string");
  const ids = safeRecords.map((r) => r.id);
  return { name: c.name, command: c.command, ids, records: safeRecords, ...safeSubsetAndShapes(safeRecords, ids, true) };
});

writeJson("tests/fixtures/kg.json", {
  recorded_from: {
    script: "scripts/fixtures/record_kg_fixture.mjs",
    store_ref: storeRef,
  },
  ontology: ontologyFiles,
  context_text: contextText,
  declarations_text: declText,
  shapes_text: shapesText,
  draft_block: draftObj,
  uri_cases: uriInputs.map((input) => ({ input, output: tsIri(input) })),
  decimal_cases: decimalInputs.map((input) => ({ input, output: canonicalDecimal(input) ?? null })),
  json_cases: jsonInputs.map((input) => ({ input, output: canonicalJson(input) })),
  base_subset: {
    id: baseRecords[0].id,
    lines: baseSubset.lines,
    checked: baseSubset.checked,
    context: baseSubset.context,
    shapes_result: runShapesOnly(baseSubset),
  },
  p5_subset_cases: p5SubsetCases,
  p6_subset_cases: p6Cases,
});

console.log(`Recorded KG fixture at ${storeRef}: ${lines.length} quads, ${chunkHashes.length} chunks, ${Object.keys(schemas).length} attribute schemas.`);
