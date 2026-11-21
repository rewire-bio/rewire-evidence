"""Everything `loadRecords` asks of the records once they are read: the validators, in order.

`validate_records` is `validateRecords` in `scripts/omics/schema.ts`, whole. It takes the
records one at a time in the order given, and for each it applies every rule before moving
on, so the first fault of the first faulty record is the one reported:

1. no member anywhere in the record has a private field's name;
2. every source ID names a source, and every link is one its relation allows;
3. a literature audit, run documentation, a run guide, run recipes, a reproduction, a
   profile and extension blocks are each well formed, on a record that may carry them, and
   cite pinned sources the record itself cites;
4. a source has an http or https URL, a retrieval date, and a version or a reason for none;
5. a result links one evaluation and has a printed value, a metric, a unit and a numeric
   value that is null or a finite number as text; a checked result has sources, a locator
   and a review; a reproduced result's evaluation is one of our own runs;
6. an evaluation has exactly one system, one assessment and one dataset;
7. a claim has a subject.

Then, if any record has comparison panels, every one of them is resolved (`panels.py`).

`validate_store` is the five validators `loadRecords` calls, in its order: attributes,
derivations, descriptions and concerns over the records as they are filed, then
`validate_records` over the records by ID. Vocabularies come before all of them and are
checked by `records.load_records`, which has the files to read them from.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from rewire_evidence import blocks
from rewire_evidence.attributes import JsTypeError, validate_attributes
from rewire_evidence.canonical import JsonObject, utf16_key
from rewire_evidence.concerns import validate_concerns
from rewire_evidence.derivations import validate_derivations
from rewire_evidence.descriptions import validate_descriptions
from rewire_evidence.js import MISSING, Held, number_of_text, text_of, trim, truthy
from rewire_evidence.panels import Resolved, resolve_panels
from rewire_evidence.schema import Record, RecordError, by_unique_id, check_references
from rewire_evidence.weburl import protocol_of

_CHECKED = ("source_checked", "reproduced")
_ROLES = ("system", "assessment", "data")


def _finite_text(value: Held) -> bool:
    """A numeric value as the store writes one: text, not blank, that JavaScript reads as a finite number."""
    if not isinstance(value, str) or not trim(value):
        return False
    number = number_of_text(value)
    return number == number and number not in {float("inf"), float("-inf")}  # noqa: PLR0124 - NaN


def numeric_value_ok(attributes: JsonObject) -> bool:
    """Whether `numeric_value` is null or text holding a finite number. Absent is neither."""
    return ("numeric_value" in attributes and attributes["numeric_value"] is None) or _finite_text(
        attributes.get("numeric_value", MISSING)
    )


def _check_source(record: Record) -> None:
    attributes = record.attributes
    try:
        protocol = protocol_of(text_of(attributes.get("url", MISSING)))
    except TypeError as error:
        raise JsTypeError(str(error)) from error
    if protocol is None:
        raise JsTypeError("Invalid URL")
    if protocol not in {"https:", "http:"}:
        raise RecordError(f"Invalid source URL {record.id}")
    if not truthy(attributes.get("retrieved_at", MISSING)):
        raise RecordError(f"Undated source {record.id}")
    missing = attributes.get("missing_metadata")
    reason = missing.get("version", MISSING) if isinstance(missing, dict) else MISSING
    if not truthy(attributes.get("version", MISSING)) and not truthy(reason):
        raise RecordError(f"Unversioned source {record.id}")


def _check_result(record: Record, by_id: Mapping[str, Record]) -> None:
    attributes = record.attributes
    evaluations = [link.target_id for link in record.links if link.relation == "evaluation"]
    evaluation = by_id.get(evaluations[0]) if len(evaluations) == 1 else None
    if evaluation is None or evaluation.kind != "evaluation":
        raise RecordError(f"Invalid result evaluation {record.id}")
    printed = attributes.get("printed_value")
    if (
        not isinstance(printed, str)
        or not trim(printed)
        or not truthy(attributes.get("metric", MISSING))
        or not truthy(attributes.get("unit", MISSING))
    ):
        raise RecordError(f"Incomplete result {record.id}")
    if not numeric_value_ok(attributes):
        raise RecordError(f"Invalid numeric value {record.id}")
    if record.status in _CHECKED and (
        not record.source_ids
        or not truthy(attributes.get("source_locator", MISSING))
        or not truthy(attributes.get("review", MISSING))
    ):
        raise RecordError(f"Missing result evidence {record.id}")
    if record.status == "reproduced" and evaluation.attributes.get("origin") != "rewire_run":
        raise RecordError(f"External result mislabelled reproduced {record.id}")


def check_record_rules(record: Record, by_id: Mapping[str, Record]) -> None:
    """Every rule `validateRecords` holds one record to, in its order.

    Raises `RecordError` with the TypeScript's message, `SchemaError` with zod's issues for
    a block of the wrong shape, and `JsTypeError` where the TypeScript fails with a
    `TypeError`: a source URL the URL parser refuses is one.
    """
    blocks.assert_no_private_fields(record.row(), "Private field cannot enter a public catalogue release.")
    check_references(record, by_id)
    attributes = record.attributes
    blocks.validate_benchmark_research(record, by_id)
    blocks.validate_run_guide(record, by_id)
    blocks.validate_run_recipes(record, by_id)
    if "profile" in attributes:
        profile = blocks.parsed(blocks.PROFILE, attributes["profile"])
        blocks.validate_profile_sources(cast("JsonObject", profile), by_id)
    if "extensions" in attributes:
        blocks.parsed(blocks.EXTENSIONS, attributes["extensions"])
    if record.kind == "source":
        _check_source(record)
    if record.kind == "result":
        _check_result(record, by_id)
    if record.kind == "evaluation":
        for role in _ROLES:
            if sum(link.relation == role for link in record.links) != 1:
                raise RecordError(f"Invalid evaluation {role} {record.id}")
    if record.kind == "claim" and not any(link.relation == "subject" for link in record.links):
        raise RecordError(f"Claim has no subject {record.id}")


def validate_records(records: Sequence[Record]) -> Mapping[str, list[Resolved] | None]:
    """`validateRecords`: every rule for every record in the order given, then every panel.

    `loadRecords` gives it the records by ID, so that is the order to give them in for the
    same first fault. Returns what the catalogue query shows for each record that has
    comparison panels, which is what the last step works out.
    """
    by_id = by_unique_id(records)
    for record in records:
        check_record_rules(record, by_id)
    return resolve_panels(records)


def by_id_order(records: Sequence[Record]) -> list[Record]:
    """The records sorted by ID as JavaScript compares text."""
    return sorted(records, key=lambda record: utf16_key(record.id))


def validate_all(
    records: Sequence[Record], root: Path
) -> tuple[list[Record], Mapping[str, list[Resolved] | None]]:
    """The validators of `loadRecords` after the vocabularies, in its order.

    `records` are in the order they are filed, which is the order the first four validators
    report in. `root` is where a derivation's script is read from. Returns the records by
    ID, as `loadRecords` does, and what the catalogue query shows for each record that has
    comparison panels.
    """
    validate_attributes(records)
    validate_derivations(records, root)
    validate_descriptions(records)
    validate_concerns(records)
    ordered = by_id_order(records)
    return ordered, validate_records(ordered)


def validate_store(records: Sequence[Record], root: Path) -> list[Record]:
    """`validate_all`, for a caller that wants the records and not the panels."""
    return validate_all(records, root)[0]
