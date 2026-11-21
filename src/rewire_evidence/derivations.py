"""Results computed from a source's own supplementary data: `scripts/omics/derivations.ts`.

A result's `attributes.derivation` says a value was computed and not printed. Its shape is
checked with the other attributes. This checks what needs the store and the repository: the
script exists and matches its hash, each input is a source record with the pinned artifact
hash, the inputs come from the publication that states the aggregation, and the evaluation
has an origin that names who ran it.

The TypeScript reads the derivation without checking its shape, because `validateAttributes`
runs first. Asked on its own about a derivation of another shape, it fails with a
JavaScript `TypeError` and reports nothing. `JsTypeError` is raised in the same places.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from rewire_evidence.attributes import JsTypeError, said
from rewire_evidence.canonical import JsonValue, js_utf8, sha256_hex
from rewire_evidence.js import MISSING, Held, strictly_equal, truthy
from rewire_evidence.schema import Record, RecordError

#: Origins whose evaluation was run by the source's authors or by another paper's authors.
DERIVABLE_ORIGINS = frozenset({"author_reported", "independent_paper", "unreported"})
#: How many problems a refusal lists before it stops.
PROBLEM_LIMIT = 20


def _member(value: JsonValue, name: str) -> Held:
    """`value.name` as JavaScript reads it: nothing on text, a number, a boolean or a list."""
    if value is None:
        raise JsTypeError(f"a derivation reads {name} of null")
    return value.get(name, MISSING) if isinstance(value, dict) else MISSING


class _Scripts:
    """The SHA-256 of each script a derivation names, read once."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.hashes: dict[str, str | None] = {}

    def hash_of(self, script: Held) -> str | None:
        if not isinstance(script, str):
            raise JsTypeError("a derivation's script is not text")
        if script not in self.hashes:
            # Joined as Node joins a path: a script that starts with a slash is still below the
            # root. Node names a file in UTF-8, with U+FFFD for half a character.
            file = os.path.normpath(f"{self.root}/{js_utf8(script).decode('utf-8')}")
            # `existsSync` answers false for a name no file system can hold, where
            # `Path.exists` raises: one too long, or one with a null character in it.
            self.hashes[script] = sha256_hex(Path(file).read_bytes()) if os.path.exists(file) else None  # noqa: PTH110
        return self.hashes[script]


def derivation_issues(records: Sequence[Record], root: Path) -> list[str]:  # noqa: C901, PLR0912 - one rule after another
    """Every fault in every derivation, in record order: `derivationIssues`.

    `root` is the repository the scripts are read from. Raises `JsTypeError` for a
    derivation whose shape the TypeScript cannot read, and `OSError` for a script that
    exists and cannot be read.
    """
    by_id = {record.id: record for record in records}
    scripts = _Scripts(root)
    issues: list[str] = []
    for record in records:
        derivation = record.attributes.get("derivation")
        if record.kind != "result" or not isinstance(derivation, (dict, list)):
            continue

        def fail(message: str, record: Record = record) -> None:
            issues.append(f"result {record.id}: derivation {message}")

        script = _member(derivation, "script")
        pinned = _member(derivation, "script_sha256")
        actual = scripts.hash_of(script)
        if actual is None:
            fail(f"script {said(script)} does not exist")
        elif not strictly_equal(actual, pinned):
            fail(f"script {said(script)} has sha256 {actual}, not {said(pinned)}")
        if record.attributes.get("numeric_value") is None:
            fail("needs a numeric_value")
        aggregation = _member(derivation, "aggregation_source_id")
        aggregation_source = by_id.get(aggregation) if isinstance(aggregation, str) else None
        if aggregation_source is None or aggregation_source.kind != "source":
            fail(f"aggregation source {said(aggregation)} is not a source record")
        inputs = _member(derivation, "inputs")
        if not isinstance(inputs, list):
            raise JsTypeError("a derivation's inputs are not a list")
        for cited in (aggregation, *(_member(each, "source_id") for each in inputs)):
            if not isinstance(cited, str) or cited not in record.source_ids:
                fail(f"source {said(cited)} is not in the result's source_ids")
        # The DOI is read from whatever record the ID names, a source or not.
        doi = MISSING if aggregation_source is None else aggregation_source.attributes.get("doi", MISSING)
        for each in inputs:
            source_id = _member(each, "source_id")
            source = by_id.get(source_id) if isinstance(source_id, str) else None
            if source is None or source.kind != "source":
                fail(f"input {said(source_id)} is not a source record")
                continue
            held = source.attributes.get("artifact_sha256", MISSING)
            wanted = _member(each, "artifact_sha256")
            if not strictly_equal(held, wanted):
                has = "none" if held is None or held is MISSING else said(held)
                fail(f"input {said(source_id)} pins sha256 {said(wanted)}, but the source record has {has}")
            # Only the source's own supplementary data counts: the input and the quoted
            # aggregation must come from the same publication.
            if not truthy(doi) or not strictly_equal(source.attributes.get("doi", MISSING), doi):
                fail(
                    f"input {said(source_id)} must be supplementary data of the publication that states "
                    "the aggregation (same doi)"
                )
        target = next((link.target_id for link in record.links if link.relation == "evaluation"), "")
        evaluation = by_id.get(target)
        if evaluation is not None:
            origin = evaluation.attributes.get("origin", MISSING)
            if said(origin) not in DERIVABLE_ORIGINS:
                fail(f"cannot be used on an evaluation with origin {said(origin)}")
    return issues


def validate_derivations(records: Sequence[Record], root: Path) -> None:
    """Refuse a derivation that cannot be traced to its script and sources: `validateDerivations`.

    Raises `RecordError` with the message the TypeScript throws.
    """
    issues = derivation_issues(records, root)
    if issues:
        listed = "\n".join(issues[:PROBLEM_LIMIT])
        more = "\n..." if len(issues) > PROBLEM_LIMIT else ""
        raise RecordError(f"{len(issues)} derivation problems:\n{listed}{more}")
