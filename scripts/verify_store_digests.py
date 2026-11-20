"""Check this library's serialisation against every record the TypeScript store committed.

Reads a `rewire-benchmark-data` checkout and nothing else. Writes nothing anywhere.

  data/provenance/records/<shard>.jsonl   the committed digest for every record
  data/entities/<kind>.jsonl              entity records
  data/evidence/<kind>/<shard>.jsonl      evaluations, results and claims

For each record it recomputes `sha256(JSON.stringify(record))` with `rewire_evidence.canonical`
and compares it against the digest the Node implementation wrote. This is the acceptance
gate for the determinism kernel: the store is the oracle, and at 93,216 records it is large
enough that a passing run means the two implementations agree rather than that the test was
weak. Anything short of every record is a failure, and the first few mismatches are printed
with the committed and the computed digest.

The work is in `rewire_evidence.parity` and `rewire_evidence.gates`, where the tests cover
it. This file is the command.

Usage: python scripts/verify_store_digests.py ../rewire-benchmark-data

Exit status: 0 when every record reproduces, 1 when one does not or has no provenance row,
2 when the run could not be made.
"""

from __future__ import annotations

import sys

from rewire_evidence.gates import digests_gate

if __name__ == "__main__":
    sys.exit(digests_gate(sys.argv[1:], __doc__))
