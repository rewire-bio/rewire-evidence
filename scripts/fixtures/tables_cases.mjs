// Cases for the table reader of scripts/omics/extract/tables.ts: what the committed artifacts
// of the data repository have no example of. record_tables_fixture.mjs runs the TypeScript on
// each and writes what it returns or throws. Nothing here needs the data repository's code.

const many = (count, text) => Array.from({ length: count }, () => text).join("");

// ---------------------------------------------------------------------------------------
// decode(text): numeric references first, in hexadecimal and then in decimal, then names.
// ---------------------------------------------------------------------------------------

export const decodes = [
  "", "plain", "&amp;", "&lt;&gt;&quot;&apos;", "a&nbsp;b|&ndash;&mdash;&minus;&plusmn;&times;", "&rho;&sigma;&alpha;&beta;&Delta;&uarr;&darr;",
  // A name the table does not hold is left as it was written, and names are held in one case only.
  "&delta;", "&AMP;", "&Amp;", "&RHO;", "&unknown;", "&;", "&amp", "amp;", "&amp1;", "&am p;", "&amp;&", "a&b;c", "&&amp;;",
  // One pass for each kind of reference, so a reference a pass writes is read by the pass after it.
  "&amp;amp;", "&amp;lt;", "&#38;lt;", "&#x26;lt;", "&#x26;#38;amp;", "&#38;#x26;", "&#x26;#x26;", "&#38;#38;",
  "&#65;&#x41;&#X41;&#x0041;&#00065;", "&#x1F600;", "&#128512;", "&#xD83D;&#xDE00;", "&#xd800;", "&#56832;", "&#0;", "&#x0;", "&#9;&#10;&#x20;", "&#x10FFFF;", "&#1114111;",
  // Beyond the last character String.fromCodePoint throws.
  "&#x110000;", "&#1114112;", "&#99999999999999999999;", "&#xffffffffffffffffffff;", "&#x20000000000001;", "&#9007199254740993;", `&#x${many(400, "f")};`, `&#${many(400, "9")};`, `&#${many(5000, "1")};`,
  "ok &#65; then &#x110000; then &#66;",
  // Not references.
  "&#x;", "&#;", "&#x1g;", "&# 65;", "&#-65;", "&#65", "&#x41", "&#+65;", "&#6_5;", "&#1e3;", "&#x 41;", "&#xx41;", "&#\uff10;", "&#x\uff11;", "&#\u0663;",
  // The table is an object, so a name is also whatever every object has under that name.
  "&constructor;", "&toString;", "&valueOf;", "&hasOwnProperty;", "&isPrototypeOf;", "&propertyIsEnumerable;", "&toLocaleString;", "&__proto__;", "&TOSTRING;", "&tostring;", "&Constructor;",
  // A letter under the i flag with no u flag: the long s and the Kelvin sign are not s and k.
  "&\u017f;", "&\u212a;", "&ma\u017f;", "&\u00e9;", "&\uff41;",
  "\u{1F600}&amp;\u{1F600}", "\ud83d&amp;\ude00", "&\ud83d;", "&#x41;\ud83d",
];

// ---------------------------------------------------------------------------------------
// text(fragment): no tags, no TeX copies, no scripts or styles, spaces made single.
// ---------------------------------------------------------------------------------------

export const texts = [
  "", " ", "plain", "  a \n\t b  ", "<b>bold</b>  text", "a<br/>b", "a<br>b<br />c", "<p>one</p><p>two</p>",
  "\u00a0a\u00a0b\u00a0", "\ufeffa\ufeff", "\u0085a\u0085", "a\u2028b\u2029c", "a\u200bb", "\u3000a\u1680",
  // LaTeXML writes a formula twice. The TeX copy is dropped.
  "<math><semantics><mn>0.31</mn><mo>\u00b1</mo><annotation encoding=\"application/x-tex\">0.31\\pm 1</annotation></semantics></math>",
  "<td>0.31&plusmn;1&times;10<sup>&minus;2</sup><annotation>0.31\\pm 1\\times 10^{-2}</annotation></td>",
  "<ANNOTATION>x</annotation>y", "<annotation>x</ANNOTATION>y", "<annotation>unclosed", "a</annotation>b", "<annotation>a</annotation>b<annotation>c</annotation>d",
  "<annotation-xml encoding=\"MathML-Content\">kept</annotation-xml>z", "<annotation-xml>a</annotation-xml>b<annotation>c</annotation>d", "<annotations>a</annotations>b",
  "<annotation\n encoding='x'>a\nb</annotation>c", "<annotation>a</annotation >b", "<annotation>a<annotation>b</annotation>c</annotation>d", "<annotation/>a</annotation>b",
  // A script or a style, closed by the tag that opened it, in any case.
  "<script>var a = '<td>';</script>z", "<style>td{}</style>z", "<script>a</style>b</script>c", "<style>a</script>b</style>c", "<SCRIPT>a</script>b", "<Style>a</STYLE>b", "<script>a</style>b",
  "<scripts>a</scripts>b", "<script type='x'>a</script >b", "<script>a</script>b<style>c</style>d", "<script-x>a</script-x>b", "<script-x>a</script>b", "<script\n>a</script>b", "<script>unclosed",
  "<\u017fcript>a</\u017fcript>b", "<\u017ftyle>a</style>b", "<sc\u0131ipt>a</script>b",
  // What is a tag: anything from a < to the next > with something between them.
  "<>", "< >", "<a", "a>", "a > b < c", "a < b > c", "1 < 2 and 3 > 2", "<<a>>", "<a title='>'>b</a>", "a<!-- c > d -->e", "<![CDATA[x > y]]>z", "<?xml version=\"1.0\"?>a", "<a\nhref='x'\n>b</a\n>",
  // References are read after the tags are gone, so one that spells a tag stays text.
  "&lt;b&gt;", "&#60;b&#62;bold&#60;/b&#62;", "<p>&amp;lt;</p>", "&lt;script&gt;a&lt;/script&gt;", "a&nbsp;&nbsp;b", "&#32;&#32;a&#9;", "&nbsp;", "<b>&nbsp;</b>", "a&#x110000;b",
  "\u{1F600} <b>\u{1F600}</b>", "\ud83d<b>\ude00</b>", "<\ud83d>a",
];

// ---------------------------------------------------------------------------------------
// tables(source): every table with its caption and its rows.
// ---------------------------------------------------------------------------------------

const row = (...cells) => `<tr>${cells.map((cell) => `<td>${cell}</td>`).join("")}</tr>`;
const head = (...cells) => `<tr>${cells.map((cell) => `<th>${cell}</th>`).join("")}</tr>`;
const table = (...rows) => `<table>${rows.join("")}</table>`;
const plain = table(head("Model", "AUROC"), row("a", "0.9"), row("b", "0.8"));

export const sources = [
  { name: "nothing", source: "" },
  { name: "text and no table", source: "<p>No table here, only a table-like word.</p>" },
  { name: "one table with no caption", source: plain },
  { name: "a caption inside the table", source: `<table><caption>Table 1: <b>Results</b></caption>${head("a", "b")}${row("1", "2")}</table>` },
  { name: "a LaTeXML figure", source: `<figure class="ltx_table"><figcaption><span>Table 2:</span> Core model results</figcaption>${plain}</figure>` },
  { name: "a JATS table-wrap", source: `<table-wrap id="t3"><label>Table 3</label><caption><title>Linear probe</title><p>All tasks.</p></caption>${plain}</table-wrap>` },
  { name: "a JATS table-wrap with a label and no caption", source: `<table-wrap><label>Table 4</label>${plain}</table-wrap>` },
  { name: "a JATS table-wrap with a caption and no label", source: `<table-wrap><caption><p>Only a caption</p></caption>${plain}</table-wrap>` },
  { name: "a label and a caption that are empty", source: `<table-wrap><label> </label><caption><p></p></caption>${plain}</table-wrap>` },
  { name: "a figcaption wins over a label and a caption", source: `<label>L</label><caption>C</caption><figcaption>F</figcaption>${plain}` },
  { name: "a figcaption with nothing in it still wins", source: `<label>L</label><caption>C</caption><figcaption> </figcaption>${plain}` },
  { name: "the last figcaption before the table", source: `<figcaption>first</figcaption><p>x</p><figcaption>second</figcaption>${plain}` },
  { name: "the last label and the last caption, each on its own", source: `<label>L1</label><caption>C1</caption><label>L2</label><caption>C2</caption><label>L3</label>${plain}` },
  { name: "a caption inside wins over one before", source: `<figcaption>before</figcaption><table><caption>inside</caption>${row("1")}</table>` },
  { name: "a caption after the table is not read", source: `${plain}<figcaption>after</figcaption>` },
  { name: "a second table does not borrow the caption of the first", source: `<figcaption>Table 1</figcaption>${plain}<p>between</p>${plain}` },
  { name: "a caption after one table belongs to the next", source: `${plain}<figcaption>Table 2</figcaption>${plain}` },
  { name: "three tables, each with its caption", source: `<figcaption>A</figcaption>${plain}<label>B</label>${plain}<caption>C</caption>${plain}` },
  { name: "a label inside a cell is taken for the caption", source: `<figcaption>before</figcaption><table><tr><td><label>in a cell</label></td></tr></table>` },
  { name: "a caption in any case, with attributes", source: `<FIGCAPTION class="x">Upper</FIGCAPTION><TABLE CLASS="y"><TR><TH>a</TH><TD>1</TD></TR></TABLE>` },
  { name: "a figcaption that is not closed", source: `<figcaption>open ${plain}` },
  { name: "tags that only start like a caption", source: `<labels>x</labels><captions>y</captions><figcaptions>z</figcaptions>${plain}` },
  { name: "a caption with a formula and a reference", source: `<figcaption>Table&nbsp;5: R<sup>2</sup> &amp; <math><mo>\u03c1</mo><annotation>\\rho</annotation></math></figcaption>${plain}` },
  // Which element is a table.
  { name: "a table-wrap and no table", source: "<table-wrap><label>Table 1</label></table-wrap>" },
  { name: "a tag that only starts like a table", source: "<tablex><tr><td>1</td></tr></tablex><tables></tables>" },
  { name: "a table tag in capitals, closed with a space", source: `<TABLE><tr><td>1</td></tr></Table >` },
  { name: "a table tag over two lines", source: `<table\n class="a"\n><tr><td>1</td></tr></table\n>` },
  { name: "a table that closes itself and is never closed", source: "<table/><tr><td>1</td></tr>" },
  { name: "a table that closes itself and is closed", source: "<table/><tr><td>1</td></tr></table>" },
  { name: "a table that is not closed", source: "<p>before</p><table><tr><td>1</td></tr>" },
  { name: "a closing tag before any table", source: `</table>${plain}` },
  { name: "a closing tag after the table", source: `${plain}</table><figcaption>x</figcaption>${plain}` },
  { name: "a table in a table", source: `<figcaption>outer</figcaption><table><tr><td>a</td><td><table><caption>inner</caption><tr><td>in 1</td><td>in 2</td></tr></table></td><td>b</td></tr><tr><td>c</td></tr></table>` },
  { name: "a table in a table in a table", source: `<table><tr><td><table><tr><td><table><tr><td>deep</td></tr></table></td></tr></table></td></tr></table>${plain}` },
  { name: "an inner table that is not closed", source: `<table><tr><td><table><tr><td>x</td></tr></td></tr></table>` },
  { name: "a table tag inside an attribute", source: `<table title="</table"><tr><td>1</td></tr></table>` },
  { name: "a table tag with a > inside an attribute", source: `<table title="a>b"><tr><td>1</td></tr></table>` },
  { name: "a table in a comment", source: `<!-- <table><tr><td>hidden</td></tr></table> -->${plain}` },
  { name: "a table with nothing in it", source: "<table></table>" },
  { name: "a table with text and no rows", source: "<table>text</table>" },
  // Rows and cells.
  { name: "rows with attributes, and tags that only start like a row", source: `<table><tr class="x"><td>1</td></tr><trx><td>2</td></trx><tr-x><td>3</td></tr-x><TR><td>4</td></tr></table>` },
  { name: "a row that is not closed", source: `<table><tr><td>1</td><tr><td>2</td></tr></table>` },
  { name: "a row with no cells", source: `<table><tr></tr><tr>text</tr>${row("1")}</table>` },
  { name: "cells outside a row", source: `<table><td>1</td>${row("2")}</table>` },
  { name: "a header cell and a data cell, each closed by the other", source: `<table><tr><td>a</th><td>b</td><th>c</td><th>d</th></tr></table>` },
  { name: "cells in capitals and mixed", source: `<table><tr><TD>a</td><Th>b</tH><td>c</TD></tr></table>` },
  { name: "a cell that is not closed", source: `<table><tr><td>a<td>b</td></tr><tr><td>c</tr></table>` },
  { name: "tags that only start like a cell", source: `<table><tr><thead>h</thead><tdx>x</tdx><td-x>y</td-x><td>z</td></tr></table>` },
  { name: "a cell over two lines with attributes", source: `<table><tr><td\n class="a"\n>\n 0.5 \n</td></tr></table>` },
  { name: "cells with tags, references, a formula and a script", source: `<table><tr><td><b>0.31</b>&plusmn;<i>0.01</i></td><td>1&times;10<sup>&minus;2</sup><annotation>1\\times 10^{-2}</annotation></td><td><script>x</script>n/a</td><td>&#x1D7CE;.&#x1D7D7;</td></tr></table>` },
  { name: "a cell holding a reference past the last character", source: `<table><tr><td>&#x110000;</td></tr></table>` },
  { name: "a caption holding a reference past the last character", source: `<figcaption>&#1114112;</figcaption>${plain}` },
  // Columns spanned.
  { name: "a header that spans three columns", source: `<table><tr><th></th><th colspan="3">Group</th><th>Last</th></tr>${row("m", "1", "2", "3", "4")}</table>` },
  {
    name: "spans written every way",
    source: `<table><tr><td colspan=2>a</td><td COLSPAN = "2">b</td><td colspan='2'>c</td><td colspan="0">d</td><td colspan="x">e</td><td colspan="2.5">f</td><td colspan="-2">g</td><td colspan="">h</td><td data-colspan="2">i</td><td colspan="1">j</td><td colspan="02">k</td><td colspan=" 2">l</td><td colspan\n=\n"2">m</td><td colspan="2" colspan="3">n</td><td rowspan="2">o</td></tr></table>`,
  },
  { name: "empty cells that span", source: `<table><tr><td colspan="2"></td><th colspan="3"></th><td>x</td></tr></table>` },
  { name: "a span of a hundred", source: `<table><tr><td colspan="100">a</td><td>b</td></tr></table>` },
  { name: "a span in full-width digits", source: `<table><tr><td colspan="\uff12">a</td></tr></table>` },
  { name: "characters beyond U+FFFF and half of one", source: `<figcaption>\u{1F600} cap \ud83d</figcaption><table><tr><td>\u{1F600}</td><td>\ude00 x</td></tr></table>` },
  { name: "line ends and indentation, as a file has them", source: `<table-wrap>\r\n  <label>Table 1.</label>\r\n  <caption>\r\n    <p>Performance of\r\n    baseline models</p>\r\n  </caption>\r\n  <table>\r\n    <thead>\r\n      <tr>\r\n        <th>Model</th>\r\n        <th>MCC</th>\r\n      </tr>\r\n    </thead>\r\n    <tbody>\r\n      <tr>\r\n        <td>A</td>\r\n        <td>0.5</td>\r\n      </tr>\r\n    </tbody>\r\n  </table>\r\n</table-wrap>\r\n` },
];

// ---------------------------------------------------------------------------------------
// tableMatching(source, pattern): the one table whose caption the pattern matches.
// ---------------------------------------------------------------------------------------

const captioned = (caption, cell) => `<figure><figcaption>${caption}</figcaption>${table(row(cell))}</figure>`;
const paper = [captioned("Table 3: Core model results", "t3"), captioned("Table 4: Zero-shot proteingym", "t4"), captioned("Table 8 :  Per-model average MCC", "t8"), captioned("Table 3: Core model results, continued", "t3b"), table(row("bare"))].join("\n");

/** Each is a source and a pattern as [source, flags]. The patterns are ones the extractors pass, and ones that find none or two. */
export const matchings = [
  { name: "one table", source: paper, pattern: ["Table 4: Zero-shot proteingym", ""] },
  { name: "one table, through spaces of any width", source: paper, pattern: ["Table 8\\s*:\\s*Per-model average MCC", ""] },
  { name: "two tables", source: paper, pattern: ["Table 3: Core model results", ""] },
  { name: "one table, by its end", source: paper, pattern: ["Table 3: Core model results$", ""] },
  { name: "no table", source: paper, pattern: ["Table 9", ""] },
  { name: "no table, for the case of a letter", source: paper, pattern: ["table 4", ""] },
  { name: "one table, in any case", source: paper, pattern: ["table 4", "i"] },
  { name: "the table with no caption", source: paper, pattern: ["^$", ""] },
  { name: "every table", source: paper, pattern: ["^", ""] },
  { name: "a source with no table", source: "<p>none</p>", pattern: ["Table", ""] },
  { name: "a source whose table is not closed", source: "<table>", pattern: ["Table", ""] },
  { name: "a pattern with a slash in it", source: captioned("a/b", "x"), pattern: ["a\\/b", ""] },
];

// ---------------------------------------------------------------------------------------
// sectioned(rows, width): a first column that labels several rows.
// ---------------------------------------------------------------------------------------

export const sectionings = [
  { name: "no rows", rows: [], width: 2 },
  { name: "a label carried down", rows: [["A", "m1", "1"], ["m2", "2"], ["B", "m3", "3"], ["m4", "4"]], width: 2 },
  { name: "rows before any label", rows: [["m0", "0"], ["A", "m1", "1"]], width: 2 },
  { name: "a blank label continues the section", rows: [["A", "m1", "1"], ["", "m2", "2"], ["  ", "m3", "3"], ["\u00a0\ufeff", "m4", "4"], ["\u0085", "m5", "5"]], width: 2 },
  { name: "a label kept as it is printed", rows: [["  A  ", "m1", "1"], ["m2", "2"]], width: 2 },
  { name: "a row too short", rows: [["A", "m1", "1"], ["m2"]], width: 2 },
  { name: "a row too long", rows: [["A", "m1", "1", "x", "y"]], width: 2 },
  { name: "an empty row", rows: [["A", "m1", "1"], []], width: 2 },
  { name: "no width", rows: [[], ["A"], [], [""]], width: 0 },
  { name: "one column", rows: [["A", "1"], ["2"], ["B", "3"]], width: 1 },
  { name: "a row with a separator in a cell", rows: [["a | b", "c"]], width: 3 },
];

// ---------------------------------------------------------------------------------------
// headedBlocks(table): header rows and the data rows under them.
// ---------------------------------------------------------------------------------------

export const blocks = [
  { name: "no rows", rows: [] },
  { name: "data and no header", rows: [["m", "1", "2"], ["n", "3", "4"]] },
  { name: "one header row", rows: [["", "AUROC", "F1"], ["m", "1", "2"], ["n", "3", "4"]] },
  { name: "a group row over a metric row", rows: [["", "Length 100", "", "Length 200", ""], ["", "scTM \u2191", "RMSD \u2193", "scTM \u2191", "RMSD \u2193"], ["m", "1", "2", "3", "4"]] },
  { name: "two stacked blocks", rows: [["", "L100", "", "L200", ""], ["", "a", "b", "a", "b"], ["m", "1", "2", "3", "4"], ["", "L300", "", "L500", ""], ["", "a", "b", "a", "b"], ["m", "5", "6", "7", "8"], ["n", "9", "10", "11", "12"]] },
  { name: "three header rows, the groups joined top to bottom", rows: [["", "G", "", "", ""], ["", "A", "", "B", ""], ["", "x", "y", "x", "y"], ["m", "1", "2", "3", "4"]] },
  { name: "the same group named twice above a column", rows: [["", "G", "H"], ["", "G", "I"], ["", "x", "y"], ["m", "1", "2"]] },
  { name: "a header with no rows under it", rows: [["", "a", "b"]] },
  { name: "a header with no rows, and then a block", rows: [["", "a", "b"], ["", "c", "d"], ["m", "1", "2"], ["", "e", "f"]] },
  { name: "rows before the first header are left out", rows: [["early", "0"], ["", "a"], ["m", "1"]] },
  { name: "a row of empty cells is neither", rows: [["", "a"], ["m", "1"], ["", ""], ["", " "], [], [""], ["n", "2"]] },
  { name: "header rows of different lengths", rows: [["", "G"], ["", "a", "b", "c"], ["m", "1", "2", "3"]] },
  { name: "a metric row shorter than its group row", rows: [["", "G", "", "H"], ["", "a"], ["m", "1"]] },
  { name: "cells with space around them", rows: [[" ", " G ", "\u00a0"], ["\ufeff", " a ", " b "], [" m ", " 1 ", " 2 "]] },
  { name: "a header cell that is only space", rows: [["", " ", "a"], ["m", "1", "2"]] },
  { name: "a first cell of a character that is not space to JavaScript", rows: [["\u0085", "a"], ["m", "1"]] },
  { name: "a block, a header pair, and a block", rows: [["", "a"], ["m", "1"], ["", "G"], ["", "b"], ["n", "2"]] },
];

// ---------------------------------------------------------------------------------------
// arrowDirection(heading)
// ---------------------------------------------------------------------------------------

export const headings = ["AUROC \u2191", "RMSD\u2193", "\u2191", "\u2193", "both \u2191 and \u2193", "\u2193 then \u2191", "", "plain", "&uarr;", "\u2b06", "\u2197", "\u2191\ufe0f", "up", "^"];

// ---------------------------------------------------------------------------------------
// layoutRows(source, from, to): rows of a table printed in a PDF text layer.
// ---------------------------------------------------------------------------------------

const layer = [
  "                 4   E XPERIMENTS",
  "",
  "    Table 3: Results on all tasks   ",
  "    Model            Enhancer    Promoter   Splice site",
  "    E NFORMER          0.71       0.82        0.90",
  "",
  "    DNABERT-2  \t   0.68       0.80        0.88   ",
  "    Splice-H510 x    0.5        0.6         0.7",
  "       ",
  "    Nucleotide Transformer v2   0.70   0.81   0.89",
  "5     R ESULTS",
  "    after   the   table",
  "5   R ESULTS",
].join("\n");
const START = ["^\\s*Table 3: Results on all tasks", ""];
const END = ["^5\\s+R ESULTS", ""];

export const layouts = [
  { name: "a table between its caption and the next heading", source: layer, from: START, to: END },
  { name: "the start is not found", source: layer, from: ["^Table 9", ""], to: END },
  { name: "the end is not found", source: layer, from: START, to: ["^6\\s+D ISCUSSION", ""] },
  { name: "the end is looked for after the start only", source: layer, from: ["^5\\s+R ESULTS", ""], to: END },
  { name: "the end is the line after the start", source: "Table\nEnd\nmore", from: ["^Table", ""], to: ["^End", ""] },
  { name: "the start is the last line", source: "a\nTable", from: ["^Table", ""], to: ["^End", ""] },
  { name: "an empty layer", source: "", from: ["^Table", ""], to: ["^End", ""] },
  { name: "a start that matches an empty line", source: "\n\na  b\nEnd", from: ["^$", ""], to: ["^End", ""] },
  { name: "the patterns are held to the line with its indent gone", source: "   Table\n  a  b\n\t End \n", from: ["^Table$", ""], to: ["^End$", ""] },
  { name: "line ends of two characters", source: "Table\r\na  b   c\r\n d  e\r\nEnd\r\n", from: ["^Table", ""], to: ["^End", ""] },
  { name: "page breaks, tabs and other spaces", source: "Table\n\fa\t\tb\tc\n a\u00a0\u00a0b \u00a0c\u3000\u3000d\na \u2003 b\na\u0085\u0085b\nEnd", from: ["^Table", ""], to: ["^End", ""] },
  { name: "one space is part of a cell", source: "Table\nNucleotide Transformer v2  0.70\nx\nEnd", from: ["^Table", ""], to: ["^End", ""] },
  { name: "characters beyond U+FFFF", source: "Table\n\u{1F600}  \u{1F600} a  \ud83d\nEnd", from: ["^Table", ""], to: ["^End", ""] },
  { name: "a pattern in any case", source: "TABLE\na  b\nend", from: ["^table", "i"], to: ["^END", "i"] },
];

// ---------------------------------------------------------------------------------------
// joinSmallCaps(printed)
// ---------------------------------------------------------------------------------------

export const smallCaps = [
  "", "E NFORMER", "D EEP SEA", "D EEP SEA B EAR", "A B", "A BC", "A BC DE FG", "A BC D EF", "Splice-H510", "a E NFORMER", "XE NFORMER", "E  NFORMER", "E NFORMER model", "E NFORMERs", "E NFORMER2", "E NFORMER_X",
  "I AM HERE", "1 ABC", "A1 BC", "-E NFORMER", "(E NFORMER)", "E NFORMER, D EEP SEA and B ORZOI", "E\tNFORMER", "E\u00a0NFORMER", "\u00c9 COLE", "E \u00c9COLE", "\u00c9 E NFORMER", "\u00e9E NFORMER", "_E NFORMER", "E NFORMER E",
  "5     R ESULTS", "T ABLE 3: R ESULTS ON ALL TASKS", "\u{1F600}E NFORMER", "E NFORMER\ud83d", "\uff25 \uff2e\uff26\uff2f\uff32\uff2d\uff25\uff32", "e nformer", "E Nformer", "E NFormer",
];
