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
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from rewire_evidence.canonical import JsonInput, JsonObject, JsonValue, js_parse, js_value


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
    except OSError as error:
        raise StoreError(f"{path} cannot be read: {error}") from error
    return [line for line in text.split("\n") if line]


def parse_line(line: str, where: str) -> JsonValue:
    """One line as the value `JSON.parse` reads from it. `where` names the line in the error."""
    try:
        return js_parse(line)
    except ValueError as error:
        raise StoreError(f"{where} is not JSON: {error}") from error
    except RecursionError as error:
        raise StoreError(f"{where} is nested too deeply to read") from error


def parse_value(line: str, where: str) -> JsonValue:
    """One line as the value JavaScript holds once `JSON.parse` has read it: `js_value` of it.

    The parser and the walk that follows it give up at different depths, so a line the
    parser reads may still be nested too deeply to walk. Both are refused in the same words.
    """
    try:
        return js_value(parse_line(line, where))
    except RecursionError as error:
        raise StoreError(f"{where} is nested too deeply to read") from error


def parse_row(line: str, where: str) -> JsonObject:
    """One line as the object it holds. `where` names the line in the error."""
    value = parse_line(line, where)
    if not isinstance(value, dict):
        raise StoreError(f"{where} is not a JSON object")
    return value


def read_rows(path: Path) -> list[JsonObject]:
    """Every row of a file, in file order."""
    return [parse_row(line, f"{path}:{number}") for number, line in enumerate(read_lines(path), 1)]


def read_values(path: Path) -> list[JsonValue]:
    """Every line of a file as the value it holds, an object or not: `readJsonl`.

    Each is in the one form JavaScript holds it in, so two numbers that are one number to
    JavaScript are one here.
    """
    return [parse_value(line, f"{path}:{number}") for number, line in enumerate(read_lines(path), 1)]


def text_field(row: object, field: str, where: str) -> str:
    """A field the layout depends on, which has to be a string for the row to be placed.

    The row is taken as whatever the caller holds and checked here, because a writer is
    handed parsed JSON, and a list or a null where a row belongs is refused by name too.
    """
    if not isinstance(row, Mapping):
        raise StoreError(f"{where} is not a JSON object")
    value = row.get(field)
    if not isinstance(value, str):
        raise StoreError(f"{where} has no string {field!r}")
    return value


def serialise_row(serialise: Callable[[Mapping[str, JsonInput]], str], row: object, where: str) -> str:
    """One row as its line, with anything that stops it being written named by `where`.

    The serialisers raise what Python raises: `ValueError` for a number JavaScript would
    write as null, `TypeError` for a value with no JSON form, `RecursionError` for nesting
    too deep to walk. A writer and a gate both need those as a named row and not a traceback.
    """
    if not isinstance(row, Mapping):
        raise StoreError(f"{where} is not a JSON object")
    try:
        return serialise(row)
    except RecursionError as error:
        raise StoreError(f"{where} cannot be written: it is nested too deeply") from error
    except (TypeError, ValueError) as error:
        raise StoreError(f"{where} cannot be written: {error}") from error


def join_lines(lines: Iterable[str]) -> str:
    """A file body: each line ended by a newline, and an empty file for no lines."""
    return "".join(f"{line}\n" for line in lines)


def write_text(path: Path, text: str) -> None:
    """Write a file as the TypeScript does: the folder made if missing, bytes as given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def write_bytes_atomically(path: Path, data: bytes) -> None:
    """Write raw bytes through a temporary file and a rename, leaving no partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def write_text_atomically(path: Path, text: str) -> None:
    """Write through a temporary file and a rename, as the manifest writer does.

    An interrupted run leaves either the old file or the new one, never part of one.
    """
    write_bytes_atomically(path, text.encode("utf-8"))


def append_text(path: Path, text: str) -> None:
    """Add to the end of a file in one write, so an interrupted run leaves whole lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(text.encode("utf-8"))
