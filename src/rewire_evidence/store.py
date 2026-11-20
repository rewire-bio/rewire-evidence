"""Where records live on disk, ported from the layout half of `scripts/omics/records.ts`.

Entity kinds are one file per kind under `data/entities`. Evidence kinds (evaluations,
results, claims) and the provenance log are sharded: a record is in
`data/evidence/<kind>s/<shard>.jsonl` and its provenance line is in
`data/provenance/records/<shard>.jsonl`, where the shard is the first hex digit of the
SHA-256 of its ID. Every file is sorted by ID, compared as JavaScript compares strings.

This module places, reads, writes and digests lines. It does not know what a valid record
is: the schema, the vocabularies and the validators are later stages. `load_store` is
therefore the structural half of `loadRecords`, and says so.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from rewire_evidence.canonical import JsonObject, js_stringify, record_sha256, sha256_hex, shard_of, utf16_key
from rewire_evidence.jsonl import (
    StoreError,
    join_lines,
    parse_row,
    read_lines,
    read_rows,
    text_field,
    write_text,
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


def _id_of(row: JsonObject, where: str) -> str:
    return text_field(row, "id", where)


def _sorted_by_id(rows: Iterable[JsonObject]) -> list[JsonObject]:
    return sorted(rows, key=lambda row: utf16_key(_id_of(row, "a row")))


def read_kind(kind: str, root: Path) -> list[JsonObject]:
    """Every stored record of one kind, across its shards, sorted by ID. Not validated."""
    return _sorted_by_id(row for file in kind_files(kind, root) for row in read_rows(file))


def _write_grouped(entries: Iterable[tuple[Path, JsonObject]]) -> None:
    by_file: dict[Path, list[JsonObject]] = {}
    for file, row in entries:
        by_file.setdefault(file, []).append(row)
    for file, rows in by_file.items():
        write_text(file, join_lines(js_stringify(row) for row in _sorted_by_id(rows)))


def _placed(record: JsonObject, root: Path) -> Path:
    where = "a record"
    return record_file(text_field(record, "kind", where), _id_of(record, where), root)


def write_kind(kind: str, records: Iterable[JsonObject], root: Path) -> None:
    """Write every record of one kind to the files the layout assigns them.

    As in the TypeScript, a file that receives no record is left as it was, so this is for
    writing a kind whole, not for removing records from it.
    """
    entries: list[tuple[Path, JsonObject]] = []
    for record in records:
        if record.get("kind") != kind:
            raise StoreError(f"{record.get('id')} is a {record.get('kind')}, not a {kind}")
        entries.append((_placed(record, root), record))
    _write_grouped(entries)


def read_provenance(root: Path) -> dict[str, JsonObject]:
    """Every provenance row by record ID, refusing a row that is in the wrong shard."""
    rows: dict[str, JsonObject] = {}
    for file in provenance_files(root):
        for number, row in enumerate(read_rows(file), 1):
            record_id = _id_of(row, f"{file}:{number}")
            if provenance_file_for(record_id, root) != file:
                raise StoreError(f"Provenance for {record_id} is in the wrong file: {file}")
            rows[record_id] = row
    return rows


def write_store(records: Iterable[JsonObject], provenance: Iterable[JsonObject], root: Path) -> None:
    """Write records and their provenance to the files the layout assigns them."""
    _write_grouped((_placed(record, root), record) for record in records)
    _write_grouped((provenance_file_for(_id_of(row, "a provenance row"), root), row) for row in provenance)


def _lines_by_id(files: Iterable[Path]) -> list[str]:
    """The files' lines in ID order. The lines are sorted, never parsed and written again."""
    keyed: list[tuple[bytes, str]] = []
    for file in files:
        for number, line in enumerate(read_lines(file), 1):
            where = f"{file}:{number}"
            keyed.append((utf16_key(_id_of(parse_row(line, where), where)), line))
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
            row = parse_row(line, where)
            record_id = _id_of(row, where)
            target = record_file(text_field(row, "kind", where), record_id, root)
            by_file.setdefault(target, []).append((utf16_key(record_id), line))
            placed += 1
    for file in old_provenance:
        for number, line in enumerate(read_lines(file), 1):
            where = f"{file}:{number}"
            record_id = _id_of(parse_row(line, where), where)
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


def checked_records(root: Path) -> Iterator[JsonObject]:
    """Every record in file order, checked for file placement, sort order and provenance.

    This is `loadRecords` without the schema and the validators, which are not ported yet.
    A record is hashed as the line was read, so until the schema lands this cannot notice a
    record whose top-level keys are out of schema order if its digest was taken the same
    way. The last check, that no provenance row is left over, runs when the records run
    out, so the iterator has to be read to its end for the store to have passed.
    """
    provenance = read_provenance(root)
    count = 0
    for file in record_files(root):
        previous = b""
        for number, record in enumerate(read_rows(file), 1):
            where = f"{file}:{number}"
            record_id = _id_of(record, where)
            kind = text_field(record, "kind", where)
            if record_file(kind, record_id, root) != file:
                raise StoreError(f"{record_id} ({kind}) is in the wrong file: {file}")
            key = utf16_key(record_id)
            if key <= previous:
                raise StoreError(f"{file} is not sorted by unique ID at {record_id}")
            previous = key
            row = provenance.get(record_id)
            if row is None:
                raise StoreError(f"{record_id} has no provenance entry in {PROVENANCE_DIR}")
            if row.get("sha256") != record_sha256(record):
                raise StoreError(
                    f"{record_id} changed without a provenance entry. Record the change in {PROVENANCE_DIR}."
                )
            count += 1
            yield record
    if len(provenance) != count:
        raise StoreError(f"{PROVENANCE_DIR} lists {len(provenance)} records; the store holds {count}")


def load_store(root: Path) -> list[JsonObject]:
    """Every record, sorted by ID, having passed every check `checked_records` makes."""
    return _sorted_by_id(checked_records(root))
