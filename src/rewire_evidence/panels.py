"""Comparison panels, resolved as the catalogue query resolves them.

A record's `attributes.comparison_panels` are curated figures: each names a protocol, a
dataset, a metric and the results one source table prints for them. `validateRecords` hands
every record to `createCatalogueQuery` and asks it for each record that has panels, so a
panel naming a stale ID, mixing protocols or showing an unreviewed result fails the store.

This is the part of `shared/omics/catalogue-query.ts` and `published-comparisons.ts` that
decides whether that call throws, and what it shows. Four things about it are easy to miss:

- The query reads every record through `normalizeRecords` and `normalizeAttributes` first,
  so an older shape is resolved as its current one, and a record those refuse fails here.
- An excluded record is not in the query at all: its own panels are never looked at, and a
  panel naming it finds nothing.
- An assessment record shows the panels of the records below it as well as its own. Below
  means a path of `part_of` links, or one `evaluates_task` link from where an evaluation
  ran, each backed by a checked claim. The same panel ID arriving twice must be the same
  panel, or the query refuses.
- The records below are resolved before the record's own panels, in ID order, so that is
  the order faults are found in.

It is the last step of `validateRecords` and relies on the steps before it: every link is one
its relation allows, and every result's numeric value is null or a finite number as text.
`resolve_panels` on records that have not passed those is not what the query would do.

The query sorts IDs with `localeCompare`. For IDs of lowercase letters, digits and hyphens
that is the order of their UTF-16 units, which a recording from Node holds this to.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from rewire_evidence.attributes import normalize_attributes, said
from rewire_evidence.blocks import pinned
from rewire_evidence.canonical import JsonObject, JsonValue, canonical_json, js_stringify, utf16_key
from rewire_evidence.concerns import concern_targets, evidence_path, source_wide_concern
from rewire_evidence.js import MISSING, Held, strictly_equal, trim, truthy
from rewire_evidence.schema import (
    BENCHMARK_SUBJECT_KINDS,
    DATASET_SUBJECT_KINDS,
    Link,
    Record,
    RecordError,
    normalize_records,
)

_CHECKED = ("source_checked", "reproduced")
_INACTIVE = ("superseded", "disputed", "excluded")
_TEXT_MEMBERS = ("id", "title", "protocol_id", "dataset_id", "metric", "unit", "source_locator", "context")
_LIST_MEMBERS = ("result_ids", "source_ids", "caveats")
_COMPARED = ("split", "subset", "population", "aggregation")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MINIMUM_RESULTS = 2


@dataclass(frozen=True)
class Resolved:
    """One panel the query shows: the panel as written, and the results it shows, in order."""

    panel: JsonObject

    @property
    def id(self) -> str:
        """The panel's ID."""
        return cast("str", self.panel["id"])

    @property
    def result_ids(self) -> list[str]:
        """The results the panel shows."""
        return cast("list[str]", self.panel["result_ids"])


def _record(row: JsonObject) -> Record:
    """A row the query reads as a record, without asking the schema: it may be an older shape."""
    links = [
        Link(cast("str", link["relation"]), cast("str", link["target_id"]))
        for link in cast("list[JsonObject]", row["links"])
    ]
    return Record(
        cast("str", row["id"]),
        cast("str", row["kind"]),
        cast("str", row["name"]),
        cast("str", row["description"]),
        cast("str", row["status"]),
        cast("dict[str, list[str]]", row["facets"]),
        cast("list[str]", row["source_ids"]),
        links,
        cast("JsonObject", row["attributes"]),
    )


def current_records(records: Sequence[Record]) -> list[Record]:
    """The records in their current shapes: `currentRecords`. An unchanged record is itself."""
    rows = [record.row() for record in records]
    current = normalize_attributes(normalize_records(rows))
    return [
        record if now is row else _record(now)
        for record, row, now in zip(records, rows, current, strict=True)
    ]


def _profile_sources(value: Held, found: list[str]) -> list[str]:
    """Every source ID cited anywhere inside a profile or a run guide."""
    children = (
        value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    )
    for key, child in children:
        if key in {"source_ids", "summary_source_ids"} and isinstance(child, list):
            found.extend(each for each in child if isinstance(each, str))
        else:
            _profile_sources(child, found)
    return found


def panels_of(record: Record) -> list[JsonObject]:  # noqa: C901 - one refusal for many faults, each checked
    """A record's panels, each checked for its shape: `comparisonPanels`.

    Raises `RecordError` for panels on a record that is no assessment record, or that are
    not a list of well-formed panels with distinct IDs.
    """
    held = record.attributes.get("comparison_panels", MISSING)
    if held is MISSING:
        return []
    refusal = RecordError(f"Invalid published comparison on {record.id}")
    if record.kind not in BENCHMARK_SUBJECT_KINDS or not isinstance(held, list):
        raise refusal
    seen: set[str] = set()
    panels: list[JsonObject] = []
    for raw in held:
        if not isinstance(raw, dict):
            # A list passes the TypeScript's first test and fails its next: it has no members.
            raise refusal
        if any(not isinstance(raw.get(key), str) or not trim(cast("str", raw[key])) for key in _TEXT_MEMBERS):
            raise refusal
        panel_id = cast("str", raw["id"])
        direction = raw.get("direction")
        if panel_id in seen or not isinstance(direction, str) or direction not in {"higher", "lower"}:
            raise refusal
        seen.add(panel_id)
        for key in _LIST_MEMBERS:
            listed = raw.get(key)
            if not isinstance(listed, list) or not listed:
                raise refusal
            if any(not isinstance(each, str) or not trim(each) for each in listed):
                raise refusal
        results = cast("list[str]", raw["result_ids"])
        if len(set(results)) != len(results) or len(results) < _MINIMUM_RESULTS:
            raise refusal
        review = raw.get("review")
        method = review.get("method") if isinstance(review, dict) else None
        if method != "automated_source_review" or not isinstance(review, dict):
            raise refusal
        if not _DATE.fullmatch(said(review.get("date", MISSING))):
            raise refusal
        panels.append(raw)
    return panels


class Query:
    """As much of `createCatalogueQuery` as resolving panels needs."""

    def __init__(self, records: Sequence[Record]) -> None:  # noqa: C901 - the two indexes are built in one pass each
        """Read the records as the query does: current shapes, none excluded, in ID order."""
        held = [record for record in current_records(records) if record.status != "excluded"]
        held.sort(key=lambda record: utf16_key(record.id))
        self.by_id = {record.id: record for record in held}
        # A checked claim with sources and a locator backs the link its `field` names, on
        # each of its subjects. One reading takes the field as text, the other as it is.
        self._claimed_text: set[tuple[str, str]] = set()
        self._claimed_exact: set[tuple[str, str]] = set()
        for record in held:
            if record.kind != "claim" or record.status not in _CHECKED or not record.source_ids:
                continue
            if not truthy(record.attributes.get("source_locator", MISSING)):
                continue
            field = record.attributes.get("field", MISSING)
            for link in record.links:
                if link.relation == "subject":
                    self._claimed_text.add((link.target_id, said(field)))
                    if isinstance(field, str):
                        self._claimed_exact.add((link.target_id, field))
        self._concerned = concern_targets(held)
        self._ancestors: dict[str, set[str]] = {}
        # Records that show their panels on the records above them, in ID order.
        self._below: dict[str, list[Record]] = {}
        for record in held:
            if record.kind not in BENCHMARK_SUBJECT_KINDS or "comparison_panels" not in record.attributes:
                continue
            for above in self.ancestors(record.id):
                if above != record.id:
                    self._below.setdefault(above, []).append(record)
        self._resolved: dict[str, list[Resolved]] = {}

    def _verified(self, record: Record, link: Link) -> bool:
        """Whether a link upwards is backed by a checked claim: `verifiedAssociation`.

        The query also follows identity links between models, to roll results up. No
        assessment record may hold one, so they never lead from a panel to its owner and
        are not read here.
        """
        target = self.by_id.get(link.target_id)
        return (
            link.relation in {"part_of", "evaluates_task"}
            and target is not None
            and (record.kind in BENCHMARK_SUBJECT_KINDS or record.kind == "evaluation")
            and target.kind in BENCHMARK_SUBJECT_KINDS
            and (record.id, f"links:{link.relation}:{link.target_id}") in self._claimed_text
        )

    def ancestors(self, record_id: str) -> set[str]:
        """A record and everything its results roll up to: `ancestors`.

        `evaluates_task` is followed only from the record itself, never after another link.
        """
        found = self._ancestors.get(record_id)
        if found is not None:
            return found
        found = {record_id}
        pending = [(record_id, 0)] if record_id in self.by_id else []
        while pending:
            current_id, depth = pending.pop()
            current = self.by_id[current_id]
            for link in current.links:
                if link.target_id in found or (link.relation == "evaluates_task" and depth > 0):
                    continue
                if self._verified(current, link):
                    found.add(link.target_id)
                    pending.append((link.target_id, depth + 1))
        self._ancestors[record_id] = found
        return found

    def _linked(self, record: Record | None, relation: str) -> list[Record]:
        targets = [link.target_id for link in record.links if link.relation == relation] if record else []
        return [self.by_id[target] for target in targets if target in self.by_id]

    def _sources(self, *items: Record) -> list[Record]:
        cited: list[str] = []
        for item in items:
            cited.extend(item.source_ids)
            _profile_sources(item.attributes.get("profile", MISSING), cited)
            _profile_sources(item.attributes.get("run_guide", MISSING), cited)
        found = [self.by_id.get(source_id) for source_id in dict.fromkeys(cited)]
        return [source for source in found if source is not None and source.kind == "source"]

    def _reaches(self, protocol: Record, owner: Record) -> bool:
        """Whether checked `part_of` and `evaluates_task` links lead from a protocol to the owner."""
        found = {protocol.id}
        pending = [protocol]
        while pending:
            current = pending.pop()
            for link in current.links:
                if link.relation not in {"part_of", "evaluates_task"} or link.target_id in found:
                    continue
                target = self.by_id.get(link.target_id)
                backed = (current.id, f"links:{link.relation}:{link.target_id}") in self._claimed_exact
                if backed and target is not None and target.kind in BENCHMARK_SUBJECT_KINDS:
                    found.add(target.id)
                    pending.append(target)
        return owner.id in found

    def _row(
        self, panel: JsonObject, result_id: str, protocol: Record, dataset: Record, sources: Sequence[Record]
    ) -> tuple[Record, Record, list[Record]]:
        """One result of a panel, with its evaluation and the systems tested, or the refusal."""

        def fail(reason: str) -> RecordError:
            return RecordError(f"Comparison {said(panel['id'])}: {reason}")

        result = self.by_id.get(result_id)
        if result is None or result.kind != "result" or result.status not in _CHECKED:
            raise fail("unchecked, superseded or missing result")
        evaluations = self._linked(result, "evaluation")
        evaluation = evaluations[0] if evaluations else None
        if evaluation is None or evaluation.status in _INACTIVE:
            raise fail("inactive or missing evaluation")
        assessments = self._linked(evaluation, "assessment")
        datasets = self._linked(evaluation, "data")
        cited = [*self._sources(result, evaluation), *sources]
        path = evidence_path(result, evaluation, assessments)
        if any(source_wide_concern(source) for source in cited) or any(
            each in self._concerned for each in path
        ):
            raise fail("unresolved source concerns")
        # The query refuses a numeric value that is not one here. `validateRecords` has
        # already refused it for the result itself, so no record reaches this with one.
        attributes = result.attributes
        qualifier = attributes.get("metric_qualifier")
        wanted = panel.get("metric_qualifier")
        if (
            not strictly_equal(attributes.get("metric", MISSING), panel["metric"])
            or not strictly_equal("" if qualifier is None else qualifier, "" if wanted is None else wanted)
            or not strictly_equal(attributes.get("unit", MISSING), panel["unit"])
            or not strictly_equal(attributes.get("metric_direction", MISSING), panel["direction"])
        ):
            raise fail("mixed metric, unit or direction")
        if protocol.id not in {each.id for each in assessments} or dataset.id not in {
            each.id for each in datasets
        }:
            raise fail("mixed protocol or dataset")
        shared = any(source_id in result.source_ids for source_id in cast("list[str]", panel["source_ids"]))
        if not shared or not truthy(attributes.get("source_locator", MISSING)):
            raise fail("result lacks table provenance")
        return result, evaluation, self._linked(evaluation, "system")

    def _resolve(self, owner: Record, panel: JsonObject) -> Resolved:  # noqa: C901 - as `_row`
        def fail(reason: str) -> RecordError:
            return RecordError(f"Comparison {said(panel['id'])}: {reason}")

        protocol = self.by_id.get(cast("str", panel["protocol_id"]))
        dataset = self.by_id.get(cast("str", panel["dataset_id"]))
        if (
            protocol is None
            or protocol.kind not in BENCHMARK_SUBJECT_KINDS
            or dataset is None
            or dataset.kind not in DATASET_SUBJECT_KINDS
        ):
            raise fail("missing protocol or dataset")
        if protocol.status in _INACTIVE or dataset.status in _INACTIVE:
            raise fail("inactive protocol or dataset")
        # Suite and task charts must have a reviewed path to their exact protocol.
        if not self._reaches(protocol, owner):
            raise fail("unverified protocol association")
        sources: list[Record] = []
        for source_id in cast("list[str]", panel["source_ids"]):
            source = self.by_id.get(source_id)
            if source is None or source.kind != "source":
                raise fail("missing source")
            if not pinned(source):
                raise fail("source artifact is not pinned")
            sources.append(source)
        rows = [
            self._row(panel, result_id, protocol, dataset, sources)
            for result_id in cast("list[str]", panel["result_ids"])
        ]
        # One published table can quote earlier experiments beside new ones. A shared table
        # never makes them independent.
        tested = {
            system.id
            for result, _, systems in rows
            if result.attributes["numeric_value"] is not None
            for system in systems
        }
        if len(tested) < _MINIMUM_RESULTS:
            raise fail("fewer than two tested configurations")
        for field in _COMPARED:
            if len({self._compared(evaluation, field) for _, evaluation, _ in rows}) != 1:
                raise fail(f"mixed {field}")
        return Resolved(panel)

    @staticmethod
    def _compared(evaluation: Record, field: str) -> str:
        comparison = evaluation.attributes.get("comparison")
        value: JsonValue = comparison.get(field) if isinstance(comparison, dict) else None
        return js_stringify(value)

    def resolve(self, owner: Record) -> list[Resolved]:
        """A record's own panels, resolved: `resolveComparisons`."""
        found = self._resolved.get(owner.id)
        if found is None:
            found = [self._resolve(owner, panel) for panel in panels_of(owner)]
            self._resolved[owner.id] = found
        return found

    def comparisons(self, record_id: str) -> list[Resolved] | None:
        """What `get` shows for a record: its panels and those of the records below it.

        None for a record the query does not hold. Raises `RecordError` for a panel that
        cannot be shown, or two panels of one ID that differ.
        """
        record = self.by_id.get(record_id)
        if record is None:
            return None
        below: list[Resolved] = []
        if record.kind in BENCHMARK_SUBJECT_KINDS:
            for child in self._below.get(record_id, []):
                below.extend(self.resolve(child))
        unique: dict[str, Resolved] = {}
        for panel in (*self.resolve(record), *below):
            existing = unique.get(panel.id)
            if (
                existing is not None
                and existing is not panel
                and canonical_json(existing.panel) != canonical_json(panel.panel)
            ):
                raise RecordError(f"Conflicting inherited comparison: {panel.id}")
            unique[panel.id] = panel
        return list(unique.values())


def resolve_panels(records: Sequence[Record]) -> Mapping[str, list[Resolved] | None]:
    """Every record with panels, and what the query shows for it: the last step of `validateRecords`.

    Nothing is read unless some record has panels. Raises `RecordError` for the first panel
    that cannot be shown, taking the records in the order given.
    """
    owners = [record for record in records if truthy(record.attributes.get("comparison_panels", MISSING))]
    if not owners:
        return {}
    query = Query(records)
    return {owner.id: query.comparisons(owner.id) for owner in owners}
