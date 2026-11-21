"""What the schema gate measures: the store held to the schema and the vocabularies.

The layout gate (`parity`) shows the library writes the lines the store holds. This shows
it knows what a record is, against two things recorded from the TypeScript:

- the committed records themselves, each of which must parse and be written back as the
  line it was read from, and must come back as that same line when its top-level keys and
  the keys of its links are put in another order first. The second half matters because
  every stored record is already in schema order, so a parser that reordered nothing would
  pass the first half;
- `scripts/expected/store-schema.json`, in which the TypeScript states what it measures on
  the same commit: the records it accepts, their links by relation, the records a public
  release may hold, every vocabulary's statements and concepts, every controlled value by
  field, and the words it refuses a seeded bad concept in. Everything measured here must
  equal it.

It also runs `records.load_records`, the loader the later stages will call, over the same
store, and requires it to return the records the schema accepted, in ID order. Without
that the loader would be held only to the small stores its unit tests build.

The gate reports every disagreement rather than stopping at the first.

What it catches of an edited checkout, and what it leaves to the other two gates:

- a record whose value was edited no longer matches its provenance digest, and a line moved
  out of ID order or into another file is misplaced. `load_records` refuses both, so each
  is a problem here. A removed record changes the recorded counts;
- anything `read_lines` drops before a record is read is not seen: a blank line, a missing
  or a doubled final newline. The layout gate compares every file with its pinned bytes;
- a record edited together with its provenance row agrees with itself, and passes here and
  in the digest gate unless it moves a count. Only the layout gate's pinned bytes refuse it.

CI runs all three.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from rewire_evidence.canonical import (
    JsonObject,
    JsonValue,
    js_parse,
    js_string,
    js_stringify,
    sha256_hex,
    utf16_key,
)
from rewire_evidence.jsonl import StoreError, join_lines, parse_row, read_lines
from rewire_evidence.records import load_records
from rewire_evidence.schema import (
    Record,
    RecordError,
    check_record,
    public_records,
    validate_references,
)
from rewire_evidence.store import record_files
from rewire_evidence.turtle import BlankLabels
from rewire_evidence.vocab import (
    ControlledFields,
    Scheme,
    VocabularyError,
    controlled_values,
    load_schemes,
    read_mapping,
    read_scheme_file,
    scheme_files,
    validate_vocabularies,
    vocabulary_problems,
)

#: How much of a value is shown when two disagree.
_SHOWN = 120


@dataclass
class SchemaReport:
    """What one run of the schema gate measured."""

    #: Lines read from the record files.
    records: int = 0
    #: Lines the schema accepts and writes back byte for byte.
    reproduced: int = 0
    #: Lines that come back byte for byte after their keys were put in another order.
    restored: int = 0
    #: Records `load_records` returns, when they are the accepted records in ID order.
    loaded: int = 0
    #: Seeded bad concepts refused in the words the TypeScript recorded.
    seeded: int = 0
    #: Vocabularies read, and the concepts they hold between them.
    schemes: int = 0
    concepts: int = 0
    #: Everything the TypeScript also measured, under the names it recorded them by.
    measured: JsonObject = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _shown(value: JsonValue) -> str:
    text = js_stringify(value)
    return text if len(text) <= _SHOWN else f"{text[:_SHOWN]}..."


def differences(measured: JsonValue, recorded: JsonValue, name: str) -> list[str]:
    """Where two values disagree, each named by its path. Objects are compared member by member."""
    if isinstance(measured, dict) and isinstance(recorded, dict):
        found: list[str] = []
        for key in sorted({*measured, *recorded}, key=utf16_key):
            if key not in measured:
                found.append(
                    f"{name}.{key}: not measured, and the TypeScript recorded {_shown(recorded[key])}"
                )
            elif key not in recorded:
                found.append(
                    f"{name}.{key}: measured {_shown(measured[key])}, and the TypeScript recorded nothing"
                )
            else:
                found.extend(differences(measured[key], recorded[key], f"{name}.{key}"))
        return found
    if isinstance(measured, list) and isinstance(recorded, list) and measured != recorded:
        first = next(
            (index for index, pair in enumerate(zip(measured, recorded, strict=False)) if pair[0] != pair[1]),
            min(len(measured), len(recorded)),
        )
        counts = f"{len(measured)} items measured and {len(recorded)} recorded by the TypeScript"
        return [f"{name}: {counts}, first differing at position {first}"]
    if measured != recorded:
        return [f"{name}: measured {_shown(measured)}, the TypeScript recorded {_shown(recorded)}"]
    return []


def moved(row: JsonObject) -> JsonObject:
    """A row with its top-level keys and the keys of each link in the reverse order."""
    out = dict(reversed(row.items()))
    links = out.get("links")
    if isinstance(links, list):
        out["links"] = [dict(reversed(link.items())) if isinstance(link, dict) else link for link in links]
    return out


def _written(row: JsonObject) -> str | None:
    """The line the schema writes for a row, or None if it refuses the row."""
    record = check_record(row).record
    return None if record is None else js_stringify(record.row())


def read_store(root: Path, report: SchemaReport) -> list[Record]:
    """Every record the schema accepts, in file order, with each line held to its bytes."""
    records: list[Record] = []
    for file in record_files(root):
        for number, line in enumerate(read_lines(file), 1):
            where = f"{file.relative_to(root)}:{number}"
            report.records += 1
            row = parse_row(line, where)
            checked = check_record(row)
            if checked.record is None:
                listed = "; ".join(f"{issue.rule}: {issue.message}" for issue in checked.issues)
                report.problems.append(f"{where} is not a record: {listed}")
                continue
            records.append(checked.record)
            try:
                written = js_stringify(checked.record.row())
            except ValueError as error:
                report.problems.append(f"{where} cannot be written: {error}")
                continue
            if written == line:
                report.reproduced += 1
            else:
                report.problems.append(
                    f"{where} is accepted but is not written back as the line it was read from"
                )
            if _written(moved(row)) == line:
                report.restored += 1
            else:
                report.problems.append(f"{where} is not restored once its keys are put in another order")
    return records


def scheme_facts(root: Path, schemes: dict[str, Scheme], labels: BlankLabels) -> JsonObject:
    """Each scheme as the TypeScript recorded it: its statements, digested, and its concepts.

    `labels` is where n3's blank node counters stood when the recorder read the files, so
    a blank node in one of them is given the label the recorded digest was taken over.
    """
    facts: JsonObject = {}
    for path in scheme_files(root):
        name = path.name.removesuffix(".ttl")
        statements = read_scheme_file(path, labels)
        lines = [statement.line for statement in statements]
        concepts: list[JsonValue] = [
            [concept.key, concept.label] for concept in schemes[name].concepts.values()
        ]
        facts[name] = {
            "iri": schemes[name].iri,
            "quads": len(statements),
            "quads_sha256": sha256_hex("".join(lines)),
            "quads_sorted_sha256": sha256_hex("".join(sorted(lines, key=utf16_key))),
            "blank_nodes": sum(statement.has_blank_node for statement in statements),
            "concepts": concepts,
        }
    return facts


def controlled_fields(mapping: ControlledFields) -> list[str]:
    """Every controlled field a record can hold, by the name a refusal gives it."""
    return [
        *(name for name, scheme in mapping.fields.items() if scheme),
        *(f"facets.{name}" for name, scheme in mapping.facets.items() if scheme),
        *(f"attributes.{name}" for name, scheme in mapping.attributes.items() if scheme),
        *(f"attributes.review.{name}" for name, scheme in mapping.review.items() if scheme),
        "attributes.derivation.method",
    ]


def _counted(counts: Counter[str]) -> JsonObject:
    return dict(sorted(counts.items(), key=lambda pair: utf16_key(pair[0])))


def measure_records(records: list[Record]) -> JsonObject:
    """The counts the TypeScript recorded of the records themselves, with them in ID order."""
    ordered = sorted(records, key=lambda record: utf16_key(record.id))
    by_relation = Counter(link.relation for record in ordered for link in record.links)
    visible = public_records(ordered)
    return {
        "records": len(ordered),
        "records_by_kind": _counted(Counter(record.kind for record in ordered)),
        "source_ids": sum(len(record.source_ids) for record in ordered),
        "links": sum(by_relation.values()),
        "links_by_relation": _counted(by_relation),
        "public_records": len(visible),
        "public_ids_sha256": sha256_hex(join_lines(record.id for record in visible)),
    }


def measure_controlled(mapping: ControlledFields, records: Iterable[Record]) -> JsonObject:
    """How many controlled values the records hold, by field and scheme."""
    by_field: Counter[str] = Counter()
    for record in records:
        by_field.update(
            f"{found.field} {js_string(found.scheme)}" for found in controlled_values(mapping, record.row())
        )
    return {"controlled_values": sum(by_field.values()), "controlled_values_by_field": _counted(by_field)}


def _refusal(mapping: ControlledFields, schemes: dict[str, Scheme], row: JsonObject) -> str | None:
    try:
        validate_vocabularies(mapping, [row], schemes)
    except VocabularyError as error:
        return str(error)
    return None


def check_seeded(
    mapping: ControlledFields, schemes: dict[str, Scheme], recorded: JsonValue, report: SchemaReport
) -> None:
    """Seed each recorded bad concept and require the refusal the TypeScript gave.

    The seeds must also cover every controlled field the mapping names, so a field added
    to the mapping without a seed is a problem and not a silence.
    """
    if not isinstance(recorded, list):
        raise StoreError("the recorded expectation has no list 'seeded'")
    fields: list[str] = []
    for number, seed in enumerate(recorded, 1):
        where = f"seeded case {number}"
        if not isinstance(seed, dict):
            raise StoreError(f"{where} is not a JSON object")
        name, text, expected = seed.get("field"), seed.get("record"), seed.get("error")
        if not isinstance(name, str) or not isinstance(text, str) or not isinstance(expected, str):
            raise StoreError(f"{where} needs text for its field, its record and its error")
        fields.append(name)
        refusal = _refusal(mapping, schemes, parse_row(text, where))
        if refusal == expected:
            report.seeded += 1
        else:
            report.problems.append(
                f"a bad concept seeded in {name} gives {refusal!r}, where the TypeScript gave {expected!r}"
            )
    wanted = controlled_fields(mapping)
    if sorted(fields) != sorted(wanted):
        missing = sorted({*wanted} - {*fields})
        extra = sorted({*fields} - {*wanted})
        report.problems.append(
            f"the seeded cases are not the mapping's controlled fields: missing {missing}, unknown {extra}"
        )


def read_recorded(path: Path) -> JsonObject:
    """What the TypeScript recorded of the pinned store."""
    try:
        value = js_parse(path.read_bytes().decode("utf-8"))
    except OSError as error:
        raise StoreError(f"{path} cannot be read: {error}") from error
    except ValueError as error:
        raise StoreError(f"{path} is not JSON: {error}") from error
    if not isinstance(value, dict):
        raise StoreError(f"{path} is not a JSON object")
    return value


def recorded_ref(recorded: JsonObject) -> str:
    """The store commit the TypeScript measured."""
    source = recorded.get("recorded_from")
    ref = source.get("store_ref") if isinstance(source, dict) else None
    if not isinstance(ref, str):
        raise StoreError("the recorded expectation does not name the store commit it was measured at")
    return ref


def recorded_labels(recorded: JsonObject) -> BlankLabels:
    """Where n3's blank node counters stood when the recorder read the vocabularies."""
    held = recorded.get("blank_labels")
    documents, anonymous = (
        (held.get("documents"), held.get("anonymous")) if isinstance(held, dict) else (0, 0)
    )
    if not isinstance(held, dict) or not isinstance(documents, int) or not isinstance(anonymous, int):
        raise StoreError("the recorded expectation does not say where n3's blank node counters stood")
    return BlankLabels(documents, anonymous)


def check_loader(root: Path, records: list[Record], report: SchemaReport) -> None:
    """Hold `load_records` to the store: it must return the accepted records, in ID order.

    A store it refuses is a problem and not a run that could not be made, because every
    file it reads has been read once already by the time it is called.
    """
    try:
        loaded = load_records(root)
    except (StoreError, VocabularyError, RecordError) as error:
        report.problems.append(f"load_records refuses the store: {error}")
        return
    if loaded == sorted(records, key=lambda record: utf16_key(record.id)):
        report.loaded = len(loaded)
    else:
        report.problems.append(
            f"load_records returns {len(loaded)} records, which are not the "
            f"{len(records)} the schema accepts in ID order"
        )


def verify_schema(root: Path, recorded: JsonObject) -> SchemaReport:
    """Hold a store to the schema, the vocabularies and what the TypeScript recorded of it.

    Raises `StoreError` or `VocabularyError` when the run cannot be made: a file that
    cannot be read, a vocabulary that does not load, an expectation with no seeded cases.
    """
    report = SchemaReport()
    labels = recorded_labels(recorded)
    mapping = read_mapping(root)
    schemes = load_schemes(root)
    report.schemes = len(schemes)
    report.concepts = sum(len(scheme.concepts) for scheme in schemes.values())
    records = read_store(root, report)

    try:
        validate_references(records)
    except RecordError as error:
        report.problems.append(f"the records break a rule of the store: {error}")
    report.problems.extend(vocabulary_problems(mapping, (record.row() for record in records), schemes))

    report.measured = {
        **measure_records(records),
        **measure_controlled(mapping, records),
        "schemes": scheme_facts(root, schemes, labels),
    }
    for name, value in report.measured.items():
        report.problems.extend(differences(value, recorded.get(name), name))
    check_seeded(mapping, schemes, recorded.get("seeded"), report)
    check_loader(root, records, report)
    return report


def summary(report: SchemaReport) -> str:
    """The report as the lines the gate prints."""
    measured = report.measured
    return "\n".join(
        [
            f"records read: {report.records:,}",
            f"  accepted and written back as the committed line: {report.reproduced:,}",
            f"  restored to the committed line with their keys moved: {report.restored:,}",
            f"  returned by load_records, in ID order: {report.loaded:,}",
            f"links allowed by the relation rules: {measured.get('links')}",
            f"source IDs naming a source: {measured.get('source_ids')}",
            f"records a public release may hold: {measured.get('public_records')}",
            f"vocabularies: {report.schemes} schemes, {report.concepts} concepts",
            f"controlled values, each a concept of its scheme: {measured.get('controlled_values')}",
            f"seeded bad concepts refused as the TypeScript refuses them: {report.seeded}",
        ]
    )


def to_json(report: SchemaReport, store_ref: str) -> str:
    """The report as JSON, with the same names the recorded expectation uses."""
    return json.dumps(
        {
            "store_ref": store_ref,
            "records_read": report.records,
            "records_reproduced": report.reproduced,
            "records_restored": report.restored,
            "records_loaded": report.loaded,
            "seeded_refused": report.seeded,
            "schemes": report.schemes,
            "concepts": report.concepts,
            "measured": report.measured,
            "problems": report.problems,
        },
        indent=1,
        ensure_ascii=False,
    )
