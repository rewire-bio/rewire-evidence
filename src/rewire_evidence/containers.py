"""Containers: gzip, tar and zip expanded to their members.

This is `shared/archive/layers/container.ts`. A container is not a text layer and never gets
one. It produces one member per file it holds, keyed on that member's own SHA-256, and each
member is then classified in its own right.

The readers are the TypeScript's, which are deliberately narrow: no zip64, two compression
methods, and a tar reader that knows regular files and the GNU long name. Anything they do
not understand is a line in `problems`, never a guess, and a container with problems still
returns the members it could read.

A problem that quotes zlib is said here in this library's words after the colon, because
the words are zlib's as Node relays them and no committed file holds one: `problems` reaches
no registry row. Everything else is said in the TypeScript's words.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from typing import Final

from rewire_evidence.archive import ArchiveError, gunzip
from rewire_evidence.canonical import sha256_hex
from rewire_evidence.js import DOT, trim

_BLOCK: Final = 512
_GZIP: Final = b"\x1f\x8b"
_GZIP_HEADER: Final = 10
_GZIP_EXTRA: Final = 0x04
_GZIP_NAME: Final = 0x08
_END_OF_DIRECTORY: Final = b"PK\x05\x06"
_END_LENGTH: Final = 22
_LONGEST_COMMENT: Final = 65557
_ENTRY: Final = b"PK\x01\x02"
_ENTRY_LENGTH: Final = 46
_LOCAL: Final = b"PK\x03\x04"
_LOCAL_LENGTH: Final = 30
_ZIP64: Final = 0xFFFFFFFF
_STORED: Final = 0
_DEFLATED: Final = 8
#: `/\0.*$/`: a NUL and everything after it, where nothing after it ends a line.
_FROM_NUL: Final = re.compile(rf"\x00{DOT}*\Z")
_OCTAL: Final = re.compile(r"[+-]?[0-7]+")
_USTAR_AT: Final = 257


@dataclass(frozen=True)
class Member:
    """One file a container holds."""

    #: The member's path inside the container, as the container records it.
    path: str
    content: bytes
    sha256: str


@dataclass
class Expansion:
    """What a container expanded to, and what could not be read, one line each."""

    kind: str
    members: list[Member] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Limits:
    """How much a container may expand to."""

    max_members: int = 1024
    max_member_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024


DEFAULT_LIMITS: Final = Limits()


def _member(path: str, content: bytes) -> Member:
    return Member(path, content, sha256_hex(content))


def _u16(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 2], "little")


def _u32(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 4], "little")


def _text(data: bytes) -> str:
    """`buffer.toString("utf8")`: U+FFFD for each run of bytes that is not UTF-8."""
    return data.decode("utf-8", "replace")


def gzip_name(data: bytes) -> str:
    """The name a gzip header carries, or no text when it carries none."""
    if len(data) < _GZIP_HEADER or not data.startswith(_GZIP):
        return ""
    flags = data[3]
    if not flags & _GZIP_NAME:
        return ""
    at = _GZIP_HEADER
    if flags & _GZIP_EXTRA:
        if at + 2 > len(data):
            return ""
        at += 2 + _u16(data, at)
    end = data.find(b"\x00", at)
    return "" if end < 0 else data[at:end].decode("latin-1")


def _expand_gzip(data: bytes, limits: Limits) -> Expansion:
    try:
        content = gunzip(data, limits.max_member_bytes)
    except ArchiveError as error:
        return Expansion("gzip", problems=[f"gzip expansion failed: {error}"])
    # gzip -n records no name, so a nameless member is called content.
    return Expansion("gzip", members=[_member(gzip_name(data) or "content", content)])


def until_nul(text: str) -> str:
    r"""`text.replace(/\0.*$/, "")`: the text up to a NUL that nothing after ends a line."""
    return _FROM_NUL.sub("", text, count=1)


def _octal(data: bytes) -> int:
    """A tar size field: the octal number it opens with, 0 for no text, and -1 for anything else."""
    text = trim(until_nul(data.decode("latin-1")))
    if not text:
        return 0
    digits = _OCTAL.match(text)
    value = int(digits.group(), 8) if digits else -1
    return value if value >= 0 else -1


def _expand_tar(data: bytes, limits: Limits) -> Expansion:
    out = Expansion("tar")
    total = at = 0
    long_name: str | None = None
    while at + _BLOCK <= len(data):
        header = data[at : at + _BLOCK]
        if not any(header):
            break
        raw_name = until_nul(_text(header[:100]))
        prefix = until_nul(_text(header[345:500]))
        size = _octal(header[124:136])
        kind = chr(header[156] or 0x30)
        if size < 0:
            out.problems.append(f"tar header at offset {at} has an unreadable size field")
            break
        start = at + _BLOCK
        end = start + size
        if end > len(data):
            out.problems.append(f"tar member {raw_name or '(unnamed)'} runs past the end of the archive")
            break
        at = start + -(-size // _BLOCK) * _BLOCK
        if kind == "L":
            long_name = until_nul(_text(data[start:end]))
            continue
        name = long_name if long_name is not None else (f"{prefix}/{raw_name}" if prefix else raw_name)
        long_name = None
        if kind != "0":
            continue
        if len(out.members) >= limits.max_members:
            out.problems.append(f"tar holds more than {limits.max_members} members; the rest were not read")
            break
        if size > limits.max_member_bytes:
            out.problems.append(
                f"tar member {name} is {size} bytes, over the {limits.max_member_bytes} byte member limit"
            )
            continue
        total += size
        if total > limits.max_total_bytes:
            out.problems.append(
                f"tar expands past the {limits.max_total_bytes} byte total limit at member {name}"
            )
            break
        out.members.append(_member(name, data[start:end]))
    return out


def _end_of_central_directory(data: bytes) -> int:
    """Where the end of central directory record starts, searched from the end, or -1."""
    last = len(data) - _END_LENGTH
    if last < 0:
        return -1
    return data.rfind(_END_OF_DIRECTORY, max(0, last - _LONGEST_COMMENT), last + len(_END_OF_DIRECTORY))


def zip_entry_names(data: bytes, limit: int = 64) -> list[str]:
    """The member names in a zip's central directory, without expanding a byte.

    No names means the directory could not be read.
    """
    names: list[str] = []
    end = _end_of_central_directory(data)
    if end < 0:
        return names
    at = _u32(data, end + 16)
    if at == _ZIP64:
        return names
    for _ in range(min(_u16(data, end + 10), limit)):
        if at + _ENTRY_LENGTH > len(data) or data[at : at + 4] != _ENTRY:
            break
        length = _u16(data, at + 28)
        names.append(_text(data[at + _ENTRY_LENGTH : at + _ENTRY_LENGTH + length]))
        at += _ENTRY_LENGTH + length + _u16(data, at + 30) + _u16(data, at + 32)
    return names


def inflate_raw(raw: bytes, limit: int) -> bytes:
    """`zlib.inflateRawSync(raw, { maxOutputLength })`, or `ArchiveError` where it throws."""
    stream = zlib.decompressobj(wbits=-zlib.MAX_WBITS)
    try:
        content = stream.decompress(raw, limit + 1)
    except zlib.error as error:
        raise ArchiveError(str(error)) from None
    if not stream.eof or len(content) > limit:
        raise ArchiveError("it ends early, or expands to more than is allowed")
    return content


def _expand_zip(data: bytes, limits: Limits) -> Expansion:  # noqa: C901, PLR0912, PLR0915 - one refusal to a branch
    out = Expansion("zip")
    end = _end_of_central_directory(data)
    if end < 0:
        out.problems.append("no zip end of central directory record")
        return out
    at = _u32(data, end + 16)
    if at == _ZIP64:
        out.problems.append("zip64 central directory, no reader")
        return out
    total = 0
    for index in range(_u16(data, end + 10)):
        if at + _ENTRY_LENGTH > len(data) or data[at : at + 4] != _ENTRY:
            out.problems.append(f"zip central directory entry {index} is not where the record says")
            break
        method = _u16(data, at + 10)
        compressed = _u32(data, at + 20)
        uncompressed = _u32(data, at + 24)
        name_length = _u16(data, at + 28)
        local_at = _u32(data, at + 42)
        name = _text(data[at + _ENTRY_LENGTH : at + _ENTRY_LENGTH + name_length])
        at += _ENTRY_LENGTH + name_length + _u16(data, at + 30) + _u16(data, at + 32)
        if name.endswith("/"):
            continue
        if _ZIP64 in {compressed, uncompressed, local_at}:
            out.problems.append(f"zip member {name} uses zip64 fields, no reader")
            continue
        if len(out.members) >= limits.max_members:
            out.problems.append(f"zip holds more than {limits.max_members} members; the rest were not read")
            break
        if uncompressed > limits.max_member_bytes:
            out.problems.append(
                f"zip member {name} is {uncompressed} bytes, "
                f"over the {limits.max_member_bytes} byte member limit"
            )
            continue
        if local_at + _LOCAL_LENGTH > len(data) or data[local_at : local_at + 4] != _LOCAL:
            out.problems.append(f"zip member {name} has no local header at the recorded offset")
            continue
        data_at = local_at + _LOCAL_LENGTH + _u16(data, local_at + 26) + _u16(data, local_at + 28)
        raw = data[data_at : data_at + compressed]
        if len(raw) != compressed:
            out.problems.append(f"zip member {name} is truncated")
            continue
        if method == _STORED:
            content = raw
        elif method == _DEFLATED:
            try:
                content = inflate_raw(raw, limits.max_member_bytes)
            except ArchiveError as error:
                out.problems.append(f"zip member {name} did not expand: {error}")
                continue
        else:
            out.problems.append(f"zip member {name} uses compression method {method}, no reader")
            continue
        total += len(content)
        if total > limits.max_total_bytes:
            out.problems.append(
                f"zip expands past the {limits.max_total_bytes} byte total limit at member {name}"
            )
            break
        out.members.append(_member(name, content))
    return out


def is_tar(data: bytes) -> bool:
    """Whether the bytes carry the ustar magic of a tar archive at least one block long."""
    return len(data) >= _BLOCK and data[_USTAR_AT : _USTAR_AT + 5] == b"ustar"


def expand_container(data: bytes, limits: Limits = DEFAULT_LIMITS) -> Expansion:
    """Expand a container into its members.

    The caller has already classified the bytes as a container. Anything else raises
    `ArchiveError`, because guessing at a format is how a layer stops being reproducible.
    """
    if data.startswith(_GZIP):
        return _expand_gzip(data, limits)
    if len(data) >= len(_LOCAL) and data.startswith(b"PK"):
        return _expand_zip(data, limits)
    if is_tar(data):
        return _expand_tar(data, limits)
    raise ArchiveError("expandContainer was given bytes that are not gzip, zip or tar")
