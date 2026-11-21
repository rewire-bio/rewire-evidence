r"""What a description may say: `shared/omics/descriptions.ts`.

The description of a model, method, pipeline, service or configuration says what it does.
Where a record came from and how a source printed it belong in `attributes.provenance_note`.
`description_problems` finds descriptions that are placeholders, carry provenance or citation
locators, or repeat a differently named entity's description word for word.

The rules are JavaScript patterns, and three things about them are JavaScript's. `\b` and the
`i` flag know ASCII letters only, so U+017F is not an `s`. `.` stops at four line terminators
and `\s` is JavaScript's white space. And a pattern counts UTF-16 units, so the text is
matched in that form. `tests/fixtures/validation.json` holds each pattern's source as the
TypeScript declares it and what the rules make of a list of descriptions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from rewire_evidence.canonical import JsonObject, JsonValue
from rewire_evidence.js import DOT, SPACE_CLASS, trim, unit_text
from rewire_evidence.schema import Record, RecordError

DESCRIBED_KINDS = ("model", "method", "pipeline", "service", "configuration")
#: Kinds whose descriptions must also differ between differently named records.
DISTINCT_KINDS = ("model", "method", "pipeline", "service")
#: How many problems a refusal lists before it stops.
PROBLEM_LIMIT = 20

_RUNS_OF_SPACE = re.compile(f"[{SPACE_CLASS}]+")


@dataclass(frozen=True)
class DescriptionRule:
    """One rule: its name, what kind of fault it finds, the pattern and what to say."""

    id: str
    kind: str
    #: The pattern as the TypeScript writes it, and its flags.
    source: str
    flags: str
    pattern: re.Pattern[str]
    message: str


def _rule(  # noqa: PLR0913 - a rule is five things, and one of them is written two ways
    rule_id: str, kind: str, source: str, message: str, *, translated: str | None = None, fold: bool = True
) -> DescriptionRule:
    flags = re.ASCII | (re.IGNORECASE if fold else 0)
    return DescriptionRule(
        rule_id, kind, source, "i" if fold else "", re.compile(translated or source, flags), message
    )


_AS_RUN = (
    r"\bas (run|evaluated|reported|scored|listed|used|printed|selected|submitted|implemented|benchmarked"
    r"|quoted|trained and evaluated|evaluated and reported)\b"
)
_LOCATOR = r"(Table|Tables|Fig\.|Figure|Supplementary|Section|Introduction|Appendix)"

#: `descriptionRules`, in the TypeScript's order.
DESCRIPTION_RULES = (
    _rule(
        "named-family-placeholder",
        "placeholder",
        r"^named prediction model family\b",
        "placeholder: says only that the source names a model family",
    ),
    _rule(
        "identity-as-reported",
        "placeholder",
        r"\bidentity as reported\b",
        "placeholder: says only that the identity is as the source reported it",
    ),
    _rule(
        "source-label-placeholder",
        "placeholder",
        r"\bsource-(reported|label(led)?) configuration\b"
        r"|\bsource-reported configuration or submission label\b",
        "placeholder: says only that the configuration label comes from the source",
    ),
    _rule(
        "catalogue-placeholder",
        "placeholder",
        r"\bcandidate catalogue entry\b",
        "placeholder: catalogue candidate text",
    ),
    _rule(
        "as-run-in",
        "provenance",
        _AS_RUN + r".{0,60}?\b(in|on|by|to|for)\b",
        "provenance: says where the record was run, listed or reported",
        translated=_AS_RUN + DOT + r"{0,60}?\b(in|on|by|to|for)\b",
    ),
    _rule("quoted-in", "provenance", r"\bquoted (in|from)\b", "provenance: says where scores were quoted"),
    _rule(
        "not-new-execution",
        "provenance",
        r"\bnot a new(ly)?[- ](execution|executed)\b",
        "provenance: says the scores are not a new execution",
    ),
    _rule(
        "cited-study",
        "provenance",
        r"\b(in|from) the cited (study|comparison|benchmark|source|paper|evaluation)\b",
        "provenance: refers to the cited study",
    ),
    _rule(
        "this-paper",
        "provenance",
        r"\(this (paper|source|study)\)|\bin this (paper|source)\b",
        "provenance: refers to this paper",
    ),
    _rule("source-reported", "provenance", r"\bsource-reported\b", "provenance: source-reported label"),
    _rule(
        "reported-by-authors",
        "provenance",
        r"\b(comparator reported by|evaluated by the [^.;]{1,60} authors\b"
        r"|is the configuration evaluated in)",
        "provenance: says who evaluated or reported it",
    ),
    _rule(
        "cited-as-reference",
        "provenance",
        r"\b(cited in the source as|the method record names)\b",
        "provenance: describes how the source cites it",
    ),
    _rule(
        "locator-in-parentheses",
        "provenance",
        r"\([^()]*\b" + _LOCATOR + r"\s*S?\d*[^()]*\)",
        "provenance: a source locator in parentheses",
        translated=r"\([^()]*\b" + _LOCATOR + f"[{SPACE_CLASS}]*S?[0-9]*" + r"[^()]*\)",
        fold=False,
    ),
    _rule(
        "citation-in-parentheses",
        "provenance",
        r"\([^()]*\bet al\.?[^()]*\)",
        "provenance: a citation in parentheses; sources belong in source_ids",
        fold=False,
    ),
)


@dataclass(frozen=True)
class DescriptionProblem:
    """One fault in one record's description."""

    id: str
    kind: str
    rule: str
    message: str
    description: str
    #: For a shared description, the records it is shared with.
    shared_with: tuple[str, ...] | None = None

    def row(self) -> JsonObject:
        """The problem as `records -- descriptions --json` writes it."""
        row: JsonObject = {
            "id": self.id,
            "kind": self.kind,
            "description": self.description,
            "rule": self.rule,
            "message": self.message,
        }
        if self.shared_with is not None:
            shared: list[JsonValue] = list(self.shared_with)
            row["shared_with"] = shared
        return row


def normalise_description(text: str) -> str:
    """A description as it is compared: trimmed, runs of white space as one space, lowercase."""
    return _RUNS_OF_SPACE.sub(" ", trim(text)).lower()


def description_problems(records: Sequence[Record]) -> list[DescriptionProblem]:
    """Problems with the descriptions of described kinds, in record order: `descriptionProblems`.

    Excluded records are not published, so they are not checked.
    """
    described = [
        record for record in records if record.kind in DESCRIBED_KINDS and record.status != "excluded"
    ]
    by_text: dict[str, list[Record]] = {}
    for record in described:
        if record.kind in DISTINCT_KINDS and trim(record.description):
            by_text.setdefault(normalise_description(record.description), []).append(record)
    problems: list[DescriptionProblem] = []
    for record in described:
        seen = unit_text(record.description)
        problems.extend(
            DescriptionProblem(record.id, record.kind, rule.id, rule.message, record.description)
            for rule in DESCRIPTION_RULES
            if rule.pattern.search(seen)
        )
        if record.kind not in DISTINCT_KINDS:
            continue
        name = normalise_description(record.name)
        others = [
            other.id
            for other in by_text.get(normalise_description(record.description), [])
            if other.id != record.id and normalise_description(other.name) != name
        ]
        if others:
            problems.append(
                DescriptionProblem(
                    record.id,
                    record.kind,
                    "shared-description",
                    "shares its description with a differently named entity",
                    record.description,
                    tuple(others),
                )
            )
    return problems


def validate_descriptions(records: Sequence[Record]) -> None:
    """Refuse a placeholder, provenance or shared description: `validateDescriptions`.

    Raises `RecordError` with the message the TypeScript throws.
    """
    problems = description_problems(records)
    if not problems:
        return
    shown = [
        f"{problem.id}: {problem.message}"
        + (f" ({', '.join(problem.shared_with)})" if problem.shared_with is not None else "")
        for problem in problems[:PROBLEM_LIMIT]
    ]
    more = len(problems) - PROBLEM_LIMIT
    tail = f"\n... and {more} more (npm run records -- descriptions)" if more > 0 else ""
    listed = "\n".join(shown)
    raise RecordError(
        f"{len(problems)} descriptions are not functional. Say what the {', '.join(DESCRIBED_KINDS)} does in "
        f"one sentence and put provenance in attributes.provenance_note:\n{listed}{tail}"
    )
