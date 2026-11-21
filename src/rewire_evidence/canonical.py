"""Byte-exact reproduction of the serialisations the TypeScript store writes.

Nothing in this module is about Python's idea of good JSON. Every function here exists to
emit the same bytes a particular piece of TypeScript emits, because those bytes are hashed
and the hashes are committed. If a function here changes its output by one character, data
already in the store stops verifying.

Three separate serialisations, and they are not interchangeable:

  js_stringify     JSON.stringify: keys in insertion order, no spaces. This is what
                   `recordSha256` hashes (scripts/omics/records.ts:42-44), so it decides
                   whether every committed provenance digest still verifies.
  canonical_line   top-level keys sorted, everything below in insertion order. The object
                   manifest and the fetch receipts are written in this
                   (shared/archive/schema.ts:300).
  canonical_json   keys sorted at every depth, undefined members dropped. The grid layer
                   headers are written in this (shared/archive/layers/grid/index.ts:52), and
                   those bytes are inside a `layer_sha256`.

The trap, named here because it is a one-line mistake that produces plausible output:
`json.dumps(sort_keys=True)` is the recursive form. Using it where `canonical_line` belongs
writes valid, sorted, wrong bytes on any row holding a nested object, and the manifest holds
many. Do not call `sort_keys` anywhere but `canonical_json`.

Verified against the live store on 2026-11-20: 93,216 of 93,216 record digests reproduce.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

#: Anything `JSON.parse` can return. Objects are dicts, and their order is their identity.
type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
#: One line of a store file.
type JsonObject = dict[str, JsonValue]
#: What the serialisers accept: the parsed form, plus read-only views and tuples of it.
type JsonInput = bool | int | float | str | Sequence[JsonInput] | Mapping[str, JsonInput] | None

#: Shard count for the record store: one hex digit, so sixteen files per sharded kind.
SHARD_DIGITS = 1

#: `Number.MAX_SAFE_INTEGER + 1`. Every integer up to here is a double, and prints as itself.
EXACT_INTEGER_LIMIT = 2**53

_EXACT_INTEGER_DIGITS = len(str(EXACT_INTEGER_LIMIT))

#: The largest array index, 2**32 - 2. Keys that are canonical integers up to here are the
#: ones a JavaScript object lists first, in numeric order, whatever order they were set in.
MAX_ARRAY_INDEX = 2**32 - 2

#: `Number::toString` writes plain digits while the decimal point sits within these bounds,
#: and exponent form outside them: `0.000001` and `1e-7`, `100000000000000000000` and `1e+21`.
_PLAIN_FROM = -6
_PLAIN_TO = 21

_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]{0,9}")
_SURROGATE = re.compile("[\ud800-\udfff]")
_SHA256 = re.compile(r"[a-f0-9]{64}")


def js_number(value: float) -> str:
    """One number as `JSON.stringify` writes it, which is ECMAScript `Number::toString`.

    Python and JavaScript agree on the digits, because both print the shortest decimal that
    round-trips, and disagree on when to switch to exponent form and how to write it.
    JavaScript uses plain decimal across `1e-6 .. 1e21` and writes `0.00001`; Python's repr
    switches at `1e-4` and writes `1e-05`. That single difference was the only thing standing
    between a naive port and the whole store, and it showed up on exactly four values.
    """
    if math.isnan(value) or math.isinf(value):
        # JSON.stringify writes null for these. A caller that reaches here is building a
        # record it cannot serialise, which is a bug worth stopping on rather than encoding.
        raise ValueError(f"JSON.stringify writes null for {value!r}; it is not a number a record may hold")
    if value == 0:
        return "0"  # JavaScript prints -0 as 0.

    # There is deliberately no `int(value)` shortcut for integral values. It is right below
    # 2**53 and wrong above it: `1.2345678901234568e20` has the exact binary value
    # 123456789012345683968, which is what `int()` prints, where JavaScript prints the
    # shortest round-tripping digits padded with zeros, 123456789012345680000. The branches
    # below produce the padded form, so integral values need no special case at all.
    negative = value < 0
    shortest = Decimal(repr(abs(float(value))))
    # Trailing zeros are dropped from the digits, which changes how many there are but never
    # where the point is, so `point` comes from the position of the leading digit.
    significant = "".join(map(str, shortest.as_tuple().digits)).rstrip("0")
    count = len(significant)
    point = shortest.adjusted() + 1

    if count <= point <= _PLAIN_TO:
        out = significant + "0" * (point - count)
    elif 0 < point <= _PLAIN_TO:
        out = significant[:point] + "." + significant[point:]
    elif _PLAIN_FROM < point <= 0:
        out = "0." + "0" * (-point) + significant
    else:
        power = point - 1
        mantissa = significant if count == 1 else significant[0] + "." + significant[1:]
        out = f"{mantissa}e{'+' if power >= 0 else '-'}{abs(power)}"
    return "-" + out if negative else out


def _well_formed(text: str) -> str:
    """The string with any surrogate pair held as two code points joined into one character.

    A JavaScript string is UTF-16 units, so a high surrogate followed by a low one is one
    astral character there. A Python string can hold the same two as separate code points.
    Encoding to UTF-16 and back joins them and leaves a surrogate with no partner as it was.
    """
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def js_quote(text: str) -> str:
    """One string as `JSON.stringify` writes it: the same escapes, non-ASCII left alone.

    A surrogate with no partner is written as a lowercase escape, `d800` to `dfff` after a
    backslash and a `u`, which is what JavaScript has done since ES2019, so the output is
    always encodable as UTF-8.
    """
    quoted = json.dumps(_well_formed(text), ensure_ascii=False)
    return _SURROGATE.sub(lambda lone: f"\\u{ord(lone.group()):04x}", quoted)


def js_utf8(text: str) -> bytes:
    """The bytes Node writes or hashes for a string: UTF-8, with U+FFFD for a lone surrogate.

    Python refuses to encode a surrogate that has no partner. Node does not: `fs.writeFileSync`
    and `crypto.createHash().update()` both put U+FFFD in its place, so `shardOf` gives an ID
    holding one a shard, and so must this.
    """
    return _SURROGATE.sub("\ufffd", _well_formed(text)).encode("utf-8")


def utf16_key(text: str) -> bytes:
    """A sort key matching JavaScript's string comparison, which orders by UTF-16 unit.

    Big-endian UTF-16 compared byte by byte is the same order as its units compared as
    numbers, so the encoded bytes are the key.

    Python orders by code point. The two agree on everything below U+E000 and disagree on
    astral characters, which sort before U+E000..U+FFFF in UTF-16 and after them in code
    points. No key in the store reaches that range today, and relying on that would be the
    kind of assumption this module exists to avoid.
    """
    return text.encode("utf-16-be", "surrogatepass")


def js_key_order(keys: Iterable[object]) -> list[str]:
    """The order a JavaScript object lists these keys in, given the order they were set in.

    Keys that are array indices (`"0"`, `"7"`, `"42"`, up to 2**32 - 2, written without a
    leading zero) come first in ascending numeric order. Every other key follows in the
    order it was set. A Python dict has no such rule, so `{"b": 1, "1": 2}` has to be
    reordered before it is written, or it produces bytes no JavaScript object can.
    """
    indices: list[tuple[int, str]] = []
    named: list[str] = []
    for key in keys:
        if not isinstance(key, str):
            # str() would write True as "True" and 1.0 as "1.0" where JavaScript writes
            # "true" and "1", so a key that is not text is refused rather than guessed at.
            raise TypeError(f"an object key must be a string, not {type(key).__name__}")
        if _ARRAY_INDEX.fullmatch(key) and int(key) <= MAX_ARRAY_INDEX:
            indices.append((int(key), key))
        else:
            named.append(key)
    return [key for _, key in sorted(indices)] + named


def js_parse(text: str) -> JsonValue:
    """`JSON.parse(text)`, as far as it can be seen through `JSON.stringify`.

    Three places where Python's reader and JavaScript's disagree are closed here. An integer
    too large for a double to hold exactly becomes the nearest double, as every JavaScript
    number is one, and infinity if it is beyond them all. `NaN` and `Infinity` are syntax
    errors, as they are in JSON. And an object lists its array-index keys first, which
    `js_stringify` applies on the way out.
    """

    def integer(literal: str) -> int | float:
        # A literal with more digits than 2**53 has is past the limit whatever they are, and
        # is read as a double without being read as an integer first. Python refuses to
        # convert an integer of more than 4,300 digits, and JSON.parse has no such limit.
        if len(literal.lstrip("-")) > _EXACT_INTEGER_DIGITS:
            return float(literal)
        value = int(literal)
        return value if abs(value) <= EXACT_INTEGER_LIMIT else float(literal)

    def constant(name: str) -> JsonValue:
        raise ValueError(f"{name} is not JSON")

    parsed: JsonValue = json.loads(text, parse_int=integer, parse_constant=constant)
    return parsed


def js_value(value: object) -> JsonValue:
    """A value as JavaScript holds it once `JSON.parse` has read it: one form per value.

    Python has two numbers where JavaScript has one, and a dict keeps any key order where a
    JavaScript object lists its array-index keys first. So `1.0` and `1` are two Python
    values and one JavaScript value, and comparing parsed records with `==` would call an
    unchanged record changed. This puts every value in the one form:

    - a whole number a double holds exactly is an `int`, so `1.0` and `-0.0` become `1` and `0`;
    - any other number is the `float` nearest to it, so an integer above 2**53 becomes the
      double JavaScript would hold, and one beyond every double becomes infinity, as
      `JSON.parse` reads `1e400`;
    - an object's keys are in the order JavaScript lists them;
    - a tuple is a list.

    The result shares nothing with what it was given, and `js_stringify` writes it exactly
    as it writes the original. Raises `TypeError` for a value JSON has no form for, a key
    that is not text, or NaN, which no JSON text can produce.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if abs(value) <= EXACT_INTEGER_LIMIT:
            return value
        try:
            return float(value)
        except OverflowError:
            return math.inf if value > 0 else -math.inf
    if isinstance(value, float):
        if math.isnan(value):
            raise TypeError("NaN is not a value JSON can hold")
        return int(value) if value.is_integer() and abs(value) <= EXACT_INTEGER_LIMIT else value
    if isinstance(value, Mapping):
        return {key: js_value(value[key]) for key in js_key_order(value)}
    if isinstance(value, (list, tuple)):
        return [js_value(item) for item in value]
    raise TypeError(f"{type(value).__name__} has no JSON form")


def js_stringify(value: JsonInput) -> str:
    """`JSON.stringify(value)`: insertion order, no whitespace, JavaScript number format.

    This is the form a store file is written in. It refuses a number beyond every double,
    which JavaScript would write as `null`, so that a writer stops and does not store one.
    """
    return _stringify(value)


def js_written(value: JsonInput) -> str:
    """`JSON.stringify(value)` to the letter: `null` for a number beyond every double.

    For reading and never for writing a store file. `JSON.parse` reads `1e400` as infinity
    and `JSON.stringify` writes infinity as `null`, so the digest the TypeScript holds for
    such a record, and the text it compares two such values by, say `null`. A reader that
    is to agree with it has to take the digest and make the comparison over the same text.
    """
    return _stringify(value, beyond_as_null=True)


def js_indented(value: JsonInput, gap: str = "  ", margin: str = "") -> str:
    """`JSON.stringify(value, null, 2)`: a member or an item to a line, `null` for infinity.

    An empty list is `[]` and an empty object `{}`, on one line, where Python's `json`
    would agree, and a number is written as JavaScript writes it, where it would not.
    """
    inner = margin + gap
    if isinstance(value, Mapping):
        members = [f"{inner}{js_quote(k)}: {js_indented(value[k], gap, inner)}" for k in js_key_order(value)]
        return "{\n" + ",\n".join(members) + f"\n{margin}}}" if members else "{}"
    if isinstance(value, (list, tuple)):
        items = [f"{inner}{js_indented(item, gap, inner)}" for item in value]
        return "[\n" + ",\n".join(items) + f"\n{margin}]" if items else "[]"
    return _stringify(value, beyond_as_null=True)


def _stringify(value: object, *, beyond_as_null: bool = False) -> str:
    """The work of `js_stringify`, on a value whose type is checked here and not assumed."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return js_quote(value)
    if isinstance(value, int):  # bool is handled above.
        return _js_integer(value)
    if isinstance(value, float):
        return "null" if beyond_as_null and math.isinf(value) else js_number(value)
    if isinstance(value, Mapping):
        members = (
            f"{js_quote(k)}:{_stringify(value[k], beyond_as_null=beyond_as_null)}"
            for k in js_key_order(value)
        )
        return "{" + ",".join(members) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_stringify(item, beyond_as_null=beyond_as_null) for item in value) + "]"
    raise TypeError(f"{type(value).__name__} has no JSON.stringify form")


def js_string(value: JsonValue) -> str:
    """A JSON value as JavaScript makes text of it: `String(value)`, as a template does.

    Text is itself. A number is written as `JSON.stringify` writes it, and one beyond every
    double as `Infinity`. A list is its items joined by commas, with nothing for a null
    among them, and an object is `[object Object]`.

    Raises `TypeError`, as JavaScript does, for an object with a member named `toString`,
    alone or inside a list: the member is found where the function should be.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "toString" in value:
            raise TypeError("Cannot convert object to primitive value")
        return "[object Object]"
    if isinstance(value, list):
        return ",".join("" if item is None else js_string(item) for item in value)
    if isinstance(value, float) and math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return _stringify(value)


def _js_integer(value: int) -> str:
    """A Python integer as JavaScript would write the number nearest to it."""
    if abs(value) <= EXACT_INTEGER_LIMIT:
        return str(value)
    # JavaScript has no integer this large, only the double nearest to it, and writes that
    # double's shortest digits padded with zeros.
    try:
        return js_number(float(value))
    except OverflowError:
        raise ValueError(f"{value} is beyond the range of a JavaScript number") from None


def canonical_line(value: Mapping[str, JsonInput]) -> str:
    """One manifest or receipt line: top-level keys sorted, nothing below reordered.

    Mirrors `canonicalLine` in `shared/archive/schema.ts`. The shallowness is deliberate
    there and must be deliberate here: a nested object keeps the order it was built in.
    The TypeScript sorts the entries and builds an object from them, so array-index keys
    still come first, at this level and below, which `js_stringify` sees to.
    """
    ordered = {k: value[k] for k in sorted(value, key=utf16_key)}
    return js_stringify(ordered)


def canonical_json(value: JsonInput) -> str:
    """Keys sorted at every depth, `None`-valued members kept, as grid layer headers are written.

    Mirrors `canonicalJson` in `shared/archive/layers/grid/index.ts`. That function drops
    members whose value is `undefined`; Python has no `undefined`, and `null` survives in
    both languages, so a caller that means "absent" omits the key rather than setting None.

    The TypeScript sorts the entries and joins them into a string itself, without building
    an object, so here an array-index key sorts as text like any other. That is the opposite
    of `canonical_line`, and it is recorded from Node in `tests/fixtures/js-semantics.json`.
    """
    return _canonical(value)


def _canonical(value: object) -> str:
    if isinstance(value, Mapping):
        keys = sorted(js_key_order(value), key=utf16_key)
        return "{" + ",".join(f"{js_quote(k)}:{_canonical(value[k])}" for k in keys) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    return _stringify(value)


def sha256_hex(data: bytes | str) -> str:
    """The digest every identifier in the store is built from. Text is hashed as Node hashes it."""
    return hashlib.sha256(js_utf8(data) if isinstance(data, str) else data).hexdigest()


def record_sha256(record: Mapping[str, JsonInput]) -> str:
    """The digest written beside a record in `data/provenance/records/<shard>.jsonl`.

    `sha256(JSON.stringify(record))`, so it depends on key order as well as on content. A
    record rebuilt by this library must carry its fields in the order the store's schema
    declares them, or this returns a different digest for the same data.
    """
    return sha256_hex(js_stringify(record))


def shard_of(record_id: str) -> str:
    """The shard a record id belongs to: the leading hex digits of the digest of the id."""
    return sha256_hex(record_id)[:SHARD_DIGITS]


def object_shard(sha256: str) -> str:
    """The shard an archived object belongs to: the first character of its own digest.

    Refuses anything that is not a lowercase SHA-256, as `objectShard` does in
    `shared/archive/schema.ts`, so a malformed key cannot name a shard file.
    """
    if not _SHA256.fullmatch(sha256):
        raise ValueError(f"Not a sha256 key: {sha256}")
    return sha256[0]
