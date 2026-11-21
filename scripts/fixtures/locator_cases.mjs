// The inputs the locator fixture is recorded from. Built-ins only: nothing here needs the
// data repository, and scripts/fixtures/record_locator_fixture.mjs gives each input to its
// TypeScript.
//
// The store holds 52,278 locators and not one character beyond U+FFFF, no letter that
// JavaScript folds onto an ASCII one and no line separator, and a port that got any of those
// wrong would still read every one of them as the TypeScript does. So they are written
// here: locators made by hand for each rule of the grammar, and a few thousand put together
// from parts by a seeded generator, some of them with an awkward character dropped in.

/** A small seeded generator (mulberry32), so the cases are the same on every run. */
function seeded(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let mixed = state;
    mixed = Math.imul(mixed ^ (mixed >>> 15), mixed | 1);
    mixed ^= mixed + Math.imul(mixed ^ (mixed >>> 7), mixed | 61);
    return ((mixed ^ (mixed >>> 14)) >>> 0) / 4294967296;
  };
}

const anchors = [
  "Table S18", "Table 3", "Tables 2", "Table 4b", "Table 2.1", "Table A.2", "Table IV", "Table XIV.",
  "Supplementary Table 9", "Supplementary Materials Table S3", "Appendix B Table 2", "Appendix Table A2",
  "Extended Data Table 1", "Supplemental Table S2", "Supporting Information Table S1", "table s4",
  "Fig. 3b", "Figure 2", "Figures 4", "Fig 5a", "Extended Data Figure 4", "Supplementary Figure S7",
  "sheet 'Table S9'", "sheet \"Main results\"", "sheet Data B12", "sheets Results", "sheet \u2018Scores\u2019",
  "'Suppl Table 3 Track performance'!K8", "Sheet1!B2", "results.csv", "data/metrics.json.gz", "scores.xlsx",
  "leaderboard.json", "path/to/file(1).tsv", "Supplementary Data 2", "Supplementary Dataset", "Zenodo",
  "Additional file 3", "Data file S2", "Source Data", "Online Methods", "Methods", "Results", "Appendix C",
  "Supplementary Note 4", "Supplementary file 1b",
];
const addresses = [
  "cell I9", "cells A1:B2", "cells C3", "!C3", "I10", "AB123", "ABCD12", "line 12", "lines 3", "line 12345678",
  "row 7", "rows 12", "data row 12", "row 3:", "XML row 3, column 4", "XML data tr row 3 col 2",
  "entries[63].score_avg", "$.a.b[0]", "data.10shot", "leaderboard.json data.10shot", "results[0][1]",
  "root.metrics.f1 (macro)", "data.csv", "results.yaml", "sentence beginning 'The model reached'",
  "sentences starting \"We find\"", "sentence reading 'abc'", "sentence 4",
  "'a quoted passage of more than forty characters, taken as it stands'",
  "\"a second quoted passage that is long enough to be read as a quote\"", "paragraph 3", "para. 2",
  "para 12", "bullet 4", "paragraph 1234",
];
const selectors = [
  "row 'Bacformer'", "column 'Binary AUPRC'", "row ESMC-300M naive supervised", "column spearman",
  "tool=alignGVGD_a", "Tool=UltraBinner 1.0 (E1)", "column Adjusted Rand index (bp)", "panel 'A'",
  "'Top' block", "\"Zero-shot\" section", "Panel B", "panel c", "metric: 'F1'", "sub-column 'mean'",
  "sub-col 'sd'", "column 1 of 4 'x'", "column 2 'y'", "key x", "model \"it's\"", "row label 'x'",
  "column group 'y'", "metric block 'AUROC'", "row group: 'ours'", "subtable='B'", "sub-table 'C'",
  "strategy 'fine-tune'", "dataset ProteinGym", "split test", "method=ESM-2 (650M)", "row 'Ridge (one-hot), tuned'",
  "col 'r'", "columns 'a'", "field score", "rows 'x'", "x = y", "a/b=c", "50% 'half'", "identification=T315_P36.M01",
  "tier 2", "depth 30x", "value block 'x'", "model", "row  'spaced'", "ROW 'UPPER'", "Column \u2018curly\u2019",
];
const qualifiers = [
  "(page 4)", "page 4", "p. 12", "pp. 3 of 9", "pages 10", "PDF page 4", "(PDF page text)", "text-layer line 12",
  "(text-layer)", "text-layer", "at commit abc1234", "at commit 0123456789abcdef0123456789abcdef01234567",
  "(a note)", "(see the footnote)", "JSON entries[63].score_avg", "JSON results.f1", "(sheet 'Table S9') cell I9",
  "(sheets 'A')", "(XML tbl1)", "XML", "SI", "HTML", "JATS table 2", "source CSV", "source data file",
  "CSV", "TSV", "PDF caption", "PDF text \u2013 page", "(a note that is far too long to be read as a note in brackets)",
  "()", "(nested (note))",
];
const noise = [
  "and the value", "footnote a", "see also", "v2", "unnamed", "\u00b10.3", "\u2192", "the mean of the first row",
  "as printed", "Header", "bold", "1", "x", "top left", "rounded to two", "n/a", ":", "|", "[1]", "-",
];
const awkward = [
  "\u00a0", "\u017f", "\u212a", "\u0130", "\u0131", "\u0345", "\ud83d\ude00", "\ud83d", "\ude00", "\u2028", "\u2029",
  "\u00e9", "\u0394", "\u2018", "\u2019", "\u201c", "\u201d", "\ufeff", "\t", "\n", "\r", "\ud835\udc00", "\u01c5",
  "\u00df", "\u1e9e", "\u0663", "\u2163", "\u3000", "\u2013", "'", "\"", "(", ")", "[", "]", "!", "=", ":", ".",
  "\u1c89", "\u0085", "\u001c", "\ud801\udc00", "\u00b5", "\u03a3", "\uff21",
];
const joins = [", ", "; ", " ", ",", ";", ", ", "; ", " ", " - ", ": ", " (", ") ", "\t", " and "];

/** Locators written for one rule each. */
export const handwritten = [
  "",
  " ",
  ",;,",
  "Table S18, row 'Bacformer', column 'Binary AUPRC'",
  "Table A2; row ESMC-300M naive supervised; column spearman",
  "Table IV, row 'X', column 'Accuracy'",
  "'Suppl Table 3 Track performance'!K8",
  "Supplementary Table 9, row 'BRCA2', column 'LR'",
  "Table 1 (XML tbl1); row Boltz R; column angle_rmsd; footnote a",
  "Table 4, row 'Ridge (one-hot), tuned'; column 'spearman'",
  "metrics.ndcg",
  "Supplementary Table 9 (sheet 'SupTable9_Likelihood_Ratios'), I10; tool 'alignGVGD_a'; column 'BRCA2_negative_LR'",
  "line 12; Tool=UltraBinner 1.0 (E1); column Adjusted Rand index (bp)",
  "Table S9 (sheet 'Table S9') cell I9",
  "leaderboard.json data.10shot",
  "JSON entries[63].score_avg",
  // A quote opened by a curly mark is closed only by a straight one.
  "row \u2018a, b\u2019, column 'c'",
  "row \u2018a, b', column 'c'",
  "row \"a; b\", column c",
  "row 'it\"s; here', column c",
  // Brackets hold a comma, and a closing one with nothing open does nothing.
  "a (b, c), d [e; f], g) , h",
  "a ))) (b, c",
  // More tokens in one segment than the grammar takes.
  Array.from({ length: 45 }, (_, at) => `(n${at})`).join(" "),
  Array.from({ length: 40 }, (_, at) => `(n${at})`).join(" "),
  Array.from({ length: 41 }, (_, at) => `(n${at})`).join(" "),
  Array.from({ length: 45 }, (_, at) => `(n${at})`).join(", "),
  // Counted in units without the u flag and in characters with it.
  `sentence beginning '\ud83d\ude00\ud83d\ude00'`,
  `sentence beginning '\ud83d\ude00a'`,
  `'${"\ud83d\ude00".repeat(20)}'`,
  `'${"\ud83d\ude00".repeat(40)}'`,
  `'${"a".repeat(39)}'`,
  `'${"a".repeat(40)}'`,
  `sheet '${"\ud83d\ude00".repeat(30)}'`,
  `sheet '${"\ud83d\ude00".repeat(31)}'`,
  `sheet ${"\ud83d\ude00".repeat(31)} B2`,
  `row ${"\ud83d\ude00".repeat(40)}, column x`,
  `row a${"\ud83d\ude00".repeat(40)}, column x`,
  `(${"\ud83d\ude00".repeat(22)})`,
  `(${"\ud83d\ude00".repeat(23)})`,
  `x=${"\ud83d\ude00".repeat(81)}`,
  `x${"\ud83d\ude00".repeat(41)}=y`,
  `${"\ud83d\ude00".repeat(51)}!B2`,
  // Letters JavaScript folds onto ASCII ones with both flags, and with neither.
  "J\u017fON entries[1].x",
  "C\u017fV file",
  "re\u017fults.c\u017fv",
  "\u017fheet 'A'",
  "Table \u017f18",
  "\u212aey 'x'",
  "\u212aEY x",
  "PDF\u017f",
  "\u017fSI",
  "SI\u212a",
  "text-layer\u0345 line 3",
  "PDF \u0345\u0345 page",
  "file\u0345.csv",
  // The layer qualifier stands before the XML cell and the text-layer line and matches where
  // they would, so it takes both. It has the i and u flags, under which a long s and a Kelvin
  // sign are word characters, and the other two have i alone. So after one of those two
  // letters its word boundary fails and theirs holds, which is the only way they are reached.
  "\u017fXML row 3 col 2",
  "\u017fXML data tr row 3, column 4",
  "Table 1, \u212aXML row 12 column 7, row 'a'",
  "\u212atext-layer line 12",
  "Table 2 (\u017ftext-layer line 7)",
  // White space as JavaScript has it.
  "Table\u00a03,\u2028row\u3000'x',\ufeffcolumn\u00a0'y'",
  "\ufeffTable 3\ufeff",
  "Table 3\u0085row 'x'",
  "Table 3\u001crow 'x'",
  "Table 3\nrow 'x'\ncolumn 'y'",
  "Table S1, row 'a\nb', column 'c'",
  "Table S1 x\u2028row 'a', column 'c'",
  // Half a character.
  "Table 3 \ud83d",
  "\ude00 Table 3",
  "row \ud83d, column \ude00",
  "sheet '\ud83d'!B2",
  // The classes, by their own rules.
  "Appendix Table 3, row 'a', col 'b', sub-col 'c'",
  "Extended Data Table 3b row \"a\" column \"b\"",
  "Table 3, row 'a\"",
  "Table I row 'x'",
  "Table C, x",
  "Table A.2 row 'x'",
  "Table 2.1, row 'x', column 'y'",
  "Table 3, row x",
  "Table 3, arrow 'x'",
  "Table 3, column 'y'",
  "see Table 3",
  "'Suppl",
  "fig. 3",
  "FIGURE 3",
  "Supplementary fig. 3",
  "Figures",
  "x data.a",
  "x,data.a",
  "xdata.a",
  "$[0]",
  "LINES 4",
  "row3",
  "Col x",
  "column",
  "columns",
  "  Table 3, row 'x', column 'y'  ",
  "\nTable 3 row 'x'",
  "Table 3\n, row 'x'",
];

const pick = (random, items) => items[Math.floor(random() * items.length)];

/** Locators put together from parts, the same ones on every run. */
export function generated(count, seed) {
  const random = seeded(seed);
  const pools = [anchors, addresses, selectors, selectors, qualifiers, noise];
  const made = [];
  for (let at = 0; at < count; at += 1) {
    const parts = 1 + Math.floor(random() * 5);
    let text = "";
    for (let part = 0; part < parts; part += 1) {
      let piece = pick(random, pick(random, pools));
      const roll = random();
      if (roll < 0.06) piece = piece.toUpperCase();
      else if (roll < 0.12) piece = piece.toLowerCase();
      else if (roll < 0.3) {
        const where = Math.floor(random() * (piece.length + 1));
        piece = piece.slice(0, where) + pick(random, awkward) + piece.slice(where);
      }
      text += (part === 0 ? "" : pick(random, joins)) + piece;
    }
    made.push(text);
  }
  return made;
}

/** Keys, for selectorRole. */
export const keys = [
  "row", "rows", "row label", "column", "col", "columns", "sub-column", "sub-col", "key", "field",
  "column group", "model", "", "Row", "column 4", "column 1 of 4", "row 12", "row label 2 of 3",
  "column  7", "column\u00a04", "column 4 ", " column", "column 1 of", "column of 4", "column 4 of 4 of 4",
  "col\u00a01\u2028of\u30004", "column \u0664", "row\n3", "tool 2", "column 4\n", "row \ud83d\ude00 1", "key 1",
];

/** Words, for carriesNoAddress. */
export const words = [
  "and", "AND", "the", "value", "v2", "V10", "v", "v2a", "row", "rows", "table", "and the", "andx", "x",
  "", "five", "six", "\u017fee", "see", "\u017fub", "sub", "\ud83d\ude00", "and\ud83d\ude00", " and", "and ",
  "\u212a", "under", "Supplementary", "fig",
];

const anchor = (kind, value, raw, container) => ({ kind, value, raw, ...(container === undefined ? {} : { container }) });
const address = (kind, value, value2) => ({ kind, value, raw: "", ...(value2 === undefined ? {} : { value2 }) });
const selector = (key, value, form) => ({ key, value, form, raw: "" });
const qualifier = (kind, text) => ({ kind, text });
const allAddresses = [
  address("sheet_cell", "I9"), address("cell_range", "A1", "B2"), address("cell_range", "A1"),
  address("cell_range", "A1", ""), address("line", "12"), address("row_number", "7"),
  address("xml_cell", "3", "4"), address("xml_cell", "3"), address("xml_cell", "", ""), address("json_path", "$.a[0]"),
  address("quote", "said so"), address("quote", "it's said"), address("sentence", "The model"),
  address("sentence", "it's"), address("paragraph", "3"),
];

/** Locators as typed fields, for renderLocator: as a parse gives them and as one is authored. */
export const rendering = [
  { anchors: [], selectors: [], qualifiers: [] },
  { anchors: [], addresses: [], selectors: [], qualifiers: [] },
  {
    anchors: [
      anchor("table", "S18", "Table S18"), anchor("table", "9", "", "Supplementary"), anchor("figure", "3b", ""),
      anchor("figure", "4", "", "Extended Data"), anchor("sheet", "Table S9", ""), anchor("sheet", "it's", ""),
      anchor("sheet", "x", "", "Workbook"), anchor("file", "a/b.csv", ""), anchor("container", "2", "", "Supplementary Data"),
      anchor("container", "Zenodo", ""), anchor("table", "1", "", ""),
    ],
    addresses: allAddresses,
    selectors: [
      selector("row", "Bacformer", "quoted"), selector("row", "it's", "quoted"), selector("tool", "x", "equals"),
      selector("column", "bp", "bracketed"), selector("column", "spearman", "bare"), selector("model", null, "bare"),
      selector("model", null, "quoted"), selector("row", "", "quoted"), selector("", "", "equals"),
    ],
    qualifiers: [
      qualifier("page", "(page 4)"), qualifier("page", "page 4"), qualifier("note", "(a note"), qualifier("note", "a note)"),
      qualifier("note", "("), qualifier("note", ")"), qualifier("note", "()"), qualifier("note", "((x))"), qualifier("layer", ""),
    ],
  },
  ...allAddresses.map((held) => ({ anchors: [anchor("table", "1", "")], address: held, selectors: [], qualifiers: [] })),
  {
    anchors: [anchor("sheet", "Data", "")],
    address: address("sheet_cell", "I9"),
    refines: [address("sheet_cell", "J9"), address("line", "3")],
    selectors: [selector("row", "x", "quoted")],
    qualifiers: [qualifier("note", "(with its uncertainty)")],
  },
  { anchors: [anchor("table", "1", "")], refines: [address("line", "3")], selectors: [], qualifiers: [] },
  { anchors: [anchor("table", "1", "")], refines: [], selectors: [], qualifiers: [] },
  // Addresses as a parse gives them are used, and the authored ones beside them are not.
  {
    anchors: [anchor("table", "1", "")],
    addresses: [address("line", "1")],
    address: address("line", "2"),
    refines: [address("line", "3")],
    selectors: [],
    qualifiers: [],
  },
  { anchors: [], addresses: [], address: address("line", "2"), selectors: [], qualifiers: [] },
];

/** Parses built by hand, for verdictOf: every mix of what is named and what is addressed. */
export function verdictCases() {
  const made = [];
  const selectorSets = [
    [], ["row"], ["column"], ["model"], ["row", "column"], ["model", "column"], ["row", "model"],
    ["row 2", "column 1 of 4"], ["row label"], ["key"], ["Row"],
  ];
  const addressSets = [
    [], ["sheet_cell"], ["xml_cell"], ["json_path"], ["line"], ["row_number"], ["quote"], ["sentence"],
    ["paragraph"], ["cell_range"], ["line", "sheet_cell"], ["quote", "json_path"], ["cell_range", "line"],
  ];
  for (const anchored of [false, true]) {
    for (const residue of [[], ["footnote a"]]) {
      for (const kinds of addressSets) {
        for (const names of selectorSets) {
          made.push({
            printed: "",
            anchors: anchored ? [anchor("table", "1", "")] : [],
            addresses: kinds.map((kind) => address(kind, "1")),
            selectors: names.map((key) => selector(key, "x", "quoted")),
            qualifiers: [],
            residue,
            verdict: "no_anchor",
          });
        }
      }
    }
  }
  return made;
}

/** Lists of locators, for tallyLocators and the report. */
export const tallied = [
  [],
  ["Table S18, row 'Bacformer', column 'Binary AUPRC'"],
  [
    "Table S18, row 'Bacformer', column 'Binary AUPRC'", "Table S18, row 'Bacformer', column 'Binary AUPRC'",
    "Table A2; row ESMC-300M naive supervised; column spearman", "Table IV, row 'X', column 'Accuracy'",
    "'Suppl Table 3 Track performance'!K8", "metrics.ndcg", "Fig. 3b", "Figure 2, panel 'A'",
    "Table 1 (XML tbl1); row Boltz R; column angle_rmsd; footnote a", "line 12; column x",
    "entries[63].score_avg", "identifier x; column y", "Table A.2, row 'x', column 'y'", "Table 3, column 'y'",
    "Table 3, row x, column y", "sheet 'Data', cell I9", "sheet 'Data', cell I9, row 'x'",
    "row \ud83d\ude00, column x", "",
    // Fully addressed, and its rendering is not read back as the same structure.
    "JSON entries[63].score_avg; submission u2MMaChop9DM3fjqkS7z",
  ],
  // One locator in 400 fully addressed is 0.25%, which toFixed writes as 0.3.
  [...Array.from({ length: 399 }, () => "metrics.ndcg"), "Table S18, row 'Bacformer', column 'Binary AUPRC'"],
  // One in eight is 12.5%, and three in eight 37.5%.
  [...Array.from({ length: 5 }, () => "Figure 2"), ...Array.from({ length: 3 }, () => "Figure 2, row 'a', column 'b'")],
];

const result = (id, locator) => JSON.stringify({ id, kind: "result", attributes: { source_locator: locator } });
const someResults = [
  result("r-1", "Table S18, row 'Bacformer', column 'Binary AUPRC'"),
  result("r-2", "Table A2; row ESMC-300M naive supervised; column spearman"),
  result("r-3", "'Suppl Table 3 Track performance'!K8"),
  result("r-4", "metrics.ndcg"),
  result("r-5", "Fig. 3b \u2013 panel \ud83d\ude00"),
].join("\n");

/** Runs of the command, each in a folder holding these files. */
export const commands = [
  { name: "coverage of a store with a few results, written where it is told", files: { "data/evidence/results/0.jsonl": `${someResults}\n` }, args: ["coverage", "--out", "out/report.tsv"] },
  { name: "coverage written under today's date when it is not told where", files: { "data/evidence/results/0.jsonl": `${someResults}\n` }, args: ["coverage"] },
  {
    name: "coverage reads every file that ends in .jsonl, in order, and only results with a locator that is text",
    files: {
      "data/evidence/results/b.jsonl": `${result("r-b", "Figure 2")}\n\n   \n${JSON.stringify({ id: "s-1", kind: "source", attributes: { source_locator: "Table 1" } })}\n`,
      "data/evidence/results/a.jsonl": [
        result("r-a", "Table 3, row 'x', column 'y'"),
        JSON.stringify({ id: "r-none", kind: "result" }),
        JSON.stringify({ id: "r-null", kind: "result", attributes: null }),
        JSON.stringify({ id: "r-list", kind: "result", attributes: ["source_locator"] }),
        JSON.stringify({ id: "r-text", kind: "result", attributes: "source_locator" }),
        JSON.stringify({ id: "r-number", kind: "result", attributes: { source_locator: 4 } }),
        JSON.stringify({ id: "r-empty", kind: "result", attributes: { source_locator: "" } }),
        "17", "\"result\"", "[]", "{}", `${result("r-cr", "line 4")}\r`,
      ].join("\n"),
      "data/evidence/results/Z.jsonl": result("r-Z", "line 3; column x"),
      "data/evidence/results/c.json": result("r-c", "line 9"),
      "data/evidence/results/notes.txt": "not read",
    },
    args: ["coverage", "--out", "report.tsv"],
  },
  { name: "coverage of a store with no results", files: { "data/evidence/results/0.jsonl": "" }, args: ["coverage", "--out", "report.tsv"] },
  { name: "coverage with --out and nothing after it", files: { "data/evidence/results/0.jsonl": `${someResults}\n` }, args: ["coverage", "--out"] },
  { name: "coverage where there is no store", files: {}, args: ["coverage", "--out", "report.tsv"] },
  { name: "coverage of a line that is not JSON", files: { "data/evidence/results/0.jsonl": "{nope\n" }, args: ["coverage", "--out", "report.tsv"] },
  { name: "coverage of a row that is null", files: { "data/evidence/results/0.jsonl": "null\n" }, args: ["coverage", "--out", "report.tsv"] },
  { name: "parse of one locator", files: {}, args: ["parse", "Table S18, row 'Bacformer', column 'Binary AUPRC'"] },
  { name: "parse of a locator given as several arguments", files: {}, args: ["parse", "Table", "S9", "(sheet 'Table S9')", "cell", "I9"] },
  { name: "parse of a locator that addresses nothing", files: {}, args: ["parse", "metrics.ndcg \ud83d\ude00"] },
  { name: "parse of a container and its number", files: {}, args: ["parse", "Supplementary Data 2, cells A1:B2, XML row 3 column 4 (page 2)"] },
  { name: "parse with nothing to parse", files: {}, args: ["parse"] },
  { name: "parse of nothing", files: {}, args: ["parse", ""] },
  { name: "no command", files: {}, args: [] },
  { name: "a command there is not", files: {}, args: ["measure"] },
];
