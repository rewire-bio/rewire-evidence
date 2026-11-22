"""Where the archive read side follows a JavaScript built-in that Python does otherwise.

The pinned store has no licence family beyond ASCII, no source ID that sorts differently by
UTF-16 unit and by character, and no cache named by an empty variable, so the store cannot
hold the port to any of these. `tests/fixtures/js-semantics.json` holds what Node does, as
`scripts/fixtures/record_js_fixture.mjs` records it, and each test here lays out a tree on
which the difference shows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from rewire_evidence import archive, licence, worklist
from rewire_evidence.canonical import JsonObject, sha256_hex
from rewire_evidence.jsonl import StoreError
from tests.recorded import SEMANTICS
from tests.test_archive import held_map

ROW: dict[str, Any] = {
    "sha256": sha256_hex(b"an object"),
    "bytes": 9,
    "media_type": "text/html",
    "layer": "exact-bytes",
    "origin": "https://example.org/a",
    "requested_url": None,
    "first_seen_at": "2026-11-17T16:10:11Z",
    "store": "r2",
    "mirrors": [],
    "visibility": "restricted",
    "archive_tier": "t4-private",
    "observed_licence": None,
    "exposure": "policy",
    "derived_from": None,
    "derivation": None,
    "previous_path": None,
}


def lay_out(root: Path, licences: list[tuple[str, str, str]], sources: list[dict[str, Any]]) -> None:
    """A licence map of (string, family, basis) rows and the source records given."""
    held = "".join(f"{text}\t{family}\tt0-public\t{basis}\tx\n" for text, family, basis in licences)
    (root / "data/archive").mkdir(parents=True)
    (root / licence.LICENCE_MAP).write_text(f"{'\t'.join(licence.COLUMNS)}\n{held}", encoding="utf-8")
    (root / "data/entities").mkdir(parents=True)
    lines = "".join(f"{json.dumps(source)}\n" for source in sources)
    (root / worklist.SOURCES).write_text(lines, encoding="utf-8")


def source(at: int, **attributes: object) -> dict[str, Any]:
    return {"id": f"s{at}", "kind": "source", "attributes": attributes}


@pytest.mark.parametrize(
    ("pairs", "printed"),
    [case for case in SEMANTICS["pair_order"] if case[0]],
    ids=lambda case: str(case)[:40],
)
def test_the_counts_are_ordered_and_padded_as_javascript_orders_and_pads_them(
    tmp_path: Path, pairs: list[tuple[str, int]], printed: list[str]
) -> None:
    # Each name is the family of one licence string, carried by as many sources as its count.
    rows = [(f"licence {at}", name, "licence-text") for at, (name, _) in enumerate(pairs)]
    sources = [
        source(at * 1000 + nth, licence=f"licence {at}")
        for at, (_, count) in enumerate(pairs)
        for nth in range(count)
    ]
    lay_out(tmp_path, rows, sources)
    report = licence.counts_report(tmp_path).split("\n")
    families = report.index("sources by licence family")
    bases = report.index("sources by determination basis")
    assert report[families + 1 : bases - 1] == printed
    assert report[bases + 1] == f"  {'licence-text'.ljust(20)} {str(len(sources)).rjust(6)}"


def test_the_recording_holds_what_the_store_does_not() -> None:
    printed = [line for _, lines in SEMANTICS["pair_order"] for line in lines]
    # U+1F600 is two UTF-16 units and one character: 18 spaces follow it, where `ljust` gives 19.
    assert f"  \U0001f600{' ' * 18}      2" in printed
    # `a b,1` sorts before `a,5`, where the pair ("a", 5) sorts before ("a b", 1).
    assert [lines for pairs, lines in SEMANTICS["pair_order"] if pairs == [["a", 5], ["a b", 1]]] == [
        [f"  {'a b'.ljust(20)}      1", f"  {'a'.ljust(20)}      5"]
    ]


@pytest.mark.parametrize(
    ("ids", "ordered"), [case for case in SEMANTICS["distinct_sorted"] if len(case[1]) > 1], ids=str
)
def test_source_ids_are_merged_in_utf16_order(tmp_path: Path, ids: list[str], ordered: list[str]) -> None:
    key = ROW["sha256"]
    assert archive.upsert_row(ROW | {"source_ids": ids[:1]}, tmp_path) == {"added": True, "changed": True}
    assert archive.upsert_row(ROW | {"source_ids": ids}, tmp_path) == {"added": False, "changed": True}
    assert archive.read_manifest(tmp_path)[key]["source_ids"] == ordered
    assert archive.upsert_row(ROW | {"source_ids": ids}, tmp_path) == {"added": False, "changed": False}


def test_utf16_order_and_character_order_part_ways_in_the_recording() -> None:
    cases = [ordered for _, ordered in SEMANTICS["distinct_sorted"]]
    assert ["\U00010000", "\uffff"] in cases
    assert sorted(["\U00010000", "\uffff"]) == ["\uffff", "\U00010000"]


def test_a_url_starts_with_its_scheme_in_ascii_letters_only(tmp_path: Path) -> None:
    prefixed: dict[str, bool] = dict(SEMANTICS["http_prefix"])
    assert (prefixed["HtTp://a"], prefixed["http\u017f://a"], prefixed["HTTP\u017f://a"]) == (
        True,
        False,
        False,
    )
    # The work list takes a URL by the pattern, once trimmed, and then by the parser, which
    # reads no host out of a scheme and two slashes alone.
    texts = list(prefixed)
    lay_out(tmp_path, [], [source(at, url=text) for at, text in enumerate(texts)])
    items = cast("list[dict[str, str]]", worklist.read_work_list(tmp_path)["items"])
    assert {item["url"] for item in items} == {"https://a", "http://a", "HTTPS://a", "HtTp://a"}
    assert {text.strip() for text in texts if prefixed[text.strip()]} == {
        *(item["url"] for item in items),
        "http://",
        "https://",
    }
    # A first URL the pattern does not take is passed over for the second. Were U+017F an
    # `s`, the first would be taken and then refused by the parser, and the record left out.
    other = tmp_path / "other"
    lay_out(other, [], [source(1, artifact_url="http\u017f://a.org/x", url="https://b.org/x")])
    (item,) = cast("list[dict[str, str]]", worklist.read_work_list(other)["items"])
    assert (item["url_field"], item["host"]) == ("url", "b.org")


def test_a_candidate_takes_a_url_over_a_label_and_a_long_s_is_no_url() -> None:
    held = held_map()
    cc_by = held["cc by"]
    held |= {"http\u017f://example.org/by": cc_by | {"licence": "http\u017f://example.org/by"}}
    target: JsonObject = {"source_id": "s1", "recorded_licence": None, "recorded_tier": "t4-private"}
    seen: JsonObject = {
        "endpoint": "https://example.org/api",
        "field": "licence",
        "observed_at": "2026-11-17T17:14:06Z",
    }

    def built(later: str) -> object:
        observations = [
            seen | {"route": "github", "value": later},
            seen | {"route": "crossref", "value": "cc by"},
        ]
        candidate = licence.build_candidate(target, observations, held)
        assert candidate is not None
        return candidate["observed_licence"]

    # A URL from a later route is carried over a label from an earlier one.
    assert built("HTTPS://EXAMPLE.ORG/LICENCE") == "HTTPS://EXAMPLE.ORG/LICENCE"
    # JavaScript's pattern does not take U+017F for an `s`: this is a label, and the earlier route's stands.
    assert built("http\u017f://example.org/by") == "cc by"


def test_a_cache_named_by_no_text_holds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert SEMANTICS["empty_path"] == {"exists": False, "joined": "ab/key.gz"}
    key = sha256_hex(b"an object")
    (tmp_path / key[:2]).mkdir()
    (tmp_path / key[:2] / f"{key}.gz").write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARCHIVE_CACHE", "")
    # Node joins no text to a folder and a file as a path under the working directory, and
    # says no text names nothing that exists: a file can be asked for and none is listed.
    assert archive.object_path(key, Path("elsewhere")) == Path(key[:2]) / f"{key}.gz"
    assert archive.has_object(key, Path("elsewhere")) is False
    assert archive.cached_files(Path("elsewhere")) == []
    assert archive.cache_contents(Path("elsewhere")) == {"keys": [], "compressed_bytes": 0}
    monkeypatch.setenv("ARCHIVE_CACHE", ".")
    assert archive.cached_keys(Path("elsewhere")) == [key]


@pytest.mark.parametrize("given", [{}, {"id": 7}, {"id": None}, {"id": ["s1"]}], ids=str)
def test_a_source_record_whose_id_is_not_text_is_refused_by_file_and_line(
    tmp_path: Path, given: dict[str, Any]
) -> None:
    record = {"kind": "source", "attributes": {"url": "https://a.com/x", "licence": "MIT"}} | given
    lay_out(tmp_path, [("MIT", "permissive-code", "licence-text")], [source(1), record])
    refusal = r"data/entities/sources\.jsonl:2: a source record whose id is not text"
    for read in (
        worklist.read_work_list,
        worklist.summarise,
        licence.read_source_licences,
        licence.build_triage,
        licence.counts_report,
    ):
        with pytest.raises(StoreError, match=refusal):
            read(tmp_path)
    # A record of another kind is not a source record, whatever it lacks.
    lay_out_other = tmp_path / "other"
    lay_out(lay_out_other, [], [source(1), {"kind": "model"}])
    assert licence.read_source_licences(lay_out_other) == {"s1": None}
