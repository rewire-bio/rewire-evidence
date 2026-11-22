"""The archive contract: the schemas, the small functions, gzip as Node reads it, and the
properties the manifest writes keep. Everything recorded is in `tests/fixtures/archive.json`.
"""

from __future__ import annotations

import gzip
import zlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import archive, licence, weburl, worklist, zod
from rewire_evidence.canonical import (
    JsonObject,
    canonical_line,
    js_parse,
    js_stringify,
    object_shard,
    sha256_hex,
)
from rewire_evidence.families import manifest_shard_path
from rewire_evidence.jsonl import StoreError
from tests.archived import ARCHIVE, CLOCK, ROOT, issues_of, stopped

SCHEMAS: dict[str, zod.Schema] = {
    "rows": archive.MANIFEST_ROW,
    "receipts": archive.FETCH_RECEIPT,
    "candidates": licence.CANDIDATE,
    "resolve_receipts": licence.RESOLVE_RECEIPT,
}


def test_the_recording_names_what_made_it_and_holds_what_the_port_plan_asks() -> None:
    made = ARCHIVE["recorded_from"]
    assert made["script"] == "scripts/fixtures/record_archive_fixture.mjs"
    assert made["cases"] == "scripts/fixtures/archive_cases.mjs"
    assert made["node"].startswith("v24.")
    assert {name: len(ARCHIVE[name]) for name in SCHEMAS} == {
        "rows": 611,
        "receipts": 358,
        "candidates": 243,
        "resolve_receipts": 170,
    }
    refused = {name: sum("issues" in case for case in ARCHIVE[name]) for name in SCHEMAS}
    assert refused == {"rows": 472, "receipts": 280, "candidates": 195, "resolve_receipts": 143}
    assert sum(len(scenario["steps"]) for scenario in ARCHIVE["scenarios"]) >= 300


def test_the_constants_are_the_typescripts() -> None:
    assert ARCHIVE["constants"] == {
        "layers": list(archive.LAYERS),
        "stores": list(archive.STORES),
        "visibilities": list(archive.VISIBILITIES),
        "archive_tiers": list(archive.ARCHIVE_TIERS),
        "exposures": list(archive.EXPOSURES),
        "routes": list(archive.ROUTES),
        "reasons": list(archive.REASONS),
        "outcomes": list(archive.OUTCOMES),
        "source_classes": list(archive.SOURCE_CLASSES),
        "default_archive_tier": archive.DEFAULT_ARCHIVE_TIER,
        "object_shards": list("0123456789abcdef"),
        "resolve_routes": list(licence.RESOLVE_ROUTES),
        "resolve_reasons": list(licence.RESOLVE_REASONS),
        "candidate_statuses": list(licence.CANDIDATE_STATUSES),
        "pre_policy_source_ids": list(licence.PRE_POLICY_SOURCE_IDS),
        "columns": list(licence.COLUMNS),
        "repository": archive.REPOSITORY,
    }


def schema_cases() -> list[Any]:
    return [
        pytest.param(name, case, id=f"{name}: {case['name']}") for name in SCHEMAS for case in ARCHIVE[name]
    ]


@pytest.mark.parametrize(("name", "case"), schema_cases())
def test_each_schema_reads_and_refuses_as_zod_does(name: str, case: dict[str, Any]) -> None:
    schema = SCHEMAS[name]
    value = js_parse(js_stringify(case["given"]))
    if "issues" in case:
        with pytest.raises(archive.Refused) as refused:
            archive.parse(schema, value)
        assert issues_of(refused.value) == case["issues"]
        assert str(refused.value) == case["issues"][0][2]
    else:
        # What the schema read, with its members in the order zod gives them.
        assert js_stringify(archive.parse(schema, value)) == js_stringify(case["parsed"])


def test_the_four_rules_of_a_home_and_its_mirrors_each_refuse_a_row() -> None:
    said = {case["name"]: case.get("issues") for case in ARCHIVE["rows"]}
    restricted = "tier t4-private is restricted, so a public release is neither its home nor a mirror of it"
    assert said["a restricted row whose home is a release"] == [["custom", [], restricted]]
    assert said["a restricted row mirrored to a release"] == [["custom", [], restricted]]
    # The rule is not applied to a removed row, whose store is a note of where the bytes were.
    assert said["a removed row whose home was a release"] is None
    assert said["a removed row with a release mirror"] == [
        ["custom", [], "a removed row keeps no copy, so it names no mirror, not github-release"]
    ]
    assert said["a removed row at t0-public"] == [
        ["custom", [], "a removed row sits at t5-no-store, not t0-public"]
    ]
    assert said["a removed row that names its home as a mirror"] == [
        ["custom", [], "r2 is this row's store, so it is not also a mirror of it"],
        ["custom", [], "a removed row keeps no copy, so it names no mirror, not r2"],
    ]


def test_the_small_functions_answer_as_the_typescripts_do() -> None:
    assert [[tier, archive.visibility_of(tier)] for tier in archive.ARCHIVE_TIERS] == ARCHIVE["visibilities"]
    assert [[a, b, licence.stricter_tier(a, b)] for a, b, _ in ARCHIVE["stricter"]] == ARCHIVE["stricter"]
    assert [[row, archive.is_removed(row)] for row, _ in ARCHIVE["removed"]] == ARCHIVE["removed"]
    typed = [[header, archive.media_type_of(header)] for header, _ in ARCHIVE["media_types"]]
    assert typed == ARCHIVE["media_types"]
    for text, line in ARCHIVE["canonical_lines"]:
        assert canonical_line(js_parse(text)) == line  # type: ignore[arg-type]
    for moment, said in ARCHIVE["clocks"]:
        assert archive.utc_now(datetime.fromisoformat(moment)) == said
    assert [[host, worklist.source_class_of(host)] for host, _ in ARCHIVE["source_classes"]] == ARCHIVE[
        "source_classes"
    ]
    assert archive.media_type_of(None) == "application/octet-stream"


def test_the_clock_is_read_in_utc_to_the_second() -> None:
    assert stopped() == "2026-11-21T12:34:56Z"
    assert archive.utc_now(CLOCK.astimezone(timezone(timedelta(hours=5)))) == "2026-11-21T12:34:56Z"
    now = archive.utc_now()
    assert abs(
        datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) - datetime.now(UTC)
    ) < timedelta(minutes=1)


def threw(case: dict[str, Any]) -> str | None:
    return case["error"]["message"] if "error" in case else None


@pytest.mark.parametrize(
    ("name", "call"),
    [
        ("object_shards", lambda case: archive.key_shard(case["given"])),
        ("shard_paths", lambda case: manifest_shard_path(case["given"], ROOT).as_posix()),
        ("receipt_paths", lambda case: archive.receipt_path(case["given"], ROOT).as_posix()),
        ("asset_urls", lambda case: archive.archive_asset_url(case["shard"], case["name"])),
    ],
)
def test_a_name_that_is_not_one_is_refused_in_the_typescripts_words(
    name: str, call: Callable[[dict[str, Any]], str]
) -> None:
    refused = 0
    for case in ARCHIVE[name]:
        if "error" in case:
            refused += 1
            with pytest.raises(StoreError) as error:
                call(case)
            assert str(error.value) == threw(case)
        else:
            assert call(case) == case["returned"]
    assert 0 < refused < len(ARCHIVE[name])


def test_a_key_names_its_shard() -> None:
    key = sha256_hex(b"an object")
    assert archive.key_shard(key) == object_shard(key) == key[0]
    assert archive.archive_asset_name(key) == f"{key}.gz"
    assert archive.archive_asset_url(key[0], f"{key}.gz").endswith(f"/archive/objects-{key[0]}/{key}.gz")


# ---------------------------------------------------------------------------------------
# gzip, as Node reads it.


@pytest.mark.parametrize("case", [pytest.param(case, id=case["name"]) for case in ARCHIVE["gunzips"]])
def test_a_gzip_file_expands_as_node_expands_it(case: dict[str, Any]) -> None:
    compressed = bytes.fromhex(case["hex"])
    if "error" in case:
        with pytest.raises(archive.ArchiveError) as error:
            archive.expand(compressed, case["digest"], "the file", case.get("bytes"))
        assert str(error.value) == threw(case)
    else:
        expanded = archive.expand(compressed, case["digest"], "the file", case.get("bytes"))
        assert expanded.hex() == case["returned"]


def test_python_reads_past_zero_bytes_where_node_stops() -> None:
    # The reason `gunzip` is written out: these two are the same file to Node and not to Python.
    one, two = gzip.compress(b"one "), gzip.compress(b"two")
    assert archive.gunzip(one + b"\x00" + two) == b"one "
    assert gzip.decompress(one + b"\x00" + two) == b"one two"
    assert archive.gunzip(one + b"\x00text") == b"one "
    with pytest.raises(gzip.BadGzipFile):
        gzip.decompress(one + b"\x00text")


@given(st.lists(st.binary(max_size=200), min_size=1, max_size=4), st.integers(0, 9), st.binary(max_size=20))
def test_members_expand_one_after_another_and_a_zero_byte_ends_them(
    parts: list[bytes], level: int, after: bytes
) -> None:
    members = b"".join(zlib.compress(part, level, wbits=31) for part in parts)
    whole = b"".join(parts)
    assert archive.gunzip(members) == whole
    assert archive.gunzip(members + b"\x00" + after) == whole
    assert archive.expand(members, sha256_hex(whole), "x", len(whole)) == whole
    assert archive.gunzip(members, len(whole)) == whole
    if whole:
        with pytest.raises(archive.ArchiveError):
            archive.gunzip(members, len(whole) - 1)
    with pytest.raises(archive.ArchiveError, match="x is not the listed gzip file"):
        archive.expand(members[:-1], sha256_hex(whole), "x")


# ---------------------------------------------------------------------------------------
# URLs.

#: Shapes the store's URLs have, which have to be read and not passed over as undecided.
DECIDED = [
    "https://example.org",
    "https://Example.ORG/Path?Q=1#F",
    "https://example.org:443/a",
    "https://example.org:8443/a",
    "http://example.org:80/",
    "https://user:pw@example.org/",
    "https://",
    "https://exa mple.org/",
    "https://127.1/",
    "https://0x7f.1/",
    "https://example.org:080/",
    "s3://Bucket/key",
    "doi:10.1/x",
    "mailto:a@example.org",
    "example.org",
    "",
]


def test_a_host_is_read_as_node_reads_it_or_not_at_all() -> None:
    undecided = []
    for url, recorded in ARCHIVE["urls"]:
        try:
            found = weburl.host_of(url)
        except weburl.UndecidedHost:
            undecided.append(url)
            continue
        assert (None if found is None else found._asdict()) == recorded, url
    assert not set(undecided) & set(DECIDED)
    assert set(DECIDED) <= {url for url, _ in ARCHIVE["urls"]}
    # What is passed over: a host only IDNA mapping can judge, and nothing else.
    assert len(undecided) == 12
    for url in undecided:
        assert not url.isascii() or "xn--" in url.lower()
        with pytest.raises(weburl.UndecidedHost, match="needs IDNA mapping"):
            weburl.host_of(url)


# ---------------------------------------------------------------------------------------
# Candidates.


def held_map() -> licence.LicenceMap:
    return {
        row[0]: dict(zip(("licence", "family", "archive_tier", "basis", "note"), row, strict=True))
        for row in ARCHIVE["built_map"]
    }


@pytest.mark.parametrize("case", [pytest.param(case, id=case["name"]) for case in ARCHIVE["built"]])
def test_a_candidate_is_built_as_the_typescript_builds_it(case: dict[str, Any]) -> None:
    if "error" in case:
        with pytest.raises(archive.Refused) as refused:
            licence.build_candidate(case["target"], case["observations"], held_map())
        assert issues_of(refused.value) == case["error"]["zod"]
    else:
        built = licence.build_candidate(case["target"], case["observations"], held_map())
        assert js_stringify(built) == js_stringify(case["returned"])


# ---------------------------------------------------------------------------------------
# Properties.

TIERS = st.sampled_from(archive.ARCHIVE_TIERS)


@given(TIERS, TIERS, TIERS)
def test_the_stricter_tier_is_a_maximum(a: str, b: str, c: str) -> None:
    stricter = licence.stricter_tier
    assert stricter(a, a) == a
    assert stricter(a, b) == stricter(b, a)
    assert stricter(stricter(a, b), c) == stricter(a, stricter(b, c))
    assert stricter(a, b) in {a, b}
    assert stricter(stricter(a, b), a) == stricter(a, b)
    # A tier that is not published is stricter than every tier that is.
    if archive.visibility_of(a) == "restricted" and archive.visibility_of(b) == "public":
        assert stricter(a, b) == a


@given(st.lists(st.tuples(TIERS, st.text(max_size=3)), max_size=6))
def test_the_strictest_reading_is_the_first_of_the_most_restrictive(readings: list[tuple[str, str]]) -> None:
    rows = [{"archive_tier": tier, "observed_licence": said} for tier, said in readings]
    tier, observed = licence.strictest(rows)
    if not readings:
        assert (tier, observed) == (archive.DEFAULT_ARCHIVE_TIER, None)
        return
    assert all(licence.stricter_tier(tier, held) == tier for held, _ in readings)
    assert (tier, observed) == next(reading for reading in readings if reading[0] == tier)


@st.composite
def rows(draw: st.DrawFn) -> dict[str, Any]:
    """A manifest row the schema accepts, removed or not."""
    tier = draw(TIERS)
    removed = tier == archive.NO_STORE and draw(st.booleans())
    restricted = archive.visibility_of(tier) == "restricted"
    stores = [store for store in archive.STORES if removed or not restricted or store != "github-release"]
    store = draw(st.sampled_from(stores))
    others = [] if removed else [held for held in stores if held != store]
    mirrors = sorted(draw(st.sets(st.sampled_from(others)))) if others else []
    derived = draw(st.booleans())
    row: dict[str, Any] = {
        "sha256": sha256_hex(draw(st.binary(max_size=8))),
        "bytes": draw(st.integers(0, 2**40)),
        "media_type": draw(st.sampled_from(["text/html", "application/pdf"])),
        "layer": "text-layer" if derived else "exact-bytes",
        "origin": "https://example.org/a",
        "requested_url": draw(st.none() | st.just("https://example.org/b")),
        "first_seen_at": "2026-11-17T16:10:11Z",
        "store": store,
        "mirrors": mirrors,
        "visibility": archive.visibility_of(tier),
        "archive_tier": tier,
        "observed_licence": draw(st.none() | st.just("CC BY 4.0")),
        "exposure": draw(st.sampled_from(archive.EXPOSURES)),
        "derived_from": sha256_hex(b"container") if derived else None,
        "derivation": "pdftotext -layout" if derived else None,
        "previous_path": None,
        "source_ids": draw(st.lists(st.sampled_from(["a", "b", "c"]), max_size=3)),
    }
    if removed:
        row |= {"removed_at": "2026-11-20T10:00:00Z", "removed_reason": "asked", "removed_requester": "x"}
    return row


@given(rows(), st.sampled_from(archive.STORES), st.sampled_from(archive.STORES))
def test_a_takedown_works_whatever_else_a_row_says(
    tmp_path_factory: pytest.TempPathFactory, row: dict[str, Any], home: str, mirror: str
) -> None:
    root = tmp_path_factory.mktemp("store")
    key = row["sha256"]
    assert archive.upsert_row(row, root) == {"added": True, "changed": True}
    assert archive.read_manifest(root) == {key: archive.parse(archive.MANIFEST_ROW, row)}
    # A second fetch of the same bytes changes nothing.
    assert archive.upsert_row(dict(row), root) == {
        "added": False,
        "changed": len(set(row["source_ids"])) != len(row["source_ids"]),
    }
    # A move the schema refuses leaves the manifest as it was.
    before = manifest_shard_path(key[0], root).read_bytes()
    for write, backend in ((archive.mark_home, home), (archive.mark_mirror, mirror)):
        try:
            wrote = write(key, backend, root)
        except archive.Refused:
            assert manifest_shard_path(key[0], root).read_bytes() == before
        else:
            assert wrote == (manifest_shard_path(key[0], root).read_bytes() != before)
            before = manifest_shard_path(key[0], root).read_bytes()
            assert write(key, backend, root) is False
    moved = archive.read_manifest(root)[key]
    mirrors = cast("list[str]", moved["mirrors"])
    assert mirrors == sorted(set(mirrors))
    assert moved["store"] not in mirrors

    assert archive.mark_removed(
        key, {"removed_reason": "asked", "removed_requester": "a publisher"}, root, stopped
    )
    taken = archive.read_manifest(root)[key]
    assert (taken["archive_tier"], taken["visibility"], taken["mirrors"]) == ("t5-no-store", "restricted", [])
    assert (taken["removed_at"], taken["removed_reason"]) == ("2026-11-21T12:34:56Z", "asked")
    kept = ("sha256", "bytes", "media_type", "origin", "first_seen_at", "store", "source_ids")
    assert all(taken[name] == moved[name] for name in kept)
    # Nothing a licence reading says lifts it out again.
    with pytest.raises(archive.Refused, match="a removed row sits at t5-no-store"):
        archive.update_row(key, {"archive_tier": "t0-public"}, root)
    assert archive.check_manifest(root) == {"rows": 1, "problems": []}
    assert archive.verify_objects(root)["removed"] == [key]


@given(st.lists(rows(), max_size=6, unique_by=lambda row: row["sha256"]))
def test_a_manifest_written_is_sorted_and_read_back_as_it_was_given(
    tmp_path_factory: pytest.TempPathFactory, given_rows: list[dict[str, Any]]
) -> None:
    root = tmp_path_factory.mktemp("store")
    for row in given_rows:
        archive.upsert_row(row, root)
    manifest = archive.read_manifest(root)
    assert manifest == {row["sha256"]: row for row in given_rows}
    assert archive.check_manifest(root) == {"rows": len(given_rows), "problems": []}
    for shard in "0123456789abcdef":
        keys = [str(row["sha256"]) for row in archive.read_shard(shard, root)]
        assert keys == sorted(keys)
        assert all(key[0] == shard for key in keys)
    assert not list(root.rglob("*.tmp"))


@given(st.lists(st.tuples(st.sampled_from(["a", "b"]), st.integers(0, 3)), max_size=8))
def test_the_latest_receipt_of_a_source_is_the_last_of_its_newest(held: list[tuple[str, int]]) -> None:
    receipts: list[JsonObject] = [
        {"source_id": source, "fetched_at": f"2026-11-17T10:00:0{second}Z", "line": at}
        for at, (source, second) in enumerate(held)
    ]
    latest = archive.latest_by_source(receipts)
    assert set(latest) == {source for source, _ in held}
    for source, receipt in latest.items():
        mine = [other for other in receipts if other["source_id"] == source]
        newest = max(str(other["fetched_at"]) for other in mine)
        assert receipt == [other for other in mine if other["fetched_at"] == newest][-1]


# ---------------------------------------------------------------------------------------
# Where this library says a thing in its own words.


def test_a_file_that_is_not_text_or_not_json_is_refused_by_name(tmp_path: Path) -> None:
    shard = manifest_shard_path("0", tmp_path)
    shard.parent.mkdir(parents=True)
    shard.write_bytes(b"\xff\xfe\n")
    with pytest.raises(StoreError, match=r"0\.jsonl is not UTF-8"):
        archive.read_shard("0", tmp_path)
    shard.write_bytes(b"not json at all\n")
    with pytest.raises(StoreError, match=r"data/archive/objects/0\.jsonl line 1 is not JSON"):
        archive.read_shard("0", tmp_path)
    # The structural check reports it and goes on, and the cache check goes no further.
    problems = archive.check_manifest(tmp_path)["problems"]
    assert isinstance(problems, list)
    assert [str(problem).split(" is not JSON")[0] for problem in problems] == [
        "data/archive/objects/0.jsonl line 1"
    ]
    with pytest.raises(StoreError, match="is not JSON"):
        archive.verify_objects(tmp_path)
    with pytest.raises(StoreError, match="cannot be read"):
        archive.raw_lines(tmp_path / "nowhere.jsonl")
