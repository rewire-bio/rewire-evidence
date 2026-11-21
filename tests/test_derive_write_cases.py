"""The script that works out, from history, the commands a merged pull request ran.

`scripts/expected/store-writes.json` is what it wrote for the data repository. Here it is
run on the small history the replay tests use, whose commits hold what the TypeScript left,
and on commits it has to refuse. CI runs it again on the data repository's history and
requires the committed file.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.history import BATCH, CONFIGURATION, History, build, case_named, commit, git
from tests.written import DATE, left

SCRIPT = Path(__file__).parent.parent / "scripts" / "fixtures" / "derive_write_cases.py"


@pytest.fixture(scope="module")
def derive() -> ModuleType:
    spec = importlib.util.spec_from_file_location("derive_write_cases", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory) -> History:
    return build(tmp_path_factory.mktemp("derived") / "repository")


def test_the_commands_of_an_add_and_of_a_change_are_read_from_the_provenance_rows(
    derive: ModuleType, history: History, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = str(history.repository)
    added, changed = history.expectation["cases"]
    # The script names a case by its commit's subject and a change's records in the order
    # of their IDs. Nothing else differs from the expectation the replay tests wrote by hand.
    assert derive.case(clone, history.commits["the add"]) == {**added, "name": "a commit"}
    (step,) = changed["steps"]
    assert derive.case(clone, history.commits["the change"]) == {
        **changed,
        "name": "a commit",
        "steps": [{**step, "change": sorted(step["change"])}],
    }
    assert step["date"] == DATE
    assert sorted(step["change"]) != step["change"]

    # The command prints the expectation file, as the committed one is printed.
    assert derive.main([clone, history.commits["the add"], "HEAD~3"]) == 0
    printed = capsys.readouterr().out
    written = json.loads(printed)
    assert printed == f"{json.dumps(written, indent=1)}\n"
    assert written["configuration"] == CONFIGURATION == list(derive.CONFIGURATION)
    assert written["derived_by"] == "scripts/fixtures/derive_write_cases.py"
    assert [case["merge"] for case in written["cases"]] == [
        history.commits["the add"],
        history.commits["the change"],
    ]


def test_the_committed_cases_are_in_the_form_the_script_prints() -> None:
    committed = SCRIPT.parent.parent / "expected" / "store-writes.json"
    text = committed.read_text("utf-8")
    assert text == f"{json.dumps(json.loads(text), indent=1)}\n"
    assert json.loads(text)["derived_by"] == "scripts/fixtures/derive_write_cases.py"


def rows_of(files: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Every provenance row of a store by its ID, with the file it is in."""
    return {
        json.loads(line)["id"]: {"file": file, "row": json.loads(line)}
        for file, text in files.items()
        if file.startswith("data/provenance/")
        for line in text.split("\n")
        if line
    }


def with_row(
    files: dict[str, str], record_id: str, change: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> dict[str, str]:
    """A store with one provenance row replaced, or removed where the change gives None."""
    file = rows_of(files)[record_id]["file"]
    lines = []
    for line in files[file].split("\n")[:-1]:
        row = json.loads(line)
        after = change(row) if row["id"] == record_id else row
        if after is not None:
            lines.append(json.dumps(after, separators=(",", ":")))
    return {**files, file: "".join(f"{line}\n" for line in lines)}


@pytest.mark.parametrize(
    ("fault", "refusal"),
    [
        ("a row removed", "removes provenance rows"),
        ("a digest changed and no entry added", "and records no change"),
        ("an entry that is not a change's", "that records change does not write"),
        ("a row added with a change already", "with changes already recorded"),
        ("a batch folder with no batch file", "has no data/omics/a-new-batch/batch.jsonl"),
    ],
)
def test_a_merge_whose_commands_cannot_be_stated_is_refused(
    fault: str, refusal: str, derive: ModuleType, tmp_path: Path
) -> None:
    history = build(tmp_path / "repository")
    case = case_named("add: a result, into a shard file that is not there yet")
    added_id = json.loads(case["input"])["id"]
    held = left(case)
    other = next(record_id for record_id in rows_of(held) if record_id != added_id)
    entry = {"step": "records:change", "inputs": "a-batch", "date": DATE, "review": "a-review"}
    # Each is committed on the store before the add or the store after it.
    start, files = {
        "a row removed": ("the add", with_row(held, other, lambda _: None)),
        "a digest changed and no entry added": (
            "the add",
            with_row(held, other, lambda row: {**row, "sha256": "0" * 64}),
        ),
        "an entry that is not a change's": (
            "the add",
            with_row(held, other, lambda row: {**row, "changed_by": [{**entry, "step": "by-hand"}]}),
        ),
        "a row added with a change already": (
            "before the add",
            {**with_row(held, added_id, lambda row: {**row, "changed_by": [entry]}), BATCH: case["input"]},
        ),
        "a batch folder with no batch file": ("before the add", held),
    }[fault]
    repository = history.repository
    # A commit on top of the one to start from.
    git(repository, "checkout", "--quiet", "--detach", history.commits[start])
    merge = commit(repository, files)
    with pytest.raises(SystemExit, match=refusal):
        derive.case(str(repository), merge)
