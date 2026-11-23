"""JavaScript patterns run through `re`, held to what Node matched.

`tests/fixtures/js-patterns.json` is written by `scripts/fixtures/record_pattern_fixture.mjs`
with Node's built-ins, from the patterns and texts in `scripts/fixtures/pattern_cases.json`.
Every expected match here is something Node returned.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import cells, checks, jsregex, locator, sanity, tables
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


def inside_a_character(source: str, flags: str, text: str) -> bool:
    """Whether V8 may find a match between the two units of a character, which is refused."""
    return "u" in flags and JsPattern(source, flags).least == 0 and len(paired(text)) < len(text)


def split(*, refused: bool) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (held, case)
        for held, case in PAIRS
        if inside_a_character(held["source"], held["flags"], unit_text(case["text"])) is refused
    ]


MATCHES = named(split(refused=False))
REFUSED_INSIDE = named(split(refused=True))


def every_pattern() -> list[tuple[str, str]]:
    """The patterns the library runs and the ones the cases test the translation with."""
    return [
        *(
            (held.source, held.flags)
            for held in (
                *locator.PATTERNS,
                *cells.PATTERNS,
                *checks.PATTERNS,
                *tables.PATTERNS,
                *sanity.PATTERNS,
            )
        ),
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


@pytest.mark.parametrize(("held", "case"), REFUSED_INSIDE)
def test_a_match_of_nothing_is_not_looked_for_where_v8_looks_inside_a_character(
    held: dict[str, Any], case: dict[str, Any]
) -> None:
    text = unit_text(case["text"])
    pattern = JsPattern(held["source"], held["flags"])
    runs = [pattern.exec, pattern.test, lambda text: pattern.replace(text, "-")]
    for run in [*runs, pattern.all] if "g" in held["flags"] else runs:
        with pytest.raises(Unsupported, match="inside a character beyond U\\+FFFF"):
            run(text)
    # The same pattern runs on the same text with the character taken out.
    plain = unit_text("".join(character for character in case["text"] if ord(character) <= 0xFFFF))
    assert pattern.test(plain) is (pattern.exec(plain) is not None)


def test_node_finds_a_match_inside_a_character_where_the_specification_has_none() -> None:
    # The reason for the refusal, as Node 24.22.3 recorded it: the index 2 is between the two
    # units of U+1D400, which are at 1 and 2. The specification looks at 1 and then at 3.
    found = {
        (held["source"], held["flags"], case["text"]): case["found"] for held, case in split(refused=True)
    }
    assert found["(?![^,;])", "u", "a\U0001d400"] == {"index": 2, "text": "", "groups": {}}
    # The specification's answer is the end of the text, which is 3 in units. Python holds
    # the character whole, so it has the end and nothing between 1 and it.
    whole = "a\U0001d400"
    ends = re.search("(?![^,;])", whole)
    assert ends is not None
    assert len(unit_text(whole[: ends.start()])) == len(unit_text(whole)) == 3
    assert len(REFUSED_INSIDE) >= 5
    # A pattern that has to take a character runs on such text, and so does one with no u flag.
    emoji = unit_text("\U0001f600")
    assert JsPattern("[^x]$", "u").exec(emoji) == Found(0, emoji, {})
    assert JsPattern("x*", "g").replace(f"a{emoji}", "-") == f"-a-{emoji[0]}-{emoji[1]}-"
    # Half a character is one unit, with nothing inside it to find.
    assert JsPattern("x*", "gu").replace("a\ud83d", "-") == "-a-\ud83d-"


@pytest.mark.parametrize(
    ("held", "case"), named([pair for pair in split(refused=False) if "replaced" in pair[1]])
)
def test_a_pattern_replaces_what_node_replaces(held: dict[str, Any], case: dict[str, Any]) -> None:
    replaced = JsPattern(held["source"], held["flags"]).replace(unit_text(case["text"]), held["replacement"])
    assert paired(replaced) == case["replaced"]


@pytest.mark.parametrize(("held", "case"), named([pair for pair in split(refused=False) if "all" in pair[1]]))
def test_a_pattern_finds_every_match_node_finds(held: dict[str, Any], case: dict[str, Any]) -> None:
    text = unit_text(case["text"])
    found = JsPattern(held["source"], held["flags"]).all(text)
    assert [as_recorded(each) for each in found] == case["all"]
    # The first is what exec finds, and each is where it says it is, after the one before it.
    assert (as_recorded(found[0]) if found else None) == case["found"]
    ends = [each.index + len(each.text) for each in found]
    assert all(text[each.index : end] == each.text for each, end in zip(found, ends, strict=True))
    assert all(each.index >= end for each, end in zip(found[1:], ends, strict=False))


def test_every_match_is_asked_only_of_a_pattern_that_looks_for_every_one() -> None:
    with pytest.raises(Unsupported, match="has no g flag, and matchAll needs one"):
        JsPattern("a").all("aaa")
    assert [found.index for found in JsPattern("a", "g").all("aaa")] == [0, 1, 2]
    # After a match of nothing the next search starts one character on, and ends with the text.
    assert [(found.index, found.text) for found in JsPattern("x*", "g").all("axx")] == [
        (0, ""),
        (1, "xx"),
        (3, ""),
    ]
    assert JsPattern("x", "g").all("") == []
    # Without the u flag a match is in units, and with it a character is taken whole.
    emoji = unit_text("\U0001f600")
    assert [found.text for found in JsPattern("[^a]", "g").all(emoji)] == [emoji[0], emoji[1]]
    whole = JsPattern("(?<c>[^a])", "gu").all(f"b{emoji}")
    assert whole == [Found(0, "b", {"c": "b"}), Found(1, emoji, {"c": emoji})]
    with pytest.raises(Unsupported, match="inside a character beyond U\\+FFFF"):
        JsPattern("x*", "gu").all(emoji)
    recorded = [pair for pair in PAIRS if "all" in pair[1]]
    assert len(recorded) > 50
    assert any(len(case["all"]) > 3 for _, case in recorded)
    assert any(any(each["groups"] for each in case["all"]) for _, case in recorded)


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
    # JavaScript gives up a pass that matched nothing, so `?` differs as well: Node leaves
    # the group of /E(?<g>\b)?/ unset on "e", and matches "ss" where Python's `re` matches "".
    ("E(?<g>\\b)?", "", "can match nothing"),
    ("(?:\\w?[^x]{0,2}\\b|\\w?[^x]q{0,2})?", "", "can match nothing"),
    ("(?:a?)?", "", "can match nothing"),
    ("(?:a|)??", "", "can match nothing"),
    ("(?:a*){0,1}", "", "can match nothing"),
    ("(?<g>a*){0,1}?", "", "can match nothing"),
    ("(?:a?){1}", "", "can match nothing"),
    ("(?:(?=a))?", "", "can match nothing"),
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


@pytest.mark.parametrize(("source", "flags", "said"), UNSUPPORTED)
def test_a_pattern_python_would_run_differently_is_refused_for_replacing_too(
    source: str, flags: str, said: str
) -> None:
    with pytest.raises(Unsupported) as refused:
        JsPattern(source, flags).replace("a", "b")
    assert said in str(refused.value)


def test_an_option_that_can_match_nothing_was_run_as_python_runs_it() -> None:
    # What `re` gives for the two patterns Node was seen to read otherwise, which is why
    # they are refused and not translated.
    assert re.search(r"E(?P<g>\b)?", "E").groupdict() == {"g": ""}  # type: ignore[union-attr]
    assert re.search(r"(?:\w?[^x]{0,2}\b|\w?[^x]q{0,2})?", "ssheet")[0] == ""  # type: ignore[index]


def test_replacing_every_match_steps_over_a_character_after_a_match_of_nothing() -> None:
    # Node writes these, and they are recorded. `re.sub` does not: after a match of nothing
    # it looks for one that is not empty at the same place, which a lazy repeat then gives it.
    lazy = JsPattern("[a-z]*?", "g")
    assert lazy.replace("x1y", "<>") == "<>x<>1<>y<>"
    assert re.sub("[a-z]*?", "<>", "x1y") == "<><><>1<><><>"
    assert JsPattern("b??", "g").replace("abc", "<>") == "<>a<>b<>c<>"
    assert JsPattern("[^a]??", "g").replace("abc", "<>") == "<>a<>b<>c<>"
    assert re.sub("[^a]??", "<>", "abc") == "<>a<><><><><>"
    # One match only without the g flag, and one after the end of the text with it.
    assert JsPattern("[a-z]*?").replace("x1y", "<>") == "<>x1y"
    assert lazy.replace("", "<>") == "<>"
    assert JsPattern("x*", "g").replace("axxb", "-") == "-a--b-"
    # A search part of the way along still sees the start of the text and what came before.
    assert JsPattern("^a|(?<=a)a", "g").replace("aaba", "-") == "--ba"
    # Without the u flag the step is one unit, which may be half a character.
    emoji = unit_text("\U0001f600")
    assert JsPattern("[^a]??", "g").replace(emoji, "-") == f"-{emoji[0]}-{emoji[1]}-"
    # With it the step is a whole character.
    assert JsPattern("[^a]", "gu").replace(f"{emoji}b{emoji}", "-") == "---"


def test_the_node_the_fixtures_job_runs_is_the_one_the_sets_were_recorded_with() -> None:
    # A set follows the Unicode data and the V8 a Node build carries, so the workflow names
    # one build and the recordings name the same one.
    workflow = (HERE.parent / ".github/workflows/checks.yml").read_text("utf-8")
    pinned = re.findall(r'node-version: "([^"]+)"', workflow)
    assert len(pinned) == 2
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", version) for version in pinned)
    held = json.loads((HERE.parent / "src/rewire_evidence" / jsregex.CLASSES_FILE).read_text("utf-8"))
    lowercase = json.loads((HERE.parent / "src/rewire_evidence/data/lowercase.json").read_text("utf-8"))
    powers = json.loads((HERE.parent / "src/rewire_evidence/data/powers-of-ten.json").read_text("utf-8"))
    checked = json.loads((HERE / "fixtures/checks.json").read_text("utf-8"))
    tabled = json.loads((HERE / "fixtures/tables.json").read_text("utf-8"))
    for recorded in (RECORDED, held, lowercase, powers, checked, tabled):
        assert recorded["recorded_from"]["node"] == f"v{pinned[1]}"
    assert RECORDED["recorded_from"]["unicode"] == held["recorded_from"]["unicode"] == "17.0"
    # Every action is named by a commit, which a tag is not.
    used = re.findall(r"uses: (\S+)", workflow)
    assert len(used) == workflow.count("uses:") == 18
    assert all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action) for action in used)


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
    if inside_a_character(held[0], held[1], text):
        with pytest.raises(Unsupported):
            once.exec(text)
        return
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


@given(SAMPLED, TEXTS)
def test_replacing_every_match_writes_once_for_each_and_first_where_the_first_match_is(
    held: tuple[str, str], written: str
) -> None:
    text = unit_text(written)
    every = JsPattern(held[0], held[1] if "g" in held[1] else held[1] + "g")
    if inside_a_character(held[0], held[1], text):
        with pytest.raises(Unsupported):
            every.replace(text, "")
        return
    found = JsPattern(held[0], held[1].replace("g", "")).exec(text)
    marked = every.replace(text, "\x00")
    if found is None:
        assert marked == text
        return
    assert marked.startswith(text[: found.index] + "\x00")
    # No more than one match starts at each place in the text and one at its end.
    assert 1 <= marked.count("\x00") <= len(text) + 1
    # What is left is the text with the matches taken out, in order.
    left = iter(text)
    assert all(unit in left for unit in marked.replace("\x00", ""))
    if every.least:
        assert len(marked) - marked.count("\x00") <= len(text) - marked.count("\x00") * every.least
    assert every.replace(text, "") == marked.replace("\x00", "")


@given(TEXTS)
def test_pairing_units_undoes_splitting_into_them(written: str) -> None:
    whole = paired(unit_text(written))
    assert paired(unit_text(whole)) == whole
    assert unit_text(paired(unit_text(written))) == unit_text(written)
    assert all(ord(unit) <= 0xFFFF for unit in unit_text(written))
