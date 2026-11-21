"""The licence map, the tier it gives an object, the candidates and the triage queue.

This is `scripts/licence/map.ts`, `triage.ts` and `counts.ts`, and the parts of
`scripts/licence/resolve.ts` that read and build a candidate without asking a service:
`candidateSchema`, `readCandidates` and `buildCandidate`.

Tiers govern publication and nothing else. A licence never decides whether a source is
fetched. `data/archive/licence-map.tsv` holds one hand-reviewed row per licence string, and a
string with no row leaves its source at the private default with the string recorded beside
it: an unresolved licence never means public and never means unstored.

The map is read as it is written, with no trimming, and so are the licence strings of the
source records. The triage queue trims a record's string before it looks it up, as the
TypeScript does, so a string with a space about it has a row for the classifier and none for
the queue.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, cast

from rewire_evidence import archive, zod
from rewire_evidence.archive import ARCHIVE_TIERS, DEFAULT_ARCHIVE_TIER, ArchiveError, visibility_of
from rewire_evidence.canonical import JsonObject, JsonValue, utf16_key
from rewire_evidence.js import member, trim
from rewire_evidence.weburl import host_of
from rewire_evidence.worklist import attributes_of, source_class_of, source_records, text_of

LICENCE_MAP: Final = "data/archive/licence-map.tsv"
CANDIDATES: Final = "data/archive/licence-candidates.jsonl"
COLUMNS: Final = ("licence", "family", "tier", "basis", "note")
RESOLVE_ROUTES: Final = ("crossref", "github", "huggingface", "preprint", "europepmc")
RESOLVE_REASONS: Final = (
    "not_applicable",
    "no_licence_in_metadata",
    "not_asserted",
    "ambiguous_version",
    "not_found",
    "http_error",
    "network_error",
    "host_stopped",
)
CANDIDATE_STATUSES: Final = ("resolved", "conflict", "unmapped")
AGREEMENTS: Final = ("none-recorded", "agrees", "disagrees", "recorded-unmapped", "no-candidate")

#: The seven originals committed to the public repository before the policy existed.
PRE_POLICY_SOURCE_IDS: Final = (
    "gpn-paper-20261116-source-biorxiv-v3",
    "gpn-paper-20261116-source-pnas-2023",
    "gpn-paper-20261116-source-pnas-2023-si-appendix",
    "phylogpn-2025",
    "phylogpn-20261116-source-supplement",
    "protein-stability-20261009-source-dieckhaus2024",
    "protein-stability-20261009-source-pancotti2022",
)

#: How restrictive a tier is, lowest first. A judgement, not a lattice: no-derivatives sits
#: below non-commercial, because a text layer counts as a format conversion while the
#: non-commercial reading rests on a premise that can change.
_RESTRICTIVENESS: Final = {
    "t0-public": 0,
    "t2-verbatim-nd": 1,
    "t1-public-nc": 2,
    "t3-verbatim-nd-nc": 3,
    "t4-private": 4,
    "t5-no-store": 5,
}

type LicenceMap = dict[str, JsonObject]
type Licences = dict[str, str | None]


def stricter_tier(a: str, b: str) -> str:
    """The more restrictive of two tiers, and the first of two that are equally so."""
    return a if _RESTRICTIVENESS[a] >= _RESTRICTIVENESS[b] else b


def read_licence_map(root: Path) -> LicenceMap:
    """The map: comment lines start with `#`, then one header line, then a row per string.

    Every field is literal: no quoting, no escapes, no trimming.
    """
    path = root / LICENCE_MAP
    rows: LicenceMap = {}
    header = False
    for number, line in enumerate(archive.raw_lines(path), 1):
        where = f"{path} line {number}"
        if archive.blank(line) or line.startswith("#"):
            continue
        fields = line.split("\t")
        if not header:
            if fields != list(COLUMNS):
                raise ArchiveError(f"{where}: expected the header {', '.join(COLUMNS)}")
            header = True
            continue
        if len(fields) != len(COLUMNS):
            raise ArchiveError(f"{where}: {len(fields)} columns, expected {len(COLUMNS)}")
        licence, family, tier, basis, note = fields
        if not licence:
            raise ArchiveError(f"{where}: the licence column is empty")
        if tier not in ARCHIVE_TIERS:
            raise ArchiveError(f'{where}: "{tier}" is not an archive tier')
        if licence in rows:
            raise ArchiveError(f'{where}: a second row for the licence string "{licence}"')
        rows[licence] = {
            "licence": licence,
            "family": family,
            "archive_tier": tier,
            "basis": basis,
            "note": note,
        }
    if not header:
        raise ArchiveError(f"{path}: no header row")
    return rows


def read_source_licences(root: Path) -> Licences:
    """Every source record's ID and the licence string it carries, if it carries one."""
    licences: Licences = {}
    for record in source_records(root):
        licence = attributes_of(record).get("licence")
        licences[cast("str", record["id"])] = licence if isinstance(licence, str) and trim(licence) else None
    return licences


def determine_for_source(source_id: str, held: LicenceMap, licences: Licences) -> JsonObject:
    """The tier for one source record, and why. A string with no row is private, and named."""
    exposure = "pre-policy" if source_id in PRE_POLICY_SOURCE_IDS else "policy"
    licence = licences.get(source_id)
    row = None if licence is None else held.get(licence)
    if row is not None:
        tier, reason = cast("str", row["archive_tier"]), "licence-map"
    elif licence is not None:
        tier, reason = DEFAULT_ARCHIVE_TIER, "unmapped-licence"
    else:
        tier = DEFAULT_ARCHIVE_TIER
        reason = "no-licence-recorded" if source_id in licences else "no-source-record"
    return {"archive_tier": tier, "observed_licence": licence, "exposure": exposure, "reason": reason}


def strictest(readings: Sequence[Mapping[str, JsonValue]]) -> tuple[str, JsonValue]:
    """The most restrictive tier of several readings and the string it was read from.

    The first of two equally restrictive readings is kept, and no reading at all is the
    private default with no string.
    """
    tier: str | None = None
    observed: JsonValue = None
    for reading in readings:
        read = cast("str", reading["archive_tier"])
        if tier is None or stricter_tier(tier, read) != tier:
            tier, observed = read, reading["observed_licence"]
    return tier or DEFAULT_ARCHIVE_TIER, observed


def licence_classifier(root: Path) -> Callable[[Sequence[str]], JsonObject]:
    """The classifier the fetcher asks: the tier of an object, from the sources it answers for.

    An object takes the most restrictive tier of its sources, and carries pre-policy exposure
    if any of them is one of the seven.
    """
    held = read_licence_map(root)
    licences = read_source_licences(root)

    def classify(source_ids: Sequence[str]) -> JsonObject:
        readings = [determine_for_source(source_id, held, licences) for source_id in source_ids]
        tier, observed = strictest(readings)
        early = any(reading["exposure"] == "pre-policy" for reading in readings)
        return {
            "archive_tier": tier,
            "observed_licence": observed,
            "exposure": "pre-policy" if early else "policy",
        }

    return classify


# ---- candidates -----------------------------------------------------------------------

_SECONDS_SOURCE: Final = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
_SECONDS: Final = zod.text(
    zod.pattern(re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"), _SECONDS_SOURCE)
)
_TEXT: Final = zod.NONEMPTY

#: One licence statement from one service, as served.
OBSERVATION: Final = zod.strict(
    {
        "route": zod.one_of(*RESOLVE_ROUTES),
        "endpoint": _TEXT,
        "field": _TEXT,
        "value": _TEXT,
        "observed_at": _SECONDS,
    }
)

#: A suggestion sitting beside a source record: what services stated, and what the map makes of it.
CANDIDATE: Final = zod.strict(
    {
        "schema_version": zod.one_of(1),
        "source_id": _TEXT,
        "status": zod.one_of(*CANDIDATE_STATUSES),
        "observed_licence": zod.nullable(zod.text()),
        "family": zod.nullable(zod.text()),
        "archive_tier": zod.nullable(zod.one_of(*ARCHIVE_TIERS)),
        "recorded_licence": zod.nullable(zod.text()),
        "recorded_tier": zod.one_of(*ARCHIVE_TIERS),
        "agreement": zod.one_of(*AGREEMENTS),
        "unmapped_values": zod.array(_TEXT),
        "reason": zod.nullable(zod.text()),
        "observations": zod.array(OBSERVATION, zod.min_length(1)),
    }
)

#: One attempt at one route for one source, kept whether it produced a licence or not.
RESOLVE_RECEIPT: Final = zod.strict(
    {
        "schema_version": zod.one_of(1),
        "source_id": _TEXT,
        "route": zod.one_of(*RESOLVE_ROUTES),
        "endpoint": zod.nullable(zod.text()),
        "attempted_at": _SECONDS,
        "http_status": zod.nullable(zod.number(zod.INT)),
        "outcome": zod.one_of("licence", "no_licence"),
        "reason": zod.nullable(zod.one_of(*RESOLVE_REASONS)),
        "values": zod.array(_TEXT),
    }
)

_URL: Final = re.compile(r"https?://", re.IGNORECASE | re.ASCII)


def read_candidates(root: Path) -> dict[str, JsonObject]:
    """The candidates on disk, keyed on source. A missing file is no candidates."""
    path = root / CANDIDATES
    if not path.exists():
        return {}
    rows = archive.parsed_lines(path, CANDIDATE, str(path), skipping=False)
    return {cast("str", row["source_id"]): row for row in rows}


def _route(observation: Mapping[str, JsonValue]) -> int:
    route = observation["route"]
    return RESOLVE_ROUTES.index(route) if route in RESOLVE_ROUTES else 0


def build_candidate(
    target: Mapping[str, JsonValue], observations: Sequence[JsonObject], held: LicenceMap
) -> JsonObject | None:
    """One candidate from one source's observations, or nothing from none.

    The map is the only thing that turns a string into a family and a tier, and the family
    is what decides whether two services agree. A value with no row is reported as unmapped
    and never read as a disagreement.
    """
    if not observations:
        return None
    rows = [held.get(cast("str", observation["value"])) for observation in observations]
    mapped = [(observation, row) for observation, row in zip(observations, rows, strict=True) if row]
    unmapped = sorted(
        {
            cast("str", observation["value"])
            for observation, row in zip(observations, rows, strict=True)
            if not row
        },
        key=utf16_key,
    )
    recorded = target["recorded_licence"]
    base: JsonObject = {
        "schema_version": 1,
        "source_id": target["source_id"],
        "recorded_licence": recorded,
        "recorded_tier": target["recorded_tier"],
        "unmapped_values": cast("JsonValue", unmapped),
        "observations": cast("JsonValue", sorted(observations, key=_route)),
    }
    nothing: JsonObject = {"observed_licence": None, "family": None, "archive_tier": None}
    if not mapped:
        named = ", ".join(f'"{value}"' for value in unmapped)
        reason = f"no row in data/archive/licence-map.tsv for {named}"
        said: JsonObject = {**nothing, "status": "unmapped", "agreement": "no-candidate", "reason": reason}
        return archive.parse(CANDIDATE, {**base, **said})
    if len({row["family"] for _, row in mapped}) > 1:
        stated = sorted(
            (f'{seen["route"]} served "{seen["value"]}" ({row["family"]})' for seen, row in mapped),
            key=utf16_key,
        )
        reason = f"two services disagree: {'; '.join(stated)}"
        said = {**nothing, "status": "conflict", "agreement": "no-candidate", "reason": reason}
        return archive.parse(CANDIDATE, {**base, **said})
    # One family. Carry the value that states it best: a licence URL over a free-text label,
    # then the earliest route.
    seen, row = min(mapped, key=lambda pair: (not _URL.match(cast("str", pair[0]["value"])), _route(pair[0])))
    recorded_row = held.get(recorded) if isinstance(recorded, str) and recorded else None
    if not recorded:
        agreement = "none-recorded"
    elif recorded_row is None:
        agreement = "recorded-unmapped"
    else:
        agreement = "agrees" if recorded_row["family"] == row["family"] else "disagrees"
    said = {
        "status": "resolved",
        "observed_licence": seen["value"],
        "family": row["family"],
        "archive_tier": row["archive_tier"],
        "agreement": agreement,
        "reason": None,
    }
    return archive.parse(CANDIDATE, {**base, **said})


# ---- the triage queue -----------------------------------------------------------------

#: Why a source is in the queue, most actionable first.
TRIAGE_REASONS: Final = (
    "string-conflicting",
    "string-unresolved",
    "missing-unreported",
    "missing-unavailable",
    "missing-unextracted",
    "no-licence-recorded",
)
#: Where to look for terms, by the host class the source sits on.
_RESOLVERS: Final = {
    "europepmc-api": "europepmc-permissions",
    "pmc": "europepmc-permissions",
    "arxiv": "arxiv-api",
    "preprint-server": "preprint-api-license-field",
    "publisher": "crossref-license",
    "code-repository": "repository-licence-at-commit",
    "model-hub": "model-card-front-matter",
    "data-repository": "record-rights-field",
    "other-web": "retrieved-page",
}
TRIAGE_COLUMNS: Final = (
    "source_id",
    "reason",
    "resolver",
    "host",
    "publication_status",
    "doi",
    "url",
    "licence",
)
_RECORDED: Final = {"unreported", "unavailable", "unextracted"}


def _text(value: object) -> str:
    return text_of(value) or ""


def _recorded_reason(attributes: Mapping[str, JsonValue]) -> str:
    """The reason a record gives for carrying no licence, or nothing."""
    missing = attributes.get("missing_metadata")
    stated = missing.get("licence") if isinstance(missing, dict) else None
    return _text(None if stated is None else member(stated, "reason"))


def build_triage(root: Path) -> list[JsonObject]:
    """Every source needing a licence determination, sorted by ID.

    A source whose record says no licence applies is left out. Everything else with no
    licence is in the queue, and so is a string whose map row is unresolved or conflicting.
    A string with no map row at all stops the queue being built.
    """
    held = read_licence_map(root)
    rows: list[JsonObject] = []
    for record in source_records(root):
        attributes = attributes_of(record)
        licence = _text(attributes.get("licence"))
        if licence:
            row = held.get(licence)
            if row is None:
                raise ArchiveError(
                    f'No row in data/archive/licence-map.tsv for the licence string "{licence}" '
                    f"(source {record['id']}). Add a reviewed row before regenerating the queue."
                )
            if row["basis"] not in {"unresolved", "conflicting"}:
                continue
            reason = f"string-{row['basis']}"
        else:
            recorded = _recorded_reason(attributes)
            if recorded == "inapplicable":
                continue
            reason = f"missing-{recorded}" if recorded in _RECORDED else "no-licence-recorded"
        url = _text(attributes.get("artifact_url")) or _text(attributes.get("url"))
        found = host_of(url)
        host = found.hostname if found else ""
        doi = _text(attributes.get("doi"))
        unhosted = "crossref-license" if doi else "no-url-recorded"
        rows.append(
            {
                "source_id": record["id"],
                "reason": reason,
                "resolver": _RESOLVERS[source_class_of(host)] if host else unhosted,
                "host": host,
                "publication_status": _text(attributes.get("publication_status")),
                "doi": doi,
                "url": url,
                "licence": licence,
            }
        )
    return sorted(rows, key=lambda row: utf16_key(cast("str", row["source_id"])))


def reason_counts(rows: Sequence[Mapping[str, JsonValue]]) -> list[str]:
    """A line for each reason the queue holds, in the order of `TRIAGE_REASONS`."""
    reasons = [row["reason"] for row in rows]
    return [f"  {reason.ljust(22)} {reasons.count(reason)}" for reason in TRIAGE_REASONS if reason in reasons]


def render_triage(rows: Sequence[Mapping[str, JsonValue]], date: str) -> str:
    """The queue as the file `scripts/licence/triage.ts` writes."""
    head = [
        "# Sources needing a licence determination. Generated, never hand written.",
        "#   npx tsx scripts/licence/triage.ts",
        f"# Generated {date} from data/entities/sources.jsonl and data/archive/licence-map.tsv.",
        f"# {len(rows)} sources in the queue.",
        *(f"# {line}" for line in reason_counts(rows)),
        "# A source here is still fetched and still stored. Its bytes are private until the",
        "# determination is made. See docs/source-preservation.md.",
        "\t".join(TRIAGE_COLUMNS),
    ]
    body = ["\t".join(cast("str", row[column]) for column in TRIAGE_COLUMNS) for row in rows]
    return "".join(f"{line}\n" for line in [*head, *body])


# ---- the measured numbers -------------------------------------------------------------


def _counted(names: Sequence[JsonValue]) -> list[str]:
    """`[...counts].sort()`: a line for each name, ordered as JavaScript orders `name,count`."""
    counts = {cast("str", name): names.count(name) for name in names}
    ordered = sorted(counts.items(), key=lambda held: utf16_key(f"{held[0]},{held[1]}"))
    return [f"  {name.ljust(20)} {str(count).rjust(6)}" for name, count in ordered]


def counts_report(root: Path) -> str:
    """Every figure `scripts/licence/counts.ts` prints, which the preservation document quotes.

    Raises for a licence string the map has no row for, on which the TypeScript throws.
    """
    held = read_licence_map(root)
    licences = read_source_licences(root)
    carried = [licence for licence in licences.values() if licence is not None]
    unmapped = next((licence for licence in carried if licence not in held), None)
    if unmapped is not None:
        raise ArchiveError(f'No row in data/archive/licence-map.tsv for the licence string "{unmapped}"')
    tiers = [determine_for_source(source_id, held, licences)["archive_tier"] for source_id in licences]
    bound = sum(tiers.count(tier) for tier in ("t1-public-nc", "t2-verbatim-nd", "t3-verbatim-nd-nc"))
    lines = [
        f"source records                     {str(len(licences)).rjust(6)}",
        f"carrying a licence string          {str(len(carried)).rjust(6)}",
        f"carrying none                      {str(len(licences) - len(carried)).rjust(6)}",
        f"distinct licence strings           {str(len(held)).rjust(6)}",
        "",
        "sources by tier",
        *(
            f"  {tier.ljust(20)} {str(tiers.count(tier)).rjust(6)}  {visibility_of(tier)}"
            for tier in ARCHIVE_TIERS
        ),
        f"  publication resting on determinations 1 and 2: {bound}",
        "",
        "sources by licence family",
        *_counted([held[licence]["family"] for licence in carried]),
        "",
        "sources by determination basis",
        *_counted([held[licence]["basis"] for licence in carried]),
        "",
        f"licence triage queue               {str(len(build_triage(root))).rjust(6)}",
    ]
    return "".join(f"{line}\n" for line in lines)
