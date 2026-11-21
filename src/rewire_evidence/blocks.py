"""The structured blocks a record may carry, and what each must cite.

Ported from `shared/omics/benchmark-research.ts`, `run-guide.ts`, `run-recipe.ts`,
`profile-schema.ts`, `private-fields.ts`, `lib/omics-profile.ts` and
`scripts/omics/extensions.ts`. Each block has a zod schema, and most have a rule the schema
cannot state: which kind of record may carry it, and that every source it cites is a source
record, cited by the record itself, with a pinned artifact hash.

`validateRecords` applies them to each record in turn. A block of the wrong shape is refused
with zod's issues, as `SchemaError`. Any other fault is a `RecordError` with the TypeScript's
message.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final, cast

from rewire_evidence import zod
from rewire_evidence.attributes import said
from rewire_evidence.canonical import JsonObject, JsonValue
from rewire_evidence.js import MISSING, Held, truthy
from rewire_evidence.schema import BENCHMARK_SUBJECT_KINDS, Issue, Record, RecordError, SchemaError

#: Member names that must never enter a public release, wherever they appear in a record.
PRIVATE_FIELD_NAMES = (
    "email",
    "email_address",
    "contact_email",
    "token",
    "token_hash",
    "private_correspondence",
    "owner_uid",
    "id_token",
    "session_token",
    "access_token",
    "private_notes",
    "password",
    "verification_token",
)
_PRIVATE = frozenset(PRIVATE_FIELD_NAMES)
_HASH = re.compile(r"[a-f0-9]{64}")
_DATE = zod.text(zod.pattern(re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}"), r"^\d{4}-\d{2}-\d{2}$"))
_NONEMPTY = zod.NONEMPTY
_SOME = zod.array(_NONEMPTY, zod.min_length(1))


def assert_no_private_fields(
    value: JsonValue, message: str = "Private data cannot enter a public catalogue release."
) -> None:
    """Refuse a value holding a member with a private field's name, at any depth.

    Names are compared in lowercase. The message never holds the value.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _PRIVATE:
                raise RecordError(message)
            assert_no_private_fields(child, message)
    elif isinstance(value, list):
        for child in value:
            assert_no_private_fields(child, message)


def parsed(schema: zod.Schema, value: Held) -> JsonValue:
    """`schema.parse(value)`: the value as zod hands it back, or `SchemaError` with its issues."""
    result = schema.run(value)
    if result.issues:
        raise SchemaError([Issue(issue.code, issue.path, issue.message) for issue in result.issues])
    return cast("JsonValue", result.value)


def pinned(source: Record) -> bool:
    """Whether a source's artifact hash is written down, as the TypeScript's pattern reads it."""
    return bool(_HASH.fullmatch(said(source.attributes.get("artifact_sha256", MISSING))))


def _texts(value: JsonValue) -> list[str]:
    return cast("list[str]", value)


def _cited(record: Record, by_id: Mapping[str, Record], ids: Iterable[str], what: str) -> None:
    """Every ID is a source the record itself cites, with a pinned artifact, or `what` is refused."""
    for source_id in ids:
        source = by_id.get(source_id)
        if (
            source is None
            or source.kind != "source"
            or source_id not in record.source_ids
            or not pinned(source)
        ):
            raise RecordError(f"{what} {record.id} -> {source_id}")


# ---- benchmark_research ---------------------------------------------------------------

BENCHMARK_RESEARCH: Final = zod.strict(
    {
        "review_date": _DATE,
        "status": _NONEMPTY,
        "primary_sources": _SOME,
        "inspected_locators": _SOME,
        "searched_queries": _SOME,
        "gaps": zod.array(_NONEMPTY),
        "claim_scope": _NONEMPTY,
    }
)


def validate_benchmark_research(record: Record, by_id: Mapping[str, Record]) -> None:
    """A literature audit belongs to an assessment record and cites papers the record cites."""
    if "benchmark_research" not in record.attributes:
        return
    if record.kind not in BENCHMARK_SUBJECT_KINDS:
        raise RecordError("Literature audit must belong to a benchmark")
    audit = cast("JsonObject", parsed(BENCHMARK_RESEARCH, record.attributes["benchmark_research"]))
    for source_id in _texts(audit["primary_sources"]):
        source = by_id.get(source_id)
        if source is None or source.kind != "source" or source_id not in record.source_ids:
            raise RecordError(f"Unlinked benchmark paper: {record.id} -> {source_id}")


# ---- run_documentation and run_guide ---------------------------------------------------

_CITATION: Final = {"source_ids": _SOME, "source_locator": _NONEMPTY}
RUN_GUIDE: Final = zod.strict(
    {
        "record_id": _NONEMPTY,
        "summary": _NONEMPTY,
        "status": zod.one_of("source_reviewed_not_executed"),
        "prerequisites": _SOME,
        "steps": zod.array(
            zod.strict({"title": _NONEMPTY, "shell": _NONEMPTY, "explanation": _NONEMPTY, **_CITATION}),
            zod.min_length(1),
        ),
        "outputs": zod.array(_NONEMPTY),
        "limitations": _SOME,
        "source_ids": _SOME,
        "review": zod.strict({"method": zod.one_of("official_repository_review"), "date": _DATE}),
    }
)
RUN_DOCUMENTATION: Final = zod.strict(
    {
        "record_id": _NONEMPTY,
        **_CITATION,
        "status": zod.one_of("official_documentation_linked", "source_reviewed_not_executed"),
        "summary": _NONEMPTY,
    }
)


def validate_run_guide(record: Record, by_id: Mapping[str, Record]) -> None:
    """Run documentation and a run guide belong to the assessment record they name."""
    attributes = record.attributes
    if "run_documentation" in attributes:
        documentation = cast("JsonObject", parsed(RUN_DOCUMENTATION, attributes["run_documentation"]))
        if record.kind not in BENCHMARK_SUBJECT_KINDS or documentation["record_id"] != record.id:
            raise RecordError("Invalid run documentation owner")
        _cited(record, by_id, _texts(documentation["source_ids"]), "Unpinned run documentation")
    if "run_guide" not in attributes:
        return
    if record.kind not in BENCHMARK_SUBJECT_KINDS:
        raise RecordError("Run instructions require a benchmark, task, protocol or evaluator")
    guide = cast("JsonObject", parsed(RUN_GUIDE, attributes["run_guide"]))
    if guide["record_id"] != record.id:
        raise RecordError("Run guide belongs to another record")
    steps = cast("list[JsonObject]", guide["steps"])
    cited = [*_texts(guide["source_ids"]), *(each for step in steps for each in _texts(step["source_ids"]))]
    _cited(record, by_id, cited, "Unpinned run-guide evidence")


# ---- run_recipes and reproduction ------------------------------------------------------

_PURPOSES = ("rescore_predictions", "generate_and_evaluate")
_NOT_EXECUTED = "source_reviewed_not_executed"


def _receipts(value: JsonValue, report: zod.Report) -> None:
    instructions = cast("list[JsonObject]", cast("JsonObject", value)["instructions"])
    for position, instruction in enumerate(instructions):
        receipt = instruction.get("validation_receipt")
        scope = receipt.get("scope") if isinstance(receipt, dict) else None
        if instruction["status"] == "smoke_tested" and scope == "full_protocol":
            report("A smoke receipt cannot claim full-protocol execution", "instructions", position)
        if instruction["status"] != _NOT_EXECUTED and not truthy(
            instruction.get("validation_receipt", MISSING)
        ):
            report("Execution claims require a validation receipt", "instructions", position)


RUN_RECIPE: Final = zod.strict(
    {
        "id": _NONEMPTY,
        "protocol_id": _NONEMPTY,
        "version": _NONEMPTY,
        "title": _NONEMPTY,
        "purpose": zod.one_of(*_PURPOSES),
        "summary": _NONEMPTY,
        "inputs": _SOME,
        "outputs": _SOME,
        "requirements": zod.strict(
            {
                "data": _NONEMPTY,
                "weights": _NONEMPTY,
                "licence": _NONEMPTY,
                "software": _NONEMPTY,
                "hardware": _NONEMPTY,
            }
        ),
        "instructions": zod.array(
            zod.strict(
                {
                    "runtime": zod.one_of("python", "command_line", "podman", "apptainer", "slurm"),
                    "title": _NONEMPTY,
                    "code": _NONEMPTY,
                    "status": zod.one_of(_NOT_EXECUTED, "smoke_tested", "executed"),
                    "validation_receipt": zod.optional(
                        zod.strict(
                            {
                                "source_id": _NONEMPTY,
                                "source_locator": _NONEMPTY,
                                "scope": zod.one_of("synthetic_smoke", "selected_assay", "full_protocol"),
                                "platform": _NONEMPTY,
                            }
                        )
                    ),
                    **_CITATION,
                }
            ),
            zod.min_length(1),
        ),
        "limitations": _SOME,
        **_CITATION,
    },
    zod.refinement(_receipts),
)
#: A record's recipes as `validateRunRecipes` parses them: at least one.
RUN_RECIPES: Final = zod.array(RUN_RECIPE, zod.min_length(1))
#: The recipes of a reproduction's owner, which may be none.
_ANY_RECIPES: Final = zod.array(RUN_RECIPE)
REPRODUCTION: Final = zod.strict(
    {
        "recipe_owner_id": _NONEMPTY,
        "recipe_id": _NONEMPTY,
        "applicability": zod.one_of(*_PURPOSES),
        "explanation": _NONEMPTY,
        **_CITATION,
    }
)


def _recipe_citations(recipe: JsonObject) -> list[str]:
    cited = _texts(recipe["source_ids"])[:]
    for instruction in cast("list[JsonObject]", recipe["instructions"]):
        cited.extend(_texts(instruction["source_ids"]))
        receipt = instruction.get("validation_receipt")
        if isinstance(receipt, dict):
            cited.append(cast("str", receipt["source_id"]))
    return cited


def validate_run_recipes(record: Record, by_id: Mapping[str, Record]) -> None:
    """Recipes belong to an assessment record, and a reproduction names one of its evaluation's."""
    attributes = record.attributes
    if "run_recipes" in attributes:
        if record.kind not in BENCHMARK_SUBJECT_KINDS:
            raise RecordError("Recipes require an evaluation-design record")
        recipes = cast("list[JsonObject]", parsed(RUN_RECIPES, attributes["run_recipes"]))
        if len({cast("str", recipe["id"]) for recipe in recipes}) != len(recipes):
            raise RecordError("Duplicate recipe IDs")
        for recipe in recipes:
            _cited(record, by_id, _recipe_citations(recipe), "Unpinned recipe evidence")
    if "reproduction" not in attributes:
        return
    if record.kind != "evaluation":
        raise RecordError("Recipe applicability belongs to an exact evaluation")
    reference = cast("JsonObject", parsed(REPRODUCTION, attributes["reproduction"]))
    owner = by_id.get(cast("str", reference["recipe_owner_id"]))
    held = MISSING if owner is None else owner.attributes.get("run_recipes", MISSING)
    owned = cast("list[JsonObject]", held) if _ANY_RECIPES.accepts(held) else []
    found = [each for each in owned if each["id"] == reference["recipe_id"]]
    if not found or found[0]["purpose"] != reference["applicability"]:
        raise RecordError("Unknown or incompatible evaluation recipe")
    if reference["recipe_owner_id"] not in [
        link.target_id for link in record.links if link.relation == "assessment"
    ]:
        raise RecordError("Recipe must belong to the evaluation's exact design record")
    _cited(record, by_id, _texts(reference["source_ids"]), "Unpinned recipe evidence")


# ---- profile --------------------------------------------------------------------------

_LOCATOR = zod.text(zod.TRIM, zod.min_length(1))
_EVIDENCE: Final = {"source_ids": _SOME, "source_locator": _LOCATOR}
_ASSERTION = zod.strict({"text": _NONEMPTY, **_EVIDENCE})


def _profile_rules(value: JsonValue, report: zod.Report) -> None:
    profile = cast("JsonObject", value)
    if truthy(profile.get("summary_source_ids", MISSING)) != truthy(
        profile.get("summary_source_locator", MISSING)
    ):
        report("Summary evidence needs both sources and a locator.")
    if profile["coverage"] == "limited" and not profile["gaps"]:
        report("Limited profiles must document evidence gaps.")
    if profile["coverage"] == "reviewed" and not profile["sections"]:
        report("Reviewed profiles need sourced explanation.")


PROFILE: Final = zod.strict(
    {
        "summary": _NONEMPTY,
        "summary_source_ids": zod.optional(_SOME),
        "summary_source_locator": zod.optional(_LOCATOR),
        "sections": zod.array(zod.strict({"title": _NONEMPTY, "body": _NONEMPTY, **_EVIDENCE})),
        "facts": zod.array(
            zod.strict(
                {
                    "label": _NONEMPTY,
                    "value": _NONEMPTY,
                    "status": zod.optional(
                        zod.one_of(
                            "source_checked", "unreported", "unextracted", "unavailable", "inapplicable"
                        )
                    ),
                    **_EVIDENCE,
                }
            )
        ),
        "strengths": zod.array(_ASSERTION),
        "limitations": zod.array(_ASSERTION),
        "diagram": zod.optional(
            zod.strict(
                {
                    "title": _NONEMPTY,
                    "steps": zod.array(_NONEMPTY, zod.min_length(2), zod.max_length(8)),
                    "caption": _NONEMPTY,
                    **_EVIDENCE,
                }
            )
        ),
        "coverage": zod.one_of("reviewed", "limited"),
        "gaps": zod.array(_NONEMPTY),
        "review": zod.strict(
            {"method": zod.one_of("automated_source_review"), "date": _DATE, "note": _NONEMPTY}
        ),
    },
    zod.refinement(_profile_rules),
)


def validate_profile_sources(profile: JsonObject, by_id: Mapping[str, Record]) -> None:
    """Every source a profile cites is a source record: `validateProfileSources`."""
    claims: list[JsonValue] = []
    if "summary_source_ids" in profile:
        claims.append({"source_ids": profile["summary_source_ids"]})
    for name in ("sections", "facts", "strengths", "limitations"):
        claims.extend(cast("list[JsonValue]", profile[name]))
    if "diagram" in profile:
        claims.append(profile["diagram"])
    for claim in claims:
        for source_id in _texts(cast("JsonObject", claim)["source_ids"]):
            source = by_id.get(source_id)
            if source is None or source.kind != "source":
                raise RecordError(f"Profile cites missing source {source_id}")


# ---- extensions -----------------------------------------------------------------------

_TEXT = zod.nullish(_NONEMPTY)
_QUANTITY = zod.nullish(zod.strict({"value": zod.number(), "unit": _NONEMPTY}))
_COUNT = zod.nullish(zod.number(zod.INT, zod.NONNEGATIVE))


def _coordinates(value: JsonValue, report: zod.Report) -> None:
    held = cast("dict[str, float | None]", value)
    start, end = held.get("start"), held.get("end")
    if start is not None and end is not None and end < start:
        report("Coordinate end precedes start")
    offset, window = held.get("variant_offset"), held.get("window_length")
    if offset is not None and window is not None and offset >= window:
        report("Zero-based variant offset is outside sequence window")


EXTENSIONS: Final = zod.strict(
    {
        "genomics": zod.optional(
            zod.strict(
                {
                    "assembly": _TEXT,
                    "chromosome": _TEXT,
                    "coordinate_system": zod.nullish(zod.one_of("0-based-half-open", "1-based-closed")),
                    "start": _COUNT,
                    "end": _COUNT,
                    "strand": zod.nullish(zod.one_of("+", "-", "unstranded")),
                    "window_length": zod.nullish(zod.number(zod.INT, zod.POSITIVE)),
                    "variant_offset": _COUNT,
                    "orientation": zod.nullish(zod.one_of("genomic-forward", "transcript", "assay")),
                    "window_placement": _TEXT,
                },
                zod.refinement(_coordinates),
            )
        ),
        "protein": zod.optional(
            zod.strict(
                {
                    "sequence_identity_threshold": zod.nullish(
                        zod.number(zod.greater(0, inclusive=True), zod.less(1, inclusive=True))
                    ),
                    "identity_algorithm": _TEXT,
                    "msa_source": _TEXT,
                    "msa_version": _TEXT,
                    "templates": _TEXT,
                    "template_cutoff": _TEXT,
                    "structure_availability": _TEXT,
                }
            )
        ),
        "cellular": zod.optional(
            zod.strict(
                {
                    "donor": _TEXT,
                    "cell_type": _TEXT,
                    "perturbation": _TEXT,
                    "dose": _QUANTITY,
                    "time": _QUANTITY,
                    "biological_replicates": _COUNT,
                    "batch": _TEXT,
                }
            )
        ),
        "molecular_measurement": zod.optional(
            zod.strict(
                {
                    "molecule_identifier": _TEXT,
                    "identifier_namespace": _TEXT,
                    "assay_platform": _TEXT,
                    "preprocessing": _TEXT,
                    "measurement_unit": _TEXT,
                    "ion_adduct": _TEXT,
                    "identification_resolution": _TEXT,
                }
            )
        ),
        "microbial": zod.optional(
            zod.strict(
                {
                    "taxonomy": _TEXT,
                    "taxonomy_version": _TEXT,
                    "reference_database": _TEXT,
                    "reference_version": _TEXT,
                    "sample_context": _TEXT,
                }
            )
        ),
        "mechanistic": zod.optional(
            zod.strict(
                {
                    "solver": _TEXT,
                    "solver_version": _TEXT,
                    "constraints": _TEXT,
                    "experimental_conditions": _TEXT,
                }
            )
        ),
    }
)
