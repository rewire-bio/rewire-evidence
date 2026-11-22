"""As much of zod 4 as the data repository's validators use, issue for issue.

The TypeScript declares the shape of an attribute, a profile, a run recipe and the rest as zod
schemas, and what it reports about a bad value is whatever zod reports: a code, a path and a
message, in zod's order. `attributeIssues` prints the first issue's message, so which issue
comes first is part of the contract. The validators that call `.parse` throw the whole list.

This follows zod 4.6.5's `core/schemas.js` and `core/checks.js`. The rules that decide what
is reported, and are easy to get wrong:

- An issue either lets later checks run or stops them. A value of the wrong type stops them.
  A failed check does not, so text that is too short and also matches no pattern is two
  issues. A number that is not whole stops every check after `int`.
- A length check runs on any value that has a `length`, whatever type was asked for, so a
  list given where text was wanted is reported for its type and may be reported for its
  length too.
- A check added by `superRefine` runs only if nothing has stopped the checks: a member of
  the wrong type stops it, a member that failed its own check or an unrecognised key does not.
- A union reports the issues of one option if only that option got as far as its checks, and
  `Invalid input` otherwise.
- An object's issues come in the order its members are declared, then its unrecognised keys
  in one issue, in the order JavaScript lists them.

`tests/fixtures/validation.json` holds every schema here against a valid value damaged in
every place, recorded from zod, and the tests hold this module to it.
"""

from __future__ import annotations

import abc
import copy
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final

from rewire_evidence.canonical import EXACT_INTEGER_LIMIT, JsonValue, js_number
from rewire_evidence.js import MISSING, Held, Missing, is_integer, number_of, received, trim, units
from rewire_evidence.schema import Issue
from rewire_evidence.weburl import can_parse

type Path = tuple[str | int, ...]

#: An issue lets later checks run (`True`), stops them (`None`), or stops even the checks
#: that run on a value of the wrong type (`False`). zod calls this `continue`.
type Proceed = bool | None


@dataclass
class Raised:
    """An issue as zod holds it while parsing: what it says, and whether checks go on."""

    code: str
    path: Path
    message: str
    proceed: Proceed = None

    def under(self, key: str | int) -> Raised:
        """The same issue, one level further in."""
        return replace(self, path=(key, *self.path))


@dataclass
class Payload:
    """A value on its way through a schema: what it is now, and what has been said of it."""

    value: Held
    issues: list[Raised] = field(default_factory=list)


def _aborted(issues: Sequence[Raised], start: int = 0) -> bool:
    return any(issue.proceed is not True for issue in issues[start:])


def _explicitly_aborted(issues: Sequence[Raised]) -> bool:
    return any(issue.proceed is False for issue in issues)


@dataclass(frozen=True)
class Check:
    """One check of a schema. `when` is zod's condition for running it on a stopped value."""

    run: Callable[[Payload], None]
    when: Callable[[Held], bool] | None = None


def _wrong_type(expected: str, value: Held) -> Raised:
    return Raised("invalid_type", (), f"Invalid input: expected {expected}, received {received(value)}")


@dataclass(frozen=True)
class Schema(abc.ABC):
    """A zod schema: a type, and the checks that follow it in the order they were added."""

    checks: tuple[Check, ...] = ()

    #: Whether an object may leave this member out, which zod calls `optin` and `optout`.
    optional: bool = field(default=False, init=False)

    @abc.abstractmethod
    def _parse(self, payload: Payload) -> Payload:
        """The value held to the type alone, with an issue added if it is not of it."""

    def run(self, value: Held) -> Payload:
        """The value parsed and then checked, as zod's `run` does going forward."""
        payload = self._parse(Payload(value))
        aborted = _aborted(payload.issues)
        for check in self.checks:
            if check.when is not None:
                if _explicitly_aborted(payload.issues) or not check.when(payload.value):
                    continue
            elif aborted:
                continue
            before = len(payload.issues)
            check.run(payload)
            if not aborted:
                aborted = _aborted(payload.issues, before)
        return payload

    def check(self, *checks: Check) -> Schema:
        """This schema with more checks after the ones it has."""
        return replace(self, checks=(*self.checks, *checks))

    def issues(self, value: Held) -> tuple[Issue, ...]:
        """`safeParse(value)`: the issues zod reports, in its order, and none if it parses."""
        return tuple(Issue(issue.code, issue.path, issue.message) for issue in self.run(value).issues)

    def accepts(self, value: Held) -> bool:
        """`safeParse(value).success`."""
        return not self.run(value).issues


# ---- checks ---------------------------------------------------------------------------


def _has_length(value: Held) -> bool:
    return isinstance(value, (str, list)) or (isinstance(value, dict) and "length" in value)


def _length(value: Held, floor: int, ceiling: int) -> float:
    """What zod compares: code points for text near the limit, units otherwise, `length` as it is."""
    if isinstance(value, str):
        count = units(value)
        return len(value) if floor <= count <= ceiling else count
    if isinstance(value, list):
        return len(value)
    assert isinstance(value, dict)  # noqa: S101 - `_has_length` let nothing else through
    return number_of(value["length"])


def _sized(value: Held, relation: str, limit: int) -> str:
    if isinstance(value, str):
        return f"string to have {relation}{limit} characters"
    if isinstance(value, list):
        return f"array to have {relation}{limit} items"
    return f"unknown to be {relation}{limit}"


def min_length(minimum: int) -> Check:
    """`.min(n)` on text or a list."""

    def run(payload: Payload) -> None:
        if not _length(payload.value, minimum, minimum * 2 - 1) >= minimum:
            payload.issues.append(
                Raised(
                    "too_small",
                    (),
                    f"Too small: expected {_sized(payload.value, '>=', minimum)}",
                    proceed=True,
                )
            )

    return Check(run, _has_length)


def max_length(maximum: int) -> Check:
    """`.max(n)` on text or a list."""

    def run(payload: Payload) -> None:
        if not _length(payload.value, maximum + 1, math.inf) <= maximum:  # type: ignore[arg-type]
            payload.issues.append(
                Raised(
                    "too_big", (), f"Too big: expected {_sized(payload.value, '<=', maximum)}", proceed=True
                )
            )

    return Check(run, _has_length)


def pattern(compiled: re.Pattern[str], source: str, message: str | None = None) -> Check:
    """`.regex(/source/)`. `compiled` is the pattern in Python's terms, matched whole."""
    said = message or f"Invalid string: must match pattern /{source}/"

    def run(payload: Payload) -> None:
        assert isinstance(payload.value, str)  # noqa: S101 - skipped unless the value is text
        if not compiled.fullmatch(payload.value):
            payload.issues.append(Raised("invalid_format", (), said, proceed=True))

    return Check(run)


def _url(payload: Payload) -> None:
    assert isinstance(payload.value, str)  # noqa: S101 - skipped unless the value is text
    if not can_parse(trim(payload.value)):
        payload.issues.append(Raised("invalid_format", (), "Invalid URL", proceed=True))


def _trimmed(payload: Payload) -> None:
    assert isinstance(payload.value, str)  # noqa: S101 - skipped unless the value is text
    payload.value = trim(payload.value)


#: `.url()`: text the URL parser reads once trimmed.
URL: Final = Check(_url)
#: `.trim()`: the value is trimmed before the checks after this one.
TRIM: Final = Check(_trimmed)

_DATE = (
    r"(?:(?:[0-9][0-9][2468][048]|[0-9][0-9][13579][26]|[0-9][0-9]0[48]|[02468][048]00|[13579][26]00)-02-29"
    r"|[0-9]{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])|(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)"
    r"|(?:02)-(?:0[1-9]|1[0-9]|2[0-8])))"
)
_TIME = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?"
_DATETIME = re.compile(rf"{_DATE}T(?:{_TIME}(?:Z|([+-](?:[01][0-9]|2[0-3]):[0-5][0-9])))")


def _datetime(payload: Payload) -> None:
    assert isinstance(payload.value, str)  # noqa: S101 - skipped unless the value is text
    if not _DATETIME.fullmatch(payload.value):
        payload.issues.append(Raised("invalid_format", (), "Invalid ISO datetime", proceed=True))


#: `.datetime({ offset: true })`: a real date, a time with seconds, and `Z` or an offset.
DATETIME: Final = Check(_datetime)


def _number(payload: Payload) -> float:
    assert isinstance(payload.value, (int, float))  # noqa: S101 - skipped unless it is a number
    return payload.value


def _int(payload: Payload) -> None:
    value = payload.value
    if not is_integer(value):  # type: ignore[arg-type]
        payload.issues.append(
            Raised("invalid_type", (), "Invalid input: expected int, received number", proceed=False)
        )
    elif abs(_number(payload)) > EXACT_INTEGER_LIMIT - 1:
        if _number(payload) > 0:
            payload.issues.append(
                Raised(
                    "too_big", (), f"Too big: expected int to be <={EXACT_INTEGER_LIMIT - 1}", proceed=True
                )
            )
        else:
            payload.issues.append(
                Raised(
                    "too_small",
                    (),
                    f"Too small: expected int to be >=-{EXACT_INTEGER_LIMIT - 1}",
                    proceed=True,
                )
            )


#: `.int()`: a whole number a double holds exactly.
INT: Final = Check(_int)


def greater(than: float, *, inclusive: bool) -> Check:
    """`.gt(n)` and `.gte(n)`, which `.min`, `.positive` and `.nonnegative` are."""

    def run(payload: Payload) -> None:
        value = _number(payload)
        if not (value >= than if inclusive else value > than):
            sign = ">=" if inclusive else ">"
            payload.issues.append(
                Raised(
                    "too_small", (), f"Too small: expected number to be {sign}{js_number(than)}", proceed=True
                )
            )

    return Check(run)


def less(than: float, *, inclusive: bool) -> Check:
    """`.lt(n)` and `.lte(n)`, which `.max` is."""

    def run(payload: Payload) -> None:
        value = _number(payload)
        if not (value <= than if inclusive else value < than):
            sign = "<=" if inclusive else "<"
            payload.issues.append(
                Raised("too_big", (), f"Too big: expected number to be {sign}{js_number(than)}", proceed=True)
            )

    return Check(run)


#: What a refinement is handed to report with: a message, and a path below the value.
type Report = Callable[..., None]


def refinement(test: Callable[[JsonValue, Report], None]) -> Check:
    """`.superRefine(fn)` and `.refine(fn, message)`: issues of code `custom` that stop nothing."""

    def run(payload: Payload) -> None:
        def report(message: str, *path: str | int) -> None:
            payload.issues.append(Raised("custom", path, message, proceed=True))

        assert not isinstance(payload.value, Missing)  # noqa: S101 - a refined schema parsed it
        test(payload.value, report)

    return Check(run)


# ---- types ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Text(Schema):
    """`z.string()`."""

    def _parse(self, payload: Payload) -> Payload:
        if not isinstance(payload.value, str):
            payload.issues.append(_wrong_type("string", payload.value))
        return payload


@dataclass(frozen=True)
class Number(Schema):
    """`z.number()`: a finite number. `JSON.parse` gives no other but one beyond every double."""

    def _parse(self, payload: Payload) -> Payload:
        value = payload.value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isinf(value):
            payload.issues.append(_wrong_type("number", value))
        return payload


@dataclass(frozen=True)
class Boolean(Schema):
    """`z.boolean()`."""

    def _parse(self, payload: Payload) -> Payload:
        if not isinstance(payload.value, bool):
            payload.issues.append(_wrong_type("boolean", payload.value))
        return payload


@dataclass(frozen=True)
class Unknown(Schema):
    """`z.unknown()`: anything, and nothing at all."""

    def _parse(self, payload: Payload) -> Payload:
        return payload


def _same(value: Held, option: JsonValue) -> bool:
    """Whether a value is the option, as a JavaScript `Set` finds it: no number is a boolean."""
    return type(value) is type(option) and value == option


def _quoted(option: JsonValue) -> str:
    return f'"{option}"' if isinstance(option, str) else js_number(option)  # type: ignore[arg-type]


@dataclass(frozen=True)
class OneOf(Schema):
    """`z.enum([...])` and `z.literal(value)`: one of the values listed."""

    options: tuple[JsonValue, ...] = ()

    def _parse(self, payload: Payload) -> Payload:
        if not any(_same(payload.value, option) for option in self.options):
            if len(self.options) == 1:
                message = f"Invalid input: expected {_quoted(self.options[0])}"
            else:
                message = (
                    f"Invalid option: expected one of {'|'.join(_quoted(option) for option in self.options)}"
                )
            payload.issues.append(Raised("invalid_value", (), message))
        return payload


@dataclass(frozen=True)
class Array(Schema):
    """`z.array(element)`."""

    element: Schema = field(default_factory=Unknown)

    def _parse(self, payload: Payload) -> Payload:
        value = payload.value
        if not isinstance(value, list):
            payload.issues.append(_wrong_type("array", value))
            return payload
        parsed: list[JsonValue] = []
        for position, item in enumerate(value):
            result = self.element.run(item)
            payload.issues.extend(issue.under(position) for issue in result.issues)
            parsed.append(_kept(result.value))
        payload.value = parsed
        return payload


def _kept(value: Held) -> JsonValue:
    """A parsed value as it is put in a list or a record, where there is always something."""
    return None if isinstance(value, Missing) else value


@dataclass(frozen=True)
class Tuple(Schema):
    """`z.tuple([...])` with every item required."""

    items: tuple[Schema, ...] = ()

    def _parse(self, payload: Payload) -> Payload:
        value = payload.value
        if not isinstance(value, list):
            payload.issues.append(_wrong_type("tuple", value))
            return payload
        count = len(self.items)
        if len(value) < count:
            payload.issues.append(
                Raised("too_small", (), f"Too small: expected array to have >={count} items")
            )
            return payload
        if len(value) > count:
            payload.issues.append(Raised("too_big", (), f"Too big: expected array to have <={count} items"))
        for position, item in enumerate(self.items):
            payload.issues.extend(issue.under(position) for issue in item.run(value[position]).issues)
        return payload


@dataclass(frozen=True)
class Record(Schema):
    """`z.record(z.string(), value)`: any member names, each value held to one schema.

    A member named `__proto__` is passed over, as zod passes over it.
    """

    value: Schema = field(default_factory=Unknown)

    def _parse(self, payload: Payload) -> Payload:
        held = payload.value
        if not isinstance(held, dict):
            payload.issues.append(_wrong_type("record", held))
            return payload
        parsed: dict[str, JsonValue] = {}
        for key, member in held.items():
            if key == "__proto__":
                continue
            result = self.value.run(member)
            payload.issues.extend(issue.under(key) for issue in result.issues)
            parsed[key] = _kept(result.value)
        payload.value = parsed
        return payload


@dataclass(frozen=True)
class Object(Schema):
    """`z.object({...}).strict()`: the members declared, in that order, and no others."""

    shape: Mapping[str, Schema] = field(default_factory=dict)

    def _parse(self, payload: Payload) -> Payload:
        held = payload.value
        if not isinstance(held, dict):
            payload.issues.append(_wrong_type("object", held))
            return payload
        parsed: dict[str, JsonValue] = {}
        for key, member in self.shape.items():
            present = key in held
            if not present and member.optional:
                continue
            result = member.run(held.get(key, MISSING))
            payload.issues.extend(issue.under(key) for issue in result.issues)
            if not present:
                if isinstance(member, Default):
                    parsed[key] = _kept(result.value)
                elif not result.issues:
                    payload.issues.append(
                        Raised(
                            "invalid_type", (key,), "Invalid input: expected nonoptional, received undefined"
                        )
                    )
                continue
            parsed[key] = _kept(result.value)
        unknown = [key for key in held if key not in self.shape]
        if unknown:
            listed = ", ".join(f'"{key}"' for key in unknown)
            payload.issues.append(
                Raised(
                    "unrecognized_keys",
                    (),
                    f"Unrecognized key{'s' if len(unknown) > 1 else ''}: {listed}",
                    proceed=True,
                )
            )
        payload.value = parsed
        return payload


@dataclass(frozen=True)
class Union(Schema):
    """`z.union([...])`: the first option that parses."""

    options: tuple[Schema, ...] = ()

    def _parse(self, payload: Payload) -> Payload:
        results = []
        for option in self.options:
            result = option.run(payload.value)
            if not result.issues:
                return result
            results.append(result)
        checked = [result for result in results if not _aborted(result.issues)]
        if len(checked) == 1:
            return checked[0]
        payload.issues.append(Raised("invalid_union", (), "Invalid input"))
        return payload


@dataclass(frozen=True)
class Optional(Schema):
    """`schema.optional()`: the member may be left out."""

    inner: Schema = field(default_factory=Unknown)
    optional: bool = field(default=True, init=False)

    def _parse(self, payload: Payload) -> Payload:
        return payload if isinstance(payload.value, Missing) else self.inner.run(payload.value)


@dataclass(frozen=True)
class Nullable(Schema):
    """`schema.nullable()`: null, or the schema."""

    inner: Schema = field(default_factory=Unknown)

    def _parse(self, payload: Payload) -> Payload:
        return payload if payload.value is None else self.inner.run(payload.value)


@dataclass(frozen=True)
class Default(Schema):
    """`schema.default(value)`: a member left out is read as the value, which is not checked."""

    inner: Schema = field(default_factory=Unknown)
    value: JsonValue = None

    def _parse(self, payload: Payload) -> Payload:
        if isinstance(payload.value, Missing):
            payload.value = copy.deepcopy(self.value)
            return payload
        return self.inner.run(payload.value)


# ---- the spellings the validators use -------------------------------------------------


def text(*checks: Check) -> Schema:
    """`z.string()` with checks."""
    return Text(checks)


def number(*checks: Check) -> Schema:
    """`z.number()` with checks."""
    return Number(checks)


def one_of(*options: JsonValue) -> Schema:
    """`z.enum([...])`, or `z.literal(value)` for one."""
    return OneOf(options=options)


def array(element: Schema, *checks: Check) -> Schema:
    """`z.array(element)` with checks."""
    return Array(checks, element)


def strict(shape: Mapping[str, Schema], *checks: Check) -> Schema:
    """`z.object(shape).strict()` with checks."""
    return Object(checks, shape)


def record(value: Schema, *checks: Check) -> Schema:
    """`z.record(z.string(), value)` with checks."""
    return Record(checks, value)


def union(*options: Schema) -> Schema:
    """`z.union([...])`."""
    return Union(options=options)


def optional(inner: Schema) -> Schema:
    """`inner.optional()`."""
    return Optional(inner=inner)


def nullable(inner: Schema) -> Schema:
    """`inner.nullable()`."""
    return Nullable(inner=inner)


def default(inner: Schema, value: JsonValue) -> Schema:
    """`inner.default(value)`."""
    return Default(inner=inner, value=value)


def nullish(inner: Schema) -> Schema:
    """`inner.nullable().optional()`."""
    return optional(nullable(inner))


#: `z.string().min(1)`, the commonest schema there is.
NONEMPTY: Final = text(min_length(1))
POSITIVE: Final = greater(0, inclusive=False)
NONNEGATIVE: Final = greater(0, inclusive=True)
