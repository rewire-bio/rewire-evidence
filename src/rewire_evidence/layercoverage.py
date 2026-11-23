"""What the archive holds for each source, and what that amounts to in characters.

This is `shared/omics/layers/coverage.ts`. Every source falls in exactly one bucket, and the
buckets sum to the source count, which the result checks and reports. A source with several
layers is counted by its best one. A layer shared by two sources is one layer, so characters
are counted once per source for the per-source column and once per distinct layer for the
total.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, cast

from rewire_evidence.canonical import JsonObject, JsonValue, utf16_key
from rewire_evidence.layerreaders import by_usefulness, is_grid
from rewire_evidence.sanity import usable

#: Where a source stands, in the order a table prints the buckets.
COVERAGE_BUCKETS: Final = (
    "layer_pass",
    "layer_suspect",
    "layer_fail",
    "no_normaliser",
    "not_the_artifact",
    "no_capture",
)
_BUCKET_OF: Final = {"pass": "layer_pass", "suspect": "layer_suspect", "fail": "layer_fail"}


@dataclass(frozen=True)
class SourceCoverage:
    """Where one source stands."""

    source_id: str
    bucket: str
    #: The object the newest receipt points at, or `None` when nothing is held.
    object_sha256: str | None
    #: The layer chosen for the source, or `None`.
    layer_sha256: str | None
    sanity: str | None
    #: Characters in the chosen layer. Zero when it is unusable or there is none.
    characters: int
    source_class: str
    #: Why the source is in this bucket, in one phrase.
    detail: str


@dataclass
class ClassTally:
    """The sources of one class, how many have a usable layer, and their characters."""

    sources: int = 0
    usable: int = 0
    characters: int = 0


@dataclass
class CoverageResult:
    """One row for each source, the totals, and anything that does not add up."""

    rows: list[SourceCoverage]
    sources: int
    buckets: dict[str, int]
    #: Sources that no fetch receipt names at all.
    no_receipt: int
    #: Characters summed over sources, a shared layer counted once for each.
    characters: int
    #: Characters summed over distinct usable layers, and how many of those there are.
    distinct_characters: int
    distinct_layers: int
    by_class: dict[str, ClassTally] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _row(
    source_id: str,
    receipt: Mapping[str, JsonValue] | None,
    by_object: Mapping[str, list[JsonObject]],
    reasons: Mapping[str, list[str]],
) -> SourceCoverage:
    if receipt is None:
        detail = "no fetch receipt names this source"
        return SourceCoverage(source_id, "no_capture", None, None, None, 0, "unknown", detail)
    source_class = cast("str", receipt["source_class"])
    reason = receipt.get("reason")
    said = "no reason recorded" if reason is None else cast("str", reason)
    held = cast("str | None", receipt.get("sha256"))
    if receipt["outcome"] == "failed":
        detail = f"the fetch failed: {said}"
        return SourceCoverage(source_id, "no_capture", None, None, None, 0, source_class, detail)
    if receipt["outcome"] == "not_artifact":
        detail = f"bytes are held and are not the source: {said}"
        return SourceCoverage(source_id, "not_the_artifact", held, None, None, 0, source_class, detail)
    layers = by_object.get(held, []) if held else []
    if not layers:
        why = sorted(reasons.get(held, []) if held else [], key=utf16_key)
        detail = "; ".join(why) if why else "the held bytes produced no text layer"
        return SourceCoverage(source_id, "no_normaliser", held, None, None, 0, source_class, detail)
    best = min(layers, key=lambda record: (*by_usefulness(record), record["layer_sha256"]))
    sanity = cast("str", best["sanity"])
    characters = cast("Mapping[str, int]", best["metrics"])["characters"]
    if sanity == "pass":
        detail = f"{best['normaliser']}, {characters} characters"
    else:
        detail = "; ".join(cast("list[str]", best["sanity_reasons"]))
    return SourceCoverage(
        source_id,
        _BUCKET_OF[sanity],
        held,
        cast("str", best["layer_sha256"]),
        sanity,
        characters if usable(sanity) else 0,
        source_class,
        detail,
    )


def coverage_of(
    source_ids: Sequence[str],
    receipts: Mapping[str, Mapping[str, JsonValue]],
    registry: Sequence[JsonObject],
    unsupported: Sequence[Mapping[str, JsonValue]] = (),
) -> CoverageResult:
    """Put every source in one bucket.

    `receipts` is the newest receipt for each source. A source with none is in `no_capture`
    and is counted, because the denominator is the store's source count.
    """
    by_object: dict[str, list[JsonObject]] = {}
    for record in registry:
        if not is_grid(record):
            by_object.setdefault(cast("str", record["object_sha256"]), []).append(record)
    reasons: dict[str, list[str]] = {}
    for entry in unsupported:
        held = reasons.setdefault(cast("str", entry["object_sha256"]), [])
        if entry["reason"] not in held:
            held.append(cast("str", entry["reason"]))

    rows = [
        _row(source_id, receipts.get(source_id), by_object, reasons)
        for source_id in sorted(source_ids, key=utf16_key)
    ]
    buckets = dict.fromkeys(COVERAGE_BUCKETS, 0)
    by_class: dict[str, ClassTally] = {}
    distinct: dict[str, int] = {}
    for row in rows:
        buckets[row.bucket] += 1
        if row.layer_sha256 and row.characters > 0:
            distinct[row.layer_sha256] = row.characters
        tally = by_class.setdefault(row.source_class, ClassTally())
        tally.sources += 1
        tally.characters += row.characters
        tally.usable += row.bucket in {"layer_pass", "layer_suspect"}

    problems: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row.source_id in seen:
            problems.append(f"{row.source_id} is in the table twice")
        seen.add(row.source_id)
    return CoverageResult(
        rows,
        len(source_ids),
        buckets,
        sum(1 for source_id in source_ids if source_id not in receipts),
        sum(row.characters for row in rows),
        sum(distinct.values()),
        len(distinct),
        by_class,
        problems,
    )
