"""Check that this library reports what the data repository's checks pack reports.

Reads two checkouts of `rewire-benchmark-data` and nothing else. Writes nothing to either.

The pack is seven report-only checks, and its findings are pinned twice.

The data repository commits the report of the first run, one row for each finding, and
the baseline pinned from it. Both were measured on the store at the commit the baseline
names, which is older than the commit that holds them. So the second checkout is of that
older commit, and gives the store and the vocabularies. The pack is run over it, and the
report has to be `maintenance/reports/checks-<date>.tsv` of the first checkout row for
row, and the baseline file `data/omics/checks-baseline.json` byte for byte.

For the pinned commit the oracle is a recording. `scripts/fixtures/record_checks_fixture.mjs`
runs the TypeScript over the pinned store in CI and writes `scripts/expected/store-checks.json`
and three files of lines beside it: the report, the anchor and column read from every
result's locator, and `parseCell`'s reading of every printed value. The gate requires each
line of this library, then each check's counts, the vocabularies as read, what the command
prints, and what the ratchet says of the run, of a seeded record and of a lowered count.

The work is in `rewire_evidence.checkparity` and `rewire_evidence.gates`, where the tests
cover it. This file is the command.

Usage: python scripts/verify_store_checks.py ../rewire-benchmark-data ../rewire-benchmark-data-first-run
       python scripts/verify_store_checks.py <pinned> <first run> --json report.json

The second checkout needs only `data/entities`, `data/evidence` and `data/vocab`.

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be
made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import checks_gate

EXPECTED = Path(__file__).parent / "expected" / "store-checks.json"

if __name__ == "__main__":
    sys.exit(checks_gate(sys.argv[1:], EXPECTED, __doc__))
