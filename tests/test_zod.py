"""The zod schemas, held to what zod reports.

`tests/fixtures/validation.json` holds every attribute type against a palette of values, and
every structured schema against a valid value damaged in every place: each node replaced by
each of sixteen values, each member removed, members added, and combinations of those from a
generator with a stated seed. Each case has the issues zod 4.6.5 reported: code, path and
message, in zod's order. `scripts/fixtures/record_validation_fixture.mjs` records it in CI.
"""

from __future__ import annotations

import copy
import math
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import attributes, blocks, zod
from rewire_evidence.canonical import JsonValue, js_value
from rewire_evidence.js import MISSING
from rewire_evidence.schema import Issue, SchemaError
from tests.recorded import VALIDATION, value_of
from tests.strategies import json_values

TYPES: dict[str, list[Any]] = VALIDATION["attribute_types"]["outcomes"]
PALETTE = [value_of(text) for text in VALIDATION["attribute_types"]["palette"]]
SCHEMAS: dict[str, zod.Schema] = {
    "missing-metadata": attributes.MISSING_METADATA,
    "uncertainty": attributes.UNCERTAINTY,
    "coverage": attributes.COVERAGE,
    "review": attributes.REVIEW,
    "derivation": attributes.DERIVATION,
    "reported-population": attributes.REPORTED_POPULATION,
    "benchmark_research": blocks.BENCHMARK_RESEARCH,
    "run_documentation": blocks.RUN_DOCUMENTATION,
    "run_guide": blocks.RUN_GUIDE,
    "run_recipes": blocks.RUN_RECIPES,
    "reproduction": blocks.REPRODUCTION,
    "profile": blocks.PROFILE,
    "extensions": blocks.EXTENSIONS,
}


def reported(schema: zod.Schema, value: JsonValue) -> list[list[Any]]:
    return [[issue.code, list(issue.path), issue.message] for issue in schema.issues(value)]


def step(node: Any, key: str | int) -> Any:  # noqa: ANN401 - a node of any JSON value
    """One step of a path: a list by position, an object by name, and nothing otherwise."""
    if isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
        return node[key]
    if isinstance(node, dict) and isinstance(key, str):
        return node.get(key)
    return None


def damaged(example: JsonValue, ops: list[list[Any]]) -> JsonValue:
    """A value with the recorder's operations applied: `applyValueOps` in validation_cases.mjs.

    An operation aimed at something an earlier one removed does nothing. A list is reached
    by position and an object by name, and a list grows only by one, at its end.
    """
    root = copy.deepcopy(example)
    for kind, path, *rest in ops:
        if not path:
            root = copy.deepcopy(rest[0])
            continue
        node: Any = root
        for key in path[:-1]:
            node = step(node, key)
        last = path[-1]
        if isinstance(node, list) and isinstance(last, int) and last >= 0:
            if kind == "set" and last <= len(node):
                node[last : last + 1] = [copy.deepcopy(rest[0])]
            elif kind == "del" and last < len(node):
                del node[last]
        elif isinstance(node, dict) and isinstance(last, str):
            if kind == "set":
                node[last] = copy.deepcopy(rest[0])
            else:
                node.pop(last, None)
    return js_value(root)


def test_the_corpus_is_the_size_it_was_recorded_at() -> None:
    assert VALIDATION["recorded_from"]["zod"] == "4.6.5"
    assert list(TYPES) == list(attributes.ATTRIBUTE_TYPES) == attributes.REGISTRY_FILE["types"]
    assert len(PALETTE) > 100
    assert set(VALIDATION["schemas"]) == set(SCHEMAS)
    counts = {
        name: sum(len(example["cases"]) for example in held) for name, held in VALIDATION["schemas"].items()
    }
    assert all(count >= 150 for count in counts.values())
    assert sum(counts.values()) > 5000
    refused = sum(
        bool(issues)
        for held in VALIDATION["schemas"].values()
        for example in held
        for _, issues in example["cases"]
    )
    assert refused > sum(counts.values()) * 3 // 4


@pytest.mark.parametrize("name", list(TYPES))
def test_an_attribute_type_reports_what_zod_reports_for_every_value_of_the_palette(name: str) -> None:
    schema = attributes.ATTRIBUTE_TYPES[name]
    assert [reported(schema, value) for value in PALETTE] == TYPES[name]
    assert [schema.accepts(value) for value in PALETTE] == [not issues for issues in TYPES[name]]


@pytest.mark.parametrize("name", list(SCHEMAS))
def test_a_structured_schema_reports_what_zod_reports_for_every_damaged_value(name: str) -> None:
    for example in VALIDATION["schemas"][name]:
        assert reported(SCHEMAS[name], js_value(example["example"])) == []
        wrong = [
            (ops, issues, got)
            for ops, issues in example["cases"]
            if (got := reported(SCHEMAS[name], damaged(example["example"], ops))) != issues
        ]
        assert wrong == []


@pytest.mark.parametrize("name", list(SCHEMAS))
def test_parsing_gives_the_value_back_or_raises_every_issue(name: str) -> None:
    example = VALIDATION["schemas"][name][0]
    assert blocks.parsed(SCHEMAS[name], js_value(example["example"])) == js_value(example["example"])
    ops, issues = next(case for case in example["cases"] if case[1])
    with pytest.raises(SchemaError) as refusal:
        blocks.parsed(SCHEMAS[name], damaged(example["example"], ops))
    assert [[issue.code, list(issue.path), issue.message] for issue in refusal.value.issues] == issues


WRITTEN: dict[str, list[list[Any]]] = VALIDATION["written"]


@pytest.mark.parametrize("name", list(WRITTEN))
def test_a_rule_across_members_reports_what_zod_reports(name: str) -> None:
    # Values written for the rules damaging one member seldom breaks: bounds without a level,
    # a retired review method on a later date, a coordinate that ends before it starts.
    assert [reported(SCHEMAS[name], value_of(text)) for text, _ in WRITTEN[name]] == [
        issues for _, issues in WRITTEN[name]
    ]
    assert sum(bool(issues) for _, issues in WRITTEN[name]) >= 3


def test_the_rules_across_members_are_each_broken_by_something_written() -> None:
    said = {
        issue[2]
        for held in WRITTEN.values()
        for _, issues in held
        for issue in issues
        if issue[0] == "custom"
    }
    assert {
        message.split(" ", 1)[1]
        if message.split(" ")[0].endswith(("_deviation", "_interval", "_error", "_spread"))
        else message
        for message in said
    } >= {
        "needs lower and upper bounds or a half-width",
        "Give both interval bounds",
        "needs a value",
        "cannot have interval bounds or a level",
        "Omit an empty missing_metadata map",
        "Summary evidence needs both sources and a locator.",
        "Limited profiles must document evidence gaps.",
        "Reviewed profiles need sourced explanation.",
        "Coordinate end precedes start",
        "Zero-based variant offset is outside sequence window",
    }
    assert any(message.startswith("rendered-source-visual-check is historical") for message in said)


def test_a_union_reports_the_one_option_that_got_as_far_as_its_checks() -> None:
    # No union of the validators has options of two types, so this is zod's rule and not a recording.
    either = zod.union(zod.NONEMPTY, zod.number())
    assert [issue.message for issue in either.issues("")] == [
        "Too small: expected string to have >=1 characters"
    ]
    assert [issue.message for issue in either.issues(None)] == ["Invalid input"]
    assert either.issues(5) == either.issues("x") == ()


def test_a_member_that_must_be_there_and_accepts_anything_is_reported_when_it_is_not() -> None:
    # No schema of the validators has such a member, so this is zod's rule and not a recording.
    schema = zod.strict({"anything": zod.Unknown(), "maybe": zod.optional(zod.Unknown())})
    assert schema.issues({}) == (
        Issue("invalid_type", ("anything",), "Invalid input: expected nonoptional, received undefined"),
    )
    assert schema.issues({"anything": None}) == ()
    assert zod.Unknown().issues(MISSING) == ()


def test_a_whole_number_beyond_what_a_double_holds_exactly_is_too_big_or_too_small() -> None:
    assert attributes.INTEGER.issues(2**53 - 1) == ()
    assert [issue.message for issue in attributes.INTEGER.issues(2**53)] == [
        "Too big: expected int to be <=9007199254740991"
    ]
    assert [issue.message for issue in attributes.INTEGER.issues(-(2**53))] == [
        "Too small: expected int to be >=-9007199254740991"
    ]
    assert [issue.message for issue in attributes.INTEGER.issues(1e21)] == [
        "Too big: expected int to be <=9007199254740991"
    ]
    assert [issue.message for issue in zod.number().issues(math.inf)] == [
        "Invalid input: expected number, received Infinity"
    ]


def test_text_is_measured_in_characters_where_units_would_decide_otherwise() -> None:
    two = zod.array(zod.NONEMPTY, zod.min_length(2), zod.max_length(2))
    assert two.issues(["a", "b"]) == ()
    assert [issue.message for issue in two.issues("\U0001f9ec")] == [
        "Invalid input: expected array, received string",
        "Too small: expected string to have >=2 characters",
    ]
    assert [issue.message for issue in two.issues("abc")] == [
        "Invalid input: expected array, received string",
        "Too big: expected string to have <=2 characters",
    ]
    assert [issue.message for issue in two.issues("\U0001f9ec\U0001f9ec")] == [
        "Invalid input: expected array, received string"
    ]
    assert [issue.message for issue in two.issues({"length": 3})] == [
        "Invalid input: expected array, received object",
        "Too big: expected unknown to be <=2",
    ]


@given(json_values)
def test_every_schema_answers_for_any_value_and_accepts_only_what_it_reports_nothing_of(
    value: JsonValue,
) -> None:
    held = js_value(value)
    for schema in (*attributes.ATTRIBUTE_TYPES.values(), *SCHEMAS.values()):
        issues = schema.issues(held)
        assert schema.accepts(held) is (not issues)
        assert all(issue.code and issue.message for issue in issues)


@given(json_values)
def test_anything_json_is_accepted_as_json_only_if_it_is_a_list_or_an_object(value: JsonValue) -> None:
    held = js_value(value)
    assert attributes.ATTRIBUTE_TYPES["json"].accepts(held) is isinstance(held, (dict, list))


@given(st.text())
def test_a_union_of_text_schemas_accepts_what_either_accepts(text: str) -> None:
    either = attributes.DATE.accepts(text) or attributes.DATE_TIME.accepts(text)
    assert attributes.ATTRIBUTE_TYPES["datetime"].accepts(text) is either
