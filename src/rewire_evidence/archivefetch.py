"""Archive artifact checks, fallback routes, and polite fetcher (`artifact.ts`, `routes.ts`, `fetch.ts`).

Determines whether a response body is the real primary artifact rather than an interstitial
page or publisher HTML stub, resolves fallback copies through public APIs in fixed order
(`direct`, `unpaywall`, `europepmc`, `preprint`), enforces per-host pacing and backoff,
stores bytes atomically in the object cache, and appends manifest rows and fetch receipts.
"""

from __future__ import annotations

import email.utils
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Final, Protocol, cast

from rewire_evidence import archive, layers, pdftext, weburl, worklist
from rewire_evidence.canonical import JsonObject, JsonValue, utf16_key
from rewire_evidence.classify import classify, decode
from rewire_evidence.js import trim

MINIMUM_CHARACTERS: Final[dict[str, int]] = {"pdf": 1024, "xml": 1024, "html": 1024, "text": 1}
PHRASE_WINDOWS: Final[dict[str, int]] = {"weak": 2048, "strong": 8192}
INTERSTITIAL_PHRASES: Final[tuple[tuple[str, str, re.Pattern[str]], ...]] = (
    (
        "cookie-consent",
        "weak",
        re.compile(
            r"we use cookies|accept all cookies|cookie (policy|settings|preferences)|manage (your )?cookies",
            re.IGNORECASE | re.ASCII,
        ),
    ),
    (
        "cookies-required",
        "weak",
        re.compile(
            r"cookies_not_supported|cookies are not supported|please enable cookies",
            re.IGNORECASE | re.ASCII,
        ),
    ),
    (
        "human-verification",
        "strong",
        re.compile(
            r"verifying you are human|verify you are (a )?human|checking your browser|just a moment|"
            r"attention required",
            re.IGNORECASE | re.ASCII,
        ),
    ),
    (
        "javascript-required",
        "strong",
        re.compile(
            r"(enable|turn on) javascript|javascript is (disabled|required)",
            re.IGNORECASE | re.ASCII,
        ),
    ),
    (
        "access-teaser",
        "strong",
        re.compile(
            r"get access|access options|buy (this )?article|purchase access|institutional access|"
            r"access through your",
            re.IGNORECASE | re.ASCII,
        ),
    ),
    (
        "subscribe-teaser",
        "strong",
        re.compile(
            r"subscribe to (continue|read)|subscription required|become a (member|subscriber)",
            re.IGNORECASE | re.ASCII,
        ),
    ),
    (
        "sign-in-wall",
        "strong",
        re.compile(r"(sign|log) in to (continue|read|view|access)", re.IGNORECASE | re.ASCII),
    ),
)
STOP_SEGMENTS: Final = frozenset(
    {
        "login",
        "signin",
        "sign-in",
        "log-in",
        "logon",
        "auth",
        "authorize",
        "sso",
        "consent",
        "cookieabsent",
        "cookies",
        "captcha",
        "challenge",
        "denied",
        "accessdenied",
        "forbidden",
        "paywall",
        "subscribe",
    }
)
GITHUB_HOSTS: Final = frozenset(
    {
        "github.com",
        "api.github.com",
        "raw.githubusercontent.com",
        "gist.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)

_DEFAULT_CONCURRENCY: Final = 4
_DEFAULT_DELAY_MS: Final = 2000
_DEFAULT_MAX_BYTES: Final = 64 * 1024 * 1024
_DEFAULT_TIMEOUT_MS: Final = 120_000
_DEFAULT_BACKOFF_LIMIT: Final = 3
_MAX_RETRY_AFTER_S: Final = 120
_MAX_RETRY_AFTER_MS: Final = 120_000
_HTTP_NOT_FOUND: Final = 404
_HTTP_TOO_MANY: Final = 429
_HTTP_UNAVAILABLE: Final = 503
_HTTP_CLIENT_MIN: Final = 400
_HTTP_CLIENT_MAX: Final = 500
_HTTP_OK_MIN: Final = 200
_HTTP_OK_MAX: Final = 300
_MIN_BLOB_PARTS: Final = 5
_FALLBACK_COUNT: Final = 3


@dataclass(frozen=True)
class ArtifactCheck:
    """Verdict from `check_artifact` for a downloaded response body."""

    terminal: bool
    reason: str | None
    layer_class: str
    characters: int | None
    signals: list[str]


def _lands_on_stop_path(url: str) -> bool:
    if weburl.host_of(url) is None:
        return False
    pathname = urllib.parse.urlsplit(url.strip()).path
    for segment in pathname.split("/"):
        decoded = urllib.parse.unquote(segment, errors="replace").lower()
        if decoded in STOP_SEGMENTS:
            return True
    return False


def _host_class_of(url: str) -> str | None:
    parsed = weburl.host_of(url)
    return None if parsed is None else worklist.source_class_of(parsed.host)


def check_artifact(  # noqa: PLR0913 - mirrors shared/archive/artifact.ts ArtifactInput
    data: bytes,
    *,
    final_url: str,
    media_type: str | None = None,
    requested_url: str | None = None,
    has_doi: bool = False,
    pdftotext: bool | None = None,
) -> ArtifactCheck:
    """Determine whether `data` is the real artifact or an interstitial/publisher surrogate."""
    found = classify(data, media_type)
    layer_class = found.kind
    if layer_class in {"container", "office", "unknown"}:
        return ArtifactCheck(terminal=True, reason=None, layer_class=layer_class, characters=None, signals=[])

    tree = layers.layer_of(data, media_type, max_depth=0, pdftotext=pdftotext)
    characters = tree.layer.characters if tree.layer is not None else None
    is_html = layer_class == "html"
    signals: list[str] = []

    if is_html:
        markup = decode(data, found.encoding) if found.encoding else ""
        for name, strength, pattern in INTERSTITIAL_PHRASES:
            window = PHRASE_WINDOWS[strength]
            if characters is not None and characters >= window:
                continue
            if pattern.search(markup):
                signals.append(name)
        if _lands_on_stop_path(final_url):
            signals.append("stop-path")
        if signals:
            return ArtifactCheck(
                terminal=False,
                reason="interstitial_body",
                layer_class=layer_class,
                characters=characters,
                signals=signals,
            )

    floor = MINIMUM_CHARACTERS.get(layer_class)
    if characters is not None and floor is not None and characters < floor:
        return ArtifactCheck(
            terminal=False,
            reason="text_below_threshold",
            layer_class=layer_class,
            characters=characters,
            signals=signals,
        )

    if (
        is_html
        and has_doi
        and (
            _host_class_of(final_url) == "publisher"
            or (requested_url is not None and _host_class_of(requested_url) == "publisher")
        )
    ):
        return ArtifactCheck(
            terminal=False,
            reason="publisher_html_only",
            layer_class=layer_class,
            characters=characters,
            signals=signals,
        )

    return ArtifactCheck(
        terminal=True, reason=None, layer_class=layer_class, characters=characters, signals=signals
    )


def _as_object(value: object) -> JsonObject | None:
    return cast("JsonObject", value) if isinstance(value, dict) else None


def _as_text(value: object) -> str | None:
    return trim(value) if isinstance(value, str) and trim(value) else None


def canonical_github_url(url: str) -> str:
    """Rewrite a `github.com/<owner>/<repo>/blob/<ref>/...` URL to `raw.githubusercontent.com`."""
    host = weburl.host_of(url)
    if host is None or host.hostname != "github.com":
        return url
    split = urllib.parse.urlsplit(url.strip())
    parts = [part for part in split.path.split("/") if part]
    if len(parts) < _MIN_BLOB_PARTS or parts[2] != "blob":
        return url
    owner, repo, _, ref, *rest = parts
    search = f"?{split.query}" if split.query else ""
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{'/'.join(rest)}{search}"


@dataclass(frozen=True)
class JsonReply:
    """Response from a route JSON lookup."""

    status: int
    body: JsonValue | None
    transport: str | None = None


@dataclass(frozen=True)
class RouteInput:
    """Inputs supplied to one fallback route resolver."""

    url: str
    doi: str | None
    get_json: Callable[[str], JsonReply]
    unpaywall_email: str | None = None


@dataclass(frozen=True)
class RouteResult:
    """Resolved download URL or non-resolution reason from one route step."""

    url: str | None
    reason: str | None


def _direct_route(given: RouteInput) -> RouteResult:
    return RouteResult(canonical_github_url(given.url), None)


def _unpaywall_route(given: RouteInput) -> RouteResult:
    if not given.doi:
        return RouteResult(None, "no_doi")
    email = os.environ.get("UNPAYWALL_EMAIL") if given.unpaywall_email is None else given.unpaywall_email
    if not email:
        return RouteResult(None, "unpaywall_no_email")
    endpoint = (
        f"https://api.unpaywall.org/v2/{weburl.encode_uri_component(given.doi)}"
        f"?email={weburl.encode_uri_component(email)}"
    )
    reply = given.get_json(endpoint)
    if reply.transport is not None:
        return RouteResult(None, reply.transport)
    if reply.status == _HTTP_NOT_FOUND:
        return RouteResult(None, "not_found")
    record = _as_object(reply.body)
    if reply.status != _HTTP_OK_MIN or record is None:
        return RouteResult(None, "http_error")
    best = _as_object(record.get("best_oa_location"))
    found = None if best is None else (_as_text(best.get("url_for_pdf")) or _as_text(best.get("url")))
    return RouteResult(found, None) if found else RouteResult(None, "no_open_copy")


def _europepmc_route(given: RouteInput) -> RouteResult:
    if not given.doi:
        return RouteResult(None, "no_doi")
    query = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query=DOI:%22{weburl.encode_uri_component(given.doi)}%22&resultType=core&format=json"
    )
    reply = given.get_json(query)
    if reply.transport is not None:
        return RouteResult(None, reply.transport)
    root_obj = _as_object(reply.body)
    if reply.status != _HTTP_OK_MIN or root_obj is None:
        return RouteResult(None, "not_found" if reply.status == _HTTP_NOT_FOUND else "http_error")
    result_list = _as_object(root_obj.get("resultList"))
    raw_results = None if result_list is None else result_list.get("result")
    first = _as_object(raw_results[0]) if isinstance(raw_results, list) and raw_results else None
    if first is None:
        return RouteResult(None, "not_found")
    pmcid = _as_text(first.get("pmcid"))
    in_epmc = first.get("inEPMC") == "Y" or first.get("isOpenAccess") == "Y"
    if not pmcid or not in_epmc:
        return RouteResult(None, "no_open_copy")
    return RouteResult(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML", None)


def _preprint_route(given: RouteInput) -> RouteResult:
    if not given.doi:
        return RouteResult(None, "no_doi")
    transport_failure: str | None = None
    encoded_doi = weburl.encode_uri_component(given.doi)
    for server in ("biorxiv", "medrxiv"):
        reply = given.get_json(f"https://api.biorxiv.org/details/{server}/{encoded_doi}")
        if reply.transport is not None:
            transport_failure = reply.transport
            continue
        root_obj = _as_object(reply.body)
        if reply.status != _HTTP_OK_MIN or root_obj is None:
            continue
        collection = root_obj.get("collection")
        if not isinstance(collection, list) or not collection:
            continue
        newest = _as_object(collection[-1])
        resolved_doi = (None if newest is None else _as_text(newest.get("doi"))) or given.doi
        version = (None if newest is None else _as_text(newest.get("version"))) or "1"
        return RouteResult(f"https://www.{server}.org/content/{resolved_doi}v{version}.full.pdf", None)
    return RouteResult(None, transport_failure or "no_open_copy")


ROUTE_CHAIN: Final[tuple[tuple[str, Callable[[RouteInput], RouteResult]], ...]] = (
    ("direct", _direct_route),
    ("unpaywall", _unpaywall_route),
    ("europepmc", _europepmc_route),
    ("preprint", _preprint_route),
)


def _resolve_github_token(env: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if env is None else env
    from_env = source.get("GITHUB_TOKEN") or source.get("GH_TOKEN")
    if from_env:
        return from_env
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],  # noqa: S607 - fixed gh binary matching fetch.ts
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = completed.stdout.strip()
    return token or None


def request_headers(
    url: str,
    accept: str,
    *,
    github_token: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Request headers for one archive fetch, adding Bearer auth only for GitHub hosts."""
    headers = {"User-Agent": archive.FETCH_USER_AGENT, "Accept": accept}
    host = weburl.host_of(url)
    if host is None or host.hostname not in GITHUB_HOSTS:
        return headers
    token = _resolve_github_token(env) if github_token is None else github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@dataclass(frozen=True)
class FetchResponse:
    """Minimal HTTP response for archive downloads and API lookups."""

    status: int
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    chunks: Iterable[bytes] | None = None

    def header(self, name: str) -> str | None:
        """Case-insensitive header lookup."""
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None

    def iter_chunks(self) -> Iterable[bytes]:
        """Yield response body chunks."""
        if self.chunks is not None:
            return self.chunks
        return (self.body,) if self.body else ()


class FetchTransport(Protocol):
    """Transport callable injected by callers and tests."""

    def __call__(self, url: str, *, headers: Mapping[str, str], timeout_ms: int) -> FetchResponse:
        """Send one GET request to `url` and return a `FetchResponse`."""


def platform_fetch(url: str, *, headers: Mapping[str, str], timeout_ms: int) -> FetchResponse:
    """Perform an HTTPS GET via `urllib.request`, returning status, final URL, headers, and bytes."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise archive.ArchiveError(f"Unsupported URL scheme: {url}")
    req = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310 - scheme validated
    try:
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000.0) as response:  # noqa: S310 - scheme validated
            resp_headers = dict(response.headers.items())
            return FetchResponse(
                status=response.status,
                url=response.geturl() or url,
                headers=resp_headers,
                body=response.read(),
            )
    except urllib.error.HTTPError as error:
        resp_headers = dict(error.headers.items()) if error.headers else {}
        return FetchResponse(
            status=error.code,
            url=error.geturl() or url,
            headers=resp_headers,
            body=error.read(),
        )


def retry_after_ms(header: str | None, *, now_ms: int | None = None) -> int | None:
    """Parse an HTTP `Retry-After` header in seconds or HTTP-date, capped at 120 seconds."""
    if not header:
        return None
    stripped = header.strip()
    if not stripped:
        return 0
    try:
        seconds = float(stripped)
    except ValueError:
        pass
    else:
        if seconds >= 0:
            return int(min(seconds, _MAX_RETRY_AFTER_S) * 1000)
    try:
        parsed_dt = email.utils.parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=UTC)
    current = int(time.time() * 1000) if now_ms is None else now_ms
    target_ms = int(parsed_dt.astimezone(UTC).timestamp() * 1000)
    return max(0, min(target_ms - current, _MAX_RETRY_AFTER_MS))


class _HostLane:
    """Serialized per-host queue enforcing politeness gap and consecutive backoff tracking."""

    def __init__(self, delay_ms: int) -> None:
        self.delay_ms = delay_ms
        self.next_ms = 0
        self.consecutive_backoffs = 0
        self.stopped = False
        self._lock = threading.Lock()

    def hold(self, ms: int, *, clock_ms: Callable[[], int]) -> None:
        self.next_ms = max(self.next_ms, clock_ms() + ms)

    def run[T](
        self,
        work: Callable[[], T],
        *,
        clock_ms: Callable[[], int],
        sleep_ms: Callable[[int], None],
    ) -> T:
        with self._lock:
            wait = self.next_ms - clock_ms()
            if wait > 0:
                sleep_ms(wait)
            try:
                return work()
            finally:
                self.next_ms = max(self.next_ms, clock_ms() + self.delay_ms)


@dataclass(frozen=True)
class _Downloaded:
    status: int
    final_url: str
    content_type: str | None
    bytes: int
    sha256: str
    file: Path


@dataclass(frozen=True)
class _DownloadError:
    error: str
    status: int | None


def _default_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _default_sleep_ms(ms: int) -> None:
    time.sleep(ms / 1000.0)


@dataclass(frozen=True)
class FetchConfig:
    """Politeness, byte ceiling, timeout, and clock/transport hooks for `Fetcher`."""

    root: Path = Path()
    delay_ms: int = _DEFAULT_DELAY_MS
    max_bytes: int = _DEFAULT_MAX_BYTES
    request_timeout_ms: int = _DEFAULT_TIMEOUT_MS
    host_backoff_limit: int = _DEFAULT_BACKOFF_LIMIT
    transport: FetchTransport = platform_fetch
    github_token: str | None = None
    env: Mapping[str, str] | None = None
    clock_ms: Callable[[], int] = _default_clock_ms
    sleep_ms: Callable[[int], None] = _default_sleep_ms


class Fetcher:
    """Polite per-host HTTP client streaming downloads into `incoming` while hashing."""

    def __init__(self, config: FetchConfig) -> None:
        """Initialise host lanes and resolve any GitHub token once for this run."""
        self.config = config
        self._lanes: dict[str, _HostLane] = {}
        self._lanes_lock = threading.Lock()
        self.stopped_hosts: set[str] = set()
        self._token = (
            _resolve_github_token(config.env) if config.github_token is None else config.github_token
        )

    def _lane(self, host: str) -> _HostLane:
        with self._lanes_lock:
            lane = self._lanes.get(host)
            if lane is None:
                lane = _HostLane(self.config.delay_ms)
                self._lanes[host] = lane
            return lane

    def _headers(self, url: str, accept: str) -> dict[str, str]:
        token = self._token or ""
        return request_headers(url, accept, github_token=token, env=self.config.env)

    def _once(
        self,
        url: str,
        accept: str,
        lane: _HostLane,
        consume: Callable[[FetchResponse], object],
    ) -> tuple[str, object]:
        def attempt() -> tuple[str, object]:
            try:
                response = self.config.transport(
                    url,
                    headers=self._headers(url, accept),
                    timeout_ms=self.config.request_timeout_ms,
                )
            except TimeoutError:
                return ("failed", "timeout")
            except (OSError, http.client.HTTPException, archive.ArchiveError):
                return ("failed", "network_error")
            if response.status in {_HTTP_TOO_MANY, _HTTP_UNAVAILABLE}:
                wait = retry_after_ms(response.header("retry-after"), now_ms=self.config.clock_ms())
                lane.hold(self.config.delay_ms * 2 if wait is None else wait, clock_ms=self.config.clock_ms)
                return ("backoff", wait)
            return ("held", consume(response))

        return lane.run(attempt, clock_ms=self.config.clock_ms, sleep_ms=self.config.sleep_ms)

    def _request(
        self, url: str, accept: str, consume: Callable[[FetchResponse], object]
    ) -> tuple[str, object]:
        parsed = weburl.host_of(url)
        host = "" if parsed is None else parsed.host
        if not host:
            return ("transport", "unsupported_url")
        lane = self._lane(host)
        if lane.stopped:
            return ("transport", "host_stopped")
        for attempt_idx in range(2):
            kind, value = self._once(url, accept, lane, consume)
            if kind == "failed":
                return ("transport", value)
            if kind == "held":
                lane.consecutive_backoffs = 0
                return ("held", value)
            lane.consecutive_backoffs += 1
            if lane.consecutive_backoffs >= self.config.host_backoff_limit:
                lane.stopped = True
                self.stopped_hosts.add(host)
                return ("transport", "host_stopped")
            if attempt_idx == 0:
                backoff_ms = cast("int | None", value)
                self.config.sleep_ms(self.config.delay_ms * 2 if backoff_ms is None else backoff_ms)
        return ("transport", "rate_limited")

    def get_json(self, url: str) -> JsonReply:
        """Fetch JSON through the host's polite lane."""

        def consume(response: FetchResponse) -> JsonReply:
            if not _HTTP_OK_MIN <= response.status < _HTTP_OK_MAX:
                return JsonReply(status=response.status, body=None)
            try:
                decoded = b"".join(response.iter_chunks()).decode("utf-8")
                parsed = json.loads(decoded)
            except (UnicodeDecodeError, ValueError, OSError):
                return JsonReply(status=response.status, body=None)
            return JsonReply(status=response.status, body=cast("JsonValue", parsed))

        kind, payload = self._request(url, "application/json", consume)
        if kind == "transport":
            return JsonReply(status=0, body=None, transport=cast("str", payload))
        return cast("JsonReply", payload)

    def download(self, url: str) -> _Downloaded | _DownloadError:
        """Download a candidate artifact body through the host's polite lane."""

        def consume(response: FetchResponse) -> _Downloaded | _DownloadError:
            return self._read_body(url, response)

        kind, payload = self._request(url, "*/*", consume)
        if kind == "transport":
            return _DownloadError(error=cast("str", payload), status=None)
        return cast("_Downloaded | _DownloadError", payload)

    def _read_body(self, url: str, response: FetchResponse) -> _Downloaded | _DownloadError:
        if not _HTTP_OK_MIN <= response.status < _HTTP_OK_MAX:
            reason = "not_found" if response.status == _HTTP_NOT_FOUND else "http_error"
            return _DownloadError(error=reason, status=response.status)
        incoming_dir = archive.cache_root(self.config.root) / "incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        temp = incoming_dir / f"{os.getpid()}-{os.urandom(8).hex()}"
        hasher = hashlib.sha256()
        total = 0
        try:
            with temp.open("wb") as handle:
                for chunk in response.iter_chunks():
                    total += len(chunk)
                    if total > self.config.max_bytes:
                        handle.close()
                        temp.unlink(missing_ok=True)
                        return _DownloadError(error="too_large", status=response.status)
                    hasher.update(chunk)
                    handle.write(chunk)
        except (OSError, http.client.HTTPException):
            temp.unlink(missing_ok=True)
            return _DownloadError(error="network_error", status=response.status)
        if total == 0:
            temp.unlink(missing_ok=True)
            return _DownloadError(error="empty_body", status=response.status)
        return _Downloaded(
            status=response.status,
            final_url=response.url or url,
            content_type=response.header("content-type"),
            bytes=total,
            sha256=hasher.hexdigest(),
            file=temp,
        )


def removed_sources(root: Path = Path()) -> set[str]:
    """All `source_ids` referenced by `t5-no-store` or removed manifest rows."""
    removed: set[str] = set()
    for row in archive.read_manifest(root).values():
        if row["archive_tier"] != archive.NO_STORE and not archive.is_removed(row):
            continue
        removed.update(cast("Sequence[str]", row["source_ids"]))
    return removed


@dataclass(frozen=True)
class FetchPlan:
    """Which work-list sources require a transfer and why the others were skipped."""

    todo: list[JsonObject]
    held_sources: int
    failed_sources: int
    not_artifact_sources: int
    removed_sources: int
    items: list[JsonObject]


def plan_run(root: Path = Path(), *, retry_failed: bool = False) -> FetchPlan:
    """Plan which work-list items need fetching, checking removed sources before `retry_failed`."""
    work = worklist.read_work_list(root)
    items = cast("list[JsonObject]", work["items"])
    latest = archive.latest_by_source(archive.read_receipts(root))
    removed = removed_sources(root)

    todo: list[JsonObject] = []
    held = failed = not_artifact = was_removed = 0
    for item in items:
        source_id = cast("str", item["source_id"])
        if source_id in removed:
            was_removed += 1
            continue
        receipt = latest.get(source_id)
        if (
            receipt is not None
            and receipt["outcome"] == "stored"
            and isinstance(receipt["sha256"], str)
            and archive.has_object(receipt["sha256"], root)
        ):
            held += 1
            continue
        if receipt is not None and receipt["outcome"] == "not_artifact" and not retry_failed:
            not_artifact += 1
            continue
        if receipt is not None and receipt["outcome"] == "failed" and not retry_failed:
            failed += 1
            continue
        todo.append(item)

    return FetchPlan(
        todo=todo,
        held_sources=held,
        failed_sources=failed,
        not_artifact_sources=not_artifact,
        removed_sources=was_removed,
        items=items,
    )


def failure_reason(doi: str | None, tried: Sequence[Mapping[str, JsonValue]]) -> str:
    """Return `paywalled_not_held` when a DOI's three fallbacks all gave conclusive refusals."""
    direct = next((attempt for attempt in tried if attempt["route"] == "direct"), None)
    fallbacks = [attempt for attempt in tried if attempt["route"] != "direct"]

    def conclusive(attempt: Mapping[str, JsonValue]) -> bool:
        status = attempt["status"]
        return attempt["reason"] in {"no_open_copy", "not_found"} or (
            attempt["url"] is not None
            and isinstance(status, int)
            and _HTTP_CLIENT_MIN <= status < _HTTP_CLIENT_MAX
        )

    if doi and len(fallbacks) == _FALLBACK_COUNT and all(conclusive(attempt) for attempt in fallbacks):
        return "paywalled_not_held"
    reason = None if direct is None else direct["reason"]
    return reason if isinstance(reason, str) else "http_error"


@dataclass(frozen=True)
class _GroupOutcome:
    deferred: bool
    receipts: list[JsonObject]
    object_written: bool
    bytes_stored: int


def _failed_outcome(
    group: Sequence[JsonObject],
    tried: Sequence[JsonObject],
    *,
    doi: str | None,
    fetched_at: str,
    persist_root: Path | None,
) -> _GroupOutcome:
    reason = failure_reason(doi, tried)
    first_status = tried[0]["status"] if tried else None
    receipts = [
        archive.parse(
            archive.FETCH_RECEIPT,
            {
                "schema_version": 1,
                "source_id": item["source_id"],
                "requested_url": item["url"],
                "final_url": None,
                "http_status": first_status,
                "fetched_at": fetched_at,
                "content_type": None,
                "bytes": None,
                "sha256": None,
                "route": None,
                "routes_tried": cast("JsonValue", tried),
                "outcome": "failed",
                "reason": reason,
                "source_class": item["source_class"],
            },
        )
        for item in group
    ]
    if persist_root is not None:
        for receipt in receipts:
            archive.append_receipt(receipt, persist_root)
    return _GroupOutcome(deferred=False, receipts=receipts, object_written=False, bytes_stored=0)


def _persist_group_object(  # noqa: PLR0913 - mirrors fetchGroup manifest + receipt write step
    group: Sequence[JsonObject],
    held: _Downloaded,
    *,
    route: str,
    outcome: str,
    reason: str | None,
    tried: Sequence[JsonObject],
    fetched_at: str,
    dry_run: bool,
    root: Path,
    classify_fn: Callable[[Sequence[str]], JsonObject] | None,
) -> _GroupOutcome:
    lead_url = cast("str", group[0]["url"])
    media_type = archive.media_type_of(held.content_type)
    object_written = False
    if dry_run:
        held.file.unlink(missing_ok=True)
    else:
        adopted = archive.adopt_file(held.file, held.sha256, root)
        object_written = bool(adopted["written"])
        source_ids = [cast("str", item["source_id"]) for item in group]
        classified = None if classify_fn is None else classify_fn(source_ids)
        tier = (
            cast("str", classified["archive_tier"])
            if classified is not None
            else archive.DEFAULT_ARCHIVE_TIER
        )
        observed_licence = None if classified is None else classified["observed_licence"]
        exposure = "policy" if classified is None else classified["exposure"]
        archive.upsert_row(
            {
                "sha256": held.sha256,
                "bytes": held.bytes,
                "media_type": media_type,
                "layer": "exact-bytes",
                "origin": held.final_url,
                "requested_url": None if held.final_url == lead_url else lead_url,
                "first_seen_at": fetched_at,
                "store": "local-cache",
                "mirrors": [],
                "visibility": archive.visibility_of(tier),
                "archive_tier": tier,
                "observed_licence": observed_licence,
                "exposure": exposure,
                "derived_from": None,
                "derivation": None,
                "previous_path": None,
                "source_ids": cast("JsonValue", sorted(source_ids, key=utf16_key)),
            },
            root,
        )

    receipts = [
        archive.parse(
            archive.FETCH_RECEIPT,
            {
                "schema_version": 1,
                "source_id": item["source_id"],
                "requested_url": item["url"],
                "final_url": held.final_url,
                "http_status": held.status,
                "fetched_at": fetched_at,
                "content_type": held.content_type,
                "bytes": held.bytes,
                "sha256": held.sha256,
                "route": route,
                "routes_tried": cast("JsonValue", tried),
                "outcome": outcome,
                "reason": reason,
                "source_class": item["source_class"],
            },
        )
        for item in group
    ]
    if not dry_run:
        for receipt in receipts:
            archive.append_receipt(receipt, root)
    return _GroupOutcome(
        deferred=False, receipts=receipts, object_written=object_written, bytes_stored=held.bytes
    )


type _RouteChainResult = tuple[
    list[JsonObject], _Downloaded | None, str | None, tuple[_Downloaded, str, ArtifactCheck] | None, bool
]


def _try_routes(
    fetcher: Fetcher,
    lead_url: str,
    doi: str | None,
    *,
    has_pdftotext: bool,
    unpaywall_email: str | None,
) -> _RouteChainResult:
    tried: list[JsonObject] = []
    downloaded: _Downloaded | None = None
    winner: str | None = None
    candidate: tuple[_Downloaded, str, ArtifactCheck] | None = None
    last_reason: str | None = None
    host_stopped = False

    for route_name, resolver in ROUTE_CHAIN:
        resolved = resolver(RouteInput(lead_url, doi, fetcher.get_json, unpaywall_email=unpaywall_email))
        if resolved.url is None:
            tried.append({"route": route_name, "url": None, "status": None, "reason": resolved.reason})
            if resolved.reason == "host_stopped":
                host_stopped = True
            continue
        got = fetcher.download(resolved.url)
        if isinstance(got, _DownloadError):
            tried.append(
                {"route": route_name, "url": resolved.url, "status": got.status, "reason": got.error}
            )
            last_reason = got.error
            if got.error == "host_stopped":
                host_stopped = True
            continue
        checked = check_artifact(
            got.file.read_bytes(),
            media_type=archive.media_type_of(got.content_type),
            final_url=got.final_url,
            requested_url=resolved.url,
            has_doi=doi is not None,
            pdftotext=has_pdftotext,
        )
        tried.append(
            {"route": route_name, "url": resolved.url, "status": got.status, "reason": checked.reason}
        )
        if checked.terminal:
            if candidate is not None:
                candidate[0].file.unlink(missing_ok=True)
            downloaded = got
            winner = route_name
            break
        if candidate is not None:
            got.file.unlink(missing_ok=True)
        else:
            candidate = (got, route_name, checked)

    deferred = downloaded is None and (host_stopped or last_reason == "host_stopped")
    return tried, downloaded, winner, candidate, deferred


def _fetch_group(  # noqa: PLR0913 - direct port of scripts/archive/fetch.ts fetchGroup
    fetcher: Fetcher,
    group: Sequence[JsonObject],
    *,
    root: Path,
    classify_fn: Callable[[Sequence[str]], JsonObject] | None,
    dry_run: bool,
    has_pdftotext: bool,
    now_fn: Callable[[], str],
    unpaywall_email: str | None,
) -> _GroupOutcome:
    lead_url = cast("str", group[0]["url"])
    doi = next((cast("str", item["doi"]) for item in group if item["doi"] is not None), None)
    tried, downloaded, winner, candidate, deferred = _try_routes(
        fetcher,
        lead_url,
        doi,
        has_pdftotext=has_pdftotext,
        unpaywall_email=unpaywall_email,
    )
    fetched_at = now_fn()
    if deferred:
        if candidate is not None:
            candidate[0].file.unlink(missing_ok=True)
        return _GroupOutcome(deferred=True, receipts=[], object_written=False, bytes_stored=0)
    if downloaded is None and candidate is None:
        persist_root = None if dry_run else root
        return _failed_outcome(group, tried, doi=doi, fetched_at=fetched_at, persist_root=persist_root)
    if downloaded is not None:
        assert winner is not None  # noqa: S101 - terminal download always names winning route
        held, outcome, reason, route = downloaded, "stored", None, winner
    else:
        assert candidate is not None  # noqa: S101 - checked above when downloaded is None
        held = candidate[0]
        outcome = "not_artifact"
        route = candidate[1]
        reason = candidate[2].reason
    return _persist_group_object(
        group,
        held,
        route=route,
        outcome=outcome,
        reason=reason,
        tried=tried,
        fetched_at=fetched_at,
        dry_run=dry_run,
        root=root,
        classify_fn=classify_fn,
    )


@dataclass(frozen=True)
class FetchOptions:
    """Options controlling `run_fetch` and test injection points."""

    concurrency: int = _DEFAULT_CONCURRENCY
    delay_ms: int = _DEFAULT_DELAY_MS
    max_bytes: int = _DEFAULT_MAX_BYTES
    request_timeout_ms: int = _DEFAULT_TIMEOUT_MS
    host_backoff_limit: int = _DEFAULT_BACKOFF_LIMIT
    limit: int | None = None
    retry_failed: bool = False
    dry_run: bool = False
    classify: Callable[[Sequence[str]], JsonObject] | None = None
    pdftotext: bool | None = None
    unpaywall_email: str | None = None
    transport: FetchTransport = platform_fetch
    github_token: str | None = None
    env: Mapping[str, str] | None = None
    now_fn: Callable[[], str] = archive.utc_now
    clock_ms: Callable[[], int] = _default_clock_ms
    sleep_ms: Callable[[int], None] = _default_sleep_ms
    log: Callable[[str], None] | None = None


def run_fetch(root: Path = Path(), options: FetchOptions | None = None) -> JsonObject:
    """Run the archive fetcher across the work list and return a summary JSON object."""
    opts = FetchOptions() if options is None else options
    emit = (lambda _line: None) if opts.log is None else opts.log
    workers = max(1, opts.concurrency)
    fetcher = Fetcher(
        FetchConfig(
            root=root,
            delay_ms=opts.delay_ms,
            max_bytes=opts.max_bytes,
            request_timeout_ms=opts.request_timeout_ms,
            host_backoff_limit=opts.host_backoff_limit,
            transport=opts.transport,
            github_token=opts.github_token,
            env=opts.env,
            clock_ms=opts.clock_ms,
            sleep_ms=opts.sleep_ms,
        )
    )
    shutil.rmtree(archive.cache_root(root) / "incoming", ignore_errors=True)
    plan = plan_run(root, retry_failed=opts.retry_failed)
    groups = list(worklist.group_by_url(plan.todo).values())
    planned_groups = groups if opts.limit is None else groups[: opts.limit]
    has_pdf = pdftext.has_pdftotext() if opts.pdftotext is None else opts.pdftotext

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(
            pool.map(
                lambda group: _fetch_group(
                    fetcher,
                    group,
                    root=root,
                    classify_fn=opts.classify,
                    dry_run=opts.dry_run,
                    has_pdftotext=has_pdf,
                    now_fn=opts.now_fn,
                    unpaywall_email=opts.unpaywall_email,
                ),
                planned_groups,
            )
        )

    attempted = stored = not_artifact = failed = objects_written = bytes_stored = deferred_sources = 0
    failures_by_reason: dict[str, int] = {}
    not_artifact_by_reason: dict[str, int] = {}

    for group, result in zip(planned_groups, outcomes, strict=True):
        url = cast("str", group[0]["url"])
        if result.deferred:
            deferred_sources += len(group)
            emit(f"deferred {url}: host stopped for this run")
            continue
        attempted += len(group)
        for receipt in result.receipts:
            reason = cast("str | None", receipt["reason"]) or "http_error"
            if receipt["outcome"] == "stored":
                stored += 1
            elif receipt["outcome"] == "not_artifact":
                not_artifact += 1
                not_artifact_by_reason[reason] = not_artifact_by_reason.get(reason, 0) + 1
            else:
                failed += 1
                failures_by_reason[reason] = failures_by_reason.get(reason, 0) + 1
        if result.object_written:
            objects_written += 1
            bytes_stored += result.bytes_stored
        lead = result.receipts[0]
        why = f": {lead['reason']}" if lead["reason"] else ""
        plural = "" if len(group) == 1 else "s"
        emit(f"{lead['outcome']} {url} ({len(group)} source{plural}){why}")

    return {
        "planned": sum(len(group) for group in planned_groups),
        "skipped_held": plan.held_sources,
        "skipped_failed": plan.failed_sources,
        "skipped_not_artifact": plan.not_artifact_sources,
        "skipped_removed": plan.removed_sources,
        "attempted": attempted,
        "stored": stored,
        "not_artifact": not_artifact,
        "failed": failed,
        "objects_written": objects_written,
        "bytes_stored": bytes_stored,
        "failures_by_reason": cast("JsonValue", failures_by_reason),
        "not_artifact_by_reason": cast("JsonValue", not_artifact_by_reason),
        "hosts_stopped": cast("JsonValue", sorted(fetcher.stopped_hosts, key=utf16_key)),
        "sources_left_for_next_run": deferred_sources,
    }
