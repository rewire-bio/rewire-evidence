"""Shared batch construction, review receipts, and store finalisation helpers.

Replaces the drifted per-batch `extract/lib.py` copies under `data/omics/**/` with one
versioned core backed by `schema.parse_record` and `writes.plan_add` / `writes.write`.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rewire_evidence import __version__, writes
from rewire_evidence.canonical import JsonObject, JsonValue, js_stringify, sha256_hex, utf16_key
from rewire_evidence.jsonl import write_text_atomically
from rewire_evidence.schema import parse_record

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """Normalise an arbitrary label into an ASCII kebab-case record ID segment."""
    folded = (
        text.lower()
        .replace("+", " plus ")
        .replace("%", " pct ")
        .replace("\u03b4", "delta")
        .replace("\u03c1", "rho")
    )
    ascii_only = "".join(ch for ch in unicodedata.normalize("NFKD", folded) if not unicodedata.combining(ch))
    return _NON_SLUG_RE.sub("-", ascii_only).strip("-")


def num(printed: str) -> str | None:
    """Decimal string for a printed value, or None if not cleanly numeric.

    Accepts leading/trailing whitespace, a trailing percent sign, grouped thousands commas
    (only before 3-digit groups), and a Unicode minus sign (`U+2212`).
    """
    cleaned = printed.strip().replace("\u2212", "-").rstrip("%").strip()
    cleaned = _THOUSANDS_RE.sub("", cleaned)
    return cleaned if _NUMBER_RE.fullmatch(cleaned) else None


def sha(path: Path | str) -> str:
    """Lowercase hex SHA-256 digest of a file on disk."""
    return sha256_hex(Path(path).read_bytes())


#: Historical alias kept from per-batch `lib.py` helpers.
sha256 = sha


def _attrs(record: JsonObject) -> JsonObject:
    raw = record.get("attributes")
    if not isinstance(raw, dict):
        raise TypeError(f"{record.get('id', '<unknown>')} attributes must be an object")
    return raw


def _str_list(values: Sequence[str]) -> list[JsonValue]:
    return [str(v) for v in values]


def extraction_review(  # noqa: PLR0913 - mirrors committed result.attributes.review schema
    *,
    method: str | Sequence[str],
    method_note: str,
    artifact_sha256: str,
    retrieval_url: str,
    date: str,
    extractor_note: str,
    reviewer: Sequence[str] = ("claude",),
    note: str = (
        "Extracted, not reviewed. "
        "The review-evidence pass must re-check this cell against the pinned artifact."
    ),
) -> JsonObject:
    """Build an unreviewed extraction provenance block for a result or claim."""
    method_value: JsonValue = method if isinstance(method, str) else _str_list(method)
    return {
        "method": method_value,
        "method_note": method_note,
        "reviewer": _str_list(reviewer),
        "reviewer_note": extractor_note,
        "date": date,
        "artifact_sha256": artifact_sha256,
        "retrieval_url": retrieval_url,
        "note": note,
    }


def write_review_receipt(  # noqa: PLR0913 - mirrors committed batch review.json receipt fields
    folder: Path | str,
    *,
    reviewer: str,
    reviewed_at: str,
    method: str = "independent-agent-review",
    review_methods: Sequence[str] = ("source-hash-verification", "independent-cell-check"),
    scope: str = "batch",
    limitations: Sequence[str] = ("Automated agent review against pinned artifacts; no human sign-off.",),
    errors: Sequence[JsonValue] = (),
    batch_sha256_before_review: str | None = None,
    extra: Mapping[str, JsonValue] | None = None,
    filename: str = "review.json",
) -> JsonObject:
    """Write `review.json` hashing every artifact/code file in `folder` and naming this package version."""
    base = Path(folder)
    files: dict[str, JsonValue] = {}
    for item in sorted(base.rglob("*"), key=lambda path: path.relative_to(base).as_posix()):
        if not item.is_file():
            continue
        rel = item.relative_to(base)
        if rel.as_posix() == filename or "__pycache__" in rel.parts or rel.suffix == ".pyc":
            continue
        files[rel.as_posix()] = sha(item)

    receipt: JsonObject = {
        "schema_version": "1.0",
        "package": "rewire-evidence",
        "package_version": __version__,
        "producer": f"rewire-evidence=={__version__}",
        "method": method,
        "review_methods": _str_list(review_methods),
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "scope": scope,
        "limitations": _str_list(limitations),
        "errors": list(errors),
    }
    if batch_sha256_before_review is not None:
        receipt["batch_sha256_before_review"] = batch_sha256_before_review
    if extra is not None:
        for key, value in extra.items():
            receipt[key] = value
    receipt["files"] = files
    write_text_atomically(base / filename, json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    return receipt


class Batch:
    """Accumulator for one extraction batch that writes canonical `batch.jsonl` and `claims.csv`."""

    def __init__(
        self,
        folder: Path | str,
        prefix: str,
        *,
        areas: Sequence[str],
        contexts: Sequence[str] = ("research",),
        today: str = "2026-11-25",
    ) -> None:
        """Initialise an empty batch rooted at `folder` with ID prefix `prefix`."""
        self.folder = Path(folder)
        self.prefix = prefix
        self.areas = list(areas)
        self.contexts = list(contexts)
        self.today = today
        self.records: list[JsonObject] = []
        self.ids: set[str] = set()
        self.claim_rows: list[list[str]] = []

    def rid(self, *parts: str) -> str:
        """Build a record ID with the batch prefix and slugified non-empty parts."""
        tokens = [slug(part) for part in parts if part]
        joined = "-".join(token for token in tokens if token)
        return f"{self.prefix}-{joined}" if joined else self.prefix

    def facets(self, method_types: Sequence[str] | None = None) -> JsonObject:
        """Return standard `facets` block, adding `method_types` when non-empty."""
        result: JsonObject = {
            "areas": _str_list(self.areas),
            "contexts": _str_list(self.contexts),
        }
        if method_types:
            result["method_types"] = _str_list(method_types)
        return result

    def add(self, record: JsonObject) -> str:
        """Register one record, rejecting duplicate IDs."""
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("record id must be a non-empty string")
        if record_id in self.ids:
            raise ValueError(f"duplicate id {record_id}")
        _attrs(record)
        self.ids.add(record_id)
        self.records.append(record)
        return record_id

    def source(
        self,
        record_id: str,
        name: str,
        attrs: Mapping[str, JsonValue],
        *,
        description: str,
    ) -> str:
        """Add a `source` record with `needs_review` status."""
        return self.add(
            {
                "id": record_id,
                "kind": "source",
                "name": name,
                "description": description,
                "status": "needs_review",
                "facets": self.facets(),
                "source_ids": [],
                "links": [],
                "attributes": dict(attrs),
            }
        )

    def entity(  # noqa: PLR0913, PLR0917 - mirrors legacy lib.py Batch.entity signature
        self,
        kind: str,
        record_id: str,
        name: str,
        description: str,
        source_ids: Sequence[str],
        attrs: Mapping[str, JsonValue],
        *,
        method_types: Sequence[str] | None = None,
        links: Sequence[Mapping[str, JsonValue]] = (),
    ) -> str:
        """Add an entity record (`model`, `dataset`, `protocol`, `configuration`, etc.)."""
        return self.add(
            {
                "id": record_id,
                "kind": kind,
                "name": name,
                "description": description,
                "status": "needs_review",
                "facets": self.facets(method_types),
                "source_ids": _str_list(source_ids),
                "links": [dict(link) for link in links],
                "attributes": dict(attrs),
            }
        )

    def evaluation(  # noqa: PLR0913 - mirrors Batch evaluation builder arguments
        self,
        record_id: str,
        name: str,
        source_ids: Sequence[str],
        *,
        system: str,
        assessment: str,
        data: str,
        origin: str,
        comparison: Mapping[str, JsonValue],
        locator: str,
        version: str | None = None,
        description: str = "Published comparison transcribed from the pinned source; not reproduced.",
        missing: Mapping[str, JsonValue] | None = None,
        extra: Mapping[str, JsonValue] | None = None,
    ) -> str:
        """Add an `evaluation` record linking system, assessment protocol, and dataset."""
        attrs: JsonObject = {
            "origin": origin,
            "protocol": assessment,
            "version": f"Primary source as retrieved {self.today}" if version is None else version,
            "comparison": dict(comparison),
            "source_locator": locator,
        }
        miss: dict[str, JsonValue] = dict(missing) if missing is not None else {}
        for key, value in comparison.items():
            miss_key = f"comparison.{key}"
            if value is None and miss_key not in miss:
                miss[miss_key] = {"reason": "unreported"}
        if miss:
            attrs["missing_metadata"] = dict(sorted(miss.items()))
        if extra:
            attrs.update(extra)
        return self.add(
            {
                "id": record_id,
                "kind": "evaluation",
                "name": name,
                "description": description,
                "status": "needs_review",
                "facets": self.facets(),
                "source_ids": _str_list(source_ids),
                "links": [
                    {"relation": "system", "target_id": system},
                    {"relation": "assessment", "target_id": assessment},
                    {"relation": "data", "target_id": data},
                ],
                "attributes": attrs,
            }
        )

    def result(  # noqa: PLR0913 - mirrors Batch result builder arguments
        self,
        record_id: str,
        name: str,
        *,
        source_id: str,
        source_ids: Sequence[str],
        evaluation: str,
        printed: str,
        metric: str,
        direction: str,
        unit: str,
        locator: str,
        review: Mapping[str, JsonValue],
        numeric: str | bool | None = None,
        qualifier: str | None = None,
        unit_detail: str | None = None,
        uncertainty: Mapping[str, JsonValue] | None = None,
        value_missing: Mapping[str, JsonValue] | None = None,
        extra: Mapping[str, JsonValue] | None = None,
    ) -> str:
        """Add a `result` record linked to an evaluation and log it in `claims.csv`."""
        parsed_numeric = None if numeric is False else (num(printed) if numeric is None else str(numeric))
        attrs: JsonObject = {
            "metric": metric,
            "metric_direction": direction,
            "unit": unit,
            "printed_value": printed,
            "numeric_value": parsed_numeric,
            "source_locator": locator,
            "review": dict(review),
        }
        miss: dict[str, JsonValue] = {}
        if parsed_numeric is None:
            miss["value"] = (
                dict(value_missing)
                if value_missing is not None
                else {"reason": "unreported", "note": "Printed cell is not a number"}
            )
        if qualifier is not None:
            attrs["metric_qualifier"] = qualifier
        if unit_detail is not None:
            attrs["unit_detail"] = unit_detail
        if uncertainty is not None:
            attrs["uncertainty"] = dict(uncertainty)
        else:
            miss["uncertainty"] = {"reason": "unreported"}
        if miss:
            attrs["missing_metadata"] = dict(sorted(miss.items()))
        if extra:
            attrs.update(extra)
        self.claim_rows.append([record_id, source_id, locator, printed, "result"])
        return self.add(
            {
                "id": record_id,
                "kind": "result",
                "name": name,
                "description": (
                    "Reported measurement transcribed from the pinned source. Not independently reproduced."
                ),
                "status": "needs_review",
                "facets": {},
                "source_ids": _str_list(source_ids),
                "links": [{"relation": "evaluation", "target_id": evaluation}],
                "attributes": attrs,
            }
        )

    def claim(  # noqa: PLR0913 - mirrors Batch claim builder arguments
        self,
        record_id: str,
        name: str,
        description: str,
        *,
        subject: str,
        source_ids: Sequence[str],
        attrs: Mapping[str, JsonValue],
        source_id: str | None = None,
    ) -> str:
        """Add a `claim` record linked via `subject` and log it in `claims.csv`."""
        row_source = source_ids[0] if source_id is None else source_id
        locator = str(attrs.get("source_locator", ""))
        printed = str(attrs.get("value", ""))[:200]
        self.claim_rows.append([record_id, row_source, locator, printed, "claim"])
        return self.add(
            {
                "id": record_id,
                "kind": "claim",
                "name": name,
                "description": description,
                "status": "needs_review",
                "facets": {},
                "source_ids": _str_list(source_ids),
                "links": [{"relation": "subject", "target_id": subject}],
                "attributes": dict(attrs),
            }
        )

    def judgement(  # noqa: PLR0913 - mirrors Batch use-case relevance judgement fields
        self,
        *,
        use_case: str,
        use_case_name: str,
        protocol: str,
        protocol_name: str,
        source_ids: Sequence[str],
        relevance: str,
        endpoint: str,
        rationale: str,
        limitations: Sequence[str],
        citation_locators: Sequence[Mapping[str, str]],
        comparison_group: str,
        comparison_title: str,
        stratum_label: str,
        stratum_order: int,
        headline_metric: str,
        constraints: Sequence[str] | None = None,
        reviewed_evaluations: Sequence[str] | None = None,
        reason: str | None = None,
        short: str | None = None,
    ) -> str:
        """Add a `links:assessed_by:` relevance judgement claim for a protocol and use case."""
        prefix_token = f"{self.prefix}-protocol-"
        default_short = protocol.removeprefix(prefix_token)
        suffix = default_short if short is None else short
        claim_id = f"use-case-mapping-{self.prefix}-{suffix}"
        source_locator = "; ".join(f"{entry['source_id']}: {entry['locator']}" for entry in citation_locators)
        default_constraints = (
            "Inspect every linked evaluation's source locator before citing a result.",
            "Do not combine this mapping's evaluations with any other protocol's results.",
        )
        attrs: JsonObject = {
            "field": f"links:assessed_by:{protocol}",
            "value": protocol,
            "relevance": relevance,
            "endpoint": endpoint,
            "rationale": rationale,
            "constraints": _str_list(default_constraints if constraints is None else constraints),
            "limitations": _str_list(limitations),
            "citation_locators": [{str(k): str(v) for k, v in entry.items()} for entry in citation_locators],
            "source_locator": source_locator,
            "headline_metric": headline_metric,
            "comparison_group": comparison_group,
            "comparison_title": comparison_title,
            "stratum_label": stratum_label,
            "stratum_order": stratum_order,
            "reason": (f"Proposed relevance judgement for {self.prefix}." if reason is None else reason),
        }
        if reviewed_evaluations is not None:
            attrs["reviewed_evaluations"] = _str_list(sorted(reviewed_evaluations))
        self.claim_rows.append([claim_id, source_ids[0], source_locator, protocol, "judgement"])
        return self.add(
            {
                "id": claim_id,
                "kind": "claim",
                "name": f'Relevance of {protocol_name} to "{use_case_name}"',
                "description": rationale,
                "status": "needs_review",
                "facets": {},
                "source_ids": _str_list(source_ids),
                "links": [{"relation": "subject", "target_id": use_case}],
                "attributes": attrs,
            }
        )

    def write_links(
        self,
        links: Sequence[Mapping[str, JsonValue]],
        *,
        note: str = (
            "Proposed assessed_by links to add to the use_case records after judgement claims are reviewed."
        ),
    ) -> Path:
        """Write `use-case-links.json` atomically inside the batch folder."""
        payload = {"note": note, "links": [dict(link) for link in links]}
        target = self.folder / "use-case-links.json"
        write_text_atomically(target, json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
        return target

    def write(self, extra_files: Mapping[str, str] | None = None) -> dict[str, int]:
        """Write `batch.jsonl` (canonical JS stringification, UTF-16 ID order), `claims.csv`, and extras."""
        self.folder.mkdir(parents=True, exist_ok=True)
        for record in self.records:
            attrs = _attrs(record)
            missing = attrs.get("missing_metadata")
            if isinstance(missing, dict) and missing:
                attrs["missing_metadata"] = dict(sorted(missing.items()))

        ordered = sorted(self.records, key=lambda rec: utf16_key(str(rec["id"])))
        lines = "".join(f"{js_stringify(rec)}\n" for rec in ordered)
        write_text_atomically(self.folder / "batch.jsonl", lines)

        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf, lineterminator="\n")
        writer.writerow(["record_id", "source_id", "locator", "printed_value", "review_scope"])
        for row in sorted(self.claim_rows):
            writer.writerow(row)
        write_text_atomically(self.folder / "claims.csv", csv_buf.getvalue())

        if extra_files is not None:
            for name, content in extra_files.items():
                write_text_atomically(self.folder / name, content)

        counts: dict[str, int] = {}
        for rec in ordered:
            kind = str(rec["kind"])
            counts[kind] = counts.get(kind, 0) + 1
        return dict(sorted(counts.items()))


@dataclass(frozen=True)
class _ReviewContext:
    reviewer_note: str
    reviewed_at: str
    review_date: str
    review_note: str
    source_note: str
    review_doc: str | None


def _review_source_block(attrs: JsonObject, ctx: _ReviewContext) -> None:
    attrs["review"] = {
        "method": ["source-hash-verification"],
        "method_note": ctx.source_note,
        "reviewer": ["claude"],
        "reviewer_note": ctx.reviewer_note,
        "date": ctx.review_date,
    }


def _review_result_block(
    record_id: str,
    attrs: JsonObject,
    ctx: _ReviewContext,
    remaining_notes: dict[str, str],
) -> None:
    old = attrs.get("review")
    if not isinstance(old, dict):
        raise TypeError(f"{record_id} result is missing prior review block")
    method_note = str(old["method_note"])
    note = f"Extraction: {method_note.rstrip('.')}. Independent review {ctx.review_date}: {ctx.review_note}"
    if record_id in remaining_notes:
        note = f"{note} {remaining_notes.pop(record_id)}"
    attrs["review"] = {
        "method": ["source-hash-verification", "deterministic-table-parse", "independent-cell-check"],
        "method_note": method_note,
        "reviewer": ["claude"],
        "reviewer_note": ctx.reviewer_note,
        "date": ctx.review_date,
        "artifact_sha256": old["artifact_sha256"],
        "retrieval_url": old["retrieval_url"],
        "note": note,
    }


def _review_claim_block(
    record: JsonObject,
    attrs: JsonObject,
    sources: Mapping[str, JsonObject],
    ctx: _ReviewContext,
) -> None:
    field_name = str(attrs.get("field", ""))
    if field_name.startswith("links:assessed_by:"):
        if ctx.review_doc is not None:
            attrs["reason"] = f"Add reviewed protocol relevance judgement ({ctx.review_doc})."
        attrs["review"] = {
            "method": ["ai-assisted-source-review", "scope-review"],
            "method_note": (
                "Reviewer checked endpoint, inputs, output, exclusions, relevance, and limitations."
            ),
            "reviewer": ["claude"],
            "reviewer_note": ctx.reviewer_note,
            "reviewed_at": ctx.reviewed_at,
            "note": "Automated review; not human scientific review.",
        }
        return

    src_ids = record.get("source_ids")
    if not isinstance(src_ids, list) or not src_ids or str(src_ids[0]) not in sources:
        raise ValueError(f"{record['id']} descriptive claim cannot resolve primary source")
    src_attrs = _attrs(sources[str(src_ids[0])])
    attrs["review"] = {
        "method": ["source-hash-verification", "deterministic-table-parse", "independent-cell-check"],
        "method_note": "Transcribed from the pinned source by the extraction script.",
        "reviewer": ["claude"],
        "reviewer_note": ctx.reviewer_note,
        "date": ctx.review_date,
        "artifact_sha256": src_attrs["artifact_sha256"],
        "retrieval_url": src_attrs.get("artifact_url", src_attrs["url"]),
        "note": f"Independent review {ctx.review_date}: {ctx.review_note}",
    }


def finalise(  # noqa: PLR0913 - consolidated finaliser options matching P6 write path
    batch: Batch,
    *,
    reviewer_id: str,
    reviewed_at: str,
    review_date: str,
    review_note: str,
    source_note: str,
    review_doc: str | None = None,
    result_notes: Mapping[str, str] | None = None,
    disputed: Mapping[str, Sequence[str]] | None = None,
    store_root: Path | None = None,
    added_in: str | None = None,
    configuration: Path | None = None,
    check_shapes: bool = True,
    dry_run: bool = False,
) -> JsonObject:
    """Apply review sign-off, validate records, emit `review.json`, and optionally write to the store."""
    batch.write()
    before_sha = sha(batch.folder / "batch.jsonl")

    ctx = _ReviewContext(
        reviewer_note=(
            f"Claude review agent {reviewer_id}, independent of extraction; no human review claimed."
        ),
        reviewed_at=reviewed_at,
        review_date=review_date,
        review_note=review_note,
        source_note=source_note,
        review_doc=review_doc,
    )
    remaining_notes = dict(result_notes) if result_notes is not None else {}
    remaining_disputed = {k: list(v) for k, v in disputed.items()} if disputed is not None else {}
    sources = {str(rec["id"]): rec for rec in batch.records if rec["kind"] == "source"}

    for record in batch.records:
        record_id = str(record["id"])
        attrs = _attrs(record)
        if record_id in remaining_disputed:
            record["status"] = "disputed"
            attrs["source_warnings"] = _str_list(remaining_disputed.pop(record_id))
        else:
            record["status"] = "source_checked"

        kind = record["kind"]
        if kind == "source":
            _review_source_block(attrs, ctx)
        elif kind == "result":
            _review_result_block(record_id, attrs, ctx, remaining_notes)
        elif kind == "claim":
            _review_claim_block(record, attrs, sources, ctx)

    if remaining_notes or remaining_disputed:
        unused = sorted([*remaining_notes.keys(), *remaining_disputed.keys()])
        raise ValueError(f"unused finalise keys: {unused}")

    for record in batch.records:
        parse_record(record)

    counts = batch.write()
    receipt = write_review_receipt(
        batch.folder,
        reviewer=ctx.reviewer_note,
        reviewed_at=reviewed_at,
        scope=f"Finalised {len(batch.records)} records for {batch.prefix} ({review_note}).",
        batch_sha256_before_review=before_sha,
    )

    counts_json: JsonObject = dict(counts.items())
    summary: JsonObject = {
        "counts": counts_json,
        "receipt": receipt,
        "written_ids": [],
    }
    if store_root is not None:
        batch_dir = f"data/omics/{batch.folder.name}" if added_in is None else added_in
        plan = writes.plan_add(batch.folder / "batch.jsonl", batch_dir, store_root, configuration)
        if dry_run:
            diff_lines: list[JsonValue] = list(writes.diff(plan.changes(), store_root))
            summary["store_diff"] = diff_lines
        else:
            check = writes.shapes_check(store_root) if check_shapes else None
            writes.write(plan, check=check)
            written: list[JsonValue] = list(plan.ids)
            summary["written_ids"] = written
    return summary
