"""The determinism kernel, checked against output Node actually produced.

`tests/js-fixture.json` and `tests/fixtures/js-semantics.json` were written by
`scripts/fixtures/record_js_fixture.mjs`, so these are not assertions about what the
specification says. They are a recording of the other implementation, and CI records them
again on every run to prove the files are what the script produces.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence.canonical import (
    EXACT_INTEGER_LIMIT,
    MAX_ARRAY_INDEX,
    JsonObject,
    JsonValue,
    canonical_json,
    canonical_line,
    js_key_order,
    js_number,
    js_parse,
    js_quote,
    js_stringify,
    js_utf8,
    js_value,
    object_shard,
    record_sha256,
    sha256_hex,
    shard_of,
    utf16_key,
)
from tests.strategies import json_objects, json_values, keys

HERE = Path(__file__).parent
FIXTURE = json.loads((HERE / "js-fixture.json").read_text("utf-8"))
SEMANTICS = json.loads((HERE / "fixtures" / "js-semantics.json").read_text("utf-8"))


@pytest.mark.parametrize(("literal", "expected"), FIXTURE["numbers"])
def test_numbers_match_node(literal: str, expected: str) -> None:
    assert js_number(float(literal)) == expected


@pytest.mark.parametrize(("text", "expected"), FIXTURE["strings"])
def test_strings_match_node(text: str, expected: str) -> None:
    assert js_stringify(text) == expected


@pytest.mark.parametrize(("value", "expected"), FIXTURE["stringify"])
def test_objects_match_node(value: JsonObject, expected: str) -> None:
    assert js_stringify(value) == expected


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["key_order"])
def test_parse_then_stringify_orders_keys_as_node_does(text: str, expected: str) -> None:
    assert js_stringify(js_parse(text)) == expected


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["canonical_line"])
def test_canonical_line_matches_node(text: str, expected: str) -> None:
    parsed = js_parse(text)
    assert isinstance(parsed, dict)
    assert canonical_line(parsed) == expected


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["canonical_json"])
def test_canonical_json_matches_node(text: str, expected: str) -> None:
    assert canonical_json(js_parse(text)) == expected


@pytest.mark.parametrize(("literal", "expected"), SEMANTICS["large_integers"])
def test_integers_beyond_a_double_match_node(literal: str, expected: str) -> None:
    # Read as JSON.parse reads them, and handed over as the Python integer they look like.
    assert js_stringify(js_parse(literal)) == expected
    assert js_stringify(int(literal)) == expected


@pytest.mark.parametrize(
    ("literal", "expected"), SEMANTICS["overflow"], ids=[text[:8] for text, _ in SEMANTICS["overflow"]]
)
def test_a_number_beyond_any_double_reads_as_infinity_and_is_not_written(literal: str, expected: str) -> None:
    # JSON.parse reads these as Infinity and JSON.stringify writes that as null. The reader
    # agrees, however many digits the literal has; the writer refuses, so that a record
    # holding one stops the run instead of being stored as null.
    assert "null" in expected
    value = js_parse(literal)
    number = value[0] if isinstance(value, list) else value
    assert isinstance(number, float)
    assert math.isinf(number)
    with pytest.raises(ValueError, match=r"JSON\.stringify writes null for"):
        js_stringify(value)


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["sha256_of_text"])
def test_text_is_hashed_as_the_bytes_node_hashes(text: str, expected: str) -> None:
    # Node encodes a string as UTF-8 with U+FFFD for a surrogate that has no partner.
    # shardOf hashes an ID that way, so an ID holding one still names a shard.
    assert sha256_hex(text) == expected
    assert shard_of(text) == expected[0]
    assert js_utf8(text) == js_utf8(text).decode("utf-8").encode("utf-8")


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["strings"])
def test_lone_surrogates_and_boundary_characters_match_node(text: str, expected: str) -> None:
    assert js_quote(text) == expected
    # Whatever the input held, the output can always be hashed.
    assert sha256_hex(js_quote(text))


@pytest.mark.parametrize(("unsorted", "expected"), SEMANTICS["sort"])
def test_sort_order_matches_node(unsorted: list[str], expected: list[str]) -> None:
    assert sorted(unsorted, key=utf16_key) == expected


def test_code_point_order_is_not_javascript_order() -> None:
    # The reason utf16_key exists: an astral character sorts before U+E000 in JavaScript
    # and after it in Python.
    astral, private = "\U0001f9ec", "\ue000"
    assert sorted([private, astral]) == [private, astral]
    assert sorted([private, astral], key=utf16_key) == [astral, private]


@pytest.mark.parametrize(("text", "refused"), SEMANTICS["refused"])
def test_parse_refuses_what_node_refuses(text: str, refused: bool) -> None:  # noqa: FBT001
    assert refused
    with pytest.raises(ValueError, match=r".*"):
        js_parse(text)


def test_a_surrogate_pair_held_as_two_code_points_is_one_character() -> None:
    # A Python string can hold the two halves as separate code points. JavaScript cannot
    # tell that apart from the character, so neither may the bytes written.
    halves = chr(0xD83E) + chr(0xDDEC)
    assert len(halves) == 2
    assert js_quote(halves) == '"' + chr(0x1F9EC) + '"'
    assert utf16_key(halves) == utf16_key(chr(0x1F9EC))


def test_the_four_values_that_broke_the_naive_port() -> None:
    # Python's default writes 1e-05 and 2e-05 here, which is what made four records in the
    # live store fail to verify before js_number existed.
    assert js_stringify({"tol": 1e-05}) == '{"tol":0.00001}'
    assert js_stringify({"learning_rate": 2e-05}) == '{"learning_rate":0.00002}'
    assert json.dumps({"tol": 1e-05}, separators=(",", ":")) == '{"tol":1e-05}'


def test_integral_floats_lose_the_decimal_point() -> None:
    assert js_number(1.0) == "1"
    assert js_number(-0.0) == "0"
    assert js_number(100.0) == "100"
    assert js_stringify([1.0, 2.0]) == "[1,2]"


def test_non_numbers_are_refused_rather_than_written_as_null() -> None:
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="not a number a record may hold"):
            js_number(bad)
    # JSON.parse reads 1e999 as Infinity, which then has no form a record may hold.
    with pytest.raises(ValueError, match="not a number a record may hold"):
        js_stringify(js_parse("1e999"))


def test_an_integer_beyond_any_double_is_refused() -> None:
    with pytest.raises(ValueError, match="beyond the range of a JavaScript number"):
        js_stringify(10**400)


def test_a_value_with_no_json_form_is_refused() -> None:
    bad: Any = {"when": object()}
    with pytest.raises(TypeError, match=r"object has no JSON\.stringify form"):
        js_stringify(bad)
    for not_json in (b"bytes", {1, 2}, 1 + 2j):
        value: Any = not_json
        with pytest.raises(TypeError, match=r"has no JSON\.stringify form"):
            js_stringify(value)
        with pytest.raises(TypeError, match=r"has no JSON\.stringify form"):
            canonical_json(value)


def test_a_key_that_is_not_text_is_refused() -> None:
    # str(True) is "True" where JavaScript writes "true", so guessing would write wrong bytes.
    for key in (1, True, None, 1.5):
        bad: Any = {key: 1}
        with pytest.raises(TypeError, match="an object key must be a string"):
            js_stringify(bad)
        with pytest.raises(TypeError, match="an object key must be a string"):
            canonical_json(bad)


def test_tuples_and_read_only_mappings_serialise_like_lists_and_dicts() -> None:
    assert js_stringify((1, "two")) == '[1,"two"]'
    assert js_stringify(MappingProxyType({"b": (1,), "a": 2})) == '{"b":[1],"a":2}'
    assert canonical_json(MappingProxyType({"b": (1,), "a": 2})) == '{"a":2,"b":[1]}'


def test_insertion_order_is_preserved_because_the_digest_depends_on_it() -> None:
    assert js_stringify({"b": 1, "a": 2}) == '{"b":1,"a":2}'
    assert record_sha256({"b": 1, "a": 2}) != record_sha256({"a": 2, "b": 1})


def test_array_index_keys_come_first_as_they_do_in_javascript() -> None:
    assert js_key_order(["b", "10", "a", "9", "01"]) == ["9", "10", "b", "a", "01"]
    # The bound is the largest array index. One past it is an ordinary key.
    assert js_key_order(["x", str(MAX_ARRAY_INDEX + 1), str(MAX_ARRAY_INDEX)]) == [
        str(MAX_ARRAY_INDEX),
        "x",
        str(MAX_ARRAY_INDEX + 1),
    ]
    assert js_stringify({"b": 1, "1": 2}) == '{"1":2,"b":1}'


def test_canonical_line_sorts_the_top_level_only() -> None:
    # The distinction that `json.dumps(sort_keys=True)` silently gets wrong: the nested
    # object keeps the order it was built in.
    value: JsonObject = {"z": 1, "a": {"y": 2, "b": 3}}
    assert canonical_line(value) == '{"a":{"y":2,"b":3},"z":1}'
    assert json.dumps(value, sort_keys=True, separators=(",", ":")) == '{"a":{"b":3,"y":2},"z":1}'


def test_canonical_json_sorts_at_every_depth() -> None:
    assert canonical_json({"z": 1, "a": {"y": 2, "b": 3}}) == '{"a":{"b":3,"y":2},"z":1}'
    assert canonical_json([{"b": 1, "a": 2}]) == '[{"a":2,"b":1}]'
    assert canonical_json("plain") == '"plain"'
    assert canonical_json(None) == "null"


def test_the_two_canonical_forms_disagree_and_that_is_the_point() -> None:
    value: JsonObject = {"z": 1, "a": {"y": 2, "b": 3}}
    assert canonical_line(value) != canonical_json(value)
    # They also disagree on array-index keys: one builds an object, the other a string.
    assert canonical_line({"10": 1, "9": 2}) == '{"9":2,"10":1}'
    assert canonical_json({"10": 1, "9": 2}) == '{"10":1,"9":2}'


def test_digests_and_shards() -> None:
    assert sha256_hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_hex(b"") == sha256_hex("")
    assert shard_of("any-record-id") in set("0123456789abcdef")
    assert object_shard("a1" * 32) == "a"


@pytest.mark.parametrize("bad", ["a1b2c3", "A1" * 32, "g" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n", ""])
def test_object_shard_refuses_what_is_not_a_sha256(bad: str) -> None:
    with pytest.raises(ValueError, match="Not a sha256 key"):
        object_shard(bad)


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_a_written_number_reads_back_as_the_same_double(value: float) -> None:
    written = js_number(value)
    assert float(written) == value
    assert written == written.strip()
    assert "E" not in written
    assert not written.startswith(("+", "."))


@given(st.integers(-EXACT_INTEGER_LIMIT, EXACT_INTEGER_LIMIT))
def test_a_safe_integer_is_written_the_same_as_an_int_and_as_a_float(value: int) -> None:
    assert js_stringify(value) == str(value)
    assert js_number(float(value)) == str(value)


@given(json_values)
def test_stringify_writes_json_that_reads_back_equal(value: JsonValue) -> None:
    written = js_stringify(value)
    # Read as JavaScript reads it: Python's own reader keeps an integer beyond 2**53 exact,
    # where the number written was the double nearest to it.
    assert js_parse(written) == value
    json.loads(written)
    assert "\n" not in written


@given(json_values)
def test_parse_then_stringify_is_the_identity_on_written_text(value: JsonValue) -> None:
    written = js_stringify(value)
    assert js_stringify(js_parse(written)) == written
    assert canonical_json(js_parse(canonical_json(value))) == canonical_json(value)


@given(json_objects, st.randoms(use_true_random=False))
def test_canonical_line_ignores_top_level_order_only(value: JsonObject, rng: random.Random) -> None:
    names = list(value)
    rng.shuffle(names)
    assert canonical_line({name: value[name] for name in names}) == canonical_line(value)
    assert canonical_line(js_parse(canonical_line(value))) == canonical_line(value)  # type: ignore[arg-type]


def _shuffled(value: JsonValue, rng: random.Random) -> JsonValue:
    if isinstance(value, dict):
        names = list(value)
        rng.shuffle(names)
        return {name: _shuffled(value[name], rng) for name in names}
    if isinstance(value, list):
        return [_shuffled(item, rng) for item in value]
    return value


@given(json_values, st.randoms(use_true_random=False))
def test_canonical_json_ignores_order_at_every_depth(value: JsonValue, rng: random.Random) -> None:
    assert canonical_json(_shuffled(value, rng)) == canonical_json(value)


def _utf16_units(text: str) -> list[int]:
    raw = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(raw[at : at + 2], "little") for at in range(0, len(raw), 2)]


@given(st.text(), st.text())
def test_utf16_key_orders_by_code_unit(left: str, right: str) -> None:
    assert (utf16_key(left) < utf16_key(right)) == (_utf16_units(left) < _utf16_units(right))
    assert (utf16_key(left) == utf16_key(right)) == (left == right)


@given(st.lists(keys, unique=True))
def test_key_order_is_a_stable_permutation_with_indices_first(names: list[str]) -> None:
    order = js_key_order(names)
    assert sorted(order) == sorted(names)
    assert js_key_order(order) == order
    indices = [name for name in order if name.isascii() and name.isdigit() and str(int(name)) == name]
    indices = [name for name in indices if int(name) <= MAX_ARRAY_INDEX]
    assert order[: len(indices)] == sorted(indices, key=int)
    assert order[len(indices) :] == [name for name in names if name not in indices]


@given(st.text(alphabet=st.characters(exclude_categories=["Cs"])))
def test_text_without_surrogates_is_hashed_as_plain_utf8(text: str) -> None:
    assert js_utf8(text) == text.encode("utf-8")
    assert sha256_hex(text) == sha256_hex(text.encode("utf-8"))


#: Text that may hold surrogates with no partner, which st.text() leaves out.
loose_text = st.text(alphabet=st.characters())


@given(loose_text, st.characters(min_codepoint=0x10000), loose_text)
def test_a_pair_held_as_two_code_points_is_hashed_as_the_character(
    before: str, astral: str, after: str
) -> None:
    # Whatever surrounds it, and whatever lone surrogates the surroundings hold, the bytes
    # are valid UTF-8 and the two spellings of one JavaScript string are one digest.
    high, low = divmod(ord(astral) - 0x10000, 0x400)
    halves = chr(0xD800 + high) + chr(0xDC00 + low)
    assert js_utf8(before + halves + after) == js_utf8(before + astral + after)
    assert shard_of(before + halves + after) == shard_of(before + astral + after)
    assert astral in js_utf8(before + astral + after).decode("utf-8")


def test_a_value_is_held_in_one_form_as_javascript_holds_it() -> None:
    assert js_value({"b": 1.0, "10": -0.0, "2": (1, 2.5), "a": {"z": None, "0": True}}) == {
        "2": [1, 2.5],
        "10": 0,
        "b": 1,
        "a": {"0": True, "z": None},
    }
    held = js_value({"b": 1.0, "10": -0.0, "2": [1.0]})
    assert isinstance(held, dict)
    assert list(held) == ["2", "10", "b"]
    assert [type(held["b"]), type(held["10"])] == [int, int]
    # An integer no double holds exactly becomes the double nearest to it, as JSON.parse reads it.
    assert js_value(2**53) == 2**53
    assert type(js_value(2**53)) is int
    assert js_value(2**53 + 1) == float(2**53)
    assert type(js_value(2**53 + 1)) is float
    assert type(js_value(float(2**60))) is float
    assert js_value(10**400) == math.inf
    assert js_value(-(10**400)) == -math.inf
    assert js_value(math.inf) == math.inf
    assert js_value(MappingProxyType({"a": 1.0})) == {"a": 1}


def test_a_value_json_cannot_hold_is_refused() -> None:
    with pytest.raises(TypeError, match="NaN is not a value JSON can hold"):
        js_value([math.nan])
    with pytest.raises(TypeError, match="set has no JSON form"):
        js_value({"a": {1}})
    with pytest.raises(TypeError, match="an object key must be a string, not int"):
        js_value({1: "a"})


@given(json_values)
def test_holding_a_value_as_javascript_does_never_changes_what_is_written(value: JsonValue) -> None:
    held = js_value(value)
    assert js_stringify(held) == js_stringify(value)
    # It is the form JSON.parse gives back, so it is its own fixed point and shares nothing.
    assert js_value(held) == held
    assert js_value(js_parse(js_stringify(value))) == held


@given(json_values)
def test_two_spellings_of_one_value_are_equal_once_held(value: JsonValue) -> None:
    # Every whole number written as a float: another Python value, the same JavaScript one.
    def respelt(item: JsonValue) -> JsonValue:
        if isinstance(item, bool) or item is None or isinstance(item, str):
            return item
        if isinstance(item, int):
            return float(item)
        if isinstance(item, list):
            return [respelt(entry) for entry in item]
        if isinstance(item, dict):
            return {key: respelt(held) for key, held in item.items()}
        return item

    other = respelt(value)
    assert js_stringify(other) == js_stringify(value)
    assert js_value(other) == js_value(value)
