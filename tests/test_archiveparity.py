"""The archive gate: a checkout read as the read side reads it, held to a recording."""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import archive, archiveparity, licence, reclassify, worklist
from rewire_evidence.archiveparity import ArchiveReport, archive_lines, said, verify_archive
from rewire_evidence.canonical import JsonObject, canonical_line, sha256_hex
from rewire_evidence.gates import archive_gate
from rewire_evidence.jsonl import StoreError
from tests.archived import ARCHIVE, FIXTURES
from tests.test_gates import run_script

ROOT = Path(__file__).parent.parent
EXPECTED = ROOT / "scripts/expected/store-archive.json"
STORE: dict[str, Any] = json.loads(EXPECTED.read_text("utf-8"))
DATE = "2026-01-02"
COMMANDS = [
    ["archive", "worklist"],
    ["archive", "worklist", "--json"],
    ["archive", "status"],
    ["archive", "status", "--json"],
    ["archive", "verify"],
    ["archive", "reclassify", "--dry-run"],
    ["archive", "reclassify", "--dry-run", "--json"],
    ["licence", "triage", "--stdout", "--date", DATE],
    ["licence", "counts"],
]
RECEIPTS = "data/omics/audits/2026-11-17-archive-fetch.receipts.jsonl"
RESOLVED = "data/omics/audits/2026-11-17-licence-resolve.receipts.jsonl"
QUEUE = f"maintenance/reports/licence-triage-{DATE}.tsv"
OLDER = "maintenance/reports/licence-triage-2025-12-01.tsv"
NOT_RECORDED = " in the checkout is not the file the recording was made from"
COUNTED = (
    "the queue generated holds {} of the {} lines of the committed queues, "
    "and the TypeScript recorded 12 of 13"
)


def scenario(name: str) -> dict[str, Any]:
    return next(held for held in ARCHIVE["scenarios"] if held["name"] == name)


def write(root: Path, files: dict[str, str]) -> None:
    for file, text in files.items():
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        (root / file).write_bytes(text.encode("utf-8"))


def lay_out(root: Path) -> Path:
    """A small checkout: a manifest, a map, sources, candidates, receipts and two committed queues."""
    receipts = scenario("receipts")["files"]
    lines = [line for line in receipts[RECEIPTS].split("\n") if line]
    files = scenario("a reclassification that is written")["files"]
    # One source of that tree carries a string its map has no row for, which stops the queue.
    held = f"{files[licence.LICENCE_MAP]}CC0 1.0\tcc0\tt0-public\tlicence-text\tread on 2026-11-21\n"
    write(root, files | {licence.LICENCE_MAP: held})
    write(root, {RECEIPTS: "".join(f"{line}\n" for line in lines), RESOLVED: receipts[RESOLVED]})
    write(root, {module["file"]: f"// {module['file']}\n" for module in STORE["modules"]})
    queue = licence.render_triage(licence.build_triage(root), DATE)
    older = "".join(f"{line}\n" for line in queue.split("\n")[:-3])
    write(root, {QUEUE: queue, OLDER: f"{older}gone-since\tno-licence-recorded\n"})
    return root


def inputs_of(root: Path) -> list[str]:
    """The files of a tree the read side reads, in the order the recorder names them."""
    files = [
        worklist.SOURCES,
        licence.LICENCE_MAP,
        *(f"data/archive/objects/{shard}.jsonl" for shard in "0123456789abcdef"),
        licence.CANDIDATES,
        RECEIPTS,
        RESOLVED,
        OLDER,
        QUEUE,
    ]
    return [file for file in files if (root / file).is_file()]


def recorded_of(root: Path, folder: Path) -> JsonObject:
    """An expectation measured on a tree as it stands, as the committed one was by the TypeScript."""
    found = ArchiveReport()
    lines = archive_lines(root, DATE, found)
    data = b"".join(line + b"\n" for line in lines)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "lines.jsonl.gz").write_bytes(gzip.compress(data))
    queue = licence.render_triage(licence.build_triage(root), DATE)
    commands: list[Any] = []
    for at, argv in enumerate(COMMANDS):
        ran = said(root, argv)
        out = f"{ran.out}\n"
        printed = (
            {"stdout": out} if at % 2 else {"stdout_sha256": sha256_hex(out), "stdout_lines": out.count("\n")}
        )
        commands.append({"argv": argv, "status": ran.status, **printed, "stderr": ""})
    recorded: JsonObject = {
        "recorded_from": {"store_ref": "abc123"},
        "modules": [
            {"file": module["file"], "sha256": sha256_hex((root / module["file"]).read_bytes())}
            for module in STORE["modules"]
        ],
        "inputs": [
            {"file": file, "sha256": sha256_hex((root / file).read_bytes())} for file in inputs_of(root)
        ],
        "counts": dict(found.counts),
        "dump": {
            "file": "lines.jsonl.gz",
            "lines": len(lines),
            "sha256": sha256_hex(data),
            "kinds": dict(found.kinds),
        },
        "triage": {
            "date": DATE,
            "sha256": sha256_hex(queue),
            "queues": [file for file in (OLDER, QUEUE) if (root / file).is_file()],
            "committed": [QUEUE] if (root / QUEUE).is_file() else [],
            **queue_lines(root, queue),
        },
        "summary": worklist.summarise(root),
        "plan": reclassify.plan_reclassify(root),
        "commands": commands,
    }
    (folder / "expected.json").write_text(json.dumps(recorded), encoding="utf-8")
    return recorded


def queue_lines(root: Path, queue: str) -> dict[str, int]:
    """The lines of the committed queues, and those the queue generated holds, as the recorder counts."""
    generated = set(queue.split("\n"))
    lines = [
        line
        for file in (OLDER, QUEUE)
        if (root / file).is_file()
        for line in (root / file).read_text("utf-8").split("\n")
        if line and not line.startswith("#")
    ]
    return {"lines": len(lines), "held": sum(line in generated for line in lines)}


def verified(root: Path, recorded: JsonObject, folder: Path) -> ArchiveReport:
    dump = archiveparity.read_archive_dump(folder / "expected.json", recorded)
    return verify_archive(root, recorded, dump)


def test_the_gate_passes_the_tree_it_was_recorded_on(tmp_path: Path) -> None:
    root = lay_out(tmp_path / "store")
    recorded = recorded_of(root, tmp_path / "expected")
    found = verified(root, recorded, tmp_path / "expected")
    assert found.problems == []
    assert found.store_ref == "abc123"
    assert found.counts == {
        "licences": 16,
        "sources": 17,
        "with_url": 0,
        "without_url": 17,
        "manifest_rows": 16,
        "manifest_rows_refused": 0,
        "receipts": 7,
        "receipts_refused": 0,
        "resolve_receipts": 1,
        "resolve_receipts_refused": 0,
        "candidates": 8,
        "candidates_refused": 0,
        "latest": 5,
        "triage_rows": 6,
    }
    assert found.kinds == {
        "licence": 16,
        "source": 17,
        "row": 16,
        "receipt": 7,
        "resolve-receipt": 1,
        "candidate": 8,
        "latest": 5,
        "triage": 14,
    }
    assert found.lines.row() == {"made": 84, "expected": 84, "same": 84}
    assert {name: tally.row() for name, tally in found.written.items()} == {
        "manifest_rows": {"read": 16, "refused": 0, "same": 16},
        "receipts": {"read": 7, "refused": 0, "same": 7},
        "resolve_receipts": {"read": 1, "refused": 0, "same": 1},
        "candidates": {"read": 8, "refused": 0, "same": 8},
    }
    assert (found.commands.row(), found.modules.row(), found.inputs.row()) == (
        {"made": 9, "expected": 9, "same": 9},
        {"made": 12, "expected": 12, "same": 12},
        {"made": 8, "expected": 8, "same": 8},
    )
    assert found.committed_queues == [QUEUE]
    # The older queue has two rows fewer and one that has since left the queue.
    assert found.queue_rows.row() == {"made": 14, "expected": 13, "same": 12}
    assert archiveparity.summary(found).split("\n") == [
        "the store at abc123: 16 manifest rows",
        "  manifest rows read by the schema and written back as committed: 16 of 16, 0 refused",
        "  receipts read by the schema and written back as committed: 7 of 7, 0 refused",
        "  resolve receipts read by the schema and written back as committed: 1 of 1, 0 refused",
        "  candidates read by the schema and written back as committed: 8 of 8, 0 refused",
        "  licence strings in the map: 16",
        "  source records: 17, 0 with a URL",
        "  sources with a receipt: 5",
        "  rows of the triage queue: 6",
        "  committed queues that are the queue generated: 1",
        "  lines of the committed queues that are lines of the queue generated: 12 of 13",
        "  TypeScript files that are the files recorded: 12 of 12",
        "  files of the checkout that are the files recorded: 8 of 8",
        "  commands that print what the TypeScript printed: 9 of 9",
        "  lines the TypeScript recorded, reproduced: 84 of 84",
    ]
    report = json.loads(archiveparity.to_json(found))
    assert report["counts"] == found.counts
    assert report["queue_rows"] == {"made": 14, "expected": 13, "same": 12}
    assert report["inputs"] == {"made": 8, "expected": 8, "same": 8}
    assert archiveparity.summary(ArchiveReport()).startswith("the store at : 0 manifest rows\n")


def line_of(root: Path, file: str, at: int) -> dict[str, Any]:
    held: dict[str, Any] = json.loads((root / file).read_text("utf-8").split("\n")[at])
    return held


def put(root: Path, file: str, at: int, line: str) -> None:
    lines = (root / file).read_text("utf-8").split("\n")
    lines[at] = line
    (root / file).write_bytes("\n".join(lines).encode("utf-8"))


def test_the_gate_names_what_differs_from_the_recording(tmp_path: Path) -> None:
    root = lay_out(tmp_path / "store")
    folder = tmp_path / "expected"
    recorded = recorded_of(root, folder)
    shard = "data/archive/objects/5.jsonl"

    # A row that says another tier: its line, the plan and what the commands print all move.
    first = line_of(root, shard, 0)
    assert (first["archive_tier"], first["store"]) == ("t0-public", "r2")
    put(root, shard, 0, canonical_line(first | {"archive_tier": "t2-verbatim-nd"}))
    problems = verified(root, recorded, folder).problems
    assert problems[0] == f"{shard}{NOT_RECORDED}"
    assert problems[1].startswith("the checkout as the read side reads it, line ")
    assert "plan: not what the TypeScript holds, or not in its order" in problems
    assert sum("does not print what the TypeScript printed" in problem for problem in problems) == 2
    put(root, shard, 0, canonical_line(first))
    assert verified(root, recorded, folder).problems == []

    # A row the schema reads and would not write back as it stands: its members in another order.
    put(root, shard, 0, json.dumps(dict(reversed(first.items())), separators=(",", ":")))
    found = verified(root, recorded, folder)
    assert found.problems == [
        f"{shard}{NOT_RECORDED}",
        "manifest_rows: 15 of 16 rows the schema read are written back as the line the checkout commits",
    ]
    assert found.lines.same == found.lines.expected

    # Rows no schema reads, and a line that is no object at all. Each is a line of its own,
    # and the commands that read the manifest refuse the tree, so the gate cannot be run on it.
    put(root, shard, 0, canonical_line(first | {"visibility": "restricted"}))
    put(root, shard, 1, "5")
    put(root, RESOLVED, 0, canonical_line(line_of(root, RESOLVED, 0) | {"schema_version": 2}))
    report = ArchiveReport()
    made = [json.loads(line) for line in archive_lines(root, DATE, report)]
    assert (report.counts["manifest_rows"], report.counts["manifest_rows_refused"]) == (14, 2)
    assert (report.counts["resolve_receipts"], report.counts["resolve_receipts_refused"]) == (0, 1)
    refused = [line for line in made if isinstance(line[-1], dict) and "issues" in line[-1]]
    assert [line[:4] for line in refused[:2]] == [["row", "5", 1, first["sha256"]], ["row", "5", 2, None]]
    assert refused[0][4] == {"issues": [["custom", [], "tier t0-public is public, not restricted"]]}
    assert refused[2] == [
        "resolve-receipt",
        RESOLVED,
        1,
        {"issues": [["invalid_value", ["schema_version"], "Invalid input: expected 1"]]},
    ]
    with pytest.raises(StoreError, match=r"data/archive/objects/5\.jsonl line 1: tier t0-public is public"):
        verified(root, recorded, folder)
    # A receipt the schema refuses stops the reading of the receipts, as it stops the TypeScript's.
    put(root, RECEIPTS, 0, canonical_line(line_of(root, RECEIPTS, 0) | {"schema_version": 2}))
    with pytest.raises(StoreError, match=r"receipts\.jsonl line 1: Invalid input: expected 1"):
        archive_lines(root, DATE, ArchiveReport())


def test_the_gate_holds_the_files_the_port_was_made_from_and_the_queues(tmp_path: Path) -> None:
    root = lay_out(tmp_path / "store")
    folder = tmp_path / "expected"
    recorded = recorded_of(root, folder)
    (root / "scripts/licence/map.ts").write_bytes(b"// changed\n")
    (root / "scripts/archive/cli.ts").unlink()
    found = verified(root, recorded, folder)
    assert found.problems == [
        "scripts/archive/cli.ts in the checkout is not the file the recording was made from",
        "scripts/licence/map.ts in the checkout is not the file the recording was made from",
    ]
    assert found.modules.row() == {"made": 12, "expected": 12, "same": 10}

    root = lay_out(tmp_path / "another")
    (root / QUEUE).write_bytes((root / QUEUE).read_bytes() + b"# edited\n")
    (root / OLDER).unlink()
    found = verified(root, recorded, folder)
    assert found.problems == [
        f"{OLDER}{NOT_RECORDED}",
        f"{QUEUE}{NOT_RECORDED}",
        "the committed queues that are the queue generated are not the ones recorded",
        COUNTED.format(7, 7),
    ]
    assert (found.committed_queues, found.queue_rows.expected) == ([], 7)
    assert found.inputs.row() == {"made": 8, "expected": 8, "same": 6}
    stale = recorded | {"triage": recorded["triage"] | {"sha256": "0" * 64}}  # type: ignore[operator]
    assert verified(lay_out(tmp_path / "fresh"), stale, folder).problems == [
        "the triage queue is not the one the TypeScript generated"
    ]
    summary = recorded | {"summary": {"sources": 1}}
    assert verified(lay_out(tmp_path / "third"), summary, folder).problems == [
        "summary: not what the TypeScript holds, or not in its order"
    ]


def test_a_committed_queue_that_was_edited_cut_short_or_removed_fails_the_gate(tmp_path: Path) -> None:
    folder = tmp_path / "expected"
    recorded = recorded_of(lay_out(tmp_path / "store"), folder)
    expected = str(folder / "expected.json")

    def held(name: str, change: Callable[[list[str]], list[str]] | None) -> list[str]:
        root = lay_out(tmp_path / name)
        if change is None:
            (root / OLDER).unlink()
        else:
            lines = (root / OLDER).read_text("utf-8").split("\n")
            (root / OLDER).write_bytes("\n".join(change(lines)).encode("utf-8"))
        assert archive_gate([str(root), "--expected", expected], folder / "expected.json") == 1
        problems = verified(root, recorded, folder).problems
        assert problems[0] == f"{OLDER}{NOT_RECORDED}"
        return problems[1:]

    # The older queue is its comment lines, a header, four rows today's queue holds and one it does not.
    lines = (tmp_path / "store" / OLDER).read_text("utf-8").split("\n")
    row = next(at for at, line in enumerate(lines) if line and not line.startswith(("#", "source_id")))
    assert held("edited", lambda held: [*held[:row], f"{held[row]}x", *held[row + 1 :]]) == [
        COUNTED.format(11, 13)
    ]
    assert held("cut", lambda held: [*held[:row], *held[row + 1 :]]) == [COUNTED.format(11, 12)]
    assert held("removed", None) == [COUNTED.format(7, 7)]
    # A change that leaves every row a row of today's queue is still another file.
    assert held("commented", lambda held: ["# a comment", *held]) == []
    assert held("unended", lambda held: held[:-1]) == []


def test_a_file_framed_otherwise_is_another_file_though_every_line_reads_the_same(tmp_path: Path) -> None:
    folder = tmp_path / "expected"
    recorded = recorded_of(lay_out(tmp_path / "store"), folder)
    shard = "data/archive/objects/5.jsonl"
    changes: list[tuple[str, Callable[[bytes], bytes]]] = [
        (licence.LICENCE_MAP, lambda held: b"# a comment\n" + held),
        (shard, lambda held: held.removesuffix(b"\n")),
        (shard, lambda held: held + b"\n"),
        (licence.CANDIDATES, lambda held: held.removesuffix(b"\n")),
        (RESOLVED, lambda held: held + b"\n\n"),
    ]
    for at, (file, change) in enumerate(changes):
        root = lay_out(tmp_path / f"tree-{at}")
        (root / file).write_bytes(change((root / file).read_bytes()))
        found = verified(root, recorded, folder)
        assert found.problems == [f"{file}{NOT_RECORDED}"]
        # Every line is read as it was and written back as committed, so nothing else can tell.
        assert found.lines.same == found.lines.expected == 84
        assert all(tally.same == tally.read for tally in found.written.values())
        assert found.inputs.row() == {"made": 8, "expected": 8, "same": 7}


def test_the_gate_cannot_be_run_on_what_was_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = lay_out(tmp_path / "store")
    folder = tmp_path / "expected"
    recorded = recorded_of(root, folder)
    for name, said_of in (
        ("triage", "has no triage"),
        ("modules", "has no list of modules"),
        ("inputs", "has no list of inputs"),
        ("commands", "has no list of commands"),
        ("dump", "does not hold dump"),
    ):
        without = {key: held for key, held in recorded.items() if key != name}
        with pytest.raises(StoreError, match=said_of):
            verified(root, without, folder)
    with pytest.raises(StoreError, match="does not name the date"):
        verified(root, recorded | {"triage": {}}, folder)
    with pytest.raises(StoreError, match="has no list of commands"):
        verified(root, recorded | {"commands": [5]}, folder)
    for argv in (["archive", "reclassify"], ["licence", "triage", "--stdout"], ["archive", "fetch"], ["x"]):
        unknown: JsonObject = {**recorded, "commands": [{"argv": list(argv)}]}
        with pytest.raises(StoreError, match="a command the gate does not run"):
            verified(root, unknown, folder)
    # A cache is not part of what was recorded.
    key = sha256_hex(b"an object")
    cached = archive.object_path(key, root)
    cached.parent.mkdir(parents=True)
    cached.write_bytes(gzip.compress(b"an object"))
    with pytest.raises(StoreError, match="has 1 objects in its cache"):
        verified(root, recorded, folder)
    # The cache is wherever ARCHIVE_CACHE says, so an empty folder there is a checkout with none.
    monkeypatch.setenv("ARCHIVE_CACHE", str(tmp_path / "empty"))
    assert verified(root, recorded, folder).problems == []


def test_a_tree_with_nothing_committed_is_read_as_nothing(tmp_path: Path) -> None:
    root = tmp_path / "store"
    write(
        root, {licence.LICENCE_MAP: "licence\tfamily\ttier\tbasis\tnote\n", "data/entities/sources.jsonl": ""}
    )
    found = ArchiveReport()
    lines = archive_lines(root, DATE, found)
    assert len(lines) == found.kinds["triage"] == 7
    assert all(count == 0 for count in found.counts.values())


# ---------------------------------------------------------------------------------------
# The gate as a command.


def test_the_archive_gate_reports_and_fails_on_a_tree_that_differs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = lay_out(tmp_path / "store")
    recorded_of(root, tmp_path / "expected")
    expected = tmp_path / "expected" / "expected.json"
    measured = tmp_path / "measured.json"
    assert archive_gate([str(root), "--json", str(measured)], expected) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.startswith(
        "expecting the manifest, the receipts and the licence map as the TypeScript read them at abc123\n"
        "the store at abc123: 16 manifest rows\n"
    )
    assert out.out.endswith(
        "\nAll 16 manifest rows, 7 receipts and 8 candidates are read as the TypeScript reads them, and "
        "9 commands print what its commands print.\n"
    )
    report = json.loads(measured.read_text("utf-8"))
    assert (report["lines"]["same"], report["commands"]["same"], report["problems"]) == (84, 9, [])

    (root / RESOLVED).unlink()
    assert archive_gate([str(root), "--expected", str(expected), "--json", str(measured)], expected) == 1
    err = capsys.readouterr().err
    assert "  the checkout as the read side reads it: 83 lines made here and 84 expected\n" in err
    assert re.search(r"\n\d+ problems\. \d+ of 84 recorded lines are reproduced\.\n$", err)
    assert json.loads(measured.read_text("utf-8"))["counts"]["resolve_receipts"] == 0


def test_the_archive_gate_cannot_be_run_on_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    write(
        root, {licence.LICENCE_MAP: "licence\tfamily\ttier\tbasis\tnote\n", "data/entities/sources.jsonl": ""}
    )
    write(root, {module["file"]: "x" for module in STORE["modules"]})
    recorded_of(root, tmp_path / "expected")
    expected = tmp_path / "expected" / "expected.json"
    assert archive_gate([str(root)], expected) == 2
    assert capsys.readouterr().err == (
        "Cannot verify: a checkout with no manifest row holds the read side to nothing\n"
    )
    assert archive_gate([str(tmp_path / "nowhere")], expected) == 2
    assert capsys.readouterr().err.startswith("Cannot verify: ")
    assert archive_gate([str(root)], tmp_path / "missing.json") == 2
    (tmp_path / "expected" / "lines.jsonl.gz").write_bytes(b"not gzip")
    assert archive_gate([str(root)], expected) == 2
    assert "lines.jsonl.gz cannot be read" in capsys.readouterr().err


def test_the_archive_script_is_the_gate(tmp_path: Path) -> None:
    root = lay_out(tmp_path / "store")
    recorded_of(root, tmp_path / "expected")
    passed = run_script(
        "verify_store_archive.py", str(root), "--expected", str(tmp_path / "expected" / "expected.json")
    )
    assert passed.returncode == 0, passed.stderr
    assert (
        "All 16 manifest rows, 7 receipts and 8 candidates are read as the TypeScript reads them"
        in passed.stdout
    )
    # With no --expected the script holds the tree to the committed recording, which this is not.
    assert run_script("verify_store_archive.py", str(root)).returncode == 1
    assert "usage:" in run_script("verify_store_archive.py", "--help").stdout


# ---------------------------------------------------------------------------------------
# What the TypeScript recorded of the pinned store.


def workflow_pin(name: str) -> str:
    workflow = (ROOT / ".github/workflows/checks.yml").read_text("utf-8")
    found = re.findall(rf'^  {name}: "?([^"\n]+)"?$', workflow, re.MULTILINE)
    assert len(found) == 1
    return str(found[0])


def test_the_recording_is_of_the_pinned_store_and_the_workflow_pins_its_counts() -> None:
    assert STORE["recorded_from"] == ARCHIVE["recorded_from"]
    assert STORE["recorded_from"]["store_ref"] == workflow_pin("STORE_REF")
    counts = STORE["counts"]
    assert str(counts["manifest_rows"]) == workflow_pin("ARCHIVE_ROWS") == "1167"
    assert str(counts["receipts"]) == workflow_pin("ARCHIVE_RECEIPTS") == "1526"
    assert str(counts["resolve_receipts"]) == workflow_pin("ARCHIVE_RESOLVE_RECEIPTS") == "1731"
    assert str(counts["candidates"]) == workflow_pin("ARCHIVE_CANDIDATES") == "1091"
    assert str(counts["sources"]) == workflow_pin("ARCHIVE_SOURCES") == "1497"
    assert str(counts["licences"]) == workflow_pin("ARCHIVE_LICENCES") == "97"
    assert str(counts["triage_rows"]) == workflow_pin("ARCHIVE_TRIAGE_ROWS") == "1091"
    assert str(STORE["dump"]["lines"]) == workflow_pin("ARCHIVE_LINES") == "9696"
    assert str(len(STORE["commands"])) == workflow_pin("ARCHIVE_COMMANDS") == "9"
    assert str(len(STORE["modules"])) == workflow_pin("ARCHIVE_MODULES") == "12"
    assert str(len(STORE["inputs"])) == workflow_pin("ARCHIVE_INPUTS") == "22"
    triage = STORE["triage"]
    assert str(triage["lines"]) == str(triage["held"]) == workflow_pin("ARCHIVE_QUEUE_ROWS") == "1083"
    # Every file the read side reads is held by its digest: the queues, the receipts and the rest.
    held = {entry["file"] for entry in STORE["inputs"]}
    assert {*triage["queues"], *STORE["files"]["receipts"], *STORE["files"]["resolve_receipts"]} < held
    assert {worklist.SOURCES, licence.LICENCE_MAP, licence.CANDIDATES, "data/archive/objects/f.jsonl"} < held
    assert all(counts[name] == 0 for name in counts if name.endswith("_refused"))
    assert [command["argv"][:2] for command in STORE["commands"]] == [argv[:2] for argv in COMMANDS]
    # Nothing moves at the pinned commit: reclassify is a no-op on the committed manifest.
    plan = STORE["plan"]
    assert (plan["moves"], plan["restrictive"], plan["tightening"]) == ([], [], [])
    assert plan["before"] == plan["after"]
    assert (plan["manifest_rows"], plan["publishable_after"]) == (1167, 871)
    # The committed queue is two days older than the pinned store and nine sources short of it.
    assert STORE["triage"]["committed"] == []
    workflow = (ROOT / ".github/workflows/checks.yml").read_text("utf-8")
    (gate,) = [
        step for step in re.split(r"\n(?=      - )", workflow) if "scripts/verify_store_archive.py" in step
    ]
    assert re.findall(r'"([a-z_.]+):(\$[A-Z_]+)"', gate) == [
        ("counts.manifest_rows", "$ARCHIVE_ROWS"),
        ("written.manifest_rows.same", "$ARCHIVE_ROWS"),
        ("counts.receipts", "$ARCHIVE_RECEIPTS"),
        ("written.receipts.same", "$ARCHIVE_RECEIPTS"),
        ("counts.resolve_receipts", "$ARCHIVE_RESOLVE_RECEIPTS"),
        ("written.resolve_receipts.same", "$ARCHIVE_RESOLVE_RECEIPTS"),
        ("counts.candidates", "$ARCHIVE_CANDIDATES"),
        ("written.candidates.same", "$ARCHIVE_CANDIDATES"),
        ("counts.sources", "$ARCHIVE_SOURCES"),
        ("counts.licences", "$ARCHIVE_LICENCES"),
        ("counts.triage_rows", "$ARCHIVE_TRIAGE_ROWS"),
        ("lines.expected", "$ARCHIVE_LINES"),
        ("lines.same", "$ARCHIVE_LINES"),
        ("commands.same", "$ARCHIVE_COMMANDS"),
        ("modules.same", "$ARCHIVE_MODULES"),
        ("inputs.same", "$ARCHIVE_INPUTS"),
        ("queue_rows.expected", "$ARCHIVE_QUEUE_ROWS"),
        ("queue_rows.same", "$ARCHIVE_QUEUE_ROWS"),
    ]


def test_the_committed_queue_is_written_again_from_the_commit_that_wrote_it() -> None:
    workflow = (ROOT / ".github/workflows/checks.yml").read_text("utf-8")
    steps = re.split(r"\n(?=      - )", workflow)
    queue_ref = workflow_pin("QUEUE_REF")
    assert re.fullmatch(r"[0-9a-f]{40}", queue_ref)
    assert queue_ref != workflow_pin("STORE_REF")
    (checkout,) = [step for step in steps if "ref: ${{ env.QUEUE_REF }}" in step]
    assert "repository: rewire-bio/rewire-benchmark-data" in checkout
    assert "path: queue-run" in checkout
    (gate,) = [step for step in steps if "--root queue-run" in step]
    # The commit is the one named, the queue there is the pinned one, and the queue written
    # again is that file byte for byte, with its lines counted.
    assert '[ "$held" != "$QUEUE_REF" ]' in gate
    assert 'cmp "queue-run/${queue}" "store/${queue}"' in gate
    assert 'licence triage --root queue-run --stdout --date "$date" > queue.tsv' in gate
    assert 'cmp queue.tsv "store/${queue}"' in gate
    assert '[ "$rows" != "$ARCHIVE_QUEUE_ROWS" ]' in gate
    assert steps.index(checkout) < steps.index(gate)


def test_the_recorded_lines_are_of_the_kinds_the_counts_state() -> None:
    lines = [json.loads(line) for line in archiveparity.read_archive_dump(EXPECTED, STORE)]
    kinds = STORE["dump"]["kinds"]
    assert {kind: sum(line[0] == kind for line in lines) for kind in kinds} == kinds
    assert sum(kinds.values()) == len(lines) == STORE["dump"]["lines"]
    rows = [line for line in lines if line[0] == "row"]
    assert [line[3] for line in rows] == sorted(line[3] for line in rows)
    assert all(line[3][0] == line[1] and len(line) == 7 for line in rows)
    # Every row is given the tier it carries by the classifier or keeps it for want of evidence.
    assert sum(line[6][2] for line in rows) == len(rows) - STORE["plan"]["unevidenced"]
    assert FIXTURES.joinpath("archive.json").stat().st_size > 1_000_000
