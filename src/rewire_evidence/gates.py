"""The gates as commands: arguments in, a report printed, an exit status out.

`scripts/verify_store_digests.py`, `scripts/verify_store_layout.py`,
`scripts/verify_store_schema.py`, `scripts/verify_store_validation.py`,
`scripts/verify_store_writes.py`, `scripts/verify_store_locators.py`,
`scripts/verify_store_checks.py`, `scripts/verify_store_tables.py`,
`scripts/verify_store_archive.py` and `scripts/verify_store_layers.py` each call one function
here and do nothing else, so everything a gate does is inside the library, where the tests
and the coverage floor reach it.

Exit status, for every one: 0 when everything reproduces, 1 on any disagreement, 2 when the run
could not be made. A disagreement never exits 2, and a run that could not be made never
exits 1, so CI can tell a store that has drifted from a job that was set up wrongly.

A run cannot be made when the store, a vocabulary or an expectation cannot be read, when a
report cannot be written, when the operating system refuses anything else the gate asks of
it, or when a record holds a URL this library does not judge (`weburl.UndecidedHost`).
`_run` holds every gate to that: each of those is one line and exit 2.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from rewire_evidence import (
    agreement,
    archiveparity,
    checkparity,
    conformance,
    layerparity,
    locators,
    replay,
    spanparity,
    tableparity,
)
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
from rewire_evidence.weburl import UndecidedHost

#: Printed in full up to here, so a failure names causes without burying the summary.
PROBLEMS_SHOWN = 20
#: A problem that quotes a registry line is cut to this many characters when it is printed.
PROBLEM_WIDTH = 600


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
    except (StoreError, VocabularyError, UndecidedHost, OSError) as error:
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


def validation_gate(arguments: Sequence[str], expected: Path, description: str | None = None) -> int:
    """Run the validation gate and print what it found. Returns the status.

    `expected` is what the TypeScript recorded of the pinned store, used when the command
    line names no other.
    """
    parser = _parser(description)
    parser.add_argument("--expected", type=Path, default=expected, help="what the TypeScript recorded")
    parser.add_argument("--json", type=Path, default=None, help="write what was measured here")
    args = parser.parse_args(arguments)
    return _run(lambda: _validation(args.root, args.expected, args.json))


def _validation(root: Path, expected: Path, report_file: Path | None) -> int:
    recorded = conformance.read_recorded(expected)
    store_ref = conformance.recorded_ref(recorded)
    sys.stdout.write(f"expecting the store as the TypeScript validated it at {store_ref}\n")
    report = agreement.verify_validation(root, recorded)
    sys.stdout.write(f"{agreement.summary(report)}\n")
    _write_report(report_file, agreement.to_json(report, store_ref))

    for problem in report.problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem}\n")
    if report.problems:
        sys.stderr.write(
            f"\n{len(report.problems):,} problems. "
            "The store is not validated as the TypeScript validates it.\n"
        )
        return 1
    sys.stdout.write(f"\n{report.summary}\n")
    return 0


def writes_gate(arguments: Sequence[str], expected: Sequence[Path], description: str | None = None) -> int:
    """Run the write gate and print what it found. Returns the status.

    `expected` is the expectation files to replay, used when the command line names none.
    The root is a clone of the data repository with its history, which a blobless clone has.
    """
    parser = _parser(description)
    parser.add_argument(
        "--expected", type=Path, action="append", help="cases to replay, in place of the committed ones"
    )
    parser.add_argument("--json", type=Path, default=None, help="write what was measured here")
    parser.add_argument("--scratch", type=Path, default=None, help="replay in this folder and keep it")
    args = parser.parse_args(arguments)
    return _run(lambda: _writes(args.root, args.expected or expected, args.json, args.scratch))


def _writes(root: Path, expected: Sequence[Path], report_file: Path | None, scratch: Path | None) -> int:
    expectations = [replay.read_cases(file) for file in expected]
    repository = replay.Repository(root)
    count = sum(len(expectation.cases) for expectation in expectations)
    sys.stdout.write(f"replaying {count} writes from the history of {root}\n")
    report = replay.ReplayReport()
    with tempfile.TemporaryDirectory(prefix="rewire-evidence-writes-") as made:
        folder = Path(made) if scratch is None else scratch
        for expectation in expectations:
            for case in expectation.cases:
                done = replay.replay_case(repository, case, expectation.configuration, folder)
                report.cases.append(done)
                sys.stdout.write(f"{replay.case_line(done)}\n")
                sys.stdout.flush()
    _write_report(report_file, replay.to_json(report))

    for problem in report.problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem}\n")
    if report.problems:
        sys.stderr.write(
            f"\n{len(report.problems):,} problems. "
            f"{report.reproduced} of {len(report.cases)} writes reproduce.\n"
        )
        return 1
    sys.stdout.write(
        f"\nAll {len(report.cases)} writes reproduce: {report.total('added'):,} records added "
        f"and {report.total('changed'):,} changes recorded, {report.total('files'):,} files byte for byte.\n"
    )
    return 0


def locators_gate(arguments: Sequence[str], expected: Path, description: str | None = None) -> int:
    """Run the locator gate and print what it found. Returns the status.

    `expected` is what the TypeScript recorded of the pinned store, used when the command
    line names no other. The lines it recorded are in the file that names, beside it.
    """
    parser = _parser(description)
    parser.add_argument("--expected", type=Path, default=expected, help="what the TypeScript recorded")
    parser.add_argument("--json", type=Path, default=None, help="write what was measured here")
    args = parser.parse_args(arguments)
    return _run(lambda: _locators(args.root, args.expected, args.json))


def _locators(root: Path, expected: Path, report_file: Path | None) -> int:
    recorded = conformance.read_recorded(expected)
    store_ref = conformance.recorded_ref(recorded)
    dump = locators.read_dump(expected, recorded)
    sys.stdout.write(f"expecting every locator as the TypeScript read it at {store_ref}\n")
    report = locators.verify_locators(root, recorded, dump)
    sys.stdout.write(f"{locators.summary(report)}\n")
    _write_report(report_file, locators.to_json(report, recorded))

    for problem in report.problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem}\n")
    if report.problems:
        sys.stderr.write(
            f"\n{len(report.problems):,} problems. "
            f"{report.reproduced:,} of {report.read:,} locators are read as the TypeScript reads them.\n"
        )
        return 1
    if not report.read:
        sys.stderr.write("Cannot verify: the result files hold no locators\n")
        return 2
    sys.stdout.write(
        f"\nAll {report.read:,} locators are read as the TypeScript reads them, "
        "and the coverage report is the committed one byte for byte.\n"
    )
    return 0


def checks_gate(arguments: Sequence[str], expected: Path, description: str | None = None) -> int:
    """Run the checks gate and print what it found. Returns the status.

    `expected` is what the TypeScript recorded of the pinned store, used when the command
    line names no other. The files of lines it recorded are beside it.
    """
    parser = _parser(description)
    parser.add_argument("first", type=Path, help="a checkout at the commit the committed baseline names")
    parser.add_argument("--expected", type=Path, default=expected, help="what the TypeScript recorded")
    parser.add_argument("--json", type=Path, default=None, help="write what was measured here")
    args = parser.parse_args(arguments)
    return _run(lambda: _checks(args.root, args.first, args.expected, args.json))


def _checks(root: Path, first: Path, expected: Path, report_file: Path | None) -> int:
    recorded = conformance.read_recorded(expected)
    dumps = checkparity.read_dumps(expected, recorded)
    sys.stdout.write(
        f"expecting the committed report of the first run, and the run the TypeScript recorded at "
        f"{conformance.recorded_ref(recorded)}\n"
    )
    report = checkparity.verify_checks(root, first, recorded, dumps)
    sys.stdout.write(f"{checkparity.summary(report)}\n")
    _write_report(report_file, checkparity.to_json(report))

    for problem in report.problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem}\n")
    if report.problems:
        early, late = report.first, report.pinned
        sys.stderr.write(
            f"\n{len(report.problems):,} problems. {early.same:,} of {early.expected:,} rows of the first "
            f"run and {late.same:,} of {late.expected:,} at the pinned commit reproduce.\n"
        )
        return 1
    if not report.first.expected or not report.pinned.expected:
        sys.stderr.write("Cannot verify: a report with no rows holds the checks to nothing\n")
        return 2
    sys.stdout.write(
        f"\nAll {report.first.expected:,} rows of the committed report reproduce, the committed baseline is "
        f"written byte for byte, and all {report.pinned.expected:,} rows at the pinned commit are the "
        "TypeScript's.\n"
    )
    return 0


def tables_gate(arguments: Sequence[str], expected: Path, description: str | None = None) -> int:
    """Run the table gate and print what it found. Returns the status.

    `expected` is what the TypeScript recorded of the pinned store, used when the command
    line names no other. The lines it recorded are in the file that names, beside it.
    """
    parser = _parser(description)
    parser.add_argument("--expected", type=Path, default=expected, help="what the TypeScript recorded")
    parser.add_argument("--json", type=Path, default=None, help="write what was measured here")
    args = parser.parse_args(arguments)
    return _run(lambda: _tables(args.root, args.expected, args.json))


def _tables(root: Path, expected: Path, report_file: Path | None) -> int:
    recorded = conformance.read_recorded(expected)
    dump = tableparity.read_tables_dump(expected, recorded)
    sys.stdout.write(
        f"expecting every committed artifact as the TypeScript read it at "
        f"{conformance.recorded_ref(recorded)}\n"
    )
    report = tableparity.verify_tables(root, recorded, dump)
    sys.stdout.write(f"{tableparity.summary(report)}\n")
    _write_report(report_file, tableparity.to_json(report))

    for problem in report.problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem}\n")
    counts, lines = report.counts, report.lines
    if report.problems:
        sys.stderr.write(
            f"\n{len(report.problems):,} problems. {lines.same:,} of {lines.expected:,} recorded lines "
            "are reproduced.\n"
        )
        return 1
    if not counts.tables:
        sys.stderr.write("Cannot verify: a checkout with no table holds the reader to nothing\n")
        return 2
    sys.stdout.write(
        f"\nAll {counts.tables:,} tables of {counts.artifacts:,} committed artifacts are read as the "
        f"TypeScript reads them, {counts.cells:,} cells in all.\n"
    )
    return 0


def archive_gate(arguments: Sequence[str], expected: Path, description: str | None = None) -> int:
    """Run the archive gate and print what it found. Returns the status.

    `expected` is what the TypeScript recorded of the pinned store, used when the command
    line names no other. The lines it recorded are in the file that names, beside it.
    """
    parser = _parser(description)
    parser.add_argument("--expected", type=Path, default=expected, help="what the TypeScript recorded")
    parser.add_argument("--json", type=Path, default=None, help="write what was measured here")
    args = parser.parse_args(arguments)
    return _run(lambda: _archive(args.root, args.expected, args.json))


def _archive(root: Path, expected: Path, report_file: Path | None) -> int:
    recorded = conformance.read_recorded(expected)
    dump = archiveparity.read_archive_dump(expected, recorded)
    sys.stdout.write(
        f"expecting the manifest, the receipts and the licence map as the TypeScript read them at "
        f"{conformance.recorded_ref(recorded)}\n"
    )
    report = archiveparity.verify_archive(root, recorded, dump)
    sys.stdout.write(f"{archiveparity.summary(report)}\n")
    _write_report(report_file, archiveparity.to_json(report))

    for problem in report.problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem}\n")
    counts, lines = report.counts, report.lines
    if report.problems:
        sys.stderr.write(
            f"\n{len(report.problems):,} problems. {lines.same:,} of {lines.expected:,} recorded lines "
            "are reproduced.\n"
        )
        return 1
    if not counts["manifest_rows"]:
        sys.stderr.write("Cannot verify: a checkout with no manifest row holds the read side to nothing\n")
        return 2
    sys.stdout.write(
        f"\nAll {counts['manifest_rows']:,} manifest rows, {counts['receipts']:,} receipts and "
        f"{counts['candidates']:,} candidates are read as the TypeScript reads them, and "
        f"{report.commands.same} commands print what its commands print.\n"
    )
    return 0


def layers_gate(arguments: Sequence[str], expected: Path, description: str | None = None) -> int:
    """Run the layer gate and print what it found. Returns the status.

    `expected` names the committed registry by digest and the manifest it was written from,
    and is used when the command line names no other.
    """
    parser = _parser(description)
    parser.add_argument(
        "layers_root", type=Path, help="a checkout holding the manifest the registry was written from"
    )
    parser.add_argument("objects", type=Path, help="a folder of objects, laid out as the cache is")
    parser.add_argument("--expected", type=Path, default=expected, help="the pinned expectation")
    parser.add_argument("--json", type=Path, default=None, help="write what was measured here")
    args = parser.parse_args(arguments)
    return _run(lambda: _layers(args.root, args.layers_root, args.objects, args.expected, args.json))


def _layers(root: Path, layers_root: Path, objects: Path, expected: Path, report_file: Path | None) -> int:
    recorded = conformance.read_recorded(expected)
    sys.stdout.write(
        f"expecting the layer registry committed at {conformance.recorded_ref(recorded)}, line for line\n"
    )
    report = layerparity.verify_layers(root, layers_root, objects, recorded)
    sys.stdout.write(f"{layerparity.summary(report)}\n")
    _write_report(report_file, layerparity.to_json(report))

    for problem in report.problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem[:PROBLEM_WIDTH]}\n")
    rows, unsupported = report.total(unsupported=False), report.total(unsupported=True)
    if report.problems:
        sys.stderr.write(
            f"\n{len(report.problems):,} problems. {rows.same:,} of {rows.expected:,} registry rows "
            "are reproduced.\n"
        )
        return 1
    if not rows.same:
        sys.stderr.write("Cannot verify: with no object held, the derivation is held to nothing\n")
        return 2
    sys.stdout.write(
        f"\n{rows.same:,} registry rows and {unsupported.same:,} unsupported rows are derived again from "
        f"{report.objects['checked']:,} objects, each the committed line. "
        f"{rows.expected - rows.same:,} rows of {report.objects['restricted_not_checked']:,} restricted "
        "objects were not checked.\n"
    )
    return 0


def spans_gate(arguments: Sequence[str], expected: Path, description: str | None = None) -> int:
    """Run the span gate and print what it found. Returns the status.

    `expected` names the recordings of the TypeScript's measurement, and is used when the
    command line names no other.
    """
    parser = _parser(description)
    parser.add_argument("objects", type=Path, help="a folder of objects, laid out as the cache is")
    parser.add_argument("--expected", type=Path, default=expected, help="the pinned expectation")
    parser.add_argument(
        "--recorded", type=Path, default=None, help="a folder record_spans_store.mjs wrote in this run"
    )
    parser.add_argument("--json", type=Path, default=None, help="write what was measured here")
    args = parser.parse_args(arguments)
    return _run(lambda: _spans(args.root, args.objects, args.expected, args.recorded, args.json))


def _spans(
    root: Path, objects: Path, expected: Path, recorded_in: Path | None, report_file: Path | None
) -> int:
    recorded = conformance.read_recorded(expected)
    sys.stdout.write(
        f"expecting what the TypeScript measured at {conformance.recorded_ref(recorded)}, result for result\n"
    )
    report = spanparity.verify_spans(root, objects, expected, recorded, recorded_in)
    sys.stdout.write(f"{spanparity.summary(report)}\n")
    _write_report(report_file, spanparity.to_json(report))

    for problem in report.problems[:PROBLEMS_SHOWN]:
        sys.stderr.write(f"  {problem[:PROBLEM_WIDTH]}\n")
    if report.problems:
        sys.stderr.write(
            f"\n{len(report.problems):,} problems. {report.rows.same:,} of {report.rows.expected:,} results "
            "measured here have the recorded line.\n"
        )
        return 1
    sys.stdout.write(
        f"\n{report.rows.same:,} results are measured again over {report.objects['held']:,} objects, each "
        f"with the line the TypeScript gives it. {report.held:,} results need a restricted object and are "
        "held to the recorded line.\n"
    )
    return 0
