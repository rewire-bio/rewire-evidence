"""Where records live on disk, ported from the layout half of `scripts/omics/records.ts`.

Entity kinds are one file per kind under `data/entities`. Evidence kinds (evaluations,
results, claims) and the provenance log are sharded: a record is in
`data/evidence/<kind>s/<shard>.jsonl` and its provenance line is in
`data/provenance/records/<shard>.jsonl`, where the shard is the first hex digit of the
SHA-256 of its ID. Every file is sorted by ID, compared as JavaScript compares strings.

This module places, reads, writes and digests lines. It does not know what a valid record
is: the schema, the vocabularies and the validators are later stages. `load_store` is
therefore the structural half of `loadRecords`, and says so.

It does know the kinds, because a kind becomes a file name. The TypeScript builds the path
from whatever kind it is given and relies on `recordSchema.parse` having run first. Until
the schema is ported nothing runs first here, so a kind outside `KINDS` is refused before
it can name a file.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from rewire_evidence.canonical import JsonObject, js_stringify, sha256_hex, shard_of, utf16_key
from rewire_evidence.jsonl import (
    StoreError,
    join_lines,
    parse_row,
    read_lines,
    read_rows,
    serialise_row,
    text_field,
    write_text,
)

#: Every kind a record may have: `entityKinds` in `shared/omics/entity-kinds.ts`, in its order.
KINDS = (
    "model",
    "method",
    "configuration",
    "pipeline",
    "service",
    "benchmark",
    "task",
    "protocol",
    "evaluator",
    "dataset",
    "dataset_subset",
    "baseline",
    "evaluation",
    "result",
    "source",
    "claim",
    "use_case",
)
EVIDENCE_KINDS = ("evaluation", "result", "claim")
#: Kinds whose records are split across shard files.
SHARDED_KINDS = EVIDENCE_KINDS
#: Folder of the sharded provenance log.
PROVENANCE_DIR = "data/provenance/records"
#: The provenance log as one logical file: the name release receipts give its digest.
PROVENANCE_FILE = "data/provenance/records.jsonl"
#: Path parts of a sharded record file under the root: data, evidence, <kind>s, <shard>.jsonl.
_SHARDED_PARTS = 4


@dataclass(frozen=True)
class FileDigest:
    """One logical store file and the digest of its lines in ID order."""

    file: str
    sha256: str


def _kind_path(kind: str, root: Path) -> Path:
    """A kind's store file or, for a sharded kind, its folder of shards."""
    if kind not in KINDS:
        raise StoreError(f"{kind!r} is not a record kind")
    folder = "evidence" if kind in EVIDENCE_KINDS else "entities"
    name = f"{kind.replace('_', '-')}s"
    return root / "data" / folder / (name if kind in SHARDED_KINDS else f"{name}.jsonl")


def record_file(kind: str, record_id: str, root: Path) -> Path:
    """The file a record belongs in."""
    base = _kind_path(kind, root)
    return base / f"{shard_of(record_id)}.jsonl" if kind in SHARDED_KINDS else base


def _by_name(paths: Iterable[Path]) -> list[Path]:
    return sorted(paths, key=lambda path: utf16_key(path.name))


def _jsonl_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return _by_name(path for path in folder.iterdir() if path.name.endswith(".jsonl"))


def kind_files(kind: str, root: Path) -> list[Path]:
    """Every store file of one kind."""
    base = _kind_path(kind, root)
    if kind in SHARDED_KINDS:
        return _jsonl_in(base)
    return [base] if base.exists() else []


def record_files(root: Path) -> list[Path]:
    """Every store file: the entity files, then each evidence kind's shards."""
    files: list[Path] = []
    for folder in ("entities", "evidence"):
        directory = root / "data" / folder
        if not directory.is_dir():
            continue
        for entry in _by_name(directory.iterdir()):
            if entry.is_dir():
                files.extend(_jsonl_in(entry))
            elif entry.name.endswith(".jsonl"):
                files.append(entry)
    return files


def provenance_file_for(record_id: str, root: Path) -> Path:
    """The provenance shard a record's line belongs in."""
    return root / PROVENANCE_DIR / f"{shard_of(record_id)}.jsonl"


def provenance_files(root: Path) -> list[Path]:
    """Every provenance shard."""
    return _jsonl_in(root / PROVENANCE_DIR)


def _by_id(row: JsonObject) -> bytes:
    """The sort key of a row whose ID has already been read."""
    return utf16_key(text_field(row, "id", "a row that was read"))


def _located(record: object, root: Path, where: str) -> tuple[Path, str, str]:
    """The file a record belongs in, with its ID and kind. `where` names it in any refusal."""
    record_id = text_field(record, "id", where)
    kind = text_field(record, "kind", where)
    if kind not in KINDS:
        raise StoreError(f"{where} ({record_id}) has the kind {kind!r}, which is not a record kind")
    return record_file(kind, record_id, root), record_id, kind


def read_kind(kind: str, root: Path) -> list[JsonObject]:
    """Every stored record of one kind, across its shards, sorted by ID. Not validated."""
    keyed: list[tuple[bytes, JsonObject]] = []
    for file in kind_files(kind, root):
        for number, row in enumerate(read_rows(file), 1):
            keyed.append((utf16_key(text_field(row, "id", f"{file}:{number}")), row))
    keyed.sort(key=lambda pair: pair[0])
    return [row for _, row in keyed]


#: A row on its way to a file: the file, the row's ID, what to call it in a refusal, the row.
type _Entry = tuple[Path, str, str, object]


def _planned(entries: Iterable[_Entry]) -> dict[Path, str]:
    """The whole body of every file these rows belong in, sorted by ID, with nothing written.

    Two rows with one ID are refused: the files hold one row per ID, and a file that held
    two would fail `load_store`. A row that cannot be serialised is refused here too, so
    everything that can be wrong with the rows is found before the first file is touched.
    """
    seen: dict[bytes, str] = {}
    by_file: dict[Path, list[tuple[bytes, str]]] = {}
    for file, row_id, where, row in entries:
        key = utf16_key(row_id)
        if key in seen:
            raise StoreError(f"{seen[key]} and {where} share the ID {row_id}")
        seen[key] = where
        line = serialise_row(js_stringify, row, f"{where} ({row_id})")
        by_file.setdefault(file, []).append((key, line))
    return {
        file: join_lines(line for _, line in sorted(lines, key=lambda pair: pair[0]))
        for file, lines in by_file.items()
    }


def _record_entries(records: Iterable[object], root: Path, kind: str | None = None) -> Iterator[_Entry]:
    for number, record in enumerate(records, 1):
        where = f"record {number}"
        file, record_id, held = _located(record, root, where)
        if kind is not None and held != kind:
            raise StoreError(f"{where} ({record_id}) is a {held}, not a {kind}")
        yield file, record_id, where, record


def _provenance_entries(rows: Iterable[object], root: Path) -> Iterator[_Entry]:
    for number, row in enumerate(rows, 1):
        where = f"provenance row {number}"
        record_id = text_field(row, "id", where)
        yield provenance_file_for(record_id, root), record_id, where, row


def _write(bodies: Mapping[Path, str]) -> None:
    for file, body in bodies.items():
        write_text(file, body)


def write_kind(kind: str, records: Iterable[JsonObject], root: Path) -> None:
    """Write every record of one kind to the files the layout assigns them.

    As in the TypeScript, a file that receives no record is left as it was, so this is for
    writing a kind whole, not for removing records from it. Every record is checked before
    any file is written.
    """
    _write(_planned(_record_entries(records, root, kind)))


def read_provenance(root: Path) -> dict[str, JsonObject]:
    """Every provenance row by record ID, refusing a row that is in the wrong shard."""
    rows: dict[str, JsonObject] = {}
    for file in provenance_files(root):
        for number, row in enumerate(read_rows(file), 1):
            record_id = text_field(row, "id", f"{file}:{number}")
            if provenance_file_for(record_id, root) != file:
                raise StoreError(f"Provenance for {record_id} is in the wrong file: {file}")
            rows[record_id] = row
    return rows


def write_store(records: Iterable[JsonObject], provenance: Iterable[JsonObject], root: Path) -> None:
    """Write records and their provenance to the files the layout assigns them.

    Every row is placed, checked and serialised before any file is written, so a row that
    is refused leaves the disk as it was. The files are then written one after another. A
    run interrupted between two of them is not undone here: the TypeScript snapshots the
    store and restores it in its write path, which is a later stage.
    """
    bodies = _planned(_record_entries(records, root))
    bodies.update(_planned(_provenance_entries(provenance, root)))
    _write(bodies)


def _lines_by_id(files: Iterable[Path]) -> list[str]:
    """The files' lines in ID order. The lines are sorted, never parsed and written again."""
    keyed: list[tuple[bytes, str]] = []
    for file in files:
        for number, line in enumerate(read_lines(file), 1):
            where = f"{file}:{number}"
            keyed.append((utf16_key(text_field(parse_row(line, where), "id", where)), line))
    keyed.sort(key=lambda pair: pair[0])
    return [line for _, line in keyed]


def logical_digest(files: Iterable[Path]) -> str:
    """SHA-256 of the files' lines in ID order: the bytes they would have as one file."""
    return sha256_hex(join_lines(_lines_by_id(files)))


def store_digests(root: Path) -> list[FileDigest]:
    """Digests of the store as one file per kind plus one provenance file.

    A sharded kind's digest is that of its lines in ID order, which is the file it was
    before it was split, so release receipts and release IDs do not depend on the shard
    layout.
    """
    logical: dict[str, list[Path]] = {}
    for file in record_files(root):
        parts = file.relative_to(root).parts
        # data/evidence/results/0.jsonl is part of data/evidence/results.jsonl.
        name = "/".join(parts[:3]) + ".jsonl" if len(parts) == _SHARDED_PARTS else "/".join(parts)
        logical.setdefault(name, []).append(file)
    digests = [FileDigest(name, logical_digest(logical[name])) for name in sorted(logical, key=utf16_key)]
    return [*digests, FileDigest(PROVENANCE_FILE, logical_digest(provenance_files(root)))]


def reshard_store(root: Path) -> int:
    """Move every record and provenance line to the file the layout assigns it.

    Each line keeps its bytes, and store files the layout no longer uses are deleted. Run
    after changing the sharded kinds or the shard width, or to split an unsharded store.
    Returns the number of records placed.
    """
    legacy = root / PROVENANCE_FILE
    old_provenance = [*provenance_files(root), *([legacy] if legacy.exists() else [])]
    old_records = record_files(root)

    by_file: dict[Path, list[tuple[bytes, str]]] = {}
    placed = 0
    for file in old_records:
        for number, line in enumerate(read_lines(file), 1):
            where = f"{file}:{number}"
            target, record_id, _ = _located(parse_row(line, where), root, where)
            by_file.setdefault(target, []).append((utf16_key(record_id), line))
            placed += 1
    for file in old_provenance:
        for number, line in enumerate(read_lines(file), 1):
            where = f"{file}:{number}"
            record_id = text_field(parse_row(line, where), "id", where)
            by_file.setdefault(provenance_file_for(record_id, root), []).append((utf16_key(record_id), line))

    for file in [*old_records, *old_provenance]:
        if file not in by_file:
            file.unlink()
    for file, lines in by_file.items():
        lines.sort(key=lambda pair: pair[0])
        write_text(file, join_lines(line for _, line in lines))
    for folder in {file.parent for file in old_records}:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    return placed


@dataclass(frozen=True)
class RecordCheck:
    """The checks `loadRecords` makes of one record: its file, its order, its provenance row."""

    root: Path
    #: Every provenance row by record ID, as `read_provenance` gives them.
    provenance: Mapping[str, JsonObject]

    def check(self, record: JsonObject, where: str, file: Path, previous: bytes) -> bytes:
        """Hold one record against the layout and its provenance row. Returns its sort key.

        `previous` is the sort key of the record before it in the file, or empty bytes for
        the first. Raises `StoreError` naming the record for a wrong file, an ID that does
        not sort after the one before it, a missing provenance row, or a digest that is
        not the row's.
        """
        target, record_id, kind = _located(record, self.root, where)
        if target != file:
            raise StoreError(f"{record_id} ({kind}) is in the wrong file: {file}")
        key = utf16_key(record_id)
        if key <= previous:
            raise StoreError(f"{file} is not sorted by unique ID at {record_id}")
        row = self.provenance.get(record_id)
        if row is None:
            raise StoreError(f"{record_id} has no provenance entry in {PROVENANCE_DIR}")
        if row.get("sha256") != sha256_hex(serialise_row(js_stringify, record, f"{where} ({record_id})")):
            raise StoreError(
                f"{record_id} changed without a provenance entry. Record the change in {PROVENANCE_DIR}."
            )
        return key


def checked_records(root: Path) -> Iterator[JsonObject]:
    """Every record in file order, checked for file placement, sort order and provenance.

    This is `loadRecords` without the schema and the validators, which are not ported yet.
    A record is hashed as the line was read, so until the schema lands this cannot notice a
    record whose top-level keys are out of schema order if its digest was taken the same
    way. The last check, that no provenance row is left over, runs when the records run
    out, so the iterator has to be read to its end for the store to have passed.

    It stops at the first fault, as `loadRecords` does. `parity.verify_records` makes the
    same checks and reports every fault.
    """
    held = RecordCheck(root, read_provenance(root))
    count = 0
    for file in record_files(root):
        previous = b""
        for number, record in enumerate(read_rows(file), 1):
            previous = held.check(record, f"{file}:{number}", file, previous)
            count += 1
            yield record
    if len(held.provenance) != count:
        raise StoreError(f"{PROVENANCE_DIR} lists {len(held.provenance)} records; the store holds {count}")


def load_store(root: Path) -> list[JsonObject]:
    """Every record, sorted by ID, having passed every check `checked_records` makes."""
    return sorted(checked_records(root), key=_by_id)
