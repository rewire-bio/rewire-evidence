"""Hold the table reader to what the TypeScript reads out of every artifact the data repository commits.

No committed file says what `scripts/omics/extract/tables.ts` reads: its tables go into the
extractors and what they write is records. So the oracle is a recording.
`scripts/fixtures/record_tables_fixture.mjs` runs the TypeScript in CI over every HTML and
XML file the pinned commit holds under `data/` and `docs/`, the files named `.html`,
`.html.gz` or `.xml.gz`, and writes one line for each file and one for each table in it.

A file's line holds the SHA-256 of its bytes, its length as text, the SHA-256 of its visible
text as `text` gives it, and how many tables `tables` finds, or the message either threw. A
table's line holds its caption, its rows and cells counted, and the SHA-256 of its rows, of
its rows with `join_small_caps` run on every cell, and of what `headed_blocks` makes of it.

The gate writes the same lines from a checkout and requires each, then the counts, and
that `tables.ts` in the checkout is the file the patterns were read out of.
"""

from __future__ import annotations

import gzip
import json
import os
import zlib
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rewire_evidence.canonical import JsonObject, JsonValue, js_stringify, js_utf8, sha256_hex, utf16_key
from rewire_evidence.checkparity import Lines, compare_lines, read_dump
from rewire_evidence.conformance import differences, recorded_ref
from rewire_evidence.js import units
from rewire_evidence.jsonl import StoreError
from rewire_evidence.tables import Table, TableError, headed_blocks, join_small_caps, tables, text

#: The file the reader was ported from, in a checkout of the data repository.
MODULE = "scripts/omics/extract/tables.ts"
#: The folders the data repository commits artifacts and documents under.
FOLDERS = ("data", "docs")
#: What the name of an HTML or XML artifact ends with.
ENDINGS = (".html", ".html.gz", ".xml.gz")


@dataclass
class TableCounts:
    """How much the reader read."""

    artifacts: int = 0
    artifacts_with_tables: int = 0
    artifacts_refused: int = 0
    tables: int = 0
    rows: int = 0
    cells: int = 0
    blocks: int = 0


@dataclass
class TableReport:
    """What the table gate found."""

    store_ref: str = ""
    counts: TableCounts = field(default_factory=TableCounts)
    lines: Lines = field(default_factory=Lines)
    #: Whether `tables.ts` in the checkout is the file the recording was made from.
    module_is_recorded: bool = False
    problems: list[str] = field(default_factory=list)


def artifact_files(root: Path) -> list[str]:
    """Every HTML and XML artifact under the folders, as paths from the checkout, in JavaScript's order."""
    found: list[str] = []
    for folder in FOLDERS:
        for directory, _, names in os.walk(root / folder):
            found.extend(
                (Path(directory) / name).relative_to(root).as_posix()
                for name in names
                if name.endswith(ENDINGS) and (Path(directory) / name).is_file()
            )
    return sorted(found, key=utf16_key)


def source_of(file: str, data: bytes) -> str:
    """The text of an artifact as Node reads it: unpacked if it is gzip, U+FFFD where it is not UTF-8."""
    if file.endswith(".gz"):
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError, zlib.error) as error:
            raise StoreError(f"{file} cannot be unpacked: {error}") from error
    return data.decode("utf-8", "replace")


def _digest(value: JsonValue) -> str:
    """The SHA-256 of a value as `JSON.stringify` writes it."""
    return sha256_hex(js_utf8(js_stringify(value)))


def _read[T](read: Callable[[], T]) -> T | JsonObject:
    """What the reader gives, or the message it refuses with, as the recorder writes one."""
    try:
        return read()
    except TableError as error:
        return {"error": str(error)}


def table_line(file: str, index: int, table: Table, counts: TableCounts) -> bytes:
    """The line of one table, which is counted as it is written."""
    rows: list[JsonValue] = [list(row) for row in table.rows]
    joined: list[JsonValue] = [[join_small_caps(cell) for cell in row] for row in table.rows]
    blocks: list[JsonValue] = [asdict(block) for block in headed_blocks(table)]
    cells = sum(len(row) for row in table.rows)
    counts.tables += 1
    counts.rows += len(table.rows)
    counts.cells += cells
    counts.blocks += len(blocks)
    line: list[JsonValue] = [
        "table",
        file,
        index,
        table.caption,
        len(table.rows),
        cells,
        _digest(rows),
        _digest(joined),
        len(blocks),
        _digest(blocks),
    ]
    return js_utf8(js_stringify(line))


def artifact_lines(file: str, data: bytes, counts: TableCounts) -> list[bytes]:
    """The line of one artifact and the line of each table in it."""
    source = source_of(file, data)
    visible = _read(lambda: text(source))
    found = _read(lambda: tables(source))
    counts.artifacts += 1
    if isinstance(found, dict):
        counts.artifacts_refused += 1
    elif found:
        counts.artifacts_with_tables += 1
    line: list[JsonValue] = [
        "artifact",
        file,
        sha256_hex(data),
        units(source),
        visible if isinstance(visible, dict) else sha256_hex(js_utf8(visible)),
        found if isinstance(found, dict) else len(found),
    ]
    made = [js_utf8(js_stringify(line))]
    if isinstance(found, list):
        made.extend(table_line(file, index, table, counts) for index, table in enumerate(found))
    return made


def _bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise StoreError(f"{path} cannot be read: {error}") from error


def verify_tables(root: Path, recorded: JsonObject, dump: Sequence[bytes]) -> TableReport:
    """Read every committed artifact under a checkout and hold each line to the recording.

    Raises `StoreError` for a checkout or a recording that cannot be read. Every
    disagreement is a problem in the report.
    """
    found = TableReport(store_ref=recorded_ref(recorded))
    module = recorded.get("module")
    stated = module.get("sha256") if isinstance(module, dict) else None
    found.module_is_recorded = sha256_hex(_bytes(root / MODULE)) == stated
    if not found.module_is_recorded:
        found.problems.append(f"{MODULE} in the checkout is not the file the recording was made from")
    made: list[bytes] = []
    for file in artifact_files(root):
        made.extend(artifact_lines(file, _bytes(root / file), found.counts))
    found.lines = compare_lines(made, dump, "a committed artifact as the reader reads it", found.problems)
    counts: JsonObject = dict(asdict(found.counts))
    found.problems.extend(differences(counts, recorded.get("counts"), "counts"))
    found.problems.extend(differences(list(FOLDERS), recorded.get("folders"), "folders"))
    return found


def read_tables_dump(expected: Path, recorded: JsonObject) -> list[bytes]:
    """The recorded lines, from the file the expectation names beside itself."""
    return read_dump(expected, recorded, "dump")


def summary(found: TableReport) -> str:
    """The report as the lines the gate prints."""
    counts, lines = found.counts, found.lines
    return "\n".join(
        [
            f"the store at {found.store_ref}: {counts.artifacts:,} committed HTML and XML artifacts",
            f"  {MODULE} is the file recorded: {'yes' if found.module_is_recorded else 'no'}",
            f"  artifacts with a table: {counts.artifacts_with_tables:,}",
            f"  artifacts refused: {counts.artifacts_refused:,}",
            f"  tables read: {counts.tables:,}, with {counts.rows:,} rows and {counts.cells:,} cells",
            f"  headed blocks found in them: {counts.blocks:,}",
            f"  lines the TypeScript recorded, reproduced: {lines.same:,} of {lines.expected:,}",
        ]
    )


def to_json(found: TableReport) -> str:
    """The report as JSON, for CI to read counts from."""
    return json.dumps(
        {
            "store_ref": found.store_ref,
            "module_is_recorded": found.module_is_recorded,
            "counts": asdict(found.counts),
            "lines": found.lines.row(),
            "problems": found.problems,
        },
        indent=1,
        ensure_ascii=False,
    )
