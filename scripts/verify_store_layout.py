"""Check that this library writes the store's files exactly as the store holds them.

Reads a `rewire-benchmark-data` checkout and nothing else. Writes nothing to it.

For every family of line file (entity and evidence records, provenance, moved attributes,
the object manifest, fetch and licence receipts, licence candidates, the layer registry and
its unsupported rows) it reads each file, writes it again in memory and requires the same
bytes. It checks every row is in the file the layout assigns it, that every record matches
its provenance row, and that the store's logical digests equal the ones a release receipt
recorded. It then writes every file again, on disk in a temporary folder, with the writer a
caller would use, compares the bytes, and reshards two copies: one already in its layout,
which has to change nothing, and one unsharded with its lines out of order, which has to
come out as the committed files. The temporary copies are about three times the size of
the store's line files.

Those checks compare the checkout with itself. What ties the run to one commit of the
store is `scripts/expected/store-layout.json`: every count has to equal the pinned one, so
a thinner checkout cannot pass, and the digest of each family's files has to equal the
pinned one, so an edited checkout cannot pass either. The script does not ask git which
commit is checked out. It checks the bytes.

The work is in `rewire_evidence.parity` and `rewire_evidence.gates`, where the tests cover
it. This file is the command.

Usage: python scripts/verify_store_layout.py ../rewire-benchmark-data
       python scripts/verify_store_layout.py ../rewire-benchmark-data --json report.json

Exit status: 0 when everything reproduces, 1 on any disagreement, 2 when the run could not
be made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import layout_gate

EXPECTED = Path(__file__).parent / "expected" / "store-layout.json"

if __name__ == "__main__":
    sys.exit(layout_gate(sys.argv[1:], EXPECTED, __doc__))
