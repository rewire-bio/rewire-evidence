"""Hold span resolution to what the TypeScript measured of every stored result.

The data repository commits five tables of totals and a file of figures. Totals cannot show
that two resolvers agree on which span each result has, so the oracle is a recording:
`scripts/fixtures/record_spans_store.mjs` runs the TypeScript's own measurement over the
pinned store and writes one line for each result, with the layer and both ranges of its span
or the reason it was refused, and everything else the measurement decided about it.

The measurement reads the stored objects, and 296 of the 1,167 are restricted. So there are
two recordings, made by one script.

- The full view was recorded once on a machine that holds every object, and its lines are
  committed. They hold no text of any layer: each text is a digest.
- The public view is recorded in CI on every run, over the objects a public release
  mirrors, and its files are held by digest.

The gate measures with this library over the objects it is given and requires three things.

1. Every file the measurement writes is the TypeScript's for the same objects, byte for
   byte: the line for each result under both arms of the quote rule, the lines
   `measure-store-spans.ts` writes, the five tables, the figures file, the records beside it
   and what the command prints.
2. Each result whose layers are all derived from objects the gate holds has the line the
   full view has. For those the two views are the same measurement. The rest cannot be
   derived again without the restricted objects and are held to the recorded line, and the
   gate says how many.
3. The five committed tables and the committed figures are what the results give, each
   result taken from this library where it could be measured and from the recorded line
   where it could not.

Both files of recorded lines are held to the digest the expectation states for them before a
line is read, each line has to be one the recorder writes, and the lines have to be of the
results of the checkout in their order.

The committed figures file does not regenerate whole at the pinned commit: it names the
inputs it was measured over by digest, and the source records have gained lines since. The
gate requires the committed file and the records beside it byte for byte, with each input
that has only gained records left as the committed file has it, and says how many those are.
It requires that no record the file was measured over is gone or changed.
"""

from __future__ import annotations

import gzip
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final, cast

from rewire_evidence import figures
from rewire_evidence.canonical import JsonObject, JsonValue, js_parse, js_stringify, sha256_hex
from rewire_evidence.checkparity import Lines
from rewire_evidence.conformance import recorded_ref
from rewire_evidence.js import paired
from rewire_evidence.jsonl import StoreError
from rewire_evidence.layerreaders import LayerStore, is_grid
from rewire_evidence.offsets import Span
from rewire_evidence.pdftext import pdftotext_version
from rewire_evidence.resolution import Refusal, Resolved
from rewire_evidence.spanmeasure import (
    REPORTS,
    MeasureReport,
    Work,
    count,
    figures_of,
    legacy_figures_of,
)
from rewire_evidence.spansaid import TABLES, MeasureRun, run_measure
from rewire_evidence.textlayer import LayerFailure

#: The files a recording holds, each by the name the recorder writes it under.
ROWS: Final = "rows.jsonl"
LEGACY_ROWS: Final = "legacy-rows.jsonl"
SPANS: Final = "store-spans.jsonl"
FIGURES: Final = "figures.json"
RECORDS: Final = "figures-records.tsv"
STDOUT: Final = "stdout.txt"
#: What a recording says of itself.
SUMMARY: Final = "summary.json"
#: The counts of layers read, which no line of a result holds.
LAYER_COUNTS: Final = ("layers_read", "layers_astral", "astral_points", "grids_read")
#: Hex digits kept of the digest over the texts of an outcome.
TEXT_DIGEST: Final = 16
#: Hex digits of a layer's hash a row names a candidate by.
CANDIDATE_DIGITS: Final = 12
_SHOWN: Final = 5
#: Characters of a line that is not a recorded one that are shown.
_LINE_SHOWN: Final = 200
_NONE: Final = type(None)
#: The members of a recorded line in the order the recorder writes them, and what each holds.
ROW_MEMBERS: Final[Mapping[str, tuple[type, ...]]] = {
    "id": (str,),
    "candidates": (list,),
    "tried": (int,),
    "ok": (bool,),
    "kind": (str, _NONE),
    "reason": (str, _NONE),
    "layer": (str, _NONE),
    "start": (int, _NONE),
    "end": (int, _NONE),
    "byte_start": (int, _NONE),
    "byte_end": (int, _NONE),
    "anchor_candidates": (int, _NONE),
    "grid_route": (str, _NONE),
    "resolved_route": (str, _NONE),
    "value_present": (bool, _NONE),
    "value_exact": (bool, _NONE),
    "value_partial": (bool, _NONE),
    "value_in_image": (bool, _NONE),
    "text": (str,),
}
#: The members a line of a result that resolved always holds.
_OF_A_SPAN: Final = ("kind", "layer", "start", "end", "byte_start", "byte_end")


def _text(held: str | None) -> str | None:
    return None if held is None else paired(held)


def row_line(item: Work) -> str:
    """Everything the measurement decided about one result, with no text of any layer in it."""
    resolved = item.resolved
    refusal = item.refusal
    span = resolved.span if resolved else None
    texts: list[JsonValue] = [
        _text(span.exact) if span else None,
        _text(span.prefix) if span else None,
        _text(span.suffix) if span else None,
        _text(resolved.row_label) if resolved else None,
        _text(resolved.column_header) if resolved else None,
        _text(refusal.detail) if refusal else None,
    ]
    return js_stringify(
        {
            "id": item.result.id,
            "candidates": [held.layer_sha256[:CANDIDATE_DIGITS] for held in item.candidates],
            "tried": item.tried,
            "ok": resolved is not None,
            "kind": resolved.kind if resolved else None,
            "reason": refusal.reason if refusal else None,
            "layer": resolved.layer_sha256 if resolved else (refusal.layer_sha256 if refusal else None),
            "start": span.start if span else None,
            "end": span.end if span else None,
            "byte_start": span.byte_start if span else None,
            "byte_end": span.byte_end if span else None,
            "anchor_candidates": resolved.anchor_candidates if resolved else None,
            "grid_route": item.grid_route,
            "resolved_route": resolved.grid_route if resolved else None,
            "value_present": item.value_present,
            "value_exact": item.value_exact,
            "value_partial": item.value_partial,
            "value_in_image": item.value_in_image,
            "text": sha256_hex(js_stringify(texts))[:TEXT_DIGEST],
        }
    )


def span_line(item: Work) -> str:
    """The line `scripts/omics/extract/measure-store-spans.ts` writes for one result."""
    resolved = item.resolved
    span = resolved.span if resolved else None
    return js_stringify(
        {
            "id": item.result.id,
            "sources": list(item.result.source_ids),
            "locator": item.result.locator,
            "printed_value": item.result.printed_value,
            "ok": resolved is not None,
            "kind": resolved.kind if resolved else None,
            "reason": item.refusal.reason if item.refusal else None,
            "layer": span.layer_sha256 if span else None,
            "start": span.start if span else None,
            "end": span.end if span else None,
            "exact": _text(span.exact) if span else None,
            "value_present": item.value_present,
            "value_exact": item.value_exact,
        }
    )


def _lines(rows: Sequence[str]) -> bytes:
    return "".join(f"{row}\n" for row in rows).encode("utf-8")


def files_of(run: MeasureRun) -> dict[str, bytes]:
    """Every file a recording holds, as this library's measurement writes it."""
    rows = [row_line(item) for item in run.report.work]
    legacy = [row_line(item) for item in run.legacy.work] if run.legacy else rows
    held = {
        ROWS: _lines(rows),
        LEGACY_ROWS: _lines([row for row, shipped in zip(legacy, rows, strict=True) if row != shipped]),
        SPANS: _lines([span_line(item) for item in run.report.work]),
    }
    held |= {f"{name}.tsv": text.encode("utf-8") for name, text in run.tables().items()}
    content = run.figure_file()
    held[FIGURES] = figures.figures_text(content).encode("utf-8")
    held[RECORDS] = (figures.records_text(content) or "").encode("utf-8")
    held[STDOUT] = f"{run.said(0)}\n".encode()
    return held


@dataclass
class SpanReport:
    """What the span gate found."""

    store_ref: str
    date: str
    #: `full` where every object of the manifest is held, `public` where the public ones are.
    view: str = ""
    pdftotext: str = ""
    objects: dict[str, int] = field(default_factory=dict)
    results: int = 0
    resolved: int = 0
    #: Results that could be measured here, held to the recorded line of the full view.
    rows: Lines = field(default_factory=Lines)
    legacy: Lines = field(default_factory=Lines)
    #: Results whose layers need an object that is not held, taken from the recording.
    held: int = 0
    #: Whether each file is the one the expectation names by digest.
    digests: dict[str, bool] = field(default_factory=dict)
    #: Whether each file is the one the TypeScript wrote in this run, where it ran.
    recorded: dict[str, bool] = field(default_factory=dict)
    #: Whether each committed table is what the results give.
    tables: dict[str, bool] = field(default_factory=dict)
    #: The committed figures, and how many of them the results give.
    figures: Lines = field(default_factory=Lines)
    #: Whether the committed figures file and its records are the bytes the results give.
    figures_file: bool = False
    #: Inputs of the committed figures file that have gained records since, left as committed.
    gained: int = 0
    problems: list[str] = field(default_factory=list)


def _member(recorded: Mapping[str, JsonValue], name: str) -> JsonObject:
    held = recorded.get(name)
    if not isinstance(held, dict):
        raise StoreError(f"the recorded expectation has no {name}")
    return held


def read_recorded(expected: Path, recorded: Mapping[str, JsonValue], member: str) -> str:
    """A file of recorded lines of the full view, which the expectation names beside itself.

    Raises `StoreError` for an expectation that names none and for a file that is not gzip.
    """
    named = recorded.get(member)
    if not isinstance(named, str):
        raise StoreError(f"{expected} does not name the file of recorded {member}")
    path = expected.parent / named
    try:
        return gzip.decompress(path.read_bytes()).decode("utf-8")
    except (OSError, EOFError, zlib.error, UnicodeDecodeError) as error:
        raise StoreError(f"{path} cannot be read: {error}") from error


def rows_of(text: str) -> list[str]:
    """The lines of a file of recorded lines. A line ends at a newline and nowhere else."""
    return text.split("\n")[:-1]


def read_rows(expected: Path, recorded: Mapping[str, JsonValue], member: str) -> list[str]:
    """The recorded lines of the full view. Raises as `read_recorded` does."""
    return rows_of(read_recorded(expected, recorded, member))


def recorded_row(line: str) -> JsonObject:
    """A recorded line as the recorder writes it: every member, in its order, of its kind.

    Raises `StoreError` for any other line, so a recording that was cut or edited stops the
    gate in a line and is never read as a result.
    """
    try:
        row = js_parse(line)
    except ValueError:
        row = None
    if (
        not isinstance(row, dict)
        or list(row) != list(ROW_MEMBERS)
        or any(type(row[name]) not in kinds for name, kinds in ROW_MEMBERS.items())
        or (row["ok"] and any(row[name] is None for name in _OF_A_SPAN))
    ):
        raise StoreError(f"a recorded line is not one the recorder writes: {line[:_LINE_SHOWN]}")
    return row


def _count(held: Mapping[str, JsonValue], name: str) -> int:
    value = held.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreError(f"the recorded expectation has no count of {name}")
    return value


def _summary(folder: Path) -> JsonObject:
    """What a recording says of itself. Raises `StoreError` for one that is not the recorder's."""
    try:
        held = js_parse((folder / SUMMARY).read_bytes().decode("utf-8"))
    except ValueError:
        held = None
    if not isinstance(held, dict):
        raise StoreError(f"{folder / SUMMARY} is not the summary of a recording")
    return held


def _determined(run: MeasureRun, store: LayerStore, held: set[str]) -> list[bool]:
    """For each result, whether every layer it may be tried on is derived from an object that is held.

    The measurement of such a result reads nothing that is missing, so it is the measurement
    the full view made. A layer the sanity gate failed is never read and does not count.
    """
    needs: dict[str, set[str]] = {}
    for record in store.records:
        if not is_grid(record) and record["sanity"] != "fail":
            for source in cast("list[str]", record["source_ids"]):
                needs.setdefault(source, set()).add(cast("str", record["object_sha256"]))
    return [
        all(needs.get(source, set()) <= held for source in item.result.source_ids) for item in run.report.work
    ]


def _differing(mine: str, theirs: str) -> str:
    """Which members of two lines differ, for a problem a reader can act on."""
    one, other = cast("JsonObject", js_parse(mine)), cast("JsonObject", js_parse(theirs))
    names = [name for name in other if one.get(name) != other.get(name)]
    return f"{other.get('id')}: differs in {', '.join(names)}"


def _as_recorded(item: Work, line: str) -> Work:
    """A result as the recording has it, for a result this run could not measure.

    The class and the parse are this library's, read off the locator. What was decided about
    the result is the recorded line's, and the text of its span is not there to take.
    """
    row = cast("dict[str, int | str | bool | None]", recorded_row(line))
    layer = cast("str | None", row["layer"])
    resolved = None
    if row["ok"]:
        span = Span(
            cast("str", layer),
            cast("int", row["start"]),
            cast("int", row["end"]),
            cast("int", row["byte_start"]),
            cast("int", row["byte_end"]),
            "",
        )
        resolved = Resolved(cast("str", row["kind"]), span, cast("str", layer))
    refusal = Refusal(cast("str", row["reason"]), "", layer) if row["reason"] else None
    return replace(
        item,
        candidates=[],
        tried=cast("int", row["tried"]),
        resolved=resolved,
        refusal=refusal,
        value_present=cast("bool | None", row["value_present"]),
        value_exact=cast("bool | None", row["value_exact"]),
        value_partial=cast("bool | None", row["value_partial"]),
        value_in_image=cast("bool | None", row["value_in_image"]),
        grid_route=cast("str | None", row["grid_route"]),
    )


def _hold_rows(  # noqa: PLR0913, PLR0917 - one arm of the measurement and what to hold it to
    work: Sequence[Work],
    made: Sequence[str],
    expected: Sequence[str],
    determined: Sequence[bool],
    counted: Lines,
    problems: list[str],
    arm: str,
) -> list[Work]:
    """Hold each measured line to the recorded one, and give the work with the rest taken as recorded."""
    merged = []
    for item, mine, theirs, known in zip(work, made, expected, determined, strict=True):
        if not known:
            merged.append(_as_recorded(item, theirs))
            continue
        counted.expected += 1
        counted.made += 1
        counted.same += mine == theirs
        if mine != theirs and counted.made - counted.same <= _SHOWN:
            problems.append(f"{arm} {_differing(mine, theirs)}")
        merged.append(item)
    return merged


def _report(work: list[Work], counts: Mapping[str, JsonValue]) -> MeasureReport:
    report = MeasureReport(work)
    for name in LAYER_COUNTS:
        setattr(report, name, counts[name])
    count(report)
    return report


def _hold_files(found: SpanReport, files: Mapping[str, bytes], view: Mapping[str, JsonValue]) -> None:
    """Hold each file to the digest the expectation states for this view."""
    stated = _member(view, "sha256")
    for name, data in files.items():
        found.digests[name] = sha256_hex(data) == stated.get(name)
        if not found.digests[name]:
            found.problems.append(
                f"{name} of the {found.view} view has the digest {sha256_hex(data)} "
                f"and the TypeScript's has {stated.get(name)}"
            )
    for name in stated:
        if name not in files:
            found.problems.append(
                f"the expectation states a digest for {name}, which this gate does not write"
            )


def _hold_recorded(found: SpanReport, files: Mapping[str, bytes], folder: Path) -> None:
    """Hold each file to the one the TypeScript wrote from the same checkout and the same objects."""
    if recorded_ref(_summary(folder)) != found.store_ref:
        found.problems.append(f"{folder} was recorded at another commit than {found.store_ref}")
    for name, data in files.items():
        try:
            theirs = (folder / name).read_bytes()
        except OSError as error:
            found.problems.append(f"{folder / name} cannot be read: {error}")
            found.recorded[name] = False
            continue
        found.recorded[name] = data == theirs
        if data != theirs:
            found.problems.append(f"{name} is not the file the TypeScript wrote in this run")


def _hold_committed(
    found: SpanReport, root: Path, run: MeasureRun, shipped: MeasureReport, legacy: MeasureReport
) -> None:
    """Hold the committed tables and figures to what the results give."""
    for name, table in TABLES:
        file = root / REPORTS / f"span-resolution-{found.date}-{name}.tsv"
        try:
            found.tables[name] = file.read_bytes() == f"{table(shipped)}\n".encode()
        except OSError as error:
            raise StoreError(f"{file} cannot be read: {error}") from error
        if not found.tables[name]:
            found.problems.append(f"{file.name} is not what the results give")
    committed = figures.read_figures(root)
    made = {**figures_of(shipped), **legacy_figures_of(legacy)}
    found.figures.expected = len(committed.figures)
    found.figures.made = len(made)
    found.figures.same = sum(1 for name, value in committed.figures.items() if made.get(name) == value)
    if list(made.items()) != list(committed.figures.items()):
        found.problems.append(f"{figures.SPAN_FIGURES} holds other figures than the results give")
    drifted = figures.changed_inputs(committed.inputs, figures.input_files(root), root)
    found.problems += [
        f"{figures.SPAN_FIGURES} was measured over {held.path}, which was {held.was} and is {held.now}"
        for held in drifted
    ]
    # The file the results give. An input that has gained records since the file was written
    # and lost or changed none is left as the committed file has it, and counted.
    was = {held.path: held for held in committed.inputs if held.records is not None}
    moved = {held.path for held in drifted}
    gained = {
        held.path
        for held in run.inputs
        if held.records is not None
        and held.path in was
        and held.path not in moved
        and was[held.path].sha256 != held.sha256
    }
    found.gained = len(gained)
    given = replace(
        run.figure_file(),
        inputs=tuple(was[held.path] if held.path in gained else held for held in run.inputs),
        figures=made,
    )
    records = figures.records_text(given)
    beside = root / figures.records_file_for(figures.SPAN_FIGURES)
    found.figures_file = (root / figures.SPAN_FIGURES).read_bytes() == figures.figures_text(given).encode(
        "utf-8"
    ) and (beside.read_bytes() if beside.exists() else None) == (
        None if records is None else records.encode("utf-8")
    )
    if not found.figures_file:
        found.problems.append(
            f"{figures.SPAN_FIGURES} and the records beside it are not the bytes the results give"
        )


def _view(
    found: SpanReport, recorded: Mapping[str, JsonValue], store: LayerStore
) -> tuple[set[str], JsonObject]:
    """The objects that are held, and what the expectation states for a run over exactly those."""
    keys = list(store.manifest)
    held = {key for key in keys if store.has_object(key)}
    found.objects = {"manifest": len(keys), "held": len(held)}
    for view in ("full", "public"):
        stated = _member(recorded, view)
        if stated.get("objects_held") == len(held):
            found.view = view
            return held, stated
    raise StoreError(
        f"{len(held):,} of {len(keys):,} objects are held, and the recordings are of "
        f"{_member(recorded, 'public').get('objects_held')} and of "
        f"{_member(recorded, 'full').get('objects_held')}"
    )


def _recorded_lines(expected: Path, recorded: Mapping[str, JsonValue]) -> tuple[list[str], dict[str, str]]:
    """The lines of the full view, and the lines the legacy arm decides otherwise by the result of each.

    Each file is the one the expectation names, to the byte and to the count of its lines,
    before a line of it is read. Raises `StoreError` where one is not, where a line is not
    one the recorder writes, and where a legacy line is of no result or is the second of one.
    """
    whole = _member(recorded, "full")
    stated = _member(whole, "sha256")
    read: list[list[str]] = []
    for member, name, lines in (
        ("rows", ROWS, _count(recorded, "results")),
        ("legacy_rows", LEGACY_ROWS, _count(whole, "legacy_otherwise")),
    ):
        text = read_recorded(expected, recorded, member)
        if text.count("\n") != lines or sha256_hex(text) != stated.get(name):
            raise StoreError(f"the recorded lines of {name} beside {expected} are not the ones it names")
        read.append(rows_of(text))
    full, legacy = read
    ids = {cast("str", recorded_row(line)["id"]) for line in full}
    otherwise = {cast("str", recorded_row(line)["id"]): line for line in legacy}
    if len(otherwise) != len(legacy) or not otherwise.keys() <= ids:
        raise StoreError(
            f"the recorded lines of {LEGACY_ROWS} name a result twice, or one {ROWS} does not hold"
        )
    return full, otherwise


def _hold_order(full: Sequence[str], work: Sequence[Work], root: Path) -> None:
    """Require the recorded lines to be of the results of the checkout, in their order.

    A line is joined to a result by where it stands, and a result that cannot be measured
    here is taken from its line whole, so a line of another result would be taken for it.
    """
    ids = [cast("str", cast("JsonObject", js_parse(line))["id"]) for line in full]
    measured = [item.result.id for item in work]
    if measured != ids:
        at = next((at for at, pair in enumerate(zip(measured, ids, strict=False)) if pair[0] != pair[1]), -1)
        raise StoreError(
            f"the recorded lines are of {len(ids):,} results and {root} holds {len(measured):,}"
            if at < 0
            else f"line {at + 1} of the recorded lines is of {ids[at]} and result {at + 1} of {root} "
            f"is {measured[at]}"
        )


def verify_spans(
    root: Path, objects: Path, expected: Path, recorded: JsonObject, recorded_in: Path | None = None
) -> SpanReport:
    """Measure the checkout over a folder of objects and hold the result to the recordings.

    `expected` is the file the expectation was read from, beside which the recorded lines
    are. `recorded_in` is a folder `record_spans_store.mjs` wrote from the same checkout and
    the same objects, where the TypeScript ran beside this.
    """
    date = recorded.get("date")
    if not isinstance(date, str):
        raise StoreError("the recorded expectation does not name the date it was measured as of")
    found = SpanReport(recorded_ref(recorded), date)
    layer_counts: JsonObject = {name: _count(_member(recorded, "full"), name) for name in LAYER_COUNTS}
    full, otherwise = _recorded_lines(expected, recorded)
    try:
        found.pdftotext = pdftotext_version()
    except LayerFailure as error:
        found.pdftotext = str(error)
    if found.pdftotext != recorded.get("pdftotext"):
        found.problems.append(
            f"pdftotext names itself as {found.pdftotext!r} and the layers were derived by "
            f"{recorded.get('pdftotext')!r}, so no PDF layer could be read"
        )
    store = LayerStore(root, objects)
    held, view = _view(found, recorded, store)

    run = run_measure(root, found.date, objects=objects)
    legacy_run = cast("MeasureReport", run.legacy)
    _hold_order(full, run.report.work, root)
    found.results = run.report.locators
    found.resolved = run.report.resolved
    found.problems += [f"{moved.path} changed while it was measured" for moved in run.moved]
    for name in ("results", "resolved"):
        stated = recorded["results"] if name == "results" else view.get("resolved")
        if getattr(found, name) != stated:
            found.problems.append(
                f"{getattr(found, name):,} {name} were measured and the recording has {stated}"
            )
    files = files_of(run)
    _hold_files(found, files, view)
    if recorded_in is not None:
        _hold_recorded(found, files, recorded_in)

    determined = _determined(run, store, held)
    found.held = determined.count(False)
    if found.held != view.get("held"):
        found.problems.append(
            f"{found.held:,} results need an object that is not held, and the recording counted "
            f"{view.get('held')}"
        )
    rows = files[ROWS].decode("utf-8").splitlines()
    shipped = _hold_rows(run.report.work, rows, full, determined, found.rows, found.problems, "shipped")
    legacy = _hold_rows(
        legacy_run.work,
        [row_line(item) for item in legacy_run.work],
        [otherwise.get(item.result.id, line) for item, line in zip(run.report.work, full, strict=True)],
        determined,
        found.legacy,
        found.problems,
        "legacy",
    )
    # The counts of layers read are in no result's line. Where a result was taken from the
    # recording they are the recording's too.
    counts = (
        {name: getattr(run.report, name) for name in LAYER_COUNTS} if found.view == "full" else layer_counts
    )
    _hold_committed(found, root, run, _report(shipped, counts), _report(legacy, counts))
    return found


def summary(found: SpanReport) -> str:
    """The report as the lines the gate prints."""

    def held(files: Mapping[str, bool]) -> str:
        return f"{sum(files.values())} of {len(files)}"

    lines = [
        f"the store at {found.store_ref}, measured as of {found.date}",
        f"  {found.pdftotext}",
        f"  objects held: {found.objects['held']:,} of {found.objects['manifest']:,}, the {found.view} view",
        f"  results: {found.results:,}, of which {found.resolved:,} resolve over these objects",
        f"  files that are the TypeScript's by digest: {held(found.digests)}",
        (
            f"  files that are the ones the TypeScript wrote in this run: {held(found.recorded)}"
            if found.recorded
            else "  the TypeScript was not run beside this"
        ),
        (
            f"  results measured here with the line the full view has: {found.rows.same:,} of "
            f"{found.rows.expected:,}"
        ),
        f"  the same under the legacy quote rule: {found.legacy.same:,} of {found.legacy.expected:,}",
        f"  results that need a restricted object, taken as recorded: {found.held:,}",
        f"  committed tables the results give: {held(found.tables)}",
        f"  committed figures the results give: {found.figures.same} of {found.figures.expected}",
        (
            "  the committed figures file is the bytes the results give: "
            f"{'yes' if found.figures_file else 'no'}, with {found.gained} of its inputs left as "
            "committed because they have gained records"
        ),
    ]
    return "\n".join(lines)


def to_json(found: SpanReport) -> str:
    """The report for a machine to read."""
    return js_stringify(
        {
            "store_ref": found.store_ref,
            "date": found.date,
            "view": found.view,
            "pdftotext": found.pdftotext,
            "objects": dict(found.objects),
            "results": found.results,
            "resolved": found.resolved,
            "rows": found.rows.row(),
            "legacy": found.legacy.row(),
            "held": found.held,
            "digests": {"expected": len(found.digests), "same": sum(found.digests.values())},
            "recorded": {"expected": len(found.recorded), "same": sum(found.recorded.values())},
            "tables": {"expected": len(found.tables), "same": sum(found.tables.values())},
            "figures": found.figures.row(),
            "figures_file": {"same": found.figures_file, "gained": found.gained},
            "problems": list(found.problems),
        }
    )


def assemble(full: Path, public: Path, pdftotext: str) -> tuple[str, bytes, bytes]:
    """The committed expectation from two recordings: the JSON, and the two files of lines of the full view.

    `full` and `public` are folders `record_spans_store.mjs` wrote, over every object and
    over the public ones. Raises `StoreError` where the two are not of one commit, one date
    and one Node.
    """
    one, other = _summary(full), _summary(public)
    for name in ("recorded_from", "date", "results", "objects"):
        if name not in one or one[name] != other.get(name):
            raise StoreError(f"the two recordings differ in {name}: {one.get(name)} and {other.get(name)}")
    if one.get("objects_held") != one["objects"]:
        raise StoreError(f"{full} is not a recording over every object")

    def view(held: JsonObject) -> JsonObject:
        return {
            name: value
            for name, value in held.items()
            if name not in {"recorded_from", "date", "results", "objects"}
        }

    text = (
        "{\n"
        + ",\n".join(
            f" {js_stringify(name)}: {js_stringify(value)}"
            for name, value in (
                ("recorded_from", one["recorded_from"]),
                ("pdftotext", pdftotext),
                ("date", one["date"]),
                ("results", one["results"]),
                ("objects", one["objects"]),
                ("rows", "store-spans.jsonl.gz"),
                ("legacy_rows", "store-spans-legacy.jsonl.gz"),
                ("full", view(one)),
                ("public", view(other)),
            )
        )
        + "\n}\n"
    )
    return (
        text,
        gzip.compress((full / ROWS).read_bytes(), 9, mtime=0),
        gzip.compress((full / LEGACY_ROWS).read_bytes(), 9, mtime=0),
    )
