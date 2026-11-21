"""The `rewire-evidence` command: `npm run records --`, `locator --` and `checks --`, in Python.

    rewire-evidence records check [ROOT]
    rewire-evidence records descriptions [--json] [ROOT]
    rewire-evidence records add [--root ROOT] [--dry-run] BATCH.jsonl BATCH-DIR
    rewire-evidence records change [--root ROOT] [--dry-run] REVIEW BATCH-DIR ID...
    rewire-evidence records change [--root ROOT] [--dry-run] REVIEW BATCH-DIR --changes CHANGES.jsonl
    rewire-evidence locator coverage [--root ROOT] [--out REPORT.tsv]
    rewire-evidence locator parse LOCATOR...
    rewire-evidence checks report [--root ROOT] [--date YYYY-MM-DD] [--out REPORT.tsv]
    rewire-evidence checks baseline [--root ROOT] [--date YYYY-MM-DD] [--commit SHA] [--out REPORT.tsv]

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
unchanged go to standard error, every store file is as it was, including any the shapes
commands touched, and the status is 1.

REVIEW, BATCH-DIR and each ID are read as Node reads an argument: bytes that are not UTF-8
become U+FFFD. The paths of files are used as given. A ROOT with no `data` folder is refused
by name, before anything is read.

With `--dry-run` they write nothing and run no shapes. They print the lines each store file
would lose and gain, as a unified diff with no context, and then what the write would do.

`locator coverage` reads the locator of every result under ROOT with the grammar, prints
how many are fully addressed, partially addressed, carrying residue and unanchored, by
class, and writes the same table as `maintenance/reports/locator-coverage-<date>.tsv` under
ROOT, or where `--out` says. It writes nothing else. `locator parse` prints one locator's
parse as JSON, the arguments joined by spaces. Both print what the TypeScript prints. A
store that cannot be read is refused in a line with status 1, where the TypeScript leaves
with a stack trace and the same status, and the table is not printed first.

`checks report` runs the seven report-only checks over the store under ROOT, writes one row
for each finding to `maintenance/reports/checks-<date>.tsv` under ROOT, or where `--out`
says, prints each check's counts, and says how the counts stand against
`data/omics/checks-baseline.json`: equal, below, or above it with the rules that rose.
`checks baseline` writes the report and then the baseline file itself, with the date and the
commit given. The date is today's in UTC unless `--date` gives one. Both print what the
TypeScript prints and exit 0, as it does whatever the counts are. The baseline is the file
`CHECKS_BASELINE` names in the environment, from ROOT, as in the TypeScript. A checkout that
cannot be read is refused in a line with status 1.

Status 2 is for a store this library cannot judge, which is neither accepted nor refused:
a URL whose host needs IDNA mapping (`weburl.UndecidedHost`). The TypeScript judges it. It
is also what a command line that cannot be read exits with.

What is printed is written as Node writes it, in UTF-8 whatever the terminal says, with
U+FFFD where the text holds half a character. Started with its output closed, a command
prints nothing and is otherwise the same. If the result cannot be printed, a write that was
made stays made and the status is 0, with a line on standard error saying so, and any other
command exits 1.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from rewire_evidence import ratchet
from rewire_evidence.agreement import check_summary
from rewire_evidence.attributes import said
from rewire_evidence.canonical import js_indented, js_utf8, utf16_key
from rewire_evidence.descriptions import DescriptionProblem, row_problems
from rewire_evidence.js import distinct
from rewire_evidence.jsonl import StoreError, read_values, write_text
from rewire_evidence.locator import classify_locator, coverage_of, parse_locator, rendered
from rewire_evidence.locators import (
    REPORT_DIR,
    report,
    report_lines,
    report_tsv,
    stored_locators,
    tally_locators,
)
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
    today,
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
    groups = parser.add_subparsers(dest="group", required=True)
    commands = groups.add_parser("records", help="the record store")
    located = groups.add_parser("locator", help="the locator grammar").add_subparsers(
        dest="action", required=True
    )
    coverage = located.add_parser("coverage", help="how much of each stored locator the grammar reads")
    coverage.add_argument("--root", type=Path, default=Path(), help="a rewire-benchmark-data checkout")
    coverage.add_argument("--out", type=Path, default=None, help="write the report here")
    parse = located.add_parser("parse", help="print one locator's parse")
    parse.add_argument("text", nargs="*", metavar="LOCATOR", help="the locator, in one argument or several")
    checked = groups.add_parser("checks", help="the report-only checks").add_subparsers(
        dest="action", required=True
    )
    reported = checked.add_parser("report", help="run every check and compare with the baseline")
    pinned = checked.add_parser("baseline", help="run every check and write the baseline")
    pinned.add_argument("--commit", default="", help="the commit the baseline records")
    for run in (reported, pinned):
        run.add_argument("--root", type=Path, default=Path(), help="a rewire-benchmark-data checkout")
        run.add_argument("--date", default=None, help="the date of the run, today's by default")
        run.add_argument("--out", type=Path, default=None, help="write the report here")
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


def _write(stream: TextIO | None, text: str) -> bool:
    """One line as `console.log` writes it: UTF-8, with U+FFFD for half a character.

    The bytes go to the stream's own binary stream, so that the terminal's encoding has no
    say. A stream that has none, such as one that collects text in memory, is given the text.

    Returns whether the line was written or there was nowhere to write it. Python has no
    stream where the command was started with that output closed, and Node opens the null
    device in its place, so nothing is written and nothing is wrong. False is for a stream
    that refused the line: a pipe nobody reads, a full disk, a stream closed since.
    """
    if stream is None:
        return True
    line = js_utf8(f"{text}\n")
    binary = getattr(stream, "buffer", None)
    try:
        if binary is None:
            stream.write(line.decode("utf-8"))
            return True
        stream.flush()
        binary.write(line)
        binary.flush()
    except (OSError, ValueError):
        return False
    return True


def _refusal(error: Exception) -> str:
    """A refusal in a line. A note on it says where, when the message is zod's and names no place."""
    return f"{error}{''.join(f' ({note})' for note in getattr(error, '__notes__', []))}"


def _written(root: Path, planned: Callable[[], Plan], said: Callable[[int], str], *, dry_run: bool) -> str:
    """Decide a write and make it, or with `dry_run` show it. Returns what to print.

    `said` is the line the TypeScript prints for a number of records, which starts with
    what was done to them.
    """
    if not (root / "data").is_dir():
        # Without this a change to a folder that holds no store is refused as an unknown record.
        raise StoreError(f"{root} holds no store: it has no folder named data")
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


def _as_node_reads(argument: str) -> str:
    """An argument as Node reads its bytes: U+FFFD wherever they are not UTF-8.

    Python keeps each byte it cannot decode as a lone surrogate, and would write it into a
    provenance row as an escape where the TypeScript writes U+FFFD. The bytes are taken
    back and decoded as Node decodes them, which `tests/fixtures/argv.json` records. Text
    that was not made from bytes, and holds a surrogate no byte stands for, is left as it is.
    """
    try:
        return os.fsencode(argument).decode("utf-8", errors="replace")
    except UnicodeEncodeError:
        return argument


def _add(args: argparse.Namespace) -> str:
    batch_dir = _as_node_reads(args.batch_dir)
    return _written(
        args.root,
        lambda: plan_add(args.batch, batch_dir, args.root),
        lambda count: f"Added {count} records.",
        dry_run=args.dry_run,
    )


def _change(args: argparse.Namespace) -> str:
    batch_dir, review = _as_node_reads(args.batch_dir), _as_node_reads(args.review)
    if args.changes is not None:
        return _written(
            args.root,
            lambda: plan_changes_file(args.changes, batch_dir, review, args.root),
            lambda count: f"Recorded changes to {count} records from {args.changes}.",
            dry_run=args.dry_run,
        )
    ids = [_as_node_reads(record_id) for record_id in args.ids]
    return _written(
        args.root,
        lambda: plan_change(ids, batch_dir, review, args.root),
        lambda count: f"Recorded changes to {count} records.",
        dry_run=args.dry_run,
    )


def _check(args: argparse.Namespace) -> str:
    return check_summary(len(load_records(args.root)))


def _listed(args: argparse.Namespace) -> str:
    return _descriptions(args.root, as_json=args.json)


def _coverage(args: argparse.Namespace) -> str:
    root: Path = args.root
    rows, total = report(tally_locators(held.printed for held in stored_locators(root)))
    target: Path = root / REPORT_DIR / f"locator-coverage-{today()}.tsv" if args.out is None else args.out
    write_text(target, report_tsv(rows))
    return "\n".join([*report_lines(rows, total), "", f"wrote {os.path.relpath(target, root)}"])


def _parse(args: argparse.Namespace) -> str:
    printed = " ".join(_as_node_reads(part) for part in args.text)
    parsed = parse_locator(printed)
    row = parsed.row()
    return js_indented(
        {
            "class": classify_locator(printed),
            "verdict": parsed.verdict,
            "coverage": coverage_of(parsed.verdict),
            "anchors": row["anchors"],
            "addresses": row["addresses"],
            "selectors": row["selectors"],
            "qualifiers": row["qualifiers"],
            "residue": row["residue"],
            "rendered": rendered(parsed),
        }
    )


def _milliseconds() -> int:
    """`Date.now()`."""
    return time.time_ns() // 1_000_000


def _checks(args: argparse.Namespace) -> ratchet.Command:
    return ratchet.Command(
        args.root,
        today() if args.date is None else args.date,
        os.environ.get("CHECKS_BASELINE", ratchet.BASELINE_FILE),
        args.out,
        getattr(args, "commit", ""),
    )


def _report(args: argparse.Namespace) -> str:
    return ratchet.report(_checks(args), _milliseconds)


def _baseline(args: argparse.Namespace) -> str:
    return ratchet.baseline(_checks(args), _milliseconds)


_ACTIONS: dict[str, Callable[[argparse.Namespace], str]] = {
    "check": _check,
    "descriptions": _listed,
    "add": _add,
    "change": _change,
    "coverage": _coverage,
    "parse": _parse,
    "report": _report,
    "baseline": _baseline,
}
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
    if args.action == "parse" and not (args.text and args.text[0]):
        # The TypeScript prints its usage for no locator and for one that starts with nothing.
        parser.error("locator parse takes a locator")
    try:
        printed = _ACTIONS[args.action](args)
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
    if _write(sys.stdout, printed):
        return 0
    # The work is done and only the saying of it failed. A write is made and checked by now,
    # and stays: it is not a refusal. For anything else what is printed is the result.
    made = args.action in _WRITTEN and not args.dry_run
    _write(
        sys.stderr, "What the command did could not be printed." + (" The store was written." if made else "")
    )
    return 0 if made else REFUSED
