"""A small history of a store in git, for the replay to run on.

The commits hold the stores `tests/fixtures/write-path.json` records: what a store held
before the TypeScript wrote to it, and what the TypeScript left. So a replay of this history
is held to the TypeScript's bytes, as a replay of the data repository's is.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rewire_evidence.replay import blob_name
from tests.recorded import write_configuration
from tests.written import CASES, DATE, WRITES, before, files_under, label, left

BATCH = "data/omics/a-new-batch/batch.jsonl"
CHANGES = "data/omics/a-new-batch/changes.jsonl"
CONFIGURATION = ["data/vocab", "data/ontology/mapping.json", "scripts/omics/derive"]
STORE = ("data/entities/", "data/evidence/", "data/provenance/")

ENVIRONMENT = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "A Test",
    "GIT_AUTHOR_EMAIL": "test@example.org",
    "GIT_COMMITTER_NAME": "A Test",
    "GIT_COMMITTER_EMAIL": "test@example.org",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def git(repository: Path, *arguments: str) -> str:
    done = subprocess.run(  # noqa: S603 - git, in a folder the test made
        ["git", "-C", str(repository), *arguments],  # noqa: S607
        capture_output=True,
        check=True,
        encoding="utf-8",
        env=ENVIRONMENT,
    )
    return done.stdout.strip()


def commit(repository: Path, files: dict[str, str]) -> str:
    """Make the work tree hold exactly these files and commit it. Returns the commit's name."""
    for entry in repository.iterdir():
        if entry.name != ".git":
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    for file, text in files.items():
        path = repository / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    git(repository, "add", "--all")
    git(repository, "commit", "--quiet", "--allow-empty", "--message", "a commit")
    return git(repository, "rev-parse", "HEAD")


def case_named(name: str) -> dict[str, Any]:
    return next(case for case in CASES if label(case) == name)


def configuration_files() -> dict[str, str]:
    with tempfile.TemporaryDirectory() as folder:
        write_configuration(Path(folder))
        return files_under(Path(folder))


def store_count(files: dict[str, str]) -> int:
    return sum(file.startswith(STORE) for file in files)


@dataclass(frozen=True)
class History:
    """A repository, its commits by what they hold, and the expectations that replay them."""

    repository: Path
    commits: dict[str, str]
    #: Two merges: an add and a change.
    expectation: dict[str, Any]
    #: One write on one commit, with the blob names of what it left: an add, then a changes file.
    recorded: dict[str, Any]

    def written(
        self, folder: Path, expectation: dict[str, Any] | None = None, name: str = "store-writes.json"
    ) -> Path:
        """The expectation of the two merges, or another, as a file."""
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(json.dumps(self.expectation if expectation is None else expectation), "utf-8")
        return path


def build(repository: Path) -> History:
    """Seven commits: an add and a change as the TypeScript made them, and what the faults need."""
    added = case_named("add: a result, into a shard file that is not there yet")
    changed = case_named("change: an entity and a result edited in place, named in another order")
    whole: dict[str, str] = WRITES["stores"]["whole"]
    settings = configuration_files()
    repository.mkdir(parents=True)
    git(repository, "init", "--quiet", "--initial-branch", "main")
    commits = {
        "before the add": commit(repository, {**settings, **before(added)}),
        "the add": commit(repository, {**settings, **left(added), BATCH: added["input"]}),
        "before the change": commit(repository, {**settings, **whole}),
        "the change": commit(repository, {**settings, **left(changed)}),
        # The batch again, in a store that holds its record already.
        "the add again": commit(repository, {**settings, **left(changed), BATCH: added["input"]}),
        # A source gone from a file that is still there.
        "a record gone": commit(
            repository,
            {
                **settings,
                **left(changed),
                "data/entities/sources.jsonl": "".join(
                    f"{line}\n" for line in left(changed)["data/entities/sources.jsonl"].split("\n")[1:-1]
                ),
            },
        ),
    }
    expectation = {
        "repository": "a test",
        "configuration": CONFIGURATION,
        "cases": [
            {
                "name": "an add",
                "merge": commits["the add"],
                "parent": commits["before the add"],
                "steps": [{"add": BATCH, "batch_dir": added["batch_dir"]}],
                "added": 1,
                "changed": 0,
                "files": store_count(left(added)),
            },
            {
                "name": "a change",
                "merge": commits["the change"],
                "parent": commits["before the change"],
                "steps": [
                    {
                        "change": changed["ids"],
                        "batch_dir": changed["batch_dir"],
                        "review": changed["review"],
                        "date": DATE,
                    }
                ],
                "added": 0,
                "changed": 2,
                "files": store_count(left(changed)),
            },
        ],
    }
    # The batch and a changes file on the store before the add. The two touch different
    # files, so what both leave is what the TypeScript left for each.
    applied = case_named("changes: one changed record")
    commits["the inputs"] = commit(
        repository, {**settings, **before(added), BATCH: added["input"], CHANGES: applied["input"]}
    )
    both = {**left(added), **{file: left(applied)[file] for file in applied["after"]}}
    recorded = {
        "recorded_from": "a test",
        "configuration": CONFIGURATION,
        "cases": [
            {
                "name": "a recorded write",
                "store": commits["the inputs"],
                "steps": [
                    {"add": BATCH, "batch_dir": added["batch_dir"]},
                    {
                        "changes": CHANGES,
                        "batch_dir": applied["batch_dir"],
                        "review": applied["review"],
                        "date": DATE,
                    },
                ],
                "added": 1,
                "changed": 1,
                "files": store_count(both),
                "left": {file: blob_name(text.encode("utf-8")) for file, text in both.items()},
            }
        ],
    }
    return History(repository, commits, expectation, recorded)
