"""Check that this library reads the archive manifest, the receipts and the licence map as
the data repository's TypeScript reads them.

Reads a checkout of `rewire-benchmark-data` and nothing else. Writes nothing to it, and asks
no network for anything.

The read side is `shared/archive/schema.ts` and `worklist.ts`, `scripts/archive/manifest.ts`,
`receipts.ts`, `reclassify.ts` and the reading half of `objects.ts`, and `scripts/licence/map.ts`,
`triage.ts` and `counts.ts`. The committed files say what the rows are and not what the
TypeScript makes of them, so the oracle is a recording. `scripts/fixtures/record_archive_fixture.mjs`
runs the TypeScript in CI over the pinned checkout and writes `scripts/expected/store-archive.json`
and, beside it, a line for each licence string, source record, manifest row, receipt and
candidate, and for each row of the triage queue.

The gate writes the same lines from the checkout and requires each one. It requires every
manifest row, receipt and candidate the schema reads to be written back as the committed
line, the work list summary and the reclassification plan as the TypeScript holds them, what
each command prints, and that each TypeScript file in the checkout is the file recorded. It
requires each file of the checkout the read side reads to be the file recorded, byte for
byte: the source records, the licence map, the manifest shards, the candidates, the receipts
and the committed triage queues. And it requires the queue generated to hold as many lines
of the committed queues as the TypeScript counted.

The work is in `rewire_evidence.archiveparity` and `rewire_evidence.gates`, where the tests
cover it. This file is the command.

Usage: python scripts/verify_store_archive.py ../rewire-benchmark-data
       python scripts/verify_store_archive.py <checkout> --json report.json

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be
made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import archive_gate

EXPECTED = Path(__file__).parent / "expected" / "store-archive.json"

if __name__ == "__main__":
    sys.exit(archive_gate(sys.argv[1:], EXPECTED, __doc__))
