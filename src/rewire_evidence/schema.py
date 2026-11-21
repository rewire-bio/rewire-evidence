"""What a record is, ported from `scripts/omics/schema.ts` and the two modules it rests on.

`recordSchema` there is a zod object, and `recordSchema.parse` does three things a record's
identity depends on. It refuses a value that is not a record. It puts the nine top-level
members in the order the schema declares them, whatever order they arrived in, and the two
members of each link likewise. And it leaves the keys inside `facets` and `attributes` in
the order they were written. A record's digest is taken over the result, so a port that
orders anything differently gives the same record another identity.

`check_record` is `recordSchema.safeParse`, and `parse_record` is `recordSchema.parse`. What
zod reports for a refused value is reproduced issue for issue: the code, the path and the
message, in zod's order. The code and the path together are the rule identifier. None of
this was written from zod's documentation. `scripts/fixtures/record_schema_fixture.mjs` runs
the TypeScript in CI on every case in `tests/fixtures/record-schema.json`, and the tests hold
this module to what it recorded.

Things zod does that are easy to miss, each recorded there:

- A link is not a strict object: a member that is not `relation` or `target_id` is dropped.
- A member named `__proto__` directly inside `facets` or `attributes` is dropped without a
  word. At the top level it is an unrecognised key, and deeper inside an attribute it is kept.
- The length check on `name` and on a link's `target_id` runs on a value of the wrong type
  too, if the value has a `length`, and compares that length as JavaScript compares.

Also here: the kinds, the statuses and the relation rules (`shared/omics/entity-kinds.ts`,
`shared/omics/relations.ts`), `publicRecords`, and the checks `validateRecords` makes of
IDs, sources and links. The rest of `validateRecords` is a later stage.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from rewire_evidence.canonical import JsonObject, JsonValue, js_value
from rewire_evidence.js import MISSING, Missing, number_of, received
from rewire_evidence.jsonl import StoreError, text_field

#: The top-level members of a record, in the order `recordSchema` declares and writes them.
SHAPE = ("id", "kind", "name", "description", "status", "facets", "source_ids", "links", "attributes")

#: Every kind a record may have: `entityKinds` in `shared/omics/entity-kinds.ts`, in its order.
KINDS = (
    "model",
    "method",
    "configuration",
    "pipeline",
    "service",
    "benchmark",
    "task",
    "protocol",
    "evaluator",
    "dataset",
    "dataset_subset",
    "baseline",
    "evaluation",
    "result",
    "source",
    "claim",
    "use_case",
)
MODEL_SUBJECT_KINDS = ("model", "method", "configuration", "pipeline", "service")
BENCHMARK_SUBJECT_KINDS = ("benchmark", "task", "protocol", "evaluator")
DATASET_SUBJECT_KINDS = ("dataset", "dataset_subset")

#: Every status a record may have: `statuses` in `scripts/omics/schema.ts`, in its order.
STATUSES = (
    "discovered",
    "needs_review",
    "source_checked",
    "reproduced",
    "disputed",
    "superseded",
    "excluded",
)

#: `recordIdPattern` as JavaScript writes it, which is how zod's message quotes it.
RECORD_ID_PATTERN = "^[a-z0-9]+(?:-[a-z0-9]+)*$"
# Matched with `fullmatch`. Python's `$` also matches before a final line break and
# JavaScript's does not, so searching for the pattern as written would accept "a\n".
_RECORD_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

#: Statuses a result must have to be public.
_PUBLIC_RESULT_STATUSES = ("source_checked", "reproduced", "superseded")


@dataclass(frozen=True)
class RelationRule:
    """The kinds a relation may link from and to."""

    sources: tuple[str, ...]
    targets: tuple[str, ...]


_SYSTEMS = MODEL_SUBJECT_KINDS
_ASSESSMENTS = BENCHMARK_SUBJECT_KINDS
_DATA = DATASET_SUBJECT_KINDS

#: `relationRules` in `shared/omics/relations.ts`, in its order, which is the order of
#: `relations`. `applicable_to` lists `evaluator` twice there, and so it does here.
RELATION_RULES: dict[str, RelationRule] = {
    "system": RelationRule(("evaluation",), _SYSTEMS),
    "assessment": RelationRule(("evaluation",), _ASSESSMENTS),
    "data": RelationRule(("evaluation",), _DATA),
    "evaluation": RelationRule(("result",), ("evaluation",)),
    "original_evaluation": RelationRule(("evaluation",), ("evaluation",)),
    "implemented_by": RelationRule(("baseline",), _SYSTEMS),
    "measured_in": RelationRule(("baseline",), ("evaluation",)),
    "uses_data": RelationRule((*_ASSESSMENTS, "baseline"), _DATA),
    "used_in": RelationRule(_DATA, _ASSESSMENTS),
    "family": RelationRule(_SYSTEMS, _SYSTEMS),
    "variant_of": RelationRule(tuple(kind for kind in _SYSTEMS if kind != "configuration"), _SYSTEMS),
    "configuration_of": RelationRule(("configuration",), _SYSTEMS),
    "alias_of": RelationRule(_SYSTEMS, _SYSTEMS),
    "uses_model": RelationRule(_SYSTEMS, _SYSTEMS),
    "part_of": RelationRule(_ASSESSMENTS, _ASSESSMENTS),
    "parent": RelationRule(_ASSESSMENTS, _ASSESSMENTS),
    "evaluates_task": RelationRule(_ASSESSMENTS, _ASSESSMENTS),
    "applicable_to": RelationRule((*_SYSTEMS, "baseline"), (*_ASSESSMENTS, "evaluator")),
    "same_data_as": RelationRule(_DATA, _DATA),
    "companion_of": RelationRule(("source",), ("source",)),
    "assessed_by": RelationRule(("use_case",), ("protocol",)),
    "supersedes": RelationRule(KINDS, KINDS),
    "subject": RelationRule(("claim",), KINDS),
    "source": RelationRule(KINDS, ("source",)),
}
#: Every relation a link may name, in the order the TypeScript lists them.
RELATIONS = tuple(RELATION_RULES)


def relation_allows(relation: str, from_kind: str, to_kind: str) -> bool:
    """Whether a relation may link a record of one kind to a record of another.

    A name that is no relation is not allowed. The TypeScript looks the name up on a plain
    object, so for a name every JavaScript object has, such as `toString`, it throws a
    `TypeError` and gives no answer. No record reaches it with such a name, because the
    schema refuses an unknown relation first.
    """
    rule = RELATION_RULES.get(relation)
    return rule is not None and from_kind in rule.sources and to_kind in rule.targets


# `currentRelation` as a table: the kind a link leaves from, then the older name, then the
# current one. A name it does not list is already current.
_OLDER_NAMES: dict[str, dict[str, str]] = {
    "evaluation": {
        "model": "system",
        **dict.fromkeys(_SYSTEMS, "system"),
        "benchmark": "assessment",
        **dict.fromkeys(_ASSESSMENTS, "assessment"),
        **dict.fromkeys(_DATA, "data"),
    },
    "baseline": {
        "model": "implemented_by",
        **dict.fromkeys(_SYSTEMS, "implemented_by"),
        "evaluation": "measured_in",
        "dataset": "uses_data",
    },
    **{kind: {"benchmark": "used_in", "protocol": "used_in"} for kind in _DATA},
    **{kind: {"dataset": "uses_data"} for kind in _ASSESSMENTS},
    "configuration": {"variant_of": "configuration_of"},
}


def current_relation(from_kind: str, relation: str) -> str:
    """The current name of a link written before October 2026, given the kind it links from."""
    return _OLDER_NAMES.get(from_kind, {}).get(relation, relation)


@dataclass(frozen=True)
class Link:
    """One link of a record: the relation and the ID it points at."""

    relation: str
    target_id: str


@dataclass(frozen=True)
class Record:
    """A record the schema accepts, with its members typed.

    The members are declared in the order the schema writes them, and `row` gives them in
    that order, so a record built here cannot be written in another. `facets` and
    `attributes` keep whatever key order they were given, because that order is part of
    the record's identity. The class is frozen but the lists and dicts it holds are not:
    change one and the record is a different record with a different digest.
    """

    id: str
    kind: str
    name: str
    description: str
    status: str
    facets: dict[str, list[str]]
    source_ids: list[str]
    links: list[Link]
    attributes: JsonObject

    def row(self) -> JsonObject:
        """The record as the object `JSON.stringify` is given: the line the store holds.

        The facets, source IDs and attributes are the record's own and not copies.
        """
        links: list[JsonValue] = [
            {"relation": link.relation, "target_id": link.target_id} for link in self.links
        ]
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            # A list of text is a JSON value, which a type checker cannot see through `list`.
            "facets": cast("JsonObject", self.facets),
            "source_ids": cast("list[JsonValue]", self.source_ids),
            "links": links,
            "attributes": self.attributes,
        }


@dataclass(frozen=True)
class Issue:
    """One thing zod reports about a value that is not a record."""

    #: zod's issue code: `invalid_type`, `invalid_value`, `invalid_format`, `too_small`,
    #: `custom` or `unrecognized_keys`.
    code: str
    #: Where in the value: member names and list positions, outermost first.
    path: tuple[str | int, ...]
    #: zod's message, word for word.
    message: str

    @property
    def rule(self) -> str:
        """The rule identifier: the code and the path, as in `invalid_type:links.0.relation`."""
        return f"{self.code}:{'.'.join(str(part) for part in self.path)}"


class SchemaError(ValueError):
    """A value is not a record. `issues` holds everything zod reports about it, in its order."""

    def __init__(self, issues: Sequence[Issue]) -> None:
        """Keep the issues and say them."""
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.rule}: {issue.message}" for issue in self.issues))


class RecordError(Exception):
    """Records that break a rule of the store. The message is the one the TypeScript throws."""


@dataclass(frozen=True)
class Checked:
    """What `recordSchema.safeParse` gives: the record, or the issues that refuse it."""

    record: Record | None
    issues: tuple[Issue, ...]


type _Held = JsonValue | Missing
type _Path = tuple[str | int, ...]


def _wrong_type(expected: str, value: _Held, path: _Path) -> Issue:
    return Issue("invalid_type", path, f"Invalid input: expected {expected}, received {received(value)}")


def _at_least_one(value: _Held, path: _Path, issues: list[Issue]) -> None:
    """Zod's `.min(1)`, which runs on anything that has a `length` and not only on text.

    Text and a list have their length. An object has one if it has a member of that name,
    whatever the member holds, and zod then compares it with 1 as JavaScript's `>=` does.
    """
    if isinstance(value, str):
        enough, expected = bool(value), "string to have >=1 characters"
    elif isinstance(value, list):
        enough, expected = bool(value), "array to have >=1 items"
    elif isinstance(value, dict) and "length" in value:
        enough, expected = number_of(value["length"]) >= 1, "unknown to be >=1"
    else:
        return
    if not enough:
        issues.append(Issue("too_small", path, f"Too small: expected {expected}"))


def _text(value: _Held, path: _Path, issues: list[Issue]) -> str:
    """`z.string()`: the text, or an issue and an empty placeholder."""
    if isinstance(value, str):
        return value
    issues.append(_wrong_type("string", value, path))
    return ""


def _one_of(options: tuple[str, ...], value: _Held, path: _Path, issues: list[Issue]) -> str:
    """`z.enum(options)`: zod names the options and not what it was given."""
    if isinstance(value, str) and value in options:
        return value
    listed = "|".join(f'"{option}"' for option in options)
    issues.append(Issue("invalid_value", path, f"Invalid option: expected one of {listed}"))
    return ""


def _texts(value: _Held, path: _Path, issues: list[Issue]) -> list[str]:
    """`z.array(z.string())`."""
    if not isinstance(value, list):
        issues.append(_wrong_type("array", value, path))
        return []
    return [_text(item, (*path, position), issues) for position, item in enumerate(value)]


def _members(value: _Held, path: _Path, issues: list[Issue]) -> JsonObject:
    """The members `z.record(z.string(), ...)` looks at: all but `__proto__`, which it drops."""
    if not isinstance(value, dict):
        issues.append(_wrong_type("record", value, path))
        return {}
    return {key: held for key, held in value.items() if key != "__proto__"}


def _link(value: JsonValue, path: _Path, issues: list[Issue]) -> Link:
    """One link: a `z.object` that is not strict, so members it does not declare are dropped."""
    if not isinstance(value, dict):
        issues.append(_wrong_type("object", value, path))
        return Link("", "")
    relation_path = (*path, "relation")
    held = value.get("relation", MISSING)
    relation = _text(held, relation_path, issues)
    # The refinement runs only on text.
    if isinstance(held, str) and held not in RELATION_RULES:
        issues.append(Issue("custom", relation_path, "Unknown catalogue relationship"))
    target_path = (*path, "target_id")
    target = value.get("target_id", MISSING)
    target_id = _text(target, target_path, issues)
    _at_least_one(target, target_path, issues)
    return Link(relation, target_id)


def _links(value: _Held, issues: list[Issue]) -> list[Link]:
    if not isinstance(value, list):
        issues.append(_wrong_type("array", value, ("links",)))
        return []
    return [_link(item, ("links", position), issues) for position, item in enumerate(value)]


def check_record(value: object) -> Checked:
    """`recordSchema.safeParse(value)`: the record, or every issue zod reports, in its order.

    `value` is anything `JSON.parse` can return. It is not changed, and the record shares
    nothing with it. Raises `TypeError` for a value JSON has no form for, and `StoreError`
    for one nested too deeply to walk, which is not something zod reports.
    """
    try:
        held = js_value(value)
    except RecursionError as error:
        raise StoreError("the value is nested too deeply to read") from error
    if not isinstance(held, dict):
        return Checked(None, (_wrong_type("object", held, ()),))

    issues: list[Issue] = []
    held_id = held.get("id", MISSING)
    record_id = _text(held_id, ("id",), issues)
    if isinstance(held_id, str) and not _RECORD_ID.fullmatch(held_id):
        issues.append(
            Issue("invalid_format", ("id",), f"Invalid string: must match pattern /{RECORD_ID_PATTERN}/")
        )
    kind = _one_of(KINDS, held.get("kind", MISSING), ("kind",), issues)
    held_name = held.get("name", MISSING)
    name = _text(held_name, ("name",), issues)
    _at_least_one(held_name, ("name",), issues)
    description = _text(held.get("description", MISSING), ("description",), issues)
    status = _one_of(STATUSES, held.get("status", MISSING), ("status",), issues)
    facets = {
        key: _texts(values, ("facets", key), issues)
        for key, values in _members(held.get("facets", MISSING), ("facets",), issues).items()
    }
    source_ids = _texts(held.get("source_ids", MISSING), ("source_ids",), issues)
    links = _links(held.get("links", MISSING), issues)
    attributes = _members(held.get("attributes", MISSING), ("attributes",), issues)

    unknown = [key for key in held if key not in SHAPE]
    if unknown:
        listed = ", ".join(f'"{key}"' for key in unknown)
        issues.append(
            Issue("unrecognized_keys", (), f"Unrecognized key{'s' if len(unknown) > 1 else ''}: {listed}")
        )
    if issues:
        return Checked(None, tuple(issues))
    return Checked(
        Record(record_id, kind, name, description, status, facets, source_ids, links, attributes), ()
    )


def parse_record(value: object) -> Record:
    """`recordSchema.parse(value)`: the record, or `SchemaError` holding every issue."""
    checked = check_record(value)
    if checked.record is None:
        raise SchemaError(checked.issues)
    return checked.record


def _row_links(row: JsonObject, where: str) -> list[JsonObject]:
    links = row.get("links")
    if not isinstance(links, list):
        raise StoreError(f"{where} has no list 'links'")
    held: list[JsonObject] = []
    for link in links:
        if not isinstance(link, dict):
            raise StoreError(f"{where} has a link that is not a JSON object")
        held.append(link)
    return held


def _renamed_attributes(
    row: JsonObject, links: list[JsonObject], kind_of: dict[str, str]
) -> JsonObject | None:
    """A claim's attributes with `field` under the current relation name, or None if it is."""
    attributes = row.get("attributes")
    if not isinstance(attributes, dict):
        return None
    field = attributes.get("field")
    if not isinstance(field, str) or not field.startswith("links:"):
        return None
    _, relation, *rest = field.split(":")
    subjects = [link.get("target_id") for link in links if link.get("relation") == "subject"]
    subject = subjects[0] if len(subjects) == 1 else None
    subject_kind = kind_of.get(subject) if isinstance(subject, str) else None
    renamed = current_relation(subject_kind, relation) if subject_kind else relation
    if renamed == relation:
        return None
    return {**attributes, "field": ":".join(["links", renamed, *rest])}


def normalize_records(records: list[JsonObject]) -> list[JsonObject]:
    """Rename relation names used before October 2026, in links and in the claims citing them.

    A port of `normalizeRecords`, which reads releases written with the older names. Those
    are not records the current schema accepts, so this works on rows as they were read
    and not on `Record`. A claim cites a link as `links:<relation>:<target>` in its `field`
    attribute, and the relation there is renamed for the kind of the claim's one subject.

    Returns the list it was given when nothing needed renaming, and never changes a row it
    was given: a renamed row is a new row, with every key where it was. Raises `StoreError`
    for a row with no text `id` or `kind`, or whose links are not a list of objects.
    """
    kind_of: dict[str, str] = {}
    for number, row in enumerate(records, 1):
        where = f"record {number}"
        kind_of[text_field(row, "id", where)] = text_field(row, "kind", where)

    changed = False
    out: list[JsonObject] = []
    for number, row in enumerate(records, 1):
        kind = text_field(row, "kind", f"record {number}")
        links = _row_links(row, f"record {number}")
        current = row
        renamed: list[JsonValue] = []
        moved = False
        for link in links:
            relation = link.get("relation")
            name = current_relation(kind, relation) if isinstance(relation, str) else relation
            moved = moved or name != relation
            renamed.append({**link, "relation": name} if "relation" in link else link)
        if moved:
            current = {**row, "links": renamed}
        attributes = _renamed_attributes(row, links, kind_of) if kind == "claim" else None
        if attributes is not None:
            current = {**current, "attributes": attributes}
        changed = changed or current is not row
        out.append(current)
    return out if changed else records


def public_records(records: Sequence[Record]) -> list[Record]:
    """The records a public release may hold: `publicRecords` in `scripts/omics/schema.ts`.

    A record is blocked if it is excluded or disputed, or is a result that has not been
    checked against its source. A record that cites or links to a blocked record is blocked
    too, and so on until nothing more is.
    """
    blocked = {
        record.id
        for record in records
        if record.status in ("excluded", "disputed")
        or (record.kind == "result" and record.status not in _PUBLIC_RESULT_STATUSES)
    }
    changed = True
    while changed:
        changed = False
        for record in records:
            if record.id not in blocked and (
                any(source in blocked for source in record.source_ids)
                or any(link.target_id in blocked for link in record.links)
            ):
                blocked.add(record.id)
                changed = True
    return [record for record in records if record.id not in blocked]


def validate_dataset_reuse_link(record: Record, link: Link, target: Record) -> None:
    """`same_data_as` links two different datasets or dataset subsets, and nothing else."""
    if link.relation == "same_data_as" and (
        record.kind not in DATASET_SUBJECT_KINDS
        or target.kind not in DATASET_SUBJECT_KINDS
        or record.id == target.id
    ):
        raise RecordError(f"Invalid dataset reuse relationship on {record.id}")


def check_references(record: Record, by_id: Mapping[str, Record]) -> None:
    """One record's source IDs and links, held to the records they name.

    Every source ID names a source record. Every link points at a record that exists, of a
    kind its relation may reach from this record's kind. Raises `RecordError` for the first
    fault, with the message the TypeScript throws.
    """
    for source_id in record.source_ids:
        source = by_id.get(source_id)
        if source is None or source.kind != "source":
            raise RecordError(f"Missing source {source_id} for {record.id}")
    for link in record.links:
        target = by_id.get(link.target_id)
        if target is None:
            raise RecordError(f"Dangling {record.id} -> {link.target_id}")
        validate_dataset_reuse_link(record, link, target)
        if not relation_allows(link.relation, record.kind, target.kind):
            raise RecordError(
                f"Relationship {link.relation} cannot link {record.kind} {record.id} "
                f"to {target.kind} {link.target_id}"
            )


def by_unique_id(records: Sequence[Record]) -> dict[str, Record]:
    """The records by ID. Raises `RecordError` if two share one."""
    by_id = {record.id: record for record in records}
    if len(by_id) != len(records):
        raise RecordError("Duplicate record ID")
    return by_id


def validate_references(records: Iterable[Record]) -> None:
    """The checks `validateRecords` makes of IDs, sources and links, and no others.

    No two records share an ID, and each record passes `check_references`. Raises
    `RecordError` for the first fault, with the message the TypeScript throws.

    `validateRecords` makes these checks record by record, among its others, so a store
    with two faults can be refused for another one first. `validation.validate_records` is
    the whole of it, in its order.
    """
    held = list(records)
    by_id = by_unique_id(held)
    for record in held:
        check_references(record, by_id)
