r"""The locator grammar: a typed reading of the free-text `source_locator` a result carries.

This is `shared/omics/locator.ts`, version 2 of the grammar. A locator is prose such as
`Table S18, row 'Bacformer', column 'Binary AUPRC'`, and the grammar reads it into anchors
(a table, a figure, a sheet, a file, a container), addresses (a cell, a line, a JSON path, a
quoted sentence), selectors (a key and a value) and qualifiers (a page, a layer, a note).

    locator   := anchor+ address? selector* qualifier*

A locator is split on `;` and on commas outside quotes and brackets. From each segment the
28 tokens below are taken one at a time: every pattern is tried on what is left, the match
that starts furthest left wins, and of two that start together the earlier token in the
list wins. The match is cut out and a space left in its place, up to 40 times. What remains
is residue unless it is punctuation and words that carry no address.

The patterns are the TypeScript's, character for character, and are run as JavaScript runs
them by `jsregex`. `tests/fixtures/locator.json` holds the patterns as the TypeScript holds
them and what it reads from several thousand locators, and the gate holds this module to
its reading of every locator in the store.

Text is handled in UTF-16 units wherever the TypeScript counts or cuts, because most of the
patterns have no `u` flag and a cut can fall inside a character beyond U+FFFF. What is
returned is ordinary text, in which half a character appears as a lone surrogate.

Not ported: `spanSchema`, `locatorSchema` and the other zod schemas the file declares. No
record holds a span, and the schema of one belongs with the stage that resolves them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rewire_evidence.canonical import JsonObject
from rewire_evidence.js import lower, paired, trim, unit_text
from rewire_evidence.jsregex import Found, JsPattern

ANCHOR_KINDS = ("table", "figure", "sheet", "file", "container")
ADDRESS_KINDS = (
    "sheet_cell",
    "cell_range",
    "line",
    "row_number",
    "xml_cell",
    "json_path",
    "quote",
    "sentence",
    "paragraph",
)
QUALIFIER_KINDS = ("page", "layer", "layer_line", "commit", "note")
#: How a selector's value was written next to its key.
SELECTOR_FORMS = ("quoted", "bracketed", "equals", "bare")
VERDICTS = (
    "full_cell",
    "full_row_col",
    "full_keyed",
    "text_span",
    "cell_only",
    "partial",
    "anchor_only",
    "residue",
    "no_anchor",
)
COVERAGES = ("full", "partial", "residue", "unanchored")
#: The classes the coverage report counts, in the order it lists them.
LOCATOR_CLASSES = (
    "table_row_col",
    "roman_table",
    "dotted_table",
    "table_unquoted_row",
    "table_row_unmatched",
    "table_no_row_keyword",
    "sheet_cell",
    "figure",
    "json_path",
    "line_addressed",
    "identifier_column",
    "other",
)


@dataclass(frozen=True)
class Anchor:
    """A named place: a table, a figure, a sheet, a file or a container."""

    kind: str
    #: The printed identifier: "S18", "4b", "IV", a sheet name, a file path, a container label.
    value: str
    #: The text the anchor was read from.
    raw: str
    #: The container the anchor sits in, where the text names one ("Supplementary").
    container: str | None = None

    def row(self) -> JsonObject:
        """The anchor as the TypeScript holds it, its members in the order it sets them."""
        row: JsonObject = {"kind": self.kind, "value": self.value, "raw": self.raw}
        if self.container is not None:
            row["container"] = self.container
        return row


@dataclass(frozen=True)
class Address:
    """A place pointed at directly, with no label to look up."""

    kind: str
    value: str
    raw: str
    #: The second coordinate, where the form has one: an XML cell's column, the end of a range.
    value2: str | None = None

    def row(self) -> JsonObject:
        """The address as the TypeScript holds it, its members in the order it sets them."""
        row: JsonObject = {"kind": self.kind, "value": self.value, "raw": self.raw}
        if self.value2 is not None:
            row["value2"] = self.value2
        return row


@dataclass(frozen=True)
class Selector:
    """A key and the value found by it: `row 'Bacformer'`, `tool=x`."""

    #: The key in lowercase.
    key: str
    #: The value, or `None` where an authored locator gives a key alone.
    value: str | None
    form: str
    raw: str

    def row(self) -> JsonObject:
        """The selector as the TypeScript holds it."""
        return {"key": self.key, "value": self.value, "form": self.form, "raw": self.raw}


@dataclass(frozen=True)
class Qualifier:
    """Which rendering of the source, which page, which commit, or a note in brackets."""

    kind: str
    text: str

    def row(self) -> JsonObject:
        """The qualifier as the TypeScript holds it."""
        return {"kind": self.kind, "text": self.text}


@dataclass(frozen=True)
class Parsed:
    """What reading a stored locator gives: `ParsedLocator`."""

    printed: str
    anchors: tuple[Anchor, ...]
    addresses: tuple[Address, ...]
    selectors: tuple[Selector, ...]
    qualifiers: tuple[Qualifier, ...]
    #: Text no token took, one entry for each segment that kept some.
    residue: tuple[str, ...]
    verdict: str

    def row(self) -> JsonObject:
        """The parse as the TypeScript holds it, which `JSON.stringify` writes in this order."""
        return {
            "printed": self.printed,
            "anchors": [anchor.row() for anchor in self.anchors],
            "addresses": [address.row() for address in self.addresses],
            "selectors": [selector.row() for selector in self.selectors],
            "qualifiers": [qualifier.row() for qualifier in self.qualifiers],
            "residue": list(self.residue),
            "verdict": self.verdict,
        }

    def structure(self) -> tuple[object, ...]:
        """The typed fields a round trip has to keep: everything but the text each was read from."""
        return (
            tuple((anchor.kind, anchor.value, anchor.container) for anchor in self.anchors),
            tuple((address.kind, address.value, address.value2) for address in self.addresses),
            tuple((selector.key, selector.value, selector.form) for selector in self.selectors),
            tuple((qualifier.kind, qualifier.text) for qualifier in self.qualifiers),
        )


# ----------------------------------------------------------------------------------------
# The grammar's tokens.
#
# Order matters twice: a token earlier in the list wins a tie on where a match starts, and
# the tokens that would swallow a later one (a quoted sentence, a note in brackets, a page)
# stand first.

_ROMAN = "(?:XVIII|XVII|XVI|XIV|XIII|XIX|XII|XI|XV|XX|IX|VIII|VII|VI|IV|III|II|I|X|V)"
#: A table or figure number: "3", "S18", "A2", "4b", "2.1", the dotted "A.2", or a Roman numeral.
_TNUM = rf"(?:[A-Z]{{0,2}}\d+(?:\.\d+)*[a-z]?|[A-Z]{{1,2}}\.\d+(?:\.\d+)*[a-z]?|{_ROMAN})"
#: Where a table, figure or data object sits in a publication.
_CONTAINER = (
    r"(?:Appendix(?:\s+[A-Z])?"
    r"|Supplementary(?:\s+(?:Materials?|Information|Data|Dataset|Note|Notes|file|Tables?|Methods|Text))?"
    r"|Supplemental(?:\s+(?:Materials?|Information|Data|Tables?))?|Supporting Information|Extended Data"
    r"|Additional file \d+|Data file [A-Z]?\d*|Source Data|Zenodo|Online Methods|Methods|Results|Discussion)"
)
_EXTENSIONS = r"(?!\.(?:json|jsonl|csv|tsv|txt|xml|html|ya?ml)\b)"
_PATH_STEPS = r"(?:\.[\p{L}\p{N}_%+() -]+|\[\d+\])+"
_SELECTOR_KEY = r"(?<key>[\p{L}\p{N}_ .#%/+-]{1,40}?)"
_BARE_KEYS = (
    "row|rows|column|col|columns|sub-column|sub-col|key|metric|model|models|tool|tools|method|methods"
    "|dataset|datasets|caller|split|task|target|subset|group|entry|field|variant|measure|statistic"
    "|system|submission|strategy|platform|depth|tier"
)

#: Every token: its name, its flags and its pattern, as `TOKENS` holds them.
TOKEN_SOURCES: tuple[tuple[str, str, str], ...] = (
    # Prose addresses, which hold quoted text a selector would otherwise claim.
    (
        "addr_sentence",
        "i",
        r"""\b(?:sentence|sentences)\s+(?:beginning|starting|reading)\s*['‘’"](?<sent>[^'’"]{4,})['’"]""",
    ),
    ("addr_quote", "u", r"""(?<![\p{L}\p{N}_])['‘’"](?<quote>[^'’"]{40,})['’"]"""),
    ("addr_para", "i", r"\b(?:paragraph|para\.?|bullet|sentence)\s*(?<para>\d{1,3})\b"),
    ("addr_jsonp", "u", rf"\b(?<jp2>(?:\$|entries|data|results|root){_EXTENSIONS}{_PATH_STEPS})"),
    # Qualifiers: which rendering of the source, which page, which commit, and loose notes.
    # The trailing run reads "text-layer line 12" and "PDF page 4" as one qualifier, and
    # stops before a JSON path, so that `JSON entries[63].score_avg` keeps its path.
    (
        "qual_layer",
        "iu",
        r"\(?\b(?:text-layer|PDF page text|PDF caption|PDF text|PDF|XML|HTML|JATS|JSON|CSV|TSV"
        r"|source CSV|source data|SI)\b"
        r"(?:(?!\s*(?:\$|entries|data|results|root|leaderboard)[[.])[\p{L}\p{N}_.: /–-]){0,24}\)?",
    ),
    ("qual_layerline", "i", r"\(?\btext-layer line\s*\d+\)?"),
    ("qual_page", "i", r"\(?\b(?:page|pages|p\.|pp\.)\s*\d+(?:\s*of\s*\d+)?\)?"),
    ("qual_commit", "i", r"\bat commit [0-9a-f]{7,40}\b"),
    # A note in brackets, except where the brackets hold a sheet name.
    ("qual_paren", "i", r"\((?!\s*sheets?\b)[^()]{0,44}\)"),
    # Addresses.
    (
        "addr_xml",
        "i",
        r"\bXML\s+(?:data\s+)?(?:tr\s+)?row\s*(?<xrow>\d+)[ ,]*(?:column|col)\s*(?<xcol>\d+)",
    ),
    ("addr_range", "", r"(?:!|\bcells?\s+)(?<c1>[A-Z]{1,3}\d{1,6}):(?<c2>[A-Z]{1,3}\d{1,6})"),
    (
        "addr_sheetcell",
        "u",
        r"(?:!|\bcell\s+|\bcells\s+|\b)(?<cell>[A-Z]{1,3}\d{1,6})\b(?![\p{L}\p{N}_.(])",
    ),
    ("addr_line", "i", r"\b(?:line|lines)\s*(?<line>\d{1,7})\b"),
    ("addr_rowno", "i", r"\b(?:data\s+)?rows?\s*(?<rowno>\d{1,7})\b(?!\s*[:=])"),
    # A head followed by a file extension is a file name, not a path into a document.
    (
        "addr_json",
        "u",
        rf"\b(?<jp>(?:\$|data|entries|results|root|leaderboard){_EXTENSIONS}{_PATH_STEPS})",
    ),
    # Anchors. A quoted sheet name ends at its closing quote and nowhere else, and stands
    # first so that the closing quote wins over the guess the unquoted form has to make.
    (
        "anchor_sheet",
        "i",
        r"""\bsheets?\s*(?:['‘’"](?<qsheet>[^'’"]{1,60})['’"]"""
        r"""|(?<sheet>[^'’",;()!]{1,60}?)(?=\s*[,;)!]|\s*$|\s+cell|\s+[A-Z]{1,3}\d))""",
    ),
    ("anchor_bangsheet", "u", r"""['‘’"]?(?<bsheet>[\p{L}\p{N}_ .%+-]{1,50})['’"]?(?=!)"""),
    ("anchor_table", "i", rf"\b(?:(?<tcontainer>{_CONTAINER})\s+)?Tables?\s*(?<table>{_TNUM})\b\.?"),
    (
        "anchor_figure",
        "i",
        rf"\b(?:(?<fcontainer>{_CONTAINER})\s+)?(?:Figures?|Fig\.?)\s*(?<figure>{_TNUM})\b",
    ),
    (
        "anchor_file",
        "iu",
        r"(?<file>[\p{L}\p{N}_./+()%-]+"
        r"\.(?:csv|tsv|json|jsonl|xlsx|xls|txt|parquet|md|ya?ml|html|xml|pdf|zip)(?:\.gz)?)",
    ),
    ("anchor_container", "i", rf"\b(?<ccontainer>{_CONTAINER})(?:\s+(?<obj>[A-Z]?\d+[a-z]?))?"),
    # Selectors.
    (
        "sel_group",
        "i",
        r"(?<key>(?:metric |column |row |value )?"
        r"(?:block|group|panel|sub-?table|part|section|strategy|subgroup))"
        r"""\s*[:=]?\s*['‘’"](?<qv>[^'’"]*)['’"]""",
    ),
    ("sel_grouprev", "i", r"""['‘’"](?<qv>[^'’"]*)['’"]\s*(?<key>block|group|panel|sub-?table|section)\b"""),
    ("sel_panel", "", r"\b(?<key>Panel|panel)\s+(?<bv>[A-Za-z]\b)"),
    ("sel_quoted", "u", _SELECTOR_KEY + r"""\s*[:=]?\s*['‘’"](?<qv>[^'’"]*)['’"]"""),
    ("sel_bracket", "u", _SELECTOR_KEY + r"\s*\(\s*(?<pv>[^()]{1,80})\s*\)"),
    ("sel_eq", "u", _SELECTOR_KEY + r"\s*=\s*(?<ev>[^,;]{1,80})"),
    ("sel_bare", "i", rf"""\b(?<key>{_BARE_KEYS})\s+(?<bv>[^,;'"()]{{1,80}}?)(?=\s*[,;)]|\s*$)"""),
)
TOKENS: tuple[tuple[str, JsPattern], ...] = tuple(
    (name, JsPattern(source, flags)) for name, flags, source in TOKEN_SOURCES
)
#: The most tokens taken from one segment. What is left after that is read no further.
MOST_TOKENS = 40

_ANCHOR_KIND = {
    "anchor_sheet": "sheet",
    "anchor_bangsheet": "sheet",
    "anchor_table": "table",
    "anchor_figure": "figure",
    "anchor_file": "file",
    "anchor_container": "container",
}
_ADDRESS_KIND = {
    "addr_sentence": "sentence",
    "addr_quote": "quote",
    "addr_para": "paragraph",
    "addr_jsonp": "json_path",
    "addr_json": "json_path",
    "addr_xml": "xml_cell",
    "addr_range": "cell_range",
    "addr_sheetcell": "sheet_cell",
    "addr_line": "line",
    "addr_rowno": "row_number",
}
_QUALIFIER_KIND = {
    "qual_layer": "layer",
    "qual_layerline": "layer_line",
    "qual_page": "page",
    "qual_commit": "commit",
    "qual_paren": "note",
}
_SELECTOR_FORM = {
    "sel_group": "quoted",
    "sel_grouprev": "quoted",
    "sel_panel": "bare",
    "sel_quoted": "quoted",
    "sel_bracket": "bracketed",
    "sel_eq": "equals",
    "sel_bare": "bare",
}
#: The named groups an anchor's value is read from, the first that took part.
_ANCHOR_VALUES = ("qsheet", "sheet", "bsheet", "table", "figure", "file", "obj")
_ADDRESS_VALUES = ("sent", "quote", "para", "jp2", "jp", "xrow", "c1", "cell", "line", "rowno")
_SELECTOR_VALUES = ("qv", "pv", "ev", "bv")

#: Keys that name the row a value sits in.
ROW_KEYS = frozenset({"row", "rows", "row label"})
#: Keys that name the column a value sits in.
COLUMN_KEYS = frozenset({"column", "col", "columns", "sub-column", "sub-col", "key", "field", "column group"})
#: A printed key may carry its own ordinal: "column 1 of 4" names the first of four columns.
ORDINAL_KEY = JsPattern(r"\s+\d+(?:\s+of\s+\d+)?$")
#: Addresses that place a value without needing a label.
_DIRECT = frozenset({"sheet_cell", "xml_cell", "json_path"})
#: Addresses that place a row but not a column.
_LINE_ADDRESSED = frozenset({"line", "row_number"})
#: Addresses that point at prose and not at a cell of a table.
_PROSE = frozenset({"sentence", "quote", "paragraph"})

#: Punctuation only: what is left of a segment when everything in it was read.
NOISE = JsPattern(r"""^[\s,;:.()[\]'"‘’/|-]*$""")
#: Words that carry no address on their own, so a segment left holding only these is not residue.
NOISE_WORDS = JsPattern(
    r"\b(?:and|the|of|in|at|for|with|value|values|cell|cells|row|rows|column|columns|col|sheet|table"
    r"|tables|workbook|xlsx|xls|csv|tsv|data|file|page|from|as|printed|reported|see|also|only|per|no"
    r"|not|is|are|to|by|version|v\d+|header|headers|label|labels|first|second|third|final|last|top"
    r"|bottom|left|right|mean|overall|all|main|text|supplement|supplementary|appendix|figure|fig|note"
    r"|notes|caption|legend|body|entries|entry|block|blocks|section|sections|sub|subtable|part|parts"
    r"|panel|panels|image|images|commit|tier|unnamed|single|standard|error|rounded|assessable|count"
    r"|counts|frozen|bar|read|group|groups|subgroup|strategy|under|above|below|next|prior|same|its"
    r"|their|which|where|when|that|this|these|those|one|two|three|four|five)\b",
    "gi",
)


def selector_role(key: str) -> str:
    """What a selector key names: `row`, `column`, or `other`, something else the row is found by."""
    head = paired(trim(ORDINAL_KEY.replace(unit_text(key), "")))
    if head in ROW_KEYS:
        return "row"
    if head in COLUMN_KEYS:
        return "column"
    return "other"


def carries_no_address(word: str) -> bool:
    """Whether a word carries no address on its own.

    The resolvers ask, because a word the grammar left as residue is a fact about the
    value's place only where it is not one of these.
    """
    seen = unit_text(word)
    found = NOISE_WORDS.exec(seen)
    return found is not None and len(found.text) == len(seen)


def split_segments(text: str) -> list[str]:
    """Split a locator into segments on `;` and on commas outside quotes and brackets.

    A quote opened by a curly mark is closed only by a straight one, as in the TypeScript,
    which holds the open quote as `'` whichever of the three opened it.
    """
    segments: list[str] = []
    held: list[str] = []
    depth = 0
    quote = ""
    for character in paired(unit_text(text)):
        if quote:
            held.append(character)
            if character == quote:
                quote = ""
            continue
        if character in "'‘’\"":
            quote = '"' if character == '"' else "'"
        elif character in "([":
            depth += 1
        elif character in ")]":
            depth = max(0, depth - 1)
        elif character in ";," and depth == 0:
            segments.append("".join(held))
            held = []
            continue
        held.append(character)
    segments.append("".join(held))
    return [trimmed for trimmed in map(trim, segments) if trimmed]


def _consume(segment: str) -> tuple[list[tuple[str, Found]], str]:
    """Take every token the grammar knows from one segment, leftmost first.

    The segment is in units, and so is what is returned: the tokens in the order they
    were taken, and what is left where that is more than punctuation and empty words.
    """
    hits: list[tuple[str, Found]] = []
    text = segment
    for _ in range(MOST_TOKENS):
        best: tuple[str, Found] | None = None
        for name, pattern in TOKENS:
            found = pattern.exec(text)
            if found is not None and (best is None or found.index < best[1].index):
                best = (name, found)
        if best is None:
            break
        hits.append(best)
        start = best[1].index
        text = trim(f"{text[:start]} {text[start + len(best[1].text) :]}")
    left = NOISE_WORDS.replace(text, " ")
    return hits, "" if NOISE.test(left) else text


def _first(groups: Mapping[str, str | None], names: Sequence[str]) -> str | None:
    """The first of these groups that took part in the match, as `a ?? b ?? c` reads them."""
    for name in names:
        held = groups.get(name)
        if held is not None:
            return held
    return None


def _anchor(name: str, found: Found) -> Anchor:
    groups = found.groups
    kind = _ANCHOR_KIND[name]
    raw = trim(found.text)
    value = _first(groups, _ANCHOR_VALUES)
    numbered = groups.get("obj")
    # A container with an object number keeps the two apart ("Supplementary Data" and "2"),
    # so rendering writes the label back out and not the bare number.
    container = _first(groups, ("tcontainer", "fcontainer")) or (groups.get("ccontainer") if numbered else None)
    if value is None or (kind == "container" and not numbered):
        value = raw
    return Anchor(kind, paired(trim(value)), paired(raw), paired(trim(container)) if container else None)


def _address(name: str, found: Found) -> Address:
    groups = found.groups
    second = _first(groups, ("xcol", "c2"))
    return Address(
        _ADDRESS_KIND[name],
        paired(trim(_first(groups, _ADDRESS_VALUES) or "")),
        paired(trim(found.text)),
        paired(trim(second)) if second else None,
    )


def _selector(name: str, found: Found) -> Selector:
    groups = found.groups
    value = _first(groups, _SELECTOR_VALUES)
    return Selector(
        lower(paired(trim(groups.get("key") or ""))),
        None if value is None else paired(trim(value)),
        _SELECTOR_FORM[name],
        paired(trim(found.text)),
    )


def parse_locator(printed: str) -> Parsed:
    """Read a stored locator into anchors, addresses, selectors, qualifiers and residue."""
    anchors: list[Anchor] = []
    addresses: list[Address] = []
    selectors: list[Selector] = []
    qualifiers: list[Qualifier] = []
    residue: list[str] = []
    for segment in split_segments(printed):
        hits, left = _consume(unit_text(segment))
        for name, found in hits:
            if name in _ANCHOR_KIND:
                anchors.append(_anchor(name, found))
            elif name in _ADDRESS_KIND:
                addresses.append(_address(name, found))
            elif name in _SELECTOR_FORM:
                selectors.append(_selector(name, found))
            else:
                qualifiers.append(Qualifier(_QUALIFIER_KIND[name], paired(trim(found.text))))
        if left:
            residue.append(paired(left))
    verdict = verdict_of(anchors, addresses, selectors, residue)
    return Parsed(
        printed, tuple(anchors), tuple(addresses), tuple(selectors), tuple(qualifiers), tuple(residue), verdict
    )


def verdict_of(
    anchors: Sequence[Anchor],
    addresses: Sequence[Address],
    selectors: Sequence[Selector],
    residue: Sequence[str],
) -> str:
    """How much of a value's position the parse pins down."""
    roles = [selector_role(selector.key) for selector in selectors]
    kinds = {address.kind for address in addresses}
    row, column, other = "row" in roles, "column" in roles, "other" in roles
    direct, lined = bool(_DIRECT & kinds), bool(_LINE_ADDRESSED & kinds)
    if not anchors and not addresses:
        return "no_anchor"
    if residue:
        return "residue"
    if _PROSE & kinds:
        return "text_span"
    # A cell address next to labels checks itself: the labels say what the cell should hold.
    if direct:
        return "full_cell" if column or row or other else "cell_only"
    if (row or lined) and column:
        return "full_row_col"
    if other and column:
        return "full_keyed"
    if lined or row or other or column:
        return "partial"
    return "anchor_only"


_COVERAGE_OF = {
    "full_cell": "full",
    "full_row_col": "full",
    "full_keyed": "full",
    "text_span": "full",
    "cell_only": "partial",
    "partial": "partial",
    "anchor_only": "partial",
    "residue": "residue",
    "no_anchor": "unanchored",
}


def coverage_of(verdict: str) -> str:
    """The reported bucket for a verdict.

    Fully addressed means every part was read into a typed field and the parse names both
    a row and a column, or an address that stands for both.
    """
    return _COVERAGE_OF[verdict]


# ----------------------------------------------------------------------------------------
# Rendering.


def _quoted(value: str) -> str:
    return f'"{value}"' if "'" in value else f"'{value}'"


def _render_anchor(anchor: Anchor) -> str:
    prefix = f"{anchor.container} " if anchor.container else ""
    if anchor.kind == "table":
        return f"{prefix}Table {anchor.value}"
    if anchor.kind == "figure":
        return f"{prefix}Figure {anchor.value}"
    if anchor.kind == "sheet":
        return f"{prefix}sheet {_quoted(anchor.value)}"
    return f"{prefix}{anchor.value}"


def _render_address(address: Address) -> str:
    value, second = address.value, address.value2
    written = {
        "sheet_cell": f"cell {value}",
        "cell_range": f"cells {value}:{value if second is None else second}",
        "line": f"line {value}",
        "row_number": f"row {value}",
        "xml_cell": trim(f"XML row {value} column {'' if second is None else second}"),
        "json_path": value,
        "quote": _quoted(value),
        "sentence": f"sentence beginning {_quoted(value)}",
        "paragraph": f"paragraph {value}",
    }
    return written[address.kind]


def _render_selector(selector: Selector) -> str:
    if selector.value is None:
        return selector.key
    if selector.form == "equals":
        return f"{selector.key}={selector.value}"
    if selector.form == "bracketed":
        return f"{selector.key} ({selector.value})"
    if selector.form == "bare":
        return f"{selector.key} {selector.value}"
    return f"{selector.key} {_quoted(selector.value)}"


def render_locator(
    anchors: Sequence[Anchor],
    selectors: Sequence[Selector],
    qualifiers: Sequence[Qualifier],
    *,
    addresses: Sequence[Address] | None = None,
    address: Address | None = None,
    refines: Sequence[Address] = (),
) -> str:
    """Write a locator back out as text, from its typed fields only.

    The form is `anchors, addresses, selectors (qualifiers)`, with keys in lowercase as
    parsed and values in single quotes. It does not keep the punctuation of the original,
    so rendering a parse gives back the text it was read from only where that text was
    already written this way. That share is what the coverage report measures.

    `addresses` is what a parse gives. An authored locator has one `address`, narrowed by
    any `refines`, and those are used when `addresses` is not given.
    """
    if addresses is None:
        addresses = [held for held in (address, *refines) if held is not None]
    parts = [
        *(_render_anchor(anchor) for anchor in anchors),
        *(_render_address(held) for held in addresses),
        *(_render_selector(selector) for selector in selectors),
    ]
    notes = (qualifier.text.removeprefix("(").removesuffix(")") for qualifier in qualifiers)
    return ", ".join(parts) + "".join(f" ({note})" for note in notes)


def rendered(parsed: Parsed) -> str:
    """`renderLocator` of a parse."""
    return render_locator(parsed.anchors, parsed.selectors, parsed.qualifiers, addresses=parsed.addresses)


# ----------------------------------------------------------------------------------------
# Classes, for the coverage report.

_PREFIX = "(?:Appendix |Supplementary |Extended Data )?"
#: The pattern at `scripts/omics/batch.py:30-31`, version 1 of the grammar, kept for
#: conformance only: `check-values` reports `locator_not_parsed` for whatever it misses.
LEGACY = JsPattern(
    r"""^(?<prefix>Appendix |Supplementary |Extended Data )?(?<table>Table [A-Z]?\d+[a-z]?)\b"""
    r""".*?row ["'](?<row>[^"']+)["'](?:.*?col(?:umn)? ["'](?<col>[^"']+)["'])?"""
    r"""(?:.*?sub-col(?:umn)? ["'](?<sub>[^"']+)["'])?"""
)
_TABLE_HEAD = JsPattern(f"^{_PREFIX}Table ")
_ROMAN_TABLE = JsPattern(rf"^{_PREFIX}Table [IVXLC]+\b")
_DOTTED_TABLE = JsPattern(rf"^{_PREFIX}Table (?:[A-Z]|[A-Z]?\d+)\.\d")
_ROW = JsPattern(r"\brow\b")
_QUOTED_ROW = JsPattern(r"""\brow ["']""")
_NAMED_TABLE = JsPattern(r"\bTable\b|'Suppl")
_FIGURE = JsPattern(rf"^{_PREFIX}(?:Figure|Fig\.)", "i")
_JSON_PATH = JsPattern(rf"(?:^|[\s,;])(?:\$|data|entries|results|root|leaderboard){_PATH_STEPS}", "u")
_NUMBERED_LINE = JsPattern(r"\b(?:line|lines|row|rows)\s*\d", "i")
_COLUMN = JsPattern(r"\bcol(?:umn)?\b", "i")

#: Every pattern this module runs, which is what has to be recorded from Node.
PATTERNS: tuple[JsPattern, ...] = (
    *(pattern for _, pattern in TOKENS),
    ORDINAL_KEY,
    NOISE,
    NOISE_WORDS,
    LEGACY,
    _TABLE_HEAD,
    _ROMAN_TABLE,
    _DOTTED_TABLE,
    _ROW,
    _QUOTED_ROW,
    _NAMED_TABLE,
    _FIGURE,
    _JSON_PATH,
    _NUMBERED_LINE,
    _COLUMN,
)


def parses_under_legacy(printed: str) -> bool:
    """Whether version 1 of the grammar parses this locator."""
    return LEGACY.test(unit_text(printed))


def _table_class(text: str) -> str:
    if _ROMAN_TABLE.test(text):
        return "roman_table"
    if _DOTTED_TABLE.test(text):
        return "dotted_table"
    if not _ROW.test(text):
        return "table_no_row_keyword"
    return "table_row_unmatched" if _QUOTED_ROW.test(text) else "table_unquoted_row"


def classify_locator(printed: str) -> str:
    """Which measured class a stored locator belongs to, read off the printed text alone.

    The first eight classes are the ones measured over the whole store on 2026-11-17 and
    keep that measurement's order of decision, so that a report can be compared with it.
    The last four split what it left in one bucket by what the text addresses.
    """
    text = unit_text(trim(printed))
    if LEGACY.test(text):
        return "table_row_col"
    if _TABLE_HEAD.test(text):
        return _table_class(text)
    for pattern, named in (
        (_NAMED_TABLE, "sheet_cell"),
        (_FIGURE, "figure"),
        (_JSON_PATH, "json_path"),
        (_NUMBERED_LINE, "line_addressed"),
        (_COLUMN, "identifier_column"),
    ):
        if pattern.test(text):
            return named
    return "other"
