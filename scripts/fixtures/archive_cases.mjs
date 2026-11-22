// Cases for the archive read side and the licence map: what the committed manifest, receipts
// and candidates of the data repository have no example of. record_archive_fixture.mjs runs
// the TypeScript on each and writes what it returns or throws. Nothing here needs the data
// repository's code: the keys and the compressed objects are made with Node's built-ins.
import crypto from "node:crypto";
import zlib from "node:zlib";

const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const many = (count, text) => Array.from({ length: count }, () => text).join("");

/** A key in a named shard: the digest of the first numbered text whose digest starts with it. */
const keyIn = (shard, salt = "") => {
  for (let at = 0; ; at += 1) {
    const key = sha256(`object ${salt} ${at}`);
    if (key.startsWith(shard)) return key;
  }
};
/** An object the cache can hold: its bytes, its key and the bytes gzip makes of it. */
const held = (text) => {
  const bytes = Buffer.from(text, "utf8");
  return { key: sha256(bytes), bytes: bytes.length, gz: zlib.gzipSync(bytes, { level: 9 }).toString("hex") };
};
const cachePath = (key) => `data/archive/cache/${key.slice(0, 2)}/${key}.gz`;

/** canonicalLine, written again so a file can be laid out without the code under test. */
const line = (value) => JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))));
const lines = (...values) => values.map((value) => `${typeof value === "string" ? value : line(value)}\n`).join("");

const without = (value, ...keys) => Object.fromEntries(Object.entries(value).filter(([key]) => !keys.includes(key)));

// ---------------------------------------------------------------------------------------
// manifestRowSchema
// ---------------------------------------------------------------------------------------

const KEY = keyIn("0", "base");
const OTHER = keyIn("1", "base");
export const baseRow = {
  sha256: KEY,
  bytes: 12,
  media_type: "text/html",
  layer: "exact-bytes",
  origin: "https://example.org/a",
  requested_url: null,
  first_seen_at: "2026-11-17T16:10:11Z",
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
};
const row = (patch = {}) => ({ ...baseRow, ...patch });
const publicRow = (patch = {}) => row({ visibility: "public", archive_tier: "t0-public", ...patch });
const removal = { removed_at: "2026-11-20T10:00:00Z", removed_reason: "asked", removed_requester: "the publisher" };
const removedRow = (patch = {}) => row({ archive_tier: "t5-no-store", ...removal, ...patch });

const WRONG = [null, 5, 1.5, -1, "", " ", "x", true, false, [], ["x"], {}, { a: 1 }];
const TIERS = ["t0-public", "t1-public-nc", "t2-verbatim-nd", "t3-verbatim-nd-nc", "t4-private", "t5-no-store"];
const STORES = ["local-cache", "github-release", "r2"];
const LAYERS = ["exact-bytes", "archive-member", "text-layer", "canonical-json", "warc"];
const TIMES = [
  "2026-11-17T16:10:11Z", "2026-11-17T16:10:11.1Z", "2026-11-17T16:10:11.12Z", "2026-11-17T16:10:11.123Z", "2026-11-17T16:10:11.1234Z", "2026-11-17T16:10:11.Z",
  "2026-11-17T16:10:11", "2026-11-17T16:10:11z", "2026-11-17T16:10:11+00:00", "2026-11-17 16:10:11Z", "2026-11-17T16:10Z", "2026-11-17", "2026-13-45T99:99:99Z", "0000-00-00T00:00:00Z",
  "2026-11-17T16:10:11Z\n", "\n2026-11-17T16:10:11Z", " 2026-11-17T16:10:11Z", "2026-11-17T16:10:11Z ", "2026-11-17T16:10:1١Z", "２026-11-17T16:10:11Z", "2026-11-17T16:10:11,5Z",
  "12026-11-17T16:10:11Z", "2026-11-17T16:10:11ZZ", "2026-11-17t16:10:11Z", "",
];
const KEYS = [
  KEY, KEY.toUpperCase(), KEY.slice(1), `${KEY}0`, `${KEY}\n`, `\n${KEY}`, ` ${KEY}`, `${KEY.slice(0, 63)}g`, `${KEY.slice(0, 63)}G`, `${KEY.slice(0, 63)}١`, `${KEY.slice(0, 62)}😀`,
  many(64, "0"), many(64, "f"), many(64, "F"), "",
];

const named = (prefix, values, make) => values.map((value, at) => ({ name: `${prefix} ${at}: ${JSON.stringify(value)}`.slice(0, 96), row: make(value) }));

export const rows = [
  { name: "the base row", row: row() },
  { name: "a public row with a mirror", row: publicRow({ mirrors: ["github-release"] }) },
  // What is not a row at all.
  ...named("not an object", [null, 5, "x", true, [], [row()], ""], (value) => value),
  { name: "an empty object", row: {} },
  // Each member left out, null, and of each wrong type.
  ...Object.keys(baseRow).map((key) => ({ name: `${key} left out`, row: without(row(), key) })),
  ...Object.keys(baseRow).flatMap((key) => named(`${key} as`, WRONG, (value) => row({ [key]: value }))),
  ...["removed_at", "removed_reason", "removed_requester"].flatMap((key) => named(`${key} as`, WRONG, (value) => removedRow({ [key]: value }))),
  // The key.
  ...named("sha256", KEYS, (value) => row({ sha256: value })),
  ...named("derived_from", KEYS, (value) => row({ layer: "text-layer", derived_from: value, derivation: "pdftotext" })),
  // The byte count.
  ...named("bytes", [0, 1, -0, 2 ** 31, 2 ** 53 - 1, 2 ** 53, 2 ** 53 + 2, 1e21, 1e300, -1e21, 0.5, -0.5, 1e-7, "12"], (value) => row({ bytes: value })),
  ...named("first_seen_at", TIMES, (value) => row({ first_seen_at: value })),
  ...named("removed_at", TIMES, (value) => removedRow({ removed_at: value })),
  // The layer, with and without what a derived layer names.
  ...LAYERS.flatMap((layer) => [
    { name: `layer ${layer} bare`, row: row({ layer }) },
    { name: `layer ${layer} with both`, row: row({ layer, derived_from: OTHER, derivation: "pdftotext -layout" }) },
    { name: `layer ${layer} with derived_from`, row: row({ layer, derived_from: OTHER }) },
    { name: `layer ${layer} with derivation`, row: row({ layer, derivation: "pdftotext -layout" }) },
    { name: `layer ${layer} with an empty derivation`, row: row({ layer, derived_from: OTHER, derivation: "" }) },
    { name: `layer ${layer} with only an empty derivation`, row: row({ layer, derivation: "" }) },
  ]),
  ...named("layer", ["exact", "EXACT-BYTES", "exact-bytes ", "text_layer"], (value) => row({ layer: value })),
  // Every tier under each visibility, on each home, with and without a release mirror.
  ...TIERS.flatMap((tier) => ["public", "restricted", "private"].map((visibility) => ({ name: `tier ${tier} as ${visibility}`, row: row({ archive_tier: tier, visibility }) }))),
  ...TIERS.flatMap((tier) =>
    STORES.flatMap((store) =>
      [[], ["github-release"], ["r2"], ["local-cache"]].map((mirrors) => ({
        name: `tier ${tier} at ${store} mirrored ${mirrors.join("+") || "nowhere"}`,
        row: row({ archive_tier: tier, visibility: tier === "t4-private" || tier === "t5-no-store" ? "restricted" : "public", store, mirrors }),
      })),
    ),
  ),
  ...named("archive_tier", ["t0", "T0-PUBLIC", "t6-other", "public"], (value) => row({ archive_tier: value })),
  ...named("store", ["s3", "R2", "github"], (value) => row({ store: value })),
  ...named("exposure", ["policy", "pre-policy", "post-policy", "PRE-POLICY"], (value) => row({ exposure: value })),
  // Mirrors: sorted, distinct, and not the home.
  ...named(
    "mirrors",
    [
      ["github-release", "local-cache"], ["local-cache", "github-release"], ["github-release", "github-release"], ["local-cache", "local-cache", "github-release"], ["r2"], ["r2", "r2"],
      ["github-release", "local-cache", "r2"], ["r2", "local-cache", "github-release"], ["s3"], ["github-release", "s3"], ["s3", "github-release"], [null], [5], [["r2"]], [""], "github-release", { 0: "github-release" },
    ],
    (value) => publicRow({ mirrors: value }),
  ),
  ...named("mirrors of a local home", [["github-release", "r2"], ["r2", "github-release"], ["local-cache"]], (value) => publicRow({ store: "local-cache", mirrors: value })),
  // The source records.
  ...named("source_ids", [[], ["a"], ["a", "a"], ["b", "a"], [""], ["a", ""], [" "], [5], [null], ["a", 5, ""], [["a"]], "a", { 0: "a" }], (value) => row({ source_ids: value })),
  ...named("observed_licence", ["CC BY 4.0", "", " ", "\n"], (value) => row({ observed_licence: value })),
  ...named("requested_url", ["https://example.org/b", "", "not a url"], (value) => row({ requested_url: value })),
  ...named("previous_path", ["data/omics/x.html", "", "/abs"], (value) => row({ previous_path: value })),
  ...named("media_type", ["a", "TEXT/HTML; charset=utf-8", "\n"], (value) => row({ media_type: value })),
  // A removal is three facts, at the tier that holds no bytes.
  { name: "a removed row", row: removedRow() },
  { name: "a removed row whose home was a release", row: removedRow({ store: "github-release" }) },
  { name: "a removed row whose home was a laptop", row: removedRow({ store: "local-cache" }) },
  { name: "a removed row with a release mirror", row: removedRow({ mirrors: ["github-release"] }) },
  { name: "a removed row with a bucket mirror", row: removedRow({ store: "github-release", mirrors: ["r2"] }) },
  { name: "a removed row with two mirrors", row: removedRow({ store: "local-cache", mirrors: ["github-release", "r2"] }) },
  { name: "a removed row with unsorted mirrors", row: removedRow({ store: "local-cache", mirrors: ["r2", "github-release"] }) },
  { name: "a removed row that names its home as a mirror", row: removedRow({ mirrors: ["r2"] }) },
  ...TIERS.map((tier) => ({ name: `a removed row at ${tier}`, row: removedRow({ archive_tier: tier, visibility: tier === "t4-private" || tier === "t5-no-store" ? "restricted" : "public" }) })),
  { name: "a removed row called public", row: removedRow({ visibility: "public" }) },
  { name: "a removed row with no reason", row: without(removedRow(), "removed_reason") },
  { name: "a removed row with no requester", row: without(removedRow(), "removed_requester") },
  { name: "a removed row with a date alone", row: without(removedRow(), "removed_reason", "removed_requester") },
  { name: "a reason with no date", row: without(removedRow(), "removed_at") },
  { name: "a reason alone", row: without(removedRow(), "removed_at", "removed_requester") },
  { name: "a requester alone", row: without(removedRow(), "removed_at", "removed_reason") },
  { name: "a reason alone on a row that holds bytes", row: row({ removed_reason: "asked" }) },
  { name: "a requester alone on a public row", row: publicRow({ removed_requester: "x" }) },
  { name: "the tier of a removal with no removal", row: row({ archive_tier: "t5-no-store" }) },
  { name: "the tier of a removal with no removal, at a release", row: row({ archive_tier: "t5-no-store", store: "github-release" }) },
  // A restricted row and the public tag.
  { name: "a restricted row whose home is a release", row: row({ store: "github-release" }) },
  { name: "a restricted row mirrored to a release", row: row({ mirrors: ["github-release"] }) },
  { name: "a restricted row on a laptop mirrored to the bucket", row: row({ store: "local-cache", mirrors: ["r2"] }) },
  { name: "a restricted row on a laptop mirrored to both", row: row({ store: "local-cache", mirrors: ["github-release", "r2"] }) },
  { name: "a restricted row at a release mirrored to a release", row: row({ store: "github-release", mirrors: ["github-release"] }) },
  // Keys the schema does not declare, in the order JavaScript lists them.
  { name: "one unknown key", row: row({ note: "x" }) },
  { name: "two unknown keys", row: row({ zeta: 1, alpha: 2 }) },
  { name: "unknown keys that are numbers", row: row({ b: 1, 10: 2, 2: 3, a: 4, "01": 5 }) },
  { name: "an unknown key and a fault a refinement finds", row: row({ note: "x", visibility: "public" }) },
  { name: "an unknown key and a member of the wrong type", row: row({ note: "x", bytes: "12" }) },
  { name: "an unknown key with an undeclared removal column", row: row({ removed_by: "x" }) },
  // More than one fault, in the order zod reports them.
  { name: "every refinement at once", row: { ...removedRow({ archive_tier: "t4-private", visibility: "public", store: "github-release", mirrors: ["r2", "github-release", "github-release"], derivation: "x" }) } },
  { name: "a wrong type stops the refinements", row: row({ visibility: "public", bytes: "12" }) },
  { name: "a failed pattern does not stop the refinements", row: row({ visibility: "public", sha256: "abc" }) },
  { name: "a negative count does not stop the refinements", row: row({ visibility: "public", bytes: -1 }) },
  { name: "a fraction stops the refinements", row: row({ visibility: "public", bytes: 0.5 }) },
  { name: "an empty text does not stop the refinements", row: row({ visibility: "public", origin: "" }) },
  { name: "a bad mirror stops the refinements", row: row({ visibility: "public", mirrors: ["s3"] }) },
  { name: "a bad source ID does not stop the refinements", row: row({ visibility: "public", source_ids: [""] }) },
  { name: "a missing member stops the refinements", row: without(row({ visibility: "public" }), "origin") },
  { name: "many members at fault", row: { sha256: 5, bytes: -1.5, media_type: "", layer: "x", origin: null, first_seen_at: "x", store: "x", mirrors: "x", visibility: "x", archive_tier: "x", exposure: "x", derived_from: "x", source_ids: [1, ""], removed_at: "x", removed_reason: "", extra: 1 } },
  // Rows written before mirrors existed parse as having none.
  { name: "no mirrors member", row: without(publicRow(), "mirrors") },
  { name: "no mirrors member, restricted, at a release", row: without(row({ store: "github-release" }), "mirrors") },
  { name: "no mirrors member, removed", row: without(removedRow(), "mirrors") },
];

// ---------------------------------------------------------------------------------------
// fetchReceiptSchema and routeAttemptSchema
// ---------------------------------------------------------------------------------------

export const baseReceipt = {
  schema_version: 1,
  source_id: "s1",
  requested_url: "https://example.org/a",
  final_url: "https://example.org/a",
  http_status: 200,
  fetched_at: "2026-11-17T16:10:11Z",
  content_type: "text/html; charset=utf-8",
  bytes: 12,
  sha256: KEY,
  route: "direct",
  routes_tried: [{ route: "direct", url: "https://example.org/a", status: 200, reason: null }],
  outcome: "stored",
  reason: null,
  source_class: "other-web",
};
const receipt = (patch = {}) => ({ ...baseReceipt, ...patch });
const failedReceipt = (patch = {}) => receipt({ final_url: null, http_status: null, content_type: null, bytes: null, sha256: null, route: null, outcome: "failed", reason: "not_found", routes_tried: [{ route: "direct", url: "https://example.org/a", status: 404, reason: "not_found" }], ...patch });
const attempt = (patch = {}) => ({ route: "direct", url: "https://example.org/a", status: 200, reason: null, ...patch });
const OUTCOMES = ["stored", "not_artifact", "failed"];
const REASONS = ["http_error", "network_error", "timeout", "empty_body", "too_large", "not_found", "rate_limited", "host_stopped", "no_doi", "no_open_copy", "unpaywall_no_email", "paywalled_not_held", "unsupported_url", "interstitial_body", "text_below_threshold", "publisher_html_only"];
const CLASSES = ["europepmc-api", "pmc", "arxiv", "preprint-server", "publisher", "code-repository", "model-hub", "data-repository", "other-web"];

export const receipts = [
  { name: "the base receipt", receipt: receipt() },
  { name: "a failed receipt", receipt: failedReceipt() },
  { name: "a receipt of what is not the artifact", receipt: receipt({ outcome: "not_artifact", reason: "interstitial_body" }) },
  ...named("not an object", [null, 5, "x", [], [receipt()]], (value) => value).map(({ name, row: value }) => ({ name, receipt: value })),
  { name: "an empty object", receipt: {} },
  ...Object.keys(baseReceipt).map((key) => ({ name: `${key} left out`, receipt: without(receipt(), key) })),
  ...Object.keys(baseReceipt).flatMap((key) => WRONG.map((value, at) => ({ name: `${key} as ${at}: ${JSON.stringify(value)}`, receipt: receipt({ [key]: value }) }))),
  ...[1, 2, 0, -1, 1.5, "1", true, null, [1]].map((value) => ({ name: `schema_version ${JSON.stringify(value)}`, receipt: receipt({ schema_version: value }) })),
  // What each outcome carries.
  ...OUTCOMES.flatMap((outcome) =>
    [null, "not_found"].flatMap((reason) =>
      [
        ["all three", {}],
        ["no sha256", { sha256: null }],
        ["no bytes", { bytes: null }],
        ["no route", { route: null }],
        ["none of the three", { sha256: null, bytes: null, route: null }],
        ["no bytes at all", { bytes: 0 }],
      ].map(([label, patch]) => ({ name: `${outcome}, reason ${reason}, ${label}`, receipt: receipt({ outcome, reason, ...patch }) })),
    ),
  ),
  ...REASONS.map((reason) => ({ name: `failed for ${reason}`, receipt: failedReceipt({ reason }) })),
  ...CLASSES.map((sourceClass) => ({ name: `class ${sourceClass}`, receipt: receipt({ source_class: sourceClass }) })),
  ...["direct", "unpaywall", "europepmc", "preprint", "crossref", ""].map((route) => ({ name: `route ${JSON.stringify(route)}`, receipt: receipt({ route }) })),
  ...TIMES.map((value, at) => ({ name: `fetched_at ${at}: ${JSON.stringify(value)}`, receipt: receipt({ fetched_at: value }) })),
  ...KEYS.map((value, at) => ({ name: `sha256 ${at}`, receipt: receipt({ sha256: value }) })),
  ...[0, -1, 0.5, 2 ** 53, 1e21].map((value) => ({ name: `bytes ${value}`, receipt: receipt({ bytes: value }) })),
  ...[0, -1, 99, 600, 200.5, 2 ** 53, 1e21, "200"].map((value) => ({ name: `http_status ${JSON.stringify(value)}`, receipt: receipt({ http_status: value }) })),
  // The routes tried.
  { name: "no route tried", receipt: receipt({ routes_tried: [] }) },
  { name: "four routes tried", receipt: receipt({ routes_tried: [attempt({ status: 403, reason: "http_error" }), attempt({ route: "unpaywall", url: null, status: null, reason: "no_doi" }), attempt({ route: "europepmc", status: null, reason: "no_open_copy" }), attempt({ route: "preprint" })] }) },
  ...[null, 5, "x", [], {}, { route: "direct" }, attempt({ route: "crossref" }), attempt({ url: 5 }), attempt({ url: "" }), attempt({ status: 200.5 }), attempt({ status: "200" }), attempt({ reason: "no" }), attempt({ extra: 1 }), attempt({ extra: 1, more: 2, reason: "no" }), without(attempt(), "url"), without(attempt(), "status", "reason")].map((value, at) => ({
    name: `an attempt ${at}: ${JSON.stringify(value)}`.slice(0, 96),
    receipt: receipt({ routes_tried: [attempt(), value] }),
  })),
  { name: "two bad attempts and a refinement", receipt: receipt({ reason: "not_found", routes_tried: [attempt({ reason: "no" }), attempt({ extra: 1 })] }) },
  { name: "one unknown key", receipt: receipt({ note: "x" }) },
  { name: "unknown keys and a refinement", receipt: receipt({ zeta: 1, 3: 1, alpha: 2, reason: "not_found" }) },
  { name: "a wrong type stops the refinements", receipt: receipt({ reason: "not_found", bytes: "12" }) },
  { name: "a bad attempt does not stop the refinements", receipt: receipt({ reason: "not_found", routes_tried: [attempt({ extra: 1 })] }) },
  { name: "a bad attempt type stops the refinements", receipt: receipt({ reason: "not_found", routes_tried: [5] }) },
];

// ---------------------------------------------------------------------------------------
// candidateSchema, observationSchema and resolveReceiptSchema
// ---------------------------------------------------------------------------------------

const observation = (patch = {}) => ({ route: "crossref", endpoint: "https://api.crossref.org/works/10.1/x", field: "message.license[].URL", value: "https://creativecommons.org/licenses/by/4.0/", observed_at: "2026-11-17T17:14:06Z", ...patch });
export const baseCandidate = {
  schema_version: 1,
  source_id: "s1",
  status: "resolved",
  observed_licence: "https://creativecommons.org/licenses/by/4.0/",
  family: "cc-by",
  archive_tier: "t0-public",
  recorded_licence: null,
  recorded_tier: "t4-private",
  agreement: "none-recorded",
  unmapped_values: [],
  reason: null,
  observations: [observation()],
};
const candidate = (patch = {}) => ({ ...baseCandidate, ...patch });

export const candidates = [
  { name: "the base candidate", candidate: candidate() },
  { name: "a conflict", candidate: candidate({ status: "conflict", observed_licence: null, family: null, archive_tier: null, agreement: "no-candidate", reason: "two services disagree" }) },
  ...[null, 5, "x", [], {}].map((value) => ({ name: `not a candidate: ${JSON.stringify(value)}`, candidate: value })),
  ...Object.keys(baseCandidate).map((key) => ({ name: `${key} left out`, candidate: without(candidate(), key) })),
  ...Object.keys(baseCandidate).flatMap((key) => WRONG.map((value, at) => ({ name: `${key} as ${at}: ${JSON.stringify(value)}`, candidate: candidate({ [key]: value }) }))),
  ...["resolved", "conflict", "unmapped", "none"].map((status) => ({ name: `status ${status}`, candidate: candidate({ status }) })),
  ...["none-recorded", "agrees", "disagrees", "recorded-unmapped", "no-candidate", "other"].map((agreement) => ({ name: `agreement ${agreement}`, candidate: candidate({ agreement }) })),
  ...[...TIERS, "t6"].map((tier) => ({ name: `tiers ${tier}`, candidate: candidate({ archive_tier: tier, recorded_tier: tier }) })),
  { name: "no observation", candidate: candidate({ observations: [] }) },
  { name: "two observations", candidate: candidate({ observations: [observation(), observation({ route: "europepmc", value: "cc by" })] }) },
  ...["crossref", "github", "huggingface", "preprint", "europepmc", "direct"].map((route) => ({ name: `observed by ${route}`, candidate: candidate({ observations: [observation({ route })] }) })),
  ...[null, 5, "x", [], {}, observation({ endpoint: "" }), observation({ field: "" }), observation({ value: "" }), observation({ value: 5 }), observation({ extra: 1 }), without(observation(), "field"), without(observation(), "route", "value")].map((value, at) => ({
    name: `an observation ${at}: ${JSON.stringify(value)}`.slice(0, 96),
    candidate: candidate({ observations: [observation(), value] }),
  })),
  // The candidate's timestamp has no fraction of a second, which the manifest's has.
  ...TIMES.map((value, at) => ({ name: `observed_at ${at}: ${JSON.stringify(value)}`, candidate: candidate({ observations: [observation({ observed_at: value })] }) })),
  ...[[""], ["a", ""], ["b", "a"], ["a", "a"], [5]].map((value) => ({ name: `unmapped_values ${JSON.stringify(value)}`, candidate: candidate({ unmapped_values: value }) })),
  { name: "unknown keys", candidate: candidate({ zeta: 1, alpha: 2 }) },
];

export const baseResolveReceipt = {
  schema_version: 1,
  source_id: "s1",
  route: "crossref",
  endpoint: "https://api.crossref.org/works/10.1/x",
  attempted_at: "2026-11-17T17:14:06Z",
  http_status: 200,
  outcome: "licence",
  reason: null,
  values: ["https://creativecommons.org/licenses/by/4.0/"],
};
const resolveReceipt = (patch = {}) => ({ ...baseResolveReceipt, ...patch });
export const resolveReceipts = [
  { name: "the base receipt", receipt: resolveReceipt() },
  { name: "nothing asked", receipt: resolveReceipt({ endpoint: null, http_status: null, outcome: "no_licence", reason: "not_applicable", values: [] }) },
  ...[null, 5, [], {}].map((value) => ({ name: `not a receipt: ${JSON.stringify(value)}`, receipt: value })),
  ...Object.keys(baseResolveReceipt).map((key) => ({ name: `${key} left out`, receipt: without(resolveReceipt(), key) })),
  ...Object.keys(baseResolveReceipt).flatMap((key) => WRONG.map((value, at) => ({ name: `${key} as ${at}: ${JSON.stringify(value)}`, receipt: resolveReceipt({ [key]: value }) }))),
  ...["not_applicable", "no_licence_in_metadata", "not_asserted", "ambiguous_version", "not_found", "http_error", "network_error", "host_stopped", "timeout"].map((reason) => ({ name: `reason ${reason}`, receipt: resolveReceipt({ outcome: "no_licence", reason, values: [] }) })),
  ...["licence", "no_licence", "stored"].map((outcome) => ({ name: `outcome ${outcome}`, receipt: resolveReceipt({ outcome }) })),
  ...TIMES.map((value, at) => ({ name: `attempted_at ${at}: ${JSON.stringify(value)}`, receipt: resolveReceipt({ attempted_at: value }) })),
  { name: "unknown keys", receipt: resolveReceipt({ zeta: 1, alpha: 2 }) },
];

// ---------------------------------------------------------------------------------------
// Small pure functions.
// ---------------------------------------------------------------------------------------

export const tiers = TIERS;
export const shardKeys = [KEY, OTHER, KEY.toUpperCase(), KEY.slice(1), `${KEY}\n`, "", "0", many(64, "g"), many(64, "a")];
export const shardNames = ["0", "9", "a", "f", "g", "A", "00", "", "\n", "0\n", "٠"];
export const dates = ["2026-11-17", "2026-1-17", "2026-11-17\n", "20261117", "", "2026-11-1٧", "2026-11-17T00", "9999-99-99"];
export const assetNames = [[`${KEY}.gz`, "0"], ["a.gz", "f"], ["a/b.gz", "0"], ["../a.gz", "0"], [".hidden", "0"], ["", "0"], ["a b", "0"], ["a.gz", "g"], ["a.gz", ""], ["a.gz", "00"], ["-a", "1"], ["_a.b-c", "2"], ["a\n", "3"], ["é", "4"]];
export const mediaTypes = [null, "", " ", "text/html", "Text/HTML; charset=UTF-8", " application/pdf ;q=1", ";charset=utf-8", "a;b;c", "TEXT/İ", " text/plain ", "text/plain﻿", "\u0085x\u0085", "x᠎", ";", "a\tb"];
export const canonicalLines = [
  {}, { b: 1, a: 2 }, { b: { z: 1, a: 2 }, a: [{ z: 1, a: 2 }] }, { 10: "a", 2: "b", a: "c", A: "d", "": "e" }, { "é": 1, e: 2, "😀": 3, "￿": 4, "\ud800": 5 },
  { a: 1e21, b: 1e-7, c: -0, d: 0.1, e: 2 ** 53 }, { a: null, b: true, c: " \n\"" },
];
export const classHosts = [
  "ebi.ac.uk", "www.ebi.ac.uk", "notebi.ac.uk", "ebi.ac.uk.example.org", "EBI.AC.UK", "europepmc.org", "biorxiv.org", "www.biorxiv.org", "connect.medrxiv.org", "arxiv.org", "export.arxiv.org", "arxiv.org.", "github.com",
  "raw.githubusercontent.com", "codeload.github.com", "gitlab.com", "bitbucket.org", "gist.github.com", "huggingface.co", "cdn-lfs.huggingface.co", "zenodo.org", "zenodo.org:8443", "figshare.com", "osf.io", "dataverse.harvard.edu",
  "harvard.edu", "ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov", "nih.gov", "link.springer.com", "media.springernature.com", "www.nature.com", "nature.com", "sciencedirect.com", "elsevier.com", "onlinelibrary.wiley.com",
  "academic.oup.com", "www.cell.com", "journals.plos.org", "mdpi.com", "frontiersin.org", "pnas.org", "science.org", "pubs.acs.org", "ieeexplore.ieee.org", "tandfonline.com", "sagepub.com", "cambridge.org", "royalsocietypublishing.org",
  "example.org", "", "nature.com\n", "xnature.com", "nature.comx", "nature_com", "İeee.org", "GITHUB.COM", "Kaggle.com", "github．com",
];

/** What `new URL(text).host` and `.hostname` are, for texts the store's URLs have no like of. */
export const urls = [
  "https://example.org", "https://example.org/", "https://Example.ORG/Path?Q=1#F", "https://example.org:443/a", "https://example.org:8443/a", "http://example.org:80/", "http://example.org:8080/", "https://example.org:080/", "https://example.org:0/",
  "https://example.org:/", "https://example.org:65535/", "https://example.org:65536/", "https://example.org:x/", "https://user:pw@example.org/", "https://user@example.org:8443/", "https://@example.org/", "https://a@b@example.org/",
  "HTTPS://EXAMPLE.ORG/", "hTTp://example.org", "https:example.org", "https:/example.org", "https:///example.org", "https:\\\\example.org\\a", "https://example.org\\a", "  https://example.org/  ", "\thttps://example.org", "https://exa\tmple.org/",
  "https://exa\nmple.org/", "https://", "https:///", "https://?x", "https://#x", "https://:80/", "https://exa mple.org/", "https://exa%20mple.org/", "https://ex%41mple.org/", "https://example%2eorg/", "https://ex<ample.org/", "https://ex|ample.org/",
  "https://ex^ample.org/", "https://example.org./", "https://.example.org/", "https://a..b/", "https://a_b.example.org/", "https://-a.example.org/", "https://a-.example.org/", "https://xn--bcher-kva.example/", "https://bücher.example/",
  "https://Éxample.org/", "https://example。org/", "https://ｅxample.org/", "https://ex­ample.org/", "https://127.0.0.1/", "https://127.1/", "https://0x7f.1/", "https://017700000001/", "https://2130706433/", "https://1.2.3.4.5/",
  "https://256.1.1.1/", "https://1.2.3.4./", "https://example.1/", "https://1e3/", "https://0x/", "https://[::1]/", "https://[::1]:8443/", "https://[2001:DB8:0:0:0:0:0:1]/", "https://[1:0:0:2:0:0:0:3]/", "https://[::ffff:1.2.3.4]/", "https://[::1/",
  "https://[z]/", "http://", "http://example.org", "ftp://example.org/a", "ftp://example.org:21/a", "s3://Bucket/key", "doi:10.1/x", "mailto:a@example.org", "file:///tmp/a", "file://host/a", "//example.org/a", "/a/b", "example.org", "example.org/a",
  "www.example.org", "", " ", "x", "10.1101/2020.01.01.123456", "https://github.com/a/b/blob/0123456789abcdef0123456789abcdef01234567/LICENSE", "https://www.biorxiv.org/content/10.1101/2020.01.01.123456v2.full.pdf", "https://example.org:8443",
  "https://EXAMPLE.org:8443/A", "https://😀.example/", "https://\ud83d.example/", "https://example.org/\ud83d", "https://example.org/%zz", "https://ex%zzample.org/", "https://ex%ample.org/", "wss://example.org:443/", "ws://example.org:80/",
  "HtTpS://ExAmPlE.OrG:443", "https://example.org:00443/", "https://example.org:+443/", "https://example.org: 443/", "https://ex'ample.org/", "https://ex\"ample.org/", "https://ex`ample.org/", "https://ex{ample}.org/", "https://ex!ample$.org/",
  "https://ex*ample.org/", "https://ex,ample;.org/", "https://ex=ample+.org/", "https://ex~ample&.org/", "https://(example).org/", "https://ex%2Fample.org/", "https://ex%3Aample.org/", "https://ex\u007fample.org/", "https://ex\u0000ample.org/",
  "https://ex\u001fample.org/", "https://xn--/", "https://xn--a/", "https://XN--BCHER-KVA.example/", "https://a.xn--b.c/", many(300, "https://a") + ".org/", `https://${many(64, "a")}.org/`, `https://${many(130, "a.")}org/`,
];

// ---------------------------------------------------------------------------------------
// expand(compressed, digest, label, bytes?): gzip as Node reads it.
// ---------------------------------------------------------------------------------------

const gz = (text, options = { level: 9 }) => zlib.gzipSync(Buffer.from(text, "utf8"), options);
const A = Buffer.from("the first member\n", "utf8");
const B = Buffer.from("and the second\n", "utf8");
const AB = Buffer.concat([A, B]);
const gzA = gz("the first member\n");
const gzB = gz("and the second\n");
const expanding = (name, compressed, of, bytes) => ({ name, hex: Buffer.from(compressed).toString("hex"), digest: sha256(of), ...(bytes === undefined ? {} : { bytes }) });
export const gunzips = [
  expanding("one member", gzA, A),
  expanding("one member, with its size", gzA, A, A.length),
  expanding("one member, with a size one too small", gzA, A, A.length - 1),
  expanding("one member, with a size one too large", gzA, A, A.length + 1),
  expanding("one member, with a size of nothing", gzA, A, 0),
  expanding("one member, against another digest", gzA, B),
  expanding("an empty member", gz(""), Buffer.alloc(0)),
  expanding("an empty member, with its size", gz(""), Buffer.alloc(0), 0),
  expanding("a member stored without compression", gz("the first member\n", { level: 0 }), A),
  expanding("two members", Buffer.concat([gzA, gzB]), AB),
  expanding("two members, held to the first", Buffer.concat([gzA, gzB]), A),
  expanding("two members, with the size of both", Buffer.concat([gzA, gzB]), AB, AB.length),
  expanding("two members, with the size of the first", Buffer.concat([gzA, gzB]), A, A.length),
  expanding("three members, one empty", Buffer.concat([gzA, gz(""), gzB]), AB),
  expanding("a member and a zero byte", Buffer.concat([gzA, Buffer.from([0])]), A),
  expanding("a member and many zero bytes", Buffer.concat([gzA, Buffer.alloc(600)]), A),
  expanding("a member, a zero byte and a member", Buffer.concat([gzA, Buffer.from([0]), gzB]), A),
  expanding("a member, a zero byte and a member, held to both", Buffer.concat([gzA, Buffer.from([0]), gzB]), AB),
  expanding("a member, a zero byte and text", Buffer.concat([gzA, Buffer.from([0]), Buffer.from("text")]), A),
  expanding("a member and text", Buffer.concat([gzA, Buffer.from("text")]), A),
  expanding("a member and one byte", Buffer.concat([gzA, Buffer.from([0x1f])]), A),
  expanding("a member and the two magic bytes", Buffer.concat([gzA, Buffer.from([0x1f, 0x8b])]), A),
  expanding("a member and half a member", Buffer.concat([gzA, gzB.subarray(0, 12)]), A),
  expanding("a member and a member without its trailer", Buffer.concat([gzA, gzB.subarray(0, gzB.length - 8)]), AB),
  expanding("a member and a member with a wrong checksum", Buffer.concat([gzA, gzB.subarray(0, gzB.length - 8), Buffer.alloc(8, 1)]), AB),
  expanding("a member and a zlib stream", Buffer.concat([gzA, zlib.deflateSync(B)]), A),
  expanding("nothing", Buffer.alloc(0), Buffer.alloc(0)),
  expanding("nothing, with a size of nothing", Buffer.alloc(0), Buffer.alloc(0), 0),
  expanding("one zero byte", Buffer.from([0]), Buffer.alloc(0)),
  expanding("text, not gzip", A, A),
  expanding("a zlib stream, not gzip", zlib.deflateSync(A), A),
  expanding("a raw deflate stream", zlib.deflateRawSync(A), A),
  expanding("the header alone", gzA.subarray(0, 10), A),
  expanding("half a member", gzA.subarray(0, 14), A),
  expanding("a member without its trailer", gzA.subarray(0, gzA.length - 8), A),
  expanding("a member without its last byte", gzA.subarray(0, gzA.length - 1), A),
  expanding("a member with a wrong checksum", Buffer.concat([gzA.subarray(0, gzA.length - 8), Buffer.alloc(4, 1), gzA.subarray(gzA.length - 4)]), A),
  expanding("a member with a wrong length", Buffer.concat([gzA.subarray(0, gzA.length - 4), Buffer.alloc(4, 1)]), A),
  expanding("a member with a wrong method", Buffer.concat([gzA.subarray(0, 2), Buffer.from([7]), gzA.subarray(3)]), A),
  expanding("a member with reserved flags", Buffer.concat([gzA.subarray(0, 3), Buffer.from([0xe0]), gzA.subarray(4)]), A),
  expanding("a member with a name and a comment", Buffer.concat([gzA.subarray(0, 3), Buffer.from([0x18]), gzA.subarray(4, 10), Buffer.from("name\0comment\0"), gzA.subarray(10)]), A),
  expanding("a member with an extra field", Buffer.concat([gzA.subarray(0, 3), Buffer.from([0x04]), gzA.subarray(4, 10), Buffer.from([3, 0, 1, 2, 3]), gzA.subarray(10)]), A),
  expanding("a larger member", gz(many(5000, "0123456789abcdef\n")), Buffer.from(many(5000, "0123456789abcdef\n"))),
  expanding("a larger member, with a size one too small", gz(many(5000, "0123456789abcdef\n")), Buffer.from(many(5000, "0123456789abcdef\n")), 5000 * 17 - 1),
];

// ---------------------------------------------------------------------------------------
// Scenarios: a tree laid out in a scratch folder, then calls made on it in order. The
// recorder writes what each call returned or threw and every file it left different.
// A step is [name, ...arguments], or { write: { path: text or null } } to change the tree.
// ---------------------------------------------------------------------------------------

const K = Object.fromEntries(["a", "b", "c", "d", "e", "g", "h"].map((name) => [name, keyIn("0", name)]));
const K1 = Object.fromEntries(["a", "b", "c"].map((name) => [name, keyIn("1", name)]));
const NOWHERE = keyIn("0", "nowhere");
const shard = (name) => `data/archive/objects/${name}.jsonl`;
const sortedByKey = (...values) => values.slice().sort((a, b) => (a.sha256 < b.sha256 ? -1 : a.sha256 > b.sha256 ? 1 : 0));

const transitions = {
  name: "manifest transitions",
  files: {
    [shard("0")]: lines(
      ...sortedByKey(
        row({ sha256: K.a, store: "local-cache", source_ids: ["s-a"] }),
        publicRow({ sha256: K.b, mirrors: ["github-release"], source_ids: ["s-b"] }),
        without(row({ sha256: K.d, source_ids: ["s-d"] }), "mirrors"),
      ),
    ),
    [shard("1")]: lines(...sortedByKey(publicRow({ sha256: K1.a, store: "github-release", source_ids: ["s-1a"] }), publicRow({ sha256: K1.b, store: "local-cache", source_ids: ["s-1b"] }), row({ sha256: K1.c, source_ids: ["s-1c"] }))),
  },
  steps: [
    ["readShard", "0"], ["readShard", "1"], ["readShard", "f"], ["readShard", "g"], ["readManifest"], ["checkManifest"],
    // upsertRow writes the row it was given, not the row the schema read.
    ["upsertRow", without(row({ sha256: K.e, source_ids: ["s-e"] }), "mirrors")],
    ["upsertRow", without(row({ sha256: K.e, source_ids: ["s-e"] }), "mirrors")],
    ["upsertRow", row({ sha256: K.e, source_ids: ["s-z", "s-0", "s-e"], bytes: 99, origin: "https://elsewhere.example/" })],
    ["upsertRow", row({ sha256: K.e, source_ids: ["s-0", "s-0"] })],
    ["upsertRow", row({ sha256: K.e, source_ids: [] })],
    ["upsertRow", row({ sha256: K.g, source_ids: ["b", "a", "b"] })],
    ["upsertRow", row({ sha256: K.g, source_ids: ["a"] })],
    ["upsertRow", row({ sha256: K.g, source_ids: ["c"] })],
    ["upsertRow", row({ sha256: K.h, visibility: "public" })],
    ["upsertRow", row({ sha256: K.h.toUpperCase() })],
    ["upsertRow", row({ sha256: keyIn("7", "new shard"), note: "x" })],
    ["upsertRow", publicRow({ sha256: keyIn("7", "new shard"), mirrors: ["github-release"] })],
    ["upsertRow", null],
    // updateRow moves the licence columns and nothing else.
    ["updateRow", K.a, { archive_tier: "t0-public" }],
    ["updateRow", K.a, { archive_tier: "t1-public-nc", observed_licence: "CC BY-NC 4.0", exposure: "pre-policy" }],
    ["updateRow", K.a, { archive_tier: "t3-verbatim-nd-nc" }],
    ["updateRow", K.a, { archive_tier: "t4-private", observed_licence: null }],
    ["updateRow", K.a, { archive_tier: "t4-private", observed_licence: "" }],
    ["updateRow", K.a, { archive_tier: "t4-private", exposure: null }],
    ["updateRow", K.a, { archive_tier: "t4-private" }],
    ["updateRow", K.b, { archive_tier: "t4-private" }],
    ["updateRow", K.b, { archive_tier: "t5-no-store" }],
    ["updateRow", K.b, { archive_tier: "bogus" }],
    ["updateRow", K.b, { archive_tier: "t2-verbatim-nd", exposure: "bogus" }],
    ["updateRow", K.b, { archive_tier: "t2-verbatim-nd", observed_licence: 5 }],
    ["updateRow", K.b, { archive_tier: "t2-verbatim-nd", observed_licence: "CC BY-ND 4.0" }],
    ["updateRow", NOWHERE, { archive_tier: "t0-public" }],
    ["updateRow", "not a key", { archive_tier: "t0-public" }],
    ["updateRow", keyIn("9", "no shard"), { archive_tier: "t0-public" }],
    // markHome keeps the old home as a mirror, unless it was a laptop.
    ["markHome", K.a, "r2"], ["markHome", K.a, "r2"], ["markHome", K1.a, "r2"], ["markHome", K1.b, "github-release"], ["markHome", K1.b, "r2"], ["markHome", K1.b, "local-cache"], ["markHome", K1.b, "github-release"],
    ["markHome", K1.c, "github-release"], ["markHome", K1.c, "local-cache"], ["markHome", K1.c, "bogus"], ["markHome", NOWHERE, "r2"], ["markHome", "not a key", "r2"],
    // markMirror adds a second copy.
    ["markMirror", K.a, "github-release"], ["markMirror", K.a, "local-cache"], ["markMirror", K.a, "local-cache"], ["markMirror", K.a, "r2"], ["markMirror", K1.a, "local-cache"], ["markMirror", K1.a, "github-release"], ["markMirror", K1.a, "r2"],
    ["markMirror", K.b, "local-cache"], ["markMirror", K.b, "bogus"], ["markMirror", NOWHERE, "r2"], ["markMirror", "not a key", "r2"],
    // markRemoved takes a row down whatever else it says.
    ["markRemoved", K.b, { removed_at: "2026-11-20T10:00:00Z", removed_reason: "asked", removed_requester: "the publisher" }],
    ["markRemoved", K1.a, { removed_reason: "asked again", removed_requester: "docs/reviews/2026-11-20-takedown.md" }],
    ["markRemoved", K1.b, { removed_at: "2026-11-20T10:00:00.5Z", removed_reason: "r", removed_requester: "q" }],
    ["markRemoved", K.a, { removed_reason: "", removed_requester: "q" }],
    ["markRemoved", K.a, { removed_reason: "r" }],
    ["markRemoved", K.a, { removed_at: "yesterday", removed_reason: "r", removed_requester: "q" }],
    ["markRemoved", K.a, { removed_at: null, removed_reason: "r", removed_requester: "q" }],
    ["markRemoved", K.b, { removed_at: "2026-11-21T00:00:00Z", removed_reason: "a second time", removed_requester: "q" }],
    ["markRemoved", NOWHERE, { removed_reason: "r", removed_requester: "q" }],
    ["markRemoved", "not a key", { removed_reason: "r", removed_requester: "q" }],
    // A removed row afterwards.
    ["updateRow", K.b, { archive_tier: "t0-public" }], ["updateRow", K.b, { archive_tier: "t5-no-store", observed_licence: "x" }], ["markHome", K.b, "github-release"], ["markHome", K.b, "local-cache"], ["markMirror", K.b, "github-release"],
    ["upsertRow", row({ sha256: K.b, source_ids: ["s-late"] })],
    ["readManifest"], ["checkManifest"], ["verifyObjects"],
  ],
};

const X = Object.fromEntries(["a", "b", "c", "d"].map((name) => [name, keyIn("3", name)]));
const badManifests = {
  name: "manifests that are not in order",
  files: {
    // A row in the wrong shard, a key twice and a shard out of order.
    [shard("2")]: lines(...sortedByKey(row({ sha256: X.a }), row({ sha256: X.b })).reverse(), row({ sha256: sortedByKey(row({ sha256: X.a }), row({ sha256: X.b }))[0].sha256, bytes: 13 })),
    // A row the schema refuses on the fourth line that is not blank, with blank lines about.
    [shard("3")]: `\n${line(row({ sha256: X.a }))}\n   \n${line(row({ sha256: X.b }))}\r\n\n${line(row({ sha256: X.c }))}\n${line(row({ sha256: X.d, visibility: "public" }))}\n`,
    // No newline at the end, and a row with every member in another order.
    [shard("4")]: JSON.stringify(Object.fromEntries(Object.entries(row({ sha256: keyIn("4", "a") })).reverse())),
    [shard("5")]: "",
    [shard("6")]: "\n\n",
    [shard("a")]: `${line(row({ sha256: keyIn("a", "a"), bytes: -1 }))}\n${line(row({ sha256: keyIn("a", "b"), bytes: "x", note: 1 }))}\n`,
    "data/archive/objects/g.jsonl": "not read\n",
    "data/archive/objects/00.jsonl": "not read\n",
    "data/archive/objects/0.json": "not read\n",
  },
  steps: [
    ["checkManifest"], ["readShard", "2"], ["readShard", "3"], ["readShard", "4"], ["readShard", "5"], ["readShard", "6"], ["readShard", "a"], ["readManifest"], ["verifyObjects"],
    ["markHome", X.a, "r2"], ["markMirror", sortedByKey(row({ sha256: X.a }), row({ sha256: X.b }))[1].sha256, "local-cache"], ["updateRow", keyIn("4", "a"), { archive_tier: "t4-private" }], ["checkManifest"],
    { write: { [shard("3")]: null, [shard("a")]: null } },
    ["checkManifest"], ["readManifest"],
    ["upsertRow", row({ sha256: keyIn("2", "late") })], ["checkManifest"], ["readShard", "2"],
  ],
};

const O = { fine: held("an object the cache holds\n"), short: held("an object whose row counts wrongly\n"), spoiled: held("an object whose file is spoiled\n"), gone: held("an object the cache lost\n"), taken: held("an object taken down\n"),
  halfTaken: held("an object taken down and still held\n"), refused: held("an object never to be held, and held\n"), stray: held("an object no row names\n"), misfiled: held("an object in the wrong folder\n"), padded: held("an object with zero bytes after it\n"), two: held("an object in two members\n") };
const objectRow = (object, patch = {}) => row({ sha256: object.key, bytes: object.bytes, ...patch });
const byShard = (rowsHeld) => {
  const files = {};
  for (const value of sortedByKey(...rowsHeld)) files[shard(value.sha256[0])] = (files[shard(value.sha256[0])] ?? "") + `${line(value)}\n`;
  return files;
};
const cacheScenario = {
  name: "the cache against the manifest",
  files: byShard([
    objectRow(O.fine), objectRow(O.short, { bytes: O.short.bytes + 1 }), objectRow(O.spoiled), objectRow(O.gone), objectRow(O.taken, { archive_tier: "t5-no-store", ...removal }),
    objectRow(O.halfTaken, { archive_tier: "t5-no-store", ...removal }), objectRow(O.refused, { archive_tier: "t5-no-store" }), objectRow(O.misfiled), objectRow(O.padded), objectRow(O.two),
    row({ sha256: keyIn("c", "never held"), archive_tier: "t5-no-store" }),
  ]),
  cache: {
    [cachePath(O.fine.key)]: O.fine.gz,
    [cachePath(O.short.key)]: O.short.gz,
    [cachePath(O.spoiled.key)]: O.fine.gz,
    [cachePath(O.halfTaken.key)]: O.halfTaken.gz,
    [cachePath(O.refused.key)]: O.refused.gz,
    [cachePath(O.stray.key)]: O.stray.gz,
    [`data/archive/cache/zz/${O.misfiled.key}.gz`]: O.misfiled.gz,
    [cachePath(O.padded.key)]: `${O.padded.gz}000000`,
    [cachePath(O.two.key)]: zlib.gzipSync(Buffer.from("an object in ")).toString("hex") + zlib.gzipSync(Buffer.from("two members\n")).toString("hex"),
    "data/archive/cache/ab/not-a-key.gz": "00",
    [`data/archive/cache/ab/${O.fine.key.toUpperCase()}.gz`]: O.fine.gz,
    [`data/archive/cache/ab/${O.fine.key}.gz.tmp`]: O.fine.gz,
    [`data/archive/cache/ab/${O.fine.key}`]: O.fine.gz,
    [`data/archive/cache/${keyIn("d", "loose")}.gz`]: "00",
    [`data/archive/cache/ab/${keyIn("e", "garbage, unlisted")}.gz`]: "6e6f74",
  },
  steps: [
    ["objectPath", O.fine.key], ["objectPath", O.fine.key.toUpperCase()], ["objectPath", ""], ["objectPath", `${O.fine.key}\n`], ["objectPath", "../../etc/passwd"],
    ["archiveAssetName", O.fine.key], ["archiveAssetName", "x"],
    ...Object.values(O).map((object) => ["hasObject", object.key]),
    ...Object.values(O).map((object) => ["readObject", object.key]),
    ["hasObject", "not a key"], ["readObject", "not a key"],
    ["cacheContents"], ["verifyObjects"],
  ],
  commands: [
    { argv: ["archive", "verify"] }, { argv: ["archive", "status"] }, { argv: ["archive", "status", "--json"] },
    { argv: ["archive", "verify"], env: { ARCHIVE_CACHE: "elsewhere" } }, { argv: ["archive", "status", "--json"], env: { ARCHIVE_CACHE: "elsewhere" } },
  ],
};
const cleanCache = {
  name: "a cache that verifies",
  files: byShard([objectRow(O.fine), objectRow(O.taken, { archive_tier: "t5-no-store", ...removal }), objectRow(O.two, { visibility: "public", archive_tier: "t0-public" })]),
  cache: {
    [cachePath(O.fine.key)]: O.fine.gz,
    [cachePath(O.two.key)]: zlib.gzipSync(Buffer.from("an object in two members\n"), { level: 1 }).toString("hex"),
  },
  steps: [["verifyObjects"], ["cacheContents"]],
  commands: [{ argv: ["archive", "verify"] }],
};
const noCache = { name: "no cache and no manifest", files: {}, steps: [["verifyObjects"], ["cacheContents"], ["readManifest"], ["checkManifest"], ["readReceipts"], ["readCandidates"]], commands: [{ argv: ["archive", "verify"] }] };

// ---------------------------------------------------------------------------------------
// Receipts.
// ---------------------------------------------------------------------------------------

const audit = (name) => `data/omics/audits/${name}`;
const receiptsScenario = {
  name: "receipts",
  files: {
    [audit("2026-11-17-archive-fetch.receipts.jsonl")]: lines(
      receipt({ source_id: "s1", fetched_at: "2026-11-17T10:00:00Z" }),
      failedReceipt({ source_id: "s2", fetched_at: "2026-11-17T10:00:01Z" }),
      "",
      receipt({ source_id: "s3", fetched_at: "2026-11-17T23:59:59Z", outcome: "not_artifact", reason: "publisher_html_only" }),
      failedReceipt({ source_id: "s4", fetched_at: "2026-11-17T12:00:00Z", reason: "timeout" }),
      receipt({ source_id: "s4", fetched_at: "2026-11-17T12:00:00Z" }),
      receipt({ source_id: "s5", fetched_at: "2026-11-17T12:00:00.5Z" }),
      failedReceipt({ source_id: "s5", fetched_at: "2026-11-17T12:00:00Z" }),
    ),
    [audit("2026-11-18-archive-fetch.receipts.jsonl")]: `${line(receipt({ source_id: "s2", fetched_at: "2026-11-18T00:00:00Z" }))}\r\n   \n${line(failedReceipt({ source_id: "s1", fetched_at: "2026-11-16T00:00:00Z" }))}`,
    [audit("2026-11-17-licence-resolve.receipts.jsonl")]: lines(resolveReceipt()),
    [audit("2026-11-19-archive-fetch.receipts.jsonl.bak")]: "not read\n",
    [audit("x2026-11-19-archive-fetch.receipts.jsonl")]: "not read\n",
    [audit("2026-11-19-archive-fetch.receipts.jsonl\n")]: "not read\n",
    [audit("26-11-19-archive-fetch.receipts.jsonl")]: "not read\n",
  },
  steps: [
    ["readReceipts"], ["latestBySource"],
    ["receiptPath", "2026-11-17"], ["receiptPath", "2026-11-17\n"], ["receiptPath", "17-11-2026"], ["receiptPath", ""],
    ["appendReceipt", receipt({ source_id: "s9", fetched_at: "2026-11-17T23:00:00Z" })],
    ["appendReceipt", Object.fromEntries(Object.entries(failedReceipt({ source_id: "s9", fetched_at: "2026-12-01T00:00:00.25Z" })).reverse())],
    ["appendReceipt", receipt({ source_id: "s9", reason: "not_found" })],
    ["appendReceipt", receipt({ source_id: "s9", fetched_at: "tomorrow" })],
    ["appendReceipt", receipt({ source_id: "s9", note: "x" })],
    ["appendReceipt", null],
    // The file with no newline at its end takes the next receipt on the same line.
    ["appendReceipt", receipt({ source_id: "s8", fetched_at: "2026-11-18T09:00:00Z" })],
    ["readReceipts"],
    { write: { [audit("2026-11-18-archive-fetch.receipts.jsonl")]: `\n${line(receipt())}\n\n${line(receipt({ reason: "timeout" }))}\n` } },
    ["readReceipts"], ["latestBySource"],
    { write: { [audit("2026-11-18-archive-fetch.receipts.jsonl")]: lines(receipt(), { schema_version: 2 }) } },
    ["readReceipts"],
  ],
};

// ---------------------------------------------------------------------------------------
// The work list and the status of a tree.
// ---------------------------------------------------------------------------------------

const source = (id, attributes, more = {}) => JSON.stringify({ id, kind: "source", name: id, ...(attributes === undefined ? {} : { attributes }), ...more });
const SOURCES = "data/entities/sources.jsonl";
const MAP = "data/archive/licence-map.tsv";
const CANDIDATES = "data/archive/licence-candidates.jsonl";
const HEADER = "licence\tfamily\ttier\tbasis\tnote";
const mapText = (...mapRows) => `# A comment.\n${HEADER}\n${mapRows.map((fields) => fields.join("\t")).join("\n")}\n`;

const W = { held: held("the artifact of w-both\n"), lost: held("the artifact of w-shared\n") };
const workSources = lines(
  source("w-both", { artifact_url: "https://www.biorxiv.org/content/10.1101/1.full.pdf", url: "https://doi.org/10.1101/1", doi: "10.1101/1", artifact_sha256: many(64, "a") }),
  source("w-url", { url: "https://GitHub.com/Org/Repo", doi: " 10.5/x " }),
  source("w-ftp", { artifact_url: "ftp://ftp.ebi.ac.uk/pub/x", url: "https://www.ebi.ac.uk/x" }),
  source("w-padded", { artifact_url: "  https://arxiv.org/abs/1\t", artifact_sha256: "  " }),
  source("w-upper", { url: "HTTPS://ZENODO.ORG/records/1" }),
  source("w-port", { url: "https://zenodo.org:8443/records/1" }),
  source("w-default-port", { url: "https://huggingface.co:443/x" }),
  source("w-shared", { url: "https://www.nature.com/articles/x", doi: "10.1038/x" }),
  source("w-shared-2", { artifact_url: "https://www.nature.com/articles/x" }),
  source("w-shared-3", { url: "https://www.nature.com/articles/x ", doi: "" }),
  source("w-user", { url: "https://user:pw@pmc.ncbi.nlm.nih.gov/articles/PMC1/" }),
  "",
  source("w-doi", { doi: "10.1/only" }),
  source("w-neither", { title: "nothing to fetch" }),
  source("w-no-attributes"),
  source("w-not-http", { artifact_url: "s3://bucket/key", url: "doi:10.1/x", doi: "10.1/x" }),
  source("w-unparsable", { url: "https://", doi: "10.1/y" }),
  source("w-unparsable-2", { artifact_url: "https://exa mple.org/a", url: "https://example.org/fallback-not-used" }),
  source("w-number", { artifact_url: 5, url: ["https://example.org"], doi: 10.1 }),
  source("w-null", { artifact_url: null, url: null, doi: null }),
  source("w-empty", { artifact_url: "", url: " " }),
  JSON.stringify({ id: "w-model", kind: "model", name: "not a source", attributes: { url: "https://example.org/model" } }),
  JSON.stringify({ id: "w-no-kind", attributes: { url: "https://example.org/x" } }),
  "   ",
  source("w-other", { url: "https://example.org/other" }),
  source("w-other-2", { url: "https://Example.org/other-2" }),
  source("w-http", { url: "http://example.org/plain" }),
);
const workScenario = {
  name: "the work list and the status of a tree",
  files: {
    [SOURCES]: workSources,
    [audit("2026-11-17-archive-fetch.receipts.jsonl")]: lines(
      receipt({ source_id: "w-both", sha256: W.held.key, bytes: W.held.bytes }),
      receipt({ source_id: "w-shared", sha256: W.lost.key, bytes: W.lost.bytes }),
      receipt({ source_id: "w-shared-2", outcome: "not_artifact", reason: "interstitial_body" }),
      receipt({ source_id: "w-shared-3", outcome: "not_artifact", reason: "publisher_html_only" }),
      receipt({ source_id: "w-user", outcome: "not_artifact", reason: "publisher_html_only" }),
      failedReceipt({ source_id: "w-url", reason: "timeout" }),
      failedReceipt({ source_id: "w-ftp", reason: "http_error" }),
      failedReceipt({ source_id: "w-padded", reason: "http_error" }),
      failedReceipt({ source_id: "w-upper", reason: "not_found", fetched_at: "2026-11-17T09:00:00Z" }),
      receipt({ source_id: "w-upper", sha256: W.held.key, bytes: W.held.bytes, fetched_at: "2026-11-17T09:30:00Z" }),
      failedReceipt({ source_id: "w-doi", reason: "no_open_copy" }),
      failedReceipt({ source_id: "w-gone", reason: "no_open_copy" }),
    ),
    ...byShard([objectRow(W.held, { source_ids: ["w-both", "w-upper"] }), objectRow(W.lost, { source_ids: ["w-shared"] })]),
  },
  cache: { [cachePath(W.held.key)]: W.held.gz, [cachePath(O.stray.key)]: O.stray.gz },
  steps: [["readWorkList"], ["groupByUrl"], ["summarise"]],
  commands: [{ argv: ["archive", "worklist"] }, { argv: ["archive", "worklist", "--json"] }, { argv: ["archive", "status"] }, { argv: ["archive", "status", "--json"] }, { argv: ["archive"] }, { argv: ["archive", "bogus"] }, { argv: ["archive", "worklist", "--quiet"] }],
};
const manyHosts = {
  name: "more hosts than the work list prints",
  files: { [SOURCES]: lines(...Array.from({ length: 31 }, (_, at) => source(`h-${String(at).padStart(2, "0")}`, { url: `https://host-${String(at % 28).padStart(2, "0")}.example.org/${at}` })), source("h-doi", { doi: "10.1/x" })) },
  steps: [["summarise"]],
  commands: [{ argv: ["archive", "worklist"] }, { argv: ["archive", "status"] }],
};
const noSources = { name: "no source records", files: {}, steps: [["readWorkList"], ["summarise"], ["readSourceLicences"], ["buildTriage"]], commands: [{ argv: ["archive", "worklist"] }, { argv: ["archive", "status"] }] };

// ---------------------------------------------------------------------------------------
// The licence map, the classifier and the triage queue.
// ---------------------------------------------------------------------------------------

const MAPPED = [
  ["CC BY 4.0", "cc-by", "t0-public", "licence-text", "read on 2026-11-17"],
  ["CC BY-NC 4.0", "cc-by-nc", "t1-public-nc", "licence-text", ""],
  ["CC BY-ND 4.0", "cc-by-nd", "t2-verbatim-nd", "licence-text", "a note with spaces "],
  ["CC BY-NC-ND 4.0", "cc-by-nc-nd", "t3-verbatim-nd-nc", "licence-text", "x"],
  ["all rights reserved", "proprietary", "t4-private", "licence-text", "x"],
  ["do not keep", "proprietary", "t5-no-store", "owner-ruling", "x"],
  ["publisher terms", "unknown", "t4-private", "unresolved", "x"],
  ["two statements", "unknown", "t4-private", "conflicting", "x"],
  ["https://creativecommons.org/licenses/by/4.0/", "cc-by", "t0-public", "spelling", "x"],
  ["cc by", "cc-by", "t0-public", "spelling", "x"],
  ["https://creativecommons.org/licenses/by-nc/4.0/", "cc-by-nc", "t1-public-nc", "spelling", "x"],
  ["MIT", "permissive-code", "t0-public", "licence-text", "x"],
  [" padded ", "cc-by", "t0-public", "licence-text", "x"],
  ["HTTPS://EXAMPLE.ORG/LICENCE", "cc-by", "t0-public", "spelling", "x"],
  ["http:/one-slash", "cc-by", "t0-public", "spelling", "x"],
];
const licenceSources = lines(
  source("l-by", { licence: "CC BY 4.0", url: "https://www.nature.com/articles/a", doi: "10.1038/a", publication_status: "published" }),
  source("l-nc", { licence: "CC BY-NC 4.0", url: "https://arxiv.org/abs/1" }),
  source("l-nd", { licence: "CC BY-ND 4.0" }),
  source("l-ncnd", { licence: "CC BY-NC-ND 4.0" }),
  source("l-arr", { licence: "all rights reserved" }),
  source("l-never", { licence: "do not keep" }),
  source("l-unresolved", { licence: "publisher terms", artifact_url: " https://pmc.ncbi.nlm.nih.gov/articles/PMC1/ ", url: "https://not-used.example/", doi: " 10.1/u ", publication_status: " preprint " }),
  source("l-conflicting", { licence: "two statements", url: "https://zenodo.org:8443/records/1" }),
  source("l-spaces", { licence: "   ", url: "https://github.com/a/b" }),
  source("l-number", { licence: 5, doi: "10.1/n" }),
  source("l-none", { url: "https://huggingface.co/a/b" }),
  source("l-no-attributes"),
  source("l-inapplicable", { missing_metadata: { licence: { reason: "inapplicable" } }, url: "https://example.org/receipt" }),
  source("l-unreported", { missing_metadata: { licence: { reason: "unreported", note: "the page states none" } }, url: "https://www.biorxiv.org/content/1" }),
  source("l-unavailable", { missing_metadata: { licence: { reason: " unavailable " } }, url: "https://www.ebi.ac.uk/x" }),
  source("l-unextracted", { missing_metadata: { licence: { reason: "unextracted" } }, url: "ftp://ftp.ebi.ac.uk/x" }),
  source("l-other-reason", { missing_metadata: { licence: { reason: "restricted" } }, url: "not a url", doi: "10.1/o" }),
  source("l-no-reason", { missing_metadata: { licence: {} }, url: "https://" }),
  source("l-missing-other", { missing_metadata: { doi: { reason: "unreported" } } }),
  source("l-missing-null", { missing_metadata: { licence: null }, url: "mailto:a@example.org" }),
  source("l-reason-number", { missing_metadata: { licence: { reason: 5 } }, url: "https://EXAMPLE.org:8443/a\tb" }),
  source("l-missing-text", { missing_metadata: "unreported" }),
  source("l-null-attributes", null),
  source("l-tab", { url: "https://example.org/a\tb", doi: "10.1/t\tu", publication_status: "pub\nlished" }),
  source("gpn-paper-20261116-source-pnas-2023", { licence: "CC BY-NC-ND 4.0" }),
  source("phylogpn-2025", {}),
  JSON.stringify({ id: "l-model", kind: "model", name: "x", attributes: { licence: "CC BY 4.0" } }),
  source("a-first", { licence: "MIT", url: "https://github.com/a/b" }),
);
const licenceCalls = [
  ["readLicenceMap"], ["readSourceLicences"],
  ...["l-by", "l-nc", "l-nd", "l-ncnd", "l-arr", "l-never", "l-unresolved", "l-spaces", "l-number", "l-none", "l-no-attributes", "l-model", "nowhere", "gpn-paper-20261116-source-pnas-2023", "phylogpn-2025", "phylogpn-20261116-source-supplement"].map((id) => ["determineForSource", id]),
  ...[
    [], ["l-by"], ["l-by", "l-none"], ["l-none", "l-by"], ["l-nc", "l-nd"], ["l-nd", "l-nc"], ["l-by", "a-first"], ["a-first", "l-by"], ["l-ncnd", "l-nc", "l-nd", "l-by"], ["l-arr", "l-none"], ["l-none", "l-arr"], ["l-none", "nowhere"], ["nowhere"],
    ["l-never", "l-arr"], ["l-by", "phylogpn-2025"], ["phylogpn-2025", "l-by"], ["gpn-paper-20261116-source-pnas-2023"], ["phylogpn-20261116-source-supplement", "l-by"], ["l-by", "l-by"], ["l-spaces"], ["l-missing-text", "l-null-attributes"],
  ].map((ids) => ["licenceClassifier", ids]),
  ["buildTriage"], ["renderTriage", "2026-01-02"], ["renderTriage", "not a date"],
];
const licenceScenario = {
  name: "the licence map, the classifier and the triage queue",
  files: { [MAP]: mapText(...MAPPED), [SOURCES]: licenceSources },
  steps: licenceCalls,
  commands: [
    { argv: ["licence", "triage", "--stdout", "--date", "2026-01-02"] }, { argv: ["licence", "triage", "--date", "2026-01-02"] }, { argv: ["licence", "triage", "--date", "2026-01-03", "--out", "elsewhere/queue.tsv"] },
    { argv: ["licence", "triage", "--date", "tomorrow"] }, { argv: ["licence", "triage", "--date", "2026-01-02\n", "--stdout"] }, { argv: ["licence", "counts"] },
  ],
};
const emptyQueue = {
  name: "a queue with nothing in it",
  files: { [MAP]: mapText(...MAPPED), [SOURCES]: lines(source("q-by", { licence: "CC BY 4.0" }), source("q-receipt", { missing_metadata: { licence: { reason: "inapplicable" } } })) },
  steps: [["buildTriage"], ["renderTriage", "2026-01-02"]],
  commands: [{ argv: ["licence", "triage", "--stdout", "--date", "2026-01-02"] }, { argv: ["licence", "counts"] }],
};
const unmappedString = {
  name: "a licence string the map has no row for",
  files: { [MAP]: mapText(...MAPPED), [SOURCES]: lines(source("u-by", { licence: "CC BY 4.0" }), source("u-new", { licence: "CC0 1.0" }), source("u-newer", { licence: "Unlicense" })) },
  steps: [["determineForSource", "u-new"], ["licenceClassifier", ["u-new"]], ["licenceClassifier", ["u-by", "u-new"]], ["licenceClassifier", ["u-new", "u-by"]], ["buildTriage"]],
  commands: [{ argv: ["licence", "triage", "--stdout", "--date", "2026-01-02"] }, { argv: ["licence", "counts"] }],
};
// readSourceLicences keeps a licence string as it is written and the triage queue trims it,
// so a string with spaces about it has a row for one and none for the other.
const paddedLicence = {
  name: "a licence string with spaces about it",
  files: { [MAP]: mapText(...MAPPED), [SOURCES]: lines(source("p-padded", { licence: " padded " }), source("p-by", { licence: " CC BY 4.0" })) },
  steps: [["readSourceLicences"], ["determineForSource", "p-padded"], ["determineForSource", "p-by"], ["licenceClassifier", ["p-padded"]], ["licenceClassifier", ["p-by"]], ["buildTriage"]],
  commands: [{ argv: ["licence", "triage", "--stdout", "--date", "2026-01-02"] }, { argv: ["licence", "counts"] }],
};
const mapFaults = {
  name: "licence maps that are refused",
  files: { [SOURCES]: lines(source("m-by", { licence: "CC BY 4.0" })) },
  steps: [
    ["readLicenceMap"],
    ...[
      "", "\n\n", "# only a comment\n", `${HEADER}`, `${HEADER}\n`, `${HEADER}\r\n`, `licence\tfamily\ttier\tbasis\n`, `${HEADER}\textra\n`, `Licence\tfamily\ttier\tbasis\tnote\n`, ` ${HEADER}\n`, `${HEADER} \n`, `licence,family,tier,basis,note\n`,
      `\n   \n# a\n${HEADER}\n# b\n\nCC BY 4.0\tcc-by\tt0-public\tlicence-text\tx\n   \n#CC BY\tcc-by\tt0-public\tlicence-text\tx\n`,
      `${HEADER}\n${HEADER}\n`,
      `${HEADER}\nCC BY 4.0\tcc-by\tt0-public\tlicence-text\n`,
      `${HEADER}\nCC BY 4.0\tcc-by\tt0-public\tlicence-text\tx\ty\n`,
      `${HEADER}\nCC BY 4.0\n`,
      `${HEADER}\n\tcc-by\tt0-public\tlicence-text\tx\n`,
      `${HEADER}\n \tcc-by\tt0-public\tlicence-text\tx\n`,
      `${HEADER}\nCC BY 4.0\tcc-by\tt0\tlicence-text\tx\n`,
      `${HEADER}\nCC BY 4.0\tcc-by\tT0-PUBLIC\tlicence-text\tx\n`,
      `${HEADER}\nCC BY 4.0\tcc-by\t\tlicence-text\tx\n`,
      `${HEADER}\nCC BY 4.0\tcc-by\tt0-public \tlicence-text\tx\n`,
      `${HEADER}\nCC BY 4.0\t\tt0-public\t\t\n`,
      `${HEADER}\nCC BY 4.0\tcc-by\tt0-public\tlicence-text\tx\nCC BY 4.0\tcc-by-nc\tt1-public-nc\tlicence-text\ty\n`,
      `${HEADER}\nCC BY 4.0\tcc-by\tt0-public\tlicence-text\tx\ncc by 4.0\tcc-by\tt0-public\tlicence-text\tx\nCC BY 4.0 \tcc-by\tt0-public\tlicence-text\tx\n`,
      `${HEADER}\nCC BY 4.0\tcc-by\tt0-public\tlicence-text\tx\r\n`,
      `${HEADER}\n # not a comment\tcc-by\tt0-public\tlicence-text\tx\n`,
      `${HEADER}\n"quoted"\tcc-by\tt0-public\tlicence-text\t"a ""note"""\n`,
      `# c\n\n\n${HEADER}\nA\tf\tt4-private\tb\tn\n\nB\tf\tt9\tb\tn\n`,
      `﻿${HEADER}\n`,
      `${HEADER}\n__proto__\tcc-by\tt0-public\tlicence-text\tx\nconstructor\tcc-by\tt1-public-nc\tlicence-text\tx\n`,
    ].flatMap((text) => [{ write: { [MAP]: text } }, ["readLicenceMap"]]),
    ["licenceClassifier", ["m-by"]], ["buildTriage"],
  ],
  commands: [{ argv: ["licence", "triage", "--stdout", "--date", "2026-01-02"] }, { argv: ["archive", "reclassify", "--dry-run"] }],
};

// ---------------------------------------------------------------------------------------
// buildCandidate: one candidate from one source's observations and a map.
// ---------------------------------------------------------------------------------------

const target = (patch = {}) => ({ source_id: "s1", url: "", host: "", doi: null, recorded_licence: null, recorded_tier: "t4-private", keys: {}, ...patch });
const BY_URL = "https://creativecommons.org/licenses/by/4.0/";
const NC_URL = "https://creativecommons.org/licenses/by-nc/4.0/";
export const built = [
  { name: "nothing observed", target: target(), observations: [] },
  { name: "one mapped URL", target: target(), observations: [observation()] },
  { name: "one mapped label", target: target(), observations: [observation({ route: "europepmc", value: "cc by" })] },
  { name: "a label then a URL", target: target(), observations: [observation({ route: "europepmc", value: "cc by" }), observation({ route: "preprint", value: BY_URL })] },
  { name: "two URLs, the later route first", target: target(), observations: [observation({ route: "preprint", value: BY_URL, field: "second" }), observation({ route: "github", value: BY_URL, field: "first" })] },
  { name: "two labels of one route", target: target(), observations: [observation({ route: "github", value: "MIT", field: "a" }), observation({ route: "github", value: "MIT", field: "b" })] },
  { name: "two labels, the later route first", target: target(), observations: [observation({ route: "europepmc", value: "cc by" }), observation({ route: "preprint", value: "CC BY 4.0" })] },
  { name: "a label, and a URL written in capitals by a later route", target: target(), observations: [observation({ value: "cc by" }), observation({ route: "europepmc", value: "HTTPS://EXAMPLE.ORG/LICENCE" })] },
  { name: "a label that only starts like a URL", target: target(), observations: [observation({ value: "cc by" }), observation({ route: "europepmc", value: "http:/one-slash" })] },
  { name: "two families", target: target(), observations: [observation(), observation({ route: "preprint", value: NC_URL })] },
  { name: "three statements, two families", target: target(), observations: [observation({ route: "europepmc", value: "cc by" }), observation({ route: "preprint", value: NC_URL }), observation()] },
  { name: "nothing mapped", target: target(), observations: [observation({ value: "zzz" }), observation({ route: "github", value: "aaa" }), observation({ route: "preprint", value: "zzz" })] },
  { name: "one mapped, one not", target: target(), observations: [observation({ value: "unheard of" }), observation({ route: "europepmc", value: "cc by" }), observation({ route: "github", value: "Another" })] },
  { name: "a recorded licence that agrees", target: target({ recorded_licence: "CC BY 4.0", recorded_tier: "t0-public" }), observations: [observation()] },
  { name: "a recorded licence that disagrees", target: target({ recorded_licence: "CC BY-NC 4.0", recorded_tier: "t1-public-nc" }), observations: [observation()] },
  { name: "a recorded licence the map lacks", target: target({ recorded_licence: "CC0 1.0" }), observations: [observation()] },
  { name: "a recorded licence, nothing mapped", target: target({ recorded_licence: "CC BY 4.0", recorded_tier: "t0-public" }), observations: [observation({ value: "zzz" })] },
  { name: "an empty recorded licence", target: target({ recorded_licence: "" }), observations: [observation()] },
  { name: "every route out of order", target: target(), observations: ["europepmc", "preprint", "huggingface", "github", "crossref", "europepmc"].map((route, at) => observation({ route, field: `f${at}`, value: at % 2 ? BY_URL : "cc by" })) },
  { name: "an observation the schema refuses", target: target(), observations: [observation({ observed_at: "2026-11-17T17:14:06.5Z" })] },
  { name: "an observation with no value", target: target(), observations: [observation({ value: "" })] },
  { name: "a recorded tier the schema refuses", target: target({ recorded_tier: "t9" }), observations: [observation()] },
  { name: "a source with no ID", target: target({ source_id: "" }), observations: [observation({ value: "zzz" })] },
  { name: "the map names a tier that keeps no copy", target: target(), observations: [observation({ value: "do not keep" })] },
  { name: "a value spelled as a member of every object", target: target(), observations: [observation({ value: "constructor" }), observation({ route: "github", value: "toString" })] },
];
export const builtMap = MAPPED;

// ---------------------------------------------------------------------------------------
// reclassify: every row's licence read again.
// ---------------------------------------------------------------------------------------

const R = Object.fromEntries(["open", "close", "tighten", "loosen", "bare", "candidate", "conflict", "unmapped", "recorded", "pair", "never", "removed", "unknown", "nobody", "same", "stale", "mirror", "pair2"].map((name) => [name, keyIn("5", name)]));
const candidateLine = (id, observations, patch = {}) => candidate({ source_id: id, observations, ...patch });
const reclassifySources = lines(
  source("r-open", { licence: "CC BY 4.0" }),
  source("r-close", { licence: "all rights reserved" }),
  source("r-tighten", { licence: "CC BY-NC-ND 4.0" }),
  source("r-loosen", { licence: "CC BY 4.0" }),
  source("r-bare", {}),
  source("r-candidate", {}),
  source("r-conflict", {}),
  source("r-unmapped", {}),
  source("r-recorded", { licence: "CC0 1.0" }),
  source("r-pair-1", { licence: "CC BY 4.0" }),
  source("r-pair-2", { licence: "CC BY-NC 4.0" }),
  source("r-pair-3", {}),
  source("r-never", { licence: "CC BY 4.0" }),
  source("r-removed", { licence: "CC BY 4.0" }),
  source("r-same", { licence: "CC BY 4.0" }),
  source("r-stale", {}),
  source("r-mirror", { licence: "CC BY-NC 4.0" }),
);
const reclassifyCandidates = lines(
  candidateLine("r-candidate", [observation({ route: "europepmc", value: "cc by" }), observation({ route: "preprint", value: BY_URL })]),
  candidateLine("r-conflict", [observation(), observation({ route: "preprint", value: NC_URL })]),
  candidateLine("r-unmapped", [observation({ value: "unheard of" })], { status: "unmapped", observed_licence: null, family: null, archive_tier: null, agreement: "no-candidate", reason: "x" }),
  candidateLine("r-recorded", [observation()]),
  candidateLine("r-unknown", [observation({ value: NC_URL })], { archive_tier: "t0-public" }),
  candidateLine("r-pair-3", [observation({ value: "CC BY-ND 4.0" })]),
  // Written when the map read this string as public. The map above reads it as never kept.
  candidateLine("r-stale", [observation({ value: "do not keep" })], { archive_tier: "t0-public" }),
  "",
  candidateLine("r-open", [observation({ value: NC_URL })]),
);
const reclassifyRows = [
  row({ sha256: R.open, source_ids: ["r-open"], store: "local-cache" }),
  publicRow({ sha256: R.close, source_ids: ["r-close"], mirrors: ["github-release"], observed_licence: "CC BY 4.0" }),
  publicRow({ sha256: R.tighten, source_ids: ["r-tighten"], mirrors: ["github-release"], observed_licence: "CC BY 4.0" }),
  publicRow({ sha256: R.loosen, source_ids: ["r-loosen"], archive_tier: "t1-public-nc", observed_licence: "CC BY-NC 4.0" }),
  publicRow({ sha256: R.bare, source_ids: ["r-bare"], observed_licence: "CC BY 4.0" }),
  row({ sha256: R.candidate, source_ids: ["r-candidate"] }),
  publicRow({ sha256: R.conflict, source_ids: ["r-conflict"], archive_tier: "t2-verbatim-nd", observed_licence: "kept" }),
  row({ sha256: R.unmapped, source_ids: ["r-unmapped"], observed_licence: "kept" }),
  row({ sha256: R.recorded, source_ids: ["r-recorded"], archive_tier: "t4-private", observed_licence: null }),
  publicRow({ sha256: R.pair, source_ids: ["r-pair-1", "r-pair-2", "r-pair-3", "r-bare"] }),
  row({ sha256: R.never, source_ids: ["r-never"], archive_tier: "t5-no-store" }),
  removedRow({ sha256: R.removed, source_ids: ["r-removed"], store: "github-release" }),
  row({ sha256: R.unknown, source_ids: ["r-unknown"] }),
  publicRow({ sha256: R.nobody, source_ids: [], observed_licence: "kept" }),
  publicRow({ sha256: R.same, source_ids: ["r-same"], observed_licence: "a spelling of it" }),
  row({ sha256: R.stale, source_ids: ["r-stale"] }),
  publicRow({ sha256: R.mirror, source_ids: ["r-mirror"], store: "github-release", archive_tier: "t3-verbatim-nd-nc", exposure: "pre-policy" }),
  publicRow({ sha256: R.pair2, source_ids: ["r-pair-3", "r-pair-1", "r-pair-2"], mirrors: ["github-release", "local-cache"] }),
];
const reclassifyCalls = [
  ["readCandidates"],
  ...["r-open", "r-close", "r-bare", "r-candidate", "r-conflict", "r-unmapped", "r-recorded", "r-unknown", "r-pair-3", "r-stale", "nowhere"].map((id) => ["readSource", id]),
  ...[[], ["r-open"], ["r-bare"], ["r-pair-1", "r-pair-2", "r-pair-3", "r-bare"], ["r-pair-3", "r-pair-2", "r-pair-1"], ["r-bare", "r-candidate"], ["r-candidate", "r-bare"], ["r-close", "r-candidate"], ["r-unknown", "nowhere"], ["r-stale", "r-open"]].map((ids) => ["readRow", ids]),
  ["planReclassify"], ["reclassifyObjects", { dryRun: true }], ["reclassifyObjects", {}], ["reclassifyObjects"],
];
const reclassifyRefused = {
  name: "a reclassification that would take a public row down",
  files: { [MAP]: mapText(...MAPPED), [SOURCES]: reclassifySources, [CANDIDATES]: reclassifyCandidates, ...byShard(reclassifyRows) },
  steps: reclassifyCalls,
  commands: [{ argv: ["archive", "reclassify", "--dry-run"] }, { argv: ["archive", "reclassify", "--dry-run", "--json"] }, { argv: ["archive", "reclassify"] }, { argv: ["archive", "reclassify", "--quiet"] }, { argv: ["archive", "reclassify", "--json", "--quiet"] }],
};
const reclassifyWritten = {
  name: "a reclassification that is written",
  files: {
    [MAP]: mapText(...MAPPED),
    [SOURCES]: reclassifySources,
    [CANDIDATES]: reclassifyCandidates,
    ...byShard(reclassifyRows.filter((value) => ![R.close, R.pair].includes(value.sha256))),
  },
  steps: [["planReclassify"], ["reclassifyObjects", { dryRun: true }], ["reclassifyObjects", {}], ["readManifest"], ["planReclassify"], ["reclassifyObjects", {}]],
  commands: [{ argv: ["archive", "reclassify", "--dry-run"] }, { argv: ["archive", "reclassify"] }, { argv: ["archive", "reclassify"] }, { argv: ["archive", "reclassify", "--json"] }],
};
const reclassifyNothing = {
  name: "a reclassification with no candidates file and no rows",
  files: { [MAP]: mapText(...MAPPED), [SOURCES]: reclassifySources },
  steps: [["readCandidates"], ["planReclassify"], ["reclassifyObjects", {}]],
  commands: [{ argv: ["archive", "reclassify"] }],
};
const candidateFaults = {
  name: "candidates that are refused",
  files: { [MAP]: mapText(...MAPPED), [SOURCES]: reclassifySources, [CANDIDATES]: `\n${line(candidateLine("c-1", [observation()]))}\n\n${line(candidateLine("c-2", []))}\n`, ...byShard([row({ sha256: R.open, source_ids: ["c-1"] })]) },
  steps: [
    ["readCandidates"], ["planReclassify"],
    { write: { [CANDIDATES]: lines(candidateLine("c-1", [observation()]), candidateLine("c-1", [observation({ value: NC_URL })]), candidateLine("c-3", [observation()], { note: 1 })) } },
    ["readCandidates"],
    { write: { [CANDIDATES]: `${line(candidateLine("c-1", [observation()]))}\r\n${line(candidateLine("c-1", [observation({ value: NC_URL })]))}` } },
    ["readCandidates"], ["readSource", "c-1"], ["planReclassify"],
  ],
  commands: [{ argv: ["archive", "reclassify", "--dry-run"] }],
};

export const scenarios = [
  transitions, badManifests, cacheScenario, cleanCache, noCache, receiptsScenario, workScenario, manyHosts, noSources, licenceScenario, emptyQueue, unmappedString, paddedLicence, mapFaults, reclassifyRefused, reclassifyWritten, reclassifyNothing, candidateFaults,
];
