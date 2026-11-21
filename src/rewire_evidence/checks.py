r"""The validator pack: seven deterministic checks over the store, report only.

This is `scripts/omics/checks/` of the data repository: `finding.ts`, the seven checks and
the half of `cli.ts` that loads the store and runs them. The other half, the baseline and
the ratchet, is `rewire_evidence.ratchet`.

A check is a function over the store and two vocabularies. It reads nothing else and returns
findings. Every finding is report severity: it names a record and a rule, and does not decide
whether the record is wrong. The checks are

- C1 `unit_for_metric`: a result whose unit is not its metric's majority unit, or is one a
  unit's scope note rules out for that metric.
- C2 `direction_default`: a result's direction against its metric's default direction.
- C3 `printed_vs_numeric`: `numeric_value` against `printed_value` as `parse_cell` reads it.
- C4 `qualifier_shape`: a `metric_qualifier` holding a bare verdict or a vendor's effort label.
- C5 `column_coherence`: results that claim one printed column and hold more than one
  metric, unit and direction.
- C9 `duplicate_artifact`: sources that pin the same bytes at the same URL.
- C13 `normalise_or_refuse`: a stored record `normalize_attributes` would rewrite.

The store is read with `read_kind`, which gives the stored JSON as it is, so the checks
report on what the store holds and do not refuse to run on it.

What is held to the TypeScript is what it does with a record whose `id` is text, whose
`source_ids` are a list or absent, and whose `attributes` are anything. A record with an ID
that is not text, or source IDs that are not a list, is refused with `StoreError`, where the
TypeScript would write `undefined` into a report or fail on a type. The store reader refuses
the first before any check runs.

The order of the findings is part of the report: the checks in the order of `CHECKS`, and
inside each the order its TypeScript gives, which is the order of the store for C1 to C4 and
C13, the order each column is first met for C5, and the order of the subjects for C9. Text is
ordered as JavaScript orders it, by UTF-16 unit.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rewire_evidence.attributes import normalize_attributes
from rewire_evidence.canonical import (
    JsonObject,
    JsonValue,
    js_key_order,
    js_number,
    js_quote,
    js_written,
    utf16_key,
)
from rewire_evidence.cells import CellError, parse_cell
from rewire_evidence.js import (
    MISSING,
    Held,
    Missing,
    distinct,
    join,
    lower,
    number_of_text,
    paired,
    text_of,
    trim,
    unit_text,
)
from rewire_evidence.jsonl import StoreError
from rewire_evidence.jsregex import JsPattern
from rewire_evidence.schema import KINDS
from rewire_evidence.store import read_kind
from rewire_evidence.turtle import BlankLabels
from rewire_evidence.vocab import VOCAB_DIR, read_scheme_file, scheme_iri

#: The only severity the pack emits.
REPORT = "report"
TSV_HEADER = "check\trule\tseverity\tsubject\tdetail"

_SKOS = "http://www.w3.org/2004/02/skos/core#"
_IN_SCHEME = f"{_SKOS}inScheme"
_SCOPE_NOTE = f"{_SKOS}scopeNote"
_DEFAULT_DIRECTION = "https://benchmarks.rewire.it/vocab#defaultDirection"

#: Tabs and line ends, which would break a row of the report.
LINE_BREAKS = JsPattern(r"[\t\r\n]+", "g")
#: Runs of white space, made one space of in an anchor and in a column name.
SPACES = JsPattern(r"\s+", "g")
#: The quotation marks a column name is written in.
QUOTES = JsPattern("[\"']", "g")
#: An exponent, whose printed precision is not read off its digits.
EXPONENT = JsPattern("[eE]")
#: "Table 2", "Supplementary Table 6", "Extended Data Fig. 3b", "Additional file 2 Table S3".
ANCHOR = JsPattern(
    r"^\s*(?<anchor>(?:appendix\s+|supplementary\s+|supplemental\s+|extended\s+data\s+"
    r"|additional\s+file\s+\d+,?\s+)*(?:table|tables|figure|fig\.?)\s*[a-z]{0,2}\d+(?:\.\d+)*[a-z]?)",
    "i",
)
#: The column name in any of the spellings the store uses: column "X", column 'X', column(X),
#: column X. "column group" names the span above the column.
COLUMN = JsPattern(
    r"\bcolumn(?<group>\s+group)?\s*"
    r"""(?:"(?<double>[^"]*)"|'(?<single>[^']*)'|\((?<round>[^)]*)\)|(?<bare>[^,;]+))""",
    "gi",
)
_COLUMN_VALUES = ("double", "single", "round", "bare")

#: The tokens of C4: verdicts first, then vendor effort labels.
TOKENS = ("yes", "no", "max", "high", "xhigh", "low effort", "medium effort")
#: One pattern for each token, which stands alone between the start or the end, white space, a
#: comma, a semicolon or a bracket. No token holds a character a pattern reads as syntax.
TOKEN_PATTERNS: tuple[tuple[str, JsPattern], ...] = tuple(
    (token, JsPattern(rf"(?:^|[;,(\s]){token}(?:$|[;,)\s])", "i")) for token in TOKENS
)

#: Every pattern this module runs, for the list of what has to be recorded from Node.
PATTERNS: tuple[JsPattern, ...] = (
    LINE_BREAKS,
    SPACES,
    QUOTES,
    EXPONENT,
    ANCHOR,
    COLUMN,
    *(pattern for _, pattern in TOKEN_PATTERNS),
)

#: `same` compares at this relative tolerance.
TOLERANCE = 1e-12


@dataclass(frozen=True)
class Finding:
    """What a check reports: a record or a group, and the rule it met."""

    #: The check that raised it: C1, C2, C3, C4, C5, C9 or C13.
    check: str
    #: The rule inside the check. Baselines and the ratchet are by rule.
    rule: str
    #: The record ID, or the key of a group when the finding is about several records.
    subject: str
    #: One line of evidence, with the values that met the rule.
    detail: str
    severity: str = REPORT

    def row(self) -> JsonObject:
        """The finding with its members in the order the TypeScript declares them."""
        return {
            "check": self.check,
            "rule": self.rule,
            "severity": self.severity,
            "subject": self.subject,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class MetricConcept:
    """A metric concept. `default_direction` is None when the concept declares none."""

    key: str
    default_direction: str | None


@dataclass(frozen=True)
class UnitConcept:
    """A unit concept, with its scope note if it carries one."""

    key: str
    scope_note: str | None


@dataclass(frozen=True)
class CheckInput:
    """What the checks read: the stored records and the two vocabularies."""

    #: Every result record.
    results: Sequence[JsonObject]
    #: Every source record.
    sources: Sequence[JsonObject]
    #: Every record of every kind, which C13 needs to resolve a result's evaluation.
    records: Sequence[JsonObject]
    metrics: Mapping[str, MetricConcept]
    units: Mapping[str, UnitConcept]


@dataclass(frozen=True)
class Check:
    """One check: its rules, and the functions that run it and describe the run."""

    id: str
    title: str
    #: Every rule the check can raise, in report order. A rule with no findings is still counted.
    rules: tuple[str, ...]
    run: Callable[[CheckInput], list[Finding]]
    #: Counts that describe the run and not the findings. The ratchet does not test them.
    context: Callable[[CheckInput, Sequence[Finding]], dict[str, int]] | None = None


@dataclass(frozen=True)
class CheckSummary:
    """One check's counts for a run."""

    id: str
    title: str
    findings: int
    rules: dict[str, int]
    context: dict[str, int]

    def row(self) -> JsonObject:
        """The summary with its members in the order the TypeScript declares them."""
        return {
            "id": self.id,
            "title": self.title,
            "findings": self.findings,
            "rules": dict(self.rules),
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class RunResult:
    """Every finding of a run, and each check's counts."""

    findings: list[Finding]
    summaries: list[CheckSummary]


def text(record: JsonObject, key: str) -> str | None:
    """A text attribute, or None when it is absent, is not text, or the record has no attributes."""
    attributes = record.get("attributes")
    value = attributes.get(key) if isinstance(attributes, dict) else None
    return value if isinstance(value, str) else None


def subject_of(record: JsonObject) -> str:
    """A record's ID. Raises `StoreError` for one that is not text."""
    record_id = record.get("id")
    if not isinstance(record_id, str):
        raise StoreError(
            f"a record given to the checks has the ID {js_written(record_id)}, which is not text"
        )
    return record_id


def one_line(value: str) -> str:
    """`oneLine(value)`: tabs and line ends shown as spaces, so the value is one cell of a row."""
    return paired(trim(LINE_BREAKS.replace(unit_text(value), " ")))


def tsv_row(finding: Finding) -> str:
    """`tsvRow(finding)`: the finding as a row of the report."""
    return "\t".join(
        [finding.check, finding.rule, finding.severity, finding.subject, one_line(finding.detail)]
    )


def report_tsv(findings: Sequence[Finding]) -> str:
    """The report `checks -- report` writes: a header and one row for each finding."""
    return "\n".join([TSV_HEADER, *map(tsv_row, findings)]) + "\n"


def _in_js_order(counts: dict[str, int]) -> dict[str, int]:
    """The counts in the order JavaScript lists an object's members."""
    return {key: counts[key] for key in js_key_order(counts)}


def count_by_rule(check: Check, findings: Sequence[Finding]) -> dict[str, int]:
    """`countByRule`: findings by rule, with every declared rule present even at zero."""
    counts = dict.fromkeys(check.rules, 0)
    for finding in findings:
        counts[finding.rule] = counts.get(finding.rule, 0) + 1
    return _in_js_order(counts)


# ---------------------------------------------------------------------------------------
# C1: the unit a result carries for its metric.


@dataclass(frozen=True)
class ScopeNoteRule:
    """One unit's scope note, as the metrics it rules a unit out for. Transcribed by hand."""

    #: The unit concept that carries the note. The rule holds only while it still carries one.
    note: str
    #: The unit the note rules out.
    unit: str
    metrics: tuple[str, ...]
    reason: str


#: Transcribed in the TypeScript from the three `skos:scopeNote` values in `data/vocab/unit.ttl`.
SCOPE_NOTE_RULES: tuple[ScopeNoteRule, ...] = (
    ScopeNoteRule(
        "fraction",
        "fraction",
        ("tm-score", "ndcg", "ndcg-at-10-percent", "adjusted-ndcg-at-100", "auroc", "auroc-difference"),
        "the fraction scope note says normalised scores such as TM-score, NDCG and AUROC are unitless",
    ),
    ScopeNoteRule(
        "unitless",
        "percent",
        (
            "matthews-correlation-coefficient",
            "macro-mcc",
            "pearson-r-squared",
            "spearman-correlation",
            "absolute-spearman-correlation",
            "spearman-correlation-difference",
            "auroc",
            "auroc-difference",
            "auprc",
        ),
        "the unitless scope note says MCC, R2, Spearman and AUC printed x100 stay unitless with "
        "unit_detail, because percent would imply a proportion",
    ),
    ScopeNoteRule(
        "count",
        "count",
        ("top-1-mces", "top-10-mces"),
        "the count scope note says MCES edit distance is unitless, not a count",
    ),
)


def majority_units(results: Sequence[JsonObject]) -> dict[str, tuple[str, int]]:
    """The majority unit of each metric, where one exists, with how many results carry it.

    A metric has one when its commonest unit is at least as common as every other unit of
    that metric put together. Of two units equally common, the first in text order is taken.
    """
    by_metric: dict[str, dict[str, int]] = {}
    for record in results:
        metric, unit = text(record, "metric"), text(record, "unit")
        if not metric or not unit:
            continue
        units = by_metric.setdefault(metric, {})
        units[unit] = units.get(unit, 0) + 1
    majority: dict[str, tuple[str, int]] = {}
    for metric, units in by_metric.items():
        if len(units) < 2:  # noqa: PLR2004 - one unit is no convention to be in a minority of
            continue
        ordered = sorted(units.items(), key=lambda pair: (-pair[1], utf16_key(pair[0])))
        unit, count = ordered[0]
        if sum(n for _, n in ordered[1:]) <= count:
            majority[metric] = (unit, count)
    return majority


def _unit_for_metric(given: CheckInput) -> list[Finding]:
    findings: list[Finding] = []
    majority = majority_units(given.results)
    for record in given.results:
        metric, unit = text(record, "metric"), text(record, "unit")
        if not metric or not unit:
            continue
        convention = majority.get(metric)
        if convention is not None and unit != convention[0]:
            findings.append(
                Finding(
                    "C1",
                    "minority-unit",
                    subject_of(record),
                    f"metric {metric} unit {unit}; the majority unit for this metric is "
                    f"{convention[0]} ({convention[1]} results)",
                )
            )
        for rule in SCOPE_NOTE_RULES:
            if unit != rule.unit or metric not in rule.metrics:
                continue
            concept = given.units.get(rule.note)
            if concept is None or not concept.scope_note:
                continue
            findings.append(
                Finding(
                    "C1", "unit-scope-note", subject_of(record), f"metric {metric} unit {unit}; {rule.reason}"
                )
            )
    return findings


def _unit_for_metric_context(given: CheckInput, findings: Sequence[Finding]) -> dict[str, int]:
    metric_of = {subject_of(record): text(record, "metric") for record in given.results}
    metrics = {
        metric
        for finding in findings
        if finding.rule == "minority-unit" and (metric := metric_of.get(finding.subject))
    }
    return {
        "metrics_with_a_minority_unit": len(metrics),
        "metrics_with_a_majority_unit": len(majority_units(given.results)),
    }


UNIT_FOR_METRIC = Check(
    "C1", "unit for metric", ("minority-unit", "unit-scope-note"), _unit_for_metric, _unit_for_metric_context
)

# ---------------------------------------------------------------------------------------
# C2: metric_direction against the metric concept's default direction.


def _direction_default(given: CheckInput) -> list[Finding]:
    findings: list[Finding] = []
    for record in given.results:
        metric, direction = text(record, "metric"), text(record, "metric_direction")
        if not metric or not direction:
            continue
        concept = given.metrics.get(metric)
        if concept is None:
            rule, detail = (
                "metric-not-a-concept",
                f"metric {metric} is not a concept in data/vocab/metric.ttl",
            )
        elif concept.default_direction is None:
            if direction == "unknown":
                continue
            rule = "asserted-where-none"
            detail = f"metric {metric} declares no default direction; the result asserts {direction}"
        elif direction == "unknown":
            rule = "unknown-where-declared"
            detail = (
                f"metric {metric} declares default direction {concept.default_direction}; "
                "the result stores unknown"
            )
        elif direction != concept.default_direction:
            rule = "opposite"
            detail = (
                f"metric {metric} declares default direction {concept.default_direction}; "
                f"the result stores {direction}"
            )
        else:
            continue
        findings.append(Finding("C2", rule, subject_of(record), detail))
    return findings


def _direction_default_context(given: CheckInput, _findings: Sequence[Finding]) -> dict[str, int]:
    declaring = sum(concept.default_direction is not None for concept in given.metrics.values())
    return {"metric_concepts": len(given.metrics), "metric_concepts_declaring_a_direction": declaring}


DIRECTION_DEFAULT = Check(
    "C2",
    "direction against the metric default",
    ("opposite", "unknown-where-declared", "asserted-where-none", "metric-not-a-concept"),
    _direction_default,
    _direction_default_context,
)

# ---------------------------------------------------------------------------------------
# C3: numeric_value against printed_value.


def same(a: float, b: float) -> bool:
    """`same(a, b)`: equal, or within the relative tolerance of each other.

    Two infinities are the same, and so, as in the TypeScript, are an infinity and any
    finite number: their difference and the tolerance scaled by the larger are both infinite.
    """
    # Where either is not a number the difference is not one either, and nothing is at most it,
    # so it does not matter that `max` and `Math.max` choose differently there.
    return a == b or abs(a - b) <= max(abs(a), abs(b)) * TOLERANCE


def js_round(value: float) -> float:
    """`Math.round(value)`: the nearest whole number, and the larger of two equally near.

    Python's `round` takes the even one of two, and `floor(value + 0.5)` is wrong for the
    largest number below one half, where the sum rounds up to one.
    """
    if not math.isfinite(value):
        return value
    below = math.floor(value)
    rounded = float(below + 1 if value - below >= 0.5 else below)  # noqa: PLR2004 - half
    # Math.round keeps the sign of a number that rounds to zero from below it.
    return math.copysign(rounded, value) if rounded == 0 else rounded


def power_of_ten(places: int) -> float:
    """`10 ** places` for a count of decimal places: the nearest double, or infinity."""
    return float(f"1e{places}")


def rounds_to(value: float, printed: str) -> bool:
    """`roundsTo(value, printed)`: whether the value rounds, half up, to the printed decimal.

    The precision is the number of digits `printed` has after its point. A printed value
    with an exponent never matches.
    """
    if EXPONENT.test(printed):
        return False
    places = len(printed.split(".")[1]) if "." in printed else 0
    scale = power_of_ten(places)
    sign = math.copysign(1.0, value) if value else value
    rounded = sign * js_round(abs(value) * scale) / scale
    return same(rounded, number_of_text(printed))


def _context_suffix(record: JsonObject) -> str:
    """The two attributes a triage splits the queue by, named when the record has them."""
    attributes = record.get("attributes")
    held = attributes if isinstance(attributes, dict) else {}
    named = [f"has {name}" for name in ("printed_value_basis", "workbook_number_format") if name in held]
    return f"; {'; '.join(named)}" if named else ""


def _printed_vs_numeric(given: CheckInput) -> list[Finding]:
    findings: list[Finding] = []
    for record in given.results:
        printed, numeric = text(record, "printed_value"), text(record, "numeric_value")
        unit = text(record, "unit")
        if printed is None or numeric is None:
            continue
        suffix = _context_suffix(record)
        shown_unit = "none" if unit is None else unit
        want = number_of_text(numeric)
        if not math.isfinite(want):
            rule = "numeric-not-a-number"
            detail = (
                f"numeric_value {js_quote(numeric)} is not a finite number; "
                f"printed_value {js_quote(printed)}{suffix}"
            )
            findings.append(Finding("C3", rule, subject_of(record), detail))
            continue
        try:
            cell = parse_cell(printed, "percent" if unit == "percent" else None)
        except CellError as error:
            detail = (
                f"parseCell refuses printed_value {js_quote(printed)} (unit {shown_unit}): {error}{suffix}"
            )
            findings.append(Finding("C3", "unparseable-printed", subject_of(record), detail))
            continue
        if cell.value is None:
            detail = (
                f"parseCell reads printed_value {js_quote(printed)} as not reported, "
                f"but numeric_value is {numeric}{suffix}"
            )
            findings.append(Finding("C3", "unparseable-printed", subject_of(record), detail))
            continue
        got = number_of_text(cell.value)
        if same(got, want):
            continue
        if same(got * 100, want) or same(got / 100, want):
            rule = "rescaled-by-100"
        elif rounds_to(want, cell.value):
            rule = "printed-is-rounded"
        elif cell.sd is not None and same(number_of_text(cell.sd), want):
            rule = "numeric-is-the-second-number"
        else:
            rule = "differs"
        detail = (
            f"printed_value {js_quote(printed)} reads as {js_number(got)}; "
            f"numeric_value is {numeric} (unit {shown_unit}){suffix}"
        )
        findings.append(Finding("C3", rule, subject_of(record), detail))
    return findings


def _printed_vs_numeric_context(given: CheckInput, _findings: Sequence[Finding]) -> dict[str, int]:
    compared = sum(
        text(record, "printed_value") is not None and text(record, "numeric_value") is not None
        for record in given.results
    )
    return {"results_with_both_values": compared, "results_skipped": len(given.results) - compared}


PRINTED_VS_NUMERIC = Check(
    "C3",
    "printed value against numeric value",
    (
        "differs",
        "numeric-is-the-second-number",
        "printed-is-rounded",
        "rescaled-by-100",
        "unparseable-printed",
        "numeric-not-a-number",
    ),
    _printed_vs_numeric,
    _printed_vs_numeric_context,
)

# ---------------------------------------------------------------------------------------
# C4: what metric_qualifier holds.


def _qualifier_shape(given: CheckInput) -> list[Finding]:
    findings: list[Finding] = []
    for record in given.results:
        qualifier = text(record, "metric_qualifier")
        if not qualifier:
            continue
        seen = unit_text(qualifier)
        findings.extend(
            Finding(
                "C4",
                "verdict-or-vendor-token",
                subject_of(record),
                f'metric_qualifier holds the token "{token}": {js_quote(qualifier)}',
            )
            for token, pattern in TOKEN_PATTERNS
            if pattern.test(seen)
        )
    return findings


def _qualifier_shape_context(given: CheckInput, findings: Sequence[Finding]) -> dict[str, int]:
    qualifiers = [unit_text(held) for record in given.results if (held := text(record, "metric_qualifier"))]
    counts = {
        "results_with_a_qualifier": len(qualifiers),
        "results_flagged": len({finding.subject for finding in findings}),
    }
    for token, pattern in TOKEN_PATTERNS:
        counts[f"token_{token.replace(' ', '_')}"] = sum(pattern.test(seen) for seen in qualifiers)
    return counts


QUALIFIER_SHAPE = Check(
    "C4", "qualifier shape", ("verdict-or-vendor-token",), _qualifier_shape, _qualifier_shape_context
)

# ---------------------------------------------------------------------------------------
# C5: results that claim the same printed column of the same table.


def anchor_of(locator: str) -> str | None:
    """`anchorOf(locator)`: the table or figure a locator starts with, in lowercase."""
    found = ANCHOR.exec(unit_text(locator))
    if found is None:
        return None
    return lower(paired(trim(SPACES.replace(found.groups["anchor"] or "", " "))))


def column_of(locator: str) -> str | None:
    """`columnOf(locator)`: the last column a locator names that is not a column group."""
    column: str | None = None
    for found in COLUMN.all(unit_text(locator)):
        if found.groups["group"]:
            continue
        held = next((value for name in _COLUMN_VALUES if (value := found.groups[name]) is not None), "")
        value = SPACES.replace(QUOTES.replace(trim(held), ""), " ")
        if value:
            column = lower(paired(value))
    return column


def _source_ids(record: JsonObject) -> list[JsonValue]:
    """A record's source IDs, or none. Raises `StoreError` for a value that is not a list."""
    held = record.get("source_ids")
    if held is None:
        return []
    if not isinstance(held, list):
        raise StoreError(f"the record {subject_of(record)} has source IDs that are not a list")
    return held


def _key_of(record: JsonObject) -> str:
    """The comparability key of a result inside a column: metric, unit and direction."""
    return " / ".join(text(record, name) or "" for name in ("metric", "unit", "metric_direction"))


@dataclass(frozen=True)
class Columns:
    """The results placed in printed columns, and the ones that could not be."""

    #: The results of each column, by the column's key, in the order each is first met.
    grouped: dict[str, list[JsonObject]]
    no_anchor: int
    no_column: int


def columns(results: Sequence[JsonObject]) -> Columns:
    """Group results by the sources they cite, the table or figure, and the column of it."""
    grouped: dict[str, list[JsonObject]] = {}
    no_anchor = no_column = 0
    for record in results:
        locator = text(record, "source_locator")
        anchor = anchor_of(locator) if locator else None
        if not anchor:
            no_anchor += 1
            continue
        column = column_of(locator or "")
        if not column:
            no_column += 1
            continue
        # `sort()` orders the IDs as text, and `join` writes nothing for a null.
        sources: Sequence[Held] = sorted(_source_ids(record), key=lambda held: utf16_key(text_of(held)))
        grouped.setdefault(f"{join(sources, '+')} | {anchor} | column {column}", []).append(record)
    return Columns(grouped, no_anchor, no_column)


def _keys(records: Sequence[JsonObject]) -> dict[str, int]:
    keys: dict[str, int] = {}
    for record in records:
        key = _key_of(record)
        keys[key] = keys.get(key, 0) + 1
    return keys


def _column_coherence(given: CheckInput) -> list[Finding]:
    findings: list[Finding] = []
    for group, records in columns(given.results).grouped.items():
        keys = _keys(records)
        if len(keys) < 2:  # noqa: PLR2004 - a column with one key is coherent
            continue
        # The commonest key first, and keys equally common in the order they were met.
        shown = "; ".join(
            f"{key} ({count})" for key, count in sorted(keys.items(), key=lambda pair: -pair[1])
        )
        detail = (
            f"{len(records)} results in this column hold {len(keys)} metric / unit / direction keys: {shown}"
        )
        findings.append(Finding("C5", "column-splits-comparability-key", group, detail))
    return findings


def _column_coherence_context(given: CheckInput, _findings: Sequence[Finding]) -> dict[str, int]:
    placed = columns(given.results)
    split = [records for records in placed.grouped.values() if len(_keys(records)) > 1]
    return {
        "columns": len(placed.grouped),
        "columns_with_more_than_one_key": len(split),
        "results_in_columns": sum(map(len, placed.grouped.values())),
        "results_in_split_columns": sum(map(len, split)),
        "results_with_no_anchor": placed.no_anchor,
        "results_with_no_column": placed.no_column,
    }


COLUMN_COHERENCE = Check(
    "C5",
    "column coherence",
    ("column-splits-comparability-key",),
    _column_coherence,
    _column_coherence_context,
)

# ---------------------------------------------------------------------------------------
# C9: source records that pin the same bytes from the same place.


def _status(record: JsonObject) -> str:
    """`record.status ?? "no status"`, as a template writes it."""
    status = record.get("status")
    return "no status" if status is None else text_of(status)


def _duplicate_artifact(given: CheckInput) -> list[Finding]:
    groups: dict[str, list[JsonObject]] = {}
    for record in given.sources:
        sha, url = text(record, "artifact_sha256"), text(record, "url")
        if not sha or not url:
            continue
        groups.setdefault(f"{sha} {url}", []).append(record)
    findings: list[Finding] = []
    for key, records in groups.items():
        if len(records) < 2:  # noqa: PLR2004 - one record is no duplicate
            continue
        statuses = sorted((f"{subject_of(record)} ({_status(record)})" for record in records), key=utf16_key)
        detail = f"{len(records)} source records pin these bytes at this url: {', '.join(statuses)}"
        findings.append(Finding("C9", "same-artifact-and-url", key, detail))
    return sorted(findings, key=lambda finding: utf16_key(finding.subject))


def _duplicate_artifact_context(given: CheckInput, _findings: Sequence[Finding]) -> dict[str, int]:
    by_sha: dict[str, int] = {}
    for record in given.sources:
        sha = text(record, "artifact_sha256")
        if sha:
            by_sha[sha] = by_sha.get(sha, 0) + 1
    return {
        "sources": len(given.sources),
        "sources_with_an_artifact_hash": sum(by_sha.values()),
        "artifact_hashes_held_by_more_than_one_source": sum(count > 1 for count in by_sha.values()),
    }


DUPLICATE_ARTIFACT = Check(
    "C9", "duplicate artifact", ("same-artifact-and-url",), _duplicate_artifact, _duplicate_artifact_context
)

# ---------------------------------------------------------------------------------------
# C13: a stored record that normalize_attributes would rewrite.


def _attributes(record: JsonObject) -> JsonObject:
    held = record.get("attributes")
    return held if isinstance(held, dict) else {}


def changed_keys(before: JsonObject, after: JsonObject) -> list[str]:
    """`changedKeys`: the keys whose value the normalisation would add, drop or change."""
    keys = sorted({*before, *after}, key=utf16_key)
    return [key for key in keys if _written(before.get(key, MISSING)) != _written(after.get(key, MISSING))]


def _written(value: Held) -> str | None:
    """`JSON.stringify(value)`, which is nothing at all for a value that is absent."""
    return None if isinstance(value, Missing) else js_written(value)


def _normalise_or_refuse(given: CheckInput) -> list[Finding]:
    rows = list(given.records)
    findings: list[Finding] = []
    for record, after in zip(rows, normalize_attributes(rows), strict=True):
        if after is record:
            continue
        keys = changed_keys(_attributes(record), _attributes(after))
        detail = f"normalizeAttributes would rewrite {', '.join(keys) if keys else 'the attribute order'}"
        findings.append(Finding("C13", "would-be-rewritten", subject_of(record), detail))
    return findings


def _normalise_or_refuse_context(given: CheckInput, _findings: Sequence[Finding]) -> dict[str, int]:
    kinds = distinct(record.get("kind", MISSING) for record in given.records)
    return {"records": len(given.records), "kinds": kinds}


NORMALISE_OR_REFUSE = Check(
    "C13", "normalise or refuse", ("would-be-rewritten",), _normalise_or_refuse, _normalise_or_refuse_context
)

#: The pack, in report order.
CHECKS: tuple[Check, ...] = (
    UNIT_FOR_METRIC,
    DIRECTION_DEFAULT,
    PRINTED_VS_NUMERIC,
    QUALIFIER_SHAPE,
    COLUMN_COHERENCE,
    DUPLICATE_ARTIFACT,
    NORMALISE_OR_REFUSE,
)

# ---------------------------------------------------------------------------------------
# Loading the store and running the pack.


@dataclass(frozen=True)
class SchemeConcept:
    """A concept of one scheme file, with the two predicates the checks read."""

    key: str
    direction: str | None
    scope_note: str | None


def read_scheme(path: Path, scheme: str, labels: BlankLabels | None = None) -> list[SchemeConcept]:
    """`readScheme`: the concepts of one scheme file, one for each statement placing a subject in it.

    The direction is the last step of the first default direction the subject is given, and
    the scope note the first it is given. Raises `VocabularyError` for a file that cannot
    be read or that n3 refuses.
    """
    iri = scheme_iri(scheme)
    statements = read_scheme_file(path, labels)
    direction: dict[str, str] = {}
    note: dict[str, str] = {}
    for statement in statements:
        if statement.predicate == _DEFAULT_DIRECTION:
            direction.setdefault(statement.subject, statement.object.rsplit("/", 1)[-1])
        elif statement.predicate == _SCOPE_NOTE:
            note.setdefault(statement.subject, statement.object)
    return [
        SchemeConcept(held.subject[len(iri) :], direction.get(held.subject), note.get(held.subject))
        for held in statements
        if held.predicate == _IN_SCHEME and held.object == iri
    ]


def gathered(
    records: Sequence[JsonObject], metrics: Mapping[str, MetricConcept], units: Mapping[str, UnitConcept]
) -> CheckInput:
    """The input of a run over these records, with the results and the sources picked out."""
    return CheckInput(
        [record for record in records if record.get("kind") == "result"],
        [record for record in records if record.get("kind") == "source"],
        records,
        metrics,
        units,
    )


def load_input(root: Path) -> CheckInput:
    """`loadInput(root)`: every stored record, and the metric and unit vocabularies.

    A metric pointing at the direction `unknown` is declaring that it has no default, so it
    is given None. Raises `StoreError` for a store that cannot be read and `VocabularyError`
    for a vocabulary that cannot.
    """
    records = [record for kind in KINDS for record in read_kind(kind, root)]
    # n3 numbers its blank nodes across the documents one process reads.
    labels = BlankLabels()
    metrics = {
        concept.key: MetricConcept(concept.key, None if concept.direction == "unknown" else concept.direction)
        for concept in read_scheme(root / VOCAB_DIR / "metric.ttl", "metric", labels)
    }
    units = {
        concept.key: UnitConcept(concept.key, concept.scope_note)
        for concept in read_scheme(root / VOCAB_DIR / "unit.ttl", "unit", labels)
    }
    return gathered(records, metrics, units)


def run_all(given: CheckInput, only: Sequence[Check] = CHECKS) -> RunResult:
    """`runAll(input, only)`: every finding of the checks, in their order, and each check's counts."""
    findings: list[Finding] = []
    summaries: list[CheckSummary] = []
    for check in only:
        raised = check.run(given)
        findings.extend(raised)
        context = _in_js_order(check.context(given, raised)) if check.context else {}
        summaries.append(
            CheckSummary(check.id, check.title, len(raised), count_by_rule(check, raised), context)
        )
    return RunResult(findings, summaries)
