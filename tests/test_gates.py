"""The gates as commands: what they print and how they exit, in process and as scripts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rewire_evidence.families import manifest_shard_path
from rewire_evidence.gates import digests_gate, layout_gate, writes_gate
from rewire_evidence.jsonl import write_text
from rewire_evidence.parity import verify_store
from tests.history import build as build_history
from tests.test_parity import RELEASE, build

SCRIPTS = Path(__file__).parent.parent / "scripts"


def pinned(root: Path, folder: Path) -> Path:
    """An expectation measured on the store as it stands, as the committed one was."""
    report = verify_store(root, RELEASE, folder / "scratch")
    path = folder / "expected.json"
    path.write_text(
        json.dumps(
            {
                "store_ref": "abc123",
                "release_receipt": RELEASE,
                "counts": report.counts(),
                "content": report.content(),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_layout_gate_passes_the_store_it_was_measured_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    build(root)
    expected = pinned(root, tmp_path)
    measured = tmp_path / "measured.json"
    assert layout_gate([str(root), "--json", str(measured)], expected) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.startswith(f"expecting the store as it is at abc123, release {RELEASE}\n")
    assert "family files are the bytes measured at abc123: 10 of 10 digests\n" in out.out
    assert out.out.endswith(" files reproduce.\n")
    assert "All 20 lines in " in out.out
    report = json.loads(measured.read_text("utf-8"))
    assert report["problems"] == []
    assert report["counts"] == json.loads(expected.read_text("utf-8"))["counts"]


def test_the_layout_gate_fails_a_thinner_an_edited_and_a_changed_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    build(root)
    expected = pinned(root, tmp_path)
    arguments = [str(root), "--expected", str(expected)]

    # An edit that is still a valid row: every self-check passes and the pinned bytes do not.
    candidates = root / "data/archive/licence-candidates.jsonl"
    original = candidates.read_text("utf-8")
    write_text(candidates, original.replace('"s1"', '"s0"'))
    assert layout_gate(arguments, tmp_path / "unused.json") == 1
    out = capsys.readouterr()
    assert "licence_candidates holds files with digest " in out.err
    assert "1 problems. The store layout does not reproduce." in out.err
    assert "reproduce.\n" not in out.out
    write_text(candidates, original)

    # A record changed without its provenance: exit 1 with the tables printed and the
    # report written, not exit 2 with one line.
    models = root / "data/entities/models.jsonl"
    write_text(models, models.read_text("utf-8").replace('"a":1', '"a":2'))
    measured = tmp_path / "measured.json"
    assert layout_gate([*arguments, "--json", str(measured)], tmp_path / "unused.json") == 1
    out = capsys.readouterr()
    assert "records: model-a changed without a provenance entry." in out.err
    assert "records matching their provenance digest:  4" in out.out
    assert "entities                 1        2        2  byte for byte" in out.out
    problems = json.loads(measured.read_text("utf-8"))["problems"]
    assert any(problem.startswith("records: model-a changed") for problem in problems)
    assert any(problem.startswith("records_matching_provenance was measured as 4") for problem in problems)
    assert any(problem.startswith("entities holds files with digest") for problem in problems)

    # More problems than are shown in full: the count is of all of them.
    for shard in "0123456789":
        write_text(manifest_shard_path(shard, root), "{\n")
    assert layout_gate(arguments, tmp_path / "unused.json") == 1
    out = capsys.readouterr()
    assert len([line for line in out.err.split("\n") if line.startswith("  ")]) == 20
    assert " problems. The store layout does not reproduce." in out.err


def test_the_layout_gate_exits_2_when_the_run_cannot_be_made(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "store"
    build(root)
    expected = pinned(root, tmp_path)
    assert layout_gate([str(root)], tmp_path / "missing.json") == 2
    assert "Cannot verify: " in capsys.readouterr().err
    assert layout_gate([str(tmp_path / "nowhere"), "--expected", str(expected)], expected) == 2
    out = capsys.readouterr()
    assert out.err.startswith("Cannot verify: ")
    assert f"{RELEASE}.json does not exist" in out.err

    # A report that cannot be written, after a run that found nothing wrong.
    unwritable = tmp_path / "no-folder" / "report.json"
    assert layout_gate([str(root), "--expected", str(expected), "--json", str(unwritable)], expected) == 2
    out = capsys.readouterr()
    assert out.err.startswith("Cannot verify: [Errno 2] No such file or directory: ")
    assert "reproduce.\n" not in out.out

    # A file of the store that cannot be opened: a link to itself, as a file nobody may read is.
    models = root / "data/entities/models.jsonl"
    models.unlink()
    models.symlink_to(models)
    assert layout_gate([str(root), "--expected", str(expected)], expected) == 2
    assert capsys.readouterr().err.startswith("Cannot verify: ")
    assert digests_gate([str(root)]) == 2
    assert capsys.readouterr().err.startswith(f"Cannot verify: {models} cannot be read: ")


def test_the_digest_gate_reports_every_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build(tmp_path)
    assert digests_gate([str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == (
        "provenance rows: 5\nrecords checked: 5\n  match                5\n\nAll 5 records reproduce.\n"
    )

    models = tmp_path / "data/entities/models.jsonl"
    write_text(
        models, models.read_text("utf-8").replace('"a":1', '"a":2') + '{"id":"model-z","kind":"model"}\n'
    )
    assert digests_gate([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "  match                4\n  MISMATCH             1\n  no provenance row    1\n" in err
    assert "\n  models.jsonl model-a\n    committed " in err
    assert err.endswith("\n1 records do not reproduce and 1 have no provenance row.\n")
    assert "records reproduce." not in err


def test_the_digest_gate_exits_2_when_the_run_cannot_be_made(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert digests_gate([str(tmp_path)]) == 2
    assert capsys.readouterr().err.startswith("Cannot verify: ")
    write_text(tmp_path / "data/provenance/records/0.jsonl", '{"id":"a","sha256":"x"}\n')
    write_text(tmp_path / "data/entities/models.jsonl", "")
    assert digests_gate([str(tmp_path)]) == 2
    assert capsys.readouterr().err.endswith("Cannot verify: the record files hold no records\n")


def run_script(name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    # The interpreter running the tests and a script in this repository: nothing untrusted.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPTS / name), *arguments], capture_output=True, text=True, check=False
    )


def test_the_scripts_are_the_gates(tmp_path: Path) -> None:
    root = tmp_path / "store"
    build(root)
    expected = pinned(root, tmp_path)

    digests = run_script("verify_store_digests.py", str(root))
    assert digests.returncode == 0, digests.stderr
    assert "\nAll 5 records reproduce.\n" in digests.stderr
    layout = run_script("verify_store_layout.py", str(root), "--expected", str(expected))
    assert layout.returncode == 0, layout.stderr
    assert "All 20 lines in " in layout.stdout

    # With no --expected the script holds the store against the committed expectation,
    # which this small store is not.
    assert run_script("verify_store_layout.py", str(root)).returncode == 2
    manifest_shard_path("b", root).unlink()
    thinner = run_script("verify_store_layout.py", str(root), "--expected", str(expected))
    assert thinner.returncode == 1
    assert "manifest.lines was measured as 1 against a pin of 2" in thinner.stderr
    assert run_script("verify_store_digests.py", str(tmp_path / "nowhere")).returncode == 2
    assert "usage:" in run_script("verify_store_layout.py", "--help").stdout


def test_the_write_gate_replays_a_history_and_fails_one_that_does_not_reproduce(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = build_history(tmp_path / "repository")
    expected = [history.written(tmp_path), history.written(tmp_path, history.recorded, "recorded.json")]
    measured = tmp_path / "measured.json"
    arguments = [str(history.repository), "--json", str(measured)]
    assert writes_gate(arguments, expected) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out == (
        f"replaying 3 writes from the history of {history.repository}\n"
        "an add: 1 added, 0 changes recorded, 2 files written, 13 compared: reproduces\n"
        "a change: 0 added, 2 changes recorded, 2 files written, 13 compared: reproduces\n"
        "a recorded write: 1 added, 1 changes recorded, 4 files written, 13 compared: reproduces\n"
        "\nAll 3 writes reproduce: 2 records added and 3 changes recorded, 39 files byte for byte.\n"
    )
    report = json.loads(measured.read_text("utf-8"))
    assert (report["cases"], report["reproduced"], report["problems"]) == (3, 3, [])

    # One expectation alone in a folder that is kept, with a batch folder the pull request did not name.
    wrong = json.loads(expected[0].read_text("utf-8"))
    wrong["cases"][0]["steps"][0]["batch_dir"] = "another-folder"
    scratch = tmp_path / "scratch"
    altered = history.written(tmp_path / "altered", wrong)
    assert writes_gate([*arguments, "--expected", str(altered), "--scratch", str(scratch)], expected) == 1
    out = capsys.readouterr()
    assert out.out.startswith(f"replaying 2 writes from the history of {history.repository}\n")
    assert (
        "an add: 1 added, 0 changes recorded, 2 files written, 13 compared: DOES NOT REPRODUCE\n" in out.out
    )
    assert "reproduce:" not in out.out
    assert out.err.startswith("  an add: data/provenance/records/0.jsonl is blob ")
    assert out.err.endswith("\n1 problems. 1 of 2 writes reproduce.\n")
    report = json.loads(measured.read_text("utf-8"))
    assert (report["cases"], report["reproduced"], len(report["problems"])) == (2, 1, 1)
    assert (scratch / "tree" / "data/entities/sources.jsonl").exists()
    assert (scratch / "batch.jsonl").exists()


def test_the_write_gate_exits_2_when_the_replay_cannot_be_made(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = build_history(tmp_path / "repository")
    expected = history.written(tmp_path)
    assert writes_gate([str(history.repository)], [expected, tmp_path / "missing.json"]) == 2
    assert capsys.readouterr().err.startswith(f"Cannot verify: {tmp_path / 'missing.json'} cannot be read: ")
    assert writes_gate([str(tmp_path / "nowhere")], [expected]) == 2
    assert capsys.readouterr().err.startswith("Cannot verify: git rev-parse --verify failed in ")
    # A batch the merge commit does not hold: the case is wrong, which is not a disagreement.
    lacking = json.loads(expected.read_text("utf-8"))
    lacking["cases"][0]["steps"][0]["add"] = "data/omics/no-such-batch/batch.jsonl"
    altered = history.written(tmp_path / "altered", lacking)
    assert writes_gate([str(history.repository)], [altered]) == 2
    out = capsys.readouterr()
    assert out.err.startswith("Cannot verify: ")
    assert out.err.endswith(" has no data/omics/no-such-batch/batch.jsonl\n")


def test_the_write_gate_is_a_script(tmp_path: Path) -> None:
    history = build_history(tmp_path / "repository")
    expected = history.written(tmp_path)
    recorded = history.written(tmp_path, history.recorded, "recorded.json")
    done = run_script(
        "verify_store_writes.py",
        str(history.repository),
        "--expected",
        str(expected),
        "--expected",
        str(recorded),
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.endswith("39 files byte for byte.\n")
    # With no --expected the script replays the data repository's own history, which this is not.
    assert run_script("verify_store_writes.py", str(history.repository)).returncode == 2
    assert "usage:" in run_script("verify_store_writes.py", "--help").stdout
