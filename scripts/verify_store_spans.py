"""Check that this library resolves every stored locator to the span the TypeScript resolves it to.

Reads a checkout of `rewire-benchmark-data` and a folder of stored objects. Writes nothing
to either and asks no network for anything. It runs pdftotext, and reports any pdftotext
but the one the layers were derived with.

The data repository commits totals, and totals cannot show that two resolvers agree on
which span a result has. So the oracle is a recording, written by
`scripts/fixtures/record_spans_store.mjs` from the TypeScript's own measurement: one line for
each result with the layer and both ranges of its span, or the reason it was refused. It was
recorded once over every object, and those lines are in
`scripts/expected/store-spans.jsonl.gz`. It is recorded again over the public objects in
every CI run, and `--recorded` names the folder that run wrote.

The gate measures with this library over the objects it is given and requires every file of
the measurement to be the TypeScript's for those objects, each result that those objects
decide to have the line the full recording has, and the five committed tables and the
committed figures to be what the results give. A result that needs a restricted object is
taken from the recording and counted.

The objects are not in the checkout. `scripts/fetch_public_objects.py` fetches the ones a
public release mirrors into a folder laid out as the object cache is.

The work is in `rewire_evidence.spanparity` and `rewire_evidence.gates`, where the tests
cover it. This file is the command.

Usage: python scripts/verify_store_spans.py <checkout> <objects>
       python scripts/verify_store_spans.py <checkout> <objects> --recorded <folder> --json report.json

Exit status: 0 when everything agrees, 1 on any disagreement, 2 when the run could not be
made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import spans_gate

EXPECTED = Path(__file__).parent / "expected" / "store-spans.json"

if __name__ == "__main__":
    sys.exit(spans_gate(sys.argv[1:], EXPECTED, __doc__))
