"""What the TypeScript span resolvers recorded, and the helpers the tests replay it with.

`tests/fixtures/spans.json` is written by `scripts/fixtures/record_spans_fixture.mjs` in CI,
from the data repository's own code at the pinned commit. Nothing in it was written by hand.

A case is the name of a call and what it was given. `CALLS` holds, under each name, the same
call made of this library, giving what the recorder gave: a value as JSON holds it.
"""

from __future__ import annotations

import functools
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rewire_evidence import (
    aligned,
    figures,
    gridspans,
    jsonspans,
    layercoverage,
    layerreaders,
    offsets,
    prose,
    regions,
    resolve,
    sheets,
    spanmeasure,
)
from rewire_evidence.grids import GridCell, GridRange, GridTable
from rewire_evidence.js import number_of_text, paired
from rewire_evidence.locator import Address, Anchor, Parsed, Qualifier, Selector, parse_locator, selector_role
from rewire_evidence.offsets import LayerIndex, Span, to_units
from rewire_evidence.prose import Anchoring
from rewire_evidence.resolution import Refusal, Resolved

FIXTURES = Path(__file__).parent / "fixtures"
SPANS: dict[str, Any] = json.loads((FIXTURES / "spans.json").read_text("utf-8"))
#: The layers the cases are resolved against, by name: text, normaliser, hash, and grid if any.
LAYERS: dict[str, dict[str, Any]] = SPANS["layers"]


@functools.cache
def index(name: str) -> LayerIndex:
    layer = LAYERS[name]
    return offsets.index_layer(layer["text"], layer["layer_sha256"], layer["normaliser"])


@functools.cache
def grid(name: str) -> gridspans.TableGrid | None:
    held = LAYERS[name]["grid"]
    return None if held is None else gridspans.read_grid(held)


def number(held: float | str) -> float:
    """A number as a case writes it: one JSON has no word for is written as its name."""
    return number_of_text(held) if isinstance(held, str) else held


def whole(held: float | str) -> int:
    """A number the port takes as an integer. The cases give no other to these calls."""
    value = number(held)
    if math.isnan(value) or not float(value).is_integer():
        raise TypeError(f"{held} is not an integer")
    return int(value)


def text(held: str | None) -> str | None:
    return None if held is None else paired(held)


def without_nothing(held: dict[str, Any]) -> dict[str, Any]:
    """A value as `JSON.stringify` writes an object: a member that is undefined is left out."""
    return {name: value for name, value in held.items() if value is not None}


def resolution(outcome: Resolved | Refusal | Anchoring) -> dict[str, Any]:
    """An outcome as the TypeScript holds one."""
    if isinstance(outcome, Refusal):
        return without_nothing(
            {
                "ok": False,
                "reason": outcome.reason,
                "detail": paired(outcome.detail),
                "layer_sha256": outcome.layer_sha256,
                "grid_route": outcome.grid_route,
            }
        )
    if isinstance(outcome, Anchoring):
        return {
            "ok": True,
            "outcome": outcome.outcome,
            "evidence": outcome.evidence,
            "span": outcome.span.row(),
        }
    return without_nothing(
        {
            "ok": True,
            "kind": outcome.kind,
            "span": outcome.span.row(),
            "layer_sha256": outcome.layer_sha256,
            "row_label": text(outcome.row_label),
            "column_header": text(outcome.column_header),
            "anchor_candidates": outcome.anchor_candidates,
            "grid_route": outcome.grid_route,
        }
    )


def extent(held: tuple[int, int] | None) -> dict[str, int] | None:
    return None if held is None else {"from": held[0], "to": held[1]}


def field(held: aligned.Cell | regions.Field | sheets.SheetField) -> dict[str, Any]:
    return {"text": paired(held.text), "from": held.start, "to": held.end}


def anchor(held: list[str] | dict[str, Any]) -> Anchor:
    if isinstance(held, dict):
        return Anchor(held["kind"], to_units(held["value"]), to_units(held["raw"]), held.get("container"))
    kind, value = held
    return Anchor(kind, to_units(value), to_units(f"{kind} {value}"))


def cell_request(request: dict[str, Any]) -> aligned.CellRequest:
    return aligned.CellRequest(
        anchor(request["anchor"]),
        to_units(request["row"]),
        to_units(request["column"]),
        tuple(request.get("keys", ())),
        tuple(request.get("subColumns", ())),
    )


def grid_request(request: dict[str, Any]) -> gridspans.GridCellRequest:
    return gridspans.GridCellRequest(
        anchor(request["anchor"]),
        to_units(request["row"]),
        to_units(request["column"]),
        tuple(request.get("keys", ())),
        tuple(request.get("subColumns", ())),
        tuple(request.get("levelHints", ())),
    )


def json_node(node: jsonspans.JsonNode) -> dict[str, Any]:
    held: dict[str, Any] = {"kind": node.kind, "from": node.start, "to": node.end}
    if node.kind == "object":
        held["members"] = [{"key": paired(key), "node": json_node(member)} for key, member in node.members]
    elif node.kind == "array":
        held["items"] = [json_node(item) for item in node.items]
    else:
        held["text"] = text(node.text)
    return held


def block(held: sheets.SheetBlock | None) -> dict[str, Any] | None:
    if held is None:
        return None
    return {
        "index": held.index,
        "name": paired(held.name),
        "range": paired(held.range),
        "header_line": held.header_line,
        "rows": held.rows,
    }


def cell_range(found: sheets.CellRange | None) -> dict[str, Any] | None:
    if found is None:
        return None
    return {
        "start": found.start,
        "end": found.end,
        "field": found.field,
        "value": found.value,
        "line": found.line,
        "field_number": found.field_number,
    }


def sheet_request(request: sheets.SheetCellRequest | None) -> dict[str, Any] | None:
    if request is None:
        return None
    return without_nothing(
        {
            "sheet": request.sheet,
            "rowKeys": [
                {"key": key.key, "value": key.value, "printed": key.printed} for key in request.row_keys
            ],
            "cell": request.cell,
            "row": request.row,
            "column": request.column,
            "columnHeader": request.column_header,
        }
    ) | {"sheet": request.sheet}


def sheet_cell_request(request: dict[str, Any]) -> sheets.SheetCellRequest:
    return sheets.SheetCellRequest(
        None if request["sheet"] is None else to_units(request["sheet"]),
        request.get("cell"),
        request.get("row"),
        request.get("column"),
        request.get("columnHeader"),
        tuple(
            sheets.RowKey(key["key"], key["value"], key.get("printed", ""))
            for key in request.get("rowKeys", ())
        ),
    )


def table_grid(read: gridspans.TableGrid | None) -> dict[str, Any] | None:
    """A grid as the TypeScript reads one: the lines of the layer as `JSON.parse` gives them."""
    if read is None:
        return None
    return {
        "normaliser": read.normaliser,
        "text_layer_sha256": read.text_layer_sha256,
        "text_normaliser": read.text_normaliser,
        "tables": [json.loads(paired(json.dumps(table.to_json()))) for table in read.tables],
    }


def grid_cell(cell: GridCell | None) -> dict[str, Any] | None:
    return None if cell is None else json.loads(paired(json.dumps(cell.to_json())))


def parsed_of(fields: dict[str, Any]) -> Parsed:
    """A parse written out by a case, which the grammar would not give."""
    return Parsed(
        fields.get("printed", ""),
        tuple(Anchor(a["kind"], a["value"], a["raw"], a.get("container")) for a in fields.get("anchors", ())),
        tuple(Address(a["kind"], a["value"], a["raw"], a.get("value2")) for a in fields.get("addresses", ())),
        tuple(
            Selector(s["key"], s["value"], s.get("form", "bare"), s.get("raw", ""))
            for s in fields.get("selectors", ())
        ),
        tuple(Qualifier(q["kind"], q["text"]) for q in fields.get("qualifiers", ())),
        tuple(fields.get("residue", ())),
        "partial",
    )


def options_of(options: dict[str, Any] | None) -> dict[str, bool]:
    held = options or {}
    return {"legacy_quote": held.get("legacyQuote", False), "sheet": held.get("sheet", True)}


def figure_input(held: dict[str, Any]) -> figures.FigureInput:
    records = held.get("records")
    return figures.FigureInput(
        held["path"],
        held["sha256"],
        None if records is None else tuple((row["key"], row["sha256"]) for row in records),
        held.get("lines"),
    )


def input_row(held: figures.FigureInput) -> dict[str, Any]:
    row: dict[str, Any] = dict(held.row())
    if held.records is not None:
        # A line of the records file with no tab names a record and no digest, which the
        # TypeScript holds as undefined and the port as no text.
        row["records"] = [
            {"key": key, "sha256": digest} if digest else {"key": key} for key, digest in held.records
        ]
    return row


def changed(found: list[figures.Changed]) -> list[dict[str, str]]:
    return [{"path": held.path, "was": held.was, "now": held.now} for held in found]


def mark(held: figures.FigureMark) -> dict[str, Any]:
    return {"figure": held.figure, "printed": held.printed, "line": held.line}


def coverage(found: layercoverage.CoverageResult) -> dict[str, Any]:
    return {
        "rows": [
            {
                "source_id": row.source_id,
                "bucket": row.bucket,
                "object_sha256": row.object_sha256,
                "layer_sha256": row.layer_sha256,
                "sanity": row.sanity,
                "characters": row.characters,
                "source_class": row.source_class,
                "detail": row.detail,
            }
            for row in found.rows
        ],
        "tally": {
            "sources": found.sources,
            "buckets": found.buckets,
            "no_receipt": found.no_receipt,
            "characters": found.characters,
            "distinct_characters": found.distinct_characters,
            "distinct_layers": found.distinct_layers,
            "by_class": {
                name: {"sources": held.sources, "usable": held.usable, "characters": held.characters}
                for name, held in found.by_class.items()
            },
        },
        "problems": found.problems,
    }


def _walk(name: str, tokens: list[str]) -> dict[str, Any]:
    root = jsonspans.read_json(to_units(LAYERS[name]["text"]))
    assert root is not None
    taken, at = jsonspans.walk(root, [to_units(token) for token in tokens])
    if taken.node is None:
        return {"ok": False, "at": at, "step": {"ok": False, "reason": taken.reason, "token": taken.token}}
    return {
        "ok": True,
        "from": taken.node.start,
        "to": taken.node.end,
        "scalars": [paired(held) for held in jsonspans.scalars_of(taken.node)],
    }


def _read_json(held: str) -> dict[str, Any] | None:
    node = jsonspans.read_json(to_units(held))
    return None if node is None else json_node(node)


def _reanchor(name: str, span: dict[str, Any]) -> dict[str, Any]:
    held = Span(
        span.get("layer_sha256", index(name).layer_sha256),
        span["start"],
        span["end"],
        span["byte_start"],
        span["byte_end"],
        to_units(span["exact"]),
        span.get("prefix"),
        span.get("suffix"),
    )
    return resolution(prose.reanchor(held, index(name)))


def _grid_cell(name: str, at: int, row: int, column: int) -> list[Any]:
    read = grid(name)
    assert read is not None
    found = gridspans.cell_range(read, at, row, column)
    place = gridspans.table_at(read, at)
    return [
        None if place is None else read.tables[place].index,
        None if found is None else {"start": found.start, "end": found.end, "text": paired(found.text)},
        grid_cell(gridspans.cell_at(read, 0, row, column)),
    ]


def _custom_grid(name: str, cell: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """One table of one row, whose one body cell is where the case says it is."""
    layer = LAYERS[name]
    cells = [
        GridCell(0, 0, 1, 1, header=True, start=None, end=None, text=""),
        GridCell(0, 1, 1, 1, header=True, start=None, end=None, text="c"),
        GridCell(1, 0, 1, 1, header=False, start=None, end=None, text="r"),
        GridCell(1, 1, 1, 1, header=False, start=cell["start"], end=cell["end"], text=to_units(cell["text"])),
    ]
    label = GridRange(0, 7, "Table 1")
    table = GridTable(0, 2, 2, 1, [[""], ["c"]], None, label, None, cells, [])
    made = gridspans.TableGrid("html-table-grid@1", layer["layer_sha256"], layer["normaliser"], [table])
    return resolution(gridspans.resolve_grid_cell(index(name), made, grid_request(request)))


def _grid_request(printed: str | dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    parsed = resolve.in_units(parsed_of(printed) if isinstance(printed, dict) else parse_locator(printed))
    request = gridspans.grid_request(
        [(selector.key, selector.value) for selector in parsed.selectors],
        [qualifier.text for qualifier in parsed.qualifiers if qualifier.kind == "note"],
        grid_request(base),
        selector_role,
    )
    held = request.anchor
    return {
        "anchor": {"kind": held.kind, "value": held.value, "raw": held.raw},
        "row": request.row,
        "column": request.column,
        "keys": list(request.keys),
        "subColumns": list(request.sub_columns),
        "levelHints": list(request.level_hints),
    }


def _resolve_locator(
    name: str, printed: str, options: dict[str, Any] | None = None, *grids: str | None
) -> dict[str, Any]:
    """A locator against a layer and its own grid, or the grid of the layer a case names, or none."""
    held = grid(name) if not grids else None if grids[0] is None else grid(grids[0])
    return resolution(
        resolve.resolve_locator(parse_locator(printed), index(name), held, **options_of(options))
    )


def _delimited(name: str, request: dict[str, Any]) -> dict[str, Any]:
    line = number(request["line"]) if "line" in request else None
    return resolution(
        regions.resolve_delimited_cell(
            index(name), line=line, cell=request.get("cell"), column=request.get("column")
        )
    )


def _cell_width(given: dict[str, Any]) -> dict[str, Any]:
    detail = aligned.cell_width(
        [(band["from"], band["to"]) for band in given["bands"]],
        given["field"]["text"],
        given["field"]["from"],
    )
    return {"ok": True} if detail is None else {"ok": False, "detail": detail}


def _presence(exact: str, printed: str) -> dict[str, bool]:
    found = spanmeasure.value_presence(to_units(exact), to_units(printed))
    return {"present": found.present, "exact": found.exact, "partial": found.partial}


def _retag(document: str, held: dict[str, float]) -> dict[str, Any]:
    rewritten, rewrote, unknown = figures.retag(document, held)
    return {
        "text": rewritten,
        "changed": [mark(found) for found in rewrote],
        "unknown": [mark(found) for found in unknown],
    }


def _index(name: str) -> dict[str, Any]:
    held = index(name)
    return {
        "lines": len(held.lines),
        "u16": held.u16,
        "points": held.points,
        "bytes": held.bytes,
        "layer_points": offsets.layer_points(held),
    }


def _windows(name: str, caption: int) -> list[Any]:
    held = index(name)
    return [
        extent(aligned.table_window(held, caption)),
        extent(aligned.wider_window(held, caption)),
        extent(aligned.block_above(held, caption)),
    ]


def _cell_range(
    layer: str, wanted: str | int, row: float | str, column: float | str
) -> dict[str, Any] | None:
    try:
        found = sheets.cell_range(layer, wanted, whole(row), whole(column))
    except TypeError:
        # The TypeScript gives nothing for a row or a column that is no integer. The port takes integers.
        return None
    held = cell_range(found)
    return None if found is None or held is None else held | {"fragment": sheets.rfc7111_fragment(found)}


def _slice(layer: str, start: float | str, end: float | str) -> dict[str, Any]:
    return dict(layerreaders.slice_span(layer, whole(start), whole(end)))


#: Each call of the recorder, made of this library.
CALLS: dict[str, Callable[..., Any]] = {
    "index": _index,
    "countPoints": lambda held: offsets.count_points(to_units(held)),
    "spanAt": lambda name, start, end: offsets.span_at(index(name), start, end).row(),
    "spanOfLines": lambda name, first, last: offsets.span_of_lines(index(name), first, last).row(),
    "lineAt": lambda name, unit: [
        offsets.line_at(index(name), unit),
        offsets.point_at(index(name), unit),
        offsets.byte_at(index(name), unit),
    ],
    "normaliseLabel": lambda held: paired(aligned.normalise_label(to_units(held))),
    "columnForms": lambda held: [paired(form) for form in aligned.column_forms(to_units(held))],
    "escape": aligned.escape,
    "cellsOf": lambda name, line: [field(cell) for cell in aligned.cells_of(index(name), line)],
    "captionLines": lambda name, word, value: aligned.caption_lines(index(name), word, to_units(value)),
    "windows": _windows,
    "overlap": lambda a, b: aligned.overlap((a["from"], a["to"]), (b["from"], b["to"])),
    "tokensOf": lambda held: [extent(token) for token in aligned.tokens_of(held["text"], held["from"])],
    "cellWidth": _cell_width,
    "resolveTableCell": lambda name, request: resolution(
        aligned.resolve_table_cell(index(name), cell_request(request))
    ),
    "resolveFigureCaption": lambda name, value: resolution(
        aligned.resolve_figure_caption(index(name), value)
    ),
    "delimiterOf": regions.delimiter_of,
    "fieldsOf": lambda name, line, delimiter: [
        field(found) for found in regions.fields_of(index(name), line, delimiter)
    ],
    "columnIndex": regions.column_index,
    "resolveDelimitedCell": _delimited,
    "resolveKeyedRow": lambda name, request: resolution(
        regions.resolve_keyed_row(
            index(name), request["column"], [(held["key"], held["value"]) for held in request["constraints"]]
        )
    ),
    "resolveLineRange": lambda name, first, *last: resolution(
        regions.resolve_line_range(index(name), number(first), *(number(held) for held in last))
    ),
    "resolvePage": lambda name, page: resolution(regions.resolve_page(index(name), number(page))),
    "resolveQuote": lambda name, quote, entire: resolution(
        prose.resolve_quote(index(name), to_units(quote), whole=entire)
    ),
    "resolveSectionParagraph": lambda name, section, paragraph: resolution(
        prose.resolve_section_paragraph(index(name), to_units(section), number(paragraph))
    ),
    "reanchor": _reanchor,
    "reanchorAfter": lambda source, target, start, end: resolution(
        prose.reanchor(offsets.span_at(index(source), start, end), index(target))
    ),
    "codePointSlice": lambda name, start, end: [
        paired(prose.point_slice(index(name), start, end)),
        prose.unit_of_point(index(name), start),
        prose.unit_of_point(index(name), end),
    ],
    "readJson": _read_json,
    "pointerTokens": lambda path: [
        jsonspans.pointer_tokens(path),
        jsonspans.render_pointer(jsonspans.pointer_tokens(path)),
    ],
    "walk": _walk,
    "resolveJsonPointer": lambda name, request: resolution(
        jsonspans.resolve_json_pointer(
            index(name),
            request["path"],
            [
                jsonspans.JsonSelector(held["key"], held["value"], held["role"])
                for held in request["selectors"]
            ],
        )
    ),
    "unescapeCell": lambda held: paired(sheets.unescape_cell(to_units(held))),
    "sheetBlocks": lambda held: [block(found) for found in sheets.sheet_blocks(held)],
    "findBlock": lambda held, wanted: block(sheets.find_block(sheets.sheet_blocks(held), wanted)),
    "cellRange": _cell_range,
    "cellRangeByReference": lambda held, wanted, reference: cell_range(
        sheets.cell_range_by_reference(held, wanted, reference)
    ),
    "reference": sheets.reference,
    "fieldsOfLine": lambda name, line: [field(found) for found in sheets.fields_of_line(index(name), line)],
    "blocksOf": lambda name: [block(found) for found in sheets.blocks_of(index(name))],
    "sheetRequest": lambda printed: sheet_request(
        sheets.sheet_request(resolve.in_units(parse_locator(printed)))
    ),
    "sheetRequestParsed": lambda fields: sheet_request(sheets.sheet_request(parsed_of(fields))),
    "resolveSheetCell": lambda name, request: resolution(
        sheets.resolve_sheet_cell(index(name), sheet_cell_request(request))
    ),
    "readGrid": lambda held: table_grid(gridspans.read_grid(held)),
    "gridOf": lambda name: table_grid(grid(name)),
    "gridCell": _grid_cell,
    "namesTable": lambda held, value: gridspans.names_table(to_units(held), to_units(value)),
    "unitOfPoint": lambda name, point: gridspans.unit_of_point(index(name), point),
    "gridRouteFor": lambda name, value: gridspans.grid_route_for(index(name), grid(name), value),  # type: ignore[arg-type]
    "gridRequest": _grid_request,
    "gridRequestParsed": _grid_request,
    "resolveCustomGrid": _custom_grid,
    "resolveGridCell": lambda name, request, over=None: resolution(
        gridspans.resolve_grid_cell(index(over or name), grid(name), grid_request(request))  # type: ignore[arg-type]
    ),
    "resolveLocator": _resolve_locator,
    "resolveParsed": lambda name, fields, options=None: resolution(
        resolve.resolve_locator(parsed_of(fields), index(name), grid(name), **options_of(options))
    ),
    "valuePresence": _presence,
    "sliceSpan": _slice,
    "characterCount": layerreaders.character_count,
    "coverageOf": lambda sources, receipts, registry, unsupported: coverage(
        layercoverage.coverage_of(
            sources, {receipt["source_id"]: receipt for receipt in receipts}, registry, unsupported or ()
        )
    ),
    "marks": lambda held: [mark(found) for found in figures.marks(held)],
    "printsFigure": figures.prints_figure,
    "retag": _retag,
    "recordsFileFor": figures.records_file_for,
    "inputDigest": lambda inputs: figures.input_digest([figure_input(held) for held in inputs]),
    "changedInputs": lambda recorded, now: changed(
        figures.changed_inputs(
            [figure_input(held) for held in recorded], [figure_input(held) for held in now], Path()
        )
    ),
}
