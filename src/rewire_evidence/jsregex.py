r"""JavaScript patterns, run by Python's `re` and held to what Node matches.

The locator grammar is 28 patterns applied in a fixed order, and what each one matches is
the specification. Python's `re` reads most of the same syntax and means something else by
it in six places, so a pattern is not copied across. It is read here as JavaScript reads
it and written out again as a Python pattern that says the same thing.

- A class, an escape such as `\s` or `\p{L}`, a dot, and a letter under the `i` flag each
  match a set of characters. Which set follows the Unicode version Node was built with and
  JavaScript's own rules for case, and Python has other answers on both counts. So every
  set is data: `data/pattern-classes.json` holds the characters Node matches for each one,
  recorded in CI by `scripts/fixtures/record_pattern_fixture.mjs`, and the Python pattern
  is written with those ranges and no flags.
- `\b` is written out from the recorded `\w`, which under `iu` holds two more letters.
- `$` is the end of the text only. Python's also stands before a final newline.
- Without the `u` flag a pattern sees UTF-16 units, so a character beyond U+FFFF is two
  and `{1,60}` counts sixty units. Text is therefore matched in the form `js.unit_text`
  gives, one character to a unit. With the `u` flag a pattern sees whole characters, so the
  units are paired up for the match and the result is put back into units.
- A group captured inside a repeat keeps its last value in Python and is reset by
  JavaScript on each pass. That is refused.
- JavaScript does not count a pass of a repeat that matched nothing once the repeat has
  what it needs: it gives the pass up and tries another way, and Python keeps it. So
  `(?<g>\b)?` leaves the group unset in JavaScript and empty in Python. Any repeat of
  something that can match nothing is refused, `?` and `{0,1}` included.
- After a match of nothing, a global `replace` in JavaScript looks again one character
  further on. Python looks again at the same place for a match that is not empty, and a
  lazy repeat then takes a character JavaScript would have stepped over. `replace` is
  therefore written here as JavaScript's own loop over `search`, and `re.sub` is not used.

- With the `u` flag the specification looks for a match only where a character starts.
  Node 24.22.3 gives `/(?![^,;])/u.exec("a\u{1D400}")` the index 2, which is between the
  two units of the character, and the specification gives 3. That one case is recorded.
  The reading of it here, which is not recorded, is that V8 also looks between the units,
  where nothing that takes a character can match and a match of nothing can. What Node
  does is what this library is held to, and Python cannot stand between the units of a
  character it holds whole. So a pattern with the `u` flag that can match nothing is refused
  on text that holds such a character, and runs on any other text. A pattern that has to
  take a character is run on such text, and is held there by the recorded cases alone.

Every construct the port does not use is refused as well, and `Unsupported` names it. A
pattern this module accepts, on text it accepts, means what Node means by it.

`tests/fixtures/js-patterns.json` holds what Node matched for a list of patterns and texts,
written by the same recorder from `scripts/fixtures/pattern_cases.json`.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import resources

from rewire_evidence.js import paired, unit_text, units

#: Inclusive ranges of character codes.
type Ranges = tuple[tuple[int, int], ...]
#: The characters one atom matches under the flags that bear on it: `i`, `u`, both or neither.
type Lookup = Callable[[str, str], Ranges]

#: Where the recorded sets are, inside the package.
CLASSES_FILE = "data/pattern-classes.json"
_LAST_UNIT = 0xFFFF
_LAST_CHARACTER = 0x10FFFF
_FLAGS = frozenset("giu")
#: What a backslash may stand before to mean the character itself, with or without `u`.
_SYNTAX = "^$\\.*+?()[]{}|/"
_CONTROL = {"t": "\t", "n": "\n", "r": "\r", "v": "\v", "f": "\f"}
_SET_ESCAPES = "dDsSwW"
_REPEAT = re.compile(r"\{([0-9]+)(,([0-9]*))?\}")
_GROUP_NAME = re.compile(r"\?<([A-Za-z_][A-Za-z0-9_]*)>")
_HEX4 = re.compile(r"[0-9A-Fa-f]{4}")
_HEX2 = re.compile(r"[0-9A-Fa-f]{2}")
_BRACED = re.compile(r"\{([0-9A-Fa-f]{1,6})\}")
_PROPERTY = re.compile(r"\{[A-Za-z_=]+\}")
_SURROGATE = re.compile("[\ud800-\udfff]")
_NEVER = "(?!)"
#: A group that captures nothing, and the four assertions.
_OPENERS = ("?:", "?=", "?!", "?<=", "?<!")


class Unsupported(ValueError):  # noqa: N818 - it is a refusal to run a pattern, not an error in one
    """A pattern, or a part of one, that this module will not claim to run as JavaScript does."""


def _ranges(text: str) -> Ranges:
    return tuple(
        (int(first, 16), int(last or first, 16))
        for first, _, last in (held.partition("-") for held in text.split())
    )


@functools.cache
def _recorded() -> Mapping[tuple[str, str], Ranges]:
    text = resources.files("rewire_evidence").joinpath(CLASSES_FILE).read_text("utf-8")
    return {(mode, atom): _ranges(held) for mode, atom, held in json.loads(text)["classes"]}


def recorded(mode: str, atom: str) -> Ranges:
    """The characters Node matches for an atom, from the recording.

    Raises `Unsupported` for one that was not recorded. A new pattern has to be listed in
    `scripts/fixtures/pattern_requests.json` and recorded before it can be run.
    """
    try:
        return _recorded()[mode, atom]
    except KeyError:
        flags = f"the flags {mode}" if mode else "no flags"
        raise Unsupported(f"what {atom} matches under {flags} has not been recorded from Node") from None


def _code(code: int) -> str:
    return f"\\u{code:04x}" if code <= _LAST_UNIT else f"\\U{code:08x}"


def _set(ranges: Ranges) -> str:
    """A Python class holding exactly these characters, or a pattern that never matches."""
    if not ranges:
        return _NEVER
    return "[" + "".join(_code(a) if a == b else f"{_code(a)}-{_code(b)}" for a, b in ranges) + "]"


@dataclass(frozen=True)
class _Piece:
    """A part of a pattern, written for Python.

    `least` is the fewest characters it can match. `captures` says it holds a named group,
    and `width` that it matches characters at all, which an assertion does not.
    """

    text: str
    least: int = 0
    captures: bool = False
    width: bool = True


class _Reader:
    """One pass over a JavaScript pattern, writing the Python one."""

    def __init__(self, source: str, mode: str, lookup: Lookup) -> None:
        self.source = source
        self.mode = mode
        self.lookup = lookup
        self.at = 0

    def whole(self) -> _Piece:
        piece = self.alternatives()
        if self.at < len(self.source):
            raise self.refused("a bracket that closes nothing")
        return piece

    def refused(self, what: str) -> Unsupported:
        return Unsupported(f"/{self.source}/ has {what} at {self.at}")

    def peek(self) -> str:
        return self.source[self.at : self.at + 1]

    def taken(self, pattern: re.Pattern[str], what: str) -> re.Match[str]:
        found = pattern.match(self.source, self.at)
        if found is None:
            raise self.refused(what)
        self.at = found.end()
        return found

    def alternatives(self) -> _Piece:
        pieces = [self.sequence()]
        while self.peek() == "|":
            self.at += 1
            pieces.append(self.sequence())
        return _Piece(
            "|".join(piece.text for piece in pieces),
            min(piece.least for piece in pieces),
            any(piece.captures for piece in pieces),
        )

    def sequence(self) -> _Piece:
        pieces: list[_Piece] = []
        while self.peek() not in {"", "|", ")"}:
            pieces.append(self.repeated(self.atom()))
        return _Piece(
            "".join(piece.text for piece in pieces),
            sum(piece.least for piece in pieces),
            any(piece.captures for piece in pieces),
        )

    def atom(self) -> _Piece:
        character = self.peek()
        self.at += 1
        if character == "(":
            return self.group()
        if character == "[":
            return self.bracketed()
        if character == "\\":
            return self.escape()
        if character == ".":
            return self.matching(".")
        if character in "^$":
            return _Piece(r"\A" if character == "^" else r"\Z", width=False)
        if character in "*+?{}]":
            self.at -= 1
            raise self.refused(f"a {character} that follows nothing it can apply to")
        return self.literal(character)

    def matching(self, atom: str) -> _Piece:
        """One character out of a set: whichever characters Node matches for this atom."""
        return _Piece(_set(self.lookup(self.mode, atom)), least=1)

    def literal(self, character: str) -> _Piece:
        code = ord(character)
        if code > _LAST_UNIT and "u" not in self.mode:
            self.at -= 1
            raise self.refused("a character beyond U+FFFF, which is two units without the u flag")
        if "i" in self.mode:
            # Which other characters a letter matches is JavaScript's rule and is recorded.
            return self.matching(f"\\u{code:04X}" if code <= _LAST_UNIT else f"\\u{{{code:X}}}")
        return _Piece(re.escape(character), least=1)

    def bracketed(self) -> _Piece:
        start = self.at - 1
        while self.peek() != "]":
            if not self.peek():
                self.at = start
                raise self.refused("a class that is not closed")
            self.at += 2 if self.peek() == "\\" else 1
        self.at += 1
        return self.matching(self.source[start : self.at])

    def escape(self) -> _Piece:
        character = self.peek()
        self.at += 1
        if character == "b":
            word = _set(self.lookup(self.mode, "\\w"))
            return _Piece(f"(?:(?<={word})(?!{word})|(?<!{word})(?={word}))", width=False)
        if character and character in _SET_ESCAPES:
            return self.matching(f"\\{character}")
        if character in {"p", "P"} and "u" in self.mode:
            return self.matching(f"\\{character}{self.taken(_PROPERTY, 'a property with no name')[0]}")
        if character == "u":
            return self.literal(self.coded())
        if character == "x":
            return self.literal(chr(int(self.taken(_HEX2, "an \\x with no two digits")[0], 16)))
        if character in _CONTROL:
            return self.literal(_CONTROL[character])
        if character and character in _SYNTAX:
            return self.literal(character)
        self.at -= 2
        raise self.refused(f"the escape \\{character}, which this module does not run")

    def coded(self) -> str:
        """The character a `u` escape names, as four digits or, with the `u` flag, in braces."""
        if "u" in self.mode and self.peek() == "{":
            code = int(self.taken(_BRACED, "a \\u{ with no code in it")[1], 16)
        else:
            code = int(self.taken(_HEX4, "a \\u with no four digits")[0], 16)
        if code > _LAST_CHARACTER or _SURROGATE.match(chr(code)):
            raise self.refused("an escape for half a character, or for none")
        return chr(code)

    def group(self) -> _Piece:
        opened = self.at - 1
        for opener in _OPENERS:
            if self.source.startswith(opener, self.at):
                self.at += len(opener)
                inner = self.closed()
                if opener == "?:":
                    return _Piece(f"(?:{inner.text})", inner.least, inner.captures)
                return _Piece(f"({opener}{inner.text})", 0, inner.captures, width=False)
        named = _GROUP_NAME.match(self.source, self.at)
        if named is None:
            self.at = opened
            raise self.refused("a group with no name, or one this module does not run")
        self.at = named.end()
        inner = self.closed()
        return _Piece(f"(?P<{named[1]}>{inner.text})", inner.least, captures=True)

    def closed(self) -> _Piece:
        inner = self.alternatives()
        if self.peek() != ")":
            raise self.refused("a group that is not closed")
        self.at += 1
        return inner

    def repeated(self, piece: _Piece) -> _Piece:
        """The piece with the repeat that follows it, if one does."""
        mark = self.peek()
        if not mark or mark not in "*+?{":
            return piece
        if mark == "{":
            found = self.taken(_REPEAT, "a brace that is not a repeat")
            fewest = int(found[1])
            most = fewest if found[2] is None else int(found[3]) if found[3] else None
            written = found[0]
        else:
            self.at += 1
            fewest, most = {"*": (0, None), "+": (1, None), "?": (0, 1)}[mark]
            written = mark
        if self.peek() == "?":
            self.at += 1
            written += "?"
        if not piece.width:
            raise self.refused("a repeat of something that matches no characters")
        if piece.captures and (most is None or most > 1):
            raise self.refused("a named group inside a repeat, which JavaScript resets on each pass")
        if not piece.least:
            # JavaScript gives up a pass that matched nothing once the fewest are had, and
            # Python keeps it, so the two differ even where the repeat is `?`.
            raise self.refused("a repeat of something that can match nothing")
        return _Piece(piece.text + written, piece.least * fewest, piece.captures)


def _mode(source: str, flags: str) -> str:
    if not set(flags) <= _FLAGS or len(set(flags)) != len(flags):
        raise Unsupported(f"/{source}/{flags} has a flag this module does not run")
    return "".join(flag for flag in "iu" if flag in flags)


def translate(source: str, flags: str, lookup: Lookup = recorded) -> tuple[str, int]:
    """A JavaScript pattern as a Python one, and the fewest characters it can match.

    The Python pattern is compiled with no flags. `lookup` gives the characters each set
    matches, and is the recording unless a caller is listing what a pattern needs.
    """
    piece = _Reader(source, _mode(source, flags), lookup).whole()
    return piece.text, piece.least


def needed(patterns: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Every set a list of patterns asks for, as the recorder is to be asked for it, in order."""
    asked: set[tuple[str, str]] = set()

    def note(mode: str, atom: str) -> Ranges:
        asked.add((mode, atom))
        return ((0, 0),)

    for source, flags in patterns:
        translate(source, flags, note)
    return sorted(asked)


def requests(patterns: Iterable[tuple[str, str]]) -> str:
    """The text of `scripts/fixtures/pattern_requests.json` for these patterns: what to record."""
    asked = ",\n".join(f"  {json.dumps(list(held), ensure_ascii=True)}" for held in needed(patterns))
    return f'{{\n "classes": [\n{asked}\n ]\n}}\n'


@dataclass(frozen=True)
class Found:
    """A match, as `RegExp.prototype.exec` returns one. All text is in units."""

    #: Where the match starts, in UTF-16 units.
    index: int
    #: `match[0]`.
    text: str
    #: `match.groups`, with `None` for a group that took no part.
    groups: Mapping[str, str | None]


class JsPattern:
    """A JavaScript pattern. Compiled when first used, so declaring one reads no data."""

    def __init__(self, source: str, flags: str = "") -> None:
        """Hold a pattern as JavaScript writes it between the slashes, and its flags."""
        self.source = source
        self.flags = flags

    @functools.cached_property
    def _translated(self) -> tuple[re.Pattern[str], int]:
        text, least = translate(self.source, self.flags)
        try:
            return re.compile(text), least
        except re.error as error:
            # Python holds a lookbehind to one width and one name to one group.
            raise Unsupported(f"/{self.source}/{self.flags} cannot be written for Python: {error}") from error

    @property
    def least(self) -> int:
        """The fewest characters a match can hold."""
        return self._translated[1]

    def _characters(self, text: str) -> str:
        """The text to match on: paired into whole characters under the `u` flag, or as it is.

        Raises `Unsupported` for a pattern with the `u` flag that can match nothing, on text
        with a character beyond U+FFFF: Node was seen to find such a match inside the character.
        """
        if "u" not in self.flags or _SURROGATE.search(text) is None:
            return text
        characters = paired(text)
        if not self.least and len(characters) < len(text):
            raise Unsupported(
                f"/{self.source}/{self.flags} can match nothing, and V8 looks for that inside a character "
                "beyond U+FFFF, which this text has"
            )
        return characters

    def exec(self, text: str) -> Found | None:
        """`pattern.exec(text)` from the start of the text, which is in units."""
        characters = self._characters(text)
        found = self._translated[0].search(characters)
        if found is None:
            return None
        if characters is text:
            return Found(found.start(), found[0], found.groupdict())
        groups = {name: None if held is None else unit_text(held) for name, held in found.groupdict().items()}
        return Found(units(characters[: found.start()]), unit_text(found[0]), groups)

    def test(self, text: str) -> bool:
        """`pattern.test(text)` from the start of the text, which is in units."""
        return self.exec(text) is not None

    def all(self, text: str) -> list[Found]:
        """`[...text.matchAll(pattern)]`: every match in turn, for a pattern with the `g` flag.

        The text is in units. Each search starts where the last match ended, and one
        character further on when that match was of nothing, as `replace` does.
        """
        if "g" not in self.flags:
            # JavaScript throws a TypeError here, so there is no answer to give.
            raise Unsupported(f"/{self.source}/{self.flags} has no g flag, and matchAll needs one")
        characters = self._characters(text)
        pattern = self._translated[0]
        found: list[Found] = []
        at = 0
        while at <= len(characters) and (match := pattern.search(characters, at)) is not None:
            if characters is text:
                found.append(Found(match.start(), match[0], match.groupdict()))
            else:
                groups = {
                    name: None if held is None else unit_text(held)
                    for name, held in match.groupdict().items()
                }
                found.append(Found(units(characters[: match.start()]), unit_text(match[0]), groups))
            at = match.end() + (match.start() == match.end())
        return found

    def replace(self, text: str, replacement: str) -> str:
        """`text.replace(pattern, replacement)`: every match with the `g` flag, or the first.

        The text is in units and the replacement is written as it stands, with nothing in it
        read as a reference to the match.
        """
        characters = self._characters(text)
        replaced = self._replaced(characters, replacement)
        return replaced if characters is text else unit_text(replaced)

    def _replaced(self, text: str, replacement: str) -> str:
        """The loop of `RegExp.prototype[Symbol.replace]`, over text that is one item to a character.

        Each search starts where the last match ended, and one character further on when
        that match was of nothing, which is where `re.sub` differs. A search that starts
        part of the way along still sees the text before it, as `lastIndex` does: the start
        of the text is the start of the whole text, and a lookbehind reads what came before.
        """
        pattern = self._translated[0]
        every = "g" in self.flags
        written: list[str] = []
        kept = at = 0
        # A search from beyond the end finds nothing in JavaScript. Python would look at the end again.
        while at <= len(text) and (found := pattern.search(text, at)) is not None:
            written += (text[kept : found.start()], replacement)
            kept = found.end()
            at = kept + (found.start() == kept) if every else len(text) + 1
        return "".join((*written, text[kept:]))
