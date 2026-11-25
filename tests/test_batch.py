"""Tests for `rewire_evidence.batch`, `rewire_evidence.batchparity`, and `gates.batches_gate`."""

from __future__ import annotations

import importlib.metadata
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import rewire_evidence
from rewire_evidence import batch as batch_core
from rewire_evidence import batchparity, gates
from rewire_evidence.canonical import canonical_json, shard_of
from rewire_evidence.store import write_store
from tests.recorded import write_base


def _write_clean_store(root: Path) -> None:
    write_base(root)


def test_version_matches_package_metadata() -> None:
    assert rewire_evidence.__version__ == importlib.metadata.version("rewire-evidence")


def test_slug_num_and_sha_helpers(tmp_path: Path) -> None:
    assert batch_core.slug("A+B % \u03b4 \u03c1 cafe\u0301!") == "a-plus-b-pct-delta-rho-cafe"
    assert batch_core.num(" 1,234.5% ") == "1234.5"
    assert batch_core.num("\u22120.75") == "-0.75"
    assert batch_core.num("not a number") is None

    sample = tmp_path / "sample.txt"
    sample.write_bytes(b"hello")
    assert batch_core.sha(sample) == batch_core.sha256(str(sample))


def test_extraction_review_and_write_review_receipt(tmp_path: Path) -> None:
    rev_str = batch_core.extraction_review(
        method="table-parse",
        method_note="Parsed Table 1.",
        artifact_sha256="a" * 64,
        retrieval_url="https://example.org/paper",
        date="2026-11-25",
        extractor_note="Automated extract.",
    )
    assert rev_str["method"] == "table-parse"

    rev_seq = batch_core.extraction_review(
        method=("table-parse", "source-hash-verification"),
        method_note="Parsed Table 1.",
        artifact_sha256="b" * 64,
        retrieval_url="https://example.org/paper",
        date="2026-11-25",
        extractor_note="Automated extract.",
    )
    assert rev_seq["method"] == ["table-parse", "source-hash-verification"]

    batch_dir = tmp_path / "b1"
    (batch_dir / "artifacts").mkdir(parents=True)
    (batch_dir / "artifacts" / "paper.txt").write_text("evidence", encoding="utf-8")
    (batch_dir / "__pycache__").mkdir()
    (batch_dir / "__pycache__" / "mod.pyc").write_bytes(b"\x00\x01")
    (batch_dir / "review.json").write_text("{}", encoding="utf-8")

    receipt = batch_core.write_review_receipt(
        batch_dir,
        reviewer="Claude tester",
        reviewed_at="2026-11-25T12:00:00Z",
        extra={"custom_note": "ok"},
    )
    assert receipt["package"] == "rewire-evidence"
    assert receipt["package_version"] == rewire_evidence.__version__
    assert receipt["producer"] == f"rewire-evidence=={rewire_evidence.__version__}"
    assert receipt["custom_note"] == "ok"
    assert receipt["files"] == {"artifacts/paper.txt": batch_core.sha(batch_dir / "artifacts" / "paper.txt")}


def test_batch_builder_variants_and_errors(tmp_path: Path) -> None:
    b = batch_core.Batch(tmp_path / "demo", "demo-20261125", areas=("dna-genomes",))
    assert b.rid() == "demo-20261125"
    assert b.rid("Model", "v1") == "demo-20261125-model-v1"
    assert b.facets() == {"areas": ["dna-genomes"], "contexts": ["research"]}
    assert b.facets(["foundation_model"])["method_types"] == ["foundation_model"]

    with pytest.raises(ValueError, match="non-empty string"):
        b.add({"id": "", "kind": "source", "attributes": {}})
    with pytest.raises(TypeError, match="attributes must be an object"):
        b.add({"id": "demo-bad-attrs", "kind": "source", "attributes": "nope"})

    b.source(
        "demo-source",
        "Paper",
        {
            "url": "https://example.org/paper",
            "artifact_sha256": "c" * 64,
            "retrieved_at": "2026-11-25T00:00:00Z",
            "version": "v1",
        },
        description="Primary source.",
    )
    with pytest.raises(ValueError, match="duplicate id"):
        b.source("demo-source", "Dup", {}, description="Dup")

    b.entity(
        "model",
        "demo-model",
        "DemoModel",
        "Model",
        ("demo-source",),
        {"entity_level": "checkpoint", "access": "Open weights"},
        method_types=("foundation_model",),
    )
    b.entity(
        "dataset",
        "demo-data",
        "DemoData",
        "Data",
        ("demo-source",),
        {"entity_level": "dataset"},
    )
    b.entity(
        "protocol",
        "demo-20261125-protocol-main",
        "Main Protocol",
        "Protocol",
        ("demo-source",),
        {"metric": "auroc", "metric_direction": "higher", "dataset": "DemoData"},
        links=({"relation": "uses_data", "target_id": "demo-data"},),
    )
    b.evaluation(
        "demo-eval-1",
        "Eval 1",
        ("demo-source",),
        system="demo-model",
        assessment="demo-20261125-protocol-main",
        data="demo-data",
        origin="published_author",
        comparison={"split": None, "note": None},
        locator="Table 1",
        version="v2",
        missing={"comparison.note": {"reason": "excluded"}},
        extra={"scope_note": "Subset"},
    )
    b.evaluation(
        "demo-eval-2",
        "Eval 2",
        ("demo-source",),
        system="demo-model",
        assessment="demo-20261125-protocol-main",
        data="demo-data",
        origin="published_author",
        comparison={"split": "test"},
        locator="Table 2",
    )

    rev = batch_core.extraction_review(
        method="table-parse",
        method_note="Table 1 row 1",
        artifact_sha256="c" * 64,
        retrieval_url="https://example.org/paper",
        date="2026-11-25",
        extractor_note="Note",
    )
    b.result(
        "demo-res-1",
        "Result 1",
        source_id="demo-source",
        source_ids=("demo-source",),
        evaluation="demo-eval-1",
        printed="0.88",
        metric="auroc",
        direction="higher",
        unit="unitless",
        locator="Table 1, row 1",
        review=rev,
        qualifier="macro",
        unit_detail="decimal",
        uncertainty={"kind": "sd", "value": "0.01"},
        extra={"subgroup": "all"},
    )
    b.result(
        "demo-res-2",
        "Result 2",
        source_id="demo-source",
        source_ids=("demo-source",),
        evaluation="demo-eval-2",
        printed="NA",
        metric="auroc",
        direction="higher",
        unit="unitless",
        locator="Table 2, row 1",
        review=rev,
        numeric=False,
        value_missing={"reason": "not_reported"},
    )
    b.claim(
        "demo-claim-1",
        "Claim 1",
        "Model evaluated zero-shot.",
        subject="demo-model",
        source_ids=("demo-source",),
        attrs={"field": "summary", "value": "zero-shot", "source_locator": "Sec 2"},
        source_id="demo-source",
    )
    b.judgement(
        use_case="demo-uc",
        use_case_name="Demo Use Case",
        protocol="demo-20261125-protocol-main",
        protocol_name="Main Protocol",
        source_ids=("demo-source",),
        relevance="direct",
        endpoint="AUROC on DemoData",
        rationale="Direct benchmark evaluation.",
        limitations=("Single cohort",),
        citation_locators=({"source_id": "demo-source", "locator": "Table 1"},),
        comparison_group="default",
        comparison_title="Default comparison",
        stratum_label="All variants",
        stratum_order=1,
        headline_metric="auroc",
        constraints=("Keep cohort intact",),
        reviewed_evaluations=("demo-eval-2", "demo-eval-1"),
        reason="Reviewed mapping.",
        short="custom-short",
    )
    links_path = b.write_links([{"use_case": "demo-uc", "target_id": "demo-20261125-protocol-main"}])
    assert links_path.is_file()
    counts = b.write(extra_files={"notes.md": "# Notes\n"})
    assert counts["result"] == 2
    assert (b.folder / "notes.md").read_text(encoding="utf-8") == "# Notes\n"

    summary_dry = batch_core.finalise(
        b,
        reviewer_id="rev-1",
        reviewed_at="2026-11-25T13:00:00Z",
        review_date="2026-11-25",
        review_note="All cells verified.",
        source_note="Hash verified.",
        review_doc="docs/reviews/demo.md",
        result_notes={"demo-res-1": "Verified decimal."},
        disputed={"demo-res-2": ("printed NA",)},
    )
    assert summary_dry["written_ids"] == []
    assert "package_version" in summary_dry["receipt"]  # type: ignore[operator]
    lines = [json.loads(line) for line in (b.folder / "batch.jsonl").read_text(encoding="utf-8").splitlines()]
    statuses = {row["id"]: row["status"] for row in lines}
    assert statuses["demo-res-1"] == "source_checked"
    assert statuses["demo-res-2"] == "disputed"


def test_batch_finalise_error_branches(tmp_path: Path) -> None:
    b_bad_res = batch_core.Batch(tmp_path / "b_bad_res", "bad1", areas=("dna-genomes",))
    b_bad_res.add(
        {
            "id": "bad1-res",
            "kind": "result",
            "name": "R",
            "description": "D",
            "status": "needs_review",
            "facets": {},
            "source_ids": [],
            "links": [],
            "attributes": {},
        }
    )
    with pytest.raises(TypeError, match="missing prior review block"):
        batch_core.finalise(
            b_bad_res,
            reviewer_id="r",
            reviewed_at="2026-11-25T00:00:00Z",
            review_date="2026-11-25",
            review_note="n",
            source_note="s",
        )

    b_bad_claim = batch_core.Batch(tmp_path / "b_bad_claim", "bad2", areas=("dna-genomes",))
    b_bad_claim.claim(
        "bad2-claim",
        "C",
        "Desc",
        subject="bad2-x",
        source_ids=("missing-src",),
        attrs={"field": "note", "value": "v", "source_locator": "p1"},
    )
    with pytest.raises(ValueError, match="cannot resolve primary source"):
        batch_core.finalise(
            b_bad_claim,
            reviewer_id="r",
            reviewed_at="2026-11-25T00:00:00Z",
            review_date="2026-11-25",
            review_note="n",
            source_note="s",
        )

    b_unused = batch_core.Batch(tmp_path / "b_unused", "bad3", areas=("dna-genomes",))
    b_unused.source(
        "bad3-src",
        "Src",
        {"url": "https://example.org", "artifact_sha256": "d" * 64},
        description="Src",
    )
    with pytest.raises(ValueError, match="unused finalise keys"):
        batch_core.finalise(
            b_unused,
            reviewer_id="r",
            reviewed_at="2026-11-25T00:00:00Z",
            review_date="2026-11-25",
            review_note="n",
            source_note="s",
            result_notes={"nonexistent-id": "extra"},
            disputed={"nonexistent-disputed": ("bad",)},
        )


def test_new_batch_end_to_end_into_store_with_shacl(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    _write_clean_store(store_root)

    batch_dir = store_root / "data/omics/demo-e2e-20261125"
    artifacts = batch_dir / "artifacts"
    artifacts.mkdir(parents=True)
    paper = artifacts / "source.txt"
    paper.write_text("AUROC 0.91\n", encoding="utf-8")
    digest = batch_core.sha(paper)

    b = batch_core.Batch(batch_dir, "demoe2e-20261125", areas=("dna-genomes",))
    src = b.source(
        b.rid("source", "paper"),
        "Demo Paper 2026",
        {
            "url": "https://example.org/demo-e2e",
            "artifact_url": "https://example.org/demo-e2e.txt",
            "version": "v1",
            "retrieved_at": "2026-11-25T00:00:00Z",
            "artifact_sha256": digest,
        },
        description="Archived text source.",
    )
    model = b.entity(
        "model",
        b.rid("model", "alpha"),
        "Demo Alpha",
        "Model record.",
        (src,),
        {"entity_level": "checkpoint", "access": "Open weights"},
        method_types=("foundation_model",),
    )
    b.entity(
        "dataset",
        b.rid("data", "panel"),
        "Demo Panel",
        "Dataset record.",
        (src,),
        {"population": "100 human variants"},
    )
    proto = b.entity(
        "protocol",
        b.rid("protocol", "auroc"),
        "Demo AUROC Protocol",
        "Protocol record.",
        (src,),
        {
            "metric": "coefficient-of-determination",
            "metric_direction": "higher",
            "dataset": "Demo Panel",
            "protocol": "Zero-shot scoring",
        },
    )
    b.claim(
        b.rid("claim", "summary"),
        "Zero-shot summary claim",
        "Evaluated without fine-tuning.",
        subject=model,
        source_ids=(src,),
        attrs={
            "field": "evaluation_mode",
            "value": "zero-shot",
            "source_locator": "Paragraph 1",
        },
    )
    b.judgement(
        use_case="benchbench-cloning-20261106-benchmark-suite",
        use_case_name="Suite",
        protocol=proto,
        protocol_name="Demo AUROC Protocol",
        source_ids=(src,),
        relevance="direct",
        endpoint="R2 on Demo Panel",
        rationale="Matches benchmark task.",
        limitations=("Small sample",),
        citation_locators=({"source_id": src, "locator": "Line 1"},),
        comparison_group="main",
        comparison_title="Main",
        stratum_label="All",
        stratum_order=1,
        headline_metric="coefficient-of-determination",
    )

    dry = batch_core.finalise(
        b,
        reviewer_id="p16-e2e",
        reviewed_at="2026-11-25T16:00:00Z",
        review_date="2026-11-25",
        review_note="Checked against local artifact.",
        source_note="Local artifact hashed cleanly.",
        store_root=store_root,
        dry_run=True,
    )
    assert isinstance(dry["store_diff"], list)
    assert len(dry["store_diff"]) > 0

    written = batch_core.finalise(
        b,
        reviewer_id="p16-e2e",
        reviewed_at="2026-11-25T16:00:00Z",
        review_date="2026-11-25",
        review_note="Checked against local artifact.",
        source_note="Local artifact hashed cleanly.",
        store_root=store_root,
        added_in="data/omics/demo-e2e-20261125",
        check_shapes=True,
        dry_run=False,
    )
    # Also assert written store reads back cleanly with read_provenance imported at top if needed
    assert len(written["written_ids"]) == 6  # type: ignore[arg-type]
    assert json.loads((batch_dir / "review.json").read_text(encoding="utf-8"))["package"] == "rewire-evidence"
    prov_shard = store_root / f"data/provenance/records/{shard_of(model)}.jsonl"
    assert model in prov_shard.read_text(encoding="utf-8")
    receipt = json.loads((batch_dir / "review.json").read_text(encoding="utf-8"))
    assert receipt["package"] == "rewire-evidence"
    assert receipt["package_version"] == rewire_evidence.__version__
    assert receipt["files"]["artifacts/source.txt"] == digest

    # Also exercise check_shapes=False path cleanly on a second batch
    b2 = batch_core.Batch(store_root / "data/omics/demo-e2e-2", "demoe2eb-20261125", areas=("dna-genomes",))
    b2.source(
        b2.rid("source", "second"),
        "Second",
        {
            "url": "https://example.org/demo-e2e-2",
            "version": "v1",
            "retrieved_at": "2026-11-25T00:00:00Z",
            "artifact_sha256": "e" * 64,
        },
        description="Second source.",
    )
    written2 = batch_core.finalise(
        b2,
        reviewer_id="p16-e2e",
        reviewed_at="2026-11-25T16:05:00Z",
        review_date="2026-11-25",
        review_note="Source checked.",
        source_note="Hashed.",
        store_root=store_root,
        check_shapes=False,
    )
    assert len(written2["written_ids"]) == 1  # type: ignore[arg-type]


def _seed_synthetic_store(store: Path) -> None:
    _write_clean_store(store)
    omics = store / "data/omics"

    # Non-py batch directory (ignored)
    (omics / "non-py-dir").mkdir(parents=True)
    (omics / "non-py-dir" / "notes.md").write_text("no python\n", encoding="utf-8")

    # 1) no_batch_jsonl + SyntaxError py file + clean py file + relative import
    b_no_jsonl = omics / "qa-descriptions-empty"
    b_no_jsonl.mkdir()
    (b_no_jsonl / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (b_no_jsonl / "helper.py").write_text("from . import foo\nimport json\n", encoding="utf-8")

    # 2) uses_network_import (Import and ImportFrom)
    valid_src = {
        "id": "lit-net1-src",
        "kind": "source",
        "name": "Net Src",
        "description": "D",
        "status": "source_checked",
        "facets": {"areas": ["dna-genomes"]},
        "source_ids": [],
        "links": [],
        "attributes": {"url": "https://example.org/n1", "artifact_sha256": "1" * 64},
    }
    b_net1 = omics / "lit-rna-net1"
    (b_net1 / "extract").mkdir(parents=True)
    (b_net1 / "extract" / "lib.py").write_text("import urllib.request\n", encoding="utf-8")
    (b_net1 / "batch.jsonl").write_text(json.dumps(valid_src) + "\n", encoding="utf-8")

    b_net2 = omics / "lit-rna-net2"
    (b_net2 / "extract").mkdir(parents=True)
    (b_net2 / "extract" / "fetch.py").write_text("from requests import get\n", encoding="utf-8")
    (b_net2 / "batch.jsonl").write_text(json.dumps(valid_src) + "\n", encoding="utf-8")

    # 3) Custom CLI, SyntaxError, historical SchemaError, non-dict/None result attrs, provenance rows
    b_custom = omics / "lit-genomics-custom"
    (b_custom / "extract").mkdir(parents=True)
    (b_custom / "extract" / "custom.py").write_text("import json\n", encoding="utf-8")
    (b_custom / "extract" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    bad_schema_row = {
        "id": "custom-bad-res",
        "kind": "result",
        "name": "Bad",
        "description": "D",
        "status": "source_checked",
        "facets": {},
        "source_ids": [],
        "links": [],
        "attributes": "not-a-dict",
    }
    non_num_res_row = {
        "id": "custom-non-num",
        "kind": "result",
        "name": "NonNum",
        "description": "D",
        "status": "source_checked",
        "facets": {},
        "source_ids": [],
        "links": [],
        "attributes": {"printed_value": "NA", "numeric_value": None},
    }
    eval_id = (
        "agront-2024-fig3e-evaluation-agront-promoter-strength-maize-protoplasts-maize-protoplasts-a-thaliana"
    )
    unchanged_row = {
        "id": "custom-unchanged",
        "kind": "result",
        "name": "ResOK",
        "description": "D",
        "status": "source_checked",
        "facets": {},
        "source_ids": ["ag-atlas-20261019-source-preprint"],
        "links": [{"relation": "evaluation", "target_id": eval_id}],
        "attributes": {
            "metric": "coefficient-of-determination",
            "metric_direction": "higher",
            "unit": "unitless",
            "printed_value": "0.5",
            "numeric_value": "0.5",
            "source_locator": "Table 1",
        },
    }
    changed_row = {
        "id": "custom-changed",
        "kind": "source",
        "name": "Changed",
        "description": "D",
        "status": "source_checked",
        "facets": {"areas": ["dna-genomes"]},
        "source_ids": [],
        "links": [],
        "attributes": {"url": "https://example.org/chg", "artifact_sha256": "2" * 64},
    }
    (b_custom / "batch.jsonl").write_text(
        "\n".join(json.dumps(r) for r in (bad_schema_row, non_num_res_row, unchanged_row, changed_row))
        + "\n",
        encoding="utf-8",
    )
    for p_obj in (
        {
            "id": "custom-unchanged",
            "sha256": "3" * 64,
            "added_in": "data/omics/lit-genomics-custom",
            "added_by": "records:add",
            "changed_by": [],
        },
        {
            "id": "custom-changed",
            "sha256": "4" * 64,
            "added_in": "data/omics/lit-genomics-custom",
            "added_by": "records:add",
            "changed_by": [{"step": "records:change", "date": "2026-11-25"}],
        },
    ):
        sf = store / f"data/provenance/records/{shard_of(str(p_obj['id']))}.jsonl"
        sf.write_text(sf.read_text(encoding="utf-8") + json.dumps(p_obj) + "\n", encoding="utf-8")

    _seed_synthetic_builders(omics, changed_row)


def _seed_synthetic_builders(omics: Path, changed_row: Mapping[str, object]) -> None:
    # 4) Self-contained direct builder reproducing byte-for-byte
    b_repro1 = omics / "gfm2-repro1"
    b_repro1.mkdir()
    r1_line = json.dumps(changed_row, separators=(",", ":")) + "\n"
    (b_repro1 / "build.py").write_text(
        f"from pathlib import Path\nPath('batch.jsonl').write_text({r1_line!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    (b_repro1 / "batch.jsonl").write_text(r1_line, encoding="utf-8")

    # 5) Self-contained builder + review/apply-review.py reproducing with --batch flag
    b_repro2 = omics / "lit-structure-repro2"
    (b_repro2 / "review").mkdir(parents=True)
    pre_line = json.dumps(changed_row) + "\n"
    post_line = json.dumps(dict(changed_row, name="Reviewed")) + "\n"
    (b_repro2 / "build.py").write_text(
        "from pathlib import Path\n"
        f"Path('data/omics/lit-structure-repro2/batch.jsonl').write_text({pre_line!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    (b_repro2 / "review" / "apply-review.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "if '--batch' not in sys.argv: sys.exit(1)\n"
        f"Path('batch.jsonl').write_text({post_line!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    (b_repro2 / "batch.jsonl").write_text(post_line, encoding="utf-8")

    # 6) post_build_review_or_store_edits (with failing apply_review and without apply_review)
    b_post1 = omics / "lit-protein-post1"
    (b_post1 / "review").mkdir(parents=True)
    (b_post1 / "build.py").write_text(
        f"from pathlib import Path\nPath('batch.jsonl').write_text({pre_line!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    (b_post1 / "review" / "apply_review.py").write_text("import sys; sys.exit(2)\n", encoding="utf-8")
    (b_post1 / "batch.jsonl").write_text(post_line, encoding="utf-8")

    b_post2 = omics / "lit-protein-post2"
    b_post2.mkdir()
    (b_post2 / "build.py").write_text(
        f"from pathlib import Path\nPath('batch.jsonl').write_text({pre_line!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    (b_post2 / "batch.jsonl").write_text(post_line, encoding="utf-8")

    # 7) needs_external_cli_or_unarchived_input
    b_fail = omics / "lit-otheromics-fail"
    b_fail.mkdir()
    (b_fail / "build.py").write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    (b_fail / "batch.jsonl").write_text(pre_line, encoding="utf-8")


def test_batchparity_synthetic_batches_and_gate_pass(tmp_path: Path) -> None:
    assert batchparity.classify_wave("use-case-coverage-somatic-x") == "use-case-coverage"
    assert batchparity.classify_wave("bioemu2-20261115") == "benchmark-batches"

    store = tmp_path / "syn_store"
    _seed_synthetic_store(store)
    exp_text = batchparity.record_expectation(store, "deadbeef")
    exp_path = tmp_path / "syn-expected.json"
    exp_path.write_text(exp_text, encoding="utf-8")

    out_json = tmp_path / "out.json"
    status = gates.batches_gate([str(store), "--expected", str(exp_path), "--json", str(out_json)], exp_path)
    assert status == 0
    assert out_json.is_file()
    payload = batchparity.report_payload(batchparity.verify_store_batches(store))
    assert payload["reproduced_batches"] == 2


def test_batchparity_gate_errors_timeout_and_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_root = tmp_path / "missing"
    expected_file = tmp_path / "expected.json"
    expected_file.write_text('{"recorded_from": {"store_ref": "abc"}}', encoding="utf-8")
    assert gates.batches_gate([str(missing_root), "--expected", str(expected_file)], expected_file) == 2

    empty_root = tmp_path / "empty_store"
    write_store([], [], empty_root)
    (empty_root / "data/omics").mkdir(parents=True)
    assert gates.batches_gate([str(empty_root), "--expected", str(expected_file)], expected_file) == 2

    # Exercise ScriptTimeout handling via public verify_store_batches API
    slow_store = tmp_path / "slow_store"
    _write_clean_store(slow_store)
    b_slow = slow_store / "data/omics/lit-otheromics-slow"
    b_slow.mkdir(parents=True)
    (b_slow / "build.py").write_text("import time; time.sleep(0.2)\n", encoding="utf-8")
    (b_slow / "batch.jsonl").write_text('{"id":"s-1"}\n', encoding="utf-8")
    monkeypatch.setattr(batchparity, "_SCRIPT_TIMEOUT_SECONDS", 0.05)
    slow_report = batchparity.verify_store_batches(slow_store)
    assert slow_report.non_reproducing[0].reason == "needs_external_cli_or_unarchived_input"

    # Exercise mismatch exits (scalar, reasons, and waves)
    store = tmp_path / "syn_store"
    _seed_synthetic_store(store)
    bad_exp = json.loads(batchparity.record_expectation(store, "deadbeef"))
    bad_exp["python_batches"] = 999
    bad_exp["non_reproducing_reasons"] = {"other": 1}
    bad_exp["waves"] = []
    bad_exp_path = tmp_path / "bad-expected.json"
    bad_exp_path.write_text(canonical_json(bad_exp), encoding="utf-8")
    assert gates.batches_gate([str(store), "--expected", str(bad_exp_path)], bad_exp_path) == 1
    assert "python_batches: measured" in capsys.readouterr().err
