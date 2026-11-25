"""The write path: add and both forms of change, held to what the TypeScript recorded."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rewire_evidence import writes
from rewire_evidence.canonical import JsonObject, js_stringify, record_sha256
from rewire_evidence.cli import main
from rewire_evidence.jsonl import StoreError, read_rows
from rewire_evidence.schema import RecordError, SchemaError, parse_record
from rewire_evidence.store import (
    load_store,
    provenance_files,
    read_provenance,
    record_file,
    record_files,
    write_store,
)
from rewire_evidence.vocab import VocabularyError
from rewire_evidence.writes import (
    Change,
    Plan,
    RestoreError,
    ShapesError,
    diff,
    plan_add,
    plan_change,
    plan_changes_file,
    record_digest,
    shapes_check,
    today,
    write,
)
from tests.recorded import write_configuration
from tests.strategies import json_objects, record_ids
from tests.written import (
    BEYOND,
    CASES,
    DATE,
    WRITES,
    answer,
    before,
    files_under,
    label,
    lay_out,
    left,
    planned,
    recorded,
)

STORE = ("data/entities/", "data/evidence/", "data/provenance/")


def store_files(root: Path) -> dict[str, str]:
    """The store's own files, without the configuration a command needs beside them."""
    return {file: text for file, text in files_under(root).items() if file.startswith(STORE)}


def cases(command: str | None = None) -> list[Any]:
    return [pytest.param(case, id=label(case)) for case in CASES if command in {None, case["command"]}]


def case_named(name: str) -> dict[str, Any]:
    return next(case for case in CASES if label(case) == name)


@pytest.fixture(scope="module")
def configuration(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("configuration")
    write_configuration(root)
    return root


def test_the_cases_are_the_ones_recorded() -> None:
    assert WRITES["recorded_from"]["script"] == "scripts/fixtures/record_write_fixture.mjs"
    assert (WRITES["clock"], DATE) == ("2026-02-04T04:30:00.000Z", "2026-02-04")
    assert Counter(case["command"] for case in CASES) == {"add": 39, "change": 24, "changes": 54}
    assert Counter(case["command"] for case in CASES if case["error"] is None) == {
        "add": 13,
        "change": 14,
        "changes": 9,
    }
    # What the recorder was told to expect is what it found, where it was told anything.
    assert all(case["accepted"] in {None, case["error"] is None} for case in CASES)
    assert len(BEYOND) == 3
    assert all(case_named(name)["error"] is None for name in BEYOND)
    kinds = Counter(
        "zod" if "zod" in case["error"] else case["error"].get("code", case["error"]["name"])
        for case in CASES
        if case["error"] is not None
    )
    assert kinds == {"Error": 56, "zod": 13, "TypeError": 6, "SyntaxError": 4, "ENOENT": 2}


@pytest.mark.parametrize("case", cases())
def test_a_write_leaves_what_the_typescript_leaves_or_is_refused_as_it_refuses(
    case: dict[str, Any], tmp_path: Path, configuration: Path
) -> None:
    root, file = lay_out(case, tmp_path)
    made: list[Plan] = []
    got = answer(lambda: made.append(planned(case, root, file, configuration)), tmp_path)
    # Deciding a write writes nothing, whether it is refused or not.
    assert files_under(root) == before(case)
    if label(case) in BEYOND:
        # The TypeScript stores null for the number. This library takes the digest as it
        # does and then refuses to write the line.
        assert got is not None
        assert "cannot be written: JSON.stringify writes null for inf" in str(got["message"])
        assert "null" in "".join(text or "" for text in case["after"].values())
        return
    assert got == recorded(case)
    if got is not None:
        assert case["after"] == {}
        return
    plan = made[0]
    assert plan.root == root
    changes = write(plan)
    assert files_under(root) == left(case)
    assert {change.file.relative_to(root).as_posix() for change in changes} == set(case["after"])
    if case["command"] == "add":
        assert len(plan.ids) == case["returned"]
    elif case["command"] == "changes":
        assert list(plan.ids) == case["returned"]
    else:
        assert list(plan.ids) == case["ids"]
    # What was written is what was planned, so a second look finds nothing left to write.
    assert plan.changes() == []


def test_what_the_typescript_leaves_is_what_its_source_says() -> None:
    # A few of the recorded answers read against records.ts, so that a recorder that went
    # wrong would be noticed here and not only by a port that agrees with it.
    added = case_named("add: a result, into a shard file that is not there yet")
    assert added["returned"] == 1
    assert sorted(added["after"]) == ["data/evidence/results/0.jsonl", "data/provenance/records/0.jsonl"]
    row = read_text_rows(added["after"]["data/provenance/records/0.jsonl"])[-1]
    record = read_text_rows(added["after"]["data/evidence/results/0.jsonl"])[0]
    assert row == {
        "id": record["id"],
        "sha256": record_sha256(record),
        "added_in": "data/omics/a-new-batch",
        "added_by": "records:add",
        "changed_by": [],
    }
    assert list(row) == ["id", "sha256", "added_in", "added_by", "changed_by"]

    twice = case_named("change: one record named twice")
    (text,) = twice["after"].values()
    entry = {
        "step": "records:change",
        "inputs": "data/omics/a-new-batch",
        "date": DATE,
        "review": "docs/reviews/a-review.md",
    }
    changed = next(row for row in read_text_rows(text) if row["id"] == twice["ids"][0])
    assert changed["changed_by"] == [entry, entry]
    assert list(entry) == ["step", "inputs", "date", "review"]

    # recordChanges validates nothing: a record no rule accepts is recorded as changed.
    assert case_named("change: a record edited into one the store's rules refuse")["error"] is None
    assert case_named("changes: a record changed into one the store's rules refuse")["error"] is None
    # A record edited into another kind is written to that kind's file, and its old line stays.
    moved = case_named("change: a record edited into another kind")
    assert sorted(moved["after"]) == ["data/entities/pipelines.jsonl", "data/provenance/records/9.jsonl"]
    # The validators see the additions first: a duplicate is found before a vocabulary fault,
    # and a line that is not a record before a duplicate on an earlier line.
    assert (
        "Batch cannot replace"
        in case_named("add: a value outside its vocabulary, and after it a record the store holds")["error"][
            "message"
        ]
    )
    assert (
        "zod"
        in case_named("add: a record the store holds, and after it a line that is not a record")["error"]
    )


def read_text_rows(text: str) -> list[JsonObject]:
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "rows.jsonl"
        file.write_bytes(text.encode("utf-8"))
        return read_rows(file)


def test_a_refusal_of_a_line_says_which_line(tmp_path: Path, configuration: Path) -> None:
    case = case_named("add: a line that is not a record")
    root, file = lay_out(case, tmp_path)
    with pytest.raises(SchemaError) as refusal:
        plan_add(file, "a-batch", root, configuration)
    assert refusal.value.__notes__ == [f"{file}:1"]
    case = case_named("changes: a record that is not one")
    root, file = lay_out(case, tmp_path / "second")
    with pytest.raises(SchemaError) as refusal:
        plan_changes_file(file, "a-batch", "a-review", root)
    assert refusal.value.__notes__ == [f"{file} line 1"]
    case = case_named("change: a line that is not a record, in a file the change does not touch")
    root, file = lay_out(case, tmp_path / "third")
    with pytest.raises(SchemaError) as refusal:
        plan_change(case["ids"], "a-batch", "a-review", root)
    assert refusal.value.__notes__ == [f"{root / 'data/entities/datasets.jsonl'}:1"]


def test_a_change_is_dated_today_in_utc_unless_told(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = case_named("change: one record edited in place")
    root, _ = lay_out(case, tmp_path)
    monkeypatch.setattr(writes, "today", lambda: "2031-12-31")
    write(plan_change(case["ids"], "a-batch", "a-review", root))
    row = read_provenance(root)[case["ids"][0]]
    assert row["changed_by"] == [
        {"step": "records:change", "inputs": "a-batch", "date": "2031-12-31", "review": "a-review"}
    ]


def test_today_is_the_date_in_utc() -> None:
    new_york = dt.timezone(dt.timedelta(hours=-5))
    assert today(dt.datetime(2026, 2, 3, 23, 30, tzinfo=new_york)) == "2026-02-04"
    assert today(dt.datetime(2026, 2, 4, 4, 30, tzinfo=dt.UTC)) == "2026-02-04"
    assert today(dt.datetime(2026, 1, 1, 0, 30, tzinfo=dt.timezone(dt.timedelta(hours=14)))) == "2025-12-31"
    assert today() == dt.datetime.now(dt.UTC).date().isoformat()


# The bounds of the strategy are given without a zone, and it gives each moment one.
EARLIEST = dt.datetime(1971, 1, 1)  # noqa: DTZ001
LATEST = dt.datetime(9998, 1, 1)  # noqa: DTZ001


@given(st.datetimes(timezones=st.timezones(), min_value=EARLIEST, max_value=LATEST))
def test_today_does_not_depend_on_the_zone_the_moment_is_told_in(moment: dt.datetime) -> None:
    assert today(moment) == today(moment.astimezone(dt.UTC)) == moment.astimezone(dt.UTC).strftime("%Y-%m-%d")


# ---- properties ----------------------------------------------------------------------------


def a_record(record_id: str, kind: str, name: str, attributes: JsonObject) -> JsonObject:
    return {
        "id": record_id,
        "kind": kind,
        "name": name,
        "description": "",
        "status": "discovered",
        "facets": {},
        "source_ids": [],
        "links": [],
        "attributes": attributes,
    }


@st.composite
def schema_stores(draw: st.DrawFn) -> list[JsonObject]:
    """Records the schema accepts, of entity and evidence kinds, which no validator would."""
    ids = draw(st.lists(record_ids, unique=True, min_size=1, max_size=10))
    kinds = st.sampled_from(["model", "dataset_subset", "source", "evaluation", "result", "claim"])
    return [
        a_record(record_id, draw(kinds), draw(st.text(min_size=1, max_size=8)), draw(json_objects))
        for record_id in ids
    ]


def rows_for(records: Sequence[JsonObject]) -> list[JsonObject]:
    return [
        {
            "id": record["id"],
            "sha256": record_digest(parse_record(record)),
            "added_in": "a-batch",
            "added_by": "records:add",
            "changed_by": [],
        }
        for record in records
    ]


@given(schema_stores(), st.data())
@settings(max_examples=60)
def test_recording_a_change_makes_an_edited_store_consistent_and_touches_no_other_row(
    records: list[JsonObject], data: st.DataObject
) -> None:
    edited = data.draw(st.lists(st.sampled_from(records), unique_by=lambda record: str(record["id"])))
    ids = [str(record["id"]) for record in edited]
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        write_store(records, rows_for(records), root)
        untouched = files_under(root)
        # With nothing named, writing the store again is writing nothing.
        assert plan_change([], "inputs", "review", root, DATE).changes() == []
        for record in edited:
            file = record_file(str(record["kind"]), str(record["id"]), root)
            text = file.read_text("utf-8")
            file.write_bytes(
                text.replace(js_stringify(record), js_stringify({**record, "name": "edited"})).encode()
            )
        if ids:
            with pytest.raises(StoreError, match="changed without a provenance entry"):
                load_store(root)
        plan = plan_change(ids, "inputs", "review", root, DATE)
        changes = write(plan)
        loaded = load_store(root)
        assert sorted(str(record["id"]) for record in loaded) == sorted(
            str(record["id"]) for record in records
        )
        assert {str(record["id"]) for record in loaded if record["name"] == "edited"} >= set(ids)
        # Only provenance files are written, and only the rows of the records named change.
        assert all("data/provenance/records/" in change.file.as_posix() for change in changes)
        rows = read_provenance(root)
        entry = {"step": "records:change", "inputs": "inputs", "date": DATE, "review": "review"}
        for record in records:
            row = rows[str(record["id"])]
            assert row["changed_by"] == ([entry] if record["id"] in ids else [])
        before_lines = {line for text in untouched.values() for line in text.split("\n")}
        after_lines = {line for text in files_under(root).values() for line in text.split("\n")}
        assert len(after_lines - before_lines) == 2 * len(ids)


@given(st.data())
@settings(max_examples=40)
def test_a_batch_in_any_order_adds_the_same_files(data: st.DataObject) -> None:
    case = case_named("add: entities and evidence out of ID order, each with its top-level keys reversed")
    lines = [line for line in case["input"].split("\n") if line]
    shuffled = data.draw(st.permutations(lines))
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        root, file = lay_out(case, work)
        write_configuration(root)
        file.write_bytes("".join(f"{line}\n" for line in shuffled).encode("utf-8"))
        plan = plan_add(file, case["batch_dir"], root)
        assert len(plan.ids) == len(lines)
        write(plan)
        assert store_files(root) == left(case)


@st.composite
def plans(draw: st.DrawFn) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Files on disk and files a plan holds, which share some names and some bytes."""
    names = st.sampled_from(["a.jsonl", "b.jsonl", "x/c.jsonl", "x/y/d.jsonl", "z/e.jsonl"])
    disk = draw(st.dictionaries(names, st.binary(max_size=12), max_size=5))
    bodies = draw(st.dictionaries(names, st.binary(max_size=12), max_size=5))
    for name in draw(st.lists(st.sampled_from(sorted(disk)), unique=True)) if disk else []:
        bodies[name] = disk[name]
    return disk, bodies


def bytes_under(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@given(plans(), st.sampled_from([ShapesError, KeyboardInterrupt, OSError]))
def test_a_write_that_is_refused_leaves_every_file_as_it_was(
    held: tuple[dict[str, bytes], dict[str, bytes]], raised: type[BaseException]
) -> None:
    disk, bodies = held
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for name, data in disk.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
        plan = Plan(root, ("an-id",), {root / name: data for name, data in bodies.items()})
        seen: list[Sequence[str]] = []

        def refuse(ids: Sequence[str]) -> None:
            # The check runs with everything written.
            assert bytes_under(root) == {**disk, **bodies}
            seen.append(ids)
            raise raised("no")

        with pytest.raises(raised):
            write(plan, refuse)
        assert seen == [("an-id",)]
        assert bytes_under(root) == disk

        changes = write(plan, seen.append)
        assert bytes_under(root) == {**disk, **bodies}
        assert {change.file.relative_to(root).as_posix() for change in changes} == {
            name for name, data in bodies.items() if disk.get(name) != data
        }
        assert [change.file for change in changes] == sorted(change.file for change in changes)
        assert write(plan) == []


def test_a_write_that_fails_part_way_puts_back_what_it_had_written(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_bytes(b"old\n")
    (tmp_path / "blocked").write_bytes(b"a file where a folder is needed\n")
    plan = Plan(tmp_path, (), {tmp_path / "a.jsonl": b"new\n", tmp_path / "blocked" / "b.jsonl": b"new\n"})
    assert [change.before for change in plan.changes()] == [b"old\n", None]
    with pytest.raises(OSError, match="blocked") as failure:
        write(plan)
    assert not isinstance(failure.value, RestoreError)
    assert bytes_under(tmp_path) == {"a.jsonl": b"old\n", "blocked": b"a file where a folder is needed\n"}


def test_a_store_that_cannot_be_put_back_is_said_to_be_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    case = case_named("change: one record edited in place")
    root, _ = lay_out(case, tmp_path)
    plan = plan_change(case["ids"], "a-batch", "a-review", root, DATE)
    (changed,) = plan.changes()

    def refuse(_: Sequence[str]) -> None:
        # The disk stops taking writes once the store is written.
        monkeypatch.setattr(writes, "_put", unwritable)
        raise ShapesError("the shapes say no")

    def unwritable(file: Path, _: bytes) -> None:
        raise PermissionError(13, "Permission denied", str(file))

    with pytest.raises(RestoreError) as failure:
        write(plan, refuse)
    assert str(failure.value) == (
        "the shapes say no. The store was then not put back as it was: "
        f"{changed.file}: [Errno 13] Permission denied: '{changed.file}'"
    )
    assert isinstance(failure.value.__cause__, ShapesError)
    assert changed.file.read_bytes() == changed.after

    # The command says so, and does not say the store is unchanged.
    monkeypatch.undo()
    monkeypatch.setattr(writes, "write", lambda *_: (_ for _ in ()).throw(failure.value))
    monkeypatch.setattr("rewire_evidence.cli.write", lambda *_: (_ for _ in ()).throw(failure.value))
    assert main(["records", "change", "--root", str(root), "a-review", "a-batch", *case["ids"]]) == 1
    said = capsys.readouterr()
    assert said.err == f"{failure.value}\n"
    assert "unchanged" not in said.err


#: A carriage return and a line separator are part of a line, as they are to the store's readers.
LINE_ALPHABET = f"abc\r{chr(0x2028)}"


def body(lines: list[str]) -> bytes:
    return "".join(f"{line}\n" for line in lines).encode("utf-8")


@given(
    st.lists(st.text(alphabet=LINE_ALPHABET, min_size=1, max_size=3), max_size=8),
    st.lists(st.text(alphabet=LINE_ALPHABET, min_size=1, max_size=3), max_size=8),
)
def test_a_diff_takes_the_old_lines_to_the_new(old: list[str], new: list[str]) -> None:
    root = Path("/a/store")
    shown = diff([Change(root / "data/x.jsonl", body(old), body(new))], root)
    if old == new:
        assert shown == []
        return
    assert shown[:2] == ["--- a/data/x.jsonl", "+++ b/data/x.jsonl"]
    lines = [line for line in shown[2:] if not line.startswith("@@")]
    removed = Counter(line[1:] for line in lines if line.startswith("-"))
    added = Counter(line[1:] for line in lines if line.startswith("+"))
    assert len(lines) == sum(removed.values()) + sum(added.values())
    assert Counter(old) - removed + added == Counter(new)


def test_a_diff_shows_a_new_file_against_nothing() -> None:
    root = Path("/a/store")
    shown = diff([Change(root / "data/new.jsonl", None, b'{"id":"a"}\n{"id":"b"}\n')], root)
    assert shown == ["--- /dev/null", "+++ b/data/new.jsonl", "@@ -0,0 +1,2 @@", '+{"id":"a"}', '+{"id":"b"}']
    assert diff([], root) == []


def test_a_diff_shows_blank_lines_and_a_missing_final_newline_going() -> None:
    root = Path("/a/store")
    shown = diff([Change(root / "x.jsonl", b'\n{"id":"a"}\n\n{"id":"b"}', b'{"id":"a"}\n{"id":"b"}\n')], root)
    assert shown == [
        "--- a/x.jsonl",
        "+++ b/x.jsonl",
        "@@ -1 +0,0 @@",
        "-",
        "@@ -3 +1,0 @@",
        "-",
        "@@ -5 +2,0 @@",
        "-\\ No newline at end of file",
    ]
    assert diff([Change(root / "x.jsonl", b"", b"a\n")], root)[2:] == ["@@ -0,0 +1 @@", "+a"]


# ---- the shapes check ----------------------------------------------------------------------


def test_the_shapes_check_validates_in_process_without_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = case_named("add: a result, into a shard file that is not there yet")
    root, file = lay_out(case, tmp_path / "work")
    write_configuration(root)
    monkeypatch.setenv("PATH", "")
    plan = plan_add(file, case["batch_dir"], root)
    changes = write(plan, shapes_check(root))
    assert files_under(root)
    assert {change.file.relative_to(root).as_posix() for change in changes} == set(case["after"])

    # Missing ontology files fail with ShapesError and roll back the write cleanly.
    case2 = case_named("change: one record edited in place")
    root2, _ = lay_out(case2, tmp_path / "missing-ttl")
    write_configuration(root2)
    (root2 / "data/ontology/shapes.ttl").unlink()
    before_files = store_files(root2)
    plan2 = plan_change(case2["ids"], case2["batch_dir"], case2["review"], root2, DATE)
    with pytest.raises(ShapesError):
        write(plan2, shapes_check(root2))
    assert store_files(root2) == before_files


def test_the_shapes_check_refuses_shacl_violation_and_restores_the_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = case_named("change: one record edited in place")
    root, _ = lay_out(case, tmp_path / "shacl-bad")
    write_configuration(root)
    shapes = root / "data/ontology/shapes.ttl"
    shapes.write_text(
        shapes.read_text("utf-8") + "\nrbs:RequireTag a sh:NodeShape ; sh:targetClass rb:Method ; "
        "sh:property [ sh:path rb:requiredTestTag ; sh:minCount 1 ] .\n",
        encoding="utf-8",
    )
    before_files = store_files(root)
    monkeypatch.setenv("PATH", "")
    assert main(["records", "change", "--root", str(root), "a-review", "a-batch", *case["ids"]]) == 1
    said = capsys.readouterr()
    assert "SHACL validation failed" in said.err
    assert said.err.endswith("The changed records failed validation; the store is unchanged.\n")
    assert store_files(root) == before_files


# ---- the commands ---------------------------------------------------------------------------

#: Synthetic cases whose post-write store violates `load_records` because an un-named record
#: in the store was also edited or `recordChanges`/`applyChanges` wrote an invalid record or link.
SHAPES_UNABLE = frozenset(
    {
        "change: a record edited and not named",
        "change: a record edited into one the store's rules refuse",
        "change: a record edited into another kind",
        "changes: a record changed into one the store's rules refuse",
    }
)


@pytest.fixture
def node_free(monkeypatch: pytest.MonkeyPatch) -> list[Sequence[str]]:
    """Run CLI writes with `PATH=""` (proving no Node binary is called) and track `shapes_check`."""
    calls: list[Sequence[str]] = []
    real_shapes_check = writes.shapes_check

    def tracked(root: Path) -> Callable[[Sequence[str]], None]:
        inner = real_shapes_check(root)

        def check(ids: Sequence[str]) -> None:
            calls.append(list(ids))
            inner(ids)

        return check

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("rewire_evidence.cli.shapes_check", tracked)
    monkeypatch.setattr(writes, "today", lambda: DATE)
    return calls


def arguments(case: dict[str, Any], root: Path, file: Path) -> list[str]:
    if case["command"] == "add":
        return ["records", "add", "--root", str(root), str(file), case["batch_dir"]]
    start = ["records", "change", "--root", str(root), case["review"], case["batch_dir"]]
    return [*start, *case["ids"]] if case["command"] == "change" else [*start, "--changes", str(file)]


def printed(case: dict[str, Any], file: Path) -> str:
    if case["command"] == "add":
        return f"Added {case['returned']} records."
    if case["command"] == "change":
        return f"Recorded changes to {len(case['ids'])} records."
    return f"Recorded changes to {len(case['returned'])} records from {file}."


@pytest.mark.parametrize("case", cases())
def test_the_command_writes_what_the_functions_write_and_prints_what_the_typescript_prints(
    case: dict[str, Any],
    tmp_path: Path,
    node_free: list[Sequence[str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, file = lay_out(case, tmp_path / "work")
    write_configuration(root)
    given = arguments(case, root, file)
    if case["command"] == "change" and not case["ids"]:
        with pytest.raises(SystemExit) as left_with:
            main(given)
        assert left_with.value.code == 2
        assert "or --changes and a file" in capsys.readouterr().err
        return
    accepted = case["error"] is None and label(case) not in BEYOND
    what = "The batch" if case["command"] == "add" else "The changed records"

    status = main([*given[:2], "--dry-run", *given[2:]])
    said = capsys.readouterr()
    assert store_files(root) == before(case)
    assert node_free == []
    if accepted:
        assert (status, said.err) == (0, "")
        *shown, summary = said.out.rstrip("\n").split("\n")
        assert summary == (
            "Dry run, so nothing was written and the SHACL shapes were not checked. "
            f"Without --dry-run: {printed(case, file)[:-1]}, in {len(case['after'])} files."
        )
        assert [line[6:] for line in shown if line.startswith("+++ b/")] == sorted(case["after"])
        gained = [line[1:] for line in shown if line.startswith("+") and not line.startswith("+++ b/")]
        wanted = Counter(line for text in left(case).values() for line in text.split("\n") if line)
        wanted -= Counter(line for text in before(case).values() for line in text.split("\n") if line)
        assert not wanted - Counter(gained)
    else:
        assert (status, said.out) == (1, "")
        assert said.err.endswith(f"\n{what} failed validation; the store is unchanged.\n")

    if accepted and label(case) in SHAPES_UNABLE:
        # Real shapes_check rejects and rolls back the invalid post-write store cleanly.
        assert main(given) == 1
        said = capsys.readouterr()
        assert said.err.endswith(f"\n{what} failed validation; the store is unchanged.\n")
        assert store_files(root) == before(case)
        assert len(node_free) == 1
        # With shapes check skipped, the write itself reproduces what TypeScript recorded.
        monkeypatch.setattr("rewire_evidence.cli.shapes_check", lambda _: lambda _: None)
        assert main(given) == 0
        assert store_files(root) == left(case)
        return

    status = main(given)
    said = capsys.readouterr()
    if accepted:
        assert (status, said.out, said.err) == (0, f"{printed(case, file)}\n", "")
        assert store_files(root) == left(case)
        assert len(node_free) == 1
    else:
        assert (status, said.out) == (1, "")
        assert said.err.endswith(f"\n{what} failed validation; the store is unchanged.\n")
        assert store_files(root) == before(case)
        assert node_free == []


@pytest.mark.parametrize(
    "name",
    [
        "add: a result, into a shard file that is not there yet",
        "change: a line with its top-level keys reversed",
        "changes: an entity and a result, in another order than the store's",
    ],
)
def test_a_write_the_shapes_refuse_is_undone(
    name: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = case_named(name)
    root, file = lay_out(case, tmp_path / "work")
    write_configuration(root)
    everything = files_under(root)

    def fail_shapes(_: Path) -> Callable[[Sequence[str]], None]:
        def check(_: Sequence[str]) -> None:
            raise ShapesError("SHACL validation failed")

        return check

    monkeypatch.setattr("rewire_evidence.cli.shapes_check", fail_shapes)
    monkeypatch.setenv("PATH", "")
    assert main(arguments(case, root, file)) == 1
    said = capsys.readouterr()
    what = "The batch" if case["command"] == "add" else "The changed records"
    assert said.out == ""
    assert said.err == f"SHACL validation failed\n{what} failed validation; the store is unchanged.\n"
    assert files_under(root) == everything
    assert sorted(path.name for path in root.rglob("*") if path.is_dir() and not any(path.iterdir())) == (
        ["results"] if case["command"] == "add" else []
    )


def test_the_command_refuses_a_change_given_both_ids_and_a_file(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as left_with:
        main(["records", "change", "a-review", "a-batch", "an-id", "--changes", "changes.jsonl"])
    assert left_with.value.code == 2
    assert "not both" in capsys.readouterr().err


def test_the_command_does_not_judge_a_host_it_cannot(
    tmp_path: Path, node_free: list[Sequence[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    case = case_named("add: a result, into a shard file that is not there yet")
    root, file = lay_out(case, tmp_path / "work")
    write_configuration(root)
    sources = root / "data/entities/sources.jsonl"
    row = read_rows(sources)[0]
    attributes = row["attributes"]
    assert isinstance(attributes, dict)
    moved = {**row, "attributes": {**attributes, "url": "https://xn--bcher-kva.example/"}}
    sources.write_bytes(
        sources.read_bytes().replace(js_stringify(row).encode(), js_stringify(moved).encode())
    )
    for shard in (root / "data/provenance/records").iterdir():
        shard.write_bytes(
            shard.read_bytes().replace(record_sha256(row).encode(), record_sha256(moved).encode())
        )
    assert main(arguments(case, root, file)) == 2
    said = capsys.readouterr()
    assert said.err.startswith("Cannot judge the store: ")
    assert "failed validation" not in said.err
    assert node_free == []


def test_an_id_twice_in_the_store_is_one_record_for_a_change(tmp_path: Path) -> None:
    # The dict the records are read into is the TypeScript's Map: the later line is the
    # record, and it stands where the earlier one stood.
    case = case_named("change: one ID on two lines of a file")
    root, _ = lay_out(case, tmp_path)
    plan = plan_change(case["ids"], "a-batch", "a-review", root, DATE)
    (file,) = [path for path in plan.bodies if path.name == "sources.jsonl"]
    lines = plan.bodies[file].decode("utf-8").split("\n")[:-1]
    assert len(lines) == len(set(lines)) == 4
    assert sum(", renamed" in line for line in lines) == 1


def test_a_record_error_is_what_a_refusal_is(tmp_path: Path, configuration: Path) -> None:
    case = case_named("add: a record the store already holds")
    root, file = lay_out(case, tmp_path)
    with pytest.raises(RecordError, match="Batch cannot replace or duplicate record: "):
        plan_add(file, "a-batch", root, configuration)


# ---- what a failed write leaves, and what the command says of it ----------------------------


def edited_store(tmp_path: Path) -> tuple[dict[str, Any], Path, Plan]:
    """A small store with one record edited, and the plan that records the change."""
    case = case_named("change: one record edited in place")
    root, _ = lay_out(case, tmp_path / "work")
    return case, root, plan_change(case["ids"], "a-batch", "a-review", root, DATE)


def test_a_refused_write_puts_back_every_store_file_and_not_only_the_ones_it_wrote(tmp_path: Path) -> None:
    # writeCheckedAgainstShapes keeps every store file and puts each back, and removes a store
    # file that was not there. The check runs other programs in the store.
    _, root, plan = edited_store(tmp_path)
    everything = bytes_under(root)
    written = {change.file for change in plan.changes()}
    other, gone = [file for file in record_files(root) if file not in written][:2]
    shard = next(file for file in provenance_files(root) if file not in written)
    made = [root / "data/entities/made-by-the-check.jsonl", root / "data/provenance/records/z.jsonl"]

    def meddle(_: Sequence[str]) -> None:
        other.write_bytes(other.read_bytes() + b"{}\n")
        shard.write_bytes(b"")
        gone.unlink()
        for file in made:
            file.write_bytes(b"{}\n")
        raise ShapesError("the shapes say no")

    with pytest.raises(ShapesError, match="the shapes say no") as failure:
        write(plan, meddle)
    assert not isinstance(failure.value, RestoreError)
    assert bytes_under(root) == everything


def test_a_write_with_no_check_reads_and_puts_back_only_its_own_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing but the write itself runs, so the rest of the store is not read. A replay of a
    # whole store depends on that for the memory it holds.
    _, root, plan = edited_store(tmp_path)
    everything = bytes_under(root)
    (first, *_rest) = plan.changes()
    monkeypatch.setattr(writes, "_store_files", lambda _: pytest.fail("the store was listed"))
    monkeypatch.setattr(writes, "_put", torn_once([]))
    with pytest.raises(OSError, match="No space left on device"):
        write(plan)
    assert bytes_under(root) == everything
    assert first.file.read_bytes() == first.before


def torn_once(calls: list[Path]) -> Callable[[Path, bytes], None]:
    """A writer whose first write leaves part of a file and fails, and which works after that."""

    def put(file: Path, data: bytes) -> None:
        calls.append(file)
        if len(calls) == 1:
            file.write_bytes(data[:3])
            raise OSError(28, "No space left on device", str(file))
        file.write_bytes(data)

    return put


def test_a_file_half_written_is_put_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, root, plan = edited_store(tmp_path)
    everything = bytes_under(root)
    first = plan.changes()[0].file
    calls: list[Path] = []
    monkeypatch.setattr(writes, "_put", torn_once(calls))
    with pytest.raises(OSError, match="No space left on device") as failure:
        write(plan, lambda _: pytest.fail("the check ran on a store that was not written"))
    assert not isinstance(failure.value, RestoreError)
    # The one file the write tore was written again, and no other.
    assert calls == [first, first]
    assert bytes_under(root) == everything


def test_a_write_that_fails_before_it_changes_anything_leaves_a_store_that_is_unchanged(
    tmp_path: Path,
    node_free: list[Sequence[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case, root, plan = edited_store(tmp_path)
    write_configuration(root)
    everything = bytes_under(root)

    def unwritable(file: Path, _: bytes) -> None:
        raise PermissionError(13, "Permission denied", str(file))

    # Nothing was written, so there is nothing to put back, and a disk that takes no writes
    # is not asked to take one. The error is the write's own.
    monkeypatch.setattr(writes, "_put", unwritable)
    with pytest.raises(PermissionError) as failure:
        write(plan, shapes_check(root))
    assert not isinstance(failure.value, RestoreError)
    assert bytes_under(root) == everything

    assert main(["records", "change", "--root", str(root), "a-review", "a-batch", *case["ids"]]) == 1
    said = capsys.readouterr()
    first = plan.changes()[0].file
    assert said.err == (
        f"[Errno 13] Permission denied: '{first}'\n"
        "The changed records failed validation; the store is unchanged.\n"
    )
    assert bytes_under(root) == everything
    assert node_free == []


def test_a_store_that_cannot_be_listed_again_is_said_to_be_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, root, plan = edited_store(tmp_path)
    everything = bytes_under(root)

    def refuse(_: Sequence[str]) -> None:
        monkeypatch.setattr(writes, "_store_files", unlistable)
        raise ShapesError("the shapes say no")

    def unlistable(folder: Path) -> list[Path]:
        raise PermissionError(13, "Permission denied", str(folder))

    with pytest.raises(RestoreError) as failure:
        write(plan, refuse)
    # The files that were there are put back all the same. What cannot be known is whether
    # the check left a new one.
    assert str(failure.value) == (
        "the shapes say no. The store was then not put back as it was: "
        f"{root}: [Errno 13] Permission denied: '{root}'"
    )
    assert bytes_under(root) == everything


def test_a_folder_that_holds_no_store_is_refused_by_name(
    tmp_path: Path, node_free: list[Sequence[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    case = case_named("add: a result, into a shard file that is not there yet")
    _, file = lay_out(case, tmp_path / "work")
    nowhere = tmp_path / "nowhere"
    for command, what in [
        (["change", "--root", str(nowhere), "a-review", "a-batch", "an-id"], "The changed records"),
        (
            ["change", "--root", str(nowhere), "a-review", "a-batch", "--changes", str(file)],
            "The changed records",
        ),
        (["add", "--root", str(nowhere), str(file), "a-batch"], "The batch"),
    ]:
        for more in ([], ["--dry-run"]):
            assert main(["records", *command, *more]) == 1
            said = capsys.readouterr()
            assert said.out == ""
            assert said.err == (
                f"{nowhere} holds no store: it has no folder named data\n"
                f"{what} failed validation; the store is unchanged.\n"
            )
    assert not nowhere.exists()
    assert node_free == []


class Refusing:
    """A stream that takes nothing, as a pipe does once nobody reads it."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.buffer = self

    def flush(self) -> None:
        pass

    def write(self, _: bytes) -> int:
        raise self.error


@pytest.mark.parametrize(
    "error", [BrokenPipeError(32, "Broken pipe"), ValueError("I/O operation on closed file")]
)
def test_a_result_that_cannot_be_printed_does_not_end_in_a_traceback_or_undo_a_write(
    error: Exception,
    tmp_path: Path,
    node_free: list[Sequence[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case, root, _ = edited_store(tmp_path)
    write_configuration(root)
    given = ["records", "change", "--root", str(root), case["review"], case["batch_dir"], *case["ids"]]
    everything = store_files(root)

    # A dry run's result is what it prints, so one that cannot print has failed.
    monkeypatch.setattr(sys, "stdout", Refusing(error))
    assert main([*given, "--dry-run"]) == 1
    assert capsys.readouterr().err == "What the command did could not be printed.\n"
    assert store_files(root) == everything

    # A write is made and checked before anything is printed. It stays, and is not a refusal.
    assert main(given) == 0
    assert capsys.readouterr().err == "What the command did could not be printed. The store was written.\n"
    assert store_files(root) == left(case)
    assert len(node_free) == 1
    # The store now checks, and a check that cannot say so has failed too.
    assert main(["records", "check", str(root)]) == 1
    assert capsys.readouterr().err == "What the command did could not be printed.\n"

    # With standard error gone too there is nothing to say and nothing more to do.
    monkeypatch.setattr(sys, "stderr", Refusing(error))
    assert main([*given, "--dry-run"]) == 1


def test_a_command_started_with_its_output_closed_prints_nothing_and_is_otherwise_the_same(
    tmp_path: Path, node_free: list[Sequence[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Python has no stream at all then. Node opens the null device in its place.
    case, root, _ = edited_store(tmp_path)
    write_configuration(root)
    given = ["records", "change", "--root", str(root), case["review"], case["batch_dir"]]
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert main([*given, "--dry-run", *case["ids"]]) == 0
    assert main([*given, "no-such-record"]) == 1
    assert store_files(root) == before(case)
    assert main([*given, *case["ids"]]) == 0
    assert store_files(root) == left(case)
    assert len(node_free) == 1

    # The real thing: a process whose standard output is closed before it starts.
    done = subprocess.run(  # noqa: S603 - this interpreter, running this library
        [
            *COMMAND,
            "records",
            "change",
            "--dry-run",
            "--root",
            str(root),
            "a-review",
            "a-batch",
            *case["ids"],
        ],
        check=False,
        capture_output=True,
        close_fds=True,
        preexec_fn=lambda: os.close(1),
    )
    assert (done.returncode, done.stderr) == (0, b"")


#: The command as a process: this interpreter, running this library's entry point.
COMMAND = [sys.executable, "-c", "import sys; from rewire_evidence.cli import main; sys.exit(main())"]

ARGV: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures" / "argv.json").read_text("utf-8"))


def test_the_arguments_are_the_ones_recorded() -> None:
    assert ARGV["source"] == "node scripts/fixtures/record_argv_fixture.mjs"
    assert len(ARGV["cases"]) == 19
    assert sum("�" in case["read"] for case in ARGV["cases"]) == 16


@pytest.mark.parametrize("case", [pytest.param(case, id=case["name"]) for case in ARGV["cases"]])
def test_an_argument_that_is_not_utf8_is_written_as_node_reads_it(
    case: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held, root, _ = edited_store(tmp_path)
    # What Python makes of the bytes of an argument: a lone surrogate for each it cannot decode.
    given = os.fsdecode(bytes.fromhex(case["bytes"]))
    assert main(["records", "change", "--dry-run", "--root", str(root), given, given, *held["ids"]]) == 0
    (gained,) = [line[1:] for line in capsys.readouterr().out.split("\n") if line.startswith("+{")]
    (entry,) = json.loads(gained)["changed_by"]
    assert (entry["review"], entry["inputs"]) == (case["read"], case["read"])
    assert "\\ud" not in gained

    # An ID is read the same way, so one that is not UTF-8 names no record, as it names none to Node.
    assert main(["records", "change", "--dry-run", "--root", str(root), "a-review", "a-batch", given]) == 1
    assert capsys.readouterr().err.startswith(f"Unknown record: {case['read']}\n")


def test_a_batch_folder_that_is_not_utf8_is_written_as_node_reads_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = case_named("add: a result, into a shard file that is not there yet")
    root, file = lay_out(case, tmp_path / "work")
    write_configuration(root)
    given = ["records", "add", "--dry-run", "--root", str(root), str(file), os.fsdecode(b"a-batch\xff")]
    assert main(given) == 0
    rows = [json.loads(line[1:]) for line in capsys.readouterr().out.split("\n") if line.startswith("+{")]
    assert [row["added_in"] for row in rows if "added_in" in row] == ["a-batch�"]


def test_text_that_no_bytes_stand_for_is_left_as_it_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A caller in Python can hand over any text. A surrogate that is not a byte kept by the
    # decoder is written as JSON.stringify writes one: as an escape.
    held, root, _ = edited_store(tmp_path)
    given = ["records", "change", "--dry-run", "--root", str(root), "half \ud83d", "a-batch", *held["ids"]]
    assert main(given) == 0
    (gained,) = [line[1:] for line in capsys.readouterr().out.split("\n") if line.startswith("+{")]
    assert '"review":"half \\ud83d"' in gained


def test_the_bytes_of_an_argument_reach_the_provenance_row_as_node_reads_them(tmp_path: Path) -> None:
    # The command itself, started with an argument that is not UTF-8.
    held, root, _ = edited_store(tmp_path)
    done = subprocess.run(  # noqa: S603 - this interpreter, running this library
        [
            *COMMAND,
            "records",
            "change",
            "--dry-run",
            "--root",
            str(root),
            b"rev\xff",
            "a-batch",
            *held["ids"],
        ],
        check=True,
        capture_output=True,
    )
    assert b'"review":"rev\xef\xbf\xbd"' in done.stdout


# ---- nesting too deep to walk ---------------------------------------------------------------


@pytest.mark.parametrize("depth", [150, 400, 900, 990, 1000, 1100, 2000, 5000, 20000, 200000])
@pytest.mark.parametrize("command", ["add", "change", "changes"])
def test_a_line_nested_to_any_depth_is_accepted_or_refused_and_never_a_traceback(
    command: str, depth: int, tmp_path: Path, configuration: Path
) -> None:
    case = case_named(
        {
            "add": "add: a result, into a shard file that is not there yet",
            "change": "change: one record edited in place",
            "changes": "changes: one changed record",
        }[command]
    )
    root, file = lay_out(case, tmp_path)
    deep = f"{'[' * depth}{']' * depth}"
    if command == "change":
        # The record itself is nested: the line of it in the store.
        (target,) = [
            path for path in record_files(root) if f'"id":"{case["ids"][0]}"' in path.read_text("utf-8")
        ]
        target.write_text(
            target.read_text("utf-8").replace('"attributes":{', f'"attributes":{{"deep":{deep},', 1), "utf-8"
        )
    else:
        file.write_text(
            file.read_text("utf-8").replace('"attributes":{', f'"attributes":{{"deep":{deep},', 1), "utf-8"
        )
    everything = files_under(root)
    # Whatever is raised is one of the refusals the command prints in a line.
    with contextlib.suppress(StoreError, RecordError, SchemaError):
        planned(case, root, file, configuration)
    assert files_under(root) == everything


def test_a_record_the_schema_reads_and_the_writer_cannot_follow_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, root, _ = edited_store(tmp_path)

    def too_deep(_: object) -> bytes:
        raise RecursionError

    monkeypatch.setattr(writes, "js_written", too_deep)
    with pytest.raises(StoreError) as refusal:
        plan_change(case["ids"], "a-batch", "a-review", root, DATE)
    assert str(refusal.value) == f"{case['ids'][0]} cannot be written: it is nested too deeply"
    assert isinstance(refusal.value.__cause__, RecursionError)


# ---- the order addBatch runs its validators in ----------------------------------------------

#: A batch with two faults, and the validator the TypeScript answered with, which runs first.
FIRST_OF_TWO = {
    "a value outside its vocabulary and an attribute its kind does not declare": "Uncontrolled values:",
    "an attribute its kind does not declare and a description the batch has under two names": (
        "1 attribute problems:"
    ),
    "a description the batch has under two names and a derivation whose script is not there": (
        "2 descriptions are not functional."
    ),
    "a derivation whose script is not there and a scoped concern naming a record that is not there": (
        "1 derivation problems:"
    ),
    "a scoped concern naming a record that is not there and a link to a record that is not there": (
        "Invalid evidence concerns:"
    ),
}
#: The same faults alone, so that each of a pair is known to be refused by its own validator.
ALONE = {
    "a value outside its vocabulary": "Uncontrolled values:",
    "an attribute its kind does not declare": "1 attribute problems:",
    "a description the store already has under another name": "2 descriptions are not functional.",
    "a derivation whose script is not there, on sources the store holds": "1 derivation problems:",
    "a scoped concern naming a record that is not there": "Invalid evidence concerns:",
    "a link to a record that is not there": "Dangling ",
}


@pytest.mark.parametrize("name", [*FIRST_OF_TWO, *ALONE])
def test_an_add_runs_its_validators_in_the_order_the_typescript_runs_them(
    name: str, tmp_path: Path, configuration: Path
) -> None:
    # Vocabularies, attributes, descriptions, derivations, concerns, then validateRecords.
    # Each pair of neighbours has a batch that fails both, and the earlier one answers.
    case = case_named(f"add: {name}")
    start = {**FIRST_OF_TWO, **ALONE}[name]
    assert case["error"]["message"].startswith(start)
    root, file = lay_out(case, tmp_path)
    with pytest.raises((RecordError, VocabularyError)) as refusal:
        plan_add(file, case["batch_dir"], root, configuration)
    assert str(refusal.value) == case["error"]["message"]


def test_an_add_holds_a_concern_to_the_store_and_not_to_the_batch_alone(
    tmp_path: Path, configuration: Path
) -> None:
    # The concern is on a source the batch adds and names a dataset the store holds. Checked
    # against the batch alone, the dataset would not exist.
    case = case_named("add: a scoped concern naming a record the store holds")
    assert case["error"] is None
    root, file = lay_out(case, tmp_path)
    named = {
        each
        for line in case["input"].split("\n")
        if line
        for entry in json.loads(line)["attributes"].get("evidence_concerns", [])
        for each in entry["applies_to"]
    }
    held = {row["id"] for path in record_files(root) for row in read_rows(path)}
    assert len(named) == 1
    assert named <= held
    plan = plan_add(file, case["batch_dir"], root, configuration)
    write(plan)
    assert files_under(root) == left(case)
