"""What the layer commands print: `npm run layers -- build`, `check` and `thresholds`.

Each function returns what the TypeScript's command writes to standard output, the lines it
writes to standard error, and its exit status. `rewire_evidence.cli` does the writing.

`build` is the only one that writes, and it writes the two generated files under
`data/omics/layers/` and nothing else. `check` regenerates in memory and compares with the
committed files: an unchanged store has to give the same bytes. Both read the manifest and
the object cache of the checkout, and neither asks a network for anything.

`coverage`, `surface` and `show` are not here. They read receipts and source records, or
re-derive a layer's text for a reader, and belong with the span resolvers.
"""

from __future__ import annotations

from pathlib import Path

from rewire_evidence import archive, registry
from rewire_evidence.archivesaid import Said
from rewire_evidence.jsonl import StoreError
from rewire_evidence.sanity import SANITY_VERDICTS, THRESHOLDS

#: A progress line is written after each this many objects.
PROGRESS_EVERY = 100


def generate(root: Path) -> tuple[registry.BuildResult, list[str]]:
    """The registry the checkout's manifest and object cache give, and the progress lines."""
    manifest = archive.read_manifest(root)
    progress: list[str] = []

    def read(key: str) -> bytes:
        try:
            return archive.read_object(key, root)
        except (StoreError, archive.ArchiveError):
            raise archive.ArchiveError("the bytes are not in the object cache") from None

    def said(_: str, done: int) -> None:
        if done % PROGRESS_EVERY == 0:
            progress.append(f"  {done} of {len(manifest)} objects")

    return registry.build_registry(manifest.values(), read, said), progress


def say_build(root: Path) -> Said:
    """`layers build`: generate the registry from the manifest, and 1 if any object went unread."""
    result, progress = generate(root)
    registry.write_registry(result.records, root)
    registry.write_unsupported(result.unsupported, root)
    lines = [
        f"layers           {len(result.records)}",
        f"objects unread   {len(result.unreadable)}",
        f"no normaliser    {len(result.unsupported)}",
        *(
            f"sanity {verdict.ljust(10)}{sum(1 for record in result.records if record['sanity'] == verdict)}"
            for verdict in SANITY_VERDICTS
        ),
        f"wrote {registry.registry_path(root)}",
        f"wrote {registry.unsupported_path(root)}",
    ]
    return Said("\n".join(lines), int(bool(result.unreadable)), tuple(progress))


def _on_disk(path: Path) -> str:
    """A file as Node reads it as text, or no text for a file that is not there."""
    return path.read_bytes().decode("utf-8", "replace") if path.exists() else ""


def say_check(root: Path) -> Said:
    """`layers check`: regenerate and report any difference from the committed files."""
    result, progress = generate(root)
    problems: list[str] = []
    if registry.serialise_registry(result.records) != _on_disk(registry.registry_path(root)):
        problems.append(f"{registry.REGISTRY_FILE} is not what the store produces")
    if registry.serialise_unsupported(result.unsupported) != _on_disk(registry.unsupported_path(root)):
        problems.append(f"{registry.UNSUPPORTED_FILE} is not what the store produces")
    problems += [f"{problem['object_sha256']}: {problem['reason']}" for problem in result.unreadable]
    said = f"{len(problems)} problems" if problems else f"{len(result.records)} layers reproduce"
    return Said(said, int(bool(problems)), (*progress, *problems))


def say_thresholds() -> Said:
    """`layers thresholds`: every threshold of the sanity gate."""
    lines = [
        "sanity thresholds, each set from the measured distribution in shared/omics/layers/sanity.ts",
        *(f"  {name.ljust(22)}{value}" for name, value in THRESHOLDS.items()),
    ]
    return Said("\n".join(lines))
