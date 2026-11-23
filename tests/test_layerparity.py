"""The layer gate, on small checkouts whose registry the TypeScript wrote.

The recorder lays out a tree, runs `npm run layers -- build` in it and keeps the two files
the TypeScript writes. Here that tree is the checkout at the commit that wrote the registry,
a second tree with the same objects mirrored by a public release is the pinned checkout,
and the gate is held to what it must say when everything agrees and to each way it can fail.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from rewire_evidence import gates, layerparity, pdftext, registry
from rewire_evidence.canonical import canonical_line
from rewire_evidence.jsonl import StoreError
from rewire_evidence.textlayer import LayerFailure
from tests.layered import LAYERS, OBJECTS, key_of, row_for

TREE: dict[str, Any] = next(tree for tree in LAYERS["commands"] if tree["name"] == "whole")
#: What the TypeScript's `build` wrote for the tree.
WRITTEN: dict[str, str] = TREE["runs"][1][0]["written"]
NAMES = ("text", "html", "jats", "zipPlain", "png", "plainWorkbook", "gzText", "empty")
PDFTOTEXT = "pdftotext version 26.08.0"
PDFINFO = "pdfinfo version 26.08.0"
SHARDS = "0123456789abcdef"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Checkouts:
    """A pinned checkout, the checkout the registry was written at, and a folder of objects."""

    def __init__(self, base: Path) -> None:
        self.root = base / "store"
        self.layers_root = base / "layers-ref"
        self.objects = base / "objects"
        self.expected = base / "store-layers.json"
        self.then = {key_of(name): row_for(name, {"source_ids": [name]}) for name in NAMES}
        #: At the pinned commit every object but the last is mirrored by a public release.
        self.now = {
            key: {**row, "visibility": "public", "archive_tier": "t0-public", "mirrors": ["github-release"]}
            for key, row in self.then.items()
        }
        self.now[key_of("empty")] = dict(self.then[key_of("empty")])
        self.registry = WRITTEN[registry.REGISTRY_FILE]
        self.unsupported = WRITTEN[registry.UNSUPPORTED_FILE]
        self.pdftotext = PDFTOTEXT
        self.pdfinfo = PDFINFO
        self.stale = ["visibility"]
        self.held = set(NAMES)

    def manifest(self, root: Path, rows: dict[str, dict[str, Any]]) -> bytes:
        whole = b""
        for shard in SHARDS:
            lines = [canonical_line(row) for key, row in sorted(rows.items()) if key.startswith(shard)]
            path = root / f"data/archive/objects/{shard}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
            whole += path.read_bytes()
        return whole

    def write(self) -> None:
        then = self.manifest(self.layers_root, self.then)
        now = self.manifest(self.root, self.now)
        for file, text in (
            (registry.REGISTRY_FILE, self.registry),
            (registry.UNSUPPORTED_FILE, self.unsupported),
        ):
            (self.root / file).parent.mkdir(parents=True, exist_ok=True)
            (self.root / file).write_bytes(text.encode("utf-8"))
        for name in self.held:
            held = self.objects / key_of(name)[:2] / f"{key_of(name)}.gz"
            held.parent.mkdir(parents=True, exist_ok=True)
            held.write_bytes(gzip.compress(OBJECTS[name]))
        self.expected.write_text(json.dumps(self.expectation(digest(then), digest(now))), encoding="utf-8")

    def expectation(self, then: str, now: str) -> dict[str, Any]:
        """What a careful recording of these two checkouts states, sizes and all."""
        public = {key for key, row in self.now.items() if row["mirrors"] == ["github-release"]}
        rows = [json.loads(line) for line in self.registry.splitlines()]
        reasons = [json.loads(line) for line in self.unsupported.splitlines()]
        return {
            "recorded_from": {"store_ref": "a" * 40, "layers_ref": "b" * 40},
            "registry": {"lines": len(rows), "sha256": digest(self.registry.encode("utf-8"))},
            "unsupported": {"lines": len(reasons), "sha256": digest(self.unsupported.encode("utf-8"))},
            "manifest": {"rows": len(self.then), "sha256": then},
            "pinned_manifest": {"rows": len(self.now), "sha256": now},
            "public": {
                "objects": len(public),
                "rows": sum(1 for row in rows if row["object_sha256"] in public),
                "unsupported": sum(1 for row in reasons if row["object_sha256"] in public),
            },
            "pdftotext": PDFTOTEXT,
            "pdfinfo": PDFINFO,
            "normalisers": dict(Counter(row["normaliser"] for row in rows)),
            "stale_members": self.stale,
        }

    def verify(self) -> layerparity.LayerReport:
        recorded = json.loads(self.expected.read_text("utf-8"))
        return layerparity.verify_layers(self.root, self.layers_root, self.objects, recorded)

    def gate(self, *extra: str) -> int:
        return gates.layers_gate(
            [
                str(self.root),
                str(self.layers_root),
                str(self.objects),
                "--expected",
                str(self.expected),
                *extra,
            ],
            self.expected,
        )


@pytest.fixture
def checkouts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Checkouts:
    held = Checkouts(tmp_path)
    monkeypatch.setattr(layerparity, "pdftotext_version", lambda: held.pdftotext)
    monkeypatch.setattr(layerparity, "pdfinfo_version", lambda: held.pdfinfo)
    monkeypatch.setattr(pdftext, "has_pdftotext", lambda: False)
    return held


def test_every_object_held_gives_the_committed_lines_and_the_files_whole(
    checkouts: Checkouts, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checkouts.write()
    found = checkouts.verify()
    assert found.problems == []
    assert found.whole_files is True
    assert dict(found.objects) == {"public": 7, "restricted": 1, "checked": 8}
    rows, unsupported = found.total(unsupported=False), found.total(unsupported=True)
    assert rows.row() == {"made": 11, "expected": 11, "same": 11}
    assert unsupported.row() == {"made": 3, "expected": 3, "same": 3}
    assert {name: held.same for name, held in found.rows.items()} == {
        "text-utf8@1": 4,
        "html-text@1": 1,
        "html-table-grid@1": 1,
        "europepmc-fulltext-xml@1": 2,
        "jats-table-grid@1": 2,
        "ooxml-sheet-tsv@1": 1,
        "unsupported": 3,
    }
    # Every row of a public object would be written otherwise from the pinned manifest.
    assert (found.stale_rows, dict(found.stale)) == (11, {"visibility": 11})
    assert {name: held.row() for name, held in found.written.items()} == {
        registry.REGISTRY_FILE: {"made": 11, "expected": 11, "same": 11},
        registry.UNSUPPORTED_FILE: {"made": 3, "expected": 3, "same": 3},
    }

    report = tmp_path / "report.json"
    assert checkouts.gate("--json", str(report)) == 0
    said = capsys.readouterr()
    assert "registry rows reproduced: 11 of 11, 0 not checked" in said.out
    assert "both files written whole from every object: the committed files" in said.out
    assert "checked rows the pinned manifest would write otherwise: 11, in visibility (11)" in said.out
    assert said.out.endswith(
        "11 registry rows and 3 unsupported rows are derived again from 8 objects, each the committed line. "
        "0 rows of 0 restricted objects were not checked.\n"
    )
    measured = json.loads(report.read_text("utf-8"))
    assert (measured["rows"], measured["whole_files"], measured["problems"]) == (
        found.total(unsupported=False).row(),
        True,
        [],
    )
    assert measured["pdf_rows"] == {"made": 0, "expected": 0, "same": 0}
    assert (measured["pdftotext"], measured["pdfinfo"]) == (PDFTOTEXT, PDFINFO)
    assert f"  {PDFTOTEXT}, {PDFINFO}\n" in said.out


def test_a_restricted_object_that_is_not_held_is_counted_and_not_checked(
    checkouts: Checkouts, capsys: pytest.CaptureFixture[str]
) -> None:
    # The zip is restricted here, and the folder holds neither it nor the empty object.
    checkouts.now[key_of("zipPlain")] = dict(checkouts.then[key_of("zipPlain")])
    checkouts.held -= {"zipPlain", "empty"}
    checkouts.write()
    found = checkouts.verify()
    assert found.problems == []
    assert found.whole_files is None
    assert dict(found.objects) == {"public": 6, "restricted": 2, "checked": 6, "restricted_not_checked": 2}
    assert dict(found.not_checked) == {
        "text-utf8@1": 2,
        "europepmc-fulltext-xml@1": 1,
        "jats-table-grid@1": 1,
        "unsupported": 2,
    }
    assert found.total(unsupported=False).row() == {"made": 7, "expected": 11, "same": 7}
    assert checkouts.gate() == 0
    said = capsys.readouterr().out
    assert "both files written whole from every object: not asked, an object was not held" in said
    assert said.endswith("4 rows of 2 restricted objects were not checked.\n")


def test_a_public_object_that_is_missing_or_is_not_its_key_fails_the_gate(
    checkouts: Checkouts, capsys: pytest.CaptureFixture[str]
) -> None:
    checkouts.held -= {"html"}
    checkouts.write()
    wrong = checkouts.objects / key_of("text")[:2] / f"{key_of('text')}.gz"
    wrong.write_bytes(gzip.compress(b"other bytes"))
    found = checkouts.verify()
    assert sorted(found.problems[:2]) == sorted(
        [
            f"{wrong} differs from its listed SHA-256",
            f"{key_of('html')} is mirrored by a public release and is not in {checkouts.objects}",
        ]
    )
    assert found.problems[2:] == [
        (
            "8 registry rows are reproduced and the public objects give 11, "
            "so fewer were checked than a run has to check"
        )
    ]
    assert found.whole_files is None
    assert checkouts.gate() == 1
    assert "3 problems. 8 of 11 registry rows are reproduced." in capsys.readouterr().err


def test_a_line_that_differs_is_named_with_its_object(
    checkouts: Checkouts, capsys: pytest.CaptureFixture[str]
) -> None:
    key = key_of("text")
    checkouts.registry = checkouts.registry.replace('"characters":23', '"characters":24', 1)
    checkouts.unsupported = checkouts.unsupported.replace("raster image, no text layer", "a raster image")
    checkouts.write()
    found = checkouts.verify()
    assert len(found.problems) == 6
    assert found.problems[0].startswith(
        f"{key_of('png')}: a unsupported line is made that the committed unsupported"
    )
    assert any(
        problem.startswith(f"{key}: a text-utf8@1 line is made that the committed registry does not hold")
        for problem in found.problems
    )
    assert any(
        problem.startswith(f"{key}: a committed registry line is not made") for problem in found.problems
    )
    # The restricted object is held here, so the unsupported rows reproduced still number the public ones.
    assert found.problems[-2:] == [
        "the two files, written whole from every object, are not the committed files",
        (
            "10 registry rows are reproduced and the public objects give 11, "
            "so fewer were checked than a run has to check"
        ),
    ]
    assert found.whole_files is False
    assert checkouts.gate() == 1
    said = capsys.readouterr()
    assert "both files written whole from every object: NOT the committed files" in said.out
    assert "6 problems. 10 of 11 registry rows are reproduced." in said.err


def test_the_committed_files_are_held_to_the_digests_and_to_their_own_order(checkouts: Checkouts) -> None:
    checkouts.write()
    lines = checkouts.registry.splitlines()
    (checkouts.root / registry.REGISTRY_FILE).write_text(
        "".join(f"{line}\n" for line in lines[::-1]), encoding="utf-8"
    )
    spaced = checkouts.unsupported.replace('":"', '": "', 1)
    (checkouts.root / registry.UNSUPPORTED_FILE).write_text(spaced, encoding="utf-8")
    found = checkouts.verify()
    assert found.problems[:4] == [
        f"{registry.REGISTRY_FILE} in the checkout is not the file the expectation names by digest",
        f"{registry.UNSUPPORTED_FILE} in the checkout is not the file the expectation names by digest",
        f"{registry.REGISTRY_FILE}, written again from its own rows, is not the committed file",
        f"{registry.UNSUPPORTED_FILE}, written again from its own rows, is not the committed file",
    ]
    assert found.written[registry.REGISTRY_FILE].same < 11


def test_no_layer_is_derived_under_another_pdftotext(
    checkouts: Checkouts, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkouts.pdftotext = "pdftotext version 24.02.0"
    checkouts.write()
    found = checkouts.verify()
    assert found.problems == [
        (
            "pdftotext names itself as 'pdftotext version 24.02.0' and the registry was written by "
            "'pdftotext version 26.08.0', so no PDF layer could agree"
        )
    ]
    assert found.objects["checked"] == 0

    def missing() -> str:
        raise LayerFailure("spawnSync pdftotext ENOENT")

    monkeypatch.setattr(layerparity, "pdftotext_version", missing)
    assert checkouts.verify().pdftotext == "spawnSync pdftotext ENOENT"
    assert checkouts.gate() == 1


def test_no_layer_is_derived_beside_another_pdfinfo(checkouts: Checkouts) -> None:
    # The pages a row states are pdfinfo's, so the pinned pdftotext beside another pdfinfo is refused.
    checkouts.pdfinfo = "pdfinfo version 24.02.0"
    checkouts.write()
    found = checkouts.verify()
    assert found.problems == [
        (
            "pdfinfo names itself as 'pdfinfo version 24.02.0' and the registry was written by "
            "'pdfinfo version 26.08.0', so no page count could agree"
        )
    ]
    assert (found.pdftotext, found.objects["checked"]) == (PDFTOTEXT, 0)
    assert checkouts.gate() == 1


def test_what_the_pinned_manifest_would_write_otherwise_is_held_to_the_expectation(
    checkouts: Checkouts,
) -> None:
    # The home moved, a source was added and a served type changed, and the expectation knows of one.
    key = key_of("text")
    checkouts.now[key] = {
        **checkouts.now[key],
        "store": "local-cache",
        "source_ids": ["text", "more"],
        "media_type": "text/csv",
    }
    checkouts.write()
    found = checkouts.verify()
    assert dict(found.stale) == {"visibility": 11, "store": 1, "source_ids": 1, "media_type": 1}
    assert found.problems == [
        (
            "against the pinned checkout's own manifest the rows differ in "
            "['media_type', 'source_ids', 'store', 'visibility'], and the expectation states ['visibility']"
        )
    ]


def test_an_object_in_one_manifest_only_or_that_cannot_be_read_is_a_problem(checkouts: Checkouts) -> None:
    odd = key_of("wideOdd")
    checkouts.then[odd] = row_for("wideOdd")
    checkouts.now[odd] = dict(checkouts.then[odd])
    checkouts.held.add("wideOdd")
    del checkouts.now[key_of("png")]
    checkouts.write()
    found = checkouts.verify()
    assert f"{key_of('png')} is in one manifest and not the other" in found.problems
    assert f"{odd} could not be read: Buffer size must be a multiple of 16-bits" in found.problems
    assert found.whole_files is None


def test_with_no_object_held_the_gate_holds_nothing_and_says_so(
    checkouts: Checkouts, capsys: pytest.CaptureFixture[str]
) -> None:
    checkouts.now = {key: dict(row) for key, row in checkouts.then.items()}
    checkouts.held = set()
    checkouts.stale = []
    checkouts.write()
    assert checkouts.verify().problems == []
    assert checkouts.gate() == 2
    said = capsys.readouterr()
    assert "Cannot verify: with no object held, the derivation is held to nothing" in said.err
    assert "checked rows the pinned manifest would write otherwise: 0, in no member" in said.out


def test_a_run_that_cannot_be_made_is_not_a_disagreement(
    checkouts: Checkouts, capsys: pytest.CaptureFixture[str]
) -> None:
    checkouts.write()
    recorded = json.loads(checkouts.expected.read_text("utf-8"))
    with pytest.raises(StoreError, match="is not the one the registry was written from"):
        layerparity.verify_layers(checkouts.root, checkouts.root, checkouts.objects, recorded)
    with pytest.raises(StoreError, match="the expectation does not state manifest"):
        layerparity.verify_layers(
            checkouts.root, checkouts.layers_root, checkouts.objects, {**recorded, "manifest": None}
        )
    (checkouts.root / registry.UNSUPPORTED_FILE).write_bytes(b"\xff\n")
    with pytest.raises(StoreError, match=r"unsupported\.jsonl is not UTF-8"):
        checkouts.verify()
    (checkouts.root / registry.REGISTRY_FILE).unlink()
    with pytest.raises(StoreError, match=r"registry\.jsonl cannot be read"):
        checkouts.verify()
    assert checkouts.gate() == 2
    assert capsys.readouterr().err.startswith("Cannot verify: ")


def test_a_thinner_checkout_cannot_pass_by_holding_fewer_public_objects(
    checkouts: Checkouts, capsys: pytest.CaptureFixture[str]
) -> None:
    # One object stops being mirrored in a copy of the pinned manifest, and its bytes are not held.
    checkouts.write()
    stated = json.loads(checkouts.expected.read_text("utf-8"))
    checkouts.now[key_of("html")] = {**checkouts.now[key_of("html")], "mirrors": []}
    checkouts.held -= {"html"}
    (checkouts.objects / key_of("html")[:2] / f"{key_of('html')}.gz").unlink()
    now = checkouts.manifest(checkouts.root, checkouts.now)
    with pytest.raises(StoreError, match=r"is not the pinned one, which the expectation holds by the digest"):
        checkouts.verify()
    assert checkouts.gate() == 2
    assert capsys.readouterr().err.startswith("Cannot verify: ")

    # With the digest restated and nothing else, every size the run fell short of is named.
    stated["pinned_manifest"]["sha256"] = digest(now)
    checkouts.expected.write_text(json.dumps(stated), encoding="utf-8")
    found = checkouts.verify()
    assert found.problems == [
        "objects a public release mirrors: 6 and the expectation states 7",
        (
            "9 registry rows are reproduced and the public objects give 11, "
            "so fewer were checked than a run has to check"
        ),
    ]
    assert checkouts.gate() == 1


def test_the_sizes_the_expectation_states_are_each_held(checkouts: Checkouts) -> None:
    checkouts.write()
    stated = json.loads(checkouts.expected.read_text("utf-8"))
    stated["registry"]["lines"] += 1
    stated["unsupported"]["lines"] -= 1
    stated["manifest"]["rows"] = 9
    stated["pinned_manifest"]["rows"] = 7
    stated["normalisers"]["text-utf8@1"] = 5
    stated["public"] = {"objects": 7, "rows": 11}
    checkouts.expected.write_text(json.dumps(stated), encoding="utf-8")
    assert checkouts.verify().problems == [
        f"{registry.REGISTRY_FILE} holds 11 lines and the expectation states 12",
        f"{registry.UNSUPPORTED_FILE} holds 3 lines and the expectation states 2",
        "the manifest the registry was written from holds 8 and the expectation states 9",
        "the pinned manifest holds 8 and the expectation states 7",
        (
            "the registry holds {'europepmc-fulltext-xml@1': 2, 'html-table-grid@1': 1, 'html-text@1': 1, "
            "'jats-table-grid@1': 2, 'ooxml-sheet-tsv@1': 1, 'text-utf8@1': 4} rows by normaliser and the "
            f"expectation states {stated['normalisers']}"
        ),
        (
            "3 unsupported rows are reproduced and the public objects give None, "
            "so fewer were checked than a run has to check"
        ),
    ]
    del stated["public"]
    checkouts.expected.write_text(json.dumps(stated), encoding="utf-8")
    with pytest.raises(StoreError, match="the expectation does not state public"):
        checkouts.verify()


def test_a_last_line_that_no_newline_ends_is_read_and_is_a_problem(
    checkouts: Checkouts, capsys: pytest.CaptureFixture[str]
) -> None:
    # The row of the one restricted object goes last with no newline, and the digest is restated.
    lines = checkouts.unsupported.splitlines()
    (restricted,) = [line for line in lines if json.loads(line)["object_sha256"] == key_of("empty")]
    lines.remove(restricted)
    checkouts.unsupported = "".join(f"{line}\n" for line in lines) + restricted
    checkouts.held -= {"empty"}
    checkouts.write()
    found = checkouts.verify()
    assert found.problems == [
        f"{registry.UNSUPPORTED_FILE} ends in a line that no newline ends",
        f"{registry.UNSUPPORTED_FILE}, written again from its own rows, is not the committed file",
    ]
    # The line is read: all three are counted, and the one not held is counted as not checked.
    assert found.written[registry.UNSUPPORTED_FILE].row() == {"made": 3, "expected": 3, "same": 3}
    assert found.not_checked[layerparity.UNSUPPORTED] == 1
    assert checkouts.gate() == 1
    assert "2 problems." in capsys.readouterr().err


@pytest.mark.parametrize(
    ("file", "line", "refusal"),
    [
        (registry.REGISTRY_FILE, "{}", r"registry\.jsonl line 12: "),
        (registry.REGISTRY_FILE, "normaliser", r"registry\.jsonl line 1: "),
        (registry.REGISTRY_FILE, "container_path", r"registry\.jsonl line 1: "),
        (registry.REGISTRY_FILE, "extra", r"registry\.jsonl line 1: "),
        (registry.UNSUPPORTED_FILE, "{}", r"unsupported\.jsonl line 4: "),
        (registry.UNSUPPORTED_FILE, "[1]", r"unsupported\.jsonl line 4 is not a JSON object"),
        (registry.UNSUPPORTED_FILE, "reason", r"unsupported\.jsonl line 1: "),
    ],
)
def test_a_committed_row_that_is_not_one_is_named_with_its_line(
    checkouts: Checkouts, capsys: pytest.CaptureFixture[str], file: str, line: str, refusal: str
) -> None:
    checkouts.write()
    path = checkouts.root / file
    lines = path.read_text("utf-8").splitlines()
    if line.startswith(("{", "[")):
        lines.append(line)
    else:
        first = json.loads(lines[0])
        if line == "extra":
            first["extra"] = 1
        elif line == "container_path":
            first["container_path"] = 7
        else:
            del first[line]
        lines[0] = canonical_line(first)
    path.write_text("".join(f"{one}\n" for one in lines), encoding="utf-8")
    with pytest.raises(StoreError, match=refusal):
        checkouts.verify()
    assert checkouts.gate() == 2
    assert capsys.readouterr().err.startswith("Cannot verify: ")


# ---------------------------------------------------------------------------------------
# The expectation, and what the workflow pins of it.

ROOT = Path(__file__).parent.parent
EXPECTED: dict[str, Any] = json.loads((ROOT / "scripts/expected/store-layers.json").read_text("utf-8"))
WORKFLOW = (ROOT / ".github/workflows/checks.yml").read_text("utf-8")


def pin(name: str) -> str:
    (found,) = re.findall(rf'^ +{name}: "?([^"\n]+)"?$', WORKFLOW, re.MULTILINE)
    return str(found)


def test_the_workflow_pins_the_commits_the_pdftotext_and_every_count_the_gate_can_fall_short_of() -> None:
    source = EXPECTED["recorded_from"]
    assert (source["store_ref"], source["layers_ref"]) == (pin("STORE_REF"), pin("LAYERS_REF"))
    assert source["store_ref"] != source["layers_ref"]
    assert EXPECTED["pdftotext"] == f"pdftotext version {pin('POPPLER')}"
    assert EXPECTED["pdfinfo"] == f"pdfinfo version {pin('POPPLER')}"
    assert EXPECTED["stale_members"] == ["store"]

    # What is checked and what is not add up to what is committed, and to the manifest.
    rows, held_back = int(pin("LAYER_ROWS")), int(pin("LAYER_ROWS_NOT_CHECKED"))
    assert rows + held_back == EXPECTED["registry"]["lines"] == int(pin("LAYER_REGISTRY_LINES")) == 2595
    unsupported = int(pin("LAYER_UNSUPPORTED")) + int(pin("LAYER_UNSUPPORTED_NOT_CHECKED"))
    assert unsupported == EXPECTED["unsupported"]["lines"] == int(pin("LAYER_UNSUPPORTED_LINES")) == 245
    assert (
        int(pin("LAYER_OBJECTS")) + int(pin("LAYER_RESTRICTED_OBJECTS"))
        == EXPECTED["manifest"]["rows"]
        == 1167
    )
    # The gate holds itself to the same sizes, so a run of the script alone cannot check less.
    assert EXPECTED["pinned_manifest"]["rows"] == EXPECTED["manifest"]["rows"]
    assert EXPECTED["pinned_manifest"]["sha256"] != EXPECTED["manifest"]["sha256"]
    assert EXPECTED["public"] == {
        "objects": int(pin("LAYER_OBJECTS")),
        "rows": rows,
        "unsupported": int(pin("LAYER_UNSUPPORTED")),
    }
    by_normaliser = {
        "text-utf8@1": "LAYER_TEXT_ROWS",
        "europepmc-fulltext-xml@1": "LAYER_XML_ROWS",
        "html-text@1": "LAYER_HTML_ROWS",
        "jats-table-grid@1": "LAYER_JATS_GRID_ROWS",
        "html-table-grid@1": "LAYER_HTML_GRID_ROWS",
        "ooxml-sheet-tsv@1": "LAYER_SHEET_ROWS",
        "ooxml-document-text@1": "LAYER_DOCUMENT_ROWS",
        "pdf-text-layer@1": "LAYER_PDF_ROWS",
    }
    assert sorted(by_normaliser) == sorted(EXPECTED["normalisers"])
    assert sum(EXPECTED["normalisers"].values()) == EXPECTED["registry"]["lines"]
    assert sum(int(pin(name)) for name in by_normaliser.values()) == rows
    assert all(
        int(pin(name)) <= EXPECTED["normalisers"][normaliser] for normaliser, name in by_normaliser.items()
    )

    # The gate's step reads each of them from what was measured, and the job runs where the
    # registry's pdftotext can be built to write the same bytes.
    (gate,) = [
        step for step in re.split(r"\n(?=      - )", WORKFLOW) if "scripts/verify_store_layers.py" in step
    ]
    pinned = set(re.findall(r":\$(LAYER_[A-Z_]+)\"", gate))
    assert pinned == set(re.findall(r"^      (LAYER_[A-Z_]+):", WORKFLOW, re.MULTILINE))
    assert "set -o pipefail" in gate
    layers_job = WORKFLOW[WORKFLOW.index("\n  layers:\n") :]
    assert "runs-on: macos-15\n" in layers_job
    assert "scripts/fetch_public_objects.py store objects" in layers_job
    # The build is cached on the sources and on the recipe, and both programs are held to the release.
    assert "-${{ hashFiles('scripts/build_poppler.sh') }}\n" in layers_job
    assert 'bash scripts/build_poppler.sh "$HOME/poppler"' in layers_job
    assert "for program in pdftotext pdfinfo; do" in layers_job
    recipe = (ROOT / "scripts/build_poppler.sh").read_text("utf-8")
    assert all(f'"${{{name}}}  ' in recipe for name in ("POPPLER_SHA256", "POPPLER_DATA_SHA256"))
