"""What the write gate measures: merged batches replayed against the history of the data repository.

The store's history is the oracle for the write path. A pull request that added a batch or
recorded a change was merged with the files the TypeScript wrote. So for each case in
`scripts/expected/store-writes.json`:

1. the store is laid out as the merge's first parent holds it, with the vocabularies, the
   mapping and the derivation scripts of the merge commit, which is the checkout the
   command ran in;
2. the commands the pull request ran are run again by this library, in order: `add` with
   the batch file the merge commit holds, `change` with the IDs, review, batch folder and
   date its provenance rows record;
3. every file under `data/entities`, `data/evidence` and `data/provenance` is hashed as git
   hashes a blob, and the hashes have to be the ones the merge commit's tree lists, file
   for file: the files that should have changed and the ones that should not.

Comparing blob IDs needs none of the merge commit's store files, so a blobless clone only
has to fetch the parent's.

`records change <id>...` records an edit made by hand beforehand. The edit is an input and
not something the command produces, so the replay takes each named record's line from the
merge commit, puts it in place of the parent's, and then runs the command. What that holds
the command to is the provenance rows and the rewrite of every store file, not the edit.

The SHACL check the command line makes after writing is not part of a replay: it reads the
store and writes nothing.

History holds no `records change --changes`, which arrived in the pinned commit. So a second
expectation, `scripts/expected/store-recorded-writes.json`, holds cases the TypeScript ran
itself in CI on the pinned store: the data repository's own reviewer fixture, a batch and a
changes file, added and applied, and two changes files written from the store and kept in
`scripts/expected/changes/`. Such a case names one commit, whose store is laid out and
which holds the inputs unless a step says they are kept with the expectation, and lists the
blob name of every store file the TypeScript left. It is replayed and compared in the same
way, against those names in place of a tree.

A case that does not reproduce is reported with the file, both blob IDs and the first line
that differs. The counts of records added and changed and of files compared are held to the
expectation, so a replay that did less cannot pass.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from rewire_evidence.canonical import JsonObject, JsonValue, js_indented
from rewire_evidence.jsonl import StoreError, join_lines, parse_row, read_lines, text_field, write_text
from rewire_evidence.schema import RecordError, SchemaError
from rewire_evidence.store import record_files
from rewire_evidence.vocab import VocabularyError
from rewire_evidence.writes import Plan, plan_add, plan_change, plan_changes_file, write

#: The folders a write may alter, which are compared whole with the merge commit.
STORE_PATHS = ("data/entities", "data/evidence", "data/provenance")
#: A full object name, which is how the expectation names a commit.
_NAME_LENGTH = 40
#: How much of a mismatched line is shown: this much of its start, and of what differs.
_SHOWN = 120


class InputError(StoreError):
    """The history does not hold what a case says a command was given, so it cannot be replayed."""


class Repository:
    """A clone of the data repository with its history, read through git and never changed.

    A blobless clone (`git clone --filter=blob:none --no-checkout`) is enough: the blobs a
    replay needs are fetched in one request when they are first asked for.
    """

    def __init__(self, path: Path) -> None:
        """Keep the path. Nothing is read until a method asks."""
        self.path = path

    def git(self, *arguments: str, given: bytes | None = None, lazy: bool = True) -> bytes:
        """Run git in the repository and return what it wrote. Raises `StoreError` if it fails."""
        environment = dict(os.environ) if lazy else {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
        try:
            done = subprocess.run(  # noqa: S603 - git, with arguments this module writes
                ["git", "-C", str(self.path), *arguments],  # noqa: S607 - whichever git the machine has
                input=given,
                capture_output=True,
                check=False,
                env=environment,
            )
        except OSError as error:
            raise StoreError(f"git cannot be run: {error}") from error
        if done.returncode:
            said = done.stderr.decode("utf-8", errors="replace").strip()
            raise StoreError(f"git {' '.join(arguments[:2])} failed in {self.path}: {said}")
        return done.stdout

    def commit(self, revision: str) -> str:
        """The full name of the commit a revision names."""
        return self.git("rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}").decode("ascii").strip()

    def tree(self, commit: str, paths: Sequence[str]) -> dict[str, str]:
        """Every file under the paths at a commit, with the name of its blob."""
        listed = self.git("ls-tree", "-r", "-z", commit, "--", *paths).decode("utf-8")
        files: dict[str, str] = {}
        for entry in listed.split("\0"):
            if entry:
                described, path = entry.split("\t", 1)
                files[path] = described.split(" ")[2]
        return files

    def promisor(self) -> str | None:
        """The remote a partial clone fetches missing objects from, or None if it is not one."""
        try:
            listed = self.git("config", "--get-regexp", r"^remote\..*\.promisor$").decode("utf-8")
        except StoreError:
            # git config exits 1 when nothing matches.
            return None
        for line in listed.split("\n"):
            name, _, value = line.partition(" ")
            if value == "true":
                return name.removeprefix("remote.").removesuffix(".promisor")
        return None

    def fetch(self, names: Sequence[str]) -> int:
        """Make sure the repository holds these blobs, fetching the missing ones in one request.

        Returns how many were fetched. Raises `StoreError` if some are missing and the
        repository is not a partial clone, which has nowhere to fetch a single blob from.
        """
        wanted = sorted(set(names))
        if not wanted:
            return 0
        answered = self.git(
            "cat-file", "--batch-check", given="\n".join(wanted).encode("ascii") + b"\n", lazy=False
        ).decode("ascii")
        missing = [line.split(" ")[0] for line in answered.split("\n") if line.endswith(" missing")]
        if not missing:
            return 0
        remote = self.promisor()
        if remote is None:
            raise StoreError(
                f"{self.path} lacks {len(missing)} blobs and is not a partial clone that can fetch them"
            )
        self.git(
            "-c",
            "fetch.negotiationAlgorithm=noop",
            "fetch",
            remote,
            "--no-tags",
            "--no-write-fetch-head",
            "--recurse-submodules=no",
            "--filter=blob:none",
            "--stdin",
            given="\n".join(missing).encode("ascii") + b"\n",
        )
        return len(missing)

    def blob(self, name: str) -> bytes:
        """The bytes of one blob."""
        return self.git("cat-file", "blob", name)


def blob_name(data: bytes) -> str:
    """The name git gives a blob holding these bytes: SHA-1 of a header and the bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def tree_names(root: Path) -> dict[str, str]:
    """Every file under a folder by its path from it, with the name git would give its blob."""
    return {
        path.relative_to(root).as_posix(): blob_name(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def lay_out(repository: Repository, root: Path, wanted: Mapping[str, str]) -> int:
    """Make a folder hold exactly these files, writing only those it does not hold already.

    Returns the number of files written. A folder kept from one case to the next shares
    most of its files with the next, so most of a store is laid out once.
    """
    held = tree_names(root) if root.is_dir() else {}
    for path in held:
        if path not in wanted:
            (root / path).unlink()
    needed = {path: name for path, name in wanted.items() if held.get(path) != name}
    repository.fetch(list(needed.values()))
    for path, name in needed.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(repository.blob(name))
    return len(needed)


@dataclass(frozen=True)
class Step:
    """One command that was run: an `add`, a `change` naming IDs, or a `change` from a changes file."""

    #: The batch file, as a path in the commit that holds the inputs, for an add.
    batch: str | None
    #: The batch folder as the command was given it: `added_in`, or a change's `inputs`.
    batch_dir: str
    #: The IDs a change names, in order.
    ids: tuple[str, ...] = ()
    review: str = ""
    date: str = ""
    #: The changes file, as a path in the commit that holds the inputs, for a change from one.
    changes: str | None = None
    #: The changes file where the expectation keeps it, for one the data repository does not hold.
    changes_file: Path | None = None


@dataclass(frozen=True)
class Case:
    """One write to replay, and what replaying it has to amount to.

    For a merged pull request, `merge` holds the inputs and the files to arrive at, and
    `parent` the store to start from. For a write the TypeScript recorded, both are the
    one commit it was run on, and `left` is what to arrive at.
    """

    name: str
    merge: str
    parent: str
    steps: tuple[Step, ...]
    #: Records the adds add, entries the changes record, and store files compared.
    added: int
    changed: int
    files: int
    #: Every store file the TypeScript left, with its blob name, where it recorded them.
    left: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Expectation:
    """The cases, and the paths taken from the merge commit beside the parent's store."""

    configuration: tuple[str, ...]
    cases: tuple[Case, ...]


def _member[T](held: Mapping[str, JsonValue], name: str, kind: type[T], where: str) -> T:
    value = held.get(name)
    if not isinstance(value, kind) or isinstance(value, bool):
        raise StoreError(f"{where} has no {kind.__name__} {name!r}")
    return value


def _texts(value: JsonValue, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StoreError(f"{where} is not a list of text")
    return tuple(cast("list[str]", value))


def _commit_name(held: Mapping[str, JsonValue], name: str, where: str) -> str:
    value = _member(held, name, str, where)
    if len(value) != _NAME_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise StoreError(f"{where} names its {name} as {value!r}, which is not a full commit name")
    return value


def _step(value: JsonValue, where: str, folder: Path) -> Step:
    """One step of a case. `folder` is where the expectation is, which `changes_here` is a path from."""
    if not isinstance(value, dict):
        raise StoreError(f"{where} is not an object")
    batch_dir = _member(value, "batch_dir", str, where)
    if "add" in value:
        return Step(_member(value, "add", str, where), batch_dir)
    review, date = _member(value, "review", str, where), _member(value, "date", str, where)
    if "changes_here" in value:
        kept = folder / _member(value, "changes_here", str, where)
        return Step(None, batch_dir, (), review, date, None, kept)
    if "changes" in value:
        return Step(None, batch_dir, (), review, date, _member(value, "changes", str, where))
    return Step(None, batch_dir, _texts(value.get("change"), f"{where}'s change"), review, date)


def _blob_names(value: JsonValue, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(name, str) for name in value.values()):
        raise StoreError(f"{where} is not an object of blob names by file")
    return cast("dict[str, str]", value)


def read_cases(path: Path) -> Expectation:
    """An expectation file: what to replay, with what commands, to what counts.

    A case that names a `merge` and its `parent` is a merged pull request. One that names
    a `store` is a write the TypeScript recorded on that commit, with the files it `left`.
    A step's `changes` is a changes file in the commit, and its `changes_here` is one kept
    with the expectation, as a path from the folder the expectation is in.
    """
    try:
        held = parse_row(path.read_text("utf-8"), str(path))
    except (OSError, UnicodeDecodeError) as error:
        raise StoreError(f"{path} cannot be read: {error}") from error
    cases: list[Case] = []
    for number, value in enumerate(_member(held, "cases", list, str(path)), 1):
        where = f"case {number} of {path}"
        if not isinstance(value, dict):
            raise StoreError(f"{where} is not an object")
        steps = _member(value, "steps", list, where)
        recorded = "store" in value
        cases.append(
            Case(
                _member(value, "name", str, where),
                _commit_name(value, "store" if recorded else "merge", where),
                _commit_name(value, "store" if recorded else "parent", where),
                tuple(_step(step, f"step {at} of {where}", path.parent) for at, step in enumerate(steps, 1)),
                _member(value, "added", int, where),
                _member(value, "changed", int, where),
                _member(value, "files", int, where),
                _blob_names(value.get("left"), f"what {where} left") if recorded else None,
            )
        )
    if not cases:
        raise StoreError(f"{path} lists no cases")
    return Expectation(_texts(held.get("configuration"), f"the configuration of {path}"), tuple(cases))


def _lines_by_id(data: bytes, where: str) -> dict[str, str]:
    """The lines of a record file by the ID each holds."""
    lines: dict[str, str] = {}
    for number, line in enumerate(data.decode("utf-8").split("\n"), 1):
        if line:
            lines[text_field(parse_row(line, f"{where}:{number}"), "id", f"{where}:{number}")] = line
    return lines


def edit_in_place(repository: Repository, merge: str, root: Path, ids: Sequence[str]) -> None:
    """Give each named record the line the merge commit holds for it, where its line now stands.

    This is the edit a person made before running `records change`. Raises `InputError`
    for an ID the store as laid out does not hold, or one the merge commit's file lacks.
    """
    wanted = set(ids)
    held: dict[Path, dict[str, int]] = {}
    for file in record_files(root):
        for at, line in enumerate(read_lines(file)):
            record_id = text_field(parse_row(line, f"{file}:{at + 1}"), "id", f"{file}:{at + 1}")
            if record_id in wanted:
                held.setdefault(file, {})[record_id] = at
    found = {record_id for places in held.values() for record_id in places}
    if found != wanted:
        raise InputError(f"the store has no record {min(wanted - found)} to edit in place")
    if not held:
        return
    tree = repository.tree(merge, STORE_PATHS)
    repository.fetch([tree[file.relative_to(root).as_posix()] for file in held])
    for file, places in held.items():
        path = file.relative_to(root).as_posix()
        merged = _lines_by_id(repository.blob(tree[path]), f"{path} at {merge}")
        lines = read_lines(file)
        for record_id, at in places.items():
            if record_id not in merged:
                raise InputError(f"{path} at {merge} has no record {record_id}")
            lines[at] = merged[record_id]
        write_text(file, join_lines(lines))


@dataclass
class CaseReport:
    """What replaying one case came to."""

    name: str
    added: int = 0
    changed: int = 0
    files: int = 0
    #: Store files written by the commands.
    written: int = 0
    problems: list[str] = field(default_factory=list)


def _lines(data: bytes) -> list[bytes]:
    lines = data.split(b"\n")
    return lines if lines[-1] else lines[:-1]


def _shown(line: bytes, at: int) -> str:
    """The start of a line, which names its row, and the stretch around a place in it."""
    start = max(_SHOWN, at - _SHOWN // 2)
    head = line[:_SHOWN].decode("utf-8", errors="replace")
    around = line[start : at + _SHOWN].decode("utf-8", errors="replace")
    return f"{head} ... {around}" if at >= _SHOWN else head


def _first_difference(got: bytes, wanted: bytes) -> str:
    ours, theirs = _lines(got), _lines(wanted)
    for number, (mine, yours) in enumerate(zip(ours, theirs, strict=False), 1):
        if mine != yours:
            shared = zip(mine, yours, strict=False)
            at = next((place for place, (a, b) in enumerate(shared) if a != b), min(len(mine), len(yours)))
            return (
                f"line {number} differs from byte {at}: it is {_shown(mine, at)} "
                f"and the merge commit holds {_shown(yours, at)}"
            )
    if len(ours) == len(theirs):
        return "its lines are the merge commit's, and it ends differently: one has a final newline"
    return f"it has {len(ours)} lines and the merge commit's has {len(theirs)}"


def compare(
    wanted: Mapping[str, str], root: Path, oracle: str, repository: Repository | None = None
) -> tuple[int, list[str]]:
    """Hold the store files under a folder to the blob names an oracle gives, file for file.

    `oracle` is what to call it in a problem. Given the repository that holds the wanted
    blobs, a file that differs is reported with the first line that does. Returns the number
    of files the two have between them and what differs.
    """
    got = {
        path: name
        for path, name in tree_names(root).items()
        if path.startswith(tuple(f"{p}/" for p in STORE_PATHS))
    }
    problems: list[str] = []
    for path in sorted(set(wanted) | set(got)):
        if path not in got:
            problems.append(f"{path} is in {oracle} and was not written")
        elif path not in wanted:
            problems.append(f"{path} was written and is not in {oracle}")
        elif got[path] != wanted[path]:
            differs = f"{path} is blob {got[path]}, not {wanted[path]}"
            if repository is None:
                problems.append(f"{differs} as in {oracle}")
            else:
                repository.fetch([wanted[path]])
                held = repository.blob(wanted[path])
                problems.append(f"{differs}: {_first_difference((root / path).read_bytes(), held)}")
    return len(set(wanted) | set(got)), problems


def _input(repository: Repository, commit: str, path: str, file: Path) -> Path:
    """One input of a command, taken from the commit that holds it and written to a file."""
    listed = repository.tree(commit, [path])
    if path not in listed:
        raise InputError(f"{commit} has no {path}")
    file.write_bytes(repository.blob(listed[path]))
    return file


def _planned(repository: Repository, case: Case, step: Step, scratch: Path) -> Plan:
    """What one step would write: its inputs taken from the commit that holds them, then the plan.

    Raises `InputError` if the commit lacks an input, and whatever the command refuses with.
    """
    root = scratch / "tree"
    if step.batch is not None:
        batch_file = _input(repository, case.merge, step.batch, scratch / "batch.jsonl")
        return plan_add(batch_file, step.batch_dir, root)
    if step.changes is not None:
        changes_file = _input(repository, case.merge, step.changes, scratch / "changes.jsonl")
        return plan_changes_file(str(changes_file), step.batch_dir, step.review, root, step.date)
    if step.changes_file is not None:
        if not step.changes_file.is_file():
            raise InputError(f"there is no changes file {step.changes_file}")
        return plan_changes_file(str(step.changes_file), step.batch_dir, step.review, root, step.date)
    edit_in_place(repository, case.merge, root, step.ids)
    return plan_change(step.ids, step.batch_dir, step.review, root, step.date)


def replay_case(
    repository: Repository, case: Case, configuration: Sequence[str], scratch: Path
) -> CaseReport:
    """Replay one case in a scratch folder and hold the result to its oracle.

    `scratch` may hold the tree of an earlier case, which is reused. A command that
    refuses, or a result that is not the oracle's, is a problem in the report. Raises
    `StoreError` where the replay could not be made: git cannot give what the case names,
    or the commit lacks a batch, a changes file or a record the case says it has.
    """
    report = CaseReport(case.name)
    if case.left is None:
        parent = repository.commit(f"{case.merge}^1")
        if parent != case.parent:
            report.problems.append(f"the first parent of {case.merge} is {parent}, not {case.parent}")
            return report
    lay_out(
        repository,
        scratch / "tree",
        {**repository.tree(case.merge, configuration), **repository.tree(case.parent, STORE_PATHS)},
    )
    for number, step in enumerate(case.steps, 1):
        try:
            plan = _planned(repository, case, step, scratch)
            report.written += len(write(plan))
        except InputError:
            raise
        except (StoreError, VocabularyError, RecordError, SchemaError) as error:
            report.problems.append(f"step {number} was refused: {type(error).__name__}: {error}")
            return report
        if step.batch is None:
            report.changed += len(plan.ids)
        else:
            report.added += len(plan.ids)
    if case.left is None:
        wanted = repository.tree(case.merge, STORE_PATHS)
        report.files, differences = compare(wanted, scratch / "tree", "the merge commit", repository)
    else:
        report.files, differences = compare(case.left, scratch / "tree", "what the TypeScript left")
    report.problems.extend(differences)
    for name in ("added", "changed", "files"):
        if getattr(report, name) != getattr(case, name):
            report.problems.append(
                f"{name} is {getattr(report, name)}, and {getattr(case, name)} is expected"
            )
    return report


@dataclass
class ReplayReport:
    """What one run of the write gate measured."""

    cases: list[CaseReport] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        """Every problem of every case, each named by its case."""
        return [f"{case.name}: {problem}" for case in self.cases for problem in case.problems]

    @property
    def reproduced(self) -> int:
        """Cases whose every store file is the merge commit's."""
        return sum(not case.problems for case in self.cases)

    def total(self, name: str) -> int:
        """One count summed over the cases: `added`, `changed`, `files` or `written`."""
        return sum(cast("int", getattr(case, name)) for case in self.cases)


def case_line(case: CaseReport) -> str:
    """One case as a line of the gate's output."""
    verdict = "reproduces" if not case.problems else "DOES NOT REPRODUCE"
    return (
        f"{case.name}: {case.added:,} added, {case.changed:,} changes recorded, "
        f"{case.written} files written, {case.files} compared: {verdict}"
    )


def to_json(report: ReplayReport) -> str:
    """What was measured, for CI to hold to its pins."""
    cases: list[JsonValue] = [
        cast(
            "JsonObject",
            {
                "name": case.name,
                "added": case.added,
                "changed": case.changed,
                "files": case.files,
                "written": case.written,
                "problems": list(case.problems),
            },
        )
        for case in report.cases
    ]
    return js_indented(
        {
            "cases": len(report.cases),
            "reproduced": report.reproduced,
            "records_added": report.total("added"),
            "changes_recorded": report.total("changed"),
            "files_compared": report.total("files"),
            "files_written": report.total("written"),
            "results": cases,
            "problems": cast("list[JsonValue]", report.problems),
        }
    )
