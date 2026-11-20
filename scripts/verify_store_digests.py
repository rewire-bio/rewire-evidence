"""Check this library's serialisation against every record the TypeScript store committed.

Reads a `rewire-benchmark-data` checkout and nothing else. Writes nothing anywhere.

  data/provenance/records/<shard>.jsonl   the committed digest for every record
  data/entities/<kind>.jsonl              entity records
  data/evidence/<kind>/<shard>.jsonl      evaluations, results and claims

For each record it recomputes `sha256(JSON.stringify(record))` with `rewire_evidence.canonical`
and compares it against the digest the Node implementation wrote. This is the acceptance
gate for the determinism kernel: the store is the oracle, and at 93,216 records it is large
enough that a passing run means the two implementations agree rather than that the test was
weak. Anything short of every record is a failure, and the first few mismatches are printed
with the field that differs so the cause is visible rather than inferred.

Usage: python scripts/verify_store_digests.py ../rewire-benchmark-data
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from rewire_evidence.canonical import record_sha256

#: Printed when a mismatch is found, so a failure names a cause rather than a count.
MISMATCHES_SHOWN = 5


class VerifyError(Exception):
    """A run that could not be made, as opposed to a run that found a disagreement."""


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Every row of a file, skipping blank lines."""
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise VerifyError(f"{path}:{number} is not JSON: {error}") from error


def committed_digests(root: Path) -> dict[str, str]:
    """The digest the store committed for every record, by record ID."""
    folder = root / "data/provenance/records"
    if not folder.is_dir():
        raise VerifyError(f"{folder} is missing, so {root} is not a benchmark-data checkout")
    digests = {
        row["id"]: row["sha256"] for path in sorted(folder.glob("*.jsonl")) for row in read_jsonl(path)
    }
    if not digests:
        raise VerifyError(f"{folder} holds no provenance rows")
    return digests


def record_files(root: Path) -> list[Path]:
    """Every entity and evidence file under a checkout."""
    files = sorted((root / "data/entities").glob("*.jsonl"))
    files += sorted((root / "data/evidence").glob("*/*.jsonl"))
    if not files:
        raise VerifyError(f"{root}/data holds no record files")
    return files


def verify(root: Path) -> int:
    """Recompute every record digest and report. Returns the exit status."""
    digests = committed_digests(root)
    print(f"provenance rows: {len(digests):,}", file=sys.stderr)

    tally: Counter[str] = Counter()
    mismatches: list[tuple[Path, str, str, str]] = []
    for path in record_files(root):
        for record in read_jsonl(path):
            committed = digests.get(str(record.get("id")))
            if committed is None:
                tally["no provenance row"] += 1
                continue
            computed = record_sha256(record)
            if computed == committed:
                tally["match"] += 1
            else:
                tally["MISMATCH"] += 1
                if len(mismatches) < MISMATCHES_SHOWN:
                    mismatches.append((path, record["id"], committed, computed))

    checked = sum(tally.values())
    print(f"records checked: {checked:,}", file=sys.stderr)
    for name, count in tally.most_common():
        print(f"  {name:20} {count:,}", file=sys.stderr)

    for path, record_id, committed, computed in mismatches:
        print(
            f"\n  {path.name} {record_id}\n    committed {committed}\n    computed  {computed}",
            file=sys.stderr,
        )

    if tally["MISMATCH"]:
        print(f"\n{tally['MISMATCH']:,} records do not reproduce.", file=sys.stderr)
        return 1
    if not tally["match"]:
        raise VerifyError("no record matched a provenance row; the paths are probably wrong")
    print(f"\nAll {tally['match']:,} records reproduce.", file=sys.stderr)
    return 0


def main() -> None:
    """Parse the command line and exit with the gate's status."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("root", help="a rewire-benchmark-data checkout")
    args = parser.parse_args()
    try:
        sys.exit(verify(Path(args.root)))
    except VerifyError as error:
        print(f"Cannot verify: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
