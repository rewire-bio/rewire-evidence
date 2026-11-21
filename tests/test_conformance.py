"""What the schema gate measures, and that it notices each thing it exists to notice."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rewire_evidence import conformance
from rewire_evidence.canonical import JsonObject, JsonValue, js_stringify
from rewire_evidence.conformance import (
    SchemaReport,
    check_seeded,
    controlled_fields,
    differences,
    moved,
    read_recorded,
    recorded_ref,
    verify_schema,
)
from rewire_evidence.gates import schema_gate
from rewire_evidence.jsonl import StoreError, join_lines, write_text
from rewire_evidence.schema import Checked, check_record
from rewire_evidence.store import record_file
from rewire_evidence.vocab import VOCAB_DIR, load_schemes, read_mapping
from tests.test_records import RECORDS, build, link, provenance_of, record

SCRIPTS = Path(__file__).parent.parent / "scripts"
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
    blank: JsonObject = {"recorded_from": {"store_ref": "abc123"}, "seeded": seeds()}
    report = verify_schema(root, blank)
    return {**blank, **report.measured}


def write_recorded(root: Path, folder: Path) -> Path:
    path = folder / "recorded.json"
    path.write_text(json.dumps(recorded_for(root)), encoding="utf-8")
    return path


def test_a_store_that_conforms_is_measured_in_full(tmp_path: Path) -> None:
    build(tmp_path)
    report = verify_schema(tmp_path, recorded_for(tmp_path))
    assert report.problems == []
    assert (report.records, report.reproduced, report.restored) == (8, 8, 8)
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
    assert measured["source_ids"] == 1
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
        "source_ids: measured 1, the TypeScript recorded null",
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
    assert (report.records, report.reproduced, report.restored) == (8, 7, 7)

    # Not a record at all.
    write_text(file, '{"id":"dataset-a","kind":"dataset"}\n')
    report = verify_schema(tmp_path, recorded)
    assert report.problems[0].startswith(
        "data/entities/datasets.jsonl:1 is not a record: invalid_type:name: Invalid input: expected string"
    )
    assert "Dangling eval-a -> dataset-a" in report.problems[1]
    assert (report.records, report.reproduced) == (8, 7)

    # A record JavaScript would write with null in it.
    write_text(file, js_stringify(row).replace('"attributes":{}', '"attributes":{"n":1e400}') + "\n")
    report = verify_schema(tmp_path, recorded)
    refusal = "JSON.stringify writes null for inf; it is not a number a record may hold"
    assert report.problems == [f"data/entities/datasets.jsonl:1 cannot be written: {refusal}"]


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
    report = verify_schema(tmp_path, {"recorded_from": {"store_ref": "abc123"}, "seeded": seeds()})
    assert report.problems[:2] == [
        "the records break a rule of the store: Dangling model-c -> gone",
        'model-d: facets.areas "genomics" is not a concept in the area vocabulary',
    ]
    assert (report.reproduced, report.restored) == (10, 10)


def test_seeded_concepts_must_be_refused_as_recorded_and_cover_the_mapping(tmp_path: Path) -> None:
    build(tmp_path)
    mapping, schemes = read_mapping(tmp_path), load_schemes(tmp_path)
    assert controlled_fields(mapping) == list(SEEDS)

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
        "links allowed by the relation rules: 6\n"
        "source IDs naming a source: 1\n"
        "records a public release may hold: 7\n"
        "vocabularies: 5 schemes, 7 concepts\n"
        "controlled values, each a concept of its scheme: 11\n"
        "seeded bad concepts refused as the TypeScript refuses them: 5\n"
        "\nAll 8 records are accepted by the schema and written back byte for byte.\n"
    )
    report = json.loads(measured.read_text("utf-8"))
    assert report["store_ref"] == "abc123"
    assert (report["records_read"], report["records_reproduced"], report["records_restored"]) == (8, 8, 8)
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

    # A vocabulary that does not load.
    write_text(root / VOCAB_DIR / "broken.ttl", "not turtle")
    assert schema_gate([str(root)], expected) == 2
    assert "Cannot verify: broken.ttl is not Turtle" in capsys.readouterr().err
    (root / VOCAB_DIR / "broken.ttl").unlink()

    # A store with a mapping and vocabularies and no records proves nothing.
    empty = tmp_path / "empty"
    build(empty, [])
    assert schema_gate([str(empty)], write_recorded(empty, tmp_path)) == 2
    out = capsys.readouterr()
    assert out.err.endswith("Cannot verify: the record files hold no records\n")


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
