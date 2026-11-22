"""Text layers for Office Open XML packages: `ooxml-sheet-tsv@1` and `ooxml-document-text@1`.

This is `shared/archive/layers/office/`: `xml.ts`, `package.ts`, `sheet.ts`, `document.ts`
and `index.ts`. `address.ts`, which turns a cell reference into a range of the layer, is
read by the span resolvers and belongs with them.

An Office package is a zip of XML parts. Nobody cites `xl/worksheets/sheet3.xml`, so the
package gives one text layer of its own, the way a PDF does, and no member objects.

A workbook becomes one block per sheet: a form feed, a tab separated header naming the
index, the sheet and its range, then one line per sheet row from row 1 to the last row
holding a value, each the row's cells from column A to its last value, tab separated. The
values are the stored text: a number is the characters in `<v>`, so 0.10 stays 0.10, and no
display format is applied. A Word document becomes its body in document order, a paragraph
to a line and a table row to a line of tab separated cells.

The scanner reports elements and character data in order and builds no tree, because a
shared string is the text of its runs in the order the file writes them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from rewire_evidence.archive import ArchiveError
from rewire_evidence.containers import DEFAULT_LIMITS, Limits, expand_container, zip_entry_names
from rewire_evidence.js import SPACE_CLASS, lower, parse_int, trim, units
from rewire_evidence.markup import declaration_end, decode_references, tag_end
from rewire_evidence.textlayer import Layer, LayerFailure, layer_from_text

# ---- the element scanner --------------------------------------------------------------

#: The five named entities XML defines without a DTD. Deliberately not the table in
#: `markup`, so that a change there cannot move a layer hash here.
_NAMED: Final = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_LINE_END: Final = re.compile(r"\r\n?")
_TAG: Final = re.compile(rf"(/?)[{SPACE_CLASS}]*([A-Za-z_][A-Za-z0-9_.:-]*)")
_ATTRIBUTE: Final = re.compile(
    rf"([A-Za-z_][A-Za-z0-9_.:-]*)[{SPACE_CLASS}]*=[{SPACE_CLASS}]*(?:\"([^\"]*)\"|'([^']*)')"
)


def decode_xml(text: str) -> str:
    """Decode the references XML defines. An unknown reference is left exactly as written."""
    return decode_references(text, _NAMED)


def normalise_line_endings(text: str) -> str:
    """A single U+000A wherever the file writes CR LF or a lone CR, as an XML parser hands over."""
    return _LINE_END.sub("\n", text)


def read_characters(raw: str) -> str:
    """Character data as a parser yields it: line endings normalised, then entities decoded."""
    return decode_xml(normalise_line_endings(raw))


class XmlHandler:
    """What a reader of an XML part is told, in document order. Each does nothing unless overridden."""

    def open(self, name: str, attributes: Mapping[str, str]) -> None:
        """An element opens. One written as self-closing is closed straight after."""

    def close(self, name: str) -> None:
        """An element closes."""

    def text(self, value: str) -> None:
        """Character data, decoded."""


def _local_name(name: str) -> str:
    """The local name of a tag or attribute, lower-cased. The names are ASCII by the pattern."""
    return name.rpartition(":")[2].lower()


def _attributes(body: str) -> dict[str, str]:
    """The attributes of a tag by local name, the last of a name winning.

    The TypeScript holds them in a plain object, where assigning text to `__proto__` sets
    nothing, so an attribute of that name is not held here either.
    """
    return {
        name: read_characters(found.group(2) or found.group(3) or "")
        for found in _ATTRIBUTE.finditer(body)
        if (name := _local_name(found.group(1))) != "__proto__"
    }


def scan_xml(text: str, handler: XmlHandler) -> None:  # noqa: C901 - the scanner is one loop
    """Walk an XML document and report every element and every run of character data, in order."""
    index = 0
    while index < len(text):
        opened = text.find("<", index)
        if opened < 0:
            handler.text(read_characters(text[index:]))
            return
        if opened > index:
            handler.text(read_characters(text[index:opened]))

        if text.startswith("<!--", opened):
            end = text.find("-->", opened + 4)
            index = len(text) if end < 0 else end + 3
            continue
        if text.startswith("<![CDATA[", opened):
            end = text.find("]]>", opened + 9)
            handler.text(normalise_line_endings(text[opened + 9 : len(text) if end < 0 else end]))
            index = len(text) if end < 0 else end + 3
            continue
        if text.startswith("<?", opened):
            end = text.find("?>", opened + 2)
            index = len(text) if end < 0 else end + 2
            continue
        if text.startswith("<!", opened):
            end = declaration_end(text, opened + 2)
            index = len(text) if end < 0 else end + 1
            continue

        end = tag_end(text, opened + 1)
        if end < 0:
            # An unterminated `<` is character data, not a tag.
            handler.text(read_characters(text[opened:]))
            return
        body = text[opened + 1 : end]
        index = end + 1
        found = _TAG.match(body)
        if not found:
            continue
        name = _local_name(found.group(2))
        if found.group(1):
            handler.close(name)
            continue
        handler.open(name, _attributes(body))
        if body.endswith("/"):
            handler.close(name)


# ---- what kind of package -------------------------------------------------------------

OFFICE_KINDS: Final = ("xlsx", "xlsm", "docx", "pptx", "opendocument", "ooxml")

#: The media type each kind is recorded under.
OFFICE_MEDIA_TYPES: Final[Mapping[str, str]] = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "opendocument": "application/vnd.oasis.opendocument",
    "ooxml": "application/vnd.openxmlformats-officedocument",
}

#: Kinds a layer is built for. A presentation and an OpenDocument package are detected and
#: reported, not guessed at.
NORMALISED_KINDS: Final = frozenset({"xlsx", "xlsm", "docx"})

#: The main-part content types OOXML defines, and the kind each one names.
_MAIN_CONTENT_TYPES: Final = (
    ("application/vnd.ms-excel.sheet.macroenabled.main+xml", "xlsm"),
    ("application/vnd.ms-excel.template.macroenabled.main+xml", "xlsm"),
    ("application/vnd.ms-excel.addin.macroenabled.main+xml", "xlsm"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml", "xlsx"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml", "xlsx"),
    ("application/vnd.ms-word.document.macroenabled.main+xml", "docx"),
    ("application/vnd.ms-word.template.macroenabled.main+xml", "docx"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml", "docx"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml", "docx"),
    ("application/vnd.ms-powerpoint.presentation.macroenabled.main+xml", "pptx"),
    ("application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml", "pptx"),
    ("application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml", "pptx"),
    ("application/vnd.openxmlformats-officedocument.presentationml.template.main+xml", "pptx"),
)

#: The central directory is read this far, well past the 60 members the largest workbook holds.
_ENTRY_LIMIT: Final = 8192
_MAIN_PARTS: Final = (
    ("xlsx", ("xl/workbook.xml", "xl/workbook.bin")),
    ("docx", ("word/document.xml",)),
    ("pptx", ("ppt/presentation.xml",)),
)
_PART_PREFIXES: Final = (("xl/", "xlsx"), ("word/", "docx"), ("ppt/", "pptx"))
_CONTENT_TYPES: Final = "[Content_Types].xml"
_UTF16_MARK: Final = b"\xff\xfe"


def unsupported_reason(kind: str) -> str:
    """Why a detected kind carries no layer."""
    if kind == "pptx":
        return "office presentation package, no normaliser"
    if kind == "opendocument":
        return "opendocument package, no normaliser"
    return "office package with no recognised main part, no normaliser"


@dataclass(frozen=True)
class OfficeDetection:
    """The kind of a package, and what in it decided the kind."""

    kind: str
    evidence: str


def kind_from_names(names: Sequence[str]) -> OfficeDetection | None:
    """The kind, from the member names alone, or `None` for a zip that is no such package."""
    if "mimetype" in names and any(name.startswith("META-INF/") for name in names):
        return OfficeDetection("opendocument", "mimetype member and a META-INF directory")
    for kind, mains in _MAIN_PARTS:
        main = next((name for name in names if name in mains), None)
        if main is not None:
            return OfficeDetection(kind, f"member {main}")
    for prefix, kind in _PART_PREFIXES:
        part_name = next((name for name in names if name.startswith(prefix)), None)
        if part_name is not None:
            return OfficeDetection(kind, f"member {part_name}")
    if _CONTENT_TYPES in names:
        return OfficeDetection("ooxml", "member [Content_Types].xml and no recognised main part")
    return None


def detect_office(data: bytes) -> OfficeDetection | None:
    """The kind of a zip, from its central directory."""
    return kind_from_names(zip_entry_names(data, _ENTRY_LIMIT))


@dataclass
class OfficePackage:
    """An expanded package."""

    kind: str
    evidence: str
    #: Every member, keyed on its path inside the package.
    members: dict[str, bytes]
    #: What the central directory promised and the reader could not deliver.
    problems: list[str]


def part(package: OfficePackage, path: str) -> str | None:
    """The member as text, or `None` when the package does not hold it.

    Every part is UTF-8 or UTF-16 with a byte order mark, which is framing and not content.
    """
    content = package.members.get(path)
    if content is None:
        return None
    if content.startswith(_UTF16_MARK):
        body = content[2:]
        return body[: len(body) - len(body) % 2].decode("utf-16-le", "surrogatepass")
    return content.decode("utf-8", "replace").removeprefix("﻿")


class _Declared(XmlHandler):
    def __init__(self) -> None:
        self.types: list[str] = []

    def open(self, name: str, attributes: Mapping[str, str]) -> None:
        if name == "override" and attributes.get("contenttype"):
            self.types.append(lower(attributes["contenttype"]))


def _kind_from_content_types(package: OfficePackage) -> OfficeDetection | None:
    """The kind `[Content_Types].xml` declares, which outranks a part prefix."""
    text = part(package, _CONTENT_TYPES)
    if text is None:
        return None
    declared = _Declared()
    scan_xml(text, declared)
    for content_type, kind in _MAIN_CONTENT_TYPES:
        if content_type in declared.types:
            return OfficeDetection(kind, f"[Content_Types].xml declares {content_type}")
    return None


def read_package(data: bytes, limits: Limits = DEFAULT_LIMITS) -> OfficePackage | None:
    """Expand an Office package and settle its kind, or `None` for bytes that are not one.

    Raises `ArchiveError` for bytes whose directory names a package and that are not a zip.
    """
    by_names = detect_office(data)
    if not by_names:
        return None
    expansion = expand_container(data, limits)
    members = {member.path: member.content for member in expansion.members}
    package = OfficePackage(by_names.kind, by_names.evidence, members, expansion.problems)
    declared = _kind_from_content_types(package)
    if declared:
        package.kind, package.evidence = declared.kind, declared.evidence
    return package


# ---- ooxml-sheet-tsv@1 ----------------------------------------------------------------

#: Rows serialised per sheet, columns per row, and characters in the whole layer. Each is
#: about three times the largest the store holds. A breach is an unsupported object, never a
#: truncated layer.
ROWS_PER_SHEET: Final = 250_000
COLUMNS_PER_ROW: Final = 4_096
SHEET_CHARACTERS: Final = 64_000_000

SHEET_NORMALISER: Final = "ooxml-sheet-tsv@1"
SHEET_DERIVATION: Final = "ooxml-sheet-tsv@1 stored cell values, one tab separated block per sheet"
#: The marker that opens a sheet block: a form feed, then a tab separated header.
BLOCK_MARKER: Final = "\f# sheet\t"

_COLUMN: Final = re.compile(r"[A-Z]{1,7}")
_REFERENCE: Final = re.compile(r"\$?([A-Z]{1,7})\$?([0-9]{1,7})")
_LETTERS: Final = 26
_ESCAPES: Final = {
    **{code: f"\\x{code:02x}" for code in (*range(0x20), 0x7F)},
    ord("\\"): "\\\\",
    ord("\t"): "\\t",
    ord("\n"): "\\n",
    ord("\r"): "\\r",
    ord("\f"): "\\f",
}


@dataclass(frozen=True)
class SheetCell:
    """One cell that holds a value."""

    row: int
    column: int
    value: str


@dataclass
class Sheet:
    """One sheet of a workbook, read."""

    name: str
    #: The part path inside the package, for a problem line.
    path: str
    cells: list[SheetCell] = field(default_factory=list)
    last_row: int = 0
    last_column: int = 0


def column_number(label: str) -> int:
    """The 1-based column number of an A1 column label, or 0 for anything that is not one."""
    if not _COLUMN.fullmatch(label):
        return 0
    value = 0
    for character in label:
        value = value * _LETTERS + ord(character) - 64
    return value


def column_label(column: int) -> str:
    """The A1 column label of a 1-based column number."""
    if column < 1:
        raise ValueError(f"{column} is not a column number")
    label = ""
    rest = column
    while rest > 0:
        rest, remainder = divmod(rest - 1, _LETTERS)
        label = chr(65 + remainder) + label
    return label


def parse_reference(reference: str) -> tuple[int, int] | None:
    """An A1 reference split into its row and column, or `None`.

    The reference is read in upper case. `str.upper` and `toUpperCase` both apply the full
    Unicode mapping, and only a mapping onto A to Z, 0 to 9 or `$` can make a reference.
    """
    found = _REFERENCE.fullmatch(trim(reference).upper())
    if not found:
        return None
    column = column_number(found.group(1))
    row = int(found.group(2))
    return (row, column) if row else None


def escape_cell(value: str) -> str:
    r"""Make a value safe to sit in a tab separated field, reversibly.

    A backslash doubles, the four characters that would break the grid get their usual
    letter, and any other C0 control or DEL becomes `\xNN`. Nothing else is touched.
    """
    return value.translate(_ESCAPES)


class _SharedStrings(XmlHandler):
    """One entry per `<si>`: the text of every `<t>` it holds, less phonetic guide text."""

    def __init__(self) -> None:
        self.table: list[str] = []
        self.in_item = False
        self.buffer: list[str] = []
        self.keeping = 0
        self.skipping = 0

    def open(self, name: str, attributes: Mapping[str, str]) -> None:  # noqa: ARG002 - the handler's shape
        if name == "si":
            self.in_item, self.buffer, self.keeping, self.skipping = True, [], 0, 0
        elif not self.in_item:
            return
        elif name == "rph":
            self.skipping += 1
        elif name == "t":
            self.keeping += 1

    def close(self, name: str) -> None:
        if name == "si":
            self.in_item = False
            self.table.append("".join(self.buffer))
        elif not self.in_item:
            return
        elif name == "rph":
            self.skipping = max(0, self.skipping - 1)
        elif name == "t":
            self.keeping = max(0, self.keeping - 1)

    def text(self, value: str) -> None:
        if self.in_item and self.keeping > 0 and self.skipping == 0:
            self.buffer.append(value)


def shared_strings(package: OfficePackage) -> list[str]:
    """The shared string table, one entry per `<si>` in file order."""
    text = part(package, "xl/sharedStrings.xml")
    if text is None:
        return []
    handler = _SharedStrings()
    scan_xml(text, handler)
    return handler.table


class _Relationships(XmlHandler):
    def __init__(self) -> None:
        self.targets: dict[str, str] = {}

    def open(self, name: str, attributes: Mapping[str, str]) -> None:
        if name == "relationship" and attributes.get("id") and attributes.get("target"):
            self.targets[attributes["id"]] = attributes["target"]


class _Declarations(XmlHandler):
    def __init__(self, targets: Mapping[str, str]) -> None:
        self.targets = targets
        self.sheets: list[tuple[str, str]] = []

    def open(self, name: str, attributes: Mapping[str, str]) -> None:
        if name != "sheet":
            return
        index = len(self.sheets) + 1
        label = attributes.get("name", f"sheet{index}")
        target = self.targets.get(attributes["id"]) if attributes.get("id") else None
        # A target is relative to the xl directory unless it opens with a slash, in which
        # case it is already a package path.
        if target is None:
            path = f"xl/worksheets/sheet{index}.xml"
        else:
            path = target[1:] if target.startswith("/") else f"xl/{target}"
        self.sheets.append((label, path))


def sheet_parts(package: OfficePackage, problems: list[str]) -> list[tuple[str, str]]:
    """The sheets a workbook declares, in workbook order, each with the part it lives in."""
    workbook = part(package, "xl/workbook.xml")
    if workbook is None:
        problems.append("the package holds no xl/workbook.xml")
        return []
    relationships = _Relationships()
    relationship_text = part(package, "xl/_rels/workbook.xml.rels")
    if relationship_text is not None:
        scan_xml(relationship_text, relationships)
    declarations = _Declarations(relationships.targets)
    scan_xml(workbook, declarations)
    if not declarations.sheets:
        problems.append("xl/workbook.xml declares no sheet")
    return declarations.sheets


class _SheetReader(XmlHandler):
    """Every cell that holds a value in one worksheet part."""

    def __init__(self, sheet: Sheet, strings: Sequence[str], problems: list[str]) -> None:
        self.sheet = sheet
        self.strings = strings
        self.problems = problems
        self.row = 0
        self.column = 0
        self.type = ""
        self.collecting: str | None = None
        self.buffer: list[str] = []
        self.inline_keep = 0

    def finish(self) -> None:
        if self.collecting is None:
            return
        raw = "".join(self.buffer)
        value = raw
        if self.collecting == "value" and self.type == "s":
            at = parse_int(raw)
            if not at.is_integer() or at < 0 or at >= len(self.strings):
                self.problems.append(
                    f"{self.sheet.path}: shared string index {raw} "
                    f"is outside the table of {len(self.strings)}"
                )
                value = ""
            else:
                value = self.strings[int(at)]
        self.collecting = None
        self.buffer = []
        if not value:
            return
        self.sheet.cells.append(SheetCell(self.row, self.column, value))
        self.sheet.last_row = max(self.sheet.last_row, self.row)
        self.sheet.last_column = max(self.sheet.last_column, self.column)

    def open(self, name: str, attributes: Mapping[str, str]) -> None:
        if name == "row":
            # A row with no `r` follows the one before it.
            declared = parse_int(attributes["r"]) if attributes.get("r") else 0.0
            self.row = int(declared) if declared.is_integer() and declared > 0 else self.row + 1
            self.column = 0
        elif name == "c":
            self.finish()
            written = attributes.get("r")
            reference = parse_reference(written) if written else None
            if reference:
                self.row, self.column = reference
            else:
                # A cell with no reference sits in the next column of its row.
                self.column += 1
                if written:
                    self.problems.append(f"{self.sheet.path}: cell reference {written} is not A1 notation")
            self.type = attributes.get("t", "")
        elif name == "v":
            self.collecting, self.buffer = "value", []
        elif name == "is":
            self.collecting, self.buffer, self.inline_keep = "inline", [], 0
        elif self.collecting == "inline" and name == "t":
            self.inline_keep += 1

    def close(self, name: str) -> None:
        if name == "v" and self.collecting == "value":
            self.finish()
        elif name == "is" and self.collecting == "inline":
            self.inline_keep = 0
            self.finish()
        elif name == "c":
            self.finish()
        elif self.collecting == "inline" and name == "t":
            self.inline_keep = max(0, self.inline_keep - 1)

    def text(self, value: str) -> None:
        if self.collecting == "value" or (self.collecting == "inline" and self.inline_keep > 0):
            self.buffer.append(value)


def read_sheet(text: str, strings: Sequence[str], name: str, path: str, problems: list[str]) -> Sheet:
    """One worksheet part, read."""
    reader = _SheetReader(Sheet(name, path), strings, problems)
    scan_xml(text, reader)
    reader.finish()
    return reader.sheet


def read_workbook(package: OfficePackage, problems: list[str]) -> list[Sheet]:
    """Every sheet of a workbook, read."""
    strings = shared_strings(package)
    sheets: list[Sheet] = []
    for name, path in sheet_parts(package, problems):
        text = part(package, path)
        if text is None:
            problems.append(f"{path} is declared by the workbook and is not in the package")
            sheets.append(Sheet(name, path))
        else:
            sheets.append(read_sheet(text, strings, name, path, problems))
    return sheets


def serialise_workbook(sheets: Sequence[Sheet]) -> str:
    """The workbook, serialised. Raises `LayerFailure` past a limit."""
    parts: list[str] = []
    characters = 0

    def push(text: str) -> None:
        nonlocal characters
        # `text.length`: UTF-16 units, so two for a character beyond U+FFFF.
        characters += len(text) if text.isascii() else units(text)
        if characters > SHEET_CHARACTERS:
            raise LayerFailure(f"the workbook serialises past the {SHEET_CHARACTERS} character limit")
        parts.append(text)

    for index, sheet in enumerate(sheets):
        if sheet.last_row > ROWS_PER_SHEET:
            raise LayerFailure(
                f"sheet {sheet.name} holds {sheet.last_row} rows, over the {ROWS_PER_SHEET} row limit"
            )
        if sheet.last_column > COLUMNS_PER_ROW:
            raise LayerFailure(
                f"sheet {sheet.name} holds {sheet.last_column} columns, "
                f"over the {COLUMNS_PER_ROW} column limit"
            )
        extent = "empty" if sheet.last_row == 0 else f"A1:{column_label(sheet.last_column)}{sheet.last_row}"
        push(f"{BLOCK_MARKER}{index + 1}\t{escape_cell(sheet.name)}\t{extent}\n")
        by_row: dict[int, dict[int, str]] = {}
        for cell in sheet.cells:
            by_row.setdefault(cell.row, {})[cell.column] = cell.value
        for number in range(1, sheet.last_row + 1):
            row = by_row.get(number)
            if not row:
                push("\n")
                continue
            push("\t".join(escape_cell(row.get(column, "")) for column in range(1, max(row) + 1)) + "\n")
    return "".join(parts)


def sheet_layer(data: bytes, package: OfficePackage, problems: list[str]) -> Layer:
    """The text layer of a workbook."""
    return layer_from_text(
        data, serialise_workbook(read_workbook(package, problems)), SHEET_NORMALISER, SHEET_DERIVATION
    )


# ---- ooxml-document-text@1 ------------------------------------------------------------

DOCUMENT_NORMALISER: Final = "ooxml-document-text@1"
DOCUMENT_DERIVATION: Final = (
    "ooxml-document-text@1 word/document.xml in document order, tables as tab separated rows"
)
#: The body part of a Word package.
BODY_PART: Final = "word/document.xml"
#: Discarded with their content: field instruction code, text a tracked change deleted, and
#: the alternate rendering of content already supplied.
_DOCUMENT_SKIP: Final = frozenset({"instrtext", "deltext", "fallback"})
_LAST_CHARACTER: Final = 0x10FFFF


class _DocumentReader(XmlHandler):
    """A paragraph outside a table is a line. A table row is a line of tab separated cells."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        #: Characters of the line being built, outside a table.
        self.line: list[str] = []
        #: Fields of the table row being built, innermost table last.
        self.rows: list[list[str]] = []
        #: Characters of the cell being built, innermost cell last.
        self.cells: list[list[str]] = []
        self.skipping = 0
        self.in_text = 0

    def current(self) -> list[str]:
        return self.cells[-1] if self.cells else self.line

    def end_line(self) -> None:
        self.lines.append("".join(self.line))
        self.line = []

    def open(self, name: str, attributes: Mapping[str, str]) -> None:  # noqa: C901, PLR0912 - one element to a branch
        if name in _DOCUMENT_SKIP:
            self.skipping += 1
        elif self.skipping > 0:
            return
        elif name == "t":
            self.in_text += 1
        elif name == "tr":
            self.rows.append([])
        elif name == "tc":
            self.cells.append([])
        elif name == "tab":
            # A space, never a literal tab, which would make a prose line look like a table row.
            self.current().append(" ")
        elif name in {"br", "cr"}:
            if self.cells:
                self.current().append(" ")
            else:
                self.end_line()
        elif name == "nobreakhyphen":
            self.current().append("‑")
        elif name == "softhyphen":
            self.current().append("­")
        elif name == "sym" and attributes.get("char"):
            code = parse_int(attributes["char"], 16)
            if 0 < code <= _LAST_CHARACTER:
                self.current().append(chr(int(code)))

    def close(self, name: str) -> None:  # noqa: C901 - one element to a branch
        if name in _DOCUMENT_SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif self.skipping > 0:
            return
        elif name == "t":
            self.in_text = max(0, self.in_text - 1)
        elif name == "p":
            # A paragraph inside a cell keeps the cell on one line.
            if not self.cells:
                self.end_line()
            elif self.cells[-1] and self.cells[-1][-1] != " ":
                self.cells[-1].append(" ")
        elif name == "tc":
            cell = self.cells.pop() if self.cells else []
            written = escape_cell("".join(cell).removesuffix(" "))
            (self.rows[-1] if self.rows else self.current()).append(written)
        elif name == "tr":
            row = self.rows.pop() if self.rows else []
            while row and not row[-1]:
                row.pop()
            rendered = "\t".join(row)
            if self.cells:
                self.current().append(rendered)
            else:
                self.lines.append(rendered)

    def text(self, value: str) -> None:
        if self.skipping == 0 and self.in_text > 0:
            self.current().append(value)


def serialise_document(text: str) -> str:
    """The document, serialised: `word/document.xml` in document order."""
    reader = _DocumentReader()
    scan_xml(text, reader)
    if reader.line:
        reader.end_line()
    return "\n".join(reader.lines) + "\n" if reader.lines else ""


def document_layer(data: bytes, package: OfficePackage, problems: list[str]) -> Layer:
    """The text layer of a Word document. Raises `LayerFailure` for a package with no body."""
    body = part(package, BODY_PART)
    if body is None:
        raise LayerFailure(f"the package holds no {BODY_PART}")
    text = serialise_document(body)
    if not text:
        problems.append(f"{BODY_PART} holds no text")
    return layer_from_text(data, text, DOCUMENT_NORMALISER, DOCUMENT_DERIVATION)


# ---- the layer of a package -----------------------------------------------------------


@dataclass
class OfficeResult:
    """The text layer of an Office package, or the reason there is none."""

    kind: str
    #: What in the package decided the kind.
    evidence: str
    media_type: str
    layer: Layer | None = None
    unsupported: str | None = None
    problems: list[str] = field(default_factory=list)


def office_layer(data: bytes, limits: Limits = DEFAULT_LIMITS) -> OfficeResult | None:
    """The text layer of an Office package, or `None` for bytes that are not one."""
    try:
        package = read_package(data, limits)
    except ArchiveError as error:
        return OfficeResult(
            "ooxml",
            "zip signature",
            OFFICE_MEDIA_TYPES["ooxml"],
            unsupported=f"office package did not expand: {error}",
        )
    if not package:
        return None
    result = OfficeResult(
        package.kind, package.evidence, OFFICE_MEDIA_TYPES[package.kind], problems=list(package.problems)
    )
    if package.kind not in NORMALISED_KINDS:
        result.unsupported = unsupported_reason(package.kind)
        return result
    try:
        if package.kind == "docx":
            result.layer = document_layer(data, package, result.problems)
        else:
            result.layer = sheet_layer(data, package, result.problems)
    except LayerFailure as error:
        result.unsupported = f"{package.kind} normaliser failed: {error}"
    return result
