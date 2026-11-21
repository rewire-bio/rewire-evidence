r"""A printed table cell, read as the extractors read it: `parseCell` of `scripts/omics/extract/tables.ts`.

`parse_cell` splits a printed cell into its number and its spread. It is the parser check C3
holds every stored `printed_value` to, and the one the extractors read cells with, so what it
accepts and what it refuses are both part of what the store means.

The patterns are the TypeScript's, run through `jsregex` so each matches what Node matches.
Two changes are made to the text of each, neither of which changes what it matches:

- A group JavaScript numbers is given a name here, because `jsregex` runs only named groups.
- A group that is only tested, never read, is written as one that captures nothing.

`asciiDigits` is a replace with a function under the `u` flag, which maps each mathematical
digit, U+1D7CE to U+1D7FF, to the digit it stands for. It is written here over whole
characters, with no pattern: half a character is not in the range under the `u` flag, and is
left as it is.

Only the cell half of `tables.ts` is here. Its reader of HTML and JATS tables is not ported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from rewire_evidence.canonical import js_number, js_quote
from rewire_evidence.js import number_of_text, paired, trim, unit_text
from rewire_evidence.jsregex import JsPattern

_FIRST_DIGIT = 0x1D7CE
_LAST_DIGIT = 0x1D7FF

#: `NOT_REPORTED`: what a paper prints in a cell it did not fill.
NOT_REPORTED = JsPattern(r"^(?:|-|--|–|—|n\/?a|na|nan|\.|\/)$", "i")
#: Runs of white space, which `parseCell` makes one space of.
SPACES = JsPattern(r"\s+", "g")
#: A number and its spread: "0.31 ± 0.01" and "0.31 (0.01)".
SPREAD = JsPattern(r"^(?<head>[^±(]+?)\s*(?:±|\()\s*(?<tail>[^)]+?)\)?$")
#: The percent sign a cell ends with, which is taken off only for a column labelled percent.
PERCENT = JsPattern(r"%\s*$")
#: The minus sign and the dashes a paper prints for one.
DASHES = JsPattern("[−‐-―]", "g")
#: Thousands separators and white space inside a number.
SEPARATORS = JsPattern(r"[,\s]", "g")
#: Arrows and footnote marks, which are typography and not data.
MARKS = JsPattern("[↑↓*†‡]", "g")
#: "1×10−2", once the dash is a minus sign.
SCIENTIFIC = JsPattern(r"^(?<mantissa>-?\d*\.?\d+)×10\^?\{?(?<exponent>-?\d+)\}?$")
#: A decimal number, with or without an exponent.
PLAIN = JsPattern(r"^(?<number>-?\d*\.?\d+(?:[eE][-+]?\d+)?)$")

#: Every pattern this module runs, for the list of what has to be recorded from Node.
PATTERNS: tuple[JsPattern, ...] = (
    NOT_REPORTED,
    SPACES,
    SPREAD,
    PERCENT,
    DASHES,
    SEPARATORS,
    MARKS,
    SCIENTIFIC,
    PLAIN,
)


class CellError(Exception):
    """A printed cell `parseCell` throws for: its number or its spread cannot be read."""


@dataclass(frozen=True)
class Cell:
    """A printed cell, split into the number and its spread, or nothing at all.

    `sd` is the historical name for the printed spread. What it means statistically has to
    come from the source and is never inferred from the sign printed before it.
    """

    #: The cell with its digits made plain and its white space made single. A cell that is
    #: not reported is kept as it was given.
    printed: str
    #: The number as text, or None for a cell that reports none.
    value: str | None
    sd: str | None


def ascii_digits(text: str) -> str:
    """`asciiDigits(text)`: bold, italic and sans-serif digits as the digits they are."""
    return "".join(
        str((ord(character) - _FIRST_DIGIT) % 10)
        if _FIRST_DIGIT <= ord(character) <= _LAST_DIGIT
        else character
        for character in paired(text)
    )


def is_missing_cell(printed: str) -> bool:
    """`isMissingCell(printed)`: whether the cell is one a paper prints for no value."""
    return NOT_REPORTED.test(trim(unit_text(printed)))


def _number_from(text: str) -> str | None:
    """`numberFrom(text)`, on text in units: the number as text, or None where there is none."""
    clean = DASHES.replace(unit_text(ascii_digits(text)), "-")
    clean = MARKS.replace(SEPARATORS.replace(clean, ""), "")
    scientific = SCIENTIFIC.exec(clean)
    if scientific is not None:
        # One decimal literal and not a multiplication, as the TypeScript has it.
        value = number_of_text(f"{scientific.groups['mantissa']}e{scientific.groups['exponent']}")
        return js_number(value) if math.isfinite(value) else None
    plain = PLAIN.exec(clean)
    if plain is None:
        return None
    number = plain.groups["number"]
    # The pattern always gives the group, and a number it accepts is ASCII.
    return number if number is not None and math.isfinite(number_of_text(number)) else None


def parse_cell(printed: str, unit: str | None = None) -> Cell:
    """`parseCell(printed, { unit })`: one printed cell as its number and its spread.

    A cell the paper did not fill has no value. The percent sign is taken off only when
    `unit` is `percent`. Raises `CellError`, with the TypeScript's message, for a cell
    whose number cannot be read and for a spread that cannot be read or is below zero.
    """
    stripped = trim(SPACES.replace(unit_text(ascii_digits(printed)).replace("±plus-or-minus±", "±"), " "))
    if NOT_REPORTED.test(trim(stripped)):
        return Cell(printed, None, None)
    spread = SPREAD.exec(stripped)
    head = stripped if spread is None else spread.groups["head"] or ""
    tail = None if spread is None else spread.groups["tail"]

    def numeric(part: str) -> str | None:
        return _number_from(PERCENT.replace(part, "") if unit == "percent" else part)

    value = numeric(head)
    if value is None:
        raise CellError(f"Unrecognised numeric cell: {js_quote(printed)}")
    sd = numeric(tail) if tail else None
    if tail and (sd is None or number_of_text(sd) < 0):
        raise CellError(f"Unrecognised uncertainty in cell: {js_quote(printed)}")
    return Cell(paired(stripped), value, sd)
