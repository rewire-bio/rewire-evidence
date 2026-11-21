// Record what the TypeScript checks pack reports, so the Python port is compared with the
// code it replaces and not with a reading of it.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_checks_fixture.mjs
//
// It runs inside the data repository, in the `fixtures` job in CI, and nowhere else. The
// inputs come from checks_cases.mjs and validation_cases.mjs, which need none of the data
// repository's code.
//
//   scripts/expected/store-checks.json
//       The run of scripts/omics/checks/ over the pinned store: how much it read, the two
//       vocabularies as it read them, each check's counts, what the command printed, and how
//       the run stands against the committed baseline, with a result seeded and a count
//       lowered as tests/checks-baseline.test.ts does. It also states what the data
//       repository commits of the first run, which the gate reproduces from another commit:
//       the baseline's commit and date, and the rows and SHA-256 of the report.
//   scripts/expected/store-checks.tsv.gz
//       The report of that run, one row for each finding.
//   scripts/expected/store-checks-columns.jsonl.gz
//       For every result, the anchor and the column check C5 reads from its locator. A
//       column read wrongly shows in the report only where it splits a group.
//   scripts/expected/store-checks-cells.jsonl.gz
//       For every printed value in the store, with and without its unit being percent as
//       the store has it, what parseCell reads or the message it throws.
//   src/rewire_evidence/data/powers-of-ten.json
//       10 ** n for every n that gives a finite number. V8 does not give the nearest double
//       for every power, so the library reads them from here.
//   tests/fixtures/checks.json
//       The patterns and constants as the TypeScript holds them, and what it does with the
//       cells, numbers, locators, small stores, checkouts, baselines and command lines in
//       checks_cases.mjs.
//
// gzip does not write the same bytes on every zlib, so each compressed file is rewritten
// only when the lines inside it differ.
import { execFileSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import zlib from "node:zlib";
import { BASELINE, cells, checkouts, commands, lines as lineCases, locators, missing, powers, ratchets, rounded, roundings, runs, samePairs } from "./checks_cases.mjs";
import { normalizeCases } from "./validation_cases.mjs";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const DIR = "scripts/omics/checks";
const COMMAND = `${DIR}/cli.ts`;
const tsx = path.join(store, "node_modules/.bin/tsx");

async function load(file) {
  const module = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(module.default ?? {}), ...module };
}

/** A module of the data repository with more of it exported: a copy written beside it, read and removed. */
async function exposed(file, exports) {
  const copy = file.replace(/\.ts$/, "-exposed-for-recording.ts");
  fs.writeFileSync(path.join(store, copy), `${fs.readFileSync(path.join(store, file), "utf8")}\n${exports}\n`);
  try {
    return await load(copy);
  } finally {
    fs.rmSync(path.join(store, copy));
  }
}

const cli = await load(COMMAND);
const { tsvHeader, tsvRow, oneLine, text } = await load(`${DIR}/finding.ts`);
const pair = (pattern) => `[${pattern}.flags, ${pattern}.source]`;
const { heldForRecording: cell$ } = await exposed(
  "scripts/omics/extract/tables.ts",
  `export const heldForRecording = { parseCell, isMissingCell, not_reported: ${pair("NOT_REPORTED")} };`,
);
const { parseCell, isMissingCell } = cell$;
const { heldForRecording: c1 } = await exposed(`${DIR}/unit_for_metric.ts`, "export const heldForRecording = { scopeNoteRules };");
const { heldForRecording: c3 } = await exposed(`${DIR}/printed_vs_numeric.ts`, "export const heldForRecording = { same, roundsTo, TOLERANCE };");
const { heldForRecording: c4 } = await exposed(`${DIR}/qualifier_shape.ts`, "export const heldForRecording = { tokens: [...tokens], matchers: matchers.map(({ token, re }) => [token, re.flags, re.source]) };");
const { heldForRecording: c5 } = await exposed(`${DIR}/column_coherence.ts`, `export const heldForRecording = { anchorOf, columnOf, anchor: ${pair("anchorRe")}, column: ${pair("columnRe")} };`);

const storeRef = execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim();
const recordedFrom = {
  script: "scripts/fixtures/record_checks_fixture.mjs",
  cases: "scripts/fixtures/checks_cases.mjs",
  store_ref: storeRef,
  node: process.version,
  unicode: process.versions.unicode,
};
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const rows = (items) => `[\n${items.map((item) => `  ${JSON.stringify(item)}`).join(",\n")}\n ]`;
const members = (held) => `{\n${Object.entries(held).map(([key, value]) => ` ${JSON.stringify(key)}: ${value}`).join(",\n")}\n}\n`;

/** What a call threw, or null. A message that names a scratch folder is this run's alone, so only the name is kept. */
function outcome(run) {
  try {
    run();
    return null;
  } catch (error) {
    return { name: error?.constructor?.name ?? typeof error, message: String(error?.message ?? error).includes(os.tmpdir()) ? null : String(error?.message ?? error) };
  }
}

const reportOf = (findings) => `${[tsvHeader, ...findings.map(tsvRow)].join("\n")}\n`;
const storeCounts = (input) => ({
  records: input.records.length,
  results: input.results.length,
  sources: input.sources.length,
  metric_concepts: input.metrics.size,
  unit_concepts: input.units.size,
});
const vocabularyOf = (input) => ({
  metrics: [...input.metrics.values()].map((concept) => [concept.key, concept.defaultDirection]),
  units: [...input.units.values()].map((concept) => [concept.key, concept.scopeNote]),
});

/** A printed cell as parseCell reads it, or the message it throws. */
function read(printed, unit) {
  try {
    const cell = parseCell(printed, unit === null ? {} : { unit });
    return [cell.printed, cell.value, cell.sd];
  } catch (error) {
    return error.message;
  }
}

/** Write a compressed file of lines, unless it already holds them. Returns its facts. */
function dump(name, content) {
  const data = Buffer.from(content, "utf8");
  const file = path.join(repo, "scripts/expected", name);
  const held = fs.existsSync(file) ? zlib.gunzipSync(fs.readFileSync(file)) : null;
  if (held === null || !held.equals(data)) fs.writeFileSync(file, zlib.gzipSync(data, { level: 9 }));
  return { file: name, lines: content.split("\n").length - 1, sha256: sha256(data) };
}

/** Run the command as `npm run checks --` runs it, in a folder. */
function command(folder, args, env = {}) {
  const done = spawnSync(tsx, [path.join(store, COMMAND), ...args], {
    cwd: folder,
    encoding: "utf8",
    env: { ...process.env, TSX_TSCONFIG_PATH: path.join(store, "tsconfig.json"), ...env },
  });
  if (done.error) throw done.error;
  return done;
}
// How long a run took is that run's alone.
const untimed = (said) => said.replace(/ in \d+\.\d s$/m, " in <seconds> s");

// ---------------------------------------------------------------------------------------
// The pinned store.

const input = cli.loadInput(store);
const run = cli.runAll(input);
const report = reportOf(run.findings);

// The command itself, which writes its report into the checkout it is run in. The report is
// read back and removed, and has to be the one made here.
const stamp = "1999-12-31";
const reportFile = path.join(store, cli.reportDir, `checks-${stamp}.tsv`);
if (fs.existsSync(reportFile)) throw new Error(`${reportFile} is in the way`);
const said = command(store, ["report", "--date", stamp]);
if (said.status !== 0) throw new Error(`checks -- report failed on the store: ${said.stderr}`);
const wrote = fs.readFileSync(reportFile, "utf8");
fs.rmSync(reportFile);
if (wrote !== report) throw new Error("the command's report is not the one runAll gives");

const columnLines = input.results.map((record) => {
  const locator = text(record, "source_locator");
  return `${JSON.stringify(locator === undefined ? null : [c5.anchorOf(locator), c5.columnOf(locator)])}\n`;
});
const seen = new Set();
const cellLines = [];
for (const record of input.results) {
  const printed = text(record, "printed_value");
  if (printed === undefined) continue;
  const percent = text(record, "unit") === "percent";
  const key = `${percent ? 1 : 0}${printed}`;
  if (seen.has(key)) continue;
  seen.add(key);
  cellLines.push(`${JSON.stringify([printed, percent, read(printed, percent ? "percent" : null)])}\n`);
}

const baselineText = fs.readFileSync(path.join(store, BASELINE), "utf8");
const baseline = JSON.parse(baselineText);
const firstReport = `${cli.reportDir}/checks-${baseline.run_date}.tsv`;
const firstRows = fs.readFileSync(path.join(store, firstReport));
// What tests/checks-baseline.test.ts seeds: one result whose qualifier is a bare verdict.
const seededRecord = {
  id: "seeded-result-for-the-ratchet-test",
  kind: "result",
  status: "source_checked",
  source_ids: [],
  links: [],
  attributes: { metric: "auroc", metric_direction: "higher", unit: "unitless", metric_qualifier: "high", printed_value: "0.9", numeric_value: "0.9", source_locator: "Table 1, column 'AUROC'" },
};
const grown = cli.runAll({ ...input, results: [...input.results, seededRecord] });
const lowered = structuredClone(baseline);
lowered.checks.C9.rules["same-artifact-and-url"] -= 1;
lowered.checks.C9.findings -= 1;
lowered.total_findings -= 1;

fs.writeFileSync(
  path.join(repo, "scripts/expected/store-checks.json"),
  members({
    recorded_from: JSON.stringify(recordedFrom),
    first_run: JSON.stringify({
      commit: baseline.commit,
      run_date: baseline.run_date,
      baseline: BASELINE,
      baseline_sha256: sha256(Buffer.from(baselineText, "utf8")),
      report: firstReport,
      rows: firstRows.toString("utf8").split("\n").length - 2,
      report_sha256: sha256(firstRows),
    }),
    store: JSON.stringify(storeCounts(input)),
    vocabulary: JSON.stringify(vocabularyOf(input)),
    total_findings: String(run.findings.length),
    summaries: rows(run.summaries),
    report: JSON.stringify(dump("store-checks.tsv.gz", report)),
    columns: JSON.stringify(dump("store-checks-columns.jsonl.gz", columnLines.join(""))),
    cells: JSON.stringify(dump("store-checks-cells.jsonl.gz", cellLines.join(""))),
    printed: JSON.stringify(untimed(said.stdout).replaceAll(stamp, "<date>")),
    ratchet: JSON.stringify({
      counts: cli.ratchetCounts(run),
      baseline_counts: cli.ratchetCounts(baseline),
      above: cli.aboveBaseline(baseline, run),
      failure: cli.ratchetFailure(baseline, run),
      seeded: { record: seededRecord, above: cli.aboveBaseline(baseline, grown), failure: cli.ratchetFailure(baseline, grown) },
      lowered: { check: "C9", rule: "same-artifact-and-url", by: 1, above: cli.aboveBaseline(lowered, run), failure: cli.ratchetFailure(lowered, run) },
    }),
  }),
);

// ---------------------------------------------------------------------------------------
// The cases.

function inputOf({ records, metrics, units }) {
  return {
    records,
    results: records.filter((record) => record.kind === "result"),
    sources: records.filter((record) => record.kind === "source"),
    metrics: new Map(metrics.map(([key, defaultDirection]) => [key, { key, defaultDirection }])),
    units: new Map(units.map(([key, scopeNote]) => [key, { key, scopeNote }])),
  };
}

/** The pack, or the checks named, over an input: the findings, the counts and the report, or what it threw. */
function ran(given, only) {
  let done = null;
  const error = outcome(() => {
    done = cli.runAll(given, only ? cli.checks.filter((check) => only.includes(check.id)) : undefined);
  });
  return error ? { error } : { findings: done.findings, summaries: done.summaries, tsv: reportOf(done.findings) };
}

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "checks-fixture-"));
function laidOut(files) {
  const folder = fs.mkdtempSync(path.join(scratch, "run-"));
  for (const [file, held] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(folder, file)), { recursive: true });
    fs.writeFileSync(path.join(folder, file), held);
  }
  return folder;
}

function checkout({ name, files }) {
  const folder = laidOut(files);
  let given = null;
  const error = outcome(() => {
    given = cli.loadInput(folder);
  });
  if (error) return { name, files, error };
  return { name, files, error: null, store: storeCounts(given), ...vocabularyOf(given), run: ran(given) };
}

/** One case of the command: each step run or made in the same folder, and the files left. */
function commanded({ name, files, steps }) {
  const folder = laidOut(files);
  const dates = new Set([new Date().toISOString().slice(0, 10)]);
  const recorded = [];
  for (const step of steps) {
    if (step.edit) {
      const file = step.file ?? BASELINE;
      const edited = `${JSON.stringify(step.edit(JSON.parse(fs.readFileSync(path.join(folder, file), "utf8"))), null, 2)}\n`;
      fs.writeFileSync(path.join(folder, file), edited);
      recorded.push({ given: { [file]: edited } });
      continue;
    }
    const done = command(folder, step.args, step.env);
    dates.add(new Date().toISOString().slice(0, 10));
    // A failure prints a stack trace with this machine's paths in it. Only the usage line is kept.
    const stderr = done.stderr.startsWith("Usage:") ? done.stderr : null;
    recorded.push({ args: step.args, env: step.env ?? {}, status: done.status, stdout: untimed(done.stdout), stderr });
  }
  // A run with no --date is under the date of the run, which is recorded as <date>.
  const dated = steps.some((step) => step.args && !step.args.includes("--date"));
  const undated = (held) => (dated ? [...dates].reduce((kept, date) => kept.replaceAll(date, "<date>"), held) : held);
  const written = {};
  const walk = (dir) => {
    for (const entry of fs.readdirSync(path.join(folder, dir), { withFileTypes: true })) {
      const file = path.posix.join(dir, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (!(file in files)) written[undated(file)] = undated(fs.readFileSync(path.join(folder, file), "utf8"));
    }
  };
  walk("");
  return { name, files, steps: recorded.map((step) => (step.stdout === undefined ? step : { ...step, stdout: undated(step.stdout) })), written };
}

const number = (held) => (Object.is(held, -0) ? "-0" : String(held));

const finite = [];
for (let places = 0; Number.isFinite(10 ** places); places += 1) finite.push(String(10 ** places));
fs.writeFileSync(
  path.join(repo, "src/rewire_evidence/data/powers-of-ten.json"),
  `{\n "recorded_from": ${JSON.stringify({ script: recordedFrom.script, node: recordedFrom.node })},\n "powers": ${JSON.stringify(finite)}\n}\n`,
);
const baselineOnly = (held) => ({ note: held.note, notes: held.notes, commit: held.commit, run_date: held.run_date, store: held.store, total_findings: held.total_findings, checks: held.checks });

fs.writeFileSync(
  path.join(repo, "tests/fixtures/checks.json"),
  members({
    recorded_from: JSON.stringify(recordedFrom),
    constants: JSON.stringify({
      checks: cli.checks.map((check) => [check.id, check.title, check.rules, typeof check.context === "function"]),
      baseline_file: cli.baselineFile,
      report_dir: cli.reportDir,
      remedy: cli.ratchetRemedy,
      note: cli.ratchetNote,
      notes: baseline.notes,
      tsv_header: tsvHeader,
      tolerance: c3.TOLERANCE,
      scope_note_rules: c1.scopeNoteRules,
      tokens: c4.tokens,
      matchers: c4.matchers,
      anchor: c5.anchor,
      column: c5.column,
      not_reported: cell$.not_reported,
    }),
    cells: rows(cells.map(([printed, unit]) => [printed, unit, read(printed, unit)])),
    missing: rows(missing.map((printed) => [printed, isMissingCell(printed)])),
    rounded: rows(rounded.map((held) => [held, number(Math.round(Number(held)))])),
    powers: rows(powers.map((places) => [places, String(10 ** places)])),
    same: rows(samePairs.map(([a, b]) => [a, b, c3.same(Number(a), Number(b))])),
    roundings: rows(roundings.map(([value, printed]) => [value, printed, c3.roundsTo(Number(value), printed)])),
    lines: rows(lineCases.map((held) => [held, oneLine(held)])),
    locators: rows(locators.map((locator) => [locator, c5.anchorOf(locator), c5.columnOf(locator)])),
    runs: rows(runs.map(({ name, records, metrics, units, only }) => ({ name, records, metrics, units, only: only ?? null, ...ran(inputOf({ records, metrics, units }), only) }))),
    normalize: rows(normalizeCases().map(({ name, records }) => ({ name, records, ...ran(inputOf({ records, metrics: [], units: [] }), ["C13"]) }))),
    checkouts: rows(checkouts.map(checkout)),
    ratchets: rows(ratchets.map(({ name, baseline: pinned, summaries }) => {
      const made = { findings: [], summaries };
      return {
        name,
        baseline: baselineOnly(pinned),
        summaries,
        counts: Object.entries(cli.ratchetCounts(made)),
        baseline_counts: Object.entries(cli.ratchetCounts(pinned)),
        above: cli.aboveBaseline(pinned, made),
        failure: cli.ratchetFailure(pinned, made),
      };
    })),
    commands: rows(commands.map(commanded)),
  }),
);
fs.rmSync(scratch, { recursive: true });
console.log(
  `Recorded ${run.findings.length} findings over ${input.records.length} records at ${storeRef}, ${columnLines.length} locators, ${cellLines.length} printed values, `
  + `${cells.length} cells, ${runs.length} small stores, ${checkouts.length} checkouts, ${ratchets.length} baselines and ${commands.length} runs of the command.`,
);
