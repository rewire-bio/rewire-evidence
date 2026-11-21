"""The gates as commands: arguments in, a report printed, an exit status out.

`scripts/verify_store_digests.py`, `scripts/verify_store_layout.py` and
`scripts/verify_store_schema.py` each call one function here and do nothing else, so
everything a gate does is inside the library, where the tests and the coverage floor reach
it.

Exit status, for all three: 0 when everything reproduces, 1 on any disagreement, 2 when the run
could not be made. A disagreement never exits 2, and a run that could not be made never
exits 1, so CI can tell a store that has drifted from a job that was set up wrongly.

A run cannot be made when the store, a vocabulary or an expectation cannot be read, when a
report cannot be written, or when the operating system refuses anything else the gate
asks of it. `_run` holds every gate to that: each of those is one line and exit 2.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from rewire_evidence import conformance
from rewire_evidence.jsonl import StoreError
from rewire_evidence.parity import (
    compare_content,
    compare_counts,
    read_expectation,
    summary,
    to_json,
    verify_record_digests,
    verify_store,
)
from rewire_evidence.vocab import VocabularyError

#: Printed in full up to here, so a failure names causes without burying the summary.
PROBLEMS_SHOWN = 20


def _parser(description: str | None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("root", type=Path, help="a rewire-benchmark-data checkout")
    return parser


def _say(text: str) -> None:
    """The digest gate reports on standard error, where CI has always read it."""
    sys.stderr.write(f"{text}\n")


def _run(gate: Callable[[], int]) -> int:
    """A gate's status, with whatever stops the run being made reported in a line as 2.

    An error may carry notes, which say where it happened when its message is another
    program's words. They follow the message.
    """
    try:
        return gate()
    except (StoreError, VocabularyError, OSError) as error:
        notes = getattr(error, "__notes__", [])
        _say(f"Cannot verify: {error}{''.join(f' ({note})' for note in notes)}")
        return 2


def _write_report(path: Path | None, text: str) -> None:
    """Write what a gate measured, if it was asked to."""
    if path is not None:
        path.write_text(f"{text}\n", encoding="utf-8")


def digests_gate(arguments: Sequence[str], description: str | None = None) -> int:
    """Recompute every record digest against the committed one and report. Returns the status."""
    root: Path = _parser(description).parse_args(arguments).root
    return _run(lambda: _digests(root))


def _digests(root: Path) -> int:
    report = verify_record_digests(root)
    _say(f"provenance rows: {report.provenance_rows:,}")
    _say(f"records checked: {report.matched + report.mismatched + report.unlisted:,}")
    tally = {"match": report.matched, "MISMATCH": report.mismatched, "no provenance row": report.unlisted}
    for name, count in tally.items():
        if count:
            _say(f"  {name:20} {count:,}")
    for mismatch in report.shown:
        _say(f"\n{mismatch}")

    if report.mismatched or report.unlisted:
        _say(
            f"\n{report.mismatched:,} records do not reproduce and "
            f"{report.unlisted:,} have no provenance row."
        )
        return 1
    if not report.matched:
        _say("Cannot verify: the record files hold no records")
        return 2
    _say(f"\nAll {report.matched:,} records reproduce.")
    return 0


def layout_gate(arguments: Sequence[str], expected: Path, description: str | None = None) -> int:
    """Run the store-layout gate and print what it found. Returns the status.

    `expected` is the pinned expectation used when the command line names no other.
    """
    parser = _parser(description)
    parser.add_argument("--expected", type=Path, default=expected, help="the pinned expectation")
    parser.add_argument("--json", type=Path, default=None, help="write the measured counts here")
    args = parser.parse_args(arguments)
    return _run(lambda: _layout(args.root, args.expected, args.json))


def _layout(root: Path, expected: Path, report_file: Path | None) -> int:
    expectation = read_expectation(expected)
    sys.stdout.write(
        f"expecting the store as it is at {expectation.store_ref}, release {expectation.release_receipt}\n"
    )
    # The writers are checked by writing the store again, into a folder that is then removed.
    with tempfile.TemporaryDirectory(prefix="rewire-evidence-layout-") as scratch:
        report = verify_store(root, expectation.release_receipt, Path(scratch))
    sys.stdout.write(f"{summary(report)}\n")
    _write_report(report_file, to_json(report, expectation))

    problems = [
        *report.problems(),
        *compare_counts(report.counts(), expectation.counts),
        *compare_content(report.content(), expectation),
    ]
    for problem in problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem}\n")
    if problems:
        sys.stderr.write(f"\n{len(problems):,} problems. The store layout does not reproduce.\n")
        return 1
    lines = sum(family.lines for family in report.families)
    files = sum(family.files for family in report.families)
    sys.stdout.write(
        f"family files are the bytes measured at {expectation.store_ref}: "
        f"{len(expectation.content)} of {len(expectation.content)} digests\n"
        f"\nAll {lines:,} lines in {files} files reproduce.\n"
    )
    return 0


def schema_gate(arguments: Sequence[str], expected: Path, description: str | None = None) -> int:
    """Run the schema gate and print what it found. Returns the status.

    `expected` is what the TypeScript recorded of the pinned store, used when the command
    line names no other.
    """
    parser = _parser(description)
    parser.add_argument("--expected", type=Path, default=expected, help="what the TypeScript recorded")
    parser.add_argument("--json", type=Path, default=None, help="write what was measured here")
    args = parser.parse_args(arguments)
    return _run(lambda: _schema(args.root, args.expected, args.json))


def _schema(root: Path, expected: Path, report_file: Path | None) -> int:
    recorded = conformance.read_recorded(expected)
    store_ref = conformance.recorded_ref(recorded)
    sys.stdout.write(f"expecting the store as the TypeScript measured it at {store_ref}\n")
    report = conformance.verify_schema(root, recorded)
    sys.stdout.write(f"{conformance.summary(report)}\n")
    _write_report(report_file, conformance.to_json(report, store_ref))

    for problem in report.problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem}\n")
    if report.problems:
        sys.stderr.write(f"\n{len(report.problems):,} problems. The store does not conform.\n")
        return 1
    if not report.records:
        sys.stderr.write("Cannot verify: the record files hold no records\n")
        return 2
    sys.stdout.write(
        f"\nAll {report.records:,} records are accepted by the schema and written back byte for byte.\n"
    )
    return 0
