// Record what the TypeScript locator grammar reads, so the Python port is compared with the
// code it replaces and not with a reading of it.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_locator_fixture.mjs
//
// It runs inside the data repository, in the `fixtures` job in CI, and nowhere else. The
// inputs come from locator_cases.mjs, which needs none of the data repository's code.
//
//   scripts/expected/store-locators.jsonl.gz
//       Every locator of every result in the pinned store, as shared/omics/locator.ts reads
//       it: one line for each, holding the record's ID, the class, the coverage, the whole
//       parse and the text it renders back to. The store holds only totals by class, which
//       cannot show that two parsers agree on structure, so this is the oracle for the gate.
//       gzip does not write the same bytes on every zlib, so the file is rewritten only when
//       the lines inside it differ.
//   scripts/expected/store-locators.json
//       The commit the dump was read at, how many lines it has and the SHA-256 of them
//       uncompressed; the coverage report `npm run locator -- coverage` prints and writes
//       for that store, with the committed report it is byte for byte; and how the pattern
//       of grammar version 1 splits the rows of the value-check files.
//   tests/fixtures/locator.json
//       The patterns as the TypeScript holds them, and what it does with the locators, keys,
//       words, typed fields and command lines in locator_cases.mjs.
import { execFileSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import zlib from "node:zlib";
import { commands, generated, handwritten, keys, rendering, tallied, verdictCases, words } from "./locator_cases.mjs";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const MODULE = "shared/omics/locator.ts";
const COMMAND = "scripts/omics/locator-cli.ts";
const tsx = path.join(store, "node_modules/.bin/tsx");

async function load(file) {
  const module = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(module.default ?? {}), ...module };
}

const locator = await load(MODULE);
const {
  parseLocator, classifyLocator, coverageOf, renderLocator, verdictOf, selectorRole, carriesNoAddress,
  splitSegments, parsesUnderLegacyRegex, legacyLocatorPattern,
} = locator;
const { tallyLocators } = await load(COMMAND);

const storeRef = execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim();
const recordedFrom = {
  script: "scripts/fixtures/record_locator_fixture.mjs",
  store_ref: storeRef,
  node: process.version.split(".")[0],
  unicode: process.versions.unicode,
};
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const lines = (items) => `[\n${items.map((item) => `  ${JSON.stringify(item)}`).join(",\n")}\n ]`;
const members = (held) => `{\n${Object.entries(held).map(([key, value]) => ` ${JSON.stringify(key)}: ${value}`).join(",\n")}\n}\n`;

// The patterns are not exported. A copy of the module that exports them is written beside
// it, read and removed, so that what is recorded is what the module compiles.
async function heldPatterns() {
  const exposed = "shared/omics/locator-exposed-for-recording.ts";
  const text = fs.readFileSync(path.join(store, MODULE), "utf8");
  const pair = (pattern) => `[${pattern}.flags, ${pattern}.source]`;
  fs.writeFileSync(
    path.join(store, exposed),
    `${text}\nexport const heldForRecording = {
      tokens: TOKENS.map((token) => [token.name, token.re.flags, token.re.source]),
      ordinal_key: ${pair("ORDINAL_KEY")}, noise: ${pair("NOISE")}, noise_words: ${pair("NOISE_WORDS")},
      legacy: ${pair("legacyLocatorPattern")}, table_head: ${pair("TABLE_HEAD")},
      row_keys: [...ROW_KEYS], column_keys: [...COL_KEYS],
      direct: [...DIRECT], line_addressed: [...LINE_ADDRESSED], prose: [...PROSE],
      anchor_kind: ANCHOR_KIND, address_kind: ADDRESS_KIND, qualifier_kind: QUALIFIER_KIND, selector_form: SELECTOR_FORM,
    };\n`,
  );
  try {
    return (await load(exposed)).heldForRecording;
  } finally {
    fs.rmSync(path.join(store, exposed));
  }
}

/** One locator as the TypeScript reads it. */
function reading(printed) {
  const parsed = parseLocator(printed);
  return { class: classifyLocator(printed), coverage: coverageOf(parsed.verdict), parsed, rendered: renderLocator(parsed) };
}

// ---------------------------------------------------------------------------------------
// The pinned store: every locator, the report, and the value-check files.

function storedResults() {
  const dir = path.join(store, "data/evidence/results");
  const found = [];
  for (const file of fs.readdirSync(dir).filter((name) => name.endsWith(".jsonl")).sort()) {
    for (const line of fs.readFileSync(path.join(dir, file), "utf8").split("\n")) {
      if (!line.trim()) continue;
      const record = JSON.parse(line);
      if (record.kind !== "result") continue;
      const printed = record.attributes?.source_locator;
      if (typeof printed === "string") found.push({ id: record.id, printed });
    }
  }
  return found;
}

const results = storedResults();
const dump = Buffer.from(results.map(({ id, printed }) => `${JSON.stringify({ id, ...reading(printed) })}\n`).join(""), "utf8");
const dumpFile = path.join(repo, "scripts/expected/store-locators.jsonl.gz");
const held = fs.existsSync(dumpFile) ? zlib.gunzipSync(fs.readFileSync(dumpFile)) : null;
if (held === null || !held.equals(dump)) fs.writeFileSync(dumpFile, zlib.gzipSync(dump, { level: 9 }));

/** Run the command as `npm run locator --` runs it, in a folder. */
function run(folder, args) {
  const done = spawnSync(tsx, [path.join(store, COMMAND), ...args], { cwd: folder, encoding: "utf8" });
  return { status: done.status, stdout: done.stdout, stderr: done.stderr };
}

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "locator-fixture-"));
const reportFile = path.join(scratch, "store-report.tsv");
const measured = run(store, ["coverage", "--out", reportFile]);
if (measured.status !== 0) throw new Error(`coverage failed on the store: ${measured.stderr}`);
const tsv = fs.readFileSync(reportFile, "utf8");
const reportDir = "maintenance/reports";
const committed = fs.readdirSync(path.join(store, reportDir))
  .filter((name) => /^locator-coverage-.*\.tsv$/.test(name))
  .sort()
  .map((name) => `${reportDir}/${name}`)
  .filter((file) => fs.readFileSync(path.join(store, file), "utf8") === tsv);
// The last line names the file the report went to, which is this run's and nobody else's.
const printed = measured.stdout.split("\n").slice(0, -2).join("\n");

function valueCheck() {
  const batches = path.join(store, "data/omics");
  const files = fs.readdirSync(batches).map((batch) => `data/omics/${batch}/value-check.tsv`)
    .filter((file) => fs.existsSync(path.join(store, file)))
    .sort();
  const counted = { files: files.length, rows: 0, parsed: 0, not_parsed: 0, disagreed: [] };
  for (const file of files) {
    const rows = fs.readFileSync(path.join(store, file), "utf8").split("\n").filter((line) => line.length > 0);
    if (rows[0] !== "id\tstatus\tprinted_value\tsource_locator") throw new Error(`${file} has another header`);
    for (const row of rows.slice(1)) {
      const fields = row.split("\t");
      const [id, status] = fields;
      const matches = parsesUnderLegacyRegex(fields.slice(3).join("\t"));
      counted.rows += 1;
      if (status === "locator_not_parsed") counted.not_parsed += 1; else counted.parsed += 1;
      if (matches !== (status !== "locator_not_parsed")) counted.disagreed.push(`${file} ${id}`);
    }
  }
  return counted;
}

fs.writeFileSync(
  path.join(repo, "scripts/expected/store-locators.json"),
  members({
    recorded_from: JSON.stringify(recordedFrom),
    dump: JSON.stringify("store-locators.jsonl.gz"),
    locators: String(results.length),
    sha256: JSON.stringify(sha256(dump)),
    report: JSON.stringify({ committed, printed, tsv }),
    value_check: JSON.stringify(valueCheck()),
  }),
);

// ---------------------------------------------------------------------------------------
// The cases.

const sample = fs.readFileSync(path.join(store, "tests/fixtures/locator-sample.tsv"), "utf8")
  .split("\n")
  .filter((line) => line.length > 0 && !line.startsWith("#"))
  .slice(1)
  .map((line) => line.split("\t")[0]);
const locators = [...new Set([...handwritten, ...sample, ...generated(2400, 20261121)])].map((printed) => ({
  printed,
  legacy: parsesUnderLegacyRegex(printed),
  segments: splitSegments(printed),
  ...reading(printed),
}));

/** The command in a scratch folder holding the case's files, and what it left there. */
function command({ name, files, args }) {
  const folder = fs.mkdtempSync(path.join(scratch, "run-"));
  for (const [file, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(folder, file)), { recursive: true });
    fs.writeFileSync(path.join(folder, file), text);
  }
  const before = new Date().toISOString().slice(0, 10);
  const done = run(folder, args);
  const dates = new Set([before, new Date().toISOString().slice(0, 10)]);
  // The report is written under the date of the run. The date is recorded as <date>.
  const undated = (text) => [...dates].reduce((said, date) => said.replaceAll(`locator-coverage-${date}.tsv`, "locator-coverage-<date>.tsv"), text);
  const written = {};
  const walk = (dir) => {
    for (const entry of fs.readdirSync(path.join(folder, dir), { withFileTypes: true })) {
      const file = path.posix.join(dir, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (!(file in files)) written[undated(file)] = fs.readFileSync(path.join(folder, file), "utf8");
    }
  };
  walk("");
  // A failure prints a stack trace with this machine's paths in it. Only a usage error is kept.
  const stderr = done.status === 2 ? done.stderr : null;
  return { name, files, args, status: done.status, stdout: undated(done.stdout), stderr, written };
}

const patterns = await heldPatterns();
fs.writeFileSync(
  path.join(repo, "tests/fixtures/locator.json"),
  members({
    recorded_from: JSON.stringify(recordedFrom),
    patterns: JSON.stringify(patterns),
    constants: JSON.stringify({
      anchor_kinds: locator.anchorKinds, address_kinds: locator.addressKinds, qualifier_kinds: locator.qualifierKinds,
      selector_forms: locator.selectorForms, verdicts: locator.verdicts, coverages: locator.coverages,
      locator_classes: locator.locatorClasses,
      coverage_of: Object.fromEntries(locator.verdicts.map((verdict) => [verdict, coverageOf(verdict)])),
      legacy: [legacyLocatorPattern.flags, legacyLocatorPattern.source],
    }),
    locators: lines(locators),
    roles: lines(keys.map((key) => [key, selectorRole(key)])),
    words: lines(words.map((word) => [word, carriesNoAddress(word)])),
    rendering: lines(rendering.map((fields) => ({ fields, rendered: renderLocator(fields) }))),
    verdicts: lines(verdictCases().map((parsed) => ({ parsed, verdict: verdictOf(parsed) }))),
    tallies: lines(tallied.map((list) => ({ locators: list, tallies: [...tallyLocators(list)] }))),
    commands: lines(commands.map(command)),
  }),
);
fs.rmSync(scratch, { recursive: true });
console.log(`Recorded ${results.length} stored locators, ${locators.length} written ones and ${commands.length} runs of the command at ${storeRef}.`);
