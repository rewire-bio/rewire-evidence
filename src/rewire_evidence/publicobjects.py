"""Fetch the stored objects a public release mirrors, into a folder laid out as the object cache is.

The layer gate reads stored bytes, and a checkout holds none. For every manifest row that is
public and names `github-release` among its mirrors, the release asset
`archive/objects-<shard>/<key>.gz` is fetched into `<objects>/<first two digits>/<key>.gz`.
An object already there, whose bytes expand to its key, is not fetched again, so a folder
restored from a cache costs a read of each file and no request.

The bytes kept are the bytes received. gzip does not write the same bytes on every zlib, so
nothing is compressed again: a file is checked by expanding it and hashing what comes out,
and one that does not expand to its key is not kept.

This is the one module here that asks a network for anything, and it asks only GitHub for
the release assets of the data repository. Fetching and pulling as the data repository's own
commands do them is a later stage.
"""

from __future__ import annotations

import http.client
import os
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rewire_evidence import archive
from rewire_evidence.jsonl import StoreError
from rewire_evidence.layerparity import fetchable

#: Requests made for one object before it is given up.
ATTEMPTS: Final = 4
#: Objects fetched at once.
WORKERS: Final = 8
#: Seconds one request may take.
TIMEOUT: Final = 300
#: What a request that did not give the object raises. A response that ends early is an
#: `http.client.IncompleteRead`, which is neither an `OSError` nor a `URLError`.
FAILURES: Final = (OSError, http.client.HTTPException, archive.ArchiveError)

Read = Callable[[str], bytes]


def read_url(url: str) -> bytes:
    """The bytes a URL serves."""
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:  # noqa: S310 - the callers build the URL
        received: bytes = response.read()
    return received


@dataclass(frozen=True)
class Fetched:
    """What became of one object."""

    key: str
    #: Bytes received, or 0 for an object that was already held or could not be fetched.
    received: int = 0
    #: Why the object could not be fetched, or no text.
    wrong: str = ""


def held(key: str, file: Path) -> bool:
    """Whether the file is there and expands to its key."""
    try:
        archive.expand(file.read_bytes(), key, str(file))
    except (OSError, archive.ArchiveError):
        return False
    return True


def fetch(
    key: str, objects: Path, read: Read = read_url, sleep: Callable[[float], None] = time.sleep
) -> Fetched:
    """Fetch one object unless it is held, asking again after a failure and waiting longer each time."""
    file = objects / key[:2] / f"{key}.gz"
    if held(key, file):
        return Fetched(key)
    url = archive.archive_asset_url(archive.key_shard(key), archive.archive_asset_name(key))
    wrong = ""
    for attempt in range(ATTEMPTS):
        try:
            received = read(url)
            archive.expand(received, key, url)
        except FAILURES as error:
            wrong = str(error) or type(error).__name__
            sleep(2**attempt)
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        partial = file.with_name(f"{file.name}.{os.getpid()}.tmp")
        partial.write_bytes(received)
        partial.replace(file)
        return Fetched(key, len(received))
    return Fetched(key, wrong=wrong)


def fetch_public(
    root: Path, objects: Path, read: Read = read_url, sleep: Callable[[float], None] = time.sleep
) -> list[Fetched]:
    """Fetch every object the checkout's manifest says a public release mirrors, in manifest order."""
    keys = [key for key, row in archive.read_manifest(root).items() if fetchable(row)]
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(lambda key: fetch(key, objects, read, sleep), keys))


def main(
    arguments: Sequence[str], usage: str, read: Read = read_url, sleep: Callable[[float], None] = time.sleep
) -> int:
    """The command: fetch, say what was fetched, and return the exit status."""
    if len(arguments) != 2:  # noqa: PLR2004 - a checkout and a folder
        sys.stdout.write(f"{usage}\n")
        return 2
    try:
        results = fetch_public(Path(arguments[0]), Path(arguments[1]), read, sleep)
    except StoreError as error:
        sys.stderr.write(f"Cannot fetch: {error}\n")
        return 2
    fetched = [result.received for result in results if result.received]
    failed = [result for result in results if result.wrong]
    for result in failed:
        sys.stderr.write(f"{result.key}: {result.wrong}\n")
    sys.stdout.write(
        f"{len(results):,} public objects: {len(fetched):,} fetched ({sum(fetched):,} bytes), "
        f"{len(results) - len(fetched) - len(failed):,} already held, {len(failed):,} failed.\n"
    )
    return 1 if failed else 0
