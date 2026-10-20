"""Verify Cursor child dispatch uses only child scope and closed source callbacks."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "route", ["content", "task-pre", "reconcile-pre", "task-post", "task-failure", "reconcile-post"]
)
def test_parent_task_routes_keep_settlement_independent(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """Task callbacks give exact reconciliation advice without ordinary effect settlement.

    Args:
        monkeypatch: Replaces classification and final effect boundaries.
        route: Parent content, admission or completion protocol boundary.
    """
    # Classify one actual parent and retain its selected issue/call in all downstream requests.
    name = (
        "beforeReadFile"
        if route == "content"
        else "preToolUse"
        if route.endswith("pre")
        else "postToolUseFailure"
        if route == "task-failure"
        else "postToolUse"
    )
    event = {
        "hook_event_name": name,
        "tool_name": "Task" if route.startswith("task") else "Shell",
        "tool_use_id": "native-token",
        "tool_output": "{}",
        "error_message": "failed",
        "failure_type": "error",
    }
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(cursor.cursor_children, "normalize", lambda *_args: event)
    monkeypatch.setattr(cursor.common, "request_for", lambda *_args: {"issue_id": "TEST-1"})
    admit, authorize = MagicMock(), MagicMock()
    monkeypatch.setattr(cursor.cursor_children, "admit", admit)
    monkeypatch.setattr(cursor.cursor_children, "authorize_reconcile", authorize)
    monkeypatch.setattr(
        cursor.cursor_children,
        "recognize_reconcile",
        lambda _event: ("TEST-1", "native-token") if route.startswith("reconcile") else None,
    )
    ordinary_pre, ordinary_post = MagicMock(), MagicMock()
    monkeypatch.setattr(cursor.common, "native_pre", ordinary_pre)
    monkeypatch.setattr(cursor.common, "native_post", ordinary_post)
    result = cursor.handle(event)
    # Only prehooks admit; posthooks neither settle the Task nor create generic pending work.
    if route in {"task-pre", "reconcile-pre"}:
        assert result == {"permission": "allow"}
    elif route.startswith("task"):
        command = cursor.cursor_children.reconcile_command("TEST-1", "native-token")
        assert result == {
            "additional_context": "Task remains charged until result reconciliation. Run: "
            + command
        }
    else:
        assert result == {}
    assert admit.call_count == int(route == "task-pre")
    assert authorize.call_count == int(route == "reconcile-pre")
    if route == "reconcile-pre":
        assert authorize.call_args.args[:3] == (event, "TEST-1", "native-token")
    ordinary_pre.assert_not_called()
    ordinary_post.assert_not_called()


@pytest.mark.parametrize(
    "lifecycle,new_child", [(True, False), (True, True), (False, False), (False, True)]
)
def test_child_pre_routes_own_lifecycle_or_assigned_read(
    monkeypatch: pytest.MonkeyPatch, lifecycle: bool, new_child: bool
) -> None:
    """Route only the selected child effect and give own context on first binding.

    Args:
        monkeypatch: Replaces child boundary actions.
        lifecycle: Whether this is an own lifecycle command.
        new_child: Whether actual child binding just occurred.
    """
    event = {"child": True, "_cursor_new_child": new_child}
    monkeypatch.setattr(cursor.cursor_children, "child_lifecycle", lambda _event: lifecycle)
    source = MagicMock()
    monkeypatch.setattr(cursor.cursor_children, "source_read_pre", source)
    context = MagicMock(return_value="own-child-command")
    monkeypatch.setattr(cursor.cursor_children, "child_context", context)
    deadline = time.monotonic() + 1
    result = cursor.child_event(event, "preToolUse", deadline)
    assert result == (
        {"permission": "allow", "additional_context": "own-child-command"}
        if new_child
        else {"permission": "allow"}
    )
    if lifecycle:
        source.assert_not_called()
    else:
        source.assert_called_once_with(event, deadline)
    assert context.call_count == int(new_child)


@pytest.mark.parametrize("name", ["postToolUse", "postToolUseFailure"])
@pytest.mark.parametrize("lifecycle,receipt", [(True, False), (False, False), (False, True)])
def test_child_post_requires_closed_receipt_or_returns_recovery(
    monkeypatch: pytest.MonkeyPatch, name: str, lifecycle: bool, receipt: bool
) -> None:
    """Own commands manage themselves; missing receipts cannot imply readiness.

    Args:
        monkeypatch: Replaces the closed effect boundary.
        name: Successful or failed native completion event.
        lifecycle: Whether an own command manages itself.
        receipt: Whether a current source receipt was proven.
    """
    event = {"child": True}
    monkeypatch.setattr(cursor.cursor_children, "child_lifecycle", lambda _event: lifecycle)
    post = MagicMock(return_value={"source_receipt": receipt})
    monkeypatch.setattr(cursor.cursor_children, "source_read_post", post)
    monkeypatch.setattr(cursor.cursor_children, "child_context", lambda _event: "own-child-command")
    deadline = time.monotonic() + 1
    result = cursor.child_event(event, name, deadline)
    if lifecycle:
        assert result == {}
        post.assert_not_called()
    else:
        post.assert_called_once_with(event, deadline, failed=name == "postToolUseFailure")
        assert result == (
            {}
            if receipt
            else {
                "additional_context": (
                    "SOURCE_DELIVERY_REQUIRED: Read finished without a current "
                    "source receipt. own-child-command"
                )
            }
        )


@pytest.mark.parametrize(
    "name",
    [
        "beforeReadFile",
        "beforeMCPExecution",
        "afterMCPExecution",
        "beforeSubmitPrompt",
        "sessionStart",
        "preCompact",
        "stop",
        "sessionEnd",
        "unknown",
    ],
)
def test_classified_child_dispatch_never_enters_parent_startup(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Route classified child events without parent startup authority.

    Args:
        monkeypatch: Replaces classification and boundary effects.
        name: Native event dispatched after child classification.
    """
    event = {"hook_event_name": name}
    observed = {**event, "child": True}
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(cursor.cursor_children, "normalize", lambda *_args: observed)
    monkeypatch.setattr(cursor.cursor_children, "child_context", lambda _event: "own-child-command")
    content = MagicMock()
    observe = MagicMock()
    monkeypatch.setattr(cursor.cursor_children, "source_read_content", content)
    monkeypatch.setattr(cursor.common, "native_observe", observe)
    parent_boundaries = []
    for boundary in ("lookup_required", "native_pre", "native_post", "native_context", "prompt"):
        action = MagicMock(side_effect=AssertionError("parent startup used by child"))
        parent_boundaries.append(action)
        monkeypatch.setattr(cursor.common, boundary, action)
    result = cursor.handle(event)
    expected = {
        "beforeReadFile": {"permission": "allow"},
        "beforeMCPExecution": cursor.failure(name, "HOST_UNSUPPORTED_PROVIDER"),
        "afterMCPExecution": cursor.failure(name, "HOST_UNSUPPORTED_PROVIDER"),
        "beforeSubmitPrompt": {"continue": True},
        "sessionStart": {"additional_context": "own-child-command"},
        "preCompact": {"user_message": "own-child-command"},
        "stop": {},
        "sessionEnd": {},
        "unknown": {},
    }[name]
    assert result == expected
    for action in parent_boundaries:
        action.assert_not_called()
    if name == "beforeReadFile":
        assert content.call_args.args[0] is observed
    else:
        content.assert_not_called()
    if name in {"stop", "sessionEnd"}:
        observe.assert_called_once_with(observed, "cursor")
    else:
        observe.assert_not_called()


@pytest.mark.parametrize("context_available", [True, False])
def test_child_denial_delivers_only_available_own_recovery(
    monkeypatch: pytest.MonkeyPatch, context_available: bool
) -> None:
    """Child denial cannot fall through to parent context if own recovery fails.

    Args:
        monkeypatch: Replaces classification, denial and recovery boundaries.
        context_available: Whether current own binding context remains readable.
    """
    event = {"hook_event_name": "preToolUse"}
    observed = {**event, "child": True}
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(cursor.cursor_children, "normalize", lambda *_args: observed)
    monkeypatch.setattr(
        cursor.cursor_children,
        "child_lifecycle",
        MagicMock(side_effect=core.WorkspaceError("NOT_OWNER")),
    )
    context = MagicMock(return_value="own-child-command")
    if not context_available:
        context.side_effect = core.WorkspaceError("STALE_BINDING")
    monkeypatch.setattr(cursor.cursor_children, "child_context", context)
    parent = MagicMock(side_effect=AssertionError("parent context used by child"))
    monkeypatch.setattr(cursor.common, "native_context", parent)
    result = cursor.handle(event)
    assert result["permission"] == "deny"
    assert "NOT_OWNER" in result["user_message"]
    assert ("own-child-command" in result.get("additional_context", "")) is context_available
    parent.assert_not_called()


@pytest.mark.parametrize("code", ["BUSY", "SIZE_LIMIT"])
def test_only_trusted_cursor_observer_failure_can_allow_transport_failure(code: str) -> None:
    """Ordinary adapter failure keeps denial even for diagnostics tolerated by the supervisor.

    Args:
        code: Timeout or size diagnostic from the observer transport.
    """
    assert cursor.failure("beforeReadFile", code)["permission"] == "deny"
    assert cursor.failure("beforeReadFile", code, content_observer=True) == {"permission": "allow"}
    assert cursor.failure("subagentStart", code) == {}


def test_cursor_handle_uses_trusted_deadline_not_native_event_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired trusted deadline is not extended by a forged native event field.

    Args:
        monkeypatch: Clock and classification boundary.
    """
    # Expiry is checked before classification can read or mutate lifecycle state.
    monkeypatch.setattr(cursor.time, "monotonic", lambda: 20.0)
    normalize = MagicMock()
    monkeypatch.setattr(cursor.cursor_children, "normalize", normalize)
    result = cursor.handle(
        {"hook_event_name": "preToolUse", "_cursor_deadline": 999999999999},
        deadline=19.0,
    )
    assert result["permission"] == "deny"
    assert "BUSY" in result["user_message"]
    normalize.assert_not_called()


@pytest.mark.parametrize(
    "failed,fields",
    [
        (False, {"tool_output": None}),
        (True, {"error_message": "failed", "failure_type": "unknown"}),
    ],
)
def test_parent_post_rejects_unknown_task_completion_shape(
    failed: bool,
    fields: dict,
) -> None:
    """Moving completion dispatch cannot give malformed Task results reconciliation authority.

    Args:
        failed: Failed native completion rather than a successful result.
        fields: Unsupported native completion shape.
    """
    with pytest.raises(core.WorkspaceError, match="INVALID_REQUEST"):
        cursor.cursor_children.parent_post(
            {"tool_name": "Task", **fields},
            time.monotonic() + cursor.runner.DEADLINE_SECONDS,
            failed=failed,
        )
