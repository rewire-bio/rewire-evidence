r"""Tables read out of an HTML or JATS XML artifact: the reader of `scripts/omics/extract/tables.ts`.

Papers reach the data repository in three shapes: LaTeXML output from arXiv, where a table
sits in a `figure` beside its `figcaption`; JATS full text from Europe PMC, where it sits in
a `table-wrap` beside a `label` and a `caption`; and plain HTML. `tables` reads all three
into rows of cells, so an extractor works on cells and not on a text layout. The other half
of that file, the reading of one printed cell, is `rewire_evidence.cells`.

The reader is patterns over text and no parser, and what each pattern matches is the
specification. They are the TypeScript's, run through `jsregex`, with three changes to the
text of some, none of which changes what is matched:

- A group JavaScript numbers is given a name, because `jsregex` runs only named groups.
- A group that is never read is written as one that captures nothing.
- `<(script|style)\b...<\/\1>` and `<(t[dh])\b...<\/\1>` close a tag with whatever opened
  it. `jsregex` runs no reference to a group, so each is written as its alternatives in
  turn: a `script` closed by `script` or a `style` by `style`, a `td` by `td` or a `th` by
  `th`. Under the `i` flag a reference matches in any case, as the letters written out do.

`tests/fixtures/tables.json` holds every pattern as the TypeScript compiler reads it out of
the file, and a test holds each one here to it with those changes undone.

Four things differ from the TypeScript, on purpose.

- What it throws as an `Error` or a `RangeError` is a `TableError` here, with its message.
- A `colspan` above `MAX_SPAN` is refused. The TypeScript pads the row with that many empty
  cells, and for a number too long to be finite it never returns.
- A pattern handed to `table_matching` or `layout_rows` may not have the `g` flag. With it
  JavaScript's `test` starts where its last match ended, so the answer depends on the call
  before.
- Text is taken and given as Python holds it, a character beyond U+FFFF as one character.
  Inside, the patterns see UTF-16 units, as JavaScript's do.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from rewire_evidence.canonical import js_string
from rewire_evidence.js import number_of_text, paired, trim, unit_text
from rewire_evidence.jsregex import Found, JsPattern, Unsupported

#: The widest span of columns a cell may state.
MAX_SPAN = 1000
_LAST_CODE_POINT = 0x10FFFF

#: The references `decode` reads by name. A name is held in one case only.
ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    # A space that does not break is read as a plain space.
    "nbsp": " ",
    "ndash": "\u2013",
    "mdash": "\u2014",
    "minus": "\u2212",
    "plusmn": "\u00b1",
    "times": "\u00d7",
    "rho": "\u03c1",
    "sigma": "\u03c3",
    "alpha": "\u03b1",
    "beta": "\u03b2",
    "Delta": "\u0394",
    "uarr": "\u2191",
    "darr": "\u2193",
}
#: The TypeScript's table of names is an object, and `ENTITIES[name]` also finds what every
#: object has under a name: these seven functions, each written out as V8 writes a built-in.
INHERITED = (
    "constructor",
    "hasOwnProperty",
    "isPrototypeOf",
    "propertyIsEnumerable",
    "toLocaleString",
    "toString",
    "valueOf",
)

HEX_REFERENCE = JsPattern(r"&#x(?<hex>[0-9a-f]+);", "gi")
DECIMAL_REFERENCE = JsPattern(r"&#(?<decimal>\d+);", "g")
NAMED_REFERENCE = JsPattern(r"&(?<name>[a-z]+);", "gi")
#: The TeX copy of a formula, which LaTeXML writes beside its glyphs.
ANNOTATION = JsPattern(r"<annotation\b[^>]*>[\s\S]*?<\/annotation>", "gi")
SCRIPT_OR_STYLE = JsPattern(r"<script\b[^>]*>[\s\S]*?<\/script>|<style\b[^>]*>[\s\S]*?<\/style>", "gi")
TAG = JsPattern(r"<[^>]+>", "g")
SPACES = JsPattern(r"\s+", "g")
CELL = JsPattern(
    r"<td\b(?<d_attributes>[^>]*)>(?<d_body>[\s\S]*?)<\/td>|<th\b(?<h_attributes>[^>]*)>(?<h_body>[\s\S]*?)<\/th>",
    "gi",
)
COLSPAN = JsPattern(r'colspan\s*=\s*"?(?<span>\d+)', "i")
ROW = JsPattern(r"<tr\b[^>]*>(?<body>[\s\S]*?)<\/tr>", "gi")
#: The element is `table` and not `table-wrap`: a word boundary would match both.
TABLE_OPEN = JsPattern(r"<table(?=[\s/>])[^>]*>", "gi")
TABLE_TAG = JsPattern(r"<table(?=[\s/>])[^>]*>|<\/table\s*>", "gi")
FIGCAPTION = JsPattern(r"<figcaption\b[^>]*>(?<body>[\s\S]*?)<\/figcaption>", "gi")
LABEL = JsPattern(r"<label\b[^>]*>(?<body>[\s\S]*?)<\/label>", "gi")
CAPTION = JsPattern(r"<caption\b[^>]*>(?<body>[\s\S]*?)<\/caption>", "gi")
UP = JsPattern(r"\u2191")
DOWN = JsPattern(r"\u2193")
TRAILING_SPACE = JsPattern(r"\s+$")
#: Two spaces or more, which is where a text layer puts one column after another. The
#: TypeScript splits by this pattern with no flag, and every match is found here by `g`.
GUTTER = JsPattern(r"\s{2,}", "g")
#: A full-size initial and the smaller capitals a text layer sets apart from it.
SMALL_CAPS = JsPattern(r"\b[A-Z] [A-Z]{2,}(?: [A-Z]{2,})*", "g")

#: Every pattern this module runs, for the list of what has to be recorded from Node.
PATTERNS: tuple[JsPattern, ...] = (
    HEX_REFERENCE,
    DECIMAL_REFERENCE,
    NAMED_REFERENCE,
    ANNOTATION,
    SCRIPT_OR_STYLE,
    TAG,
    SPACES,
    CELL,
    COLSPAN,
    ROW,
    TABLE_OPEN,
    TABLE_TAG,
    FIGCAPTION,
    LABEL,
    CAPTION,
    UP,
    DOWN,
    TRAILING_SPACE,
    GUTTER,
    SMALL_CAPS,
)


class TableError(Exception):
    """What the reader throws: a table not closed, a reference to no character, a row of the wrong width."""


@dataclass(frozen=True)
class Table:
    """One table of an artifact: its caption, and its rows of cells as visible text."""

    caption: str
    rows: list[list[str]]


@dataclass(frozen=True)
class Sectioned:
    """A row with the section label that stands over it."""

    section: str
    cells: list[str]


@dataclass(frozen=True)
class Block:
    """Header rows and the data rows under them."""

    #: Column headings above the metric row, joined top to bottom.
    groups: list[str]
    #: The metric row itself, one entry for each column.
    metrics: list[str]
    rows: list[list[str]]


def _replaced(pattern: JsPattern, text: str, written: Callable[[Found], str]) -> str:
    """`text.replace(pattern, function)` for a pattern with the `g` flag that takes a character."""
    out: list[str] = []
    kept = 0
    for found in pattern.all(text):
        out += (text[kept : found.index], written(found))
        kept = found.index + len(found.text)
    return "".join((*out, text[kept:]))


def _character(code: float) -> str:
    """`String.fromCodePoint(code)`, in units. Raises `TableError` with the `RangeError`'s message."""
    if code > _LAST_CODE_POINT:
        raise TableError(f"Invalid code point {js_string(code)}")
    return unit_text(chr(int(code)))


def _of_hex(found: Found) -> str:
    # `parseInt(hex, 16)` is the nearest double to the whole number, and infinite beyond the last.
    try:
        code = float(int(found.groups["hex"] or "", 16))
    except OverflowError:
        code = float("inf")
    return _character(code)


def _of_decimal(found: Found) -> str:
    return _character(number_of_text(found.groups["decimal"] or ""))


def _of_name(found: Found) -> str:
    name = found.groups["name"] or ""
    if name in INHERITED:
        return f"function {'Object' if name == 'constructor' else name}() {{ [native code] }}"
    return ENTITIES.get(name, found.text)


def _decoded(text: str) -> str:
    """`decode(text)`, on text in units."""
    text = _replaced(HEX_REFERENCE, text, _of_hex)
    text = _replaced(DECIMAL_REFERENCE, text, _of_decimal)
    return _replaced(NAMED_REFERENCE, text, _of_name)


def decode(text: str) -> str:
    """`decode(text)`: character references as the characters they name.

    References in hexadecimal are read first, then in decimal, then by name, each in one
    pass over what the pass before left, so `&#38;lt;` is read twice and is `<`. A name the
    table does not hold stays as written. Raises `TableError` for a number past U+10FFFF.
    """
    return paired(_decoded(unit_text(text)))


def _visible(fragment: str) -> str:
    """`text(fragment)`, on text in units."""
    bare = TAG.replace(SCRIPT_OR_STYLE.replace(ANNOTATION.replace(fragment, ""), ""), " ")
    return trim(SPACES.replace(_decoded(bare), " "))


def text(fragment: str) -> str:
    """`text(fragment)`: the visible text of a fragment.

    No TeX copy of a formula, no script or style, no tags, references read, and every run of
    white space made one space. Raises `TableError` as `decode` does.
    """
    return paired(_visible(unit_text(fragment)))


def _span(attributes: str) -> int:
    """How many columns a cell states it spans: `Number(/colspan.../.exec(attributes)?.[1] || 1)`."""
    stated = COLSPAN.exec(attributes)
    # The digits are text, and text that is not empty is true to JavaScript even when it is "0".
    span = 1.0 if stated is None else number_of_text(stated.groups["span"] or "")
    if span > MAX_SPAN:
        raise TableError(f"A cell spans {js_string(span)} columns, and no more than {MAX_SPAN} are read")
    return int(span)


def _cells(row: str) -> list[str]:
    out: list[str] = []
    for found in CELL.all(row):
        groups = found.groups
        header = groups["d_body"] is None
        out.append(paired(_visible((groups["h_body"] if header else groups["d_body"]) or "")))
        # A spanning header labels its first column, and the padding keeps the later ones in place.
        attributes = (groups["h_attributes"] if header else groups["d_attributes"]) or ""
        out.extend("" for _ in range(1, _span(attributes)))
    return out


def _rows(table: str) -> list[list[str]]:
    return [_cells(found.groups["body"] or "") for found in ROW.all(table)]


def _last(pattern: JsPattern, fragment: str) -> str | None:
    """The visible text of the last element a pattern finds, or None where it finds none."""
    found = pattern.all(fragment)
    return _visible(found[-1].groups["body"] or "") if found else None


def _caption(fragment: str) -> str:
    """The caption a fragment holds: its last `figcaption`, or its last `label` and last `caption`."""
    figure = _last(FIGCAPTION, fragment)
    if figure is not None:
        return figure
    return " ".join(part for part in (_last(LABEL, fragment), _last(CAPTION, fragment)) if part)


def tables(source: str) -> list[Table]:
    """`tables(source)`: every table in the artifact, in document order, with its caption.

    A table runs from its opening tag to the closing tag that balances it, so a table
    inside a cell is part of the cell. Its caption is inside it, or in what lies between
    the table before it and its own start, and never after it. Raises `TableError` for a
    table that is not closed, and as `decode` and the limit on a span do.
    """
    held = unit_text(source)
    # Every opening and closing tag in turn. The TypeScript looks for an opening tag from
    # where the last table ended and then counts tags from there, which is one pass over
    # these: a closing tag holds no opening one, and the pass resumes where a table ended.
    tags = TABLE_TAG.all(held)
    out: list[Table] = []
    previous_end = 0
    at = 0
    while at < len(tags):
        opened = tags[at]
        if opened.text.startswith("</"):
            at += 1
            continue
        depth = 0
        end = -1
        for index in range(at, len(tags)):
            depth += -1 if tags[index].text.startswith("</") else 1
            if depth == 0:
                end = tags[index].index + len(tags[index].text)
                at = index + 1
                break
        if end < 0:
            raise TableError("Unclosed table element in artifact")
        body = held[opened.index : end]
        caption = _caption(body) or _caption(held[previous_end : opened.index])
        out.append(Table(paired(caption), _rows(body)))
        previous_end = end
    return out


def _written(pattern: JsPattern) -> str:
    """`String(pattern)`: the pattern between slashes, with its flags."""
    return f"/{pattern.source or '(?:)'}/{pattern.flags}"


def _stateless(pattern: JsPattern) -> JsPattern:
    if "g" in pattern.flags:
        raise Unsupported(
            f"{_written(pattern)} has the g flag, and what JavaScript's test then answers depends on the "
            "call before it"
        )
    return pattern


def table_matching(source: str, pattern: JsPattern) -> Table:
    """`tableMatching(source, pattern)`: the one table whose caption the pattern matches.

    Raises `TableError` when no table or more than one matches, so an extractor never
    guesses an index, and `Unsupported` for a pattern with the `g` flag.
    """
    _stateless(pattern)
    found = [table for table in tables(source) if pattern.test(unit_text(table.caption))]
    if len(found) != 1:
        raise TableError(f"Expected one table matching {_written(pattern)}, found {len(found)}")
    return found[0]


def _blank(cell: str) -> bool:
    """Whether a cell is empty or only white space: `!cell.trim()`."""
    return not trim(unit_text(cell))


def sectioned(rows: Sequence[Sequence[str]], width: int) -> list[Sectioned]:
    """`sectioned(rows, width)`: rows of a table whose first column labels several rows.

    A row that carries the label is one cell wider than the rest. The label is taken from
    it and carried down to the next, and a blank label continues the section. Raises
    `TableError` for a row that is neither width.
    """
    out: list[Sectioned] = []
    section = ""
    for row in rows:
        if len(row) == width + 1:
            if not _blank(row[0]):
                section = row[0]
            out.append(Sectioned(section, list(row[1:])))
        elif len(row) == width:
            out.append(Sectioned(section, list(row)))
        else:
            raise TableError(f"Row has {len(row)} cells, expected {width} or {width + 1}: {' | '.join(row)}")
    return out


def _trimmed(cell: str) -> str:
    return paired(trim(unit_text(cell)))


def _filled(row: Sequence[str], width: int) -> list[str]:
    """A group row with each heading carried across the padding its span left."""
    filled: list[str] = []
    current = ""
    for index in range(width):
        if index < len(row) and not _blank(row[index]):
            current = _trimmed(row[index])
        filled.append(current)
    return filled


def _block(header: Sequence[Sequence[str]]) -> Block:
    width = max(len(row) for row in header)
    groups = [_filled(row, width) for row in header[:-1]]
    return Block(
        # Each heading once, in the order met from the top, as a JavaScript Set keeps them.
        [
            ", ".join(dict.fromkeys(group[index] for group in groups if group[index]))
            for index in range(width)
        ],
        [_trimmed(cell) for cell in header[-1]],
        [],
    )


def headed_blocks(table: Table) -> list[Block]:
    """`headedBlocks(table)`: the table as blocks of header rows followed by their data rows.

    A header row is one whose first cell is empty and which has a cell that is not. A wide
    comparison is often printed as several stacked blocks, each with its own header, and
    reading one as a single grid gives the second block's numbers to the first block's
    columns. A row before the first header, and a block with no rows, are left out.
    """
    out: list[Block] = []
    header: list[Sequence[str]] = []
    for row in table.rows:
        first_blank = not row or _blank(row[0])
        if first_blank and any(not _blank(cell) for cell in row[1:]):
            if out and out[-1].rows:
                header = []
            header.append(row)
            block = _block(header)
            if out and not out[-1].rows:
                out.pop()
            out.append(block)
        elif out and not first_blank:
            out[-1].rows.append(list(row))
    return [block for block in out if block.rows]


def arrow_direction(heading: str) -> Literal["higher", "lower"] | None:
    """`arrowDirection(heading)`: the direction a metric heading marks with an arrow, upward first."""
    held = unit_text(heading)
    if UP.test(held):
        return "higher"
    if DOWN.test(held):
        return "lower"
    return None


def _split(pattern: JsPattern, text: str) -> list[str]:
    """`text.split(pattern)` for a pattern that takes a character and has no groups."""
    out: list[str] = []
    kept = 0
    for found in pattern.all(text):
        out.append(text[kept : found.index])
        kept = found.index + len(found.text)
    return [*out, text[kept:]]


def layout_rows(source: str, start: JsPattern, end: JsPattern) -> list[list[str]]:
    """`layoutRows(source, from, to)`: rows of a table printed in a PDF text layer.

    The rows are the lines after the first that `start` matches and before the next that
    `end` matches, each pattern held to the line with the space around it gone. A PDF has
    no table structure, so columns are where two spaces or more stand together. One space
    is part of a cell. Raises `TableError` when either line is not found, and `Unsupported`
    for a pattern with the `g` flag.
    """
    _stateless(start)
    _stateless(end)
    lines = [TRAILING_SPACE.replace(line, "") for line in unit_text(source).split("\n")]
    first = next((index for index, line in enumerate(lines) if start.test(trim(line))), None)
    if first is None:
        raise TableError(f"Table start not found: {_written(start)}")
    rest = lines[first + 1 :]
    last = next((index for index, line in enumerate(rest) if end.test(trim(line))), None)
    if last is None:
        raise TableError(f"Table end not found: {_written(end)}")
    return [
        [paired(trim(cell)) for cell in _split(GUTTER, line)]
        for line in (trim(line) for line in rest[:last])
        if line
    ]


def join_small_caps(printed: str) -> str:
    """`joinSmallCaps(printed)`: a name a text layer set in small capitals, as one token.

    Small capitals are a full-size initial and smaller capitals, and the extracted text
    puts a space between them: Enformer arrives as "E NFORMER". The capitals are the
    typeface's and are kept.
    """
    return paired(_replaced(SMALL_CAPS, unit_text(printed), lambda found: SPACES.replace(found.text, "")))
