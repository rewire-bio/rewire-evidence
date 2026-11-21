"""The seven report-only checks, held to what the TypeScript pack does.

The cases are what `scripts/omics/checks/` did at the pinned commit, recorded in
`tests/fixtures/checks.json`: small stores run through the whole pack, the records
`normalizeAttributes` rewrites, checkouts read by `loadInput`, and the numbers, lines and
locators the checks are built on.
"""

from __future__ import annotations

import json
import math
import re
from fractions import Fraction
from pathlib import Path
from random import Random
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import checks
from rewire_evidence.canonical import JsonObject, JsonValue, js_number, utf16_key
from rewire_evidence.checks import (
    CHECKS,
    Check,
    CheckInput,
    CheckSummary,
    Finding,
    MetricConcept,
    RunResult,
    SchemeConcept,
    UnitConcept,
    anchor_of,
    changed_keys,
    column_of,
    columns,
    count_by_rule,
    gathered,
    js_round,
    load_input,
    majority_units,
    one_line,
    power_of_ten,
    read_scheme,
    report_tsv,
    rounds_to,
    run_all,
    same,
    subject_of,
    text,
    tsv_row,
)
from rewire_evidence.js import number_of_text
from rewire_evidence.jsonl import StoreError
from rewire_evidence.vocab import VocabularyError
from tests.checked import CHECKED, given_of, lay_out, named, only_of

CONSTANTS: dict[str, Any] = CHECKED["constants"]
RUNS = named(CHECKED["runs"])
NORMALIZE = named(CHECKED["normalize"])
CHECKOUTS = named(CHECKED["checkouts"])
ANY_TEXT = st.text(st.characters(codec=None))
FINITE = st.floats(allow_nan=False, allow_infinity=False)


def shown(value: float) -> str:
    """A number as the recorder writes one: `String(value)`, with the sign of a zero kept."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return "-0" if value == 0 and math.copysign(1, value) < 0 else js_number(value)


def ran(case: dict[str, Any]) -> RunResult:
    return run_all(given_of(case), only_of(case))


# ---------------------------------------------------------------------------------------
# What the pack is.


def test_the_pack_is_the_typescripts_checks_in_its_order_with_its_rules() -> None:
    assert [[check.id, check.title, list(check.rules), check.context is not None] for check in CHECKS] == (
        CONSTANTS["checks"]
    )
    assert [check.id for check in CHECKS] == ["C1", "C2", "C3", "C4", "C5", "C9", "C13"]
    assert sum(len(check.rules) for check in CHECKS) == 16
    assert CONSTANTS["tsv_header"] == checks.TSV_HEADER
    assert CONSTANTS["tolerance"] == checks.TOLERANCE


def test_the_scope_note_rules_and_the_tokens_are_the_typescripts() -> None:
    assert [
        {"note": rule.note, "unit": rule.unit, "metrics": list(rule.metrics), "reason": rule.reason}
        for rule in checks.SCOPE_NOTE_RULES
    ] == CONSTANTS["scope_note_rules"]
    assert list(checks.TOKENS) == CONSTANTS["tokens"]


def test_the_patterns_are_the_typescripts_with_their_groups_named_or_dropped() -> None:
    # A group JavaScript numbers is named here, which changes nothing it matches.
    unnamed = re.compile(r"\(\?<[a-z]+>")
    for held, recorded in ((checks.ANCHOR, CONSTANTS["anchor"]), (checks.COLUMN, CONSTANTS["column"])):
        assert [held.flags, unnamed.sub("(", held.source)] == recorded
    assert unnamed.findall(checks.COLUMN.source) == [
        "(?<group>",
        "(?<double>",
        "(?<single>",
        "(?<round>",
        "(?<bare>",
    ]
    # The two groups of a token's pattern are only tested, so they capture nothing here.
    assert [
        [token, pattern.flags, pattern.source.replace("(?:^|", "(^|").replace("(?:$|", "($|")]
        for token, pattern in checks.TOKEN_PATTERNS
    ] == CONSTANTS["matchers"]
    assert len(checks.PATTERNS) == len({(held.source, held.flags) for held in checks.PATTERNS}) == 13


# ---------------------------------------------------------------------------------------
# Numbers.


@pytest.mark.parametrize("case", CHECKED["rounded"], ids=lambda case: case[0])
def test_a_number_is_rounded_as_math_round_rounds_it(case: list[str]) -> None:
    value, rounded = case
    assert shown(js_round(number_of_text(value))) == rounded


def test_rounding_is_neither_pythons_nor_a_half_added_and_floored() -> None:
    assert (js_round(2.5), round(2.5)) == (3.0, 2)
    below_half = 0.49999999999999994
    assert (js_round(below_half), math.floor(below_half + 0.5)) == (0.0, 1)
    assert shown(js_round(-0.2)) == "-0"
    assert shown(js_round(-0.5)) == "-0"
    assert math.isnan(js_round(math.nan))


@given(FINITE)
def test_rounding_gives_the_nearest_whole_number_and_the_larger_of_two(value: float) -> None:
    rounded = js_round(value)
    assert rounded == math.floor(rounded)
    assert Fraction(-1, 2) < Fraction(rounded) - Fraction(value) <= Fraction(1, 2)
    # A number that rounds to zero keeps its sign, and no other rounding changes one.
    assert math.copysign(1, rounded) == math.copysign(1, value) or rounded != 0 or value > 0


@pytest.mark.parametrize("case", CHECKED["powers"], ids=lambda case: str(case[0]))
def test_a_power_of_ten_is_the_one_node_gives(case: list[Any]) -> None:
    places, power = case
    assert shown(power_of_ten(places)) == power


def test_the_powers_of_ten_are_recorded_because_two_are_not_the_nearest_double() -> None:
    held = json.loads((Path(checks.__file__).parent / checks.POWERS_FILE).read_text("utf-8"))
    assert held["recorded_from"] == {
        "script": "scripts/fixtures/record_checks_fixture.mjs",
        "node": CHECKED["recorded_from"]["node"],
    }
    assert len(held["powers"]) == 309
    assert held["powers"] == [power for _, power in CHECKED["powers"][:309]]
    assert {power for _, power in CHECKED["powers"][309:]} == {"Infinity"}
    out = [places for places in range(309) if power_of_ten(places) != float(f"1e{places}")]
    assert out == [23, 210]
    assert shown(power_of_ten(23)) == "1.0000000000000001e+23"
    assert power_of_ten(10_000) == math.inf


@pytest.mark.parametrize("case", CHECKED["same"], ids=lambda case: f"{case[0]} and {case[1]}")
def test_two_numbers_are_the_same_where_the_typescript_says_so(case: list[Any]) -> None:
    a, b, said = case
    assert same(number_of_text(a), number_of_text(b)) is said


def test_an_infinity_is_the_same_as_any_number_as_it_is_in_the_typescript() -> None:
    recorded = {(a, b): said for a, b, said in CHECKED["same"]}
    assert recorded["Infinity", "1"] is recorded["1", "Infinity"] is recorded["-Infinity", "5"] is True
    assert recorded["Infinity", "-Infinity"] is True
    assert recorded["NaN", "NaN"] is recorded["NaN", "Infinity"] is False


@given(st.floats(), st.floats())
def test_sameness_does_not_depend_on_the_order_of_the_two(a: float, b: float) -> None:
    assert same(a, b) is same(b, a)
    assert same(a, a) is not math.isnan(a)
    assert same(a, b) is same(-a, -b)


@pytest.mark.parametrize("case", CHECKED["roundings"], ids=lambda case: f"{case[0]} to {case[1][:24]}")
def test_a_value_rounds_to_a_printed_decimal_where_the_typescript_says_so(case: list[Any]) -> None:
    value, printed, said = case
    assert rounds_to(number_of_text(value), printed) is said


@given(st.integers(-(10**9), 10**9), st.integers(0, 6))
def test_a_decimal_rounds_to_itself_and_never_to_an_exponent(digits: int, places: int) -> None:
    printed = f"{digits / 10**places:.{places}f}"
    assert rounds_to(float(printed), printed)
    assert not rounds_to(float(printed), f"{printed}e0")


# ---------------------------------------------------------------------------------------
# Lines and locators.


@pytest.mark.parametrize("case", CHECKED["lines"], ids=lambda case: repr(case[0]))
def test_text_is_made_one_line_as_the_typescript_makes_it(case: list[str]) -> None:
    assert one_line(case[0]) == case[1]


@given(ANY_TEXT)
def test_one_line_has_no_tab_or_line_end_and_is_not_changed_by_being_made_again(value: str) -> None:
    line = one_line(value)
    assert not re.search(r"[\t\r\n]", line)
    assert one_line(line) == line


@pytest.mark.parametrize("case", CHECKED["locators"], ids=lambda case: repr(case[0]))
def test_a_locator_gives_the_anchor_and_the_column_the_typescript_reads(case: list[Any]) -> None:
    locator, anchor, column = case
    assert (anchor_of(locator), column_of(locator)) == (anchor, column)


def test_the_locator_cases_reach_what_python_would_read_otherwise() -> None:
    read = {locator: (anchor, column) for locator, anchor, column in CHECKED["locators"]}
    # A long s is not an s to JavaScript without the u flag, and a digit is an ASCII one.
    assert read["Tab\u017fe 1, column 'x'"][0] is None
    assert read["Table \u0661, column 'x'"][0] is None
    # White space is JavaScript's, which has U+FEFF and not U+0085.
    assert read["\ufeffTable 7, column 'x'"][0] == "table 7"
    # Lowercase is JavaScript's: a dotted capital I becomes two characters, a final sigma its own.
    assert read["Table 1, column '\u0130'"][1] == "i\u0307"
    assert read["Table 1, column '\u0391\u03a3'"][1] == "\u03b1\u03c2"
    assert read["Table 1, column '\U00010400'"][1] == "\U00010428"
    assert read["Table 1, column group 'G', column 'x'"][1] == "x"
    assert read["Table 1, column group 'G'"][1] is None
    assert sum(anchor is None for anchor, _ in read.values()) > 15
    assert sum(column is None for _, column in read.values()) > 15


@given(ANY_TEXT)
def test_any_locator_is_read_without_failing(locator: str) -> None:
    anchor, column = anchor_of(locator), column_of(locator)
    assert anchor is None or anchor == anchor.strip() != ""
    assert column is None or column != ""


# ---------------------------------------------------------------------------------------
# The pack over small stores.


@pytest.mark.parametrize("name", [name for name, case in RUNS.items() if "error" not in case])
def test_a_small_store_gets_the_findings_the_typescript_gives_it(name: str) -> None:
    case = RUNS[name]
    run = ran(case)
    assert [finding.row() for finding in run.findings] == case["findings"]
    assert [summary.row() for summary in run.summaries] == case["summaries"]
    # The order of the counts is the order JavaScript lists them in.
    for summary, recorded in zip(run.summaries, case["summaries"], strict=True):
        assert list(summary.rules) == list(recorded["rules"])
        assert list(summary.context) == list(recorded["context"])
    assert report_tsv(run.findings) == case["tsv"]


def test_the_small_stores_raise_every_rule_the_pack_declares() -> None:
    raised = {
        f"{finding['check']}.{finding['rule']}"
        for case in [*RUNS.values(), *NORMALIZE.values()]
        for finding in case.get("findings", [])
    }
    declared = {f"{check.id}.{rule}" for check in CHECKS for rule in check.rules}
    assert raised == declared
    assert len(RUNS) == 19
    assert sum(len(case.get("findings", [])) for case in RUNS.values()) > 140


def test_a_record_with_no_links_stops_the_typescript_and_is_refused_here() -> None:
    case = RUNS["records with no attributes, and attributes that are not an object, given to the whole pack"]
    assert case["error"] == {
        "name": "TypeError",
        "message": "Cannot read properties of undefined (reading 'find')",
    }
    with pytest.raises(
        StoreError, match="the record q-1 has a kind that is not text or links that are not a list"
    ):
        ran(case)
    # Every other check takes the same records, as the case before it records.
    quiet = RUNS["records with no attributes, and attributes that are not an object"]
    assert quiet["only"] == ["C1", "C2", "C3", "C4", "C5", "C9"]
    assert quiet["findings"] == []
    unread: list[JsonObject] = [{"id": "a", "kind": ["model"], "links": []}, {"id": "a", "kind": "model"}]
    for record in unread:
        with pytest.raises(StoreError, match="the record a has a kind"):
            run_all(gathered([record], {}, {}), [checks.NORMALISE_OR_REFUSE])


@pytest.mark.parametrize("name", list(NORMALIZE))
def test_a_record_normalisation_would_rewrite_is_reported_as_the_typescript_reports_it(name: str) -> None:
    case = NORMALIZE[name]
    given_ = gathered(case["records"], {}, {})
    if "error" in case:
        assert case["error"] == {"name": "Error", "message": "r: license and licence differ"}
        with pytest.raises(Exception, match="r: license and licence differ"):
            run_all(given_, [checks.NORMALISE_OR_REFUSE])
        return
    run = run_all(given_, [checks.NORMALISE_OR_REFUSE])
    assert [finding.row() for finding in run.findings] == case["findings"]
    assert [summary.row() for summary in run.summaries] == case["summaries"]
    assert report_tsv(run.findings) == case["tsv"]


def test_the_rewritten_records_say_which_attributes_would_change() -> None:
    details = [finding["detail"] for case in NORMALIZE.values() for finding in case.get("findings", [])]
    assert len(details) > 75
    assert any(", " in detail for detail in details)
    assert sum("findings" in case and not case["findings"] for case in NORMALIZE.values()) > 10
    # The TypeScript can say a record's attributes would only change order, and no case has it
    # say so: JavaScript lists a name that is a whole number first, so no record it reads is in
    # another order. A row made here can be, and is reported in those words.
    assert "normalizeAttributes would rewrite the attribute order" not in details
    unordered: JsonObject = {"id": "r", "kind": "model", "links": [], "attributes": {"b": 1, "2": "x"}}
    run = run_all(gathered([unordered], {}, {}), [checks.NORMALISE_OR_REFUSE])
    assert [finding.detail for finding in run.findings] == [
        "normalizeAttributes would rewrite the attribute order"
    ]


def test_changed_keys_are_the_ones_added_dropped_or_changed_in_text_order() -> None:
    before: JsonObject = {"b": 1, "a": {"x": 1, "y": 2}, "\uff5a": 1, "gone": None, "same": [1, 2]}
    after: JsonObject = {
        "same": [1, 2],
        "a": {"y": 2, "x": 1},
        "\U0001f600": 1,
        "\uff5a": 2,
        "b": 1.0,
        "new": None,
    }
    # Half of a character beyond U+FFFF sorts before U+FF5A, as JavaScript sorts text.
    assert changed_keys(before, after) == ["a", "gone", "new", "\U0001f600", "\uff5a"]
    assert changed_keys({"a": None}, {}) == ["a"]
    assert changed_keys({}, {}) == []


# ---------------------------------------------------------------------------------------
# Reading a checkout.


@pytest.mark.parametrize("name", [name for name, case in CHECKOUTS.items() if case["error"] is None])
def test_a_checkout_is_read_as_the_typescript_reads_it(name: str, tmp_path: Path) -> None:
    case = CHECKOUTS[name]
    read = load_input(lay_out(tmp_path, case["files"]))
    assert {
        "records": len(read.records),
        "results": len(read.results),
        "sources": len(read.sources),
        "metric_concepts": len(read.metrics),
        "unit_concepts": len(read.units),
    } == case["store"]
    assert [[held.key, held.default_direction] for held in read.metrics.values()] == case["metrics"]
    assert [[held.key, held.scope_note] for held in read.units.values()] == case["units"]
    assert all(key == held.key for key, held in read.metrics.items())
    assert all(key == held.key for key, held in read.units.items())
    run = run_all(read)
    assert [finding.row() for finding in run.findings] == case["run"]["findings"]
    assert [summary.row() for summary in run.summaries] == case["run"]["summaries"]
    assert report_tsv(run.findings) == case["run"]["tsv"]


@pytest.mark.parametrize("name", [name for name, case in CHECKOUTS.items() if case["error"] is not None])
def test_a_checkout_the_typescript_cannot_read_is_refused(name: str, tmp_path: Path) -> None:
    case = CHECKOUTS[name]
    with pytest.raises((StoreError, VocabularyError)) as refused:
        load_input(lay_out(tmp_path, case["files"]))
    if "n3 refuses" in name:
        assert str(refused.value) == case["error"]["message"]


def test_the_checkouts_reach_every_way_a_concept_is_stated() -> None:
    odd = CHECKOUTS["vocabularies with concepts stated oddly"]
    metrics = dict(odd["metrics"])
    # The first direction a concept is given is the one read, and its last step is the direction.
    assert metrics["two-directions"] == "lower"
    assert metrics["later"] == "sideways"
    assert metrics["text-direction"] is None
    assert metrics["slash-direction"] == ""
    assert metrics["no-slash"] == "urn:higher"
    assert metrics["a/b"] is None
    assert "elsewhere" not in metrics
    # A subject that is not in the scheme's own namespace is cut short all the same.
    assert "" in metrics
    units = dict(odd["units"])
    assert units["two-notes"] == "first"
    assert units["fraction"] == "A note that comes before the concept is placed."
    assert units["directed"] is None
    assert [case["error"]["name"] for case in CHECKOUTS.values() if case["error"]] == [
        "Error",
        "Error",
        "Error",
        "SyntaxError",
    ]


def test_a_scheme_file_gives_one_concept_for_each_statement_placing_a_subject_in_it(tmp_path: Path) -> None:
    file = tmp_path / "metric.ttl"
    file.write_text(
        CHECKOUTS["vocabularies with concepts stated oddly"]["files"]["data/vocab/metric.ttl"], "utf-8"
    )
    concepts = read_scheme(file, "metric")
    assert concepts.count(SchemeConcept("twice", "higher", None)) == 2
    assert SchemeConcept("two-directions", "lower", "a note on a metric") in concepts
    assert read_scheme(file, "unit") == [SchemeConcept("c/elsewhere", "higher", None)]
    with pytest.raises(VocabularyError, match="cannot be read"):
        read_scheme(tmp_path / "missing.ttl", "metric")


# ---------------------------------------------------------------------------------------
# The parts the checks are made of.


def result(record_id: str, **attributes: JsonValue) -> JsonObject:
    return {"id": record_id, "kind": "result", "source_ids": ["s"], "links": [], "attributes": attributes}


def test_a_text_attribute_is_read_only_where_it_is_text() -> None:
    assert text(result("r", metric="auroc"), "metric") == "auroc"
    assert text(result("r", metric=5), "metric") is None
    assert text(result("r"), "metric") is None
    not_objects: list[JsonValue] = [None, "metric", ["metric"], 5]
    for attributes in not_objects:
        assert text({"id": "r", "attributes": attributes}, "metric") is None
    assert text({"id": "r"}, "metric") is None


def test_a_record_whose_id_is_not_text_is_refused() -> None:
    assert subject_of({"id": "r"}) == "r"
    unnamed: list[JsonObject] = [{"id": 5}, {"id": None}, {}, {"id": ["r"]}]
    for record in unnamed:
        with pytest.raises(StoreError, match="which is not text"):
            subject_of(record)
    numbered: JsonObject = {"id": 5, "kind": "result", "attributes": {"metric_qualifier": "no"}}
    with pytest.raises(StoreError, match="has the ID 5, which is not text"):
        run_all(gathered([numbered], {}, {}))


def test_source_ids_that_are_not_a_list_are_refused() -> None:
    record = result("r", source_locator="Table 1, column 'a'")
    record["source_ids"] = "s1"
    with pytest.raises(StoreError, match="the record r has source IDs that are not a list"):
        columns([record])
    # With no anchor or no column the sources are never read.
    record["attributes"] = {"source_locator": "Table 1"}
    assert columns([record]).no_column == 1


def test_a_finding_and_a_summary_are_written_in_the_typescripts_order() -> None:
    finding = Finding("C4", "a-rule", "an-id", "a\tdetail\n")
    assert list(finding.row()) == ["check", "rule", "severity", "subject", "detail"]
    assert finding.severity == checks.REPORT == "report"
    assert tsv_row(finding) == "C4\ta-rule\treport\tan-id\ta detail"
    assert report_tsv([]) == f"{checks.TSV_HEADER}\n"
    assert report_tsv([finding, finding]).count("\n") == 3
    summary = CheckSummary("C4", "t", 1, {"a-rule": 1}, {"seen": 2})
    assert list(summary.row()) == ["id", "title", "findings", "rules", "context"]


def test_rules_are_counted_with_every_declared_one_present_and_in_javascripts_order() -> None:
    check = Check("X", "a check", ("b", "a"), lambda _given: [])
    raised = [Finding("X", rule, "s", "d") for rule in ("a", "10", "undeclared", "a", "9")]
    counts = count_by_rule(check, raised)
    # A name that is a whole number comes first, by its value, as in a JavaScript object.
    assert list(counts.items()) == [("9", 1), ("10", 1), ("b", 0), ("a", 2), ("undeclared", 1)]


def test_a_check_with_nothing_to_say_of_its_run_has_no_context() -> None:
    quiet = Check("X", "a check", ("r",), lambda _given: [Finding("X", "r", "s", "d")])
    loud = Check("Y", "another", ("r",), lambda _given: [], lambda _given, _found: {"b": 1, "2": 2, "a": 3})
    run = run_all(gathered([], {}, {}), [quiet, loud])
    assert run.summaries == [
        CheckSummary("X", "a check", 1, {"r": 1}, {}),
        CheckSummary("Y", "another", 0, {"r": 0}, {"2": 2, "b": 1, "a": 3}),
    ]
    assert list(run.summaries[1].context) == ["2", "b", "a"]
    assert run_all(gathered([], {}, {}), []) == RunResult([], [])


def test_the_majority_unit_is_the_commonest_where_it_is_at_least_half() -> None:
    def held(*units: str) -> list[JsonObject]:
        return [result(f"r{index}", metric="m", unit=unit) for index, unit in enumerate(units)]

    assert majority_units(held("a", "a", "b")) == {"m": ("a", 2)}
    assert majority_units(held("b", "a")) == {"m": ("a", 1)}
    assert majority_units(held("a", "b", "c")) == {}
    assert majority_units(held("a", "a")) == {}
    assert majority_units(held("\uff5a", "\U0001f600")) == {"m": ("\U0001f600", 1)}
    assert utf16_key("\U0001f600") < utf16_key("\uff5a")
    # In characters the order is the other way about, which is what Python's own sort would use.
    assert sorted(["\U0001f600", "\uff5a"]) == ["\uff5a", "\U0001f600"]


# ---------------------------------------------------------------------------------------
# Properties of the whole pack.

WORDS = st.sampled_from(
    ["auroc", "rmse", "count", "accuracy", "nothing", "", "percent", "fraction", "higher"]
)
VALUES = st.one_of(
    WORDS, st.sampled_from(["0.5", "50", "0.13", "0.125", "n/a", "45 (59.2%)", "x", "lower", "unknown"])
)
QUALIFIERS = st.sampled_from(["no", "high", "top-1", "max, yes", "", "low effort"])
LOCATORS = st.sampled_from(["Table 1, column 'a'", "Table 1, column 'b'", "Fig. 2, column a", "Page 3", ""])
ATTRIBUTES = st.fixed_dictionaries(
    {},
    optional={
        "metric": WORDS,
        "unit": WORDS,
        "metric_direction": VALUES,
        "printed_value": VALUES,
        "numeric_value": VALUES,
        "metric_qualifier": QUALIFIERS,
        "source_locator": LOCATORS,
        "artifact_sha256": st.sampled_from(["aa", "bb"]),
        "url": st.sampled_from(["https://a", "https://b"]),
    },
)
VOCABULARY = {name: CHECKED["runs"][0][name] for name in ("metrics", "units")}


@st.composite
def stores(draw: st.DrawFn) -> list[JsonObject]:
    held: list[tuple[str, JsonObject]] = draw(
        st.lists(st.tuples(st.sampled_from(["result", "source", "model"]), ATTRIBUTES), max_size=12)
    )
    return [
        {
            "id": f"r-{index}",
            "kind": kind,
            "status": "s",
            "source_ids": ["s"],
            "links": [],
            "attributes": attributes,
        }
        for index, (kind, attributes) in enumerate(held)
    ]


def over(records: list[JsonObject], only: tuple[Check, ...] = CHECKS) -> RunResult:
    return run_all(given_of({"records": records, **VOCABULARY}), only)


@given(stores())
def test_the_counts_of_a_run_add_up_to_its_findings(records: list[JsonObject]) -> None:
    run = over(records)
    assert [summary.id for summary in run.summaries] == [check.id for check in CHECKS]
    assert sum(summary.findings for summary in run.summaries) == len(run.findings)
    for summary, check in zip(run.summaries, CHECKS, strict=True):
        assert sum(summary.rules.values()) == summary.findings
        assert set(summary.rules) == set(check.rules)
        assert all(
            finding.check == check.id for finding in check.run(given_of({"records": records, **VOCABULARY}))
        )
    assert report_tsv(run.findings).count("\n") == len(run.findings) + 1


@given(stores(), stores())
def test_a_check_that_reads_one_record_at_a_time_reports_two_stores_as_one_after_the_other(
    first: list[JsonObject], second: list[JsonObject]
) -> None:
    alone = (checks.DIRECTION_DEFAULT, checks.PRINTED_VS_NUMERIC, checks.QUALIFIER_SHAPE)
    renamed = [{**record, "id": f"s-{index}"} for index, record in enumerate(second)]
    together = over([*first, *renamed], alone)
    for position in range(len(alone)):
        here = [finding for finding in together.findings if finding.check == alone[position].id]
        apart = (
            over(first, alone[position : position + 1]).findings
            + over(renamed, alone[position : position + 1]).findings
        )
        assert here == apart


@given(stores(), st.randoms(use_true_random=False))
def test_duplicate_sources_are_reported_the_same_in_whatever_order_they_are_stored(
    records: list[JsonObject], random: Random
) -> None:
    shuffled = list(records)
    random.shuffle(shuffled)
    only = (checks.DUPLICATE_ARTIFACT,)
    assert over(shuffled, only) == over(records, only)
    subjects = [finding.subject for finding in over(records, only).findings]
    assert subjects == sorted(subjects, key=utf16_key)


@given(stores())
def test_every_result_is_in_one_column_or_counted_as_in_none(records: list[JsonObject]) -> None:
    results = [record for record in records if record["kind"] == "result"]
    placed = columns(results)
    assert sum(map(len, placed.grouped.values())) + placed.no_anchor + placed.no_column == len(results)
    context = over(records, (checks.COLUMN_COHERENCE,)).summaries[0].context
    assert (
        context["columns_with_more_than_one_key"]
        == over(records, (checks.COLUMN_COHERENCE,)).summaries[0].findings
    )
    assert context["results_in_split_columns"] <= context["results_in_columns"]


def test_the_input_of_a_run_picks_out_the_results_and_the_sources() -> None:
    records: list[JsonObject] = [{"id": "a", "kind": "result"}, {"id": "b", "kind": "source"}, {"id": "c"}]
    read = gathered(records, {"m": MetricConcept("m", None)}, {"u": UnitConcept("u", "n")})
    assert isinstance(read, CheckInput)
    assert (read.results, read.sources, read.records) == (records[:1], records[1:2], records)
