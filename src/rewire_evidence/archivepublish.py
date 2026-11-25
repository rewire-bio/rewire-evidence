"""GitHub release asset publish path for public archive objects (`scripts/archive/publish.ts`).

Publishes public cached objects as assets on `archive/objects-<0-f>` tags without clobbering,
refuses `t5-no-store` by name before any visibility check, and promotes `local-cache` rows to
`github-release` home while recording R2-homed rows with a `github-release` mirror.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast

from rewire_evidence import archive
from rewire_evidence.canonical import JsonObject, JsonValue, utf16_key

_SHARD: Final = re.compile(r"[0-9a-f]", re.ASCII)
_PROGRESS_SHOWN_FAILURES: Final = 10


class ReleaseClient(Protocol):
    """GitHub release interface injected so tests never shell out to `gh`."""

    def assets(self, tag: str) -> list[str] | None:
        """List asset filenames on `tag`, or `None` when the release does not exist."""

    def create(self, tag: str, title: str, notes: str) -> None:
        """Create a non-latest release at `tag` with `title` and `notes`."""

    def upload(self, tag: str, file: Path) -> None:
        """Upload `file` as an asset onto `tag` without clobbering."""


@dataclass(frozen=True)
class PublishCandidate:
    """One cached public object selected for publishing to a release shard."""

    key: str
    shard: str
    tag: str
    name: str
    url: str
    bytes: int
    compressed_bytes: int
    archive_tier: str
    row: JsonObject


@dataclass(frozen=True)
class PublishPlan:
    """Everything `plan_publish` measured across the manifest and local cache."""

    manifest_rows: int
    public_rows: int
    public_by_tier: dict[str, int]
    public_bytes: int
    public_compressed_bytes: int
    refused: int
    refused_by_tier: dict[str, int]
    public_not_in_cache: int
    candidates: list[PublishCandidate]
    by_tag: dict[str, int]


def archive_tag(shard: str) -> str:
    """Release tag carrying objects whose SHA-256 begins with `shard`."""
    if not _SHARD.fullmatch(shard):
        raise archive.ArchiveError(f"Not a manifest shard: {shard}")
    return f"archive/objects-{shard}"


def shard_notes(shard: str) -> str:
    """Release body notes describing one archive shard tag."""
    return "\n".join(
        [
            f"Archive objects whose sha256 begins with {shard}.",
            "",
            "Each asset is named <sha256>.gz and holds the gzip of the exact bytes of one",
            "primary source. Expand it and check the sha256 against the asset name.",
            "",
            f"The row for every asset here is in data/archive/objects/{shard}.jsonl: the media type,",
            "the byte count, the URL the bytes came from, the licence tier they are published under",
            "and the source records they answer for.",
            "",
            "Only objects at a licence tier that permits publication are here. See",
            "docs/source-preservation.md, which also carries the takedown contact.",
        ]
    )


def _gh(*args: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed gh command arguments matching publish.ts
        ["gh", *args],  # noqa: S607 - fixed gh binary matching publish.ts
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


class _GhReleaseClient:
    """Default `ReleaseClient` shelling out to the GitHub CLI."""

    def __init__(self, repo: str) -> None:
        self._repo = repo

    def assets(self, tag: str) -> list[str] | None:
        try:
            raw = _gh("release", "view", tag, "--repo", self._repo, "--json", "assets")
        except subprocess.CalledProcessError as error:
            output = f"{error.stderr or ''}{error}"
            if re.search(r"release not found", output, re.IGNORECASE):
                return None
            raise archive.ArchiveError(f"gh release view {tag} failed: {output.strip()}") from error
        payload = cast("JsonObject", json.loads(raw))
        entries = cast("list[JsonObject]", payload.get("assets", []))
        return [cast("str", entry["name"]) for entry in entries]

    def create(self, tag: str, title: str, notes: str) -> None:
        target = os.environ.get("RELEASE_TARGET_SHA")
        args = ["release", "create", tag, "--repo", self._repo, "--latest=false"]
        if target:
            args.extend(["--target", target])
        args.extend(["--title", title, "--notes", notes])
        _gh(*args)

    def upload(self, tag: str, file: Path) -> None:
        _gh("release", "upload", tag, "--repo", self._repo, str(file))


def gh_client(repo: str = archive.REPOSITORY) -> ReleaseClient:
    """Return a `ReleaseClient` backed by the `gh` CLI for `repo`."""
    return _GhReleaseClient(repo)


def assert_publishable(row: Mapping[str, JsonValue]) -> None:
    """Refuse `t5-no-store` by name first, then any non-public tier or visibility."""
    tier = cast("str", row["archive_tier"])
    sha = row["sha256"]
    if tier == archive.NO_STORE:
        removed_note = f", removed at {row['removed_at']}" if archive.is_removed(row) else ""
        raise archive.ArchiveError(
            f"Refusing to publish {sha}: it is at {archive.NO_STORE}{removed_note}, "
            "and a removed or unstored object may never become a release asset."
        )
    effective = archive.visibility_of(tier)
    if row["visibility"] != "public" or effective != "public":
        raise archive.ArchiveError(
            f"Refusing to publish {sha}: tier {tier} is {effective}, "
            "and only a public object may become a release asset."
        )


def _compressed_bytes(key: str, root: Path) -> int:
    try:
        return archive.object_path(key, root).stat().st_size
    except OSError:
        return 0


def plan_publish(root: Path = Path(), *, limit: int | None = None) -> PublishPlan:
    """Compute what a publish run would upload from the manifest and verified local cache."""
    manifest = archive.read_manifest(root)
    public_by_tier: dict[str, int] = {}
    refused_by_tier: dict[str, int] = {}
    cached: list[PublishCandidate] = []
    public_rows = public_bytes = public_compressed = refused = not_in_cache = 0

    for key in sorted(manifest, key=utf16_key):
        row = manifest[key]
        tier = cast("str", row["archive_tier"])
        if row["visibility"] != "public":
            refused += 1
            refused_by_tier[tier] = refused_by_tier.get(tier, 0) + 1
            continue
        public_rows += 1
        public_by_tier[tier] = public_by_tier.get(tier, 0) + 1
        row_bytes = cast("int", row["bytes"])
        public_bytes += row_bytes
        if not archive.has_object(key, root):
            not_in_cache += 1
            continue
        shard = archive.key_shard(key)
        name = archive.archive_asset_name(key)
        compressed = _compressed_bytes(key, root)
        public_compressed += compressed
        cached.append(
            PublishCandidate(
                key=key,
                shard=shard,
                tag=archive_tag(shard),
                name=name,
                url=archive.archive_asset_url(shard, name),
                bytes=row_bytes,
                compressed_bytes=compressed,
                archive_tier=tier,
                row=row,
            )
        )

    candidates = cached if limit is None else cached[:limit]
    by_tag: dict[str, int] = {}
    for candidate in candidates:
        by_tag[candidate.tag] = by_tag.get(candidate.tag, 0) + 1

    return PublishPlan(
        manifest_rows=len(manifest),
        public_rows=public_rows,
        public_by_tier=public_by_tier,
        public_bytes=public_bytes,
        public_compressed_bytes=public_compressed,
        refused=refused,
        refused_by_tier=refused_by_tier,
        public_not_in_cache=not_in_cache,
        candidates=candidates,
        by_tag=by_tag,
    )


def _mark_published(published: Sequence[PublishCandidate], root: Path) -> int:
    marked = 0
    for candidate in published:
        row = candidate.row
        mirrors = cast("Sequence[str]", row.get("mirrors", ()))
        if row["store"] == "github-release" or "github-release" in mirrors:
            continue
        wrote = (
            archive.mark_home(candidate.key, "github-release", root)
            if row["store"] == "local-cache"
            else archive.mark_mirror(candidate.key, "github-release", root)
        )
        if wrote:
            marked += 1
    return marked


def publish_objects(  # noqa: PLR0913 - mirrors scripts/archive/publish.ts publishObjects options
    root: Path = Path(),
    *,
    limit: int | None = None,
    dry_run: bool = False,
    mark: bool = True,
    client: ReleaseClient | None = None,
    log: Callable[[str], None] | None = None,
) -> JsonObject:
    """Publish every public cached manifest object absent from its release shard and mark rows."""
    release_client = gh_client() if client is None else client
    emit = (lambda _line: None) if log is None else log
    plan = plan_publish(root, limit=limit)

    listings: dict[str, list[str] | None] = {}
    tags_created: list[JsonValue] = []
    tags_present: list[JsonValue] = []
    for tag in sorted(plan.by_tag, key=utf16_key):
        assets = release_client.assets(tag)
        listings[tag] = assets
        if assets is None:
            tags_created.append(tag)
        else:
            tags_present.append(tag)

    uploaded = already_present = 0
    failed: list[JsonValue] = []
    urls: list[JsonValue] = []
    published: list[PublishCandidate] = []

    for candidate in plan.candidates:
        assert_publishable(candidate.row)
        existing = listings.get(candidate.tag)
        if existing is not None and candidate.name in existing:
            already_present += 1
            published.append(candidate)
            continue
        if dry_run:
            urls.append(candidate.url)
            continue
        if listings.get(candidate.tag) is None:
            release_client.create(
                candidate.tag, f"Archive objects {candidate.shard}", shard_notes(candidate.shard)
            )
            listings[candidate.tag] = []
            emit(f"created {candidate.tag}")
        try:
            release_client.upload(candidate.tag, archive.object_path(candidate.key, root))
        except (archive.ArchiveError, OSError, subprocess.SubprocessError) as error:
            failed.append({"key": candidate.key, "message": str(error).strip()})
            continue
        listings[candidate.tag] = [*(listings.get(candidate.tag) or []), candidate.name]
        uploaded += 1
        urls.append(candidate.url)
        published.append(candidate)
        emit(f"uploaded {candidate.name} to {candidate.tag}")

    marked = _mark_published(published, root) if not dry_run and mark else 0
    return {
        "dry_run": dry_run,
        "manifest_rows": plan.manifest_rows,
        "public_rows": plan.public_rows,
        "public_by_tier": cast("JsonValue", plan.public_by_tier),
        "public_bytes": plan.public_bytes,
        "public_compressed_bytes": plan.public_compressed_bytes,
        "refused": plan.refused,
        "refused_by_tier": cast("JsonValue", plan.refused_by_tier),
        "public_not_in_cache": plan.public_not_in_cache,
        "considered": len(plan.candidates),
        "uploaded": uploaded,
        "already_present": already_present,
        "failed": failed,
        "tags_created": tags_created,
        "tags_present": tags_present,
        "marked": marked,
        "urls": urls,
    }
