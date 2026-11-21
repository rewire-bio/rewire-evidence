"""Declared attributes and the older shapes, held to what the TypeScript does.

Every expected value that comes from `VALIDATION` was produced by `attributeIssues`,
`validateAttributes`, `normalizeAttributes`, `convertUncertainty` or `currentRecords` in the
data repository at the pinned commit.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import attributes
from rewire_evidence.attributes import (
    INHERITED,
    REGISTRY,
    REGISTRY_FILE,
    JsTypeError,
    attribute_issues,
    convert_uncertainty,
    normalize_attributes,
    same_json,
    schema_for,
    validate_attributes,
)
from rewire_evidence.canonical import JsonObject, JsonValue, js_stringify, js_value, sha256_hex
from rewire_evidence.js import MISSING
from rewire_evidence.schema import KINDS, RecordError, normalize_records, parse_record
from tests.recorded import BASE_ROWS, VALIDATION, named, value_of
from tests.strategies import json_objects, json_values

NORMALIZE: list[dict[str, Any]] = VALIDATION["normalize"]


def test_the_registry_is_the_one_the_typescript_declares() -> None:
    # The digest is of JSON.stringify(attributeRegistry), which this recomputes.
    assert sha256_hex(js_stringify(REGISTRY)) == REGISTRY_FILE["sha256"]
    assert REGISTRY_FILE["recorded_from"] == {
        "script": "scripts/fixtures/record_validation_fixture.mjs",
        "store_ref": VALIDATION["recorded_from"]["store_ref"],
        "source": "shared/omics/attribute-registry.ts",
    }
    assert set(REGISTRY) == set(KINDS)
    assert sum(len(declared) for declared in REGISTRY.values()) > 600
    for declared in REGISTRY.values():
        for kind in declared.values():
            assert schema_for(kind) is schema_for(kind.split(":")[0])
    # No kind declares a list of concepts today. The TypeScript reads one as it reads `concepts`.
    assert schema_for("concepts:area") is attributes.ATTRIBUTE_TYPES["concepts"]
    assert set(VALIDATION["contract"]["inherited"]) == INHERITED


def test_every_record_of_the_base_store_has_declared_attributes_of_their_types() -> None:
    records = [parse_record(row) for row in BASE_ROWS]
    assert [attribute_issues(record) for record in records] == [[]] * len(records)
    validate_attributes(records)
    assert len(records) > 200


@pytest.mark.parametrize("case", named(VALIDATION["attribute_issues"]))
def test_a_record_has_the_attribute_issues_the_typescript_finds(case: dict[str, Any]) -> None:
    record = parse_record(value_of(case["record"]))
    if case["error"] is None:
        assert attribute_issues(record) == case["issues"]
    else:
        assert case["error"]["name"] == "TypeError"
        with pytest.raises(JsTypeError, match="a member of every object"):
            attribute_issues(record)
    if case["thrown"] is None:
        validate_attributes([record])
    elif case["thrown"]["name"] == "TypeError":
        with pytest.raises(JsTypeError):
            validate_attributes([record])
    else:
        with pytest.raises(RecordError) as refusal:
            validate_attributes([record])
        assert str(refusal.value) == case["thrown"]["message"]


def test_each_attribute_of_the_base_store_is_refused_for_the_values_the_typescript_refuses() -> None:
    by_id = {row["id"]: row for row in BASE_ROWS}
    values = VALIDATION["base_attribute_issues"]["values"]
    held = VALIDATION["base_attribute_issues"]["records"]
    assert len(held) > 200
    wrong = []
    for record_id, key, outcomes in held:
        for value, recorded in zip(values, outcomes, strict=True):
            row = copy.deepcopy(by_id[record_id])
            attributes_of = row["attributes"]
            assert isinstance(attributes_of, dict)
            attributes_of[key] = value
            if attribute_issues(parse_record(row)) != recorded:
                wrong.append((record_id, key, value))
    assert wrong == []


def written(rows: list[JsonObject], before: list[JsonObject]) -> list[list[Any]]:
    return [[js_stringify(row), row is given] for row, given in zip(rows, before, strict=True)]


@pytest.mark.parametrize("case", named(NORMALIZE))
def test_an_older_shape_is_converted_as_the_typescript_converts_it(case: dict[str, Any]) -> None:
    rows: list[Any] = [value_of(text) for text in case["records"]]
    kept = copy.deepcopy(rows)
    lookup = {text: (entry["reason"], entry["keepNote"]) for text, entry in case.get("lookup") or []} or None
    if case["error"] is None:
        assert written(normalize_attributes(rows, lookup), rows) == case["output"]
    else:
        with pytest.raises(RecordError) as refusal:
            normalize_attributes(rows, lookup)
        assert str(refusal.value) == case["error"]["message"]
    # The rows given are never changed, whatever comes back.
    assert rows == kept
    assert [js_stringify(row) for row in rows] == [js_stringify(row) for row in kept]


def test_a_release_written_before_october_2026_reads_as_the_typescript_reads_it() -> None:
    held = VALIDATION["current"]
    assert held["error"] is None
    rows: list[Any] = [value_of(text) for text in held["records"]]
    assert len(rows) == 42
    current = normalize_attributes(normalize_records(rows))
    assert written(current, rows) == held["output"]
    changed = sum(not same for _, same in held["output"])
    assert changed > 30
    # What comes out is in the current shapes: converting it again changes nothing.
    again = normalize_attributes(current)
    assert all(now is before for now, before in zip(again, current, strict=True))


@pytest.mark.parametrize(("text", "recorded"), VALIDATION["uncertainty"])
def test_an_uncertainty_is_converted_as_the_typescript_converts_it(text: str, recorded: str) -> None:
    uncertainty, missing = convert_uncertainty(value_of(text))
    found: dict[str, Any] = {}
    if uncertainty is not MISSING:
        found["uncertainty"] = uncertainty
    if missing is not MISSING:
        found["missing"] = missing
    assert js_stringify(found) == recorded
    assert len(found) <= 1


def test_no_uncertainty_at_all_converts_to_nothing() -> None:
    assert convert_uncertainty(MISSING) == (MISSING, MISSING)


def test_a_row_with_no_attributes_or_links_is_left_alone() -> None:
    rows: list[JsonObject] = [
        {"id": "r", "kind": "evaluation"},
        {"id": "s", "kind": "result", "links": [5, {"relation": "evaluation"}]},
    ]
    assert normalize_attributes(rows) == rows
    assert all(now is row for now, row in zip(normalize_attributes(rows), rows, strict=True))


@given(json_values, json_values)
def test_two_values_are_the_same_json_exactly_when_they_are_written_alike(
    left: JsonValue, right: JsonValue
) -> None:
    one, other = js_value(left), js_value(right)
    try:
        expected = js_stringify(one) == js_stringify(other)
    except ValueError:
        return
    assert same_json(one, other) is expected
    assert same_json(one, one)
    assert same_json(MISSING, MISSING)
    assert not same_json(one, MISSING)


@given(st.sampled_from(KINDS), json_objects)
def test_converting_any_attributes_twice_changes_nothing_the_second_time(kind: str, held: JsonObject) -> None:
    rows: list[JsonObject] = [{"id": "r", "kind": kind, "links": [], "attributes": js_value(held)}]
    try:
        once = normalize_attributes(rows)
    except (RecordError, ValueError, TypeError):
        return
    assert all(now is before for now, before in zip(normalize_attributes(once), once, strict=True))


@given(st.sampled_from(KINDS), json_objects)
def test_validation_refuses_exactly_the_records_that_have_issues(kind: str, held: JsonObject) -> None:
    record = parse_record(
        {
            "id": "r",
            "kind": kind,
            "name": "R",
            "description": "",
            "status": "discovered",
            "facets": {},
            "source_ids": [],
            "links": [],
            "attributes": held,
        }
    )
    try:
        issues = attribute_issues(record)
    except JsTypeError:
        with pytest.raises(JsTypeError):
            validate_attributes([record])
        return
    if issues:
        with pytest.raises(RecordError, match=f"^{len(issues)} attribute problems:"):
            validate_attributes([record])
    else:
        validate_attributes([record])
    assert attributes.attribute_problems([record]) == [f"{kind} r: {issue}" for issue in issues]
