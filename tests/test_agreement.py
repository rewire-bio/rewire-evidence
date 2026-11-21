"""The validation gate, the loader and the command, on stores written to disk.

The stores are the base store of `tests/fixtures/validation.json`, which is real records, and
the small stores the recorder wrote out for `loadRecords`, each one fault away from loading.
What the TypeScript did with each is in the fixture.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import agreement
from rewire_evidence.agreement import (
    ValidationReport,
    check_seeded,
    check_summary,
    measure,
    panel_lines,
    verify_validation,
)
from rewire_evidence.attributes import REGISTRY_FILE
from rewire_evidence.canonical import JsonObject, JsonValue, js_stringify
from rewire_evidence.cli import main
from rewire_evidence.gates import validation_gate
from rewire_evidence.jsonl import StoreError, write_text
from rewire_evidence.records import load_records, read_records
from rewire_evidence.schema import RecordError, SchemaError, parse_record
from rewire_evidence.store import record_file
from rewire_evidence.validation import validate_all
from rewire_evidence.vocab import MAPPING_FILE, VocabularyError
from tests.recorded import BASE_ROWS, ROLES, VALIDATION, named, write_base, write_configuration

SCRIPTS = Path(__file__).parent.parent / "scripts"
STORE_REF: str = VALIDATION["recorded_from"]["store_ref"]
CASES = {case["name"]: case for case in VALIDATION["validators"]}
#: One fault for each validator, with what the TypeScript answered for it on the base store.
SEEDED = {
    "attributes": "an attribute nothing declares",
    "derivations": "a derivation whose script has another hash",
    "descriptions": "a placeholder description",
    "concerns": "a concern naming nothing that exists",
    "records": "a panel result of another metric",
}


def seeds() -> list[JsonValue]:
    return [
        {"name": name, "stage": stage, "ops": CASES[name]["ops"], "error": CASES[name]["stages"][stage]}
        for stage, name in SEEDED.items()
    ]


def bare() -> JsonObject:
    """An expectation that says where it came from and nothing of what was measured."""
    return {"recorded_from": {"store_ref": STORE_REF}, "seeded": seeds()}


def recorded_for(root: Path) -> JsonObject:
    """What the recorder would say of this store, taken from a run that had no problems to do with it."""
    report = verify_validation(root, bare())
    return {**bare(), **report.measured, "records": report.records, "summary": report.summary}


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_base(tmp_path_factory.mktemp("base"))


@pytest.fixture(scope="module")
def expected(base: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("expected") / "store-validation.json"
    path.write_text(json.dumps(recorded_for(base)), encoding="utf-8")
    return path


def test_the_base_store_is_measured_in_full(base: Path) -> None:
    report = verify_validation(base, recorded_for(base))
    assert report.problems == []
    assert (report.records, report.loaded, report.seeded) == (len(BASE_ROWS), len(BASE_ROWS), 5)
    assert report.summary == f"{len(BASE_ROWS)} records match their provenance."
    measured: dict[str, Any] = dict(report.measured)
    assert measured["registry_sha256"] == REGISTRY_FILE["sha256"]
    by_type = measured["attribute_values_by_type"]
    assert measured["attribute_values"] == sum(by_type.values())
    assert measured["attribute_values"] == sum(len(row["attributes"]) for row in BASE_ROWS)
    assert by_type["text"] > 500
    assert "undefined" not in by_type
    assert measured["blocks"] == {
        name: count
        for name in sorted(agreement.BLOCKS)
        if (count := sum(name in row["attributes"] for row in BASE_ROWS))
    }
    assert measured["sources"] == sum(row["kind"] == "source" for row in BASE_ROWS)
    assert measured["description_problems"] == []
    assert measured["derivation_problems"] == []
    assert measured["normalize_unchanged"] == len(BASE_ROWS)
    assert measured["panel_owners"] == sum("comparison_panels" in row["attributes"] for row in BASE_ROWS)
    assert measured["panels"] >= measured["panel_owners"]
    assert measured["panel_rows"] >= 2 * measured["panels"]
    assert measured["concern_targets"] == sorted(measured["concern_targets"])
    assert measured["concern_targets"]


def test_the_panels_are_digested_one_owner_to_a_line(base: Path) -> None:
    _, shown = validate_all(read_records(base), base)
    lines = panel_lines({**shown, "an-excluded-owner": None}).splitlines()
    assert len(lines) == len(shown) + 1
    assert lines[-1] == "an-excluded-owner\tnull"
    owner, listed = lines[0].split("\t")
    assert owner == next(iter(shown))
    panel, results = listed.split("|")[0].split(":")
    assert panel
    assert len(results.split(",")) >= 2


def test_an_attribute_the_registry_does_not_declare_is_counted_as_untyped(base: Path) -> None:
    records = read_records(base)
    stray: dict[str, Any] = dict(records[0].row())
    stray["attributes"] = {**stray["attributes"], "nothing_declares_this": 1}
    measured: dict[str, Any] = dict(measure([parse_record(stray), *records[1:]], base, {}))
    assert measured["attribute_values_by_type"]["undefined"] == 1


def test_what_the_typescript_recorded_differently_is_named(base: Path) -> None:
    recorded = recorded_for(base)
    changed: JsonObject = {
        **recorded,
        "records": 5,
        "summary": "5 records match their provenance.",
        "panels": 99,
        "recorded_from": {"store_ref": "another-commit"},
    }
    del changed["sources"]
    problems = verify_validation(base, changed).problems
    count = len(BASE_ROWS)
    assert problems[:2] == [
        f"records: measured {count}, the TypeScript recorded 5",
        (
            f'summary: measured "{count} records match their provenance.", '
            'the TypeScript recorded "5 records match their provenance."'
        ),
    ]
    assert f"sources: measured {recorded['sources']}, the TypeScript recorded null" in problems
    assert f"panels: measured {recorded['panels']}, the TypeScript recorded 99" in problems
    assert problems[-1].startswith("the attribute registry was read at ")
    assert len(problems) == 5


def test_a_seeded_fault_must_be_refused_in_the_recorded_words(base: Path) -> None:
    records = read_records(base)
    report = ValidationReport()
    check_seeded(records, base, seeds(), report)
    assert (report.seeded, report.problems) == (5, [])

    wrong: list[Any] = seeds()
    wrong[0] = {**wrong[0], "error": {"name": "Error", "message": "something else"}}
    wrong[1] = {**wrong[1], "error": None}
    wrong[2] = {**wrong[2], "ops": []}
    report = ValidationReport()
    check_seeded(records, base, wrong, report)
    assert report.seeded == 2
    assert [problem.split(": got ")[0] for problem in report.problems] == [
        'the seeded fault "an attribute nothing declares" (attributes)',
        'the seeded fault "a derivation whose script has another hash" (derivations)',
        'the seeded fault "a placeholder description" (descriptions)',
    ]
    recorded = CASES[SEEDED["descriptions"]]["stages"]["descriptions"]
    assert report.problems[2].endswith(f"got null, and the TypeScript recorded {js_stringify(recorded)}")


@pytest.mark.parametrize(
    ("seeded", "why"),
    [
        (None, "holds no seeded faults"),
        ([], "holds no seeded faults"),
        ([5], "does not name its operations and its validator"),
        ([{"name": "x", "stage": "records"}], "does not name its operations and its validator"),
        ([{"name": "x", "stage": "nope", "ops": []}], "does not name its operations and its validator"),
        (
            [{"name": "x", "stage": "records", "ops": [["set", "no-such-record", ["name"], "x"]]}],
            "which is not a record",
        ),
        (
            [{"name": "x", "stage": "records", "ops": [["set", ROLES["described"], ["name"], ""]]}],
            "is not a record",
        ),
    ],
)
def test_a_seeded_fault_that_cannot_be_run_stops_the_gate(base: Path, seeded: JsonValue, why: str) -> None:
    with pytest.raises(StoreError, match=why):
        check_seeded(read_records(base), base, seeded, ValidationReport())


def test_an_answer_is_compared_by_its_words_unless_javascript_only_failed() -> None:
    assert agreement.agrees(None, None)
    assert not agreement.agrees(None, {"name": "Error", "message": "x"})
    assert agreement.agrees({"name": "Error", "message": "x"}, {"name": "Error", "message": "x"})
    assert not agreement.agrees({"name": "Error", "message": "y"}, {"name": "Error", "message": "x"})
    assert agreement.agrees({"name": "TypeError"}, {"name": "TypeError", "message": "Invalid URL"})
    assert not agreement.agrees(
        {"name": "Error", "message": "Invalid URL"}, {"name": "TypeError", "message": "Invalid URL"}
    )
    assert not agreement.agrees(None, {"name": "TypeError", "message": "x"})

    def raising(error: Exception) -> JsonObject | None:
        def run() -> None:
            raise error

        return agreement.outcome(run)

    assert raising(StoreError("s")) == {"name": "Error", "message": "s"}
    assert raising(VocabularyError("v")) == {"name": "Error", "message": "v"}
    assert raising(IsADirectoryError("d")) == {"name": "Error", "code": "EISDIR"}
    assert agreement.outcome(lambda: 5) is None


# ---- the gate as a command -------------------------------------------------------------


def test_the_validation_gate_passes_the_store_it_was_recorded_on(
    base: Path, expected: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    measured = tmp_path / "measured.json"
    assert validation_gate([str(base), "--expected", str(expected), "--json", str(measured)], expected) == 0
    out = capsys.readouterr()
    assert out.err == ""
    count = len(BASE_ROWS)
    lines = out.out.splitlines()
    assert lines[0] == f"expecting the store as the TypeScript validated it at {STORE_REF}"
    assert lines[1:3] == [
        f"records read: {count:,}",
        f"  accepted by every validator of load_records: {count:,}",
    ]
    assert lines[-3:] == [
        "seeded faults refused as the TypeScript refuses them: 5",
        "",
        f"{count} records match their provenance.",
    ]
    written = json.loads(measured.read_text("utf-8"))
    assert written["problems"] == []
    assert (written["records_read"], written["records_loaded"], written["seeded_refused"]) == (
        count,
        count,
        5,
    )
    assert written["measured"] == {
        key: value
        for key, value in json.loads(expected.read_text("utf-8")).items()
        if key in written["measured"]
    }


def rewritten(tmp_path: Path, ops: list[Any]) -> Path:
    return write_base(tmp_path / "store", agreement.apply_ops(BASE_ROWS, ops))


def test_the_validation_gate_fails_a_thinner_store(
    expected: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A result nothing cites or shows, removed with its provenance row: every validator still passes.
    spare = ROLES["disputed"]
    root = rewritten(tmp_path, [["drop", spare]])
    assert validation_gate([str(root)], expected) == 1
    out = capsys.readouterr()
    count = len(BASE_ROWS)
    assert f"records: measured {count - 1}, the TypeScript recorded {count}" in out.err
    assert "problems. The store is not validated as the TypeScript validates it." in out.err
    assert "records match their provenance." not in out.out.splitlines()[-1]


@pytest.mark.parametrize(
    "name",
    [
        "a placeholder description",
        "an attribute nothing declares",
        "a metric that is no concept",
        "a claim with no subject",
        "a profile that is not one",
    ],
)
def test_the_validation_gate_fails_a_store_a_validator_refuses(
    name: str, expected: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = rewritten(tmp_path, CASES[name]["ops"])
    assert validation_gate([str(root)], expected) == 1
    err = capsys.readouterr().err
    assert "  the store is refused: " in err
    assert "1 problems." in err
    # The command refuses it too, with status 1 and the refusal in a line.
    assert main(["records", "check", str(root)]) == 1
    said = capsys.readouterr()
    assert said.out == ""
    assert said.err.strip()


def test_the_validation_gate_fails_a_store_that_is_misfiled(
    expected: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_base(tmp_path / "store")
    file = record_file("source", "any", root)
    lines = file.read_text("utf-8").splitlines()
    write_text(file, "".join(f"{line}\n" for line in [lines[1], lines[0], *lines[2:]]))
    assert validation_gate([str(root)], expected) == 1
    err = capsys.readouterr().err
    assert "the store is refused as it is read: " in err
    assert "is not sorted by unique ID" in err


def test_the_validation_gate_exits_2_for_what_cannot_be_read_or_run(
    base: Path, expected: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(arguments: list[str], recorded: Path = expected) -> str:
        assert validation_gate(arguments, recorded) == 2
        return capsys.readouterr().err

    assert "cannot be read" in refused([str(base), "--expected", str(tmp_path / "missing.json")])
    assert "mapping.json cannot be read" in refused([str(tmp_path / "nowhere")])
    unreadable = write_base(tmp_path / "unreadable")
    write_text(record_file("source", "any", unreadable), "not json\n")
    assert "Cannot verify: " in refused([str(unreadable)])
    no_seeds = tmp_path / "no-seeds.json"
    no_seeds.write_text(
        json.dumps({**json.loads(expected.read_text("utf-8")), "seeded": []}), encoding="utf-8"
    )
    assert "holds no seeded faults" in refused([str(base)], no_seeds)
    no_ref = tmp_path / "no-ref.json"
    no_ref.write_text(json.dumps({"seeded": seeds()}), encoding="utf-8")
    assert "does not name the store commit" in refused([str(base)], no_ref)
    empty = tmp_path / "empty"
    write_configuration(empty)
    assert "which is not a record" in refused([str(empty)])


def test_the_script_is_the_gate(base: Path, expected: Path) -> None:
    script = SCRIPTS / "verify_store_validation.py"
    command = [sys.executable, str(script), str(base), "--expected", str(expected)]
    done = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603 - our own script
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.endswith(f"\n{len(BASE_ROWS)} records match their provenance.\n")
    command = [sys.executable, str(script), str(base / "nowhere")]
    missing = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603 - our own script
    assert missing.returncode == 2
    assert missing.stderr.startswith("Cannot verify: ")


# ---- loadRecords on stores on disk -----------------------------------------------------


def laid_out(case: dict[str, Any], root: Path) -> Path:
    for file, text in case["files"].items():
        write_text(root / file, text)
    return root


@pytest.fixture(scope="module")
def configuration(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("configuration")
    write_configuration(root)
    return root


def test_the_stores_on_disk_are_the_ones_recorded() -> None:
    held = VALIDATION["load"]
    assert len(held) == 13
    assert sum(case["error"] is None for case in held) == 4
    assert [case["count"] for case in held if case["error"] is None] == [10, 10, 10, 0]


@pytest.mark.parametrize("case", named(VALIDATION["load"]))
def test_a_store_on_disk_loads_or_is_refused_as_the_typescript_loads_or_refuses_it(
    case: dict[str, Any], tmp_path: Path, configuration: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = laid_out(case, tmp_path / "store")
    recorded = case["error"]
    if recorded is None:
        loaded = load_records(root, configuration)
        assert len(loaded) == case["count"]
        assert [record.id for record in loaded] == sorted(record.id for record in loaded)
    elif "zod" in recorded:
        with pytest.raises(StoreError, match="is not a record") as refusal:
            load_records(root, configuration)
        cause = refusal.value.__cause__
        assert isinstance(cause, SchemaError)
        assert [[issue.code, list(issue.path), issue.message] for issue in cause.issues] == recorded["zod"]
    else:
        with pytest.raises(StoreError) as refusal:
            load_records(root, configuration)
        assert str(refusal.value).replace(str(root), "<root>") == recorded["message"]
    # The command agrees: the line the TypeScript prints, or the refusal and status 1.
    write_configuration(root)
    status = main(["records", "check", str(root)])
    said = capsys.readouterr()
    if recorded is None:
        assert (status, said.out, said.err) == (0, f"{case['count']} records match their provenance.\n", "")
    else:
        assert (status, said.out) == (1, "")
        assert said.err.count("\n") >= 1


# ---- the command -----------------------------------------------------------------------


def test_check_prints_what_the_typescript_prints(
    base: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert check_summary(93216) == "93216 records match their provenance."
    assert main(["records", "check", str(base)]) == 0
    assert capsys.readouterr().out == f"{len(BASE_ROWS)} records match their provenance.\n"
    # The store defaults to the working directory, as it does for the TypeScript.
    monkeypatch.chdir(base)
    assert main(["records", "check"]) == 0
    assert capsys.readouterr().out == f"{len(BASE_ROWS)} records match their provenance.\n"
    assert main(["records", "check", str(base / "nowhere")]) == 1
    assert MAPPING_FILE in capsys.readouterr().err


def test_descriptions_lists_what_the_check_refuses(
    base: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["records", "descriptions", str(base)]) == 0
    assert capsys.readouterr().out == "\n0 records, 0 problems\n"
    assert main(["records", "descriptions", "--json", str(base)]) == 0
    assert capsys.readouterr().out == "[]\n"

    model, method = ROLES["described"], ROLES["described_other"]
    root = rewritten(
        tmp_path,
        [
            ["set", model, ["description"], "Source-reported configuration (Table 2)."],
            ["set", method, ["description"], "Candidate catalogue entry"],
        ],
    )
    assert main(["records", "descriptions", str(root)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-8:] == [
        "",
        "2 records, 4 problems",
        "  kind method: 1",
        "  kind model: 3",
        "  rule catalogue-placeholder: 1",
        "  rule locator-in-parentheses: 1",
        "  rule source-label-placeholder: 1",
        "  rule source-reported: 1",
    ]
    assert lines[0] == f"{method}\tcatalogue-placeholder\tCandidate catalogue entry"
    assert len(lines) == 12
    assert main(["records", "descriptions", "--json", str(root)]) == 0
    problems = json.loads(capsys.readouterr().out)
    assert [(problem["id"], problem["rule"]) for problem in problems] == [
        (method, "catalogue-placeholder"),
        (model, "source-label-placeholder"),
        (model, "source-reported"),
        (model, "locator-in-parentheses"),
    ]
    assert list(problems[0]) == ["id", "kind", "description", "rule", "message"]
    with pytest.raises(RecordError):
        load_records(root)
    with pytest.raises(SystemExit):
        main(["records"])
