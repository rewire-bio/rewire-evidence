r"""Turtle as n3 reads it: the lexer and parser of n3 1.17.4 in Turtle mode, ported.

`scripts/omics/vocab.ts` reads each vocabulary with `new Parser({ format: "Turtle" })`, and
what a vocabulary holds is whatever that parser makes of the file. n3 is not a strict
reading of the Turtle grammar. It refuses some documents the grammar allows, accepts some
it does not, keeps a number as it is written, lowercases a language tag, leaves a relative
IRI relative when there is no base, and words its own refusals. Another Turtle library
agrees with it on ordinary files and on none of those, so this module follows n3's source,
`src/N3Lexer.js` and `src/N3Parser.js`, function by function. The N3, TriG, quad and
RDF-star paths that Turtle mode switches off are left out, and where one of them is
reachable only to be refused, the refusal is kept.

Three things about JavaScript are part of the behaviour and are reproduced:

- A JavaScript string is UTF-16 units and n3's patterns are written over units, so the text
  is read here as units too, an astral character as its two surrogates, and joined again in
  what is handed back.
- `\s`, `\d`, `$`, `.` and case-insensitive matching each mean something else in Python,
  so every pattern spells out the set JavaScript means.
- n3 goes on reading after one kind of refusal, a bracket that closes nothing, and reads a
  member of a missing value in a few places, which JavaScript reports as a `TypeError`.
  Both are kept, in V8's words.

`tests/fixtures/turtle.json` holds what n3 itself gives for each construct and for a few
hundred damaged documents, recorded in CI by `scripts/fixtures/record_schema_fixture.mjs`.

n3 is copyright Ruben Verborgh and is under the MIT licence. Its notice is in
`THIRD-PARTY-NOTICES.md` at the root of this repository and ships in the wheel.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

XSD = "http://www.w3.org/2001/XMLSchema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

# --- JavaScript's character sets, spelt out ----------------------------------------------

# What `\s` matches in a JavaScript pattern. Python's has U+001C to U+001F and U+0085 as
# well, and not U+FEFF.
_SPACE = r"\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
# What `.` does not match in a JavaScript pattern.
_LINE_END = r"\n\r\u2028\u2029"
# The letters a name may start with and go on with, above ASCII, as n3 lists them.
_START = (
    r"\xc0-\xd6\xd8-\xf6\xf8-\u02ff\u0370-\u037d\u037f-\u1fff\u200c\u200d"
    r"\u2070-\u218f\u2c00-\u2fef\u3001-\ud7ff\uf900-\ufdcf\ufdf0-\ufffd"
)
_INSIDE = (
    r"\xb7\xc0-\xd6\xd8-\xf6\xf8-\u037d\u037f-\u1fff\u200c\u200d\u203f\u2040"
    r"\u2070-\u218f\u2c00-\u2fef\u3001-\ud7ff\uf900-\ufdcf\ufdf0-\ufffd"
)
# An astral character, as the two units JavaScript holds it in.
_ASTRAL = r"[\ud800-\udb7f][\udc00-\udfff]"
_NAME = rf"(?:[A-Za-z{_START}]|{_ASTRAL})(?:\.?[\-0-9A-Z_a-z{_INSIDE}]|{_ASTRAL})*"
_LOCAL_ESCAPE = r"%[0-9a-fA-F]{2}|\\[!#-\/;=?\-@_~]"
_LOCAL = (
    rf"(?:(?:[0-:A-Z_a-z{_START}]|{_ASTRAL}|{_LOCAL_ESCAPE})"
    rf"(?:(?:[\.\-0-:A-Z_a-z{_INSIDE}]|{_ASTRAL}|{_LOCAL_ESCAPE})*"
    rf"(?:[\-0-:A-Z_a-z{_INSIDE}]|{_ASTRAL}|{_LOCAL_ESCAPE}))?)?"
)

_IRI = re.compile(r"<((?:[^ <>{}\\]|\\[uU])+)>[ \t]*")
_UNESCAPED_IRI = re.compile(r'<([^\x00-\x20<>\\"\{\}\|\^\`]*)>[ \t]*')
_ILLEGAL_IRI = re.compile(r'[\x00-\x20<>\\"\{\}\|\^\`]')
_SIMPLE_STRING = {
    '"': re.compile(r'"([^"\\\r\n]*)"(?=[^"])'),
    "'": re.compile(r"'([^'\\\r\n]*)'(?=[^'])"),
}
_OPENING = re.compile(r"\"\"\"|\"|'''|'")
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_LANGCODE = re.compile(r"@([A-Za-z]+(?:-[A-Za-z0-9]+)*)(?=[^A-Za-z0-9\-])")
_PREFIX = re.compile(rf"({_NAME})?:(?=[#{_SPACE}<])")
_PREFIXED = re.compile(rf"({_NAME})?:({_LOCAL})(?:[ \t]+|(?=\.?[,;!\^{_SPACE}#()\[\]\{{\}}\"'<>]))")
_BLANK = re.compile(
    rf"_:((?:[0-9A-Z_a-z{_START}]|{_ASTRAL})(?:\.?[\-0-9A-Z_a-z{_INSIDE}]|{_ASTRAL})*)"
    rf"(?:[ \t]+|(?=\.?[,;:{_SPACE}#()\[\]\{{\}}\"'<>]))"
)
_NUMBER = re.compile(
    r"[\-+]?(?:([0-9]+\.[0-9]*|\.?[0-9]+)[eE][\-+]?|[0-9]*(\.)?)[0-9]+"
    rf"(?=\.?[,;:{_SPACE}#()\[\]\{{\}}\"'<>])"
)
_BOOLEAN = re.compile(rf"(?:true|false)(?=[.,;{_SPACE}#()\[\]\{{\}}\"'<>])")
_KEYWORD = re.compile(rf"@[A-Za-z]+(?=[{_SPACE}#<:])")
_SPARQL_KEYWORD = re.compile(
    rf"(?:[Pp][Rr][Ee][Ff][Ii][Xx]|[Bb][Aa][Ss][Ee]|[Gg][Rr][Aa][Pp][Hh])(?=[{_SPACE}#<])"
)
_SHORT_PREDICATE = re.compile(rf"a(?=[{_SPACE}#()\[\]\{{\}}\"'<>])")
_NEWLINE = re.compile(r"[ \t]*(?:#[^\n\r]*)?(?:\r\n|\n|\r)[ \t]*")
_WHITESPACE = re.compile(r"[ \t]+")
_END_OF_FILE = re.compile(r"(?:#[^\n\r]*)?\Z")
_NOT_SPACE = re.compile(rf"[^{_SPACE}]*")
_ESCAPE = re.compile(r"\\u([a-fA-F0-9]{4})|\\U([a-fA-F0-9]{8})|\\([\s\S])")
_ESCAPED = {
    "\\": "\\", "'": "'", '"': '"', "n": "\n", "r": "\r", "t": "\t", "f": "\f", "b": "\b",
    "_": "_", "~": "~", ".": ".", "-": "-", "!": "!", "$": "$", "&": "&", "(": "(", ")": ")",
    "*": "*", "+": "+", ",": ",", ";": ";", "=": "=", "/": "/", "?": "?", "#": "#", "@": "@",
    "%": "%",
}  # fmt: skip
_ASTRAL_CHARACTER = re.compile(r"[\U00010000-\U0010ffff]")

_ABSOLUTE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")
_COLON_FIRST = re.compile(r"[^/:]*:")
_BASE_PARTS = re.compile(r"(?:([A-Za-z][A-Za-z0-9+.\-]*:))?(?://[^/]*)?")
_BASE_TAIL = re.compile(rf"[^/?]*(?:\?[^{_LINE_END}]*)?\Z")
_QUERY_TAIL = re.compile(rf"(?:\?[^{_LINE_END}]*)?\Z")
_DOT_SEGMENT = re.compile(r"(?:^|/)\.\.?(?:\Z|[/#?])")
_REPLACEMENT = re.compile(r"\$([$&'])")

_INT32 = 2**31
_UNIT = 0xFFFF
_FIRST_ASTRAL = 0x10000


class TurtleError(Exception):
    """n3 refuses the document. The message is the one n3, or V8 under it, gives."""


def _units(text: str) -> str:
    """The text as JavaScript holds it: an astral character as its two surrogates."""

    def pair(found: re.Match[str]) -> str:
        code = ord(found.group()) - _FIRST_ASTRAL
        return chr(0xD800 + (code >> 10)) + chr(0xDC00 + (code & 0x3FF))

    return _ASTRAL_CHARACTER.sub(pair, text)


def _text(units: str) -> str:
    """Units as text: a surrogate pair joined into its character, a lone one left as it is."""
    return units.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


# --- The lexer ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    """One token, as the lexer hands it to the parser."""

    type: str
    value: str
    prefix: str
    line: int


@dataclass(frozen=True)
class _Found:
    """What one reader makes of the text at the current place."""

    type: str
    value: str = ""
    prefix: str = ""
    length: int = 0


def _unescape(item: str) -> str | None:
    """The text with its escape sequences replaced, or None if one of them is not an escape."""
    valid = True

    def replace(found: re.Match[str]) -> str:
        nonlocal valid
        four, eight, single = found.groups()
        if four is not None:
            return chr(int(four, 16))
        if eight is not None:
            code = int(eight, 16)
            if code <= _UNIT:
                return chr(code)
            # JavaScript shifts a number as a signed 32-bit integer and keeps the low 16
            # bits of each unit, so a code point above U+10FFFF still comes out as two units.
            code -= _FIRST_ASTRAL
            signed = code - 2 * _INT32 if code >= _INT32 else code
            return chr((0xD800 + (signed >> 10)) & _UNIT) + chr((0xDC00 + (code & 0x3FF)) & _UNIT)
        if single in _ESCAPED:
            return _ESCAPED[single]
        valid = False
        return ""

    replaced = _ESCAPE.sub(replace, item)
    return replaced if valid else None


class _Lexer:
    """`N3Lexer` with `n3: false` and no line mode, reading a whole document at once."""

    def __init__(self, text: str) -> None:
        # `_readStartingBom`
        self.text = text.removeprefix("\ufeff")
        self.pos = 0
        self.line = 1
        self.marker = ""

    def tokens(self) -> list[Token]:
        """Every token of the document, ending in `eof`: `_tokenizeToEnd`."""
        tokens: list[Token] = []
        while True:
            while newline := _NEWLINE.match(self.text, self.pos):
                self.pos = newline.end()
                self.line += 1
            if space := _WHITESPACE.match(self.text, self.pos):
                self.pos = space.end()
            if _END_OF_FILE.match(self.text, self.pos):
                tokens.append(Token("eof", "", "", self.line))
                return tokens

            line = self.line
            found = _READERS.get(self.text[self.pos], _Lexer.name)(self)
            kind = found.type if found else ""
            # A type can only follow `^^`, and only an IRI or a prefixed name can be one.
            if self.marker == "^^":
                kind = {"prefixed": "type", "IRI": "typeIRI"}.get(kind, "")
            if found is None or not kind:
                raise self._unexpected()
            tokens.append(Token(kind, found.value, found.prefix, line))
            self.marker = kind
            self.pos += found.length

    def _unexpected(self) -> TurtleError:
        """`_syntaxError`, naming the text up to the next white space."""
        issue = _NOT_SPACE.match(self.text, self.pos)
        return TurtleError(f'Unexpected "{issue.group() if issue else ""}" on line {self.line}.')

    def _left(self) -> int:
        return len(self.text) - self.pos

    def _to_the_end(self, pattern: re.Pattern[str]) -> re.Match[str] | None:
        """A match here, or failing that a match as if a space followed the document.

        A blank node, a number and a prefixed name may hold a dot but not end in one, so
        n3 looks one character past each, and at the end of the document supplies a space.
        """
        return pattern.match(self.text, self.pos) or pattern.match(f"{self.text[self.pos :]} ")

    def caret(self) -> _Found | None:
        if self._left() < 3 or self.text[self.pos + 1] != "^":  # noqa: PLR2004
            # One caret is a path, which only N3 has.
            return None
        self.marker = "^^"
        self.pos += 2
        return self.iri() if self.text[self.pos] == "<" else self.name()

    def iri(self) -> _Found | None:
        if plain := _UNESCAPED_IRI.match(self.text, self.pos):
            return _Found("IRI", plain[1], length=len(plain[0]))
        if escaped := _IRI.match(self.text, self.pos):
            value = _unescape(escaped[1])
            if value is None or _ILLEGAL_IRI.search(value):
                raise self._unexpected()
            return _Found("IRI", value, length=len(escaped[0]))
        if self._left() > 1 and self.text[self.pos + 1] == "<":
            return _Found("<<", length=2)
        return None

    def closing(self) -> _Found | None:
        if self._left() > 1 and self.text[self.pos + 1] == ">":
            return _Found(">>", length=2)
        return None

    def blank(self) -> _Found | None:
        if found := self._to_the_end(_BLANK):
            return _Found("blank", found[1], "_", len(found[0]))
        return None

    def string(self) -> _Found | None:
        if simple := _SIMPLE_STRING[self.text[self.pos]].match(self.text, self.pos):
            return _Found("literal", simple[1], length=len(simple[0]))
        value, length = self._long_string()
        if value is None:
            raise self._unexpected()
        return _Found("literal", value, length=length) if length else None

    def _long_string(self) -> tuple[str | None, int]:
        """A string with escapes, or in three quotes: `_parseLiteral`. No match is length 0."""
        if self._left() < 3:  # noqa: PLR2004
            return "", 0
        opening = _OPENING.match(self.text, self.pos)
        quotes = opening.group() if opening else ""
        closing = self.text.find(quotes, self.pos + len(quotes))
        while closing >= 0:
            backslashes = 0
            while self.text[closing - backslashes - 1] == "\\":
                backslashes += 1
            # An even number of backslashes, none among them, leaves the quotes unescaped.
            if backslashes % 2 == 0:
                raw = self.text[self.pos + len(quotes) : closing]
                lines = len(_LINE_BREAK.findall(raw))
                # Only a string in three quotes may run over a line.
                if len(quotes) == 1 and lines:
                    break
                self.line += lines
                return _unescape(raw), closing + len(quotes) - self.pos
            closing = self.text.find(quotes, closing + 1)
        return "", 0

    def at(self) -> _Found | None:
        if self.marker == "literal" and (language := _LANGCODE.match(self.text, self.pos)):
            return _Found("langcode", language[1], length=len(language[0]))
        if keyword := _KEYWORD.match(self.text, self.pos):
            return _Found(keyword[0], length=len(keyword[0]))
        return None

    def dot(self) -> _Found | None:
        if self._left() == 1 or not "0" <= self.text[self.pos + 1] <= "9":
            return _Found(".", length=1)
        return self.number()

    def number(self) -> _Found | None:
        found = self._to_the_end(_NUMBER)
        if not found:
            return None
        datatype = "double" if found[1] is not None else "decimal" if found[2] is not None else "integer"
        return _Found("literal", found[0], f"{XSD}{datatype}", len(found[0]))

    def sparql_keyword(self) -> _Found | None:
        if keyword := _SPARQL_KEYWORD.match(self.text, self.pos):
            return _Found(keyword[0].upper(), length=len(keyword[0]))
        return self.name()

    def boolean(self) -> _Found | None:
        if found := _BOOLEAN.match(self.text, self.pos):
            return _Found("literal", found[0], f"{XSD}boolean", len(found[0]))
        return self.name()

    def a(self) -> _Found | None:
        if _SHORT_PREDICATE.match(self.text, self.pos):
            return _Found("abbreviation", "a", length=1)
        return self.name()

    def mark(self) -> _Found | None:
        return _Found(self.text[self.pos], length=1)

    def brace(self) -> _Found | None:
        if self._left() < 2:  # noqa: PLR2004
            return None
        return _Found("{|", length=2) if self.text[self.pos + 1] == "|" else _Found("{", length=1)

    def bar(self) -> _Found | None:
        if self._left() > 1 and self.text[self.pos + 1] == "}":
            return _Found("|}", length=2)
        return None

    def nothing(self) -> _Found | None:
        """`?`, `=` and `!` begin a token only in N3."""
        return None

    def name(self) -> _Found | None:
        """A prefix after `@prefix`, or a prefixed name: the characters that settle nothing."""
        if self.marker in {"@prefix", "PREFIX"} and (prefix := _PREFIX.match(self.text, self.pos)):
            return _Found("prefix", prefix[1] or "", length=len(prefix[0]))
        if name := self._to_the_end(_PREFIXED):
            # Each escape the pattern lets into a local name stands for its own character.
            return _Found("prefixed", re.sub(r"\\(.)", r"\1", name[2]), name[1] or "", len(name[0]))
        return None


_READERS: dict[str, Callable[[_Lexer], _Found | None]] = {
    "^": _Lexer.caret,
    "<": _Lexer.iri,
    ">": _Lexer.closing,
    "_": _Lexer.blank,
    '"': _Lexer.string,
    "'": _Lexer.string,
    "?": _Lexer.nothing,
    "=": _Lexer.nothing,
    "!": _Lexer.nothing,
    "@": _Lexer.at,
    ".": _Lexer.dot,
    **dict.fromkeys("0123456789+-", _Lexer.number),
    **dict.fromkeys("BbPpGg", _Lexer.sparql_keyword),
    **dict.fromkeys("ft", _Lexer.boolean),
    "a": _Lexer.a,
    **dict.fromkeys(",;[]()}", _Lexer.mark),
    "{": _Lexer.brace,
    "|": _Lexer.bar,
}


def tokenize(text: str) -> list[Token]:
    """Every token of a document as n3's lexer gives them, values in UTF-16 units.

    Raises `TurtleError` at the first text that begins no token.
    """
    return _Lexer(_units(text)).tokens()


# --- Terms ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Term:
    """One term of a statement: an IRI, a blank node or a literal.

    `kind` is n3's `termType`. A literal's `datatype` is empty when it is plain text or
    carries a language, which is where n3 answers `xsd:string` and `rdf:langString`.
    """

    kind: str
    value: str
    language: str = ""
    datatype: str = ""

    @property
    def id(self) -> str:
        """The term as n3 names it in a refusal. Reading it of nothing is JavaScript's error."""
        if self.kind == "null":
            raise TurtleError("Cannot read properties of null (reading 'id')")
        if self.kind == "BlankNode":
            return f"_:{self.value}"
        if self.kind == "NamedNode":
            return self.value
        tail = f"@{self.language}" if self.language else f"^^{self.datatype}" if self.datatype else ""
        return f'"{self.value}"{tail}'


#: Where JavaScript holds `null`: a statement part that has not been read.
NULL = Term("null", "")
_NIL = Term("NamedNode", f"{RDF}nil")
_FIRST = Term("NamedNode", f"{RDF}first")
_REST = Term("NamedNode", f"{RDF}rest")
_TYPE = Term("NamedNode", f"{RDF}type")

type Triple = tuple[Term, Term, Term]


def _literal(value: str, *, language: str = "", datatype: str = "") -> Term:
    """`DataFactory.literal`: a language is lowercased, and `xsd:string` is no datatype."""
    if language:
        return Term("Literal", value, language=language.lower())
    return Term("Literal", value, datatype="" if datatype == f"{XSD}string" else datatype)


@dataclass
class BlankLabels:
    """Where n3's two counters stand, which between them name every blank node.

    Both are global to the JavaScript process. `documents` numbers each call to `parse`:
    a node written `_:x` in the first document parsed is `b0_x`. `anonymous` numbers each
    node written without a label, `[]` or a list cell: the first is `n3-0`. A fresh pair
    is a process that has parsed nothing, which is how `records check` starts.
    """

    documents: int = 0
    anonymous: int = 0


@dataclass
class _Context:
    """What `_saveContext` keeps of the statement around a blank node or a list."""

    type: str
    subject: Term
    predicate: Term
    object: Term


# --- The parser -----------------------------------------------------------------------------

type _Reader = Callable[[Token], _Reader | None]


def _substring(text: str, start: int, end: int) -> str:
    """`String.prototype.substring`: both ends held inside the text, and swapped if reversed."""
    low, high = sorted((max(0, min(start, len(text))), max(0, min(end, len(text)))))
    return text[low:high]


def _remove_dot_segments(iri: str) -> str:  # noqa: C901, PLR0912
    """`_removeDotSegments`: `./` and `../` resolved as n3 resolves them, step for step."""
    if not _DOT_SEGMENT.search(iri):
        return iri
    length = len(iri)

    def at(index: int) -> str | None:
        return iri[index] if 0 <= index < length else None

    result = ""
    i = -1
    path_start = -1
    segment_start = 0
    following: str | None = "/"
    while i < length:
        if following == ":":
            if path_start < 0:
                # Skip the two slashes before the authority, and then the authority.
                i += 1
                if at(i) == "/":
                    i += 1
                    if at(i) == "/":
                        path_start = i + 1
                        while path_start < length and iri[path_start] != "/":
                            i = path_start
                            path_start = i + 1
        elif following in {"?", "#"}:
            i = length
        elif following == "/" and at(i + 1) == ".":
            i += 1
            following = at(i + 1)
            if following == "/":
                result += _substring(iri, segment_start, i - 1)
                segment_start = i + 1
            elif following in {None, "?", "#"}:
                return result + _substring(iri, segment_start, i) + iri[i + 1 :]
            elif following == ".":
                i += 1
                following = at(i + 1)
                if following in {None, "/", "?", "#"}:
                    result += _substring(iri, segment_start, i - 2)
                    segment_start = result.rfind("/")
                    if segment_start >= path_start:
                        result = result[: max(segment_start, 0)]
                    if following != "/":
                        return f"{result}/{iri[i + 1 :]}"
                    segment_start = i + 1
        i += 1
        following = at(i)
    return result + _substring(iri, segment_start, length)


class _Parser:
    """`N3Parser` with `format: "Turtle"`: one reader per state, each returning the next."""

    def __init__(self, labels: BlankLabels) -> None:
        self._labels = labels
        self._blank_prefix = f"b{labels.documents}_"
        labels.documents += 1
        self._prefixes: dict[str, str] = {}
        self._stack: list[_Context] = []
        self._subject = NULL
        self._predicate = NULL
        self._object = NULL
        self._literal_value = ""
        self._prefix = ""
        self._sparql_style = False
        self._base = ""
        self._base_path = ""
        # Neither is set until a base is declared, and JavaScript joins an unset one to
        # text as the word it prints it as.
        self._base_root = "undefined"
        self._base_scheme = "undefined"
        self.triples: list[Triple] = []
        self.error: str | None = None

    # The callback: statements out, and the first refusal kept. After a refusal n3 swaps
    # the callback for one that does nothing, so later statements and refusals are lost.

    def _emit(self, subject: Term, predicate: Term, held: Term) -> None:
        if self.error is None:
            self.triples.append((subject, predicate, held))

    def _fail(self, message: str, token: Token) -> None:
        if self.error is None:
            self.error = f"{message} on line {token.line}."

    def _new_blank(self) -> Term:
        label = f"n3-{self._labels.anonymous}"
        self._labels.anonymous += 1
        return Term("BlankNode", label)

    def _save(self, kind: str, subject: Term, predicate: Term, held: Term) -> None:
        self._stack.append(_Context(kind, subject, predicate, held))

    def _restore(self, kind: str, token: Token) -> None:
        """`_restoreContext`. A bracket that closes nothing is refused and reading goes on."""
        context = self._stack.pop() if self._stack else None
        if context is None or context.type != kind:
            self._fail(f"Unexpected {token.type}", token)
            return
        self._subject = context.subject
        self._predicate = context.predicate
        self._object = context.object

    def _context_end(self) -> _Reader:
        """`_getContextEndReader`: what follows an object, by what encloses it."""
        if not self._stack:
            return self._punctuation
        return self._blank_tail if self._stack[-1].type == "blank" else self._list_item

    # IRIs

    def _set_base(self, iri: str) -> None:
        base = iri.split("#", 1)[0]
        self._base = base
        self._base_path = base if "/" not in base else _BASE_TAIL.sub("", base, count=1)
        parts = _BASE_PARTS.match(base)
        self._base_root = parts[0] if parts else ""
        self._base_scheme = parts[1] if parts and parts[1] is not None else "undefined"

    def _resolve(self, iri: str) -> str | None:
        """`_resolveIRI`: an IRI with a scheme as it is, any other against the base."""
        if _ABSOLUTE.match(iri):
            return iri
        if not iri:
            return self._base
        if iri[0] == "#":
            return self._base + iri
        if iri[0] == "?":
            return self._replace_query(iri)
        if iri[0] == "/":
            return (self._base_scheme if iri[1:2] == "/" else self._base_root) + _remove_dot_segments(iri)
        # A relative IRI cannot hold a colon in its first segment.
        return None if _COLON_FIRST.match(iri) else _remove_dot_segments(self._base_path + iri)

    def _replace_query(self, iri: str) -> str:
        """The base with its query replaced, through `String.prototype.replace`.

        The IRI is the replacement text, in which JavaScript reads `$$` as a dollar, `$&`
        as what was matched and `$'` as what follows the match, which is nothing here.
        """
        found = _QUERY_TAIL.search(self._base)
        matched = found.group() if found else ""
        start = found.start() if found else len(self._base)
        expanded = _REPLACEMENT.sub(lambda mark: {"$": "$", "&": matched, "'": ""}[mark[1]], iri)
        return self._base[:start] + expanded

    def _entity(self, token: Token) -> Term | None:
        """`_readEntity`: an IRI, a prefixed name or a labelled blank node, or a refusal."""
        if token.type in {"IRI", "typeIRI"}:
            iri = self._resolve(token.value)
            if iri is None:
                self._fail("Invalid IRI", token)
                return None
            return Term("NamedNode", iri)
        if token.type in {"type", "prefixed"}:
            prefix = self._prefixes.get(token.prefix)
            if prefix is None:
                self._fail(f'Undefined prefix "{token.prefix}:"', token)
                return None
            return Term("NamedNode", prefix + token.value)
        if token.type == "blank":
            return Term("BlankNode", self._blank_prefix + token.value)
        self._fail(f"Expected entity but got {token.type}", token)
        return None

    # Readers

    def top(self, token: Token) -> _Reader | None:
        """`_readInTopContext`: a declaration, the end, or the subject of a statement."""
        if token.type == "eof":
            return None
        if token.type in {"PREFIX", "@prefix"}:
            self._sparql_style = self._sparql_style or token.type == "PREFIX"
            return self._prefix_name
        if token.type in {"BASE", "@base"}:
            self._sparql_style = self._sparql_style or token.type == "BASE"
            return self._base_iri
        return self._read_subject(token)

    def _read_subject(self, token: Token) -> _Reader | None:
        self._predicate = NULL
        if token.type == "[":
            self._subject = self._new_blank()
            self._save("blank", self._subject, NULL, NULL)
            return self._blank_head
        if token.type == "(":
            self._save("list", _NIL, NULL, NULL)
            self._subject = NULL
            return self._list_item
        if token.type == "}":
            return self._punctuation(token)
        refusal = {
            "{": "Unexpected graph",
            "@forSome": 'Unexpected "@forSome"',
            "@forAll": 'Unexpected "@forAll"',
            "literal": "Unexpected literal",
            "<<": "Unexpected RDF* syntax",
        }.get(token.type)
        if refusal:
            self._fail(refusal, token)
            return None
        subject = self._entity(token)
        if subject is None:
            return None
        self._subject = subject
        return self._read_predicate

    def _read_predicate(self, token: Token) -> _Reader | None:
        kind = token.type
        if kind == "abbreviation":
            self._predicate = _TYPE
            return self._read_object
        if kind in {".", "]", "}"}:
            # No predicate came, so this must follow a trailing semicolon.
            if self._predicate is NULL:
                self._fail(f"Unexpected {kind}", token)
                return None
            self._subject = NULL
            return self._blank_tail(token) if kind == "]" else self._punctuation(token)
        if kind == ";":
            # Further semicolons are passed over.
            if self._predicate is not NULL:
                return self._read_predicate
            self._fail("Expected predicate but got ;", token)
            return None
        if kind in {"[", "blank"}:
            self._fail("Disallowed blank node as predicate", token)
            return None
        predicate = self._entity(token)
        if predicate is None:
            return None
        self._predicate = predicate
        return self._read_object

    def _read_object(self, token: Token) -> _Reader | None:
        if token.type == "literal":
            if not token.prefix:
                # Text, which may still be given a datatype or a language.
                self._literal_value = token.value
                return self._datatype_or_language
            self._object = _literal(token.value, datatype=token.prefix)
            return self._context_end()
        if token.type == "[":
            blank = self._new_blank()
            self._save("blank", self._subject, self._predicate, blank)
            self._subject = blank
            return self._blank_head
        if token.type == "(":
            self._save("list", self._subject, self._predicate, _NIL)
            self._subject = NULL
            return self._list_item
        refusal = {"{": "Unexpected graph", "<<": "Unexpected RDF* syntax"}.get(token.type)
        if refusal:
            self._fail(refusal, token)
            return None
        held = self._entity(token)
        if held is None:
            return None
        self._object = held
        return self._context_end()

    def _blank_head(self, token: Token) -> _Reader | None:
        if token.type == "]":
            self._subject = NULL
            return self._blank_tail(token)
        self._predicate = NULL
        return self._read_predicate(token)

    def _blank_tail(self, token: Token) -> _Reader | None:
        if token.type != "]":
            return self._blank_punctuation(token)
        if self._subject is not NULL:
            self._emit(self._subject, self._predicate, self._object)
        empty = self._predicate is NULL
        self._restore("blank", token)
        if self._object is not NULL:
            # The blank node was an object.
            return self._context_end()
        if self._predicate is not NULL:
            # The blank node was a predicate, which only N3 allows. Nothing in Turtle mode
            # leaves this state, and n3 reads it all the same, so it is kept.
            return self._read_object
        return self._read_predicate if empty else self._predicate_after_blank

    def _predicate_after_blank(self, token: Token) -> _Reader | None:
        if token.type in {".", "}"}:
            # The statement ends here with no predicate.
            self._subject = NULL
            return self._punctuation(token)
        return self._read_predicate(token)

    def _list_item(self, token: Token) -> _Reader | None:  # noqa: C901, PLR0912
        item = NULL
        cell = NULL
        following: _Reader = self._list_item
        previous = self._subject
        # A list is only ever read inside the context saved for it, so there is one.
        parent = self._stack[-1]

        if token.type == "[":
            cell = self._new_blank()
            item = self._new_blank()
            self._save("blank", cell, _FIRST, item)
            self._subject = item
            following = self._blank_head
        elif token.type == "(":
            cell = self._new_blank()
            self._save("list", cell, _FIRST, _NIL)
            self._subject = NULL
        elif token.type == ")":
            self._restore("list", token)
            # A list inside a list is the item of the cell that holds it.
            if self._stack and self._stack[-1].type == "list":
                self._emit(self._subject, self._predicate, self._object)
            if self._predicate is NULL:
                # The list was a subject, and an empty one needs no closing.
                following = self._read_predicate
                if self._subject is _NIL:
                    return following
            else:
                following = self._context_end()
                if self._object is _NIL:
                    return following
            cell = _NIL
        elif token.type == "literal":
            if not token.prefix:
                self._literal_value = token.value
                following = self._list_datatype_or_language
            else:
                item = _literal(token.value, datatype=token.prefix)
                following = self._context_end()
        elif token.type == "{":
            self._fail("Unexpected graph", token)
            return None
        else:
            entity = self._entity(token)
            if entity is None:
                return None
            item = entity

        self._link(previous, parent, cell, item)
        return following

    def _link(self, previous: Term, parent: _Context, cell: Term, item: Term) -> None:
        """The end of `_readListItem`: the cell joined to the one before it, and its item."""
        if cell is NULL:
            cell = self._new_blank()
            self._subject = cell
        if previous is NULL:
            # The first cell is the list, which is the subject or the object around it.
            if parent.predicate is NULL:
                parent.subject = cell
            else:
                parent.object = cell
        else:
            self._emit(previous, _REST, cell)
        if item is not NULL:
            self._emit(cell, _FIRST, item)

    def _datatype_or_language(self, token: Token) -> _Reader | None:
        return self._complete_literal(token, in_list=False)

    def _list_datatype_or_language(self, token: Token) -> _Reader | None:
        return self._complete_literal(token, in_list=True)

    def _complete_literal(self, token: Token, *, in_list: bool) -> _Reader | None:
        """`_completeObjectLiteral`: the text read, with the datatype or language after it."""
        consumed = True
        if token.type in {"type", "typeIRI"}:
            datatype = self._entity(token)
            if datatype is None:
                return None
            self._object = _literal(self._literal_value, datatype=datatype.value)
        elif token.type == "langcode":
            self._object = _literal(self._literal_value, language=token.value)
        else:
            self._object = _literal(self._literal_value)
            consumed = False
        if in_list:
            self._emit(self._subject, _FIRST, self._object)
        following = self._context_end()
        return following if consumed else following(token)

    def _punctuation(self, token: Token) -> _Reader | None:
        following: _Reader
        subject = self._subject
        if token.type == "}":
            self._fail("Unexpected graph closing", token)
            return None
        if token.type == ".":
            self._subject = NULL
            following = self._read_subject if self._stack else self.top
        elif token.type == ";":
            following = self._read_predicate
        elif token.type == ",":
            following = self._read_object
        elif token.type == "{|":
            self._fail("Unexpected RDF* syntax", token)
            return None
        elif token.type == "|}":
            if self._subject is NULL:
                raise TurtleError("Cannot read properties of null (reading 'termType')")
            self._fail("Unexpected asserted triple closing", token)
            return None
        else:
            self._fail(f'Expected punctuation to follow "{self._object.id}"', token)
            return None
        if subject is not NULL:
            self._emit(subject, self._predicate, self._object)
        return following

    def _blank_punctuation(self, token: Token) -> _Reader | None:
        following: _Reader
        if token.type == ";":
            following = self._read_predicate
        elif token.type == ",":
            following = self._read_object
        else:
            self._fail(f'Expected punctuation to follow "{self._object.id}"', token)
            return None
        self._emit(self._subject, self._predicate, self._object)
        return following

    def _prefix_name(self, token: Token) -> _Reader | None:
        if token.type != "prefix":
            self._fail("Expected prefix to follow @prefix", token)
            return None
        self._prefix = token.value
        return self._prefix_iri

    def _prefix_iri(self, token: Token) -> _Reader | None:
        if token.type != "IRI":
            self._fail(f'Expected IRI to follow prefix "{self._prefix}:"', token)
            return None
        node = self._entity(token)
        if node is None:
            # n3 reads the value of an IRI it has just refused.
            raise TurtleError("Cannot read properties of undefined (reading 'value')")
        self._prefixes[self._prefix] = node.value
        return self._declaration_end

    def _base_iri(self, token: Token) -> _Reader | None:
        iri = self._resolve(token.value) if token.type == "IRI" else None
        if not iri:
            self._fail("Expected valid IRI to follow base declaration", token)
            return None
        self._set_base(iri)
        return self._declaration_end

    def _declaration_end(self, token: Token) -> _Reader | None:
        # A declaration in SPARQL's style ends without a dot.
        if self._sparql_style:
            self._sparql_style = False
            return self.top(token)
        if token.type != ".":
            self._fail("Expected declaration to end with a dot", token)
            return None
        return self.top


def parse(text: str, labels: BlankLabels | None = None) -> list[Triple]:
    """Every statement of a Turtle document, in the order n3 emits them.

    `labels` is where n3's blank node counters stand, and is moved on. None is a process
    that has parsed nothing. Raises `TurtleError` with n3's message for a document n3
    refuses. The lexer runs over the whole document first, as n3's does when it is not
    given a callback, so a fault of spelling anywhere is reported before a fault of
    grammar above it.
    """
    parser = _Parser(BlankLabels() if labels is None else labels)
    try:
        # The last token is `eof`, and no reader asks for another after it.
        tokens = iter(tokenize(text))
        reader: _Reader | None = parser.top
        while reader is not None:
            reader = reader(next(tokens))
    except TurtleError as error:
        raise TurtleError(_text(str(error))) from None
    if parser.error is not None:
        raise TurtleError(_text(parser.error))
    return [
        (_term_text(subject), _term_text(predicate), _term_text(held))
        for subject, predicate, held in parser.triples
    ]


def _term_text(term: Term) -> Term:
    return Term(term.kind, _text(term.value), term.language, _text(term.datatype))
