"""The locators of a whole store: the report, the command and the gate.

The tallies and the runs of the command are what `scripts/omics/locator-cli.ts` did at the
pinned commit, recorded in `tests/fixtures/locator.json`. The whole store's report is in
`scripts/expected/store-locators.json`, recorded by the same script.
"""

from __future__ import annotations

import gzip
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import cli, locators
from rewire_evidence.canonical import JsonObject, sha256_hex
from rewire_evidence.cli import main
from rewire_evidence.js import MISSING
from rewire_evidence.jsonl import StoreError, write_text
from rewire_evidence.locator import LOCATOR_CLASSES
from rewire_evidence.locators import (
    Stored,
    Tally,
    dump_line,
    read_dump,
    report,
    report_lines,
    report_tsv,
    stored_locators,
    tally_locators,
    value_check,
    verify_locators,
)
from tests.located import LOCATOR

EXPECTED = Path(__file__).parent.parent / "scripts/expected/store-locators.json"
STORE: dict[str, Any] = json.loads(EXPECTED.read_text("utf-8"))
COMMANDS: dict[str, dict[str, Any]] = {case["name"]: case for case in LOCATOR["commands"]}
DATE = "2026-01-02"


@pytest.mark.parametrize("case", LOCATOR["tallies"])
def test_locators_are_tallied_by_class_as_the_typescript_tallies_them(case: dict[str, Any]) -> None:
    tallies = tally_locators(case["locators"])
    assert [[name, tally.row()] for name, tally in tallies.items()] == case["tallies"]


def tallies_of(tsv: str) -> dict[str, Tally]:
    """The tallies a report was written from, read back out of its rows."""
    rows = [line.split("\t") for line in tsv.split("\n")[1:-2]]
    return {row[0]: Tally(*(int(row[at]) for at in (1, 2, 4, 5, 6, 7, 8, 9))) for row in rows}


def test_the_report_of_the_pinned_store_is_written_and_printed_as_the_typescript_does() -> None:
    held = STORE["report"]
    assert held["committed"] == ["maintenance/reports/locator-coverage-2026-11-18.tsv"]
    tallies = tallies_of(held["tsv"])
    assert sum(tally.locators for tally in tallies.values()) == STORE["locators"] == 52278
    rows, total = report(tallies)
    assert report_tsv(rows) == held["tsv"]
    assert "".join(f"{line}\n" for line in report_lines(rows, total)) == held["printed"]
    # The classes are listed in the grammar's order whatever order they were met in.
    backwards = dict(reversed(list(tallies.items())))
    assert report(backwards)[0] == rows
    assert [row[0] for row in rows[:-1]] == [name for name in LOCATOR_CLASSES if name in tallies]


def test_the_recorded_store_is_the_pinned_one_and_names_its_lines() -> None:
    assert STORE["recorded_from"]["script"] == "scripts/fixtures/record_locator_fixture.mjs"
    assert STORE["recorded_from"] == LOCATOR["recorded_from"]
    assert STORE["value_check"] == {
        "files": 14,
        "rows": 2432,
        "parsed": 1881,
        "not_parsed": 551,
        "disagreed": [],
    }
    dump = read_dump(EXPECTED, STORE)
    assert sha256_hex(dump) == STORE["sha256"]
    assert dump.count(b"\n") == STORE["locators"]
    first = json.loads(dump.split(b"\n", 1)[0])
    assert list(first) == ["id", "class", "coverage", "parsed", "rendered"]
    assert dump_line(Stored(first["id"], first["parsed"]["printed"])).encode() == dump.split(b"\n", 1)[0]


@given(st.integers(0, 10**6), st.integers(1, 10**6))
def test_a_share_is_written_to_one_decimal_as_tofixed_writes_it(part: int, whole: int) -> None:
    tally = Tally(locators=whole, full=min(part, whole))
    written = locators.report({"other": tally})[0][0][3]
    whole_part, _, tenths = written.partition(".")
    assert len(tenths) == 1
    # The share is a double, and it is the double that is rounded: 3 in 2,000 is the number
    # just under 0.15 and is written 0.1. The written tenths are the nearest to that double's
    # exact value, and the larger of two equally near.
    share = Fraction(100 * tally.full / whole)
    away = Fraction(int(whole_part) * 10 + int(tenths), 10) - share
    assert Fraction(-1, 20) < away <= Fraction(1, 20)


def laid_out(case: dict[str, Any], root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for file, text in case["files"].items():
        write_text(root / file, text)
    return root


def arguments(case: dict[str, Any], root: Path) -> list[str]:
    """The command line of a recorded run, as this command takes it."""
    action, *rest = case["args"] or [""]
    if action != "coverage":
        return ["locator", *case["args"]]
    out = ["--out", str(root / rest[1])] if rest else []
    return ["locator", "coverage", "--root", str(root), *out]


def left(root: Path, case: dict[str, Any]) -> dict[str, str]:
    """Every file under a root that the case did not put there."""
    return {
        file.relative_to(root).as_posix(): file.read_bytes().decode("utf-8")
        for file in sorted(root.rglob("*"))
        if file.is_file() and file.relative_to(root).as_posix() not in case["files"]
    }


PRINTED = [
    name for name, case in COMMANDS.items() if case["status"] == 0 and case["args"] != ["coverage", "--out"]
]
REFUSED = [name for name, case in COMMANDS.items() if case["status"] == 1]
MISUSED = [name for name, case in COMMANDS.items() if case["status"] == 2]


def test_the_runs_of_the_command_are_the_ones_expected() -> None:
    assert len(PRINTED) == 8
    assert len(REFUSED) == 3
    assert len(MISUSED) == 4
    assert len(COMMANDS) == len(PRINTED) + len(REFUSED) + len(MISUSED) + 1


@pytest.mark.parametrize("name", PRINTED)
def test_the_command_prints_and_writes_what_the_typescript_does(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = COMMANDS[name]
    root = laid_out(case, tmp_path / "store")
    monkeypatch.setattr(cli, "today", lambda: DATE)
    assert main(arguments(case, root)) == 0
    said = capsys.readouterr()
    assert said.err == ""
    assert said.out == case["stdout"].replace("<date>", DATE)
    assert left(root, case) == {file.replace("<date>", DATE): text for file, text in case["written"].items()}


@pytest.mark.parametrize("name", REFUSED)
def test_the_command_refuses_in_a_line_what_stops_the_typescript(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = COMMANDS[name]
    root = laid_out(case, tmp_path / "store")
    assert main(arguments(case, root)) == 1
    said = capsys.readouterr()
    assert said.out == case["stdout"] == ""
    assert said.err.count("\n") == 1
    assert "results" in said.err
    assert left(root, case) == case["written"] == {}


@pytest.mark.parametrize("name", MISUSED)
def test_the_command_exits_2_for_a_command_line_the_typescript_answers_with_its_usage(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = COMMANDS[name]
    assert case["stderr"].startswith("usage: npm run locator -- coverage")
    with pytest.raises(SystemExit) as left_with:
        main(arguments(case, tmp_path))
    assert left_with.value.code == 2
    said = capsys.readouterr()
    assert said.out == case["stdout"] == ""
    assert said.err.startswith("usage: rewire-evidence")


def test_out_with_no_path_is_a_usage_error_here_and_the_dated_report_in_the_typescript(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = COMMANDS["coverage with --out and nothing after it"]
    assert list(case["written"]) == ["maintenance/reports/locator-coverage-<date>.tsv"]
    root = laid_out(case, tmp_path / "store")
    with pytest.raises(SystemExit) as left_with:
        main(["locator", "coverage", "--root", str(root), "--out"])
    assert left_with.value.code == 2
    assert "--out: expected one argument" in capsys.readouterr().err
    assert left(root, case) == {}


def test_a_report_that_cannot_be_written_is_refused_in_a_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = COMMANDS["coverage of a store with a few results, written where it is told"]
    root = laid_out(case, tmp_path / "store")
    assert main(["locator", "coverage", "--root", str(root), "--out", str(root / "data")]) == 1
    said = capsys.readouterr()
    assert said.out == ""
    assert said.err.count("\n") == 1


def result(record_id: object, printed: object) -> str:
    return json.dumps({"id": record_id, "kind": "result", "attributes": {"source_locator": printed}})


def test_stored_locators_are_read_as_the_typescript_reads_them(tmp_path: Path) -> None:
    folder = tmp_path / locators.RESULTS_DIR
    write_text(folder / "b.jsonl", f"{result('r-b', 'Figure 2')}\n\n  \t\n{result(7, 'line 3')}\r\n")
    write_text(
        folder / "a.jsonl",
        "\n".join([result("r-a", "Table 3"), '{"kind":"result","attributes":{"source_locator":"x"}}']),
    )
    write_text(folder / "Z.jsonl", result("r-Z", "Table 1"))
    write_text(folder / "skipped.json", result("r-s", "Table 9"))
    write_text(
        folder / "others.jsonl",
        '17\n"result"\n[]\n{}\n{"kind":"result"}\n{"kind":"result","attributes":[1]}\n',
    )
    assert stored_locators(tmp_path) == [
        Stored("r-Z", "Table 1"),
        Stored("r-a", "Table 3"),
        Stored(MISSING, "x"),
        Stored("r-b", "Figure 2"),
        Stored(7, "line 3"),
    ]
    assert dump_line(Stored(MISSING, "x")).startswith(
        '{"class":"other","coverage":"unanchored","parsed":{"printed":"x",'
    )
    assert dump_line(Stored(7, "line 3")).startswith('{"id":7,"class":"line_addressed","coverage":"partial",')
    assert dump_line(Stored(None, "x")).startswith('{"id":null,"class"')


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (b"null\n", "is null, which has no kind to read"),
        (b"{nope\n", "is not JSON"),
        (b"\xff\n", "is not UTF-8"),
    ],
)
def test_a_result_file_that_stops_the_typescript_is_refused(tmp_path: Path, text: bytes, said: str) -> None:
    file = tmp_path / locators.RESULTS_DIR / "0.jsonl"
    file.parent.mkdir(parents=True)
    file.write_bytes(text)
    with pytest.raises(StoreError, match=said):
        stored_locators(tmp_path)


def test_a_checkout_with_no_result_files_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="cannot be read"):
        stored_locators(tmp_path)


# ----------------------------------------------------------------------------------------
# The gate, on a small store.

RESULTS = [
    ("r-1", "Table S18, row 'Bacformer', column 'Binary AUPRC'"),
    ("r-2", "Table A2; row ESMC-300M naive supervised; column spearman"),
    ("r-3", "'Suppl Table 3 Track performance'!K8"),
    ("r-4", "metrics.ndcg"),
    ("r-5", "Fig. 3b, panel \U0001f600"),
]
CHECKED = (
    f"{locators.VALUE_CHECK_HEADER}\n"
    "r-1\tin_cell\t0.9\tTable S18, row 'Bacformer', column 'Binary AUPRC'\n"
    "r-2\tlocator_not_parsed\t0.1\tTable A2; row ESMC-300M naive supervised; column spearman\n"
    "r-6\tin_row\t0.2\tTable 3, row 'a\tb', column 'c'\n"
)


def build(root: Path, results: list[tuple[str, str]] | None = None) -> Path:
    """A checkout with a few results, two batches that were checked, and its own report committed."""
    held = RESULTS if results is None else results
    write_text(root / locators.RESULTS_DIR / "0.jsonl", "".join(f"{result(*each)}\n" for each in held))
    write_text(root / locators.BATCHES_DIR / "a-batch" / locators.VALUE_CHECK, CHECKED)
    write_text(
        root / locators.BATCHES_DIR / "b-batch" / locators.VALUE_CHECK, f"{locators.VALUE_CHECK_HEADER}\n"
    )
    write_text(root / locators.BATCHES_DIR / "c-batch" / "batch.jsonl", "")
    rows, _ = report(tally_locators(printed for _, printed in held))
    write_text(root / locators.REPORT_DIR / "locator-coverage-2026-11-18.tsv", report_tsv(rows))
    write_text(root / locators.REPORT_DIR / "locator-coverage-2026-11-17.tsv", "another\n")
    write_text(root / locators.REPORT_DIR / "notes.tsv", report_tsv(rows))
    return root


def recorded_of(root: Path, folder: Path) -> tuple[JsonObject, bytes]:
    """An expectation measured on a store as it stands, as the committed one was by the TypeScript."""
    dump = "".join(f"{dump_line(held)}\n" for held in stored_locators(root)).encode()
    measured = verify_locators(root, {}, dump).measured
    recorded: JsonObject = {"recorded_from": {"store_ref": "abc123"}, "dump": "lines.jsonl.gz", **measured}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "lines.jsonl.gz").write_bytes(gzip.compress(dump))
    (folder / "expected.json").write_text(json.dumps(recorded), encoding="utf-8")
    return recorded, dump


def test_the_gate_passes_the_store_it_was_recorded_on(tmp_path: Path) -> None:
    root = build(tmp_path / "store")
    recorded, dump = recorded_of(root, tmp_path / "expected")
    assert read_dump(tmp_path / "expected" / "expected.json", recorded) == dump
    found = verify_locators(root, recorded, dump)
    assert found.problems == []
    assert (found.read, found.reproduced, found.recorded_lines) == (5, 5, 5)
    assert (found.value_check_rows, found.value_check_agreed) == (3, 3)
    assert found.measured["value_check"] == {
        "files": 2,
        "rows": 3,
        "parsed": 2,
        "not_parsed": 1,
        "disagreed": [],
    }
    assert recorded["report"] == found.measured["report"]
    assert found.measured["report"]["committed"] == ["maintenance/reports/locator-coverage-2026-11-18.tsv"]  # type: ignore[index, call-overload]
    assert locators.summary(found) == (
        "locators read: 5\n"
        "  lines the TypeScript recorded: 5\n"
        "  read as the TypeScript reads them, byte for byte: 5\n"
        "value-check rows: 3 in 2 files\n"
        "  version 1 of the grammar agrees with check-values on: 3\n"
        "  parsed: 2, not parsed: 1"
    )
    written = json.loads(locators.to_json(found, recorded))
    assert written["store_ref"] == "abc123"
    assert written["locators_reproduced"] == written["locators_read"] == written["locators_recorded"] == 5
    assert written["value_check_agreed"] == written["value_check_rows"] == 3
    assert "report" not in written["measured"]
    assert written["problems"] == []
    # The lines may end without a newline, as a file cut short would.
    assert verify_locators(root, recorded, dump[:-1]).reproduced == 5


def test_a_locator_read_differently_is_named_with_where_the_lines_part(tmp_path: Path) -> None:
    root = build(tmp_path / "store")
    recorded, dump = recorded_of(root, tmp_path / "expected")
    lines = dump.split(b"\n")
    lines[1] = lines[1].replace(b'"key":"row"', b'"key":"ROW"')
    lines[4] = lines[4][:-9]
    found = verify_locators(root, recorded, b"\n".join(lines))
    assert found.reproduced == 3
    assert len(found.problems) == 3
    assert found.problems[0] == "the recorded lines do not have the SHA-256 the expectation names for them"
    assert found.problems[1].startswith(
        'locator 2 ("r-2") is not read as the TypeScript reads it: from byte '
    )
    assert '"key":"row"' in found.problems[1]
    assert '"key":"ROW"' in found.problems[1]
    assert found.problems[2].startswith('locator 5 ("r-5") is not read')


def test_a_store_with_more_or_fewer_locators_than_were_recorded_fails(tmp_path: Path) -> None:
    root = build(tmp_path / "store")
    recorded, dump = recorded_of(root, tmp_path / "expected")
    fewer = verify_locators(build(tmp_path / "fewer", RESULTS[:4]), recorded, dump)
    assert "the store holds 4 locators and the TypeScript recorded 5" in fewer.problems
    assert "locators: measured 4, the TypeScript recorded 5" in fewer.problems
    assert fewer.reproduced == 4
    more = verify_locators(build(tmp_path / "more", [*RESULTS, ("r-9", "line 4")]), recorded, dump)
    assert "the store holds 6 locators and the TypeScript recorded 5" in more.problems
    assert any(problem.startswith('locator 6 ("r-9") is not read') for problem in more.problems)
    assert more.reproduced == 5


def test_rows_that_differ_are_named_up_to_a_point_and_counted_beyond_it(tmp_path: Path) -> None:
    many = [(f"r-{at}", f"line {at}") for at in range(14)]
    root = build(tmp_path / "store", many)
    recorded, _ = recorded_of(root, tmp_path / "expected")
    found = verify_locators(root, recorded, b"{}\n" * 14)
    assert found.reproduced == 0
    assert sum(problem.startswith("locator ") for problem in found.problems) == locators.ROWS_SHOWN
    assert "4 more locators are not read as the TypeScript reads them" in found.problems
    nameless = build(tmp_path / "nameless", [])
    write_text(
        nameless / locators.RESULTS_DIR / "0.jsonl", '{"kind":"result","attributes":{"source_locator":"x"}}\n'
    )
    assert any(
        problem.startswith("locator 1 (with no ID) is not read")
        for problem in verify_locators(nameless, recorded, b"{}\n").problems
    )


def test_the_report_has_to_be_one_the_data_repository_commits(tmp_path: Path) -> None:
    root = build(tmp_path / "store")
    recorded, dump = recorded_of(root, tmp_path / "expected")
    write_text(root / locators.REPORT_DIR / "locator-coverage-2026-11-18.tsv", "edited\n")
    found = verify_locators(root, recorded, dump)
    assert found.problems == [
        "report.committed: 0 items measured and 1 recorded by the TypeScript, first differing at position 0",
        "no committed coverage report is the one measured, so the report is held to nothing",
    ]
    (root / locators.REPORT_DIR / "locator-coverage-2026-11-17.tsv").unlink()
    (root / locators.REPORT_DIR / "locator-coverage-2026-11-18.tsv").unlink()
    (root / locators.REPORT_DIR / "notes.tsv").unlink()
    (root / locators.REPORT_DIR).rmdir()
    assert verify_locators(root, recorded, dump).problems == found.problems


def test_version_1_of_the_grammar_has_to_agree_with_check_values_row_for_row(tmp_path: Path) -> None:
    root = build(tmp_path / "store")
    recorded, dump = recorded_of(root, tmp_path / "expected")
    file = root / locators.BATCHES_DIR / "b-batch" / locators.VALUE_CHECK
    write_text(
        file,
        f"{locators.VALUE_CHECK_HEADER}\nr-7\tlocator_not_parsed\t1\tTable 1, row 'a'\nr-8\n\nr-9\tin_cell\n",
    )
    counted = value_check(root)
    assert counted.row() == {
        "files": 2,
        "rows": 6,
        "parsed": 4,
        "not_parsed": 2,
        "disagreed": [
            "data/omics/b-batch/value-check.tsv r-7",
            "data/omics/b-batch/value-check.tsv r-8",
            "data/omics/b-batch/value-check.tsv r-9",
        ],
    }
    found = verify_locators(root, recorded, dump)
    assert (found.value_check_rows, found.value_check_agreed) == (6, 3)
    assert "value_check.rows: measured 6, the TypeScript recorded 3" in found.problems
    assert any(problem.startswith("value_check.disagreed: 3 items measured") for problem in found.problems)


@pytest.mark.parametrize("text", ["", "id\tstatus\n", f"x{locators.VALUE_CHECK_HEADER}\n"])
def test_a_value_check_file_with_another_header_stops_the_run(tmp_path: Path, text: str) -> None:
    root = build(tmp_path / "store")
    write_text(root / locators.BATCHES_DIR / "b-batch" / locators.VALUE_CHECK, text)
    with pytest.raises(StoreError, match="does not start with the header of a value-check file"):
        value_check(root)


def test_value_check_files_that_cannot_be_read_stop_the_run(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="cannot be read"):
        value_check(tmp_path)
    root = build(tmp_path / "store")
    (root / locators.BATCHES_DIR / "b-batch" / locators.VALUE_CHECK).write_bytes(b"\xff")
    with pytest.raises(StoreError, match="is not UTF-8"):
        value_check(root)


def test_the_recorded_lines_have_to_be_named_and_be_gzip(tmp_path: Path) -> None:
    expected = tmp_path / "expected.json"
    with pytest.raises(StoreError, match="does not name the file of recorded lines"):
        read_dump(expected, {})
    with pytest.raises(StoreError, match="cannot be read"):
        read_dump(expected, {"dump": "missing.gz"})
    (tmp_path / "plain.gz").write_bytes(b"not gzip")
    with pytest.raises(StoreError, match="cannot be read"):
        read_dump(expected, {"dump": "plain.gz"})
    (tmp_path / "short.gz").write_bytes(gzip.compress(b"lines\n")[:-6])
    with pytest.raises(StoreError, match="cannot be read"):
        read_dump(expected, {"dump": "short.gz"})
    (tmp_path / "damaged.gz").write_bytes(gzip.compress(b"lines\n" * 40)[:14] + b"\xff" * 12)
    with pytest.raises(StoreError, match="cannot be read"):
        read_dump(expected, {"dump": "damaged.gz"})
