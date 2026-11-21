// The cases the write path is recorded on: small stores, and a batch, some IDs or a changes
// file for each. Built-ins only, so this file runs anywhere:
//
//   node scripts/fixtures/write_cases.mjs
//
// prints how many cases there are of each command, from the store in
// tests/fixtures/validation.json. record_write_fixture.mjs gives each case to the
// TypeScript and writes down what it did.
//
// The stores are built here and not by the TypeScript, so that a case can hold a store the
// TypeScript would never write: a line with its keys out of order, a provenance row with a
// member missing. Where a store is meant to load, the recorder fails if it does not.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** The moment the recorder's clock stands at. In New York it is still the day before. */
export const CLOCK = "2026-02-04T04:30:00.000Z";
/** The date a change made at that moment is recorded under: the date in UTC. */
export const DATE = "2026-02-04";

const sha256 = (text) => crypto.createHash("sha256").update(text).digest("hex");
const lines = (text) => text.split("\n").filter(Boolean);
const joined = (held) => held.map((line) => `${line}\n`).join("");
const byId = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
const evidence = ["evaluation", "result", "claim"];
const shard = (id) => sha256(id)[0];
const recordFile = (record) =>
  evidence.includes(record.kind)
    ? `data/evidence/${record.kind}s/${shard(record.id)}.jsonl`
    : `data/entities/${record.kind.replace(/_/g, "-")}s.jsonl`;
const provenanceFile = (id) => `data/provenance/records/${shard(id)}.jsonl`;
const row = (record) => ({ id: record.id, sha256: sha256(JSON.stringify(record)), added_in: "a-batch", added_by: "records:add", changed_by: [] });
const reversed = (value) => Object.fromEntries(Object.entries(value).reverse());

/** A store on disk as {file: text}: the records and a provenance row for each, sorted. */
function storeOf(records) {
  const out = {};
  const put = (file, line) => {
    out[file] = (out[file] ?? "") + line;
  };
  for (const record of [...records].sort(byId)) {
    put(recordFile(record), `${JSON.stringify(record)}\n`);
    put(provenanceFile(record.id), `${JSON.stringify(row(record))}\n`);
  }
  return out;
}

/** The files of `to` that differ from `from`, and null for a file `to` lacks. */
function edits(from, to) {
  const out = {};
  for (const file of new Set([...Object.keys(from), ...Object.keys(to)])) if (from[file] !== to[file]) out[file] = to[file] ?? null;
  return out;
}

/** A store with one line replaced, found by its ID in the file given. */
function withLine(files, file, id, change) {
  const held = lines(files[file]);
  const at = held.findIndex((line) => JSON.parse(line).id === id);
  if (at < 0) throw new Error(`${file} has no ${id}`);
  const next = change(held[at]);
  const out = { ...files, [file]: joined([...held.slice(0, at), ...(next === null ? [] : [next]), ...held.slice(at + 1)]) };
  if (!out[file]) delete out[file];
  return out;
}
const withRecord = (files, record, change) => withLine(files, recordFile(record), record.id, (line) => JSON.stringify(change(JSON.parse(line))));
const withRow = (files, id, change) =>
  withLine(files, provenanceFile(id), id, (line) => {
    const next = change(JSON.parse(line));
    return next === null ? null : JSON.stringify(next);
  });

export function writeCases(tiny) {
  const records = Object.entries(tiny)
    .filter(([file]) => !file.startsWith("data/provenance/"))
    .flatMap(([, text]) => lines(text).map((line) => JSON.parse(line)))
    .sort(byId);
  const whole = storeOf(records);
  for (const [file, text] of Object.entries(tiny)) if (whole[file] !== text) throw new Error(`the store built here is not the one recorded: ${file}`);
  const one = (kind) => {
    const found = records.filter((record) => record.kind === kind);
    if (found.length !== 1) throw new Error(`expected one ${kind}, and there are ${found.length}`);
    return found[0];
  };
  const [result, evaluation, protocol, dataset, configuration, method] = ["result", "evaluation", "protocol", "dataset", "configuration", "method"].map(one);
  const sources = records.filter((record) => record.kind === "source");
  // The sources nothing but the method cites, which leave the store with it.
  const cited = (record) => new Set([...record.source_ids, ...record.links.map((link) => link.target_id)]);
  const methodOnly = sources.filter((source) => records.every((record) => record === method || !cited(record).has(source.id)));
  if (!methodOnly.length || methodOnly.length === sources.length) throw new Error("the method's own sources are not what this file assumes");
  const without = (...gone) => storeOf(records.filter((record) => !gone.includes(record)));
  const batch = (...held) => joined(held.map((record) => JSON.stringify(record)));

  const cases = [];
  const stores = { whole };
  const put = (command, name, accepted, held) => {
    const { files = whole, ...rest } = held;
    cases.push({ name, command, accepted, store: "whole", edits: edits(whole, files), input: null, batch_dir: "data/omics/a-new-batch", review: "docs/reviews/a-review.md", ids: [], ...rest });
  };

  // ---- records add -----------------------------------------------------------------------
  const add = (name, accepted, held) => put("add", name, accepted, held);
  const noResult = without(result);
  add("a result, into a shard file that is not there yet", true, { files: noResult, input: batch(result) });
  add("an evaluation and its result, the result first", true, { files: without(result, evaluation), input: batch(result, evaluation) });
  const chain = [method, configuration, evaluation, result, ...methodOnly];
  add("entities and evidence out of ID order, each with its top-level keys reversed", true, {
    files: without(...chain),
    input: batch(...[result, ...methodOnly, method, evaluation, configuration].map(reversed)),
  });
  add("a batch folder named as nothing in particular", true, { files: noResult, input: batch(result), batch_dir: "  any text, kept as given/ " });
  add("blank lines, a carriage return and no final line break", true, {
    files: without(result, evaluation),
    input: `\n\n${JSON.stringify(result)}\r\n\n${JSON.stringify(evaluation)}`,
  });
  add("a line that is one space", false, { files: noResult, input: `${JSON.stringify(result)}\n \n` });
  add("a line with space around it", true, { files: noResult, input: ` \t${JSON.stringify(result, null, 1).replace(/\n/g, " ")} \n` });
  add("an empty batch", true, { files: noResult, input: "" });
  add("an empty batch, into a store whose lines are not as they are written", true, {
    files: {
      ...withLine(withLine(noResult, recordFile(method), method.id, (line) => JSON.stringify(reversed(JSON.parse(line)))), provenanceFile(dataset.id), dataset.id, (line) => line.replace(/":/g, '": ')),
      [recordFile(sources[0])]: `\n${noResult[recordFile(sources[0])].replace(/\n/g, "\n\n").trimEnd()}`,
    },
    input: "",
  });
  add("into a store with a provenance row that has its own members, in its own order", true, {
    // The member named 7 is written in the middle of the line. JavaScript lists it first.
    files: withLine(
      withRow(noResult, method.id, ({ id, sha256: digest, ...rest }) => ({ note: "kept", sha256: digest, ...rest, id })),
      provenanceFile(method.id),
      method.id,
      (text) => text.replace('"added_by"', '"7":"seven","added_by"'),
    ),
    input: batch(result),
  });
  // Numbers and keys as JavaScript writes them, in an attribute that takes any JSON.
  const loose = { ...configuration, attributes: { ...configuration.attributes, entity_classification: { b: "@1", a: "@2", 10: "x", 2: ["@3", "@4", "@5"] } } };
  const looseText = JSON.stringify(loose).replace('"@1"', "1.0").replace('"@2"', "1e-7").replace('"@3"', "1E3").replace('"@4"', "-0.0").replace('"@5"', "123456789012345678901234567890");
  add("numbers and member names as JavaScript writes them", true, { files: without(configuration, evaluation, result), input: `${looseText}\n${batch(evaluation, result)}` });
  const beyond = JSON.stringify({ ...loose, attributes: { ...configuration.attributes, entity_classification: { beyond: "@@" } } }).replace('"@@"', "1e400");
  add("a number beyond every double", true, { files: without(configuration, evaluation, result), input: `${beyond}\n${batch(evaluation, result)}` });

  add("a record the store already holds", false, { input: batch(result) });
  add("one record twice in the batch", false, { files: noResult, input: batch(result, result) });
  add("a line that is not a record", false, { files: noResult, input: batch({ ...result, status: "nonsense" }) });
  add("a line that is a list", false, { files: noResult, input: "[]\n" });
  add("a line that is null", false, { files: noResult, input: "null\n" });
  add("a line that is not JSON", false, { files: noResult, input: "{nope\n" });
  add("no batch file", false, { files: noResult, input: null });
  add("a record the store holds, and after it a line that is not a record", false, { input: batch(result, { ...result, id: "Not An ID" }) });
  add("a value outside its vocabulary, and after it a record the store holds", false, {
    files: noResult,
    input: batch({ ...result, attributes: { ...result.attributes, metric: "no-such-metric" } }, method),
  });
  add("a value outside its vocabulary", false, { files: noResult, input: batch({ ...result, attributes: { ...result.attributes, metric: "no-such-metric" } }) });
  add("an attribute its kind does not declare", false, { files: noResult, input: batch({ ...result, attributes: { ...result.attributes, made_up: 1 } }) });
  add("a value outside its vocabulary and an attribute its kind does not declare", false, {
    files: noResult,
    input: batch({ ...result, attributes: { ...result.attributes, made_up: 1, metric: "no-such-metric" } }),
  });
  add("a link to a record that is not there", false, { files: noResult, input: batch({ ...result, links: [{ relation: "evaluation", target_id: "no-such-evaluation" }] }) });
  add("a source that is not there", false, { files: noResult, input: batch({ ...result, source_ids: ["no-such-source"] }) });
  add("a result with no printed value", false, { files: noResult, input: batch({ ...result, attributes: { ...result.attributes, printed_value: " " } }) });
  // The validators after the first two, each on its own and then in pairs, so that the one
  // that answers says which runs first. The store holds everything but the method's chain,
  // or everything but the result, and the batch brings the rest.
  const derivedResult = {
    ...result,
    attributes: {
      ...result.attributes,
      derivation: {
        method: "computed-from-source-data",
        inputs: [{ source_id: result.source_ids[1], artifact_sha256: records.find((record) => record.id === result.source_ids[1]).attributes.artifact_sha256, locator: "Table S9", row_filter: "every row", row_count: 1 }],
        aggregation: "The count of true positives",
        aggregation_source_id: result.source_ids[0],
        aggregation_locator: "Table S9 legend",
        script: "scripts/omics/derive/no-such-script.py",
        script_sha256: sha256("no such script"),
        precision: "Exact",
      },
    },
  };
  const concerned = (...named) => ({ ...methodOnly[0], attributes: { ...methodOnly[0].attributes, evidence_concerns: [{ message: "A concern.", applies_to: named }] } });
  const again = { ...method, id: `${method.id}-again`, name: "Another name" };
  const dangling = { ...result, links: [{ relation: "evaluation", target_id: "no-such-evaluation" }] };
  const brought = (...changed) => batch(...[result, ...methodOnly, method, evaluation, configuration].map((record) => changed.find((other) => other.id === record.id) ?? record), ...changed.filter((other) => !chain.some((record) => record.id === other.id)));
  add("a derivation whose script is not there, on sources the store holds", false, { files: noResult, input: batch(derivedResult) });
  add("a scoped concern naming a record that is not there", false, { files: without(...chain), input: brought(concerned("no-such-record")) });
  add("a scoped concern naming a record the store holds", true, { files: without(...chain), input: brought(concerned(dataset.id)) });
  add("an attribute its kind does not declare and a description the batch has under two names", false, {
    files: without(...chain),
    input: brought({ ...result, attributes: { ...result.attributes, made_up: 1 } }, again),
  });
  add("a description the batch has under two names and a derivation whose script is not there", false, { files: without(...chain), input: brought(again, derivedResult) });
  add("a derivation whose script is not there and a scoped concern naming a record that is not there", false, { files: without(...chain), input: brought(derivedResult, concerned("no-such-record")) });
  add("a scoped concern naming a record that is not there and a link to a record that is not there", false, { files: without(...chain), input: brought(concerned("no-such-record"), dangling) });
  add("a description the store already has under another name", null, { input: batch({ ...method, id: `${method.id}-again`, name: "Another name" }) });
  add("a description that says where it was found", null, { input: batch({ ...method, id: `${method.id}-again`, name: "Another name", description: "See Table 2 of the paper." }) });
  add("an attribute its kind does not declare, in a store with a description that says where it was found", false, {
    files: withRow(withRecord(noResult, method, (held) => ({ ...held, description: "See Table 2 of the paper." })), method.id, (held) => ({
      ...held,
      sha256: sha256(JSON.stringify({ ...method, description: "See Table 2 of the paper." })),
    })),
    input: batch({ ...result, attributes: { ...result.attributes, made_up: 1 } }),
  });
  add("into a store with a record changed and not recorded", false, { files: withRecord(noResult, method, (held) => ({ ...held, name: "Another name" })), input: batch(result) });
  add("into a store with a provenance row and no record", false, { files: withLine(noResult, recordFile(method), method.id, () => null), input: batch(result) });

  // ---- records change <id>... ------------------------------------------------------------
  const change = (name, accepted, held) => put("change", name, accepted, held);
  const renamed = (record) => ({ ...record, name: `${record.name}, renamed` });
  const edited = (files, ...held) => held.reduce((out, record) => withRecord(out, record, renamed), files);
  change("one record edited in place", true, { files: edited(whole, method), ids: [method.id] });
  change("an entity and a result edited in place, named in another order", true, { files: edited(whole, sources[0], result), ids: [result.id, sources[0].id] });
  change("one record named twice", true, { files: edited(whole, method), ids: [method.id, method.id] });
  change("a record that was not edited", true, { ids: [dataset.id] });
  change("no records", true, { ids: [] });
  change("a review and a batch folder that are any text", true, { files: edited(whole, method), ids: [method.id], review: "", batch_dir: "shared/omics/use-cases.ts claimPins" });
  change("a record edited and not named", true, { files: edited(whole, method, dataset), ids: [method.id] });
  change("a record edited into one the store's rules refuse", true, {
    files: withRecord(whole, result, (held) => ({ ...held, links: [{ relation: "evaluation", target_id: "no-such-evaluation" }], attributes: { ...held.attributes, metric: "no-such-metric" } })),
    ids: [result.id],
  });
  change("a line with its top-level keys reversed", true, { files: withRecord(whole, method, (held) => reversed(renamed(held))), ids: [method.id] });
  change("a provenance row with changes already, and members of its own", true, {
    files: withRow(edited(whole, method), method.id, ({ id, ...rest }) => ({ ...rest, changed_by: [{ step: "by-hand", inputs: "x" }], id, note: "kept" })),
    ids: [method.id],
  });
  change("a provenance row with no digest", true, { files: withRow(edited(whole, method), method.id, ({ sha256: _, ...rest }) => rest), ids: [method.id] });
  change("a record edited into another kind", true, { files: withRecord(whole, method, (held) => ({ ...held, kind: "pipeline" })), ids: [method.id] });
  change("one ID on two lines of a file", true, {
    files: withLine(whole, recordFile(sources[0]), sources[0].id, (line) => `${line}\n${JSON.stringify(renamed(JSON.parse(line)))}`),
    ids: [sources[0].id],
  });
  change("a record with a number beyond every double", true, {
    files: withLine(whole, recordFile(configuration), configuration.id, () => beyond),
    ids: [configuration.id],
  });
  change("an ID the store does not hold, after one it does", false, { files: edited(whole, method), ids: [method.id, "no-such-record"] });
  change("a record with no provenance row", false, { files: withRow(edited(whole, method), method.id, () => null), ids: [method.id] });
  change("a provenance row with no record", false, { files: withLine(whole, recordFile(method), method.id, () => null), ids: [method.id] });
  for (const [said, value] of [["no", undefined], ["null for a", null], ["text for a", "none"], ["an object for a", { push: 1 }]])
    change(`a provenance row with ${said} list of changes`, false, { files: withRow(edited(whole, method), method.id, ({ changed_by: _, ...rest }) => ({ ...rest, changed_by: value })), ids: [method.id] });
  change("a line that is not a record, in a file the change does not touch", false, { files: withRecord(edited(whole, method), dataset, (held) => ({ ...held, status: "nonsense" })), ids: [method.id] });
  change("a line that is not JSON", false, { files: { ...whole, [recordFile(dataset)]: "{nope\n" }, ids: [method.id] });
  change("a provenance row in the wrong shard", false, {
    files: (() => {
      const from = provenanceFile(method.id);
      const to = `data/provenance/records/${["0", "1"].find((digit) => digit !== shard(method.id))}.jsonl`;
      const moved = lines(whole[from]).find((line) => JSON.parse(line).id === method.id);
      const out = withLine(whole, from, method.id, () => null);
      return { ...out, [to]: joined([...lines(out[to] ?? ""), moved].sort((a, b) => byId(JSON.parse(a), JSON.parse(b)))) };
    })(),
    ids: [method.id],
  });

  // ---- records change --changes <file> ---------------------------------------------------
  const changes = (name, accepted, held) => put("changes", name, accepted, held);
  const digest = (record) => sha256(JSON.stringify(record));
  const line = (record, next, more = {}) => ({ id: record.id, base_sha256: digest(record), sha256: digest(next), record: next, ...more });
  const file = (...held) => joined(held.map((value) => (typeof value === "string" ? value : JSON.stringify(value))));
  const good = line(method, renamed(method));
  changes("one changed record", true, { input: file(good) });
  changes("an entity and a result, in another order than the store's", true, { input: file(line(result, renamed(result)), line(dataset, renamed(dataset))) });
  changes("a record with its top-level keys reversed, and the digest of the record in schema order", true, { input: file({ ...good, record: reversed(good.record) }) });
  changes("a line with members of its own, in another order", true, { input: file({ note: "kept", record: good.record, sha256: good.sha256, base_sha256: good.base_sha256, id: good.id }) });
  changes("blank lines, a carriage return and no final line break", true, { input: `\n${JSON.stringify(good)}\r\n\n${JSON.stringify(line(dataset, renamed(dataset)))}` });
  changes("a record changed into one the store's rules refuse", true, {
    input: file(line(result, { ...result, links: [{ relation: "evaluation", target_id: "no-such-evaluation" }], attributes: { ...result.attributes, metric: "no-such-metric" } })),
  });
  changes("a provenance row with changes already, and members of its own", true, {
    files: withRow(whole, method.id, ({ id, ...rest }) => ({ ...rest, changed_by: [{ step: "by-hand", inputs: "x" }], id, note: "kept" })),
    input: file(good),
  });
  changes("a record on a line with its top-level keys reversed", true, { files: withRecord(whole, method, reversed), input: file(good) });
  changes("an empty file", false, { input: "" });
  changes("a file of blank lines", false, { input: "\n\n" });
  changes("no file", false, { input: null });
  changes("a line that is not JSON", false, { input: file(good, "{nope") });
  for (const [said, value] of [
    ["null", "null"], ["a list", "[]"], ["a number", "5"], ["text", '"text"'], ["an empty object", "{}"], ["false", "false"],
    ["without an ID", (({ id: _, ...rest }) => rest)(good)], ["with a number for an ID", { ...good, id: 5 }], ["with no base digest", (({ base_sha256: _, ...rest }) => rest)(good)],
    ["with null for a base digest", { ...good, base_sha256: null }], ["with no digest", (({ sha256: _, ...rest }) => rest)(good)], ["with a list for a digest", { ...good, sha256: [good.sha256] }],
    ["with no record", (({ record: _, ...rest }) => rest)(good)], ["with null for a record", { ...good, record: null }], ["with zero for a record", { ...good, record: 0 }],
    ["with empty text for a record", { ...good, record: "" }], ["with false for a record", { ...good, record: false }],
  ])
    changes(said.startsWith("with") ? `a line ${said}` : `a line that is ${said}`, false, { input: file(value) });
  for (const [said, value] of [["a list", []], ["text", "text"], ["true", true], ["a number", 5], ["an empty object", {}]]) changes(`a line with ${said} for a record`, false, { input: file({ ...good, record: value }) });
  changes("a good line and then a line that is null", false, { input: file(good, "null") });
  changes("one record changed twice", false, { input: file(good, line(method, { ...method, name: "A third name" })) });
  changes("a record the store does not hold", false, { input: file({ ...good, id: "no-such-record" }) });
  changes("a record with no provenance row", false, { files: withRow(whole, method.id, () => null), input: file(good) });
  changes("a change made against an older line", false, { input: file({ ...good, base_sha256: sha256("an older line") }) });
  changes("a provenance row whose digest is a number", false, { files: withRow(whole, method.id, (held) => ({ ...held, sha256: 5 })), input: file({ ...good, base_sha256: "5" }) });
  changes("a provenance row with no digest", false, { files: withRow(whole, method.id, ({ sha256: _, ...rest }) => rest), input: file(good) });
  changes("a record edited in place and not recorded", false, { files: withRecord(whole, method, (held) => ({ ...held, description: "Edited." })), input: file(good) });
  changes("a record that is not one", false, { input: file(line(method, { ...method, status: "nonsense" })) });
  changes("a record with another ID", false, { input: file(line(method, { ...renamed(method), id: dataset.id })) });
  changes("a record of another kind", false, { input: file(line(method, { ...renamed(method), kind: "pipeline" })) });
  changes("the digest of the record with its top-level keys reversed", false, { input: file({ ...good, record: reversed(good.record), sha256: digest(reversed(good.record)) }) });
  changes("the digest of another record", false, { input: file({ ...good, sha256: digest(method) }) });
  changes("an unchanged record", false, { input: file(line(method, method)) });
  changes("a good line and then an unchanged record", false, { input: file(good, line(dataset, dataset)) });
  changes("a record that is not one, on a line with another ID", false, { input: file({ ...line(method, { ...method, status: "nonsense" }), id: dataset.id, base_sha256: digest(dataset) }) });
  for (const [said, value] of [["no", undefined], ["text for a", "none"]])
    changes(`a provenance row with ${said} list of changes`, false, { files: withRow(whole, method.id, ({ changed_by: _, ...rest }) => ({ ...rest, changed_by: value })), input: file(good) });
  changes("a line that is not a record, in a file the change does not touch", false, { files: withRecord(whole, dataset, (held) => ({ ...held, status: "nonsense" })), input: file(good) });
  changes("a record with a number beyond every double", true, {
    input: file(JSON.stringify({ ...line(configuration, JSON.parse(beyond)), record: "@@" }).replace('"@@"', beyond)),
  });

  const names = cases.map((held) => `${held.command}: ${held.name}`);
  if (new Set(names).size !== names.length) throw new Error("two cases share a name");
  return { stores, cases };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const tiny = JSON.parse(fs.readFileSync(path.join(repo, "tests/fixtures/validation.json"), "utf8")).load[0].files;
  const { cases } = writeCases(tiny);
  for (const command of ["add", "change", "changes"]) console.log(`${command}: ${cases.filter((held) => held.command === command).length} cases`);
  if (process.argv[2]) fs.writeFileSync(process.argv[2], JSON.stringify(writeCases(tiny)));
}
