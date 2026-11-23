"""Building and reading the layer registry.

This is `shared/omics/layers/registry.ts` and `types.ts`. The registry is one line per text
layer in `data/omics/layers/registry.jsonl`, generated from the object manifest and the
stored bytes. It holds no bytes and no clock, so regenerating it over an unchanged store
writes the same file. Beside it, `unsupported.jsonl` lists the held objects that gave no
layer, each with the reason the derivation gave.

A row names the layer by its hash, the manifest object it was reached from, the bytes the
normaliser read and, for a member of a container, the path it sat at. A member is not an
object in the manifest, so that triple is its whole address: expanding the container again
reproduces it.

A table grid is registered as a text layer is, with its own hash and row. Its verdict is
never better than the verdict on the text layer it addresses.

Both files are written by `canonical_line`: the top-level keys sorted and `metrics` in the
order the schema declares its counts.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from rewire_evidence import zod
from rewire_evidence.archive import ArchiveError, parse, parsed_lines
from rewire_evidence.canonical import JsonObject, JsonValue, canonical_line, utf16_key
from rewire_evidence.classify import Undecodable
from rewire_evidence.jsonl import StoreError, join_lines, write_text
from rewire_evidence.layers import LayerNode, layer_of, walk
from rewire_evidence.pdftext import pdf_page_count
from rewire_evidence.sanity import RANK, SANITY_VERDICTS, metrics_of, sanity_of
from rewire_evidence.textlayer import Layer

REGISTRY_FILE: Final = "data/omics/layers/registry.jsonl"
UNSUPPORTED_FILE: Final = "data/omics/layers/unsupported.jsonl"

_HEX64: Final = zod.text(zod.pattern(re.compile(r"[a-f0-9]{64}"), "^[a-f0-9]{64}$"))
_COUNT: Final = zod.number(zod.INT, zod.NONNEGATIVE)

#: What the sanity gate counted, in the order a row holds the counts.
LAYER_METRICS: Final = zod.strict(
    {
        "characters": _COUNT,
        "non_space": _COUNT,
        "replacement": _COUNT,
        "printable": _COUNT,
        "digits": _COUNT,
        "letters": _COUNT,
        "punctuation": _COUNT,
        "longest_punctuation_run": _COUNT,
        "page_breaks": _COUNT,
        "pdf_pages": zod.nullable(_COUNT),
    }
)

#: One text layer, addressable by `layer_sha256`.
LAYER_RECORD: Final = zod.strict(
    {
        "layer_sha256": _HEX64,
        "object_sha256": _HEX64,
        "derived_from": _HEX64,
        "container_path": zod.nullable(zod.text()),
        "normaliser": zod.NONEMPTY,
        "tool": zod.NONEMPTY,
        "tool_version": zod.NONEMPTY,
        "derivation": zod.NONEMPTY,
        "encoding": zod.nullable(zod.text()),
        "media_type": zod.NONEMPTY,
        "identity": zod.Boolean(),
        "bytes": _COUNT,
        "metrics": LAYER_METRICS,
        "sanity": zod.one_of(*SANITY_VERDICTS),
        "sanity_reasons": zod.array(zod.NONEMPTY),
        "source_ids": zod.array(zod.NONEMPTY),
        "visibility": zod.NONEMPTY,
        "store": zod.NONEMPTY,
    }
)

#: One object or member that gave no layer, with the reason the derivation gave.
UNSUPPORTED_RECORD: Final = zod.strict(
    {
        "object_sha256": _HEX64,
        "container_path": zod.nullable(zod.text()),
        "reason": zod.NONEMPTY,
    }
)


def registry_path(root: Path) -> Path:
    """The registry file of a checkout."""
    return root / REGISTRY_FILE


def unsupported_path(root: Path) -> Path:
    """The file listing the held objects that gave no layer."""
    return root / UNSUPPORTED_FILE


@dataclass
class BuildResult:
    """What a build made, and what it could not do."""

    records: list[JsonObject] = field(default_factory=list)
    #: Objects, or members, that carry no layer, each with the reason.
    unsupported: list[JsonObject] = field(default_factory=list)
    #: Objects whose bytes could not be read at all.
    unreadable: list[JsonObject] = field(default_factory=list)


def _problem(sha256: JsonValue, container_path: str | None, reason: str) -> JsonObject:
    return {"object_sha256": sha256, "container_path": container_path, "reason": reason}


def _text(row: Mapping[str, JsonValue], name: str) -> str:
    """A member that is text, or no text for one that is null: `row[name] ?? ""`."""
    return cast("str | None", row[name]) or ""


def _record_key(record: Mapping[str, JsonValue]) -> tuple[str, str, bytes]:
    """The order of the file: the layer hash, then the object it was reached from, then the path."""
    return (
        _text(record, "layer_sha256"),
        _text(record, "object_sha256"),
        utf16_key(_text(record, "container_path")),
    )


def _problem_key(problem: Mapping[str, JsonValue]) -> tuple[str, bytes, bytes]:
    return (
        _text(problem, "object_sha256"),
        utf16_key(_text(problem, "container_path")),
        utf16_key(_text(problem, "reason")),
    )


def _record_of(
    row: Mapping[str, JsonValue], node: LayerNode, container_path: str | None, layer: Layer
) -> JsonObject:
    pages = pdf_page_count(node.content) if node.classification.kind == "pdf" else None
    metrics = metrics_of(layer.content.decode("utf-8", "replace"), pages)
    sanity = sanity_of(metrics, layer.normaliser)
    source_ids: list[JsonValue] = [*sorted(cast("list[str]", row["source_ids"]), key=utf16_key)]
    return parse(
        LAYER_RECORD,
        {
            "layer_sha256": layer.output_sha256,
            "object_sha256": row["sha256"],
            "derived_from": node.sha256,
            "container_path": container_path,
            "normaliser": layer.normaliser,
            "tool": layer.tool,
            "tool_version": layer.tool_version,
            "derivation": layer.derivation,
            "encoding": node.classification.encoding,
            "media_type": node.classification.media_type,
            "identity": layer.identity,
            "bytes": len(layer.content),
            "metrics": metrics,
            "sanity": sanity.verdict,
            "sanity_reasons": list(sanity.reasons),
            "source_ids": source_ids,
            "visibility": row["visibility"],
            "store": row["store"],
        },
    )


def _records_for(
    row: Mapping[str, JsonValue], node: LayerNode, container_path: str | None
) -> list[JsonObject]:
    """The rows one node gives: its text layer, and the table grid beside it where it has one."""
    if not node.layer:
        return []
    text = _record_of(row, node, container_path, node.layer)
    if not node.grid:
        return [text]
    grid = _record_of(row, node, container_path, node.grid)
    # A grid over a layer nobody may cite is a grid nobody may cite.
    if RANK[_text(text, "sanity")] > RANK[_text(grid, "sanity")]:
        said = "; ".join(cast("list[str]", text["sanity_reasons"]))
        grid["sanity"] = text["sanity"]
        grid["sanity_reasons"] = [f"the text layer this grid addresses is {text['sanity']}: {said}"]
    return [text, grid]


def layers_of_object(row: Mapping[str, JsonValue], data: bytes) -> BuildResult:
    """Every layer under one stored object, whose expanded bytes `data` are.

    Nothing is fetched and nothing is written. Raises `archive.Refused` for a row the
    registry schema will not hold.
    """
    result = BuildResult()
    try:
        tree = layer_of(data, cast("str | None", row["media_type"]))
    except Undecodable as error:
        result.unreadable.append(_problem(row["sha256"], None, str(error)))
        return result
    for node, _, member_path in walk(tree):
        records = _records_for(row, node, member_path)
        if records:
            result.records += records
        elif node.unsupported:
            result.unsupported.append(_problem(row["sha256"], member_path, node.unsupported))
    return result


def build_registry(
    rows: Iterable[Mapping[str, JsonValue]],
    read: Callable[[str], bytes],
    on_object: Callable[[str, int], None] | None = None,
) -> BuildResult:
    """The registry over a whole manifest.

    `read` gives an object's expanded bytes, and raising `StoreError` or `ArchiveError` is
    how it says the bytes are not held. `on_object` is called after each object.
    """
    result = BuildResult()
    for done, row in enumerate(rows, 1):
        key = _text(row, "sha256")
        try:
            data = read(key)
        except (StoreError, ArchiveError) as error:
            result.unreadable.append(_problem(key, None, str(error)))
        else:
            one = layers_of_object(row, data)
            result.records += one.records
            result.unsupported += one.unsupported
            result.unreadable += one.unreadable
        if on_object:
            on_object(key, done)
    result.records.sort(key=_record_key)
    return result


def serialise_registry(records: Sequence[Mapping[str, JsonValue]]) -> str:
    """The registry file: one record per line, sorted by layer hash."""
    return join_lines(canonical_line(record) for record in sorted(records, key=_record_key))


def serialise_unsupported(problems: Sequence[Mapping[str, JsonValue]]) -> str:
    """The unsupported file: one object or member per line, sorted by object, path and reason."""
    return join_lines(canonical_line(problem) for problem in sorted(problems, key=_problem_key))


def write_registry(records: Sequence[Mapping[str, JsonValue]], root: Path) -> None:
    """Write the registry file of a checkout."""
    registry_path(root).parent.mkdir(parents=True, exist_ok=True)
    write_text(registry_path(root), serialise_registry(records))


def write_unsupported(problems: Sequence[Mapping[str, JsonValue]], root: Path) -> None:
    """Write the unsupported file of a checkout."""
    unsupported_path(root).parent.mkdir(parents=True, exist_ok=True)
    write_text(unsupported_path(root), serialise_unsupported(problems))


def read_registry(root: Path) -> list[JsonObject]:
    """The registry as it stands. A missing file is an empty registry."""
    path = registry_path(root)
    return parsed_lines(path, LAYER_RECORD, REGISTRY_FILE, skipping=True) if path.exists() else []
