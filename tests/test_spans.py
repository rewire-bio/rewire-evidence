"""What the recordings cannot hold of the span resolvers: properties, and where the port parts from Node.

`tests/test_spans_recorded.py` replays every case the TypeScript was run on. This file holds
what is true of every input, which a list of cases cannot say, and the few places where the
port does something the TypeScript cannot be recorded doing.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from rewire_evidence import (
    aligned,
    cli,
    figures,
    gridspans,
    jsonspans,
    jsregex,
    layercoverage,
    layerreaders,
    offsets,
    prose,
    registry,
    resolution,
    sheets,
    spanmeasure,
    spansaid,
)
from rewire_evidence.js import SPACE, paired, unit_text
from rewire_evidence.jsonl import StoreError
from rewire_evidence.jsregex import JsPattern, Unsupported
from rewire_evidence.office import escape_cell
from rewire_evidence.resolution import REFUSAL_REASONS, Refusal, progress
from tests.spanned import SPANS
from tests.test_spans_recorded import lay_out

#: Text as a layer holds it: any characters, none of them half of one.
TEXT = st.text(alphabet=st.characters(blacklist_categories=["Cs"]), max_size=60)
#: The same with the characters that make the three counts differ put where they will be met.
MIXED = st.lists(
    st.sampled_from(["a", " ", "\n", "\t", "\u00e9", "\u20ac", "\U0001f600", "\U00010400", "0", ".", "\f"]),
    max_size=40,
).map("".join)
LAYER = TEXT | MIXED
#: Text in units, which may hold half a character: what JavaScript can hold and a layer cannot.
UNITS = st.text(alphabet=st.characters(), max_size=40).map(unit_text)


def test_the_first_and_the_last_character_beyond_the_plane_are_two_units() -> None:
    # The scan that spares a layer with no such character a copy turns on U+10000 itself.
    assert offsets.to_units("\U00010000") == "\ud800\udc00"
    assert offsets.to_units("a\U0010ffff") == "a\udbff\udfff"
    assert offsets.to_units("\uffff\ue000") == "\uffff\ue000"


@given(LAYER)
def test_text_in_units_counts_as_javascript_counts_and_comes_back_whole(text: str) -> None:
    units = offsets.to_units(text)
    assert units == unit_text(text)
    assert paired(units) == text
    assert len(units) == len(text.encode("utf-16-le")) // 2
    assert offsets.count_points(units) == len(text)
    assert offsets.byte_length(units) == len(text.encode("utf-8"))


@given(LAYER)
def test_the_index_of_a_layer_ends_on_its_three_sizes_and_starts_each_line_where_it_starts(text: str) -> None:
    index = offsets.index_layer(text, "0" * 64, "text-utf8@1")
    assert index.u16[-1] == len(index.text)
    assert index.points[-1] == offsets.layer_points(index) == len(text)
    assert index.bytes[-1] == len(text.encode("utf-8"))
    assert len(index.u16) == len(index.points) == len(index.bytes) == len(index.lines) + 1
    for line, start in enumerate(index.u16[:-1]):
        assert index.text[start : start + len(index.lines[line])] == index.lines[line]
        assert offsets.line_at(index, start) == line
        assert offsets.point_at(index, start) == index.points[line]
        assert offsets.byte_at(index, start) == index.bytes[line]


@given(LAYER, st.integers(-5, 70), st.integers(-5, 70))
def test_a_span_holds_the_text_its_offsets_name_and_is_found_again_where_it_is(
    text: str, start: int, end: int
) -> None:
    index = offsets.index_layer(text, "0" * 64, "text-utf8@1")
    span = offsets.span_at(index, start, end)
    exact = paired(span.exact)
    # Both published ranges name the same text, and a span never holds half a character.
    assert text[span.start : span.end] == exact
    assert text.encode("utf-8")[span.byte_start : span.byte_end] == exact.encode("utf-8")
    assert span.byte_end - span.byte_start >= span.end - span.start
    assert f"{span.prefix or ''}{span.exact}{span.suffix or ''}" in index.text
    assert offsets.count_points(span.prefix or "") <= offsets.CONTEXT_POINTS
    assert offsets.count_points(span.suffix or "") <= offsets.CONTEXT_POINTS
    assert layerreaders.slice_span(text, span.start, span.end) == {
        "exact": exact,
        "start": span.start,
        "end": span.end,
        "byte_start": span.byte_start,
        "byte_end": span.byte_end,
    }
    assert prose.point_slice(index, span.start, span.end) == span.exact
    assert prose.reanchor(span, index) == prose.Anchoring("unchanged", "offsets", span)


@given(LAYER, st.integers(0, 70))
def test_a_character_offset_and_a_unit_offset_are_each_others_inverse_where_a_character_starts(
    text: str, at: int
) -> None:
    index = offsets.index_layer(text, "0" * 64, "text-utf8@1")
    point = min(at, len(text))
    unit = len(offsets.to_units(text[:point]))
    assert offsets.point_at(index, unit) == point
    assert prose.unit_of_point(index, point) == unit
    assert gridspans.unit_of_point(index, point) == unit
    assert prose.unit_of_point(index, len(text) + 1 + at) == -1


@given(UNITS)
def test_text_that_holds_half_a_character_is_counted_as_node_counts_it(units: str) -> None:
    # Node writes U+FFFD for a half with no partner, which is three bytes and one character.
    written = paired(units).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    assert offsets.byte_length(units) == len(written.encode("utf-8"))
    assert offsets.count_points(units) == len(written)


@given(TEXT)
def test_a_label_normalised_twice_is_the_label_normalised_once(text: str) -> None:
    once = aligned.normalise_label(offsets.to_units(text))
    assert aligned.normalise_label(once) == once
    assert once == once.strip(" \t\n")
    forms = aligned.column_forms(offsets.to_units(text))
    assert forms[0] == aligned.normalise_label(
        re.sub("[\u2191\u2193\u2195\u21d1\u21d3]", " ", offsets.to_units(text))
    )
    assert 1 <= len(forms) <= 2


@given(st.lists(st.text(alphabet="ab \t0.", max_size=6), max_size=8))
def test_the_cells_of_a_line_are_its_text_between_runs_of_spaces(parts: list[str]) -> None:
    line = "  ".join(parts)
    index = offsets.index_layer(line.replace("\n", ""), "0" * 64, "pdf-text-layer@1")
    cells = aligned.cells_of(index, 0)
    for cell in cells:
        assert index.text[cell.start : cell.end] == cell.text
        assert "  " not in cell.text
        assert cell.text[0] != " "
    assert [cell.start for cell in cells] == sorted(cell.start for cell in cells)
    assert aligned.cells_of(index, 0) is cells


@given(st.text(max_size=30))
def test_an_escaped_cell_is_read_back_as_it_was(value: str) -> None:
    assert sheets.unescape_cell(escape_cell(value)) == value


@given(
    st.lists(st.lists(st.text(alphabet="ab1 .", max_size=4), min_size=1, max_size=4), min_size=1, max_size=5)
)
def test_every_cell_of_a_sheet_block_is_found_by_its_row_and_column(rows: list[list[str]]) -> None:
    layer = "\f# sheet\t1\tS\tA1\n" + "".join("\t".join(row) + "\n" for row in rows)
    index = offsets.index_layer(layer, "0" * 64, "ooxml-sheet-tsv@1")
    for at, row in enumerate(rows, 1):
        for column, value in enumerate(row, 1):
            found = sheets.cell_range(layer, "S", at, column)
            assert found is not None
            assert (found.field, found.line, found.field_number) == (value, at, column)
            assert layer[found.start : found.end] == value
            assert sheets.cell_range_by_reference(layer, 1, sheets.reference(at, column)) == found
            outcome = sheets.resolve_sheet_cell(index, sheets.SheetCellRequest("S", row=at, column=column))
            assert isinstance(outcome, Refusal) == (not value.strip(" "))
        assert sheets.cell_range(layer, "S", at, len(row) + 1) is None
    assert sheets.cell_range(layer, "S", len(rows) + 1, 1) is None


JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-(10**6), 10**6)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=8),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=4), inner, max_size=3),
    max_leaves=10,
)


def _valued(node: jsonspans.JsonNode) -> object:
    """The value a node holds, read from what the reader kept of it."""
    if node.kind == "object":
        return {paired(key): _valued(member) for key, member in node.members}
    if node.kind == "array":
        return [_valued(item) for item in node.items]
    return paired(node.text or "") if node.kind == "string" else json.loads(node.text or "")


@given(value=JSON_VALUES, indent=st.sampled_from([None, 1, "\t"]), ascii_only=st.booleans())
def test_the_reader_keeps_every_value_of_a_document_and_where_it_is(
    *, value: object, indent: int | str | None, ascii_only: bool
) -> None:
    document = offsets.to_units(json.dumps(value, indent=indent, ensure_ascii=ascii_only))
    root = jsonspans.read_json(document)
    assert root is not None
    assert _valued(root) == value
    assert (root.start, root.end) == (0, len(document))

    def placed(node: jsonspans.JsonNode) -> None:
        assert json.loads(paired(document[node.start : node.end])) == _valued(node)
        for _, member in node.members:
            placed(member)
        for item in node.items:
            placed(item)

    placed(root)
    # Anything after the document, and a document cut short, is no document.
    assert jsonspans.read_json(f"{document} x") is None
    if len(document) > 1 and not isinstance(value, (int, float)):
        assert jsonspans.read_json(document[:-1]) is None


@given(st.lists(st.text(alphabet=st.characters(blacklist_categories=["Cs"]), max_size=6), max_size=5))
def test_a_pointer_written_from_tokens_is_read_back_as_them(tokens: list[str]) -> None:
    pointer = jsonspans.render_pointer(tokens)
    assume(pointer.strip(SPACE) == pointer)
    assert jsonspans.pointer_tokens(pointer) == tokens
    assert all(jsonspans.unescape_token(jsonspans.escape_token(token)) == token for token in tokens)


def test_a_document_nested_deeper_than_node_can_follow_is_still_read() -> None:
    # Node's reader calls itself for each level and gives up some thousands deep, at a depth
    # that is the machine's. This reader keeps its own stack and has no such depth.
    depth = 50_000
    root = jsonspans.read_json("[" * depth + "]" * depth)
    assert root is not None
    for _ in range(depth - 1):
        (root,) = root.items
    assert (root.kind, root.items) == ("array", [])


def test_a_document_of_more_values_than_the_reader_indexes_is_not_read() -> None:
    assert jsonspans.read_json("[1, 2, 3]", most=4) is not None
    assert jsonspans.read_json("[1, 2, 3]", most=3) is None
    assert jsonspans.MAX_NODES == 4_000_000


DECIMALS = st.decimals(
    min_value=Decimal("-1e6"), max_value=Decimal("1e6"), allow_nan=False, allow_infinity=False, places=8
)


@given(DECIMALS, st.integers(0, 8))
def test_a_decimal_is_rounded_half_away_from_zero_on_its_digits(value: Decimal, places: int) -> None:
    spelling = format(value, "f")
    rounded = spanmeasure.round_decimal(spelling, places)
    expected = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    assert Decimal(rounded) == expected
    assert len(rounded.partition(".")[2]) == places


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_a_number_spelled_without_an_exponent_is_the_same_number(value: float) -> None:
    spelling = spanmeasure.plain_decimal(value)
    assert "e" not in spelling.lower()
    assert float(spelling) == value


@given(st.floats(min_value=-1e15, max_value=1e15, allow_nan=False), st.integers(0, 6))
def test_a_number_prints_the_value_it_rounds_to_and_text_prints_itself(value: float, places: int) -> None:
    # The record keeps what a reader rounds the printed digits to, half away from zero.
    printed = format(Decimal(repr(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")
    found = spanmeasure.value_presence(repr(value), printed)
    assert (found.present, found.exact, found.partial) == (True, True, False)
    assert spanmeasure.decimals_written(printed) == places


@given(
    st.text(alphabet=st.characters(blacklist_categories=["Cs"]), min_size=1, max_size=20), st.text(max_size=5)
)
def test_a_span_that_holds_the_stored_value_prints_it(value: str, around: str) -> None:
    units = offsets.to_units(value)
    assume(spanmeasure.flatten_value(units))
    found = spanmeasure.value_presence(units, units)
    assert (found.present, found.exact) == (True, True)
    assert spanmeasure.value_presence(offsets.to_units(f"{around}{value}{around}"), units).present


FIGURE_NAMES = st.sampled_from(["resolved", "share", "big", "absent"])
PRINTED = st.sampled_from(["3", "1,234", "35.7", "12", "0.5", "7,654,321"])
#: Documents in which each tagged number is the first number printed on its line.
DOCUMENTS = st.lists(
    st.tuples(
        st.sampled_from(["", "text ", "a line\n", "(", "\U0001f600 "]),
        PRINTED,
        st.sampled_from(["", " ", "% ", ") "]),
        FIGURE_NAMES,
    ),
    max_size=6,
).map(
    lambda parts: "".join(
        f"{before}{printed}{between}<!-- figure:{name} -->\n" for before, printed, between, name in parts
    )
)


@given(DOCUMENTS)
def test_a_document_retagged_once_has_nothing_left_to_retag(document: str) -> None:
    # The number is looked for from the start of its line, so this holds where no number stands
    # before it there. The recorded cases hold what the TypeScript writes where one does.
    held = {"resolved": 18667, "share": 35.7, "big": 7654321}
    rewritten, changed, unknown = figures.retag(document, held)
    marks = figures.marks(rewritten)
    assert len(marks) == len(figures.marks(document))
    assert [mark.figure for mark in unknown] == [mark.figure for mark in marks if mark.figure == "absent"]
    assert all(
        figures.prints_figure(mark.printed, held[mark.figure]) for mark in marks if mark.figure in held
    )
    assert figures.retag(rewritten, held) == (rewritten, [], unknown)
    assert (rewritten == document) == (not changed)


KEYS = st.text(
    alphabet=st.characters(blacklist_categories=["Cs"], blacklist_characters="\t\n#"), min_size=1, max_size=6
)
# A key names one record of a file. The TypeScript gives the second record of an ID the key
# `id#2`, so no file it reads holds a key twice, and a test below holds what two would do.
INPUTS = st.lists(
    st.tuples(
        st.text(alphabet="abc/.", min_size=1, max_size=8),
        st.none()
        | st.lists(
            st.tuples(KEYS, st.text(alphabet="0123456789abcdef", min_size=12, max_size=12)),
            max_size=4,
            unique_by=lambda record: record[0],
        ),
    ),
    max_size=4,
    unique_by=lambda held: held[0],
)


def test_a_key_held_twice_is_one_record_and_the_later_line_is_the_one_compared() -> None:
    # What the property above was once given, and wrongly expected no change of: a file that
    # grew, measured over two lines of one key. The key is looked up, the later line is found,
    # and the earlier one reads as changed. `changedInputs` keeps the records in a Map too.
    twice = (("0", "000000000000"), ("0", "000000000001"))
    measured = figures.FigureInput("a", "0" * 64, twice, 2)
    grown = figures.FigureInput("a", "1" * 64, (*twice, ("new", "0" * 12)))
    assert figures.changed_inputs([measured], [measured], Path()) == []
    assert figures.changed_inputs([measured], [grown], Path()) == [
        figures.Changed("a", "2 records measured", "1 measured record changed: 0")
    ]
    once = figures.FigureInput("a", "0" * 64, twice[1:], 1)
    assert figures.changed_inputs([once], [grown], Path()) == []


@given(
    INPUTS,
    st.dictionaries(
        st.sampled_from(["a", "b.c", "pct"]),
        st.integers(0, 10**6) | st.floats(0, 100).map(lambda x: round(x, 1)),
        max_size=3,
    ),
)
def test_a_figures_file_written_and_read_is_the_same_file(
    inputs: list[tuple[str, Any]], held: dict[str, float]
) -> None:
    content = figures.FigureFile(
        "a command",
        "2026-11-19",
        ("a.md",),
        tuple(
            figures.FigureInput(
                path, "0" * 64, None if rows is None else tuple(rows), None if rows is None else len(rows)
            )
            for path, rows in inputs
        ),
        held,
    )
    import tempfile  # noqa: PLC0415 - hypothesis runs the body many times, each in a folder of its own

    with tempfile.TemporaryDirectory() as folder:
        figures.write_figures(Path(folder), content)
        read = figures.read_figures(Path(folder))
        assert read == content
        assert json.loads((Path(folder) / figures.SPAN_FIGURES).read_text("utf-8"))["figures"] == held
        # A file measured over itself has not drifted, and one that only gained records has not either.
        assert figures.changed_inputs(read.inputs, content.inputs, Path(folder)) == []
        grown = tuple(
            figures.FigureInput(
                row.path, "1" * 64, None if row.records is None else (*row.records, ("new", "0" * 12))
            )
            for row in content.inputs
        )
        changed = figures.changed_inputs(read.inputs, grown, Path(folder))
        assert [row.path for row in changed] == sorted(
            row.path for row in content.inputs if row.records is None
        )


SANITY = st.sampled_from(["pass", "suspect", "fail"])
OBJECT = st.sampled_from(["a" * 64, "b" * 64, "c" * 64])
SOURCE = st.sampled_from(["s1", "s2", "s3", "S3", "s10", "\U0001f600", ""])
RECEIPT = st.fixed_dictionaries(
    {
        "outcome": st.sampled_from(["stored", "failed", "not_artifact"]),
        "sha256": st.none() | OBJECT,
        "reason": st.none() | st.sampled_from(["timeout", "interstitial_body"]),
        "source_class": st.sampled_from(["publisher", "arxiv", "pmc"]),
    }
)
LAYER_ROW = st.fixed_dictionaries(
    {
        "object_sha256": OBJECT,
        "layer_sha256": st.text(alphabet="0123456789abcdef", min_size=4, max_size=4),
        "normaliser": st.sampled_from(["text-utf8@1", "pdf-text-layer@1", "html-table-grid@1"]),
        "sanity": SANITY,
        "sanity_reasons": st.lists(st.sampled_from(["few letters", "mojibake"]), max_size=2),
        "metrics": st.fixed_dictionaries({"characters": st.integers(0, 10**6)}),
    }
)


@given(
    st.lists(SOURCE, max_size=12),
    st.dictionaries(SOURCE, RECEIPT, max_size=7),
    st.lists(LAYER_ROW, max_size=8),
    st.lists(
        st.fixed_dictionaries({"object_sha256": OBJECT, "reason": st.sampled_from(["raster", "binary"])})
    ),
)
def test_every_source_is_in_one_bucket_and_the_buckets_sum_to_the_sources(
    sources: list[str],
    receipts: dict[str, dict[str, Any]],
    registry: list[dict[str, Any]],
    unsupported: list[dict[str, Any]],
) -> None:
    # `coverageOf` checks these two sums of its own rows and reports a breach. Neither can
    # be breached, so the port does not check them, and this holds that they cannot.
    found = layercoverage.coverage_of(sources, receipts, registry, unsupported)
    assert len(found.rows) == len(sources) == found.sources
    assert sum(found.buckets.values()) == len(sources)
    assert tuple(found.buckets) == layercoverage.COVERAGE_BUCKETS
    assert sorted(row.source_id for row in found.rows) == sorted(sources)
    assert sum(tally.sources for tally in found.by_class.values()) == len(sources)
    assert found.characters == sum(row.characters for row in found.rows)
    assert found.distinct_characters <= found.characters
    assert found.no_receipt == sum(1 for source in sources if source not in receipts)
    # The one breach it can report: a source the store names twice.
    assert len(found.problems) == len(sources) - len(set(sources))


def test_a_row_outside_its_sheet_has_no_heading() -> None:
    # Row 0 of a sheet that holds no rows is its header line, and the line below that is the
    # header of the next sheet, which is not a heading of this one.
    index = offsets.index_layer(
        "\f# sheet\t1\tNone\t\n\f# sheet\t2\tNext\tA1:A1\nvalue", "0" * 64, "ooxml-sheet-tsv@1"
    )
    found = sheets.resolve_sheet_cell(index, sheets.SheetCellRequest("None", row=0, column=1))
    assert isinstance(found, resolution.Resolved)
    assert (found.span.exact, found.row_label, found.column_header) == ("\f# sheet", "# sheet", None)
    below = sheets.resolve_sheet_cell(index, sheets.SheetCellRequest("Next", row=1, column=1))
    assert isinstance(below, resolution.Resolved)
    assert (below.span.exact, below.column_header) == ("value", "value")


def test_a_refusal_that_got_further_is_later_in_the_list() -> None:
    assert [progress(reason) for reason in REFUSAL_REASONS] == list(range(len(REFUSAL_REASONS)))
    assert (
        progress("no_capture")
        < progress("no_anchor")
        < progress("anchor_not_found")
        < progress("json_not_parsed")
    )


# ---- where the port parts from Node, or holds what no recording can -------------------------


def test_a_grid_layer_whose_lines_are_json_and_not_a_grid_is_refused_whole() -> None:
    # The TypeScript trusts the shape of each line and would read these as tables with members
    # missing. No registered layer is one, because a grid's bytes are named by their hash.
    header = {
        "normaliser": "html-table-grid@1",
        "text_layer_sha256": "a" * 64,
        "text_normaliser": "html-text@1",
        "tables": 1,
        "cells": 0,
        "empty_cells": 0,
    }
    layers: list[list[object]] = [[{**header, "tables": "1"}], [header, {"index": 0}], [header, []], [[]]]
    for lines in layers:
        with pytest.raises(gridspans.GridUnreadable, match="is not what a grid holds"):
            gridspans.read_grid("".join(f"{json.dumps(line)}\n" for line in lines))


def test_the_layers_of_a_small_store_are_read_from_its_cache_or_from_a_folder_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = SPANS["recordings"]
    lay_out(tmp_path, recorded["files"], recorded["objects"])
    monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
    store = layerreaders.LayerStore(tmp_path)
    grids = store.grids_for_source("src-both")
    assert sorted(str(record["normaliser"]) for record in grids) == ["html-table-grid@1", "jats-table-grid@1"]
    assert grids == sorted(grids, key=layerreaders.by_usefulness)
    assert [layerreaders.is_grid(record) for record in store.layers_for_source("src-both")] == [False, False]
    text = store.layers_for_source("src-both")[0]
    assert store.layer_by_hash(str(text["layer_sha256"])) is text
    assert store.grid_for_layer(str(text["layer_sha256"])) in grids
    assert store.grid_for_layer("0" * 64) is None
    assert store.grid_for_layer(str(store.layers_for_source("src-csv")[0]["layer_sha256"])) is None
    assert store.grid_of_record(grids[0]).normaliser == grids[0]["normaliser"]
    with pytest.raises(layerreaders.LayerUnreadable, match=r"is a \S+ layer, not a grid"):
        store.grid_of_record(text)
    with pytest.raises(layerreaders.LayerUnreadable, match="is not in the object manifest"):
        store.layer_text({**text, "object_sha256": "0" * 64})
    characters = cast("dict[str, int]", text["metrics"])["characters"]
    assert layerreaders.character_count(store.layer_text(text)) == characters

    # The same from a folder that is named, whatever the checkout's own cache holds.
    elsewhere = tmp_path / "objects"
    (tmp_path / "data/archive/cache").rename(elsewhere)
    assert not store.has_object(str(text["object_sha256"]))
    named = layerreaders.LayerStore(tmp_path, elsewhere)
    assert named.has_object(str(text["object_sha256"]))
    assert named.layer_text(text) == store.layer_text(text)
    assert not named.has_object("0" * 64)
    with pytest.raises(StoreError, match="cannot be read"):
        named.read_object("0" * 64)
    with pytest.raises(ValueError, match="span 3 to 2 is not a range"):
        layerreaders.slice_span("abc", 3, 2)
    with pytest.raises(ValueError, match="span ends at 9 and the layer holds 3 characters"):
        layerreaders.slice_span("abc", 0, 9)


def test_a_missing_file_of_what_gave_no_layer_is_an_empty_one(tmp_path: Path) -> None:
    assert registry.read_unsupported(tmp_path) == []
    lay_out(tmp_path, SPANS["recordings"]["files"], SPANS["recordings"]["objects"])
    assert [row["reason"] for row in registry.read_unsupported(tmp_path)] == [
        "raster image, no text layer"
    ] * 2


def test_a_store_with_no_results_folder_cannot_be_measured(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="cannot be read"):
        spanmeasure.read_results(tmp_path)
    assert spanmeasure.legacy_figures_of(None) == {}


def test_a_result_is_read_as_the_typescript_reads_it_and_one_with_no_id_is_refused(tmp_path: Path) -> None:
    file = tmp_path / spanmeasure.RESULTS / "0.jsonl"
    file.parent.mkdir(parents=True)
    read = spanmeasure.StoredResult

    def given(*lines: object) -> list[spanmeasure.StoredResult]:
        file.write_text("".join(f"{json.dumps(line)}\n" for line in lines), "utf-8")
        return spanmeasure.read_results(tmp_path)

    # `record.attributes ?? {}`, and `typeof ... === "string"` of each member read from it.
    assert given(
        {"id": "a"},
        {"id": "b", "source_ids": None, "attributes": None},
        {"id": "c", "source_ids": ["s", "t"], "attributes": {"source_locator": 5, "printed_value": ["0.5"]}},
        {"id": "d", "attributes": "text"},
        {"id": "e", "attributes": [1]},
        {"id": "f", "attributes": 7},
        {"id": "g", "attributes": {"source_locator": "Table 1", "printed_value": "0.5"}},
    ) == [
        read("a", (), "", ""),
        read("b", (), "", ""),
        read("c", ("s", "t"), "", ""),
        read("d", (), "", ""),
        read("e", (), "", ""),
        read("f", (), "", ""),
        read("g", (), "Table 1", "0.5"),
    ]
    unidentified: tuple[object, ...] = ([1, 2], {"attributes": {}}, {"id": 5}, {"id": None}, "text", 7)
    for line in unidentified:
        with pytest.raises(StoreError) as refused:
            given({"id": "a"}, line)
        assert str(refused.value) == f"{file}: record 2 is not an object with an ID"
    for sources in ("s", 5, {"s": 1}, ["s", 5], [None]):
        with pytest.raises(StoreError) as refused:
            given({"id": "a", "source_ids": sources})
        assert str(refused.value) == f"{file}: the sources of a are not a list of IDs"
    listed = tmp_path / spanmeasure.SOURCES
    listed.parent.mkdir(parents=True)
    listed.write_text('{"id":"s"}\n\n{"id":"t"}\n', "utf-8")
    assert spanmeasure.source_ids(tmp_path) == ["s", "t"]
    listed.write_text('{"id":"s"}\n\n[1]\n', "utf-8")
    with pytest.raises(StoreError) as refused:
        spanmeasure.source_ids(tmp_path)
    assert str(refused.value) == f"{listed}: record 2 is not an object with an ID"


def test_a_run_whose_inputs_moved_under_it_writes_nothing_and_says_which(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lay_out(tmp_path, SPANS["recordings"]["files"], SPANS["recordings"]["objects"])
    monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
    moved = iter(["data/entities/sources.jsonl", "shared/omics/locator.ts"])

    def moving(root: Path, **options: Any) -> spanmeasure.MeasureReport:  # noqa: ANN401 - passed on as they come
        report = spanmeasure.measure(root, **options)
        with (root / next(moved)).open("a", encoding="utf-8") as file:
            file.write('{"id":"added-under-the-run"}\n')
        return report

    monkeypatch.setattr(spansaid, "measure", moving)
    detail = tmp_path / "detail.tsv"
    said = spansaid.say_measure(tmp_path, None, "2026-11-19", detail=detail)
    # A source record that was added is no change to what was measured. A source file that changed is.
    assert said.status == 1
    assert said.err[-1] == (
        "the tree moved while this run was measuring it, so nothing was written. 1 input file changed:\n"
        "  shared/omics/locator.ts\n"
        "the figures this run holds are of a tree that no longer exists. run it again."
    )
    assert said.out.startswith("class\tlocators\t")
    assert not detail.exists()
    assert not (tmp_path / "maintenance/reports").joinpath("span-resolution-figures.json").exists()
    assert not list((tmp_path / "maintenance/reports").glob("span-resolution-2026-11-19-*"))


# ---- patterns built from text that arrives at run time --------------------------------------


def test_every_unit_matches_under_the_i_flag_what_the_recorded_letters_match() -> None:
    held = json.loads((Path(jsregex.__file__).parent / jsregex.CASE_UNITS_FILE).read_text("utf-8"))
    classes = json.loads((Path(jsregex.__file__).parent / jsregex.CLASSES_FILE).read_text("utf-8"))
    assert held["recorded_from"]["script"] == "scripts/fixtures/record_case_units.mjs"
    assert held["recorded_from"]["node"] == classes["recorded_from"]["node"]
    assert held["recorded_from"]["unicode"] == classes["recorded_from"]["unicode"]
    # The two recordings were made apart, and every letter both hold has the same answer in each.
    letters = [
        (mode, atom)
        for mode, atom, _ in classes["classes"]
        if mode == "i" and re.fullmatch(r"\\u[0-9A-F]{4}", atom)
    ]
    assert len(letters) > 50
    for mode, atom in letters:
        assert jsregex.recorded_or_unit(mode, atom) == jsregex.recorded(mode, atom)
    # A unit matches itself, and what it matches matches it.
    matches = {
        int(unit, 16): {int(other, 16) for other in others.split()} for unit, others in held["matches"]
    }
    assert all(unit in others and len(others) > 1 for unit, others in matches.items())
    assert all(unit in matches[other] for unit, others in matches.items() for other in others)


def test_an_ascii_letter_matches_its_other_case_and_nothing_else() -> None:
    # The resolvers' fixed patterns are Python's own, with the `i` flag written as IGNORECASE
    # over ASCII. That is JavaScript's rule only if no other character matches an ASCII letter.
    for code in range(0x80):
        character = chr(code)
        expected = {ord(character.lower()), ord(character.upper())} if character.isalpha() else {code}
        ranges = jsregex.recorded_or_unit("i", f"\\u{code:04X}")
        assert {unit for first, last in ranges for unit in range(first, last + 1)} == expected
    assert jsregex.recorded_or_unit("i", "\\u017F") == ((0x17F, 0x17F),)
    assert jsregex.recorded_or_unit("i", "\\u212A") == ((0x212A, 0x212A),)
    assert jsregex.recorded_or_unit("i", "\\u00C9") == ((0xC9, 0xC9), (0xE9, 0xE9))
    assert jsregex.recorded_or_unit("", ".") == jsregex.recorded("", ".")
    with pytest.raises(Unsupported, match="has not been recorded from Node"):
        jsregex.recorded_or_unit("iu", "\\u0416")


def test_a_pattern_built_at_run_time_reads_any_letter_and_searches_from_a_place() -> None:
    assert aligned.caption_pattern("Table", "\u00e9\u03c3").match("  TABLES \u00c9\u03a3: x") is not None
    assert aligned.caption_pattern("Table", "\u00e9").match("Table e") is None
    with pytest.raises(Unsupported, match="has not been recorded from Node"):
        JsPattern("\u0416", "i").test("\u0436")
    assert JsPattern("\u0416", "i", jsregex.recorded_or_unit).test("\u0436")
    found = JsPattern("a+", "g").exec_from("aa b aaa", 2)
    assert found == jsregex.Found(5, "aaa", {})
    assert JsPattern("(?<=b )a", "g").exec_from("aa b aaa", 5) == jsregex.Found(5, "a", {})
    assert JsPattern("a", "g").exec_from("aa", 2) is None
    assert JsPattern("$", "g").exec_from("aa", 3) is None
    assert JsPattern("$", "g").exec_from("aa", 2) == jsregex.Found(2, "", {})
    with pytest.raises(Unsupported, match="has no g flag"):
        JsPattern("a").exec_from("a", 0)
    with pytest.raises(Unsupported, match="counts characters"):
        JsPattern(r"\p{L}", "gu").exec_from(unit_text("\U0001f600a"), 0)
    assert JsPattern(r"\p{L}", "gu").exec_from("ab", 1) == jsregex.Found(1, "b", {})
    assert JsPattern("^a").compiled.match("ab") is not None


def test_the_command_says_how_long_the_measurement_took(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The replayed commands hold the clock still. This one reads it.
    bare = next(tree for tree in SPANS["commands"] if tree["name"] == "bare")
    lay_out(tmp_path, bare["files"])
    monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
    monkeypatch.delenv("SPAN_DETAIL", raising=False)
    assert cli.main(["spans", "measure", "--root", str(tmp_path), "--date", "2026-11-19"]) == 0
    assert re.search(r"\n[0-9]+ s\n\Z", capsys.readouterr().out)
