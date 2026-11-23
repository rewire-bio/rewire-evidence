"""The span gate, held to recordings of the TypeScript on a store small enough to commit.

`scripts/fixtures/record_spans_store.mjs` is the script that records the pinned store for
the gate. The fixtures job also runs it over a small tree, once with every object in the
cache and once with two taken out, and `tests/fixtures/spans.json` holds what it wrote both
times. So the gate is run here as CI runs it, in both views, against recordings the
TypeScript made, and then against the same recordings with one thing wrong in each.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import figures, gates, spanparity, spansaid
from rewire_evidence.canonical import sha256_hex
from rewire_evidence.jsonl import StoreError
from rewire_evidence.spanmeasure import REPORTS, MeasureReport, measure
from rewire_evidence.textlayer import LayerFailure
from tests.spanned import SPANS
from tests.test_spans_recorded import lay_out

RECORDED: dict[str, Any] = SPANS["recordings"]
DATE: str = RECORDED["date"]
PDFTOTEXT = "pdftotext version 26.08.0"
CACHE = "data/archive/cache"


@dataclass
class Gate:
    """A small store, the two recordings of it, and the expectation assembled from them."""

    root: Path
    full: Path
    public: Path
    expected: Path

    @property
    def objects(self) -> Path:
        return self.root / CACHE

    def restrict(self) -> None:
        """Take out of the cache the objects the public recording was made without."""
        for file in RECORDED["restricted"]:
            (self.root / file).unlink()

    def stated(self) -> dict[str, Any]:
        held: dict[str, Any] = json.loads(self.expected.read_text("utf-8"))
        return held

    def state(self, held: dict[str, Any]) -> None:
        self.expected.write_text(json.dumps(held), "utf-8")

    def verify(self, recorded_in: Path | None = None) -> spanparity.SpanReport:
        return spanparity.verify_spans(self.root, self.objects, self.expected, self.stated(), recorded_in)

    def run(self, *extra: str) -> int:
        return gates.spans_gate(
            [str(self.root), str(self.objects), "--expected", str(self.expected), *extra], self.expected
        )


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gate:
    root = tmp_path / "store"
    lay_out(root, RECORDED["files"], RECORDED["objects"])
    full, public = tmp_path / "full", tmp_path / "public"
    lay_out(full, RECORDED["full"])
    lay_out(public, RECORDED["public"])
    # What the data repository commits is what a measurement over every object writes.
    reports = root / REPORTS
    for name, _ in spansaid.TABLES:
        (reports / f"span-resolution-{DATE}-{name}.tsv").write_bytes((full / f"{name}.tsv").read_bytes())
    (root / figures.SPAN_FIGURES).write_bytes((full / spanparity.FIGURES).read_bytes())
    (root / figures.records_file_for(figures.SPAN_FIGURES)).write_bytes(
        (full / spanparity.RECORDS).read_bytes()
    )
    text, rows, legacy = spanparity.assemble(full, public, PDFTOTEXT)
    expected = tmp_path / "expected" / "store-spans.json"
    expected.parent.mkdir()
    expected.write_text(text, "utf-8")
    (expected.parent / "store-spans.jsonl.gz").write_bytes(rows)
    (expected.parent / "store-spans-legacy.jsonl.gz").write_bytes(legacy)
    monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
    monkeypatch.setattr(spanparity, "pdftotext_version", lambda: PDFTOTEXT)
    return Gate(root, full, public, expected)


def test_every_object_held_gives_every_file_and_every_line_the_typescript_wrote(gate: Gate) -> None:
    found = gate.verify(gate.full)
    assert found.problems == []
    assert found.view == "full"
    assert found.objects == {"manifest": 10, "held": 10}
    assert found.rows.same == found.rows.expected == found.results == 35
    assert found.legacy.same == found.legacy.expected == 35
    assert found.held == 0
    assert len(found.digests) == len(found.recorded) == 11
    assert all(found.digests.values())
    assert all(found.recorded.values())
    assert found.tables == dict.fromkeys((name for name, _ in spansaid.TABLES), True)
    assert found.figures.same == found.figures.expected == found.figures.made > 0


def test_the_public_objects_give_the_public_recording_and_the_full_lines_they_decide(gate: Gate) -> None:
    gate.restrict()
    found = gate.verify(gate.public)
    assert found.problems == []
    assert found.view == "public"
    assert found.objects == {"manifest": 10, "held": 8}
    # The results the held objects decide are measured, and the rest are taken as recorded.
    assert (found.rows.same, found.rows.expected, found.held) == (20, 20, 15)
    assert (found.legacy.same, found.legacy.expected) == (20, 20)
    assert all(found.digests.values())
    assert all(found.recorded.values())
    # The committed tables are of every object, and come out of the two kinds of line together.
    assert all(found.tables.values())
    assert found.figures.same == found.figures.expected


def test_the_two_views_differ_so_the_gate_cannot_pass_one_for_the_other(gate: Gate) -> None:
    stated = gate.stated()
    assert stated["full"]["sha256"]["rows.jsonl"] != stated["public"]["sha256"]["rows.jsonl"]
    assert stated["full"]["resolved"] > stated["public"]["resolved"]
    assert stated["full"]["legacy_otherwise"] == 1
    found = gate.verify(gate.public)
    assert found.view == "full"
    assert found.problems
    assert not any(found.recorded[name] for name in (spanparity.ROWS, spanparity.SPANS, spanparity.STDOUT))


def test_the_gate_prints_what_it_found_and_writes_it_for_a_machine(
    gate: Gate, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "report.json"
    assert gate.run("--recorded", str(gate.full), "--json", str(report)) == 0
    said = capsys.readouterr()
    assert said.err == ""
    assert "  objects held: 10 of 10, the full view\n" in said.out
    assert "  files that are the ones the TypeScript wrote in this run: 11 of 11\n" in said.out
    assert said.out.endswith(
        "35 results are measured again over 10 objects, each with the line the TypeScript gives it. "
        "0 results need a restricted object and are held to the recorded line.\n"
    )
    held = json.loads(report.read_text("utf-8"))
    assert held["view"] == "full"
    assert held["rows"] == {"made": 35, "expected": 35, "same": 35}
    assert held["digests"] == held["recorded"] == {"expected": 11, "same": 11}
    assert held["tables"] == {"expected": 5, "same": 5}
    assert held["problems"] == []

    gate.restrict()
    assert gate.run() == 0
    said = capsys.readouterr()
    assert "  the TypeScript was not run beside this\n" in said.out
    assert "15 results need a restricted object and are held to the recorded line.\n" in said.out


def test_the_gate_fails_on_a_disagreement_and_cannot_run_without_its_expectation(
    gate: Gate, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stated = gate.stated()
    stated["full"]["sha256"][spanparity.STDOUT] = "0" * 64
    gate.state(stated)
    assert gate.run() == 1
    said = capsys.readouterr()
    assert "stdout.txt of the full view has the digest" in said.err
    assert said.err.endswith("1 problems. 35 of 35 results measured here have the recorded line.\n")
    assert gates.spans_gate([str(gate.root), str(gate.objects)], tmp_path / "absent.json") == 2
    assert "Cannot verify: " in capsys.readouterr().err


def test_another_pdftotext_is_a_problem_and_so_is_none(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spanparity, "pdftotext_version", lambda: "pdftotext version 26.11.0")
    found = gate.verify()
    assert found.pdftotext == "pdftotext version 26.11.0"
    assert found.problems == [
        (
            "pdftotext names itself as 'pdftotext version 26.11.0' and the layers were derived by "
            "'pdftotext version 26.08.0', so no PDF layer could be read"
        )
    ]

    def absent() -> str:
        raise LayerFailure("pdftotext is not on the path")

    monkeypatch.setattr(spanparity, "pdftotext_version", absent)
    assert gate.verify().pdftotext == "pdftotext is not on the path"


def test_a_file_the_expectation_does_not_name_or_names_otherwise_is_a_problem(gate: Gate) -> None:
    stated = gate.stated()
    stated["full"]["sha256"]["by-kind.tsv"] = "0" * 64
    stated["full"]["sha256"]["another.tsv"] = "0" * 64
    gate.state(stated)
    found = gate.verify()
    assert found.digests["by-kind.tsv"] is False
    assert sum(found.digests.values()) == 10
    assert found.problems[0].startswith("by-kind.tsv of the full view has the digest ")
    assert (
        found.problems[1] == "the expectation states a digest for another.tsv, which this gate does not write"
    )


def test_a_recording_of_this_run_that_differs_is_missing_or_is_of_another_commit_is_a_problem(
    gate: Gate,
) -> None:
    (gate.full / "by-kind.tsv").write_bytes(b"other\n")
    (gate.full / spanparity.ROWS).unlink()
    summary = json.loads((gate.full / spanparity.SUMMARY).read_text("utf-8"))
    summary["recorded_from"]["store_ref"] = "0" * 40
    (gate.full / spanparity.SUMMARY).write_text(json.dumps(summary), "utf-8")
    found = gate.verify(gate.full)
    assert found.recorded["by-kind.tsv"] is False
    assert found.recorded[spanparity.ROWS] is False
    assert sum(found.recorded.values()) == 9
    assert found.problems[0] == f"{gate.full} was recorded at another commit than {found.store_ref}"
    assert "by-kind.tsv is not the file the TypeScript wrote in this run" in found.problems
    assert any(
        problem.startswith(f"{gate.full / spanparity.ROWS} cannot be read: ") for problem in found.problems
    )


def rewrite_rows(gate: Gate, member: str, change: Callable[[list[dict[str, Any]]], None]) -> None:
    """Change the recorded lines of the full view, and restate the digest the expectation holds of them."""
    stated = gate.stated()
    path = gate.expected.parent / stated[member]
    lines = [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()]
    change(lines)
    data = "".join(f"{json.dumps(line, separators=(',', ':'), ensure_ascii=False)}\n" for line in lines)
    path.write_bytes(gzip.compress(data.encode("utf-8")))
    if member == "rows":
        stated["full"]["sha256"][spanparity.ROWS] = sha256_hex(data.encode("utf-8"))
        gate.state(stated)


def test_a_result_measured_otherwise_than_recorded_is_named_with_what_differs(gate: Gate) -> None:
    def change(lines: list[dict[str, Any]]) -> None:
        for line in lines[:7]:
            line["tried"] += 1
            line["text"] = "0" * 16

    rewrite_rows(gate, "rows", change)
    found = gate.verify()
    assert (found.rows.same, found.rows.expected) == (28, 35)
    # The first five are named. The count says how many there are.
    named = [problem for problem in found.problems if problem.startswith("shipped ")]
    assert len(named) == 5
    assert named[0].endswith(": differs in tried, text")
    assert found.legacy.same < found.legacy.expected
    assert found.digests[spanparity.ROWS] is False


def test_the_legacy_arm_is_held_to_its_own_lines_where_the_recording_has_them(gate: Gate) -> None:
    def change(lines: list[dict[str, Any]]) -> None:
        assert len(lines) == 1
        lines[0]["ok"] = not lines[0]["ok"]

    rewrite_rows(gate, "legacy_rows", change)
    found = gate.verify()
    assert (found.rows.same, found.rows.expected) == (35, 35)
    assert (found.legacy.same, found.legacy.expected) == (34, 35)
    assert [problem for problem in found.problems if problem.startswith("legacy ")] == [
        "legacy r-quoted-section: differs in ok"
    ]


def test_recorded_lines_that_are_not_the_ones_the_expectation_names_stop_the_gate(gate: Gate) -> None:
    stated = gate.stated()
    path = gate.expected.parent / stated["rows"]
    lines = gzip.decompress(path.read_bytes())
    path.write_bytes(gzip.compress(lines.replace(b'"tried":1', b'"tried":2', 1)))
    with pytest.raises(StoreError, match="are not the ones it names"):
        gate.verify()
    path.write_bytes(gzip.compress(lines + lines[: lines.index(b"\n") + 1]))
    with pytest.raises(StoreError, match="are not the ones it names"):
        gate.verify()
    path.write_bytes(b"not gzip")
    with pytest.raises(StoreError, match="cannot be read"):
        gate.verify()
    gate.state({**stated, "rows": None})
    with pytest.raises(StoreError, match="does not name the file of recorded rows"):
        gate.verify()
    path.write_bytes(gzip.compress(lines))
    gate.state({name: value for name, value in stated.items() if name != "full"})
    with pytest.raises(StoreError, match="the recorded expectation has no full"):
        gate.verify()


def test_a_folder_of_objects_that_is_neither_recording_stops_the_gate(gate: Gate) -> None:
    (gate.root / RECORDED["restricted"][0]).unlink()
    with pytest.raises(StoreError, match="9 of 10 objects are held, and the recordings are of 8 and of 10"):
        gate.verify()


def test_a_count_the_recording_states_otherwise_is_a_problem(gate: Gate) -> None:
    stated = gate.stated()
    stated["full"]["resolved"] += 1
    stated["full"]["held"] = 3
    gate.state(stated)
    assert gate.verify().problems == [
        "22 resolved were measured and the recording has 23",
        "0 results need an object that is not held, and the recording counted 3",
    ]


def test_a_committed_table_or_figure_the_results_do_not_give_is_a_problem(gate: Gate) -> None:
    table = gate.root / REPORTS / f"span-resolution-{DATE}-by-kind.tsv"
    table.write_bytes(table.read_bytes() + b"more\n")
    held = json.loads((gate.root / figures.SPAN_FIGURES).read_text("utf-8"))
    held["figures"]["resolved"] += 1
    (gate.root / figures.SPAN_FIGURES).write_text(json.dumps(held), "utf-8")
    found = gate.verify()
    assert found.tables["by-kind"] is False
    assert sum(found.tables.values()) == 4
    assert found.figures.same == found.figures.expected - 1
    assert found.problems == [
        f"span-resolution-{DATE}-by-kind.tsv is not what the results give",
        f"{figures.SPAN_FIGURES} holds other figures than the results give",
    ]
    table.unlink()
    with pytest.raises(StoreError, match=r"by-kind\.tsv cannot be read"):
        gate.verify()


def test_a_record_the_committed_figures_were_measured_over_that_is_gone_is_a_problem(gate: Gate) -> None:
    records = gate.root / figures.records_file_for(figures.SPAN_FIGURES)
    records.write_text(records.read_text("utf-8").replace("src-csv\t", "src-gone\t", 1), "utf-8")
    held = json.loads((gate.root / figures.SPAN_FIGURES).read_text("utf-8"))
    for row in held["inputs"]:
        if row["path"] == "data/entities/sources.jsonl":
            row["sha256"] = "0" * 64
    (gate.root / figures.SPAN_FIGURES).write_text(json.dumps(held), "utf-8")
    assert gate.verify().problems == [
        (
            f"{figures.SPAN_FIGURES} was measured over data/entities/sources.jsonl, which was 13 records "
            "measured and is 1 measured record is gone: src-gone"
        )
    ]


def test_an_input_that_moves_while_it_is_measured_is_a_problem(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    def moving(root: Path, **options: Any) -> MeasureReport:  # noqa: ANN401 - passed on as they come
        report = measure(root, **options)
        (root / "shared/omics/locator.ts").write_text("export const grammar = 2;\n", "utf-8")
        return report

    monkeypatch.setattr(spansaid, "measure", moving)
    found = gate.verify()
    assert "shared/omics/locator.ts changed while it was measured" in found.problems


def test_two_recordings_that_are_not_of_one_store_are_not_assembled(gate: Gate) -> None:
    summary = json.loads((gate.public / spanparity.SUMMARY).read_text("utf-8"))
    (gate.public / spanparity.SUMMARY).write_text(json.dumps({**summary, "date": "2026-11-20"}), "utf-8")
    with pytest.raises(StoreError, match="the two recordings differ in date: 2026-11-19 and 2026-11-20"):
        spanparity.assemble(gate.full, gate.public, PDFTOTEXT)
    with pytest.raises(StoreError, match="is not a recording over every object"):
        spanparity.assemble(gate.public, gate.public, PDFTOTEXT)


def test_the_committed_expectation_is_what_the_two_recordings_assemble_to() -> None:
    # The lines are committed and their digest is in the expectation, so one cannot move alone.
    expected = Path(__file__).parent.parent / "scripts/expected/store-spans.json"
    stated = json.loads(expected.read_text("utf-8"))
    rows = spanparity.read_rows(expected, stated, "rows")
    assert len(rows) == stated["results"] == 52278
    assert sha256_hex("".join(f"{row}\n" for row in rows)) == stated["full"]["sha256"][spanparity.ROWS]
    legacy = spanparity.read_rows(expected, stated, "legacy_rows")
    assert len(legacy) == stated["full"]["legacy_otherwise"] == 101
    assert (
        sha256_hex("".join(f"{row}\n" for row in legacy)) == stated["full"]["sha256"][spanparity.LEGACY_ROWS]
    )
    assert stated["full"]["objects_held"] == stated["objects"] == 1167
    assert stated["public"]["objects_held"] == 871
    assert stated["public"]["determined"] + stated["public"]["held"] == stated["results"]
    assert (stated["full"]["resolved"], stated["full"]["refused"]) == (18667, 33611)
    # No line holds the text of a layer: every member is a number, a flag, a name or a digest.
    held = json.loads(rows[0])
    assert set(held) == {
        *("id", "candidates", "tried", "ok", "kind", "reason", "layer", "start", "end"),
        *("byte_start", "byte_end", "anchor_candidates", "grid_route", "resolved_route"),
        *("value_present", "value_exact", "value_partial", "value_in_image", "text"),
    }
    assert all(len(json.loads(row)["text"]) == spanparity.TEXT_DIGEST for row in rows[:500])
