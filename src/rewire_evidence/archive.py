"""The archive contract: manifest rows, fetch receipts, and the object cache, read side.

This is `shared/archive/schema.ts`, `scripts/archive/manifest.ts`, `scripts/archive/receipts.ts`
and the reading half of `scripts/archive/objects.ts`. Nothing here asks a network for
anything. The fetcher, the uploads and the pulls are a later stage.

An object is keyed on the SHA-256 of its expanded bytes. The manifest is one line per object
in `data/archive/objects/<0-f>.jsonl`, sharded on the key's first digit and sorted by key. A
receipt is one line per transfer in `data/omics/audits/<date>-archive-fetch.receipts.jsonl`,
appended and never edited. Both are written by `canonical_line`: top-level keys sorted.

`store` on a row names the one home of the bytes and `mirrors` the other backends holding a
copy. The rules the schema holds a row to, and which the writes below must keep working
under, are the ones a takedown depends on:

- A restricted row never names a public release as its home or a mirror. The rule is not
  applied to a removed row, whose `store` is a note of where the bytes were.
- A removed row sits at `t5-no-store` and names no mirror, so `mark_removed` clears them.
- `mark_home` and `mark_mirror` return whether they wrote, not whether they found the row.

gzip is not byte-stable across zlib versions, so a cached object is checked by expanding it
and hashing what comes out, and nothing here compresses anything.

Where the TypeScript would throw whatever `JSON.parse` or the file system throws, this
raises `StoreError` in its own words. Everything else is said in the TypeScript's words.
"""

from __future__ import annotations

import os
import re
import zlib
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from rewire_evidence import zod
from rewire_evidence.canonical import (
    JsonObject,
    JsonValue,
    canonical_line,
    object_shard,
    sha256_hex,
    utf16_key,
)
from rewire_evidence.families import AUDIT_DIR, OBJECT_SHARDS, manifest_shard_path
from rewire_evidence.js import lower, trim, truthy
from rewire_evidence.jsonl import StoreError, append_text, parse_value, write_text_atomically
from rewire_evidence.schema import Issue

LAYERS: Final = ("exact-bytes", "archive-member", "text-layer", "canonical-json", "warc")
STORES: Final = ("local-cache", "github-release", "r2")
VISIBILITIES: Final = ("public", "restricted")
ARCHIVE_TIERS: Final = (
    "t0-public",
    "t1-public-nc",
    "t2-verbatim-nd",
    "t3-verbatim-nd-nc",
    "t4-private",
    "t5-no-store",
)
EXPOSURES: Final = ("policy", "pre-policy")
ROUTES: Final = ("direct", "unpaywall", "europepmc", "preprint")
REASONS: Final = (
    "http_error",
    "network_error",
    "timeout",
    "empty_body",
    "too_large",
    "not_found",
    "rate_limited",
    "host_stopped",
    "no_doi",
    "no_open_copy",
    "unpaywall_no_email",
    "paywalled_not_held",
    "unsupported_url",
    "interstitial_body",
    "text_below_threshold",
    "publisher_html_only",
)
OUTCOMES: Final = ("stored", "not_artifact", "failed")
SOURCE_CLASSES: Final = (
    "europepmc-api",
    "pmc",
    "arxiv",
    "preprint-server",
    "publisher",
    "code-repository",
    "model-hub",
    "data-repository",
    "other-web",
)
#: The tier an object gets when no licence has been determined: never public, never unstored.
DEFAULT_ARCHIVE_TIER: Final = "t4-private"
#: The tier that keeps no copy, which every publish path refuses by name.
NO_STORE: Final = "t5-no-store"
#: The data repository, whose release tags hold the public mirror.
REPOSITORY: Final = "rewire-bio/rewire-benchmark-data"
CACHE_DIR: Final = "data/archive/cache"
#: `expand` gives up on a file that expands to more than this, as `scripts/release/files.ts` does.
MAX_EXPANDED: Final = 4 * 1024**3

_KEY: Final = re.compile(r"[a-f0-9]{64}")
_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_RECEIPTS: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-archive-fetch\.receipts\.jsonl")
_CACHED: Final = re.compile(r"([a-f0-9]{64})\.gz")
_SAFE_NAME: Final = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._/-]*")


class ArchiveError(StoreError):
    """What the TypeScript throws as an `Error`, in its words."""


class Refused(ArchiveError):  # noqa: N818 - it carries a schema's refusal, in zod's words
    """What `schema.parse(value)` throws: zod's issues, in zod's order."""

    def __init__(self, issues: Sequence[Issue]) -> None:
        """Keep the issues, and say the first."""
        super().__init__(issues[0].message)
        self.issues = tuple(issues)


def visibility_of(tier: str) -> str:
    """Which tiers may reach a public backend: all but the private one and the one never kept."""
    return "restricted" if tier in {"t4-private", NO_STORE} else "public"


def is_removed(row: Mapping[str, JsonValue]) -> bool:
    """Whether a row's bytes were removed on request."""
    return truthy(row.get("removed_at"))


def media_type_of(header: str | None) -> str:
    """A Content-Type header reduced to its media type."""
    return lower(trim((header or "").split(";")[0])) or "application/octet-stream"


def utc_now(now: datetime | None = None) -> str:
    """A UTC timestamp to the second, which is the only form either file holds."""
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def key_shard(sha256: str) -> str:
    """The manifest shard a key belongs to, refusing what is not a key in the TypeScript's words."""
    try:
        return object_shard(sha256)
    except ValueError as error:
        raise ArchiveError(str(error)) from None


# ---- the schemas ----------------------------------------------------------------------

_HEX64: Final = zod.text(zod.pattern(_KEY, "^[a-f0-9]{64}$"))
_UTC_SOURCE: Final = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?Z$"
_UTC: Final = zod.text(
    zod.pattern(
        re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,3})?Z"), _UTC_SOURCE
    )
)
_BYTES: Final = zod.number(zod.INT, zod.NONNEGATIVE)
_TEXT: Final = zod.NONEMPTY


def _joined(items: JsonValue) -> str:
    return ", ".join(cast("list[str]", items))


def _row_rules(value: JsonValue, report: zod.Report) -> None:  # noqa: C901 - one rule to a branch
    row = cast("JsonObject", value)
    layer, tier, store = row["layer"], cast("str", row["archive_tier"]), row["store"]
    mirrors = cast("list[str]", row["mirrors"])
    derived = truthy(row["derived_from"]), truthy(row["derivation"])
    removed = truthy(row.get("removed_at"))
    explained = truthy(row.get("removed_reason")), truthy(row.get("removed_requester"))
    if layer != "exact-bytes" and not all(derived):
        report(f"layer {layer} needs derived_from and derivation")
    if layer == "exact-bytes" and any(derived):
        report("layer exact-bytes carries no derived_from or derivation")
    if row["visibility"] != visibility_of(tier):
        report(f"tier {tier} is {visibility_of(tier)}, not {row['visibility']}")
    if removed and not all(explained):
        report("a removed row carries removed_at, removed_reason and removed_requester")
    if not removed and any(explained):
        report("removed_reason and removed_requester belong to a removed row, which carries removed_at")
    if removed and tier != NO_STORE:
        report(f"a removed row sits at t5-no-store, not {tier}")
    if len(set(mirrors)) != len(mirrors) or mirrors != sorted(mirrors):
        report(f"mirrors are sorted and distinct, not {_joined(row['mirrors'])}")
    if store in mirrors:
        report(f"{store} is this row's store, so it is not also a mirror of it")
    # Not applied to a removed row: its store is a note of where the bytes were, and a rule
    # that covered it would make a published object impossible to take down.
    public_tag = store == "github-release" or "github-release" in mirrors
    if not removed and row["visibility"] == "restricted" and public_tag:
        report(f"tier {tier} is restricted, so a public release is neither its home nor a mirror of it")
    if removed and mirrors:
        report(f"a removed row keeps no copy, so it names no mirror, not {_joined(row['mirrors'])}")


#: One manifest line.
MANIFEST_ROW: Final = zod.strict(
    {
        "sha256": _HEX64,
        "bytes": _BYTES,
        "media_type": _TEXT,
        "layer": zod.one_of(*LAYERS),
        "origin": _TEXT,
        "requested_url": zod.nullable(zod.text()),
        "first_seen_at": _UTC,
        "store": zod.one_of(*STORES),
        "mirrors": zod.default(zod.array(zod.one_of(*STORES)), []),
        "visibility": zod.one_of(*VISIBILITIES),
        "archive_tier": zod.one_of(*ARCHIVE_TIERS),
        "observed_licence": zod.nullable(zod.text()),
        "exposure": zod.one_of(*EXPOSURES),
        "derived_from": zod.nullable(_HEX64),
        "derivation": zod.nullable(zod.text()),
        "previous_path": zod.nullable(zod.text()),
        "source_ids": zod.array(_TEXT),
        "removed_at": zod.optional(_UTC),
        "removed_reason": zod.optional(_TEXT),
        "removed_requester": zod.optional(_TEXT),
    },
    zod.refinement(_row_rules),
)

#: One attempt at one route, kept whether it worked or not.
ROUTE_ATTEMPT: Final = zod.strict(
    {
        "route": zod.one_of(*ROUTES),
        "url": zod.nullable(zod.text()),
        "status": zod.nullable(zod.number(zod.INT)),
        "reason": zod.nullable(zod.one_of(*REASONS)),
    }
)


def _receipt_rules(value: JsonValue, report: zod.Report) -> None:
    receipt = cast("JsonObject", value)
    outcome, reason = receipt["outcome"], truthy(receipt["reason"])
    held = truthy(receipt["sha256"]) and receipt["bytes"] is not None and truthy(receipt["route"])
    if outcome != "failed" and not held:
        report(f"a {outcome} receipt carries sha256, bytes and route")
    if outcome != "stored" and not reason:
        report(f"a {outcome} receipt carries a reason")
    if outcome == "stored" and reason:
        report("a stored receipt carries no reason")


#: The fetch receipt, written for a failure as well as a success.
FETCH_RECEIPT: Final = zod.strict(
    {
        "schema_version": zod.one_of(1),
        "source_id": _TEXT,
        "requested_url": _TEXT,
        "final_url": zod.nullable(zod.text()),
        "http_status": zod.nullable(zod.number(zod.INT)),
        "fetched_at": _UTC,
        "content_type": zod.nullable(zod.text()),
        "bytes": zod.nullable(_BYTES),
        "sha256": zod.nullable(_HEX64),
        "route": zod.nullable(zod.one_of(*ROUTES)),
        "routes_tried": zod.array(ROUTE_ATTEMPT),
        "outcome": zod.one_of(*OUTCOMES),
        "reason": zod.nullable(zod.one_of(*REASONS)),
        "source_class": zod.one_of(*SOURCE_CLASSES),
    },
    zod.refinement(_receipt_rules),
)


def parse(schema: zod.Schema, value: JsonValue) -> JsonObject:
    """`schema.parse(value)`: what the schema read, or `Refused` with its issues."""
    payload = schema.run(value)
    if payload.issues:
        raise Refused([Issue(issue.code, issue.path, issue.message) for issue in payload.issues])
    return cast("JsonObject", payload.value)


# ---- reading line files ---------------------------------------------------------------


def raw_lines(path: Path) -> list[str]:
    r"""Every line of a file as `text.split("\n")` gives them, blank ones included."""
    try:
        return path.read_bytes().decode("utf-8").split("\n")
    except UnicodeDecodeError as error:
        raise StoreError(f"{path} is not UTF-8: {error}") from error
    except OSError as error:
        raise StoreError(f"{path} cannot be read: {error}") from error


def blank(line: str) -> bool:
    """Whether a line is one the readers pass over: nothing but space, as JavaScript counts it."""
    return not trim(line)


def parsed_lines(path: Path, schema: zod.Schema, where: str, *, skipping: bool) -> list[JsonObject]:
    """The rows of a line file held to a schema, refused with the line's number.

    The TypeScript's readers count differently: the manifest reader drops the blank lines and
    then counts, so its number is among the lines that are left (`skipping`), and the other
    readers count every line.
    """
    rows = []
    kept = 0
    for number, line in enumerate(raw_lines(path), 1):
        if blank(line):
            continue
        kept += 1
        at = kept if skipping else number
        payload = schema.run(parse_value(line, f"{where} line {at}"))
        if payload.issues:
            raise ArchiveError(f"{where} line {at}: {payload.issues[0].message}")
        rows.append(cast("JsonObject", payload.value))
    return rows


# ---- the manifest ---------------------------------------------------------------------


def _key(row: Mapping[str, JsonValue]) -> str:
    return cast("str", row["sha256"])


def read_shard(shard: str, root: Path) -> list[JsonObject]:
    """One shard's rows, in file order. A missing shard is an empty shard."""
    path = manifest_shard_path(shard, root)
    if not path.exists():
        return []
    return parsed_lines(path, MANIFEST_ROW, f"data/archive/objects/{shard}.jsonl", skipping=True)


def read_manifest(root: Path) -> dict[str, JsonObject]:
    """The whole manifest, keyed on sha256."""
    return {_key(row): row for shard in OBJECT_SHARDS for row in read_shard(shard, root)}


def _write_shard(shard: str, rows: Sequence[Mapping[str, JsonValue]], root: Path) -> None:
    ordered = sorted(rows, key=lambda row: utf16_key(_key(row)))
    write_text_atomically(
        manifest_shard_path(shard, root), "".join(f"{canonical_line(row)}\n" for row in ordered)
    )


def _find(sha256: str, root: Path) -> tuple[str, list[JsonObject], int]:
    shard = key_shard(sha256)
    rows = read_shard(shard, root)
    return shard, rows, next((at for at, row in enumerate(rows) if row["sha256"] == sha256), -1)


def upsert_row(row: JsonValue, root: Path) -> dict[str, bool]:
    """Add a row, or widen an existing row's source_ids.

    The bytes decide the row, so a second fetch of the same object changes nothing. A new row
    is written as it was given, not as the schema read it, which is what the TypeScript does:
    a row given with no `mirrors` is written with none.
    """
    given = row if isinstance(row, dict) else {}
    parsed = parse(MANIFEST_ROW, row)
    shard, rows, at = _find(_key(parsed), root)
    if at < 0:
        _write_shard(shard, [*rows, given], root)
        return {"added": True, "changed": True}
    held = cast("list[str]", rows[at]["source_ids"])
    merged = sorted({*held, *cast("list[str]", given["source_ids"])}, key=utf16_key)
    if len(merged) == len(held):
        return {"added": False, "changed": False}
    rows[at] = parse(MANIFEST_ROW, {**rows[at], "source_ids": cast("JsonValue", merged)})
    _write_shard(shard, rows, root)
    return {"added": False, "changed": True}


def _rewrite(sha256: str, root: Path, change: Callable[[JsonObject], JsonObject | None]) -> bool:
    """Change one row and write its shard, unless the key is not there or nothing changes."""
    shard, rows, at = _find(sha256, root)
    changed = None if at < 0 else change(rows[at])
    if changed is None:
        return False
    rows[at] = parse(MANIFEST_ROW, changed)
    _write_shard(shard, rows, root)
    return True


def update_row(sha256: str, patch: Mapping[str, JsonValue], root: Path) -> bool:
    """Change an object's licence columns in place. False when the key is not in the manifest.

    Visibility follows the tier. `observed_licence` left out of the patch keeps the string
    the row carries, and null clears it. A missing or null `exposure` keeps the row's.
    """

    def change(row: JsonObject) -> JsonObject:
        tier = patch.get("archive_tier")
        return {
            **row,
            "archive_tier": tier,
            "visibility": visibility_of(tier) if isinstance(tier, str) else "public",
            "observed_licence": patch["observed_licence"]
            if "observed_licence" in patch
            else row["observed_licence"],
            "exposure": row["exposure"] if patch.get("exposure") is None else patch["exposure"],
        }

    return _rewrite(sha256, root, change)


def mark_home(sha256: str, store: str, root: Path) -> bool:
    """Record which backend is an object's home, keeping the old home as a mirror.

    The one home never kept as a mirror is `local-cache`, a directory on whichever machine
    ran the command. Returns whether a row was written: false when the key is not in the
    manifest and false when the backend is the home already.
    """

    def change(row: JsonObject) -> JsonObject | None:
        if row["store"] == store:
            return None
        was = [] if row["store"] == "local-cache" else [cast("str", row["store"])]
        named = dict.fromkeys([*cast("list[str]", row["mirrors"]), *was])
        mirrors = sorted((mirror for mirror in named if mirror != store), key=utf16_key)
        return {**row, "store": store, "mirrors": cast("JsonValue", mirrors)}

    return _rewrite(sha256, root, change)


def mark_mirror(sha256: str, store: str, root: Path) -> bool:
    """Record that a backend holds a second copy of an object whose home is elsewhere.

    Returns whether a row was written: false when the key is not in the manifest and false
    when the row names the backend already, as its home or as a mirror.
    """

    def change(row: JsonObject) -> JsonObject | None:
        mirrors = cast("list[str]", row["mirrors"])
        if row["store"] == store or store in mirrors:
            return None
        return {**row, "mirrors": cast("JsonValue", sorted([*mirrors, store], key=utf16_key))}

    return _rewrite(sha256, root, change)


def mark_removed(
    sha256: str, removal: Mapping[str, JsonValue], root: Path, now: Callable[[], str] = utc_now
) -> bool:
    """Record a takedown on a row whose bytes have gone, or are going.

    The row moves to `t5-no-store`, so no publish path selects it again, its mirrors go,
    because it keeps no copy, and it records when, why and at whose request. The key, the
    byte count, the origin, `first_seen_at`, `store` and `source_ids` stay. False when the
    key is not in the manifest.
    """

    def change(row: JsonObject) -> JsonObject:
        facts = ("removed_reason", "removed_requester")
        said = {key: removal[key] for key in facts if key in removal}
        at = removal.get("removed_at")
        return {
            **{key: held for key, held in row.items() if key not in facts},
            "archive_tier": NO_STORE,
            "visibility": visibility_of(NO_STORE),
            "mirrors": [],
            "removed_at": now() if at is None else at,
            **said,
        }

    return _rewrite(sha256, root, change)


def check_manifest(root: Path) -> JsonObject:
    """Every row parses, sits in the shard its key names, and each shard is sorted and distinct."""
    problems: list[JsonValue] = []
    count = 0
    for shard in OBJECT_SHARDS:
        try:
            rows = read_shard(shard, root)
        except StoreError as error:
            problems.append(str(error))
            continue
        seen: set[str] = set()
        previous = ""
        for row in rows:
            key = _key(row)
            count += 1
            if key[0] != shard:
                problems.append(f"{key} is in shard {shard}, not {key[0]}")
            if key in seen:
                problems.append(f"{key} appears twice in shard {shard}")
            if key < previous:
                problems.append(f"shard {shard} is not sorted at {key}")
            seen.add(key)
            previous = key
    return {"rows": count, "problems": problems}


def verify_objects(root: Path) -> JsonObject:
    """Recompute every cached object against the manifest: `npm run archive -- verify`.

    A row at `t5-no-store` is expected to hold no bytes, so it is counted as removed and not
    as missing. Bytes still in the cache under one are a takedown that stopped halfway.
    """
    problems = cast("list[JsonValue]", check_manifest(root)["problems"])
    manifest = read_manifest(root)
    removed: list[JsonValue] = []
    missing: list[JsonValue] = []
    corrupt: list[JsonValue] = []
    verified = 0
    for key, row in manifest.items():
        held = has_object(key, root)
        if row["archive_tier"] == NO_STORE:
            when = f", removed at {row['removed_at']}" if is_removed(row) else ""
            if held:
                problems.append(f"{key} is at t5-no-store{when} and its bytes are still in the cache")
            else:
                removed.append(key)
        elif not held:
            missing.append(key)
        elif len(read_object(key, root)) != row["bytes"]:
            corrupt.append(key)
        else:
            verified += 1
    unlisted: list[JsonValue] = [key for key in cached_keys(root) if key not in manifest]
    return {
        "objects": len(manifest),
        "verified": verified,
        "removed": removed,
        "missing": missing,
        "corrupt": corrupt,
        "unlisted": unlisted,
        "problems": problems,
    }


# ---- the object cache -----------------------------------------------------------------


def cache_root(root: Path) -> Path:
    """The gitignored cache, or the folder `ARCHIVE_CACHE` names."""
    named = os.environ.get("ARCHIVE_CACHE")
    return root / CACHE_DIR if named is None else Path(named)


def _checked_key(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise ArchiveError(f"Not a sha256 key: {key}")
    return key


def object_path(key: str, root: Path) -> Path:
    """The cached file for a key: two-digit fan-out, then the key, gzipped."""
    return cache_root(root) / _checked_key(key)[:2] / f"{key}.gz"


def archive_asset_name(key: str) -> str:
    """The release asset an object is published as."""
    return f"{_checked_key(key)}.gz"


def archive_asset_url(shard: str, name: str) -> str:
    """Where a release asset is served from: the tag is `archive/objects-<shard>`."""
    if len(shard) != 1 or shard not in OBJECT_SHARDS:
        raise ArchiveError(f"Not a manifest shard: {shard}")
    if not _SAFE_NAME.fullmatch(name) or "/" in name:
        raise ArchiveError(f"Unsafe archive asset name: {name}")
    return f"https://github.com/{REPOSITORY}/releases/download/archive/objects-{shard}/{name}"


def gunzip(compressed: bytes, limit: int = MAX_EXPANDED) -> bytes:
    """What Node's `gunzipSync` expands a file to, or `ArchiveError` where it throws.

    Member after member, as Node reads them: after a member, a byte that is not zero starts
    another, which has to be whole, and a zero byte ends the reading whatever follows it.
    Python's `gzip` reads on past zero bytes and refuses what follows them if it is not a
    member, so it is not used. More than `limit` bytes is refused, as `maxOutputLength` does.
    """
    out = bytearray()
    rest = compressed
    while True:
        member = zlib.decompressobj(wbits=31)
        try:
            out += member.decompress(rest, limit + 1 - len(out))
        except zlib.error as error:
            raise ArchiveError(f"not gzip: {error}") from None
        if not member.eof or len(out) > limit:
            raise ArchiveError("not gzip: it ends early, or expands to more than is allowed")
        rest = member.unused_data
        if not rest or rest[0] == 0:
            return bytes(out)


def expand(compressed: bytes, digest: str, label: str, size: int | None = None) -> bytes:
    """Expand gzip bytes and check them against a digest, and a size when one is known."""
    try:
        expanded = gunzip(compressed, MAX_EXPANDED if size is None else size + 1)
    except ArchiveError:
        raise ArchiveError(f"{label} is not the listed gzip file") from None
    if (size is not None and len(expanded) != size) or sha256_hex(expanded) != digest:
        raise ArchiveError(f"{label} differs from its listed SHA-256")
    return expanded


def read_object(key: str, root: Path) -> bytes:
    """The expanded bytes of a cached object, verified. Raises when absent or not the object."""
    path = object_path(key, root)
    try:
        compressed = path.read_bytes()
    except OSError as error:
        raise StoreError(f"{path} cannot be read: {error}") from error
    return expand(compressed, key, str(path))


def has_object(key: str, root: Path) -> bool:
    """Whether the cache holds the key, with its bytes verified against it."""
    path = object_path(key, root)
    try:
        expand(path.read_bytes(), key, str(path))
    except (OSError, ArchiveError):
        return False
    return True


def cached_files(root: Path) -> list[tuple[str, Path]]:
    """Every key the cache holds a file for, with the file, in the order the folders list them."""
    base = cache_root(root)
    if not base.exists():
        return []
    found = []
    for folder in sorted(base.iterdir(), key=lambda path: utf16_key(path.name)):
        if not folder.is_dir():
            continue
        for file in sorted(folder.iterdir(), key=lambda path: utf16_key(path.name)):
            named = _CACHED.fullmatch(file.name)
            if named:
                found.append((named.group(1), file))
    return found


def cached_keys(root: Path) -> list[str]:
    """Every key the cache holds a file for."""
    return [key for key, _ in cached_files(root)]


def cache_contents(root: Path) -> JsonObject:
    """Every key the cache holds, and the bytes those gzip files occupy."""
    files = cached_files(root)
    return {
        "keys": [key for key, _ in files],
        "compressed_bytes": sum(file.stat().st_size for _, file in files),
    }


# ---- receipts -------------------------------------------------------------------------


def receipt_path(date: str, root: Path) -> Path:
    """The receipts file of one day."""
    if not _DATE.fullmatch(date):
        raise ArchiveError(f"Not a date: {date}")
    return root / AUDIT_DIR / f"{date}-archive-fetch.receipts.jsonl"


def append_receipt(receipt: JsonValue, root: Path) -> None:
    """Append one receipt to the file of the day it was fetched on, in one write."""
    parsed = parse(FETCH_RECEIPT, receipt)
    path = receipt_path(cast("str", parsed["fetched_at"])[:10], root)
    append_text(path, f"{canonical_line(parsed)}\n")


def read_receipts(root: Path) -> list[JsonObject]:
    """Every archive fetch receipt written so far, oldest file first."""
    folder = root / AUDIT_DIR
    if not folder.exists():
        return []
    receipts: list[JsonObject] = []
    for name in sorted((path.name for path in folder.iterdir()), key=utf16_key):
        if _RECEIPTS.fullmatch(name):
            receipts.extend(parsed_lines(folder / name, FETCH_RECEIPT, name, skipping=False))
    return receipts


def latest_by_source(receipts: Sequence[JsonObject]) -> dict[str, JsonObject]:
    """The latest receipt per source, by `fetched_at`. A tie goes to the later line."""
    latest: dict[str, JsonObject] = {}
    for receipt in receipts:
        source = cast("str", receipt["source_id"])
        held = latest.get(source)
        if held is None or cast("str", receipt["fetched_at"]) >= cast("str", held["fetched_at"]):
            latest[source] = receipt
    return latest
