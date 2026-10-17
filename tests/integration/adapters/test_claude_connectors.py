"""Exercise configured Desktop Linear reads through the real startup lifecycle."""

import json
import shlex

import pytest

from nplet.adapters import claude, common
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.test_native_hosts import native_event, ticket_callbacks
from tests.platform_support import link_directory, unlink_directory
from tests.support import Fixture

pytestmark = pytest.mark.integration
CONNECTOR = "01234567-89ab-4cde-8f01-23456789abcd"


@pytest.mark.parametrize("source", ["claudeai", "dynamic", "sdk"])
@pytest.mark.parametrize("content_blocks", [False, True])
def test_desktop_connector_requires_mapping_and_exact_correlated_read(
    monkeypatch: pytest.MonkeyPatch, source: str, content_blocks: bool
) -> None:
    """Admit only the configured provider and settle its exact issue read.

    Args:
        monkeypatch: Scoped environment configuration for the connector binding.
        source: Documented remote connector provenance.
        content_blocks: Use Desktop's observed native result instead of the normalized fixture.
    """
    # Create a disposable repository; never register the user's active session.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.delenv("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", raising=False)
    case = Fixture()
    case.setUp()
    case.base["host"] = claude.HOST
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        # Record the selected ticket and construct Desktop's UUID-qualified callbacks.
        claude.handle(native_event("claude", case, "UserPromptSubmit", prompt="Task: TEST-1"))
        issue = {"id": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977", "identifier": "TEST-1"}
        before, after = ticket_callbacks("claude", case, issue)
        # Desktop supplies the successful MCP content list without a CallToolResult wrapper.
        if content_blocks:
            after["tool_response"] = [
                {"type": "text", "text": json.dumps({"id": "TEST-1", "uuid": issue["id"]})}
            ]
        for event in [*before, after]:
            event.update(
                tool_name=f"mcp__{CONNECTOR}__get_issue",
                mcp_server={"name": CONNECTOR, "source": source},
            )
        # A UUID alone grants no exception, even when the host reports a known source.
        assert _denied(claude.handle(before[0]), "TICKET_READ_REQUIRED")
        monkeypatch.setenv("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
        # Unrelated research reads do not consume the selected-ticket transition.
        wrong = {**before[0], "tool_input": {"id": "TEST-2"}}
        assert claude.handle(wrong) == {}
        assert not (case.root / ".task/TEST-1").exists()
        # Admit the real read, reject a mismatched completion, then settle the right call.
        assert claude.handle(before[0]) == {}
        marker = case.root / ".task/.bindings" / (case.base["coordinator"] + ".lookup.json")
        reserved = marker.read_bytes()
        assert claude.handle({**after, "tool_use_id": "different-call"}) == {}
        assert marker.read_bytes() == reserved
        assert not (case.root / ".task/TEST-1").exists()
        # Multiple content blocks remain invalid and cannot create workspace state.
        if content_blocks:
            assert "PROVIDER_RESPONSE_INVALID" in str(
                claude.handle({**after, "tool_response": [{"type": "text", "text": "{}"}] * 2})
            )
            assert not (case.root / ".task/TEST-1").exists()
            assert claude.handle(before[0]) == {}
        assert "TASK_WORKSPACE_READY" in str(claude.handle(after))
        observed = common.native_identity(native_event("claude", case, "SessionStart"), claude.HOST)
        assert core.execute(common.request_for(observed, "ready", claude.HOST))["ok"] is True
    finally:
        # Release filesystem state even when a denial or readiness assertion fails.
        case.doCleanups()


def _start_desktop_task(case: Fixture) -> None:
    """Record the selected Task line for the disposable Desktop session.

    Args:
        case: Disposable repository whose coordinator is the Claude session.
    """
    claude.handle(native_event("claude", case, "UserPromptSubmit", prompt="Task: TEST-1"))


def _ready_desktop(case: Fixture) -> None:
    """Complete the exact configured connector read so the session becomes ready.

    Args:
        case: Disposable repository with a recorded Task line and mapped connector.
    """
    # Build the configured connector's exact read and settle it through startup.
    issue = {"id": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977", "identifier": "TEST-1"}
    before, after = ticket_callbacks("claude", case, issue)
    # Address every callback to the mapped Desktop connector.
    for event in [*before, after]:
        event.update(
            tool_name=f"mcp__{CONNECTOR}__get_issue",
            mcp_server={"name": CONNECTOR, "source": "sdk"},
        )
    # Admit the read, then settle it into readiness.
    assert claude.handle(before[0]) == {}
    assert "TASK_WORKSPACE_READY" in str(claude.handle(after))


def _desktop_case(monkeypatch: pytest.MonkeyPatch) -> Fixture:
    """Create a disposable Claude-hosted repository with the connector mapped.

    Args:
        monkeypatch: Scoped environment configuration for the connector binding.

    Returns:
        The started fixture; the caller registers cleanup.
    """
    # Remove imported-hook markers and map one verified connector.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.setenv("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    case = Fixture()
    case.setUp()
    case.base["host"] = claude.HOST
    case.base["coordinator"] = core.participant_key(case.base)
    return case


def _denied(response: core.JSONObject, code: str) -> bool:
    """Check a Claude pre-tool denial carrying one diagnostic.

    Args:
        response: Adapter response for a PreToolUse event.
        code: Expected diagnostic code.

    Returns:
        Whether the response denies the call with that code.
    """
    decision = response.get("hookSpecificOutput", {})
    return decision.get("permissionDecision") == "deny" and code in str(
        decision.get("permissionDecisionReason")
    )


def test_preparation_reads_precede_ticket_without_lifecycle_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admit task-free inspection and administration without consuming the exact ticket read.

    Args:
        monkeypatch: Scoped environment configuration for the connector binding.
    """
    # Record the Task line and locate the governing files and lookup marker.
    case = _desktop_case(monkeypatch)
    try:
        _start_desktop_task(case)
        procedure = str(case.root / "docs/runtime/contributor-workflow.md")
        agents = str(case.root / "AGENTS.md")
        # Quote shell arguments so native Windows separators survive POSIX parsing.
        quoted_procedure, quoted_agents = shlex.quote(procedure), shlex.quote(agents)
        # The shared shell grammar uses POSIX path spelling, including Git Bash on Windows.
        shell_procedure = shlex.quote(
            (case.root / "docs/runtime/contributor-workflow.md").as_posix()
        )
        shell_agents = shlex.quote((case.root / "AGENTS.md").as_posix())
        marker = (
            case.root / ".task/.bindings" / (case.base["coordinator"] + ".lookup-required.json")
        )
        assert marker.exists()
        # Each documented preparation call gets no decision on either hook event.
        admitted = [
            ("ToolSearch", {"query": f"select:mcp__{CONNECTOR}__get_issue", "max_results": 1}),
            ("ToolSearch", {"query": "linear get_issue", "max_results": 5}),
            ("Read", {"file_path": procedure}),
            ("Read", {"file_path": agents}),
            ("Bash", {"command": f"cat {quoted_procedure}", "description": "Read procedure"}),
            ("Bash", {"command": f"cat {quoted_agents}"}),
        ]
        # Deliver each preparation call's pre and post events.
        for tool, args in admitted:
            fields: dict[str, object] = {"tool_name": tool, "tool_input": args}
            assert claude.handle(native_event("claude", case, "PreToolUse", **fields)) == {}
            # Keep native Windows literal paths in the regression and supply valid
            # foreground Bash completion facts instead of the old untyped fixture string.
            output: object = (
                {"stdout": "governing source", "stderr": "", "interrupted": False}
                if tool == "Bash"
                else "ok"
            )
            completed = native_event("claude", case, "PostToolUse", tool_response=output, **fields)
            before_completion = {
                path.name: path.read_bytes() for path in marker.parent.glob("*.json")
            }
            assert claude.handle(completed) == {}
            assert {
                path.name: path.read_bytes() for path in marker.parent.glob("*.json")
            } == before_completion
        # Task-free reads and schema loading stay available during the ticket transition.
        free = [
            ("Read", {"file_path": str(case.root / "README.md")}),
            ("Read", {"file_path": str(case.root / "docs/../AGENTS.md")}),
            ("Read", {"file_path": "docs/runtime/contributor-workflow.md"}),
            ("Bash", {"command": f"cat {shell_agents}"}),
            ("Bash", {"command": f"cat {shell_procedure} | head"}),
            ("Bash", {"command": f"cat {shell_procedure} {shell_agents}"}),
            ("Bash", {"command": f"cat -n {shell_procedure}"}),
            ("ToolSearch", {"query": f"select:mcp__{CONNECTOR}__save_issue"}),
            ("ToolSearch", {"query": "linear save_issue"}),
            ("ToolSearch", {"query": f"select:mcp__{CONNECTOR}__get_issue,Agent"}),
            ("ToolSearch", {"query": "select:mcp__other__get_issue"}),
            ("Bash", {"command": "cat " + shlex.quote(case.root.as_posix() + "//AGENTS.md")}),
            ("Glob", {"pattern": "**/*.md"}),
        ]
        for tool, args in free:
            assert (
                claude.handle(
                    native_event("claude", case, "PreToolUse", tool_name=tool, tool_input=args)
                )
                == {}
            ), (tool, args)
        # Protected writes and opaque execution still require the selected ticket.
        for tool, args in (
            ("Write", {"file_path": str(case.root / "AGENTS.md"), "content": "changed"}),
            ("Bash", {"command": f"cat {quoted_procedure} > src/copied.py"}),
            ("Bash", {"command": f"cat {quoted_procedure}; true"}),
        ):
            response = claude.handle(
                native_event("claude", case, "PreToolUse", tool_name=tool, tool_input=args)
            )
            assert _denied(response, "TICKET_READ_REQUIRED"), (tool, args)
        # Isolate unsupported background execution from the portable recognized read command.
        background = native_event(
            "claude",
            case,
            "PreToolUse",
            tool_name="Bash",
            tool_input={"command": f"cat {shell_agents}", "run_in_background": True},
        )
        assert _denied(claude.handle(background), "HOST_UNSUPPORTED_ASYNC")
        # Known issue administration stays task-free before the ticket read.
        save = native_event(
            "claude",
            case,
            "PreToolUse",
            tool_name=f"mcp__{CONNECTOR}__save_issue",
            tool_input={"id": "TEST-1", "state": "In Progress"},
            mcp_server={"name": CONNECTOR, "source": "sdk"},
        )
        assert claude.handle(save) == {}
        # A linked read is inspection; it cannot supply task-startup authority.
        (case.root / "elsewhere/runtime").mkdir(parents=True)
        (case.root / "elsewhere/runtime/contributor-workflow.md").write_text("redirected")
        assert not (case.root / "docs").exists()
        link_directory(case.root / "docs", case.root / "elsewhere")
        try:
            # Both read routes are task-free and preserve the lookup marker.
            for tool, args in (
                ("Read", {"file_path": procedure}),
                ("Bash", {"command": f"cat {shell_procedure}"}),
            ):
                event = native_event("claude", case, "PreToolUse", tool_name=tool, tool_input=args)
                assert claude.handle(event) == {}
        finally:
            # Remove only the fixture link, never its target.
            unlink_directory(case.root / "docs")
        # Nothing above touched the lookup marker or created issue state.
        assert marker.exists()
        assert not (case.root / ".task/TEST-1").exists()
        # The exact read still completes startup and clears the marker.
        _ready_desktop(case)
        assert not marker.exists()
    finally:
        # Release filesystem state even when an assertion fails.
        case.doCleanups()


def test_ready_session_keeps_configured_provider_administration_taskfree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admit known provider research/admin without adding task pending work.

    Args:
        monkeypatch: Scoped environment configuration for the connector binding.
    """
    # Create the disposable Desktop repository with the connector mapped.
    case = _desktop_case(monkeypatch)
    try:
        # Reach readiness through the exact configured connector read.
        _start_desktop_task(case)
        _ready_desktop(case)
        server = {"name": CONNECTOR, "source": "sdk"}

        def provider(name: str, operation: str, tool_id: str, **fields: object) -> core.JSONObject:
            """Build one configured-connector event after readiness.

            Args:
                name: Native hook event name.
                operation: Linear operation name.
                tool_id: Native tool-use identifier.
                **fields: Event-specific fields, such as the result or error.

            Returns:
                The native Claude envelope.
            """
            return native_event(
                "claude",
                case,
                name,
                tool_name=f"mcp__{CONNECTOR}__{operation}",
                tool_input={"id": "OTHER-2"},
                tool_use_id=tool_id,
                mcp_server=server,
                **fields,
            )

        def pending() -> dict[str, object]:
            """Read the coordinator's pending native tool map.

            Returns:
                Pending tool entries keyed by native tool ID.
            """
            return case.state()["participants"][case.base["coordinator"]]["pending"]

        # Another issue's read remains task-free with the native content-list result.
        assert claude.handle(provider("PreToolUse", "get_issue", "read-other")) == {}
        assert pending() == {}
        blocks = [{"type": "text", "text": json.dumps({"id": "OTHER-2"})}]
        assert (
            claude.handle(provider("PostToolUse", "get_issue", "read-other", tool_response=blocks))
            == {}
        )
        assert pending() == {}
        # Known issue administration is also task-free, including failure callbacks.
        assert claude.handle(provider("PreToolUse", "save_issue", "write-state")) == {}
        assert pending() == {}
        failure = provider(
            "PostToolUseFailure", "save_issue", "write-state", error="denied", is_interrupt=False
        )
        assert claude.handle(failure) == {}
        assert pending() == {}
        # Shared document research and administration use the same configured provenance.
        before = case.state()
        for operation in ("get_document", "save_document"):
            assert claude.handle(provider("PreToolUse", operation, operation)) == {}
            assert (
                claude.handle(
                    provider("PostToolUse", operation, operation, tool_response={"ok": True})
                )
                == {}
            )
            assert case.state() == before
        # Unconfigured servers, unknown operations and wrong provenance stay denied.
        before = case.state()
        for event in (
            {**provider("PreToolUse", "execute_code", "opaque"), "tool_input": {}},
            {**provider("PreToolUse", "get_issue", "other"), "tool_name": "mcp__other__get_issue"},
            {
                **provider("PreToolUse", "get_issue", "local"),
                "mcp_server": {"name": CONNECTOR, "source": "user"},
            },
        ):
            # Each rejected provider call leaves lifecycle state unchanged.
            assert _denied(claude.handle(event), "HOST_UNSUPPORTED_PROVIDER")
        assert case.state() == before
        # Schema and skill loads after readiness need no settlement.
        for tool, args in (("ToolSearch", {"query": "select:Read"}), ("Skill", {"skill": "x"})):
            # Pre and post events both pass without recording pending work.
            fields: dict[str, object] = {"tool_name": tool, "tool_input": args, "tool_use_id": tool}
            assert claude.handle(native_event("claude", case, "PreToolUse", **fields)) == {}
            done = native_event("claude", case, "PostToolUse", tool_response="ok", **fields)
            assert claude.handle(done) == {}
        assert case.state() == before
    finally:
        # Release filesystem state even when an assertion fails.
        case.doCleanups()


def test_ready_parent_tracks_app_pr_monitor_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep monitor reads task-free and track automatic-fix arming after readiness.

    Args:
        monkeypatch: Scoped environment configuration for the connector binding.
    """
    # Create the disposable Desktop repository with the connector mapped.
    case = _desktop_case(monkeypatch)
    try:

        def monitor(name: str, tool: str, tool_id: str, **fields: object) -> core.JSONObject:
            """Build one desktop-app PR tool event.

            Args:
                name: Native hook event name.
                tool: Qualified native tool name.
                tool_id: Native tool-use identifier.
                **fields: Event-specific fields, such as the result or server object.

            Returns:
                The native Claude envelope.
            """
            return native_event(
                "claude",
                case,
                name,
                tool_name=tool,
                tool_input={"pr_url": "https://example.test/pull/1"},
                tool_use_id=tool_id,
                **fields,
            )

        def pending() -> dict[str, object]:
            """Read the coordinator's pending native tool map.

            Returns:
                Pending tool entries keyed by native tool ID.
            """
            return case.state()["participants"][case.base["coordinator"]]["pending"]

        # Missing provider provenance stays denied independently of task readiness.
        _start_desktop_task(case)
        early = monitor("PreToolUse", "mcp__ccd_pr__get_status", "early")
        assert _denied(claude.handle(early), "TICKET_READ_REQUIRED")
        _ready_desktop(case)
        server = {"name": "ccd_pr", "source": "sdk"}
        # The status read is task-free; its result adds no pending work.
        read = monitor("PreToolUse", "mcp__ccd_pr__get_status", "status", mcp_server=server)
        assert claude.handle(read) == {}
        assert pending() == {}
        done = monitor(
            "PostToolUse",
            "mcp__ccd_pr__get_status",
            "status",
            mcp_server=server,
            tool_response=[{"type": "text", "text": "{}"}],
        )
        assert claude.handle(done) == {}
        assert pending() == {}
        # Arming the monitor is admitted the same way and settled by its failure event.
        arm = monitor("PreToolUse", "mcp__ccd_pr__set_monitor", "arm", mcp_server=server)
        assert claude.handle(arm) == {}
        assert "arm" in pending()
        failed = monitor(
            "PostToolUseFailure",
            "mcp__ccd_pr__set_monitor",
            "arm",
            mcp_server=server,
            error="denied",
            is_interrupt=False,
        )
        assert claude.handle(failed) == {}
        assert pending() == {}
        # Auto-merge, PR binding, other app servers, a missing server object, another
        # provenance source and a wrong server name stay denied.
        before = case.state()
        for event in (
            monitor("PreToolUse", "mcp__ccd_pr__set_auto_merge", "merge", mcp_server=server),
            monitor("PreToolUse", "mcp__ccd_pr__bind_pr", "bind", mcp_server=server),
            monitor("PreToolUse", "mcp__ccd_session_mgmt__get_session", "session"),
            monitor("PreToolUse", "mcp__ccd_pr__get_status", "no-server"),
            monitor(
                "PreToolUse",
                "mcp__ccd_pr__get_status",
                "wrong-source",
                mcp_server={"name": "ccd_pr", "source": "user"},
            ),
            monitor(
                "PreToolUse",
                "mcp__ccd_pr__get_status",
                "wrong-server",
                mcp_server={"name": "linear", "source": "user"},
            ),
        ):
            # Each rejected call leaves lifecycle state unchanged.
            assert _denied(claude.handle(event), "HOST_UNSUPPORTED_PROVIDER")
        assert case.state() == before
    finally:
        # Release filesystem state even when an assertion fails.
        case.doCleanups()
