"""A cell of a workbook, by sheet, row and column, read off a sheet layer's blocks.

This is `shared/archive/layers/office/address.ts`, `unescapeCell` of `office/sheet.ts`, and
`shared/omics/spans/sheet.ts`.

An `ooxml-sheet-tsv@1` layer keeps the geometry of the workbook. It is one block for each
sheet: a header line that opens with a form feed and names the sheet, then one line for each
row from row 1 to the last row that holds a value, each a run of tab separated fields from
column A. So row r of a sheet is the line r below its header and column c is field c of
that line, and no caption, alignment or guess is involved.

Text inside the resolver is in units. The functions of `address.ts`, which other code calls
with a layer's text, take it in characters and answer in characters.
"""

from __future__ import annotations

import re
import weakref
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from rewire_evidence.aligned import lower_units, normalise_label
from rewire_evidence.js import SPACE, SPACE_CLASS, paired, parse_int
from rewire_evidence.locator import Parsed
from rewire_evidence.office import BLOCK_MARKER, column_label, column_number, parse_reference
from rewire_evidence.offsets import LayerIndex, is_low, span_at, to_units
from rewire_evidence.resolution import Resolution, Resolved, refuse

#: Normalisers whose text is sheet blocks.
SHEET_NORMALISERS: Final = frozenset({"ooxml-sheet-tsv@1"})
#: How far down a sheet a column heading is looked for.
HEADING_ROWS: Final = 50
#: How far up a value is carried into the rows under it. A merged or grouped cell is stored
#: once and stands for every row below it until the next value.
CARRY_ROWS: Final = 200
#: How many sheet names a refusal lists.
NAMES_SHOWN: Final = 8

_HEX: Final = re.compile(r"[0-9a-f]{2}")
_LETTERS: Final = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r", "f": "\f"}
_S: Final = f"[{SPACE_CLASS}]"
_SPACES: Final = re.compile(f"{_S}+")
_ROW_WORDS: Final = frozenset({"row", "rows"})
_COLUMN_WORDS: Final = frozenset({"column", "col", "columns", "sub-column", "sub-col"})
#: Keys that name a level above the row, which the row itself does not print.
_ABOVE_THE_ROW: Final = re.compile(
    r"\b(?:block|blocks|group|groups|panel|panels|section|sections|part|parts|subtable|under|metric)\b",
    re.IGNORECASE | re.ASCII,
)
_ROW_NUMBER: Final = re.compile(rf"([0-9]{{1,7}}){_S}*[.:,;]?")
_COLUMN_LETTERS: Final = re.compile(r"[A-Za-z]{1,3}")
_NAMES_SHEET: Final = re.compile(rf"(?:\A|{_S})sheets?\Z", re.IGNORECASE | re.ASCII)


def unescape_cell(text: str) -> str:
    """Reverse `office.escape_cell`. An escape it does not write is left as it stands."""
    if "\\" not in text:
        return text
    out: list[str] = []
    at = 0
    while at < len(text):
        if text[at] != "\\":
            out.append(text[at])
            at += 1
            continue
        following = text[at + 1 : at + 2]
        if following in _LETTERS:
            out.append(_LETTERS[following])
            at += 2
        elif following == "x" and _HEX.fullmatch(text[at + 2 : at + 4]):
            out.append(chr(int(text[at + 2 : at + 4], 16)))
            at += 4
        else:
            out.append("\\")
            at += 1
    return "".join(out)


# ---- the blocks of a layer -------------------------------------------------------------


@dataclass
class SheetBlock:
    """One sheet block: its place in the workbook, its name, and where its rows are."""

    #: Place in workbook order, from 1.
    index: int
    #: The sheet name, with its escapes undone.
    name: str
    #: The extent the layer wrote, or no text for a sheet with no cells.
    range: str
    #: The line of the layer that holds the block's header, counted from 0.
    header_line: int
    #: Rows the block prints, which is the last row of the sheet that holds a value.
    rows: int = 0


def _line_spans(text: str) -> list[tuple[int, int]]:
    """Where each line starts and ends. A last line with nothing on it is not a line."""
    spans: list[tuple[int, int]] = []
    at = 0
    for line in text.split("\n"):
        spans.append((at, at + len(line)))
        at += len(line) + 1
    if spans[-1][0] == len(text):
        del spans[-1]
    return spans


def _blocks(text: str) -> list[SheetBlock]:
    spans = _line_spans(text)
    blocks: list[SheetBlock] = []
    for line, (start, end) in enumerate(spans):
        if not text.startswith(BLOCK_MARKER, start, end):
            continue
        fields = text[start:end].split("\t")
        number = parse_int(fields[1])
        blocks.append(
            SheetBlock(
                int(number) if number.is_integer() else len(blocks) + 1,
                unescape_cell(fields[2]) if len(fields) > 2 else "",  # noqa: PLR2004 - the name is the third field
                fields[3] if len(fields) > 3 else "",  # noqa: PLR2004 - and the range the fourth
                line,
            )
        )
    for at, block in enumerate(blocks):
        following = blocks[at + 1].header_line if at + 1 < len(blocks) else len(spans)
        block.rows = max(0, following - block.header_line - 1)
    return blocks


def sheet_blocks(text: str) -> list[SheetBlock]:
    """Every sheet block of a layer's text, in order, the names in characters."""
    blocks = _blocks(to_units(text))
    for block in blocks:
        block.name = paired(block.name)
        block.range = paired(block.range)
    return blocks


def find_block(blocks: Sequence[SheetBlock], sheet: str | int) -> SheetBlock | None:
    """The block for a sheet, by name or by its place from 1.

    A name is matched exactly first. Where no sheet has it exactly and one sheet has it up
    to case, that one is taken. Two that differ only in case give nothing.
    """
    if isinstance(sheet, int):
        return next((block for block in blocks if block.index == sheet), None)
    exact = [block for block in blocks if block.name == sheet]
    if exact:
        return exact[0] if len(exact) == 1 else None
    folded = [block for block in blocks if lower_units(block.name) == lower_units(sheet)]
    return folded[0] if len(folded) == 1 else None


@dataclass(frozen=True)
class CellRange:
    """A cell's place in a sheet layer, and what it holds."""

    #: Offset of the first character of the field, in characters, and one past its last.
    start: int
    end: int
    #: The field as the layer prints it, and with its escapes undone.
    field: str
    value: str
    #: The line of the layer the row is on, counted from 0, and the field from 1.
    line: int
    field_number: int


def _points_before(text: str, end: int) -> int:
    """Characters before a unit offset, as the TypeScript counts: every unit but a second half."""
    head = text[:end]
    return len(head) if head.isascii() else sum(1 for unit in head if not is_low(unit))


def cell_range(text: str, sheet: str | int, row: int, column: int) -> CellRange | None:
    """The range a cell covers, or `None` where the block does not hold that row or that field.

    `row` and `column` are counted from 1, as a spreadsheet counts them. An empty cell inside
    the block has a range of no characters.
    """
    if row < 1 or column < 1:
        return None
    units = to_units(text)
    block = find_block(_blocks(units), to_units(sheet) if isinstance(sheet, str) else sheet)
    spans = _line_spans(units)
    if block is None or row > block.rows:
        return None
    line_start, line_end = spans[block.header_line + row]
    start = line_start
    for _ in range(1, column):
        tab = units.find("\t", start, line_end)
        if tab < 0:
            return None
        start = tab + 1
    tab = units.find("\t", start, line_end)
    held = units[start : line_end if tab < 0 else tab]
    before = _points_before(units, start)
    return CellRange(
        before,
        before + _points_before(held, len(held)),
        paired(held),
        paired(unescape_cell(held)),
        block.header_line + row,
        column,
    )


def cell_range_by_reference(text: str, sheet: str | int, reference: str) -> CellRange | None:
    """The same, by an A1 reference."""
    parsed = parse_reference(reference)
    return None if parsed is None else cell_range(text, sheet, *parsed)


def rfc7111_fragment(found: CellRange) -> str:
    """A cell's range as an RFC 7111 fragment against the layer, which is tab separated text."""
    return f"#cell={found.line + 1},{found.field_number}"


def reference(row: int, column: int) -> str:
    """The A1 reference for a row and a column."""
    return f"{column_label(column)}{row}"


# ---- the resolver ----------------------------------------------------------------------


def is_sheet_layer(normaliser: str) -> bool:
    """Whether a layer is sheet blocks."""
    return normaliser in SHEET_NORMALISERS


@dataclass(frozen=True)
class SheetField:
    """One field of a line, with its unit offsets into the layer."""

    text: str
    start: int
    end: int


@dataclass
class _Read:
    """What has been read of one sheet layer already."""

    blocks: list[SheetBlock] | None = None
    fields: dict[int, list[SheetField]] = field(default_factory=dict)
    values: dict[int, list[str]] = field(default_factory=dict)


_READ: Final[weakref.WeakKeyDictionary[LayerIndex, _Read]] = weakref.WeakKeyDictionary()


def _read(index: LayerIndex) -> _Read:
    held = _READ.get(index)
    if held is None:
        held = _READ[index] = _Read()
    return held


def blocks_of(index: LayerIndex) -> list[SheetBlock]:
    """The blocks of a layer, found once for each index. The names are in units."""
    read = _read(index)
    if read.blocks is None:
        read.blocks = _blocks(index.text)
    return read.blocks


def fields_of_line(index: LayerIndex, line: int) -> list[SheetField]:
    """Every field of one line, split on tabs and nothing else. A line the layer lacks is one empty field."""
    read = _read(index).fields
    held = read.get(line)
    if held is None:
        text = index.lines[line] if 0 <= line < len(index.lines) else ""
        base = index.u16[line] if 0 <= line < len(index.u16) else 0
        held = read[line] = []
        start = 0
        for part in text.split("\t"):
            held.append(SheetField(part, base + start, base + start + len(part)))
            start += len(part) + 1
    return held


def _values(index: LayerIndex, line: int) -> list[str]:
    """What each field of a line holds, with its escapes undone."""
    read = _read(index).values
    held = read.get(line)
    if held is None:
        held = read[line] = [unescape_cell(found.text) for found in fields_of_line(index, line)]
    return held


def _heading_columns(index: LayerIndex, block: SheetBlock, row: int, heading: str) -> list[tuple[int, int]]:
    """The columns a heading names: each column, and the row its heading is printed on.

    The heading may be on any row above the one being read, because a sheet often prints a
    title or a group heading first. Each column is counted once, at the first row that
    prints the heading in it.
    """
    wanted = normalise_label(heading)
    if not wanted:
        return []
    found: dict[int, int] = {}
    for above in range(1, min(row - 1, block.rows, HEADING_ROWS) + 1):
        for at, value in enumerate(_values(index, block.header_line + above)):
            if normalise_label(value) == wanted and at + 1 not in found:
                found[at + 1] = above
    return sorted(found.items())


def _values_in_force(index: LayerIndex, block: SheetBlock, row: int) -> dict[int, str]:
    """What each column holds for a row: its own cell, or the nearest value above it."""
    in_force: dict[int, str] = {}
    for above in range(row, max(1, row - CARRY_ROWS) - 1, -1):
        for at, value in enumerate(_values(index, block.header_line + above)):
            text = value.strip(SPACE)
            if text and at + 1 not in in_force:
                in_force[at + 1] = text
    return in_force


def _heading_of(index: LayerIndex, block: SheetBlock, column: int, row: int) -> str | None:
    """The heading over a column as the sheet prints it on one row, or `None` where that is blank.

    `row` is the first row of the block, or one a heading was found on, so it is in the block.
    """
    values = _values(index, block.header_line + row)
    return (values[column - 1].strip(SPACE) if column <= len(values) else "") or None


@dataclass(frozen=True)
class RowKey:
    """Something the locator says the row holds: the selector's key, its value, and its text as printed."""

    key: str
    value: str
    printed: str = ""


@dataclass(frozen=True)
class SheetCellRequest:
    """What a locator says about one cell of a workbook.

    The sheet's name as the locator prints it, or `None`. An A1 reference, a row and a
    column counted from 1, the heading the locator names for the column, and what else it
    says the row holds.
    """

    sheet: str | None
    cell: str | None = None
    row: int | None = None
    column: int | None = None
    column_header: str | None = None
    row_keys: tuple[RowKey, ...] = ()


def resolve_sheet_cell(index: LayerIndex, request: SheetCellRequest) -> Resolution:  # noqa: C901, PLR0912 - one reason to a return
    """The span of one cell of one sheet, or the reason there is none."""
    layer = index.layer_sha256
    if not is_sheet_layer(index.normaliser):
        return refuse("no_column_geometry", f"{index.normaliser} carries no sheet blocks", layer)
    blocks = blocks_of(index)
    if not blocks:
        return refuse("no_column_geometry", "this layer holds no sheet block", layer)
    names = ", ".join(f'"{block.name}"' for block in blocks[:NAMES_SHOWN])
    held = f"this workbook holds {len(blocks)}: {names}{' and more' if len(blocks) > NAMES_SHOWN else ''}"
    wanted = "" if request.sheet is None else request.sheet.strip(SPACE)
    if not wanted:
        return refuse("no_anchor", f"the locator names a cell and no sheet, and {held}", layer)
    block = find_block(blocks, wanted)
    if block is None:
        folded = sum(1 for candidate in blocks if lower_units(candidate.name) == lower_units(wanted))
        if folded > 1:
            return refuse(
                "anchor_ambiguous", f'{folded} sheets of this workbook are named "{wanted}" up to case', layer
            )
        return refuse("anchor_not_found", f'no sheet of this workbook is named "{wanted}", and {held}', layer)

    row: int | None = None
    column: int | None = None
    if request.cell is not None:
        parsed = parse_reference(request.cell)
        if parsed is None:
            return refuse("no_resolver_for_kind", f'"{request.cell}" is not an A1 reference', layer)
        row, column = parsed
    if request.row is not None:
        if row is not None and row != request.row:
            return refuse("row_ambiguous", f"the locator names cell row {row} and row {request.row}", layer)
        row = request.row
    if row is None:
        return refuse("no_row_in_locator", f'the locator names sheet "{block.name}" and no row', layer)
    if request.column is not None and column is None:
        column = request.column

    heading_row = 1
    heading = request.column_header
    if column is None:
        if heading is None:
            return refuse(
                "no_column_in_locator",
                f'the locator names row {row} of sheet "{block.name}" and no column',
                layer,
            )
        columns = _heading_columns(index, block, row, heading)
        if not columns:
            return refuse(
                "column_not_found",
                f'no row above row {row} of sheet "{block.name}" prints the heading "{heading}"',
                layer,
            )
        if len(columns) > 1:
            return refuse(
                "column_ambiguous",
                f'sheet "{block.name}" prints the heading "{heading}" in {len(columns)} columns '
                f"above row {row}",
                layer,
            )
        column, heading_row = columns[0]
    elif heading is not None:
        columns = _heading_columns(index, block, row, heading)
        agreed = [above for at, above in columns if at == column]
        if columns and not agreed:
            over = " and ".join(column_label(at) for at, _ in columns)
            return refuse(
                "column_not_found",
                f'sheet "{block.name}" prints the heading "{heading}" over column {over}, '
                f"and the locator names column {column_label(column)}",
                layer,
            )
        if agreed:
            heading_row = agreed[0]

    if row > block.rows:
        return refuse(
            "row_not_found",
            f'sheet "{block.name}" holds {block.rows} rows and the locator names row {row}',
            layer,
        )
    line = block.header_line + row
    fields = fields_of_line(index, line)
    values = _values(index, line)
    if column > len(fields) or not values[column - 1].strip(SPACE):
        return refuse(
            "cell_empty",
            f'row {row} of sheet "{block.name}" prints nothing in column {column_label(column)}',
            layer,
        )
    unmatched = _unmatched(index, block, row, request.row_keys)
    if unmatched:
        named = " or ".join(f'{entry.key} "{entry.value}"' for entry in unmatched)
        return refuse("row_key_unmatched", f'row {row} of sheet "{block.name}" does not hold {named}', layer)
    found = fields[column - 1]
    return Resolved(
        "sheet_cell",
        span_at(index, found.start, found.end),
        layer,
        row_label=next((text for text in (value.strip(SPACE) for value in values) if text), None),
        column_header=_heading_of(index, block, column, heading_row),
    )


def _unmatched(index: LayerIndex, block: SheetBlock, row: int, keys: Sequence[RowKey]) -> list[RowKey]:
    """The keys the row does not hold, in its own cells or in a value carried down to it.

    A key that names a heading of the sheet is looked for under that heading. Any other is
    met by a cell anywhere on the row, because the locator's word for a key is the author's
    and need not be the sheet's.
    """
    unmatched: list[RowKey] = []
    in_force: dict[int, str] | None = None
    for entry in keys:
        spellings = [text for text in map(normalise_label, (entry.value, entry.printed)) if text]
        if not spellings:
            continue
        if in_force is None:
            in_force = _values_in_force(index, block, row)
        named = _heading_columns(index, block, row, entry.key)
        held = [in_force.get(named[0][0], "")] if len(named) == 1 else list(in_force.values())
        if not any(normalise_label(text) in spellings for text in held):
            unmatched.append(entry)
    return unmatched


def _first_word(key: str) -> str:
    return lower_units(_SPACES.split(key.strip(SPACE))[0])


def _rest_of_key(key: str) -> str:
    return " ".join(_SPACES.split(key.strip(SPACE))[1:])


def _column_of_letters(letters: str) -> int | None:
    """A column from the letters a key carries, or `None` where the rest of the key is no column."""
    if not _COLUMN_LETTERS.fullmatch(letters):
        return None
    return column_number(letters.upper()) or None


def sheet_request(parsed: Parsed) -> SheetCellRequest | None:  # noqa: C901, PLR0912 - one kind of selector to a branch
    """What a parsed locator says about a cell of a workbook, or `None` where it says too little.

    A locator qualifies where it prints a cell reference, or where it names a sheet, a row
    and a column or a column heading. Anything less has no cell in it. `parsed` is in units.
    """
    cell = next((address for address in parsed.addresses if address.kind == "sheet_cell"), None)
    row_address = next((address for address in parsed.addresses if address.kind == "row_number"), None)
    anchor = next((held for held in parsed.anchors if held.kind == "sheet" and held.value.strip(SPACE)), None)
    sheet = anchor.value if anchor else None
    row: int | None = None
    column: int | None = None
    column_header: str | None = None
    row_keys: list[RowKey] = []

    if row_address:
        number = parse_int(row_address.value)
        if number.is_integer() and number > 0:
            row = int(number)

    for selector in parsed.selectors:
        if selector.value is None:
            continue
        if sheet is None and _NAMES_SHEET.search(selector.key.strip(SPACE)):
            sheet = selector.value
            continue
        head = _first_word(selector.key)
        if head in _COLUMN_WORDS:
            letters = _column_of_letters(_rest_of_key(selector.key))
            if letters is not None and column is None:
                column = letters
            if column_header is None:
                column_header = selector.value
            continue
        if head in _ROW_WORDS:
            number_text = _ROW_NUMBER.fullmatch(selector.value.strip(SPACE))
            if number_text:
                if row is None and int(number_text[1]) > 0:
                    row = int(number_text[1])
            else:
                row_keys.append(RowKey(selector.key, selector.value, selector.raw))
            continue
        if _ABOVE_THE_ROW.search(selector.key):
            continue
        row_keys.append(RowKey(selector.key, selector.value, selector.raw))

    if cell is None and not (
        sheet is not None and row is not None and (column is not None or column_header is not None)
    ):
        return None
    return SheetCellRequest(
        sheet,
        cell.value if cell else None,
        row,
        column if cell is None else None,
        column_header,
        tuple(entry for entry in row_keys if entry.value.strip(SPACE) and entry.value != sheet),
    )
