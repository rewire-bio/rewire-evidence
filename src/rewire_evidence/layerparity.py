"""Hold the layer derivation to the registry the data repository commits.

The oracle is `data/omics/layers/registry.jsonl` and `unsupported.jsonl`: 2,595 rows that
each name a layer by the SHA-256 of its bytes, with the counts the sanity gate read from
them, and 245 objects or members that gave no layer, each with the reason. The gate derives
every layer again from the stored bytes and requires each committed line, whole.

Three things about the oracle decide the gate's shape.

1. A row copies `store`, `visibility` and `source_ids` from the manifest row of its object,
   and the manifest has moved since the registry was written: every object's home is now R2.
   So the rows are regenerated from the manifest of the commit that wrote the registry,
   which the expectation names and holds by digest, and must then be the committed lines
   byte for byte. What the pinned checkout's own manifest would write instead is measured
   and reported: which members of how many rows differ, and that nothing differs but them.
2. The bytes are not in the checkout. The gate reads a folder of objects laid out as the
   cache is, each file the gzip bytes that were received. Every object the manifest says a
   public release mirrors has to be there, and one that is missing is a failure. A
   restricted object is checked when it is there and counted as not checked when it is not.
3. A PDF layer is whatever the pinned pdftotext writes. The gate refuses to run on any other
   version, by the line pdftotext prints, rather than report rows that could not have agreed.

When every object is held the two files are also written whole and compared, which is
`layers -- check` as a gate. In any run the committed files are written again from their own
rows, which holds the sort order and the serialisation over all of them without the bytes.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from rewire_evidence import archive, registry
from rewire_evidence.canonical import JsonObject, JsonValue, canonical_line, sha256_hex, utf16_key
from rewire_evidence.checkparity import Lines
from rewire_evidence.conformance import recorded_ref
from rewire_evidence.families import OBJECT_SHARDS, manifest_shard_path
from rewire_evidence.jsonl import StoreError, parse_row
from rewire_evidence.pdftext import PDF_NORMALISER, pdftotext_version
from rewire_evidence.textlayer import LayerFailure

#: The members of a registry row that are copied from the manifest row of its object.
COPIED: Final = ("store", "visibility", "source_ids")
#: The backend whose copy of an object anyone can fetch.
PUBLIC_MIRROR: Final = "github-release"
UNSUPPORTED: Final = "unsupported"


@dataclass
class LayerReport:
    """What the layer gate found."""

    store_ref: str = ""
    layers_ref: str = ""
    #: The line pdftotext names its version in, or why it named none.
    pdftotext: str = ""
    #: Objects of the manifest, by whether a public release mirrors them and whether they were held.
    objects: Counter[str] = field(default_factory=Counter)
    #: Registry rows by normaliser, and the unsupported rows under `unsupported`.
    rows: dict[str, Lines] = field(default_factory=dict)
    #: Committed rows whose object was not held, by the same names.
    not_checked: Counter[str] = field(default_factory=Counter)
    #: Lines of each committed file that are written back as they stand.
    written: dict[str, Lines] = field(default_factory=dict)
    #: Whether both files, written whole, are the committed ones. `None` when an object was not held.
    whole_files: bool | None = None
    #: Checked rows that the pinned checkout's own manifest would write otherwise, by member.
    stale: Counter[str] = field(default_factory=Counter)
    stale_rows: int = 0
    problems: list[str] = field(default_factory=list)

    def total(self, *, unsupported: bool) -> Lines:
        """The registry rows of every normaliser together, or the unsupported rows."""
        held = [lines for name, lines in self.rows.items() if (name == UNSUPPORTED) == unsupported]
        return Lines(
            sum(lines.made for lines in held),
            sum(lines.expected for lines in held),
            sum(lines.same for lines in held),
        )


def _bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise StoreError(f"{path} cannot be read: {error}") from error


def _split(text: str) -> list[str]:
    """The lines of a file body, each of which a newline ends."""
    return text.split("\n")[:-1]


def _lines(data: bytes, where: str) -> list[str]:
    try:
        return _split(data.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise StoreError(f"{where} is not UTF-8: {error}") from error


def _member(recorded: Mapping[str, JsonValue], name: str) -> JsonObject:
    held = recorded.get(name)
    if not isinstance(held, dict):
        raise StoreError(f"the expectation does not state {name}")
    return held


def manifest_at(root: Path, stated: Mapping[str, JsonValue]) -> dict[str, JsonObject]:
    """The manifest rows of the commit that wrote the registry, by key, held to the digest stated.

    The rows are read as JSON and not through the manifest schema, which has gained members
    since. Raises `StoreError` when the shards are not the ones the expectation names.
    """
    shards = [_bytes(manifest_shard_path(shard, root)) for shard in OBJECT_SHARDS]
    if sha256_hex(b"".join(shards)) != stated.get("sha256"):
        raise StoreError(
            f"the manifest under {root} is not the one the registry was written from, "
            f"which the expectation holds by the digest {stated.get('sha256')}"
        )
    rows: dict[str, JsonObject] = {}
    for shard, data in zip(OBJECT_SHARDS, shards, strict=True):
        where = f"data/archive/objects/{shard}.jsonl"
        for number, line in enumerate(_lines(data, where), 1):
            row = parse_row(line, f"{where} line {number}")
            rows[cast("str", row["sha256"])] = row
    return rows


def _committed(root: Path, file: str, stated: Mapping[str, JsonValue], found: LayerReport) -> list[str]:
    """The lines of a committed file, with a problem if it is not the file the expectation names."""
    data = _bytes(root / file)
    if sha256_hex(data) != stated.get("sha256"):
        found.problems.append(f"{file} in the checkout is not the file the expectation names by digest")
    return _lines(data, file)


def _by_object(lines: list[str], where: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for number, line in enumerate(lines, 1):
        row = parse_row(line, f"{where} line {number}")
        grouped.setdefault(cast("str", row["object_sha256"]), []).append(line)
    return grouped


def _normaliser(line: str) -> str:
    return cast("str", json.loads(line)["normaliser"])


def _compare(kind: str, made: list[str], expected: list[str], found: LayerReport, key: str) -> None:
    """Count the lines one object gave against the committed ones, and name each that differs."""
    left = Counter(expected)
    for line in made:
        name = UNSUPPORTED if kind == UNSUPPORTED else _normaliser(line)
        lines = found.rows.setdefault(name, Lines())
        lines.made += 1
        if left[line]:
            left[line] -= 1
            lines.same += 1
        else:
            found.problems.append(
                f"{key}: a {name} line is made that the committed {kind} does not hold: {line}"
            )
    found.problems.extend(
        f"{key}: a committed {kind} line is not made: {line}"
        for line in sorted(left.elements(), key=utf16_key)
    )


def _restated(record: Mapping[str, JsonValue], row: Mapping[str, JsonValue]) -> JsonObject:
    """The record as the manifest row of another commit would have it written."""
    source_ids: list[JsonValue] = [*sorted(cast("list[str]", row["source_ids"]), key=utf16_key)]
    return {**record, "store": row["store"], "visibility": row["visibility"], "source_ids": source_ids}


def _fetchable(row: Mapping[str, JsonValue]) -> bool:
    return row["visibility"] == "public" and PUBLIC_MIRROR in cast("list[str]", row["mirrors"])


def _expect(found: LayerReport, lines: Mapping[str, list[str]], kind: str) -> None:
    for held in lines.values():
        for line in held:
            name = UNSUPPORTED if kind == UNSUPPORTED else _normaliser(line)
            found.rows.setdefault(name, Lines()).expected += 1


def _body(lines: list[str]) -> str:
    return "".join(f"{line}\n" for line in lines)


def _written(found: LayerReport, name: str, lines: list[str]) -> None:
    """Hold a committed file to what the writer makes of its own rows."""
    rows = [parse_row(line, f"{name} line {number}") for number, line in enumerate(lines, 1)]
    if name == registry.REGISTRY_FILE:
        made = registry.serialise_registry(rows)
    else:
        made = registry.serialise_unsupported(rows)
    same = sum(1 for one, other in zip(_split(made), lines, strict=False) if one == other)
    found.written[name] = Lines(len(_split(made)), len(lines), same)
    if made != _body(lines):
        found.problems.append(f"{name}, written again from its own rows, is not the committed file")


@dataclass
class _Committed:
    """The two committed files: their lines, and the lines of each by the object they name."""

    registry: list[str]
    unsupported: list[str]
    rows: dict[str, list[str]]
    reasons: dict[str, list[str]]


def _check(  # noqa: PLR0913 - one object, the two manifests' rows for it, and what to hold it to
    key: str,
    file: Path,
    *,
    then: Mapping[str, JsonValue],
    now: Mapping[str, JsonValue],
    committed: _Committed,
    found: LayerReport,
) -> registry.BuildResult:
    """Derive the layers of one held object and hold its lines to the committed ones."""
    try:
        data = archive.expand(_bytes(file), key, str(file))
    except archive.ArchiveError as error:
        found.problems.append(str(error))
        return registry.BuildResult()
    found.objects["checked"] += 1
    if now["media_type"] != then["media_type"]:
        found.stale["media_type"] += 1
    one = registry.layers_of_object(then, data)
    found.problems.extend(f"{key} could not be read: {problem['reason']}" for problem in one.unreadable)
    made = [canonical_line(record) for record in one.records]
    _compare("registry", made, committed.rows.get(key, []), found, key)
    made = [canonical_line(problem) for problem in one.unsupported]
    _compare(UNSUPPORTED, made, committed.reasons.get(key, []), found, key)
    for record in one.records:
        differing = [name for name in COPIED if _restated(record, now)[name] != record[name]]
        found.stale.update(differing)
        found.stale_rows += bool(differing)
    return one


def _pdftotext(found: LayerReport, recorded: Mapping[str, JsonValue]) -> bool:
    """Whether pdftotext is the one the registry was written by, with a problem if it is not."""
    try:
        found.pdftotext = pdftotext_version()
    except LayerFailure as error:
        found.pdftotext = str(error)
    if found.pdftotext == recorded.get("pdftotext"):
        return True
    found.problems.append(
        f"pdftotext names itself as {found.pdftotext!r} and the registry was written by "
        f"{recorded.get('pdftotext')!r}, so no PDF layer could agree"
    )
    return False


def verify_layers(root: Path, layers_root: Path, objects: Path, recorded: JsonObject) -> LayerReport:
    """Derive every layer the held objects give and hold each line to the committed registry.

    `root` is a checkout at the pinned commit, `layers_root` holds the manifest of the commit
    that wrote the registry, and `objects` is a folder laid out as the object cache is.
    Raises `StoreError` for a checkout, a manifest or an expectation that cannot be read.
    Every disagreement is a problem in the report.
    """
    source = _member(recorded, "recorded_from")
    found = LayerReport(store_ref=recorded_ref(recorded), layers_ref=str(source.get("layers_ref")))
    then = manifest_at(layers_root, _member(recorded, "manifest"))
    now = archive.read_manifest(root)
    registry_lines = _committed(root, registry.REGISTRY_FILE, _member(recorded, "registry"), found)
    unsupported_lines = _committed(root, registry.UNSUPPORTED_FILE, _member(recorded, UNSUPPORTED), found)
    committed = _Committed(
        registry_lines,
        unsupported_lines,
        _by_object(registry_lines, registry.REGISTRY_FILE),
        _by_object(unsupported_lines, registry.UNSUPPORTED_FILE),
    )
    _expect(found, committed.rows, "registry")
    _expect(found, committed.reasons, UNSUPPORTED)
    _written(found, registry.REGISTRY_FILE, registry_lines)
    _written(found, registry.UNSUPPORTED_FILE, unsupported_lines)
    if not _pdftotext(found, recorded):
        return found

    result = registry.BuildResult()
    for key in sorted({*now, *then}):
        if key not in now or key not in then:
            found.problems.append(f"{key} is in one manifest and not the other")
            continue
        fetchable = _fetchable(now[key])
        found.objects["public" if fetchable else "restricted"] += 1
        file = objects / key[:2] / f"{key}.gz"
        if file.exists():
            one = _check(key, file, then=then[key], now=now[key], committed=committed, found=found)
            result.records += one.records
            result.unsupported += one.unsupported
        elif fetchable:
            found.problems.append(f"{key} is mirrored by a public release and is not in {objects}")
        else:
            found.objects["restricted_not_checked"] += 1
            for line in committed.rows.get(key, []):
                found.not_checked[_normaliser(line)] += 1
            found.not_checked[UNSUPPORTED] += len(committed.reasons.get(key, []))

    if found.objects["checked"] == len(now) == len(then):
        found.whole_files = registry.serialise_registry(result.records) == _body(
            registry_lines
        ) and registry.serialise_unsupported(result.unsupported) == _body(unsupported_lines)
        if not found.whole_files:
            found.problems.append(
                "the two files, written whole from every object, are not the committed files"
            )
    stale = sorted(found.stale)
    if found.objects["checked"] and stale != recorded.get("stale_members"):
        found.problems.append(
            f"against the pinned checkout's own manifest the rows differ in {stale}, "
            f"and the expectation states {recorded.get('stale_members')}"
        )
    return found


def summary(found: LayerReport) -> str:
    """The report as the lines the gate prints."""
    rows, unsupported = found.total(unsupported=False), found.total(unsupported=True)
    objects = found.objects
    stale = ", ".join(f"{name} ({count:,})" for name, count in sorted(found.stale.items())) or "no member"
    lines = [
        f"the store at {found.store_ref}",
        f"  the registry was written from the manifest of {found.layers_ref}",
        f"  {found.pdftotext}",
        (
            f"  objects: {objects['public']:,} public and {objects['restricted']:,} restricted, "
            f"{objects['checked']:,} checked, {objects['restricted_not_checked']:,} restricted not checked"
        ),
        (
            f"  registry rows reproduced: {rows.same:,} of {rows.expected:,}, "
            f"{sum(count for name, count in found.not_checked.items() if name != UNSUPPORTED):,} not checked"
        ),
    ]
    for name in sorted(name for name in found.rows if name != UNSUPPORTED):
        held = found.rows[name]
        lines.append(
            f"    {name:28}{held.same:>6,} of {held.expected:>6,}, {found.not_checked[name]:,} not checked"
        )
    lines += [
        (
            f"  unsupported rows reproduced: {unsupported.same:,} of {unsupported.expected:,}, "
            f"{found.not_checked[UNSUPPORTED]:,} not checked"
        ),
        *(
            f"  {name} written again from its own rows: {held.same:,} of {held.expected:,} lines"
            for name, held in found.written.items()
        ),
        "  both files written whole from every object: "
        + {
            None: "not asked, an object was not held",
            True: "the committed files",
            False: "NOT the committed files",
        }[found.whole_files],
        f"  checked rows the pinned manifest would write otherwise: {found.stale_rows:,}, in {stale}",
    ]
    return "\n".join(lines)


def to_json(found: LayerReport) -> str:
    """The report as JSON, for CI to read counts from."""
    rows, unsupported = found.total(unsupported=False), found.total(unsupported=True)
    return json.dumps(
        {
            "store_ref": found.store_ref,
            "layers_ref": found.layers_ref,
            "pdftotext": found.pdftotext,
            "objects": dict(found.objects),
            "rows": rows.row(),
            "unsupported": unsupported.row(),
            "normalisers": {name: held.row() for name, held in sorted(found.rows.items())},
            "not_checked": dict(found.not_checked),
            "rows_not_checked": sum(
                count for name, count in found.not_checked.items() if name != UNSUPPORTED
            ),
            "written": {name: held.row() for name, held in found.written.items()},
            "whole_files": found.whole_files,
            "stale_rows": found.stale_rows,
            "stale": dict(found.stale),
            "pdf_rows": found.rows.get(PDF_NORMALISER, Lines()).row(),
            "problems": found.problems,
        },
        indent=1,
        ensure_ascii=False,
    )
