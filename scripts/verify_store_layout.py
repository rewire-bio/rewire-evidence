"""Check that this library writes the store's files exactly as the store holds them.

Reads a `rewire-benchmark-data` checkout and nothing else. Writes nothing to it.

For every family of line file (entity and evidence records, provenance, moved attributes,
the object manifest, fetch and licence receipts, licence candidates, the layer registry and
its unsupported rows) it reads each file, writes it again in memory and requires the same
bytes. It checks every row is in the file the layout assigns it, that the store's logical
digests equal the ones a release receipt recorded, and that every count equals the pinned
expectation, so a thinner checkout cannot pass.

The work is in `rewire_evidence.parity`, where the tests cover it. This file is the command.

Usage: python scripts/verify_store_layout.py ../rewire-benchmark-data
       python scripts/verify_store_layout.py ../rewire-benchmark-data --json report.json

Exit status: 0 when everything reproduces, 1 on any disagreement, 2 when the run could not
be made.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rewire_evidence.jsonl import StoreError
from rewire_evidence.parity import compare_counts, read_expectation, summary, to_json, verify_store

EXPECTED = Path(__file__).parent / "expected" / "store-layout.json"
#: Printed in full up to here, so a failure names causes without burying the summary.
PROBLEMS_SHOWN = 20


def run(root: Path, expected: Path, report_file: Path | None) -> int:
    """Run the gate and print what it found. Returns the exit status."""
    expectation = read_expectation(expected)
    print(f"store pinned at {expectation.store_ref}, release {expectation.release_receipt}")
    report = verify_store(root, expectation.release_receipt)
    print(summary(report))
    if report_file is not None:
        report_file.write_text(to_json(report, expectation) + "\n", encoding="utf-8")

    problems = [*report.problems(), *compare_counts(report.counts(), expectation.counts)]
    for problem in problems[:PROBLEMS_SHOWN]:
        print(f"  {problem}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems):,} problems. The store layout does not reproduce.", file=sys.stderr)
        return 1
    lines = sum(family.lines for family in report.families)
    print(f"\nAll {lines:,} lines in {sum(family.files for family in report.families)} files reproduce.")
    return 0


def main() -> None:
    """Parse the command line and exit with the gate's status."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("root", type=Path, help="a rewire-benchmark-data checkout")
    parser.add_argument("--expected", type=Path, default=EXPECTED, help="the pinned expectation")
    parser.add_argument("--json", type=Path, default=None, help="write the measured counts here")
    args = parser.parse_args()
    try:
        sys.exit(run(args.root, args.expected, args.json))
    except StoreError as error:
        print(f"Cannot verify: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
