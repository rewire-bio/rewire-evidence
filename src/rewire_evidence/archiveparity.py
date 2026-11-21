"""Hold the archive read side to what the TypeScript makes of the pinned checkout.

The data repository commits the manifest, the receipts, the licence map and the candidates,
and P1 already writes each of those files back byte for byte. What no committed file says is
what the TypeScript reads out of them: which rows its schemas accept, the tier its classifier
gives each object, the work list, the triage queue, the reclassification plan, and what its
commands print. So the oracle is a recording. `scripts/fixtures/record_archive_fixture.mjs`
runs the TypeScript in CI over the pinned checkout and writes one line for each thing read.

The gate writes the same lines from a checkout and requires each. It also requires every
manifest row, receipt and candidate, once its schema has read it, to be written back as the
line the checkout commits; the work list summary and the reclassification plan as the
TypeScript holds them; each command's output and exit status; and each TypeScript file the
port was made from to be the file the recording was made from.

The recording was made of a checkout with no object cache, so `verify` and `status` report
every object as missing. A checkout that has a cache is not what was recorded, and the gate
says so and does not run.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from rewire_evidence import archive, archivesaid, licence, reclassify, worklist, zod
from rewire_evidence.archivesaid import Said
from rewire_evidence.canonical import JsonObject, JsonValue, canonical_line, js_stringify, js_utf8, sha256_hex
from rewire_evidence.checkparity import Lines, compare_lines, read_dump
from rewire_evidence.conformance import differences, recorded_ref
from rewire_evidence.families import AUDIT_DIR, OBJECT_SHARDS, manifest_shard_path
from rewire_evidence.jsonl import StoreError, parse_value
from rewire_evidence.schema import Issue

_FETCHED = "-archive-fetch.receipts.jsonl"
_RESOLVED = "-licence-resolve.receipts.jsonl"
#: A date written YYYY-MM-DD, which a receipts file's name starts with.
_DATE_LENGTH = 10


@dataclass
class Written:
    """Rows of one family a schema read, and how many are written back as the committed line."""

    read: int = 0
    refused: int = 0
    same: int = 0

    def row(self) -> JsonObject:
        """The three counts by name."""
        return {"read": self.read, "refused": self.refused, "same": self.same}


@dataclass
class ArchiveReport:
    """What the archive gate found."""

    store_ref: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    lines: Lines = field(default_factory=Lines)
    #: Lines made here of each kind.
    kinds: dict[str, int] = field(default_factory=dict)
    #: Rows read by each schema and written back.
    written: dict[str, Written] = field(default_factory=dict)
    #: Commands the TypeScript ran at the pinned commit, and those that print and leave the same.
    commands: Lines = field(default_factory=Lines)
    #: TypeScript files the port was made from, and those that are the files recorded.
    modules: Lines = field(default_factory=Lines)
    #: The committed triage queues that are the queue as it is generated today.
    committed_queues: list[str] = field(default_factory=list)
    #: Rows of the committed queues, and those that are rows of the queue generated today.
    queue_rows: Lines = field(default_factory=Lines)
    problems: list[str] = field(default_factory=list)


def _digest(text: str) -> str:
    return sha256_hex(js_utf8(text))


def _issues(issues: Sequence[zod.Raised] | Sequence[Issue]) -> JsonObject:
    return {"issues": [[issue.code, list(issue.path), issue.message] for issue in issues]}


class _Made:
    """The lines being made, and the report that counts them."""

    def __init__(self, found: ArchiveReport) -> None:
        self.lines: list[bytes] = []
        self.found = found

    def push(self, kind: str, *held: JsonValue) -> None:
        self.lines.append(js_utf8(js_stringify([kind, *held])))
        self.found.kinds[kind] = self.found.kinds.get(kind, 0) + 1

    def family(  # noqa: PLR0913, PLR0917 - a family is its name, files and schema, and what a line says
        self,
        kind: str,
        name: str,
        files: Sequence[tuple[str, Path]],
        schema: zod.Schema,
        lead: Callable[[JsonValue], list[JsonValue]],
        more: Callable[[JsonObject], list[JsonValue]],
    ) -> None:
        """A line for each row of a family of files: what its schema refuses it for, or the
        digest of the line written back from what the schema read, and what `more` adds.

        A row the schema read is counted as written back when that line is the committed one.
        """
        tally = self.found.written.setdefault(name, Written())
        for label, path in files:
            for number, line in enumerate(archive.raw_lines(path), 1):
                if archive.blank(line):
                    continue
                given = parse_value(line, f"{path}:{number}")
                payload = schema.run(given)
                tally.read += 1
                if payload.issues:
                    tally.refused += 1
                    self.push(kind, label, number, *lead(given), _issues(payload.issues))
                    continue
                parsed = cast("JsonObject", payload.value)
                written = canonical_line(parsed)
                tally.same += written == line
                self.push(kind, label, number, *lead(given), _digest(written), *more(parsed))


def _rebuilt(candidate: JsonObject, held: licence.LicenceMap) -> JsonValue:
    """A candidate built again from its own observations: the digest of its line, or the refusal."""
    try:
        again = licence.build_candidate(candidate, cast("list[JsonObject]", candidate["observations"]), held)
    except archive.Refused as error:
        return {"error": {"zod": _issues(error.issues)["issues"]}}
    return None if again is None else _digest(canonical_line(again))


def _audits(root: Path, ending: str) -> list[tuple[str, Path]]:
    folder = root / AUDIT_DIR
    names = sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []
    dated = [name for name in names if name.endswith(ending) and len(name) == len(ending) + _DATE_LENGTH]
    return [(f"{AUDIT_DIR}/{name}", folder / name) for name in dated]


def _key_of(given: JsonValue) -> list[JsonValue]:
    return [given.get("sha256") if isinstance(given, dict) else None]


def _nothing(_: JsonValue) -> list[JsonValue]:
    return []


def archive_lines(root: Path, date: str, found: ArchiveReport) -> list[bytes]:
    """One line for each thing the read side reads out of a checkout, as the recorder writes them."""
    made = _Made(found)
    held = licence.read_licence_map(root)
    for name, row in held.items():
        made.push("licence", name, row)

    licences = licence.read_source_licences(root)
    work = worklist.read_work_list(root)
    items = cast("list[JsonObject]", work["items"])
    without = cast("list[JsonObject]", work["withoutUrl"])
    with_url = {item["source_id"]: item for item in items}
    with_none = {item["source_id"]: item for item in without}
    queue = licence.build_triage(root)
    queued = {row["source_id"]: row for row in queue}
    for source in licences:
        determined = licence.determine_for_source(source, held, licences)
        made.push(
            "source", source, with_url.get(source), with_none.get(source), determined, queued.get(source)
        )

    candidates = licence.read_candidates(root)
    classify = licence.licence_classifier(root)

    def read(row: JsonObject) -> list[JsonValue]:
        sources = cast("list[str]", row["source_ids"])
        reading = reclassify.read_row(sources, held, licences, candidates)
        evidence: JsonValue = [source["evidence"] for source in cast("list[JsonObject]", reading["sources"])]
        return [
            classify(sources),
            [reading["archive_tier"], reading["observed_licence"], reading["evidenced"], evidence],
        ]

    shards = [(shard, manifest_shard_path(shard, root)) for shard in OBJECT_SHARDS]
    held_shards = [(shard, path) for shard, path in shards if path.exists()]
    made.family("row", "manifest_rows", held_shards, archive.MANIFEST_ROW, _key_of, read)
    made.family("receipt", "receipts", _audits(root, _FETCHED), archive.FETCH_RECEIPT, _nothing, _nothing)
    resolved = _audits(root, _RESOLVED)
    made.family("resolve-receipt", "resolve_receipts", resolved, licence.RESOLVE_RECEIPT, _nothing, _nothing)
    candidate_file = [(licence.CANDIDATES, root / licence.CANDIDATES)]
    made.family(
        "candidate",
        "candidates",
        candidate_file if candidate_file[0][1].exists() else [],
        licence.CANDIDATE,
        _nothing,
        lambda row: [row["source_id"], _rebuilt(row, held)],
    )

    latest = archive.latest_by_source(archive.read_receipts(root))
    for source, receipt in latest.items():
        facts = (receipt[name] for name in ("fetched_at", "outcome", "reason", "sha256"))
        made.push("latest", source, *facts)
    for text in licence.render_triage(queue, date).split("\n")[:-1]:
        made.push("triage", text)

    found.counts = {"licences": len(held), "sources": len(licences)}
    found.counts |= {"with_url": len(items), "without_url": len(without)}
    for name, tally in found.written.items():
        found.counts |= {name: tally.read - tally.refused, f"{name}_refused": tally.refused}
    found.counts |= {"latest": len(latest), "triage_rows": len(queue)}
    return made.lines


def said(root: Path, argv: Sequence[str]) -> Said:
    """What one of the recorded commands says of a checkout. Raises `StoreError` for any other."""
    flags = set(argv[2:])
    as_json = "--json" in flags
    match tuple(argv[:2]):
        case ("archive", "worklist"):
            return archivesaid.say_worklist(root, as_json=as_json)
        case ("archive", "status"):
            return archivesaid.say_status(root, as_json=as_json)
        case ("archive", "verify"):
            return archivesaid.say_verify(root)
        case ("archive", "reclassify") if "--dry-run" in flags:
            return archivesaid.say_reclassify(root, dry_run=True, as_json=as_json, quiet=False)
        case ("licence", "triage") if "--stdout" in flags and "--date" in flags:
            return archivesaid.say_triage(root, argv[argv.index("--date") + 1], out=None, to_stdout=True)
        case ("licence", "counts"):
            return archivesaid.say_counts(root)
    raise StoreError(f"the recording holds a command the gate does not run: {' '.join(argv)}")


def _command(root: Path, recorded: Mapping[str, JsonValue]) -> str | None:
    """What is not as recorded about one command, or None."""
    argv = cast("list[str]", recorded["argv"])
    ran = said(root, argv)
    out = f"{ran.out}\n"
    if "stdout" in recorded:
        printed = out == recorded["stdout"]
    else:
        printed = _digest(out) == recorded["stdout_sha256"] and out.count("\n") == recorded["stdout_lines"]
    err = "".join(f"{line}\n" for line in ran.err)
    if printed and ran.status == recorded["status"] and err == recorded["stderr"]:
        return None
    return f"`{' '.join(argv)}` does not print what the TypeScript printed, or leaves with another status"


def _member(recorded: Mapping[str, JsonValue], name: str) -> JsonObject:
    held = recorded.get(name)
    if not isinstance(held, dict):
        raise StoreError(f"the recorded expectation has no {name}")
    return held


def _listed(recorded: Mapping[str, JsonValue], name: str) -> list[JsonObject]:
    held = recorded.get(name)
    if not isinstance(held, list) or not all(isinstance(item, dict) for item in held):
        raise StoreError(f"the recorded expectation has no list of {name}")
    return cast("list[JsonObject]", held)


def _modules(root: Path, recorded: JsonObject, found: ArchiveReport) -> None:
    """Whether each TypeScript file the port was made from is the file the recording was made from."""
    for module in _listed(recorded, "modules"):
        found.modules.expected += 1
        path = root / cast("str", module["file"])
        if path.is_file() and sha256_hex(path.read_bytes()) == module["sha256"]:
            found.modules.same += 1
        else:
            found.problems.append(
                f"{module['file']} in the checkout is not the file the recording was made from"
            )
    found.modules.made = found.modules.expected


def _queues(root: Path, date: str, triage: JsonObject, found: ArchiveReport) -> None:
    """The triage queue against the recorded one, and against the queues the checkout commits.

    A committed queue is as old as the day it was generated, so it is not required to be
    the queue of today. Its lines are counted, and those that today's queue still holds.
    """
    rendered = licence.render_triage(licence.build_triage(root), date)
    generated = set(rendered.split("\n"))
    queues = [file for file in cast("list[str]", triage.get("queues") or []) if (root / file).is_file()]
    for file in queues:
        rows = [line for line in archive.raw_lines(root / file) if line and not line.startswith("#")]
        found.queue_rows.expected += len(rows)
        found.queue_rows.same += sum(1 for row in rows if row in generated)
        if (root / file).read_bytes() == js_utf8(rendered):
            found.committed_queues.append(file)
    found.queue_rows.made = len(generated) - 1
    if _digest(rendered) != triage.get("sha256"):
        found.problems.append("the triage queue is not the one the TypeScript generated")
    if found.committed_queues != triage.get("committed"):
        found.problems.append("the committed queues that are the queue generated are not the ones recorded")


def verify_archive(root: Path, recorded: JsonObject, dump: Sequence[bytes]) -> ArchiveReport:
    """Read a checkout as the archive read side reads it and hold it to the recording.

    Raises `StoreError` for a checkout or a recording that cannot be read, and for a checkout
    with an object cache, which the recording was not made of. Every disagreement is a
    problem in the report.
    """
    found = ArchiveReport(store_ref=recorded_ref(recorded))
    cached = len(archive.cached_files(root))
    if cached:
        raise StoreError(
            f"the checkout has {cached} objects in its cache, and the recording was made of one with none"
        )
    triage = _member(recorded, "triage")
    date = triage.get("date")
    if not isinstance(date, str):
        raise StoreError("the recorded expectation does not name the date of the triage queue")
    _modules(root, recorded, found)
    made = archive_lines(root, date, found)
    found.lines = compare_lines(made, dump, "the checkout as the read side reads it", found.problems)
    counts: JsonObject = dict(found.counts)
    found.problems.extend(differences(counts, recorded.get("counts"), "counts"))
    kinds: JsonObject = dict(found.kinds)
    found.problems.extend(differences(kinds, _member(recorded, "dump").get("kinds"), "dump.kinds"))
    found.problems.extend(
        f"{name}: {tally.same} of {tally.read - tally.refused} rows the schema read are written back "
        "as the line the checkout commits"
        for name, tally in found.written.items()
        if tally.same != tally.read - tally.refused
    )
    _queues(root, date, triage, found)
    for name, measured in (
        ("summary", worklist.summarise(root)),
        ("plan", reclassify.plan_reclassify(root)),
    ):
        if js_stringify(measured) != js_stringify(recorded.get(name)):
            found.problems.append(f"{name}: not what the TypeScript holds, or not in its order")
    for command in _listed(recorded, "commands"):
        found.commands.expected += 1
        problem = _command(root, command)
        found.commands.same += problem is None
        found.problems.extend([] if problem is None else [problem])
    found.commands.made = found.commands.expected
    return found


def read_archive_dump(expected: Path, recorded: JsonObject) -> list[bytes]:
    """The recorded lines, from the file the expectation names beside itself."""
    return read_dump(expected, recorded, "dump")


def summary(found: ArchiveReport) -> str:
    """The report as the lines the gate prints."""
    counts, lines, modules, commands = found.counts, found.lines, found.modules, found.commands
    written = [
        f"  {name.replace('_', ' ')} read by the schema and written back as committed: "
        f"{tally.same:,} of {tally.read:,}, {tally.refused:,} refused"
        for name, tally in found.written.items()
    ]
    return "\n".join(
        [
            f"the store at {found.store_ref}: {counts.get('manifest_rows', 0):,} manifest rows",
            *written,
            f"  licence strings in the map: {counts.get('licences', 0):,}",
            f"  source records: {counts.get('sources', 0):,}, {counts.get('with_url', 0):,} with a URL",
            f"  sources with a receipt: {counts.get('latest', 0):,}",
            f"  rows of the triage queue: {counts.get('triage_rows', 0):,}",
            f"  committed queues that are the queue generated: {len(found.committed_queues)}",
            (
                f"  lines of the committed queues that are lines of the queue generated: "
                f"{found.queue_rows.same:,} of {found.queue_rows.expected:,}"
            ),
            f"  TypeScript files that are the files recorded: {modules.same} of {modules.expected}",
            f"  commands that print what the TypeScript printed: {commands.same} of {commands.expected}",
            f"  lines the TypeScript recorded, reproduced: {lines.same:,} of {lines.expected:,}",
        ]
    )


def to_json(found: ArchiveReport) -> str:
    """The report as JSON, for CI to read counts from."""
    return json.dumps(
        {
            "store_ref": found.store_ref,
            "counts": found.counts,
            "kinds": found.kinds,
            "lines": found.lines.row(),
            "written": {name: tally.row() for name, tally in found.written.items()},
            "commands": found.commands.row(),
            "modules": found.modules.row(),
            "committed_queues": found.committed_queues,
            "queue_rows": found.queue_rows.row(),
            "problems": found.problems,
        },
        indent=1,
        ensure_ascii=False,
    )
