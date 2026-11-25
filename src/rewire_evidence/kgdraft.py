"""Manifest `draft` block generation, ported from `scripts/kg/draft-block.ts`."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from importlib import resources
from typing import Final, cast

from rewire_evidence.attributes import REGISTRY
from rewire_evidence.canonical import JsonObject, JsonValue, utf16_key
from rewire_evidence.jsonl import StoreError
from rewire_evidence.schema import KINDS, RECORD_ID_PATTERN, RELATION_RULES, SHAPE, STATUSES
from rewire_evidence.vocab import Scheme

#: Kinds permitted in draft bundles (`draft-block.ts:88`).
DRAFT_KINDS: Final[tuple[str, ...]] = tuple(
    kind for kind in KINDS if kind not in {"evaluation", "result", "claim", "baseline"}
)


def _read_type_schemas() -> JsonObject:
    text = resources.files("rewire_evidence").joinpath("data/attribute-type-schemas.json").read_text("utf-8")
    return cast("JsonObject", json.loads(text))


#: Recorded zod JSON Schemas for `shared/omics/attributes.ts:attributeTypes`.
TYPE_SCHEMAS_FILE: Final[JsonObject] = _read_type_schemas()


def _type_name(declared_type: str) -> str:
    if declared_type.startswith("concepts:"):
        return "concepts"
    if declared_type.startswith("concept:"):
        return "concept"
    return declared_type


def _ref(definition: str) -> JsonObject:
    return {"$ref": f"#/$defs/{definition}"}


def _controlled(base: JsonObject, scheme: str) -> JsonObject:
    if base.get("type") == "array":
        return {"type": "array", "items": _ref(f"scheme-{scheme}")}
    return _ref(f"scheme-{scheme}")


def _kind_clauses(
    mapping: Mapping[str, object],
    types: Mapping[str, JsonObject],
    control: Callable[[JsonObject, str], JsonObject],
) -> list[JsonObject]:
    mapped_attrs = cast("dict[str, dict[str, str]]", mapping["attributes"])
    per_kind: list[JsonObject] = []
    for kind in KINDS:
        declared = REGISTRY.get(kind, {})
        props: JsonObject = {}
        for key, declared_type in declared.items():
            name = _type_name(declared_type)
            attr_entry = mapped_attrs.get(key)
            scheme = attr_entry.get("scheme") if isinstance(attr_entry, dict) else None
            props[key] = (
                {"allOf": [_ref(f"type-{name}"), control(types[name], scheme)]}
                if scheme
                else _ref(f"type-{name}")
            )
        per_kind.append(
            {
                "if": {"properties": {"kind": {"const": kind}}, "required": ["kind"]},
                "then": {"properties": {"attributes": {"properties": props, "additionalProperties": False}}},
            }
        )
    return per_kind


def attribute_schemas(
    mapping: Mapping[str, object], schemes: Mapping[str, Scheme]
) -> tuple[JsonObject, list[JsonObject], JsonObject]:
    """Build `$defs`, per-kind `allOf` constraints, and controlled facets for the draft block."""
    used: set[str] = set()

    def control(base: JsonObject, scheme: str) -> JsonObject:
        if scheme not in schemes:
            raise StoreError(f"draft block: no vocabulary data/vocab/{scheme}.ttl")
        used.add(scheme)
        return _controlled(base, scheme)

    raw_schemas = cast("dict[str, JsonObject]", TYPE_SCHEMAS_FILE["schemas"])
    types: dict[str, JsonObject] = {name: copy.deepcopy(schema) for name, schema in raw_schemas.items()}

    def patch(name: str, terms: Mapping[str, str | None]) -> None:
        props = dict(cast("dict[str, JsonObject]", types[name]["properties"]))
        for field_name, scheme in terms.items():
            if scheme and field_name in props:
                props[field_name] = control(props[field_name], scheme)
        types[name] = {**types[name], "properties": cast("JsonValue", props)}

    review_terms = cast("dict[str, dict[str, str]]", mapping["review"])
    patch("review", {field: term.get("scheme") for field, term in review_terms.items()})
    patch("derivation", {"method": "derivation-method"})

    per_kind = _kind_clauses(mapping, types, control)
    mapped_facets = cast("dict[str, dict[str, str]]", mapping["facets"])
    controlled_facets: JsonObject = {
        name: control({"type": "array", "items": {"type": "string"}}, term["scheme"])
        for name, term in mapped_facets.items()
        if term.get("scheme")
    }

    defs: JsonObject = {}
    for name in sorted(types, key=utf16_key):
        defs[f"type-{name}"] = types[name]
    for name in sorted(used, key=utf16_key):
        enum_values = cast("list[JsonValue]", sorted(schemes[name].concepts, key=utf16_key))
        defs[f"scheme-{name}"] = {"type": "string", "enum": enum_values}
    return defs, per_kind, controlled_facets


def draft_block(mapping: Mapping[str, object], schemes: Mapping[str, Scheme]) -> JsonObject:
    """Build the KG bundle manifest `draft` block (`draft-block.ts:draftBlock`)."""
    defs, per_kind, controlled_facets = attribute_schemas(mapping, schemes)
    kind_enum: list[JsonValue] = list(KINDS)
    status_enum: list[JsonValue] = list(STATUSES)
    relation_enum: list[JsonValue] = list(RELATION_RULES)
    properties_by_field: dict[str, JsonValue] = {
        "id": {"type": "string", "pattern": RECORD_ID_PATTERN},
        "kind": {"enum": kind_enum},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "status": {"enum": status_enum},
        "facets": {
            "type": "object",
            "properties": controlled_facets,
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        },
        "source_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["relation", "target_id"],
                "properties": {
                    "relation": {"enum": relation_enum},
                    "target_id": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        },
        "attributes": {"type": "object"},
    }
    classes = cast("dict[str, dict[str, list[str]]]", mapping["classes"])
    return {
        "iri_base": cast("str", mapping["base"]),
        "id_field": "id",
        "type_field": "kind",
        "label_field": "name",
        "record_schema": {
            "type": "object",
            "required": list(SHAPE),
            "properties": {field: properties_by_field[field] for field in SHAPE},
            "additionalProperties": False,
            "allOf": cast("list[JsonValue]", per_kind),
            "$defs": defs,
        },
        "kinds": {kind: classes[kind]["types"][0] for kind in KINDS},
        "draft_kinds": list(DRAFT_KINDS),
        "links": {"field": "links", "relation": "relation", "target": "target_id"},
        "link_rules": {
            relation: {
                "from": list(dict.fromkeys(rule.sources)),
                "to": list(dict.fromkeys(rule.targets)),
                "description": rule.meaning,
            }
            for relation, rule in RELATION_RULES.items()
        },
        "references": [{"field": "source_ids", "kinds": ["source"]}],
        "sort_within": ["attributes", "facets"],
        "sort_arrays": ["links"],
    }
