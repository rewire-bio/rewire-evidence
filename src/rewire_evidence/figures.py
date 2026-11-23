"""Figures a document prints, tied to the code and data that produced them.

This is `shared/omics/reports/figures.ts`. A measurement is read off a tree at one moment
and written into prose, and when the tree moves the prose still reads as current. The
figures file holds the figures by name beside the digest of every input that produced them,
so a figure that has drifted from its tree can be found without measuring again.

A data file changes on every intake, and a file digest alone would call each one drift. So
beside the digest a data input records the lines it held, one for each record, by the
record's ID and the digest of its line. A record that is gone or changed is drift, and a
file that gained records is not. Those lines are kept beside the JSON in a file of their own,
which `read_figures` folds back in and `write_figures` splits out.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from rewire_evidence.canonical import (
    JsonObject,
    JsonValue,
    js_indented,
    js_number,
    js_parse,
    js_quote,
    sha256_hex,
    utf16_key,
)
from rewire_evidence.js import SPACE_CLASS, paired
from rewire_evidence.jsonl import StoreError, write_text
from rewire_evidence.offsets import to_units

#: The figures of the span resolution measurement.
SPAN_FIGURES: Final = "maintenance/reports/span-resolution-figures.json"
#: What that measurement reads: resolver source, layer source, and the three data inputs.
SPAN_INPUTS: Final = (
    "shared/omics/spans",
    "shared/omics/locator.ts",
    "shared/omics/layers",
    "shared/archive/layers",
    "shared/archive/layers/grid",
    "shared/archive/layers/office",
    "scripts/omics/spans/cli.ts",
    "data/omics/layers/registry.jsonl",
    "data/entities/sources.jsonl",
    "data/evidence/results",
)
#: Hex digits kept of a line's digest.
KEY_DIGEST: Final = 12
#: How many records a report of drift names before it counts the rest.
NAMED: Final = 3
#: How far before a mark its number is looked for, in units.
_BEFORE_A_MARK: Final = 32

_S: Final = f"[{SPACE_CLASS}]"
_MARK: Final = re.compile(rf"<!--{_S}*figure:([a-z0-9_.]+){_S}*-->", re.IGNORECASE | re.ASCII)
_PRINTED: Final = re.compile(rf"([0-9][0-9,]*(?:\.[0-9]+)?){_S}*(?:percent|%|-fold)?[{SPACE_CLASS})\]]*\Z")
_PLAIN: Final = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_THOUSANDS: Final = re.compile(r"\B(?=(?:[0-9]{3})+(?![0-9]))", re.ASCII)
_BREAKS: Final = re.compile(r"[\t\n]")


@dataclass(frozen=True)
class FigureInput:
    """One file a measurement read: its path, its digest, and its records where it is a file of them."""

    path: str
    sha256: str
    #: Each record the file held, by its key and the digest of its line, or `None` for source.
    records: tuple[tuple[str, str], ...] | None = None
    #: Lines the file held, as the JSON states them. `None` for source.
    lines: int | None = None

    def row(self) -> JsonObject:
        """The input as the JSON holds it, which is without its records."""
        row: JsonObject = {"path": self.path, "sha256": self.sha256}
        if self.lines is not None:
            row["lines"] = self.lines
        return row


@dataclass(frozen=True)
class FigureFile:
    """A figures file: the command, the day, the documents that print the figures, and both lists."""

    command: str
    produced: str
    documents: tuple[str, ...]
    inputs: tuple[FigureInput, ...]
    #: Every figure by name. A count is an integer and a percentage has one decimal.
    figures: Mapping[str, float]


def records_file_for(figures_file: str) -> str:
    """The file a figures file keeps its measured records in: the same name, ending `-records.tsv`."""
    return (
        f"{figures_file.removesuffix('.json')}-records.tsv"
        if figures_file.endswith(".json")
        else figures_file
    )


def _text(path: Path) -> str:
    """A file as Node reads it as text."""
    return path.read_bytes().decode("utf-8", "replace")


def records_of(path: Path) -> tuple[tuple[str, str], ...] | None:
    """One line for each record of a JSONL file, in the file's order, or `None` for any other file.

    A record is keyed on its `id`. A line with none is keyed on its own digest. A key held
    twice is numbered. Raises `StoreError` for a line that is not JSON, where the TypeScript
    throws.
    """
    if not path.name.endswith(".jsonl"):
        return None
    text = _text(path)
    if not text:
        return ()
    seen: dict[str, int] = {}
    rows = []
    for line in text.removesuffix("\n").split("\n"):
        digest = sha256_hex(line)[:KEY_DIGEST]
        try:
            held = js_parse(line)
        except ValueError as error:
            raise StoreError(f"{path} holds a line that is not JSON: {error}") from error
        if held is None:
            raise StoreError(f"{path} holds a line that is null")
        named = held.get("id") if isinstance(held, dict) else None
        key = named if isinstance(named, str) and named else digest
        before = seen.get(key, 0)
        seen[key] = before + 1
        rows.append((f"{key}#{before + 1}" if before else key, digest))
    return tuple(rows)


def _input(path: str, file: Path) -> FigureInput:
    rows = records_of(file)
    return FigureInput(path, sha256_hex(file.read_bytes()), rows, None if rows is None else len(rows))


def input_files(root: Path, inputs: Sequence[str] = SPAN_INPUTS) -> list[FigureInput]:
    """Every file under the inputs, with its digest, in path order.

    A folder gives the `.ts` and `.jsonl` files directly in it. Each file is listed once
    however many inputs reach it.
    """
    found: dict[str, FigureInput] = {}
    for entry in inputs:
        full = root / entry
        if not full.exists():
            continue
        if full.is_dir():
            for name in sorted((held.name for held in full.iterdir()), key=utf16_key):
                if (full / name).is_file() and name.endswith((".ts", ".jsonl")):
                    found[f"{entry}/{name}"] = _input(f"{entry}/{name}", full / name)
        else:
            found[entry] = _input(entry, full)
    return sorted(found.values(), key=lambda held: utf16_key(held.path))


def input_digest(inputs: Sequence[FigureInput]) -> str:
    """One digest over all the inputs, for a caller that needs a single value."""
    return sha256_hex("\n".join(f"{held.path} {held.sha256}" for held in inputs))


def _names(rows: Sequence[str]) -> str:
    rest = f", and {len(rows) - NAMED} more" if len(rows) > NAMED else ""
    return ", ".join(rows[:NAMED]) + rest


@dataclass(frozen=True)
class Changed:
    """An input that is not what a measurement read: its path, what it was, and what it is now."""

    path: str
    was: str
    now: str


def _records_changed(
    recorded: FigureInput, records: Sequence[tuple[str, str]], now: FigureInput, root: Path
) -> Changed | None:
    held = dict(now.records if now.records is not None else records_of(root / recorded.path) or ())
    gone = [key for key, _ in records if key not in held]
    edited = [key for key, digest in records if key in held and held[key] != digest]
    if not gone and not edited:
        return None
    parts = []
    if edited:
        parts.append(
            f"{len(edited)} measured {'record' if len(edited) == 1 else 'records'} changed: {_names(edited)}"
        )
    if gone:
        parts.append(
            f"{len(gone)} measured {'record is' if len(gone) == 1 else 'records are'} gone: {_names(gone)}"
        )
    return Changed(recorded.path, f"{len(records)} records measured", "; ".join(parts))


def changed_inputs(recorded: Sequence[FigureInput], now: Sequence[FigureInput], root: Path) -> list[Changed]:
    """Which inputs are not what a measurement read.

    A source file is compared by its digest. A data file is compared record by record, so a
    file that only gained records is not reported.
    """
    after = {held.path: held for held in now}
    before = {held.path for held in recorded}
    changed: list[Changed] = []
    for held in recorded:
        current = after.get(held.path)
        if current is None:
            changed.append(Changed(held.path, held.sha256, "absent"))
        elif current.sha256 != held.sha256:
            if held.records is None:
                changed.append(Changed(held.path, held.sha256, current.sha256))
            else:
                moved = _records_changed(held, held.records, current, root)
                changed += [] if moved is None else [moved]
    changed += [Changed(held.path, "absent", held.sha256) for held in now if held.path not in before]
    return sorted(changed, key=lambda held: utf16_key(held.path))


def read_figures(root: Path, file: str = SPAN_FIGURES) -> FigureFile:
    """Read a figures file, with the records each data input was measured over folded in.

    Raises `StoreError` for a file that is not a figures file, and for a records file that
    names a record before any file.
    """
    try:
        content = js_parse(_text(root / file))
    except (OSError, ValueError) as error:
        raise StoreError(f"{file} cannot be read: {error}") from error
    beside = root / records_file_for(file)
    by_path: dict[str, list[tuple[str, str]]] = {}
    section: list[tuple[str, str]] | None = None
    for line in _text(beside).split("\n") if beside.exists() else ():
        if not line:
            continue
        if line.startswith("# "):
            section = by_path[line[2:]] = []
            continue
        key, _, rest = line.partition("\t")
        if section is None:
            raise StoreError(f"{records_file_for(file)} names a record before any file: {key}")
        section.append((key, rest.partition("\t")[0]))
    try:
        held = cast("JsonObject", content)
        inputs = tuple(
            FigureInput(
                cast("str", row["path"]),
                cast("str", row["sha256"]),
                tuple(by_path[row["path"]]) if row["path"] in by_path else None,
                cast("int | None", row.get("lines")),
            )
            for row in cast("list[JsonObject]", held["inputs"])
        )
        return FigureFile(
            cast("str", held["command"]),
            cast("str", held["produced"]),
            tuple(cast("list[str]", held["documents"])),
            inputs,
            dict(cast("dict[str, float]", held["figures"])),
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise StoreError(f"{file} is not a figures file") from error


def figures_text(content: FigureFile) -> str:
    """The JSON of a figures file, as `JSON.stringify` writes it with two spaces."""
    held: JsonObject = {
        "command": content.command,
        "produced": content.produced,
        "documents": list(content.documents),
        "inputs": [held.row() for held in content.inputs],
        "figures": cast("JsonValue", dict(content.figures)),
    }
    return f"{js_indented(held)}\n"


def records_text(content: FigureFile) -> str | None:
    """The records file beside it: a `# path` line for each data input, then its records. `None` for none.

    Raises `StoreError` for a record whose key holds a tab or a newline.
    """
    with_records = [held for held in content.inputs if held.records is not None]
    for held in with_records:
        for key, _ in held.records or ():
            if _BREAKS.search(key):
                raise StoreError(
                    f"{held.path} holds a record whose ID has a tab or newline in it: {js_quote(key)}"
                )
    if not with_records:
        return None
    sections = (
        "\n".join((f"# {held.path}", *(f"{key}\t{digest}" for key, digest in held.records or ())))
        for held in with_records
    )
    return "\n".join(sections) + "\n"


def write_figures(root: Path, content: FigureFile, file: str = SPAN_FIGURES) -> None:
    """Write a figures file, and the records it was measured over beside it."""
    beside = root / records_file_for(file)
    records = records_text(content)
    if records is None:
        beside.unlink(missing_ok=True)
    else:
        write_text(beside, records)
    write_text(root / file, figures_text(content))


@dataclass(frozen=True)
class FigureMark:
    """A figure tagged in a document: its name, the number printed before the tag, and its line."""

    figure: str
    #: The number the document prints for it, as written, or no text where none is found.
    printed: str
    #: Counted from 1.
    line: int


def _marks(units: str) -> list[tuple[FigureMark, int]]:
    found = []
    for mark in _MARK.finditer(units):
        number = _PRINTED.search(units[max(0, mark.start() - _BEFORE_A_MARK) : mark.start()])
        found.append(
            (
                FigureMark(mark[1], number[1] if number else "", units.count("\n", 0, mark.start()) + 1),
                mark.start(),
            )
        )
    return found


def marks(text: str) -> list[FigureMark]:
    """Every tagged figure in a document. A tag is `<!-- figure:name -->` right after the number it names."""
    return [mark for mark, _ in _marks(to_units(text))]


def prints_figure(printed: str, value: float) -> bool:
    """Whether a printed number is the measured value. Thousands separators are allowed."""
    plain = printed.replace(",", "")
    return bool(_PLAIN.fullmatch(plain)) and float(plain) == value


def _spell(value: float, like: str) -> str:
    """A measured value spelled as the document spelled the one it replaces."""
    plain = js_number(value)
    if "," not in like or not float(value).is_integer():
        return plain
    return _THOUSANDS.sub(",", plain)


def retag(text: str, figures: Mapping[str, float]) -> tuple[str, list[FigureMark], list[FigureMark]]:
    """Rewrite each tagged number that has drifted to its measured value.

    The number is replaced where it stands and the tag is left. What comes back is the text,
    the tags whose numbers were rewritten, and the tags that name a figure the file does not
    hold.
    """
    units = to_units(text)
    changed: list[FigureMark] = []
    unknown: list[FigureMark] = []
    out: list[str] = []
    read = 0
    for mark, marked in _marks(units):
        value = figures.get(mark.figure)
        if value is None:
            unknown.append(mark)
            continue
        if not mark.printed or prints_figure(mark.printed, value):
            continue
        # The number is looked for from the start of the mark's own line, as the TypeScript does.
        at = units.find(mark.printed, units.rfind("\n", 0, marked) + 1)
        if at < 0:
            continue
        out += [units[read:at], _spell(value, mark.printed)]
        read = at + len(mark.printed)
        changed.append(mark)
    return paired("".join((*out, units[read:]))), changed, unknown
