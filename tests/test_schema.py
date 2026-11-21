"""The record schema, held to what the TypeScript does.

`tests/fixtures/record-schema.json` was written by
`scripts/fixtures/record_schema_fixture.mjs`, which runs `recordSchema` and its neighbours
from the data repository at the pinned commit. Every expected value below that comes from
`FIXTURE` is something zod or the TypeScript produced, not something written by hand. CI
records the file again on every run and fails if it changes.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence.canonical import JsonObject, JsonValue, js_parse, js_stringify, sha256_hex
from rewire_evidence.jsonl import StoreError
from rewire_evidence.schema import (
    BENCHMARK_SUBJECT_KINDS,
    DATASET_SUBJECT_KINDS,
    KINDS,
    MODEL_SUBJECT_KINDS,
    RECORD_ID_PATTERN,
    RELATION_RULES,
    RELATIONS,
    SHAPE,
    STATUSES,
    Issue,
    Link,
    Record,
    RecordError,
    SchemaError,
    check_record,
    current_relation,
    normalize_records,
    parse_record,
    public_records,
    relation_allows,
    validate_references,
)
from rewire_evidence.store import EVIDENCE_KINDS, SHARDED_KINDS
from tests.strategies import json_objects, record_ids

FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "record-schema.json").read_text("utf-8")
)
CONTRACT: dict[str, Any] = FIXTURE["contract"]
#: The one accepted case Python cannot write: JavaScript writes null for a number beyond
#: every double, and `js_stringify` refuses to, as it has since the first stage.
BEYOND_A_DOUBLE = "a number beyond any double"


def named(cases: list[dict[str, Any]]) -> list[Any]:
    return [pytest.param(case, id=case["name"]) for case in cases]


ACCEPTED = [case for case in FIXTURE["parse"] if case["accepted"] and case["name"] != BEYOND_A_DOUBLE]
REJECTED = [case for case in FIXTURE["parse"] if not case["accepted"]]


def test_the_fixture_is_the_size_it_is_meant_to_be() -> None:
    # A recorder that silently dropped cases would leave these tests passing on less.
    assert len(ACCEPTED) >= 85
    assert len(REJECTED) >= 190
    assert len(FIXTURE["moved_keys"]) == 68
    assert FIXTURE["recorded_from"]["zod"] == "4.6.5"


def test_the_constants_are_the_ones_the_typescript_declares() -> None:
    assert list(SHAPE) == CONTRACT["shape"]
    assert list(KINDS) == CONTRACT["kinds"]
    assert list(STATUSES) == CONTRACT["statuses"]
    assert list(MODEL_SUBJECT_KINDS) == CONTRACT["model_subject_kinds"]
    assert list(BENCHMARK_SUBJECT_KINDS) == CONTRACT["benchmark_subject_kinds"]
    assert list(DATASET_SUBJECT_KINDS) == CONTRACT["dataset_subject_kinds"]
    assert list(EVIDENCE_KINDS) == CONTRACT["evidence_kinds"]
    assert list(SHARDED_KINDS) == CONTRACT["sharded_kinds"]
    assert CONTRACT["id_pattern"] == RECORD_ID_PATTERN
    assert CONTRACT["id_pattern_flags"] == ""
    assert list(RELATIONS) == CONTRACT["relations"]
    assert {
        name: {"from": list(rule.sources), "to": list(rule.targets)} for name, rule in RELATION_RULES.items()
    } == CONTRACT["relation_rules"]


@pytest.mark.parametrize("case", named(ACCEPTED))
def test_an_accepted_value_is_written_as_zod_writes_it(case: dict[str, Any]) -> None:
    checked = check_record(js_parse(case["input"]))
    assert checked.issues == ()
    assert checked.record is not None
    written = js_stringify(checked.record.row())
    assert written == case["output"]
    assert sha256_hex(written) == case["sha256"]


@pytest.mark.parametrize("case", named(REJECTED))
def test_a_refused_value_is_refused_by_the_same_rules_in_the_same_words(case: dict[str, Any]) -> None:
    checked = check_record(js_parse(case["input"]))
    assert checked.record is None
    assert [
        {"code": issue.code, "path": list(issue.path), "message": issue.message} for issue in checked.issues
    ] == case["issues"]
    with pytest.raises(SchemaError) as refused:
        parse_record(js_parse(case["input"]))
    assert refused.value.issues == checked.issues


@pytest.mark.parametrize("case", named(FIXTURE["moved_keys"]))
def test_a_stored_record_with_its_keys_moved_gets_the_digest_zod_gives(case: dict[str, Any]) -> None:
    written = js_stringify(parse_record(js_parse(case["input"])).row())
    assert sha256_hex(written) == case["sha256"]
    # Moving top-level keys and link keys changes nothing. Moving keys inside facets and
    # attributes makes another record, unless there was nothing there to move.
    if case["name"].endswith(", top keys moved"):
        assert case["sha256"] == case["committed_sha256"]


def test_moving_nested_keys_changes_the_digest_of_most_records() -> None:
    nested = [case for case in FIXTURE["moved_keys"] if case["name"].endswith(", nested keys moved")]
    changed = [case for case in nested if case["sha256"] != case["committed_sha256"]]
    assert len(nested) == 34
    assert len(changed) > 25


def test_a_number_beyond_any_double_is_accepted_and_cannot_be_written() -> None:
    (case,) = [case for case in FIXTURE["parse"] if case["name"] == BEYOND_A_DOUBLE]
    assert case["accepted"]
    assert '"a":null' in case["output"]
    record = parse_record(js_parse(case["input"]))
    assert record.attributes == {"a": float("inf"), "b": [float("-inf")]}
    with pytest.raises(ValueError, match=r"JSON\.stringify writes null for inf"):
        js_stringify(record.row())


def test_a_record_shares_nothing_with_the_value_it_was_parsed_from() -> None:
    value = js_parse(ACCEPTED[0]["input"])
    before = js_stringify(value)
    record = parse_record(value)
    record.attributes["added"] = 1
    record.facets["areas"].append("x")
    record.source_ids.append("y")
    assert js_stringify(value) == before


def test_a_record_is_written_in_declaration_order_however_it_is_built() -> None:
    record = Record(
        attributes={"z": 1, "a": 2},
        links=[Link(target_id="s", relation="source")],
        source_ids=["s"],
        facets={"b": ["x"], "a": []},
        status="discovered",
        description="",
        name="M",
        kind="model",
        id="m",
    )
    assert list(record.row()) == list(SHAPE)
    assert js_stringify(record.row()) == (
        '{"id":"m","kind":"model","name":"M","description":"","status":"discovered",'
        '"facets":{"b":["x"],"a":[]},"source_ids":["s"],'
        '"links":[{"relation":"source","target_id":"s"}],"attributes":{"z":1,"a":2}}'
    )
    assert check_record(record.row()).record == record


def test_an_issue_names_its_rule_and_an_error_lists_them() -> None:
    issue = Issue("invalid_type", ("links", 0, "relation"), "Invalid input: expected string, received number")
    assert issue.rule == "invalid_type:links.0.relation"
    assert Issue("unrecognized_keys", (), 'Unrecognized key: "x"').rule == "unrecognized_keys:"
    with pytest.raises(SchemaError) as refused:
        parse_record({"id": "A"})
    assert len(refused.value.issues) == 9
    assert str(refused.value).startswith("invalid_format:id: Invalid string: must match pattern /^[a-z0-9]+")
    assert "; invalid_type:attributes: Invalid input: expected record, received undefined" in str(
        refused.value
    )


def test_a_value_json_cannot_hold_is_not_a_schema_matter() -> None:
    with pytest.raises(TypeError, match="set has no JSON form"):
        check_record({"id": {1, 2}})
    with pytest.raises(TypeError, match="NaN is not a value JSON can hold"):
        check_record({"attributes": {"a": float("nan")}})
    deep: JsonValue = []
    for _ in range(100_000):
        deep = [deep]
    with pytest.raises(StoreError, match="nested too deeply to read"):
        check_record({"attributes": {"a": deep}})


def test_a_tuple_is_a_list_and_whole_floats_are_whole_numbers() -> None:
    row = js_parse(ACCEPTED[0]["input"])
    assert isinstance(row, dict)
    loose = {**row, "source_ids": ("source-a",), "attributes": {"n": 2.0, "big": 2**70}}
    record = parse_record(loose)
    assert record.source_ids == ["source-a"]
    assert record.attributes == {"n": 2, "big": float(2**70)}
    assert type(record.attributes["n"]) is int


# --- Relations -----------------------------------------------------------------------


@pytest.mark.parametrize("relation", list(FIXTURE["relation_allows"]))
def test_relation_allows_agrees_for_every_pair_of_kinds(relation: str) -> None:
    kinds = FIXTURE["relation_kinds"]
    rows = " ".join(
        "".join("1" if relation_allows(relation, source, target) else "0" for target in kinds)
        for source in kinds
    )
    assert rows == FIXTURE["relation_allows"][relation]


def test_a_name_every_javascript_object_has_is_no_relation() -> None:
    # The TypeScript throws for these, because it finds the name on Object.prototype. No
    # record reaches it with one: the schema refuses an unknown relation first.
    for name, recorded in FIXTURE["relation_allows_inherited_names"].items():
        assert recorded == {
            "allowed": None,
            "error": "Cannot read properties of undefined (reading 'includes')",
        }
        assert relation_allows(name, "model", "model") is False


def test_current_relation_agrees_for_every_kind_and_every_older_name() -> None:
    renamed = FIXTURE["current_relation"]
    checked = 0
    for kind in FIXTURE["relation_kinds"]:
        for name in FIXTURE["older_names"]:
            assert current_relation(kind, name) == renamed.get(kind, {}).get(name, name), (kind, name)
            checked += 1
    assert checked == 18 * 41
    assert sum(len(names) for names in renamed.values()) == 27


@pytest.mark.parametrize("case", named(FIXTURE["normalize_records"]))
def test_normalize_records_renames_what_the_typescript_renames(case: dict[str, Any]) -> None:
    rows = js_parse(case["input"])
    assert isinstance(rows, list)
    out = normalize_records(rows)  # type: ignore[arg-type]  # the fixture holds objects
    assert js_stringify(out) == case["output"]
    assert (out is rows) is case["same_list"]
    # Nothing it was given is changed.
    assert case["input_untouched"] is True
    assert js_stringify(rows) == case["input"]


def test_normalize_records_leaves_alone_what_it_cannot_read_as_a_relation() -> None:
    rows: list[JsonObject] = [
        {
            "id": "e",
            "kind": "evaluation",
            "links": [{"relation": 5}, {"target_id": "m"}, {"relation": "model"}],
        },
        {
            "id": "c",
            "kind": "claim",
            "links": [{"relation": "subject", "target_id": 7}],
            "attributes": {"field": "links:model:m"},
        },
        {"id": "d", "kind": "claim", "links": [], "attributes": ["field"]},
        {"id": "f", "kind": "claim", "links": []},
    ]
    out = normalize_records(rows)
    assert out[0]["links"] == [{"relation": 5}, {"target_id": "m"}, {"relation": "system"}]
    assert out[1:] == rows[1:]
    assert out[1] is rows[1]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"kind": "model", "links": []}], "record 1 has no string 'id'"),
        ([{"id": "a", "links": []}], "record 1 has no string 'kind'"),
        ([{"id": "a", "kind": "model"}], "record 1 has no list 'links'"),
        ([{"id": "a", "kind": "model", "links": {}}], "record 1 has no list 'links'"),
        (
            [{"id": "a", "kind": "model", "links": []}, {"id": "b", "kind": "model", "links": ["x"]}],
            "record 2 has a link that is not a JSON object",
        ),
    ],
)
def test_normalize_records_refuses_a_row_it_cannot_place(rows: list[JsonObject], message: str) -> None:
    with pytest.raises(StoreError, match=message):
        normalize_records(rows)


# --- Public records and references -----------------------------------------------------


def records_of(text: str) -> list[Record]:
    rows = js_parse(text)
    assert isinstance(rows, list)
    return [parse_record(row) for row in rows]


@pytest.mark.parametrize("case", named(FIXTURE["public_records"]))
def test_public_records_are_the_ones_the_typescript_keeps(case: dict[str, Any]) -> None:
    assert [record.id for record in public_records(records_of(case["input"]))] == case["public_ids"]


@pytest.mark.parametrize("case", named(FIXTURE["link_rules"]))
def test_references_are_refused_in_the_words_the_typescript_uses(case: dict[str, Any]) -> None:
    records = records_of(case["input"])
    if case["error"] is None:
        validate_references(records)
        validate_references(iter(records))
    else:
        with pytest.raises(RecordError) as refused:
            validate_references(records)
        assert str(refused.value) == case["error"]


def test_the_reference_cases_cover_every_message() -> None:
    errors = {case["error"].split(" ")[0] for case in FIXTURE["link_rules"] if case["error"]}
    assert errors == {"Duplicate", "Missing", "Dangling", "Relationship", "Invalid"}


# --- Properties ------------------------------------------------------------------------

names = st.text(min_size=1, max_size=8)
links = st.lists(
    st.tuples(st.sampled_from(RELATIONS), names).map(
        lambda pair: {"relation": pair[0], "target_id": pair[1]}
    ),
    max_size=4,
)
rows = st.fixed_dictionaries(
    {
        "id": record_ids,
        "kind": st.sampled_from(KINDS),
        "name": names,
        "description": st.text(max_size=8),
        "status": st.sampled_from(STATUSES),
        "facets": st.dictionaries(st.text(max_size=4), st.lists(st.text(max_size=4), max_size=3), max_size=3),
        "source_ids": st.lists(st.text(max_size=6), max_size=3),
        "links": links,
        "attributes": json_objects,
    }
    # Hypothesis does not promise the order of the keys it was given, and here order is the point.
).map(lambda row: {key: row[key] for key in SHAPE})


@given(rows, st.randoms(use_true_random=False))
def test_the_order_of_top_level_and_link_keys_never_changes_a_record(
    row: JsonObject, rng: random.Random
) -> None:
    def shuffled(value: JsonObject) -> JsonObject:
        items = list(value.items())
        rng.shuffle(items)
        return dict(items)

    held = row["links"]
    assert isinstance(held, list)
    moved = shuffled({**row, "links": [shuffled(link) for link in held if isinstance(link, dict)]})
    assert js_stringify(parse_record(moved).row()) == js_stringify(parse_record(row).row())


@given(rows)
def test_parsing_what_was_parsed_changes_nothing(row: JsonObject) -> None:
    record = parse_record(row)
    again = parse_record(record.row())
    assert again == record
    assert js_stringify(again.row()) == js_stringify(record.row())


@given(rows)
def test_a_valid_row_is_written_as_javascript_writes_the_row_itself(row: JsonObject) -> None:
    # The strategy builds rows in declaration order, so the schema has nothing to move and
    # the record is exactly what JSON.stringify gives for the row it came from.
    assert js_stringify(parse_record(row).row()) == js_stringify(row)


@given(rows, st.text(max_size=5).filter(lambda key: key not in SHAPE))
def test_any_key_the_schema_does_not_declare_is_refused(row: JsonObject, key: str) -> None:
    checked = check_record({**row, key: 1})
    assert checked.record is None
    assert [issue.code for issue in checked.issues] == ["unrecognized_keys"]


@given(rows, st.sampled_from(SHAPE))
def test_a_row_missing_any_member_is_refused_at_that_member(row: JsonObject, member: str) -> None:
    checked = check_record({key: value for key, value in row.items() if key != member})
    assert checked.record is None
    assert [issue.path for issue in checked.issues] == [(member,)]
