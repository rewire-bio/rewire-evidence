"""Hold P14 archive network modules and the 2026-11-17 licence resolver to the TypeScript.

Verifies:
- SHA-256 digests of the 11 P14 TypeScript modules in `<root>`.
- Exact count and SHA-256 of `licenceresolve.read_targets(<root>)` against the TS recording
  over every source record (`1,497` at `STORE_REF`).
- Recorded fixtures and offline end-to-end scenarios in `tests/fixtures/archive-network.json`.
- Committed `2026-11-17` candidate, receipt, TSV, and Markdown files by SHA-256, and when
  `--resolve-root <QUEUE_REF>` is supplied, zero-network byte-for-byte regeneration of all 4
  committed `2026-11-17` outputs.
"""

from __future__ import annotations

import base64
import json
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, cast

from rewire_evidence import (
    archive,
    archivefetch,
    archivepublish,
    conformance,
    layerstore,
    licence,
    licenceresolve,
    r2,
)
from rewire_evidence.canonical import JsonObject, JsonValue, canonical_line, js_stringify, js_utf8, sha256_hex
from rewire_evidence.checkparity import Lines
from rewire_evidence.jsonl import StoreError

_CONTENT_VERSION_RE: Final = re.compile(r"content-version=([^\]]+)")
_GITHUB_FILE_RE: Final = re.compile(r"\(from (.+)\)$")
_FIXED_CLOCK_ISO: Final = "2026-11-21T12:34:56Z"
_HTTP_NOT_FOUND: Final = 404
_AUDIT_2026_11_17: Final = "data/omics/audits/2026-11-17-licence-resolve.receipts.jsonl"
_TSV_2026_11_17: Final = "maintenance/reports/licence-resolve-2026-11-17.tsv"
_MD_2026_11_17: Final = "maintenance/reports/licence-resolve-2026-11-17.md"
_PUB_GZ_FIXTURE: Final[dict[str, tuple[int, str, str, str]]] = {
    "d9a94fd3393c02bf8fbc898923fdca5d36828a07195a7bf7b5e699d68f4d7f1d": (
        18,
        "t0-public",
        "local-cache",
        "H4sIAAAAAAACE8vJT07MUSgoTcrJTFZIqixJLQYA9m3DFhIAAAA=",
    ),
    "6e0f019328e082ece26d071e7f169a7a4a3ff5d1161412feade060b81e47155f": (
        15,
        "t1-public-nc",
        "r2",
        "H4sIAAAAAAACEysyUigoTcrJTFZIqixJLQYAgyZYQQ8AAAA=",
    ),
    "1d4fec327a6cac6f1521d1b5321f55136d0472499d8482c4115eeacffaff140f": (
        13,
        "t4-private",
        "local-cache",
        "H4sIAAAAAAACEysoyixLLElVSKosSS0GACWgpGcNAAAA",
    ),
}


class _CheckFn(Protocol):
    def __call__(self, message: str, *, passed: bool) -> None: ...


@dataclass
class ArchiveNetworkReport:
    """Measurements collected by `verify_archive_network`."""

    store_ref: str = ""
    targets_expected: int = 0
    targets_same: int = 0
    fixtures_checked: int = 0
    fixtures_same: int = 0
    modules: Lines = field(default_factory=Lines)
    committed_2026_11_17: Lines = field(default_factory=Lines)
    reproduced_2026_11_17: Lines = field(default_factory=Lines)
    problems: list[str] = field(default_factory=list)


def _check_file_list(
    root: Path, entries: Sequence[Mapping[str, JsonValue]], counter: Lines, problems: list[str]
) -> None:
    for entry in entries:
        counter.expected += 1
        rel = cast("str", entry["file"])
        path = root / rel
        if path.is_file() and sha256_hex(path.read_bytes()) == entry["sha256"]:
            counter.same += 1
        else:
            problems.append(f"{rel} does not match recorded SHA-256 {entry['sha256']}")
    counter.made = counter.expected


def _write_cached_reply(cache_dir: Path, url: str, status: int, body_obj: object, fetched_at: str) -> None:
    path = cache_dir / f"{sha256_hex(url)}.json"
    path.write_text(
        json.dumps({"status": status, "body": json.dumps(body_obj), "fetched_at": fetched_at}),
        encoding="utf-8",
    )


def _seed_preprint_entry(
    preprint_eps: dict[str, tuple[str, list[dict[str, str]]]],
    pk: Mapping[str, JsonValue],
    rec: Mapping[str, JsonValue],
    obs: Sequence[JsonObject],
) -> None:
    doi = cast("str", pk["doi"])
    server = cast("str | None", pk.get("server"))
    endpoint = cast("str", rec["endpoint"])
    reason = cast("str | None", rec["reason"])
    attempted_at = cast("str", rec["attempted_at"])
    if reason == "not_found":
        if server is None:
            for srv in ("biorxiv", "medrxiv"):
                preprint_eps.setdefault(f"https://api.biorxiv.org/details/{srv}/{doi}", (attempted_at, []))
        else:
            preprint_eps.setdefault(endpoint, (attempted_at, []))
        return
    if server is None and "/medrxiv/" in endpoint:
        preprint_eps.setdefault(f"https://api.biorxiv.org/details/biorxiv/{doi}", (attempted_at, []))
    version = cast("str | None", pk.get("version")) or "1"
    held = preprint_eps.setdefault(endpoint, (attempted_at, []))[1]
    for item in obs:
        row = {"version": version, "license": cast("str", item["value"])}
        if row not in held:
            held.append(row)


def _seed_crossref(cache_dir: Path, endpoint: str, code: int, at: str, obs: Sequence[JsonObject]) -> None:
    if code == _HTTP_NOT_FOUND:
        _write_cached_reply(cache_dir, endpoint, _HTTP_NOT_FOUND, {}, at)
    elif not obs:
        _write_cached_reply(cache_dir, endpoint, 200, {"message": {"license": []}}, at)
    else:
        entries = []
        for item in obs:
            match = _CONTENT_VERSION_RE.search(cast("str", item["field"]))
            entries.append({"content-version": match.group(1) if match else "vor", "URL": item["value"]})
        _write_cached_reply(cache_dir, endpoint, 200, {"message": {"license": entries}}, at)


def _seed_github(cache_dir: Path, rec: Mapping[str, JsonValue], code: int, obs: Sequence[JsonObject]) -> None:
    endpoint = cast("str", rec["endpoint"])
    at = cast("str", rec["attempted_at"])
    if code == _HTTP_NOT_FOUND:
        _write_cached_reply(cache_dir, endpoint, _HTTP_NOT_FOUND, {}, at)
    elif rec["reason"] == "not_asserted":
        not_asserted_body: JsonObject = {"license": {"spdx_id": "NOASSERTION"}, "name": "LICENSE"}
        _write_cached_reply(cache_dir, endpoint, 200, not_asserted_body, at)
    else:
        first = obs[0]
        match = _GITHUB_FILE_RE.search(cast("str", first["field"]))
        fname = match.group(1) if match else "LICENSE"
        name_val: JsonValue = None if fname == "the repository licence file" else fname
        ok_body: JsonObject = {"license": {"spdx_id": first["value"]}, "name": name_val}
        _write_cached_reply(cache_dir, endpoint, 200, ok_body, at)


def _seed_huggingface(
    cache_dir: Path, rec: Mapping[str, JsonValue], code: int, obs: Sequence[JsonObject]
) -> None:
    endpoint = cast("str", rec["endpoint"])
    at = cast("str", rec["attempted_at"])
    reason = cast("str | None", rec["reason"])
    if code in {401, _HTTP_NOT_FOUND}:
        _write_cached_reply(cache_dir, endpoint, code, {}, at)
    elif reason == "no_licence_in_metadata":
        _write_cached_reply(cache_dir, endpoint, 200, {"cardData": {}}, at)
    elif reason == "not_asserted":
        _write_cached_reply(cache_dir, endpoint, 200, {"cardData": {"license": "other"}}, at)
    else:
        vals = [item["value"] for item in obs]
        card_lic: JsonValue = vals[0] if len(vals) == 1 else vals
        _write_cached_reply(cache_dir, endpoint, 200, {"cardData": {"license": card_lic}}, at)


def _load_observations_index(store_root: Path) -> dict[tuple[str, str], list[JsonObject]]:
    obs_by_key: dict[tuple[str, str], list[JsonObject]] = {}
    for line in (store_root / licence.CANDIDATES).read_text("utf-8").splitlines():
        if not line.strip():
            continue
        cand = cast("JsonObject", json.loads(line))
        sid = cast("str", cand["source_id"])
        for entry in cast("list[JsonObject]", cand["observations"]):
            obs_by_key.setdefault((sid, cast("str", entry["route"])), []).append(entry)
    return obs_by_key


def _seed_europepmc_entry(
    epmc_batches: dict[str, tuple[str, dict[str, dict[str, str]]]],
    keys_obj: Mapping[str, JsonValue],
    rec: Mapping[str, JsonValue],
    obs: Sequence[Mapping[str, JsonValue]],
) -> None:
    ep = cast("str", rec["endpoint"])
    at = cast("str", rec["attempted_at"])
    reason = cast("str | None", rec["reason"])
    pmcid = cast("str", keys_obj["europepmc"])
    batch = epmc_batches.setdefault(ep, (at, {}))[1]
    if reason == "no_licence_in_metadata":
        batch[pmcid] = {"pmcid": pmcid}
    elif reason is None:
        batch[pmcid] = {"pmcid": pmcid, "license": cast("str", obs[0]["value"])}


def _synthesize_resolve_cache(store_root: Path, resolve_root: Path, cache_dir: Path) -> None:
    """Populate `Client` `cacheDir` purely from committed 2026-11-17 candidates and receipts."""
    obs_by_key = _load_observations_index(store_root)
    targets_by_id = {cast("str", t["source_id"]): t for t in licenceresolve.read_targets(resolve_root)}
    epmc_batches: dict[str, tuple[str, dict[str, dict[str, str]]]] = {}
    preprint_eps: dict[str, tuple[str, list[dict[str, str]]]] = {}

    for line in (store_root / _AUDIT_2026_11_17).read_text("utf-8").splitlines():
        if not line.strip():
            continue
        rec = cast("JsonObject", json.loads(line))
        sid = cast("str", rec["source_id"])
        route = cast("str", rec["route"])
        ep = cast("str", rec["endpoint"])
        raw_status = rec["http_status"]
        code = 200 if raw_status is None else cast("int", raw_status)
        at = cast("str", rec["attempted_at"])
        obs = obs_by_key.get((sid, route), [])
        keys_obj = cast("JsonObject", targets_by_id[sid]["keys"])
        if route == "europepmc":
            _seed_europepmc_entry(epmc_batches, keys_obj, rec, obs)
        elif route == "preprint":
            _seed_preprint_entry(
                preprint_eps, cast("Mapping[str, JsonValue]", keys_obj["preprint"]), rec, obs
            )
        elif route == "crossref":
            _seed_crossref(cache_dir, ep, code, at, obs)
        elif route == "github":
            _seed_github(cache_dir, rec, code, obs)
        else:
            _seed_huggingface(cache_dir, rec, code, obs)

    for ep, (at, items) in preprint_eps.items():
        _write_cached_reply(cache_dir, ep, 200, {"collection": items}, at)
    for ep, (at, pmcid_map) in epmc_batches.items():
        _write_cached_reply(cache_dir, ep, 200, {"resultList": {"result": list(pmcid_map.values())}}, at)


def _verify_2026_11_17_regeneration(
    store_root: Path, resolve_root: Path, report: ArchiveNetworkReport
) -> None:
    """Reproduce all four 2026-11-17 licence resolution files byte-for-byte with zero network."""

    def fail_transport(url: str, headers: Mapping[str, str]) -> licenceresolve.HttpResponse:
        _ = headers
        raise StoreError(f"Unexpected network call during offline 2026-11-17 verification: {url}")

    with tempfile.TemporaryDirectory(prefix="p14-cache-") as tmp:
        cache_dir = Path(tmp) / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _synthesize_resolve_cache(store_root, resolve_root, cache_dir)
        res = licenceresolve.run_resolve(
            resolve_root,
            licenceresolve.ResolveOptions(
                date="2026-11-17",
                cache_dir=cache_dir,
                transport=fail_transport,
                sleep_ms=lambda _ms: None,
            ),
        )
    held = licence.read_licence_map(resolve_root)
    counts = licenceresolve.count_run(
        res, held, proposed_keys=licenceresolve.HISTORICAL_2026_11_17_PROPOSED_KEYS
    )
    generated = {
        licence.CANDIDATES: "".join(f"{canonical_line(c)}\n" for c in res.candidates),
        _AUDIT_2026_11_17: "".join(f"{canonical_line(r)}\n" for r in res.receipts),
        _TSV_2026_11_17: licenceresolve.render_candidate_table(res),
        _MD_2026_11_17: licenceresolve.render_report(res, counts),
    }
    for rel, actual in generated.items():
        report.reproduced_2026_11_17.expected += 1
        committed = (store_root / rel).read_text("utf-8")
        if actual == committed:
            report.reproduced_2026_11_17.same += 1
        else:
            report.problems.append(f"regenerated 2026-11-17 {rel} differs from committed store bytes")
    report.reproduced_2026_11_17.made = report.reproduced_2026_11_17.expected


def _check_unit_fixtures(fixture: JsonObject, check: _CheckFn) -> None:
    for raw_url, expected_url in cast("list[list[str]]", fixture["canonical_github_urls"]):
        check(
            f"canonical_github_url mismatch for {raw_url}",
            passed=archivefetch.canonical_github_url(raw_url) == expected_url,
        )
    for item in cast("list[JsonObject]", fixture["artifact_checks"]):
        verdict = archivefetch.check_artifact(
            base64.b64decode(cast("str", item["body_b64"])),
            media_type=cast("str | None", item["media_type"]),
            final_url=cast("str", item["final_url"]),
            requested_url=cast("str | None", item["requested_url"]),
            has_doi=cast("bool", item["has_doi"]),
            pdftotext=cast("bool", item["pdftotext"]),
        )
        got = {
            "terminal": verdict.terminal,
            "reason": verdict.reason,
            "layer_class": verdict.layer_class,
            "characters": verdict.characters,
            "signals": verdict.signals,
        }
        check(
            f"artifact check mismatch on {item['name']}: {got} != {item['expected']}",
            passed=got == item["expected"],
        )
    for item in cast("list[JsonObject]", fixture["failure_reasons"]):
        actual_reason = archivefetch.failure_reason(
            cast("str | None", item["doi"]), cast("list[JsonObject]", item["tried"])
        )
        check(
            f"failure_reason mismatch for doi={item['doi']}",
            passed=actual_reason == item["expected"],
        )
    for item in cast("list[JsonObject]", fixture["r2_signatures"]):
        raw_settings = cast("JsonObject", item["settings"])
        settings = r2.R2Settings(
            account_id=cast("str", raw_settings["accountId"]),
            access_key_id=cast("str", raw_settings["accessKeyId"]),
            secret_access_key=cast("str", raw_settings["secretAccessKey"]),
            bucket=cast("str", raw_settings["bucket"]),
        )
        signed = r2.sign_request(
            settings,
            cast("str", item["method"]),
            cast("str", item["key"]),
            cast("str", item["body"]).encode("utf-8"),
            datetime.fromisoformat(cast("str", item["timestamp"])),
        )
        check(
            f"R2 SigV4 mismatch on {item['method']} {item['key']}",
            passed=signed.url == item["url"] and signed.headers == item["headers"],
        )
    refusals = cast("JsonObject", fixture["publish_refusals"])
    check("archive_tag('a') mismatch", passed=archivepublish.archive_tag("a") == refusals["shard_tag_a"])
    check("shard_notes('a') mismatch", passed=archivepublish.shard_notes("a") == refusals["shard_notes_a"])
    for item in cast("list[JsonObject]", fixture["route_keys"]):
        got_keys = licenceresolve.route_keys_for(cast("str", item["url"]), cast("str | None", item["doi"]))
        check(
            f"route_keys mismatch for {item['url']}",
            passed=js_stringify(got_keys) == js_stringify(item["expected"]),
        )


def _manifest_sample(sha: str, size: int, tier: str, store: str, source_id: str) -> JsonObject:
    return {
        "sha256": sha,
        "bytes": size,
        "media_type": "text/plain",
        "layer": "exact-bytes",
        "origin": "https://example.org/item",
        "requested_url": None,
        "first_seen_at": _FIXED_CLOCK_ISO,
        "store": store,
        "mirrors": [],
        "visibility": archive.visibility_of(tier),
        "archive_tier": tier,
        "observed_licence": None,
        "exposure": "policy",
        "derived_from": None,
        "derivation": None,
        "previous_path": None,
        "source_ids": [source_id],
    }


def _check_store_layers_scenario(data: JsonObject, check: _CheckFn) -> None:
    with tempfile.TemporaryDirectory(prefix="p14-layers-") as tmp:
        root = Path(tmp)
        zip_bytes = base64.b64decode(cast("str", data["zip_b64"]))
        put = archive.put_object(zip_bytes, root)
        parent = archive.parse(
            archive.MANIFEST_ROW,
            {
                **_manifest_sample(
                    cast("str", put["key"]), len(zip_bytes), "t0-public", "local-cache", "src-zip"
                ),
                "media_type": "application/zip",
                "observed_licence": "MIT",
            },
        )
        archive.upsert_row(parent, root)
        res = layerstore.store_layers(parent, zip_bytes, root, pdftotext=False)
        got_stored = [
            {
                "sha256": s.sha256,
                "layer": s.layer,
                "derived_from": s.derived_from,
                "derivation": s.derivation,
                "bytes": s.bytes,
                "characters": s.characters,
                "written": s.written,
                "added": s.added,
            }
            for s in res.stored
        ]
        check("store_layers scenario mismatch on stored entries", passed=got_stored == data["stored"])


def _check_pull_scenario(pv: JsonObject, check: _CheckFn) -> None:
    with tempfile.TemporaryDirectory(prefix="p14-pull-") as tmp:
        root = Path(tmp)
        hello = base64.b64decode(cast("str", pv["hello_b64"]))
        gz = base64.b64decode(cast("str", pv["fixed_gzip_b64"]))
        r1 = _manifest_sample(cast("str", pv["hello_sha256"]), len(hello), "t4-private", "r2", "s1")
        missing_public = b"no remote copy"
        r2_row = _manifest_sample(
            sha256_hex(missing_public), len(missing_public), "t0-public", "local-cache", "s2"
        )
        removed_bytes = b"removed bytes"
        r3 = {
            **_manifest_sample(sha256_hex(removed_bytes), len(removed_bytes), "t5-no-store", "r2", "s3"),
            "removed_at": "2026-11-20T00:00:00Z",
            "removed_reason": "takedown",
            "removed_requester": "owner",
        }
        for row in (r1, r2_row, r3):
            archive.upsert_row(row, root)
        dry = r2.pull_objects(root, source="release", dry_run=True)
        check("pull release dry-run mismatch", passed=js_stringify(dry) == js_stringify(pv["release_dry"]))

        def r2_200(url: str, *, method: str, headers: Mapping[str, str], body: bytes = b"") -> r2.R2Reply:
            _ = url, method, headers, body
            return r2.R2Reply(200, gz)

        real = r2.pull_objects(
            root,
            env={
                "R2_ACCOUNT_ID": "acc",
                "R2_ACCESS_KEY_ID": "key",
                "R2_SECRET_ACCESS_KEY": "sec",
                "R2_BUCKET": "b",
            },
            fetch=r2_200,
        )
        on_disk = archive.object_path(cast("str", pv["hello_sha256"]), root).read_bytes()
        disk_b64 = base64.b64encode(on_disk).decode()
        check(
            "pull verbatim mismatch",
            passed=js_stringify(real) == js_stringify(pv["pull_real"]) and disk_b64 == pv["fixed_gzip_b64"],
        )


def _check_publish_scenario(pm: JsonObject, check: _CheckFn) -> None:
    with tempfile.TemporaryDirectory(prefix="p14-pub-") as tmp:
        root = Path(tmp)
        for idx, (key, (size, tier, store, b64)) in enumerate(_PUB_GZ_FIXTURE.items(), 1):
            dest = archive.object_path(key, root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(b64))
            archive.upsert_row(_manifest_sample(key, size, tier, store, f"p{idx}"), root)

        class _Client:
            def assets(self, _tag: str) -> list[str] | None:
                return None

            def create(self, _tag: str, _title: str, _notes: str) -> None:
                return None

            def upload(self, _tag: str, _file: Path) -> None:
                return None

        summary_obj = archivepublish.publish_objects(root, client=_Client())
        rows_after = {
            k: {"store": v["store"], "mirrors": v["mirrors"]} for k, v in archive.read_manifest(root).items()
        }
        check(
            "publish marking scenario mismatch",
            passed=(
                js_stringify(summary_obj) == js_stringify(pm["summary"])
                and js_stringify(rows_after) == js_stringify(pm["rows"])
            ),
        )


def _check_upload_scenario(ur: JsonObject, check: _CheckFn) -> None:
    with tempfile.TemporaryDirectory(prefix="p14-up-") as tmp:
        root = Path(tmp)
        repair_key = cast("str", archive.put_object(b"already in r2", root)["key"])
        send_key = cast("str", archive.put_object(b"needs upload", root)["key"])
        removed_bytes = b"removed bytes"
        removed_row = {
            **_manifest_sample(sha256_hex(removed_bytes), len(removed_bytes), "t5-no-store", "r2", "s3"),
            "removed_at": "2026-11-20T00:00:00Z",
            "removed_reason": "takedown",
            "removed_requester": "owner",
        }
        archive.upsert_row(_manifest_sample(repair_key, 13, "t0-public", "github-release", "u1"), root)
        archive.upsert_row(_manifest_sample(send_key, 12, "t0-public", "local-cache", "u2"), root)
        archive.upsert_row(removed_row, root)
        calls: list[str] = []

        def fake_r2(url: str, *, method: str, headers: Mapping[str, str], body: bytes = b"") -> r2.R2Reply:
            _ = headers, body
            name = url.rsplit("/", maxsplit=1)[-1]
            calls.append(f"{method} {name}")
            if method == "HEAD":
                return r2.R2Reply(200 if name == f"{repair_key}.gz" else _HTTP_NOT_FOUND)
            return r2.R2Reply(200)

        summary_obj = r2.upload_objects(
            root,
            env={
                "R2_ACCOUNT_ID": "acc",
                "R2_ACCESS_KEY_ID": "key",
                "R2_SECRET_ACCESS_KEY": "sec",
                "R2_BUCKET": "b",
            },
            fetch=fake_r2,
        )
        manifest_after = {
            k: {"store": v["store"], "mirrors": v["mirrors"]} for k, v in archive.read_manifest(root).items()
        }
        check(
            "upload repair scenario mismatch",
            passed=(
                js_stringify(summary_obj) == js_stringify(ur["summary"])
                and calls == ur["calls"]
                and js_stringify(manifest_after) == js_stringify(ur["manifest"])
            ),
        )


def _check_fetch_scenario(ff: JsonObject, check: _CheckFn) -> None:
    with tempfile.TemporaryDirectory(prefix="p14-fetch-") as tmp:
        root = Path(tmp)
        (root / "data/entities").mkdir(parents=True)
        (root / "data/archive").mkdir(parents=True)
        (root / "data/archive/licence-map.tsv").write_text(
            "licence\tfamily\ttier\tbasis\tnote\nCC BY 4.0\tcc-by\tt0-public\tstandard-cc\tOK\n", "utf-8"
        )
        (root / "data/entities/sources.jsonl").write_text(
            json.dumps(
                {
                    "id": "src-xml-rescue",
                    "kind": "source",
                    "name": "Nature article",
                    "description": "Test source.",
                    "status": "source_checked",
                    "facets": {"areas": ["genomics"], "contexts": ["research"]},
                    "source_ids": [],
                    "links": [],
                    "attributes": {
                        "url": "https://www.nature.com/articles/s41586",
                        "doi": "10.1038/s41586",
                        "licence": "CC BY 4.0",
                    },
                }
            )
            + "\n",
            "utf-8",
        )
        html_body = cast("str", ff["publisher_html"]).encode("utf-8")
        xml_body = cast("str", ff["jats_xml"]).encode("utf-8")

        def transport(url: str, *, headers: Mapping[str, str], timeout_ms: int) -> archivefetch.FetchResponse:
            _ = headers, timeout_ms
            if url.startswith("https://www.nature.com/"):
                return archivefetch.FetchResponse(200, url, {"content-type": "text/html"}, html_body)
            if url.startswith("https://api.unpaywall.org/"):
                return archivefetch.FetchResponse(200, url, {}, b'{"best_oa_location": null}')
            if "/search?" in url:
                return archivefetch.FetchResponse(
                    200, url, {}, b'{"resultList": {"result": [{"pmcid": "PMC1234567", "inEPMC": "Y"}]}}'
                )
            return archivefetch.FetchResponse(200, url, {"content-type": "application/xml"}, xml_body)

        summary_obj = archivefetch.run_fetch(
            root,
            archivefetch.FetchOptions(
                concurrency=1,
                delay_ms=0,
                pdftotext=False,
                classify=licence.licence_classifier(root),
                unpaywall_email="contact@example.org",
                transport=transport,
                github_token="",
                now_fn=lambda: _FIXED_CLOCK_ISO,
                clock_ms=lambda: 0,
                sleep_ms=lambda _ms: None,
            ),
        )
        receipts = [canonical_line(r) for r in archive.read_receipts(root)]
        expected_receipts = [canonical_line(r) for r in cast("list[JsonObject]", ff["receipts"])]
        check(
            "fetch fallback scenario mismatch",
            passed=(
                js_stringify(summary_obj) == js_stringify(ff["summary"]) and receipts == expected_receipts
            ),
        )


def _check_resolve_scenario(ro: JsonObject, check: _CheckFn) -> None:
    with tempfile.TemporaryDirectory(prefix="p14-resolve-") as tmp:
        root = Path(tmp)
        (root / "data/entities").mkdir(parents=True)
        (root / "data/archive").mkdir(parents=True)
        (root / "data/archive/licence-map.tsv").write_text(
            "licence\tfamily\ttier\tbasis\tnote\n"
            "CC BY 4.0\tcc-by\tt0-public\tstandard-cc\tOK\n"
            "MIT\tpermissive-code\tt0-public\tspdx\tOK\n",
            "utf-8",
        )
        (root / "data/entities/sources.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "id": "r-gh",
                            "kind": "source",
                            "name": "GH",
                            "description": "s",
                            "status": "source_checked",
                            "facets": {"areas": ["genomics"], "contexts": ["research"]},
                            "source_ids": [],
                            "links": [],
                            "attributes": {"url": "https://github.com/org/repo"},
                        }
                    ),
                    json.dumps(
                        {
                            "id": "r-unmapped",
                            "kind": "source",
                            "name": "HF",
                            "description": "s",
                            "status": "source_checked",
                            "facets": {"areas": ["genomics"], "contexts": ["research"]},
                            "source_ids": [],
                            "links": [],
                            "attributes": {"url": "https://huggingface.co/org/mod"},
                        }
                    ),
                ]
            )
            + "\n",
            "utf-8",
        )
        run = licenceresolve.run_resolve(
            root,
            licenceresolve.ResolveOptions(
                date="2026-11-21",
                delay_ms=0,
                host_delay_ms={},
                transport=lambda url, _h: (
                    licenceresolve.HttpResponse(
                        200, '{"license": {"spdx_id": "MIT"}, "name": "LICENSE"}', _FIXED_CLOCK_ISO
                    )
                    if "api.github.com" in url
                    else licenceresolve.HttpResponse(
                        200, '{"cardData": {"license": "apache-2.0"}}', _FIXED_CLOCK_ISO
                    )
                ),
                now_fn=lambda: _FIXED_CLOCK_ISO,
                sleep_ms=lambda _ms: None,
            ),
        )
        counts = licenceresolve.count_run(run, licence.read_licence_map(root))
        tsv = licenceresolve.render_candidate_table(run)
        md = licenceresolve.render_report(run, counts)
        check(
            "resolve offline scenario mismatch",
            passed=(
                js_stringify(run.targets) == js_stringify(ro["targets"])
                and js_stringify(run.candidates) == js_stringify(ro["candidates"])
                and js_stringify(run.receipts) == js_stringify(ro["receipts"])
                and js_stringify(counts) == js_stringify(ro["counts"])
                and tsv == ro["tsv"]
                and md == ro["md"]
            ),
        )


def verify_archive_network(
    root: Path,
    recorded: JsonObject,
    fixture: JsonObject | None = None,
    *,
    resolve_root: Path | None = None,
) -> ArchiveNetworkReport:
    """Verify P14 modules, target resolution across all sources, and 2026-11-17 regeneration."""
    report = ArchiveNetworkReport(store_ref=conformance.recorded_ref(recorded))
    _check_file_list(root, cast("list[JsonObject]", recorded["modules"]), report.modules, report.problems)
    _check_file_list(
        root,
        cast("list[JsonObject]", recorded["commit_2026_11_17"]),
        report.committed_2026_11_17,
        report.problems,
    )

    targets_meta = cast("JsonObject", recorded["targets"])
    report.targets_expected = cast("int", targets_meta["count"])
    targets = licenceresolve.read_targets(root)
    targets_bytes = js_utf8("".join(f"{js_stringify(t)}\n" for t in targets))
    if len(targets) == report.targets_expected and sha256_hex(targets_bytes) == targets_meta["sha256"]:
        report.targets_same = len(targets)
    else:
        report.problems.append(
            f"read_targets produced {len(targets)} targets with {sha256_hex(targets_bytes)}, "
            f"expected {report.targets_expected} with {targets_meta['sha256']}"
        )

    def check(message: str, *, passed: bool) -> None:
        report.fixtures_checked += 1
        if passed:
            report.fixtures_same += 1
        else:
            report.problems.append(message)

    if fixture is not None:
        _check_unit_fixtures(fixture, check)
        scenarios = cast("JsonObject", fixture["scenarios"])
        _check_store_layers_scenario(cast("JsonObject", scenarios["store_layers"]), check)
        _check_pull_scenario(cast("JsonObject", scenarios["pull_verbatim"]), check)
        _check_publish_scenario(cast("JsonObject", scenarios["publish_marking"]), check)
        _check_upload_scenario(cast("JsonObject", scenarios["upload_repair"]), check)
        _check_fetch_scenario(cast("JsonObject", scenarios["fetch_fallback"]), check)
        _check_resolve_scenario(cast("JsonObject", scenarios["resolve_offline"]), check)

    if resolve_root is not None:
        _verify_2026_11_17_regeneration(root, resolve_root, report)

    return report


def summary(report: ArchiveNetworkReport) -> str:
    """Human-readable summary matching the sibling gates."""
    lines = [
        f"the store at {report.store_ref}: {report.targets_same:,} of {report.targets_expected:,} targets",
        (
            f"  TypeScript modules that are the files recorded: "
            f"{report.modules.same} of {report.modules.expected}"
        ),
        (
            f"  committed 2026-11-17 files verified: "
            f"{report.committed_2026_11_17.same} of {report.committed_2026_11_17.expected}"
        ),
        f"  fixture checks agreed: {report.fixtures_same} of {report.fixtures_checked}",
    ]
    if report.reproduced_2026_11_17.expected:
        lines.append(
            f"  2026-11-17 files regenerated byte-for-byte offline: "
            f"{report.reproduced_2026_11_17.same} of {report.reproduced_2026_11_17.expected}"
        )
    return "\n".join(lines)


def load_fixture(path: Path) -> JsonObject:
    """Read a JSON object fixture file or raise `StoreError`."""
    try:
        loaded = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise StoreError(f"{path} cannot be read as a fixture: {error}") from error
    if not isinstance(loaded, dict):
        raise StoreError(f"{path} is not a JSON object")
    return cast("JsonObject", loaded)


def to_json(report: ArchiveNetworkReport) -> str:
    """Machine-readable report JSON for CI pin assertions."""
    return json.dumps(
        {
            "store_ref": report.store_ref,
            "targets_expected": report.targets_expected,
            "targets_same": report.targets_same,
            "fixtures_checked": report.fixtures_checked,
            "fixtures_same": report.fixtures_same,
            "modules": report.modules.row(),
            "committed_2026_11_17": report.committed_2026_11_17.row(),
            "reproduced_2026_11_17": report.reproduced_2026_11_17.row(),
            "problems": report.problems,
        },
        indent=1,
        ensure_ascii=False,
    )
