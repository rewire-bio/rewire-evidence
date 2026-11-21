"""The gate itself: it passes a store written by the library and names what is wrong in one that is not."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from rewire_evidence.canonical import (
    JsonObject,
    canonical_json,
    js_stringify,
    record_sha256,
    sha256_hex,
    shard_of,
)
from rewire_evidence.families import (
    CANDIDATES,
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
    stray_files,
    write_family,
)
from rewire_evidence.jsonl import StoreError, join_lines, write_text
from rewire_evidence.parity import (
    Expectation,
    compare_content,
    compare_counts,
    first_difference,
    newest_release,
    read_expectation,
    release_inputs,
    summary,
    to_json,
    verify_digests,
    verify_family,
    verify_record_digests,
    verify_records,
    verify_store,
    verify_writers,
)
from rewire_evidence.store import record_file, store_digests, write_store

RELEASE = "2026-11-20-aaaaaaaaaaaa"
OLDER = "2026-11-20-ffffffffffff"
KEY_A = "a" * 64
KEY_B = "b" * 64

RECORDS: list[JsonObject] = [
    {"id": "model-a", "kind": "model", "attributes": {"z": 0.00001, "a": 1}},
    {"id": "model-b", "kind": "model"},
    {"id": "result-1", "kind": "result"},
    {"id": "result-2", "kind": "result"},
    {"id": "claim-1", "kind": "claim"},
]


def write_receipt(root: Path, release_id: str, released_at: str, inputs: list[JsonObject]) -> Path:
    path = root / "data/omics/releases" / f"{release_id}.json"
    receipt = {"release_id": release_id, "released_at": released_at, "coverage": {"source_inputs": inputs}}
    write_text(path, json.dumps(receipt, indent=2) + "\n")
    return path


def build(root: Path) -> None:
    """A store holding at least one file of every family, with two release receipts."""
    provenance: list[JsonObject] = [
        {"id": record["id"], "sha256": record_sha256(record), "added_in": "batch", "changed_by": []}
        for record in RECORDS
    ]
    write_store(RECORDS, provenance, root)
    append_rows(MOVED, [{"id": "model-a", "attributes": {"old": 1}, "moved_by": "review.md"}], root)
    write_family(MANIFEST, [{"sha256": KEY_B, "store": "r2"}, {"sha256": KEY_A, "store": "r2"}], root)
    append_rows(FETCH_RECEIPTS, [{"source_id": "s", "fetched_at": "2026-11-17T10:00:00Z"}], root)
    append_rows(LICENCE_RECEIPTS, [{"source_id": "s"}], root, licence_receipt_path("2026-11-17", root))
    write_family(CANDIDATES, [{"source_id": "s2"}, {"source_id": "s1"}], root)
    layers: list[JsonObject] = [
        {"layer_sha256": KEY_A, "object_sha256": KEY_A, "container_path": None, "metrics": {"b": 1, "a": 2}},
        {"layer_sha256": KEY_A, "object_sha256": KEY_B, "container_path": None, "metrics": {"b": 1, "a": 2}},
    ]
    write_family(REGISTRY, layers, root)
    write_family(UNSUPPORTED, [{"object_sha256": KEY_B, "container_path": None, "reason": "binary"}], root)
    inputs: list[JsonObject] = [
        {"file": digest.file, "sha256": digest.sha256} for digest in store_digests(root)
    ]
    inputs.append({"file": "data/omics/use-cases/sources/notes.md", "sha256": "0" * 64})
    # The older receipt has the later file name, as the real ones do.
    write_receipt(root, RELEASE, "2026-11-20T09:58:39.000Z", inputs)
    write_receipt(root, OLDER, "2026-11-20T09:36:18.000Z", [])


EXPECTED_COUNTS = {
    "entities.files": 1,
    "entities.lines": 2,
    "entities.placed": 2,
    "evidence.files": 3,
    "evidence.lines": 3,
    "evidence.placed": 3,
    "provenance.lines": 5,
    "provenance.placed": 5,
    "moved_attributes.files": 1,
    "moved_attributes.lines": 1,
    "moved_attributes.placed": 1,
    "manifest.files": 2,
    "manifest.lines": 2,
    "manifest.placed": 2,
    "fetch_receipts.files": 1,
    "fetch_receipts.lines": 1,
    "fetch_receipts.placed": 1,
    "licence_receipts.files": 1,
    "licence_receipts.lines": 1,
    "licence_receipts.placed": 0,
    "licence_candidates.files": 1,
    "licence_candidates.lines": 2,
    "licence_candidates.placed": 2,
    "layer_registry.files": 1,
    "layer_registry.lines": 2,
    "layer_registry.placed": 2,
    "layers_unsupported.files": 1,
    "layers_unsupported.lines": 1,
    "layers_unsupported.placed": 1,
    "sharded_lines_placed": 8,
    "records_matching_provenance": 5,
    "logical_digests": 4,
    "logical_digests_matched": 4,
    "written.family_lines": 20,
    "written.resharded_records": 5,
    "written.split_records": 5,
}


def test_a_store_written_by_the_library_passes_with_every_count(tmp_path: Path) -> None:
    build(tmp_path)
    report = verify_store(tmp_path, RELEASE, tmp_path / "scratch")
    assert report.problems() == []
    counts = report.counts()
    # Five provenance rows land in one to five shards, depending on the digests of the IDs.
    provenance_files = counts.pop("provenance.files")
    assert 1 <= provenance_files <= 5
    assert counts.pop("written.family_files") == 12 + provenance_files
    assert counts.pop("written.store_files") == 4 + provenance_files
    assert counts.pop("written.resharded_files") == 4 + provenance_files
    assert counts.pop("written.split_files") == 4 + provenance_files
    assert counts == EXPECTED_COUNTS
    assert compare_counts(counts, EXPECTED_COUNTS) == []

    text = summary(report)
    assert "entities                 1        2        2  byte for byte" in text
    assert "sharded lines in the shard their ID names: 8" in text
    assert "records matching their provenance digest:  5" in text
    assert f"logical digests equal to release {RELEASE}: 4 of 4" in text
    assert f"files the family writers reproduce on disk: {12 + provenance_files} (20 lines)" in text
    assert f"record and provenance files write_store reproduces: {4 + provenance_files}" in text
    assert f"reshard of that copy: 5 records placed, {4 + provenance_files} files unchanged" in text
    assert (
        "reshard of an unsharded copy, lines out of order: 5 records placed, "
        f"{4 + provenance_files} files as committed"
    ) in text
    content = report.content()
    assert list(content) == [family.name for family in FAMILIES]
    assert all(len(digest) == 64 for digest in content.values())
    expectation = Expectation("abc123", RELEASE, report.counts(), content)
    assert json.loads(to_json(report, expectation)) == {
        "store_ref": "abc123",
        "release_receipt": RELEASE,
        "counts": report.counts(),
        "content": content,
        "problems": [],
    }
    # The report file carries what the gate printed: a count or a digest that is off is in it.
    off = Expectation(
        "abc123", RELEASE, {**report.counts(), "manifest.lines": 3}, {**content, "manifest": "0"}
    )
    assert [problem.split(" ")[0] for problem in json.loads(to_json(report, off))["problems"]] == [
        "manifest.lines",
        "manifest",
    ]


def test_an_empty_checkout_reports_no_files_and_cannot_name_a_receipt(tmp_path: Path) -> None:
    for family in FAMILIES:
        report = verify_family(family, tmp_path)
        assert (report.files, report.lines, report.placed, report.problems) == (0, 0, 0, [])
    with pytest.raises(StoreError, match="does not exist"):
        verify_store(tmp_path, RELEASE, tmp_path / "scratch")
    written = verify_writers(tmp_path, tmp_path / "scratch")
    assert (written.family_files, written.store_files, written.resharded_records, written.problems) == (
        0,
        0,
        0,
        [],
    )
    with pytest.raises(StoreError, match="holds no release receipts"):
        newest_release(tmp_path)


def test_a_writer_that_sorts_at_every_depth_does_not_reproduce_the_registry(tmp_path: Path) -> None:
    # The one-line mistake: json.dumps(sort_keys=True), which is canonical_json, on a row
    # holding a nested object. The registry is the family that catches it, because its
    # `metrics` object is not in key order.
    build(tmp_path)
    assert verify_family(REGISTRY, tmp_path).problems == []
    report = verify_family(replace(REGISTRY, serialise=canonical_json), tmp_path)
    assert len(report.problems) == 1
    assert report.problems[0].startswith(
        "data/omics/layers/registry.jsonl does not re-emit byte for byte, line 1: committed"
    )
    assert '"metrics":{"b":1,"a":2}' in report.problems[0]
    assert '"metrics":{"a":2,"b":1}' in report.problems[0]
    assert (report.files, report.lines, report.placed) == (1, 2, 2)
    # The manifest holds no nested object, so it cannot tell the two serialisations apart.
    assert verify_family(replace(MANIFEST, serialise=canonical_json), tmp_path).problems == []


def test_a_family_with_a_problem_says_so_in_the_summary(tmp_path: Path) -> None:
    build(tmp_path)
    file = tmp_path / "data/omics/layers/registry.jsonl"
    write_text(file, file.read_text("utf-8").replace('":', '": '))
    report = verify_store(tmp_path, RELEASE, tmp_path / "scratch")
    assert "layer_registry           1        2        2  1 problems" in summary(report)
    assert len(report.problems()) == 2
    assert report.problems()[0].startswith(
        "layer_registry: data/omics/layers/registry.jsonl does not re-emit"
    )
    assert (
        report.problems()[1]
        == "writers: data/omics/layers/registry.jsonl is not what its family writer writes"
    )


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (
            '{"source_id":"s1"}\n{"source_id":"s2"}',
            "the re-emitted file has more lines than the committed one",
        ),
        ('{"source_id":"s1"}\n\n', "line 3: the committed file has more lines than the re-emitted one"),
        ('{"source_id":"s1"}\n\n{"source_id":"s2"}\n', "line 2: committed '', re-emitted"),
        ('{"source_id":"s2"}\n{"source_id":"s1"}\n', 'line 1: committed \'{"source_id":"s2"}\', re-emitted'),
        ('{"source_id": "s1"}\n{"source_id":"s2"}\n', 'line 1: committed \'{"source_id": "s1"}\''),
        ('{"source_id":"s1","n":1.0}\n', "line 1: committed"),
        ('{"source_id":"s1","n":1e-05}\n', "line 1: committed"),
        ('{"source_id":"s1"}\r\n', "line 1: committed"),
    ],
)
def test_any_byte_the_writer_would_not_write_is_named(tmp_path: Path, body: str, problem: str) -> None:
    write_text(tmp_path / "data/archive/licence-candidates.jsonl", body)
    report = verify_family(CANDIDATES, tmp_path)
    assert len(report.problems) == 1
    assert problem in report.problems[0]


def test_first_difference_names_the_line() -> None:
    assert first_difference("a\nb\n", "a\nc\n") == "line 2: committed 'b', re-emitted 'c'"
    assert first_difference("a\n", "a\nb\n") == "line 2: committed '', re-emitted 'b'"
    assert first_difference("a\nb\n", "a\n") == "line 2: committed 'b', re-emitted ''"
    assert first_difference("a", "a\n") == "the re-emitted file has more lines than the committed one"
    assert first_difference("a\n", "a") == "line 2: the committed file has more lines than the re-emitted one"
    long = first_difference("x" * 500 + "\n", "y\n")
    assert "x" * 160 + "..." in long
    assert "x" * 161 not in long


def test_rows_in_the_wrong_file_and_out_of_order_are_named(tmp_path: Path) -> None:
    build(tmp_path)
    # A manifest row in the wrong shard, a duplicate key and a row out of order.
    right = manifest_shard_path("a", tmp_path)
    row = right.read_text("utf-8")
    write_text(manifest_shard_path("0", tmp_path), row)
    write_text(right, row + row)
    report = verify_family(MANIFEST, tmp_path)
    assert report.problems == [
        "data/archive/objects/0.jsonl:1 is in the wrong file",
        "data/archive/objects/a.jsonl:2 is not sorted by unique key",
    ]
    assert (report.files, report.lines, report.placed) == (3, 4, 3)

    wrong_day = fetch_receipt_path("2026-11-18", tmp_path)
    write_text(wrong_day, fetch_receipt_path("2026-11-17", tmp_path).read_text("utf-8"))
    assert verify_family(FETCH_RECEIPTS, tmp_path).problems == [
        "data/omics/audits/2026-11-18-archive-fetch.receipts.jsonl:1 is in the wrong file"
    ]


def test_a_file_that_cannot_be_read_as_rows_is_a_problem_and_not_a_crash(tmp_path: Path) -> None:
    file = tmp_path / "data/archive/licence-candidates.jsonl"
    write_text(file, '{"source_id":\n')
    assert verify_family(CANDIDATES, tmp_path).problems == [
        "data/archive/licence-candidates.jsonl:1 is not JSON: Expecting value: line 1 column 14 (char 13)"
    ]
    file.write_bytes(b'{"source_id":"\xff"}\n')
    report = verify_family(CANDIDATES, tmp_path)
    assert report.problems[0].startswith("data/archive/licence-candidates.jsonl is not UTF-8")
    assert (report.files, report.lines) == (1, 0)
    write_text(file, '{"id":"no-source"}\n')
    assert verify_family(CANDIDATES, tmp_path).problems == [
        "data/archive/licence-candidates.jsonl:1 has no string 'source_id'"
    ]


def test_digests_are_held_against_the_named_receipt(tmp_path: Path) -> None:
    build(tmp_path)
    report = verify_digests(tmp_path, RELEASE)
    assert (report.digests, report.matched, report.problems) == (4, 4, [])
    assert newest_release(tmp_path) == RELEASE

    # A record changed with its provenance: the layout still holds and the release does not.
    changed: JsonObject = {"id": "model-b", "kind": "model", "name": "changed"}
    file = record_file("model", "model-b", tmp_path)
    write_text(file, join_lines([js_stringify(RECORDS[0]), js_stringify(changed)]))
    report = verify_digests(tmp_path, RELEASE)
    assert (report.digests, report.matched) == (4, 3)
    assert len(report.problems) == 1
    assert report.problems[0].startswith("data/entities/models.jsonl has digest ")
    assert report.problems[0].endswith(
        f"and {RELEASE} recorded {release_inputs(tmp_path, RELEASE)[file.relative_to(tmp_path).as_posix()]}"
    )


def test_an_older_receipt_is_refused_even_when_its_name_sorts_last(tmp_path: Path) -> None:
    build(tmp_path)
    report = verify_digests(tmp_path, OLDER)
    assert report.problems[0] == f"{OLDER} is not the newest release receipt; {RELEASE} is"
    assert report.matched == 0
    assert "data/entities/models.jsonl is not among the inputs of " + OLDER in report.problems
    assert len(report.problems) == 5
    full = verify_store(tmp_path, OLDER, tmp_path / "scratch")
    assert f"digests: {OLDER} is not the newest release receipt; {RELEASE} is" in full.problems()


@pytest.mark.parametrize(
    ("receipt", "problem"),
    [
        ("[]", "is not a JSON object"),
        ("{", "is not JSON"),
        ('{"coverage": 3}', "coverage is not a JSON object"),
        ('{"coverage": {}}', "coverage.source_inputs is not a list"),
        ('{"coverage": {"source_inputs": ["x"]}}', r"source_inputs\[0\] is not a JSON object"),
        ('{"coverage": {"source_inputs": [{"file": 1}]}}', r"source_inputs\[0\]\.file is not a string"),
        ('{"coverage": {"source_inputs": [{"file": "f"}]}}', r"source_inputs\[0\]\.sha256 is not a string"),
    ],
)
def test_a_malformed_receipt_is_refused_by_name(tmp_path: Path, receipt: str, problem: str) -> None:
    write_text(tmp_path / "data/omics/releases" / f"{RELEASE}.json", receipt)
    with pytest.raises(StoreError, match=problem):
        release_inputs(tmp_path, RELEASE)


@pytest.mark.parametrize(
    ("receipt", "problem"),
    [
        ('{"release_id": "r"}', "released_at is not a string"),
        ('{"release_id": "r", "released_at": "yesterday"}', "released_at is not a timestamp: yesterday"),
        ('{"release_id": "r", "released_at": "2026-11-20T09:58:39"}', "released_at has no time zone"),
        ('{"released_at": "2026-11-20T09:58:39Z"}', "release_id is not a string"),
    ],
)
def test_a_receipt_that_cannot_be_dated_is_refused(tmp_path: Path, receipt: str, problem: str) -> None:
    write_text(tmp_path / "data/omics/releases/r.json", receipt)
    with pytest.raises(StoreError, match=problem):
        newest_release(tmp_path)


def test_release_times_are_compared_as_times_and_not_as_text(tmp_path: Path) -> None:
    write_receipt(tmp_path, "early", "2026-11-20T10:00:00+02:00", [])
    write_receipt(tmp_path, "late", "2026-11-20T09:00:00Z", [])
    assert newest_release(tmp_path) == "late"


def test_the_expectation_is_read_and_checked(tmp_path: Path) -> None:
    path = tmp_path / "expected.json"
    path.write_text(
        '{"store_ref": "abc", "release_receipt": "r", "counts": {"a.lines": 3}, "content": {}}',
        encoding="utf-8",
    )
    assert read_expectation(path) == Expectation("abc", "r", {"a.lines": 3}, {})
    path.write_text(
        '{"store_ref": "abc", "release_receipt": "r", "counts": {}, "content": {"manifest": "00ff"}}',
        encoding="utf-8",
    )
    assert read_expectation(path) == Expectation("abc", "r", {}, {"manifest": "00ff"})
    for body, problem in [
        ('{"store_ref": "abc", "release_receipt": "r", "content": {}}', "counts is not a JSON object"),
        (
            '{"store_ref": "abc", "release_receipt": "r", "counts": {"a": "3"}, "content": {}}',
            "counts.a is not a whole number",
        ),
        (
            '{"store_ref": "abc", "release_receipt": "r", "counts": {"a": true}, "content": {}}',
            "counts.a is not a whole number",
        ),
        (
            '{"store_ref": "abc", "release_receipt": "r", "counts": {"a": 1.5}, "content": {}}',
            "counts.a is not a whole number",
        ),
        ('{"store_ref": "abc", "release_receipt": "r", "counts": {}}', "content is not a JSON object"),
        (
            '{"store_ref": "abc", "release_receipt": "r", "counts": {}, "content": {"manifest": 1}}',
            r"content\.manifest is not a string",
        ),
        ('{"release_receipt": "r", "counts": {}, "content": {}}', "store_ref is not a string"),
        ('{"store_ref": "abc", "counts": {}, "content": {}}', "release_receipt is not a string"),
    ]:
        path.write_text(body, encoding="utf-8")
        with pytest.raises(StoreError, match=problem):
            read_expectation(path)
    with pytest.raises(StoreError, match="does not exist"):
        read_expectation(tmp_path / "missing.json")


def test_the_committed_expectation_is_well_formed() -> None:
    expectation = read_expectation(Path(__file__).parent.parent / "scripts/expected/store-layout.json")
    assert len(expectation.store_ref) == 40
    assert expectation.counts["logical_digests"] == expectation.counts["logical_digests_matched"] == 18
    assert expectation.counts["sharded_lines_placed"] == (
        expectation.counts["evidence.lines"] + expectation.counts["provenance.lines"]
    )
    assert expectation.counts["records_matching_provenance"] == expectation.counts["provenance.lines"]
    assert expectation.counts["written.resharded_records"] == expectation.counts["provenance.lines"]
    assert expectation.counts["written.split_records"] == expectation.counts["provenance.lines"]
    assert expectation.counts["written.split_files"] == expectation.counts["written.store_files"]
    assert list(expectation.content) == [family.name for family in FAMILIES]
    assert all(re.fullmatch("[0-9a-f]{64}", digest) for digest in expectation.content.values())
    assert len(set(expectation.content.values())) == len(FAMILIES)
    assert expectation.counts["written.family_files"] == sum(
        count for name, count in expectation.counts.items() if name.endswith(".files")
    )


def test_a_count_that_differs_is_missing_or_is_not_pinned_fails() -> None:
    assert compare_counts({"a": 1, "b": 2}, {"a": 1, "b": 2}) == []
    assert compare_counts({"a": 1000, "b": 2, "c": 3}, {"a": 1001, "b": 2, "d": 4}) == [
        "a was measured as 1,000 against a pin of 1,001",
        "c was measured as 3 and is not pinned",
        "d is pinned at 4 and was not measured",
    ]


def test_the_writers_are_held_against_the_committed_files(tmp_path: Path) -> None:
    root = tmp_path / "store"
    build(root)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    report = verify_writers(root, tmp_path / "scratch")
    assert report.problems == []
    assert report.family_lines == 20
    assert report.resharded_records == 5
    assert report.family_files - report.store_files == 8
    assert report.resharded_files == report.store_files
    # models.jsonl holds two records, so the unsharded copy holds them in descending order
    # and only a reshard that sorts gives the committed file back.
    assert (report.split_records, report.split_files) == (5, report.store_files)
    # The checkout is read and never written to.
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_a_file_no_writer_would_write_is_named_by_the_writers(tmp_path: Path) -> None:
    root = tmp_path / "store"
    build(root)
    # A record in the wrong shard: the writers put it where it belongs, so the committed
    # file is neither written nor left by a reshard, and the file it belongs in appears.
    right = record_file("claim", "claim-1", root)
    wrong = right.with_name(f"{'0' if right.stem != '0' else '1'}.jsonl")
    right.rename(wrong)
    misplaced = wrong.relative_to(root).as_posix()
    report = verify_writers(root, tmp_path / "scratch")
    assert report.problems == [
        f"{misplaced} is not what its family writer writes",
        f"{misplaced} is not what write_store writes",
        *sorted(
            f"{name} is not the same file after a reshard"
            for name in (misplaced, right.relative_to(root).as_posix())
        ),
        *sorted(
            f"{name} is not the same file after a reshard of an unsharded copy"
            for name in (misplaced, right.relative_to(root).as_posix())
        ),
    ]
    assert (report.resharded_records, report.split_records) == (5, 5)


def test_a_file_whose_rows_cannot_be_placed_is_named_by_the_writers(tmp_path: Path) -> None:
    root = tmp_path / "store"
    build(root)
    write_text(root / "data/entities/models.jsonl", '{"kind":"model"}\n')
    report = verify_writers(root, tmp_path / "scratch")
    assert report.problems[0] == (
        "data/entities/models.jsonl could not be written again: row 1 has no string 'id'"
    )
    assert "data/entities/models.jsonl is not the same file after a reshard" in report.problems


def test_a_second_writer_that_drifts_from_the_first_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # write_store and the family writers are two implementations of one layout. If they
    # ever disagree, the files the gate reads still re-emit, and only this check fails.
    root = tmp_path / "store"
    build(root)

    def drifted(records: list[JsonObject], provenance: list[JsonObject], scratch: Path) -> None:
        write_store([{**record, "extra": 1} for record in records], provenance, scratch)

    monkeypatch.setattr("rewire_evidence.parity.write_store", drifted)
    report = verify_writers(root, tmp_path / "scratch")
    assert "data/entities/models.jsonl is not what write_store writes" in report.problems
    assert "data/entities/models.jsonl is not the same file after a reshard" in report.problems
    assert not any("family writer" in problem for problem in report.problems)


def test_an_edit_that_stays_canonical_passes_every_self_check_and_fails_the_pinned_bytes(
    tmp_path: Path,
) -> None:
    # Outside the records, nothing ties a row to a digest. Each of these edits leaves a file
    # that re-emits byte for byte, in order, with the same counts. Only the pinned digest of
    # the family's files can tell it is not the store the expectation was measured on.
    build(tmp_path)
    before = verify_store(tmp_path, RELEASE, tmp_path / "scratch-before")
    pinned = Expectation("abc123", RELEASE, before.counts(), before.content())
    assert compare_content(before.content(), pinned) == []

    for file, old, new in (
        (manifest_shard_path("a", tmp_path), '"store":"r2"', '"store":"r3"'),
        (fetch_receipt_path("2026-11-17", tmp_path), '"source_id":"s"', '"source_id":"t"'),
        (licence_receipt_path("2026-11-17", tmp_path), '"source_id":"s"', '"source_id":"t"'),
        (tmp_path / "data/archive/licence-candidates.jsonl", '"s1"', '"s0"'),
        (tmp_path / "data/omics/layers/registry.jsonl", '{"b":1,"a":2}', '{"a":2,"b":2}'),
        (tmp_path / "data/omics/layers/unsupported.jsonl", '"binary"', '"other"'),
        (tmp_path / "data/provenance/moved-attributes.jsonl", '"old":1', '"old":2'),
    ):
        text = file.read_text("utf-8")
        assert old in text
        write_text(file, text.replace(old, new))
    after = verify_store(tmp_path, RELEASE, tmp_path / "scratch-after")
    assert after.problems() == []
    assert compare_counts(after.counts(), pinned.counts) == []
    problems = compare_content(after.content(), pinned)
    assert [problem.split(" ")[0] for problem in problems] == [
        "fetch_receipts",
        "layer_registry",
        "layers_unsupported",
        "licence_candidates",
        "licence_receipts",
        "manifest",
        "moved_attributes",
    ]
    assert problems[0] == (
        f"fetch_receipts holds files with digest {after.content()['fetch_receipts']}, not the "
        f"{before.content()['fetch_receipts']} measured at abc123: "
        "the checkout is another commit, or was edited"
    )


def test_a_family_digest_covers_the_paths_as_well_as_the_bytes(tmp_path: Path) -> None:
    build(tmp_path)
    before = verify_family(MANIFEST, tmp_path).sha256
    assert verify_family(MANIFEST, tmp_path).sha256 == before
    # The same bytes under another name, and an empty file, are both a different family.
    empty = manifest_shard_path("0", tmp_path)
    write_text(empty, "")
    with_empty = verify_family(MANIFEST, tmp_path).sha256
    assert with_empty != before
    empty.rename(manifest_shard_path("1", tmp_path))
    assert verify_family(MANIFEST, tmp_path).sha256 not in {before, with_empty}
    assert verify_family(MANIFEST, tmp_path / "nowhere").sha256 == sha256_hex("")


def test_a_digest_that_is_missing_or_not_pinned_fails() -> None:
    pinned = Expectation("abc123", RELEASE, {}, {"a": "1", "b": "2"})
    assert compare_content({"a": "1", "b": "2"}, pinned) == []
    assert compare_content({"a": "1", "c": "3"}, pinned) == [
        "b has a pinned digest and was not measured",
        "c holds files with digest 3, and no digest is pinned for it",
    ]


def test_files_no_family_reads_are_reported(tmp_path: Path) -> None:
    build(tmp_path)
    assert stray_files(tmp_path) == []
    assert stray_files(tmp_path / "nowhere") == []
    strays = [
        "data/archive/objects/g.jsonl",
        "data/archive/objects/notes.md",
        "data/omics/audits/2026-1-01-archive-fetch.receipts.jsonl",
        "data/omics/audits/2026-11-17-licence-resolve.receipts.jsonl.bak",
        "data/omics/audits/x2026-11-17-archive-fetch.receipts.jsonl",
    ]
    for name in strays:
        write_text(tmp_path / name, "garbage\n")
    # Other audit files are not receipts of a family here and are nobody's stray.
    write_text(tmp_path / "data/omics/audits/2026-09-19-source-access.receipts.jsonl", "by hand\n")
    write_text(tmp_path / "data/omics/audits/audit-2026-09-19.run.json", "{}\n")
    assert [file.relative_to(tmp_path).as_posix() for file in stray_files(tmp_path)] == strays
    report = verify_store(tmp_path, RELEASE, tmp_path / "scratch")
    assert report.strays == strays
    assert report.problems() == [
        f"stray: {name} sits among a family's files and belongs to none" for name in strays
    ]


def test_every_record_that_fails_is_named_and_the_gate_still_reports(tmp_path: Path) -> None:
    build(tmp_path)
    assert verify_records(tmp_path).problems == []
    assert verify_records(tmp_path).matched == 5
    assert verify_records(tmp_path / "nowhere").problems == []

    models = tmp_path / "data/entities/models.jsonl"
    changed = '{"id":"model-a","kind":"model","attributes":{"z":0.00001,"a":2}}'
    unlisted = '{"id":"model-c","kind":"model"}'
    banana = '{"id":"model-d","kind":"banana"}'
    endless = '{"id":"model-e","kind":"model","n":1e400}'
    write_text(models, join_lines([changed, js_stringify(RECORDS[1]), js_stringify(RECORDS[1]), unlisted]))
    write_text(
        tmp_path / "data/entities/datasets.jsonl", join_lines([js_stringify(RECORDS[1]), banana, endless])
    )
    write_text(tmp_path / "data/entities/tasks.jsonl", "{\n")
    report = verify_records(tmp_path)
    assert report.matched == 4
    assert [problem.replace(str(tmp_path), "") for problem in report.problems] == [
        "model-b (model) is in the wrong file: /data/entities/datasets.jsonl",
        "/data/entities/datasets.jsonl:2 (model-d) has the kind 'banana', which is not a record kind",
        "model-e (model) is in the wrong file: /data/entities/datasets.jsonl",
        "model-a changed without a provenance entry. Record the change in data/provenance/records.",
        "/data/entities/models.jsonl is not sorted by unique ID at model-b",
        "model-c has no provenance entry in data/provenance/records",
        (
            "/data/entities/tasks.jsonl:1 is not JSON: Expecting property name enclosed in double quotes: "
            "line 1 column 2 (char 1)"
        ),
        "data/provenance/records lists 5 records; the store holds 10",
    ]

    # The whole gate finishes with every table filled in. One changed record is a
    # disagreement to report, not a run that could not be made.
    full = verify_store(tmp_path, RELEASE, tmp_path / "scratch")
    assert full.records.matched == 4
    assert "records: model-a changed without a provenance entry." in " ".join(full.problems())
    assert "records matching their provenance digest:  4" in summary(full)
    assert full.digests.digests == 0
    assert full.digests.problems[0].startswith("the store's digests could not be taken: ")
    assert full.digests.problems[0].endswith(
        "tasks.jsonl:1 is not JSON: " + report.problems[-2].split(": ", 1)[1]
    )
    assert len(full.families) == len(FAMILIES)


def test_a_record_that_cannot_be_hashed_is_named(tmp_path: Path) -> None:
    build(tmp_path)
    models = tmp_path / "data/entities/models.jsonl"
    write_text(models, models.read_text("utf-8").replace("0.00001", "1e400"))
    report = verify_records(tmp_path)
    assert report.matched == 4
    # The record is read as the TypeScript reads it: its digest is taken over the null that
    # JSON.stringify writes for the number, which is not the digest its provenance row holds.
    assert report.problems == [
        "model-a changed without a provenance entry. Record the change in data/provenance/records."
    ]
    family = verify_family(FAMILIES[0], tmp_path)
    assert family.problems == [
        (
            "data/entities/models.jsonl:1 cannot be written: JSON.stringify writes null for inf; "
            "it is not a number a record may hold"
        )
    ]
    written = verify_writers(tmp_path, tmp_path / "scratch")
    assert written.problems[0].startswith(
        "data/entities/models.jsonl could not be written again: row 1 cannot be written: JSON.stringify"
    )


def test_a_provenance_log_that_cannot_be_read_is_one_named_problem(tmp_path: Path) -> None:
    build(tmp_path)
    right = next(iter(sorted((tmp_path / "data/provenance/records").iterdir())))
    wrong = right.with_name(f"{'0' if right.stem != '0' else '1'}.jsonl")
    right.rename(wrong)
    report = verify_records(tmp_path)
    assert report.matched == 0
    assert len(report.problems) == 1
    assert report.problems[0].startswith("Provenance for ")
    assert report.problems[0].endswith(f"is in the wrong file: {wrong}")


def test_rows_no_writer_could_place_are_problems_and_not_crashes(tmp_path: Path) -> None:
    # A row that parses and cannot be placed, hashed or walked ends as a named problem.
    lone = "\ud800-lone"
    row: JsonObject = {"id": lone, "sha256": "0" * 64}
    line = js_stringify(row)
    assert "\\ud800" in line
    shard = tmp_path / "data/provenance/records" / f"{shard_of(lone)}.jsonl"
    write_text(shard, line + "\n")
    report = verify_family(PROVENANCE, tmp_path)
    assert (report.files, report.lines, report.placed, report.problems) == (1, 1, 1, [])

    write_text(
        tmp_path / "data/entities/models.jsonl", '{"id":"x","kind":"banana"}\n{"id":"y","kind":"model"}\n'
    )
    report = verify_family(FAMILIES[0], tmp_path)
    assert report.problems == ["data/entities/models.jsonl:1 (x): 'banana' is not a record kind"]
    assert (report.lines, report.placed) == (2, 1)

    write_text(tmp_path / "data/archive/licence-candidates.jsonl", "[" * 100_000 + "\n")
    assert verify_family(CANDIDATES, tmp_path).problems == [
        "data/archive/licence-candidates.jsonl:1 is nested too deeply to read"
    ]
    write_text(
        tmp_path / "data/archive/licence-candidates.jsonl", '{"source_id":"s","n":' + "9" * 5000 + "}\n"
    )
    assert verify_family(CANDIDATES, tmp_path).problems == [
        (
            "data/archive/licence-candidates.jsonl:1 cannot be written: JSON.stringify writes null for inf; "
            "it is not a number a record may hold"
        )
    ]


def test_every_record_digest_is_recomputed(tmp_path: Path) -> None:
    build(tmp_path)
    report = verify_record_digests(tmp_path)
    assert (report.provenance_rows, report.matched, report.mismatched, report.unlisted) == (5, 5, 0, 0)
    assert report.shown == []

    models = tmp_path / "data/entities/models.jsonl"
    rows = [
        '{"id":"model-a","kind":"model","attributes":{"a":1,"z":0.00001}}',
        '{"id":"model-b","kind":"model","n":1e400}',
        '{"id":"model-c","kind":"model"}',
        '{"kind":"model"}',
    ]
    write_text(models, join_lines(rows))
    report = verify_record_digests(tmp_path, shown=1)
    assert (report.matched, report.mismatched, report.unlisted) == (3, 2, 2)
    assert report.shown == [
        (
            "  models.jsonl model-a\n"
            f"    committed {record_sha256(RECORDS[0])}\n"
            f"    computed  {sha256_hex(rows[0])}"
        )
    ]
    assert "cannot be written" in verify_record_digests(tmp_path).shown[1]


def test_the_digest_gate_refuses_what_is_not_a_store(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match=r"is missing, so .* is not a benchmark-data checkout"):
        verify_record_digests(tmp_path)
    (tmp_path / "data/provenance/records").mkdir(parents=True)
    with pytest.raises(StoreError, match="holds no provenance rows"):
        verify_record_digests(tmp_path)
    write_text(tmp_path / "data/provenance/records/0.jsonl", '{"sha256":"x"}\n')
    with pytest.raises(StoreError, match=r"0\.jsonl:1 has no string 'id'"):
        verify_record_digests(tmp_path)
    write_text(tmp_path / "data/provenance/records/0.jsonl", '{"id":"a","sha256":"x"}\n')
    with pytest.raises(StoreError, match="holds no record files"):
        verify_record_digests(tmp_path)
