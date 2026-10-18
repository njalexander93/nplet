"""Verify bounded Cursor result decoding without native application access."""

import pytest

from nplet.adapters import common, cursor_results

pytestmark = pytest.mark.unit


def cursor_varint(number: int) -> bytes:
    """Encode a nonnegative test integer without a protobuf dependency.

    Args:
        number: Unsigned fixture integer.

    Returns:
        Canonical unsigned protobuf varint bytes.
    """
    # Emit continuation groups until the remaining integer fits in one byte.
    result = bytearray()
    while number >= 128:
        result.append((number & 127) | 128)
        number >>= 7
    return bytes(result) + bytes([number])


def cursor_wire(number: int, value: bytes | int) -> bytes:
    """Encode one fixture field with its actual expected wire type.

    Args:
        number: Positive field number.
        value: Opaque message bytes or scalar integer.

    Returns:
        Encoded field with canonical tag and optional length.
    """
    # Bytes use length-delimited encoding; integers use varints.
    if isinstance(value, bytes):
        return cursor_varint(number * 8 + 2) + cursor_varint(len(value)) + value
    return cursor_varint(number * 8) + cursor_varint(value)


def cursor_success_wire(extra: bytes = b"", outcome_extra: bytes = b"") -> bytes:
    """Construct one full original-call Task result with an opaque report.

    Args:
        extra: Deliberate success-field alteration or duplicate.
        outcome_extra: Deliberate result-oneof alteration.

    Returns:
        Full pinned Task ToolCall binary.
    """
    # The report is opaque; only the returned identity and scalar metadata matter.
    success = (
        cursor_wire(1, b"REPORT MUST NOT ESCAPE")
        + cursor_wire(2, b"c11e3a99-d371-410b-a910-378914bdb9a1")
        + cursor_wire(4, 12)
        + extra
    )
    return cursor_wire(
        19, cursor_wire(1, b"") + cursor_wire(2, cursor_wire(1, success) + outcome_extra)
    )


@pytest.mark.parametrize("extra", [b"", cursor_wire(3, 0) + cursor_wire(6, 0)])
def test_cursor_task_result_uses_pinned_false_defaults(extra: bytes) -> None:
    """Project only identity and foreground metadata with protobuf false defaults.

    Args:
        extra: Omitted defaults or explicit equivalent defaults.
    """
    assert cursor_results.task_success(cursor_success_wire(extra)) == {
        "child_conversation": "c11e3a99-d371-410b-a910-378914bdb9a1",
        "kind": "foreground-success",
        "is_background": False,
        "background_reason": 0,
        "duration_ms": 12,
    }


@pytest.mark.parametrize(
    "extra,outcome_extra,code",
    [
        (cursor_wire(2, b"other"), b"", "RESULT_SCHEMA_MISMATCH"),
        (cursor_wire(4, 12), b"", "RESULT_SCHEMA_MISMATCH"),
        (cursor_wire(3, 0) * 2, b"", "RESULT_SCHEMA_MISMATCH"),
        (cursor_wire(6, 0) * 2, b"", "RESULT_SCHEMA_MISMATCH"),
        (cursor_wire(3, b"wrong wire"), b"", "RESULT_SCHEMA_MISMATCH"),
        (cursor_wire(3, 2), b"", "RESULT_SCHEMA_MISMATCH"),
        (cursor_wire(3, 1), b"", "RESULT_NOT_TERMINAL"),
        (cursor_wire(6, 1), b"", "RESULT_NOT_TERMINAL"),
        (cursor_wire(8, 0), b"", "RESULT_SCHEMA_MISMATCH"),
        (b"", cursor_wire(2, b""), "RESULT_NOT_TERMINAL"),
        (b"", cursor_wire(1, b""), "RESULT_SCHEMA_MISMATCH"),
    ],
)
def test_cursor_task_result_rejects_ambiguous_or_background_fields(
    extra: bytes, outcome_extra: bytes, code: str
) -> None:
    """Keep malformed or nonforeground results from becoming retirement evidence.

    Args:
        extra: Deliberate success-field violation.
        outcome_extra: Deliberate oneof violation.
        code: Bounded expected failure.
    """
    with pytest.raises(common.core.WorkspaceError) as error:
        cursor_results.task_success(cursor_success_wire(extra, outcome_extra))
    assert error.value.code == code


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"", "RESULT_SCHEMA_MISMATCH"),
        (cursor_wire(19, cursor_wire(1, b"")), "RESULT_NOT_AVAILABLE"),
        (cursor_success_wire() * 2, "RESULT_SCHEMA_MISMATCH"),
        (cursor_success_wire() + cursor_wire(20, b""), "RESULT_SCHEMA_MISMATCH"),
        (b"\x9a\x01\x10x", "RESULT_SCHEMA_MISMATCH"),
        (b"\x80" * 11, "RESULT_SCHEMA_MISMATCH"),
        (b"\x00", "RESULT_SCHEMA_MISMATCH"),
        (cursor_wire(19, b"") + b"\xff", "RESULT_SCHEMA_MISMATCH"),
    ],
)
def test_cursor_task_result_rejects_invalid_envelopes(raw: bytes, code: str) -> None:
    """Reject missing results, duplicate Task oneofs and malformed protobuf bounds.

    Args:
        raw: Deliberately invalid binary envelope.
        code: Expected bounded failure.
    """
    with pytest.raises(common.core.WorkspaceError) as error:
        cursor_results.task_success(raw)
    assert error.value.code == code


@pytest.mark.parametrize(
    "raw,expected",
    [
        (cursor_wire(1, 0), {1: [(0, 0)]}),
        (cursor_wire(1, (1 << 64) - 1), {1: [(0, (1 << 64) - 1)]}),
        (b"\x09" + b"12345678", {1: [(1, b"12345678")]}),
        (b"\x0d" + b"1234", {1: [(5, b"1234")]}),
        (cursor_wire(1, b"opaque"), {1: [(2, b"opaque")]}),
        (cursor_wire(1, 0) * 2, {1: [(0, 0), (0, 0)]}),
    ],
)
def test_cursor_fields_preserve_typed_values_and_occurrences(raw: bytes, expected: dict) -> None:
    """Retain exact supported wire types without dropping duplicate fields.

    Args:
        raw: Canonical fixture protobuf bytes.
        expected: Full expected typed projection.
    """
    assert cursor_results.fields(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        b"\x08\x80\x00",
        b"\x88\x00\x00",
        b"\x08" + b"\xff" * 9 + b"\x02",
        b"\x08\x80",
        b"\x09short",
        b"\x0d123",
        b"\x0b",
        b"\x0a\x04x",
    ],
)
def test_cursor_fields_reject_malformed_wire_data(raw: bytes) -> None:
    """Reject overlong, overflowed, unsupported and truncated encodings.

    Args:
        raw: Malformed wire fixture.
    """
    with pytest.raises(common.core.WorkspaceError) as error:
        cursor_results.fields(raw)
    assert error.value.code == "RESULT_SCHEMA_MISMATCH"


def test_cursor_fields_enforce_byte_and_occurrence_limits() -> None:
    """Bound both source allocation and many-small-fields parser work."""
    # Exact occurrence boundary remains valid; the next occurrence is rejected.
    assert len(cursor_results.fields(cursor_wire(1, 0) * 65536)[1]) == 65536
    for raw in (b"x" * (cursor_results.BINARY_LIMIT + 1), cursor_wire(1, 0) * 65537):
        with pytest.raises(common.core.WorkspaceError) as error:
            cursor_results.fields(raw)
        assert error.value.code == "SIZE_LIMIT"
    # The exact byte limit can contain one opaque length-delimited payload.
    prefix = cursor_varint(10) + cursor_varint(cursor_results.BINARY_LIMIT - 5)
    assert len(prefix) == 5
    raw = prefix + b"x" * (cursor_results.BINARY_LIMIT - 5)
    assert cursor_results.fields(raw)[1] == [(2, raw[5:])]


@pytest.mark.parametrize(
    "message,default,expected",
    [({}, 0, 0), ({}, b"", b""), ({1: [(0, 7)]}, None, 7)],
)
def test_cursor_singleton_applies_only_explicit_defaults(
    message: dict, default: int | bytes | None, expected: int | bytes
) -> None:
    """Keep valid singleton values and explicit protobuf defaults.

    Args:
        message: Decoded fixture message.
        default: Permitted default or required-presence marker.
        expected: Typed expected value.
    """
    assert cursor_results.singleton(message, 1, 0, default) == expected


@pytest.mark.parametrize(
    "message,code",
    [
        ({}, "RESULT_NOT_AVAILABLE"),
        ({1: [(0, 0), (0, 0)]}, "RESULT_SCHEMA_MISMATCH"),
        ({1: [(0, 0), (0, 1)]}, "RESULT_SCHEMA_MISMATCH"),
        ({1: [(2, b"x")]}, "RESULT_SCHEMA_MISMATCH"),
    ],
)
def test_cursor_singleton_rejects_missing_duplicate_or_wrong_wire(message: dict, code: str) -> None:
    """Reject scalar ambiguity before any retirement metadata is projected.

    Args:
        message: Invalid decoded fixture.
        code: Expected bounded diagnostic.
    """
    with pytest.raises(common.core.WorkspaceError) as error:
        cursor_results.singleton(message, 1, 0)
    assert error.value.code == code


@pytest.mark.parametrize(
    "child",
    [b"not-a-uuid", b"\xff", b"\xff" * 36, b"z" * 36, b"C11E3A99-D371-410B-A910-378914BDB9A1"],
)
def test_cursor_task_success_normalizes_invalid_child_errors(child: bytes) -> None:
    """Keep invalid UUID and ASCII decoder failures inside the bounded result contract.

    Args:
        child: Invalid returned child identity.
    """
    success = cursor_wire(2, child)
    raw = cursor_wire(19, cursor_wire(1, b"") + cursor_wire(2, cursor_wire(1, success)))
    with pytest.raises(common.core.WorkspaceError) as error:
        cursor_results.task_success(raw)
    assert error.value.code == "RESULT_SCHEMA_MISMATCH"


def test_cursor_task_result_accepts_pinned_envelope_metadata() -> None:
    """Accept real ToolCall metadata while corroborating the exact newline-bearing call ID."""
    # Hook contexts are repeated opaque messages; timestamps are optional uint64 scalars.
    native_call = "call-original" + chr(10) + "fc-original"
    metadata = (
        cursor_wire(54, b"opaque hook context") * 2
        + cursor_wire(57, native_call.encode())
        + cursor_wire(59, 1792281663722)
        + cursor_wire(60, 1792281722014)
    )
    assert (
        cursor_results.task_success(cursor_success_wire() + metadata, native_call)["duration_ms"]
        == 12
    )


@pytest.mark.parametrize(
    "metadata,native_call",
    [
        (cursor_wire(54, 0), "call"),
        (cursor_wire(57, 0), "call"),
        (cursor_wire(57, b"call") * 2, "call"),
        (cursor_wire(57, b"different"), "call"),
        (cursor_wire(57, b"call"), None),
        (cursor_wire(59, b"wrong"), "call"),
        (cursor_wire(59, 0) * 2, "call"),
        (cursor_wire(60, b"wrong"), "call"),
        (cursor_wire(60, 0) * 2, "call"),
        (cursor_wire(18, b"other tool"), "call"),
        (cursor_wire(90, b"unknown"), "call"),
    ],
)
def test_cursor_task_result_rejects_envelope_ambiguity(
    metadata: bytes, native_call: str | None
) -> None:
    """Reject conflicting call identity, unknown fields and malformed pinned metadata.

    Args:
        metadata: Invalid or contradictory envelope field encoding.
        native_call: Exact call expected from the selected persisted row.
    """
    with pytest.raises(common.core.WorkspaceError) as error:
        cursor_results.task_success(cursor_success_wire() + metadata, native_call)
    assert error.value.code == "RESULT_SCHEMA_MISMATCH"


def test_cursor_empty_task_result_remains_retryable() -> None:
    """Treat a not-yet-persisted result oneof as unavailable, retaining the invocation."""
    raw = cursor_wire(19, cursor_wire(1, b"") + cursor_wire(2, b""))
    with pytest.raises(common.core.WorkspaceError) as error:
        cursor_results.task_success(raw)
    assert error.value.code == "RESULT_NOT_AVAILABLE"
