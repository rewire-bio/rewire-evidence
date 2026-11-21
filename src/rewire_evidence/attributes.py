"""Declared record attributes: `shared/omics/attributes.ts` and the registry beside it.

Every attribute key a record may carry is declared for its kind in the registry, with a type.
`attribute_issues` is `attributeIssues`: it refuses a key nothing declares, a null anywhere
but `numeric_value`, and a value its type's schema refuses, in the words the TypeScript
prints. A value that is not known is absent, and `missing_metadata` says why.

The registry is not copied by hand. `data/attribute-registry.json` is written by
`scripts/fixtures/record_validation_fixture.mjs` from the TypeScript's own `attributeRegistry`
in CI, with the commit it was read at and the digest of its JSON, and a test recomputes the
digest.

`normalize_attributes` is `normalizeAttributes`, which turns records written before October
2026 into the declared shapes. The store holds none: it leaves every stored record as it is.
It is here because the catalogue query reads every record through it before a comparison
panel is resolved, so it can change what `validate_records` decides, and can refuse.

Where the TypeScript looks a name up on a plain object it finds the members every
JavaScript object has: `constructor`, `toString` and ten more. An attribute with such a name
is therefore "declared", and the TypeScript then fails with a `TypeError` where it meant to
check a type. That is kept: `JsTypeError` is raised where JavaScript throws one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from importlib import resources
from typing import Final, cast

from rewire_evidence import zod
from rewire_evidence.canonical import JsonObject, JsonValue, js_key_order, js_stringify, js_value, utf16_key
from rewire_evidence.js import (
    DOT,
    MISSING,
    SPACE_CLASS,
    Missing,
    joined,
    number_of_text,
    slice_units,
    text_of,
    trim,
    truthy,
)
from rewire_evidence.schema import KINDS, Record, RecordError

MISSING_REASONS = ("unreported", "unextracted", "unavailable", "inapplicable", "conflicting")
UNCERTAINTY_TYPES = (
    "standard_deviation",
    "standard_error",
    "confidence_interval",
    "credible_interval",
    "unresolved_spread",
)
#: The review method no review dated after `VISUAL_CHECK_RETIRED_AFTER` may carry.
RENDERED_SOURCE_VISUAL_CHECK = "rendered-source-visual-check"
VISUAL_CHECK_RETIRED_AFTER = "2026-11-17"

#: The members every JavaScript object answers to, which a lookup on a plain object finds.
INHERITED = frozenset(
    {
        "constructor",
        "toString",
        "valueOf",
        "hasOwnProperty",
        "isPrototypeOf",
        "propertyIsEnumerable",
        "toLocaleString",
        "__proto__",
        "__defineGetter__",
        "__defineSetter__",
        "__lookupGetter__",
        "__lookupSetter__",
    }
)


class JsTypeError(RecordError):
    """Where the TypeScript does not report a fault but fails with a JavaScript `TypeError`.

    The value is refused either way. The words are V8's and are not reproduced, so the
    class is what identifies the case.
    """


def said(value: object) -> str:
    """`String(value)` for a JSON value or its absence, as a message or a comparison takes it.

    Raises `JsTypeError` where JavaScript throws a `TypeError`: an object with a member
    named `toString` has no text.
    """
    try:
        return text_of(value)
    except TypeError as error:
        raise JsTypeError(str(error)) from error


# ---- the schemas ----------------------------------------------------------------------

_DECIMAL = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_HASH = zod.pattern(re.compile(r"[a-f0-9]{64}"), "^[a-f0-9]{64}$")

TEXT: Final = zod.NONEMPTY
DECIMAL: Final = zod.text(
    zod.pattern(_DECIMAL, r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$", "Expected a decimal string")
)
INTEGER: Final = zod.number(zod.INT)
DATE: Final = zod.text(zod.pattern(_DATE, r"^\d{4}-\d{2}-\d{2}$", "Expected a date (YYYY-MM-DD)"))
DATE_TIME: Final = zod.text(zod.DATETIME)
RECORD_ID: Final = zod.text(
    zod.pattern(re.compile(r"[a-z0-9][a-z0-9-]{0,254}"), "^[a-z0-9][a-z0-9-]{0,254}$")
)
SHA256: Final = zod.text(_HASH)
URL: Final = zod.text(zod.URL)
_JSON = zod.union(zod.record(zod.Unknown()), zod.array(zod.Unknown()))
_REPO_PATH = zod.text(
    zod.pattern(
        re.compile(rf"(?!/)(?!{DOT}*\.\.)[A-Za-z0-9_./-]+"),
        r"^(?!\/)(?!.*\.\.)[\w./-]+$",
        "Expected a repository-relative path",
    )
)

MISSING_ENTRY: Final = zod.strict({"reason": zod.one_of(*MISSING_REASONS), "note": zod.optional(TEXT)})


def _some_reason(value: JsonValue, report: zod.Report) -> None:
    if not value:
        report("Omit an empty missing_metadata map")


MISSING_METADATA: Final = zod.record(MISSING_ENTRY, zod.refinement(_some_reason))


def _uncertainty_rules(value: JsonValue, report: zod.Report) -> None:
    held = cast("JsonObject", value)
    kind = text_of(held.get("type", MISSING))
    interval = kind in {"confidence_interval", "credible_interval"}
    if interval and "lower" not in held and "half_width" not in held:
        report(f"{kind} needs lower and upper bounds or a half-width")
    if ("lower" not in held) != ("upper" not in held):
        report("Give both interval bounds")
    if not interval and "value" not in held:
        report(f"{kind} needs a value")
    if not interval and ("lower" in held or "half_width" in held or "level" in held):
        report(f"{kind} cannot have interval bounds or a level")


UNCERTAINTY: Final = zod.strict(
    {
        "type": zod.one_of(*UNCERTAINTY_TYPES),
        "printed": zod.optional(TEXT),
        "value": zod.optional(DECIMAL),
        "half_width": zod.optional(DECIMAL),
        "lower": zod.optional(DECIMAL),
        "upper": zod.optional(DECIMAL),
        "center": zod.optional(DECIMAL),
        "level": zod.optional(zod.number(zod.POSITIVE, zod.less(1, inclusive=False))),
        "method": zod.optional(zod.one_of("bootstrap", "bootstrap_percentile", "analytic", "propagated")),
        "n": zod.optional(INTEGER.check(zod.POSITIVE)),
        "resamples": zod.optional(INTEGER.check(zod.POSITIVE)),
        "scope": zod.optional(TEXT),
        "unit": zod.optional(TEXT),
        "source_column": zod.optional(TEXT),
        "note": zod.optional(TEXT),
    },
    zod.refinement(_uncertainty_rules),
)

COVERAGE: Final = zod.strict(
    {
        "scored": zod.optional(INTEGER.check(zod.NONNEGATIVE)),
        "eligible": zod.optional(INTEGER.check(zod.NONNEGATIVE)),
        "unit": zod.optional(TEXT),
        "generated_per_run": zod.optional(INTEGER.check(zod.POSITIVE)),
        "repeats": zod.optional(INTEGER.check(zod.POSITIVE)),
        "note": zod.optional(TEXT),
    }
)

_RETIRED = (
    f"{RENDERED_SOURCE_VISUAL_CHECK} is historical and a review dated after {VISUAL_CHECK_RETIRED_AFTER} "
    "may not carry it. Record what the tool did: pdf-text-parse or deterministic-table-parse for a "
    "parsed value, independent-cell-check for a second check, and name the technique in method_note."
)


def _review_rules(value: JsonValue, report: zod.Report) -> None:
    held = cast("JsonObject", value)
    reviewed = held.get("date")
    if reviewed is None:
        at = held.get("reviewed_at")
        reviewed = slice_units(at, 10) if isinstance(at, str) else None
    method = cast("list[JsonValue]", held["method"])
    if (
        RENDERED_SOURCE_VISUAL_CHECK in method
        and isinstance(reviewed, str)
        and utf16_key(reviewed) > utf16_key(VISUAL_CHECK_RETIRED_AFTER)
    ):
        report(_RETIRED)


REVIEW: Final = zod.strict(
    {
        "method": zod.array(TEXT, zod.min_length(1)),
        "reviewer": zod.optional(zod.array(TEXT, zod.min_length(1))),
        "date": zod.optional(DATE),
        "reviewed_at": zod.optional(DATE_TIME),
        "scope": zod.optional(TEXT),
        "note": zod.optional(TEXT),
        "method_note": zod.optional(TEXT),
        "reviewer_note": zod.optional(TEXT),
        "evidence": zod.optional(TEXT),
        "source_id": zod.optional(RECORD_ID),
        "receipt": zod.optional(TEXT),
        "artifact_sha256": zod.optional(SHA256),
        "retrieval_url": zod.optional(URL),
    },
    zod.refinement(_review_rules),
)

DERIVATION_INPUT: Final = zod.strict(
    {
        "source_id": RECORD_ID,
        "artifact_sha256": SHA256,
        "locator": TEXT,
        "row_filter": TEXT,
        "row_count": INTEGER.check(zod.POSITIVE),
    }
)
DERIVATION: Final = zod.strict(
    {
        "method": TEXT,
        "inputs": zod.array(DERIVATION_INPUT, zod.min_length(1)),
        "aggregation": TEXT,
        "aggregation_source_id": RECORD_ID,
        "aggregation_locator": TEXT,
        "script": _REPO_PATH,
        "script_sha256": SHA256,
        "precision": TEXT,
        "note": zod.optional(TEXT),
    }
)
REPORTED_POPULATION: Final = zod.strict(
    {
        "count": zod.optional(INTEGER.check(zod.NONNEGATIVE)),
        "unit": zod.optional(TEXT),
        "train_validation_test": zod.optional(zod.Tuple(items=(INTEGER, INTEGER, INTEGER))),
        "note": zod.optional(TEXT),
    }
)

#: `attributeTypes`: the schema of each type the registry names, in the TypeScript's order.
ATTRIBUTE_TYPES: Final[Mapping[str, zod.Schema]] = {
    "text": TEXT,
    "integer": INTEGER,
    "number": zod.number(),
    "boolean": zod.Boolean(),
    "decimal": DECIMAL,
    "decimal-or-null": zod.nullable(DECIMAL),
    "date": DATE,
    "datetime": zod.union(DATE_TIME, DATE),
    "url": URL,
    "url-or-path": zod.union(URL, zod.text(zod.pattern(re.compile(r"/[A-Za-z0-9_./-]+"), r"^\/[\w./-]+$"))),
    "doi": zod.text(
        zod.pattern(
            re.compile(rf"10\.[0-9]{{4,9}}/[^{SPACE_CLASS}]+"),
            r"^10\.\d{4,9}\/\S+$",
            "Expected a bare DOI (10.xxxx/...)",
        )
    ),
    "sha256": SHA256,
    "git-sha": zod.text(zod.pattern(re.compile(r"[a-f0-9]{7,40}"), "^[a-f0-9]{7,40}$")),
    "record-id": RECORD_ID,
    "record-ids": zod.array(RECORD_ID),
    "entity-kinds": zod.array(zod.one_of(*KINDS)),
    "texts": zod.array(TEXT),
    "concept": TEXT,
    "concepts": zod.array(TEXT),
    "json": _JSON,
    "missing-metadata": MISSING_METADATA,
    "uncertainty": UNCERTAINTY,
    "coverage": COVERAGE,
    "review": REVIEW,
    "reported-population": REPORTED_POPULATION,
    "derivation": DERIVATION,
    "depth-tier": zod.union(zod.one_of(1), zod.one_of(2), zod.one_of(3)),
}


def _read_registry() -> JsonObject:
    text = resources.files("rewire_evidence").joinpath("data/attribute-registry.json").read_text("utf-8")
    return cast("JsonObject", json.loads(text))


#: The registry file as recorded: where it was read, its digest, the types and the kinds.
REGISTRY_FILE: Final = _read_registry()
#: `attributeRegistry`: for each kind, the type of each attribute it may carry.
REGISTRY: Final = cast("dict[str, dict[str, str]]", REGISTRY_FILE["kinds"])


def schema_for(declared: str) -> zod.Schema:
    """The schema of a declared type: `concept:<scheme>` is `concept`, and so for `concepts`."""
    if declared.startswith("concept:"):
        return ATTRIBUTE_TYPES["concept"]
    if declared.startswith("concepts:"):
        return ATTRIBUTE_TYPES["concepts"]
    return ATTRIBUTE_TYPES[declared]


def _has(held: Mapping[str, object], key: str) -> bool:
    """JavaScript's `key in object` for a plain object: its own members and the inherited ones."""
    return key in held or key in INHERITED


def attribute_issues(record: Record) -> list[str]:  # noqa: C901 - the TypeScript's one function, in its order
    """Problems with a record's attributes, in the TypeScript's words and order: `attributeIssues`.

    Raises `JsTypeError` for an attribute named as a member every JavaScript object has,
    unless it is null: the TypeScript takes it as declared and then fails.
    """
    declared = REGISTRY.get(record.kind, {})
    issues: list[str] = []
    for key, value in record.attributes.items():
        kind = declared.get(key)
        if kind is None:
            if key not in INHERITED:
                issues.append(f"undeclared attribute {key}")
                continue
            if value is not None:
                raise JsTypeError(
                    f"{record.kind} {record.id}: the attribute {key} is a member of every object"
                )
        if value is None and kind != "decimal-or-null":
            issues.append(f"{key} is null; omit it and give a reason in missing_metadata")
            continue
        assert kind is not None  # noqa: S101 - an inherited name is null or was refused above
        found = schema_for(kind).issues(value)
        if found:
            issues.append(f"{key} is not {kind}: {found[0].message}")
    missing = record.attributes.get("missing_metadata")
    if isinstance(missing, (dict, list)):
        names = list(missing) if isinstance(missing, dict) else [str(index) for index in range(len(missing))]
        for key in names:
            if _has(record.attributes, key) and key != "missing_metadata":
                issues.append(f"{key} has a value and a missing_metadata reason")
            if not _has(declared, key.split(".")[0]):
                issues.append(f"missing_metadata names undeclared field {key}")
    return issues


#: How many problems a refusal lists before it stops.
PROBLEM_LIMIT = 20


def attribute_problems(records: Sequence[Record]) -> list[str]:
    """Every attribute problem of every record, each naming its record."""
    return [f"{record.kind} {record.id}: {issue}" for record in records for issue in attribute_issues(record)]


def validate_attributes(records: Sequence[Record]) -> None:
    """Refuse records whose attributes are undeclared or badly typed: `validateAttributes`.

    Raises `RecordError` with the message the TypeScript throws.
    """
    problems = attribute_problems(records)
    if problems:
        listed = "\n".join(problems[:PROBLEM_LIMIT])
        more = "\n..." if len(problems) > PROBLEM_LIMIT else ""
        raise RecordError(f"{len(problems)} attribute problems:\n{listed}{more}")


# ---- records written before October 2026 ----------------------------------------------

#: Attributes the migration moved to `data/provenance/moved-attributes.jsonl`. Readers drop them.
MOVED_ATTRIBUTES = (
    "acquisition_candidate_id",
    "legacy_row",
    "legacy_paper",
    "legacy_import_source_id",
    "local_cache_path",
    "historical_entity_links",
    "historical_missing_metadata",
)
_DROPPED: Mapping[str, tuple[str, ...]] = {
    "*": (
        "metadata_review_scope",
        "benchmark_applicability",
        "published_score_reproduction",
        "suite_complete",
    ),
    "configuration": ("reference_kind", "entity_level"),
    "dataset": ("entity_level",),
    "dataset_subset": ("entity_level",),
    "evaluator": ("entity_level",),
    "pipeline": ("entity_level",),
    "protocol": ("entity_level",),
    "task": ("entity_level",),
    "evaluation": ("evaluation_group_note", "publication_status"),
    "benchmark": ("publication_status",),
}
_RENAMED: Mapping[str, Mapping[str, str]] = {
    "result": {"source_printed_cell": "printed_source_cell"},
    "source": {
        "article_license": "article_licence",
        "code_license": "code_licence",
        "license": "licence",
        "locator": "source_locator",
        "sha256": "artifact_sha256",
    },
    "dataset": {"scope": "scope_note"},
    "dataset_subset": {"dataset_id": "recipe_dataset_id"},
    "configuration": {"family": "family_label", "foundation_model": "foundation_model_eligible"},
    "method": {"family": "family_label"},
}
_COPIED_FROM_EVALUATION = ("same_paper_experiment_set", "total_targets", "missing_count", "evidence_overlap")
_NULL_MEANS_NONE = frozenset({"source_discrepancy", "undefined_reason"})

_INAPPLICABLE = re.compile(r"not[_ ]applicable|inapplicable")
_UNAVAILABLE = re.compile(r"inaccessible|not exposed|unpublished")
_UNEXTRACTED = re.compile(r"unextracted|not extracted|not[_ ]yet[_ ]extracted|not established|legacy")
_INTERVAL = re.compile(r"95% CI ([0-9.]+)%?[\u2013-]([0-9.]+)%?")
_REPLICATES = re.compile(r"([0-9,]+) bootstrap replicates")
_PLUS_MINUS = re.compile(rf"\u00b1[{SPACE_CLASS}]*([0-9.]+)({DOT}*)")
_FRACTION = re.compile(rf"([0-9]+)[{SPACE_CLASS}]*/[{SPACE_CLASS}]*([0-9]+)")
_COUNT = re.compile(r"[0-9]+")
_CONFIDENCE_INTERVAL = re.compile(rf"confidence{DOT}interval")

#: A reviewed table for free-text missing reasons: the text, its reason, and whether the text
#: is kept as the note. The reason `not_missing` drops the entry.
type MissingLookup = Mapping[str, tuple[str, bool]]
#: An object while it is being converted: a member may be `undefined`, and is then not written.
type _Loose = dict[str, object]


def _number(value: float) -> JsonValue:
    """A number as JavaScript would hold it, with null for NaN, which is how it is written."""
    return None if value != value else js_value(value)  # noqa: PLR0124 - NaN is the value unequal to itself


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _written(value: object) -> JsonValue:
    """A converted value as `JSON.stringify` would leave it: no member that is `undefined`."""
    if isinstance(value, dict):
        return {key: _written(member) for key, member in value.items() if not isinstance(member, Missing)}
    if isinstance(value, list):
        return [_written(item) for item in value]
    return cast("JsonValue", value)


def same_json(left: object, right: object) -> bool:
    """Whether `JSON.stringify` writes two values alike, which is how the TypeScript compares.

    Key order counts. Nothing at all equals nothing at all.
    """
    if left is right:
        return True
    if isinstance(left, Missing) or isinstance(right, Missing):
        return False
    return js_stringify(cast("JsonValue", left)) == js_stringify(cast("JsonValue", right))


def _guess_reason(note: str) -> str:
    lowered = note.lower()
    if _INAPPLICABLE.search(lowered):
        return "inapplicable"
    if _UNAVAILABLE.search(lowered):
        return "unavailable"
    if _UNEXTRACTED.search(lowered):
        return "unextracted"
    return "unreported"


def _missing_entry(value: object, lookup: MissingLookup | None = None) -> object:
    """One missing-value marker as `{reason, note?}`, or nothing if the table says it is not one."""
    if isinstance(value, dict) and isinstance(value.get("reason"), str):
        return value
    if not isinstance(value, str) or not trim(value):
        return {"reason": "unextracted"}
    if value in MISSING_REASONS:
        return {"reason": value}
    if value in INHERITED:
        # The TypeScript finds a member of every object where it looks for a reason. All but
        # one are functions, which are not written, so the entry has no reason at all.
        return {"reason": {}} if value == "__proto__" else {}
    row = None if lookup is None else lookup.get(value)
    if row is None:
        return {"reason": _guess_reason(value), "note": value}
    reason, keep_note = row
    if reason == "not_missing":
        return MISSING
    return {"reason": reason, "note": value} if keep_note else {"reason": reason}


def _decimal_text(value: object) -> object:
    if _is_number(value):
        return text_of(value)
    if isinstance(value, str) and _DECIMAL.fullmatch(trim(value)):
        return trim(value)
    return MISSING


def _first(*values: object) -> object:
    """`a ?? b ?? c`: the first that is neither null nor absent, or the last."""
    for value in values[:-1]:
        if value is not None and not isinstance(value, Missing):
            return value
    return values[-1]


def _unreported(note: object) -> tuple[object, object]:
    return MISSING, {"reason": "unreported", **({"note": note} if isinstance(note, str) else {})}


def _text_uncertainty(raw: str) -> tuple[object, object]:
    interval = _INTERVAL.match(raw)
    if interval:
        found: JsonObject = {
            "type": "confidence_interval",
            "printed": raw,
            "lower": interval.group(1),
            "upper": interval.group(2),
            "level": 0.95,
        }
        replicates = _REPLICATES.search(raw)
        if replicates:
            found["method"] = "bootstrap"
            found["resamples"] = _number(number_of_text(replicates.group(1).replace(",", "")))
        return found, MISSING
    spread = _PLUS_MINUS.fullmatch(raw)
    if spread:
        rest = trim(spread.group(2))
        deviation = "standard deviation" in rest
        return {
            "type": "standard_deviation" if deviation else "unresolved_spread",
            "printed": raw,
            "value": spread.group(1),
            **({"note": rest} if rest and not deviation else {}),
        }, MISSING
    return _unreported(raw)


def _typed_uncertainty(held: JsonObject, kind: str) -> _Loose:
    """The members an older uncertainty of each type is read from."""
    get = held.get
    spread = _first(get("value", MISSING), get("reported_spread", MISSING), get("printed_value", MISSING))
    if "bootstrap_summary" in kind:
        return {
            "type": "standard_deviation",
            "method": "bootstrap",
            "value": get("printed_sd", MISSING),
            "center": get("printed_mean", MISSING),
        }
    if "standard_deviation" in kind:
        return {"type": "standard_deviation", "value": spread}
    if "standard_error" in kind:
        return {
            "type": "standard_error",
            "value": spread,
            "method": "propagated" if "propagated" in kind else MISSING,
        }
    if _CONFIDENCE_INTERVAL.search(kind):
        given = get("level", MISSING)
        level: object = (
            _number(number_of_text(given.replace("%", "", 1)) / 100)
            if isinstance(given, str)
            else _first(given, get("confidence_level", MISSING))
        )
        return {
            "type": "confidence_interval",
            "level": level,
            "lower": _first(get("lower", MISSING), get("low", MISSING)),
            "upper": _first(get("upper", MISSING), get("high", MISSING)),
            "half_width": get("reported_half_width", MISSING),
            "resamples": _first(get("resamples", MISSING), get("replicates", MISSING)),
            "method": "bootstrap_percentile"
            if "percentile" in kind
            else "bootstrap"
            if "bootstrap" in kind
            else MISSING,
        }
    return {
        "type": "unresolved_spread",
        "value": _first(
            get("value", MISSING),
            get("printed_spread", MISSING),
            get("reported_spread", MISSING),
            get("printed_value", MISSING),
        ),
    }


def convert_uncertainty(raw: object) -> tuple[object, object]:  # noqa: C901, PLR0912 - one branch for each shape
    """One uncertainty in the declared shape, or the reason there is none: `convertUncertainty`.

    Returns the uncertainty and the missing reason. At most one of them is something.
    """
    if isinstance(raw, Missing):
        return MISSING, MISSING
    if raw is None:
        return MISSING, {"reason": "unextracted"}
    if isinstance(raw, str):
        return _text_uncertainty(raw)
    if not isinstance(raw, dict):
        return MISSING, MISSING
    get = raw.get
    if get("status") == "unreported" or (
        get("type") == "unreported"
        and "value" not in raw
        and "reported_spread" not in raw
        and "printed_spread" not in raw
    ):
        return _unreported(get("note", MISSING))
    if UNCERTAINTY.accepts(raw):
        return dict(raw), MISSING
    kind = said(_first(get("type", MISSING), get("kind", MISSING), ""))
    out = _typed_uncertainty(raw, kind)
    for key in ("value", "lower", "upper", "half_width", "center"):
        out[key] = _decimal_text(out.get(key, MISSING))
    printed = _first(get("printed", MISSING), get("printed_spread", MISSING))
    if isinstance(printed, str):
        out["printed"] = printed
    if _is_number(get("n", MISSING)):
        out["n"] = get("n")
    if isinstance(get("scope"), str) or isinstance(get("aggregation"), str):
        out["scope"] = _first(get("scope", MISSING), get("aggregation", MISSING))
    unit = get("unit")
    if isinstance(unit, str) and unit != "same_as_metric":
        out["unit"] = unit
    if isinstance(get("source_column"), str):
        out["source_column"] = get("source_column")
    unresolved = out["type"] == "unresolved_spread"
    if unresolved and isinstance(out["value"], Missing):
        return _unreported(f'printed spread "{printed}"' if isinstance(printed, str) else MISSING)
    note = get("note") if isinstance(get("note"), str) else None
    type_note = (
        kind if unresolved and kind not in {"unreported", "reported_plus_minus_type_unresolved"} else ""
    )
    if note or type_note:
        out["note"] = ". ".join(part for part in (type_note, note) if part)
    return _written(out), MISSING


def _convert_coverage(raw: object, missing: _Loose) -> object:
    if isinstance(raw, str):
        fraction = _FRACTION.fullmatch(raw)
        if fraction:
            return {
                "scored": _number(number_of_text(fraction.group(1))),
                "eligible": _number(number_of_text(fraction.group(2))),
            }
        return {"note": raw}
    if not isinstance(raw, dict):
        return MISSING
    out: _Loose = dict(raw)
    for key in ("scored", "eligible"):
        value = out.get(key)
        if isinstance(value, str):
            if _COUNT.fullmatch(value):
                out[key] = _number(number_of_text(value))
            else:
                _default(missing, f"coverage.{key}", _missing_entry(value))
                del out[key]
    return out


def _default(missing: _Loose, key: str, entry: object) -> None:
    """`missing[key] ??= entry`, which finds an inherited member where there is no own one."""
    if key not in missing and key not in INHERITED:
        missing[key] = entry


def _as_list(value: object) -> object:
    if isinstance(value, Missing):
        return MISSING
    return value if isinstance(value, list) else [value]


def _distinct(items: Sequence[object]) -> list[object]:
    """`[...new Set(items)]`: one of each text, number, boolean and null, and every list and object."""
    seen: set[tuple[type, object]] = set()
    out: list[object] = []
    for item in items:
        if isinstance(item, (dict, list)):
            out.append(item)
            continue
        key = (type(item), item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _convert_review(raw: object) -> object:
    if not isinstance(raw, dict):
        return MISSING
    review: _Loose = {key: value for key, value in raw.items() if value != "" and value is not None}
    review["method"] = _as_list(review.get("method", MISSING))
    reviewer = _as_list(review.get("reviewer", MISSING))
    actor = _as_list(review.get("actor", MISSING))
    reviewers = [
        *(reviewer if isinstance(reviewer, list) else []),
        *(actor if isinstance(actor, list) else []),
    ]
    review.pop("actor", None)
    if reviewers:
        review["reviewer"] = _distinct(reviewers)
    notes = review.get("notes")
    if isinstance(notes, str):
        note = review.get("note")
        review["note"] = f"{note}\n\n{notes}" if isinstance(note, str) and note != notes else notes
        del review["notes"]
    at = review.get("reviewed_at")
    if isinstance(at, str) and _DATE.fullmatch(at) and ("date" not in review or review["date"] == at):
        review["date"] = at
        del review["reviewed_at"]
    at = review.get("reviewed_at")
    if isinstance(at, str) and review.get("date", MISSING) == slice_units(at, 10):
        review.pop("date", None)
    return review


def _source_review(attributes: _Loose) -> None:
    """A source's `review_method` and `review_scope`, moved into its review."""
    held = attributes.get("review", MISSING)
    review: _Loose = held if isinstance(held, dict) else {}
    method_given = attributes.get("review_method", MISSING)
    if not isinstance(method_given, Missing):
        given = said(method_given)
        if given == "automated_source_review":
            method, method_note = "automated-source-review", ""
        else:
            method = (
                "ai-assisted-source-review" if given.startswith("AI-assisted") else "automated-source-review"
            )
            method_note = given
        before = review.get("method", MISSING)
        review["method"] = _distinct([*(before if isinstance(before, list) else []), method])
        if method_note:
            earlier = review.get("method_note", MISSING)
            review["method_note"] = f"{said(earlier)}\n\n{method_note}" if truthy(earlier) else method_note
    scope = attributes.get("review_scope", MISSING)
    if not isinstance(scope, Missing):
        review["scope"] = scope
    if review.get("method", MISSING) is None or isinstance(review.get("method", MISSING), Missing):
        review["method"] = ["automated-source-review"]
    attributes["review"] = review
    attributes.pop("review_method", None)
    attributes.pop("review_scope", None)


def _attributes_of(row: JsonObject | None) -> JsonObject:
    held = None if row is None else row.get("attributes")
    return held if isinstance(held, dict) else {}


def _linked(row: JsonObject, relation: str, by_id: Mapping[str, JsonObject]) -> JsonObject | None:
    """The record the first link of a relation points at, if it is there."""
    links = row.get("links")
    for link in links if isinstance(links, list) else []:
        if isinstance(link, dict) and link.get("relation") == relation:
            target = link.get("target_id")
            return by_id.get(target) if isinstance(target, str) else None
    return None


def _protocol(a: _Loose, missing: _Loose) -> None:
    extra: list[JsonValue] = []
    for held in (a.get("caveats", MISSING), a.get("important_limitation", MISSING)):
        extra.extend(item for item in (held if isinstance(held, list) else [held]) if isinstance(item, str))
    if extra:
        before = a.get("limitations", MISSING)
        kept = before if isinstance(before, list) else [before] if truthy(before) else []
        a["limitations"] = [*kept, *extra]
    a.pop("caveats", None)
    a.pop("important_limitation", None)
    notes = {
        "definition": a.get("uncertainty_definition", MISSING),
        "mechanism": a.get("uncertainty_mechanism", MISSING),
        "note": a.get("uncertainty_note", MISSING),
    }
    stated = {key: value for key, value in notes.items() if not isinstance(value, Missing)}
    if stated:
        a["uncertainty_reporting"] = stated
    for key in ("uncertainty_definition", "uncertainty_mechanism", "uncertainty_note"):
        a.pop(key, None)
    if isinstance(a.get("uncertainty"), str):
        missing["uncertainty"] = {"reason": "unreported", "note": a.pop("uncertainty")}
    if isinstance(a.get("procedure"), dict):
        a["protocol_configuration"] = a.pop("procedure")


def _evaluation(a: _Loose, row: JsonObject, by_id: Mapping[str, JsonObject]) -> None:
    protocol = a.get("protocol")
    if isinstance(protocol, dict):
        a["protocol"] = protocol.get("name", MISSING)
        if "version" in protocol:
            a["protocol_version"] = protocol["version"]
        if "proposed_id" in protocol:
            a["proposed_protocol_id"] = protocol["proposed_id"]
    run_url = a.get("run_url")
    if isinstance(run_url, str):
        if run_url == a.get("reproduction_url", MISSING):
            del a["run_url"]
        elif run_url.startswith("/"):
            a["run_page"] = a.pop("run_url")
    configuration = _linked(row, "system", by_id)
    if configuration is not None and same_json(
        _attributes_of(configuration).get("configuration", MISSING), a.get("model_configuration", MISSING)
    ):
        a.pop("model_configuration", None)


def _result(a: _Loose, missing: _Loose, row: JsonObject, by_id: Mapping[str, JsonObject]) -> None:
    parent = _linked(row, "evaluation", by_id)
    for key in _COPIED_FROM_EVALUATION:
        if key in a and parent is not None and same_json(_attributes_of(parent).get(key, MISSING), a[key]):
            del a[key]
    if "source_uncertainty_note" in a:
        note = a.pop("source_uncertainty_note")
        held = a.get("uncertainty")
        if isinstance(held, dict):
            parts = [part for part in (held.get("note", MISSING), note) if truthy(part)]
            a["uncertainty"] = {
                **held,
                "note": ". ".join(joined([cast("JsonValue", part)]) for part in parts),
            }
        else:
            before = missing.get("uncertainty", MISSING)
            base = before if isinstance(before, dict) else {"reason": "unreported"}
            missing["uncertainty"] = {**base, "note": note}
    uncertainty, reason = convert_uncertainty(a.pop("uncertainty", MISSING))
    if truthy(uncertainty):
        a["uncertainty"] = uncertainty
        missing.pop("uncertainty", None)
    elif truthy(reason) and not truthy(missing.get("uncertainty", MISSING)):
        missing["uncertainty"] = reason


def _reported_population(a: _Loose, missing: _Loose) -> None:
    held = a.get("reported_population")
    if not isinstance(held, dict):
        return
    population = {key: value for key, value in held.items() if value is not None}
    if "count" not in population and "train_validation_test" not in population:
        del a["reported_population"]
        note = population.get("note")
        _default(
            missing,
            "reported_population",
            {"reason": "unextracted", **({"note": note} if isinstance(note, str) else {})},
        )
    else:
        a["reported_population"] = population


def _convert(  # noqa: C901, PLR0912, PLR0915 - one branch for each older shape, in the TypeScript's order
    row: JsonObject,
    original: JsonObject,
    by_id: Mapping[str, JsonObject],
    evaluations: Mapping[str, list[JsonObject]],
    lookup: MissingLookup | None,
) -> JsonObject:
    """One record's attributes in the declared shapes, or `original` itself if they are: `convert`."""
    kind = row.get("kind")
    a: _Loose = dict(original)
    for key in (
        *MOVED_ATTRIBUTES,
        *_DROPPED["*"],
        *(_DROPPED.get(kind, ()) if isinstance(kind, str) else ()),
    ):
        a.pop(key, None)
    for old, new in (_RENAMED.get(kind, {}) if isinstance(kind, str) else {}).items():
        if old in a:
            if new in a and not same_json(a[new], a[old]):
                raise RecordError(f"{said(row.get('id', MISSING))}: {old} and {new} differ")
            if new not in a:
                a[new] = a[old]
            del a[old]

    missing: _Loose = {}
    held = a.get("missing_metadata")
    if isinstance(held, dict):
        for key, value in held.items():
            entry = _missing_entry(value, lookup)
            if not isinstance(entry, Missing):
                missing[key] = entry

    if kind in {"method", "model", "service"}:
        a.pop("method_type", None)
    if kind == "protocol":
        _protocol(a, missing)
    if kind == "evaluation":
        _evaluation(a, row, by_id)
    if kind == "configuration" and "execution" in a:
        record_id = row.get("id")
        tested = evaluations.get(record_id, []) if isinstance(record_id, str) else []
        if tested and all(
            same_json(_attributes_of(each).get("execution", MISSING), a["execution"]) for each in tested
        ):
            del a["execution"]
    if kind == "dataset" and isinstance(a.get("population"), dict):
        a["population_detail"] = a.pop("population")
    _reported_population(a, missing)
    derived = a.get("derived_normalized_value", MISSING)
    if _is_number(derived):
        a["derived_normalized_value"] = text_of(derived)
    if kind == "result":
        _result(a, missing, row, by_id)
    if "coverage" in a:
        coverage = _convert_coverage(a["coverage"], missing)
        if isinstance(coverage, dict) and coverage:
            a["coverage"] = coverage
        else:
            del a["coverage"]
    if "review" in a:
        a["review"] = _convert_review(a["review"])
    if kind == "source" and ("review_method" in a or "review_scope" in a):
        _source_review(a)

    # Bare nulls become absence; the reason, if not already given, is "unextracted".
    for key, value in list(a.items()):
        if value is not None or key == "numeric_value":
            continue
        del a[key]
        if key not in _NULL_MEANS_NONE:
            _default(missing, key, {"reason": "unextracted"})
    for key in list(missing):
        if _has(a, key):
            del missing[key]
    a.pop("missing_metadata", None)
    if missing:
        a["missing_metadata"] = {key: missing[key] for key in sorted(missing, key=utf16_key)}
    # The original key order is kept so unchanged records keep their bytes; new keys go last.
    order = [key for key in original if _has(a, key)] + [key for key in a if not _has(original, key)]
    keys = js_key_order(key for key in order if not isinstance(a.get(key, MISSING), Missing))
    # Most records come out as they went in, member for member, and are not written again.
    if keys == list(original) and all(
        a[key] is original[key] or same_json(_written(a[key]), original[key]) for key in keys
    ):
        return original
    return cast("JsonObject", js_value(_written({key: a[key] for key in keys})))


def normalize_attributes(rows: list[JsonObject], lookup: MissingLookup | None = None) -> list[JsonObject]:
    """Records with attributes in the declared shapes: `normalizeAttributes`.

    Works on rows, not on `Record`, because the older shapes are not ones the current
    schema's validators accept. A row whose attributes come out as they went in is returned
    as it is, the same object. A changed row is a new row and the one given is not touched.
    The rows must list their keys as JavaScript would, as `js_value` and `Record.row` do.
    A row is found by its ID as it is written, so an ID that is not text finds nothing.

    Raises `RecordError`, as the TypeScript throws, for a record holding one attribute
    under its older and its current name with different values, and `JsTypeError` where the
    TypeScript fails making text of an object with a member named `toString`.
    """
    by_id: dict[str, JsonObject] = {}
    evaluations: dict[str, list[JsonObject]] = {}
    for row in rows:
        record_id = row.get("id")
        if isinstance(record_id, str):
            by_id[record_id] = row
    for row in rows:
        if row.get("kind") != "evaluation":
            continue
        links = row.get("links")
        for link in links if isinstance(links, list) else []:
            target = link.get("target_id") if isinstance(link, dict) else None
            if isinstance(link, dict) and link.get("relation") == "system" and isinstance(target, str):
                evaluations.setdefault(target, []).append(row)
    out: list[JsonObject] = []
    for row in rows:
        original = _attributes_of(row)
        attributes = _convert(row, original, by_id, evaluations, lookup)
        out.append(row if attributes is original else {**row, "attributes": attributes})
    return out
