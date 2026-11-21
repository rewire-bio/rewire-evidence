"""The locators of a whole store: the coverage report, and what the locator gate measures.

`scripts/omics/locator-cli.ts` reads `data/evidence/results/*.jsonl`, parses the
`source_locator` of every result with the grammar and counts four buckets by class: fully
addressed, partially addressed, carrying residue, unanchored. That is `stored_locators`,
`tally_locators` and `report` here, and `rewire-evidence locator coverage` prints and
writes what `npm run locator -- coverage` does.

The store commits that report and nothing finer, and twelve rows of totals cannot show that
two parsers agree on what a locator says. So the gate rests on a recording.
`scripts/expected/store-locators.jsonl.gz` is one line for every stored locator, written by
the TypeScript at the pinned commit: the record's ID, the class, the coverage, the whole
parse and the text it renders back to. `verify_locators` reads the same locators from the
checkout, writes the same line for each with this library and requires every line to be
the recorded one. It also requires the report to be the bytes of the one the data
repository commits, and version 1 of the grammar to accept exactly the rows of the
`value-check.tsv` files that `check-values` did not report as `locator_not_parsed`.
"""

from __future__ import annotations

import gzip
import json
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path

from rewire_evidence.canonical import JsonObject, JsonValue, js_utf8, js_written, sha256_hex, utf16_key
from rewire_evidence.conformance import differences, recorded_ref
from rewire_evidence.js import Held, Missing, fixed, member, trim, unit_text
from rewire_evidence.jsonl import StoreError, parse_value, read_lines
from rewire_evidence.locator import (
    LOCATOR_CLASSES,
    Parsed,
    classify_locator,
    coverage_of,
    parse_locator,
    parses_under_legacy,
    rendered,
)

RESULTS_DIR = "data/evidence/results"
REPORT_DIR = "maintenance/reports"
BATCHES_DIR = "data/omics"
VALUE_CHECK = "value-check.tsv"
VALUE_CHECK_HEADER = "id\tstatus\tprinted_value\tsource_locator"
#: The status `check-values` gives a row whose locator version 1 of the grammar does not parse.
NOT_PARSED = "locator_not_parsed"
REPORT_HEADER = (
    "class",
    "locators",
    "fully_addressed",
    "fully_addressed_pct",
    "partially_addressed",
    "residue",
    "unanchored",
    "cell_only",
    "round_trip_exact",
    "round_trip_idempotent",
)
#: Rows that differ from the recording are named up to here, and counted beyond it.
ROWS_SHOWN = 10
_CONTEXT = 40


@dataclass(frozen=True)
class Stored:
    """The locator of one result, with the record's ID, which is nothing where it has none."""

    id: Held
    printed: str


def _jsonl_files(folder: Path) -> list[Path]:
    """`readdirSync(dir).filter((f) => f.endsWith(".jsonl")).sort()`."""
    try:
        names = [entry.name for entry in folder.iterdir()]
    except OSError as error:
        raise StoreError(f"{folder} cannot be read: {error}") from error
    return [folder / name for name in sorted(names, key=utf16_key) if name.endswith(".jsonl")]


def stored_locators(root: Path) -> list[Stored]:
    """Every result's locator, in the order the result files hold them: `storedLocators`.

    A line of white space is passed over. A row that is not a result, or whose locator is
    not text, is left out. Raises `StoreError` for a file that cannot be read, a line that
    is not JSON and a row that is null, each of which stops the TypeScript too.
    """
    found: list[Stored] = []
    for file in _jsonl_files(root / RESULTS_DIR):
        for number, line in enumerate(read_lines(file), 1):
            if not trim(line):
                continue
            row = parse_value(line, f"{file}:{number}")
            if row is None:
                raise StoreError(f"{file}:{number} is null, which has no kind to read")
            if not isinstance(row, dict) or row.get("kind") != "result":
                continue
            attributes = row.get("attributes")
            printed = attributes.get("source_locator") if isinstance(attributes, dict) else None
            if isinstance(printed, str):
                found.append(Stored(member(row, "id"), printed))
    return found


def reading(printed: str, parsed: Parsed | None = None) -> JsonObject:
    """One locator as the grammar reads it: its class, coverage, parse and rendering."""
    parsed = parse_locator(printed) if parsed is None else parsed
    return {
        "class": classify_locator(printed),
        "coverage": coverage_of(parsed.verdict),
        "parsed": parsed.row(),
        "rendered": rendered(parsed),
    }


def dump_line(stored: Stored, parsed: Parsed | None = None) -> str:
    """The line the recording holds for a stored locator: `JSON.stringify` of its ID and reading."""
    row: JsonObject = {} if isinstance(stored.id, Missing) else {"id": stored.id}
    return js_written({**row, **reading(stored.printed, parsed)})


@dataclass
class Tally:
    """What the report counts for one class of locator: `ClassTally`."""

    locators: int = 0
    full: int = 0
    partial: int = 0
    residue: int = 0
    unanchored: int = 0
    #: A cell address with no label to check it. Counted inside `partial` and reported on
    #: its own, because it is what a looser reading of "addressed" would quietly take in.
    cell_only: int = 0
    #: Rendering the parse gives back the text, among the fully addressed.
    round_trip_exact: int = 0
    #: Parsing the rendering gives back the same structure, among the fully addressed.
    round_trip_idempotent: int = 0

    def row(self) -> JsonObject:
        """The tally as the TypeScript holds it."""
        return {each.name: getattr(self, each.name) for each in fields(self)}

    def add(self, other: Tally) -> None:
        """Count another tally into this one."""
        for each in fields(self):
            setattr(self, each.name, getattr(self, each.name) + getattr(other, each.name))


def _count(tallies: dict[str, Tally], printed: str, parsed: Parsed) -> None:
    tally = tallies.setdefault(classify_locator(printed), Tally())
    bucket = coverage_of(parsed.verdict)
    tally.locators += 1
    setattr(tally, bucket, getattr(tally, bucket) + 1)
    if parsed.verdict == "cell_only":
        tally.cell_only += 1
    if bucket == "full":
        written = rendered(parsed)
        # `===` compares units, and text given as two halves is one character to JavaScript.
        if unit_text(written) == unit_text(printed):
            tally.round_trip_exact += 1
        if parse_locator(written).structure() == parsed.structure():
            tally.round_trip_idempotent += 1


def tally_locators(locators: Iterable[str]) -> dict[str, Tally]:
    """The tallies by class, in the order the classes were first met: `tallyLocators`."""
    tallies: dict[str, Tally] = {}
    for printed in locators:
        _count(tallies, printed, parse_locator(printed))
    return tallies


def _percent(part: int, whole: int) -> str:
    return "0.0" if whole == 0 else fixed(100 * part / whole, 1)


def _report_row(name: str, tally: Tally) -> list[str]:
    return [
        name,
        str(tally.locators),
        str(tally.full),
        _percent(tally.full, tally.locators),
        str(tally.partial),
        str(tally.residue),
        str(tally.unanchored),
        str(tally.cell_only),
        str(tally.round_trip_exact),
        str(tally.round_trip_idempotent),
    ]


def report(tallies: dict[str, Tally]) -> tuple[list[list[str]], Tally]:
    """The rows of the report: one for each class the store holds, in the fixed order, and the total."""
    total = Tally()
    rows: list[list[str]] = []
    for named in LOCATOR_CLASSES:
        tally = tallies.get(named)
        if tally is not None:
            total.add(tally)
            rows.append(_report_row(named, tally))
    rows.append(_report_row("TOTAL", total))
    return rows, total


def report_tsv(rows: Sequence[Sequence[str]]) -> str:
    """The report as the file the command writes."""
    return "".join("\t".join(row) + "\n" for row in (REPORT_HEADER, *rows))


def report_lines(rows: Sequence[Sequence[str]], total: Tally) -> list[str]:
    """The report as the command prints it: the table, a blank line, and the totals in words."""
    table = [REPORT_HEADER, *rows]
    widths = [max(len(row[column]) for row in table) for column in range(len(REPORT_HEADER))]
    lines = [
        "  ".join(
            cell.ljust(width) if column == 0 else cell.rjust(width)
            for column, (cell, width) in enumerate(zip(row, widths, strict=True))
        )
        for row in table
    ]
    everything, full = total.locators, total.full
    addressed = full + total.partial
    return [
        *lines,
        "",
        f"fully addressed            {full} of {everything} ({_percent(full, everything)}%)",
        f"plus partially addressed   {addressed} of {everything} ({_percent(addressed, everything)}%)",
        (
            f"residue-carrying           {total.residue} of {everything} "
            f"({_percent(total.residue, everything)}%)"
        ),
        (
            f"unanchored                 {total.unanchored} of {everything} "
            f"({_percent(total.unanchored, everything)}%)"
        ),
        f"of the partial, cell only  {total.cell_only}",
        (
            f"round trip exact           {total.round_trip_exact} of {full} addressed "
            f"({_percent(total.round_trip_exact, full)}%)"
        ),
        (
            f"round trip idempotent      {total.round_trip_idempotent} of {full} addressed "
            f"({_percent(total.round_trip_idempotent, full)}%)"
        ),
    ]


# ----------------------------------------------------------------------------------------
# The gate.


@dataclass
class LocatorReport:
    """What the locator gate found."""

    read: int = 0
    reproduced: int = 0
    recorded_lines: int = 0
    value_check_rows: int = 0
    value_check_agreed: int = 0
    measured: JsonObject = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise StoreError(f"{path} is not UTF-8: {error}") from error


@dataclass
class ValueCheck:
    """Version 1 of the grammar against the rows of the value-check files."""

    files: int = 0
    #: Rows `check-values` gave any status but `locator_not_parsed`.
    parsed: int = 0
    not_parsed: int = 0
    #: Rows the pattern accepts and `check-values` did not, or the reverse: the file and the ID.
    disagreed: list[JsonValue] = field(default_factory=list)

    @property
    def rows(self) -> int:
        """Every row read."""
        return self.parsed + self.not_parsed

    def row(self) -> JsonObject:
        """The counts in the names the TypeScript records them under."""
        return {
            "files": self.files,
            "rows": self.rows,
            "parsed": self.parsed,
            "not_parsed": self.not_parsed,
            "disagreed": self.disagreed,
        }


def value_check(root: Path) -> ValueCheck:
    """Version 1 of the grammar against every row of the value-check files.

    The pattern agrees with a row when it accepts the locator exactly where `check-values`
    did not report it as not parsed. Raises `StoreError` for a file with another header.
    """
    batches = root / BATCHES_DIR
    try:
        names = [entry.name for entry in batches.iterdir()]
    except OSError as error:
        raise StoreError(f"{batches} cannot be read: {error}") from error
    # The recording sorts the paths of the files, not the names of the batches, and the two
    # orders part where one name starts another: `a-b/` sorts before `a/`.
    files = sorted(
        (f"{BATCHES_DIR}/{name}/{VALUE_CHECK}" for name in names if (batches / name / VALUE_CHECK).is_file()),
        key=utf16_key,
    )
    counted = ValueCheck(len(files))
    for file in files:
        header, *rows = [line for line in _text(root / file).split("\n") if line] or [""]
        if header != VALUE_CHECK_HEADER:
            raise StoreError(f"{root / file} does not start with the header of a value-check file")
        for row in rows:
            cells = row.split("\t")
            said_parsed = cells[1:2] != [NOT_PARSED]
            if said_parsed:
                counted.parsed += 1
            else:
                counted.not_parsed += 1
            if parses_under_legacy("\t".join(cells[3:])) != said_parsed:
                counted.disagreed.append(f"{file} {cells[0]}")
    return counted


def read_dump(expected: Path, recorded: JsonObject) -> bytes:
    """The recorded lines, uncompressed, from the file the expectation names beside itself.

    Raises `StoreError` for an expectation that names none and for a file that is not gzip.
    """
    named = recorded.get("dump")
    if not isinstance(named, str):
        raise StoreError(f"{expected} does not name the file of recorded lines")
    path = expected.parent / named
    try:
        return gzip.decompress(path.read_bytes())
    except (OSError, EOFError, zlib.error) as error:
        raise StoreError(f"{path} cannot be read: {error}") from error


def _differing(number: int, stored: Stored, mine: bytes, theirs: bytes) -> str:
    """One line that is not the recorded one, shown where the two part."""
    at = next(
        (index for index, pair in enumerate(zip(mine, theirs, strict=False)) if pair[0] != pair[1]), None
    )
    at = min(len(mine), len(theirs)) if at is None else at
    start = max(0, at - _CONTEXT)
    here = mine[start : at + _CONTEXT].decode("utf-8", "replace")
    there = theirs[start : at + _CONTEXT].decode("utf-8", "replace")
    named = "with no ID" if isinstance(stored.id, Missing) else js_written(stored.id)
    return (
        f"locator {number} ({named}) is not read as the TypeScript reads it: "
        f"from byte {start} this library writes {here!r} and the TypeScript wrote {there!r}"
    )


def _committed_reports(root: Path, tsv: str) -> list[JsonValue]:
    """The committed coverage reports that are byte for byte this one."""
    folder = root / REPORT_DIR
    names = sorted((entry.name for entry in folder.iterdir()), key=utf16_key) if folder.is_dir() else []
    wanted = tsv.encode("utf-8")
    return [
        f"{REPORT_DIR}/{name}"
        for name in names
        if name.startswith("locator-coverage-")
        and name.endswith(".tsv")
        and (folder / name).read_bytes() == wanted
    ]


def verify_locators(root: Path, recorded: JsonObject, dump: bytes) -> LocatorReport:
    """Hold this library's reading of every stored locator to what the TypeScript recorded.

    `recorded` is `scripts/expected/store-locators.json` and `dump` the uncompressed lines
    of the file it names. Raises `StoreError` when the run cannot be made. A locator read
    differently, a count or a report that differs, and a dump that is not the one the
    expectation names are problems in the report.
    """
    found = LocatorReport()
    stored = stored_locators(root)
    theirs = [line for line in dump.split(b"\n") if line]
    found.read, found.recorded_lines = len(stored), len(theirs)
    if found.read != found.recorded_lines:
        found.problems.append(
            f"the store holds {found.read} locators and the TypeScript recorded {found.recorded_lines}"
        )
    if sha256_hex(dump) != recorded.get("sha256"):
        found.problems.append("the recorded lines do not have the SHA-256 the expectation names for them")
    tallies: dict[str, Tally] = {}
    digest_of: list[bytes] = []
    differing = 0
    for number, held in enumerate(stored, 1):
        parsed = parse_locator(held.printed)
        _count(tallies, held.printed, parsed)
        mine = js_utf8(dump_line(held, parsed))
        digest_of.append(mine)
        if number <= len(theirs) and mine == theirs[number - 1]:
            found.reproduced += 1
            continue
        differing += 1
        if differing <= ROWS_SHOWN:
            other = theirs[number - 1] if number <= len(theirs) else b""
            found.problems.append(_differing(number, held, mine, other))
    if differing > ROWS_SHOWN:
        found.problems.append(
            f"{differing - ROWS_SHOWN} more locators are not read as the TypeScript reads them"
        )
    rows, total = report(tallies)
    tsv = report_tsv(rows)
    counted = value_check(root)
    found.value_check_rows = counted.rows
    found.value_check_agreed = counted.rows - len(counted.disagreed)
    committed = _committed_reports(root, tsv)
    found.measured = {
        "locators": len(stored),
        "sha256": sha256_hex(b"".join(line + b"\n" for line in digest_of)),
        "report": {
            "committed": committed,
            "printed": "".join(f"{line}\n" for line in report_lines(rows, total)),
            "tsv": tsv,
        },
        "value_check": counted.row(),
    }
    for name, value in found.measured.items():
        found.problems.extend(differences(value, recorded.get(name), name))
    if not committed:
        found.problems.append(
            "no committed coverage report is the one measured, so the report is held to nothing"
        )
    return found


def summary(found: LocatorReport) -> str:
    """The report as the lines the gate prints."""
    held = found.measured.get("value_check")
    counted = held if isinstance(held, dict) else {}
    return "\n".join(
        [
            f"locators read: {found.read:,}",
            f"  lines the TypeScript recorded: {found.recorded_lines:,}",
            f"  read as the TypeScript reads them, byte for byte: {found.reproduced:,}",
            f"value-check rows: {found.value_check_rows:,} in {counted.get('files')} files",
            f"  version 1 of the grammar agrees with check-values on: {found.value_check_agreed:,}",
            f"  parsed: {counted.get('parsed')}, not parsed: {counted.get('not_parsed')}",
        ]
    )


def to_json(found: LocatorReport, recorded: JsonObject) -> str:
    """The report as JSON, for CI to read counts from."""
    return json.dumps(
        {
            "store_ref": recorded_ref(recorded),
            "locators_read": found.read,
            "locators_recorded": found.recorded_lines,
            "locators_reproduced": found.reproduced,
            "value_check_rows": found.value_check_rows,
            "value_check_agreed": found.value_check_agreed,
            "measured": {name: value for name, value in found.measured.items() if name != "report"},
            "problems": found.problems,
        },
        indent=1,
        ensure_ascii=False,
    )
