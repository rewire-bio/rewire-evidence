"""What the TypeScript validators recorded, and the helpers the tests read it with.

`tests/fixtures/validation.json` is written by `scripts/fixtures/record_validation_fixture.mjs`
in CI, from the data repository's own validators at the pinned commit. Nothing in it was
written by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence.canonical import JsonObject, JsonValue, js_value, record_sha256
from rewire_evidence.jsonl import write_text
from rewire_evidence.schema import Record, parse_record
from rewire_evidence.store import write_store
from rewire_evidence.vocab import MAPPING_FILE, VOCAB_DIR, Concept, ControlledFields, Scheme, mapping_of

FIXTURES = Path(__file__).parent / "fixtures"
VALIDATION: dict[str, Any] = json.loads((FIXTURES / "validation.json").read_text("utf-8"))
SEMANTICS: dict[str, Any] = json.loads((FIXTURES / "js-semantics.json").read_text("utf-8"))
KG_FIXTURE: dict[str, Any] = json.loads((FIXTURES / "kg.json").read_text("utf-8"))

#: The base store: real records, valid on their own, in the order the store files them.
#: Typed loosely, because the tests reach into them by name.
BASE_ROWS: list[dict[str, Any]] = [json.loads(text) for text in VALIDATION["base"]["records"]]
#: The scripts its derivations name, by path, with their text.
BASE_FILES: dict[str, str] = VALIDATION["base"]["files"]
ROLES: dict[str, str] = VALIDATION["base"]["roles"]


def named(cases: list[dict[str, Any]]) -> list[Any]:
    return [pytest.param(case, id=case["name"]) for case in cases]


def value_of(text: str) -> JsonValue:
    """A JSON text as JavaScript holds what it parses to."""
    return js_value(json.loads(text))


def row_of(record_id: str) -> dict[str, Any]:
    """One record of the base store."""
    return next(row for row in BASE_ROWS if row["id"] == record_id)


def records_of(rows: list[JsonObject]) -> list[Record]:
    return [parse_record(row) for row in rows]


def mapping() -> ControlledFields:
    return mapping_of(VALIDATION["mapping"])


def schemes() -> dict[str, Scheme]:
    """The pinned vocabularies, as `loadSchemes` held them when the fixture was recorded."""
    return {
        name: Scheme(
            name, held["iri"], {key: Concept(key, iri, label) for key, iri, label in held["concepts"]}
        )
        for name, held in VALIDATION["schemes"].items()
    }


def write_scripts(root: Path, files: dict[str, str] | None = None) -> Path:
    """The files a store's derivations name, under a root."""
    for file, text in (BASE_FILES if files is None else files).items():
        write_text(root / file, text)
    return root


def write_configuration(root: Path) -> None:
    """The recorded mapping, vocabularies and ontology/SHACL files under a checkout root."""
    write_text(root / MAPPING_FILE, json.dumps(VALIDATION["mapping"]))
    for name, held in VALIDATION["schemes"].items():
        iri = held["iri"]
        body = "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
        body += f"<{iri}> a skos:ConceptScheme .\n"
        body += "".join(
            f"<{concept}> a skos:Concept ; skos:inScheme <{iri}> ; skos:prefLabel {json.dumps(label)}@en .\n"
            for _, concept, label in held["concepts"]
        )
        write_text(root / VOCAB_DIR / f"{name}.ttl", body)
    for rel, text in KG_FIXTURE["ontology"].items():
        if rel != MAPPING_FILE:
            write_text(root / rel, text)


def provenance_of(rows: list[JsonObject]) -> list[JsonObject]:
    return [
        {
            "id": row["id"],
            "sha256": record_sha256(row),
            "added_in": "a-batch",
            "added_by": "records:add",
            "changed_by": [],
        }
        for row in rows
    ]


def write_base(root: Path, rows: list[JsonObject] | None = None) -> Path:
    """A whole checkout: the base store or another, its provenance, scripts, mapping and vocabularies."""
    held = BASE_ROWS if rows is None else rows
    write_store(held, provenance_of(held), root)
    write_scripts(root)
    write_configuration(root)
    return root
