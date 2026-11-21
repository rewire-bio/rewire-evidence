"""Hold the checks pack to what the data repository commits and to what its TypeScript recorded.

There are two oracles, at two commits of the store.

The first run. The data repository commits the report of the first run of the pack,
`maintenance/reports/checks-2026-11-17.tsv`, and the baseline pinned from it,
`data/omics/checks-baseline.json`. Both were measured on the store as it stood at the commit
the baseline names, which is older than the commit that holds the two files. So the gate
reads the store from a checkout of that older commit and the two files from the pinned
checkout, runs the pack, and requires the report row for row and the baseline file byte for
byte. The baseline holds counts the report cannot: how many columns were formed, how many
results had no anchor, how many were skipped.

The pinned commit. The store has grown since, and nothing committed says what the pack
finds in it now. `scripts/fixtures/record_checks_fixture.mjs` runs the TypeScript over the
pinned store in CI and records the report, each check's counts, the vocabularies as read,
what the command prints and how the ratchet stands. It also records two things a report
cannot show: the anchor and the column read from every result's locator, and what
`parseCell` makes of every printed value. The gate requires each of those of this library,
line for line.

Every count is reported by rule, so a disagreement names the rule it is in.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rewire_evidence import ratchet
from rewire_evidence.canonical import JsonObject, JsonValue, js_parse, js_stringify, js_utf8, sha256_hex
from rewire_evidence.cells import CellError, parse_cell
from rewire_evidence.checks import (
    CHECKS,
    CheckInput,
    RunResult,
    anchor_of,
    column_of,
    load_input,
    report_tsv,
    run_all,
    text,
)
from rewire_evidence.conformance import differences, recorded_ref
from rewire_evidence.jsonl import StoreError

#: How many differing lines of one kind are shown in full.
ROWS_SHOWN = 5
_CONTEXT = 60
#: The date and the time the command prints, which are a run's own.
DATE = "<date>"
SECONDS = "<seconds>"


@dataclass
class Lines:
    """How many lines of one kind were made here, were expected, and are the same."""

    made: int = 0
    expected: int = 0
    same: int = 0

    def row(self) -> JsonObject:
        """The three counts by name."""
        return {"made": self.made, "expected": self.expected, "same": self.same}


@dataclass
class CheckReport:
    """What the checks gate found."""

    #: The commit the first run was measured at, and the one the TypeScript recorded.
    first_ref: str = ""
    store_ref: str = ""
    #: Rows of the first run's committed report, and of the report recorded at the pinned commit.
    first: Lines = field(default_factory=Lines)
    pinned: Lines = field(default_factory=Lines)
    #: The same, by check and rule.
    first_rules: dict[str, Lines] = field(default_factory=dict)
    pinned_rules: dict[str, Lines] = field(default_factory=dict)
    #: Whether the baseline file written for the first run is the committed one, byte for byte.
    baseline_reproduced: bool = False
    #: Records read at the commit of the first run, and at the pinned commit.
    first_records: int = 0
    pinned_records: int = 0
    columns: Lines = field(default_factory=Lines)
    cells: Lines = field(default_factory=Lines)
    measured: JsonObject = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise StoreError(f"{path} cannot be read: {error}") from error


def _member(held: JsonValue, name: str, where: str) -> JsonObject:
    value = held.get(name) if isinstance(held, dict) else None
    if not isinstance(value, dict):
        raise StoreError(f"{where} does not hold {name}")
    return value


def _text_member(held: JsonObject, name: str, where: str) -> str:
    value = held.get(name)
    if not isinstance(value, str):
        raise StoreError(f"{where} does not name its {name}")
    return value


def read_dump(expected: Path, recorded: JsonObject, name: str) -> list[bytes]:
    """The lines of one recorded file, from the compressed file the expectation names beside itself.

    Raises `StoreError` for an expectation that names none, a file that is not gzip, and
    lines that are not the number or do not have the SHA-256 the expectation states.
    """
    facts = _member(recorded, name, str(expected))
    path = expected.parent / _text_member(facts, "file", f"{name} in {expected}")
    try:
        data = gzip.decompress(path.read_bytes())
    except (OSError, EOFError, zlib.error) as error:
        raise StoreError(f"{path} cannot be read: {error}") from error
    lines = data.split(b"\n")[:-1]
    if sha256_hex(data) != facts.get("sha256") or len(lines) != facts.get("lines"):
        raise StoreError(f"{path} is not the file {expected} describes: its lines or its SHA-256 differ")
    return lines


def _where(mine: bytes, theirs: bytes) -> str:
    at = next(
        (index for index, pair in enumerate(zip(mine, theirs, strict=False)) if pair[0] != pair[1]), None
    )
    at = min(len(mine), len(theirs)) if at is None else at
    start = max(0, at - _CONTEXT)
    here = mine[start : at + _CONTEXT].decode("utf-8", "replace")
    there = theirs[start : at + _CONTEXT].decode("utf-8", "replace")
    return f"from byte {start} this library writes {here!r} and the other has {there!r}"


def compare_lines(mine: Sequence[bytes], theirs: Sequence[bytes], what: str, problems: list[str]) -> Lines:
    """Count the lines that are the same in the same place, and say where the first few differ."""
    counted = Lines(len(mine), len(theirs))
    if len(mine) != len(theirs):
        problems.append(f"{what}: {len(mine)} lines made here and {len(theirs)} expected")
    differing = 0
    for number, (here, there) in enumerate(zip(mine, theirs, strict=False), 1):
        if here == there:
            counted.same += 1
            continue
        differing += 1
        if differing <= ROWS_SHOWN:
            problems.append(f"{what}, line {number}: {_where(here, there)}")
    if differing > ROWS_SHOWN:
        problems.append(f"{what}: {differing - ROWS_SHOWN} more lines differ")
    return counted


def _rule_of(row: bytes) -> str:
    """The check and the rule of a row of a report, as the key the ratchet gives the pair."""
    cells = row.split(b"\t")
    return b".".join(cells[:2]).decode("utf-8", "replace")


def by_rule(mine: Sequence[bytes], theirs: Sequence[bytes]) -> dict[str, Lines]:
    """Rows of two reports by check and rule: how many each has, and how many are the same in turn.

    Every rule the pack declares is there, in its order, with or without rows. A row under
    any other name follows them.
    """
    grouped: dict[str, tuple[list[bytes], list[bytes]]] = {
        f"{check.id}.{rule}": ([], []) for check in CHECKS for rule in check.rules
    }
    for side, rows in enumerate((mine, theirs)):
        for row in rows:
            grouped.setdefault(_rule_of(row), ([], []))[side].append(row)
    return {
        rule: Lines(len(here), len(there), sum(a == b for a, b in zip(here, there, strict=False)))
        for rule, (here, there) in grouped.items()
    }


def _report_rows(report: bytes) -> list[bytes]:
    """The rows of a report, without its header and the line end that closes it."""
    return report.split(b"\n")[1:-1]


def column_lines(given: CheckInput) -> list[bytes]:
    """One line for each result: the anchor and the column of its locator, or null for no locator."""
    lines: list[bytes] = []
    for record in given.results:
        locator = text(record, "source_locator")
        read: JsonValue = None if locator is None else [anchor_of(locator), column_of(locator)]
        lines.append(js_utf8(js_stringify(read)))
    return lines


def cell_reading(printed: str, unit: str | None) -> JsonValue:
    """A printed cell as `parse_cell` reads it, or the message it refuses it with."""
    try:
        cell = parse_cell(printed, unit)
    except CellError as error:
        return str(error)
    return [cell.printed, cell.value, cell.sd]


def cell_lines(given: CheckInput) -> list[bytes]:
    """One line for each printed value of the results, apart by whether its unit is percent."""
    seen: set[tuple[bool, str]] = set()
    lines: list[bytes] = []
    for record in given.results:
        printed = text(record, "printed_value")
        if printed is None:
            continue
        percent = text(record, "unit") == "percent"
        if (percent, printed) in seen:
            continue
        seen.add((percent, printed))
        read = cell_reading(printed, "percent" if percent else None)
        lines.append(js_utf8(js_stringify([printed, percent, read])))
    return lines


def vocabulary_of(given: CheckInput) -> JsonObject:
    """The two vocabularies as the recorder writes them: each concept with what the checks read of it."""
    return {
        "metrics": [[concept.key, concept.default_direction] for concept in given.metrics.values()],
        "units": [[concept.key, concept.scope_note] for concept in given.units.values()],
    }


def printed(run: RunResult, pinned_baseline: ratchet.Baseline | None) -> str:
    """What `checks -- report` prints for a run, with its date and its time left as placeholders."""
    said = [
        *ratchet.summary_lines(run),
        "",
        f"{len(run.findings)} findings written to {ratchet.REPORT_DIR}/checks-{DATE}.tsv in {SECONDS} s",
        "",
        ratchet.compared(pinned_baseline, run),
    ]
    return "".join(f"{line}\n" for line in said)


def _lowered(baseline: ratchet.Baseline, check: str, rule: str, by: int) -> ratchet.Baseline:
    """A baseline with one rule, its check and the total each lower by the same number."""
    pinned = baseline.checks[check]
    rules = {**pinned.rules, rule: pinned.rules[rule] - by}
    checks = {
        **baseline.checks,
        check: dataclasses.replace(pinned, findings=pinned.findings - by, rules=rules),
    }
    return ratchet.Baseline(baseline.total_findings - by, checks)


def ratchet_facts(
    given: CheckInput, run: RunResult, baseline: ratchet.Baseline, recorded: JsonValue
) -> JsonObject:
    """How a run stands against the baseline, with the record and the lowering the recording names.

    The record is added to the results and the pack run again, and one count of the
    baseline is lowered, as `tests/checks-baseline.test.ts` does. Raises `StoreError` for
    a recording that names neither.
    """
    seeded = _member(recorded, "seeded", "the recorded ratchet")
    lowered = _member(recorded, "lowered", "the recorded ratchet")
    record = _member(seeded, "record", "the seeded case")
    check, rule = _text_member(lowered, "check", "the lowered case"), _text_member(lowered, "rule", "its")
    by = lowered.get("by")
    pinned = baseline.checks.get(check)
    if not isinstance(by, int) or pinned is None or rule not in pinned.rules:
        raise StoreError(f"the recording lowers {check}.{rule}, which the baseline does not pin")
    grown = run_all(dataclasses.replace(given, results=[*given.results, record]))
    less = _lowered(baseline, check, rule, by)
    return {
        "counts": dict(ratchet.ratchet_counts(run)),
        "baseline_counts": dict(ratchet.ratchet_counts(baseline)),
        "above": list(ratchet.above_baseline(baseline, run)),
        "failure": ratchet.ratchet_failure(baseline, run),
        "seeded": {
            "record": record,
            "above": list(ratchet.above_baseline(baseline, grown)),
            "failure": ratchet.ratchet_failure(baseline, grown),
        },
        "lowered": {
            "check": check,
            "rule": rule,
            "by": by,
            "above": list(ratchet.above_baseline(less, run)),
            "failure": ratchet.ratchet_failure(less, run),
        },
    }


@dataclass(frozen=True)
class Dumps:
    """The lines the TypeScript recorded of the pinned store."""

    report: Sequence[bytes]
    columns: Sequence[bytes]
    cells: Sequence[bytes]


def read_dumps(expected: Path, recorded: JsonObject) -> Dumps:
    """The three recorded files an expectation names. Raises `StoreError` as `read_dump` does."""
    return Dumps(
        read_dump(expected, recorded, "report"),
        read_dump(expected, recorded, "columns"),
        read_dump(expected, recorded, "cells"),
    )


def _written_alike(measured: JsonValue, recorded: JsonValue) -> bool:
    """Whether two values are written as the same JSON: the keys in one order, true never for one.

    `differences` names where two values disagree and takes 1 and true as equal and keys
    in any order. The counts of a run are written into a baseline file in the order the
    TypeScript holds them, so the order and the form are held here as well.
    """
    return js_stringify(measured) == js_stringify(recorded)


def _verify_first(pinned: Path, first: Path, recorded: JsonObject, found: CheckReport) -> ratchet.Baseline:
    """The first run: the committed report and baseline, from the store at the baseline's commit."""
    baseline_bytes = _bytes(pinned / ratchet.BASELINE_FILE)
    try:
        held = js_parse(baseline_bytes.decode("utf-8"))
    except ValueError as error:
        raise StoreError(f"{pinned / ratchet.BASELINE_FILE} is not JSON: {error}") from error
    baseline = ratchet.baseline_of(held, ratchet.BASELINE_FILE)
    facts = held if isinstance(held, dict) else {}
    commit = _text_member(facts, "commit", ratchet.BASELINE_FILE)
    run_date = _text_member(facts, "run_date", ratchet.BASELINE_FILE)
    report_file = f"{ratchet.REPORT_DIR}/checks-{run_date}.tsv"
    committed = _bytes(pinned / report_file)
    theirs = _report_rows(committed)
    found.first_ref = commit
    stated: JsonObject = {
        "commit": commit,
        "run_date": run_date,
        "baseline": ratchet.BASELINE_FILE,
        "baseline_sha256": sha256_hex(baseline_bytes),
        "report": report_file,
        "rows": len(theirs),
        "report_sha256": sha256_hex(committed),
    }
    found.problems.extend(differences(stated, recorded.get("first_run"), "first_run"))

    given = load_input(first)
    run = run_all(given)
    found.first_records = len(given.records)
    made = js_utf8(report_tsv(run.findings))
    mine = _report_rows(made)
    found.first = compare_lines(mine, theirs, f"the report of the first run, {report_file}", found.problems)
    found.first_rules = by_rule(mine, theirs)
    if made != committed and found.first.same == found.first.made == found.first.expected:
        found.problems.append(f"{report_file}: the rows are the committed ones and the file is not")
    written = js_utf8(ratchet.baseline_text(given, run, run_date, commit))
    found.baseline_reproduced = written == baseline_bytes
    if not found.baseline_reproduced:
        compare_lines(
            written.split(b"\n"),
            baseline_bytes.split(b"\n"),
            f"the baseline of the first run, {ratchet.BASELINE_FILE}",
            found.problems,
        )
    return baseline


def verify_checks(pinned: Path, first: Path, recorded: JsonObject, dumps: Dumps) -> CheckReport:
    """Run the pack over two checkouts and hold it to the committed first run and to the recording.

    `pinned` is a checkout at the commit the recording names, which also holds the report
    and the baseline of the first run. `first` is a checkout at the commit that baseline
    names. Raises `StoreError` or `VocabularyError` when the run cannot be made. Every
    disagreement is a problem in the report.
    """
    found = CheckReport(store_ref=recorded_ref(recorded))
    baseline = _verify_first(pinned, first, recorded, found)

    given = load_input(pinned)
    run = run_all(given)
    found.pinned_records = len(given.records)
    made = js_utf8(report_tsv(run.findings)).split(b"\n")[:-1]
    mine = made[1:]
    theirs = list(dumps.report)[1:]
    # The rows are counted without the header, and the header is a line of the file all the same.
    if made[:1] != list(dumps.report)[:1]:
        found.problems.append(
            f"the header of the report at the pinned commit: {_where(made[0], b''.join(dumps.report[:1]))}"
        )
    found.pinned = compare_lines(mine, theirs, "the report at the pinned commit", found.problems)
    found.pinned_rules = by_rule(mine, theirs)
    found.columns = compare_lines(
        column_lines(given), dumps.columns, "the anchor and column of a result", found.problems
    )
    found.cells = compare_lines(
        cell_lines(given), dumps.cells, "a printed value as parse_cell reads it", found.problems
    )
    found.measured = {
        "store": dict(ratchet.store_counts(given)),
        "vocabulary": vocabulary_of(given),
        "total_findings": len(run.findings),
        "summaries": [summary.row() for summary in run.summaries],
        "printed": printed(run, baseline),
        "ratchet": ratchet_facts(given, run, baseline, recorded.get("ratchet")),
    }
    for name, value in found.measured.items():
        differing = differences(value, recorded.get(name), name)
        found.problems.extend(differing)
        if not differing and not _written_alike(value, recorded.get(name)):
            found.problems.append(f"{name}: the values are the recorded ones, in another order or form")
    return found


def _rule_lines(rules: dict[str, Lines]) -> list[str]:
    return [f"    {rule}: {held.same:,} of {held.expected:,}" for rule, held in rules.items()]


def summary(found: CheckReport) -> str:
    """The report as the lines the gate prints: each oracle, and each rule under it."""
    first, pinned, columns, cells = found.first, found.pinned, found.columns, found.cells
    written = "yes" if found.baseline_reproduced else "no"
    return "\n".join(
        [
            f"first run, the store at {found.first_ref}: {found.first_records:,} records",
            f"  rows of the committed report reproduced: {first.same:,} of {first.expected:,}",
            *_rule_lines(found.first_rules),
            f"  the committed baseline file written byte for byte: {written}",
            f"pinned commit, the store at {found.store_ref}: {found.pinned_records:,} records",
            f"  rows of the report the TypeScript recorded: {pinned.same:,} of {pinned.expected:,}",
            *_rule_lines(found.pinned_rules),
            f"  locators read for the same anchor and column: {columns.same:,} of {columns.expected:,}",
            f"  printed values read as parseCell reads them: {cells.same:,} of {cells.expected:,}",
        ]
    )


def to_json(found: CheckReport) -> str:
    """The report as JSON, for CI to read counts from."""
    return json.dumps(
        {
            "first_ref": found.first_ref,
            "store_ref": found.store_ref,
            "first_records": found.first_records,
            "first_rows": found.first.expected,
            "first_rows_reproduced": found.first.same,
            "first_rules": {rule: held.row() for rule, held in found.first_rules.items()},
            "baseline_reproduced": found.baseline_reproduced,
            "pinned_records": found.pinned_records,
            "pinned_rows": found.pinned.expected,
            "pinned_rows_reproduced": found.pinned.same,
            "pinned_rules": {rule: held.row() for rule, held in found.pinned_rules.items()},
            "columns": found.columns.row(),
            "cells": found.cells.row(),
            "measured": {name: value for name, value in found.measured.items() if name != "printed"},
            "problems": found.problems,
        },
        indent=1,
        ensure_ascii=False,
    )
