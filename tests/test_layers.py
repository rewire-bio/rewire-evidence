"""Layer derivation: what holds of every input, the built-ins it leans on, and the PDF path.

The recorded cases are replayed in `test_layers_recorded.py`. Here are the properties that
hold whatever the input, each normaliser held to a plain reading of its rule, Node's own
answers for the built-ins the TypeScript calls, and pdftotext and pdfinfo stood in for by
scripts, because the conversion itself is another program's.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import (
    classify,
    cli,
    containers,
    grids,
    js,
    jsregex,
    layers,
    layersaid,
    markup,
    office,
    pdftext,
    registry,
    sanity,
)
from rewire_evidence.archive import ArchiveError
from rewire_evidence.canonical import canonical_line, js_utf8, sha256_hex
from rewire_evidence.textlayer import Layer, LayerFailure, character_count, layer_from_text
from tests.layered import BASE_ROW, OBJECTS, row_for
from tests.recorded import SEMANTICS

# ---------------------------------------------------------------------------------------
# The built-ins the TypeScript calls, as Node answers.


@pytest.mark.parametrize(("text", "radix", "expected"), SEMANTICS["parse_int"])
def test_a_number_is_read_from_the_start_of_text_as_parseint_reads_it(
    text: str, radix: int, expected: str
) -> None:
    found = js.parse_int(text, radix)
    # Node prints the number in its shortest form, an infinity included, and `float` reads all of them.
    assert math.isnan(found) if expected == "NaN" else found == float(expected)


@given(
    st.integers(min_value=-(2**53), max_value=2**53),
    st.text(alphabet=" \t\n", max_size=3),
    st.text(max_size=4),
)
def test_parseint_reads_a_whole_number_whatever_follows_it(number: int, before: str, after: str) -> None:
    tail = after if not after[:1].isdigit() else f" {after}"
    assert js.parse_int(f"{before}{number}{tail}") == number


@pytest.mark.parametrize(("encoding", "data", "expected"), SEMANTICS["buffer_text"])
def test_bytes_are_read_as_text_the_way_a_buffer_reads_them(encoding: str, data: str, expected: str) -> None:
    held = bytes.fromhex(data)
    if encoding == "utf8":
        assert classify.decode(b"\xef\xbb\xbf" + held, "utf-8-bom") == expected
    elif encoding == "latin1":
        assert classify.decode(held, "latin-1") == expected
    else:
        assert classify.decode(b"\xff\xfe" + held, "utf-16le") == expected


@pytest.mark.parametrize(("text", "expected"), SEMANTICS["until_nul"])
def test_a_tar_field_ends_at_a_nul_that_nothing_after_ends_a_line(text: str, expected: str) -> None:
    assert containers.until_nul(text) == expected


def test_upper_case_makes_ascii_of_the_letters_javascript_makes_ascii_of() -> None:
    # A cell reference is read in upper case, and only these letters can become part of one.
    made = [
        [f"{code:x}", upper]
        for code in range(0x80, 0x110000)
        if not 0xD800 <= code <= 0xDFFF and (upper := chr(code).upper()).isascii()
    ]
    assert made == SEMANTICS["upper_ascii"]


@pytest.mark.parametrize(("data", "expected"), SEMANTICS["inflate_raw"])
def test_a_raw_deflate_stream_is_read_as_node_reads_it(data: str, expected: str | None) -> None:
    try:
        assert containers.inflate_raw(bytes.fromhex(data), 16).hex() == expected
    except ArchiveError:
        assert expected is None


# ---------------------------------------------------------------------------------------
# Containers


def test_a_gzip_header_too_short_to_hold_a_name_holds_none() -> None:
    assert containers.gzip_name(b"\x1f\x8b\x08\x08") == ""
    assert containers.gzip_name(b"plain text, ten bytes or more") == ""
    # The extra field is flagged and the header ends before its length.
    assert containers.gzip_name(bytes([0x1F, 0x8B, 8, 0x0C, 0, 0, 0, 0, 0, 3, 1])) == ""
    assert containers.gzip_name(bytes([0x1F, 0x8B, 8, 0x08, 0, 0, 0, 0, 0, 3]) + b"a.txt\x00") == "a.txt"


FILES = st.dictionaries(
    st.text(alphabet="abcdefghij/._-", min_size=1, max_size=20).filter(
        lambda name: not name.endswith("/") and not name.startswith("/") and "//" not in name
    ),
    st.binary(max_size=200),
    max_size=6,
)


@given(FILES, st.sampled_from([zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED]))
def test_a_zip_the_standard_library_writes_expands_to_its_files(files: dict[str, bytes], method: int) -> None:
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w", method) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    expansion = containers.expand_container(held.getvalue())
    assert (expansion.kind, expansion.problems) == ("zip", [])
    assert [(member.path, member.content) for member in expansion.members] == list(files.items())
    assert all(member.sha256 == sha256_hex(member.content) for member in expansion.members)
    assert containers.zip_entry_names(held.getvalue(), len(files)) == list(files)


@given(FILES.filter(bool))
def test_a_tar_the_standard_library_writes_expands_to_its_files(files: dict[str, bytes]) -> None:
    # A tar of no files is blocks of zeros, with no magic to know it by.
    held = io.BytesIO()
    with tarfile.open(fileobj=held, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    expansion = containers.expand_container(held.getvalue())
    assert (expansion.kind, expansion.problems) == ("tar", [])
    assert [(member.path, member.content) for member in expansion.members] == list(files.items())


# ---------------------------------------------------------------------------------------
# The scanner


def tidy_by_the_letter(raw: str) -> tuple[str, list[int]]:
    """`tidyTraced` as the TypeScript writes it, one character at a time."""
    out = [-1] * len(raw)
    pieces: list[str] = []
    length = held = 0
    wrote = False
    for start, line in zip(_starts(raw), raw.split("\n"), strict=True):
        units: list[str] = []
        origin: list[int] = []
        run = -1
        for index, unit in enumerate(line, start):
            if unit in js.SPACE:
                run = index if run < 0 else run
                continue
            if run >= 0:
                if units:
                    units.append(" ")
                    origin.append(run)
                run = -1
            units.append(unit)
            origin.append(index)
        if not units:
            held = 1 if wrote else held
            continue
        if wrote:
            pieces.append("\n" * (1 + held))
            length += 1 + held
        held = 0
        for index, at in enumerate(origin):
            out[at] = length + index
        pieces.append("".join(units))
        length += len(units)
        wrote = True
    return "".join(pieces) + ("\n" if wrote else ""), out


def _starts(raw: str) -> list[int]:
    starts = [0]
    for line in raw.split("\n")[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    return starts


SPACED = st.text(alphabet="ab \t\n\u00a0\u2003\ufeff\u0085\U0001f600", max_size=60)


@given(SPACED)
def test_tidying_is_the_typescripts_loop_and_changes_nothing_a_second_time(raw: str) -> None:
    trace = markup.tidy_traced(raw)
    assert (trace.text, trace.out) == tidy_by_the_letter(raw)
    assert markup.tidy(trace.text) == trace.text
    # Every character kept is the character it was, and a run of space is one space.
    for at, placed in enumerate(trace.out):
        assert placed < 0 or trace.text[placed] == (" " if raw[at] in js.SPACE else raw[at])
    assert "\n\n\n" not in trace.text
    assert not trace.text.startswith(("\n", " "))


MARKUP = st.lists(
    st.sampled_from(
        [
            "<table>", "</table>", "<tr>", "</tr>", "<td>", "</td>", "<th colspan='2'>", "</th>", "<thead>",
            "</thead>", "<tbody>", "<caption>", "</caption>", "<label>", "</label>", "<table-wrap>",
            "</table-wrap>", "<p>", "</p>", "<script>", "</script>", "<br/>", "<!-- c -->", "<![CDATA[x]]>",
            "a", " b ", "0.5", "\n", "&amp;", "&nbsp;", "\U0001f600", "<", ">", "<td rowspan=3>",
            "<inline-graphic>",
        ]
    ),
    max_size=40,
).map("".join)  # fmt: skip


@given(MARKUP, st.sampled_from(sorted(grids.GRID_NORMALISERS)))
def test_the_scan_a_grid_is_built_by_is_the_scan_the_text_layer_is_built_by(
    source: str, normaliser: str
) -> None:
    rules = markup.XML_RULES if normaliser == "jats-table-grid@1" else markup.HTML_RULES
    grid, scanned = grids.grid_of(source, rules, normaliser, grids.GRID_NORMALISERS[normaliser], "0" * 64)
    assert scanned == markup.scan_markup(source, rules)
    for table in grid.tables:
        for cell in table.cells:
            # A cell's offsets are into the layer, and its text is what the layer holds there.
            assert cell.start is None or scanned[cell.start : cell.end] == cell.text != ""
            assert 0 <= cell.row < table.rows
            assert 0 <= cell.column < max(table.columns, 1)
        assert len(table.headers) == table.columns
        assert all(len(path) == table.header_rows for path in table.headers)
        assert table.inferred_header is None or table.header_rows == 0
    assert grids.header_of(grid)["tables"] == len(grid.tables)


@given(st.text(max_size=40).filter(lambda text: "&" not in text))
def test_text_with_no_reference_is_not_decoded(text: str) -> None:
    assert markup.decode_entities(text) == text
    assert office.read_characters(text.replace("\r", "")) == text.replace("\r", "")


@given(st.integers(min_value=1, max_value=0x10FFFF).filter(lambda code: not 0xD800 <= code <= 0xDFFF))
def test_a_numeric_reference_is_the_character_it_numbers(code: int) -> None:
    assert markup.decode_entities(f"&#{code};&#x{code:x};&#X{code:X};") == chr(code) * 3
    assert office.decode_xml(f"&#{code};") == chr(code)


# ---------------------------------------------------------------------------------------
# Office


@given(st.integers(min_value=1, max_value=26**7))
def test_a_column_label_names_its_number(column: int) -> None:
    label = office.column_label(column)
    assert office.column_number(label) == column
    assert office.parse_reference(f"${label}$7") == (7, column)


@given(st.text(max_size=30))
def test_an_escaped_cell_holds_nothing_that_would_break_a_line_or_a_field(value: str) -> None:
    escaped = office.escape_cell(value)
    assert not any(ord(character) < 0x20 or character == "\x7f" for character in escaped)
    assert escaped.count("\\") >= value.count("\\") * 2
    assert (escaped == value) == (not any(c == "\\" or ord(c) < 0x20 or c == "\x7f" for c in value))


def test_a_package_whose_directory_names_a_workbook_and_whose_bytes_are_no_zip_did_not_expand() -> None:
    # The central directory is read from the end, and the expansion refuses bytes that do not open as a zip.
    data = b"XK" + OBJECTS["macroWorkbook"][2:]
    found = office.office_layer(data)
    assert found is not None
    assert found.unsupported == (
        "office package did not expand: expandContainer was given bytes that are not gzip, zip or tar"
    )
    assert (found.kind, found.layer, found.problems) == ("ooxml", None, [])


def workbook(sheet: str) -> bytes:
    """A workbook of one sheet, whose rows are the ones given."""
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("xl/workbook.xml", '<workbook><sheets><sheet name="S" sheetId="1"/></sheets></workbook>')
        package.writestr("xl/worksheets/sheet1.xml", f"<worksheet><sheetData>{sheet}</sheetData></worksheet>")
    return held.getvalue()


#: A value that no cell holds gives the sheet a last row and no column, so its extent cannot be written.
NO_COLUMN = workbook('<row r="3"><v>5</v></row>')


def test_a_sheet_whose_extent_cannot_be_written_is_a_reason_and_not_an_error() -> None:
    # The TypeScript throws from columnLabel and catches it as it catches every refusal.
    with pytest.raises(LayerFailure, match=r"^0 is not a column number$"):
        office.column_label(0)
    found = office.office_layer(NO_COLUMN)
    assert found is not None
    assert (found.layer, found.unsupported) == (None, "xlsx normaliser failed: 0 is not a column number")
    node = layers.layer_of(NO_COLUMN, pdftotext=False)
    assert (node.layer, node.grid, node.problems) == (None, None, [])
    assert node.unsupported == "xlsx normaliser failed: 0 is not a column number"
    row = {**BASE_ROW, "sha256": sha256_hex(NO_COLUMN), "bytes": len(NO_COLUMN)}
    result = registry.layers_of_object(row, NO_COLUMN)
    assert (result.records, result.unreadable) == ([], [])
    assert result.unsupported == [
        {
            "object_sha256": row["sha256"],
            "container_path": None,
            "reason": "xlsx normaliser failed: 0 is not a column number",
        }
    ]


def test_a_build_writes_the_reason_for_such_a_sheet_and_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
    text = b"read after the workbook\n"
    for data in (NO_COLUMN, text):
        key = sha256_hex(data)
        row = {**BASE_ROW, "sha256": key, "bytes": len(data), "source_ids": ["s1"]}
        shard = tmp_path / f"data/archive/objects/{key[0]}.jsonl"
        shard.parent.mkdir(parents=True, exist_ok=True)
        shard.write_text(canonical_line(row) + "\n", encoding="utf-8")
        cached = tmp_path / f"data/archive/cache/{key[:2]}/{key}.gz"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(gzip.compress(data))
    assert cli.main(["layers", "build", "--root", str(tmp_path)]) == 0
    assert "no normaliser    1\n" in capsys.readouterr().out
    assert (tmp_path / registry.UNSUPPORTED_FILE).read_text("utf-8") == (
        '{"container_path":null,"object_sha256":"'
        + sha256_hex(NO_COLUMN)
        + '","reason":"xlsx normaliser failed: 0 is not a column number"}\n'
    )
    assert [row["object_sha256"] for row in registry.read_registry(tmp_path)] == [sha256_hex(text)]
    assert cli.main(["layers", "check", "--root", str(tmp_path)]) == 0


def test_a_close_that_closes_nothing_does_not_stop_the_next_string_being_kept() -> None:
    table = "<sst><si></t><t>kept</t></si><si><t>a</t></t></t><t>b</t></si></sst>"
    package = office.OfficePackage("xlsx", "a case", {"xl/sharedStrings.xml": table.encode()}, [])
    assert office.shared_strings(package) == ["kept", "ab"]


def test_a_sheet_may_be_as_wide_as_the_limit_and_no_wider() -> None:
    # FAN is column 4,096 and FAO the one after it.
    widest = office.office_layer(workbook('<row r="1"><c r="FAN1"><v>1</v></c></row>'))
    assert widest is not None
    assert widest.layer is not None
    assert widest.layer.content == b"\x0c# sheet\t1\tS\tA1:FAN1\n" + b"\t" * 4095 + b"1\n"
    wider = office.office_layer(workbook('<row r="1"><c r="FAO1"><v>1</v></c></row>'))
    assert wider is not None
    assert (wider.layer, wider.unsupported) == (
        None,
        "xlsx normaliser failed: sheet S holds 4097 columns, over the 4096 column limit",
    )


# ---------------------------------------------------------------------------------------
# Text layers and the sanity gate


@given(st.text(alphabet=st.characters(blacklist_categories=["Cs"], blacklist_characters="\x00"), max_size=50))
def test_text_that_is_already_utf8_is_its_own_layer(text: str) -> None:
    data = text.encode("utf-8")
    layer = layers.text_layer(data)
    if text.startswith("\ufeff"):
        assert (layer.identity, layer.derivation) == (False, "text-utf8@1 decode utf-8-bom to utf-8")
        return
    assert (layer.identity, layer.content, layer.characters) == (True, data, len(text))
    assert layer.input_sha256 == layer.output_sha256 == sha256_hex(data)
    assert (layer.tool, layer.tool_version) == ("text-utf8@1", "1")


def test_half_a_surrogate_pair_is_written_as_node_writes_it() -> None:
    # Two halves that end up side by side are one character, and a half alone is U+FFFD.
    joined = layer_from_text(b"", "\ud83d\ude00", "text-utf8@1", "a case")
    assert (joined.content, joined.characters) == ("\U0001f600".encode(), 1)
    # A tag between the halves leaves them side by side in the layer of a UTF-16 page.
    page = "<html><body><p>a\ud83d<b></b>\ude00z</p></body></html>".encode("utf-16-le", "surrogatepass")
    node = layers.layer_of(b"\xff\xfe" + page, pdftotext=False)
    assert node.layer is not None
    assert (node.layer.content, node.layer.characters) == ("a\U0001f600z\n".encode(), 4)
    alone = layer_from_text(b"", "a\ud83d", "text-utf8@1", "a case")
    assert (alone.content, alone.characters) == (js_utf8("a\ufffd"), 2)
    assert character_count("\U0001f600\ude00") == 2


def test_a_normaliser_refuses_bytes_that_are_not_text() -> None:
    for derive, name in (
        (layers.text_layer, "textLayer"),
        (layers.xml_layer, "xmlLayer"),
        (layers.html_layer, "htmlLayer"),
    ):
        with pytest.raises(LayerFailure, match=f"{name} was given bytes that do not decode as text"):
            derive(b"\x00\x01\x02")
    html = layers.html_layer(b"<p>x</p>")
    with pytest.raises(LayerFailure, match="gridLayer was given bytes that do not decode as text"):
        grids.grid_layer(b"\x00\x01", html)
    with pytest.raises(
        LayerFailure, match=r"the grid scan of [a-f0-9]{64} gives [a-f0-9]{64} and the text layer is"
    ):
        grids.grid_layer(b"<p>other</p>", html)
    assert grids.grid_layer(b"<p>x</p>", html) is None
    assert grids.grid_layer(b"plain", layers.text_layer(b"plain")) is None


def metrics_by_the_letter(text: str) -> dict[str, int | None]:
    """`metricsOf` as the TypeScript writes it, one character at a time, over Node's own sets."""
    sets = [
        {code for first, last in jsregex.recorded("u", held.source) for code in range(first, last + 1)}
        for held in sanity.PATTERNS
    ]
    counts = dict.fromkeys(("non_space", "replacement", "printable", "digits", "letters", "punctuation"), 0)
    run = longest = breaks = 0
    for character in text:
        code = ord(character)
        space, digit = code in sets[0], code in sets[1]
        letter = not digit and code in sets[2]
        counts["replacement"] += character == "\ufffd"
        breaks += character == "\f"
        counts["printable"] += not ((code < 0x20 and character not in "\t\n\r\f") or 0x7F <= code <= 0x9F)
        counts["non_space"] += not space
        counts["digits"] += digit
        counts["letters"] += letter
        run = run + 1 if not (digit or letter or space) else 0
        counts["punctuation"] += not (digit or letter or space)
        longest = max(longest, run)
    return {
        "characters": len(text),
        **counts,
        "longest_punctuation_run": longest,
        "page_breaks": breaks,
        "pdf_pages": None,
    }


@given(st.text(alphabet=st.characters(blacklist_categories=["Cs"]), max_size=80))
def test_the_counts_are_the_typescripts_loop_and_add_up(text: str) -> None:
    metrics = sanity.metrics_of(text)
    assert metrics == metrics_by_the_letter(text)
    counted = sanity.ratios_of(metrics)
    assert all(
        0 <= ratio <= 1
        for ratio in (counted.printable, counted.replacement, counted.punctuation, counted.digits)
    )
    verdict = sanity.sanity_of(metrics, "text-utf8@1")
    assert (verdict.verdict == "pass") == (verdict.reasons == [])
    assert sanity.usable(verdict.verdict) == (verdict.verdict != "fail")


@given(st.binary(max_size=300), st.sampled_from([None, "text/html", "text/csv"]))
def test_any_bytes_have_a_tree_or_are_refused_in_nodes_words(data: bytes, hint: str | None) -> None:
    if data.startswith(b"\xfe\xff") and len(data) % 2:
        with pytest.raises(classify.Undecodable, match=r"^Buffer size must be a multiple of 16-bits$"):
            layers.layer_of(data, hint, pdftotext=False)
        return
    node = layers.layer_of(data, hint, pdftotext=False)
    assert node.sha256 == sha256_hex(data)
    for held, _, _ in layers.walk(node):
        assert held.layer is None or held.unsupported is None
        assert held.grid is None or held.layer is not None
        assert held.layer is None or held.layer.output_sha256 == sha256_hex(held.layer.content)
        assert held.layer is None or held.layer.identity == (held.layer.content == held.content)


def test_the_two_files_are_ordered_by_utf16_unit_in_the_path_and_in_the_reason() -> None:
    # A character beyond U+FFFF is two units from U+D800 on, so JavaScript puts it before U+E000,
    # which follows it by number and by its bytes in UTF-8.
    astral, private = "\U0001f600", "\ue000"
    paths = [private, None, astral, "z"]
    ordered = [None, "z", astral, private]
    records = [{"layer_sha256": "a" * 64, "object_sha256": "b" * 64, "container_path": path} for path in paths]
    written = registry.serialise_registry(records).splitlines()
    assert [json.loads(line)["container_path"] for line in written] == ordered
    problems = [{"object_sha256": "b" * 64, "container_path": path, "reason": "r"} for path in paths]
    written = registry.serialise_unsupported(problems).splitlines()
    assert [json.loads(line)["container_path"] for line in written] == ordered
    problems = [
        {"object_sha256": "b" * 64, "container_path": None, "reason": reason} for reason in (private, astral, "z")
    ]
    written = registry.serialise_unsupported(problems).splitlines()
    assert [json.loads(line)["reason"] for line in written] == ["z", astral, private]


# ---------------------------------------------------------------------------------------
# What only another answer from the detector or the grid could reach


def test_a_package_the_detector_stops_recognising_has_no_layer_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(layers, "office_layer", lambda *_: None)
    node = layers.layer_of(OBJECTS["plainWorkbook"], pdftotext=False)
    assert (node.layer, node.unsupported) == (None, "office package that the detector no longer recognises")


def test_a_grid_that_cannot_be_built_leaves_the_text_layer_and_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_: object) -> Layer | None:
        raise LayerFailure("the scan gave other text")

    monkeypatch.setattr(grids, "grid_layer", refuse)
    node = layers.layer_of(OBJECTS["html"], "text/html", pdftotext=False)
    assert node.layer is not None
    assert (node.grid, node.problems) == (
        None,
        ["table grid over html-text@1 failed: the scan gave other text"],
    )


def test_a_progress_line_is_written_after_each_hundred_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(layersaid, "PROGRESS_EVERY", 2)
    rows = sorted(
        (row_for(name) for name in ("text", "crlf", "markdown", "bom", "png")), key=lambda row: row["sha256"]
    )
    for row in rows:
        shard = tmp_path / f"data/archive/objects/{row['sha256'][0]}.jsonl"
        shard.parent.mkdir(parents=True, exist_ok=True)
        with shard.open("a", encoding="utf-8") as file:
            file.write(canonical_line(row) + "\n")
    said = layersaid.say_check(tmp_path)
    assert said.err[:2] == ("  2 of 5 objects", "  4 of 5 objects")
    assert said.err[2:] == tuple(f"{row['sha256']}: the bytes are not in the object cache" for row in rows)
    assert (said.out, said.status) == ("5 problems", 1)


# ---------------------------------------------------------------------------------------
# pdftotext and pdfinfo, stood in for by scripts

PDFTOTEXT = """#!/bin/sh
if [ "$1" = "-v" ]; then
  case "$MODE" in
    unnamed) echo "some other program" >&2; exit 0 ;;
    refuses-version) echo "pdftotext version 9.99.0" >&2; exit 3 ;;
  esac
  echo "pdftotext version 9.99.0" >&2
  echo "Copyright nobody" >&2
  exit 0
fi
cat > /dev/null
case "$MODE" in
  fails) echo "  Syntax Error: not a PDF  " >&2; exit 1 ;;
  slow) sleep 5 ;;
  not-utf8) printf 'caf\\351' ;;
  warns) printf 'ok'; printf 'www' >&2 ;;
  *) printf 'page one \\303\\251\\014page two\\014' ;;
esac
"""
PDFINFO = """#!/bin/sh
case "$MODE" in
  no-pages) echo "Title: Pages: 9" ;;
  unreadable) echo "Pages: 3"; exit 1 ;;
  *) printf 'Title:          A paper\\nPages:          2\\nEncrypted:      no\\n' ;;
esac
"""


@pytest.fixture
def programs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A folder holding scripts named pdftotext and pdfinfo, and nothing else on the path but the shell's."""
    folder = tmp_path / "bin"
    folder.mkdir()
    for name, body in (("pdftotext", PDFTOTEXT), ("pdfinfo", PDFINFO)):
        (folder / name).write_text(body, encoding="utf-8")
        (folder / name).chmod(stat.S_IRWXU)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}/bin{os.pathsep}/usr/bin")
    monkeypatch.setenv("MODE", "")
    pdftext.has_pdftotext.cache_clear()
    return folder


def test_a_pdf_layer_is_what_pdftotext_writes_and_names_its_version(programs: Path) -> None:
    data = OBJECTS["pdf"]
    layer = pdftext.pdf_layer(data)
    assert layer.content == "page one \u00e9\fpage two\f".encode()
    assert (layer.characters, layer.identity, layer.tool) == (20, False, "pdftotext")
    assert layer.tool_version == pdftext.pdftotext_version() == "pdftotext version 9.99.0"
    assert layer.derivation == "pdf-text-layer@1 pdftotext -layout -enc UTF-8 - - (pdftotext version 9.99.0)"
    assert (layer.input_sha256, layer.output_sha256) == (sha256_hex(data), sha256_hex(layer.content))
    assert pdftext.pdf_page_count(data) == 2
    assert pdftext.has_pdftotext()
    assert programs.is_dir()


def test_a_stored_pdf_gives_a_row_with_its_pages_and_fails_when_they_are_not_its_form_feeds(
    programs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = registry.layers_of_object(row_for("pdf"), OBJECTS["pdf"])
    (row,) = found.records
    assert (row["normaliser"], row["encoding"], row["media_type"]) == (
        "pdf-text-layer@1",
        None,
        "application/pdf",
    )
    assert (row["metrics"], row["sanity"]) == (
        {
            "characters": 20,
            "non_space": 15,
            "replacement": 0,
            "printable": 20,
            "digits": 0,
            "letters": 15,
            "punctuation": 0,
            "longest_punctuation_run": 0,
            "page_breaks": 2,
            "pdf_pages": 2,
        },
        "pass",
    )
    monkeypatch.setenv("MODE", "no-pages")
    (unread,) = registry.layers_of_object(row_for("pdf"), OBJECTS["pdf"]).records
    assert (unread["metrics"]["pdf_pages"], unread["sanity"]) == (None, "pass")  # type: ignore[index, call-overload]
    assert programs.is_dir()


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("fails", "pdftotext failed: Syntax Error: not a PDF"),
        ("slow", "spawnSync pdftotext ETIMEDOUT"),
        ("unnamed", "Could not identify the pdftotext version used for extraction"),
        ("refuses-version", "Could not identify the pdftotext version used for extraction"),
    ],
)
def test_a_conversion_that_fails_is_a_reason_and_not_a_layer(
    mode: str, reason: str, programs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODE", mode)
    monkeypatch.setattr(pdftext, "TIMEOUT", 1)
    node = layers.layer_of(OBJECTS["pdf"])
    assert (node.layer, node.unsupported) == (None, f"pdf normaliser failed: {reason}")
    assert programs.is_dir()


def test_output_that_is_too_long_or_is_not_utf8_is_refused(
    programs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODE", "not-utf8")
    key = sha256_hex(OBJECTS["pdf"])
    with pytest.raises(LayerFailure, match=f"pdftotext output for {key} does not round trip through UTF-8"):
        pdftext.pdf_layer(OBJECTS["pdf"])
    monkeypatch.setenv("MODE", "")
    monkeypatch.setattr(pdftext, "MAX_OUTPUT", 4)
    with pytest.raises(LayerFailure, match="spawnSync pdftotext ENOBUFS"):
        pdftext.pdf_layer(OBJECTS["pdf"])
    # The limit is on either stream: a conversion that writes little and warns at length is refused too.
    monkeypatch.setenv("MODE", "warns")
    assert pdftext.pdf_layer(OBJECTS["pdf"]).content == b"ok"
    monkeypatch.setattr(pdftext, "MAX_OUTPUT", 3)
    assert pdftext.pdf_layer(OBJECTS["pdf"]).content == b"ok"
    monkeypatch.setattr(pdftext, "MAX_OUTPUT", 2)
    with pytest.raises(LayerFailure, match="spawnSync pdftotext ENOBUFS"):
        pdftext.pdf_layer(OBJECTS["pdf"])
    assert programs.is_dir()


def test_pages_are_unknown_when_pdfinfo_cannot_say(programs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODE", "unreadable")
    assert pdftext.pdf_page_count(b"%PDF-") is None
    (programs / "pdfinfo").unlink()
    assert pdftext.pdf_page_count(b"%PDF-") is None


def test_with_no_pdftotext_a_pdf_has_no_layer_and_the_reason_says_so(programs: Path) -> None:
    (programs / "pdftotext").unlink()
    assert not pdftext.has_pdftotext()
    node = layers.layer_of(OBJECTS["pdf"])
    assert (node.layer, node.unsupported) == (None, "pdftotext is not installed, so no PDF layer was made")
    with pytest.raises(LayerFailure, match="spawnSync pdftotext ENOENT"):
        pdftext.pdftotext_version()
    pdftext.has_pdftotext.cache_clear()
