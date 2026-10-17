"""Check Codex event dispatch and exact admission/completion decisions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nplet.adapters import codex
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


@pytest.mark.usefixtures("recorded_operation")
def test_native_web_result_settles_without_interpreting_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the observed terminal web callback without trusting response content.

    Args:
        monkeypatch: Isolate lifecycle effects while retaining actual event translation.
    """
    # A native web result is a content list, not shell transport metadata.
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core, "execute", lambda request: calls.append(request) or {"ok": True}
    )
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "webrun",
        "tool_use_id": "web-original",
        "tool_input": {"open": [{"ref_id": "https://learn.chatgpt.com/docs/hooks"}]},
        "tool_response": [{"type": "text", "text": '{"session_id":77}'}],
    }
    # Settlement uses the envelope's exact call ID and never a value inside output.
    assert codex.handle(event) == {}
    assert len(calls) == 1
    assert calls[0]["tool_id"] == "web-original"
    assert calls[0]["completed"] is True
    assert calls[0]["async_handle"] is None


@pytest.mark.parametrize("output", ["", "plain output", "{", '{"session_id":77}'])
@pytest.mark.parametrize("tool_name", ["Bash", "apply_patch"])
@pytest.mark.usefixtures("recorded_operation")
def test_native_text_completion_settles_original_call(
    monkeypatch: pytest.MonkeyPatch, output: str, tool_name: str
) -> None:
    """Use the canonical final callback, never parse command stdout as transport metadata.

    Args:
        monkeypatch: Isolate lifecycle effects while retaining real adapter dispatch.
        output: Native stdout, including text that resembles a transport handle.
        tool_name: Observed canonical shell or patch tool with a final text response.
    """
    # Native terminal callbacks carry text whose contents cannot grant lifecycle authority.
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core, "execute", lambda request: calls.append(request) or {"ok": True}
    )
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_use_id": "original",
        "tool_input": {"command": "true"},
        "tool_response": output,
    }
    assert codex.handle(event) == {}
    assert len(calls) == 1
    assert calls[0]["tool_id"] == "original"
    assert calls[0]["completed"] is True
    assert calls[0].get("async_handle") is None
    assert calls[0].get("poll", False) is False


def test_codex_wrappers_fix_the_host_and_defer_to_shared_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep Codex identity fixed in common request, prompt, and attach calls.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        tmp_path: Disposable directory for repository or file fixtures.
    """
    seen: list[tuple[object, ...]] = []
    event = {"cwd": str(tmp_path / "checkout"), "session_id": "s"}
    monkeypatch.setattr(
        codex.common,
        "request_for",
        lambda native, operation, host: seen.append(("request", native, operation, host)) or {},
    )
    monkeypatch.setattr(
        codex.common,
        "automatic_attach",
        lambda native, identifier, host: seen.append(("attach", native, identifier, host)),
    )
    monkeypatch.setattr(
        codex.common,
        "prompt",
        lambda native, host: seen.append(("prompt", native, host)) or {},
    )
    assert codex.request_for(event, "ready") == {}
    assert codex.automatic_attach(event, "TEST-30") is None
    assert codex.prompt(event) == {}
    assert seen == [
        ("request", event, "ready", "codex"),
        ("attach", event, "TEST-30", "codex"),
        ("prompt", event, "codex"),
    ]


def test_codex_bootstrap_rejects_alternate_tool_shapes_before_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed shell metadata without accepting a lifecycle command.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex.common, "canonical_bootstrap", lambda *_args: pytest.fail("parsed"))
    event = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "command", "login": False, "shell": "/bin/sh"},
    }
    assert codex.bootstrap({**event, "tool_name": "Read"}) is False
    assert codex.bootstrap({**event, "tool_input": {**event["tool_input"], "tty": True}}) is False
    assert (
        codex.bootstrap({**event, "tool_input": {**event["tool_input"], "command": "other"}})
        is False
    )


@pytest.mark.parametrize(
    ("platform", "shell", "foreign_shell"),
    [
        pytest.param("nt", "powershell.exe", "/bin/sh", id="windows-powershell"),
        pytest.param("posix", "/bin/sh", "powershell.exe", id="posix-sh"),
    ],
)
def test_codex_bootstrap_passes_exact_eligible_shell_call(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    shell: str,
    foreign_shell: str,
) -> None:
    """Forward only the shell supported by the selected native adapter platform.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        platform: Platform case selected by parametrization.
        shell: Shell executable selected for this case.
        foreign_shell: Shell executable that must be rejected.
    """
    captured: list[tuple[object, ...]] = []

    def canonical(*args: object) -> bool:
        """Capture the shared parser inputs.

        Args:
            args: Arguments supplied to the helper under test.

        Returns:
            The canonical serialized fixture value.
        """
        captured.append(args)
        return True

    monkeypatch.setattr(codex.common, "canonical_bootstrap", canonical)
    monkeypatch.setattr(codex, "os", SimpleNamespace(name=platform))
    event = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "command", "login": False, "shell": shell},
    }
    assert codex.bootstrap(event, ready=True) is True
    assert captured == [(event, "command", "codex", True, codex.PYTHON, codex.LIFECYCLE)]
    assert (
        codex.bootstrap({**event, "tool_input": {**event["tool_input"], "shell": foreign_shell}})
        is False
    )
    assert len(captured) == 1


def test_codex_pretool_denies_unsupported_child_control_without_binding_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block unsupported child controls before creating pending lifecycle work.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("binding read"))
    response = codex.handle(
        {"hook_event_name": "PreToolUse", "tool_name": "multi_agent_v1resume_agent"}
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "HOST_UNSUPPORTED_CHILD_IDENTITY"
        in response["hookSpecificOutput"]["permissionDecisionReason"]
    )
    assert (
        "no lifecycle operation grants a child binding here"
        in response["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_keeps_unknown_completion_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not settle work from an untyped, handle-free response.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("settled work"))
    assert (
        codex.handle(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "exec_command",
                "tool_use_id": "tool-1",
                "tool_response": {"exit_code": "0"},
            }
        )
        == {}
    )


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_settles_typed_failure_without_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat an explicit handle-free shell error as finished failed work.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(
        codex.core, "execute", lambda request: calls.append(request) or {"ok": True}
    )
    assert (
        codex.handle(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "exec_command",
                "tool_use_id": "tool-1",
                "tool_response": {"isError": True},
            }
        )
        == {}
    )
    assert calls[0]["tool_id"] == "tool-1"
    assert calls[0]["completed"] is True
    assert calls[0]["async_handle"] is None


def test_codex_failure_response_matches_event_contract() -> None:
    """Render denial, prompt block, or advisory result for the exact event."""
    assert codex.failure("PreToolUse", "BINDING_MISSING") == codex.denial("BINDING_MISSING")
    assert codex.failure("UserPromptSubmit", "BINDING_MISSING") == {
        "decision": "block",
        "reason": "TASK_WORKSPACE_NOT_READY: BINDING_MISSING",
    }
    assert codex.failure("SessionStart", "BINDING_MISSING") == {
        "systemMessage": "TASK_WORKSPACE_NOT_READY: BINDING_MISSING"
    }


def test_codex_main_uses_exit_zero_supervision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve the host's zero-exit wire convention for bounded failures.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    observed: list[tuple[object, object, int]] = []

    def run(handler: object, failure: object, *, error_status: int) -> int:
        """Record the runner contract without reading standard input.

        Args:
            handler: Hook handler passed to the supervised runner.
            failure: Failure callback or result selected by this case.
            error_status: Expected process status for the failure mode.

        Returns:
            The simulated subprocess or hook result.
        """
        observed.append((handler, failure, error_status))
        return 0

    monkeypatch.setattr(codex.runner, "run", run)
    assert codex.main() == 0
    assert observed == [(codex.handle, codex.failure, 0)]


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (set(), False),
        ({".task"}, False),
        ({".task", ".bindings", "lookup-required"}, True),
    ],
)
def test_startup_lookup_reads_only_explicit_session_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entries: set[str], expected: bool
) -> None:
    """Check the selected worktree's assignment marker through direct handles.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        tmp_path: Disposable directory for repository or file fixtures.
        entries: Existing binding or task entries for this case.
        expected: Expected outcome for this case.
    """
    root = tmp_path / "checkout"
    key = core.participant_key({"host": "codex", "session_id": "s"})

    class Node:
        """Expose one direct registered task or binding level."""

        def __init__(self, level: int = 0) -> None:
            """Select root, task, or bindings lookup depth.

            Args:
                level: Boundary severity level selected for this case.
            """
            self.level = level

        def __enter__(self) -> Node:
            """Hold the modeled directory.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled directory.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def exists(self, name: str) -> bool:
            """Report only the configured direct marker for this level.

            Args:
                name: Event or case name selected for this test.

            Returns:
                Whether the requested fixture entry exists.
            """
            # Resolve the Task directory before descending into marker state.
            if self.level == 0:
                assert name == ".task"
                return ".task" in entries
            # Resolve the private binding directory at the next level.
            if self.level == 1:
                assert name == ".bindings"
                return ".bindings" in entries
            assert name == key + ".lookup-required.json"
            return "lookup-required" in entries

        def child(self, name: str) -> Node:
            """Move only to the expected direct child level.

            Args:
                name: Event or case name selected for this test.

            Returns:
                The requested child directory fixture.
            """
            assert name == (".task" if self.level == 0 else ".bindings")
            return Node(self.level + 1)

    monkeypatch.setattr(codex.core, "repository", lambda _cwd: (root, None, [root]))
    monkeypatch.setattr(codex.core.Directory, "absolute", lambda _path: Node())
    assert (
        codex.startup_lookup_state({"cwd": str(root), "session_id": "s"}, ".lookup-required.json")
        is expected
    )


@pytest.mark.usefixtures("protected_effect")
def test_codex_pretool_preserves_failed_core_readiness_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deny ordinary tool admission when the lifecycle reports missing source scope.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    calls: list[str] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Record only readiness before denying the tool.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request["operation"])
        return {"ok": False, "code": "SOURCE_STALE"}

    monkeypatch.setattr(codex.core, "execute", execute)
    response = codex.handle(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "src/main.py", "content": "source"},
        }
    )
    assert response == codex.denial("SOURCE_STALE")
    assert calls == ["ready"]


@pytest.mark.usefixtures("protected_effect")
def test_codex_pretool_rejects_poll_without_observed_process_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never reserve a poll when its session handle is absent.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    calls: list[str] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Allow readiness but record forbidden later dispatch.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request["operation"])
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(codex.core, "execute", execute)
    response = codex.handle(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "write_stdin",
            "tool_input": {},
            "tool_use_id": "poll-1",
        }
    )
    assert response == codex.denial("ASYNC_HANDLE_CONFLICT")
    assert calls == ["ready"]


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_rejects_mismatched_poll_response_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the pending process when the observed poll response names another handle.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("settled poll"))
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "write_stdin",
            "tool_input": {"session_id": "process-1"},
            "tool_response": {"session_id": "process-2", "exit_code": 0},
            "tool_use_id": "poll-1",
        }
    )
    assert response == {"systemMessage": "TASK_WORKSPACE_NOT_READY: ASYNC_HANDLE_CONFLICT"}


@pytest.mark.usefixtures("protected_effect", "selected_ticket_dispatch")
def test_codex_pretool_requires_recorded_ticket_lookup_before_other_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fence implementation edits until the exact recorded Task issue read completes.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    monkeypatch.setattr(codex, "ticket_lookup", lambda *_args: {})
    assert codex.handle(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "src/main.py", "content": "source"},
        }
    ) == codex.denial("TICKET_READ_REQUIRED")
    assert (
        codex.handle(
            {"hook_event_name": "PreToolUse", "tool_name": "mcp__codex_apps__linear_get_issue"}
        )
        == {}
    )


@pytest.mark.usefixtures("protected_effect", "selected_ticket_dispatch")
def test_codex_pretool_preserves_ticket_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a failed direct issue read bounded and do not admit later work.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    monkeypatch.setattr(
        codex,
        "ticket_lookup",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("BINDING_CONFLICT")),
    )
    assert codex.handle(
        {"hook_event_name": "PreToolUse", "tool_name": "mcp__codex_apps__linear_get_issue"}
    ) == codex.denial("BINDING_CONFLICT")


def test_codex_pretool_admits_exact_provider_gate_without_tool_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow a separately verified archive provider call without pending-tool mutation.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: True)
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("opened tool binding"))
    assert (
        codex.handle(
            {"hook_event_name": "PreToolUse", "tool_name": "mcp__codex_apps__linear_get_document"}
        )
        == {}
    )


@pytest.mark.usefixtures("protected_effect")
def test_codex_pretool_records_ready_tool_or_exact_core_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record observed tool identity only after readiness and return core admission failure.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    requests: list[dict[str, Any]] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Permit readiness but reject the explicit tool-start transaction.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        requests.append(request)
        return (
            {"ok": True, "code": "READY"}
            if request["operation"] == "ready"
            else {
                "ok": False,
                "code": "REQUEST_CONFLICT",
            }
        )

    monkeypatch.setattr(codex.core, "execute", execute)
    response = codex.handle(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "src/main.py", "content": "source"},
            "tool_use_id": "write-1",
        }
    )
    assert response == codex.denial("REQUEST_CONFLICT")
    assert [item["operation"] for item in requests] == ["ready", "tool-start"]
    assert requests[-1]["tool_id"] == "write-1"


@pytest.mark.usefixtures("recorded_operation", "selected_ticket_dispatch")
def test_codex_posttool_ticket_lookup_reports_exact_missing_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve confirmed provider absence with the requested issue identifier.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    monkeypatch.setattr(
        codex,
        "ticket_lookup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(core.WorkspaceError("ISSUE_NOT_FOUND")),
    )
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__codex_apps__linear_get_issue",
            "tool_input": {"id": "TEST-30"},
        }
    )
    assert response == {"systemMessage": "TASK_WORKSPACE_NOT_READY: ISSUE_NOT_FOUND for TEST-30"}


@pytest.mark.usefixtures("recorded_operation", "selected_ticket_dispatch")
def test_codex_posttool_forwards_verified_ticket_startup_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The successful direct issue read returns its verified startup result unchanged.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    expected = {"systemMessage": "TASK_WORKSPACE_READY: assigned packet acknowledged"}
    calls: list[bool] = []
    monkeypatch.setattr(
        codex,
        "ticket_lookup",
        lambda _event, complete=False: calls.append(complete) or expected,
    )
    event = {"hook_event_name": "PostToolUse", "tool_name": "mcp__codex_apps__linear_get_issue"}
    assert codex.handle(event) is expected
    assert calls == [True]


def test_codex_session_and_endings_report_readiness_without_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose bounded startup context and keep Stop as an advisory observation.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(
        codex,
        "request_for",
        lambda _event, operation: {"operation": operation},
    )
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: requests.append(request) or {"ok": True, "code": "READY"},
    )
    start = codex.handle({"hook_event_name": "SessionStart"})
    assert start["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Task binding checked" in start["hookSpecificOutput"]["additionalContext"]
    assert codex.handle({"hook_event_name": "PreCompact"}) == {}
    assert codex.handle({"hook_event_name": "Stop"}) == {}
    assert [item["operation"] for item in requests] == ["ready", "ready", "event"]
    assert requests[-1]["event"] == {"code": "UNKNOWN"}


@pytest.mark.parametrize(
    ("tool_name", "inputs"),
    [
        ("exec_command", {}),
        ("Bash", {"command": "sleep 100 &", "login": False, "shell": "/bin/sh"}),
    ],
)
@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_records_observed_async_handle_without_inferred_completion(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    inputs: dict[str, object],
) -> None:
    """Keep asynchronous shell work pending under its observed process handle.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        tool_name: Legacy direct or Bash-wrapped transport spelling.
        inputs: Transport arguments distinguishing the legacy wrapper from native text.
    """
    # Isolate persistence while preserving the real transport classification and decoder.
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: requests.append(request) or {"ok": True, "code": "OK"},
    )
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": inputs,
            "tool_use_id": "shell-1",
            "tool_response": '{"session_id":77,"exit_code":null}',
        }
    )
    # Both legacy spellings retain the observed handle without claiming process exit.
    assert response == {}
    assert requests[0]["async_handle"] == "77"
    assert requests[0]["completed"] is False
    assert requests[0]["poll"] is False


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_preserves_core_correlation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface a failed completion transaction instead of hiding it as success.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(
        codex.core, "execute", lambda _request: {"ok": False, "code": "UNKNOWN_OPERATION"}
    )
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_use_id": "read-1",
            "tool_response": {"isError": False},
        }
    )
    assert response == {"systemMessage": "TASK_WORKSPACE_NOT_READY: UNKNOWN_OPERATION"}


def test_codex_pretool_keeps_clarification_available_without_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unready session can ask the user for an identity clarification.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("read binding"))
    # Neither interactive request tool can bypass a required ticket read.
    for tool in ("request_user_input", "request_user_input_async"):
        assert codex.handle({"hook_event_name": "PreToolUse", "tool_name": tool}) == {}


def test_codex_pretool_admits_exact_unready_bootstrap_before_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical recovery command is available before normal readiness.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("read binding"))
    assert codex.handle({"hook_event_name": "PreToolUse", "tool_name": "exec_command"}) == {}


@pytest.mark.usefixtures("protected_effect")
def test_codex_pretool_rechecks_ready_bootstrap_without_tool_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ready lifecycle command remains outside external pending-tool tracking.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda _event, ready=False: ready)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: calls.append(request) or {"ok": True, "code": "READY"},
    )
    assert codex.handle({"hook_event_name": "PreToolUse", "tool_name": "exec_command"}) == {}
    assert [call["operation"] for call in calls] == ["ready"]


@pytest.mark.usefixtures("recorded_operation")
def test_codex_pretool_records_correlated_poll_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reserve a poll against the exact observed process handle.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    # The real read-only polling effect retains an existing protected process admission.
    monkeypatch.setattr(codex.core, "repository", lambda _path: (codex.ROOT, None, []))
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: calls.append(request) or {"ok": True, "code": "READY"},
    )
    event = {
        "session_id": "session",
        "cwd": str(codex.ROOT),
        "hook_event_name": "PreToolUse",
        "tool_name": "write_stdin",
        "tool_use_id": "poll-1",
        "tool_input": {"session_id": 77},
    }
    assert codex.handle(event) == {}
    assert calls[-1]["operation"] == "tool-start"
    assert calls[-1]["poll_handle"] == "77"


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_uses_poll_input_when_response_omits_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed poll retains the input handle for original-operation correlation.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        codex.core,
        "execute",
        lambda request: calls.append(request) or {"ok": True, "code": "OK"},
    )
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "write_stdin",
        "tool_use_id": "poll-1",
        "tool_input": {"session_id": 77},
        "tool_response": {"exit_code": 0},
    }
    assert codex.handle(event) == {}
    assert calls[-1]["poll"] is True
    assert calls[-1]["completed"] is True
    assert calls[-1]["async_handle"] == "77"


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_does_not_infer_completion_from_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed serialized provider output has no completion evidence.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("settled work"))
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_use_id": "read-1",
        "tool_response": "{",
    }
    assert codex.handle(event) == {}


def test_codex_session_reports_denial_and_ending_failure_stays_advisory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed session readiness stays visible; ending observation grants nothing.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda _event, op: {"operation": op})
    monkeypatch.setattr(
        codex.core, "execute", lambda _request: {"ok": False, "code": "SOURCE_STALE"}
    )
    result = codex.handle({"hook_event_name": "SessionStart"})
    assert (
        "TASK_WORKSPACE_NOT_READY: SOURCE_STALE"
        in result["hookSpecificOutput"]["additionalContext"]
    )
    assert codex.handle({"hook_event_name": "Interrupt"}) == {}


def test_codex_prompt_permission_and_child_hooks_keep_distinct_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only prompt dispatch records task intent; host permission and child hooks grant none.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(
        codex, "prompt", lambda _event: {"decision": "block", "reason": "BINDING_CONFLICT"}
    )
    assert codex.handle({"hook_event_name": "UserPromptSubmit"}) == {
        "decision": "block",
        "reason": "BINDING_CONFLICT",
    }
    assert codex.handle({"hook_event_name": "PermissionRequest"}) == {}
    assert "HOST_UNSUPPORTED" in codex.handle({"hook_event_name": "SubagentStart"})["systemMessage"]


@pytest.mark.usefixtures("protected_effect")
def test_codex_provider_gate_failure_falls_through_to_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed archive gate never bypasses normal readiness enforcement.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        codex,
        "provider_gate",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("ARCHIVE_BLOCKED")),
    )
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "ready"})
    monkeypatch.setattr(
        codex.core, "execute", lambda _request: {"ok": False, "code": "SOURCE_STALE"}
    )
    assert codex.handle(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__codex_apps__github_update_file",
            "tool_input": {"path": "src/main.py", "content": "source"},
        }
    ) == codex.denial("SOURCE_STALE")


@pytest.mark.usefixtures("recorded_operation")
def test_codex_posttool_rejects_scalar_response_without_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A serialized scalar cannot prove a tool completed.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "request_for", lambda *_args: {"operation": "tool-complete"})
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("settled work"))
    assert (
        codex.handle(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "tool_use_id": "read-1",
                "tool_response": "42",
            }
        )
        == {}
    )


@pytest.mark.usefixtures("selected_ticket_dispatch")
def test_codex_posttool_linear_missing_reference_names_requested_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observed Linear missing-reference envelope yields its distinct diagnostic.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: True)
    monkeypatch.setattr(
        codex,
        "ticket_lookup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            core.WorkspaceError("LINEAR_ISSUE_UNRESOLVED")
        ),
    )
    response = codex.handle(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__codex_apps__linear_get_issue",
            "tool_input": {"id": "TEST-30"},
        }
    )
    assert response == {
        "systemMessage": (
            "TASK_WORKSPACE_NOT_READY: LINEAR_ISSUE_UNRESOLVED "
            "Linear could not find requested issue TEST-30."
        )
    }


def test_codex_session_binding_error_and_optional_ending_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session errors remain visible while optional ending failures stay advisory.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(
        codex,
        "request_for",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("BINDING_MISSING")),
    )
    result = codex.handle({"hook_event_name": "SessionStart"})
    assert "available without a task" in result["hookSpecificOutput"]["additionalContext"]
    assert "TASK_WORKSPACE_NOT_READY" not in result["hookSpecificOutput"]["additionalContext"]
    assert codex.handle({"hook_event_name": "SessionEnd"}) == {}


def test_codex_unknown_hook_has_no_lifecycle_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognized host event grants no readiness or completion.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex.core, "execute", lambda *_args: pytest.fail("executed lifecycle"))
    assert codex.handle({"hook_event_name": "FutureHostEvent"}) == {}


@pytest.mark.parametrize("operation,expected", [("diagnose", True), ("register", False)])
@pytest.mark.parametrize("tool_name", ["Bash", "exec_command"])
def test_diagnostic_bootstrap_narrows_validated_command(
    monkeypatch: pytest.MonkeyPatch, operation: str, expected: bool, tool_name: str
) -> None:
    """Only diagnosis survives operation narrowing after canonical admission.

    Args:
        monkeypatch: Fixture isolating the filesystem-dependent canonical admission.
        operation: Lifecycle operation carried by the canonical command.
        expected: Whether that operation is read-only diagnosis.
        tool_name: Supported shell transport selecting its command argument field.
    """
    # Keep parsing real while isolating the already-covered persistent identity gate.
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: True)
    request = {"operation": operation}
    command = codex.common.bootstrap_command(request, "codex")
    field = "command" if tool_name == "Bash" else "cmd"
    observed = {"tool_name": tool_name, "tool_input": {field: command}}
    assert codex.diagnostic_bootstrap(observed) is expected
    # A rejected canonical envelope must never reach operation parsing.
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    assert codex.diagnostic_bootstrap({}) is False


@pytest.mark.parametrize(
    "hook,route",
    [
        ("SubagentStart", "start"),
        ("PreToolUse", "child_pre"),
        ("PostToolUse", "child_post"),
        ("Stop", "child_end"),
        ("Interrupt", "child_end"),
        ("SessionEnd", "child_end"),
        ("SubagentStop", "child_end"),
        ("FutureChildEvent", None),
    ],
)
def test_native_child_dispatch_preserves_derived_identity_and_excludes_parent_work(
    monkeypatch: pytest.MonkeyPatch, hook: str, route: str | None
) -> None:
    """Verified child callbacks use only their own route and never parent tool accounting.

    Args:
        monkeypatch: Fixture isolating stateful lifecycle boundaries.
        hook: Native child callback under examination.
        route: Expected child handler, or None for an unknown event.
    """
    from unittest.mock import Mock

    # Retain real native identity derivation while modeling persistent child operations.
    event = {
        "hook_event_name": hook,
        "session_id": "parent",
        "agent_id": "12345678-1234-4234-8234-123456789abc",
        "cwd": str(Path.cwd()),
    }
    handlers = {
        name: Mock(return_value="reader instructions")
        for name in ("start", "child_pre", "child_post", "child_end")
    }
    for name, handler in handlers.items():
        # Each callback has its own observable persistence boundary.
        monkeypatch.setattr(codex.codex_children, name, handler)
    execute = Mock()
    monkeypatch.setattr(codex.core, "execute", execute)
    # Dispatch must preserve both native parent and independently derived child identity.
    result = codex.handle(event)
    if route is not None:
        observed = handlers[route].call_args.args[0]
        assert observed["parent_session_id"] == "parent"
        assert observed["session_id"] == "parent/agent/12345678-1234-4234-8234-123456789abc"
        assert observed["child"] is True
    # Only the selected route may run; the parent has no generic transaction here.
    assert {name: handler.call_count for name, handler in handlers.items()} == {
        name: int(name == route) for name in handlers
    }
    assert result == (
        {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": "reader instructions",
            }
        }
        if hook == "SubagentStart"
        else {}
    )
    execute.assert_not_called()


def test_invalid_child_identity_cannot_dispatch_or_fall_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed child UUIDs cannot borrow generic parent admission.

    Args:
        monkeypatch: Fixture isolating stateful lifecycle boundaries.
    """
    from unittest.mock import Mock

    # Observe both possible authority boundaries before supplying a forged identity.
    child, parent = Mock(), Mock()
    monkeypatch.setattr(codex.codex_children, "child_pre", child)
    monkeypatch.setattr(codex.core, "execute", parent)
    with pytest.raises(core.WorkspaceError) as captured:
        codex.handle(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "parent",
                "agent_id": "forged",
                "cwd": str(Path.cwd()),
            }
        )
    # Rejection occurs before any child or parent work is admitted.
    assert captured.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
    child.assert_not_called()
    parent.assert_not_called()


@pytest.mark.parametrize(
    "tool,route",
    [
        ("spawn_agent", "admit_spawn"),
        ("collaborationspawn_agent", "admit_spawn"),
        ("multi_agent_v1send_input", "admit_control"),
        ("multi_agent_v1close_agent", "admit_control"),
        ("multi_agent_v1wait_agent", "admit_control"),
        ("collaborationlist_agents", "admit_control"),
    ],
)
@pytest.mark.parametrize("phase", ["PreToolUse", "PostToolUse"])
def test_parent_collaboration_dispatch_uses_bounded_route_only(
    monkeypatch: pytest.MonkeyPatch, tool: str, route: str, phase: str
) -> None:
    """Supported native calls use adapter-specific admission or completion without duplication.

    Args:
        monkeypatch: Fixture isolating storage and lifecycle boundaries.
        tool: Observed native or legacy collaboration tool.
        route: Bounded pre-admission handler for that tool.
        phase: Native callback phase.
    """
    from unittest.mock import Mock

    # Remove unrelated startup state while retaining actual identity normalization.
    monkeypatch.setattr(codex, "startup_lookup_state", Mock(return_value=False))
    monkeypatch.setattr(codex, "bootstrap", Mock(return_value=False))
    monkeypatch.setattr(codex, "provider_gate", Mock(return_value=False))
    execute = Mock()
    monkeypatch.setattr(codex.core, "execute", execute)
    handlers = {name: Mock() for name in ("admit_spawn", "admit_control", "parent_post")}
    for name, handler in handlers.items():
        # Model only the independently tested persistent route boundary.
        monkeypatch.setattr(codex.codex_children, name, handler)
    event = {
        "hook_event_name": phase,
        "session_id": "parent",
        "cwd": str(Path.cwd()),
        "tool_name": tool,
        "tool_use_id": "native-call",
        "tool_input": {},
        "tool_response": {},
    }
    # One callback must reach exactly one bounded handler with unchanged call correlation.
    assert codex.handle(event) == {}
    selected = route if phase == "PreToolUse" else "parent_post"
    handlers[selected].assert_called_once_with(event)
    assert sum(handler.call_count for handler in handlers.values()) == 1
    execute.assert_not_called()


@pytest.mark.parametrize("route", ["preparation", "diagnosis"])
def test_required_ticket_allows_only_explicit_recovery_without_tool_start(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """Named preparation and diagnosis stay reachable before the first ticket read.

    Args:
        monkeypatch: Fixture isolating startup and persistent readiness boundaries.
        route: Exact exception recognized by its independently tested admission helper.
    """
    from unittest.mock import Mock

    # Model a required ticket marker and one explicitly validated recovery command.
    marker = Mock(return_value=True)
    diagnostic = Mock(return_value=route == "diagnosis")
    monkeypatch.setattr(codex, "startup_lookup_state", marker)
    monkeypatch.setattr(codex.common, "preparation_tool", Mock(return_value=route == "preparation"))
    monkeypatch.setattr(codex, "diagnostic_bootstrap", diagnostic)
    execute, lookup = Mock(), Mock()
    monkeypatch.setattr(codex.core, "execute", execute)
    monkeypatch.setattr(codex, "ticket_lookup", lookup)
    # Recovery admission neither consumes the provider handshake nor starts ordinary work.
    assert codex.handle({"hook_event_name": "PreToolUse", "tool_name": "Bash"}) == {}
    execute.assert_not_called()
    lookup.assert_not_called()
    if route == "preparation":
        # Preparation does not need to interpret a diagnostic command.
        diagnostic.assert_not_called()


@pytest.mark.parametrize("route", ["preparation", "bootstrap"])
def test_preparation_and_bootstrap_post_do_not_settle_unrelated_work(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """Callbacks for exempt commands cannot complete generic pending tool work.

    Args:
        monkeypatch: Fixture isolating exact command gates and lifecycle persistence.
        route: Preparation read or canonical lifecycle command.
    """
    from unittest.mock import Mock

    # These routes own no ordinary tool receipt, even if the callback has a call ID.
    monkeypatch.setattr(codex.common, "preparation_tool", Mock(return_value=route == "preparation"))
    monkeypatch.setattr(codex, "startup_lookup_state", Mock(return_value=True))
    bootstrap = Mock(return_value=route == "bootstrap")
    monkeypatch.setattr(codex, "bootstrap", bootstrap)
    execute = Mock()
    monkeypatch.setattr(codex.core, "execute", execute)
    # Dispatch must terminate before generic completion settlement.
    assert (
        codex.handle(
            {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "unrelated"}
        )
        == {}
    )
    execute.assert_not_called()
    if route == "preparation":
        # The earlier preparation exemption does not require lifecycle parsing.
        bootstrap.assert_not_called()


@pytest.mark.parametrize(
    "tool,phase,explicit_error,expected",
    [
        ("mcp__codex_apps__linear__get_issue", "PostToolUse", None, None),
        ("mcp__codex_apps__linear_get_issue", "PostToolUse", None, "PROVIDER_RESPONSE_INVALID"),
        ("mcp__codex_apps__linear__get_issue", "PreToolUse", None, "PROVIDER_RESPONSE_INVALID"),
        ("mcp__codex_apps__linear__get_issue", "PostToolUse", True, "PROVIDER_ERROR"),
    ],
)
def test_ticket_completion_normalizes_only_observed_native_success(
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    phase: str,
    explicit_error: bool | None,
    expected: str | None,
) -> None:
    """Only the exact native final envelope may omit its optional MCP error flag.

    Args:
        monkeypatch: Fixture modeling binding storage and workspace startup.
        tool: Native or legacy issue lookup spelling.
        phase: Observed callback phase.
        explicit_error: Optional explicit provider failure flag.
        expected: Expected diagnostic, or None for verified ticket success.
    """
    import json
    from unittest.mock import MagicMock, Mock

    # Model the exact durable assignment and prior provider-call correlation.
    key = core.participant_key({"host": "codex", "session_id": "parent"})
    records = {
        key + ".assignment.json": {"issue_id": "TEST-30"},
        key + ".lookup-required.json": {"issue_id": "TEST-30"},
        key + ".lookup.json": {"id": "TEST-30", "tool_id": "lookup"},
    }
    directory = MagicMock()
    directory.__enter__.return_value = directory
    directory.child.return_value = directory
    directory.json.side_effect = records.__getitem__
    directory.exists.side_effect = records.__contains__
    monkeypatch.setattr(codex.core, "repository", Mock(return_value=(Path.cwd(), None, None)))
    monkeypatch.setattr(codex.core.Directory, "absolute", Mock(return_value=directory))
    startup = Mock(return_value={"participant_id": key, "coordinator": key})
    monkeypatch.setattr(codex.startup, "start", startup)
    # Keep the actual provider parser so normalization cannot accept an arbitrary issue.
    issue = {
        "id": "12345678-1234-4234-8234-123456789abc",
        "identifier": "TEST-30",
        "title": "Assigned task",
    }
    response = {"content": [{"type": "text", "text": json.dumps(issue)}]}
    if explicit_error is not None:
        # An explicit provider failure must survive native normalization.
        response["isError"] = explicit_error
    event = {
        "hook_event_name": phase,
        "tool_name": tool,
        "cwd": str(Path.cwd()),
        "session_id": "parent",
        "tool_use_id": "lookup",
        "tool_input": {"id": "TEST-30"},
        "tool_response": response,
    }
    # Verify successful startup or exact rejection, including marker cleanup effects.
    if expected is None:
        result = codex.ticket_lookup(event, complete=True)
        assert "TASK_WORKSPACE_READY" in result["systemMessage"]
        assert startup.call_args.args[1]["uuid"] == issue["id"]
        assert startup.call_args.args[1]["id"] == "TEST-30"
        assert {call.args[0] for call in directory.unlink.call_args_list} == {
            key + ".lookup.json",
            key + ".lookup-required.json",
        }
    else:
        with pytest.raises(core.WorkspaceError) as captured:
            codex.ticket_lookup(event, complete=True)
        assert captured.value.code == expected
        startup.assert_not_called()
        directory.unlink.assert_called_once_with(key + ".lookup.json")
    # Provider output remains unchanged and cannot be treated as a mutable authority channel.
    assert response.get("isError") is explicit_error
    directory.put.assert_not_called()
