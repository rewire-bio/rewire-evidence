"""Controlled vocabularies, ported from `scripts/omics/vocab.ts`. Read only.

Each vocabulary is a SKOS concept scheme in `data/vocab/<scheme>.ttl`. A record stores a
concept's key, the last segment of its IRI: `auprc` for
`https://benchmarks.rewire.it/vocab/metric/auprc`. `data/ontology/mapping.json` names the
scheme of each controlled field, and `validate_vocabularies` refuses a value that is not a
key of its field's scheme.

The TypeScript reads Turtle with n3, and what a vocabulary holds is what n3 makes of the
file: which documents it refuses, the order it hands statements back in, how it spells a
number or a language tag, how it names a blank node. Another Turtle parser agrees with n3
on the files the store holds today and not on those, so the files are read by
`rewire_evidence.turtle`, a port of n3's own lexer and parser, and a file n3 refuses is
refused here in n3's words. The schema gate compares a digest of every file's statements
with the one n3 gave.

What is not reproduced is the wording of a refusal that comes from the operating system: a
scheme file that is a folder or cannot be opened. Node words those and Python words them
differently. Both refuse.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rewire_evidence.canonical import (
    JsonObject,
    JsonValue,
    js_parse,
    js_quote,
    js_string,
    js_stringify,
    utf16_key,
)
from rewire_evidence.jsonl import text_field
from rewire_evidence.turtle import BlankLabels, Term, TurtleError, parse

VOCAB_BASE = "https://benchmarks.rewire.it/vocab/"
VOCAB_DIR = "data/vocab"
MAPPING_FILE = "data/ontology/mapping.json"
#: The scheme of a derivation's method, which the TypeScript names in code and not in the mapping.
DERIVATION_METHOD_SCHEME = "derivation-method"
#: `validateVocabularies` stops looking at a record once it holds this many problems.
PROBLEM_LIMIT = 20

_SKOS = "http://www.w3.org/2004/02/skos/core#"
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_IN_SCHEME = f"{_SKOS}inScheme"
_PREF_LABEL = f"{_SKOS}prefLabel"
_BROADER = f"{_SKOS}broader"
_NARROWER = f"{_SKOS}narrower"

# Keys are lowercase words joined by hyphens, or by underscores where a closed list already
# used them in code (source_checked, author_reported). Matched whole: see schema._RECORD_ID.
_KEY = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
# A concept IRI below the vocabulary base: the scheme, a slash, the key. JavaScript's dot
# stops at four line terminators and Python's at one, so they are spelt out.
_CONCEPT = re.compile(r"([a-z0-9-]+)/([^\n\r\u2028\u2029]+)")


class VocabularyError(Exception):
    """A vocabulary cannot be read, or a record holds a value outside one."""


@dataclass(frozen=True)
class Statement:
    """One statement of a Turtle file, as the three values n3 gives it."""

    subject: str
    predicate: str
    object: str
    #: Whether the object is an IRI, and not text or a blank node.
    object_is_iri: bool
    #: Whether the subject or the object is a blank node, whose label is the parser's own.
    has_blank_node: bool
    #: The statement on one line, in the form the recorded digests are taken over.
    line: str


@dataclass(frozen=True)
class Concept:
    """One concept of a scheme."""

    key: str
    iri: str
    label: str


@dataclass(frozen=True)
class Scheme:
    """One vocabulary: its name, its IRI and its concepts by key, in the order of the file."""

    name: str
    iri: str
    concepts: Mapping[str, Concept]


@dataclass(frozen=True)
class ControlledFields:
    """The controlled fields `mapping.json` names: field to scheme, or to None if it has none.

    Each section keeps the order of the file, which is the order values are checked in. A
    scheme is whatever the file holds there that JavaScript counts as true. One that is not
    text names no vocabulary, and is kept so the report can say so as the TypeScript does.
    """

    fields: Mapping[str, JsonValue]
    facets: Mapping[str, JsonValue]
    attributes: Mapping[str, JsonValue]
    review: Mapping[str, JsonValue]


@dataclass(frozen=True)
class Controlled:
    """One controlled value on a record: where it is, the scheme it is held to, and the value."""

    field: str
    scheme: JsonValue
    value: JsonValue


def scheme_iri(name: str) -> str:
    """The IRI of a scheme, which is also the prefix of every concept in it."""
    return f"{VOCAB_BASE}{name}/"


def _term(term: Term) -> str:
    """A subject or object as the recorded digests write it: its kind, then its value."""
    if term.kind == "Literal":
        return f"Literal {js_quote(term.value)} {term.language} {term.datatype}"
    return f"{term.kind} {term.value}"


def read_turtle(text: str, where: str, labels: BlankLabels | None = None) -> list[Statement]:
    """Every statement of a Turtle document, in the order n3 hands them back.

    `labels` is where n3's blank node counters stand, and None is a process that has parsed
    nothing. Raises `VocabularyError` with n3's own message for text n3 refuses. The
    TypeScript lets that message through as it is, so it does not name the file. `where`
    is added as a note, which the gates print after the message.
    """
    try:
        triples = parse(text, labels)
    except TurtleError as error:
        refusal = VocabularyError(str(error))
        refusal.add_note(f"in {where}")
        raise refusal from error
    return [
        Statement(
            subject.value,
            predicate.value,
            held.value,
            held.kind == "NamedNode",
            "BlankNode" in {subject.kind, held.kind},
            f"{_term(subject)}\t{predicate.value}\t{_term(held)}\n",
        )
        for subject, predicate, held in triples
    ]


def scheme_files(root: Path) -> list[Path]:
    """The scheme files of a checkout in name order: `*.ttl` directly in `data/vocab`.

    No `data/vocab` is no schemes. Raises `VocabularyError` where it exists and cannot be
    listed, a file of that name for one, as the TypeScript throws there.
    """
    folder = root / VOCAB_DIR
    # `fs.existsSync` answers false for anything it cannot reach, where Path.exists raises.
    if not os.path.exists(folder):  # noqa: PTH110
        return []
    try:
        found = [path for path in folder.iterdir() if path.name.endswith(".ttl")]
    except OSError as error:
        raise VocabularyError(f"{VOCAB_DIR} cannot be read: {error}") from error
    return sorted(found, key=lambda path: utf16_key(path.name))


def decode(data: bytes) -> str:
    """Bytes as Node reads a file with `readFileSync(path, "utf8")`.

    Whatever is not UTF-8 becomes U+FFFD, one for each run of bytes that cannot begin or
    continue a character, and a byte order mark is kept for the parser to drop.
    """
    return data.decode("utf-8", errors="replace")


def read_scheme_file(path: Path, labels: BlankLabels | None = None) -> list[Statement]:
    """The statements of one scheme file. `labels` is as for `read_turtle`."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise VocabularyError(f"{path.name} cannot be read: {error}") from error
    return read_turtle(decode(data), path.name, labels)


def _concepts(file: str, iri: str, statements: Sequence[Statement]) -> dict[str, Concept]:
    """The concepts a file places in its scheme, each with the first label the file gives it."""
    labels: dict[str, str] = {}
    for statement in statements:
        if statement.predicate == _PREF_LABEL:
            labels.setdefault(statement.subject, statement.object)
    concepts: dict[str, Concept] = {}
    for statement in statements:
        if statement.predicate != _IN_SCHEME or statement.object != iri:
            continue
        key = statement.subject[len(iri) :]
        if not statement.subject.startswith(iri) or not _KEY.fullmatch(key):
            raise VocabularyError(
                f"{file}: concept <{statement.subject}> is not <{iri}key> with a lowercase key"
            )
        if statement.subject not in labels:
            raise VocabularyError(f"{file}: concept {key} has no skos:prefLabel")
        concepts[key] = Concept(key, statement.subject, labels[statement.subject])
    return concepts


def load_schemes(root: Path, labels: BlankLabels | None = None) -> dict[str, Scheme]:
    """Every scheme of a checkout by name: `loadSchemes`.

    A concept belongs to the scheme whose IRI prefixes its own. Raises `VocabularyError`,
    in the TypeScript's words, for a file n3 does not read, a file that does not declare
    its scheme, a concept whose key is not lowercase words, a concept with no label, a
    reference to a concept that does not exist, a concept placed in another scheme than
    its IRI, and a loop of `skos:broader`.

    A refusal can name a blank node, and n3 names those from counters that run for the life
    of the process. `labels` is where they stand. None is a process that has parsed nothing
    before, which is what `records check` is, and gives the labels it prints.
    """
    counters = BlankLabels() if labels is None else labels
    schemes: dict[str, Scheme] = {}
    parsed: list[tuple[str, list[Statement]]] = []
    for path in scheme_files(root):
        name = path.name.removesuffix(".ttl")
        iri = scheme_iri(name)
        statements = read_scheme_file(path, counters)
        parsed.append((path.name, statements))
        declared = any(
            statement.subject == iri
            and statement.predicate == _RDF_TYPE
            and statement.object == f"{_SKOS}ConceptScheme"
            for statement in statements
        )
        if not declared:
            raise VocabularyError(f"{path.name} does not declare the concept scheme <{iri}>")
        schemes[name] = Scheme(name, iri, _concepts(path.name, iri, statements))
    _check_references(parsed, schemes)
    return schemes


def _concept_parts(iri: str) -> tuple[str, str] | None:
    """The scheme name and key of a concept IRI, or None for anything outside the pattern."""
    if not iri.startswith(VOCAB_BASE):
        return None
    match = _CONCEPT.fullmatch(iri[len(VOCAB_BASE) :])
    return (match[1], match[2]) if match else None


def _loops(parents: Mapping[str, Mapping[str, None]]) -> list[str]:
    """Every loop of `skos:broader`, found depth first as the TypeScript finds them.

    The TypeScript recurses. This keeps its own stack, so a chain of any length is walked:
    a recursive walk in Python gives out near a thousand concepts deep, where Node goes on
    for several thousand more.
    """
    problems: list[str] = []
    closed: set[str] = set()
    for start in parents:
        if start in closed:
            continue
        # The concepts on the way down from `start`, each with the parents it has left.
        trail: list[str] = [start]
        waiting: list[Iterator[str]] = [iter(parents[start])]
        while trail:
            parent = next(waiting[-1], None)
            if parent is None:
                closed.add(trail.pop())
                waiting.pop()
            elif parent in trail:
                loop = [*trail[trail.index(parent) :], parent]
                problems.append(
                    f"skos:broader cycle: {', then '.join(iri[len(VOCAB_BASE) :] for iri in loop)}"
                )
            elif parent not in closed:
                trail.append(parent)
                waiting.append(iter(parents.get(parent, {})))
    return problems


def _check_references(
    parsed: Iterable[tuple[str, Sequence[Statement]]], schemes: Mapping[str, Scheme]
) -> None:
    """Links between concepts, which nothing else dereferences.

    Every object IRI in a `vocab/<scheme>/` namespace must be a concept that exists, every
    `skos:inScheme` must name the scheme its subject's IRI sits in, and `skos:broader`,
    with `skos:narrower` read as its inverse, must not loop. Only the first twenty problems
    are reported.
    """
    problems: list[str] = []
    parents: dict[str, dict[str, None]] = {}
    for file, statements in parsed:
        for statement in statements:
            if statement.predicate == _IN_SCHEME:
                subject = _concept_parts(statement.subject)
                if subject and scheme_iri(subject[0]) != statement.object:
                    problems.append(
                        f"{file}: <{statement.subject}> is in scheme <{statement.object}> "
                        f"but its IRI is in <{scheme_iri(subject[0])}>"
                    )
                continue
            target = _concept_parts(statement.object) if statement.object_is_iri else None
            if target is None:
                continue
            scheme = schemes.get(target[0])
            if scheme is None or target[1] not in scheme.concepts:
                predicate = statement.predicate.replace(_SKOS, "skos:", 1)
                problems.append(
                    f"{file}: <{statement.subject}> {predicate} <{statement.object}>, which is not a concept"
                )
            elif statement.predicate == _BROADER:
                parents.setdefault(statement.subject, {})[statement.object] = None
            elif statement.predicate == _NARROWER:
                parents.setdefault(statement.object, {})[statement.subject] = None
    problems.extend(_loops(parents))
    if problems:
        listed = "\n  ".join(problems[:PROBLEM_LIMIT])
        raise VocabularyError(f"Vocabulary references do not resolve:\n  {listed}")


def _entries(section: JsonValue) -> Iterable[tuple[str, JsonValue]]:
    """What `Object.entries` lists of a section: the members of an object, the items of a list."""
    if isinstance(section, dict):
        return section.items()
    if isinstance(section, list):
        return ((str(index), item) for index, item in enumerate(section))
    # The characters of text have no scheme, and a number or a boolean has no entries.
    return ()


def _section(mapping: JsonObject, name: str, where: str) -> dict[str, JsonValue]:
    """One section as field to scheme, read the way `controlledValues` reads it.

    The TypeScript asks each term for its `scheme` and tests that for truth, and never
    checks a shape. So a term that is not an object has no scheme, and a scheme that is
    false, zero or empty text is none. Only what JavaScript cannot read is refused: a
    section or a term that is null or missing, where the TypeScript throws a `TypeError`
    at the first record it checks.
    """
    section = mapping.get(name)
    if section is None:
        raise VocabularyError(f"{where} has no {name!r}")
    terms: dict[str, JsonValue] = {}
    for field, term in _entries(section):
        if term is None:
            raise VocabularyError(f"{where}: {name}.{field} is null")
        scheme = term.get("scheme") if isinstance(term, dict) else None
        terms[field] = scheme if _truthy(scheme) else None
    return terms


def _truthy(value: JsonValue) -> bool:
    """Whether JavaScript counts a JSON value as true: all but null, false, zero and no text."""
    return isinstance(value, list | dict) or bool(value)


def mapping_of(value: JsonValue, where: str = MAPPING_FILE) -> ControlledFields:
    """The controlled fields of a mapping that has been read. `where` names it in a refusal."""
    if not isinstance(value, dict):
        raise VocabularyError(f"{where} is not a JSON object")
    return ControlledFields(
        _section(value, "fields", where),
        _section(value, "facets", where),
        _section(value, "attributes", where),
        _section(value, "review", where),
    )


def read_mapping(root: Path) -> ControlledFields:
    """The controlled fields of a checkout's `data/ontology/mapping.json`."""
    path = root / MAPPING_FILE
    try:
        return mapping_of(js_parse(path.read_bytes().decode("utf-8")))
    except OSError as error:
        raise VocabularyError(f"{MAPPING_FILE} cannot be read: {error}") from error
    except ValueError as error:
        raise VocabularyError(f"{MAPPING_FILE} is not JSON: {error}") from error


def _object(row: Mapping[str, JsonValue], name: str, where: str) -> JsonObject:
    held = row.get(name)
    if not isinstance(held, dict):
        raise VocabularyError(f"{where} has no object {name!r}")
    return held


def controlled_values(mapping: ControlledFields, row: Mapping[str, JsonValue]) -> list[Controlled]:
    """Every controlled value on a record, with where it came from: `controlledValues`.

    The record is taken as a row, because the fields the mapping controls are looked up by
    name. Null and absent hold nothing. A list holds each of its items, which is how a
    facet is checked. A review or a derivation that is not an object is not looked into.
    Raises `VocabularyError` for a row whose `facets` or `attributes` is not an object,
    where the TypeScript would fail on reading it.
    """
    where = f"the record {row.get('id')!r}"
    facets = _object(row, "facets", where)
    attributes = _object(row, "attributes", where)
    found: list[Controlled] = []

    def add(field: str, scheme: JsonValue, value: JsonValue) -> None:
        if scheme is None or value is None:
            return
        found.extend(
            Controlled(field, scheme, item) for item in (value if isinstance(value, list) else [value])
        )

    for name, scheme in mapping.fields.items():
        add(name, scheme, row.get(name))
    for name, scheme in mapping.facets.items():
        add(f"facets.{name}", scheme, facets.get(name))
    for name, scheme in mapping.attributes.items():
        add(f"attributes.{name}", scheme, attributes.get(name))
    review = attributes.get("review")
    if isinstance(review, dict):
        for name, scheme in mapping.review.items():
            add(f"attributes.review.{name}", scheme, review.get(name))
    derivation = attributes.get("derivation")
    if isinstance(derivation, dict):
        add("attributes.derivation.method", DERIVATION_METHOD_SCHEME, derivation.get("method"))
    return found


def _without_infinity(value: JsonValue) -> JsonValue:
    """The value with null where it holds a number beyond every double, as JavaScript prints it."""
    if isinstance(value, float) and value in (float("inf"), float("-inf")):
        return None
    if isinstance(value, list):
        return [_without_infinity(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_infinity(held) for key, held in value.items()}
    return value


def vocabulary_problems(
    mapping: ControlledFields, rows: Iterable[Mapping[str, JsonValue]], schemes: Mapping[str, Scheme]
) -> list[str]:
    """What `validateVocabularies` would report, in its order, each problem once.

    The TypeScript stops looking at a record as soon as it holds twenty problems, and then
    goes on to the next record, where it stops after the first problem it adds. So more
    than twenty can be reported, and that is kept.
    """
    problems: list[str] = []
    for row in rows:
        for found in controlled_values(mapping, row):
            # A scheme is looked up as it is and printed as text, so one that is not text
            # is never found, whatever it prints as.
            scheme = schemes.get(found.scheme) if isinstance(found.scheme, str) else None
            if scheme is None:
                try:
                    name = js_string(found.scheme)
                except TypeError as error:
                    # The TypeScript throws here too, and reports nothing else.
                    raise VocabularyError(f"the scheme of {found.field} has no name: {error}") from error
                problems.append(f'{found.field} uses scheme "{name}", which has no {VOCAB_DIR}/{name}.ttl')
            elif not isinstance(found.value, str) or found.value not in scheme.concepts:
                shown = js_stringify(_without_infinity(found.value))
                record_id = text_field(row, "id", "a record")
                problems.append(
                    f"{record_id}: {found.field} {shown} is not a concept in the {found.scheme} vocabulary"
                )
            if len(problems) >= PROBLEM_LIMIT:
                break
    return list(dict.fromkeys(problems))


def validate_vocabularies(
    mapping: ControlledFields, rows: Iterable[Mapping[str, JsonValue]], schemes: Mapping[str, Scheme]
) -> None:
    """Refuse values that are not keys of their field's scheme: `validateVocabularies`.

    Raises `VocabularyError` with the message the TypeScript throws.
    """
    problems = vocabulary_problems(mapping, rows, schemes)
    if problems:
        listed = "\n  ".join(problems)
        raise VocabularyError(f"Uncontrolled values:\n  {listed}")
