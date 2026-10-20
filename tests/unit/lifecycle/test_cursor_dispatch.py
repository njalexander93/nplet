"""Verify Cursor transitions enter shared dispatch without bypassing binding or scope."""

import copy
from pathlib import Path

import pytest

from nplet.lifecycle import task_workspace as core
from tests.unit.lifecycle.cursor_support import (
    cursor_apply,
    cursor_binding_request,
    cursor_launch,
    cursor_model,
    cursor_plan_request,
    cursor_request,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("absolute", [False, True])
def test_source_receipts_gate_child_acknowledgment_through_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, absolute: bool
) -> None:
    """Dispatch exact source admission and both proofs before granting child readiness.

    Args:
        monkeypatch: Replaces only source-byte lookup and the current binding boundary.
        tmp_path: Native absolute root used for modeled source paths.
        absolute: External source locator versus a managed relative locator.
    """
    # Establish a real modeled invocation and assignment, with byte lookup isolated from storage.
    store, issue, _ = cursor_model()
    store.root = tmp_path
    locator = str(tmp_path / "source.md") if absolute else "roadmap.md"
    template = [
        {
            "id": "source",
            "locator": locator,
            "sha256": core.sha(b"approved roadmap"),
            "reader": "cursor-child",
            "required": True,
            "authority": "task-workspace",
            "reason": "assigned-source",
            "stage": "execution",
        }
    ]
    invocation, _ = cursor_launch(store, issue, packet=template)
    binding = cursor_binding_request(issue, invocation)
    core.operate(store, issue, binding)
    identity = {"session_id": binding["session_id"], "binding_generation": 1}
    key = core.participant_key(binding)
    packet = issue.state["participants"][key]["packet"]
    digest = core.sha(core.canonical(packet))
    ref = {**packet[0], "available": True}
    monkeypatch.setattr(core, "packet_reads", lambda *_args: [ref])
    monkeypatch.setattr(store, "binding", lambda: {"issue_id": issue.id, "binding_generation": 1})
    # An exact digest alone cannot acknowledge source delivery; failed checks commit nothing.
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
        core.operate(
            store, issue, cursor_request(issue, "acknowledge", **identity, packet_digest=digest)
        )
    assert issue.state == before
    core.operate(
        store,
        issue,
        cursor_request(
            issue,
            "source-read-start",
            **identity,
            tool_id="read",
            source_locator=locator,
            packet_digest=digest,
        ),
    )
    pending = issue.state["participants"][key]["pending"]["read"]["cursor_source"]
    path = str(Path(locator) if absolute else tmp_path / ".task" / issue.id / locator)
    assert pending == {
        "invocation_id": invocation["invocation_id"],
        "generation": 1,
        "packet_digest": digest,
        "locator": locator,
        "sha256": ref["sha256"],
        "file_path": path,
        "provisional": None,
    }
    # Actual content remains provisional; only the same successful Read settles this pending call.
    core.operate(
        store,
        issue,
        cursor_request(
            issue,
            "cursor-source-callback",
            **identity,
            tool_id="read",
            source_event="content",
            source_path=path,
            observed_sha256=ref["sha256"],
            content_length=16,
        ),
    )
    assert "read" in issue.state["participants"][key]["pending"]
    assert issue.state["participants"][key].get("cursor_sources", {}) == {}
    with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
        core.operate(
            store, issue, cursor_request(issue, "acknowledge", **identity, packet_digest=digest)
        )
    core.operate(
        store,
        issue,
        cursor_request(
            issue,
            "cursor-source-callback",
            **identity,
            tool_id="read",
            source_event="success",
            source_path=path,
            content_length=16,
        ),
    )
    member = issue.state["participants"][key]
    assert member["pending"] == {}
    assert member["cursor_sources"] == {
        locator: {
            "packet_digest": digest,
            "sha256": ref["sha256"],
            "invocation_id": invocation["invocation_id"],
        }
    }
    core.operate(
        store, issue, cursor_request(issue, "acknowledge", **identity, packet_digest=digest)
    )
    assert issue.state["participants"][key]["ack"] == digest
    assert issue.state["participants"][key]["status"] == "ready"


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
