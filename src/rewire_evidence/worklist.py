"""The fetch work list, read from the source records, and what the archive holds of it.

This is `shared/archive/worklist.ts` and the `status` command of `scripts/archive/cli.ts`.
One entry per source record. Sources that share a URL share one transfer and one stored
object, so the fetcher groups by URL. Nothing here fetches or writes.

A line of `sources.jsonl` that is not an object is passed over. The TypeScript passes over
everything but a null, on which it throws.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Final, cast

from rewire_evidence import archive
from rewire_evidence.canonical import JsonObject, JsonValue
from rewire_evidence.js import lower, trim
from rewire_evidence.jsonl import parse_value
from rewire_evidence.weburl import host_of

SOURCES: Final = "data/entities/sources.jsonl"

_PUBLISHERS: Final = (
    "springer|springernature|nature|sciencedirect|elsevier|wiley|oup|cell|plos|mdpi|frontiersin|pnas"
    "|science|acs|ieee|tandfonline|sagepub|cambridge|royalsocietypublishing"
)
#: The host-behaviour class of a host, by the first pattern that finds it.
_CLASSES: Final = tuple(
    (re.compile(rf"(?:^|\.)(?:{hosts})\Z"), name)
    for hosts, name in (
        (r"ebi\.ac\.uk", "europepmc-api"),
        (r"(?:biorxiv|medrxiv)\.org", "preprint-server"),
        (r"arxiv\.org", "arxiv"),
        (
            r"github\.com|githubusercontent\.com|codeload\.github\.com|gitlab\.com|bitbucket\.org",
            "code-repository",
        ),
        (r"huggingface\.co", "model-hub"),
        (r"zenodo\.org|figshare\.com|osf\.io|dataverse\.harvard\.edu", "data-repository"),
        (r"ncbi\.nlm\.nih\.gov", "pmc"),
        (rf"(?:{_PUBLISHERS})\.(?:com|org)", "publisher"),
    )
)
_HTTP: Final = re.compile(r"https?://", re.IGNORECASE | re.ASCII)


def source_class_of(host: str) -> str:
    """The host-behaviour class a treatment is keyed on."""
    lowered = lower(host)
    return next((name for pattern, name in _CLASSES if pattern.search(lowered)), "other-web")


def text_of(value: object) -> str | None:
    """Text with something in it, trimmed, or nothing."""
    return (trim(value) or None) if isinstance(value, str) else None


def attributes_of(record: Mapping[str, JsonValue]) -> JsonObject:
    """A record's attributes, or none where it has none that hold members of their own."""
    held = record.get("attributes")
    return held if isinstance(held, dict) else {}


def source_records(root: Path) -> Iterator[JsonObject]:
    """Every source record of `data/entities/sources.jsonl`, in file order."""
    path = root / SOURCES
    for number, line in enumerate(archive.raw_lines(path), 1):
        if archive.blank(line):
            continue
        record = parse_value(line, f"{path}:{number}")
        if isinstance(record, dict) and record.get("kind") == "source":
            yield record


def read_work_list(root: Path) -> JsonObject:
    """Every source record, in file order, with the first fetchable URL it carries."""
    items: list[JsonValue] = []
    without: list[JsonValue] = []
    for record in source_records(root):
        attributes = attributes_of(record)
        doi = text_of(attributes.get("doi"))
        fields = ((field, text_of(attributes.get(field))) for field in ("artifact_url", "url"))
        picked = next(((field, url) for field, url in fields if url and _HTTP.match(url)), None)
        host = host_of(picked[1]) if picked else None
        if picked is None or host is None:
            without.append({"source_id": record["id"], "doi": doi})
            continue
        items.append(
            {
                "source_id": record["id"],
                "url": picked[1],
                "url_field": picked[0],
                "doi": doi,
                "host": host.host,
                "source_class": source_class_of(host.host),
                "pinned_sha256": text_of(attributes.get("artifact_sha256")),
            }
        )
    return {"items": items, "withoutUrl": without}


def _items(work: Mapping[str, JsonValue], name: str) -> list[JsonObject]:
    return cast("list[JsonObject]", work[name])


def group_by_url(items: Sequence[JsonObject]) -> dict[str, list[JsonObject]]:
    """The work list grouped by URL, so one URL is fetched once."""
    groups: dict[str, list[JsonObject]] = {}
    for item in items:
        groups.setdefault(cast("str", item["url"]), []).append(item)
    return groups


def tally(names: Sequence[JsonValue]) -> JsonObject:
    """How often each name occurs, commonest first, and in the order first met among equals."""
    counts: dict[str, int] = {}
    for name in names:
        counts[cast("str", name)] = counts.get(cast("str", name), 0) + 1
    ordered = sorted(counts.items(), key=lambda held: -held[1])
    return dict(ordered)


def summarise(root: Path) -> JsonObject:
    """The counts the fetch run is planned from."""
    work = read_work_list(root)
    items, without = _items(work, "items"), _items(work, "withoutUrl")
    groups = group_by_url(items)
    return {
        "sources": len(items) + len(without),
        "with_url": len(items),
        "doi_only": sum(1 for entry in without if entry["doi"]),
        "neither": sum(1 for entry in without if not entry["doi"]),
        "distinct_urls": len(groups),
        "shared_urls": sum(1 for group in groups.values() if len(group) > 1),
        "with_doi": sum(1 for item in items if item["doi"]),
        "by_class": tally([item["source_class"] for item in items]),
        "by_host": tally([item["host"] for item in items]),
    }


def status(root: Path) -> JsonObject:
    """Sources total, stored, held but not the artifact, failed by reason, and bytes held.

    A receipt says a transfer happened. Only the cache says the bytes are still held, so a
    stored source whose object the cache lacks is counted apart.
    """
    work = read_work_list(root)
    items, without = _items(work, "items"), _items(work, "withoutUrl")
    latest = archive.latest_by_source(archive.read_receipts(root))
    manifest = archive.read_manifest(root)
    cache = archive.cached_files(root)
    stored = missing = 0
    not_artifact: list[JsonValue] = []
    failed: list[JsonValue] = []
    for item in items:
        receipt = latest.get(cast("str", item["source_id"]))
        if receipt is None:
            continue
        reason = receipt["reason"] or "http_error"
        if receipt["outcome"] == "stored":
            held = isinstance(receipt["sha256"], str) and archive.has_object(receipt["sha256"], root)
            stored += held
            missing += not held
        elif receipt["outcome"] == "not_artifact":
            not_artifact.append(reason)
        else:
            failed.append(reason)
    attempted = stored + missing + len(not_artifact) + len(failed)
    return {
        "sources": len(items) + len(without),
        "with_url": len(items),
        "stored": stored,
        "stored_but_bytes_missing": missing,
        "not_artifact": len(not_artifact),
        "failed": len(failed),
        "no_attempt_yet": len(items) - attempted,
        "not_artifact_by_reason": tally(not_artifact),
        "failures_by_reason": tally(failed),
        "manifest_rows": len(manifest),
        "objects_in_cache": len(cache),
        "expanded_bytes": sum(cast("int", manifest[key]["bytes"]) for key, _ in cache if key in manifest),
        "compressed_bytes": sum(file.stat().st_size for _, file in cache),
    }
