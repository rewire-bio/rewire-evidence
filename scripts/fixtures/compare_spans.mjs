// Run the TypeScript span resolvers on input put together at random, for compare_spans.py to
// run the port on and compare.
//
//   cd <rewire-benchmark-data checkout at STORE_REF, after npm ci>
//   node_modules/.bin/tsx <rewire-evidence>/scripts/fixtures/compare_spans.mjs <out.jsonl> <seed> <locators> <texts>
//   cd <rewire-evidence> && PYTHONPATH=. python scripts/fixtures/compare_spans.py <out.jsonl>
//
// A differential check, for looking at a change to a resolver before its cases are written.
// It is not a gate and CI does not run it: the recorded fixtures are the evidence. It writes
// one line for each call, with what the TypeScript returned or the name of what it threw.
//
// Two kinds of input. Locators from the generator of locator_cases.mjs, each resolved under
// both quote rules against every layer of tests/fixtures/spans.json that the list below
// names. And text made of the pieces in ATOMS, which are the characters the two languages
// count, match and change the case of differently, given to the functions the resolvers are
// built from, alone and as a layer.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { generated } from "./locator_cases.mjs";

const [out, seed = "1", locators = "1000", texts = "2000"] = process.argv.slice(2);
if (!out) {
  console.error("usage: tsx compare_spans.mjs <out.jsonl> [<seed> [<locators> [<texts>]]]");
  process.exit(2);
}
const store = process.cwd();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const load = async (file) => {
  const loaded = await import(pathToFileURL(path.join(store, file)).href);
  return { ...(loaded.default ?? {}), ...loaded };
};
const spans = await load("shared/omics/spans/index.ts");
const grid = await load("shared/archive/layers/grid/index.ts");
const locator = await load("shared/omics/locator.ts");
const officeSheet = await load("shared/archive/layers/office/sheet.ts");
const address = await load("shared/archive/layers/office/address.ts");
const figures = await load("shared/omics/reports/figures.ts");
const command = await load("scripts/omics/spans/cli.ts");

const plain = (value) => JSON.parse(JSON.stringify(value === undefined ? null : value));
const lines = [];
const keep = (name, given, run) => {
  let result;
  try {
    result = plain(run());
  } catch (error) {
    result = { thrown: error.constructor.name };
  }
  lines.push(JSON.stringify([name, given, result]));
};

// Locators against the recorded layers.
const LAYERS = ["aligned", "prose", "proseLines", "csv", "tsv", "json", "sheets", "jats", "html", "paged", "astral"];
const fixture = JSON.parse(fs.readFileSync(path.join(repo, "tests/fixtures/spans.json"), "utf8"));
for (const name of LAYERS) {
  const layer = fixture.layers[name];
  const index = spans.indexLayer(layer.text, layer.layer_sha256, layer.normaliser);
  const read = layer.grid === null ? null : grid.readGrid(layer.grid);
  for (const printed of generated(Number(locators), Number(seed))) {
    for (const options of [null, { legacyQuote: true }]) keep("resolveLocator", [name, printed, options], () => spans.resolveLocator(locator.parseLocator(printed), index, read, options ?? undefined));
  }
}

// Text put together at random.
let state = Number(seed) >>> 0;
const random = () => {
  state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
  return state / 2 ** 32;
};
const pick = (list) => list[Math.floor(random() * list.length)];
const ATOMS = [
  "a", "B", "c", "Table", "Fig.", "Figure", "table", " ", "  ", "\t", "\n", "\n\n", " ", " ", "﻿", "\u001c", "\u0085", " ",
  ".", ",", ":", ";", "'", '"', "‘", "’", "“", "”", "*", "†", "(", ")", "[", "]", "{", "}", "\\", "/", "-", "−",
  "–", "±", "+", "0", "1", "5", "9", "0.5", "12", "e", "E", "%", "é", "É", "İ", "Σ", "ß", "😀",
  "\ud83d", "\ude00", "𐐀", "\f", "↑", "<!-- figure:a -->", "<!-- figure:b -->", "\\t", "\\x41", "\\n", "~0", "~1", "$", "S1", "IV", "x",
];
const JSONISH = ["{", "}", "[", "]", ",", ":", '"a"', '"b c"', "1", "-2.5e3", "0", "true", "false", "null", " ", "\n", '"\\u0041"', '"\\n"', '"\\x"', "01", '"', "\\"];
const NUMBERISH = ["0", "1", "5", "9", ".", "-", "+", "e", "E", " ", "−", "±", "a", ",", "0.5", "49", "50", "99", "\t"];
const made = (pieces, most) => Array.from({ length: Math.floor(random() * most) }, () => pick(pieces)).join("");
const sha = (held) => crypto.createHash("sha256").update(Buffer.from(held, "utf8")).digest("hex");
for (let at = 0; at < Number(texts); at += 1) {
  const a = made(ATOMS, 8);
  const b = made(ATOMS, 8);
  const layer = made(ATOMS, 40);
  const document = made(JSONISH, 10);
  const one = made(NUMBERISH, 8);
  const other = made(NUMBERISH, 8);
  keep("normaliseLabel", [a], () => spans.normaliseLabel(a));
  keep("columnForms", [a], () => spans.columnForms(a));
  keep("valuePresence", [a, b], () => command.valuePresence(a, b));
  keep("valuePresence", [one, other], () => command.valuePresence(one, other));
  keep("unescapeCell", [a], () => officeSheet.unescapeCell(a));
  keep("pointerTokens", [a], () => [spans.pointerTokens(a), spans.renderPointer(spans.pointerTokens(a))]);
  keep("readJson", [document], () => spans.readJson(document));
  keep("countPoints", [a], () => spans.countPoints(a));
  keep("namesTable", [layer, b], () => spans.namesTable(layer, b));
  keep("sheetBlocks", [`\f# sheet\t${a}\n${layer}`], () => address.sheetBlocks(`\f# sheet\t${a}\n${layer}`));
  keep("marks", [layer], () => figures.marks(layer));
  keep("retag", [layer, { a: 12345, b: 0.5 }], () => figures.retag(layer, { a: 12345, b: 0.5 }));
  // The same text as a layer. A name that opens with `layer:` is a call compare_spans.py makes
  // of a layer it indexes itself.
  const normaliser = pick(["pdf-text-layer@1", "text-utf8@1", "europepmc-fulltext-xml@1", "ooxml-sheet-tsv@1"]);
  const index = spans.indexLayer(layer, sha(layer), normaliser);
  const from = Math.floor(random() * 50) - 3;
  const to = Math.floor(random() * 50) - 3;
  const whole = random() < 0.5;
  const part = layer.slice(Math.max(0, from), Math.max(0, from) + 6);
  const every = (read) => index.lines.map((_, line) => read(line));
  keep("layer:spanAt", [layer, normaliser, from, to], () => spans.spanAt(index, from, to));
  keep("layer:spanOfLines", [layer, normaliser, from, to], () => spans.spanOfLines(index, from, to));
  keep("layer:cellsOf", [layer, normaliser], () => every((line) => spans.cellsOf(index, line)));
  keep("layer:fieldsOf", [layer, normaliser], () => every((line) => spans.fieldsOf(index, line, ",")));
  keep("layer:fieldsOfLine", [layer, normaliser], () => every((line) => spans.fieldsOfLine(index, line)));
  keep("layer:windows", [layer, normaliser], () => every((line) => [spans.tableWindow(index, line), spans.widerWindow(index, line), spans.blockAbove(index, line)]));
  keep("layer:captionLines", [layer, normaliser, b], () => [spans.captionLines(index, "Table", b), spans.captionLines(index, "Figure", b)]);
  keep("layer:resolveQuote", [layer, normaliser, b, whole], () => spans.resolveQuote(index, b, whole));
  keep("layer:resolveQuote", [layer, normaliser, part, whole], () => spans.resolveQuote(index, part, whole));
  keep("layer:resolveSectionParagraph", [layer, normaliser, a], () => spans.resolveSectionParagraph(index, a, 1));
  keep("layer:resolvePage", [layer, normaliser], () => spans.resolvePage(index, 1));
  keep("layer:resolveLineRange", [layer, normaliser, from, to], () => spans.resolveLineRange(index, from, to));
  keep("layer:resolveDelimitedCell", [layer, normaliser, from, a], () => spans.resolveDelimitedCell(index, { line: from, column: a }));
  keep("layer:unitOfPoint", [layer, normaliser, from], () => spans.unitOfPoint(index, from));
  const span = spans.spanAt(index, Math.max(0, from), Math.max(0, to));
  const moved = spans.indexLayer(layer + b, sha(layer + b), normaliser);
  keep("layer:reanchor", [layer, normaliser, b, Math.max(0, from), Math.max(0, to)], () => [spans.reanchor(span, index), spans.reanchor(span, moved)]);
}
fs.writeFileSync(out, `${lines.join("\n")}\n`);
console.log(`Wrote ${lines.length} calls with Node ${process.version}.`);
