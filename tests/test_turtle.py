"""The Turtle reader, held to n3.

`tests/fixtures/turtle.json` is what n3 1.17.4 makes of each document, recorded in CI by
`scripts/fixtures/record_schema_fixture.mjs` from the cases in
`scripts/fixtures/turtle_cases.mjs`. Every document is compared whole: the statements in
n3's order with n3's blank node labels, or the message n3 throws.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence.canonical import js_quote, sha256_hex
from rewire_evidence.turtle import (
    NULL,
    RDF,
    XSD,
    BlankLabels,
    Term,
    Token,
    Triple,
    TurtleError,
    _Context,
    _Parser,
    parse,
    tokenize,
)

FIXTURE: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures" / "turtle.json").read_text("utf-8"))


def shown(term: Term) -> str:
    """One term as the recorder writes it."""
    if term.kind == "Literal":
        return f"Literal {js_quote(term.value)} {term.language} {term.datatype}"
    return f"{term.kind} {term.value}"


def lines(triples: list[Triple]) -> list[str]:
    return [f"{shown(subject)}\t{predicate.value}\t{shown(held)}\n" for subject, predicate, held in triples]


def read(case: dict[str, Any]) -> tuple[str | None, list[str] | None]:
    """What the port makes of a recorded document: the refusal, or the statements."""
    try:
        return None, lines(parse(case["text"], BlankLabels(**case["labels"])))
    except TurtleError as error:
        return str(error), None


def test_the_fixture_is_the_size_it_is_meant_to_be() -> None:
    assert len(FIXTURE["documents"]) >= 300
    assert len(FIXTURE["mutations"]) == 600
    assert FIXTURE["recorded_from"]["n3"] == "1.17.4"
    accepted = [case for case in FIXTURE["documents"] if case["error"] is None]
    # Both outcomes are well represented, among the written cases and the damaged ones.
    assert len(accepted) >= 80
    assert len(FIXTURE["documents"]) - len(accepted) >= 200
    assert sum(case["error"] is None for case in FIXTURE["mutations"]) >= 30


@pytest.mark.parametrize("case", FIXTURE["documents"], ids=range(len(FIXTURE["documents"])))
def test_a_document_is_read_or_refused_as_n3_does(case: dict[str, Any]) -> None:
    assert read(case) == (case["error"], case["lines"])


def test_damaged_documents_are_read_or_refused_as_n3_does() -> None:
    for case in FIXTURE["mutations"]:
        error, read_lines = read(case)
        assert error == case["error"], case["text"]
        assert (None if read_lines is None else len(read_lines)) == case["statements"], case["text"]
        if read_lines is not None:
            assert sha256_hex("".join(read_lines)) == case["lines_sha256"], case["text"]


def test_the_refusals_cover_every_message_n3_has_in_turtle_mode() -> None:
    errors = [case["error"] for case in FIXTURE["documents"] if case["error"]]
    for part in (
        'Unexpected "',
        "Unexpected graph on line",
        "Unexpected graph closing on line",
        'Unexpected "@forSome" on line',
        'Unexpected "@forAll" on line',
        "Unexpected literal on line",
        "Unexpected RDF* syntax on line",
        "Unexpected asserted triple closing on line",
        "Unexpected . on line",
        "Unexpected ] on line",
        "Unexpected } on line",
        "Expected entity but got eof on line",
        "Expected predicate but got ; on line",
        "Disallowed blank node as predicate on line",
        'Expected punctuation to follow "',
        "Expected prefix to follow @prefix on line",
        'Expected IRI to follow prefix "',
        "Expected valid IRI to follow base declaration on line",
        "Expected declaration to end with a dot on line",
        "Invalid IRI on line",
        'Undefined prefix "',
        "Cannot read properties of undefined (reading 'value')",
        "Cannot read properties of null (reading 'termType')",
    ):
        assert any(part in error for error in errors), part


# --- What n3 does that a reading of the grammar would not ----------------------------------


def only(text: str) -> Triple:
    (triple,) = parse(text)
    return triple


def test_a_number_and_a_boolean_keep_the_spelling_they_were_written_in() -> None:
    held = [held for _, _, held in parse("<a:s> <a:p> 01 , 1e0 , 1.e3 , .50 , true .")]
    assert [(term.value, term.datatype.removeprefix(XSD)) for term in held] == [
        ("01", "integer"),
        ("1e0", "double"),
        ("1.e3", "double"),
        (".50", "decimal"),
        ("true", "boolean"),
    ]
    typed = only('<a:s> <a:p> "TRUE"^^<http://www.w3.org/2001/XMLSchema#boolean> .')[2]
    assert (typed.value, typed.datatype) == ("TRUE", f"{XSD}boolean")


def test_a_language_is_lowercased_and_a_string_type_is_no_type() -> None:
    assert only('<a:s> <a:p> "x"@EN-Latn-gb .')[2] == Term("Literal", "x", language="en-latn-gb")
    assert only(f'<a:s> <a:p> "x"^^<{XSD}string> .')[2] == Term("Literal", "x")


def test_an_escaped_surrogate_pair_is_one_character() -> None:
    assert only('<a:s> <a:p> "\\uD83D\\uDE00" .')[2].value == "\U0001f600"
    assert only('<a:s> <a:p> "\\uD83D" .')[2].value == "\ud83d"


def test_a_relative_iri_stays_relative_where_no_base_is_declared() -> None:
    subject, predicate, held = only("<rel> </root> <//host/path> .")
    assert (subject.value, predicate.value, held.value) == ("rel", "undefined/root", "undefined//host/path")


def test_blank_nodes_are_named_from_the_two_counters_and_move_them_on() -> None:
    labels = BlankLabels()
    first = parse("_:x <a:p> [] , ( 1 ) .", labels)
    assert [(subject.value, held.value) for subject, _, held in first] == [
        ("b0_x", "n3-0"),
        ("n3-1", "1"),
        ("n3-1", f"{RDF}nil"),
        ("b0_x", "n3-1"),
    ]
    assert labels == BlankLabels(documents=1, anonymous=2)
    assert only("_:x <a:p> [] .")[0].value == "b0_x"
    again = parse("_:x <a:p> [] .", labels)
    assert [(subject.value, held.value) for subject, _, held in again] == [("b1_x", "n3-2")]
    # A document that is refused has still been counted.
    with pytest.raises(TurtleError):
        parse("?", labels)
    assert labels == BlankLabels(documents=3, anonymous=3)


def test_a_fault_of_spelling_is_reported_before_a_fault_of_grammar_above_it() -> None:
    with pytest.raises(TurtleError, match=r'^Unexpected "\?" on line 2\.$'):
        parse("<a:s> <a:p> .\n?")
    with pytest.raises(TurtleError, match=r"^Expected entity but got \. on line 1\.$"):
        parse("<a:s> <a:p> .\n<a:t>")


def test_tokens_carry_their_line_and_end_in_eof() -> None:
    tokens = tokenize('<a:s>\n<a:p> """two\nlines"""@en ;\n  a 1.5 .')
    assert [(token.type, token.value, token.line) for token in tokens] == [
        ("IRI", "a:s", 1),
        ("IRI", "a:p", 2),
        ("literal", "two\nlines", 2),
        ("langcode", "en", 3),
        (";", "", 3),
        ("abbreviation", "a", 4),
        ("literal", "1.5", 4),
        (".", "", 4),
        ("eof", "", 4),
    ]
    assert tokens[6].prefix == f"{XSD}decimal"


def test_nothing_has_no_name() -> None:
    # JavaScript's null, where n3 reads the `id` of a statement part that was never read.
    with pytest.raises(TurtleError, match=r"^Cannot read properties of null \(reading 'id'\)$"):
        _ = NULL.id
    assert Term("NamedNode", "a:b").id == "a:b"
    assert Term("BlankNode", "b0_x").id == "_:b0_x"
    assert Term("Literal", "x").id == '"x"'
    assert Term("Literal", "x", language="en").id == '"x"@en'
    assert Term("Literal", "x", datatype="a:t").id == '"x"^^a:t'


def test_a_blank_node_closed_as_a_predicate_is_followed_by_an_object() -> None:
    # n3 keeps one state for N3, where a blank node may be a predicate. Turtle mode never
    # saves such a context, so no document reaches the line, and it is read here directly
    # to show it is n3's: after the bracket, the reader is the one that reads an object.
    parser = _Parser(BlankLabels())
    subject, predicate = Term("NamedNode", "a:s"), Term("BlankNode", "n3-0")
    parser._stack.append(_Context("blank", subject, predicate, NULL))  # noqa: SLF001
    following = parser._blank_tail(Token("]", "", "", 1))  # noqa: SLF001
    assert following == parser._read_object  # noqa: SLF001
    assert (parser._subject, parser._predicate, parser._object) == (subject, predicate, NULL)  # noqa: SLF001
    assert parser.triples == []


# --- Properties ------------------------------------------------------------------------

# Characters an IRI may hold unescaped, with some above ASCII and one above the first plane.
iri_text = st.text(st.sampled_from("abz09/-._~?#=&%:\u00e9\u03b1\U0001f9ec"), max_size=8)
iris = iri_text.map(lambda tail: f"http://e/{tail}")
languages = st.from_regex(r"[a-zA-Z]{1,3}(-[a-zA-Z0-9]{1,4}){0,2}", fullmatch=True)
texts = st.text(st.characters(exclude_categories=["Cs"]), max_size=12)
literals = st.builds(
    Term,
    st.just("Literal"),
    texts,
    language=st.just("") | languages.map(str.lower),
).flatmap(
    lambda term: (
        st.just(term)
        if term.language
        else st.builds(Term, st.just("Literal"), st.just(term.value), datatype=st.just("") | iris)
    )
)
subjects = iris.map(lambda iri: Term("NamedNode", iri))
triples = st.tuples(subjects, subjects, subjects | literals)
gaps = st.sampled_from([" ", "\t", "\n", "\r\n", " # a comment\n", "  \n\t"])


def escaped(text: str) -> str:
    """Text inside double quotes, with every character that would end or break the string escaped."""
    table = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r"}
    return "".join(table.get(character, character) for character in text)


def written(term: Term) -> str:
    if term.kind == "NamedNode":
        return f"<{term.value}>"
    tail = f"@{term.language}" if term.language else f"^^<{term.datatype}>" if term.datatype else ""
    return f'"{escaped(term.value)}"{tail}'


@given(st.lists(triples, max_size=6), st.data())
def test_statements_written_one_to_a_line_are_read_back_as_they_were(
    statements: list[Triple], data: st.DataObject
) -> None:
    document = "".join(
        f"{written(subject)}{data.draw(gaps)}{written(predicate)}{data.draw(gaps)}{written(held)}"
        f"{data.draw(gaps)}.{data.draw(gaps)}"
        for subject, predicate, held in statements
    )
    assert parse(document) == statements


@given(st.lists(triples, min_size=1, max_size=6))
def test_shared_subjects_and_predicates_say_the_same_as_writing_each_out(statements: list[Triple]) -> None:
    subject, predicate, _ = statements[0]
    objects = [held for _, _, held in statements]
    full = "".join(f"{written(subject)} {written(predicate)} {written(held)} .\n" for held in objects)
    short = f"{written(subject)} {written(predicate)} {' , '.join(written(held) for held in objects)} ."
    again = (
        f"{written(subject)} "
        + " ; ".join(f"{written(predicate)} {written(held)}" for held in objects)
        + " ."
    )
    assert parse(full) == parse(short) == parse(again) == [(subject, predicate, held) for held in objects]


@given(st.lists(subjects, max_size=5))
def test_a_list_is_a_chain_of_cells_ending_in_nil(items: list[Term]) -> None:
    read_back = parse(f"<a:s> <a:p> ( {' '.join(written(item) for item in items)} ) .")
    *cells, (subject, predicate, head) = read_back
    assert (subject.value, predicate.value) == ("a:s", "a:p")
    firsts = [held for _, link, held in cells if link.value == f"{RDF}first"]
    rests = [held.value for _, link, held in cells if link.value == f"{RDF}rest"]
    assert firsts == items
    assert head.value == (f"{RDF}nil" if not items else "n3-0")
    assert rests == [*(f"n3-{index}" for index in range(1, len(items))), *([f"{RDF}nil"] if items else [])]
