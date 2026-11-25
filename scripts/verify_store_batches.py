"""Check shared batch tooling invariants and per-wave store batch reproduction.

Reads a checkout of `rewire-benchmark-data` at the pinned `STORE_REF` and measures all
Python batch directories under `data/omics/**/`:

- Inventory counts (batch dirs, Python scripts, line counts, drifted `lib.py` digests).
- Per-wave row validation against `schema.parse_record` and `data/provenance/records/`.
- Isolated `python3 -I` reproduction of self-contained builders in temporary copies.
- Complete list of non-reproducing batches with explicit reasons (`reproduced + len == total`).

Usage: python scripts/verify_store_batches.py ../rewire-benchmark-data
       python scripts/verify_store_batches.py ../rewire-benchmark-data --json report.json

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import batches_gate

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / "scripts/expected/store-batches.json"

if __name__ == "__main__":
    sys.exit(batches_gate(sys.argv[1:], EXPECTED, __doc__))
