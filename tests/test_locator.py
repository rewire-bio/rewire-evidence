"""The locator grammar, held to what the TypeScript reads.

Every expected value here is something `shared/omics/locator.ts` produced at the pinned
commit, recorded in `tests/fixtures/locator.json`.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import locator
from rewire_evidence.canonical import js_written
from rewire_evidence.js import paired, trim, unit_text
from rewire_evidence.locator import (
    authored_addresses,
    carries_no_address,
    classify_locator,
    coverage_of,
    parse_locator,
    parses_under_legacy,
    render_locator,
    rendered,
    selector_role,
    split_segments,
    verdict_of,
)
from tests.located import LOCATOR, addresses_of, anchors_of, qualifiers_of, selectors_of

HELD: dict[str, Any] = LOCATOR["patterns"]
CASES: list[dict[str, Any]] = LOCATOR["locators"]
#: The two letters that are word characters under the i and u flags together and not under i alone.
LONG_S = chr(0x17F)
KELVIN = chr(0x212A)


def test_the_patterns_are_the_typescripts_character_for_character() -> None:
    assert LOCATOR["recorded_from"]["script"] == "scripts/fixtures/record_locator_fixture.mjs"
    assert [list(token) for token in locator.TOKEN_SOURCES] == HELD["tokens"]
    assert [(name, held.flags, held.source) for name, held in locator.TOKENS] == list(locator.TOKEN_SOURCES)
    for held, name in (
        (locator.ORDINAL_KEY, "ordinal_key"),
        (locator.NOISE, "noise"),
        (locator.NOISE_WORDS, "noise_words"),
        (locator.LEGACY, "legacy"),
    ):
        assert [held.flags, held.source] == HELD[name], name
    assert [locator.LEGACY.flags, locator.LEGACY.source] == LOCATOR["constants"]["legacy"]
    assert len(locator.PATTERNS) == len(locator.TOKENS) + 14


def test_the_tables_of_the_grammar_are_the_typescripts() -> None:
    assert set(HELD["row_keys"]) == locator.ROW_KEYS
    assert set(HELD["column_keys"]) == locator.COLUMN_KEYS
    constants = LOCATOR["constants"]
    assert list(locator.ANCHOR_KINDS) == constants["anchor_kinds"]
    assert list(locator.ADDRESS_KINDS) == constants["address_kinds"]
    assert list(locator.QUALIFIER_KINDS) == constants["qualifier_kinds"]
    assert list(locator.SELECTOR_FORMS) == constants["selector_forms"]
    assert list(locator.VERDICTS) == constants["verdicts"]
    assert list(locator.COVERAGES) == constants["coverages"]
    assert list(locator.LOCATOR_CLASSES) == constants["locator_classes"]
    assert {verdict: coverage_of(verdict) for verdict in locator.VERDICTS} == constants["coverage_of"]


def test_every_token_matches_something_so_none_can_be_taken_for_ever() -> None:
    # The TypeScript stops when the leftmost match is empty. No token can match nothing, so
    # that stop is never reached and this port has none.
    for name, held in locator.TOKENS:
        assert held.least >= 1, name


def test_a_token_names_what_it_is_read_into() -> None:
    families = {"anchor": HELD["anchor_kind"], "addr": HELD["address_kind"], "sel": HELD["selector_form"]}
    for name, _ in locator.TOKENS:
        family = name.split("_")[0]
        if family == "qual":
            assert name in HELD["qualifier_kind"]
        else:
            assert name in families[family]


def test_every_locator_is_read_as_the_typescript_reads_it() -> None:
    assert len(CASES) > 2000
    wrong = []
    for case in CASES:
        printed = case["printed"]
        parsed = parse_locator(printed)
        got = {
            "printed": printed,
            "legacy": parses_under_legacy(printed),
            "segments": split_segments(printed),
            "class": classify_locator(printed),
            "coverage": coverage_of(parsed.verdict),
            "parsed": parsed.row(),
            "rendered": rendered(parsed),
        }
        # Compared as JavaScript writes them, so the order of the members is held too.
        if js_written(got) != js_written(case):
            wrong.append(printed)
    assert wrong == []


def test_the_recorded_locators_reach_every_part_of_the_grammar() -> None:
    parsed = [case["parsed"] for case in CASES]
    assert {anchor["kind"] for case in parsed for anchor in case["anchors"]} == set(locator.ANCHOR_KINDS)
    assert {address["kind"] for case in parsed for address in case["addresses"]} == set(locator.ADDRESS_KINDS)
    assert {held["kind"] for case in parsed for held in case["qualifiers"]} == set(locator.QUALIFIER_KINDS)
    assert {held["form"] for case in parsed for held in case["selectors"]} == set(locator.SELECTOR_FORMS)
    assert {case["verdict"] for case in parsed} == set(locator.VERDICTS)
    assert {case["class"] for case in CASES} == set(locator.LOCATOR_CLASSES)
    assert {case["coverage"] for case in CASES} == set(locator.COVERAGES)
    assert any("container" in anchor for case in parsed for anchor in case["anchors"])
    assert any("value2" in address for case in parsed for address in case["addresses"])
    assert any(case["legacy"] for case in CASES)
    # What the store has none of: a character beyond U+FFFF, half of one, and more tokens
    # in a segment than the grammar takes.
    assert any(any(ord(character) > 0xFFFF for character in case["printed"]) for case in CASES)
    assert any(any(0xD800 <= ord(character) <= 0xDFFF for character in case["printed"]) for case in CASES)
    taken = [
        len(case["anchors"]) + len(case["addresses"]) + len(case["selectors"]) + len(case["qualifiers"])
        for case in parsed
        if len(split_segments(case["printed"])) == 1
    ]
    assert max(taken) == locator.MOST_TOKENS


@pytest.mark.parametrize(("key", "role"), LOCATOR["roles"])
def test_a_key_names_the_row_the_column_or_something_else(key: str, role: str) -> None:
    assert selector_role(key) == role


@pytest.mark.parametrize(("word", "empty"), LOCATOR["words"])
def test_a_word_carries_an_address_or_does_not(word: str, *, empty: bool) -> None:
    assert carries_no_address(word) is empty


@pytest.mark.parametrize("case", LOCATOR["rendering"])
def test_a_locator_is_rendered_from_its_typed_fields(case: dict[str, Any]) -> None:
    fields = case["fields"]
    if "addresses" in fields:
        addresses = addresses_of(fields["addresses"])
    else:
        narrowed = addresses_of(fields.get("refines", []))
        one = addresses_of([fields["address"]])[0] if "address" in fields else None
        addresses = authored_addresses(one, narrowed)
    written = render_locator(
        anchors_of(fields["anchors"]),
        addresses,
        selectors_of(fields["selectors"]),
        qualifiers_of(fields["qualifiers"]),
    )
    assert written == case["rendered"]


def test_the_rendering_cases_cover_every_kind_and_form() -> None:
    fields = [case["fields"] for case in LOCATOR["rendering"]]
    assert {held["kind"] for case in fields for held in case.get("addresses", [])} == set(
        locator.ADDRESS_KINDS
    )
    assert {case["address"]["kind"] for case in fields if "address" in case} == set(locator.ADDRESS_KINDS)
    assert {held["kind"] for case in fields for held in case["anchors"]} == set(locator.ANCHOR_KINDS)
    assert {held["form"] for case in fields for held in case["selectors"]} == set(locator.SELECTOR_FORMS)
    assert any(held["value"] is None for case in fields for held in case["selectors"])
    assert authored_addresses(None) == []


def test_a_verdict_is_the_typescripts_for_every_mix_of_fields() -> None:
    cases = LOCATOR["verdicts"]
    assert len(cases) > 500
    assert {case["verdict"] for case in cases} == set(locator.VERDICTS)
    for case in cases:
        held = case["parsed"]
        verdict = verdict_of(
            anchors_of(held["anchors"]),
            addresses_of(held["addresses"]),
            selectors_of(held["selectors"]),
            held["residue"],
        )
        assert verdict == case["verdict"], held


def test_a_parse_holds_its_fields_in_the_order_javascript_writes_them() -> None:
    parsed = parse_locator("Supplementary Table 9, cells A1:B2, row 'x' (page 2)")
    assert js_written(parsed.row()) == (
        '{"printed":"Supplementary Table 9, cells A1:B2, row \'x\' (page 2)",'
        '"anchors":[{"kind":"table","value":"9","raw":"Supplementary Table 9","container":"Supplementary"}],'
        '"addresses":[{"kind":"cell_range","value":"A1","raw":"cells A1:B2","value2":"B2"}],'
        '"selectors":[{"key":"row","value":"x","form":"quoted","raw":"row \'x\'"}],'
        '"qualifiers":[{"kind":"page","text":"(page 2)"}],"residue":[],"verdict":"partial"}'
    )
    assert parsed.structure() == (
        (("table", "9", "Supplementary"),),
        (("cell_range", "A1", "B2"),),
        (("row", "x", "quoted"),),
        (("page", "(page 2)"),),
    )


PARTS = st.sampled_from(
    [
        "Table S18",
        "Fig. 3b",
        "sheet 'Data'",
        "cell I9",
        "line 12",
        "row 'a'",
        "column 'b'",
        "tool=x",
        "(page 4)",
        "entries[3].score",
        "results.csv",
        "footnote a",
        "and the",
        "'",
        '"',
        "(",
        ")",
        "[",
        "]",
        ", ",
        "; ",
        " ",
        "\u2018",
        "\xa0",
        "\U0001f600",
        "\ud83d",
        "\ude00",
        "\u017f",
        "\u212a",
        "x",
        "1",
    ]
)
LOCATORS = st.lists(PARTS, max_size=8).map("".join)


@given(LOCATORS)
def test_any_text_is_read_into_fields_that_agree_with_each_other(printed: str) -> None:
    parsed = parse_locator(printed)
    assert parsed.printed == printed
    assert parsed.verdict == verdict_of(parsed.anchors, parsed.addresses, parsed.selectors, parsed.residue)
    assert parsed.verdict in locator.VERDICTS
    assert coverage_of(parsed.verdict) in locator.COVERAGES
    assert classify_locator(printed) in locator.LOCATOR_CLASSES
    assert (parsed.verdict == "no_anchor") is (not parsed.anchors and not parsed.addresses)
    assert (coverage_of(parsed.verdict) == "residue") is bool(
        parsed.residue and (parsed.anchors or parsed.addresses)
    )
    segments = split_segments(printed)
    assert len(parsed.residue) <= len(segments)
    for held in (*parsed.residue, *segments):
        assert held
        assert trim(held) == held
    for selector in parsed.selectors:
        assert selector.form in locator.SELECTOR_FORMS
        assert selector.value is not None
    # Reading is a function of the text, and so is rendering what was read.
    assert parse_locator(printed) == parsed
    assert isinstance(rendered(parsed), str)


@given(LOCATORS)
def test_text_with_nothing_to_split_on_is_one_segment(printed: str) -> None:
    # Two halves side by side are one character to JavaScript, and come back as one.
    plain = paired(unit_text("".join(character for character in printed if character not in ",;")))
    assert split_segments(plain) == ([trim(plain)] if trim(plain) else [])
    # Splitting what was split changes nothing where no segment holds a separator.
    for segment in split_segments(printed):
        if "," not in segment and ";" not in segment:
            assert split_segments(segment) == [segment]


@given(
    st.sampled_from(sorted(locator.ROW_KEYS | locator.COLUMN_KEYS | {"model", "tool"})), st.integers(0, 999)
)
def test_an_ordinal_after_a_key_does_not_change_what_the_key_names(key: str, number: int) -> None:
    role = selector_role(key)
    assert selector_role(f"{key} {number}") == role
    assert selector_role(f"{key}  {number} of {number + 3}") == role
    assert selector_role(f"{key} {number}x") == "other"


def test_an_xml_cell_and_a_line_of_the_text_layer_are_read_only_after_two_letters() -> None:
    # The layer qualifier stands before both in the list and matches where they would, so it
    # takes `XML row 3 col 2` and `text-layer line 12` whole. Its word boundary is the one
    # under the i and u flags, which a long s or a Kelvin sign before the word breaks.
    plain = parse_locator("XML row 3 col 2; text-layer line 12")
    assert plain.addresses == ()
    assert [held.row() for held in plain.qualifiers] == [
        {"kind": "layer", "text": "XML row 3 col 2"},
        {"kind": "layer", "text": "text-layer line 12"},
    ]
    reached = 0
    for case in CASES:
        kinds = {held["kind"] for held in (*case["parsed"]["addresses"], *case["parsed"]["qualifiers"])}
        if kinds & {"xml_cell", "layer_line"}:
            reached += 1
            assert LONG_S in case["printed"] or KELVIN in case["printed"], case["printed"]
    assert reached == 3
    after = parse_locator(f"{LONG_S}XML row 3 col 2")
    assert [held.row() for held in after.addresses] == [
        {"kind": "xml_cell", "value": "3", "raw": "XML row 3 col 2", "value2": "2"}
    ]
    assert rendered(after) == "XML row 3 column 2"
    assert [held.row() for held in parse_locator(f"{KELVIN}text-layer line 12").qualifiers] == [
        {"kind": "layer_line", "text": "text-layer line 12"}
    ]
