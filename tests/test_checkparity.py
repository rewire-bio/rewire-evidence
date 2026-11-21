"""The checks gate: the pack held to a committed first run and to a recorded one.

The gate reads two checkouts of the data repository, which no test has. So it is run here on
small ones laid out from the recorded cases, with an expectation measured on them as the
committed one was measured on the pinned store by the TypeScript. What the TypeScript
recorded of the pinned store itself, `scripts/expected/store-checks.json` and the three
files beside it, is read here for what it states.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import checkparity, ratchet
from rewire_evidence.canonical import JsonObject, sha256_hex
from rewire_evidence.checkparity import (
    CheckReport,
    Dumps,
    Lines,
    by_rule,
    cell_lines,
    column_lines,
    compare_lines,
    read_dump,
    read_dumps,
    summary,
    to_json,
    verify_checks,
)
from rewire_evidence.checks import CHECKS, gathered, load_input, report_tsv, run_all
from rewire_evidence.jsonl import StoreError
from tests.checked import CHECKED, lay_out, named

ROOT = Path(__file__).parent.parent
EXPECTED = ROOT / "scripts/expected/store-checks.json"
STORE: dict[str, Any] = json.loads(EXPECTED.read_text("utf-8"))
SMALL: dict[str, str] = named(CHECKED["checkouts"])["a small checkout"]["files"]
DATE = "2026-01-01"
COMMIT = "0123abc"
SEEDED: JsonObject = {
    "id": "seeded",
    "kind": "result",
    "source_ids": [],
    "links": [],
    "attributes": {"metric_qualifier": "high"},
}


def build(root: Path, files: dict[str, str] | None = None) -> Path:
    """A checkout holding a small store, with the report and the baseline of a run over it committed."""
    lay_out(root, SMALL if files is None else files)
    ratchet.baseline(ratchet.Command(root, DATE, commit=COMMIT), lambda: 0)
    return root


def store_only(root: Path, files: dict[str, str] | None = None) -> Path:
    """A checkout at the commit of the first run: the store, and no report or baseline."""
    return lay_out(root, SMALL if files is None else files)


def facts(name: str, lines: list[bytes]) -> tuple[JsonObject, bytes]:
    data = b"".join(line + b"\n" for line in lines)
    return {"file": name, "lines": len(lines), "sha256": sha256_hex(data)}, data


def recorded_of(pinned: Path, first: Path, folder: Path) -> tuple[JsonObject, Dumps]:
    """An expectation measured on two checkouts as they stand, as the committed one was by the TypeScript."""
    given = load_input(pinned)
    dumps = Dumps(
        report_tsv(run_all(given).findings).encode().split(b"\n")[:-1], column_lines(given), cell_lines(given)
    )
    recorded: JsonObject = {
        "recorded_from": {"store_ref": "abc123"},
        "ratchet": {
            "seeded": {"record": SEEDED},
            "lowered": {"check": "C9", "rule": "same-artifact-and-url", "by": 1},
        },
    }
    folder.mkdir(parents=True, exist_ok=True)
    for name, lines in (("report", dumps.report), ("columns", dumps.columns), ("cells", dumps.cells)):
        recorded[name], data = facts(f"{name}.gz", list(lines))
        (folder / f"{name}.gz").write_bytes(gzip.compress(data))
    found = verify_checks(pinned, first, recorded, dumps)
    recorded.update(found.measured)
    baseline = (pinned / ratchet.BASELINE_FILE).read_bytes()
    report = (pinned / ratchet.REPORT_DIR / f"checks-{DATE}.tsv").read_bytes()
    recorded["first_run"] = {
        "commit": COMMIT,
        "run_date": DATE,
        "baseline": ratchet.BASELINE_FILE,
        "baseline_sha256": sha256_hex(baseline),
        "report": f"{ratchet.REPORT_DIR}/checks-{DATE}.tsv",
        "rows": report.count(b"\n") - 1,
        "report_sha256": sha256_hex(report),
    }
    (folder / "expected.json").write_text(json.dumps(recorded), encoding="utf-8")
    return recorded, dumps


def test_the_gate_passes_the_checkouts_it_was_recorded_on(tmp_path: Path) -> None:
    pinned, first = build(tmp_path / "pinned"), store_only(tmp_path / "first")
    recorded, dumps = recorded_of(pinned, first, tmp_path / "expected")
    assert read_dumps(tmp_path / "expected" / "expected.json", recorded) == dumps
    found = verify_checks(pinned, first, recorded, dumps)
    assert found.problems == []
    assert (found.first_ref, found.store_ref) == (COMMIT, "abc123")
    assert found.first == found.pinned == Lines(9, 9, 9)
    assert found.baseline_reproduced
    assert (found.first_records, found.pinned_records) == (6, 6)
    assert found.columns == Lines(3, 3, 3)
    assert found.cells == Lines(3, 3, 3)
    assert found.first_rules == found.pinned_rules
    assert list(found.first_rules) == [f"{check.id}.{rule}" for check in CHECKS for rule in check.rules]
    assert {rule: held.row() for rule, held in found.first_rules.items() if held.expected} == {
        "C2.opposite": {"made": 1, "expected": 1, "same": 1},
        "C2.asserted-where-none": {"made": 1, "expected": 1, "same": 1},
        "C3.printed-is-rounded": {"made": 1, "expected": 1, "same": 1},
        "C3.unparseable-printed": {"made": 2, "expected": 2, "same": 2},
        "C4.verdict-or-vendor-token": {"made": 2, "expected": 2, "same": 2},
        "C5.column-splits-comparability-key": {"made": 1, "expected": 1, "same": 1},
        "C9.same-artifact-and-url": {"made": 1, "expected": 1, "same": 1},
    }
    ratchet_: Any = found.measured["ratchet"]
    assert (ratchet_["above"], ratchet_["failure"]) == ([], "")
    assert ratchet_["seeded"] == {
        "record": SEEDED,
        "above": ["C4: 3, baseline 2", "C4.verdict-or-vendor-token: 3, baseline 2", "total: 10, baseline 9"],
        "failure": ratchet_["seeded"]["failure"],
    }
    assert (
        "  C4 qualifier shape, rule verdict-or-vendor-token: 3, baseline 2, up 1\n"
        in ratchet_["seeded"]["failure"]
    )
    assert ratchet_["lowered"]["above"] == [
        "C9: 1, baseline 0",
        "C9.same-artifact-and-url: 1, baseline 0",
        "total: 9, baseline 8",
    ]
    printed = found.measured["printed"]
    assert isinstance(printed, str)
    assert printed.endswith(
        "\n9 findings written to maintenance/reports/checks-<date>.tsv in <seconds> s\n\n"
        "Counts equal data/omics/checks-baseline.json.\n"
    )
    assert summary(found).split("\n")[:3] == [
        f"first run, the store at {COMMIT}: 6 records",
        "  rows of the committed report reproduced: 9 of 9",
        "    C1.minority-unit: 0 of 0",
    ]
    assert "  the committed baseline file written byte for byte: yes\n" in summary(found)
    assert summary(found).endswith("  printed values read as parseCell reads them: 3 of 3")
    written = json.loads(to_json(found))
    assert (written["first_rows"], written["first_rows_reproduced"], written["pinned_rows_reproduced"]) == (
        9,
        9,
        9,
    )
    assert (written["baseline_reproduced"], written["columns"]["same"], written["problems"]) == (True, 3, [])
    assert "printed" not in written["measured"]


def without(files: dict[str, str], name: str) -> dict[str, str]:
    return {file: text for file, text in files.items() if file != name}


def test_the_gate_names_what_differs_between_the_first_run_and_the_store_it_is_given(tmp_path: Path) -> None:
    pinned, first = build(tmp_path / "pinned"), store_only(tmp_path / "first")
    recorded, dumps = recorded_of(pinned, first, tmp_path / "expected")
    # A store of the first run with one file of results gone: two results and their findings.
    thinner = store_only(tmp_path / "thinner", without(SMALL, "data/evidence/results/0.jsonl"))
    found = verify_checks(pinned, thinner, recorded, dumps)
    assert found.first == Lines(2, 9, 0)
    assert found.pinned == Lines(9, 9, 9)
    assert not found.baseline_reproduced
    assert found.first_rules["C4.verdict-or-vendor-token"] == Lines(0, 2, 0)
    # The rows are held in place, so one that is still made counts for its rule and not for the report.
    assert found.first_rules["C9.same-artifact-and-url"] == Lines(1, 1, 1)
    said = "\n".join(found.problems)
    first_report = "the report of the first run, maintenance/reports/checks-2026-01-01.tsv"
    assert f"{first_report}: 2 lines made here and 9 expected" in said
    assert f"{first_report}, line 1: from byte 0 this library writes" in said
    assert "the baseline of the first run, data/omics/checks-baseline.json, line " in said
    assert " more lines differ" in said
    assert "no\n" in summary(found)
    assert json.loads(to_json(found))["first_rules"]["C4.verdict-or-vendor-token"] == {
        "made": 0,
        "expected": 2,
        "same": 0,
    }


def test_the_gate_names_what_differs_from_the_recording(tmp_path: Path) -> None:
    pinned, first = build(tmp_path / "pinned"), store_only(tmp_path / "first")
    recorded, dumps = recorded_of(pinned, first, tmp_path / "expected")
    other = Dumps(
        [*dumps.report[:-1], dumps.report[-1] + b"!"],
        [b"null", *dumps.columns[1:]],
        dumps.cells[:-1],
    )
    changed: JsonObject = {
        **recorded,
        "total_findings": 8,
        "store": {"records": 7},
        "first_run": {"commit": "another"},
    }
    found = verify_checks(pinned, first, changed, other)
    assert found.first == Lines(9, 9, 9)
    assert found.pinned == Lines(9, 9, 8)
    assert found.pinned_rules["C9.same-artifact-and-url"] == Lines(1, 1, 0)
    assert (found.columns, found.cells) == (Lines(3, 3, 2), Lines(3, 2, 2))
    said = "\n".join(found.problems)
    assert 'first_run.commit: measured "0123abc", the TypeScript recorded "another"' in said
    assert "first_run.rows: measured 9, and the TypeScript recorded nothing" in said
    assert "the report at the pinned commit, line 9: " in said
    here, there = '\'["table 1","acc"]\'', "'null'"
    assert (
        f"the anchor and column of a result, line 1: from byte 0 this library writes {here} "
        f"and the other has {there}" in said
    )
    assert "a printed value as parse_cell reads it: 3 lines made here and 2 expected" in said
    assert "total_findings: measured 9, the TypeScript recorded 8" in said
    assert "store.records: measured 6, the TypeScript recorded 7" in said


def test_a_committed_report_that_is_not_only_its_rows_is_named(tmp_path: Path) -> None:
    pinned, first = build(tmp_path / "pinned"), store_only(tmp_path / "first")
    recorded, dumps = recorded_of(pinned, first, tmp_path / "expected")
    report = pinned / ratchet.REPORT_DIR / f"checks-{DATE}.tsv"
    report.write_bytes(report.read_bytes().replace(b"check\trule", b"Check\trule"))
    found = verify_checks(pinned, first, recorded, dumps)
    assert found.first == Lines(9, 9, 9)
    assert (
        "maintenance/reports/checks-2026-01-01.tsv: the rows are the committed ones and the file is not"
        in found.problems
    )
    assert any(problem.startswith("first_run.report_sha256: ") for problem in found.problems)


def test_the_gate_cannot_be_run_without_its_files(tmp_path: Path) -> None:
    pinned, first = build(tmp_path / "pinned"), store_only(tmp_path / "first")
    recorded, dumps = recorded_of(pinned, first, tmp_path / "expected")
    expected = tmp_path / "expected" / "expected.json"
    baseline = pinned / ratchet.BASELINE_FILE
    kept = baseline.read_bytes()

    def refused(said: str, given: JsonObject = recorded, at: Path = pinned) -> None:
        with pytest.raises(StoreError, match=said):
            verify_checks(at, first, given, dumps)

    refused("does not name the store commit", {})
    refused("the recorded ratchet does not hold seeded", {**recorded, "ratchet": None})
    refused("the recorded ratchet does not hold lowered", {**recorded, "ratchet": {"seeded": {}}})
    ratchet_ = recorded["ratchet"]
    assert isinstance(ratchet_, dict)
    refused("the seeded case does not hold record", {**recorded, "ratchet": {**ratchet_, "seeded": {}}})
    refused("the lowered case does not name its check", {**recorded, "ratchet": {**ratchet_, "lowered": {}}})
    unpinned: list[JsonObject] = [
        {"check": "C9", "rule": "same-artifact-and-url", "by": "1"},
        {"check": "C99", "rule": "same-artifact-and-url", "by": 1},
        {"check": "C9", "rule": "another", "by": 1},
    ]
    for lowered in unpinned:
        refused("which the baseline does not pin", {**recorded, "ratchet": {**ratchet_, "lowered": lowered}})
    baseline.write_bytes(b"{not json")
    refused("checks-baseline.json is not JSON")
    baseline.write_bytes(b"[]")
    refused("is not a baseline")
    held = json.loads(kept)
    baseline.write_text(json.dumps({**held, "commit": 5}), "utf-8")
    refused("does not name its commit")
    baseline.write_text(json.dumps({**held, "run_date": "2026-02-02"}), "utf-8")
    refused("checks-2026-02-02.tsv cannot be read")
    baseline.unlink()
    refused("checks-baseline.json cannot be read")

    with pytest.raises(StoreError, match="does not hold report"):
        read_dump(expected, {}, "report")
    with pytest.raises(StoreError, match="does not name its file"):
        read_dump(expected, {"report": {}}, "report")
    with pytest.raises(StoreError, match=r"missing\.gz cannot be read"):
        read_dump(expected, {"report": {"file": "missing.gz"}}, "report")
    (tmp_path / "expected" / "bad.gz").write_bytes(b"not gzip")
    with pytest.raises(StoreError, match=r"bad\.gz cannot be read"):
        read_dump(expected, {"report": {"file": "bad.gz"}}, "report")
    report = recorded["report"]
    assert isinstance(report, dict)
    for wrong in ({**report, "sha256": "0" * 64}, {**report, "lines": 1}):
        with pytest.raises(StoreError, match=r"is not the file .* describes"):
            read_dump(expected, {"report": wrong}, "report")


def test_lines_are_compared_in_place_and_the_first_few_that_differ_are_shown() -> None:
    problems: list[str] = []
    mine = [b"a", b"b", *[b"x"] * 8]
    theirs = [b"a", b"B", *[b"y"] * 7]
    assert compare_lines(mine, theirs, "a file", problems) == Lines(10, 9, 1)
    assert problems == [
        "a file: 10 lines made here and 9 expected",
        "a file, line 2: from byte 0 this library writes 'b' and the other has 'B'",
        *[
            f"a file, line {number}: from byte 0 this library writes 'x' and the other has 'y'"
            for number in (3, 4, 5, 6)
        ],
        "a file: 3 more lines differ",
    ]
    problems.clear()
    assert compare_lines([b"same"], [b"same"], "a file", problems) == Lines(1, 1, 1)
    assert compare_lines([b"a" * 100 + b"b"], [b"a" * 100], "a file", problems) == Lines(1, 1, 0)
    assert problems == [
        f"a file, line 1: from byte 40 this library writes '{'a' * 60}b' and the other has '{'a' * 60}'"
    ]
    assert Lines(1, 2, 3).row() == {"made": 1, "expected": 2, "same": 3}


def test_each_printed_value_is_read_once_for_each_way_its_unit_has_it_read() -> None:
    def result(record_id: str, **attributes: str) -> JsonObject:
        return {"id": record_id, "kind": "result", "attributes": dict(attributes)}

    records: list[JsonObject] = [
        result("a", printed_value="59.2%", unit="percent"),
        result("b", printed_value="59.2%", unit="fraction"),
        result("c", printed_value="59.2%"),
        result("d", printed_value="59.2%", unit="percent"),
        result("e", unit="percent"),
        result("f", printed_value="0.5 (0.1)", source_locator="Table 2, column 'x'"),
        {"id": "g", "kind": "source", "attributes": {"printed_value": "1"}},
    ]
    given = gathered(records, {}, {})
    assert cell_lines(given) == [
        b'["59.2%",true,["59.2%","59.2",null]]',
        b'["59.2%",false,"Unrecognised numeric cell: \\"59.2%\\""]',
        b'["0.5 (0.1)",false,["0.5 (0.1)","0.5","0.1"]]',
    ]
    assert column_lines(given) == [b"null", b"null", b"null", b"null", b"null", b'["table 2","x"]']


def test_rows_are_counted_by_check_and_rule_on_both_sides() -> None:
    mine = [b"C1\ta\treport\tx\td", b"C1\ta\treport\ty\td", b"C2\tb\treport\tx\td"]
    theirs = [b"C1\ta\treport\tx\td", b"C1\ta\treport\tz\td", b"C3\tc\treport\tx\td", b"lone"]
    counted = by_rule(mine, theirs)
    assert len(counted) == 16 + 4
    assert counted["C1.minority-unit"] == counted["C13.would-be-rewritten"] == Lines(0, 0, 0)
    assert dict(list(counted.items())[16:]) == {
        "C1.a": Lines(2, 2, 1),
        "C2.b": Lines(1, 0, 0),
        "C3.c": Lines(0, 1, 0),
        "lone": Lines(0, 1, 0),
    }
    assert all(held == Lines(0, 0, 0) for held in by_rule([], []).values())
    assert CheckReport().problems == []


# ---------------------------------------------------------------------------------------
# What the TypeScript recorded of the pinned store.


def workflow_pin(name: str) -> str:
    workflow = (ROOT / ".github/workflows/checks.yml").read_text("utf-8")
    found = re.findall(rf'^  {name}: "?([^"\n]+)"?$', workflow, re.MULTILINE)
    assert len(found) == 1
    return str(found[0])


def test_the_recorded_run_is_of_the_pinned_store_and_the_workflow_pins_its_counts() -> None:
    assert STORE["recorded_from"] == CHECKED["recorded_from"]
    assert STORE["recorded_from"]["script"] == "scripts/fixtures/record_checks_fixture.mjs"
    assert STORE["recorded_from"]["store_ref"] == workflow_pin("STORE_REF")
    first = STORE["first_run"]
    assert first["commit"] == workflow_pin("CHECKS_REF") != workflow_pin("STORE_REF")
    assert str(first["rows"]) == workflow_pin("CHECKS_ROWS") == "10754"
    assert first["report"] == "maintenance/reports/checks-2026-11-17.tsv"
    assert str(STORE["store"]["records"]) == workflow_pin("STORE_RECORDS")
    assert str(STORE["store"]["results"]) == workflow_pin("STORE_LOCATORS")
    assert str(STORE["report"]["lines"] - 1) == workflow_pin("CHECKS_PINNED_ROWS")
    assert STORE["columns"]["lines"] == STORE["store"]["results"]
    assert STORE["store"] == {
        "records": 93216,
        "results": 52278,
        "sources": 1497,
        "metric_concepts": 275,
        "unit_concepts": 27,
    }


def test_the_recorded_report_has_the_rows_its_counts_state() -> None:
    dumps = read_dumps(EXPECTED, STORE)
    assert dumps.report[0] == b"check\trule\tseverity\tsubject\tdetail"
    rows = dumps.report[1:]
    assert len(rows) == STORE["total_findings"] == sum(held["findings"] for held in STORE["summaries"])
    counted = {rule: held.made for rule, held in by_rule(rows, []).items() if held.made}
    stated = {
        f"{held['id']}.{rule}": count
        for held in STORE["summaries"]
        for rule, count in held["rules"].items()
        if count
    }
    assert counted == stated
    assert len(stated) == 13
    assert [[held["id"], held["title"], list(held["rules"])] for held in STORE["summaries"]] == [
        [check.id, check.title, list(check.rules)] for check in CHECKS
    ]
    assert all(row.split(b"\t")[2] == b"report" for row in rows)
    # The report at the pinned commit is, as it happens, the one committed from the first run:
    # the store has grown by 29 records since and none of them raised or settled a finding.
    assert STORE["report"]["sha256"] == STORE["first_run"]["report_sha256"]
    assert sum(line != b"null" for line in dumps.columns) == 52278
    placed = [json.loads(line) for line in dumps.columns]
    context = STORE["summaries"][4]["context"]
    assert sum(anchor is None for anchor, _ in placed) == context["results_with_no_anchor"] == 16225
    assert (
        sum(anchor is not None and column is None for anchor, column in placed)
        == context["results_with_no_column"]
    )


def test_the_recorded_ratchet_is_what_the_data_repositorys_own_test_asserts() -> None:
    held = STORE["ratchet"]
    assert (held["above"], held["failure"]) == ([], "")
    assert held["counts"] == held["baseline_counts"]
    assert len(held["counts"]) == 24
    before = held["counts"]["C4.verdict-or-vendor-token"]
    total = held["counts"]["total"]
    assert held["seeded"]["record"]["id"] == "seeded-result-for-the-ratchet-test"
    assert held["seeded"]["above"] == [
        f"C4: {before + 1}, baseline {before}",
        f"C4.verdict-or-vendor-token: {before + 1}, baseline {before}",
        f"total: {total + 1}, baseline {total}",
    ]
    message = held["seeded"]["failure"]
    assert (
        f"C4 qualifier shape, rule verdict-or-vendor-token: {before + 1}, baseline {before}, up 1" in message
    )
    for words in (
        "report severity",
        "not a correctness failure",
        "npm run checks -- report",
        "docs/reviews/",
    ):
        assert words in message
    pinned = held["counts"]["C9.same-artifact-and-url"]
    assert held["lowered"]["above"] == [
        f"C9: {pinned}, baseline {pinned - 1}",
        f"C9.same-artifact-and-url: {pinned}, baseline {pinned - 1}",
        f"total: {total}, baseline {total - 1}",
    ]
    assert STORE["printed"].endswith("\n\nCounts equal data/omics/checks-baseline.json.\n")
    assert checkparity.DATE in STORE["printed"]
    assert checkparity.SECONDS in STORE["printed"]
