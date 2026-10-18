"""Verify exact Cursor result rows through disposable SQLite and source files."""

import base64
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from nplet.adapters import common, cursor_results
from tests.unit.adapters.test_cursor_results import cursor_success_wire, cursor_wire

pytestmark = pytest.mark.integration


def cursor_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alteration: str = ""
) -> dict:
    """Build one disposable exact-call database and replace fixed external boundaries.

    Args:
        tmp_path: Disposable filesystem root.
        monkeypatch: Scoped fixed-path/source-pin replacements.
        alteration: Deliberate identity, availability or result violation.

    Returns:
        The committed invocation metadata used by the reader.
    """
    # Construct one exact newline-bearing call and an unrelated bubble with invalid content.
    parent = "93ea96d2-93ce-4359-b562-65c859fb042f"
    native_call = "call-original" + chr(10) + "fc-original"
    bubble = "dfcef075-9b32-4652-a3f4-f3af03de669f/t:" + native_call
    invocation = {
        "parent_conversation": parent,
        "native_call": native_call,
        "child_conversation": None,
    }
    headers = [{"bubbleId": "unrelated", "type": 2}, {"bubbleId": bubble, "type": 2}]
    tool = {
        "name": "task_v2",
        "toolCallId": native_call,
        "toolCallBinary": base64.b64encode(cursor_success_wire()).decode(),
    }
    composer = {"composerId": parent, "fullConversationHeadersOnly": headers}
    # Apply one hostile/unavailable source condition without modifying the reader interface.
    if alteration == "duplicate-header":
        headers.append(headers[-1].copy())
    elif alteration == "missing-header":
        headers.pop()
    elif alteration == "foreign-parent":
        composer["composerId"] = "foreign"
    elif alteration == "foreign-call":
        tool["toolCallId"] = "foreign"
    elif alteration == "foreign-tool":
        tool["name"] = "read_file"
    elif alteration == "missing-binary":
        del tool["toolCallBinary"]
    elif alteration == "corrupt-binary":
        tool["toolCallBinary"] = "%%%"
    elif alteration == "background":
        tool["toolCallBinary"] = base64.b64encode(cursor_success_wire(cursor_wire(3, 1))).decode()
    elif alteration == "foreign-child":
        invocation["child_conversation"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    elif alteration == "contradictory-root-call":
        tool["toolCallBinary"] = base64.b64encode(
            cursor_success_wire() + cursor_wire(57, b"foreign")
        ).decode()
    # Persist the fixture through SQLite, then close every writer before the read-only call.
    database = tmp_path / "state.vscdb"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
        connection.executemany(
            "INSERT INTO cursorDiskKV VALUES (?, ?)",
            [
                ("composerData:" + parent, json.dumps(composer)),
                ("bubbleId:" + parent + ":" + bubble, json.dumps({"toolFormerData": tool})),
                ("bubbleId:" + parent + ":unrelated", "NOT JSON: MUST NOT BE READ"),
            ],
        )
        connection.commit()
    monkeypatch.setattr(cursor_results, "DATABASE", database)
    monkeypatch.setattr(
        cursor_results,
        "pinned_sources",
        lambda _deadline: {"executor_sha256": "a" * 64, "workbench_sha256": "b" * 64},
    )
    return invocation


def test_cursor_reader_selects_only_exact_header_and_bubble(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read only the exact original call and project no content from unrelated rows.

    Args:
        tmp_path: Disposable database directory.
        monkeypatch: Scoped external-boundary replacements.
    """
    invocation = cursor_persistence(tmp_path, monkeypatch)
    queries = []
    with closing(
        sqlite3.connect(cursor_results.DATABASE.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        connection.set_trace_callback(queries.append)
        key, tool = cursor_results.exact_row(connection, invocation, time.monotonic() + 1)
    assert key.endswith("/t:" + invocation["native_call"])
    assert tool["name"] == "task_v2"
    assert len(queries) == 2
    assert "fullConversationHeadersOnly" in queries[0]
    assert "WHERE key=" in queries[1]
    assert ":unrelated'" not in queries[1]
    # Independently exercise the fixed-path read-only wrapper and its exact output vocabulary.
    result = cursor_results.read_terminal(invocation, time.monotonic() + 1)
    assert result == {
        "parent_conversation": invocation["parent_conversation"],
        "native_call": invocation["native_call"],
        "storage_key": key,
        "child_conversation": "c11e3a99-d371-410b-a910-378914bdb9a1",
        "kind": "foreground-success",
        "is_background": False,
        "background_reason": 0,
        "duration_ms": 12,
        "binary_sha256": hashlib.sha256(cursor_success_wire()).hexdigest(),
        "executor_sha256": "a" * 64,
        "workbench_sha256": "b" * 64,
    }


@pytest.mark.parametrize(
    "alteration,code",
    [
        ("duplicate-header", "RESULT_SCHEMA_MISMATCH"),
        ("missing-header", "RESULT_NOT_AVAILABLE"),
        ("foreign-parent", "RESULT_SCHEMA_MISMATCH"),
        ("foreign-call", "RESULT_SCHEMA_MISMATCH"),
        ("foreign-tool", "RESULT_SCHEMA_MISMATCH"),
        ("missing-binary", "RESULT_NOT_AVAILABLE"),
        ("corrupt-binary", "RESULT_SCHEMA_MISMATCH"),
        ("background", "RESULT_NOT_TERMINAL"),
        ("foreign-child", "RESULT_SCHEMA_MISMATCH"),
        ("contradictory-root-call", "RESULT_SCHEMA_MISMATCH"),
    ],
)
def test_cursor_reader_preserves_missing_or_invalid_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alteration: str, code: str
) -> None:
    """Require the exact pinned foreground result before producing retirement evidence.

    Args:
        tmp_path: Disposable database directory.
        monkeypatch: Scoped fixed-path/source replacements.
        alteration: Deliberate evidence violation.
        code: Expected bounded diagnostic.
    """
    invocation = cursor_persistence(tmp_path, monkeypatch, alteration)
    with pytest.raises(common.core.WorkspaceError) as error:
        cursor_results.read_terminal(invocation, time.monotonic() + 1)
    assert error.value.code == code


def test_cursor_pins_version_source_and_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject changed installation semantics and expired deadlines before database access.

    Args:
        tmp_path: Disposable fixed application source root.
        monkeypatch: Scoped platform and path replacements.
    """
    # Verify a small synthetic installation through the actual streaming hash implementation.
    app = tmp_path / "app"
    app.mkdir()
    (app / "package.json").write_text(json.dumps({"version": "3.25.9"}))
    (app / "executor.js").write_bytes(b"executor")
    (app / "workbench.js").write_bytes(b"workbench")
    pins = {
        "executor_sha256": ("executor.js", hashlib.sha256(b"executor").hexdigest()),
        "workbench_sha256": ("workbench.js", hashlib.sha256(b"workbench").hexdigest()),
    }
    monkeypatch.setattr(cursor_results, "APP", app)
    monkeypatch.setattr(cursor_results, "PINS", pins)
    monkeypatch.setattr(cursor_results.sys, "platform", "darwin")
    expected = {key: value[1] for key, value in pins.items()}
    assert cursor_results.pinned_sources(time.monotonic() + 1) == expected
    # Each changed semantic source fails closed; expiry is independent of source validity.
    for alteration, code in (
        ("version", "RESULT_SCHEMA_MISMATCH"),
        ("source", "RESULT_SCHEMA_MISMATCH"),
        ("platform", "HOST_UNSUPPORTED"),
        ("deadline", "BUSY"),
    ):
        (app / "package.json").write_text(
            json.dumps({"version": "changed" if alteration == "version" else "3.25.9"})
        )
        (app / "executor.js").write_bytes(b"changed" if alteration == "source" else b"executor")
        monkeypatch.setattr(
            cursor_results.sys, "platform", "linux" if alteration == "platform" else "darwin"
        )
        with pytest.raises(common.core.WorkspaceError) as error:
            cursor_results.pinned_sources(
                time.monotonic() + (-1 if alteration == "deadline" else 1)
            )
        assert error.value.code == code
