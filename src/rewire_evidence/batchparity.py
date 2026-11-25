"""Per-wave batch inventory, store provenance match rate, and isolated script reproduction."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from rewire_evidence import batch as batch_core
from rewire_evidence.canonical import JsonObject, JsonValue, canonical_json, js_stringify, sha256_hex
from rewire_evidence.jsonl import StoreError, read_rows
from rewire_evidence.schema import SchemaError, parse_record
from rewire_evidence.store import read_provenance

WAVE_PREFIXES: tuple[str, ...] = (
    "use-case-coverage",
    "gfm-guides",
    "lit-genomics",
    "qa-descriptions",
    "lit-cells-networks",
    "lit-interactions",
    "gfm2",
    "lit-rna-other",
    "lit-rna",
    "lit-structure",
    "lit-otheromics",
    "lit-protein",
    "lit-search",
)

_BUILDER_CANDIDATES: tuple[str, ...] = (
    "build.py",
    "extract/build_batch.py",
    "extract/build.py",
)

_REVIEW_CANDIDATES: tuple[str, ...] = (
    "review/apply_review.py",
    "review/apply-review.py",
)

_NETWORK_MODULES: frozenset[str] = frozenset(
    {"aiohttp", "ftplib", "http", "httpx", "requests", "socket", "ssl", "urllib"}
)

_SCRIPT_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class WaveSummary:
    """Measured batch inventory and reproduction statistics for one wave."""

    wave: str
    batches: int
    py_files: int
    py_lines: int
    lib_copies: int
    committed_rows: int
    store_unchanged_rows: int
    store_changed_rows: int
    store_other_rows: int
    reproduced_batches: int
    reproduced_rows: int
    non_reproducing_batches: int
    reproduction_rate: float
    store_row_match_rate: float

    def to_json(self) -> JsonObject:
        """Serialisable mapping form matching `scripts/expected/store-batches.json`."""
        return {
            "wave": self.wave,
            "batches": self.batches,
            "py_files": self.py_files,
            "py_lines": self.py_lines,
            "lib_copies": self.lib_copies,
            "committed_rows": self.committed_rows,
            "store_unchanged_rows": self.store_unchanged_rows,
            "store_changed_rows": self.store_changed_rows,
            "store_other_rows": self.store_other_rows,
            "reproduced_batches": self.reproduced_batches,
            "reproduced_rows": self.reproduced_rows,
            "non_reproducing_batches": self.non_reproducing_batches,
            "reproduction_rate": self.reproduction_rate,
            "store_row_match_rate": self.store_row_match_rate,
        }


@dataclass(frozen=True)
class NonReproducingBatch:
    """One batch directory whose committed `batch.jsonl` did not reproduce offline as-is."""

    batch: str
    wave: str
    rows: int
    store_unchanged_rows: int
    store_changed_rows: int
    reason: str
    detail: str

    def to_json(self) -> JsonObject:
        """Serialisable mapping form naming the batch and the exact reason it did not reproduce."""
        return {
            "batch": self.batch,
            "wave": self.wave,
            "rows": self.rows,
            "store_unchanged_rows": self.store_unchanged_rows,
            "store_changed_rows": self.store_changed_rows,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class BatchesReport:
    """Whole-store parity measurement across `data/omics/**/` Python batch scripts."""

    total_batch_dirs: int
    python_batches: int
    py_files: int
    py_lines: int
    lib_copies: int
    lib_digests: int
    committed_batches_with_jsonl: int
    committed_rows: int
    store_unchanged_rows: int
    store_changed_rows: int
    store_other_rows: int
    schema_valid_rows: int
    schema_drift_rows: int
    num_agreed_rows: int
    reproduced_batches: int
    reproduced_rows: int
    waves: tuple[WaveSummary, ...]
    non_reproducing: tuple[NonReproducingBatch, ...]
    problems: tuple[str, ...] = ()


def classify_wave(batch_name: str) -> str:
    """Assign a `data/omics/<name>` directory to its named collection wave."""
    for prefix in WAVE_PREFIXES:
        if batch_name == prefix or batch_name.startswith(f"{prefix}-"):
            return prefix
    return "benchmark-batches"


def _has_network_import(py_files: Sequence[Path]) -> bool:
    for path in py_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] in _NETWORK_MODULES for alias in node.names
            ):
                return True
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.split(".")[0] in _NETWORK_MODULES
            ):
                return True
    return False


def _run_isolated(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603 - isolated Python -I run of pinned batch script
            [sys.executable, "-I", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_SCRIPT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None


def _run_review_applier(
    repo_root: Path,
    batch_copy: Path,
    batch_name: str,
    generated: Path,
    orig_bytes: bytes,
) -> str | None:
    pre_review = generated.read_bytes()
    for rev in _REVIEW_CANDIDATES:
        if not (batch_copy / rev).is_file():
            continue
        for cwd, args in (
            (repo_root, [f"data/omics/{batch_name}/{rev}"]),
            (batch_copy, [rev]),
            (batch_copy, [rev, "batch.jsonl"]),
            (batch_copy, [rev, "--batch", "batch.jsonl"]),
        ):
            generated.write_bytes(pre_review)
            rev_proc = _run_isolated(args, cwd)
            if rev_proc is not None and rev_proc.returncode == 0 and generated.read_bytes() == orig_bytes:
                return rev
    return None


def _reproduce_batch(folder: Path, py_files: Sequence[Path]) -> tuple[bool, str, str]:
    batch_file = folder / "batch.jsonl"
    if not batch_file.is_file():
        return (False, "no_batch_jsonl", "directory holds helper or audit scripts without batch.jsonl")
    if _has_network_import(py_files):
        return (False, "uses_network_import", "script imports a network module; blocked offline")

    builder = next((rel for rel in _BUILDER_CANDIDATES if (folder / rel).is_file()), None)
    if builder is None:
        return (
            False,
            "needs_external_cli_or_custom_invocation",
            "no standard zero-arg builder script present",
        )

    orig_bytes = batch_file.read_bytes()
    with tempfile.TemporaryDirectory(prefix="rewire-evidence-batch-") as scratch:
        repo_root = Path(scratch)
        batch_copy = repo_root / "data" / "omics" / folder.name
        batch_copy.parent.mkdir(parents=True)
        shutil.copytree(folder, batch_copy)
        generated = batch_copy / "batch.jsonl"
        generated.unlink()

        built = False
        for cwd, cmd in (
            (batch_copy, [builder]),
            (repo_root, [f"data/omics/{folder.name}/{builder}"]),
        ):
            proc = _run_isolated(cmd, cwd)
            if proc is not None and proc.returncode == 0 and generated.is_file():
                built = True
                break
        if not built:
            return (
                False,
                "needs_external_cli_or_unarchived_input",
                f"{builder} could not run cleanly offline without external inputs",
            )

        if generated.read_bytes() == orig_bytes:
            return (True, "reproduced", f"{builder} reproduced batch.jsonl byte-for-byte")

        rev = _run_review_applier(repo_root, batch_copy, folder.name, generated, orig_bytes)
        if rev is not None:
            return (True, "reproduced", f"{builder} + {rev} reproduced batch.jsonl byte-for-byte")

        return (
            False,
            "post_build_review_or_store_edits",
            f"{builder} ran cleanly offline; committed batch.jsonl carries post-build review or store edits",
        )


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _inspect_batch_rows(
    batch_file: Path,
    batch_name: str,
    provenance: dict[str, JsonObject],
) -> tuple[int, int, int, int, int, int, int]:
    rows = 0
    unchanged = 0
    changed = 0
    other = 0
    schema_valid = 0
    schema_drift = 0
    num_agreed = 0
    expected_dir = f"data/omics/{batch_name}"
    for row in read_rows(batch_file):
        js_stringify(row)
        rows += 1
        try:
            parse_record(row)
            schema_valid += 1
        except SchemaError:
            schema_drift += 1

        record_id = str(row["id"])
        prov_row = provenance.get(record_id)
        if prov_row is not None and prov_row.get("added_in") == expected_dir:
            if prov_row.get("changed_by"):
                changed += 1
            else:
                unchanged += 1
        else:
            other += 1

        if row.get("kind") == "result":
            attrs = row.get("attributes")
            if isinstance(attrs, dict):
                printed = attrs.get("printed_value")
                numeric_value = attrs.get("numeric_value")
                if (
                    isinstance(printed, str)
                    and isinstance(numeric_value, str)
                    and batch_core.num(printed) == numeric_value
                ):
                    num_agreed += 1
    return rows, unchanged, changed, other, schema_valid, schema_drift, num_agreed


def _compare_expected(report: BatchesReport, recorded: JsonObject) -> list[str]:
    problems: list[str] = []
    scalar_checks = (
        ("total_batch_dirs", report.total_batch_dirs),
        ("python_batches", report.python_batches),
        ("py_files", report.py_files),
        ("py_lines", report.py_lines),
        ("lib_copies", report.lib_copies),
        ("lib_digests", report.lib_digests),
        ("committed_batches_with_jsonl", report.committed_batches_with_jsonl),
        ("committed_rows", report.committed_rows),
        ("store_unchanged_rows", report.store_unchanged_rows),
        ("store_changed_rows", report.store_changed_rows),
        ("store_other_rows", report.store_other_rows),
        ("schema_valid_rows", report.schema_valid_rows),
        ("schema_drift_rows", report.schema_drift_rows),
        ("reproduced_batches", report.reproduced_batches),
        ("reproduced_rows", report.reproduced_rows),
        ("non_reproducing_batches", len(report.non_reproducing)),
    )
    for field_name, actual in scalar_checks:
        expected_val = recorded.get(field_name)
        if expected_val != actual:
            problems.append(f"{field_name}: measured {actual}, expected {expected_val}")

    measured_reasons = dict(sorted(Counter(item.reason for item in report.non_reproducing).items()))
    expected_reasons = recorded.get("non_reproducing_reasons")
    if measured_reasons != expected_reasons:
        problems.append(f"non_reproducing_reasons: measured {measured_reasons}, expected {expected_reasons}")

    measured_waves = [wave.to_json() for wave in report.waves]
    expected_waves = recorded.get("waves")
    if measured_waves != expected_waves:
        problems.append("per-wave summary disagrees with pinned expectation")
    return problems


def _new_bucket() -> dict[str, int]:
    return {
        "batches": 0,
        "py_files": 0,
        "py_lines": 0,
        "lib_copies": 0,
        "committed_rows": 0,
        "store_unchanged_rows": 0,
        "store_changed_rows": 0,
        "store_other_rows": 0,
        "reproduced_batches": 0,
        "reproduced_rows": 0,
        "non_reproducing_batches": 0,
    }


def _wave_summaries(wave_buckets: dict[str, dict[str, int]]) -> tuple[WaveSummary, ...]:
    return tuple(
        WaveSummary(
            wave=wave,
            batches=b["batches"],
            py_files=b["py_files"],
            py_lines=b["py_lines"],
            lib_copies=b["lib_copies"],
            committed_rows=b["committed_rows"],
            store_unchanged_rows=b["store_unchanged_rows"],
            store_changed_rows=b["store_changed_rows"],
            store_other_rows=b["store_other_rows"],
            reproduced_batches=b["reproduced_batches"],
            reproduced_rows=b["reproduced_rows"],
            non_reproducing_batches=b["non_reproducing_batches"],
            reproduction_rate=_ratio(b["reproduced_batches"], b["batches"]),
            store_row_match_rate=_ratio(b["store_unchanged_rows"], b["committed_rows"]),
        )
        for wave, b in sorted(wave_buckets.items(), key=lambda kv: (-kv[1]["batches"], kv[0]))
    )


def verify_store_batches(root: Path, recorded: JsonObject | None = None) -> BatchesReport:
    """Measure every Python batch directory under `data/omics/` and compare against expectations."""
    omics = root / "data" / "omics"
    if not omics.is_dir():
        raise StoreError(f"{omics} is not a directory")
    provenance = read_provenance(root)

    totals: dict[str, int] = {
        "dirs": 0,
        "py_batches": 0,
        "py_files": 0,
        "py_lines": 0,
        "lib_copies": 0,
        "with_jsonl": 0,
        "rows": 0,
        "unchanged": 0,
        "changed": 0,
        "other": 0,
        "schema_valid": 0,
        "schema_drift": 0,
        "num_agreed": 0,
        "reproduced_batches": 0,
        "reproduced_rows": 0,
    }
    lib_digest_set: set[str] = set()
    wave_buckets: dict[str, dict[str, int]] = {}
    non_reproducing: list[NonReproducingBatch] = []

    folders = sorted(
        (d for d in omics.iterdir() if d.is_dir() and d.name not in {"audits", "layers"}),
        key=lambda d: d.name,
    )
    for folder in folders:
        totals["dirs"] += 1
        py_files = sorted(folder.rglob("*.py"))
        if not py_files:
            continue

        totals["py_batches"] += 1
        totals["py_files"] += len(py_files)
        wave = classify_wave(folder.name)
        bucket = wave_buckets.setdefault(wave, _new_bucket())
        bucket["batches"] += 1
        bucket["py_files"] += len(py_files)

        for py_file in py_files:
            raw = py_file.read_bytes()
            lines = raw.count(b"\n")
            totals["py_lines"] += lines
            bucket["py_lines"] += lines
            if py_file.name == "lib.py":
                totals["lib_copies"] += 1
                bucket["lib_copies"] += 1
                lib_digest_set.add(sha256_hex(raw))

        rows = unchanged = changed = 0
        batch_file = folder / "batch.jsonl"
        if batch_file.is_file():
            totals["with_jsonl"] += 1
            rows, unchanged, changed, other, schema_valid, schema_drift, num_agreed = _inspect_batch_rows(
                batch_file, folder.name, provenance
            )
            for key, delta in (
                ("rows", rows),
                ("unchanged", unchanged),
                ("changed", changed),
                ("other", other),
                ("schema_valid", schema_valid),
                ("schema_drift", schema_drift),
                ("num_agreed", num_agreed),
            ):
                totals[key] += delta
            bucket["committed_rows"] += rows
            bucket["store_unchanged_rows"] += unchanged
            bucket["store_changed_rows"] += changed
            bucket["store_other_rows"] += other

        ok, reason, detail = _reproduce_batch(folder, py_files)
        if ok:
            totals["reproduced_batches"] += 1
            totals["reproduced_rows"] += rows
            bucket["reproduced_batches"] += 1
            bucket["reproduced_rows"] += rows
        else:
            bucket["non_reproducing_batches"] += 1
            non_reproducing.append(
                NonReproducingBatch(
                    batch=folder.name,
                    wave=wave,
                    rows=rows,
                    store_unchanged_rows=unchanged,
                    store_changed_rows=changed,
                    reason=reason,
                    detail=detail,
                )
            )

    preliminary = BatchesReport(
        total_batch_dirs=totals["dirs"],
        python_batches=totals["py_batches"],
        py_files=totals["py_files"],
        py_lines=totals["py_lines"],
        lib_copies=totals["lib_copies"],
        lib_digests=len(lib_digest_set),
        committed_batches_with_jsonl=totals["with_jsonl"],
        committed_rows=totals["rows"],
        store_unchanged_rows=totals["unchanged"],
        store_changed_rows=totals["changed"],
        store_other_rows=totals["other"],
        schema_valid_rows=totals["schema_valid"],
        schema_drift_rows=totals["schema_drift"],
        num_agreed_rows=totals["num_agreed"],
        reproduced_batches=totals["reproduced_batches"],
        reproduced_rows=totals["reproduced_rows"],
        waves=_wave_summaries(wave_buckets),
        non_reproducing=tuple(non_reproducing),
    )
    problems = tuple(_compare_expected(preliminary, recorded)) if recorded is not None else ()
    return replace(preliminary, problems=problems)


def report_payload(report: BatchesReport, store_ref: str = "") -> JsonObject:
    """Build the JSON payload written by `--json` and pinned by `scripts/expected/store-batches.json`."""
    reasons: JsonObject = dict(sorted(Counter(item.reason for item in report.non_reproducing).items()))
    problems_list: list[JsonValue] = list(report.problems)
    payload: JsonObject = {}
    if store_ref:
        payload["recorded_from"] = {"store_ref": store_ref}
    payload.update(
        {
            "total_batch_dirs": report.total_batch_dirs,
            "python_batches": report.python_batches,
            "py_files": report.py_files,
            "py_lines": report.py_lines,
            "lib_copies": report.lib_copies,
            "lib_digests": report.lib_digests,
            "committed_batches_with_jsonl": report.committed_batches_with_jsonl,
            "committed_rows": report.committed_rows,
            "store_unchanged_rows": report.store_unchanged_rows,
            "store_changed_rows": report.store_changed_rows,
            "store_other_rows": report.store_other_rows,
            "schema_valid_rows": report.schema_valid_rows,
            "schema_drift_rows": report.schema_drift_rows,
            "num_agreed_rows": report.num_agreed_rows,
            "reproduced_batches": report.reproduced_batches,
            "reproduced_rows": report.reproduced_rows,
            "non_reproducing_batches": len(report.non_reproducing),
            "non_reproducing_reasons": reasons,
            "waves": [wave.to_json() for wave in report.waves],
            "non_reproducing": [item.to_json() for item in report.non_reproducing],
            "problems": problems_list,
        }
    )
    return payload


def to_json(report: BatchesReport, store_ref: str = "") -> str:
    """Canonical indented JSON report."""
    return canonical_json(report_payload(report, store_ref=store_ref))


def summary_lines(report: BatchesReport) -> list[str]:
    """Human-readable per-wave and non-reproduction summary table."""
    lines = [
        (
            f"python_batches={report.python_batches}/{report.total_batch_dirs} "
            f"py_files={report.py_files} py_lines={report.py_lines:,} "
            f"lib_copies={report.lib_copies} lib_digests={report.lib_digests} "
            f"reproduced={report.reproduced_batches} ({report.reproduced_rows:,} rows) "
            f"non_reproducing={len(report.non_reproducing)}"
        ),
        "wave                         batches  repro   rate  committed  unchanged  changed   store_rate",
    ]
    lines.extend(
        f"{w.wave:28} {w.batches:7} {w.reproduced_batches:6} {w.reproduction_rate:6.2%} "
        f"{w.committed_rows:10,} {w.store_unchanged_rows:10,} {w.store_changed_rows:8,} "
        f"{w.store_row_match_rate:11.2%}"
        for w in report.waves
    )
    lines.append(f"non_reproducing ({len(report.non_reproducing)} batches):")
    lines.extend(
        f"  {item.batch} [{item.wave}] rows={item.rows} reason={item.reason}: {item.detail}"
        for item in report.non_reproducing
    )
    return lines


def record_expectation(root: Path, store_ref: str) -> str:
    """Measure the pinned store and emit formatted `scripts/expected/store-batches.json`."""
    report = verify_store_batches(root)
    payload: JsonObject = report_payload(report, store_ref=store_ref)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
