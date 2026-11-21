// Changes files for the pinned store, written from the store itself. Built-ins only.
//
//   node scripts/fixtures/store_changes.mjs <rewire-benchmark-data checkout>
//
// prints how many records each file changes. record_write_fixture.mjs writes the files to
// scripts/expected/changes/, gives each to the TypeScript's applyChanges on a copy of the
// pinned store, and records every store file it left. The write gate replays them.
//
// The data repository holds one changes file, which changes one record. These are two more,
// so that `records change --changes` is held to the pinned store on a record in every record
// file, on a record whose provenance row already lists changes, and on a record whose
// attributes arrive in another order than the store holds them in.
//
// A line is {id, base_sha256, sha256, record}. The base digest is read from the record's
// provenance row. The digest of the changed record is taken here, of JSON.stringify of the
// stored record with one member replaced, and applyChanges refuses the line if the record as
// the TypeScript writes it has another.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const sha256 = (text) => crypto.createHash("sha256").update(text).digest("hex");
const rows = (file) => fs.readFileSync(file, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));

/** Every .jsonl file under a folder of the store, by its path from the store, in the order of the paths. */
function filesUnder(store, folder) {
  const out = [];
  for (const entry of fs.readdirSync(path.join(store, folder), { withFileTypes: true })) {
    const name = `${folder}/${entry.name}`;
    if (entry.isDirectory()) out.push(...filesUnder(store, name));
    else if (name.endsWith(".jsonl")) out.push(name);
  }
  return out.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}

/** The changes files, each as {name, about, lines}. */
export function storeChanges(store) {
  const files = [...filesUnder(store, "data/entities"), ...filesUnder(store, "data/evidence")].map((file) => ({ file, records: rows(path.join(store, file)) }));
  const provenance = new Map(filesUnder(store, "data/provenance/records").flatMap((file) => rows(path.join(store, file))).map((row) => [row.id, row]));
  const line = (record, next) => ({ id: record.id, base_sha256: provenance.get(record.id).sha256, sha256: sha256(JSON.stringify(next)), record: next });
  const found = (what, record) => {
    if (!record) throw new Error(`the store has no ${what}`);
    return record;
  };

  // The first record of every record file, the last file first, each with its description added to.
  const everyFile = files
    .filter((held) => held.records.length)
    .map((held) => held.records[0])
    .reverse()
    .map((record) => line(record, { ...record, description: `${record.description} Changed.` }));

  const all = files.flatMap((held) => held.records);
  const recorded = found(
    "record with a change already recorded",
    all.find((record) => provenance.get(record.id).changed_by.length > 0),
  );
  const result = found(
    "result with two attributes whose names are not numbers",
    files
      .filter((held) => held.file.startsWith("data/evidence/results/"))
      .flatMap((held) => held.records)
      .find((record) => record.id !== recorded.id && Object.keys(record.attributes).length > 1 && Object.keys(record.attributes).every((key) => !/^\d+$/.test(key))),
  );
  const entity = found(
    "entity apart from those",
    files
      .filter((held) => held.file.startsWith("data/entities/"))
      .flatMap((held) => held.records)
      .findLast((record) => record.id !== recorded.id),
  );
  const three = [
    line(recorded, { ...recorded, name: `${recorded.name} (changed)` }),
    line(result, { ...result, attributes: Object.fromEntries(Object.entries(result.attributes).reverse()) }),
    line(entity, { ...entity, status: entity.status === "needs_review" ? "source_checked" : "needs_review" }),
  ];

  return [
    { name: "one-record-in-every-file", about: "The first record of every record file, the last file first, each with its description added to", lines: everyFile },
    {
      name: "three-records",
      about: "A record whose provenance row already lists changes, renamed; a result with its attributes in reverse order; and an entity with another status",
      lines: three,
    },
  ];
}

/** A changes file as it is written: one line of JSON to a line. */
export const changesText = (lines) => lines.map((held) => `${JSON.stringify(held)}\n`).join("");

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  for (const { name, lines } of storeChanges(process.argv[2])) console.log(`${name}: ${lines.length} records`);
}
