"""Reading a registered layer: its text, its grid, and a span of it.

This is the reading half of `shared/omics/layers/index.ts`. The registry says which layers
exist and what each was derived from. It does not hold their bytes. A layer's text is derived
again from the stored object every time it is read, and the derivation is accepted only when
it gives the hash the registry names, so a reader never holds text the registry does not
vouch for.

The TypeScript keeps the registry and the manifest of one checkout in module variables.
`LayerStore` is that, as an object: one checkout, read once.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from rewire_evidence import archive, registry
from rewire_evidence.canonical import JsonObject, JsonValue
from rewire_evidence.classify import Undecodable
from rewire_evidence.grids import grid_normaliser_for
from rewire_evidence.gridspans import TableGrid, read_grid
from rewire_evidence.jsonl import StoreError
from rewire_evidence.layers import layer_of, walk
from rewire_evidence.sanity import RANK

_GRID_SUFFIX: Final = "-table-grid@1"


class LayerUnreadable(StoreError):  # noqa: N818 - a layer that cannot be read, which a caller counts
    """A registered layer whose text cannot be derived again from the stored object."""


def is_grid(record: Mapping[str, JsonValue]) -> bool:
    """Whether a registry row is a table grid and not a text layer."""
    return cast("str", record["normaliser"]).endswith(_GRID_SUFFIX)


def by_usefulness(record: Mapping[str, JsonValue]) -> tuple[int, int]:
    """The order layers are tried in: passing before suspect before failed, then the longest first."""
    metrics = cast("Mapping[str, int]", record["metrics"])
    return RANK[cast("str", record["sanity"])], -metrics["characters"]


def character_count(text: str) -> int:
    """Characters of a layer's text, which is what an offset counts."""
    return len(text)


def slice_span(text: str, start: int, end: int) -> JsonObject:
    """The characters of a span, with both pairs of offsets.

    `text` is a layer as its bytes decode, and `start` and `end` are character offsets. A
    resolver uses this and never slices by itself. Raises `ValueError` for a range the layer
    does not hold.
    """
    if start < 0 or end < start:
        raise ValueError(f"span {start} to {end} is not a range")
    if end > len(text):
        raise ValueError(f"span ends at {end} and the layer holds {len(text)} characters")
    exact = text[start:end]
    byte_start = len(text[:start].encode("utf-8"))
    return {
        "exact": exact,
        "start": start,
        "end": end,
        "byte_start": byte_start,
        "byte_end": byte_start + len(exact.encode("utf-8")),
    }


class LayerStore:
    """The layers of one checkout: its registry, its manifest and the objects its cache holds."""

    def __init__(self, root: Path, objects: Path | None = None) -> None:
        """Read the registry and the manifest of a checkout.

        `objects` is a folder of objects laid out as the cache is, read in place of the
        checkout's own cache and of whatever `ARCHIVE_CACHE` names.
        """
        self.root: Final = root
        self.objects: Final = objects
        self.records: Final = registry.read_registry(root)
        #: The object manifest, by key.
        self.manifest: Final = archive.read_manifest(root)
        self._first: Final[dict[str, JsonObject]] = {}
        for record in self.records:
            self._first.setdefault(cast("str", record["layer_sha256"]), record)
        #: The layers of the object derived last, so its grid is not a second derivation.
        self._derived: tuple[str, dict[str, bytes]] = ("", {})

    def read_object(self, key: str) -> bytes:
        """The expanded bytes of a stored object, verified against its key. Raises `StoreError`."""
        if self.objects is None:
            return archive.read_object(key, self.root)
        file = self.objects / key[:2] / f"{key}.gz"
        try:
            return archive.expand(file.read_bytes(), key, str(file))
        except OSError as error:
            raise StoreError(f"{file} cannot be read: {error}") from error

    def has_object(self, key: str) -> bool:
        """Whether an object is held, with its bytes verified against its key."""
        try:
            self.read_object(key)
        except StoreError:
            return False
        return True

    def layers_for_source(self, source_id: str) -> list[JsonObject]:
        """The text layers that carry a source, the best first."""
        held = [
            record
            for record in self.records
            if source_id in cast("list[str]", record["source_ids"]) and not is_grid(record)
        ]
        return sorted(held, key=by_usefulness)

    def grids_for_source(self, source_id: str) -> list[JsonObject]:
        """The table grids over the layers that carry a source."""
        held = [
            record
            for record in self.records
            if source_id in cast("list[str]", record["source_ids"]) and is_grid(record)
        ]
        return sorted(held, key=by_usefulness)

    def layer_by_hash(self, layer_sha256: str) -> JsonObject | None:
        """The registry row for a layer hash: the first, where two objects give one layer."""
        return self._first.get(layer_sha256)

    def grid_for_layer(self, layer_sha256: str) -> JsonObject | None:
        """The grid built beside a text layer: the same object, the same member, the paired normaliser."""
        text = self.layer_by_hash(layer_sha256)
        if text is None:
            return None
        normaliser = grid_normaliser_for(cast("str", text["normaliser"]))
        if normaliser is None:
            return None
        return next(
            (
                record
                for record in self.records
                if record["object_sha256"] == text["object_sha256"]
                and record["derived_from"] == text["derived_from"]
                and record["normaliser"] == normaliser
            ),
            None,
        )

    def layers_for_object(self, object_sha256: str) -> list[JsonObject]:
        """Every layer derived from one stored object, its members and grids included."""
        return [record for record in self.records if record["object_sha256"] == object_sha256]

    def _layers_of(self, key: str) -> dict[str, bytes]:
        """The bytes of every layer and grid the object gives, by hash."""
        if self._derived[0] != key:
            row = self.manifest.get(key)
            if row is None:
                raise LayerUnreadable(f"{key} is not in the object manifest")
            try:
                tree = layer_of(self.read_object(key), cast("str", row["media_type"]))
            except (StoreError, Undecodable) as error:
                raise LayerUnreadable(str(error)) from error
            found: dict[str, bytes] = {}
            for node, _, _ in walk(tree):
                for layer in (node.layer, node.grid):
                    if layer is not None:
                        found.setdefault(layer.output_sha256, layer.content)
            self._derived = (key, found)
        return self._derived[1]

    def layer_text(self, record: Mapping[str, JsonValue]) -> str:
        """The text of a registered layer, derived again from the stored object.

        Raises `LayerUnreadable` where the object is not in the manifest or the cache, and
        where the derivation does not give the hash the registry names, which says a tool
        changed.
        """
        key = cast("str", record["object_sha256"])
        content = self._layers_of(key).get(cast("str", record["layer_sha256"]))
        if content is None:
            path = record["container_path"]
            where = "" if path is None else f" member {path}"
            raise LayerUnreadable(
                f"re-deriving {key}{where} did not reproduce layer {record['layer_sha256']}"
            )
        return content.decode("utf-8", "replace")

    def grid_of_record(self, record: Mapping[str, JsonValue]) -> TableGrid:
        """A table grid, read from its registry row. Raises `LayerUnreadable` or `GridUnreadable`."""
        if not is_grid(record):
            raise LayerUnreadable(f"{record['layer_sha256']} is a {record['normaliser']} layer, not a grid")
        return read_grid(self.layer_text(record))
