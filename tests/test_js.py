"""What JavaScript does with a value, held to what Node does.

`tests/fixtures/js-semantics.json` is written by `scripts/fixtures/record_js_fixture.mjs`
with Node's built-ins. Every expected value here is something Node produced.
"""

from __future__ import annotations

import math
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import js
from rewire_evidence.canonical import JsonValue, js_string, js_value, utf16_key
from tests.recorded import SEMANTICS, value_of
from tests.strategies import json_values


def written(number: float) -> str:
    """A number as `String(number)` writes it."""
    return "NaN" if math.isnan(number) else js_string(js_value(number))


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["truthy"])
def test_a_value_counts_as_true_when_javascript_takes_it_so(text: str, *, expected: bool) -> None:
    assert js.truthy(value_of(text)) is expected


def test_nothing_at_all_is_not_true_and_reads_as_undefined() -> None:
    assert js.truthy(js.MISSING) is False
    assert js.text_of(js.MISSING) == "undefined"
    assert js.received(js.MISSING) == "undefined"
    assert js.text_of(None) == "null"
    with pytest.raises(TypeError):
        js.text_of({"toString": 1})


@pytest.mark.parametrize(("text", "trimmed", "collapsed"), SEMANTICS["trim"])
def test_white_space_is_javascripts(text: str, trimmed: str, collapsed: str) -> None:
    assert js.trim(text) == trimmed
    assert re.sub(f"[{js.SPACE_CLASS}]+", " ", text) == collapsed


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["number_of_text"])
def test_text_is_read_as_a_number_the_way_javascript_reads_it(text: str, expected: str) -> None:
    assert written(js.number_of_text(text)) == expected


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["number_of"])
def test_any_value_is_read_as_a_number_the_way_javascript_reads_it(text: str, expected: str) -> None:
    assert written(js.number_of(value_of(text))) == expected


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["lower"])
def test_lowercase_is_javascripts(text: str, expected: str) -> None:
    assert text.lower() == expected


@pytest.mark.parametrize(("text", "end", "expected", "length"), SEMANTICS["slice"])
def test_text_is_measured_and_cut_in_utf16_units(text: str, end: int, expected: str, length: int) -> None:
    assert js.slice_units(text, end) == expected
    assert js.units(text) == length
    assert len(js.unit_text(text)) == length


@pytest.mark.parametrize(("left", "right", "expected"), SEMANTICS["strictly_equal"])
def test_strict_equality_is_javascripts(left: str, right: str, *, expected: bool) -> None:
    assert js.strictly_equal(value_of(left), value_of(right)) is expected


def test_a_list_or_an_object_strictly_equals_only_itself() -> None:
    held: JsonValue = {"a": 1}
    assert js.strictly_equal(held, held)
    assert not js.strictly_equal(held, {"a": 1})
    assert js.strictly_equal(js.MISSING, js.MISSING)
    assert not js.strictly_equal(js.MISSING, None)


@pytest.mark.parametrize(("ids", "expected"), SEMANTICS["locale_order"])
def test_record_ids_sort_by_unit_as_localecompare_sorts_them(ids: list[str], expected: list[str]) -> None:
    assert sorted(ids, key=utf16_key) == expected


def test_whole_numbers_are_integers_whatever_python_holds_them_as() -> None:
    assert js.is_integer(3)
    assert js.is_integer(1e21)
    assert not js.is_integer(1.5)
    assert not js.is_integer(value_of("true"))
    assert not js.is_integer("3")


@given(st.text())
def test_trimming_twice_is_trimming_once(text: str) -> None:
    assert js.trim(js.trim(text)) == js.trim(text)


@given(st.text())
def test_the_unit_form_has_one_character_for_each_unit(text: str) -> None:
    assert len(js.unit_text(text)) == js.units(text)
    assert js.unit_text(js.unit_text(text)) == js.unit_text(text)


@given(st.text(), st.integers(0, 40))
def test_a_slice_is_a_prefix_no_longer_than_asked(text: str, end: int) -> None:
    cut = js.slice_units(text, end)
    assert js.units(cut) == min(end, js.units(text))
    assert js.unit_text(text).startswith(js.unit_text(cut))


@given(json_values)
def test_strict_equality_holds_between_a_value_and_itself_only_for_what_javascript_compares_by_value(
    value: JsonValue,
) -> None:
    held = js_value(value)
    assert js.strictly_equal(held, held)
    assert js.truthy(held) is (held not in (None, False, 0, ""))
