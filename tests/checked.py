"""What the TypeScript checks pack recorded, and the helpers the tests read it with.

`tests/fixtures/checks.json` is written by `scripts/fixtures/record_checks_fixture.mjs` in
CI, from `scripts/omics/checks/` and `scripts/omics/extract/tables.ts` at the pinned commit.
Nothing in it was written by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rewire_evidence.checks import CHECKS, Check, CheckInput, MetricConcept, UnitConcept, gathered

CHECKED: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/checks.json").read_text("utf-8"))


def named(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Cases by name, which has to be theirs alone."""
    by_name = {case["name"]: case for case in cases}
    assert len(by_name) == len(cases)
    return by_name


def given_of(case: dict[str, Any]) -> CheckInput:
    """The input of a recorded run: its records and its two vocabularies."""
    return gathered(
        case["records"],
        {key: MetricConcept(key, direction) for key, direction in case.get("metrics", [])},
        {key: UnitConcept(key, note) for key, note in case.get("units", [])},
    )


def only_of(case: dict[str, Any]) -> tuple[Check, ...]:
    """The checks a recorded run was made with: the ones it names, or the pack."""
    only = case.get("only")
    return CHECKS if only is None else tuple(check for check in CHECKS if check.id in only)


def lay_out(root: Path, files: dict[str, str]) -> Path:
    """Write a case's files under a folder, as the recorder did."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root
