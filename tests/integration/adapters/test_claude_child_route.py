"""Exercise the Claude Agent-tool subagent route through the real adapter and core.

Events are synthetic shapes from https://code.claude.com/docs/en/hooks (common
``agent_id``/``agent_type`` fields, SubagentStart and SubagentStop). They are not
retained live Desktop payloads, so these tests do not verify native delivery.
"""

import copy
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from nplet.adapters import claude, common, cursor
from nplet.lifecycle import task_workspace as core
from tests.support import Fixture
from tests.types import JsonObject, JsonValue

pytestmark = pytest.mark.integration

PARENT = "coordinator"
AGENT = "agent-1"
CHILD = PARENT + "/agent/" + AGENT


@pytest.fixture
def case(monkeypatch: pytest.MonkeyPatch) -> Iterator[Fixture]:
    """Create a disposable repository whose coordinator is a Claude parent session.

    Args:
        monkeypatch: Removes Cursor markers so the Claude entry point handles events.

    Yields:
        Real-core fixture with the issue created but no packet acknowledged.
    """
    # Exercise Claude's own entry point with a Claude-host coordinator identity.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    fixture = Fixture()
    fixture.setUp()
    fixture.base["host"] = claude.HOST
    fixture.base["coordinator"] = core.participant_key(fixture.base)
    # Release disposable stores even when an assertion fails.
    try:
        fixture.create()
        yield fixture
    finally:
        fixture.doCleanups()


def event(case: Fixture, name: str, child: bool = False, **fields: JsonValue) -> JsonObject:
    """Build one Claude hook envelope for the parent or its subagent.

    Args:
        case: Disposable repository fixture.
        name: Native hook event name.
        child: Whether the hook fires inside the subagent (adds agent_id/agent_type).
        **fields: Event-specific fields and intentional overrides.

    Returns:
        A native envelope with the parent's raw session ID and the real checkout.
    """
    # Every Claude hook carries the parent session; subagent hooks add agent fields.
    envelope: JsonObject = {"hook_event_name": name, "session_id": PARENT, "cwd": str(case.root)}
    if child:
        envelope.update(agent_id=AGENT, agent_type="general-purpose")
    envelope.update(fields)
    return envelope


def agent_call(
    case: Fixture, name: str, inputs: JsonObject | None = None, **fields: JsonValue
) -> JsonObject:
    """Build the parent's Agent tool envelope.

    Args:
        case: Disposable repository fixture.
        name: PreToolUse, PostToolUse or PostToolUseFailure.
        inputs: Agent tool_input overrides.
        **fields: Event fields such as tool_response or error.

    Returns:
        The parent's Agent tool event under the native call ID ``agent-call``.
    """
    tool_input: JsonObject = {"description": "step", "prompt": "fixture", **(inputs or {})}
    return event(
        case, name, tool_name="Agent", tool_use_id="agent-call", tool_input=tool_input, **fields
    )


def read_call(case: Fixture, name: str, tool_id: str, **fields: JsonValue) -> JsonObject:
    """Build a subagent Read tool envelope for the issue roadmap.

    Args:
        case: Disposable repository fixture.
        name: PreToolUse or PostToolUse.
        tool_id: Native tool-use ID.
        **fields: Extra event fields such as tool_response.

    Returns:
        A child Read tool event.
    """
    return event(
        case,
        name,
        child=True,
        tool_name="Read",
        tool_use_id=tool_id,
        tool_input={"file_path": str(case.root / ".task/TEST-1/roadmap.md")},
        **fields,
    )


def edit_call(case: Fixture, name: str, tool_id: str, **fields: JsonValue) -> JsonObject:
    """Build a protected child edit for native settlement assertions.

    Args:
        case: Disposable repository fixture.
        name: PreToolUse or PostToolUse.
        tool_id: Native tool-use identity.
        **fields: Explicit result or child identity overrides.

    Returns:
        A child Write event targeting implementation.
    """
    return event(
        case,
        name,
        child=True,
        tool_name="Write",
        tool_use_id=tool_id,
        tool_input={"file_path": str(case.root / "src/example.py"), "content": "pass\n"},
        **fields,
    )


def denied(response: JsonObject) -> bool:
    """Report whether a PreToolUse response denies the native call.

    Args:
        response: Adapter response.

    Returns:
        Whether Claude's permission decision is a denial.
    """
    return response.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def key(session: str) -> str:
    """Derive the Claude participant key for a lifecycle session.

    Args:
        session: Raw or normalized Claude session ID.

    Returns:
        The SHA-256 participant key.
    """
    return core.participant_key({"host": claude.HOST, "session_id": session})


def bindings(case: Fixture) -> Path:
    """Locate the checkout's local binding directory.

    Args:
        case: Disposable repository fixture.

    Returns:
        The ``.task/.bindings`` path of the main worktree.
    """
    return case.root / ".task" / ".bindings"


def child_request(case: Fixture, operation: str, **fields: JsonValue) -> JsonObject:
    """Build a lifecycle request bound to the child session.

    Args:
        case: Disposable repository fixture.
        operation: Core operation.
        **fields: Operation fields.

    Returns:
        A request carrying the child's session and recorded binding generation.
    """
    # Read the child's current binding generation from committed issue state.
    generation = case.state()["participants"][key(CHILD)]["generation"]
    # Build the request under the child's own session and recorded generation.
    return {
        "schema_version": 1,
        "operation": operation,
        "request_id": str(uuid.uuid4()),
        "worktree": str(case.root),
        "host": claude.HOST,
        "session_id": CHILD,
        "repo_id": case.base["repo_id"],
        "issue_id": "TEST-1",
        "binding_generation": generation,
        **fields,
    }


def start_child(case: Fixture) -> JsonObject:
    """Make the parent ready, admit its Agent call and deliver SubagentStart.

    Args:
        case: Disposable repository fixture.

    Returns:
        The SubagentStart response.

    Raises:
        AssertionError: The parent Agent call is not admitted.
    """
    # A ready parent's foreground Agent call becomes ordinary pending work.
    case.ready()
    assert not denied(claude.handle(agent_call(case, "PreToolUse")))
    return claude.handle(event(case, "SubagentStart", child=True))


def test_ready_parent_agent_child_joins_works_and_stops(case: Fixture) -> None:
    """Run the full child route while the parent stays ready.

    Args:
        case: Disposable repository fixture.

    Raises:
        AssertionError: A route step is refused or the parent loses readiness.
    """
    # The parent's Agent call is pending under its native call ID.
    response = start_child(case)
    state = case.state()
    assert "agent-call" in state["participants"][case.base["coordinator"]]["pending"]
    # SubagentStart returns only fixed text plus the child's key and packet digest.
    context = response["hookSpecificOutput"]["additionalContext"]
    member = state["participants"][key(CHILD)]
    assert response["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert context.startswith("TASK_WORKSPACE_CHILD_READY: participant " + key(CHILD))
    assert member["ack"] in context
    assert "fixture" not in context
    # The adapter records the child's issue assignment for its own bootstrap gate.
    assert (bindings(case) / (key(CHILD) + ".assignment.json")).exists()
    # The child is ready on its own roadmap-only join packet, never the parent's.
    assert member["status"] == "ready"
    assert [ref["locator"] for ref in member["packet"]] == ["roadmap.md"]
    assert member["packet"][0]["reason"] == "issue-resume"
    assert state["coordinator"] == case.base["coordinator"]
    # A child read remains task-free under its own joined identity.
    assert not denied(claude.handle(read_call(case, "PreToolUse", "child-read")))
    assert case.state()["participants"][key(CHILD)]["pending"] == {}
    claude.handle(read_call(case, "PostToolUse", "child-read", tool_response={"ok": True}))
    state = case.state()
    assert state["participants"][key(CHILD)]["pending"] == {}
    assert "child-read" not in state["participants"][case.base["coordinator"]]["pending"]
    # Task-free prose mutations do not inherit this child's managed-note ownership.
    before = case.state()
    note = edit_call(case, "PreToolUse", "child-note")
    note["tool_input"] = {"file_path": str(case.root / "outside.md"), "content": "note"}
    response = claude.handle(note)
    assert denied(response)
    assert "HOST_UNSUPPORTED_TOOL" in str(response)
    assert case.state() == before
    # Protected child work still needs its own pending identity and correlated completion.
    assert not denied(claude.handle(edit_call(case, "PreToolUse", "child-edit")))
    state = case.state()
    assert "child-edit" in state["participants"][key(CHILD)]["pending"]
    assert "child-edit" not in state["participants"][case.base["coordinator"]]["pending"]
    claude.handle(edit_call(case, "PostToolUse", "child-edit", tool_response={"ok": True}))
    assert case.state()["participants"][key(CHILD)]["pending"] == {}
    # SubagentStop is an observation: no detach and no settlement of the parent call.
    stopped = claude.handle(
        event(case, "SubagentStop", child=True, stop_hook_active=False, last_assistant_message="")
    )
    assert stopped == {}
    state = case.state()
    assert state["participants"][key(CHILD)]["status"] == "ready"
    assert "agent-call" in state["participants"][case.base["coordinator"]]["pending"]
    # The parent remained ready throughout and its Agent call settles on PostToolUse.
    assert case.call("ready")["ok"]
    claude.handle(agent_call(case, "PostToolUse", tool_response=[{"type": "text", "text": "x"}]))
    assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}


def test_parent_agent_failure_settles_through_failure_path(case: Fixture) -> None:
    """Settle a failed parent Agent call through PostToolUseFailure.

    Args:
        case: Disposable repository fixture.
    """
    start_child(case)
    claude.handle(agent_call(case, "PostToolUseFailure", error="stopped", is_interrupt=True))
    assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}


def test_unready_parent_child_stays_unbound(case: Fixture) -> None:
    """Leave a child of an unready parent unbound and deny its first tool.

    Args:
        case: Disposable repository fixture.
    """
    # The unready parent's Agent call itself is denied before any pending work.
    before = copy.deepcopy(case.state())
    assert denied(claude.handle(agent_call(case, "PreToolUse")))
    # SubagentStart cannot block: it only advises and joins nobody.
    response = claude.handle(event(case, "SubagentStart", child=True))
    assert set(response) == {"systemMessage"}
    assert "TASK_WORKSPACE_NOT_READY" in response["systemMessage"]
    assert case.state() == before
    # The child's first tool has no binding or assignment of its own and is denied.
    assert denied(claude.handle(read_call(case, "PreToolUse", "child-read")))
    assert case.state() == before
    assert not (bindings(case) / (key(CHILD) + ".assignment.json")).exists()


def test_child_without_pending_agent_call_is_not_joined(case: Fixture) -> None:
    """Refuse a join when the ready parent has no pending admitted Agent call.

    Args:
        case: Disposable repository fixture.
    """
    case.ready()
    before = copy.deepcopy(case.state())
    response = claude.handle(event(case, "SubagentStart", child=True))
    assert "HOST_UNSUPPORTED_CHILD_IDENTITY" in response["systemMessage"]
    assert case.state() == before
    assert denied(claude.handle(read_call(case, "PreToolUse", "child-read")))


def test_settled_agent_call_no_longer_authorizes_a_join(case: Fixture) -> None:
    """Ignore a recorded Agent call ID once the core no longer holds it pending.

    Args:
        case: Disposable repository fixture.
    """
    case.ready()
    claude.handle(agent_call(case, "PreToolUse"))
    claude.handle(agent_call(case, "PostToolUse", tool_response="done"))
    response = claude.handle(event(case, "SubagentStart", child=True))
    assert "HOST_UNSUPPORTED_CHILD_IDENTITY" in response["systemMessage"]
    assert key(CHILD) not in case.state()["participants"]


@pytest.mark.parametrize(
    ("inputs", "code"),
    [
        ({"run_in_background": True}, "HOST_UNSUPPORTED_BACKGROUND"),
        ({"isolation": "worktree"}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
    ],
)
def test_background_or_isolated_agent_is_denied(
    case: Fixture, inputs: JsonObject, code: str
) -> None:
    """Deny background and worktree-isolated Agent calls before pending work.

    Args:
        case: Disposable repository fixture.
        inputs: Agent tool_input fields that make the child unsupported.
        code: Expected diagnostic.
    """
    case.ready()
    before = copy.deepcopy(case.state())
    response = claude.handle(agent_call(case, "PreToolUse", inputs))
    assert denied(response)
    assert code in response["hookSpecificOutput"]["permissionDecisionReason"]
    assert case.state() == before


def test_child_cannot_start_nested_agent(case: Fixture) -> None:
    """Deny an Agent call from a ready child without recording pending work.

    Args:
        case: Disposable repository fixture.
    """
    start_child(case)
    before = copy.deepcopy(case.state())
    nested = event(
        case,
        "PreToolUse",
        child=True,
        tool_name="Agent",
        tool_use_id="nested-call",
        tool_input={"description": "nested", "prompt": "fixture"},
    )
    response = claude.handle(nested)
    assert denied(response)
    assert (
        "HOST_UNSUPPORTED_CHILD_IDENTITY"
        in response["hookSpecificOutput"]["permissionDecisionReason"]
    )
    assert case.state() == before


def test_agent_id_without_parent_binding_is_not_joined(case: Fixture) -> None:
    """Refuse a child whose parent session has no binding in this checkout.

    Args:
        case: Disposable repository fixture.
    """
    case.ready()
    before = copy.deepcopy(case.state())
    response = claude.handle(event(case, "SubagentStart", child=True, session_id="stranger"))
    assert "BINDING_MISSING" in response["systemMessage"]
    assert case.state() == before
    tool = read_call(case, "PreToolUse", "child-read", session_id="stranger")
    assert denied(claude.handle(tool))


def test_agent_id_without_session_id_is_refused(case: Fixture) -> None:
    """Refuse child hooks that lack the parent session.

    Args:
        case: Disposable repository fixture.
    """
    start = event(case, "SubagentStart", child=True)
    del start["session_id"]
    assert "HOST_UNSUPPORTED_CHILD_IDENTITY" in claude.handle(start)["systemMessage"]
    tool = read_call(case, "PreToolUse", "child-read")
    del tool["session_id"]
    assert denied(claude.handle(tool))


def test_child_bash_bootstrap_limits_and_core_authority(case: Fixture) -> None:
    """Admit child read/acknowledge/resume bootstraps but not scope or create.

    Args:
        case: Disposable repository fixture.
    """
    # A coordinator re-scope leaves the child attached and unacknowledged.
    start_child(case)
    packet = case.state()["assignments"][key(CHILD)]["packet"]
    case.call(
        "scope",
        expected_revision=case.state()["revision"],
        target_participant=key(CHILD),
        packet=packet,
    )
    assert case.state()["participants"][key(CHILD)]["ack"] is None

    def bash(request: JsonObject) -> JsonObject:
        """Dispatch a child Bash bootstrap of the given request.

        Args:
            request: Lifecycle request encoded into the canonical command.

        Returns:
            The adapter's PreToolUse response.
        """
        return claude.handle(
            event(
                case,
                "PreToolUse",
                child=True,
                tool_name="Bash",
                tool_use_id="child-bash",
                tool_input={"command": common.bootstrap_command(request, claude.HOST)},
            )
        )

    # Recovery operations bound to the child session pass the exact bootstrap gate.
    for operation, fields in (
        ("read", {}),
        ("acknowledge", {"packet_digest": "0" * 64}),
        ("resume", {}),
    ):
        assert bash(child_request(case, operation, **fields)) == {}
    # Coordinator-only and creation requests are denied for the unready child.
    scope = child_request(
        case,
        "scope",
        expected_revision=case.state()["revision"],
        target_participant=key(CHILD),
        packet=[],
    )
    assert denied(bash(scope))
    create = child_request(case, "create", coordinator=key(CHILD), issue_uuid="fixture-issue-uuid")
    assert denied(bash(create))
    # The child bootstrap cannot impersonate the parent session either.
    assert denied(bash({**child_request(case, "read"), "session_id": PARENT}))
    # After the child re-acknowledges, the core still refuses its scope request.
    read = core.execute(child_request(case, "read"))
    acknowledged = core.execute(
        child_request(
            case,
            "acknowledge",
            expected_revision=read["revision"],
            packet_digest=read["packet_digest"],
        )
    )
    assert acknowledged["ok"]
    refused = core.execute({**scope, "expected_revision": case.state()["revision"]})
    assert not refused["ok"]
    assert refused["code"] == "NOT_OWNER"
    assert case.state()["coordinator"] == case.base["coordinator"]


def provider_call(case: Fixture, operation: str, tool_id: str, **fields: JsonValue) -> JsonObject:
    """Build a child call to the configured named Linear server.

    Args:
        case: Disposable repository fixture.
        operation: Linear connector operation name.
        tool_id: Native tool-use ID.
        **fields: Extra event fields such as tool_response.

    Returns:
        A child MCP tool event with the configured server provenance.
    """
    return event(
        case,
        "PreToolUse" if "tool_response" not in fields else "PostToolUse",
        child=True,
        tool_name="mcp__linear-server__" + operation,
        tool_use_id=tool_id,
        tool_input={"id": "TEST-1"},
        mcp_server={"name": "linear-server", "source": "user"},
        **fields,
    )


def test_child_provider_read_is_admitted_and_settled(case: Fixture) -> None:
    """Admit a ready child's Linear read without adding task pending work.

    Args:
        case: Disposable repository fixture.
    """
    # Join the child, then admit its read without recording task work.
    start_child(case)
    assert not denied(claude.handle(provider_call(case, "get_issue", "child-get")))
    assert case.state()["participants"][key(CHILD)]["pending"] == {}
    # A task-free completion also leaves the child pending map unchanged.
    claude.handle(provider_call(case, "get_issue", "child-get", tool_response=[{"type": "text"}]))
    assert case.state()["participants"][key(CHILD)]["pending"] == {}


@pytest.mark.parametrize("operation", ["save_issue", "save_comment"])
def test_child_provider_writes_are_denied(case: Fixture, operation: str) -> None:
    """Deny Linear writes from a ready child while its parent keeps them.

    Args:
        case: Disposable repository fixture.
        operation: Linear write operation the child attempts.
    """
    # The ready child's write is denied without recording pending work.
    start_child(case)
    before = copy.deepcopy(case.state())
    response = claude.handle(provider_call(case, operation, "child-write"))
    assert denied(response)
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "HOST_UNSUPPORTED_PROVIDER" in reason
    assert case.state() == before
    # The parent's known administrative write is task-free.
    parent = provider_call(case, operation, "parent-write")
    del parent["agent_id"], parent["agent_type"]
    assert not denied(claude.handle(parent))
    assert case.state() == before


@pytest.mark.parametrize("operation", ["get_status", "set_monitor"])
def test_child_app_pr_monitor_is_denied(case: Fixture, operation: str) -> None:
    """Deny the desktop app's PR-monitor tools to a ready child; its parent keeps them.

    Args:
        case: Disposable repository fixture.
        operation: Admitted PR-monitor operation the child attempts.
    """
    # The ready child's call is denied without recording pending work.
    start_child(case)
    before = copy.deepcopy(case.state())
    fields: dict[str, JsonValue] = {
        "tool_name": "mcp__ccd_pr__" + operation,
        "tool_input": {},
        "mcp_server": {"name": "ccd_pr", "source": "sdk"},
    }
    response = claude.handle(
        event(case, "PreToolUse", child=True, tool_use_id="child-pr", **fields)
    )
    assert denied(response)
    assert "HOST_UNSUPPORTED_PROVIDER" in response["hookSpecificOutput"]["permissionDecisionReason"]
    assert case.state() == before
    # The parent status read is task-free; automatic fixes still require tracked work.
    assert not denied(claude.handle(event(case, "PreToolUse", tool_use_id="parent-pr", **fields)))
    assert ("parent-pr" in case.state()["participants"][case.base["coordinator"]]["pending"]) is (
        operation == "set_monitor"
    )


def test_child_handback_and_parent_message_record_no_pending_work(case: Fixture) -> None:
    """Admit the child's hand-back and the parent's message without pending entries.

    Args:
        case: Disposable repository fixture.
    """
    # The ready child's report to its parent is admitted and settles nothing.
    start_child(case)
    before = copy.deepcopy(case.state())
    handback = event(
        case,
        "PreToolUse",
        child=True,
        tool_name="SubagentHandback",
        tool_use_id="child-report",
        tool_input={"message": "heading"},
    )
    assert not denied(claude.handle(handback))
    assert (
        claude.handle({**handback, "hook_event_name": "PostToolUse", "tool_response": "ok"}) == {}
    )
    assert case.state() == before
    # The parent may message its subagent the same way, also without pending work.
    message = event(
        case,
        "PreToolUse",
        tool_name="SendMessage",
        tool_use_id="parent-message",
        tool_input={"to": "agent", "message": "report"},
    )
    assert not denied(claude.handle(message))
    assert case.state() == before
    # A parent cannot impersonate a hand-back; the tool stays unsupported for it.
    parent_handback = {**handback, "tool_use_id": "parent-report"}
    del parent_handback["agent_id"], parent_handback["agent_type"]
    response = claude.handle(parent_handback)
    assert denied(response)
    assert "HOST_UNSUPPORTED_TOOL" in response["hookSpecificOutput"]["permissionDecisionReason"]
    assert case.state() == before


def test_cursor_subagent_start_is_unchanged(case: Fixture) -> None:
    """Keep Cursor's subagentStart denial independent of the Claude child route.

    Args:
        case: Disposable repository fixture.
    """
    response = cursor.handle(
        {
            "hook_event_name": "subagentStart",
            "conversation_id": PARENT,
            "workspace_roots": [str(case.root)],
            "agent_id": AGENT,
        }
    )
    assert response["permission"] == "deny"
    assert "HOST_UNSUPPORTED_CHILD_IDENTITY" in response["user_message"]


def parent_edit(case: Fixture, tool_id: str) -> JsonObject:
    """Build a protected parent edit whose tool-start advances the revision.

    Args:
        case: Disposable repository fixture.
        tool_id: Native tool-use ID of the parent's call.

    Returns:
        A parent Write event for a protected source file.
    """
    return event(
        case,
        "PreToolUse",
        tool_name="Write",
        tool_use_id=tool_id,
        tool_input={"file_path": str(case.root / "src/example.py"), "content": "pass\n"},
    )


def drift_before(
    case: Fixture, monkeypatch: pytest.MonkeyPatch, operation: str
) -> list[JsonObject]:
    """Advance the issue revision once, just before the child's first call of an operation.

    This reproduces a concurrent hook process (the parent's own tool-start) committing
    between the adapter's state read and its revision-bearing child request.

    Args:
        case: Disposable repository fixture.
        monkeypatch: Replaces the shared core dispatcher for the test.
        operation: Core operation whose first call is preceded by the drift.

    Returns:
        The live list of the child's core results for that operation, in call order.
    """
    original = core.execute
    results: list[JsonObject] = []

    def execute(request: JsonObject) -> JsonObject:
        """Dispatch to the real core, committing a parent tool-start before the first child match.

        Args:
            request: Lifecycle request from the adapter.

        Returns:
            The real core result.
        """
        # Only the child's selected operation is observed; everything else passes through.
        if request.get("operation") != operation or request.get("session_id") != CHILD:
            return original(request)
        # The first matching call sees a revision another hook has just advanced.
        if not results:
            assert not denied(claude.handle(parent_edit(case, "parent-drift")))
        result = original(request)
        results.append(result)
        return result

    monkeypatch.setattr(core, "execute", execute)
    return results


def test_child_join_retries_after_revision_drift(
    case: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Join the child on a fresh revision after a concurrent commit conflicts the first join.

    Args:
        case: Disposable repository fixture.
        monkeypatch: Injects the revision drift before the first join.
    """
    # The first join fails on the drifted revision; the retry re-reads and succeeds. A
    # generous budget keeps slow native runners inside the retry window.
    monkeypatch.setattr(common, "CHILD_JOIN_BUDGET_SECONDS", 60.0)
    joins = drift_before(case, monkeypatch, "join")
    response = start_child(case)
    assert [result["code"] for result in joins] == ["REVISION_CONFLICT", "OK"]
    # The child still ends ready on its own packet and the drift call stays the parent's.
    context = response["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("TASK_WORKSPACE_CHILD_READY: participant " + key(CHILD))
    state = case.state()
    assert state["participants"][key(CHILD)]["status"] == "ready"
    assert "parent-drift" in state["participants"][case.base["coordinator"]]["pending"]


def test_child_acknowledge_tolerates_revision_drift(
    case: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acknowledge the child's packet despite a commit between its read and acknowledge.

    The core checks only the packet digest on acknowledge, so real drift succeeds at
    once; a refused first acknowledgment is retried once after a fresh read.

    Args:
        case: Disposable repository fixture.
        monkeypatch: Injects the drift and one refused acknowledgment.
    """
    # A parent commit before the child's acknowledge leaves the digest valid.
    acknowledgments = drift_before(case, monkeypatch, "acknowledge")
    original = core.execute
    refused: list[str] = []

    def execute(request: JsonObject) -> JsonObject:
        """Refuse the child's first acknowledgment with a conflict, then delegate.

        Args:
            request: Lifecycle request from the adapter.

        Returns:
            One conflict refusal, otherwise the drift-injecting dispatcher's result.
        """
        # Only the child's first acknowledgment is refused; later calls pass through.
        if request.get("operation") == "acknowledge" and request["session_id"] == CHILD:
            if not refused:
                refused.append(request["request_id"])
                return {"ok": False, "code": "REVISION_CONFLICT"}
        return original(request)

    monkeypatch.setattr(core, "execute", execute)
    # The adapter reads again, acknowledges the fresh packet and ends ready.
    response = start_child(case)
    assert len(refused) == 1
    assert [result["code"] for result in acknowledgments] == ["OK"]
    assert "TASK_WORKSPACE_CHILD_READY" in response["hookSpecificOutput"]["additionalContext"]
    state = case.state()
    assert state["participants"][key(CHILD)]["status"] == "ready"
    assert "parent-drift" in state["participants"][case.base["coordinator"]]["pending"]


@pytest.mark.parametrize(
    ("code", "budget", "attempts"),
    [
        ("REVISION_CONFLICT", 60.0, common.CHILD_JOIN_ATTEMPTS),
        ("BUSY", 60.0, common.CHILD_JOIN_ATTEMPTS),
        ("REVISION_CONFLICT", 0.0, 1),
        ("ISSUE_MISMATCH", 60.0, 1),
    ],
)
def test_child_join_failure_after_bounded_retries_leaves_child_unbound(
    case: Fixture,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    budget: float,
    attempts: int,
) -> None:
    """Report the join code advisorily when retries, budget or a non-retryable code end it.

    Args:
        case: Disposable repository fixture.
        monkeypatch: Forces every join to fail and removes backoff delays.
        code: Diagnostic every join attempt returns.
        budget: Retry budget in seconds for this case.
        attempts: Expected number of join attempts.
    """
    # Every join fails with the given code; other core calls run for real.
    original = core.execute
    joins: list[str] = []

    def execute(request: JsonObject) -> JsonObject:
        """Refuse every join and pass every other request to the real core.

        Args:
            request: Lifecycle request from the adapter.

        Returns:
            A refusal for joins, otherwise the real core result.
        """
        # Record each join attempt and refuse it; other operations reach the real core.
        if request.get("operation") == "join":
            joins.append(str(request["expected_revision"]))
            return {"ok": False, "code": code}
        return original(request)

    monkeypatch.setattr(core, "execute", execute)
    monkeypatch.setattr(common, "CHILD_JOIN_BUDGET_SECONDS", budget)
    monkeypatch.setattr(common.time, "sleep", lambda seconds: None)
    # SubagentStart cannot block: it advises with the code after the bounded attempts.
    response = start_child(case)
    assert len(joins) == attempts
    assert set(response) == {"systemMessage"}
    assert code in response["systemMessage"]
    # The child holds no participant or binding, so its first tool is denied.
    assert key(CHILD) not in case.state()["participants"]
    tool = claude.handle(read_call(case, "PreToolUse", "child-read"))
    assert denied(tool)
    assert "BINDING_MISSING" in tool["hookSpecificOutput"]["permissionDecisionReason"]


def test_sequential_children_each_end_ready_under_their_own_keys(case: Fixture) -> None:
    """Join two children of one ready parent and settle each child's call under its own key.

    Args:
        case: Disposable repository fixture.
    """
    # The ready parent issues two foreground Agent calls, each pending under its own ID.
    case.ready()
    for call in ("agent-call", "agent-call-2"):
        dispatch = event(
            case,
            "PreToolUse",
            tool_name="Agent",
            tool_use_id=call,
            tool_input={"description": "step", "prompt": "fixture"},
        )
        assert not denied(claude.handle(dispatch))
    # Each SubagentStart joins its own child and makes it ready.
    second = PARENT + "/agent/agent-2"
    for agent in (AGENT, "agent-2"):
        response = claude.handle(event(case, "SubagentStart", child=True, agent_id=agent))
        assert "TASK_WORKSPACE_CHILD_READY" in response["hookSpecificOutput"]["additionalContext"]
    state = case.state()
    assert state["participants"][key(CHILD)]["status"] == "ready"
    assert state["participants"][key(second)]["status"] == "ready"
    # Each child's protected edit settles only under that child's participant.
    for agent, session, tool_id in ((AGENT, CHILD, "edit-1"), ("agent-2", second, "edit-2")):
        assert not denied(claude.handle(edit_call(case, "PreToolUse", tool_id, agent_id=agent)))
        members = case.state()["participants"]
        assert list(members[key(session)]["pending"]) == [tool_id]
        assert tool_id not in members[case.base["coordinator"]]["pending"]
        done = edit_call(case, "PostToolUse", tool_id, agent_id=agent, tool_response={"ok": 1})
        claude.handle(done)
        assert case.state()["participants"][key(session)]["pending"] == {}
