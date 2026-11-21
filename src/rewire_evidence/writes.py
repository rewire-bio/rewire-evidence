"""The write path: `records add` and both forms of `records change`, from `scripts/omics/records.ts`.

Three functions decide what a write leaves on disk and write nothing:

- `plan_add` is `addBatch`. It loads the store through every check `loadRecords` makes,
  parses the batch, refuses an ID the store or the batch already holds, gives each new
  record a provenance row, and runs the validators in the order the TypeScript runs them
  for a batch, which is not the order `loadRecords` runs them in: vocabularies and
  attributes over the additions alone, then descriptions, derivations, concerns and
  `validateRecords` over the store followed by the additions.
- `plan_change` is `recordChanges`, for records edited in place beforehand. It takes the
  digest of each named record as it now stands and adds one entry to its provenance row.
  It validates nothing, as the TypeScript validates nothing there, and a name given twice
  gets two entries.
- `plan_changes_file` is `applyChanges`, for a changes file (rdf-kg-mcp `draft_export`).
  Each line is checked against the stored record and its provenance row, and every line is
  checked before anything is decided.

Each returns a `Plan`: the whole body of every file `writeStore` would write. `writeStore`
writes every file that receives a row, changed or not, and a record or a provenance row is
written from its parsed form. So a file whose lines are not as `JSON.stringify` writes them
is rewritten even when no record in it was added or changed, and the plan holds that file
too. `write` puts the plan on disk, writing only the files whose bytes differ, which leaves
the disk as the TypeScript leaves it.

`write` takes a check to run once the files are written. If the check or a write fails,
every file is put back as it was and a file the write created is removed, which is
`writeCheckedAgainstShapes`. `shapes_check` is the check the TypeScript runs: the SHACL
shapes over the records written, the records that link to them and their targets, through
the data repository's own `npm run kg -- subset` and `npm run kg:build -- --shapes-only`.
Those two are not ported yet, so the check needs a data repository checkout with its
dependencies installed.

A provenance row is written with the members it was read with, in their order. A change
sets `sha256` where it stands and adds to the end of `changed_by`.
"""

from __future__ import annotations

import datetime as dt
import difflib
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rewire_evidence.attributes import JsTypeError, said, validate_attributes
from rewire_evidence.canonical import JsonObject, JsonValue, js_utf8, js_written, sha256_hex, utf16_key
from rewire_evidence.concerns import validate_concerns
from rewire_evidence.derivations import validate_derivations
from rewire_evidence.descriptions import validate_descriptions
from rewire_evidence.js import MISSING, truthy
from rewire_evidence.jsonl import read_values
from rewire_evidence.records import load_records
from rewire_evidence.schema import Record, RecordError, SchemaError, parse_record
from rewire_evidence.store import read_provenance, record_files, store_bodies
from rewire_evidence.validation import validate_records
from rewire_evidence.vocab import load_schemes, read_mapping, validate_vocabularies

#: What `records add` writes as the step that added a record.
ADDED_BY = "records:add"
#: What both forms of `records change` write as the step of a change.
CHANGE_STEP = "records:change"

#: Runs once the files are written, with the IDs written. Raising undoes the write.
type Check = Callable[[Sequence[str]], None]
#: Runs a command in a folder and returns its exit status.
type Runner = Callable[[Sequence[str], Path], int]


class ShapesError(Exception):
    """The shapes check did not pass, or could not be run. The write it followed is undone."""


@dataclass(frozen=True)
class Change:
    """One file a write would alter: its bytes on disk, or None for no file, and its new bytes."""

    file: Path
    before: bytes | None
    after: bytes


@dataclass(frozen=True)
class Plan:
    """What a write leaves on disk, decided and not yet written."""

    root: Path
    #: The IDs added or changed, in the order they were given, repeats included.
    ids: tuple[str, ...]
    #: Every store file that receives a row, whole, as `writeStore` writes it.
    bodies: Mapping[Path, bytes]

    def changes(self) -> list[Change]:
        """The files whose bytes on disk are not the planned ones, in the order of their paths."""
        changes: list[Change] = []
        for file in sorted(self.bodies, key=lambda path: utf16_key(path.as_posix())):
            before = file.read_bytes() if file.exists() else None
            if before != self.bodies[file]:
                changes.append(Change(file, before, self.bodies[file]))
        return changes


def today(now: dt.datetime | None = None) -> str:
    """The date a change is recorded under: `new Date().toISOString().slice(0, 10)`.

    That is the date in UTC, whatever the clock of the machine says. `now` is the moment to
    take it at, with its time zone, and the present by default.
    """
    moment = dt.datetime.now(dt.UTC) if now is None else now.astimezone(dt.UTC)
    return moment.date().isoformat()


def record_digest(record: Record) -> str:
    """`recordSha256(record)`: the digest of the record as `JSON.stringify` writes it."""
    return sha256_hex(js_written(record.row()))


def _parsed(value: JsonValue, where: str) -> Record:
    """`recordSchema.parse(value)`, with the place the value came from noted on a refusal."""
    try:
        return parse_record(value)
    except SchemaError as error:
        error.add_note(where)
        raise


def _planned(
    root: Path, ids: Sequence[str], records: Sequence[Record], provenance: Mapping[str, JsonObject]
) -> Plan:
    bodies = store_bodies((record.row() for record in records), provenance.values(), root)
    return Plan(root, tuple(ids), {file: body.encode("utf-8") for file, body in bodies.items()})


def plan_add(batch_file: Path, batch_dir: str, root: Path, configuration: Path | None = None) -> Plan:
    """`addBatch`: the store with a reviewed batch of new records added to it.

    `batch_dir` is written as each new record's `added_in`, exactly as given.
    `configuration` is the checkout whose mapping and vocabularies the records are held to,
    and the store's own root by default, as for `records.load_records`.

    Raises whatever `load_records` raises for the store as it stands, `StoreError` for a
    batch that cannot be read or a line that is not JSON, `SchemaError` for a line that is
    not a record, with the line noted, `RecordError` for an ID the store or an earlier line
    already holds, and then what each validator raises. Nothing is written.
    """
    existing = load_records(root, configuration)
    ids = {record.id for record in existing}
    provenance = read_provenance(root)
    additions = [
        _parsed(value, f"{batch_file}:{number}") for number, value in enumerate(read_values(batch_file), 1)
    ]
    for record in additions:
        if record.id in ids:
            raise RecordError(f"Batch cannot replace or duplicate record: {record.id}")
        ids.add(record.id)
        provenance[record.id] = {
            "id": record.id,
            "sha256": record_digest(record),
            "added_in": batch_dir,
            "added_by": ADDED_BY,
            "changed_by": [],
        }
    settings = root if configuration is None else configuration
    validate_vocabularies(
        read_mapping(settings), (record.row() for record in additions), load_schemes(settings)
    )
    validate_attributes(additions)
    together = [*existing, *additions]
    validate_descriptions(together)
    validate_derivations(together, root)
    validate_concerns(together)
    validate_records(together)
    return _planned(root, [record.id for record in additions], together, provenance)


def _records_as_filed(root: Path) -> dict[str, Record]:
    """Every record by ID, as the two forms of change read them: parsed, and checked no further.

    The TypeScript builds a `Map` from the lines in file order, so of two lines with one ID
    the later is the record and the earlier's place is where it stands. A dict does the same.
    """
    records: dict[str, Record] = {}
    for file in record_files(root):
        for number, value in enumerate(read_values(file), 1):
            record = _parsed(value, f"{file}:{number}")
            records[record.id] = record
    return records


def _record_change(row: JsonObject, record_id: str, digest: str, entry: JsonObject) -> None:
    """Set a provenance row's digest and add one entry to the end of its changes.

    The TypeScript calls `row.changed_by.push`, which fails with a `TypeError` on a row
    whose `changed_by` is not a list.
    """
    row["sha256"] = digest
    changed_by = row.get("changed_by")
    if not isinstance(changed_by, list):
        raise JsTypeError(f"the provenance row of {record_id} has no list of changes to add to")
    changed_by.append(dict(entry))


def _entry(inputs: str, review: str, date: str | None) -> JsonObject:
    return {
        "step": CHANGE_STEP,
        "inputs": inputs,
        "date": today() if date is None else date,
        "review": review,
    }


def plan_change(ids: Sequence[str], inputs: str, review: str, root: Path, date: str | None = None) -> Plan:
    """`recordChanges`: the store with a reviewed change to records edited in place recorded.

    Each named record's provenance row is given the digest of the record as it now stands
    and one more entry: the step, `inputs`, the date and `review`. `date` is the day to
    record, and today in UTC by default.

    Nothing about the records is validated, as in the TypeScript: `records check` is what
    holds an edited record to the rules. Raises `StoreError` for a provenance row in the
    wrong shard or a line that is not JSON, `SchemaError` for a line that is not a record,
    `RecordError` naming an ID that has no record or no provenance row, and `JsTypeError`
    for a row with no list of changes. Nothing is written.
    """
    provenance = read_provenance(root)
    records = _records_as_filed(root)
    entry = _entry(inputs, review, date)
    for record_id in ids:
        record = records.get(record_id)
        row = provenance.get(record_id)
        if record is None or row is None:
            raise RecordError(f"Unknown record: {record_id}")
        _record_change(row, record_id, record_digest(record), entry)
    return _planned(root, ids, list(records.values()), provenance)


def _text_member(line: JsonObject, name: str) -> str | None:
    value = line.get(name)
    return value if isinstance(value, str) else None


def plan_changes_file(  # noqa: C901 - one refusal after another, in the TypeScript's order
    changes_file: Path | str, inputs: str, review: str, root: Path, date: str | None = None
) -> Plan:
    """`applyChanges`: the store with a reviewed changes file applied and recorded.

    Each line is `{id, base_sha256, sha256, record}`: the whole changed record, the digest
    its provenance row must hold now, and the digest of the record as the store writes it.
    A change made against an older line is refused, and so is a record whose bytes would
    not be the reviewed ones. Every line is checked before anything is decided.

    The file is named in a refusal as it was given, so pass the text of the argument and
    not a path made from it. Raises `StoreError` for a file that cannot be read or a line
    that is not JSON, `RecordError` in the TypeScript's words for a line it refuses,
    `SchemaError` for a record that is not one, and `JsTypeError` for a provenance row with
    no list of changes. Nothing is written.
    """
    lines = read_values(Path(changes_file))
    if not lines:
        raise RecordError(f"{changes_file} holds no changes")
    provenance = read_provenance(root)
    records = _records_as_filed(root)
    accepted: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, 1):
        where = f"{changes_file} line {number}"
        record_id = _text_member(line, "id") if isinstance(line, dict) else None
        base = _text_member(line, "base_sha256") if isinstance(line, dict) else None
        digest = _text_member(line, "sha256") if isinstance(line, dict) else None
        changed = line.get("record") if isinstance(line, dict) else None
        if record_id is None or base is None or digest is None or not truthy(changed):
            raise RecordError(f"{where}: expected {{id, base_sha256, sha256, record}}")
        if record_id in seen:
            raise RecordError(f"{where}: {record_id} is changed twice")
        seen.add(record_id)
        stored = records.get(record_id)
        row = provenance.get(record_id)
        if stored is None or row is None:
            raise RecordError(f"{where}: unknown record {record_id}; records add adds new records")
        held = row.get("sha256", MISSING)
        if held != base:
            raise RecordError(
                f"{where}: {record_id} has provenance digest {said(held)}, not base_sha256 {base}. "
                "The record changed after this change was made; redo the change against the current record."
            )
        if record_digest(stored) != held:
            raise RecordError(f"{where}: {record_id} was edited in place without a recorded change")
        following = _parsed(changed, where)
        if following.id != record_id:
            raise RecordError(f"{where}: the record's id is {following.id}, not {record_id}")
        if following.kind != stored.kind:
            raise RecordError(
                f"{where}: {record_id} is a {stored.kind}; a change cannot make it a {following.kind}"
            )
        written = record_digest(following)
        if written != digest:
            raise RecordError(
                f"{where}: {record_id} as the store writes it has SHA-256 {written}, not {digest}; "
                "its bytes would differ from the reviewed line"
            )
        if written == held:
            raise RecordError(f"{where}: {record_id} is unchanged")
        records[record_id] = following
        accepted.append((record_id, digest))
    entry = _entry(inputs, review, date)
    for record_id, digest in accepted:
        _record_change(provenance[record_id], record_id, digest, entry)
    return _planned(root, [record_id for record_id, _ in accepted], list(records.values()), provenance)


def _put(file: Path, data: bytes) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)


class RestoreError(Exception):
    """A write failed or was refused, and the store could not be put back as it was."""


def _restore(changes: Sequence[Change]) -> list[str]:
    """Put every file back as it was, and remove a file that was not there.

    Every file is tried, whatever happens to the one before it. Returns the files that
    could not be put back, each with the reason.
    """
    failed: list[str] = []
    for change in changes:
        try:
            if change.before is not None:
                _put(change.file, change.before)
            elif change.file.exists():
                change.file.unlink()
        except OSError as error:
            failed.append(f"{change.file}: {error}")
    return failed


def write(plan: Plan, check: Check | None = None) -> list[Change]:
    """Put a plan on disk, then run the check, and undo the write if either fails.

    Only the files whose bytes differ are written. `check` is given the IDs written, once
    every file is in place, and refuses by raising. Whatever is raised, by a write or by the
    check, every file is put back as it was first and the error is then raised again, so the
    store is either as planned and checked or unchanged. Returns the files written.

    Raises `RestoreError` from the first error if a file could not be put back, which is
    the one case in which the store is neither.
    """
    changes = plan.changes()
    started: list[Change] = []
    try:
        for change in changes:
            started.append(change)
            _put(change.file, change.after)
        if check is not None:
            check(plan.ids)
    except BaseException as error:
        failed = _restore(started)
        if failed:
            raise RestoreError(
                f"{error}. The store was then not put back as it was: {'; '.join(failed)}"
            ) from error
        raise
    return changes


def run_command(command: Sequence[str], folder: Path) -> int:
    """Run a command in a folder, its output going where this process's goes. Returns its status."""
    return subprocess.run(command, cwd=folder, check=False).returncode  # noqa: S603 - the commands are the two below


def shapes_check(root: Path, run: Runner = run_command) -> Check:
    """The check `records add` and `records change` make of what they wrote: the SHACL shapes.

    The records written, the records that link to them and their link targets are exported
    by `npm run kg -- subset` and held to the shapes by `npm run kg:build -- --shapes-only`,
    both run in `root`, which has to be a data repository checkout with its dependencies
    installed. The whole store is not checked: that takes minutes and runs in the data
    repository's CI. The check raises `ShapesError` if either command fails or cannot be
    run.
    """

    def check(ids: Sequence[str]) -> None:
        with tempfile.TemporaryDirectory(prefix="records-shapes-") as scratch:
            listed = Path(scratch) / "ids.txt"
            listed.write_bytes(js_utf8("\n".join(ids) + "\n"))
            context = Path(scratch) / "context.txt"
            commands = (
                ["npm", "run", "-s", "kg", "--", "subset", str(listed), scratch],
                [
                    "npm",
                    "run",
                    "-s",
                    "kg:build",
                    "--",
                    "--shapes-only",
                    "--kg-dir",
                    scratch,
                    "--context",
                    str(context),
                ],
            )
            for command in commands:
                try:
                    status = run(command, root)
                except OSError as error:
                    raise ShapesError(
                        f"`{' '.join(command[:4])}` could not be run in {root}: {error}"
                    ) from error
                if status:
                    raise ShapesError(f"`{' '.join(command[:4])}` exited with status {status}")

    return check


#: What stands for the end of a file that does not end in a newline, as git shows it.
NO_FINAL_NEWLINE = "\\ No newline at end of file"


def _lines(data: bytes | None) -> list[str]:
    """A file's lines for showing, split on the newline only, as the store's readers split them.

    Blank lines are kept, because a write removes them and that is a change to show. A
    file that does not end in a newline is given a last line that says so.
    """
    if not data:
        return []
    lines = data.decode("utf-8", errors="replace").split("\n")
    if lines.pop():
        lines.extend([data.decode("utf-8", errors="replace").split("\n")[-1], NO_FINAL_NEWLINE])
    return lines


def diff(changes: Sequence[Change], root: Path) -> list[str]:
    """The changed lines of each file a write would alter, as a unified diff with no context.

    A store file holds one row to a line, sorted by ID, so a record added is one line added
    and a record or provenance row changed is one line removed and one added. A file the
    write would create is compared with `/dev/null`.
    """
    shown: list[str] = []
    for change in changes:
        name = change.file.relative_to(root).as_posix()
        shown.extend(
            difflib.unified_diff(
                _lines(change.before),
                _lines(change.after),
                "/dev/null" if change.before is None else f"a/{name}",
                f"b/{name}",
                n=0,
                lineterm="",
            )
        )
    return shown
