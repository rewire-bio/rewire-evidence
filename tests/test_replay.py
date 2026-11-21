"""The write gate's replay: a history in git, the commands run again, the result held to the tree."""

from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rewire_evidence.jsonl import StoreError
from rewire_evidence.replay import (
    STORE_PATHS,
    Case,
    InputError,
    ReplayReport,
    Repository,
    Step,
    blob_name,
    case_line,
    compare,
    edit_in_place,
    lay_out,
    read_cases,
    replay_case,
    to_json,
    tree_names,
)
from tests.history import BATCH, CHANGES, CONFIGURATION, History, build, case_named, commit, git
from tests.written import DATE, WRITES, left


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory) -> History:
    return build(tmp_path_factory.mktemp("history") / "repository")


def cases_of(
    history: History, folder: Path, change: Callable[[dict[str, Any]], None] | None = None
) -> tuple[Case, ...]:
    expectation = copy.deepcopy(history.expectation)
    if change is not None:
        change(expectation)
    return read_cases(history.written(folder, expectation)).cases


def test_a_history_replays_to_the_bytes_the_typescript_left(history: History, tmp_path: Path) -> None:
    expectation = read_cases(history.written(tmp_path))
    assert expectation.configuration == tuple(CONFIGURATION)
    repository = Repository(history.repository)
    report = ReplayReport()
    for case in expectation.cases:
        report.cases.append(replay_case(repository, case, expectation.configuration, tmp_path / "scratch"))
    assert report.problems == []
    assert report.reproduced == 2
    assert (report.total("added"), report.total("changed")) == (1, 2)
    assert report.total("files") == sum(case.files for case in expectation.cases) == 26
    assert [case.written for case in report.cases] == [2, 2]
    # The scratch folder is left as the last case made it.
    change = case_named("change: an entity and a result edited in place, named in another order")
    names = tree_names(tmp_path / "scratch" / "tree")
    assert {file: name for file, name in names.items() if file.startswith("data/e")} == {
        file: blob_name(text.encode("utf-8"))
        for file, text in left(change).items()
        if file.startswith("data/e")
    }
    assert case_line(report.cases[0]) == (
        "an add: 1 added, 0 changes recorded, 2 files written, 13 compared: reproduces"
    )
    measured = json.loads(to_json(report))
    assert {name: measured[name] for name in measured if name not in {"results", "problems"}} == {
        "cases": 2,
        "reproduced": 2,
        "records_added": 1,
        "changes_recorded": 2,
        "files_compared": 26,
        "files_written": 4,
    }
    assert measured["problems"] == []
    assert measured["results"][1] == {
        "name": "a change",
        "added": 0,
        "changed": 2,
        "files": 13,
        "written": 2,
        "problems": [],
    }


def test_a_write_the_typescript_recorded_replays_to_the_blob_names_it_left(
    history: History, tmp_path: Path
) -> None:
    expectation = read_cases(history.written(tmp_path, history.recorded))
    (case,) = expectation.cases
    assert case.merge == case.parent == history.commits["the inputs"]
    assert case.left is not None
    assert len(case.left) == case.files == 13
    assert [(step.batch, step.changes, step.ids) for step in case.steps] == [
        (BATCH, None, ()),
        (None, CHANGES, ()),
    ]
    repository = Repository(history.repository)
    report = replay_case(repository, case, expectation.configuration, tmp_path / "scratch")
    assert report.problems == []
    assert (report.added, report.changed, report.files, report.written) == (1, 1, 13, 4)
    # The changes file is given to the command by name, as the batch is.
    assert (tmp_path / "scratch" / "changes.jsonl").read_text("utf-8") == case_named(
        "changes: one changed record"
    )["input"]

    # A blob name that is not what was left is a problem, and so is a command that is refused.
    def other_blob(recorded: dict[str, Any]) -> None:
        recorded["cases"][0]["left"]["data/entities/methods.jsonl"] = "0" * 40

    altered = copy.deepcopy(history.recorded)
    other_blob(altered)
    (case,) = read_cases(history.written(tmp_path / "altered", altered)).cases
    (problem,) = replay_case(repository, case, CONFIGURATION, tmp_path / "scratch").problems
    assert re.fullmatch(
        r"data/entities/methods\.jsonl is blob [0-9a-f]{40}, not 0{40} as in what the TypeScript left",
        problem,
    )
    stale = copy.deepcopy(history.recorded)
    stale["cases"][0]["steps"] = [stale["cases"][0]["steps"][1]] * 2
    (case,) = read_cases(history.written(tmp_path / "stale", stale)).cases
    (problem,) = replay_case(repository, case, CONFIGURATION, tmp_path / "scratch").problems
    assert problem.startswith(
        f"step 2 was refused: RecordError: {tmp_path / 'scratch' / 'changes.jsonl'} line 1: "
    )
    assert " has provenance digest " in problem
    lacking = copy.deepcopy(history.recorded)
    lacking["cases"][0]["steps"][1]["changes"] = "no-such/changes.jsonl"
    (case,) = read_cases(history.written(tmp_path / "lacking", lacking)).cases
    with pytest.raises(InputError, match=r" has no no-such/changes\.jsonl"):
        replay_case(repository, case, CONFIGURATION, tmp_path / "scratch")


def test_a_blobless_clone_fetches_what_a_replay_needs(history: History, tmp_path: Path) -> None:
    git(history.repository, "config", "uploadpack.allowFilter", "true")
    git(history.repository, "config", "uploadpack.allowAnySHA1InWant", "true")
    clone = tmp_path / "clone"
    git(
        tmp_path,
        "clone",
        "--quiet",
        "--filter=blob:none",
        "--no-checkout",
        f"file://{history.repository}",
        str(clone),
    )
    repository = Repository(clone)
    assert repository.promisor() == "origin"
    case = cases_of(history, tmp_path)[0]
    wanted = sorted(set(repository.tree(case.parent, STORE_PATHS).values()))
    assert repository.fetch([]) == 0
    assert repository.fetch([*wanted, wanted[0]]) == len(wanted)
    assert repository.fetch(wanted) == 0
    report = replay_case(repository, case, CONFIGURATION, tmp_path / "scratch")
    assert report.problems == []
    assert (report.added, report.files) == (1, case.files)


def test_a_clone_that_lacks_a_blob_and_cannot_fetch_it_is_refused(history: History, tmp_path: Path) -> None:
    copied = tmp_path / "copy"
    shutil.copytree(history.repository, copied)
    repository = Repository(copied)
    assert repository.promisor() is None
    case = cases_of(history, tmp_path)[0]
    name = repository.tree(case.merge, [BATCH])[BATCH]
    assert repository.fetch([name]) == 0
    (copied / ".git" / "objects" / name[:2] / name[2:]).unlink()
    with pytest.raises(StoreError, match="lacks 1 blobs and is not a partial clone that can fetch them"):
        repository.fetch([name])
    # A remote that is named and is not a promisor is no more use.
    git(copied, "config", "remote.elsewhere.promisor", "false")
    assert repository.promisor() is None


def test_git_that_fails_or_cannot_be_run_is_a_run_that_cannot_be_made(
    history: History, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(StoreError, match=f"git rev-parse --verify failed in {tmp_path}: "):
        Repository(tmp_path).commit("HEAD")
    with pytest.raises(StoreError, match="git rev-parse --verify failed in "):
        Repository(history.repository).commit("no-such-revision")
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(StoreError, match="git cannot be run: "):
        Repository(history.repository).commit("HEAD")


def test_a_folder_is_laid_out_by_writing_only_what_it_lacks(history: History, tmp_path: Path) -> None:
    repository = Repository(history.repository)
    first = repository.tree(history.commits["before the add"], [*STORE_PATHS, *CONFIGURATION])
    second = repository.tree(history.commits["the change"], [*STORE_PATHS, *CONFIGURATION])
    root = tmp_path / "tree"
    assert lay_out(repository, root, first) == len(first)
    assert tree_names(root) == first
    assert lay_out(repository, root, first) == 0
    (root / "data/entities/models.jsonl").write_bytes(b"left by an earlier case\n")
    differing = {file for file in second if first.get(file) != second[file]}
    assert 0 < len(differing) < len(second)
    assert lay_out(repository, root, second) == len(differing)
    assert tree_names(root) == second


@given(st.binary(max_size=64))
@settings(max_examples=15)
def test_a_blob_is_named_as_git_names_it(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "blob").write_bytes(data)
        assert blob_name(data) == git(Path(folder), "hash-object", "blob")


def test_a_tree_of_no_files_has_no_names(tmp_path: Path) -> None:
    (tmp_path / "empty" / "folder").mkdir(parents=True)
    assert tree_names(tmp_path) == {}


# ---- what does not reproduce ---------------------------------------------------------------


def replayed(
    history: History, folder: Path, change: Callable[[dict[str, Any]], None], which: int = 0
) -> list[str]:
    case = cases_of(history, folder, change)[which]
    return replay_case(Repository(history.repository), case, CONFIGURATION, folder / "scratch").problems


def test_a_parent_that_is_not_the_first_parent_is_a_problem(history: History, tmp_path: Path) -> None:
    def other_parent(expectation: dict[str, Any]) -> None:
        expectation["cases"][0]["parent"] = history.commits["the change"]

    (problem,) = replayed(history, tmp_path, other_parent)
    assert problem == (
        f"the first parent of {history.commits['the add']} is {history.commits['before the add']}, "
        f"not {history.commits['the change']}"
    )
    assert not (tmp_path / "scratch").exists()


def test_a_command_that_is_refused_is_a_problem(history: History, tmp_path: Path) -> None:
    def onto_itself(expectation: dict[str, Any]) -> None:
        expectation["cases"][0].update(
            merge=history.commits["the add again"], parent=history.commits["the change"]
        )

    (problem,) = replayed(history, tmp_path, onto_itself)
    assert problem.startswith("step 1 was refused: RecordError: Batch cannot replace or duplicate record: ")


def test_a_result_that_is_not_the_merge_commits_is_a_problem_naming_the_line(
    history: History, tmp_path: Path
) -> None:
    def other_folder(expectation: dict[str, Any]) -> None:
        expectation["cases"][0]["steps"][0]["batch_dir"] = "another-folder"

    (problem,) = replayed(history, tmp_path, other_folder)
    assert problem.startswith("data/provenance/records/0.jsonl is blob ")
    assert re.search(r': line \d+ differs from byte \d+: it is \{"id":"rna-splicing-', problem)
    assert '"added_in":"another-folder"' in problem
    assert ' and the merge commit holds {"id":"rna-splicing-' in problem
    assert '"added_in":"data/omics/a-new-batch"' in problem

    def no_steps(expectation: dict[str, Any]) -> None:
        expectation["cases"][0]["steps"] = []

    problems = replayed(history, tmp_path / "second", no_steps)
    assert problems == [
        "data/evidence/results/0.jsonl is in the merge commit and was not written",
        "data/provenance/records/0.jsonl is in the merge commit and was not written",
        "added is 0, and 1 is expected",
    ]

    def fewer_files(expectation: dict[str, Any]) -> None:
        expectation["cases"][0]["files"] -= 1
        expectation["cases"][0]["changed"] = 5

    assert replayed(history, tmp_path / "third", fewer_files) == [
        "changed is 0, and 5 is expected",
        "files is 13, and 12 is expected",
    ]


def test_a_comparison_names_what_is_missing_what_is_extra_and_where_a_file_differs(tmp_path: Path) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    git(source, "init", "--quiet", "--initial-branch", "main")
    merge = commit(
        source,
        {
            "data/entities/same.jsonl": "1\n",
            "data/entities/shorter.jsonl": "1\n2\n",
            "data/entities/other.jsonl": "1\n2\n3\n",
            "data/entities/longer.jsonl": "1\n22\n",
            "data/entities/open.jsonl": "1\n2",
            "data/evidence/claims/0.jsonl": "1\n",
            "elsewhere/ignored.jsonl": "1\n",
        },
    )
    root = tmp_path / "tree"
    for file, text in {
        "data/entities/same.jsonl": "1\n",
        "data/entities/shorter.jsonl": "1\n",
        "data/entities/other.jsonl": "1\nB\n3\n",
        "data/entities/longer.jsonl": "1\n2\n",
        "data/entities/open.jsonl": "1\n2\n",
        "data/provenance/records/0.jsonl": "1\n",
        "data/vocab/ignored.ttl": "1\n",
    }.items():
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        (root / file).write_text(text, "utf-8")
    repository = Repository(source)
    wanted = repository.tree(merge, STORE_PATHS)
    count, problems = compare(wanted, root, "the merge commit", repository)
    assert count == 7
    assert problems == [
        (
            f"data/entities/longer.jsonl is blob {blob_name(b'1\n2\n')}, not {blob_name(b'1\n22\n')}: "
            "line 2 differs from byte 1: it is 2 and the merge commit holds 22"
        ),
        (
            f"data/entities/open.jsonl is blob {blob_name(b'1\n2\n')}, not {blob_name(b'1\n2')}: "
            "its lines are the merge commit's, and it ends differently: one has a final newline"
        ),
        (
            f"data/entities/other.jsonl is blob {blob_name(b'1\nB\n3\n')}, not {blob_name(b'1\n2\n3\n')}: "
            "line 2 differs from byte 0: it is B and the merge commit holds 2"
        ),
        (
            f"data/entities/shorter.jsonl is blob {blob_name(b'1\n')}, not {blob_name(b'1\n2\n')}: "
            "it has 1 lines and the merge commit's has 2"
        ),
        "data/evidence/claims/0.jsonl is in the merge commit and was not written",
        "data/provenance/records/0.jsonl was written and is not in the merge commit",
    ]
    # With blob names alone there is no line to show, and the oracle is named as it is given.
    count, named = compare(wanted, root, "what was recorded")
    assert count == 7
    assert [problem.split(": ")[0] for problem in problems[:4]] == [
        problem.removesuffix(" as in what was recorded") for problem in named[:4]
    ]
    assert named[4:] == [
        "data/evidence/claims/0.jsonl is in what was recorded and was not written",
        "data/provenance/records/0.jsonl was written and is not in what was recorded",
    ]


def test_a_case_whose_inputs_the_history_lacks_cannot_be_replayed(history: History, tmp_path: Path) -> None:
    repository = Repository(history.repository)

    def no_batch(expectation: dict[str, Any]) -> None:
        expectation["cases"][0]["steps"][0]["add"] = "data/omics/no-such-batch/batch.jsonl"

    with pytest.raises(InputError, match=r" has no data/omics/no-such-batch/batch\.jsonl"):
        replayed(history, tmp_path, no_batch)

    def unknown_id(expectation: dict[str, Any]) -> None:
        expectation["cases"][1]["steps"][0]["change"].append("no-such-record")

    with pytest.raises(InputError, match="the store has no record no-such-record to edit in place"):
        replayed(history, tmp_path / "second", unknown_id, 1)

    # A record the parent holds and the merge commit's file does not.
    gone = WRITES["stores"]["whole"]["data/entities/sources.jsonl"].split("\n")[0]
    gone_id = json.loads(gone)["id"]
    root = tmp_path / "tree"
    lay_out(repository, root, repository.tree(history.commits["the change"], STORE_PATHS))
    with pytest.raises(InputError, match=f"data/entities/sources.jsonl at .* has no record {gone_id}"):
        edit_in_place(repository, history.commits["a record gone"], root, [gone_id])
    # With nothing named there is nothing to edit, and nothing is read from the history.
    untouched = tree_names(root)
    edit_in_place(Repository(tmp_path / "nowhere"), "no-such-commit", root, [])
    assert tree_names(root) == untouched


# ---- the expectation file -------------------------------------------------------------------


def test_the_expectation_is_read_as_cases_and_steps(history: History, tmp_path: Path) -> None:
    add, change = cases_of(history, tmp_path)
    assert add.steps == (Step(BATCH, "data/omics/a-new-batch"),)
    assert change.steps[0].batch is None
    assert (change.steps[0].review, change.steps[0].date) == ("docs/reviews/a-review.md", DATE)
    assert len(change.steps[0].ids) == 2
    assert (add.added, add.changed, add.files) == (1, 0, 13)


def _set(path: list[str | int], value: object) -> Callable[[dict[str, Any]], None]:
    def change(expectation: dict[str, Any]) -> None:
        node: Any = expectation
        for key in path[:-1]:
            node = node[key]
        if value is ...:
            del node[path[-1]]
        else:
            node[path[-1]] = value

    return change


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        (_set(["cases"], []), "lists no cases"),
        (_set(["cases"], {}), "has no list 'cases'"),
        (_set(["cases"], ...), "has no list 'cases'"),
        (_set(["cases", 0], "a case"), "case 1 of .* is not an object"),
        (_set(["cases", 1, "name"], 5), "case 2 of .* has no str 'name'"),
        (
            _set(["cases", 0, "merge"], "abf2338"),
            "names its merge as 'abf2338', which is not a full commit name",
        ),
        (_set(["cases", 0, "parent"], "G" * 40), "names its parent as .* which is not a full commit name"),
        (_set(["cases", 0, "parent"], ...), "has no str 'parent'"),
        (_set(["cases", 0, "steps"], "add"), "has no list 'steps'"),
        (_set(["cases", 0, "steps", 0], ["add"]), "step 1 of case 1 of .* is not an object"),
        (_set(["cases", 0, "steps", 0, "batch_dir"], ...), "step 1 of case 1 of .* has no str 'batch_dir'"),
        (_set(["cases", 0, "steps", 0, "add"], 5), "has no str 'add'"),
        (
            _set(["cases", 1, "steps", 0, "change"], "an-id"),
            "step 1 of case 2 of .*'s change is not a list of text",
        ),
        (_set(["cases", 1, "steps", 0, "change"], ["an-id", 5]), "'s change is not a list of text"),
        (_set(["cases", 1, "steps", 0, "review"], ...), "has no str 'review'"),
        (_set(["cases", 1, "steps", 0, "date"], None), "has no str 'date'"),
        (_set(["cases", 0, "added"], "1"), "has no int 'added'"),
        (_set(["cases", 0, "changed"], True), "has no int 'changed'"),  # noqa: FBT003 - the value refused
        (_set(["cases", 0, "files"], 1.5), "has no int 'files'"),
        (_set(["configuration"], "data/vocab"), "the configuration of .* is not a list of text"),
        (_set(["configuration"], ...), "the configuration of .* is not a list of text"),
    ],
)
def test_an_expectation_that_does_not_say_what_to_replay_is_refused(
    history: History, tmp_path: Path, change: Callable[[dict[str, Any]], None], refusal: str
) -> None:
    with pytest.raises(StoreError, match=refusal):
        cases_of(history, tmp_path, change)


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        (_set(["cases", 0, "store"], "HEAD"), "names its store as 'HEAD', which is not a full commit name"),
        (_set(["cases", 0, "left"], ...), "what case 1 of .* left is not an object of blob names by file"),
        (_set(["cases", 0, "left"], ["a"]), "left is not an object of blob names by file"),
        (_set(["cases", 0, "left", "data/entities/methods.jsonl"], 5), "left is not an object of blob names"),
        (_set(["cases", 0, "steps", 1, "changes"], ["a"]), "step 2 of case 1 of .* has no str 'changes'"),
        (_set(["cases", 0, "steps", 1, "review"], ...), "has no str 'review'"),
    ],
)
def test_a_recorded_write_that_does_not_say_what_was_left_is_refused(
    history: History, tmp_path: Path, change: Callable[[dict[str, Any]], None], refusal: str
) -> None:
    recorded = copy.deepcopy(history.recorded)
    change(recorded)
    with pytest.raises(StoreError, match=refusal):
        read_cases(history.written(tmp_path, recorded))


def test_an_expectation_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="cannot be read"):
        read_cases(tmp_path / "missing.json")
    (tmp_path / "bytes.json").write_bytes(b"\xff")
    with pytest.raises(StoreError, match="cannot be read"):
        read_cases(tmp_path / "bytes.json")
    (tmp_path / "list.json").write_text("[]", "utf-8")
    with pytest.raises(StoreError, match="is not a JSON object"):
        read_cases(tmp_path / "list.json")


def test_a_changes_file_kept_with_the_expectation_is_replayed_from_there(
    history: History, tmp_path: Path
) -> None:
    # The data repository holds one changes file. Others are written from the pinned store
    # and kept beside the expectation, which names each by its path from its own folder.
    applied = case_named("changes: one changed record")
    kept = copy.deepcopy(history.recorded)
    (held,) = kept["cases"]
    held["steps"][1] = {**held["steps"][1], "changes_here": "changes/one.jsonl"}
    del held["steps"][1]["changes"]
    folder = tmp_path / "expected"
    (folder / "changes").mkdir(parents=True)
    (folder / "changes" / "one.jsonl").write_text(applied["input"], "utf-8")
    expectation = read_cases(history.written(folder, kept))
    (case,) = expectation.cases
    assert [(step.batch, step.changes, step.changes_file) for step in case.steps] == [
        (BATCH, None, None),
        (None, None, folder / "changes" / "one.jsonl"),
    ]
    repository = Repository(history.repository)
    report = replay_case(repository, case, expectation.configuration, tmp_path / "scratch")
    assert report.problems == []
    assert (report.added, report.changed, report.files, report.written) == (1, 1, 13, 4)
    # Nothing is copied: the command is given the file where it is kept.
    assert not (tmp_path / "scratch" / "changes.jsonl").exists()

    # A refusal names the file as it is kept, and a file that is not there cannot be replayed.
    held["steps"] = [held["steps"][1]] * 2
    (case,) = read_cases(history.written(folder, kept)).cases
    (problem,) = replay_case(repository, case, CONFIGURATION, tmp_path / "scratch").problems
    assert problem.startswith(f"step 2 was refused: RecordError: {folder / 'changes' / 'one.jsonl'} line 1: ")
    (folder / "changes" / "one.jsonl").unlink()
    with pytest.raises(InputError, match=r"there is no changes file .*changes/one\.jsonl"):
        replay_case(repository, case, CONFIGURATION, tmp_path / "scratch")
    held["steps"] = [{"changes_here": 5, "batch_dir": "a", "review": "b", "date": "c"}]
    with pytest.raises(StoreError, match="has no str 'changes_here'"):
        read_cases(history.written(folder, kept))


def test_the_committed_expectation_names_what_the_gate_replays() -> None:
    expectation = read_cases(Path(__file__).parent.parent / "scripts/expected/store-writes.json")
    assert expectation.configuration == ("data/vocab", "data/ontology/mapping.json", "scripts/omics/derive")
    assert len(expectation.cases) >= 6
    first = expectation.cases[0]
    assert first.name == "Merge pull request #310 from rewire-bio/intake/issue-250"
    assert first.steps == (
        Step("data/omics/intake-250-20261119/batch.jsonl", "data/omics/intake-250-20261119"),
    )
    assert (first.added, first.changed) == (10, 0)
    assert any(step.batch is None for case in expectation.cases for step in case.steps)
    # Every case is a first parent and its merge, by full name, with something to do.
    assert all(case.steps and case.merge != case.parent for case in expectation.cases)
    assert sum(case.added for case in expectation.cases) >= 10

    # The writes the TypeScript recorded, each on the one commit: an add and the data
    # repository's changes file, and two changes files kept here, with what each left.
    expected = Path(__file__).parent.parent / "scripts/expected"
    recorded = read_cases(expected / "store-recorded-writes.json")
    assert recorded.configuration == expectation.configuration
    fixture, every_file, three = recorded.cases
    assert [(step.batch is None, step.changes is None) for step in fixture.steps] == [
        (False, True),
        (True, False),
    ]
    assert [[step.changes_file for step in case.steps] for case in (every_file, three)] == [
        [expected / "changes/one-record-in-every-file.jsonl"],
        [expected / "changes/three-records.jsonl"],
    ]
    assert [(case.added, case.changed) for case in recorded.cases] == [(1, 1), (0, 62), (0, 3)]
    for case in recorded.cases:
        assert case.merge == case.parent == fixture.merge
        assert case.left is not None
        assert len(case.left) == case.files == 79
        assert all(
            re.fullmatch(r"data/(entities|evidence|provenance)/[a-z0-9/-]+\.jsonl", file)
            for file in case.left
        )
        assert all(re.fullmatch(r"[0-9a-f]{40}", name) for name in case.left.values())
    # A changes file kept here holds as many lines as its case says it changes, each with a
    # record, the digest it was based on and the digest it has. No record is named twice.
    for case in (every_file, three):
        (step,) = case.steps
        assert step.changes_file is not None
        lines = [json.loads(line) for line in step.changes_file.read_text("utf-8").split("\n") if line]
        assert len(lines) == len({line["id"] for line in lines}) == case.changed
        assert all(list(line) == ["id", "base_sha256", "sha256", "record"] for line in lines)
        assert all(line["record"]["id"] == line["id"] for line in lines)
    # One record of every record file.
    assert every_file.left is not None
    assert sum(not file.startswith("data/provenance/") for file in every_file.left) == every_file.changed
