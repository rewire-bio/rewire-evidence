"""Tests for P14 archive network modules, licence resolver, CLI, and parity gate."""

from __future__ import annotations

import gzip
import http.client
import json
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Self, cast
from zipfile import ZipFile

import pytest

from rewire_evidence import (
    archive,
    archivefetch,
    archivenetparity,
    archivepublish,
    archivesaid,
    containers,
    layerstore,
    licence,
    licenceresolve,
    r2,
    weburl,
)
from rewire_evidence.canonical import JsonObject, canonical_line, js_stringify, js_utf8, sha256_hex
from rewire_evidence.cli import main as cli_main
from rewire_evidence.gates import archive_network_gate
from rewire_evidence.jsonl import StoreError, write_text

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_FILE = ROOT / "tests/fixtures/archive-network.json"
FIXTURE: dict[str, object] = json.loads(FIXTURE_FILE.read_text("utf-8"))
FIXED_ISO = "2026-11-21T12:34:56Z"
R2_ENV = {
    "R2_ACCOUNT_ID": "acc",
    "R2_ACCESS_KEY_ID": "key",
    "R2_SECRET_ACCESS_KEY": "sec",
    "R2_BUCKET": "bucket",
}


class _FakeHttpResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        url: str = "https://example.org/",
        headers: dict[str, str] | None = None,
        body: bytes = b"{}",
    ) -> None:
        self.status = status
        self._url = url
        self.headers = headers or {}
        self._body = body

    def geturl(self) -> str:
        return self._url

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _http_error(
    url: str, code: int, body: bytes, headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    message = http.client.HTTPMessage()
    for key, val in (headers or {}).items():
        message[key] = val
    return urllib.error.HTTPError(url, code, "error", message, BytesIO(body))


def _manifest_row(
    sha: str,
    size: int,
    *,
    tier: str = "t0-public",
    store: str = "local-cache",
    media_type: str = "text/plain",
) -> dict[str, object]:
    return {
        "sha256": sha,
        "bytes": size,
        "media_type": media_type,
        "layer": "exact-bytes",
        "origin": "https://example.org/file",
        "requested_url": None,
        "first_seen_at": FIXED_ISO,
        "store": store,
        "mirrors": [],
        "visibility": archive.visibility_of(tier),
        "archive_tier": tier,
        "observed_licence": None,
        "exposure": "policy",
        "derived_from": None,
        "derivation": None,
        "previous_path": None,
        "source_ids": ["s1"],
    }


def _removed_row(
    sha: str, size: int, *, tier: str = "t5-no-store", source_ids: list[str] | None = None
) -> dict[str, object]:
    return {
        **_manifest_row(sha, size, tier=tier, store="r2"),
        "source_ids": source_ids or ["s1"],
        "removed_at": "2026-11-20T00:00:00Z",
        "removed_reason": "takedown",
        "removed_requester": "owner",
    }


def _source_record(
    source_id: str,
    *,
    url: str = "https://example.org/data.txt",
    doi: str | None = None,
    recorded_licence: str | None = None,
) -> dict[str, object]:
    attrs: dict[str, object] = {"url": url}
    if doi is not None:
        attrs["doi"] = doi
    if recorded_licence is not None:
        attrs["licence"] = recorded_licence
    return {
        "id": source_id,
        "kind": "source",
        "name": source_id,
        "description": "Test source record.",
        "status": "source_checked",
        "facets": {"areas": ["genomics"], "contexts": ["research"]},
        "source_ids": [],
        "links": [],
        "attributes": attrs,
    }


def _seed_store(root: Path, sources: list[dict[str, object]]) -> None:
    write_text(
        root / licence.LICENCE_MAP,
        (
            "licence\tfamily\ttier\tbasis\tnote\n"
            "CC BY 4.0\tcc-by\tt0-public\tstandard-cc\tOK\n"
            "MIT\tpermissive-code\tt0-public\tspdx\tOK\n"
            "Proprietary\tproprietary\tt4-private\towner\tLocked\n"
        ),
    )
    write_text(root / "data/entities/sources.jsonl", "".join(f"{json.dumps(row)}\n" for row in sources))


def test_weburl_encode_and_archive_put_adopt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert weburl.encode_uri("https://example.org/a b?q=1#x") == "https://example.org/a%20b?q=1#x"
    assert weburl.encode_uri_component("10.1038/s41586 a!") == "10.1038%2Fs41586%20a!"

    put = archive.put_object(b"hello archive", tmp_path)
    assert put["written"] is True
    assert archive.put_object(b"hello archive", tmp_path)["written"] is False

    with pytest.raises(StoreError, match="cannot be read"):
        archive.adopt_file(tmp_path / "missing.bin", "a" * 64, tmp_path)

    monkeypatch.setattr(archive, "MAX_EXPANDED", 4)
    with pytest.raises(archive.ArchiveError, match="exceeds maximum expanded size"):
        archive.put_object(b"12345", tmp_path)
    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"12345")
    with pytest.raises(archive.ArchiveError, match="exceeds maximum expanded size"):
        archive.adopt_file(oversized, sha256_hex(b"12345"), tmp_path)
    monkeypatch.undo()

    wrong = tmp_path / "wrong.bin"
    wrong.write_bytes(b"actual bytes")
    with pytest.raises(archive.ArchiveError, match="differs from its listed SHA-256"):
        archive.adopt_file(wrong, sha256_hex(b"other bytes"), tmp_path)

    dup = tmp_path / "dup.bin"
    dup.write_bytes(b"hello archive")
    assert archive.adopt_file(dup, str(put["key"]), tmp_path)["written"] is False
    assert not dup.exists()

    fresh = tmp_path / "fresh.bin"
    fresh.write_bytes(b"fresh payload bytes")
    assert archive.adopt_file(fresh, sha256_hex(b"fresh payload bytes"), tmp_path)["written"] is True
    assert not fresh.exists()


def test_layerstore_identities_containers_and_unsupported(tmp_path: Path) -> None:
    plain = b"plain text layer"
    plain_key = str(archive.put_object(plain, tmp_path)["key"])
    plain_parent = _manifest_row(plain_key, len(plain), media_type="text/plain")
    archive.upsert_row(cast("JsonObject", plain_parent), tmp_path)
    plain_res = layerstore.store_layers(
        cast("JsonObject", plain_parent), plain, tmp_path, options=layerstore.StoreLayerOptions()
    )
    assert plain_res.identities == [plain_key]
    assert plain_res.stored == []

    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("page.html", "<html><body><p>Hello world from zip.</p></body></html>")
        zf.writestr("image.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        zf.writestr("notes.txt", "raw note text")
    zip_bytes = buf.getvalue()
    zip_key = str(archive.put_object(zip_bytes, tmp_path)["key"])
    zip_parent = {
        **_manifest_row(zip_key, len(zip_bytes), media_type="application/zip"),
        "source_ids": ["s2"],
    }
    archive.upsert_row(cast("JsonObject", zip_parent), tmp_path)
    zip_res = layerstore.store_layers(cast("JsonObject", zip_parent), zip_bytes, tmp_path, pdftotext=False)
    assert len(zip_res.stored) >= 2
    assert zip_res.unsupported
    assert zip_res.problems == []

    with ZipFile(buf2 := BytesIO(), "w") as zf:
        zf.writestr("big.txt", b"a" * 20)
    small_lim = layerstore.StoreLayerOptions(
        limits=containers.Limits(max_members=10, max_member_bytes=4, max_total_bytes=20),
        pdftotext=False,
    )
    bad_zip = buf2.getvalue()
    bad_key = str(archive.put_object(bad_zip, tmp_path)["key"])
    bad_parent = {**_manifest_row(bad_key, len(bad_zip), media_type="application/zip"), "source_ids": ["s2b"]}
    archive.upsert_row(cast("JsonObject", bad_parent), tmp_path)
    assert layerstore.store_layers(
        cast("JsonObject", bad_parent), bad_zip, tmp_path, options=small_lim
    ).problems

    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    png_key = str(archive.put_object(png, tmp_path)["key"])
    png_parent = {**_manifest_row(png_key, len(png), media_type="image/png"), "source_ids": ["s3"]}
    archive.upsert_row(cast("JsonObject", png_parent), tmp_path)
    png_res = layerstore.store_layers(cast("JsonObject", png_parent), png, tmp_path, pdftotext=False)
    assert png_res.stored == []
    assert png_res.unsupported


def test_r2_settings_signing_platform_and_storable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    with pytest.raises(archive.ArchiveError, match="R2_ACCOUNT_ID"):
        r2.read_settings({"R2_ACCOUNT_ID": "   "})
    settings = r2.read_settings(R2_ENV)
    assert "x-amz-date" in r2.sign_request(settings, "GET", "k.gz").headers
    offset_dt = datetime(2026, 11, 21, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    assert (
        r2.sign_request(settings, "PUT", "k.gz", b"abc", now=offset_dt).headers["x-amz-date"]
        == "20261121T120000Z"
    )

    with pytest.raises(archive.ArchiveError, match="Refusing non-HTTPS"):
        r2.platform_fetch("http://example.com/k.gz", method="GET", headers={})

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeHttpResponse:
        _ = timeout
        if req.get_method() == "PUT":
            return _FakeHttpResponse(status=204, body=b"")
        raise _http_error(req.full_url, 403, b"forbidden")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert r2.platform_fetch("https://example.com/k.gz", method="PUT", headers={}, body=b"x").status == 204
    assert r2.platform_fetch("https://example.com/k.gz", method="GET", headers={}).status == 403
    monkeypatch.undo()

    with pytest.raises(archive.ArchiveError, match="t5-no-store"):
        r2.assert_storable(cast("JsonObject", _manifest_row("a" * 64, 1, tier="t5-no-store")))
    with pytest.raises(archive.ArchiveError, match="removed at 2026-11-20"):
        r2.assert_storable(cast("JsonObject", _removed_row("a" * 64, 1, tier="t5-no-store")))
    with pytest.raises(archive.ArchiveError, match="removed at 2026-11-20"):
        r2.assert_storable(cast("JsonObject", _removed_row("a" * 64, 1, tier="t0-public")))


def test_r2_upload_objects_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    k_rem = sha256_hex(b"gone")
    k_uncached = sha256_hex(b"not cached")
    k_r2_already = str(archive.put_object(b"already marked r2", tmp_path)["key"])
    k_head_200 = str(archive.put_object(b"head 200 repair", tmp_path)["key"])
    k_put_ok = str(archive.put_object(b"put 200 ok", tmp_path)["key"])
    k_put_nomark = str(archive.put_object(b"put 200 nomark", tmp_path)["key"])
    k_put_fail = str(archive.put_object(b"put 500 fail", tmp_path)["key"])
    for idx, row in enumerate(
        (
            _manifest_row(k_rem, 4, tier="t5-no-store"),
            _manifest_row(k_uncached, 10, tier="t0-public"),
            _manifest_row(k_r2_already, 17, store="r2"),
            _manifest_row(k_head_200, 15, store="local-cache"),
            _manifest_row(k_put_ok, 10, store="local-cache"),
            _manifest_row(k_put_nomark, 14, store="local-cache"),
            _manifest_row(k_put_fail, 12, store="local-cache"),
        )
    ):
        archive.upsert_row(cast("JsonObject", {**row, "source_ids": [f"s{idx}"]}), tmp_path)

    dry = r2.upload_objects(tmp_path, dry_run=True, env=R2_ENV)
    assert (dry["would_upload"], dry["removed"]) == (5, 1)

    orig_mark = archive.mark_home
    monkeypatch.setattr(
        archive,
        "mark_home",
        lambda key, home, root=Path(): False if key == k_put_nomark else orig_mark(key, home, root),
    )

    def fake_upload(url: str, *, method: str, headers: Mapping[str, str], body: bytes = b"") -> r2.R2Reply:
        _ = headers, body
        name = url.rsplit("/", maxsplit=1)[-1]
        if method == "HEAD":
            return r2.R2Reply(200 if name in {f"{k_r2_already}.gz", f"{k_head_200}.gz"} else 404)
        return r2.R2Reply(500 if name == f"{k_put_fail}.gz" else 200)

    up = r2.upload_objects(tmp_path, env=R2_ENV, fetch=fake_upload)
    assert (up["uploaded"], up["skipped"], up["marked"], up["removed"]) == (2, 2, 2, 1)
    assert up["failed"] == [{"key": k_put_fail, "status": 500}]


def test_r2_pull_objects_branches(tmp_path: Path) -> None:
    with pytest.raises(archive.ArchiveError, match="--from takes"):
        r2.pull_objects(tmp_path, source="ftp")

    pull_root = tmp_path / "pull"
    p_held = str(archive.put_object(b"held already", pull_root)["key"])
    p_rel = sha256_hex(b"from release")
    p_r2_m = sha256_hex(b"mirror r2")
    p_none = sha256_hex(b"no remote")
    p_rem = sha256_hex(b"removed")
    p_priv = sha256_hex(b"restricted")
    for idx, row in enumerate(
        (
            _manifest_row(p_held, 12, store="r2"),
            {**_manifest_row(p_rel, 12, store="r2"), "mirrors": ["github-release"]},
            {**_manifest_row(p_r2_m, 9, store="local-cache"), "mirrors": ["r2"]},
            _manifest_row(p_none, 9),
            _removed_row(p_rem, 7),
            _manifest_row(p_priv, 10, tier="t4-private", store="r2"),
        )
    ):
        archive.upsert_row(cast("JsonObject", {**row, "source_ids": [f"a{idx}"]}), pull_root)

    logs: list[str] = []
    empty_pull = r2.pull_objects(pull_root, source="release", keys=[p_held], sources=["a0"], log=logs.append)
    assert empty_pull["downloaded"] == 0
    assert "nothing to fetch" in logs[-1]
    assert "already held" in archivesaid.say_pull(pull_root, source="release", dry_run=True).out
    assert r2.select_rows(archive.read_manifest(pull_root), keys=[p_held], sources=["nope"]) == []

    def rel_fetch(url: str, *, method: str, headers: Mapping[str, str], body: bytes = b"") -> r2.R2Reply:
        _ = url, method, headers, body
        return r2.R2Reply(200, gzip.compress(b"from release"))

    assert r2.pull_objects(pull_root, source="release", keys=[p_rel], fetch=rel_fetch)["downloaded"] == 1

    many_root = tmp_path / "many"
    replies: dict[str, r2.R2Reply] = {}
    for idx in range(51):
        data = f"item-{idx}".encode()
        key = sha256_hex(data)
        archive.upsert_row(
            cast("JsonObject", {**_manifest_row(key, len(data), store="r2"), "source_ids": [f"m{idx}"]}),
            many_root,
        )
        replies[f"{key}.gz"] = r2.R2Reply(200, gzip.compress(data))
    p_bad_gz, p_net_err, p_http_err = sha256_hex(b"bad gz"), sha256_hex(b"net er"), sha256_hex(b"http e")
    for k, rep in ((p_bad_gz, r2.R2Reply(200, b"corrupt")), (p_http_err, r2.R2Reply(502, b"x" * 350))):
        archive.upsert_row(
            cast("JsonObject", {**_manifest_row(k, 8, store="r2"), "source_ids": [k]}), many_root
        )
        replies[f"{k}.gz"] = rep
    archive.upsert_row(
        cast("JsonObject", {**_manifest_row(p_net_err, 6, store="r2"), "source_ids": [p_net_err]}), many_root
    )

    def pull_tx(url: str, *, method: str, headers: Mapping[str, str], body: bytes = b"") -> r2.R2Reply:
        _ = method, headers, body
        name = url.rsplit("/", maxsplit=1)[-1]
        if name == f"{p_net_err}.gz":
            raise OSError("connection reset")
        return replies[name]

    many_logs: list[str] = []
    r2_res = r2.pull_objects(many_root, source="r2", env=R2_ENV, fetch=pull_tx, log=many_logs.append)
    assert r2_res["downloaded"] == 51
    assert any("50 of 54 objects" in line for line in many_logs)
    assert len(cast("list[object]", r2_res["failed"])) == 3

    pull_txt = archivesaid.say_pull(many_root, source="r2", env=R2_ENV, fetch=pull_tx, quiet=True)
    assert pull_txt.status == 1
    assert "502" in pull_txt.out
    assert "no status" in pull_txt.out


def test_archivepublish_client_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(archive.ArchiveError, match="Not a manifest shard"):
        archivepublish.archive_tag("zz")

    monkeypatch.setenv("RELEASE_TARGET_SHA", "deadbeef")
    seen_args: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen_args.append(cmd)
        if cmd[1:3] == ["release", "view"]:
            tag = cmd[3]
            if tag == "archive/objects-0":
                raise subprocess.CalledProcessError(1, cmd, stderr="release not found")
            if tag == "archive/objects-1":
                raise subprocess.CalledProcessError(1, cmd, stderr="rate limit exceeded")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"assets": [{"name": "abc.gz"}]}')
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = archivepublish.gh_client("rewire-bio/rewire-benchmark-data")
    assert client.assets("archive/objects-0") is None
    with pytest.raises(archive.ArchiveError, match="gh release view archive/objects-1 failed"):
        client.assets("archive/objects-1")
    assert client.assets("archive/objects-2") == ["abc.gz"]
    client.create("archive/objects-0", "Title", "Notes")
    assert "--target" in seen_args[-1]
    client.upload("archive/objects-0", tmp_path / "f.gz")
    monkeypatch.delenv("RELEASE_TARGET_SHA", raising=False)
    client.create("archive/objects-0", "Title", "Notes")
    assert "--target" not in seen_args[-1]

    with pytest.raises(archive.ArchiveError, match="t5-no-store, removed at 2026-11-20"):
        archivepublish.assert_publishable(cast("JsonObject", _removed_row("a" * 64, 1)))
    with pytest.raises(archive.ArchiveError, match="only a public object"):
        archivepublish.assert_publishable(cast("JsonObject", _manifest_row("a" * 64, 1, tier="t4-private")))


def test_archivepublish_plan_and_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    k_uncached = sha256_hex(b"uncached public")
    k_priv = sha256_hex(b"private row")
    k_present = str(archive.put_object(b"already asset", tmp_path)["key"])
    k_fail = str(archive.put_object(b"upload fails", tmp_path)["key"])
    k_nomark = str(archive.put_object(b"uploaded nomark", tmp_path)["key"])
    for idx, row in enumerate(
        (
            _manifest_row(k_uncached, 15),
            _manifest_row(k_priv, 11, tier="t4-private"),
            _manifest_row(k_present, 13, store="github-release"),
            _manifest_row(k_fail, 12, store="local-cache"),
            _manifest_row(k_nomark, 15, store="local-cache"),
        )
    ):
        archive.upsert_row(cast("JsonObject", {**row, "source_ids": [f"p{idx}"]}), tmp_path)

    orig_stat = Path.stat
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self, *a, **k: (
            (_ for _ in ()).throw(OSError("gone"))
            if self.name == f"{k_nomark}.gz"
            else orig_stat(self, *a, **k)
        ),
    )
    limited = archivepublish.plan_publish(tmp_path, limit=3)
    assert (len(limited.candidates), limited.public_not_in_cache, limited.refused) == (3, 1, 1)
    monkeypatch.undo()

    orig_mark = archive.mark_home
    monkeypatch.setattr(
        archive,
        "mark_home",
        lambda key, home, root=Path(): False if key == k_nomark else orig_mark(key, home, root),
    )
    present_tag = archivepublish.archive_tag(archive.key_shard(k_present))
    existing_tag = archivepublish.archive_tag(archive.key_shard(k_nomark))
    fail_name = archive.archive_asset_name(k_fail)

    class _StubClient:
        def assets(self, tag: str) -> list[str] | None:
            if tag == present_tag:
                return [archive.archive_asset_name(k_present)]
            if tag == existing_tag:
                return []
            return None

        def create(self, _tag: str, _title: str, _notes: str) -> None:
            return None

        def upload(self, _tag: str, file: Path) -> None:
            if file.name == fail_name:
                raise archive.ArchiveError("asset upload error")

    assert archivepublish.publish_objects(tmp_path, dry_run=True, client=_StubClient())["dry_run"] is True
    pub_logs: list[str] = []
    real = archivepublish.publish_objects(tmp_path, mark=False, client=_StubClient(), log=pub_logs.append)
    assert (real["marked"], real["already_present"]) == (0, 1)
    assert real["failed"] == [{"key": k_fail, "message": "asset upload error"}]

    assert archivesaid.say_publish(tmp_path, dry_run=True, client=_StubClient(), quiet=True).status == 0
    pub_fail = archivesaid.say_publish(tmp_path, client=_StubClient(), quiet=True)
    assert pub_fail.status == 1
    assert "failed                1" in pub_fail.out
    pub_json = archivesaid.say_publish(tmp_path, dry_run=True, as_json=True, quiet=True, client=_StubClient())
    assert json.loads(pub_json.out)["dry_run"] is True


def test_archivefetch_artifacts_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        archivefetch.check_artifact(b"PK\x03\x04", final_url="bad", media_type="application/zip").layer_class
        == "container"
    )
    long_body = "x" * 2500
    assert (
        archivefetch.check_artifact(
            f"<html><body><p>we use cookies</p><p>{long_body}</p></body></html>".encode(),
            final_url="not a url",
            media_type="text/html",
            has_doi=True,
        ).terminal
        is True
    )
    stop_hit = archivefetch.check_artifact(
        b"<html><body><p>Hello full text " + b"x" * 1200 + b"</p></body></html>",
        final_url="https://example.org/Sign-In/item",
        media_type="text/html",
    )
    assert (stop_hit.reason, "stop-path" in stop_hit.signals) == ("interstitial_body", True)
    weak_hit = archivefetch.check_artifact(
        b"<html><body><p>Verify you are human</p></body></html>",
        final_url="https://example.org/gate",
        media_type="text/html",
    )
    assert "human-verification" in weak_hit.signals
    assert (
        archivefetch.check_artifact(
            b"<html><body><p>" + b"w" * 1200 + b"</p></body></html>",
            final_url="https://example.org/nonpub",
            requested_url="https://www.nature.com/articles/x",
            media_type="text/html",
            has_doi=True,
        ).reason
        == "publisher_html_only"
    )
    assert (
        archivefetch.check_artifact(b"hi", final_url="https://example.org/a", media_type="text/html").reason
        == "text_below_threshold"
    )
    assert (
        archivefetch.check_artifact(b"\x00\x01\x02\x03", final_url="https://example.org/bin").layer_class
        == "unknown"
    )
    assert archivefetch.canonical_github_url("bad url") == "bad url"
    assert (
        archivefetch.canonical_github_url("https://github.com/org/repo/issues/1")
        == "https://github.com/org/repo/issues/1"
    )
    assert (
        archivefetch.canonical_github_url("https://github.com/org/repo/blob/main/a.txt?raw=1")
        == "https://raw.githubusercontent.com/org/repo/main/a.txt?raw=1"
    )

    monkeypatch.setattr(
        "subprocess.run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout="cli-tok\n")
    )
    assert (
        archivefetch.request_headers("https://api.github.com/repos/a/b", "application/json", env={})[
            "Authorization"
        ]
        == "Bearer cli-tok"
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_a, **_k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["gh"])),
    )
    assert "Authorization" not in archivefetch.request_headers("https://github.com/a/b", "*/*", env={})
    assert "Authorization" not in archivefetch.request_headers("https://example.org/a", "*/*", env={})
    assert (
        archivefetch.request_headers("https://github.com/a/b", "*/*", env={"GITHUB_TOKEN": "env-tok"})[
            "Authorization"
        ]
        == "Bearer env-tok"
    )


def test_archivefetch_route_chain() -> None:
    routes = dict(archivefetch.ROUTE_CHAIN)

    def no_op(_u: str) -> archivefetch.JsonReply:
        return archivefetch.JsonReply(200, {})

    def unp(doi: str | None, fn: Callable[[str], archivefetch.JsonReply], email: str = "a@b.c") -> str | None:
        return routes["unpaywall"](
            archivefetch.RouteInput("https://x", doi, fn, unpaywall_email=email)
        ).reason

    assert unp(None, no_op) == "no_doi"
    assert unp("10.1/a", no_op, "") == "unpaywall_no_email"
    assert unp("10.1/a", lambda _: archivefetch.JsonReply(0, None, "net")) == "net"
    assert unp("10.1/a", lambda _: archivefetch.JsonReply(404, None)) == "not_found"
    assert unp("10.1/a", lambda _: archivefetch.JsonReply(500, None)) == "http_error"

    def epmc(doi: str | None, fn: Callable[[str], archivefetch.JsonReply]) -> str | None:
        return routes["europepmc"](archivefetch.RouteInput("https://x", doi, fn)).reason

    assert epmc(None, no_op) == "no_doi"
    assert epmc("10.1/a", lambda _: archivefetch.JsonReply(0, None, "timeout")) == "timeout"
    assert epmc("10.1/a", lambda _: archivefetch.JsonReply(404, None)) == "not_found"
    assert epmc("10.1/a", lambda _: archivefetch.JsonReply(500, None)) == "http_error"
    assert (
        epmc("10.1/a", lambda _: archivefetch.JsonReply(200, {"resultList": {"result": []}})) == "not_found"
    )
    assert (
        epmc(
            "10.1/a",
            lambda _: archivefetch.JsonReply(
                200, {"resultList": {"result": [{"pmcid": "PMC1", "inEPMC": "N"}]}}
            ),
        )
        == "no_open_copy"
    )

    assert routes["preprint"](archivefetch.RouteInput("https://x", None, no_op)).reason == "no_doi"
    assert (
        routes["preprint"](
            archivefetch.RouteInput(
                "https://x", "10.1/a", lambda _: archivefetch.JsonReply(0, None, "network_error")
            )
        ).reason
        == "network_error"
    )
    med = routes["preprint"](
        archivefetch.RouteInput(
            "https://x",
            "10.1/a",
            lambda u: (
                archivefetch.JsonReply(500, None)
                if "/biorxiv/" in u
                else archivefetch.JsonReply(200, {"collection": [{"doi": "10.1/a"}]})
            ),
        )
    )
    assert med.url == "https://www.medrxiv.org/content/10.1/av1.full.pdf"
    med2 = routes["preprint"](
        archivefetch.RouteInput(
            "https://x",
            "10.1/a",
            lambda u: (
                archivefetch.JsonReply(200, {"collection": []})
                if "/biorxiv/" in u
                else archivefetch.JsonReply(200, {"collection": [{"doi": "10.1/a"}]})
            ),
        )
    )
    assert med2.url == "https://www.medrxiv.org/content/10.1/av1.full.pdf"
    assert (
        routes["preprint"](
            archivefetch.RouteInput("https://x", "10.1/a", lambda _: archivefetch.JsonReply(404, None))
        ).reason
        == "no_open_copy"
    )


def test_archivefetch_platform_and_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks_resp = archivefetch.FetchResponse(200, "https://example.org", {"X-Test": "1"}, chunks=[b"a", b"b"])
    assert (chunks_resp.header("x-test"), chunks_resp.header("missing")) == ("1", None)
    assert list(chunks_resp.iter_chunks()) == [b"a", b"b"]
    assert list(archivefetch.FetchResponse(200, "https://example.org").iter_chunks()) == []

    with pytest.raises(archive.ArchiveError, match="Unsupported URL scheme"):
        archivefetch.platform_fetch("ftp://example.org/a", headers={}, timeout_ms=1000)

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeHttpResponse:
        _ = timeout
        if "ok" in req.full_url:
            return _FakeHttpResponse(
                status=200, url=req.full_url, headers={"Content-Type": "text/plain"}, body=b"ok"
            )
        raise _http_error(req.full_url, 404, b"missing", {"Content-Type": "text/plain"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert archivefetch.platform_fetch("https://example.org/ok", headers={}, timeout_ms=500).body == b"ok"
    assert archivefetch.platform_fetch("https://example.org/err", headers={}, timeout_ms=500).status == 404
    monkeypatch.setattr("time.sleep", lambda _s: None)

    assert archivefetch.retry_after_ms(None) is None
    assert archivefetch.retry_after_ms("   ") == 0
    assert archivefetch.retry_after_ms("10", now_ms=0) == 10_000
    assert archivefetch.retry_after_ms("-5") is None
    assert archivefetch.retry_after_ms("not-a-date") is None
    assert archivefetch.retry_after_ms("Thu, 01 Jan 1970 00:00:05 GMT", now_ms=0) == 5_000
    assert archivefetch.retry_after_ms("Thu, 01 Jan 1970 00:00:02", now_ms=1_000) == 1_000
    assert archivefetch.FetchOptions().clock_ms() > 0
    archivefetch.FetchOptions().sleep_ms(1)


def _broken_chunks() -> Iterator[bytes]:
    yield b"a"
    raise OSError("chunk fail")


def _error_transport(url: str, *, headers: Mapping[str, str], timeout_ms: int) -> archivefetch.FetchResponse:
    _ = headers, timeout_ms
    routes_map: dict[str, archivefetch.FetchResponse] = {
        "429": archivefetch.FetchResponse(429, url, {}),
        "badjson": archivefetch.FetchResponse(200, url, {}, b"{bad"),
        "500": archivefetch.FetchResponse(500, url, {}),
        "big": archivefetch.FetchResponse(200, url, {}, b"0123456"),
        "empty": archivefetch.FetchResponse(200, url, {}, b""),
    }
    if "timeout" in url:
        raise TimeoutError
    if "net" in url:
        raise OSError("net")
    if "streamerr" in url:
        return archivefetch.FetchResponse(200, url, {}, chunks=_broken_chunks())
    for tag, reply in routes_map.items():
        if tag in url:
            return reply
    return archivefetch.FetchResponse(200, url, {}, b'{"ok": 1}')


def test_archivefetch_fetcher_errors(tmp_path: Path) -> None:
    ticks = [0]
    slept: list[int] = []

    def adv_sleep(ms: int) -> None:
        slept.append(ms)
        ticks[0] += ms

    fetcher = archivefetch.Fetcher(
        archivefetch.FetchConfig(
            root=tmp_path / "fc",
            delay_ms=100,
            max_bytes=5,
            host_backoff_limit=3,
            clock_ms=lambda: ticks[0],
            sleep_ms=adv_sleep,
            github_token="",
            transport=_error_transport,
        )
    )
    assert fetcher.get_json("not-a-url").transport == "unsupported_url"
    assert fetcher.get_json("https://timeout.example/a").transport == "timeout"
    assert fetcher.get_json("https://net.example/a").transport == "network_error"
    assert fetcher.get_json("https://429.example/a").transport == "rate_limited"
    assert slept
    assert fetcher.get_json("https://429.example/b").transport == "host_stopped"
    assert fetcher.get_json("https://429.example/c").transport == "host_stopped"
    assert fetcher.get_json("https://badjson.example/a").body is None
    assert fetcher.get_json("https://ok.example/a").body == {"ok": 1}
    assert fetcher.get_json("https://500.example/a").status == 500
    assert "timeout" in str(fetcher.download("https://timeout.example/a"))
    assert "http_error" in str(fetcher.download("https://500.example/a"))
    assert "too_large" in str(fetcher.download("https://big.example/a"))
    assert "empty_body" in str(fetcher.download("https://empty.example/a"))
    assert "network_error" in str(fetcher.download("https://streamerr.example/a"))


def _fetch_dry_tx(url: str, *, headers: Mapping[str, str], timeout_ms: int) -> archivefetch.FetchResponse:
    _ = headers, timeout_ms
    if "stop.example" in url or "stopunp" in url:
        return archivefetch.FetchResponse(503, url, {})
    if "pay.example" in url:
        return archivefetch.FetchResponse(200, url, {"content-type": "text/html"}, b"short")
    if "api.unpaywall.org" in url and "defer" in url:
        return archivefetch.FetchResponse(
            200, url, {}, b'{"best_oa_location": {"url": "https://stop.example/a"}}'
        )
    if "api.unpaywall.org" in url:
        return archivefetch.FetchResponse(
            200, url, {}, b'{"best_oa_location": {"url": "https://pay.example/3"}}'
        )
    if "grp.example" in url:
        return archivefetch.FetchResponse(200, url, {"content-type": "text/plain"}, b"held bytes")
    return archivefetch.FetchResponse(404, url, {})


def _fetch_na_tx(url: str, *, headers: Mapping[str, str], timeout_ms: int) -> archivefetch.FetchResponse:
    _ = headers, timeout_ms
    if "stop.example" in url:
        return archivefetch.FetchResponse(200, url, {"content-type": "text/html"}, b"short")
    if "pay.example/1" in url:
        return archivefetch.FetchResponse(
            200, url, {"content-type": "text/html"}, b"<html><p>tiny</p></html>"
        )
    if "api.unpaywall.org" in url and "defer" in url:
        return archivefetch.FetchResponse(
            200, url, {}, b'{"best_oa_location": {"url": "https://www.nature.com/articles/s1"}}'
        )
    if "www.nature.com" in url:
        return archivefetch.FetchResponse(
            200, url, {"content-type": "text/html"}, b"<html><p>tiny</p></html>"
        )
    if "www.ebi.ac.uk" in url and "fullTextXML" in url:
        return archivefetch.FetchResponse(
            200, url, {"content-type": "application/xml"}, b"<article>" + b"w" * 1100 + b"</article>"
        )
    if "www.ebi.ac.uk" in url and "defer" in url:
        return archivefetch.FetchResponse(
            200, url, {}, b'{"resultList": {"result": [{"pmcid": "PMC1", "inEPMC": "Y"}]}}'
        )
    return archivefetch.FetchResponse(404, url, {})


def test_archivefetch_run_fetch_dry_and_write(tmp_path: Path) -> None:
    run_root = tmp_path / "run_fetch"
    sources = [
        _source_record("src-removed", url="https://rem.example/1"),
        _source_record("src-held", url="https://held.example/1"),
        _source_record("src-na", url="https://na.example/1"),
        _source_record("src-fail", url="https://fail.example/1"),
        _source_record("src-dry-fail", url="https://always404.example/1"),
        _source_record("src-defer", url="https://stop.example/1", doi="10.1/defer"),
        _source_record("src-defer2", url="https://pay.example/2", doi="10.1/stopunp"),
        _source_record("src-paywall", url="https://pay.example/1", doi="10.1/pay"),
        _source_record("src-grp-1", url="https://grp.example/1"),
        _source_record("src-grp-2", url="https://grp.example/1"),
    ]
    _seed_store(run_root, sources)
    held_key = str(archive.put_object(b"held bytes", run_root)["key"])
    archive.upsert_row(cast("JsonObject", _manifest_row(held_key, 10)), run_root)
    archive.upsert_row(cast("JsonObject", _removed_row("b" * 64, 1, source_ids=["src-removed"])), run_root)
    for rec in (
        {
            "schema_version": 1,
            "source_id": "src-held",
            "requested_url": "https://held.example/1",
            "final_url": "https://held.example/1",
            "http_status": 200,
            "fetched_at": FIXED_ISO,
            "content_type": "text/plain",
            "bytes": 10,
            "sha256": held_key,
            "route": "direct",
            "routes_tried": [],
            "outcome": "stored",
            "reason": None,
            "source_class": "other-web",
        },
        {
            "schema_version": 1,
            "source_id": "src-na",
            "requested_url": "https://na.example/1",
            "final_url": "https://na.example/1",
            "http_status": 200,
            "fetched_at": FIXED_ISO,
            "content_type": "text/html",
            "bytes": 2,
            "sha256": held_key,
            "route": "direct",
            "routes_tried": [],
            "outcome": "not_artifact",
            "reason": "text_below_threshold",
            "source_class": "other-web",
        },
        {
            "schema_version": 1,
            "source_id": "src-fail",
            "requested_url": "https://fail.example/1",
            "final_url": None,
            "http_status": 500,
            "fetched_at": FIXED_ISO,
            "content_type": None,
            "bytes": None,
            "sha256": None,
            "route": None,
            "routes_tried": [],
            "outcome": "failed",
            "reason": "http_error",
            "source_class": "other-web",
        },
    ):
        archive.append_receipt(cast("JsonObject", rec), run_root)

    run_logs: list[str] = []
    dry_sum = archivefetch.run_fetch(
        run_root,
        archivefetch.FetchOptions(
            concurrency=1,
            delay_ms=0,
            host_backoff_limit=2,
            pdftotext=False,
            dry_run=True,
            classify=licence.licence_classifier(run_root),
            unpaywall_email="a@b.c",
            transport=_fetch_dry_tx,
            github_token="",
            now_fn=lambda: FIXED_ISO,
            clock_ms=lambda: 0,
            sleep_ms=lambda _ms: None,
            log=run_logs.append,
        ),
    )
    assert (dry_sum["skipped_removed"], dry_sum["skipped_held"]) == (1, 1)
    assert (dry_sum["skipped_not_artifact"], dry_sum["skipped_failed"]) == (1, 1)
    assert (dry_sum["sources_left_for_next_run"], dry_sum["stored"]) == (3, 2)
    assert dry_sum["failed"] == 1
    assert any("2 sources" in line for line in run_logs)

    na_sum = archivefetch.run_fetch(
        run_root,
        archivefetch.FetchOptions(
            concurrency=1,
            delay_ms=0,
            host_backoff_limit=2,
            retry_failed=True,
            pdftotext=False,
            unpaywall_email="a@b.c",
            transport=_fetch_na_tx,
            github_token="",
            now_fn=lambda: FIXED_ISO,
            clock_ms=lambda: 0,
            sleep_ms=lambda _ms: None,
        ),
    )
    assert (na_sum["stored"], na_sum["not_artifact"], na_sum["failed"]) == (1, 1, 6)
    assert (
        "planned"
        in archivesaid.say_fetch(
            run_root, archivefetch.FetchOptions(dry_run=True, limit=0, pdftotext=False), quiet=False
        ).out
    )
    assert (
        archivesaid.say_fetch(
            run_root, archivefetch.FetchOptions(dry_run=True, limit=0, pdftotext=False), quiet=True
        ).err
        == ()
    )


def test_licenceresolve_keys_and_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha1 = "a" * 40
    assert licenceresolve.route_keys_for(f"https://raw.githubusercontent.com/o/r/{sha1}/f", None)[
        "github"
    ] == {"owner": "o", "repo": "r", "ref": sha1}
    assert licenceresolve.route_keys_for(f"https://codeload.github.com/o/r/zip/{sha1}", None)["github"] == {
        "owner": "o",
        "repo": "r",
        "ref": sha1,
    }
    assert licenceresolve.route_keys_for(f"https://api.github.com/repos/o/r/tarball?ref={sha1}", None)[
        "github"
    ] == {"owner": "o", "repo": "r", "ref": sha1}
    assert licenceresolve.route_keys_for("https://github.com/only-one", None) == {}
    assert licenceresolve.route_keys_for(f"https://huggingface.co/datasets/org/ds/tree/{sha1}", None)[
        "huggingface"
    ] == {"kind": "datasets", "id": "org/ds", "revision": sha1}
    assert licenceresolve.route_keys_for("https://huggingface.co/api/spaces/org/sp/resolve/main", None)[
        "huggingface"
    ] == {"kind": "spaces", "id": "org/sp", "revision": None}
    assert licenceresolve.route_keys_for("https://huggingface.co/models", None) == {}
    assert licenceresolve.route_keys_for("https://www.biorxiv.org/about", None) == {}
    assert licenceresolve.route_keys_for("not a url", None) == {}

    with pytest.raises(archive.ArchiveError, match="Refusing non-HTTPS"):
        licenceresolve.http_transport()("http://example.org", {})

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> _FakeHttpResponse:
        _ = timeout
        if "ok" in req.full_url:
            return _FakeHttpResponse(status=200, body=b'{"ok":true}')
        raise _http_error(req.full_url, 404, b"no")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    tx = licenceresolve.http_transport()
    assert tx("https://example.org/ok", {}).status == 200
    assert tx("https://example.org/miss", {}).status == 404
    monkeypatch.setattr("time.sleep", lambda _s: None)
    licenceresolve.ResolveOptions().sleep_ms(1)
    monkeypatch.setattr(
        "subprocess.run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout="gh-tok\n")
    )
    assert licenceresolve.github_token_from_cli() == "gh-tok"
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_a, **_k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["gh"])),
    )
    assert licenceresolve.github_token_from_cli() is None
    monkeypatch.undo()

    cache_dir = tmp_path / "lr-cache"
    cache_dir.mkdir()
    cached_url = "https://cached.example/item"
    (cache_dir / f"{sha256_hex(cached_url)}.json").write_text('{"status":200,"body":"{}"}', encoding="utf-8")

    def client_tx(url: str, headers: Mapping[str, str]) -> licenceresolve.HttpResponse:
        _ = headers
        return licenceresolve.HttpResponse(503 if "stop" in url else 200, "{}")

    client = licenceresolve.Client(
        transport=client_tx,
        cache_dir=cache_dir,
        attempts=2,
        backoff_ms=1000,
        now_fn=lambda: FIXED_ISO,
        sleep_ms=lambda _ms: None,
    )
    assert client.get(cached_url).fetched_at == FIXED_ISO
    assert client.get("https://miss.example/item").status == 200
    with pytest.raises(licenceresolve.HostStopped):
        client.get("https://stop.example/1")
    with pytest.raises(licenceresolve.HostStopped):
        client.get("https://stop.example/2")

    zero_client = licenceresolve.Client(
        transport=client_tx, attempts=0, now_fn=lambda: FIXED_ISO, sleep_ms=lambda _ms: None
    )
    with pytest.raises(licenceresolve.HostStopped):
        zero_client.get("https://zero.example/1")
    race_client = licenceresolve.Client(
        transport=lambda _u, _h: licenceresolve.HttpResponse(429, "{}"),
        attempts=2,
        now_fn=lambda: FIXED_ISO,
        sleep_ms=lambda ms: race_client.stopped.add("race.example") if ms == 2000 else None,
    )
    with pytest.raises(licenceresolve.HostStopped):
        race_client.get("https://race.example/1")


def test_licenceresolve_individual_routes() -> None:
    route_answers: dict[str, licenceresolve.HttpResponse] = {
        "https://api.crossref.org/works/10.1/404": licenceresolve.HttpResponse(404, "{}"),
        "https://api.crossref.org/works/10.1/500": licenceresolve.HttpResponse(500, "bad"),
        "https://api.crossref.org/works/10.1/empty?mailto=a%40b.c": licenceresolve.HttpResponse(
            200,
            json.dumps(
                {
                    "message": {
                        "license": [
                            123,
                            {"content-version": "tdm", "URL": "http://a"},
                            {"content-version": "vor", "URL": "  "},
                        ]
                    }
                }
            ),
        ),
        "https://api.crossref.org/works/10.1/dup": licenceresolve.HttpResponse(
            200,
            json.dumps(
                {
                    "message": {
                        "license": [
                            {"content-version": "vor", "URL": "https://lic/1"},
                            {"content-version": "am", "URL": "https://lic/1"},
                        ]
                    }
                }
            ),
        ),
        "https://api.github.com/repos/o/404/license": licenceresolve.HttpResponse(404, "{}"),
        "https://api.github.com/repos/o/500/license": licenceresolve.HttpResponse(500, "{}"),
        "https://api.github.com/repos/o/nolic/license?ref=abc": licenceresolve.HttpResponse(200, "{}"),
        "https://api.github.com/repos/o/noassert/license": licenceresolve.HttpResponse(
            200, '{"license":{"spdx_id":"NOASSERTION"}}'
        ),
        "https://huggingface.co/api/models/o/401": licenceresolve.HttpResponse(401, "{}"),
        "https://huggingface.co/api/models/o/500": licenceresolve.HttpResponse(500, "{}"),
        "https://huggingface.co/api/models/o/other/revision/main": licenceresolve.HttpResponse(
            200, '{"cardData":{"license":["other"]}}'
        ),
        "https://huggingface.co/api/models/o/none": licenceresolve.HttpResponse(200, '{"cardData":{}}'),
        "https://api.biorxiv.org/details/biorxiv/10.1101/ambig": licenceresolve.HttpResponse(
            200,
            json.dumps(
                {"collection": [{"version": "1", "license": "cc_by"}, {"version": "2", "license": "cc_no"}]}
            ),
        ),
        "https://api.biorxiv.org/details/medrxiv/10.1101/ok": licenceresolve.HttpResponse(
            200, json.dumps({"collection": [{"version": "1", "license": "cc_by"}]})
        ),
        "https://api.biorxiv.org/details/biorxiv/10.1101/500": licenceresolve.HttpResponse(500, "{}"),
        "https://api.biorxiv.org/details/biorxiv/10.1101/empty": licenceresolve.HttpResponse(
            200, '{"collection": []}'
        ),
        "https://api.biorxiv.org/details/biorxiv/10.1101/vmiss": licenceresolve.HttpResponse(
            200, json.dumps({"collection": [{"version": "1", "license": "cc_by"}]})
        ),
        "https://api.biorxiv.org/details/biorxiv/10.1101/nolic": licenceresolve.HttpResponse(
            200, json.dumps({"collection": [{"version": "1", "license": None}]})
        ),
    }

    def route_tx(url: str, headers: Mapping[str, str]) -> licenceresolve.HttpResponse:
        _ = headers
        if "netfail" in url.lower():
            raise OSError("boom")
        if "resultType=core" in url:
            if "PMC500" in url:
                return licenceresolve.HttpResponse(500, "{}")
            if "PMC200" in url:
                return licenceresolve.HttpResponse(
                    200,
                    json.dumps({"resultList": {"result": ["skip", {"license": "mit"}, {"pmcid": "PMC200"}]}}),
                )
        return route_answers.get(url, licenceresolve.HttpResponse(404, "{}"))

    rc = licenceresolve.Client(transport=route_tx, sleep_ms=lambda _ms: None, now_fn=lambda: FIXED_ISO)
    assert licenceresolve.crossref_licences("10.1/netfail", rc, None).reason == "network_error"
    assert licenceresolve.crossref_licences("10.1/404", rc, None).reason == "not_found"
    assert licenceresolve.crossref_licences("10.1/500", rc, None).reason == "http_error"
    assert licenceresolve.crossref_licences("10.1/empty", rc, "a@b.c").reason == "no_licence_in_metadata"
    assert len(licenceresolve.crossref_licences("10.1/dup", rc, None).values) == 1

    assert licenceresolve.europepmc_licences(["PMCNETFAIL"], rc)["PMCNETFAIL"].reason == "network_error"
    assert licenceresolve.europepmc_licences(["PMC500"], rc)["PMC500"].reason == "http_error"
    epmc_res = licenceresolve.europepmc_licences(["PMC200", "PMC999"], rc)
    assert (epmc_res["PMC200"].reason, epmc_res["PMC999"].reason) == ("no_licence_in_metadata", "not_found")

    tok = "ghp-test"
    assert licenceresolve.github_licence({"owner": "o", "repo": "netfail"}, rc, tok).reason == "network_error"
    assert licenceresolve.github_licence({"owner": "o", "repo": "404"}, rc, None).reason == "not_found"
    assert licenceresolve.github_licence({"owner": "o", "repo": "500"}, rc, None).reason == "http_error"
    assert (
        licenceresolve.github_licence({"owner": "o", "repo": "nolic", "ref": "abc"}, rc, None).reason
        == "no_licence_in_metadata"
    )
    assert (
        licenceresolve.github_licence({"owner": "o", "repo": "noassert"}, rc, None).reason == "not_asserted"
    )

    assert (
        licenceresolve.huggingface_licence({"kind": "models", "id": "o/netfail"}, rc).reason
        == "network_error"
    )
    assert licenceresolve.huggingface_licence({"kind": "models", "id": "o/401"}, rc).reason == "not_found"
    assert licenceresolve.huggingface_licence({"kind": "models", "id": "o/500"}, rc).reason == "http_error"
    assert (
        licenceresolve.huggingface_licence({"kind": "models", "id": "o/other", "revision": "main"}, rc).reason
        == "not_asserted"
    )
    assert (
        licenceresolve.huggingface_licence({"kind": "models", "id": "o/none"}, rc).reason
        == "no_licence_in_metadata"
    )

    assert (
        licenceresolve.preprint_licence({"server": "biorxiv", "doi": "10.1101/netfail"}, rc).reason
        == "network_error"
    )
    assert (
        licenceresolve.preprint_licence({"server": "biorxiv", "doi": "10.1101/500"}, rc).reason
        == "http_error"
    )
    assert (
        licenceresolve.preprint_licence({"server": "biorxiv", "doi": "10.1101/empty"}, rc).reason
        == "not_found"
    )
    assert (
        licenceresolve.preprint_licence({"server": "biorxiv", "doi": "10.1101/ambig"}, rc).reason
        == "ambiguous_version"
    )
    assert (
        licenceresolve.preprint_licence(
            {"server": "biorxiv", "doi": "10.1101/vmiss", "version": "9"}, rc
        ).reason
        == "not_found"
    )
    assert (
        licenceresolve.preprint_licence({"server": "biorxiv", "doi": "10.1101/nolic"}, rc).reason
        == "no_licence_in_metadata"
    )
    med_pinned = licenceresolve.preprint_licence({"server": None, "doi": "10.1101/ok", "version": "1"}, rc)
    assert med_pinned.values[0]["field"] == "collection[version=1].license"


def _resolve_tx(url: str, headers: Mapping[str, str]) -> licenceresolve.HttpResponse:
    _ = headers
    if "api.biorxiv.org" in url:
        return licenceresolve.HttpResponse(503, "")
    if "10.1/conf" in url or "10.1/same" in url:
        return licenceresolve.HttpResponse(
            200,
            json.dumps(
                {
                    "message": {
                        "license": [
                            {"content-version": "vor", "URL": "CC BY 4.0"},
                            {"content-version": "am", "URL": "MIT"},
                        ]
                    }
                }
            ),
            FIXED_ISO,
        )
    if "europepmc/webservices/rest/search" in url:
        return licenceresolve.HttpResponse(
            200,
            json.dumps({"resultList": {"result": [{"pmcid": "PMC1111", "license": "CC BY 4.0"}]}}),
            FIXED_ISO,
        )
    if "keeppriv" in url:
        return licenceresolve.HttpResponse(
            200, '{"cardData":{"license":["prop_custom","extra_unmapped"]}}', FIXED_ISO
        )
    if "huggingface.co" in url:
        return licenceresolve.HttpResponse(200, '{"cardData":{"license":"cc_by"}}', FIXED_ISO)
    return licenceresolve.HttpResponse(200, '{"license":{"spdx_id":"MIT"}}', FIXED_ISO)


def test_licenceresolve_full_run_and_counts(tmp_path: Path) -> None:
    lr_root = tmp_path / "lr_full"
    many = [_source_record(f"s-{i:03d}", url=f"https://github.com/org{i}/r{i}") for i in range(52)]
    many.extend(_source_record(f"u-{i:03d}", url=f"https://h{i}.example.org/p") for i in range(27))
    many.append(_source_record("s-dup", url="https://github.com/org0/r0"))
    many.append(
        _source_record(
            "s-conf", url="https://github.com/org/conf", doi="10.1/conf", recorded_licence="Proprietary"
        )
    )
    many.append(_source_record("s-leave", url="https://github.com/org/leave", recorded_licence="Proprietary"))
    many.append(_source_record("s-stay-pub", url="https://github.com/org/stay", recorded_licence="MIT"))
    many.append(
        _source_record(
            "s-same-cr", url="https://github.com/org/same", doi="10.1/same", recorded_licence="CC BY 4.0"
        )
    )
    many.append(
        _source_record("s-unmap-t4", url="https://huggingface.co/org/unmap", recorded_licence="Proprietary")
    )
    many.append(
        _source_record(
            "s-unmap-stay", url="https://huggingface.co/org/keeppriv", recorded_licence="Proprietary"
        )
    )
    many.append(
        _source_record("s-stop-pre", url="https://www.biorxiv.org/content/10.1101/stopv1", doi="10.1101/stop")
    )
    many.append(_source_record("s-epmc", url="https://europepmc.org/articles/PMC1111"))
    _seed_store(lr_root, many)

    lr_logs: list[str] = []
    said_loud = archivesaid.say_licence_resolve(
        lr_root,
        licenceresolve.ResolveOptions(
            date="2026-11-21",
            delay_ms=0,
            host_delay_ms={},
            transport=_resolve_tx,
            now_fn=lambda: FIXED_ISO,
            sleep_ms=lambda _ms: None,
            log=lr_logs.append,
        ),
        proposed_keys={"cc_by": "CC BY 4.0", "prop_custom": "Proprietary"},
    )
    assert said_loud.status == 1
    assert any("github: 50 of 56" in line for line in lr_logs)
    md_text = (lr_root / "maintenance/reports/licence-resolve-2026-11-21.md").read_text("utf-8")
    assert "## Hosts that stopped answering" in md_text
    assert "A further" in md_text

    synthetic_counts = licenceresolve.count_run(
        licenceresolve.RunResult(
            date="2026-11-21",
            targets=[],
            candidates=[
                {
                    "source_id": "s1",
                    "status": "resolved",
                    "observed_licence": "MIT",
                    "family": "permissive-code",
                    "archive_tier": "t0-public",
                    "recorded_licence": None,
                    "recorded_tier": "t4-private",
                    "agreement": "new",
                    "observations": [],
                    "unmapped_values": [],
                    "reason": None,
                }
            ],
            receipts=[],
            stopped_hosts=[],
            requests={},
        ),
        licence.read_licence_map(lr_root),
    )
    assert synthetic_counts["resolved_by_route"] == dict.fromkeys(licence.RESOLVE_ROUTES, 0)

    with pytest.raises(archive.ArchiveError, match="--route must be one of"):
        archivesaid.say_licence_resolve(lr_root, licenceresolve.ResolveOptions(routes=["bad"]))
    for route in ("crossref", "github"):
        assert (
            archivesaid.say_licence_resolve(
                lr_root,
                licenceresolve.ResolveOptions(
                    date="2026-11-21",
                    only=["u-000"],
                    limit=1,
                    routes=[route],
                    delay_ms=0,
                    host_delay_ms={},
                    transport=_resolve_tx,
                    sleep_ms=lambda _ms: None,
                ),
                dry_run=True,
            ).status
            == 0
        )

    empty_out = tmp_path / "lr_empty_out"
    _seed_store(empty_out, [_source_record("u-only", url="https://example.org/none")])
    for _ in range(2):
        assert (
            archivesaid.say_licence_resolve(
                empty_out,
                licenceresolve.ResolveOptions(
                    date="2026-11-21", transport=_resolve_tx, sleep_ms=lambda _ms: None
                ),
                quiet=True,
            ).status
            == 0
        )


def test_p14_cli_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "would_upload" in archivesaid.say_upload(tmp_path, dry_run=True, env=R2_ENV).out
    srcs_txt = tmp_path / "sources.txt"
    srcs_txt.write_text("# comment\n\n s-1 \n", encoding="utf-8")
    with pytest.raises(archive.ArchiveError, match="--from takes"):
        archivesaid.say_pull(tmp_path, source="bad")
    pull_json = archivesaid.say_pull(
        tmp_path, source="release", sources_from=srcs_txt, dry_run=True, as_json=True, quiet=True
    )
    assert json.loads(pull_json.out)["dry_run"] is True

    called_cli: list[str] = []

    def make_stub(cmd_name: str) -> Callable[..., archivesaid.Said]:
        def _stub(*_args: object, **_kwargs: object) -> archivesaid.Said:
            called_cli.append(cmd_name)
            return archivesaid.Said(cmd_name)

        return _stub

    for name in ("say_fetch", "say_publish", "say_upload", "say_pull", "say_licence_resolve"):
        monkeypatch.setattr(archivesaid, name, make_stub(name))
    assert cli_main(["archive", "fetch", "--root", str(tmp_path), "--limit", "bad"]) == 1
    assert cli_main(["archive", "fetch", "--root", str(tmp_path), "--limit", "0"]) == 1
    assert cli_main(["archive", "fetch", "--root", str(tmp_path), "--limit", "5", "--dry-run"]) == 0
    assert cli_main(["archive", "publish", "--root", str(tmp_path), "--no-mark", "--dry-run"]) == 0
    assert cli_main(["archive", "upload", "--root", str(tmp_path), "--dry-run"]) == 0
    assert cli_main(["archive", "pull", "--root", str(tmp_path), "--from", "release", "--dry-run"]) == 0
    assert cli_main(["licence", "resolve", "--root", str(tmp_path), "--limit", "2", "--dry-run"]) == 0
    assert called_cli == ["say_fetch", "say_publish", "say_upload", "say_pull", "say_licence_resolve"]


def test_archivenetparity_synthetic_regeneration(tmp_path: Path) -> None:
    # Seed every route type into 2026-11-17 candidates and receipts to cover offline cache synthesis.
    regen_root = tmp_path / "regen_store"
    regen_sources = [
        _source_record("r-epmc-ok", url="https://europepmc.org/articles/PMC1111"),
        _source_record("r-epmc-no", url="https://europepmc.org/articles/PMC2222"),
        _source_record("r-epmc-err", url="https://europepmc.org/articles/PMC3333"),
        _source_record("r-cr-ok", url="https://example.org/cr1", doi="10.1/ok"),
        _source_record("r-cr-404", url="https://example.org/cr2", doi="10.1/404"),
        _source_record("r-cr-none", url="https://example.org/cr3", doi="10.1/none"),
        _source_record("r-gh-ok", url="https://github.com/o/ok"),
        _source_record("r-gh-404", url="https://github.com/o/404"),
        _source_record("r-gh-na", url="https://github.com/o/na"),
        _source_record("r-hf-list", url="https://huggingface.co/o/list"),
        _source_record("r-hf-401", url="https://huggingface.co/o/401"),
        _source_record("r-hf-no", url="https://huggingface.co/o/no"),
        _source_record("r-hf-na", url="https://huggingface.co/o/na"),
        _source_record("r-pre-miss", url="https://example.org/p1", doi="10.1101/miss"),
        _source_record(
            "r-pre-srv-miss", url="https://www.biorxiv.org/content/10.1101/smissv1", doi="10.1101/smiss"
        ),
        _source_record("r-pre-srv", url="https://www.biorxiv.org/content/10.1101/srvv1", doi="10.1101/srv"),
        _source_record("r-pre-med", url="https://example.org/p2", doi="10.1101/med"),
    ]
    _seed_store(regen_root, regen_sources)
    epmc_ep = licenceresolve.europepmc_endpoint("PMCID:PMC1111 OR PMCID:PMC2222 OR PMCID:PMC3333", 3)
    epmc_no_ep = epmc_ep
    synth_receipts = [
        {
            "schema_version": 1,
            "source_id": "r-epmc-ok",
            "route": "europepmc",
            "endpoint": epmc_ep,
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "licence",
            "reason": None,
            "values": ["CC BY 4.0"],
        },
        {
            "schema_version": 1,
            "source_id": "r-epmc-no",
            "route": "europepmc",
            "endpoint": epmc_no_ep,
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "no_licence",
            "reason": "no_licence_in_metadata",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-epmc-err",
            "route": "europepmc",
            "endpoint": epmc_ep,
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-cr-ok",
            "route": "crossref",
            "endpoint": "https://api.crossref.org/works/10.1/ok",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "licence",
            "reason": None,
            "values": ["CC BY 4.0"],
        },
        {
            "schema_version": 1,
            "source_id": "r-cr-404",
            "route": "crossref",
            "endpoint": "https://api.crossref.org/works/10.1/404",
            "attempted_at": FIXED_ISO,
            "http_status": 404,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-cr-none",
            "route": "crossref",
            "endpoint": "https://api.crossref.org/works/10.1/none",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "no_licence",
            "reason": "no_licence_in_metadata",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-gh-ok",
            "route": "github",
            "endpoint": "https://api.github.com/repos/o/ok/license",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "licence",
            "reason": None,
            "values": ["MIT"],
        },
        {
            "schema_version": 1,
            "source_id": "r-gh-404",
            "route": "github",
            "endpoint": "https://api.github.com/repos/o/404/license",
            "attempted_at": FIXED_ISO,
            "http_status": 404,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-gh-na",
            "route": "github",
            "endpoint": "https://api.github.com/repos/o/na/license",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "no_licence",
            "reason": "not_asserted",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-hf-list",
            "route": "huggingface",
            "endpoint": "https://huggingface.co/api/models/o/list",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "licence",
            "reason": None,
            "values": ["MIT", "CC BY 4.0"],
        },
        {
            "schema_version": 1,
            "source_id": "r-hf-401",
            "route": "huggingface",
            "endpoint": "https://huggingface.co/api/models/o/401",
            "attempted_at": FIXED_ISO,
            "http_status": 401,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-hf-no",
            "route": "huggingface",
            "endpoint": "https://huggingface.co/api/models/o/no",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "no_licence",
            "reason": "no_licence_in_metadata",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-hf-na",
            "route": "huggingface",
            "endpoint": "https://huggingface.co/api/models/o/na",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "no_licence",
            "reason": "not_asserted",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-miss",
            "route": "crossref",
            "endpoint": "https://api.crossref.org/works/10.1101/miss",
            "attempted_at": FIXED_ISO,
            "http_status": 404,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-miss",
            "route": "crossref",
            "endpoint": "https://api.crossref.org/works/10.1101/miss",
            "attempted_at": FIXED_ISO,
            "http_status": 404,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-miss",
            "route": "preprint",
            "endpoint": "https://api.biorxiv.org/details/biorxiv/10.1101/miss",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-srv-miss",
            "route": "crossref",
            "endpoint": "https://api.crossref.org/works/10.1101/smiss",
            "attempted_at": FIXED_ISO,
            "http_status": 404,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-srv-miss",
            "route": "preprint",
            "endpoint": "https://api.biorxiv.org/details/biorxiv/10.1101/smiss",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-srv",
            "route": "crossref",
            "endpoint": "https://api.crossref.org/works/10.1101/srv",
            "attempted_at": FIXED_ISO,
            "http_status": 404,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-srv",
            "route": "preprint",
            "endpoint": "https://api.biorxiv.org/details/biorxiv/10.1101/srv",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "licence",
            "reason": None,
            "values": ["CC BY 4.0"],
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-med",
            "route": "crossref",
            "endpoint": "https://api.crossref.org/works/10.1101/med",
            "attempted_at": FIXED_ISO,
            "http_status": 404,
            "outcome": "no_licence",
            "reason": "not_found",
            "values": [],
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-med",
            "route": "preprint",
            "endpoint": "https://api.biorxiv.org/details/medrxiv/10.1101/med",
            "attempted_at": FIXED_ISO,
            "http_status": 200,
            "outcome": "licence",
            "reason": None,
            "values": ["CC BY 4.0"],
        },
    ]
    synth_cands = [
        {
            "schema_version": 1,
            "source_id": "r-epmc-ok",
            "status": "resolved",
            "observed_licence": "CC BY 4.0",
            "family": "cc-by",
            "archive_tier": "t0-public",
            "recorded_licence": None,
            "recorded_tier": "t4-private",
            "agreement": "new",
            "observations": [
                {
                    "route": "europepmc",
                    "endpoint": epmc_ep,
                    "field": "resultList.result[].license",
                    "value": "CC BY 4.0",
                    "observed_at": FIXED_ISO,
                }
            ],
            "unmapped_values": [],
            "reason": None,
        },
        {
            "schema_version": 1,
            "source_id": "r-cr-ok",
            "status": "resolved",
            "observed_licence": "CC BY 4.0",
            "family": "cc-by",
            "archive_tier": "t0-public",
            "recorded_licence": None,
            "recorded_tier": "t4-private",
            "agreement": "new",
            "observations": [
                {
                    "route": "crossref",
                    "endpoint": "https://api.crossref.org/works/10.1/ok",
                    "field": "message.license[content-version=vor].URL",
                    "value": "CC BY 4.0",
                    "observed_at": FIXED_ISO,
                }
            ],
            "unmapped_values": [],
            "reason": None,
        },
        {
            "schema_version": 1,
            "source_id": "r-gh-ok",
            "status": "resolved",
            "observed_licence": "MIT",
            "family": "permissive-code",
            "archive_tier": "t0-public",
            "recorded_licence": None,
            "recorded_tier": "t4-private",
            "agreement": "new",
            "observations": [
                {
                    "route": "github",
                    "endpoint": "https://api.github.com/repos/o/ok/license",
                    "field": "license.spdx_id (from LICENSE)",
                    "value": "MIT",
                    "observed_at": FIXED_ISO,
                }
            ],
            "unmapped_values": [],
            "reason": None,
        },
        {
            "schema_version": 1,
            "source_id": "r-hf-list",
            "status": "resolved",
            "observed_licence": "CC BY 4.0",
            "family": "cc-by",
            "archive_tier": "t0-public",
            "recorded_licence": None,
            "recorded_tier": "t4-private",
            "agreement": "new",
            "observations": [
                {
                    "route": "huggingface",
                    "endpoint": "https://huggingface.co/api/models/o/list",
                    "field": "cardData.license",
                    "value": "MIT",
                    "observed_at": FIXED_ISO,
                },
                {
                    "route": "huggingface",
                    "endpoint": "https://huggingface.co/api/models/o/list",
                    "field": "cardData.license",
                    "value": "CC BY 4.0",
                    "observed_at": FIXED_ISO,
                },
            ],
            "unmapped_values": [],
            "reason": None,
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-srv",
            "status": "resolved",
            "observed_licence": "CC BY 4.0",
            "family": "cc-by",
            "archive_tier": "t0-public",
            "recorded_licence": None,
            "recorded_tier": "t4-private",
            "agreement": "new",
            "observations": [
                {
                    "route": "preprint",
                    "endpoint": "https://api.biorxiv.org/details/biorxiv/10.1101/srv",
                    "field": "collection[version=1].license",
                    "value": "CC BY 4.0",
                    "observed_at": FIXED_ISO,
                }
            ],
            "unmapped_values": [],
            "reason": None,
        },
        {
            "schema_version": 1,
            "source_id": "r-pre-med",
            "status": "resolved",
            "observed_licence": "CC BY 4.0",
            "family": "cc-by",
            "archive_tier": "t0-public",
            "recorded_licence": None,
            "recorded_tier": "t4-private",
            "agreement": "new",
            "observations": [
                {
                    "route": "preprint",
                    "endpoint": "https://api.biorxiv.org/details/medrxiv/10.1101/med",
                    "field": "collection[].license",
                    "value": "CC BY 4.0",
                    "observed_at": FIXED_ISO,
                },
                {
                    "route": "preprint",
                    "endpoint": "https://api.biorxiv.org/details/medrxiv/10.1101/med",
                    "field": "collection[].license",
                    "value": "CC BY 4.0",
                    "observed_at": FIXED_ISO,
                },
            ],
            "unmapped_values": [],
            "reason": None,
        },
    ]
    write_text(
        regen_root / "data/omics/audits/2026-11-17-licence-resolve.receipts.jsonl",
        "".join(f"{canonical_line(cast('JsonObject', r))}\n" for r in synth_receipts) + "\n",
    )
    write_text(
        regen_root / licence.CANDIDATES,
        "".join(f"{canonical_line(cast('JsonObject', c))}\n" for c in synth_cands) + "\n",
    )
    write_text(regen_root / "maintenance/reports/licence-resolve-2026-11-17.tsv", "stale\n")
    write_text(regen_root / "maintenance/reports/licence-resolve-2026-11-17.md", "stale\n")

    rep_regen = archivenetparity.verify_archive_network(
        regen_root,
        cast(
            "JsonObject",
            {
                "recorded_from": {"store_ref": "x"},
                "modules": [],
                "commit_2026_11_17": [],
                "targets": {"count": 15, "sha256": "0" * 64},
            },
        ),
        resolve_root=regen_root,
    )
    assert any("differs from committed store bytes" in p for p in rep_regen.problems)
    assert "2026-11-17 files regenerated" in archivenetparity.summary(rep_regen)

    uncached_root = tmp_path / "uncached_resolve"
    _seed_store(
        uncached_root, [*regen_sources, _source_record("missing-gh", url="https://github.com/unseen/repo")]
    )
    with pytest.raises(StoreError, match="Unexpected network call"):
        archivenetparity.verify_archive_network(
            regen_root,
            cast(
                "JsonObject",
                {
                    "recorded_from": {"store_ref": "x"},
                    "modules": [],
                    "commit_2026_11_17": [],
                    "targets": {"count": 17, "sha256": "0" * 64},
                },
            ),
            resolve_root=uncached_root,
        )

    # Also test exact byte-for-byte regeneration matching when committed 2026-11-17 files match.
    match_root = tmp_path / "match_resolve"
    match_cache = tmp_path / "match_cache"
    _seed_store(match_root, [_source_record("r-match", url="https://github.com/o/match")])
    match_opts = licenceresolve.ResolveOptions(
        date="2026-11-17",
        cache_dir=match_cache,
        delay_ms=0,
        host_delay_ms={},
        transport=lambda _u, _h: licenceresolve.HttpResponse(
            200, '{"license":{"spdx_id":"MIT"},"name":"LICENSE"}', FIXED_ISO
        ),
        now_fn=lambda: FIXED_ISO,
        sleep_ms=lambda _ms: None,
    )
    licenceresolve.run_resolve(match_root, match_opts)
    # Second run reads purely from `match_cache`, yielding requests={} as offline verification expects.
    res_match = licenceresolve.run_resolve(match_root, match_opts)
    counts_match = licenceresolve.count_run(
        res_match,
        licence.read_licence_map(match_root),
        proposed_keys=licenceresolve.HISTORICAL_2026_11_17_PROPOSED_KEYS,
    )
    licenceresolve.write_resolve_outputs(match_root, res_match, counts_match)
    rep_match = archivenetparity.verify_archive_network(
        match_root,
        cast(
            "JsonObject",
            {
                "recorded_from": {"store_ref": "x"},
                "modules": [],
                "commit_2026_11_17": [],
                "targets": {"count": 1, "sha256": "0" * 64},
            },
        ),
        resolve_root=match_root,
    )
    assert rep_match.reproduced_2026_11_17.same == 4


def test_archivenetparity_gate_and_script(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(StoreError, match="cannot be read as a fixture"):
        archivenetparity.load_fixture(tmp_path / "missing.json")
    bad_json_fix = tmp_path / "list.json"
    bad_json_fix.write_text("[]", encoding="utf-8")
    with pytest.raises(StoreError, match="is not a JSON object"):
        archivenetparity.load_fixture(bad_json_fix)

    fake_store = tmp_path / "fake_store"
    _seed_store(fake_store, [_source_record("s0")])
    write_text(fake_store / "scripts/archive/ok.ts", "// ok\n")
    ok_sha = sha256_hex(b"// ok\n")
    exp_bad: dict[str, object] = {
        "recorded_from": {"store_ref": "badref"},
        "modules": [
            {"file": "scripts/archive/ok.ts", "sha256": ok_sha},
            {"file": "scripts/archive/r2.ts", "sha256": "0" * 64},
        ],
        "commit_2026_11_17": [{"file": "data/archive/licence-candidates.jsonl", "sha256": "0" * 64}],
        "targets": {"count": 2, "sha256": "0" * 64},
    }
    bad_fix = json.loads(json.dumps(FIXTURE))
    bad_fix["canonical_github_urls"] = [["https://github.com/a/b/blob/main/x", "wrong"]]
    bad_fix["scenarios"]["store_layers"]["stored"] = []
    bad_fix["scenarios"]["pull_verbatim"]["pull_real"]["downloaded"] = 99
    bad_fix["scenarios"]["publish_marking"]["summary"]["uploaded"] = 99
    bad_fix["scenarios"]["upload_repair"]["calls"] = ["BAD"]
    bad_fix["scenarios"]["fetch_fallback"]["summary"]["stored"] = 99
    bad_fix["scenarios"]["resolve_offline"]["tsv"] = "wrong"
    rep_bad = archivenetparity.verify_archive_network(
        fake_store, cast("JsonObject", exp_bad), cast("JsonObject", bad_fix)
    )
    assert (rep_bad.modules.same, rep_bad.modules.expected) == (1, 2)
    assert len(rep_bad.problems) >= 9
    assert json.loads(archivenetparity.to_json(rep_bad))["store_ref"] == "badref"

    zero_exp = tmp_path / "zero.json"
    zero_exp.write_text(
        json.dumps({**exp_bad, "targets": {"count": 0, "sha256": "0" * 64}}), encoding="utf-8"
    )
    assert archive_network_gate([str(fake_store)], zero_exp) == 2
    assert "expectation lists no targets" in capsys.readouterr().err

    bad_exp = tmp_path / "bad.json"
    bad_exp.write_text(json.dumps(exp_bad), encoding="utf-8")
    assert archive_network_gate([str(fake_store)], bad_exp, FIXTURE_FILE) == 1
    assert "reproduced." in capsys.readouterr().err

    one_store = tmp_path / "one_store"
    _seed_store(one_store, [_source_record("s1")])
    pass_targets = licenceresolve.read_targets(one_store)
    pass_exp = tmp_path / "pass.json"
    pass_exp.write_text(
        json.dumps(
            {
                "recorded_from": {"store_ref": "ok"},
                "modules": [],
                "commit_2026_11_17": [],
                "targets": {"count": 1, "sha256": sha256_hex(js_utf8(f"{js_stringify(pass_targets[0])}\n"))},
            }
        ),
        encoding="utf-8",
    )
    assert archive_network_gate([str(one_store), "--fixture", str(FIXTURE_FILE)], pass_exp) == 0
    assert "All 1 targets, 0 TS modules" in capsys.readouterr().out

    help_run = subprocess.run(  # noqa: S603 - invokes repository script under test
        [sys.executable, str(ROOT / "scripts/verify_store_archive_network.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_run.returncode == 0
    assert "usage:" in help_run.stdout
