"""The store layout: where a record goes, how files are sorted, what the digests cover."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given

from rewire_evidence.canonical import JsonObject, js_stringify, record_sha256, sha256_hex, shard_of
from rewire_evidence.jsonl import StoreError, join_lines, write_text
from rewire_evidence.schema import KINDS
from rewire_evidence.store import (
    EVIDENCE_KINDS,
    PROVENANCE_FILE,
    FileDigest,
    checked_records,
    kind_files,
    load_store,
    logical_digest,
    provenance_file_for,
    provenance_files,
    read_kind,
    read_provenance,
    record_file,
    record_files,
    reshard_store,
    store_digests,
    write_kind,
    write_store,
)
from tests.strategies import stores


def provenance_of(records: list[JsonObject]) -> list[JsonObject]:
    return [
        {
            "id": record["id"],
            "sha256": record_sha256(record),
            "added_in": "a-batch",
            "added_by": "records:add",
        }
        for record in records
    ]


def build(root: Path, records: list[JsonObject]) -> None:
    write_store(records, provenance_of(records), root)


RECORDS: list[JsonObject] = [
    {"id": "model-b", "kind": "model", "name": "B"},
    {"id": "model-a", "kind": "model", "name": "A"},
    {"id": "subset-1", "kind": "dataset_subset", "name": "S"},
    {"id": "use-1", "kind": "use_case", "name": "U"},
    {"id": "eval-1", "kind": "evaluation", "name": "E1"},
    {"id": "eval-2", "kind": "evaluation", "name": "E2"},
    {"id": "result-1", "kind": "result", "name": "R1"},
    {"id": "result-2", "kind": "result", "name": "R2"},
    {"id": "result-3", "kind": "result", "name": "R3"},
    {"id": "claim-1", "kind": "claim", "name": "C"},
]


def test_a_record_file_is_named_by_kind_and_shard(tmp_path: Path) -> None:
    assert record_file("model", "x", tmp_path) == tmp_path / "data/entities/models.jsonl"
    assert record_file("dataset_subset", "x", tmp_path) == tmp_path / "data/entities/dataset-subsets.jsonl"
    assert record_file("use_case", "x", tmp_path) == tmp_path / "data/entities/use-cases.jsonl"
    shard = sha256_hex("result-1")[0]
    assert shard_of("result-1") == shard
    assert record_file("result", "result-1", tmp_path) == tmp_path / f"data/evidence/results/{shard}.jsonl"
    assert (
        record_file("evaluation", "result-1", tmp_path)
        == tmp_path / f"data/evidence/evaluations/{shard}.jsonl"
    )
    assert record_file("claim", "result-1", tmp_path) == tmp_path / f"data/evidence/claims/{shard}.jsonl"
    assert provenance_file_for("result-1", tmp_path) == tmp_path / f"data/provenance/records/{shard}.jsonl"


def test_an_empty_root_holds_no_files(tmp_path: Path) -> None:
    assert record_files(tmp_path) == []
    assert provenance_files(tmp_path) == []
    assert kind_files("model", tmp_path) == []
    assert kind_files("result", tmp_path) == []
    assert read_kind("model", tmp_path) == []
    assert read_provenance(tmp_path) == {}
    assert load_store(tmp_path) == []
    assert reshard_store(tmp_path) == 0


def test_files_are_listed_entities_first_then_each_evidence_kind(tmp_path: Path) -> None:
    build(tmp_path, RECORDS)
    # Files that are not line files are not store files, wherever they sit.
    write_text(tmp_path / "data/entities/README.md", "notes\n")
    write_text(tmp_path / "data/evidence/results/notes.txt", "notes\n")
    write_text(tmp_path / "data/evidence/notes.txt", "notes\n")
    names = [file.relative_to(tmp_path).as_posix() for file in record_files(tmp_path)]
    assert names[:3] == [
        "data/entities/dataset-subsets.jsonl",
        "data/entities/models.jsonl",
        "data/entities/use-cases.jsonl",
    ]
    folders = [name.split("/")[2] for name in names[3:]]
    assert folders == sorted(folders)
    assert set(folders) == {"claims", "evaluations", "results"}
    assert kind_files("model", tmp_path) == [tmp_path / "data/entities/models.jsonl"]
    assert kind_files("result", tmp_path) == sorted(kind_files("result", tmp_path))
    assert {file.parent.name for file in kind_files("result", tmp_path)} == {"results"}


def test_a_written_store_loads_sorted_by_id(tmp_path: Path) -> None:
    build(tmp_path, RECORDS)
    assert (tmp_path / "data/entities/models.jsonl").read_text("utf-8") == (
        '{"id":"model-a","kind":"model","name":"A"}\n{"id":"model-b","kind":"model","name":"B"}\n'
    )
    assert [record["id"] for record in load_store(tmp_path)] == sorted(
        str(record["id"]) for record in RECORDS
    )
    assert [record["id"] for record in read_kind("result", tmp_path)] == ["result-1", "result-2", "result-3"]
    assert set(read_provenance(tmp_path)) == {record["id"] for record in RECORDS}


def test_write_kind_refuses_a_record_of_another_kind(tmp_path: Path) -> None:
    write_kind("model", RECORDS[:2], tmp_path)
    assert [record["id"] for record in read_kind("model", tmp_path)] == ["model-a", "model-b"]
    with pytest.raises(StoreError, match=r"record 3 \(subset-1\) is a dataset_subset, not a model"):
        write_kind("model", RECORDS[:3], tmp_path)


def test_ids_sort_as_javascript_compares_them(tmp_path: Path) -> None:
    # Code point order puts the astral ID last. JavaScript, comparing UTF-16 units, puts it
    # before U+E000, and the store files are in JavaScript's order.
    astral, private = "\U0001f9ec", "\ue000"
    records: list[JsonObject] = [{"id": private, "kind": "model"}, {"id": astral, "kind": "model"}]
    build(tmp_path, records)
    assert [record["id"] for record in load_store(tmp_path)] == [astral, private]
    write_text(
        tmp_path / "data/entities/models.jsonl",
        join_lines(js_stringify(record) for record in records),
    )
    with pytest.raises(StoreError, match="is not sorted by unique ID"):
        load_store(tmp_path)


def test_a_record_in_the_wrong_file_is_refused(tmp_path: Path) -> None:
    build(tmp_path, RECORDS)
    right = record_file("result", "result-1", tmp_path)
    wrong = right.with_name(f"{'0' if right.stem != '0' else '1'}.jsonl")
    write_text(wrong, right.read_text("utf-8") + (wrong.read_text("utf-8") if wrong.exists() else ""))
    with pytest.raises(StoreError, match=r"result-1 \(result\) is in the wrong file"):
        load_store(tmp_path)


def test_a_duplicate_or_unsorted_id_is_refused(tmp_path: Path) -> None:
    build(tmp_path, RECORDS)
    file = tmp_path / "data/entities/models.jsonl"
    first = file.read_text("utf-8").split("\n")[0]
    write_text(file, join_lines([first, first]))
    with pytest.raises(StoreError, match="is not sorted by unique ID at model-a"):
        load_store(tmp_path)


def test_a_record_without_provenance_is_refused(tmp_path: Path) -> None:
    write_store(RECORDS, provenance_of(RECORDS[1:]), tmp_path)
    with pytest.raises(StoreError, match="model-b has no provenance entry in data/provenance/records"):
        load_store(tmp_path)


def test_a_record_changed_without_a_provenance_entry_is_refused(tmp_path: Path) -> None:
    build(tmp_path, RECORDS)
    file = tmp_path / "data/entities/use-cases.jsonl"
    write_text(file, file.read_text("utf-8").replace('"name":"U"', '"name":"V"'))
    with pytest.raises(StoreError, match="use-1 changed without a provenance entry"):
        load_store(tmp_path)


def test_reordering_a_records_keys_is_a_change(tmp_path: Path) -> None:
    build(tmp_path, RECORDS)
    file = tmp_path / "data/entities/use-cases.jsonl"
    write_text(file, '{"kind":"use_case","id":"use-1","name":"U"}\n')
    with pytest.raises(StoreError, match="use-1 changed without a provenance entry"):
        load_store(tmp_path)


def test_a_provenance_row_with_no_record_is_refused(tmp_path: Path) -> None:
    extra: JsonObject = {"id": "ghost", "kind": "model"}
    write_store(RECORDS, provenance_of([*RECORDS, extra]), tmp_path)
    with pytest.raises(StoreError, match="lists 11 records; the store holds 10"):
        load_store(tmp_path)
    # The check runs when the records run out, so a reader that stops early has not passed.
    assert next(checked_records(tmp_path))["id"] == "subset-1"


def test_a_provenance_row_in_the_wrong_shard_is_refused(tmp_path: Path) -> None:
    build(tmp_path, RECORDS)
    right = provenance_file_for("model-a", tmp_path)
    wrong = right.with_name(f"{'0' if right.stem != '0' else '1'}.jsonl")
    write_text(wrong, right.read_text("utf-8"))
    with pytest.raises(StoreError, match=r"Provenance for .* is in the wrong file"):
        read_provenance(tmp_path)


def test_a_row_without_an_id_or_kind_is_refused(tmp_path: Path) -> None:
    write_text(tmp_path / "data/entities/models.jsonl", '{"kind":"model"}\n')
    with pytest.raises(StoreError, match=r"models\.jsonl:1 has no string 'id'"):
        load_store(tmp_path)
    with pytest.raises(StoreError, match=r"models\.jsonl:1 has no string 'id'"):
        store_digests(tmp_path)
    write_text(tmp_path / "data/entities/models.jsonl", '{"id":"model-a"}\n')
    with pytest.raises(StoreError, match=r"models\.jsonl:1 has no string 'kind'"):
        load_store(tmp_path)
    with pytest.raises(StoreError, match=r"models\.jsonl:1 has no string 'kind'"):
        reshard_store(tmp_path)


def test_logical_digests_are_those_of_the_unsharded_files(tmp_path: Path) -> None:
    build(tmp_path, RECORDS)
    by_id = sorted(RECORDS, key=lambda record: str(record["id"]))

    def whole(kind: str) -> str:
        return sha256_hex(join_lines(js_stringify(record) for record in by_id if record["kind"] == kind))

    provenance = sorted(provenance_of(RECORDS), key=lambda row: str(row["id"]))
    assert store_digests(tmp_path) == [
        FileDigest("data/entities/dataset-subsets.jsonl", whole("dataset_subset")),
        FileDigest("data/entities/models.jsonl", whole("model")),
        FileDigest("data/entities/use-cases.jsonl", whole("use_case")),
        FileDigest("data/evidence/claims.jsonl", whole("claim")),
        FileDigest("data/evidence/evaluations.jsonl", whole("evaluation")),
        FileDigest("data/evidence/results.jsonl", whole("result")),
        FileDigest(PROVENANCE_FILE, sha256_hex(join_lines(js_stringify(row) for row in provenance))),
    ]
    assert logical_digest([]) == sha256_hex("")


def test_digests_hash_the_lines_as_they_are_and_not_as_they_would_be_written(tmp_path: Path) -> None:
    # A line with spaces in it is not what this library writes, and the digest still covers
    # the bytes on disk, as the TypeScript digest does.
    line = '{"id": "model-a", "kind": "model"}'
    write_text(tmp_path / "data/entities/models.jsonl", line + "\n")
    assert store_digests(tmp_path)[0] == FileDigest("data/entities/models.jsonl", sha256_hex(line + "\n"))


def test_reshard_splits_an_unsharded_store_and_keeps_every_line(tmp_path: Path) -> None:
    results = [record for record in RECORDS if record["kind"] == "result"]
    spaced = '{"id": "result-0", "kind": "result"}'
    # The store before it was sharded: one file per kind and one provenance file.
    write_text(
        tmp_path / "data/evidence/results.jsonl",
        join_lines([spaced, *(js_stringify(record) for record in results)]),
    )
    write_text(
        tmp_path / "data/entities/models.jsonl", join_lines(js_stringify(record) for record in RECORDS[:2])
    )
    write_text(tmp_path / "data/evidence/stale/0.jsonl", "")
    write_text(tmp_path / PROVENANCE_FILE, join_lines(js_stringify(row) for row in provenance_of(results)))
    before = {digest.file: digest.sha256 for digest in store_digests(tmp_path)}
    legacy = sha256_hex((tmp_path / PROVENANCE_FILE).read_text("utf-8"))

    assert reshard_store(tmp_path) == 6
    assert not (tmp_path / "data/evidence/results.jsonl").exists()
    assert not (tmp_path / PROVENANCE_FILE).exists()
    assert not (tmp_path / "data/evidence/stale").exists()
    assert (tmp_path / "data/entities/models.jsonl").exists()
    assert {file.parent.name for file in record_files(tmp_path)} == {"entities", "results"}
    # The line with spaces moved as it was: resharding never parses and writes again.
    assert spaced in record_file("result", "result-0", tmp_path).read_text("utf-8").split("\n")
    assert len(read_provenance(tmp_path)) == 3
    # The digests are those of the files before the split. The old provenance file was
    # outside the shard folder, so only now does the provenance digest cover its lines.
    assert store_digests(tmp_path) == [
        FileDigest("data/entities/models.jsonl", before["data/entities/models.jsonl"]),
        FileDigest("data/evidence/results.jsonl", before["data/evidence/results.jsonl"]),
        FileDigest(PROVENANCE_FILE, legacy),
    ]


def test_reshard_changes_nothing_in_a_store_already_in_its_layout(tmp_path: Path) -> None:
    build(tmp_path, RECORDS)
    before = {file: file.read_bytes() for file in [*record_files(tmp_path), *provenance_files(tmp_path)]}
    assert reshard_store(tmp_path) == len(RECORDS)
    assert {
        file: file.read_bytes() for file in [*record_files(tmp_path), *provenance_files(tmp_path)]
    } == before


@given(stores())
def test_any_written_store_loads_reshards_and_keeps_its_digests(records: list[JsonObject]) -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        build(root, records)
        loaded = load_store(root)
        assert [record["id"] for record in loaded] == sorted(str(record["id"]) for record in records)
        assert {js_stringify(record) for record in loaded} == {js_stringify(record) for record in records}

        digests = store_digests(root)
        files = {file: file.read_bytes() for file in [*record_files(root), *provenance_files(root)]}
        assert reshard_store(root) == len(records)
        assert {file: file.read_bytes() for file in [*record_files(root), *provenance_files(root)]} == files
        assert store_digests(root) == digests


@given(stores())
def test_the_digests_do_not_depend_on_the_shard_layout(records: list[JsonObject]) -> None:
    with tempfile.TemporaryDirectory() as sharded, tempfile.TemporaryDirectory() as whole:
        build(Path(sharded), records)
        # The same store as one file per kind and one provenance file, as it was before the
        # split, in any order: the digest is of the lines in ID order.
        by_kind: dict[str, list[str]] = {}
        for record in reversed(records):
            kind = str(record["kind"])
            folder = "evidence" if kind in {"evaluation", "result", "claim"} else "entities"
            name = f"data/{folder}/{kind.replace('_', '-')}s.jsonl"
            by_kind.setdefault(name, []).append(js_stringify(record))
        for name, lines in by_kind.items():
            write_text(Path(whole) / name, join_lines(lines))
        write_text(
            Path(whole) / "data/provenance/records/all.jsonl",
            join_lines(js_stringify(row) for row in provenance_of(records)),
        )
        assert store_digests(Path(whole)) == store_digests(Path(sharded))


def files_under(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_the_kinds_are_the_seventeen_the_typescript_declares() -> None:
    # entityKinds in shared/omics/entity-kinds.ts at the pinned commit, in its order.
    assert len(KINDS) == len(set(KINDS)) == 17
    assert set(EVIDENCE_KINDS) < set(KINDS)
    assert KINDS[0] == "model"
    assert KINDS[-1] == "use_case"


@pytest.mark.parametrize("kind", ["../../../escaped", "banana", "", "Model", "models", "use-case", "a/b"])
def test_a_kind_that_is_not_a_record_kind_names_no_file(kind: str, tmp_path: Path) -> None:
    # The kind becomes a file name. One the store does not have must never reach the disk:
    # "../../../escaped" would be written three folders above the store.
    root = tmp_path / "a" / "b" / "c" / "root"
    record: JsonObject = {"id": "x", "kind": kind}
    with pytest.raises(StoreError, match="is not a record kind"):
        record_file(kind, "x", root)
    with pytest.raises(StoreError, match="is not a record kind"):
        kind_files(kind, root)
    with pytest.raises(StoreError, match=r"record 1 \(x\) has the kind .*, which is not a record kind"):
        write_store([record], [], root)
    with pytest.raises(StoreError, match=r"record 1 \(x\) has the kind .*, which is not a record kind"):
        write_kind(kind, [record], root)
    assert files_under(tmp_path) == {}

    write_text(root / "data/entities/models.jsonl", join_lines([js_stringify(record)]))
    with pytest.raises(
        StoreError, match=r"models\.jsonl:1 \(x\) has the kind .*, which is not a record kind"
    ):
        load_store(root)
    with pytest.raises(
        StoreError, match=r"models\.jsonl:1 \(x\) has the kind .*, which is not a record kind"
    ):
        reshard_store(root)
    assert list(files_under(tmp_path)) == ["a/b/c/root/data/entities/models.jsonl"]


def test_the_writers_refuse_two_rows_with_one_id(tmp_path: Path) -> None:
    # A file with a repeated ID fails load_store, so a writer must not be able to make one.
    twice: list[JsonObject] = [
        {"id": "b", "kind": "model"},
        {"id": "a", "kind": "model", "name": "first"},
        {"id": "a", "kind": "model", "name": "second"},
    ]
    with pytest.raises(StoreError, match="record 2 and record 3 share the ID a"):
        write_kind("model", twice, tmp_path)
    with pytest.raises(StoreError, match="record 2 and record 3 share the ID a"):
        write_store(twice, [], tmp_path)
    with pytest.raises(StoreError, match="provenance row 1 and provenance row 2 share the ID a"):
        write_store([], provenance_of(twice[1:]), tmp_path)
    assert files_under(tmp_path) == {}


def test_write_store_checks_every_row_before_it_writes_any_file(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="provenance row 2 has no string 'id'"):
        write_store(RECORDS, [*provenance_of(RECORDS[:1]), {"sha256": "x"}], tmp_path)
    assert files_under(tmp_path) == {}
    unwritable: JsonObject = {"id": "model-z", "kind": "model", "value": float("inf")}
    with pytest.raises(
        StoreError, match=r"record 11 \(model-z\) cannot be written: JSON\.stringify writes null"
    ):
        write_store([*RECORDS, unwritable], provenance_of(RECORDS), tmp_path)
    with pytest.raises(
        StoreError, match=r"record 1 \(model-z\) cannot be written: JSON\.stringify writes null"
    ):
        write_kind("model", [unwritable], tmp_path)
    assert files_under(tmp_path) == {}


def test_a_row_that_is_not_an_object_is_refused_by_position(tmp_path: Path) -> None:
    # What a caller gets wrong when it hands over parsed JSON without looking at it.
    rows = cast("list[JsonObject]", [RECORDS[0], ["x"], None])
    with pytest.raises(StoreError, match="record 2 is not a JSON object"):
        write_store(rows, [], tmp_path)
    with pytest.raises(StoreError, match="provenance row 2 is not a JSON object"):
        write_store([], rows, tmp_path)
    with pytest.raises(StoreError, match="record 2 is not a JSON object"):
        write_kind("model", rows, tmp_path)
    with pytest.raises(StoreError, match="record 2 has no string 'kind'"):
        write_kind("model", [RECORDS[0], {"id": "x"}], tmp_path)
    assert files_under(tmp_path) == {}


def test_reshard_sorts_every_file_it_writes(tmp_path: Path) -> None:
    # Enough records that every shard holds several, handed over in descending ID order as
    # one file per kind and one provenance file. The result has to be the bytes write_store
    # writes, which the logical digests cannot show because they sort the lines themselves.
    records: list[JsonObject] = [
        {"id": f"{kind}-{number:03}", "kind": kind}
        for kind in ("model", "result", "claim")
        for number in range(80)
    ]
    laid_out, whole = tmp_path / "laid-out", tmp_path / "whole"
    build(laid_out, records)
    backwards = sorted(records, key=lambda record: str(record["id"]), reverse=True)
    for kind, name in (
        ("model", "entities/models"),
        ("result", "evidence/results"),
        ("claim", "evidence/claims"),
    ):
        write_text(
            whole / f"data/{name}.jsonl",
            join_lines(js_stringify(record) for record in backwards if record["kind"] == kind),
        )
    write_text(whole / PROVENANCE_FILE, join_lines(js_stringify(row) for row in provenance_of(backwards)))

    assert reshard_store(whole) == len(records)
    assert files_under(whole) == files_under(laid_out)
    assert max(len(file.read_bytes().split(b"\n")) for file in record_files(whole)) > 3
