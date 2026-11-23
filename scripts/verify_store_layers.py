"""Check that this library derives every text layer and table grid the data repository registers.

Reads a checkout of `rewire-benchmark-data`, the manifest of the commit that wrote the layer
registry, and a folder of stored objects. Writes nothing to any of them and asks no network
for anything. It runs pdftotext and pdfinfo, and refuses any pdftotext but the one the
registry names.

The oracle is committed: `data/omics/layers/registry.jsonl`, one line for each layer with
the SHA-256 of its bytes and the counts the sanity gate read from them, and
`unsupported.jsonl`, one line for each object or member that gave no layer. The gate derives
every layer again from each object it holds and requires each committed line, whole.

The objects are not in the checkout. `scripts/fetch_public_objects.py` fetches the ones a
public release mirrors into a folder laid out as the object cache is. A public object that
is missing fails the gate. A restricted one is checked when it is held and counted as not
checked when it is not.

The work is in `rewire_evidence.layerparity` and `rewire_evidence.gates`, where the tests
cover it. This file is the command.

Usage: python scripts/verify_store_layers.py <checkout> <manifest checkout> <objects>
       python scripts/verify_store_layers.py <checkout> <manifest checkout> <objects> --json report.json

`<manifest checkout>` is a checkout, of `data/archive/objects` at least, at the commit that
wrote the registry.

Exit status: 0 when every held object gives the committed lines, 1 on any disagreement, 2
when the run could not be made.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rewire_evidence.gates import layers_gate

EXPECTED = Path(__file__).parent / "expected" / "store-layers.json"

if __name__ == "__main__":
    sys.exit(layers_gate(sys.argv[1:], EXPECTED, __doc__))
