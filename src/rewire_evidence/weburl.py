"""Whether `new URL(text)` reads a piece of text, and the scheme it finds.

Two validators rest on JavaScript's URL parser. zod's `.url()` accepts whatever
`URL.canParse` accepts, and `validateRecords` builds `new URL(String(url))` for every source
and looks at its protocol. That parser is the one in the WHATWG URL standard, and it is not
the one in Python's `urllib`, which refuses almost nothing: `urlsplit("not a url")` succeeds.

This is the standard's basic URL parser with no base URL, followed state by state, keeping
only what decides whether parsing fails and what the scheme is. A path, a query and a
fragment cannot make it fail, so they are not read. What can: no scheme, credentials with
no host, a host the host parser refuses, and a port that is not a number up to 65535.

One part is not ported. The host of a special scheme with a character outside ASCII, or a
label starting `xn--`, goes through Unicode's IDNA mapping (UTS 46): a table of some
thousands of ranges that changes with each Unicode version, then normalisation and the
rules for joiners and for text that runs both ways, all by the Unicode version Node was
built with, which is not the one Python's `unicodedata` holds. `protocol_of` and `host_of`
raise `UndecidedHost` for such a host and do not guess. No URL in the store has one.

Everything else is answered: a name, an IPv4 address in any of its spellings, an IPv6
address, the host of a `file:` URL, and the host of a scheme that is not special.

`tests/fixtures/js-semantics.json` holds what Node answers for a list of inputs, and the
tests hold this module to it.
"""

from __future__ import annotations

import re
from typing import NamedTuple

#: Schemes the standard calls special: they have a host, and `\` is a path separator in them.
SPECIAL = frozenset({"ftp", "file", "http", "https", "ws", "wss"})

_EDGE = "".join(chr(code) for code in range(0x21))
_TAB_OR_NEWLINE = re.compile(r"[\t\n\r]")
_FORBIDDEN_HOST = frozenset("\x00\t\n\r #/:<>?@[\\]^|")
_FORBIDDEN_DOMAIN = _FORBIDDEN_HOST | frozenset(chr(code) for code in range(0x20)) | frozenset("%\x7f")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+\-.]*")
_ESCAPE = re.compile(rb"%([0-9A-Fa-f]{2})")
_DIGITS = re.compile(r"[0-9]+")
_DRIVE = re.compile(r"[A-Za-z][:|]")
_IPV4_IN_IPV6 = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}")
_SURROGATE = re.compile("[\ud800-\udfff]")
_HEX_PIECE = re.compile(r"[0-9A-Fa-f]{1,4}")
_MAX_PORT = 65535
_PIECES = 8
_MAX_IPV4_PARTS = 4
_BYTE = 255
#: Where an IPv4 address ends: no part of one may reach this, wherever it stands.
_IPV4_END = 256**4


class UndecidedHost(Exception):  # noqa: N818 - it is a refusal to answer, not an error in the text
    """A URL whose host needs Unicode's IDNA mapping to be judged, which is not ported."""


def _decimal(digits: str, most: int) -> int:
    """The number decimal digits write, or `most` + 1 for one with more digits than `most` has.

    A port and a part of an address are numbers however many digits write them, zeros in
    front included, and Python refuses to read an integer of more than 4,300 digits. Only
    whether the number is above a limit matters here, and its length settles that for any
    number long enough to be refused.
    """
    written = digits.lstrip("0")
    return most + 1 if len(written) > len(str(most)) else int(written or "0")


def _ipv4_number(part: str) -> int | None:
    """The standard's IPv4 number parser: decimal, `0x` hexadecimal, or octal after a `0`.

    A decimal number with more digits than an address can hold is answered as `_IPV4_END`,
    which is refused wherever it stands.
    """
    if not part:
        return None
    digits, radix, alphabet = part, 10, "0123456789"
    if len(part) >= 2 and part[:2] in {"0x", "0X"}:  # noqa: PLR2004 - a two-character prefix
        digits, radix, alphabet = part[2:], 16, "0123456789abcdefABCDEF"
    elif len(part) >= 2 and part[0] == "0":  # noqa: PLR2004 - a zero and at least one more
        digits, radix, alphabet = part[1:], 8, "01234567"
    if any(digit not in alphabet for digit in digits):
        return None
    if radix == 10:  # noqa: PLR2004 - decimal
        return _decimal(digits, _IPV4_END - 1)
    # Python reads a power-of-two radix at any length.
    return int(digits, radix) if digits else 0


def _ends_in_a_number(domain: str) -> bool:
    parts = domain.split(".")
    # A domain is never empty here, so a last part that is empty follows a dot.
    last = parts[-1] or parts[-2]
    return bool(_DIGITS.fullmatch(last)) or _ipv4_number(last) is not None


def _ipv4_ok(domain: str) -> bool:
    parts = domain.split(".")
    if not parts[-1] and len(parts) > 1:
        parts.pop()
    if len(parts) > _MAX_IPV4_PARTS:
        return False
    numbers = [_ipv4_number(part) for part in parts]
    if any(number is None for number in numbers):
        return False
    held = [number for number in numbers if number is not None]
    return all(number <= _BYTE for number in held[:-1]) and held[-1] < 256 ** (5 - len(held))


def _ipv6(address: str) -> list[int] | None:  # noqa: C901, PLR0912 - the standard's parser, step for step
    """The standard's IPv6 parser: the eight pieces of an address, or None where it fails."""
    held: list[int] = []
    # Where the run of zeros `::` stands for begins, once one has been read.
    compress: int | None = None
    rest = address
    if rest.startswith(":"):
        if not rest.startswith("::"):
            return None
        rest = rest[2:]
        held.append(0)
        compress = 1
    while rest:
        if len(held) == _PIECES:
            return None
        if rest[0] == ":":
            if compress is not None:
                return None
            rest = rest[1:]
            held.append(0)
            compress = len(held)
            continue
        piece = _HEX_PIECE.match(rest)
        if piece is None:
            return None
        after = rest[piece.end() :]
        if after.startswith("."):
            # The last 32 bits written as an IPv4 address, which takes two pieces.
            if len(held) > _PIECES - 2 or not _IPV4_IN_IPV6.fullmatch(rest):
                return None
            numbers = [int(number) for number in rest.split(".")]
            if any(number > _BYTE for number in numbers):
                return None
            held.extend([numbers[0] << 8 | numbers[1], numbers[2] << 8 | numbers[3]])
            break
        if after.startswith(":"):
            after = after[1:]
            if not after:
                return None
        elif after:
            return None
        held.append(int(piece.group(), 16))
        rest = after
    if compress is None:
        return held if len(held) == _PIECES else None
    return [*held[:compress], *([0] * (_PIECES - len(held))), *held[compress:]]


def _ipv6_text(pieces: list[int]) -> str:
    """The standard's IPv6 serialiser: lowercase hexadecimal, with `::` for the first of the
    longest runs of two or more pieces that are zero.
    """
    start, length = -1, 1
    at = 0
    while at < _PIECES:
        end = at
        while end < _PIECES and pieces[end] == 0:
            end += 1
        if end - at > length:
            start, length = at, end - at
        at = end + 1
    if start < 0:
        return ":".join(f"{piece:x}" for piece in pieces)
    before = ":".join(f"{piece:x}" for piece in pieces[:start])
    after = ":".join(f"{piece:x}" for piece in pieces[start + length :])
    return f"{before}::{after}"


def _domain(host: str) -> str:
    """The host of a special scheme with its percent escapes read, which is the first step."""
    decoded = _ESCAPE.sub(
        lambda found: bytes([int(found.group(1), 16)]), host.encode("utf-8", "surrogatepass")
    )
    return decoded.decode("utf-8", "replace")


def _host_ok(host: str, *, special: bool) -> bool:
    """The standard's host parser, as far as whether it fails."""
    if host.startswith("["):
        return host.endswith("]") and _ipv6(host[1:-1]) is not None
    if not special:
        return not any(character in _FORBIDDEN_HOST for character in host)
    domain = _domain(host)
    if not domain.isascii() or any(label[:4].lower() == "xn--" for label in domain.split(".")):
        raise UndecidedHost(f"the host {host!r} needs IDNA mapping, which is not ported")
    if not domain or any(character in _FORBIDDEN_DOMAIN for character in domain):
        return False
    return _ipv4_ok(domain) if _ends_in_a_number(domain) else True


def _authority_ok(rest: str, *, special: bool) -> bool:
    """The authority, host and port states: `rest` is what follows the slashes."""
    found = _authority(rest, special=special)
    return found is not None and _host_ok(found[0], special=special)


def _authority(rest: str, *, special: bool) -> tuple[str, str] | None:
    """The host and the port as they are written, or None where one is refused unread."""
    ends = "/?#\\" if special else "/?#"
    end = next((index for index, character in enumerate(rest) if character in ends), len(rest))
    authority = rest[:end]
    credentials, at, host_and_port = authority.rpartition("@")
    if at and not host_and_port:
        return None
    del credentials
    # The first colon outside brackets ends the host and starts the port.
    inside = False
    split = len(host_and_port)
    for index, character in enumerate(host_and_port):
        if character == "[":
            inside = True
        elif character == "]":
            inside = False
        elif character == ":" and not inside:
            split = index
            break
    host, port = host_and_port[:split], host_and_port[split + 1 :]
    if split < len(host_and_port):
        if not host:
            return None
        if port and (not port.isascii() or not port.isdigit() or _decimal(port, _MAX_PORT) > _MAX_PORT):
            return None
    elif special and not host:
        return None
    return host, port


def _file_host(rest: str) -> str | None:
    """The file, file slash and file host states: `rest` is what follows `file:`.

    The host as it is written, nothing where the URL has none, and None where the host
    parser refuses the one it has. A drive letter where a host would stand is a path.
    """
    if len(rest) < 2 or rest[0] not in "/\\" or rest[1] not in "/\\":  # noqa: PLR2004 - two slashes
        return ""
    after = rest[2:]
    end = next((index for index, character in enumerate(after) if character in "/\\?#"), len(after))
    host = after[:end]
    if not host or _DRIVE.fullmatch(host):
        return ""
    return host if _host_ok(host, special=True) else None


def protocol_of(text: str) -> str | None:
    """`new URL(text).protocol`, or None where `new URL(text)` throws.

    Raises `UndecidedHost` for a host that only IDNA mapping can judge.
    """
    stripped = _TAB_OR_NEWLINE.sub("", text.strip(_EDGE))
    found = _SCHEME.match(stripped)
    if found is None or not stripped.startswith(":", found.end()):
        return None
    scheme = found.group().lower()
    rest = stripped[found.end() + 1 :]
    if scheme == "file":
        accepted = _file_host(rest) is not None
    elif scheme in SPECIAL:
        accepted = _authority_ok(rest.lstrip("/\\"), special=True)
    elif rest.startswith("//"):
        accepted = _authority_ok(rest[2:], special=False)
    else:
        accepted = True
    return f"{scheme}:" if accepted else None


def can_parse(text: str) -> bool:
    """`URL.canParse(text)`."""
    return protocol_of(text) is not None


class Host(NamedTuple):
    """Where a URL points: `host` has the port when it is not the scheme's own."""

    host: str
    hostname: str


#: The port each special scheme has when none is written, which `host` leaves out.
_DEFAULT_PORT = {"ftp": 21, "http": 80, "https": 443, "ws": 80, "wss": 443}


def _ipv4_text(domain: str) -> str:
    """An address the IPv4 parser accepted, written as four decimal numbers."""
    parts = domain.split(".")
    numbers = [_ipv4_number(part) or 0 for part in (parts if parts[-1] else parts[:-1])]
    address = numbers[-1] + sum(number << 8 * (3 - at) for at, number in enumerate(numbers[:-1]))
    return ".".join(str(address >> shift & _BYTE) for shift in (24, 16, 8, 0))


def _host_text(host: str, *, special: bool) -> str:
    """A host the host parser accepted, as the standard serialises it, in lowercase."""
    if host.startswith("["):
        # The host parser accepted it, so it has pieces.
        return f"[{_ipv6_text(_ipv6(host[1:-1]) or [])}]"
    if not special:
        # An opaque host keeps what was written, with a control or a character beyond `~` as
        # the percent escapes of its UTF-8. Half of a surrogate pair is U+FFFD to the parser.
        whole = _SURROGATE.sub("\ufffd", host)
        return "".join(
            character if " " < character < "\x7f" else "".join(f"%{byte:02X}" for byte in character.encode())
            for character in whole
        ).lower()
    domain = _domain(host)
    return _ipv4_text(domain) if _ends_in_a_number(domain) else domain.lower()


def host_of(text: str) -> Host | None:
    """`new URL(text)`'s `host` and `hostname` in lowercase, or None where `new URL` throws.

    Raises `UndecidedHost` for a host that only IDNA mapping can judge, and for nothing
    else. No URL in the store has one.
    """
    stripped = _TAB_OR_NEWLINE.sub("", text.strip(_EDGE))
    found = _SCHEME.match(stripped)
    if found is None or not stripped.startswith(":", found.end()):
        return None
    scheme = found.group().lower()
    rest = stripped[found.end() + 1 :]
    if scheme == "file":
        # A file URL has no port, and `localhost` is no host at all.
        written = _file_host(rest)
        if written is None:
            return None
        name = _host_text(written, special=True) if written else ""
        return Host("", "") if name == "localhost" else Host(name, name)
    special = scheme in SPECIAL
    if not special and not rest.startswith("//"):
        return Host("", "")
    authority = _authority(rest.lstrip("/\\") if special else rest[2:], special=special)
    if authority is None or not _host_ok(authority[0], special=special):
        return None
    name = _host_text(authority[0], special=special)
    port = _decimal(authority[1], _MAX_PORT) if authority[1] else None
    return Host(name if port in {None, _DEFAULT_PORT.get(scheme)} else f"{name}:{port}", name)
