"""Fetch the stored objects a public release mirrors, into a folder laid out as the object cache is.

Usage: python scripts/fetch_public_objects.py <checkout> <objects>

Reads the manifest of a checkout of `rewire-benchmark-data` and fetches, for every row that is
public and names `github-release` among its mirrors, the release asset
`archive/objects-<shard>/<key>.gz` into `<objects>/<first two digits>/<key>.gz`. An object
already there, whose bytes expand to its key, is not fetched again, so a folder restored from
a cache costs a read of each file and no request.

The bytes kept are the bytes received, and one that does not expand to its key is not kept.

This is the one script here that asks a network for anything, and it asks only GitHub for
the release assets of the data repository. The layer gate reads the folder and asks nothing.

The work is in `rewire_evidence.publicobjects`, where the tests cover it. This file is the
command.

Exit status: 0 when every public object is held, 1 when any could not be fetched, 2 when the
run could not be made.
"""

from __future__ import annotations

import sys

from rewire_evidence.publicobjects import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], __doc__))
