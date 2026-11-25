"""Hold the KG export and SHACL checks to what the TypeScript produces at the pinned store."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from rewire_evidence import kgdraft, kgexport, kgshapes, kgterms
from rewire_evidence.agreement import apply_ops
from rewire_evidence.canonical import JsonObject, JsonValue, js_indented, js_utf8, sha256_hex
from rewire_evidence.checkparity import Lines
from rewire_evidence.conformance import read_recorded, recorded_ref
from rewire_evidence.jsonl import StoreError
from rewire_evidence.records import load_records
from rewire_evidence.schema import Link, Record, public_records
from rewire_evidence.vocab import Scheme, load_schemes


@dataclass
class KgReport:
    """What the KG parity gate measured against a checkout."""

    store_ref: str = ""
    records: int = 0
    public_records: int = 0
    asserted_quads: int = 0
    expected_quads: int = 0
    chunks_expected: int = 0
    chunks_same: int = 0
    modules: Lines = field(default_factory=Lines)
    subset_cases: Lines = field(default_factory=Lines)
    problems: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Render the measured report as indented JSON."""
        payload: JsonObject = {
            "store_ref": self.store_ref,
            "records": self.records,
            "public_records": self.public_records,
            "asserted_quads": self.asserted_quads,
            "expected_quads": self.expected_quads,
            "chunks_expected": self.chunks_expected,
            "chunks_same": self.chunks_same,
            "modules": {
                "expected": self.modules.expected,
                "made": self.modules.made,
                "same": self.modules.same,
            },
            "subset_cases": {
                "expected": self.subset_cases.expected,
                "made": self.subset_cases.made,
                "same": self.subset_cases.same,
            },
            "problems": list(self.problems),
        }
        return json.dumps(payload, indent=2)


def _check_modules(root: Path, recorded: Mapping[str, JsonValue], report: KgReport) -> None:
    modules = cast("dict[str, str]", recorded["modules"])
    for rel, expected_sha in modules.items():
        report.modules.expected += 1
        report.modules.made += 1
        target = root / rel
        if target.is_file() and sha256_hex(target.read_bytes()) == expected_sha:
            report.modules.same += 1
        else:
            report.problems.append(f"{rel} in the checkout is not the file the recording was made from")


def _check_chunks(lines: list[str], recorded: Mapping[str, JsonValue], report: KgReport) -> None:
    chunk_size = cast("int", recorded["chunk_size"])
    expected_chunks = cast("list[str]", recorded["chunk_sha256s"])
    report.chunks_expected = len(expected_chunks)
    full_hash = hashlib.sha256()
    actual_chunks: list[str] = []
    for start in range(0, len(lines), chunk_size):
        chunk = js_utf8("\n".join(lines[start : start + chunk_size]) + "\n")
        full_hash.update(chunk)
        actual_chunks.append(sha256_hex(chunk))
    for idx, (actual, expected) in enumerate(zip(actual_chunks, expected_chunks, strict=False)):
        if actual == expected:
            report.chunks_same += 1
        else:
            report.problems.append(f"asserted chunk {idx} digest is {actual}, expected {expected}")
    if len(actual_chunks) != len(expected_chunks):
        report.problems.append(
            f"asserted chunks count is {len(actual_chunks)}, expected {len(expected_chunks)}"
        )
    asserted_sha = full_hash.hexdigest()
    expected_sha = cast("str", recorded["asserted_sha256"])
    if asserted_sha != expected_sha:
        report.problems.append(f"asserted.nq sha256 is {asserted_sha}, expected {expected_sha}")


def _check_artifacts(
    root: Path,
    mapping: JsonObject,
    schemes: Mapping[str, Scheme],
    recorded: Mapping[str, JsonValue],
    report: KgReport,
) -> None:
    context_text = json.dumps(kgexport.build_context(mapping), indent=2, ensure_ascii=False) + "\n"
    decl_text = kgterms.declarations_turtle(mapping, kgexport.declared_in_rb(root))
    shapes_text = kgterms.shapes_turtle(mapping)
    draft_text = js_indented(kgdraft.draft_block(mapping, schemes)) + "\n"
    checks = (
        ("context.jsonld", sha256_hex(context_text), cast("str", recorded["context_sha256"])),
        ("rb-attributes.ttl", sha256_hex(decl_text), cast("str", recorded["declarations_sha256"])),
        (
            "attribute-shapes.ttl",
            sha256_hex(shapes_text),
            cast("str", recorded["attribute_shapes_sha256"]),
        ),
        ("draft_block", sha256_hex(draft_text), cast("str", recorded["draft_block_sha256"])),
    )
    for label, actual_sha, expected_sha in checks:
        if actual_sha != expected_sha:
            report.problems.append(f"{label} sha256 is {actual_sha}, expected {expected_sha}")


def _check_sample_subset(
    mapping: JsonObject,
    records: list[Record],
    recorded: Mapping[str, JsonValue],
    report: KgReport,
) -> None:
    sample = cast("dict[str, JsonValue]", recorded["sample_subset"])
    ids = cast("list[str]", sample["ids"])
    sub = kgexport.subset_quad_lines(mapping, records, ids)
    actual_sha = sha256_hex("\n".join(sub.lines) + "\n")
    expected_tuple = (
        cast("int", sample["quads"]),
        cast("int", sample["checked"]),
        cast("int", sample["context"]),
        cast("str", sample["sha256"]),
    )
    actual_tuple = (len(sub.lines), len(sub.checked), len(sub.context), actual_sha)
    if actual_tuple != expected_tuple:
        report.problems.append(f"sample subset measured {actual_tuple}, expected {expected_tuple}")


def _coerce_record(row: Mapping[str, JsonValue]) -> Record:
    raw_links = cast("list[Mapping[str, JsonValue]]", row["links"])
    return Record(
        id=cast("str", row["id"]),
        kind=cast("str", row["kind"]),
        name=cast("str", row.get("name", "")),
        description=cast("str", row.get("description", "")),
        status=cast("str", row["status"]),
        facets=cast("dict[str, list[str]]", row["facets"]),
        source_ids=cast("list[str]", row["source_ids"]),
        links=[Link(cast("str", link["relation"]), cast("str", link["target_id"])) for link in raw_links],
        attributes=cast("JsonObject", row["attributes"]),
    )


def _run_subset_case(
    ontology_root: Path,
    mapping: JsonObject,
    base_rows: list[JsonObject],
    case: Mapping[str, JsonValue],
    records_from_case: list[Record] | None,
) -> str | None:
    if records_from_case is None:
        mutated = apply_ops(base_rows, cast("list[JsonValue]", case.get("ops", [])))
        recs = [_coerce_record(row) for row in mutated]
    else:
        recs = records_from_case
    ids = cast("list[str]", case["ids"])
    try:
        sub = kgexport.subset_quad_lines(mapping, recs, ids)
    except StoreError as error:
        if case["ok"]:
            return f"{case['name']}: unexpected subset failure: {error}"
        return None
    if not case["ok"]:
        return f"{case['name']}: expected subset failure, but succeeded"
    actual_sha = sha256_hex("\n".join(sub.lines) + "\n")
    if (
        len(sub.lines) != case["lines_count"]
        or actual_sha != case["lines_sha256"]
        or sub.checked != case["checked"]
        or sub.context != case["context"]
    ):
        return f"{case['name']}: subset export mismatch"
    shapes_recorded = case.get("shapes_result")
    if isinstance(shapes_recorded, dict):
        graph = kgshapes.graph_from_lines(sub.lines)
        context = set(sub.context) or None
        conforms, _ = kgshapes.validate_shapes(ontology_root, graph, asserted_only=True, context=context)
        if conforms != bool(shapes_recorded["conforms"]):
            return f"{case['name']}: shapes_only conforms={conforms}, expected {shapes_recorded['conforms']}"
    return None


def verify_kg(root: Path, recorded: JsonObject, fixture: Mapping[str, JsonValue] | None = None) -> KgReport:
    """Verify full KG export, artifacts, and recorded subset/SHACL cases on `root`."""
    store_ref = recorded_ref(recorded)
    report = KgReport(store_ref=store_ref, expected_quads=cast("int", recorded["asserted_quads"]))
    _check_modules(root, recorded, report)

    mapping = kgexport.load_ontology_mapping(root)
    records = load_records(root)
    schemes = load_schemes(root)
    pub = public_records(records)
    report.records = len(records)
    report.public_records = len(pub)
    if report.records != recorded["records"]:
        report.problems.append(f"records count is {report.records}, expected {recorded['records']}")
    if report.public_records != recorded["public_records"]:
        report.problems.append(
            f"public_records count is {report.public_records}, expected {recorded['public_records']}"
        )

    lines = kgexport.export_quad_lines(mapping, records)
    report.asserted_quads = len(lines)
    if report.asserted_quads != report.expected_quads:
        report.problems.append(f"asserted_quads is {report.asserted_quads}, expected {report.expected_quads}")
    _check_chunks(lines, recorded, report)
    _check_artifacts(root, mapping, schemes, recorded, report)
    _check_sample_subset(mapping, records, recorded, report)

    if fixture is not None:
        base_rows = cast("list[JsonObject]", fixture["base_records"])
        for case in cast("list[Mapping[str, JsonValue]]", fixture["p5_subset_cases"]):
            report.subset_cases.expected += 1
            report.subset_cases.made += 1
            problem = _run_subset_case(root, mapping, base_rows, case, None)
            if problem is None:
                report.subset_cases.same += 1
            else:
                report.problems.append(problem)
        for case in cast("list[Mapping[str, JsonValue]]", fixture["p6_subset_cases"]):
            report.subset_cases.expected += 1
            report.subset_cases.made += 1
            recs = [_coerce_record(row) for row in cast("list[JsonObject]", case["records"])]
            problem = _run_subset_case(root, mapping, base_rows, case, recs)
            if problem is None:
                report.subset_cases.same += 1
            else:
                report.problems.append(problem)
    return report


def load_fixture_with_base(kg_fixture: Path, validation_fixture: Path) -> JsonObject:
    """Load `tests/fixtures/kg.json` enriched with parsed base records from `validation.json`."""
    kg = read_recorded(kg_fixture)
    val = read_recorded(validation_fixture)
    base_json = cast("list[str]", cast("JsonObject", val["base"])["records"])
    return {**kg, "base_records": [cast("JsonObject", json.loads(line)) for line in base_json]}
