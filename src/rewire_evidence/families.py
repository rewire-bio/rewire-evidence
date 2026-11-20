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
later stages. What a writer does check is what the layout itself needs: every row is an
object, has the fields it is placed and sorted by, can be serialised, and in a uniquely
keyed family shares its key with no other row. All of that is checked for every row before
any file is written, and a refusal names the row by its position in what was handed over.

Files that have no canonical form (the 2026-09-19 audit receipts, the search ledger, the
scope audit) were written by hand and by `json.dumps` in mixed formats, belong to no
family, and are never rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise
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
    serialise_row,
    text_field,
    write_text,
    write_text_atomically,
)
from rewire_evidence.store import provenance_file_for, provenance_files, record_file, record_files

type SortKey = tuple[bytes, ...]
#: A row with its sort key and what to call it in a refusal.
type _Keyed = tuple[SortKey, str, JsonObject]

#: How a row is named when it came from a caller and not from a file: "row 3".
ROWS = "row "

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
    #: The second argument names the row in a refusal.
    order: Callable[[JsonObject, str], SortKey] | None
    #: The file a row belongs in, or None where the writer is told the file. The last
    #: argument names the row in a refusal.
    target: Callable[[JsonObject, Path, str], Path] | None
    #: Whether two rows may share a sort key.
    unique: bool = False
    #: Whether a file is replaced through a temporary file and a rename.
    atomic: bool = False


def _text(row: JsonObject, field: str, where: str) -> bytes:
    return utf16_key(text_field(row, field, where))


def _by(field: str) -> Callable[[JsonObject, str], SortKey]:
    return lambda row, where: (_text(row, field, where),)


def _container_path(row: JsonObject, where: str) -> bytes:
    """`container_path ?? ""`: a layer of the object itself sorts before its members."""
    path = row.get("container_path")
    if path is not None and not isinstance(path, str):
        raise StoreError(f"{where} has a container_path that is neither a string nor null")
    return utf16_key(path or "")


def _existing(path: str) -> Callable[[Path], list[Path]]:
    return lambda root: [root / path] if (root / path).exists() else []


def _fixed(path: str) -> Callable[[JsonObject, Path, str], Path]:
    return lambda _row, root, _where: root / path


def _record_target(row: JsonObject, root: Path, where: str) -> Path:
    record_id = text_field(row, "id", where)
    try:
        return record_file(text_field(row, "kind", where), record_id, root)
    except StoreError as error:
        raise StoreError(f"{where} ({record_id}): {error}") from error


def _provenance_target(row: JsonObject, root: Path, where: str) -> Path:
    return provenance_file_for(text_field(row, "id", where), root)


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


def _manifest_target(row: JsonObject, root: Path, where: str) -> Path:
    try:
        shard = object_shard(text_field(row, "sha256", where))
    except ValueError as error:
        raise StoreError(f"{where}: {error}") from error
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


def _fetch_receipt_target(row: JsonObject, root: Path, where: str) -> Path:
    fetched_at = text_field(row, "fetched_at", where)
    if not _DATE.fullmatch(fetched_at[:10]):
        raise StoreError(f"{where} has a fetched_at that does not start with a date: {fetched_at}")
    return fetch_receipt_path(fetched_at[:10], root)


def stray_files(root: Path) -> list[Path]:
    """Files that sit among a family's files and that no family reads.

    The manifest is read shard by shard, so anything else in its folder is ignored, and a
    receipts file whose name is not exactly a date and a kind is ignored too. Nothing reads
    such a file, so nothing would notice what it held. A gate reports them for that reason.
    Other files in the audits folder are not receipts of these two kinds and are left alone.
    """
    strays: list[Path] = []
    manifest = root / MANIFEST_DIR
    if manifest.is_dir():
        shards = {f"{shard}.jsonl" for shard in OBJECT_SHARDS}
        strays.extend(path for path in manifest.iterdir() if path.name not in shards)
    audits = root / AUDIT_DIR
    if audits.is_dir():
        for kind in (_FETCH_RECEIPTS, _LICENCE_RECEIPTS):
            claimed = set(_receipt_files(kind)(root))
            strays.extend(
                path for path in audits.iterdir() if f"{kind}.receipts" in path.name and path not in claimed
            )
    return sorted(strays, key=lambda path: utf16_key(path.relative_to(root).as_posix()))


ENTITIES = Family(
    name="entities",
    serialise=js_stringify,
    files=_record_files("entities"),
    order=_by("id"),
    target=_record_target,
    unique=True,
)
EVIDENCE = Family(
    name="evidence",
    serialise=js_stringify,
    files=_record_files("evidence"),
    order=_by("id"),
    target=_record_target,
    unique=True,
)
PROVENANCE = Family(
    name="provenance",
    serialise=js_stringify,
    files=provenance_files,
    order=_by("id"),
    target=_provenance_target,
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
    order=_by("sha256"),
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
    order=_by("source_id"),
    target=_fixed(LICENCE_CANDIDATES),
)
REGISTRY = Family(
    name="layer_registry",
    serialise=canonical_line,
    files=_existing(LAYER_REGISTRY),
    order=lambda row, where: (
        _text(row, "layer_sha256", where),
        _text(row, "object_sha256", where),
        _container_path(row, where),
    ),
    target=_fixed(LAYER_REGISTRY),
)
UNSUPPORTED = Family(
    name="layers_unsupported",
    serialise=canonical_line,
    files=_existing(LAYER_UNSUPPORTED),
    order=lambda row, where: (
        _text(row, "object_sha256", where),
        _container_path(row, where),
        _text(row, "reason", where),
    ),
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


def _keyed(family: Family, rows: Iterable[JsonObject], source: str) -> list[_Keyed]:
    """The rows in the family's order, each with its key and its name. The sort is stable."""
    named = [(f"{source}{number}", row) for number, row in enumerate(rows, 1)]
    order = family.order
    if order is None:
        return [((), where, row) for where, row in named]
    return sorted(((order(row, where), where, row) for where, row in named), key=lambda entry: entry[0])


def _refuse_repeats(family: Family, keyed: list[_Keyed]) -> None:
    """Refuse two rows with one key in a family whose files hold one row per key."""
    if not family.unique:
        return
    for (key, first, _), (other, second, _) in pairwise(keyed):
        if key == other:
            shown = ", ".join(part.decode("utf-16-be", "surrogatepass") for part in key)
            raise StoreError(
                f"{first} and {second} share the key {shown}; {family.name} holds one row per key"
            )


def _body(family: Family, keyed: Iterable[_Keyed]) -> str:
    return join_lines(serialise_row(family.serialise, row, where) for _, where, row in keyed)


def ordered(family: Family, rows: Iterable[JsonObject], source: str = ROWS) -> list[JsonObject]:
    """The rows in the family's order. An append-only family keeps the order it was given.

    `source` names the rows in a refusal: row 3 is `f"{source}3"`, so a file passes its name
    and a colon, and a caller's list passes nothing and gets "row 3".
    """
    return [row for _, _, row in _keyed(family, rows, source)]


def serialise_rows(family: Family, rows: Iterable[JsonObject], source: str = ROWS) -> str:
    """A whole file body for these rows: ordered, one line each, newline after every line.

    Rows that share a key are written side by side in the order given, in any family. That
    is what a gate needs to compare a file with itself; `write_family` is what refuses them.
    """
    return _body(family, _keyed(family, rows, source))


def read_family(family: Family, root: Path) -> list[JsonObject]:
    """Every row of a family under a root, file by file, in file order."""
    return [row for file in family.files(root) for row in read_rows(file)]


def write_family(family: Family, rows: Iterable[JsonObject], root: Path) -> list[Path]:
    """Replace the files these rows belong in with exactly these rows. Returns the files.

    A file that receives no row is left alone, as the TypeScript writers leave it. An
    append-only family is refused: its files are history, and rewriting one would edit it.
    Every row is placed, ordered and serialised before any file is written, and two rows
    with one key are refused in a uniquely keyed family.
    """
    order, target = family.order, family.target
    if order is None or target is None:
        raise StoreError(f"{family.name} is append-only; use append_rows")
    by_file: dict[Path, list[_Keyed]] = {}
    for number, row in enumerate(rows, 1):
        where = f"{ROWS}{number}"
        key = order(row, where)
        by_file.setdefault(target(row, root, where), []).append((key, where, row))
    for held in by_file.values():
        held.sort(key=lambda entry: entry[0])
        _refuse_repeats(family, held)
    bodies = {file: _body(family, held) for file, held in by_file.items()}
    write = write_text_atomically if family.atomic else write_text
    for file, body in bodies.items():
        write(file, body)
    return list(bodies)


def append_rows(
    family: Family, rows: Iterable[JsonObject], root: Path, file: Path | None = None
) -> list[Path]:
    """Add rows to the end of an append-only family, in the order given. Returns the files.

    `file` names the file for a family that does not place its own rows, which is the
    licence receipts: they are dated by the run, not by anything a row holds. Every row is
    placed and serialised before anything is appended.
    """
    if family.order is not None:
        raise StoreError(f"{family.name} is sorted; use write_family")
    target = family.target
    by_file: dict[Path, list[_Keyed]] = {}
    for entry in _keyed(family, rows, ROWS):
        if file is not None:
            destination = file
        elif target is not None:
            destination = target(entry[2], root, entry[1])
        else:
            raise StoreError(f"{family.name} does not place its own rows; name the file")
        by_file.setdefault(destination, []).append(entry)
    bodies = {destination: _body(family, held) for destination, held in by_file.items()}
    for destination, body in bodies.items():
        append_text(destination, body)
    return list(bodies)
