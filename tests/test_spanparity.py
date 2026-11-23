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


def rewrite_rows(
    gate: Gate, member: str, change: Callable[[list[Any]], None], *, restate: bool = True
) -> None:
    """Change the recorded lines of the full view.

    The digest and the count the expectation holds of them are restated with them, which is
    what an edit to the committed recording has to do to get as far as a line being read.
    A line the change leaves as text is written as it is.
    """
    stated = gate.stated()
    path = gate.expected.parent / stated[member]
    lines = [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()]
    change(lines)
    data = "".join(
        f"{line if isinstance(line, str) else json.dumps(line, separators=(',', ':'), ensure_ascii=False)}\n"
        for line in lines
    )
    path.write_bytes(gzip.compress(data.encode("utf-8")))
    if restate:
        name = spanparity.ROWS if member == "rows" else spanparity.LEGACY_ROWS
        stated["full"]["sha256"][name] = sha256_hex(data.encode("utf-8"))
        if member == "rows":
            stated["results"] = len(lines)
        else:
            stated["full"]["legacy_otherwise"] = len(lines)
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


def test_the_legacy_lines_are_held_to_the_digest_and_the_count_the_expectation_states(gate: Gate) -> None:
    # A result that needs a restricted object is taken from these lines in CI, so a line of
    # one that is changed, added or taken out has to stop the gate there too.
    gate.restrict()

    def held(lines: list[Any]) -> None:
        lines[0]["start"] = (lines[0]["start"] or 0) + 1
        lines[0]["layer"] = "f" * 64
        lines[0]["text"] = "0" * 16

    for change in (held, lambda lines: lines.append({**lines[0], "id": "r-unknown"}), list.clear):
        before = gzip.decompress((gate.expected.parent / gate.stated()["legacy_rows"]).read_bytes())
        rewrite_rows(gate, "legacy_rows", change, restate=False)
        with pytest.raises(
            StoreError, match=r"lines of legacy-rows\.jsonl beside .* are not the ones it names"
        ):
            gate.verify()
        (gate.expected.parent / gate.stated()["legacy_rows"]).write_bytes(gzip.compress(before))
    assert gate.verify().problems == []
    # The count is held as the digest is.
    stated = gate.stated()
    stated["full"]["legacy_otherwise"] += 1
    gate.state(stated)
    with pytest.raises(StoreError, match="are not the ones it names"):
        gate.verify()


def test_a_legacy_line_of_no_result_or_a_second_of_one_stops_the_gate(gate: Gate) -> None:
    rewrite_rows(gate, "legacy_rows", lambda lines: lines.append({**lines[0], "id": "r-unknown"}))
    with pytest.raises(StoreError, match=r"name a result twice, or one rows\.jsonl does not hold"):
        gate.verify()
    rewrite_rows(gate, "legacy_rows", lambda lines: lines.__setitem__(1, lines[0]))
    with pytest.raises(StoreError, match="name a result twice"):
        gate.verify()


def test_recorded_lines_that_are_not_of_the_results_in_their_order_stop_the_gate(gate: Gate) -> None:
    # The lines are joined to the results by position, so two that change places would be
    # taken for each other where neither can be measured.
    gate.restrict()
    names: list[str] = []

    def swap(lines: list[Any]) -> None:
        names[:] = [lines[4]["id"], lines[12]["id"]]
        lines[4], lines[12] = lines[12], lines[4]

    rewrite_rows(gate, "rows", swap)
    with pytest.raises(StoreError) as refused:
        gate.verify()
    assert str(refused.value) == (
        f"line 5 of the recorded lines is of {names[1]} and result 5 of {gate.root} is {names[0]}"
    )
    rewrite_rows(gate, "rows", swap)
    rewrite_rows(gate, "rows", list.pop)
    with pytest.raises(StoreError) as refused:
        gate.verify()
    assert str(refused.value) == f"the recorded lines are of 34 results and {gate.root} holds 35"


def without(member: str) -> Callable[[list[Any]], None]:
    return lambda lines: lines[0].pop(member)


@pytest.mark.parametrize(
    ("member", "change"),
    [
        ("rows", without("tried")),
        ("rows", lambda lines: lines.__setitem__(0, [1, 2])),
        ("rows", lambda lines: lines.__setitem__(0, "not JSON")),
        ("rows", lambda lines: lines[0].update(tried=True)),
        ("rows", lambda lines: lines[0].update(start="1")),
        ("rows", lambda lines: lines[0].update(ok=True, kind=None)),
        ("rows", lambda lines: lines.__setitem__(0, dict(reversed(lines[0].items())))),
        ("legacy_rows", without("id")),
        ("legacy_rows", lambda lines: lines.__setitem__(0, "{")),
    ],
)
def test_a_recorded_line_the_recorder_does_not_write_stops_the_gate_in_a_line(
    gate: Gate, capsys: pytest.CaptureFixture[str], member: str, change: Callable[[list[Any]], None]
) -> None:
    rewrite_rows(gate, member, change)
    assert gate.run() == 2
    said = capsys.readouterr().err
    assert said.startswith("Cannot verify: a recorded line is not one the recorder writes: ")
    assert said.count("\n") == 1


def test_an_expectation_or_a_recording_that_is_not_the_recorders_stops_the_gate_in_a_line(
    gate: Gate, capsys: pytest.CaptureFixture[str]
) -> None:
    stated = gate.stated()
    for less, said in (
        ("date", "the recorded expectation does not name the date it was measured as of"),
        ("results", "the recorded expectation has no count of results"),
    ):
        gate.state({name: value for name, value in stated.items() if name != less})
        assert gate.run() == 2
        assert capsys.readouterr().err == f"Cannot verify: {said}\n"
    for less in ("legacy_otherwise", "layers_read"):
        gate.state(
            {**stated, "full": {name: value for name, value in stated["full"].items() if name != less}}
        )
        assert gate.run() == 2
        assert capsys.readouterr().err == f"Cannot verify: the recorded expectation has no count of {less}\n"
    gate.state({**stated, "results": True})
    assert gate.run() == 2
    assert capsys.readouterr().err == "Cannot verify: the recorded expectation has no count of results\n"
    gate.state(stated)
    for held in ("[1]", "not JSON", "\xff"):
        (gate.full / spanparity.SUMMARY).write_bytes(held.encode("latin-1"))
        assert gate.run("--recorded", str(gate.full)) == 2
        assert capsys.readouterr().err == (
            f"Cannot verify: {gate.full / spanparity.SUMMARY} is not the summary of a recording\n"
        )
        with pytest.raises(StoreError, match="is not the summary of a recording"):
            spanparity.assemble(gate.full, gate.public, PDFTOTEXT)
    (gate.full / spanparity.SUMMARY).write_text("{}", "utf-8")
    assert gate.run("--recorded", str(gate.full)) == 2
    assert "does not name the store commit" in capsys.readouterr().err
    with pytest.raises(StoreError, match="the two recordings differ in recorded_from: None and "):
        spanparity.assemble(gate.full, gate.public, PDFTOTEXT)


def test_recorded_lines_that_are_not_the_ones_the_expectation_names_stop_the_gate(gate: Gate) -> None:
    stated = gate.stated()
    path = gate.expected.parent / stated["rows"]
    lines = gzip.decompress(path.read_bytes())
    path.write_bytes(gzip.compress(lines.replace(b'"tried":1', b'"tried":2', 1)))
    with pytest.raises(StoreError, match=r"lines of rows\.jsonl beside .* are not the ones it names"):
        gate.verify()
    path.write_bytes(gzip.compress(lines + lines[: lines.index(b"\n") + 1]))
    with pytest.raises(StoreError, match="are not the ones it names"):
        gate.verify()
    # A line ends at a newline and nowhere else, and the last one ends at one.
    path.write_bytes(gzip.compress(lines.removesuffix(b"\n")))
    with pytest.raises(StoreError, match="are not the ones it names"):
        gate.verify()
    path.write_bytes(gzip.compress(b""))
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
    assert found.figures_file is False
    assert found.problems == [
        f"span-resolution-{DATE}-by-kind.tsv is not what the results give",
        f"{figures.SPAN_FIGURES} holds other figures than the results give",
        f"{figures.SPAN_FIGURES} and the records beside it are not the bytes the results give",
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
        ),
        f"{figures.SPAN_FIGURES} and the records beside it are not the bytes the results give",
    ]


SOURCES = "data/entities/sources.jsonl"


def test_the_committed_figures_file_is_held_byte_for_byte(gate: Gate) -> None:
    # Every figure is the one the results give, and the file still is not the one they write.
    file = gate.root / figures.SPAN_FIGURES
    whole = file.read_text("utf-8")
    assert gate.verify().figures_file is True
    for other in (
        whole.replace('"npm run spans -- measure"', '"npm run spans -- measured"'),
        whole.replace('"produced": "2026-11-19"', '"produced": "2026-11-18"'),
        whole.replace("\n  ", "\n ", 1),
        whole.removesuffix("\n"),
    ):
        assert other != whole
        file.write_text(other, "utf-8")
        found = gate.verify()
        assert found.figures.same == found.figures.expected
        assert found.gained == 0
        assert found.problems == [
            f"{figures.SPAN_FIGURES} and the records beside it are not the bytes the results give"
        ]
    file.write_text(whole, "utf-8")
    # The records beside it are held too, to the line and to there being any.
    records = gate.root / figures.records_file_for(figures.SPAN_FIGURES)
    held = records.read_text("utf-8")
    records.write_text(f"{held}\n", "utf-8")
    assert gate.verify().figures_file is False
    records.unlink()
    assert gate.verify().problems == [
        f"{figures.SPAN_FIGURES} and the records beside it are not the bytes the results give"
    ]


def test_an_input_that_has_only_gained_records_is_left_as_committed_and_counted(gate: Gate) -> None:
    # The committed file of the pinned store is of a day when the sources were fewer. It is
    # then the file the results give but for that one input, and the gate says so.
    file = gate.root / figures.SPAN_FIGURES
    records = gate.root / figures.records_file_for(figures.SPAN_FIGURES)
    row = next(row for row in json.loads(file.read_text("utf-8"))["inputs"] if row["path"] == SOURCES)
    file.write_text(
        file.read_text("utf-8")
        .replace(row["sha256"], "0" * 64)
        .replace(f'"lines": {row["lines"]}', f'"lines": {row["lines"] - 1}', 1),
        "utf-8",
    )
    lines = records.read_text("utf-8").split("\n")
    del lines[lines.index(f"# {SOURCES}") + 1]
    records.write_text("\n".join(lines), "utf-8")
    found = gate.verify()
    assert found.problems == []
    assert (found.figures_file, found.gained) == (True, 1)
    assert json.loads(spanparity.to_json(found))["figures_file"] == {"same": True, "gained": 1}
    assert (
        "  the committed figures file is the bytes the results give: yes, with 1 of its inputs left as "
        "committed because they have gained records\n"
    ) in f"{spanparity.summary(found)}\n"


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
