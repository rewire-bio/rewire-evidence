// Record how Node reads a command-line argument that is not valid UTF-8.
//
//   node scripts/fixtures/record_argv_fixture.mjs
//
// Built-ins only. It writes tests/fixtures/argv.json. CI runs it on the Node the other
// built-in fixtures are recorded on and fails if the file changes.
//
// `records change <review> <batch-dir> <id>...` writes its arguments into provenance rows, so
// the text Node makes of an argument's bytes is part of what the store holds. Node puts
// U+FFFD where the bytes are not UTF-8. Python keeps each such byte as a lone surrogate, which
// the command line of this library turns into what is recorded here.
//
// A JavaScript string cannot hold the bytes, so each case is given to a second Node by a
// shell, which writes them with printf from octal escapes. The second Node prints the
// argument as JSON.stringify writes it, lone surrogates as escapes.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

// Each case is the bytes of one argument, in hexadecimal. No argument can hold a zero byte.
const cases = [
  ["plain text", "726576"],
  ["two, three and four byte characters", "c3a9e282acf09f9880"],
  ["a byte that starts nothing", "726576ff"],
  ["a continuation byte alone", "80"],
  ["two continuation bytes", "8080"],
  ["a two byte character cut short", "c3"],
  ["a three byte character cut after one byte", "e2"],
  ["a three byte character cut after two bytes", "e282"],
  ["a four byte character cut after three bytes", "f09f98"],
  ["a cut character and then text", "e28261"],
  ["a cut character between text", "61f09f9862"],
  ["an overlong slash", "c0af"],
  ["an overlong null", "e08080"],
  ["a high surrogate written as three bytes", "eda080"],
  ["a surrogate pair written as six bytes", "eda0bdedb880"],
  ["a code point beyond the last", "f4908080"],
  ["a byte no character starts with", "f8888080"],
  ["Latin-1 text", "636166e9"],
  ["a byte order mark", "efbbbf61"],
];

const printed = 'process.stdout.write(JSON.stringify(process.argv[1]))';
const recorded = cases.map(([name, hex]) => {
  const octal = hex.match(/../g).map((byte) => `\\${Number.parseInt(byte, 16).toString(8).padStart(3, "0")}`).join("");
  // The shell reads the escapes from its own first argument, so nothing here is quoted twice.
  const read = execFileSync("/bin/sh", ["-c", 'exec "$1" -e "$2" -- "$(printf "$0")"', octal, process.execPath, printed], { encoding: "utf8" });
  return { name, bytes: hex, read: JSON.parse(read) };
});

fs.writeFileSync(
  path.join(root, "tests/fixtures/argv.json"),
  `${JSON.stringify({ source: "node scripts/fixtures/record_argv_fixture.mjs", cases: recorded }, null, 1)}\n`,
);
console.log(`argv: ${recorded.length} arguments read`);
