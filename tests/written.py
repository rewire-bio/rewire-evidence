"""What the TypeScript write path recorded, and how a case of it is run here.

`tests/fixtures/write-path.json` is written by `scripts/fixtures/record_write_fixture.mjs`
in CI, from `addBatch`, `recordChanges` and `applyChanges` at the pinned commit, on the
cases `scripts/fixtures/write_cases.mjs` builds. Nothing in it was written by hand.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rewire_evidence.attributes import JsTypeError
from rewire_evidence.canonical import JsonObject, JsonValue
from rewire_evidence.jsonl import StoreError
from rewire_evidence.schema import RecordError, SchemaError
from rewire_evidence.vocab import VocabularyError
from rewire_evidence.writes import Plan, plan_add, plan_change, plan_changes_file

FIXTURES = Path(__file__).parent / "fixtures"
WRITES: dict[str, Any] = json.loads((FIXTURES / "write-path.json").read_text("utf-8"))
#: The date the recorder's stopped clock gives a change.
DATE: str = WRITES["date"]
CASES: list[dict[str, Any]] = WRITES["cases"]

#: Cases the TypeScript writes and this library refuses to: each holds a number beyond every
#: double, which `JSON.stringify` writes as null. The digest is taken as the TypeScript takes
#: it, and the line is not written.
BEYOND = frozenset(
    f"{case['command']}: {case['name']}" for case in CASES if "a number beyond every double" in case["name"]
)


def label(case: dict[str, Any]) -> str:
    return f"{case['command']}: {case['name']}"


def before(case: dict[str, Any]) -> dict[str, str]:
    """The store a case starts from, as {file: text}."""
    files: dict[str, str] = dict(WRITES["stores"][case["store"]])
    for file, text in case["edits"].items():
        if text is None:
            del files[file]
        else:
            files[file] = text
    return files


def lay_out(case: dict[str, Any], work: Path) -> tuple[Path, Path]:
    """Write a case's store and its batch or changes file. Returns the store's root and the file."""
    root = work / "store"
    root.mkdir(parents=True)
    for file, text in before(case).items():
        path = root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    given = work / "input.jsonl"
    if case["input"] is not None:
        given.write_bytes(case["input"].encode("utf-8"))
    return root, given


def planned(case: dict[str, Any], root: Path, given: Path, configuration: Path) -> Plan:
    """The plan this library makes for a case, with the date the recorder's clock gave."""
    if case["command"] == "add":
        return plan_add(given, case["batch_dir"], root, configuration)
    if case["command"] == "change":
        return plan_change(case["ids"], case["batch_dir"], case["review"], root, DATE)
    return plan_changes_file(str(given), case["batch_dir"], case["review"], root, DATE)


def answer(run: Callable[[], object], work: Path) -> JsonObject | None:
    """What a call did, in the form the recorder writes the TypeScript's answer in.

    Three refusals are recorded in words this library does not use, and are told apart by
    kind: a JavaScript `TypeError`, a line `JSON.parse` refuses, and a file that is not
    there. A line the schema refuses while the store is loading is zod's issues there, and
    here a `StoreError` that names the line and was raised from them.
    """
    try:
        run()
    except SchemaError as error:
        return {"zod": [[issue.code, list(issue.path), issue.message] for issue in error.issues]}
    except JsTypeError:
        return {"name": "TypeError"}
    except (RecordError, VocabularyError) as error:
        return {"name": "Error", "message": str(error).replace(str(work), "<work>")}
    except StoreError as error:
        if isinstance(error.__cause__, SchemaError):
            issues: list[JsonValue] = [
                [issue.code, list(issue.path), issue.message] for issue in error.__cause__.issues
            ]
            return {"zod": issues}
        if " is not JSON: " in str(error):
            return {"name": "SyntaxError"}
        if " cannot be read: " in str(error):
            return {"name": "Error", "code": "ENOENT"}
        return {"name": "Error", "message": str(error).replace(str(work), "<work>")}
    return None


def recorded(case: dict[str, Any]) -> JsonObject | None:
    """The TypeScript's answer, with V8's words left out where only the kind is compared."""
    error: JsonObject | None = case["error"]
    if error is not None and error.get("name") in {"TypeError", "SyntaxError"}:
        return {"name": error["name"]}
    return error


def files_under(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes().decode("utf-8")
        for path in root.rglob("*")
        if path.is_file()
    }


def left(case: dict[str, Any]) -> dict[str, str]:
    """The store a case leaves, as the TypeScript left it: {file: text}."""
    files = before(case)
    for file, text in case["after"].items():
        files[file] = text
    return files
