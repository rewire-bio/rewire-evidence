"""Resolvers that address a region of a layer directly: a delimited cell, a line range, a page.

This is `shared/omics/spans/regions.ts`. These locators need no caption and no label,
because the address is the answer. Each counting convention is stated:

    line N        the Nth line of the layer, counted from 1, a newline being the separator.
                  It is not a count of records: a quoted field that holds a newline is two
                  lines here.
    row N         the same, so "row 12" and "line 12" address one line.
    A1 notation   column letters from A, row number from 1, the header being row 1.
    page N        the Nth region of a PDF text layer between form feeds, counted from 1.

A number arrives as JavaScript's `Number` gives it, which is a double, and is printed as
JavaScript prints one. All text is in units.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from rewire_evidence.aligned import normalise_label
from rewire_evidence.canonical import js_number
from rewire_evidence.js import SPACE
from rewire_evidence.offsets import LayerIndex, span_at, span_of_lines
from rewire_evidence.resolution import Resolution, Resolved, refuse

#: Normalisers whose layers are the file itself, so a delimiter in them is the file's.
FILE_NORMALISERS: Final = frozenset({"text-utf8@1"})

_A1: Final = re.compile(r"([A-Z]{1,3})([0-9]{1,6})")
_LETTERS: Final = 26


@dataclass(frozen=True)
class Field:
    """One field of a delimited line, with its unit offsets into the layer."""

    text: str
    start: int
    end: int


def shown(number: float) -> str:
    """A number as a JavaScript template writes it into a message."""
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "Infinity" if number > 0 else "-Infinity"
    return js_number(number)


def whole(number: float) -> int:
    """A number that names an item of a list, as an integer.

    Raises `TypeError` for one that is no integer. JavaScript reads `undefined` at such an
    index and throws a TypeError when the resolver goes on to use it.
    """
    if not float(number).is_integer():
        raise TypeError(f"{shown(number)} is not the index of anything")
    return int(number)


def delimiter_of(line: str) -> str | None:
    """The delimiter a line uses, read off the line itself. A tab beats a comma."""
    if "\t" in line:
        return "\t"
    return "," if "," in line else None


def fields_of(index: LayerIndex, line: int, delimiter: str) -> list[Field]:
    """Split one line into fields, with RFC 4180 double quoting.

    A quoted field keeps its quotes out of `text` and inside its offsets, so a span over the
    field covers what the file prints.
    """
    text = index.lines[line]
    base = index.u16[line]
    fields: list[Field] = []
    start = 0
    quoted = False
    for at in range(len(text) + 1):
        if at == len(text) or (text[at] == delimiter and not quoted):
            raw = text[start:at]
            inner = raw[1:-1].replace('""', '"') if len(raw) > 1 and raw[0] == raw[-1] == '"' else raw
            fields.append(Field(inner.strip(SPACE), base + start, base + at))
            start = at + 1
        elif text[at] == '"':
            quoted = not quoted
    return fields


def column_index(letters: str) -> int:
    """A1 column letters as a column counted from 0: A is 0, Z is 25, AA is 26."""
    value = 0
    for character in letters.upper():
        value = value * _LETTERS + (ord(character) - 64)
    return value - 1


def _header_line(index: LayerIndex) -> int:
    """The first line of the layer that holds any printed text, which is where a header is."""
    for line, text in enumerate(index.lines):
        if text.strip(SPACE):
            return line
    return 0


def _named(index: LayerIndex, headers: Sequence[Field], column: str) -> int | Resolution:
    wanted = normalise_label(column)
    matches = [at for at, field in enumerate(headers) if normalise_label(field.text) == wanted]
    if not matches:
        return refuse("column_not_found", f'no header field reads "{column}"', index.layer_sha256)
    if len(matches) > 1:
        return refuse("column_ambiguous", f'{len(matches)} header fields read "{column}"', index.layer_sha256)
    return matches[0]


def resolve_delimited_cell(  # noqa: C901 - one reason to a return
    index: LayerIndex, *, line: float | None = None, cell: str | None = None, column: str | None = None
) -> Resolution:
    """A cell of a delimited file, by line and column header or by A1 notation."""
    layer = index.layer_sha256
    if index.normaliser not in FILE_NORMALISERS:
        return refuse(
            "no_column_geometry",
            f"{index.normaliser} is not the file itself, so it has no delimited columns",
            layer,
        )
    header = _header_line(index)
    delimiter = delimiter_of(index.lines[header])
    if delimiter is None:
        return refuse("no_column_geometry", "the first line of this layer holds no tab and no comma", layer)

    at: int | None = None
    if cell:
        found = _A1.fullmatch(cell.upper())
        if not found:
            return refuse("no_resolver_for_kind", f'"{cell}" is not A1 notation', layer)
        at = column_index(found[1])
        number: float = int(found[2]) - 1
    elif line is not None:
        number = line - 1
    else:
        return refuse("no_anchor", "the locator names neither a line nor a cell", layer)
    if number < 0 or number >= len(index.lines):
        return refuse(
            "line_out_of_range",
            f"line {shown(number + 1)} is outside a layer of {len(index.lines)} lines",
            layer,
        )
    row = whole(number)

    fields = fields_of(index, row, delimiter)
    headers = fields_of(index, header, delimiter)
    if at is None:
        if column is None:
            return refuse("column_not_found", "the locator names no column", layer)
        named = _named(index, headers, column)
        if not isinstance(named, int):
            return named
        at = named
    if at >= len(fields):
        return refuse(
            "cell_empty",
            f"line {row + 1} holds {len(fields)} fields and the column is number {at + 1}",
            layer,
        )
    field = fields[at]
    if not field.text:
        return refuse("cell_empty", f"line {row + 1} prints nothing in column {at + 1}", layer)
    return Resolved(
        "delimited_cell",
        span_at(index, field.start, field.end),
        layer,
        row_label=fields[0].text,
        column_header=headers[at].text if at < len(headers) else None,
    )


def resolve_keyed_row(  # noqa: C901 - one reason to a return
    index: LayerIndex, column: str, constraints: Sequence[tuple[str | None, str]]
) -> Resolution:
    """A cell of a delimited file whose row is identified by keys and not by a line number.

    A constraint is a key and a value. One whose key names a header field is checked against
    that field. One whose key names nothing in the header, or is `None`, is met by any field
    on the line holding the value. Every constraint has to hold, and exactly one row has to
    be left.
    """
    layer = index.layer_sha256
    if index.normaliser not in FILE_NORMALISERS:
        return refuse(
            "no_column_geometry", f"{index.normaliser} is not the file itself, so it has no keyed rows", layer
        )
    if not constraints:
        return refuse(
            "no_row_in_locator", "the locator names a column and nothing that identifies a row", layer
        )
    header = _header_line(index)
    delimiter = delimiter_of(index.lines[header])
    if delimiter is None:
        return refuse("no_column_geometry", "the first line of this layer holds no tab and no comma", layer)
    header_fields = fields_of(index, header, delimiter)
    headers = [normalise_label(field.text) for field in header_fields]
    named = _named(index, header_fields, column)
    if not isinstance(named, int):
        return named

    wanted = [
        (
            -1 if key is None or normalise_label(key) not in headers else headers.index(normalise_label(key)),
            normalise_label(value),
        )
        for key, value in constraints
    ]
    rows: list[list[Field]] = []
    line = header + 1
    while line < len(index.lines) and len(rows) < 3:  # noqa: PLR2004 - two rows settle that it is ambiguous
        if index.lines[line].strip(SPACE):
            fields = fields_of(index, line, delimiter)
            values = [normalise_label(field.text) for field in fields]
            if all(
                (at < len(values) and values[at] == value) if at >= 0 else value in values
                for at, value in wanted
            ):
                rows.append(fields)
        line += 1
    if not rows:
        return refuse(
            "row_key_unmatched", f"no line satisfies all {len(wanted)} keys the locator names", layer
        )
    if len(rows) > 1:
        return refuse("row_ambiguous", "more than one line satisfies the keys the locator names", layer)

    fields = rows[0]
    if named >= len(fields):
        return refuse(
            "cell_empty", f"the row holds {len(fields)} fields and the column is number {named + 1}", layer
        )
    field = fields[named]
    if not field.text:
        return refuse("cell_empty", f'the row prints nothing under "{column}"', layer)
    return Resolved(
        "delimited_cell",
        span_at(index, field.start, field.end),
        layer,
        row_label=fields[0].text,
        column_header=header_fields[named].text,
    )


def resolve_line_range(index: LayerIndex, first: float, last: float | None = None) -> Resolution:
    """A line, or a run of lines, as a span. Both ends are counted from 1 and both are included."""
    if not float(first).is_integer() or first < 1:
        return refuse("line_out_of_range", f'"{shown(first)}" is not a line number', index.layer_sha256)
    if first > len(index.lines):
        return refuse(
            "line_out_of_range",
            f"line {shown(first)} is outside a layer of {len(index.lines)} lines",
            index.layer_sha256,
        )
    end = first if last is None else last
    if math.isnan(end):
        whole(end)
    closing = min(max(end, first), len(index.lines))
    return Resolved(
        "line_range", span_of_lines(index, whole(first) - 1, whole(closing) - 1), index.layer_sha256
    )


def resolve_page(index: LayerIndex, page: float) -> Resolution:
    """One page of a PDF text layer, between form feeds, counted from 1."""
    text = index.text
    if "\f" not in text:
        return refuse(
            "page_out_of_range",
            f"{index.normaliser} writes no page breaks, so a page cannot be addressed",
            index.layer_sha256,
        )
    if not float(page).is_integer() or page < 1:
        return refuse("page_out_of_range", f'"{shown(page)}" is not a page number', index.layer_sha256)
    start = 0
    seen = 1
    while seen < page:
        following = text.find("\f", start)
        if following < 0:
            pages = text.count("\f") + 1
            return refuse(
                "page_out_of_range",
                f"page {shown(page)} is outside a layer of {pages} pages",
                index.layer_sha256,
            )
        start = following + 1
        seen += 1
    end = text.find("\f", start)
    return Resolved("page", span_at(index, start, len(text) if end < 0 else end), index.layer_sha256)
