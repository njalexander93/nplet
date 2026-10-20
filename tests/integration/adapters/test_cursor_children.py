"""Exercise real Cursor adapter Store/lock lifetimes with synthetic native evidence only."""

from __future__ import annotations

import json
import shutil
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
def cursor_case(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[Fixture]:
    """Create a real registered disposable Cursor repository with external evidence mocked.

    Args:
        monkeypatch: Replaces only installed source/result reads and native remote marker.
        request: Registers cleanup before disposable repository setup can fail.

    Yields:
        Ready real-core Cursor coordinator.
    """
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    case = Fixture()
    request.addfinalizer(case.doCleanups)
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
    yield case


def cursor_handle(event: dict) -> dict:
    """Exercise the direct handler with an explicit synthetic transaction budget.

    Args:
        event: Synthetic native callback without supervisor metadata in its JSON.

    Returns:
        Actual Cursor adapter response under a widened direct-test deadline.
    """
    return cursor.handle(event, deadline=time.monotonic() + 10)


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
        Synthetic native envelope with no trusted supervisor fields.
    """
    # Trusted direct-test deadlines are passed separately by cursor_handle, never in event JSON.
    event = {
        "hook_event_name": name,
        "conversation_id": CHILD if child else case.base["session_id"],
        "generation_id": generation if child else "parent-generation",
        "workspace_roots": [str(case.root)],
        "cwd": str(case.root),
        "cursor_version": "3.25.9",
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
    result = cursor_handle(
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
    result = cursor_handle(
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
    response = cursor_handle(
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
        cursor_handle(
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
        tool_input={"command": command, "cwd": "", "timeout": 30000},
    )
    decision = cursor_handle(native)
    assert decision["permission"] == "allow", decision
    result = core.execute(request)
    native.update(
        hook_event_name="postToolUse",
        tool_output=json.dumps({"exitCode": 0 if result["ok"] else 3}),
    )
    assert cursor_handle(native) == {}
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
        tool_input={"command": command, "cwd": "", "timeout": 30000},
    )
    decision = cursor_handle(native)
    assert decision["permission"] == "allow", decision
    result = children.reconcile(str(case.root), case.base["issue_id"], token, time.monotonic() + 10)
    native.update(hook_event_name="postToolUse", tool_output=json.dumps({"exitCode": 0}))
    assert cursor_handle(native) == {}
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
        denied = cursor_handle(
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
    response = cursor_handle(
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
    denied = cursor_handle(
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
        assert cursor_handle(native)["permission"] == "allow"
        native.update(hook_event_name="postToolUse", tool_output=output)
        assert cursor_handle(native) == {}
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
    cursor_handle(failure)
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
    assert "STALE_BINDING" in json.dumps(cursor_handle(old_post))
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
            tool_input={"command": command, "cwd": "", "timeout": 30000},
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
@pytest.mark.parametrize("missing_bindings", [False, True])
def test_retained_raw_child_cannot_fall_through_to_parent(
    cursor_case: Fixture, status: str, missing_bindings: bool
) -> None:
    """Preserve legacy identity fences without changing retained state or granting effects.

    Args:
        cursor_case: Disposable current-core repository with a raw-session legacy participant.
        status: Retained status cannot make a known child eligible for parent authority.
        missing_bindings: Remove local bindings while retaining the canonical legacy index.
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
    # Local binding loss must not hide canonical raw-session classification evidence.
    if missing_bindings:
        shutil.rmtree(case.root / ".task" / ".bindings")
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
    decision = cursor_handle(event)
    assert decision["permission"] == "deny"
    assert "HOST_UNSUPPORTED_CHILD_IDENTITY" in decision["user_message"]
    assert case.state() == before
    assert index.read_bytes() == retained
    if missing_bindings:
        assert not (case.root / ".task" / ".bindings").exists()


@pytest.mark.parametrize("missing_mutex", [False, True])
def test_retained_child_without_local_bindings_cannot_become_parent(
    cursor_case: Fixture, missing_mutex: bool
) -> None:
    """Reject retained terminal children even after local classification storage is lost.

    Args:
        cursor_case: Disposable real repository with retained child invocation evidence.
        missing_mutex: Also remove the optional repository classification mutex.
    """
    # Retain a genuine invocation and its terminal child pointer in canonical issue state.
    case = cursor_case
    raw_call = "retained-child-without-bindings"
    planned_task(case, "retained-child", raw_call)
    path = read_pre(case, raw_call, "retained-source")
    read_complete(case, raw_call, "retained-source", path)
    case.require_ok(reconcile_native(case, raw_call))
    bindings = case.root / ".task" / ".bindings"
    shutil.rmtree(bindings)
    mutex = case.root / ".task" / ".control" / "cursor-children"
    if missing_mutex:
        shutil.rmtree(mutex)
    before = case.state()
    # An omitted marker still discovers the retained child and cannot receive parent authority.
    with pytest.raises(core.WorkspaceError) as error:
        children.normalize(
            cursor_native(case, "beforeSubmitPrompt", child=True), time.monotonic() + 10
        )
    assert error.value.code == ("RECOVERY_REQUIRED" if missing_mutex else "STALE_BINDING")
    assert case.state() == before
    assert not bindings.exists()
    assert mutex.exists() is not missing_mutex


def test_parent_classification_does_not_recreate_local_storage(cursor_case: Fixture) -> None:
    """Discover no child using existing issue locks without publishing bindings or a mutex.

    Args:
        cursor_case: Disposable real repository whose parent has no Cursor child invocation.
    """
    # A registered parent with canonical issue state does not need local participant bindings.
    case = cursor_case
    bindings = case.root / ".task" / ".bindings"
    shutil.rmtree(bindings)
    mutex = case.root / ".task" / ".control" / "cursor-children"
    assert not mutex.exists()
    before = case.state()
    observed = children.normalize(
        cursor_native(case, "postToolUse", tool_name="Read", tool_use_id="unbound-read"),
        time.monotonic() + 10,
    )
    assert observed.get("child") is not True
    assert observed["session_id"] == case.base["session_id"]
    assert case.state() == before
    assert not bindings.exists()
    assert not mutex.exists()


def test_rendered_child_templates_preserve_receipts_and_recover_revision(
    cursor_case: Fixture,
) -> None:
    """Execute rendered schemas through the parser, admission and real lifecycle core.

    Args:
        cursor_case: Disposable registered Cursor coordinator with synthetic host callbacks.
    """
    # Bind an actual scoped child and parse each advisory command without rebuilding its schema.
    case = cursor_case
    raw_call = "rendered-guidance-call"
    planned_task(case, "rendered-guidance", raw_call)
    path = read_pre(case, raw_call, "rendered-source")
    event = {
        "child": True,
        "cwd": str(case.root),
        "session_id": case.base["session_id"] + "/agent/" + CHILD,
    }
    guidance = children.child_context(event)
    lines = guidance.splitlines()
    templates = {
        name: common.bootstrap_request(
            lines[lines.index(name + ":") + 1], "cursor", common.PYTHON, common.LIFECYCLE
        )
        for name in ("read", "acknowledge", "ready", "diagnose", "update")
    }

    def execute(request: dict) -> dict:
        """Admit the substituted exact template and execute its real core request.

        Args:
            request: Complete advisory template with explicit current substitutions.

        Returns:
            Actual core result, including expected denials.
        """
        # Reparse canonical shell bytes and validate actual child scope before core execution.
        command = common.bootstrap_command(request, "cursor")
        parsed = common.bootstrap_request(command, "cursor", common.PYTHON, common.LIFECYCLE)
        assert parsed == request
        assert children.child_lifecycle(
            {**event, "tool_name": "Shell", "tool_input": {"command": command}}
        )
        return core.execute(parsed)

    # Missing delivery remains denied; full native source callbacks permit independent ack/ready.
    packet = case.require_ok(execute(templates["read"]))
    ack = {
        **templates["acknowledge"],
        "expected_revision": packet["revision"],
        "packet_digest": packet["packet_digest"],
    }
    assert execute(ack)["code"] == "SOURCE_DELIVERY_REQUIRED"
    read_complete(case, raw_call, "rendered-source", path)
    packet = case.require_ok(execute({**templates["read"], "request_id": str(uuid.uuid4())}))
    ack.update(expected_revision=packet["revision"], request_id=str(uuid.uuid4()))
    acknowledged = case.require_ok(execute(ack))
    ready = case.require_ok(execute(templates["ready"]))
    assert ready["revision"] == acknowledged["revision"]
    child = core.participant_key(event | {"host": "cursor"})
    receipts = case.state()["participants"][child]["cursor_sources"]

    # Interleave another participant's committed event and retry only the intended write.
    sibling = {"host": "cursor", "session_id": "sibling"}
    case.call(
        "scope",
        expected_revision=ready["revision"],
        target_participant=core.participant_key(sibling),
        packet=[],
        owned_paths=["context/sibling.md"],
    )
    attached = case.call("attach", session_id="sibling", binding_generation=None)
    case.call(
        "acknowledge",
        session_id="sibling",
        binding_generation=attached["binding_generation"],
        packet_digest=core.sha(core.canonical([])),
    )
    case.call(
        "update",
        session_id="sibling",
        binding_generation=attached["binding_generation"],
        expected_revision=case.state()["revision"],
        path="context/sibling.md",
        old_digest=None,
        content="Sibling result.",
        provenance={"sources": case.evidence(), "applicability": "test", "status": "verified"},
    )
    source = {key: packet["references"][0][key] for key in ("id", "locator", "sha256")}
    update = {
        **templates["update"],
        "expected_revision": ready["revision"],
        "path": "context/rendered-guidance.md",
        "content": "Verified assigned result.\n",
        "provenance": {**templates["update"]["provenance"], "sources": [source]},
    }
    assert execute(update)["code"] == "REVISION_CONFLICT"
    metadata = case.require_ok(execute(templates["diagnose"]))
    update.update(expected_revision=metadata["revision"], request_id=str(uuid.uuid4()))
    committed = case.require_ok(execute(update))
    assert execute(update) == committed
    assert execute({**update, "content": "Changed uncertain request"})["code"] == "REQUEST_CONFLICT"
    assert case.state()["participants"][child]["cursor_sources"] == receipts
    assert (case.root / ".task/TEST-1/context/rendered-guidance.md").read_text() == update[
        "content"
    ]
    assert case.state()["provenance"][update["path"]]["sources"] == [source]

    # Complete schema guidance does not widen ownership or weaken provenance validation.
    wrong = {
        **update,
        "request_id": str(uuid.uuid4()),
        "expected_revision": committed["revision"],
        "path": "context/foreign.md",
    }
    assert execute(wrong)["code"] == "NOT_OWNER"
    malformed = {
        **update,
        "request_id": str(uuid.uuid4()),
        "expected_revision": committed["revision"],
        "old_digest": core.sha(update["content"].encode()),
        "provenance": {},
    }
    assert execute(malformed)["code"] == "EVIDENCE_REQUIRED"
    assert "A denial does not restart packet/source reads" in guidance
    assert "never pin the startup revision" in guidance


def test_parent_read_survives_more_than_sixty_four_non_cursor_issues(cursor_case: Fixture) -> None:
    """Keep the same ready parent's native Read available beyond the old lifetime cutoff.

    Args:
        cursor_case: Disposable registered ready Cursor coordinator.
    """
    # Establish ordinary parent admission before adding unrelated retained issue histories.
    case = cursor_case
    before = cursor.handle(
        cursor_native(
            case,
            "preToolUse",
            tool_name="Read",
            tool_use_id="before-history",
            tool_input={"file_path": "/ordinary-parent-read"},
        )
    )
    assert before["permission"] == "allow", before
    # Create real non-Cursor issues through the core; do not manufacture control directories.
    for index in range(65):
        session = "non-cursor-history-" + str(index)
        case.call(
            "create",
            host="codex",
            session_id=session,
            issue_id="HISTORY-" + str(index + 1),
            issue_uuid="history-uuid-" + str(index),
            binding_generation=None,
            coordinator=core.participant_key({"host": "codex", "session_id": session}),
        )
    assert len(list((case.root / ".task/.control/issues").iterdir())) > 64
    # The same native parent still passes without creating any Cursor registry.
    after = cursor.handle(
        cursor_native(
            case,
            "preToolUse",
            tool_name="Read",
            tool_use_id="after-history",
            tool_input={"file_path": "/ordinary-parent-read"},
        )
    )
    assert after["permission"] == "allow", after
    assert not (case.root / ".task/.control/cursor-children").exists()


@pytest.mark.parametrize("transition", ["bind", "terminal-only-retire"])
def test_orphan_conversation_route_denies_parent_then_exact_retry_recovers(
    cursor_case: Fixture, monkeypatch: pytest.MonkeyPatch, transition: str
) -> None:
    """Keep pointer-before-commit crashes restrictive and recover the original transition.

    Args:
        cursor_case: Real disposable ready Cursor coordinator.
        monkeypatch: Injects failure at the durable routing boundary only.
        transition: Binding or retirement before any child callback.
    """
    # Admit the genuine native call before interrupting its first conversation publication.
    case = cursor_case
    raw_call = "orphan-route-" + transition
    planned_task(case, "orphan-route", raw_call)
    before = case.state()

    def interrupt(point: str) -> None:
        """Simulate a crash immediately after durable conversation pointer publication.

        Args:
            point: Production persistence failpoint.

        Raises:
            RuntimeError: At the exact conversation reservation boundary.
        """
        if point == "cursor-route:conversation":
            raise RuntimeError("interrupted conversation reservation")

    monkeypatch.setattr(core, "FAILPOINT", interrupt)
    # No issue transition may commit authority when its preceding reservation crashes.
    with pytest.raises(RuntimeError, match="interrupted conversation reservation"):
        if transition == "bind":
            children.normalize(
                cursor_native(case, "beforeSubmitPrompt", child=True, parent_call=raw_call),
                time.monotonic() + 10,
            )
        else:
            children.reconcile(
                str(case.root),
                case.base["issue_id"],
                children.call_token(raw_call),
                time.monotonic() + 10,
            )
    assert case.state() == before
    monkeypatch.setattr(core, "FAILPOINT", None)
    shutil.rmtree(case.root / ".task/.bindings")
    # Missing local bindings and omitted markers must never turn the orphan into a parent.
    with pytest.raises(core.WorkspaceError, match="UNKNOWN_OPERATION"):
        children.normalize(
            cursor_native(case, "beforeSubmitPrompt", child=True), time.monotonic() + 10
        )
    # Retry the exact original transition; the durable selector remains idempotent.
    if transition == "bind":
        result = children.normalize(
            cursor_native(case, "beforeSubmitPrompt", child=True, parent_call=raw_call),
            time.monotonic() + 10,
        )
        assert result["child"] is True
    else:
        # Losing all local bindings also removes the parent's required retirement binding.
        with pytest.raises(core.WorkspaceError, match="BINDING_MISSING"):
            children.reconcile(
                str(case.root),
                case.base["issue_id"],
                children.call_token(raw_call),
                time.monotonic() + 10,
            )
        # Supported attachment restores the existing parent generation without child authority.
        case.call("bind")
        result = children.reconcile(
            str(case.root),
            case.base["issue_id"],
            children.call_token(raw_call),
            time.monotonic() + 10,
        )
        assert result["ok"], result
        with pytest.raises(core.WorkspaceError, match="STALE_BINDING"):
            children.normalize(
                cursor_native(case, "beforeSubmitPrompt", child=True), time.monotonic() + 10
            )


def test_selected_child_recovers_staged_binding_before_classification(
    cursor_case: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recover a genuine binding intent through the exact pointer with no local binding.

    Args:
        cursor_case: Real disposable coordinator and recoverable issue transaction.
        monkeypatch: Injects failure after the genuine issue transaction intent.
    """
    # Leave a durable selected binding intent, rather than hand-writing transaction state.
    case = cursor_case
    raw_call = "staged-selected-binding"
    planned_task(case, "staged-selected", raw_call)

    def interrupt(point: str) -> None:
        """Stop after the real transaction intent is durable.

        Args:
            point: Production persistence boundary.

        Raises:
            RuntimeError: At the durable issue intent boundary.
        """
        if point == "intent":
            raise RuntimeError("interrupted binding intent")

    monkeypatch.setattr(core, "FAILPOINT", interrupt)
    with pytest.raises(RuntimeError, match="interrupted binding intent"):
        children.normalize(
            cursor_native(case, "beforeSubmitPrompt", child=True, parent_call=raw_call),
            time.monotonic() + 10,
        )
    transaction = case.root / ".task/.control/issues" / case.base["issue_id"] / "transaction.json"
    assert transaction.exists()
    monkeypatch.setattr(core, "FAILPOINT", None)
    shutil.rmtree(case.root / ".task/.bindings")
    # Even an omitted-marker callback recovers committed identity before deciding authority.
    result = children.normalize(
        cursor_native(case, "beforeSubmitPrompt", child=True), time.monotonic() + 10
    )
    assert result["child"] is True
    assert not transaction.exists()


@pytest.mark.parametrize("interruption", ["pointer", "inventory-change"])
def test_explicit_modern_population_retries_without_automatic_cutover(
    cursor_case: Fixture, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    """Verify full explicit migration, restrictive interruption and exact recovery.

    Args:
        cursor_case: Disposable retained modern invocation history.
        monkeypatch: Injects a publication crash or concurrent retained issue creation.
        interruption: Boundary that prevents the completion marker.
    """
    # Model a pre-routing modern store by retaining real issue invocations and its mutex.
    case = cursor_case
    raw_call = "modern-population"
    planned_task(case, "modern-population", raw_call)
    case.require_ok(
        children.reconcile(
            str(case.root),
            case.base["issue_id"],
            children.call_token(raw_call),
            time.monotonic() + 10,
        )
    )
    control = case.root / ".task/.control"
    (control / children.routing.MARKER).unlink()
    registry = control / "cursor-children"
    for pointer in [*registry.glob("node-*.json"), registry / children.routing.ROOT]:
        pointer.unlink()
    with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
        children.normalize(
            cursor_native(case, "beforeSubmitPrompt", child=True), time.monotonic() + 10
        )
    with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
        children.populate_routes(str(case.root), [], time.monotonic() + 10)
    # A partial migration must never publish a completion marker.
    if interruption == "pointer":

        def interrupt(point: str) -> None:
            """Crash after one durable migration pointer.

            Args:
                point: Exact production persistence boundary.

            Raises:
                RuntimeError: After the call pointer is persisted.
            """
            if point == "cursor-route:call":
                raise RuntimeError("interrupted population")

        monkeypatch.setattr(core, "FAILPOINT", interrupt)
        with pytest.raises(RuntimeError, match="interrupted population"):
            children.populate_routes(str(case.root), [case.base["issue_id"]], time.monotonic() + 10)
        monkeypatch.setattr(core, "FAILPOINT", None)
    else:
        original = children.issue_records
        changed = False

        def change_inventory(*args: object) -> list:
            """Create one unrelated issue after the selected real recovered read.

            Args:
                *args: Unchanged selected issue lookup arguments.

            Returns:
                Original selected records after the controlled inventory change.
            """
            nonlocal changed
            records = original(*args)
            if not changed:
                changed = True
                session = "concurrent-population-parent"
                case.call(
                    "create",
                    host="codex",
                    session_id=session,
                    issue_id="OTHER-2",
                    issue_uuid="other-uuid",
                    binding_generation=None,
                    coordinator=core.participant_key({"host": "codex", "session_id": session}),
                )
            return records

        monkeypatch.setattr(children, "issue_records", change_inventory)
        with pytest.raises(core.WorkspaceError, match="REVISION_CONFLICT"):
            children.populate_routes(str(case.root), [case.base["issue_id"]], time.monotonic() + 10)
        monkeypatch.setattr(children, "issue_records", original)
    assert not (control / children.routing.MARKER).exists()
    with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
        children.normalize(
            cursor_native(case, "beforeSubmitPrompt", child=True), time.monotonic() + 10
        )
    # The explicit complete inventory can resume over already durable orphan selectors.
    inventory = sorted(item.name for item in (control / "issues").iterdir())
    marker = children.populate_routes(str(case.root), inventory, time.monotonic() + 10)
    assert marker["complete"] is True
    with pytest.raises(core.WorkspaceError, match="STALE_BINDING"):
        children.normalize(
            cursor_native(case, "beforeSubmitPrompt", child=True), time.monotonic() + 10
        )


def test_exact_child_and_parent_ignore_unrelated_corrupt_history(cursor_case: Fixture) -> None:
    """Read only exact routing and selected authoritative state despite unrelated corruption.

    Args:
        cursor_case: Disposable ready coordinator with one admitted native Task.
    """
    # Retain a real route and corrupt only an unrelated issue after admission.
    case = cursor_case
    raw_call = "selected-despite-corrupt-history"
    planned_task(case, "selected-corruption", raw_call)
    unrelated = case.root / ".task/.control/issues/UNRELATED-2"
    unrelated.mkdir()
    (unrelated / "state.json").write_bytes(b'{"corrupt":')
    # Both ordinary parent Read and the genuine selected child continue independently.
    parent = cursor.handle(
        cursor_native(
            case,
            "preToolUse",
            tool_name="Read",
            tool_use_id="parent-with-corrupt-history",
            tool_input={"file_path": "/ordinary-parent-read"},
        )
    )
    assert parent["permission"] == "allow", parent
    child = children.normalize(
        cursor_native(case, "beforeSubmitPrompt", child=True, parent_call=raw_call),
        time.monotonic() + 10,
    )
    assert child["child"] is True


def test_modern_population_preserves_coexisting_legacy_denial_index(cursor_case: Fixture) -> None:
    """Populate modern routes alongside immutable retained legacy raw-child fences.

    Args:
        cursor_case: Disposable real coordinator with modern invocation history.
    """
    # Retain a real modern terminal child and a distinct version-2 raw-child denial record.
    case = cursor_case
    raw_call = "modern-with-legacy"
    planned_task(case, "modern-with-legacy", raw_call)
    case.require_ok(
        children.reconcile(
            str(case.root),
            case.base["issue_id"],
            children.call_token(raw_call),
            time.monotonic() + 10,
        )
    )
    control = case.root / ".task/.control"
    registry = control / "cursor-children"
    (control / children.routing.MARKER).unlink()
    for pointer in [*registry.glob("node-*.json"), registry / children.routing.ROOT]:
        pointer.unlink()
    legacy_child = "22222222-2222-4222-8222-222222222222"
    legacy_key = core.participant_key({"host": "cursor", "session_id": legacy_child})
    original = core.canonical(
        {
            "schema_version": 2,
            "children": {legacy_key: {"status": "closed", "call_id": "retained-legacy-call"}},
        }
    )
    legacy = registry / "index.json"
    legacy.write_bytes(original)
    # Complete modern population must not reinterpret or rewrite any legacy bytes.
    result = children.populate_routes(
        str(case.root), [case.base["issue_id"]], time.monotonic() + 10
    )
    assert result["complete"] is True
    assert legacy.read_bytes() == original
    with pytest.raises(core.WorkspaceError, match="HOST_UNSUPPORTED_CHILD_IDENTITY"):
        children.normalize(
            cursor_native(case, "beforeSubmitPrompt", conversation_id=legacy_child),
            time.monotonic() + 10,
        )
    with pytest.raises(core.WorkspaceError, match="STALE_BINDING"):
        children.normalize(
            cursor_native(case, "beforeSubmitPrompt", child=True), time.monotonic() + 10
        )
    # An ordinary modern parent stays available without changing retained legacy controls.
    parent = children.normalize(cursor_native(case, "beforeSubmitPrompt"), time.monotonic() + 10)
    assert parent.get("child") is not True
    assert legacy.read_bytes() == original


def test_deleted_referenced_child_node_cannot_restore_parent_authority(
    cursor_case: Fixture,
) -> None:
    """Deny omitted-marker fallback when a referenced node disappears after completion.

    Args:
        cursor_case: Disposable genuine bound child and complete registry.
    """
    # Bind through the actual adapter before deleting only its selected referenced node.
    case = cursor_case
    raw_call = "deleted-selected-node"
    planned_task(case, "deleted-selected-node", raw_call)
    children.normalize(
        cursor_native(case, "beforeSubmitPrompt", child=True, parent_call=raw_call),
        time.monotonic() + 10,
    )
    registry = case.root / ".task/.control/cursor-children"
    digest = core.sha(core.canonical(["conversation", CHILD]))
    selected = registry / ("node-" + digest + ".json")
    selected.unlink()
    shutil.rmtree(case.root / ".task/.bindings")
    before = case.state()
    # The marker stays intact; a missing referenced node is uncertainty, never parent permission.
    decision = cursor.handle(
        cursor_native(
            case,
            "preToolUse",
            child=True,
            tool_name="Read",
            tool_use_id="deleted-node-read",
            tool_input={"file_path": "/outside-assigned-packet"},
        )
    )
    assert decision["permission"] == "deny", decision
    assert "RECOVERY_REQUIRED" in decision["user_message"]
    assert not selected.exists()
    assert case.state() == before


def test_interrupted_root_initialization_needs_explicit_complete_recovery(
    cursor_case: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep partial initialization restrictive until explicit verified recovery.

    Args:
        cursor_case: Disposable coordinator without child routing.
        monkeypatch: Interrupts mandatory root publication.
    """
    case = cursor_case
    configured = children.registration(str(case.root))

    def interrupt(point: str) -> None:
        """Stop during required file initialization before any completion marker.

        Args:
            point: Production persistence failpoint.

        Raises:
            RuntimeError: After the durable mandatory root.
        """
        if point == "cursor-radix-after:root":
            raise RuntimeError("interrupted root initialization")

    monkeypatch.setattr(core, "FAILPOINT", interrupt)
    with (
        core.Store(configured, create=False, bindings=False) as store,
        store.control.child("cursor-children", True) as registry,
        registry.lock(),
    ):
        with pytest.raises(RuntimeError, match="interrupted root initialization"):
            children.routing.initialize(store, registry, False)
    monkeypatch.setattr(core, "FAILPOINT", None)
    control = case.root / ".task/.control"
    assert not (control / children.routing.MARKER).exists()
    with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
        children.normalize(cursor_native(case, "beforeSubmitPrompt"), time.monotonic() + 10)
    # Only complete explicit recovery fills partial markerless initialization.
    result = children.populate_routes(
        str(case.root), [case.base["issue_id"]], time.monotonic() + 10
    )
    assert result["complete"] is True
    assert (control / "cursor-children" / children.routing.ROOT).exists()
