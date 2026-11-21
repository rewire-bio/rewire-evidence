"""The `rewire-evidence` command: `npm run records -- check` and `-- descriptions`, in Python.

    rewire-evidence records check [ROOT]
    rewire-evidence records descriptions [--json] [ROOT]

`check` loads the store at ROOT, the working directory by default, through every check
`loadRecords` makes, and prints the line the TypeScript prints: how many records match their
provenance. It exits 0. For a store that is refused it prints the refusal on standard error
and exits 1, where the TypeScript leaves with a stack trace and the same status.

`descriptions` lists every description the check refuses, with counts by kind and by rule,
or with `--json` the problems as JSON. It reads the record files and applies no other check,
as the TypeScript's does.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from rewire_evidence.agreement import check_summary
from rewire_evidence.canonical import utf16_key
from rewire_evidence.descriptions import DescriptionProblem, description_problems
from rewire_evidence.jsonl import StoreError, read_rows
from rewire_evidence.records import load_records, parsed
from rewire_evidence.schema import RecordError, SchemaError
from rewire_evidence.store import record_files
from rewire_evidence.vocab import VocabularyError
from rewire_evidence.weburl import UndecidedHost


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
    counts = Counter(getattr(problem, key) for problem in problems)
    return [f"  {label} {name}: {counts[name]}" for name in sorted(counts, key=utf16_key)]


def _descriptions(root: Path, *, as_json: bool) -> str:
    records = [
        parsed(row, f"{file}:{number}")
        for file in record_files(root)
        for number, row in enumerate(read_rows(file), 1)
    ]
    problems = description_problems(records)
    if as_json:
        return json.dumps([problem.row() for problem in problems], indent=2, ensure_ascii=False)
    lines = [f"{problem.id}\t{problem.rule}\t{problem.description}" for problem in problems]
    lines.append(f"\n{len({problem.id for problem in problems})} records, {len(problems)} problems")
    return "\n".join([*lines, *_tally(problems, "kind", "kind"), *_tally(problems, "rule", "rule")])


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the command. Returns the exit status."""
    args = _parser().parse_args(arguments)
    try:
        if args.action == "check":
            said = check_summary(len(load_records(args.root)))
        else:
            said = _descriptions(args.root, as_json=args.json)
    except (StoreError, VocabularyError, RecordError, SchemaError, UndecidedHost, OSError) as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"{said}\n")
    return 0
