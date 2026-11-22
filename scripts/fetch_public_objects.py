"""Fetch the stored objects a public release mirrors, into a folder laid out as the object cache is.

Usage: python scripts/fetch_public_objects.py <checkout> <objects>

Reads the manifest of a checkout of `rewire-benchmark-data` and fetches, for every row that is
public and names `github-release` among its mirrors, the release asset
`archive/objects-<shard>/<key>.gz` into `<objects>/<first two digits>/<key>.gz`. An object
already there, whose bytes expand to its key, is not fetched again, so a folder restored from
a cache costs a read of each file and no request.

The bytes kept are the bytes received. gzip does not write the same bytes on every zlib, so
nothing is compressed again: a file is checked by expanding it and hashing what comes out,
and one that does not expand to its key is not kept.

This is the one script here that asks a network for anything, and it asks only GitHub for
the release assets of the data repository. The layer gate reads the folder and asks nothing.
Fetching and pulling as the data repository's own commands do them is a later stage.

Exit status: 0 when every public object is held, 1 when any could not be fetched.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from rewire_evidence import archive

ATTEMPTS = 4
WORKERS = 8
TIMEOUT = 300


def held(key: str, file: Path) -> bool:
    """Whether the file is there and expands to its key."""
    try:
        archive.expand(file.read_bytes(), key, str(file))
    except (OSError, archive.ArchiveError):
        return False
    return True


def fetch(key: str, objects: Path) -> tuple[str, int, str]:
    """Fetch one object unless it is held. Returns its key, the bytes received and what went wrong."""
    file = objects / key[:2] / f"{key}.gz"
    if held(key, file):
        return key, 0, ""
    url = archive.archive_asset_url(archive.key_shard(key), archive.archive_asset_name(key))
    wrong = ""
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response:  # noqa: S310 - an https URL built here
                received = response.read()
            archive.expand(received, key, url)
        except (urllib.error.URLError, OSError, archive.ArchiveError) as error:
            wrong = str(error)
            time.sleep(2**attempt)
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        partial = file.with_name(f"{file.name}.{os.getpid()}.tmp")
        partial.write_bytes(received)
        partial.replace(file)
        return key, len(received), ""
    return key, 0, wrong


def main(arguments: list[str]) -> int:
    """Fetch every public object of the checkout's manifest. Returns the exit status."""
    if len(arguments) != 2:  # noqa: PLR2004 - a checkout and a folder
        print(__doc__)
        return 2
    root, objects = Path(arguments[0]), Path(arguments[1])
    keys = [
        key
        for key, row in archive.read_manifest(root).items()
        if row["visibility"] == "public" and "github-release" in cast("list[str]", row["mirrors"])
    ]
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(lambda key: fetch(key, objects), keys))
    fetched = [size for _, size, wrong in results if size and not wrong]
    failed = [(key, wrong) for key, _, wrong in results if wrong]
    for key, wrong in failed:
        print(f"{key}: {wrong}", file=sys.stderr)
    print(
        f"{len(keys):,} public objects: {len(fetched):,} fetched ({sum(fetched):,} bytes), "
        f"{len(keys) - len(fetched) - len(failed):,} already held, {len(failed):,} failed."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
