"""Write patterns put together at random, with what `jsregex` makes of each on a list of texts.

Usage: python scripts/fixtures/compare_patterns.py <seed> <patterns> <out.json>
       node scripts/fixtures/compare_patterns.mjs <out.json>

A differential check of `jsregex` against a Node, for looking at a new construct before its
cases are written. It is not a gate and CI does not run it: the recorded fixtures are the
evidence, and they come from the Node the workflow names. This compares with whatever Node
runs the second command, so a difference may be that Node's and not this library's.

Each pattern is built from the pieces in `ATOMS`, `REPEATS` and `GROUPS`. One that `jsregex`
refuses is counted and left out. For the rest, the file holds what `exec` returns and what
`replace` writes on each text, and `compare_patterns.mjs` asks Node for the same and prints
every case where the two part. A text with a character beyond U+FFFF is left out for a
pattern with the `u` flag, where V8 is known to part from the specification.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from rewire_evidence.js import paired, unit_text
from rewire_evidence.jsregex import JsPattern, Unsupported

#: Pieces whose sets `data/pattern-classes.json` holds for the flags below.
ATOMS: tuple[str, ...] = (
    "a",
    "b",
    "x",
    "s",
    "k",
    ".",
    "\\s",
    "\\d",
    "\\w",
    "\\b",
    "[a-z]",
    "[^a]",
    "[^x]",
    "^",
    "$",
    "'",
)
ATOMS += ("\\(", "\\)", " ", "1")
REPEATS = ("", "", "", "*", "+", "?", "*?", "+?", "??", "{0,2}", "{1,3}?", "{2}", "{0,1}")
GROUPS = ("(?:", "(?:", "(?=", "(?!", "(?<=", "(?<!")
FLAGS = ("", "g", "i", "gi", "u", "gu")
TEXTS: tuple[str, ...] = (
    "",
    "a",
    "ab",
    "x1y",
    "abc",
    "ssheet",
    "e",
    " a  b ",
    "(a)",
    "aaxxb1",
    "a's k",
    "\n a",
    "éa",
)
TEXTS += ("a\U0001f600b", "\U0001f600")
_DEPTH = 2
_GROUP_SHARE = 0.3
_LAST_UNIT = 0xFFFF
_ARGUMENTS = 4

type Case = tuple[str, list[object] | None, str]


def _sequence(chance: random.Random, depth: int) -> str:
    return "".join(_piece(chance, depth) for _ in range(chance.randint(0, 3)))


def _piece(chance: random.Random, depth: int) -> str:
    if depth < _DEPTH and chance.random() < _GROUP_SHARE:
        inner = "|".join(_sequence(chance, depth + 1) for _ in range(chance.randint(1, 3)))
        opener = chance.choice((*GROUPS, f"(?<g{chance.randint(1, 9)}>"))
        return f"{opener}{inner}){chance.choice(REPEATS)}"
    return chance.choice(ATOMS) + chance.choice(REPEATS)


def _cases(source: str, flags: str) -> list[Case]:
    """What this library makes of one pattern on every text. Raises `Unsupported` for one it refuses."""
    every, once = JsPattern(source, flags), JsPattern(source, flags.replace("g", ""))
    cases: list[Case] = []
    for written in TEXTS:
        if "u" in flags and any(ord(character) > _LAST_UNIT for character in written):
            continue
        text = unit_text(written)
        found = once.exec(text)
        groups = found and {name: held and paired(held) for name, held in found.groups.items()}
        returned = None if found is None else [found.index, paired(found.text), groups]
        cases.append((written, returned, paired(every.replace(text, "<>"))))
    return cases


def generated(seed: int, wanted: int) -> dict[str, object]:
    """`wanted` patterns this library accepts, each with its cases, and how many it refused on the way."""
    # Not for secrecy: the seed is given so that a run can be made again.
    chance = random.Random(seed)  # noqa: S311
    cases: list[tuple[str, str, list[Case]]] = []
    refused = 0
    while len(cases) < wanted:
        source = "|".join(_sequence(chance, 0) for _ in range(chance.choice((1, 1, 1, 2))))
        flags = chance.choice(FLAGS)
        try:
            cases.append((source, flags, _cases(source, flags)))
        except Unsupported:
            refused += 1
    return {"seed": seed, "refused": refused, "cases": cases}


if __name__ == "__main__":
    if len(sys.argv) != _ARGUMENTS:
        sys.exit(str(__doc__))
    made = generated(int(sys.argv[1]), int(sys.argv[2]))
    Path(sys.argv[3]).write_text(json.dumps(made), encoding="utf-8")
    print(f"Wrote {sys.argv[2]} patterns to {sys.argv[3]}, and refused {made['refused']} on the way.")
