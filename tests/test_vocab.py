"""The vocabulary loader and the controlled-value check, held to what the TypeScript does.

`tests/fixtures/vocabularies.json` was written by `scripts/fixtures/record_schema_fixture.mjs`
from `loadSchemes`, `controlledValues` and `validateVocabularies` in the data repository,
reading small vocabularies the script writes for the purpose. CI records it again on every
run. The real vocabularies are held to the same code by the schema gate.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence.canonical import JsonObject, js_parse, js_stringify, sha256_hex
from rewire_evidence.jsonl import StoreError
from rewire_evidence.turtle import BlankLabels
from rewire_evidence.vocab import (
    MAPPING_FILE,
    VOCAB_BASE,
    VOCAB_DIR,
    Concept,
    ControlledFields,
    Scheme,
    VocabularyError,
    _loops,
    controlled_values,
    decode,
    load_schemes,
    mapping_of,
    read_mapping,
    read_scheme_file,
    read_turtle,
    scheme_files,
    scheme_iri,
    validate_vocabularies,
    vocabulary_problems,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE: dict[str, Any] = json.loads((FIXTURES / "vocabularies.json").read_text("utf-8"))
TURTLE: dict[str, Any] = json.loads((FIXTURES / "turtle.json").read_text("utf-8"))
MAPPING = mapping_of(FIXTURE["mapping"])


def named(cases: list[dict[str, Any]]) -> list[Any]:
    return [pytest.param(case, id=case["name"]) for case in cases]


def write_vocabulary(
    root: Path,
    files: dict[str, str | dict[str, str] | None] | None,
    *,
    vocab_is_file: bool = False,
) -> Path:
    """Lay out a `data/vocab` as a fixture case describes it.

    A name maps to its text, to `{"hex": ...}` for bytes that are not text, or to None for
    a folder of that name. No files at all is no vocabulary, or a file where it belongs.
    """
    folder = root / VOCAB_DIR
    if vocab_is_file:
        folder.parent.mkdir(parents=True)
        folder.write_bytes(b"a file where the folder belongs\n")
    if files is None:
        return root
    folder.mkdir(parents=True)
    for name, body in files.items():
        if body is None:
            (folder / name).mkdir()
        elif isinstance(body, str):
            (folder / name).write_bytes(body.encode("utf-8"))
        else:
            (folder / name).write_bytes(bytes.fromhex(body["hex"]))
    return root


def laid_out(case: dict[str, Any], root: Path) -> Path:
    return write_vocabulary(root, case["files"], vocab_is_file=case["vocab_is_file"])


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
    assert len(FIXTURE["load_schemes"]) == 67
    assert len(FIXTURE["controlled"]) == 36
    assert len(FIXTURE["chains"]) == 2
    assert FIXTURE["recorded_from"]["n3"] == "1.17.4"
    # All but the two refusals the operating system words are compared word for word.
    assert [case["error"] for case in FIXTURE["load_schemes"] if not case["message_comparable"]] == [
        "EISDIR",
        "ENOTDIR",
    ]


@pytest.mark.parametrize("case", named([case for case in FIXTURE["load_schemes"] if case["error"] is None]))
def test_schemes_load_as_the_typescript_loads_them(case: dict[str, Any], tmp_path: Path) -> None:
    loaded = load_schemes(laid_out(case, tmp_path), BlankLabels(**case["labels"]))
    assert dumped(loaded) == case["schemes"]
    assert list(loaded) == list(case["schemes"])


@pytest.mark.parametrize(
    "case", named([case for case in FIXTURE["load_schemes"] if case["error"] is not None])
)
def test_a_vocabulary_is_refused_as_the_typescript_refuses_it(case: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(VocabularyError) as refused:
        load_schemes(laid_out(case, tmp_path), BlankLabels(**case["labels"]))
    if case["message_comparable"]:
        assert str(refused.value) == case["error"]
    else:
        # Node and Python each word what the operating system said. Both refuse.
        assert " cannot be read: " in str(refused.value)


@pytest.mark.parametrize("case", FIXTURE["chains"], ids=["a chain", "a chain closed into a loop"])
def test_a_chain_deeper_than_python_recurses_is_walked(case: dict[str, Any], tmp_path: Path) -> None:
    metric = scheme_iri("metric")
    text = (
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
        "@prefix rb: <https://benchmarks.rewire.it/vocab#> .\n"
        f"@prefix : <{metric}> .\n<{metric}> a skos:ConceptScheme .\n"
    )
    last = case["length"] - 1
    for index in range(case["length"]):
        broader = (
            f" ; skos:broader :c{index + 1}"
            if index < last
            else " ; skos:broader :c0"
            if case["loop"]
            else ""
        )
        text += f':c{index} skos:inScheme <{metric}> ; skos:prefLabel "{index}"{broader} .\n'
    # The document is written here by the recorder's rule, and is the one it recorded.
    assert sha256_hex(text) == case["text_sha256"]
    root = write_vocabulary(tmp_path, {"metric.ttl": text})
    if case["error"] is None:
        assert len(load_schemes(root)["metric"].concepts) == case["concepts"] == case["length"]
    else:
        with pytest.raises(VocabularyError) as refused:
            load_schemes(root)
        assert str(refused.value) == case["error"]


def test_a_refusal_names_a_blank_node_as_a_fresh_process_would() -> None:
    # The recorder had parsed other documents first, so its labels start where its counters
    # stood. `records check` is a process that has parsed nothing, and that is the default.
    (case,) = [
        case
        for case in FIXTURE["load_schemes"]
        if case["name"] == "a labelled blank node placed in the scheme, in the second file"
    ]
    assert f"<b{case['labels']['documents'] + 1}_q>" in case["error"]


def test_blank_nodes_are_numbered_from_nothing_by_default(tmp_path: Path) -> None:
    metric = scheme_iri("metric")
    head = f"@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n<{metric}> a skos:ConceptScheme .\n"
    root = write_vocabulary(
        tmp_path,
        {"alpha.ttl": "", "metric.ttl": f'{head}[] skos:note "first" .\n_:q skos:inScheme <{metric}> .\n'},
    )
    message = f"metric.ttl: concept <b1_q> is not <{metric}key> with a lowercase key"
    with pytest.raises(VocabularyError) as refused:
        load_schemes(root)
    assert (
        str(refused.value)
        == "alpha.ttl does not declare the concept scheme <https://benchmarks.rewire.it/vocab/alpha/>"
    )
    (root / VOCAB_DIR / "alpha.ttl").unlink()
    (root / VOCAB_DIR / "a.txt").write_text("not a scheme file", "utf-8")
    for _ in range(2):
        with pytest.raises(VocabularyError) as refused:
            load_schemes(root)
        assert str(refused.value) == message.replace("b1_q", "b0_q")
    statements = read_scheme_file(root / VOCAB_DIR / "metric.ttl")
    assert [statement.subject for statement in statements[1:]] == ["n3-0", "b0_q"]


@pytest.mark.parametrize(
    "case", TURTLE["decoding"], ids=[case["hex"] or "empty" for case in TURTLE["decoding"]]
)
def test_bytes_are_decoded_as_node_decodes_them(case: dict[str, str]) -> None:
    assert decode(bytes.fromhex(case["hex"])) == case["text"]


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


def test_no_vocabulary_folder_is_no_schemes_and_a_file_in_its_place_is_refused(tmp_path: Path) -> None:
    assert scheme_files(tmp_path) == []
    assert load_schemes(tmp_path) == {}
    write_vocabulary(tmp_path, None, vocab_is_file=True)
    with pytest.raises(VocabularyError, match=r"^data/vocab cannot be read: .*Not a directory"):
        scheme_files(tmp_path)


def test_a_scheme_file_that_cannot_be_opened_is_refused_by_name(tmp_path: Path) -> None:
    root = write_vocabulary(tmp_path, {"metric.ttl": None})
    with pytest.raises(VocabularyError, match=r"^metric\.ttl cannot be read: .*Is a directory"):
        load_schemes(root)
    (root / VOCAB_DIR / "metric.ttl").rmdir()
    file = root / VOCAB_DIR / "metric.ttl"
    file.write_text("", "utf-8")
    file.chmod(0)
    if os.access(file, os.R_OK):
        pytest.skip("this user reads files whatever their mode")
    with pytest.raises(VocabularyError, match=r"^metric\.ttl cannot be read: .*Permission denied"):
        load_schemes(root)


def test_a_refusal_to_read_turtle_is_n3s_and_notes_the_file() -> None:
    with pytest.raises(VocabularyError) as refused:
        read_turtle("<a:s> <a:p> .\nnot turtle", "metric.ttl")
    assert str(refused.value) == 'Unexpected "not" on line 2.'
    assert refused.value.__notes__ == ["in metric.ttl"]


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
    assert statements[4].line == 'BlankNode n3-0\thttps://example.org/r\tLiteral "blank"  \n'
    assert statements[5].line == "NamedNode https://example.org/a\thttps://example.org/q\tBlankNode n3-0\n"
    counted = read_turtle("_:x <a:p> [] .", "here", BlankLabels(documents=7, anonymous=3))
    assert counted[0].line == "BlankNode b7_x\ta:p\tBlankNode n3-3\n"


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
        ({"facets": None}, "^here has no 'facets'$"),
        ({"fields": {"status": None}}, r"^here: fields\.status is null$"),
        ({"review": [{"scheme": "agent"}, None]}, r"^here: review\.1 is null$"),
    ],
)
def test_a_mapping_javascript_cannot_read_is_refused(change: JsonObject, message: str) -> None:
    # The TypeScript throws a TypeError for each of these at the first record it checks.
    with pytest.raises(VocabularyError, match=message):
        mapping_of({**FIXTURE["mapping"], **change}, "here")
    missing = {name: held for name, held in FIXTURE["mapping"].items() if name != "review"}
    with pytest.raises(VocabularyError, match=r"^here has no 'review'$"):
        mapping_of(missing, "here")


def test_a_mapping_of_any_other_shape_is_read_as_javascript_reads_it() -> None:
    # Recorded in full among the controlled cases. This is the shape those are read into.
    mapping = mapping_of(
        {
            "fields": [{"scheme": "status"}, 5],
            "facets": "areas",
            "attributes": {"a": {"scheme": 5}, "b": {"scheme": 0}, "c": "metric", "d": {"scheme": []}},
            "review": 7,
        }
    )
    assert mapping == ControlledFields(
        {"0": "status", "1": None}, {}, {"a": 5, "b": None, "c": None, "d": []}, {}
    )
    row: JsonObject = {"id": "r", "0": "x", "facets": {}, "attributes": {"a": "x", "d": "x"}}
    assert vocabulary_problems(mapping, [row], {}) == [
        '0 uses scheme "status", which has no data/vocab/status.ttl',
        'attributes.a uses scheme "5", which has no data/vocab/5.ttl',
        'attributes.d uses scheme "", which has no data/vocab/.ttl',
    ]


def test_a_scheme_javascript_cannot_print_stops_the_check() -> None:
    mapping = mapping_of(
        {"fields": {}, "facets": {}, "attributes": {"a": {"scheme": {"toString": 1}}}, "review": {}}
    )
    row: JsonObject = {"id": "r", "facets": {}, "attributes": {"a": "x"}}
    with pytest.raises(VocabularyError, match=r"^the scheme of attributes\.a has no name: Cannot convert"):
        vocabulary_problems(mapping, [row], {})


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


def loops_by_recursion(parents: Mapping[str, Mapping[str, None]]) -> list[str]:
    """The search for loops as the TypeScript writes it: a function that calls itself."""
    problems: list[str] = []
    state: dict[str, str] = {}

    def visit(node: str, trail: list[str]) -> None:
        if state.get(node) == "done":
            return
        if state.get(node) == "open":
            loop = [*trail[trail.index(node) :], node]
            problems.append(f"skos:broader cycle: {', then '.join(iri[len(VOCAB_BASE) :] for iri in loop)}")
            return
        state[node] = "open"
        for parent in parents.get(node, {}):
            visit(parent, [*trail, node])
        state[node] = "done"

    for node in parents:
        visit(node, [])
    return problems


nodes = st.integers(0, 7).map(lambda number: f"{VOCAB_BASE}m/c{number}")
graphs = st.dictionaries(nodes, st.lists(nodes, max_size=4).map(dict.fromkeys), max_size=8)


@given(graphs)
def test_loops_are_found_as_the_recursive_search_finds_them(parents: dict[str, dict[str, None]]) -> None:
    assert _loops(parents) == loops_by_recursion(parents)


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
