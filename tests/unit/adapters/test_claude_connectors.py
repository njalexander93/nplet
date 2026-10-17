"""Validate explicit Desktop connector routing without trusting arbitrary tool names."""

from pathlib import Path

import pytest

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core
from tests.types import JsonObject

pytestmark = pytest.mark.unit


CONNECTOR = "01234567-89ab-4cde-8f01-23456789abcd"


@pytest.mark.parametrize("source", ["claudeai", "dynamic", "sdk"])
def test_connector_binding_requires_matching_host_provenance(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """Keep routing, source validation and argument checks on configured connectors.

    Args:
        monkeypatch: Scoped configuration for the verified Linear connector.
        source: Accepted documented remote-server source.
    """
    # Configure a single verified connector and construct its exact read operation.
    monkeypatch.setenv("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    event: JsonObject = {
        "tool_name": f"mcp__{CONNECTOR}__get_issue",
        "mcp_server": {"name": CONNECTOR, "source": source},
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "native-call",
    }
    assert common._linear_tool(event, "claude-code") == ("native-call", {"id": "TEST-1"})
    # Missing, mismatched, local or unknown provenance cannot borrow the connector mapping.
    for server in [
        None,
        {"name": "linear", "source": source},
        {"name": [], "source": source},
        *[{"name": CONNECTOR, "source": value} for value in ["user", "unknown", None]],
    ]:
        with pytest.raises(core.WorkspaceError) as failure:
            common._linear_tool({**event, "mcp_server": server}, "claude-code")
        assert failure.value.code == "HOST_UNSUPPORTED_PROVIDER"
    # Other operations and other connector IDs never qualify for the ticket-read exception.
    for name in [f"mcp__{CONNECTOR}__save_issue", "mcp__other__get_issue", "ToolSearch"]:
        assert common._linear_tool({**event, "tool_name": name}, "claude-code") is None
    with pytest.raises(core.WorkspaceError) as failure:
        common._linear_tool({**event, "tool_input": []}, "claude-code")
    assert failure.value.code == "INVALID_REQUEST"
    # Invalid configuration must neither admit the opaque connector nor affect named Linear.
    for configured in ["", "*", "linear", CONNECTOR + ",other", CONNECTOR.upper()]:
        monkeypatch.setenv("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", configured)
        assert common._linear_tool(event, "claude-code") is None
    named = {
        **event,
        "tool_name": "mcp__linear__get_issue",
        "mcp_server": {"name": "linear", "source": "user"},
    }
    assert common._linear_tool(named, "claude-code") == ("native-call", {"id": "TEST-1"})


@pytest.mark.parametrize(
    "operation",
    sorted(
        operation for provider, operation in common.provider_contracts() if provider == "linear"
    ),
)
def test_provider_admission_accepts_only_allowlisted_configured_operations(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Admit each allowlisted operation only on the configured server and provenance.

    Args:
        monkeypatch: Scoped configuration for the verified Linear connector.
        operation: One allowlisted Linear operation.
    """
    # Configure one connector and build its operation event.
    monkeypatch.setenv("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    event: JsonObject = {
        "tool_name": f"mcp__{CONNECTOR}__{operation}",
        "mcp_server": {"name": CONNECTOR, "source": "sdk"},
        "tool_input": {},
        "tool_use_id": "native-call",
    }
    assert common._linear_provider_tool(event, "claude-code") == operation
    named = {
        **event,
        "tool_name": f"mcp__linear__{operation}",
        "mcp_server": {"name": "linear", "source": "project"},
    }
    assert common._linear_provider_tool(named, "claude-code") == operation
    # Shared parent admission does not enlarge the existing child provider route.
    child = {**event, "child": True}
    if operation in common.LINEAR_CHILD_OPERATIONS:
        assert common.native_tool(child, "claude-code") == child["tool_name"]
    else:
        with pytest.raises(core.WorkspaceError) as failure:
            common.native_tool(child, "claude-code")
        assert failure.value.code == "HOST_UNSUPPORTED_PROVIDER"
    # Another host, provenance, server or mismatched name never qualifies.
    for changed, host in [
        (event, "cursor"),
        ({**event, "mcp_server": {"name": CONNECTOR, "source": "user"}}, "claude-code"),
        ({**event, "mcp_server": None}, "claude-code"),
        ({**event, "tool_name": f"mcp__other__{operation}"}, "claude-code"),
        ({**event, "mcp_server": {"name": "other", "source": "user"}}, "claude-code"),
        ({**named, "tool_name": f"mcp__linear-server__{operation}"}, "claude-code"),
    ]:
        assert common._linear_provider_tool(changed, host) is None


@pytest.mark.parametrize("operation", ["execute_code", "get_issue_x", "unknown_operation"])
def test_provider_admission_rejects_other_operations(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Keep operations without verified contracts outside the native route.

    Args:
        monkeypatch: Scoped configuration for the verified Linear connector.
        operation: An operation outside the allowlist.
    """
    # Even the configured connector cannot run an unlisted operation.
    monkeypatch.setenv("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    event: JsonObject = {
        "tool_name": f"mcp__{CONNECTOR}__{operation}",
        "mcp_server": {"name": CONNECTOR, "source": "sdk"},
        "tool_input": {},
        "tool_use_id": "native-call",
    }
    assert common._linear_provider_tool(event, "claude-code") is None
    with pytest.raises(core.WorkspaceError) as failure:
        common.native_tool(event, "claude-code")
    assert failure.value.code == "HOST_UNSUPPORTED_PROVIDER"


def test_native_tool_routes_providers_and_no_effect_tools_by_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admit configured Claude providers and provisional known Cursor operations.

    Args:
        monkeypatch: Scoped configuration for the verified Linear connector.
    """
    # A configured Claude provider write is an ordinary correlated tool.
    monkeypatch.setenv("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    save: JsonObject = {
        "tool_name": f"mcp__{CONNECTOR}__save_issue",
        "mcp_server": {"name": CONNECTOR, "source": "claudeai"},
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "native-call",
    }
    assert common.native_tool(save, "claude-code") == save["tool_name"]
    # Cursor's operation-only generic gate still rejects unregistered provider operations.
    assert (
        common.native_tool(
            {"tool_name": "MCP:get_issue", "tool_input": {}, "tool_use_id": "c"}, "cursor"
        )
        == "MCP:get_issue"
    )
    for event, host, code in [
        (
            {"tool_name": "MCP:execute_code", "tool_input": {}, "tool_use_id": "c"},
            "cursor",
            "HOST_UNSUPPORTED_PROVIDER",
        ),
        (
            {**save, "tool_name": "ToolSearch", "mcp_server": None},
            "cursor",
            "HOST_UNSUPPORTED_TOOL",
        ),
        (
            {"tool_name": "Skill", "tool_input": {}, "tool_use_id": "c"},
            "cursor",
            "HOST_UNSUPPORTED_TOOL",
        ),
    ]:
        with pytest.raises(core.WorkspaceError) as failure:
            common.native_tool(event, host)
        assert failure.value.code == code
    # Claude admits schema loads, skill loads, messages and user questions by name.
    for tool in ["ToolSearch", "Skill", "SendMessage", "AskUserQuestion"]:
        event = {"tool_name": tool, "tool_input": {}, "tool_use_id": "c"}
        assert common.native_tool(event, "claude-code") == tool
    # Only a normalized child identity may hand a report back; a parent cannot.
    handback = {"tool_name": "SubagentHandback", "tool_input": {}, "tool_use_id": "c"}
    assert common.native_tool({**handback, "child": True}, "claude-code") == "SubagentHandback"
    with pytest.raises(core.WorkspaceError) as parent_failure:
        common.native_tool(handback, "claude-code")
    assert parent_failure.value.code == "HOST_UNSUPPORTED_TOOL"


@pytest.mark.parametrize("tool", ["ToolSearch", "Skill", "SendMessage", "AskUserQuestion"])
def test_no_effect_tools_are_task_free_and_record_no_pending_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool: str
) -> None:
    """Use real effect classification for unbound schema, skill and clarification calls.

    Args:
        monkeypatch: Isolate repository discovery and the existing-admission read boundary.
        tmp_path: Absolute root with no lifecycle storage.
        tool: Native administrative tool with no implementation effect.
    """
    # Complete native identities reach the real policy; no readiness lookup is permitted.
    monkeypatch.setattr(core, "repository", lambda _path: (tmp_path, None, []))
    monkeypatch.setattr(common, "pending_tool", lambda *_args: False)
    monkeypatch.setattr(
        common, "request_for", lambda *_args: pytest.fail("task-free readiness lookup")
    )
    monkeypatch.setattr(
        core, "execute", lambda _request: pytest.fail("task-free lifecycle mutation")
    )
    event: JsonObject = {
        "session_id": "native-session",
        "cwd": str(tmp_path),
        "tool_name": tool,
        "tool_input": {},
        "tool_use_id": "tool-1",
    }
    # Pre and post remain free even when no task binding or state directory exists.
    common.native_pre(event, "claude-code")
    common.native_post({**event, "tool_response": "ok"}, "claude-code", failed=False)
    assert not (tmp_path / ".task").exists()


def test_preparation_tool_bounds_tool_search_and_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Accept only bounded issue-read schema loads and exact governing-file reads.

    Args:
        monkeypatch: Scoped connector configuration and repository resolution.
        tmp_path: Disposable checkout root.
    """
    # Resolve every event to the disposable checkout.
    monkeypatch.setenv("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    monkeypatch.setattr(common.core, "repository", lambda _path: (tmp_path, tmp_path, [tmp_path]))
    agents = str(tmp_path / "AGENTS.md")

    def event(tool: str, args: object) -> JsonObject:
        """Build one Claude preparation event.

        Args:
            tool: Native tool name.
            args: Native tool input.

        Returns:
            The native envelope.
        """
        return {"cwd": str(tmp_path), "tool_name": tool, "tool_input": args, "tool_use_id": "t"}

    # Positive selections and reads.
    selection = f"select:mcp__{CONNECTOR}__get_issue, mcp__linear__get_issue"
    for tool, args in [
        ("ToolSearch", {"query": selection}),
        ("ToolSearch", {"query": "mcp__linear-server__get_issue", "max_results": 20}),
        ("Read", {"file_path": agents, "limit": 10}),
        ("Bash", {"command": f"cat '{agents}'", "timeout": 1000}),
    ]:
        assert common.preparation_tool(event(tool, args), "claude-code") is True, args
        assert common.preparation_tool(event(tool, args), "cursor") is False
    # Malformed inputs, widened searches and other files are rejected.
    for tool, args in [
        ("ToolSearch", "select:get_issue"),
        ("ToolSearch", {"query": "select:"}),
        ("ToolSearch", {"query": "get_issue", "max_results": 0}),
        ("ToolSearch", {"query": "get_issue", "max_results": 21}),
        ("ToolSearch", {"query": "get_issue", "max_results": True}),
        ("ToolSearch", {"query": "get_issue", "extra": 1}),
        ("ToolSearch", {"query": "get_issue " + "x" * 200}),
        ("ToolSearch", {"query": "get_issue; select:Bash"}),
        ("ToolSearch", {"query": "select:mcp__linear__list_issues"}),
        ("Read", {"file_path": agents, "pages": "1"}),
        ("Read", {"file_path": str(tmp_path / "docs/runtime/development.md")}),
        ("Bash", {"command": "cat AGENTS.md"}),
        ("Bash", {"command": f"cat {agents}\nrm -rf ."}),
        ("Bash", {"command": f"cat '{agents}"}),
        ("Bash", {"command": f"cat {agents}", "extra": True}),
        ("Write", {"file_path": agents}),
    ]:
        assert common.preparation_tool(event(tool, args), "claude-code") is False, args


@pytest.mark.parametrize("operation", sorted(common.APP_PR_OPERATIONS))
def test_app_pr_monitor_admits_only_named_operations_for_a_parent(operation: str) -> None:
    """Admit the desktop app's PR-monitor reads and arming for a parent only.

    Args:
        operation: One admitted PR-monitor operation.
    """
    # The exact qualified name is admitted only with the app's captured server identity.
    event: JsonObject = {
        "tool_name": f"mcp__ccd_pr__{operation}",
        "tool_input": {},
        "tool_use_id": "native-call",
    }
    served = {**event, "mcp_server": {"name": "ccd_pr", "source": "sdk"}}
    assert common._app_pr_monitor_tool(served, "claude-code") == operation
    assert common.native_tool(served, "claude-code") == served["tool_name"]
    # A missing server object, another host, name, source or shape never qualifies.
    for changed, host in [
        (event, "claude-code"),
        (served, "cursor"),
        ({**served, "mcp_server": {"name": "linear", "source": "user"}}, "claude-code"),
        ({**served, "mcp_server": {"name": "ccd_pr", "source": "user"}}, "claude-code"),
        ({**served, "mcp_server": {"name": "ccd_pr", "source": "observed"}}, "claude-code"),
        ({**served, "mcp_server": {"name": "ccd_pr", "source": None}}, "claude-code"),
        ({**served, "mcp_server": {"name": "ccd_pr"}}, "claude-code"),
        ({**served, "mcp_server": "ccd_pr"}, "claude-code"),
    ]:
        assert common._app_pr_monitor_tool(changed, host) is None
        with pytest.raises(core.WorkspaceError) as failure:
            common.native_tool(changed, host)
        assert failure.value.code == "HOST_UNSUPPORTED_PROVIDER"
    # A child identity receives neither operation.
    with pytest.raises(core.WorkspaceError) as child_failure:
        common.native_tool({**served, "child": True}, "claude-code")
    assert child_failure.value.code == "HOST_UNSUPPORTED_PROVIDER"


@pytest.mark.parametrize(
    "name",
    [
        "mcp__ccd_pr__set_auto_merge",
        "mcp__ccd_pr__bind_pr",
        "mcp__ccd_pr__unbind_pr",
        "mcp__ccd_session_mgmt__get_session",
        "mcp__ccd_settings__set_setting",
        "mcp__ccd_pr__get_status_x",
        "mcp__other__get_status",
    ],
)
def test_app_pr_monitor_denies_other_app_tools(name: str) -> None:
    """Keep auto-merge, PR binding and other desktop-app servers denied.

    Args:
        name: A qualified app tool outside the PR-monitor allowlist.
    """
    # Neither a missing nor a self-consistent server object widens the allowlist.
    server = name.split("__")[1]
    for event in [
        {"tool_name": name, "tool_input": {}, "tool_use_id": "c"},
        {"tool_name": name, "tool_input": {}, "tool_use_id": "c", "mcp_server": {"name": server}},
    ]:
        assert common._app_pr_monitor_tool(event, "claude-code") is None
        with pytest.raises(core.WorkspaceError) as failure:
            common.native_tool(event, "claude-code")
        assert failure.value.code == "HOST_UNSUPPORTED_PROVIDER"


def test_unsupported_provider_recovery_names_app_pr_monitor() -> None:
    """Name the admitted PR-monitor operations in the Claude recovery message only."""
    # The Claude message lists both operations; Cursor's message is unchanged.
    message = common.recovery("HOST_UNSUPPORTED_PROVIDER", "claude-code")
    assert "get_status" in message
    assert "set_monitor" in message
    assert "set_monitor" not in common.recovery("HOST_UNSUPPORTED_PROVIDER", "cursor")
