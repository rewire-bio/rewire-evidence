"""What the TypeScript layer derivation recorded, and the helpers the tests replay it with.

`tests/fixtures/layers.json` is written by `scripts/fixtures/record_layers_fixture.mjs` in CI,
from the data repository's own code at the pinned commit. Nothing in it was written by hand.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from rewire_evidence.classify import Classification
from rewire_evidence.containers import DEFAULT_LIMITS, Limits
from rewire_evidence.layers import LayerNode
from rewire_evidence.textlayer import Layer

FIXTURES = Path(__file__).parent / "fixtures"
LAYERS: dict[str, Any] = json.loads((FIXTURES / "layers.json").read_text("utf-8"))
#: The objects the cases are made of, by name.
OBJECTS: dict[str, bytes] = {name: bytes.fromhex(held) for name, held in LAYERS["objects"].items()}

#: The manifest row the recorder gives every object, before the case's own members.
BASE_ROW: dict[str, Any] = {
    "sha256": None,
    "bytes": 0,
    "media_type": "text/plain",
    "layer": "exact-bytes",
    "origin": "https://example.org/a",
    "requested_url": None,
    "first_seen_at": "2026-11-17T16:10:11Z",
    "store": "r2",
    "mirrors": [],
    "visibility": "restricted",
    "archive_tier": "t4-private",
    "observed_licence": None,
    "exposure": "policy",
    "derived_from": None,
    "derivation": None,
    "previous_path": None,
    "source_ids": ["s2", "s10", "S1", "é", "\U0001f600", "～"],
}


def key_of(name: str) -> str:
    return hashlib.sha256(OBJECTS[name]).hexdigest()


def row_for(name: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
    """The manifest row of a named object, as `rowFor` in the cases writes it."""
    return {**BASE_ROW, "sha256": key_of(name), "bytes": len(OBJECTS[name]), **(patch or {})}


def limits_of(held: dict[str, int] | None) -> Limits:
    if held is None:
        return DEFAULT_LIMITS
    return Limits(held["maxMembers"], held["maxMemberBytes"], held["maxTotalBytes"])


def classification_of(found: Classification) -> dict[str, Any]:
    return {
        "class": found.kind,
        "media_type": found.media_type,
        "evidence": found.evidence,
        "unsupported": found.unsupported,
        "encoding": found.encoding,
    }


def layer_of(layer: Layer | None) -> dict[str, Any] | None:
    """A layer as the recorder holds one."""
    if layer is None:
        return None
    return {
        "normaliser": layer.normaliser,
        "tool": layer.tool,
        "tool_version": layer.tool_version,
        "derivation": layer.derivation,
        "input_sha256": layer.input_sha256,
        "output_sha256": layer.output_sha256,
        "bytes": len(layer.content),
        "characters": layer.characters,
        "identity": layer.identity,
        "text": layer.content.decode("utf-8"),
    }


def node_of(node: LayerNode) -> dict[str, Any]:
    """A layer tree as the recorder holds one."""
    return {
        "sha256": node.sha256,
        "bytes": len(node.content),
        "classification": classification_of(node.classification),
        "layer": layer_of(node.layer),
        "grid": layer_of(node.grid),
        "unsupported": node.unsupported,
        "problems": node.problems,
        "members": [{"path": path, "node": node_of(member)} for path, member in node.members],
    }
