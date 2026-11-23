"""Span resolution, replayed against what the TypeScript did with each case.

`tests/fixtures/spans.json` holds a list of calls with what the data repository's own code
returned or threw for each, and what its commands printed and wrote on small trees. The port
has to give the same, in the same words.

Where the TypeScript throws, the recording holds the name of what it threw and the port has
to raise. The words of a thrown error are Node's and are not compared.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import cli, figures, js, regions, resolution, sheets, spanmeasure
from rewire_evidence.aligned import ALIGNED_NORMALISERS
from rewire_evidence.gridspans import GridUnreadable
from rewire_evidence.jsonl import StoreError
from rewire_evidence.office import BLOCK_MARKER
from rewire_evidence.offsets import CONTEXT_POINTS
from rewire_evidence.textlayer import LayerFailure
from tests.spanned import CALLS, SPANS, changed, figure_input, input_row

#: What the port raises where the TypeScript throws.
THROWN = (TypeError, ValueError, StoreError, LayerFailure, GridUnreadable)
MODULES = 20
CACHE_DIR = "data/archive/cache/"


def test_the_recording_is_of_the_pinned_commit_and_of_every_file_the_port_was_made_from() -> None:
    workflow = (Path(__file__).parent.parent / ".github/workflows/checks.yml").read_text("utf-8")
    source = SPANS["recorded_from"]
    assert f"STORE_REF: {source['store_ref']}\n" in workflow
    assert source["script"] == "scripts/fixtures/record_spans_fixture.mjs"
    assert len(SPANS["modules"]) == MODULES
    assert all(re.fullmatch(r"[a-f0-9]{64}", digest) for digest in SPANS["modules"].values())


def test_the_lists_the_port_holds_are_the_typescripts() -> None:
    constants = SPANS["constants"]
    assert list(resolution.SPAN_KINDS) == constants["span_kinds"]
    assert list(resolution.REFUSAL_REASONS) == constants["refusal_reasons"]
    assert list(resolution.GRID_ROUTES) == constants["grid_routes"]
    assert constants["context_points"] == CONTEXT_POINTS
    assert sorted(regions.FILE_NORMALISERS) == sorted(constants["file_normalisers"])
    assert sorted(ALIGNED_NORMALISERS) == sorted(constants["aligned_normalisers"])
    assert sorted(sheets.SHEET_NORMALISERS) == sorted(constants["sheet_normalisers"])
    assert constants["span_figures"] == figures.SPAN_FIGURES
    assert list(figures.SPAN_INPUTS) == constants["span_inputs"]
    assert constants["block_marker"] == BLOCK_MARKER


def test_a_negative_power_of_ten_is_the_nearest_double_as_node_gives_it() -> None:
    # `spans value-check` perturbs a number by one unit of the record's last place.
    powers = SPANS["constants"]["negative_powers"]
    assert [js.number_of_text(power) for power in powers] == [
        float(f"1e-{places}") for places in range(len(powers))
    ]


@pytest.mark.parametrize(("held", "digits", "expected"), SPANS["constants"]["precision"])
def test_a_number_is_written_to_a_precision_as_node_writes_it(held: str, digits: int, expected: str) -> None:
    assert js.precision(js.number_of_text(held), digits) == expected


def _id(case: list[Any]) -> str:
    name, given, _ = case
    return f"{name}-{'-'.join(str(held)[:24] for held in given[:3])}"


@pytest.mark.parametrize("case", SPANS["calls"], ids=_id)
def test_each_call_gives_what_the_typescript_gave(case: list[Any]) -> None:
    name, given, expected = case
    if isinstance(expected, dict) and "thrown" in expected:
        with pytest.raises(THROWN):
            CALLS[name](*given)
    else:
        assert CALLS[name](*given) == expected


def test_every_call_the_recorder_makes_is_replayed() -> None:
    assert {case[0] for case in SPANS["calls"]} == set(CALLS)


def lay_out(root: Path, files: dict[str, str], objects: dict[str, str] | None = None) -> None:
    """Lay a tree out: its files as they are, and each object of its cache compressed.

    A cached object is held to its expanded bytes, and gzip does not write the same bytes on
    every machine, so the recording holds each object as it is.
    """
    for file, data in files.items():
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        (root / file).write_bytes(bytes.fromhex(data))
    for file, data in (objects or {}).items():
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        (root / file).write_bytes(gzip.compress(bytes.fromhex(data)))


def differing(root: Path, laid: dict[str, str]) -> dict[str, str]:
    """Every file under a folder that was not laid out there as it now is, the object cache apart."""
    found = {}
    for path in sorted(root.rglob("*")):
        file = path.relative_to(root).as_posix()
        if path.is_file() and not file.startswith(CACHE_DIR):
            data = path.read_bytes().hex()
            if laid.get(file) != data:
                found[file] = data
    return found


def figure_file(held: dict[str, Any]) -> figures.FigureFile:
    return figures.FigureFile(
        held["command"],
        held["produced"],
        tuple(held["documents"]),
        tuple(figure_input(row) for row in held["inputs"]),
        held["figures"],
    )


def figure_json(held: figures.FigureFile) -> dict[str, Any]:
    return {
        "command": held.command,
        "produced": held.produced,
        "documents": list(held.documents),
        "inputs": [input_row(row) for row in held.inputs],
        "figures": dict(held.figures),
    }


@pytest.mark.parametrize("tree", SPANS["figure_trees"], ids=lambda tree: tree["name"])
def test_the_figures_file_is_read_and_written_as_the_typescript_does(  # noqa: C901 - one step to a branch
    tree: dict[str, Any], tmp_path: Path
) -> None:
    lay_out(tmp_path, tree["files"])
    inputs = () if tree["inputs"] is None else (tree["inputs"],)

    def step(name: str, given: list[Any]) -> object:
        if name == "inputFiles":
            return [input_row(held) for held in figures.input_files(tmp_path, *inputs)]
        if name == "readFigures":
            return figure_json(figures.read_figures(tmp_path, *given))
        if name == "writeFigures":
            figures.write_figures(tmp_path, figure_file(given[0]), *given[1:])
        elif name == "snapshot":
            held = figure_file(given[0])
            figures.write_figures(
                tmp_path,
                figures.FigureFile(
                    held.command,
                    held.produced,
                    held.documents,
                    tuple(figures.input_files(tmp_path, *inputs)),
                    held.figures,
                ),
            )
        elif name == "edit":
            (tmp_path / given[0]).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / given[0]).write_bytes(given[1].encode("utf-8"))
        elif name == "remove":
            (tmp_path / given[0]).unlink()
        else:
            return changed(
                figures.changed_inputs(
                    figures.read_figures(tmp_path).inputs, figures.input_files(tmp_path, *inputs), tmp_path
                )
            )
        return None

    for name, given, expected, *written in tree["steps"]:
        if isinstance(expected, dict) and "thrown" in expected:
            with pytest.raises(THROWN):
                step(name, given)
        else:
            assert step(name, given) == expected
        if written:
            assert differing(tmp_path, tree["files"]) == written[0]


DATE = "2026-01-02"
COMMANDS = [
    pytest.param(tree, runs, id=f"{tree['name']}-{at}")
    for tree in SPANS["commands"]
    for at, runs in enumerate(tree["runs"])
]


def _argv(argv: list[str], root: Path) -> list[str] | None:
    """The command line of the port for one of the TypeScript's, or `None` where the port has none."""
    program, *rest = argv
    rooted = ["--root", str(root)]
    words = [word for word in rest if word != "--detail"]
    words = [str(root / word[7:]) if word.startswith("<root>") else word for word in words]
    if program == "layers":
        return [program, *words[:1], *rooted, *words[1:]]
    if not words or words[0] == "measure":
        return [program, "measure", *rooted, "--date", DATE, *words[1:]]
    if words[0] == "show":
        return [program, "show", *rooted, *(words[1:] or [""])]
    return [program, words[0], *rooted] if words[0] in {"retag", "control", "value-check"} else None


@pytest.mark.parametrize(("tree", "runs"), COMMANDS)
def test_the_commands_print_and_write_what_the_typescripts_do(
    tree: dict[str, Any],
    runs: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lay_out(tmp_path, tree["files"], tree["objects"])
    monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
    monkeypatch.setattr(cli, "_seconds", lambda: 0.0)
    laid = dict(tree["files"])

    def said(held: str) -> str:
        return (
            held.replace(str(tmp_path), "<root>")
            .replace(DATE, "<date>")
            .replace("\n0 s\n", "\n<seconds> s\n")
        )

    for run in runs:
        # Two steps are no command: a file taken out of the tree, and one rewritten in a place.
        if run["argv"][0] == "rm":
            (tmp_path / run["argv"][1]).unlink()
            continue
        if run["argv"][0] == "sub":
            file, pattern, replacement = run["argv"][1:]
            before = (tmp_path / file).read_text("utf-8")
            (tmp_path / file).write_text(re.sub(pattern, replacement, before, count=1), "utf-8")
            assert (tmp_path / file).read_text("utf-8") != before
            laid[file] = (tmp_path / file).read_bytes().hex()
            continue
        argv = _argv(run["argv"], tmp_path)
        if argv is None:
            # The TypeScript prints its usage for a command it does not know, and argparse refuses it.
            with pytest.raises(SystemExit):
                cli.main([*run["argv"]])
            capsys.readouterr()
            continue
        if "--detail" in run["argv"]:
            monkeypatch.setenv("SPAN_DETAIL", str(tmp_path / "detail.tsv"))
        else:
            monkeypatch.delenv("SPAN_DETAIL", raising=False)
        status = cli.main(argv)
        printed = capsys.readouterr()
        assert status == run["status"], printed.err
        assert said(printed.out) == run["out"]
        if run["status"] == 0:
            assert said(printed.err) == run["err"]
        written = {
            said(file): said(bytes.fromhex(data).decode("utf-8")).encode("utf-8").hex()
            for file, data in differing(tmp_path, laid).items()
        }
        assert written == run["written"]
        laid |= {file: (tmp_path / file).read_bytes().hex() for file in differing(tmp_path, laid)}


def test_the_measurement_of_a_small_store_counts_what_the_tables_say(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The tables are held by the commands. This holds the report they are written from.
    tree = SPANS["commands"][0]
    lay_out(tmp_path, tree["files"], tree["objects"])
    monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
    # One object of the manifest is not in the cache, which the build reports.
    assert cli.main(["layers", "build", "--root", str(tmp_path)]) == 1
    logged: list[str] = []
    report = spanmeasure.measure(tmp_path, log=logged.append)
    assert report.locators == len(report.work)
    assert report.resolved == sum(report.by_kind.values())
    assert report.locators - report.resolved == sum(report.by_reason.values())
    assert logged[0].startswith("pass 1: ")
    assert spanmeasure.measure(tmp_path, limit=3).locators == 3
    assert spanmeasure.measure(tmp_path, limit=0).locators == report.locators
