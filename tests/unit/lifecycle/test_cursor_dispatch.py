"""Verify Cursor transitions enter shared dispatch without bypassing binding or scope."""

import copy

import pytest

from nplet.lifecycle import task_workspace as core
from tests.unit.lifecycle.test_operate_contracts import (
    cursor_apply,
    cursor_binding_request,
    cursor_launch,
    cursor_model,
    cursor_plan_request,
    cursor_request,
)

pytestmark = pytest.mark.unit


def test_cursor_dispatch_revalidates_exact_plan_replay_and_child_binding() -> None:
    """Dispatch planned work and exact replay through the normal shared entry point."""
    # The real dispatcher must preserve an exact receipt without republishing the plan.
    store, issue, _ = cursor_model()
    request = cursor_plan_request(issue)
    result = core.operate(store, issue, request)
    issue.state["requests"][core.sha(request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(request)),
        "result": result,
    }
    before = copy.deepcopy(issue.state)
    count = len(issue.commits)
    assert core.operate(store, issue, request) == result
    assert issue.state == before
    assert len(issue.commits) == count
    # Internal child binding establishes its own generation through shared dispatch.
    invocation, _ = cursor_launch(store, issue, "second", owned_paths=["context/second.md"])
    bound = core.operate(store, issue, cursor_binding_request(issue, invocation, "second"))
    assert bound["binding_generation"] == 1
    assert store.saved[-1] in issue.state["participants"]


@pytest.mark.parametrize("child", [False, True])
def test_cursor_dispatch_rejects_foreign_binding_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    child: bool,
) -> None:
    """Require matching store identity for parent and child transition dispatch.

    Args:
        monkeypatch: Restores the modeled binding lookup.
        child: Whether the request establishes a child rather than plans a launch.
    """
    # Return a foreign issue binding before either transition may publish state.
    store, issue, _ = cursor_model()
    monkeypatch.setattr(store, "binding", lambda: {"issue_id": "TEST-99", "binding_generation": 2})
    request = cursor_request(issue, "cursor-bind-child") if child else cursor_plan_request(issue)
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, request)
    assert captured.value.code == ("BINDING_CONFLICT" if child else "BINDING_MISSING")
    assert issue.state == before
    assert issue.commits == []


def test_classified_scope_refresh_and_owned_update_use_shared_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforce the current invocation's sources and outputs at the mutation boundary.

    Args:
        monkeypatch: Restores the modeled child's binding generation.
    """
    # Install one actual source and one output through the real transition helper.
    store, issue, _ = cursor_model()
    template = [
        {
            "id": "roadmap",
            "locator": "roadmap.md",
            "sha256": core.sha(issue.payload["roadmap.md"]),
            "reader": "cursor-child",
            "required": True,
            "authority": "task-workspace",
            "reason": "assigned-source",
            "stage": "execution",
        }
    ]
    invocation, _ = cursor_launch(store, issue, packet=template, owned_paths=["context/child.md"])
    binding = cursor_binding_request(issue, invocation)
    cursor_apply(store, issue, binding)
    child = core.participant_key(binding)
    packet = issue.state["participants"][child]["packet"]
    before = copy.deepcopy(issue.state)
    # Same-length source replacement is forbidden; exact refresh preserves the assignment.
    bad = cursor_request(
        issue, "scope", target_participant=child, packet=[{**packet[0], "id": "replacement"}]
    )
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, bad)
    assert captured.value.code == "SCOPE_MISSING"
    assert issue.state == before
    core.operate(
        store, issue, cursor_request(issue, "scope", target_participant=child, packet=packet)
    )
    assert issue.state["participants"][child]["ack"] is None
    # Source acknowledgment and output ownership are checked again at actual note mutation.
    monkeypatch.setattr(store, "binding", lambda: {"issue_id": issue.id, "binding_generation": 1})
    update = cursor_request(
        issue,
        "update",
        session_id=binding["session_id"],
        binding_generation=1,
        path="context/child.md",
        old_digest=None,
        content="verified output",
        provenance={
            "sources": [{"id": "check", "locator": "fixture", "sha256": "a" * 64}],
            "status": "draft",
            "applicability": "test",
        },
    )
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, update)
    assert captured.value.code == "NOT_READY"
    assert issue.state == before
    member = issue.state["participants"][child]
    member.update(status="ready", ack=core.sha(core.canonical(member["packet"])))
    core.operate(store, issue, update)
    assert issue.payload["context/child.md"] == b"verified output"
