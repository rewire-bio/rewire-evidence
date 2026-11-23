"""A cell of a table, crossed against a table grid, and the reader of a grid layer.

This is `shared/omics/spans/grid.ts`, with `readGrid`, `cellAt`, `tableAt` and `cellRange` of
`shared/archive/layers/grid/index.ts`.

A markup text layer flattens a table: every cell becomes a run of characters on a line, and
the row and column it sat in are gone. A grid is the geometry that was lost. It holds, for
every table of one text layer, each cell's row, column and spans, and the character offsets
of its text in that layer. So a locator that names a table, a row and a column can be
answered by looking the cell up, and the span is the grid's.

Every refusal the aligned resolver makes has a counterpart here, and one more: the grid's
offsets are checked against the layer before a span is returned, so a grid that has drifted
from its text layer cannot commit a span.

A grid layer is read into the classes `grids` builds one from, with its text put into units,
which is how the resolvers hold all text. A layer that is not a grid is refused as a whole.
The TypeScript reads each line with `JSON.parse` and trusts its shape, so a line that is
JSON and not a table is read there as a table with members missing. No registered layer is
one: a grid's bytes are named by their hash.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Final, cast

from rewire_evidence import zod
from rewire_evidence.aligned import (
    any_caption,
    caption_lines,
    caption_pattern,
    column_forms,
    normalise_label,
    table_window,
)
from rewire_evidence.canonical import JsonObject, JsonValue, js_parse
from rewire_evidence.grids import GridCell, GridRange, GridTable, InferredHeader
from rewire_evidence.js import SPACE
from rewire_evidence.jsonl import StoreError
from rewire_evidence.locator import Anchor
from rewire_evidence.offsets import LayerIndex, is_high, is_low, span_at, to_units
from rewire_evidence.resolution import Refusal, Resolution, Resolved


class GridUnreadable(StoreError):  # noqa: N818 - a layer that is not a grid, which the measurement counts
    """A grid layer that cannot be read as one."""


_COUNT: Final = zod.number(zod.INT, zod.NONNEGATIVE)
_RANGE: Final = zod.strict({"start": _COUNT, "end": _COUNT, "text": zod.text()})
_PATHS: Final = zod.array(zod.array(zod.text()))
_CELL: Final = zod.strict(
    {
        "row": _COUNT,
        "column": _COUNT,
        "row_span": _COUNT,
        "column_span": _COUNT,
        "header": zod.Boolean(),
        "start": zod.nullable(_COUNT),
        "end": zod.nullable(_COUNT),
        "text": zod.text(),
    }
)
_TABLE: Final = zod.strict(
    {
        "index": _COUNT,
        "rows": _COUNT,
        "columns": _COUNT,
        "header_rows": _COUNT,
        "headers": _PATHS,
        "label": zod.nullable(_RANGE),
        "caption": zod.nullable(_RANGE),
        "cells": zod.array(_CELL),
        "problems": zod.array(zod.text()),
        "inferred_header": zod.optional(zod.strict({"rule": zod.text(), "rows": _COUNT, "headers": _PATHS})),
    }
)
_HEADER: Final = zod.strict(
    {
        "normaliser": zod.text(),
        "text_layer_sha256": zod.text(),
        "text_normaliser": zod.text(),
        "tables": _COUNT,
        "cells": _COUNT,
        "empty_cells": _COUNT,
    }
)
_CONTINUES: Final = re.compile(r"[/|>+]")


class TableGrid:
    """A grid layer as read: the text layer it is over, and its tables with their text in units."""

    def __init__(
        self, normaliser: str, text_layer_sha256: str, text_normaliser: str, tables: Sequence[GridTable]
    ) -> None:
        """Hold the tables of one grid."""
        self.normaliser: Final = normaliser
        self.text_layer_sha256: Final = text_layer_sha256
        self.text_normaliser: Final = text_normaliser
        self.tables: Final = list(tables)
        self._covers: dict[int, dict[tuple[int, int], GridCell]] = {}

    def cover(self, at: int) -> dict[tuple[int, int], GridCell]:
        """For one table, the cell that covers each place: the first the markup wrote there."""
        held = self._covers.get(at)
        if held is None:
            held = self._covers[at] = {}
            for cell in self.tables[at].cells:
                for row in range(cell.row, cell.row + cell.row_span):
                    for column in range(cell.column, cell.column + cell.column_span):
                        held.setdefault((row, column), cell)
        return held


def _parsed(schema: zod.Schema, line: str) -> JsonObject:
    try:
        value = js_parse(line)
    except ValueError as error:
        raise GridUnreadable(f"a line of the grid is not JSON: {error}") from error
    payload = schema.run(value)
    if payload.issues:
        raise GridUnreadable(f"a line of the grid is not what a grid holds: {payload.issues[0].message}")
    return cast("JsonObject", payload.value)


def _texts(paths: JsonValue) -> list[list[str]]:
    return [[to_units(text) for text in path] for path in cast("list[list[str]]", paths)]


def _range(held: JsonValue) -> GridRange | None:
    if held is None:
        return None
    found = cast("JsonObject", held)
    return GridRange(
        cast("int", found["start"]), cast("int", found["end"]), to_units(cast("str", found["text"]))
    )


def _cell(held: JsonValue) -> GridCell:
    found = cast("dict[str, int]", held)
    return GridCell(
        found["row"],
        found["column"],
        found["row_span"],
        found["column_span"],
        cast("bool", found["header"]),
        cast("int | None", found["start"]),
        cast("int | None", found["end"]),
        to_units(cast("str", found["text"])),
    )


def _table(line: str) -> GridTable:
    held = _parsed(_TABLE, line)
    inferred = cast("JsonObject | None", held.get("inferred_header"))
    return GridTable(
        cast("int", held["index"]),
        cast("int", held["rows"]),
        cast("int", held["columns"]),
        cast("int", held["header_rows"]),
        _texts(held["headers"]),
        InferredHeader(
            cast("str", inferred["rule"]), cast("int", inferred["rows"]), _texts(inferred["headers"])
        )
        if inferred
        else None,
        _range(held["label"]),
        _range(held["caption"]),
        [_cell(cell) for cell in cast("list[JsonValue]", held["cells"])],
        [to_units(problem) for problem in cast("list[str]", held["problems"])],
    )


def read_grid(text: str) -> TableGrid:
    """Read a grid layer back. The inverse of `grids.serialise_grid`.

    `text` is the layer as its bytes decode. Raises `GridUnreadable` for text that holds no
    header line, another number of tables than its header says, or a line that is not what a
    grid holds.
    """
    lines = [line for line in text.split("\n") if line.strip(SPACE)]
    if not lines:
        raise GridUnreadable("a grid layer holds at least a header line")
    header = _parsed(_HEADER, lines[0])
    tables = [_table(line) for line in lines[1:]]
    if len(tables) != header["tables"]:
        raise GridUnreadable(
            f"the grid header says {header['tables']} tables and the layer holds {len(tables)}"
        )
    return TableGrid(
        cast("str", header["normaliser"]),
        cast("str", header["text_layer_sha256"]),
        cast("str", header["text_normaliser"]),
        tables,
    )


def table_at(grid: TableGrid, index: int) -> int | None:
    """The place in the grid of the table with this index, or `None`."""
    return next((at for at, table in enumerate(grid.tables) if table.index == index), None)


def cell_at(grid: TableGrid, table: int, row: int, column: int) -> GridCell | None:
    """The cell that covers a place of a table, where the markup wrote one.

    `table` is the table's place in the grid, which is not the index it carries.
    """
    return grid.cover(table).get((row, column))


def cell_range(grid: TableGrid, table: int, row: int, column: int) -> GridCell | None:
    """The cell at a place of the table with an index, where it prints anything: its range and its text."""
    at = table_at(grid, table)
    cell = None if at is None else cell_at(grid, at, row, column)
    return None if cell is None or cell.start is None or cell.end is None else cell


# ---- the resolver ----------------------------------------------------------------------


def _deny(route: str, reason: str, detail: str, layer: str) -> Refusal:
    """A refusal that names the route the grid was reached by, so it is counted as a grid attempt."""
    return Refusal(reason, detail, layer, route)


@dataclass(frozen=True)
class GridCellRequest:
    """A table, a row and a column, with whatever else the locator says about either."""

    anchor: Anchor
    row: str
    column: str
    #: Selector values past the row label and the column heading, which the row has to print.
    keys: tuple[str, ...] = ()
    #: Column headings past the first, each a level under the one before.
    sub_columns: tuple[str, ...] = ()
    #: What the locator says in a note, which may name a level of the heading above or below.
    level_hints: tuple[str, ...] = ()


def names_table(text: str, value: str) -> bool:
    """Whether a line or a label opens by naming this table, as a caption does."""
    return caption_pattern("Table", value).match(text) is not None


def _extent(table: GridTable) -> tuple[int, int] | None:
    """The characters of the layer a table's cells cover, or `None` for a table that prints nothing."""
    starts = [cell.start for cell in table.cells if cell.start is not None and cell.end is not None]
    ends = [cell.end for cell in table.cells if cell.start is not None and cell.end is not None]
    return (min(starts), max(ends)) if ends else None


def _line_of_point(index: LayerIndex, point: int) -> int:
    """The line a character offset falls in."""
    return max(bisect.bisect_right(index.points, point, 0, len(index.lines)) - 1, 0)


def unit_of_point(index: LayerIndex, point: int) -> int | None:
    """A character offset as a unit offset, or `None` where the layer holds no such character.

    A grid counts characters and a slice needs units. A layer with no character beyond U+FFFF
    counts the two alike.
    """
    if point < 0:
        return None
    line = _line_of_point(index, point)
    unit = index.u16[line]
    left = point - index.points[line]
    text = index.text
    end = min(unit + len(index.lines[line]), len(text))
    if index.points is index.u16:
        taken = min(left, end - unit)
        unit += taken
        left -= taken
    while left > 0 and unit < end:
        unit += 2 if is_high(text[unit]) and unit + 1 < len(text) and is_low(text[unit + 1]) else 1
        left -= 1
    if left == 0:
        return unit
    # One past the last character of a line is the newline that ends it.
    return unit + 1 if left == 1 and unit == end and end < len(text) else None


def _tables_by_label(grid: TableGrid, value: str) -> list[int]:
    """Tables whose own label names this table number, or whose caption does where there is no label."""
    found = []
    for at, table in enumerate(grid.tables):
        label = table.label.text.strip(SPACE) if table.label else ""
        if label:
            if names_table(label, value):
                found.append(at)
            continue
        caption = table.caption.text.strip(SPACE) if table.caption else ""
        if caption and names_table(caption, value):
            found.append(at)
    return found


def _table_against_caption(
    index: LayerIndex, line: int, extents: Sequence[tuple[int, int] | None]
) -> int | None:
    """The table standing against a caption line from above, where the page prints its caption under it."""
    best: int | None = None
    ends = -1
    for at, extent in enumerate(extents):
        if extent is not None and ends < extent[1] <= index.points[line]:
            ends = extent[1]
            best = at
    if best is None:
        return None
    for above in range(_line_of_point(index, ends) + 1, line):
        if index.lines[above].strip(SPACE):
            return None
    return best


def _tables_at_caption(index: LayerIndex, grid: TableGrid, value: str) -> list[int]:
    """Tables a caption line of the text layer names, where the markup gave them no label.

    For each line that names the table, the tables beginning in the window under it, and the
    one table standing against it from above where the line is a caption and not a sentence.
    """
    found: list[int] = []
    extents = [_extent(table) for table in grid.tables]
    for line in caption_lines(index, "Table", value):
        if any_caption(index, line):
            against = _table_against_caption(index, line, extents)
            if against is not None and against not in found:
                found.append(against)
        first, last = table_window(index, line)
        if last <= first:
            continue
        start = index.points[first]
        end = index.points[last]
        found += [
            at
            for at, extent in enumerate(extents)
            if extent is not None and start <= extent[0] < end and at not in found
        ]
    return found


def _distinct(cells: Sequence[GridCell | None]) -> list[GridCell]:
    """The cells of a run of places, each once. A spanning cell covers several places."""
    found: list[GridCell] = []
    for cell in cells:
        if cell is not None and not any(cell is held for held in found):
            found.append(cell)
    return found


def _label_cells(grid: TableGrid, at: int, row: int, before: int) -> list[GridCell]:
    """The cells of a row left of a column, which is where a row label may be printed."""
    limit = min(max(before, 1), grid.tables[at].columns)
    return _distinct([cell_at(grid, at, row, column) for column in range(limit)])


def _row_cells(grid: TableGrid, at: int, row: int) -> list[GridCell]:
    """Every cell of a row, for the keys a locator names beside the row label."""
    return _distinct([cell_at(grid, at, row, column) for column in range(grid.tables[at].columns)])


def _matches(text: str, wanted: Sequence[str]) -> bool:
    return any(form in wanted for form in column_forms(text))


def _columns_named(table: GridTable, column: str, sub_columns: Sequence[str]) -> list[tuple[int, int]]:
    """The columns whose heading path names the column: each column, and the level that matched last.

    The first heading is matched at any level of the path, and each one after it at a level
    below the one before, so `column 'Human', sub-column 'AUROC'` matches the path
    Human then AUROC and not a column whose own heading is AUROC under another group.
    """
    wanted = column_forms(column)
    deeper = [column_forms(sub) for sub in sub_columns]
    found = []
    for at, path in enumerate(table.headers):
        level = next((depth for depth, text in enumerate(path) if _matches(text, wanted)), -1)
        if level < 0:
            continue
        for sub in deeper:
            level = next((depth for depth in range(level + 1, len(path)) if _matches(path[depth], sub)), -1)
            if level < 0:
                break
        else:
            found.append((at, level))
    return found


def _narrow_by_hints(
    table: GridTable, candidates: Sequence[tuple[int, int]], hints: Sequence[str]
) -> list[tuple[int, int]]:
    """Of several columns, the ones a hint of the locator names at some other level of the path."""
    wanted = [column_forms(hint) for hint in hints]
    return [
        (at, level)
        for at, level in candidates
        if any(
            depth != level
            and text.strip(SPACE)
            and any(form in hint for form in column_forms(text) for hint in wanted)
            for depth, text in enumerate(table.headers[at])
        )
    ]


def _resolve_in(
    index: LayerIndex, route: str, grid: TableGrid, at: int, request: GridCellRequest
) -> Resolution:
    """One table of the grid: the cell at the named row and column, or the reason there is none."""
    layer = index.layer_sha256
    table = grid.tables[at]
    hints = [hint for hint in request.level_hints if hint.strip(SPACE)]
    matched = _columns_named(table, request.column, request.sub_columns)
    named = " then ".join(f'"{text}"' for text in (request.column, *request.sub_columns))
    if not matched:
        return _deny(
            route,
            "column_not_found",
            f"table {table.index} carries {table.header_rows} header row(s) and none of its "
            f"{table.columns} column headings is {named}",
            layer,
        )
    narrowed = _narrow_by_hints(table, matched, hints) if len(matched) > 1 and hints else matched
    columns = narrowed if len(narrowed) == 1 else matched
    if len(columns) > 1:
        also = (
            f" and {len(narrowed)} of them also print {' or '.join(f'"{hint}"' for hint in hints)}"
            if hints
            else ""
        )
        return _deny(
            route,
            "column_ambiguous",
            f"{len(columns)} columns of table {table.index} carry the heading {named}{also}",
            layer,
        )
    column = columns[0][0]

    wanted_row = normalise_label(request.row)
    labelled = [
        row
        for row in range(table.header_rows, table.rows)
        if any(normalise_label(cell.text) == wanted_row for cell in _label_cells(grid, at, row, column))
    ]
    if not labelled:
        return _deny(
            route,
            "row_not_found",
            f'no body row of table {table.index} prints the row label "{request.row}" in a column '
            f'left of "{request.column}"',
            layer,
        )
    wanted_column = column_forms(request.column)
    keys = [
        key
        for key in map(normalise_label, request.keys)
        if key and key != wanted_row and key not in wanted_column
    ]
    rows = [
        row
        for row in labelled
        if all(
            key in normalise_label(" ".join(cell.text for cell in _row_cells(grid, at, row))) for key in keys
        )
    ]
    if not rows:
        return _deny(
            route,
            "row_key_unmatched",
            f'the row "{request.row}" is printed in table {table.index}, and not on a row that also prints '
            f"{' and '.join(f'"{key}"' for key in keys)}",
            layer,
        )
    if len(rows) > 1:
        return _deny(
            route,
            "row_ambiguous",
            f'{len(rows)} body rows of table {table.index} print the row label "{request.row}"',
            layer,
        )
    row = rows[0]

    cell = cell_at(grid, at, row, column)
    if cell is None:
        return _deny(
            route, "cell_empty", f"table {table.index} writes no cell at row {row}, column {column}", layer
        )
    if cell.start is None or cell.end is None:
        return _deny(
            route,
            "cell_empty",
            f"the cell at row {row}, column {column} of table {table.index} prints nothing",
            layer,
        )
    # The grid's offsets are checked against the layer, so a grid that has drifted from its
    # text layer refuses here and commits no span.
    start = unit_of_point(index, cell.start)
    end = unit_of_point(index, cell.end)
    if start is None or end is None:
        return _deny(
            route,
            "no_column_geometry",
            f"the grid puts the cell at code points [{cell.start}, {cell.end}) and the layer holds fewer "
            "characters than that",
            layer,
        )
    span = span_at(index, start, end)
    if span.exact != cell.text or span.start != cell.start or span.end != cell.end:
        return _deny(
            route,
            "no_column_geometry",
            f"the grid says the cell at row {row}, column {column} of table {table.index} is code points "
            f"[{cell.start}, {cell.end}) and the layer prints something else there",
            layer,
        )
    stub = next(
        (
            held.text
            for held in _label_cells(grid, at, row, column)
            if normalise_label(held.text) == wanted_row
        ),
        "",
    )
    path = table.headers[column] if column < len(table.headers) else []
    return Resolved(
        "grid_cell",
        span,
        layer,
        row_label=stub,
        column_header=" / ".join(level for level in path if level.strip(SPACE)),
        grid_route=route,
    )


def resolve_grid_cell(index: LayerIndex, grid: TableGrid, request: GridCellRequest) -> Resolution:
    """The span of one table cell, crossed against the grid, or the reason there is none.

    Two routes to the table, in order. The markup's own label is authoritative. Failing that,
    a caption line of the text layer and the tables around it, of which exactly one has to
    yield the cell. `grid` has to be the grid of this text layer.
    """
    layer = index.layer_sha256
    value = request.anchor.value
    if grid.text_layer_sha256 != layer:
        return _deny(
            "none",
            "no_column_geometry",
            f"the grid addresses layer {grid.text_layer_sha256[:12]} and this is {layer[:12]}",
            layer,
        )
    if request.anchor.kind == "figure":
        return _deny("none", "no_resolver_for_kind", "a figure anchor names no table of the grid", layer)
    by_label = _tables_by_label(grid, value)
    candidates = by_label or _tables_at_caption(index, grid, value)
    if not candidates:
        return _deny(
            "none",
            "anchor_not_found",
            f"no table of the grid is labelled Table {value} and no caption line in the layer names one",
            layer,
        )
    route = "label" if by_label else "caption_window"
    hits: list[Resolved] = []
    last: Refusal | None = None
    for at in candidates:
        outcome = _resolve_in(index, route, grid, at, request)
        if isinstance(outcome, Resolved):
            hits.append(outcome)
        else:
            last = outcome
    if len(hits) > 1:
        return _deny(
            route,
            "anchor_ambiguous",
            f'{len(hits)} of {len(candidates)} tables found by {route} hold the row "{request.row}" under '
            f'the column "{request.column}"',
            layer,
        )
    if hits:
        return replace(hits[0], anchor_candidates=len(candidates)) if len(candidates) > 1 else hits[0]
    # No table held the cell, so the last of them refused.
    return last or _deny(route, "anchor_not_found", f"no table of the grid names Table {value}", layer)


def grid_request(
    selectors: Sequence[tuple[str, str | None]],
    notes: Sequence[str],
    base: GridCellRequest,
    role_of: Callable[[str], str],
) -> GridCellRequest:
    """The request a grid is asked, which holds two things only a grid can read.

    A selector whose key is a bare solidus continues the one before it, so the heading after
    a slash is a level under the column named first. And a note in the locator may name a
    heading level, which decides between columns that carry the same heading. `selectors`
    are each key with its value, and `notes` the text of each note qualifier.
    """
    continued: list[str] = []
    last: str | None = None
    for key, value in selectors:
        if value is None:
            continue
        role = role_of(key)
        if role in {"row", "column"}:
            last = role
        elif _CONTINUES.fullmatch(key.strip(SPACE)) and last == "column":
            continued.append(value)
    hints = [text for text in (_unbracketed(note) for note in notes) if text]
    return replace(
        base,
        keys=tuple(key for key in base.keys if key not in continued),
        sub_columns=(*base.sub_columns, *(value for value in continued if value not in base.sub_columns)),
        level_hints=tuple(hints),
    )


def _unbracketed(note: str) -> str:
    """A note without the bracket it opens with and the one it closes with."""
    return note.removeprefix("(").removesuffix(")").strip(SPACE)


def grid_route_for(index: LayerIndex, grid: TableGrid, value: str) -> str:
    """How a grid would find the table an anchor names, without resolving any cell."""
    if _tables_by_label(grid, value):
        return "label"
    return "caption_window" if _tables_at_caption(index, grid, value) else "none"
