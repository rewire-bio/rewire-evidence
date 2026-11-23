"""Resolvers for prose, and the rule that finds a stored span again in a layer that moved.

This is `shared/omics/spans/prose.ts` and `reanchor.ts`.

A quote is the strongest locator in the store, because it carries its own answer. The only
work is finding it, and the only question is whether it is there once or more than once. A
sentence printed across two lines of a PDF has a newline and some indentation inside it that
nobody typed, so every run of whitespace in the quote matches any run of whitespace in the
layer, and nothing else is relaxed.

All text is in units. A quote's own characters are matched as they stand, with no flag, so
the pattern is Python's and the whitespace class is JavaScript's written out.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Final

from rewire_evidence.aligned import lower_units, normalise_label
from rewire_evidence.js import SPACE, SPACE_CLASS
from rewire_evidence.jsregex import JsPattern
from rewire_evidence.offsets import LayerIndex, Span, is_high, span_at
from rewire_evidence.regions import shown
from rewire_evidence.resolution import Refusal, Resolution, Resolved, refuse

#: How far past the quote a sentence may run before the resolver gives up looking for its end.
SENTENCE_UNITS: Final = 900
#: A quote shorter than this addresses nothing.
SHORTEST_QUOTE: Final = 4
#: Words that take a full stop and do not end a sentence. Reading past one lengthens the
#: span and never shortens it.
ABBREVIATIONS: Final = frozenset(
    [
        "al",
        "approx",
        "ca",
        "cf",
        "chr",
        "dr",
        "e.g",
        "eq",
        "eqs",
        "est",
        "etc",
        "ext",
        "fig",
        "figs",
        "i.e",
        "no",
        "nos",
        "pp",
        "prof",
        "ref",
        "refs",
        "resp",
        "sec",
        "secs",
        "suppl",
        "supp",
        "tab",
        "tabs",
        "vs",
        "viz",
    ]
)
#: Normalisers that print one block element to a line, so a paragraph is a line.
BLOCK_PER_LINE: Final = frozenset({"europepmc-fulltext-xml@1", "html-text@1"})
#: A heading is no longer than this, unless the name it has to equal is.
HEADING_UNITS: Final = 80

_S: Final = f"[{SPACE_CLASS}]"
_SPACES: Final = re.compile(f"{_S}+")
_BLANK: Final = re.compile(rf"{_S}*\Z")
_OPENS: Final = re.compile(rf"{_S}+[\"'(\u2018\u201c]?[A-Z0-9]")
_NUMBERED: Final = re.compile(rf"\A{_S}*(?:[0-9]+(?:\.[0-9]+)*\.?|[IVXL]+\.){_S}+", re.IGNORECASE | re.ASCII)
_TRAILING_SPACE: Final = re.compile(rf"{_S}+\Z")
#: A character of a word, as the TypeScript's `[\p{L}\p{N}.]` reads one unit.
WORD_UNIT: Final = JsPattern(r"[\p{L}\p{N}.]", "u")
PATTERNS: Final = (WORD_UNIT,)


def flexible(quote: str) -> re.Pattern[str]:
    """A quote with its inner whitespace left free, which is all a line break changes in a sentence."""
    return re.compile(f"{_S}+".join(re.escape(part) for part in _SPACES.split(quote.strip(SPACE))))


def occurrences(text: str, pattern: re.Pattern[str], limit: int) -> list[tuple[int, int]]:
    """The first places a pattern matches, as unit ranges, each search starting past the last match."""
    found: list[tuple[int, int]] = []
    at = 0
    while len(found) < limit and at <= len(text):
        match = pattern.search(text, at)
        if match is None:
            break
        found.append(match.span())
        at = match.start() + max(1, match.end() - match.start())
    return found


def _word_before(text: str, at: int) -> str:
    """The word a full stop follows, in lower case, with its own inner stops kept."""
    start = at
    while start > 0 and WORD_UNIT.test(text[start - 1]):
        start -= 1
    return lower_units(text[start:at])


def _is_digit(unit: str) -> bool:
    return "0" <= unit <= "9"


def sentence_end(text: str, start: int) -> int | None:
    """The end of the sentence that holds `start`, as a unit offset, or `None` where none is near.

    A full stop ends a sentence when a space and a capital letter or a digit follow it, or
    when the text ends. A full stop between two digits is a decimal point. A full stop
    after a known abbreviation does not end a sentence.
    """
    for at in range(start, min(len(text), start + SENTENCE_UNITS)):
        here = text[at]
        if here not in ".!?":
            continue
        if here == ".":
            # Before the first character there is nothing, where Python would read the last one.
            before = text[at - 1] if at > 0 else ""
            if _is_digit(before) and _is_digit(text[at + 1 : at + 2]):
                continue
            if _word_before(text, at) in ABBREVIATIONS:
                continue
        following = text[at + 1 : at + 4]
        if _BLANK.match(following) or _OPENS.match(following):
            return at + 1
    return None


def resolve_quote(index: LayerIndex, quote: str, *, whole: bool = False) -> Resolution:
    """The span of a quoted sentence or phrase.

    `whole` is for a locator that names a sentence and prints only its opening. The span
    then runs from the quote to the end of its sentence, and stays the quote where no end is
    found.
    """
    if len(quote.strip(SPACE)) < SHORTEST_QUOTE:
        return refuse("no_anchor", "a quote under four characters addresses nothing", index.layer_sha256)
    hits = occurrences(index.text, flexible(quote), 8)
    if not hits:
        return refuse("quote_not_found", "the quoted text is not in this layer", index.layer_sha256)
    if len(hits) > 1:
        return refuse(
            "quote_ambiguous",
            f"the quoted text occurs {len(hits)} times or more in this layer",
            index.layer_sha256,
        )
    start, end = hits[0]
    if whole:
        ended = sentence_end(index.text, end)
        end = end if ended is None else ended
    return Resolved("quote", span_at(index, start, end), index.layer_sha256)


def _heading_lines(index: LayerIndex, name: str) -> list[int]:
    """Lines that are a heading for this section name: the whole line is the name, numbered or not."""
    wanted = normalise_label(name)
    bound = max(HEADING_UNITS, len(wanted) + 16)
    found = []
    for line, held in enumerate(index.lines):
        text = held.strip(SPACE)
        if text and len(text) <= bound and normalise_label(_NUMBERED.sub("", text, count=1)) == wanted:
            found.append(line)
    return found


def _heading_shaped(lines: list[str], line: int) -> bool:
    """A line shaped like a heading: short, alone between blank lines, not ending as a sentence does."""
    text = lines[line].strip(SPACE)
    if not text or len(text) > HEADING_UNITS or text[-1] in ".;:,":
        return False
    before = line == 0 or not lines[line - 1].strip(SPACE)
    after = line + 1 >= len(lines) or not lines[line + 1].strip(SPACE)
    return before and after


def _wrapped_paragraphs(index: LayerIndex, heading: int, wanted: float) -> list[tuple[int, int]]:
    """Paragraphs as runs of lines that are not blank, which is what a layout renderer leaves."""
    blocks: list[tuple[int, int]] = []
    opened: int | None = None
    for line in range(heading + 1, len(index.lines)):
        text = index.lines[line].strip(SPACE)
        if text and _heading_shaped(index.lines, line):
            # The line before a line shaped like a heading is blank, so no paragraph is open.
            return blocks
        if not text:
            if opened is not None:
                blocks.append((opened, line - 1))
            opened = None
            if len(blocks) >= wanted:
                return blocks
        elif opened is None:
            opened = line
    if opened is not None and len(blocks) < wanted:
        blocks.append((opened, len(index.lines) - 1))
    return blocks


def _line_paragraphs(index: LayerIndex, heading: int) -> list[tuple[int, int]]:
    """Paragraphs as lines, stopping at the first blank line: what follows one cannot be placed."""
    blocks = []
    for line in range(heading + 1, len(index.lines)):
        if not index.lines[line].strip(SPACE):
            break
        blocks.append((line, line))
    return blocks


def resolve_section_paragraph(index: LayerIndex, section: str, paragraph: float) -> Resolution:
    """The nth paragraph of a named section, counted from 1."""
    layer = index.layer_sha256
    headings = _heading_lines(index, section)
    if not headings:
        return refuse("section_not_found", f'no heading line reads "{section}"', layer)
    if len(headings) > 1:
        return refuse("section_ambiguous", f'{len(headings)} heading lines read "{section}"', layer)
    if not float(paragraph).is_integer() or paragraph < 1:
        return refuse(
            "paragraph_out_of_range", f"paragraph {shown(paragraph)} is not a paragraph number", layer
        )
    per_line = index.normaliser in BLOCK_PER_LINE
    if per_line:
        blocks = _line_paragraphs(index, headings[0])
    else:
        blocks = _wrapped_paragraphs(index, headings[0], paragraph)
    if len(blocks) < paragraph:
        counted = f"{len(blocks)} {'paragraph' if len(blocks) == 1 else 'paragraphs'}"
        said = f"prints {counted} before the first blank line" if per_line else f"holds {counted}"
        return refuse(
            "paragraph_out_of_range",
            f'the section "{section}" {said} and the locator names {shown(paragraph)}',
            layer,
        )
    first, last = blocks[int(paragraph) - 1]
    opening = index.lines[first]
    return Resolved(
        "section_paragraph",
        span_at(
            index,
            index.u16[first] + len(opening) - len(opening.lstrip(SPACE)),
            index.u16[last] + len(_TRAILING_SPACE.sub("", index.lines[last])),
        ),
        layer,
    )


# ---- finding a stored span again -------------------------------------------------------


@dataclass(frozen=True)
class Anchoring:
    """A span that still resolves: where it is now, and what settled that.

    `outcome` is `unchanged` or `relocated`. `evidence` is `offsets` where nothing moved,
    `context` where the text around the quote placed it, `exact` where the quote occurs once,
    and `whitespace` where it does once the whitespace inside it may differ.
    """

    outcome: str
    evidence: str
    span: Span


def _find_all(text: str, needle: str) -> list[int]:
    """The first two places a literal occurs, which is enough to settle once, twice or never."""
    found: list[int] = []
    at = text.find(needle)
    while at >= 0 and len(found) < 2:  # noqa: PLR2004 - once, or more than once
        found.append(at)
        at = text.find(needle, at + 1)
    return found


def unit_of_point(index: LayerIndex, point: int) -> int:
    """`utf16Of`: the unit offset of a character offset, or -1 for one the layer does not hold."""
    points = index.points
    if point < 0 or point > points[-1]:
        return -1
    line = bisect.bisect_right(points, point) - 1
    if line >= len(index.lines):
        return len(index.text)
    unit = index.u16[line]
    remaining = point - points[line]
    text = index.text
    while remaining > 0 and unit < len(text):
        unit += 2 if is_high(text[unit]) and unit + 1 < len(text) else 1
        remaining -= 1
    return -1 if remaining > 0 else unit


def point_slice(index: LayerIndex, start: int, end: int) -> str:
    """`codePointSlice`: the text between two character offsets, or no text for a range that is not there."""
    first = unit_of_point(index, start)
    last = unit_of_point(index, end)
    return "" if first < 0 or last < 0 else index.text[first:last]


def reanchor(span: Span, index: LayerIndex) -> Anchoring | Refusal:
    """Decide whether a span still resolves against this layer, and where.

    The first test that applies decides. The layer is the span's and holds the quote at the
    offsets: unchanged. The quote with its context occurs once: moved to it. The quote
    alone occurs once: moved to it. It occurs more than once: refused. It occurs once with
    its whitespace free: moved, and said to be so. Nothing picks the occurrence nearest the
    old offset.
    """
    layer = index.layer_sha256
    if span.layer_sha256 == layer and point_slice(index, span.start, span.end) == span.exact:
        return Anchoring("unchanged", "offsets", span)
    prefix = span.prefix or ""
    context = f"{prefix}{span.exact}{span.suffix or ''}"
    if context != span.exact:
        hits = _find_all(index.text, context)
        if len(hits) == 1:
            start = hits[0] + len(prefix)
            return Anchoring("relocated", "context", span_at(index, start, start + len(span.exact)))
    literal = _find_all(index.text, span.exact)
    if len(literal) == 1:
        return Anchoring("relocated", "exact", span_at(index, literal[0], literal[0] + len(span.exact)))
    if literal:
        return refuse(
            "quote_ambiguous", "the quote occurs more than once and its context does not pin it down", layer
        )
    loose = occurrences(index.text, flexible(span.exact), 2)
    if len(loose) == 1:
        return Anchoring("relocated", "whitespace", span_at(index, *loose[0]))
    if loose:
        return refuse(
            "quote_ambiguous", "the quote occurs more than once once whitespace is allowed to differ", layer
        )
    return refuse("quote_not_found", "the quote is not in this layer", layer)
