"""Read only pinned Cursor original-call Task return metadata, never report content."""

from __future__ import annotations

import base64
import binascii
import hashlib
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

from nplet.lifecycle import _invocation_records as records
from nplet.lifecycle import task_workspace as core

BINARY_LIMIT = 8 * 1024 * 1024


def fields(data: bytes) -> dict[int, list[tuple[int, int | bytes]]]:
    """Decode bounded protobuf fields while preserving wire types and duplicates.

    Args:
        data: One bounded schema-pinned protobuf message.

    Returns:
        Field numbers mapped to ordered wire/value occurrences.

    Raises:
        core.WorkspaceError: Truncation, unsupported wire types or invalid integers.
    """
    # Bound each message before allocating decoded field metadata.
    core.require(isinstance(data, bytes) and len(data) <= BINARY_LIMIT, "SIZE_LIMIT")
    decoded: dict[int, list[tuple[int, int | bytes]]] = {}
    cursor = 0
    occurrences = 0

    def integer() -> int:
        """Consume one canonical unsigned64-bit protobuf integer.

        Returns:
            The decoded unsigned integer.

        Raises:
            core.WorkspaceError: Integer is truncated, overlong or noncanonical.
        """
        nonlocal cursor
        value = 0
        index = 0
        # Ten bytes suffice for64 bits; a tenth byte can contain only bit zero.
        while True:
            core.require(cursor < len(data), "RESULT_SCHEMA_MISMATCH")
            byte = data[cursor]
            cursor += 1
            core.require(index < 9 or byte <= 1, "RESULT_SCHEMA_MISMATCH")
            value |= (byte & 127) << (7 * index)
            # Reject redundant trailing zero groups to keep one encoding per integer.
            if byte < 128:
                core.require(index == 0 or byte != 0, "RESULT_SCHEMA_MISMATCH")
                return value
            index += 1

    # Skip opaque length-delimited payloads without decoding report or argument text.
    while cursor < len(data):
        tag = integer()
        number, wire = tag >> 3, tag & 7
        core.require(0 < number < (1 << 29), "RESULT_SCHEMA_MISMATCH")
        # Varints are scalar metadata; other supported wires remain opaque bytes.
        if wire == 0:
            value: int | bytes = integer()
        else:
            core.require(wire in {1, 2, 5}, "RESULT_SCHEMA_MISMATCH")
            length = integer() if wire == 2 else (8 if wire == 1 else 4)
            core.require(length <= len(data) - cursor, "RESULT_SCHEMA_MISMATCH")
            value = data[cursor : cursor + length]
            cursor += length
        decoded.setdefault(number, []).append((wire, value))
        occurrences += 1
        core.require(occurrences <= 65536, "SIZE_LIMIT")
    return decoded


def singleton(
    message: dict[int, list[tuple[int, int | bytes]]],
    number: int,
    wire: int,
    default: int | bytes | None = None,
) -> int | bytes:
    """Read one typed field and reject duplicate/conflicting singleton encoding.

    Args:
        message: Decoded message retaining every occurrence.
        number: Expected field number.
        wire: Required protobuf wire type.
        default: Pinned schema default; None means presence is required.

    Returns:
        The unique typed value or its permitted default.

    Raises:
        core.WorkspaceError: Missing field, duplicate field or wrong wire type.
    """
    # Apply only explicitly supplied protobuf defaults.
    values = message.get(number, [])
    if not values:
        core.require(default is not None, "RESULT_NOT_AVAILABLE")
        assert default is not None
        return default
    # No scalar/discriminator may occur twice, even with equal values.
    core.require(len(values) == 1 and values[0][0] == wire, "RESULT_SCHEMA_MISMATCH")
    return values[0][1]


def task_success(raw: bytes, native_call: str | None = None) -> core.JSONObject:
    """Project pinned ToolCall19/TaskToolCall2/TaskResult1 foreground metadata.

    Args:
        raw: Full original Task ToolCall binary from its exact persisted row.
        native_call: Expected exact original call, required when root57 is present.

    Returns:
        Returned child identity and foreground scalar metadata only.

    Raises:
        core.WorkspaceError: Missing, nonterminal, ambiguous or malformed result.
    """
    # Require the Task oneof and its unique nested result; never inspect step content.
    root = fields(raw)
    core.require(19 in root and set(root) <= {19, 54, 57, 59, 60}, "RESULT_SCHEMA_MISMATCH")
    # The pinned envelope has repeated opaque hook contexts and optional scalar metadata.
    core.require(all(wire == 2 for wire, _ in root.get(54, [])), "RESULT_SCHEMA_MISMATCH")
    for number in (59, 60):
        singleton(root, number, 0, 0)
    # A supplied envelope call ID must corroborate the selected original-call row.
    if 57 in root:
        envelope_call = singleton(root, 57, 2)
        core.require(
            isinstance(native_call, str) and envelope_call == native_call.encode("utf-8"),
            "RESULT_SCHEMA_MISMATCH",
        )
    task_bytes = singleton(root, 19, 2)
    core.require(isinstance(task_bytes, bytes), "RESULT_SCHEMA_MISMATCH")
    assert isinstance(task_bytes, bytes)
    task = fields(task_bytes)
    core.require(set(task) <= {1, 2}, "RESULT_SCHEMA_MISMATCH")
    singleton(task, 1, 2)
    result_bytes = singleton(task, 2, 2)
    assert isinstance(result_bytes, bytes)
    outcome = fields(result_bytes)
    core.require(outcome, "RESULT_NOT_AVAILABLE")
    # Error/interruption is intentionally unresolved; only this pinned success is terminal.
    core.require(set(outcome) == {1}, "RESULT_NOT_TERMINAL")
    success_bytes = singleton(outcome, 1, 2)
    assert isinstance(success_bytes, bytes)
    success = fields(success_bytes)
    core.require(set(success) <= {1, 2, 3, 4, 5, 6, 7}, "RESULT_SCHEMA_MISMATCH")
    # Repeated conversation_steps is valid but opaque; every other field is singleton.
    core.require(all(wire == 2 for wire, _ in success.get(1, [])), "RESULT_SCHEMA_MISMATCH")
    for number in (5, 7):
        singleton(success, number, 2, b"")
    child_bytes = singleton(success, 2, 2)
    assert isinstance(child_bytes, bytes)
    # Canonical UUID text excludes malformed, non-ASCII and oversized native identities.
    core.require(len(child_bytes) == 36, "RESULT_SCHEMA_MISMATCH")
    try:
        child = child_bytes.decode("ascii")
        core.require(str(uuid.UUID(child)) == child, "RESULT_SCHEMA_MISMATCH")
    except (ValueError, UnicodeError) as error:
        raise core.WorkspaceError("RESULT_SCHEMA_MISMATCH") from error
    background = singleton(success, 3, 0, 0)
    reason = singleton(success, 6, 0, 0)
    duration = singleton(success, 4, 0, 0)
    # Explicit false and omitted false share the pinned protobuf semantic default.
    core.require(background in {0, 1}, "RESULT_SCHEMA_MISMATCH")
    core.require(background == 0 and reason == 0, "RESULT_NOT_TERMINAL")
    return {
        "child_conversation": child,
        "kind": "foreground-success",
        "is_background": False,
        "background_reason": reason,
        "duration_ms": duration,
    }


APP = Path("/Applications/Cursor.app/Contents/Resources/app")
DATABASE = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
PINS = {
    "executor_sha256": (
        "extensions/cursor-agent-exec/dist/main.js",
        "6d13818167a3619794433ca1e1a9b822a7d23aceab98ef6acab110fa3d50991b",
    ),
    "workbench_sha256": (
        "out/vs/workbench/workbench.desktop.main.js",
        "c999d13ea376bb074a6f269abeb4297d302e2ac76aa8b9cdc4af1e5bb199398b",
    ),
}
ROW_LIMIT = 16 * 1024 * 1024
HEADER_LIMIT = 4096


def check_deadline(deadline: float) -> None:
    """Refuse further work after the caller's shared monotonic hook deadline.

    Args:
        deadline: Absolute monotonic deadline, selected by adapter code.

    Raises:
        core.WorkspaceError: The shared execution budget expired.
    """
    core.require(time.monotonic() < deadline, "BUSY")


def pinned_sources(deadline: float) -> core.JSONObject:
    """Verify the fixed macOS Cursor installation and its exact schema sources.

    Args:
        deadline: Shared adapter deadline; no independent polling or background worker.

    Returns:
        Verified executor and workbench digests only.

    Raises:
        core.WorkspaceError: Platform, version, byte budget or source hashes differ.
        OSError: A pinned source cannot be read.
    """
    # This internal persistence route has physical evidence only for this installation.
    core.require(sys.platform == "darwin", "HOST_UNSUPPORTED")
    check_deadline(deadline)
    with (APP / "package.json").open("rb") as source:
        package = source.read(256 * 1024 + 1)
    core.require(len(package) <= 256 * 1024, "SIZE_LIMIT")
    manifest = core.strict_json(package)
    core.require(
        isinstance(manifest, dict) and manifest.get("version") == "3.25.9", "RESULT_SCHEMA_MISMATCH"
    )
    # Hash fixed source files in bounded chunks; no caller chooses paths or accepted hashes.
    observed: core.JSONObject = {}
    for name, (relative, expected) in PINS.items():
        digest = hashlib.sha256()
        total = 0
        with (APP / relative).open("rb") as source:
            # Enforce byte and time budgets while reading the installed source.
            while chunk := source.read(128 * 1024):
                check_deadline(deadline)
                total += len(chunk)
                core.require(total <= 128 * 1024 * 1024, "SIZE_LIMIT")
                digest.update(chunk)
        core.require(digest.hexdigest() == expected, "RESULT_SCHEMA_MISMATCH")
        observed[name] = expected
    return observed


def exact_row(
    connection: sqlite3.Connection, invocation: core.JSONObject, deadline: float
) -> tuple[str, core.JSONObject]:
    """Read one original-call bubble selected only from its recorded parent's header.

    Args:
        connection: Read-only connection to the fixed Cursor database.
        invocation: Committed invocation containing exact parent and raw native call.
        deadline: Shared execution deadline.

    Returns:
        The unique exact storage key and its Task tool metadata.

    Raises:
        core.WorkspaceError: Parent, header, row or tool identity is missing or ambiguous.
        sqlite3.Error: SQLite cannot provide the selected bounded values.
    """
    # Query only the recorded parent's header; do not fetch unrelated conversation values.
    check_deadline(deadline)
    parent = core.token(invocation["parent_conversation"])
    native_call = records.native_text(invocation["native_call"])
    row = connection.execute(
        "SELECT json_extract(value, '$.composerId'), "
        "json_extract(value, '$.fullConversationHeadersOnly') "
        "FROM cursorDiskKV WHERE key=? AND length(value)<=?",
        ("composerData:" + parent, ROW_LIMIT),
    ).fetchone()
    core.require(row is not None, "RESULT_NOT_AVAILABLE")
    assert row is not None
    core.require(row[0] == parent and isinstance(row[1], str), "RESULT_SCHEMA_MISMATCH")
    headers = core.strict_json(row[1])
    core.require(isinstance(headers, list) and len(headers) <= HEADER_LIMIT, "SIZE_LIMIT")
    # Select the exact newline-preserving call suffix without reading any other bubble.
    matches = []
    for header in headers:
        check_deadline(deadline)
        core.require(isinstance(header, dict), "RESULT_SCHEMA_MISMATCH")
        bubble = header.get("bubbleId")
        core.require(
            isinstance(bubble, str) and len(bubble.encode()) <= 4096, "RESULT_SCHEMA_MISMATCH"
        )
        if bubble.endswith("/t:" + native_call):
            core.require(header.get("type") == 2, "RESULT_SCHEMA_MISMATCH")
            matches.append(bubble)
    core.require(matches, "RESULT_NOT_AVAILABLE")
    core.require(len(matches) == 1, "RESULT_SCHEMA_MISMATCH")
    key = records.native_text("bubbleId:" + parent + ":" + matches[0])
    row = connection.execute(
        "SELECT value FROM cursorDiskKV WHERE key=? AND length(value)<=?",
        (key, ROW_LIMIT),
    ).fetchone()
    core.require(row is not None, "RESULT_NOT_AVAILABLE")
    assert row is not None
    bubble_value = core.strict_json(row[0])
    core.require(isinstance(bubble_value, dict), "RESULT_SCHEMA_MISMATCH")
    tool = bubble_value.get("toolFormerData")
    core.require(
        isinstance(tool, dict)
        and tool.get("name") == "task_v2"
        and tool.get("toolCallId") == native_call,
        "RESULT_SCHEMA_MISMATCH",
    )
    return key, tool


def read_terminal(invocation: core.JSONObject, deadline: float) -> records.TerminalEvidence:
    """Independently project one pinned original-call foreground return.

    Args:
        invocation: Committed metadata selected by the actual parent hook or adapter CLI.
        deadline: Shared adapter deadline, never a result-polling loop.

    Returns:
        Exact metadata accepted by internal cursor-retire; no binary/report is persisted.

    Raises:
        core.WorkspaceError: Pin, schema, identity, terminal evidence or deadline is invalid.
        OSError: The fixed application/database source is inaccessible.
    """
    # Verify installed semantics before consulting internal persistence.
    pin = pinned_sources(deadline)
    # Open only the fixed database read-only and cap SQLite allocations and execution.
    try:
        with closing(
            sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True, timeout=0.05)
        ) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, ROW_LIMIT)
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            key, tool = exact_row(connection, invocation, deadline)
    except sqlite3.DataError as error:
        raise core.WorkspaceError("SIZE_LIMIT") from error
    except sqlite3.OperationalError as error:
        # Missing/locked persistence remains charged and retryable; never infer completion.
        check_deadline(deadline)
        raise core.WorkspaceError("RESULT_NOT_AVAILABLE") from error
    # Decode a bounded full ToolCall; abbreviated UI status/result is not terminal evidence.
    encoded = tool.get("toolCallBinary")
    core.require(encoded is not None, "RESULT_NOT_AVAILABLE")
    core.require(isinstance(encoded, str), "RESULT_SCHEMA_MISMATCH")
    assert isinstance(encoded, str)
    core.require(len(encoded) <= 4 * ((BINARY_LIMIT + 2) // 3), "SIZE_LIMIT")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise core.WorkspaceError("RESULT_SCHEMA_MISMATCH") from error
    core.require(base64.b64encode(raw).decode("ascii") == encoded, "RESULT_SCHEMA_MISMATCH")
    check_deadline(deadline)
    terminal = task_success(raw, invocation["native_call"])
    check_deadline(deadline)
    core.require(
        invocation.get("child_conversation") in {None, terminal["child_conversation"]},
        "RESULT_SCHEMA_MISMATCH",
    )
    # Revalidation under the issue lock belongs to core cursor-retire after this read.
    return records.terminal_evidence(
        {
            **terminal,
            **pin,
            "parent_conversation": invocation["parent_conversation"],
            "native_call": invocation["native_call"],
            "storage_key": key,
            "binary_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
