"""The archive read side and the licence map, replayed against what the TypeScript did.

Every scenario of `tests/fixtures/archive.json` is laid out in a scratch folder and the calls
the recorder made are made again in order: each has to return what the TypeScript returned,
or refuse in its words, and leave every file as the TypeScript left it. Then the commands.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import archive, cli
from rewire_evidence.canonical import js_stringify
from rewire_evidence.jsonl import StoreError
from tests.archived import ARCHIVE, CALLS, ROOT, changed, issues_of, lay_out, lay_out_scenario, tree

SCENARIOS: list[dict[str, Any]] = ARCHIVE["scenarios"]


def outcome(call: list[Any]) -> dict[str, Any]:
    """What a call did, in the recorder's terms."""
    name, *given = call
    try:
        returned = CALLS[name](*given)
    except archive.Refused as error:
        return {"error": {"zod": issues_of(error)}}
    except StoreError as error:
        return {"error": {"name": "Error", "message": str(error)}}
    return {"returned": returned}


def same(recorded: dict[str, Any], got: dict[str, Any]) -> bool:
    if "error" in recorded:
        # An error of the file system or of `JSON.parse` is said in this library's own words.
        if "code" in recorded["error"] or recorded["error"].get("name") == "SyntaxError":
            return "error" in got and "zod" not in got["error"]
        return got == {"error": recorded["error"]}
    return "returned" in got and js_stringify(got["returned"]) == js_stringify(recorded["returned"])


@pytest.mark.parametrize("scenario", [pytest.param(held, id=held["name"]) for held in SCENARIOS])
def test_every_call_returns_refuses_and_writes_what_the_typescript_did(
    scenario: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lay_out_scenario(tmp_path, scenario)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
    before = tree(tmp_path / ROOT)
    for at, step in enumerate(scenario["steps"]):
        if "write" in step:
            lay_out(tmp_path, step["write"])
            before = tree(tmp_path / ROOT)
            continue
        got = outcome(step["call"])
        after = tree(tmp_path / ROOT)
        said = {key: step[key] for key in ("returned", "error") if key in step}
        assert same(said, got), f"step {at} {step['call']}: recorded {said}, got {got}"
        assert changed(before, after) == step.get("changed", {}), f"step {at} {step['call']}"
        before = after


def arguments(argv: list[str]) -> list[str]:
    """A command of the recorder as this library's command line takes it."""
    if argv[:2] == ["licence", "counts"]:
        return [*argv, ROOT.as_posix()]
    return [*argv[:2], "--root", ROOT.as_posix(), *argv[2:]]


def commands() -> list[Any]:
    return [
        pytest.param(
            scenario, at, id=f"{scenario['name']}: {' '.join(scenario['commands'][at]['argv'])} ({at})"
        )
        for scenario in SCENARIOS
        for at in range(len(scenario["commands"]))
    ]


def trace(ran: dict[str, Any]) -> bool:
    """Whether the TypeScript left with a stack trace, or with its usage: neither is ported."""
    return "\n    at " in ran["stderr"] or ran["stdout"].startswith("Usage:")


@pytest.mark.parametrize(("scenario", "last"), commands())
def test_every_command_prints_and_leaves_as_the_typescript_does(
    scenario: dict[str, Any],
    last: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lay_out_scenario(tmp_path, scenario)
    monkeypatch.chdir(tmp_path)
    for at, ran in enumerate(scenario["commands"][: last + 1]):
        monkeypatch.delenv("ARCHIVE_CACHE", raising=False)
        for name, value in ran.get("env", {}).items():
            monkeypatch.setenv(name, value)
        before = tree(tmp_path)
        capsys.readouterr()
        if trace(ran):
            # The TypeScript's status is 1 for both but one: its usage with no command at all.
            try:
                status = cli.main(arguments(ran["argv"]))
            except SystemExit as left:
                status = int(left.code or 0)
            assert status != 0, f"command {at}"
            assert tree(tmp_path) == before
            continue
        status = cli.main(arguments(ran["argv"]))
        said = capsys.readouterr()
        assert said.out == ran["stdout"], f"command {at}"
        # An error of the file system is said in this library's own words.
        assert said.err == ran["stderr"] or re.match(r"E[A-Z]+: ", ran["stderr"]), f"command {at}"
        assert bool(said.err) == bool(ran["stderr"])
        assert status == ran["status"], f"command {at}"
        assert changed(before, tree(tmp_path)) == ran.get("changed", {}), f"command {at}"
