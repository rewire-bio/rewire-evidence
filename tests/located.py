"""What the TypeScript locator grammar recorded, and the helpers the tests read it with.

`tests/fixtures/locator.json` is written by `scripts/fixtures/record_locator_fixture.mjs` in
CI, from `shared/omics/locator.ts` and `scripts/omics/locator-cli.ts` at the pinned commit.
Nothing in it was written by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rewire_evidence.locator import Address, Anchor, Qualifier, Selector

LOCATOR: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/locator.json").read_text("utf-8"))


def anchors_of(rows: list[dict[str, Any]]) -> list[Anchor]:
    return [Anchor(row["kind"], row["value"], row["raw"], row.get("container")) for row in rows]


def addresses_of(rows: list[dict[str, Any]]) -> list[Address]:
    return [Address(row["kind"], row["value"], row["raw"], row.get("value2")) for row in rows]


def selectors_of(rows: list[dict[str, Any]]) -> list[Selector]:
    return [Selector(row["key"], row["value"], row["form"], row["raw"]) for row in rows]


def qualifiers_of(rows: list[dict[str, Any]]) -> list[Qualifier]:
    return [Qualifier(row["kind"], row["text"]) for row in rows]
