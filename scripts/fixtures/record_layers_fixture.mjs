// Record what the TypeScript layer derivation does, so the Python port is compared with the
// code it replaces and not with a reading of it.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_layers_fixture.mjs
//
// It runs inside the data repository, in the `fixtures` job in CI, and nowhere else. The
// cases come from layers_cases.mjs, which needs none of the data repository's code.
//
//   tests/fixtures/layers.json
//       What classify, the container readers, the markup scanner, the Office readers, the
//       grid builder and its header rule, layerOf, the sanity gate and the registry build
//       return or throw for each case, and what `npm run layers -- build`, `check` and
//       `thresholds` print and write on small trees laid out in a scratch folder. It names
//       the commit, and the SHA-256 of each TypeScript file the port was made from.
//
// The stored objects themselves are held by the layer gate, against the committed registry.
// This holds what they have no example of. The runner has no pdftotext, so a PDF here is an
// object with no layer and the reason says so, which is what the port does when told the
// same.
import { execFileSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import * as cases from "./layers_cases.mjs";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const MODULES = [
  "shared/archive/layers/classify.ts",
  "shared/archive/layers/container.ts",
  "shared/archive/layers/text.ts",
  "shared/archive/layers/xml.ts",
  "shared/archive/layers/html.ts",
  "shared/archive/layers/markup.ts",
  "shared/archive/layers/pdf.ts",
  "shared/archive/layers/index.ts",
  "shared/archive/layers/types.ts",
  "shared/archive/layers/grid/types.ts",
  "shared/archive/layers/grid/index.ts",
  "shared/archive/layers/grid/tables.ts",
  "shared/archive/layers/grid/header.ts",
  "shared/archive/layers/office/index.ts",
  "shared/archive/layers/office/package.ts",
  "shared/archive/layers/office/xml.ts",
  "shared/archive/layers/office/sheet.ts",
  "shared/archive/layers/office/document.ts",
  "shared/omics/layers/types.ts",
  "shared/omics/layers/registry.ts",
  "shared/omics/layers/sanity.ts",
  "scripts/omics/layers/cli.ts",
  "scripts/omics/extract/pdf.ts",
];

const load = async (file) => {
  const loaded = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(loaded.default ?? {}), ...loaded };
};
const classify = await load("shared/archive/layers/classify.ts");
const container = await load("shared/archive/layers/container.ts");
const markup = await load("shared/archive/layers/markup.ts");
const xmlRules = (await load("shared/archive/layers/xml.ts")).xmlRules;
const htmlRules = (await load("shared/archive/layers/html.ts")).htmlRules;
const layers = await load("shared/archive/layers/index.ts");
const grid = await load("shared/archive/layers/grid/index.ts");
const office = await load("shared/archive/layers/office/index.ts");
const officeXml = await load("shared/archive/layers/office/xml.ts");
const sanity = await load("shared/omics/layers/sanity.ts");
const registry = await load("shared/omics/layers/registry.ts");
const pdf = await load("shared/archive/layers/pdf.ts");

if (pdf.hasPdftotext()) throw new Error("This recording is made on a machine with no pdftotext, and this one has it.");

const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const storeRef = execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim();
const dirtyBefore = execFileSync("git", ["status", "--porcelain"], { cwd: store, encoding: "utf8" });
const from = (hex) => Buffer.from(hex, "hex");
const attempt = (run) => {
  try {
    return run();
  } catch (error) {
    return { error: error.message };
  }
};
const rules = { xml: xmlRules, html: htmlRules };

const layerOf = (layer) =>
  layer && {
    normaliser: layer.normaliser,
    tool: layer.tool,
    tool_version: layer.tool_version,
    derivation: layer.derivation,
    input_sha256: layer.input_sha256,
    output_sha256: layer.output_sha256,
    bytes: layer.bytes.length,
    characters: layer.characters,
    identity: layer.identity,
    text: layer.bytes.toString("utf8"),
  };
const nodeOf = (node) => ({
  sha256: node.sha256,
  bytes: node.bytes,
  classification: node.classification,
  layer: layerOf(node.layer),
  grid: layerOf(node.grid),
  unsupported: node.unsupported,
  problems: node.problems,
  members: node.members.map((member) => ({ path: member.path, node: nodeOf(member.node) })),
});
const expansionOf = (expansion) => ({
  kind: expansion.kind,
  members: expansion.members.map((member) => ({ path: member.path, sha256: member.sha256, bytes: member.bytes.length })),
  problems: expansion.problems,
});

function scanned(kind, text) {
  const events = [];
  const raw = markup.scanRaw(text, rules[kind], {
    open: (name, attributes, selfClosing, at) => events.push(["open", name, attributes, at]),
    close: (name, at) => events.push(["close", name, at]),
    text: (start, end) => events.push(["text", start, end]),
  });
  return [kind, text, raw, markup.scanMarkup(text, rules[kind]), events];
}
function xmlEvents(text) {
  const events = [];
  officeXml.scanXml(text, {
    open: (name, attributes, selfClosing) => events.push(["open", name, Object.entries(attributes), selfClosing]),
    close: (name) => events.push(["close", name]),
    text: (value) => events.push(["text", value]),
  });
  return [text, events];
}
const packageOf = (members) => ({
  kind: "xlsx",
  evidence: "a case",
  members: new Map(Object.entries(members).map(([name, hex]) => [name, from(hex)])),
  problems: [],
});
function partsOf(members) {
  const problems = [];
  const strings = attempt(() => office.sharedStrings(packageOf(members)));
  const sheets = attempt(() => office.sheetParts(packageOf(members), problems));
  return [members, { strings, sheets, problems }];
}
function sheetOf(text, strings) {
  const problems = [];
  const sheet = attempt(() => office.readSheet(text, strings, "xl/worksheets/case.xml", problems));
  return [text, strings, { sheet, problems }];
}
const officeOf = (result) => result && { ...result, layer: layerOf(result.layer) };
function gridOf(normaliser, text) {
  const kind = normaliser === "jats-table-grid@1" ? "xml" : "html";
  const input = Buffer.from(text, "utf8");
  return [
    normaliser,
    text,
    attempt(() => {
      const built = grid.gridOf(text, rules[kind], normaliser, "the text normaliser", "0".repeat(64));
      return { serialised: grid.serialiseGrid(built.grid), text: built.text, header: grid.headerOf(built.grid) };
    }),
    attempt(() => {
      const textLayer = kind === "xml" ? layers.layerOf(input, { pdftotext: false }) : layers.layerOf(input, { mediaType: "text/html", pdftotext: false });
      return { text: layerOf(textLayer.layer), grid: layerOf(textLayer.grid), problems: textLayer.problems };
    }),
  ];
}
function metricsOf(text) {
  const metrics = sanity.metricsOf(text, null);
  return [text, metrics, sanity.ratiosOf(metrics)];
}
function everyCharacter() {
  const parts = [];
  for (let code = 0; code <= 0x10ffff; code += 1) if (code < 0xd800 || code > 0xdfff) parts.push(String.fromCodePoint(code));
  return sanity.metricsOf(parts.join(""), 7);
}
const objectOf = (name, patch) => [name, patch, attempt(() => registry.layersOfObject(cases.rowFor(name, patch), cases.objects[name]))];
function buildOf({ names, missing }) {
  const rows = names.map((name) => cases.rowFor(name, { source_ids: [name, "shared"] }));
  const held = new Map(names.filter((name) => !missing.includes(name)).map((name) => [sha256(cases.objects[name]), cases.objects[name]]));
  const seen = [];
  const result = registry.buildRegistry(
    rows,
    (key) => {
      if (!held.has(key)) throw new Error("the bytes are not in the object cache");
      return held.get(key);
    },
    (key, done) => seen.push([key, done]),
  );
  const shuffled = [...result.records].reverse();
  return {
    names,
    missing,
    result,
    seen,
    registry: registry.serialiseRegistry(shuffled),
    unsupported: registry.serialiseUnsupported([...result.unsupported].reverse()),
  };
}

// The commands, each run in a scratch folder laid out as a checkout with an object cache.
const TSX = path.join(store, "node_modules/.bin/tsx");
const CLI = path.join(store, "scripts/omics/layers/cli.ts");
const FILES = ["data/omics/layers/registry.jsonl", "data/omics/layers/unsupported.jsonl"];
function layOut(tree) {
  const files = {};
  const shards = new Map();
  for (const name of tree.names) {
    const row = cases.rowFor(name, { source_ids: [name] });
    shards.set(row.sha256[0], [...(shards.get(row.sha256[0]) ?? []), row]);
  }
  for (const [shard, rows] of shards)
    files[`data/archive/objects/${shard}.jsonl`] = Buffer.from(cases.lines(...rows.sort((a, b) => (a.sha256 < b.sha256 ? -1 : 1))), "utf8").toString("hex");
  for (const name of tree.cached) {
    const { key, gz } = cases.cachedObject(name);
    files[`data/archive/cache/${key.slice(0, 2)}/${key}.gz`] = gz;
  }
  return files;
}
function run(folder, argv) {
  const env = { ...process.env };
  delete env.ARCHIVE_CACHE;
  const done = spawnSync(TSX, [CLI, ...argv], { cwd: folder, encoding: "utf8", env });
  const here = fs.realpathSync(folder);
  const said = (text) => text.split(here).join("<root>").split(folder).join("<root>");
  const written = Object.fromEntries(FILES.filter((file) => fs.existsSync(path.join(folder, file))).map((file) => [file, fs.readFileSync(path.join(folder, file), "utf8")]));
  return { argv, status: done.status, out: said(done.stdout), err: said(done.stderr), written };
}
function commandsOf(tree) {
  const files = layOut(tree);
  const steps = [];
  for (const sequence of [[["check"]], [["build"], ["check"], ["build"]], [["thresholds"]], [[]], [["show"]], [["nothing"]]]) {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), "layers-"));
    for (const [file, hex] of Object.entries(files)) {
      fs.mkdirSync(path.dirname(path.join(folder, file)), { recursive: true });
      fs.writeFileSync(path.join(folder, file), from(hex));
    }
    steps.push(sequence.map((argv) => run(folder, argv)));
    fs.rmSync(folder, { recursive: true, force: true });
  }
  return { name: tree.name, files, runs: steps };
}

const recorded = {
  recorded_from: {
    script: "scripts/fixtures/record_layers_fixture.mjs",
    cases: "scripts/fixtures/layers_cases.mjs",
    store_ref: storeRef,
    node: process.version,
    unicode: process.versions.unicode,
  },
  modules: Object.fromEntries(MODULES.map((file) => [file, sha256(fs.readFileSync(path.join(store, file)))])),
  classify: cases.classifyCases.map(([hex, hint]) => [hex, hint, attempt(() => classify.classify(from(hex), hint))]),
  encoding: cases.classifyCases.map(([hex]) => [hex, classify.encodingOf(from(hex))]),
  decode: cases.decodeCases.map(([hex, encoding]) => [hex, encoding, attempt(() => classify.decode(from(hex), encoding))]),
  expand: cases.expandCases.map(([hex, limits, words]) => [hex, limits, words, attempt(() => expansionOf(limits ? container.expandContainer(from(hex), limits) : container.expandContainer(from(hex))))]),
  zip_names: cases.zipNameCases.map(([hex, limit]) => [hex, limit, container.zipEntryNames(from(hex), limit)]),
  entities: cases.entityCases.map((text) => [text, markup.decodeEntities(text), officeXml.decodeXml(text)]),
  scan: cases.scanCases.map(([kind, text]) => scanned(kind, text)),
  tidy: cases.tidyCases.map((text) => [text, markup.tidyTraced(text).text, Array.from(markup.tidyTraced(text).out)]),
  tidy_text: cases.tidyTextCases.map((text) => [text, markup.tidy(text)]),
  derivations: { html: (await load("shared/archive/layers/html.ts")).htmlDerivation, normalisers: layers.normalisers },
  xml_scan: cases.xmlScanCases.map(xmlEvents),
  names: cases.nameCases.map((names) => [names, office.kindFromNames(names)]),
  kinds: Object.fromEntries(office.officeKinds.map((kind) => [kind, [office.officeMediaTypes[kind], office.normalisedKinds.has(kind), office.unsupportedReason(kind)]])),
  columns: {
    numbers: cases.columnCases.numbers.map((label) => [label, office.columnNumber(label)]),
    labels: cases.columnCases.labels.map((column) => [column, attempt(() => office.columnLabel(column))]),
    references: cases.columnCases.references.map((text) => [text, office.parseReference(text)]),
    escapes: cases.columnCases.escapes.map((text) => [text, office.escapeCell(text)]),
  },
  documents: cases.documentCases.map((text) => [text, office.serialiseDocument(text)]),
  sheets: cases.sheetCases.map(([text, strings]) => sheetOf(text, strings)),
  parts: cases.partCases.map(partsOf),
  office: Object.entries(cases.packages).map(([name, data]) => [name, attempt(() => officeOf(office.officeLayer(data)))]),
  office_other: [cases.objects.text, cases.objects.zipPlain, Buffer.concat([Buffer.from("junk"), cases.packages.macroWorkbook]), Buffer.concat([Buffer.from([0x1f, 0x8b]), cases.packages.macroWorkbook]), Buffer.concat([Buffer.from("XK"), cases.packages.macroWorkbook.subarray(2)]), Buffer.concat([Buffer.from([0x1f, 0x8b]), cases.packages.macroWorkbook.subarray(2)])].map((data) => [data.toString("hex"), attempt(() => officeOf(office.officeLayer(data)))]),
  grids: cases.gridCases.map(([normaliser, text]) => gridOf(normaliser, text)),
  grid_for: layers.normalisers.map((name) => [name, grid.gridNormaliserFor(name)]),
  candidates: cases.candidateCases.map((table) => [table, grid.candidateHeader({ rows: table.rows, columns: table.columns, cells: table.cells }, { thead: table.thead })]),
  prints: cases.printCases.map((text) => [text, grid.printsNumber(text), grid.printsValue(text)]),
  objects: cases.objectHex,
  trees: cases.treeCases.map(([name, mediaType, options]) => [name, mediaType, options, attempt(() => nodeOf(layers.layerOf(cases.objects[name], { mediaType, pdftotext: false, ...options })))]),
  walks: ["gz3", "zipPlain", "text"].map((name) => [name, layers.walk(layers.layerOf(cases.objects[name], { pdftotext: false })).map((step) => [step.node.sha256, step.parent, step.path])]),
  metrics: cases.metricTexts.map(metricsOf),
  metrics_of_every_character: everyCharacter(),
  thresholds: sanity.thresholds,
  sanity: cases.sanityCases.map(([metrics, normaliser]) => [metrics, normaliser, sanity.sanityOf(metrics, normaliser), sanity.ratiosOf(metrics), sanity.usable(sanity.sanityOf(metrics, normaliser).verdict)]),
  rows: cases.objectCases.map(([name, patch]) => objectOf(name, patch)),
  builds: cases.buildCases.map(buildOf),
  commands: cases.commandTrees.map(commandsOf),
};

const dirtyAfter = execFileSync("git", ["status", "--porcelain"], { cwd: store, encoding: "utf8" });
if (dirtyAfter !== dirtyBefore) throw new Error(`The recording changed the checkout:\n${dirtyAfter}`);

const body = Object.entries(recorded).map(([key, value]) => ` ${JSON.stringify(key)}: ${Array.isArray(value) ? `[\n${value.map((item) => `  ${JSON.stringify(item)}`).join(",\n")}\n ]` : JSON.stringify(value)}`);
fs.writeFileSync(path.join(repo, "tests/fixtures/layers.json"), `{\n${body.join(",\n")}\n}\n`);
console.log(`Recorded ${Object.keys(recorded).length} sections of layer derivation at ${storeRef} with Node ${process.version}.`);
