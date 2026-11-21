"""Line files are split on the newline and on nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from rewire_evidence.canonical import JsonObject, JsonValue, js_stringify
from rewire_evidence.jsonl import (
    StoreError,
    append_text,
    join_lines,
    parse_row,
    read_lines,
    read_rows,
    read_values,
    text_field,
    write_text,
    write_text_atomically,
)


def test_a_line_is_ended_by_a_newline_and_by_nothing_else(tmp_path: Path) -> None:
    # JSON.stringify leaves U+2028, U+2029 and U+0085 unescaped inside a string, and a
    # carriage return can sit in one too. str.splitlines() would cut each of these lines.
    rows = [
        {"id": "a", "text": "one\u2028two\u2029three\u0085four"},
        {"id": "b", "text": "tab\tand\x0bvt\x1cfs"},
    ]
    file = tmp_path / "rows.jsonl"
    write_text(file, join_lines(js_stringify(row) for row in rows))
    assert len(file.read_text("utf-8").splitlines()) > len(rows)
    assert read_rows(file) == rows


def test_blank_lines_are_skipped_as_the_typescript_skips_them(tmp_path: Path) -> None:
    file = tmp_path / "rows.jsonl"
    file.write_bytes(b'{"id":"a"}\n\n{"id":"b"}')
    assert read_lines(file) == ['{"id":"a"}', '{"id":"b"}']


def test_bytes_that_are_not_utf8_are_refused(tmp_path: Path) -> None:
    file = tmp_path / "rows.jsonl"
    file.write_bytes(b'{"id":"\xff"}\n')
    with pytest.raises(StoreError, match="is not UTF-8"):
        read_lines(file)


def test_a_line_that_is_not_an_object_is_refused_by_name(tmp_path: Path) -> None:
    file = tmp_path / "rows.jsonl"
    file.write_bytes(b'{"id":"a"}\n{"id":\n')
    with pytest.raises(StoreError, match=r"rows\.jsonl:2 is not JSON"):
        read_rows(file)
    with pytest.raises(StoreError, match="here is not a JSON object"):
        parse_row("[1,2]", "here")
    with pytest.raises(StoreError, match="here is not JSON: NaN is not JSON"):
        parse_row('{"a":NaN}', "here")


def test_a_field_the_layout_needs_must_be_text() -> None:
    assert text_field({"id": "a"}, "id", "here") == "a"
    rows: list[JsonObject] = [{}, {"id": 1}, {"id": None}, {"id": ["a"]}]
    for row in rows:
        with pytest.raises(StoreError, match="here has no string 'id'"):
            text_field(row, "id", "here")


def test_a_file_body_ends_every_line_and_is_empty_for_no_lines() -> None:
    assert join_lines(["a", "b"]) == "a\nb\n"
    assert join_lines([]) == ""


def test_writing_makes_the_folder_and_keeps_the_bytes(tmp_path: Path) -> None:
    file = tmp_path / "new" / "folder" / "rows.jsonl"
    write_text(file, "caf\u00e9\r\n")
    assert file.read_bytes() == "caf\u00e9\r\n".encode()


def test_an_atomic_write_replaces_the_file_and_leaves_no_temporary(tmp_path: Path) -> None:
    file = tmp_path / "new" / "rows.jsonl"
    write_text_atomically(file, "first\n")
    write_text_atomically(file, "second\n")
    assert file.read_bytes() == b"second\n"
    assert [path.name for path in file.parent.iterdir()] == ["rows.jsonl"]


def test_an_atomic_write_that_fails_leaves_the_old_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The new bytes go to a temporary file beside the target and reach it by a rename, so
    # a write that dies before the rename has not touched the file a reader sees.
    file = tmp_path / "rows.jsonl"
    write_text_atomically(file, "first\n")
    renamed: list[tuple[Path, Path]] = []

    def refuse(self: Path, target: Path) -> Path:
        renamed.append((self, target))
        raise OSError("the disk went away")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError, match="the disk went away"):
        write_text_atomically(file, "second\n")
    assert file.read_bytes() == b"first\n"
    assert len(renamed) == 1
    temporary, target = renamed[0]
    assert target == file
    assert temporary.parent == file.parent
    assert temporary.read_bytes() == b"second\n"


def test_appending_adds_to_the_end_and_makes_the_file(tmp_path: Path) -> None:
    file = tmp_path / "new" / "rows.jsonl"
    append_text(file, "first\n")
    append_text(file, "second\n")
    assert file.read_bytes() == b"first\nsecond\n"


def test_a_line_the_parser_reads_and_the_walk_cannot_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The parser and the walk that puts a value in JavaScript's form give up at different
    # depths, so a line can pass the first and fail the second.
    file = tmp_path / "rows.jsonl"
    file.write_bytes(b'{"id":"a"}\n[1]\n')

    def too_deep(_: object) -> None:
        raise RecursionError

    monkeypatch.setattr("rewire_evidence.jsonl.js_value", too_deep)
    with pytest.raises(StoreError) as refusal:
        read_values(file)
    assert str(refusal.value) == f"{file}:1 is nested too deeply to read"
    assert isinstance(refusal.value.__cause__, RecursionError)


@pytest.mark.parametrize("depth", [1, 100, 400, 900, 990, 1000, 1100, 2000, 5000, 20000, 200000])
def test_nesting_of_any_depth_is_read_or_refused_by_name(depth: int, tmp_path: Path) -> None:
    file = tmp_path / "rows.jsonl"
    file.write_bytes(b"[" * depth + b"]" * depth + b"\n")
    refused = None
    values: list[JsonValue] = []
    try:
        values = read_values(file)
    except StoreError as refusal:
        refused = str(refusal)
    if refused is not None:
        assert refused == f"{file}:1 is nested too deeply to read"
        assert depth > 400
        return
    (value,) = values
    nested = 0
    while value:
        assert isinstance(value, list)
        (value,) = value
        nested += 1
    assert nested == depth - 1
