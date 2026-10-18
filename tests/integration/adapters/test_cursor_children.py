"""Exercise real Cursor adapter Store/lock lifetimes with synthetic native evidence only."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator

import pytest

from nplet.adapters import common, cursor, cursor_results
from nplet.adapters import cursor_children as children
from nplet.lifecycle import task_workspace as core
from tests.support import Fixture

pytestmark = pytest.mark.integration
CHILD = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def cursor_case(monkeypatch: pytest.MonkeyPatch) -> Iterator[Fixture]:
    """Create a real registered disposable Cursor repository with external evidence mocked.

    Args:
        monkeypatch: Replaces only installed source/result reads and native remote marker.

    Yields:
        Ready real-core Cursor coordinator.
    """
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    case = Fixture()
    case.setUp()
    case.base["host"] = "cursor"
    case.base["coordinator"] = core.participant_key(case.base)
    case.create()
    case.ready()
    monkeypatch.setattr(
        cursor_results,
        "pinned_sources",
        lambda _deadline: {"executor_sha256": "a" * 64, "workbench_sha256": "b" * 64},
    )

    def terminal(invocation: dict, _deadline: float) -> dict:
        """Supply independent synthetic result metadata, without replacing any core/Store path.

        Args:
            invocation: Exact committed original-call metadata.
            _deadline: Caller budget.

        Returns:
            The exact closed foreground projection for this synthetic invocation.
        """
        return {
            "parent_conversation": invocation["parent_conversation"],
            "native_call": invocation["native_call"],
            "storage_key": "bubbleId:"
            + invocation["parent_conversation"]
            + ":bubble/t:"
            + invocation["native_call"],
            "child_conversation": CHILD,
            "kind": "foreground-success",
            "is_background": False,
            "background_reason": 0,
            "duration_ms": 12,
            "binary_sha256": "c" * 64,
            "executor_sha256": "a" * 64,
            "workbench_sha256": "b" * 64,
        }

    monkeypatch.setattr(cursor_results, "read_terminal", terminal)
    try:
        yield case
    finally:
        case.doCleanups()


def cursor_native(
    case: Fixture,
    name: str,
    *,
    child: bool = False,
    parent_call: str | None = None,
    generation: str = "child-generation-one",
    **fields: object,
) -> dict:
    """Build synthetic native identities while retaining real repository and locking behavior.

    Args:
        case: Real disposable registered repository.
        name: Actual Cursor hook spelling.
        child: Select independent actual synthetic child UUID.
        parent_call: Exact parent call marker, or intentionally omitted.
        generation: Native child frame for stale-event fencing.
        **fields: Native tool/content/completion fields.

    Returns:
        Synthetic native envelope with widened test deadline for deterministic transactions.
    """
    # The widened direct-handler budget does not establish the native2second latency requirement.
    event = {
        "hook_event_name": name,
        "conversation_id": CHILD if child else case.base["session_id"],
        "generation_id": generation if child else "parent-generation",
        "workspace_roots": [str(case.root)],
        "cwd": str(case.root),
        "cursor_version": "3.25.9",
        "_cursor_deadline": time.monotonic() + 10,
    }
    if parent_call is not None:
        event["parent_tool_call_id"] = parent_call
    event.update(fields)
    return event


def planned_task(case: Fixture, identifier: str, raw_call: str, resume: str | None = None) -> dict:
    """Plan and admit one exact local Task through the real Cursor handler.

    Args:
        case: Real disposable coordinator.
        identifier: Disjoint plan/output suffix.
        raw_call: Exact native call with optional newline.
        resume: Retired child identity for a new invocation, if requested.

    Returns:
        Exact planned native input.
    """
    args = {
        "description": identifier,
        "environment": "local",
        "model": "selected-model",
        "prompt": "Read assigned sources and write only the owned note.",
        "run_in_background": False,
        "subagent_type": "generalPurpose",
    }
    if resume is not None:
        args["resume"] = resume
    packet = [
        {
            "id": "roadmap",
            "locator": "roadmap.md",
            "sha256": case.state()["files"]["roadmap.md"],
            "required": True,
            "authority": "fixture",
            "reason": "native-synthetic",
            "stage": "implementation",
            "reader": "cursor-child",
        }
    ]
    case.call(
        "cursor-plan",
        expected_revision=case.state()["revision"],
        plan_id=identifier,
        parent_conversation=case.base["session_id"],
        task_digest=core.sha(core.canonical(args)),
        packet=packet,
        owned_paths=["context/" + identifier + ".md"],
        resume_child=resume,
    )
    result = cursor.handle(
        cursor_native(case, "preToolUse", tool_name="Task", tool_use_id=raw_call, tool_input=args)
    )
    assert result["permission"] == "allow", result
    return args


def read_pre(
    case: Fixture, parent_call: str, raw_tool: str, generation: str = "child-generation-one"
) -> str:
    """Bind the genuine child and admit one exact assigned-source Read.

    Args:
        case: Real disposable repository.
        parent_call: Exact original parent Task call.
        raw_tool: Exact child Read call.
        generation: Native child frame.

    Returns:
        Exact native source file path.
    """
    path = str(case.root / ".task" / case.base["issue_id"] / "roadmap.md")
    result = cursor.handle(
        cursor_native(
            case,
            "preToolUse",
            child=True,
            parent_call=parent_call,
            generation=generation,
            tool_name="Read",
            tool_use_id=raw_tool,
            tool_input={"file_path": path},
        )
    )
    assert result["permission"] == "allow", result
    return path


def read_complete(
    case: Fixture,
    parent_call: str,
    raw_tool: str,
    path: str,
    generation: str = "child-generation-one",
) -> None:
    """Send actual fixture bytes through the synthetic content and post-success envelopes.

    Args:
        case: Real disposable repository.
        parent_call: Exact current parent Task.
        raw_tool: Exact already-admitted child Read.
        path: Current source path.
        generation: Native child frame.
    """
    from pathlib import Path

    content = Path(path).read_bytes().decode("utf-8")
    response = cursor.handle(
        cursor_native(
            case,
            "beforeReadFile",
            child=True,
            parent_call=parent_call,
            generation=generation,
            tool_use_id=raw_tool,
            file_path=path,
            content=content,
            attachments=[],
        )
    )
    assert response["permission"] == "allow", response
    output = json.dumps(
        {"file_path": path, "content_length": len(content.encode("utf-16-le")) // 2}
    )
    assert (
        cursor.handle(
            cursor_native(
                case,
                "postToolUse",
                child=True,
                parent_call=parent_call,
                generation=generation,
                tool_name="Read",
                tool_use_id=raw_tool,
                tool_input={"file_path": path},
                tool_output=output,
            )
        )
        == {}
    )


def own_lifecycle(
    case: Fixture,
    parent_call: str,
    operation: str,
    generation: str = "child-generation-one",
    **fields: object,
) -> dict:
    """Validate the native command and execute its real lifecycle transaction directly.

    This is synthetic protocol evidence, not a native Shell process launch.

    Args:
        case: Real disposable repository.
        parent_call: Exact current parent Task.
        operation: Allowed own lifecycle operation.
        generation: Native child frame.
        **fields: Explicit operation metadata.

    Returns:
        Actual core transaction result.
    """
    observed = {"cwd": str(case.root), "session_id": case.base["session_id"] + "/agent/" + CHILD}
    bound = common.request_for(observed, operation, "cursor")
    request = {
        key: value
        for key, value in bound.items()
        if key in common.COMMON_FIELDS and key != "issue_uuid"
    }
    request.update(fields)
    command = common.bootstrap_command(request, "cursor")
    raw_tool = "own-" + str(uuid.uuid4())
    native = cursor_native(
        case,
        "preToolUse",
        child=True,
        parent_call=parent_call,
        generation=generation,
        tool_name="Shell",
        tool_use_id=raw_tool,
        tool_input={"command": command},
    )
    decision = cursor.handle(native)
    assert decision["permission"] == "allow", decision
    result = core.execute(request)
    native.update(
        hook_event_name="postToolUse",
        tool_output=json.dumps({"exitCode": 0 if result["ok"] else 3}),
    )
    assert cursor.handle(native) == {}
    return result


def acknowledge_ready(
    case: Fixture, parent_call: str, generation: str = "child-generation-one"
) -> None:
    """Acknowledge only after the real receipt gate accepts completed source proof.

    Args:
        case: Real disposable repository.
        parent_call: Exact current parent Task.
        generation: Native child frame.
    """
    read = case.require_ok(own_lifecycle(case, parent_call, "read", generation))
    case.require_ok(
        own_lifecycle(
            case, parent_call, "acknowledge", generation, packet_digest=read["packet_digest"]
        )
    )
    case.require_ok(own_lifecycle(case, parent_call, "ready", generation))


def reconcile_native(case: Fixture, raw_call: str) -> dict:
    """Authorize the actual parent command, independently obtain evidence and retire atomically.

    Args:
        case: Real disposable repository.
        raw_call: Exact original native Task call.

    Returns:
        Actual core retirement result.
    """
    token = children.call_token(raw_call)
    command = children.reconcile_command(case.base["issue_id"], token)
    native = cursor_native(
        case,
        "preToolUse",
        tool_name="Shell",
        tool_use_id="reconcile-" + str(uuid.uuid4()),
        tool_input={"command": command},
    )
    decision = cursor.handle(native)
    assert decision["permission"] == "allow", decision
    result = children.reconcile(str(case.root), case.base["issue_id"], token, time.monotonic() + 10)
    native.update(hook_event_name="postToolUse", tool_output=json.dumps({"exitCode": 0}))
    assert cursor.handle(native) == {}
    return result


def test_real_cursor_store_lifecycle_owns_sources_note_and_exact_retirement(
    cursor_case: Fixture,
) -> None:
    """Run real locks from planned Task through verified sources, owned note and retired fencing.

    Args:
        cursor_case: Ready real disposable Cursor repository.
    """
    case = cursor_case
    raw_call = "parent-call\ntrace-one"
    args = planned_task(case, "first", raw_call)
    parent = case.base["coordinator"]
    token = children.call_token(raw_call)
    assert token in case.state()["participants"][parent]["pending"]
    # Admission reserves the Task but does not invent a child participant.
    assert set(case.state()["participants"]) == {parent}
    # Only the actual independent conversation binds, then source proof and success precede ack.
    path = read_pre(case, raw_call, "read-one")
    child_key = core.participant_key(
        {"host": "cursor", "session_id": case.base["session_id"] + "/agent/" + CHILD}
    )
    assert case.state()["participants"][child_key]["ack"] is None
    read = case.require_ok(own_lifecycle(case, raw_call, "read"))
    assert (
        own_lifecycle(case, raw_call, "acknowledge", packet_digest=read["packet_digest"])["code"]
        == "SOURCE_DELIVERY_REQUIRED"
    )
    read_complete(case, raw_call, "read-one", path)
    acknowledge_ready(case, raw_call)
    # Managed output commits through the actual own-command/core ownership route.
    case.require_ok(
        own_lifecycle(
            case,
            raw_call,
            "update",
            expected_revision=case.state()["revision"],
            path="context/first.md",
            old_digest=None,
            content="bounded child result\n",
            provenance={"sources": case.evidence(), "status": "draft", "applicability": "fixture"},
        )
    )
    assert case.state()["files"]["context/first.md"] == core.sha(b"bounded child result\n")
    for tool, tool_input in (
        ("Write", {"file_path": path, "content": "forbidden"}),
        ("Grep", {"pattern": "anything"}),
        ("Shell", {"command": "python -m pytest"}),
    ):
        before = case.state()
        denied = cursor.handle(
            cursor_native(
                case,
                "preToolUse",
                child=True,
                generation="child-generation-one",
                tool_name=tool,
                tool_use_id="denied-" + tool,
                tool_input=tool_input,
            )
        )
        assert denied["permission"] == "deny", denied
        assert case.state() == before
    # Generic Task post does not settle the charge; exact independent reconciliation does.
    response = cursor.handle(
        cursor_native(
            case,
            "postToolUse",
            tool_name="Task",
            tool_use_id=raw_call,
            tool_input=args,
            tool_output="{}",
        )
    )
    assert "cursor-reconcile" in response["additional_context"]
    assert token in case.state()["participants"][parent]["pending"]
    case.require_ok(reconcile_native(case, raw_call))
    state = case.state()
    assert state["participants"][parent]["pending"] == {}
    assert state["participants"][child_key]["status"] == "detached"
    assert state["participants"][child_key]["pending"] == {}
    # Exact reconciliation replays have no new state; omitted child markers stay retired.
    case.require_ok(reconcile_native(case, raw_call))
    assert case.state() == state
    denied = cursor.handle(
        cursor_native(
            case,
            "preToolUse",
            child=True,
            tool_name="Read",
            tool_use_id="late-read",
            tool_input={"file_path": path},
        )
    )
    assert denied["permission"] == "deny"
    assert "STALE_BINDING" in json.dumps(denied)
    assert case.state() == state
    # Ordinary parent foreground Shell and file-tool paths still admit and settle their own work.
    for tool, tool_input, output in (
        ("Shell", {"command": "python -m pytest -q"}, json.dumps({"exitCode": 0})),
        ("Read", {"file_path": path}, "{}"),
        ("Edit", {"file_path": path}, "{}"),
        ("Grep", {"pattern": "anything"}, "{}"),
    ):
        native = cursor_native(
            case, "preToolUse", tool_name=tool, tool_use_id="parent-" + tool, tool_input=tool_input
        )
        assert cursor.handle(native)["permission"] == "allow"
        native.update(hook_event_name="postToolUse", tool_output=output)
        assert cursor.handle(native) == {}
    assert case.state()["participants"][parent]["pending"] == {}


def test_real_cursor_resume_fresh_scope_rejects_old_completion(cursor_case: Fixture) -> None:
    """Resume the same real child participant freshly and fence old native/control completions.

    Args:
        cursor_case: Ready real disposable Cursor repository.
    """
    case = cursor_case
    first = "parent-call\ntrace-one"
    planned_task(case, "first", first)
    path = read_pre(case, first, "first-read")
    # Failed assigned work can still have an independently terminal foreground Task invocation.
    failure = cursor_native(
        case,
        "postToolUseFailure",
        child=True,
        parent_call=first,
        tool_name="Read",
        tool_use_id="first-read",
        tool_input={"file_path": path},
        error_message="synthetic failed work",
        failure_type="error",
    )
    cursor.handle(failure)
    case.require_ok(reconcile_native(case, first))
    second = "parent-call\ntrace-two"
    planned_task(case, "second", second, resume=CHILD)
    path = read_pre(case, second, "second-read", "child-generation-two")
    key = core.participant_key(
        {"host": "cursor", "session_id": case.base["session_id"] + "/agent/" + CHILD}
    )
    member = case.state()["participants"][key]
    assert member["generation"] == 2
    assert member["ack"] is None
    assert member.get("cursor_sources", {}) == {}
    before = case.state()
    # An omitted old parent marker cannot make an old native generation current.
    old_post = cursor_native(
        case,
        "postToolUse",
        child=True,
        generation="child-generation-one",
        tool_name="Read",
        tool_use_id="first-read",
        tool_input={"file_path": path},
        tool_output=json.dumps({"file_path": path, "content_length": 1}),
    )
    assert "STALE_BINDING" in json.dumps(cursor.handle(old_post))
    assert case.state() == before
    with pytest.raises(core.WorkspaceError) as error:
        children.reconcile(
            str(case.root), case.base["issue_id"], children.call_token(first), time.monotonic() + 10
        )
    assert error.value.code == "STALE_BINDING"
    assert case.state() == before
    # Current source proof/readiness and only the resumed assignment's output succeed.
    read_complete(case, second, "second-read", path, "child-generation-two")
    acknowledge_ready(case, second, "child-generation-two")
    denied = own_lifecycle(
        case,
        second,
        "update",
        "child-generation-two",
        expected_revision=case.state()["revision"],
        path="context/first.md",
        old_digest=None,
        content="stale ownership",
        provenance={"sources": case.evidence(), "status": "draft", "applicability": "fixture"},
    )
    assert denied["code"] == "NOT_OWNER"
    case.require_ok(
        own_lifecycle(
            case,
            second,
            "update",
            "child-generation-two",
            expected_revision=case.state()["revision"],
            path="context/second.md",
            old_digest=None,
            content="resumed owned output\n",
            provenance={"sources": case.evidence(), "status": "draft", "applicability": "fixture"},
        )
    )
    case.require_ok(reconcile_native(case, second))
    assert case.state()["participants"][key]["generation"] == 2
    assert case.state()["participants"][key]["status"] == "detached"
    assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}


def test_issue_local_reconcile_ignores_unrelated_corrupt_issue(
    cursor_case: Fixture,
) -> None:
    """Retire and replay the selected Task without reading unrelated corrupt state.

    Args:
        cursor_case: Real disposable registered Cursor coordinator.
    """
    case = cursor_case
    raw_call = "issue-local\nnative-call"
    planned_task(case, "local", raw_call)
    issue_id = case.base["issue_id"]
    token = children.call_token(raw_call)
    parent = case.base["coordinator"]
    command = children.reconcile_command(issue_id, token)
    # Normalize before adding corruption: the unchanged classifier still performs a census.
    observed = children.normalize(
        cursor_native(
            case,
            "preToolUse",
            tool_name="Shell",
            tool_use_id="issue-local-reconcile",
            tool_input={"command": command},
        ),
        time.monotonic() + 10,
    )
    unrelated = case.root / ".task" / ".control" / "issues" / "OTHER-2"
    unrelated.mkdir()
    corrupt = unrelated / "state.json"
    corrupt.write_bytes(b'{"intentionally-corrupt":')
    # The old global reconciliation census would attempt to parse this real malformed issue.
    children.authorize_reconcile(observed, issue_id, token, time.monotonic() + 10)
    case.require_ok(children.reconcile(str(case.root), issue_id, token, time.monotonic() + 10))
    state = case.state()
    assert state["participants"][parent]["pending"] == {}
    invocation = next(
        item for item in state["cursor_invocations"].values() if item["tool_id"] == token
    )
    assert invocation["phase"] == "retired"
    assert invocation["terminal_result"]["native_call"] == raw_call
    # The same exact issue/call can replay without reading or repairing the corrupt neighbor.
    children.authorize_reconcile(observed, issue_id, token, time.monotonic() + 10)
    case.require_ok(children.reconcile(str(case.root), issue_id, token, time.monotonic() + 10))
    assert case.state() == state
    assert corrupt.read_bytes() == b'{"intentionally-corrupt":'


def test_issue_local_reconcile_keeps_pending_child_read_charged(
    cursor_case: Fixture,
) -> None:
    """Retain exact pending work until its successful completion permits retirement.

    Args:
        cursor_case: Real disposable registered Cursor coordinator.
    """
    case = cursor_case
    raw_call = "pending-local\nnative-call"
    planned_task(case, "pending-local", raw_call)
    path = read_pre(case, raw_call, "pending-local-read")
    issue_id = case.base["issue_id"]
    token = children.call_token(raw_call)
    parent = case.base["coordinator"]
    child = core.participant_key(
        {"host": "cursor", "session_id": case.base["session_id"] + "/agent/" + CHILD}
    )
    before = case.state()
    assert token in before["participants"][parent]["pending"]
    assert children.call_token("pending-local-read") in before["participants"][child]["pending"]
    with pytest.raises(core.WorkspaceError, match="PENDING_OPERATION"):
        children.reconcile(str(case.root), issue_id, token, time.monotonic() + 10)
    assert case.state() == before
    # No generic completion or forced detach: the exact content/success protocol releases Read.
    read_complete(case, raw_call, "pending-local-read", path)
    case.require_ok(children.reconcile(str(case.root), issue_id, token, time.monotonic() + 10))
    after = case.state()
    assert after["participants"][parent]["pending"] == {}
    assert after["participants"][child]["pending"] == {}
    assert after["participants"][child]["status"] == "detached"


def test_issue_local_reconcile_refuses_foreign_issue_and_unknown_call(
    cursor_case: Fixture,
) -> None:
    """Explicit locators cannot grant another issue or substitute an unadmitted call.

    Args:
        cursor_case: Real disposable registered Cursor coordinator.
    """
    case = cursor_case
    raw_call = "authorized-local\nnative-call"
    planned_task(case, "authorized-local", raw_call)
    issue_id = case.base["issue_id"]
    token = children.call_token(raw_call)
    observed = children.normalize(
        cursor_native(
            case,
            "preToolUse",
            tool_name="Shell",
            tool_use_id="foreign-locator-attempt",
            tool_input={"command": children.reconcile_command(issue_id, token)},
        ),
        time.monotonic() + 10,
    )
    before = case.state()
    with pytest.raises(core.WorkspaceError, match="NOT_OWNER"):
        children.authorize_reconcile(observed, "OTHER-2", token, time.monotonic() + 10)
    unknown = children.call_token("never-admitted-native-call")
    with pytest.raises(core.WorkspaceError, match="UNKNOWN_OPERATION"):
        children.authorize_reconcile(observed, issue_id, unknown, time.monotonic() + 10)
    with pytest.raises(core.WorkspaceError, match="UNKNOWN_OPERATION"):
        children.reconcile(str(case.root), issue_id, unknown, time.monotonic() + 10)
    assert case.state() == before
    assert token in before["participants"][case.base["coordinator"]]["pending"]


@pytest.mark.parametrize("status", ["claimed", "closed"])
def test_retained_raw_child_cannot_fall_through_to_parent(
    cursor_case: Fixture, status: str
) -> None:
    """Preserve legacy identity fences without changing retained state or granting effects.

    Args:
        cursor_case: Disposable current-core repository with a raw-session legacy participant.
        status: Retained status cannot make a known child eligible for parent authority.
    """
    case = cursor_case
    key = core.participant_key({"host": "cursor", "session_id": CHILD})
    # Model the old binding shape through supported core operations in this disposable store.
    case.call(
        "scope",
        expected_revision=case.state()["revision"],
        target_participant=key,
        packet=[],
        owned_paths=["context/legacy.md"],
    )
    joined = case.call(
        "attach",
        session_id=CHILD,
        binding_generation=None,
        expected_revision=case.state()["revision"],
    )
    case.call(
        "acknowledge",
        session_id=CHILD,
        binding_generation=joined["binding_generation"],
        packet_digest=core.sha(core.canonical([])),
    )
    directory = case.root / ".task/.control/cursor-children"
    directory.mkdir(exist_ok=True)
    index = directory / "index.json"
    retained = core.canonical(
        {
            "schema_version": 2,
            "children": {key: {"call_id": children.call_token("legacy-task"), "status": status}},
        }
    )
    index.write_bytes(retained)
    before = case.state()
    # Omitted native child markers must not turn the ready raw binding into a parent Read.
    event = cursor_native(
        case,
        "preToolUse",
        child=True,
        tool_name="Read",
        tool_use_id="legacy-outside-read",
        tool_input={"file_path": "/outside/assigned/packet"},
    )
    decision = cursor.handle(event)
    assert decision["permission"] == "deny"
    assert "HOST_UNSUPPORTED_CHILD_IDENTITY" in decision["user_message"]
    assert case.state() == before
    assert index.read_bytes() == retained
