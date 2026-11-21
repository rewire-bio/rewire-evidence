"""The vocabulary loader and the controlled-value check, held to what the TypeScript does.

`tests/fixtures/vocabularies.json` was written by `scripts/fixtures/record_schema_fixture.mjs`
from `loadSchemes`, `controlledValues` and `validateVocabularies` in the data repository,
reading small vocabularies the script writes for the purpose. CI records it again on every
run. The real vocabularies are held to the same code by the schema gate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence.canonical import JsonObject, js_parse, js_stringify
from rewire_evidence.jsonl import StoreError
from rewire_evidence.vocab import (
    MAPPING_FILE,
    VOCAB_DIR,
    Concept,
    ControlledFields,
    Scheme,
    VocabularyError,
    controlled_values,
    load_schemes,
    mapping_of,
    read_mapping,
    read_turtle,
    scheme_files,
    scheme_iri,
    validate_vocabularies,
    vocabulary_problems,
)

FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "vocabularies.json").read_text("utf-8")
)
MAPPING = mapping_of(FIXTURE["mapping"])


def named(cases: list[dict[str, Any]]) -> list[Any]:
    return [pytest.param(case, id=case["name"]) for case in cases]


def write_vocabulary(root: Path, files: dict[str, str | None] | None) -> Path:
    """Lay out a `data/vocab` as a fixture case describes it. None is a folder, or no vocabulary."""
    if files is None:
        return root
    folder = root / VOCAB_DIR
    folder.mkdir(parents=True)
    for name, body in files.items():
        if body is None:
            (folder / name).mkdir()
        else:
            (folder / name).write_bytes(body.encode("utf-8"))
    return root


def dumped(schemes: dict[str, Scheme]) -> dict[str, Any]:
    return {
        name: {
            "iri": scheme.iri,
            "concepts": [[concept.key, concept.iri, concept.label] for concept in scheme.concepts.values()],
        }
        for name, scheme in schemes.items()
    }


@pytest.fixture(scope="module")
def schemes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Scheme]:
    return load_schemes(write_vocabulary(tmp_path_factory.mktemp("vocabulary"), FIXTURE["vocabulary"]))


def test_the_fixture_is_the_size_it_is_meant_to_be() -> None:
    assert len(FIXTURE["load_schemes"]) == 40
    assert len(FIXTURE["controlled"]) >= 31
    assert FIXTURE["recorded_from"]["n3"] == "1.17.4"


@pytest.mark.parametrize("case", named([case for case in FIXTURE["load_schemes"] if case["error"] is None]))
def test_schemes_load_as_the_typescript_loads_them(case: dict[str, Any], tmp_path: Path) -> None:
    loaded = load_schemes(write_vocabulary(tmp_path, case["files"]))
    assert dumped(loaded) == case["schemes"]
    assert list(loaded) == list(case["schemes"])


@pytest.mark.parametrize(
    "case", named([case for case in FIXTURE["load_schemes"] if case["error"] is not None])
)
def test_a_vocabulary_is_refused_as_the_typescript_refuses_it(case: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(VocabularyError) as refused:
        load_schemes(write_vocabulary(tmp_path, case["files"]))
    if case["message_comparable"]:
        assert str(refused.value) == case["error"]
    else:
        # The message is the Turtle parser's own. The file is named and the cause is a parser's.
        assert str(refused.value).startswith("metric.ttl is not Turtle: ")


def test_the_refusals_cover_every_message_the_loader_has() -> None:
    errors = [
        case["error"] for case in FIXTURE["load_schemes"] if case["error"] and case["message_comparable"]
    ]
    for part in (
        "does not declare the concept scheme",
        "with a lowercase key",
        "has no skos:prefLabel",
        ", which is not a concept",
        "but its IRI is in",
        "skos:broader cycle: ",
    ):
        assert any(part in error for error in errors), part
    # Only the first twenty problems are listed.
    (many,) = [case for case in FIXTURE["load_schemes"] if case["name"] == "more than twenty problems"]
    assert many["error"].count("\n  ") == 20


def test_the_small_vocabulary_loads_as_recorded(schemes: dict[str, Scheme]) -> None:
    assert dumped(schemes) == FIXTURE["schemes"]
    assert schemes["metric"].concepts["auprc"] == Concept(
        "auprc", f"{scheme_iri('metric')}auprc", "auprc label"
    )


@pytest.mark.parametrize("case", named(FIXTURE["controlled"]))
def test_controlled_values_are_found_and_refused_as_the_typescript_does(
    case: dict[str, Any], schemes: dict[str, Scheme]
) -> None:
    mapping = MAPPING if case["mapping"] is None else mapping_of(case["mapping"])
    rows = js_parse(case["input"])
    assert isinstance(rows, list)
    found = [
        [[value.field, value.scheme, shown(value.value)] for value in controlled_values(mapping, row)]  # type: ignore[arg-type]
        for row in rows
    ]
    assert found == case["values"]
    if case["error"] is None:
        validate_vocabularies(mapping, rows, schemes)  # type: ignore[arg-type]
        assert vocabulary_problems(mapping, rows, schemes) == []  # type: ignore[arg-type]
    else:
        with pytest.raises(VocabularyError) as refused:
            validate_vocabularies(mapping, rows, schemes)  # type: ignore[arg-type]
        assert str(refused.value) == case["error"]


def shown(value: object) -> str:
    """A value as the recorder wrote it: JSON.stringify, which writes null beyond a double."""
    text = json.dumps(value, allow_nan=True)
    return js_stringify(js_parse(text.replace("-Infinity", "null").replace("Infinity", "null")))


def test_more_than_twenty_problems_can_be_reported() -> None:
    # The TypeScript leaves a record at twenty problems and then takes one from each record
    # after it, so the limit is not a limit on the report. Kept, and recorded here.
    (case,) = [
        case for case in FIXTURE["controlled"] if case["name"] == "thirty records with two faults each"
    ]
    # Two from each of the first ten records, then one from each of the twenty after.
    assert case["error"].count("\n  ") == 40


def test_scheme_files_are_the_turtle_files_of_the_folder_in_name_order(tmp_path: Path) -> None:
    assert scheme_files(tmp_path) == []
    (tmp_path / "data").mkdir()
    (tmp_path / VOCAB_DIR).write_text("a file where the folder belongs", "utf-8")
    assert scheme_files(tmp_path) == []
    assert load_schemes(tmp_path) == {}


def test_a_scheme_file_that_is_not_utf8_is_refused_by_name(tmp_path: Path) -> None:
    write_vocabulary(tmp_path, {})
    (tmp_path / VOCAB_DIR / "metric.ttl").write_bytes(b"\xff\xfe")
    with pytest.raises(VocabularyError, match=r"metric\.ttl is not UTF-8"):
        load_schemes(tmp_path)


def test_statements_come_in_the_order_they_are_written() -> None:
    text = (
        '@prefix : <https://example.org/> .\n:b :p "two"@en , 3 , :c .\n:a :p "one" ; :q [ :r "blank" ] .\n'
    )
    statements = read_turtle(text, "here")
    assert [(statement.subject[-1], statement.object) for statement in statements[:4]] == [
        ("b", "two"),
        ("b", "3"),
        ("b", "https://example.org/c"),
        ("a", "one"),
    ]
    assert [statement.object_is_iri for statement in statements[:4]] == [False, False, True, False]
    assert [statement.has_blank_node for statement in statements] == [False, False, False, False, True, True]
    assert statements[0].line == 'NamedNode https://example.org/b\thttps://example.org/p\tLiteral "two" en \n'
    assert statements[1].line == (
        "NamedNode https://example.org/b\thttps://example.org/p\t"
        'Literal "3"  http://www.w3.org/2001/XMLSchema#integer\n'
    )
    assert statements[2].line.endswith("\tNamedNode https://example.org/c\n")
    assert statements[3].line.endswith('\tLiteral "one"  \n')
    assert "\tBlankNode " in statements[4].line or "BlankNode " in statements[4].line.split("\t")[0]
    with pytest.raises(VocabularyError, match="here is not Turtle"):
        read_turtle("not turtle", "here")


def test_a_literal_typed_as_a_string_is_a_plain_literal() -> None:
    text = (
        '<https://example.org/a> <https://example.org/p> "x"^^<http://www.w3.org/2001/XMLSchema#string> .\n'
    )
    assert read_turtle(text, "here")[0].line.endswith('\tLiteral "x"  \n')


# --- The mapping -----------------------------------------------------------------------


def test_the_mapping_is_read_from_the_checkout(tmp_path: Path) -> None:
    file = tmp_path / MAPPING_FILE
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({**FIXTURE["mapping"], "base": "ignored"}), "utf-8")
    mapping = read_mapping(tmp_path)
    assert mapping == MAPPING
    assert mapping.fields == {"name": None, "status": "status"}
    assert list(mapping.attributes) == ["url", "metric", "unit", "ghost"]
    assert mapping.review == {"date": None, "method": "review-method", "reviewer": "agent"}


def test_a_mapping_that_cannot_be_read_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(VocabularyError, match=r"data/ontology/mapping\.json cannot be read"):
        read_mapping(tmp_path)
    file = tmp_path / MAPPING_FILE
    file.parent.mkdir(parents=True)
    file.write_text("{", "utf-8")
    with pytest.raises(VocabularyError, match=r"data/ontology/mapping\.json is not JSON"):
        read_mapping(tmp_path)
    file.write_text("[]", "utf-8")
    with pytest.raises(VocabularyError, match=r"data/ontology/mapping\.json is not a JSON object"):
        read_mapping(tmp_path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"facets": None}, "here has no object 'facets'"),
        ({"review": []}, "here has no object 'review'"),
        ({"fields": {"status": "status"}}, r"here: fields\.status is not an object"),
        ({"attributes": {"metric": {"scheme": 7}}}, r"here: the scheme of attributes\.metric is not text"),
    ],
)
def test_a_mapping_of_the_wrong_shape_is_refused(change: JsonObject, message: str) -> None:
    with pytest.raises(VocabularyError, match=message):
        mapping_of({**FIXTURE["mapping"], **change}, "here")


def test_an_empty_scheme_is_no_scheme() -> None:
    mapping = mapping_of({"fields": {"status": {"scheme": ""}}, "facets": {}, "attributes": {}, "review": {}})
    assert mapping == ControlledFields({"status": None}, {}, {}, {})
    assert controlled_values(mapping, {"id": "r", "status": "x", "facets": {}, "attributes": {}}) == []


# --- Rows the TypeScript would fail on ---------------------------------------------------


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"id": "r", "attributes": {}}, "the record 'r' has no object 'facets'"),
        ({"id": "r", "facets": [], "attributes": {}}, "the record 'r' has no object 'facets'"),
        ({"id": "r", "facets": {}}, "the record 'r' has no object 'attributes'"),
        ({"facets": {}, "attributes": None}, "the record None has no object 'attributes'"),
    ],
)
def test_a_row_without_facets_or_attributes_is_refused_by_name(row: JsonObject, message: str) -> None:
    with pytest.raises(VocabularyError, match=message):
        controlled_values(MAPPING, row)


def test_a_bad_value_on_a_row_with_no_id_is_refused_as_a_row(schemes: dict[str, Scheme]) -> None:
    with pytest.raises(StoreError, match="a record has no string 'id'"):
        validate_vocabularies(MAPPING, [{"facets": {}, "attributes": {"metric": "nope"}}], schemes)


# --- Properties ------------------------------------------------------------------------

concepts = st.sampled_from(["auprc", "constructor", "1"])
strangers = st.text(max_size=6).filter(lambda text: text not in {"auprc", "constructor", "1"})


@given(st.lists(concepts | strangers, max_size=8))
def test_a_record_is_refused_exactly_when_it_holds_a_value_outside_the_scheme(values: list[str]) -> None:
    scheme = Scheme(
        "metric", scheme_iri("metric"), {key: Concept(key, key, key) for key in ("auprc", "constructor", "1")}
    )
    row: JsonObject = {"id": "r", "facets": {}, "attributes": {"metric": [*values]}}
    problems = vocabulary_problems(MAPPING, [row], {"metric": scheme})
    outside = list(dict.fromkeys(value for value in values if value not in scheme.concepts))
    assert problems == [
        f"r: attributes.metric {js_stringify(value)} is not a concept in the metric vocabulary"
        for value in outside
    ]


@given(st.lists(st.sampled_from(["dna-genomes", "proteins"]), max_size=5), st.booleans())
def test_values_are_found_one_per_item_whether_or_not_they_are_listed(
    values: list[str],
    listed: bool,  # noqa: FBT001
) -> None:
    held: list[str] | str = values if listed or not values else values[0]
    found = controlled_values(MAPPING, {"id": "r", "facets": {"areas": held}, "attributes": {}})  # type: ignore[dict-item]
    expected = values if listed or not values else values[:1]
    assert [(value.field, value.scheme, value.value) for value in found] == [
        ("facets.areas", "area", value) for value in expected
    ]
