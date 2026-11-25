"""Cloudflare R2 upload and download paths (`scripts/archive/r2.ts`, `upload.ts`, `pull.ts`).

Reads R2 credentials lazily at transfer time rather than at import time, signs S3 SigV4
requests, repairs manifest `store` homes when an upload finds an object already in the bucket,
and stores downloaded gzip bytes verbatim without recompressing.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast

from rewire_evidence import archive
from rewire_evidence.canonical import JsonObject, JsonValue, sha256_hex, utf16_key
from rewire_evidence.jsonl import write_bytes_atomically

VARIABLES: Final = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
_SPACE: Final = re.compile(r"\s+")
_REQUEST_TIMEOUT_SECONDS: Final = 120
_FAILURE_TEXT_LIMIT: Final = 300
_PROGRESS_EVERY: Final = 50
_HTTP_OK_MIN: Final = 200
_HTTP_OK_MAX: Final = 299


@dataclass(frozen=True)
class R2Settings:
    """Four Cloudflare R2 configuration values read from the environment at call time."""

    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str


@dataclass(frozen=True)
class SignedRequest:
    """A signed HTTPS request URL and headers for Cloudflare R2."""

    url: str
    headers: dict[str, str]


@dataclass(frozen=True)
class R2Reply:
    """Minimal HTTP response returned by an R2 or release transport."""

    status: int
    body: bytes = b""


class R2Transport(Protocol):
    """HTTP transport interface injected by callers and offline tests."""

    def __call__(
        self,
        url: str,
        *,
        method: str,
        headers: Mapping[str, str],
        body: bytes = b"",
    ) -> R2Reply:
        """Execute one HTTP request and return `R2Reply`."""


def read_settings(env: Mapping[str, str] | None = None) -> R2Settings:
    """Read R2 credentials from `env`, naming every missing variable at once."""
    source = os.environ if env is None else env
    missing = [name for name in VARIABLES if not source.get(name, "").strip()]
    if missing:
        raise archive.ArchiveError(
            f"The Cloudflare R2 backend needs {', '.join(missing)} in the environment. "
            "Every other archive command runs without a credential; this one does not, and it "
            "refuses here rather than part way through a transfer."
        )
    return R2Settings(
        account_id=source["R2_ACCOUNT_ID"].strip(),
        access_key_id=source["R2_ACCESS_KEY_ID"].strip(),
        secret_access_key=source["R2_SECRET_ACCESS_KEY"].strip(),
        bucket=source["R2_BUCKET"].strip(),
    )


def _hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def sign_request(
    settings: R2Settings,
    method: str,
    key: str,
    body: bytes = b"",
    now: datetime | None = None,
) -> SignedRequest:
    """Create an AWS SigV4 Authorization header for R2 (`region=auto`, `service=s3`)."""
    moment = datetime.now(UTC) if now is None else now.astimezone(UTC)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    date = stamp[:8]
    host = f"{settings.account_id}.r2.cloudflarestorage.com"
    payload_hash = sha256_hex(body)
    canonical_path = f"/{settings.bucket}/{key}"
    headers: dict[str, str] = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": stamp,
    }
    ordered_names = sorted(headers)
    signed_headers = ";".join(ordered_names)
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in ordered_names)
    canonical_request = f"{method}\n{canonical_path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    scope = f"{date}/auto/s3/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{sha256_hex(canonical_request.encode())}"
    signing_key = _hmac_sha256(
        _hmac_sha256(
            _hmac_sha256(_hmac_sha256(f"AWS4{settings.secret_access_key}".encode(), date), "auto"),
            "s3",
        ),
        "aws4_request",
    )
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={settings.access_key_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return SignedRequest(url=f"https://{host}{canonical_path}", headers=headers)


def platform_fetch(
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    body: bytes = b"",
) -> R2Reply:
    """Send an HTTPS request via the Python standard library."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise archive.ArchiveError(f"Refusing non-HTTPS archive URL: {url}")
    data = body if method == "PUT" else None
    request = urllib.request.Request(  # noqa: S310 - HTTPS scheme checked above
        url, data=data, headers=dict(headers), method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            return R2Reply(status=response.status, body=response.read())
    except urllib.error.HTTPError as error:
        return R2Reply(status=error.code, body=error.read())


def assert_storable(row: Mapping[str, JsonValue]) -> None:
    """Refuse `t5-no-store` (or removed) manifest rows before any visibility check or upload."""
    sha = row["sha256"]
    if row["archive_tier"] == archive.NO_STORE:
        removed_note = f", removed at {row['removed_at']}" if archive.is_removed(row) else ""
        raise archive.ArchiveError(
            f"Refusing to upload {sha}: it is at {archive.NO_STORE}{removed_note}, "
            "so its bytes were taken down and never go back."
        )
    if archive.is_removed(row):
        raise archive.ArchiveError(
            f"Refusing to upload {sha}: removed at {row['removed_at']}, "
            "so its bytes were taken down and never go back."
        )


def upload_objects(
    root: Path = Path(),
    *,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    fetch: R2Transport | None = None,
) -> JsonObject:
    """Put every cached non-removed manifest object into R2 and mark the manifest home."""
    settings = read_settings(env)
    transport = platform_fetch if fetch is None else fetch
    manifest = archive.read_manifest(root)
    cached = set(archive.cached_keys(root))

    considered = uploaded = skipped = would_upload = removed = marked = 0
    failed: list[JsonValue] = []

    for key in sorted(manifest, key=utf16_key):
        row = manifest[key]
        if row["archive_tier"] == archive.NO_STORE:
            removed += 1
            continue
        if key not in cached:
            continue
        assert_storable(row)
        considered += 1
        name = archive.archive_asset_name(key)
        if dry_run:
            would_upload += 1
            continue
        body = archive.object_path(key, root).read_bytes()
        head = sign_request(settings, "HEAD", name)
        existing = transport(head.url, method="HEAD", headers=head.headers)
        if existing.status == 200:  # noqa: PLR2004 - HTTP 200 OK
            skipped += 1
            if archive.mark_home(key, "r2", root):
                marked += 1
            continue
        put = sign_request(settings, "PUT", name, body)
        reply = transport(put.url, method="PUT", headers=put.headers, body=body)
        if _HTTP_OK_MIN <= reply.status <= _HTTP_OK_MAX:
            uploaded += 1
            if archive.mark_home(key, "r2", root):
                marked += 1
        else:
            failed.append({"key": key, "status": reply.status})

    return {
        "considered": considered,
        "uploaded": uploaded,
        "skipped": skipped,
        "would_upload": would_upload,
        "dry_run": dry_run,
        "removed": removed,
        "failed": failed,
        "marked": marked,
    }


def on_a_release(row: Mapping[str, JsonValue]) -> bool:
    """Whether a GitHub release tag holds this row's bytes."""
    mirrors = cast("Sequence[str]", row.get("mirrors", ()))
    return row["store"] == "github-release" or "github-release" in mirrors


def select_rows(
    manifest: Mapping[str, JsonObject],
    *,
    keys: Sequence[str] | None = None,
    sources: Sequence[str] | None = None,
) -> list[JsonObject]:
    """Select manifest rows matching optional key and source filters, sorted by SHA-256."""
    wanted_keys = set(keys) if keys else None
    wanted_sources = set(sources) if sources else None
    rows: list[JsonObject] = []
    for key in sorted(manifest, key=utf16_key):
        row = manifest[key]
        if wanted_keys is not None and key not in wanted_keys:
            continue
        if wanted_sources is not None:
            row_sources = cast("Sequence[str]", row["source_ids"])
            if not any(source_id in wanted_sources for source_id in row_sources):
                continue
        rows.append(row)
    return rows


def _failure_text(raw: bytes) -> str:
    try:
        decoded = raw.decode("utf-8", "replace")
    except ValueError:  # pragma: no cover - defensive on custom error decoder
        return "no body"
    cleaned = _SPACE.sub(" ", decoded.strip())
    return f"{cleaned[:_FAILURE_TEXT_LIMIT]}..." if len(cleaned) > _FAILURE_TEXT_LIMIT else cleaned


def _download_one(
    row: JsonObject,
    *,
    source: str,
    settings: R2Settings | None,
    transport: R2Transport,
    root: Path,
) -> tuple[bool, int, int, JsonObject | None]:
    key = cast("str", row["sha256"])
    name = archive.archive_asset_name(key)
    if settings is not None:
        signed = sign_request(settings, "GET", name)
        url, headers = signed.url, signed.headers
    else:
        url = archive.archive_asset_url(archive.key_shard(key), name)
        headers = {}
    try:
        reply = transport(url, method="GET", headers=headers)
    except (OSError, http.client.HTTPException, archive.ArchiveError) as error:
        return False, 0, 0, {"key": key, "status": None, "message": str(error)}
    if reply.status != 200:  # noqa: PLR2004 - HTTP 200 OK
        return False, 0, 0, {"key": key, "status": reply.status, "message": _failure_text(reply.body)}
    label = f"{'R2 object' if source == 'r2' else 'release asset'} {key}"
    try:
        expanded = archive.expand(reply.body, key, label, cast("int", row["bytes"]))
    except archive.ArchiveError as error:
        return False, 0, 0, {"key": key, "status": reply.status, "message": str(error)}
    write_bytes_atomically(archive.object_path(key, root), reply.body)
    return True, len(expanded), len(reply.body), None


def _select_pull_targets(
    matched: Sequence[JsonObject], *, source: str, root: Path
) -> tuple[list[JsonObject], int, int, int, int, int, int, int]:
    removed = selected = selected_restricted = selected_public = 0
    already_held = already_in_the_bucket = no_remote_copy = 0
    wanted: list[JsonObject] = []

    for row in matched:
        if row["archive_tier"] == archive.NO_STORE:
            removed += 1
            continue
        selected += 1
        if row["visibility"] == "restricted":
            selected_restricted += 1
        else:
            selected_public += 1
        key = cast("str", row["sha256"])
        if archive.has_object(key, root):
            already_held += 1
            continue
        if source == "release" and not on_a_release(row):
            mirrors = cast("Sequence[str]", row.get("mirrors", ()))
            if row["store"] == "r2" or "r2" in mirrors:
                already_in_the_bucket += 1
            else:
                no_remote_copy += 1
            continue
        wanted.append(row)
    return (
        wanted,
        removed,
        selected,
        selected_restricted,
        selected_public,
        already_held,
        already_in_the_bucket,
        no_remote_copy,
    )


def pull_objects(  # noqa: PLR0913 - mirrors scripts/archive/pull.ts PullOptions directly
    root: Path = Path(),
    *,
    source: str = "r2",
    keys: Sequence[str] | None = None,
    sources: Sequence[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    fetch: R2Transport | None = None,
    log: Callable[[str], None] | None = None,
) -> JsonObject:
    """Download missing objects from R2 or public release assets, verifying before keeping bytes."""
    if source not in {"r2", "release"}:
        raise archive.ArchiveError(f"--from takes r2 or release, not {source}")
    emit = (lambda _line: None) if log is None else log
    manifest = archive.read_manifest(root)
    matched = select_rows(manifest, keys=keys, sources=sources)
    (
        wanted,
        removed,
        selected,
        selected_restricted,
        selected_public,
        already_held,
        already_in_the_bucket,
        no_remote_copy,
    ) = _select_pull_targets(matched, source=source, root=root)

    take = wanted if limit is None else wanted[:limit]
    held_back = len(wanted) - len(take)

    summary: JsonObject = {
        "manifest_rows": len(manifest),
        "matched": len(matched),
        "removed": removed,
        "selected": selected,
        "selected_restricted": selected_restricted,
        "selected_public": selected_public,
        "already_held": already_held,
        "held_back_by_limit": held_back,
        "already_in_the_bucket": already_in_the_bucket,
        "no_remote_copy": no_remote_copy,
        "downloaded": 0,
        "expanded_bytes": 0,
        "compressed_bytes": 0,
        "dry_run": dry_run,
        "failed": [],
    }
    if dry_run:
        origin = "bucket" if source == "r2" else "release assets"
        emit(
            f"dry run: {len(take)} objects would be fetched from the {origin}, "
            f"{already_held} are already held"
        )
        return summary
    if not take:
        emit(f"nothing to fetch: {already_held} of {selected} selected objects are already held and verified")
        return summary

    settings = read_settings(env) if source == "r2" else None
    transport = platform_fetch if fetch is None else fetch
    downloaded = expanded_total = compressed_total = 0
    failed: list[JsonValue] = []

    for row in take:
        ok, expanded_len, compressed_len, failure = _download_one(
            row, source=source, settings=settings, transport=transport, root=root
        )
        if not ok:
            assert failure is not None  # noqa: S101 - failure always accompanies ok=False
            failed.append(failure)
            continue
        downloaded += 1
        expanded_total += expanded_len
        compressed_total += compressed_len
        if downloaded % _PROGRESS_EVERY == 0:
            emit(f"  {downloaded} of {len(take)} objects")

    summary["downloaded"] = downloaded
    summary["expanded_bytes"] = expanded_total
    summary["compressed_bytes"] = compressed_total
    summary["failed"] = failed
    return summary
