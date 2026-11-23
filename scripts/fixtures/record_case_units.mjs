// Record which UTF-16 units each unit matches under the `i` flag, so the Python port can run
// a pattern that is built from text it has not seen before.
//
//   node scripts/fixtures/record_case_units.mjs
//
// Built-ins only. It writes src/rewire_evidence/data/case-units.json.
//
// The span resolvers build patterns from a locator: `Table S3` becomes a pattern that finds
// its caption, under the `i` flag. scripts/fixtures/record_pattern_fixture.mjs records what a
// letter matches for each letter a fixed pattern holds, and cannot list the letters of text
// that arrives later. So this records every unit: for each of the 65,536, the units a
// pattern of that one unit matches under `i` and without `u`, wherever that is more than
// the unit itself. Nothing is worked out from a case mapping. Each set is what Node matched.
//
// It follows the Unicode version Node was built with, so the `fixtures` job in CI runs it on
// the Node the other recordings are made on and fails if the committed file is not what it
// writes.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const hex = (code) => code.toString(16).toUpperCase().padStart(4, "0");
const every = Array.from({ length: 0x10000 }, (_, code) => String.fromCharCode(code)).join("");

const matches = [];
for (let code = 0; code < 0x10000; code += 1) {
  const pattern = new RegExp(`\\u${hex(code)}`, "gi");
  const found = [];
  for (const match of every.matchAll(pattern)) found.push(match.index);
  if (!found.includes(code)) throw new Error(`a pattern of unit ${hex(code)} does not match it`);
  if (found.length > 1) matches.push([hex(code), found.map(hex).join(" ")]);
}
const recordedFrom = { script: "scripts/fixtures/record_case_units.mjs", node: process.version, unicode: process.versions.unicode };
fs.writeFileSync(
  path.join(root, "src/rewire_evidence/data/case-units.json"),
  `{\n "recorded_from": ${JSON.stringify(recordedFrom)},\n "matches": [\n${matches.map((held) => `  ${JSON.stringify(held)}`).join(",\n")}\n ]\n}\n`,
);
console.log(`Recorded ${matches.length} units that match more than themselves under the i flag, with Node ${process.version}, Unicode ${process.versions.unicode}.`);
