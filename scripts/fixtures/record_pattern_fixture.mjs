// Record what Node's patterns match, so the Python port runs each one as JavaScript does and
// not as the Python it happens to run on would.
//
//   node scripts/fixtures/record_pattern_fixture.mjs
//
// Built-ins only. It writes two files.
//
//   src/rewire_evidence/data/pattern-classes.json
//       For every class, escape, dot and letter that scripts/fixtures/pattern_requests.json
//       lists, the characters it matches under the flags listed with it. `\p{L}` follows the
//       Unicode version Node was built with, and a letter under the `i` flag follows
//       JavaScript's own rules for case, which differ with and without the `u` flag. Python
//       has other answers on both counts, so the sets are data and the library reads them.
//       Without the `u` flag a pattern sees UTF-16 units, so those sets are over the 65,536
//       units. With it they are over every code point, a lone surrogate included.
//   tests/fixtures/js-patterns.json
//       What `exec` returns and `replace` writes for each pattern and text in
//       scripts/fixtures/pattern_cases.json, every match `matchAll` gives for a pattern with
//       the `g` flag, and what `toFixed` writes for each number there.
//
// The requests are listed by scripts/fixtures/list_pattern_requests.py from the patterns the
// library runs and the ones in pattern_cases.json, and a test fails if the list is stale.
//
// The Unicode version matters, so this runs on the Node the data repository's own workflows
// run, which is the major version the `fixtures` job uses. That job runs this and fails if
// a committed file is not what it writes.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const read = (file) => JSON.parse(fs.readFileSync(path.join(root, file), "utf8"));
const hex = (code) => code.toString(16).toUpperCase();
const recordedFrom = {
  script: "scripts/fixtures/record_pattern_fixture.mjs",
  node: process.version,
  unicode: process.versions.unicode,
};

const units = Array.from({ length: 0x10000 }, (_, code) => String.fromCharCode(code));
const points = Array.from({ length: 0x110000 }, (_, code) => String.fromCodePoint(code));

/** The characters an atom matches under these flags, as inclusive ranges in hexadecimal. */
function matched(mode, atom) {
  const pattern = new RegExp(`^(?:${atom})$`, mode);
  const domain = mode.includes("u") ? points : units;
  const found = [];
  let start = null;
  for (let code = 0; code <= domain.length; code += 1) {
    const matches = code < domain.length && pattern.test(domain[code]);
    if (matches && start === null) start = code;
    if (!matches && start !== null) {
      found.push(start === code - 1 ? hex(start) : `${hex(start)}-${hex(code - 1)}`);
      start = null;
    }
  }
  return found.join(" ");
}

const requests = read("scripts/fixtures/pattern_requests.json").classes;
const classes = requests.map(([mode, atom]) => [mode, atom, matched(mode, atom)]);
// A group that took no part is undefined, which JSON has no word for. It is written as null.
const withNull = (key, value) => (value === undefined ? null : value);
const lines = (items) => `[\n${items.map((item) => `  ${JSON.stringify(item, withNull)}`).join(",\n")}\n ]`;
const classesFile = path.join(root, "src/rewire_evidence/data/pattern-classes.json");
fs.writeFileSync(
  classesFile,
  `{\n "recorded_from": ${JSON.stringify(recordedFrom)},\n "classes": ${lines(classes)}\n}\n`,
);

const shown = (found) => ({ index: found.index, text: found[0], groups: { ...(found.groups ?? {}) } });
const cases = read("scripts/fixtures/pattern_cases.json");
const patterns = cases.patterns.map(({ source, flags, texts, replacement }) => ({
  source,
  flags,
  ...(replacement === undefined ? {} : { replacement }),
  cases: texts.map((text) => {
    const pattern = new RegExp(source, flags);
    const found = new RegExp(source, flags.replace("g", "")).exec(text);
    return {
      text,
      found: found === null ? null : shown(found),
      ...(replacement === undefined ? {} : { replaced: text.replace(pattern, () => replacement) }),
      ...(flags.includes("g") ? { all: [...text.matchAll(new RegExp(source, flags))].map(shown) } : {}),
    };
  }),
}));
const fixed = cases.fixed.map(([text, digits]) => [text, digits, Number(text).toFixed(digits)]);
const patternsFile = path.join(root, "tests/fixtures/js-patterns.json");
fs.writeFileSync(
  patternsFile,
  `{\n "recorded_from": ${JSON.stringify(recordedFrom)},\n "patterns": ${lines(patterns)},\n "fixed": ${lines(fixed)}\n}\n`,
);
console.log(
  `Recorded ${classes.length} sets, ${patterns.reduce((sum, held) => sum + held.cases.length, 0)} matches and ${fixed.length} numbers with Node ${process.version}, Unicode ${process.versions.unicode}.`,
);
