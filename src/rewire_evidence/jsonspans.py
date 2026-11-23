r"""The JSON Pointer resolver: the value a pointer addresses, in the JSON text itself.

This is `shared/omics/spans/json.ts`. A source that is a JSON document has no captions and
no column positions, and it does not need them: RFC 6901 names one value by the path to it.
The standard library reads a document into values and throws the positions away, so the
reader here keeps them. It checks the grammar as it goes and refuses a document it cannot
read, because a span taken from a document read wrongly is a wrong span.

All text is in units, the offsets are unit offsets, and a `\u` escape gives one unit, as
`String.fromCharCode` does.

The TypeScript's reader calls itself for each level of nesting, and a document nested some
thousands deep overflows Node's stack, which it catches and reports as a document that is
not JSON. The depth is the machine's. This reader keeps its own stack and reads such a
document, which is the one place the two can differ.
"""

from __future__ import annotations

import math
import re
import weakref
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from rewire_evidence.aligned import lower_units
from rewire_evidence.js import SPACE, parse_int
from rewire_evidence.offsets import LayerIndex, span_at
from rewire_evidence.regions import FILE_NORMALISERS
from rewire_evidence.resolution import Resolution, Resolved, refuse

#: The most values a document may hold before the reader stops. It guards memory.
MAX_NODES: Final = 4_000_000

_LITERAL: Final = re.compile(r"true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
#: How much of the text a number or a word is looked for in.
_LITERAL_UNITS: Final = 48
_SIMPLE: Final = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_BLANK: Final = " \n\r\t"
_UNITS: Final = 0x10000
_INDEX: Final = re.compile(r"0|[1-9][0-9]*")
_HEAD: Final = re.compile(r"[^\[\]]*")
_SUBSCRIPT: Final = re.compile(r"\[([0-9]+)\]")


class _NotJson(Exception):  # noqa: N818 - the reader's own word for text it gives up on
    """The text is not one JSON document this reader reads."""


@dataclass
class JsonNode:
    """One value of a JSON document, with where it sits in the text.

    `start` is the first unit of the value and `end` one past its last, quotes included for
    a string. `members` are an object's, in the order printed, duplicates kept. `text` is a
    scalar's decoded value, or the literal as printed for a number, a boolean or a null.
    """

    kind: str
    start: int
    end: int = 0
    members: list[tuple[str, JsonNode]] = field(default_factory=list)
    items: list[JsonNode] = field(default_factory=list)
    text: str | None = None
    #: The key read for the member whose value is being read.
    key: str = ""


class _Reader:
    """One pass over the text, as RFC 8259 writes the grammar."""

    def __init__(self, text: str, most: int) -> None:
        self.text = text
        self.most = most
        self.at = 0
        self.nodes = 0

    def read(self) -> JsonNode:
        self.space()
        node = self.value()
        self.space()
        if self.at != len(self.text):
            raise _NotJson
        return node

    def peek(self) -> str:
        return self.text[self.at : self.at + 1]

    def space(self) -> None:
        while self.peek() and self.peek() in _BLANK:
            self.at += 1

    def expect(self, character: str) -> None:
        if self.peek() != character:
            raise _NotJson
        self.at += 1

    def key(self, parent: JsonNode) -> None:
        """The key of an object's next member, and the colon after it."""
        self.space()
        parent.key = self.string()
        self.space()
        self.expect(":")
        self.space()

    def opened(self, kind: str, start: int, closer: str) -> JsonNode | None:
        """An object or an array just opened: the node where it is empty, or `None` and it is held."""
        self.at += 1
        node = JsonNode(kind, start)
        self.space()
        if self.peek() == closer:
            self.at += 1
            node.end = self.at
            return node
        self.held.append(node)
        if kind == "object":
            self.key(node)
        else:
            self.space()
        return None

    def scalar(self, start: int) -> JsonNode:
        if self.peek() == '"':
            text = self.string()
            return JsonNode("string", start, self.at, text=text)
        literal = _LITERAL.match(self.text[self.at : self.at + _LITERAL_UNITS])
        if not literal:
            raise _NotJson
        self.at += literal.end()
        kind = "boolean" if literal[0] in {"true", "false"} else "null" if literal[0] == "null" else "number"
        return JsonNode(kind, start, self.at, text=literal[0])

    def value(self) -> JsonNode:
        """One value. An object or an array is held open here while its values are read."""
        self.held: list[JsonNode] = []
        while True:
            self.nodes += 1
            if self.nodes > self.most:
                raise _NotJson
            start = self.at
            here = self.peek()
            if here in {"{", "["}:
                done = self.opened("object" if here == "{" else "array", start, "}" if here == "{" else "]")
                if done is None:
                    continue
            else:
                done = self.scalar(start)
            closed = self.closed(done)
            if closed is not None:
                return closed

    def closed(self, done: JsonNode) -> JsonNode | None:
        """Put a finished value where it belongs. The whole value where nothing is open, or `None`."""
        while self.held:
            parent = self.held[-1]
            if parent.kind == "object":
                parent.members.append((parent.key, done))
            else:
                parent.items.append(done)
            self.space()
            if self.peek() == ",":
                self.at += 1
                if parent.kind == "object":
                    self.key(parent)
                else:
                    self.space()
                return None
            self.expect("}" if parent.kind == "object" else "]")
            parent.end = self.at
            self.held.pop()
            done = parent
        return done

    def string(self) -> str:
        self.expect('"')
        out: list[str] = []
        while True:
            here = self.peek()
            if not here:
                raise _NotJson
            if here == '"':
                self.at += 1
                return "".join(out)
            if here != "\\":
                out.append(here)
                self.at += 1
                continue
            escape = self.text[self.at + 1 : self.at + 2]
            self.at += 2
            if escape == "u":
                code = parse_int(self.text[self.at : self.at + 4], 16)
                out.append(chr(0 if math.isnan(code) else int(code) % _UNITS))
                self.at += 4
            elif escape in _SIMPLE:
                out.append(_SIMPLE[escape])
            else:
                raise _NotJson


def read_json(text: str, most: int = MAX_NODES) -> JsonNode | None:
    """Read a document held in units, or `None` where it is not one JSON document."""
    try:
        return _Reader(text, most).read()
    except _NotJson:
        return None


_DOCUMENTS: Final[weakref.WeakKeyDictionary[LayerIndex, tuple[JsonNode | None]]] = weakref.WeakKeyDictionary()


def json_of(index: LayerIndex) -> JsonNode | None:
    """The document of a layer, read once for each index."""
    held = _DOCUMENTS.get(index)
    if held is None:
        held = _DOCUMENTS[index] = (read_json(index.text),)
    return held[0]


def unescape_token(token: str) -> str:
    """RFC 6901 section 4: `~1` is a solidus and `~0` a tilde, in that order."""
    return token.replace("~1", "/").replace("~0", "~")


def escape_token(token: str) -> str:
    """The inverse, for a pointer written into a message."""
    return token.replace("~", "~0").replace("/", "~1")


def render_pointer(tokens: Sequence[str]) -> str:
    """Reference tokens as the pointer that names them."""
    return "".join(f"/{escape_token(token)}" for token in tokens)


def pointer_tokens(path: str) -> list[str]:
    """Reference tokens from a path, in either spelling the store uses.

    `/a/b/0` is RFC 6901 and is read as it is. `a.b[0]` and `$.a.b[0]` are the dotted
    spelling older locators print, turned into the same tokens.
    """
    text = path.strip(SPACE)
    if text.startswith("/"):
        return [unescape_token(token) for token in text.split("/")[1:]]
    tokens: list[str] = []
    for part in text.split("."):
        head = _HEAD.match(part)
        if head and head[0]:
            tokens.append(head[0])
        tokens += _SUBSCRIPT.findall(part)
    if tokens and tokens[0] in {"$", "root"}:
        del tokens[0]
    return tokens


@dataclass(frozen=True)
class Step:
    """One reference token taken, or why it could not be: `reason` is set where there is no node."""

    node: JsonNode | None
    token: str
    reason: str = ""


def step(node: JsonNode, token: str) -> Step:
    """One reference token against one node. A key printed twice in an object is ambiguous."""
    if node.kind == "object":
        found = [held for key, held in node.members if key == token]
        if not found:
            return Step(None, token, "missing")
        if len(found) > 1:
            return Step(None, token, "duplicate")
        return Step(found[0], token)
    if node.kind == "array":
        if not _INDEX.fullmatch(token):
            return Step(None, token, "bad_index")
        at = int(token)
        if at >= len(node.items):
            return Step(None, token, "index_out_of_range")
        return Step(node.items[at], token)
    return Step(None, token, "not_a_container")


def walk(root: JsonNode, tokens: Sequence[str]) -> tuple[Step, int]:
    """Follow tokens from the root: the last step taken, and the place of a token that could not be."""
    node = root
    for at, token in enumerate(tokens):
        taken = step(node, token)
        if taken.node is None:
            return taken, at
        node = taken.node
    return Step(node, ""), len(tokens)


def scalars_of(node: JsonNode) -> list[str]:
    """The scalar values an object holds directly, or a scalar's own value."""
    if node.kind != "object":
        return [] if node.text is None else [node.text]
    return [held.text for _, held in node.members if held.text is not None]


@dataclass(frozen=True)
class JsonSelector:
    """A selector as the pointer resolver uses one: its key, its value and what the key names."""

    key: str
    value: str
    role: str


def _same_value(printed: str, wanted: str) -> bool:
    return printed == wanted or lower_units(printed.strip(SPACE)) == lower_units(wanted.strip(SPACE))


def _readings(node: JsonNode, value: str) -> list[JsonNode]:
    """Every node a selector's value names under a node, read as one key and as a path of keys."""
    found: list[JsonNode] = []
    whole = step(node, value)
    if whole.node is not None:
        found.append(whole.node)
    dot = value.find(".")
    while dot >= 0:
        head = step(node, value[:dot])
        if head.node is not None:
            found += _readings(head.node, value[dot + 1 :])
        dot = value.find(".", dot + 1)
    return found


def resolve_json_pointer(  # noqa: C901, PLR0912 - one reason to a return
    index: LayerIndex, path: str, selectors: Sequence[JsonSelector]
) -> Resolution:
    """The span of the value a JSON Pointer addresses.

    The pointer is followed token by token. An array it lands on is narrowed to one element
    by the selectors that name no column, and each selector that names a column is one step
    further in. What is left has to be a scalar, and every selector not used on the way has to
    be printed by the object the value sits in.
    """
    layer = index.layer_sha256
    if index.normaliser not in FILE_NORMALISERS:
        return refuse(
            "no_column_geometry", f"{index.normaliser} is not the JSON file the pointer addresses", layer
        )
    root = json_of(index)
    if root is None:
        return refuse("json_not_parsed", "the layer is not one JSON document", layer)
    tokens = pointer_tokens(path)
    if not tokens:
        return refuse("no_anchor", f'"{path}" names no reference token', layer)
    pointer = render_pointer(tokens)
    walked, at = walk(root, tokens)
    if walked.node is None:
        before = render_pointer(tokens[:at])
        if walked.reason == "duplicate":
            return refuse("column_ambiguous", f'{before} prints "{walked.token}" more than once', layer)
        return refuse("column_not_found", f'{before} has no "{walked.token}"', layer)

    node = walked.node
    parent = None if len(tokens) < 2 else walk(root, tokens[:-1])[0].node  # noqa: PLR2004 - a pointer of one token has no object above its value
    picked: str | None = None
    used: set[int] = set()
    if node.kind == "array":
        picks = [(at, selector) for at, selector in enumerate(selectors) if selector.role != "column"]
        named = " and ".join(f'"{selector.value}"' for _, selector in picks)
        if not picks:
            return refuse(
                "no_row_in_locator",
                f"{pointer} is an array and the locator names nothing to pick an element by",
                layer,
            )
        matched = [
            item
            for item in node.items
            if all(
                any(_same_value(printed, selector.value) for printed in scalars_of(item))
                for _, selector in picks
            )
        ]
        if not matched:
            return refuse("row_not_found", f"no element of {pointer} prints {named}", layer)
        if len(matched) > 1:
            return refuse("row_ambiguous", f"{len(matched)} elements of {pointer} print {named}", layer)
        parent = node = matched[0]
        picked = " / ".join(selector.value for _, selector in picks)
        used = {at for at, _ in picks}
    header: list[str] = []
    for at, selector in enumerate(selectors):
        if selector.role != "column":
            continue
        found = _readings(node, selector.value)
        if len(found) > 1:
            return refuse(
                "column_ambiguous",
                f'"{selector.value}" reads both as one key and as a path, and both are printed',
                layer,
            )
        if not found:
            return refuse(
                "column_not_found", f'nothing under {pointer} is named by "{selector.value}"', layer
            )
        parent = node
        node = found[0]
        header.append(selector.value)
        used.add(at)

    if node.kind in {"object", "array"}:
        stopped = "an array" if node.kind == "array" else "an object"
        return refuse(
            "column_not_found", f"{pointer} stops on {stopped} and a span over a value is wanted", layer
        )
    if node.kind == "null" or not node.text:
        return refuse("cell_empty", f"{pointer} prints nothing", layer)
    printed = [] if parent is None else scalars_of(parent)
    for at, selector in enumerate(selectors):
        if at not in used and not any(_same_value(text, selector.value) for text in printed):
            return refuse(
                "row_key_unmatched",
                f'{pointer} and {selector.key} "{selector.value}" name different places: '
                "the object the pointer lands in does not print it",
                layer,
            )
    return Resolved(
        "json_value",
        span_at(index, node.start, node.end),
        layer,
        row_label=picked,
        column_header=" / ".join(header) if header else tokens[-1],
    )
