"""jats-table-grid@1 and html-table-grid@1: the table grid as a second layer.

This is `shared/archive/layers/grid/`: `types.ts`, `tables.ts`, `header.ts` and `index.ts`.
A text layer flattens a table: a row is one line with the cells a space apart, so the column
a cell sat in is gone. A grid keeps the geometry the markup carries. It is built from the
same stored bytes as the text layer, by the same scan, and says which characters of that
layer are the cell at a row and a column.

The layer bytes are JSON lines. The first line names the text layer the offsets are into,
and each line after it is one table, in document order. Keys are sorted at every depth and
every number is an integer.

Nothing the markup wrote is inferred. A span is the number the markup writes, a header row
is a row the markup marks, and a cell that prints nothing carries no offsets. The one
inference is kept apart: a table whose markup marks no header row at all may carry a
candidate first row in `inferred_header`, chosen by the rule `candidate_header` holds.

A grid whose scan does not reproduce the text layer byte for byte is not returned at all,
because its offsets would point into a document nobody stored.

Offsets are characters, counted from 0, half open. A document decoded from UTF-16 that
splits a surrogate pair across a tag is counted here with the two halves apart, where the
TypeScript counts the pair they form in the layer as one. No stored object does that.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from rewire_evidence import markup
from rewire_evidence.canonical import JsonObject, canonical_json, js_utf8, sha256_hex
from rewire_evidence.classify import decode, encoding_of
from rewire_evidence.js import SPACE, SPACE_CLASS, lower, parse_int, trim
from rewire_evidence.textlayer import Layer, LayerFailure, character_count

#: The grid normalisers, each with the text normaliser it shadows.
GRID_NORMALISERS: Final[Mapping[str, str]] = {
    "jats-table-grid@1": markup.XML_NORMALISER,
    "html-table-grid@1": markup.HTML_NORMALISER,
}
_RULES: Final[Mapping[str, markup.ScanRules]] = {
    "jats-table-grid@1": markup.XML_RULES,
    "html-table-grid@1": markup.HTML_RULES,
}

#: A cell span the markup may write. Past this the cell is placed as one, with the fact recorded.
MAX_SPAN: Final = 1000
#: Coordinates one table may cover.
MAX_COORDINATES: Final = 1_000_000
#: Columns one table may have.
COLUMN_LIMIT: Final = 4096

#: The elements a caption or a label cannot run past.
_STRUCTURE: Final = frozenset({"table", "table-wrap", "thead", "tbody", "tfoot", "tr", "td", "th"})
_SPANS: Final = {
    name: re.compile(
        rf"(?:^|[{SPACE_CLASS}]){name}[{SPACE_CLASS}]*=[{SPACE_CLASS}]*"
        rf"(?:\"([^\"]*)\"|'([^']*)'|([^{SPACE_CLASS}\"'>]+))",
        re.IGNORECASE | re.ASCII,
    )
    for name in ("rowspan", "colspan")
}


@dataclass
class _Range:
    """A half open range of the untidied scan. -1 means nothing was written there."""

    start: int = -1
    end: int = -1

    def extend(self, start: int, end: int) -> None:
        if self.start < 0:
            self.start = start
        self.end = end


@dataclass
class _Cell:
    header: bool
    row_span: int
    column_span: int
    range: _Range = field(default_factory=_Range)
    row: int = -1
    column: int = -1


@dataclass
class _Row:
    #: The row sits inside a thead.
    marked: bool
    cells: list[_Cell] = field(default_factory=list)


@dataclass
class _Table:
    index: int
    rows: list[_Row] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    label: _Range | None = None
    caption: _Range | None = None
    # Open state while the scan runs.
    row: _Row | None = None
    cell: _Cell | None = None
    head: int = 0
    #: The document holds a thead somewhere in this table.
    any_head: bool = False


@dataclass
class _Wrap:
    label: _Range | None = None
    caption: _Range | None = None
    tables: list[_Table] = field(default_factory=list)


@dataclass
class _Capture:
    """The label or caption being read, and what it belongs to."""

    name: str
    owner: _Table | _Wrap
    range: _Range = field(default_factory=_Range)
    depth: int = 1


def _span(attributes: str, name: str, problems: list[str], where: str) -> int:
    """A span attribute as a whole number of cells. Anything else is one cell and a recorded problem."""
    found = _SPANS[name].search(attributes)
    if not found:
        return 1
    written = next(held for held in found.groups() if held is not None)
    value = parse_int(trim(written))
    if not value.is_integer() or value < 1:
        problems.append(
            f'{where} writes {name}="{written}", which is not a count of cells, so it is placed as one'
        )
        return 1
    if value > MAX_SPAN:
        problems.append(
            f'{where} writes {name}="{written}", over the {MAX_SPAN} cell limit, so it is placed as one'
        )
        return 1
    return int(value)


class _Reader(markup.ScanEvents):
    """Every table the markup writes, in document order, with each cell's range in the untidied scan.

    A document that omits `</td>` or `</tr>` is closed at the next cell, row, section or
    table, which is what a browser does. Nothing else is repaired.
    """

    def __init__(self) -> None:
        self.tables: list[_Table] = []
        self.opened: list[_Table] = []
        self.wraps: list[_Wrap] = []
        self.capture: _Capture | None = None

    def _start_capture(self, name: str) -> None:
        if self.capture:
            if self.capture.name == name:
                self.capture.depth += 1
            return
        owner = self.opened[-1] if self.opened else self.wraps[-1] if self.wraps else None
        if owner:
            self.capture = _Capture(name, owner)

    def _close_capture(self) -> None:
        """End the capture wherever it has got to, so an unclosed caption is not the rest of the document."""
        capture, self.capture = self.capture, None
        if capture and capture.range.start >= 0 and getattr(capture.owner, capture.name) is None:
            setattr(capture.owner, capture.name, capture.range)

    def _end_capture(self, name: str) -> None:
        if not self.capture or self.capture.name != name:
            return
        self.capture.depth -= 1
        if self.capture.depth <= 0:
            self._close_capture()

    def _open_cell(self, table: _Table, name: str, attributes: str) -> None:
        table.cell = None
        if not table.row:
            table.row = _Row(table.head > 0)
            table.rows.append(table.row)
            table.problems.append(f"a {name} outside any tr, placed in a row of its own")
        where = f"table {table.index}, row {len(table.rows)}"
        cell = _Cell(
            header=name == "th",
            row_span=_span(attributes, "rowspan", table.problems, where),
            column_span=_span(attributes, "colspan", table.problems, where),
        )
        table.row.cells.append(cell)
        table.cell = cell

    def open(self, name: str, attributes: str, at: int) -> None:  # noqa: ARG002, C901 - one element to a branch
        if name in _STRUCTURE:
            self._close_capture()
        if name == "table-wrap":
            self.wraps.append(_Wrap())
            return
        if name == "table":
            table = _Table(len(self.tables))
            self.tables.append(table)
            self.opened.append(table)
            if self.wraps:
                self.wraps[-1].tables.append(table)
            return
        if name in {"caption", "label"}:
            self._start_capture(name)
            return
        if not self.opened:
            return
        table = self.opened[-1]
        if name in {"thead", "tbody", "tfoot", "tr"}:
            table.row = table.cell = None
            if name == "thead":
                table.head += 1
                table.any_head = True
            elif name == "tr":
                table.row = _Row(table.head > 0)
                table.rows.append(table.row)
        elif name in {"td", "th"}:
            self._open_cell(table, name, attributes)

    def close(self, name: str, at: int) -> None:  # noqa: ARG002, C901 - one element to a branch
        if name in {"caption", "label"}:
            self._end_capture(name)
            return
        if name in {"table", "table-wrap"}:
            self._close_capture()
        if name == "table-wrap":
            if self.wraps:
                wrap = self.wraps.pop()
                for table in wrap.tables:
                    table.label = table.label or wrap.label
                    table.caption = table.caption or wrap.caption
            return
        if not self.opened:
            return
        table = self.opened[-1]
        if name in {"td", "th"}:
            table.cell = None
        elif name == "tr":
            table.row = table.cell = None
        elif name == "thead":
            table.head = max(0, table.head - 1)
        elif name == "table":
            table.row = table.cell = None
            self.opened.pop()

    def text(self, start: int, end: int) -> None:
        if self.capture:
            self.capture.range.extend(start, end)
        # A nested table's text is inside the outer cell as well, so every open cell grows.
        for table in self.opened:
            if table.cell:
                table.cell.range.extend(start, end)


def _place(table: _Table) -> tuple[int, dict[int, _Cell]]:  # noqa: C901 - the placing rule is one loop
    """Where every cell sits, once the spans are expanded: the columns, and the cell over each coordinate.

    A cell takes the first free coordinate on its row and then occupies its span. Nothing is
    moved to make a table rectangular, so a row that is short is short.
    """
    taken: set[int] = set()
    cover: dict[int, _Cell] = {}
    columns = coordinates = 0
    overflowed = False
    for index, row in enumerate(table.rows):
        column = 0
        for cell in row.cells:
            while column < COLUMN_LIMIT and index * COLUMN_LIMIT + column in taken:
                column += 1
            if column >= COLUMN_LIMIT:
                if not overflowed:
                    table.problems.append(
                        f"the table runs past column {COLUMN_LIMIT}, so the rest of it is not placed"
                    )
                    overflowed = True
                break
            cell.row = index
            cell.column = column
            coordinates += cell.row_span * cell.column_span
            if coordinates > MAX_COORDINATES:
                if not overflowed:
                    table.problems.append(
                        f"the spans cover more than {MAX_COORDINATES} coordinates, "
                        "so the rest of the table is not placed"
                    )
                    overflowed = True
                break
            for r in range(index, index + cell.row_span):
                for c in range(column, min(column + cell.column_span, COLUMN_LIMIT)):
                    key = r * COLUMN_LIMIT + c
                    taken.add(key)
                    if r < len(table.rows):
                        cover.setdefault(key, cell)
            column += cell.column_span
            columns = max(columns, column)
    return min(columns, COLUMN_LIMIT), cover


def _header_rows(table: _Table) -> int:
    """Leading rows the markup marks as the header: by `thead`, or by writing every cell as `th`."""
    count = 0
    for row in table.rows:
        marked = row.marked if table.any_head else bool(row.cells) and all(cell.header for cell in row.cells)
        if not marked:
            break
        count += 1
    return count


@dataclass(frozen=True)
class GridRange:
    """A run of characters in the text layer."""

    start: int
    end: int
    text: str

    def to_json(self) -> JsonObject:
        """The range as the layer writes it."""
        return {"start": self.start, "end": self.end, "text": self.text}


def _printed(trace: markup.TidyTrace, raw: _Range | None) -> GridRange | None:
    """The layer characters a raw range became, trimmed of the whitespace around it, or `None`."""
    if raw is None or raw.start < 0:
        return None
    first = last = -1
    for at in trace.out[raw.start : raw.end]:
        if at < 0 or trace.text[at] in SPACE:
            continue
        if first < 0:
            first = at
        last = at
    if first < 0:
        return None
    return GridRange(first, last + 1, trace.text[first : last + 1])


@dataclass(frozen=True)
class GridCell:
    """One cell the markup wrote, placed on the grid.

    `row` and `column` are the zero based coordinates of its top left corner. `start` and
    `end` are character offsets into the text layer and `None` when the cell prints nothing.
    """

    row: int
    column: int
    row_span: int
    column_span: int
    #: Whether the markup wrote the cell as a header cell.
    header: bool
    start: int | None
    end: int | None
    #: Exactly the characters the text layer holds between the offsets.
    text: str

    def to_json(self) -> JsonObject:
        """The cell as the layer writes it."""
        return {
            "row": self.row,
            "column": self.column,
            "row_span": self.row_span,
            "column_span": self.column_span,
            "header": self.header,
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }


@dataclass(frozen=True)
class InferredHeader:
    """A header row no markup marked, put forward by the rule named on it."""

    rule: str
    rows: int
    #: One path per column, the same shape as the marked `headers`.
    headers: list[list[str]]


@dataclass(frozen=True)
class GridTable:
    """One table."""

    #: Zero based position among the tables of the layer. A nested table counts as its own.
    index: int
    rows: int
    columns: int
    #: Leading rows the markup marks as header rows.
    header_rows: int
    #: For each column, the heading at each header row, top level first.
    headers: list[list[str]]
    #: A header row the markup marks nowhere and the rule puts forward, or `None`.
    inferred_header: InferredHeader | None
    #: The label the markup gives the table, such as "Table 3".
    label: GridRange | None
    caption: GridRange | None
    #: The cells the markup wrote, in row then column order.
    cells: list[GridCell]
    #: What the parse could not do, one line each.
    problems: list[str]

    def to_json(self) -> JsonObject:
        """The table as the layer writes it, with no member for a header that was not inferred."""
        held: JsonObject = {
            "index": self.index,
            "rows": self.rows,
            "columns": self.columns,
            "header_rows": self.header_rows,
            "headers": [list(path) for path in self.headers],
            "label": self.label.to_json() if self.label else None,
            "caption": self.caption.to_json() if self.caption else None,
            "cells": [cell.to_json() for cell in self.cells],
            "problems": list(self.problems),
        }
        if self.inferred_header:
            held["inferred_header"] = {
                "rule": self.inferred_header.rule,
                "rows": self.inferred_header.rows,
                "headers": [list(path) for path in self.inferred_header.headers],
            }
        return held


@dataclass(frozen=True)
class Grid:
    """One grid layer."""

    normaliser: str
    #: The text layer the offsets are into, by its own hash.
    text_layer_sha256: str
    text_normaliser: str
    tables: list[GridTable]


# ---- the candidate header row ---------------------------------------------------------

#: The rule that chose a candidate, recorded on it.
CANDIDATE_RULE: Final = "first-row@1"
MIN_COLUMNS: Final = 3
MIN_ROWS: Final = 3

_S: Final = f"[{SPACE_CLASS}]"
_SIGN: Final = r"[+\-‐‑−]?"
#: A number as a paper prints one, the whole of the cell: a comparator or a bracket, digits
#: with thousands separators and a decimal part, an exponent, a unit, and a closing bracket
#: or a footnote mark.
_NUMBER: Final = re.compile(
    rf"[(\[{{<>≤≥~≈]*{_S}*{_SIGN}[0-9][0-9,  ]*(?:\.[0-9]+)?"
    rf"(?:{_S}*(?:[eE]|[×x]{_S}*10){_S}*\^?{_S}*{_SIGN}[0-9]+)?"
    rf"(?:{_S}*(?:bp|kb|Mb|Gb|kB|MB|GB|min|ms|mM|nM|%|‰|[KkMGBbshm]))?"
    r"[)\]}%*†‡]*"
)
#: What a table prints where it has no value. A heading is none of these.
_PLACEHOLDERS: Final = frozenset(
    {
        "-", "--", "–", "—", "−", "n/a", "na", "nd", "ns", "yes", "no", "true", "false",
        "✓", "✔", "✕", "✖", "✗", "✘", "√", "×", "•",
    }
)  # fmt: skip
#: Where one part of a compound value ends and the next begins.
_PARTS: Final = re.compile(rf"[{SPACE_CLASS}±]*(?:±|\+/-|/|–|—|,|;|→){_S}*|{_S}+")
_SPACES: Final = re.compile(f"{_S}+")


def prints_number(text: str) -> bool:
    """Whether the cell prints a number, alone or as a compound of numbers."""
    trimmed = trim(text)
    if not trimmed:
        return False
    if _NUMBER.fullmatch(trimmed):
        return True
    parts = [part for part in _PARTS.split(trimmed) if trim(part)]
    return len(parts) > 1 and all(_NUMBER.fullmatch(part) for part in parts)


def prints_value(text: str) -> bool:
    """Whether the cell prints a value: a number, or the mark a table uses for no number."""
    trimmed = trim(text)
    return lower(trimmed) in _PLACEHOLDERS or prints_number(trimmed)


def _same_heading(text: str) -> str:
    """A heading as two are told apart: without the whitespace a renderer put in it, or its case."""
    return lower(trim(_SPACES.sub(" ", text)))


def _cover(cells: Sequence[GridCell]) -> dict[tuple[int, int], GridCell]:
    """The cell covering each coordinate, a spanning cell covering all of its own."""
    found: dict[tuple[int, int], GridCell] = {}
    for cell in cells:
        for row in range(cell.row, cell.row + cell.row_span):
            for column in range(cell.column, cell.column + cell.column_span):
                found.setdefault((row, column), cell)
    return found


def candidate_header(  # noqa: C901, PLR0912 - one test of the rule to a return
    rows: int, columns: int, cells: Sequence[GridCell], *, thead: bool
) -> tuple[InferredHeader | None, str | None]:
    """The candidate header row of a table whose markup marks none, or the test that declined it.

    A row is the candidate only when it is the first row and: the markup marks no header
    elsewhere; the table has three columns and three rows; the first row writes its own
    unspanned cell in every column; every cell of it prints something, bar the corner; none
    prints a value; none repeats; some column prints values below it; and the row under it
    prints a value. `thead` says the markup wrote a thead somewhere in the table.
    """
    marked = [cell for cell in cells if cell.header]
    if thead or (marked and not any(cell.row == 0 for cell in marked)):
        return None, "marked_elsewhere"
    if columns < MIN_COLUMNS:
        return None, "too_few_columns"
    if rows < MIN_ROWS:
        return None, "too_few_rows"

    first = {cell.column: cell for cell in cells if cell.row == 0}
    if len(first) != columns:
        return None, "first_row_incomplete"
    heading: list[str] = []
    for column in range(columns):
        cell = first.get(column)
        if not cell:
            return None, "first_row_incomplete"
        if cell.row_span != 1 or cell.column_span != 1:
            return None, "first_row_spans"
        heading.append(trim(cell.text))

    if not all(heading[1:]):
        return None, "first_row_blank_cell"
    named = [text for text in heading if text]
    if any(prints_value(text) for text in named):
        return None, "first_row_prints_a_value"
    if len({_same_heading(text) for text in named}) != len(named):
        return None, "first_row_repeats_a_heading"

    covered = _cover(cells)
    columns_of_values = 0
    for column in range(columns):
        printed = [
            text
            for row in range(1, rows)
            if (row, column) in covered and (text := trim(covered[row, column].text))
        ]
        values = sum(1 for text in printed if prints_number(text))
        if values >= 2 and values * 2 >= len(printed):  # noqa: PLR2004 - at least two values, and at least half
            columns_of_values += 1
    if not columns_of_values:
        return None, "no_column_of_values_below"

    below = [covered[1, column].text for column in range(columns) if (1, column) in covered]
    if not any(prints_value(text) for text in below if trim(text)):
        return None, "row_below_prints_no_value"

    return InferredHeader(CANDIDATE_RULE, 1, [[text] for text in heading]), None


# ---- the grid -------------------------------------------------------------------------


def grid_of(
    source: str, rules: markup.ScanRules, normaliser: str, text_normaliser: str, text_layer_sha256: str
) -> tuple[Grid, str]:
    """The grid for one markup document, and the text its scan gives.

    `source` is the decoded document and `rules` are the text normaliser's own, so the text
    this walks is the text the layer holds. The caller checks that by hashing it.
    """
    reader = _Reader()
    trace = markup.tidy_traced(markup.scan_raw(source, rules, reader))
    built: list[GridTable] = []
    for table in reader.tables:
        columns, cover = _place(table)
        header_rows = _header_rows(table)
        placed = [cell for row in table.rows for cell in row.cells if cell.row >= 0]
        placed.sort(key=lambda cell: (cell.row, cell.column))
        cells = [_cell(_printed(trace, cell.range), cell) for cell in placed]
        headers = [
            [_heading(trace, cover.get(level * COLUMN_LIMIT + column)) for level in range(header_rows)]
            for column in range(columns)
        ]
        # The one inference the grid carries, and only where the markup marks no header row.
        inferred = None
        if header_rows == 0:
            inferred, _ = candidate_header(len(table.rows), columns, cells, thead=table.any_head)
        built.append(
            GridTable(
                index=table.index,
                rows=len(table.rows),
                columns=columns,
                header_rows=header_rows,
                headers=headers,
                inferred_header=inferred,
                label=_printed(trace, table.label),
                caption=_printed(trace, table.caption),
                cells=cells,
                problems=table.problems,
            )
        )
    return Grid(normaliser, text_layer_sha256, text_normaliser, built), trace.text


def _cell(found: GridRange | None, cell: _Cell) -> GridCell:
    return GridCell(
        row=cell.row,
        column=cell.column,
        row_span=cell.row_span,
        column_span=cell.column_span,
        header=cell.header,
        start=found.start if found else None,
        end=found.end if found else None,
        text=found.text if found else "",
    )


def _heading(trace: markup.TidyTrace, cell: _Cell | None) -> str:
    found = _printed(trace, cell.range) if cell else None
    return found.text if found else ""


def header_of(grid: Grid) -> JsonObject:
    """The header line of a grid layer."""
    cells = [cell for table in grid.tables for cell in table.cells]
    return {
        "normaliser": grid.normaliser,
        "text_layer_sha256": grid.text_layer_sha256,
        "text_normaliser": grid.text_normaliser,
        "tables": len(grid.tables),
        "cells": len(cells),
        "empty_cells": sum(1 for cell in cells if cell.start is None),
    }


def serialise_grid(grid: Grid) -> str:
    """The layer bytes: the header, then one line per table."""
    lines = [canonical_json(header_of(grid)), *(canonical_json(table.to_json()) for table in grid.tables)]
    return "\n".join(lines) + "\n"


def grid_normaliser_for(text_normaliser: str) -> str | None:
    """Which grid normaliser shadows a text normaliser, or `None` where none does."""
    return next((grid for grid, text in GRID_NORMALISERS.items() if text == text_normaliser), None)


def grid_layer(data: bytes, text: Layer, encoding: str | None = None) -> Layer | None:
    """The grid layer over a markup object, or `None` when the document holds no table.

    `text` is the text layer the normaliser made from the same bytes. It is not read for
    its characters: it is the hash this checks its own scan against. Raises `LayerFailure`
    when the scan gives other text.
    """
    normaliser = grid_normaliser_for(text.normaliser)
    if not normaliser:
        return None
    found = encoding or encoding_of(data)
    if not found:
        raise LayerFailure("gridLayer was given bytes that do not decode as text")
    grid, scanned = grid_of(
        decode(data, found), _RULES[normaliser], normaliser, text.normaliser, text.output_sha256
    )
    scanned_sha = sha256_hex(js_utf8(scanned))
    if scanned_sha != text.output_sha256:
        raise LayerFailure(
            f"the grid scan of {text.input_sha256} gives {scanned_sha} "
            f"and the text layer is {text.output_sha256}"
        )
    if not grid.tables:
        return None
    body = serialise_grid(grid)
    content = js_utf8(body)
    return Layer(
        normaliser=normaliser,
        tool=normaliser,
        tool_version=normaliser.split("@")[1],
        derivation=f"{normaliser} table geometry over {text.normaliser}",
        input_sha256=sha256_hex(data),
        output_sha256=sha256_hex(content),
        content=content,
        characters=character_count(body),
        identity=False,
    )
