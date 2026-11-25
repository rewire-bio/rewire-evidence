"""Publish a knowledge-graph bundle release asset, ported from `scripts/kg/publish.ts`."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final, cast

from rewire_evidence.canonical import JsonObject, sha256_hex
from rewire_evidence.jsonl import StoreError

REPOSITORY: Final = "rewire-bio/rewire-benchmark-data"
DEFAULT_RDF_KG_MCP: Final = "git+https://github.com/rewire-bio/rdf-kg-mcp@v0.3.1"

#: Executes a command and raises on non-zero status or OS error.
type Runner = Callable[[Sequence[str], Path], str]


def default_runner(command: Sequence[str], cwd: Path) -> str:
    """Run an external CLI command (`uvx` or `gh`) with captured output."""
    completed = subprocess.run(  # noqa: S603 - explicit fixed tool invocations
        command,
        cwd=cwd,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise StoreError(f"`{' '.join(command[:3])}` failed ({completed.returncode}): {detail}")
    return completed.stdout


def _verify_existing_release(
    runner: Runner,
    root: Path,
    directory: Path,
    tag_and_name: tuple[str, str],
    hashes: tuple[str, Path],
) -> str:
    tag, name = tag_and_name
    built, discrepancies_file = hashes
    download = directory / "published"
    runner(
        ["gh", "release", "download", tag, "--repo", REPOSITORY, "--pattern", name, "--dir", str(download)],
        root,
    )
    published = sha256_hex((download / name).read_bytes())
    if published != built:
        raise StoreError(
            f"Published {name} ({published}) differs from this build ({built}); releases are immutable"
        )
    with contextlib.suppress(StoreError, OSError):
        runner(
            [
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                REPOSITORY,
                "--pattern",
                "discrepancies.json",
                "--dir",
                str(download),
            ],
            root,
        )
    published_discrepancies = download / "discrepancies.json"
    if published_discrepancies.exists() and (
        sha256_hex(published_discrepancies.read_bytes()) != sha256_hex(discrepancies_file.read_bytes())
    ):
        raise StoreError("Published discrepancies.json differs from this build; releases are immutable")
    return f"{tag} already published and byte-identical ({built})."


def _create_release(
    runner: Runner,
    root: Path,
    manifest: MappingInfo,
) -> str:
    tag = f"kg/{manifest.release_id}"
    name = f"kg-{manifest.release_id}.tar.gz"
    files = cast("JsonObject", manifest.manifest["files"])
    asserted_info = cast("JsonObject", files["asserted.nq"])
    inferred_info = cast("JsonObject", files["inferred.nq"])
    derived = cast("JsonObject", manifest.manifest.get("derived") or {})
    notes = "\n".join(
        [
            (
                f"Knowledge-graph bundle for release {manifest.release_id}: "
                f"{asserted_info['quads']} asserted "
                f"and {inferred_info['quads']} inferred statements, SHACL validated; "
                f"{derived.get('discrepancies', 0)} discrepancies between studies (discrepancies.json)."
            ),
            "",
            f"sha256: {manifest.built_sha}",
            "",
            "Serve it to Claude Code with [rdf-kg-mcp](https://github.com/rewire-bio/rdf-kg-mcp):",
            "",
            "```sh",
            (
                f"claude mcp add rewire-benchmarks -- uvx --from {manifest.tool} rdf-kg-mcp serve "
                f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
            ),
            "```",
        ]
    )
    target_args = ["--target", manifest.target_sha] if manifest.target_sha else []
    runner(
        [
            "gh",
            "release",
            "create",
            tag,
            *target_args,
            str(manifest.archive),
            str(manifest.manifest_file),
            str(manifest.discrepancies_file),
            "--repo",
            REPOSITORY,
            "--title",
            f"Knowledge graph {manifest.release_id}",
            "--notes",
            notes,
        ],
        root,
    )
    return f"Published {tag}: {name} ({manifest.built_sha})."


class MappingInfo:
    """Bundled release inputs and target metadata for release creation."""

    def __init__(
        self,
        release_id: str,
        manifest: JsonObject,
        built_sha: str,
        tool_and_target: tuple[str, str | None],
        assets: tuple[Path, Path, Path],
    ) -> None:
        """Store release metadata and asset file paths."""
        self.release_id = release_id
        self.manifest = manifest
        self.built_sha = built_sha
        self.tool, self.target_sha = tool_and_target
        self.archive, self.manifest_file, self.discrepancies_file = assets


def publish_bundle(
    root: Path,
    *,
    check_only: bool = False,
    rdf_kg_mcp: str | None = None,
    release_target_sha: str | None = None,
    runner: Runner = default_runner,
) -> list[str]:
    """Pack, verify, and publish or verify the immutable KG bundle release asset."""
    kg_dir = root / "public/kg"
    manifest_file = kg_dir / "manifest.json"
    if not manifest_file.exists():
        raise StoreError("No knowledge-graph bundle found; run npm run kg:build first")
    manifest = cast("JsonObject", json.loads(manifest_file.read_text("utf-8")))
    files = cast("JsonObject", manifest.get("files") or {})
    if "inferred.nq" not in files:
        raise StoreError("The bundle has no inferred graph; run npm run kg:build first")
    discrepancies_file = kg_dir / "discrepancies.json"
    if not discrepancies_file.exists():
        raise StoreError("No discrepancies.json found; run npm run kg:build first")

    tool = os.environ.get("RDF_KG_MCP", DEFAULT_RDF_KG_MCP) if rdf_kg_mcp is None else rdf_kg_mcp
    target_sha = os.environ.get("RELEASE_TARGET_SHA") if release_target_sha is None else release_target_sha
    release_id = cast("str", manifest["release_id"])
    name = f"kg-{release_id}.tar.gz"
    tag = f"kg/{release_id}"

    with tempfile.TemporaryDirectory(prefix="kg-") as scratch:
        directory = Path(scratch)
        archive = directory / name
        runner(["uvx", "--from", tool, "rdf-kg-mcp", "pack", "public/kg", "-o", str(archive)], root)
        runner(["uvx", "--from", tool, "rdf-kg-mcp", "verify", str(archive)], root)
        built = sha256_hex(archive.read_bytes())

        exists = True
        try:
            runner(["gh", "release", "view", tag, "--repo", REPOSITORY, "--json", "tagName"], root)
        except (StoreError, OSError):
            exists = False

        if exists:
            head = _verify_existing_release(runner, root, directory, (tag, name), (built, discrepancies_file))
        elif check_only:
            raise StoreError(f"{tag} is not published")
        else:
            info = MappingInfo(
                release_id,
                manifest,
                built,
                (tool, target_sha),
                (archive, manifest_file, discrepancies_file),
            )
            head = _create_release(runner, root, info)
        return [head, f"URL: https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"]
