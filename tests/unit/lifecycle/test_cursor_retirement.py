"""Verify Cursor invocation retirement decisions without filesystem persistence."""

from __future__ import annotations

import copy

import pytest

from nplet.lifecycle import task_workspace as core
from tests.unit.lifecycle.cursor_support import (
    base_request,
    cursor_apply,
    cursor_binding_request,
    cursor_denied,
    cursor_launch,
    cursor_model,
    cursor_plan_request,
    cursor_retirement_request,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fault,code",
    [
        ("result-type", "INVALID_REQUEST"),
        ("keys", "INVALID_REQUEST"),
        ("native", "RESULT_NOT_TERMINAL"),
        ("kind", "RESULT_NOT_TERMINAL"),
        ("background", "RESULT_NOT_TERMINAL"),
        ("reason", "RESULT_NOT_TERMINAL"),
        ("duration", "RESULT_NOT_TERMINAL"),
        ("hash", "INVALID_REQUEST"),
        ("storage", "INVALID_REQUEST"),
        ("child", "STALE_BINDING"),
        ("generation", "STALE_BINDING"),
        ("classification", "STALE_BINDING"),
        ("effect", "PENDING_OPERATION"),
        ("phase", "STALE_BINDING"),
        ("revision", "REVISION_CONFLICT"),
    ],
)
def test_cursor_retirement_denies_invalid_result_or_live_effect(fault: str, code: str) -> None:
    """Keep the exact parent charge and child authority for every failed retirement.

    Args:
        fault: One projected-result or generation relationship changed.
        code: Exact expected diagnostic.
    """
    # Bind one genuine modeled callback before asking its parent to retire it.
    store, issue, _parent = cursor_model()
    admitted, _admission = cursor_launch(store, issue)
    binding = cursor_binding_request(issue, admitted)
    cursor_apply(store, issue, binding)
    member = issue.state["participants"][core.participant_key(binding)]
    request = cursor_retirement_request(issue, admitted)
    invocation = issue.state["cursor_invocations"][admitted["invocation_id"]]
    if fault == "result-type":
        request["result"] = []
    elif fault == "keys":
        request["result"]["extra"] = "unapproved"
    elif fault == "native":
        request["result"]["native_call"] = "other"
    elif fault == "kind":
        request["result"]["kind"] = "task-error"
    elif fault == "background":
        request["result"]["is_background"] = True
    elif fault == "reason":
        request["result"]["background_reason"] = True
    elif fault == "duration":
        request["result"]["duration_ms"] = -1
    elif fault == "hash":
        request["result"]["binary_sha256"] = "bad"
    elif fault == "storage":
        request["result"]["storage_key"] = ""
    elif fault == "child":
        request["result"]["child_conversation"] = "foreign"
    elif fault == "generation":
        member["generation"] = 2
    elif fault == "classification":
        member["cursor_invocation"] = "other"
    elif fault == "effect":
        member["pending"]["effect"] = {"status": "pending"}
    elif fault == "phase":
        invocation["phase"] = "unknown"
    else:
        request["expected_revision"] = 6
    cursor_denied(store, issue, request, code)


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("receipt", [False, True])
def test_cursor_retirement_commits_both_sides_and_duplicates_only_repair_binding(
    bound: bool,
    receipt: bool,
) -> None:
    """Detach the exact generation with parent settlement, then recognize identical metadata.

    Args:
        bound: Whether a genuine child callback precedes the returned result.
        receipt: Whether an exact prior-result receipt is explicitly seeded.
    """
    # The same transition supports ordinary and pre-callback foreground completion.
    store, issue, parent = cursor_model()
    admitted, _admission = cursor_launch(store, issue)
    if bound:
        cursor_apply(store, issue, cursor_binding_request(issue, admitted))
    request = cursor_retirement_request(issue, admitted)
    first = cursor_apply(store, issue, request)
    child = core.participant_key(
        base_request(
            "read",
            host="cursor",
            session_id="session/agent/child",
        )
    )
    member = issue.state["participants"][child]
    invocation = issue.state["cursor_invocations"][admitted["invocation_id"]]
    assert member["status"] == "detached" and member["ack"] is None
    assert member["pending"] == {} and member["generation"] == 1
    assert member["packet"] == ([] if bound else None)
    assert issue.state["participants"][parent]["pending"] == {}
    assert invocation["phase"] == "retired"
    assert invocation["terminal_result"] == request["result"]
    assert invocation["terminal_result"] is not request["result"]
    if receipt:
        issue.state["requests"][core.sha(request["request_id"].encode())] = {
            "digest": core.sha(core.canonical(request)),
            "result": first,
        }
    before, commits, saves = copy.deepcopy(issue.state), len(issue.commits), len(store.saved)
    duplicate = cursor_apply(store, issue, request)
    assert duplicate["binding_generation"] == 1
    if receipt:
        assert duplicate == first
    assert issue.state == before and len(issue.commits) == commits
    assert store.saved[saves:] == [child]


@pytest.mark.parametrize(
    "fault,code",
    [
        ("metadata", "REQUEST_CONFLICT"),
        ("generation", "STALE_BINDING"),
        ("classification", "STALE_BINDING"),
        ("missing", "STALE_BINDING"),
    ],
)
def test_cursor_retired_duplicate_cannot_detach_a_resumed_child(fault: str, code: str) -> None:
    """Reject contradictory results and old-generation replay after a newer child binding.

    Args:
        fault: One relationship changed after the original retirement.
        code: Exact expected diagnostic.
    """
    # Seed a real retired relationship through the actual helper, then change only one fact.
    store, issue, _parent = cursor_model()
    admitted, _admission = cursor_launch(store, issue)
    request = cursor_retirement_request(issue, admitted)
    cursor_apply(store, issue, request)
    child = core.participant_key(
        base_request(
            "read",
            host="cursor",
            session_id="session/agent/child",
        )
    )
    if fault == "metadata":
        request["result"]["binary_sha256"] = "e" * 64
    elif fault == "generation":
        issue.state["participants"][child]["generation"] = 2
    elif fault == "classification":
        issue.state["participants"][child]["cursor_invocation"] = "new-invocation"
    else:
        del issue.state["participants"][child]
    cursor_denied(store, issue, request, code)


@pytest.mark.parametrize("operation", ["cursor-bind-child", "cursor-retire"])
def test_cursor_early_transition_refuses_another_active_actual_conversation(
    operation: str,
) -> None:
    """Reject actual conversation collisions even before this invocation gets a callback.

    Args:
        operation: Callback binding or terminal-only projected return.
    """
    # Another normalized parent's invocation reserves the same actual conversation.
    store, issue, _parent = cursor_model()
    admitted, _admission = cursor_launch(store, issue)
    issue.state["cursor_invocations"]["other-parent-invocation"] = {
        "phase": "bound",
        "child_conversation": "child",
        "resume_child": None,
    }
    request = (
        cursor_binding_request(issue, admitted)
        if operation == "cursor-bind-child"
        else cursor_retirement_request(issue, admitted)
    )
    cursor_denied(store, issue, request, "PENDING_OPERATION")


@pytest.mark.parametrize(
    "fault,code",
    [
        ("host", "HOST_UNSUPPORTED"),
        ("terminal", "TERMINAL"),
        ("storage", "TERMINAL"),
    ],
)
def test_cursor_transition_rejects_host_and_retention_failures(fault: str, code: str) -> None:
    """Reject the outer internal-operation boundary before any modeled publication.

    Args:
        fault: Host or retained issue state made invalid.
        code: Exact expected diagnostic.
    """
    # Keep the request otherwise complete so this test reaches the intended first guard.
    store, issue, _key = cursor_model()
    request = cursor_plan_request(issue)
    if fault == "host":
        request["host"] = "codex"
    elif fault == "terminal":
        issue.state["disposition"] = "completed"
    else:
        issue.state["storage"] = "cleaned"
    cursor_denied(store, issue, request, code)


@pytest.mark.parametrize("fault", ["attached", "pending", "unclassified"])
def test_cursor_resume_plan_requires_a_retired_classified_empty_child(fault: str) -> None:
    """Reject planning that would borrow a live or unclassified conversation.

    Args:
        fault: One prerequisite of the retained conversation made invalid.
    """
    # Create classification through the real early-return transition.
    store, issue, _key = cursor_model()
    first, _request = cursor_launch(store, issue)
    cursor_apply(store, issue, cursor_retirement_request(issue, first))
    child = core.participant_key(
        base_request(
            "read",
            host="cursor",
            session_id="session/agent/child",
        )
    )
    member = issue.state["participants"][child]
    if fault == "attached":
        member["status"] = "attached"
    elif fault == "pending":
        member["pending"]["unresolved"] = {"status": "pending"}
    else:
        del member["cursor_invocation"]
    cursor_denied(
        store,
        issue,
        cursor_plan_request(
            issue,
            "second",
            resume_child="child",
            owned_paths=["context/second.md"],
        ),
        "STALE_BINDING",
    )


@pytest.mark.parametrize("operation", ["cursor-bind-child", "cursor-retire"])
@pytest.mark.parametrize("fault", ["generation", "attached", "pending", "unclassified"])
def test_cursor_resume_effect_boundary_rechecks_its_retired_generation(
    operation: str,
    fault: str,
) -> None:
    """Do not bind or classify an early return over a changed retired conversation.

    Args:
        operation: First callback or terminal-only return.
        fault: One retired relationship changed after the new admission.
    """
    # Admit explicit resume before simulating a later change in its inspected prior generation.
    store, issue, _key = cursor_model()
    first, _request = cursor_launch(store, issue)
    cursor_apply(store, issue, cursor_retirement_request(issue, first))
    second, _request = cursor_launch(
        store,
        issue,
        "second",
        resume_child="child",
        owned_paths=["context/second.md"],
    )
    child = core.participant_key(
        base_request(
            "read",
            host="cursor",
            session_id="session/agent/child",
        )
    )
    member = issue.state["participants"][child]
    if fault == "generation":
        member["generation"] = 2
    elif fault == "attached":
        member["status"] = "attached"
    elif fault == "pending":
        member["pending"]["effect"] = {"status": "pending"}
    else:
        del member["cursor_invocation"]
    request = (
        cursor_binding_request(issue, second, "second")
        if operation == "cursor-bind-child"
        else cursor_retirement_request(issue, second, "second")
    )
    cursor_denied(store, issue, request, "STALE_BINDING")


def test_unknown_cursor_transition_is_not_silently_treated_as_retirement() -> None:
    """Keep the internal dispatch vocabulary bounded even with otherwise valid correlation."""
    # A valid existing invocation lets the test reach the explicit operation discriminator.
    store, issue, _parent = cursor_model()
    admitted, _request = cursor_launch(store, issue)
    request = cursor_retirement_request(issue, admitted)
    request["operation"] = "cursor-unsupported"
    cursor_denied(store, issue, request, "UNSUPPORTED_OPERATION")
