"""RDF terms and SHACL shapes for declared attributes, ported from `scripts/kg/attribute-terms.ts`.

Every attribute in `shared/omics/attribute-registry.ts` (loaded here as `attributes.REGISTRY`)
becomes an OWL property declaration in `data/ontology/rb-attributes.ttl` and a per-kind SHACL
property constraint in `data/ontology/attribute-shapes.ttl`. Structured attributes
(uncertainty, coverage, reported_population, derivation, missing_metadata) expand into typed
nodes with their own shapes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Final, cast

from rewire_evidence.attributes import REGISTRY
from rewire_evidence.canonical import JsonObject, utf16_key
from rewire_evidence.vocab import VOCAB_BASE

_SNAKE = re.compile(r"_([a-z0-9])")

#: Attributes already exported via core record fields or the review mapping.
NOT_EXPORTED: Final[frozenset[str]] = frozenset({"review"})

#: Declaration prefixes emitted into `rb-attributes.ttl`, in `mapping.prefixes` order.
_DECL_PREFIXES: Final[frozenset[str]] = frozenset(
    {"rb", "rdf", "rdfs", "xsd", "schema", "mls", "dcat", "obo"}
)
#: Shape prefixes emitted into `attribute-shapes.ttl`, in `mapping.prefixes` order.
_SHAPE_PREFIXES: Final[frozenset[str]] = frozenset({"rb", "rdf", "xsd", "schema", "mls", "dcat", "obo"})


@dataclass(frozen=True)
class Encoding:
    """How a registry attribute type maps to RDF."""

    kind: str
    datatype: str | None = None
    scheme: str | None = None
    node: str | None = None
    is_list: bool = False


@dataclass(frozen=True)
class NodeField:
    """One field inside a structured RDF attribute node."""

    property: str
    datatype: str | None = None
    scheme: str | None = None
    range_constraint: str | None = None
    record: bool = False
    node: str | None = None


@dataclass(frozen=True)
class NodeSpec:
    """Class metadata and fields for one structured RDF node kind."""

    rdf_class: str
    comment: str
    fields: Mapping[str, NodeField]


@dataclass(frozen=True)
class MissingSpec:
    """Terms for `missing_metadata` nodes."""

    property: str = "rb:missingValue"
    rdf_class: str = "rb:MissingValue"
    comment: str = "A declared field the record has no value for, and why."
    field: str = "rb:missingField"
    reason: str = "rb:missingReason"
    note: str = "rb:note"


MISSING_SPEC: Final = MissingSpec()

NODES: Final[dict[str, NodeSpec]] = {
    "uncertainty": NodeSpec(
        "rb:Uncertainty",
        "A result's reported uncertainty: one of the uncertainty types, with its spread or interval.",
        {
            "type": NodeField("rb:uncertaintyType", scheme="uncertainty-type"),
            "printed": NodeField("rb:printedUncertainty"),
            "value": NodeField("rb:spread", datatype="xsd:decimal"),
            "half_width": NodeField("rb:halfWidth", datatype="xsd:decimal"),
            "lower": NodeField("rb:lowerBound", datatype="xsd:decimal"),
            "upper": NodeField("rb:upperBound", datatype="xsd:decimal"),
            "center": NodeField("rb:spreadCenter", datatype="xsd:decimal"),
            "level": NodeField(
                "rb:confidenceLevel",
                datatype="xsd:decimal",
                range_constraint="sh:minExclusive 0 ; sh:maxExclusive 1",
            ),
            "method": NodeField("rb:estimationMethod"),
            "n": NodeField("rb:sampleSize", datatype="xsd:integer"),
            "resamples": NodeField("rb:resampleCount", datatype="xsd:integer"),
            "scope": NodeField("rb:uncertaintyScope"),
            "unit": NodeField("rb:uncertaintyUnit"),
            "source_column": NodeField("rb:sourceColumn"),
            "note": NodeField("rb:note"),
        },
    ),
    "coverage": NodeSpec(
        "rb:Coverage",
        "How many items were scored out of how many were eligible.",
        {
            "scored": NodeField("rb:scoredCount", datatype="xsd:integer"),
            "eligible": NodeField("rb:eligibleCount", datatype="xsd:integer"),
            "unit": NodeField("rb:countUnit"),
            "generated_per_run": NodeField("rb:generatedPerRun", datatype="xsd:integer"),
            "repeats": NodeField("rb:repeatCount", datatype="xsd:integer"),
            "note": NodeField("rb:note"),
        },
    ),
    "reported-population": NodeSpec(
        "rb:ReportedPopulation",
        "The population size a source reports, as a count or as train, validation and test counts.",
        {
            "count": NodeField("rb:populationCount", datatype="xsd:integer"),
            "unit": NodeField("rb:populationUnit"),
            "train": NodeField("rb:trainCount", datatype="xsd:integer"),
            "validation": NodeField("rb:validationCount", datatype="xsd:integer"),
            "test": NodeField("rb:testCount", datatype="xsd:integer"),
            "note": NodeField("rb:note"),
        },
    ),
    "derivation": NodeSpec(
        "rb:Derivation",
        (
            "How Rewire computed a result the source does not print: the source's own "
            "supplementary data rows, the aggregation the source states, and the pinned script."
        ),
        {
            "method": NodeField("rb:derivationMethod", scheme="derivation-method"),
            "inputs": NodeField("rb:derivationInput", node="derivation-input"),
            "aggregation": NodeField("rb:statedAggregation"),
            "aggregation_source_id": NodeField("rb:aggregationSource", record=True),
            "aggregation_locator": NodeField("rb:aggregationLocator"),
            "script": NodeField("rb:scriptPath"),
            "script_sha256": NodeField("rb:scriptSha256"),
            "precision": NodeField("rb:precision"),
            "note": NodeField("rb:note"),
        },
    ),
    "derivation-input": NodeSpec(
        "rb:DerivationInput",
        "The rows of one pinned source artifact that a derivation reads.",
        {
            "source_id": NodeField("rb:inputSource", record=True),
            "artifact_sha256": NodeField("rb:artifactSha256"),
            "locator": NodeField("rb:sourceLocator"),
            "row_filter": NodeField("rb:rowFilter"),
            "row_count": NodeField("rb:rowCount", datatype="xsd:integer"),
        },
    ),
}

_SIMPLE_ENCODINGS: Final[dict[str, Encoding]] = {
    "integer": Encoding("literal", datatype="xsd:integer"),
    "depth-tier": Encoding("literal", datatype="xsd:integer"),
    "number": Encoding("literal", datatype="xsd:double"),
    "boolean": Encoding("literal", datatype="xsd:boolean"),
    "decimal": Encoding("literal", datatype="xsd:decimal"),
    "decimal-or-null": Encoding("literal", datatype="xsd:decimal"),
    "date": Encoding("literal", datatype="xsd:date"),
    "datetime": Encoding("literal", datatype="xsd:dateTime"),
    "url": Encoding("iri"),
    "record-id": Encoding("record"),
    "record-ids": Encoding("record", is_list=True),
    "texts": Encoding("literal", is_list=True),
    "entity-kinds": Encoding("literal", is_list=True),
    "json": Encoding("literal", datatype="rdf:JSON"),
    "uncertainty": Encoding("node", node="uncertainty"),
    "coverage": Encoding("node", node="coverage"),
    "reported-population": Encoding("node", node="reported-population"),
    "derivation": Encoding("node", node="derivation"),
    "missing-metadata": Encoding("missing"),
}


def camel(key: str) -> str:
    """Convert `snake_case` to `camelCase` (`attribute-terms.ts:camel`)."""
    return _SNAKE.sub(lambda match: match.group(1).upper(), key)


def attribute_property(mapping: Mapping[str, object], key: str) -> str:
    """The RDF predicate CURIE for an attribute key: mapped or `rb:<camelCase>`."""
    attrs = cast("JsonObject", mapping["attributes"])
    entry = attrs.get(key)
    if isinstance(entry, dict):
        prop = entry.get("property")
        if isinstance(prop, str):
            return prop
    return f"rb:{camel(key)}"


def encoding(declared_type: str) -> Encoding:
    """Map one attribute registry type name to its RDF `Encoding`."""
    if declared_type.startswith("concept:"):
        return Encoding("concept", scheme=declared_type.removeprefix("concept:"))
    if declared_type.startswith("concepts:"):
        return Encoding("concept", scheme=declared_type.removeprefix("concepts:"), is_list=True)
    return _SIMPLE_ENCODINGS.get(declared_type, Encoding("literal"))


def attribute_uses(mapping: Mapping[str, object]) -> dict[str, list[tuple[str, str, str]]]:
    """Property CURIE to `[(kind, key, declared_type), ...]` across the registry."""
    uses: dict[str, list[tuple[str, str, str]]] = {}
    for kind, keys in REGISTRY.items():
        for key, declared_type in keys.items():
            if key in NOT_EXPORTED:
                continue
            prop = (
                MISSING_SPEC.property
                if declared_type == "missing-metadata"
                else attribute_property(mapping, key)
            )
            uses.setdefault(prop, []).append((kind, key, declared_type))
    return uses


def _xsd_of(enc: Encoding) -> str | None:
    return enc.datatype or "xsd:string" if enc.kind == "literal" else None


def _class_of(mapping: Mapping[str, object], kind: str) -> str:
    classes = cast("dict[str, dict[str, list[str]]]", mapping["classes"])
    return classes[kind]["types"][0]


def _prefixes(mapping: Mapping[str, object], allowed: AbstractSet[str]) -> list[str]:
    held = cast("dict[str, str]", mapping["prefixes"])
    return [f"@prefix {prefix}: <{ns}> ." for prefix, ns in held.items() if prefix in allowed]


def _ranges_for_encodings(encs: list[Encoding]) -> list[str]:
    raw: list[str] = []
    for enc in encs:
        if enc.kind == "node" and enc.node is not None:
            raw.append(NODES[enc.node].rdf_class)
        elif enc.kind == "missing":
            raw.append(MISSING_SPEC.rdf_class)
        elif enc.kind == "concept":
            raw.append("skos:Concept")
        else:
            xsd = _xsd_of(enc)
            if xsd is not None:
                raw.append(xsd)
    return sorted(set(raw), key=utf16_key)


def _declare_lines(
    prop: str,
    declared: set[str],
    meta: tuple[bool, str, str, list[str], list[str]],
) -> list[str]:
    if not prop.startswith("rb:") or prop in declared:
        return []
    declared.add(prop)
    object_property, label, comment, domains, ranges = meta
    escaped = comment.replace('"', '\\"')
    clauses = [
        *(f"schema:domainIncludes {d}" for d in domains),
        *(f"schema:rangeIncludes {r}" for r in ranges),
    ]
    return [
        f"{prop} a owl:{'ObjectProperty' if object_property else 'DatatypeProperty'} ;",
        f'    rdfs:label "{label}"@en ;',
        f'    rdfs:comment "{escaped}"@en ;',
        *(f"    {line}{' .' if idx == len(clauses) - 1 else ' ;'}" for idx, line in enumerate(clauses)),
        "",
    ]


def declarations_turtle(mapping: Mapping[str, object], declared_in_rb: AbstractSet[str]) -> str:
    """Turtle declaring every `rb:` property and class used by attributes not in `rb.ttl`."""
    lines = [
        "# Generated by npm run kg -- terms from shared/omics/attribute-registry.ts. Do not edit.",
        "# Properties for declared record attributes. schema:domainIncludes and schema:rangeIncludes",
        "# document use without adding inference; the per-kind shapes in attribute-shapes.ttl enforce it.",
        "",
        *_prefixes(mapping, _DECL_PREFIXES),
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "",
    ]
    declared = set(declared_in_rb)
    for prop, uses in sorted(attribute_uses(mapping).items(), key=lambda pair: utf16_key(pair[0])):
        encs = [encoding(declared_type) for _, _, declared_type in uses]
        keys = list(dict.fromkeys(key for _, key, _ in uses))
        kinds = sorted({kind for kind, _, _ in uses}, key=utf16_key)
        comment = (
            MISSING_SPEC.comment
            if prop == MISSING_SPEC.property
            else (
                f"The {' or '.join(keys)} attribute of "
                f"{', '.join(k.replace('_', ' ') for k in kinds)} records."
            )
        )
        lines.extend(
            _declare_lines(
                prop,
                declared,
                (
                    all(enc.kind != "literal" for enc in encs),
                    keys[0].replace("_", " "),
                    comment,
                    [_class_of(mapping, kind) for kind in kinds],
                    _ranges_for_encodings(encs),
                ),
            )
        )

    for node in NODES.values():
        if node.rdf_class not in declared:
            lines.extend([f"{node.rdf_class} a owl:Class ;", f'    rdfs:comment "{node.comment}"@en .', ""])
        declared.add(node.rdf_class)
        for field_name, field in node.fields.items():
            field_ranges = (
                [NODES[field.node].rdf_class]
                if field.node is not None
                else []
                if field.record
                else ["skos:Concept"]
                if field.scheme is not None
                else [field.datatype or "xsd:string"]
            )
            lines.extend(
                _declare_lines(
                    field.property,
                    declared,
                    (
                        bool(field.scheme or field.record or field.node),
                        field_name.replace("_", " "),
                        f"Field {field_name} of {node.rdf_class[3:]} nodes.",
                        [node.rdf_class],
                        field_ranges,
                    ),
                )
            )

    lines.extend(
        [
            f"{MISSING_SPEC.rdf_class} a owl:Class ;",
            f'    rdfs:comment "{MISSING_SPEC.comment}"@en .',
            "",
            *_declare_lines(
                MISSING_SPEC.field,
                declared,
                (
                    False,
                    "missing field",
                    "The declared attribute that has no value.",
                    [MISSING_SPEC.rdf_class],
                    ["xsd:string"],
                ),
            ),
            *_declare_lines(
                MISSING_SPEC.reason,
                declared,
                (
                    True,
                    "missing reason",
                    "Why the field has no value, from the missingness scheme.",
                    [MISSING_SPEC.rdf_class],
                    ["skos:Concept"],
                ),
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def _scheme_shape(scheme: str) -> str:
    return (
        "[ sh:class skos:Concept ; sh:property "
        f"[ sh:path skos:inScheme ; sh:hasValue <{VOCAB_BASE}{scheme}/> ; sh:minCount 1 ] ]"
    )


def _value_constraint(enc: Encoding) -> str:
    if enc.kind == "literal":
        if enc.datatype == "xsd:dateTime":
            return "sh:or ( [ sh:datatype xsd:dateTime ] [ sh:datatype xsd:date ] )"
        return f"sh:datatype {enc.datatype or 'xsd:string'}"
    if enc.kind in {"iri", "record"}:
        return "sh:nodeKind sh:IRI"
    if enc.kind == "concept" and enc.scheme is not None:
        return f"sh:node {_scheme_shape(enc.scheme)}"
    if enc.kind == "node" and enc.node is not None:
        return f"sh:node rbs:{NODES[enc.node].rdf_class[3:]}"
    return "sh:node rbs:MissingValue"


def _node_field_shape(field: NodeField) -> str:
    if field.node is not None:
        target = NODES[field.node].rdf_class[3:]
        return f"[ sh:path {field.property} ; sh:minCount 1 ; sh:node rbs:{target} ]"
    if field.record:
        constraint = "sh:nodeKind sh:IRI"
    elif field.scheme is not None:
        constraint = f"sh:node {_scheme_shape(field.scheme)}"
    else:
        constraint = f"sh:datatype {field.datatype or 'xsd:string'}"
    extra = f" ; {field.range_constraint}" if field.range_constraint else ""
    return f"[ sh:path {field.property} ; sh:maxCount 1 ; {constraint}{extra} ]"


def shapes_turtle(mapping: Mapping[str, object]) -> str:
    """Per-kind SHACL shapes for declared attributes and structured nodes."""
    lines = [
        "# Generated by npm run kg -- terms from shared/omics/attribute-registry.ts. Do not edit.",
        "# Each declared attribute has the datatype, node kind or concept scheme its registry type gives;",
        "# single-valued attributes appear at most once.",
        "",
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix rbs: <https://benchmarks.rewire.it/shapes#> .",
        *_prefixes(mapping, _SHAPE_PREFIXES),
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "",
    ]
    for kind, keys in REGISTRY.items():
        props: list[str] = []
        for key, declared_type in keys.items():
            if key in NOT_EXPORTED:
                continue
            enc = encoding(declared_type)
            prop = (
                MISSING_SPEC.property
                if declared_type == "missing-metadata"
                else attribute_property(mapping, key)
            )
            single = " sh:maxCount 1 ;" if enc.kind != "missing" and not enc.is_list else ""
            props.append(f"[ sh:path {prop} ;{single} {_value_constraint(enc)} ]")
        class_curie = _class_of(mapping, kind)
        lines.extend(
            [
                f"rbs:{class_curie[3:]}Attributes a sh:NodeShape ;",
                f"    sh:targetClass {class_curie} ;",
                "    sh:property\n        " + " ,\n        ".join(props) + " .",
                "",
            ]
        )

    for node in NODES.values():
        props = [_node_field_shape(field) for field in node.fields.values()]
        lines.extend(
            [
                f"rbs:{node.rdf_class[3:]} a sh:NodeShape ;",
                f"    sh:class {node.rdf_class} ;",
                "    sh:property\n        " + " ,\n        ".join(props) + " .",
                "",
            ]
        )

    missing_scheme = _scheme_shape("missingness")
    lines.extend(
        [
            "rbs:MissingValue a sh:NodeShape ;",
            f"    sh:class {MISSING_SPEC.rdf_class} ;",
            (
                f"    sh:property [ sh:path {MISSING_SPEC.field} ; sh:minCount 1 ; sh:maxCount 1 ; "
                "sh:datatype xsd:string ] ,"
            ),
            (
                f"        [ sh:path {MISSING_SPEC.reason} ; sh:minCount 1 ; sh:maxCount 1 ; "
                f"sh:node {missing_scheme} ] ,"
            ),
            f"        [ sh:path {MISSING_SPEC.note} ; sh:maxCount 1 ; sh:datatype xsd:string ] .",
            "",
        ]
    )
    return "\n".join(lines)
