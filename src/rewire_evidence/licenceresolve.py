"""Licence resolution against Crossref, Europe PMC, GitHub, Hugging Face, and preprints.

This ports `scripts/licence/resolve.ts`. It queries public metadata endpoints cleanly using
cached responses and polite per-host queues, builds unpromoted candidates via
`licence.build_candidate`, writes candidate tables and audit reports, and reproduces the
committed 2026-11-17 licence resolution artifacts.
"""

from __future__ import annotations

import http.client
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast

from rewire_evidence import archive, licence, weburl, worklist
from rewire_evidence.canonical import JsonObject, JsonValue, canonical_line, sha256_hex, utf16_key
from rewire_evidence.families import AUDIT_DIR
from rewire_evidence.js import fixed, lower, trim
from rewire_evidence.jsonl import append_text, write_text

USER_AGENT: Final = archive.LICENCE_RESOLVE_USER_AGENT
PROPOSED_MAP_KEYS: Final[dict[str, str]] = {}
HISTORICAL_2026_11_17_PROPOSED_KEYS: Final[dict[str, str]] = {
    "cc by": "CC BY 4.0",
    "https://creativecommons.org/licenses/by/4.0": "https://creativecommons.org/licenses/by/4.0/",
    "http://creativecommons.org/licenses/by/4.0/": "https://creativecommons.org/licenses/by/4.0/",
    "cc by-nc-nd": "CC-BY-NC-ND-4.0",
    "https://creativecommons.org/licenses/by-nc-nd/4.0": "CC-BY-NC-ND-4.0",
    "cc_by_nc_nd": "CC-BY-NC-ND-4.0",
    "cc by-nc": "CC BY-NC 4.0",
    "cc_by": "CC BY 4.0",
    "http://creativecommons.org/licenses/by-nc-nd/4.0/": "CC-BY-NC-ND-4.0",
    "http://creativecommons.org/licenses/by-nc/4.0/": "CC BY-NC 4.0",
    "apache-2.0": "Apache-2.0",
    "mit": "MIT",
    "https://creativecommons.org/licenses/by-nc/4.0/": "CC BY-NC 4.0",
    "https://creativecommons.org/licenses/by-nc-nd/4.0/": "CC-BY-NC-ND-4.0",
    "cc_by_nc": "CC BY-NC 4.0",
    "cc-by-nc-sa-4.0": "CC-BY-NC-SA-4.0",
    "bsd-3-clause": "BSD-3-Clause",
    "cc0": "CC0-1.0",
    "cc_no": "All rights reserved (bioRxiv licence 'cc_no')",
    "https://creativecommons.org/publicdomain/zero/1.0/": "CC0-1.0",
    "cc_by_nd": "CC-BY-ND-4.0",
    "cc-by-nc-4.0": "CC-BY-NC-4.0",
    "http://creativecommons.org/licenses/by-nd/4.0/": "CC-BY-ND-4.0",
    "http://creativecommons.org/licenses/by/2.0/": "CC-BY-2.0",
}
CANDIDATE_COLUMNS: Final = (
    "source_id",
    "status",
    "observed_licence",
    "family",
    "archive_tier",
    "recorded_licence",
    "recorded_tier",
    "agreement",
    "routes",
    "endpoints",
    "fields",
    "observed_at",
    "unmapped_values",
    "reason",
)
DEFAULT_HOST_DELAY_MS: Final[dict[str, int]] = {
    "www.ebi.ac.uk": 1000,
    "api.biorxiv.org": 1000,
    "api.crossref.org": 250,
    "api.github.com": 150,
    "huggingface.co": 250,
}

_SHA1: Final = re.compile(r"[0-9a-f]{40}", re.ASCII)
_PMCID: Final = re.compile(r"PMC[0-9]{4,}", re.ASCII)
_PREPRINT_HOST: Final = re.compile(r"(?:^|\.)(biorxiv|medrxiv)\.org$", re.ASCII)
_PREPRINT_PATH: Final = re.compile(
    r"(10\.[0-9]{4,9}/[^\s?]+?)(v([0-9]+))?(\.full|\.full\.pdf|\.supplementary-material)?$",
    re.ASCII,
)
_PREPRINT_PREFIXES: Final = ("10.1101/", "10.64898/")
_CELL_SEP: Final = re.compile(r"[\t\n]+")
_ROUTE_ORDER: Final = {name: idx for idx, name in enumerate(licence.RESOLVE_ROUTES)}
_DEFAULT_DELAY_MS: Final = 250
_DEFAULT_ATTEMPTS: Final = 3
_DEFAULT_BACKOFF_MS: Final = 2000
_DEFAULT_BATCH_SIZE: Final = 25
_HTTP_OK: Final = 200
_HTTP_UNAUTHORIZED: Final = 401
_HTTP_NOT_FOUND: Final = 404
_HTTP_TOO_MANY: Final = 429
_HTTP_UNAVAILABLE: Final = 503
_TOP_UNRESOLVED_HOSTS: Final = 25
_TOP_UNRESOLVED_REASONS: Final = 20
_PROGRESS_INTERVAL: Final = 50


def _trimmed(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = trim(value)
    return cleaned or None


def _host_of(url: str) -> str:
    parsed = weburl.host_of(url)
    return "" if parsed is None else parsed.hostname


def _github_keys(host: str, segments: Sequence[str], url: str) -> JsonObject | None:
    if host == "raw.githubusercontent.com" and len(segments) >= 3:  # noqa: PLR2004 - owner/repo/ref
        ref = segments[2] if _SHA1.fullmatch(segments[2]) else None
        return {"owner": segments[0], "repo": segments[1], "ref": ref}
    if host == "codeload.github.com" and len(segments) >= 4:  # noqa: PLR2004 - owner/repo/zip/ref
        ref = segments[-1] if _SHA1.fullmatch(segments[-1]) else None
        return {"owner": segments[0], "repo": segments[1], "ref": ref}
    if host == "api.github.com" and len(segments) >= 3 and segments[0] == "repos":  # noqa: PLR2004
        query = urllib.parse.urlsplit(url.strip()).query
        ref_param = urllib.parse.parse_qs(query).get("ref", [None])[0]
        ref = ref_param if ref_param is not None and _SHA1.fullmatch(ref_param) else None
        return {"owner": segments[1], "repo": segments[2], "ref": ref}
    if host in {"github.com", "www.github.com"} and len(segments) >= 2:  # noqa: PLR2004 - owner/repo
        pinned = (
            segments[3]
            if (
                len(segments) >= 4  # noqa: PLR2004
                and segments[2] in {"blob", "tree", "raw", "commit"}
                and _SHA1.fullmatch(segments[3])
            )
            else None
        )
        return {"owner": segments[0], "repo": segments[1].removesuffix(".git"), "ref": pinned}
    return None


def _huggingface_keys(host: str, segments: Sequence[str]) -> JsonObject | None:
    if host not in {"huggingface.co", "www.huggingface.co"}:
        return None
    rest = list(segments[1:] if segments and segments[0] == "api" else segments)
    kind = "models"
    if rest and rest[0] in {"datasets", "spaces"}:
        kind = "datasets" if rest[0] == "datasets" else "spaces"
        rest = rest[1:]
    if rest and rest[0] == "models":
        rest = rest[1:]
    verbs = {"resolve", "raw", "blob", "tree", "revision", "commit"}
    verb = next((idx for idx, seg in enumerate(rest) if seg in verbs), -1)
    repo_id = "/".join(rest[:verb] if verb > 0 else rest[:2])
    revision = rest[verb + 1] if verb > 0 and verb + 1 < len(rest) else None
    if not repo_id:
        return None
    ref = revision if revision and _SHA1.fullmatch(revision) else None
    return {"kind": kind, "id": repo_id, "revision": ref}


def route_keys_for(url: str, doi: str | None) -> JsonObject:
    """Derive resolver lookup keys from a source URL and DOI alone."""
    keys: JsonObject = {}
    host = _host_of(url)
    pathname = ""
    if weburl.host_of(url) is not None:
        pathname = urllib.parse.unquote(urllib.parse.urlsplit(url.strip()).path, errors="replace")
    segments = [seg for seg in pathname.split("/") if seg]

    if doi:
        keys["crossref"] = doi
    pmcid = _PMCID.search(url)
    if pmcid:
        keys["europepmc"] = pmcid.group(0)
    github_key = _github_keys(host, segments, url)
    if github_key is not None:
        keys["github"] = github_key
    hf_key = _huggingface_keys(host, segments)
    if hf_key is not None:
        keys["huggingface"] = hf_key

    preprint_host = _PREPRINT_HOST.search(host)
    preprint_doi = doi if doi and any(doi.startswith(prefix) for prefix in _PREPRINT_PREFIXES) else None
    if preprint_host or preprint_doi:
        server = preprint_host.group(1) if preprint_host else None
        stripped_path = re.sub(r"^/(details|content)/", "", pathname, count=1)
        stripped_path = re.sub(r"^(biorxiv|medrxiv)/", "", stripped_path, count=1)
        in_path = _PREPRINT_PATH.search(stripped_path)
        from_path = (
            in_path.group(1)
            if in_path and any(in_path.group(1).startswith(prefix) for prefix in _PREPRINT_PREFIXES)
            else None
        )
        chosen = preprint_doi or from_path
        if chosen:
            keys["preprint"] = {
                "server": server,
                "doi": chosen,
                "version": in_path.group(3) if in_path and in_path.group(3) else None,
            }
    return keys


def read_targets(root: Path = Path()) -> list[JsonObject]:
    """Read every source record with route lookup keys and current recorded licence."""
    held = licence.read_licence_map(root)
    licences = licence.read_source_licences(root)
    targets: list[JsonObject] = []
    for record in worklist.source_records(root):
        source_id = cast("str", record["id"])
        attributes = worklist.attributes_of(record)
        url = _trimmed(attributes.get("artifact_url")) or _trimmed(attributes.get("url")) or ""
        doi = _trimmed(attributes.get("doi"))
        targets.append(
            {
                "source_id": source_id,
                "url": url,
                "host": _host_of(url),
                "doi": doi,
                "recorded_licence": licences.get(source_id),
                "recorded_tier": licence.determine_for_source(source_id, held, licences)["archive_tier"],
                "keys": route_keys_for(url, doi),
            }
        )
    return targets


@dataclass(frozen=True)
class HttpResponse:
    """Minimal HTTP response returned by a resolver transport."""

    status: int
    body: str
    fetched_at: str | None = None


class ResolveTransport(Protocol):
    """HTTP transport interface injected for offline testing and caching."""

    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        """Send one GET request to `url` with `headers`."""


class HostStopped(Exception):  # noqa: N818 - mirrors scripts/licence/resolve.ts HostStopped class name
    """Raised when a host keeps answering 429 or 503 after all retries."""

    def __init__(self, host: str) -> None:
        """Record which host stopped answering and construct its message."""
        self.host = host
        super().__init__(f"{host} stopped answering: 429 or 503 past the backoff")


def http_transport(timeout_ms: int = 30_000) -> ResolveTransport:
    """Default stdlib HTTPS GET transport honoring HTTP error bodies."""

    def send(url: str, headers: Mapping[str, str]) -> HttpResponse:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https":
            raise archive.ArchiveError(f"Refusing non-HTTPS licence URL: {url}")
        req = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310 - scheme validated
        try:
            with urllib.request.urlopen(req, timeout=timeout_ms / 1000.0) as response:  # noqa: S310 - scheme validated
                body = response.read().decode("utf-8", "replace")
                return HttpResponse(status=response.status, body=body, fetched_at=archive.utc_now())
        except urllib.error.HTTPError as error:
            return HttpResponse(
                status=error.code,
                body=error.read().decode("utf-8", "replace"),
                fetched_at=archive.utc_now(),
            )

    return send


def _default_sleep_ms(ms: int) -> None:
    time.sleep(ms / 1000.0)


class Client:
    """Serialized, rate-limited HTTP client with SHA-256 response file caching."""

    def __init__(  # noqa: PLR0913 - mirrors scripts/licence/resolve.ts Client options
        self,
        *,
        transport: ResolveTransport,
        delay_ms: int = _DEFAULT_DELAY_MS,
        host_delay_ms: Mapping[str, int] | None = None,
        cache_dir: Path | None = None,
        attempts: int = _DEFAULT_ATTEMPTS,
        backoff_ms: int = _DEFAULT_BACKOFF_MS,
        log: Callable[[str], None] | None = None,
        now_fn: Callable[[], str] = archive.utc_now,
        sleep_ms: Callable[[int], None] = _default_sleep_ms,
    ) -> None:
        """Initialise per-host request locks, retry parameters, and optional cache directory."""
        self._transport = transport
        self._delay_ms = delay_ms
        self._host_delay_ms = dict(host_delay_ms or {})
        self._cache_dir = cache_dir
        self._attempts = attempts
        self._backoff_ms = backoff_ms
        self._log = (lambda _line: None) if log is None else log
        self._now = now_fn
        self._sleep_ms = sleep_ms
        self._locks: dict[str, threading.Lock] = {}
        self.stopped: set[str] = set()
        self.counts: dict[str, int] = {}

    def _cache_file(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{sha256_hex(url)}.json"

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResponse:
        """Fetch `url` from cache or network under per-host delay and retry controls."""
        host = _host_of(url)
        if host in self.stopped:
            raise HostStopped(host)
        cached = self._cache_file(url)
        if cached is not None and cached.exists():
            held = cast("JsonObject", json.loads(cached.read_text("utf-8")))
            fetched_at = held.get("fetched_at")
            return HttpResponse(
                status=cast("int", held["status"]),
                body=cast("str", held["body"]),
                fetched_at=fetched_at if isinstance(fetched_at, str) else self._now(),
            )
        lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            response = self._fetch_once(host, url, headers or {})
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(
                json.dumps(
                    {
                        "status": response.status,
                        "body": response.body,
                        "fetched_at": response.fetched_at,
                    }
                ),
                encoding="utf-8",
            )
        return response

    def _fetch_once(self, host: str, url: str, headers: Mapping[str, str]) -> HttpResponse:
        delay = self._host_delay_ms.get(host, self._delay_ms)
        for attempt in range(1, self._attempts + 1):
            if host in self.stopped:
                raise HostStopped(host)
            self._sleep_ms(delay)
            self.counts[host] = self.counts.get(host, 0) + 1
            answered = self._transport(url, {"user-agent": USER_AGENT, **headers})
            fetched_at = self._now() if answered.fetched_at is None else answered.fetched_at
            response = HttpResponse(status=answered.status, body=answered.body, fetched_at=fetched_at)
            if response.status not in {_HTTP_TOO_MANY, _HTTP_UNAVAILABLE}:
                return response
            if attempt == self._attempts:
                break
            wait = self._backoff_ms * attempt * attempt
            self._log(
                f"{host} answered {response.status}, waiting {round(wait / 1000)}s "
                f"(attempt {attempt} of {self._attempts})"
            )
            self._sleep_ms(wait)
        self.stopped.add(host)
        self._log(f"{host} is still answering 429 or 503. Nothing further is asked of it in this run.")
        raise HostStopped(host)


@dataclass(frozen=True)
class RouteOutcome:
    """Resolved values or failure for one key on one resolver route."""

    endpoint: str
    http_status: int | None
    values: list[JsonObject]
    reason: str | None
    observed_at: str | None
    request: str | None = None


def _parse_json(body: str) -> JsonValue | None:
    try:
        return cast("JsonValue", json.loads(body))
    except ValueError:
        return None


def _failure(
    endpoint: str,
    status: int | None,
    reason: str,
    observed_at: str | None = None,
    *,
    request: str | None = None,
) -> RouteOutcome:
    return RouteOutcome(
        endpoint=endpoint,
        http_status=status,
        values=[],
        reason=reason,
        observed_at=observed_at,
        request=request,
    )


def _transport_reason(error: Exception) -> str:
    return "host_stopped" if isinstance(error, HostStopped) else "network_error"


def crossref_licences(doi: str, client: Client, contact: str | None) -> RouteOutcome:
    """Look up Crossref `vor` and `am` licences for one DOI."""
    endpoint = f"https://api.crossref.org/works/{weburl.encode_uri(doi)}"
    asked = f"{endpoint}?mailto={weburl.encode_uri_component(contact)}" if contact else endpoint
    try:
        response = client.get(asked)
    except (HostStopped, OSError, http.client.HTTPException, ValueError) as error:
        return _failure(endpoint, None, _transport_reason(error))
    at = response.fetched_at
    if response.status == _HTTP_NOT_FOUND:
        return _failure(endpoint, _HTTP_NOT_FOUND, "not_found", at)
    parsed = _parse_json(response.body)
    message = parsed.get("message") if isinstance(parsed, dict) else None
    if response.status != _HTTP_OK or not isinstance(message, dict):
        return _failure(endpoint, response.status, "http_error", at)
    raw_entries = message.get("license")
    entries = raw_entries if isinstance(raw_entries, list) else []
    values: list[JsonObject] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        version = entry.get("content-version")
        if version not in {"vor", "am"}:
            continue
        value = _trimmed(entry.get("URL"))
        if value and value not in seen:
            seen.add(value)
            values.append({"value": value, "field": f"message.license[content-version={version!s}].URL"})
    if not values:
        return _failure(endpoint, response.status, "no_licence_in_metadata", at)
    return RouteOutcome(
        endpoint=endpoint, http_status=response.status, values=values, reason=None, observed_at=at
    )


def europepmc_endpoint(query: str, page_size: int) -> str:
    """Build a Europe PMC REST search URL."""
    encoded = weburl.encode_uri_component(query)
    return (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={encoded}&resultType=core&format=json&pageSize={page_size}"
    )


def _epmc_record_outcome(
    record: Mapping[str, JsonValue],
    one_url: str,
    status: int,
    observed_at: str | None,
    request: str,
) -> RouteOutcome:
    value = _trimmed(record.get("license"))
    if value:
        return RouteOutcome(
            endpoint=one_url,
            request=request,
            http_status=status,
            values=[{"value": value, "field": "resultList.result[].license"}],
            reason=None,
            observed_at=observed_at,
        )
    return _failure(one_url, status, "no_licence_in_metadata", observed_at, request=request)


def europepmc_licences(pmcids: Sequence[str], client: Client) -> dict[str, RouteOutcome]:
    """Query Europe PMC in batches and return per-PMCID `RouteOutcome` results."""
    results: dict[str, RouteOutcome] = {}
    request = europepmc_endpoint(" OR ".join(f"PMCID:{pmcid}" for pmcid in pmcids), len(pmcids))

    def one(pmcid: str) -> str:
        return europepmc_endpoint(f"PMCID:{pmcid}", 1)

    try:
        response = client.get(request)
    except (HostStopped, OSError, http.client.HTTPException, ValueError) as error:
        reason = _transport_reason(error)
        return {pmcid: _failure(one(pmcid), None, reason, request=request) for pmcid in pmcids}

    parsed = _parse_json(response.body)
    result_list = parsed.get("resultList") if isinstance(parsed, dict) else None
    at = response.fetched_at
    if response.status != _HTTP_OK or not isinstance(result_list, dict):
        return {
            pmcid: _failure(one(pmcid), response.status, "http_error", at, request=request)
            for pmcid in pmcids
        }

    raw_records = result_list.get("result")
    for record in raw_records if isinstance(raw_records, list) else ():
        if not isinstance(record, dict):
            continue
        pmcid = _trimmed(record.get("pmcid"))
        if pmcid:
            results[pmcid] = _epmc_record_outcome(record, one(pmcid), response.status, at, request)
    for pmcid in pmcids:
        if pmcid not in results:
            results[pmcid] = _failure(one(pmcid), response.status, "not_found", at, request=request)
    return results


def github_licence(key: Mapping[str, JsonValue], client: Client, token: str | None) -> RouteOutcome:
    """Query GitHub's repository licence endpoint at an optional commit ref."""
    owner = weburl.encode_uri_component(cast("str", key["owner"]))
    repo = weburl.encode_uri_component(cast("str", key["repo"]))
    ref = key.get("ref")
    base = f"https://api.github.com/repos/{owner}/{repo}/license"
    endpoint = f"{base}?ref={ref}" if ref else base
    headers: dict[str, str] = {"accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    try:
        response = client.get(endpoint, headers)
    except (HostStopped, OSError, http.client.HTTPException, ValueError) as error:
        return _failure(endpoint, None, _transport_reason(error))
    at = response.fetched_at
    if response.status == _HTTP_NOT_FOUND:
        return _failure(endpoint, _HTTP_NOT_FOUND, "not_found", at)
    if response.status != _HTTP_OK:
        return _failure(endpoint, response.status, "http_error", at)
    body = _parse_json(response.body)
    lic_obj = body.get("license") if isinstance(body, dict) else None
    spdx = _trimmed(lic_obj.get("spdx_id")) if isinstance(lic_obj, dict) else None
    if not spdx:
        return _failure(endpoint, response.status, "no_licence_in_metadata", at)
    if spdx == "NOASSERTION" or lower(spdx) == "other":
        return _failure(endpoint, response.status, "not_asserted", at)
    filename = _trimmed(body.get("name")) if isinstance(body, dict) else None
    label = filename or "the repository licence file"
    return RouteOutcome(
        endpoint=endpoint,
        http_status=response.status,
        values=[{"value": spdx, "field": f"license.spdx_id (from {label})"}],
        reason=None,
        observed_at=at,
    )


def huggingface_licence(key: Mapping[str, JsonValue], client: Client) -> RouteOutcome:
    """Query a Hugging Face model, dataset, or space `cardData.license` field."""
    base = f"https://huggingface.co/api/{key['kind']}/{key['id']}"
    revision = key.get("revision")
    endpoint = f"{base}/revision/{revision}" if revision else base
    try:
        response = client.get(endpoint)
    except (HostStopped, OSError, http.client.HTTPException, ValueError) as error:
        return _failure(endpoint, None, _transport_reason(error))
    at = response.fetched_at
    if response.status in {_HTTP_NOT_FOUND, _HTTP_UNAUTHORIZED}:
        return _failure(endpoint, response.status, "not_found", at)
    if response.status != _HTTP_OK:
        return _failure(endpoint, response.status, "http_error", at)
    parsed = _parse_json(response.body)
    card = parsed.get("cardData") if isinstance(parsed, dict) else None
    declared = card.get("license") if isinstance(card, dict) else None
    items = declared if isinstance(declared, list) else [declared]
    cleaned = [val for item in items if (val := _trimmed(item)) is not None]
    values: list[JsonObject] = [
        {"value": val, "field": "cardData.license"}
        for val in cleaned
        if lower(val) not in {"other", "unknown"}
    ]
    if not values:
        reason = "not_asserted" if cleaned else "no_licence_in_metadata"
        return _failure(endpoint, response.status, reason, at)
    return RouteOutcome(
        endpoint=endpoint, http_status=response.status, values=values, reason=None, observed_at=at
    )


def _evaluate_preprint(
    endpoint: str,
    response: HttpResponse,
    given_version: str | None,
) -> RouteOutcome:
    at = response.fetched_at
    if response.status != _HTTP_OK:
        return _failure(endpoint, response.status, "http_error", at)
    parsed = _parse_json(response.body)
    collection = parsed.get("collection") if isinstance(parsed, dict) else None
    if not isinstance(collection, list) or not collection:
        return _failure(endpoint, response.status, "not_found", at)
    versions = [
        (str(entry.get("version") or ""), _trimmed(entry.get("license")))
        for entry in collection
        if isinstance(entry, dict)
    ]
    pinned = [item for item in versions if item[0] == given_version] if given_version else versions
    distinct = list(dict.fromkeys(lic for _, lic in pinned if lic is not None))
    if not distinct:
        reason = "no_licence_in_metadata" if pinned else "not_found"
        return _failure(endpoint, response.status, reason, at)
    if len(distinct) > 1 and not given_version:
        return _failure(endpoint, response.status, "ambiguous_version", at)
    field_name = f"collection[version={given_version}].license" if given_version else "collection[].license"
    return RouteOutcome(
        endpoint=endpoint,
        http_status=response.status,
        values=[{"value": val, "field": field_name} for val in distinct],
        reason=None,
        observed_at=at,
    )


def preprint_licence(key: Mapping[str, JsonValue], client: Client) -> RouteOutcome:
    """Query bioRxiv/medRxiv API for one preprint DOI and optional version pin."""
    doi = cast("str", key["doi"])
    given_server = cast("str | None", key.get("server"))
    given_version = cast("str | None", key.get("version"))
    servers = (given_server,) if given_server else ("biorxiv", "medrxiv")
    last: RouteOutcome | None = None
    for server in servers:
        endpoint = f"https://api.biorxiv.org/details/{server}/{doi}"
        try:
            response = client.get(endpoint)
        except (HostStopped, OSError, http.client.HTTPException, ValueError) as error:
            last = _failure(endpoint, None, _transport_reason(error))
            continue
        outcome = _evaluate_preprint(endpoint, response, given_version)
        if outcome.reason is None:
            return outcome
        last = outcome
    fallback_url = f"https://api.biorxiv.org/details/biorxiv/{doi}"
    return last if last is not None else _failure(fallback_url, None, "not_found")


@dataclass(frozen=True)
class ResolveOptions:
    """Configuration and test hooks for `run_resolve`."""

    date: str | None = None
    transport: ResolveTransport | None = None
    github_token: str | None = None
    crossref_contact: str | None = None
    cache_dir: Path | None = None
    delay_ms: int = _DEFAULT_DELAY_MS
    host_delay_ms: Mapping[str, int] | None = None
    only: Sequence[str] | None = None
    limit: int | None = None
    routes: Sequence[str] | None = None
    batch_size: int = _DEFAULT_BATCH_SIZE
    log: Callable[[str], None] | None = None
    now_fn: Callable[[], str] = archive.utc_now
    sleep_ms: Callable[[int], None] = _default_sleep_ms


@dataclass(frozen=True)
class RunResult:
    """Complete results from `run_resolve`."""

    date: str
    targets: list[JsonObject]
    candidates: list[JsonObject]
    receipts: list[JsonObject]
    stopped_hosts: list[str]
    requests: dict[str, int]


@dataclass(frozen=True)
class _RunContext:
    targets: Sequence[JsonObject]
    client: Client
    opts: ResolveOptions
    record_fn: Callable[[JsonObject, str, RouteOutcome], None]
    emit: Callable[[str], None]


def _run_simple_route(
    route_name: str,
    ctx: _RunContext,
    key_id: Callable[[JsonObject], tuple[str, object] | None],
    ask: Callable[[object], RouteOutcome],
) -> None:
    groups: dict[str, tuple[object, list[JsonObject]]] = {}
    for target in ctx.targets:
        key_pair = key_id(target)
        if key_pair is None:
            continue
        group_key, payload = key_pair
        if group_key in groups:
            groups[group_key][1].append(target)
        else:
            groups[group_key] = (payload, [target])
    total = len(groups)
    ctx.emit(f"{route_name}: {total} keys")
    for idx, (payload, group) in enumerate(groups.values(), 1):
        result = ask(payload)
        for target in group:
            ctx.record_fn(target, route_name, result)
        if idx % _PROGRESS_INTERVAL == 0:
            ctx.emit(f"{route_name}: {idx} of {total}")


def _run_europepmc_batches(ctx: _RunContext) -> None:
    by_pmcid: dict[str, list[JsonObject]] = {}
    for target in ctx.targets:
        pmcid = cast("str | None", cast("JsonObject", target["keys"]).get("europepmc"))
        if pmcid:
            by_pmcid.setdefault(pmcid, []).append(target)
    ids = list(by_pmcid)
    batch_size = ctx.opts.batch_size
    ctx.emit(f"europepmc: {len(ids)} PMCIDs in {(len(ids) + batch_size - 1) // batch_size} requests")
    for start in range(0, len(ids), batch_size):
        batch = ids[start : start + batch_size]
        epmc_results = europepmc_licences(batch, ctx.client)
        for pmcid in batch:
            outcome = epmc_results.get(pmcid)
            if outcome is None:  # pragma: no cover - europepmc_licences fills every requested PMCID
                continue
            for target in by_pmcid[pmcid]:
                ctx.record_fn(target, "europepmc", outcome)
        ctx.emit(f"europepmc: {min(start + batch_size, len(ids))} of {len(ids)}")


def _make_recorder(
    receipts: list[JsonObject],
    observations: dict[str, list[JsonObject]],
    now_fn: Callable[[], str],
) -> Callable[[JsonObject, str, RouteOutcome], None]:
    def record(target: JsonObject, route: str, result: RouteOutcome) -> None:
        at = result.observed_at or now_fn()
        receipts.append(
            archive.parse(
                licence.RESOLVE_RECEIPT,
                {
                    "schema_version": 1,
                    "source_id": target["source_id"],
                    "route": route,
                    "endpoint": result.request if result.request is not None else result.endpoint,
                    "attempted_at": at,
                    "http_status": result.http_status,
                    "outcome": "licence" if result.values else "no_licence",
                    "reason": result.reason,
                    "values": [item["value"] for item in result.values],
                },
            )
        )
        if not result.values:
            return
        bucket = observations.setdefault(cast("str", target["source_id"]), [])
        for item in result.values:
            bucket.append(
                archive.parse(
                    licence.OBSERVATION,
                    {
                        "route": route,
                        "endpoint": result.endpoint,
                        "field": item["field"],
                        "value": item["value"],
                        "observed_at": at,
                    },
                )
            )

    return record


def _repo_key(
    target: JsonObject, name: str, format_id: Callable[[JsonObject], str]
) -> tuple[str, object] | None:
    held = cast("JsonObject | None", cast("JsonObject", target["keys"]).get(name))
    return None if held is None else (format_id(held), held)


def _dispatch_routes(wanted: set[str], ctx: _RunContext) -> None:
    if "crossref" in wanted:
        _run_simple_route(
            "crossref",
            ctx,
            lambda t: (
                (doi, doi)
                if (doi := cast("str | None", cast("JsonObject", t["keys"]).get("crossref")))
                else None
            ),
            lambda p: crossref_licences(cast("str", p), ctx.client, ctx.opts.crossref_contact),
        )
    if "github" in wanted:
        _run_simple_route(
            "github",
            ctx,
            lambda t: _repo_key(t, "github", lambda g: f"{g['owner']}/{g['repo']}@{g['ref'] or 'default'}"),
            lambda p: github_licence(cast("Mapping[str, JsonValue]", p), ctx.client, ctx.opts.github_token),
        )
    if "huggingface" in wanted:
        _run_simple_route(
            "huggingface",
            ctx,
            lambda t: _repo_key(
                t, "huggingface", lambda h: f"{h['kind']}/{h['id']}@{h['revision'] or 'default'}"
            ),
            lambda p: huggingface_licence(cast("Mapping[str, JsonValue]", p), ctx.client),
        )
    if "preprint" in wanted:
        _run_simple_route(
            "preprint",
            ctx,
            lambda t: _repo_key(
                t, "preprint", lambda p: f"{p['server'] or 'unknown'}/{p['doi']}v{p['version'] or 'all'}"
            ),
            lambda p: preprint_licence(cast("Mapping[str, JsonValue]", p), ctx.client),
        )
    if "europepmc" in wanted:
        _run_europepmc_batches(ctx)


def run_resolve(root: Path = Path(), options: ResolveOptions | None = None) -> RunResult:
    """Resolve source licences across the five routes without mutating source records."""
    opts = ResolveOptions() if options is None else options
    date = opts.date or archive.utc_now()[:10]
    held = licence.read_licence_map(root)
    emit = (lambda _line: None) if opts.log is None else opts.log
    wanted = set(opts.routes) if opts.routes else set(licence.RESOLVE_ROUTES)
    targets = read_targets(root)
    if opts.only:
        only_set = set(opts.only)
        targets = [target for target in targets if target["source_id"] in only_set]
    if opts.limit:
        targets = targets[: opts.limit]

    client = Client(
        transport=opts.transport if opts.transport is not None else http_transport(),
        delay_ms=opts.delay_ms,
        host_delay_ms=DEFAULT_HOST_DELAY_MS if opts.host_delay_ms is None else opts.host_delay_ms,
        cache_dir=opts.cache_dir,
        log=emit,
        now_fn=opts.now_fn,
        sleep_ms=opts.sleep_ms,
    )
    receipts: list[JsonObject] = []
    observations: dict[str, list[JsonObject]] = {}
    record_fn = _make_recorder(receipts, observations, opts.now_fn)
    _dispatch_routes(wanted, _RunContext(targets, client, opts, record_fn, emit))

    candidates: list[JsonObject] = []
    for target in targets:
        sid = cast("str", target["source_id"])
        candidate = licence.build_candidate(target, observations.get(sid, []), held)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda c: utf16_key(cast("str", c["source_id"])))
    receipts.sort(
        key=lambda r: (utf16_key(cast("str", r["source_id"])), _ROUTE_ORDER[cast("str", r["route"])])
    )
    ordered_requests = {host: client.counts[host] for host in sorted(client.counts, key=utf16_key)}
    return RunResult(
        date=date,
        targets=targets,
        candidates=candidates,
        receipts=receipts,
        stopped_hosts=sorted(client.stopped, key=utf16_key),
        requests=ordered_requests,
    )


def _cell(value: object) -> str:
    return trim(_CELL_SEP.sub(" ", str(value)))


def render_candidate_table(result: RunResult) -> str:
    """Format `result.candidates` as `maintenance/reports/licence-resolve-<date>.tsv`."""
    head = [
        (
            "# Licence candidates, one row per source with at least one observation. "
            "Generated, never hand written."
        ),
        "#   npm run licence:resolve",
        f"# Generated {result.date}. A candidate is never promoted into a source record by this file.",
        "\t".join(CANDIDATE_COLUMNS),
    ]
    body: list[str] = []
    for candidate in result.candidates:
        obs = cast("list[JsonObject]", candidate["observations"])
        unmapped = cast("list[str]", candidate["unmapped_values"])
        fields = [
            candidate["source_id"],
            candidate["status"],
            candidate["observed_licence"] or "",
            candidate["family"] or "",
            candidate["archive_tier"] or "",
            candidate["recorded_licence"] or "",
            candidate["recorded_tier"],
            candidate["agreement"],
            " ".join(cast("str", o["route"]) for o in obs),
            " ".join(cast("str", o["endpoint"]) for o in obs),
            " | ".join(cast("str", o["field"]) for o in obs),
            obs[0]["observed_at"],
            " | ".join(unmapped),
            candidate["reason"] or "",
        ]
        body.append("\t".join(_cell(value) for value in fields))
    return "\n".join([*head, *body]) + "\n"


def _tally_candidates(
    result: RunResult,
    held: licence.LicenceMap,
    proposed: Mapping[str, str],
) -> tuple[
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[str, int],
    int,
    int,
    dict[str, tuple[set[str], set[str], str]],
]:
    by_status = dict.fromkeys(licence.CANDIDATE_STATUSES, 0)
    resolved_by_route = dict.fromkeys(licence.RESOLVE_ROUTES, 0)
    by_tier: dict[str, int] = {}
    agreement: dict[str, int] = {}
    unmapped: dict[str, tuple[set[str], set[str], str]] = {}
    leaves_t4 = leaves_t4_if_proposed = 0

    for candidate in result.candidates:
        status = cast("str", candidate["status"])
        source_id = cast("str", candidate["source_id"])
        by_status[status] += 1
        agr = cast("str", candidate["agreement"])
        agreement[agr] = agreement.get(agr, 0) + 1
        obs = cast("list[JsonObject]", candidate["observations"])
        if status == "resolved" and candidate["archive_tier"]:
            tier = cast("str", candidate["archive_tier"])
            by_tier[tier] = by_tier.get(tier, 0) + 1
            carried = next((o for o in obs if o["value"] == candidate["observed_licence"]), None)
            if carried is not None:
                resolved_by_route[cast("str", carried["route"])] += 1
            if candidate["recorded_tier"] == "t4-private" and tier not in {"t4-private", archive.NO_STORE}:
                leaves_t4 += 1
                leaves_t4_if_proposed += 1
        for value in cast("list[str]", candidate["unmapped_values"]):
            entry = unmapped.setdefault(value, (set(), set(), source_id))
            entry[0].add(source_id)
            for item in obs:
                if item["value"] == value:
                    entry[1].add(cast("str", item["route"]))
        if status == "unmapped" and candidate["recorded_tier"] == "t4-private":
            tiers = [
                cast("str", held[key]["archive_tier"])
                for value in cast("list[str]", candidate["unmapped_values"])
                if (key := proposed.get(value)) and key in held
            ]
            distinct = set(tiers)
            if len(distinct) == 1 and next(iter(distinct)) not in {"t4-private", archive.NO_STORE}:
                leaves_t4_if_proposed += 1

    return (
        by_status,
        resolved_by_route,
        by_tier,
        agreement,
        leaves_t4,
        leaves_t4_if_proposed,
        unmapped,
    )


def count_run(
    result: RunResult,
    held: licence.LicenceMap,
    *,
    proposed_keys: Mapping[str, str] | None = None,
) -> JsonObject:
    """Compute every summary count reported by `render_report` and the CLI."""
    proposed = PROPOSED_MAP_KEYS if proposed_keys is None else proposed_keys
    by_route: dict[str, JsonObject] = {
        route: {"sources": 0, "licence": 0, "no_licence": 0, "by_reason": {}}
        for route in licence.RESOLVE_ROUTES
    }
    for receipt in result.receipts:
        entry = by_route[cast("str", receipt["route"])]
        entry["sources"] = cast("int", entry["sources"]) + 1
        if receipt["outcome"] == "licence":
            entry["licence"] = cast("int", entry["licence"]) + 1
        else:
            entry["no_licence"] = cast("int", entry["no_licence"]) + 1
            reason = cast("str | None", receipt["reason"]) or "unknown"
            reasons = cast("dict[str, int]", entry["by_reason"])
            reasons[reason] = reasons.get(reason, 0) + 1

    by_status, resolved_by_route, by_tier, agreement, leaves_t4, leaves_t4_if_proposed, unmapped = (
        _tally_candidates(result, held, proposed)
    )
    with_candidate = {cast("str", c["source_id"]) for c in result.candidates}
    unresolved_hosts: dict[str, int] = {}
    unresolved_classes: dict[str, int] = {}
    for target in result.targets:
        if target["source_id"] in with_candidate:
            continue
        host = cast("str", target["host"]) or "no-url"
        unresolved_hosts[host] = unresolved_hosts.get(host, 0) + 1
        name = worklist.source_class_of(host) if target["host"] else "no-url"
        unresolved_classes[name] = unresolved_classes.get(name, 0) + 1

    unresolved_reasons: dict[str, int] = {}
    for receipt in result.receipts:
        if receipt["source_id"] in with_candidate or receipt["outcome"] == "licence":
            continue
        reason = cast("str | None", receipt["reason"]) or "unknown"
        key = f"{receipt['route']}:{reason}"
        unresolved_reasons[key] = unresolved_reasons.get(key, 0) + 1

    unmapped_strings = []
    for value, (sources, routes, example) in unmapped.items():
        proposed_key = proposed.get(value)
        proposed_row = held.get(proposed_key) if proposed_key else None
        unmapped_strings.append(
            {
                "value": value,
                "sources": len(sources),
                "routes": sorted(routes, key=utf16_key),
                "example": example,
                "proposed": proposed_key,
                "proposed_tier": None if proposed_row is None else proposed_row["archive_tier"],
            }
        )
    unmapped_strings.sort(key=lambda e: (-cast("int", e["sources"]), utf16_key(cast("str", e["value"]))))
    host_rows = [
        {"host": h, "sources": n}
        for h, n in sorted(unresolved_hosts.items(), key=lambda kv: (-kv[1], utf16_key(kv[0])))
    ]
    class_rows = [
        {"source_class": sc, "sources": n}
        for sc, n in sorted(unresolved_classes.items(), key=lambda kv: (-kv[1], utf16_key(kv[0])))
    ]
    ordered_reasons = dict(sorted(unresolved_reasons.items(), key=lambda kv: -kv[1]))
    attempted = len({cast("str", r["source_id"]) for r in result.receipts})

    return {
        "sources": len(result.targets),
        "attempted": attempted,
        "candidates": len(result.candidates),
        "by_status": cast("JsonValue", by_status),
        "by_route": cast("JsonValue", by_route),
        "resolved_by_route": cast("JsonValue", resolved_by_route),
        "by_tier": cast("JsonValue", by_tier),
        "leaves_t4": leaves_t4,
        "leaves_t4_if_proposed": leaves_t4_if_proposed,
        "agreement": cast("JsonValue", agreement),
        "unmapped_strings": cast("JsonValue", unmapped_strings),
        "unresolved_hosts": cast("JsonValue", host_rows),
        "unresolved_classes": cast("JsonValue", class_rows),
        "unresolved_reasons": cast("JsonValue", ordered_reasons),
    }


def _md_table(lines: list[str], header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    lines.append(f"| {' | '.join(header)} |")
    lines.append(f"| {' | '.join('---' for _ in header)} |")
    lines.extend(f"| {' | '.join(row)} |" for row in rows)
    lines.append("")


def render_report(result: RunResult, counts: Mapping[str, JsonValue]) -> str:
    """Format the markdown licence resolution report matching `resolve.ts:renderReport`."""
    lines: list[str] = []
    by_status = cast("dict[str, int]", counts["by_status"])
    by_route = cast("dict[str, JsonObject]", counts["by_route"])
    resolved_by_route = cast("dict[str, int]", counts["resolved_by_route"])
    by_tier = cast("dict[str, int]", counts["by_tier"])
    agreement = cast("dict[str, int]", counts["agreement"])
    unmapped_strings = cast("list[JsonObject]", counts["unmapped_strings"])
    unresolved_hosts = cast("list[JsonObject]", counts["unresolved_hosts"])
    unresolved_classes = cast("list[JsonObject]", counts["unresolved_classes"])
    unresolved_reasons = cast("dict[str, int]", counts["unresolved_reasons"])
    total_sources = cast("int", counts["sources"])
    total_candidates = cast("int", counts["candidates"])

    lines.extend(
        [
            f"# Licence resolver run, {result.date}",
            "",
            "Generated by `npm run licence:resolve`. Do not edit. Every figure below is counted over the",
            "candidates and receipts this run wrote, not modelled.",
            "",
            "A candidate is a candidate. Nothing here is written into a source record, no licence a person",
            "recorded is changed, and no tier comes from anywhere but `data/archive/licence-map.tsv`.",
            "",
            "## What the run did",
            "",
        ]
    )
    _md_table(
        lines,
        ["What", "Count"],
        [
            ["source records read", str(total_sources)],
            ["sources at least one route applied to", str(counts["attempted"])],
            ["sources a service stated a licence for", str(total_candidates)],
            ["resolved to a tier through the licence map", str(by_status["resolved"])],
            ["served a string the map has no row for", str(by_status["unmapped"])],
            ["two services disagreeing, resolved to nothing", str(by_status["conflict"])],
            ["no service stated a licence", str(total_sources - total_candidates)],
        ],
    )
    lines.extend(
        [
            "## The number that decides how much can be published",
            "",
            "Sources that leave t4-private if these candidates are accepted, with the licence map exactly as",
            (
                f"it stands today: **{counts['leaves_t4']}**. That is the measurement, counted over the "
                "candidates below."
            ),
            "",
            "One further figure is conditional and is not a measurement of anything yet. Sources that would",
            (
                "leave t4-private if the owner also added a reviewed row for each unmapped string below, "
                "with the"
            ),
            (
                f"reading this report proposes: {counts['leaves_t4_if_proposed']}. Those rows do not exist, "
                "so that number is a forecast of a"
            ),
            "decision rather than a count of a state.",
            "",
            "## Per route",
            "",
            "One attempt per source per route. A route that applies to a source and states no licence is",
            "counted here, which is what makes the hit rate a measurement.",
            "",
        ]
    )
    route_rows = [
        [
            f"`{route}`",
            str(entry["sources"]),
            str(entry["licence"]),
            f"{fixed((cast('int', entry['licence']) / cast('int', entry['sources'])) * 100, 1)} percent",
            str(resolved_by_route[route]),
        ]
        for route in licence.RESOLVE_ROUTES
        if cast("int", (entry := by_route[route])["sources"]) > 0
    ]
    _md_table(
        lines,
        ["Route", "Sources asked", "Stated a licence", "Hit rate", "Carried the resolved value"],
        route_rows,
    )
    lines.extend(["Why a route stated nothing:", ""])
    for route in licence.RESOLVE_ROUTES:
        reasons = cast("dict[str, int]", by_route[route]["by_reason"])
        ordered = sorted(reasons.items(), key=lambda kv: -kv[1])
        if ordered:
            lines.append(f"- `{route}`: {', '.join(f'{reason} {count}' for reason, count in ordered)}")
    lines.extend(["", "## Tiers the resolved candidates map to", ""])
    _md_table(
        lines,
        ["Tier", "Candidates"],
        [[f"`{tier}`", str(by_tier[tier])] for tier in sorted(by_tier, key=utf16_key)],
    )
    lines.extend(["## Against the licence a person recorded", ""])
    _md_table(
        lines,
        ["Candidate sits", "Sources"],
        [[state, str(agreement[state])] for state in sorted(agreement, key=utf16_key)],
    )
    lines.extend(
        [
            "`disagrees` is a research lead, not a correction. The recorded value stays exactly as it is and",
            "the candidate sits beside it in `data/archive/licence-candidates.jsonl`.",
            "",
            "## Strings the licence map has no row for",
            "",
            "Each one is a build failure by design: the resolver exits non-zero while any of these is",
            "unmapped, so a new licence string cannot become a silent t4-private. The proposed key is the",
            "existing map row a reviewer would most likely point the string at. It is a suggestion for the",
            "owner, it sets no tier, and the resolver does not consult it.",
            "",
        ]
    )
    _md_table(
        lines,
        [
            "Value as served",
            "Sources",
            "Routes",
            "Example source",
            "Proposed map row",
            "Tier that row carries",
        ],
        [
            [
                f"`{entry['value']}`",
                str(entry["sources"]),
                " ".join(cast("list[str]", entry["routes"])),
                f"`{entry['example']}`",
                f"`{entry['proposed']}`" if entry["proposed"] else "none proposed",
                f"`{entry['proposed_tier']}`" if entry["proposed_tier"] else "",
            ]
            for entry in unmapped_strings
        ],
    )
    lines.extend(
        [
            "## Still unresolved",
            "",
            (
                f"{total_sources - total_candidates} sources came away with no licence statement from "
                "any service. By host:"
            ),
            "",
        ]
    )
    _md_table(
        lines,
        ["Host", "Sources"],
        [[cast("str", e["host"]), str(e["sources"])] for e in unresolved_hosts[:_TOP_UNRESOLVED_HOSTS]],
    )
    rest = sum(cast("int", e["sources"]) for e in unresolved_hosts[_TOP_UNRESOLVED_HOSTS:])
    if rest:
        other = len(unresolved_hosts) - _TOP_UNRESOLVED_HOSTS
        lines.append(f"A further {rest} sources sit on {other} other hosts.")
    lines.extend(
        [
            "",
            "By the host-behaviour class the repository keys treatments on. A class with no route is one",
            "this resolver leaves alone on purpose: the rule is to resolve nothing rather than guess, and",
            "arXiv, a data repository's rights field and a project web page each need their own route.",
            "",
        ]
    )
    _md_table(
        lines,
        ["Source class", "Sources"],
        [[f"`{e['source_class']}`", str(e["sources"])] for e in unresolved_classes],
    )
    lines.extend(["Why, by route and reason:", ""])
    lines.extend(
        f"- `{key}`: {count}" for key, count in list(unresolved_reasons.items())[:_TOP_UNRESOLVED_REASONS]
    )
    lines.append("")
    if result.stopped_hosts:
        lines.extend(
            [
                "## Hosts that stopped answering",
                "",
                (
                    "A host that answers 429 or 503 past the backoff is not asked again in the run. Its "
                    "sources are"
                ),
                "unresolved for this run and nothing about them is inferred.",
                "",
                *[f"- {host}" for host in result.stopped_hosts],
                "",
            ]
        )
    lines.extend(["## Requests made", ""])
    if result.requests:
        lines.extend(
            [
                (
                    "One line per host, counting every request the run sent, including a retry after a "
                    "429 or a 503."
                ),
                "",
            ]
        )
        _md_table(lines, ["Host", "Requests"], [[h, str(c)] for h, c in result.requests.items()])
    else:
        lines.extend(
            [
                "None. Every response came from the response cache of an earlier run, which keeps each",
                "response's own retrieval date, so the dates above are the dates of the transfers and not of",
                "this run.",
                "",
            ]
        )
    lines.extend(
        [
            "## Files",
            "",
            "- `data/archive/licence-candidates.jsonl`: one row per candidate. `readCandidates()` in",
            "  `scripts/licence/resolve.ts` parses it. The triage queue does not consume it yet.",
            (
                f"- `data/omics/audits/{result.date}-licence-resolve.receipts.jsonl`: every attempt, "
                "including the ones"
            ),
            "  that stated nothing.",
            f"- `maintenance/reports/licence-resolve-{result.date}.tsv`: the full candidate list as a table.",
            "",
            "A candidate is promoted into `attributes.licence` by a record change like any other, and the",
            "manifest's `observed_licence` is written by the archive work, not here.",
        ]
    )
    return "\n".join(lines) + "\n"


def github_token_from_cli() -> str | None:
    """Read an authenticated GitHub token via `gh auth token` when available."""
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],  # noqa: S607 - fixed gh binary matching resolve.ts
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = completed.stdout.strip()
    return token or None


def write_resolve_outputs(root: Path, result: RunResult, counts: Mapping[str, JsonValue]) -> list[str]:
    """Write candidates, receipts, markdown report, and TSV candidate table under `root`."""
    candidates_file = root / licence.CANDIDATES
    receipts_file = root / AUDIT_DIR / f"{result.date}-licence-resolve.receipts.jsonl"
    report_file = root / "maintenance" / "reports" / f"licence-resolve-{result.date}.md"
    table_file = root / "maintenance" / "reports" / f"licence-resolve-{result.date}.tsv"

    candidates_text = "".join(f"{canonical_line(row)}\n" for row in result.candidates)
    write_text(candidates_file, candidates_text)

    receipts_text = "".join(f"{canonical_line(row)}\n" for row in result.receipts)
    receipts_file.parent.mkdir(parents=True, exist_ok=True)
    if receipts_text:
        append_text(receipts_file, receipts_text)
    elif not receipts_file.exists():
        write_text(receipts_file, "")

    write_text(report_file, render_report(result, counts))
    write_text(table_file, render_candidate_table(result))
    return [
        f"{candidates_file}: {len(result.candidates)} candidates",
        f"{receipts_file}: {len(result.receipts)} attempts appended",
        f"{report_file}",
        f"{table_file}",
    ]
