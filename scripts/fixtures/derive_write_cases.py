"""Work out, from the data repository's history, the commands a merged pull request ran.

    python scripts/fixtures/derive_write_cases.py <clone> <merge>... > scripts/expected/store-writes.json

Nothing records the command line of a `records add` or a `records change`. What it did is
in the provenance rows, so this reads the rows a merge commit changed against its first
parent and writes the commands down as a case for `scripts/verify_store_writes.py`:

- rows that are new, grouped by `added_in`, are one `add` each, of `<added_in>/batch.jsonl`
  as the merge commit holds it;
- entries added to `changed_by` are one `change` for each distinct entry, in the order the
  rows hold them, naming the rows that gained it.

It refuses a merge it cannot state that way: a row removed, a row whose digest or earlier
entries changed with no new entry, a new row that already has changes, or a batch folder
with no `batch.jsonl`. Adds come before changes, because an add loads the store through
every check and a record edited for the change may cite a record the batch adds.

The counts written are what the provenance rows say was added and changed, and the number of
store files in the merge commit. The gate holds its replay to them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

STORE = ("data/entities", "data/evidence", "data/provenance")
CONFIGURATION = ("data/vocab", "data/ontology/mapping.json", "scripts/omics/derive")


def git(clone: str, *arguments: str) -> str:
    """What git writes for these arguments, run in the clone."""
    return subprocess.run(  # noqa: S603 - git, with the arguments below
        ["git", "-C", clone, *arguments],  # noqa: S607 - whichever git the machine has
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout


def rows(clone: str, merge: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The provenance rows the merge removed and the rows it added, by ID."""
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    changed = git(clone, "diff", "--no-ext-diff", "-U0", f"{merge}^1", merge, "--", "data/provenance/records")
    for line in changed.split("\n"):
        # A row is an object on one line. The headers and the hunk marks are not.
        if line[:2] in {"+{", "-{"}:
            row = json.loads(line[1:])
            (after if line[0] == "+" else before)[row["id"]] = row
    return before, after


def case(clone: str, merge: str) -> dict[str, Any]:  # noqa: C901, PLR0912 - one refusal after another
    """One merge as a case: its commits, the commands it ran and what they amount to."""
    name = git(clone, "rev-parse", f"{merge}^{{commit}}").strip()
    subject = git(clone, "log", "-1", "--format=%s", name).strip()
    before, after = rows(clone, name)
    if set(before) - set(after):
        raise SystemExit(f"{merge} removes provenance rows")
    adds: dict[str, int] = {}
    entries: list[tuple[str, dict[str, Any]]] = []
    for record_id in sorted(after):
        row = after[record_id]
        if record_id not in before:
            if row["added_by"] != "records:add" or row["changed_by"]:
                raise SystemExit(f"{merge} adds {record_id} with changes already recorded")
            adds[row["added_in"]] = adds.get(row["added_in"], 0) + 1
            continue
        earlier = before[record_id]["changed_by"]
        new = row["changed_by"][len(earlier) :]
        if row["changed_by"][: len(earlier)] != earlier or not new:
            raise SystemExit(f"{merge} changes the row of {record_id} and records no change")
        entries.extend((record_id, entry) for entry in new)
    steps: list[dict[str, Any]] = []
    for batch_dir in adds:
        batch = f"{batch_dir}/batch.jsonl"
        if not git(clone, "ls-tree", name, "--", batch):
            raise SystemExit(f"{merge} has no {batch}")
        steps.append({"add": batch, "batch_dir": batch_dir})
    order: list[str] = []
    named: dict[str, list[str]] = {}
    for record_id, entry in entries:
        if entry.get("step") != "records:change" or list(entry) != ["step", "inputs", "date", "review"]:
            raise SystemExit(f"{merge} records a change to {record_id} that records change does not write")
        key = json.dumps(entry)
        if key not in named:
            order.append(key)
        named.setdefault(key, []).append(record_id)
    # Each row holds its new entries in the order of the steps, or the steps are not in an order.
    for record_id in {record_id for record_id, _ in entries}:
        held = [order.index(json.dumps(entry)) for other, entry in entries if other == record_id]
        if held != sorted(held):
            raise SystemExit(
                f"{merge} records the changes to {record_id} in an order no sequence of commands gives"
            )
    for key in order:
        entry = json.loads(key)
        steps.append(
            {
                "change": named[key],
                "batch_dir": entry["inputs"],
                "review": entry["review"],
                "date": entry["date"],
            }
        )
    files = [
        line for line in git(clone, "ls-tree", "-r", "--name-only", name, "--", *STORE).split("\n") if line
    ]
    return {
        "name": subject,
        "merge": name,
        "parent": git(clone, "rev-parse", f"{name}^1").strip(),
        "steps": steps,
        "added": sum(adds.values()),
        "changed": len(entries),
        "files": len(files),
    }


def main(arguments: list[str]) -> int:
    """Print the expectation for the merges named."""
    clone, *merges = arguments
    written = {
        "repository": "rewire-bio/rewire-benchmark-data",
        "derived_by": "scripts/fixtures/derive_write_cases.py",
        "configuration": list(CONFIGURATION),
        "cases": [case(clone, merge) for merge in merges],
    }
    print(json.dumps(written, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
