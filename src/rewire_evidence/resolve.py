"""From a locator to a span: which resolver a locator's shape calls for.

This is `shared/omics/spans/index.ts`. The grammar reads a stored locator into anchors,
addresses, selectors and qualifiers, and says what the locator means. It does not say where
in a layer the value is. Given a parse and one indexed layer, `resolve_locator` picks the
resolver the locator's shape calls for and returns a span or one stated reason for none.
Nothing here reads a stored value and nothing calls a model.

The order of the tests is the order of specificity. A locator that quotes its own text is
resolved by the quote, a cell comes next, then a region. Nothing falls through to a looser
resolver: a locator that names a row and no column is refused, not resolved to the row.

A parse arrives in characters, as `locator.parse_locator` gives it, and is put into units
here, which is how the resolvers hold all text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from rewire_evidence.aligned import CellRequest, normalise_label, resolve_figure_caption, resolve_table_cell
from rewire_evidence.grids import grid_normaliser_for
from rewire_evidence.gridspans import GridCellRequest, TableGrid, grid_request, resolve_grid_cell
from rewire_evidence.js import DOT, SPACE, SPACE_CLASS, number_of_text
from rewire_evidence.jsonspans import JsonSelector, resolve_json_pointer
from rewire_evidence.jsregex import JsPattern
from rewire_evidence.locator import (
    Address,
    Anchor,
    Parsed,
    Qualifier,
    Selector,
    carries_no_address,
    selector_role,
)
from rewire_evidence.offsets import LayerIndex, count_points, to_units
from rewire_evidence.prose import resolve_quote, resolve_section_paragraph
from rewire_evidence.regions import (
    FILE_NORMALISERS,
    resolve_delimited_cell,
    resolve_keyed_row,
    resolve_line_range,
    resolve_page,
)
from rewire_evidence.resolution import Resolution, Resolved, refuse
from rewire_evidence.sheets import is_sheet_layer, resolve_sheet_cell, sheet_request

#: Keys whose first word names the row or the column, where the rest of the key is the label.
_ROW_WORDS: Final = frozenset({"row", "rows"})
_COLUMN_WORDS: Final = frozenset({"column", "col", "columns", "sub-column", "sub-col"})
#: Addresses that say where to look and not what to find. A quoted run printed before the
#: last of these is a label for one of them and not the evidence.
_PLACE_KINDS: Final = frozenset(
    {"row_number", "line", "paragraph", "sheet_cell", "cell_range", "xml_cell", "json_path"}
)
#: Which closing mark each opening mark takes.
_CLOSERS: Final = {"'": "'", '"': '"', "\u2018": "\u2019", "\u201c": "\u201d"}
#: How long a quoted run has to be before it is taken as the evidence and not a label.
EVIDENCE_POINTS: Final = 8
#: How long it has to be where the grammar read no quote at all.
LONG_RUN_POINTS: Final = 40

_S: Final = f"[{SPACE_CLASS}]"
_SPACES: Final = re.compile(f"{_S}+")
_LABEL_FROM_RAW: Final = re.compile(rf"\A{_S}*[^{SPACE_CLASS}]+{_S}+({DOT}+)\Z")
_DIGIT: Final = re.compile("[0-9]")
_DIGITS: Final = re.compile("[0-9]+")
_BEFORE_A_RUN: Final = re.compile(rf"[{SPACE_CLASS}:,'\"\u2018\u201c]+\Z")
#: The phrase in front of a quoted run that says the run opens a sentence.
_SENTENCE_PHRASE: Final = re.compile(
    rf"\b(?:sentence|sentences)(?:{_S}+(?:beginning|starting|begins|opening|that begins|which begins))?\Z",
    re.IGNORECASE | re.ASCII,
)
#: A container's object number that is the publisher's paragraph number.
_PUBLISHER_PARAGRAPH: Final = re.compile(rf"P\.?{_S}*[0-9]{{1,3}}", re.IGNORECASE | re.ASCII)
#: Numbers in the text the grammar did not read, of two digits at least.
RESIDUE_NUMBER: Final = JsPattern(r"(?<![\p{L}\p{N}.])\d{2,}(?:\.\d+)?(?![\p{L}\p{N}.])", "gu")
#: The name of a block in that text: the word before `block`, `group`, `panel`, `section` or `part`.
RESIDUE_BLOCK: Final = JsPattern(
    r"(?<word>[\p{L}\p{N}][\p{L}\p{N}-]*)\s+(?:block|group|panel|section|part)\b", "giu"
)
PATTERNS: Final = (RESIDUE_NUMBER, RESIDUE_BLOCK)


def in_units(parsed: Parsed) -> Parsed:
    """A parse with all its text in units. One with no character beyond U+FFFF is returned as it is."""
    if to_units(parsed.printed) is parsed.printed:
        return parsed
    return Parsed(
        to_units(parsed.printed),
        tuple(
            Anchor(
                held.kind,
                to_units(held.value),
                to_units(held.raw),
                None if held.container is None else to_units(held.container),
            )
            for held in parsed.anchors
        ),
        tuple(
            Address(
                held.kind,
                to_units(held.value),
                to_units(held.raw),
                None if held.value2 is None else to_units(held.value2),
            )
            for held in parsed.addresses
        ),
        tuple(
            Selector(
                to_units(held.key),
                None if held.value is None else to_units(held.value),
                held.form,
                to_units(held.raw),
            )
            for held in parsed.selectors
        ),
        tuple(Qualifier(held.kind, to_units(held.text)) for held in parsed.qualifiers),
        tuple(to_units(held) for held in parsed.residue),
        parsed.verdict,
    )


def _label_from_raw(selector: Selector) -> str | None:
    """The whole printed phrase after a selector's first word, which is the label it names."""
    found = _LABEL_FROM_RAW.match(selector.raw)
    return found[1].strip(SPACE) if found else None


def _roles(selectors: Sequence[Selector]) -> tuple[str | None, str | None, list[str], list[str]]:
    """The first selector that names the row, the first that names the column, and the rest.

    The rest are keys the accepted row has to print, and column headings past the first.
    """
    row: str | None = None
    column: str | None = None
    keys: list[str] = []
    sub_columns: list[str] = []
    for selector in selectors:
        if selector.value is None:
            continue
        role = selector_role(selector.key)
        if role == "row" and row is None:
            row = selector.value
        elif role == "column" and column is None:
            column = selector.value
        elif role == "column":
            sub_columns.append(selector.value)
        elif role == "other":
            first = _SPACES.split(selector.key)[0]
            if first in _ROW_WORDS and row is None:
                row = _label_from_raw(selector)
            elif first in _COLUMN_WORDS and column is None:
                column = _label_from_raw(selector)
            else:
                keys.append(selector.value)
    return row, column, keys, sub_columns


def _residue_keys(residue: Sequence[str]) -> list[str]:
    """Numbers and block names in the text the grammar did not read, which say which row was meant."""
    text = " ".join(residue)
    numbers = [found.text for found in RESIDUE_NUMBER.all(text)]
    blocks = [
        word
        for word in (found.groups["word"] or "" for found in RESIDUE_BLOCK.all(text))
        if len(word) >= 2 and not carries_no_address(word)  # noqa: PLR2004 - one character names no block
    ]
    return list(dict.fromkeys((*numbers, *blocks)))


def _quoted_runs(printed: str) -> list[tuple[str, int]]:
    """The quoted runs of a printed locator, in the order printed: each text, and where it starts."""
    runs: list[tuple[str, int]] = []
    at = 0
    while at < len(printed):
        closer = _CLOSERS.get(printed[at])
        if closer is not None:
            end = printed.find(closer, at + 1)
            if end < 0:
                break
            runs.append((printed[at + 1 : end].strip(SPACE), at + 1))
            at = end
        at += 1
    return runs


def _names_sentence(printed: str, at: int) -> bool:
    """Whether the locator says the run at `at` opens a sentence, and does not stand for one."""
    return _SENTENCE_PHRASE.search(_BEFORE_A_RUN.sub("", printed[: max(0, at - 1)])) is not None


def _place_cut(parsed: Parsed) -> int:
    """Where the last part of the locator that names a place ends in the printed text, or -1."""
    cut = -1
    raws = [address.raw for address in parsed.addresses if address.kind in _PLACE_KINDS]
    raws += [selector.raw for selector in parsed.selectors if selector_role(selector.key) != "other"]
    for raw in raws:
        at = parsed.printed.rfind(raw)
        if at >= 0:
            cut = max(cut, at + len(raw))
    return cut


def _label_values(parsed: Parsed) -> set[str]:
    """The text of every part the grammar read as a label, which a quoted run may only repeat."""
    labels = {normalise_label(anchor.value) for anchor in parsed.anchors}
    labels |= {
        normalise_label(selector.value)
        for selector in parsed.selectors
        if selector.value is not None and selector_role(selector.key) != "other"
    }
    return labels


def _quoted_addresses(parsed: Parsed) -> list[Address]:
    return [address for address in parsed.addresses if address.kind in {"quote", "sentence"}]


def _last_numbered(runs: Sequence[tuple[str, int]]) -> tuple[str, int] | None:
    """Of the candidate runs, the last that prints a number, or the last of them all."""
    numbered = [run for run in runs if _DIGIT.search(run[0])]
    pool = numbered or runs
    return pool[-1] if pool else None


def _evidence_quote(parsed: Parsed) -> tuple[str, bool] | None:
    """The text a quoting locator says to go and find, and whether it names a whole sentence.

    A locator that quotes anything usually quotes the section it was read from and then the
    evidence. The last quoted run is the evidence, read off the printed text because it is
    often shorter than the grammar's own threshold for a quote.
    """
    quoted = _quoted_addresses(parsed)
    cut = _place_cut(parsed)
    labels = _label_values(parsed)
    floor = EVIDENCE_POINTS if quoted else LONG_RUN_POINTS
    last = _last_numbered(
        [
            (text, at)
            for text, at in _quoted_runs(parsed.printed)
            if (count_points(text) >= floor or _names_sentence(parsed.printed, at))
            and at > cut
            and normalise_label(text) not in labels
        ]
    )
    address = quoted[-1] if quoted else None
    address_at = -1 if address is None else parsed.printed.rfind(address.value)
    # A run wins over the quote address only where it is printed after it.
    if last and last[1] > address_at:
        return last[0], _names_sentence(parsed.printed, last[1])
    # An address printed before the place the locator names is a label for that place.
    if address and address_at > cut:
        return address.value, address.kind == "sentence" or _names_sentence(parsed.printed, address_at)
    return None


def _legacy_evidence_quote(parsed: Parsed) -> tuple[str, bool] | None:
    """The quote selection as it stood before the label rule, kept so the rule can be measured."""
    quoted = _quoted_addresses(parsed)
    if not quoted:
        return None
    address = quoted[-1]
    chosen = (address.value, address.kind == "sentence")
    last = _last_numbered(
        [run for run in _quoted_runs(parsed.printed) if count_points(run[0]) >= EVIDENCE_POINTS]
    )
    if last is None or last[0] == chosen[0]:
        return chosen
    return (last[0], False) if last[1] > parsed.printed.rfind(address.value) else chosen


def _quoted_section(parsed: Parsed, paragraph: Address) -> str | None:
    """The section name a locator quotes, where it quotes one before the paragraph it names."""
    at = parsed.printed.rfind(paragraph.raw)
    name: str | None = None
    for address in _quoted_addresses(parsed):
        position = parsed.printed.rfind(address.raw)
        if position >= 0 and (at < 0 or position < at):
            name = address.value
    return name


def _page_of(parsed: Parsed) -> float | None:
    """A page number from a page qualifier, where the qualifier holds one."""
    for qualifier in parsed.qualifiers:
        found = _DIGITS.search(qualifier.text) if qualifier.kind == "page" else None
        if found:
            return number_of_text(found[0])
    return None


def _usable_grid(index: LayerIndex, grid: TableGrid | None) -> TableGrid | None:
    """The grid to cross a row and a column against, or `None`.

    It has to be the grid normaliser that shadows this text normaliser, to name this layer
    as the one its offsets are into, and to hold a table.
    """
    if (
        grid is None
        or grid_normaliser_for(index.normaliser) != grid.normaliser
        or grid.text_layer_sha256 != index.layer_sha256
    ):
        return None
    return grid if grid.tables else None


def resolve_locator(
    parsed: Parsed,
    index: LayerIndex,
    grid: TableGrid | None = None,
    *,
    legacy_quote: bool = False,
    sheet: bool = True,
) -> Resolution:
    """The span a parsed locator names in this layer, or the one reason it does not.

    `legacy_quote` selects the quote as it was selected before the label rule, and `sheet`
    set to false leaves a sheet layer's blocks unread. Both are there so a rule's effect can
    be measured against the same store.
    """
    outcome = _resolve_parsed(
        in_units(parsed), index, _usable_grid(index, grid), legacy_quote=legacy_quote, sheet=sheet
    )
    # A span has to cover characters. One over a blank line states a place where nothing is printed.
    if isinstance(outcome, Resolved) and not outcome.span.exact:
        return refuse(
            "empty_span", "the address names a place in the layer that prints nothing", index.layer_sha256
        )
    return outcome


def _find(held: Sequence[Address], *kinds: str) -> Address | None:
    return next((address for address in held if address.kind in kinds), None)


def _anchor(held: Sequence[Anchor], *kinds: str) -> Anchor | None:
    return next((anchor for anchor in held if anchor.kind in kinds), None)


def _resolve_parsed(  # noqa: C901, PLR0912, PLR0915 - the dispatch is one test to a return
    parsed: Parsed, index: LayerIndex, grid: TableGrid | None, *, legacy_quote: bool, sheet: bool
) -> Resolution:
    layer = index.layer_sha256
    row, column, keys, sub_columns = _roles(parsed.selectors)
    addresses = parsed.addresses
    anchors = parsed.anchors
    table = _anchor(anchors, "table", "figure", "sheet")
    sheet_cell = _find(addresses, "sheet_cell")
    line_like = _find(addresses, "line", "row_number")

    # A sheet layer keeps the geometry of the workbook, so a cell reference on it is an
    # address. It is read before everything else on this layer: a quoted run in such a
    # locator is a sheet name, a row label or a column heading, and the layer holds no prose.
    if is_sheet_layer(index.normaliser) and sheet:
        request = sheet_request(parsed)
        if request:
            return resolve_sheet_cell(index, request)

    # A quote is the strongest evidence a locator carries and is resolved first, except that a
    # locator naming a table, a row and a column addresses a cell, and the long quoted run in
    # it is a label.
    if legacy_quote:
        addresses_a_cell = bool(table and row and column)
    else:
        addresses_a_cell = bool(table and column and (row or line_like))
    if not addresses_a_cell:
        quoted = _legacy_evidence_quote(parsed) if legacy_quote else _evidence_quote(parsed)
        if quoted:
            return resolve_quote(index, quoted[0], whole=quoted[1])

    # An address into a file reads only against a layer that is the file.
    is_file = index.normaliser in FILE_NORMALISERS
    if sheet_cell and is_file:
        return resolve_delimited_cell(index, cell=sheet_cell.value, column=column)
    if line_like and column and is_file:
        return resolve_delimited_cell(index, line=number_of_text(line_like.value), column=column)
    json_path = _find(addresses, "json_path")
    if json_path:
        return resolve_json_pointer(
            index,
            json_path.value,
            [
                JsonSelector(selector.key, selector.value, selector_role(selector.key))
                for selector in parsed.selectors
                if selector.value is not None
            ],
        )
    if is_file and column and (row is not None or keys):
        constraints: list[tuple[str | None, str]] = [] if row is None else [(None, row)]
        constraints += [
            (selector.key, selector.value)
            for selector in parsed.selectors
            if selector.value is not None and selector_role(selector.key) == "other"
        ]
        # The TypeScript goes on from a refusal here where the locator also holds a cell or a
        # line. One that does was resolved above, against this file, so the refusal is returned.
        return resolve_keyed_row(index, column, constraints)
    names_a_cell = bool(table and row and column)
    if sheet_cell and not is_file and not names_a_cell:
        return refuse(
            "no_column_geometry", f"{index.normaliser} is not the file the cell address names", layer
        )
    if line_like and column and not is_file and not names_a_cell:
        return refuse(
            "no_column_geometry", f"{index.normaliser} is not the file the line address names", layer
        )

    if table and row and column:
        all_keys = (*keys, *_residue_keys(parsed.residue))
        if grid is None:
            return resolve_table_cell(index, CellRequest(table, row, column, all_keys, tuple(sub_columns)))
        # A grid in hand is the geometry the text layer lost, so it is read and the aligned
        # resolver is not.
        return resolve_grid_cell(
            index,
            grid,
            grid_request(
                [(selector.key, selector.value) for selector in parsed.selectors],
                [qualifier.text for qualifier in parsed.qualifiers if qualifier.kind == "note"],
                GridCellRequest(table, row, column, all_keys, tuple(sub_columns)),
                selector_role,
            ),
        )
    if table and row:
        return refuse("no_column_in_locator", "the locator names a row and no column", layer)
    if table and column and table.kind != "figure":
        return refuse("no_row_in_locator", "the locator names a column and no row", layer)

    figure = _anchor(anchors, "figure")
    if figure and not row and not column:
        return resolve_figure_caption(index, figure.value)

    paragraph = _find(addresses, "paragraph")
    section = _anchor(anchors, "container")
    # "Results P13" names the publisher's paragraph 13, and the publisher's numbering is not
    # the count of blocks in the layer. A count that cannot be reproduced is refused.
    if section and section.container and _PUBLISHER_PARAGRAPH.fullmatch(section.value):
        return refuse(
            "no_resolver_for_kind",
            f'"{section.container} {section.value}" is the publisher\'s paragraph number, '
            "which this layer does not carry",
            layer,
        )
    if paragraph and section:
        named = None if legacy_quote else _quoted_section(parsed, paragraph)
        if named is None:
            named = section.value if section.container is None else section.container
        return resolve_section_paragraph(index, named, number_of_text(paragraph.value))
    if paragraph:
        return refuse("no_anchor", "a paragraph number with no section names no place", layer)

    if line_like:
        # "line 446" counts lines of the text layer, so it reads against any layer. "row 7"
        # counts rows of a table, and a table row is a line only where the layer is the file.
        if line_like.kind == "row_number" and not is_file:
            return refuse(
                "no_resolver_for_kind",
                "a table row number needs a layer that is the file, not a rendering of it",
                layer,
            )
        same = [address for address in addresses if address.kind == line_like.kind]
        first = number_of_text(line_like.value)
        return resolve_line_range(index, first, number_of_text(same[1].value) if len(same) > 1 else first)

    page = _page_of(parsed)
    if page is not None:
        return resolve_page(index, page)

    if not anchors and not addresses:
        return refuse("no_anchor", "the locator addresses nothing the grammar reads", layer)
    kinds = sorted({*(address.kind for address in addresses), *(anchor.kind for anchor in anchors)})
    return refuse("no_resolver_for_kind", f"no resolver reads {' plus '.join(kinds)}", layer)
