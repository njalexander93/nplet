"""Assert host translation decisions and prohibited lifecycle effects."""

from __future__ import annotations

import pytest

from nplet.adapters import claude, codex, cursor
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def test_codex_async_handle_preserves_absence_and_rejects_boolean() -> None:
    """Codex async handle preserves absence and rejects boolean."""
    assert codex.async_handle(None) is None
    assert codex.async_handle(7) == "7"
    assert codex.async_handle("worker-1") == "worker-1"
    # Exercise malformed asynchronous handles against their specific diagnostics.
    for value, code in (
        (False, "ASYNC_HANDLE_CONFLICT"),
        (True, "ASYNC_HANDLE_CONFLICT"),
        ([], "ASYNC_HANDLE_CONFLICT"),
        ("", "INVALID_REQUEST"),
        ("with space", "INVALID_REQUEST"),
    ):
        # Each rejected handle must raise before creating a pending call.
        with pytest.raises(core.WorkspaceError) as captured:
            codex.async_handle(value)
        assert captured.value.code == code


def test_codex_pretool_denies_unsupported_child_control_before_request_or_core_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deny unsupported child controls before request or core work.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda _event, _suffix: False)
    monkeypatch.setattr(codex, "bootstrap", lambda _event, ready=False: False)
    monkeypatch.setattr(codex, "provider_gate", lambda _event: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("unexpected binding read"))
    event = {"hook_event_name": "PreToolUse", "tool_name": "multi_agent_v1resume_agent"}
    result = codex.handle(event)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "HOST_UNSUPPORTED_CHILD_IDENTITY"
        in result["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.usefixtures("recorded_operation")
def test_codex_pretool_records_exact_poll_handle_only_after_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex pretool records exact poll handle only after ready.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    # A real read-only poll follows its previously admitted protected process.
    monkeypatch.setattr(codex.core, "repository", lambda _path: (codex.ROOT, None, []))
    operations: list[dict[str, object]] = []
    monkeypatch.setattr(codex, "startup_lookup_state", lambda _event, _suffix: False)
    monkeypatch.setattr(codex, "bootstrap", lambda _event, ready=False: False)
    monkeypatch.setattr(codex, "provider_gate", lambda _event: False)
    monkeypatch.setattr(
        codex,
        "request_for",
        lambda _event, operation: {"operation": operation, "request_id": "seed"},
    )

    def execute(request: dict[str, object]) -> dict[str, object]:
        """Execute.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        operations.append(request)
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(codex.core, "execute", execute)
    event = {
        "session_id": "session",
        "cwd": str(codex.ROOT),
        "hook_event_name": "PreToolUse",
        "tool_name": "write_stdin",
        "tool_use_id": "tool-1",
        "tool_input": {"session_id": 41},
    }
    assert codex.handle(event) == {}
    assert [item["operation"] for item in operations] == ["ready", "tool-start"]
    assert operations[-1]["poll_handle"] == "41"
    assert operations[-1]["tool_id"] == "tool-1"


@pytest.mark.usefixtures("protected_effect")
def test_codex_pretool_invalid_poll_handle_has_no_tool_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex pretool invalid poll handle has no tool start.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    operations: list[str] = []
    monkeypatch.setattr(codex, "startup_lookup_state", lambda _event, _suffix: False)
    monkeypatch.setattr(codex, "bootstrap", lambda _event, ready=False: False)
    monkeypatch.setattr(codex, "provider_gate", lambda _event: False)
    monkeypatch.setattr(codex, "request_for", lambda _event, operation: {"operation": operation})

    def execute(request: dict[str, object]) -> dict[str, object]:
        """Execute.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        operations.append(str(request["operation"]))
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(codex.core, "execute", execute)
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "write_stdin",
        "tool_use_id": "tool-1",
        "tool_input": {"session_id": True},
    }
    result = codex.handle(event)
    assert operations == ["ready"]
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "ASYNC_HANDLE_CONFLICT" in result["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_keeps_unproven_completion_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex posttool keeps unproven completion pending.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda _event, _suffix: False)
    monkeypatch.setattr(codex, "request_for", lambda _event, operation: {"operation": operation})
    monkeypatch.setattr(codex.core, "execute", lambda _request: pytest.fail("unproven completion"))
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "exec_command",
        "tool_use_id": "tool-1",
        "tool_response": {"output": "still running"},
    }
    assert codex.handle(event) == {}


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_records_typed_completion_and_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex posttool records typed completion and handle.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(codex, "startup_lookup_state", lambda _event, _suffix: False)
    monkeypatch.setattr(codex, "request_for", lambda _event, operation: {"operation": operation})

    def execute(request: dict[str, object]) -> dict[str, object]:
        """Execute.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request)
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(codex.core, "execute", execute)
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "exec_command",
        "tool_use_id": "tool-1",
        "tool_response": {"exit_code": 0, "session_id": "41"},
    }
    assert codex.handle(event) == {}
    assert len(calls) == 1
    assert calls[0]["completed"] is True
    assert calls[0]["async_handle"] == "41"
    assert calls[0]["tool_id"] == "tool-1"


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_rejects_conflicting_poll_handle_without_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex posttool rejects conflicting poll handle without settlement.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda _event, _suffix: False)
    monkeypatch.setattr(codex, "request_for", lambda _event, operation: {"operation": operation})
    monkeypatch.setattr(codex.core, "execute", lambda _request: pytest.fail("conflicting handle"))
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "write_stdin",
        "tool_use_id": "tool-1",
        "tool_input": {"session_id": "41"},
        "tool_response": {"exit_code": 0, "session_id": "42"},
    }
    assert codex.handle(event) == {
        "systemMessage": "TASK_WORKSPACE_NOT_READY: ASYNC_HANDLE_CONFLICT"
    }


def test_claude_failure_denies_tool_without_permission_grant() -> None:
    """Claude failure denies tool without permission grant."""
    output = claude.failure("PreToolUse", "SOURCE_STALE")
    decision = output["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "SOURCE_STALE" in decision["permissionDecisionReason"]
    assert claude.failure("PreCompact", "SOURCE_STALE") == {}


def test_cursor_failure_denies_tool_and_blocks_prompt() -> None:
    """Cursor failure denies tool and blocks prompt."""
    assert cursor.failure("preToolUse", "BINDING_MISSING")["permission"] == "deny"
    assert cursor.failure("beforeSubmitPrompt", "BINDING_MISSING")["continue"] is False


@pytest.mark.parametrize("adapter,event_name", [(claude, "PreToolUse"), (cursor, "preToolUse")])
def test_native_adapter_pretool_preserves_host_specific_permission(
    adapter: object, event_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native adapter pretool preserves host specific permission.

    Args:
        adapter: Adapter module selected by the parameterized case.
        event_name: Native hook event name selected for this test.
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(adapter.common, "native_identity", lambda event, _host: event)
    observed: list[dict[str, object]] = []
    monkeypatch.setattr(adapter.common, "native_pre", lambda event, _host: observed.append(event))
    event = {"hook_event_name": event_name}
    result = adapter.handle(event)
    assert observed == [event]
    assert result == ({} if adapter is claude else {"permission": "allow"})


def test_cursor_remote_rejected_before_native_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cursor remote rejected before native identity.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setenv("CURSOR_CODE_REMOTE", "true")
    monkeypatch.setattr(
        cursor.common, "native_identity", lambda *_args: pytest.fail("identity read")
    )
    result = cursor.handle({"hook_event_name": "preToolUse"})
    assert result["permission"] == "deny"
    assert "HOST_UNSUPPORTED_REMOTE" in result["user_message"]


def test_claude_cursor_import_has_no_duplicate_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude cursor import has no duplicate effect.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setenv("CURSOR_VERSION", "1")
    monkeypatch.setattr(
        claude.common, "native_identity", lambda *_args: pytest.fail("duplicate hook")
    )
    assert claude.handle({"hook_event_name": "PreToolUse"}) == {}
    assert claude.main() == 0


@pytest.mark.parametrize(
    ("name", "expected_effect"),
    [
        ("PostToolUse", "post:False"),
        ("PostToolUseFailure", "post:True"),
        ("PreCompact", "observe"),
        ("PostCompact", "observe"),
        ("Stop", "observe"),
        ("SessionEnd", "observe"),
    ],
)
def test_claude_routes_completion_and_advisory_events_without_permission_decision(
    monkeypatch: pytest.MonkeyPatch, name: str, expected_effect: str
) -> None:
    """Record only the documented completion or observation effect.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        name: Event or case name selected for this test.
        expected_effect: Expected effect for this case.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.setattr(claude.common, "native_identity", lambda event, _host: event)
    effects: list[str] = []
    monkeypatch.setattr(
        claude.common,
        "native_post",
        lambda _event, _host, failed: effects.append(f"post:{failed}"),
    )
    monkeypatch.setattr(
        claude.common, "native_observe", lambda _event, _host: effects.append("observe")
    )
    assert claude.handle({"hook_event_name": name}) == {}
    assert effects == [expected_effect]


def test_claude_prompt_and_session_context_keep_host_permission_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return task selection and recovery text without any allow decision.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.setattr(claude.common, "native_identity", lambda event, _host: event)
    monkeypatch.setattr(claude.common, "prompt", lambda _event, _host: {"decision": "block"})
    monkeypatch.setattr(claude.common, "native_context", lambda _event, _host: "ready context")
    assert claude.handle({"hook_event_name": "UserPromptSubmit"}) == {"decision": "block"}
    context = claude.handle({"hook_event_name": "SessionStart"})
    assert context["hookSpecificOutput"]["additionalContext"] == "ready context"
    assert "permissionDecision" not in context["hookSpecificOutput"]


def test_cursor_routes_prompt_completion_context_and_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use Cursor's native continue/context fields and advisory ending.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(cursor.common, "native_identity", lambda event, _host: event)
    effects: list[str] = []
    monkeypatch.setattr(
        cursor.common, "native_post", lambda _event, _host, failed: effects.append(f"post:{failed}")
    )
    monkeypatch.setattr(
        cursor.common, "native_observe", lambda _event, _host: effects.append("observe")
    )
    monkeypatch.setattr(cursor.common, "native_context", lambda _event, _host: "ready context")
    monkeypatch.setattr(
        cursor.common, "prompt", lambda _event, _host: {"decision": "block", "reason": "wrong task"}
    )
    assert cursor.handle({"hook_event_name": "postToolUse"}) == {}
    assert cursor.handle({"hook_event_name": "postToolUseFailure"}) == {}
    assert cursor.handle({"hook_event_name": "beforeSubmitPrompt"}) == {
        "continue": False,
        "user_message": "wrong task",
    }
    assert cursor.handle({"hook_event_name": "sessionStart"}) == {
        "additional_context": "ready context"
    }
    assert cursor.handle({"hook_event_name": "preCompact"}) == {"user_message": "ready context"}
    assert cursor.handle({"hook_event_name": "stop"}) == {}
    assert effects == ["post:False", "post:True", "observe"]


@pytest.mark.parametrize("failed", [False, True])
def test_cursor_nonbootstrap_linear_completion_uses_normal_admission(
    monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    """Route an unassigned Linear completion through normal tool settlement.

    Args:
        monkeypatch: Pytest fixture isolating the native callback boundary.
        failed: Whether Cursor reports provider failure instead of success.
    """
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(cursor.common, "native_identity", lambda event, _host: event)
    monkeypatch.setattr(cursor.common, "lookup_required", lambda _event, _host: False)
    settled: list[bool] = []
    monkeypatch.setattr(
        cursor.common,
        "native_post",
        lambda _event, _host, failed: settled.append(failed),
    )
    # An ordinary provider result must never disappear at the bootstrap branch.
    name = "postToolUseFailure" if failed else "postToolUse"
    assert cursor.handle({"hook_event_name": name, "tool_name": "MCP:get_issue"}) == {}
    assert settled == [failed]


def test_cursor_subagent_denied_before_identity_or_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep child-session work outside the parent binding.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(cursor.common, "native_identity", lambda *_args: pytest.fail("identity"))
    result = cursor.handle({"hook_event_name": "subagentStart"})
    assert result["permission"] == "deny"
    assert "HOST_UNSUPPORTED_CHILD_IDENTITY" in result["user_message"]


def test_claude_failure_blocks_prompt_and_reports_advisory_fallback() -> None:
    """Use event-specific denial shapes without granting permission."""
    prompt = claude.failure("UserPromptSubmit", "BINDING_MISSING")
    assert prompt["decision"] == "block"
    assert "BINDING_MISSING" in prompt["reason"]
    ending = claude.failure("SessionStart", "BINDING_MISSING")
    assert "BINDING_MISSING" in ending["systemMessage"]


def test_claude_unknown_child_and_permission_events_do_not_grant_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse subagent hooks without a child identity and leave host permissions untouched.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.setattr(claude.common, "native_identity", lambda event, _host: event)
    monkeypatch.setattr(claude.common, "native_observe", lambda *_args: pytest.fail("observe"))
    assert claude.handle({"hook_event_name": "PermissionRequest"}) == {}
    # Subagent hooks without a normalized child identity only advise; they cannot block.
    for name in ("SubagentStart", "SubagentStop"):
        child = claude.handle({"hook_event_name": name})
        assert "HOST_UNSUPPORTED_CHILD_IDENTITY" in child["systemMessage"]
    unknown = claude.handle({"hook_event_name": "Unknown"})
    assert "HOST_UNSUPPORTED_EVENT" in unknown["systemMessage"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (core.WorkspaceError("SOURCE_STALE"), "SOURCE_STALE"),
        (OSError("private detail"), "RECOVERY_REQUIRED"),
    ],
)
def test_claude_catches_native_failure_as_bounded_tool_denial(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: str
) -> None:
    """Keep exception contents out of the PreToolUse response.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        error: Exception raised by the simulated failure.
        code: Diagnostic code selected for this case.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.setattr(
        claude.common,
        "native_identity",
        lambda *_args: (_ for _ in ()).throw(error),
    )
    response = claude.handle({"hook_event_name": "PreToolUse"})
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert code in reason
    assert "private detail" not in reason


@pytest.mark.parametrize(
    ("name", "field"),
    [
        ("subagentStart", "permission"),
        ("postToolUse", "additional_context"),
        ("postToolUseFailure", "additional_context"),
        ("sessionStart", "additional_context"),
        ("preCompact", "user_message"),
    ],
)
def test_cursor_failure_renders_documented_event_field(name: str, field: str) -> None:
    """Use the documented native field for each denied or advisory event.

    Args:
        name: Event or case name selected for this test.
        field: Request or event field varied by this case.
    """
    response = cursor.failure(name, "SOURCE_STALE")
    assert field in response
    # Advisory fields carry the diagnostic text; the permission field is a literal.
    if field != "permission":
        assert "SOURCE_STALE" in response[field]
    assert cursor.failure("unknown", "SOURCE_STALE") == {}


def test_cursor_prompt_continue_and_unsupported_event_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Continue a valid prompt without granting tools and deny unknown event grammar.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(cursor.common, "native_identity", lambda event, _host: event)
    monkeypatch.setattr(cursor.common, "prompt", lambda *_args: {})
    assert cursor.handle({"hook_event_name": "beforeSubmitPrompt"}) == {"continue": True}
    assert cursor.handle({"hook_event_name": "unknown"}) == {}


def test_cursor_main_delegates_to_bounded_native_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the shared supervised protocol for one Cursor event.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        cursor.common,
        "run_native",
        lambda handler, failure: calls.append((handler, failure)) or 2,
    )
    assert cursor.main() == 2
    assert calls == [(cursor.handle, cursor.failure)]


def test_claude_main_uses_runner_when_not_cursor_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run one Claude protocol exchange only outside Cursor-imported settings.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        claude.common,
        "run_native",
        lambda handler, failure: calls.append((handler, failure)) or 2,
    )
    assert claude.main() == 2
    assert calls == [(claude.handle, claude.failure)]


def test_claude_preparation_reads_pass_through_a_pending_ticket_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preparation reads keep the Task marker and never settle the ticket lookup.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.setattr(claude.common, "native_identity", lambda event, _host: event)
    monkeypatch.setattr(claude.common, "lookup_required", lambda _event, _host: True)
    monkeypatch.setattr(claude.common, "preparation_tool", lambda _event, _host: True)
    monkeypatch.setattr(
        claude.common, "native_ticket_lookup", lambda *_args, **_kwargs: pytest.fail("lookup")
    )
    monkeypatch.setattr(claude.common, "native_pre", lambda *_args: pytest.fail("pre"))
    monkeypatch.setattr(claude.common, "ticket_callback", lambda *_args: False)
    posts: list[object] = []
    monkeypatch.setattr(
        claude.common, "native_post", lambda value, *_args, **_kwargs: posts.append(value)
    )
    # Neither the start nor the completion of a preparation read is recorded or settled.
    assert claude.handle({"hook_event_name": "PreToolUse", "tool_name": "Read"}) == {}
    assert claude.handle({"hook_event_name": "PostToolUse", "tool_name": "Read"}) == {}
    assert posts == [{"hook_event_name": "PostToolUse", "tool_name": "Read"}]


def test_claude_subagent_start_reports_the_child_join_as_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SubagentStart returns the child join text as additional context only.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.setattr(claude.common, "native_identity", lambda event, _host: event)
    seen: list[tuple[object, str]] = []
    monkeypatch.setattr(
        claude.common,
        "child_start",
        lambda event, host: seen.append((event, host)) or "TASK_WORKSPACE_CHILD_READY: x",
    )
    event = {"hook_event_name": "SubagentStart", "child": True}
    assert claude.handle(event) == {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": "TASK_WORKSPACE_CHILD_READY: x",
        }
    }
    assert seen == [(event, claude.HOST)]


def test_claude_subagent_stop_observes_only_a_child_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SubagentStop records an observation for a child and never settles or detaches it.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.setattr(claude.common, "native_identity", lambda event, _host: event)
    observed: list[object] = []
    monkeypatch.setattr(
        claude.common, "native_observe", lambda event, _host: observed.append(event)
    )
    event = {"hook_event_name": "SubagentStop", "child": True}
    assert claude.handle(event) == {}
    assert observed == [event]
