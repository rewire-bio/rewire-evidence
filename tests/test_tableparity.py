"""The table gate: the reader held to a recording of every committed artifact.

The gate reads a checkout of the data repository, which no test has. So it is run here on a
small tree of artifacts, with an expectation measured on it as the committed one was measured
on the pinned store by the TypeScript. What the TypeScript recorded of the pinned store
itself, `scripts/expected/store-tables.json` and the lines beside it, is read here for what
it states.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import tableparity
from rewire_evidence.canonical import JsonObject, sha256_hex
from rewire_evidence.checkparity import Lines
from rewire_evidence.jsonl import StoreError
from rewire_evidence.tableparity import (
    TableCounts,
    artifact_files,
    artifact_lines,
    read_tables_dump,
    source_of,
    summary,
    to_json,
    verify_tables,
)

ROOT = Path(__file__).parent.parent
EXPECTED = ROOT / "scripts/expected/store-tables.json"
STORE: dict[str, Any] = json.loads(EXPECTED.read_text("utf-8"))
MODULE = b"export function tables() {}\n"
PLAIN = "<table><tr><th></th><th>AUROC</th></tr><tr><td>E NFORMER</td><td>0.9</td></tr></table>"
#: A small tree: four artifacts, one with two tables, one with none, one whose table is not closed.
FILES: dict[str, bytes] = {
    "scripts/omics/extract/tables.ts": MODULE,
    "data/omics/a/artifacts/paper.html.gz": gzip.compress(
        f"<figure><figcaption>Table 1: one</figcaption>{PLAIN}</figure><p>x</p>{PLAIN}".encode()
    ),
    "data/omics/a/artifacts/article.xml.gz": gzip.compress(
        "<article><p>No table \U0001f600</p></article>".encode()
    ),
    "data/omics/b/open.html": b"<p>before</p><table><tr><td>&#x110000;</td></tr>",
    "docs/adr/0001.html": b"<table><tr><td>\xff half</td></tr></table>",
    # Not artifacts: another ending, another folder, and a folder named like one.
    "data/omics/a/artifacts/notes.txt": b"<table></table>",
    "data/omics/a/artifacts/paper.xml": b"<table></table>",
    "elsewhere/page.html": b"<table></table>",
    "data/omics/folder.html/kept.txt": b"",
}


def lay_out(root: Path, files: dict[str, bytes] = FILES) -> Path:
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def recorded_of(root: Path, folder: Path) -> tuple[JsonObject, list[bytes]]:
    """An expectation measured on a tree as it stands, as the committed one was by the TypeScript."""
    counts = TableCounts()
    lines = [
        line
        for file in artifact_files(root)
        for line in artifact_lines(file, (root / file).read_bytes(), counts)
    ]
    data = b"".join(line + b"\n" for line in lines)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "lines.jsonl.gz").write_bytes(gzip.compress(data))
    recorded: JsonObject = {
        "recorded_from": {"store_ref": "abc123"},
        "module": {
            "file": tableparity.MODULE,
            "sha256": sha256_hex((root / tableparity.MODULE).read_bytes()),
        },
        "folders": list(tableparity.FOLDERS),
        "counts": dict(asdict(counts)),
        "dump": {"file": "lines.jsonl.gz", "lines": len(lines), "sha256": sha256_hex(data)},
    }
    (folder / "expected.json").write_text(json.dumps(recorded), encoding="utf-8")
    return recorded, lines


def test_the_artifacts_are_the_html_and_xml_files_under_the_two_folders(tmp_path: Path) -> None:
    root = lay_out(tmp_path)
    assert artifact_files(root) == [
        "data/omics/a/artifacts/article.xml.gz",
        "data/omics/a/artifacts/paper.html.gz",
        "data/omics/b/open.html",
        "docs/adr/0001.html",
    ]
    assert artifact_files(tmp_path / "nowhere") == []
    # JavaScript's order, by UTF-16 unit, where a character beyond U+FFFF sorts before U+FF5A.
    lay_out(
        root, {"data/\uff5a.html": b"", "data/\U0001f600.html": b"", "data/Z.html": b"", "data/a.html": b""}
    )
    assert artifact_files(root)[:2] == ["data/Z.html", "data/a.html"]
    assert artifact_files(root)[-3:-1] == ["data/\U0001f600.html", "data/\uff5a.html"]


def test_an_artifact_is_read_as_node_reads_it() -> None:
    assert source_of("a.html", b"caf\xc3\xa9 \xff\xfe") == "caf\u00e9 \ufffd\ufffd"
    assert source_of("a.xml.gz", gzip.compress(b"<a/>")) == "<a/>"
    # A name that says nothing of gzip is read as it is.
    packed = gzip.compress(b"x")
    assert source_of("a.html", packed) == packed.decode("utf-8", "replace")
    for damaged in (b"not gzip", packed[:-4], packed[:12] + b"\x00" * 8):
        with pytest.raises(StoreError, match=r"a\.html\.gz cannot be unpacked"):
            source_of("a.html.gz", damaged)


def test_each_artifact_and_each_table_is_one_line(tmp_path: Path) -> None:
    root = lay_out(tmp_path)
    counts = TableCounts()
    file = "data/omics/a/artifacts/paper.html.gz"
    lines = [json.loads(line) for line in artifact_lines(file, (root / file).read_bytes(), counts)]
    assert [line[:2] for line in lines] == [["artifact", file], ["table", file], ["table", file]]
    assert lines[0][2] == sha256_hex(FILES[file])
    assert lines[0][3:] == [len(gzip.decompress(FILES[file])), lines[0][4], 2]
    assert re.fullmatch("[0-9a-f]{64}", lines[0][4])
    assert [line[2:6] for line in lines[1:]] == [[0, "Table 1: one", 2, 4], [1, "", 2, 4]]
    # The rows are the same in both, so their digests are, and one headed block is found in each.
    assert lines[1][6:] == lines[2][6:]
    assert lines[1][6] != lines[1][7]
    assert lines[1][8] == 1
    assert asdict(counts) == {
        "artifacts": 1,
        "artifacts_with_tables": 1,
        "artifacts_refused": 0,
        "tables": 2,
        "rows": 4,
        "cells": 8,
        "blocks": 2,
    }
    # What the reader refuses is written as its message, for the text and for the tables apart.
    counts = TableCounts()
    (refused,) = artifact_lines("data/omics/b/open.html", FILES["data/omics/b/open.html"], counts)
    assert json.loads(refused)[4:] == [
        {"error": "Invalid code point 1114112"},
        {"error": "Unclosed table element in artifact"},
    ]
    assert (counts.artifacts, counts.artifacts_refused, counts.artifacts_with_tables) == (1, 1, 0)
    (none,) = artifact_lines("a.xml.gz", FILES["data/omics/a/artifacts/article.xml.gz"], counts)
    # The length is in UTF-16 units, as JavaScript counts it.
    assert json.loads(none)[3] == len("<article><p>No table </p></article>") + 2
    assert json.loads(none)[5] == 0
    assert (counts.artifacts, counts.artifacts_refused, counts.artifacts_with_tables) == (2, 1, 0)


def test_the_gate_passes_the_tree_it_was_recorded_on(tmp_path: Path) -> None:
    root = lay_out(tmp_path / "store")
    recorded, lines = recorded_of(root, tmp_path / "expected")
    assert read_tables_dump(tmp_path / "expected" / "expected.json", recorded) == lines
    found = verify_tables(root, recorded, lines)
    assert found.problems == []
    assert found.store_ref == "abc123"
    assert found.module_is_recorded
    assert found.lines == Lines(7, 7, 7)
    assert asdict(found.counts) == {
        "artifacts": 4,
        "artifacts_with_tables": 2,
        "artifacts_refused": 1,
        "tables": 3,
        "rows": 5,
        "cells": 9,
        "blocks": 2,
    }
    assert summary(found).split("\n") == [
        "the store at abc123: 4 committed HTML and XML artifacts",
        "  scripts/omics/extract/tables.ts is the file recorded: yes",
        "  artifacts with a table: 2",
        "  artifacts refused: 1",
        "  tables read: 3, with 5 rows and 9 cells",
        "  headed blocks found in them: 2",
        "  lines the TypeScript recorded, reproduced: 7 of 7",
    ]
    written = json.loads(to_json(found))
    assert (written["store_ref"], written["module_is_recorded"], written["problems"]) == ("abc123", True, [])
    assert (written["counts"]["tables"], written["lines"]) == (3, {"made": 7, "expected": 7, "same": 7})


def test_the_gate_names_what_differs_from_the_recording(tmp_path: Path) -> None:
    root = lay_out(tmp_path / "store")
    recorded, lines = recorded_of(root, tmp_path / "expected")
    # One artifact gone, one cell changed, and the file the patterns were read out of edited.
    (root / "docs/adr/0001.html").unlink()
    page = root / "data/omics/a/artifacts/paper.html.gz"
    page.write_bytes(gzip.compress(gzip.decompress(page.read_bytes()).replace(b"0.9", b"0.8", 1)))
    (root / tableparity.MODULE).write_bytes(MODULE + b"// edited\n")
    found = verify_tables(root, recorded, lines)
    assert not found.module_is_recorded
    assert found.lines == Lines(5, 7, 3)
    said = "\n".join(found.problems)
    assert (
        "scripts/omics/extract/tables.ts in the checkout is not the file the recording was made from" in said
    )
    assert "a committed artifact as the reader reads it: 5 lines made here and 7 expected" in said
    assert "a committed artifact as the reader reads it, line 2: from byte " in said
    assert "counts.artifacts: measured 3, the TypeScript recorded 4" in said
    assert "counts.tables: measured 2, the TypeScript recorded 3" in said
    assert "  scripts/omics/extract/tables.ts is the file recorded: no\n" in summary(found)
    other: JsonObject = {**recorded, "folders": ["data"], "module": None}
    assert "folders: 2 items measured and 1 recorded by the TypeScript" in "\n".join(
        verify_tables(root, other, lines).problems
    )


def test_the_gate_cannot_be_run_without_its_files(tmp_path: Path) -> None:
    root = lay_out(tmp_path / "store")
    recorded, lines = recorded_of(root, tmp_path / "expected")
    with pytest.raises(StoreError, match="does not name the store commit"):
        verify_tables(root, {}, lines)
    with pytest.raises(StoreError, match=r"tables\.ts cannot be read"):
        verify_tables(tmp_path / "nowhere", recorded, lines)
    (root / "data/omics/b/broken.xml.gz").write_bytes(b"not gzip")
    with pytest.raises(StoreError, match=r"data/omics/b/broken\.xml\.gz cannot be unpacked"):
        verify_tables(root, recorded, lines)
    with pytest.raises(StoreError, match="does not hold dump"):
        read_tables_dump(tmp_path / "expected" / "expected.json", {})


# ---------------------------------------------------------------------------------------
# What the TypeScript recorded of the pinned store.


def workflow_pin(name: str) -> str:
    workflow = (ROOT / ".github/workflows/checks.yml").read_text("utf-8")
    found = re.findall(rf'^  {name}: "?([^"\n]+)"?$', workflow, re.MULTILINE)
    assert len(found) == 1
    return str(found[0])


def test_the_recording_is_of_the_pinned_store_and_the_workflow_pins_its_counts() -> None:
    assert STORE["recorded_from"]["script"] == "scripts/fixtures/record_tables_fixture.mjs"
    assert STORE["recorded_from"]["store_ref"] == workflow_pin("STORE_REF")
    assert STORE["module"]["file"] == tableparity.MODULE
    assert STORE["folders"] == list(tableparity.FOLDERS)
    counts = STORE["counts"]
    assert str(counts["artifacts"]) == workflow_pin("TABLE_ARTIFACTS") == "97"
    assert str(counts["tables"]) == workflow_pin("TABLES") == "245"
    assert str(counts["cells"]) == workflow_pin("TABLE_CELLS") == "17307"
    assert str(STORE["dump"]["lines"]) == workflow_pin("TABLE_LINES") == "342"
    assert list(counts) == list(asdict(TableCounts()))
    assert counts["artifacts_refused"] == 0
    workflow = (ROOT / ".github/workflows/checks.yml").read_text("utf-8")
    (gate,) = [
        step for step in re.split(r"\n(?=      - )", workflow) if "scripts/verify_store_tables.py" in step
    ]
    assert re.findall(r'"([a-z_.]+):(\$[A-Z_]+|true)"', gate) == [
        ("counts.artifacts", "$TABLE_ARTIFACTS"),
        ("counts.tables", "$TABLES"),
        ("counts.cells", "$TABLE_CELLS"),
        ("lines.expected", "$TABLE_LINES"),
        ("lines.same", "$TABLE_LINES"),
        ("module_is_recorded", "true"),
    ]


def test_the_recorded_lines_are_the_artifacts_and_tables_the_counts_state() -> None:
    lines = [json.loads(line) for line in read_tables_dump(EXPECTED, STORE)]
    artifacts = [line for line in lines if line[0] == "artifact"]
    found = [line for line in lines if line[0] == "table"]
    counts = STORE["counts"]
    assert len(artifacts) + len(found) == len(lines)
    assert (len(artifacts), len(found)) == (counts["artifacts"], counts["tables"])
    assert sum(line[5] for line in artifacts) == counts["tables"]
    assert sum(line[5] > 0 for line in artifacts) == counts["artifacts_with_tables"]
    assert (sum(line[4] for line in found), sum(line[5] for line in found)) == (
        counts["rows"],
        counts["cells"],
    )
    assert sum(line[8] for line in found) == counts["blocks"]
    names = [line[1] for line in artifacts]
    assert names == sorted(names)
    assert all(name.startswith(("data/", "docs/")) and name.endswith(tableparity.ENDINGS) for name in names)
    # Every table follows the line of its artifact, numbered from nothing.
    at = dict.fromkeys(names, 0)
    for line in found:
        assert line[2] == at[line[1]]
        at[line[1]] += 1
    assert sum(bool(line[3]) for line in found) > len(found) // 2
