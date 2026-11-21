// Record what the TypeScript table reader reads, so the Python port is compared with the code
// it replaces and not with a reading of it.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_tables_fixture.mjs
//
// It runs inside the data repository, in the `fixtures` job in CI, and nowhere else. The
// cases come from tables_cases.mjs, which needs none of the data repository's code.
//
//   scripts/expected/store-tables.json
//   scripts/expected/store-tables.jsonl.gz
//       scripts/omics/extract/tables.ts run over every HTML and XML file the data repository
//       commits under data/ and docs/: the files whose names end .html, .html.gz or .xml.gz.
//       One line for each file, with the SHA-256 of its bytes, of its visible text as
//       text() gives it, and how many tables tables() finds, or what either threw. Then one
//       line for each table: its caption, how many rows and cells it has, and the SHA-256
//       of its rows, of its rows with joinSmallCaps run on every cell, and of what
//       headedBlocks makes of it. The JSON file states the commit, the counts and the
//       SHA-256 of the lines.
//   tests/fixtures/tables.json
//       Every pattern written in tables.ts, as the TypeScript compiler reads the file, and
//       what the reader does with the cases in tables_cases.mjs.
//
// gzip does not write the same bytes on every zlib, so the compressed file is rewritten
// only when the lines inside it differ.
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import zlib from "node:zlib";
import { blocks, decodes, headings, layouts, matchings, sectionings, smallCaps, sources, texts } from "./tables_cases.mjs";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const MODULE = "scripts/omics/extract/tables.ts";
/** The folders the data repository commits artifacts and documents under. */
const FOLDERS = ["data", "docs"];
const ARTIFACT = /\.(?:html|html\.gz|xml\.gz)$/;

const loaded = await import(pathToFileURL(path.join(store, MODULE)).href);
const reader = { ...(loaded.default ?? {}), ...loaded };
const { decode, text, tables, tableMatching, sectioned, headedBlocks, arrowDirection, layoutRows, joinSmallCaps } = reader;

const storeRef = execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim();
const recordedFrom = {
  script: "scripts/fixtures/record_tables_fixture.mjs",
  cases: "scripts/fixtures/tables_cases.mjs",
  store_ref: storeRef,
  node: process.version,
  unicode: process.versions.unicode,
};
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
/** The SHA-256 of a value as JSON.stringify writes it. Half a character is written as an escape. */
const digest = (value) => sha256(Buffer.from(JSON.stringify(value), "utf8"));
const rows = (items) => `[\n${items.map((item) => `  ${JSON.stringify(item)}`).join(",\n")}\n ]`;
const members = (held) => `{\n${Object.entries(held).map(([key, value]) => ` ${JSON.stringify(key)}: ${value}`).join(",\n")}\n}\n`;

/** What a call returned, or the name and the message of what it threw. */
function outcome(run) {
  try {
    return { returned: run() };
  } catch (error) {
    return { error: { name: error?.constructor?.name ?? typeof error, message: String(error?.message ?? error) } };
  }
}

// ---------------------------------------------------------------------------------------
// The patterns, as the compiler reads them out of the file.

const ts = createRequire(path.join(store, "package.json"))("typescript");
const sourceText = fs.readFileSync(path.join(store, MODULE), "utf8");
const parsed = ts.createSourceFile(MODULE, sourceText, ts.ScriptTarget.Latest, true);
const patterns = [];
(function walk(node, within) {
  const named = (ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node)) && node.name && ts.isIdentifier(node.name) ? node.name.text : within;
  if (node.kind === ts.SyntaxKind.RegularExpressionLiteral) {
    const written = node.getText(parsed);
    const close = written.lastIndexOf("/");
    patterns.push([within, written.slice(close + 1), written.slice(1, close)]);
  }
  ts.forEachChild(node, (child) => walk(child, named));
})(parsed, "");
if (!patterns.length) throw new Error(`no pattern was found in ${MODULE}`);

// ---------------------------------------------------------------------------------------
// The committed artifacts.

function artifactsUnder(folder) {
  const found = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(path.join(store, dir), { withFileTypes: true })) {
      const file = path.posix.join(dir, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (entry.isFile() && ARTIFACT.test(entry.name)) found.push(file);
    }
  };
  if (fs.existsSync(path.join(store, folder))) walk(folder);
  return found;
}
const artifacts = FOLDERS.flatMap(artifactsUnder).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
// Every such file git holds is one of them, so none is left out for where it is.
const tracked = execFileSync("git", ["ls-files", "-z"], { cwd: store, encoding: "utf8", maxBuffer: 1 << 28 }).split("\0").filter((file) => ARTIFACT.test(file));
const outside = tracked.filter((file) => !artifacts.includes(file));
if (outside.length) throw new Error(`committed artifacts outside ${FOLDERS.join(" and ")}: ${outside.slice(0, 5).join(", ")}`);

const lines = [];
const counts = { artifacts: 0, artifacts_with_tables: 0, artifacts_refused: 0, tables: 0, rows: 0, cells: 0, blocks: 0 };
for (const file of artifacts) {
  const bytes = fs.readFileSync(path.join(store, file));
  const source = (file.endsWith(".gz") ? zlib.gunzipSync(bytes) : bytes).toString("utf8");
  const visible = outcome(() => text(source));
  const found = outcome(() => tables(source));
  counts.artifacts += 1;
  if (found.error) counts.artifacts_refused += 1;
  else if (found.returned.length) counts.artifacts_with_tables += 1;
  lines.push(JSON.stringify(["artifact", file, sha256(bytes), source.length, visible.error ? { error: visible.error.message } : sha256(Buffer.from(visible.returned, "utf8")), found.error ? { error: found.error.message } : found.returned.length]));
  for (const [index, table] of (found.returned ?? []).entries()) {
    const cells = table.rows.reduce((sum, row) => sum + row.length, 0);
    const made = headedBlocks(table);
    counts.tables += 1;
    counts.rows += table.rows.length;
    counts.cells += cells;
    counts.blocks += made.length;
    lines.push(JSON.stringify(["table", file, index, table.caption, table.rows.length, cells, digest(table.rows), digest(table.rows.map((row) => row.map(joinSmallCaps))), made.length, digest(made)]));
  }
}
const content = lines.map((line) => `${line}\n`).join("");
const data = Buffer.from(content, "utf8");
const dumpName = "store-tables.jsonl.gz";
const dumpFile = path.join(repo, "scripts/expected", dumpName);
const held = fs.existsSync(dumpFile) ? zlib.gunzipSync(fs.readFileSync(dumpFile)) : null;
if (held === null || !held.equals(data)) fs.writeFileSync(dumpFile, zlib.gzipSync(data, { level: 9 }));
fs.writeFileSync(
  path.join(repo, "scripts/expected/store-tables.json"),
  members({
    recorded_from: JSON.stringify(recordedFrom),
    module: JSON.stringify({ file: MODULE, sha256: sha256(Buffer.from(sourceText, "utf8")) }),
    folders: JSON.stringify(FOLDERS),
    counts: JSON.stringify(counts),
    dump: JSON.stringify({ file: dumpName, lines: lines.length, sha256: sha256(data) }),
  }),
);

// ---------------------------------------------------------------------------------------
// The cases.

const pattern = ([source, flags]) => new RegExp(source, flags);
const tableOf = (found) => (found.error ? found : { returned: found.returned.map(({ caption, rows: held }) => ({ caption, rows: held })) });

fs.writeFileSync(
  path.join(repo, "tests/fixtures/tables.json"),
  members({
    recorded_from: JSON.stringify(recordedFrom),
    patterns: rows(patterns),
    decodes: rows(decodes.map((given) => ({ given, ...outcome(() => decode(given)) }))),
    texts: rows(texts.map((given) => ({ given, ...outcome(() => text(given)) }))),
    sources: rows(sources.map(({ name, source }) => ({ name, source, ...tableOf(outcome(() => tables(source))) }))),
    matchings: rows(matchings.map(({ name, source, pattern: held }) => ({ name, source, pattern: held, written: String(pattern(held)), ...outcome(() => tableMatching(source, pattern(held))) }))),
    sectionings: rows(sectionings.map(({ name, rows: held, width }) => ({ name, rows: held, width, ...outcome(() => sectioned(held, width)) }))),
    blocks: rows(blocks.map(({ name, rows: held }) => ({ name, rows: held, ...outcome(() => headedBlocks({ caption: "", rows: held })) }))),
    headings: rows(headings.map((given) => [given, arrowDirection(given)])),
    layouts: rows(layouts.map(({ name, source, from, to }) => ({ name, source, from, to, ...outcome(() => layoutRows(source, pattern(from), pattern(to))) }))),
    small_caps: rows(smallCaps.map((given) => [given, joinSmallCaps(given)])),
  }),
);
console.log(
  `Recorded ${counts.tables} tables with ${counts.cells} cells in ${counts.artifacts} committed artifacts at ${storeRef}, ${patterns.length} patterns, `
  + `${decodes.length} references, ${texts.length} fragments, ${sources.length} sources, ${matchings.length} captions matched, ${sectionings.length} sectioned tables, `
  + `${blocks.length} headed tables, ${layouts.length} layouts and ${smallCaps.length} small capitals.`,
);
