// Record what the TypeScript write path does, so the Python port is compared with the code
// it replaces and not with a reading of it.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_write_fixture.mjs
//
// It runs inside the data repository, in the `fixtures` job in CI, and nowhere else, after
// record_validation_fixture.mjs, whose small store on disk the cases are built from. The
// cases come from write_cases.mjs, which needs none of the data repository's code.
//
//   tests/fixtures/write-path.json
//       addBatch, recordChanges and applyChanges, each on stores written to a scratch folder:
//       what the call returned or how it refused, and every file it left different.
//
// The functions are called with the scratch folder as their root. The command line is not
// run: it writes the checkout it is run in, and its SHACL step is not what is recorded here.
// The clock is held still while they run, so the date a change is recorded under is the same
// on every run and the file can be compared with the committed one.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { CLOCK, DATE, writeCases } from "./write_cases.mjs";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

async function load(file) {
  const module = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(module.default ?? {}), ...module };
}
const { addBatch, recordChanges, applyChanges } = await load("scripts/omics/records.ts");

const version = (name) => JSON.parse(fs.readFileSync(path.join(store, "node_modules", name, "package.json"), "utf8")).version;
const recordedFrom = {
  script: "scripts/fixtures/record_write_fixture.mjs",
  cases: "scripts/fixtures/write_cases.mjs",
  store_ref: execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim(),
  zod: version("zod"),
  node: process.version.split(".")[0],
};

// One member or item per line down to `levels`, and one line each below that.
function written(value, levels, indent = "") {
  if (levels === 0 || value === null || typeof value !== "object") return JSON.stringify(value);
  const inner = `${indent} `;
  if (Array.isArray(value)) return value.length ? `[\n${value.map((item) => inner + written(item, levels - 1, inner)).join(",\n")}\n${indent}]` : "[]";
  const members = Object.entries(value).filter(([, held]) => held !== undefined);
  return members.length ? `{\n${members.map(([key, held]) => `${inner}${JSON.stringify(key)}: ${written(held, levels - 1, inner)}`).join(",\n")}\n${indent}}` : "{}";
}

/** What a call does: null if it returns, zod's issues if it throws them, a system error's
 * code, or the error's name and message, with the scratch folder written as <work>. */
function outcome(run, work) {
  try {
    run();
    return null;
  } catch (error) {
    if (Array.isArray(error?.issues)) return { zod: error.issues.map((issue) => [issue.code, issue.path, issue.message]) };
    if (error?.code && error?.syscall) return { name: error.name, code: error.code };
    return { name: error?.name ?? typeof error, message: String(error?.message).split(work).join("<work>") };
  }
}

function filesUnder(root, folder = "") {
  const out = {};
  for (const entry of fs.readdirSync(path.join(root, folder), { withFileTypes: true })) {
    const name = folder ? `${folder}/${entry.name}` : entry.name;
    if (entry.isDirectory()) Object.assign(out, filesUnder(root, name));
    else out[name] = fs.readFileSync(path.join(root, name), "utf8");
  }
  return out;
}

const tiny = JSON.parse(fs.readFileSync(path.join(repo, "tests/fixtures/validation.json"), "utf8")).load[0].files;
const { stores, cases } = writeCases(tiny);

const RealDate = Date;
class StoppedDate extends RealDate {
  constructor(...given) {
    if (given.length) super(...given);
    else super(CLOCK);
  }
  static now() {
    return new RealDate(CLOCK).getTime();
  }
}

const recorded = cases.map((held) => {
  const work = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "rewire-evidence-write-")));
  const root = path.join(work, "store");
  fs.mkdirSync(root);
  const before = { ...stores[held.store] };
  for (const [file, text] of Object.entries(held.edits)) {
    if (text === null) delete before[file];
    else before[file] = text;
  }
  for (const [file, text] of Object.entries(before)) {
    fs.mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    fs.writeFileSync(path.join(root, file), text);
  }
  const input = path.join(work, "input.jsonl");
  if (held.input !== null) fs.writeFileSync(input, held.input);
  let returned = null;
  globalThis.Date = StoppedDate;
  const error = outcome(() => {
    if (held.command === "add") returned = addBatch(input, held.batch_dir, root);
    else if (held.command === "change") recordChanges(held.ids, held.batch_dir, held.review, root);
    else returned = applyChanges(input, held.batch_dir, held.review, root);
  }, work);
  globalThis.Date = RealDate;
  const left = filesUnder(root);
  const after = {};
  for (const file of new Set([...Object.keys(before), ...Object.keys(left)])) if (before[file] !== left[file]) after[file] = left[file] ?? null;
  fs.rmSync(work, { recursive: true });
  if (held.accepted !== null && held.accepted !== (error === null))
    throw new Error(`${held.command}: ${held.name}: expected to be ${held.accepted ? "accepted" : "refused"}, and got ${JSON.stringify(error)}`);
  if (error !== null && Object.keys(after).length) throw new Error(`${held.command}: ${held.name}: refused, and left ${Object.keys(after).join(", ")} changed`);
  return { ...held, error, returned, after };
});

fs.writeFileSync(path.join(repo, "tests/fixtures/write-path.json"), `${written({ recorded_from: recordedFrom, clock: CLOCK, date: DATE, stores, cases: recorded }, 2)}\n`);
const count = (command) => recorded.filter((held) => held.command === command).length;
const refused = recorded.filter((held) => held.error !== null).length;
console.log(`write path: ${count("add")} adds, ${count("change")} changes by ID, ${count("changes")} changes files; ${refused} refused, ${recorded.length - refused} written`);
