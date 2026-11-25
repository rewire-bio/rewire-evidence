"""Tests for KG export, attribute terms, draft block, SHACL validation, publish, and parity."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import rdflib
from rdflib import OWL, RDF, RDFS, BNode, Literal, URIRef

from rewire_evidence import kgdraft, kgexport, kgpublish, kgshapes, kgterms
from rewire_evidence.attributes import REGISTRY
from rewire_evidence.canonical import (
    canonical_json,
    js_indented,
    js_stringify,
    record_sha256,
    sha256_hex,
)
from rewire_evidence.cli import main
from rewire_evidence.gates import kg_gate
from rewire_evidence.jsonl import StoreError, write_text
from rewire_evidence.records import load_records
from rewire_evidence.schema import parse_record, public_records
from rewire_evidence.vocab import load_schemes
from tests.recorded import (
    BASE_ROWS,
    KG_FIXTURE,
    VALIDATION,
    row_of,
    schemes,
    write_configuration,
    write_scripts,
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
MODS = (
    "scripts/kg/export.ts",
    "scripts/kg/attribute-terms.ts",
    "scripts/kg/draft-block.ts",
    "scripts/kg/publish.ts",
)
RB_ID = kgshapes.ID_PREFIX
STATUS_DISC = URIRef("https://benchmarks.rewire.it/vocab/status/discovered")


def _setup_ontology_store(root: Path, rows: list[dict[str, Any]] | None = None) -> Path:
    held = BASE_ROWS if rows is None else rows
    by_file: dict[Path, list[tuple[str, dict[str, Any]]]] = {}
    for row in held:
        shard = sha256_hex(str(row["id"]))[0]
        kind = str(row["kind"])
        if kind in {"evaluation", "result", "claim"}:
            target = root / f"data/evidence/{kind}s/{shard}.jsonl"
        else:
            target = root / f"data/entities/{kind.replace('_', '-')}s.jsonl"
        by_file.setdefault(target, []).append((str(row["id"]), row))
        prov_target = root / f"data/provenance/records/{shard}.jsonl"
        prov_row: dict[str, Any] = {
            "id": row["id"],
            "sha256": record_sha256(row),
            "added_in": "a-batch",
            "added_by": "records:add",
            "changed_by": [],
        }
        by_file.setdefault(prov_target, []).append((str(row["id"]), prov_row))
    for path, items in by_file.items():
        items.sort(key=lambda pair: pair[0])
        write_text(path, "".join(f"{js_stringify(obj)}\n" for _, obj in items))
    write_configuration(root)
    if rows is None:
        write_scripts(root)
    return root


def test_terms_and_draft_block_match_typescript_recordings(tmp_path: Path) -> None:
    mapping = VALIDATION["mapping"]
    rb_text = KG_FIXTURE["ontology"]["data/ontology/rb.ttl"]
    write_text(tmp_path / "data/ontology/rb.ttl", rb_text)
    declared = kgexport.declared_in_rb(tmp_path)

    assert kgterms.camel("metric_direction") == "metricDirection"
    assert kgterms.attribute_property(mapping, "no_such_attr") == "rb:noSuchAttr"
    custom_map = {**mapping, "attributes": {"no_prop": {"datatype": "xsd:string"}}}
    assert kgterms.attribute_property(custom_map, "no_prop") == "rb:noProp"
    assert kgterms.encoding("concept:metric").scheme == "metric"
    assert kgterms.encoding("concepts:review-method").is_list is True
    assert kgterms.encoding("unknown-type").kind == "literal"

    assert kgterms.declarations_turtle(mapping, declared) == KG_FIXTURE["declarations_text"]
    assert "rb:Uncertainty a owl:Class ;" in kgterms.declarations_turtle(mapping, set())
    assert "rb:Uncertainty a owl:Class" not in kgterms.declarations_turtle(mapping, {"rb:Uncertainty"})
    assert kgterms.shapes_turtle(mapping) == KG_FIXTURE["shapes_text"]

    loaded_schemes = schemes()
    block = kgdraft.draft_block(mapping, loaded_schemes)
    assert block == KG_FIXTURE["draft_block"]
    assert js_indented(block) + "\n" == js_indented(KG_FIXTURE["draft_block"]) + "\n"
    no_scheme_review = {**mapping, "review": {**mapping["review"], "extra": {"property": "rb:extra"}}}
    assert kgdraft.attribute_schemas(no_scheme_review, loaded_schemes)

    with pytest.raises(StoreError, match="no vocabulary data/vocab/"):
        kgdraft.attribute_schemas(mapping, {})


def test_uri_decimal_json_and_context_match_typescript_recordings() -> None:
    for case in KG_FIXTURE["uri_cases"]:
        assert kgexport.iri(case["input"]) == case["output"]
    assert kgexport.js_decode_uri_safe("%C3%A9%2Fok") == "é%2Fok"
    assert kgexport.js_decode_uri_safe("%80") == "%80"
    assert kgexport.js_decode_uri_safe("%C3") == "%C3"
    assert kgexport.js_decode_uri_safe("%C3XX") == "%C3XX"
    assert kgexport.js_decode_uri_safe("%ED%A0%80") == "%ED%A0%80"
    assert kgexport.js_decode_uri_safe("%F0%9F%98%80") == "😀"
    assert kgexport.iri("https://example.org/ok?q=1") == "<https://example.org/ok?q=1>"

    for case in KG_FIXTURE["decimal_cases"]:
        assert kgexport.canonical_decimal(case["input"]) == case["output"]
    assert kgexport.canonical_decimal("-1e3") == "-1000"

    for case in KG_FIXTURE["json_cases"]:
        assert canonical_json(case["input"]) == case["output"]

    mapping = VALIDATION["mapping"]
    context_text = json.dumps(kgexport.build_context(mapping), indent=2, ensure_ascii=False) + "\n"
    assert context_text == KG_FIXTURE["context_text"]
    assert kgexport.expand(mapping, "rb:Model") == "https://benchmarks.rewire.it/vocab#Model"
    with pytest.raises(StoreError, match="Unknown prefix"):
        kgexport.expand(mapping, "missing:Foo")
    with pytest.raises(StoreError, match="Unknown prefix"):
        kgexport.expand(mapping, "no_colon")


def test_record_quads_result_and_derivation_encodings() -> None:
    mapping = VALIDATION["mapping"]
    base_sub = KG_FIXTURE["base_subset"]
    parsed_base = [parse_record(r) for r in BASE_ROWS]
    sub = kgexport.subset_quad_lines(mapping, parsed_base, [base_sub["id"]])
    assert sub.lines == base_sub["lines"]
    assert sub.checked == base_sub["checked"]
    assert sub.context == base_sub["context"]

    res_row = dict(row_of(next(r["id"] for r in BASE_ROWS if r["kind"] == "result")))
    res_row["status"] = "source_checked"
    res_row["description"] = "A checked result."
    res_row["facets"] = {**res_row["facets"], "unknown_facet": ["ignored"], "iri_facet": ["ftp://skip"]}
    src_id = res_row["source_ids"][0]
    res_row["attributes"] = {
        **res_row["attributes"],
        "unknown_attr": "ignored",
        "numeric_value": "not-a-decimal",
        "uncertainty": {
            "type": "confidence_interval",
            "lower": "0.1",
            "upper": "0.9",
            "level": 0.95,
            "note": None,
        },
        "derivation": {
            "method": "computed-from-source-data",
            "inputs": [
                {
                    "source_id": src_id,
                    "artifact_sha256": "a" * 64,
                    "locator": "Table 1",
                    "row_filter": "all",
                    "row_count": 3,
                }
            ],
            "aggregation": "mean",
            "aggregation_source_id": src_id,
            "aggregation_locator": "Table 1",
            "script": "scripts/x.py",
            "script_sha256": "b" * 64,
            "precision": "exact",
        },
        "missing_metadata": {
            "unit_detail": {"reason": "unreported", "note": "missing"},
            "split": {"reason": "inapplicable"},
        },
        "review": {
            "method": ["llm-extraction"],
            "date": "2026-11-20",
            "reviewed_at": "2026-11-20",
            "note": None,
            "extra": None,
        },
    }
    ext_mapping = {
        **mapping,
        "facets": {**mapping["facets"], "iri_facet": {"property": "rb:iriFacet", "iri": True}},
        "review": {**mapping["review"], "note": {"property": "rb:reviewNote"}},
    }
    res_quads = kgexport.record_quads(ext_mapping, parse_record(res_row))
    assert any("vocab#lowerBound" in line for line in res_quads)
    assert any("vocab#inputSource" in line for line in res_quads)
    assert any("vocab#missingValue" in line for line in res_quads)
    assert any("vocab#note" in line for line in res_quads)
    assert any("XMLSchema#date>" in line for line in res_quads)


def test_record_quads_claim_dataset_errors_and_writers(tmp_path: Path) -> None:
    mapping = VALIDATION["mapping"]
    src_id = next(r["id"] for r in BASE_ROWS if r["kind"] == "source")
    claim = parse_record(
        {
            "id": "claim-test-1",
            "kind": "claim",
            "name": "Relevance claim",
            "description": "",
            "status": "discovered",
            "facets": {},
            "source_ids": [src_id],
            "links": [{"relation": "subject", "target_id": src_id}],
            "attributes": {"field": "links:assessed_by:p1", "relevance": "direct", "review": "not-dict"},
        }
    )
    assert any("RelevanceJudgement" in line for line in kgexport.record_quads(mapping, claim))

    ds_dict: dict[str, Any] = {
        "id": "ds-test-1",
        "kind": "dataset",
        "name": "DS",
        "description": "",
        "status": "discovered",
        "facets": {},
        "source_ids": [src_id],
        "links": [],
        "attributes": {
            "repository": "ftp://skip.invalid/non-http",
            "entity_classification": {"z": 1},
            "reported_population": {"train_validation_test": [10, 2, 3]},
        },
    }
    ds_quads = kgexport.record_quads(mapping, parse_record(ds_dict))
    assert not any("ftp://skip.invalid" in line for line in ds_quads)
    assert any("vocab#trainCount" in line for line in ds_quads)
    assert any("rdf-syntax-ns#JSON>" in line for line in ds_quads)

    ds_short = {**ds_dict, "attributes": {"reported_population": {"train_validation_test": [5]}}}
    assert any("vocab#trainCount" in line for line in kgexport.record_quads(mapping, parse_record(ds_short)))

    eval_row = dict(row_of(next(r["id"] for r in BASE_ROWS if r["kind"] == "evaluation")))
    eval_row["attributes"] = {"primary_protocol_source_id": src_id}
    assert any(src_id in line for line in kgexport.record_quads(mapping, parse_record(eval_row)))
    ds_cand = {**ds_dict, "attributes": {"candidate_benchmark_ids": ["claim-test-1"]}}
    assert any("claim-test-1" in line for line in kgexport.record_quads(mapping, parse_record(ds_cand)))

    with pytest.raises(StoreError, match="No class mapping"):
        kgexport.record_quads({**mapping, "classes": {}}, claim)
    with pytest.raises(StoreError, match="No property mapping"):
        kgexport.record_quads({**mapping, "relations": {}}, claim)

    root = _setup_ontology_store(tmp_path / "store")
    kg_dir, quads, rel_id = kgexport.export_store(root)
    assert rel_id == "unreleased"
    assert quads > 0
    assert (kg_dir / "asserted.nq").exists()
    assert (kg_dir / "manifest.json").exists()
    write_text(root / "public/omics/manifest.json", '{"release_id": "2026-11-25-rel"}\n')
    _, _, rel_id2 = kgexport.export_store(root)
    assert rel_id2 == "2026-11-25-rel"

    ids_file = tmp_path / "ids.txt"
    base_id = str(KG_FIXTURE["base_subset"]["id"])
    ids_file.write_text(f"{base_id}\n", "utf-8")
    kgexport.export_subset(root, ids_file, tmp_path / "sub-from-file")
    kgexport.export_subset(root, [base_id], tmp_path / "sub-from-list")
    kgexport.write_context(root)
    kgexport.write_terms(root)
    out_draft = kgexport.write_draft_block(root, tmp_path / "draft.json")
    assert json.loads(out_draft.read_text("utf-8")) == KG_FIXTURE["draft_block"]

    bad_map = tmp_path / "bad-map"
    write_text(bad_map / "data/ontology/mapping.json", "[]")
    with pytest.raises(StoreError, match="is not a JSON object"):
        kgexport.load_ontology_mapping(bad_map)


def test_kgshapes_validation_and_shards(tmp_path: Path) -> None:
    root = _setup_ontology_store(tmp_path / "store")
    write_text(root / "data/ontology/rb-attributes.ttl", KG_FIXTURE["declarations_text"])
    imports = root / "data/ontology/imports"
    ttl = "@prefix ex: <https://example.org/> .\nex:Thing a <http://www.w3.org/2002/07/owl#Class> .\n"
    nt = (
        "<https://example.org/S> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
        "<https://example.org/Thing> .\n"
    )
    write_text(imports / "extra.ttl", ttl)
    write_text(imports / "extra.nt", nt)
    write_text(imports / "ignore.txt", "ignored")

    ont = kgshapes.load_ontology(root)
    assert len(ont) > 0
    assert len(kgshapes.graph_from_lines([])) == 0
    assert kgshapes.nquads([], kgshapes.GRAPHS["inferred"]) == ""

    kgexport.export_store(root)
    asserted = kgshapes.load_asserted(root / "public/kg/asserted.nq")
    inferred = kgshapes.infer(asserted, ont)
    kgshapes.check_inferred(asserted, inferred)
    assert kgshapes.GRAPHS["inferred"] in kgshapes.nquads(inferred, kgshapes.GRAPHS["inferred"])
    ok, _ = kgshapes.validate_shapes(root, kgshapes.combined_data_graph(asserted, inferred))
    assert ok is True

    assert kgshapes.parse_shard("1/2") == (1, 2)
    for bad in ("1", "a/2", "0/2", "3/2"):
        with pytest.raises(kgshapes.BuildError):
            kgshapes.parse_shard(bad)
    assert "shape group 1/2" in kgshapes.shapes_only_check(root, asserted, shard="1/2")


def test_kgshapes_context_and_no_vocab(tmp_path: Path) -> None:
    root = _setup_ontology_store(tmp_path / "store")
    no_vocab_root = _setup_ontology_store(tmp_path / "no-vocab")
    for path in (no_vocab_root / "data/vocab").glob("*.ttl"):
        path.unlink()
    (no_vocab_root / "data/vocab").rmdir()
    shacl_hdr = "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
    write_text(no_vocab_root / "data/ontology/shapes.ttl", shacl_hdr)
    write_text(no_vocab_root / "data/ontology/attribute-shapes.ttl", shacl_hdr)
    ok_nv, _ = kgshapes.validate_shapes(no_vocab_root, rdflib.Graph())
    assert ok_nv is True

    bad_claim = URIRef(f"{RB_ID}c1")
    ctx_claim = URIRef(f"{RB_ID}c2")
    small = rdflib.Graph()
    small.add((ctx_claim, RDF.type, kgshapes.RB.Claim))
    small.add((ctx_claim, kgshapes.RB.status, STATUS_DISC))
    small.add((ctx_claim, RDFS.label, Literal("C2")))
    ok_ctx, rep_ctx = kgshapes.validate_shapes(root, small, asserted_only=True, context={str(ctx_claim)})
    assert ok_ctx is True
    assert "context records" in rep_ctx

    small.add((bad_claim, RDF.type, kgshapes.RB.Claim))
    small.add((bad_claim, kgshapes.RB.status, STATUS_DISC))
    small.add((bad_claim, RDFS.label, Literal("C1")))
    ok_bad, rep_bad = kgshapes.validate_shapes(root, small, asserted_only=True, context={str(ctx_claim)})
    assert ok_bad is False
    assert str(bad_claim) in rep_bad

    bad_model = URIRef(f"{RB_ID}m-bad")
    no_path = rdflib.Graph()
    no_path.add((bad_model, RDF.type, kgshapes.RB.Model))
    no_path.add((bad_model, kgshapes.RB.testedSystem, bad_model))
    no_path.add((bad_model, RDFS.label, Literal("M")))
    no_path.add((bad_model, kgshapes.RB.status, STATUS_DISC))
    ok_np, rep_np = kgshapes.validate_shapes(root, no_path, asserted_only=True, context={str(ctx_claim)})
    assert ok_np is False
    assert str(bad_model) in rep_np
    assert kgshapes.record_of("https://example.org/ext") == "https://example.org/ext"
    assert kgshapes.record_of(f"{RB_ID}r1/uncertainty") == f"{RB_ID}r1"


def test_kgshapes_inference_guards_and_errors(tmp_path: Path) -> None:
    root = _setup_ontology_store(tmp_path / "store")
    kgexport.export_store(root)
    asserted = kgshapes.load_asserted(root / "public/kg/asserted.nq")
    bad_claim = URIRef(f"{RB_ID}c1")
    bad_model = URIRef(f"{RB_ID}m-bad")

    with pytest.raises(kgshapes.BuildError, match="Protected links"):
        kgshapes.check_inferred(asserted, {(bad_claim, kgshapes.RB.evaluation, bad_model)})
    res_node = URIRef(f"{RB_ID}r-check")
    ev_node = URIRef(f"{RB_ID}e-check")
    sys_a, sys_b = URIRef(f"{RB_ID}s-a"), URIRef(f"{RB_ID}s-b")
    g_check = rdflib.Graph()
    g_check.add((res_node, kgshapes.RB.evaluation, ev_node))
    g_check.add((ev_node, kgshapes.RB.testedSystem, sys_a))
    with pytest.raises(kgshapes.BuildError, match="did not test"):
        kgshapes.check_inferred(g_check, {(res_node, kgshapes.RB.resultFor, sys_b)})

    ax_ont = rdflib.Graph()
    p1 = URIRef("https://benchmarks.rewire.it/vocab#p1")
    p2 = URIRef("https://benchmarks.rewire.it/vocab#p2")
    p3 = URIRef("https://benchmarks.rewire.it/vocab#p3")
    ax_ont.add((p1, RDFS.subPropertyOf, p2))
    ax_ont.add((p1, RDF.type, OWL.SymmetricProperty))
    head = BNode()
    rdflib.collection.Collection(ax_ont, head, [p1, p2])
    ax_ont.add((p3, OWL.propertyChainAxiom, head))
    ax_ont.add((kgshapes.RB.DisjClassA, OWL.disjointWith, kgshapes.RB.DisjClassB))
    assert p3 in kgshapes.axiom_properties(ax_ont)

    g_inconsistent = rdflib.Graph()
    g_inconsistent.add((bad_claim, RDF.type, kgshapes.RB.DisjClassA))
    g_inconsistent.add((bad_claim, RDF.type, kgshapes.RB.DisjClassB))
    with pytest.raises(kgshapes.BuildError, match="inconsistencies"):
        kgshapes.infer(g_inconsistent, ax_ont)


def test_kgshapes_unsat_sameas_and_bnode_filtering() -> None:
    bad_claim = URIRef(f"{RB_ID}c1")
    sys_a, sys_b = URIRef(f"{RB_ID}s-a"), URIRef(f"{RB_ID}s-b")

    unsat_ont = rdflib.Graph()
    c_unsat = URIRef("https://benchmarks.rewire.it/vocab#Unsat")
    unsat_ont.add((c_unsat, RDF.type, OWL.Class))
    unsat_ont.add((c_unsat, RDFS.subClassOf, OWL.Nothing))
    g_unsat = rdflib.Graph()
    g_unsat.add((bad_claim, RDF.type, c_unsat))
    with pytest.raises(kgshapes.BuildError, match=r"Records typed owl:Nothing"):
        kgshapes.infer(g_unsat, unsat_ont)

    disj_ont = rdflib.Graph()
    c_other = URIRef("https://benchmarks.rewire.it/vocab#Other")
    disj_ont.add((c_unsat, RDFS.subClassOf, c_other))
    disj_ont.add((c_unsat, OWL.disjointWith, c_other))
    disj_ont.add((c_unsat, RDF.type, OWL.Class))
    disj_ont.add((c_other, RDF.type, OWL.Class))
    with pytest.raises(kgshapes.BuildError, match=r"reasoner found inconsistencies"):
        kgshapes.infer(g_unsat, disj_ont)

    same_ont = rdflib.Graph()
    fp = URIRef("https://benchmarks.rewire.it/vocab#funcProp")
    same_ont.add((fp, RDF.type, OWL.FunctionalProperty))
    g_same = rdflib.Graph()
    g_same.add((bad_claim, fp, sys_a))
    g_same.add((bad_claim, fp, sys_b))
    with pytest.raises(kgshapes.BuildError, match=r"owl:sameAs"):
        kgshapes.infer(g_same, same_ont)

    bn_ont = rdflib.Graph()
    restr = BNode()
    p1 = URIRef("https://benchmarks.rewire.it/vocab#p1")
    bn_ont.add((restr, RDF.type, OWL.Restriction))
    bn_ont.add((restr, OWL.onProperty, p1))
    bn_ont.add((restr, OWL.someValuesFrom, OWL.Thing))
    bn_ont.add((kgshapes.RB.SubX, RDFS.subClassOf, restr))
    sub_p = URIRef("https://benchmarks.rewire.it/vocab#extSub")
    bn_ont.add((sub_p, RDFS.subPropertyOf, URIRef("https://example.org/extP")))
    bn_ont.add((URIRef("https://example.org/other"), RDF.type, kgshapes.RB.Model))
    g_bn = rdflib.Graph()
    g_bn.add((bad_claim, RDF.type, kgshapes.RB.SubX))
    g_bn.add((bad_claim, sub_p, Literal("v")))
    inferred_bn = kgshapes.infer(g_bn, bn_ont)
    assert all(str(s).startswith(RB_ID) for s, _, _ in inferred_bn)


def test_kgpublish_runner_and_errors(tmp_path: Path) -> None:
    out = kgpublish.default_runner([sys.executable, "-c", "print('ok')"], tmp_path)
    assert out.strip() == "ok"
    err_cmd = [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"]
    with pytest.raises(StoreError, match="boom"):
        kgpublish.default_runner(err_cmd, tmp_path)
    loud_cmd = [sys.executable, "-c", "import sys; print('loud'); sys.exit(3)"]
    with pytest.raises(StoreError, match="loud"):
        kgpublish.default_runner(loud_cmd, tmp_path)
    with pytest.raises(StoreError, match="exit 4"):
        kgpublish.default_runner([sys.executable, "-c", "import sys; sys.exit(4)"], tmp_path)

    root = tmp_path / "repo"
    kg_dir = root / "public/kg"
    with pytest.raises(StoreError, match="No knowledge-graph bundle"):
        kgpublish.publish_bundle(root)
    manifest_min = '{"release_id":"2026-11-25-rel","files":{"asserted.nq":{"quads":10}}}\n'
    write_text(kg_dir / "manifest.json", manifest_min)
    with pytest.raises(StoreError, match="no inferred graph"):
        kgpublish.publish_bundle(root)
    manifest_inf = (
        '{"release_id":"2026-11-25-rel","files":{"asserted.nq":{"quads":10},'
        '"inferred.nq":{"quads":5}},"derived":{"discrepancies":2}}\n'
    )
    write_text(kg_dir / "manifest.json", manifest_inf)
    with pytest.raises(StoreError, match=r"No discrepancies\.json"):
        kgpublish.publish_bundle(root)


def test_kgpublish_create_and_verify_existing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    kg_dir = root / "public/kg"
    manifest_inf = (
        '{"release_id":"2026-11-25-rel","files":{"asserted.nq":{"quads":10},'
        '"inferred.nq":{"quads":5}},"derived":{"discrepancies":2}}\n'
    )
    write_text(kg_dir / "manifest.json", manifest_inf)
    write_text(kg_dir / "discrepancies.json", '{"discrepancies":[]}\n')

    archive_bytes = b"fake-tar-gz"
    disc_bytes = b'{"discrepancies":[]}\n'
    commands: list[list[str]] = []

    def fake_runner_new(cmd: Sequence[str], _cwd: Path) -> str:
        commands.append(list(cmd))
        if cmd[0] == "uvx" and "pack" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(archive_bytes)
        elif cmd[0] == "gh" and cmd[1:3] == ["release", "view"]:
            raise StoreError("not found")
        return ""

    with pytest.raises(StoreError, match="not published"):
        kgpublish.publish_bundle(
            root, check_only=True, rdf_kg_mcp="pkg", release_target_sha="", runner=fake_runner_new
        )
    lines = kgpublish.publish_bundle(
        root, check_only=False, rdf_kg_mcp="pkg", release_target_sha="abc123", runner=fake_runner_new
    )
    assert lines[0].startswith("Published kg/2026-11-25-rel: kg-2026-11-25-rel.tar.gz")
    assert any("--target" in c for c in commands)

    def make_existing(pub_archive: bytes, pub_disc: bytes | None) -> kgpublish.Runner:
        def runner(cmd: Sequence[str], _cwd: Path) -> str:
            if cmd[0] == "uvx" and "pack" in cmd:
                Path(cmd[cmd.index("-o") + 1]).write_bytes(archive_bytes)
            elif cmd[0] == "gh" and cmd[1:3] == ["release", "download"]:
                pattern = cmd[cmd.index("--pattern") + 1]
                dest = Path(cmd[cmd.index("--dir") + 1])
                dest.mkdir(parents=True, exist_ok=True)
                if pattern.endswith(".tar.gz"):
                    (dest / pattern).write_bytes(pub_archive)
                elif pub_disc is None:
                    raise StoreError("no discrepancies asset")
                else:
                    (dest / pattern).write_bytes(pub_disc)
            return ""

        return runner

    ok_existing = kgpublish.publish_bundle(root, runner=make_existing(archive_bytes, disc_bytes))
    assert "already published and byte-identical" in ok_existing[0]
    ok_old = kgpublish.publish_bundle(root, runner=make_existing(archive_bytes, None))
    assert "already published and byte-identical" in ok_old[0]
    with pytest.raises(StoreError, match="releases are immutable"):
        kgpublish.publish_bundle(root, runner=make_existing(b"other", disc_bytes))
    with pytest.raises(StoreError, match=r"Published discrepancies\.json differs"):
        kgpublish.publish_bundle(root, runner=make_existing(archive_bytes, b"{}"))


def test_kg_cli_subcommands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_ontology_store(tmp_path / "store")
    assert main(["kg", "context", "--root", str(root)]) == 0
    assert (root / kgexport.CONTEXT_FILE).exists()
    assert main(["kg", "terms", "--root", str(root)]) == 0
    assert (root / kgexport.ATTRIBUTE_DECLARATIONS_FILE).exists()
    draft_path = tmp_path / "draft.json"
    assert main(["kg", "draft-block", "--root", str(root), str(draft_path)]) == 0
    assert draft_path.exists()
    assert main(["kg", "export", "--root", str(root)]) == 0
    assert main(["kg", "shapes", "--root", str(root)]) == 0
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text(f"{BASE_ROWS[0]['id']}\n", "utf-8")
    sub_dir = tmp_path / "sub"
    assert main(["kg", "subset", "--root", str(root), str(ids_file), str(sub_dir)]) == 0
    ctx_file = sub_dir / "context.txt"
    shapes_cmd = ["kg", "shapes", "--root", str(root), "--kg-dir", str(sub_dir), "--context", str(ctx_file)]
    assert main(shapes_cmd) == 0
    monkeypatch.setattr(kgpublish, "publish_bundle", lambda *_args, **_kw: ["ok"])
    assert main(["kg", "publish", "--root", str(root), "--check"]) == 0
    assert "ok\n" in capsys.readouterr().out
    assert main(["kg", "shapes", "--root", str(root), "--shard", "bad"]) == 1


def _make_kg_expectation(root: Path) -> tuple[dict[str, Any], list[Any], kgexport.SubsetResult]:
    for mod in MODS:
        write_text(root / mod, f"// {mod}\n")
    mapping = kgexport.load_ontology_mapping(root)
    loaded = load_records(root)
    public = public_records(loaded)
    lines = kgexport.export_quad_lines(mapping, loaded)
    chunks = [sha256_hex("\n".join(lines[i : i + 100]) + "\n") for i in range(0, len(lines), 100)]
    context_text = json.dumps(kgexport.build_context(mapping), indent=2, ensure_ascii=False) + "\n"
    decl_text = kgterms.declarations_turtle(mapping, kgexport.declared_in_rb(root))
    shapes_text = kgterms.shapes_turtle(mapping)
    draft_text = js_indented(kgdraft.draft_block(mapping, load_schemes(root))) + "\n"
    sub = kgexport.subset_quad_lines(mapping, loaded, [str(BASE_ROWS[0]["id"])])
    payload: dict[str, Any] = {
        "recorded_from": {"script": "test", "store_ref": "abc123"},
        "modules": {mod: sha256_hex((root / mod).read_bytes()) for mod in MODS},
        "records": len(loaded),
        "public_records": len(public),
        "asserted_quads": len(lines),
        "asserted_sha256": sha256_hex("\n".join(lines) + "\n"),
        "chunk_size": 100,
        "chunk_sha256s": chunks,
        "context_sha256": sha256_hex(context_text),
        "declarations_sha256": sha256_hex(decl_text),
        "attribute_shapes_sha256": sha256_hex(shapes_text),
        "draft_block_sha256": sha256_hex(draft_text),
        "sample_subset": {
            "ids": [str(BASE_ROWS[0]["id"])],
            "quads": len(sub.lines),
            "checked": len(sub.checked),
            "context": len(sub.context),
            "sha256": sha256_hex("\n".join(sub.lines) + "\n"),
        },
    }
    return payload, loaded, sub


def test_kg_parity_gate_pass_and_subsets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _setup_ontology_store(tmp_path / "store")
    expected_payload, loaded, sub = _make_kg_expectation(root)
    expected_file = tmp_path / "expected.json"
    write_text(expected_file, json.dumps(expected_payload))
    report_file = tmp_path / "out.json"
    gate_args = [str(root), "--expected", str(expected_file), "--json", str(report_file)]
    assert kg_gate(gate_args, expected_file) == 0
    assert "reproduce." in capsys.readouterr().out

    kg_fix = tmp_path / "kg-fix.json"
    val_fix = tmp_path / "val-fix.json"
    write_text(val_fix, json.dumps({"base": {"records": [js_stringify(r) for r in BASE_ROWS]}}))
    mapping = kgexport.load_ontology_mapping(root)
    replaced = [
        parse_record({**BASE_ROWS[0], "description": "updated"}),
        *[parse_record(r) for r in BASE_ROWS[1:]],
    ]
    sub_rep = kgexport.subset_quad_lines(mapping, replaced, [BASE_ROWS[0]["id"]])
    p5_ok: dict[str, Any] = {
        "name": "ok-case",
        "ops": [
            ["set", BASE_ROWS[0]["id"], ["description"], "updated"],
            ["add", {**BASE_ROWS[0], "id": "extra-1"}],
            ["drop", "extra-1"],
        ],
        "ids": [BASE_ROWS[0]["id"]],
        "ok": True,
        "lines_count": len(sub_rep.lines),
        "lines_sha256": sha256_hex("\n".join(sub_rep.lines) + "\n"),
        "checked": sub_rep.checked,
        "context": sub_rep.context,
        "shapes_result": {"conforms": True},
    }
    p6_ok: dict[str, Any] = {
        "name": "p6-ok-no-shapes",
        "ids": [str(BASE_ROWS[0]["id"])],
        "records": [r.row() for r in loaded],
        "ok": True,
        "lines_count": len(sub.lines),
        "lines_sha256": sha256_hex("\n".join(sub.lines) + "\n"),
        "checked": sub.checked,
        "context": sub.context,
    }
    write_text(kg_fix, json.dumps({"p5_subset_cases": [p5_ok], "p6_subset_cases": [p6_ok]}))
    assert kg_gate([str(root), "--expected", str(expected_file)], expected_file, (kg_fix, val_fix)) == 0
    assert "subsets=2/2" in capsys.readouterr().out


def test_kg_parity_gate_failures_and_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_ontology_store(tmp_path / "store")
    expected_payload, loaded, sub = _make_kg_expectation(root)
    loaded_rows = [r.row() for r in loaded]
    expected_file = tmp_path / "expected.json"
    write_text(
        expected_file,
        json.dumps(
            {
                **expected_payload,
                "records": 0,
                "public_records": 0,
                "asserted_quads": 0,
                "asserted_sha256": "0" * 64,
                "chunk_sha256s": ["0" * 64],
                "context_sha256": "0" * 64,
            }
        ),
    )
    (root / "scripts/kg/publish.ts").unlink()
    kg_fix = tmp_path / "kg-fix.json"
    val_fix = tmp_path / "val-fix.json"
    write_text(val_fix, json.dumps({"base": {"records": [js_stringify(r) for r in BASE_ROWS]}}))
    p5_base = {
        "name": "mismatch-case",
        "ops": [],
        "ids": [str(BASE_ROWS[0]["id"])],
        "ok": True,
        "lines_count": len(sub.lines),
        "lines_sha256": sha256_hex("\n".join(sub.lines) + "\n"),
        "checked": sub.checked,
        "context": sub.context,
    }
    p6_bad = {
        "name": "p6-bad-subset",
        "ops": [],
        "ids": [str(BASE_ROWS[0]["id"])],
        "records": loaded_rows,
        "ok": True,
        "lines_count": len(sub.lines),
        "lines_sha256": sha256_hex("\n".join(sub.lines) + "\n"),
        "checked": sub.checked,
        "context": sub.context,
        "shapes_result": {"conforms": False},
    }
    write_text(
        kg_fix,
        json.dumps(
            {
                "p5_subset_cases": [{**p5_base, "lines_count": 999999}, {**p5_base, "ok": False}],
                "p6_subset_cases": [
                    p6_bad,
                    {**p6_bad, "name": "p6-ok-shapes", "shapes_result": {"conforms": True}},
                ],
            }
        ),
    )
    assert kg_gate([str(root), "--expected", str(expected_file)], expected_file, (kg_fix, val_fix)) == 1
    assert "shapes_only conforms=True, expected False" in capsys.readouterr().err

    # Cover sample subset mismatch and p5 subset StoreError branches (ok=True and ok=False).
    write_text(
        expected_file,
        json.dumps({**expected_payload, "sample_subset": {**expected_payload["sample_subset"], "quads": 0}}),
    )
    mapping = kgexport.load_ontology_mapping(root)
    raw_rels = mapping["relations"]
    assert isinstance(raw_rels, dict)
    trimmed_rels = {k: v for k, v in raw_rels.items() if k != "implemented_by"}
    write_text(root / "data/ontology/mapping.json", json.dumps({**mapping, "relations": trimmed_rels}))
    write_text(root / "scripts/kg/publish.ts", "// scripts/kg/publish.ts\n")
    src_id = next(str(r["id"]) for r in BASE_ROWS if r["kind"] == "source")
    bad_rel_row = {
        **BASE_ROWS[0],
        "id": "bad-rel",
        "links": [{"relation": "implemented_by", "target_id": src_id}],
    }
    p5_err_ok = {**p5_base, "name": "err-ok", "ops": [["add", bad_rel_row]], "ids": ["bad-rel"], "ok": True}
    p5_err_exp = {**p5_err_ok, "name": "err-exp", "ok": False}
    write_text(kg_fix, json.dumps({"p5_subset_cases": [p5_err_ok, p5_err_exp], "p6_subset_cases": []}))
    assert kg_gate([str(root), "--expected", str(expected_file)], expected_file, (kg_fix, val_fix)) == 1
    err_text = capsys.readouterr().err
    assert "sample subset measured" in err_text
    assert "unexpected subset failure" in err_text

    # Cover Registry concepts: in kgdraft via temporary mutation.
    monkeypatch.setitem(REGISTRY["benchmark"], "temp_concepts", "concepts:direction")
    assert kgdraft.draft_block(kgexport.load_ontology_mapping(root), load_schemes(root))

    bad_map_root = _setup_ontology_store(tmp_path / "bad-map-store")
    for mod in MODS:
        write_text(bad_map_root / mod, f"// {mod}\n")
    write_text(bad_map_root / "data/ontology/mapping.json", json.dumps({**mapping, "classes": {}}))
    bad_args = [str(bad_map_root), "--expected", str(expected_file)]
    assert kg_gate(bad_args, expected_file, (kg_fix, val_fix)) == 2
    assert "No class mapping" in capsys.readouterr().err

    empty_root = _setup_ontology_store(tmp_path / "empty-store", [])
    assert kg_gate([str(empty_root), "--expected", str(expected_file)], expected_file) == 2
    assert "no asserted quads" in capsys.readouterr().err
    assert kg_gate([str(tmp_path / "nowhere"), "--expected", str(expected_file)], expected_file) == 2

    help_res = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPTS / "verify_store_kg.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_res.returncode == 0
    assert "usage:" in help_res.stdout
