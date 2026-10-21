"""Shared Cursor invocation scenarios over the existing in-memory lifecycle collaborators."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from nplet.lifecycle import _invocations as invocations
from nplet.lifecycle import task_workspace as core
from tests.unit.lifecycle.memory_support import MemoryIssue, MemoryStore, base_request


def cursor_model() -> tuple[MemoryStore, MemoryIssue, str]:
    """Reuse the existing memory collaborators with an explicit ready Cursor parent.

    Returns:
        The existing store, issue model and Cursor coordinator key.
    """
    # Move the existing modeled coordinator to the selected host without filesystem work.
    store, issue = MemoryStore(), MemoryIssue()
    old_key = issue.state["coordinator"]
    key = core.participant_key(base_request("read", host="cursor"))
    member = issue.state["participants"].pop(old_key)
    member.update(packet=[], ack=core.sha(core.canonical([])))
    issue.state["participants"][key] = member
    issue.state["coordinator"] = key
    issue.state["owners"] = {path: key for path in issue.state["owners"]}
    return store, issue, key


def cursor_request(issue: MemoryIssue, operation: str, **fields: object) -> dict[str, Any]:
    """Build one deterministic internal request for the memory model.

    Args:
        issue: Existing issue whose fixed modeled revision is named.
        operation: Internal Cursor transition.
        **fields: Explicit overrides for the scenario.

    Returns:
        A request naming the modeled parent unless explicitly overridden.
    """
    return base_request(
        operation,
        host="cursor",
        expected_revision=issue.state["revision"],
        request_id=operation + "-" + str(len(issue.commits)),
        **fields,
    )


def cursor_apply(
    store: MemoryStore,
    issue: MemoryIssue,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Call the real transition helper with an isolated staging copy.

    Args:
        store: Existing memory store.
        issue: Existing memory issue.
        request: Complete internal request.

    Returns:
        The actual transition response from the existing modeled commit.

    Raises:
        core.WorkspaceError: The transition rejects its explicit state or request.
    """
    # Production operate also passes a copied state and verified payload to this helper.
    return invocations.operate(
        store,
        issue,
        copy.deepcopy(issue.state),
        issue.payload.copy(),
        request,
    )


def cursor_plan_request(
    issue: MemoryIssue,
    identifier: str = "plan",
    **fields: object,
) -> dict[str, Any]:
    """Name a packet template and one bounded output using the model request builder.

    Args:
        issue: Existing modeled issue.
        identifier: Plan identity.
        **fields: Scenario-specific plan overrides.

    Returns:
        A ready-to-dispatch plan request.
    """
    # Keep supplied overrides distinct from the shared request metadata.
    values: dict[str, Any] = {
        "plan_id": identifier,
        "parent_conversation": "parent-conversation",
        "task_digest": "a" * 64,
        "packet": [],
        "owned_paths": ["context/child.md"],
    }
    values.update(fields)
    return cursor_request(issue, "cursor-plan", **values)


def cursor_launch(
    store: MemoryStore,
    issue: MemoryIssue,
    identifier: str = "plan",
    **plan_fields: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Plan and admit one invocation without modeling native execution.

    Args:
        store: Existing memory store.
        issue: Existing memory issue.
        identifier: Unique plan and tool suffix.
        **plan_fields: Explicit template, output or resume overrides.

    Returns:
        Admission response and its original request.

    Raises:
        core.WorkspaceError: Planning or admission is denied.
    """
    # Apply only the prerequisite planned metadata and parent pending reservation.
    cursor_apply(store, issue, cursor_plan_request(issue, identifier, **plan_fields))
    request = cursor_request(
        issue,
        "cursor-admit",
        plan_id=identifier,
        parent_conversation="parent-conversation",
        task_digest="a" * 64,
        tool_id="tool-" + identifier,
        native_call="native\n" + identifier,
    )
    return cursor_apply(store, issue, request), request


def cursor_binding_request(
    issue: MemoryIssue,
    invocation: dict[str, Any],
    identifier: str = "plan",
    child: str = "child",
    **fields: object,
) -> dict[str, Any]:
    """Build one actual child callback identity for an admitted invocation.

    Args:
        issue: Existing modeled issue.
        invocation: Admission response naming invocation_id.
        identifier: Plan/tool suffix.
        child: Exact synthetic returned conversation.
        **fields: Explicit callback overrides.

    Returns:
        A callback request under the normalized child identity.
    """
    # Opaque native generation is deliberately distinct from framework generation two.
    values: dict[str, Any] = {
        "session_id": "session/agent/" + child,
        "invocation_id": invocation["invocation_id"],
        "tool_id": "tool-" + identifier,
        "child_conversation": child,
        "native_generation": "actual-child-generation",
    }
    values.update(fields)
    return cursor_request(issue, "cursor-bind-child", **values)


def cursor_result(identifier: str = "plan", child: str = "child") -> dict[str, Any]:
    """Return bounded synthetic result metadata for internal transition tests.

    Args:
        identifier: Original native call suffix.
        child: Exact returned child conversation.

    Returns:
        A complete foreground projection, with no claim of SQLite authentication.
    """
    return {
        "parent_conversation": "parent-conversation",
        "native_call": "native\n" + identifier,
        "storage_key": "row\n" + identifier,
        "child_conversation": child,
        "kind": "foreground-success",
        "is_background": False,
        "background_reason": 0,
        "duration_ms": 12,
        "binary_sha256": "b" * 64,
        "executor_sha256": "c" * 64,
        "workbench_sha256": "d" * 64,
    }


def cursor_retirement_request(
    issue: MemoryIssue,
    invocation: dict[str, Any],
    identifier: str = "plan",
    child: str = "child",
) -> dict[str, Any]:
    """Build an original parent's retirement request.

    Args:
        issue: Existing modeled issue.
        invocation: Admission response naming invocation_id.
        identifier: Original call suffix.
        child: Exact child conversation.

    Returns:
        A complete internal retirement request.
    """
    return cursor_request(
        issue,
        "cursor-retire",
        invocation_id=invocation["invocation_id"],
        result=cursor_result(identifier, child),
    )


def cursor_denied(
    store: MemoryStore,
    issue: MemoryIssue,
    request: dict[str, Any],
    code: str,
) -> None:
    """Assert an exact denial and no modeled publication or binding effect.

    Args:
        store: Existing memory store.
        issue: Existing memory issue.
        request: Request expected to fail before commit.
        code: Exact contractual diagnostic.

    Raises:
        AssertionError: The diagnostic or unchanged-state invariant fails.
    """
    # Snapshot committed collaborators, not the disposable staging copy.
    state, payload = copy.deepcopy(issue.state), issue.payload.copy()
    commits, saved = len(issue.commits), list(store.saved)
    with pytest.raises(core.WorkspaceError) as captured:
        cursor_apply(store, issue, request)
    # A denial cannot publish copied metadata, notes, receipts or binding repairs.
    assert captured.value.code == code
    assert issue.state == state
    assert issue.payload == payload
    assert len(issue.commits) == commits
    assert store.saved == saved
