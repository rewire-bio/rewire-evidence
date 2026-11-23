"""Measure span resolution over the whole store: `measure` of `scripts/omics/spans/cli.ts`.

For every stored result that carries a locator, find the text layers the archive holds for
its sources, parse the locator, resolve it, and record a span or the one reason there is
none. Nothing is written back to the store: a span is derived when it is measured.

Two things are reported apart and must not be confused: how many locators the grammar
addresses, and how many reach a span in a layer the archive holds. A third is measured
because a span can resolve and still be wrong: whether the text the span covers prints the
value the record stores.

The work is grouped by layer, because a layer's text is derived again from the stored object
each time it is read, and a locator may be tried on up to four layers.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from rewire_evidence import archive, registry
from rewire_evidence.aligned import lower_units
from rewire_evidence.archive import blank, raw_lines
from rewire_evidence.canonical import JsonObject, JsonValue, js_number, utf16_key
from rewire_evidence.gridspans import GridUnreadable, TableGrid
from rewire_evidence.js import SPACE_CLASS, fixed, number_of_text
from rewire_evidence.jsonl import StoreError, parse_value
from rewire_evidence.layercoverage import coverage_of
from rewire_evidence.layerreaders import LayerStore, LayerUnreadable, by_usefulness, is_grid
from rewire_evidence.locator import Parsed, classify_locator, parse_locator
from rewire_evidence.locator import coverage_of as parse_coverage
from rewire_evidence.offsets import index_layer, to_units
from rewire_evidence.resolution import GRID_ROUTES, REFUSAL_REASONS, Refusal, Resolved, progress
from rewire_evidence.resolve import resolve_locator

RESULTS: Final = "data/evidence/results"
SOURCES: Final = "data/entities/sources.jsonl"
REPORTS: Final = "maintenance/reports"
#: How many layers a locator is tried against before it is counted as refused.
MAX_LAYERS: Final = 4
#: A progress line is written after each this many layers.
PROGRESS_EVERY: Final = 100

_S: Final = f"[{SPACE_CLASS}]"
_DASHES: Final = re.compile("[\u2212\u2013\u2014]")
_SPACES: Final = re.compile(f"{_S}+")
#: One number and nothing else, as either side of the value check has to be after flattening.
ONE_NUMBER: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?")
_EXPONENT: Final = re.compile(r"(-?)([0-9]+)(?:\.([0-9]+))?e([+-][0-9]+)", re.IGNORECASE)
#: A locator that says its value was read off an image.
VALUE_IN_IMAGE: Final = re.compile(
    rf"\b(?:shown|drawn|plotted|depicted|read){_S}+(?:in|on|off|from){_S}+(?:the{_S}+)?"
    r"(?:fig\b|fig\.|figure|panel|chart|plot|bar)",
    re.IGNORECASE | re.ASCII,
)
_BEYOND: Final = re.compile("[\U00010000-\U0010ffff]")
_MOST_DECIMALS: Final = 100
_HALF: Final = "5"


@dataclass(frozen=True)
class StoredResult:
    """A stored result as the measurement reads it."""

    id: str
    source_ids: tuple[str, ...]
    locator: str
    printed_value: str


@dataclass(frozen=True)
class Candidate:
    """A layer a locator may be tried on."""

    layer_sha256: str
    object_sha256: str
    normaliser: str


@dataclass
class Work:
    """One result, and everything the measurement decided about it."""

    result: StoredResult
    parsed: Parsed
    locator_class: str
    parse_bucket: str
    candidates: list[Candidate] = field(default_factory=list)
    tried: int = 0
    resolved: Resolved | None = None
    #: The refusal that got furthest. It is kept when a later layer resolves the locator.
    refusal: Refusal | None = None
    #: Whether the span's text prints the stored value, once whitespace and dash variants are
    #: set aside. `None` where there is no span.
    value_present: bool | None = None
    #: Whether the span's text is the stored value and nothing else.
    value_exact: bool | None = None
    value_partial: bool | None = None
    #: The span is the right place and the value is in an image.
    value_in_image: bool | None = None
    #: How a grid found the table, where a grid was read for this locator.
    grid_route: str | None = None


def _rows(path: Path) -> list[JsonValue]:
    return [parse_value(line, str(path)) for line in raw_lines(path) if not blank(line)]


def _identified(path: Path) -> list[tuple[str, JsonObject]]:
    """Every record of a file with its ID.

    Raises `StoreError` for a line that is no object or holds no ID that is text. The
    TypeScript reads such a line as a record whose ID is `undefined` or a number, and no
    line of what it then writes can be told from the next.
    """
    found = []
    for number, held in enumerate(_rows(path), 1):
        name = held.get("id") if isinstance(held, dict) else None
        if not isinstance(held, dict) or not isinstance(name, str):
            raise StoreError(f"{path}: record {number} is not an object with an ID")
        found.append((name, held))
    return found


def read_results(root: Path) -> list[StoredResult]:
    """Every record of the result files, in file order then line order.

    A record with no attributes, or with a locator or a printed value that is not text, is a
    result with none, as the TypeScript reads it. Raises `StoreError` for a record with no
    ID and for sources that are not a list of IDs.
    """
    folder = root / RESULTS
    try:
        names = sorted(
            (path.name for path in folder.iterdir() if path.name.endswith(".jsonl")), key=utf16_key
        )
    except OSError as error:
        raise StoreError(f"{folder} cannot be read: {error}") from error
    results = []
    for name in names:
        for found, record in _identified(folder / name):
            held = record.get("attributes")
            attributes = held if isinstance(held, dict) else {}
            sources = record.get("source_ids")
            if not isinstance(sources, list | None) or any(
                not isinstance(source, str) for source in sources or []
            ):
                raise StoreError(f"{folder / name}: the sources of {found} are not a list of IDs")
            locator = attributes.get("source_locator")
            printed = attributes.get("printed_value")
            results.append(
                StoredResult(
                    found,
                    tuple(cast("list[str]", sources or [])),
                    locator if isinstance(locator, str) else "",
                    printed if isinstance(printed, str) else "",
                )
            )
    return results


def source_ids(root: Path) -> list[str]:
    """The ID of every source record. Raises `StoreError` for a record with none."""
    return [name for name, _ in _identified(root / SOURCES)]


def preferred_normaliser(parsed: Parsed) -> str | None:
    """Which layer a locator says it was read from, where its layer qualifier says."""
    for qualifier in parsed.qualifiers:
        if qualifier.kind != "layer":
            continue
        text = lower_units(to_units(qualifier.text))
        if "pdf" in text:
            return "pdf-text-layer@1"
        if "xml" in text or "jats" in text:
            return "europepmc-fulltext-xml@1"
        if "html" in text:
            return "html-text@1"
        if "csv" in text or "tsv" in text or "json" in text:
            return "text-utf8@1"
    return None


# ---- does the span print the stored value ----------------------------------------------


def flatten_value(text: str) -> str:
    """A value with whitespace out, the dashes folded and in lower case. `text` is in units."""
    return lower_units(_SPACES.sub("", _DASHES.sub("-", text).replace("\u00b1", "+/-")))


def decimals_written(spelling: str) -> int:
    """Decimal places a spelling of a number commits to, an exponent counted in."""
    mantissa, _, exponent = spelling.partition("e")
    fraction = len(mantissa.partition(".")[2])
    return int(max(0, min(_MOST_DECIMALS, fraction - number_of_text(exponent or "0"))))


def plain_decimal(value: float) -> str:
    """A number's shortest spelling as a plain decimal, with no exponent."""
    text = js_number(value)
    found = _EXPONENT.fullmatch(text)
    if not found:
        return text
    sign, integer, fraction, exponent = found.groups()
    digits = integer + (fraction or "")
    point = len(integer) + int(exponent)
    if point <= 0:
        return f"{sign}0.{'0' * -point}{digits}"
    # JavaScript writes an exponent from 1e21 up, and no double has that many digits, so the
    # point falls past the last of them. The TypeScript also writes a point among the digits,
    # which no number reaches.
    return f"{sign}{digits}{'0' * (point - len(digits))}"


def round_decimal(spelling: str, places: int) -> str:
    """A plain decimal rounded half away from zero to a number of places, done on its digits."""
    sign = "-" if spelling.startswith("-") else ""
    integer, _, fraction = spelling.removeprefix("-").removeprefix("+").partition(".")
    if len(fraction) <= places:
        return f"{sign}{integer}.{fraction.ljust(places, '0')}"
    digits = integer + fraction[:places]
    if fraction[places] >= _HALF:
        digits = str(int(digits) + 1).rjust(len(digits), "0")
    cut = len(digits) - places
    return f"{sign}{digits[:cut] or '0'}.{digits[cut:]}"


def prints_same_number(span: str, value: str) -> bool:
    """Whether a span and a stored value print the same number at the stored value's precision."""
    if not ONE_NUMBER.fullmatch(span) or not ONE_NUMBER.fullmatch(value):
        return False
    theirs = number_of_text(span)
    ours = number_of_text(value)
    if math.isinf(theirs) or math.isinf(ours):
        return False
    places = decimals_written(value)
    return number_of_text(round_decimal(plain_decimal(theirs), places)) == number_of_text(
        round_decimal(plain_decimal(ours), places)
    )


@dataclass(frozen=True)
class Presence:
    """How a span stands to the stored value: it prints it, it is it, or it is a part of it."""

    present: bool = False
    exact: bool = False
    partial: bool = False


def value_presence(exact: str, printed: str) -> Presence:
    """Whether a span prints the stored value. Both texts are in units.

    Whitespace and case are set aside, the dashes are folded, and where both are one number
    they are compared as numbers at the precision the record wrote.
    """
    if not printed:
        return Presence()
    span = flatten_value(exact)
    value = flatten_value(printed)
    if prints_same_number(span, value):
        return Presence(present=True, exact=True)
    if value in span:
        return Presence(present=True, exact=span == value)
    if span and re.search("[0-9]", span) and span in value:
        return Presence(partial=True)
    return Presence()


# ---- the measurement -------------------------------------------------------------------


@dataclass
class Tally:
    """Locators of one class, bucket or route, and how many of them resolved and how well."""

    locators: int = 0
    resolved: int = 0
    value_present: int = 0
    value_exact: int = 0


@dataclass
class MeasureReport:
    """What a measurement found, with the work behind it."""

    work: list[Work]
    locators: int = 0
    resolved: int = 0
    value_present: int = 0
    value_exact: int = 0
    value_partial: int = 0
    value_in_image: int = 0
    by_grid_route: dict[str, Tally] = field(default_factory=dict)
    grids_read: int = 0
    grids_unreadable: int = 0
    by_reason: dict[str, int] = field(default_factory=dict)
    by_kind: dict[str, int] = field(default_factory=dict)
    by_class: dict[str, Tally] = field(default_factory=dict)
    by_parse_bucket: dict[str, Tally] = field(default_factory=dict)
    layers_read: int = 0
    layers_astral: int = 0
    astral_points: int = 0
    #: Locators resolved on a layer after the first tried.
    later_layer: int = 0


def receipt_facts(root: Path) -> dict[str, JsonObject]:
    """The newest fetch receipt of each source."""
    return archive.latest_by_source(archive.read_receipts(root))


def _keep(item: Work, refusal: Refusal) -> None:
    """Hold the refusal that got furthest."""
    if item.refusal is None or progress(refusal.reason) > progress(item.refusal.reason):
        item.refusal = refusal


def _candidates(
    result: StoredResult,
    by_source: Mapping[str, Sequence[JsonObject]],
    readable: Callable[[str], bool],
) -> tuple[list[Candidate], int, int]:
    """The layers a result may be tried on, and how many were set aside as failed or as not held."""
    found: list[Candidate] = []
    seen: set[str] = set()
    unusable = unreadable = 0
    for source in result.source_ids:
        for record in by_source.get(source, ()):
            layer = cast("str", record["layer_sha256"])
            if layer in seen:
                continue
            seen.add(layer)
            key = cast("str", record["object_sha256"])
            if record["sanity"] == "fail":
                unusable += 1
            elif not readable(key):
                unreadable += 1
            else:
                found.append(Candidate(layer, key, cast("str", record["normaliser"])))
    return found, unusable, unreadable


def _no_layer(result: StoredResult, buckets: Mapping[str, str], unusable: int, unreadable: int) -> Refusal:
    """Why a result has no layer to be tried on, in the archive's own words."""
    held = [buckets.get(source) for source in result.source_ids]
    if unreadable:
        reason = "layer_not_readable"
    elif unusable:
        reason = "layer_unusable"
    elif "no_normaliser" in held:
        reason = "no_normaliser"
    elif "not_the_artifact" in held:
        reason = "not_the_artifact"
    else:
        reason = "no_capture"
    return Refusal(reason, f"no readable layer for {' '.join(result.source_ids)}")


def _work(root: Path, store: LayerStore, limit: int | None) -> list[Work]:
    coverage = coverage_of(
        source_ids(root), receipt_facts(root), store.records, registry.read_unsupported(root)
    )
    buckets = {row.source_id: row.bucket for row in coverage.rows}
    by_source: dict[str, list[JsonObject]] = {}
    for record in store.records:
        if not is_grid(record):
            for source in cast("list[str]", record["source_ids"]):
                by_source.setdefault(source, []).append(record)
    for held in by_source.values():
        held.sort(key=by_usefulness)
    known: dict[str, bool] = {}

    def readable(key: str) -> bool:
        if key not in known:
            known[key] = store.has_object(key)
        return known[key]

    work = []
    for result in read_results(root)[: limit or None]:
        parsed = parse_locator(result.locator)
        item = Work(result, parsed, classify_locator(result.locator), parse_coverage(parsed.verdict))
        found, unusable, unreadable = _candidates(result, by_source, readable)
        preferred = preferred_normaliser(parsed)
        if preferred:
            found.sort(key=lambda candidate: candidate.normaliser != preferred)
        item.candidates = found[:MAX_LAYERS]
        if not item.candidates:
            item.refusal = _no_layer(result, buckets, unusable, unreadable)
        work.append(item)
    return work


def _measure_item(item: Work, outcome: Resolved | Refusal) -> None:
    item.tried += 1
    if outcome.grid_route:
        item.grid_route = outcome.grid_route
    if isinstance(outcome, Refusal):
        _keep(item, outcome)
        return
    item.resolved = outcome
    presence = value_presence(outcome.span.exact, to_units(item.result.printed_value))
    item.value_present = presence.present
    item.value_exact = presence.exact
    item.value_partial = presence.partial
    item.value_in_image = (
        not presence.present
        and not presence.partial
        and (
            outcome.kind == "figure_caption"
            or VALUE_IN_IMAGE.search(to_units(item.result.locator)) is not None
        )
    )


def count(report: MeasureReport) -> None:
    """The totals and the tables of a report, counted from its work."""
    report.locators = len(report.work)
    for item in report.work:
        by_class = report.by_class.setdefault(item.locator_class, Tally())
        by_class.locators += 1
        by_bucket = report.by_parse_bucket.setdefault(item.parse_bucket, Tally())
        by_bucket.locators += 1
        resolved = item.resolved
        if item.grid_route:
            by_route = report.by_grid_route.setdefault(item.grid_route, Tally())
            by_route.locators += 1
            # A route counts a span only where the grid gave it, not where a later layer did.
            if resolved is not None and resolved.kind == "grid_cell":
                by_route.resolved += 1
                by_route.value_present += bool(item.value_present)
                by_route.value_exact += bool(item.value_exact)
        if resolved is not None:
            report.resolved += 1
            by_class.resolved += 1
            by_bucket.resolved += 1
            report.later_layer += item.tried > 1
            report.value_present += bool(item.value_present)
            by_class.value_present += bool(item.value_present)
            report.value_exact += bool(item.value_exact)
            by_class.value_exact += bool(item.value_exact)
            report.value_partial += bool(item.value_partial)
            report.value_in_image += bool(item.value_in_image)
            report.by_kind[resolved.kind] = report.by_kind.get(resolved.kind, 0) + 1
        else:
            reason = item.refusal.reason if item.refusal else "no_resolver_for_kind"
            report.by_reason[reason] = report.by_reason.get(reason, 0) + 1


def measure(  # noqa: C901, PLR0913 - the options are the command's flags, and the passes are one loop
    root: Path,
    *,
    limit: int | None = None,
    log: Callable[[str], None] = lambda _: None,
    grid: bool = True,
    sheet: bool = True,
    legacy_quote: bool = False,
    objects: Path | None = None,
) -> MeasureReport:
    """Resolve every stored locator against the layers the archive holds.

    `grid` set to false reads no table grid, `sheet` set to false leaves a sheet layer's
    blocks unread, and `legacy_quote` selects the quote as it was selected before the label
    rule. Each is one arm of a pair, measured so that a rule's effect is two runs of one
    program over one store. `objects` is a folder of objects to read in place of the cache.
    """
    store = LayerStore(root, objects)
    report = MeasureReport(_work(root, store, limit))
    grids: dict[str, TableGrid | None] = {}

    def grid_for(layer: str) -> TableGrid | None:
        if not grid:
            return None
        if layer not in grids:
            grids[layer] = None
            record = store.grid_for_layer(layer)
            if record is not None:
                try:
                    grids[layer] = store.grid_of_record(record)
                    report.grids_read += 1
                except (LayerUnreadable, GridUnreadable):
                    # An unreadable grid leaves the locator where it was without one, and is counted.
                    report.grids_unreadable += 1
        return grids[layer]

    for attempt in range(MAX_LAYERS):
        by_layer: dict[str, list[Work]] = {}
        for item in report.work:
            if item.resolved is None and len(item.candidates) > attempt:
                by_layer.setdefault(item.candidates[attempt].layer_sha256, []).append(item)
        if not by_layer:
            break
        log(f"pass {attempt + 1}: {len(by_layer)} layers, {sum(map(len, by_layer.values()))} locators")
        done = 0
        for layer, items in by_layer.items():
            record = cast("JsonObject", store.layer_by_hash(layer))
            try:
                text = store.layer_text(record)
            except LayerUnreadable as error:
                for item in items:
                    _keep(item, Refusal("layer_not_readable", f"Error: {error}", layer))
                continue
            report.layers_read += 1
            astral = len(_BEYOND.findall(text))
            report.layers_astral += astral > 0
            report.astral_points += astral
            index = index_layer(text, layer, cast("str", record["normaliser"]))
            held = grid_for(layer)
            for item in items:
                _measure_item(
                    item, resolve_locator(item.parsed, index, held, sheet=sheet, legacy_quote=legacy_quote)
                )
            done += 1
            if done % PROGRESS_EVERY == 0:
                log(f"  {done} of {len(by_layer)} layers")
    count(report)
    return report


# ---- the tables ------------------------------------------------------------------------


def pct(part: float, whole: float) -> str:
    """A share as a percentage with one decimal, as `toFixed(1)` writes it."""
    return "0.0" if whole == 0 else fixed(part / whole * 100, 1)


def reason_table(report: MeasureReport) -> str:
    """Refusals by reason, in the order the reasons are declared."""
    lines = ["reason\tlocators\tshare_of_all"]
    for reason in REFUSAL_REASONS:
        count = report.by_reason.get(reason, 0)
        if count:
            lines.append(f"{reason}\t{count}\t{pct(count, report.locators)}")
    refused = report.locators - report.resolved
    lines.append(f"TOTAL REFUSED\t{refused}\t{pct(refused, report.locators)}")
    return "\n".join(lines)


def _top_refusal(report: MeasureReport, name: str) -> tuple[str, int]:
    reasons: dict[str, int] = {}
    for item in report.work:
        if item.locator_class == name and item.resolved is None and item.refusal:
            reasons[item.refusal.reason] = reasons.get(item.refusal.reason, 0) + 1
    ordered = sorted(reasons.items(), key=lambda held: -held[1])
    return ordered[0] if ordered else ("", 0)


def class_table(report: MeasureReport) -> str:
    """Locators by class, the largest first, each with the reason it is most refused for."""
    lines = [
        (
            "class\tlocators\tresolved\tresolved_pct\tvalue_exact\tvalue_exact_pct\tvalue_present"
            "\tvalue_present_pct\ttop_refusal\ttop_refusal_count"
        )
    ]
    for name, row in sorted(report.by_class.items(), key=lambda held: -held[1].locators):
        lines.append(
            "\t".join(
                map(
                    str,
                    (
                        name,
                        row.locators,
                        row.resolved,
                        pct(row.resolved, row.locators),
                        row.value_exact,
                        pct(row.value_exact, row.resolved),
                        row.value_present,
                        pct(row.value_present, row.resolved),
                        *_top_refusal(report, name),
                    ),
                )
            )
        )
    lines.append(
        "\t".join(
            map(
                str,
                (
                    "TOTAL",
                    report.locators,
                    report.resolved,
                    pct(report.resolved, report.locators),
                    report.value_exact,
                    pct(report.value_exact, report.resolved),
                    report.value_present,
                    pct(report.value_present, report.resolved),
                    "",
                    "",
                ),
            )
        )
    )
    return "\n".join(lines)


def _of_kind(report: MeasureReport, kind: str, member: str) -> int:
    return sum(
        1
        for item in report.work
        if item.resolved is not None and item.resolved.kind == kind and getattr(item, member) is True
    )


def kind_table(report: MeasureReport) -> str:
    """Spans by resolver, the most first, with how each stands to the stored value."""
    lines = [
        (
            "span_kind\tspans\tvalue_exact\tvalue_exact_pct\tvalue_present\tvalue_present_pct"
            "\twider_than_value\tvalue_partly_present\tvalue_in_image\tno_value"
        )
    ]
    for kind, count in sorted(report.by_kind.items(), key=lambda held: -held[1]):
        exact = _of_kind(report, kind, "value_exact")
        present = _of_kind(report, kind, "value_present")
        partial = _of_kind(report, kind, "value_partial")
        image = _of_kind(report, kind, "value_in_image")
        lines.append(
            f"{kind}\t{count}\t{exact}\t{pct(exact, count)}\t{present}\t{pct(present, count)}"
            f"\t{present - exact}\t{partial}\t{image}\t{count - present - partial - image}"
        )
    return "\n".join(lines)


def bucket_table(report: MeasureReport) -> str:
    """Locators by how much of each the grammar read."""
    lines = ["parse_bucket\tlocators\tresolved\tresolved_pct"]
    for bucket in ("full", "partial", "residue", "unanchored"):
        row = report.by_parse_bucket.get(bucket)
        if row:
            lines.append(f"{bucket}\t{row.locators}\t{row.resolved}\t{pct(row.resolved, row.locators)}")
    return "\n".join(lines)


def grid_table(report: MeasureReport) -> str:
    """Locators a grid was read for, by the route to the table."""
    lines = [
        (
            "grid_route\tlocators\tresolved\tresolved_pct\tvalue_present\tvalue_present_pct"
            "\tvalue_exact\tvalue_exact_pct"
        )
    ]
    total = Tally()
    for route in GRID_ROUTES:
        row = report.by_grid_route.get(route)
        if not row:
            continue
        total.locators += row.locators
        total.resolved += row.resolved
        total.value_present += row.value_present
        total.value_exact += row.value_exact
        lines.append(_route_line(route, row))
    lines.append(_route_line("TOTAL", total))
    return "\n".join(lines)


def _route_line(name: str, row: Tally) -> str:
    return (
        f"{name}\t{row.locators}\t{row.resolved}\t{pct(row.resolved, row.locators)}\t{row.value_present}"
        f"\t{pct(row.value_present, row.resolved)}\t{row.value_exact}\t{pct(row.value_exact, row.resolved)}"
    )


def detail_rows(report: MeasureReport) -> str:
    """One line for each result, for a reader who wants to join a span to something else."""
    lines = [
        (
            "id\tclass\tparse_bucket\toutcome\treason_or_kind\tlayer_sha256\tvalue_present\tvalue_exact"
            "\tgrid_route\tlayers_tried\tsource_ids\tstart\tend"
        )
    ]
    for item in report.work:
        resolved = item.resolved
        refusal = item.refusal
        lines.append(
            "\t".join(
                (
                    item.result.id,
                    item.locator_class,
                    item.parse_bucket,
                    "resolved" if resolved else "refused",
                    resolved.kind if resolved else (refusal.reason if refusal else "none"),
                    resolved.layer_sha256
                    if resolved
                    else ((refusal.layer_sha256 if refusal else None) or ""),
                    str(item.value_present).lower() if resolved else "",
                    str(item.value_exact).lower() if resolved else "",
                    item.grid_route or "",
                    str(item.tried),
                    "|".join(item.result.source_ids),
                    str(resolved.span.start) if resolved else "",
                    str(resolved.span.end) if resolved else "",
                )
            )
        )
    return "\n".join(lines)


def wrong_commits(report: MeasureReport) -> int:
    """Spans whose text does not print the stored value, in whole or in part, and names no image."""
    return report.resolved - report.value_present - report.value_partial - report.value_in_image


def _share(part: float, whole: float) -> float:
    return number_of_text(pct(part, whole))


def figures_of(report: MeasureReport) -> dict[str, float]:
    """Every figure a document prints from this measurement, by name, in the TypeScript's order."""
    figures: dict[str, float] = {
        "locators": report.locators,
        "resolved": report.resolved,
        "resolved_pct": _share(report.resolved, report.locators),
        "refused": report.locators - report.resolved,
        "value_exact": report.value_exact,
        "value_exact_pct": _share(report.value_exact, report.resolved),
        "value_present": report.value_present,
        "value_present_pct": _share(report.value_present, report.resolved),
        "value_partial": report.value_partial,
        "value_in_image": report.value_in_image,
        "wrong_commits": wrong_commits(report),
        "wider_than_value": report.value_present - report.value_exact,
        "layers_read": report.layers_read,
        "layers_astral": report.layers_astral,
        "astral_points": report.astral_points,
        "grids_read": report.grids_read,
        "later_layer": report.later_layer,
    }
    for reason, count in report.by_reason.items():
        figures[f"reason.{reason}"] = count
        figures[f"reason.{reason}.pct"] = _share(count, report.locators)
    for kind, count in report.by_kind.items():
        present = _of_kind(report, kind, "value_present")
        image = _of_kind(report, kind, "value_in_image")
        figures[f"kind.{kind}.spans"] = count
        figures[f"kind.{kind}.value_exact"] = _of_kind(report, kind, "value_exact")
        figures[f"kind.{kind}.value_present"] = present
        figures[f"kind.{kind}.value_in_image"] = image
        figures[f"kind.{kind}.no_value"] = count - present - _of_kind(report, kind, "value_partial") - image
    for name, row in report.by_class.items():
        figures[f"class.{name}.locators"] = row.locators
        figures[f"class.{name}.resolved"] = row.resolved
        figures[f"class.{name}.resolved_pct"] = _share(row.resolved, row.locators)
        figures[f"class.{name}.value_exact"] = row.value_exact
        figures[f"class.{name}.value_present"] = row.value_present
    for bucket, tally in report.by_parse_bucket.items():
        figures[f"bucket.{bucket}.locators"] = tally.locators
        figures[f"bucket.{bucket}.resolved"] = tally.resolved
    return figures


def legacy_figures_of(report: MeasureReport | None) -> dict[str, float]:
    """The same store measured with the quote selection as it stood before the label rule."""
    if report is None:
        return {}
    quotes = report.by_kind.get("quote", 0)
    return {
        "legacy.resolved": report.resolved,
        "legacy.value_present": report.value_present,
        "legacy.value_exact": report.value_exact,
        "legacy.wrong_commits": wrong_commits(report),
        "legacy.kind.quote.spans": quotes,
        "legacy.kind.quote.no_value": quotes
        - _of_kind(report, "quote", "value_present")
        - _of_kind(report, "quote", "value_partial")
        - _of_kind(report, "quote", "value_in_image"),
    }
