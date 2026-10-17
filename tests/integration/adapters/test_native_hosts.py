"""Exercise native host wire contracts in disposable stores, not trusted live host sessions.

Payload references: https://code.claude.com/docs/en/hooks and
https://prod.cursor.com/docs/hooks. Inputs use each host's documented field names.
"""

import copy
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, cast

import pytest

from nplet.adapters import claude, codex, common, cursor, startup
from nplet.lifecycle import task_workspace as core
from tests.platform_support import link_directory, shell_command, unlink_directory
from tests.support import ROOT, Fixture
from tests.types import JsonObject, JsonValue

pytestmark = pytest.mark.integration

type NativeHost = Literal["claude", "cursor"]
type NativeCase = tuple[NativeHost, Fixture]


@pytest.fixture(params=["claude", "cursor"])
def native(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[NativeCase]:
    """Create a disposable repository with a host-specific coordinator identity.

    Args:
        request: Pytest-selected native host name.
        monkeypatch: Scoped environment cleanup for host identity determinism.

    Yields:
        Native host and shared real-core fixture with cleanup registered.
    """
    # Remove imported-hook markers so each native fixture exercises its own entry point.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    # Allocate independent Git state and select the native host before issue creation.
    case = Fixture()
    host = cast(NativeHost, request.param)
    case.setUp()
    case.base["host"] = {"claude": claude.HOST, "cursor": cursor.HOST}[host]
    case.base["coordinator"] = core.participant_key(case.base)
    # Always release disposable stores after assertions or setup-dependent failures.
    try:
        yield host, case
    finally:
        # Release the disposable repository even when an assertion fails.
        case.doCleanups()


def native_event(host: NativeHost, case: Fixture, name: str, **fields: JsonValue) -> JsonObject:
    """Build lifecycle work envelopes without Codex-only shell/login parameters.

    Args:
        host: Native wire protocol to use.
        case: Disposable repository and session identity.
        name: Claude-style event name translated to Cursor's lower-camel event spelling.
        **fields: Event-specific overrides, including intentionally malformed test values.

    Returns:
        Native envelope with stable session identity and real disposable cwd.
    """
    # Default to task-scoped work so lifecycle tests exercise readiness and settlement.
    event: JsonObject = {
        "hook_event_name": name,
        "cwd": str(case.root),
        "tool_use_id": "actual-tool",
        "tool_name": "Write" if host == "claude" else "Shell",
        "tool_input": (
            {"file_path": str(case.root / "src/example.py"), "content": "pass\n"}
            if host == "claude"
            else {"command": "true"}
        ),
    }
    # Use Claude's session identifier and native Bash arguments.
    if host == "claude":
        event["session_id"] = "coordinator"
    # Use Cursor's conversation identity and workspace roots.
    else:
        # Cursor supplies conversation identity and one workspace root.
        event.update(
            hook_event_name=(
                "beforeSubmitPrompt" if name == "UserPromptSubmit" else name[0].lower() + name[1:]
            ),
            conversation_id="coordinator",
            generation_id="generation-1",
            workspace_roots=[str(case.root)],
            cursor_version="fixture",
        )
    # Apply explicit mutations after building the ordinary native envelope.
    event.update(fields)
    return event


def dispatch(host: NativeHost, event: JsonObject) -> JsonObject:
    """Call the selected real native adapter without replacing lifecycle behavior.

    Args:
        host: Native adapter identity.
        event: Native host event to translate.

    Returns:
        The adapter's native decision or observation response.
    """
    return {"claude": claude, "cursor": cursor}[host].handle(event)


def assert_decision(host: NativeHost, response: JsonObject, allowed: bool) -> None:
    """Check native denial while preserving Claude's ordinary permission flow.

    Args:
        host: Native response protocol.
        response: Actual adapter response.
        allowed: Whether lifecycle readiness or bounded bootstrap should permit continuation.

    Raises:
        AssertionError: Native decision disagrees with the expected gate result.
    """
    # Cursor expresses permission directly in its native result.
    if host == "cursor":
        assert response["permission"] == ("allow" if allowed else "deny")
    # Claude must not override ordinary host permissions on a successful lifecycle check.
    elif allowed:
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") is None
    # Claude expresses blocked tool use through its event-specific permission decision.
    else:
        # Claude denies through its hook-specific permission decision.
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def ticket_callbacks(
    host: NativeHost, case: Fixture, response: JsonObject, tool_id: str = "ticket-read"
) -> tuple[list[JsonObject], JsonObject]:
    """Build the documented native Linear callback pair for one exact issue read.

    Args:
        host: Native callback protocol.
        case: Disposable repository and session fixture.
        response: Explicit result fixture; its provider provenance remains unverified.
        tool_id: Claude's native call ID for correlation.

    Returns:
        The pre-call envelopes and correlated completed-call envelope.
    """
    # Select Claude's MCP-qualified name and object arguments.
    if host == "claude":
        fields: JsonObject = {
            "tool_name": "mcp__linear-server__get_issue",
            "tool_input": {"id": "TEST-1"},
            "tool_use_id": tool_id,
            "mcp_server": {"name": "linear-server", "source": "user"},
        }
        return [native_event(host, case, "PreToolUse", **fields)], native_event(
            host, case, "PostToolUse", tool_response=response, **fields
        )
    # Select Cursor's MCP-specific callback with server identity and JSON strings.
    fields = {
        "tool_name": "get_issue",
        "tool_input": json.dumps({"id": "TEST-1"}),
        "mcp_server_name": "linear",
        "mcp_server_url": "https://mcp.linear.app/mcp",
    }
    generic = {
        "tool_name": "MCP:get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": tool_id,
    }
    return [
        native_event(host, case, "preToolUse", **generic),
        native_event(host, case, "beforeMCPExecution", **fields),
    ], native_event(host, case, "postToolUse", tool_output=json.dumps(response), **generic)


def test_native_ticket_first_start_uses_host_specific_provider_callbacks(
    native: NativeCase,
) -> None:
    """Start a fresh issue from each documented native callback shape.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Ticket ordering or host-bound readiness fails.
    """
    # Record one Task line without creating the issue workspace first.
    host, case = native
    prompt = dispatch(host, native_event(host, case, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "block" not in str(prompt)
    assert not (case.root / ".task/TEST-1").exists()
    # Deny ordinary tools before the requested provider read.
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), False)
    issue = {
        "id": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977",
        "identifier": "TEST-1",
        "title": "Test issue",
    }
    # Invoke the host-specific pre/post pair with separate response fields.
    before, after = ticket_callbacks(host, case, issue)
    # Deliver both Cursor prehook facts before the generic completion.
    for callback in before:
        assert_decision(host, dispatch(host, callback), True)
    assert not (case.root / ".task/TEST-1").exists()
    completed = dispatch(host, after)
    assert "TASK_WORKSPACE_READY" in str(completed)
    # The real lifecycle must verify readiness for this exact host and session.
    observed = common.native_identity(native_event(host, case, "SessionStart"), case.base["host"])
    base = common.request_for(observed, "ready", case.base["host"])
    assert core.execute(base)["ok"] is True


@pytest.mark.parametrize(
    "error_code,expected",
    [("NOT_FOUND", "ISSUE_NOT_FOUND"), ("NETWORK_ERROR", "PROVIDER_NETWORK_ERROR")],
)
def test_native_ticket_failure_retains_task_and_allows_exact_retry(
    native: NativeCase, error_code: str, expected: str
) -> None:
    """Distinguish confirmed absence from provider outage without creating state.

    Args:
        native: Host-specific disposable repository fixture.
        error_code: Typed provider result fixture.
        expected: Required adapter diagnostic.

    Raises:
        AssertionError: Failure is misclassified or retry state is lost.
    """
    # Submit the Task and complete one typed provider failure.
    host, case = native
    dispatch(host, native_event(host, case, "UserPromptSubmit", prompt="Task: TEST-1"))
    before, after = ticket_callbacks(host, case, {"isError": True, "code": error_code})
    # Exercise the initial callback order for this read attempt.
    for callback in before:
        assert_decision(host, dispatch(host, callback), True)
    assert expected in str(dispatch(host, after))
    assert not (case.root / ".task/TEST-1").exists()
    # Retry the same issue with a fresh Claude ID or Cursor's exact MCP callback.
    retry_before, _ = ticket_callbacks(
        host, case, {"isError": True, "code": error_code}, "retry-read"
    )
    # A fresh retry must collect both prehook facts again.
    for callback in retry_before:
        assert_decision(host, dispatch(host, callback), True)


def test_cursor_ticket_callbacks_require_both_pre_events_and_native_call_id(
    native: NativeCase,
) -> None:
    """Reject unmatched and stale Cursor completion despite reordered pre-hooks.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Missing server validation or call correlation reaches readiness.
    """
    # The Cursor-specific state machine does not apply to Claude's single pre-hook.
    host, case = native
    # Claude has one prehook; Cursor requires a server-specific prehook too.
    if host != "cursor":
        return
    dispatch(host, native_event(host, case, "UserPromptSubmit", prompt="Task: TEST-1"))
    issue = {"id": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977", "identifier": "TEST-1"}
    before, after = ticket_callbacks(host, case, issue)
    # Admit the server-specific callback first, then reject completion without call ID.
    assert_decision(host, dispatch(host, before[1]), True)
    marker = case.root / ".task/.bindings" / (case.base["coordinator"] + ".lookup.json")
    reserved = marker.read_bytes()
    assert dispatch(host, after) == {}
    assert marker.read_bytes() == reserved
    assert not (case.root / ".task/TEST-1").exists()
    # Correlate generic pre with the same single call and reject overlapping calls.
    assert_decision(host, dispatch(host, before[0]), True)
    competing = {**before[0], "tool_use_id": "other-read"}
    assert_decision(host, dispatch(host, competing), False)
    assert "TASK_WORKSPACE_READY" in str(dispatch(host, after))


def test_cursor_failed_local_setup_retries_with_new_call_id(
    native: NativeCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep Task identity and reject stale completion after local setup failure.

    Args:
        native: Host-specific disposable repository fixture.
        monkeypatch: Pytest fixture isolating one lifecycle failure.

    Raises:
        AssertionError: A stale callback completes or fresh retry cannot become ready.
    """
    # Select the Cursor case and inject one scope failure after provider verification.
    host, case = native
    # Claude has one prehook; Cursor requires both native prehook facts.
    if host != "cursor":
        return
    dispatch(host, native_event(host, case, "UserPromptSubmit", prompt="Task: TEST-1"))
    issue = {"id": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977", "identifier": "TEST-1"}
    original = startup._call
    failed_once = False

    def fail_once(request: JsonObject) -> JsonObject:
        """Fail the first scope operation after the ticket result is verified.

        Args:
            request: Lifecycle request dispatched by startup.

        Returns:
            The real lifecycle response after the injected failure.

        Raises:
            core.WorkspaceError: Once at the scope boundary.
        """
        nonlocal failed_once
        # Simulate a bounded local failure and let later calls use the real core.
        if request["operation"] == "scope" and not failed_once:
            failed_once = True
            raise core.WorkspaceError("BUSY")
        return original(request)

    monkeypatch.setattr(startup, "_call", fail_once)
    # Complete the first provider call and observe the local failure.
    before, after = ticket_callbacks(host, case, issue, "first-read")
    # Deliver each required prehook before the provider failure.
    for callback in before:
        assert_decision(host, dispatch(host, callback), True)
    assert "BUSY" in str(dispatch(host, after))
    # Begin a fresh call with server-specific callback first; old completion is stale.
    retry_before, retry_after = ticket_callbacks(host, case, issue, "second-read")
    assert_decision(host, dispatch(host, retry_before[1]), True)
    marker = case.root / ".task/.bindings" / (case.base["coordinator"] + ".lookup.json")
    reserved = marker.read_bytes()
    assert dispatch(host, after) == {}
    assert marker.read_bytes() == reserved
    assert_decision(host, dispatch(host, retry_before[0]), True)
    assert "TASK_WORKSPACE_READY" in str(dispatch(host, retry_after))


def test_native_admission_records_actual_tool_before_continuation(native: NativeCase) -> None:
    """Require a ready native participant and persist its actual tool identity.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Admission fails, skips pending state or aliases another host.
    """
    # Establish the native participant's explicit scope and acknowledgment.
    host, case = native
    case.create()
    case.ready()
    # Admit using native fields and inspect the real committed participant state.
    response = dispatch(host, native_event(host, case, "PreToolUse"))
    assert_decision(host, response, True)
    state = case.state()
    assert "actual-tool" in state["participants"][case.base["coordinator"]]["pending"]
    # Identical session text must never collide across any of the three hosts.
    keys = {
        core.participant_key({**case.base, "host": name})
        for name in ("codex", claude.HOST, cursor.HOST)
    }
    assert len(keys) == 3


@pytest.mark.parametrize("native", ["claude"], indirect=True)
@pytest.mark.parametrize("event_name", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("acknowledged", [False, True])
def test_claude_compaction_preserves_authority_and_pending_work(
    native: NativeCase, event_name: str, acknowledged: bool
) -> None:
    """Keep compaction advisory and use supported events for context and tool gating.

    Args:
        native: Claude-specific disposable repository fixture.
        event_name: Documented compaction event to observe.
        acknowledged: Whether the assigned packet remains acknowledged at compaction.

    Raises:
        AssertionError: Compaction settles work, grants authority or claims injected context.
    """
    # Admit real work before optionally invalidating its participant's acknowledgment.
    host, case = native
    case.create()
    case.ready()
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), True)
    # Only acknowledged packets grant ordinary tool readiness.
    if not acknowledged:
        case.call(
            "scope",
            expected_revision=case.state()["revision"],
            target_participant=case.base["coordinator"],
            packet=[],
        )
    before = case.state()["participants"][case.base["coordinator"]]
    assert "actual-tool" in before["pending"]
    assert bool(before["ack"]) is acknowledged
    # Use compaction wire fields without a tool result or completion identifier.
    event: JsonObject = {
        "hook_event_name": event_name,
        "session_id": "coordinator",
        "cwd": str(case.root),
        "trigger": "manual",
    }
    # Compaction cannot itself establish readiness.
    if event_name == "PreCompact":
        event["custom_instructions"] = "Retain the task's outstanding work."
    else:
        # Other compaction hooks receive only advisory context.
        event["compact_summary"] = "The task still has outstanding work."
    assert dispatch(host, event) == {}
    # An observation must preserve both pending evidence and existing authority.
    state = case.state()
    after = state["participants"][case.base["coordinator"]]
    assert after == before
    assert state["disposition"] == "active"
    # SessionStart is the supported context route after compaction.
    context = dispatch(host, {**event, "hook_event_name": "SessionStart", "source": "compact"})
    output = context["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    assert "permissionDecision" not in output
    # Acknowledged sessions can admit supported work.
    if acknowledged:
        assert "Task binding checked" in output["additionalContext"]
    else:
        # Unacknowledged sessions must receive a recovery diagnostic.
        assert "TASK_WORKSPACE_NOT_READY" in output["additionalContext"]
        assert "acknowledge" in output["additionalContext"]
    # Later tool admission must still enforce the actual acknowledgment state.
    response = dispatch(host, native_event(host, case, "PreToolUse", tool_use_id="later-tool"))
    assert_decision(host, response, acknowledged)
    pending = case.state()["participants"][case.base["coordinator"]]["pending"]
    assert pending["actual-tool"] == before["pending"]["actual-tool"]
    assert ("later-tool" in pending) is acknowledged


@pytest.mark.parametrize("binding", ["missing", "malformed", "other_host"])
def test_native_denies_unusable_binding(native: NativeCase, binding: str) -> None:
    """Reject missing, corrupt or foreign-host authority before recording tool work.

    Args:
        native: Host-specific disposable repository fixture.
        binding: Binding failure to construct locally.

    Raises:
        AssertionError: Native pre-tool admission bypasses the binding boundary.
    """
    # Leave the native participant unbound, or create a corrupt binding at its exact key.
    host, case = native
    # Malformed binding variants must fail before native tool use.
    if binding == "malformed":
        case.create()
        case.ready()
        path = case.root / ".task/.bindings" / (str(case.base["coordinator"]) + ".json")
        path.write_text("{")
    # Establish only the Codex participant with the same session text.
    elif binding == "other_host":
        case.base["host"] = "codex"
        case.base["coordinator"] = core.participant_key(case.base)
        case.create()
        case.ready()
    # Require native denial without synthesizing root or foreign-host permissions.
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), False)


def test_native_bootstrap_waits_for_ticket_even_with_exact_command(native: NativeCase) -> None:
    """Keep an exact lifecycle command behind the pending ticket-first gate.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: A pre-ticket command or altered request is admitted.
    """
    # Create an unready participant and construct the exact four-argument recovery command.
    host, case = native
    case.create()
    # Record the explicit native task assignment required by the recovery command gate.
    dispatch(host, native_event(host, case, "UserPromptSubmit", prompt="Task: TEST-1"))
    request = case.req("read", coordinator=None)
    command = common.bootstrap_command(
        request, {"claude": claude.HOST, "cursor": cursor.HOST}[host]
    )
    event = native_event(
        host,
        case,
        "PreToolUse",
        tool_name="Bash" if host == "claude" else "Shell",
        tool_input={"command": command},
    )
    assert_decision(host, dispatch(host, event), False)
    # Reject shell composition and alternative executable spellings before readiness.
    for altered in (
        "env " + command,
        command + "; true",
        command + " && true",
        command + " > output",
        command.replace(codex.PYTHON, "python3", 1),
    ):
        changed = {**event, "tool_input": {"command": altered}}
        assert_decision(host, dispatch(host, changed), False)
    # Reject otherwise canonical commands that change any assigned identity boundary.
    for field, value in (
        ("host", "codex"),
        ("session_id", "other-session"),
        ("worktree", str(case.other)),
        ("issue_id", "OTHER-2"),
    ):
        changed_request = {**request, field: value}
        changed_command = common.bootstrap_command(
            changed_request, {"claude": claude.HOST, "cursor": cursor.HOST}[host]
        )
        assert_decision(
            host, dispatch(host, {**event, "tool_input": {"command": changed_command}}), False
        )


def test_native_uncertain_completion_and_stop_retain_pending_work(native: NativeCase) -> None:
    """Keep uncertainty and lifecycle-end observations distinct from tool settlement.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: An ambiguous response or end event settles pending work or the issue.
    """
    # Admit a real native tool whose eventual completion remains unknown.
    host, case = native
    case.create()
    case.ready()
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), True)
    # Feed raw text without typed completion evidence through the native result field.
    field = "tool_response" if host == "claude" else "tool_output"
    dispatch(
        host,
        native_event(
            host, case, "PostToolUse", **{field: None if host == "claude" else "still running"}
        ),
    )
    # Stop and session-end events must retain the pending operation and active disposition.
    for name in ("Stop", "SessionEnd"):
        dispatch(host, native_event(host, case, name))
        state = case.state()
        assert state["disposition"] == "active"
        assert "actual-tool" in state["participants"][case.base["coordinator"]]["pending"]


def test_native_wrong_tool_completion_cannot_settle_original(native: NativeCase) -> None:
    """Match completion to the admitted tool rather than clearing unrelated pending work.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Foreign tool identity retires the admitted original operation.
    """
    # Admit the original tool and construct a native failure for another tool identity.
    host, case = native
    case.create()
    case.ready()
    inputs: JsonObject = {"file_path": str(case.root / "src/example.py"), "content": "pass\n"}
    dispatch(host, native_event(host, case, "PreToolUse", tool_name="Write", tool_input=inputs))
    failure = native_event(
        host,
        case,
        "PostToolUseFailure",
        tool_use_id="unrelated-tool",
        is_interrupt=False,
        tool_name="Write",
        tool_input=inputs,
    )
    # Use each host's documented failure-description field.
    failure["error" if host == "claude" else "error_message"] = "fixture failure"
    # Cursor success uses a serialized generic tool output.
    if host == "cursor":
        failure["failure_type"] = "error"
    dispatch(host, failure)
    # The unrelated failure must leave the original pending work intact.
    assert "actual-tool" in case.state()["participants"][case.base["coordinator"]]["pending"]
    # A matching terminal file-tool failure may settle only the actual admitted operation.
    failure["tool_use_id"] = "actual-tool"
    dispatch(host, failure)
    assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}


def test_native_child_without_binding_cannot_inherit_root(native: NativeCase) -> None:
    """Reject a child tool call even when its root participant is ready.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: An unbound child inherits the coordinator's tool authority.
    """
    # Make root permissions usable so the child identity is the only changed boundary.
    host, case = native
    case.create()
    case.ready()
    event = native_event(host, case, "PreToolUse")
    event["agent_id" if host == "claude" else "subagent_id"] = "unbound-child"
    # Require denial and no mutation of root pending operations.
    before = copy.deepcopy(case.state())
    assert_decision(host, dispatch(host, event), False)
    assert case.state() == before


@pytest.mark.parametrize(
    "response",
    [
        "not JSON",
        '"plain text"',
        '{"exitCode":null}',
        '{"exitCode":true}',
        '{"exitCode":0,"backgroundTaskId":"pending"}',
    ],
)
def test_cursor_ambiguous_result_strings_retain_work(response: str) -> None:
    """Require typed completion inside Cursor's JSON-stringified result payload.

    Args:
        response: Raw or JSON-encoded text without a valid terminal result.

    Raises:
        AssertionError: Ambiguous string data retires an admitted operation.
    """
    # Create a Cursor-owned ready issue independently of parameterized Claude fixtures.
    case = Fixture()
    case.setUp()
    case.base["host"] = "cursor"
    case.base["coordinator"] = core.participant_key(case.base)
    # Contain a late provider exception as a native denial.
    try:
        # Admit the actual native Shell operation before delivering ambiguous result data.
        case.create()
        case.ready()
        dispatch("cursor", native_event("cursor", case, "PreToolUse"))
        dispatch("cursor", native_event("cursor", case, "PostToolUse", tool_output=response))
        assert "actual-tool" in case.state()["participants"][case.base["coordinator"]]["pending"]
    # Release all disposable repositories after the uncertainty assertion.
    finally:
        # Release the disposable repository even when an assertion fails.
        case.doCleanups()


def test_native_invalid_cwd_cannot_borrow_ready_workspace(native: NativeCase) -> None:
    """Reject an ambiguous cwd value rather than falling back to ready workspace authority.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Invalid cwd permits tool admission under another location.
    """
    # Establish a ready participant so cwd is the only failing authority boundary.
    host, case = native
    case.create()
    case.ready()
    # Replace the native cwd with a malformed value without altering workspace roots.
    event = native_event(host, case, "PreToolUse", cwd=[str(case.root), str(case.other)])
    assert_decision(host, dispatch(host, event), False)


def test_cursor_ambiguous_workspace_roots_deny() -> None:
    """Reject multiple workspace roots when no single native authority is established.

    Raises:
        AssertionError: Multi-root ambiguity permits tool admission.
    """
    # Build two real worktrees and create readiness in only the first one.
    case = Fixture()
    case.setUp()
    case.base["host"] = "cursor"
    case.base["coordinator"] = core.participant_key(case.base)
    # Contain an unexpected bootstrap failure as a native denial.
    try:
        case.create()
        case.ready()
        # Remove cwd so workspace roots alone cannot identify one safe execution root.
        event = native_event("cursor", case, "PreToolUse")
        event.pop("cwd")
        event["workspace_roots"] = [str(case.root), str(case.other)]
        assert_decision("cursor", dispatch("cursor", event), False)
    # Remove all fixture state regardless of the decision result.
    finally:
        # Release the disposable repository even when an assertion fails.
        case.doCleanups()


def test_native_synchronous_completion_settles_only_admitted_work(native: NativeCase) -> None:
    """Accept explicit terminal results without changing the active issue disposition.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Valid completion leaves matching work pending or completes the issue.
    """
    # Admit a supported native operation using real readiness and pending state.
    host, case = native
    case.create()
    case.ready()
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), True)
    # Use the documented native result representation for each host.
    fields: JsonObject = {"tool_response": {"content": "fixture file content"}}
    # Cursor needs both generic and MCP-specific admission.
    if host == "cursor":
        fields = {"tool_output": json.dumps({"exitCode": 0, "stdout": "finished"})}
    dispatch(host, native_event(host, case, "PostToolUse", **fields))
    # Tool completion retires only pending work, not the issue itself.
    state = case.state()
    assert state["participants"][case.base["coordinator"]]["pending"] == {}
    assert state["disposition"] == "active"


@pytest.mark.parametrize("tool", ["mcp__fixture__read", "Task", "Agent"])
def test_native_unsupported_provider_or_child_tool_denies(native: NativeCase, tool: str) -> None:
    """Keep provider and child execution outside this adapter's claimed synchronous scope.

    Args:
        native: Host-specific disposable repository fixture.
        tool: Unsupported provider or child-launch tool name.

    Raises:
        AssertionError: Unsupported work inherits root permissions or creates pending state.
    """
    # Make the coordinator ready before changing only the tool being requested.
    host, case = native
    case.create()
    case.ready()
    before = case.state()
    # Claude admits a foreground Agent call (see test_claude_child_route), so its
    # worktree-isolated form is the unsupported child launch exercised here.
    inputs: JsonObject = (
        {"prompt": "fixture", "isolation": "worktree"}
        if host == "claude" and tool == "Agent"
        else {}
    )
    # Missing child identity must not be synthesized from the ready root session.
    result = dispatch(
        host, native_event(host, case, "PreToolUse", tool_name=tool, tool_input=inputs)
    )
    assert_decision(host, result, False)
    assert case.state() == before


def test_claude_ordinary_shell_requires_foreground_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deny ordinary Bash without the explicit foreground-only host configuration.

    Args:
        monkeypatch: Scoped environment override removing the required foreground setting.

    Raises:
        AssertionError: Readiness grants an unsupported asynchronous shell path.
    """
    # Remove foreground guarantees before creating the real ready Claude participant.
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", raising=False)
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    case = Fixture()
    case.setUp()
    case.base["host"] = claude.HOST
    case.base["coordinator"] = core.participant_key(case.base)
    # A malformed callback must not crash the hook process.
    try:
        case.create()
        case.ready()
        # Native Bash inputs must be refused without invented Codex login/shell flags.
        result = claude.handle(
            native_event(
                "claude", case, "PreToolUse", tool_name="Bash", tool_input={"command": "true"}
            )
        )
        assert_decision("claude", result, False)
        assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}
    # Release the temporary repository after either success or assertion failure.
    finally:
        # Release the disposable repository even when an assertion fails.
        case.doCleanups()


def test_cursor_imported_claude_hook_does_not_duplicate_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip imported Claude hooks while the native Cursor hook records exactly one operation.

    Args:
        monkeypatch: Scoped environment override matching Cursor's imported-hook marker.

    Raises:
        AssertionError: The imported hook mutates state or duplicates native pending work.
    """
    # Create a ready Cursor issue and snapshot state before the imported hook runs.
    case = Fixture()
    case.setUp()
    case.base["host"] = cursor.HOST
    case.base["coordinator"] = core.participant_key(case.base)
    # A second malformed callback must remain bounded.
    try:
        case.create()
        case.ready()
        before = case.state()
        monkeypatch.setenv("CURSOR_VERSION", "fixture")
        # Invoke the imported Claude translator and require a strict no-op.
        assert claude.handle(native_event("claude", case, "PreToolUse")) == {}
        assert case.state() == before
        # The native Cursor translator alone records the actual operation once.
        result = cursor.handle(native_event("cursor", case, "PreToolUse"))
        assert_decision("cursor", result, True)
        state = case.state()
        assert set(state["participants"][case.base["coordinator"]]["pending"]) == {"actual-tool"}
        assert state["seq"] == before["seq"] + 1
    # Restore the environment through pytest and remove only disposable fixture state.
    finally:
        # Release the disposable repository even when an assertion fails.
        case.doCleanups()


@pytest.mark.parametrize("host", ["claude", "cursor"])
@pytest.mark.parametrize("payload", ["{", "[]", "oversized"])
def test_native_process_rejects_invalid_or_oversized_json(host: NativeHost, payload: str) -> None:
    """Use the real process boundary to reject malformed or over-budget host envelopes.

    Args:
        host: Native adapter process to invoke.
        payload: Invalid input or selector for a valid oversized JSON envelope.

    Raises:
        AssertionError: Invalid input fails open or emits an unusable protocol response.
    """
    # Construct a valid envelope exceeding the native one-megabyte input budget.
    if payload == "oversized":
        payload = json.dumps({"hook_event_name": "PreToolUse", "padding": "x" * (1024 * 1024)})
    # Remove Cursor's imported-Claude marker so this process exercises its own parser.
    environment = os.environ.copy()
    environment.pop("CURSOR_VERSION", None)
    result = subprocess.run(
        [codex.PYTHON, "-m", "nplet.adapters." + host],
        input=payload,
        text=True,
        capture_output=True,
        env=environment,
    )
    # Both native protocols recognize exit two as a blocking parser failure.
    assert result.returncode == 2
    assert isinstance(json.loads(result.stdout), dict)
    assert result.stderr.strip()
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("host", ["claude", "cursor", "codex"])
@pytest.mark.parametrize(
    "shell", ["cmd.exe", "powershell.exe", "pwsh.exe"] if os.name == "nt" else ["/bin/sh"]
)
def test_native_config_quotes_checkout_and_reports_missing_environment(
    host: Literal["claude", "cursor", "codex"], shell: str, tmp_path: Path
) -> None:
    """Run actual checked-in hook commands under quoted roots and missing local interpreters.

    Args:
        host: Native configuration to inspect and execute.
        shell: Actual host shell; Windows runners must provide PowerShell 5.1 and 7.
        tmp_path: Disposable directory for a nested Git checkout with shell metacharacters.

    Raises:
        AssertionError: Quoting fails, native denial is lost or missing setup fails silently.
    """
    # Select the checked-in pre-tool command without translating or rewriting its shell text.
    assert shutil.which(shell) is not None, f"Required native shell missing: {shell}"
    # Claude and Codex use Bash-style lifecycle commands.
    if host in {"claude", "codex"}:
        config_path = ".claude/settings.json" if host == "claude" else ".codex/hooks.json"
        config = json.loads((ROOT / config_path).read_text())
        command = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    # Cursor additionally requires fail-closed configuration at the permission boundary.
    else:
        # Require both blocking boundaries: generic identity and server-specific provenance.
        config = json.loads((ROOT / ".cursor/hooks.json").read_text())
        command = (
            "git -c alias.task-workspace-hook=!./src/nplet/adapters/launch.sh "
            "task-workspace-hook cursor"
        )
        for event_name in ("preToolUse", "beforeMCPExecution"):
            # Both registrations must invoke the reviewed adapter and fail closed.
            entries = config["hooks"][event_name]
            assert entries
            assert all(entry["command"] == command for entry in entries)
            assert all(entry.get("failClosed") is True for entry in entries)
    # Make a quoted checkout containing only the real entry script and local interpreter link.
    root = tmp_path / "checkout with 'quotes'"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    nested = root / "nested" / "directory"
    nested.mkdir(parents=True)
    shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    link_directory(root / ".venv", ROOT / ".venv")
    # Use missing registration to obtain a native denial without mutating a live task.
    event: JsonObject = {
        "hook_event_name": "preToolUse" if host == "cursor" else "PreToolUse",
        "session_id": "isolated",
        "conversation_id": "isolated",
        "workspace_roots": [str(root)],
        "cwd": str(nested),
        "tool_name": "Write",
        "tool_input": {"file_path": str(root / "src/unregistered.py"), "content": "pass\n"},
        "tool_use_id": "isolated-tool",
    }
    environment = os.environ.copy()
    environment.pop("CURSOR_VERSION", None)
    # Each configured command must locate its checkout from both root and nested host cwd.
    for cwd in (root, nested):
        result = subprocess.run(
            shell_command(shell, command),
            cwd=cwd,
            input=json.dumps(event),
            text=True,
            capture_output=True,
            env=environment,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert_decision(
            "cursor" if host == "cursor" else "claude", json.loads(result.stdout), False
        )
    extra = subprocess.run(
        shell_command(shell, command + " extra", preserve_native_exit=True),
        cwd=nested,
        input="{}",
        text=True,
        capture_output=True,
        env=environment,
        timeout=10,
    )
    assert extra.returncode == 2
    assert extra.stdout == ""
    # PowerShell command spellings require Windows parsing.
    if shell in {"powershell.exe", "pwsh.exe"}:
        # Document the unmodified -Command wrapper separately from actual launcher exit 2.
        bare = subprocess.run(
            shell_command(shell, command + " extra"),
            cwd=nested,
            input="{}",
            text=True,
            capture_output=True,
            env=environment,
            timeout=10,
        )
        assert bare.returncode == 1
        assert bare.stdout == ""
    # Remove only the disposable interpreter link and require a useful blocking setup error.
    unlink_directory(root / ".venv")
    missing = subprocess.run(
        shell_command(shell, command, preserve_native_exit=True),
        cwd=nested,
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=environment,
        timeout=10,
    )
    assert missing.returncode == 2
    assert "TASK_WORKSPACE_SETUP_REQUIRED" in missing.stderr
    assert "Poetry" in missing.stderr
    assert missing.stdout == ""
    # PowerShell failures use Cursor’s native recovery fields.
    if shell in {"powershell.exe", "pwsh.exe"}:
        bare_missing = subprocess.run(
            shell_command(shell, command),
            cwd=nested,
            input=json.dumps(event),
            text=True,
            capture_output=True,
            env=environment,
            timeout=10,
        )
        assert bare_missing.returncode == 1
        assert "TASK_WORKSPACE_SETUP_REQUIRED" in bare_missing.stderr
        assert bare_missing.stdout == ""
    # Claude’s native failure shape differs from Cursor’s.
    if host == "claude":
        imported = subprocess.run(
            shell_command(shell, command),
            cwd=nested,
            input="not JSON",
            text=True,
            capture_output=True,
            env={**environment, "CURSOR_VERSION": "fixture"},
            timeout=10,
        )
        assert imported.returncode == 0
        assert imported.stdout == imported.stderr == ""
    # Failure before the launcher starts is Git/shell discovery failure, not hook exit 2.
    (root / "src/nplet/adapters/launch.sh").unlink()
    absent_launcher = subprocess.run(
        shell_command(shell, command, preserve_native_exit=True),
        cwd=nested,
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=environment,
        timeout=10,
    )
    assert absent_launcher.returncode in {127, 128}
    assert "launch.sh" in absent_launcher.stderr
    assert absent_launcher.stdout == ""


def test_claude_foreground_shell_retains_ambiguous_results_then_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require foreground configuration and an unambiguous terminal shell result.

    Args:
        monkeypatch: Scoped configuration matching the checked-in Claude foreground policy.

    Raises:
        AssertionError: Uncertain results clear work or the complete foreground result fails.
    """
    # Confirm the checked-in configuration supplies the required host guarantee.
    config = json.loads((ROOT / ".claude/settings.json").read_text())
    assert config["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    case = Fixture()
    case.setUp()
    case.base["host"] = claude.HOST
    case.base["coordinator"] = core.participant_key(case.base)
    # A failed native process must be killed within the test deadline.
    try:
        # Admit foreground Bash with only its native command arguments.
        case.create()
        case.ready()
        event = native_event(
            "claude", case, "PreToolUse", tool_name="Bash", tool_input={"command": "true"}
        )
        assert_decision("claude", claude.handle(event), True)
        # Reject missing certainty, interruption and explicit background handles.
        for result in (
            {"stdout": "", "stderr": ""},
            {"stdout": "", "stderr": "", "interrupted": True},
            {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": "pending"},
        ):
            claude.handle({**event, "hook_event_name": "PostToolUse", "tool_response": result})
            assert (
                "actual-tool" in case.state()["participants"][case.base["coordinator"]]["pending"]
            )
        # A matching explicit foreground completion may finally retire the pending operation.
        claude.handle(
            {
                **event,
                "hook_event_name": "PostToolUse",
                "tool_response": {"stdout": "done", "stderr": "", "interrupted": False},
            }
        )
        assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}
        assert case.state()["disposition"] == "active"
    # Remove only the disposable fixture; pytest restores host environment variables.
    finally:
        # Release the disposable repository even when an assertion fails.
        case.doCleanups()


@pytest.mark.parametrize("identifier", [None, "", 0])
def test_native_admission_requires_actual_host_tool_id(
    native: NativeCase, identifier: JsonValue
) -> None:
    """Refuse to invent tool correlation identities when native admission input is incomplete.

    Args:
        native: Host-specific disposable repository fixture.
        identifier: Missing, empty or incorrectly typed host tool identity.

    Raises:
        AssertionError: Admission invents an operation ID or mutates pending work.
    """
    # Establish readiness so malformed native identity is the isolated failure condition.
    host, case = native
    case.create()
    case.ready()
    before = case.state()
    # Require native denial and exact state preservation without synthesizing a UUID.
    result = dispatch(host, native_event(host, case, "PreToolUse", tool_use_id=identifier))
    assert_decision(host, result, False)
    assert case.state() == before


def started_coordinator(native: NativeCase) -> tuple[NativeHost, Fixture, JsonObject, str]:
    """Start a ready coordinator whose packet includes a governing checkout file.

    Args:
        native: Host-specific disposable repository fixture.

    Returns:
        The host, fixture, identity-resolved base request and native shell tool name.

    Raises:
        AssertionError: Ticket-first startup does not reach readiness.
    """
    # Provide a governing source before startup hashes it into the coordinator packet.
    host, case = native
    # Write through the lifecycle directory boundary so every native backend can read it.
    with core.Directory.absolute(case.root) as checkout:
        checkout.write("AGENTS.md", b"# Rules\n")
    dispatch(host, native_event(host, case, "UserPromptSubmit", prompt="Task: TEST-1"))
    issue = {"id": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977", "identifier": "TEST-1"}
    before, after = ticket_callbacks(host, case, issue)
    # Complete the exact ticket read so startup scopes, acknowledges and readies.
    for callback in before:
        dispatch(host, callback)
    assert "TASK_WORKSPACE_READY" in str(dispatch(host, after))
    observed = common.native_identity(native_event(host, case, "SessionStart"), case.base["host"])
    base = common.request_for(observed, "ready", case.base["host"])
    # Keep only the fields a literal bootstrap command carries.
    base.pop("participant_id", None)
    return host, case, base, "Bash" if host == "claude" else "Shell"


def lifecycle_call(
    host: NativeHost, case: Fixture, shell: str, request: JsonObject, admitted: bool = True
) -> JsonObject | None:
    """Submit one lifecycle request as the exact native shell bootstrap command.

    Args:
        host: Native adapter identity.
        case: Disposable repository fixture.
        shell: Native shell tool name for the host.
        request: Lifecycle request encoded with bootstrap_command.
        admitted: Whether the hook is expected to admit the command.

    Returns:
        The core result of running the admitted command, or None when it was denied.

    Raises:
        AssertionError: The hook decision differs from the expectation.
    """
    # Ask the real native hook whether the canonical command may run.
    command = common.bootstrap_command(request, case.base["host"])
    event = native_event(
        host,
        case,
        "PreToolUse",
        tool_use_id="bootstrap",
        tool_name=shell,
        tool_input={"command": command},
    )
    assert_decision(host, dispatch(host, event), admitted)
    # Run the admitted command's request through the core, as the shell would.
    return core.execute(request) if admitted else None


def test_native_coordinator_self_refresh_recovers_stale_governing_source(
    native: NativeCase,
) -> None:
    """A coordinator stranded by its own governing edit recovers through self-refresh.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: The stale source is not detected or recovery is not admitted.
    """
    # Change a governing source in the ready coordinator's packet.
    host, case, base, shell = started_coordinator(native)
    with core.Directory.absolute(case.root) as checkout:
        checkout.write("AGENTS.md", b"# Rules\n\nEdited by the coordinator.\n")
    denied = dispatch(host, native_event(host, case, "PreToolUse"))
    assert_decision(host, denied, False)
    assert "SOURCE_STALE" in str(denied) and "self-refresh scope" in str(denied)
    # Stale readiness blocks implementation but does not block inspection or settle work.
    state_before_read = case.state()
    inspection = native_event(
        host,
        case,
        "PreToolUse",
        tool_name="Read",
        tool_use_id="stale-inspection",
        tool_input={"file_path": str(case.root / "AGENTS.md")},
    )
    assert_decision(host, dispatch(host, inspection), True)
    assert case.state() == state_before_read
    # Obtain the current packet through the admitted issue-level diagnose, not state.json.
    diagnosis = lifecycle_call(
        host, case, shell, {**base, "operation": "diagnose", "request_id": "d"}
    )
    assert diagnosis is not None and diagnosis["ok"] is True, diagnosis
    assert all("available" not in ref for ref in diagnosis["packet"])
    # The aligned sources name the stale governing file; nothing here hashes a file.
    sources = diagnosis["sources"]
    assert [s["locator"] for s in sources] == [r["locator"] for r in diagnosis["packet"]]
    assert [s["locator"] for s in sources if not s["available"]] == [str(case.root / "AGENTS.md")]
    # Re-scope the coordinator's own packet with diagnose's current digests only.
    packet = [
        {**ref, "sha256": source["current_sha256"]}
        for ref, source in zip(diagnosis["packet"], sources, strict=True)
    ]
    scope = {
        **base,
        "operation": "scope",
        "request_id": "self-refresh",
        "expected_revision": diagnosis["revision"],
        "target_participant": case.base["coordinator"],
        "packet": packet,
    }
    scoped = lifecycle_call(host, case, shell, scope)
    assert scoped is not None and scoped["ok"] is True, scoped
    # The scope alone grants nothing: ordinary tools stay denied until ready.
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), False)
    # Readiness still requires the explicit read, acknowledge and ready cycle.
    read = lifecycle_call(host, case, shell, {**base, "operation": "read", "request_id": "r"})
    assert read is not None and read["ok"] is True, read
    acknowledged = lifecycle_call(
        host,
        case,
        shell,
        {
            **base,
            "operation": "acknowledge",
            "request_id": "a",
            "packet_digest": read["packet_digest"],
        },
    )
    assert acknowledged is not None and acknowledged["ok"] is True, acknowledged
    ready = lifecycle_call(host, case, shell, {**base, "operation": "ready", "request_id": "y"})
    assert ready is not None and ready["ok"] is True, ready
    # Ordinary tools are admitted again at the refreshed packet.
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), True)


def test_native_coordinator_unchanged_digest_scope_keeps_source_stale(
    native: NativeCase,
) -> None:
    """A self-refresh repeating stale digests is accepted but cannot restore readiness.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: The unchanged scope is refused, or read no longer names the
            stale governing source.
    """
    # Strand the coordinator at SOURCE_STALE through its own governing edit.
    host, case, base, shell = started_coordinator(native)
    with core.Directory.absolute(case.root) as checkout:
        checkout.write("AGENTS.md", b"# Rules\n\nEdited by the coordinator.\n")
    diagnosis = lifecycle_call(
        host, case, shell, {**base, "operation": "diagnose", "request_id": "d"}
    )
    assert diagnosis is not None and diagnosis["ok"] is True, diagnosis
    # Re-scope with the recorded packet unchanged: admitted and committed.
    scope = {
        **base,
        "operation": "scope",
        "request_id": "unchanged",
        "expected_revision": diagnosis["revision"],
        "target_participant": case.base["coordinator"],
        "packet": diagnosis["packet"],
    }
    scoped = lifecycle_call(host, case, shell, scope)
    assert scoped is not None and scoped["ok"] is True, scoped
    # Read still fails, and the failure itself names the stale source and digests.
    read = lifecycle_call(host, case, shell, {**base, "operation": "read", "request_id": "r"})
    assert read is not None and read["ok"] is False and read["code"] == "SOURCE_STALE", read
    stale = {s["locator"]: s for s in diagnosis["sources"] if not s["available"]}
    assert read["stale"] == list(stale.values())
    assert [s["locator"] for s in read["stale"]] == [str(case.root / "AGENTS.md")]
    assert read["stale"][0]["current_sha256"] is not None
    assert read["stale"][0]["current_sha256"] != read["stale"][0]["recorded_sha256"]
    # Ordinary tools stay denied.
    assert_decision(host, dispatch(host, native_event(host, case, "PreToolUse")), False)


@pytest.mark.parametrize("change", ["locator", "target"])
def test_native_coordinator_self_refresh_rejects_widened_scope(
    native: NativeCase, change: str
) -> None:
    """A stale coordinator cannot change a reference or scope another key before readiness.

    Args:
        native: Host-specific disposable repository fixture.
        change: Whether the request changes a locator or targets another participant.

    Raises:
        AssertionError: A widened scope is admitted or mutates state.
    """
    # Strand the coordinator at SOURCE_STALE through its own governing edit.
    host, case, base, shell = started_coordinator(native)
    with core.Directory.absolute(case.root) as checkout:
        checkout.write("AGENTS.md", b"# Rules\n\nEdited by the coordinator.\n")
    state = case.state()
    key = case.base["coordinator"]
    data = (case.root / "AGENTS.md").read_bytes()
    packet = [{**ref, "sha256": core.sha(data)} for ref in state["participants"][key]["packet"]]
    # Widen the request beyond a digest-only refresh of the caller's own packet.
    target = key
    if change == "locator":
        packet[-1] = {**packet[-1], "locator": str(case.root / "OTHER.md")}
    else:
        target = "f" * 64
        packet = [{**ref, "reader": target} for ref in packet]
    scope = {
        **base,
        "operation": "scope",
        "request_id": "widened",
        "expected_revision": state["revision"],
        "target_participant": target,
        "packet": packet,
    }
    state_file = case.root / ".task/.control/issues/TEST-1/state.json"
    before = state_file.read_bytes()
    assert lifecycle_call(host, case, shell, scope, admitted=False) is None
    # Committed state is unchanged and ordinary tools remain denied as stale.
    assert state_file.read_bytes() == before
    denied = dispatch(host, native_event(host, case, "PreToolUse"))
    assert_decision(host, denied, False)
    assert "SOURCE_STALE" in str(denied)


def test_native_self_refresh_from_prior_read_requires_removing_available(
    native: NativeCase,
) -> None:
    """A prior read result's references work for self-refresh only without ``available``.

    Args:
        native: Host-specific disposable repository fixture.

    Raises:
        AssertionError: Read references are admitted with their computed field, or
            rejected after it is removed.
    """
    # Keep the references from a successful read while the coordinator is still ready.
    host, case, base, shell = started_coordinator(native)
    read = lifecycle_call(host, case, shell, {**base, "operation": "read", "request_id": "r0"})
    assert read is not None and read["ok"] is True, read
    # Strand the coordinator, then refresh the AGENTS.md digest in those references.
    with core.Directory.absolute(case.root) as checkout:
        checkout.write("AGENTS.md", b"# Rules\n\nEdited by the coordinator.\n")
    digest = core.sha((case.root / "AGENTS.md").read_bytes())
    references = [
        {**ref, "sha256": digest} if ref["locator"].endswith("AGENTS.md") else ref
        for ref in read["references"]
    ]
    scope = {
        **base,
        "operation": "scope",
        "request_id": "from-read",
        "expected_revision": case.state()["revision"],
        "target_participant": case.base["coordinator"],
    }
    # The computed availability field is not part of a packet reference.
    assert lifecycle_call(host, case, shell, {**scope, "packet": references}, False) is None
    # Removing it yields exactly the committed shape with refreshed digests.
    stripped = [{k: v for k, v in ref.items() if k != "available"} for ref in references]
    scoped = lifecycle_call(host, case, shell, {**scope, "packet": stripped})
    assert scoped is not None and scoped["ok"] is True, scoped
