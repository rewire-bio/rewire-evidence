"""RDF N-Quads and JSON-LD export, ported from `scripts/kg/export.ts`."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from rewire_evidence import kgdraft, kgterms
from rewire_evidence.attributes import REGISTRY
from rewire_evidence.canonical import (
    JsonObject,
    JsonValue,
    canonical_json,
    js_indented,
    js_parse,
    js_string,
    js_utf8,
    utf16_key,
)
from rewire_evidence.js import trim
from rewire_evidence.jsonl import StoreError, write_text
from rewire_evidence.records import load_records
from rewire_evidence.schema import Record, public_records
from rewire_evidence.vocab import Scheme, load_schemes, scheme_iri

MAPPING_FILE: Final = "data/ontology/mapping.json"
CONTEXT_FILE: Final = "data/ontology/context.jsonld"
ATTRIBUTE_DECLARATIONS_FILE: Final = "data/ontology/rb-attributes.ttl"
ATTRIBUTE_SHAPES_FILE: Final = "data/ontology/attribute-shapes.ttl"

_ENCODE_URI_SAFE: Final = ";/?:@&=+$,#-_.!~*'()"
_DECODE_URI_RESERVED: Final = frozenset(b";/?:@&=+$,#")
_URI_RESERVED_ESCAPE: Final = re.compile(r'([<>"{}|^`\\])')
_DECIMAL_RE: Final = re.compile(r"([-+]?)([0-9]+)(?:\.([0-9]+))?(?:[eE]([-+]?[0-9]+))?")
_DATE_RE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_RB_DECL_RE: Final = re.compile(r"^(rb:[A-Za-z0-9]+) a ", re.MULTILINE)
_HEX: Final = frozenset("0123456789abcdefABCDEF")
_WS_SPLIT: Final = re.compile(r"[ \t\n\r\v\f\u00a0\ufeff]+")

_ASCII_END: Final = 0x80
_UTF8_TWO_MIN: Final = 0xC2
_UTF8_TWO_MAX: Final = 0xDF
_UTF8_THREE_MIN: Final = 0xE0
_UTF8_THREE_MAX: Final = 0xEF
_UTF8_FOUR_MIN: Final = 0xF0
_UTF8_FOUR_MAX: Final = 0xF4


@dataclass(frozen=True)
class SubsetResult:
    """What `subset_quad_lines` returns: sorted N-Quads, checked IDs, and link-target context IRIs."""

    lines: list[str]
    checked: list[str]
    context: list[str]


@dataclass(frozen=True)
class _EmitTarget:
    """Destination context for emitting statements of one record."""

    mapping: Mapping[str, object]
    graph_iri: str
    base: str
    record_id: str
    lines: list[str]


def _read_hex_byte(text: str, pos: int) -> int | None:
    if pos + 2 >= len(text) or text[pos] != "%" or text[pos + 1] not in _HEX or text[pos + 2] not in _HEX:
        return None
    return int(text[pos + 1 : pos + 3], 16)


def _utf8_seq_length(first: int) -> int | None:
    if first < _ASCII_END:
        return 1
    if _UTF8_TWO_MIN <= first <= _UTF8_TWO_MAX:
        return 2
    if _UTF8_THREE_MIN <= first <= _UTF8_THREE_MAX:
        return 3
    if _UTF8_FOUR_MIN <= first <= _UTF8_FOUR_MAX:
        return 4
    return None


def _decode_percent_run(value: str, idx: int) -> tuple[str, int] | None:
    first = _read_hex_byte(value, idx)
    seq_len = None if first is None else _utf8_seq_length(first)
    if first is None or seq_len is None:
        return None
    if seq_len == 1:
        text = value[idx : idx + 3] if first in _DECODE_URI_RESERVED else chr(first)
        return text, idx + 3
    raw = bytearray([first])
    pos = idx + 3
    for _ in range(seq_len - 1):
        nxt = _read_hex_byte(value, pos)
        if nxt is None:
            return None
        raw.append(nxt)
        pos += 3
    try:
        return raw.decode("utf-8", errors="strict"), pos
    except UnicodeDecodeError:
        return None


def js_decode_uri_safe(value: str) -> str:
    """ECMAScript `decodeURI(value)`, returning `value` unchanged on malformed escapes (`URIError`)."""
    out: list[str] = []
    idx = 0
    length = len(value)
    while idx < length:
        if value[idx] != "%":
            out.append(value[idx])
            idx += 1
            continue
        decoded = _decode_percent_run(value, idx)
        if decoded is None:
            return value
        piece, idx = decoded
        out.append(piece)
    return "".join(out)


def js_encode_uri(value: str) -> str:
    """ECMAScript `encodeURI(value)`."""
    return urllib.parse.quote(value, safe=_ENCODE_URI_SAFE, encoding="utf-8", errors="strict")


def escape_literal(value: str) -> str:
    """N-Quads string escaping as written by `export.ts:escapeLiteral`."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def iri(value: str) -> str:
    """Encode an IRI in N-Quads angle brackets matching `export.ts:iri`."""
    encoded = js_encode_uri(js_decode_uri_safe(value))
    escaped = _URI_RESERVED_ESCAPE.sub(lambda m: f"%{ord(m.group(1)):02X}", encoded)
    return f"<{escaped}>"


def expand(mapping: Mapping[str, object], curie: str) -> str:
    """Expand a prefixed CURIE using `mapping['prefixes']` (`export.ts:expand`)."""
    parts = curie.split(":")
    prefix = parts[0]
    local = parts[1] if len(parts) > 1 else None
    prefixes = cast("dict[str, str]", mapping["prefixes"])
    ns = prefixes.get(prefix)
    if not ns or local is None:
        raise StoreError(f"Unknown prefix in {curie}")
    return ns + local


def canonical_decimal(text: str) -> str | None:
    """Canonical `xsd:decimal` lexical form without exponent (`export.ts:canonicalDecimal`)."""
    match = _DECIMAL_RE.fullmatch(trim(text))
    if not match:
        return None
    sign, whole, fraction, exponent = match.groups()
    frac = fraction or ""
    digits = whole + frac
    point = len(whole) + int(exponent or "0")
    if point <= 0:
        digits = "0" * (1 - point) + digits
        point = 1
    if point > len(digits):
        digits = digits + "0" * (point - len(digits))
    integer = digits[:point].lstrip("0") or "0"
    decimals = digits[point:]
    prefix = "-" if sign == "-" else ""
    return f"{prefix}{integer}.{decimals}" if decimals else f"{prefix}{integer}"


def _scalar_context(datatype: str | None) -> JsonObject:
    return {"@type": datatype} if datatype else {}


def _field_context(fields: Mapping[str, kgterms.NodeField]) -> JsonObject:
    out: JsonObject = {}
    for name, field in fields.items():
        if field.scheme is not None:
            meta: JsonObject = {
                "@id": field.property,
                "@type": "@vocab",
                "@context": {"@vocab": scheme_iri(field.scheme)},
            }
        elif field.record:
            meta = {"@id": field.property, "@type": "@id"}
        elif field.node is not None:
            meta = {"@id": field.property, "@context": _field_context(kgterms.NODES[field.node].fields)}
        else:
            meta = {"@id": field.property, **_scalar_context(field.datatype)}
        out[name] = meta
    return out


def _single_encoding_context(enc: kgterms.Encoding) -> JsonObject:
    if enc.kind == "missing":
        return {
            "@id": kgterms.MISSING_SPEC.property,
            "@container": "@index",
            "@index": kgterms.MISSING_SPEC.field,
            "@context": {
                "reason": {
                    "@id": kgterms.MISSING_SPEC.reason,
                    "@type": "@vocab",
                    "@context": {"@vocab": scheme_iri("missingness")},
                },
                "note": kgterms.MISSING_SPEC.note,
            },
        }
    if enc.kind == "node" and enc.node is not None:
        return {"@context": _field_context(kgterms.NODES[enc.node].fields)}
    if enc.kind == "concept" and enc.scheme is not None:
        return {"@type": "@vocab", "@context": {"@vocab": scheme_iri(enc.scheme)}}
    if enc.kind in {"iri", "record"}:
        return {"@type": "@id"}
    if enc.datatype == "rdf:JSON":
        return {"@type": "@json"}
    return _scalar_context(enc.datatype)


def _attribute_context(mapping: Mapping[str, object]) -> JsonObject:
    types: dict[str, set[str]] = {}
    for keys in REGISTRY.values():
        for key, declared_type in keys.items():
            types.setdefault(key, set()).add(declared_type)
    out: JsonObject = {}
    for key, type_set in sorted(types.items(), key=lambda pair: utf16_key(pair[0])):
        if key in kgterms.NOT_EXPORTED:
            continue
        prop = kgterms.attribute_property(mapping, key)
        extra = _single_encoding_context(kgterms.encoding(next(iter(type_set)))) if len(type_set) == 1 else {}
        out[key] = {"@id": prop, "@nest": "attributes", **extra}
    return out


def _term_context(term: Mapping[str, JsonValue]) -> JsonObject:
    prop = cast("str", term["property"])
    scheme = term.get("scheme")
    if isinstance(scheme, str):
        return {"@id": prop, "@type": "@vocab", "@context": {"@vocab": scheme_iri(scheme)}}
    if term.get("iri"):
        return {"@id": prop, "@type": "@id"}
    datatype = term.get("datatype")
    if isinstance(datatype, str):
        return {"@id": prop, "@type": datatype}
    return {"@id": prop}


def build_context(mapping: Mapping[str, object]) -> JsonObject:
    """Build the JSON-LD 1.1 `@context` document (`export.ts:buildContext`)."""
    prefixes = cast("dict[str, JsonValue]", mapping["prefixes"])
    classes = cast("dict[str, dict[str, list[str]]]", mapping["classes"])
    fields = cast("dict[str, dict[str, JsonValue]]", mapping["fields"])
    facets = cast("dict[str, dict[str, JsonValue]]", mapping["facets"])
    attrs = cast("dict[str, dict[str, JsonValue]]", mapping["attributes"])
    relations = cast("dict[str, dict[str, JsonValue]]", mapping["relations"])
    ctx: JsonObject = {
        "@version": 1.1,
        "@base": cast("str", mapping["base"]),
        "@vocab": cast("str", mapping["vocab"]),
        **prefixes,
        "id": "@id",
        "kind": {
            "@id": "rdf:type",
            "@type": "@vocab",
            "@context": {kind: entry["types"][0] for kind, entry in classes.items()},
        },
        **{name: _term_context(term) for name, term in fields.items()},
        "facets": "@nest",
        **{name: {**_term_context(term), "@nest": "facets"} for name, term in facets.items()},
        "attributes": "@nest",
        **_attribute_context(mapping),
        **{name: {**_term_context(term), "@nest": "attributes"} for name, term in attrs.items()},
        "links": {"@id": "rb:link"},
        "relation": {
            "@id": "rb:relation",
            "@type": "@vocab",
            "@context": {rel: entry["property"] for rel, entry in relations.items()},
        },
        "target_id": {"@id": "rb:target", "@type": "@id"},
    }
    return {"@context": ctx}


def _literal(
    mapping: Mapping[str, object],
    value: object,
    *,
    datatype: str | None = None,
    scheme: str | None = None,
    is_iri: bool = False,
) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = js_string(cast("JsonValue", value))
    if scheme is not None:
        return iri(scheme_iri(scheme) + text)
    if is_iri:
        return iri(text) if text.startswith(("http://", "https://")) else None
    if datatype == "xsd:decimal":
        dec = canonical_decimal(text)
        if dec is None:
            return f'"{escape_literal(text)}"'
        return f'"{dec}"^^' + iri(expand(mapping, "xsd:decimal"))
    effective_dt = "xsd:date" if datatype == "xsd:dateTime" and _DATE_RE.fullmatch(text) else datatype
    if effective_dt is not None:
        return f'"{escape_literal(text)}"^^' + iri(expand(mapping, effective_dt))
    return f'"{escape_literal(text)}"'


def _term_literal(mapping: Mapping[str, object], value: object, term: Mapping[str, JsonValue]) -> str | None:
    scheme = term.get("scheme")
    datatype = term.get("datatype")
    return _literal(
        mapping,
        value,
        datatype=datatype if isinstance(datatype, str) else None,
        scheme=scheme if isinstance(scheme, str) else None,
        is_iri=bool(term.get("iri")),
    )


def _quad(target: _EmitTarget, subj: str, predicate: str, obj: str) -> str:
    return f"{subj} {iri(expand(target.mapping, predicate))} {obj} {target.graph_iri} ."


def _emit_node(target: _EmitTarget, subj: str, spec: kgterms.NodeSpec, value: JsonObject, path: str) -> None:
    target.lines.append(_quad(target, subj, "rdf:type", iri(expand(target.mapping, spec.rdf_class))))
    fields = dict(value)
    split = fields.get("train_validation_test")
    if isinstance(split, list):
        for idx, split_name in enumerate(("train", "validation", "test")):
            if idx < len(split):
                fields[split_name] = split[idx]
    for field_name, field in spec.fields.items():
        if field_name not in fields:
            continue
        item = fields[field_name]
        if field.node is not None and isinstance(item, list):
            for idx, child in enumerate(item):
                child_path = f"{path}/{field_name}/{idx}"
                child_subj = iri(f"{target.base}{target.record_id}/{child_path}")
                target.lines.append(_quad(target, subj, field.property, child_subj))
                child_spec = kgterms.NODES[field.node]
                _emit_node(target, child_subj, child_spec, cast("JsonObject", child), child_path)
        elif field.record:
            target.lines.append(_quad(target, subj, field.property, iri(target.base + js_string(item))))
        else:
            obj = _literal(target.mapping, item, datatype=field.datatype, scheme=field.scheme)
            if obj is not None:
                target.lines.append(_quad(target, subj, field.property, obj))


def _emit_missing_attribute(target: _EmitTarget, subject: str, entries: Mapping[str, JsonObject]) -> None:
    missing_type = iri(expand(target.mapping, kgterms.MISSING_SPEC.rdf_class))
    for field_name, raw_entry in entries.items():
        missing_subj = iri(f"{target.base}{target.record_id}/missing/{field_name}")
        target.lines.append(_quad(target, subject, kgterms.MISSING_SPEC.property, missing_subj))
        target.lines.append(_quad(target, missing_subj, "rdf:type", missing_type))
        target.lines.append(
            _quad(target, missing_subj, kgterms.MISSING_SPEC.field, f'"{escape_literal(field_name)}"')
        )
        target.lines.append(
            _quad(
                target,
                missing_subj,
                kgterms.MISSING_SPEC.reason,
                iri(scheme_iri("missingness") + cast("str", raw_entry["reason"])),
            )
        )
        note = raw_entry.get("note")
        if note:
            target.lines.append(
                _quad(
                    target,
                    missing_subj,
                    kgterms.MISSING_SPEC.note,
                    f'"{escape_literal(cast("str", note))}"',
                )
            )


def _emit_scalar_or_ref(
    target: _EmitTarget, subject: str, prop: str, enc: kgterms.Encoding, value: object
) -> None:
    items = value if isinstance(value, list) else [value]
    for item in items:
        if enc.kind == "record":
            obj: str | None = iri(target.base + js_string(cast("JsonValue", item)))
        elif enc.kind == "concept":
            obj = _literal(target.mapping, item, scheme=enc.scheme)
        elif enc.kind == "iri":
            obj = _literal(target.mapping, item, is_iri=True)
        else:
            obj = _literal(target.mapping, item, datatype=enc.datatype)
        if obj is not None:
            target.lines.append(_quad(target, subject, prop, obj))


def _emit_attributes(target: _EmitTarget, record: Record, subject: str) -> None:
    declared = REGISTRY.get(record.kind, {})
    for key, value in record.attributes.items():
        declared_type = declared.get(key)
        if not declared_type or key in kgterms.NOT_EXPORTED:
            continue
        enc = kgterms.encoding(declared_type)
        prop = kgterms.attribute_property(target.mapping, key)
        if enc.kind == "missing":
            _emit_missing_attribute(target, subject, cast("dict[str, JsonObject]", value))
        elif enc.kind == "node" and enc.node is not None:
            node_subj = iri(f"{target.base}{target.record_id}/{key}")
            target.lines.append(_quad(target, subject, prop, node_subj))
            _emit_node(target, node_subj, kgterms.NODES[enc.node], cast("JsonObject", value), key)
        elif enc.kind == "literal" and enc.datatype == "rdf:JSON":
            rdf_json_dt = iri(expand(target.mapping, "rdf:JSON"))
            json_obj = f'"{escape_literal(canonical_json(value))}"^^{rdf_json_dt}'
            target.lines.append(_quad(target, subject, prop, json_obj))
        else:
            _emit_scalar_or_ref(target, subject, prop, enc, value)


def _emit_types_and_core(target: _EmitTarget, record: Record, subject: str) -> None:
    classes = cast("dict[str, dict[str, list[str]]]", target.mapping["classes"])
    kind_info = classes.get(record.kind)
    if kind_info is None:
        raise StoreError(f"No class mapping for kind {record.kind}")
    target.lines.extend(
        _quad(target, subject, "rdf:type", iri(expand(target.mapping, rdf_type)))
        for rdf_type in kind_info["types"]
    )
    raw_field = record.attributes.get("field")
    field_text = js_string(raw_field) if raw_field is not None else ""
    if record.kind == "claim" and field_text.startswith("links:assessed_by:"):
        target.lines.append(
            _quad(target, subject, "rdf:type", iri(expand(target.mapping, "rb:RelevanceJudgement")))
        )
    fields = cast("dict[str, dict[str, JsonValue]]", target.mapping["fields"])
    core_values = (("name", record.name), ("description", record.description), ("status", record.status))
    for field_name, val in core_values:
        term = fields[field_name]
        obj = _term_literal(target.mapping, val, term)
        if obj is not None and val != "":
            target.lines.append(_quad(target, subject, cast("str", term["property"]), obj))
    source_prop = cast("str", fields["source_ids"]["property"])
    target.lines.extend(
        _quad(target, subject, source_prop, iri(target.base + src_id)) for src_id in record.source_ids
    )


def _emit_facets_and_links(target: _EmitTarget, record: Record, subject: str) -> None:
    facet_terms = cast("dict[str, dict[str, JsonValue]]", target.mapping["facets"])
    for facet_name, facet_values in record.facets.items():
        facet_term = facet_terms.get(facet_name)
        if facet_term is None:
            continue
        prop = cast("str", facet_term["property"])
        for val in facet_values:
            obj = _term_literal(target.mapping, val, facet_term)
            if obj is not None:
                target.lines.append(_quad(target, subject, prop, obj))
    relation_terms = cast("dict[str, dict[str, str]]", target.mapping["relations"])
    for link in record.links:
        rel_entry = relation_terms.get(link.relation)
        if rel_entry is None:
            raise StoreError(f"No property mapping for relation {link.relation}")
        target.lines.append(_quad(target, subject, rel_entry["property"], iri(target.base + link.target_id)))


def _emit_review(target: _EmitTarget, record: Record, subject: str) -> None:
    review = record.attributes.get("review")
    if not isinstance(review, dict):
        return
    review_terms = cast("dict[str, dict[str, JsonValue]]", target.mapping["review"])
    for review_key, review_term in review_terms.items():
        if review_key not in review:
            continue
        raw_val = review[review_key]
        items = raw_val if isinstance(raw_val, list) else [raw_val]
        prop = cast("str", review_term["property"])
        for item in items:
            obj = _term_literal(target.mapping, item, review_term)
            if obj is not None:
                target.lines.append(_quad(target, subject, prop, obj))


def record_quads(mapping: Mapping[str, object], record: Record) -> list[str]:
    """Project one `Record` into unsorted N-Quads lines (`export.ts:recordQuads`)."""
    base = cast("str", mapping["base"])
    target = _EmitTarget(mapping, iri(cast("str", mapping["graph"])), base, record.id, [])
    subject = iri(base + record.id)
    _emit_types_and_core(target, record, subject)
    _emit_facets_and_links(target, record, subject)
    _emit_attributes(target, record, subject)
    _emit_review(target, record, subject)
    return target.lines


def export_quad_lines(mapping: Mapping[str, object], records: Sequence[Record]) -> list[str]:
    """Deduplicated, UTF-16 sorted N-Quads lines for all public records."""
    unique: set[str] = set()
    for record in public_records(records):
        unique.update(record_quads(mapping, record))
    return sorted(unique, key=utf16_key)


def subset_quad_lines(
    mapping: Mapping[str, object], records: Sequence[Record], ids: Iterable[str]
) -> SubsetResult:
    """Subset N-Quads for checked records, referrers, and one-hop target context."""
    quads = {record.id: record_quads(mapping, record) for record in public_records(records)}
    base = cast("str", mapping["base"])
    prefix = f"<{base}"

    def targets(record_id: str) -> set[str]:
        found: set[str] = set()
        for line in quads.get(record_id, ()):
            at = line.find(prefix, 1)
            while at != -1:
                end = line.find(">", at)
                target = line[at + len(prefix) : end]
                if target != record_id and target in quads:
                    found.add(target)
                at = line.find(prefix, at + 1)
        return found

    checked = {record_id for record_id in ids if record_id in quads}
    for record_id in quads:
        if record_id not in checked and any(target in checked for target in targets(record_id)):
            checked.add(record_id)
    context: set[str] = set()
    for record_id in checked:
        for target in targets(record_id):
            if target not in checked:
                context.add(target)
    raw_lines = {line for record_id in (*checked, *context) for line in quads[record_id]}
    return SubsetResult(
        lines=sorted(raw_lines, key=utf16_key),
        checked=sorted(checked, key=utf16_key),
        context=[base + record_id for record_id in sorted(context, key=utf16_key)],
    )


def write_quads(file: Path, lines: Sequence[str], *, chunk_size: int = 10000) -> str:
    """Write N-Quads lines in chunks matching `export.ts:writeQuads` and return SHA-256 hex."""
    file.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with file.open("wb") as handle:
        for start in range(0, len(lines), chunk_size):
            chunk = js_utf8("\n".join(lines[start : start + chunk_size]) + "\n")
            digest.update(chunk)
            handle.write(chunk)
    return digest.hexdigest()


def load_ontology_mapping(root: Path) -> JsonObject:
    """Read `data/ontology/mapping.json` as a raw JSON object (`export.ts:readMapping`)."""
    path = root / MAPPING_FILE
    parsed = js_parse(path.read_bytes().decode("utf-8"))
    if not isinstance(parsed, dict):
        raise StoreError(f"{MAPPING_FILE} is not a JSON object")
    return parsed


def declared_in_rb(root: Path) -> set[str]:
    """Read `rb:` classes and properties already declared in `data/ontology/rb.ttl`."""
    text = (root / "data/ontology/rb.ttl").read_bytes().decode("utf-8", errors="replace")
    return set(_RB_DECL_RE.findall(text))


def write_context(root: Path, mapping: Mapping[str, object] | None = None) -> Path:
    """Write `data/ontology/context.jsonld` and return its path."""
    loaded = load_ontology_mapping(root) if mapping is None else mapping
    target = root / CONTEXT_FILE
    write_text(target, json.dumps(build_context(loaded), indent=2, ensure_ascii=False) + "\n")
    return target


def write_terms(root: Path, mapping: Mapping[str, object] | None = None) -> tuple[Path, Path]:
    """Write `data/ontology/rb-attributes.ttl` and `attribute-shapes.ttl`."""
    loaded = load_ontology_mapping(root) if mapping is None else mapping
    decl_target = root / ATTRIBUTE_DECLARATIONS_FILE
    shapes_target = root / ATTRIBUTE_SHAPES_FILE
    write_text(decl_target, kgterms.declarations_turtle(loaded, declared_in_rb(root)))
    write_text(shapes_target, kgterms.shapes_turtle(loaded))
    return decl_target, shapes_target


def write_draft_block(
    root: Path,
    out: Path,
    mapping: Mapping[str, object] | None = None,
    schemes: Mapping[str, Scheme] | None = None,
) -> Path:
    """Write the manifest `draft` block JSON file."""
    loaded_mapping = load_ontology_mapping(root) if mapping is None else mapping
    loaded_schemes = load_schemes(root) if schemes is None else schemes
    block = kgdraft.draft_block(loaded_mapping, loaded_schemes)
    write_text(out, js_indented(block) + "\n")
    return out


def export_store(root: Path) -> tuple[Path, int, str]:
    """Export public records into `public/kg/asserted.nq` and `manifest.json`."""
    mapping = load_ontology_mapping(root)
    records = load_records(root)
    schemes = load_schemes(root)
    lines = export_quad_lines(mapping, records)

    release_manifest = root / "public/omics/manifest.json"
    release = (
        cast("JsonObject", json.loads(release_manifest.read_text("utf-8")))
        if release_manifest.exists()
        else cast("JsonObject", {"release_id": "unreleased"})
    )
    release_id = cast("str", release["release_id"])
    kg_dir = root / "public/kg"
    asserted_sha = write_quads(kg_dir / "asserted.nq", lines)
    mapping_sha = hashlib.sha256((root / MAPPING_FILE).read_bytes()).hexdigest()
    manifest: JsonObject = {
        "format_version": "1.0",
        # Keep dataset identifier aligned with scripts/kg/export.ts:324.
        "dataset": "rewire-benchmark-data",
        "release_id": release_id,
        "files": {"asserted.nq": {"sha256": asserted_sha, "quads": len(lines)}},
        "mapping_sha256": mapping_sha,
        "draft": kgdraft.draft_block(mapping, schemes),
        "inferred": None,
        "note": "Asserted statements only. Inference and SHACL validation are a later build step.",
    }
    write_text(kg_dir / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return kg_dir, len(lines), release_id


def export_subset(root: Path, ids: Iterable[str] | Path, out_dir: Path) -> SubsetResult:
    """Write `asserted.nq` and `context.txt` for subset SHACL validation."""
    mapping = load_ontology_mapping(root)
    records = load_records(root)
    id_list = (
        [part for part in _WS_SPLIT.split(ids.read_text("utf-8")) if part]
        if isinstance(ids, Path)
        else list(ids)
    )
    subset = subset_quad_lines(mapping, records, id_list)
    write_quads(out_dir / "asserted.nq", subset.lines)
    write_text(out_dir / "context.txt", "".join(f"{item}\n" for item in subset.context))
    return subset
