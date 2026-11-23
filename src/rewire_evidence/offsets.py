"""Three ways of counting along one layer, and the index that converts between them.

This is `shared/omics/spans/offsets.ts`. A span is published in characters, because that is
what `oa:TextPositionSelector` counts. A resolver slices bytes, because that is what a UTF-8
file holds. Everything in JavaScript that finds text works in UTF-16 units, which is neither,
and the resolvers are written in those: a column is found by where its heading stands on
the line, a sentence is given 900 units to end in, a quote needs four of them.

So the text of a layer is held here as JavaScript holds it, one item of the string to a
UTF-16 unit, which `to_units` makes of a Python string and `js.paired` undoes. A layer with
no character beyond U+FFFF is the same string either way, and that is nearly all of them.
Every offset inside the resolvers is in units, as it is in the TypeScript, and a span carries
none: it is converted when it is made.

Nothing here normalises the text.
"""

from __future__ import annotations

import bisect
import functools
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from rewire_evidence.canonical import JsonObject
from rewire_evidence.js import paired

#: Characters of context either side of a span, which let it be found again in a rebuilt layer.
CONTEXT_POINTS: Final = 48

_BEYOND: Final = re.compile("[\U00010000-\U0010ffff]")
_PAIR: Final = re.compile("[\ud800-\udbff][\udc00-\udfff]")
_HALF: Final = re.compile("[\ud800-\udfff]")
_FIRST_BEYOND: Final = 0x10000


def _halves(found: re.Match[str]) -> str:
    code = ord(found[0]) - _FIRST_BEYOND
    return chr(0xD800 + (code >> 10)) + chr(0xDC00 + (code & 0x3FF))


def to_units(text: str) -> str:
    """The text with one item for each UTF-16 unit, as a JavaScript string holds it.

    It is `js.unit_text`, written so that a layer of millions of characters with none beyond
    U+FFFF costs one scan and no copy.
    """
    return text if text.isascii() or max(text) < "\U00010000" else _BEYOND.sub(_halves, text)


def is_high(unit: str) -> bool:
    """Whether a unit is the first half of a character beyond U+FFFF."""
    return "\ud800" <= unit <= "\udbff"


def is_low(unit: str) -> bool:
    """Whether a unit is the second half of a character beyond U+FFFF."""
    return "\udc00" <= unit <= "\udfff"


def count_points(text: str) -> int:
    """Characters in text held in units: the units, less one for each pair of halves."""
    if text.isascii():
        return len(text)
    return len(text) - len(_PAIR.findall(text))


def byte_length(text: str) -> int:
    """`Buffer.byteLength(text, "utf8")` for text held in units.

    A pair of halves is one character of four bytes. A half with no partner is written as
    U+FFFD, which is three, and three is what Python writes for the half itself.
    """
    if text.isascii():
        return len(text)
    return len(text.encode("utf-8", "surrogatepass")) - 2 * len(_PAIR.findall(text))


@dataclass(frozen=True)
class Span:
    """A place in a layer: `Span` of `shared/omics/locator.ts`. The three texts are in units."""

    layer_sha256: str
    #: First character of the span, counted from 0, and one past its last.
    start: int
    end: int
    #: The same in UTF-8 bytes.
    byte_start: int
    byte_end: int
    #: The layer text the span covers.
    exact: str
    #: Layer text before and after it, or `None` where the span is at an end of the layer.
    prefix: str | None = None
    suffix: str | None = None

    def row(self) -> JsonObject:
        """The span as the TypeScript holds it, its texts as characters."""
        row: JsonObject = {
            "layer_sha256": self.layer_sha256,
            "start": self.start,
            "end": self.end,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "exact": paired(self.exact),
        }
        if self.prefix is not None:
            row["prefix"] = paired(self.prefix)
        if self.suffix is not None:
            row["suffix"] = paired(self.suffix)
        return row


class LayerIndex:
    """A layer, with the tables for each line that make converting an offset cheap.

    `lines` is the layer split on a newline with the separators removed, so line `i` holds
    the units from `u16[i]`. The three lists hold the count before each line in each system,
    and one more entry for the end of the layer.
    """

    def __init__(self, text: str, layer_sha256: str, normaliser: str) -> None:
        """Index a layer. `text` is as the layer's bytes decode, in characters."""
        self.text: Final = to_units(text)
        self.layer_sha256: Final = layer_sha256
        self.normaliser: Final = normaliser
        self.lines: Final = self.text.split("\n")
        self.u16: Final[list[int]] = []
        unit = 0
        for line in self.lines:
            self.u16.append(unit)
            unit += len(line) + 1
        # The last line has no separator after it.
        self.u16.append(unit - 1)

    @functools.cached_property
    def plain(self) -> bool:
        """Whether every character of the layer is one byte, so the three counts are one."""
        return self.text.isascii()

    @functools.cached_property
    def points(self) -> list[int]:
        """Characters before each line starts, and the count of them all."""
        if self.plain or _HALF.search(self.text) is None:
            return self.u16
        return self._before(count_points)

    @functools.cached_property
    def bytes(self) -> list[int]:
        """UTF-8 bytes before each line starts, and the count of them all."""
        return self.u16 if self.plain else self._before(byte_length)

    def _before(self, measure: Callable[[str], int]) -> list[int]:
        counted: list[int] = []
        total = 0
        for line in self.lines:
            counted.append(total)
            total += measure(line) + 1
        counted.append(total - 1)
        return counted


def index_layer(text: str, layer_sha256: str, normaliser: str) -> LayerIndex:
    """`indexLayer`: the index of a layer whose text is in characters."""
    return LayerIndex(text, layer_sha256, normaliser)


def line_at(index: LayerIndex, u16: int) -> int:
    """The line a unit offset falls in."""
    return max(bisect.bisect_right(index.u16, u16, 0, len(index.lines)) - 1, 0)


def point_at(index: LayerIndex, u16: int) -> int:
    """The character offset of a unit offset that is in the layer."""
    line = line_at(index, u16)
    return index.points[line] + count_points(index.text[index.u16[line] : u16])


def byte_at(index: LayerIndex, u16: int) -> int:
    """The UTF-8 byte offset of a unit offset that is in the layer."""
    line = line_at(index, u16)
    return index.bytes[line] + byte_length(index.text[index.u16[line] : u16])


def layer_points(index: LayerIndex) -> int:
    """Characters in the whole layer."""
    return index.points[-1]


def _inside_pair(text: str, u16: int) -> bool:
    return 0 < u16 < len(text) and is_low(text[u16]) and is_high(text[u16 - 1])


def _tail(text: str, points: int) -> str:
    """The last `points` characters of text in units, without splitting a pair."""
    unit = len(text)
    taken = 0
    while unit > 0 and taken < points:
        unit -= 1
        if unit > 0 and is_low(text[unit]) and is_high(text[unit - 1]):
            unit -= 1
        taken += 1
    return text[unit:]


def _head(text: str, points: int) -> str:
    """The first `points` characters of text in units, without splitting a pair."""
    unit = 0
    taken = 0
    while unit < len(text) and taken < points:
        unit += 2 if is_high(text[unit]) and unit + 1 < len(text) and is_low(text[unit + 1]) else 1
        taken += 1
    return text[:unit]


def span_at(index: LayerIndex, start: int, end: int) -> Span:
    """The span at a half-open range of units: both published ranges, the text and its context.

    An end that falls between the halves of a character is moved outwards, so a span never
    holds half of one.
    """
    text = index.text
    first = max(0, min(start, len(text)))
    if _inside_pair(text, first):
        first -= 1
    last = max(first, min(end, len(text)))
    if _inside_pair(text, last):
        last += 1
    prefix = _tail(text[max(0, first - CONTEXT_POINTS * 2) : first], CONTEXT_POINTS)
    suffix = _head(text[last : min(len(text), last + CONTEXT_POINTS * 2)], CONTEXT_POINTS)
    return Span(
        index.layer_sha256,
        point_at(index, first),
        point_at(index, last),
        byte_at(index, first),
        byte_at(index, last),
        text[first:last],
        prefix or None,
        suffix or None,
    )


def span_of_lines(index: LayerIndex, first: int, last: int) -> Span:
    """The span covering whole lines `first` to `last`, both counted from 0 and both included."""
    opening = max(0, min(first, len(index.lines) - 1))
    closing = max(opening, min(last, len(index.lines) - 1))
    return span_at(index, index.u16[opening], index.u16[closing] + len(index.lines[closing]))
