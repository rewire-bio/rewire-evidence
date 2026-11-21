// Inputs for record_checks_fixture.mjs: printed cells, numbers, locators, small stores and
// baselines for the report-only checks. Nothing here needs the data repository's code.
//
// Every record has a text ID, and source IDs that are a list or absent, which is what the
// Python port holds itself to. Everything else about a record is free.

// ---------------------------------------------------------------------------------------
// Printed cells, for parseCell and isMissingCell. Each is the cell and the unit of its column.
// ---------------------------------------------------------------------------------------

const printed = [
  "0.5", "-0.5", "\u22120.5", "\u20130.5", "\u20140.5", "\u20100.5", "\u20150.5", "+0.5", ".5", "5.", "-.5", "00012", "-0", "\u22120.0",
  "0.31 \u00b1 0.01", "0.31\u00b10.01", "0.31 (0.01)", "0.31 (0.01", "0.31(0.01)", "0.31 \u00b1 (0.01)", "0.31 ( \u00b1 0.01)",
  "45 (59.2%)", "4,205 (264)", "59.2%", "59.2 %", "59.2% ", "12 (3%)", "0.5%%", "% 5", "5 % (1 %)", "5% \u00b1 1%",
  "1\u00d710\u22122", "1\u00d710^-2", "1\u00d710^{-2}", "1.5\u00d710^{3}", "1.5\u00d710^{3", "1.5\u00d710^3}", "9\u00d710\u22124 \u00b1 1\u00d710\u22125", "1\u00d710^999", "1\u00d710^-999",
  "1\u00d710\u22127", "1\u00d710^21", "0.1\u00d710^1", "-1\u00d710\u22122", "1\u00d710", "\u00d710^2", "1 \u00d7 10 ^ 2", "1x10^2", "1\u00d710^+2", "1\u00d710^2.5", ".5\u00d710^1",
  "4.03e-04", "4.03E+04", "1e999", "-1e999", "1e5", "1E5 (2e1)", "1e-7", "1e", "e5", "1e5.5", "1e+", "0x10", "1_000", "Infinity", "-Infinity", "NaN5",
  "", " ", "\t", "-", "--", "---", "\u2013", "\u2014", "\u2012", "\u2212", "n/a", "N/A", "na", "NA", "Na", "NaN", "nan", "nAn", ".", "/", "..", "n//a", "n\\a", "n /a", " n/a ", "none", "null",
  "\u017f", "NA\u212a", "N\u2215A", "\u0274a", "\uff4e\uff41", "n\u0131a",
  "0.5*", "0.5\u2020", "0.5\u2021", "\u21910.5", "0.5 \u2193", "*0.5*", "0.5**", "0.5\u00a7", "0.5\u00b6", "0.5a", "0.5^a",
  "\ud835\udfce.\ud835\udfd3", "\ud835\udfec.\ud835\udff5\ud835\udfed \u00b1 \ud835\udfec.\ud835\udfec\ud835\udfed", "\ud835\udfff", "\ud835\udfcd", "\ud835\ude00", "\ud835\udfd8\ud835\udfe2\ud835\udff6", "1\ud835\udfce", "\ud835\udfce (\ud835\udfcf)",
  "0.31 \u00b1plus-or-minus\u00b1 0.01", "0.31\u00b1plus-or-minus\u00b10.01", "\u00b1plus-or-minus\u00b1", "0.31 plus-or-minus 0.01",
  "1,234.5", "1 234.5", "1\u00a0234", "1\u202f234", "1\u2009234", "1\ufeff234", "1\u200b234", "1\u0085234", "1\u001c234", "1.234,5", "1,2,3", ",5", "5,",
  "abc", "0.5 abc", "0.5 (abc)", "0.5 \u00b1 -0.1", "0.5 (\u22120.1)", "0.5 (-0)", "0.5 \u00b1 0", "0.5 (0.1) (0.2)", "0.5 \u00b1 0.1 \u00b1 0.2", "(0.5)", "\u00b10.5", "0.5 ()",
  "0.5 ( )", "0.5 (0.1))", "0.5\t\u00b1\n0.1", "0.5 [0.4, 0.6]", "0.5 (0.4, 0.6)", "0.5 (0.4-0.6)", "0.5 (0.4\u20130.6)", "0.5 \u00b1 0.1 (n=3)", "0.5 (n=3)", "0.5 (3)",
  "0.5 (1e999)", "0.5 \u00b1 1\u00d710^999", "0.5 (\u00b10.1)", "0.5\u00b1", "0.5(", "0.5 \u00b1 ", "a (0.1)", "(0.1) 0.5", "0.5 \u00b1 0.1)", "0.5 \u00b1 0.1%", "0.5% (0.1%)",
  "0.5\ud83d", "\ud835", "\udfce", "0.5 \ud83d\ude00", "\ud83d\ude00", "0.5 (\ud83d\ude00)", "0.5\u2028", "\ufeff0.5", "0.5\n", "\r\n0.5\r\n", "0 . 5", "0.5 \u00b1\u00b1 0.1",
  "123456789012345678901234567890", "0.000000000000000000001", "1e21", "1e-7 (1e-8)", "9007199254740993", "1.7976931348623157e308", "1.7976931348623159e308", "5e-324", "2e-324",
  "12.50", "0.125", "100", "1e2", "1.0e2", "-12.5 (0.5)", "12.5 (\u221212.5)",
];

export const cells = [
  ...printed.map((cell) => [cell, null]),
  ...printed.map((cell) => [cell, "percent"]),
  ["59.2%", "fraction"], ["59.2%", ""], ["59.2%", "Percent"], ["59.2%", "percent "],
];

export const missing = [...printed, "  -  ", "\u00a0\u2014\u00a0", "\ufeffn/a", "N/a\u2028", "\u0085-", "-\u001f"];

// ---------------------------------------------------------------------------------------
// Numbers: Math.round, 10 ** n, and the two comparisons of C3.
// ---------------------------------------------------------------------------------------

export const rounded = [
  "0", "-0", "0.4", "0.5", "0.49999999999999994", "0.5000000000000001", "1.5", "2.5", "-0.4", "-0.5", "-0.5000000000000001", "-1.5", "-2.5", "-0.49999999999999994",
  "4503599627370495.5", "4503599627370496", "4503599627370497", "9007199254740991", "9007199254740992", "9007199254740993", "-4503599627370495.5", "-9007199254740993",
  "1e21", "1.7976931348623157e308", "5e-324", "-5e-324", "Infinity", "-Infinity", "NaN", "12.5", "12.49", "1250", "0.125", "125.49999999999999", "125.5", "1.005", "100.49999999999999",
];

/** Every n for which 10 ** n is recorded. */
export const powers = Array.from({ length: 331 }, (_, n) => n);

/** Pairs for same(a, b), as text. */
export const samePairs = [
  ["0", "0"], ["0", "-0"], ["1", "1"], ["1", "1.000000000001"], ["1", "1.0000000000011"], ["1", "1.0000000000009"], ["1", "0.999999999999"], ["1e300", "1.000000000001e300"],
  ["0", "1e-320"], ["0", "5e-324"], ["1e-320", "1.00001e-320"], ["-1", "1"], ["-1", "-1.0000000000005"], ["Infinity", "Infinity"], ["Infinity", "-Infinity"], ["Infinity", "1"],
  ["1", "Infinity"], ["-Infinity", "5"], ["NaN", "NaN"], ["NaN", "1"], ["1", "NaN"], ["NaN", "Infinity"], ["Infinity", "NaN"], ["0.0009", "0.0009000000000000001"], ["0.1", "0.10000000000001"],
];

/** A value and the printed decimal it may round to, for roundsTo(value, printed). */
export const roundings = [
  ["0.125", "0.13"], ["0.125", "0.12"], ["0.135", "0.14"], ["0.145", "0.15"], ["0.145", "0.14"], ["-0.125", "-0.13"], ["-0.125", "-0.12"], ["2.5", "3"], ["2.5", "2"], ["-2.5", "-3"], ["-2.5", "-2"],
  ["1.005", "1.01"], ["1.005", "1.00"], ["0.5", "1"], ["0.5", "0"], ["-0.5", "-1"], ["-0.5", "0"], ["-0.5", "-0"], ["0.4", "0"], ["-0.4", "-0"], ["-0.4", "0"], ["0", "0"], ["-0", "0"], ["0", "0.00"],
  ["123.456", "123"], ["123.456", "123.5"], ["123.456", "123.46"], ["123.456", "123.4560"], ["123.456", "1.23456e2"], ["123.456", "1E2"], ["123.456", "123."], ["123.456", ".5"],
  ["0.1", "0." + "1".padEnd(30, "0")], ["0.1", "0." + "1".padEnd(330, "0")], ["0", "0." + "0".repeat(330)], ["1e300", "1" + "0".repeat(300) + ".000000000"], ["1e300", "1" + "0".repeat(300) + ".0000000000"],
  ["5e-324", "0"], ["5e-324", "0." + "0".repeat(323) + "5"], ["1.7976931348623157e308", "1.8"], ["99.995", "100.00"], ["99.994999", "99.99"], ["0.000001", "0.00000"], ["0.0000005", "0.000001"],
  ["12.345", "12.35"], ["12.345", "12.34"], ["1234.5", "1235"], ["1234.5", "1234"], ["0.285", "0.29"], ["0.285", "0.28"], ["8.345", "8.35"], ["8.345", "8.34"], ["1.45", "1.5"], ["1.45", "1.4"],
  ["1", "1.2.3"], ["1.2", "1.2.3"], ["1", "abc"], ["1", ""], ["0", ""], ["16", "0x10"], ["1", "1.x"],
];

// ---------------------------------------------------------------------------------------
// Text made one line, and locators read for their anchor and their column.
// ---------------------------------------------------------------------------------------

export const lines = [
  "plain", " padded ", "a\tb", "a\nb", "a\r\nb", "a\t\t\n\rb", "\ta\t", "\n", "", "a\u2028b", "a\u000bb", "a\u000cb", "\ufeffa\u00a0", "a  b", "\ud83d\ude00\tx", "\ud83d\tx", "\u0085a\u0085", "\u001fa\u001f",
];

export const locators = [
  "Table 1, column 'AUROC'", "Table 2, row 'X', column \"F1\"", "table 3 column(AUPRC)", "Table 4, column MCC, row 5", "TABLE 5; COLUMN 'Top-1'", "Tables 2, column 'a'", "tables2 column 'a'",
  "Supplementary Table 6, column 'x'", "Supplemental Table S6, column 'x'", "Extended Data Fig. 3b, column 'x'", "Extended Data Figure 3, column 'x'", "Additional file 2 Table S3, column 'x'",
  "Additional file 2, Table S3, column 'x'", "Additional file Table S3, column 'x'", "Appendix Table A1, column 'x'", "Appendix Supplementary Extended Data Table 1, column 'x'",
  "Supplementary  Table\t6, column 'x'", "  Table 7, column 'x'", "\ufeffTable 7, column 'x'", "\u00a0Table\u00a07, column 'x'", "Table\n8, column 'x'", "Table S12, column 'x'", "Table S1a, column 'x'",
  "Table SI12, column 'x'", "Table ABC1, column 'x'", "Table 1.2.3, column 'x'", "Table 1.2.a, column 'x'", "Table 1ab, column 'x'", "Table A, column 'x'", "Table, column 'x'", "Table IV, column 'x'",
  "Fig 2, column 'x'", "Fig. 2, column 'x'", "Fig.2, column 'x'", "Figure S2A, column 'x'", "figure2b column 'x'", "Fig.. 2, column 'x'", "Figs 2, column 'x'", "Figures 2, column 'x'",
  "See Table 1, column 'x'", "Section 3, Table 1, column 'x'", "Page 4, column 'x'", "column 'x'", "", " ", "Table 1", "Table 1, row 'a'", "Table 1, columns 'a' and 'b'", "Table 1, column",
  "Table 1, column ''", "Table 1, column \"\"", "Table 1, column ()", "Table 1, column ' '", "Table 1, column  ,", "Table 1, column ;", "Table 1, column '\"'", "Table 1, column \"'\"",
  "Table 1, column group 'G', column 'x'", "Table 1, column 'x', column group 'G'", "Table 1, column group 'G'", "Table 1, column groupie", "Table 1, column  group  'G', column  'x'",
  "Table 1, column Group \"G\"; column \"A  B\"", "Table 1, column groups 'G'", "Table 1, column group", "Table 1, columngroup 'x'", "Table 1, column\ngroup 'G'",
  "Table 1, column 'a', column 'b'", "Table 1, column 'a', column ''", "Table 1, column 'a'; column \"B\"; column (C)", "Table 1, column (a (b))", "Table 1, column 'it''s'",
  "Table 1, column \"a 'b' c\"", "Table 1, column 'a \"b\" c'", "Table 1, column a 'b' c, row 2", "Table 1, column a\tb\n c", "Table 1, column 'A\u00a0\u2003B'", "Table 1, column x; y",
  "Table 1, COLUMN 'X'", "Table 1, Column \u2018X\u2019", "Table 1, column \u201cX\u201d", "Table 1, subcolumn 'x'", "Table 1, column_'x'", "Table 1, column-'x'", "Table 1, columnx", "Table 1, column: 'x'",
  "Table 1, column=x", "Table 1, column 'x", "Table 1, column \"x", "Table 1, column (x", "Table 1, column x)", "Table 1, column 'x' (percent)", "Table 1, column\u00a0'x'", "Table 1, column\ufeffx",
  "Table 1, column '\u0130'", "Table 1, column '\u0391\u03a3'", "Table 1, column '\u0391\u03a3 \u03a3'", "Table 1, column '\ud801\udc00'", "Table 1, column '\ud83d\ude00'", "Table 1, column \ud83d", "Table 1, column '\u212a'",
  "Table 1, column '\u00c9COLE'", "Table 1, \u212aolumn 'x'", "Table 1, c\u00f6lumn 'x'", "Table 1, colum\u0274 'x'", "\u0164able 1, column 'x'", "TABLE 1, column 'x'", "Tab\u017fe 1, column 'x'", "Table \u0661, column 'x'",
  "Table \uff11, column 'x'", "Table 1\u0661, column 'x'", "FIG. 1, column 'x'", "Extended  Data  Table 1, column 'x'", "Additional file 12, Table 1, column 'x'", "Additional file 1,, Table 1, column 'x'",
  "Supplementary Table\u00a0S1, column 'x'", "Supplementary Figure 1.2, column \"Mean \u00b1 SD\"", "Table 1, column 'a,b'", "Table 1, column a,b", "Table 1, column \"a;b\"", "Table 1, column a;b",
  "Table 1, row 'column', column 'x'", "Table 1, row \"column 'y'\", column 'x'", "Table 1, column 'x', row 'column'", "Table 1, column 'x', row 'in column y'",
];

// ---------------------------------------------------------------------------------------
// Small stores for the whole pack. Each is the records, the metric concepts with their
// default direction, and the unit concepts with their scope note.
// ---------------------------------------------------------------------------------------

const result = (id, attributes, more = {}) => ({ id, kind: "result", status: "source_checked", source_ids: ["s1"], links: [], attributes, ...more });
const source = (id, attributes, more = {}) => ({ id, kind: "source", status: "source_checked", source_ids: [], links: [], attributes, ...more });

const vocabulary = {
  metrics: [["auroc", "higher"], ["rmse", "lower"], ["count", null], ["f1-score", "higher"], ["accuracy", "higher"], ["tm-score", "higher"], ["top-1-mces", "lower"], ["spearman-correlation", "higher"], ["odd", "sideways"]],
  units: [["fraction", "Only for proportions."], ["unitless", "Printed x100 stays unitless."], ["count", "Not for distances."], ["percent", null], ["seconds", null]],
};

const many = (count, make) => Array.from({ length: count }, (_, index) => make(index));

export const runs = [
  { name: "an empty store", records: [], ...vocabulary },
  { name: "an empty store with no vocabulary", records: [], metrics: [], units: [] },
  {
    name: "units for a metric: a majority, a tie that is one, and a split that is none",
    ...vocabulary,
    records: [
      ...many(3, (i) => result(`a-major-${i}`, { metric: "accuracy", unit: "percent" })),
      result("a-minor-0", { metric: "accuracy", unit: "fraction" }),
      result("a-minor-1", { metric: "accuracy", unit: "unitless" }),
      ...many(2, (i) => result(`b-half-${i}`, { metric: "f1-score", unit: "percent" })),
      ...many(2, (i) => result(`b-other-${i}`, { metric: "f1-score", unit: "fraction" })),
      ...many(2, (i) => result(`c-split-${i}`, { metric: "rmse", unit: "seconds" })),
      ...many(2, (i) => result(`c-split-b-${i}`, { metric: "rmse", unit: "count" })),
      result("c-split-c", { metric: "rmse", unit: "percent" }),
      ...many(2, (i) => result(`d-one-${i}`, { metric: "count", unit: "count" })),
      result("e-no-unit", { metric: "accuracy" }),
      result("e-empty-unit", { metric: "accuracy", unit: "" }),
      result("e-no-metric", { unit: "percent" }),
      result("e-empty-metric", { metric: "", unit: "percent" }),
      result("e-number-unit", { metric: "accuracy", unit: 5 }),
      result("e-null-metric", { metric: null, unit: "percent" }),
      result("e-list-metric", { metric: ["accuracy"], unit: "percent" }),
    ],
  },
  {
    name: "units for a metric: three units tied, where the first in text order is the majority of none",
    ...vocabulary,
    records: [result("x-1", { metric: "m", unit: "b" }), result("x-2", { metric: "m", unit: "a" }), result("x-3", { metric: "m", unit: "B" }), result("y-1", { metric: "n", unit: "\uff5a" }), result("y-2", { metric: "n", unit: "\ud83d\ude00" })],
  },
  {
    name: "units for a metric: two units equally common, ordered by UTF-16 unit and not by character",
    ...vocabulary,
    records: [result("y-1", { metric: "n", unit: "\uff5a" }), result("y-2", { metric: "n", unit: "\ud83d\ude00" }), result("z-1", { metric: "o", unit: "b" }), result("z-2", { metric: "o", unit: "a" })],
  },
  {
    name: "scope notes: each unit ruled out, and a metric two notes name",
    ...vocabulary,
    records: [
      result("f-1", { metric: "tm-score", unit: "fraction" }), result("f-2", { metric: "auroc", unit: "fraction" }), result("f-3", { metric: "auroc", unit: "percent" }), result("f-4", { metric: "auroc", unit: "unitless" }),
      result("f-5", { metric: "top-1-mces", unit: "count" }), result("f-6", { metric: "top-10-mces", unit: "count" }), result("f-7", { metric: "spearman-correlation", unit: "percent" }),
      result("f-8", { metric: "accuracy", unit: "fraction" }), result("f-9", { metric: "TM-score", unit: "fraction" }), result("f-10", { metric: "auroc-difference", unit: "fraction" }), result("f-11", { metric: "auroc-difference", unit: "percent" }),
    ],
  },
  {
    name: "scope notes: units that no longer carry one, or are not concepts",
    metrics: vocabulary.metrics,
    units: [["fraction", null], ["unitless", ""], ["percent", "a note on percent, which no rule reads"]],
    records: [result("g-1", { metric: "tm-score", unit: "fraction" }), result("g-2", { metric: "auroc", unit: "percent" }), result("g-3", { metric: "top-1-mces", unit: "count" })],
  },
  {
    name: "directions against the default",
    ...vocabulary,
    records: [
      result("h-1", { metric: "auroc", metric_direction: "higher" }), result("h-2", { metric: "auroc", metric_direction: "lower" }), result("h-3", { metric: "auroc", metric_direction: "unknown" }),
      result("h-4", { metric: "count", metric_direction: "higher" }), result("h-5", { metric: "count", metric_direction: "unknown" }), result("h-6", { metric: "count", metric_direction: "lower" }),
      result("h-7", { metric: "nothing", metric_direction: "higher" }), result("h-8", { metric: "nothing", metric_direction: "unknown" }), result("h-9", { metric: "auroc" }), result("h-10", { metric_direction: "higher" }),
      result("h-11", { metric: "auroc", metric_direction: "" }), result("h-12", { metric: "", metric_direction: "higher" }), result("h-13", { metric: "rmse", metric_direction: "sideways" }),
      result("h-14", { metric: "odd", metric_direction: "sideways" }), result("h-15", { metric: "odd", metric_direction: "higher" }), result("h-16", { metric: "rmse", metric_direction: "Lower" }),
      result("h-17", { metric: "auroc", metric_direction: "tab\there\nand a line" }), result("h-18", { metric: "a\tb", metric_direction: "higher" }),
    ],
  },
  {
    name: "printed against numeric: every rule",
    ...vocabulary,
    records: [
      result("i-01", { printed_value: "0.5", numeric_value: "0.5" }), result("i-02", { printed_value: "0.5", numeric_value: "0.6" }), result("i-03", { printed_value: "0.5", numeric_value: "50" }),
      result("i-04", { printed_value: "50", numeric_value: "0.5" }), result("i-05", { printed_value: "0.13", numeric_value: "0.125" }), result("i-06", { printed_value: "0.12", numeric_value: "0.125" }),
      result("i-07", { printed_value: "45 (59.2%)", numeric_value: "59.2", unit: "percent" }), result("i-08", { printed_value: "45 (59.2%)", numeric_value: "59.2" }), result("i-09", { printed_value: "4,205 (264)", numeric_value: "264" }),
      result("i-10", { printed_value: "abc", numeric_value: "1" }), result("i-11", { printed_value: "-", numeric_value: "1" }), result("i-12", { printed_value: "", numeric_value: "1" }),
      result("i-13", { printed_value: "0.5", numeric_value: "abc" }), result("i-14", { printed_value: "0.5", numeric_value: "Infinity" }), result("i-15", { printed_value: "0.5", numeric_value: "" }),
      result("i-16", { printed_value: "0.5", numeric_value: " 0.5 " }), result("i-17", { printed_value: "16", numeric_value: "0x10" }), result("i-18", { printed_value: "0.5", numeric_value: 0.5 }),
      result("i-19", { printed_value: 0.5, numeric_value: "0.5" }), result("i-20", { numeric_value: "0.5" }), result("i-21", { printed_value: "0.5" }), result("i-22", { printed_value: "59.2%", numeric_value: "59.2", unit: "percent" }),
      result("i-23", { printed_value: "59.2%", numeric_value: "59.2", unit: "fraction" }), result("i-24", { printed_value: "59.2%", numeric_value: "0.592", unit: "percent" }), result("i-25", { printed_value: "1e307", numeric_value: "5" }),
      result("i-26", { printed_value: "1e-320", numeric_value: "0" }), result("i-27", { printed_value: "0", numeric_value: "-0" }), result("i-28", { printed_value: "9\u00d710\u22124", numeric_value: "9e-4" }),
      result("i-29", { printed_value: "9\u00d710\u22124", numeric_value: "0.0009000000000000001" }), result("i-30", { printed_value: "1e2", numeric_value: "100.4" }), result("i-31", { printed_value: "100", numeric_value: "100.4" }),
      result("i-32", { printed_value: "-0.13", numeric_value: "-0.125" }), result("i-33", { printed_value: "-0.12", numeric_value: "-0.125" }), result("i-34", { printed_value: "3", numeric_value: "2.5" }),
      result("i-35", { printed_value: "0.5 \u00b1 0.1", numeric_value: "0.1" }), result("i-36", { printed_value: "0.5 \u00b1 0.1", numeric_value: "0.7" }), result("i-37", { printed_value: "0.5 (5e1)", numeric_value: "50" }),
      result("i-38", { printed_value: "0.5 (0.5)", numeric_value: "0.5" }), result("i-39", { printed_value: "0.50 (0.004)", numeric_value: "0.004" }), result("i-40", { printed_value: "\ud835\udfce.\ud835\udfd3", numeric_value: "0.5" }),
      result("i-41", { printed_value: "\ud835\udfce.\ud835\udfd3", numeric_value: "0.6" }), result("i-42", { printed_value: "0.5\ud83d", numeric_value: "0.5" }), result("i-43", { printed_value: "a\tb\nc", numeric_value: "1" }),
      result("i-44", { printed_value: "0.5", numeric_value: "1e999" }), result("i-45", { printed_value: "0.5", numeric_value: "-Infinity" }), result("i-46", { printed_value: "0.5", numeric_value: "NaN" }),
      result("i-47", { printed_value: "0.5", numeric_value: "1,000" }), result("i-48", { printed_value: "1,000", numeric_value: "1000" }), result("i-49", { printed_value: "1.000,5", numeric_value: "1000.5" }),
      result("i-50", { printed_value: "1e-7", numeric_value: "0.0000002" }), result("i-51", { printed_value: "123456789012345678901234567890", numeric_value: "1" }), result("i-52", { printed_value: "0.000001", numeric_value: "0.0000014" }),
      result("i-53", { printed_value: "0.0000001", numeric_value: "0.00000014" }), result("i-54", { printed_value: "0.5 \u00b1 -0.1", numeric_value: "0.5" }), result("i-55", { printed_value: "0.5 (x)", numeric_value: "0.5", unit: "percent" }),
      result("i-56", { printed_value: "0.5", numeric_value: "0.6", unit: "" }), result("i-57", { printed_value: "0.5", numeric_value: "0.6", unit: 5 }), result("i-58", { printed_value: "n/a", numeric_value: "0.6", unit: "percent" }),
      result("i-59", { printed_value: "1.7976931348623157e308", numeric_value: "1" }), result("i-60", { printed_value: "1", numeric_value: "1.7976931348623157e308" }), result("i-61", { printed_value: "0.00", numeric_value: "0.004" }),
      result("i-62", { printed_value: "0.00", numeric_value: "-0.004" }), result("i-63", { printed_value: "-0.00", numeric_value: "0.004" }), result("i-64", { printed_value: "12.50", numeric_value: "12.5" }),
    ],
  },
  {
    name: "printed against numeric: what the detail says of the record",
    ...vocabulary,
    records: [
      result("j-1", { printed_value: "0.5", numeric_value: "0.6", printed_value_basis: "table" }), result("j-2", { printed_value: "0.5", numeric_value: "0.6", workbook_number_format: "0.0" }),
      result("j-3", { printed_value: "0.5", numeric_value: "0.6", workbook_number_format: "0.0", printed_value_basis: "table" }), result("j-4", { printed_value: "0.5", numeric_value: "0.6", printed_value_basis: null }),
      result("j-5", { printed_value: "x", numeric_value: "0.6", printed_value_basis: false, workbook_number_format: null }), result("j-6", { printed_value: "-", numeric_value: "0.6", printed_value_basis: {} }),
      result("j-7", { printed_value: "0.5", numeric_value: "x", workbook_number_format: [] }), result("j-8", { printed_value: "0.5", numeric_value: "0.5", printed_value_basis: "table" }),
    ],
  },
  {
    name: "qualifiers",
    ...vocabulary,
    records: [
      result("k-01", { metric_qualifier: "high" }), result("k-02", { metric_qualifier: "High quality (DockQ >= 0.80)" }), result("k-03", { metric_qualifier: "high-ranking docking false positives" }),
      result("k-04", { metric_qualifier: "min-max normalised" }), result("k-05", { metric_qualifier: "max absolute track aggregation" }), result("k-06", { metric_qualifier: "yes" }), result("k-07", { metric_qualifier: "no" }),
      result("k-08", { metric_qualifier: "no, yes; (max) high" }), result("k-09", { metric_qualifier: "xhigh" }), result("k-10", { metric_qualifier: "low effort" }), result("k-11", { metric_qualifier: "medium effort" }),
      result("k-12", { metric_qualifier: "low  effort" }), result("k-13", { metric_qualifier: "LOW EFFORT" }), result("k-14", { metric_qualifier: "low\teffort" }), result("k-15", { metric_qualifier: "(no)" }), result("k-16", { metric_qualifier: "[no]" }),
      result("k-17", { metric_qualifier: "no." }), result("k-18", { metric_qualifier: "no:" }), result("k-19", { metric_qualifier: "ye\u017f" }), result("k-20", { metric_qualifier: "\u212ao" }), result("k-21", { metric_qualifier: "no\u00a0signal" }),
      result("k-22", { metric_qualifier: "say\ufeffno\u2003more" }), result("k-23", { metric_qualifier: "no\u0085" }), result("k-24", { metric_qualifier: "no\u001f" }), result("k-25", { metric_qualifier: "nono" }), result("k-26", { metric_qualifier: "no no" }),
      result("k-27", { metric_qualifier: "" }), result("k-28", { metric_qualifier: 5 }), result("k-29", {}), result("k-30", { metric_qualifier: "h\u0131gh" }), result("k-31", { metric_qualifier: "HIGH\nmax" }), result("k-32", { metric_qualifier: "\"no\"" }),
      result("k-33", { metric_qualifier: "max, n\u00f3, no\u0301" }), result("k-34", { metric_qualifier: "no\ud83d\ude00" }), result("k-35", { metric_qualifier: "\ud83d no" }), result("k-36", { metric_qualifier: "medium effort, low effort, xhigh, high, max, no, yes" }),
    ],
  },
  {
    name: "columns: one coherent, one split, and results that cannot be placed",
    ...vocabulary,
    records: [
      result("l-01", { source_locator: "Table 1, column 'AUROC'", metric: "auroc", unit: "unitless", metric_direction: "higher" }), result("l-02", { source_locator: "Table 1, row 2, column \"auroc\"", metric: "auroc", unit: "unitless", metric_direction: "higher" }),
      result("l-03", { source_locator: "Table 2, column 'F1'", metric: "f1-score", unit: "percent", metric_direction: "higher" }), result("l-04", { source_locator: "table 2, column (f1)", metric: "f1-score", unit: "fraction", metric_direction: "higher" }),
      result("l-05", { source_locator: "TABLE  2, COLUMN F1", metric: "f1-score", unit: "fraction", metric_direction: "higher" }), result("l-06", { source_locator: "Table 2, column 'F1'", metric: "accuracy" }),
      result("l-07", { source_locator: "Table 2, column 'F1'" }), result("l-08", { source_locator: "Table 2, column 'F1'", metric: "f1-score", unit: "percent", metric_direction: "higher" }, { source_ids: ["s2"] }),
      result("l-09", { source_locator: "Page 3" }), result("l-10", { source_locator: "Table 3, row 1" }), result("l-11", {}), result("l-12", { source_locator: "" }), result("l-13", { source_locator: 5 }),
      result("l-14", { source_locator: "Table 2, column group 'G', column 'F1'", metric: "rmse" }), result("l-15", { source_locator: "Table 2, column group 'F1'", metric: "rmse" }),
    ],
  },
  {
    name: "columns: the sources of a result, in every order and shape",
    ...vocabulary,
    records: [
      result("m-01", { source_locator: "Table 1, column 'a'", metric: "x" }, { source_ids: ["b", "a"] }), result("m-02", { source_locator: "Table 1, column 'a'", metric: "y" }, { source_ids: ["a", "b"] }),
      result("m-03", { source_locator: "Table 1, column 'a'", metric: "x" }, { source_ids: [] }), result("m-04", { source_locator: "Table 1, column 'a'", metric: "y" }, { source_ids: null }),
      (({ source_ids, ...rest }) => rest)(result("m-05", { source_locator: "Table 1, column 'a'", metric: "z" })),
      result("m-06", { source_locator: "Table 1, column 'a'", metric: "x" }, { source_ids: ["\uff5a", "\ud83d\ude00", "a"] }), result("m-07", { source_locator: "Table 1, column 'a'", metric: "y" }, { source_ids: ["a", "\ud83d\ude00", "\uff5a"] }),
      result("m-08", { source_locator: "Table 1, column 'a'", metric: "x" }, { source_ids: [10, 9, "1", null, true, ["z", "y"], { a: 1 }, 1.5, "null"] }),
      result("m-09", { source_locator: "Table 1, column 'a'", metric: "y" }, { source_ids: [{ a: 1 }, true, "null", 1.5, ["z", "y"], 9, null, "1", 10] }),
      result("m-10", { source_locator: "Table 1, column 'a'", metric: "x" }, { source_ids: ["a+b"] }), result("m-11", { source_locator: "Table 1, column 'a'", metric: "y" }, { source_ids: ["a", "b"] }),
      result("m-12", { source_locator: "Table 1, column 'a'", metric: "x" }, { source_ids: ["B", "a", "A", "b", "10", "9"] }), result("m-13", { source_locator: "Table 1, column 'a'", metric: "y" }, { source_ids: ["9", "10", "b", "A", "a", "B"] }),
    ],
  },
  {
    name: "columns: keys shown commonest first, and equally common ones in the order they were met",
    ...vocabulary,
    records: [
      ...many(2, (i) => result(`n-a-${i}`, { source_locator: "Fig. 2, column x", metric: "b" })), ...many(3, (i) => result(`n-b-${i}`, { source_locator: "Fig. 2, column x", metric: "a" })),
      ...many(2, (i) => result(`n-c-${i}`, { source_locator: "Fig. 2, column x", metric: "c", unit: "u", metric_direction: "d" })), result("n-d", { source_locator: "Fig. 2, column x", metric: "", unit: 5, metric_direction: null }),
      result("n-e", { source_locator: "Fig. 2, column X", metric: "b" }), result("n-f", { source_locator: "Fig 2, column x", metric: "b" }), result("n-g", { source_locator: "Fig. 2, column x", unit: "u" }),
    ],
  },
  { name: "columns: every locator of the list, each a result with a metric of its own", ...vocabulary, records: locators.map((locator, index) => result(`o-${String(index).padStart(3, "0")}`, { source_locator: locator, metric: `m${index % 3}` })) },
  {
    name: "duplicate artifacts",
    ...vocabulary,
    records: [
      source("p-b", { artifact_sha256: "aa", url: "https://a" }), source("p-a", { artifact_sha256: "aa", url: "https://a" }, { status: "superseded" }), source("p-c", { artifact_sha256: "aa", url: "https://b" }),
      source("p-d", { artifact_sha256: "bb", url: "https://a" }), source("p-e", { artifact_sha256: "bb", url: "https://a" }, { status: null }), (({ status, ...rest }) => rest)(source("p-f", { artifact_sha256: "bb", url: "https://a" })),
      source("p-g", { artifact_sha256: "bb", url: "https://a" }, { status: 5 }), source("p-h", { artifact_sha256: "", url: "https://a" }), source("p-i", { artifact_sha256: "cc", url: "" }), source("p-j", { url: "https://a" }),
      source("p-k", { artifact_sha256: "cc" }), source("p-l", { artifact_sha256: 5, url: "https://a" }), source("p-m", { artifact_sha256: "\uff5a", url: "u" }), source("p-n", { artifact_sha256: "\uff5a", url: "u" }),
      source("p-o", { artifact_sha256: "\ud83d\ude00", url: "u" }), source("p-p", { artifact_sha256: "\ud83d\ude00", url: "u" }), source("p-q", { artifact_sha256: "a b", url: "c" }), source("p-r", { artifact_sha256: "a", url: "b c" }),
      source("p-s", { artifact_sha256: "a b", url: "c" }, { status: true }), source("p-t", { artifact_sha256: "t\tab", url: "line\nbreak" }), source("p-u", { artifact_sha256: "t\tab", url: "line\nbreak" }, { status: "a\tb" }),
      source("P-B", { artifact_sha256: "dd", url: "u" }), source("p-10", { artifact_sha256: "dd", url: "u" }), source("p-9", { artifact_sha256: "dd", url: "u" }), source("\uff5a", { artifact_sha256: "dd", url: "u" }), source("\ud83d\ude00", { artifact_sha256: "dd", url: "u" }),
      result("p-result", { artifact_sha256: "aa", url: "https://a" }),
    ],
  },
  {
    name: "records with no attributes, and attributes that are not an object",
    ...vocabulary,
    // Without C13, which is given these records in the case after this one.
    only: ["C1", "C2", "C3", "C4", "C5", "C9"],
    records: [
      { id: "q-1", kind: "result" }, { id: "q-2", kind: "result", attributes: null }, { id: "q-3", kind: "result", attributes: "metric" }, { id: "q-4", kind: "result", attributes: ["metric"] }, { id: "q-5", kind: "result", attributes: 5 },
      { id: "q-6", kind: "source" }, { id: "q-7", kind: "source", attributes: null }, { id: "q-8", kind: "model", attributes: {} }, { id: "q-9", kind: "Result", attributes: { metric_qualifier: "no" } }, { id: "q-10", attributes: { metric_qualifier: "no" } },
      { id: "q-11", kind: 5, attributes: {} }, { id: "q-12", kind: null, attributes: {} }, { id: "q-13", kind: ["result"], attributes: {} }, { id: "q-14", kind: { a: 1 }, attributes: {} }, { id: "q-15", kind: { a: 1 }, attributes: {} }, { id: "q-16", kind: 5.0, attributes: {} },
    ],
  },
  {
    name: "records with no attributes, and attributes that are not an object, given to the whole pack",
    ...vocabulary,
    records: [
      { id: "q-1", kind: "result" }, { id: "q-2", kind: "result", attributes: null }, { id: "q-3", kind: "result", attributes: "metric" }, { id: "q-4", kind: "result", attributes: ["metric"] }, { id: "q-5", kind: "result", attributes: 5 },
      { id: "q-6", kind: "source" }, { id: "q-7", kind: "source", attributes: null }, { id: "q-8", kind: "model", attributes: {} }, { id: "q-9", kind: "Result", attributes: { metric_qualifier: "no" } }, { id: "q-10", attributes: { metric_qualifier: "no" } },
      { id: "q-11", kind: 5, attributes: {} }, { id: "q-12", kind: null, attributes: {} }, { id: "q-13", kind: ["result"], attributes: {} }, { id: "q-14", kind: { a: 1 }, attributes: {} }, { id: "q-15", kind: { a: 1 }, attributes: {} }, { id: "q-16", kind: 5.0, attributes: {} },
    ],
  },
  {
    name: "one of everything",
    ...vocabulary,
    records: [
      { id: "r-model", kind: "model", name: "m", status: "needs_review", source_ids: [], links: [], attributes: { review: { method: ["m"], reviewed_at: "2026-09-01", date: "2026-09-01" } } },
      result("r-1", { metric: "accuracy", unit: "percent", metric_direction: "lower", printed_value: "0.50", numeric_value: "0.504", metric_qualifier: "no", source_locator: "Table 1, column 'acc'" }),
      result("r-2", { metric: "accuracy", unit: "percent", metric_direction: "higher", printed_value: "50%", numeric_value: "50", metric_qualifier: "top-1", source_locator: "Table 1, column 'Acc'" }),
      result("r-3", { metric: "accuracy", unit: "fraction", metric_direction: "unknown", printed_value: "n/a", numeric_value: "0.5", metric_qualifier: "high", source_locator: "Table 1, column 'ACC'", license: "MIT" }),
      source("s1", { artifact_sha256: "ab", url: "https://x" }), source("s2", { artifact_sha256: "ab", url: "https://x" }),
      result("r-4", { coverage: "5 / 10", uncertainty: null }),
    ],
  },
];

// ---------------------------------------------------------------------------------------
// Checkouts for loadInput: the two vocabulary files and whatever store files there are.
// ---------------------------------------------------------------------------------------

const prefixes = "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n@prefix rb: <https://benchmarks.rewire.it/vocab#> .\n@prefix m: <https://benchmarks.rewire.it/vocab/metric/> .\n@prefix u: <https://benchmarks.rewire.it/vocab/unit/> .\n@prefix d: <https://benchmarks.rewire.it/vocab/direction/> .\n";
const metricTtl = `${prefixes}
m:auroc a skos:Concept ; skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; skos:prefLabel "AUROC" ; rb:defaultDirection d:higher .
m:rmse a skos:Concept ; skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection d:lower .
m:count a skos:Concept ; skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection d:unknown .
m:accuracy a skos:Concept ; skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> .
`;
const unitTtl = `${prefixes}
u:fraction a skos:Concept ; skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ; skos:scopeNote "Only for proportions." .
u:percent a skos:Concept ; skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> .
u:count a skos:Concept ; skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ; skos:scopeNote "Not for distances."@en .
`;
const oddMetricTtl = `${prefixes}
m:two-directions skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection d:lower , d:higher .
m:later rb:defaultDirection <https://example.org/a/b/sideways> .
m:later skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> .
m:text-direction skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection "unknown" .
m:slash-direction skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection <https://example.org/trailing/> .
m:no-slash skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection <urn:higher> .
m:twice skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> , <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection d:higher .
m:elsewhere skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ; rb:defaultDirection d:higher .
m:text-scheme skos:inScheme "https://benchmarks.rewire.it/vocab/metric/" ; rb:defaultDirection d:lower .
<https://example.org/outside> skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection d:higher .
<https://benchmarks.rewire.it/vocab/metric/> skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> .
<https://benchmarks.rewire.it/vocab/metric/a/b> skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection d:unknown ; rb:defaultDirection d:higher .
_:blank skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection d:higher .
[ skos:inScheme <https://benchmarks.rewire.it/vocab/metric/> ; rb:defaultDirection d:lower ] .
m:two-directions skos:scopeNote "a note on a metric" .
`;
const oddUnitTtl = `${prefixes}
u:two-notes skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ; skos:scopeNote "first" , "second" .
u:empty-note skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ; skos:scopeNote "" .
u:iri-note skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ; skos:scopeNote <https://example.org/note> .
u:directed skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ; rb:defaultDirection d:higher .
u:fraction skos:scopeNote "A note that comes before the concept is placed." .
u:fraction skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> .
m:stray skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ; skos:scopeNote "a metric in the unit scheme" .
_:blank skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ; skos:scopeNote "a blank unit" .
[ skos:inScheme <https://benchmarks.rewire.it/vocab/unit/> ] .
`;
const line = (record) => `${JSON.stringify(record)}\n`;
const model = { id: "a-model", kind: "model", name: "A", description: "", status: "needs_review", facets: {}, source_ids: [], links: [], attributes: {} };

/** A small checkout the commands run on: three results, two sources and a model. */
export const smallStore = {
  "data/vocab/metric.ttl": metricTtl,
  "data/vocab/unit.ttl": unitTtl,
  "data/entities/models.jsonl": line(model),
  "data/entities/sources.jsonl": line(source("s2", { artifact_sha256: "ab", url: "https://x" })) + line(source("s1", { artifact_sha256: "ab", url: "https://x" })),
  "data/evidence/results/0.jsonl":
    line(result("r-3", { metric: "accuracy", unit: "fraction", metric_direction: "higher", printed_value: "n/a", numeric_value: "0.5", metric_qualifier: "high", source_locator: "Table 1, column 'ACC'" }))
    + line(result("r-1", { metric: "auroc", unit: "percent", metric_direction: "lower", printed_value: "0.50", numeric_value: "0.504", metric_qualifier: "no", source_locator: "Table 1, column 'acc'" })),
  "data/evidence/results/f.jsonl": line(result("r-2", { metric: "auroc", unit: "percent", metric_direction: "higher", printed_value: "a\tb \ud83d", numeric_value: "50", metric_qualifier: "top-1", source_locator: "Table 1, column 'Acc'" })),
};

export const checkouts = [
  { name: "a small checkout", files: smallStore },
  { name: "vocabularies and no store", files: { "data/vocab/metric.ttl": metricTtl, "data/vocab/unit.ttl": unitTtl } },
  { name: "vocabularies with concepts stated oddly", files: { "data/vocab/metric.ttl": oddMetricTtl, "data/vocab/unit.ttl": oddUnitTtl, "data/entities/models.jsonl": line(model) } },
  { name: "empty vocabularies", files: { "data/vocab/metric.ttl": "", "data/vocab/unit.ttl": prefixes } },
  { name: "no metric vocabulary", files: { "data/vocab/unit.ttl": unitTtl } },
  { name: "no unit vocabulary", files: { "data/vocab/metric.ttl": metricTtl } },
  { name: "a metric vocabulary n3 refuses", files: { "data/vocab/metric.ttl": "m:auroc a .", "data/vocab/unit.ttl": unitTtl } },
  { name: "a store file that is not JSON", files: { ...smallStore, "data/entities/models.jsonl": "{not json}\n" } },
  { name: "results in files of any name, and a file that is not a store file", files: { ...smallStore, "data/evidence/results/zz.jsonl": line(result("r-0", { metric_qualifier: "yes" })), "data/evidence/results/notes.txt": "not read", "data/entities/unknowns.jsonl": line(model) } },
  { name: "a record filed under another kind", files: { ...smallStore, "data/entities/models.jsonl": line(model) + line(result("r-9", { metric_qualifier: "max" })) } },
];

// ---------------------------------------------------------------------------------------
// Baselines and runs for the ratchet. A run is its summaries, which are all the ratchet reads.
// ---------------------------------------------------------------------------------------

const pinned = (checks, total) => ({ note: "n", notes: [], commit: "c", run_date: "2026-01-01", store: {}, total_findings: total, checks });
const summary = (id, findings, rules, title = `title of ${id}`) => ({ id, title, findings, rules, context: {} });
const two = { C1: { title: "unit for metric", findings: 3, rules: { a: 1, b: 2 } }, C9: { title: "duplicate artifact", findings: 4, rules: { d: 4 } } };

export const ratchets = [
  { name: "equal", baseline: pinned(two, 7), summaries: [summary("C1", 3, { a: 1, b: 2 }), summary("C9", 4, { d: 4 })] },
  { name: "one rule up", baseline: pinned(two, 7), summaries: [summary("C1", 4, { a: 2, b: 2 }), summary("C9", 4, { d: 4 })] },
  { name: "one rule up and one down, the check and the total level", baseline: pinned(two, 7), summaries: [summary("C1", 3, { a: 2, b: 1 }), summary("C9", 4, { d: 4 })] },
  { name: "everything down", baseline: pinned(two, 7), summaries: [summary("C1", 0, { a: 0, b: 0 }), summary("C9", 1, { d: 1 })] },
  { name: "a check total up with no rule up", baseline: pinned(two, 7), summaries: [summary("C1", 5, { a: 1, b: 2 }), summary("C9", 4, { d: 4 })] },
  { name: "the total up through a check the baseline does not pin", baseline: pinned(two, 7), summaries: [summary("C1", 3, { a: 1, b: 2 }), summary("C9", 4, { d: 4 }), summary("C20", 2, { z: 2 })] },
  { name: "a rule the baseline does not pin", baseline: pinned(two, 7), summaries: [summary("C1", 3, { a: 1, b: 1, c: 1 }), summary("C9", 4, { d: 4 })] },
  { name: "a check the run does not make", baseline: pinned(two, 7), summaries: [summary("C1", 3, { a: 1, b: 2 })] },
  { name: "no checks run", baseline: pinned(two, 7), summaries: [] },
  { name: "a baseline that pins nothing", baseline: pinned({}, 0), summaries: [summary("C1", 3, { a: 1, b: 2 })] },
  { name: "a baseline whose total is not its checks added up", baseline: pinned(two, 2), summaries: [summary("C1", 3, { a: 1, b: 2 }), summary("C9", 4, { d: 4 })] },
  { name: "rises listed in the order of their keys", baseline: pinned({ C10: { title: "ten", findings: 0, rules: { "b": 0, "B": 0, "a.b": 0 } }, C9: { title: "nine", findings: 0, rules: { z: 0 } }, c1: { title: "", findings: 0, rules: { x: 0 } } }, 0), summaries: [summary("c1", 1, { x: 1 }), summary("C9", 1, { z: 1 }), summary("C10", 3, { b: 1, B: 1, "a.b": 1 })] },
  { name: "check IDs that are numbers, and one holding a dot", baseline: pinned({ 10: { title: "ten", findings: 0, rules: { 1: 0 } }, 9: { title: "nine", findings: 0, rules: { 2: 0 } }, "C1.a": { title: "dotted", findings: 0, rules: { b: 0 } }, C1: { title: "plain", findings: 0, rules: { a: 0, "a.b": 0 } } }, 0), summaries: [summary("9", 1, { 2: 1 }), summary("10", 1, { 1: 1 }), summary("C1", 2, { a: 1, "a.b": 1 }), summary("C1.a", 1, { b: 1 })] },
  { name: "a check named total", baseline: pinned({ total: { title: "a check", findings: 1, rules: { r: 1 } } }, 0), summaries: [summary("total", 2, { r: 2 })] },
  { name: "a check with no title in the baseline", baseline: pinned({ C1: { findings: 0, rules: { a: 0 } }, C2: { title: "", findings: 0, rules: { a: 0 } } }, 0), summaries: [summary("C1", 1, { a: 1 }), summary("C2", 1, { a: 1 })] },
  { name: "counts that are not whole", baseline: pinned({ C1: { title: "t", findings: 0.5, rules: { a: 1e-7, b: 1e21 } } }, 2.5), summaries: [summary("C1", 3, { a: 1, b: 2 })] },
  { name: "the seven checks against a first run", baseline: pinned({ C1: { title: "unit for metric", findings: 5391, rules: { "minority-unit": 5093, "unit-scope-note": 298 } }, C4: { title: "qualifier shape", findings: 338, rules: { "verdict-or-vendor-token": 338 } } }, 10754), summaries: [summary("C1", 5400, { "minority-unit": 5100, "unit-scope-note": 300 }, "unit for metric"), summary("C4", 337, { "verdict-or-vendor-token": 337 }, "qualifier shape")] },
];

// ---------------------------------------------------------------------------------------
// Runs of the command, each in a scratch folder holding the files, one step after another.
// A step is a run of the command, or an edit of the baseline file the step before it wrote.
// ---------------------------------------------------------------------------------------

export const BASELINE = "data/omics/checks-baseline.json";
const ELSEWHERE = "elsewhere/deep/pinned.json";
const written = { args: ["baseline", "--date", "2026-01-01", "--commit", "0123abc"] };
const report = { args: ["report", "--date", "2026-01-02"] };
const c4 = (by) => (held) => {
  held.checks.C4.findings += by;
  held.checks.C4.rules["verdict-or-vendor-token"] += by;
  held.total_findings += by;
  return held;
};

export const commands = [
  { name: "a report with no baseline", files: smallStore, steps: [report] },
  { name: "a report under today's date", files: smallStore, steps: [{ args: ["report"] }] },
  { name: "a baseline, and then a report that equals it", files: smallStore, steps: [written, report] },
  { name: "a baseline with no commit, under today's date", files: smallStore, steps: [{ args: ["baseline"] }] },
  { name: "a report above a baseline", files: smallStore, steps: [written, { edit: c4(-1) }, report] },
  { name: "a report below a baseline", files: smallStore, steps: [written, { edit: c4(7) }, report] },
  {
    name: "a report above a baseline on one count and below it on another",
    files: smallStore,
    steps: [written, { edit: (held) => { held.checks.C4.rules["verdict-or-vendor-token"] -= 1; held.checks.C2.rules.opposite += 5; return held; } }, report],
  },
  { name: "a report above a baseline that gives a check no title", files: smallStore, steps: [written, { edit: (held) => { c4(-1)(held); held.checks.C4.title = ""; return held; } }, report] },
  { name: "a baseline and a report where the environment names the file", files: smallStore, steps: [{ ...written, env: { CHECKS_BASELINE: ELSEWHERE } }, { ...report, env: { CHECKS_BASELINE: ELSEWHERE } }, report] },
  { name: "a report above a baseline the environment names", files: smallStore, steps: [{ ...written, env: { CHECKS_BASELINE: ELSEWHERE } }, { edit: c4(-2), file: ELSEWHERE }, { ...report, env: { CHECKS_BASELINE: ELSEWHERE } }] },
  { name: "a report on vocabularies and no store", files: { "data/vocab/metric.ttl": metricTtl, "data/vocab/unit.ttl": unitTtl }, steps: [written, report] },
  { name: "a report on a checkout with no vocabulary", files: { "data/entities/models.jsonl": line(model) }, steps: [report] },
  { name: "no command", files: smallStore, steps: [{ args: [] }] },
  { name: "another command", files: smallStore, steps: [{ args: ["check"] }] },
];
