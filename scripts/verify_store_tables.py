"""Check that this library reads tables as the data repository's table reader reads them.

Reads a checkout of `rewire-benchmark-data` and nothing else. Writes nothing to it.

The reader is `scripts/omics/extract/tables.ts`: `tables`, which finds every table of an
HTML or JATS XML artifact with its caption and its rows of cells, and what the extractors
build on it. Nothing the data repository commits says what it reads, so the oracle is a
recording. `scripts/fixtures/record_tables_fixture.mjs` runs the TypeScript in CI over
every file under `data/` and `docs/` whose name ends `.html`, `.html.gz` or `.xml.gz`, and
writes `scripts/expected/store-tables.json` and, beside it, a line for each file and a line
for each table: the caption, the rows and cells counted, and the SHA-256 of the rows, of
the rows with small capitals joined, and of the headed blocks.

The gate writes the same lines from the checkout and requires each one, then the counts,
and that `tables.ts` in the checkout is the file the recording was made from.

The work is in `rewire_evidence.tableparity` and `rewire_evidence.gates`, where the tests
cover it. This file is the command.

Usage: python scripts/verify_store_tables.py ../rewire-benchmark-data
       python scripts/verify_store_tables.py <checkout> --json report.json

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be
made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import tables_gate

EXPECTED = Path(__file__).parent / "expected" / "store-tables.json"

if __name__ == "__main__":
    sys.exit(tables_gate(sys.argv[1:], EXPECTED, __doc__))
