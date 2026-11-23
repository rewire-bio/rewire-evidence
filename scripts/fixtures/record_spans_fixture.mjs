// Record what the TypeScript span resolvers do, so the Python port is compared with the code
// it replaces and not with a reading of it.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_spans_fixture.mjs
//
// It runs inside the data repository, in the `fixtures` job in CI. The cases come from
// spans_cases.mjs, which needs none of the data repository's code.
//
//   tests/fixtures/spans.json
//       What the offset index, each resolver, the dispatch, the value check, the layer
//       readers, the coverage table and the figures file return or throw for each case, and
//       what `npm run spans --` and the reading commands of `npm run layers --` print and
//       write on small trees laid out in a scratch folder. It names the commit, and the
//       SHA-256 of each TypeScript file the port was made from.
//   scripts/expected/store-spans-commands.json
//       What `npm run layers -- coverage` and `surface` print for the pinned store. They read
//       the registry, the receipts and the source records and no object, so they are
//       recorded here and not where the objects are.
//
// The pinned store itself is held by the span gate, result for result. This holds what the
// stored results have no example of. No case here needs pdftotext: a resolver is given text
// and the name of the normaliser that would have written it.
import { execFileSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import zlib from "node:zlib";
import * as cases from "./spans_cases.mjs";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const MODULES = [
  "shared/omics/spans/figure.ts",
  "shared/omics/spans/grid.ts",
  "shared/omics/spans/index.ts",
  "shared/omics/spans/json.ts",
  "shared/omics/spans/offsets.ts",
  "shared/omics/spans/prose.ts",
  "shared/omics/spans/reanchor.ts",
  "shared/omics/spans/regions.ts",
  "shared/omics/spans/sheet.ts",
  "shared/omics/spans/table.ts",
  "shared/omics/spans/types.ts",
  "shared/omics/spans/width.ts",
  "shared/omics/reports/figures.ts",
  "shared/omics/layers/index.ts",
  "shared/omics/layers/coverage.ts",
  "shared/archive/layers/office/address.ts",
  "shared/archive/layers/grid/index.ts",
  "scripts/omics/spans/cli.ts",
  "scripts/omics/layers/cli.ts",
  "scripts/omics/extract/measure-store-spans.ts",
];

const load = async (file) => {
  const loaded = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(loaded.default ?? {}), ...loaded };
};
const spans = await load("shared/omics/spans/index.ts");
const table = await load("shared/omics/spans/table.ts");
const offsets = await load("shared/omics/spans/offsets.ts");
const reanchoring = await load("shared/omics/spans/reanchor.ts");
const json = await load("shared/omics/spans/json.ts");
const sheet = await load("shared/omics/spans/sheet.ts");
const address = await load("shared/archive/layers/office/address.ts");
const officeSheet = await load("shared/archive/layers/office/sheet.ts");
const layers = await load("shared/archive/layers/index.ts");
const grid = await load("shared/archive/layers/grid/index.ts");
const storeLayers = await load("shared/omics/layers/index.ts");
const figures = await load("shared/omics/reports/figures.ts");
const locator = await load("shared/omics/locator.ts");
const command = await load("scripts/omics/spans/cli.ts");

const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const storeRef = execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim();
const dirtyBefore = execFileSync("git", ["status", "--porcelain"], { cwd: store, encoding: "utf8" });
// A value JSON has no word for is written as a word of its own, so a recording never reads as
// a number where the TypeScript gave none.
const plain = (value) =>
  JSON.parse(
    JSON.stringify(value === undefined ? null : value, (key, held) =>
      typeof held === "number" && !Number.isFinite(held) ? String(held) : held instanceof Map ? Object.fromEntries(held) : held instanceof Set ? [...held] : held,
    ),
  );
const attempt = (run) => {
  try {
    return plain(run());
  } catch (error) {
    return { thrown: error.constructor.name };
  }
};
const number = (held) => (typeof held === "string" ? Number(held) : held);

// ---------------------------------------------------------------------------------------
// The layers the cases are resolved against: text written out in the cases, or the text
// layer and the grid the TypeScript derives from markup written out there.

const derived = {};
for (const [name, source] of Object.entries(cases.markup)) {
  const node = layers.layerOf(Buffer.from(source, "utf8"), { pdftotext: false });
  if (!node.layer) throw new Error(`${name} gives no text layer: ${node.unsupported}`);
  derived[name] = {
    text: node.layer.bytes.toString("utf8"),
    normaliser: node.layer.normaliser,
    layer_sha256: node.layer.output_sha256,
    grid: node.grid ? node.grid.bytes.toString("utf8") : null,
  };
}
const held = (name) => {
  if (name in derived) return derived[name];
  const layer = cases.layers[name];
  if (!layer) throw new Error(`no layer is named ${name}`);
  return { ...layer, layer_sha256: sha256(Buffer.from(layer.text, "utf8")), grid: null };
};
const indexes = new Map();
const index = (name) => {
  if (!indexes.has(name)) {
    const layer = held(name);
    indexes.set(name, spans.indexLayer(layer.text, layer.layer_sha256, layer.normaliser));
  }
  return indexes.get(name);
};
const gridOf = (name) => {
  const layer = held(name);
  return layer.grid === null ? null : grid.readGrid(layer.grid);
};
const node = (name, tokens) => {
  const root = json.readJson(cases.layers[name].text);
  const walked = json.walk(root, tokens);
  return walked.ok ? walked.node : null;
};
const parsed = (printed) => locator.parseLocator(printed);
const anchorOf = ([kind, value]) => ({ kind, value, raw: `${kind} ${value}` });
const cellRequest = (request) => ({ ...request, anchor: anchorOf(request.anchor) });

// What each case calls. The first word of a case names one of these, and the rest are its
// arguments as the cases write them.
const calls = {
  // offsets.ts
  index: (name) => {
    const { lines, u16, points, bytes } = index(name);
    return { lines: lines.length, u16, points, bytes, layer_points: spans.layerPoints(index(name)) };
  },
  countPoints: (text) => spans.countPoints(text),
  spanAt: (name, from, to) => spans.spanAt(index(name), number(from), number(to)),
  spanOfLines: (name, from, to) => spans.spanOfLines(index(name), number(from), number(to)),
  lineAt: (name, unit) => [offsets.lineAt(index(name), unit), offsets.pointAt(index(name), unit), offsets.byteAt(index(name), unit)],
  // table.ts, width.ts, figure.ts
  normaliseLabel: (text) => spans.normaliseLabel(text),
  columnForms: (text) => spans.columnForms(text),
  escape: (text) => table.escape(text),
  cellsOf: (name, line) => spans.cellsOf(index(name), line),
  captionLines: (name, word, value) => spans.captionLines(index(name), word, value),
  windows: (name, caption) => [spans.tableWindow(index(name), caption), spans.widerWindow(index(name), caption), spans.blockAbove(index(name), caption)],
  overlap: (a, b) => spans.overlap(a, b),
  tokensOf: (field) => spans.tokensOf(field),
  cellWidth: (input) => spans.cellWidth(input),
  resolveTableCell: (name, request) => spans.resolveTableCell(index(name), cellRequest(request)),
  resolveFigureCaption: (name, value) => spans.resolveFigureCaption(index(name), value),
  // regions.ts
  delimiterOf: (line) => spans.delimiterOf(line),
  fieldsOf: (name, line, delimiter) => spans.fieldsOf(index(name), line, delimiter),
  columnIndex: (letters) => spans.columnIndex(letters),
  resolveDelimitedCell: (name, request) => spans.resolveDelimitedCell(index(name), "line" in request ? { ...request, line: number(request.line) } : request),
  resolveKeyedRow: (name, request) => spans.resolveKeyedRow(index(name), request),
  resolveLineRange: (name, from, to) => (to === undefined ? spans.resolveLineRange(index(name), number(from)) : spans.resolveLineRange(index(name), number(from), number(to))),
  resolvePage: (name, page) => spans.resolvePage(index(name), number(page)),
  // prose.ts, reanchor.ts
  resolveQuote: (name, quote, whole) => spans.resolveQuote(index(name), quote, whole),
  resolveSectionParagraph: (name, section, paragraph) => spans.resolveSectionParagraph(index(name), section, number(paragraph)),
  reanchor: (name, span) => spans.reanchor({ ...span, layer_sha256: span.layer_sha256 ?? index(name).layer_sha256 }, index(name)),
  reanchorAfter: (from, to, start, end) => spans.reanchor(spans.spanAt(index(from), start, end), index(to)),
  codePointSlice: (name, start, end) => [reanchoring.codePointSlice(index(name), start, end), reanchoring.utf16Of(index(name), start), reanchoring.utf16Of(index(name), end)],
  // json.ts
  readJson: (text) => spans.readJson(text),
  pointerTokens: (path_) => [spans.pointerTokens(path_), spans.renderPointer(spans.pointerTokens(path_))],
  walk: (name, tokens) => {
    const walked = spans.walk(json.readJson(cases.layers[name].text), tokens);
    return walked.ok ? { ok: true, from: walked.node.from, to: walked.node.to, scalars: spans.scalarsOf(walked.node) } : walked;
  },
  resolveJsonPointer: (name, request) => spans.resolveJsonPointer(index(name), request),
  // office/address.ts, and sheet.ts
  unescapeCell: (text) => officeSheet.unescapeCell(text),
  sheetBlocks: (text) => address.sheetBlocks(text),
  findBlock: (text, wanted) => address.findBlock(address.sheetBlocks(text), wanted),
  cellRange: (text, wanted, row, column) => {
    const range = address.cellRange(text, wanted, number(row), number(column));
    return range === null ? null : { ...range, fragment: address.rfc7111Fragment(range) };
  },
  cellRangeByReference: (text, wanted, reference) => address.cellRangeByReference(text, wanted, reference),
  reference: (row, column) => address.reference(row, column),
  fieldsOfLine: (name, line) => spans.fieldsOfLine(index(name), line),
  blocksOf: (name) => spans.blocksOf(index(name)),
  sheetRequest: (printed) => spans.sheetRequest(parsed(printed)),
  sheetRequestParsed: (fields) => spans.sheetRequest({ printed: "", anchors: [], addresses: [], selectors: [], qualifiers: [], residue: [], verdict: "partial", ...fields }),
  resolveSheetCell: (name, request) => spans.resolveSheetCell(index(name), request),
  // grid/index.ts and grid.ts
  readGrid: (text) => grid.readGrid(text),
  gridOf: (name) => gridOf(name),
  gridCell: (name, at, row, column) => {
    const read = gridOf(name);
    const found = grid.cellRange(read, at, row, column);
    return [grid.tableAt(read, at)?.index ?? null, found && { start: found.start, end: found.end, text: found.text }, grid.cellAt(read.tables[0], row, column)];
  },
  namesTable: (text, value) => spans.namesTable(text, value),
  unitOfPoint: (name, point) => spans.unitOfPoint(index(name), point),
  gridRouteFor: (name, value) => spans.gridRouteFor(index(name), gridOf(name), value),
  gridRequest: (printed, base) => spans.gridRequest(parsed(printed), cellRequest(base), locator.selectorRole),
  gridRequestParsed: (fields, base) => spans.gridRequest({ selectors: [], qualifiers: [], ...fields }, cellRequest(base), locator.selectorRole),
  resolveCustomGrid: (name, { start, end, text }, request) => {
    // One table of one row, whose one body cell is where the case says it is.
    const cells = [
      { row: 0, column: 0, row_span: 1, column_span: 1, header: true, start: null, end: null, text: "" },
      { row: 0, column: 1, row_span: 1, column_span: 1, header: true, start: null, end: null, text: "c" },
      { row: 1, column: 0, row_span: 1, column_span: 1, header: false, start: null, end: null, text: "r" },
      { row: 1, column: 1, row_span: 1, column_span: 1, header: false, start, end, text },
    ];
    const layer = held(name);
    const made = { normaliser: "html-table-grid@1", text_layer_sha256: layer.layer_sha256, text_normaliser: layer.normaliser, tables: [{ index: 0, rows: 2, columns: 2, header_rows: 1, headers: [[""], ["c"]], label: { start: 0, end: 7, text: "Table 1" }, caption: null, cells, problems: [] }] };
    return spans.resolveGridCell(index(name), made, cellRequest(request));
  },
  resolveGridCell: (name, request, over) => spans.resolveGridCell(index(over ?? name), gridOf(name), cellRequest(request)),
  // index.ts
  resolveLocator: (name, printed, options, grids) => {
    const read = grids === undefined ? gridOf(name) : grids === null ? null : gridOf(grids);
    return spans.resolveLocator(parsed(printed), index(name), read, options ?? undefined);
  },
  resolveParsed: (name, fields, options) =>
    spans.resolveLocator({ printed: "", anchors: [], addresses: [], selectors: [], qualifiers: [], residue: [], verdict: "partial", ...fields }, index(name), gridOf(name), options ?? undefined),
  // spans/cli.ts
  valuePresence: (exact, printed) => command.valuePresence(exact, printed),
  // layers/index.ts and coverage.ts
  sliceSpan: (text, start, end) => storeLayers.sliceSpan(text, start, end),
  characterCount: (text) => storeLayers.characterCount(text),
  coverageOf: (sources, receipts, registry, unsupported) => storeLayers.coverageOf(sources, new Map(receipts.map((receipt) => [receipt.source_id, receipt])), registry, unsupported),
  // reports/figures.ts
  marks: (text) => figures.marks(text),
  printsFigure: (printed, value) => figures.printsFigure(printed, value),
  retag: (text, held_) => figures.retag(text, held_),
  recordsFileFor: (file) => figures.recordsFileFor(file),
  inputDigest: (inputs) => figures.inputDigest(inputs),
  changedInputs: (recorded, now) => figures.changedInputs(recorded, now),
};

const called = cases.calls.map(([name, ...given]) => {
  if (!(name in calls)) throw new Error(`no call is named ${name}`);
  return [name, given, attempt(() => calls[name](...given))];
});

// ---------------------------------------------------------------------------------------
// The figures file on small trees.

const CACHE = "data/archive/cache/";
/** A tree in a scratch folder: its files as they are, and each object of its cache compressed. */
function laidOut(files, objects = {}) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "spans-"));
  for (const [file, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(folder, file)), { recursive: true });
    fs.writeFileSync(path.join(folder, file), Buffer.from(text, "hex"));
  }
  for (const [file, data] of Object.entries(objects)) {
    fs.mkdirSync(path.dirname(path.join(folder, file)), { recursive: true });
    fs.writeFileSync(path.join(folder, file), zlib.gzipSync(Buffer.from(data, "hex"), { level: 9 }));
  }
  return folder;
}
/** Every file under a folder that is not one of `but` as it was laid out, in the order of their names.
 * The object cache is left out: no command writes to it, and its bytes are this machine's gzip. */
function everyFile(folder, but = {}) {
  const found = {};
  const walk = (dir) => {
    const entries = fs.readdirSync(path.join(folder, dir), { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1));
    for (const entry of entries) {
      const file = path.posix.join(dir, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (file.startsWith(CACHE)) continue;
      else if (!(file in but) || Buffer.from(but[file], "hex").compare(fs.readFileSync(path.join(folder, file))) !== 0) found[file] = fs.readFileSync(path.join(folder, file)).toString("hex");
    }
  };
  walk("");
  return found;
}
const hexed = (files) => Object.fromEntries(Object.entries(files).map(([file, text]) => [file, Buffer.from(text, "utf8").toString("hex")]));

const figureTrees = cases.figureTrees.map(({ name, files, inputs, steps }) => {
  const laid = hexed(files);
  const folder = laidOut(laid);
  const done = steps.map(([step, ...given]) => {
    if (step === "inputFiles") return [step, given, attempt(() => (inputs ? figures.inputFiles(folder, inputs) : figures.inputFiles(folder)))];
    if (step === "readFigures") return [step, given, attempt(() => figures.readFigures(folder, ...given))];
    if (step === "writeFigures") {
      const outcome = attempt(() => figures.writeFigures(folder, ...given));
      return [step, given, outcome, everyFile(folder, laid)];
    }
    if (step === "snapshot") {
      // The figures of a measurement of the tree as it stands: its inputs are the tree's own.
      figures.writeFigures(folder, { ...given[0], inputs: inputs ? figures.inputFiles(folder, inputs) : figures.inputFiles(folder) });
      return [step, given, null, everyFile(folder, laid)];
    }
    if (step === "edit") {
      fs.mkdirSync(path.dirname(path.join(folder, given[0])), { recursive: true });
      fs.writeFileSync(path.join(folder, given[0]), given[1]);
      return [step, given, null];
    }
    if (step === "remove") {
      fs.rmSync(path.join(folder, given[0]));
      return [step, given, null];
    }
    if (step === "drift") {
      // What tests/omics-report-drift.test.ts asks: the inputs the figures name against the tree.
      return [step, given, attempt(() => figures.changedInputs(figures.readFigures(folder).inputs, inputs ? figures.inputFiles(folder, inputs) : figures.inputFiles(folder), folder))];
    }
    throw new Error(`no step is named ${step}`);
  });
  fs.rmSync(folder, { recursive: true, force: true });
  return { name, files: laid, inputs: inputs ?? null, steps: done };
});

// ---------------------------------------------------------------------------------------
// The commands, each run in a scratch folder laid out as a checkout with an object cache.

const TSX = path.join(store, "node_modules/.bin/tsx");
const SPANS = path.join(store, "scripts/omics/spans/cli.ts");
const LAYERS = path.join(store, "scripts/omics/layers/cli.ts");
function run(folder, [program, ...argv], laid) {
  // Two steps are no command: a file taken out of the tree, and one rewritten in a place.
  if (program === "rm") {
    fs.rmSync(path.join(folder, argv[0]));
    return { argv: [program, ...argv] };
  }
  if (program === "sub") {
    const [file, pattern, replacement] = argv;
    const before = fs.readFileSync(path.join(folder, file), "utf8");
    const after = before.replace(new RegExp(pattern), replacement);
    if (after === before) throw new Error(`${pattern} changes nothing in ${file}`);
    fs.writeFileSync(path.join(folder, file), after);
    return { argv: [program, ...argv] };
  }
  const env = { ...process.env, SPAN_ROOT: folder };
  delete env.ARCHIVE_CACHE;
  delete env.SPAN_DETAIL;
  const detail = argv.includes("--detail");
  const given = argv.filter((word) => word !== "--detail").map((word) => (word.startsWith("<root>") ? path.join(folder, word.slice(7)) : word));
  if (detail) env.SPAN_DETAIL = path.join(folder, "detail.tsv");
  const before = new Date().toISOString().slice(0, 10);
  // The layer commands read the working directory, and the span commands read SPAN_ROOT.
  const done = spawnSync(TSX, [program === "spans" ? SPANS : LAYERS, ...given], { cwd: folder, encoding: "utf8", env });
  const dates = new Set([before, new Date().toISOString().slice(0, 10)]);
  const here = fs.realpathSync(folder);
  const said = (text) =>
    [...dates]
      .reduce((held_, date) => held_.split(date).join("<date>"), text.split(here).join("<root>").split(folder).join("<root>"))
      .replace(/^\d+ s$/m, "<seconds> s");
  const written = Object.fromEntries(
    Object.entries(everyFile(folder, laid)).map(([file, hex]) => [said(file), Buffer.from(said(Buffer.from(hex, "hex").toString("utf8")), "utf8").toString("hex")]),
  );
  // A failure prints a stack trace with this machine's paths in it. Only its first line is kept.
  const err = done.status === 0 || program === "spans" ? said(done.stderr) : said(done.stderr).split("\n").slice(0, 1).join("\n");
  return { argv: [program, ...argv], status: done.status, out: said(done.stdout), err: done.status !== 0 && program === "spans" ? null : err, written };
}
const commandTrees = cases.commandTrees.map(({ name, files, sequences }) => {
  const laid = hexed(files.text ?? {});
  const objects = files.objects ?? {};
  const runs = sequences.map((sequence) => {
    const folder = laidOut(laid, objects);
    let now = { ...laid };
    const steps = sequence.map((argv) => {
      const done = run(folder, argv, now);
      now = { ...now, ...everyFile(folder) };
      return done;
    });
    fs.rmSync(folder, { recursive: true, force: true });
    return steps;
  });
  return { name, files: laid, objects, runs };
});

// ---------------------------------------------------------------------------------------
// The store recorder itself, run over one of the trees, so that the gate that reads its
// recordings is held to them on a store small enough to commit.

function recordings() {
  const { name, date, extra, restricted } = cases.recordedTree;
  const tree = cases.commandTrees.find((held_) => held_.name === name);
  const objects = { ...(tree.files.objects ?? {}), ...extra };
  const folder = laidOut(hexed(tree.files.text ?? {}), objects);
  const env = { ...process.env };
  delete env.ARCHIVE_CACHE;
  delete env.SPAN_DETAIL;
  spawnSync(TSX, [LAYERS, "build"], { cwd: folder, encoding: "utf8", env });
  const files = everyFile(folder);
  const recorder = path.join(repo, "scripts/fixtures/record_spans_store.mjs");
  const views = {};
  for (const view of ["full", "public"]) {
    if (view === "public") for (const file of restricted) fs.rmSync(path.join(folder, file));
    const out = fs.mkdtempSync(path.join(os.tmpdir(), "recording-"));
    const done = spawnSync(TSX, [recorder, out, date, store], { cwd: folder, encoding: "utf8", env });
    if (done.status !== 0) throw new Error(`the store recorder failed on the ${view} view: ${done.stderr}`);
    views[view] = everyFile(out);
    fs.rmSync(out, { recursive: true, force: true });
  }
  fs.rmSync(folder, { recursive: true, force: true });
  return { files, objects, date, restricted, ...views };
}

// ---------------------------------------------------------------------------------------
// The pinned store, for the two commands that read no object.

function atThePin(argv) {
  const env = { ...process.env };
  delete env.ARCHIVE_CACHE;
  const done = spawnSync(TSX, [LAYERS, ...argv], { cwd: store, encoding: "utf8", env });
  return { argv: ["layers", ...argv], status: done.status, out: done.stdout, err: done.stderr };
}
const members = (held_) => `{\n${Object.entries(held_).map(([key, value]) => ` ${JSON.stringify(key)}: ${value}`).join(",\n")}\n}\n`;
const recordedFrom = {
  script: "scripts/fixtures/record_spans_fixture.mjs",
  cases: "scripts/fixtures/spans_cases.mjs",
  store_ref: storeRef,
  node: process.version,
  unicode: process.versions.unicode,
};
fs.writeFileSync(
  path.join(repo, "scripts/expected/store-spans-commands.json"),
  members({
    recorded_from: JSON.stringify({ ...recordedFrom, cases: undefined }),
    commands: `[\n${[["coverage"], ["surface"]].map((argv) => `  ${JSON.stringify(atThePin(argv))}`).join(",\n")}\n ]`,
  }),
);

const recorded = {
  recorded_from: recordedFrom,
  modules: Object.fromEntries(MODULES.map((file) => [file, sha256(fs.readFileSync(path.join(store, file)))])),
  constants: {
    span_kinds: spans.spanKinds,
    refusal_reasons: spans.refusalReasons,
    grid_routes: spans.gridRoutes,
    context_points: spans.CONTEXT_POINTS,
    file_normalisers: [...spans.FILE_NORMALISERS],
    aligned_normalisers: [...spans.ALIGNED_NORMALISERS],
    sheet_normalisers: [...spans.SHEET_NORMALISERS],
    span_figures: figures.SPAN_FIGURES,
    span_inputs: figures.SPAN_INPUTS,
    block_marker: officeSheet.blockMarker,
    negative_powers: Array.from({ length: 101 }, (_, places) => String(10 ** -places)),
    precision: cases.precisionCases.map(([text, digits]) => [text, digits, Number(text).toPrecision(digits)]),
  },
  layers: Object.fromEntries(Object.keys({ ...cases.layers, ...cases.markup }).map((name) => [name, held(name)])),
  markup: cases.markup,
  calls: called,
  figure_trees: figureTrees,
  commands: commandTrees,
  recordings: recordings(),
};

const dirtyAfter = execFileSync("git", ["status", "--porcelain"], { cwd: store, encoding: "utf8" });
if (dirtyAfter !== dirtyBefore) throw new Error(`The recording changed the checkout:\n${dirtyAfter}`);

const body = Object.entries(recorded).map(([key, value]) => ` ${JSON.stringify(key)}: ${key === "recordings" ? `{\n${Object.entries(value).map(([name, held_]) => `  ${JSON.stringify(name)}: ${JSON.stringify(held_)}`).join(",\n")}\n }` : Array.isArray(value) ? `[\n${value.map((item) => `  ${JSON.stringify(item)}`).join(",\n")}\n ]` : JSON.stringify(value)}`);
fs.writeFileSync(path.join(repo, "tests/fixtures/spans.json"), `{\n${body.join(",\n")}\n}\n`);
const outcomes = {};
for (const [, , result] of called) {
  const key = result && typeof result === "object" && "ok" in result ? (result.ok ? result.kind ?? "ok" : result.reason ?? "refused") : null;
  if (key) outcomes[key] = (outcomes[key] ?? 0) + 1;
}
console.log(`Recorded ${called.length} calls, ${figureTrees.length} figure trees and ${commandTrees.length} command trees at ${storeRef} with Node ${process.version}.`);
console.log(Object.entries(outcomes).sort().map(([key, count]) => `${key} ${count}`).join(", "));
