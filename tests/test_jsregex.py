"""JavaScript patterns run through `re`, held to what Node matched.

`tests/fixtures/js-patterns.json` is written by `scripts/fixtures/record_pattern_fixture.mjs`
with Node's built-ins, from the patterns and texts in `scripts/fixtures/pattern_cases.json`.
Every expected match here is something Node returned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import jsregex, locator
from rewire_evidence.js import paired, unit_text
from rewire_evidence.jsregex import Found, JsPattern, Unsupported

HERE = Path(__file__).parent
RECORDED: dict[str, Any] = json.loads((HERE / "fixtures/js-patterns.json").read_text("utf-8"))
CASES: dict[str, Any] = json.loads((HERE.parent / "scripts/fixtures/pattern_cases.json").read_text("utf-8"))
REQUESTS = HERE.parent / "scripts/fixtures/pattern_requests.json"
PAIRS: list[tuple[dict[str, Any], dict[str, Any]]] = [
    (held, case) for held in RECORDED["patterns"] for case in held["cases"]
]


def named(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[Any]:
    return [
        pytest.param(held, case, id=f"/{held['source']}/{held['flags']} on {case['text']!r}")
        for held, case in pairs
    ]


MATCHES = named(PAIRS)


def every_pattern() -> list[tuple[str, str]]:
    """The patterns the library runs and the ones the cases test the translation with."""
    return [
        *((held.source, held.flags) for held in locator.PATTERNS),
        *((case["source"], case["flags"]) for case in CASES["patterns"]),
    ]


def as_recorded(found: Found | None) -> dict[str, Any] | None:
    if found is None:
        return None
    return {
        "index": found.index,
        "text": paired(found.text),
        "groups": {name: None if held is None else paired(held) for name, held in found.groups.items()},
    }


@pytest.mark.parametrize(("held", "case"), MATCHES)
def test_a_pattern_matches_what_node_matches(held: dict[str, Any], case: dict[str, Any]) -> None:
    text = unit_text(case["text"])
    found = JsPattern(held["source"], held["flags"].replace("g", "")).exec(text)
    assert as_recorded(found) == case["found"]
    assert JsPattern(held["source"], held["flags"]).test(text) is (case["found"] is not None)
    if found is not None:
        assert text[found.index : found.index + len(found.text)] == found.text


@pytest.mark.parametrize(("held", "case"), named([pair for pair in PAIRS if "replaced" in pair[1]]))
def test_a_pattern_replaces_what_node_replaces(held: dict[str, Any], case: dict[str, Any]) -> None:
    replaced = JsPattern(held["source"], held["flags"]).replace(unit_text(case["text"]), held["replacement"])
    assert paired(replaced) == case["replaced"]


def test_the_recording_is_of_the_committed_cases_and_of_enough_of_them() -> None:
    assert RECORDED["recorded_from"]["script"] == "scripts/fixtures/record_pattern_fixture.mjs"
    assert [
        (held["source"], held["flags"], [case["text"] for case in held["cases"]])
        for held in RECORDED["patterns"]
    ] == [(case["source"], case["flags"], case["texts"]) for case in CASES["patterns"]]
    assert len(MATCHES) > 190
    modes = {"".join(sorted(set(held["flags"]) - {"g"})) for held in RECORDED["patterns"]}
    assert modes == {"", "i", "u", "iu"}
    assert any(case["found"] is None for held in RECORDED["patterns"] for case in held["cases"])


def test_the_list_of_what_to_record_is_what_the_patterns_need() -> None:
    wanted = jsregex.requests(every_pattern())
    # Stale? Run scripts/fixtures/list_pattern_requests.py, push, and commit what CI records.
    assert REQUESTS.read_text("utf-8") == wanted
    listed = [tuple(held) for held in json.loads(wanted)["classes"]]
    assert listed == jsregex.needed(every_pattern())
    assert listed == sorted(set(listed))


def test_every_set_asked_for_was_recorded_and_no_other() -> None:
    held = json.loads((HERE.parent / "src/rewire_evidence" / jsregex.CLASSES_FILE).read_text("utf-8"))
    assert held["recorded_from"]["script"] == "scripts/fixtures/record_pattern_fixture.mjs"
    assert held["recorded_from"]["node"] == RECORDED["recorded_from"]["node"]
    assert [(mode, atom) for mode, atom, _ in held["classes"]] == jsregex.needed(every_pattern())


def test_a_set_is_the_characters_node_matched_and_the_flags_decide_it() -> None:
    assert jsregex.recorded("", "[A-Z]") == ((0x41, 0x5A),)
    assert jsregex.recorded("", "[]") == ()
    assert jsregex.recorded("", "\\d") == ((0x30, 0x39),)
    # Under both flags a long s and a Kelvin sign are word characters. Under one they are not.
    assert (0x17F, 0x17F) in jsregex.recorded("iu", "\\w")
    assert (0x212A, 0x212A) in jsregex.recorded("iu", "\\w")
    assert jsregex.recorded("i", "\\w") == jsregex.recorded("", "\\w")
    # Without the u flag a set is of units, and with it of characters.
    assert jsregex.recorded("", "[^a]")[-1][1] == 0xFFFF
    assert jsregex.recorded("u", "[^a]")[-1][1] == 0x10FFFF


def test_a_pattern_is_written_for_python_with_no_flags_to_rely_on() -> None:
    def lookup(_mode: str, atom: str) -> jsregex.Ranges:
        return {"[ab]": ((0x61, 0x62),), "\\w": ((0x61, 0x7A), (0x1F600, 0x1F600)), "[]": ()}[atom]

    assert jsregex.translate("^a$", "", lookup) == ("\\Aa\\Z", 1)
    assert jsregex.translate("[ab]{2,3}?|x", "", lookup) == ("[\\u0061-\\u0062]{2,3}?|x", 1)
    assert jsregex.translate("(?<n>a.c)?", "", lambda _mode, _atom: ((0x41, 0x41),)) == (
        "(?P<n>a[\\u0041]c)?",
        0,
    )
    assert jsregex.translate("[]", "", lookup) == ("(?!)", 1)
    word = "[\\u0061-\\u007a\\U0001f600]"
    assert jsregex.translate("\\b", "u", lookup) == (f"(?:(?<={word})(?!{word})|(?<!{word})(?={word}))", 0)
    assert jsregex.translate("a+(?:bc|d)*(?=e)(?!f)(?<=g)(?<!h)", "g", lookup)[1] == 1
    assert jsregex.translate("\\.\\$\\/", "u", lookup) == ("\\.\\$/", 3)


def test_the_fewest_characters_a_pattern_can_match_are_counted() -> None:
    assert JsPattern("a{2,3}b?").least == 2
    assert JsPattern("(?:ab|c)(?=d)").least == 1
    assert JsPattern("a*").least == 0
    assert JsPattern("(?<n>a){0,1}(?:b{3}){2}").least == 6


UNSUPPORTED = [
    ("a", "m", "a flag"),
    ("a", "s", "a flag"),
    ("a", "y", "a flag"),
    ("a", "ii", "a flag"),
    ("a)", "", "a bracket that closes nothing"),
    ("*a", "", "follows nothing"),
    ("a|+", "", "follows nothing"),
    ("(?:?)", "", "follows nothing"),
    ("{", "", "follows nothing"),
    ("a}", "", "follows nothing"),
    ("a]", "", "follows nothing"),
    ("a**", "", "follows nothing"),
    ("a{2}{3}", "", "follows nothing"),
    ("a\U0001f600", "", "two units"),
    ("a\U0001f600", "i", "two units"),
    ("[abc", "", "a class that is not closed"),
    ("[abc\\", "", "a class that is not closed"),
    ("a\\B", "", "the escape \\B"),
    ("(?<a>x)\\1", "", "the escape \\1"),
    ("\\k<a>", "", "the escape \\k"),
    ("\\cA", "", "the escape \\c"),
    ("\\p{L}", "", "the escape \\p"),
    ("\\P{L}", "i", "the escape \\P"),
    ("\\-", "u", "the escape \\-"),
    ("a\\", "", "the escape \\"),
    ("\\pL", "u", "a property with no name"),
    ("\\u12", "", "no four digits"),
    ("\\u{41}", "", "no four digits"),
    ("\\u{}", "u", "no code in it"),
    ("\\u{110000}", "u", "half a character, or for none"),
    ("\\ud83d", "", "half a character, or for none"),
    ("\\x4", "", "no two digits"),
    ("(a)", "", "a group with no name"),
    ("(?i:a)", "", "a group with no name"),
    ("(?<1a>x)", "", "a group with no name"),
    ("(?<a>x", "", "a group that is not closed"),
    ("(?:a", "", "a group that is not closed"),
    ("(?=a", "", "a group that is not closed"),
    ("a{,2}", "", "a brace that is not a repeat"),
    ("a{x}", "", "a brace that is not a repeat"),
    ("^*", "", "matches no characters"),
    ("\\b+", "", "matches no characters"),
    ("(?=a)?", "", "matches no characters"),
    ("(?<a>x)*", "", "a named group inside a repeat"),
    ("(?:b|(?<a>x))+", "", "a named group inside a repeat"),
    ("(?<a>x){2}", "", "a named group inside a repeat"),
    ("(?<a>x){0,}?", "", "a named group inside a repeat"),
    ("(?:a?)*", "", "can match nothing"),
    ("(?:a|)+", "", "can match nothing"),
    ("(?:a*){2,3}", "", "can match nothing"),
    ("[qz]", "", "what [qz] matches under no flags has not been recorded"),
    ("\\p{Lu}", "iu", "what \\p{Lu} matches under the flags iu has not been recorded"),
    ("q", "i", "what \\u0071 matches under the flags i has not been recorded"),
    ("\U0001f601", "iu", "what \\u{1F601} matches under the flags iu has not been recorded"),
    ("(?<=a|bc)d", "", "cannot be written for Python"),
    ("(?<a>x)|(?<a>y)", "", "cannot be written for Python"),
]


@pytest.mark.parametrize(("source", "flags", "said"), UNSUPPORTED)
def test_a_pattern_python_would_run_differently_is_refused(source: str, flags: str, said: str) -> None:
    with pytest.raises(Unsupported) as refused:
        JsPattern(source, flags).exec("a")
    assert said in str(refused.value)
    assert source in str(refused.value) or "recorded" in str(refused.value)


def test_a_pattern_is_read_only_when_it_is_first_run() -> None:
    held = JsPattern("(a)")
    assert (held.source, held.flags) == ("(a)", "")
    with pytest.raises(Unsupported):
        held.test("a")


AWKWARD = st.sampled_from(
    [
        "a",
        "b",
        "x",
        "s",
        "S",
        " ",
        "\n",
        "(",
        ")",
        "'",
        "k",
        "\u017f",
        "\u212a",
        "\u00e9",
        "\u2028",
        "\u0345",
        "\U0001f600",
        "\U0001d400",
        "\ud83d",
        "\ude00",
        "1",
        "the",
        "and",
        "v2",
    ]
)
TEXTS = st.lists(AWKWARD, max_size=12).map("".join)
SAMPLED = st.sampled_from([(held["source"], held["flags"]) for held in RECORDED["patterns"]])


@given(SAMPLED, TEXTS)
def test_a_match_is_a_piece_of_the_text_and_replacing_it_with_itself_changes_nothing(
    held: tuple[str, str], written: str
) -> None:
    text = unit_text(written)
    once = JsPattern(held[0], held[1].replace("g", ""))
    found = once.exec(text)
    assert once.test(text) is (found is not None)
    if found is None:
        assert once.replace(text, "!") == text
        return
    assert len(found.text) >= once.least
    assert text[found.index : found.index + len(found.text)] == found.text
    assert once.replace(text, found.text) == text
    assert once.replace(text, "") == text[: found.index] + text[found.index + len(found.text) :]
    for group in found.groups.values():
        assert group is None or group in found.text


@given(TEXTS)
def test_pairing_units_undoes_splitting_into_them(written: str) -> None:
    whole = paired(unit_text(written))
    assert paired(unit_text(whole)) == whole
    assert unit_text(paired(unit_text(written))) == unit_text(written)
    assert all(ord(unit) <= 0xFFFF for unit in unit_text(written))
