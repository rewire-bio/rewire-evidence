"""Check that this library exports the KG and evaluates SHACL shapes as the TypeScript does.

Reads a checkout of `rewire-benchmark-data` at the pinned `STORE_REF` and holds:

- Every asserted N-Quads line of `public/kg/asserted.nq` against `scripts/expected/store-kg.json`
  (overall SHA-256, every 50,000-line chunk digest, record count, and asserted statement count).
- Generated `data/ontology/context.jsonld`, `rb-attributes.ttl`, `attribute-shapes.ttl`, and manifest
  `draft` block against their recorded digests.
- Each recorded P5 validator fault and P6 write-path case in `tests/fixtures/kg.json` against both
  subset N-Quads export and `pyshacl` `--shapes-only` verdicts.

Usage: python scripts/verify_store_kg.py ../rewire-benchmark-data
       python scripts/verify_store_kg.py ../rewire-benchmark-data --json report.json

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import kg_gate

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / "scripts/expected/store-kg.json"
KG_FIXTURE = ROOT / "tests/fixtures/kg.json"
VALIDATION_FIXTURE = ROOT / "tests/fixtures/validation.json"

if __name__ == "__main__":
    sys.exit(kg_gate(sys.argv[1:], EXPECTED, (KG_FIXTURE, VALIDATION_FIXTURE), __doc__))
