"""Check that this library reads every stored locator as the TypeScript does.

Reads a `rewire-benchmark-data` checkout and nothing else. Writes nothing to it.

The store commits a coverage report of twelve rows, and totals cannot show that two parsers
agree on what a locator says. So the oracle is a recording:
`scripts/expected/store-locators.jsonl.gz` holds one line for every locator of every result,
written by `shared/omics/locator.ts` at the pinned commit, with the record's ID, the class,
the coverage, the whole parse and the text it renders back to.
`scripts/fixtures/record_locator_fixture.mjs` writes it in CI, and
`scripts/expected/store-locators.json` names the commit, the number of lines and their
SHA-256.

The gate reads the same locators from the checkout, writes the same line for each with this
library, and requires every one to be the recorded line, byte for byte. It then requires

- the coverage report, as `rewire-evidence locator coverage` writes and prints it, to be
  what the TypeScript's command wrote and printed, and the bytes of the report the data
  repository commits;
- version 1 of the grammar to accept exactly the rows of the `value-check.tsv` files that
  `check-values` did not report as `locator_not_parsed`, in the recorded numbers.

The work is in `rewire_evidence.locators` and `rewire_evidence.gates`, where the tests
cover it. This file is the command.

Usage: python scripts/verify_store_locators.py ../rewire-benchmark-data
       python scripts/verify_store_locators.py ../rewire-benchmark-data --json report.json

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be
made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import locators_gate

EXPECTED = Path(__file__).parent / "expected" / "store-locators.json"

if __name__ == "__main__":
    sys.exit(locators_gate(sys.argv[1:], EXPECTED, __doc__))
