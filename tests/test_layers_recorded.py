"""Layer derivation, replayed against what the TypeScript did with each case.

Every section of `tests/fixtures/layers.json` is a list of inputs with what the data
repository's own code returned or threw for each. The port has to give the same, in the same
words. A problem line that quotes zlib is the exception: the words after the colon are
zlib's as Node relays them, and this library says them in its own.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import cli, grids, layers, markup, office, pdftext, registry, sanity, textlayer
from rewire_evidence.archive import ArchiveError
from rewire_evidence.classify import Undecodable, classify, decode, encoding_of
from rewire_evidence.containers import expand_container, zip_entry_names
from tests.layered import LAYERS, OBJECTS, classification_of, key_of, layer_of, limits_of, node_of, row_for

ZLIB_WORDS = re.compile(r"(gzip expansion failed|did not expand): .*")


@pytest.fixture(autouse=True)
def _no_pdftotext(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorder ran on a machine with no pdftotext, and so does every test here."""
    monkeypatch.setattr(pdftext, "has_pdftotext", lambda: False)


def test_the_recording_is_of_the_pinned_commit_and_of_every_file_the_port_was_made_from() -> None:
    workflow = (Path(__file__).parent.parent / ".github/workflows/checks.yml").read_text("utf-8")
    source = LAYERS["recorded_from"]
    assert f"STORE_REF: {source['store_ref']}\n" in workflow
    assert source["script"] == "scripts/fixtures/record_layers_fixture.mjs"
    assert len(LAYERS["modules"]) == 23
    assert all(re.fullmatch(r"[a-f0-9]{64}", digest) for digest in LAYERS["modules"].values())


@pytest.mark.parametrize(("data", "hint", "expected"), LAYERS["classify"])
def test_bytes_are_classified_as_the_typescript_classifies_them(
    data: str, hint: str | None, expected: dict[str, Any]
) -> None:
    try:
        got = classification_of(classify(bytes.fromhex(data), hint))
    except Undecodable as error:
        got = {"error": str(error)}
    assert got == expected


@pytest.mark.parametrize(("data", "expected"), LAYERS["encoding"])
def test_the_encoding_of_bytes_is_the_one_the_typescript_names(data: str, expected: str | None) -> None:
    assert encoding_of(bytes.fromhex(data)) == expected


@pytest.mark.parametrize(("data", "encoding", "expected"), LAYERS["decode"])
def test_bytes_decode_to_the_text_node_holds(data: str, encoding: str, expected: Any) -> None:
    try:
        got: Any = decode(bytes.fromhex(data), encoding)
    except Undecodable as error:
        got = {"error": str(error)}
    assert got == expected


def without_zlib_words(problems: list[str]) -> list[str]:
    return [ZLIB_WORDS.sub(r"\1", problem) for problem in problems]


@pytest.mark.parametrize(("data", "limits", "words", "expected"), LAYERS["expand"])
def test_a_container_expands_to_the_members_the_typescript_reads(
    data: str, limits: dict[str, int] | None, words: str | None, expected: dict[str, Any]
) -> None:
    try:
        expansion = expand_container(bytes.fromhex(data), limits_of(limits))
    except ArchiveError as error:
        assert expected == {"error": str(error)}
        return
    got = {
        "kind": expansion.kind,
        "members": [
            {"path": member.path, "sha256": member.sha256, "bytes": len(member.content)}
            for member in expansion.members
        ],
        "problems": expansion.problems,
    }
    # Where the cases expect zlib's words there are some, and they are the only thing left out.
    assert not words or any(ZLIB_WORDS.search(problem) for problem in expected["problems"])
    got["problems"] = without_zlib_words(got["problems"])
    assert got == {**expected, "problems": without_zlib_words(expected["problems"])}


@pytest.mark.parametrize(("data", "limit", "expected"), LAYERS["zip_names"])
def test_the_names_in_a_zip_directory_are_read_without_expanding_it(
    data: str, limit: int, expected: list[str]
) -> None:
    assert zip_entry_names(bytes.fromhex(data), limit) == expected


@pytest.mark.parametrize(("text", "in_markup", "in_xml"), LAYERS["entities"])
def test_references_decode_as_each_table_decodes_them(text: str, in_markup: str, in_xml: str) -> None:
    assert markup.decode_entities(text) == in_markup
    assert office.decode_xml(text) == in_xml


class Told(markup.ScanEvents):
    """Every event of a scan, as the recorder lists them."""

    def __init__(self) -> None:
        self.events: list[list[Any]] = []

    def open(self, name: str, attributes: str, at: int) -> None:
        self.events.append(["open", name, attributes, at])

    def close(self, name: str, at: int) -> None:
        self.events.append(["close", name, at])

    def text(self, start: int, end: int) -> None:
        self.events.append(["text", start, end])


RULES = {"xml": markup.XML_RULES, "html": markup.HTML_RULES}


@pytest.mark.parametrize(("kind", "text", "raw", "tidied", "events"), LAYERS["scan"])
def test_markup_is_scanned_to_the_text_and_the_events_the_typescript_gives(
    kind: str, text: str, raw: str, tidied: str, events: list[list[Any]]
) -> None:
    told = Told()
    assert markup.scan_raw(text, RULES[kind], told) == raw
    assert told.events == events
    assert markup.scan_raw(text, RULES[kind]) == raw
    assert markup.scan_markup(text, RULES[kind]) == tidied
    # A listener that hears nothing changes nothing.
    assert markup.scan_markup(text, RULES[kind], markup.ScanEvents()) == tidied


@pytest.mark.parametrize(("raw", "text", "out"), LAYERS["tidy"])
def test_text_is_tidied_with_every_character_placed_where_the_typescript_places_it(
    raw: str, text: str, out: list[int]
) -> None:
    trace = markup.tidy_traced(raw)
    assert (trace.text, trace.out) == (text, out)
    assert markup.tidy(raw) == text


@pytest.mark.parametrize(("raw", "text"), LAYERS["tidy_text"])
def test_half_a_character_is_tidied_as_any_other(raw: str, text: str) -> None:
    assert markup.tidy(raw) == text


def test_the_derivations_and_the_normalisers_are_named_as_the_typescript_names_them() -> None:
    assert LAYERS["derivations"]["html"] == markup.HTML_DERIVATION
    assert tuple(LAYERS["derivations"]["normalisers"]) == textlayer.NORMALISERS
    assert [[name, grids.grid_normaliser_for(name)] for name in textlayer.NORMALISERS] == LAYERS["grid_for"]
    assert {name: str(value) for name, value in LAYERS["thresholds"].items()} == dict(sanity.THRESHOLDS)
    assert list(LAYERS["thresholds"].values()) == [
        sanity.PRINTABLE_FAIL,
        sanity.PRINTABLE_SUSPECT,
        sanity.REPLACEMENT_FAIL,
        sanity.MOJIBAKE_PUNCTUATION,
        sanity.MOJIBAKE_DIGITS,
        sanity.MOJIBAKE_CHARACTERS,
    ]


class Heard(office.XmlHandler):
    def __init__(self) -> None:
        self.events: list[list[Any]] = []

    def open(self, name: str, attributes: Mapping[str, str]) -> None:
        self.events.append(["open", name, [list(pair) for pair in attributes.items()]])

    def close(self, name: str) -> None:
        self.events.append(["close", name])

    def text(self, value: str) -> None:
        self.events.append(["text", value])


@pytest.mark.parametrize(("text", "events"), LAYERS["xml_scan"])
def test_an_office_part_is_scanned_to_the_events_the_typescript_gives(
    text: str, events: list[list[Any]]
) -> None:
    heard = Heard()
    office.scan_xml(text, heard)
    # The TypeScript also says whether an element closed itself. Here it is closed straight after.
    assert heard.events == [event[:3] if event[0] == "open" else event for event in events]
    office.scan_xml(text, office.XmlHandler())


@pytest.mark.parametrize(("names", "expected"), LAYERS["names"])
def test_the_kind_of_a_package_is_read_from_its_member_names(
    names: list[str], expected: dict[str, str] | None
) -> None:
    found = office.kind_from_names(names)
    assert (asdict(found) if found else None) == expected


def test_each_kind_has_the_media_type_and_the_reason_the_typescript_gives_it() -> None:
    assert tuple(LAYERS["kinds"]) == office.OFFICE_KINDS
    for kind, (media_type, normalised, reason) in LAYERS["kinds"].items():
        assert office.OFFICE_MEDIA_TYPES[kind] == media_type
        assert (kind in office.NORMALISED_KINDS) is normalised
        assert office.unsupported_reason(kind) == reason


def test_columns_references_and_escapes_are_the_typescripts() -> None:
    columns = LAYERS["columns"]
    assert [[label, office.column_number(label)] for label, _ in columns["numbers"]] == columns["numbers"]
    for column, expected in columns["labels"]:
        try:
            assert office.column_label(column) == expected
        except ValueError as error:
            assert expected == {"error": str(error)}
    for text, expected in columns["references"]:
        found = office.parse_reference(text)
        assert (found and {"row": found[0], "column": found[1]}) == expected, text
    assert [[text, office.escape_cell(text)] for text, _ in columns["escapes"]] == columns["escapes"]


@pytest.mark.parametrize(("text", "expected"), LAYERS["documents"])
def test_a_word_body_is_serialised_as_the_typescript_serialises_it(text: str, expected: str) -> None:
    assert office.serialise_document(text) == expected


@pytest.mark.parametrize(("text", "strings", "expected"), LAYERS["sheets"])
def test_a_worksheet_is_read_to_the_cells_the_typescript_reads(
    text: str, strings: list[str], expected: dict[str, Any]
) -> None:
    problems: list[str] = []
    sheet = office.read_sheet(text, strings, "named", "xl/worksheets/case.xml", problems)
    got = {
        "path": sheet.path,
        "cells": [asdict(cell) for cell in sheet.cells],
        "lastRow": sheet.last_row,
        "lastColumn": sheet.last_column,
    }
    assert {"sheet": got, "problems": problems} == expected
    assert sheet.name == "named"


@pytest.mark.parametrize(("members", "expected"), LAYERS["parts"])
def test_the_shared_strings_and_the_sheets_of_a_workbook_are_the_typescripts(
    members: dict[str, str], expected: dict[str, Any]
) -> None:
    held = {name: bytes.fromhex(data) for name, data in members.items()}
    problems: list[str] = []
    strings = office.shared_strings(office.OfficePackage("xlsx", "a case", held, []))
    sheets = office.sheet_parts(office.OfficePackage("xlsx", "a case", held, []), problems)
    got = {
        "strings": strings,
        "sheets": [{"name": name, "path": path} for name, path in sheets],
        "problems": problems,
    }
    assert got == expected


def office_result(found: office.OfficeResult | None) -> dict[str, Any] | None:
    return found and {**asdict(found), "layer": layer_of(found.layer)}


@pytest.mark.parametrize(("name", "expected"), LAYERS["office"])
def test_an_office_package_gives_the_layer_or_the_reason_the_typescript_gives(
    name: str, expected: dict[str, Any]
) -> None:
    got = office_result(office.office_layer(OBJECTS[name]))
    assert got is not None
    got["problems"] = without_zlib_words(got["problems"])
    assert got == {**expected, "problems": without_zlib_words(expected["problems"])}


@pytest.mark.parametrize(("data", "expected"), LAYERS["office_other"])
def test_bytes_that_are_no_package_give_no_office_layer(data: str, expected: dict[str, Any] | None) -> None:
    assert office_result(office.office_layer(bytes.fromhex(data))) == expected


@pytest.mark.parametrize(("normaliser", "text", "built", "layered"), LAYERS["grids"])
def test_a_grid_is_the_bytes_the_typescript_writes(
    normaliser: str, text: str, built: dict[str, Any], layered: dict[str, Any]
) -> None:
    kind = "xml" if normaliser == "jats-table-grid@1" else "html"
    grid, scanned = grids.grid_of(text, RULES[kind], normaliser, "the text normaliser", "0" * 64)
    got = {"serialised": grids.serialise_grid(grid), "text": scanned, "header": grids.header_of(grid)}
    assert got == built
    node = layers.layer_of(text.encode("utf-8"), None if kind == "xml" else "text/html", pdftotext=False)
    assert {"text": layer_of(node.layer), "grid": layer_of(node.grid), "problems": node.problems} == layered


@pytest.mark.parametrize(("table", "expected"), LAYERS["candidates"])
def test_the_candidate_header_is_chosen_or_declined_as_the_typescript_does(
    table: dict[str, Any], expected: dict[str, Any]
) -> None:
    cells = [grids.GridCell(**cell) for cell in table["cells"]]
    header, declined = grids.candidate_header(table["rows"], table["columns"], cells, thead=table["thead"])
    assert {"header": header and asdict(header), "declined": declined} == expected


@pytest.mark.parametrize(("text", "number", "value"), LAYERS["prints"])
def test_a_cell_prints_a_number_or_a_value_when_the_typescript_says_so(
    text: str, *, number: bool, value: bool
) -> None:
    assert (grids.prints_number(text), grids.prints_value(text)) == (number, value)


@pytest.mark.parametrize(("name", "media_type", "options", "expected"), LAYERS["trees"])
def test_the_layer_tree_of_an_object_is_the_typescripts(
    name: str, media_type: str | None, options: dict[str, Any], expected: dict[str, Any]
) -> None:
    try:
        node = layers.layer_of(
            OBJECTS[name],
            media_type,
            max_depth=options.get("maxDepth", layers.MAX_DEPTH),
            limits=limits_of(options.get("limits")),
            pdftotext=False,
        )
    except Undecodable as error:
        assert expected == {"error": str(error)}
        return
    got = node_of(node)
    assert strip_zlib(got) == strip_zlib(expected)


def strip_zlib(node: dict[str, Any]) -> dict[str, Any]:
    """A tree with the words of zlib cut from every problem line in it."""
    return {
        **node,
        "problems": without_zlib_words(node["problems"]),
        "members": [{"path": held["path"], "node": strip_zlib(held["node"])} for held in node["members"]],
    }


@pytest.mark.parametrize(("name", "expected"), LAYERS["walks"])
def test_a_tree_is_walked_parents_first(name: str, expected: list[list[Any]]) -> None:
    walked = layers.walk(layers.layer_of(OBJECTS[name]))
    assert [[node.sha256, parent, path] for node, parent, path in walked] == expected


@pytest.mark.parametrize(("text", "metrics", "ratios"), LAYERS["metrics"])
def test_the_counts_of_a_layer_are_the_typescripts(
    text: str, metrics: dict[str, Any], ratios: dict[str, float]
) -> None:
    assert sanity.metrics_of(text) == metrics
    assert asdict(sanity.ratios_of(metrics)) == ratios


def test_every_character_counts_as_node_counts_it() -> None:
    # The letters, digits and spaces of the Unicode version Node carries, not of Python's.
    everything = "".join(chr(code) for code in range(0x110000) if not 0xD800 <= code <= 0xDFFF)
    assert sanity.metrics_of(everything, 7) == LAYERS["metrics_of_every_character"]


@pytest.mark.parametrize(("metrics", "normaliser", "expected", "ratios", "usable"), LAYERS["sanity"])
def test_the_verdict_on_a_layer_is_the_typescripts(
    metrics: dict[str, Any], normaliser: str, expected: dict[str, Any], ratios: dict[str, float], *, usable: bool
) -> None:
    found = sanity.sanity_of(metrics, normaliser)
    assert asdict(found) == expected
    assert asdict(sanity.ratios_of(metrics)) == ratios
    assert sanity.usable(found.verdict) is usable


@pytest.mark.parametrize(("name", "patch", "expected"), LAYERS["rows"])
def test_the_registry_rows_of_an_object_are_the_typescripts(
    name: str, patch: dict[str, Any], expected: dict[str, Any]
) -> None:
    found = registry.layers_of_object(row_for(name, patch), OBJECTS[name])
    assert asdict(found) == expected
    # Each row is one the schema reads back as it stands.
    assert [registry.parse(registry.LAYER_RECORD, record) for record in found.records] == found.records


@pytest.mark.parametrize("build", LAYERS["builds"])
def test_a_registry_is_built_and_written_as_the_typescript_builds_and_writes_it(build: dict[str, Any]) -> None:
    held = {key_of(name): OBJECTS[name] for name in build["names"] if name not in build["missing"]}
    seen: list[list[Any]] = []

    def read(key: str) -> bytes:
        if key not in held:
            raise ArchiveError("the bytes are not in the object cache")
        return held[key]

    rows = [row_for(name, {"source_ids": [name, "shared"]}) for name in build["names"]]
    result = registry.build_registry(rows, read, lambda key, done: seen.append([key, done]))
    assert asdict(result) == build["result"]
    assert seen == build["seen"]
    assert registry.build_registry(rows, read).records == result.records
    assert registry.serialise_registry(result.records[::-1]) == build["registry"]
    assert registry.serialise_unsupported(result.unsupported[::-1]) == build["unsupported"]


def lay_out(root: Path, files: dict[str, str]) -> None:
    for file, data in files.items():
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        (root / file).write_bytes(bytes.fromhex(data))


WRITTEN = (registry.REGISTRY_FILE, registry.UNSUPPORTED_FILE)
COMMANDS = [
    pytest.param(tree, runs, id=f"{tree['name']}-{'-'.join(run['argv'][0] for run in runs if run['argv']) or 'none'}")
    for tree in LAYERS["commands"]
    for runs in tree["runs"]
]


@pytest.mark.parametrize(("tree", "runs"), COMMANDS)
def test_the_commands_print_and_write_what_the_typescripts_do(
    tree: dict[str, Any],
    runs: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lay_out(tmp_path, tree["files"])
    monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
    for run in runs:
        known = run["argv"][:1] in (["build"], ["check"], ["thresholds"])
        rooted = ["--root", str(tmp_path)] if known else []
        try:
            status = cli.main(["layers", *run["argv"], *rooted])
        except SystemExit as left:
            # The TypeScript prints its usage for a command it does not know, and so does argparse.
            status = int(str(left.code))
        said = capsys.readouterr()
        assert status == run["status"]
        if known:
            assert said.out.replace(str(tmp_path), "<root>") == run["out"]
            assert said.err.replace(str(tmp_path), "<root>") == run["err"]
        written = {file: (tmp_path / file).read_text("utf-8") for file in WRITTEN if (tmp_path / file).exists()}
        assert written == run["written"]
    assert registry.read_registry(tmp_path) == [
        registry.parse(registry.LAYER_RECORD, row)
        for row in map(__import__("json").loads, runs[-1]["written"].get(registry.REGISTRY_FILE, "").splitlines())
    ]
