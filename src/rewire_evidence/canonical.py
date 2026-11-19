"""Byte-exact reproduction of the serialisations the TypeScript store writes.

Nothing in this module is about Python's idea of good JSON. Every function here exists to
emit the same bytes a particular piece of TypeScript emits, because those bytes are hashed
and the hashes are committed. If a function here changes its output by one character, data
already in the store stops verifying.

Three separate serialisations, and they are not interchangeable:

  js_stringify     JSON.stringify: keys in insertion order, no spaces. This is what
                   `recordSha256` hashes (scripts/omics/records.ts:42-44), so it decides
                   whether all 93,206 committed provenance digests still verify.
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

Verified against the live store on 2026-11-19: 93,206 of 93,206 record digests reproduce.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

#: Shard count for the record store: one hex digit, so sixteen files per sharded kind.
SHARD_DIGITS = 1


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
    _, digits, exponent = Decimal(repr(abs(float(value)))).as_tuple()
    # `significant` drops trailing zeros, which changes the digit count but never the
    # position of the point, so `point` is computed from the untrimmed tuple.
    significant = "".join(map(str, digits)).rstrip("0") or "0"
    count = len(significant)
    point = exponent + len(digits)

    if count <= point <= 21:
        out = significant + "0" * (point - count)
    elif 0 < point <= 21:
        out = significant[:point] + "." + significant[point:]
    elif -6 < point <= 0:
        out = "0." + "0" * (-point) + significant
    else:
        power = point - 1
        mantissa = significant if count == 1 else significant[0] + "." + significant[1:]
        out = f"{mantissa}e{'+' if power >= 0 else '-'}{abs(power)}"
    return "-" + out if negative else out


def js_quote(text: str) -> str:
    """One string as `JSON.stringify` writes it: the same escapes, non-ASCII left alone."""
    return json.dumps(text, ensure_ascii=False)


def _utf16_key(text: str) -> tuple[int, ...]:
    """A sort key matching JavaScript's string comparison, which orders by UTF-16 unit.

    Python orders by code point. The two agree on everything below U+E000 and disagree on
    astral characters, which sort before U+E000..U+FFFF in UTF-16 and after them in code
    points. No key in the store reaches that range today, and relying on that would be the
    kind of assumption this module exists to avoid.
    """
    return tuple(text.encode("utf-16-be"))


def js_stringify(value: Any) -> str:
    """`JSON.stringify(value)`: insertion order, no whitespace, JavaScript number format."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return js_quote(value)
    if isinstance(value, int):  # bool is handled above, and int is exact in JSON.
        return str(value)
    if isinstance(value, float):
        return js_number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(js_stringify(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(f"{js_quote(str(k))}:{js_stringify(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"{type(value).__name__} has no JSON.stringify form")


def canonical_line(value: dict[str, Any]) -> str:
    """One manifest or receipt line: top-level keys sorted, nothing below reordered.

    Mirrors `canonicalLine` in `shared/archive/schema.ts`. The shallowness is deliberate
    there and must be deliberate here: a nested object keeps the order it was built in.
    """
    ordered = {k: value[k] for k in sorted(value, key=_utf16_key)}
    return js_stringify(ordered)


def canonical_json(value: Any) -> str:
    """Keys sorted at every depth, `None`-valued members kept, as grid layer headers are written.

    Mirrors `canonicalJson` in `shared/archive/layers/grid/index.ts`. That function drops
    members whose value is `undefined`; Python has no `undefined`, and `null` survives in
    both languages, so a caller that means "absent" omits the key rather than setting None.
    """
    if value is None or not isinstance(value, (dict, list, tuple)):
        return js_stringify(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    keys = sorted(value, key=lambda k: _utf16_key(str(k)))
    return "{" + ",".join(f"{js_quote(str(k))}:{canonical_json(value[k])}" for k in keys) + "}"


def sha256_hex(data: bytes | str) -> str:
    """The digest every identifier in the store is built from."""
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def record_sha256(record: dict[str, Any]) -> str:
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
    """The shard an archived object belongs to: the first character of its own digest."""
    return sha256[0]
