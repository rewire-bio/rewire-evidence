"""The shape every normaliser returns, and the one way a layer is made from text.

This is `shared/archive/layers/types.ts` and `layerFromText` in `text.ts`. A normaliser is a
pure function: bytes in, layer out. It reads no file, asks no network and reads no clock, so
the same bytes through the same tool version give the same layer SHA-256, which is what a
span cites.

A JavaScript string is UTF-16 units and a Python string is characters. The two write the
same bytes for any text that came out of UTF-8. They differ only on half a surrogate pair,
which UTF-16 input can hold: Node writes U+FFFD for a half with no partner and joins two
halves that end up side by side, so the text is put into that form before it is counted or
written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rewire_evidence.canonical import js_utf8, sha256_hex
from rewire_evidence.js import paired

#: The normalisers, in the order the TypeScript declares them. The last two are grids.
NORMALISERS: Final = (
    "pdf-text-layer@1",
    "europepmc-fulltext-xml@1",
    "html-text@1",
    "text-utf8@1",
    "archive-member@1",
    "ooxml-sheet-tsv@1",
    "ooxml-document-text@1",
    "jats-table-grid@1",
    "html-table-grid@1",
)


class LayerFailure(Exception):  # noqa: N818 - a normaliser's refusal, which the caller reports as a reason
    """A normaliser could not make a layer of the bytes. The message is the TypeScript's."""


@dataclass(frozen=True)
class Layer:
    """One layer, and everything the registry row for it needs."""

    normaliser: str
    #: The tool that produced the bytes. The normalisers here name themselves; the PDF one
    #: names pdftotext.
    tool: str
    tool_version: str
    #: The exact command. It carries no path, no timestamp and nothing a second run could change.
    derivation: str
    input_sha256: str
    output_sha256: str
    content: bytes
    #: Characters in the layer, which is what a span is counted in.
    characters: int
    #: Whether the layer bytes are the input bytes, so the object is its own text layer.
    identity: bool


def character_count(text: str) -> int:
    """Characters the way a span counts them: code points, a surrogate pair being one."""
    return len(paired(text))


def layer_from_text(data: bytes, text: str, normaliser: str, derivation: str) -> Layer:
    """The layer for a decoded string, with the digests filled in."""
    content = js_utf8(text)
    input_sha = sha256_hex(data)
    output_sha = sha256_hex(content)
    return Layer(
        normaliser=normaliser,
        tool=normaliser,
        tool_version=normaliser.split("@")[1],
        derivation=derivation,
        input_sha256=input_sha,
        output_sha256=output_sha,
        content=content,
        characters=character_count(text),
        identity=input_sha == output_sha,
    )
