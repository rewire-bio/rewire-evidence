"""Loading a store as records: the schema, the layout, the vocabularies and the references."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rewire_evidence.canonical import JsonObject, js_stringify, record_sha256
from rewire_evidence.jsonl import StoreError, join_lines, write_text
from rewire_evidence.records import load_records, parsed
from rewire_evidence.schema import RecordError, parse_record
from rewire_evidence.store import provenance_file_for, record_file, write_store
from rewire_evidence.vocab import MAPPING_FILE, VOCAB_DIR, VocabularyError, scheme_iri

MAPPING = {
    "fields": {"status": {"scheme": "status"}},
    "facets": {"areas": {"scheme": "area"}},
    "attributes": {"metric": {"scheme": "metric"}, "url": {}},
    "review": {"method": {"scheme": "review-method"}},
}
CONCEPTS = {
    "status": ["discovered", "source_checked", "excluded"],
    "area": ["dna-genomes"],
    "metric": ["auprc"],
    "review-method": ["span-check"],
    "derivation-method": ["difference"],
}


def record(record_id: str, kind: str, **members: object) -> JsonObject:
    row: JsonObject = {
        "id": record_id,
        "kind": kind,
        "name": record_id.upper(),
        "description": "",
        "status": "discovered",
        "facets": {},
        "source_ids": [],
        "links": [],
        "attributes": {},
    }
    return {**row, **members}  # type: ignore[dict-item]


def link(relation: str, target_id: str) -> JsonObject:
    return {"relation": relation, "target_id": target_id}


RECORDS = [
    record(
        "source-a",
        "source",
        attributes={"url": "https://example.org/a", "retrieved_at": "2026-01-01", "version": "1"},
    ),
    record("model-b", "model", source_ids=["source-a"], facets={"areas": ["dna-genomes"]}),
    record("model-a", "model", links=[link("family", "model-b")]),
    record("dataset-a", "dataset"),
    record("protocol-a", "protocol"),
    record(
        "eval-a",
        "evaluation",
        links=[link("system", "model-a"), link("assessment", "protocol-a"), link("data", "dataset-a")],
    ),
    record(
        "result-a",
        "result",
        status="source_checked",
        source_ids=["source-a"],
        links=[link("evaluation", "eval-a")],
        attributes={
            "printed_value": "0.5",
            "numeric_value": "0.5",
            "metric": "auprc",
            "unit": "unitless",
            "source_locator": "Table 1",
            # Keys out of alphabetical order and a number JavaScript writes its own way.
            "scoring_conditions": {"z": 1, "a": 0.00001},
            "review": {"method": ["span-check"]},
        },
    ),
    record("claim-a", "claim", status="excluded", links=[link("subject", "model-a")]),
]


def provenance_of(records: list[JsonObject]) -> list[JsonObject]:
    return [
        {"id": row["id"], "sha256": record_sha256(row), "added_in": "a-batch", "added_by": "records:add"}
        for row in records
    ]


def write_configuration(root: Path) -> None:
    """A mapping and the vocabularies it names, as a checkout holds them."""
    write_text(root / MAPPING_FILE, json.dumps(MAPPING))
    for name, keys in CONCEPTS.items():
        iri = scheme_iri(name)
        body = "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
        body += f"<{iri}> a skos:ConceptScheme .\n"
        body += "".join(
            f'<{iri}{key}> a skos:Concept ; skos:inScheme <{iri}> ; skos:prefLabel "{key}"@en .\n'
            for key in keys
        )
        write_text(root / VOCAB_DIR / f"{name}.ttl", body)


def build(root: Path, records: list[JsonObject] | None = None) -> None:
    held = RECORDS if records is None else records
    write_store(held, provenance_of(held), root)
    write_configuration(root)


def test_a_store_loads_as_records_sorted_by_id(tmp_path: Path) -> None:
    build(tmp_path)
    loaded = load_records(tmp_path)
    assert [item.id for item in loaded] == sorted(str(row["id"]) for row in RECORDS)
    assert loaded[-1] == parse_record(RECORDS[0])
    assert {js_stringify(item.row()) for item in loaded} == {js_stringify(row) for row in RECORDS}


def test_an_empty_store_loads_as_nothing(tmp_path: Path) -> None:
    write_configuration(tmp_path)
    assert load_records(tmp_path) == []


def test_the_vocabularies_are_those_of_the_repository_not_of_the_store(tmp_path: Path) -> None:
    store, repository = tmp_path / "store", tmp_path / "repository"
    write_store(RECORDS, provenance_of(RECORDS), store)
    write_configuration(repository)
    assert len(load_records(store, repository)) == len(RECORDS)
    with pytest.raises(VocabularyError, match=r"mapping\.json cannot be read"):
        load_records(store)


def test_a_line_with_its_keys_out_of_order_is_hashed_as_the_schema_writes_it(tmp_path: Path) -> None:
    # The TypeScript parses each line before it hashes it, so the digest is of the record in
    # schema order and a line in another order still matches its provenance.
    build(tmp_path)
    file = record_file("dataset", "dataset-a", tmp_path)
    row = RECORDS[3]
    write_text(file, join_lines([js_stringify(dict(reversed(row.items())))]))
    assert "dataset-a" in [item.id for item in load_records(tmp_path)]


def test_a_line_that_is_not_a_record_is_refused_with_its_place_and_its_rules(tmp_path: Path) -> None:
    build(tmp_path)
    file = record_file("dataset", "dataset-a", tmp_path)
    write_text(file, '{"id":"dataset-a","kind":"dataset","name":"","extra":1}\n')
    with pytest.raises(StoreError) as refused:
        load_records(tmp_path)
    message = str(refused.value)
    assert message.startswith(f"{file}:1 is not a record: too_small:name: Too small")
    assert 'unrecognized_keys:: Unrecognized key: "extra"' in message
    with pytest.raises(
        StoreError, match="here is not a record: invalid_type:: Invalid input: expected object"
    ):
        parsed([], "here")


def test_the_layout_checks_still_apply(tmp_path: Path) -> None:
    build(tmp_path)
    changed = {**RECORDS[3], "name": "changed"}
    write_text(record_file("dataset", "dataset-a", tmp_path), join_lines([js_stringify(changed)]))
    with pytest.raises(StoreError, match="dataset-a changed without a provenance entry"):
        load_records(tmp_path)


def test_a_provenance_row_with_no_record_is_refused(tmp_path: Path) -> None:
    build(tmp_path)
    ghost = record("ghost", "model")
    file = provenance_file_for("ghost", tmp_path)
    existing = file.read_text("utf-8") if file.exists() else ""
    lines = sorted([*existing.split("\n")[:-1], js_stringify(provenance_of([ghost])[0])])
    write_text(file, join_lines(lines))
    with pytest.raises(StoreError, match=r"data/provenance/records lists 9 records; the store holds 8"):
        load_records(tmp_path)


def test_a_value_outside_its_vocabulary_is_refused(tmp_path: Path) -> None:
    build(tmp_path, [*RECORDS, record("model-c", "model", facets={"areas": ["genomics"]})])
    with pytest.raises(
        VocabularyError, match=r'model-c: facets\.areas "genomics" is not a concept in the area'
    ):
        load_records(tmp_path)


def test_a_link_to_nothing_is_refused(tmp_path: Path) -> None:
    build(tmp_path, [*RECORDS, record("model-c", "model", links=[link("family", "gone")])])
    with pytest.raises(RecordError, match="Dangling model-c -> gone"):
        load_records(tmp_path)
