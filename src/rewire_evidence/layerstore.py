"""Put a layer tree into the object store and manifest (`shared/archive/layers/store.ts`).

A derived row inherits the parent's origin, licence columns and source IDs, because a layer
answers for the same sources as the bytes it came from, and a tier governs publication of
the work, not of one rendering of it. A layer whose SHA-256 equals its input's is not
written, because the object already is its own text layer and the manifest holds one row per
SHA-256.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from rewire_evidence import archive, layers
from rewire_evidence.canonical import JsonObject, JsonValue
from rewire_evidence.containers import DEFAULT_LIMITS, Limits


@dataclass(frozen=True)
class StoreLayerOptions:
    """Layer derivation limits forwarded by `store_layers`."""

    max_depth: int = layers.MAX_DEPTH
    limits: Limits = DEFAULT_LIMITS
    pdftotext: bool | None = None


@dataclass(frozen=True)
class StoredLayer:
    """One derived manifest and cache entry recorded by `store_layers`."""

    sha256: str
    layer: str
    derived_from: str
    derivation: str
    bytes: int
    characters: int | None
    written: bool
    added: bool


@dataclass
class StoreResult:
    """What `store_layers` wrote and recorded for one object tree."""

    root: layers.LayerNode
    stored: list[StoredLayer] = field(default_factory=list)
    identities: list[str] = field(default_factory=list)
    unsupported: list[JsonObject] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def derived_row(parent: Mapping[str, JsonValue], fields: Mapping[str, JsonValue]) -> JsonObject:
    """Build a derived manifest row inheriting provenance and licence fields from `parent`."""
    return archive.parse(
        archive.MANIFEST_ROW,
        {
            **parent,
            "first_seen_at": archive.utc_now(),
            "previous_path": None,
            **fields,
        },
    )


def _record_layer(
    parent: Mapping[str, JsonValue],
    data: bytes,
    fields: Mapping[str, JsonValue],
    characters: int | None,
    root: Path,
) -> StoredLayer:
    put = archive.put_object(data, root)
    row = derived_row(parent, fields)
    upsert = archive.upsert_row(row, root)
    return StoredLayer(
        sha256=cast("str", row["sha256"]),
        layer=cast("str", row["layer"]),
        derived_from=cast("str", row["derived_from"]),
        derivation=cast("str", row["derivation"]),
        bytes=len(data),
        characters=characters,
        written=bool(put["written"]),
        added=bool(upsert["added"]),
    )


def store_layers(
    parent: Mapping[str, JsonValue],
    data: bytes,
    root: Path = Path(),
    *,
    pdftotext: bool | None = None,
    options: StoreLayerOptions | None = None,
) -> StoreResult:
    """Derive every layer under one stored object and write them beside it."""
    opts = StoreLayerOptions() if options is None else options
    use_pdf = opts.pdftotext if pdftotext is None else pdftotext
    media_type = cast("str | None", parent.get("media_type"))
    tree = layers.layer_of(data, media_type, max_depth=opts.max_depth, limits=opts.limits, pdftotext=use_pdf)
    result = StoreResult(root=tree)

    for node, container, member_path in layers.walk(tree):
        result.problems.extend(f"{node.sha256}: {problem}" for problem in node.problems)
        if node.unsupported:
            result.unsupported.append(
                {"sha256": node.sha256, "path": member_path, "reason": node.unsupported}
            )

        if container and member_path is not None:
            result.stored.append(
                _record_layer(
                    parent,
                    node.content,
                    {
                        "sha256": node.sha256,
                        "bytes": len(node.content),
                        "media_type": node.classification.media_type,
                        "layer": "archive-member",
                        "derived_from": container,
                        "derivation": f"archive-member@1 {member_path}",
                    },
                    None,
                    root,
                )
            )

        layer = node.layer
        if layer is None:
            continue
        if layer.identity:
            result.identities.append(node.sha256)
            continue
        result.stored.append(
            _record_layer(
                parent,
                layer.content,
                {
                    "sha256": layer.output_sha256,
                    "bytes": len(layer.content),
                    "media_type": "text/plain",
                    "layer": "text-layer",
                    "derived_from": node.sha256,
                    "derivation": layer.derivation,
                },
                layer.characters,
                root,
            )
        )
    return result
