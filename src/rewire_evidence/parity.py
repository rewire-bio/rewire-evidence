"""The gates: does this library write the files the store already holds?

`verify_record_digests` is the first gate and the narrowest: every record hashed, against
the digest the store committed for it. `verify_store` is the store-layout gate, which asks
six questions, each answered over a whole checkout of `rewire-benchmark-data`:

1. Does every file of every family come back byte for byte when it is read and written
   again? The comparison is of whole files, so it covers the serialisation, the order of
   the rows, the final newline and the absence of blank lines in one assertion.
2. Is every row in the file the layout assigns it, and is every uniquely keyed file
   strictly sorted?
3. Do the logical store digests equal the ones a named release receipt recorded, and is
   that receipt the newest one?
4. Is every record in its file, in order, with a provenance row holding its digest? Every
   record that is not is named, and the run goes on to the rest.
5. Do the functions that write to disk produce those files? Every committed file is read
   and written into an empty folder by the writer a caller would use, the bytes are
   compared, and the copy is then resharded, which has to move nothing. A second copy is
   made as the store was before it was sharded, one file per kind with its lines out of
   order, and resharded, which has to produce the committed files.
6. Are these the bytes the expectation was measured on? Questions 1 to 5 compare the
   checkout with itself, so a row edited into another valid row passes all of them unless
   a digest elsewhere covers it. Each family's files are therefore hashed, path and bytes,
   and `compare_content` holds those digests against the pinned ones. A file that sits
   among a family's files and belongs to none is reported too, since nothing else reads it.

A count is reported for everything read, and `compare_counts` holds those counts against a
committed expectation, so a checkout that held fewer rows cannot pass while proving less.

A disagreement is a problem in the report. `StoreError` is raised only when a run cannot
be made at all: no release receipt, no expectation, no checkout.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rewire_evidence.canonical import JsonObject, JsonValue, js_parse, js_stringify, sha256_hex
from rewire_evidence.families import (
    ENTITIES,
    EVIDENCE,
    FAMILIES,
    PROVENANCE,
    Family,
    append_rows,
    serialise_rows,
    stray_files,
    write_family,
)
from rewire_evidence.jsonl import (
    StoreError,
    append_text,
    join_lines,
    parse_row,
    read_lines,
    read_rows,
    serialise_row,
    text_field,
)
from rewire_evidence.store import (
    PROVENANCE_DIR,
    PROVENANCE_FILE,
    RecordCheck,
    provenance_files,
    read_provenance,
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
    #: SHA-256 over the family's files in order: each one's path, length and bytes.
    sha256: str = ""
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
    #: Records it placed from an unsharded copy with its lines out of order, and the files
    #: that came out as the committed ones.
    split_records: int = 0
    split_files: int = 0
    problems: list[str] = field(default_factory=list[str])


@dataclass
class RecordReport:
    """Every record held against the layout and its provenance row."""

    #: Records whose placement, order and provenance digest all hold.
    matched: int = 0
    problems: list[str] = field(default_factory=list[str])


@dataclass
class RecordDigests:
    """The first gate: every record's digest against the one the store committed."""

    provenance_rows: int = 0
    matched: int = 0
    #: Records the provenance log has no row for.
    unlisted: int = 0
    mismatched: int = 0
    #: The first few mismatches, each as the lines the gate prints.
    shown: list[str] = field(default_factory=list[str])


@dataclass
class StoreReport:
    """Everything the gate measured."""

    families: list[FamilyReport]
    digests: DigestReport
    written: WriteReport
    records: RecordReport
    #: Files among a family's files that belong to no family.
    strays: list[str] = field(default_factory=list[str])

    def problems(self) -> list[str]:
        """Every disagreement found, family by family and then check by check."""
        found = [f"{report.name}: {problem}" for report in self.families for problem in report.problems]
        found.extend(f"stray: {name} sits among a family's files and belongs to none" for name in self.strays)
        found.extend(f"digests: {problem}" for problem in self.digests.problems)
        found.extend(f"records: {problem}" for problem in self.records.problems)
        return [*found, *(f"writers: {problem}" for problem in self.written.problems)]

    def content(self) -> dict[str, str]:
        """The digest of each family's files, which a pinned expectation is compared with."""
        return {report.name: report.sha256 for report in self.families}

    def counts(self) -> dict[str, int]:
        """The figures a pinned expectation is compared with."""
        counts: dict[str, int] = {}
        for report in self.families:
            counts[f"{report.name}.files"] = report.files
            counts[f"{report.name}.lines"] = report.lines
            counts[f"{report.name}.placed"] = report.placed
        by_name = {report.name: report for report in self.families}
        counts["sharded_lines_placed"] = by_name[EVIDENCE.name].placed + by_name[PROVENANCE.name].placed
        counts["records_matching_provenance"] = self.records.matched
        counts["logical_digests"] = self.digests.digests
        counts["logical_digests_matched"] = self.digests.matched
        counts["written.family_files"] = self.written.family_files
        counts["written.family_lines"] = self.written.family_lines
        counts["written.store_files"] = self.written.store_files
        counts["written.resharded_records"] = self.written.resharded_records
        counts["written.resharded_files"] = self.written.resharded_files
        counts["written.split_records"] = self.written.split_records
        counts["written.split_files"] = self.written.split_files
        return counts


@dataclass(frozen=True)
class Expectation:
    """The pinned result: which store commit, which release receipt, every count and digest."""

    store_ref: str
    release_receipt: str
    counts: dict[str, int]
    #: The digest of each family's files at `store_ref`, by family name.
    content: dict[str, str]


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
    """Read every file of a family, write it again in memory and compare the bytes.

    The files are hashed as they are read, path and length and bytes, so the report also
    says which bytes were checked and not only that they agree with themselves.
    """
    report = FamilyReport(family.name)
    content = hashlib.sha256()
    for file in family.files(root):
        report.files += 1
        name = file.relative_to(root).as_posix()
        data = file.read_bytes()
        content.update(f"{name}\0{len(data)}\0".encode("utf-8", "surrogateescape"))
        content.update(data)
        try:
            report.lines += _verify_file(family, root, file, data, report)
        except StoreError as error:
            report.problems.append(str(error))
    report.sha256 = content.hexdigest()
    return report


def _verify_file(family: Family, root: Path, file: Path, data: bytes, report: FamilyReport) -> int:
    """Check one file, adding what is wrong to the report. Returns the rows it holds."""
    name = file.relative_to(root).as_posix()
    try:
        committed = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StoreError(f"{name} is not UTF-8: {error}") from error
    lines = [line for line in committed.split("\n") if line]
    rows = [parse_row(line, f"{name}:{number}") for number, line in enumerate(lines, 1)]

    emitted = serialise_rows(family, rows, f"{name}:")
    if emitted != committed:
        report.problems.append(
            f"{name} does not re-emit byte for byte, {first_difference(committed, emitted)}"
        )
    if family.target is not None:
        for number, row in enumerate(rows, 1):
            try:
                placed = family.target(row, root, f"{name}:{number}") == file
            except StoreError as error:
                report.problems.append(str(error))
                continue
            if placed:
                report.placed += 1
            else:
                report.problems.append(f"{name}:{number} is in the wrong file")
    if family.order is not None and family.unique:
        keys = [family.order(row, f"{name}:{number}") for number, row in enumerate(rows, 1)]
        for number in range(1, len(keys)):
            if keys[number] <= keys[number - 1]:
                report.problems.append(f"{name}:{number + 1} is not sorted by unique key")
    return len(rows)


def verify_records(root: Path) -> RecordReport:
    """Hold every record against the layout and its provenance row, naming each that fails.

    These are the checks `store.checked_records` makes, which stops at the first fault as
    `loadRecords` does. A gate has to finish: one changed record is a disagreement to list
    beside everything else that was measured, not a run that could not be made.
    """
    report = RecordReport()
    try:
        store = RecordCheck(root, read_provenance(root))
    except StoreError as error:
        report.problems.append(str(error))
        return report
    held = 0
    for file in record_files(root):
        try:
            rows = read_rows(file)
        except StoreError as error:
            report.problems.append(str(error))
            continue
        previous = b""
        for number, record in enumerate(rows, 1):
            held += 1
            try:
                previous = store.check(record, f"{file}:{number}", file, previous)
            except StoreError as error:
                report.problems.append(str(error))
            else:
                report.matched += 1
    if len(store.provenance) != held:
        report.problems.append(
            f"{PROVENANCE_DIR} lists {len(store.provenance)} records; the store holds {held}"
        )
    return report


def _committed_digests(root: Path) -> dict[str, JsonValue]:
    """The digest the provenance log holds for every record, by record ID."""
    folder = root / PROVENANCE_DIR
    if not folder.is_dir():
        raise StoreError(f"{folder} is missing, so {root} is not a benchmark-data checkout")
    committed: dict[str, JsonValue] = {}
    for file in provenance_files(root):
        for number, row in enumerate(read_rows(file), 1):
            committed[text_field(row, "id", f"{file}:{number}")] = row.get("sha256")
    if not committed:
        raise StoreError(f"{folder} holds no provenance rows")
    return committed


def verify_record_digests(root: Path, shown: int = 5) -> RecordDigests:
    """Recompute `sha256(JSON.stringify(record))` for every record against the committed digest.

    Reads the provenance log and the record files and nothing else, and checks nothing but
    the digest, so it says whether the serialisation is right whatever else is wrong. Raises
    `StoreError` when the root is not a store at all, which is a run that could not be made.
    """
    committed = _committed_digests(root)
    files = record_files(root)
    if not files:
        raise StoreError(f"{root / 'data'} holds no record files")

    report = RecordDigests(provenance_rows=len(committed))
    for file in files:
        for number, record in enumerate(read_rows(file), 1):
            record_id = record.get("id")
            if not isinstance(record_id, str) or record_id not in committed:
                report.unlisted += 1
                continue
            try:
                computed = sha256_hex(serialise_row(js_stringify, record, f"{file}:{number}"))
            except StoreError as error:
                computed = str(error)
            if computed == committed[record_id]:
                report.matched += 1
                continue
            report.mismatched += 1
            if len(report.shown) < shown:
                report.shown.append(
                    f"  {file.name} {record_id}\n"
                    f"    committed {committed[record_id]}\n"
                    f"    computed  {computed}"
                )
    return report


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
    try:
        measured = store_digests(root)
    except StoreError as error:
        # A record file that cannot be read is a disagreement with the receipt, not a run
        # that could not be made: the receipt was found and the store does not match it.
        report.problems.append(f"the store's digests could not be taken: {error}")
        return report
    for digest in measured:
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


def _unsharded(family: Family, name: Path) -> Path:
    """The file a store file was part of before the store was sharded, relative to the root."""
    if family is PROVENANCE:
        return Path(PROVENANCE_FILE)
    # data/evidence/results/0.jsonl was part of data/evidence/results.jsonl.
    return name.parent.with_suffix(".jsonl") if family is EVIDENCE else name


def _same_layout(root: Path, copy: Path, what: str, problems: list[str]) -> int:
    """How many record and provenance files of a resharded copy are the committed ones."""
    committed = {file.relative_to(root) for file in [*record_files(root), *provenance_files(root)]}
    held = {file.relative_to(copy) for file in [*record_files(copy), *provenance_files(copy)]}
    same = 0
    for name in sorted(committed | held):
        if name in committed and name in held and _same_bytes(root / name, copy / name):
            same += 1
        else:
            problems.append(f"{name.as_posix()} is not the same file after {what}")
    return same


def verify_writers(root: Path, scratch: Path) -> WriteReport:
    """Write every committed file again with the library's writers and compare the bytes.

    `scratch` is an empty folder. Each file is read, and its rows are handed to the writer
    a caller would use: `write_family` for a sorted family, `append_rows` for an append-only
    one, and `write_store` for records and provenance, which is a separate implementation
    of the same layout. The copy `write_store` made is then resharded, which must place
    every record and change no byte.

    A reshard of a store already in its layout cannot show that `reshard_store` sorts or
    places anything, so a third copy holds the committed lines as the store was before it
    was sharded: one file per kind and one provenance file, each shard's lines in
    descending order. Resharding that has to produce the committed files.

    Nothing is written to `root`.
    """
    report = WriteReport()
    by_family = scratch / "families"
    by_store = scratch / "store"
    whole = scratch / "unsharded"
    for family in FAMILIES:
        for file in family.files(root):
            name = file.relative_to(root)
            try:
                rows = read_rows(file)
                _write_again(family, rows, by_family, by_store, name)
                if family in (ENTITIES, EVIDENCE, PROVENANCE):
                    backwards = reversed(read_lines(file))
                    append_text(whole / _unsharded(family, name), join_lines(backwards))
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
    report.resharded_files = _same_layout(root, by_store, "a reshard", report.problems)
    report.split_records = reshard_store(whole)
    report.split_files = _same_layout(root, whole, "a reshard of an unsharded copy", report.problems)
    return report


def verify_store(root: Path, release_id: str, scratch: Path) -> StoreReport:
    """Run the whole gate over a checkout. `scratch` is an empty folder the writers fill."""
    families = [verify_family(family, root) for family in FAMILIES]
    strays = [file.relative_to(root).as_posix() for file in stray_files(root)]
    digests = verify_digests(root, release_id)
    written = verify_writers(root, scratch)
    return StoreReport(families, digests, written, verify_records(root), strays)


def read_expectation(path: Path) -> Expectation:
    """The pinned expectation committed beside the gate."""
    pinned = _read_json(path)
    counts: dict[str, int] = {}
    for name, value in _object(pinned.get("counts"), f"{path} counts").items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise StoreError(f"{path} counts.{name} is not a whole number")
        counts[name] = value
    content = {
        name: _string(value, f"{path} content.{name}")
        for name, value in _object(pinned.get("content"), f"{path} content").items()
    }
    return Expectation(
        store_ref=_string(pinned.get("store_ref"), f"{path} store_ref"),
        release_receipt=_string(pinned.get("release_receipt"), f"{path} release_receipt"),
        counts=counts,
        content=content,
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


def compare_content(measured: dict[str, str], expectation: Expectation) -> list[str]:
    """Every family whose files are not the bytes the expectation was measured on."""
    problems: list[str] = []
    for name in sorted({*measured, *expectation.content}):
        if name not in expectation.content:
            problems.append(
                f"{name} holds files with digest {measured[name]}, and no digest is pinned for it"
            )
        elif name not in measured:
            problems.append(f"{name} has a pinned digest and was not measured")
        elif measured[name] != expectation.content[name]:
            problems.append(
                f"{name} holds files with digest {measured[name]}, not the {expectation.content[name]} "
                f"measured at {expectation.store_ref}: the checkout is another commit, or was edited"
            )
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
    lines.append(f"records matching their provenance digest:  {report.records.matched:,}")
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
    lines.append(
        f"reshard of an unsharded copy, lines out of order: {written.split_records:,} records placed, "
        f"{written.split_files} files as committed"
    )
    return "\n".join(lines)


def to_json(report: StoreReport, expectation: Expectation) -> str:
    """The measured counts and problems, for a CI summary or a later comparison."""
    return json.dumps(
        {
            "store_ref": expectation.store_ref,
            "release_receipt": report.digests.release_id,
            "counts": report.counts(),
            "content": report.content(),
            "problems": [
                *report.problems(),
                *compare_counts(report.counts(), expectation.counts),
                *compare_content(report.content(), expectation),
            ],
        },
        indent=2,
    )
