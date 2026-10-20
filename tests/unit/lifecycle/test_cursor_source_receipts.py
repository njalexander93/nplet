"""Verify Cursor source receipt transitions without filesystem or native-host claims."""

from __future__ import annotations

import copy

import pytest

from nplet.lifecycle import _invocations as invocations
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def source_case(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict, dict]:
    """Provide one independently identified source Read and available assigned bytes.

    Args:
        monkeypatch: Replace only source availability at the filesystem boundary.

    Returns:
        Participant, content callback and pending source metadata.
    """
    # Keep identity and content expectations explicit rather than borrowing parent state.
    ref = {"locator": "context/source.md", "sha256": "a" * 64, "available": True}
    packet = [{key: value for key, value in ref.items() if key != "available"}]
    source = {
        "invocation_id": "invocation",
        "generation": 2,
        "packet_digest": core.sha(core.canonical(packet)),
        "locator": ref["locator"],
        "sha256": ref["sha256"],
        "file_path": "/checkout/.task/TEST-1/context/source.md",
        "provisional": None,
    }
    member = {
        "cursor_invocation": "invocation",
        "generation": 2,
        "packet": packet,
        "pending": {"read-call": {"status": "pending", "cursor_source": source}},
    }
    request = {
        "host": "cursor",
        "source_event": "content",
        "tool_id": "read-call",
        "source_path": source["file_path"],
        "observed_sha256": ref["sha256"],
        "content_length": 7,
    }
    monkeypatch.setattr(core, "packet_reads", lambda *_args: [ref])
    return member, request, source


def test_content_is_provisional_and_exact_replay_does_not_add_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content alone preserves the pending Read and has no readiness credit.

    Args:
        monkeypatch: Source availability boundary.
    """
    member, request, source = source_case(monkeypatch)
    result = invocations.source_callback({}, {}, request, member)
    assert result == {"source_content_verified": True, "source_receipt": False}
    assert source["provisional"] == {"sha256": "a" * 64, "content_length": 7}
    before = copy.deepcopy(member)
    # Equal evidence is harmless; no source text or readiness entry is created.
    assert invocations.source_callback({}, {}, request, member) == result
    assert member == before
    assert "cursor_sources" not in member


@pytest.mark.parametrize(
    "change,code",
    [
        ("host", "NOT_OWNER"),
        ("unclassified", "NOT_OWNER"),
        ("phase", "INVALID_REQUEST"),
        ("prose", "INVALID_REQUEST"),
        ("unknown-call", "UNKNOWN_OPERATION"),
        ("generic-call", "UNKNOWN_OPERATION"),
        ("invocation", "REQUEST_CONFLICT"),
        ("generation", "REQUEST_CONFLICT"),
        ("path", "REQUEST_CONFLICT"),
        ("packet", "SOURCE_STALE"),
        ("hash-type", "INVALID_REQUEST"),
        ("length-bool", "INVALID_REQUEST"),
        ("wrong-hash", "SOURCE_DELIVERY_REQUIRED"),
        ("conflicting-proof", "REQUEST_CONFLICT"),
    ],
)
def test_invalid_source_evidence_preserves_pending(
    monkeypatch: pytest.MonkeyPatch, change: str, code: str
) -> None:
    """Reject foreign or malformed proof without settling the selected effect.

    Args:
        monkeypatch: Source availability boundary.
        change: One invalid identity, shape or content field.
        code: Exact contractual diagnostic.
    """
    member, request, source = source_case(monkeypatch)
    # Change a single independently enforced boundary.
    if change == "host":
        request["host"] = "codex"
    elif change == "unclassified":
        del member["cursor_invocation"]
    elif change == "phase":
        request["source_event"] = "completed"
    elif change == "prose":
        request["content"] = "must not persist"
    elif change == "unknown-call":
        request["tool_id"] = "another-call"
    elif change == "generic-call":
        member["pending"]["read-call"] = {"status": "pending"}
    elif change == "invocation":
        source["invocation_id"] = "old-invocation"
    elif change == "generation":
        source["generation"] = 1
    elif change == "path":
        request["source_path"] += ".foreign"
    elif change == "packet":
        source["packet_digest"] = "0" * 64
    elif change == "hash-type":
        request["observed_sha256"] = 123
    elif change == "length-bool":
        request["content_length"] = True
    elif change == "wrong-hash":
        request["observed_sha256"] = "b" * 64
    elif change == "conflicting-proof":
        source["provisional"] = {"sha256": "a" * 64, "content_length": 9}
    before = copy.deepcopy(member)
    with pytest.raises(core.WorkspaceError, match=code):
        invocations.source_callback({}, {}, request, member)
    assert member == before


@pytest.mark.parametrize(
    "phase,alteration,receipt",
    [
        ("success", "", True),
        ("success", "no-proof", False),
        ("failure", "", False),
        ("success", "stale", False),
        ("success", "length", False),
        ("success", "unavailable", False),
        ("success", "io-error", False),
        ("success", "source-error", False),
    ],
)
def test_known_finished_read_settles_only_itself(
    monkeypatch: pytest.MonkeyPatch, phase: str, alteration: str, receipt: bool
) -> None:
    """Finalize exact verified content or clear known finished work without credit.

    Args:
        monkeypatch: Source availability boundary.
        phase: Actual success or failure callback.
        alteration: Missing or stale proof at completion.
        receipt: Whether current source evidence can credit readiness.
    """
    member, request, source = source_case(monkeypatch)
    invocations.source_callback({}, {}, request, member)
    member["pending"]["unrelated"] = {"status": "pending"}
    member["cursor_sources"] = {"old-source": {"packet_digest": "old"}}
    # Model independent completion evidence and changed source availability.
    request.pop("observed_sha256")
    request["source_event"] = phase
    if phase == "failure":
        request.pop("content_length")
    if alteration == "no-proof":
        source["provisional"] = None
    elif alteration == "stale":
        source["packet_digest"] = "old"
    elif alteration == "length":
        request["content_length"] = 8
    elif alteration == "unavailable":
        monkeypatch.setattr(core, "packet_reads", lambda *_args: [])
    elif alteration in {"io-error", "source-error"}:

        def unavailable(*_args: object) -> list:
            """Report unavailable source bytes without preserving finished work.

            Args:
                *_args: Ignored filesystem lookup arguments.

            Returns:
                No return because the source read fails.

            Raises:
                OSError: Source is inaccessible.
                core.WorkspaceError: Source digest is stale.
            """
            if alteration == "io-error":
                raise OSError("unavailable")
            raise core.WorkspaceError("SOURCE_STALE")

        monkeypatch.setattr(core, "packet_reads", unavailable)
    result = invocations.source_callback({}, {}, request, member)
    assert result["source_receipt"] is receipt
    assert member["pending"] == {"unrelated": {"status": "pending"}}
    if receipt:
        assert member["cursor_sources"] == {
            source["locator"]: {
                "packet_digest": source["packet_digest"],
                "sha256": source["sha256"],
                "invocation_id": "invocation",
            }
        }
    else:
        assert member["cursor_sources"] == {"old-source": {"packet_digest": "old"}}
