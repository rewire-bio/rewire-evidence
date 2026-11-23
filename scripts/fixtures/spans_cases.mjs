// Cases for the span resolvers: what the stored results have no example of.
//
// Built-ins only, and the zip writer of layers_cases.mjs. record_spans_fixture.mjs runs each
// case through the TypeScript in the `fixtures` job, and tests/test_spans_recorded.py replays
// every one against the port.
//
// The layers are written here as text, with the name of the normaliser that would have
// written them, because a resolver reads text and a name. The markup is derived by the
// TypeScript when the cases are recorded, which gives the text layer and the grid beside it.
import crypto from "node:crypto";
import { zip } from "./layers_cases.mjs";

const lines = (...rows) => rows.join("\n");
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const many = (count, text) => Array.from({ length: count }, () => text).join("");
const FACE = "😀";
const HIGH = "\ud83d";
const LOW = "\ude00";

// ---------------------------------------------------------------------------------------
// Text layers.

const ALIGNED = lines(
  "A study of things", // 0
  "",
  "Table 1. Accuracy of models", // 2
  "Model        Acc      F1 (%)     AUROC ↑",
  "Alpha        0.50     60.1       0.91",
  "Beta         0.71     80.2       0.93",
  "Beta         0.72     81.0       0.94    v2",
  "Gamma        0.80                0.95",
  "Delta*       0.90     61.5       0.96",
  "",
  "Table 2: Two levels", // 10
  "             Human            Mouse",
  "Model        AUROC    AUPRC   AUROC    AUPRC",
  "Alpha        0.11     0.12    0.13     0.14",
  "Beta         0.21     0.22    0.23     0.24",
  "",
  "Figure 7. A figure whose caption stands above a caption", // 16
  "Table 4. A caption with nothing under it",
  "Table 5. Merged, unaligned and doubled", // 18
  "Model   Acc  F1  AUC      Acc",
  "Alpha   0.5 0.6  0.7      0.8",
  "",
  "Table 6. The same table twice", // 22
  "Model   Score",
  "Alpha   1.5",
  "",
  "Table 6. The same table twice (continued)", // 26
  "Model   Score",
  "Alpha   1.5",
  "Beta    2.5",
  "",
  "Table 8. Unaligned and wide", // 31
  "Model      AccuracyOfThings      F1",
  "Alpha      0.1  0.2              0.3",
  "Beta                                          0.4",
  "Gamma      0.6",
  "",
  "Table 9. Sub headings twice under one", // 37
  "Model      Everything In One Wide Heading",
  "Model      AUROC      AUROC",
  "Alpha      0.1        0.2",
  "",
  "Table 10. Runs over a page", // 42
  "\fModel   Acc",
  "Alpha   0.77",
  "",
  "Supplementary Table S1. With a prefix", // 46
  "Model   Acc",
  "Alpha   0.88",
  "\fSecond page", // 49
  "Model   Score    Rank",
  "Alpha   0.31     1",
  "Beta    0.32     2",
  "",
  "Table 3. Caption under its table", // 54
  "Some text after the caption of table three.",
  "",
  "Fig. 2. A caption of two lines", // 57
  "that goes on here.",
  "",
  "Figure 2 shows nothing much in the text.", // 60
  "Figure 3: first",
  "Figure 3: second",
  "Table 1a. Not table one", // 63
  "Tables 11 and 12 are discussed together",
  "",
  "Table 12. Two columns printed as one field", // 66
  "Model   Acc  F1  AUC",
  "Alpha   0.5 0.6  0.7",
);

const PROSE = lines(
  "Title of the paper", // 0
  "",
  "1. Introduction",
  "",
  "This is the first paragraph of the introduction. It cites (Fig. 6C) before a number 10.3 and ends",
  "on a second line. A second sentence follows.",
  "",
  "The second paragraph, with a value of 0.58 in it; and e.g. a short form. Then more",
  "",
  "Results",
  "",
  "First results paragraph. It says 13,770 things were found.",
  "",
  "Second results paragraph. Scoring real homologs against real homologs gives 0.91 on average and nothing",
  "else is claimed. Final sentence here!",
  "",
  "Methods:",
  "",
  "A paragraph under a line that ends in a colon.",
  "",
  "Discussion",
  "",
  "Results",
  "",
  "A second section named Results, which makes the name ambiguous.",
  "IV. Conclusion",
  "",
  "The only paragraph of the conclusion repeats repeats repeats itself.",
  "",
  `Astral ${FACE} sentence with a face ${FACE} in it and a tail.`,
);

// Tables whose cells stand exactly at an edge: a block above its caption that opens the layer
// while the last line of the layer is a caption, a sub heading that starts where its parent ends
// and one that ends where its parent starts, a cell that does the same to its heading, a cell
// midway between two headings, and two captions of one number whose refusals got equally far.
const TOUCHING = lines(
  "Model   Acc", // 0
  "Alpha   0.5",
  "Table 20. A caption under a table that opens the layer", // 2
  "",
  "Table 21. Sub headings that touch their parents", // 4
  "Model   Wide          Next",
  "Model   AA  BB      CC  DD",
  "Alpha   1   2       3   4",
  "",
  "Table 22. A cell that starts where its heading ends", // 9
  "Model   Acc                 F1",
  "Alpha      0.5              0.6",
  "",
  "Table 23. A cell that ends where its heading starts", // 13
  "Model       Acc             F1",
  "Alpha    0.5                0.6",
  "",
  "Table 24. A cell midway between two headings", // 17
  "Model   Acc   F11",
  "Alpha       7",
  "",
  "Table 26. Two captions of one table, each with a cell that touches nothing", // 21
  "Model   Acc                 F1",
  "Alpha      0.5              0.6",
  "",
  "Table 26. Two captions of one table (continued)", // 25
  "Model   Acc                 F1",
  "Alpha  0   0.5              0.6",
  "",
  "Some text.",
  "Table 25. A caption that is the last line", // 30
);
const PAGED = lines("Page one line one", "", "second paragraph of page one", "\fPage two line one", "", "\fPage three");

const CSV = lines(
  "",
  'model,acc,"f1, macro",note,acc2',
  'Alpha,0.5,0.61,"says ""hi""",1',
  "Beta,0.71,,plain,2",
  "",
  'Gamma,0.9,0.92,"a, b",3',
  "Delta,1.0",
  "Beta,0.72,0.8,again,4",
);
const TSV = lines("tool\tsource\tscore\tscore", "GFSeeker\tSGNex\t0.9\t1", "Arriba\tSGNex\t0.8\t2", "Arriba\tOther\t0.7\t3");
const PLAIN = lines("no delimiter here", "second line");
const BLANK = "\n\n";

const JSON_DOC = JSON.stringify(
  {
    data: {
      full: [
        { model: "evo-1", macro_MCC: 0.5, "cat_MCC.Histone Mod.": 0.25, cat: { "a.b": 1, a: { b: 2 } }, note: null, empty: "" },
        { model: "gena", macro_MCC: 0.75, tags: ["x", "y"], deep: { er: { value: "0.125" } } },
        { model: "gena", macro_MCC: 0.8 },
      ],
      one: { value: 1.5e-3, flag: true, nothing: null, list: [1, 2, 3], text: "café", "a/b": 7, "m~n": 8 },
    },
    results: [{ id: "r1", score: 3 }],
    "": { "": 9 },
  },
  null,
  1,
);
const JSON_DUPLICATE = '{"a": 1, "a": 2, "b": {"c": [10, 20]}}';
const JSON_ESCAPES = '{"k\\u0041\\n\\t\\"\\\\\\/\\b\\f\\r": "v\\ud83d\\ude00", "bad": "\\u12G4", "neg": "\\u-1ab", "none": "\\uzzzz", "hex": "\\u0x1f"}';

const SHEETS = lines(
  "\f# sheet\t1\tScores\tA1:D6",
  "Benchmark\tresults",
  "model\tacc\tf1\tacc",
  "Alpha\t0.5\t0.61\t9",
  "\t0.71\t\t",
  "Beta\t\\\\x\\t\\n\\r\\f\\x41\\q\\x4G\t0.8\t",
  "group\tAlpha\t0.81699999999999995\t",
  "\f# sheet\t2\tscores\t",
  "\f# sheet\tx\tOther Sheet\tA1:B2",
  "a\tb",
  `${FACE}\tafter a face`,
  "\f# sheet\t7\tLast",
);

const ODD = lines("\t", "a  \t  b", "\fpage", "", "x  y", "Table 20. A caption under a block that opens with a blank line", "tail");

export const layers = {
  odd: { normaliser: "pdf-text-layer@1", text: ODD },
  ending: { normaliser: "pdf-text-layer@1", text: "Head\n\npara one.\n\n" },
  endingLines: { normaliser: "html-text@1", text: "Head\npara one\npara two" },
  spaces: { normaliser: "pdf-text-layer@1", text: "one  two\nthree one\ttwo\n" },
  tiny: { normaliser: "html-text@1", text: "ab\ncd" },
  aligned: { normaliser: "pdf-text-layer@1", text: ALIGNED },
  touching: { normaliser: "pdf-text-layer@1", text: TOUCHING },
  alignedAsText: { normaliser: "text-utf8@1", text: ALIGNED },
  prose: { normaliser: "pdf-text-layer@1", text: PROSE },
  proseLines: { normaliser: "europepmc-fulltext-xml@1", text: PROSE },
  paged: { normaliser: "pdf-text-layer@1", text: PAGED },
  csv: { normaliser: "text-utf8@1", text: CSV },
  csvAsPdf: { normaliser: "pdf-text-layer@1", text: CSV },
  tsv: { normaliser: "text-utf8@1", text: TSV },
  plain: { normaliser: "text-utf8@1", text: PLAIN },
  blank: { normaliser: "text-utf8@1", text: BLANK },
  empty: { normaliser: "text-utf8@1", text: "" },
  json: { normaliser: "text-utf8@1", text: JSON_DOC },
  jsonAsPdf: { normaliser: "pdf-text-layer@1", text: JSON_DOC },
  jsonDuplicate: { normaliser: "text-utf8@1", text: JSON_DUPLICATE },
  jsonEscapes: { normaliser: "text-utf8@1", text: JSON_ESCAPES },
  jsonScalar: { normaliser: "text-utf8@1", text: ' "only a string" ' },
  jsonEmptyKeys: { normaliser: "text-utf8@1", text: '{"x": {"": {"": 5, "k": 6}, "k": 7}}' },
  notJson: { normaliser: "text-utf8@1", text: '{"a": 1,}' },
  sheets: { normaliser: "ooxml-sheet-tsv@1", text: SHEETS },
  sheetsAsText: { normaliser: "text-utf8@1", text: SHEETS },
  noSheets: { normaliser: "ooxml-sheet-tsv@1", text: "a\tb\n" },
  astral: { normaliser: "pdf-text-layer@1", text: lines(`a${FACE}b`, `${HIGH}lone ${LOW}halves${HIGH}`, `café ${FACE}${FACE} end`, "", `${LOW}${HIGH}`) },
  long: { normaliser: "pdf-text-layer@1", text: `${many(60, FACE)}MIDDLE${many(60, "é")}${many(120, "z")}` },
  sentence: { normaliser: "pdf-text-layer@1", text: `Opening words here. The value is 0.5 (see Fig. 2) and et al. agree; i.e. it holds. Next one! ${many(950, "x")}. End` },
  // Where a rule turns on one unit. A heading line of exactly 80 units and one of 81, each
  // before a line shaped like a heading that is the last line of the layer.
  bounds: { normaliser: "pdf-text-layer@1", text: lines("Head", "", "para one.", "", `Long${many(73, " ")}Sec`, "", "para two.", "", "Tail") },
  boundsOver: { normaliser: "pdf-text-layer@1", text: lines("Head", "", "para one.", "", `Long${many(74, " ")}Sec`, "", "para two.", "", "Tail") },
  // An abbreviation that is the first word of the layer, a full stop after a digit that ends
  // a sentence, and a last character that is a letter.
  stops: { normaliser: "pdf-text-layer@1", text: "Figs. 2 and 3 show it scored 5. Then it was 0.5 and 9.9 in turn. The text ends in a word" },
  // The first and the last unit of each half of a pair, paired and alone.
  edges: { normaliser: "pdf-text-layer@1", text: `x\ud800\udc00y\udbff\udfffz\ud800w\udbffv\udc00u\udfffend` },
  // Halves with no partner on both sides of a span, more of them than its context holds, and
  // a layer that opens with a second half and ends on a first.
  halves: { normaliser: "pdf-text-layer@1", text: `${many(30, `${HIGH}a${LOW}b`)}MID${many(30, `${HIGH}a${LOW}b`)}` },
  halfEnds: { normaliser: "pdf-text-layer@1", text: `${LOW}a${HIGH}Xb${HIGH}` },
  // A row longer than the header, and fields that are a quotation mark, two and three.
  ragged: { normaliser: "text-utf8@1", text: lines("a,b", "1,2,3", 'x,"', '",y', '"', '"",z', '""",w', '"q"') },
  // A layer whose first unit is a page break.
  pagedFirst: { normaliser: "pdf-text-layer@1", text: "\fsecond page\fthird" },
  // As many sheets as a refusal names, and one more.
  eightSheets: { normaliser: "ooxml-sheet-tsv@1", text: lines(...[1, 2, 3, 4, 5, 6, 7, 8].flatMap((sheet) => [`\f# sheet\t${sheet}\tS${sheet}\tA1:A1`, "v"])) },
  nineSheets: { normaliser: "ooxml-sheet-tsv@1", text: lines(...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((sheet) => [`\f# sheet\t${sheet}\tS${sheet}\tA1:A1`, "v"])) },
};

// ---------------------------------------------------------------------------------------
// Markup, from which the TypeScript derives a text layer and a grid.

const cell = (tag, text, attributes = "") => `<${tag}${attributes}>${text}</${tag}>`;
const tr = (cells) => `<tr>${cells.join("")}</tr>`;
const th = (texts) => texts.map((text) => cell("th", text));
const td = (texts) => texts.map((text) => cell("td", text));
const wrap = (label, caption, head, body) =>
  `<table-wrap>${label === null ? "" : `<label>${label}</label>`}<caption><p>${caption}</p></caption><table><thead>${head.join("")}</thead><tbody>${body.join("")}</tbody></table></table-wrap>`;

const JATS = `<?xml version="1.0"?><article><front><article-meta><title-group><article-title>Grids</article-title></title-group></article-meta></front><body>
<sec><title>Results</title><p>First paragraph of the results, which mentions Table 1 in passing.</p><p>Second paragraph with 13,770 splice junctions (Pearson r = 0.58) in it.</p><p>Third paragraph.</p>
${wrap("Table 1", "Scores by species", [tr([cell("th", "Model", ' rowspan="2"'), cell("th", "Human", ' colspan="2"'), cell("th", "Mouse", ' colspan="2"')]), tr(th(["AUROC", "AUPRC", "AUROC", "AUPRC"]))], [tr(td(["Alpha", "0.11", "0.12", "0.13", "0.14"])), tr(td(["Beta", "0.21", "", "0.23", "0.24"])), tr(td(["Beta", "0.31", "0.32", "0.33", "0.34 v2"])), tr([cell("td", "Gamma"), cell("td", "0.41")])])}
${wrap("Table 2", "One level, a column printed twice", [tr(th(["Group", "Model", "Acc", "Acc", `F1 ${FACE}`]))], [tr([cell("td", "Small", ' rowspan="2"'), ...td(["Alpha", "0.5", "0.6", `0.7${FACE}`])]), tr(td(["Beta", "0.8", "0.9", "1.0"]))])}
${wrap("Table 2", "The same label again", [tr(th(["Model", "Other"]))], [tr(td(["Alpha", "7"]))])}
${wrap(null, "Table 3. Labelled by its caption alone", [tr(th(["Model", "Acc"]))], [tr(td(["Alpha", "0.55"]))])}
${wrap("Table 5", "Both hold the cell", [tr(th(["Model", "Acc"]))], [tr(td(["Alpha", "0.61"]))])}
${wrap("Table 5", "Both hold the cell", [tr(th(["Model", "Acc"]))], [tr(td(["Alpha", "0.62"]))])}
</sec><sec><title>Methods</title><p>Only paragraph of the methods.</p></sec></body></article>`;

const table = (head, body) => `<table><thead>${head.join("")}</thead><tbody>${body.join("")}</tbody></table>`;
const HTML = `<!DOCTYPE html><html><head><title>Page</title></head><body><h1>A page of tables</h1>
<p>Table 1. Scores on the page</p>
${table([tr(th(["Model", "Acc (%)", "F1"]))], [tr(td(["Alpha", "50", "0.6"])), tr(td(["Beta", "71", "0.8"]))])}
<p>Some text between the tables.</p>
${table([tr(th(["Model", "Rank"]))], [tr(td(["Alpha", "1"]))])}
<p>Table 2. Caption under its table</p>
<p>Table 3 is mentioned in a sentence and has no table.</p>
<p>Table 4. Two tables under one caption</p>
${table([tr(th(["Model", "Acc"]))], [tr(td(["Alpha", "0.1"]))])}
${table([tr(th(["Model", "Acc"]))], [tr(td(["Alpha", "0.2"]))])}
<p>Table (a). A label that is no number</p>
${table([tr(th(["Model", "Acc"]))], [tr(td(["Alpha", "0.3"]))])}
<p>Table 6. A caption with a caption straight after it<br>Table 7. The caption after it</p>
${table([tr(th(["Model", "Acc"]))], [tr(td(["Alpha", "0.4"]))])}
<h2>Notes</h2><p>The last paragraph.</p></body></html>`;

const HTML_NO_TABLE = "<!DOCTYPE html><html><body><h1>Nothing</h1><p>Table 1. A caption and no table</p></body></html>";

export const markup = { jats: JATS, html: HTML, htmlNoTable: HTML_NO_TABLE };

// ---------------------------------------------------------------------------------------
// The calls.

const table1 = (row, column, more = {}) => ({ anchor: ["table", "1"], row, column, ...more });
const anchored = (value, row, column, more = {}) => ({ anchor: ["table", value], row, column, ...more });
const TEXTS = [
  "",
  " ",
  "Plain",
  "  ‘Quoted’ “Label”  ",
  "'it''s' \"x\"",
  "Stars*†‡§¶",
  " .,:; inner ; text :;,. ",
  "a  b﻿c\u001cd\u0085e f",
  "İstanbul ΣΟΣ STRASSE ẞ",
  "AUROC ↑",
  "F1 (%)",
  "F1 [macro] (%)",
  "(only a bracket)",
  "Acc (a) (b)",
  "x ↓↕⇑⇓ y",
  `face ${FACE} 𐐀 Deseret`,
  `${HIGH} half`,
  "MiXeD   Case\tTabs\nLines",
];

const offsetCalls = [
  ...["aligned", "astral", "long", "empty", "blank", "prose", "sheets"].map((name) => ["index", name]),
  ...["", "a", FACE, `${FACE}${FACE}`, HIGH, LOW, `${LOW}${HIGH}`, `${HIGH}${HIGH}${LOW}`, `a${FACE}b`, "café"].map((text) => ["countPoints", text]),
  ...[[0, 0], [0, 1], [1, 2], [1, 3], [2, 3], [0, 4], [3, 3], [5, 7], [4, 12], [-3, 2], [2, 999], [9, 4], [20, 25], [26, 28], [27, 29], [0, 60]].map(([from, to]) => ["spanAt", "astral", from, to]),
  ...[[0, 5], [60, 126], [119, 127], [120, 126], [121, 125], [126, 300], [0, 366], [245, 250], [1, 1]].map(([from, to]) => ["spanAt", "long", from, to]),
  ["spanAt", "empty", 0, 0],
  ["spanAt", "empty", 0, 5],
  ...["edges", "halves", "halfEnds"].map((name) => ["index", name]),
  ...["\ud800", "\udbff", "\udc00", "\udfff", "\ud7ff\udc00", "\udc00\ue000", "\ud800\udc00", "\udbff\udfff", "\ud800\udbff", "\udfff\udc00"].map((text) => ["countPoints", text]),
  ...[[0, 2], [1, 2], [2, 3], [2, 2], [3, 5], [4, 5], [5, 6], [5, 5], [6, 8], [7, 9], [9, 10], [11, 12], [13, 14], [0, 17]].map(([from, to]) => ["spanAt", "edges", from, to]),
  ...[[120, 123], [121, 122], [0, 1], [242, 243], [60, 61], [119, 124]].map(([from, to]) => ["spanAt", "halves", from, to]),
  ...[[0, 1], [3, 4], [1, 2], [5, 6], [0, 6], [4, 5]].map(([from, to]) => ["spanAt", "halfEnds", from, to]),
  ...[0, 1, 2, 3, 4, 5, 6, 7].map((unit) => ["lineAt", "halfEnds", unit]),
  ["spanAt", "blank", 1, 2],
  ...[[0, 0], [0, 2], [1, 1], [2, 1], [-1, 0], [3, 99], [99, 99], [4, 4]].map(([from, to]) => ["spanOfLines", "astral", from, to]),
  ["spanOfLines", "empty", 0, 0],
  ...[0, 1, 3, 4, 5, 6, 20, 30, 43, 44].map((unit) => ["lineAt", "astral", unit]),
];

const tableCalls = [
  ...TEXTS.map((text) => ["normaliseLabel", text]),
  ...TEXTS.map((text) => ["columnForms", text]),
  ...["", "a.b*c+d?e^f$g{h}i(j)k|l[m]n\\o", "Table 1", "S1-a/b"].map((text) => ["escape", text]),
  ...[0, 1, 3, 4, 7, 11, 43, 49].map((line) => ["cellsOf", "aligned", line]),
  ["cellsOf", "astral", 2],
  ["cellsOf", "odd", 0],
  ["cellsOf", "odd", 1],
  ["windows", "odd", 5],
  ...[
    ["Table", "1"],
    ["Table", "2"],
    ["Table", "3"],
    ["Table", "4"],
    ["Table", "6"],
    ["Table", "S1"],
    ["Table", "s1"],
    ["Table", "1a"],
    ["Table", "11"],
    ["Table", "99"],
    ["Table", ""],
    ["Table", "1."],
    ["Table", "("],
    ["Figure", "2"],
    ["Figure", "3"],
    ["Figure", "7"],
    ["Figure", "9"],
  ].map(([word, value]) => ["captionLines", "aligned", word, value]),
  ...[0, 2, 10, 16, 17, 18, 26, 42, 46, 49, 54, 63, 64].map((caption) => ["windows", "aligned", caption]),
  ["windows", "empty", 0],
  ["windows", "paged", 3],
  ["windows", "paged", 5],
  ...[
    [{ from: 0, to: 2 }, { from: 2, to: 4 }],
    [{ from: 0, to: 3 }, { from: 2, to: 4 }],
    [{ from: 2, to: 4 }, { from: 0, to: 3 }],
    [{ from: 5, to: 6 }, { from: 0, to: 3 }],
    [{ from: 1, to: 1 }, { from: 0, to: 3 }],
  ].map(([a, b]) => ["overlap", a, b]),
  ...[{ text: "", from: 0 }, { text: "one", from: 4 }, { text: "one two  three", from: 10 }, { text: " lead", from: 0 }].map((field) => ["tokensOf", field]),
  ...[
    { bands: [{ from: 0, to: 5 }, { from: 8, to: 11 }, { from: 13, to: 15 }], at: 1, field: { text: "0.5 0.6", from: 8, to: 15 } },
    { bands: [{ from: 0, to: 5 }, { from: 8, to: 11 }, { from: 13, to: 15 }], at: 1, field: { text: "0.5", from: 8, to: 11 } },
    { bands: [{ from: 0, to: 5 }, { from: 8, to: 11 }], at: 1, field: { text: "0.5 +/- 0.1", from: 8, to: 19 } },
    { bands: [], at: 0, field: { text: "x y", from: 0, to: 3 } },
    { bands: [{ from: 0, to: 2 }, { from: 2, to: 4 }, { from: 4, to: 6 }], at: 0, field: { text: "a b c", from: 0, to: 5 } },
    { bands: [{ from: 0, to: 9 }, { from: 0, to: 9 }], at: 0, field: { text: "a b", from: 0, to: 3 } },
  ].map((input) => ["cellWidth", input]),
  // One table, every way a row and a column cross or fail to.
  ["resolveTableCell", "aligned", table1("Alpha", "Acc")],
  ["resolveTableCell", "aligned", table1("alpha", "acc")],
  ["resolveTableCell", "aligned", table1("Alpha", "F1")],
  ["resolveTableCell", "aligned", table1("Alpha", "F1 (%)")],
  ["resolveTableCell", "aligned", table1("Alpha", "AUROC")],
  ["resolveTableCell", "aligned", table1("Delta", "Acc")],
  ["resolveTableCell", "aligned", table1("Alpha", "Model")],
  ["resolveTableCell", "aligned", table1("Beta", "Acc")],
  ["resolveTableCell", "aligned", table1("Beta", "Acc", { keys: ["v2"] })],
  ["resolveTableCell", "aligned", table1("Beta", "Acc", { keys: ["V2", "", "Beta", "acc"] })],
  ["resolveTableCell", "aligned", table1("Beta", "Acc", { keys: ["zzz", "v2"] })],
  ["resolveTableCell", "aligned", table1("Gamma", "F1")],
  ["resolveTableCell", "aligned", table1("Nobody", "Acc")],
  ["resolveTableCell", "aligned", table1("Alpha", "Nothing")],
  ["resolveTableCell", "aligned", table1("Alpha", "Acc", { subColumns: [] })],
  ["resolveTableCell", "aligned", anchored("2", "Alpha", "Human", { subColumns: ["AUROC"] })],
  ["resolveTableCell", "aligned", anchored("2", "Beta", "Mouse", { subColumns: ["AUPRC"] })],
  ["resolveTableCell", "aligned", anchored("2", "Alpha", "Human", { subColumns: ["F1"] })],
  ["resolveTableCell", "aligned", anchored("2", "Alpha", "Human", { subColumns: ["AUROC", "x"] })],
  ["resolveTableCell", "aligned", anchored("2", "Alpha", "AUROC")],
  ["resolveTableCell", "aligned", anchored("3", "Alpha", "Score")],
  ["resolveTableCell", "aligned", anchored("3", "Beta", "Rank")],
  ["resolveTableCell", "aligned", anchored("3", "Alpha", "Nothing")],
  ["resolveTableCell", "aligned", anchored("4", "Alpha", "Acc")],
  ["resolveTableCell", "aligned", anchored("5", "Alpha", "Acc")],
  ["resolveTableCell", "aligned", anchored("5", "Alpha", "F1")],
  ["resolveTableCell", "aligned", anchored("5", "Alpha", "AUC")],
  ["resolveTableCell", "aligned", anchored("6", "Alpha", "Score")],
  ["resolveTableCell", "aligned", anchored("6", "Beta", "Score")],
  ["resolveTableCell", "aligned", anchored("8", "Alpha", "AccuracyOfThings")],
  ["resolveTableCell", "aligned", anchored("8", "Beta", "F1")],
  ["resolveTableCell", "aligned", anchored("8", "Gamma", "F1")],
  ["resolveTableCell", "aligned", anchored("8", "Gamma", "AccuracyOfThings")],
  ["resolveTableCell", "aligned", anchored("9", "Alpha", "Everything In One Wide Heading", { subColumns: ["AUROC"] })],
  ["resolveTableCell", "aligned", anchored("10", "Alpha", "Acc")],
  ["resolveTableCell", "aligned", anchored("12", "Alpha", "Acc")],
  ["resolveTableCell", "aligned", anchored("12", "Alpha", "AUC")],
  ["resolveTableCell", "aligned", anchored("S1", "Alpha", "Acc")],
  ["resolveTableCell", "aligned", anchored("99", "Alpha", "Acc")],
  ["resolveTableCell", "aligned", { anchor: ["figure", "7"], row: "Alpha", column: "Acc" }],
  ["resolveTableCell", "aligned", { anchor: ["figure", "2"], row: "Alpha", column: "Acc" }],
  ["resolveTableCell", "alignedAsText", table1("Alpha", "Acc")],
  ...[
    anchored("20", "Alpha", "Acc"),
    anchored("21", "Alpha", "Wide", { subColumns: ["AA"] }),
    anchored("21", "Alpha", "Wide", { subColumns: ["BB"] }),
    anchored("21", "Alpha", "Next", { subColumns: ["DD"] }),
    anchored("21", "Alpha", "Next", { subColumns: ["CC"] }),
    anchored("22", "Alpha", "Acc"),
    anchored("22", "Alpha", "F1"),
    anchored("23", "Alpha", "Acc"),
    anchored("23", "Alpha", "F1"),
    anchored("24", "Alpha", "Acc"),
    anchored("24", "Alpha", "F11"),
    anchored("25", "Alpha", "Acc"),
    anchored("26", "Alpha", "Acc"),
    anchored("26", "Alpha", "F1"),
  ].map((request) => ["resolveTableCell", "touching", request]),
  ...["2", "3", "7", "9"].map((value) => ["resolveFigureCaption", "aligned", value]),
  ["resolveFigureCaption", "prose", "1"],
];

const regionCalls = [
  ...["a\tb,c", "a,b", "a b", ""].map((line) => ["delimiterOf", line]),
  ...[1, 2, 3, 5, 6, 0].map((line) => ["fieldsOf", "csv", line, ","]),
  ["fieldsOf", "tsv", 1, "\t"],
  ["fieldsOf", "csv", 2, "\t"],
  ...["A", "Z", "AA", "AZ", "zz", "AAA", "", "a1"].map((letters) => ["columnIndex", letters]),
  ...[
    { cell: "B3" },
    { cell: "b3" },
    { cell: "C4" },
    { cell: "E3" },
    { cell: "F3" },
    { cell: "A1" },
    { cell: "A2" },
    { cell: "A9" },
    { cell: "A0" },
    { cell: "AAAA1" },
    { cell: "3B" },
    { cell: "", line: 3, column: "acc" },
    { line: 3, column: "acc" },
    { line: 3, column: "ACC " },
    { line: 3, column: "f1, macro" },
    { line: 3, column: "note" },
    { line: 4, column: "f1, macro" },
    { line: 7, column: "note" },
    { line: 3, column: "missing" },
    { line: 3 },
    { line: 0, column: "acc" },
    { line: 99, column: "acc" },
    { line: "NaN", column: "acc" },
    { line: 2.5, column: "acc" },
    { column: "acc" },
    {},
  ].map((request) => ["resolveDelimitedCell", "csv", request]),
  ["resolveDelimitedCell", "tsv", { line: 2, column: "score" }],
  ["resolveDelimitedCell", "tsv", { cell: "C2" }],
  ["resolveDelimitedCell", "plain", { cell: "A1" }],
  ["resolveDelimitedCell", "csvAsPdf", { cell: "A1" }],
  ["resolveDelimitedCell", "empty", { cell: "A1" }],
  ["resolveDelimitedCell", "blank", { cell: "A1" }],
  ...[1, 2, 3, 4, 5, 6, 7].map((line) => ["fieldsOf", "ragged", line, ","]),
  ...[{ cell: "C2" }, { cell: "B2" }, { cell: "B3" }, { cell: "A4" }, { cell: "A5" }, { cell: "A6" }, { cell: "A7" }, { cell: "A8" }, { line: 2, column: "b" }].map((request) => ["resolveDelimitedCell", "ragged", request]),
  // A key on the first column, on a column the row is too short for, and a value under the wrong heading.
  ...[
    { column: "acc", constraints: [{ key: "model", value: "0.5" }] },
    { column: "acc", constraints: [{ key: "f1, macro", value: "0.92" }] },
    { column: "acc", constraints: [{ key: "acc", value: "1.0" }] },
    { column: "f1, macro", constraints: [{ key: "model", value: "Delta" }] },
    { column: "acc", constraints: [{ key: "model", value: "Delta" }] },
    { column: "model", constraints: [{ key: "acc2", value: "3" }] },
  ].map((request) => ["resolveKeyedRow", "csv", request]),
  ...[1, 2, 3, 4].map((page) => ["resolvePage", "pagedFirst", page]),
  ...[
    { column: "acc", constraints: [{ key: null, value: "Alpha" }] },
    { column: "acc", constraints: [{ key: "model", value: "alpha" }] },
    { column: "acc", constraints: [{ key: "nothing", value: "Gamma" }] },
    { column: "acc", constraints: [{ key: null, value: "Beta" }] },
    { column: "acc", constraints: [{ key: null, value: "Beta" }, { key: "note", value: "again" }] },
    { column: "f1, macro", constraints: [{ key: "note", value: "plain" }] },
    { column: "note", constraints: [{ key: "model", value: "Delta" }] },
    { column: "acc", constraints: [{ key: "model", value: "Nobody" }] },
    { column: "acc", constraints: [{ key: "acc2", value: "9" }] },
    { column: "nothing", constraints: [{ key: null, value: "Alpha" }] },
    { column: "acc", constraints: [] },
  ].map((request) => ["resolveKeyedRow", "csv", request]),
  ["resolveKeyedRow", "tsv", { column: "score", constraints: [{ key: "tool", value: "GFSeeker" }] }],
  ["resolveKeyedRow", "tsv", { column: "source", constraints: [{ key: "tool", value: "Arriba" }, { key: "Source", value: "sgnex" }] }],
  ["resolveKeyedRow", "tsv", { column: "source", constraints: [{ key: "tool", value: "Arriba" }] }],
  ["resolveKeyedRow", "plain", { column: "a", constraints: [{ key: null, value: "x" }] }],
  ["resolveKeyedRow", "csvAsPdf", { column: "a", constraints: [{ key: null, value: "x" }] }],
  ...[[1], [3], [3, 5], [5, 3], [2, 99], [8], [9], [0], [-1], [1.5], ["NaN"], ["Infinity"], [2, "NaN"], [2, 2.5], [7, 7.5], [8, 8.5], [2, "Infinity"], [3, "-Infinity"]].map((given) => ["resolveLineRange", "csv", ...given]),
  ["resolveLineRange", "empty", 1],
  ...[1, 2, 3, 4, 0, -2, 1.5, "NaN", "Infinity", 1e30].map((page) => ["resolvePage", "paged", page]),
  ["resolvePage", "csv", 1],
];

const proseCalls = [
  ...[
    ["It says 13,770 things were found.", false],
    ["it says 13,770 things were found.", false],
    ["ends on a second line", false],
    ["ends   on\ta second\n line", false],
    ["repeats", false],
    ["Results", false],
    ["abc", false],
    ["   ab   ", false],
    ["not in the layer at all", false],
    ["a value of 0.58 in it", true],
    ["Scoring real homologs", true],
    ["It cites", true],
    ["Final sentence", true],
    ["A second sentence", true],
    ["Then more", true],
    ["The only paragraph", true],
    [`Astral ${FACE} sentence`, false],
    [`a face ${FACE} in`, true],
    ["(Fig. 6C)", false],
    ["a.b*c", false],
  ].map(([quote, whole]) => ["resolveQuote", "prose", quote, whole]),
  ["resolveQuote", "sentence", "Opening words", true],
  ["resolveQuote", "sentence", "The value is", true],
  ["resolveQuote", "sentence", "Next one", true],
  ["resolveQuote", "sentence", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", true],
  ["resolveQuote", "empty", "something", false],
  // Four units are a quote and three are not.
  ...[["Figs", true], ["Figs", false], ["Fig", true], [" Figs ", true], ["5. T", true], ["was 0.5", true], ["and 9", true], ["Then it was", true], ["The text ends", true], ["in turn", true], ["show", true]].map(([quote, whole]) => ["resolveQuote", "stops", quote, whole]),
  ...[
    ["Introduction", 1],
    ["introduction", 2],
    ["Introduction", 3],
    ["1. Introduction", 1],
    ["Results", 1],
    ["Methods", 1],
    ["Methods:", 1],
    ["Discussion", 1],
    ["Conclusion", 1],
    ["Conclusion", 2],
    ["Title of the paper", 1],
    ["Title of the paper", 4],
    ["Nowhere", 1],
    ["Introduction", 0],
    ["Introduction", 1.5],
    ["Introduction", "NaN"],
    ["Introduction", "Infinity"],
  ].flatMap(([section, paragraph]) => [
    ["resolveSectionParagraph", "prose", section, paragraph],
    ["resolveSectionParagraph", "proseLines", section, paragraph],
  ]),
  ["resolveSectionParagraph", "empty", "x", 1],
  ...[1, 2].map((paragraph) => ["resolveSectionParagraph", "ending", "Head", paragraph]),
  ...["bounds", "boundsOver"].flatMap((name) => [
    ...[1, 2, 3].map((paragraph) => ["resolveSectionParagraph", name, "Head", paragraph]),
    ...[1, 2].map((paragraph) => ["resolveSectionParagraph", name, "Long Sec", paragraph]),
    ["resolveSectionParagraph", name, "Tail", 1],
  ]),
  ...[1, 2, 3].map((paragraph) => ["resolveSectionParagraph", "endingLines", "Head", paragraph]),
  ["reanchor", "spaces", { start: 0, end: 7, byte_start: 0, byte_end: 7, exact: "one two" }],
  ["reanchor", "spaces", { start: 0, end: 7, byte_start: 0, byte_end: 7, exact: "three  one" }],
  // A span taken on one layer and looked for on the same one, and on one that moved.
  ["reanchorAfter", "prose", "prose", 30, 45],
  ["reanchorAfter", "prose", "proseLines", 30, 45],
  ["reanchorAfter", "astral", "astral", 0, 4],
  ["reanchorAfter", "csv", "prose", 5, 12],
  ["reanchor", "prose", { start: 0, end: 5, byte_start: 0, byte_end: 5, exact: "Title" }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "Title" }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "repeats" }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "repeats", prefix: "conclusion ", suffix: " repeats repeats itself." }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "repeats", prefix: "nowhere ", suffix: " to be found" }],
  // The context on one side alone, where the other side would not place the quote.
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "repeats", prefix: "repeats repeats ", suffix: "" }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "repeats", prefix: "", suffix: " itself." }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "repeats", prefix: "repeats repeats " }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "repeats", suffix: " itself." }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "Results" }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "ends on a second line." }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "repeats  repeats" }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "absent text" }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: "" }],
  ["reanchor", "prose", { start: 3, end: 8, byte_start: 3, byte_end: 8, exact: " " }],
  ["reanchor", "prose", { start: 0, end: 5, byte_start: 0, byte_end: 5, exact: "Title", layer_sha256: many(64, "0") }],
  ["reanchor", "prose", { start: -1, end: 5, byte_start: 0, byte_end: 5, exact: "Title" }],
  ["reanchor", "prose", { start: 0, end: 99999, byte_start: 0, byte_end: 5, exact: "Title" }],
  ["reanchor", "empty", { start: 0, end: 0, byte_start: 0, byte_end: 0, exact: "" }],
  ["reanchor", "empty", { start: 0, end: 0, byte_start: 0, byte_end: 0, exact: "x" }],
  // A quote that is all whitespace is looked for with its whitespace free, which is a pattern of nothing, and a layer of nothing holds that once.
  ["reanchor", "empty", { start: 0, end: 0, byte_start: 0, byte_end: 0, exact: " " }],
  ["reanchor", "empty", { start: 0, end: 0, byte_start: 0, byte_end: 0, exact: " \n " }],
  ["reanchor", "tiny", { start: 0, end: 0, byte_start: 0, byte_end: 0, exact: "  " }],
  ...[[0, 3], [1, 2], [2, 3], [3, 10], [9, 12], [0, 99], [-1, 2], [16, 17], [25, 26], [26, 27]].map(([start, end]) => ["codePointSlice", "astral", start, end]),
  ["codePointSlice", "empty", 0, 0],
  ["codePointSlice", "blank", 1, 2],
  // A first half with no partner is stepped over as two units, so the units of a layer can run out before its characters do.
  ...[["halfEnds", 4, 5], ["halfEnds", 0, 5], ["halfEnds", 5, 6], ["halfEnds", 0, 6], ["halves", 100, 200], ["halves", 0, 121], ["halves", 0, 122], ["halves", 242, 243]].map(([name, from, to]) => ["codePointSlice", name, from, to]),
];

const DOCUMENTS = [
  "{}",
  "[]",
  " { } ",
  "[ ]",
  "null",
  "true",
  "false",
  "0",
  "-0",
  "-12.5e+3",
  "1E-2",
  "01",
  "1.",
  ".5",
  "-",
  "+1",
  "1e",
  "nul",
  "truex",
  '""',
  '"a"',
  '"a',
  '"a\\"',
  '"\\x"',
  '"\\',
  '"\\u00e9\\u0041"',
  '"\\uD83D\\uDE00"',
  '"\\u"',
  '"\\u12"',
  '"\\u12" ',
  '"\\u 41"',
  '"tab\there"',
  '{"a":1}',
  '{"a":1,"a":2}',
  '{"a" : [1, {"b": null}], "c": "d"}',
  '{"a":1,}',
  '{"a" 1}',
  "{a:1}",
  '{"a":}',
  '{"a":1 "b":2}',
  "[1,]",
  "[1 2]",
  "[,1]",
  "[1,2",
  '{"a":1',
  "{} x",
  "{}{}",
  "﻿{}",
  " {}",
  " \n\r\t[\n1\r,\t2 ]\n",
  `[${many(47, "1")}]`,
  `[${many(48, "1")}]`,
  `[${many(49, "1")}]`,
  `[0.${many(60, "1")}]`,
  `"${FACE}${HIGH}"`,
  `${many(600, "[")}1${many(600, "]")}`,
  "",
  " ",
];
const jsonCalls = [
  ...DOCUMENTS.map((text) => ["readJson", text]),
  ["readJson", JSON_ESCAPES],
  ...["/a/b/0", "/", "", "  /a~1b/m~0n/~01  ", "a.b[0]", "$.a.b[0][12]", "root.a", "$", "data.full[1].deep", "a..b", "[3]", "a[b].c[1]x[2]", "x.$", "root", "a.[1]"].map((path) => ["pointerTokens", path]),
  ...[[], ["data"], ["data", "full"], ["data", "full", "1"], ["data", "full", "01"], ["data", "full", "9"], ["data", "full", "x"], ["data", "one", "value", "x"], ["data", "missing"], ["", ""], ["data", "one", "list", "2"], ["data", "full", "1", "tags"]].map((tokens) => ["walk", "json", tokens]),
  // An index that is the length of its array, and one past the last.
  ...[["data", "full", "3"], ["data", "full", "2"], ["data", "one", "list", "3"], ["results", "1"], ["results", "0"]].map((tokens) => ["walk", "json", tokens]),
  ["walk", "jsonDuplicate", ["a"]],
  ["walk", "jsonDuplicate", ["b", "c", "1"]],
  ...[
    { path: "/data/one/value", selectors: [] },
    { path: "data.one.value", selectors: [] },
    { path: "$.data.one.flag", selectors: [] },
    { path: "/data/one/nothing", selectors: [] },
    { path: "/data/full/0/empty", selectors: [] },
    { path: "/data/one/text", selectors: [] },
    { path: "/data/one/a~1b", selectors: [] },
    { path: "/data/one/m~0n", selectors: [] },
    { path: "/data/one", selectors: [] },
    { path: "/data/one/list", selectors: [] },
    { path: "/data/one/list/1", selectors: [] },
    { path: "/data/one/missing", selectors: [] },
    { path: "/data/one/list/9", selectors: [] },
    { path: "/data/one/value/deeper", selectors: [] },
    { path: "/", selectors: [] },
    { path: "", selectors: [] },
    { path: "$", selectors: [] },
    { path: "/results", selectors: [] },
    { path: "/data/full", selectors: [{ key: "model", value: "evo-1", role: "other" }, { key: "key", value: "macro_MCC", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "EVO-1 ", role: "other" }, { key: "key", value: "cat_MCC.Histone Mod.", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "evo-1", role: "row" }, { key: "key", value: "cat.a.b", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "evo-1", role: "row" }, { key: "key", value: "cat", role: "column" }, { key: "key", value: "a", role: "column" }, { key: "key", value: "b", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "evo-1", role: "row" }, { key: "key", value: "cat", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "evo-1", role: "row" }, { key: "key", value: "note", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "evo-1", role: "row" }, { key: "key", value: "nothing", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "gena", role: "row" }, { key: "key", value: "macro_MCC", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "gena", role: "row" }, { key: "score", value: "0.75", role: "other" }, { key: "key", value: "deep.er.value", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "nobody", role: "row" }] },
    { path: "/data/full", selectors: [{ key: "key", value: "macro_MCC", role: "column" }] },
    { path: "/data/full", selectors: [{ key: "model", value: "evo-1", role: "row" }] },
    { path: "/data/full/1/macro_MCC", selectors: [{ key: "model", value: "gena", role: "other" }] },
    { path: "/data/full/1/macro_MCC", selectors: [{ key: "model", value: "evo-1", role: "other" }] },
    { path: "/data/full/1", selectors: [{ key: "key", value: "tags", role: "column" }] },
    { path: "/results", selectors: [{ key: "id", value: "r1", role: "other" }, { key: "key", value: "score", role: "column" }] },
    // A pointer of two tokens and of one, each with a selector the pointer did not use, and a
    // value that opens with a full stop.
    { path: "//", selectors: [{ key: "k", value: "9", role: "other" }] },
    { path: "//", selectors: [{ key: "k", value: "8", role: "other" }] },
    { path: "/results/0/score", selectors: [{ key: "id", value: "r1", role: "other" }] },
    { path: "/results/0", selectors: [{ key: "key", value: "score", role: "column" }, { key: "id", value: "r1", role: "other" }] },
    { path: "/data/one", selectors: [{ key: "key", value: ".value", role: "column" }] },
    { path: "/data/one", selectors: [{ key: "key", value: "value.", role: "column" }] },
    { path: "/data/full/0", selectors: [{ key: "key", value: "cat.a.b", role: "column" }] },
    { path: "/data/full/3", selectors: [] },
    { path: "/", selectors: [{ key: "key", value: "", role: "column" }] },
  ].map((request) => ["resolveJsonPointer", "json", request]),
  // A key that is the empty text, read as the part of a path before its first full stop.
  ...[".", ".k", "..", "k"].map((value) => ["resolveJsonPointer", "jsonEmptyKeys", { path: "/x", selectors: [{ key: "key", value, role: "column" }] }]),
  ["resolveJsonPointer", "jsonDuplicate", { path: "/a", selectors: [] }],
  ["resolveJsonPointer", "jsonDuplicate", { path: "/b/c/1", selectors: [] }],
  ["resolveJsonPointer", "jsonScalar", { path: "/x", selectors: [] }],
  ["resolveJsonPointer", "notJson", { path: "/a", selectors: [] }],
  ["resolveJsonPointer", "jsonAsPdf", { path: "/data/one/value", selectors: [] }],
  ["resolveJsonPointer", "jsonEscapes", { path: "/bad", selectors: [] }],
];

const FIELDS = ["", "plain", "a\\\\b", "\\t\\n\\r\\f", "\\x41\\x7f\\x00", "\\x4", "\\x4G", "\\X41", "\\q", "trailing\\", "\\\\\\", `${FACE}\\x${FACE}`];
const sheetCalls = [
  ...FIELDS.map((text) => ["unescapeCell", text]),
  ...[SHEETS, "", "no blocks\n", "\f# sheet\t\t\t", "\f# sheet\t-3\tneg\n\f# sheet\t1e3\texp\n\f# sheet\t 2\tpad", "x\n\f# sheet\t1\tA\n", `\f# sheet\t1\t${FACE}\n${FACE}\tb\n`].map((text) => ["sheetBlocks", text]),
  ...["Scores", "scores", "SCORES", "Other Sheet", "other sheet", "missing", "", 1, 2, 3, 7, 9].map((wanted) => ["findBlock", SHEETS, wanted]),
  ...[
    ["Scores", 1, 1],
    ["Scores", 3, 2],
    ["Scores", 4, 1],
    ["Scores", 4, 4],
    ["Scores", 4, 5],
    ["Scores", 5, 2],
    ["Scores", 6, 3],
    ["Scores", 7, 1],
    ["Scores", 0, 1],
    ["Scores", 1, 0],
    ["Scores", 1.5, 1],
    ["Scores", 1, "NaN"],
    ["scores", 1, 1],
    ["SCORES", 1, 1],
    ["Other Sheet", 2, 1],
    ["Other Sheet", 2, 2],
    ["Other Sheet", 3, 1],
    ["missing", 1, 1],
    [1, 2, 3],
    [3, 1, 2],
    [9, 1, 1],
    [7, 1, 1],
  ].map(([wanted, row, column]) => ["cellRange", SHEETS, wanted, row, column]),
  ["cellRange", "", "Scores", 1, 1],
  ...["B3", "$b$3", " c4 ", "ZZ1", "A0", "3B", ""].map((reference) => ["cellRangeByReference", SHEETS, "Scores", reference]),
  ...[[1, 1], [12, 27], [3, 16384]].map(([row, column]) => ["reference", row, column]),
  ["reference", 1, 0],
  ...[0, 1, 4, 10, 11, 12, 99, -1].map((line) => ["fieldsOfLine", "sheets", line]),
  ["blocksOf", "sheets"],
  ["blocksOf", "noSheets"],
  ...[
    "Supplementary Material 2, sheet 'Scores', cell B3 (row 'Alpha', column 'acc')",
    "Data file S2 sheet Scores, row 3 (model 'Alpha'), column C 'f1'",
    "sheet 'Scores', row 3, column 'acc'",
    "sheet 'Scores', row 3, column zz",
    "sheet 'Scores', row 3, column ABCD",
    "sheet 'Scores', row 0, column B",
    "sheet 'Scores', rows '3.', column B",
    "sheet 'Scores', row 'Alpha', column 'f1'",
    "Supporting Information sheet Scores, C4; group 'x'; row 'Alpha'; column 'f1'",
    "Scores!B3",
    "'Other Sheet'!A2",
    "cell B3",
    "data sheet = 'Scores', row 3, column B",
    "sheet 'Scores', row 3",
    "sheet 'Scores', column B",
    "row 3, column B",
    "sheet '', row 3, column B",
    "sheet 'Scores', row 3, column B, block 'top', metric 'acc', model 'Alpha', model ''",
    "sheet 'Scores', row 3, column B, tool 'Scores'",
    "Table 1, row 'Alpha', column 'Acc'",
    "sheet 'Scores', row 3, column 'acc', column 'f1'",
    "sheet 'Scores', row 3, row '5', column B",
    "sheet 'Scores', row '0', column B",
    // No sheet, a sheet named twice, and a column named by two letters.
    "row 3, column B",
    "model 'Alpha', row 3, column B",
    "sheet 'Scores', workbook sheet 'Other', row 3, column B",
    "sheet 'Scores', row 3, column B 'acc', column C 'f1'",
    "workbook sheet 'Scores', workbook sheet 'Other', row 3, column C",
    "",
  ].map((printed) => ["sheetRequest", printed]),
  ["sheetRequestParsed", { anchors: [{ kind: "sheet", value: "Scores", raw: "sheet 'Scores'" }], selectors: [{ key: "row", value: null, form: "bare", raw: "row" }, { key: "row", value: "3", form: "bare", raw: "row 3" }, { key: "column b", value: "x", form: "bare", raw: "column B x" }] }],
  ...[
    { sheet: "Scores", cell: "B3" },
    { sheet: "scores", cell: "B3" },
    { sheet: "SCORES", cell: "B3" },
    { sheet: " Other Sheet ", cell: "A2" },
    { sheet: "missing", cell: "B3" },
    { sheet: null, cell: "B3" },
    { sheet: "  ", cell: "B3" },
    { sheet: "Scores", cell: "3B" },
    { sheet: "Scores", cell: "B3", row: 3 },
    { sheet: "Scores", cell: "B3", row: 4 },
    { sheet: "Scores" },
    { sheet: "Scores", row: 3 },
    { sheet: "Scores", row: 3, column: 2 },
    { sheet: "Scores", row: 3, column: 3, columnHeader: "f1" },
    { sheet: "Scores", row: 3, column: 3, columnHeader: "acc" },
    { sheet: "Scores", row: 3, column: 3, columnHeader: "nothing" },
    { sheet: "Scores", row: 3, columnHeader: "f1" },
    { sheet: "Scores", row: 3, columnHeader: "F1 " },
    { sheet: "Scores", row: 3, columnHeader: "acc" },
    { sheet: "Scores", row: 3, columnHeader: "nothing" },
    { sheet: "Scores", row: 3, columnHeader: "" },
    { sheet: "Scores", row: 2, columnHeader: "results" },
    { sheet: "Scores", row: 1, columnHeader: "Benchmark" },
    { sheet: "Scores", row: 6, columnHeader: "Alpha" },
    { sheet: "Scores", row: 9, column: 1 },
    { sheet: "Scores", row: 4, column: 1 },
    { sheet: "Scores", row: 4, column: 3 },
    { sheet: "Scores", row: 4, column: 9 },
    { sheet: "Scores", row: 5, column: 2 },
    { sheet: "Scores", row: 6, column: 3 },
    { sheet: "Scores", row: 4, column: 2, rowKeys: [{ key: "model", value: "Alpha" }] },
    { sheet: "Scores", row: 4, column: 2, rowKeys: [{ key: "model", value: "Beta" }] },
    { sheet: "Scores", row: 4, column: 2, rowKeys: [{ key: "tool", value: "nothing", printed: "tool 'alpha'" }, { key: "tool", value: "", printed: "" }] },
    { sheet: "Scores", row: 4, column: 2, rowKeys: [{ key: "tool", value: "0.61" }, { key: "acc", value: "0.71" }] },
    { sheet: "Scores", row: 4, column: 2, rowKeys: [{ key: "f1", value: "0.61" }, { key: "f1", value: "9" }, { key: "other", value: "9" }] },
    { sheet: "Scores", row: 3, column: 2, rowKeys: [{ key: "acc", value: "9" }] },
    { sheet: "Other Sheet", row: 2, column: 1 },
    { sheet: "Other Sheet", row: 2, column: 2, columnHeader: "b" },
    { sheet: "Last", row: 1, column: 1 },
    // A reference and a column that is not its column.
    { sheet: "Scores", cell: "B3", column: 3 },
    { sheet: "Scores", cell: "B3", column: 3, columnHeader: "f1" },
    // A row no locator gives: the header line of a sheet, and a line before it.
    { sheet: "scores", row: 0, column: 1 },
    { sheet: "Last", row: 0, column: 1 },
    { sheet: "Scores", row: 0, column: 1 },
    { sheet: "Other Sheet", row: 0, column: 3 },
    { sheet: "Scores", row: -1, column: 1 },
    { sheet: "Other Sheet", row: -1, column: 1 },
  ].map((request) => ["resolveSheetCell", "sheets", request]),
  ["resolveSheetCell", "noSheets", { sheet: "Scores", cell: "A1" }],
  ...["eightSheets", "nineSheets"].flatMap((name) => [
    ["resolveSheetCell", name, { sheet: "missing", cell: "A1" }],
    ["resolveSheetCell", name, { sheet: null, cell: "A1" }],
    ["resolveSheetCell", name, { sheet: "S8", cell: "A1" }],
  ]),
  ...["\f# sheet\t1", "\f# sheet\t1\tName", "\f# sheet", "\f# sheet\t1\t\tA1:B2\nrow"].map((held) => ["sheetBlocks", held]),
  ...[13, 14].map((line) => ["fieldsOfLine", "sheets", line]),
  ["resolveSheetCell", "sheetsAsText", { sheet: "Scores", cell: "A1" }],
];

const GRID_HEADER = { normaliser: "html-table-grid@1", text_layer_sha256: many(64, "a"), text_normaliser: "html-text@1", tables: 1, cells: 1, empty_cells: 0 };
const GRID_TABLE = { index: 0, rows: 1, columns: 1, header_rows: 0, headers: [[]], label: null, caption: null, cells: [{ row: 0, column: 0, row_span: 1, column_span: 1, header: false, start: 0, end: 1, text: "a" }], problems: [] };
const gridText = (header, ...tables) => `${[header, ...tables].map((held) => JSON.stringify(held)).join("\n")}\n`;
const gridCalls = [
  ["gridOf", "jats"],
  ["gridOf", "html"],
  ["gridOf", "htmlNoTable"],
  ["readGrid", gridText(GRID_HEADER, GRID_TABLE)],
  ["readGrid", `\n  \n${gridText(GRID_HEADER, { ...GRID_TABLE, inferred_header: { rule: "first-row@1", rows: 1, headers: [["a"]] } })}\n\n`],
  ["readGrid", gridText({ ...GRID_HEADER, tables: 0 })],
  ["readGrid", gridText({ ...GRID_HEADER, tables: 2 }, GRID_TABLE)],
  ["readGrid", gridText(GRID_HEADER)],
  ["readGrid", ""],
  ["readGrid", " \n\t\n"],
  ["readGrid", "not json\n"],
  ["readGrid", `${JSON.stringify(GRID_HEADER)}\nnot json\n`],
  ...[[0, 2, 1], [0, 2, 2], [0, 3, 2], [0, 0, 0], [0, 0, 1], [0, 1, 0], [0, 5, 4], [0, 5, 0], [0, 9, 9], [1, 1, 0], [1, 2, 0], [9, 0, 0]].map(([at, row, column]) => ["gridCell", "jats", at, row, column]),
  ...[
    ["Table 1", "1"],
    ["table 1. Scores", "1"],
    ["  Supplementary  Table 1", "1"],
    ["Supplemental Tables 1", "1"],
    ["Extended Data Table 1", "1"],
    ["Appendix Table 1", "1"],
    ["Table 11", "1"],
    ["Table 1a", "1"],
    ["Table 1.", "1"],
    ["Table S1", "s1"],
    ["Table  1", "1"],
    ["Table ı", "I"],
    ["Table ſ", "S"],
    ["Table K", "K"],
    ["Table É", "é"],
    ["Table Σ", "ς"],
    ["Table ǅ", "Ǆ"],
    ["See Table 1", "1"],
    ["Table 1", ""],
    ["Table (a)", "(a)"],
    ["Table A.1", "A.1"],
    ["Table A.1", "A11"],
    [`Table ${FACE}`, FACE],
  ].map(([text, value]) => ["namesTable", text, value]),
  ...[0, 1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23, 24, 25, 26, 27, 28, -1, 999].map((point) => ["unitOfPoint", "astral", point]),
  ["unitOfPoint", "empty", 0],
  ["unitOfPoint", "empty", 1],
  ["unitOfPoint", "blank", 1],
  ["unitOfPoint", "blank", 2],
  ["unitOfPoint", "blank", 3],
  ...["1", "2", "3", "5", "9"].map((value) => ["gridRouteFor", "jats", value]),
  ...["1", "2", "3", "4", "9"].map((value) => ["gridRouteFor", "html", value]),
  ...[
    "Table 1, row 'Alpha', column 'Human' / 'AUROC'",
    "Table 1, row 'Alpha' / 'x', column 'Human' | 'AUROC' > 'deep' + 'more' (the note) (AUROC)",
    "Table 1, row 'Alpha', column 'Human', sub-column 'AUROC', / 'AUROC', / 'z'",
    "Table 1, / 'first', row 'Alpha', column 'Human' ()",
    "Table 1",
  ].map((printed) => ["gridRequest", printed, table1("Alpha", "Human", { keys: ["AUROC", "x", "deep"], subColumns: ["AUROC"] })]),
  ["gridRequest", "Table 1, row 'a', column 'b' / 'c'", table1("a", "b")],
  ["gridRequestParsed", { selectors: [{ key: "column", value: null }, { key: "column", value: "b" }, { key: "/", value: null }, { key: " / ", value: "c" }, { key: "//", value: "d" }], qualifiers: [{ kind: "note", text: "((x))" }, { kind: "page", text: "(page 2)" }] }, table1("a", "b")],
  // A grid that says it is over a layer and is not: its offsets are checked before a span is returned.
  ...[
    [0, 2, "ab"],
    [0, 2, "xx"],
    [1, 3, "b\n"],
    [0, 3, "ab\n"],
    [3, 5, "cd"],
    [3, 6, "cd"],
    [5, 6, ""],
    [40, 42, "zz"],
    [0, 99, "ab"],
  ].map(([start, end, text]) => ["resolveCustomGrid", "tiny", { start, end, text }, table1("r", "c")]),
  // One grid, every way a row and a column cross or fail to.
  ...[
    table1("Alpha", "Human"),
    table1("Alpha", "AUROC"),
    table1("Alpha", "Human", { subColumns: ["AUROC"] }),
    table1("Alpha", "Mouse", { subColumns: ["AUPRC"] }),
    table1("alpha", "human", { subColumns: ["auprc"] }),
    table1("Alpha", "Human", { subColumns: ["Nothing"] }),
    table1("Alpha", "Human", { subColumns: ["AUROC", "deeper"] }),
    table1("Alpha", "AUROC", { levelHints: ["Mouse"] }),
    table1("Alpha", "AUROC", { levelHints: ["mouse ", "  "] }),
    table1("Alpha", "AUROC", { levelHints: ["Nothing"] }),
    table1("Alpha", "AUROC", { levelHints: ["AUROC"] }),
    table1("Alpha", "Nothing"),
    table1("Alpha", "Model"),
    table1("Nobody", "Mouse", { subColumns: ["AUROC"] }),
    table1("Beta", "Mouse", { subColumns: ["AUROC"] }),
    table1("Beta", "Mouse", { subColumns: ["AUROC"], keys: ["v2"] }),
    table1("Beta", "Mouse", { subColumns: ["AUROC"], keys: ["0.21", "", "beta", "mouse"] }),
    table1("Beta", "Mouse", { subColumns: ["AUROC"], keys: ["zzz"] }),
    table1("Beta", "Human", { subColumns: ["AUPRC"], keys: ["0.21"] }),
    table1("Gamma", "Mouse", { subColumns: ["AUPRC"] }),
    table1("Gamma", "Human", { subColumns: ["AUROC"] }),
    anchored("2", "Alpha", "Acc"),
    anchored("2", "Alpha", "F1"),
    anchored("2", "Alpha", `F1 ${FACE}`),
    anchored("2", "Small", `F1 ${FACE}`),
    anchored("2", "Beta", "Group"),
    anchored("2", "Alpha", "Other"),
    anchored("2", "Alpha", "Model"),
    anchored("3", "Alpha", "Acc"),
    anchored("5", "Alpha", "Acc"),
    anchored("9", "Alpha", "Acc"),
    { anchor: ["figure", "1"], row: "Alpha", column: "Acc" },
  ].map((request) => ["resolveGridCell", "jats", request]),
  ...[anchored("1", "Alpha", "Acc (%)"), anchored("1", "Beta", "Acc"), anchored("2", "Alpha", "Rank"), anchored("3", "Alpha", "Acc"), anchored("4", "Alpha", "Acc"), anchored("9", "Alpha", "Acc"), anchored("(a)", "Alpha", "Acc"), anchored("6", "Alpha", "Acc"), anchored("7", "Alpha", "Acc")].map((request) => ["resolveGridCell", "html", request]),
  ...["(a)", "6", "7"].map((value) => ["gridRouteFor", "html", value]),
  ["resolveGridCell", "jats", table1("Alpha", "Human"), "html"],
];

// The dispatch, through the grammar. Each is a layer, a locator, and the options.
const located = (name, printed, options, grids) => ["resolveLocator", name, printed, ...(options === undefined && grids === undefined ? [] : [options ?? null]), ...(grids === undefined ? [] : [grids])];
const ALIGNED_LOCATORS = [
  "Table 1, row 'Alpha', column 'Acc'",
  "Table 1, row 'Beta', column 'Acc' (v2)",
  "Table 1, row 'Beta', column 'Acc', version 'v2'",
  "Table 1, row 'Beta' / 'v2', column 'Acc'",
  "Table 1, row 'Beta', column 'Acc', length 400",
  "Table 1, lower block, row 'Alpha', column 'Acc'",
  "Table 1, of block, row 'Alpha', column 'Acc', x block, 12.5 things, a7 and 3.14159",
  "Table 1, row Alpha, column Acc",
  "Table 1, row(Alpha), column(Acc)",
  "Table 1; row Alpha (one-hot); column Acc",
  "Table 2, row 'Alpha', column 'Human', sub-column 'AUROC'",
  "Table 2, row 'Alpha', column group 'Human', column 'AUROC'",
  "Table 1, row 'Alpha'",
  "Table 1, row 'Alpha', row 'Beta', column 'Acc'",
  "Table 12, row 'Alpha', column 'Acc'",
  "Table 1, column 'Acc'",
  "Table 1, model 'Alpha', column 'Acc'",
  "Table 1",
  "Supplementary Table S1, row 'Alpha', column 'Acc'",
  "Table 3, row 'Alpha', column 'Score' (page 2 of 2)",
  "Table 1, row 3 ('Qwen2.5', 'Binary class prompt + Default temperature (0.8)', 'FoundationOne'), column 'Acc'",
  "Table 1, row 3, column 'Acc'",
  "Table 1, cell B3",
  "Table 1, cell B3, row 'Alpha', column 'Acc'",
  "Table 1, line 5, column 'Acc'",
  "Table 1, line 5, row 'Alpha', column 'Acc'",
  "Figure 7, row 'Alpha', column 'Acc'",
  "Figure 2, column 'Acc'",
  "Figure 2",
  "Fig. 2 caption, panel b",
  "Figure 3",
  "Figure 9 (page 4, raster), panel 'x'",
  "Figure 2, row 'Alpha', bar label",
  "line 5",
  "lines 5 and line 7",
  "line 0",
  "line 9999",
  "row 5",
  "rows 5 to row 6",
  "page 1",
  "Page 2, something",
  "(page 3 of 3)",
  "page 99",
  "p. 0",
  "",
  "nothing the grammar reads at all",
  "leaderboard.json data.full, model 'evo-1', key 'macro_MCC'",
  "some/file.csv",
  "Sheet 'S8', row 3, column B",
  "Scores!B3",
];
const PROSE_LOCATORS = [
  "Results 'First results paragraph. It says 13,770 things were found.' paragraph 1",
  "Results, 'Second results paragraph' paragraph 1, citing Fig. 2A: 'gives 0.91 on average'",
  "Methods paragraph 1: 'A paragraph under a line that ends in a colon.'",
  "Introduction, the sentence beginning 'This is the first paragraph'",
  "Introduction, sentence 'The second paragraph', value for x",
  "Introduction, sentences beginning 'A second sentence'",
  "Introduction paragraph 1",
  "Introduction paragraph 2",
  "Introduction paragraph 9",
  "Introduction, paragraph 2, sentence 1",
  "Results paragraph 1",
  "Conclusion paragraph 1",
  "Discussion paragraph 1",
  "Nowhere paragraph 1",
  "paragraph 3",
  "Results P13, sentence 1",
  "Results, Benchmarking P2, 'It says 13,770 things were found.'",
  "Section 3.2: 'It says 13,770 things were found.'",
  "Results 'It says 13,770 things were found, which the layer does not print in this form at all' paragraph 1",
  "Results 'Second results paragraph. Scoring real homologs against real homologs gives 0.91' paragraph 2",
  "Introduction 'cites (Fig. 6C) before a number 10.3 and ends' paragraph 1; Fig. 2b panel 'Cerebellum vs blood'",
  "Methods 'Harvesting', page 8, line 2: 'nothing is printed here'",
  "line 2: 'Title'",
  "Table 1, row 'A paragraph under a line that ends in a colon and goes on', column 'x'",
  "Table 1, row 'First results paragraph. It says 13,770 things were found.'",
  "'repeats repeats'",
  "columns 'AP' and 'AP 95% CI'",
  "run 'rrf prf rprec'",
  "the phrase ‘It says 13,770 things were found.’ and “Final sentence here!”",
  "the phrase '3’ UTR categories (0.50 vs. 0.18)'",
  "an unclosed 'quote that never ends",
  `Astral 'sentence with a face ${FACE} in it and a tail.'`,
  "Section 5.4 'Reinforcement learning', parenthesis '(+0.0133, Figure 8a)' (page 1 of 2)",
  "Results 'Second results paragraph. Scoring real homologs against real homologs' paragraph 2: 'gives 0.91 on average'",
  "Results: 'gives 0.91 on average' and 'Second results paragraph. Scoring real homologs against real homologs'",
  "Results 'Second results paragraph. Scoring real homologs against real homologs' and 'no number in this one'",
  "Introduction paragraph 1 'This is the first paragraph of the introduction.'",
];
const FILE_LOCATORS = [
  "data/metrics.csv row 3, column 2 (acc)",
  "line 3; model=Alpha; column acc",
  "line 3, column 'acc'",
  "row 3, column 'f1, macro'",
  "line 99, column 'acc'",
  "results.csv, row 'Alpha', column 'acc'",
  "results.csv, row 'Beta', column 'acc'",
  "results.csv, model 'Gamma', column 'acc'",
  "results.csv, model 'Beta', note 'again', column 'acc'",
  "results.csv, model 'Nobody', column 'acc'",
  "results.csv, line 4, model 'Nobody', column 'acc'",
  "results.csv, cell B3, model 'Nobody', column 'acc'",
  "cell B3",
  "cell B3, column 'acc'",
  "Table 1, cell B3, row 'Alpha', column 'acc'",
  "results.csv, column 'acc'",
  "results.csv, row 'Alpha'",
  "line 3",
  "row 3",
  "rows 3 and row 4",
  "line 3 'model,acc'",
];
const JSON_LOCATORS = [
  "leaderboard.json data.full, model 'evo-1', key 'macro_MCC'",
  "leaderboard.json data.full, model 'gena', key 'macro_MCC'",
  "leaderboard.json data.one.value",
  "leaderboard.json $.data.one.list[1]",
  "results.json results[0].score",
  "leaderboard.json data.full, model 'evo-1', key 'cat_MCC.Histone Mod.', run ''",
];
const SHEET_LOCATORS = [
  "Supplementary Material 2, sheet 'Scores', cell B3 (row 'Alpha', column 'acc')",
  "Data file S2 sheet Scores, row 3 (model 'Alpha'), column C 'f1'",
  "sheet 'Scores', row 3, column 'f1'",
  "Supporting Information sheet Scores, C4; group 'x'; row 'Alpha'; column 'f1'",
  "sheet 'Scores', row 3",
  "sheet 'Scores', 'a quoted run of more than forty characters in a sheet locator'",
  "Table 1, row 'Alpha', column 'acc'",
  "sheet 'Scores', row 'Alpha', column 'acc'",
  "line 3",
];
const GRID_LOCATORS = [
  "Table 1, row 'Alpha', column 'Human' / 'AUROC'",
  "Table 1, row 'Alpha', column 'Human', sub-column 'AUPRC'",
  "Table 1, row 'Alpha', column 'AUROC' (Mouse)",
  "Table 1, row 'Alpha', column 'AUROC'",
  "Table 1, row Beta, column Mouse / AUPRC; XML row4 column5",
  "Table 1, row 'Beta', column 'Mouse' / 'AUPRC', variant 'v2'",
  "Table 2, row 'Alpha', column 'Model'",
  `Table 2, row 'Alpha', column 'F1 ${FACE}'`,
  "Table 3, row 'Alpha', column 'Acc'",
  "Table 9, row 'Alpha', column 'Acc'",
  "Figure 1, row 'Alpha', column 'Acc'",
  "Results paragraph 2",
  "Results paragraph 1",
  "Results paragraph 9",
  "Methods paragraph 1",
  "Results, 'Second paragraph with 13,770 splice junctions (Pearson r = 0.58) in it.'",
  "Table 1, row 'Alpha'",
  "row 4",
  "line 4",
  "cell B3",
  "line 4, column 'Human'",
];
const resolveCalls = [
  ...ALIGNED_LOCATORS.map((printed) => located("aligned", printed)),
  ...ALIGNED_LOCATORS.slice(0, 4).map((printed) => located("aligned", printed, { legacyQuote: true })),
  ...PROSE_LOCATORS.map((printed) => located("prose", printed)),
  ...PROSE_LOCATORS.map((printed) => located("prose", printed, { legacyQuote: true })),
  ...PROSE_LOCATORS.slice(0, 12).map((printed) => located("proseLines", printed)),
  ...FILE_LOCATORS.map((printed) => located("csv", printed)),
  ...FILE_LOCATORS.map((printed) => located("csvAsPdf", printed)),
  ...JSON_LOCATORS.map((printed) => located("json", printed)),
  located("jsonAsPdf", JSON_LOCATORS[0]),
  located("notJson", JSON_LOCATORS[0]),
  ...SHEET_LOCATORS.map((printed) => located("sheets", printed)),
  ...SHEET_LOCATORS.map((printed) => located("sheets", printed, { sheet: false })),
  ...SHEET_LOCATORS.slice(0, 3).map((printed) => located("sheets", printed, {})),
  ...GRID_LOCATORS.map((printed) => located("jats", printed)),
  ...GRID_LOCATORS.slice(0, 6).map((printed) => located("jats", printed, null, null)),
  ...GRID_LOCATORS.slice(0, 3).map((printed) => located("jats", printed, null, "html")),
  ...GRID_LOCATORS.slice(0, 3).map((printed) => located("html", printed, null, "jats")),
  located("htmlNoTable", "Table 1, row 'Alpha', column 'Acc'"),
  located("aligned", "Table 1, row 'Alpha', column 'Acc'", null, "jats"),
  ...["Table 1, row 'Alpha', column 'Acc (%)'", "Table 2, row 'Alpha', column 'Rank'", "Table 4, row 'Alpha', column 'Acc'", "Table 3, row 'Alpha', column 'Acc'", "Notes paragraph 1"].map((printed) => located("html", printed)),
  located("paged", "page 2"),
  located("paged", "line 2"),
  located("blank", "line 1"),
  located("empty", "line 1"),
  located("astral", `the phrase 'café ${FACE}${FACE} end'`),
  located("astral", `Table ${FACE}, row '${FACE}', column '${FACE}'`),
  // Parses the grammar does not give: a value that is no number, and a key with no value.
  ["resolveParsed", "csv", { addresses: [{ kind: "line", value: "x", raw: "line x" }], selectors: [{ key: "column", value: "acc", form: "quoted", raw: "column 'acc'" }] }],
  ["resolveParsed", "csv", { addresses: [{ kind: "line", value: "x", raw: "line x" }] }],
  ["resolveParsed", "csv", { addresses: [{ kind: "line", value: "2", raw: "line 2" }, { kind: "line", value: "x", raw: "line x" }] }],
  ["resolveParsed", "prose", { anchors: [{ kind: "container", value: "Introduction", raw: "Introduction" }], addresses: [{ kind: "paragraph", value: "x", raw: "paragraph x" }] }],
  ["resolveParsed", "prose", { anchors: [{ kind: "container", value: "Section", container: "Introduction", raw: "Introduction" }], addresses: [{ kind: "paragraph", value: "1", raw: "paragraph 1" }] }],
  ["resolveParsed", "prose", { anchors: [{ kind: "container", value: "Introduction", raw: "Introduction" }], addresses: [{ kind: "paragraph", value: "1", raw: "paragraph 1" }, { kind: "quote", value: "", raw: "''" }], printed: "'' paragraph 1" }],
  ["resolveParsed", "prose", { anchors: [{ kind: "container", value: "Introduction", raw: "Introduction" }], addresses: [{ kind: "paragraph", value: "1", raw: "paragraph 1" }, { kind: "quote", value: "a quote the printed locator does not hold", raw: "not printed" }], printed: "Introduction paragraph 1" }],
  ["resolveParsed", "prose", { anchors: [{ kind: "container", value: "Introduction", raw: "Introduction" }], addresses: [{ kind: "paragraph", value: "1", raw: "not printed either" }, { kind: "sentence", value: "Results", raw: "'Results'" }], printed: "'Results' Introduction paragraph 1" }],
  ["resolveParsed", "aligned", { anchors: [{ kind: "table", value: "1", raw: "Table 1" }], selectors: [{ key: "row", value: null, form: "bare", raw: "row" }, { key: "row", value: "Alpha", form: "quoted", raw: "row 'Alpha'" }, { key: "column", value: "Acc", form: "quoted", raw: "column 'Acc'" }, { key: "column", value: "", form: "quoted", raw: "column ''" }] }],
  ["resolveParsed", "aligned", { anchors: [{ kind: "table", value: "1", raw: "Table 1" }], selectors: [{ key: "row", value: "", form: "quoted", raw: "row ''" }, { key: "column", value: "Acc", form: "quoted", raw: "column 'Acc'" }] }],
  ["resolveParsed", "paged", { qualifiers: [{ kind: "note", text: "(a note)" }, { kind: "page", text: "page" }, { kind: "page", text: `page ${many(400, "9")}` }] }],
  ["resolveParsed", "paged", { qualifiers: [{ kind: "page", text: "p. 2 of 3" }] }],
  ["resolveParsed", "aligned", { anchors: [{ kind: "file", value: "a.csv", raw: "a.csv" }], addresses: [{ kind: "xml_cell", value: "4", value2: "3", raw: "XML row4 column3" }, { kind: "cell_range", value: "A1", value2: "B2", raw: "A1:B2" }] }],
  ["resolveParsed", "csv", { selectors: [{ key: " row label x", value: "Alpha", form: "quoted", raw: "row label x 'Alpha'" }, { key: "rows of", value: "v", form: "bare", raw: "rows\nof v" }, { key: "col x", value: "v", form: "bare", raw: "col  acc " }], anchors: [{ kind: "file", value: "a.csv", raw: "a.csv" }] }],
];

const valueCalls = [
  ["", ""],
  ["0.5", ""],
  ["", "0.5"],
  ["0.5", "0.5"],
  [" 0.50 ", "0.5"],
  ["0.5", "0.50"],
  ["0.81699999999999995", "0.82"],
  ["0.81699999999999995", "0.817"],
  ["0.81699999999999995", "0.8"],
  ["0.825", "0.83"],
  ["0.8249999", "0.82"],
  ["0.835", "0.84"],
  ["-0.004", "-0.00"],
  ["-0.004", "0.00"],
  ["0.999", "1.0"],
  ["9.995", "10.00"],
  ["99.5", "100"],
  ["1e-7", "0.0000001"],
  ["1.5e-7", "2e-7"],
  ["1.5e-7", "1.5E-07"],
  ["123456789012345678901234", "1.2345678901234568e+23"],
  ["1e21", "1000000000000000000000"],
  ["1e400", "1e400"],
  ["1e400", "2e400"],
  ["1e-400", "0"],
  ["5", "5."],
  [".5", "0.5"],
  ["+.5e1", "5"],
  ["1.", "1"],
  ["12", "1.2e1"],
  ["0.5e-200", "0"],
  ["1e5", "1e+5"],
  ["3", "3e9999999999999999999999"],
  ["3", "3e-9999999999999999999999"],
  ["−0.5", "-0.5"],
  ["0.5 ± 0.1", "0.5+/-0.1"],
  ["0.5 – 0.7", "0.5—0.7"],
  ["Acc 0.5 (n=3)", "0.5"],
  ["0.5", "Acc 0.5 (n=3)"],
  ["Acc", "Acc 0.5"],
  ["0.5", "0.6"],
  ["ALPHA Beta", "alpha beta"],
  ["1,234", "1234"],
  ["0x10", "16"],
  ["Infinity", "Infinity"],
  ["1.5", "1.5.0"],
  ["--1", "-1"],
  [`${FACE}0.5`, "0.5"],
  ["0.5", `${FACE}0.5`],
].map(([exact, printed]) => ["valuePresence", exact, printed]);

const record = (patch) => ({
  layer_sha256: many(64, "1"),
  object_sha256: many(64, "a"),
  derived_from: many(64, "a"),
  container_path: null,
  normaliser: "text-utf8@1",
  tool: "text-utf8@1",
  tool_version: "1",
  derivation: "text-utf8@1 decoded as utf-8",
  encoding: "utf-8",
  media_type: "text/plain",
  identity: true,
  bytes: 10,
  metrics: { characters: 10, non_space: 9, replacement: 0, printable: 10, digits: 1, letters: 8, punctuation: 0, longest_punctuation_run: 0, page_breaks: 0, pdf_pages: null },
  sanity: "pass",
  sanity_reasons: [],
  source_ids: ["s1"],
  visibility: "public",
  store: "r2",
  ...patch,
});
const fact = (source_id, patch = {}) => ({ source_id, outcome: "stored", sha256: many(64, "a"), reason: null, source_class: "publisher", ...patch });
const A = many(64, "a");
const B = many(64, "b");
const C = many(64, "c");
const REGISTRY = [
  record({}),
  record({ layer_sha256: many(64, "2"), metrics: { ...record({}).metrics, characters: 99 }, sanity: "suspect", sanity_reasons: ["few letters", "odd"] }),
  record({ layer_sha256: many(64, "3"), normaliser: "html-table-grid@1", metrics: { ...record({}).metrics, characters: 5000 } }),
  record({ layer_sha256: many(64, "4"), object_sha256: B, sanity: "fail", sanity_reasons: ["mojibake"], metrics: { ...record({}).metrics, characters: 700 } }),
  record({ layer_sha256: many(64, "6"), object_sha256: C, sanity: "suspect", sanity_reasons: ["thin"], metrics: { ...record({}).metrics, characters: 40 } }),
  record({ layer_sha256: many(64, "5"), object_sha256: C, sanity: "suspect", sanity_reasons: ["thin"], metrics: { ...record({}).metrics, characters: 40 } }),
  record({ layer_sha256: many(64, "7"), object_sha256: C, sanity: "suspect", sanity_reasons: ["thinner"], metrics: { ...record({}).metrics, characters: 30 } }),
];
const coverageCalls = [
  ["coverageOf", [], [], [], []],
  [
    "coverageOf",
    ["s9", "s1", "s2", "S3", "s4", "s5", "s6", "s7", "s8", "s1", "", FACE, "10", "9"],
    [
      fact("s1"),
      fact("s2", { sha256: B, source_class: "arxiv" }),
      fact("S3", { sha256: C, source_class: "arxiv" }),
      fact("s4", { outcome: "failed", sha256: null, reason: "timeout" }),
      fact("s5", { outcome: "failed", sha256: null }),
      fact("s6", { outcome: "not_artifact", reason: "interstitial_body", source_class: "pmc" }),
      fact("s7", { outcome: "not_artifact", source_class: "10" }),
      fact("s8", { sha256: many(64, "d"), source_class: "9" }),
      fact("10", { sha256: many(64, "e") }),
      fact("9", { sha256: null }),
      fact(FACE, { sha256: A }),
    ],
    REGISTRY,
    [
      { object_sha256: many(64, "d"), container_path: null, reason: "raster image" },
      { object_sha256: many(64, "d"), container_path: "a", reason: "legacy office binary" },
      { object_sha256: many(64, "d"), container_path: "b", reason: "raster image" },
      { object_sha256: many(64, "f"), container_path: null, reason: "not read by any source" },
    ],
  ],
  ["coverageOf", ["s1"], [fact("s1")], REGISTRY.slice(0, 1), undefined],
  ...["", "abc", `a${FACE}b`, "café"].map((text) => ["characterCount", text]),
  ...[[0, 0], [0, 1], [1, 3], [2, 3], [0, 4], [3, 2], [-1, 2], [0, 9], [1.5, 2], [1, "NaN"]].map(([start, end]) => ["sliceSpan", `a${FACE}bé`, start, end]),
];

const DOCUMENT = lines(
  "# Report",
  "Resolved 11,360 <!-- figure:resolved --> of 52,278<!--figure:locators--> locators, 21.7% <!-- FIGURE:resolved_pct -->.",
  "A fold of 10.3-fold <!-- figure:fold --> and 35.7 percent <!-- figure:resolved_pct --> and (99.9%) <!-- figure:value_present_pct -->",
  "No number here <!-- figure:resolved --> and an unknown 5 <!-- figure:nothing.here -->.",
  "A number on the line above:",
  "7",
  "<!-- figure:seven --> and a big one 1,234,567 <!-- figure:big --> and 1,000 <!-- figure:fraction --> and 12 <!-- figure:huge -->",
  "<!-- figure: spaced --> <!-- figure:bad name --> <!-- figure:resolved -> 18,667 <!-- figure:resolved -->",
  `${many(40, FACE)}123 <!-- figure:astral --> ${FACE}4${FACE} <!-- figure:astral -->`,
  "12345678901234567890123456789012345 <!-- figure:long -->",
  "3 <!-- figure:three -->3 <!-- figure:three -->",
);
const FIGURES = { resolved: 18667, locators: 52278, resolved_pct: 35.7, value_present_pct: 100, fold: 10.3, seven: 8, big: 7654321, fraction: 1234.5, huge: 1e21, astral: 5, long: 9, three: 30 };
const input = (path_, digest, records) => (records === undefined ? { path: path_, sha256: digest } : { path: path_, sha256: digest, lines: records.length, records });
const rows = (...keys) => keys.map(([key, digest]) => ({ key, sha256: digest }));
const figureCalls = [
  ...[DOCUMENT, "", "no marks", "5 <!-- figure:a -->", "<!-- figure:a -->"].map((text) => ["marks", text]),
  ...[["18,667", 18667], ["18667", 18667], ["18,667", 18668], ["35.7", 35.7], ["35.70", 35.7], ["", 0], ["1e3", 1000], ["1.", 1], [".5", 0.5], ["-5", -5], ["1,2,3", 123], ["100", 100]].map(([printed, value]) => ["printsFigure", printed, value]),
  ["retag", DOCUMENT, FIGURES],
  ["retag", DOCUMENT, {}],
  ["retag", "", FIGURES],
  ["retag", "9\n<!-- figure:n --> and the same again, 9\n\n<!-- figure:n -->", { n: 5 }],
  ["retag", "18,667 <!-- figure:resolved --> is right", FIGURES],
  ["retag", "1,000 <!-- figure:n -->\n2,000 <!-- figure:m --> 1,000 <!-- figure:n -->", { n: -1234567, m: 1000000.5 }],
  ...["a.json", "a/b.json", "a.JSON", "a.json.bak", "json", ""].map((file) => ["recordsFileFor", file]),
  ["inputDigest", []],
  ["inputDigest", [input("a", "1"), input("b", "2")]],
  ["changedInputs", [], []],
  ["changedInputs", [input("src.ts", "1"), input("gone.ts", "2"), input("same.ts", "3")], [input("src.ts", "9"), input("same.ts", "3"), input("new.ts", "4"), input("also.ts", "5")]],
  [
    "changedInputs",
    [input("data.jsonl", "1", rows(["a", "x"], ["b", "y"], ["c", "z"], ["d", "w"], ["e", "v"], ["f", "u"]))],
    [input("data.jsonl", "2", rows(["a", "x"], ["b", "CHANGED"], ["new", "n"]))],
  ],
  ["changedInputs", [input("data.jsonl", "1", rows(["a", "x"], ["b", "y"]))], [input("data.jsonl", "2", rows(["zero", "0"], ["a", "x"], ["b", "y"], ["c", "z"]))]],
  ["changedInputs", [input("data.jsonl", "1", rows(["a", "x"]))], [input("data.jsonl", "2", rows(["a", "CHANGED"]))]],
  ["changedInputs", [input("data.jsonl", "1", rows(["a", "x"]))], [input("data.jsonl", "2", rows())]],
  ["changedInputs", [input("data.jsonl", "1", rows(["a", "x"], ["b", "y"], ["c", "z"], ["d", "w"]))], [input("data.jsonl", "2", rows(["a", "1"], ["b", "2"], ["c", "3"], ["d", "4"]))]],
  ["changedInputs", [input("b", "1"), input("a", "1")], [input("c", "1")]],
  // A key held twice, which no file read by `inputFiles` gives: the later line is the one compared.
  ["changedInputs", [input("data.jsonl", "1", rows(["a", "x"], ["a", "y"]))], [input("data.jsonl", "2", rows(["a", "x"], ["a", "y"], ["new", "n"]))]],
  ["changedInputs", [input("data.jsonl", "1", rows(["a", "y"]))], [input("data.jsonl", "2", rows(["a", "x"], ["a", "y"], ["new", "n"]))]],
  ["changedInputs", [input("data.jsonl", "1", rows(["a", "x"], ["a", "y"]))], [input("data.jsonl", "1", rows(["a", "x"], ["a", "y"]))]],
];

export const calls = [...offsetCalls, ...tableCalls, ...regionCalls, ...proseCalls, ...jsonCalls, ...sheetCalls, ...gridCalls, ...resolveCalls, ...valueCalls, ...coverageCalls, ...figureCalls];

export const precisionCases = [
  ...["0", "-0", "1", "-1.5", "123.456", "0.000001234", "1e-7", "1e21", "1e-6", "9.5", "0.95", "999.5", "99999999999999999", "5e-324", "1.7976931348623157e308", "0.30000000000000004", "2.5", "25", "0.5", "NaN", "Infinity", "-Infinity", "123456789"].flatMap((text) =>
    [1, 2, 3, 5, 17, 21, 100].map((digits) => [text, digits]),
  ),
];

// ---------------------------------------------------------------------------------------
// The figures file on small trees.

const jsonl = (...values) => values.map((value) => `${typeof value === "string" ? value : JSON.stringify(value)}\n`).join("");
const DATA = jsonl({ id: "b", v: 1 }, { id: "a", v: 2 }, { v: 3 }, { id: "", v: 4 }, { id: 5 }, { id: "a", v: 6 }, { id: "a", v: 7 }, [1], "7", '"text"', { id: "té" });
const TREE_INPUTS = ["src", "one.ts", "data/records.jsonl", "data/folder", "missing", "src/inner", "empty.jsonl", "bare.jsonl"];
const TREE_FILES = {
  "src/b.ts": "export const b = 2;\n",
  "src/a.ts": "export const a = 1;\n",
  "src/skipped.md": "not an input\n",
  "src/inner/c.ts": "export const c = 3;\n",
  "src/rows.jsonl": jsonl({ id: "x" }),
  "one.ts": "one\n",
  "data/records.jsonl": DATA,
  "data/folder/0.jsonl": jsonl({ id: "r0" }, { id: "r1" }),
  "data/folder/1.jsonl": "",
  "empty.jsonl": "",
  "bare.jsonl": '{"id":"no newline at the end"}',
};
const figureFile = (inputs, more = {}) => ({ command: "npm run spans -- measure", produced: "2026-11-19", documents: ["doc.md"], inputs, figures: { resolved: 3, resolved_pct: 100, share: 0.5 }, ...more });
export const figureTrees = [
  {
    name: "inputs",
    files: TREE_FILES,
    inputs: TREE_INPUTS,
    steps: [
      ["inputFiles"],
      ["writeFigures", figureFile([input("one.ts", "1"), input("data/records.jsonl", "2", rows(["b", "x"], ["a", "y"]))])],
      ["readFigures"],
      ["drift"],
      ["writeFigures", figureFile([input("one.ts", "1")])],
      ["readFigures"],
      ["writeFigures", figureFile([input("one.ts", "1")]), "out/deep/other.json"],
      ["readFigures", "out/deep/other.json"],
      ["writeFigures", figureFile([input("d.jsonl", "1", rows(["tab\there", "x"]))])],
      ["writeFigures", figureFile([input("d.jsonl", "1", rows(["line\nhere", "x"]))])],
      ["writeFigures", figureFile([input("d.jsonl", "1", rows()), input("e.jsonl", "2", rows(["k", "x"]))]), "other.txt"],
      ["readFigures", "other.txt"],
      ["readFigures", "missing.json"],
    ],
  },
  {
    name: "drift",
    files: TREE_FILES,
    inputs: TREE_INPUTS,
    steps: [
      ["drift"],
      ["snapshot", figureFile([])],
      ["drift"],
      ["edit", "data/records.jsonl", `${DATA}${jsonl({ id: "added" })}`],
      ["edit", "data/folder/9.jsonl", jsonl({ id: "a new file" })],
      ["drift"],
      ["edit", "data/records.jsonl", jsonl({ id: "added" }, { id: "b", v: 1 }, { id: "a", v: "changed" }, { id: "a", v: 6 })],
      ["edit", "src/a.ts", "export const a = 2;\n"],
      ["remove", "src/b.ts"],
      ["remove", "data/folder/0.jsonl"],
      ["edit", "bare.jsonl", ""],
      ["drift"],
      ["edit", "maintenance/reports/span-resolution-figures.json", "not json"],
      ["readFigures"],
      ["edit", "maintenance/reports/span-resolution-figures.json", "[1]"],
      ["readFigures"],
      ["drift"],
    ],
  },
  { name: "nothing", files: { "readme.md": "nothing to measure\n" }, steps: [["inputFiles"]] },
  { name: "bad line", files: { "data/evidence/results/0.jsonl": '{"id":"a"}\n\n{"id":"b"}\n' }, steps: [["inputFiles"]] },
  { name: "null line", files: { "data/evidence/results/0.jsonl": "null\n" }, steps: [["inputFiles"]] },
  {
    name: "sidecar",
    files: {
      "maintenance/reports/span-resolution-figures.json": JSON.stringify(figureFile([input("a.jsonl", "1"), input("b.ts", "2"), input("c.jsonl", "3")])),
      "maintenance/reports/span-resolution-figures-records.tsv": "# a.jsonl\nk1\td1\n\nk2\td2\textra\nk3\n# c.jsonl\n# unnamed.jsonl\nz\t9\n",
    },
    steps: [["readFigures"]],
  },
  {
    name: "sidecar with no file named",
    files: {
      "maintenance/reports/span-resolution-figures.json": JSON.stringify(figureFile([input("a.jsonl", "1")])),
      "maintenance/reports/span-resolution-figures-records.tsv": "k1\td1\n# a.jsonl\n",
    },
    steps: [["readFigures"]],
  },
];

// ---------------------------------------------------------------------------------------
// The commands, on a checkout laid out in a scratch folder with an object cache.

const xml = (name, body) => `<?xml version="1.0" encoding="UTF-8"?><${name}>${body}</${name.split(" ")[0]}>`;
const WORKBOOK = zip(
  Object.entries({
    "[Content_Types].xml": xml('Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"', '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'),
    "xl/workbook.xml": xml("workbook", '<sheets><sheet name="Scores" sheetId="1" r:id="rId1"/></sheets>'),
    "xl/_rels/workbook.xml.rels": xml("Relationships", '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'),
    "xl/worksheets/sheet1.xml": xml(
      "worksheet",
      `<sheetData>${[["model", "acc", "f1"], ["Alpha", 0.5, 0.61], ["Beta", 0.71, 0.81699999999999995]]
        .map((cells, r) => `<row r="${r + 1}">${cells.map((value, c) => (typeof value === "number" ? `<c r="${"ABC"[c]}${r + 1}"><v>${value === 0.81699999999999995 ? "0.81699999999999995" : value}</v></c>` : `<c r="${"ABC"[c]}${r + 1}" t="inlineStr"><is><t>${value}</t></is></c>`)).join("")}</row>`)
        .join("")}</sheetData>`,
    ),
  }).map(([name, data]) => ({ name, data: Buffer.from(data, "utf8") })),
);
const OBJECTS = {
  csv: Buffer.from("model,acc,f1\nAlpha,0.5,0.61\nBeta,0.71,0.8\nGamma,0.25,0.85\n", "utf8"),
  json: Buffer.from('{"results": [{"model": "Alpha", "acc": 0.5}, {"model": "Beta", "acc": 0.71}]}', "utf8"),
  jats: Buffer.from(JATS, "utf8"),
  html: Buffer.from(HTML, "utf8"),
  workbook: WORKBOOK,
  png: Buffer.concat([Buffer.from("89504e470d0a1a0a", "hex"), Buffer.alloc(32, 7)]),
  missing: Buffer.from("these bytes are in the manifest and not in the cache\n", "utf8"),
  page: Buffer.from("<!DOCTYPE html><html><body><p>Please enable JavaScript.</p></body></html>", "utf8"),
  control: Buffer.from(`<!DOCTYPE html><html><body><table><tr><td>${many(40, "&#1;")}</td><td>x</td></tr></table></body></html>`, "utf8"),
  archive: zip([{ name: "inner/scores.csv", data: Buffer.from("model,acc\nAlpha,0.33\n", "utf8") }, { name: "inner/picture.png", data: Buffer.concat([Buffer.from("89504e470d0a1a0a", "hex"), Buffer.alloc(8, 3)]) }]),
};
const key = (name) => sha256(OBJECTS[name]);
const baseRow = { sha256: null, bytes: 0, media_type: "application/octet-stream", layer: "exact-bytes", origin: "https://example.org/a", requested_url: null, first_seen_at: "2026-11-17T16:10:11Z", store: "r2", mirrors: [], visibility: "restricted", archive_tier: "t4-private", observed_licence: null, exposure: "policy", derived_from: null, derivation: null, previous_path: null, source_ids: [] };
const sorted = (value) => JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))));
const SOURCES = { csv: ["src-csv"], json: ["src-json"], jats: ["src-jats", "src-both"], html: ["src-html", "src-both"], workbook: ["src-workbook"], png: ["src-png"], missing: ["src-missing"], page: ["src-page"], control: ["src-control"], archive: ["src-archive"] };
const MEDIA = { csv: "text/csv", json: "application/json", jats: "application/xml", html: "text/html", workbook: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", png: "image/png", missing: "text/plain", page: "text/html", control: "text/html", archive: "application/zip" };
function manifest(names) {
  const shards = new Map();
  for (const name of names) {
    const row = { ...baseRow, sha256: key(name), bytes: OBJECTS[name].length, media_type: MEDIA[name], source_ids: SOURCES[name] };
    shards.set(row.sha256[0], [...(shards.get(row.sha256[0]) ?? []), row]);
  }
  return Object.fromEntries([...shards].map(([shard, held]) => [`data/archive/objects/${shard}.jsonl`, held.sort((a, b) => (a.sha256 < b.sha256 ? -1 : 1)).map((row) => `${sorted(row)}\n`).join("")]));
}
// The cache of a tree: each object by the path it is cached at, as its own bytes. The recorder
// and the tests compress each one where they lay the tree out, because gzip does not write the
// same bytes on every machine and a cached object is held to its expanded bytes alone.
const cache = (names) => Object.fromEntries(names.map((name) => [`data/archive/cache/${key(name).slice(0, 2)}/${key(name)}.gz`, OBJECTS[name].toString("hex")]));
const receipt = (source_id, name, patch = {}) =>
  sorted({ schema_version: 1, source_id, requested_url: "https://example.org/a", final_url: "https://example.org/a", http_status: 200, content_type: MEDIA[name] ?? null, bytes: name ? OBJECTS[name].length : null, sha256: name ? key(name) : null, fetched_at: "2026-11-17T16:10:11Z", route: "direct", routes_tried: [{ route: "direct", url: "https://example.org/a", status: 200, reason: null }], source_class: "publisher", outcome: "stored", reason: null, ...patch });
const failed = (source_id, reason) => receipt(source_id, null, { final_url: null, http_status: null, content_type: null, route: null, outcome: "failed", reason, routes_tried: [{ route: "direct", url: "https://example.org/a", status: 404, reason }] });
const RECEIPTS = [
  receipt("src-csv", "csv", { source_class: "code-repository" }),
  receipt("src-json", "json", { source_class: "code-repository" }),
  receipt("src-jats", "jats", { source_class: "europepmc-api" }),
  receipt("src-both", "jats", { source_class: "europepmc-api" }),
  receipt("src-html", "html"),
  receipt("src-workbook", "workbook"),
  receipt("src-png", "png", { source_class: "other-web" }),
  receipt("src-missing", "missing", { source_class: "other-web" }),
  receipt("src-page", "page", { outcome: "not_artifact", reason: "interstitial_body" }),
  receipt("src-control", "control", { source_class: "other-web" }),
  receipt("src-archive", "archive", { source_class: "data-repository" }),
  failed("src-failed", "not_found"),
].map((line) => `${line}\n`).join("");
const source = (id) => JSON.stringify({ id, kind: "source", name: id });
const result = (id, sources, locator, printed) => JSON.stringify({ id, kind: "result", source_ids: sources, attributes: { source_locator: locator, printed_value: printed } });
const RESULTS = {
  "data/evidence/results/0.jsonl": [
    result("r-csv-cell", ["src-csv"], "results.csv, row 'Beta', column 'acc'", "0.71"),
    result("r-csv-line", ["src-csv"], "line 2, column 'f1'", "0.6"),
    result("r-csv-wrong", ["src-csv"], "line 3, column 'f1'", "0.9"),
    result("r-json", ["src-json"], "results.json results, model 'Beta', key 'acc'", "0.71"),
    result("r-grid", ["src-jats"], "Table 1, row 'Alpha', column 'Human' / 'AUROC'", "0.11"),
    result("r-grid-wide", ["src-jats"], "Table 1, row 'Beta', column 'Mouse' / 'AUPRC', variant 'v2'", "0.34"),
    result("r-grid-none", ["src-jats"], "Table 9, row 'Alpha', column 'Acc'", "1"),
    result("r-caption", ["src-html"], "Table 1, row 'Beta', column 'Acc (%)'", "71"),
    result("r-quote", ["src-jats"], "Results 'Scores' paragraph 2: 'Second paragraph with 13,770 splice junctions (Pearson r = 0.58) in it.'", "0.58"),
  ].join("\n"),
  "data/evidence/results/1.jsonl": `${[
    result("r-sheet", ["src-workbook"], "Supplementary Data, sheet 'Scores', cell C3 (row 'Beta', column 'f1')", "0.82"),
    result("r-sheet-exact", ["src-workbook"], "sheet 'Scores', row 2, column 'acc'", "0.5"),
    result("r-sheet-part", ["src-workbook"], "sheet 'Scores', row 3, column B", "0.71 (best)"),
    result("r-later", ["src-png", "src-both"], "Table 1, row 'Beta', column 'Acc (%)' (HTML)", "71"),
    result("r-preferred", ["src-both"], "Table 2, row 'Alpha', column 'Rank' (JATS XML)", "1"),
    result("r-image", ["src-jats"], "Results paragraph 3, value shown in the figure", "0.99"),
    result("r-no-capture", ["src-failed"], "Table 1, row 'a', column 'b'", "1"),
    result("r-no-source", [], "Table 1, row 'a', column 'b'", "1"),
    result("r-unknown-source", ["src-nowhere"], "line 1", "1"),
    result("r-not-artifact", ["src-page"], "line 1", "1"),
    result("r-no-normaliser", ["src-png"], "line 1", "1"),
    result("r-not-readable", ["src-missing"], "line 1", "these"),
    result("r-no-locator", ["src-csv"], "", ""),
    result("r-number-rule", ["src-csv"], "line 4, column 'acc'", "0.2"),
    result("r-exponent", ["src-csv"], "line 4, column 'f1'", "9e-1"),
    result("r-same-layer-twice", ["src-jats", "src-both", "src-jats"], "Table 9, row 'Alpha', column 'Acc'", "1"),
    result("r-prefers-pdf", ["src-both"], "Table 1, row 'Alpha', column 'Human' / 'AUROC' (PDF page text)", "0.11"),
    result("r-prefers-csv", ["src-both", "src-csv"], "line 2 (CSV)", "Alpha"),
    result("r-unusable", ["src-control"], "line 1", "x"),
    result("r-unusable-and-usable", ["src-control", "src-csv"], "line 2, column 'acc'", "0.5"),
    result("r-member", ["src-archive"], "inner/scores.csv line 2, column 'acc'", "0.33"),
    result("r-names-no-layer", ["src-csv"], "line 3 (text-layer)", "Beta"),
    result("r-quoted-section", ["src-jats"], "Results 'First paragraph of the results, which mentions Table 1 in passing.' paragraph 2", "0.58"),
    result("r-every-layer", ["src-csv", "src-json", "src-jats", "src-html", "src-workbook", "src-archive"], "Table 99, row 'nobody', column 'nothing'", "1"),
    JSON.stringify({ id: "r-bare" }),
    JSON.stringify({ id: "r-odd", source_ids: ["src-csv"], attributes: { source_locator: 5, printed_value: null } }),
  ].join("\n")}\n`,
  "data/evidence/results/notes.txt": "not a file of records\n",
};
const STORE = {
  ...manifest(["csv", "json", "jats", "html", "workbook", "png", "missing", "page", "control", "archive"]),
  "data/omics/audits/2026-11-17-archive-fetch.receipts.jsonl": RECEIPTS,
  "data/entities/sources.jsonl": `${["src-csv", "src-json", "src-jats", "src-both", "src-html", "src-workbook", "src-png", "src-missing", "src-page", "src-failed", "src-unreceipted", "src-control", "src-archive"].map(source).join("\n")}\n`,
  ...RESULTS,
  "data/omics/batch-a/value-check.tsv": "id\tstatus\tprinted_value\tsource_locator\nr-csv-cell\tfound\t0.71\tx\nr-csv-wrong\tfound\t0.9\tx\nr-grid-none\tnot_found\t1\tx\nshort\trow\nr-absent\tfound\t1\tx\nr-sheet-part\t\t1\tx\n",
  // No result is named in two files: the command reads them in the order the directory lists
  // them, which is the machine's, and the later file would decide.
  "data/omics/batch-b/value-check.tsv": "id\tstatus\tprinted_value\tsource_locator\nr-sheet\tfound\t0.82\tx\textra\nr-sheet-exact\tfound\t0.5\tx\nr-json\tlocator_not_parsed\t0.71\tx\n",
  "data/omics/batch-c/notes.txt": "no value check here\n",
  "maintenance/reports/span-resolution-2026-11-18.md": "# Spans\n\nResolved 3 <!-- figure:resolved --> of 99 <!-- figure:locators --> and an unknown 4 <!-- figure:nothing -->.\n",
  "shared/omics/locator.ts": "export const grammar = 1;\n",
  "shared/omics/spans/index.ts": "export const spans = 1;\n",
};
const cached = cache(["csv", "json", "jats", "html", "workbook", "png", "page", "control", "archive"]);
const cachePath = (name) => `data/archive/cache/${key(name).slice(0, 2)}/${key(name)}.gz`;
const REGISTRY_FILE = "data/omics/layers/registry.jsonl";
const ZEROS = many(64, "0");
// The registry line of one normaliser's layer of one object, with another layer hash on it.
const stale = (name, normaliser) => ["sub", REGISTRY_FILE, `"layer_sha256":"[a-f0-9]{64}"(?=[^\\n]*"normaliser":"${normaliser}"[^\\n]*"object_sha256":"${key(name)}")`, `"layer_sha256":"${ZEROS}"`];

// A hundred objects, so that the measurement reads a hundred layers in a pass and says so.
const HUNDRED = Array.from({ length: 100 }, (_, at) => Buffer.from(`value\n${at}\n`, "utf8"));
const hundredKey = (at) => sha256(HUNDRED[at]);
const hundredShards = new Map();
HUNDRED.forEach((data, at) => {
  const row = { ...baseRow, sha256: hundredKey(at), bytes: data.length, media_type: "text/csv", source_ids: [`h-${at}`] };
  hundredShards.set(row.sha256[0], [...(hundredShards.get(row.sha256[0]) ?? []), row]);
});
const HUNDRED_STORE = {
  ...Object.fromEntries([...hundredShards].map(([shard, held]) => [`data/archive/objects/${shard}.jsonl`, held.sort((a, b) => (a.sha256 < b.sha256 ? -1 : 1)).map((row) => `${sorted(row)}\n`).join("")])),
  "data/entities/sources.jsonl": `${HUNDRED.map((_, at) => source(`h-${at}`)).join("\n")}\n`,
  "data/evidence/results/0.jsonl": `${HUNDRED.map((_, at) => result(`r-${at}`, [`h-${at}`], "line 2", String(at))).join("\n")}\n`,
  "data/omics/audits/2026-11-17-archive-fetch.receipts.jsonl": HUNDRED.map((data, at) => `${sorted({ ...JSON.parse(receipt(`h-${at}`, "csv")), bytes: data.length, sha256: hundredKey(at) })}\n`).join(""),
};
const hundredCache = Object.fromEntries(HUNDRED.map((data, at) => [`data/archive/cache/${hundredKey(at).slice(0, 2)}/${hundredKey(at)}.gz`, data.toString("hex")]));

// A registry and a file of what gave no layer, written out, for the two commands that read no object.
const layerRow = (patch) => sorted(record(patch));
const SURFACE = {
  "data/entities/sources.jsonl": `${["u-pass", "u-suspect", "u-fail", "u-broken", "u-slides", "u-odt", "u-office", "u-legacy", "u-image", "u-other", "u-page", "u-failed"].map(source).join("\n")}\n`,
  "data/omics/audits/2026-11-17-archive-fetch.receipts.jsonl": [
    ["u-pass", "1", {}],
    ["u-suspect", "2", { source_class: "arxiv" }],
    ["u-fail", "3", { source_class: "arxiv" }],
    ["u-broken", "4", {}],
    ["u-slides", "5", {}],
    ["u-odt", "6", {}],
    ["u-office", "7", {}],
    ["u-legacy", "8", {}],
    ["u-image", "9", {}],
    ["u-other", "a", {}],
    ["u-page", "b", { outcome: "not_artifact", reason: "publisher_html_only" }],
  ]
    .map(([id, digit, patch]) => `${sorted({ ...JSON.parse(receipt(id, "csv")), sha256: many(64, digit), ...patch })}\n`)
    .concat(`${failed("u-failed", "timeout")}\n`)
    .join(""),
  "data/omics/layers/registry.jsonl": [
    layerRow({ layer_sha256: many(64, "1"), object_sha256: many(64, "1"), derived_from: many(64, "1"), source_ids: ["u-pass"], metrics: { ...record({}).metrics, characters: 1234567 } }),
    layerRow({ layer_sha256: many(64, "2"), object_sha256: many(64, "2"), derived_from: many(64, "2"), source_ids: ["u-suspect"], sanity: "suspect", sanity_reasons: ["few letters"], metrics: { ...record({}).metrics, characters: 1000 } }),
    layerRow({ layer_sha256: many(64, "3"), object_sha256: many(64, "3"), derived_from: many(64, "3"), source_ids: ["u-fail"], sanity: "fail", sanity_reasons: ["mostly not printable"], metrics: { ...record({}).metrics, characters: 50 } }),
  ]
    .map((line) => `${line}\n`)
    .join(""),
  "data/omics/layers/unsupported.jsonl": [
    ["4", "xlsx normaliser failed: the package did not expand: bad zip"],
    ["5", "office presentation package, which no normaliser reads"],
    ["6", "opendocument package, which no normaliser reads"],
    ["7", "office package with no main part"],
    ["8", "legacy office binary"],
    ["9", "raster image"],
    ["a", "an unknown format"],
  ]
    .map(([digit, reason]) => `${sorted({ object_sha256: many(64, digit), container_path: null, reason })}\n`)
    .join(""),
};
const MEASURES = [["spans", "measure"], ["spans", "measure", "--out", "<root>/elsewhere", "--detail"], ["spans", "measure", "--no-grid"], ["spans", "measure", "--no-sheet", "--detail"], ["spans", "measure", "--legacy-quote"], ["spans"]];
export const commandTrees = [
  {
    name: "store",
    files: { text: STORE, objects: cached },
    sequences: [
      [["layers", "build"], ["spans", "measure"], ["spans", "retag"], ["spans", "retag"], ["spans", "measure", "--detail"]],
      ...MEASURES.slice(1).map((argv) => [["layers", "build"], argv]),
      [["layers", "build"], ["spans", "control"], ["spans", "value-check"]],
      [["layers", "build"], ...["r-grid", "r-csv-wrong", "r-no-source", "r-nobody", "r-quote", "r-sheet"].map((id) => ["spans", "show", id]), ["spans", "show"], ["spans", "nothing"]],
      [["layers", "build"], ["layers", "coverage"], ["layers", "surface"], ...["src-both", "src-png", "src-nowhere", key("jats"), key("png"), key("workbook"), "src-missing", key("archive"), "src-control"].map((id) => ["layers", "show", id])],
      // An object that left the cache after the registry was built.
      [["layers", "build"], ["rm", cachePath("json")], ["spans", "measure"], ["spans", "show", "r-json"], ["layers", "show", "src-json"]],
      // A registry that names a layer the object no longer gives, and the same for a grid.
      [["layers", "build"], stale("csv", "text-utf8@1"), ["spans", "measure"], ["spans", "show", "r-csv-cell"], ["layers", "show", "src-csv"]],
      [["layers", "build"], stale("jats", "jats-table-grid@1"), ["spans", "measure"], ["spans", "show", "r-grid"]],
      [["spans", "measure"], ["spans", "retag"], ["layers", "coverage"], ["layers", "surface"], ["layers", "show", "src-csv"]],
    ],
  },
  {
    name: "bare",
    files: { text: { "data/entities/sources.jsonl": "", "data/evidence/results/0.jsonl": "", "data/omics/readme.md": "nothing\n" } },
    sequences: [[["spans", "measure"], ["spans", "control"], ["spans", "value-check"], ["layers", "coverage"], ["layers", "surface"]]],
  },
  {
    // The measurement reads past a blank line of a results file, and the figures it would
    // write name each line of that file as a record, so the command ends on the blank line.
    name: "blank line",
    files: { text: { ...STORE, "data/evidence/results/2.jsonl": `${result("r-after-a-blank-line", ["src-csv"], "line 2", "Alpha")}\n\n` }, objects: cached },
    sequences: [[["layers", "build"], ["spans", "measure"], ["spans", "show", "r-after-a-blank-line"], ["spans", "control"]]],
  },
  { name: "hundred", files: { text: HUNDRED_STORE, objects: hundredCache }, sequences: [[["layers", "build"], ["spans", "measure"]]] },
  { name: "written registry", files: { text: SURFACE }, sequences: [[["layers", "coverage"], ["layers", "surface"], ["layers", "show", "u-pass"]]] },
  { name: "no results", files: { text: { "data/entities/sources.jsonl": "" } }, sequences: [[["spans", "measure"], ["spans", "show", "x"]]] },
  {
    name: "duplicate source",
    files: { text: { ...STORE, "data/entities/sources.jsonl": `${["src-csv", "src-csv", "src-json"].map(source).join("\n")}\n` }, objects: cached },
    sequences: [[["layers", "build"], ["layers", "coverage"], ["layers", "surface"]]],
  },
];

// The tree the store recorder is run over, twice: with every object in its cache, and with
// these taken out, which is what a folder of the public objects is to the pinned store.
export const recordedTree = { name: "store", date: "2026-11-19", extra: cache(["missing"]), restricted: [cachePath("jats"), cachePath("workbook")] };
