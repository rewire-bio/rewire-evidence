"""Fetching the public objects, with the network stood in for by a function.

No test here asks a network for anything. A request is a function from a URL to bytes, and
each test gives one that serves, fails or serves other bytes, as GitHub might.
"""

from __future__ import annotations

import gzip
import http.client
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from rewire_evidence import archive, publicobjects
from rewire_evidence.canonical import canonical_line
from tests.layered import OBJECTS, key_of, row_for

PUBLIC = ("text", "html", "png")
SCRIPT = Path(__file__).parent.parent / "scripts" / "fetch_public_objects.py"


def url_of(name: str) -> str:
    key = key_of(name)
    return f"https://github.com/rewire-bio/rewire-benchmark-data/releases/download/archive/objects-{key[0]}/{key}.gz"


#: What the release serves: each public object, compressed once and for all.
SERVED = {url_of(name): gzip.compress(OBJECTS[name], mtime=0) for name in PUBLIC}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A checkout whose manifest holds three public objects and one that is not mirrored."""
    rows = [
        row_for(name, {"visibility": "public", "archive_tier": "t0-public", "mirrors": ["github-release"]})
        for name in PUBLIC
    ]
    rows += [row_for("jats"), row_for("crlf", {"visibility": "public", "archive_tier": "t0-public"})]
    folder = tmp_path / "store"
    for row in sorted(rows, key=lambda row: row["sha256"]):
        shard = folder / f"data/archive/objects/{row['sha256'][0]}.jsonl"
        shard.parent.mkdir(parents=True, exist_ok=True)
        with shard.open("a", encoding="utf-8") as file:
            file.write(canonical_line(row) + "\n")
    return folder


def never(url: str) -> bytes:
    raise AssertionError(f"{url} was asked for")


def test_every_public_object_is_fetched_once_and_kept_as_it_was_received(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    objects = tmp_path / "objects"
    assert publicobjects.main([str(root), str(objects)], "usage", SERVED.__getitem__) == 0
    size = sum(len(data) for data in SERVED.values())
    assert (
        capsys.readouterr().out
        == f"3 public objects: 3 fetched ({size:,} bytes), 0 already held, 0 failed.\n"
    )
    # The bytes kept are the bytes received, in the layout of the cache, and nothing else is left.
    kept = {
        str(file.relative_to(objects)): file.read_bytes() for file in objects.rglob("*") if file.is_file()
    }
    assert kept == {f"{key_of(name)[:2]}/{key_of(name)}.gz": SERVED[url_of(name)] for name in PUBLIC}
    # A second run reads each file and asks for nothing.
    assert publicobjects.main([str(root), str(objects)], "usage", never) == 0
    assert capsys.readouterr().out == "3 public objects: 0 fetched (0 bytes), 3 already held, 0 failed.\n"


def test_a_file_that_is_not_its_key_is_fetched_again(tmp_path: Path) -> None:
    objects = tmp_path / "objects"
    file = objects / key_of("text")[:2] / f"{key_of('text')}.gz"
    file.parent.mkdir(parents=True)
    file.write_bytes(gzip.compress(b"other bytes"))
    assert not publicobjects.held(key_of("text"), file)
    found = publicobjects.fetch(key_of("text"), objects, SERVED.__getitem__)
    assert found == publicobjects.Fetched(key_of("text"), len(SERVED[url_of("text")]))
    assert publicobjects.held(key_of("text"), file)
    assert file.read_bytes() == SERVED[url_of("text")]


def test_a_response_that_ends_early_is_asked_for_again(tmp_path: Path) -> None:
    # A short read is no OSError and no URLError, so a fetch that catches only those ends in a traceback.
    assert not issubclass(http.client.IncompleteRead, (OSError, urllib.error.URLError))
    answers: list[bytes | Exception] = [
        http.client.IncompleteRead(b"part", 9),
        urllib.error.URLError("the name did not resolve"),
        gzip.compress(b"bytes of some other object"),
        SERVED[url_of("html")],
    ]
    waits: list[float] = []

    def read(url: str) -> bytes:
        assert url == url_of("html")
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    found = publicobjects.fetch(key_of("html"), tmp_path, read, waits.append)
    assert found == publicobjects.Fetched(key_of("html"), len(SERVED[url_of("html")]))
    assert (answers, waits) == ([], [1, 2, 4])


def test_an_object_that_never_arrives_fails_the_run_and_is_named(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    waits: list[float] = []

    def read(url: str) -> bytes:
        if url == url_of("png"):
            raise http.client.IncompleteRead(b"")
        if url == url_of("html"):
            raise http.client.HTTPException
        return SERVED[url]

    objects = tmp_path / "objects"
    assert publicobjects.main([str(root), str(objects)], "usage", read, waits.append) == 1
    assert sorted(waits) == [1, 1, 2, 2, 4, 4, 8, 8]
    said = capsys.readouterr()
    assert sorted(said.err.splitlines()) == sorted(
        [f"{key_of('png')}: IncompleteRead(0 bytes read)", f"{key_of('html')}: HTTPException"]
    )
    size = len(SERVED[url_of("text")])
    assert said.out == f"3 public objects: 1 fetched ({size:,} bytes), 0 already held, 2 failed.\n"
    assert [file.name for file in objects.rglob("*") if file.is_file()] == [f"{key_of('text')}.gz"]


def test_a_run_that_cannot_be_made_says_why(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert publicobjects.main([], "how it is used", never) == 2
    assert capsys.readouterr().out == "how it is used\n"
    shard = tmp_path / "data/archive/objects/0.jsonl"
    shard.parent.mkdir(parents=True)
    shard.write_text("not a row\n", encoding="utf-8")
    assert publicobjects.main([str(tmp_path), str(tmp_path / "objects")], "usage", never) == 2
    assert capsys.readouterr().err.startswith("Cannot fetch: ")
    # A checkout with no manifest has no public object, which is a run that fetched all of none.
    assert publicobjects.main([str(tmp_path / "bare"), str(tmp_path / "objects")], "usage", never) == 0


def test_a_url_is_read_whole(tmp_path: Path) -> None:
    file = tmp_path / "served.gz"
    file.write_bytes(SERVED[url_of("text")])
    assert publicobjects.read_url(file.as_uri()) == SERVED[url_of("text")]
    assert archive.archive_asset_url(key_of("text")[0], f"{key_of('text')}.gz") == url_of("text")


def test_the_script_is_the_command() -> None:
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)  # noqa: S603 - our own script
    assert done.returncode == 2
    assert done.stdout.startswith("Fetch the stored objects a public release mirrors")
    assert "Usage: python scripts/fetch_public_objects.py <checkout> <objects>" in done.stdout
