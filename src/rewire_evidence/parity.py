"""The store-layout gate: does this library write the files the store already holds?

Four questions, each answered over a whole checkout of `rewire-benchmark-data`:

1. Does every file of every family come back byte for byte when it is read and written
   again? The comparison is of whole files, so it covers the serialisation, the order of
   the rows, the final newline and the absence of blank lines in one assertion.
2. Is every row in the file the layout assigns it, and is every uniquely keyed file
   strictly sorted?
3. Do the logical store digests equal the ones a named release receipt recorded, and is
   that receipt the newest one?
4. Do the functions that write to disk produce those files? Every committed file is read
   and written into an empty folder by the writer a caller would use, the bytes are
   compared, and the copy is then resharded, which has to move nothing.

A count is reported for everything read, and `compare_counts` holds those counts against a
committed expectation, so a checkout that held fewer rows cannot pass while proving less.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rewire_evidence.canonical import JsonObject, JsonValue, js_parse
from rewire_evidence.families import (
    ENTITIES,
    EVIDENCE,
    FAMILIES,
    PROVENANCE,
    Family,
    append_rows,
    serialise_rows,
    write_family,
)
from rewire_evidence.jsonl import StoreError, parse_row, read_rows
from rewire_evidence.store import (
    checked_records,
    provenance_files,
    record_files,
    reshard_store,
    store_digests,
    write_store,
)

RELEASES_DIR = "data/omics/releases"
#: How much of a differing line is shown. Enough to see the cause, not a whole record.
_SHOWN = 160


@dataclass
class FamilyReport:
    """What the gate found in one family."""

    name: str
    files: int = 0
    lines: int = 0
    #: Rows found in the file the layout assigns them. Zero for a family with no placement.
    placed: int = 0
    problems: list[str] = field(default_factory=list[str])


@dataclass
class DigestReport:
    """The logical digests against one release receipt."""

    release_id: str
    digests: int = 0
    matched: int = 0
    problems: list[str] = field(default_factory=list[str])


@dataclass
class WriteReport:
    """What the writers put on disk, against the committed files."""

    #: Committed files a family writer reproduced in an empty folder.
    family_files: int = 0
    family_lines: int = 0
    #: Record and provenance files `write_store` reproduced, which is a second writer.
    store_files: int = 0
    #: Records `reshard_store` placed in that copy, and the files it left byte for byte.
    resharded_records: int = 0
    resharded_files: int = 0
    problems: list[str] = field(default_factory=list[str])


@dataclass
class StoreReport:
    """Everything the gate measured."""

    families: list[FamilyReport]
    digests: DigestReport
    written: WriteReport
    #: Records whose placement, order and provenance digest all hold.
    records: int

    def problems(self) -> list[str]:
        """Every disagreement found: family by family, then the digests, then the writers."""
        found = [f"{report.name}: {problem}" for report in self.families for problem in report.problems]
        found.extend(f"digests: {problem}" for problem in self.digests.problems)
        return [*found, *(f"writers: {problem}" for problem in self.written.problems)]

    def counts(self) -> dict[str, int]:
        """The figures a pinned expectation is compared with."""
        counts: dict[str, int] = {}
        for report in self.families:
            counts[f"{report.name}.files"] = report.files
            counts[f"{report.name}.lines"] = report.lines
            counts[f"{report.name}.placed"] = report.placed
        by_name = {report.name: report for report in self.families}
        counts["sharded_lines_placed"] = by_name[EVIDENCE.name].placed + by_name[PROVENANCE.name].placed
        counts["records_matching_provenance"] = self.records
        counts["logical_digests"] = self.digests.digests
        counts["logical_digests_matched"] = self.digests.matched
        counts["written.family_files"] = self.written.family_files
        counts["written.family_lines"] = self.written.family_lines
        counts["written.store_files"] = self.written.store_files
        counts["written.resharded_records"] = self.written.resharded_records
        counts["written.resharded_files"] = self.written.resharded_files
        return counts


@dataclass(frozen=True)
class Expectation:
    """The pinned result: which store commit, which release receipt, and every count."""

    store_ref: str
    release_receipt: str
    counts: dict[str, int]


def _shown(line: str) -> str:
    return line if len(line) <= _SHOWN else f"{line[:_SHOWN]}..."


def first_difference(committed: str, emitted: str) -> str:
    """Where two file bodies part, as a sentence naming the line."""
    ours = emitted.split("\n")
    for number, line in enumerate(committed.split("\n"), 1):
        if number > len(ours):
            return f"line {number}: the committed file has more lines than the re-emitted one"
        if line != ours[number - 1]:
            return f"line {number}: committed {_shown(line)!r}, re-emitted {_shown(ours[number - 1])!r}"
    return "the re-emitted file has more lines than the committed one"


def verify_family(family: Family, root: Path) -> FamilyReport:
    """Read every file of a family, write it again in memory and compare the bytes."""
    report = FamilyReport(family.name)
    for file in family.files(root):
        report.files += 1
        try:
            report.lines += _verify_file(family, root, file, report)
        except StoreError as error:
            report.problems.append(str(error))
    return report


def _verify_file(family: Family, root: Path, file: Path, report: FamilyReport) -> int:
    """Check one file, adding what is wrong to the report. Returns the rows it holds."""
    name = file.relative_to(root).as_posix()
    try:
        committed = file.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise StoreError(f"{name} is not UTF-8: {error}") from error
    lines = [line for line in committed.split("\n") if line]
    rows = [parse_row(line, f"{name}:{number}") for number, line in enumerate(lines, 1)]

    emitted = serialise_rows(family, rows)
    if emitted != committed:
        report.problems.append(
            f"{name} does not re-emit byte for byte, {first_difference(committed, emitted)}"
        )
    if family.target is not None:
        for number, row in enumerate(rows, 1):
            if family.target(row, root) == file:
                report.placed += 1
            else:
                report.problems.append(f"{name}:{number} is in the wrong file")
    if family.order is not None and family.unique:
        keys = [family.order(row) for row in rows]
        for number in range(1, len(keys)):
            if keys[number] <= keys[number - 1]:
                report.problems.append(f"{name}:{number + 1} is not sorted by unique key")
    return len(rows)


def _object(value: JsonValue, where: str) -> JsonObject:
    if not isinstance(value, dict):
        raise StoreError(f"{where} is not a JSON object")
    return value


def _string(value: JsonValue, where: str) -> str:
    if not isinstance(value, str):
        raise StoreError(f"{where} is not a string")
    return value


def _read_json(path: Path) -> JsonObject:
    try:
        return _object(js_parse(path.read_bytes().decode("utf-8")), str(path))
    except FileNotFoundError as error:
        raise StoreError(f"{path} does not exist") from error
    except ValueError as error:
        raise StoreError(f"{path} is not JSON: {error}") from error


def release_inputs(root: Path, release_id: str) -> dict[str, str]:
    """The source inputs a release receipt recorded, as digest by file name."""
    path = root / RELEASES_DIR / f"{release_id}.json"
    receipt = _read_json(path)
    coverage = _object(receipt.get("coverage"), f"{path} coverage")
    inputs = coverage.get("source_inputs")
    if not isinstance(inputs, list):
        raise StoreError(f"{path} coverage.source_inputs is not a list")
    digests: dict[str, str] = {}
    for position, item in enumerate(inputs):
        where = f"{path} coverage.source_inputs[{position}]"
        entry = _object(item, where)
        name = _string(entry.get("file"), f"{where}.file")
        digests[name] = _string(entry.get("sha256"), f"{where}.sha256")
    return digests


def newest_release(root: Path) -> str:
    """The ID of the receipt with the latest `released_at`.

    Receipt file names start with a date and end with a hash, so the last name in a
    directory listing is not the newest release when two share a date.
    """
    folder = root / RELEASES_DIR
    released: list[tuple[datetime, str]] = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        receipt = _read_json(path)
        stamp = _string(receipt.get("released_at"), f"{path} released_at")
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError as error:
            raise StoreError(f"{path} released_at is not a timestamp: {stamp}") from error
        if moment.tzinfo is None:
            raise StoreError(f"{path} released_at has no time zone: {stamp}")
        released.append((moment, _string(receipt.get("release_id"), f"{path} release_id")))
    if not released:
        raise StoreError(f"{folder} holds no release receipts")
    return max(released)[1]


def verify_digests(root: Path, release_id: str) -> DigestReport:
    """Hold the store's logical digests against a release receipt."""
    report = DigestReport(release_id)
    recorded = release_inputs(root, release_id)
    newest = newest_release(root)
    if newest != release_id:
        report.problems.append(f"{release_id} is not the newest release receipt; {newest} is")
    for digest in store_digests(root):
        report.digests += 1
        held = recorded.get(digest.file)
        if held is None:
            report.problems.append(f"{digest.file} is not among the inputs of {release_id}")
        elif held != digest.sha256:
            report.problems.append(
                f"{digest.file} has digest {digest.sha256}, and {release_id} recorded {held}"
            )
        else:
            report.matched += 1
    return report


def _same_bytes(committed: Path, written: Path) -> bool:
    """Whether a writer left the committed bytes. A writer given no rows writes no file."""
    return (written.read_bytes() if written.exists() else b"") == committed.read_bytes()


def _write_again(family: Family, rows: list[JsonObject], by_family: Path, by_store: Path, name: Path) -> None:
    """Hand one committed file's rows to every writer that writes its family."""
    if family.order is None:
        append_rows(family, rows, by_family, by_family / name)
    else:
        write_family(family, rows, by_family)
    if family in (ENTITIES, EVIDENCE):
        write_store(rows, [], by_store)
    elif family is PROVENANCE:
        write_store([], rows, by_store)


def verify_writers(root: Path, scratch: Path) -> WriteReport:
    """Write every committed file again with the library's writers and compare the bytes.

    `scratch` is an empty folder. Each file is read, and its rows are handed to the writer
    a caller would use: `write_family` for a sorted family, `append_rows` for an append-only
    one, and `write_store` for records and provenance, which is a separate implementation
    of the same layout. The copy `write_store` made is then resharded, which must place
    every record and change no byte. Nothing is written to `root`.
    """
    report = WriteReport()
    by_family = scratch / "families"
    by_store = scratch / "store"
    for family in FAMILIES:
        for file in family.files(root):
            name = file.relative_to(root)
            try:
                rows = read_rows(file)
                _write_again(family, rows, by_family, by_store, name)
            except StoreError as error:
                report.problems.append(f"{name.as_posix()} could not be written again: {error}")
                continue
            if _same_bytes(file, by_family / name):
                report.family_files += 1
                report.family_lines += len(rows)
            else:
                report.problems.append(f"{name.as_posix()} is not what its family writer writes")
            if family not in (ENTITIES, EVIDENCE, PROVENANCE):
                continue
            if _same_bytes(file, by_store / name):
                report.store_files += 1
            else:
                report.problems.append(f"{name.as_posix()} is not what write_store writes")

    report.resharded_records = reshard_store(by_store)
    committed = {file.relative_to(root) for file in [*record_files(root), *provenance_files(root)]}
    held = {file.relative_to(by_store) for file in [*record_files(by_store), *provenance_files(by_store)]}
    for name in sorted(committed | held):
        if name in committed and name in held and _same_bytes(root / name, by_store / name):
            report.resharded_files += 1
        else:
            report.problems.append(f"{name.as_posix()} is not the same file after a reshard")
    return report


def verify_store(root: Path, release_id: str, scratch: Path) -> StoreReport:
    """Run the whole gate over a checkout. `scratch` is an empty folder the writers fill."""
    families = [verify_family(family, root) for family in FAMILIES]
    digests = verify_digests(root, release_id)
    written = verify_writers(root, scratch)
    return StoreReport(families, digests, written, records=sum(1 for _ in checked_records(root)))


def read_expectation(path: Path) -> Expectation:
    """The pinned expectation committed beside the gate."""
    pinned = _read_json(path)
    counts: dict[str, int] = {}
    for name, value in _object(pinned.get("counts"), f"{path} counts").items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise StoreError(f"{path} counts.{name} is not a whole number")
        counts[name] = value
    return Expectation(
        store_ref=_string(pinned.get("store_ref"), f"{path} store_ref"),
        release_receipt=_string(pinned.get("release_receipt"), f"{path} release_receipt"),
        counts=counts,
    )


def compare_counts(measured: dict[str, int], expected: dict[str, int]) -> list[str]:
    """Every count that is not exactly the pinned one, including one that is not pinned."""
    problems: list[str] = []
    for name in sorted({*measured, *expected}):
        if name not in expected:
            problems.append(f"{name} was measured as {measured[name]:,} and is not pinned")
        elif name not in measured:
            problems.append(f"{name} is pinned at {expected[name]:,} and was not measured")
        elif measured[name] != expected[name]:
            problems.append(f"{name} was measured as {measured[name]:,} against a pin of {expected[name]:,}")
    return problems


def summary(report: StoreReport) -> str:
    """The report as the lines the gate prints."""
    lines = [f"{'family':20} {'files':>5} {'lines':>8} {'placed':>8}  re-emitted"]
    for family in report.families:
        verdict = "byte for byte" if not family.problems else f"{len(family.problems)} problems"
        lines.append(f"{family.name:20} {family.files:>5} {family.lines:>8,} {family.placed:>8,}  {verdict}")
    counts = report.counts()
    lines.append("")
    lines.append(f"sharded lines in the shard their ID names: {counts['sharded_lines_placed']:,}")
    lines.append(f"records matching their provenance digest:  {report.records:,}")
    lines.append(
        f"logical digests equal to release {report.digests.release_id}: "
        f"{report.digests.matched} of {report.digests.digests}"
    )
    written = report.written
    lines.append(
        f"files the family writers reproduce on disk: {written.family_files} ({written.family_lines:,} lines)"
    )
    lines.append(f"record and provenance files write_store reproduces: {written.store_files}")
    lines.append(
        f"reshard of that copy: {written.resharded_records:,} records placed, "
        f"{written.resharded_files} files unchanged"
    )
    return "\n".join(lines)


def to_json(report: StoreReport, expectation: Expectation) -> str:
    """The measured counts and problems, for a CI summary or a later comparison."""
    return json.dumps(
        {
            "store_ref": expectation.store_ref,
            "release_receipt": report.digests.release_id,
            "counts": report.counts(),
            "problems": report.problems(),
        },
        indent=2,
    )
