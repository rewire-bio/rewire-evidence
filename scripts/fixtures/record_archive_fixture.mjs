// Record what the TypeScript archive read side and licence map do, so the Python port is
// compared with the code it replaces and not with a reading of it.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_archive_fixture.mjs
//
// It runs inside the data repository, in the `fixtures` job in CI, and nowhere else. The
// cases come from archive_cases.mjs, which needs none of the data repository's code.
//
//   scripts/expected/store-archive.json
//   scripts/expected/store-archive.jsonl.gz
//       The pinned checkout as the TypeScript reads it, one line for each thing read: every
//       row of the licence map; every source record with its work item, the tier its licence
//       determines and its row in the triage queue; every manifest row with the verdict of
//       manifestRowSchema, the SHA-256 of the line canonicalLine writes from what the schema
//       read, what licenceClassifier says of it and how reclassify reads it; every fetch
//       receipt, licence candidate and licence receipt with its schema's verdict and the same
//       digest; each candidate built again from its own observations; the latest receipt of
//       each source; and the triage queue as render writes it. The JSON file states the
//       commit, the counts, the SHA-256 of the lines, the SHA-256 of each TypeScript file
//       read, the work list summary, the reclassification plan, and what each command prints.
//   tests/fixtures/archive.json
//       What the schemas say of the rows, receipts and candidates in archive_cases.mjs, what
//       the small functions return, how Node reads each URL and expands each gzip file, and
//       the scenarios: a tree laid out in a scratch folder, the calls made on it in order
//       with what each returned or threw and every file it left different, then the commands
//       run on the tree laid out again.
//
// gzip does not write the same bytes on every zlib, so the compressed file is rewritten
// only when the lines inside it differ. The clock is held still while a call runs.
import { execFileSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import zlib from "node:zlib";
import * as cases from "./archive_cases.mjs";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const MODULES = [
  "shared/archive/schema.ts",
  "shared/archive/worklist.ts",
  "scripts/archive/manifest.ts",
  "scripts/archive/objects.ts",
  "scripts/archive/receipts.ts",
  "scripts/archive/reclassify.ts",
  "scripts/archive/cli.ts",
  "scripts/licence/map.ts",
  "scripts/licence/triage.ts",
  "scripts/licence/counts.ts",
  "scripts/licence/resolve.ts",
  "scripts/release/files.ts",
];
const CLOCK = "2026-11-21T12:34:56.789Z";
const ROOT = "r";

const load = async (file) => {
  const loaded = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(loaded.default ?? {}), ...loaded };
};
const schema = await load("shared/archive/schema.ts");
const worklist = await load("shared/archive/worklist.ts");
const manifest = await load("scripts/archive/manifest.ts");
const objects = await load("scripts/archive/objects.ts");
const receipts = await load("scripts/archive/receipts.ts");
const reclassify = await load("scripts/archive/reclassify.ts");
const licenceMap = await load("scripts/licence/map.ts");
const triage = await load("scripts/licence/triage.ts");
const resolve = await load("scripts/licence/resolve.ts");
const release = await load("scripts/release/files.ts");

const storeRef = execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim();
const dirty = () => execFileSync("git", ["status", "--porcelain"], { cwd: store, encoding: "utf8" });
const dirtyBefore = dirty();
const recordedFrom = {
  script: "scripts/fixtures/record_archive_fixture.mjs",
  cases: "scripts/fixtures/archive_cases.mjs",
  store_ref: storeRef,
  node: process.version,
  unicode: process.versions.unicode,
  zlib: process.versions.zlib,
};
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const digest = (text) => sha256(Buffer.from(text, "utf8"));
const rows = (items) => `[\n${items.map((item) => `  ${JSON.stringify(item)}`).join(",\n")}\n ]`;
const members = (held) => `{\n${Object.entries(held).map(([key, value]) => ` ${JSON.stringify(key)}: ${value}`).join(",\n")}\n}\n`;
const issuesOf = (issues) => issues.map((issue) => [issue.code, issue.path, issue.message]);

/** What went wrong: zod's issues, the code of a file system error, or the message. */
function errorOf(error) {
  if (Array.isArray(error?.issues)) return { zod: issuesOf(error.issues) };
  const name = error?.constructor?.name ?? typeof error;
  if (typeof error?.code === "string" && typeof error?.syscall === "string") return { name, code: error.code };
  return { name, message: String(error?.message ?? error) };
}
function outcome(run) {
  try {
    const returned = run();
    return { returned: returned === undefined ? null : returned };
  } catch (error) {
    return { error: errorOf(error) };
  }
}
async function outcomeLater(run) {
  try {
    const returned = await run();
    return { returned: returned === undefined ? null : returned };
  } catch (error) {
    return { error: errorOf(error) };
  }
}
/** What a schema says of a value: what it read, or its issues in its order. */
const verdict = (held, value) => {
  const result = held.safeParse(value);
  return result.success ? { parsed: result.data } : { issues: issuesOf(result.error.issues) };
};

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

// ---------------------------------------------------------------------------------------
// The pinned checkout.

const here = ".";
const lines = [];
const counts = {};
const push = (kind, ...held) => {
  lines.push(JSON.stringify([kind, ...held]));
  counts[kind] = (counts[kind] ?? 0) + 1;
};
const readLines = (file) => fs.readFileSync(path.join(store, file), "utf8").split("\n");

const map = licenceMap.readLicenceMap(here);
for (const [licence, held] of map) push("licence", licence, held);

const licences = licenceMap.readSourceLicences(here);
const work = worklist.readWorkList(here);
const items = new Map(work.items.map((item) => [item.source_id, item]));
const withoutUrl = new Map(work.withoutUrl.map((item) => [item.source_id, item]));
const queue = triage.buildTriage(here);
const queued = new Map(queue.map((held) => [held.source_id, held]));
for (const id of licences.keys())
  push("source", id, items.get(id) ?? null, withoutUrl.get(id) ?? null, licenceMap.determineForSource(id, map, licences), queued.get(id) ?? null);

const candidates = resolve.readCandidates(here);
const classify = licenceMap.licenceClassifier(here);
const manifestRows = { accepted: 0, refused: 0 };
for (const shard of schema.objectShards) {
  const file = `data/archive/objects/${shard}.jsonl`;
  if (!fs.existsSync(path.join(store, file))) continue;
  for (const [index, text] of readLines(file).entries()) {
    if (!text.trim()) continue;
    const given = JSON.parse(text);
    const result = schema.manifestRowSchema.safeParse(given);
    if (!result.success) {
      manifestRows.refused += 1;
      push("row", shard, index + 1, given.sha256 ?? null, { issues: issuesOf(result.error.issues) });
      continue;
    }
    manifestRows.accepted += 1;
    const data = result.data;
    const reading = reclassify.readRow({ sourceIds: data.source_ids, map, licences, candidates });
    push(
      "row", shard, index + 1, data.sha256, digest(schema.canonicalLine(data)),
      classify({ sourceIds: data.source_ids, url: data.origin, mediaType: data.media_type }),
      [reading.archive_tier, reading.observed_licence, reading.evidenced, reading.sources.map((source) => source.evidence)],
    );
  }
}

/** Every line of a line file held to a schema: the verdict, and the digest of the line written back. */
function heldTo(kind, file, against, more = () => []) {
  const tally = { accepted: 0, refused: 0 };
  for (const [index, text] of readLines(file).entries()) {
    if (!text.trim()) continue;
    const result = against.safeParse(JSON.parse(text));
    if (!result.success) {
      tally.refused += 1;
      push(kind, file, index + 1, { issues: issuesOf(result.error.issues) });
      continue;
    }
    tally.accepted += 1;
    push(kind, file, index + 1, digest(schema.canonicalLine(result.data)), ...more(result.data));
  }
  return tally;
}
const audits = fs.readdirSync(path.join(store, "data/omics/audits")).sort();
const fetchFiles = audits.filter((name) => /^\d{4}-\d{2}-\d{2}-archive-fetch\.receipts\.jsonl$/.test(name)).map((name) => `data/omics/audits/${name}`);
const resolveFiles = audits.filter((name) => /^\d{4}-\d{2}-\d{2}-licence-resolve\.receipts\.jsonl$/.test(name)).map((name) => `data/omics/audits/${name}`);
const fetchReceipts = fetchFiles.map((file) => heldTo("receipt", file, schema.fetchReceiptSchema));
const resolveReceipts = resolveFiles.map((file) => heldTo("resolve-receipt", file, resolve.resolveReceiptSchema));
/** A candidate built again from its own observations, against the map as it stands. */
const rebuilt = (data) => {
  const again = outcome(() =>
    resolve.buildCandidate({
      target: { source_id: data.source_id, url: "", host: "", doi: null, recorded_licence: data.recorded_licence, recorded_tier: data.recorded_tier, keys: {} },
      observations: data.observations,
      map,
    }),
  );
  return [data.source_id, again.error ? again : again.returned === null ? null : digest(schema.canonicalLine(again.returned))];
};
const candidateRows = fs.existsSync(path.join(store, "data/archive/licence-candidates.jsonl")) ? heldTo("candidate", "data/archive/licence-candidates.jsonl", resolve.candidateSchema, rebuilt) : { accepted: 0, refused: 0 };

const latest = receipts.latestBySource(receipts.readReceipts(here));
for (const [id, receipt] of latest) push("latest", id, receipt.fetched_at, receipt.outcome, receipt.reason, receipt.sha256);

// The triage queue, under the date of the newest queue the data repository commits.
const reports = fs.existsSync(path.join(store, "maintenance/reports")) ? fs.readdirSync(path.join(store, "maintenance/reports")).sort() : [];
const queues = reports.filter((name) => /^licence-triage-\d{4}-\d{2}-\d{2}\.tsv$/.test(name));
if (!queues.length) throw new Error("the checkout commits no triage queue to take a date from");
const triageDate = queues.at(-1).slice("licence-triage-".length, -".tsv".length);
const rendered = triage.render(queue, triageDate);
for (const text of rendered.split("\n").slice(0, -1)) push("triage", text);
const committedQueues = queues.filter((name) => fs.readFileSync(path.join(store, "maintenance/reports", name), "utf8") === rendered).map((name) => `maintenance/reports/${name}`);

const content = lines.map((text) => `${text}\n`).join("");
const data = Buffer.from(content, "utf8");
const dumpName = "store-archive.jsonl.gz";
const dumpFile = path.join(repo, "scripts/expected", dumpName);
const before = fs.existsSync(dumpFile) ? zlib.gunzipSync(fs.readFileSync(dumpFile)) : null;
if (before === null || !before.equals(data)) fs.writeFileSync(dumpFile, zlib.gzipSync(data, { level: 9 }));

// ---------------------------------------------------------------------------------------
// The commands. Each is a script that runs when it is loaded, so each is its own process.

const tsx = path.join(store, "node_modules/.bin/tsx");
const script = (argv, root) => {
  const [group, command, ...rest] = argv;
  if (group === "archive") return ["scripts/archive/cli.ts", ...(command === undefined ? [] : [command]), ...rest, ...(root === null ? [] : ["--root", root])];
  if (command === "triage") return ["scripts/licence/triage.ts", ...rest, ...(root === null ? [] : ["--root", root])];
  if (command === "counts") return ["scripts/licence/counts.ts", ...(root === null ? [] : [root])];
  throw new Error(`no such command: ${argv.join(" ")}`);
};
/** Paths of this runner are not part of what a command says. */
const placed = (text, cwd) => text.split(cwd).join("<work>").split(store).join("<store>");
function command(argv, cwd, root, env = {}) {
  const [file, ...rest] = script(argv, root);
  const cleaned = { ...process.env };
  delete cleaned.ARCHIVE_CACHE;
  const ran = spawnSync(tsx, [path.join(store, file), ...rest], { cwd, env: { ...cleaned, ...env }, encoding: "utf8", maxBuffer: 1 << 28 });
  if (ran.error) throw ran.error;
  return { argv, ...(Object.keys(env).length ? { env } : {}), status: ran.status, stdout: placed(ran.stdout, cwd), stderr: placed(ran.stderr, cwd) };
}
/** At the pin a long answer is kept as its digest, and the lines of it are in the dump. */
const pinned = (argv, whole = true) => {
  const ran = command(argv, store, null);
  return whole ? ran : { argv: ran.argv, status: ran.status, stdout_sha256: digest(ran.stdout), stdout_lines: ran.stdout.split("\n").length - 1, stderr: ran.stderr };
};
const pinnedCommands = [
  pinned(["archive", "worklist"]),
  pinned(["archive", "worklist", "--json"]),
  pinned(["archive", "status"]),
  pinned(["archive", "status", "--json"]),
  pinned(["archive", "verify"], false),
  pinned(["archive", "reclassify", "--dry-run"]),
  pinned(["archive", "reclassify", "--dry-run", "--json"], false),
  pinned(["licence", "triage", "--stdout", "--date", triageDate], false),
  pinned(["licence", "counts"]),
];
const verification = manifest.verifyObjects(here);
const plan = reclassify.planReclassify(here);
if (dirty() !== dirtyBefore) throw new Error("recording the pinned checkout wrote to it");

fs.writeFileSync(
  path.join(repo, "scripts/expected/store-archive.json"),
  members({
    recorded_from: JSON.stringify(recordedFrom),
    modules: rows(MODULES.map((file) => ({ file, sha256: sha256(fs.readFileSync(path.join(store, file))) }))),
    counts: JSON.stringify({
      licences: map.size,
      sources: licences.size,
      with_url: work.items.length,
      without_url: work.withoutUrl.length,
      manifest_rows: manifestRows.accepted,
      manifest_rows_refused: manifestRows.refused,
      receipts: fetchReceipts.reduce((sum, tally) => sum + tally.accepted, 0),
      receipts_refused: fetchReceipts.reduce((sum, tally) => sum + tally.refused, 0),
      resolve_receipts: resolveReceipts.reduce((sum, tally) => sum + tally.accepted, 0),
      resolve_receipts_refused: resolveReceipts.reduce((sum, tally) => sum + tally.refused, 0),
      candidates: candidateRows.accepted,
      candidates_refused: candidateRows.refused,
      latest: latest.size,
      triage_rows: queue.length,
    }),
    files: JSON.stringify({ receipts: fetchFiles, resolve_receipts: resolveFiles }),
    dump: JSON.stringify({ file: dumpName, lines: lines.length, sha256: sha256(data), kinds: counts }),
    triage: JSON.stringify({ date: triageDate, sha256: digest(rendered), queues: queues.map((name) => `maintenance/reports/${name}`), committed: committedQueues }),
    summary: JSON.stringify(worklist.summarise(here)),
    verification: JSON.stringify({
      objects: verification.objects,
      verified: verification.verified,
      removed: verification.removed.length,
      missing: verification.missing.length,
      corrupt: verification.corrupt.length,
      unlisted: verification.unlisted.length,
      problems: verification.problems,
    }),
    plan: JSON.stringify(plan),
    commands: rows(pinnedCommands),
  }),
);

// ---------------------------------------------------------------------------------------
// The scenarios.

const binary = (file) => file.endsWith(".gz") || file.includes("/cache/") || file.includes(".gz.");
function tree(base) {
  const found = {};
  const walk = (dir) => {
    for (const entry of fs.readdirSync(path.join(base, dir), { withFileTypes: true })) {
      const file = dir ? `${dir}/${entry.name}` : entry.name;
      if (entry.isDirectory()) walk(file);
      else found[file] = binary(file) ? { hex: fs.readFileSync(path.join(base, file)).toString("hex") } : fs.readFileSync(path.join(base, file), "utf8");
    }
  };
  walk("");
  return found;
}
/** The files one tree holds that another does not hold as they are, and null for a file gone. */
function changed(from, to) {
  const out = {};
  for (const [file, text] of Object.entries(to)) if (JSON.stringify(from[file]) !== JSON.stringify(text)) out[file] = text;
  for (const file of Object.keys(from)) if (!(file in to)) out[file] = null;
  return out;
}
function write(base, files) {
  for (const [file, text] of Object.entries(files)) {
    const target = path.join(base, ROOT, file);
    if (text === null) fs.rmSync(target, { force: true });
    else {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, typeof text === "string" ? text : Buffer.from(text.hex, "hex"));
    }
  }
}
function layOut(scenario) {
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "rewire-evidence-archive-")));
  fs.mkdirSync(path.join(base, ROOT));
  write(base, scenario.files);
  write(base, Object.fromEntries(Object.entries(scenario.cache ?? {}).map(([file, hex]) => [file, { hex }])));
  return base;
}

const entries = (held) => [...held];
const readings = () => ({ map: licenceMap.readLicenceMap(ROOT), licences: licenceMap.readSourceLicences(ROOT), candidates: resolve.readCandidates(ROOT) });
const calls = {
  readShard: (shard) => manifest.readShard(shard, ROOT),
  readManifest: () => entries(manifest.readManifest(ROOT)),
  checkManifest: () => manifest.checkManifest(ROOT),
  verifyObjects: () => manifest.verifyObjects(ROOT),
  upsertRow: (row) => manifest.upsertRow(row, ROOT),
  updateRow: (key, patch) => manifest.updateRow(key, patch, ROOT),
  markHome: (key, home) => manifest.markHome(key, home, ROOT),
  markMirror: (key, mirror) => manifest.markMirror(key, mirror, ROOT),
  markRemoved: (key, removal) => manifest.markRemoved(key, { ...removal, removed_at: removal.removed_at ?? undefined }, ROOT),
  objectPath: (key) => objects.objectPath(key, ROOT),
  archiveAssetName: (key) => objects.archiveAssetName(key),
  hasObject: (key) => objects.hasObject(key, ROOT),
  readObject: (key) => objects.readObject(key, ROOT).toString("hex"),
  cacheContents: () => objects.cacheContents(ROOT),
  readReceipts: () => receipts.readReceipts(ROOT),
  latestBySource: () => entries(receipts.latestBySource(receipts.readReceipts(ROOT))),
  receiptPath: (date) => receipts.receiptPath(date, ROOT),
  appendReceipt: (receipt) => receipts.appendReceipt(receipt, ROOT),
  readWorkList: () => worklist.readWorkList(ROOT),
  groupByUrl: () => entries(worklist.groupByUrl(worklist.readWorkList(ROOT).items)),
  summarise: () => worklist.summarise(ROOT),
  readLicenceMap: () => entries(licenceMap.readLicenceMap(ROOT)),
  readSourceLicences: () => entries(licenceMap.readSourceLicences(ROOT)),
  determineForSource: (id) => licenceMap.determineForSource(id, licenceMap.readLicenceMap(ROOT), licenceMap.readSourceLicences(ROOT)),
  licenceClassifier: (sourceIds) => licenceMap.licenceClassifier(ROOT)({ sourceIds, url: "", mediaType: "" }),
  buildTriage: () => triage.buildTriage(ROOT),
  renderTriage: (date) => triage.render(triage.buildTriage(ROOT), date),
  readCandidates: () => entries(resolve.readCandidates(ROOT)),
  readSource: (sourceId) => reclassify.readSource({ sourceId, ...readings() }),
  readRow: (sourceIds) => reclassify.readRow({ sourceIds, ...readings() }),
  planReclassify: () => reclassify.planReclassify(ROOT),
  reclassifyObjects: async (options) => {
    const log = [];
    const summary = await reclassify.reclassifyObjects({ ...(options ?? {}), root: ROOT, log: (text) => log.push(text) });
    return { summary, log };
  },
};

// A scenario the recorder itself fails on is reported at the end, after the file is written,
// so that one run shows every such scenario.
const failures = [];
const recorded = [];
for (const scenario of cases.scenarios) {
  try {
    recorded.push(await record(scenario));
  } catch (error) {
    process.chdir(store);
    globalThis.Date = RealDate;
    failures.push(`${scenario.name}: ${error?.stack ?? error}`);
  }
}
async function record(scenario) {
  const base = layOut(scenario);
  const steps = [];
  let before = tree(base);
  process.chdir(base);
  for (const step of scenario.steps) {
    if (!Array.isArray(step)) {
      write(base, step.write);
      before = tree(base);
      steps.push(step);
      continue;
    }
    const [name, ...given] = step;
    if (!calls[name]) throw new Error(`${scenario.name}: no such call: ${name}`);
    globalThis.Date = StoppedDate;
    const result = await outcomeLater(() => calls[name](...given));
    globalThis.Date = RealDate;
    const after = tree(base);
    const left = changed(before, after);
    before = after;
    steps.push({ call: step, ...result, ...(Object.keys(left).length ? { changed: Object.fromEntries(Object.entries(left).map(([file, text]) => [file.slice(ROOT.length + 1), text])) } : {}) });
  }
  process.chdir(store);
  fs.rmSync(base, { recursive: true, force: true });

  const ran = [];
  if (scenario.commands?.length) {
    const fresh = layOut(scenario);
    let held = tree(fresh);
    for (const { argv, env } of scenario.commands) {
      const result = command(argv, fresh, ROOT, env);
      const after = tree(fresh);
      const left = changed(held, after);
      held = after;
      ran.push({ ...result, ...(Object.keys(left).length ? { changed: left } : {}) });
    }
    fs.rmSync(fresh, { recursive: true, force: true });
  }
  return { name: scenario.name, files: scenario.files, cache: scenario.cache ?? {}, steps, commands: ran };
}

// ---------------------------------------------------------------------------------------
// The schemas and the small functions.

const hostOf = (text) => {
  try {
    const url = new URL(text);
    return { host: url.host.toLowerCase(), hostname: url.hostname.toLowerCase() };
  } catch {
    return null;
  }
};
const mapOf = (held) => new Map(held.map(([licence, family, tier, basis, note]) => [licence, { licence, family, archive_tier: tier, basis, note }]));

fs.writeFileSync(
  path.join(repo, "tests/fixtures/archive.json"),
  members({
    recorded_from: JSON.stringify(recordedFrom),
    clock: JSON.stringify(CLOCK),
    root: JSON.stringify(ROOT),
    constants: JSON.stringify({
      layers: schema.layers,
      stores: schema.stores,
      visibilities: schema.visibilities,
      archive_tiers: schema.archiveTiers,
      exposures: schema.exposures,
      routes: schema.routes,
      reasons: schema.reasons,
      outcomes: schema.outcomes,
      source_classes: schema.sourceClasses,
      default_archive_tier: schema.defaultArchiveTier,
      object_shards: schema.objectShards,
      resolve_routes: resolve.resolveRoutes,
      resolve_reasons: resolve.resolveReasons,
      candidate_statuses: resolve.candidateStatuses,
      pre_policy_source_ids: licenceMap.prePolicySourceIds,
      columns: licenceMap.columns,
      repository: release.repository,
    }),
    rows: rows(cases.rows.map(({ name, row }) => ({ name, given: row, ...verdict(schema.manifestRowSchema, row) }))),
    receipts: rows(cases.receipts.map(({ name, receipt }) => ({ name, given: receipt, ...verdict(schema.fetchReceiptSchema, receipt) }))),
    candidates: rows(cases.candidates.map(({ name, candidate }) => ({ name, given: candidate, ...verdict(resolve.candidateSchema, candidate) }))),
    resolve_receipts: rows(cases.resolveReceipts.map(({ name, receipt }) => ({ name, given: receipt, ...verdict(resolve.resolveReceiptSchema, receipt) }))),
    visibilities: rows(cases.tiers.map((tier) => [tier, schema.visibilityOf(tier)])),
    stricter: rows(cases.tiers.flatMap((a) => cases.tiers.map((b) => [a, b, licenceMap.stricterTier(a, b)]))),
    removed: rows([{}, { removed_at: "2026-11-20T10:00:00Z" }, { removed_at: "" }].map((given) => [given, schema.isRemoved(given)])),
    object_shards: rows(cases.shardKeys.map((given) => ({ given, ...outcome(() => schema.objectShard(given)) }))),
    shard_paths: rows(cases.shardNames.map((given) => ({ given, ...outcome(() => manifest.shardPath(given, ROOT)) }))),
    receipt_paths: rows(cases.dates.map((given) => ({ given, ...outcome(() => receipts.receiptPath(given, ROOT)) }))),
    asset_urls: rows(cases.assetNames.map(([name, shard]) => ({ name, shard, ...outcome(() => objects.archiveAssetUrl(shard, name)) }))),
    media_types: rows(cases.mediaTypes.map((given) => [given, schema.mediaTypeOf(given)])),
    canonical_lines: rows(cases.canonicalLines.map((given) => [JSON.stringify(given), schema.canonicalLine(given)])),
    clocks: rows(["2026-11-21T12:34:56.789Z", "2026-01-02T03:04:05.000Z", "1999-12-31T23:59:59.999Z"].map((given) => [given, schema.utcNow(new Date(given))])),
    source_classes: rows(cases.classHosts.map((given) => [given, worklist.sourceClassOf(given)])),
    urls: rows(cases.urls.map((given) => [given, hostOf(given)])),
    gunzips: rows(cases.gunzips.map((given) => {
      const result = outcome(() => release.expand(Buffer.from(given.hex, "hex"), given.digest, "the file", given.bytes));
      return { ...given, ...(result.error ? result : { returned: result.returned.toString("hex") }) };
    })),
    built_map: rows(cases.builtMap),
    built: rows(cases.built.map(({ name, target, observations }) => ({ name, target, observations, ...outcome(() => resolve.buildCandidate({ target, observations, map: mapOf(cases.builtMap) })) }))),
    scenarios: rows(recorded),
  }),
);
console.log(
  `Recorded ${lines.length} lines of the pinned checkout at ${storeRef}: ${manifestRows.accepted} manifest rows, ${counts.receipt ?? 0} receipts, ${counts.candidate ?? 0} candidates, `
  + `${licences.size} sources and ${queue.length} rows of the triage queue. ${cases.rows.length} rows, ${cases.receipts.length} receipts and ${cases.candidates.length} candidates held to the schemas, `
  + `${cases.urls.length} URLs, ${cases.gunzips.length} gzip files, and ${recorded.length} scenarios of ${recorded.reduce((sum, held) => sum + held.steps.length, 0)} steps and ${recorded.reduce((sum, held) => sum + held.commands.length, 0)} commands.`,
);
if (failures.length) {
  for (const failure of failures) console.error(failure);
  process.exitCode = 1;
}
