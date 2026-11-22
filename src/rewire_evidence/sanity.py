"""The sanity gate: whether a layer can be used as an extraction substrate.

This is `shared/omics/layers/sanity.ts`. Every metric on a registry row is a count over the
layer text in characters, so a regenerated row is the same bytes whatever a float formatter
does. The ratios and the verdict are worked out from the counts.

The thresholds are the TypeScript's, each set there from the layers the archive produces.
A layer with no content, one whose printable share is under 0.98, one that is more than a
hundredth U+FFFD, one that is heavy in punctuation and holds no digits, and a PDF layer whose
form feeds are not its page count all come out `fail`, which no record may cite.

Which characters are letters, digits and white space follows the Unicode version Node was
built with, so the three sets are the recorded ones `jsregex` reads and not Python's own.
"""

from __future__ import annotations

import bisect
import functools
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from rewire_evidence import jsregex
from rewire_evidence.canonical import JsonObject
from rewire_evidence.js import fixed

SANITY_VERDICTS: Final = ("pass", "suspect", "fail")
#: A verdict's place among them, best first.
RANK: Final[Mapping[str, int]] = {"pass": 0, "suspect": 1, "fail": 2}

PRINTABLE_FAIL: Final = 0.98
PRINTABLE_SUSPECT: Final = 0.999
REPLACEMENT_FAIL: Final = 0.01
MOJIBAKE_PUNCTUATION: Final = 0.25
MOJIBAKE_DIGITS: Final = 0.005
MOJIBAKE_CHARACTERS: Final = 1000

#: Every threshold under the name the TypeScript gives it, as `layers -- thresholds` prints them.
THRESHOLDS: Final[Mapping[str, str]] = {
    "printableFail": "0.98",
    "printableSuspect": "0.999",
    "replacementFail": "0.01",
    "mojibakePunctuation": "0.25",
    "mojibakeDigits": "0.005",
    "mojibakeCharacters": "1000",
}

#: C0 controls other than tab, newline, carriage return and form feed, and DEL and the C1 controls.
_CONTROLS: Final = re.compile("[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")
_PUNCTUATION_RUN: Final = re.compile("p+")
#: The patterns `metricsOf` tests each character with, all under the `u` flag.
PATTERNS: Final = (
    jsregex.JsPattern(r"\s", "u"),
    jsregex.JsPattern(r"\p{Nd}", "u"),
    jsregex.JsPattern(r"\p{L}", "u"),
)


class _Kinds(dict[int, str]):
    """What each character counts as: `s` for space, `d` for digit, `l` for letter, `p` for neither."""

    def __init__(self) -> None:
        super().__init__()
        self.sets = [
            (kind, [first for first, _ in ranges], [last for _, last in ranges])
            for kind, ranges in zip(
                "sdl", (jsregex.recorded("u", held.source) for held in PATTERNS), strict=True
            )
        ]

    def __missing__(self, code: int) -> str:
        found = "p"
        for kind, firsts, lasts in self.sets:
            at = bisect.bisect_right(firsts, code) - 1
            if at >= 0 and code <= lasts[at]:
                found = kind
                break
        self[code] = found
        return found


@functools.cache
def _kinds() -> _Kinds:
    return _Kinds()


def metrics_of(text: str, pdf_pages: int | None = None) -> JsonObject:
    """Count what the gate reads, in the order the registry row holds the counts."""
    kinds = text.translate(_kinds())
    digits = kinds.count("d")
    letters = kinds.count("l")
    spaces = kinds.count("s")
    return {
        "characters": len(text),
        "non_space": len(text) - spaces,
        "replacement": text.count("�"),
        "printable": len(text) - len(_CONTROLS.findall(text)),
        "digits": digits,
        "letters": letters,
        "punctuation": len(text) - spaces - digits - letters,
        "longest_punctuation_run": max(map(len, _PUNCTUATION_RUN.findall(kinds)), default=0),
        "page_breaks": text.count("\f"),
        "pdf_pages": pdf_pages,
    }


@dataclass(frozen=True)
class Ratios:
    """The ratios the thresholds are written in."""

    printable: float
    replacement: float
    punctuation: float
    digits: float


def _count(metrics: Mapping[str, object], name: str) -> int:
    held = metrics[name]
    if not isinstance(held, int):
        raise TypeError(f"the metric {name} is not a count")
    return held


def ratios_of(metrics: Mapping[str, object]) -> Ratios:
    """The printable, replacement and punctuation shares of the layer, and digits among alphanumerics."""
    characters = _count(metrics, "characters")
    digits = _count(metrics, "digits")
    alphanumeric = digits + _count(metrics, "letters")
    return Ratios(
        printable=_count(metrics, "printable") / characters if characters else 0,
        replacement=_count(metrics, "replacement") / characters if characters else 0,
        punctuation=_count(metrics, "punctuation") / characters if characters else 0,
        digits=digits / alphanumeric if alphanumeric else 0,
    )


@dataclass(frozen=True)
class Sanity:
    """The verdict for one layer, with one line per property the gate did not find, worst first."""

    verdict: str
    reasons: list[str]


def sanity_of(metrics: Mapping[str, object], normaliser: str) -> Sanity:
    """The verdict for one layer. `normaliser` decides whether the page-break property applies."""
    ratios = ratios_of(metrics)
    characters = _count(metrics, "characters")
    non_space = _count(metrics, "non_space")
    replacement = _count(metrics, "replacement")
    page_breaks = _count(metrics, "page_breaks")
    pdf_pages = metrics["pdf_pages"]
    fail: list[str] = []
    suspect: list[str] = []

    # A layer with no content cannot carry a span.
    if non_space == 0:
        fail.append(f"the layer holds {characters} characters and none of them is content")

    # Control characters where text should be.
    if non_space > 0 and ratios.printable < PRINTABLE_FAIL:
        fail.append(f"printable characters are {fixed(ratios.printable, 4)} of the layer, under 0.98")
    elif non_space > 0 and ratios.printable < PRINTABLE_SUSPECT:
        suspect.append(f"printable characters are {fixed(ratios.printable, 4)} of the layer, under 0.999")

    # Bytes the decoder could not read.
    if ratios.replacement > REPLACEMENT_FAIL:
        fail.append(f"U+FFFD is {fixed(ratios.replacement, 4)} of the layer, over 0.01")
    elif replacement > 0:
        noun = "character" if replacement == 1 else "characters"
        suspect.append(f"the layer carries {replacement} U+FFFD {noun}")

    # Punctuation where the numbers were.
    if (
        characters >= MOJIBAKE_CHARACTERS
        and ratios.punctuation > MOJIBAKE_PUNCTUATION
        and ratios.digits < MOJIBAKE_DIGITS
    ):
        fail.append(
            f"punctuation is {fixed(ratios.punctuation, 4)} of the layer and digits are "
            f"{fixed(ratios.digits, 4)} of its alphanumerics, "
            "the signature of a font mapping that rendered the numbers as punctuation"
        )

    # A page of the PDF for every page break.
    if normaliser.startswith("pdf-text-layer@") and pdf_pages is not None and page_breaks != pdf_pages:
        fail.append(f"the layer carries {page_breaks} page breaks and pdfinfo reports {pdf_pages} pages")

    if fail:
        return Sanity("fail", [*fail, *suspect])
    return Sanity("suspect", suspect) if suspect else Sanity("pass", [])


def usable(verdict: str) -> bool:
    """Whether a layer may be cited: no record, claim or candidate may cite one at `fail`."""
    return verdict != "fail"
