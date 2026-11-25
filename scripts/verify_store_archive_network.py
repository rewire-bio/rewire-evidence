"""Check that this library matches the TypeScript archive network modules and licence resolver.

Reads a checkout of `rewire-benchmark-data` at the pinned `STORE_REF`, holds every P14
TypeScript module digest, all 1,497 `read_targets` rows, all recorded scenarios and
unit fixtures in `tests/fixtures/archive-network.json`, and all four committed
2026-11-17 licence resolution files. When `--resolve-root` points at `QUEUE_REF`, it
synthesizes a zero-network API cache from committed 2026-11-17 candidates and receipts
and asserts byte-for-byte regeneration of all four 2026-11-17 files.

Usage: python scripts/verify_store_archive_network.py ../rewire-benchmark-data
       python scripts/verify_store_archive_network.py <store-ref> --resolve-root <queue-ref> \
           --json report.json

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import archive_network_gate

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / "scripts/expected/store-archive-network.json"
FIXTURE = ROOT / "tests/fixtures/archive-network.json"

if __name__ == "__main__":
    sys.exit(archive_network_gate(sys.argv[1:], EXPECTED, FIXTURE, __doc__))
