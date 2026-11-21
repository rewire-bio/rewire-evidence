"""The baseline of the checks pack, the ratchet on it, and the two commands.

The cases are what `scripts/omics/checks/cli.ts` did at the pinned commit, recorded in
`tests/fixtures/checks.json`: baselines compared with runs, and the command run in scratch
folders, one step after another.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rewire_evidence import cli, ratchet
from rewire_evidence.canonical import JsonObject, js_parse
from rewire_evidence.checks import CHECKS, CheckInput, CheckSummary, RunResult, load_input, run_all
from rewire_evidence.cli import main
from rewire_evidence.jsonl import StoreError
from rewire_evidence.ratchet import (
    Baseline,
    BaselineCheck,
    Command,
    above_baseline,
    baseline_of,
    baseline_text,
    compared,
    ratchet_counts,
    ratchet_failure,
    read_baseline,
    rises,
    shrunk,
    store_counts,
    summary_lines,
)
from rewire_evidence.writes import today
from tests.checked import CHECKED, given_of, lay_out, named
from tests.test_checks import VOCABULARY, over, stores

CONSTANTS: dict[str, Any] = CHECKED["constants"]
RATCHETS = named(CHECKED["ratchets"])
COMMANDS = named(CHECKED["commands"])
SMALL: dict[str, str] = named(CHECKED["checkouts"])["a small checkout"]["files"]
USAGE = (
    "Usage: npm run checks -- report [--date YYYY-MM-DD] | baseline [--date YYYY-MM-DD] [--commit <sha>]\n"
)


def run_of(summaries: list[dict[str, Any]]) -> RunResult:
    """A run with these counts and no findings, which is all the ratchet reads of one."""
    return RunResult(
        [],
        [
            CheckSummary(held["id"], held["title"], held["findings"], held["rules"], held["context"])
            for held in summaries
        ],
    )


def load_of(records: list[JsonObject]) -> CheckInput:
    """The input `over` runs the pack on."""
    return given_of({"records": records, **VOCABULARY})


def test_the_words_and_the_files_are_the_typescripts() -> None:
    assert CONSTANTS["remedy"] == ratchet.RATCHET_REMEDY
    assert CONSTANTS["note"] == ratchet.RATCHET_NOTE
    assert tuple(CONSTANTS["notes"]) == ratchet.BASELINE_NOTES
    assert CONSTANTS["baseline_file"] == ratchet.BASELINE_FILE
    assert CONSTANTS["report_dir"] == ratchet.REPORT_DIR


#: The baselines this library refuses to read, which the TypeScript reads.
MISSING_COUNTS = "a baseline with no count of a check's findings and no total"


def test_a_baseline_with_a_count_missing_is_refused_where_the_typescript_leaves_the_count_out() -> None:
    case = RATCHETS[MISSING_COUNTS]
    assert "total_findings" not in case["baseline"]
    assert "findings" not in case["baseline"]["checks"]["C1"]
    # What it compares is the one rule the file does count, and neither the check nor the total.
    assert case["baseline_counts"] == [["C1", None], ["C1.a", 0], ["total", None]]
    assert case["above"] == ["C1.a: 1, baseline 0"]
    with pytest.raises(
        StoreError, match=re.escape("the findings of C1 in data/omics/checks-baseline.json is missing")
    ):
        baseline_of(case["baseline"])


@pytest.mark.parametrize("name", [name for name in RATCHETS if name != MISSING_COUNTS])
def test_a_run_is_held_to_a_baseline_as_the_typescript_holds_it(name: str) -> None:
    case = RATCHETS[name]
    pinned = baseline_of(case["baseline"])
    run = run_of(case["summaries"])
    assert [list(pair) for pair in ratchet_counts(run).items()] == case["counts"]
    assert [list(pair) for pair in ratchet_counts(pinned).items()] == case["baseline_counts"]
    assert above_baseline(pinned, run) == case["above"]
    assert ratchet_failure(pinned, run) == case["failure"]
    assert bool(case["failure"]) is bool(case["above"]) is bool(rises(pinned, run))
    for rise, line in zip(rises(pinned, run), case["above"], strict=True):
        assert line.startswith(f"{rise.key}: ")
        assert rise.rise == rise.count - rise.baseline > 0


def test_the_ratchet_cases_reach_every_way_a_count_is_named() -> None:
    failures = "\n".join(case["failure"] for case in RATCHETS.values())
    assert "  total findings: " in failures
    assert " (check total): " in failures
    assert "  C1 unit for metric, rule a: 2, baseline 1, up 1\n" in failures
    # A check the baseline gives no title, or an empty one, is named by its ID alone.
    untitled = RATCHETS["a check with no title in the baseline"]["failure"]
    assert "  C1 (check total): 1, baseline 0, up 1\n  C1, rule a: 1" in untitled
    assert "  C2 (check total): 1, baseline 0, up 1\n  C2, rule a: 1" in untitled
    # The rule is whatever follows the first dot, so a check whose ID holds one is read as a rule.
    dotted = RATCHETS["check IDs that are numbers, and one holding a dot"]
    assert "  C1 plain, rule a.b: " in dotted["failure"]
    assert [key for key, _ in dotted["counts"]][:2] == ["9", "10"]
    assert sum(not case["above"] for case in RATCHETS.values()) >= 5
    assert len(RATCHETS) == 18
    assert all(
        case["failure"].endswith(ratchet.RATCHET_REMEDY) for case in RATCHETS.values() if case["failure"]
    )
    assert RATCHETS["counts that are not whole"]["above"] == [
        "C1: 3, baseline 0.5",
        "C1.a: 1, baseline 1e-7",
        "total: 3, baseline 2.5",
    ]


def test_a_baseline_file_that_does_not_hold_counts_is_refused(tmp_path: Path) -> None:
    good: JsonObject = {
        "total_findings": 1,
        "checks": {"C1": {"title": "t", "findings": 1, "rules": {"a": 1}}},
    }
    assert baseline_of(good) == Baseline(1, {"C1": BaselineCheck("t", 1, {"a": 1})})

    def damaged(path: list[str], value: object) -> JsonObject:
        held = json.loads(json.dumps(good))
        at = held
        for step in path[:-1]:
            at = at[step]
        at[path[-1]] = value
        return held  # type: ignore[no-any-return]

    refusals: list[tuple[list[str], object, str]] = [
        (["checks"], None, "has no checks"),
        (["checks"], [], "has no checks"),
        (["checks", "C1"], "x", "gives the check C1 no rules"),
        (["checks", "C1", "rules"], [], "gives the check C1 no rules"),
        (["checks", "C1", "title"], 5, "gives the check C1 a title that is not text"),
        (["checks", "C1", "findings"], "1", "the findings of C1 in a.json is not a number"),
        (["checks", "C1", "findings"], True, "the findings of C1 in a.json is not a number"),
        (["checks", "C1", "rules", "a"], None, "the count of C1.a in a.json is not a number"),
        (["total_findings"], None, "the total findings in a.json is not a number"),
    ]
    for path, value, said in refusals:
        with pytest.raises(StoreError, match=re.escape(said)):
            baseline_of(damaged(path, value), "a.json")
    # A count that is not there is refused as missing. The TypeScript reads such a file and
    # leaves the count out of what it compares.
    no_findings: JsonObject = {"total_findings": 1, "checks": {"C1": {"title": "t", "rules": {"a": 1}}}}
    with pytest.raises(StoreError, match=re.escape("the findings of C1 in a.json is missing")):
        baseline_of(no_findings, "a.json")
    with pytest.raises(StoreError, match=re.escape("the total findings in a.json is missing")):
        baseline_of({"checks": good["checks"]}, "a.json")
    with pytest.raises(StoreError, match="is not a baseline"):
        baseline_of([])
    file = tmp_path / "baseline.json"
    with pytest.raises(StoreError, match="cannot be read"):
        read_baseline(file)
    file.write_text("{not json", "utf-8")
    with pytest.raises(StoreError, match="is not JSON"):
        read_baseline(file)
    file.write_text(json.dumps(good), "utf-8")
    assert read_baseline(file) == baseline_of(good)


def test_a_run_is_summed_up_in_the_lines_the_typescript_prints() -> None:
    run = run_of(
        [{"id": "C1", "title": "a check", "findings": 3, "rules": {"a": 1, "b": 2}, "context": {"seen": 9}}]
    )
    assert summary_lines(run) == [
        "C1 a check: 3",
        "    a: 1",
        "    b: 2",
        "    (seen: 9)",
        "total findings: 0",
    ]
    pinned = Baseline(3, {"C1": BaselineCheck("a check", 3, {"a": 1, "b": 2})})
    assert (
        compared(None, run)
        == "No data/omics/checks-baseline.json yet; run npm run checks -- baseline to write one."
    )
    assert compared(pinned, run, "b.json") == "Counts equal b.json."
    higher = Baseline(9, {"C1": BaselineCheck("a check", 9, {"a": 1, "b": 2})})
    assert (
        compared(higher, run)
        == "Below data/omics/checks-baseline.json on 2 counts; lower the baseline in the same PR."
    )
    assert shrunk(higher, run) == 2
    lower = Baseline(3, {"C1": BaselineCheck("a check", 3, {"a": 0, "b": 2, "c": 5})})
    assert compared(lower, run, "b.json").startswith(
        "Check counts rose above b.json:\n  C1 a check, rule a: 1, baseline 0, up 1\n\n"
    )


@given(stores())
def test_a_run_is_never_above_or_below_the_baseline_written_from_it(records: list[JsonObject]) -> None:
    run = over(records)
    read = load_of(records)
    text = baseline_text(read, run, "2026-01-01", "abc")
    assert text.endswith("\n}\n")
    held = js_parse(text)
    assert isinstance(held, dict)
    assert list(held) == ["note", "notes", "commit", "run_date", "store", "total_findings", "checks"]
    assert held["store"] == store_counts(read)
    pinned = baseline_of(held)
    assert ratchet_counts(pinned) == ratchet_counts(run)
    assert above_baseline(pinned, run) == []
    assert shrunk(pinned, run) == 0
    assert compared(pinned, run) == "Counts equal data/omics/checks-baseline.json."


@given(stores(), st.sampled_from(["no", "high", "max"]))
def test_one_more_bare_verdict_raises_the_rule_its_check_and_the_total_by_one(
    records: list[JsonObject], verdict: str
) -> None:
    run = over(records)
    pinned = baseline_of(js_parse(baseline_text(load_of(records), run, "2026-01-01", "")))
    seeded: JsonObject = {
        "id": "seeded",
        "kind": "result",
        "links": [],
        "attributes": {"metric_qualifier": verdict},
    }
    grown = over([*records, seeded])
    before = run.summaries[3].findings
    assert above_baseline(pinned, grown) == [
        f"C4: {before + 1}, baseline {before}",
        f"C4.verdict-or-vendor-token: {before + 1}, baseline {before}",
        f"total: {len(run.findings) + 1}, baseline {len(run.findings)}",
    ]
    assert f"  C4 qualifier shape, rule verdict-or-vendor-token: {before + 1}, baseline {before}, up 1\n" in (
        ratchet_failure(pinned, grown)
    )
    assert shrunk(pinned, grown) == 0


# ---------------------------------------------------------------------------------------
# The commands.


def commanded(
    case: dict[str, Any], root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> list[tuple[dict[str, Any], int, str, str]]:
    """Run a recorded case step by step under a folder: each step, its status and what it printed."""
    lay_out(root, case["files"])
    clock = iter(range(0, 10**9, 1234))
    monkeypatch.setattr(cli, "_milliseconds", lambda: next(clock))
    # A run with no --date is under the date of the run, which is recorded as <date>.
    monkeypatch.setattr(cli, "today", lambda: "<date>")
    done = []
    for step in case["steps"]:
        if "given" in step:
            lay_out(root, step["given"])
            continue
        monkeypatch.delenv("CHECKS_BASELINE", raising=False)
        for name, value in step["env"].items():
            monkeypatch.setenv(name, value)
        try:
            status = main(["checks", *step["args"][:1], "--root", str(root), *step["args"][1:]])
        except SystemExit as left:
            status = int(str(left.code))
        said = capsys.readouterr()
        done.append((step, status, said.out, said.err))
    return done


def written_under(root: Path, files: dict[str, str]) -> dict[str, str]:
    found = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_file() and name not in files:
            found[name] = path.read_bytes().decode("utf-8")
    return found


@pytest.mark.parametrize(
    "name", [name for name, case in COMMANDS.items() if case["steps"][-1]["status"] == 0]
)
def test_the_command_prints_and_writes_what_the_typescript_does(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = COMMANDS[name]
    for step, status, out, err in commanded(case, tmp_path, capsys, monkeypatch):
        assert (status, err) == (step["status"], "")
        # The clock moved 1,234 milliseconds, which is written to one decimal.
        assert out == step["stdout"].replace("<seconds>", "1.2")
    assert written_under(tmp_path, case["files"]) == case["written"]


def test_the_runs_of_the_command_are_the_ones_expected() -> None:
    assert len(COMMANDS) == 14
    said = {
        name: [step["stdout"] for step in case["steps"] if "stdout" in step]
        for name, case in COMMANDS.items()
    }
    assert said["a report with no baseline"][0].endswith(
        "\n9 findings written to maintenance/reports/checks-2026-01-02.tsv in <seconds> s\n\n"
        "No data/omics/checks-baseline.json yet; run npm run checks -- baseline to write one.\n"
    )
    assert said["a baseline, and then a report that equals it"][1].endswith(
        "\n\nCounts equal data/omics/checks-baseline.json.\n"
    )
    assert (
        "\n\nCheck counts rose above data/omics/checks-baseline.json:\n"
        "  C4 qualifier shape (check total): 2, baseline 1, up 1\n" in (said["a report above a baseline"][1])
    )
    assert (
        "\n\nCheck counts rose above elsewhere/deep/pinned.json:\n"
        in said["a report above a baseline the environment names"][1]
    )
    assert (
        "\n  C4 (check total): 2, baseline 1, up 1\n  C4, rule verdict-or-vendor-token: 2"
        in (said["a report above a baseline that gives a check no title"][1])
    )
    # A run that is above on one count and below on another says only that it is above.
    mixed = said["a report above a baseline on one count and below it on another"][1]
    assert "rose above" in mixed
    assert "Below" not in mixed
    assert said["a report below a baseline"][1].endswith(
        "Below data/omics/checks-baseline.json on 3 counts; lower the baseline in the same PR.\n"
    )
    # The report is written with U+FFFD where a record holds half a character.
    report = COMMANDS["a report with no baseline"]["written"]["maintenance/reports/checks-2026-01-02.tsv"]
    assert '"a\\tb \\ud83d"' in report
    assert "unparseable-printed\treport\tr-2\tparseCell refuses" in report


def test_the_baseline_the_command_writes_is_the_typescripts_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = COMMANDS["a baseline, and then a report that equals it"]
    commanded(case, tmp_path, capsys, monkeypatch)
    held = json.loads((tmp_path / ratchet.BASELINE_FILE).read_text("utf-8"))
    assert (held["commit"], held["run_date"], held["total_findings"]) == ("0123abc", "2026-01-01", 9)
    assert held["note"] == ratchet.RATCHET_NOTE
    assert held["store"] == {
        "records": 6,
        "results": 3,
        "sources": 2,
        "metric_concepts": 4,
        "unit_concepts": 3,
    }
    assert list(held["checks"]) == [check.id for check in CHECKS]
    read = load_input(tmp_path)
    assert (tmp_path / ratchet.BASELINE_FILE).read_text("utf-8") == (
        baseline_text(read, run_all(read), "2026-01-01", "0123abc")
    )
    undated = json.loads(
        COMMANDS["a baseline with no commit, under today's date"]["written"][ratchet.BASELINE_FILE]
    )
    assert (undated["commit"], undated["run_date"]) == ("", "<date>")


def test_the_command_refuses_in_a_line_a_checkout_the_typescript_fails_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = COMMANDS["a report on a checkout with no vocabulary"]
    ((step, status, out, err),) = commanded(case, tmp_path, capsys, monkeypatch)
    assert (step["status"], step["stdout"], step["stderr"]) == (1, "", None)
    assert (status, out) == (1, "")
    assert (
        err
        == "metric.ttl cannot be read: [Errno 2] No such file or directory: "
        + repr(str(tmp_path / "data/vocab/metric.ttl"))
        + "\n"
    )
    assert written_under(tmp_path, case["files"]) == case["written"] == {}


@pytest.mark.parametrize("name", ["no command", "another command"])
def test_the_command_exits_2_for_a_command_line_the_typescript_answers_with_its_usage(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = COMMANDS[name]
    ((step, status, out, err),) = commanded(case, tmp_path, capsys, monkeypatch)
    assert (step["status"], step["stdout"], step["stderr"]) == (1, "", USAGE)
    assert (status, out) == (2, "")
    assert "usage: rewire-evidence" in err
    assert written_under(tmp_path, case["files"]) == {}


def test_the_report_goes_where_the_command_is_told_and_the_checkout_is_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = lay_out(tmp_path / "store", SMALL)
    out = tmp_path / "elsewhere" / "report.tsv"
    monkeypatch.delenv("CHECKS_BASELINE", raising=False)
    assert main(["checks", "report", "--root", str(root), "--date", "2026-01-02", "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert re.search(rf"\n9 findings written to {re.escape(str(out))} in \d+\.\d s\n\nNo data/omics/", said)
    assert (
        out.read_text("utf-8")
        == COMMANDS["a report with no baseline"]["written"]["maintenance/reports/checks-2026-01-02.tsv"]
    )
    assert written_under(root, SMALL) == {}
    # With no date the report is under today's, in UTC.
    assert main(["checks", "report", "--root", str(root)]) == 0
    capsys.readouterr()
    assert list(written_under(root, SMALL)) == [f"maintenance/reports/checks-{today()}.tsv"]


@pytest.mark.parametrize(
    "date", ["../../escaped", "", "2026-1-01", "2026-01-011", "\uff12\uff10\uff12\uff16-01-01", "today"]
)
def test_a_date_that_is_not_one_is_a_command_line_that_cannot_be_read(
    date: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = lay_out(tmp_path / "store", SMALL)
    for command in ("report", "baseline"):
        with pytest.raises(SystemExit) as left:
            main(["checks", command, "--root", str(root), "--date", date])
        assert left.value.code == 2
        assert "is not a date written YYYY-MM-DD" in capsys.readouterr().err
    # Nothing was read and nothing written, in the folder of reports or outside it.
    assert written_under(tmp_path / "store", SMALL) == {}
    assert sorted(path.name for path in tmp_path.iterdir()) == ["store"]


def test_a_folder_that_is_no_checkout_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for root in (tmp_path / "missing", tmp_path):
        for command in ("report", "baseline"):
            assert main(["checks", command, "--root", str(root), "--date", "2026-01-02"]) == 1
            said = capsys.readouterr()
            assert (said.out, said.err) == ("", f"{root} holds no store: it has no folder named data\n")
    assert list(tmp_path.iterdir()) == []


def test_a_baseline_named_as_nothing_is_no_baseline_and_one_that_is_not_one_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = lay_out(tmp_path, SMALL)
    run = ratchet.report(Command(root, "2026-01-02", ""), lambda: 0)
    assert run.endswith("in 0.0 s\n\nNo  yet; run npm run checks -- baseline to write one.")
    (root / "pinned.json").write_text("[]", "utf-8")
    monkeypatch.setenv("CHECKS_BASELINE", "pinned.json")
    assert main(["checks", "report", "--root", str(root), "--date", "2026-01-02"]) == 1
    assert capsys.readouterr().err == f"{root / 'pinned.json'} is not a baseline: it has no checks\n"
    command = Command(root, "2026-01-02")
    assert command.report_file == "maintenance/reports/checks-2026-01-02.tsv"
    assert (command.baseline_file, command.out, command.commit) == (ratchet.BASELINE_FILE, None, "")
