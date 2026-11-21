"""Check that this library validates a store as the TypeScript does.

Reads a `rewire-benchmark-data` checkout and nothing else. Writes nothing to it.

Every committed record has to pass every validator `npm run records -- check` applies: the
attribute registry, derivations, descriptions, evidence concerns, and the whole of
`validateRecords`, comparison panels included. The store passes them all, so passing proves
little on its own. A validator that looked at nothing would pass too.

What ties the run to the TypeScript is `scripts/expected/store-validation.json`, which
`scripts/fixtures/record_validation_fixture.mjs` records in CI from the data repository's
own code at the pinned commit: how many attribute values there are of each declared type,
how many reasons for a missing value, how many of each structured block, every comparison
panel as the catalogue query resolves it with the results it shows, and the words each
validator refuses a seeded fault in. Everything measured here has to equal it, and each
seeded fault is put into the whole store and has to be refused in the recorded words.

The work is in `rewire_evidence.agreement` and `rewire_evidence.gates`, where the tests
cover it. This file is the command.

Usage: python scripts/verify_store_validation.py ../rewire-benchmark-data
       python scripts/verify_store_validation.py ../rewire-benchmark-data --json report.json

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be
made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import validation_gate

EXPECTED = Path(__file__).parent / "expected" / "store-validation.json"

if __name__ == "__main__":
    sys.exit(validation_gate(sys.argv[1:], EXPECTED, __doc__))
