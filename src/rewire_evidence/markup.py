"""A deterministic tag scanner, shared by the XML and HTML normalisers and the table grid.

This is `shared/archive/layers/markup.ts`, with the rules `xml.ts` and `html.ts` scan by.
Neither normaliser needs a tree: both want the text content in document order with the
element boundaries a reader would see as a line or a section break. A scanner gives that,
handles a document with no prolog, and cannot reorder anything.

Entity decoding is a fixed table plus numeric references. A table that grows changes the
output, so it is part of the normaliser version, and the table here is the TypeScript's.

The TypeScript counts in UTF-16 units and this counts in characters. Every offset here is
internal: a reader of the scan is told where text landed in the scan's own output, and
`tidy_traced` maps that onto the layer. The characters are the same either way.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from rewire_evidence.js import SPACE, SPACE_CLASS, parse_int

#: Boundary strength an element contributes. 0 is inline. 1 separates with a space, which is
#: what a table cell needs so a row stays one line. 2 is a line break. 3 is a blank line.
INLINE: Final = 0
CELL: Final = 1
LINE: Final = 2
SECTION: Final = 3

_LAST_CHARACTER: Final = 0x10FFFF
_FIRST_SURROGATE: Final = 0xD800
_LAST_SURROGATE: Final = 0xDFFF


@dataclass(frozen=True)
class ScanRules:
    """What a normaliser scans by."""

    #: Elements whose content is discarded along with their tags.
    skip: frozenset[str]
    #: Boundary strength by lowercased element name.
    boundary: Callable[[str], int]


class ScanEvents:
    """What a reader of the scan is told, as the scan happens. Each does nothing unless overridden.

    The offsets are into the untidied text the scan builds, not into the layer. `text`
    reports a run of content that was appended, without the space or newline a boundary put
    before it, so a cell's range starts at its own first character.
    """

    def open(self, name: str, attributes: str, at: int) -> None:
        """An element opens."""

    def close(self, name: str, at: int) -> None:
        """An element closes. One written as self-closing closes where it opened."""

    def text(self, start: int, end: int) -> None:
        """A run of content was appended."""


_NAMED: Final[Mapping[str, str]] = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
    "ensp": " ",
    "emsp": " ",
    "thinsp": " ",
    "shy": "",
    "mdash": "\u2014",
    "ndash": "\u2013",
    "minus": "\u2212",
    "hellip": "\u2026",
    "lsquo": "\u2018",
    "rsquo": "\u2019",
    "ldquo": "\u201c",
    "rdquo": "\u201d",
    "sbquo": "\u201a",
    "bdquo": "\u201e",
    "dagger": "\u2020",
    "Dagger": "\u2021",
    "bull": "\u2022",
    "middot": "\u00b7",
    "deg": "\u00b0",
    "plusmn": "\u00b1",
    "times": "\u00d7",
    "divide": "\u00f7",
    "frac12": "\u00bd",
    "micro": "\u00b5",
    "copy": "\u00a9",
    "reg": "\u00ae",
    "trade": "\u2122",
    "sect": "\u00a7",
    "para": "\u00b6",
    "prime": "\u2032",
    "Prime": "\u2033",
    "le": "\u2264",
    "ge": "\u2265",
    "ne": "\u2260",
    "asymp": "\u2248",
    "equiv": "\u2261",
    "sim": "\u223c",
    "prop": "\u221d",
    "infin": "\u221e",
    "sum": "\u2211",
    "prod": "\u220f",
    "radic": "\u221a",
    "int": "\u222b",
    "part": "\u2202",
    "nabla": "\u2207",
    "isin": "\u2208",
    "notin": "\u2209",
    "cap": "\u2229",
    "cup": "\u222a",
    "alpha": "\u03b1",
    "beta": "\u03b2",
    "gamma": "\u03b3",
    "delta": "\u03b4",
    "epsilon": "\u03b5",
    "zeta": "\u03b6",
    "eta": "\u03b7",
    "theta": "\u03b8",
    "kappa": "\u03ba",
    "lambda": "\u03bb",
    "mu": "\u03bc",
    "nu": "\u03bd",
    "xi": "\u03be",
    "pi": "\u03c0",
    "rho": "\u03c1",
    "sigma": "\u03c3",
    "tau": "\u03c4",
    "phi": "\u03c6",
    "chi": "\u03c7",
    "psi": "\u03c8",
    "omega": "\u03c9",
    "Alpha": "\u0391",
    "Beta": "\u0392",
    "Gamma": "\u0393",
    "Delta": "\u0394",
    "Theta": "\u0398",
    "Lambda": "\u039b",
    "Pi": "\u03a0",
    "Sigma": "\u03a3",
    "Phi": "\u03a6",
    "Psi": "\u03a8",
    "Omega": "\u03a9",
    "larr": "\u2190",
    "rarr": "\u2192",
    "harr": "\u2194",
    "uarr": "\u2191",
    "darr": "\u2193",
    "aacute": "\u00e1",
    "eacute": "\u00e9",
    "iacute": "\u00ed",
    "oacute": "\u00f3",
    "uacute": "\u00fa",
    "agrave": "\u00e0",
    "egrave": "\u00e8",
    "auml": "\u00e4",
    "ouml": "\u00f6",
    "uuml": "\u00fc",
    "szlig": "\u00df",
    "ccedil": "\u00e7",
    "ntilde": "\u00f1",
    "oslash": "\u00f8",
    "aring": "\u00e5",
    "ae": "\u00e6",
}

#: `&name;`, `&#10;` or `&#x0a;`.
REFERENCE: Final = re.compile(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]{1,31});")
_NAME: Final = re.compile(rf"/?[{SPACE_CLASS}]*([A-Za-z_][A-Za-z0-9_.:-]*)")
_BLANK: Final = SPACE.replace("\n", "")
_WORDS: Final = re.compile(rf"[^{SPACE_CLASS}]+")


def decode_references(text: str, named: Mapping[str, str]) -> str:
    """Decode the numeric references and the named ones in `named`.

    An unknown reference is left exactly as written, and so is a number that is no character.
    """
    if "&" not in text:
        return text

    def decoded(found: re.Match[str]) -> str:
        body = found.group(1)
        if body[0] != "#":
            return named.get(body, found.group())
        code = parse_int(body[2:], 16) if body[1] in "xX" else parse_int(body[1:])
        if not 0 < code <= _LAST_CHARACTER or _FIRST_SURROGATE <= code <= _LAST_SURROGATE:
            return found.group()
        return chr(int(code))

    return REFERENCE.sub(decoded, text)


def decode_entities(text: str) -> str:
    """Decode the entity references the markup table knows."""
    return decode_references(text, _NAMED)


def tag_end(text: str, start: int) -> int:
    """The end of a tag, respecting quoted attribute values, or -1."""
    quote = ""
    for index in range(start, len(text)):
        character = text[index]
        if quote:
            if character == quote:
                quote = ""
        elif character in "\"'":
            quote = character
        elif character == ">":
            return index
    return -1


def declaration_end(text: str, start: int) -> int:
    """The `>` that closes a `<!` declaration, past any bracketed subset, or -1."""
    depth = 0
    for at in range(start, len(text)):
        character = text[at]
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
        elif character == ">" and depth <= 0:
            return at
    return -1


def _after(found: int, width: int, text: str) -> int:
    return len(text) if found < 0 else found + width


class _Scan:
    """One scan: the parts appended so far and the boundary waiting to be written."""

    def __init__(self, events: ScanEvents | None) -> None:
        self.parts: list[str] = []
        self.pending = INLINE
        self.length = 0
        self.events = events

    def emit(self, value: str) -> None:
        if not value:
            return
        if self.pending:
            mark = "\n\n" if self.pending == SECTION else "\n" if self.pending == LINE else " "
            self.parts.append(mark)
            self.length += len(mark)
            self.pending = INLINE
        start = self.length
        self.parts.append(value)
        self.length += len(value)
        if self.events:
            self.events.text(start, self.length)

    def boundary(self, strength: int) -> None:
        self.pending = max(self.pending, strength)


def scan_raw(text: str, rules: ScanRules, events: ScanEvents | None = None) -> str:  # noqa: C901, PLR0912, PLR0915 - the scanner is one loop in the TypeScript
    """The scan before `tidy` runs: the same characters, with the source's whitespace still in them."""
    scan = _Scan(events)
    skipping: str | None = None
    skip_depth = 0
    index = 0
    while index < len(text):
        opened = text.find("<", index)
        if opened < 0:
            if not skipping:
                scan.emit(decode_entities(text[index:]))
            break
        if opened > index and not skipping:
            scan.emit(decode_entities(text[index:opened]))

        if text.startswith("<!--", opened):
            index = _after(text.find("-->", opened + 4), 3, text)
            continue
        if text.startswith("<![CDATA[", opened):
            end = text.find("]]>", opened + 9)
            if not skipping:
                scan.emit(text[opened + 9 : len(text) if end < 0 else end])
            index = _after(end, 3, text)
            continue
        if text.startswith("<!", opened):
            index = _after(declaration_end(text, opened + 2), 1, text)
            continue
        if text.startswith("<?", opened):
            index = _after(text.find("?>", opened + 2), 2, text)
            continue

        end = tag_end(text, opened + 1)
        if end < 0:
            # An unterminated `<` is text, not a tag.
            if not skipping:
                scan.emit(decode_entities(text[opened:]))
            break
        body = text[opened + 1 : end]
        closing = body.startswith("/")
        self_closing = body.endswith("/")
        named = _NAME.match(body)
        index = end + 1
        if not named:
            continue
        name = named.group(1).lower()

        if skipping:
            if name != skipping:
                continue
            if closing:
                skip_depth -= 1
                if skip_depth <= 0:
                    skipping = None
                    scan.boundary(LINE)
            elif not self_closing:
                skip_depth += 1
            continue
        if not closing and name in rules.skip:
            if not self_closing:
                skipping = name
                skip_depth = 1
            scan.boundary(LINE)
            continue
        if events:
            if closing:
                events.close(name, scan.length)
            else:
                events.open(name, body[named.end() :].removesuffix("/"), scan.length)
                if self_closing:
                    events.close(name, scan.length)
        scan.boundary(rules.boundary(name))

    return "".join(scan.parts)


@dataclass(frozen=True)
class TidyTrace:
    """`tidy`, with where every character went."""

    text: str
    #: For each character of the input, its index in `text`, or -1 when `tidy` dropped it. A
    #: whitespace run that collapsed to one space maps its first character to that space.
    out: list[int]


def tidy_traced(raw: str) -> TidyTrace:
    """The tidied text and the map from the untidied text onto it.

    Whitespace runs inside a line collapse to a space, both ends of a line are trimmed, blank
    lines beyond one are dropped and the text ends with a single newline. A layer with no
    characters is no text at all, never a lone newline.
    """
    out = [-1] * len(raw)
    pieces: list[str] = []
    length = 0
    # A blank line waiting to be written, which a trailing one never is.
    held = 0
    at = 0
    for line in raw.split("\n"):
        words: list[str] = []
        # Where this line starts in the tidied text, if it turns out to hold anything.
        base = length + 1 + held if pieces else 0
        width = 0
        before = -1
        for word in _WORDS.finditer(line):
            if words:
                out[at + before] = base + width
                width += 1
            start = base + width
            out[at + word.start() : at + word.end()] = range(start, start + len(word.group()))
            words.append(word.group())
            width += len(word.group())
            before = word.end()
        if not words:
            if pieces:
                held = 1
        else:
            if pieces:
                pieces.append("\n" * (1 + held))
                length += 1 + held
            held = 0
            pieces.append(" ".join(words))
            length += width
        at += len(line) + 1
    if pieces:
        pieces.append("\n")
    return TidyTrace("".join(pieces), out)


def tidy(text: str) -> str:
    """Collapse whitespace runs inside a line, drop blank lines beyond one, end with one newline."""
    return tidy_traced(text).text


def scan_markup(text: str, rules: ScanRules, events: ScanEvents | None = None) -> str:
    """The text content of a markup document, in order, with boundaries kept."""
    return tidy(scan_raw(text, rules, events))


# ---- europepmc-fulltext-xml@1 ---------------------------------------------------------

#: Neither holds article text: one is the Europe PMC tagging record, the other image data.
_XML_SKIP: Final = frozenset({"processing-meta", "inline-graphic"})
#: A blank line between these: the structures a reader sees as sections.
_XML_SECTIONS: Final = frozenset(
    {
        "abstract", "ack", "app", "app-group", "article-meta", "back", "body", "boxed-text", "fig",
        "fn-group", "front", "glossary", "notes", "ref-list", "sec", "supplementary-material",
        "table-wrap", "trans-abstract",
    }
)  # fmt: skip
#: Run together with the text around them. Everything else gets a line.
_XML_INLINE: Final = frozenset(
    {
        "abbrev", "bold", "chem-struct", "email", "ext-link", "inline-formula",
        "inline-supplementary-material", "italic", "milestone-end", "milestone-start", "monospace",
        "named-content", "overline", "private-char", "roman", "sans-serif", "sc", "strike",
        "styled-content", "sub", "sup", "target", "tex-math", "underline", "uri", "x", "xref",
    }
)  # fmt: skip
#: A space, not a line, so that a table row reads as one line.
_XML_CELLS: Final = frozenset({"td", "th", "entry"})


def _xml_boundary(name: str) -> int:
    if name in _XML_SECTIONS:
        return SECTION
    if name in _XML_INLINE or name.startswith(("mml:", "m:")):
        return INLINE
    return CELL if name in _XML_CELLS else LINE


XML_RULES: Final = ScanRules(_XML_SKIP, _xml_boundary)
XML_NORMALISER: Final = "europepmc-fulltext-xml@1"
XML_DERIVATION: Final = "europepmc-fulltext-xml@1 text content in document order"

# ---- html-text@1 ----------------------------------------------------------------------

#: Removed with their content. `form` is deliberately not here: a page may wrap every one of
#: its tables in one.
_HTML_SKIP: Final = frozenset(
    {
        "script", "style", "noscript", "template", "svg", "canvas", "iframe", "nav", "header",
        "footer", "aside", "dialog",
    }
)  # fmt: skip
_HTML_SECTIONS: Final = frozenset(
    {
        "article", "blockquote", "details", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "main",
        "ol", "p", "pre", "section", "table", "ul",
    }
)  # fmt: skip
_HTML_LINES: Final = frozenset(
    {"br", "caption", "dd", "div", "dt", "figcaption", "li", "option", "summary", "tr"}
)
_HTML_CELLS: Final = frozenset({"td", "th"})


def _html_boundary(name: str) -> int:
    if name in _HTML_SECTIONS:
        return SECTION
    if name in _HTML_LINES:
        return LINE
    return CELL if name in _HTML_CELLS else INLINE


HTML_RULES: Final = ScanRules(_HTML_SKIP, _html_boundary)
HTML_NORMALISER: Final = "html-text@1"
#: The derivation the layer records, which names the skipped set.
HTML_DERIVATION: Final = f"html-text@1 strip {','.join(sorted(_HTML_SKIP))} then tags"
