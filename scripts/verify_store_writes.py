"""Check that this library writes the store as the TypeScript does, by replaying its history.

Reads a clone of `rewire-benchmark-data` that has its history, and writes nothing to it. A
blobless clone is enough, and the blobs a replay needs are fetched as it goes:

    git clone --filter=blob:none --no-checkout https://github.com/rewire-bio/rewire-benchmark-data.git

Each case in `scripts/expected/store-writes.json` is a merged pull request that added a
batch or recorded a change. The store is laid out in a scratch folder as the merge's first
parent holds it, the commands the pull request ran are run again by this library (`records
add` with the batch file the merge holds, `records change` with the IDs, review and date its
provenance rows record), and every file under `data/entities`, `data/evidence` and
`data/provenance` then has to be the merge commit's, byte for byte: the files that changed
and the ones that should not have. The comparison is on git's own blob names.

The commands are not recorded anywhere but in what they did, so
`scripts/fixtures/derive_write_cases.py` works them out from the provenance rows each merge
changed. The counts of records added, changes recorded and files compared are in the
expectation, and a replay that does less fails.

The work is in `rewire_evidence.replay`, `rewire_evidence.writes` and
`rewire_evidence.gates`, where the tests cover it. This file is the command.

Usage: python scripts/verify_store_writes.py ../rewire-benchmark-data
       python scripts/verify_store_writes.py ../rewire-benchmark-data --json report.json

Exit status: 0 when every case reproduces, 1 on any disagreement, 2 when the run could not
be made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import writes_gate

EXPECTED = Path(__file__).parent / "expected" / "store-writes.json"

if __name__ == "__main__":
    sys.exit(writes_gate(sys.argv[1:], EXPECTED, __doc__))
