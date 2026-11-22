// Cases for layer derivation: what the stored objects of the data repository have no example
// of. record_layers_fixture.mjs runs the TypeScript on each and writes what it returns or
// throws. Nothing here needs the data repository's code: the archives, the packages and the
// compressed objects are made with Node's built-ins.
import crypto from "node:crypto";
import zlib from "node:zlib";

const bytes = (text) => Buffer.from(text, "utf8");
const raw = (text) => Buffer.from(text, "latin1");
const hex = (data) => (Buffer.isBuffer(data) ? data : bytes(data)).toString("hex");
const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");
const many = (count, text) => Array.from({ length: count }, () => text).join("");
const u16 = (value) => {
  const out = Buffer.alloc(2);
  out.writeUInt16LE(value);
  return out;
};
const u32 = (value) => {
  const out = Buffer.alloc(4);
  out.writeUInt32LE(value >>> 0);
  return out;
};

// ---------------------------------------------------------------------------------------
// Archives, written by hand so that each field can be wrong on purpose.
// ---------------------------------------------------------------------------------------

/** A zip. An entry is { name, data, method, patch }, where patch overrides recorded fields. */
export function zip(entries, { comment = "", count = null, directoryAt = null, drop = 0 } = {}) {
  const locals = [];
  const central = [];
  let at = 0;
  for (const entry of entries) {
    const name = Buffer.isBuffer(entry.name) ? entry.name : bytes(entry.name);
    const data = Buffer.isBuffer(entry.data ?? "") ? entry.data : bytes(entry.data ?? "");
    const method = entry.method ?? 8;
    const stored = entry.stored ?? (method === 8 ? zlib.deflateRawSync(data) : data);
    const held = { compressed: stored.length, uncompressed: data.length, localAt: at, method, ...(entry.patch ?? {}) };
    const extra = entry.extra ?? Buffer.alloc(0);
    const local = Buffer.concat([
      raw(entry.localSignature ?? "PK\x03\x04"), u16(20), u16(0), u16(method), u16(0), u16(0), u32(0),
      u32(stored.length), u32(data.length), u16(name.length), u16(extra.length), name, extra, stored,
    ]);
    locals.push(local);
    central.push(Buffer.concat([
      raw(entry.signature ?? "PK\x01\x02"), u16(20), u16(20), u16(0), u16(held.method), u16(0), u16(0), u32(0),
      u32(held.compressed), u32(held.uncompressed), u16(name.length), u16(0), u16(0), u16(0), u16(0), u32(0),
      u32(held.localAt), name,
    ]));
    at += local.length;
  }
  const directory = Buffer.concat(central);
  const body = Buffer.concat(locals);
  const end = Buffer.concat([
    raw("PK\x05\x06"), u16(0), u16(0), u16(count ?? entries.length), u16(count ?? entries.length),
    u32(directory.length), u32(directoryAt ?? body.length), u16(comment.length), raw(comment),
  ]);
  const whole = Buffer.concat([body, directory, end]);
  return drop ? whole.subarray(0, whole.length - drop) : whole;
}

/** A tar. An entry is { name, data, type, size, prefix, sizeField }. */
export function tar(entries, { tail = 1024 } = {}) {
  const blocks = [];
  for (const entry of entries) {
    const data = Buffer.isBuffer(entry.data ?? "") ? entry.data : bytes(entry.data ?? "");
    const header = Buffer.alloc(512);
    (Buffer.isBuffer(entry.name) ? entry.name : bytes(entry.name)).copy(header, 0);
    raw(entry.sizeField ?? (entry.size ?? data.length).toString(8).padStart(11, "0") + "\0").copy(header, 124);
    if (entry.type !== null) header.write(entry.type ?? "0", 156, "latin1");
    header.write(entry.magic ?? "ustar", 257, "latin1");
    if (entry.prefix) bytes(entry.prefix).copy(header, 345);
    const padded = Buffer.alloc(Math.ceil(data.length / 512) * 512);
    data.copy(padded);
    blocks.push(header, entry.cut ? data.subarray(0, entry.cut) : padded);
  }
  blocks.push(Buffer.alloc(tail));
  return Buffer.concat(blocks);
}

/** A gzip member with a header written by hand, so it can carry a name or an extra field. */
function gzipWith({ name = null, extra = null, flags = null, cut = 0 }, text) {
  const data = bytes(text);
  const flag = flags ?? (name === null ? 0 : 0x08) | (extra === null ? 0 : 0x04);
  const parts = [Buffer.from([0x1f, 0x8b, 8, flag, 0, 0, 0, 0, 0, 3])];
  if (extra !== null) parts.push(u16(extra.length), extra);
  if (name !== null) parts.push(Buffer.isBuffer(name) ? name : raw(name), Buffer.from([0]));
  parts.push(zlib.deflateRawSync(data), u32(zlib.crc32(data)), u32(data.length));
  const whole = Buffer.concat(parts);
  return cut ? whole.subarray(0, cut) : whole;
}
const gz = (data) => zlib.gzipSync(Buffer.isBuffer(data) ? data : bytes(data), { level: 9 });

// ---------------------------------------------------------------------------------------
// Office packages
// ---------------------------------------------------------------------------------------

const TYPES = {
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
  xlsm: "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
};
const contentTypes = (type, partName = "/x") =>
  `<?xml version="1.0"?><Types xmlns="t"><Default Extension="xml" ContentType="application/xml"/><Override PartName="${partName}" ContentType="${type}"/></Types>`;
const pack = (members, options) => zip(Object.entries(members).map(([name, data]) => ({ name, data })), options);
const workbookXml = (sheets) =>
  `<workbook xmlns:r="r"><sheets>${sheets.map(([name, id, rid]) => `<sheet${name === null ? "" : ` name="${name}"`} sheetId="${id}"${rid ? ` r:id="${rid}"` : ""}/>`).join("")}</sheets></workbook>`;
const relsXml = (targets) =>
  `<Relationships>${targets.map(([id, target]) => `<Relationship Id="${id}" Target="${target}"/>`).join("")}</Relationships>`;
const sheetXml = (rows) => `<worksheet><sheetData>${rows}</sheetData></worksheet>`;
const stringsXml = (items) => `<sst>${items.join("")}</sst>`;
const documentXml = (body) => `<w:document xmlns:w="w"><w:body>${body}</w:body></w:document>`;
const p = (text) => `<w:p><w:r><w:t>${text}</w:t></w:r></w:p>`;
const tc = (body) => `<w:tc>${body}</w:tc>`;
const tr = (cells) => `<w:tr>${cells.join("")}</w:tr>`;

const SHEET_ROWS =
  '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>0.10</v></c><c r="D1" t="inlineStr"><is><t>in</t><r><t>line</t></r></is></c></row>' +
  '<row r="3"><c r="B3" t="b"><v>1</v></c><c r="A3" t="str"><f>A1</f><v>tab\there</v></c></row>';
const SHARED = ["<si><t>name</t></si>", "<si><r><t>ru</t></r><r><t xml:space=\"preserve\">n s </t></r><rPh><t>phonetic</t></rPh></si>", "<si/>", "<si><t>a&amp;b&#10;c\\d</t></si>"];

const plainWorkbook = pack({
  "[Content_Types].xml": contentTypes(TYPES.xlsx, "/xl/workbook.xml"),
  "xl/workbook.xml": workbookXml([["First", 1, "rId1"], ["Se\tcond", 2, "rId2"], [null, 3, "rId9"], ["Absolute", 4, "rId4"]]),
  "xl/_rels/workbook.xml.rels": relsXml([["rId1", "worksheets/sheet1.xml"], ["rId2", "worksheets/other.xml"], ["rId4", "/xl/worksheets/abs.xml"]]),
  "xl/sharedStrings.xml": stringsXml(SHARED),
  "xl/worksheets/sheet1.xml": sheetXml(SHEET_ROWS),
  "xl/worksheets/other.xml": sheetXml('<row><c><v>1</v></c><c><v>2</v></c></row><row><c t="s"><v>1</v></c><c t="s"><v>3</v></c><c t="s"><v>9</v></c><c t="s"><v>x</v></c></row>'),
  "xl/worksheets/sheet3.xml": sheetXml(""),
  "xl/worksheets/abs.xml": sheetXml('<row r="2"><c r="C2"><v>😀 z</v></c></row>'),
});
const megabyte = many(1000, many(1000, "a"));
const hugeSheet = many(65, '<row><c t="s"><v>0</v></c></row>');

export const packages = {
  plainWorkbook,
  macroWorkbook: pack({ "[Content_Types].xml": contentTypes(TYPES.xlsm), "xl/workbook.xml": workbookXml([["M", 1, null]]), "xl/worksheets/sheet1.xml": sheetXml('<row r="1"><c r="A1"><v>7</v></c></row>') }),
  workbookByNameOnly: pack({ "docProps/app.xml": "<a/>", "xl/workbook.bin": "binary" }),
  workbookNoSheets: pack({ "xl/workbook.xml": "<workbook/>" }),
  workbookMissingPart: pack({ "xl/workbook.xml": workbookXml([["Gone", 1, null]]) }),
  workbookPrefixOnly: pack({ "xl/styles.xml": "<styleSheet/>" }),
  workbookTooManyRows: pack({ "xl/workbook.xml": workbookXml([["Long", 1, null]]), "xl/worksheets/sheet1.xml": sheetXml('<row r="250001"><c r="A250001"><v>1</v></c></row>') }),
  workbookTooManyColumns: pack({ "xl/workbook.xml": workbookXml([["Wide", 1, null]]), "xl/worksheets/sheet1.xml": sheetXml('<row r="1"><c r="FAO1"><v>1</v></c></row>') }),
  workbookTooManyCharacters: pack({ "xl/workbook.xml": workbookXml([["Big", 1, null]]), "xl/sharedStrings.xml": stringsXml([`<si><t>${megabyte}</t></si>`]), "xl/worksheets/sheet1.xml": sheetXml(hugeSheet) }),
  workbookUtf16: pack({ "xl/workbook.xml": Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(workbookXml([["W", 1, null]]), "utf16le"), Buffer.from([0x41])]), "xl/worksheets/sheet1.xml": Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), bytes(sheetXml('<row r="1"><c r="A1"><v>﻿bom</v></c></row>'))]) }),
  workbookBadMember: zip([{ name: "xl/workbook.xml", data: workbookXml([["S", 1, null]]) }, { name: "xl/worksheets/sheet1.xml", data: "x", stored: raw("\xff\xff\xff"), patch: {} }]),
  plainDocument: pack({
    "[Content_Types].xml": contentTypes(TYPES.docx, "/word/document.xml"),
    "word/document.xml": documentXml(
      p("Title") + `<w:p><w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t>c</w:t><w:noBreakHyphen/><w:softHyphen/><w:sym w:char="F0B7"/><w:sym w:char="zz"/><w:sym/><w:cr/></w:r></w:p>` +
      `<w:tbl>${tr([tc(p("h1") + p("h 2")), tc(p("x\ty")), tc(""), tc(p(""))])}${tr([tc(`<w:p><w:r><w:t>1</w:t><w:br/><w:tab/><w:t>2 </w:t></w:r></w:p>`), tc(`<w:tbl>${tr([tc(p("in")), tc(p("ner"))])}</w:tbl>`)])}</w:tbl>` +
      `<w:p><w:r><w:instrText>FIELD</w:instrText><w:delText>gone</w:delText><w:t>kept</w:t></w:r><mc:AlternateContent><mc:Choice><w:r><w:t>choice</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>fallback</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p>` +
      "<w:p><w:r><w:t>last, unclosed",
    ),
  }),
  documentNoBody: pack({ "word/styles.xml": "<styles/>" }),
  documentNoText: pack({ "word/document.xml": documentXml("") }),
  presentation: pack({ "[Content_Types].xml": contentTypes(TYPES.pptx), "ppt/presentation.xml": "<p/>" }),
  presentationByDeclaration: pack({ "[Content_Types].xml": contentTypes(TYPES.pptx.toUpperCase()), "xl/workbook.xml": workbookXml([["S", 1, null]]) }),
  openDocument: pack({ mimetype: "application/vnd.oasis.opendocument.text", "META-INF/manifest.xml": "<m/>", "content.xml": "<c/>" }),
  unknownPackage: pack({ "[Content_Types].xml": contentTypes("application/unknown"), "other/part.xml": "<x/>" }),
  typesOnly: pack({ "[Content_Types].xml": "<Types><Override ContentType=\"\"/><Default/></Types>", "word/x.xml": "<x/>" }),
  mimetypeAlone: pack({ mimetype: "x", "word/document.xml": documentXml(p("still a document")) }),
};

// ---------------------------------------------------------------------------------------
// classify, decode
// ---------------------------------------------------------------------------------------

const latin = (text) => raw(text);
const HINTS = [null, "text/html", "TEXT/HTML ; charset=utf-8", " text/csv ", "application/json", "text/", ""];
const classifyInputs = [
  ["", null], ["%PDF-1.7\n", null], ["%PDF", null], [Buffer.from([0x1f, 0x8b]), null], [raw("PK\x03\x04junk"), null],
  [raw("PK\x05\x06" + many(18, "\0")), null], [raw("PK\x07\x08"), null], [raw("PK\x03"), null],
  [packages.plainWorkbook, null], [packages.plainDocument, null], [packages.presentation, null], [packages.openDocument, null],
  [packages.unknownPackage, null], [packages.workbookPrefixOnly, null], [packages.mimetypeAlone, null], [pack({ "a.txt": "a" }), null],
  [tar([{ name: "a.txt", data: "a" }]), null], [Buffer.concat([Buffer.alloc(257, 0x61), raw("ustar"), Buffer.alloc(30, 0x61)]), null],
  [raw("\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest"), null], ["BZh91AY", null], [raw("\xfd7zXZ\0"), null], [raw("\x28\xb5\x2f\xfd"), null],
  [raw("\x89PNG\r\n"), null], [raw("\xff\xd8\xff\xe0"), null], ["GIF89a", null], ["GIF", null],
  ["hello\n", null], ["a\0b", null], [raw("caf\xe9 " + many(16, "x")), null], [raw("\xe9" + many(19, "x")), null], [raw("\xe9" + many(18, "x")), null],
  [raw("\xe9\0" + many(40, "x")), null], [raw("\xef\xbb\xbfhello"), null], [raw("\xef\xbb\xbf"), null], [raw("\xef\xbb\xbf\xff\0<html>"), null],
  [raw("\xef\xbb\xbf\xef\xbb\xbf<html>"), null], [Buffer.concat([raw("\xff\xfe"), Buffer.from("<html>😀", "utf16le")]), null],
  [raw("\xff\xfea\0b"), null], [raw("\xff\xfe\x3d\xd8a\0\0\xde"), null], [raw("\xff\xfe"), null], [raw("\xfe\xff\0<\0a\0>"), null], [raw("\xfe\xff"), null],
  [raw("\xfe\xff\0a\0"), null], [raw("\xfe\xff\xd8\x3d\0a"), null],
  ["<html>", null], ["<HTML lang=en>", null], ["<Html>", null], ["  \n\t<html>", null], ["  <html>", null], ["\u0085<html>", null],
  ["<!DOCTYPE html><p>x", null], ["<!doctype HTML PUBLIC \"-//W3C//DTD HTML 4.01//EN\"><x>", null], ["<!DOCTYPE\nhtml><x>", null], ["<!doctypehtml><x>", null],
  ["<!DOCTYPE article [ <!ENTITY x '>'> ]><article>", null], ["<!DOCTYPE a ]><b>", null], ["<!doctype html", null], ["<!DOCTYPE svg><svg>", null],
  ["<?xml version='1.0'?><article>", null], ["<?xml?><a>", null], ["<?XML ?>\n<a:b.c-d_e x='1'>", null], ["<?xmlx ?><a>", null], ["<?php echo 1 ?><div>", null],
  ["<?xml version='1.0'", null], ["<?xml ?><?xml-stylesheet ?><!-- c --><!DOCTYPE x><x>", null], ["<!-- c --><div>", null], ["<!--><div>", null], ["<!-- open", null],
  [many(16, "<!-- c -->") + "<html>", null], [many(17, "<!-- c -->") + "<html>", null], ["<div align=center>", null], ["<DIV>", null], ["<article>", null],
  ["<svg>", null], ["<p>", null], ["<unknown", null], ["<1a>", null], ["< a>", null], ["<_a>", null], ["<aé>", null], ["<K>", null], ["x<html>", null],
  [many(4090, " ") + "<html>", null], [many(4091, " ") + "<html>", null], [many(4095, " ") + "<html>", null], [many(5000, " ") + "<html>", null],
  ["<!--" + many(2040, "😀") + "--><html>", null], ["<!--" + many(2045, "😀") + "--><html>", null],
  ["<!-- " + many(2043, "😀") + "--><html>", null], ["<!-- " + many(2044, "😀") + "--><html>", null],
];
export const classifyCases = [
  ...classifyInputs.map(([data, hint]) => [hex(data), hint]),
  ...["hello", "<p>", "<div>", "<article>", "<html>", "<?xml ?><a>", "", "a,b\n1,2\n", "<!DOCTYPE x><div>"].flatMap((text) => HINTS.map((hint) => [hex(text), hint])),
];
export const decodeCases = [
  ["utf-8", "café"], ["utf-8-bom", raw("\xef\xbb\xbfcaf\xc3\xa9\xff")], ["utf-8-bom", raw("\xef\xbb\xbf\xed\xa0\x80\xc3")],
  ["utf-16le", raw("\xff\xfea\0\x3d\xd8\0\xdeb")], ["utf-16le", raw("\xff\xfe\0\xde\x3d\xd8")], ["utf-16be", raw("\xfe\xff\0a\xd8\x3d\xde\0")],
  ["utf-16be", raw("\xfe\xff\0a\0")], ["utf-16be", raw("\xfe\xff")], ["latin-1", raw("caf\xe9\x80\xff")],
].map(([encoding, data]) => [hex(data), encoding]);

// ---------------------------------------------------------------------------------------
// Containers
// ---------------------------------------------------------------------------------------

const SMALL = { maxMembers: 2, maxMemberBytes: 8, maxTotalBytes: 12 };
export const expandCases = [
  [gz("hello"), null], [gzipWith({ name: "name.txt" }, "named"), null], [gzipWith({ name: "caf\xe9.txt", extra: raw("xy") }, "both"), null],
  [gzipWith({ extra: raw("abcd") }, "extra only"), null], [gzipWith({ flags: 0x0c, extra: null, name: null }, "x").subarray(0, 11), null],
  [gzipWith({ flags: 0x08 }, "no terminator").subarray(0, 10), null], [Buffer.concat([gz("one"), gz("two")]), null],
  [Buffer.concat([gz("one"), Buffer.alloc(4)]), null], [Buffer.concat([gz("one"), raw("junk")]), null, "zlib"], [gz("hello").subarray(0, 12), null, "zlib"],
  [raw("\x1f\x8bnot gzip at all"), null, "zlib"], [gz("123456789"), SMALL, "zlib"], [gz("12345678"), SMALL], [raw("\x1f\x8b"), null, "zlib"],
  [tar([{ name: "a.txt", data: "alpha" }, { name: "dir/b.txt", data: many(600, "b") }]), null],
  [tar([{ name: "b.txt", data: "b", prefix: "pre/fix" }, { name: "d/", type: "5" }, { name: "zero", data: "z", type: null }, { name: "link", type: "2", data: "" }]), null],
  [tar([{ name: "././@LongLink", type: "L", data: "a/very/long/name.txt\0" }, { name: "short", data: "body" }, { name: "after", data: "x" }]), null],
  [tar([{ name: "././@LongLink", type: "L", data: "" }, { name: "kept-empty-long-name", data: "x" }]), null],
  [tar([{ name: "bad", data: "x", sizeField: "zzzzzzzzzzz\0" }, { name: "never", data: "y" }]), null], [tar([{ name: "neg", data: "x", sizeField: "-0000000001\0" }]), null],
  [tar([{ name: "minus-zero", data: "", sizeField: "-0\0" }, { name: "spaced", data: "abc", sizeField: "         3 \0" }, { name: "blank", data: "", sizeField: many(12, " ") }]), null],
  [tar([{ name: "base256", data: "x", sizeField: "\x80\0\0\0\0\0\0\0\0\0\0\x01" }]), null], [tar([{ name: "past", data: "x", size: 5000 }]), null],
  [tar([{ name: "", data: "x", size: 5000 }]), null], [tar([{ name: raw("nul\0tail"), data: "x" }, { name: raw("nul\0line\nmore"), data: "y" }, { name: raw("caf\xe9"), data: "z" }]), null],
  [tar([{ name: "a", data: "1" }, { name: "b", data: "2" }, { name: "c", data: "3" }]), SMALL], [tar([{ name: "big", data: "123456789" }, { name: "ok", data: "1" }]), SMALL],
  [tar([{ name: "a", data: "1234567" }, { name: "b", data: "123456" }, { name: "c", data: "1" }]), SMALL], [tar([{ name: "a", data: "x" }], { tail: 0 }), null],
  [Buffer.concat([tar([{ name: "a", data: "x" }], { tail: 512 }), tar([{ name: "after-zeros", data: "y" }])]), null], [tar([{ name: "cut", data: many(600, "c"), cut: 100 }], { tail: 0 }), null],
  [zip([{ name: "a.txt", data: "alpha" }, { name: "dir/", data: "" }, { name: "dir/b.txt", data: "beta", method: 0 }]), null],
  [zip([{ name: "a.txt", data: "alpha" }], { comment: "a trailing comment" }), null], [zip([{ name: "a.txt", data: "a" }, { name: "a.txt", data: "b" }, { name: raw("caf\xe9"), data: "c" }]), null],
  [zip([{ name: "z64", data: "x", patch: { compressed: 0xffffffff } }, { name: "z64b", data: "x", patch: { uncompressed: 0xffffffff } }, { name: "z64c", data: "x", patch: { localAt: 0xffffffff } }, { name: "ok", data: "fine" }]), null],
  [zip([{ name: "a", data: "x" }], { directoryAt: 0xffffffff }), null], [zip([{ name: "a", data: "x" }, { name: "b", data: "y", signature: "PK\x01\x03" }, { name: "c", data: "z" }]), null],
  [zip([{ name: "a", data: "x" }], { count: 3 }), null], [zip([{ name: "odd", data: "x", patch: { method: 99 } }, { name: "ok", data: "y" }]), null],
  [zip([{ name: "nolocal", data: "x", localSignature: "PK\x03\x05" }, { name: "far", data: "y", patch: { localAt: 100000 } }, { name: "ok", data: "z" }]), null],
  [zip([{ name: "short", data: "x", patch: { compressed: 5000 } }, { name: "empty-deflate", data: "", patch: { compressed: 0 } }]), null, "zlib"],
  [zip([{ name: "bad", data: "x", stored: raw("\xff\xff\xff\xff") }, { name: "ok", data: "y" }]), null, "zlib"],
  [zip([{ name: "a", data: "1" }, { name: "b", data: "2" }, { name: "c", data: "3" }]), SMALL], [zip([{ name: "big", data: "123456789" }, { name: "ok", data: "1" }]), SMALL],
  [zip([{ name: "a", data: "1234567", method: 0 }, { name: "b", data: "123456", method: 0 }, { name: "c", data: "1" }]), SMALL],
  [zip([{ name: "lies", data: "123456789", patch: { uncompressed: 1 } }]), SMALL, "zlib"], [zip([]), null], [raw("PK\x03\x04 no directory at all"), null],
  [raw("PK\x05\x06" + many(17, "\0")), null], [zip([{ name: "a", data: "x" }], { drop: 1 }), null], [raw("neither"), null], [raw("PK"), null], [Buffer.alloc(0), null],
].map(([data, limits, words]) => [hex(data), limits, words ?? null]);
export const zipNameCases = [
  [zip([{ name: "a" }, { name: "b/c" }, { name: raw("caf\xe9") }]), 64], [zip([{ name: "a" }, { name: "b" }, { name: "c" }]), 2], [zip([{ name: "a" }], { count: 9 }), 64],
  [zip([{ name: "a" }], { directoryAt: 0xffffffff }), 64], [raw("PK\x03\x04"), 64], [zip([{ name: "a" }, { name: "b", signature: "XXXX" }]), 64], [zip([{ name: "a" }], { comment: "c" }), 0],
].map(([data, limit]) => [hex(data), limit]);

// ---------------------------------------------------------------------------------------
// The markup scanner
// ---------------------------------------------------------------------------------------

export const entityCases = [
  "no references", "&amp;&lt;&gt;&quot;&apos;", "&nbsp;|&ensp;|&emsp;|&thinsp;|&shy;|", "&mdash;&ndash;&minus;&hellip;&alpha;&Omega;&szlig;&ae;", "&#65;&#x41;&#X41;&#x1F600;&#128512;",
  "&#0;&#x0;&#xD800;&#xDFFF;&#x110000;&#1114111;&#99999999999999999999;&#x;&#;", "&unknown;&AMP;&amp&amp ;&a;&ab;&constructor;&toString;&__proto__;", "&#x" + many(400, "F") + ";&#" + many(400, "9") + ";",
  "&amp;amp;&#38;lt;", "a & b &; &#abc; &#x12g;", "&" + many(32, "a") + ";&" + many(33, "a") + ";", "&#000065;&#x000041;&#0x41;&#-1;&#+1;&# 1;",
];
const TABLE = '<table><thead><tr><th>Model</th><th colspan="2">Score</th></tr></thead><tbody><tr><td>a</td><td>0.5</td><td>0.6</td></tr><tr><td>b<td>0.7<td>0.8</tbody></table>';
export const scanCases = [
  ["html", "plain text"], ["html", "<p>one</p><p>two</p>"], ["html", "<p>a<br>b<br/>c</p>"], ["html", "<div>a</div><span>b</span><span>c</span><td>d</td><td>e</td>"],
  ["html", "<script>var a = '<p>';</script>kept<style>x</style><SCRIPT>y</SCRIPT>"], ["html", "<nav><nav>inner</nav>still skipped</nav>after"], ["html", "<nav/>shown<nav>hidden"],
  ["html", "<svg><svg/><g>x</g></svg>tail"], ["html", "a<!-- comment -->b<!-- open"], ["html", "a<![CDATA[<raw> &amp; ]]>b<![CDATA[open"], ["html", "a<!DOCTYPE x [ <!ENTITY y '>'> ]>b<!open"],
  ["html", "a<?pi <x> ?>b<?open"], ["html", "a < b and <unterminated"], ["html", "<a href='x>y' title=\"q>r\">link</a>"], ["html", "<a href='unclosed>text"], ["html", "<>empty</><1>digit</1>< p>space</ p>"],
  ["html", "x<p/>y<br/>z<hr>w"], ["html", "&lt;p&gt; &amp;amp; &#x41;<p>&nbsp;x&nbsp;</p>"], ["html", "<nav>&lt;</nav><p>&unknown;</p>"], ["html", "<P CLASS='a'>upper</P><Td>cell</Td>"],
  ["html", TABLE], ["html", "  lead   and\ttrail  \n\n\n\nnext  line \n"], ["html", "\n\n\n"], ["html", ""], ["html", "<p></p><p> </p>"], ["html", "tail<p>"],
  ["html", "<pre>  keep?\n  no  </pre><table><tr><td>a</td><td></td><td>c</td></tr></table>"], ["html", "<form><table><caption>Cap</caption><tr><th>h</th></tr></table></form>"],
  ["xml", "<article><front><article-meta><title-group><article-title>T</article-title></title-group></article-meta></front><body><sec><title>S</title><p>a <italic>b</italic> c<sup>2</sup></p></sec></body></article>"],
  ["xml", "<p>x<inline-graphic>data</inline-graphic>y<processing-meta><a/></processing-meta>z</p>"], ["xml", "<p>e=<mml:math><mml:mi>m</mml:mi></mml:math><m:r>c</m:r><tex-math>\\alpha</tex-math></p>"],
  ["xml", "<table-wrap><label>Table 1</label><caption><title>Cap</title><p>more</p></caption>" + TABLE + "</table-wrap>"], ["xml", "<row><entry>a</entry><entry>b</entry></row><unknown>u</unknown><x>i</x>"],
  ["html", "a<![CDATA[]]>b&shy;<p>&shy;</p>c"], ["html", "<script><![CDATA[x]]>y</script>z<style>a <b"],
  ["xml", "<?xml version=\"1.0\"?><!DOCTYPE article><article xmlns:xlink=\"x\"><ext-link xlink:href=\"u\">l</ext-link></article>"], ["xml", "a\r\nb\rc d﻿e\u0085f\u000bg\u000ch"],
];
export const tidyCases = ["", "\n", "a", " a ", "a\n", "\na", "a\n\n\n\nb", "a \t b\n\n c  d \n\n\n", "\n\n a\n\n", "a\r\nb", "a b\u0085c\u001cd", "  \n \n  ", "a\n \nb\n\t\n\nc", "﻿a﻿ b", "one  two   three\n  four"];
export const tidyTextCases = ["😀  a 😀\n\n\n \ud83d b\ude00", "\ud83d \ude00"];

// ---------------------------------------------------------------------------------------
// The office scanner and its parts
// ---------------------------------------------------------------------------------------

export const xmlScanCases = [
  "<a x=\"1\" y='two' Z:Q=\"&amp;&#10;\">t&lt;\r\nu\rv</a>", "<a/><b />< c/></ c><x:Row R=\"1\"></x:Row>", "text only &amp; more", "tail <open", "a<!-- c -->b<!-- open", "a<![CDATA[ <x>\r\n&amp; ]]>b<![CDATA[open",
  "a<?pi ?>b<?open", "a<!DOCTYPE x [ <!E '>'> ]>b<!open", "<a b=c d=\"e\" f = 'g' h=\"\" i=''>", "<>x</><1>", "<a x=\"1\" x=\"2\" __proto__=\"p\" constructor=\"c\"/>", "<a x='>' y=\">\">q</a>", "",
  "<a:b:c d:e:f=\"1\"/>", "&unknown;&nbsp;&#xD800;&#65;", "<t xml:space=\"preserve\"> kept </t>",
];
export const nameCases = [
  [], ["mimetype"], ["mimetype", "META-INF/manifest.xml"], ["META-INF/x", "mimetype", "xl/workbook.xml"], ["xl/workbook.bin", "xl/workbook.xml"], ["a", "xl/workbook.xml", "word/document.xml"],
  ["word/document.xml", "ppt/presentation.xml"], ["ppt/presentation.xml"], ["ppt/slides/slide1.xml", "word/x", "xl/y"], ["word/x", "ppt/y"], ["ppt/y", "[Content_Types].xml"], ["[Content_Types].xml"],
  ["XL/workbook.xml"], ["a.txt", "b/c.txt"], ["xl/"], ["word/document.xml/"],
];
export const columnCases = {
  numbers: ["A", "Z", "AA", "AZ", "BA", "ZZ", "AAA", "XFD", "FAO", "ZZZZZZZ", "AAAAAAAA", "", "a", "A1", "Ä", " A"],
  labels: [1, 26, 27, 52, 53, 702, 703, 4096, 16384, 0, -1],
  references: ["A1", "$B$2", "c3", " D4 ", "XFD1048576", "A0", "A00", "A", "1", "A1B", "$A", "AAAAAAAA1", "A12345678", "ß1", "ı1", "ﬁ1", "A１", "A1:B2", "", "A 1", "$$A1", "A$$1", "ſı1"],
  escapes: ["plain", "a\tb\nc\rd\fe\\f", "\u0000\u0001\u000b\u001f\u007f\u0080 ", "\\t stays doubled", "", "😀 "],
};
export const documentCases = [
  "", documentXml(""), documentXml(p("one") + p("") + p("three")), documentXml(tr([tc(p("a")), tc(p("b"))])), documentXml(`<w:tbl>${tr([tc(p("a")), tc(""), tc("")])}${tr([])}${tr([tc(""), tc(p("z"))])}</w:tbl>`),
  "</w:tc></w:tr></w:p>stray", "<w:tc><w:p><w:r><w:t>cell with no row</w:t></w:r></w:p></w:tc><w:p/>", "<w:tr><w:tc><w:p><w:r><w:t>a </w:t></w:r></w:p><w:p><w:r><w:t> b</w:t></w:r></w:p></w:tc>",
  "<w:t>a</w:t></w:t></w:t><w:t>b", "<w:instrText><w:t>skipped</w:t><w:fallback>deep</w:fallback></w:instrText></w:delText></w:delText><w:t>kept</w:t>", "<w:p><w:t>x</w:t><w:br/><w:t>y</w:t><w:cr/></w:p><w:p><w:tab/></w:p>",
  "<w:p><w:sym w:char=\"41\"/><w:sym w:char=\"0x42\"/><w:sym w:char=\"D800\"/><w:sym w:char=\"110000\"/><w:sym w:char=\"0\"/><w:sym w:char=\"-41\"/><w:sym w:char=\"\"/><w:sym w:char=\"1F600\"/></w:p>",
  "<w:tc><w:tr><w:tc><w:t>inner\\\t</w:t></w:tc></w:tr><w:t>outer</w:t></w:tc>", "<w:p><w:t>é &amp; &#x1F600;\r\n</w:t></w:p>", "<w:t>no paragraph</w:t>",
];
const STRINGS = ["zero", "one", ""];
export const sheetCases = [
  ["", STRINGS], [SHEET_ROWS, STRINGS], ['<row r="2"><c r="B2"><v>x</v></c></row><row><c><v>y</v></c></row><row r="0"><c><v>z</v></c></row><row r="1.5"><c><v>w</v></c></row><row r="abc"><c><v>v</v></c></row>', STRINGS],
  ['<row r=" 7 "><c r="b7"><v>low</v></c><c r="$C$7"><v>abs</v></c><c r="7C"><v>bad</v></c><c r=""><v>empty</v></c><c r="D9"><v>jumps</v></c><c><v>next</v></c></row>', STRINGS],
  ['<c t="s"><v>1</v></c><c t="s"><v>2</v></c><c t="s"><v>3</v></c><c t="s"><v>-1</v></c><c t="s"><v>-0</v></c><c t="s"><v>1.9</v></c><c t="s"><v> 1x</v></c><c t="s"><v></v></c><c t="s"><v>x</v></c><c t="S"><v>1</v></c>', STRINGS],
  ['<row r="1"><c r="A1"><v>1</v><v>2</v></c><c r="B1"><v>open</c><c r="C1"><is><t>a</t><x>ignored</x><t>b</t></is></c><c r="D1"><is><t>unclosed</c><c r="E1"><v/></c><c r="F1"/></row>', STRINGS],
  ['<row r="1"><c r="A1"><v>first</v></c><c r="A1"><v>second</v></c></row><c r="B3"><v>cell sets row</v></c><c><v>after</v></c>', STRINGS], ['<v>no cell</v><is><t>no cell either</t></is>', STRINGS],
  ['<row r="1"><c r="A1"><v>a&amp;b</v></c><c r="B1"><v><![CDATA[c<d]]></v></c><c r="C1"><v>e\r\nf</v></c><c r="D1"><f>SUM(A1)</f><v>3</v></c><t>stray</t></row>', STRINGS],
  ['<row r="999999999999999999999999"><c><v>1</v></c></row>', STRINGS], ['<x:row R="2"><x:c R="b2" T="s"><x:v>0</x:v></x:c></x:row>', STRINGS],
];
export const partCases = [
  {}, { "xl/workbook.xml": workbookXml([]) }, { "xl/workbook.xml": workbookXml([["A", 1, "r1"], ["B", 2, "r2"], ["", 3, ""]]), "xl/_rels/workbook.xml.rels": relsXml([["r1", "/abs.xml"], ["r2", "rel.xml"], ["", "x"], ["r3", ""]]) },
  { "xl/workbook.xml": "<sheet/><Sheet Name=\"Mixed\" r:Id=\"z\"/><definedName name=\"n\"/>", "xl/sharedStrings.xml": stringsXml(SHARED) },
  { "xl/sharedStrings.xml": "<t>outside</t><si><t>a</t><rPh><t>p</t></rPh><t>b</t></rPh></rPh><t>c</t><si><t>nested open</t></si><t>after</t></t></t></si><si><r><rPr/><t>r1</t></r><t/></si>" },
  { "xl/sharedStrings.xml": Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from("<si><t>wide 😀</t></si>", "utf16le")]) }, { "xl/sharedStrings.xml": Buffer.from([0xff]) },
].map((members) => Object.fromEntries(Object.entries(members).map(([name, data]) => [name, hex(data)])));

// ---------------------------------------------------------------------------------------
// Grids
// ---------------------------------------------------------------------------------------

const cell = (tag, text, attributes = "") => `<${tag}${attributes}>${text}</${tag}>`;
const row = (cells) => `<tr>${cells.join("")}</tr>`;
const grid = (rows) => `<table>${rows.map(row).join("")}</table>`;
const plain = (texts) => texts.map((text) => cell("td", text));
const HEADED = grid([plain(["Model", "Acc", "F1"]), plain(["a", "0.5", "0.6"]), plain(["b", "0.7", "0.8"]), plain(["c", "0.9", "n/a"])]);
export const gridCases = [
  ["html-table-grid@1", "<p>no table here</p>"], ["html-table-grid@1", TABLE], ["jats-table-grid@1", "<table-wrap><label>Table 1</label><caption><title>The cap</title><p>and more</p></caption>" + TABLE + "</table-wrap>"],
  ["html-table-grid@1", HEADED], ["html-table-grid@1", "<table><caption>C <b>bold</b></caption><caption>second</caption>" + row(plain(["a", "b"])) + "</table>"],
  ["html-table-grid@1", grid([[cell("th", "H1"), cell("th", "H2")], [cell("th", "H3"), cell("th", "H4")], plain(["1", "2"]), [cell("th", "stub"), cell("td", "3")]])],
  ["html-table-grid@1", grid([[cell("td", "wide", ' colspan="2"'), cell("td", "tall", ' rowspan="2"')], plain(["x", "y"]), plain(["p", "q", "r", "s"])])],
  ["html-table-grid@1", grid([[cell("td", "a", " colspan=2 rowspan='3'"), cell("td", "b", ' COLSPAN = "2"'), cell("td", "c", ' xcolspan="9" data-rowspan="9"')], plain(["d"]), plain(["e"]), plain(["f"])])],
  ["html-table-grid@1", grid([[cell("td", "a", ' colspan="0"'), cell("td", "b", ' colspan="-2"'), cell("td", "c", ' rowspan="1.9"'), cell("td", "d", ' rowspan="x"'), cell("td", "e", ' colspan="1001"'), cell("td", "f", ' colspan=" 3 "'), cell("td", "g", ' colspan=""'), cell("td", "h", ' colspan="' + many(400, "9") + '"')]])],
  ["html-table-grid@1", "<table><td>no row</td><td>same row</td><tr><td>r2</td></tr><th>after close</th></table>"], ["html-table-grid@1", "<td>outside any table</td><tr><td>x</td></tr><thead></thead>"],
  ["html-table-grid@1", grid([[cell("td", "outer " + grid([plain(["in1", "in2"])]) + " tail")], plain(["next"])])], ["html-table-grid@1", "<table><tr><td>a<td>b<tr><td>c</table><table><tr><td>second</td></tr></table>"],
  ["html-table-grid@1", "<table><thead><tr><td>h</td></tr><tbody><tr><td>b</td></tr><tfoot><tr><td>f</td></tr></table>"], ["html-table-grid@1", "<table><tbody><tr><td>b</td></tr></tbody><thead><tr><td>late head</td></tr></thead></table>"],
  ["html-table-grid@1", "<table><thead></thead><tr><td>a</td><td>b</td><td>c</td></tr><tr><td>1</td><td>2</td><td>3</td></tr><tr><td>4</td><td>5</td><td>6</td></tr></table>"],
  ["html-table-grid@1", "<table><thead><thead><tr><th>deep</th></tr></thead><tr><th>still head</th></tr></thead></thead><tr><td>body</td></tr></table>"],
  ["html-table-grid@1", grid([plain(["", " ", " "]), [cell("td", "<br>"), cell("td", " x  y "), cell("td", "<script>s</script>")]])], ["html-table-grid@1", grid([plain(["😀 a", "b😀", "😀"]), plain(["1", "2", "3"]), plain(["4", "5", "6"])])],
  ["html-table-grid@1", "<p>😀😀 before</p>" + HEADED], ["html-table-grid@1", "<table><caption>unclosed<tr><td>cell</td></tr></table>"], ["html-table-grid@1", "<label>stray</label><caption>stray</caption><table><label>L</label><tr><td>x</td></tr></table>"],
  ["jats-table-grid@1", "<table-wrap><label>T<label>nested</label> end</label><caption/><table><tr><td>a</td></tr></table><table><caption>own</caption><tr><td>b</td></tr></table></table-wrap></table-wrap>"],
  ["jats-table-grid@1", "<table-wrap><caption>open cap<table><tr><td>a</td></tr></table></table-wrap><table-wrap><label></label><label>second</label><table><tr><td>b</td></tr></table></table-wrap>"],
  ["html-table-grid@1", grid([plain(["a", "b", "c"]).concat(many(4100, "<td></td>")), plain(["z"])])], ["html-table-grid@1", grid([[cell("td", "a", ' colspan="1000" rowspan="1000"'), cell("td", "b", ' colspan="1000" rowspan="1000"'), cell("td", "c")], plain(["d"])])],
  ["html-table-grid@1", "<table><tr><td rowspan=\"3\">a</td></tr></table>"], ["html-table-grid@1", grid([[cell("td", "x", ' colspan="4000"'), cell("td", "y", ' colspan="200"'), cell("td", "z")]])],
  ["html-table-grid@1", "<table></table><table><tr></tr></table><table><tr><td></td></tr></table>"],
  ["html-table-grid@1", "<table><caption>cap <label>inner label</label> tail</caption><tr><td>x</td></tr></table>"],
  ["html-table-grid@1", grid([[many(4100, "<td>a</td>")], [many(4100, "<td>b</td>")], plain(["c"])])],
  ["html-table-grid@1", grid([[cell("td", "a", ' colspan="1000" rowspan="1000"'), cell("td", "b", ' colspan="1000" rowspan="1000"')], [cell("td", "c", ' colspan="2"'), cell("td", "d")]])], ["jats-table-grid@1", "<table><tr><entry>cals</entry><th>h</th></tr></table>"],
];
const g = (rows, columns, cells, thead = false) => ({ rows, columns, thead, cells: cells.map(([r, c, text, header = false, rs = 1, cs = 1]) => ({ row: r, column: c, row_span: rs, column_span: cs, header, start: text === "" ? null : 0, end: text === "" ? null : 1, text })) });
const filled = (texts, header = []) => texts.flatMap((line, r) => line.map((text, c) => [r, c, text, header.includes(r)]));
const BODY = [["a", "0.5", "0.6"], ["b", "0.7", "x"]];
export const candidateCases = [
  g(3, 3, filled([["Model", "Acc", "F1"], ...BODY])), g(3, 3, filled([["Model", "Acc", "F1"], ...BODY]), true), g(3, 3, filled([["Model", "Acc", "F1"], ...BODY], [0])), g(3, 3, filled([["Model", "Acc", "F1"], ...BODY], [1])),
  g(3, 2, filled([["Model", "Acc"], ["a", "1"], ["b", "2"]])), g(2, 3, filled([["Model", "Acc", "F1"], BODY[0]])), g(3, 3, filled([["Model", "Acc"], ...BODY])), g(3, 3, [[0, 0, "M"], [0, 1, "A"], [0, 5, "far"], ...filled(BODY).map(([r, c, t]) => [r + 1, c, t])]),
  g(3, 3, [[0, 0, "M"], [0, 1, "A", false, 1, 2], [0, 2, "F"], ...filled(BODY).map(([r, c, t]) => [r + 1, c, t])]), g(3, 3, [[0, 0, "M", false, 2, 1], [0, 1, "A"], [0, 2, "F"], ...filled(BODY).map(([r, c, t]) => [r + 1, c, t])]),
  g(3, 3, filled([["", "Acc", "F1"], ...BODY])), g(3, 3, filled([["Model", " ", "F1"], ...BODY])), g(3, 3, filled([["Model", "0.5", "F1"], ...BODY])), g(3, 3, filled([["Model", "N/A", "F1"], ...BODY])),
  g(3, 3, filled([["Model", "Acc", " acc  "], ...BODY])), g(3, 3, filled([["Model", "A  B", "a b"], ...BODY])), g(3, 3, filled([["Model", "Acc", "F1"], ["a", "x", "y"], ["b", "z", "w"]])),
  g(4, 3, filled([["Model", "Acc", "F1"], ["a", "b", "c"], ["d", "1", "2"], ["e", "3", "4"]])), g(4, 3, filled([["Model", "Acc", "F1"], ["a", "-", ""], ["d", "1", "x"], ["e", "3", "y"]])),
  g(5, 3, filled([["Model", "Acc", "F1"], ["a", "1", ""], ["d", "x", ""], ["e", "y", ""], ["f", "2", ""]])), g(5, 3, filled([["Model", "Acc", "F1"], ["a", "1", ""], ["d", "x", ""], ["e", "y", ""], ["f", "z", ""]])),
  g(3, 3, [...filled([["Model", "Acc", "F1"]]), [1, 0, "span", false, 2, 3]]), g(3, 3, [...filled([["Model", "Acc", "F1"]]), [1, 0, "1", false, 2, 3]]), g(3, 3, []), g(0, 0, []),
  g(3, 3, [[0, 0, "M"], [0, 1, "A"], [0, 1, "dup"], [0, 2, "F"], ...filled(BODY).map(([r, c, t]) => [r + 1, c, t])]),
];
export const printCases = [
  "", " ", "1", "0.5", ".5", "5.", "1,234.5", "1 234", "1 234", "-1", "+1", "−1", "‐1", "–1", "1e-4", "1E4", "1e", "3 x 10^-4", "3×10−4", "3 × 10 ^ 4", "<0.05", ">= 5", "≤5", "~3", "≈ 3", "(1.5)", "[2]", "{3}",
  "5%", "5 %", "5‰", "12bp", "3 kb", "4Mb", "5 Gb", "6kB", "7 MB", "8GB", "9 min", "10ms", "1 mM", "2nM", "3K", "4k", "5M", "6G", "7B", "8b", "9s", "1h", "2m", "5x", "5 km", "1*", "2†", "3‡%)", "((1))", "1%%", "0.722 / 0.948",
  "76.1 {71.1–80.6}", "0.020 ± 3e-4", "1 +/- 2", "1,2", "1;2", "1 → 2", "1 — 2", "1 2 3", "1 a", "a 1", "19,237 raQTLs", "10-cl./binary", "1/", "/1", "1 // 2", "± 1", "1 ±", "±", "-", "--", "---", "–", "—",
  "−", "n/a", "N/A", "na", "NA", "nd", "ns", "yes", "No", "TRUE", "false", "✓", "✔", "✕", "✖", "✗", "✘", "√", "×", "•", "x", "none", " - ", "İ", "1\n2", "1 2", "１", "1.2.3", "1e4e5", "1,", ",1",
  "0x10", "1_000", "$5", "5$", "±0.5", "1 ± 0.5 ± 0.1", "0.5 %", "5   kb",
];

// ---------------------------------------------------------------------------------------
// Trees, metrics and the registry
// ---------------------------------------------------------------------------------------

const HTML_PAGE = `<!DOCTYPE html><html><head><title>T</title><style>p{}</style></head><body><h1>Results</h1>${HEADED}<p>Done ✓</p></body></html>`;
const JATS = `<?xml version="1.0"?><article><body><sec><title>R</title><table-wrap><label>Table 1</label><caption><p>Scores</p></caption>${TABLE}</table-wrap></sec></body></article>`;
const CONTROL_PAGE = `<html><body><table><tr><td>${many(40, "&#1;")}</td><td>x</td></tr></table></body></html>`;
const nested = (depth, data) => (depth === 0 ? data : gz(nested(depth - 1, data)));
export const objects = {
  text: bytes("plain text\nsecond line\n"), crlf: bytes("a\r\nb\r\n"), bom: raw("\xef\xbb\xbfwith a mark\n"), latin: raw("caf\xe9 " + many(30, "x")), wide: Buffer.concat([raw("\xff\xfe"), Buffer.from("wide 😀\n", "utf16le"), raw("\x3d\xd8")]),
  wideOdd: raw("\xfe\xff\0a\0"), markdown: bytes("<div align=\"center\">\n\n# Title\n"), html: bytes(HTML_PAGE), jats: bytes(JATS), xmlNoTable: bytes("<article><body><p>Just prose &amp; more.</p></body></article>"),
  emptyHtml: bytes("<html><head><script>x</script></head><body><nav>n</nav></body></html>"), controlPage: bytes(CONTROL_PAGE), replaced: raw("\xef\xbb\xbf<html><body><table><tr><td>\xff\xfe\xfd</td><td>ok</td></tr></table></body></html>"),
  pdf: bytes("%PDF-1.4\n%%EOF\n"), png: raw("\x89PNG\r\n\x1a\n"), ole: raw("\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"), binary: raw("\0\x01\x02"), empty: Buffer.alloc(0),
  gzText: gz("inside gzip\n"), gzNamed: gzipWith({ name: "inner.html" }, "<html><body><p>named member</p></body></html>"), gzBroken: gz("hello").subarray(0, 12), gz3: nested(3, bytes("three deep\n")), gz4: nested(4, bytes("four deep\n")),
  tarGz: gz(tar([{ name: "a.txt", data: "alpha\n" }, { name: "b.pdf", data: "%PDF-1.4\n" }, { name: "c.png", data: raw("\x89PNG") }, { name: "d.html", data: HTML_PAGE }, { name: "e.bin", data: raw("\xfe\xff\0a\0") }])),
  zipPlain: pack({ "one.txt": "one\n", "two/three.xml": JATS, "empty.txt": "", "inner.zip": pack({ "deep.txt": "deep\n" }) }), zipEmpty: zip([]), zipOdd: pack({ "ok.txt": "ok\n", "bad.txt": raw("\xfe\xff\0a\0") }),
  zipSame: pack({ "b.txt": "same\n", "a.txt": "same\n", "c.txt": "same\n" }), ...packages,
};
export const treeCases = [
  ...Object.keys(objects).map((name) => [name, null, {}]), ["text", "text/html", {}], ["text", "text/csv; charset=utf-8", {}], ["xmlNoTable", "text/html", {}], ["gzText", "text/html", {}],
  ["gz3", null, { maxDepth: 2 }], ["gzText", null, { maxDepth: 0 }], ["zipPlain", null, { maxDepth: 1 }], ["zipPlain", null, { limits: SMALL }], ["plainWorkbook", null, { limits: SMALL }], ["tarGz", null, { limits: { maxMembers: 1024, maxMemberBytes: 16, maxTotalBytes: 1024 } }],
];
export const metricTexts = [
  "", "plain text 123\n", " \t\n\r\f  ﻿\u0085\u001c", "\u0000\u0001\u0008\u0009\u000a\u000b\u000c\u000d\u000e\u001f\u007f\u0080\u009f ", "�� a�", "\f\fpage\f", "+.)! +.+( and =>> then !!!!! end", "((((",
  "٣१１𝟎²½Ⅳ〇", "é中𝐀ʰªא̀‿€", "😀😀a😀", "a.b,c;d", "...a...." + many(3, ".."), many(1000, "+") + "ab", many(400, "+.") + many(200, "a"),
];
const m = (characters, non_space, replacement, printable, digits, letters, punctuation, page_breaks = 0, pdf_pages = null, longest = 0) => ({ characters, non_space, replacement, printable, digits, letters, punctuation, longest_punctuation_run: longest, page_breaks, pdf_pages });
export const sanityCases = [
  [m(0, 0, 0, 0, 0, 0, 0), "text-utf8@1"], [m(10, 0, 0, 10, 0, 0, 0), "html-text@1"], [m(100, 90, 0, 100, 10, 70, 10), "text-utf8@1"], [m(1000, 900, 0, 979, 10, 700, 190), "text-utf8@1"], [m(1000, 900, 0, 980, 10, 700, 190), "text-utf8@1"],
  [m(1000, 900, 0, 998, 10, 700, 190), "text-utf8@1"], [m(1000, 900, 0, 999, 10, 700, 190), "text-utf8@1"], [m(1000, 0, 0, 900, 0, 0, 0), "text-utf8@1"], [m(1000, 900, 10, 1000, 10, 700, 190), "text-utf8@1"], [m(1000, 900, 11, 1000, 10, 700, 190), "text-utf8@1"],
  [m(1000, 900, 1, 1000, 10, 700, 190), "text-utf8@1"], [m(100000, 900, 2, 100000, 10, 700, 190), "text-utf8@1"], [m(1000, 900, 0, 1000, 0, 700, 251), "text-utf8@1"], [m(1000, 900, 0, 1000, 0, 700, 250), "text-utf8@1"], [m(999, 900, 0, 999, 0, 300, 600), "text-utf8@1"],
  [m(1000, 900, 0, 1000, 3, 600, 300), "text-utf8@1"], [m(1000, 900, 0, 1000, 4, 600, 300), "text-utf8@1"], [m(1000, 900, 0, 1000, 0, 0, 900), "text-utf8@1"], [m(1000, 900, 0, 1000, 1, 199, 700), "text-utf8@1"], [m(1000, 900, 0, 1000, 1, 200, 700), "text-utf8@1"],
  [m(100, 90, 0, 100, 10, 70, 10, 3, 3), "pdf-text-layer@1"], [m(100, 90, 0, 100, 10, 70, 10, 3, 4), "pdf-text-layer@1"], [m(100, 90, 0, 100, 10, 70, 10, 3, null), "pdf-text-layer@1"], [m(100, 90, 0, 100, 10, 70, 10, 3, 4), "pdf-text-layer@2"],
  [m(100, 90, 0, 100, 10, 70, 10, 3, 4), "text-utf8@1"], [m(100, 90, 0, 100, 10, 70, 10, 0, 0), "pdf-text-layer@1"], [m(3, 0, 0, 3, 0, 0, 0, 3, 2), "pdf-text-layer@1"], [m(2000, 1900, 30, 1900, 0, 100, 1800, 0, 1), "pdf-text-layer@1"],
  [m(3, 1, 0, 2, 0, 1, 0), "text-utf8@1"], [m(8, 8, 0, 7, 0, 8, 0), "text-utf8@1"], [m(2000, 2000, 1, 1999, 0, 2000, 0), "text-utf8@1"], [m(200, 200, 2, 200, 0, 198, 2), "text-utf8@1"],
];
const baseRow = { sha256: null, bytes: 0, media_type: "text/plain", layer: "exact-bytes", origin: "https://example.org/a", requested_url: null, first_seen_at: "2026-11-17T16:10:11Z", store: "r2", mirrors: [], visibility: "restricted", archive_tier: "t4-private", observed_licence: null, exposure: "policy", derived_from: null, derivation: null, previous_path: null, source_ids: ["s2", "s10", "S1", "é", "😀", "～"] };
export const rowFor = (name, patch = {}) => ({ ...baseRow, sha256: sha256(objects[name]), bytes: objects[name].length, ...patch });
export const objectCases = [
  ...Object.keys(objects).filter((name) => !["workbookTooManyCharacters"].includes(name)).map((name) => [name, {}]), ["text", { media_type: "text/html", store: "github-release", mirrors: ["r2"], visibility: "public", archive_tier: "t0-public", source_ids: ["only"] }],
  ["html", { media_type: "text/html; charset=utf-8", source_ids: [] }], ["zipPlain", { store: "local-cache" }],
];
export const buildCases = [
  { names: ["text", "html", "jats", "zipPlain", "zipSame", "tarGz", "png", "wideOdd", "controlPage", "plainWorkbook", "plainDocument", "gzText"], missing: ["html", "png"] },
  { names: ["zipSame", "text", "crlf"], missing: [] }, { names: [], missing: [] },
];
/** A scratch tree for the commands: the manifest shards and the object cache. */
export const commandTrees = [
  { name: "whole", names: ["text", "html", "jats", "zipPlain", "png", "plainWorkbook", "gzText", "empty"], cached: ["text", "html", "jats", "zipPlain", "png", "plainWorkbook", "gzText", "empty"] },
  { name: "partial", names: ["text", "html", "wideOdd"], cached: ["text", "wideOdd"] }, { name: "bare", names: [], cached: [] },
];
export const cachedObject = (name) => ({ key: sha256(objects[name]), gz: gz(objects[name]).toString("hex") });
export const objectHex = Object.fromEntries(Object.entries(objects).map(([name, data]) => [name, data.toString("hex")]));
export const lines = (...values) => values.map((value) => `${JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))))}\n`).join("");
