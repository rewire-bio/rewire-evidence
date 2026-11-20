r"""Reading and writing the store's line files without disturbing their bytes.

Every file in the store is JSON Lines: one object per line, each line ended by a single
`\n`. The TypeScript reads one with `text.split("\n")` and nothing cleverer, and that is the
rule here too. `str.splitlines()` and text-mode iteration are both wrong for this, because
they also break on `\r`, U+2028 and U+2029, and `JSON.stringify` writes the last two inside
a string without escaping them. A line holding one would be cut in half.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterable
from pathlib import Path

from rewire_evidence.canonical import JsonObject, js_parse


class StoreError(Exception):
    """The store on disk is not in the shape its layout defines."""


def read_lines(path: Path) -> list[str]:
    """The non-empty lines of a file, as `readLines` gives them in `scripts/omics/records.ts`.

    Empty lines are dropped, as the TypeScript drops them, so this reader is more forgiving
    than the writer. A gate that wants to know the file is exactly what the writer would
    produce compares whole files, which this function does not do.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise StoreError(f"{path} is not UTF-8: {error}") from error
    return [line for line in text.split("\n") if line]


def parse_row(line: str, where: str) -> JsonObject:
    """One line as the object it holds. `where` names the line in the error."""
    try:
        value = js_parse(line)
    except ValueError as error:
        raise StoreError(f"{where} is not JSON: {error}") from error
    if not isinstance(value, dict):
        raise StoreError(f"{where} is not a JSON object")
    return value


def read_rows(path: Path) -> list[JsonObject]:
    """Every row of a file, in file order."""
    return [parse_row(line, f"{path}:{number}") for number, line in enumerate(read_lines(path), 1)]


def text_field(row: JsonObject, field: str, where: str) -> str:
    """A field the layout depends on, which has to be a string for the row to be placed."""
    value = row.get(field)
    if not isinstance(value, str):
        raise StoreError(f"{where} has no string {field!r}")
    return value


def join_lines(lines: Iterable[str]) -> str:
    """A file body: each line ended by a newline, and an empty file for no lines."""
    return "".join(f"{line}\n" for line in lines)


def write_text(path: Path, text: str) -> None:
    """Write a file as the TypeScript does: the folder made if missing, bytes as given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def write_text_atomically(path: Path, text: str) -> None:
    """Write through a temporary file and a rename, as the manifest writer does.

    An interrupted run leaves either the old file or the new one, never part of one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    temporary.write_bytes(text.encode("utf-8"))
    temporary.replace(path)


def append_text(path: Path, text: str) -> None:
    """Add to the end of a file in one write, so an interrupted run leaves whole lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(text.encode("utf-8"))
