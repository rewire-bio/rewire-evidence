"""What the schema gate measures, and that it notices each thing it exists to notice."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import conformance
from rewire_evidence.canonical import JsonObject, JsonValue, js_stringify, record_sha256
from rewire_evidence.conformance import (
    SchemaReport,
    check_loader,
    check_seeded,
    controlled_fields,
    differences,
    moved,
    read_recorded,
    recorded_labels,
    recorded_ref,
    scheme_facts,
    verify_schema,
)
from rewire_evidence.gates import digests_gate, schema_gate
from rewire_evidence.jsonl import StoreError, join_lines, write_text
from rewire_evidence.schema import Checked, check_record, parse_record
from rewire_evidence.store import provenance_file_for, record_file
from rewire_evidence.turtle import BlankLabels
from rewire_evidence.vocab import VOCAB_DIR, load_schemes, mapping_of, read_mapping, scheme_iri
from tests.test_records import RECORDS, build, link, provenance_of, record

SCRIPTS = Path(__file__).parent.parent / "scripts"
#: A URL Node reads and this library does not judge: its host needs IDNA mapping.
IDNA = "https://b\u00fccher.example/"
SEEDS: dict[str, JsonObject] = {
    "status": {"status": "nope"},
    "facets.areas": {"facets": {"areas": ["nope"]}},
    "attributes.metric": {"attributes": {"metric": "nope"}},
    "attributes.review.method": {"attributes": {"review": {"method": "nope"}}},
    "attributes.derivation.method": {"attributes": {"derivation": {"method": "nope"}}},
}
SCHEME_OF = {
    "status": "status",
    "facets.areas": "area",
    "attributes.metric": "metric",
    "attributes.review.method": "review-method",
    "attributes.derivation.method": "derivation-method",
}


def seeds() -> list[JsonValue]:
    out: list[JsonValue] = []
    for name, members in SEEDS.items():
        row = {"id": "seeded", "facets": {}, "attributes": {}, **members}
        refusal = f'seeded: {name} "nope" is not a concept in the {SCHEME_OF[name]} vocabulary'
        error = f"Uncontrolled values:\n  {refusal}"
        out.append({"field": name, "record": json.dumps(row), "error": error})
    return out


def recorded_for(root: Path) -> JsonObject:
    """What the recorder would say of this store, taken from a run that had no problems."""
    report = verify_schema(root, bare())
    return {**bare(), **report.measured}


def bare() -> JsonObject:
    """An expectation that says where it came from and nothing of what was measured."""
    return {
        "recorded_from": {"store_ref": "abc123"},
        "blank_labels": {"documents": 0, "anonymous": 0},
        "seeded": seeds(),
    }


def write_recorded(root: Path, folder: Path) -> Path:
    path = folder / "recorded.json"
    path.write_text(json.dumps(recorded_for(root)), encoding="utf-8")
    return path


def test_a_store_that_conforms_is_measured_in_full(tmp_path: Path) -> None:
    build(tmp_path)
    report = verify_schema(tmp_path, recorded_for(tmp_path))
    assert report.problems == []
    assert (report.records, report.reproduced, report.restored, report.loaded) == (8, 8, 8, 8)
    assert (report.schemes, report.concepts, report.seeded) == (5, 7, 5)
    measured = report.measured
    assert measured["records"] == 8
    assert measured["records_by_kind"] == {
        "claim": 1,
        "dataset": 1,
        "evaluation": 1,
        "model": 2,
        "protocol": 1,
        "result": 1,
        "source": 1,
    }
    assert measured["source_ids"] == 2
    assert measured["links"] == 6
    assert measured["links_by_relation"] == {
        "assessment": 1,
        "data": 1,
        "evaluation": 1,
        "family": 1,
        "subject": 1,
        "system": 1,
    }
    # The excluded claim is the one record a public release may not hold.
    assert measured["public_records"] == 7
    assert measured["controlled_values"] == 11
    assert measured["controlled_values_by_field"] == {
        "attributes.metric metric": 1,
        "attributes.review.method review-method": 1,
        "facets.areas area": 1,
        "status status": 8,
    }
    schemes = measured["schemes"]
    assert isinstance(schemes, dict)
    status = schemes["status"]
    assert isinstance(status, dict)
    assert status["concepts"] == [
        ["discovered", "discovered"],
        ["source_checked", "source_checked"],
        ["excluded", "excluded"],
    ]
    assert status["quads"] == 10
    assert status["blank_nodes"] == 0
    assert status["quads_sha256"] != status["quads_sorted_sha256"]


def test_what_the_typescript_recorded_differently_is_named(tmp_path: Path) -> None:
    build(tmp_path)
    recorded = recorded_for(tmp_path)
    schemes = recorded["schemes"]
    assert isinstance(schemes, dict)
    metric = schemes["metric"]
    assert isinstance(metric, dict)
    changed: JsonObject = {
        **recorded,
        "links": 7,
        "public_ids_sha256": "0" * 64,
        "schemes": {**schemes, "metric": {**metric, "concepts": [["auprc", "AUPRC"]], "quads": 99}},
    }
    del changed["source_ids"]
    problems = verify_schema(tmp_path, changed).problems
    digest, counts = recorded["public_ids_sha256"], "1 items measured and 1 recorded by the TypeScript"
    assert problems == [
        "source_ids: measured 2, the TypeScript recorded null",
        "links: measured 6, the TypeScript recorded 7",
        f'public_ids_sha256: measured "{digest}", the TypeScript recorded "{"0" * 64}"',
        f"schemes.metric.concepts: {counts}, first differing at position 0",
        "schemes.metric.quads: measured 4, the TypeScript recorded 99",
    ]


def test_differences_are_named_by_path() -> None:
    assert differences({"a": 1, "b": {"c": [1, 2]}}, {"a": 1, "b": {"c": [1, 2]}}, "x") == []
    assert differences({"a": 1}, {"b": 2}, "x") == [
        "x.a: measured 1, and the TypeScript recorded nothing",
        "x.b: not measured, and the TypeScript recorded 2",
    ]
    assert differences([1, 2, 3], [1, 2], "x") == [
        "x: 3 items measured and 2 recorded by the TypeScript, first differing at position 2"
    ]
    assert differences([1, 9, 3], [1, 2, 3], "x") == [
        "x: 3 items measured and 3 recorded by the TypeScript, first differing at position 1"
    ]
    assert differences([1], {"a": 1}, "x") == ['x: measured [1], the TypeScript recorded {"a":1}']
    long = differences("a" * 300, "b", "x")
    assert long == [f'x: measured "{"a" * 119}..., the TypeScript recorded "b"']


def test_a_line_the_schema_would_write_differently_is_a_problem(tmp_path: Path) -> None:
    build(tmp_path)
    recorded = recorded_for(tmp_path)
    file = record_file("dataset", "dataset-a", tmp_path)
    row = RECORDS[3]

    # Keys out of order: the TypeScript loads it, and it is still not the line the schema writes.
    write_text(file, join_lines([js_stringify(dict(reversed(row.items())))]))
    report = verify_schema(tmp_path, recorded)
    assert report.problems == [
        "data/entities/datasets.jsonl:1 is accepted but is not written back as the line it was read from",
        "data/entities/datasets.jsonl:1 is not restored once its keys are put in another order",
    ]
    # The loader follows the TypeScript, which hashes the record the schema makes of the line.
    assert (report.records, report.reproduced, report.restored, report.loaded) == (8, 7, 7, 8)

    # Not a record at all.
    write_text(file, '{"id":"dataset-a","kind":"dataset"}\n')
    report = verify_schema(tmp_path, recorded)
    assert report.problems[0].startswith(
        "data/entities/datasets.jsonl:1 is not a record: invalid_type:name: Invalid input: expected string"
    )
    assert "Dangling eval-a -> dataset-a" in report.problems[1]
    assert report.problems[-1].startswith("load_records refuses the store: ")
    assert "datasets.jsonl:1 is not a record: " in report.problems[-1]
    assert (report.records, report.reproduced, report.loaded) == (8, 7, 0)

    # A record JavaScript would write with null in it.
    write_text(file, js_stringify(row).replace('"attributes":{}', '"attributes":{"n":1e400}') + "\n")
    report = verify_schema(tmp_path, recorded)
    refusal = "JSON.stringify writes null for inf; it is not a number a record may hold"
    assert report.problems[0] == f"data/entities/datasets.jsonl:1 cannot be written: {refusal}"
    assert [problem.partition(": ")[0] for problem in report.problems] == [
        "data/entities/datasets.jsonl:1 cannot be written",
        "load_records refuses the store",
    ]


def test_a_parser_that_reorders_nothing_is_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Every stored record is already in schema order, so writing each back unchanged proves
    # nothing about ordering. This is the parser that check exists to catch: it accepts what
    # the real one accepts and hands back the keys in the order they came.
    build(tmp_path)

    class AsGiven:
        def __init__(self, row: JsonObject) -> None:
            self.given = row

        def row(self) -> JsonObject:
            return self.given

    def lazy(value: object) -> Checked:
        checked = check_record(value)
        assert isinstance(value, dict)
        return Checked(AsGiven(value), ()) if checked.record else checked  # type: ignore[arg-type]

    monkeypatch.setattr(conformance, "check_record", lazy)
    report = SchemaReport()
    conformance.read_store(tmp_path, report)
    assert (report.records, report.reproduced, report.restored) == (8, 8, 0)
    assert len(report.problems) == 8
    assert report.problems[0].endswith(":1 is not restored once its keys are put in another order")


def test_moving_keys_reverses_the_top_level_and_each_link() -> None:
    row = record("m", "model", links=[link("family", "f"), "not a link"])
    out = moved(row)
    assert list(out) == list(reversed(list(row)))
    assert out["links"] == [{"target_id": "f", "relation": "family"}, "not a link"]
    assert js_stringify(out["links"]) == '[{"target_id":"f","relation":"family"},"not a link"]'
    assert moved({"id": "m", "links": 3}) == {"links": 3, "id": "m"}
    assert row["links"] == [link("family", "f"), "not a link"]


def test_faults_in_references_and_vocabularies_are_problems(tmp_path: Path) -> None:
    extra = [
        record("model-c", "model", links=[link("family", "gone")]),
        record("model-d", "model", facets={"areas": ["genomics"]}),
    ]
    build(tmp_path, [*RECORDS, *extra])
    report = verify_schema(tmp_path, bare())
    assert report.problems[:2] == [
        "the records break a rule of the store: Dangling model-c -> gone",
        'model-d: facets.areas "genomics" is not a concept in the area vocabulary',
    ]
    # The loader stops at the first of those it meets, which is the vocabulary.
    assert report.problems[-1] == (
        "load_records refuses the store: Uncontrolled values:\n"
        '  model-d: facets.areas "genomics" is not a concept in the area vocabulary'
    )
    assert (report.reproduced, report.restored, report.loaded) == (10, 10, 0)

    # A link to nothing alone gets past the vocabularies and is refused as a reference.
    build(tmp_path / "dangling", [*RECORDS, extra[0]])
    report = verify_schema(tmp_path / "dangling", bare())
    assert report.problems[-1] == "load_records refuses the store: Dangling model-c -> gone"


def test_the_loader_must_return_the_records_the_schema_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build(tmp_path)
    records = [parse_record(row) for row in RECORDS]
    report = SchemaReport()
    check_loader(tmp_path, records, report)
    assert (report.loaded, report.problems) == (8, [])

    # Given in another order, they are still compared in ID order.
    report = SchemaReport()
    check_loader(tmp_path, list(reversed(records)), report)
    assert (report.loaded, report.problems) == (8, [])

    # A loader that drops a record, or returns them unsorted, is a problem.
    for wrong in (records[1:], records):
        monkeypatch.setattr(conformance, "load_records", lambda _root, wrong=wrong: wrong)
        report = SchemaReport()
        check_loader(tmp_path, records, report)
        assert report.loaded == 0
        assert report.problems == [
            f"load_records returns {len(wrong)} records, which are not the 8 the schema accepts in ID order"
        ]


def test_what_the_gate_sees_of_an_edited_store_and_what_it_leaves_to_the_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    build(root)
    expected = write_recorded(root, tmp_path)
    arguments = [str(root), "--expected", str(expected)]
    file = record_file("dataset", "dataset-a", root)
    committed = file.read_text("utf-8")

    # A value edited, and nothing else: still a record, no count moves, and the provenance
    # digest no longer matches. The loader refuses it, and so does the digest gate.
    edited = {**RECORDS[3], "description": "edited"}
    write_text(file, join_lines([js_stringify(edited)]))
    assert schema_gate(arguments, expected) == 1
    assert "load_records refuses the store: dataset-a changed without a provenance entry." in (
        capsys.readouterr().err
    )
    assert digests_gate([str(root)]) == 1
    capsys.readouterr()

    # The same edit with its provenance row written to match agrees with itself. Neither of
    # these two gates can know, and the layout gate's pinned bytes are what refuse it.
    provenance = provenance_file_for("dataset-a", root)
    listed = provenance.read_text("utf-8")
    write_text(provenance, listed.replace(record_sha256(RECORDS[3]), record_sha256(edited)))
    assert schema_gate(arguments, expected) == 0
    assert digests_gate([str(root)]) == 0
    capsys.readouterr()
    write_text(provenance, listed)

    # A blank line and a doubled final newline are dropped before a record is read.
    write_text(file, f"\n{committed}\n")
    assert schema_gate(arguments, expected) == 0
    assert digests_gate([str(root)]) == 0
    capsys.readouterr()

    # Two lines out of ID order are misplaced, which the loader refuses.
    models = record_file("model", "model-a", root)
    first, second = models.read_text("utf-8").splitlines()
    write_text(models, join_lines([second, first]))
    assert schema_gate(arguments, expected) == 1
    assert "load_records refuses the store: " in capsys.readouterr().err


def test_blank_nodes_in_a_vocabulary_are_named_as_the_recorder_named_them(tmp_path: Path) -> None:
    build(tmp_path)
    metric = tmp_path / VOCAB_DIR / "metric.ttl"
    write_text(metric, metric.read_text("utf-8") + f"<{scheme_iri('metric')}auprc> <a:p> [ <a:q> _:x ] .\n")
    schemes = load_schemes(tmp_path)
    fresh = scheme_facts(tmp_path, schemes, BlankLabels())
    moved_on = scheme_facts(tmp_path, schemes, BlankLabels(documents=25, anonymous=4))
    again = scheme_facts(tmp_path, schemes, BlankLabels(documents=25, anonymous=4))
    assert moved_on == again
    first, later = fresh["metric"], moved_on["metric"]
    assert isinstance(first, dict)
    assert isinstance(later, dict)
    assert (first["blank_nodes"], later["blank_nodes"]) == (2, 2)
    assert first["quads"] == later["quads"]
    # The labels are part of each line, so the digests are of the counters too.
    assert first["quads_sha256"] != later["quads_sha256"]
    assert fresh["area"] == moved_on["area"]

    recorded = recorded_for(tmp_path)
    assert verify_schema(tmp_path, recorded).problems == []
    report = verify_schema(tmp_path, {**recorded, "blank_labels": {"documents": 3, "anonymous": 0}})
    assert [problem.partition(": ")[0] for problem in report.problems] == [
        "schemes.metric.quads_sha256",
        "schemes.metric.quads_sorted_sha256",
    ]


@pytest.mark.parametrize(
    "held",
    [None, [], {}, {"documents": 1}, {"documents": "1", "anonymous": 2}, {"documents": 1, "anonymous": None}],
)
def test_an_expectation_must_say_where_the_blank_node_counters_stood(held: JsonValue) -> None:
    with pytest.raises(StoreError, match="does not say where n3's blank node counters stood"):
        recorded_labels({"blank_labels": held})
    assert recorded_labels({"blank_labels": {"documents": 3, "anonymous": 4}}) == BlankLabels(3, 4)


def test_seeded_concepts_must_be_refused_as_recorded_and_cover_the_mapping(tmp_path: Path) -> None:
    build(tmp_path)
    mapping, schemes = read_mapping(tmp_path), load_schemes(tmp_path)
    assert controlled_fields(mapping) == list(SEEDS)
    # A scheme JavaScript counts as true is a controlled field, an empty list among them.
    odd = mapping_of(
        {"fields": {"a": {"scheme": []}, "b": {"scheme": 0}}, "facets": {}, "attributes": {}, "review": {}}
    )
    assert controlled_fields(odd) == ["a", "attributes.derivation.method"]

    held = seeds()
    first = held[0]
    assert isinstance(first, dict)
    report = SchemaReport()
    check_seeded(mapping, schemes, [{**first, "error": "another message"}, *held[1:3]], report)
    assert report.seeded == 2
    assert report.problems[0].startswith("a bad concept seeded in status gives 'Uncontrolled values:")
    assert report.problems[0].endswith("where the TypeScript gave 'another message'")
    assert report.problems[1] == (
        "the seeded cases are not the mapping's controlled fields: "
        "missing ['attributes.derivation.method', 'attributes.review.method'], unknown []"
    )

    # A seed the store's vocabulary accepts is a problem too: nothing was refused.
    accepted = {**first, "record": '{"id":"seeded","facets":{},"attributes":{},"status":"discovered"}'}
    report = SchemaReport()
    check_seeded(mapping, schemes, [accepted, *held[1:], {**first, "field": "attributes.unknown"}], report)
    assert report.problems[0].startswith("a bad concept seeded in status gives None")
    assert report.problems[1].endswith("missing [], unknown ['attributes.unknown']")


@pytest.mark.parametrize(
    ("recorded", "message"),
    [
        (None, "the recorded expectation has no list 'seeded'"),
        (["seed"], "seeded case 1 is not a JSON object"),
        (
            [{"field": "status", "record": "{}"}],
            "seeded case 1 needs text for its field, its record and its error",
        ),
        ([{"field": 1, "record": "{}", "error": "x"}], "seeded case 1 needs text"),
        ([{"field": "status", "record": 2, "error": "x"}], "seeded case 1 needs text"),
        ([{"field": "status", "record": "[", "error": "x"}], "seeded case 1 is not JSON"),
    ],
)
def test_seeded_cases_of_the_wrong_shape_stop_the_run(
    tmp_path: Path, recorded: JsonValue, message: str
) -> None:
    build(tmp_path)
    with pytest.raises(StoreError, match=message):
        check_seeded(read_mapping(tmp_path), load_schemes(tmp_path), recorded, SchemaReport())


def test_the_recorded_expectation_is_read_or_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / "recorded.json"
    with pytest.raises(StoreError, match=r"recorded\.json cannot be read"):
        read_recorded(path)
    path.write_text("{", "utf-8")
    with pytest.raises(StoreError, match=r"recorded\.json is not JSON"):
        read_recorded(path)
    path.write_text("[]", "utf-8")
    with pytest.raises(StoreError, match=r"recorded\.json is not a JSON object"):
        read_recorded(path)
    path.write_text('{"recorded_from":{"store_ref":"abc123"}}', "utf-8")
    assert recorded_ref(read_recorded(path)) == "abc123"
    unnamed: list[JsonObject] = [{}, {"recorded_from": []}, {"recorded_from": {"store_ref": 1}}]
    for recorded in unnamed:
        with pytest.raises(StoreError, match="does not name the store commit"):
            recorded_ref(recorded)


# --- The gate as a command ---------------------------------------------------------------


def test_a_store_a_later_validator_refuses_is_a_problem_and_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The loader now runs every validator, and each kind of refusal has to come back as a line.
    root = tmp_path / "store"
    build(root)
    expected = write_recorded(root, tmp_path)
    profiled: list[Any] = [
        row if row["id"] != "model-b" else {**row, "attributes": {"profile": {}}} for row in RECORDS
    ]
    build(root, profiled)
    assert schema_gate([str(root)], expected) == 1
    err = capsys.readouterr().err
    assert "  load_records refuses the store: invalid_type:summary: Invalid input: expected string" in err
    undeclared: list[Any] = [
        row if row["id"] != "model-b" else {**row, "attributes": {"nope": 1}} for row in RECORDS
    ]
    build(root, undeclared)
    assert schema_gate([str(root)], expected) == 1
    assert "load_records refuses the store: 1 attribute problems:" in capsys.readouterr().err
    source: dict[str, Any] = dict(RECORDS[0])
    assert source["id"] == "source-a"
    unjudged = [{**source, "attributes": {**source["attributes"], "url": IDNA}}, *RECORDS[1:]]
    build(root, unjudged)
    assert schema_gate([str(root)], expected) == 2
    assert capsys.readouterr().err.startswith("Cannot verify: the host ")


def test_the_schema_gate_passes_the_store_it_was_recorded_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    build(root)
    expected = write_recorded(root, tmp_path)
    measured = tmp_path / "measured.json"
    assert schema_gate([str(root), "--json", str(measured)], expected) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out == (
        "expecting the store as the TypeScript measured it at abc123\n"
        "records read: 8\n"
        "  accepted and written back as the committed line: 8\n"
        "  restored to the committed line with their keys moved: 8\n"
        "  returned by load_records, in ID order: 8\n"
        "links allowed by the relation rules: 6\n"
        "source IDs naming a source: 2\n"
        "records a public release may hold: 7\n"
        "vocabularies: 5 schemes, 7 concepts\n"
        "controlled values, each a concept of its scheme: 11\n"
        "seeded bad concepts refused as the TypeScript refuses them: 5\n"
        "\nAll 8 records are accepted by the schema and written back byte for byte.\n"
    )
    report = json.loads(measured.read_text("utf-8"))
    assert report["store_ref"] == "abc123"
    assert (report["records_read"], report["records_reproduced"], report["records_restored"]) == (8, 8, 8)
    assert report["records_loaded"] == 8
    assert (report["schemes"], report["concepts"], report["seeded_refused"]) == (5, 7, 5)
    assert report["problems"] == []
    assert report["measured"]["links"] == 6


def test_the_schema_gate_fails_a_thinner_and_an_edited_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    build(root)
    expected = write_recorded(root, tmp_path)
    arguments = [str(root), "--expected", str(expected)]

    # A record gone, with everything that pointed at nothing else still valid: every check
    # the store can make of itself passes, and the recorded counts do not.
    thinner = [row for row in RECORDS if row["id"] != "claim-a"]
    claims = record_file("claim", "claim-a", root)
    claims.unlink()
    measured = tmp_path / "measured.json"
    assert schema_gate([*arguments, "--json", str(measured)], tmp_path / "unused.json") == 1
    out = capsys.readouterr()
    assert "records: measured 7, the TypeScript recorded 8" in out.err
    assert "problems. The store does not conform." in out.err
    assert "records are accepted" not in out.out
    assert "records read: 7\n" in out.out
    assert any(
        problem.startswith("links: measured 5")
        for problem in json.loads(measured.read_text("utf-8"))["problems"]
    )
    assert len(thinner) == 7

    # A concept gone from a vocabulary no record uses.
    write_text(claims, join_lines([js_stringify(RECORDS[-1])]))
    assert schema_gate(arguments, tmp_path / "unused.json") == 0
    capsys.readouterr()
    scheme = root / VOCAB_DIR / "derivation-method.ttl"
    text = scheme.read_text("utf-8")
    write_text(scheme, text.replace('"difference"@en', '"Difference"@en'))
    assert schema_gate(arguments, tmp_path / "unused.json") == 1
    out = capsys.readouterr()
    assert "schemes.derivation-method.concepts: 1 items measured and 1 recorded" in out.err
    assert "schemes.derivation-method.quads_sha256: measured " in out.err

    # More problems than are shown in full: the count is of all of them.
    for kind in ("model", "dataset", "protocol", "source"):
        write_text(record_file(kind, "x", root), "\n".join(["{}"] * 8) + "\n")
    assert schema_gate(arguments, tmp_path / "unused.json") == 1
    out = capsys.readouterr()
    assert len([line for line in out.err.split("\n") if line.startswith("  ")]) == 20
    assert " problems. The store does not conform." in out.err


def test_the_schema_gate_exits_2_when_the_run_cannot_be_made(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    build(root)
    expected = write_recorded(root, tmp_path)
    assert schema_gate([str(root)], tmp_path / "missing.json") == 2
    assert capsys.readouterr().err.startswith("Cannot verify: ")

    # No mapping: the checkout is not a store, or not all of one.
    assert schema_gate([str(tmp_path / "nowhere"), "--expected", str(expected)], expected) == 2
    assert "mapping.json cannot be read" in capsys.readouterr().err

    # A vocabulary that does not load: n3's words, and then the file they are about.
    write_text(root / VOCAB_DIR / "broken.ttl", "not turtle")
    assert schema_gate([str(root)], expected) == 2
    assert capsys.readouterr().err == 'Cannot verify: Unexpected "not" on line 1. (in broken.ttl)\n'
    # Text that begins nothing in Turtle, which once left the gate with a traceback.
    write_text(root / VOCAB_DIR / "broken.ttl", '?x <a:p> "v" .\n')
    assert schema_gate([str(root)], expected) == 2
    assert capsys.readouterr().err == 'Cannot verify: Unexpected "?x" on line 1. (in broken.ttl)\n'
    (root / VOCAB_DIR / "broken.ttl").unlink()

    # An expectation that does not say where n3's counters stood.
    unlabelled = tmp_path / "unlabelled.json"
    held = json.loads(expected.read_text("utf-8"))
    del held["blank_labels"]
    unlabelled.write_text(json.dumps(held), "utf-8")
    assert schema_gate([str(root)], unlabelled) == 2
    assert "does not say where n3's blank node counters stood" in capsys.readouterr().err

    # A store with a mapping and vocabularies and no records proves nothing.
    empty = tmp_path / "empty"
    build(empty, [])
    assert schema_gate([str(empty)], write_recorded(empty, tmp_path)) == 2
    out = capsys.readouterr()
    assert out.err.endswith("Cannot verify: the record files hold no records\n")


def test_the_schema_gate_exits_2_for_what_cannot_be_opened_or_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    build(root)
    expected = write_recorded(root, tmp_path)

    # A folder where a scheme file belongs.
    (root / VOCAB_DIR / "zz.ttl").mkdir()
    assert schema_gate([str(root)], expected) == 2
    assert capsys.readouterr().err.startswith("Cannot verify: zz.ttl cannot be read: ")
    (root / VOCAB_DIR / "zz.ttl").rmdir()

    # A record file that cannot be opened: a link to itself, as a file nobody may read is.
    models = record_file("model", "model-a", root)
    kept = models.read_bytes()
    models.unlink()
    models.symlink_to(models)
    assert schema_gate([str(root)], expected) == 2
    assert capsys.readouterr().err.startswith(f"Cannot verify: {models} cannot be read: ")
    models.unlink()
    models.write_bytes(kept)

    # The provenance, which only the loader uses: one shard that cannot be opened, and one
    # that is not JSON. Neither is the loader refusing the store.
    shard = provenance_file_for("dataset-a", root)
    listed = shard.read_bytes()
    shard.unlink()
    shard.symlink_to(shard)
    assert schema_gate([str(root)], expected) == 2
    assert capsys.readouterr().err.startswith(f"Cannot verify: {shard} cannot be read: ")
    shard.unlink()
    shard.write_text("{\n", "utf-8")
    assert schema_gate([str(root)], expected) == 2
    assert capsys.readouterr().err.startswith(f"Cannot verify: {shard}:1 is not JSON: ")
    shard.write_bytes(listed)
    assert schema_gate([str(root)], expected) == 0
    capsys.readouterr()

    # A report that cannot be written, after a run that found nothing wrong.
    assert schema_gate([str(root), "--json", str(tmp_path / "no-folder" / "report.json")], expected) == 2
    out = capsys.readouterr()
    assert "seeded bad concepts refused" in out.out
    assert "records are accepted" not in out.out
    assert out.err.startswith("Cannot verify: [Errno 2] No such file or directory: ")


def test_the_script_is_the_gate(tmp_path: Path) -> None:
    root = tmp_path / "store"
    build(root)
    expected = write_recorded(root, tmp_path)
    # The interpreter running the tests and a script in this repository: nothing untrusted.
    done = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPTS / "verify_store_schema.py"), str(root), "--expected", str(expected)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.endswith(
        "\nAll 8 records are accepted by the schema and written back byte for byte.\n"
    )
    assert provenance_of(RECORDS)[0]["added_by"] == "records:add"
