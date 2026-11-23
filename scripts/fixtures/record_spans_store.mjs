// Record what the TypeScript span measurement makes of the pinned store, so the Python port is
// compared with the code it replaces over every stored result.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   ARCHIVE_CACHE=<folder of objects> node_modules/.bin/tsx \
//     <rewire-evidence>/scripts/fixtures/record_spans_store.mjs <folder to write> <date>
//
// A third argument names the checkout of the data repository to take the TypeScript from,
// where the store to measure is another folder: record_spans_fixture.mjs runs this over a
// store small enough to commit, from that folder.
//
// The measurement reads the bytes of the archived objects, which no checkout holds, and derives
// each PDF layer with pdftotext. So it runs where those are: in the `layers` job in CI, over
// the objects a public release mirrors, and once on a machine that holds every object. The
// two runs are the same script, and the folder it is given decides which it is.
//
// It writes, into the folder it is given:
//
//   store-spans.jsonl   the lines scripts/omics/extract/measure-store-spans.ts writes: one for
//                       each result, with the layer and the range of the span it resolves to,
//                       or the reason it was refused. It holds the text of each span.
//   rows.jsonl          one line for each result with everything else the measurement decided:
//                       the layers it had to choose from and how many it tried, both ranges of
//                       the span, the route to a grid, the four readings of the stored value,
//                       and one digest over every text the outcome carries. It holds no text of
//                       any layer, so it can be committed where the objects are restricted.
//   legacy-rows.jsonl   the same line for each result the measurement decides otherwise with
//                       `--legacy-quote`, which is the other arm of the pair the figures hold.
//   stdout.txt          what `npm run spans -- measure` prints, with the clock held still.
//   by-*.tsv            the five tables it writes.
//   figures.json        the figures file it writes, and
//   figures-records.tsv the records beside it.
//   summary.json        the commit, the Node, the counts and the SHA-256 of each file above.
//   pdftotext.json      how many PDFs pdftotext was given, and each time it failed on one.
//
// The command writes its tables and figures into the checkout under the date of the run. This
// script holds the date to the one it is given, takes the files out and puts the checkout back
// as it was.
import childProcess, { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [out, date, code = process.cwd()] = process.argv.slice(2);
if (!out || !/^\d{4}-\d{2}-\d{2}$/.test(date ?? "")) {
  console.error("usage: tsx record_spans_store.mjs <folder to write> <date as YYYY-MM-DD> [<checkout of the code>]");
  process.exit(2);
}
const store = process.cwd();
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const COMMAND = "scripts/omics/spans/cli.ts";
const REPORTS = "maintenance/reports";
const TABLES = ["class", "reason", "kind", "parse-bucket", "grid-route"];
async function load(file) {
  const module = await import(pathToFileURL(path.join(code, file)).href);
  return { ...(module.default ?? {}), ...module };
}

/** The command with `runMeasure` exported: a copy written beside it, read and removed.
 * The copy is not one of the files the figures name as inputs, which are listed by name. */
async function exposed() {
  const copy = COMMAND.replace(/\.ts$/, "-exposed-for-recording.ts");
  fs.writeFileSync(path.join(code, copy), `${fs.readFileSync(path.join(code, COMMAND), "utf8")}\nexport const runMeasureForRecording = runMeasure;\n`);
  try {
    return await load(copy);
  } finally {
    fs.rmSync(path.join(code, copy));
  }
}

// A measurement loses a PDF layer without a word when pdftotext fails on it: the layer has no
// text that time, and every result on it is refused as not readable. That is a run of the
// program going wrong and not what the TypeScript decides, so pdftotext is asked again here,
// up to three times, and every failure is written to pdftotext.json with what it was. The
// TypeScript reaches pdftotext through this function, which it looks up each time it calls.
// A PDF that gives two different texts in one run is written there too, and one that still
// has no text after four attempts ends the recording.
const spawnSync = childProcess.spawnSync;
const now = Date.now;
const conversions = { calls: 0, failed: [], lost: 0, texts: new Map() };
childProcess.spawnSync = function asked(program, given, options) {
  // A call with no input asks pdftotext for its version, and is whether there is one at all.
  if (program !== "pdftotext" || !options?.input) return spawnSync.call(this, program, given, options);
  conversions.calls += 1;
  const input = sha256(options.input);
  let done;
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    const started = now();
    done = spawnSync.call(this, program, given, options);
    if (!done.error && done.status === 0) break;
    conversions.failed.push({
      arguments: given,
      input,
      attempt,
      milliseconds: now() - started,
      error: done.error ? `${done.error.code ?? ""} ${done.error.message}`.trim() : null,
      status: done.status,
      signal: done.signal,
      stderr: done.stderr ? done.stderr.toString("utf8").slice(-300) : null,
    });
  }
  if (done.error || done.status !== 0) conversions.lost += 1;
  else {
    const seen = conversions.texts.get(input) ?? new Set();
    seen.add(sha256(done.stdout));
    conversions.texts.set(input, seen);
  }
  return done;
};

const storeRef = execFileSync("git", ["rev-parse", "HEAD"], { cwd: code, encoding: "utf8" }).trim();
const { measure, runMeasureForRecording } = await exposed();

// The clock, held at noon of the date given, so the files are named for it and the last line
// the command prints, which is how long it took, is always `0 s`.
const RealDate = Date;
const noon = new RealDate(`${date}T12:00:00Z`).getTime();
class HeldDate extends RealDate {
  constructor(...given) {
    if (given.length === 0) super(noon);
    else super(...given);
  }
  static now() {
    return noon;
  }
}

const written = [...TABLES.map((name) => `${REPORTS}/span-resolution-${date}-by-${name}.tsv`), `${REPORTS}/span-resolution-figures.json`, `${REPORTS}/span-resolution-figures-records.tsv`];
const before = new Map(written.map((file) => [file, fs.existsSync(path.join(store, file)) ? fs.readFileSync(path.join(store, file)) : null]));
const printed = [];
const write = process.stdout.write;
fs.mkdirSync(out, { recursive: true });
const keep = (name, data) => {
  fs.writeFileSync(path.join(out, name), data);
  return sha256(data);
};
const digests = {};
try {
  globalThis.Date = HeldDate;
  process.stdout.write = (chunk) => {
    printed.push(String(chunk));
    return true;
  };
  runMeasureForRecording(".", path.join(".", REPORTS), true, true, false);
} finally {
  process.stdout.write = write;
  globalThis.Date = RealDate;
  if (process.exitCode) throw new Error("the command refused to write: the checkout moved while it was measured");
  for (const name of TABLES) digests[`by-${name}.tsv`] = keep(`by-${name}.tsv`, fs.readFileSync(path.join(store, `${REPORTS}/span-resolution-${date}-by-${name}.tsv`)));
  digests["figures.json"] = keep("figures.json", fs.readFileSync(path.join(store, `${REPORTS}/span-resolution-figures.json`)));
  digests["figures-records.tsv"] = keep("figures-records.tsv", fs.readFileSync(path.join(store, `${REPORTS}/span-resolution-figures-records.tsv`)));
  for (const [file, data] of before) {
    if (data === null) fs.rmSync(path.join(store, file), { force: true });
    else fs.writeFileSync(path.join(store, file), data);
  }
}
digests["stdout.txt"] = keep("stdout.txt", printed.join(""));

// The same measurement once more, for what it decided about each result. The command keeps
// its report to itself, and `measure` is what scripts/omics/extract/measure-store-spans.ts calls.
const report = measure({ root: ".", log: () => {} });
const spans = [];
const text = (held) => sha256(Buffer.from(JSON.stringify(held), "utf8")).slice(0, 16);
function rowOf(item) {
  const resolved = item.resolved;
  const span = resolved?.span ?? null;
  return JSON.stringify({
    id: item.result.id,
    candidates: item.candidates.map((candidate) => candidate.layer_sha256.slice(0, 12)),
    tried: item.tried,
    ok: Boolean(resolved),
    kind: resolved?.kind ?? null,
    // A refusal is kept beside a span when an earlier layer refused the locator and a later one
    // resolved it, and the tables count the span. Both are recorded.
    reason: item.refusal?.reason ?? null,
    layer: resolved ? resolved.layer_sha256 : (item.refusal?.layer_sha256 ?? null),
    start: span?.start ?? null,
    end: span?.end ?? null,
    byte_start: span?.byte_start ?? null,
    byte_end: span?.byte_end ?? null,
    anchor_candidates: resolved?.anchor_candidates ?? null,
    grid_route: item.grid_route,
    resolved_route: resolved?.grid_route ?? null,
    value_present: item.value_present,
    value_exact: item.value_exact,
    value_partial: item.value_partial,
    value_in_image: item.value_in_image,
    text: text([
      span?.exact ?? null,
      span?.prefix ?? null,
      span?.suffix ?? null,
      resolved?.row_label ?? null,
      resolved?.column_header ?? null,
      item.refusal?.detail ?? null,
    ]),
  });
}
const rows = report.work.map(rowOf);
for (const item of report.work) {
  const resolved = item.resolved;
  const span = resolved?.span ?? null;
  spans.push(
    JSON.stringify({
      id: item.result.id,
      sources: item.result.source_ids,
      locator: item.result.locator,
      printed_value: item.result.printed_value,
      ok: Boolean(resolved),
      kind: resolved?.kind ?? null,
      reason: item.refusal?.reason ?? null,
      layer: span?.layer_sha256 ?? null,
      start: span?.start ?? null,
      end: span?.end ?? null,
      exact: span?.exact ?? null,
      value_present: item.value_present,
      value_exact: item.value_exact,
    }),
  );
}
digests["store-spans.jsonl"] = keep("store-spans.jsonl", `${spans.join("\n")}\n`);
digests["rows.jsonl"] = keep("rows.jsonl", `${rows.join("\n")}\n`);

// The other arm. Only the results it decides otherwise are written.
const legacy = measure({ root: ".", legacyQuote: true, log: () => {} });
if (legacy.work.length !== rows.length) throw new Error("the two arms measured another number of results");
const otherwise = legacy.work.map(rowOf).filter((row, at) => row !== rows[at]);
digests["legacy-rows.jsonl"] = keep("legacy-rows.jsonl", otherwise.map((row) => `${row}\n`).join(""));

// Which results the objects in this folder decide. A result is tried on the text layers of its
// sources that the sanity gate did not fail, and each is derived from one object. Where every
// one of those objects is held, the measurement reads nothing that is missing, so it is the
// measurement a folder of every object gives. The gate works this out for itself and has to
// reach the same count.
const { readManifest } = await load("scripts/archive/manifest.ts");
const { hasObject } = await load("scripts/archive/objects.ts");
const { readRegistry, isGrid } = await load("shared/omics/layers/index.ts");
const keys = [...readManifest(".").keys()];
const heldKeys = new Set(keys.filter((key) => hasObject(key, ".")));
const needs = new Map();
for (const record of readRegistry(".")) {
  if (isGrid(record) || record.sanity === "fail") continue;
  for (const source of record.source_ids) {
    if (!needs.has(source)) needs.set(source, new Set());
    needs.get(source).add(record.object_sha256);
  }
}
const determined = report.work.filter((item) =>
  item.result.source_ids.every((source) => [...(needs.get(source) ?? [])].every((key) => heldKeys.has(key))),
).length;

const summary = {
  recorded_from: { script: "scripts/fixtures/record_spans_store.mjs", store_ref: storeRef, node: process.version, unicode: process.versions.unicode },
  date,
  results: report.work.length,
  objects: keys.length,
  objects_held: heldKeys.size,
  determined,
  held: report.work.length - determined,
  resolved: report.resolved,
  refused: report.locators - report.resolved,
  layers_read: report.layers_read,
  layers_astral: report.layers_astral,
  astral_points: report.astral_points,
  grids_read: report.grids_read,
  legacy_resolved: legacy.resolved,
  legacy_otherwise: otherwise.length,
  sha256: digests,
};
fs.writeFileSync(path.join(out, "summary.json"), `${JSON.stringify(summary)}\n`);
// What pdftotext was asked and how it went. This is of the run and not of the store, so it is
// a file of its own that the gate does not read: a failure that asking again put right changed
// nothing above.
const pdftotext = {
  conversions: conversions.calls,
  failed: conversions.failed,
  without_a_text: conversions.lost,
  gave_two_texts: [...conversions.texts].filter(([, seen]) => seen.size > 1).map(([input, seen]) => ({ input, texts: [...seen] })),
};
fs.writeFileSync(path.join(out, "pdftotext.json"), `${JSON.stringify(pdftotext, null, 1)}\n`);
if (pdftotext.failed.length > 0 || pdftotext.gave_two_texts.length > 0) console.error(`pdftotext: ${JSON.stringify(pdftotext)}`);
// A layer of an object that is held and was not read: the measurement is not of the store.
const unread = [...report.work, ...legacy.work].filter((item) => item.refusal?.reason === "layer_not_readable" && item.refusal.layer_sha256);
if (pdftotext.without_a_text > 0 || pdftotext.gave_two_texts.length > 0 || unread.length > 0) {
  const said = [...new Set(unread.map((item) => item.refusal.detail))].slice(0, 5).join("; ");
  console.error(`The measurement is not of the store: ${unread.length} results were refused on a layer an object in the cache did not give. ${said}`);
  process.exitCode = 1;
}
console.log(
  `Recorded ${summary.results} results at ${storeRef} over ${summary.objects_held} of ${summary.objects} objects: ` +
    `${summary.resolved} resolved, ${summary.refused} refused, ${summary.layers_read} layers read, ` +
    `${summary.determined} results decided by the objects held.`,
);
