"""Write the committed expectation of the span gate from two recordings of the TypeScript.

Usage: python scripts/fixtures/assemble_spans_expectation.py <full> <public>

`<full>` and `<public>` are folders `scripts/fixtures/record_spans_store.mjs` wrote from one
checkout of the data repository at the pinned commit, on one Node: the first over a cache
that holds every object, the second over the objects a public release mirrors.

It writes `scripts/expected/store-spans.json`, which states both recordings by the digest of
each file, and beside it the lines of the full recording, which hold no text of any layer:
`store-spans.jsonl.gz`, one for each result, and `store-spans-legacy.jsonl.gz`, one for each
result the legacy quote rule decides otherwise.

The full recording cannot be made in CI, which has no access to the restricted objects. It
is made on a machine that holds them, and this is run after it. CI records the public view
on every run and holds it to the digests written here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rewire_evidence.spanparity import assemble

EXPECTED = Path(__file__).parent.parent / "expected"

if __name__ == "__main__":
    full, public = (Path(argument) for argument in sys.argv[1:3])
    pdftotext = json.loads((EXPECTED / "store-layers.json").read_text("utf-8"))["pdftotext"]
    text, rows, legacy = assemble(full, public, pdftotext)
    (EXPECTED / "store-spans.json").write_text(text, encoding="utf-8")
    (EXPECTED / "store-spans.jsonl.gz").write_bytes(rows)
    (EXPECTED / "store-spans-legacy.jsonl.gz").write_bytes(legacy)
    print(f"Wrote the expectation of the span gate from {full} and {public}.")
