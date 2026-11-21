"""Printed cells, read as `parseCell` reads them.

The cases are what `scripts/omics/extract/tables.ts` did at the pinned commit, recorded in
`tests/fixtures/checks.json`: 398 cells with and without a percent column, and 203 texts
asked whether they are a cell a paper leaves unfilled. The printed values of the pinned
store, 26,130 of them, are in `scripts/expected/store-checks-cells.jsonl.gz` and are read
again here, so the store's own cells are held without a checkout.
"""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import cells
from rewire_evidence.canonical import js_quote, js_stringify, js_utf8, sha256_hex
from rewire_evidence.cells import Cell, CellError, ascii_digits, is_missing_cell, parse_cell
from rewire_evidence.checkparity import cell_reading
from rewire_evidence.js import number_of_text
from tests.checked import CHECKED

EXPECTED = Path(__file__).parent.parent / "scripts/expected"
STORE: dict[str, Any] = json.loads((EXPECTED / "store-checks.json").read_text("utf-8"))
ANY_TEXT = st.text(st.characters(codec=None))
DIGITS = st.text("0123456789", min_size=1, max_size=12)


def shown(case: list[Any]) -> str:
    return f"{case[0]!r} as {case[1]}"


@pytest.mark.parametrize("case", CHECKED["cells"], ids=shown)
def test_a_cell_is_read_as_the_typescript_reads_it(case: list[Any]) -> None:
    printed, unit, recorded = case
    assert cell_reading(printed, unit) == recorded
    if isinstance(recorded, str):
        with pytest.raises(CellError) as refused:
            parse_cell(printed, unit)
        assert str(refused.value) == recorded
        assert recorded.endswith(f": {js_quote(printed)}")
    else:
        assert parse_cell(printed, unit) == Cell(*recorded)
        # A cell that reports nothing is kept as it was given, and one that reports a number is tidied.
        assert (recorded[1] is None) == is_missing_cell(recorded[0])


@pytest.mark.parametrize("case", CHECKED["missing"], ids=lambda case: repr(case[0]))
def test_a_cell_a_paper_did_not_fill_is_known_as_the_typescript_knows_it(case: list[Any]) -> None:
    printed, missing = case
    assert is_missing_cell(printed) is missing


def test_the_cases_cover_every_way_a_cell_is_read() -> None:
    recorded = [case[2] for case in CHECKED["cells"]]
    refusals = {held.split(":")[0] for held in recorded if isinstance(held, str)}
    assert refusals == {"Unrecognised numeric cell", "Unrecognised uncertainty in cell"}
    read = [held for held in recorded if not isinstance(held, str)]
    assert any(held[1] is None for held in read)
    assert any(held[2] is not None for held in read)
    assert any("e" in held[1] for held in read if held[1])
    # The percent sign is taken off for a percent column and for no other.
    by_unit = {(case[0], case[1]): case[2] for case in CHECKED["cells"]}
    assert by_unit["59.2%", "percent"] == ["59.2%", "59.2", None]
    for unit in (None, "fraction", "", "Percent", "percent "):
        assert by_unit["59.2%", unit] == 'Unrecognised numeric cell: "59.2%"'
    assert sum(missing for _, missing in CHECKED["missing"]) > 20
    assert len(CHECKED["cells"]) > 390


def test_the_pattern_for_an_unfilled_cell_is_the_typescripts_without_its_group() -> None:
    flags, source = CHECKED["constants"]["not_reported"]
    assert source.startswith("^(|-|--")
    # The group is only tested, so it is written as one that captures nothing.
    assert (cells.NOT_REPORTED.flags, cells.NOT_REPORTED.source) == (flags, source.replace("^(", "^(?:", 1))
    assert len(cells.PATTERNS) == len({(held.source, held.flags) for held in cells.PATTERNS}) == 9


def test_every_printed_value_of_the_pinned_store_is_read_as_the_typescript_read_it() -> None:
    facts = STORE["cells"]
    data = gzip.decompress((EXPECTED / facts["file"]).read_bytes())
    assert sha256_hex(data) == facts["sha256"]
    lines = data.split(b"\n")[:-1]
    assert len(lines) == facts["lines"] == 26130
    refused = 0
    for line in lines:
        printed, percent, recorded = json.loads(line)
        read = cell_reading(printed, "percent" if percent else None)
        assert js_utf8(js_stringify([printed, percent, read])) == line
        refused += isinstance(recorded, str)
    # Most of what parseCell refuses is a value printed with more than a number in it.
    assert refused == 1455


def test_digits_set_in_bold_are_the_digits_they_stand_for() -> None:
    assert ascii_digits("\U0001d7ce\U0001d7d7\U0001d7d8\U0001d7ff") == "0909"
    assert ascii_digits("a\U0001d7cdb\U0001d800") == "a\U0001d7cdb\U0001d800"
    # Two halves held apart are the character they spell, and half a character is left alone.
    assert ascii_digits("\U0001d7ce") == "0"
    assert ascii_digits("\ud835") == "\ud835"
    assert ascii_digits("\udfce\ud835") == "\udfce\ud835"


@given(ANY_TEXT)
def test_making_digits_plain_twice_is_making_them_plain_once(text: str) -> None:
    plain = ascii_digits(text)
    assert ascii_digits(plain) == plain
    assert not any(0x1D7CE <= ord(character) <= 0x1D7FF for character in plain)


@given(ANY_TEXT, st.sampled_from([None, "percent", "fraction"]))
def test_any_text_is_read_or_refused_and_nothing_else(text: str, unit: str | None) -> None:
    read = cell_reading(text, unit)
    if isinstance(read, str):
        assert read.endswith(f": {js_quote(text)}")
        assert not is_missing_cell(text)
        with pytest.raises(CellError):
            parse_cell(text, unit)
        return
    cell = parse_cell(text, unit)
    if cell.value is None:
        assert cell == Cell(text, None, None)
        return
    assert math.isfinite(number_of_text(cell.value))
    assert cell.sd is None or number_of_text(cell.sd) >= 0
    # What is kept of the cell reads as the same cell.
    assert parse_cell(cell.printed, unit) == cell


@given(st.sampled_from(["", "-"]), DIGITS, st.one_of(st.none(), DIGITS))
def test_a_plain_decimal_is_read_as_itself(sign: str, whole: str, fraction: str | None) -> None:
    number = f"{sign}{whole}{'' if fraction is None else '.' + fraction}"
    assert parse_cell(number) == Cell(number, number, None)
    # Marks, thousands separators and the minus sign a paper prints do not change the number.
    assert parse_cell(f" {number.replace('-', '\u2212')}*\u2020 ").value == number
    assert parse_cell(f"{number}%", "percent").value == number


@given(DIGITS, DIGITS, st.sampled_from([" \u00b1 ", "\u00b1", " (", "("]))
def test_a_number_and_its_spread_are_read_apart(value: str, spread: str, between: str) -> None:
    closing = ")" if "(" in between else ""
    cell = parse_cell(f"{value}{between}{spread}{closing}")
    assert (cell.value, cell.sd) == (value, spread)


@given(
    st.sampled_from(["", "-", "--", "\u2013", "\u2014", "n/a", "NA", "nan", ".", "/"]),
    st.text(" \t\n", max_size=3),
)
def test_an_unfilled_cell_has_no_value_whatever_space_is_around_it(missing: str, space: str) -> None:
    printed = f"{space}{missing}{space}"
    assert is_missing_cell(printed)
    assert parse_cell(printed) == Cell(printed, None, None)
