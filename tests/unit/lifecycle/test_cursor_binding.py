"""Verify Cursor invocation binding decisions without filesystem persistence."""

from __future__ import annotations

import copy
from typing import Any

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
    "fields,code",
    [
        ({"expected_revision": 6}, "REVISION_CONFLICT"),
        ({"packet": [{"reader": "foreign"}]}, "SCOPE_MISSING"),
        ({"owned_paths": "context/child.md"}, "INVALID_REQUEST"),
        ({"owned_paths": ["context/child.md", "context/child.md"]}, "INVALID_REQUEST"),
        ({"owned_paths": ["roadmap.md"]}, "NOT_OWNER"),
        ({"owned_paths": ["context/../foreign.md"]}, "UNSAFE_PATH"),
        ({"task_digest": "bad"}, "INVALID_REQUEST"),
        ({"parent_conversation": ""}, "INVALID_REQUEST"),
        ({"resume_child": "unknown-child"}, "STALE_BINDING"),
    ],
)
def test_cursor_plan_rejects_invalid_boundary_without_publication(
    fields: dict[str, Any],
    code: str,
) -> None:
    """Validate plan revision, template, scope and resume identity before publication.

    Args:
        fields: Single invalid plan override.
        code: Exact expected diagnostic.
    """
    # Apply metadata overrides after request construction to permit expected_revision overrides.
    store, issue, _key = cursor_model()
    request = cursor_plan_request(issue)
    request.update(fields)
    cursor_denied(store, issue, request, code)


@pytest.mark.parametrize("native_generation", [None, "actual-child-generation"])
@pytest.mark.parametrize("receipt", [False, True])
def test_cursor_bind_installs_independent_scope_and_repairs_duplicate_binding(
    native_generation: str | None,
    receipt: bool,
) -> None:
    """Bind an actual callback once and repair only its derived binding on retry.

    Args:
        native_generation: Opaque observed child generation or host omission.
        receipt: Whether an exact replay receipt is explicitly seeded.
    """
    # Use a nonempty template to verify reader replacement rather than merely an empty packet.
    store, issue, parent = cursor_model()
    packet = [
        {
            "id": "source",
            "locator": "roadmap.md",
            "sha256": core.sha(b"approved roadmap"),
            "required": True,
            "authority": "fixture",
            "reason": "test",
            "stage": "execution",
            "reader": "cursor-child",
        }
    ]
    admitted, _admission = cursor_launch(store, issue, packet=packet)
    request = cursor_binding_request(issue, admitted, native_generation=native_generation)
    bound = cursor_apply(store, issue, request)
    child = core.participant_key(request)
    member = issue.state["participants"][child]
    assert bound["binding_generation"] == 1
    assert member["packet"] == [{**packet[0], "reader": child}]
    assert member["packet"] is not packet
    assert member["ack"] is None and member["pending"] == {}
    assert member["status"] == "attached"
    assert issue.state["owners"]["context/child.md"] == child
    assert issue.state["coordinator"] == parent
    if receipt:
        issue.state["requests"][core.sha(request["request_id"].encode())] = {
            "digest": core.sha(core.canonical(request)),
            "result": bound,
        }
    before, commits, saves = copy.deepcopy(issue.state), len(issue.commits), len(store.saved)
    duplicate = cursor_apply(store, issue, request)
    assert duplicate["binding_generation"] == 1
    if receipt:
        assert duplicate == bound
    assert issue.state == before and len(issue.commits) == commits
    assert store.saved[saves:] == [child]


@pytest.mark.parametrize(
    "fault,code",
    [
        ("unknown", "UNKNOWN_OPERATION"),
        ("parent-missing", "STALE_BINDING"),
        ("parent-generation", "STALE_BINDING"),
        ("parent-pending", "REQUEST_CONFLICT"),
        ("child-session", "NOT_OWNER"),
        ("tool", "REQUEST_CONFLICT"),
        ("native-generation", "INVALID_REQUEST"),
        ("retired", "STALE_BINDING"),
        ("active-conversation", "PENDING_OPERATION"),
        ("resume-other", "REQUEST_CONFLICT"),
        ("resume-missing", "STALE_BINDING"),
        ("existing-unplanned", "STALE_BINDING"),
        ("owner", "NOT_OWNER"),
    ],
)
def test_cursor_bind_denies_conflicting_identity_or_scope(fault: str, code: str) -> None:
    """Require genuine child, current parent, independent output and explicit resume.

    Args:
        fault: One pre-binding relationship made incompatible.
        code: Exact expected diagnostic.
    """
    # Admit once; manipulate only the state dimension checked by this case.
    store, issue, parent = cursor_model()
    admitted, _admission = cursor_launch(store, issue)
    request = cursor_binding_request(issue, admitted)
    identifier = admitted["invocation_id"]
    invocation = issue.state["cursor_invocations"][identifier]
    child = core.participant_key(request)
    if fault == "unknown":
        request["invocation_id"] = "missing"
    elif fault == "parent-missing":
        del issue.state["participants"][parent]
    elif fault == "parent-generation":
        issue.state["participants"][parent]["generation"] = 3
    elif fault == "parent-pending":
        issue.state["participants"][parent]["pending"] = {}
    elif fault == "child-session":
        request["session_id"] = "foreign/agent/child"
    elif fault == "tool":
        request["tool_id"] = "other"
    elif fault == "native-generation":
        request["native_generation"] = ""
    elif fault == "retired":
        invocation["phase"] = "retired"
    elif fault == "active-conversation":
        issue.state["cursor_invocations"]["other"] = {
            "phase": "admitted",
            "child_conversation": None,
            "resume_child": "child",
        }
    elif fault == "resume-other":
        invocation["resume_child"] = "other-child"
    elif fault == "resume-missing":
        invocation["resume_child"] = "child"
    elif fault == "existing-unplanned":
        issue.state["participants"][child] = {
            "generation": 1,
            "status": "detached",
            "pending": {},
            "packet": None,
            "ack": None,
            "cursor_invocation": "older",
        }
    else:
        issue.state["owners"]["context/child.md"] = "other-owner"
    cursor_denied(store, issue, request, code)


@pytest.mark.parametrize("fault", ["native", "generation", "status", "classification", "child"])
def test_cursor_bound_duplicate_checks_the_original_child_generation(fault: str) -> None:
    """Keep duplicate callback repair from reviving or replacing child authority.

    Args:
        fault: One already-bound relationship contradicted by the callback.
    """
    # Bind one child before corrupting its duplicate relationship.
    store, issue, _parent = cursor_model()
    admitted, _admission = cursor_launch(store, issue)
    request = cursor_binding_request(issue, admitted)
    cursor_apply(store, issue, request)
    invocation = issue.state["cursor_invocations"][admitted["invocation_id"]]
    member = issue.state["participants"][core.participant_key(request)]
    if fault == "native":
        request["native_generation"] = "different-native-generation"
    elif fault == "generation":
        member["generation"] = 2
    elif fault == "status":
        member["status"] = "detached"
    elif fault == "classification":
        member["cursor_invocation"] = "other"
    else:
        invocation["child_participant"] = "foreign"
    cursor_denied(store, issue, request, "STALE_BINDING")


@pytest.mark.parametrize("early", [False, True])
def test_cursor_resume_advances_generation_with_fresh_or_absent_packet(early: bool) -> None:
    """Advance a planned resumed conversation without carrying old acknowledgment.

    Args:
        early: Whether the resumed invocation returns before its first callback.
    """
    # A terminal-only first invocation provides classification without invented source delivery.
    store, issue, parent = cursor_model()
    first, _request = cursor_launch(store, issue)
    cursor_apply(store, issue, cursor_retirement_request(issue, first))
    first_child = core.participant_key(
        base_request(
            "read",
            host="cursor",
            session_id="session/agent/child",
        )
    )
    old = copy.deepcopy(issue.state["participants"][first_child])
    assert old["packet"] is None and old["pending"] == {} and old["status"] == "detached"
    second, _request = cursor_launch(
        store,
        issue,
        "second",
        resume_child="child",
        owned_paths=["context/second.md"],
    )
    invocation = issue.state["cursor_invocations"][second["invocation_id"]]
    assert invocation["resume_generation"] == 1
    if early:
        result = cursor_apply(store, issue, cursor_retirement_request(issue, second, "second"))
    else:
        result = cursor_apply(store, issue, cursor_binding_request(issue, second, "second"))
    member = issue.state["participants"][first_child]
    assert result["binding_generation"] == 2
    assert member["generation"] == 2 and member["ack"] is None and member["pending"] == {}
    assert member["cursor_invocation"] == second["invocation_id"]
    assert member["packet"] is None if early else member["packet"] == []
    assert member["status"] == ("detached" if early else "attached")
    assert issue.state["coordinator"] == parent


def test_cursor_bound_resume_can_reuse_its_own_existing_note_without_acknowledgment() -> None:
    """Install a fresh generation while preserving an explicitly planned existing note owner."""
    # The first callback installs the actual note owner before its invocation retires.
    store, issue, _parent = cursor_model()
    first, _request = cursor_launch(store, issue)
    binding = cursor_binding_request(issue, first)
    cursor_apply(store, issue, binding)
    cursor_apply(store, issue, cursor_retirement_request(issue, first))
    child = core.participant_key(binding)
    assert issue.state["owners"]["context/child.md"] == child
    second, _request = cursor_launch(store, issue, "second", resume_child="child")
    result = cursor_apply(store, issue, cursor_binding_request(issue, second, "second"))
    assert result["binding_generation"] == 2
    assert issue.state["owners"]["context/child.md"] == child
    assert issue.state["participants"][child]["ack"] is None
