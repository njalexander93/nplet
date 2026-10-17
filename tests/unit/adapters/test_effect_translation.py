"""Check native normalization and admission receipts independently of lifecycle dispatch."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core
from tests.types import JsonObject

pytestmark = pytest.mark.unit


@pytest.fixture
def native(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JsonObject:
    """Supply a complete native identity while isolating Git repository discovery.

    Args:
        tmp_path: Absolute checkout root used by target classification.
        monkeypatch: Scoped discovery replacement.

    Returns:
        A native callback with actual identity fields and no lifecycle setup.
    """
    # Repository discovery is a separate integration boundary from normalization.
    monkeypatch.setattr(core, "repository", lambda _path: (tmp_path, None, []))
    return {
        "cwd": str(tmp_path),
        "session_id": "native-session",
        "tool_use_id": "native-call",
        "tool_name": "Read",
        "tool_input": {"file_path": "src/main.py"},
    }


@pytest.mark.parametrize(
    "tool,args,expected",
    [
        ("Read", {"file_path": "src/main.py"}, "read"),
        ("Glob", {"pattern": "**/*.py"}, "read"),
        ("Grep", {"pattern": "def"}, "read"),
        ("WebSearch", {"query": "specification"}, "read"),
        ("WebFetch", {"url": "https://example.invalid"}, "read"),
        ("webrun", {"search_query": []}, "read"),
        ("web.run", {}, "read"),
        ("ToolSearch", {}, "admin"),
        ("AskUserQuestion", {}, "admin"),
        ("request_user_input", {}, "admin"),
        ("request_user_input_async", {}, "admin"),
        ("Write", {"file_path": "notes.md"}, "note"),
        ("Edit", {"path": "notes.txt"}, "note"),
        ("Delete", {"target_file": "notes.rst"}, "note"),
        ("NotebookEdit", {"notebook_path": "notebook.ipynb"}, "protected"),
        ("Write", {"file_path": "notes.md", "path": "src/main.py"}, "protected"),
        ("Write", {"file_path": "notes.md", "executable": True}, "protected"),
        ("Write", {}, "unknown"),
        ("Bash", {"command": "pwd"}, "read"),
        ("Bash", {"command": "pwd", "extra": True}, "unknown"),
        ("exec_command", {"cmd": "pwd", "shell": "/bin/sh", "login": False}, "read"),
        ("exec_command", {"cmd": "pwd", "shell": "custom-interpreter"}, "unknown"),
        ("exec_command", {"cmd": "pwd"}, "unknown"),
        ("exec_command", {"cmd": "pwd", "login": False}, "unknown"),
        ("exec_command", {"cmd": "pwd", "shell": "/bin/sh"}, "unknown"),
        ("exec_command", {"cmd": "pwd", "login": True}, "unknown"),
        ("exec_command", {"cmd": "pwd", "tty": True}, "unknown"),
        ("exec_command", {"cmd": "pwd", "workdir": "relative"}, "unknown"),
        ("exec_command", {"cmd": "pwd", "workdir": 2}, "unknown"),
        ("exec_command", {"cmd": "python inspect.py"}, "unknown"),
        ("opaque_wrapper", {"read_only": True}, "unknown"),
    ],
)
def test_native_operation_normalizes_complete_effect(
    native: JsonObject, tool: str, args: JsonObject, expected: str
) -> None:
    """Assert independent effect expectations for native tool contracts.

    Args:
        native: Complete identity fixture.
        tool: Observed native tool spelling.
        args: Untrusted tool arguments.
        expected: Expected effect from the task boundary.
    """
    assert (
        common.effect_decision({**native, "tool_name": tool, "tool_input": args}, "codex").effect
        == expected
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, []),
        ("not a patch", []),
        ("*** Begin Patch\n*** Add File: notes.md\n+text\n*** End Patch", ["notes.md"]),
        (
            "*** Begin Patch\n*** Update File: notes.md\n*** Move to: src/new.py\n"
            "@@\n-old\n+new\n*** End of File\n*** End Patch",
            ["notes.md", "src/new.py"],
        ),
        ("*** Begin Patch\n*** Delete File: notes.md\n*** End Patch", ["notes.md"]),
        ("*** Begin Patch\n*** Add File: \n*** End Patch", []),
        ("*** Begin Patch\n*** Move to: new.md\n*** End Patch", []),
        ("*** Begin Patch\n*** Update File: old.md\n*** Move to: \n*** End Patch", []),
        ("*** Begin Patch\n*** Chmod: notes.md\n*** End Patch", []),
    ],
)
def test_patch_contract_enumerates_all_targets(value: object, expected: list[str]) -> None:
    """Reject unfamiliar patch directives instead of guessing a safe destination.

    Args:
        value: Native patch text or malformed value.
        expected: Independently selected complete endpoint list.
    """
    assert common.patch_targets(value) == expected


@pytest.mark.parametrize(
    "provider,operation,expected",
    [
        ("linear__", "get_issue", "read"),
        ("linear_", "fetch", "read"),
        ("linear__", "list_templates", "read"),
        ("linear__", "save_document", "admin"),
        ("linear__", "save_issue", "admin"),
        ("linear__", "execute_code", "unknown"),
        ("github_", "fetch_file", "read"),
        ("github_", "get_pr_info", "read"),
        ("github_", "fetch_pr", "read"),
        ("github_", "fetch_pr_patch", "read"),
        ("github_", "get_pr_diff", "read"),
        ("github_", "fetch_pr_comments", "read"),
        ("github_", "list_pull_request_reviews", "read"),
        ("github__", "list_pull_request_reviews", "read"),
        ("github_", "list_pull_request_review_threads", "read"),
        ("github__", "list_pull_request_review_threads", "read"),
        ("github__", "create_issue", "admin"),
        ("github_", "update_issue", "admin"),
        ("github_", "create_file", "unknown"),
        ("github_", "update_file", "unknown"),
        ("github_", "delete_file", "unknown"),
        ("github_", "merge_pull_request", "unknown"),
        ("foreign__", "get_issue", "unknown"),
    ],
)
def test_codex_provider_contracts_are_exact(
    native: JsonObject, provider: str, operation: str, expected: str
) -> None:
    """Separate known project administration from file writes and opaque providers.

    Args:
        native: Complete native callback fixture.
        provider: Exact native qualified provider segment.
        operation: Exact operation name.
        expected: Expected effect class.
    """
    value = {
        **native,
        "tool_name": "mcp__codex_apps__" + provider + operation,
        "tool_input": {"id": "OTHER-2"},
    }
    assert common.effect_decision(value, "codex").effect == expected


@pytest.mark.parametrize(
    "operation,expected",
    [("list_threads", "read"), ("set_thread_title", "admin"), ("create_worktree", "unknown")],
)
def test_codex_app_contracts_do_not_exempt_implementation(
    native: JsonObject, operation: str, expected: str
) -> None:
    """Use exact app operations without a blanket app-server exemption.

    Args:
        native: Complete native callback fixture.
        operation: Exact app operation.
        expected: Expected shared effect.
    """
    assert (
        common.effect_decision(
            {**native, "tool_name": "mcp__codex_app__" + operation}, "codex"
        ).effect
        == expected
    )


@pytest.mark.parametrize(
    "operation,expected", [("get_status", "read"), ("set_monitor", "protected")]
)
def test_claude_monitor_retains_automatic_correction_gate(
    native: JsonObject, operation: str, expected: str
) -> None:
    """Pin app provenance while treating automatic correction as implementation.

    Args:
        native: Complete native callback fixture.
        operation: Pinned PR-monitor operation.
        expected: Expected local effect.
    """
    value = {
        **native,
        "tool_name": "mcp__ccd_pr__" + operation,
        "mcp_server": {"name": "ccd_pr", "source": "sdk"},
        "tool_input": {"auto_fix": True},
    }
    assert common.effect_decision(value, "claude-code").effect == expected


@pytest.mark.parametrize(
    "changes,code",
    [({"session_id": None}, "INVALID_REQUEST"), ({"tool_use_id": None}, "INVALID_REQUEST")],
)
def test_effect_translation_requires_actual_native_identity(
    native: JsonObject, changes: JsonObject, code: str
) -> None:
    """Reject incomplete native identities before any task-free classification.

    Args:
        native: Complete native callback fixture.
        changes: Missing identity override.
        code: Expected contractual failure code.
    """
    with pytest.raises(core.WorkspaceError) as failure:
        common.effect_decision({**native, **changes}, "codex")
    assert failure.value.code == code


@pytest.mark.parametrize(
    "code,absent",
    [
        ("REGISTRATION_REQUIRED", True),
        ("BINDING_MISSING", True),
        ("REPOSITORY_MISMATCH", False),
        ("RECOVERY_REQUIRED", False),
    ],
)
def test_pending_lookup_distinguishes_absence_from_corruption(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, code: str, absent: bool
) -> None:
    """Ignore only expected missing setup when deciding whether a free post can return.

    Args:
        native: Complete native callback fixture.
        monkeypatch: Isolate the registration/binding boundary.
        code: Contractual registration result.
        absent: Whether this code denotes ordinary missing task setup.
    """
    # Produce one precise binding failure and preserve all corruption diagnostics.
    monkeypatch.setattr(
        common, "request_for", lambda *_args: (_ for _ in ()).throw(core.WorkspaceError(code))
    )
    if absent:
        assert common.pending_tool(native, "codex") is False
    else:
        with pytest.raises(core.WorkspaceError) as failure:
            common.pending_tool(native, "codex")
        assert failure.value.code == code


@pytest.mark.parametrize(
    "pending,expected",
    [
        ({"native-call": {"status": "pending"}}, True),
        ({"foreign-call": {"status": "pending"}}, False),
    ],
)
def test_pending_lookup_uses_exact_call_identity(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, pending: JsonObject, expected: bool
) -> None:
    """A free callback cannot settle another operation's pending native call.

    Args:
        native: Complete native callback fixture.
        monkeypatch: Isolate committed lifecycle state.
        pending: Existing participant pending map.
        expected: Whether this exact native call is pending.
    """
    # Keep participant and call identities independent of the requested effect.
    request = {"host": "codex", "session_id": native["session_id"]}
    key = core.participant_key(request)
    monkeypatch.setattr(common, "request_for", lambda *_args: request)
    monkeypatch.setattr(
        common, "_issue_state", lambda _request: {"participants": {key: {"pending": pending}}}
    )
    assert common.pending_tool(native, "codex") is expected


@pytest.fixture
def markers(native: JsonObject, monkeypatch: pytest.MonkeyPatch) -> dict[str, JsonObject]:
    """Isolate safe native filesystem reads without claiming native-handle coverage.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Scoped Directory boundary replacement.

    Returns:
        Mutable marker values read through a signature-constrained directory double.
    """
    # Filesystem handle identity belongs to integration tests; these cases assert marker logic.
    values: dict[str, JsonObject] = {}
    directory = MagicMock(spec=core.Directory)
    directory.__enter__.return_value = directory
    directory.child.return_value = directory
    directory.exists.side_effect = lambda name: name in values
    directory.json.side_effect = lambda name: values[name]
    monkeypatch.setattr(core.Directory, "absolute", lambda _root: directory)
    return values


def test_ticket_receipt_matches_only_reserved_call(
    native: JsonObject, markers: dict[str, JsonObject], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not fabricate ticket completion for a foreign or unreserved research callback.

    Args:
        native: Complete native identity fixture.
        markers: Isolated existing marker bytes.
        monkeypatch: Ticket-required boundary replacement.
    """
    # The exact selected session and call ID must both match the existing receipt.
    monkeypatch.setattr(common, "lookup_required", lambda *_args: True)
    key = core.participant_key({"host": "codex", "session_id": native["session_id"]})
    assert common.ticket_callback(native, "codex") is False
    markers[key + ".lookup.json"] = {"tool_id": "foreign-call"}
    assert common.ticket_callback(native, "codex") is False
    markers[key + ".lookup.json"] = {"tool_id": "native-call"}
    assert common.ticket_callback(native, "codex") is True
    monkeypatch.setattr(common, "lookup_required", lambda *_args: False)
    assert common.ticket_callback(native, "codex") is False


@pytest.mark.parametrize(
    "host,tool,args,specific,expected",
    [
        ("codex", "mcp__codex_apps__linear__get_issue", {"id": "TEST-1"}, False, True),
        ("codex", "mcp__codex_apps__linear_get_issue", {"id": "OTHER-2"}, False, False),
        ("codex", "Read", {}, False, False),
        ("cursor", "MCP:get_issue", {"id": "TEST-1"}, False, True),
        ("cursor", "Read", {}, False, False),
        ("cursor", "get_issue", '{"id":"TEST-1"}', True, True),
        ("claude-code", "mcp__linear-server__get_issue", {"id": "TEST-1"}, False, True),
        ("claude-code", "Read", {}, False, False),
    ],
)
def test_exact_ticket_selection_leaves_research_unbound(
    native: JsonObject,
    markers: dict[str, JsonObject],
    host: str,
    tool: str,
    args: object,
    specific: bool,
    expected: bool,
) -> None:
    """Reserve startup only for the exact selected-ticket native argument object.

    Args:
        native: Complete native identity fixture.
        markers: Isolated assignment bytes.
        host: Explicit adapter identity.
        tool: Native issue-read or research tool.
        args: Structured generic or serialized specific native arguments.
        specific: Whether Cursor supplied its MCP-specific callback.
        expected: Whether this call belongs to selected-ticket startup.
    """
    # Provider provenance and the assignment independently constrain startup selection.
    key = core.participant_key({"host": host, "session_id": native["session_id"]})
    markers[key + ".assignment.json"] = {"issue_id": "TEST-1"}
    value = {
        **native,
        "tool_name": tool,
        "tool_input": args,
        "mcp_server": {"name": "linear-server", "source": "user"},
    }
    assert common.selected_ticket(value, host, specific) is expected


@pytest.mark.parametrize(
    "host,tool,args,metadata,expected",
    [
        (
            "codex",
            "apply_patch",
            {"command": "*** Begin Patch\n*** Add File: notes.md\n+text\n*** End Patch"},
            {},
            "note",
        ),
        (
            "codex",
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Delete File: src/main.py\n*** End Patch"},
            {},
            "protected",
        ),
        ("codex", "apply_patch", {"command": "patch", "opaque": True}, {}, "unknown"),
        ("cursor", "Shell", {"command": "pwd"}, {}, "read"),
        ("cursor", "MCP:save_issue", {}, {}, "admin"),
        ("cursor", "mcp__foreign__get_issue", {}, {}, "unknown"),
        (
            "claude-code",
            "mcp__linear-server__get_issue",
            {},
            {"mcp_server": {"name": "linear-server", "source": "user"}},
            "read",
        ),
        ("claude-code", "mcp__foreign__get_issue", {}, {}, "unknown"),
    ],
)
def test_native_remaining_translation_shapes(
    native: JsonObject, host: str, tool: str, args: JsonObject, metadata: JsonObject, expected: str
) -> None:
    """Cover patch, native-shell and server-metadata normalization boundaries.

    Args:
        native: Complete native identity fixture.
        host: Explicit adapter identity.
        tool: Native tool name.
        args: Native argument object.
        metadata: Native provenance fields independent of arguments.
        expected: Independent expected effect class.
    """
    assert (
        common.effect_decision(
            {**native, **metadata, "tool_name": tool, "tool_input": args}, host
        ).effect
        == expected
    )


def test_translation_handles_scalar_args_and_absolute_shell_override(native: JsonObject) -> None:
    """Keep malformed arguments opaque and normalize explicit absolute shell cwd.

    Args:
        native: Complete native identity fixture.
    """
    # Malformed arguments cannot acquire a trusted read family from their tool name.
    assert (
        common.effect_decision({**native, "tool_input": "read_only"}, "codex").effect == "unknown"
    )
    # A valid override is observed directly; it does not change the canonical policy root.
    value = {
        **native,
        "tool_name": "exec_command",
        "tool_input": {"cmd": "pwd", "login": False, "shell": "/bin/sh", "workdir": native["cwd"]},
    }
    assert common.effect_decision(value, "codex").effect == "read"


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"tool_name": "execute_code"}, "HOST_UNSUPPORTED_PROVIDER"),
        ({"mcp_server_name": "foreign"}, "HOST_UNSUPPORTED_PROVIDER"),
        ({"mcp_server_url": "https://example.invalid"}, "HOST_UNSUPPORTED_PROVIDER"),
        ({"tool_input": {}}, "INVALID_REQUEST"),
        ({"tool_input": "[]"}, "INVALID_REQUEST"),
    ],
)
def test_cursor_specific_provider_gate_rejects_unverified_shapes(
    changes: JsonObject, code: str
) -> None:
    """Require exact server, URL, operation and serialized object arguments.

    Args:
        changes: One invalid native specific-callback field.
        code: Expected contractual diagnostic.
    """
    # A generic provisional permit cannot override this blocking provenance gate.
    value = {
        "tool_name": "save_issue",
        "tool_input": '{"id":"OTHER-2"}',
        "mcp_server_name": "linear-server",
        "mcp_server_url": "https://mcp.linear.app/mcp",
    }
    with pytest.raises(core.WorkspaceError) as failure:
        common.cursor_provider({**value, **changes})
    assert failure.value.code == code


def test_cursor_specific_provider_gate_accepts_official_readonly_url(native: JsonObject) -> None:
    """Accept the pinned read-only Linear endpoint with parsed object arguments.

    Args:
        native: Complete native identity and repository discovery fixture.
    """
    common.cursor_provider(
        {
            **native,
            "tool_name": "get_issue",
            "tool_input": "{}",
            "mcp_server_name": "linear",
            "url": "https://mcp.linear.app/mcp/readonly",
        }
    )


@pytest.mark.parametrize("child", [False, True])
def test_native_pre_task_free_call_has_no_lifecycle_start(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, child: bool
) -> None:
    """Admit free native work while requiring an actual child's own existing binding.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate lifecycle I/O and native synchronous transport checks.
        child: Whether the observed call belongs to a previously joined child.
    """
    # No task-free pre event may create a pending lifecycle operation.
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "native_tool", lambda *_args: "Read")
    monkeypatch.setattr(
        core, "execute", lambda _request: pytest.fail("task-free lifecycle mutation")
    )
    request = {"host": "claude-code", "session_id": native["session_id"]}
    key = core.participant_key(request)
    monkeypatch.setattr(
        common,
        "request_for",
        lambda *_args: request if child else pytest.fail("task-free binding lookup"),
    )
    monkeypatch.setattr(common, "_issue_state", lambda _request: {"participants": {key: {}}})
    common.native_pre({**native, "child": child}, "claude-code")


@pytest.mark.parametrize(
    "code,expected",
    [
        ("REGISTRATION_REQUIRED", "UNKNOWN_SHELL_EFFECT"),
        ("BINDING_MISSING", "UNKNOWN_SHELL_EFFECT"),
        ("REPOSITORY_MISMATCH", "REPOSITORY_MISMATCH"),
    ],
)
def test_native_pre_unknown_execution_preserves_corruption(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, code: str, expected: str
) -> None:
    """Map normal absence to effect guidance while preserving foreign-state errors.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate the binding boundary.
        code: Actual lifecycle lookup failure.
        expected: Exact diagnostic the native adapter must expose.
    """
    # Opaque execution must run neither native work nor a lifecycle start after denial.
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        common, "request_for", lambda *_args: (_ for _ in ()).throw(core.WorkspaceError(code))
    )
    with pytest.raises(core.WorkspaceError) as failure:
        common.native_pre(
            {**native, "tool_name": "Bash", "tool_input": {"command": "python inspect.py"}},
            "claude-code",
        )
    assert failure.value.code == expected


@pytest.mark.parametrize("target", ["notes.md", "source.py"])
def test_task_free_native_post_does_not_fabricate_completion(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """Return from an unrecorded callback without settling any lifecycle call.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate existing admission and completion boundaries.
        target: Current note or implementation destination after an unrecorded admission.
    """
    # Current classification cannot give an unrecorded callback settlement authority.
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "pending_tool", lambda *_args: False)
    monkeypatch.setattr(core, "execute", lambda _request: pytest.fail("fabricated completion"))
    common.native_post(
        {**native, "tool_name": "Write", "tool_input": {"file_path": target}},
        "claude-code",
        failed=False,
    )


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({"session_id": 71}, "read"),
        ({"session_id": "handle-1", "chars": ""}, "read"),
        ({"session_id": 71, "chars": "input"}, "unknown"),
        ({"session_id": 71, "chars": None}, "unknown"),
        ({"session_id": True}, "unknown"),
        ({"session_id": ""}, "unknown"),
        ({"session_id": 71, "opaque": True}, "unknown"),
        ({}, "unknown"),
    ],
)
def test_empty_input_polling_has_a_bounded_read_contract(
    native: JsonObject, arguments: JsonObject, expected: str
) -> None:
    """Distinguish read-only transport polling from process-driving input.

    Args:
        native: Complete native identity fixture.
        arguments: Native polling fields.
        expected: Independent expected effect.
    """
    assert (
        common.effect_decision(
            {**native, "tool_name": "write_stdin", "tool_input": arguments}, "codex"
        ).effect
        == expected
    )


@pytest.mark.parametrize(
    "arguments,expected,code",
    [
        ({"session_id": 71}, True, None),
        ({"session_id": 72}, False, None),
        ({"session_id": True}, False, "ASYNC_HANDLE_CONFLICT"),
        ({"session_id": ""}, False, "INVALID_REQUEST"),
        (None, False, "INVALID_REQUEST"),
    ],
)
def test_pending_poll_lookup_requires_participant_local_handle(
    native: JsonObject,
    monkeypatch: pytest.MonkeyPatch,
    arguments: JsonObject | None,
    expected: bool,
    code: str | None,
) -> None:
    """Retain protected async correlation while unrelated task-free polling stays untracked.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate committed lifecycle state.
        arguments: Native polling fields or malformed input.
        expected: Whether the protected original's handle matches.
        code: Expected failure for malformed identity, or None.
    """
    # A poll can borrow only this participant's exact existing handle, never another call.
    request = {"host": "codex", "session_id": native["session_id"]}
    key = core.participant_key(request)
    monkeypatch.setattr(common, "request_for", lambda *_args: request)
    monkeypatch.setattr(
        common,
        "_issue_state",
        lambda _request: {
            "participants": {key: {"pending": {"original": {"status": "unknown", "handle": "71"}}}}
        },
    )
    value = {**native, "tool_name": "write_stdin", "tool_input": arguments}
    if code is None:
        assert common.pending_tool(value, "codex") is expected
    else:
        with pytest.raises(core.WorkspaceError) as failure:
            common.pending_tool(value, "codex")
        assert failure.value.code == code


@pytest.mark.parametrize("host", ["codex", "claude-code", "cursor"])
def test_unknown_recovery_names_supported_task_free_route(host: str) -> None:
    """Explain opaque execution without implying that all research needs a task.

    Args:
        host: Explicit adapter identity for native recovery details.
    """
    result = common.recovery("UNKNOWN_SHELL_EFFECT", host)
    assert "supported structured read/search" in result
    if host == "codex":
        assert 'login:false and shell:"/bin/sh"' in result
    else:
        assert "exec_command" not in result


def test_stale_native_context_keeps_precise_existing_task_error(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep stale-task recovery precise while stating that research remains available.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate an existing stale binding's readiness error.
    """
    monkeypatch.setattr(
        common,
        "request_for",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("SOURCE_STALE")),
    )
    result = common.native_context(native, "claude-code")
    assert "SOURCE_STALE" in result
    assert "ordinary notes remain available" in result


@pytest.mark.parametrize("pending_ticket", [False, True])
def test_codex_free_native_pre_and_post_do_not_start_or_settle(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, pending_ticket: bool
) -> None:
    """Keep task-free research independent of absent or pending ticket state.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate lifecycle state while keeping real effect translation.
        pending_ticket: Whether an explicit ticket marker is awaiting its exact read.
    """
    from nplet.adapters import codex

    # Neither the unbound route nor pending-ticket research may reach core tool-start/complete.
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: pending_ticket)
    monkeypatch.setattr(common, "selected_ticket", lambda *_args: False)
    monkeypatch.setattr(common, "preparation_tool", lambda *_args: False)
    monkeypatch.setattr(codex, "diagnostic_bootstrap", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(common, "pending_tool", lambda *_args: False)
    monkeypatch.setattr(core, "execute", lambda _request: pytest.fail("task-free core operation"))
    assert codex.handle({**native, "hook_event_name": "PreToolUse"}) == {}
    assert codex.handle({**native, "hook_event_name": "PostToolUse"}) == {}


@pytest.mark.parametrize("effect_code", ["REGISTRATION_REQUIRED", "REPOSITORY_MISMATCH"])
def test_codex_unknown_pre_preserves_invalid_existing_identity(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, effect_code: str
) -> None:
    """Give normal absence effect guidance without hiding a foreign checkout failure.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate native pre-tool binding checks.
        effect_code: Contractual registration or foreign-state diagnostic.
    """
    from nplet.adapters import codex

    # Readiness lookup is the only failed boundary; no tool-start may run afterward.
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: False)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(
        codex, "request_for", lambda *_args: (_ for _ in ()).throw(core.WorkspaceError(effect_code))
    )
    value = {
        **native,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "python inspect.py"},
    }
    response = codex.handle(value)
    expected = "UNKNOWN_SHELL_EFFECT" if effect_code == "REGISTRATION_REQUIRED" else effect_code
    assert expected in str(response)


@pytest.mark.parametrize(
    "tool,packet_result",
    [
        ("Read", {"ok": True}),
        ("Bash", {"ok": False, "code": "SOURCE_STALE"}),
        ("Bash", {"ok": True, "code": "OK", "references": []}),
    ],
)
def test_joined_codex_child_inspection_keeps_identity_without_readiness(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, tool: str, packet_result: JsonObject
) -> None:
    """Preserve joined identity and source-delivery rules while admitting ordinary reads.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate cohort and packet I/O.
        tool: Direct read or bounded native shell read.
        packet_result: Packet freshness result used only by the shell source-delivery route.
    """
    from contextlib import nullcontext

    from nplet.adapters import codex_children

    # A verified child cohort remains mandatory before any free effect exemption.
    key = core.participant_key({"host": "codex", "session_id": native["session_id"]})
    monkeypatch.setattr(codex_children, "parent_request", lambda _event: {})
    monkeypatch.setattr(
        codex_children,
        "registry",
        lambda _parent: nullcontext((None, "registry", {"children": {key: {"status": "joined"}}})),
    )
    monkeypatch.setattr(common, "canonical_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "request_for", lambda *_args: {})
    monkeypatch.setattr(core, "execute", lambda _request: packet_result)
    monkeypatch.setattr(codex_children, "source_paths", lambda *_args: {})
    codex_children.child_pre(
        {
            **native,
            "tool_name": tool,
            "tool_input": {"command": "pwd"} if tool == "Bash" else {"file_path": "source.py"},
        }
    )


@pytest.mark.parametrize("host", ["claude-code", "cursor"])
def test_native_pending_ticket_routes_unrelated_research_to_free_admission(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """Keep unrelated research separate from exact selected-ticket pre/post handling.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate host envelope and native admission boundaries.
        host: Claude or Cursor native adapter.
    """
    from nplet.adapters import claude, cursor

    # A pending Task is preserved while the actual research call uses normal free handling.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.setattr(common, "native_identity", lambda value, _host: value)
    monkeypatch.setattr(common, "lookup_required", lambda *_args: True)
    monkeypatch.setattr(common, "selected_ticket", lambda *_args: False)
    monkeypatch.setattr(common, "preparation_tool", lambda *_args: False)
    monkeypatch.setattr(common, "ticket_callback", lambda *_args: False)
    observed: list[str] = []
    monkeypatch.setattr(common, "native_pre", lambda *_args: observed.append("pre"))
    monkeypatch.setattr(common, "native_post", lambda *_args, **_kwargs: observed.append("post"))
    adapter = claude if host == "claude-code" else cursor
    pre_name = "PreToolUse" if host == "claude-code" else "preToolUse"
    post_name = "PostToolUse" if host == "claude-code" else "postToolUse"
    assert adapter.handle({**native, "hook_event_name": pre_name}) in ({}, {"permission": "allow"})
    assert adapter.handle({**native, "hook_event_name": post_name}) == {}
    assert observed == ["pre", "post"]


def test_cursor_handler_requires_specific_provider_gate(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route non-startup MCP execution through the separate verified-server gate.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate envelope translation and exact provider-policy call.
    """
    from nplet.adapters import cursor

    # The generic permit is insufficient: the server-specific event has its own blocking gate.
    monkeypatch.setattr(common, "native_identity", lambda value, _host: value)
    monkeypatch.setattr(common, "lookup_required", lambda *_args: False)
    observed: list[JsonObject] = []
    monkeypatch.setattr(common, "cursor_provider", lambda value: observed.append(value))
    value = {**native, "hook_event_name": "beforeMCPExecution"}
    assert cursor.handle(value) == {"permission": "allow"}
    assert observed == [value]


@pytest.mark.parametrize(
    "host,name,server,expected",
    [
        (
            "claude-code",
            "mcp__ccd_turn__update_plan",
            {"name": "ccd_turn", "source": "sdk"},
            "update_plan",
        ),
        ("claude-code", "mcp__ccd_turn__wrap_up", {"name": "ccd_turn", "source": "sdk"}, "wrap_up"),
        ("codex", "mcp__ccd_turn__update_plan", {"name": "ccd_turn", "source": "sdk"}, None),
        ("cursor", "mcp__ccd_turn__wrap_up", {"name": "ccd_turn", "source": "sdk"}, None),
        ("claude-code", "mcp__foreign__update_plan", {"name": "ccd_turn", "source": "sdk"}, None),
        ("claude-code", None, {"name": "ccd_turn", "source": "sdk"}, None),
        ("claude-code", "mcp__ccd_turn__execute", {"name": "ccd_turn", "source": "sdk"}, None),
        ("claude-code", "mcp__ccd_turn__update_plan", None, None),
        ("claude-code", "mcp__ccd_turn__update_plan", {"name": "foreign", "source": "sdk"}, None),
        ("claude-code", "mcp__ccd_turn__update_plan", {"name": "ccd_turn", "source": "user"}, None),
        ("claude-code", "mcp__ccd_turn__update_plan", {"name": "ccd_turn"}, None),
    ],
)
def test_desktop_turn_ui_requires_exact_native_contract(
    host: str, name: str | None, server: JsonObject | None, expected: str | None
) -> None:
    """Never infer turn-UI administration from tool names or untrusted annotations.

    Args:
        host: Explicit native adapter identity.
        name: Observed qualified tool name or missing value.
        server: Observed native server provenance or missing value.
        expected: The pinned operation, or None for unsupported provenance.
    """
    assert common._app_turn_tool({"tool_name": name, "mcp_server": server}, host) == expected


@pytest.mark.parametrize("operation", ["update_plan", "wrap_up"])
def test_desktop_turn_ui_is_task_free_without_pending_work(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Keep the built-in plan and recap UI usable before a task exists.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate lifecycle boundaries that task-free UI must not mutate.
        operation: Exact pinned UI operation.
    """
    # Action buttons remain offers; their names do not execute source changes here.
    value = {
        **native,
        "tool_name": "mcp__ccd_turn__" + operation,
        "mcp_server": {"name": "ccd_turn", "source": "sdk"},
        "tool_input": {"recap": "Planning only", "actions": [{"action": "fix_ci"}]},
    }
    assert common.effect_decision(value, "claude-code").effect == "admin"
    assert common.native_tool(value, "claude-code") == value["tool_name"]
    monkeypatch.setattr(common, "pending_tool", lambda *_args: False)
    monkeypatch.setattr(core, "execute", lambda _request: pytest.fail("UI created lifecycle state"))
    common.native_pre(value, "claude-code")
    common.native_post(value, "claude-code", failed=False)


@pytest.mark.parametrize("operation", ["update_plan", "wrap_up"])
def test_desktop_turn_ui_does_not_grant_child_provider_authority(
    native: JsonObject, operation: str
) -> None:
    """Retain the child provider boundary for parent-only conversation UI.

    Args:
        native: Complete native identity fixture.
        operation: Exact pinned app UI operation.
    """
    value = {
        **native,
        "child": True,
        "tool_name": "mcp__ccd_turn__" + operation,
        "mcp_server": {"name": "ccd_turn", "source": "sdk"},
    }
    with pytest.raises(core.WorkspaceError) as failure:
        common.native_tool(value, "claude-code")
    assert failure.value.code == "HOST_UNSUPPORTED_PROVIDER"


@pytest.mark.parametrize("code", ["ASYNC_HANDLE_CONFLICT", "INVALID_REQUEST"])
def test_codex_post_pending_lookup_error_remains_advisory(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """Report invalid completion handles without raising or settling another operation.

    Args:
        native: Complete native identity fixture.
        monkeypatch: Isolate the exact failing pending-correlation boundary.
        code: Contractual malformed-handle diagnostic.
    """
    from nplet.adapters import codex

    # The post-hook must preserve uncertainty when its new pre-classification lookup fails.
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        common, "pending_tool", lambda *_args: (_ for _ in ()).throw(core.WorkspaceError(code))
    )
    monkeypatch.setattr(
        core, "execute", lambda _request: pytest.fail("invalid handle settled work")
    )
    result = codex.handle(
        {
            **native,
            "hook_event_name": "PostToolUse",
            "tool_name": "write_stdin",
            "tool_input": {"session_id": True},
        }
    )
    assert result == {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + code}


@pytest.mark.parametrize("host", ["codex", "claude-code"])
@pytest.mark.parametrize("pending_ticket", [False, True])
def test_exact_native_windows_preparation_read_has_consistent_pre_post_effect(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, host: str, pending_ticket: bool
) -> None:
    """Honor the existing literal governing-read contract across admission and completion.

    Args:
        native: Complete native identity with isolated repository discovery.
        monkeypatch: Model only the native path boundary and absent tracked admission.
        host: Adapter whose existing preparation contract accepts literal Bash reads.
        pending_ticket: Whether the selected-ticket transition remains outstanding.
    """
    import shlex

    from nplet.adapters import claude, codex

    # Model Windows path identity without replacing the shared preparation parser or policy.
    path = r"C:\checkout\AGENTS.md"
    monkeypatch.setattr(
        common, "_preparation_path", lambda _event, value, exact=False: exact and value == path
    )
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    monkeypatch.setattr(common, "lookup_required", lambda *_args: pending_ticket)
    monkeypatch.setattr(common, "ticket_callback", lambda *_args: False)
    monkeypatch.setattr(common, "pending_tool", lambda *_args: False)
    monkeypatch.setattr(codex, "startup_lookup_state", lambda *_args: pending_ticket)
    monkeypatch.setattr(codex, "bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(codex, "provider_gate", lambda *_args: False)
    monkeypatch.setattr(
        core, "execute", lambda _request: pytest.fail("untracked governing read used lifecycle")
    )
    value = {
        **native,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "cat " + shlex.quote(path)},
    }
    assert common.preparation_tool(value, host) is True
    assert common.effect_decision(value, host).effect == "read"
    adapter = codex if host == "codex" else claude
    assert adapter.handle(value) == {}
    # Valid native completion remains untracked instead of requiring a binding after the fact.
    completed = {
        **value,
        "hook_event_name": "PostToolUse",
        "tool_response": {"stdout": "source", "stderr": "", "interrupted": False},
    }
    assert adapter.handle(completed) == {}


@pytest.mark.parametrize(
    "command",
    [
        r"cat 'C:\checkout\other.md'",
        r"cat 'C:\checkout\AGENTS.md' && python mutate.py",
        r"cat 'C:\checkout\AGENTS.md' > output.txt",
    ],
)
def test_preparation_normalization_does_not_exempt_unknown_windows_shell(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """Keep non-governing targets and composition outside the literal preparation contract.

    Args:
        native: Complete native identity with isolated repository discovery.
        monkeypatch: Model only exact Windows governing-path identity.
        command: Unsupported Windows-spelled shell input.
    """
    # Exact path admission does not widen the general shell grammar or composed execution.
    monkeypatch.setattr(
        common,
        "_preparation_path",
        lambda _event, value, exact=False: exact and value == r"C:\checkout\AGENTS.md",
    )
    value = {**native, "tool_name": "Bash", "tool_input": {"command": command}}
    assert common.preparation_tool(value, "claude-code") is False
    assert common.effect_decision(value, "claude-code").requires_task is True


def test_recorded_native_admission_precedes_preparation_read_classification(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain exact existing work even when completion now matches a trusted governing read.

    Args:
        native: Complete native identity with isolated repository discovery.
        monkeypatch: Isolate existing admission and record the actual completion operation.
    """
    import shlex

    # A real recorded operation still settles through the existing native completion contract.
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    monkeypatch.setattr(common, "pending_tool", lambda *_args: True)
    monkeypatch.setattr(
        common,
        "_preparation_path",
        lambda _event, value, exact=False: exact and value == r"C:\checkout\AGENTS.md",
    )
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    observed: list[JsonObject] = []
    monkeypatch.setattr(
        core, "execute", lambda request: observed.append(request) or {"ok": True, "code": "OK"}
    )
    value = {
        **native,
        "tool_name": "Bash",
        "tool_input": {"command": "cat " + shlex.quote(r"C:\checkout\AGENTS.md")},
        "tool_response": {"stdout": "source", "stderr": "", "interrupted": False},
    }
    common.native_post(value, "claude-code", failed=False)
    assert len(observed) == 1
    assert observed[0]["operation"] == "tool-complete"
    assert observed[0]["tool_id"] == "native-call"
    assert observed[0]["completed"] is True


@pytest.mark.parametrize("field", ["session_id", "tool_use_id"])
@pytest.mark.parametrize("value", [None, 7, ""])
def test_pending_lookup_validates_identity_before_absence(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """Reject malformed callback identity even when task registration would be absent.

    Args:
        native: Complete native callback fixture.
        monkeypatch: Isolate the registration boundary.
        field: Native correlation field under test.
        value: Missing, nontext or empty native identifier.
    """
    # Missing setup cannot authorize a malformed native callback to bypass validation.
    monkeypatch.setattr(common, "request_for", lambda *_args: pytest.fail("identity not checked"))
    with pytest.raises(core.WorkspaceError) as failure:
        common.pending_tool({**native, field: value}, "codex")
    assert failure.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_child_task_free_note_does_not_acquire_scope(
    native: JsonObject, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    """Reject generic child notes before native execution or lifecycle mutation.

    Args:
        native: Complete native callback fixture.
        monkeypatch: Isolate lifecycle and execution boundaries.
        tool: Native prose mutation spelling.
    """
    # Use the real note classifier; scoped lifecycle updates have their separate route.
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "native_tool", lambda *_args: pytest.fail("unscoped child edit"))
    monkeypatch.setattr(common, "request_for", lambda *_args: pytest.fail("unexpected lifecycle"))
    value = {**native, "child": True, "tool_name": tool, "tool_input": {"file_path": "outside.md"}}
    with pytest.raises(core.WorkspaceError) as failure:
        common.native_pre(value, "claude-code")
    assert failure.value.code == "HOST_UNSUPPORTED_TOOL"
