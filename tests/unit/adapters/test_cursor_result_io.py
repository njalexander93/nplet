"""Verify Cursor persistence boundaries with isolated application and SQLite doubles."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor_results as reader
from nplet.lifecycle import task_workspace as core
from tests.unit.adapters.test_cursor_results import cursor_success_wire

pytestmark = pytest.mark.unit


def invocation() -> dict:
    """Return independent original-call metadata for the external reader boundary.

    Returns:
        One committed parent and newline-bearing call identity.
    """
    return {"parent_conversation": "parent", "native_call": "call\noriginal"}


def connection_rows(rows: list) -> MagicMock:
    """Expose the exact SQLite methods used by the reader without a database.

    Args:
        rows: Ordered fetchone results for exact-key queries.

    Returns:
        Signature-constrained connection whose cursor results follow the supplied rows.
    """
    # Keep SQL effects observable without modeling a persistence engine.
    connection = MagicMock(spec=sqlite3.Connection)
    cursors = []
    for row in rows:
        cursor = MagicMock(spec=sqlite3.Cursor)
        cursor.fetchone.return_value = row
        cursors.append(cursor)
    connection.execute.side_effect = cursors
    return connection


def test_exact_row_queries_only_correlated_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select one original call without consulting unrelated bubble values.

    Args:
        monkeypatch: Isolates the monotonic deadline.
    """
    # Provide metadata and a result independently of the reader's key construction.
    monkeypatch.setattr(reader.time, "monotonic", lambda: 0)
    headers = [{"bubbleId": "other", "type": 1}, {"bubbleId": "bubble/t:call\noriginal", "type": 2}]
    tool = {"name": "task_v2", "toolCallId": "call\noriginal"}
    connection = connection_rows(
        [("parent", json.dumps(headers)), (json.dumps({"toolFormerData": tool}),)]
    )
    assert reader.exact_row(connection, invocation(), 1) == (
        "bubbleId:parent:bubble/t:call\noriginal",
        tool,
    )
    assert connection.execute.call_count == 2
    assert connection.execute.call_args_list[0].args[1] == ("composerData:parent", reader.ROW_LIMIT)
    assert connection.execute.call_args_list[1].args[1] == (
        "bubbleId:parent:bubble/t:call\noriginal",
        reader.ROW_LIMIT,
    )
    connection.commit.assert_not_called()


@pytest.mark.parametrize(
    "row,code",
    [
        (None, "RESULT_NOT_AVAILABLE"),
        (("foreign", "[]"), "RESULT_SCHEMA_MISMATCH"),
        (("parent", None), "RESULT_SCHEMA_MISMATCH"),
        (("parent", "{}"), "SIZE_LIMIT"),
        (("parent", "[]"), "RESULT_NOT_AVAILABLE"),
        (("parent", "[null]"), "RESULT_SCHEMA_MISMATCH"),
        (("parent", '[{"bubbleId":7}]'), "RESULT_SCHEMA_MISMATCH"),
        (("parent", json.dumps([{"bubbleId": "x" * 4097}])), "RESULT_SCHEMA_MISMATCH"),
        (
            ("parent", json.dumps([{"bubbleId": "b/t:call\noriginal", "type": 1}])),
            "RESULT_SCHEMA_MISMATCH",
        ),
        (
            ("parent", json.dumps([{"bubbleId": "b/t:call\noriginal", "type": 2}] * 2)),
            "RESULT_SCHEMA_MISMATCH",
        ),
        (("parent", json.dumps([{}] * (reader.HEADER_LIMIT + 1))), "SIZE_LIMIT"),
    ],
)
def test_invalid_headers_never_fetch_a_bubble(
    monkeypatch: pytest.MonkeyPatch, row: object, code: str
) -> None:
    """Stop at invalid parent metadata without probing other result rows.

    Args:
        monkeypatch: Isolates time.
        row: Invalid external metadata row.
        code: Expected bounded diagnostic.
    """
    monkeypatch.setattr(reader.time, "monotonic", lambda: 0)
    connection = connection_rows([row])
    with pytest.raises(core.WorkspaceError) as captured:
        reader.exact_row(connection, invocation(), 1)
    assert captured.value.code == code
    assert connection.execute.call_count == 1
    connection.commit.assert_not_called()


@pytest.mark.parametrize(
    "row,code",
    [
        (None, "RESULT_NOT_AVAILABLE"),
        (("[]",), "RESULT_SCHEMA_MISMATCH"),
        (("{}",), "RESULT_SCHEMA_MISMATCH"),
        (
            (json.dumps({"toolFormerData": {"name": "other", "toolCallId": "call\noriginal"}}),),
            "RESULT_SCHEMA_MISMATCH",
        ),
    ],
)
def test_invalid_exact_bubble_never_produces_evidence(
    monkeypatch: pytest.MonkeyPatch, row: object, code: str
) -> None:
    """Reject the selected row without looking for a substitute result.

    Args:
        monkeypatch: Isolates time.
        row: Invalid exact-call row.
        code: Expected diagnostic.
    """
    monkeypatch.setattr(reader.time, "monotonic", lambda: 0)
    headers = [{"bubbleId": "b/t:call\noriginal", "type": 2}]
    connection = connection_rows([("parent", json.dumps(headers)), row])
    with pytest.raises(core.WorkspaceError) as captured:
        reader.exact_row(connection, invocation(), 1)
    assert captured.value.code == code
    assert connection.execute.call_count == 2


def test_pinned_source_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hash fixed source paths and reject changed semantics without touching installed apps.

    Args:
        monkeypatch: Replaces filesystem and platform boundaries for one call at a time.
    """
    # Only the three declared files may be opened; no caller selects an accepted source.
    monkeypatch.setattr(reader.sys, "platform", "darwin")
    monkeypatch.setattr(reader.time, "monotonic", lambda: 0)
    monkeypatch.setattr(reader, "APP", Path("/fixture/app"))
    digest = hashlib.sha256(b"source").hexdigest()
    monkeypatch.setattr(reader, "PINS", {"executor_sha256": ("executor", digest)})
    contents = {"package.json": b'{"version":"3.25.9"}', "executor": b"source"}
    opened = []

    def open_source(path: Path, mode: str) -> io.BytesIO:
        """Return selected immutable source bytes at the exact read-only boundary.

        Args:
            path: Fixed application file.
            mode: Required binary read mode.

        Returns:
            A closeable byte stream containing the selected fixture.
        """
        assert mode == "rb" and path.parent == reader.APP
        opened.append(path.name)
        return io.BytesIO(contents[path.name])

    monkeypatch.setattr(Path, "open", open_source)
    assert reader.pinned_sources(1) == {"executor_sha256": digest}
    assert opened == ["package.json", "executor"]
    # Each rejection retains the external boundary; no repair or write is attempted.
    for name, value, code in [
        ("package.json", b"x" * (256 * 1024 + 1), "SIZE_LIMIT"),
        ("package.json", b'{"version":"3.25.6"}', "RESULT_SCHEMA_MISMATCH"),
        ("executor", b"changed", "RESULT_SCHEMA_MISMATCH"),
    ]:
        previous = contents[name]
        contents[name] = value
        with pytest.raises(core.WorkspaceError) as captured:
            reader.pinned_sources(1)
        assert captured.value.code == code
        contents[name] = previous
    with pytest.raises(core.WorkspaceError) as captured:
        reader.pinned_sources(0)
    assert captured.value.code == "BUSY"
    monkeypatch.setattr(reader.sys, "platform", "linux")
    with pytest.raises(core.WorkspaceError) as captured:
        reader.pinned_sources(1)
    assert captured.value.code == "HOST_UNSUPPORTED"


@pytest.mark.parametrize(
    "failure,code",
    [
        ("", None),
        ("missing", "RESULT_NOT_AVAILABLE"),
        ("type", "RESULT_SCHEMA_MISMATCH"),
        ("base64", "RESULT_SCHEMA_MISMATCH"),
        ("oversized", "SIZE_LIMIT"),
        ("foreign-child", "RESULT_SCHEMA_MISMATCH"),
        ("sqlite-size", "SIZE_LIMIT"),
        ("sqlite-busy", "RESULT_NOT_AVAILABLE"),
        ("decode-expired", "BUSY"),
        ("projection-pin", "INVALID_REQUEST"),
    ],
)
def test_terminal_reader_closes_external_connection(
    monkeypatch: pytest.MonkeyPatch, failure: str, code: str | None
) -> None:
    """Return only validated metadata and close SQLite on every external-boundary outcome.

    Args:
        monkeypatch: Replaces fixed app and database I/O, retaining actual result parsing.
        failure: One explicit external or binary fault.
        code: Expected diagnostic, or None for valid evidence.
    """
    # Supply one bounded result without granting a real database or application path.
    clock = [0]
    monkeypatch.setattr(reader.time, "monotonic", lambda: clock[0])
    pins = {"executor_sha256": "a" * 64, "workbench_sha256": "b" * 64}
    if failure == "projection-pin":
        pins["workbench_sha256"] = "invalid-digest"
    monkeypatch.setattr(reader, "pinned_sources", lambda deadline: pins)
    connection = MagicMock(spec=sqlite3.Connection)
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(reader.sqlite3, "connect", connect)
    tool = {"toolCallBinary": base64.b64encode(cursor_success_wire()).decode()}
    request = invocation()
    if failure == "missing":
        tool.clear()
    elif failure == "type":
        tool["toolCallBinary"] = 7
    elif failure == "base64":
        tool["toolCallBinary"] = "%"
    elif failure == "oversized":
        tool["toolCallBinary"] = "a" * (4 * ((reader.BINARY_LIMIT + 2) // 3) + 1)
    elif failure == "foreign-child":
        request["child_conversation"] = "different"
    row = MagicMock(return_value=("exact-key", tool))
    if failure == "sqlite-size":
        row.side_effect = sqlite3.DataError()
    elif failure == "sqlite-busy":
        row.side_effect = sqlite3.OperationalError()
    monkeypatch.setattr(reader, "exact_row", row)
    if failure == "decode-expired":
        decode = reader.task_success

        def expire(raw: bytes, call: str) -> dict:
            """Finish valid decoding after the shared execution budget expires.

            Args:
                raw: Actual synthetic Task binary.
                call: Original call identity.

            Returns:
                Real parser metadata with an expired clock for the subsequent boundary.
            """
            result = decode(raw, call)
            clock[0] = 2
            return result

        monkeypatch.setattr(reader, "task_success", expire)
    # Success and rejection must both release the read-only database connection.
    if code is None:
        result = reader.read_terminal(request, 1)
        assert result["kind"] == "foreground-success"
        assert result["binary_sha256"] == hashlib.sha256(cursor_success_wire()).hexdigest()
        assert result["storage_key"] == "exact-key"
        assert set(result) == {
            "kind",
            "child_conversation",
            "is_background",
            "background_reason",
            "duration_ms",
            "executor_sha256",
            "workbench_sha256",
            "parent_conversation",
            "native_call",
            "storage_key",
            "binary_sha256",
        }
    else:
        with pytest.raises(core.WorkspaceError) as captured:
            reader.read_terminal(request, 1)
        assert captured.value.code == code
    connection.close.assert_called_once_with()
    connection.commit.assert_not_called()
    assert connect.call_args.kwargs == {"uri": True, "timeout": 0.05}
    assert connect.call_args.args == (reader.DATABASE.as_uri() + "?mode=ro",)
