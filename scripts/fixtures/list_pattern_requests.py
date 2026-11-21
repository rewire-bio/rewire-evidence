"""List the sets of characters the library's patterns need recorded from Node.

Usage: python scripts/fixtures/list_pattern_requests.py

Writes `scripts/fixtures/pattern_requests.json`: every class, escape, dot and letter under
the `i` flag in the patterns the library runs and in the ones
`scripts/fixtures/pattern_cases.json` tests the translation with, each with the flags that
decide what it matches. `scripts/fixtures/record_pattern_fixture.mjs` reads the list and
records the characters Node matches for each, in the `fixtures` job in CI.

Run it after adding or changing a pattern. A test fails while the committed list is not
what this writes.
"""

from __future__ import annotations

import json
from pathlib import Path

from rewire_evidence import jsregex, locator

HERE = Path(__file__).parent

if __name__ == "__main__":
    cases = json.loads((HERE / "pattern_cases.json").read_text("utf-8"))["patterns"]
    patterns = [(held.source, held.flags) for held in locator.PATTERNS]
    patterns += [(case["source"], case["flags"]) for case in cases]
    (HERE / "pattern_requests.json").write_text(jsregex.requests(patterns), encoding="utf-8")
    print(f"Listed what {len(patterns)} patterns need.")
