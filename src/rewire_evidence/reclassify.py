"""`npm run archive -- reclassify`: every manifest row's licence read again.

This is `scripts/archive/reclassify.ts`. A manifest row carries the tier the classifier gave
it when the bytes landed. This reads every row against the licence map as it stands and the
candidates on disk, and writes back the three columns a licence decides and no others:
`archive_tier`, `visibility` and `observed_licence`. Nothing is fetched and no bytes are read.

Three rules:

1. A row moves only to a tier that evidence justifies. A row whose sources give no evidence
   keeps the tier it has: absence is not a determination.
2. A licence recorded on a source record beats a candidate, mapped or not.
3. A row that would move from public to restricted is a takedown, not a reclassification.
   Every such row is reported, and a run that finds one writes nothing at all.

A row at `t5-no-store` is passed over before any of that, so no reading lifts a row out of it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from rewire_evidence.archive import (
    ARCHIVE_TIERS,
    NO_STORE,
    is_removed,
    read_manifest,
    update_row,
    visibility_of,
)
from rewire_evidence.canonical import JsonObject, JsonValue, utf16_key
from rewire_evidence.licence import (
    LicenceMap,
    Licences,
    build_candidate,
    determine_for_source,
    read_candidates,
    read_licence_map,
    read_source_licences,
    stricter_tier,
    strictest,
)

type Candidates = Mapping[str, JsonObject]


def read_source(source_id: str, held: LicenceMap, licences: Licences, candidates: Candidates) -> JsonObject:
    """The tier one source record justifies today, and what kind of evidence it rests on.

    The recorded licence is asked first and its answer stands whether the map covers the
    string or not. A candidate is read again against the map passed in, never against the
    tier stored in the candidates file, which was written against the map of another day.
    """
    recorded = determine_for_source(source_id, held, licences)
    reading = {
        "source_id": source_id,
        "archive_tier": recorded["archive_tier"],
        "observed_licence": recorded["observed_licence"],
    }
    if recorded["reason"] in {"licence-map", "unmapped-licence"}:
        return {**reading, "evidence": "recorded-licence"}
    candidate = candidates.get(source_id)
    if candidate is not None:
        target = {"source_id": source_id, "recorded_licence": None, "recorded_tier": recorded["archive_tier"]}
        reread = build_candidate(target, cast("list[JsonObject]", candidate["observations"]), held)
        if reread is not None and reread["status"] == "resolved" and reread["archive_tier"]:
            return {
                "source_id": source_id,
                "archive_tier": reread["archive_tier"],
                "observed_licence": reread["observed_licence"],
                "evidence": "candidate",
            }
    return {**reading, "evidence": "none"}


def read_row(
    source_ids: Sequence[str], held: LicenceMap, licences: Licences, candidates: Candidates
) -> JsonObject:
    """The tier one object justifies: the most restrictive of its sources."""
    sources = [read_source(source_id, held, licences, candidates) for source_id in source_ids]
    tier, observed = strictest(sources)
    return {
        "archive_tier": tier,
        "observed_licence": observed,
        "evidenced": any(source["evidence"] != "none" for source in sources),
        "sources": cast("JsonValue", sources),
    }


def plan_reclassify(root: Path) -> JsonObject:
    """Read the whole manifest and work out what each row should say. Writes nothing."""
    manifest = read_manifest(root)
    held = read_licence_map(root)
    licences = read_source_licences(root)
    candidates = read_candidates(root)
    before = dict.fromkeys(ARCHIVE_TIERS, 0)
    after = dict.fromkeys(ARCHIVE_TIERS, 0)
    pairs: dict[str, int] = {}
    moves: list[JsonObject] = []
    restrictive: list[JsonObject] = []
    tightening: list[JsonObject] = []
    skipped = unevidenced = publishable_before = publishable_after = 0

    for key in sorted(manifest, key=utf16_key):
        row = manifest[key]
        was = cast("str", row["archive_tier"])
        before[was] += 1
        publishable_before += row["visibility"] == "public"
        tier, observed = was, row["observed_licence"]
        evidence: JsonValue = []
        # A removed row is a recorded decision, not a licence reading.
        if was == NO_STORE or is_removed(row):
            skipped += 1
        else:
            reading = read_row(cast("list[str]", row["source_ids"]), held, licences, candidates)
            evidence = [source["evidence"] for source in cast("list[JsonObject]", reading["sources"])]
            if reading["evidenced"]:
                tier, observed = cast("str", reading["archive_tier"]), reading["observed_licence"]
            else:
                unevidenced += 1
        after[tier] += 1
        publishable_after += visibility_of(tier) == "public"
        if tier == was:
            continue
        move: JsonObject = {
            "sha256": key,
            "from": was,
            "to": tier,
            "from_visibility": row["visibility"],
            "to_visibility": visibility_of(tier),
            "observed_licence": observed,
            "previous_observed_licence": row["observed_licence"],
            "store": row["store"],
            "source_ids": row["source_ids"],
            "evidence": evidence,
        }
        moves.append(move)
        pair = f"{was} to {tier}"
        pairs[pair] = pairs.get(pair, 0) + 1
        if move["from_visibility"] == "public" and move["to_visibility"] == "restricted":
            restrictive.append(move)
        elif stricter_tier(was, tier) == tier and move["to_visibility"] == "public":
            tightening.append(move)

    # `Object.entries(...).sort()` orders each pair by the text `name,count`.
    by_tier = dict(sorted(pairs.items(), key=lambda pair: utf16_key(f"{pair[0]},{pair[1]}")))
    return {
        "manifest_rows": len(manifest),
        "skipped_removed": skipped,
        "unevidenced": unevidenced,
        "before": cast("JsonValue", before),
        "after": cast("JsonValue", after),
        "moves_by_tier": cast("JsonValue", by_tier),
        "moves": cast("JsonValue", moves),
        "restrictive": cast("JsonValue", restrictive),
        "tightening": cast("JsonValue", tightening),
        "publishable_before": publishable_before,
        "publishable_after": publishable_after,
    }


def reclassify_objects(
    root: Path, *, dry_run: bool = False, log: Callable[[str], None] = lambda _: None
) -> JsonObject:
    """Apply the plan, or report it.

    A run with any move from public to restricted writes nothing at all: if part of a
    reading is a takedown, the whole reading wants a person before any of it lands.
    """
    plan = plan_reclassify(root)
    summary: JsonObject = {**plan, "dry_run": dry_run, "written": 0, "refused": False}
    restrictive = cast("list[JsonObject]", plan["restrictive"])
    if dry_run:
        return summary
    if restrictive:
        summary["refused"] = True
        log(
            f"refusing to write: {len(restrictive)} row(s) would move from public to restricted, "
            "which is a takedown"
        )
        return summary
    written = 0
    for move in cast("list[JsonObject]", plan["moves"]):
        patch = {"archive_tier": move["to"], "observed_licence": move["observed_licence"]}
        written += update_row(cast("str", move["sha256"]), patch, root)
        log(f"{move['sha256']} {move['from']} to {move['to']}")
    summary["written"] = written
    return summary
