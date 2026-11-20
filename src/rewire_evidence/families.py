"""Every family of line file the store keeps, and how each one is written.

A family is a set of files that share four things: how a row is serialised, what order the
rows are in, which file a row belongs in, and how the file is replaced. The TypeScript
spreads these over six modules. They are gathered here because the mistakes worth guarding
against are mix-ups between families: sorting keys at every depth where only the top level
is sorted, sorting a file that is append-only, or placing a row by the wrong digest.

| Family             | Serialisation    | Order                                         |
| ------------------ | ---------------- | --------------------------------------------- |
| entities, evidence | `js_stringify`   | by `id`, unique                               |
| provenance         | `js_stringify`   | by `id`, unique                               |
| moved attributes   | `js_stringify`   | append-only                                   |
| manifest           | `canonical_line` | by `sha256`, unique                           |
| fetch receipts     | `canonical_line` | append-only                                   |
| licence receipts   | `canonical_line` | append-only                                   |
| licence candidates | `canonical_line` | by `source_id`                                |
| layer registry     | `canonical_line` | layer hash, object hash, path in container    |
| unsupported layers | `canonical_line` | object hash, path in container, reason        |

Nothing here validates a row against a schema. This is layout and bytes; the schemas are
later stages. Files that have no canonical form (the 2026-09-19 audit receipts, the search
ledger, the scope audit) were written by hand and by `json.dumps` in mixed formats, belong
to no family, and are never rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rewire_evidence.canonical import (
    JsonInput,
    JsonObject,
    canonical_line,
    js_stringify,
    object_shard,
    utf16_key,
)
from rewire_evidence.jsonl import (
    StoreError,
    append_text,
    join_lines,
    read_rows,
    text_field,
    write_text,
    write_text_atomically,
)
from rewire_evidence.store import provenance_file_for, provenance_files, record_file, record_files

type SortKey = tuple[bytes, ...]

MANIFEST_DIR = "data/archive/objects"
AUDIT_DIR = "data/omics/audits"
LICENCE_CANDIDATES = "data/archive/licence-candidates.jsonl"
LAYER_REGISTRY = "data/omics/layers/registry.jsonl"
LAYER_UNSUPPORTED = "data/omics/layers/unsupported.jsonl"
MOVED_ATTRIBUTES = "data/provenance/moved-attributes.jsonl"
OBJECT_SHARDS = "0123456789abcdef"

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_FETCH_RECEIPTS = "archive-fetch"
_LICENCE_RECEIPTS = "licence-resolve"


@dataclass(frozen=True)
class Family:
    """One family of store files."""

    name: str
    #: One row as its line, without the newline.
    serialise: Callable[[Mapping[str, JsonInput]], str]
    #: The family's files that exist under a root, in the order the TypeScript reads them.
    files: Callable[[Path], list[Path]]
    #: The sort key of a row, or None for an append-only family, whose order is its history.
    order: Callable[[JsonObject], SortKey] | None
    #: The file a row belongs in, or None where the writer is told the file.
    target: Callable[[JsonObject, Path], Path] | None
    #: Whether two rows may share a sort key.
    unique: bool = False
    #: Whether a file is replaced through a temporary file and a rename.
    atomic: bool = False


def _text(row: JsonObject, field: str) -> bytes:
    return utf16_key(text_field(row, field, "a row"))


def _container_path(row: JsonObject) -> bytes:
    """`container_path ?? ""`: a layer of the object itself sorts before its members."""
    path = row.get("container_path")
    if path is not None and not isinstance(path, str):
        raise StoreError("a row has a container_path that is neither a string nor null")
    return utf16_key(path or "")


def _existing(path: str) -> Callable[[Path], list[Path]]:
    return lambda root: [root / path] if (root / path).exists() else []


def _fixed(path: str) -> Callable[[JsonObject, Path], Path]:
    return lambda _row, root: root / path


def _record_target(row: JsonObject, root: Path) -> Path:
    where = "a record"
    return record_file(text_field(row, "kind", where), text_field(row, "id", where), root)


def _record_files(folder: str) -> Callable[[Path], list[Path]]:
    return lambda root: [file for file in record_files(root) if file.relative_to(root).parts[1] == folder]


def manifest_shard_path(shard: str, root: Path) -> Path:
    """The manifest file for one shard."""
    if len(shard) != 1 or shard not in OBJECT_SHARDS:
        raise StoreError(f"Not a manifest shard: {shard}")
    return root / MANIFEST_DIR / f"{shard}.jsonl"


def _manifest_files(root: Path) -> list[Path]:
    paths = [manifest_shard_path(shard, root) for shard in OBJECT_SHARDS]
    return [path for path in paths if path.exists()]


def _manifest_target(row: JsonObject, root: Path) -> Path:
    try:
        shard = object_shard(text_field(row, "sha256", "a manifest row"))
    except ValueError as error:
        raise StoreError(str(error)) from error
    return manifest_shard_path(shard, root)


def receipt_path(kind: str, date: str, root: Path) -> Path:
    """A dated receipts file under the audits folder."""
    if not _DATE.fullmatch(date):
        raise StoreError(f"Not a date: {date}")
    return root / AUDIT_DIR / f"{date}-{kind}.receipts.jsonl"


def _receipt_files(kind: str) -> Callable[[Path], list[Path]]:
    """Every receipts file of one kind, oldest first. A run crossing midnight writes two."""
    name = re.compile(rf"{_DATE.pattern}-{re.escape(kind)}\.receipts\.jsonl")

    def files(root: Path) -> list[Path]:
        folder = root / AUDIT_DIR
        if not folder.is_dir():
            return []
        found = [path for path in folder.iterdir() if name.fullmatch(path.name)]
        return sorted(found, key=lambda path: utf16_key(path.name))

    return files


def fetch_receipt_path(date: str, root: Path) -> Path:
    """`data/omics/audits/<date>-archive-fetch.receipts.jsonl`."""
    return receipt_path(_FETCH_RECEIPTS, date, root)


def licence_receipt_path(date: str, root: Path) -> Path:
    """`data/omics/audits/<date>-licence-resolve.receipts.jsonl`, dated by the run."""
    return receipt_path(_LICENCE_RECEIPTS, date, root)


def _fetch_receipt_target(row: JsonObject, root: Path) -> Path:
    return fetch_receipt_path(text_field(row, "fetched_at", "a fetch receipt")[:10], root)


ENTITIES = Family(
    name="entities",
    serialise=js_stringify,
    files=_record_files("entities"),
    order=lambda row: (_text(row, "id"),),
    target=_record_target,
    unique=True,
)
EVIDENCE = Family(
    name="evidence",
    serialise=js_stringify,
    files=_record_files("evidence"),
    order=lambda row: (_text(row, "id"),),
    target=_record_target,
    unique=True,
)
PROVENANCE = Family(
    name="provenance",
    serialise=js_stringify,
    files=provenance_files,
    order=lambda row: (_text(row, "id"),),
    target=lambda row, root: provenance_file_for(text_field(row, "id", "a provenance row"), root),
    unique=True,
)
MOVED = Family(
    name="moved_attributes",
    serialise=js_stringify,
    files=_existing(MOVED_ATTRIBUTES),
    order=None,
    target=_fixed(MOVED_ATTRIBUTES),
)
MANIFEST = Family(
    name="manifest",
    serialise=canonical_line,
    files=_manifest_files,
    order=lambda row: (_text(row, "sha256"),),
    target=_manifest_target,
    unique=True,
    atomic=True,
)
FETCH_RECEIPTS = Family(
    name="fetch_receipts",
    serialise=canonical_line,
    files=_receipt_files(_FETCH_RECEIPTS),
    order=None,
    target=_fetch_receipt_target,
)
LICENCE_RECEIPTS = Family(
    name="licence_receipts",
    serialise=canonical_line,
    files=_receipt_files(_LICENCE_RECEIPTS),
    order=None,
    target=None,
)
CANDIDATES = Family(
    name="licence_candidates",
    serialise=canonical_line,
    files=_existing(LICENCE_CANDIDATES),
    order=lambda row: (_text(row, "source_id"),),
    target=_fixed(LICENCE_CANDIDATES),
)
REGISTRY = Family(
    name="layer_registry",
    serialise=canonical_line,
    files=_existing(LAYER_REGISTRY),
    order=lambda row: (_text(row, "layer_sha256"), _text(row, "object_sha256"), _container_path(row)),
    target=_fixed(LAYER_REGISTRY),
)
UNSUPPORTED = Family(
    name="layers_unsupported",
    serialise=canonical_line,
    files=_existing(LAYER_UNSUPPORTED),
    order=lambda row: (_text(row, "object_sha256"), _container_path(row), _text(row, "reason")),
    target=_fixed(LAYER_UNSUPPORTED),
)

#: Every family, in the order the gate reports them.
FAMILIES = (
    ENTITIES,
    EVIDENCE,
    PROVENANCE,
    MOVED,
    MANIFEST,
    FETCH_RECEIPTS,
    LICENCE_RECEIPTS,
    CANDIDATES,
    REGISTRY,
    UNSUPPORTED,
)


def ordered(family: Family, rows: Iterable[JsonObject]) -> list[JsonObject]:
    """The rows in the family's order. An append-only family keeps the order it was given."""
    if family.order is None:
        return list(rows)
    return sorted(rows, key=family.order)


def serialise_rows(family: Family, rows: Iterable[JsonObject]) -> str:
    """A whole file body for these rows: ordered, one line each, newline after every line."""
    return join_lines(family.serialise(row) for row in ordered(family, rows))


def read_family(family: Family, root: Path) -> list[JsonObject]:
    """Every row of a family under a root, file by file, in file order."""
    return [row for file in family.files(root) for row in read_rows(file)]


def write_family(family: Family, rows: Iterable[JsonObject], root: Path) -> list[Path]:
    """Replace the files these rows belong in with exactly these rows. Returns the files.

    A file that receives no row is left alone, as the TypeScript writers leave it. An
    append-only family is refused: its files are history, and rewriting one would edit it.
    """
    target = family.target
    if family.order is None or target is None:
        raise StoreError(f"{family.name} is append-only; use append_rows")
    by_file: dict[Path, list[JsonObject]] = {}
    for row in rows:
        by_file.setdefault(target(row, root), []).append(row)
    write = write_text_atomically if family.atomic else write_text
    for file, held in by_file.items():
        write(file, serialise_rows(family, held))
    return list(by_file)


def append_rows(
    family: Family, rows: Iterable[JsonObject], root: Path, file: Path | None = None
) -> list[Path]:
    """Add rows to the end of an append-only family, in the order given. Returns the files.

    `file` names the file for a family that does not place its own rows, which is the
    licence receipts: they are dated by the run, not by anything a row holds.
    """
    if family.order is not None:
        raise StoreError(f"{family.name} is sorted; use write_family")
    target = family.target
    by_file: dict[Path, list[JsonObject]] = {}
    for row in rows:
        if file is not None:
            destination = file
        elif target is not None:
            destination = target(row, root)
        else:
            raise StoreError(f"{family.name} does not place its own rows; name the file")
        by_file.setdefault(destination, []).append(row)
    for destination, held in by_file.items():
        append_text(destination, serialise_rows(family, held))
    return list(by_file)
