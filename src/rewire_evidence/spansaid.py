"""What the span commands and the layer reading commands print.

This is the rest of `scripts/omics/spans/cli.ts`, `npm run spans -- measure`, `retag`,
`control`, `value-check` and `show`, and the `coverage`, `surface` and `show` commands of
`scripts/omics/layers/cli.ts`.

Each function returns what the TypeScript's command writes to standard output, the lines it
writes to standard error, and its exit status. `rewire_evidence.cli` does the writing.

`measure` is the only one that writes to the checkout, and `retag` the only other one that
writes at all. `measure` writes five tables under the date of the run and, where they go to
the checkout's own reports folder, the figures file and the records beside it. It refuses to
write anything when an input moved while it was measuring.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from rewire_evidence import figures, registry
from rewire_evidence.archivesaid import Said
from rewire_evidence.canonical import JsonObject, js_key_order, js_number, js_quote, utf16_key
from rewire_evidence.js import fixed, number_of_text, paired, precision
from rewire_evidence.jsonl import StoreError, write_text
from rewire_evidence.layercoverage import COVERAGE_BUCKETS, CoverageResult, coverage_of
from rewire_evidence.layerreaders import LayerStore
from rewire_evidence.offsets import to_units
from rewire_evidence.resolution import Refusal
from rewire_evidence.spanmeasure import (
    ONE_NUMBER,
    REPORTS,
    MeasureReport,
    Work,
    bucket_table,
    class_table,
    decimals_written,
    detail_rows,
    figures_of,
    flatten_value,
    grid_table,
    kind_table,
    legacy_figures_of,
    measure,
    pct,
    reason_table,
    receipt_facts,
    source_ids,
    value_presence,
    wrong_commits,
)

#: The documents that print the figures, and so the ones `retag` rewrites.
FIGURE_DOCUMENTS: Final = (
    "maintenance/reports/span-resolution-2026-11-18.md",
    "docs/adr/0002-claim-extraction.html",
)
COMMAND: Final = "npm run spans -- measure"
BATCHES: Final = "data/omics"
VALUE_CHECK: Final = "value-check.tsv"
_USABLE: Final = frozenset({"layer_pass", "layer_suspect"})
_KEY: Final = 64
_SHOWN: Final = 300
#: A value-check row has an ID, a status, a value and a locator.
_VALUE_CHECK_FIELDS: Final = 4
#: The digits a double is written with so that no two doubles are written alike.
_FULL_PRECISION: Final = 17
#: A record that writes this many decimal places or fewer leaves a double room to tell two values apart.
_COARSE: Final = 15
#: The numbers tried against each pair: four perturbations, each spelled two ways.
_PERTURBATIONS: Final = 8


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _same_folder(one: Path, other: Path) -> bool:
    """Whether two paths name one folder, as `path.resolve` compares them: by their text, not their links."""
    return os.path.abspath(one) == os.path.abspath(other)  # noqa: PTH100 - Path.resolve follows links


#: The five tables, by the end of the name each is written under.
TABLES: Final = (
    ("by-class", class_table),
    ("by-reason", reason_table),
    ("by-kind", kind_table),
    ("by-parse-bucket", bucket_table),
    ("by-grid-route", grid_table),
)


@dataclass
class MeasureRun:
    """One run of `spans measure`: both arms, what moved under it, and what it would write."""

    report: MeasureReport
    #: The other arm of the quote rule, where the run is the shipped one and so has a pair.
    legacy: MeasureReport | None
    #: The inputs as they stood when the run opened them, and the ones that changed under it.
    inputs: list[figures.FigureInput]
    moved: list[figures.Changed]
    date: str
    err: list[str] = field(default_factory=list)

    def tables(self) -> dict[str, str]:
        """The five tables as files, by the end of each name."""
        return {name: f"{table(self.report)}\n" for name, table in TABLES}

    def figure_file(self) -> figures.FigureFile:
        """The figures file of the run, with the records each data input was measured over."""
        return figures.FigureFile(
            COMMAND,
            self.date,
            FIGURE_DOCUMENTS,
            tuple(self.inputs),
            {**figures_of(self.report), **legacy_figures_of(self.legacy)},
        )

    def said(self, seconds: float) -> str:
        """What the command prints, given how long it took."""
        report = self.report
        said = []
        if self.legacy is None and not self.moved:
            said.append("one arm of a pair: nothing written, because these figures are not the tree's\n")
        said.append("".join(f"{table(report)}\n\n" for _, table in TABLES))
        unreadable = f", {report.grids_unreadable} unreadable" if report.grids_unreadable else ""
        said.append(
            f"locators {report.locators}, resolved {report.resolved} "
            f"({pct(report.resolved, report.locators)}%), "
            f"span prints the stored value and nothing else {report.value_exact} "
            f"({pct(report.value_exact, report.resolved)}% of resolved), "
            f"span prints it somewhere {report.value_present} "
            f"({pct(report.value_present, report.resolved)}%, "
            f"the looser check), span is part of the value {report.value_partial} "
            f"({pct(report.value_partial, report.resolved)}%)\n"
            f"committed to text that does not print the stored value: {wrong_commits(report)}; "
            f"committed to more text than the stored value: {report.value_present - report.value_exact}; "
            f"span right and the value in an image: {report.value_in_image}\n"
            f"grids read {report.grids_read}{unreadable}\n"
            f"layers read {report.layers_read}, of which {report.layers_astral} hold a code point above the "
            f"basic multilingual plane ({report.astral_points} in all); {report.later_layer} locators "
            "resolved on a layer after the first\n"
        )
        legacy = self.legacy
        if legacy:
            said.append(
                f"the quote rule as a pair, legacy then shipped: resolved {legacy.resolved} then "
                f"{report.resolved}; prints the value somewhere {legacy.value_present} then "
                f"{report.value_present}; prints it and nothing else {legacy.value_exact} then "
                f"{report.value_exact}; commits to text that does not print it {wrong_commits(legacy)} then "
                f"{wrong_commits(report)}; quote spans {legacy.by_kind.get('quote', 0)} then "
                f"{report.by_kind.get('quote', 0)}\n"
            )
        said.append(f"{fixed(seconds, 0)} s")
        return "".join(said)


def run_measure(  # noqa: PLR0913 - the options are the command's flags
    root: Path,
    date: str,
    *,
    grid: bool = True,
    sheet: bool = True,
    legacy_quote: bool = False,
    objects: Path | None = None,
) -> MeasureRun:
    """Measure as `spans measure` does, and write nothing.

    The shipped run is measured as a pair: the legacy arm of the quote rule in the same
    process on the same tree, so the rule's effect is two runs of one program and the figures
    file holds both sides. `--no-grid`, `--no-sheet` and `--legacy-quote` are each one arm of
    another pair and have none.
    """
    err: list[str] = []
    opened_with = figures.input_files(root)
    report = measure(root, grid=grid, sheet=sheet, legacy_quote=legacy_quote, log=err.append, objects=objects)
    legacy = None
    if grid and sheet and not legacy_quote:
        legacy = measure(
            root, legacy_quote=True, log=lambda line: err.append(f"legacy arm: {line}"), objects=objects
        )
    moved = figures.changed_inputs(opened_with, figures.input_files(root), root)
    return MeasureRun(report, legacy, opened_with, moved, date, err)


def say_measure(  # noqa: PLR0913 - the options are the command's flags
    root: Path,
    out: Path | None,
    date: str,
    *,
    grid: bool = True,
    sheet: bool = True,
    legacy_quote: bool = False,
    detail: Path | None = None,
    seconds: Callable[[], float] = lambda: 0.0,
) -> Said:
    """`spans measure`: resolve every stored locator, print the tables and write them.

    `date` names the tables and is written into the figures file. `detail`, which the
    TypeScript takes from `SPAN_DETAIL`, is a file to write one line for each result to.
    `seconds` is a clock, read before and after for the last line printed.
    """
    started = seconds()
    reports = root / REPORTS
    target = reports if out is None else out
    run = run_measure(root, date, grid=grid, sheet=sheet, legacy_quote=legacy_quote)
    err = list(run.err)
    if run.moved:
        err.append(
            f"the tree moved while this run was measuring it, so nothing was written. {len(run.moved)} input "
            f"{_plural(len(run.moved), 'file', 'files')} changed:\n"
            + "\n".join(f"  {held.path}" for held in run.moved)
            + "\nthe figures this run holds are of a tree that no longer exists. run it again."
        )
    else:
        if run.legacy is not None:
            for name, text in run.tables().items():
                write_text(target / f"span-resolution-{date}-{name}.tsv", text)
            if _same_folder(target, reports):
                figures.write_figures(root, run.figure_file())
        if detail is not None:
            write_text(detail, f"{detail_rows(run.report)}\n")
    return Said(run.said(seconds() - started), int(bool(run.moved)), tuple(err))


def say_retag(root: Path) -> Said:
    """`spans retag`: rewrite each tagged figure in the documents to its measured value."""
    held = figures.read_figures(root)
    lines = []
    for document in held.documents:
        file = root / document
        if not file.exists():
            lines.append(f"{document}: not in this tree")
            continue
        before = file.read_bytes().decode("utf-8", "replace")
        text, changed, unknown = figures.retag(before, held.figures)
        if text != before:
            write_text(file, text)
        was = ", ".join(f"{mark.figure} was {mark.printed}" for mark in changed)
        missing = (
            f"; {len(unknown)} tagged {_plural(len(unknown), 'figure', 'figures')} {figures.SPAN_FIGURES} "
            f"does not hold: {', '.join(mark.figure for mark in unknown)}"
            if unknown
            else ""
        )
        lines.append(
            f"{document}: {len(changed)} {_plural(len(changed), 'figure', 'figures')} rewritten"
            f"{f' ({was})' if changed else ''}{missing}"
        )
    return Said("\n".join(lines))


def _span_outcome(item: Work) -> str:
    if item.resolved is None:
        return item.refusal.reason if item.refusal else "none"
    if item.value_present:
        return "span_with_value"
    return "span_inside_value" if item.value_partial else "span_without_value"


def say_control(root: Path) -> Said:
    """`spans control`: the span outcomes of the results the committed value-check files name.

    The value-check files are the results of a human-authored extractor run against the
    source, so they are the nearest thing the store has to ground truth. The files are read
    in the order of their folders' names. Node reads them in the order the directory lists
    them, which differs only where two files name one result with two statuses.
    """
    folder = root / BATCHES
    files = [
        folder / name / VALUE_CHECK
        for name in sorted((held.name for held in folder.iterdir()), key=utf16_key)
        if (folder / name / VALUE_CHECK).exists()
    ]
    wanted: dict[str, str] = {}
    for file in files:
        for line in file.read_bytes().decode("utf-8", "replace").split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) >= _VALUE_CHECK_FIELDS:
                wanted[parts[0]] = parts[1]
    err: list[str] = []
    report = measure(root, log=err.append)
    counts: dict[str, int] = {}
    matched = 0
    for item in report.work:
        status = wanted.get(item.result.id)
        if not status:
            continue
        matched += 1
        key = f"{status}\t{_span_outcome(item)}"
        counts[key] = counts.get(key, 0) + 1
    lines = [
        f"value-check rows found in the store: {matched} of {len(wanted)} over {len(files)} files",
        "",
        "value_check_status\tspan_outcome\trows",
        *(f"{key}\t{count}" for key, count in sorted(counts.items(), key=lambda held: -held[1])),
    ]
    return Said("\n".join(lines), 0, tuple(err))


def _string_only_presence(exact: str, printed: str) -> bool:
    """The value check as it stood before it compared numbers: the stored value's characters in the span."""
    return bool(printed) and flatten_value(printed) in flatten_value(exact)


def _wrong_numbers(span: str, value: str, places: int) -> list[tuple[str, bool]]:
    """A different number at the record's precision, four ways, each as a short and as a full double.

    With each is whether it is further than one unit of the last place from the stored value.
    A perturbation that still rounds to the stored value is left out: it is the same number at
    the precision the record wrote.
    """
    # Node gives the nearest double for each of these, which is not the reciprocal of ten to the power.
    unit = float(f"1e-{places}")
    base = number_of_text(span)
    ours = number_of_text(value)
    stored = fixed(ours, places)
    out: list[tuple[str, bool]] = []
    for candidate in (base + unit, base - unit, base + 10 * unit, base * 1.1):
        if math.isinf(candidate) or math.isnan(candidate) or fixed(candidate, places) == stored:
            continue
        far = abs(candidate - ours) > unit
        out += [(js_number(candidate), far), (precision(candidate, _FULL_PRECISION), far)]
    return out


def _rate(part: int, whole: int) -> str:
    return "0.0000" if whole == 0 else fixed(part / whole * 100, 4)


def say_value_check(root: Path) -> Said:
    """`spans value-check`: what the numeric value check accepts, measured on the store's own spans.

    The check sits between a resolver and every wrong-answer figure this lane reports, so its
    own error is measured: how often it accepts a different number at the record's precision,
    and how often it accepts a span holding several numbers.
    """
    err: list[str] = []
    report = measure(root, log=err.append)
    resolved = [
        (item, item.resolved.kind, item.resolved.span.exact, to_units(item.result.printed_value))
        for item in report.work
        if item.resolved is not None
    ]
    pairs = [
        (span, value, decimals_written(value))
        for span, value in (
            (flatten_value(exact), flatten_value(printed)) for _, _, exact, printed in resolved
        )
        if ONE_NUMBER.fullmatch(span) and ONE_NUMBER.fullmatch(value)
    ]
    accepted = [pair for pair in pairs if value_presence(pair[0], pair[1]).present]

    wrong_tried = wrong_by_number = wrong_by_string = wrong_excluded = 0
    coarse_tried = coarse_by_number = far_tried = far_by_number = 0
    several_tried = several_accepted = several_excluded = 0
    for span, value, places in accepted:
        wrongs = _wrong_numbers(span, value, places)
        wrong_excluded += _PERTURBATIONS - len(wrongs)
        for text, far in wrongs:
            wrong_tried += 1
            coarse_tried += places <= _COARSE
            far_tried += far
            if not value_presence(text, value).present:
                continue
            if _string_only_presence(text, value):
                wrong_by_string += 1
            else:
                wrong_by_number += 1
                coarse_by_number += places <= _COARSE
                far_by_number += far
        other = wrongs[0][0] if wrongs else "0"
        for text in (f"{span}\t{other}", f"{span} ({other})", f"{other}\t{span}"):
            if value in flatten_value(text):
                several_excluded += 1
                continue
            several_tried += 1
            several_accepted += value_presence(text, value).present

    sheet = [held for held in resolved if held[1] == "sheet_cell"]
    sheet_by_string = sum(1 for _, _, exact, printed in sheet if _string_only_presence(exact, printed))
    flipped = [
        held
        for held in resolved
        if held[0].value_present is True and not _string_only_presence(held[2], held[3])
    ]
    by_kind: dict[str, int] = {}
    for _, kind, _, _ in flipped:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    was_partial = sum(1 for _, _, exact, printed in flipped if flatten_value(exact) in flatten_value(printed))
    kinds = ", ".join(f"{kind} {count}" for kind, count in sorted(by_kind.items(), key=lambda held: -held[1]))
    no_part = sum(
        1
        for item, _, _, _ in sheet
        if item.value_present is not True
        and item.value_partial is not True
        and item.value_in_image is not True
    )
    lines = [
        (
            f"resolved spans {len(resolved)}; number pairs {len(pairs)}, of which the rule accepts "
            f"{len(accepted)}"
        ),
        (
            f"spans the numeric rule alone finds the value in: {len(flipped)} ({kinds}), of which the "
            f"character test called {was_partial} part of the value and {len(flipped) - was_partial} "
            "no part of it"
        ),
        (
            f"a different number at the record's precision: tried {wrong_tried}, accepted by the "
            f"numeric rule {wrong_by_number} ({_rate(wrong_by_number, wrong_tried)}%), accepted by the "
            f"character test {wrong_by_string} ({_rate(wrong_by_string, wrong_tried)}%), excluded as "
            f"finer than the record's precision {wrong_excluded}"
        ),
        (
            f"  over the records writing 15 decimal places or fewer: tried {coarse_tried}, accepted by "
            f"the numeric rule {coarse_by_number}"
        ),
        (
            f"  over the perturbations further than one unit of the record's last place: tried {far_tried}, "
            f"accepted by the numeric rule {far_by_number}"
        ),
        (
            f"several numbers, one of them rounding to the stored value: tried {several_tried}, accepted "
            f"{several_accepted} ({_rate(several_accepted, several_tried)}%), excluded as printing the value "
            f"outright {several_excluded}"
        ),
        (
            f"sheet_cell spans {len(sheet)}: print the stored value "
            f"{sum(1 for item, _, _, _ in sheet if item.value_present is True)}, of which the character test "
            f"alone sees {sheet_by_string}; print no part of it {no_part}"
        ),
    ]
    return Said("\n".join(lines), 0, tuple(err))


def _json(text: str | None) -> str:
    return "null" if text is None else js_quote(paired(text))


def say_show(root: Path, result_id: str) -> Said:
    """`spans show`: what the measurement decided about one result."""
    report: MeasureReport = measure(root)
    item = next((held for held in report.work if held.result.id == result_id), None)
    if item is None:
        return Said(f"no result with id {result_id}")
    layers = ", ".join(f"{held.normaliser} {held.layer_sha256[:12]}" for held in item.candidates)
    lines = [
        item.result.id,
        f"  locator  {item.result.locator}",
        f"  printed  {item.result.printed_value}",
        f"  class    {item.locator_class}, parse bucket {item.parse_bucket}, verdict {item.parsed.verdict}",
        f"  layers   {layers or 'none'}",
    ]
    resolved = item.resolved
    if resolved is None:
        # A result that did not resolve was refused on a layer, or had no layer to be tried on.
        refusal = cast("Refusal", item.refusal)
        lines.append(f"  refused  {refusal.reason}: {paired(refusal.detail)}")
    else:
        span = resolved.span
        lines += [
            (
                f"  span     {resolved.kind} [{span.start}, {span.end}) code points, "
                f"[{span.byte_start}, {span.byte_end}) bytes"
            ),
            f"  exact    {_json(span.exact)}",
            f"  crossed  row {_json(resolved.row_label)}, column {_json(resolved.column_header)}",
        ]
    return Said("\n".join(lines))


# ---- the layer commands that read ------------------------------------------------------


def _coverage(root: Path) -> CoverageResult:
    return coverage_of(
        source_ids(root), receipt_facts(root), registry.read_registry(root), registry.read_unsupported(root)
    )


def say_coverage(root: Path) -> Said:
    """`layers coverage`: sources by what the archive holds of each, and the characters behind them."""
    result = _coverage(root)
    lines = ["bucket             sources   characters"]
    for bucket in COVERAGE_BUCKETS:
        characters = sum(row.characters for row in result.rows if row.bucket == bucket)
        lines.append(
            f"{bucket.ljust(18)}{str(result.buckets[bucket]).rjust(7)}   {f'{characters:,}'.rjust(12)}"
        )
    total = sum(result.buckets.values())
    lines += [
        f"{'total'.ljust(18)}{str(total).rjust(7)}   {f'{result.characters:,}'.rjust(12)}",
        (
            "\nthe character column counts a shared layer once per source. Counted once, it is "
            f"{result.distinct_characters:,} over {result.distinct_layers} layers."
        ),
        (
            f"\nidentity: {' + '.join(str(result.buckets[bucket]) for bucket in COVERAGE_BUCKETS)} = "
            f"{total}, sources {result.sources}"
        ),
    ]
    return Said("\n".join(lines), int(bool(result.problems)), tuple(result.problems))


def _work_for(bucket: str, detail: str) -> str:
    """The work that would make a source extractable, from why it is not."""
    if bucket == "no_normaliser":
        if "did not expand" in detail:
            return "a repair of the stored bytes, because the office package will not open"
        if "office presentation package" in detail:
            return "a slide normaliser, which nothing in the store needs yet"
        if "opendocument package" in detail:
            return "an opendocument normaliser, which nothing in the store needs yet"
        if "office package" in detail:
            return "a main part this office package does not declare, so no normaliser can claim it"
        if "legacy office binary" in detail:
            return "a reader for the pre-2007 office binary formats"
        if "raster image" in detail:
            return "optical character recognition, which no layer makes verifiable"
        return "a normaliser for a format nothing here reads"
    if bucket == "not_the_artifact":
        return (
            "a second capture route for the host that served a page instead of the source, "
            "which is the archive lane"
        )
    if bucket == "no_capture":
        return "a rescue wave against the host, which is the archive lane"
    # What is left is a layer the sanity gate failed. A usable layer is not asked about.
    return "a rebuild of the layer, because the one the store holds is unusable"


def say_surface(root: Path) -> Said:
    """`layers surface`: how much text exists, by source class, and what would make the rest readable."""
    result = _coverage(root)
    usable = [row for row in result.rows if row.bucket in _USABLE]
    lines = [
        f"sources                 {str(result.sources).rjust(8)}",
        (
            f"usable layer            {str(len(usable)).rjust(8)}  ({result.buckets['layer_pass']} pass, "
            f"{result.buckets['layer_suspect']} suspect)"
        ),
        (
            f"characters, distinct    {f'{result.distinct_characters:,}'.rjust(8)}  over "
            f"{result.distinct_layers} layers: the text that exists"
        ),
        (
            f"characters, per source  {f'{result.characters:,}'.rjust(8)}  a layer cited by two sources "
            "counted twice: the reading"
        ),
    ]
    lengths = sorted(row.characters for row in usable)
    if lengths:

        def at(share: float) -> int:
            return lengths[min(len(lengths) - 1, math.floor(share * len(lengths)))]

        lines.append(
            f"characters per source   min {at(0)}, median {at(0.5)}, p95 {at(0.95)}, max {lengths[-1]}"
        )
    lines.append("\nsource class          sources   usable   characters")
    classes = [(name, result.by_class[name]) for name in js_key_order(result.by_class)]
    lines += [
        f"{name.ljust(20)}{str(held.sources).rjust(8)}{str(held.usable).rjust(9)}   "
        f"{f'{held.characters:,}'.rjust(12)}"
        for name, held in sorted(classes, key=lambda pair: -pair[1].usable)
    ]
    lines.append("\nnot extractable without new work")
    blocked = [row for row in result.rows if row.bucket not in _USABLE]
    by_work: dict[str, int] = {}
    for row in blocked:
        work = _work_for(row.bucket, row.detail)
        by_work[work] = by_work.get(work, 0) + 1
    lines += [
        f"{str(count).rjust(6)}  {work}" for work, count in sorted(by_work.items(), key=lambda held: -held[1])
    ]
    lines.append(f"{str(len(blocked)).rjust(6)}  total")
    return Said("\n".join(lines))


def say_show_layers(root: Path, named: str) -> Said:
    """`layers show`: the layers of a source or of an object, and the opening of the best one."""
    store = LayerStore(root)
    is_key = len(named) == _KEY and all(character in "0123456789abcdef" for character in named)
    records = store.layers_for_object(named) if is_key else store.layers_for_source(named)
    if not records:
        return Said(f"no layer for {named}", 1)
    lines = []
    for record in records:
        metrics = cast("JsonObject", record["metrics"])
        lines.append(
            f"{record['layer_sha256']}  {cast('str', record['sanity']).ljust(8)}"
            f"{cast('str', record['normaliser']).ljust(28)}{metrics['characters']} characters"
        )
        if record["container_path"]:
            lines.append(f"  in {record['object_sha256']} at {record['container_path']}")
        lines += [f"  {reason}" for reason in cast("list[str]", record["sanity_reasons"])]
    first = records[0]
    try:
        opening = store.layer_text(first)[:_SHOWN]
    except StoreError as error:
        # The TypeScript has printed the list by the time it throws, and leaves with a stack trace.
        return Said("\n".join(lines), 1, (str(error),))
    lines += [f"\nfirst 300 characters of {first['layer_sha256']}:", opening]
    return Said("\n".join(lines))
