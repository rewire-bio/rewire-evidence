"""The write path: add and both forms of change, held to what the TypeScript recorded."""

from __future__ import annotations

import datetime as dt
import os
import stat
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
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
from rewire_evidence.store import load_store, read_provenance, record_file, write_store
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
    run_command,
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
    assert Counter(case["command"] for case in CASES) == {"add": 32, "change": 24, "changes": 54}
    assert Counter(case["command"] for case in CASES if case["error"] is None) == {
        "add": 12,
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
    assert kinds == {"Error": 49, "zod": 14, "TypeError": 6, "SyntaxError": 4, "ENOENT": 2}


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


def test_the_shapes_check_runs_the_two_commands_the_typescript_runs(tmp_path: Path) -> None:
    ran: list[tuple[list[str], Path, str]] = []

    def run(command: Sequence[str], folder: Path) -> int:
        listed = Path(command[6]) if command[2] == "kg" else Path(command[7]) / "ids.txt"
        ran.append((list(command), folder, listed.read_text("utf-8")))
        return 0

    shapes_check(tmp_path, run)(["b-record", "a-record", "b-record"])
    (subset, where, ids), (build, where_again, _) = ran
    scratch = subset[7]
    assert subset == ["npm", "run", "-s", "kg", "--", "subset", f"{scratch}/ids.txt", scratch]
    assert build[:6] == ["npm", "run", "-s", "kg:build", "--", "--shapes-only"]
    assert build[6:] == ["--kg-dir", scratch, "--context", f"{scratch}/context.txt"]
    assert (where, where_again, ids) == (tmp_path, tmp_path, "b-record\na-record\nb-record\n")
    assert Path(scratch).name.startswith("records-shapes-")
    assert not Path(scratch).exists()


def test_the_shapes_check_refuses_when_a_command_fails_or_cannot_be_run(tmp_path: Path) -> None:
    ran: list[str] = []

    def second_fails(command: Sequence[str], _: Path) -> int:
        ran.append(command[3])
        return 3 if command[3] == "kg:build" else 0

    with pytest.raises(ShapesError, match="`npm run -s kg:build` exited with status 3"):
        shapes_check(tmp_path, second_fails)(["a"])
    assert ran == ["kg", "kg:build"]

    def first_fails(command: Sequence[str], _: Path) -> int:
        ran.append(command[3])
        return 1

    with pytest.raises(ShapesError, match="`npm run -s kg` exited with status 1"):
        shapes_check(tmp_path, first_fails)(["a"])
    assert ran == ["kg", "kg:build", "kg"]

    def absent(_: Sequence[str], __: Path) -> int:
        raise FileNotFoundError(2, "No such file or directory", "npm")

    with pytest.raises(ShapesError, match=r"`npm run -s kg` could not be run in .*: \[Errno 2\]"):
        shapes_check(tmp_path, absent)(["a"])


def test_a_command_is_run_in_the_folder_given_and_its_status_returned(tmp_path: Path) -> None:
    script = "import pathlib, sys; pathlib.Path('here').write_text('x'); sys.exit(4)"
    assert run_command([sys.executable, "-c", script], tmp_path) == 4
    assert (tmp_path / "here").exists()
    with pytest.raises(FileNotFoundError):
        run_command([str(tmp_path / "no-such-program")], tmp_path)


# ---- the commands ---------------------------------------------------------------------------

NPM = """#!/bin/sh
echo "$PWD|$*" >> "$NPM_LOG"
if [ "$3" = "kg" ]; then
  while IFS= read -r line; do echo "$line"; done < "$6" > "$NPM_IDS"
fi
if [ "$3" = "$NPM_FAILS" ]; then exit 7; fi
exit 0
"""


@pytest.fixture
def npm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for npm that writes down how it was run, and fails when told to."""
    folder = tmp_path / "bin"
    folder.mkdir()
    program = folder / "npm"
    program.write_text(NPM, "utf-8")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("NPM_LOG", str(tmp_path / "npm.log"))
    monkeypatch.setenv("NPM_IDS", str(tmp_path / "npm.ids"))
    monkeypatch.setenv("NPM_FAILS", "")
    monkeypatch.setattr(writes, "today", lambda: DATE)
    return tmp_path / "npm.log"


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
    case: dict[str, Any], tmp_path: Path, npm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, file = lay_out(case, tmp_path / "work")
    write_configuration(root)
    given = arguments(case, root, file)
    if case["command"] == "change" and not case["ids"]:
        # The TypeScript prints its usage for a change that names nothing.
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
    assert not npm.exists()
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
        # A line a file gains is a line the store gains, unless the file was only tidied.
        assert not wanted - Counter(gained)
    else:
        assert (status, said.out) == (1, "")
        assert said.err.endswith(f"\n{what} failed validation; the store is unchanged.\n")

    status = main(given)
    said = capsys.readouterr()
    if accepted:
        assert (status, said.out, said.err) == (0, f"{printed(case, file)}\n", "")
        assert store_files(root) == left(case)
        subset, build = npm.read_text("utf-8").rstrip("\n").split("\n")
        assert subset.startswith(f"{root.resolve()}|run -s kg -- subset ")
        assert build.startswith(f"{root.resolve()}|run -s kg:build -- --shapes-only --kg-dir ")
        ids = case["ids"] if case["command"] == "change" else None
        listed = [line for line in (tmp_path / "npm.ids").read_text("utf-8").split("\n") if line]
        assert ids is None or listed == ids
        assert len(listed) == (case["returned"] if case["command"] == "add" else len(listed))
    else:
        assert (status, said.out) == (1, "")
        assert said.err.endswith(f"\n{what} failed validation; the store is unchanged.\n")
        assert store_files(root) == before(case)
        assert not npm.exists()


@pytest.mark.parametrize("fails", ["kg", "kg:build"])
@pytest.mark.parametrize(
    "name",
    [
        "add: a result, into a shard file that is not there yet",
        "change: a line with its top-level keys reversed",
        "changes: an entity and a result, in another order than the store's",
    ],
)
def test_a_write_the_shapes_refuse_is_undone(  # noqa: PLR0913, PLR0917 - two parameters and four fixtures
    name: str,
    fails: str,
    tmp_path: Path,
    npm: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = case_named(name)
    root, file = lay_out(case, tmp_path / "work")
    write_configuration(root)
    everything = files_under(root)
    monkeypatch.setenv("NPM_FAILS", fails)
    assert main(arguments(case, root, file)) == 1
    said = capsys.readouterr()
    what = "The batch" if case["command"] == "add" else "The changed records"
    assert said.out == ""
    assert said.err == (
        f"`npm run -s {fails}` exited with status 7\n{what} failed validation; the store is unchanged.\n"
    )
    # The check ran on the written store, and then every file was put back and no new one left.
    assert len(npm.read_text("utf-8").rstrip("\n").split("\n")) == (1 if fails == "kg" else 2)
    assert files_under(root) == everything
    assert sorted(path.name for path in root.rglob("*") if path.is_dir() and not any(path.iterdir())) == (
        ["results"] if case["command"] == "add" else []
    )

    # With no npm to run at all the write is undone the same way.
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert main(arguments(case, root, file)) == 1
    said = capsys.readouterr()
    assert said.err.startswith(f"`npm run -s kg` could not be run in {root}: ")
    assert files_under(root) == everything


def test_the_command_refuses_a_change_given_both_ids_and_a_file(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as left_with:
        main(["records", "change", "a-review", "a-batch", "an-id", "--changes", "changes.jsonl"])
    assert left_with.value.code == 2
    assert "not both" in capsys.readouterr().err


def test_the_command_does_not_judge_a_host_it_cannot(
    tmp_path: Path, npm: Path, capsys: pytest.CaptureFixture[str]
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
    assert not npm.exists()


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
