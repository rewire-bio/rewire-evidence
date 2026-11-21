"""The validators of `loadRecords`, held to what the TypeScript does.

`tests/fixtures/validation.json` holds a small store of real records and several hundred
faults seeded into it. For each, the recorder asked every validator on its own and wrote down
what it did: nothing, zod's issues, or an error's name and message. Here the same fault is
seeded and every validator has to do the same. The validator that refuses first in
`loadRecords`' order is what `records -- check` would report.
"""

from __future__ import annotations

import contextlib
import copy
import math
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import blocks, concerns, descriptions
from rewire_evidence.agreement import agrees, apply_ops, outcome, stage_runners
from rewire_evidence.attributes import JsTypeError
from rewire_evidence.canonical import JsonObject, JsonValue, js_value
from rewire_evidence.derivations import derivation_issues
from rewire_evidence.descriptions import DESCRIPTION_RULES, description_problems, normalise_description
from rewire_evidence.jsonl import StoreError
from rewire_evidence.panels import Query, Resolved, compared, current_records, panels_of, resolve_panels
from rewire_evidence.schema import Record, RecordError, SchemaError, parse_record
from rewire_evidence.validation import (
    by_id_order,
    check_record_rules,
    validate_all,
    validate_records,
    validate_store,
)
from rewire_evidence.vocab import validate_vocabularies
from rewire_evidence.weburl import UndecidedHost
from tests.recorded import (
    BASE_FILES,
    BASE_ROWS,
    ROLES,
    SEMANTICS,
    VALIDATION,
    mapping,
    named,
    records_of,
    row_of,
    schemes,
    value_of,
    write_scripts,
)
from tests.strategies import json_values

CASES: list[dict[str, Any]] = VALIDATION["validators"]
CONTRACT: dict[str, Any] = VALIDATION["contract"]
STAGES = ("vocabulary", "attributes", "derivations", "descriptions", "concerns", "records")


@pytest.fixture(scope="module")
def scripts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_scripts(tmp_path_factory.mktemp("repository"))


def answers(rows: list[JsonObject], root: Path) -> dict[str, JsonObject | None]:
    """What each validator does with some rows, each asked on its own, as the recorder asked."""
    refused = outcome(lambda: records_of(rows))
    if refused is not None:
        return {"schema": refused}
    records = records_of(rows)
    runners = stage_runners(root)
    vocabulary = outcome(
        lambda: validate_vocabularies(mapping(), (record.row() for record in records), schemes())
    )
    return {"vocabulary": vocabulary, **{name: asked(runners[name], records) for name in STAGES[1:]}}


def asked(runner: Callable[[Sequence[Record]], object], records: Sequence[Record]) -> JsonObject | None:
    return outcome(lambda: runner(records))


def test_the_corpus_is_the_size_it_was_recorded_at() -> None:
    assert len(BASE_ROWS) > 200
    assert len(CASES) > 380
    first = Counter(
        next((stage for stage, held in case["stages"].items() if held), "accepted") for case in CASES
    )
    assert first["records"] > 180
    assert first["attributes"] > 100
    assert first["accepted"] > 30
    assert all(first[stage] for stage in ("schema", *STAGES))
    kinds = Counter(
        "zod" if "zod" in held else held["name"]
        for case in CASES
        for held in case["stages"].values()
        if held is not None
    )
    assert kinds["Error"] > 370
    assert kinds["zod"] > 30
    assert kinds["TypeError"] > 15
    # Every message validateRecords can throw is in the corpus at least once.
    refusals = {
        held["message"].split(":")[0].rstrip(" 0123456789")
        for case in CASES
        if (held := case["stages"].get("records")) and "message" in held
    }
    assert len(refusals) > 40


def test_the_base_store_is_accepted_by_every_validator(scripts: Path) -> None:
    assert CASES[0] == {"name": "the base store, unchanged", "ops": [], "stages": dict.fromkeys(STAGES)}
    assert answers(BASE_ROWS, scripts) == dict.fromkeys(STAGES)
    records = records_of(BASE_ROWS)
    ordered, shown = validate_all(records, scripts)
    assert ordered == by_id_order(records) == validate_store(records, scripts)
    assert [record.id for record in ordered] == sorted(record.id for record in records)
    assert ROLES["panel_self"] in shown
    assert ROLES["panel_parent"] in shown
    assert all(panels for panels in shown.values())


@pytest.mark.parametrize("case", named(CASES))
def test_a_seeded_fault_is_refused_by_the_validators_that_refuse_it_in_the_same_words(
    case: dict[str, Any], scripts: Path
) -> None:
    got = answers(apply_ops(BASE_ROWS, case["ops"]), scripts)
    recorded = case["stages"]
    assert set(got) == set(recorded)
    for stage, held in recorded.items():
        assert agrees(got[stage], held), stage
    # A TypeError is the one answer compared by its name alone.
    words = {stage: held for stage, held in got.items() if held and held.get("name") != "TypeError"}
    assert words == {
        stage: held for stage, held in recorded.items() if held and held.get("name") != "TypeError"
    }


def test_seeding_a_fault_leaves_the_base_store_as_it_was() -> None:
    kept = copy.deepcopy(BASE_ROWS)
    for case in CASES:
        faulted = apply_ops(BASE_ROWS, case["ops"])
        touched = {op[1] for op in case["ops"] if op[0] in {"set", "del"}}
        assert all(
            row is before
            for row, before in zip(faulted, BASE_ROWS, strict=False)
            if before["id"] not in touched and row["id"] == before["id"]
        )
    assert kept == BASE_ROWS


def test_an_operation_that_cannot_be_applied_is_refused() -> None:
    rows: list[JsonObject] = [{"id": "a", "attributes": {"list": [1], "text": "x"}}]
    assert apply_ops(
        rows, [["set", "a", ["attributes", "list", 1], 2], ["del", "a", ["attributes", "list", 0]]]
    ) == [{"id": "a", "attributes": {"list": [2], "text": "x"}}]
    assert apply_ops(
        rows, [["set", "a", ["attributes", "list", 0], 5], ["del", "a", ["attributes", "text"]]]
    ) == [{"id": "a", "attributes": {"list": [5]}}]
    assert apply_ops(rows, [["add", {"id": "b"}], ["drop", "a"]]) == [{"id": "b"}]
    refused: list[tuple[JsonValue, str]] = [
        (["set", "nope", ["x"], 1], "which is not a record"),
        (["set", "a", ["attributes", "nope", "x"], 1], "cannot be applied"),
        (["set", "a", ["attributes", "list", 5, "x"], 1], "cannot be applied"),
        (["set", "a", ["attributes", "text", "x"], 1], "cannot be applied"),
        (["set", "a", ["attributes", "text", "x", "y"], 1], "cannot be applied"),
        (["set", "a", ["attributes", "list", "x"], 1], "cannot be applied"),
        (["del", "a", ["attributes", "list", 3]], "cannot be applied"),
        (["set", "a", [], 1], "is not an operation"),
        (["move", "a"], "is not an operation"),
        (["add", "a"], "is not an operation"),
        ([], "is not an operation"),
        ("set", "is not an operation"),
        (["set", "a"], "cannot be applied"),
    ]
    for op, why in refused:
        with pytest.raises(StoreError, match=why):
            apply_ops(rows, [op])
    assert rows == [{"id": "a", "attributes": {"list": [1], "text": "x"}}]


# ---- descriptions ---------------------------------------------------------------------


def test_the_description_rules_are_the_ones_the_typescript_declares() -> None:
    assert [
        [rule.id, rule.kind, rule.source, rule.flags, rule.message] for rule in DESCRIPTION_RULES
    ] == CONTRACT["description_rules"]
    assert list(descriptions.DESCRIBED_KINDS) == CONTRACT["described_kinds"]
    assert list(descriptions.DISTINCT_KINDS) == CONTRACT["distinct_kinds"]
    assert list(concerns.CONCERN_TARGET_KINDS) == CONTRACT["concern_target_kinds"]
    assert list(blocks.PRIVATE_FIELD_NAMES) == CONTRACT["private_field_names"]


def described(text: str, kind: str = "model", **members: str) -> Record:
    row = {
        "id": "d",
        "kind": kind,
        "name": "D",
        "description": text,
        "status": "needs_review",
        "facets": {},
        "source_ids": [],
        "links": [],
        "attributes": {},
    }
    return parse_record({**row, **members})


@pytest.mark.parametrize(
    ("text", "rules", "normalised"),
    VALIDATION["descriptions"],
    ids=[repr(case[0])[:40] for case in VALIDATION["descriptions"]],
)
def test_a_description_breaks_the_rules_the_typescript_says_it_breaks(
    text: str, rules: list[str], normalised: str
) -> None:
    problems = description_problems([described(text)])
    assert [problem.rule for problem in problems] == rules
    assert normalise_description(text) == normalised
    by_id = {rule.id: rule for rule in DESCRIPTION_RULES}
    assert [problem.row() for problem in problems] == [
        {"id": "d", "kind": "model", "description": text, "rule": rule, "message": by_id[rule].message}
        for rule in rules
    ]


def test_every_rule_is_broken_by_some_recorded_description_and_most_break_none() -> None:
    broken = Counter(rule for _, rules, _ in VALIDATION["descriptions"] for rule in rules)
    assert set(broken) == {rule.id for rule in DESCRIPTION_RULES}
    assert sum(not rules for _, rules, _ in VALIDATION["descriptions"]) > 30


def test_a_shared_description_names_the_records_it_is_shared_with() -> None:
    one = described("Predicts splicing.", id="one", name="One")
    two = described(" predicts  SPLICING. ", id="two", name="Two")
    same_name = described("Predicts splicing.", id="three", name=" one ")
    configuration = described("Predicts splicing.", "configuration", id="four", name="Four")
    excluded = described("Predicts splicing.", id="five", name="Five", status="excluded")
    problems = description_problems([one, two, same_name, configuration, excluded])
    assert [(problem.id, problem.rule, problem.shared_with) for problem in problems] == [
        ("one", "shared-description", ("two",)),
        ("two", "shared-description", ("one", "three")),
        ("three", "shared-description", ("two",)),
    ]
    assert problems[0].row()["shared_with"] == ["two"]


@given(st.text(max_size=60))
def test_a_description_compares_the_same_however_it_is_spaced_or_cased(text: str) -> None:
    normalised = normalise_description(text)
    assert normalise_description(f" \t{text}\n") == normalised
    assert normalise_description(normalised) == normalise_description(normalised.lower())
    assert "  " not in normalised


# ---- private fields, concerns, derivations --------------------------------------------


@pytest.mark.parametrize(("text", "recorded"), VALIDATION["private"])
def test_a_private_field_is_found_wherever_the_typescript_finds_one(
    text: str, recorded: JsonObject | None
) -> None:
    assert outcome(lambda: blocks.assert_no_private_fields(value_of(text))) == recorded


@given(json_values, st.sampled_from(blocks.PRIVATE_FIELD_NAMES))
def test_a_private_field_is_refused_at_any_depth_and_in_any_case(value: JsonValue, name: str) -> None:
    held = js_value(value)
    with pytest.raises(RecordError, match=r"^Private data cannot enter a public catalogue release\.$"):
        blocks.assert_no_private_fields({"a": [held, {name.upper(): held}]})
    with pytest.raises(RecordError, match=r"^no$"):
        blocks.assert_no_private_fields([{name: None}], "no")


def source(record_id: str, entries: JsonValue) -> Record:
    return parse_record(
        {
            "id": record_id,
            "kind": "source",
            "name": "S",
            "description": "",
            "status": "discovered",
            "facets": {},
            "source_ids": [],
            "links": [],
            "attributes": {"evidence_concerns": entries},
        }
    )


def test_a_concern_covers_the_whole_source_unless_it_names_what_it_covers() -> None:
    assert concerns.entries(None) == []
    assert concerns.entries(source("s", "text")) == []
    whole = source("s", ["a problem", "  ", 5, None, {"message": "m"}, ["x"]])
    assert concerns.entries(whole) == [{"message": "a problem"}, {"message": "m"}, ["x"]]
    assert concerns.source_wide_concern(whole)
    scoped = source("t", [{"applies_to": ["r", 5]}, {"applies_to": ["e"]}])
    assert not concerns.source_wide_concern(scoped)
    assert not concerns.source_wide_concern(None)
    assert concerns.scope({"applies_to": ["r", 5]}) == ["r", "5"]
    assert concerns.scope({"applies_to": []}) is None
    with pytest.raises(JsTypeError):
        concerns.scope({"applies_to": [{"toString": 1}]})
    assert concerns.scope(["x"]) is None
    assert concerns.concern_targets([whole, scoped, described("x")]) == {"r", "5", "e"}
    assert concerns.concern_problems([whole, scoped]) == [
        "t: applies_to must be a non-empty list of record IDs",
        "t: applies_to names e, which does not exist",
    ]


def test_a_derivation_script_is_read_below_the_root_whatever_its_path(scripts: Path) -> None:
    derived = ROLES["derived"]
    script = row_of(derived)["attributes"]["derivation"]["script"]
    outside = apply_ops(BASE_ROWS, [["set", derived, ["attributes", "derivation", "script"], "/etc/hosts"]])
    assert derivation_issues(records_of(outside), scripts) == [
        f"result {derived}: derivation script /etc/hosts does not exist"
    ]
    # Node joins a path that starts with a slash below the root, and so does this.
    slashed = apply_ops(BASE_ROWS, [["set", derived, ["attributes", "derivation", "script"], f"/{script}"]])
    assert derivation_issues(records_of(slashed), scripts) == []
    folder = apply_ops(BASE_ROWS, [["set", derived, ["attributes", "derivation", "script"], "scripts"]])
    with pytest.raises(IsADirectoryError):
        derivation_issues(records_of(folder), scripts)


def test_a_derivation_script_is_named_on_disk_as_node_names_it(tmp_path: Path) -> None:
    # Node names a file in UTF-8 with U+FFFD for half a character, so that is the file it reads.
    derived = ROLES["derived"]
    held = row_of(derived)["attributes"]["derivation"]["script"]
    write_scripts(tmp_path, {"scripts/a\ufffdb.py": BASE_FILES[held]})
    halved = apply_ops(
        BASE_ROWS, [["set", derived, ["attributes", "derivation", "script"], "scripts/a\ud800b.py"]]
    )
    assert derivation_issues(records_of(halved), tmp_path) == []
    # A name no file system can hold is a file that does not exist, and not an error.
    for name in ("scripts/" + "a" * 300 + ".py", "scripts/" * 900 + "a.py", "scripts/a\x00b.py"):
        unheld = apply_ops(BASE_ROWS, [["set", derived, ["attributes", "derivation", "script"], name]])
        assert derivation_issues(records_of(unheld), tmp_path) == [
            f"result {derived}: derivation script {name} does not exist"
        ]


@pytest.mark.parametrize(("text", "written"), SEMANTICS["compared"])
def test_a_comparison_is_read_for_a_panel_as_javascript_reads_it(text: str, written: list[Any]) -> None:
    # JSON has no word for undefined, so the recorder wrote an object where JavaScript answered it.
    recorded = [None if isinstance(held, dict) else held for held in written]
    assert [
        compared(value_of(text), field) for field in ("split", "subset", "population", "aggregation")
    ] == recorded


def test_a_comparison_beyond_every_double_is_compared_as_the_null_javascript_writes() -> None:
    assert compared(value_of('{"subset":1e400}'), "subset") == "null"
    assert compared(value_of('{"subset":[1e400,{"a":-1e400}]}'), "subset") == '[null,{"a":null}]'


# ---- validateRecords, where the corpus is not the whole story --------------------------


def test_the_rules_of_one_record_are_checked_in_the_typescripts_order() -> None:
    records = records_of(BASE_ROWS)
    by_id = {record.id: record for record in records}
    for record in records:
        check_record_rules(record, by_id)
    with pytest.raises(RecordError, match=r"^Duplicate record ID$"):
        validate_records([records[0], records[0]])
    assert validate_records([]) == {}


def test_a_value_only_javascript_fails_on_is_refused_as_a_type_error() -> None:
    rows = apply_ops(BASE_ROWS, [["set", ROLES["kind_source"], ["attributes", "url"], {"toString": 1}]])
    assert outcome(lambda: validate_records(by_id_order(records_of(rows)))) == {"name": "TypeError"}
    cited = row_of(ROLES["guide"])["attributes"]["run_guide"]["source_ids"][0]
    rows = apply_ops(BASE_ROWS, [["set", cited, ["attributes", "artifact_sha256"], {"toString": 1}]])
    assert outcome(lambda: validate_records(by_id_order(records_of(rows)))) == {"name": "TypeError"}


def test_a_source_whose_host_only_idna_can_judge_is_not_judged() -> None:
    # Node reads this URL. The port does not carry the table that says so, and says that.
    rows = apply_ops(
        BASE_ROWS, [["set", ROLES["kind_source"], ["attributes", "url"], "https://b\u00fccher.example/"]]
    )
    with pytest.raises(UndecidedHost):
        validate_records(by_id_order(records_of(rows)))


def test_an_artifact_hash_is_read_as_javascript_writes_the_value() -> None:
    hash_ = "a" * 64
    for held, expected in [
        (hash_, True),
        ([hash_], True),
        ([[hash_]], True),
        (hash_.upper(), False),
        ([hash_, hash_], False),
        (5, False),
    ]:
        record = parse_record(
            {
                "id": "s",
                "kind": "source",
                "name": "S",
                "description": "",
                "status": "discovered",
                "facets": {},
                "source_ids": [],
                "links": [],
                "attributes": {"artifact_sha256": held},
            }
        )
        assert blocks.pinned(record) is expected
    assert blocks.pinned(described("x")) is False


# ---- comparison panels -----------------------------------------------------------------


def test_the_query_reads_the_records_in_their_current_shapes() -> None:
    records = records_of(BASE_ROWS)
    current = current_records(records)
    assert all(now is record for now, record in zip(current, records, strict=True))
    older = apply_ops(
        BASE_ROWS, [["set", ROLES["kind_source"], ["attributes", "review_scope"], "the tables"]]
    )
    changed = current_records(records_of(older))
    (moved,) = [now for now, row in zip(changed, older, strict=True) if now.row() != js_value(row)]
    assert moved.id == ROLES["kind_source"]
    assert "review_scope" not in moved.attributes
    review = moved.attributes["review"]
    assert isinstance(review, dict)
    assert review["scope"] == "the tables"
    assert moved == parse_record(moved.row())


def test_a_record_shows_its_own_panels_and_those_of_the_records_below_it() -> None:
    panel = row_of(ROLES["panel_parent"])["attributes"]["comparison_panels"][0]
    child = panel["protocol_id"]
    another = {**panel, "id": "another-panel"}
    rows = apply_ops(BASE_ROWS, [["set", child, ["attributes", "comparison_panels"], [panel, another]]])
    records = by_id_order(records_of(rows))
    shown = resolve_panels(records)
    own = [held.id for held in shown[ROLES["panel_parent"]] or []]
    assert own[-1] == "another-panel"
    assert own.count(panel["id"]) == 1
    assert [held.id for held in shown[child] or []] == [panel["id"], "another-panel"]
    query = Query(records)
    assert query.comparisons("no-such-record") is None
    (resolved, *_) = query.resolve(query.by_id[child])
    assert isinstance(resolved, Resolved)
    assert resolved.result_ids == panel["result_ids"]
    assert query.resolve(query.by_id[child]) is query.resolve(query.by_id[child])


def test_a_record_with_no_panels_has_none_and_a_store_with_none_is_not_asked() -> None:
    records = records_of(BASE_ROWS)
    assert panels_of(next(record for record in records if record.kind == "source")) == []
    owners = [row["id"] for row in BASE_ROWS if "comparison_panels" in row["attributes"]]
    without = apply_ops(BASE_ROWS, [["del", owner, ["attributes", "comparison_panels"]] for owner in owners])
    assert resolve_panels(records_of(without)) == {}


def test_what_a_record_rolls_up_to_follows_checked_links_upwards_only() -> None:
    parent = ROLES["panel_parent"]
    child = row_of(parent)["attributes"]["comparison_panels"][0]["protocol_id"]
    suite = row_of(ROLES["panel_self"])["links"][0]["target_id"]
    query = Query(by_id_order(records_of(BASE_ROWS)))
    assert query.ancestors(child) == {child, parent}
    assert query.ancestors(parent) == {parent}
    assert query.ancestors(ROLES["panel_self"]) == {ROLES["panel_self"], suite}
    assert query.ancestors(child) is query.ancestors(child)
    # A model's links are not followed: no assessment record can be reached through one.
    assert query.ancestors(ROLES["described"]) == {ROLES["described"]}
    assert query.ancestors("no-such-record") == {"no-such-record"}


def _positions(value: object, at: tuple[JsonValue, ...]) -> list[tuple[JsonValue, ...]]:
    below: list[tuple[JsonValue, ...]] = [at]
    if isinstance(value, dict):
        for key, held in value.items():
            below.extend(_positions(held, (*at, key)))
    elif isinstance(value, list):
        for index, held in enumerate(value):
            below.extend(_positions(held, (*at, index)))
    return below


#: Every place in the attributes of every record of the base store, by record and path.
POSITIONS = [
    (row["id"], list(path))
    for row in BASE_ROWS
    for path in _positions(row["attributes"], ("attributes",))
    if len(path) > 1
]


@given(st.sampled_from(POSITIONS))
def test_a_number_beyond_every_double_is_refused_or_accepted_and_never_stops_a_validator(
    scripts: Path, position: tuple[str, list[JsonValue]]
) -> None:
    # JSON.parse reads 1e400 as infinity, wherever it stands, and each validator then either
    # refuses the record or does not. None of them may fail on being unable to write it.
    record_id, path = position
    rows = apply_ops(BASE_ROWS, [["set", record_id, path, math.inf]])
    try:
        records = records_of(rows)
    except SchemaError:
        return
    for runner in stage_runners(scripts).values():
        with contextlib.suppress(RecordError, SchemaError):
            runner(records)
