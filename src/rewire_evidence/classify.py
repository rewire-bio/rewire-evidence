"""What class of bytes an object holds, which decides the normaliser that runs.

This is `shared/archive/layers/classify.ts`. The class is read from the bytes themselves,
with a served media type as a hint only, because the suffix and the served type are both
unreliable: a third of the gzipped artifacts file(1) calls text/plain are JATS XML whose
first byte is the root element with no prolog.

A class that cannot be determined comes back as `unknown` with the reason, because an
unclassifiable source is a finding. One input raises instead, as it does in Node: UTF-16
text in big-endian order with an odd number of bytes after the mark, which `swap16` refuses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from rewire_evidence.js import SPACE, SPACE_CLASS, lower, slice_units, trim
from rewire_evidence.markup import declaration_end
from rewire_evidence.office import NORMALISED_KINDS, OFFICE_MEDIA_TYPES, detect_office, unsupported_reason

LAYER_CLASSES: Final = ("pdf", "xml", "html", "text", "office", "container", "unknown")
ENCODINGS: Final = ("utf-8", "utf-8-bom", "utf-16le", "utf-16be", "latin-1")

_UTF8_MARK: Final = b"\xef\xbb\xbf"
_UTF16_LE: Final = b"\xff\xfe"
_UTF16_BE: Final = b"\xfe\xff"
#: A latin-1 page, and not a binary, holds at most one byte in twenty outside ASCII.
_HIGH_SHARE: Final = 20
_HEAD: Final = 4096
_STEPS: Final = 16
_TAR_AT: Final = 257
_TAR_BLOCK: Final = 512

_PROLOG: Final = re.compile(rf"<\?xml[{SPACE_CLASS}?]", re.IGNORECASE | re.ASCII)
_DOCTYPE: Final = re.compile(r"<!doctype", re.IGNORECASE | re.ASCII)
_HTML_DOCTYPE: Final = re.compile(rf"<!doctype[{SPACE_CLASS}]+html")
_ROOT: Final = re.compile(r"<([A-Za-z_][A-Za-z0-9_.:-]*)")
_HIGH: Final = bytes(range(0x80))

#: HTML flow elements a markdown file commonly opens with. A document rooted at one of these,
#: with no prolog, no doctype and no html element, is markdown or a fragment. Every name
#: that is also a plausible XML root is left out, `article` above all.
_HTML_ELEMENTS: Final = frozenset(
    {
        "b", "blockquote", "br", "center", "details", "div", "em", "font", "h1", "h2", "h3", "h4", "h5",
        "h6", "hr", "i", "iframe", "img", "kbd", "li", "ol", "p", "picture", "small", "span", "strong",
        "sub", "summary", "sup", "u", "ul", "video",
    }
)  # fmt: skip


class Undecodable(ValueError):  # noqa: N818 - Node's RangeError, which stops the object being read at all
    """Bytes Node refuses to decode. The message is Node's."""


@dataclass(frozen=True)
class Classification:
    """The class of a run of bytes."""

    kind: str
    #: The media type this classification implies, not the one served.
    media_type: str
    #: What in the bytes decided it, or why nothing did.
    evidence: str
    #: Set when the class is known but no text layer can be made from it.
    unsupported: str | None = None
    #: How the bytes decode, for the text classes.
    encoding: str | None = None


def is_utf8(data: bytes) -> bool:
    """Whether the bytes are well formed UTF-8."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def encoding_of(data: bytes) -> str | None:
    """How the bytes decode to characters, or `None` when they are not text."""
    if data.startswith(_UTF8_MARK):
        return "utf-8-bom"
    if data.startswith(_UTF16_LE):
        return "utf-16le"
    if data.startswith(_UTF16_BE):
        return "utf-16be"
    if b"\x00" in data:
        return None
    if is_utf8(data):
        return "utf-8"
    # No NUL and not UTF-8. It is text only when the bytes outside ASCII are a minority.
    high = len(data.translate(None, _HIGH))
    return "latin-1" if high * _HIGH_SHARE <= len(data) else None


def decode(data: bytes, encoding: str) -> str:
    """The characters the bytes decode to, with any byte order mark removed.

    Half a surrogate pair in UTF-16 input is kept as it is, as Node keeps it. Raises
    `Undecodable` for big-endian UTF-16 of an odd length.
    """
    if encoding == "utf-8":
        return data.decode("utf-8")
    if encoding == "utf-8-bom":
        return data[3:].decode("utf-8", "replace")
    if encoding == "utf-16le":
        body = data[2:]
        return body[: len(body) - len(body) % 2].decode("utf-16-le", "surrogatepass")
    if encoding == "utf-16be":
        if len(data) % 2:
            raise Undecodable("Buffer size must be a multiple of 16-bits")
        return data[2:].decode("utf-16-be", "surrogatepass")
    return data.decode("latin-1")


@dataclass(frozen=True)
class _Element:
    name: str
    doctype: str | None
    prolog: bool


def _first_element(text: str) -> _Element | None:
    """The first markup token, with a prolog, doctype or comment skipped."""
    rest = text.removeprefix("﻿").lstrip(SPACE)
    doctype: str | None = None
    prolog = False
    for _ in range(_STEPS):
        if rest.startswith("<?"):
            end = rest.find("?>")
            if end < 0:
                return None
            prolog = prolog or _PROLOG.match(rest) is not None
            rest = rest[end + 2 :].lstrip(SPACE)
        elif rest.startswith("<!--"):
            end = rest.find("-->")
            if end < 0:
                return None
            rest = rest[end + 3 :].lstrip(SPACE)
        elif _DOCTYPE.match(rest):
            # A doctype may carry a bracketed internal subset before its closing >.
            end = declaration_end(rest, 0)
            if end < 0:
                return None
            doctype = rest[: end + 1]
            rest = rest[end + 1 :].lstrip(SPACE)
        else:
            break
    found = _ROOT.match(rest)
    return _Element(found.group(1), doctype, prolog) if found else None


def _signature(data: bytes) -> Classification | None:
    """The class a signature in the first bytes decides, or `None` when none is there."""
    if not data:
        return Classification("unknown", "application/octet-stream", "zero bytes", "empty object")
    if data.startswith(b"%PDF-"):
        return Classification("pdf", "application/pdf", "%PDF- signature")
    if data.startswith(b"\x1f\x8b"):
        return Classification("container", "application/gzip", "gzip signature")
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        office = detect_office(data)
        if not office:
            return Classification("container", "application/zip", "zip signature")
        return Classification(
            "office",
            OFFICE_MEDIA_TYPES[office.kind],
            f"{office.kind} package, {office.evidence}",
            None if office.kind in NORMALISED_KINDS else unsupported_reason(office.kind),
        )
    if data[_TAR_AT : _TAR_AT + 5] == b"ustar" and len(data) >= _TAR_BLOCK:
        return Classification("container", "application/x-tar", "ustar magic at offset 257")
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return Classification(
            "unknown",
            "application/x-ole-storage",
            "ole2 compound file signature",
            "legacy office binary, no normaliser",
        )
    if data.startswith((b"BZh", b"\xfd7zXZ", b"\x28\xb5\x2f\xfd")):
        return Classification(
            "unknown",
            "application/octet-stream",
            "compressed container that is not gzip, zip or tar",
            "unsupported compression",
        )
    if data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8")):
        return Classification(
            "unknown", "image/unknown", "raster image signature", "raster image, no text layer"
        )
    return None


def classify(data: bytes, media_type_hint: str | None = None) -> Classification:
    """The class of a run of bytes.

    The hint is the served Content-Type where one exists. It decides nothing on its own:
    magic bytes and the first markup token override it, and it only separates text
    subclasses that look identical.
    """
    signed = _signature(data)
    if signed:
        return signed
    encoding = encoding_of(data)
    if not encoding:
        return Classification(
            "unknown",
            "application/octet-stream",
            "not decodable as text and no known signature",
            "binary, no normaliser",
        )
    hint = lower(trim((media_type_hint or "").split(";")[0]))
    text_type = hint if hint.startswith("text/") else None
    # The first 4,096 UTF-16 units, which is at most that many characters.
    element = _first_element(slice_units(decode(data, encoding)[:_HEAD], _HEAD))
    if not element:
        if hint == "text/html":
            return Classification(
                "html", "text/html", "served as text/html, no markup root", encoding=encoding
            )
        return Classification(
            "text", text_type or "text/plain", f"decodes as {encoding}, no markup root", encoding=encoding
        )
    name = lower(element.name)
    if name == "html" or hint == "text/html" or _HTML_DOCTYPE.match(lower(element.doctype or "")):
        return Classification("html", "text/html", f"root element {element.name}", encoding=encoding)
    if not element.prolog and not element.doctype and name in _HTML_ELEMENTS:
        return Classification(
            "text",
            text_type or "text/markdown",
            f"opens with the html element {element.name} and is not a document",
            encoding=encoding,
        )
    how = ", xml prolog" if element.prolog else ", doctype present" if element.doctype else ""
    return Classification("xml", "application/xml", f"root element {element.name}{how}", encoding=encoding)
