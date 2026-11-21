"""Check that this library knows what a record is, as the TypeScript does.

Reads a `rewire-benchmark-data` checkout and nothing else. Writes nothing to it.

Every committed record has to be accepted by the schema and written back as the line it was
read from. Every record then has its top-level keys and the keys of its links put in another
order, and the schema has to restore the committed line: the stored records are already in
schema order, so without this a parser that reordered nothing would pass. Every link has to
be one its relation allows, every source ID has to name a source, and every controlled
value has to be a concept of its vocabulary.

What ties the run to the TypeScript is `scripts/expected/store-schema.json`, which
`scripts/fixtures/record_schema_fixture.mjs` records in CI from the data repository's own
code at the pinned commit: the records `loadRecords` accepts, their links by relation, the
records a public release may hold, the statements and concepts of every vocabulary as n3
reads them, the controlled values by field, and the message for a bad concept seeded in
each controlled field. Everything measured here has to equal it, so a thinner or an edited
checkout cannot pass.

The work is in `rewire_evidence.conformance` and `rewire_evidence.gates`, where the tests
cover it. This file is the command.

Usage: python scripts/verify_store_schema.py ../rewire-benchmark-data
       python scripts/verify_store_schema.py ../rewire-benchmark-data --json report.json

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be
made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import schema_gate

EXPECTED = Path(__file__).parent / "expected" / "store-schema.json"

if __name__ == "__main__":
    sys.exit(schema_gate(sys.argv[1:], EXPECTED, __doc__))
