"""The baseline of the checks pack and the ratchet on it: the other half of `checks/cli.ts`.

`data/omics/checks-baseline.json` pins the count of every rule of every check. The checks are
report severity and gate nothing when a record is written, so the ratchet is the only thing
that bites, and it bites on growth: a run may leave the backlog alone and may not add to it.

`above_baseline` and `ratchet_failure` are what `tests/checks-baseline.test.ts` asserts with,
`baseline_text` is the file `checks -- baseline` writes, byte for byte, and `report` and
`baseline` are the two commands, which print what the TypeScript prints.

A baseline file is read more strictly here than by the TypeScript, which takes whatever JSON
it holds. One whose counts are not numbers, or whose checks are not objects, is refused with
`StoreError` before it is compared with anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rewire_evidence.canonical import (
    JsonObject,
    JsonValue,
    js_indented,
    js_key_order,
    js_parse,
    js_string,
    js_utf8,
    utf16_key,
)
from rewire_evidence.checks import CheckInput, RunResult, load_input, report_tsv, run_all
from rewire_evidence.js import fixed
from rewire_evidence.jsonl import StoreError

#: Where the baseline is, in a checkout of the data repository.
BASELINE_FILE = "data/omics/checks-baseline.json"
#: Where the report of a run is written.
REPORT_DIR = "maintenance/reports"

#: `ratchetRemedy`: why a count may not rise and what to do when one has.
RATCHET_REMEDY = (
    "Every check is report severity: nothing here gates records -- add, and a finding is a lead to read, "
    "not a wrong value. tests/checks-baseline.test.ts fails when a run raises any count above the number "
    "pinned in data/omics/checks-baseline.json, so the backlog can be worked down but not grown. A failure "
    "on an unrelated evidence pull request means the records that pull request adds raised the check and "
    "rule the failure names: a backlog ratchet, not a correctness failure. Two ways forward, both in that "
    "same pull request. Fix the new findings: npm run checks -- report writes one row per finding to "
    "maintenance/reports/checks-<date>.tsv, and the rows carrying the named rule are the ones to read. Or "
    "raise the baseline: rerun npm run checks -- baseline and add a dated review in docs/reviews/ saying "
    "which rule rose, by how much, and why the new findings stand."
)
#: `ratchetNote`: the `note` of the baseline file.
RATCHET_NOTE = (
    "Counts of every deterministic check in scripts/omics/checks/, pinned from the first run. "
    + RATCHET_REMEDY
)
#: The `notes` of the baseline file, which carry the numbers of the first run. They are the
#: TypeScript's sentences and are written whatever a later run counts, as it writes them.
BASELINE_NOTES = (
    (
        "The commit is the HEAD the pack was written against. The checks themselves were not committed "
        "when this file was written, so the run was from the working tree at that commit. Rerun npm run "
        "checks -- baseline --commit <sha> after committing: the run is deterministic, so only this field "
        "changes."
    ),
    (
        "The sentences below carry the numbers of the first run. Re-pinning this file after a count "
        "changes means rewriting them in scripts/omics/checks/cli.ts first, or they describe a run that "
        "no longer exists."
    ),
    (
        "C1 minority-unit, C2 and C4 reproduce the figures the design predicted exactly: 5,093 over 43 "
        "metrics; 1,256 splitting 58, 206 and 992; 338 splitting high 171, no 161 and max 6. C1 "
        "unit-scope-note is new work and is measured here for the first time."
    ),
    (
        "C3: the prototype figure of 170 did not reproduce and was not inherited, and nor did the two "
        "other figures in circulation (1,640 from a strict check, 603 from a naive half-up check). "
        "parseCell measures 3,436 findings over 51,892 results holding both values, and the split is the "
        "deliverable: 1,784 printed values parseCell cannot read, 1,473 where the printed value is the "
        "numeric value rounded, 167 where the stored number is the second number of a two-number cell, 6 "
        "off by a factor of 100 and 6 disagreeing outright. The last two rules, 12 records, are one "
        "source printing numbers with a dot thousands separator and a comma decimal."
    ),
    (
        "C5: neither prototype grouping was inherited (676 of 2,446 columns and 19,374 results by one, "
        "362 of 530 tables and 32,141 results by the other). This implementation groups by source, table "
        "or figure anchor and column, which column_coherence.ts states and argues for: 146 of 2,092 "
        "columns, holding 2,436 of the 34,688 results that could be placed in a column."
    ),
    (
        "C9: this implementation reports 187 groups where the design carried 171. The difference is the "
        "grouping, not the store, and duplicate_artifact.ts says why."
    ),
    (
        "C13: zero is the expected count. The 2,574 records of "
        "docs/reviews/2026-11-16-attribute-normalisation.md were migrated before this check existed; the "
        "ratchet is what keeps the count at zero."
    ),
)

#: A count as a baseline file holds it. JSON writes a whole number either way.
type Count = int | float


@dataclass(frozen=True)
class BaselineCheck:
    """One check as the baseline pins it."""

    #: None for a check the file gives no title, which is then named by its ID alone.
    title: str | None
    findings: Count
    rules: Mapping[str, Count]


@dataclass(frozen=True)
class Baseline:
    """The counts a baseline file pins: each rule, each check and the total."""

    total_findings: Count
    checks: Mapping[str, BaselineCheck]


@dataclass(frozen=True)
class Rise:
    """One count above its baseline."""

    key: str
    count: Count
    baseline: Count
    rise: Count


def _count(value: JsonValue, where: str) -> Count:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoreError(f"{where} is not a number")
    return value


def baseline_of(value: JsonValue, where: str = BASELINE_FILE) -> Baseline:
    """The counts of a baseline file's JSON. Raises `StoreError` for one that does not hold them."""
    checks = value.get("checks") if isinstance(value, dict) else None
    if not isinstance(value, dict) or not isinstance(checks, dict):
        raise StoreError(f"{where} is not a baseline: it has no checks")
    held: dict[str, BaselineCheck] = {}
    for check_id, check in checks.items():
        rules = check.get("rules") if isinstance(check, dict) else None
        if not isinstance(check, dict) or not isinstance(rules, dict):
            raise StoreError(f"{where} gives the check {check_id} no rules")
        title = check.get("title")
        if title is not None and not isinstance(title, str):
            raise StoreError(f"{where} gives the check {check_id} a title that is not text")
        held[check_id] = BaselineCheck(
            title,
            _count(check.get("findings"), f"the findings of {check_id} in {where}"),
            {
                rule: _count(count, f"the count of {check_id}.{rule} in {where}")
                for rule, count in rules.items()
            },
        )
    return Baseline(_count(value.get("total_findings"), f"the total findings in {where}"), held)


def read_baseline(path: Path) -> Baseline:
    """The baseline a file holds. Raises `StoreError` for one that cannot be read or is not one."""
    try:
        value = js_parse(path.read_bytes().decode("utf-8"))
    except OSError as error:
        raise StoreError(f"{path} cannot be read: {error}") from error
    except ValueError as error:
        raise StoreError(f"{path} is not JSON: {error}") from error
    return baseline_of(value, str(path))


def ratchet_counts(source: Baseline | RunResult) -> dict[str, Count]:
    """`ratchetCounts`: every count a later run must not exceed.

    Each check by its ID, each rule as the ID, a dot and the rule, and `total`: the total a
    baseline states, or the findings of a run added up.
    """
    counts: dict[str, Count] = {}
    if isinstance(source, Baseline):
        entries = [(check_id, check.findings, check.rules) for check_id, check in source.checks.items()]
    else:
        entries = [(summary.id, summary.findings, summary.rules) for summary in source.summaries]
    total: Count = 0
    for check_id, findings, rules in entries:
        counts[check_id] = findings
        total += findings
        for rule, count in rules.items():
            counts[f"{check_id}.{rule}"] = count
    counts["total"] = source.total_findings if isinstance(source, Baseline) else total
    return {key: counts[key] for key in js_key_order(counts)}


def rises(baseline: Baseline, run: RunResult) -> list[Rise]:
    """`risesAboveBaseline`: the counts of a run above their baseline, in the order of their keys.

    A count the baseline does not pin is not compared.
    """
    want = ratchet_counts(baseline)
    got = ratchet_counts(run)
    return [
        Rise(key, got[key], want[key], got[key] - want[key])
        for key in sorted(got, key=utf16_key)
        if key in want and got[key] > want[key]
    ]


def above_baseline(baseline: Baseline, run: RunResult) -> list[str]:
    """`aboveBaseline`: counts above their baseline, as readable lines. Empty when nothing grew."""
    return [
        f"{rise.key}: {js_string(rise.count)}, baseline {js_string(rise.baseline)}"
        for rise in rises(baseline, run)
    ]


def _named(key: str, baseline: Baseline) -> str:
    """A count's key as the failure names it: the check with its title, and the rule."""
    if key == "total":
        return "total findings"
    check_id, dot, rule = key.partition(".")
    pinned = baseline.checks.get(check_id)
    check = f"{check_id} {pinned.title}" if pinned is not None and pinned.title else check_id
    return f"{check}, rule {rule}" if dot else f"{check} (check total)"


def ratchet_failure(baseline: Baseline, run: RunResult, baseline_file: str = BASELINE_FILE) -> str:
    """`ratchetFailure`: which check and rule rose, by how much, and the two ways forward.

    Empty when nothing grew. `baseline_file` is the name the TypeScript holds for the file,
    which is the default unless its environment names another.
    """
    grown = rises(baseline, run)
    if not grown:
        return ""
    lines = [
        f"  {_named(rise.key, baseline)}: {js_string(rise.count)}, "
        f"baseline {js_string(rise.baseline)}, up {js_string(rise.rise)}"
        for rise in grown
    ]
    return "\n".join([f"Check counts rose above {baseline_file}:", *lines, "", RATCHET_REMEDY])


def shrunk(baseline: Baseline, run: RunResult) -> int:
    """How many counts of a run are below their baseline."""
    want = ratchet_counts(baseline)
    return sum(key in want and count < want[key] for key, count in ratchet_counts(run).items())


def store_counts(given: CheckInput) -> dict[str, int]:
    """`storeCounts`: how much of the store a run read."""
    return {
        "records": len(given.records),
        "results": len(given.results),
        "sources": len(given.sources),
        "metric_concepts": len(given.metrics),
        "unit_concepts": len(given.units),
    }


def baseline_text(given: CheckInput, run: RunResult, date: str, commit: str) -> str:
    """The file `checks -- baseline` writes for a run, to the byte."""
    baseline: JsonObject = {
        "note": RATCHET_NOTE,
        "notes": list(BASELINE_NOTES),
        "commit": commit,
        "run_date": date,
        "store": dict(store_counts(given)),
        "total_findings": len(run.findings),
        "checks": {
            summary.id: {
                "title": summary.title,
                "findings": summary.findings,
                "rules": dict(summary.rules),
                "context": dict(summary.context),
            }
            for summary in run.summaries
        },
    }
    return js_indented(baseline) + "\n"


def summary_lines(run: RunResult) -> list[str]:
    """`printSummary`: each check with its rules and its context, and the total."""
    lines: list[str] = []
    for summary in run.summaries:
        lines.append(f"{summary.id} {summary.title}: {summary.findings}")
        lines.extend(f"    {rule}: {count}" for rule, count in summary.rules.items())
        lines.extend(f"    ({key}: {count})" for key, count in summary.context.items())
    return [*lines, f"total findings: {len(run.findings)}"]


def compared(baseline: Baseline | None, run: RunResult, baseline_file: str = BASELINE_FILE) -> str:
    """What `checks -- report` says of a run against the baseline, or of there being none."""
    if baseline is None:
        return f"No {baseline_file} yet; run npm run checks -- baseline to write one."
    failure = ratchet_failure(baseline, run, baseline_file)
    if failure:
        return failure
    below = shrunk(baseline, run)
    if below:
        return f"Below {baseline_file} on {below} counts; lower the baseline in the same PR."
    return f"Counts equal {baseline_file}."


@dataclass(frozen=True)
class Command:
    """One run of `checks -- report` or `checks -- baseline`."""

    #: A checkout of the data repository.
    root: Path
    #: The date the report is written under and a baseline records.
    date: str
    #: The baseline file, as the TypeScript names it: a path from the checkout.
    baseline_file: str = BASELINE_FILE
    #: Where to write the report in place of the checkout's own folder of reports.
    out: Path | None = None
    #: The commit a baseline records.
    commit: str = ""

    @property
    def report_file(self) -> str:
        """The report as the TypeScript names it, from the checkout."""
        return f"{REPORT_DIR}/checks-{self.date}.tsv"


def _write_bytes(path: Path, text: str) -> None:
    """Write a file as Node writes text: the folder made if missing, U+FFFD for half a character."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(js_utf8(text))


def _run(command: Command, clock: Callable[[], int]) -> tuple[CheckInput, RunResult, list[str]]:
    """Run the pack, write the report and say what both commands say first."""
    started = clock()
    given = load_input(command.root)
    run = run_all(given)
    _write_bytes(command.out or command.root / command.report_file, report_tsv(run.findings))
    named = command.report_file if command.out is None else str(command.out)
    took = fixed((clock() - started) / 1000, 1)
    said = [*summary_lines(run), "", f"{len(run.findings)} findings written to {named} in {took} s"]
    return given, run, said


def report(command: Command, clock: Callable[[], int]) -> str:
    """`checks -- report`: run every check, write the report, and compare with the baseline.

    Returns what the TypeScript prints. `clock` gives the time in milliseconds, as
    `Date.now` does, for the line that says how long the run took. Raises `StoreError` or
    `VocabularyError` for a checkout that cannot be read.
    """
    _, run, said = _run(command, clock)
    path = command.root / command.baseline_file
    # No name is no file: `fs.existsSync("")` is false, where the checkout itself exists.
    pinned = read_baseline(path) if command.baseline_file and path.exists() else None
    return "\n".join([*said, "", compared(pinned, run, command.baseline_file)])


def baseline(command: Command, clock: Callable[[], int]) -> str:
    """`checks -- baseline`: run every check, write the report and the baseline file.

    Returns what the TypeScript prints.
    """
    given, run, said = _run(command, clock)
    _write_bytes(
        command.root / command.baseline_file, baseline_text(given, run, command.date, command.commit)
    )
    return "\n".join([*said, f"Wrote {command.baseline_file}."])
