"""What JavaScript does with a value `JSON.parse` returned, where the validators rely on it.

The TypeScript validators are written in JavaScript's terms: a value is used if it is
"truthy", text is trimmed of JavaScript's white space, anything can be compared with a
number, and a member that is absent is `undefined` and still has a printed form. Each of
those is a rule Python does not share, so each is written out here once. `canonical.py`
holds the forms a value is written in. This holds what a value counts as.

Everything here is held to recordings from Node: `tests/fixtures/js-semantics.json` for the
built-ins, and the zod and validator fixtures for the places they are used.

Lowercase is data. `toLowerCase` follows the Unicode version Node was built with and
`str.lower` the one Python was built with, and they differ on every letter added between
the two. `data/lowercase.json` is what the Node the data repository runs on does to every
code point, recorded in CI by `scripts/fixtures/record_lowercase.mjs`, and `lower` reads it.
"""

from __future__ import annotations

import enum
import functools
import json
import math
import re
from collections.abc import Iterable, Sequence
from fractions import Fraction
from importlib import resources
from typing import cast

from rewire_evidence.canonical import JsonValue, js_number, js_string


class Missing(enum.Enum):
    """A member that is not there, which JavaScript calls `undefined`."""

    MISSING = enum.auto()


MISSING = Missing.MISSING
#: A JSON value, or the absence of one.
type Held = JsonValue | Missing

#: What `String.prototype.trim` removes and `\s` matches: JavaScript's white space and line
#: terminators. Python's `str.strip()` and `\s` take U+001C to U+001F and U+0085 as well,
#: and leave U+FEFF.
SPACE = (
    "\t\n\v\f\r "
    "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
#: The same characters as a class body for a pattern, so `[{SPACE_CLASS}]` is `\s`.
SPACE_CLASS = r"\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
#: JavaScript's `.` without the `s` flag, which stops at four line terminators where
#: Python's stops at one.
DOT = r"[^\n\r\u2028\u2029]"

_DECIMAL = re.compile(r"[+-]?(?:Infinity|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)")
#: `toFixed` writes a number this large as `String` does.
_FIXED_BELOW = 1e21
_RADIX = re.compile(r"0[xX][0-9a-fA-F]+|0[oO][0-7]+|0[bB][01]+")


def received(value: Held) -> str:
    """The name zod gives the type of a value in an `invalid_type` message."""
    if isinstance(value, Missing):
        return "undefined"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, float) and math.isinf(value):
        # A number beyond every double, which JSON.parse reads as infinity and zod names.
        return "Infinity" if value > 0 else "-Infinity"
    return "number"


def trim(text: str) -> str:
    """`text.trim()`."""
    return text.strip(SPACE)


def truthy(value: object) -> bool:
    """Whether JavaScript takes the value as true in a condition.

    Nothing, null, false, zero and empty text are false. Everything else is true, an empty
    list and an empty object included, which is where Python differs.
    """
    if isinstance(value, Missing) or value is None:
        return False
    if isinstance(value, (bool, int, float, str)):
        return bool(value)
    return True


def joined(items: list[JsonValue]) -> str:
    """`String(items)` for a list `JSON.parse` returned, which is `items.join(",")`."""
    return join(items, ",")


def join(items: Sequence[Held], separator: str) -> str:
    """`items.join(separator)`: nothing for null or for a member that is absent."""
    return separator.join(_element(item) for item in items)


def _element(item: Held) -> str:
    """One element as `Array.prototype.join` writes it: nothing for null, `String(item)` otherwise."""
    if item is None or isinstance(item, Missing):
        return ""
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, str):
        return item
    if isinstance(item, list):
        return joined(item)
    if isinstance(item, dict):
        return "[object Object]"
    if math.isinf(item):
        return "Infinity" if item > 0 else "-Infinity"
    return js_number(item)


def number_of_text(text: str) -> float:
    """`Number(text)`: the number JavaScript reads from text, or NaN."""
    trimmed = trim(text)
    if not trimmed:
        return 0.0
    if _RADIX.fullmatch(trimmed):
        try:
            return float(int(trimmed, 0))
        except OverflowError:
            return math.inf
    if _DECIMAL.fullmatch(trimmed):
        return float(trimmed.replace("Infinity", "inf"))
    return math.nan


_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_HEX = 16


def parse_int(text: str, radix: int = 10) -> float:
    """`Number.parseInt(text, radix)` for a radix of 2 to 36: a number, NaN or an infinity.

    JavaScript reads the longest run of digits the text opens with, after any white space and
    one sign, and after `0x` when the radix is 16. `int` refuses text with anything after the
    digits. A run too long for a double is an infinity, which `Number.isInteger` refuses.

    `int` also refuses a run of more than 4,300 digits in a radix that is no power of two,
    whatever its value, so the zeros a number opens with are dropped before it reads the
    rest. What is then still too long to read is past any double.
    """
    rest = text.lstrip(SPACE)
    negative = rest.startswith("-")
    if rest[:1] in {"+", "-"}:
        rest = rest[1:]
    if radix == _HEX and rest[:2] in {"0x", "0X"}:
        rest = rest[2:]
    allowed = _DIGITS[:radix]
    length = 0
    while length < len(rest) and rest[length].lower() in allowed and rest[length].isascii():
        length += 1
    if not length:
        return math.nan
    try:
        value = float(int(rest[:length].lstrip("0") or "0", radix))
    except (OverflowError, ValueError):
        # More digits than a double holds, or than `int` will read at once.
        value = math.inf
    return -value if negative else value


def number_of(value: JsonValue) -> float:
    """`Number(value)` for a value `JSON.parse` returned, as a comparison converts it."""
    if value is None:
        return 0.0
    if isinstance(value, (bool, int, float)):
        return float(value)
    if isinstance(value, dict):
        return math.nan
    return number_of_text(value if isinstance(value, str) else joined(value))


def member(value: JsonValue, name: str) -> Held:
    """`value.name` for a member no built-in type has: the value's own, or nothing.

    Text, a number, a boolean and a list have members of their own, such as `length` and
    `split`, and a caller that asks for one of those names has to say what it is. Raises
    `TypeError` for null, which has no members, as JavaScript does.
    """
    if value is None:
        raise TypeError(f"Cannot read properties of null (reading '{name}')")
    return value.get(name, MISSING) if isinstance(value, dict) else MISSING


def distinct(values: Iterable[Held]) -> int:
    """`new Set(values).size`: how many of the values a JavaScript `Set` keeps apart.

    Text, numbers, booleans, null and nothing at all are one value each however often they
    come, and a boolean is never a number. A list or an object is itself and no other, so
    two that read alike count twice.
    """
    seen: set[tuple[str, object]] = set()
    for value in values:
        if isinstance(value, (dict, list)):
            seen.add(("object", id(value)))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            seen.add(("number", float(value)))
        else:
            seen.add((type(value).__name__, value))
    return len(seen)


class _Case:
    """What `data/lowercase.json` holds, as `lower` asks it."""

    def __init__(self) -> None:
        text = resources.files("rewire_evidence").joinpath("data/lowercase.json").read_text("utf-8")
        held = json.loads(text)
        self.lower: dict[int, str] = {
            int(code, 16): "".join(chr(int(each, 16)) for each in becomes.split(" "))
            for code, becomes in held["lower"]
        }
        self.cased = _code_points(held["cased"])
        self.ignorable = _code_points(held["case_ignorable"])

    def final(self, text: str, at: int) -> bool:
        """Whether the capital sigma at `at` ends a word, which is Unicode's Final_Sigma.

        A cased letter comes before it and none comes after, passing over case-ignorable
        characters on both sides. A character that is both cased and case-ignorable is
        passed over, as ICU passes over it.
        """
        return self._cased_next(reversed(text[:at])) and not self._cased_next(text[at + 1 :])

    def _cased_next(self, characters: Iterable[str]) -> bool:
        for character in characters:
            if ord(character) not in self.ignorable:
                return ord(character) in self.cased
        return False


def _code_points(ranges: list[str]) -> frozenset[int]:
    found: set[int] = set()
    for held in ranges:
        first, last = held.split("-")
        found.update(range(int(first, 16), int(last, 16) + 1))
    return frozenset(found)


_case = functools.cache(_Case)
_SIGMA = "\u03a3"
_FINAL_SIGMA = "\u03c2"


def lower(text: str) -> str:
    """`text.toLowerCase()`, by the Unicode version the recorded Node was built with.

    Each character becomes what the recording says, which is one character or more: a
    dotted capital I becomes an `i` and a combining dot. The one rule that looks at the
    neighbours is the capital sigma, which becomes a final sigma at the end of a word.
    """
    if text.isascii():
        return text.lower()
    case = _case()
    if _SIGMA not in text:
        return text.translate(case.lower)
    return "".join(
        _FINAL_SIGMA
        if character == _SIGMA and case.final(text, at)
        else case.lower.get(ord(character), character)
        for at, character in enumerate(text)
    )


def text_of(value: object) -> str:
    """`String(value)`, as a template writes a value into a message: `undefined` for nothing.

    `value` is a JSON value or `MISSING`. Raises `TypeError` where JavaScript does, for an
    object with a member named `toString`.
    """
    return "undefined" if isinstance(value, Missing) else js_string(cast("JsonValue", value))


def units(text: str) -> int:
    """`text.length`: the number of UTF-16 units, so two for a character beyond U+FFFF."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def pad_end(text: str, width: int) -> str:
    """`text.padEnd(width)`, which counts UTF-16 units: a character beyond U+FFFF takes two.

    `str.ljust` counts characters, and would pad such a name one space wider.
    """
    return text + " " * (width - units(text))


def is_integer(value: JsonValue) -> bool:
    """`Number.isInteger(value)`. A whole number too large for an `int` here is still one."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def slice_units(text: str, end: int) -> str:
    """`text.slice(0, end)`, which counts UTF-16 units and can cut a character in half."""
    return text.encode("utf-16-le", "surrogatepass")[: end * 2].decode("utf-16-le", "surrogatepass")


def strictly_equal(left: object, right: object) -> bool:
    """`left === right` for two values `JSON.parse` returned, or the absence of one.

    Text, numbers, booleans and null are equal by value, and a boolean is never a number.
    A list or an object is equal only to itself, however alike another one is.
    """
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return left is right
    return type(left) is type(right) and left == right


def unit_text(text: str) -> str:
    """The text with one character for each UTF-16 unit, which is what a JavaScript pattern sees.

    A pattern without the `u` flag counts a character beyond U+FFFF as two, so `.{0,60}`
    spans thirty of them. Matching against this form counts as JavaScript counts.
    """
    if text.isascii():
        return text
    data = text.encode("utf-16-le", "surrogatepass")
    return "".join(chr(int.from_bytes(data[at : at + 2], "little")) for at in range(0, len(data), 2))


def paired(text: str) -> str:
    """Text in units as whole characters: each pair of surrogates becomes the character it spells.

    The inverse of `unit_text`. Half a character with no other half beside it stays as it is.
    """
    if text.isascii():
        return text
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def fixed(value: float, digits: int) -> str:
    """`value.toFixed(digits)`, for 0 to 100 digits.

    JavaScript takes the nearest number with that many decimals to the exact value of the
    double, and the larger of two that are equally near. Python's `%.1f` takes the even one,
    so it writes 0.25 as 0.2 where JavaScript writes 0.3. A number of 1e21 or more, and one
    that is not a number, is written as `String` writes it.
    """
    if not abs(value) < _FIXED_BELOW:
        return "NaN" if math.isnan(value) else js_string(value)
    scaled = abs(Fraction(value)) * 10**digits
    whole = str((2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)).rjust(
        digits + 1, "0"
    )
    sign = "-" if value < 0 else ""
    return f"{sign}{whole[:-digits]}.{whole[-digits:]}" if digits else f"{sign}{whole}"
