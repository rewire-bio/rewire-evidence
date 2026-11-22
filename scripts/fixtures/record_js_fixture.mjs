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
//                                     as, string comparison, what JSON.parse refuses, what counts
//                                     as true, trimming, Number of text, lowercase, strict
//                                     equality, localeCompare on record IDs, the URL parser
//                                     and the host it reads, padEnd, sort with no comparator,
//                                     a pattern that ignores case, and a path of no text.
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
// Values as JavaScript joins them into text, which is what a template does with a scheme
// that mapping.json gives as something other than text.
const joined = [
  '"text"', '""', "5", "-1", "1.5", "1.0", "-0", "1e21", "1e-7", "0.000001", "123456789012345678901", "1e400", "-1e400",
  "true", "false", "null", "[]", "[[]]", "[null]", "[null,null]", '["metric"]', '[1,"a",null,[2,null,[3]],{},true]',
  "[1e400,-1e400,1e21]", "{}", '{"a":1}', '{"valueOf":1}', '{"toString":1}', '[1,{"toString":null}]', '{"a":{"toString":1}}',
];
// An object with a member named toString has no text: JavaScript finds the member where
// the function should be and throws. That is recorded as null.
const asText = (value) => {
  try {
    return `${value}`;
  } catch {
    return null;
  }
};
const refuses = (text) => {
  try {
    JSON.parse(text);
    return false;
  } catch {
    return true;
  }
};


// What the validators lean on: which values count as true, what trim removes, what Number
// makes of text, lowercase, slices and lengths in UTF-16 units, and strict equality.
const values = [
  "null", "true", "false", "0", "-0", "1", "0.5", "1e400", '""', '" "', '"0"', '"false"', "[]", "[0]", "{}", '{"a":null}',
];
const spaced = [
  "", " ", "a", " a ", "\t\n\v\f\r a \t\n\v\f\r", "\u00a0a\u00a0", "\ufeffa\ufeff", "\u1680a\u1680", "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200aa",
  "\u2028a\u2029", "\u202fa\u205f\u3000", "\u001ca\u001f", "\u0085a\u0085", "\u200ba\u200b", "\u180ea", "a b", "\u0000a\u0000",
];
const numerals = [
  "", " ", "0", "-0", "+5", "5.", ".5", "5e3", "5E-3", "1e400", "-1e400", "Infinity", "-Infinity", "+Infinity", "infinity", "NaN", "abc", "5x", "0x10", "0X1f",
  "0x", "0b101", "0B11", "0o17", "0O7", "-0x10", "+0x10", "0x1p3", "1_000", "1,5", " 12 ", "\t12\n", "\u00a012\ufeff", "\u200b12", "1 2", "\u0661\u0662", "\uff11\uff12", "0.1e-7", "00012",
  "9007199254740993", "0x20000000000001", "1e", "e5", ".", "+", "-", "++5", "5e+", "0x" + "f".repeat(300),
];
const cased = ["ABC", "Named Model", "\u0130stanbul", "\u0391\u03a3", "\u0391\u03a3 \u03a3", "\u038c\u03a3\u039f\u03a3", "\u01c5", "\u1e9e", "K", "\u00c5", "\u017f", "\ud801\udc00", "\ud800A",
  // A capital sigma is written as a final sigma after a cased letter and before none, and
  // characters such as a full stop, an accent or a soft hyphen are passed over on both sides.
  "\u03a3", "\u03a3\u0391", "\u0391\u03a3\u0391", "\u0391\u03a3.", "\u0391\u03a3.\u0391", "\u0391.\u03a3", "\u0391\u0301\u03a3", "\u0391\u03a3\u0301", "\u0391\u03a3\u0301\u0391", "\u0391\u03a3\u00ad\u0391",
  "\u0391\u03a3'\u0392", "\u0391\u03a3:\u0392", "\u0391\u03a3 \u0392", "a\u03a31", "1\u03a3", "1\u03a3a", "\u0391\u03a3\u02b0", "\u02b0\u03a3", "\ud835\udc00\u03a3", "\u03a3\u03a3", "\u03a3\u03a3\u03a3", "\u0391\u03a3\u03a3", "\u0391\ud800\u03a3", "\u0130\u03a3", "\u0130", "I\u0307", "\u00df", "\u212a", "\u2126", "\u01c4\u01c5\u01c6", "\u2160", "\u24b6", "\ud83a\udd00",
];
const sliced = [["2026-11-18T00:00:00Z", 10], ["2026", 10], ["", 10], ["\ud83e\uddec2026-11-18", 10], ["123456789\ud83e\uddec", 10], ["\ud800123456789", 10]];
const equal = [
  ["1", "1.0"], ["1", "1"], ["0", "-0"], ["1", "true"], ["0", "false"], ["0", '""'], ["null", "null"], ["null", "0"], ['"a"', '"a"'], ['"a"', '"A"'],
  ['"1"', "1"], ["[]", "[]"], ["{}", "{}"], ["[1]", "[1]"], ["true", "true"], ["1e400", "1e400"], ["1e400", "-1e400"], ['"\\ud83e\\uddec"', '"\\ud83e\\uddec"'],
];

// The catalogue query sorts record IDs with localeCompare. For IDs of lowercase letters,
// digits and hyphens that is the order of their UTF-16 units, which is what the port sorts by.
const idLists = [
  ["b", "a", "a-b", "ab", "a0", "a-0", "a", "0", "-", "a--b", "a-", "z9", "z-9", "10", "9", "1-0"],
  ["paper-result-10", "paper-result-9", "paper-result-1a", "paper-result-a1", "paper-result", "paper-results", "paper-result-", "paper"],
  ["reported-task-9f9ab0090f6522", "reported-task-9f9ab0090f6522-a", "reported-task-9f9ab0090f65220", "reported-task-9f9ab0090f652", "reported-task-a", "reported-task-0"],
];
let idState = 20261121;
const idNext = () => {
  idState = (idState + 0x6d2b79f5) >>> 0;
  let t = idState;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const idAlphabet = "abcxyz019-";
for (let list = 0; list < 6; list += 1) {
  const ids = new Set();
  while (ids.size < 60) {
    let id = "";
    const length = 1 + Math.floor(idNext() * 6);
    for (let i = 0; i < length; i += 1) id += idAlphabet[Math.floor(idNext() * idAlphabet.length)];
    ids.add(id);
  }
  idLists.push([...ids]);
}

// What the URL parser makes of an input: the protocol it finds, or null where it throws.
// zod's .url() and the source URL check of validateRecords both rest on it.
const urls = [
  "https://example.org/a", "http://example.org", "HTTP://EXAMPLE.ORG", " https://example.org ", "\thttps://example.org\n", "https://exa mple.org", "https://exa\tmple.org", "https://exa\nmple.org",
  "ftp://example.org/a.pdf", "mailto:someone@example.org", "example.org", "/a/b.txt", "//example.org/a", "https://", "https:", "https:/", "https:///", "https:///example.org", "http:example.org", "https:\\\\example.org", "https:/example.org",
  "c:/x", "c:\\x", "file:///tmp/a.pdf", "file://host/a", "file://c:/a", "file://c|/a", "file://ho st/a", "file:a", "file:", "file://", "file://localhost/a", "file://[::1]/a", "file://1.2.3.4.5/a", "file://%00/a",
  "not a url", "", " ", "a", "a:", "a:b", "1a:b", "+a:b", "a+b-c.d:e", "a b:c", ":a", "://a", "data:text/plain,x", "javascript:void(0)", "urn:isbn:1", "foo://", "foo:///a", "foo://a b", "foo://a%20b", "foo://a^b", "foo://a|b", "foo://a<b", "foo://a:80", "foo://:80", "foo://a:b", "foo://[::1]", "foo://[::1", "foo://a@", "foo://@a",
  "https://user@example.org", "https://user:pass@example.org", "https://@example.org", "https://user@", "https://user:pass@", "https://@", "https://a@b@c", "https://a@b@", "https://:@example.org", "https://example.org:", "https://example.org:80", "https://example.org:080", "https://example.org:65535", "https://example.org:65536",
  "https://example.org:99999", "https://example.org:8a", "https://example.org:-1", "https://example.org: 80", "https://:80", "https://example.org:80:80", "https://example.org::80", "https://example.org:\t8\n0",
  "https://[::1]/a", "https://[::1]:80", "https://[::1]:", "https://[::1]x", "https://[::1", "https://::1]", "https://[]", "https://[::]", "https://[1:2:3:4:5:6:7:8]", "https://[1:2:3:4:5:6:7:8:9]", "https://[1:2:3:4:5:6:7]", "https://[1::2::3]", "https://[:1]", "https://[1:]", "https://[g::]", "https://[12345::]",
  "https://[::1.2.3.4]", "https://[::1.2.3]", "https://[::1.2.3.4.5]", "https://[::256.1.1.1]", "https://[::01.2.3.4]", "https://[1:2:3:4:5:6:1.2.3.4]", "https://[1:2:3:4:5:6:7:1.2.3.4]", "https://[1.2.3.4]", "https://[::1.]", "https://[::.1.2.3.4]", "https://[::ffff:1.2.3.4]", "https://[1:2:3:4:5:6:7::]", "https://[1:2:3:4:5:6::7:8]", "https://[::%25eth0]",
  "https://1.2.3.4", "https://1.2.3.4.", "https://1.2.3.4..", "https://1.2.3", "https://1.2", "https://1", "https://256.1.1.1", "https://1.1.1.256", "https://1.2.3.4.5", "https://0x10.1", "https://0x.1", "https://0x", "https://08", "https://07", "https://4294967295", "https://4294967296", "https://1.2.65536", "https://1.16777216", "https://1..2",
  "https://a.1", "https://a.1.", "https://1.a", "https://a.0x1", "https://a.0xz", "https://a.08", "https://0xffz", "https://1e3", "https://a..b", "https://.", "https://..", "https://.a", "https://a.", "https://a_b", "https://-", "https://a%41", "https://a%2e", "https://a%20b", "https://a%00b", "https://a%7fb", "https://a%b", "https://a%zz", "https://%", "https://%2e",
  "https://a#b", "https://a?b", "https://a/b", "https://a\\b", "https://a^b", "https://a|b", "https://a<b", "https://a>b", "https://a[b", "https://a]b", "https://a\u0000b", "https://a\u007fb", "https://a\u0019b", "https://a\u001fb",
  "ws://example.org", "wss://example.org", "ws://", "ftp://", "ftp:example.org", "https://example.org/a b?c d#e f", "https://example.org/%zz", "https://example.org/../..", "\u0000https://example.org\u0000", "\u00a0https://example.org",
];
// Hosts only Unicode's IDNA mapping can judge. The port refuses to answer for these, and the
// tests hold it to that. Node's answer is kept for the record.
const idnaUrls = ["https://b\u00fccher.example/", "https://xn--bcher-kva.example/", "https://\u4f8b\u3048.jp/", "https://exa\u00admple.org/", "https://\uff25\uff38\uff21\uff2d\uff30\uff2c\uff25.org/", "https://a.XN--bcher-kva/", "https://a%C3%A9.org/", "file://b\u00fccher/a", "https://example.org\u00a0"];
// More of the same shapes, from a generator with a stated seed.
let urlState = 4242;
const urlNext = () => {
  urlState = (urlState + 0x6d2b79f5) >>> 0;
  let t = urlState;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const urlPick = (list) => list[Math.floor(urlNext() * list.length)];
const urlSchemes = ["http", "https", "HTTP", "ftp", "file", "ws", "wss", "mailto", "foo", "a+b-c.d", "c", "1a", "", "ht tp"];
const urlSeparators = [":", "://", ":/", ":///", ":\\\\", ":\\/", "", ": //", "::", ":////"];
const urlAtoms = ["example", "org", "a", "0", "1", "255", "256", "0x10", "0x", "08", "07", "4294967295", "4294967296", "localhost", "", "-", "_", "%41", "%20", "%", "%zz", "%2e", "%00", "1e3", "0xffz"];
const urlJoins = [".", ".", ".", "..", "", ":", "@", "[", "]", " ", "\t", "#", "?", "/", "\\", "^", "|", "<", ">", "%", "\u0000", "\u007f"];
const urlBrackets = ["[::1]", "[::]", "[1:2:3:4:5:6:7:8]", "[::1.2.3.4]", "[1::2::3]", "[::256.1.1.1]", "[:1]", "[1:]", "[g::]", "[::1", "[]", "[::1]x"];
const urlPorts = ["", ":80", ":", ":0", ":65535", ":65536", ":8a", ":-1", ": 80", ":080"];
const urlTails = ["", "/", "/a b", "?q", "#f", "\\x"];
const urlEdges = ["", "", "", " ", "\t", "\n", "\u0000", "\u001f"];
for (let i = 0; i < 1500; i += 1) {
  let host = "";
  if (urlNext() < 0.15) host = urlPick(urlBrackets);
  else {
    const count = 1 + Math.floor(urlNext() * 4);
    for (let j = 0; j < count; j += 1) host += (j ? urlPick(urlJoins) : "") + urlPick(urlAtoms);
  }
  const user = urlNext() < 0.12 ? urlPick(["u@", "u:p@", "@", ":@", "u@v@"]) : "";
  urls.push(urlPick(urlEdges) + urlPick(urlSchemes) + urlPick(urlSeparators) + user + host + urlPick(urlPorts) + urlPick(urlTails) + urlPick(urlEdges));
}
// A port and an IPv4 number are numbers however many digits write them, and Python refuses to
// read an integer of more than 4,300 digits.
const long = 4400;
for (const text of [
  `http://a:${"0".repeat(long)}80/`, `http://a:${"0".repeat(long)}65535`, `http://a:${"0".repeat(long)}65536`, `http://a:${"1".repeat(long)}`, `http://a:${"0".repeat(long)}`, `foo://a:${"0".repeat(long)}80`, `foo://a:${"9".repeat(long)}`, `http://[::1]:${"0".repeat(long)}1`,
  `http://1.2.3.${"1".repeat(long)}`, `http://${"1".repeat(long)}`, `http://${"0".repeat(long)}1`, `http://${"0".repeat(long)}9`, `http://0${"7".repeat(long)}`, `http://0x${"f".repeat(long)}`, `http://0x${"0".repeat(long)}f`, `http://a.${"1".repeat(long)}`, `http://${"1".repeat(long)}.a`,
  `http://1.2.3.${"0".repeat(long)}4`, `http://${"9".repeat(long)}.2.3.4`, `file://${"1".repeat(long)}/a`,
])
  urls.push(text);
// What the archive work list and the licence triage queue read off a URL: its host and its
// hostname in lowercase, or null where the parser throws. More hosts follow that the list
// above has few of: an IPv6 address as it is written out again, a file URL, and a host of a
// scheme that is not special, which keeps what was written with escapes where it needs them.
const hostUrls = [
  "https://[::1]/", "https://[::1]:8443/", "https://[::1]:443/", "http://[::1]:80/", "https://[::]/", "https://[0:0:0:0:0:0:0:0]/", "https://[2001:DB8:0:0:0:0:0:1]/", "https://[1:0:0:2:0:0:0:3]/", "https://[1:0:0:0:2:0:0:0]/", "https://[0:0:1:0:0:1:0:0]/",
  "https://[1:2:3:4:5:6:7:8]/", "https://[1:2:3:4:5:6:7::]/", "https://[::2:3:4:5:6:7:8]/", "https://[1:0:3:4:5:6:7:8]/", "https://[1:0:0:4:5:6:7:8]/", "https://[1:2:3:4:5:6:0:0]/", "https://[0:0:3:4:5:6:7:8]/", "https://[0:2:0:4:0:6:0:8]/", "https://[A:B:C:D:E:F:0:0]/", "https://[00AB:0:0:0::]/", "https://[::ffff:1.2.3.4]/",
  "https://[::1.2.3.4]/", "https://[1:2:3:4:5:6:1.2.3.4]/", "https://[::255.255.255.255]/", "https://[1::0.0.0.0]/", "https://[ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff]/", "https://[1::8]:65535/", "https://user@[1::8]:1/", "ws://[::1]:80", "wss://[::1]:443", "ftp://[::1]:21/", "ftp://[::1]:22/",
  "foo://[::1]", "foo://[::1]:80/a", "foo://[2001:DB8::]/", "foo://[::1.2.3.4]:9/", "s3://[1::]:443",
  "file:///tmp/a", "file:///", "file://", "file:", "file:a", "file:/a", "file://host/a", "file://HOST/a", "file://localhost/a", "file://LOCALHOST/a", "file://local%68ost/a", "file://localhost", "file://localhost.x/a", "file://c:/a", "file://c|/a", "file://C:", "file://cd:/a", "file://[::1]/a", "file://[0:0:0:0:0:0:0:1]/a",
  "file://1.2.3.4/a", "file://0x7f.1/a", "file://1.2.3.4.5/a", "file://ho st/a", "file://host:80/a", "file://a%41/b", "file:\\\\host\\a", "file:/\\host/a", "file://host?q", "file://host#f", "file://host\\a", " file://host/a ", "FILE://Host/a", "file://%00/a", "file://a_b/c",
  "s3://Bucket/key", "s3://Bucket:9000/key", "s3://b\u00e9/key", "s3://B\u00c9/key", "s3://a\u0001b/key", "s3://a\u001fb/key", "s3://a\u007fb/key", "s3://a~b/key", "s3://a\u0080b", "s3://\ud83d\ude00/k", "s3://\ud83d/k", "s3://a\ude00b/k", "s3://\u4f8b\u3048:80/", "s3://a%41%zz%/k", "s3://a b/k", "s3://xn--bcher-kva/k", "s3://A.B:080",
  "s3://", "s3:///a", "s3://:80", "s3://a:", "s3://u:p@H\u00e9/", "foo:bar", "foo:/bar", "doi:10.1/x", "mailto:a@example.org", "urn:isbn:1", "data:text/plain,x", "git+ssh://Git@GitHub.com/a/b", "hf://Datasets/a",
];
const hostOf = (text) => {
  try {
    const url = new URL(text);
    return { host: url.host.toLowerCase(), hostname: url.hostname.toLowerCase() };
  } catch {
    return null;
  }
};
// What the archive read side leaves to a built-in. `padEnd` counts UTF-16 units, so a
// character beyond U+FFFF is padded as two. `sort()` with no comparator orders by the text
// of each element in UTF-16 units, and the text of a pair is `name,count`, so a name that
// is the start of another comes after it when the other goes on with a character below the
// comma. The pairs are recorded as the lines `scripts/licence/counts.ts` prints of them. A pattern with the `i` flag and no `u` flag does not take U+017F for an `s`. And
// `ARCHIVE_CACHE` set to nothing names a cache that does not exist, where the same text
// joined to a folder and a file is a path under the working directory.
const padded = [["cc-by", 20], ["cc-by", 5], ["cc-by", 3], ["", 4], ["\ud83d\ude00", 4], ["a\ud83d\ude00b", 6], ["\ud83d", 3], ["\u00e9", 3], ["e\u0301", 4], ["\uff21", 4], ["string-unresolved", 22], ["\ud83e\uddec\ud83e\uddec", 4], ["\ud83e\uddec\ud83e\uddec", 5], ["\ud83d\ude00", 20], ["\uff21", 20], ["cc", 20]];
const pairLists = [
  [["a", 5], ["a b", 1]], [["a b", 1], ["a", 5]], [["cc-by", 3], ["cc", 12], ["cc-by-nc", 1], ["cc0", 7]], [["x", 10], ["x y", 9], ["x+", 2], ["x,", 1], ["x-", 4]],
  [["\uffff", 1], ["\ud800\udc00", 2]], [["b", 2], ["a", 10], ["B", 3], ["", 4]], [["licence-text", 100], ["licence text", 20], ["licence", 3]], [["\ud83d\ude00", 2], ["cc", 1], ["\uff21", 3]], [],
];
const idSets = [["\uffff", "\ud800\udc00"], ["\ud800\udc00", "\uffff"], ["b", "a", "B", "a"], ["s10", "s9", "s1", "S2"], ["z", "\u00e9", "e\u0301", "\ud83d\ude00", "\uff5e"], ["a-b", "a b", "a", "a,b"], []];
const prefixed = ["https://a", "http://a", "HTTPS://a", "HtTp://a", "http\u017f://a", "HTTP\u017f://a", "http\u017F://a", "https:/a", "https//a", " https://a", "ftp://a", "httpss://a", "http://", "https://", "", "h", "\u212attps://a", "http:\\\\a", "xhttps://a"];
const protocolOf = (text) => {
  try {
    return new URL(text).protocol;
  } catch {
    return null;
  }
};

// A panel compares `comparison?.[field] ?? null` across its results, written by JSON.stringify.
// A value that is not an object still has members: text has a method named split.
const comparedFields = ["split", "subset", "population", "aggregation"];
const comparedValues = ['"x"', '""', '"split"', "5", "0", "true", "false", "null", "[]", '["x"]', "{}", '{"split":"x"}', '{"split":null}', '{"subset":0}', '{"population":["x"]}', '{"aggregation":{"a":1}}'];
const compared = (text, field) => {
  const written = JSON.stringify(JSON.parse(text)?.[field] ?? null);
  // undefined is what JSON.stringify answers for a function, and JSON has no word for it.
  return written === undefined ? { undefined: true } : written;
};

const semantics = {
  source: "node scripts/fixtures/record_js_fixture.mjs",
  key_order: objects.map((text) => [text, JSON.stringify(JSON.parse(text))]),
  canonical_line: objects.map((text) => [text, canonicalLine(JSON.parse(text))]),
  canonical_json: objects.map((text) => [text, canonicalJson(JSON.parse(text))]),
  large_integers: integers.map((text) => [text, JSON.stringify(JSON.parse(text))]),
  overflow: overflowing.map((text) => [text, JSON.stringify(JSON.parse(text))]),
  strings: strings.map((text) => [text, JSON.stringify(text)]),
  as_text: joined.map((text) => [text, asText(JSON.parse(text))]),
  sha256_of_text: hashed.map((text) => [text, sha256(text)]),
  sort: sortInputs.map((list) => [list, [...list].sort(byText)]),
  refused: refused.map((text) => [text, refuses(text)]),
  truthy: values.map((text) => [text, Boolean(JSON.parse(text))]),
  trim: spaced.map((text) => [text, text.trim(), text.replace(/\s+/g, " ")]),
  number_of_text: numerals.map((text) => [text, String(Number(text))]),
  number_of: [...values, '"5"', '" 12 "', "[5]", '["5"]', "[1,2]", "[[7]]", "[null]", '["0x10"]'].map((text) => [text, String(Number(JSON.parse(text)))]),
  lower: cased.map((text) => [text, text.toLowerCase()]),
  slice: sliced.map(([text, end]) => [text, end, text.slice(0, end), text.length]),
  strictly_equal: equal.map(([left, right]) => [left, right, JSON.parse(left) === JSON.parse(right)]),
  locale_order: idLists.map((list) => [list, [...list].sort((a, b) => a.localeCompare(b))]),
  url: urls.map((text) => [text, protocolOf(text), URL.canParse(text.trim())]),
  compared: comparedValues.map((text) => [text, comparedFields.map((field) => compared(text, field))]),
  url_idna: idnaUrls.map((text) => [text, protocolOf(text)]),
  url_host: [...urls, ...hostUrls].map((text) => [text, hostOf(text)]),
  pad_end: padded.map(([text, width]) => [text, width, text.padEnd(width)]),
  pair_order: pairLists.map((pairs) => [pairs, [...pairs].sort().map(([name, count]) => `  ${name.padEnd(20)} ${String(count).padStart(6)}`)]),
  distinct_sorted: idSets.map((ids) => [ids, [...new Set(ids)].sort()]),
  http_prefix: prefixed.map((text) => [text, /^https?:\/\//i.test(text)]),
  empty_path: { exists: fs.existsSync(""), joined: path.join("", "ab", "key.gz") },
};
fs.mkdirSync(path.join(root, "tests/fixtures"), { recursive: true });
fs.writeFileSync(path.join(root, "tests/fixtures/js-semantics.json"), JSON.stringify(semantics, null, 1) + "\n");
console.log(`Recorded with Node ${process.version}.`);
