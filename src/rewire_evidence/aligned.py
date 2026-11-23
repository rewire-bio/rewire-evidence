r"""The table cell resolver for column-aligned text, the width check and the figure caption.

This is `shared/omics/spans/table.ts`, `width.ts` and `figure.ts`.

`pdftotext -layout` keeps the horizontal position of every word, so a table in a PDF text
layer is lines whose cells sit under their column headings. That is enough to cross a row
label against a column heading without parsing the PDF, and it is the only geometry a PDF
layer has. It is also not always true, and every way it fails is a refusal: the caption is
not found, the heading is printed twice, two cells of the row sit under the heading, the
field runs across more than one column.

All text here is in units and every offset is a unit offset, as in the TypeScript. The
patterns are written out with JavaScript's own classes: `\s` is `js.SPACE_CLASS`, a digit is
`[0-9]`, and the `i` flag over ASCII letters is `re.IGNORECASE` with `re.ASCII`, which is
what JavaScript's rule gives for a letter of A to Z.
"""

from __future__ import annotations

import functools
import re
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Final

from rewire_evidence.js import SPACE, SPACE_CLASS, lower, paired
from rewire_evidence.jsregex import JsPattern, recorded_or_unit
from rewire_evidence.locator import Anchor
from rewire_evidence.offsets import LayerIndex, span_at, to_units
from rewire_evidence.resolution import Refusal, Resolution, Resolved, progress, refuse

#: Normalisers whose text keeps column positions. Only these can be read by alignment.
ALIGNED_NORMALISERS: Final = frozenset({"pdf-text-layer@1"})
#: How far below a caption the table may run before the resolver gives up, in lines.
WINDOW_LINES: Final = 200
#: How many lines under a top heading the second level may be.
SUB_HEADER_LINES: Final = 2
#: A caption runs from its first line to the first blank line after it, or this many lines.
CAPTION_LINES: Final = 12

_S: Final = f"[{SPACE_CLASS}]"
_QUOTES: Final = re.compile("[\u2018\u2019\u201c\u201d'\"]")
_MARKS: Final = re.compile("[*\u2020\u2021\u00a7\u00b6]+")
_SPACES: Final = re.compile(f"{_S}+")
_EDGES: Final = re.compile(rf"\A[{SPACE_CLASS}.,:;]+|[{SPACE_CLASS}.,:;]+\Z")
_TRAILING_SPACE: Final = re.compile(rf"{_S}+\Z")
_CELL: Final = re.compile("[^ ](?:[^ ]| (?! ))*")
_SYNTAX: Final = re.compile(r"[.*+?^${}()|[\]\\]")
_PREFIX: Final = rf"(?:Supplementary{_S}+|Supplemental{_S}+|Extended{_S}+Data{_S}+|Appendix{_S}+)?"
#: Any caption line at all, of a table or a figure, whatever its number.
ANY_CAPTION: Final = re.compile(
    rf"\A{_S}*{_PREFIX}(?:Tables?|Figures?|Fig\.){_S}*[0-9A-Z]", re.IGNORECASE | re.ASCII
)
#: What follows a figure's number where the line is its caption and not a sentence about it.
_CAPTION_TAIL: Final = re.compile(
    rf"\A(?:{_S}*\Z|{_S}*[.:|)\u2013\u2014,;]|{_S}+[\"'(\u2018\u201c]?[A-Z0-9])"
)
_PAGE_BREAK: Final = "\f"
_DECORATION: Final = re.compile("[\u2191\u2193\u2195\u21d1\u21d3]")
_TRAILING_BRACKET: Final = re.compile(rf"{_S}*[(\[][^()\[\]]*[)\]]{_S}*\Z")
_TOKEN: Final = re.compile("[^ ]+")
_HALF: Final = re.compile("[\ud800-\udfff]")


def lower_units(text: str) -> str:
    """`text.toLowerCase()` for text held in units, where a letter beyond U+FFFF is two of them."""
    return lower(text) if _HALF.search(text) is None else to_units(lower(paired(text)))


@functools.lru_cache(maxsize=1 << 18)
def normalise_label(text: str) -> str:
    """A label as it is compared: quotation marks and footnote marks off, spaces single, lower case."""
    return lower_units(_EDGES.sub("", _SPACES.sub(" ", _MARKS.sub("", _QUOTES.sub("", text)))))


@dataclass(frozen=True)
class Cell:
    """One cell of a line: its text and where it sits, in units of the layer."""

    text: str
    start: int
    end: int


@dataclass
class _Read:
    """What has been read of one layer already. The resolvers ask for the same lines over and over."""

    cells: dict[int, list[Cell]] = field(default_factory=dict)
    captions: dict[tuple[str, str], list[int]] = field(default_factory=dict)
    any_caption: dict[int, bool] = field(default_factory=dict)


_READ: Final[weakref.WeakKeyDictionary[LayerIndex, _Read]] = weakref.WeakKeyDictionary()


def _read(index: LayerIndex) -> _Read:
    held = _READ.get(index)
    if held is None:
        held = _READ[index] = _Read()
    return held


def cells_of(index: LayerIndex, line: int) -> list[Cell]:
    """Split a line into cells on runs of two or more spaces, which is what a layout renderer leaves."""
    read = _read(index).cells
    held = read.get(line)
    if held is None:
        base = index.u16[line]
        held = []
        for found in _CELL.finditer(index.lines[line]):
            trimmed = _TRAILING_SPACE.sub("", found[0])
            if trimmed:
                held.append(Cell(trimmed, base + found.start(), base + found.start() + len(trimmed)))
        read[line] = held
    return held


def _centre(cell: Cell, base: int) -> float:
    return (cell.start + cell.end) / 2 - base


def escape(text: str) -> str:
    """`escape`: the text as a pattern that matches it and nothing else."""
    return _SYNTAX.sub(lambda found: f"\\{found[0]}", text)


def caption_source(word: str, value: str) -> str:
    """The source of the pattern for a caption line of this table or figure, as the TypeScript builds it."""
    head = "Tables?" if word == "Table" else "(?:Figures?|Fig\\.?)"
    return (
        "^\\s*(?:Supplementary\\s+|Supplemental\\s+|Extended\\s+Data\\s+|Appendix\\s+)?"
        f"{head}\\s*{escape(value)}(?![0-9A-Za-z])"
    )


@functools.lru_cache(maxsize=4096)
def caption_pattern(word: str, value: str) -> re.Pattern[str]:
    """The pattern for a caption line of this table or figure, compiled.

    The number comes from the locator, so the pattern is read as JavaScript reads it, with
    what each of its letters matches under the `i` flag taken from the recording.
    """
    return JsPattern(caption_source(word, value), "i", recorded_or_unit).compiled


#: The two patterns with a number in them, so the sets their fixed parts need are recorded.
PATTERNS: Final = (
    JsPattern(caption_source("Table", "1"), "i", recorded_or_unit),
    JsPattern(caption_source("Figure", "1"), "i", recorded_or_unit),
)


def caption_lines(index: LayerIndex, word: str, value: str) -> list[int]:
    """Lines that open a caption for this table or figure, by line index.

    A sentence that mentions "Table 3" does not start with it, so this finds the caption and
    not the references to it. A figure is cited at the start of a line far more often than a
    table is, so a figure caption has also to be followed by what a caption is followed by.
    """
    read = _read(index).captions
    held = read.get((word, value))
    if held is None:
        pattern = caption_pattern(word, value)
        held = []
        for line, text in enumerate(index.lines):
            found = pattern.match(text)
            if found is None:
                continue
            if word == "Figure" and not _CAPTION_TAIL.match(text[found.end() :]):
                continue
            held.append(line)
        read[word, value] = held
    return held


def any_caption(index: LayerIndex, line: int) -> bool:
    """Whether a line opens a caption of any table or figure."""
    read = _read(index).any_caption
    held = read.get(line)
    if held is None:
        held = read[line] = ANY_CAPTION.match(index.lines[line]) is not None
    return held


def _boundary(index: LayerIndex, line: int) -> bool:
    return any_caption(index, line) or _PAGE_BREAK in index.lines[line]


def table_window(index: LayerIndex, caption: int) -> tuple[int, int]:
    """The lines a table can occupy under its caption: to the next caption, a page break or the limit."""
    limit = min(len(index.lines), caption + 1 + WINDOW_LINES)
    for line in range(caption + 1, limit):
        if _boundary(index, line):
            return caption + 1, line
    return caption + 1, limit


def wider_window(index: LayerIndex, caption: int) -> tuple[int, int]:
    """The same window without the page break as a boundary, for a table that runs over a page."""
    limit = min(len(index.lines), caption + 1 + WINDOW_LINES)
    for line in range(caption + 1, limit):
        if any_caption(index, line):
            return caption + 1, line
    return caption + 1, limit


def block_above(index: LayerIndex, caption: int) -> tuple[int, int] | None:
    """The block of lines standing against the caption from above, or `None`.

    A caption that sits under its table is found this way. A block that has a caption of its
    own on its other side is not taken, because nothing says which of the two it belongs to.
    """
    floor = max(0, caption - WINDOW_LINES)
    first = caption
    while first > floor and not _boundary(index, first - 1):
        first -= 1
    if first > 0 and any_caption(index, first - 1):
        return None
    last = caption
    while last > first and not index.lines[last - 1].strip(SPACE):
        last -= 1
    while first < last and not index.lines[first].strip(SPACE):
        first += 1
    return (first, last) if first < last else None


def column_forms(text: str) -> tuple[str, ...]:
    """The forms of a column heading: as printed, and with a trailing bracket of units dropped."""
    return _column_forms(text)


@functools.lru_cache(maxsize=1 << 18)
def _column_forms(text: str) -> tuple[str, ...]:
    plain = normalise_label(_DECORATION.sub(" ", text))
    stripped = normalise_label(_TRAILING_BRACKET.sub("", plain))
    return (plain,) if stripped in {plain, ""} else (plain, stripped)


def _names_column(cell: Cell, wanted: Sequence[str]) -> bool:
    return any(form in wanted for form in column_forms(cell.text))


@dataclass(frozen=True)
class CellRequest:
    """A table, a row and a column, with whatever else the locator says about the row."""

    anchor: Anchor
    row: str
    column: str
    #: Selector values past the row label and the column heading, which the row has to print.
    keys: tuple[str, ...] = ()
    #: Column headings past the first, each a level under the one before.
    sub_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Level:
    line: int
    cells: list[Cell]
    at: int


def overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Whether two extents on a line share any position. Touching end to end is not sharing."""
    return a[0] < b[1] and b[0] < a[1]


def tokens_of(text: str, start: int) -> list[tuple[int, int]]:
    """The runs of characters that are not spaces in a field, with their places on the line."""
    return [(start + found.start(), start + found.end()) for found in _TOKEN.finditer(text)]


def cell_width(bands: Sequence[tuple[int, int]], text: str, start: int) -> str | None:
    """Whether the picked field is one cell, or several columns the renderer joined.

    `bands` are the heading level's fields on their own line, and `text` at `start` is the
    row's field, everything counted from the start of its own line. The field runs across
    more than one column when two of its runs sit under two different headings, and what is
    returned then is the detail of the refusal. `None` is one cell.
    """
    crossed: set[int] = set()
    for token in tokens_of(text, start):
        for at, band in enumerate(bands):
            if overlap(token, band):
                crossed.add(at)
                break
    if len(crossed) <= 1:
        return None
    return (
        f"the field runs across {len(crossed)} columns, and the layer prints no boundary inside it "
        "that says which part is the cell"
    )


def _header(index: LayerIndex, window: tuple[int, int], request: CellRequest) -> _Level | Refusal:
    wanted = column_forms(request.column)
    for line in range(*window):
        cells = cells_of(index, line)
        if len(cells) < 2:  # noqa: PLR2004 - a line of one cell is a title or a sentence
            continue
        named = [at for at, cell in enumerate(cells) if _names_column(cell, wanted)]
        if len(named) > 1:
            return refuse(
                "column_ambiguous",
                f'the header on line {line + 1} prints "{request.column}" {len(named)} times',
                index.layer_sha256,
            )
        if named:
            return _Level(line, cells, named[0])
    return refuse(
        "column_not_found",
        f'no line between {window[0] + 1} and {window[1]} carries the column "{request.column}"',
        index.layer_sha256,
    )


def _sub_header(
    index: LayerIndex, window: tuple[int, int], request: CellRequest, header: _Level
) -> _Level | Refusal:
    """The level under the top heading that the locator's second heading names."""
    subs = request.sub_columns
    if len(subs) > 1:
        return refuse(
            "column_ambiguous",
            f'the locator names {len(subs)} levels under "{request.column}" and this resolver reads one',
            index.layer_sha256,
        )
    parent_base = index.u16[header.line]
    parent = header.cells[header.at]
    wanted = column_forms(subs[0])
    under: list[_Level] = []
    for line in range(header.line + 1, min(header.line + 1 + SUB_HEADER_LINES, window[1])):
        cells = cells_of(index, line)
        base = index.u16[line]
        under += [
            _Level(line, cells, at)
            for at, cell in enumerate(cells)
            if _names_column(cell, wanted)
            and cell.start - base < parent.end - parent_base
            and parent.start - parent_base < cell.end - base
        ]
        if under:
            break
    if not under:
        return refuse(
            "column_not_found",
            f'no line under the header prints "{subs[0]}" inside the band of "{request.column}"',
            index.layer_sha256,
        )
    if len(under) > 1:
        return refuse(
            "column_ambiguous",
            f'{len(under)} fields under "{request.column}" print "{subs[0]}"',
            index.layer_sha256,
        )
    return under[0]


def _row(index: LayerIndex, window: tuple[int, int], request: CellRequest, level: _Level) -> int | Refusal:
    """The one line of the window that starts with the row label and prints every key."""
    wanted_row = normalise_label(request.row)
    wanted_column = column_forms(request.column)
    labelled = []
    for line in range(level.line + 1, window[1]):
        cells = cells_of(index, line)
        if cells and normalise_label(cells[0].text) == wanted_row:
            labelled.append(line)
    if not labelled:
        return refuse(
            "row_not_found",
            f'no line between {level.line + 2} and {window[1]} starts with the row "{request.row}"',
            index.layer_sha256,
        )
    keys = [
        key
        for key in map(normalise_label, request.keys)
        if key and key != wanted_row and key not in wanted_column
    ]
    rows = [line for line in labelled if all(key in normalise_label(index.lines[line]) for key in keys)]
    if not rows:
        named = " and ".join(f'"{key}"' for key in keys)
        return refuse(
            "row_key_unmatched",
            f'the row "{request.row}" is printed, and not on a line that also prints {named}',
            index.layer_sha256,
        )
    if len(rows) > 1:
        return refuse(
            "row_ambiguous",
            f'{len(rows)} lines in the window start with the row "{request.row}"',
            index.layer_sha256,
        )
    return rows[0]


def _resolve_at(  # noqa: C901, PLR0912 - one reason to a return
    index: LayerIndex, window: tuple[int, int], request: CellRequest
) -> Resolution:
    header = _header(index, window, request)
    if isinstance(header, Refusal):
        return header
    level = header
    if request.sub_columns:
        under = _sub_header(index, window, request, header)
        if isinstance(under, Refusal):
            return under
        level = under
    line = _row(index, window, request, level)
    if isinstance(line, Refusal):
        return line
    cells = cells_of(index, line)

    # The data cell is the one whose centre is nearest the heading's centre. Both offsets are
    # taken from the start of their own line.
    header_base = index.u16[level.line]
    row_base = index.u16[line]
    centres = [_centre(cell, header_base) for cell in level.cells]
    band = level.cells[level.at]
    nearest = []
    for at, cell in enumerate(cells):
        if at == 0 and level.at != 0:
            continue
        here = _centre(cell, row_base)
        best = 0
        for other in range(1, len(centres)):
            if abs(centres[other] - here) < abs(centres[best] - here):
                best = other
        if best == level.at:
            nearest.append(cell)
    picked = [
        cell
        for cell in nearest
        if cell.start - row_base < band.end - header_base and band.start - header_base < cell.end - row_base
    ]
    if not picked and nearest:
        return refuse(
            "column_unaligned",
            f'row "{request.row}" prints {len(nearest)} field(s) nearest column "{request.column}" '
            "and none of them overlaps it",
            index.layer_sha256,
        )
    if not picked:
        return refuse(
            "cell_empty",
            f'row "{request.row}" prints nothing under column "{request.column}"',
            index.layer_sha256,
        )
    if len(picked) > 1:
        return refuse(
            "column_ambiguous",
            f'{len(picked)} fields on row "{request.row}" sit under column "{request.column}"',
            index.layer_sha256,
        )
    merged = cell_width(
        [(cell.start - header_base, cell.end - header_base) for cell in level.cells],
        picked[0].text,
        picked[0].start - row_base,
    )
    if merged is not None:
        return refuse(
            "column_merged",
            f'row "{request.row}" under column "{request.column}": {merged}',
            index.layer_sha256,
        )
    return Resolved(
        "table_cell",
        span_at(index, picked[0].start, picked[0].end),
        index.layer_sha256,
        row_label=cells[0].text,
        column_header=level.cells[level.at].text,
    )


_Stage = Callable[[LayerIndex, int], tuple[int, int] | None]
_STAGES: Final[tuple[_Stage, ...]] = (table_window, block_above, wider_window)


def resolve_table_cell(index: LayerIndex, request: CellRequest) -> Resolution:  # noqa: C901 - three stages, each a loop
    """The span of one table cell, or the reason there is none.

    Three readings of where the table is, in order: under each caption to the next caption
    or page break, above each caption, and under each caption across a page break. One
    region across all the captions has to yield the cell. Two that both do are a refusal.
    """
    if index.normaliser not in ALIGNED_NORMALISERS:
        return refuse(
            "no_column_geometry",
            f"{index.normaliser} prints no column positions, so a row and column cannot be crossed",
            index.layer_sha256,
        )
    word = "Figure" if request.anchor.kind == "figure" else "Table"
    value = request.anchor.value
    captions = caption_lines(index, word, value)
    if not captions:
        return refuse("anchor_not_found", f"no caption line names {word} {value}", index.layer_sha256)
    blocks = 0
    refusal: Refusal | None = None
    for stage in _STAGES:
        hits: list[Resolved] = []
        for caption in captions:
            window = stage(index, caption)
            if window is None or window[1] <= window[0]:
                continue
            blocks += 1
            outcome = _resolve_at(index, window, request)
            if isinstance(outcome, Resolved):
                hits.append(outcome)
            elif refusal is None or progress(outcome.reason) > progress(refusal.reason):
                refusal = outcome
        if len(hits) == 1:
            return replace(hits[0], anchor_candidates=blocks) if blocks > 1 else hits[0]
        if hits:
            return refuse(
                "anchor_ambiguous",
                f"{len(hits)} regions around a caption for {word} {value} hold this row and column",
                index.layer_sha256,
            )
    if blocks == 0:
        return refuse(
            "empty_table_window",
            f"every caption for {word} {value} stands between two captions",
            index.layer_sha256,
        )
    # A block that was read ends in a span or a refusal, so one is held here.
    return refusal or refuse("anchor_not_found", f"no caption line names {word} {value}", index.layer_sha256)


def resolve_figure_caption(index: LayerIndex, value: str) -> Resolution:
    """The caption of the figure this anchor names, as a span.

    A figure's numbers are in the image, and an image has no text layer. What a layer does
    hold is the caption, which is the text a reader checks a figure-sourced claim against.
    """
    captions = caption_lines(index, "Figure", value)
    if not captions:
        return refuse("anchor_not_found", f"no caption line names Figure {value}", index.layer_sha256)
    if len(captions) > 1:
        return refuse(
            "anchor_ambiguous", f"{len(captions)} caption lines name Figure {value}", index.layer_sha256
        )
    first = captions[0]
    limit = min(table_window(index, first)[1], first + 1 + CAPTION_LINES)
    last = first
    for line in range(first + 1, limit):
        if not index.lines[line].strip(SPACE):
            break
        last = line
    opening = index.lines[first]
    return Resolved(
        "figure_caption",
        span_at(
            index,
            index.u16[first] + len(opening) - len(opening.lstrip(SPACE)),
            index.u16[last] + len(_TRAILING_SPACE.sub("", index.lines[last])),
        ),
        index.layer_sha256,
    )
