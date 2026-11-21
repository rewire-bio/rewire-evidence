"""The table reader, held to what `scripts/omics/extract/tables.ts` does.

The cases are what the TypeScript did at the pinned commit, recorded in
`tests/fixtures/tables.json` by `scripts/fixtures/record_tables_fixture.mjs`: references
decoded, fragments made visible text, sources read into tables, captions matched, tables
sectioned and split into headed blocks, text layers read into rows, and small capitals
joined. The fixture also holds every pattern written in that file, as the TypeScript
compiler reads it, and the patterns of this library are held to those.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import cells, tables
from rewire_evidence.js import SPACE
from rewire_evidence.jsregex import JsPattern, Unsupported
from rewire_evidence.tables import (
    Block,
    Sectioned,
    Table,
    TableError,
    arrow_direction,
    decode,
    headed_blocks,
    join_small_caps,
    layout_rows,
    sectioned,
    table_matching,
    text,
)

HERE = Path(__file__).parent
TABLED: dict[str, Any] = json.loads((HERE / "fixtures/tables.json").read_text("utf-8"))
ANY_TEXT = st.text(st.characters(codec=None))
#: Text a cell can hold and come back as: no tag, no reference, no space at either end or doubled.
WORDS = st.lists(st.text("abcXYZ019.-%", min_size=1, max_size=6), min_size=1, max_size=3).map(" ".join)


def named(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_name = {case["name"]: case for case in cases}
    assert len(by_name) == len(cases)
    return by_name


SOURCES = named(TABLED["sources"])
MATCHINGS = named(TABLED["matchings"])
SECTIONINGS = named(TABLED["sectionings"])
BLOCKS = named(TABLED["blocks"])
LAYOUTS = named(TABLED["layouts"])


def held(case: dict[str, Any], run: Callable[[], object]) -> None:
    """Hold one call to what the TypeScript returned, or to the message of what it threw."""
    if "error" in case:
        assert case["error"]["name"] in {"Error", "RangeError"}
        with pytest.raises(TableError) as refused:
            run()
        assert str(refused.value) == case["error"]["message"]
    else:
        assert run() == case["returned"]


def as_recorded(table: Table) -> dict[str, Any]:
    return {"caption": table.caption, "rows": table.rows}


# ---------------------------------------------------------------------------------------
# The patterns.

#: A group JavaScript numbers is named here, which changes nothing it matches.
NAMED_GROUP = re.compile(r"\(\?<[a-z_]+>")


def numbered(pattern: JsPattern) -> list[str]:
    """A pattern of this library as the TypeScript writes it: its flags, and its groups with no names."""
    return [pattern.flags, NAMED_GROUP.sub("(", pattern.source)]


def test_every_pattern_of_the_file_is_the_one_this_library_runs() -> None:
    recorded = [[flags, source] for _, flags, source in TABLED["patterns"]]
    within = [name for name, _, _ in TABLED["patterns"]]
    assert within == [
        *["decode"] * 3,
        *["text"] * 4,
        "cells",
        "span",
        "rowsOf",
        "open",
        "scan",
        "figure",
        "label",
        "jats",
        "NOT_REPORTED",
        "asciiDigits",
        *["clean"] * 3,
        "sci",
        "plain",
        *["stripped"] * 2,
        "spread",
        "numeric",
        *["arrowDirection"] * 2,
        "lines",
        "layoutRows",
        *["joinSmallCaps"] * 2,
    ]
    # The reader, in the order the file writes them.
    assert recorded[:4] == [
        numbered(tables.HEX_REFERENCE),
        numbered(tables.DECIMAL_REFERENCE),
        numbered(tables.NAMED_REFERENCE),
        numbered(tables.ANNOTATION),
    ]
    assert recorded[5:7] == [numbered(tables.TAG), numbered(tables.SPACES)]
    assert recorded[8:15] == [
        numbered(pattern)
        for pattern in (
            tables.COLSPAN,
            tables.ROW,
            tables.TABLE_OPEN,
            tables.TABLE_TAG,
            tables.FIGCAPTION,
            tables.LABEL,
            tables.CAPTION,
        )
    ]
    # A tag closed by whatever opened it is written here as each tag closed by itself.
    flags, script = recorded[4]
    assert (flags, script) == ("gi", r"<(script|style)\b[^>]*>[\s\S]*?<\/\1>")
    assert [tables.SCRIPT_OR_STYLE.flags, tables.SCRIPT_OR_STYLE.source] == [
        flags,
        "|".join(script.replace("(script|style)", tag).replace(r"\1", tag) for tag in ("script", "style")),
    ]
    flags, cell = recorded[7]
    assert (flags, cell) == ("gi", r"<(t[dh])\b([^>]*)>([\s\S]*?)<\/\1>")
    assert numbered(tables.CELL) == [
        flags,
        "|".join(cell.replace("(t[dh])", tag).replace(r"\1", tag) for tag in ("td", "th")),
    ]
    assert recorded[26:29] == [
        numbered(tables.UP),
        numbered(tables.DOWN),
        numbered(tables.TRAILING_SPACE),
    ]
    # `split` finds every match with or without the g flag, and `all` asks for it.
    assert recorded[29] == ["", tables.GUTTER.source]
    assert tables.GUTTER.flags == "g"
    # The three groups of the small capitals are never read, so they capture nothing here.
    flags, capitals = recorded[30]
    assert [tables.SMALL_CAPS.flags, tables.SMALL_CAPS.source] == [
        flags,
        capitals.replace("([A-Z])", "[A-Z]").replace("([A-Z]{2,})", "[A-Z]{2,}"),
    ]
    assert recorded[31] == numbered(tables.SPACES)
    # The reading of one cell, which is `rewire_evidence.cells`.
    assert recorded[15] == [cells.NOT_REPORTED.flags, cells.NOT_REPORTED.source.replace("(?:", "(", 1)]
    # The digits are mapped over whole characters here, with no pattern.
    assert recorded[16] == ["gu", f"[\\u{{{0x1D7CE:X}}}-\\u{{{0x1D7FF:X}}}]"]
    assert cells.ascii_digits("\U0001d7cd\U0001d7ce\U0001d7ff\U0001d800") == "\U0001d7cd09\U0001d800"
    assert recorded[17:22] == [
        numbered(pattern)
        for pattern in (cells.DASHES, cells.SEPARATORS, cells.MARKS, cells.SCIENTIFIC, cells.PLAIN)
    ]
    # Replaced as text here, which is what a pattern of no special character does.
    assert recorded[22] == ["g", "\u00b1plus-or-minus\u00b1"]
    assert recorded[23:26] == [numbered(cells.SPACES), numbered(cells.SPREAD), numbered(cells.PERCENT)]
    assert len(recorded) == 32
    assert len(tables.PATTERNS) == len({(pattern.source, pattern.flags) for pattern in tables.PATTERNS}) == 20


def test_the_cases_were_recorded_from_the_file_the_gate_holds() -> None:
    expected = json.loads((HERE.parent / "scripts/expected/store-tables.json").read_text("utf-8"))
    assert TABLED["recorded_from"] == expected["recorded_from"]
    assert TABLED["recorded_from"]["script"] == "scripts/fixtures/record_tables_fixture.mjs"
    assert expected["module"]["file"] == "scripts/omics/extract/tables.ts"


# ---------------------------------------------------------------------------------------
# References and visible text.


@pytest.mark.parametrize("case", TABLED["decodes"], ids=lambda case: repr(case["given"])[:40])
def test_references_are_decoded_as_the_typescript_decodes_them(case: dict[str, Any]) -> None:
    held(case, lambda: decode(case["given"]))


def test_the_references_reach_every_way_one_is_read() -> None:
    read = {case["given"]: case.get("returned") for case in TABLED["decodes"]}
    refused = {case["given"]: case["error"]["message"] for case in TABLED["decodes"] if "error" in case}
    assert read["a&nbsp;b|&ndash;&mdash;&minus;&plusmn;&times;"] == "a b|\u2013\u2014\u2212\u00b1\u00d7"
    # A name is held in one case, and one the table lacks is left as written.
    assert (read["&AMP;"], read["&delta;"], read["&unknown;"]) == ("&AMP;", "&delta;", "&unknown;")
    # Each kind of reference is one pass, so what a pass writes the next one reads.
    assert (read["&amp;lt;"], read["&#38;lt;"], read["&#x26;#38;amp;"]) == ("&lt;", "<", "&")
    assert read["&#38;#x26;"] == "&#x26;"
    assert read["&#x1F600;"] == read["&#128512;"] == read["&#xD83D;&#xDE00;"] == "\U0001f600"
    assert read["&#xd800;"] == "\ud800"
    assert refused["&#x110000;"] == refused["&#1114112;"] == "Invalid code point 1114112"
    assert refused["&#xffffffffffffffffffff;"] == "Invalid code point 1.2089258196146292e+24"
    assert sum(message == "Invalid code point Infinity" for message in refused.values()) == 3
    # The table of names is an object, so a name every object has is read as that function.
    assert read["&constructor;"] == "function Object() { [native code] }"
    assert read["&toString;"] == "function toString() { [native code] }"
    inherited = {name for name in tables.INHERITED if read[f"&{name};"] != f"&{name};"}
    assert inherited == set(tables.INHERITED)
    assert (read["&tostring;"], read["&__proto__;"]) == ("&tostring;", "&__proto__;")
    # Under the i flag with no u flag a long s is no letter of a name, and a digit is an ASCII one.
    assert (read["&\u017f;"], read["&#\uff10;"]) == ("&\u017f;", "&#\uff10;")
    assert set(tables.ENTITIES) & set(tables.INHERITED) == set()
    assert len(tables.ENTITIES) == 18


@pytest.mark.parametrize("case", TABLED["texts"], ids=lambda case: repr(case["given"])[:40])
def test_a_fragment_is_made_visible_text_as_the_typescript_makes_it(case: dict[str, Any]) -> None:
    held(case, lambda: text(case["given"]))


def test_the_fragments_reach_every_way_text_is_hidden() -> None:
    read = {case["given"]: case.get("returned") for case in TABLED["texts"]}
    assert read["<ANNOTATION>x</annotation>y"] == "y"
    assert read["<annotation>unclosed"] == "unclosed"
    # A script is closed by a script and a style by a style, in any case.
    assert read["<script>a</style>b</script>c"] == "c"
    assert read["<style>a</script>b</style>c"] == "c"
    assert read["<SCRIPT>a</script>b"] == "b"
    assert read["<script>a</style>b"] == "a b"
    assert read["<scripts>a</scripts>b"] == "a b"
    # A reference that spells a tag is read after the tags are gone, and stays.
    assert read["&lt;b&gt;"] == "<b>"
    assert read["a < b > c"] == "a c"
    assert read["\u0085a\u0085"] == "\u0085a\u0085"
    assert read["\ufeffa\ufeff"] == "a"


@given(ANY_TEXT)
def test_visible_text_has_single_spaces_and_none_at_its_ends(fragment: str) -> None:
    try:
        visible = text(fragment)
    except TableError:
        # A reference to no character, which the cases hold the message of.
        return
    assert not any(character in SPACE for character in visible.replace(" ", ""))
    assert "  " not in visible
    assert visible == visible.strip(" ")


@given(st.text(st.characters(codec=None, exclude_characters="&")))
def test_text_with_no_ampersand_is_decoded_as_itself(given_text: str) -> None:
    # Half a character beside its other half is one character afterwards, and nothing else changes.
    assert decode(given_text) == given_text.encode("utf-16-le", "surrogatepass").decode(
        "utf-16-le", "surrogatepass"
    )


@given(st.text(st.characters(codec=None, exclude_characters="&"), max_size=20))
def test_a_character_written_as_its_number_is_read_back(given_text: str) -> None:
    whole = given_text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
    assert decode("".join(f"&#x{ord(character):X};" for character in whole)) == whole
    assert decode("".join(f"&#{ord(character)};" for character in whole)) == whole


# ---------------------------------------------------------------------------------------
# Tables.


@pytest.mark.parametrize("name", list(SOURCES))
def test_a_source_is_read_into_tables_as_the_typescript_reads_it(name: str) -> None:
    case = SOURCES[name]
    held(case, lambda: [as_recorded(table) for table in tables.tables(case["source"])])


def test_the_sources_reach_every_way_a_table_is_found() -> None:
    read = {name: case["returned"] for name, case in SOURCES.items() if "returned" in case}
    captions = {
        name: [table["caption"] for table in found] for name, found in read.items() if found is not None
    }
    assert captions["a LaTeXML figure"] == ["Table 2: Core model results"]
    assert captions["a JATS table-wrap"] == ["Table 3 Linear probe All tasks."]
    assert captions["a figcaption wins over a label and a caption"] == ["F"]
    assert captions["a figcaption with nothing in it still wins"] == [""]
    assert captions["the last label and the last caption, each on its own"] == ["L3 C2"]
    assert captions["a second table does not borrow the caption of the first"] == ["Table 1", ""]
    assert captions["a caption after one table belongs to the next"] == ["", "Table 2"]
    assert captions["a label inside a cell is taken for the caption"] == ["in a cell"]
    # A table in a cell is part of the cell, and the row ends where the first row inside it ends.
    assert len(read["a table in a table"]) == 1
    assert read["a table in a table"][0]["caption"] == "inner"
    assert read["a table-wrap and no table"] == read["a tag that only starts like a table"] == []
    spans = read["spans written every way"][0]["rows"][0]
    assert spans == [
        "a",
        "",
        "b",
        "",
        "c",
        "d",
        "e",
        "f",
        "",
        "g",
        "h",
        "i",
        "",
        "j",
        "k",
        "",
        "l",
        "m",
        "",
        "n",
        "",
        "o",
    ]
    assert len(read["a span of a hundred"][0]["rows"][0]) == 101
    assert read["empty cells that span"][0]["rows"] == [["", "", "", "", "", "x"]]
    assert read["a header cell and a data cell, each closed by the other"][0]["rows"] == [["a b", "c d"]]
    refused = {name: case["error"]["message"] for name, case in SOURCES.items() if "error" in case}
    assert sum(message == "Unclosed table element in artifact" for message in refused.values()) == 3
    assert sum(message == "Invalid code point 1114112" for message in refused.values()) == 2


def test_a_span_past_the_limit_is_refused_where_the_typescript_pads_or_never_returns() -> None:
    def spanning(span: str) -> str:
        return f'<table><tr><td colspan="{span}">a</td><td>b</td></tr></table>'

    assert tables.MAX_SPAN == 1000
    assert len(tables.tables(spanning("1000"))[0].rows[0]) == 1001
    with pytest.raises(TableError, match="A cell spans 1001 columns, and no more than 1000 are read"):
        tables.tables(spanning("1001"))
    with pytest.raises(TableError, match="A cell spans Infinity columns"):
        tables.tables(spanning("9" * 400))
    # The text of the cell is read first, so what it refuses is refused first.
    with pytest.raises(TableError, match="Invalid code point 1114112"):
        tables.tables('<table><tr><td colspan="2000">&#x110000;</td></tr></table>')


@st.composite
def grids(draw: st.DrawFn) -> list[list[tuple[str, str, int]]]:
    cell = st.tuples(st.sampled_from(["td", "th", "TD", "Th"]), WORDS, st.integers(1, 4))
    return draw(st.lists(st.lists(cell, min_size=0, max_size=4), min_size=0, max_size=4))


def written(grid: list[list[tuple[str, str, int]]], caption: str | None = None) -> str:
    rows = "".join(
        "<tr>"
        + "".join(
            f"<{tag}{'' if span == 1 else f' colspan="{span}"'}> <b>{words}</b> </{tag}>"
            for tag, words, span in row
        )
        + "</tr>\n"
        for row in grid
    )
    return f"<table>{'' if caption is None else f'<caption>{caption}</caption>'}\n{rows}</table>"


def padded(grid: list[list[tuple[str, str, int]]]) -> list[list[str]]:
    return [[cell for _, words, span in row for cell in (words, *[""] * (span - 1))] for row in grid]


@given(st.lists(st.tuples(grids(), st.none() | WORDS), max_size=3), WORDS)
def test_tables_written_as_markup_are_read_back_cell_for_cell(
    drawn: list[tuple[list[list[tuple[str, str, int]]], str | None]], between: str
) -> None:
    source = f"<p>{between}</p>".join(written(grid, caption) for grid, caption in drawn)
    assert tables.tables(source) == [Table(caption or "", padded(grid)) for grid, caption in drawn]
    # A table inside a cell is no table of its own, and its cells are text of the cell that holds it.
    inside = f"<table><tr><td>{source}</td></tr></table>"
    assert len(tables.tables(inside)) == 1


@pytest.mark.parametrize("name", list(MATCHINGS))
def test_one_table_is_found_by_its_caption_as_the_typescript_finds_it(name: str) -> None:
    case = MATCHINGS[name]
    pattern = JsPattern(*case["pattern"])
    assert tables._written(pattern) == case["written"]  # noqa: SLF001 - the text of a pattern in a message
    held(case, lambda: as_recorded(table_matching(case["source"], pattern)))


def test_a_pattern_is_written_as_javascript_writes_one_and_may_not_hold_its_place() -> None:
    messages = {name: case["error"]["message"] for name, case in MATCHINGS.items() if "error" in case}
    assert messages["two tables"] == "Expected one table matching /Table 3: Core model results/, found 2"
    assert messages["every table"] == "Expected one table matching /^/, found 5"
    assert MATCHINGS["a pattern with a slash in it"]["written"] == r"/a\/b/"
    assert MATCHINGS["one table, in any case"]["written"] == "/table 4/i"
    # `String(new RegExp(""))`.
    assert tables._written(JsPattern("")) == "/(?:)/"  # noqa: SLF001
    for run in (
        lambda: table_matching("<table></table>", JsPattern("a", "g")),
        lambda: layout_rows("a\nb", JsPattern("a", "g"), JsPattern("b")),
        lambda: layout_rows("a\nb", JsPattern("a"), JsPattern("b", "gi")),
    ):
        with pytest.raises(Unsupported, match="has the g flag"):
            run()


# ---------------------------------------------------------------------------------------
# Sections, headed blocks and arrows.


@pytest.mark.parametrize("name", list(SECTIONINGS))
def test_rows_are_sectioned_as_the_typescript_sections_them(name: str) -> None:
    case = SECTIONINGS[name]
    held(case, lambda: [asdict(row) for row in sectioned(case["rows"], case["width"])])


def test_the_sectioned_cases_reach_every_way_a_label_is_carried() -> None:
    read = {name: case["returned"] for name, case in SECTIONINGS.items() if "returned" in case}
    assert [row["section"] for row in read["a blank label continues the section"]] == [
        "A",
        "A",
        "A",
        "A",
        "\u0085",
    ]
    assert [row["section"] for row in read["rows before any label"]] == ["", "A"]
    assert read["a label kept as it is printed"][1] == {"section": "  A  ", "cells": ["m2", "2"]}
    assert SECTIONINGS["a row too long"]["error"]["message"] == (
        "Row has 5 cells, expected 2 or 3: A | m1 | 1 | x | y"
    )
    assert sectioned([("A", "1"), ("2",)], 1) == [Sectioned("A", ["1"]), Sectioned("A", ["2"])]


@given(st.lists(st.tuples(st.none() | WORDS, st.lists(WORDS, min_size=3, max_size=3)), max_size=8))
def test_every_row_is_kept_at_its_width_under_the_last_label_above_it(
    drawn: list[tuple[str | None, list[str]]],
) -> None:
    rows = [cells_ if label is None else [label, *cells_] for label, cells_ in drawn]
    made = sectioned(rows, 3)
    assert [row.cells for row in made] == [cells_ for _, cells_ in drawn]
    section = ""
    for (label, _), row in zip(drawn, made, strict=True):
        section = section if label is None else label
        assert row.section == section


@pytest.mark.parametrize("name", list(BLOCKS))
def test_a_table_is_split_into_headed_blocks_as_the_typescript_splits_it(name: str) -> None:
    case = BLOCKS[name]
    held(case, lambda: [asdict(block) for block in headed_blocks(Table("", case["rows"]))])


def test_the_headed_cases_reach_every_way_a_header_is_read() -> None:
    read = {name: case["returned"] for name, case in BLOCKS.items()}
    assert read["no rows"] == read["data and no header"] == read["a header with no rows under it"] == []
    stacked = read["two stacked blocks"]
    assert [(block["groups"], len(block["rows"])) for block in stacked] == [
        (["", "L100", "L100", "L200", "L200"], 1),
        (["", "L300", "L300", "L500", "L500"], 2),
    ]
    assert read["three header rows, the groups joined top to bottom"][0]["groups"] == [
        "",
        "G, A",
        "G, A",
        "G, B",
        "G, B",
    ]
    assert read["the same group named twice above a column"][0]["groups"] == ["", "G", "H, I"]
    assert read["cells with space around them"][0] == {
        "groups": ["", "G", "G"],
        "metrics": ["", "a", "b"],
        "rows": [[" m ", " 1 ", " 2 "]],
    }
    assert read["rows before the first header are left out"][0]["rows"] == [["m", "1"]]
    assert headed_blocks(Table("", [["", "a"], ["m", "1"]])) == [Block([""] * 2, ["", "a"], [["m", "1"]])]


@given(st.lists(st.lists(st.sampled_from(["", " ", "a", "b", "1"]), max_size=4), max_size=8))
def test_every_block_has_rows_and_no_row_is_in_two(rows: list[list[str]]) -> None:
    made = headed_blocks(Table("", rows))
    data = [row for row in rows if row and row[0].strip()]
    assert all(block.rows for block in made)
    assert sum(len(block.rows) for block in made) <= len(data)
    assert all(row in data for block in made for row in block.rows)
    assert all(len(block.groups) >= len(block.metrics) for block in made)


@pytest.mark.parametrize("case", TABLED["headings"], ids=lambda case: repr(case[0]))
def test_the_arrow_of_a_heading_is_read_as_the_typescript_reads_it(case: list[Any]) -> None:
    heading, direction = case
    assert arrow_direction(heading) == direction


def test_an_upward_arrow_is_read_before_a_downward_one() -> None:
    read = dict(TABLED["headings"])
    assert (read["AUROC \u2191"], read["RMSD\u2193"], read["\u2193 then \u2191"]) == (
        "higher",
        "lower",
        "higher",
    )
    assert read["&uarr;"] is read["plain"] is None


# ---------------------------------------------------------------------------------------
# Text layers.


@pytest.mark.parametrize("name", list(LAYOUTS))
def test_a_text_layer_is_read_into_rows_as_the_typescript_reads_it(name: str) -> None:
    case = LAYOUTS[name]
    held(case, lambda: layout_rows(case["source"], JsPattern(*case["from"]), JsPattern(*case["to"])))


def test_the_layouts_reach_every_way_a_line_is_cut() -> None:
    read = {name: case["returned"] for name, case in LAYOUTS.items() if "returned" in case}
    assert read["a table between its caption and the next heading"] == [
        ["Model", "Enhancer", "Promoter", "Splice site"],
        ["E NFORMER", "0.71", "0.82", "0.90"],
        ["DNABERT-2", "0.68", "0.80", "0.88"],
        ["Splice-H510 x", "0.5", "0.6", "0.7"],
        ["Nucleotide Transformer v2", "0.70", "0.81", "0.89"],
    ]
    assert read["the end is the line after the start"] == []
    assert read["one space is part of a cell"] == [["Nucleotide Transformer v2", "0.70"], ["x"]]
    messages = {name: case["error"]["message"] for name, case in LAYOUTS.items() if "error" in case}
    assert messages["the start is not found"] == "Table start not found: /^Table 9/"
    assert messages["the end is not found"] == r"Table end not found: /^6\s+D ISCUSSION/"
    assert read["the end is looked for after the start only"] == [["after", "the", "table"]]


@given(st.lists(st.lists(WORDS, min_size=1, max_size=4), max_size=6), st.integers(2, 5), st.integers(0, 4))
def test_rows_set_two_spaces_apart_are_read_back(rows: list[list[str]], gutter: int, indent: int) -> None:
    lines = [f"{' ' * indent}{(' ' * gutter).join(row)}   " for row in rows]
    source = "\n".join(["before", "  START of it", *lines, "", " STOP", "after  it"])
    assert layout_rows(source, JsPattern("^START"), JsPattern("^STOP$")) == rows


@pytest.mark.parametrize("case", TABLED["small_caps"], ids=lambda case: repr(case[0]))
def test_small_capitals_are_joined_as_the_typescript_joins_them(case: list[str]) -> None:
    printed, joined = case
    assert join_small_caps(printed) == joined


def test_the_small_capitals_reach_every_way_a_name_is_set() -> None:
    read = dict(TABLED["small_caps"])
    assert (read["E NFORMER"], read["D EEP SEA"]) == ("ENFORMER", "DEEPSEA")
    assert (read["XE NFORMER"], read["E  NFORMER"], read["Splice-H510"]) == (
        "XE NFORMER",
        "E  NFORMER",
        "Splice-H510",
    )
    assert read["A BC D EF"] == "ABC DEF"
    assert read["E NFORMER, D EEP SEA and B ORZOI"] == "ENFORMER, DEEPSEA and BORZOI"


@given(ANY_TEXT)
def test_joining_small_capitals_takes_out_spaces_and_nothing_else(printed: str) -> None:
    whole = printed.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
    joined = join_small_caps(printed)
    assert joined.replace(" ", "") == whole.replace(" ", "")
    assert len(joined) <= len(whole)
