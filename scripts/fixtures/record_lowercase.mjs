// Record how Node writes text in lowercase, so the Python port lowercases as the TypeScript
// does and not as the Python it happens to run on does.
//
//   node scripts/fixtures/record_lowercase.mjs
//
// Built-ins only. It writes src/rewire_evidence/data/lowercase.json, which the library reads.
//
// `toLowerCase` follows the Unicode version Node was built with, and Python's `str.lower`
// follows the one Python was built with. Python 3.12 has Unicode 15.0 and Node 24 a later
// one, and they differ on every letter added in between. So the mapping is data, recorded
// from the Node the data repository's own workflows run, which is the major version the
// `fixtures` job uses. That job runs this and fails if the committed file is not what it
// writes, so a Node release that moves to a newer Unicode shows as a red run and a commit.
//
// Three things are recorded:
//
//   lower           every code point `toLowerCase` changes, with what it becomes
//   cased           the code points with the property Cased
//   case_ignorable  the code points with the property Case_Ignorable
//
// The last two decide the one rule that looks at the neighbours: a capital sigma becomes a
// final sigma when a cased letter comes before it and none comes after, passing over
// case-ignorable characters on both sides.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const LAST = 0x10ffff;
const surrogate = (code) => code >= 0xd800 && code <= 0xdfff;
const hex = (code) => code.toString(16).toUpperCase().padStart(4, "0");

const lower = [];
for (let code = 0; code <= LAST; code += 1) {
  if (surrogate(code)) continue;
  const text = String.fromCodePoint(code);
  const lowered = text.toLowerCase();
  if (lowered !== text) lower.push([hex(code), [...lowered].map((character) => hex(character.codePointAt(0))).join(" ")]);
}

/** The code points a pattern matches, as inclusive ranges. */
function ranges(pattern) {
  const found = [];
  let start = null;
  for (let code = 0; code <= LAST + 1; code += 1) {
    const matches = code <= LAST && !surrogate(code) && pattern.test(String.fromCodePoint(code));
    if (matches && start === null) start = code;
    if (!matches && start !== null) {
      found.push(`${hex(start)}-${hex(code - 1)}`);
      start = null;
    }
  }
  return found;
}

const lines = (items) => `[\n${items.map((item) => `  ${JSON.stringify(item)}`).join(",\n")}\n ]`;
const body = [
  ` "recorded_from": ${JSON.stringify({ script: "scripts/fixtures/record_lowercase.mjs", node: process.version, unicode: process.versions.unicode })}`,
  ` "lower": ${lines(lower)}`,
  ` "cased": ${lines(ranges(/\p{Cased}/u))}`,
  ` "case_ignorable": ${lines(ranges(/\p{Case_Ignorable}/u))}`,
];
const file = path.join(root, "src/rewire_evidence/data/lowercase.json");
fs.mkdirSync(path.dirname(file), { recursive: true });
fs.writeFileSync(file, `{\n${body.join(",\n")}\n}\n`);
console.log(`Recorded ${lower.length} lowercase mappings with Node ${process.version}, Unicode ${process.versions.unicode}.`);
