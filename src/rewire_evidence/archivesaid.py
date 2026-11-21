"""What the archive and licence commands print: `npm run archive --` and the licence scripts.

Each function returns what the TypeScript's command writes to standard output, the lines it
writes to standard error, and its exit status. `rewire_evidence.cli` does the writing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from rewire_evidence import archive, licence, reclassify, worklist
from rewire_evidence.canonical import JsonObject, JsonValue, js_indented, js_key_order
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
