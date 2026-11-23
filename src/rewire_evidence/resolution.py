"""What a resolver returns: a span, or a refusal with one stated reason.

This is `shared/omics/spans/types.ts`. A locator says where a value came from in words a
person can read. A span says where it is in a layer, exactly, so a machine can check it
again with no model. A resolver turns the first into the second or says why it cannot.

Refusal is the common case. A resolver that guesses puts a wrong span on a record, and a
wrong span reads as a check that passed. So every refusal names one reason from a closed
list, and the measurement reports the list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rewire_evidence.offsets import Span

#: Which resolver read the locator, in the order the TypeScript declares them.
SPAN_KINDS: Final = (
    "table_cell",
    "grid_cell",
    "delimited_cell",
    "sheet_cell",
    "figure_caption",
    "section_paragraph",
    "page",
    "line_range",
    "quote",
    "json_value",
)

#: Why a locator did not reach a span. The order is how far a resolver got: the archive's own
#: answers, then this lane's reading of the locator, then a resolver that read the layer and
#: would not commit. Of two refusals the later in this list is kept.
REFUSAL_REASONS: Final = (
    "no_capture",
    "no_normaliser",
    "not_the_artifact",
    "layer_unusable",
    "layer_not_readable",
    "no_anchor",
    "no_resolver_for_kind",
    "no_column_geometry",
    "no_row_in_locator",
    "no_column_in_locator",
    "anchor_not_found",
    "anchor_ambiguous",
    "empty_table_window",
    "column_not_found",
    "column_ambiguous",
    "column_unaligned",
    "column_merged",
    "row_not_found",
    "row_ambiguous",
    "row_key_unmatched",
    "cell_empty",
    "line_out_of_range",
    "page_out_of_range",
    "section_not_found",
    "section_ambiguous",
    "paragraph_out_of_range",
    "quote_not_found",
    "quote_ambiguous",
    "empty_span",
    "json_not_parsed",
)

#: How a grid resolver found the table it read. `none` is a grid that was read and holds no
#: table the anchor names.
GRID_ROUTES: Final = ("label", "caption_window", "none")


def progress(reason: str) -> int:
    """How far a refusal got: its place in `REFUSAL_REASONS`."""
    return REFUSAL_REASONS.index(reason)


@dataclass(frozen=True)
class Refusal:
    """A refusal. `detail` is for a reader of the report and is never parsed. It is in units."""

    reason: str
    detail: str
    #: The layer the refusal was reached on, where one was read.
    layer_sha256: str | None = None
    #: Set where a table grid was read.
    grid_route: str | None = None


@dataclass(frozen=True)
class Resolved:
    """A span, with what the resolver crossed it against.

    `row_label` and `column_header` hold the layer's own text, in units, and not the
    locator's, so a report can compare the two.
    """

    kind: str
    span: Span
    layer_sha256: str
    #: The row label as the layer prints it, or `None` where the resolver crossed no row.
    row_label: str | None = None
    column_header: str | None = None
    #: How many places the resolver had to choose between, where it chose by cross-check.
    anchor_candidates: int | None = None
    grid_route: str | None = None


type Resolution = Resolved | Refusal


def refuse(reason: str, detail: str, layer_sha256: str | None = None) -> Refusal:
    """A refusal for one reason, on a layer where one was read."""
    return Refusal(reason, detail, layer_sha256)
