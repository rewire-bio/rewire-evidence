"""What the validation gate measures: the store held to every validator, as the TypeScript holds it.

`records -- check` passes on the pinned store, and so must this library. But a validator
that looked at nothing would pass too, so passing is not what the gate rests on. It rests on
`scripts/expected/store-validation.json`, in which the TypeScript states, at the same commit:

- how much there was to look at: every attribute value by the type the registry gives it,
  every reason for a missing value, every structured block, every described record, every
  source;
- what it found: no description problems, no derivation problems, the records named by
  scoped concerns, and every record left as it is by `normalizeAttributes`;
- every comparison panel as the catalogue query resolves it for the record that owns it,
  with the results it shows, as one digest;
- for each family of rule, a fault seeded into the whole store and the words the
  validator that owns the rule refuses it in.

Everything measured here must equal it. The seeded faults are the part that shows a rule is
enforced on the real store and not only on the small one the tests use: each is applied to
the records as read, and the one validator is run over all of them.

`apply_ops` is how a fault is written down, here and in `tests/fixtures/validation.json`.
`outcome` and `agrees` are how a refusal is compared with a recorded one.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from rewire_evidence.attributes import (
    REGISTRY,
    REGISTRY_FILE,
    JsTypeError,
    normalize_attributes,
    validate_attributes,
)
from rewire_evidence.canonical import JsonObject, JsonValue, js_stringify, sha256_hex, utf16_key
from rewire_evidence.concerns import concern_targets, validate_concerns
from rewire_evidence.conformance import differences, recorded_ref
from rewire_evidence.derivations import derivation_issues, validate_derivations
from rewire_evidence.descriptions import DESCRIBED_KINDS, description_problems, validate_descriptions
from rewire_evidence.jsonl import StoreError, read_rows
from rewire_evidence.panels import Resolved
from rewire_evidence.records import parsed, read_records
from rewire_evidence.schema import Record, RecordError, SchemaError
from rewire_evidence.store import read_provenance, record_files
from rewire_evidence.validation import by_id_order, validate_all, validate_records
from rewire_evidence.vocab import VocabularyError, load_schemes, read_mapping, validate_vocabularies

#: The structured blocks the TypeScript counts.
BLOCKS = (
    "benchmark_research",
    "run_documentation",
    "run_guide",
    "run_recipes",
    "reproduction",
    "profile",
    "extensions",
    "comparison_panels",
    "derivation",
    "evidence_concerns",
)


def _child(node: JsonValue, key: JsonValue) -> JsonValue:
    if isinstance(node, dict) and isinstance(key, str):
        return node[key]
    if isinstance(node, list) and isinstance(key, int):
        return node[key]
    raise KeyError(key)


def _change(node: JsonValue, kind: str, last: JsonValue, value: JsonValue) -> None:
    """Set or delete the last step of a path, in a list by position or an object by name."""
    if isinstance(node, list) and isinstance(last, int):
        if kind == "del":
            del node[last]
        elif last == len(node):
            node.append(value)
        else:
            node[last] = value
    elif isinstance(node, dict) and isinstance(last, str):
        if kind == "del":
            node.pop(last, None)
        else:
            node[last] = value
    else:
        raise KeyError(last)


def apply_ops(rows: Sequence[JsonObject], ops: Sequence[JsonValue]) -> list[JsonObject]:
    """Records with a fault put into them, as `scripts/fixtures/validation_cases.mjs` writes one.

    Each operation is a list: `["set", id, path, value]` puts a value at a path inside one
    record, creating the last key or adding to the end of a list. `["del", id, path]`
    deletes a member or removes a list item. `["add", record]` appends a record and
    `["drop", id]` removes one. A path is member names and list positions from the record
    itself. The rows given are not changed: a record an operation touches is copied first,
    and every other row in the result is the row that was given.

    Raises `StoreError` for an operation that names no record, follows a path that is not
    there, or is not one of the four.
    """
    out = list(rows)

    def held(record_id: JsonValue) -> int:
        for position, row in enumerate(out):
            if row.get("id") == record_id:
                out[position] = copy.deepcopy(row)
                return position
        raise StoreError(f"an operation names {js_stringify(record_id)}, which is not a record")

    for op in ops:
        kind, *rest = op if isinstance(op, list) and op else [None]
        try:
            if kind == "add" and isinstance(rest[0], dict):
                out.append(copy.deepcopy(rest[0]))
            elif kind == "drop":
                del out[held(rest[0])]
            elif kind in {"set", "del"} and isinstance(rest[1], list) and rest[1]:
                node: JsonValue = out[held(rest[0])]
                for key in rest[1][:-1]:
                    node = _child(node, key)
                _change(node, kind, rest[1][-1], copy.deepcopy(rest[2]) if kind == "set" else None)
            else:
                raise StoreError(f"{js_stringify(op)} is not an operation")
        except (KeyError, IndexError) as error:
            raise StoreError(f"the operation {js_stringify(op)} cannot be applied: {error!r}") from error
    return out


def outcome(run: Callable[[], object]) -> JsonObject | None:
    """What a validator did, in the form the TypeScript's answer is recorded in.

    None if it returned. zod's issues, each as its code, path and message, for a value zod
    refuses. Otherwise the JavaScript error's name, with its message, or for a file the
    operating system refuses, its code.
    """
    try:
        run()
    except SchemaError as error:
        issues: list[JsonValue] = [[issue.code, list(issue.path), issue.message] for issue in error.issues]
        return {"zod": issues}
    except JsTypeError:
        return {"name": "TypeError"}
    except (RecordError, VocabularyError, StoreError) as error:
        return {"name": "Error", "message": str(error)}
    except IsADirectoryError:
        return {"name": "Error", "code": "EISDIR"}
    return None


def agrees(got: JsonValue, recorded: JsonValue) -> bool:
    """Whether a validator's answer is the one the TypeScript recorded.

    Where the TypeScript failed with a `TypeError` it reported no fault of the records, and
    the words are V8's. Refusing for the same reason is agreement, whatever the words.
    """
    if isinstance(recorded, dict) and recorded.get("name") == "TypeError":
        return isinstance(got, dict) and got.get("name") == "TypeError"
    return got == recorded


def stage_runners(root: Path) -> Mapping[str, Callable[[Sequence[Record]], object]]:
    """Each validator of `loadRecords` after the vocabularies, by the name the fixtures use.

    The first four take the records as filed. `records` is `validateRecords`, which is
    given them by ID.
    """
    return {
        "attributes": validate_attributes,
        "derivations": lambda records: validate_derivations(records, root),
        "descriptions": validate_descriptions,
        "concerns": validate_concerns,
        "records": lambda records: validate_records(by_id_order(records)),
    }


@dataclass
class ValidationReport:
    """What one run of the validation gate measured."""

    #: Records read from the record files, placed and matched to their provenance.
    records: int = 0
    #: Records `load_records` returns, having passed every validator.
    loaded: int = 0
    #: The line `records -- check` prints for them.
    summary: str = ""
    #: Seeded faults refused as the TypeScript refuses them.
    seeded: int = 0
    #: Everything the TypeScript also measured, under the names it recorded them by.
    measured: JsonObject = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def check_summary(count: int) -> str:
    """The line `npm run records -- check` prints for a store it accepts."""
    return f"{count} records match their provenance."


def panel_lines(shown: Mapping[str, list[Resolved] | None]) -> str:
    """Every owner's panels as the recorded digest is taken over them: one line per owner."""
    lines: list[str] = []
    for owner, panels in shown.items():
        listed = (
            "null"
            if panels is None
            else "|".join(f"{panel.id}:{','.join(panel.result_ids)}" for panel in panels)
        )
        lines.append(f"{owner}\t{listed}\n")
    return "".join(lines)


def _counted(counts: Counter[str]) -> JsonObject:
    return {key: counts[key] for key in sorted(counts, key=utf16_key) if counts[key]}


def measure(records: Sequence[Record], root: Path, shown: Mapping[str, list[Resolved] | None]) -> JsonObject:
    """What the validators look at and find in the records, under the TypeScript's names.

    `records` are in the order they are filed, and `shown` is what `validate_records`
    returned for them.
    """
    by_type: Counter[str] = Counter()
    blocks: Counter[str] = Counter()
    reasons = 0
    for record in records:
        declared = REGISTRY[record.kind]
        for key in record.attributes:
            # An attribute the registry does not declare has no type, which JavaScript calls undefined.
            by_type[declared.get(key, "undefined")] += 1
        missing = record.attributes.get("missing_metadata")
        reasons += len(missing) if isinstance(missing, dict) else 0
        for block in BLOCKS:
            blocks[block] += block in record.attributes
    ordered = by_id_order(records)
    rows = [record.row() for record in ordered]
    unchanged = sum(now is row for now, row in zip(normalize_attributes(rows), rows, strict=True))
    panels = [panel for held in shown.values() if held is not None for panel in held]
    return {
        "registry_sha256": sha256_hex(js_stringify(REGISTRY)),
        "attribute_values": sum(by_type.values()),
        "attribute_values_by_type": _counted(by_type),
        "missing_reasons": reasons,
        "blocks": _counted(blocks),
        "described_records": sum(
            record.kind in DESCRIBED_KINDS and record.status != "excluded" for record in records
        ),
        "description_problems": [problem.row() for problem in description_problems(records)],
        "derivation_problems": cast("list[JsonValue]", derivation_issues(ordered, root)),
        "concern_targets": cast("list[JsonValue]", sorted(concern_targets(ordered), key=utf16_key)),
        "sources": sum(record.kind == "source" for record in records),
        "normalize_unchanged": unchanged,
        "panel_owners": len(shown),
        "panels": len(panels),
        "panel_rows": sum(len(panel.result_ids) for panel in panels),
        "panels_sha256": sha256_hex(panel_lines(shown)),
    }


def check_seeded(records: Sequence[Record], root: Path, seeded: JsonValue, report: ValidationReport) -> None:
    """Seed each recorded fault into the records and require the recorded refusal.

    Each fault names the validator that owns its rule, and that validator is run over every
    record. Raises `StoreError` if the expectation holds no faults, or one that cannot be
    applied or is not a set of records: the gate cannot be run without them.
    """
    if not isinstance(seeded, list) or not seeded:
        raise StoreError("the recorded expectation holds no seeded faults")
    runners = stage_runners(root)
    rows = [record.row() for record in records]
    known = {id(row): record for record, row in zip(records, rows, strict=True)}
    for case in seeded:
        ops = case.get("ops") if isinstance(case, dict) else None
        stage = case.get("stage") if isinstance(case, dict) else None
        if (
            not isinstance(case, dict)
            or not isinstance(ops, list)
            or not isinstance(stage, str)
            or stage not in runners
        ):
            raise StoreError("a seeded fault does not name its operations and its validator")
        name = js_stringify(case.get("name"))
        changed = [
            known[id(row)] if id(row) in known else parsed(row, f"the seeded fault {name}")
            for row in apply_ops(rows, ops)
        ]
        got = _answer(runners[stage], changed)
        recorded = case.get("error")
        if recorded is None or not agrees(got, recorded):
            report.problems.append(
                f"the seeded fault {name} ({stage}): got {js_stringify(got)}, "
                f"and the TypeScript recorded {js_stringify(recorded)}"
            )
        else:
            report.seeded += 1


def _answer(runner: Callable[[Sequence[Record]], object], records: Sequence[Record]) -> JsonObject | None:
    return outcome(lambda: runner(records))


def _readable(root: Path) -> None:
    """Read every file of the store as lines of JSON, so one that cannot be read stops the run."""
    read_provenance(root)
    for file in record_files(root):
        read_rows(file)


def verify_validation(root: Path, recorded: JsonObject) -> ValidationReport:
    """Hold a store to every validator and to what the TypeScript recorded of it.

    Raises `StoreError` or `VocabularyError` when the run cannot be made: a file that
    cannot be read, a vocabulary that does not load, an expectation with no seeded faults.
    A store that can be read and is refused is a problem in the report, not a raise.
    """
    report = ValidationReport()
    mapping = read_mapping(root)
    schemes = load_schemes(root)
    try:
        records = read_records(root)
    except StoreError as error:
        _readable(root)
        report.problems.append(f"the store is refused as it is read: {error}")
        return report
    report.records = len(records)
    report.problems.extend(differences(report.records, recorded.get("records"), "records"))
    try:
        validate_vocabularies(mapping, (record.row() for record in records), schemes)
        ordered, shown = validate_all(records, root)
    except (VocabularyError, RecordError, SchemaError) as error:
        report.problems.append(f"the store is refused: {error}")
        return report
    report.loaded = len(ordered)
    report.summary = check_summary(report.loaded)
    report.problems.extend(differences(report.summary, recorded.get("summary"), "summary"))
    report.measured = measure(records, root, shown)
    for name, value in report.measured.items():
        report.problems.extend(differences(value, recorded.get(name), name))
    registry_ref = cast("JsonObject", REGISTRY_FILE["recorded_from"])["store_ref"]
    if recorded_ref(recorded) != registry_ref:
        report.problems.append(
            f"the attribute registry was read at {js_stringify(registry_ref)}, which is not the commit "
            "the expectation was recorded at"
        )
    check_seeded(records, root, recorded.get("seeded"), report)
    return report


def summary(report: ValidationReport) -> str:
    """The report as the lines the gate prints."""
    measured = report.measured
    return "\n".join(
        [
            f"records read: {report.records:,}",
            f"  accepted by every validator of load_records: {report.loaded:,}",
            f"attribute values, each of its declared type: {measured.get('attribute_values')}",
            f"reasons for a missing value: {measured.get('missing_reasons')}",
            f"described records, none with a description problem: {measured.get('described_records')}",
            f"records normalize_attributes leaves as they are: {measured.get('normalize_unchanged')}",
            (
                f"comparison panels resolved: {measured.get('panels')} for {measured.get('panel_owners')} "
                f"records, showing {measured.get('panel_rows')} results"
            ),
            f"seeded faults refused as the TypeScript refuses them: {report.seeded}",
        ]
    )


def to_json(report: ValidationReport, store_ref: str) -> str:
    """The report as JSON, with the same names the recorded expectation uses."""
    return json.dumps(
        {
            "store_ref": store_ref,
            "records_read": report.records,
            "records_loaded": report.loaded,
            "summary": report.summary,
            "seeded_refused": report.seeded,
            "measured": report.measured,
            "problems": report.problems,
        },
        indent=1,
        ensure_ascii=False,
    )
