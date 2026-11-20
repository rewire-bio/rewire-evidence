"""Each family of store file: its serialisation, its order and where its rows go."""

from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence.canonical import JsonObject, canonical_json, canonical_line, js_stringify, sha256_hex
from rewire_evidence.families import (
    CANDIDATES,
    ENTITIES,
    EVIDENCE,
    FAMILIES,
    FETCH_RECEIPTS,
    LICENCE_RECEIPTS,
    MANIFEST,
    MOVED,
    PROVENANCE,
    REGISTRY,
    UNSUPPORTED,
    append_rows,
    fetch_receipt_path,
    licence_receipt_path,
    manifest_shard_path,
    ordered,
    read_family,
    serialise_rows,
    write_family,
)
from rewire_evidence.jsonl import StoreError, read_rows, write_text
from tests.strategies import json_objects, record_ids

KEY_A = "a" * 64
KEY_B = "b" * 64
KEY_A2 = "ab" * 32


def layer(layer_key: str, object_key: str, path: str | None) -> JsonObject:
    # `metrics` is built out of key order on purpose: the registry is the family whose
    # nested object proves the writer sorts the top level and nothing below it.
    return {
        "layer_sha256": layer_key,
        "object_sha256": object_key,
        "container_path": path,
        "metrics": {"characters": 3, "non_space": 2, "digits": 1},
        "bytes": 3,
    }


def test_families_have_distinct_names_and_cover_the_store() -> None:
    names = [family.name for family in FAMILIES]
    assert len(set(names)) == len(names) == 10


def test_record_families_use_insertion_order_and_the_layout(tmp_path: Path) -> None:
    records: list[JsonObject] = [
        {"id": "model-b", "kind": "model", "z": 1, "a": {"y": 1, "b": 2}},
        {"id": "model-a", "kind": "model"},
        {"id": "result-1", "kind": "result"},
    ]
    assert serialise_rows(ENTITIES, records[:2]) == (
        '{"id":"model-a","kind":"model"}\n{"id":"model-b","kind":"model","z":1,"a":{"y":1,"b":2}}\n'
    )
    written = write_family(ENTITIES, records[:2], tmp_path) + write_family(EVIDENCE, records[2:], tmp_path)
    assert [file.relative_to(tmp_path).parts[1] for file in written] == ["entities", "evidence"]
    assert read_family(ENTITIES, tmp_path) == [records[1], records[0]]
    assert read_family(EVIDENCE, tmp_path) == [records[2]]
    assert ENTITIES.files(tmp_path) == [tmp_path / "data/entities/models.jsonl"]
    assert EVIDENCE.files(tmp_path) == written[1:]


def test_provenance_rows_go_to_the_shard_of_their_record(tmp_path: Path) -> None:
    rows: list[JsonObject] = [{"id": f"record-{n}", "sha256": "0" * 64, "changed_by": []} for n in range(40)]
    written = write_family(PROVENANCE, rows, tmp_path)
    assert len(written) > 1
    for file in written:
        for row in read_rows(file):
            assert sha256_hex(str(row["id"]))[0] == file.stem
    assert sorted(str(row["id"]) for row in read_family(PROVENANCE, tmp_path)) == sorted(
        str(row["id"]) for row in rows
    )


def test_the_manifest_is_sharded_by_its_key_and_sorted_by_it(tmp_path: Path) -> None:
    rows: list[JsonObject] = [
        {"sha256": KEY_B, "store": "r2", "bytes": 2},
        {"sha256": KEY_A2, "store": "r2", "bytes": 1},
        {"sha256": KEY_A, "store": "r2", "bytes": 0},
    ]
    written = write_family(MANIFEST, rows, tmp_path)
    assert written == [manifest_shard_path("b", tmp_path), manifest_shard_path("a", tmp_path)]
    assert manifest_shard_path("a", tmp_path).read_text("utf-8") == (
        f'{{"bytes":0,"sha256":"{KEY_A}","store":"r2"}}\n{{"bytes":1,"sha256":"{KEY_A2}","store":"r2"}}\n'
    )
    # Shards are read in shard order, and the temporary files of the write are gone.
    assert [row["sha256"] for row in read_family(MANIFEST, tmp_path)] == [KEY_A, KEY_A2, KEY_B]
    assert sorted(path.name for path in manifest_shard_path("a", tmp_path).parent.iterdir()) == [
        "a.jsonl",
        "b.jsonl",
    ]


@pytest.mark.parametrize("shard", ["", "g", "A", "10", "a\n"])
def test_a_manifest_shard_is_one_hex_digit(shard: str, tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="Not a manifest shard"):
        manifest_shard_path(shard, tmp_path)


def test_a_manifest_row_without_a_sha256_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="row 1: Not a sha256 key: abc"):
        write_family(MANIFEST, [{"sha256": "abc"}], tmp_path)
    with pytest.raises(StoreError, match="row 1 has no string 'sha256'"):
        write_family(MANIFEST, [{"key": KEY_A}], tmp_path)


def test_the_registry_sorts_the_top_level_only(tmp_path: Path) -> None:
    rows = [
        layer(KEY_B, KEY_A, None),
        layer(KEY_A, KEY_B, "inner/b.txt"),
        layer(KEY_A, KEY_B, None),
        layer(KEY_A, KEY_A, "z"),
        layer(KEY_A, KEY_B, "inner/a.txt"),
    ]
    write_family(REGISTRY, rows, tmp_path)
    file = tmp_path / "data/omics/layers/registry.jsonl"
    lines = file.read_text("utf-8").split("\n")[:-1]
    # Layer hash, then object hash, then path, where no path sorts as the empty string.
    assert [(json.loads(line)["object_sha256"], json.loads(line)["container_path"]) for line in lines] == [
        (KEY_A, "z"),
        (KEY_B, None),
        (KEY_B, "inner/a.txt"),
        (KEY_B, "inner/b.txt"),
        (KEY_A, None),
    ]
    assert '"metrics":{"characters":3,"non_space":2,"digits":1}' in lines[0]
    assert lines[0] == canonical_line(rows[3])
    # The mistake this family exists to catch: sorting at every depth writes other bytes.
    assert lines[0] != canonical_json(rows[3])
    assert read_family(REGISTRY, tmp_path) == ordered(REGISTRY, rows)


def test_unsupported_rows_sort_by_object_then_path_then_reason(tmp_path: Path) -> None:
    rows: list[JsonObject] = [
        {"object_sha256": KEY_B, "container_path": None, "reason": "binary, no normaliser"},
        {"object_sha256": KEY_A, "container_path": "x", "reason": "b"},
        {"object_sha256": KEY_A, "container_path": "x", "reason": "a"},
        {"object_sha256": KEY_A, "container_path": None, "reason": "z"},
    ]
    write_family(UNSUPPORTED, rows, tmp_path)
    assert read_family(UNSUPPORTED, tmp_path) == [rows[3], rows[2], rows[1], rows[0]]


def test_a_container_path_must_be_text_or_null() -> None:
    bad: JsonObject = {"object_sha256": KEY_A, "container_path": 3, "reason": "r"}
    with pytest.raises(StoreError, match="container_path that is neither a string nor null"):
        serialise_rows(UNSUPPORTED, [bad])


def test_licence_candidates_sort_by_source(tmp_path: Path) -> None:
    rows: list[JsonObject] = [{"source_id": "b", "family": "cc-by"}, {"source_id": "a", "family": "mit"}]
    assert write_family(CANDIDATES, rows, tmp_path) == [tmp_path / "data/archive/licence-candidates.jsonl"]
    assert CANDIDATES.files(tmp_path) == [tmp_path / "data/archive/licence-candidates.jsonl"]
    assert read_family(CANDIDATES, tmp_path) == [rows[1], rows[0]]


def test_fetch_receipts_are_appended_to_the_file_of_their_date(tmp_path: Path) -> None:
    first: JsonObject = {"source_id": "b", "fetched_at": "2026-11-17T23:59:59Z", "outcome": "stored"}
    second: JsonObject = {"source_id": "a", "fetched_at": "2026-11-18T00:00:01Z", "outcome": "failed"}
    third: JsonObject = {"source_id": "a", "fetched_at": "2026-11-17T10:00:00Z", "outcome": "stored"}
    assert append_rows(FETCH_RECEIPTS, [first, second], tmp_path) == [
        fetch_receipt_path("2026-11-17", tmp_path),
        fetch_receipt_path("2026-11-18", tmp_path),
    ]
    append_rows(FETCH_RECEIPTS, [third], tmp_path)
    # Appended in the order given, never sorted: the order of a receipts file is its history.
    assert fetch_receipt_path("2026-11-17", tmp_path).read_text("utf-8") == (
        '{"fetched_at":"2026-11-17T23:59:59Z","outcome":"stored","source_id":"b"}\n'
        '{"fetched_at":"2026-11-17T10:00:00Z","outcome":"stored","source_id":"a"}\n'
    )
    # Other files in the folder are not fetch receipts, including ones that nearly match.
    for name in (
        "2026-09-19-source-access.receipts.jsonl",
        "x2026-11-17-archive-fetch.receipts.jsonl",
        "notes.md",
    ):
        write_text(tmp_path / "data/omics/audits" / name, "not a receipt\n")
    write_text(tmp_path / "data/omics/audits/2026-11-17-archive-fetch.receipts.jsonl\n", "")
    assert read_family(FETCH_RECEIPTS, tmp_path) == [first, third, second]
    assert read_family(LICENCE_RECEIPTS, tmp_path) == []


def test_a_receipt_file_needs_a_date(tmp_path: Path) -> None:
    for bad in ("2026-11-7", "20261117", "2026-11-17T", "2026-11-17\n", ""):
        with pytest.raises(StoreError, match="Not a date"):
            fetch_receipt_path(bad, tmp_path)
    with pytest.raises(
        StoreError, match="row 1 has a fetched_at that does not start with a date: not-a-timestamp"
    ):
        append_rows(FETCH_RECEIPTS, [{"fetched_at": "not-a-timestamp"}], tmp_path)


def test_licence_receipts_are_appended_to_the_file_the_run_names(tmp_path: Path) -> None:
    rows: list[JsonObject] = [
        {"source_id": "b", "route": "crossref"},
        {"source_id": "a", "route": "europepmc"},
    ]
    file = licence_receipt_path("2026-11-17", tmp_path)
    assert file == tmp_path / "data/omics/audits/2026-11-17-licence-resolve.receipts.jsonl"
    assert append_rows(LICENCE_RECEIPTS, rows, tmp_path, file) == [file]
    assert append_rows(LICENCE_RECEIPTS, [], tmp_path, file) == []
    assert read_family(LICENCE_RECEIPTS, tmp_path) == rows
    assert read_family(FETCH_RECEIPTS, tmp_path) == []
    with pytest.raises(StoreError, match="licence_receipts does not place its own rows; name the file"):
        append_rows(LICENCE_RECEIPTS, rows, tmp_path)


def test_moved_attributes_are_appended_in_insertion_order(tmp_path: Path) -> None:
    rows: list[JsonObject] = [
        {"id": "b", "attributes": {"z": 1, "a": 2}, "moved_by": "review.md"},
        {"id": "a", "attributes": {}, "moved_by": "review.md"},
    ]
    assert MOVED.files(tmp_path) == []
    append_rows(MOVED, rows[:1], tmp_path)
    append_rows(MOVED, rows[1:], tmp_path)
    assert (tmp_path / "data/provenance/moved-attributes.jsonl").read_text("utf-8") == (
        '{"id":"b","attributes":{"z":1,"a":2},"moved_by":"review.md"}\n'
        '{"id":"a","attributes":{},"moved_by":"review.md"}\n'
    )
    assert read_family(MOVED, tmp_path) == rows


def test_an_append_only_family_cannot_be_rewritten_and_a_sorted_one_cannot_be_appended_to(
    tmp_path: Path,
) -> None:
    for family in (MOVED, FETCH_RECEIPTS, LICENCE_RECEIPTS):
        with pytest.raises(StoreError, match="is append-only; use append_rows"):
            write_family(family, [], tmp_path)
    for family in (ENTITIES, EVIDENCE, PROVENANCE, MANIFEST, CANDIDATES, REGISTRY, UNSUPPORTED):
        with pytest.raises(StoreError, match="is sorted; use write_family"):
            append_rows(family, [], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_empty_file_body_is_empty() -> None:
    for family in FAMILIES:
        assert serialise_rows(family, []) == ""


@given(st.lists(record_ids, unique=True, max_size=10), st.randoms(use_true_random=False))
def test_a_sorted_family_writes_the_same_file_whatever_order_it_is_given(
    ids: list[str], rng: random.Random
) -> None:
    rows: list[JsonObject] = [{"source_id": source_id, "family": "cc-by"} for source_id in ids]
    shuffled = list(rows)
    rng.shuffle(shuffled)
    assert serialise_rows(CANDIDATES, shuffled) == serialise_rows(CANDIDATES, rows)
    assert serialise_rows(MOVED, shuffled) == "".join(js_stringify(row) + "\n" for row in shuffled)


@given(
    st.lists(
        st.tuples(st.sampled_from("0123456789abcdef"), json_objects), max_size=8, unique_by=lambda row: row[0]
    )
)
def test_a_written_manifest_reads_back_and_writes_the_same_bytes(drawn: list[tuple[str, JsonObject]]) -> None:
    rows: list[JsonObject] = [{**extra, "sha256": digit * 64} for digit, extra in drawn]
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        files = write_family(MANIFEST, rows, root)
        before = {file: file.read_bytes() for file in files}
        held = read_family(MANIFEST, root)
        assert [row["sha256"] for row in held] == sorted(str(row["sha256"]) for row in rows)
        write_family(MANIFEST, held, root)
        assert {file: file.read_bytes() for file in files} == before


def files_under(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def test_a_uniquely_keyed_family_refuses_two_rows_with_one_key(tmp_path: Path) -> None:
    # A manifest shard with a repeated key fails the gate, so the writer must not make one.
    manifest: list[JsonObject] = [{"sha256": KEY_A, "n": 1}, {"sha256": KEY_B}, {"sha256": KEY_A, "n": 2}]
    with pytest.raises(StoreError, match=f"row 1 and row 3 share the key {KEY_A}; manifest holds one row"):
        write_family(MANIFEST, manifest, tmp_path)
    records: list[JsonObject] = [{"id": "a", "kind": "model"}, {"id": "a", "kind": "model", "name": "again"}]
    with pytest.raises(StoreError, match="row 1 and row 2 share the key a; entities holds one row"):
        write_family(ENTITIES, records, tmp_path)
    with pytest.raises(StoreError, match="row 1 and row 2 share the key a; provenance holds one row"):
        write_family(PROVENANCE, records, tmp_path)
    assert files_under(tmp_path) == []
    # The licence candidates are not uniquely keyed: two for one source keep the order given.
    twice: list[JsonObject] = [{"source_id": "s", "n": 2}, {"source_id": "s", "n": 1}]
    write_family(CANDIDATES, twice, tmp_path)
    assert read_family(CANDIDATES, tmp_path) == twice


def test_a_family_writer_checks_every_row_before_it_writes_any_file(tmp_path: Path) -> None:
    good: JsonObject = {"sha256": KEY_B, "store": "r2"}
    for bad, problem in (
        ({"sha256": "abc"}, "row 2: Not a sha256 key: abc"),
        ({"key": KEY_A}, "row 2 has no string 'sha256'"),
        ({"sha256": KEY_A, "bytes": float("inf")}, r"row 2 cannot be written: JSON\.stringify writes null"),
        ({"sha256": KEY_A, "bytes": {1, 2}}, "row 2 cannot be written: set has no JSON.stringify form"),
        (["x"], "row 2 is not a JSON object"),
        (None, "row 2 is not a JSON object"),
    ):
        rows = cast("list[JsonObject]", [good, bad])
        with pytest.raises(StoreError, match=problem):
            write_family(MANIFEST, rows, tmp_path)
    receipts = cast("list[JsonObject]", [{"fetched_at": "2026-11-17T10:00:00Z"}, {"fetched_at": "soon"}])
    with pytest.raises(StoreError, match="row 2 has a fetched_at that does not start with a date: soon"):
        append_rows(FETCH_RECEIPTS, receipts, tmp_path)
    for bad_row in (["x"], {"id": "a", "attributes": float("nan")}):
        moved = cast("list[JsonObject]", [{"id": "a"}, bad_row])
        with pytest.raises(StoreError, match=r"row 2 (is not a JSON object|cannot be written)"):
            append_rows(MOVED, moved, tmp_path)
    assert files_under(tmp_path) == []


def test_a_row_that_cannot_be_ordered_is_named_by_its_source() -> None:
    rows: list[JsonObject] = [{"id": "a"}, {"id": 7}]
    with pytest.raises(StoreError, match="row 2 has no string 'id'"):
        serialise_rows(PROVENANCE, rows)
    with pytest.raises(StoreError, match=r"records/0\.jsonl:2 has no string 'id'"):
        ordered(PROVENANCE, rows, "records/0.jsonl:")
    nested: JsonObject = {"id": "deep"}
    for _ in range(100_000):
        nested = {"id": "deep", "inner": nested}
    with pytest.raises(StoreError, match="row 1 cannot be written: it is nested too deeply"):
        serialise_rows(PROVENANCE, [nested])
