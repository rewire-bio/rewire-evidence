"""The text layer: one layer per stored object, so a claim can cite a span of characters in a file.

This is `shared/archive/layers/index.ts` and `text.ts`, with the entry points of `xml.ts` and
`html.ts`. `layer_of` is pure apart from the PDF path, which runs pdftotext: bytes go in, a
tree of layers comes out, and nothing is written.

Three rules a reader needs.

1. The class comes from the bytes. A served media type is a hint and the suffix is nothing.
2. A layer that equals its input is not a second object. Plain UTF-8 text passes through
   unchanged, so its layer SHA-256 is the object's own key, and the layer says `identity`.
3. A container has no text layer. It gives a node per member, and a member's layer is
   derived from the member. A gzipped PDF is three nodes.

`text-utf8@1` normalises the encoding to UTF-8 and changes nothing else. Line endings are
left alone, because CRLF is content, and Unicode is not normalised, because an ICU change
would then move every stored offset.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from rewire_evidence import grids, markup, pdftext
from rewire_evidence.archive import ArchiveError
from rewire_evidence.canonical import sha256_hex
from rewire_evidence.classify import Classification, classify, decode, encoding_of
from rewire_evidence.containers import DEFAULT_LIMITS, Limits, expand_container
from rewire_evidence.office import office_layer
from rewire_evidence.textlayer import Layer, LayerFailure, layer_from_text

TEXT_NORMALISER = "text-utf8@1"
#: How many container expansions deep to go. A .tar.gz is two.
MAX_DEPTH = 3


@dataclass
class LayerNode:
    """One run of bytes in the tree: an object, or a member of a container above it."""

    sha256: str
    #: The node's own bytes, so a member can be read without expanding the container again.
    content: bytes
    classification: Classification
    #: The text layer, or `None` when the class carries none.
    layer: Layer | None = None
    #: The table grid over the same bytes, where the class carries one and the document holds
    #: a table. A second layer beside the text layer, never instead of it.
    grid: Layer | None = None
    #: Why there is no layer, or `None` when there is one.
    unsupported: str | None = None
    #: Container members, each the root of its own node, with the path it sat at.
    members: list[tuple[str, LayerNode]] = field(default_factory=list)
    #: What could not be read. Never raised, always reported.
    problems: list[str] = field(default_factory=list)


def _decoded(data: bytes, encoding: str | None, name: str) -> tuple[str, str]:
    found = encoding or encoding_of(data)
    if not found:
        raise LayerFailure(f"{name} was given bytes that do not decode as text")
    return decode(data, found), found


def text_layer(data: bytes, encoding: str | None = None) -> Layer:
    """`text-utf8@1`: the text layer of plain text, markdown or source code."""
    text, found = _decoded(data, encoding, "textLayer")
    return layer_from_text(data, text, TEXT_NORMALISER, f"text-utf8@1 decode {found} to utf-8")


def xml_layer(data: bytes, encoding: str | None = None) -> Layer:
    """`europepmc-fulltext-xml@1`: the text content of JATS or other publisher XML, in document order."""
    text, _ = _decoded(data, encoding, "xmlLayer")
    scanned = markup.scan_markup(text, markup.XML_RULES)
    return layer_from_text(data, scanned, markup.XML_NORMALISER, markup.XML_DERIVATION)


def html_layer(data: bytes, encoding: str | None = None) -> Layer:
    """`html-text@1`: an HTML page to text, with a fixed list of elements removed."""
    text, _ = _decoded(data, encoding, "htmlLayer")
    scanned = markup.scan_markup(text, markup.HTML_RULES)
    return layer_from_text(data, scanned, markup.HTML_NORMALISER, markup.HTML_DERIVATION)


def _layer(node: LayerNode, limits: Limits, *, pdftotext: bool) -> None:
    """Give the node its text layer, or the reason it has none. Raises `LayerFailure`."""
    data, found = node.content, node.classification
    if found.kind == "pdf":
        if pdftotext:
            node.layer = pdftext.pdf_layer(data)
        else:
            node.unsupported = "pdftotext is not installed, so no PDF layer was made"
    elif found.kind == "xml":
        node.layer = xml_layer(data, found.encoding)
    elif found.kind == "html":
        node.layer = html_layer(data, found.encoding)
    elif found.kind == "text":
        node.layer = text_layer(data, found.encoding)
    elif found.kind == "office":
        # An Office package is a zip whose members nobody cites, so it gets one layer of
        # its own and no member objects.
        office = office_layer(data, limits)
        if not office:
            node.unsupported = "office package that the detector no longer recognises"
            return
        node.problems += office.problems
        node.layer = office.layer
        node.unsupported = office.unsupported


def layer_of(
    data: bytes,
    media_type: str | None = None,
    *,
    max_depth: int = MAX_DEPTH,
    limits: Limits = DEFAULT_LIMITS,
    pdftotext: bool | None = None,
) -> LayerNode:
    """The layer tree for one run of bytes.

    `media_type` is the Content-Type as served, where there is one. `pdftotext` overrides
    the question of whether pdftotext can be run: a run without it makes no PDF layers and
    says why, rather than making empty ones. Raises `classify.Undecodable`.
    """
    runs = pdftext.has_pdftotext() if pdftotext is None else pdftotext
    found = classify(data, media_type)
    node = LayerNode(sha256_hex(data), data, found, unsupported=found.unsupported)

    if found.kind == "container":
        if max_depth <= 0:
            node.unsupported = "container nested deeper than the expansion limit"
            return node
        try:
            expansion = expand_container(data, limits)
        except ArchiveError as error:
            node.unsupported = str(error)
            return node
        node.problems = expansion.problems
        if not expansion.members and not expansion.problems:
            node.unsupported = "container holds no readable member"
        for member in expansion.members:
            inner = layer_of(member.content, max_depth=max_depth - 1, limits=limits, pdftotext=runs)
            node.members.append((member.path, inner))
        return node

    try:
        _layer(node, limits, pdftotext=runs)
    except LayerFailure as error:
        node.unsupported = f"{found.kind} normaliser failed: {error}"
        node.layer = None
        return node
    if node.layer:
        if node.layer.characters == 0:
            node.problems.append(f"{node.layer.normaliser} produced an empty layer")
        # A grid is additional. A document whose tables cannot be read keeps its text layer
        # and records why.
        try:
            node.grid = grids.grid_layer(data, node.layer, found.encoding)
        except LayerFailure as error:
            node.problems.append(f"table grid over {node.layer.normaliser} failed: {error}")
    return node


def walk(root: LayerNode) -> Iterator[tuple[LayerNode, str | None, str | None]]:
    """Every node in the tree, parents before children.

    With each comes the digest of the container it came from and the path it sat at, both
    `None` for the root.
    """

    def visit(
        node: LayerNode, parent: str | None, path: str | None
    ) -> Iterator[tuple[LayerNode, str | None, str | None]]:
        yield node, parent, path
        for member_path, member in node.members:
            yield from visit(member, node.sha256, member_path)

    return visit(root, None, None)
