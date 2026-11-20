// Record what Node does, so the Python tests compare against JavaScript and not against a
// reading of the specification.
//
//   node scripts/fixtures/record_js_fixture.mjs
//
// Built-ins only: no install, no dependency on the data repository. It writes two files.
//
//   tests/js-fixture.json             Number, string and object output of JSON.stringify.
//                                     The inputs are read from the file and the outputs are
//                                     recorded again, so the file is its own input list.
//   tests/fixtures/js-semantics.json  Behaviour the store holds no example of: key order,
//                                     integers a double cannot hold, numbers beyond any double,
//                                     lone surrogates, the UTF-8 bytes a string is hashed
//                                     as, string comparison, and what JSON.parse refuses.
//
// CI runs this and fails if either file changes, so a fixture cannot drift from the script
// that claims to have produced it.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

// Copied from rewire-benchmark-data, shared/archive/schema.ts (canonicalLine) and
// shared/archive/layers/grid/index.ts (canonicalJson), at 91f76cf, types removed.
const canonicalLine = (value) =>
  JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))));
function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const entries = Object.entries(value)
    .filter(([, held]) => held !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return `{${entries.map(([key, held]) => `${JSON.stringify(key)}:${canonicalJson(held)}`).join(",")}}`;
}
// The comparison every store file is sorted with (byId in scripts/omics/records.ts).
const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

// 1. The original fixture, recorded again from its own inputs.
const kernelFile = path.join(root, "tests/js-fixture.json");
const kernel = JSON.parse(fs.readFileSync(kernelFile, "utf8"));
const kernelOut = {
  numbers: kernel.numbers.map(([literal]) => [literal, JSON.stringify(Number(literal))]),
  strings: kernel.strings.map(([text]) => [text, JSON.stringify(text)]),
  stringify: kernel.stringify.map(([value]) => [value, JSON.stringify(value)]),
};
fs.writeFileSync(kernelFile, JSON.stringify(kernelOut, null, 1) + "\n");

// 2. Behaviour the store cannot show.
const objects = [
  '{"b":1,"a":2}',
  '{"b":1,"1":2}',
  '{"b":1,"10":2,"9":3,"a":4,"0":5}',
  '{"2":"two","1":"one","0":"zero"}',
  '{"01":1,"1":2,"-1":3,"1.5":4,"1e3":5,"+1":6," 1":7,"":8}',
  '{"4294967296":1,"4294967295":2,"4294967294":3,"4294967293":4,"x":5}',
  '{"99999999999":1,"12345678901":2,"5":3}',
  '{"a":1,"b":2,"a":3}',
  '{"1":1,"b":2,"1":3,"0":4}',
  '{"z":{"b":1,"2":2,"1":3},"3":{"y":1,"x":2}}',
  '{"list":[{"b":1,"0":2},{"k":[3,2,1]}]}',
  '{"__proto__":1,"constructor":2,"a":3}',
  '{"\\u00e9":1,"e":2,"\\ud83e\\uddec":3,"\\uffff":4,"\\ue000":5}',
  '{"n":null,"t":true,"f":false,"s":"","zero":0,"neg":-0,"float":1.0,"exp":1E2}',
  '{}',
  '{"a":{}}',
];
const integers = [
  "9007199254740991", "9007199254740992", "9007199254740993", "9007199254740994", "-9007199254740993",
  "18014398509481985", "123456789012345678901", "-123456789012345678901", "1000000000000000000000",
  "999999999999999999999", "1" + "0".repeat(22), "1" + "0".repeat(308), "340282366920938463463374607431768211456",
];
// Literals JSON.parse reads as Infinity, which JSON.stringify then writes as null.
const overflowing = ["1e400", "-1e400", "1e309", "1" + "0".repeat(5000), "-1" + "0".repeat(5000), "[1e400]"];
const strings = [
  "\ud800", "\udc00", "a\ud83db", "\udd2c\ud83e", "\ud83e\uddec", "\ud83e\ud83e\uddec", "pair \ud83e\uddec then lone \udfff",
  "\u007f", "\u0080", "\u009f", "\u00a0", "\u2028", "\ufeff", "\uffff", "\b\f\n\r\t", "\u000b", "/", "<script>",
];
// What shardOf hashes (scripts/omics/records.ts): the string as UTF-8, where a surrogate
// with no partner becomes U+FFFD.
const sha256 = (text) => crypto.createHash("sha256").update(text).digest("hex");
const hashed = [
  "", "any-record-id", "\u00e9", "\ud83e\uddec", "\ud800", "\udc00", "\ufffd", "\ud800-then-text", "a\ud83db",
  "\udd2c\ud83e", "\ud83e\ud83e\uddec", "pair \ud83e\uddec then lone \udfff",
];
const sortInputs = [
  ["b", "a", "B", "A", "", "aa", "a-", "a0", "a_"],
  ["\uffff", "\ud83e\uddec", "\ue000", "z", "\u00e9", "\ud7ff", "\ud800", "\udfff"],
  ["id-10", "id-9", "id-1", "id", "id-", "ID-1", "id-a", "id.1", "id_1"],
  ["a\ud83e\uddec", "a\uffff", "a", "a\ue000b", "a\ud83e"],
];
const refused = [
  "NaN", "Infinity", "-Infinity", "[NaN]", '{"a":Infinity}', "undefined", "", "{'a':1}", "[1,]", '{"a":1,}', "01", "+1",
  ".5", "1.", "0x10", '"tab\there"', '"line\nbreak"', "\ufeff{}", '{"a":1} x', "[1] [2]", "{a:1}",
];
const refuses = (text) => {
  try {
    JSON.parse(text);
    return false;
  } catch {
    return true;
  }
};

const semantics = {
  source: "node scripts/fixtures/record_js_fixture.mjs",
  key_order: objects.map((text) => [text, JSON.stringify(JSON.parse(text))]),
  canonical_line: objects.map((text) => [text, canonicalLine(JSON.parse(text))]),
  canonical_json: objects.map((text) => [text, canonicalJson(JSON.parse(text))]),
  large_integers: integers.map((text) => [text, JSON.stringify(JSON.parse(text))]),
  overflow: overflowing.map((text) => [text, JSON.stringify(JSON.parse(text))]),
  strings: strings.map((text) => [text, JSON.stringify(text)]),
  sha256_of_text: hashed.map((text) => [text, sha256(text)]),
  sort: sortInputs.map((list) => [list, [...list].sort(byText)]),
  refused: refused.map((text) => [text, refuses(text)]),
};
fs.mkdirSync(path.join(root, "tests/fixtures"), { recursive: true });
fs.writeFileSync(path.join(root, "tests/fixtures/js-semantics.json"), JSON.stringify(semantics, null, 1) + "\n");
console.log(`Recorded with Node ${process.version}.`);
