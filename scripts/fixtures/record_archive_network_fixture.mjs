// Record what the TypeScript archive network modules and licence resolver do, so the
// Python port (P14) is held to the exact TypeScript implementation and produces byte-identical
// outputs with zero live network calls.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/record_archive_network_fixture.mjs
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const MODULES = [
  "scripts/archive/fetch.ts",
  "scripts/archive/routes.ts",
  "scripts/archive/upload.ts",
  "scripts/archive/pull.ts",
  "scripts/archive/publish.ts",
  "scripts/archive/r2.ts",
  "scripts/archive/objects.ts",
  "scripts/archive/cli.ts",
  "shared/archive/artifact.ts",
  "shared/archive/layers/store.ts",
  "scripts/licence/resolve.ts",
];
const FILES_2026_11_17 = [
  "data/archive/licence-candidates.jsonl",
  "data/omics/audits/2026-11-17-licence-resolve.receipts.jsonl",
  "maintenance/reports/licence-resolve-2026-11-17.tsv",
  "maintenance/reports/licence-resolve-2026-11-17.md",
];
const CLOCK = "2026-11-21T12:34:56.789Z";

const load = async (file) => {
  const loaded = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(loaded.default ?? {}), ...loaded };
};

const schema = await load("shared/archive/schema.ts");
const artifact = await load("shared/archive/artifact.ts");
const layerStore = await load("shared/archive/layers/store.ts");
const routes = await load("scripts/archive/routes.ts");
const archiveFetch = await load("scripts/archive/fetch.ts");
const manifest = await load("scripts/archive/manifest.ts");
const objects = await load("scripts/archive/objects.ts");
const r2 = await load("scripts/archive/r2.ts");
const upload = await load("scripts/archive/upload.ts");
const pull = await load("scripts/archive/pull.ts");
const publish = await load("scripts/archive/publish.ts");
const resolve = await load("scripts/licence/resolve.ts");

const storeRef = execFileSync("git", ["rev-parse", "HEAD"], { cwd: store, encoding: "utf8" }).trim();
const dirty = () => execFileSync("git", ["status", "--porcelain"], { cwd: store, encoding: "utf8" });
const dirtyBefore = dirty();

const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const sha256Text = (text) => sha256(Buffer.from(text, "utf8"));

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
// 1. Pinned store expectations (scripts/expected/store-archive-network.json)

const targets = resolve.readTargets(".");
const targetsLines = targets.map((t) => `${JSON.stringify(t)}\n`).join("");

const storeExpected = {
  recorded_from: {
    script: "scripts/fixtures/record_archive_network_fixture.mjs",
    store_ref: storeRef,
  },
  modules: MODULES.map((file) => ({ file, sha256: sha256(fs.readFileSync(path.join(store, file))) })),
  commit_2026_11_17: FILES_2026_11_17.map((file) => ({
    file,
    sha256: sha256(fs.readFileSync(path.join(store, file))),
  })),
  targets: {
    count: targets.length,
    sha256: sha256Text(targetsLines),
  },
};

fs.writeFileSync(
  path.join(repo, "scripts/expected/store-archive-network.json"),
  `${JSON.stringify(storeExpected, null, 2)}\n`,
);

// ---------------------------------------------------------------------------------------
// 2. Unit & offline scenario fixture (tests/fixtures/archive-network.json)

function makeStoredZip(entries) {
  const crcTable = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crcTable[n] = c;
  }
  const crc32 = (buf) => {
    let c = 0xffffffff;
    for (const byte of buf) c = crcTable[(c ^ byte) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const { name, data } of entries) {
    const nameBuf = Buffer.from(name, "utf8");
    const crc = crc32(data);
    const local = Buffer.alloc(30 + nameBuf.length + data.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(0, 10);
    local.writeUInt16LE(0, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    local.writeUInt16LE(0, 28);
    nameBuf.copy(local, 30);
    data.copy(local, 30 + nameBuf.length);

    const central = Buffer.alloc(46 + nameBuf.length);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt16LE(0, 12);
    central.writeUInt16LE(0, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuf.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE(0, 38);
    central.writeUInt32LE(offset, 42);
    nameBuf.copy(central, 46);

    locals.push(local);
    centrals.push(central);
    offset += local.length;
  }
  const cdSize = centrals.reduce((sum, b) => sum + b.length, 0);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(cdSize, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, ...centrals, eocd]);
}

const canonicalCases = [
  "https://example.org/file.pdf",
  "not a url",
  "https://github.com/org/repo",
  "https://github.com/org/repo/tree/main/dir",
  "https://github.com/org/repo/blob/abc123/path/to/file.py",
  "https://github.com/org/repo/blob/main/a/b.tsv?plain=1",
  "https://www.github.com/org/repo/blob/main/a.txt",
].map((u) => [u, routes.canonicalGithubUrl(u)]);

const cookieShort = `<html><body><p>We use cookies on this site.</p><p>Short title only.</p></body></html>`;
const cookieGenuine = `<html><body><p>We use cookies on this site.</p><p>${"Long genuine scientific text word ".repeat(120)}</p></body></html>`;
const humanVerify = `<html><body><h1>Just a moment</h1><p>Verify you are human before continuing.</p></body></html>`;
const publisherHtml = `<html><body><main>${"Scientific article body paragraph ".repeat(80)}</main></body></html>`;
const tinyHtml = `<html><body><p>Tiny stub.</p></body></html>`;
const sampleText = `${"Observation measurement value ".repeat(10)}`;
const samplePdfHeader = "%PDF-1.4\n%fake";

const artifactCases = [
  { name: "unknown_empty", body: Buffer.alloc(0), media_type: null, final_url: "https://example.org/x", requested_url: null, has_doi: false, pdftotext: false },
  { name: "zip_container", body: makeStoredZip([{ name: "a.txt", data: Buffer.from("hello world") }]), media_type: "application/zip", final_url: "https://example.org/a.zip", requested_url: null, has_doi: false, pdftotext: false },
  { name: "short_cookie_wall", body: Buffer.from(cookieShort), media_type: "text/html", final_url: "https://link.springer.com/article/10.1/x", requested_url: null, has_doi: true, pdftotext: false },
  { name: "long_with_cookie_notice", body: Buffer.from(cookieGenuine), media_type: "text/html", final_url: "https://github.com/org/repo", requested_url: null, has_doi: false, pdftotext: false },
  { name: "human_verify_teaser", body: Buffer.from(humanVerify), media_type: "text/html", final_url: "https://example.org/paper", requested_url: null, has_doi: false, pdftotext: false },
  { name: "stop_path_login", body: Buffer.from(cookieGenuine), media_type: "text/html", final_url: "https://idp.example.org/auth/login?next=1", requested_url: null, has_doi: false, pdftotext: false },
  { name: "tiny_html_stub", body: Buffer.from(tinyHtml), media_type: "text/html", final_url: "https://example.org/stub", requested_url: null, has_doi: false, pdftotext: false },
  { name: "publisher_html_with_doi", body: Buffer.from(publisherHtml), media_type: "text/html", final_url: "https://www.nature.com/articles/s41586", requested_url: null, has_doi: true, pdftotext: false },
  { name: "publisher_redirect_with_doi", body: Buffer.from(publisherHtml), media_type: "text/html", final_url: "https://cdn.example.net/render", requested_url: "https://www.nature.com/articles/s41586", has_doi: true, pdftotext: false },
  { name: "publisher_html_no_doi", body: Buffer.from(publisherHtml), media_type: "text/html", final_url: "https://www.nature.com/articles/s41586", requested_url: null, has_doi: false, pdftotext: false },
  { name: "pdf_no_pdftotext", body: Buffer.from(samplePdfHeader), media_type: "application/pdf", final_url: "https://example.org/paper.pdf", requested_url: null, has_doi: true, pdftotext: false },
  { name: "plain_text", body: Buffer.from(sampleText), media_type: "text/plain", final_url: "https://example.org/notes.txt", requested_url: null, has_doi: false, pdftotext: false },
].map((c) => {
  const res = artifact.checkArtifact({
    bytes: c.body,
    mediaType: c.media_type,
    finalUrl: c.final_url,
    requestedUrl: c.requested_url ?? undefined,
    hasDoi: c.has_doi,
    pdftotext: c.pdftotext,
  });
  return {
    name: c.name,
    body_b64: c.body.toString("base64"),
    media_type: c.media_type,
    final_url: c.final_url,
    requested_url: c.requested_url,
    has_doi: c.has_doi,
    pdftotext: c.pdftotext,
    expected: {
      terminal: res.terminal,
      reason: res.reason,
      layer_class: res.layerClass,
      characters: res.characters,
      signals: res.signals,
    },
  };
});

const failureReasonCases = [
  [
    "10.1000/a",
    [
      { route: "direct", url: "https://p.example/a", status: 403, reason: "http_error" },
      { route: "unpaywall", url: null, status: null, reason: "no_open_copy" },
      { route: "europepmc", url: null, status: null, reason: "not_found" },
      { route: "preprint", url: "https://biorxiv.org/a.pdf", status: 404, reason: "not_found" },
    ],
  ],
  [
    "10.1000/b",
    [
      { route: "direct", url: "https://p.example/b", status: 403, reason: "http_error" },
      { route: "unpaywall", url: null, status: null, reason: "unpaywall_no_email" },
      { route: "europepmc", url: null, status: null, reason: "no_open_copy" },
      { route: "preprint", url: null, status: null, reason: "no_open_copy" },
    ],
  ],
  [
    null,
    [
      { route: "direct", url: "https://p.example/c", status: 500, reason: "http_error" },
      { route: "unpaywall", url: null, status: null, reason: "no_doi" },
      { route: "europepmc", url: null, status: null, reason: "no_doi" },
      { route: "preprint", url: null, status: null, reason: "no_doi" },
    ],
  ],
].map(([doi, tried]) => ({ doi, tried, expected: archiveFetch.failureReason(doi, tried) }));

const r2Settings = { accountId: "acc123", accessKeyId: "key456", secretAccessKey: "secret789", bucket: "bkt" };
const r2SignTime = "2026-11-21T12:34:56.000Z";
const r2Signatures = [
  ["PUT", "abcd.gz", "hello bytes"],
  ["HEAD", "abcd.gz", ""],
  ["GET", "ef01.gz", ""],
].map(([method, key, text]) => {
  const signed = r2.signRequest(r2Settings, method, key, Buffer.from(text, "utf8"), new RealDate(r2SignTime));
  return { method, key, body: text, timestamp: r2SignTime, settings: r2Settings, url: signed.url, headers: signed.headers };
});

const routeKeySamples = [
  ["https://raw.githubusercontent.com/org/repo/0123456789abcdef0123456789abcdef01234567/README.md", null],
  ["https://codeload.github.com/org/repo/tar.gz/0123456789abcdef0123456789abcdef01234567", null],
  ["https://api.github.com/repos/org/repo/license?ref=0123456789abcdef0123456789abcdef01234567", null],
  ["https://github.com/org/repo.git", "10.1038/s41586-024-00001-1"],
  ["https://huggingface.co/datasets/org/ds/blob/fedcba9876543210fedcba9876543210fedcba98/data.tsv", null],
  ["https://www.biorxiv.org/content/10.1101/2025.01.02.123456v2.full.pdf", "10.1101/2025.01.02.123456"],
  ["https://www.ebi.ac.uk/europepmc/webservices/rest/PMC9999999/fullTextXML", "10.1093/bioinformatics/btaf417"],
].map(([url, doi]) => ({ url, doi, expected: resolve.routeKeysFor({ url, doi }) }));

// Offline scenarios run with frozen clock and mocked globals
const realFetch = globalThis.fetch;
const realDate = globalThis.Date;
const heldEnv = {
  ARCHIVE_CACHE: process.env.ARCHIVE_CACHE,
  GITHUB_TOKEN: process.env.GITHUB_TOKEN,
  GH_TOKEN: process.env.GH_TOKEN,
  UNPAYWALL_EMAIL: process.env.UNPAYWALL_EMAIL,
};
const tempDirs = [];
const mkTmp = (prefix) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
};

let scenarios;
try {
  globalThis.Date = StoppedDate;
  delete process.env.ARCHIVE_CACHE;
  process.env.GITHUB_TOKEN = "tok";
  delete process.env.GH_TOKEN;
  process.env.UNPAYWALL_EMAIL = "contact@example.org";

  // Scenario A: storeLayers
  const layerRoot = mkTmp("an-layers-");
  const zipPayload = makeStoredZip([
    { name: "note.txt", data: Buffer.from("plain note content") },
    { name: "page.html", data: Buffer.from("<html><body><h1>Title</h1><p>Body paragraph here.</p></body></html>") },
  ]);
  const { key: zipKey } = objects.putObject(zipPayload, layerRoot);
  const parentRow = schema.manifestRowSchema.parse({
    sha256: zipKey,
    bytes: zipPayload.length,
    media_type: "application/zip",
    layer: "exact-bytes",
    origin: "https://example.org/pack.zip",
    requested_url: null,
    first_seen_at: "2026-11-21T12:34:56Z",
    store: "local-cache",
    mirrors: [],
    visibility: "public",
    archive_tier: "t0-public",
    observed_licence: "MIT",
    exposure: "policy",
    derived_from: null,
    derivation: null,
    previous_path: null,
    source_ids: ["src-zip"],
  });
  await manifest.upsertRow(parentRow, layerRoot);
  const storedLayers = await layerStore.storeLayers(parentRow, zipPayload, layerRoot, { pdftotext: false });

  // Scenario B: pullObjects with fixed pre-compressed bytes (base64 encoded here so zlib version never shifts it!)
  // Valid gzip of b"hello pull bytes" generated offline, with level=1 header (0x04 os/xfl)
  const helloBytes = Buffer.from("hello pull bytes", "utf8");
  const helloSha = sha256(helloBytes);
  const fixedGzipB64 = "H4sIAAAAAAAEE8tIzcnJVygozclRSKosSS0GAHiCU84QAAAA";
  const fixedGzip = Buffer.from(fixedGzipB64, "base64");
  const pullRoot = mkTmp("an-pull-");
  const rowBucketOnly = schema.manifestRowSchema.parse({
    sha256: helloSha,
    bytes: helloBytes.length,
    media_type: "text/plain",
    layer: "exact-bytes",
    origin: "https://example.org/hello.txt",
    requested_url: null,
    first_seen_at: "2026-11-21T12:34:56Z",
    store: "r2",
    mirrors: [],
    visibility: "restricted",
    archive_tier: "t4-private",
    observed_licence: null,
    exposure: "policy",
    derived_from: null,
    derivation: null,
    previous_path: null,
    source_ids: ["s1"],
  });
  const missingPublicBytes = Buffer.from("no remote copy", "utf8");
  const rowLocalOnly = schema.manifestRowSchema.parse({
    ...rowBucketOnly,
    sha256: sha256(missingPublicBytes),
    bytes: missingPublicBytes.length,
    store: "local-cache",
    visibility: "public",
    archive_tier: "t0-public",
    source_ids: ["s2"],
  });
  const removedBytes = Buffer.from("removed bytes", "utf8");
  const rowRemoved = schema.manifestRowSchema.parse({
    ...rowBucketOnly,
    sha256: sha256(removedBytes),
    bytes: removedBytes.length,
    visibility: "restricted",
    archive_tier: "t5-no-store",
    removed_at: "2026-11-20T00:00:00Z",
    removed_reason: "takedown",
    removed_requester: "owner",
    source_ids: ["s3"],
  });
  await manifest.upsertRow(rowBucketOnly, pullRoot);
  await manifest.upsertRow(rowLocalOnly, pullRoot);
  await manifest.upsertRow(rowRemoved, pullRoot);

  const releaseDry = await pull.pullObjects({
    root: pullRoot,
    from: "release",
    dryRun: true,
    fetch: async () => ({ status: 404, arrayBuffer: async () => new ArrayBuffer(0), text: async () => "" }),
  });
  const pullReal = await pull.pullObjects({
    root: pullRoot,
    env: { R2_ACCOUNT_ID: "acc", R2_ACCESS_KEY_ID: "key", R2_SECRET_ACCESS_KEY: "sec", R2_BUCKET: "b" },
    fetch: async () => ({
      status: 200,
      arrayBuffer: async () => fixedGzip.buffer.slice(fixedGzip.byteOffset, fixedGzip.byteOffset + fixedGzip.byteLength),
      text: async () => "",
    }),
  });
  const pulledDiskB64 = fs.readFileSync(objects.objectPath(helloSha, pullRoot)).toString("base64");

  // Scenario C: publishObjects (local-cache promoted to home, r2 row gets mirror)
  // Write fixed base64 .gz files so compressed_bytes never depends on host zlib.
  const pubRoot = mkTmp("an-pub-");
  const pubLocalBytes = Buffer.from("local public bytes", "utf8");
  const pubR2Bytes = Buffer.from("r2 public bytes", "utf8");
  const pubPrivBytes = Buffer.from("private bytes", "utf8");
  const kLocal = sha256(pubLocalBytes);
  const kR2 = sha256(pubR2Bytes);
  const kPriv = sha256(pubPrivBytes);
  const pubFixedGz = {
    [kLocal]: "H4sIAAAAAAACE8vJT07MUSgoTcrJTFZIqixJLQYA9m3DFhIAAAA=",
    [kR2]: "H4sIAAAAAAACEysyUigoTcrJTFZIqixJLQYAgyZYQQ8AAAA=",
    [kPriv]: "H4sIAAAAAAACEysoyixLLElVSKosSS0GACWgpGcNAAAA",
  };
  for (const [k, b64] of Object.entries(pubFixedGz)) {
    const dest = objects.objectPath(k, pubRoot);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, Buffer.from(b64, "base64"));
  }
  await manifest.upsertRow({ ...parentRow, sha256: kLocal, bytes: pubLocalBytes.length, store: "local-cache", source_ids: ["p1"] }, pubRoot);
  await manifest.upsertRow({ ...parentRow, sha256: kR2, bytes: pubR2Bytes.length, store: "r2", archive_tier: "t1-public-nc", source_ids: ["p2"] }, pubRoot);
  await manifest.upsertRow({ ...rowBucketOnly, sha256: kPriv, bytes: pubPrivBytes.length, store: "local-cache", source_ids: ["p3"] }, pubRoot);
  const createdTags = [];
  const uploadedAssets = [];
  const pubSummary = await publish.publishObjects({
    root: pubRoot,
    client: {
      assets: () => null,
      create: (tag) => createdTags.push(tag),
      upload: (tag, file) => uploadedAssets.push({ tag, asset: path.basename(file) }),
    },
  });
  const pubRows = Object.fromEntries(
    [...manifest.readManifest(pubRoot).entries()].sort().map(([k, v]) => [k, { store: v.store, mirrors: v.mirrors }]),
  );

  // Scenario D: uploadObjects (HEAD 200 repairs manifest, HEAD 404 + PUT 200 uploads, t5-no-store skipped)
  const upRoot = mkTmp("an-up-");
  const uRepair = objects.putObject(Buffer.from("already in r2"), upRoot);
  const uSend = objects.putObject(Buffer.from("needs upload"), upRoot);
  await manifest.upsertRow({ ...parentRow, sha256: uRepair.key, bytes: uRepair.bytes, store: "github-release", source_ids: ["u1"] }, upRoot);
  await manifest.upsertRow({ ...parentRow, sha256: uSend.key, bytes: uSend.bytes, store: "local-cache", source_ids: ["u2"] }, upRoot);
  await manifest.upsertRow(rowRemoved, upRoot);
  const upCalls = [];
  globalThis.fetch = async (url, init) => {
    const name = url.split("/").pop();
    upCalls.push(`${init.method} ${name}`);
    if (init.method === "HEAD") {
      const hit = name === `${uRepair.key}.gz`;
      return { status: hit ? 200 : 404, ok: hit };
    }
    return { status: 200, ok: true };
  };
  const upSummary = await upload.uploadObjects({
    root: upRoot,
    env: { R2_ACCOUNT_ID: "acc", R2_ACCESS_KEY_ID: "key", R2_SECRET_ACCESS_KEY: "sec", R2_BUCKET: "b" },
  });
  const upManifest = Object.fromEntries(
    [...manifest.readManifest(upRoot).entries()].sort().map(([k, v]) => [k, { store: v.store, mirrors: v.mirrors }]),
  );

  // Scenario E: runFetch (route fallback keeps europepmc XML and drops direct publisher HTML)
  const fetchRoot = mkTmp("an-fetch-");
  fs.mkdirSync(path.join(fetchRoot, "data/entities"), { recursive: true });
  fs.mkdirSync(path.join(fetchRoot, "data/archive"), { recursive: true });
  fs.writeFileSync(
    path.join(fetchRoot, "data/archive/licence-map.tsv"),
    "licence\tfamily\ttier\tbasis\tnote\nCC BY 4.0\tcc-by\tt0-public\tstandard-cc\tOK\n",
  );
  fs.writeFileSync(
    path.join(fetchRoot, "data/entities/sources.jsonl"),
    `${JSON.stringify({
      id: "src-xml-rescue",
      kind: "source",
      name: "Nature article",
      description: "Test source.",
      status: "source_checked",
      facets: { areas: ["genomics"], contexts: ["research"] },
      source_ids: [],
      links: [],
      attributes: { url: "https://www.nature.com/articles/s41586", doi: "10.1038/s41586", licence: "CC BY 4.0" },
    })}\n`,
  );
  const jatsXml = `<article><body><p>${"Genuine JATS full text body word ".repeat(60)}</p></body></article>`;
  const fetchCalls = [];
  globalThis.fetch = async (url) => {
    fetchCalls.push(url);
    if (url.startsWith("https://www.nature.com/")) return new Response(publisherHtml, { status: 200, headers: { "content-type": "text/html" } });
    if (url.startsWith("https://api.unpaywall.org/")) return new Response(JSON.stringify({ best_oa_location: null }), { status: 200 });
    if (url.includes("/search?")) return new Response(JSON.stringify({ resultList: { result: [{ pmcid: "PMC1234567", inEPMC: "Y" }] } }), { status: 200 });
    if (url.includes("/fullTextXML")) return new Response(jatsXml, { status: 200, headers: { "content-type": "application/xml" } });
    return new Response("not found", { status: 404 });
  };
  const fetchLogs = [];
  const fetchSummary = await archiveFetch.runFetch({
    root: fetchRoot,
    concurrency: 1,
    delayMs: 0,
    pdftotext: false,
    log: (line) => fetchLogs.push(line),
  });
  const fetchReceipts = fs
    .readFileSync(path.join(fetchRoot, "data/omics/audits/2026-11-21-archive-fetch.receipts.jsonl"), "utf8")
    .trim()
    .split("\n")
    .map((l) => JSON.parse(l));

  // Scenario F: runResolve + countRun + renderCandidateTable + renderReport
  const resRoot = mkTmp("an-resolve-");
  fs.mkdirSync(path.join(resRoot, "data/entities"), { recursive: true });
  fs.mkdirSync(path.join(resRoot, "data/archive"), { recursive: true });
  fs.writeFileSync(
    path.join(resRoot, "data/archive/licence-map.tsv"),
    "licence\tfamily\ttier\tbasis\tnote\nCC BY 4.0\tcc-by\tt0-public\tstandard-cc\tOK\nMIT\tpermissive-code\tt0-public\tspdx\tOK\n",
  );
  fs.writeFileSync(
    path.join(resRoot, "data/entities/sources.jsonl"),
    [
      JSON.stringify({ id: "r-gh", kind: "source", name: "GH", description: "s", status: "source_checked", facets: { areas: ["genomics"], contexts: ["research"] }, source_ids: [], links: [], attributes: { url: "https://github.com/org/repo" } }),
      JSON.stringify({ id: "r-unmapped", kind: "source", name: "HF", description: "s", status: "source_checked", facets: { areas: ["genomics"], contexts: ["research"] }, source_ids: [], links: [], attributes: { url: "https://huggingface.co/org/mod" } }),
    ].join("\n") + "\n",
  );
  const resMap = resolve.readTargets(resRoot);
  const resRun = await resolve.runResolve({
    root: resRoot,
    date: "2026-11-21",
    delayMs: 0,
    hostDelayMs: {},
    transport: async (url) => {
      if (url.includes("api.github.com")) return { status: 200, body: JSON.stringify({ license: { spdx_id: "MIT" }, name: "LICENSE" }), fetched_at: "2026-11-21T12:34:56Z" };
      return { status: 200, body: JSON.stringify({ cardData: { license: "apache-2.0" } }), fetched_at: "2026-11-21T12:34:56Z" };
    },
  });
  const resCounts = resolve.countRun(resRun, resolve.readCandidates ? (await load("scripts/licence/map.ts")).readLicenceMap(resRoot) : new Map());
  const resTsv = resolve.renderCandidateTable(resRun);
  const resMd = resolve.renderReport(resRun, resCounts);

  scenarios = {
    store_layers: {
      zip_b64: zipPayload.toString("base64"),
      stored: storedLayers.stored,
      identities: storedLayers.identities,
      unsupported: storedLayers.unsupported,
      problems: storedLayers.problems,
    },
    pull_verbatim: {
      hello_b64: helloBytes.toString("base64"),
      hello_sha256: helloSha,
      fixed_gzip_b64: fixedGzipB64,
      release_dry: releaseDry,
      pull_real: pullReal,
      disk_b64: pulledDiskB64,
      equal_verbatim: pulledDiskB64 === fixedGzipB64,
    },
    publish_marking: {
      local_key: kLocal,
      r2_key: kR2,
      priv_key: kPriv,
      summary: pubSummary,
      rows: pubRows,
      created_tags: createdTags.sort(),
      uploaded_count: uploadedAssets.length,
    },
    upload_repair: {
      repair_key: uRepair.key,
      send_key: uSend.key,
      removed_key: rowRemoved.sha256,
      summary: upSummary,
      calls: upCalls,
      manifest: upManifest,
    },
    fetch_fallback: {
      jats_xml: jatsXml,
      publisher_html: publisherHtml,
      calls: fetchCalls,
      logs: fetchLogs,
      summary: fetchSummary,
      receipts: fetchReceipts,
    },
    resolve_offline: {
      targets: resMap,
      candidates: resRun.candidates,
      receipts: resRun.receipts,
      counts: resCounts,
      tsv: resTsv,
      md: resMd,
    },
  };
} finally {
  globalThis.fetch = realFetch;
  globalThis.Date = realDate;
  for (const [k, v] of Object.entries(heldEnv)) {
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  for (const dir of tempDirs) fs.rmSync(dir, { recursive: true, force: true });
}

if (dirty() !== dirtyBefore) throw new Error("recording archive-network fixtures modified the store");

const outFixture = {
  recorded_from: {
    script: "scripts/fixtures/record_archive_network_fixture.mjs",
    store_ref: storeRef,
  },
  canonical_github_urls: canonicalCases,
  artifact_checks: artifactCases,
  failure_reasons: failureReasonCases,
  r2_signatures: r2Signatures,
  publish_refusals: {
    shard_tag_a: publish.archiveTag("a"),
    shard_notes_a: publish.shardNotes("a"),
    no_store_tier: "t5-no-store",
  },
  route_keys: routeKeySamples,
  scenarios,
};

fs.mkdirSync(path.join(repo, "tests/fixtures"), { recursive: true });
fs.writeFileSync(path.join(repo, "tests/fixtures/archive-network.json"), `${JSON.stringify(outFixture, null, 2)}\n`);
console.log(
  `Recorded archive-network fixture (${canonicalCases.length} canonical URLs, ${artifactCases.length} artifact checks, ` +
    `${targets.length} store targets, ${MODULES.length} modules).`,
);
