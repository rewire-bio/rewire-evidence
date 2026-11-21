"""The `rewire-evidence` command: `npm run records --` check, descriptions, add and change, in Python.

    rewire-evidence records check [ROOT]
    rewire-evidence records descriptions [--json] [ROOT]
    rewire-evidence records add [--root ROOT] [--dry-run] BATCH.jsonl BATCH-DIR
    rewire-evidence records change [--root ROOT] [--dry-run] REVIEW BATCH-DIR ID...
    rewire-evidence records change [--root ROOT] [--dry-run] REVIEW BATCH-DIR --changes CHANGES.jsonl

`check` loads the store at ROOT, the working directory by default, through every check
`loadRecords` makes, and prints the line the TypeScript prints: how many records match their
provenance. It exits 0. For a store that is refused it prints the refusal on standard error
and exits 1, where the TypeScript leaves with a stack trace and the same status.

`descriptions` lists every description the check refuses, with counts by kind and by rule,
or with `--json` the problems as JSON. It reads the lines of the record files as they are
and asks the record schema nothing, as the TypeScript's does, so it lists what is wrong in a
store that does not load. It exits 1 only where the TypeScript's fails: a line that is not
JSON, a row that is null, or a name that is not text on a row that shares its description.

`add` adds a reviewed batch of new records and gives each a provenance row naming
BATCH-DIR. `change` records a reviewed change: to the records named, which were edited in
place beforehand, or with `--changes` to the records a changes file holds, which it writes.
REVIEW and BATCH-DIR are written into each changed record's provenance row with today's
date in UTC. Both write every store file as the TypeScript writes it, then hold what they
wrote to the SHACL shapes through the data repository's own `npm run kg -- subset` and
`npm run kg:build -- --shapes-only`, so ROOT has to be a checkout of it with its
dependencies installed. They print the line the TypeScript prints and exit 0. If the batch,
the change or the shapes are refused, the refusal and one more line saying the store is
unchanged go to standard error, every file is as it was, and the status is 1.

With `--dry-run` they write nothing and run no shapes. They print the lines each store file
would lose and gain, as a unified diff with no context, and then what the write would do.

Status 2 is for a store this library cannot judge, which is neither accepted nor refused:
a URL whose host needs IDNA mapping (`weburl.UndecidedHost`). The TypeScript judges it. It
is also what a command line that cannot be read exits with.

What is printed is written as Node writes it, in UTF-8 whatever the terminal says, with
U+FFFD where the text holds half a character.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Sequence
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
from rewire_evidence.writes import (
    Plan,
    RestoreError,
    ShapesError,
    diff,
    plan_add,
    plan_change,
    plan_changes_file,
    shapes_check,
    write,
)

#: The store is refused, or the command could not read it.
REFUSED = 1
#: The store holds something this library does not judge.
UNDECIDED = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rewire-evidence", description="Check a store of benchmark records, and write to it."
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
    add = actions.add_parser("add", help="add a reviewed batch of new records")
    add.add_argument("batch", type=Path, help="the batch: one new record to a line")
    add.add_argument("batch_dir", help="the batch folder, written as each record's added_in")
    change = actions.add_parser("change", help="record a reviewed change to existing records")
    change.add_argument("review", help="the review of the change, as a path in the data repository")
    change.add_argument("batch_dir", help="the batch folder, written as the change's inputs")
    change.add_argument("ids", nargs="*", metavar="ID", help="records edited in place beforehand")
    change.add_argument("--changes", help="a changes file (rdf-kg-mcp draft_export) to apply")
    for writer in (add, change):
        writer.add_argument("--root", type=Path, default=Path(), help="a rewire-benchmark-data checkout")
        writer.add_argument(
            "--dry-run", action="store_true", help="write nothing: print the lines that would change"
        )
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


def _refusal(error: Exception) -> str:
    """A refusal in a line. A note on it says where, when the message is zod's and names no place."""
    return f"{error}{''.join(f' ({note})' for note in getattr(error, '__notes__', []))}"


def _written(root: Path, planned: Callable[[], Plan], said: Callable[[int], str], *, dry_run: bool) -> str:
    """Decide a write and make it, or with `dry_run` show it. Returns what to print.

    `said` is the line the TypeScript prints for a number of records, which starts with
    what was done to them.
    """
    plan = planned()
    done = said(len(plan.ids))
    if not dry_run:
        write(plan, shapes_check(root))
        return done
    changes = plan.changes()
    summary = (
        f"Dry run, so nothing was written and the SHACL shapes were not checked. "
        f"Without --dry-run: {done[:-1]}, in {len(changes)} files."
    )
    return "\n".join([*diff(changes, root), summary])


def _add(args: argparse.Namespace) -> str:
    return _written(
        args.root,
        lambda: plan_add(args.batch, args.batch_dir, args.root),
        lambda count: f"Added {count} records.",
        dry_run=args.dry_run,
    )


def _change(args: argparse.Namespace) -> str:
    if args.changes is not None:
        return _written(
            args.root,
            lambda: plan_changes_file(args.changes, args.batch_dir, args.review, args.root),
            lambda count: f"Recorded changes to {count} records from {args.changes}.",
            dry_run=args.dry_run,
        )
    return _written(
        args.root,
        lambda: plan_change(args.ids, args.batch_dir, args.review, args.root),
        lambda count: f"Recorded changes to {count} records.",
        dry_run=args.dry_run,
    )


#: What a write is called when it is refused, as the TypeScript calls it.
_WRITTEN = {"add": "The batch", "change": "The changed records"}


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the command. Returns the exit status."""
    parser = _parser()
    args = parser.parse_args(arguments)
    if args.action == "change" and bool(args.ids) == (args.changes is not None):
        parser.error(
            "records change takes the IDs of records edited in place, or --changes and a file, not both"
        )
    try:
        if args.action == "check":
            printed = check_summary(len(load_records(args.root)))
        elif args.action == "descriptions":
            printed = _descriptions(args.root, as_json=args.json)
        elif args.action == "add":
            printed = _add(args)
        else:
            printed = _change(args)
    except UndecidedHost as error:
        _write(sys.stderr, f"Cannot judge the store: {error}")
        return UNDECIDED
    except RestoreError as error:
        _write(sys.stderr, str(error))
        return REFUSED
    except (StoreError, VocabularyError, RecordError, SchemaError, ShapesError, OSError) as error:
        _write(sys.stderr, _refusal(error))
        if args.action in _WRITTEN:
            _write(sys.stderr, f"{_WRITTEN[args.action]} failed validation; the store is unchanged.")
        return REFUSED
    _write(sys.stdout, printed)
    return 0
