"""Whether `new URL(text)` reads a piece of text, and the scheme it finds.

Two validators rest on JavaScript's URL parser. zod's `.url()` accepts whatever
`URL.canParse` accepts, and `validateRecords` builds `new URL(String(url))` for every source
and looks at its protocol. That parser is the one in the WHATWG URL standard, and it is not
the one in Python's `urllib`, which refuses almost nothing: `urlsplit("not a url")` succeeds.

This is the standard's basic URL parser with no base URL, followed state by state, keeping
only what decides whether parsing fails and what the scheme is. A path, a query and a
fragment cannot make it fail, so they are not read. What can: no scheme, credentials with
no host, a host the host parser refuses, and a port that is not a number up to 65535.

One part is not ported. A host with a character outside ASCII, or a label starting `xn--`,
goes through Unicode's IDNA mapping (UTS 46), which is a table of some thousands of ranges
that changes with each Unicode version. `protocol_of` raises `UndecidedHost` for such a host
and does not guess. No URL in the store has one.

`tests/fixtures/js-semantics.json` holds what Node answers for a list of inputs, and the
tests hold this module to it.
"""

from __future__ import annotations

import re

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
_HEX_PIECE = re.compile(r"[0-9A-Fa-f]{1,4}")
_MAX_PORT = 65535
_PIECES = 8
_MAX_IPV4_PARTS = 4
_BYTE = 255


class UndecidedHost(Exception):  # noqa: N818 - it is a refusal to answer, not an error in the text
    """A URL whose host needs Unicode's IDNA mapping to be judged, which is not ported."""


def _ipv4_number(part: str) -> int | None:
    """The standard's IPv4 number parser: decimal, `0x` hexadecimal, or octal after a `0`."""
    if not part:
        return None
    digits, radix, alphabet = part, 10, "0123456789"
    if len(part) >= 2 and part[:2] in {"0x", "0X"}:  # noqa: PLR2004 - a two-character prefix
        digits, radix, alphabet = part[2:], 16, "0123456789abcdefABCDEF"
    elif len(part) >= 2 and part[0] == "0":  # noqa: PLR2004 - a zero and at least one more
        digits, radix, alphabet = part[1:], 8, "01234567"
    if any(digit not in alphabet for digit in digits):
        return None
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


def _ipv6_ok(address: str) -> bool:  # noqa: C901, PLR0912 - the standard's parser, step for step
    """The standard's IPv6 parser, as far as whether it fails."""
    pieces = 0
    compressed = False
    rest = address
    if rest.startswith(":"):
        if not rest.startswith("::"):
            return False
        rest = rest[2:]
        pieces = 1
        compressed = True
    while rest:
        if pieces == _PIECES:
            return False
        if rest[0] == ":":
            if compressed:
                return False
            rest = rest[1:]
            pieces += 1
            compressed = True
            continue
        piece = _HEX_PIECE.match(rest)
        if piece is None:
            return False
        after = rest[piece.end() :]
        if after.startswith("."):
            # The last 32 bits written as an IPv4 address, which takes two pieces.
            if pieces > _PIECES - 2 or not _IPV4_IN_IPV6.fullmatch(rest):
                return False
            if any(int(number) > _BYTE for number in rest.split(".")):
                return False
            pieces += 2
            rest = ""
            break
        if after.startswith(":"):
            after = after[1:]
            if not after:
                return False
        elif after:
            return False
        pieces += 1
        rest = after
    return compressed or pieces == _PIECES


def _host_ok(host: str, *, special: bool) -> bool:
    """The standard's host parser, as far as whether it fails."""
    if host.startswith("["):
        return host.endswith("]") and _ipv6_ok(host[1:-1])
    if not special:
        return not any(character in _FORBIDDEN_HOST for character in host)
    decoded = _ESCAPE.sub(
        lambda found: bytes([int(found.group(1), 16)]), host.encode("utf-8", "surrogatepass")
    )
    domain = decoded.decode("utf-8", "replace")
    if not domain.isascii() or any(label[:4].lower() == "xn--" for label in domain.split(".")):
        raise UndecidedHost(f"the host {host!r} needs IDNA mapping, which is not ported")
    if not domain or any(character in _FORBIDDEN_DOMAIN for character in domain):
        return False
    return _ipv4_ok(domain) if _ends_in_a_number(domain) else True


def _authority_ok(rest: str, *, special: bool) -> bool:
    """The authority, host and port states: `rest` is what follows the slashes."""
    ends = "/?#\\" if special else "/?#"
    end = next((index for index, character in enumerate(rest) if character in ends), len(rest))
    authority = rest[:end]
    credentials, at, host_and_port = authority.rpartition("@")
    if at and not host_and_port:
        return False
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
            return False
        if port and (not port.isascii() or not port.isdigit() or int(port) > _MAX_PORT):
            return False
    elif special and not host:
        return False
    return _host_ok(host, special=special)


def _file_ok(rest: str) -> bool:
    """The file, file slash and file host states: `rest` is what follows `file:`."""
    if len(rest) < 2 or rest[0] not in "/\\" or rest[1] not in "/\\":  # noqa: PLR2004 - two slashes
        return True
    after = rest[2:]
    end = next((index for index, character in enumerate(after) if character in "/\\?#"), len(after))
    host = after[:end]
    if not host or _DRIVE.fullmatch(host):
        return True
    return _host_ok(host, special=True)


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
        accepted = _file_ok(rest)
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
