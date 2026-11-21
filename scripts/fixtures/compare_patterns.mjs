// Ask this Node what it makes of the patterns scripts/fixtures/compare_patterns.py wrote, and
// print every case where it and the Python part.
//
//   node scripts/fixtures/compare_patterns.mjs <out.json>
//
// Built-ins only. It exits 1 if any case differs. It is a way of looking, not a gate: see
// the Python script.
import fs from "node:fs";

const SHOWN = 20;
const { seed, refused, cases } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
let compared = 0;
let differing = 0;
const differs = (...said) => {
  differing += 1;
  if (differing <= SHOWN) console.log(...said);
};
for (const [source, flags, rows] of cases) {
  let every;
  let once;
  try {
    every = new RegExp(source, flags);
    once = new RegExp(source, flags.replace("g", ""));
  } catch (error) {
    differs("Node refuses", JSON.stringify([source, flags]), error.message);
    continue;
  }
  for (const [text, returned, replaced] of rows) {
    compared += 1;
    const found = once.exec(text);
    const groups = found && Object.fromEntries(Object.entries(found.groups ?? {}).map(([name, held]) => [name, held ?? null]));
    const mine = [found === null ? null : [found.index, found[0], groups], text.replace(every, () => "<>")];
    if (JSON.stringify(mine) !== JSON.stringify([returned, replaced])) {
      differs(JSON.stringify([source, flags, text]), "Node:", JSON.stringify(mine), "Python:", JSON.stringify([returned, replaced]));
    }
  }
}
console.log(`Node ${process.version}, seed ${seed}: ${cases.length} patterns, ${refused} refused by the Python, ${compared} cases, ${differing} differ.`);
process.exit(differing === 0 ? 0 : 1);
