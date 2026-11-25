"""What the archive and licence commands print: `npm run archive --` and the licence scripts.

Each function returns what the TypeScript's command writes to standard output, the lines it
writes to standard error, and its exit status. `rewire_evidence.cli` does the writing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from rewire_evidence import (
    archive,
    archivefetch,
    archivepublish,
    licence,
    licenceresolve,
    r2,
    reclassify,
    worklist,
)
from rewire_evidence.canonical import JsonObject, JsonValue, js_indented, js_key_order, utf16_key
from rewire_evidence.js import fixed
from rewire_evidence.jsonl import write_text

REPORT_DIR = "maintenance/reports"
#: How many hosts the work list prints before it counts the rest.
HOSTS_SHOWN = 25


@dataclass(frozen=True)
class Said:
    """What a command wrote and how it left."""

    out: str
    status: int = 0
    err: tuple[str, ...] = ()


def _counted(held: JsonValue, shown: int | None = None) -> list[str]:
    """A line for each member of a tally, in the order JavaScript lists an object's members."""
    counts = cast("Mapping[str, int]", held)
    return [f"  {str(counts[name]).rjust(5)}  {name}" for name in js_key_order(counts)[:shown]]


def _mib(size: JsonValue) -> str:
    return f"{fixed(cast('int', size) / 1024 / 1024, 1)} MiB"


def say_worklist(root: Path, *, as_json: bool) -> Said:
    """`archive worklist`: the fetch work list by class and by host."""
    summary = worklist.summarise(root)
    if as_json:
        return Said(js_indented(summary))
    rest = len(cast("JsonObject", summary["by_host"])) - HOSTS_SHOWN
    lines = [
        f"sources                 {summary['sources']}",
        f"with a fetchable URL    {summary['with_url']}",
        f"DOI only, no URL        {summary['doi_only']}",
        f"neither URL nor DOI     {summary['neither']}",
        (
            f"distinct URLs           {summary['distinct_urls']} "
            f"({summary['shared_urls']} carried by more than one source)"
        ),
        f"carrying a DOI          {summary['with_doi']}",
        "",
        "source class",
        *_counted(summary["by_class"]),
        "",
        "host",
        *_counted(summary["by_host"], HOSTS_SHOWN),
        *([f"  {str(rest).rjust(5)}  further hosts"] if rest > 0 else []),
    ]
    return Said("\n".join(lines))


def say_verify(root: Path) -> Said:
    """`archive verify`: every cached object against the manifest, and 1 if anything is amiss."""
    found = archive.verify_objects(root)
    amiss = any(found[name] for name in ("missing", "corrupt", "unlisted", "problems"))
    return Said(js_indented(found), int(amiss))


def say_status(root: Path, *, as_json: bool) -> Said:
    """`archive status`: what the archive holds of the work list."""
    summary = worklist.status(root)
    if as_json:
        return Said(js_indented(summary))
    lines = [
        f"sources            {summary['sources']} ({summary['with_url']} with a fetchable URL)",
        f"stored             {summary['stored']}",
        f"bytes missing      {summary['stored_but_bytes_missing']}",
        f"not the artifact   {summary['not_artifact']}",
        *_counted(summary["not_artifact_by_reason"]),
        f"failed             {summary['failed']}",
        f"no attempt yet     {summary['no_attempt_yet']}",
        *_counted(summary["failures_by_reason"]),
        f"manifest rows      {summary['manifest_rows']}",
        f"objects in cache   {summary['objects_in_cache']}",
        (
            f"bytes held         {_mib(summary['expanded_bytes'])} expanded, "
            f"{_mib(summary['compressed_bytes'])} on disk"
        ),
    ]
    return Said("\n".join(lines))


def _moves(held: JsonValue, *, sources: bool) -> list[str]:
    lines = []
    for move in cast("list[JsonObject]", held):
        line = f"  {move['sha256']} {move['from']} to {move['to']}, store {move['store']}"
        named = " ".join(cast("list[str]", move["source_ids"]))
        lines.append(f"{line}, sources {named}" if sources else line)
    return lines


def say_reclassify(root: Path, *, dry_run: bool, as_json: bool, quiet: bool) -> Said:
    """`archive reclassify`: the plan, written unless it is a dry run or holds a takedown."""
    logged: list[str] = []
    summary = reclassify.reclassify_objects(root, dry_run=dry_run, log=logged.append)
    err = () if quiet else tuple(logged)
    status = int(bool(summary["restrictive"]))
    if as_json:
        return Said(js_indented(summary), status, err)
    tightening, restrictive = summary["tightening"], summary["restrictive"]
    lines = [
        "dry run: no manifest row was written" if dry_run else "reclassify",
        f"manifest rows         {summary['manifest_rows']}",
        f"skipped, t5-no-store  {summary['skipped_removed']}",
        f"no evidence, unmoved  {summary['unevidenced']}",
        "tier before",
        *_counted(summary["before"]),
        "tier after",
        *_counted(summary["after"]),
        f"publishable before    {summary['publishable_before']}",
        f"publishable after     {summary['publishable_after']}",
        f"rows moving           {len(cast('list[JsonValue]', summary['moves']))}",
        *_counted(summary["moves_by_tier"]),
        f"rows written          {summary['written']}",
        "",
        f"tighter, still public {len(cast('list[JsonValue]', tightening))}",
    ]
    if tightening:
        lines += [
            "Not a takedown and not refused. The bytes may already be a release asset carrying the",
            "old tier, so the tags want rereading after this run.",
            *_moves(tightening, sources=False),
        ]
    lines += ["", f"public to restricted  {len(cast('list[JsonValue]', restrictive))}"]
    if restrictive:
        lines += [
            "These rows are a takedown, not a reclassification. Nothing was written. Their bytes may",
            "already be a release asset, so each one goes down the takedown path in",
            "docs/source-preservation.md, which records who asked and why.",
            *_moves(restrictive, sources=True),
        ]
    return Said("\n".join(lines), status, err)


def say_triage(root: Path, date: str, *, out: Path | None, to_stdout: bool) -> Said:
    """`licence triage`: the queue, written to the reports folder, to `out`, or printed."""
    rows = licence.build_triage(root)
    body = licence.render_triage(rows, date)
    if to_stdout:
        return Said(body.removesuffix("\n"))
    target = root / REPORT_DIR / f"licence-triage-{date}.tsv" if out is None else out
    write_text(target, body)
    return Said("\n".join([f"{target}: {len(rows)} sources", *licence.reason_counts(rows)]))


def say_counts(root: Path) -> Said:
    """`licence counts`: the measured licence numbers."""
    return Said(licence.counts_report(root).removesuffix("\n"))


def _sorted_counted(held: JsonValue) -> list[str]:
    counts = cast("Mapping[str, int]", held)
    pairs = sorted(counts.items(), key=lambda pair: utf16_key(f"{pair[0]},{pair[1]}"))
    return [f"  {str(count).rjust(5)}  {name}" for name, count in pairs]


def say_fetch(
    root: Path,
    options: archivefetch.FetchOptions | None = None,
    *,
    quiet: bool = False,
) -> Said:
    """`archive fetch`: fetch missing sources through routes and store artifacts."""
    given = archivefetch.FetchOptions() if options is None else options
    logged: list[str] = []
    classify_fn = licence.licence_classifier(root) if given.classify is None else given.classify
    log_fn = given.log if given.log is not None else (None if quiet else logged.append)
    effective = archivefetch.FetchOptions(
        concurrency=given.concurrency,
        delay_ms=given.delay_ms,
        max_bytes=given.max_bytes,
        request_timeout_ms=given.request_timeout_ms,
        host_backoff_limit=given.host_backoff_limit,
        limit=given.limit,
        retry_failed=given.retry_failed,
        dry_run=given.dry_run,
        classify=classify_fn,
        pdftotext=given.pdftotext,
        unpaywall_email=given.unpaywall_email,
        transport=given.transport,
        github_token=given.github_token,
        env=given.env,
        now_fn=given.now_fn,
        clock_ms=given.clock_ms,
        sleep_ms=given.sleep_ms,
        log=log_fn,
    )
    summary = archivefetch.run_fetch(root, effective)
    return Said(js_indented(summary), 0, () if quiet else tuple(logged))


def say_publish(  # noqa: PLR0913 - mirrors scripts/archive/cli.ts publish flags directly
    root: Path,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    mark: bool = True,
    as_json: bool = False,
    quiet: bool = False,
    client: archivepublish.ReleaseClient | None = None,
) -> Said:
    """`archive publish`: upload public objects to release shard tags and mark manifest rows."""
    logged: list[str] = []
    summary = archivepublish.publish_objects(
        root,
        limit=limit,
        dry_run=dry_run,
        mark=mark,
        client=client,
        log=None if quiet else logged.append,
    )
    err = () if quiet else tuple(logged)
    failures = cast("list[JsonObject]", summary["failed"])
    status = int(bool(failures))
    if as_json:
        return Said(js_indented(summary), status, err)
    tags_present = cast("list[str]", summary["tags_present"])
    tags_created = cast("list[str]", summary["tags_created"])
    urls = cast("list[str]", summary["urls"])
    lines = [
        "dry run: nothing was created or uploaded" if summary["dry_run"] else "publish",
        f"manifest rows         {summary['manifest_rows']}",
        f"public rows           {summary['public_rows']}",
        *_sorted_counted(summary["public_by_tier"]),
        (
            f"public bytes          {_mib(summary['public_bytes'])} expanded, "
            f"{_mib(summary['public_compressed_bytes'])} on disk"
        ),
        f"refused, not public   {summary['refused']}",
        *_sorted_counted(summary["refused_by_tier"]),
        f"public, no bytes held {summary['public_not_in_cache']}",
        f"considered            {summary['considered']}",
        f"already an asset      {summary['already_present']}",
        (
            f"would upload          {len(urls)}"
            if summary["dry_run"]
            else f"uploaded              {summary['uploaded']}"
        ),
        f"tags that exist       {', '.join(tags_present) or 'none'}",
        (
            f"tags to create        {', '.join(tags_created) or 'none'}"
            if summary["dry_run"]
            else f"tags created          {', '.join(tags_created) or 'none'}"
        ),
        f"manifest rows marked  {summary['marked']}",
    ]
    if failures:
        lines.append(f"failed                {len(failures)}")
        lines.extend(f"  {failure['key']} {failure['message']}" for failure in failures[:10])
    return Said("\n".join(lines), status, err)


def say_upload(
    root: Path,
    *,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    fetch: r2.R2Transport | None = None,
) -> Said:
    """`archive upload`: put cached non-removed objects into Cloudflare R2."""
    return Said(js_indented(r2.upload_objects(root, dry_run=dry_run, env=env, fetch=fetch)))


def _read_sources_file(path: Path) -> list[str]:
    text = path.read_bytes().decode("utf-8")
    return [line.strip() for line in text.split("\n") if line.strip() and not line.strip().startswith("#")]


def say_pull(  # noqa: PLR0913 - mirrors scripts/archive/cli.ts pull flags directly
    root: Path,
    *,
    source: str = "r2",
    keys: Sequence[str] = (),
    sources: Sequence[str] = (),
    sources_from: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    as_json: bool = False,
    quiet: bool = False,
    env: Mapping[str, str] | None = None,
    fetch: r2.R2Transport | None = None,
) -> Said:
    """`archive pull`: download objects into the local cache from R2 or public releases."""
    if source not in {"r2", "release"}:
        raise archive.ArchiveError(f"--from takes r2 or release, not {source}")
    from_file = _read_sources_file(sources_from) if sources_from is not None else []
    merged_sources = list(dict.fromkeys([*sources, *from_file]))
    logged: list[str] = []
    summary = r2.pull_objects(
        root,
        source=source,
        keys=keys,
        sources=merged_sources,
        limit=limit,
        dry_run=dry_run,
        env=env,
        fetch=fetch,
        log=None if quiet else logged.append,
    )
    err = () if quiet else tuple(logged)
    failures = cast("list[JsonObject]", summary["failed"])
    status = int(bool(failures))
    if as_json:
        return Said(js_indented(summary), status, err)
    selected = cast("int", summary["selected"])
    would_fetch = (
        selected
        - cast("int", summary["already_held"])
        - cast("int", summary["held_back_by_limit"])
        - cast("int", summary["already_in_the_bucket"])
        - cast("int", summary["no_remote_copy"])
    )
    lines = [
        "dry run: nothing was fetched or written" if summary["dry_run"] else "pull",
        f"manifest rows         {summary['manifest_rows']}",
        f"matched the filter    {summary['matched']}",
        f"refused, t5-no-store  {summary['removed']}",
        (
            f"selected              {selected} ({summary['selected_restricted']} restricted, "
            f"{summary['selected_public']} public)"
        ),
        f"already held          {summary['already_held']}",
    ]
    if source == "release":
        lines.extend(
            [
                f"no release, already in R2 {summary['already_in_the_bucket']}",
                f"no release, no remote copy {summary['no_remote_copy']}",
            ]
        )
    lines.extend(
        [
            f"held back by --limit  {summary['held_back_by_limit']}",
            (
                f"would fetch           {would_fetch}"
                if summary["dry_run"]
                else f"downloaded            {summary['downloaded']}"
            ),
        ]
    )
    if not summary["dry_run"]:
        lines.append(
            f"bytes written         {_mib(summary['expanded_bytes'])} expanded, "
            f"{_mib(summary['compressed_bytes'])} on disk"
        )
    if failures:
        lines.append(f"failed                {len(failures)}")
        for failure in failures[:10]:
            code = "no status" if failure["status"] is None else failure["status"]
            lines.append(f"  {failure['key']} {code} {failure['message']}")
    return Said("\n".join(lines), status, err)


def _pad(value: object) -> str:
    return str(value).rjust(5)


def _format_resolve_summary(counts: Mapping[str, JsonValue], result: licenceresolve.RunResult) -> list[str]:
    by_status = cast("dict[str, int]", counts["by_status"])
    by_route = cast("dict[str, JsonObject]", counts["by_route"])
    lines = [
        f"sources                        {_pad(counts['sources'])}",
        f"routes applied to              {_pad(counts['attempted'])}",
        f"candidates                     {_pad(counts['candidates'])}",
        *[f"  {status.ljust(28)} {_pad(by_status[status])}" for status in licence.CANDIDATE_STATUSES],
        f"leaves t4-private              {_pad(counts['leaves_t4'])}",
        f"leaves t4-private if proposed  {_pad(counts['leaves_t4_if_proposed'])}",
        "",
        "per route: asked, stated a licence",
    ]
    for route in licence.RESOLVE_ROUTES:
        entry = by_route[route]
        if cast("int", entry["sources"]):
            lines.append(f"  {route.ljust(14)} {_pad(entry['sources'])} {_pad(entry['licence'])}")
    if result.stopped_hosts:
        lines.append(f"hosts that stopped answering: {', '.join(result.stopped_hosts)}")
    return lines


def say_licence_resolve(
    root: Path,
    options: licenceresolve.ResolveOptions | None = None,
    *,
    dry_run: bool = False,
    quiet: bool = False,
    proposed_keys: Mapping[str, str] | None = None,
) -> Said:
    """`licence resolve`: resolve source licences across services and report summary."""
    given = licenceresolve.ResolveOptions() if options is None else options
    for route in given.routes or ():
        if route not in licence.RESOLVE_ROUTES:
            raise archive.ArchiveError(f"--route must be one of {', '.join(licence.RESOLVE_ROUTES)}")
    logged: list[str] = []
    effective = licenceresolve.ResolveOptions(
        date=given.date,
        transport=given.transport,
        github_token=given.github_token,
        crossref_contact=given.crossref_contact,
        cache_dir=given.cache_dir,
        delay_ms=given.delay_ms,
        host_delay_ms=given.host_delay_ms,
        only=given.only,
        limit=given.limit,
        routes=given.routes,
        batch_size=given.batch_size,
        log=given.log if given.log is not None else (None if quiet else logged.append),
        now_fn=given.now_fn,
        sleep_ms=given.sleep_ms,
    )
    result = licenceresolve.run_resolve(root, effective)
    held = licence.read_licence_map(root)
    counts = licenceresolve.count_run(result, held, proposed_keys=proposed_keys)
    written_lines = [] if dry_run else licenceresolve.write_resolve_outputs(root, result, counts)
    out_lines = [*written_lines, *_format_resolve_summary(counts, result)]
    err_lines = [] if quiet else logged
    by_status = cast("dict[str, int]", counts["by_status"])
    loud = by_status["unmapped"] + by_status["conflict"]
    if loud:
        unmapped_msg = (
            f"{by_status['unmapped']} sources served a licence string data/archive/licence-map.tsv "
            f"has no row for, and {by_status['conflict']} carry two services that disagree."
        )
        err_lines.extend(
            [
                "",
                unmapped_msg,
                "Add a reviewed row for each string, or settle the conflict, then run this again.",
            ]
        )
    return Said("\n".join(out_lines), 1 if loud else 0, tuple(err_lines))
