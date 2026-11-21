"""Evidence concerns: `shared/omics/concerns.ts`.

A concern is an entry in a source record's `attributes.evidence_concerns`: a problem a review
found that should stop evidence supporting comparisons. An entry without `applies_to` covers
everything the source supports. An entry with `applies_to` covers only the records it names
and the evidence that rests on them.

`validate_concerns` refuses a scoped concern that is not on a source or names a record that
does not exist or cannot be named. The rest is what a comparison panel asks before it shows
a result: whether a source carries a concern over everything, which records scoped concerns
name, and which records a result's value rests on.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from rewire_evidence.canonical import JsonValue
from rewire_evidence.js import MISSING, Held, text_of, trim
from rewire_evidence.schema import Record, RecordError

CONCERN_TARGET_KINDS = (
    "result",
    "evaluation",
    "configuration",
    "dataset",
    "dataset_subset",
    "protocol",
    "task",
    "benchmark",
    "claim",
)
#: How many problems a refusal lists before it stops.
PROBLEM_LIMIT = 20


def entries(record: Record | None) -> list[JsonValue]:
    """A record's concern entries: each object or list in the list, and each text that says something.

    A plain-text entry, the form older records use, is a concern over the whole source. A
    list inside the list is taken as an entry by the TypeScript too, and has no scope.
    """
    value = None if record is None else record.attributes.get("evidence_concerns")
    if not isinstance(value, list):
        return []
    found: list[JsonValue] = []
    for entry in value:
        if isinstance(entry, str):
            if trim(entry):
                found.append({"message": entry})
        elif isinstance(entry, (dict, list)):
            found.append(entry)
    return found


def _applies_to(entry: JsonValue) -> Held:
    return entry.get("applies_to", MISSING) if isinstance(entry, dict) else MISSING


def scope(entry: JsonValue) -> list[str] | None:
    """The records an entry is limited to, or None if it covers the whole source."""
    named = _applies_to(entry)
    return [text_of(each) for each in named] if isinstance(named, list) and named else None


def source_wide_concern(source: Record | None) -> bool:
    """Whether the source carries a concern over everything it supports."""
    return any(scope(entry) is None for entry in entries(source))


def concern_targets(records: Iterable[Record]) -> set[str]:
    """The IDs named by scoped concerns on any source."""
    return {
        named
        for record in records
        if record.kind == "source"
        for entry in entries(record)
        for named in scope(entry) or []
    }


def evidence_path(result: Record, evaluation: Record, assessments: Sequence[Record]) -> list[str]:
    """The IDs a result's value rests on.

    The result, its evaluation, the evaluation's tested system, dataset and assessments,
    and what those assessments are directly part of.
    """
    return [
        result.id,
        evaluation.id,
        *(link.target_id for link in evaluation.links if link.relation in {"system", "assessment", "data"}),
        *(link.target_id for each in assessments for link in each.links if link.relation == "part_of"),
    ]


def concern_problems(records: Sequence[Record]) -> list[str]:
    """Every fault in every scoped concern, in record order."""
    by_id = {record.id: record for record in records}
    problems: list[str] = []
    for record in records:
        for entry in entries(record):
            named = _applies_to(entry)
            if named is MISSING:
                continue
            if record.kind != "source":
                problems.append(f"{record.id}: evidence_concerns belong on a source record")
            if not isinstance(named, list) or not named or any(not isinstance(each, str) for each in named):
                problems.append(f"{record.id}: applies_to must be a non-empty list of record IDs")
                continue
            for each in named:
                target = by_id.get(text_of(each))
                if target is None:
                    problems.append(f"{record.id}: applies_to names {text_of(each)}, which does not exist")
                elif target.kind not in CONCERN_TARGET_KINDS:
                    problems.append(
                        f"{record.id}: applies_to names {target.kind} {target.id}; "
                        f"a concern can name {', '.join(CONCERN_TARGET_KINDS)}"
                    )
    return problems


def validate_concerns(records: Sequence[Record]) -> None:
    """Refuse scoped concerns that are not on a source or name what they cannot: `validateConcerns`.

    Raises `RecordError` with the message the TypeScript throws.
    """
    problems = concern_problems(records)
    if problems:
        listed = "\n  ".join(problems[:PROBLEM_LIMIT])
        raise RecordError(f"Invalid evidence concerns:\n  {listed}")
