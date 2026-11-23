"""Run the port on what `compare_spans.mjs` ran the TypeScript on, and print where the two part.

Usage: PYTHONPATH=. python scripts/fixtures/compare_spans.py <out.jsonl>

A differential check of the span resolvers against the TypeScript on input put together at
random, for looking at a change to a resolver before its cases are written. It is not a
gate and CI does not run it: the recorded fixtures are the evidence. `compare_spans.mjs`
says how the file is made.

Each line is the name of a call, what it was given, and what the TypeScript returned or the
name of what it threw. A call by a name the recorded cases use is made as the tests make it.
One whose name opens with `layer:` is made of a layer indexed here from the text on the line.
Where the TypeScript threw, the port has to raise.
"""

from __future__ import annotations

import collections
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

from rewire_evidence import aligned, gridspans, offsets, prose, regions, sheets
from rewire_evidence.canonical import JsonValue, js_utf8
from rewire_evidence.offsets import LayerIndex, to_units
from tests.spanned import CALLS, extent, field, resolution

#: Differences shown of each call before the rest are only counted.
SHOWN = 3


def indexed(layer: str, normaliser: str) -> LayerIndex:
    """A layer as the recorder indexed it: named by the digest of its text as Node writes it."""
    return offsets.index_layer(layer, hashlib.sha256(js_utf8(layer)).hexdigest(), normaliser)


def _every(index: LayerIndex, read: Callable[[int], object]) -> list[object]:
    return [read(line) for line in range(len(index.lines))]


def _windows(index: LayerIndex, line: int) -> list[object]:
    return [
        extent(aligned.table_window(index, line)),
        extent(aligned.wider_window(index, line)),
        extent(aligned.block_above(index, line)),
    ]


def _reanchor(index: LayerIndex, more: str, start: int, end: int) -> list[object]:
    span = offsets.span_at(index, start, end)
    moved = indexed(f"{paired_text(index)}{more}", index.normaliser)
    return [resolution(prose.reanchor(span, index)), resolution(prose.reanchor(span, moved))]


def paired_text(index: LayerIndex) -> str:
    """The text of a layer as it was given, before it was put into units."""
    return index.text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


#: The calls made of a layer indexed from the line, by the name after `layer:`.
OF_A_LAYER: dict[str, Callable[..., object]] = {
    "spanAt": lambda index, start, end: offsets.span_at(index, start, end).row(),
    "spanOfLines": lambda index, first, last: offsets.span_of_lines(index, first, last).row(),
    "cellsOf": lambda index: _every(
        index, lambda line: [field(cell) for cell in aligned.cells_of(index, line)]
    ),
    "fieldsOf": lambda index: _every(
        index, lambda line: [field(found) for found in regions.fields_of(index, line, ",")]
    ),
    "fieldsOfLine": lambda index: _every(
        index, lambda line: [field(found) for found in sheets.fields_of_line(index, line)]
    ),
    "windows": lambda index: _every(index, lambda line: _windows(index, line)),
    "captionLines": lambda index, value: [
        aligned.caption_lines(index, word, to_units(value)) for word in ("Table", "Figure")
    ],
    "resolveQuote": lambda index, quote, whole: resolution(
        prose.resolve_quote(index, to_units(quote), whole=whole)
    ),
    "resolveSectionParagraph": lambda index, name: resolution(
        prose.resolve_section_paragraph(index, to_units(name), 1)
    ),
    "resolvePage": lambda index: resolution(regions.resolve_page(index, 1)),
    "resolveLineRange": lambda index, first, last: resolution(regions.resolve_line_range(index, first, last)),
    "resolveDelimitedCell": lambda index, line, column: resolution(
        regions.resolve_delimited_cell(index, line=line, column=to_units(column))
    ),
    "unitOfPoint": gridspans.unit_of_point,
    "reanchor": _reanchor,
}


def made(name: str, given: list[JsonValue]) -> object:
    """One call, made of the port."""
    if not name.startswith("layer:"):
        return CALLS[name](*given)
    layer, normaliser, *rest = given
    return OF_A_LAYER[name.removeprefix("layer:")](indexed(str(layer), str(normaliser)), *rest)


def compare(path: Path) -> int:
    """Make every call of a file and print where the port parts from the TypeScript. Returns how many do."""
    calls = 0
    parted: collections.Counter[str] = collections.Counter()
    # A line ends at a newline and nowhere else: the text on it holds the other line breaks.
    for line in filter(None, path.read_text("utf-8").split("\n")):
        name, given, expected = json.loads(line)
        calls += 1
        threw = isinstance(expected, dict) and "thrown" in expected
        try:
            got = made(name, given)
        except (TypeError, ValueError) as error:
            got = {"thrown": type(error).__name__}
            same = threw
        else:
            same = not threw and got == expected
        if not same:
            parted[name] += 1
            if parted[name] <= SHOWN:
                print(f"{name} {json.dumps([given, expected, got], ensure_ascii=True)[:900]}")
    print(f"{calls:,} calls, {sum(parted.values()):,} differ{f': {dict(parted)}' if parted else ''}")
    return sum(parted.values())


if __name__ == "__main__":
    # The recorded cases hold a document nested deeper than Python follows a value by default.
    sys.setrecursionlimit(100_000)
    sys.exit(1 if compare(Path(sys.argv[1])) else 0)
