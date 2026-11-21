"""What the TypeScript archive read side recorded, and the helpers the tests replay it with.

`tests/fixtures/archive.json` is written by `scripts/fixtures/record_archive_fixture.mjs` in
CI, from the data repository's own code at the pinned commit. Nothing in it was written by
hand. A scenario is a tree, the calls the recorder made on it in order with what each
returned or threw and every file it left different, and the commands it ran on the tree laid
out again.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rewire_evidence import archive, licence, reclassify, worklist
from rewire_evidence.canonical import JsonObject, JsonValue, js_value

FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVE: dict[str, Any] = json.loads((FIXTURES / "archive.json").read_text("utf-8"))
#: The folder a scenario's tree is laid out in, under the folder the calls are made from.
ROOT = Path(ARCHIVE["root"])
#: The moment the recorder's clock was stopped at.
CLOCK = datetime.fromisoformat(ARCHIVE["clock"]).astimezone(UTC)


def stopped() -> str:
    return archive.utc_now(CLOCK)


def binary(file: str) -> bool:
    """Whether the recorder kept a file as hexadecimal: a cached object, or what stands for one."""
    return file.endswith(".gz") or "/cache/" in file or ".gz." in file


def lay_out(base: Path, files: Mapping[str, Any]) -> None:
    """Write files under the root: text, hexadecimal for bytes, and None to remove one."""
    for file, held in files.items():
        target = base / ROOT / file
        if held is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(held.encode("utf-8") if isinstance(held, str) else bytes.fromhex(held["hex"]))


def lay_out_scenario(base: Path, scenario: Mapping[str, Any]) -> None:
    (base / ROOT).mkdir()
    lay_out(base, scenario["files"])
    lay_out(base, {file: {"hex": held} for file, held in scenario["cache"].items()})


def tree(base: Path) -> dict[str, Any]:
    """Every file under a folder, as the recorder holds it."""
    found: dict[str, Any] = {}
    for path in sorted(base.rglob("*")):
        if path.is_file():
            file = path.relative_to(base).as_posix()
            found[file] = (
                {"hex": path.read_bytes().hex()} if binary(file) else path.read_bytes().decode("utf-8")
            )
    return found


def changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """The files one tree holds that another does not hold as they are, and None for one gone."""
    out = {file: held for file, held in after.items() if before.get(file) != held}
    return out | {file: None for file in before if file not in after}


def _readings() -> tuple[licence.LicenceMap, licence.Licences, dict[str, JsonObject]]:
    return licence.read_licence_map(ROOT), licence.read_source_licences(ROOT), licence.read_candidates(ROOT)


def _entries(held: Mapping[str, Any]) -> list[list[Any]]:
    return [[key, value] for key, value in held.items()]


def _reclassified(options: Mapping[str, Any] | None = None) -> JsonObject:
    log: list[JsonValue] = []
    summary = reclassify.reclassify_objects(ROOT, dry_run=bool((options or {}).get("dryRun")), log=log.append)
    return {"summary": summary, "log": log}


def _work_items() -> list[JsonObject]:
    return worklist.read_work_list(ROOT)["items"]  # type: ignore[return-value]


#: Each call the recorder made, as this library makes it. The root is the recorder's.
CALLS: dict[str, Callable[..., Any]] = {
    "readShard": lambda shard: archive.read_shard(shard, ROOT),
    "readManifest": lambda: _entries(archive.read_manifest(ROOT)),
    "checkManifest": lambda: archive.check_manifest(ROOT),
    "verifyObjects": lambda: archive.verify_objects(ROOT),
    "upsertRow": lambda row: archive.upsert_row(js_value(row), ROOT),
    "updateRow": lambda key, patch: archive.update_row(key, patch, ROOT),
    "markHome": lambda key, home: archive.mark_home(key, home, ROOT),
    "markMirror": lambda key, mirror: archive.mark_mirror(key, mirror, ROOT),
    "markRemoved": lambda key, removal: archive.mark_removed(key, removal, ROOT, stopped),
    "objectPath": lambda key: archive.object_path(key, ROOT).as_posix(),
    "archiveAssetName": archive.archive_asset_name,
    "hasObject": lambda key: archive.has_object(key, ROOT),
    "readObject": lambda key: archive.read_object(key, ROOT).hex(),
    "cacheContents": lambda: archive.cache_contents(ROOT),
    "readReceipts": lambda: archive.read_receipts(ROOT),
    "latestBySource": lambda: _entries(archive.latest_by_source(archive.read_receipts(ROOT))),
    "receiptPath": lambda date: archive.receipt_path(date, ROOT).as_posix(),
    "appendReceipt": lambda receipt: archive.append_receipt(js_value(receipt), ROOT),
    "readWorkList": lambda: worklist.read_work_list(ROOT),
    "groupByUrl": lambda: _entries(worklist.group_by_url(_work_items())),
    "summarise": lambda: worklist.summarise(ROOT),
    "readLicenceMap": lambda: _entries(licence.read_licence_map(ROOT)),
    "readSourceLicences": lambda: _entries(licence.read_source_licences(ROOT)),
    "determineForSource": lambda source: licence.determine_for_source(
        source, licence.read_licence_map(ROOT), licence.read_source_licences(ROOT)
    ),
    "licenceClassifier": lambda sources: licence.licence_classifier(ROOT)(list(sources)),
    "buildTriage": lambda: licence.build_triage(ROOT),
    "renderTriage": lambda date: licence.render_triage(licence.build_triage(ROOT), date),
    "readCandidates": lambda: _entries(licence.read_candidates(ROOT)),
    "readSource": lambda source: reclassify.read_source(source, *_readings()),
    "readRow": lambda sources: reclassify.read_row(sources, *_readings()),
    "planReclassify": lambda: reclassify.plan_reclassify(ROOT),
    "reclassifyObjects": _reclassified,
}


def issues_of(error: archive.Refused) -> list[list[Any]]:
    return [[issue.code, list(issue.path), issue.message] for issue in error.issues]
