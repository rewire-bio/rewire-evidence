"""The `rewire-evidence` command: `npm run records -- check` and `-- descriptions`, in Python.

    rewire-evidence records check [ROOT]
    rewire-evidence records descriptions [--json] [ROOT]

`check` loads the store at ROOT, the working directory by default, through every check
`loadRecords` makes, and prints the line the TypeScript prints: how many records match their
provenance. It exits 0. For a store that is refused it prints the refusal on standard error
and exits 1, where the TypeScript leaves with a stack trace and the same status.

`descriptions` lists every description the check refuses, with counts by kind and by rule,
or with `--json` the problems as JSON. It reads the lines of the record files as they are
and asks the record schema nothing, as the TypeScript's does, so it lists what is wrong in a
store that does not load. It exits 1 only where the TypeScript's fails: a line that is not
JSON, a row that is null, or a name that is not text on a row that shares its description.

Status 2 is for a store this library cannot judge, which is neither accepted nor refused:
a URL whose host needs IDNA mapping (`weburl.UndecidedHost`). The TypeScript judges it.

What is printed is written as Node writes it, in UTF-8 whatever the terminal says, with
U+FFFD where the text holds half a character.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from rewire_evidence.agreement import check_summary
from rewire_evidence.attributes import said
from rewire_evidence.canonical import js_indented, js_utf8, utf16_key
from rewire_evidence.descriptions import DescriptionProblem, row_problems
from rewire_evidence.js import distinct
from rewire_evidence.jsonl import StoreError, read_values
from rewire_evidence.records import load_records
from rewire_evidence.schema import RecordError, SchemaError
from rewire_evidence.store import record_files
from rewire_evidence.vocab import VocabularyError
from rewire_evidence.weburl import UndecidedHost

#: The store is refused, or the command could not read it.
REFUSED = 1
#: The store holds something this library does not judge.
UNDECIDED = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rewire-evidence", description="Check a store of benchmark records."
    )
    commands = parser.add_subparsers(dest="group", required=True).add_parser(
        "records", help="the record store"
    )
    actions = commands.add_subparsers(dest="action", required=True)
    check = actions.add_parser("check", help="hold every record to every check loadRecords makes")
    check.add_argument("root", type=Path, nargs="?", default=Path(), help="a rewire-benchmark-data checkout")
    listed = actions.add_parser("descriptions", help="list every description the check refuses")
    listed.add_argument("--json", action="store_true", help="write the problems as JSON")
    listed.add_argument("root", type=Path, nargs="?", default=Path(), help="a rewire-benchmark-data checkout")
    return parser


def _tally(problems: Sequence[DescriptionProblem], label: str, key: str) -> list[str]:
    """Counts by kind or by rule, in the order `[...counts].sort()` puts the pairs in.

    That sort compares each pair as text, which is the name, a comma and the count.
    """
    counts = Counter(getattr(problem, key) for problem in problems)
    pairs = sorted(counts.items(), key=lambda pair: utf16_key(f"{pair[0]},{pair[1]}"))
    return [f"  {label} {name}: {count}" for name, count in pairs]


def _descriptions(root: Path, *, as_json: bool) -> str:
    problems = row_problems([row for file in record_files(root) for row in read_values(file)])
    if as_json:
        return js_indented([problem.row() for problem in problems])
    lines = [f"{said(problem.id)}\t{problem.rule}\t{problem.description}" for problem in problems]
    lines.append(f"\n{distinct(problem.id for problem in problems)} records, {len(problems)} problems")
    return "\n".join([*lines, *_tally(problems, "kind", "kind"), *_tally(problems, "rule", "rule")])


def _write(stream: TextIO, text: str) -> None:
    """One line as `console.log` writes it: UTF-8, with U+FFFD for half a character.

    The bytes go to the stream's own binary stream, so that the terminal's encoding has no
    say. A stream that has none, such as one that collects text in memory, is given the text.
    """
    line = js_utf8(f"{text}\n")
    binary = getattr(stream, "buffer", None)
    if binary is None:
        stream.write(line.decode("utf-8"))
        return
    stream.flush()
    binary.write(line)
    binary.flush()


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the command. Returns the exit status."""
    args = _parser().parse_args(arguments)
    try:
        if args.action == "check":
            printed = check_summary(len(load_records(args.root)))
        else:
            printed = _descriptions(args.root, as_json=args.json)
    except UndecidedHost as error:
        _write(sys.stderr, f"Cannot judge the store: {error}")
        return UNDECIDED
    except (StoreError, VocabularyError, RecordError, SchemaError, OSError) as error:
        _write(sys.stderr, str(error))
        return REFUSED
    _write(sys.stdout, printed)
    return 0
