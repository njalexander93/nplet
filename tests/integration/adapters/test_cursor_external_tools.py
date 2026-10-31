"""Exercise Cursor's unchecked external MCP calls and Shell directories in real stores."""

import copy
import json
from pathlib import Path

import pytest

from nplet.adapters import common, cursor
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.native_events import event

pytestmark = pytest.mark.integration
GITHUB = {"mcp_server_name": "github", "mcp_server_url": "https://api.githubcopilot.com/mcp/"}
LINEAR = {"mcp_server_name": "linear", "mcp_server_url": "https://mcp.linear.app/mcp"}


def specific(checkout: Path, name: str, operation: str, args: object, **server: str) -> dict:
    """Build an MCP-specific Cursor callback, which carries no native call ID.

    Args:
        checkout: Disposable Git checkout.
        name: Native hook name, before or after execution.
        operation: Server operation name.
        args: Native tool input, normally serialized JSON text.
        **server: Server name and URL fields.

    Returns:
        The Cursor envelope.
    """
    # Cursor's MCP callbacks name the bare operation and the server, not the tool call.
    observed = event(checkout, "cursor", operation, {}, name=name)
    del observed["tool_use_id"]
    observed.update(tool_input=args, **server)
    return observed


def test_cursor_external_mcp_is_unchecked_through_every_callback(checkout: Path) -> None:
    """Admit another server's call with no binding, lifecycle state or event change.

    Args:
        checkout: Unregistered checkout whose lifecycle storage must remain absent.
    """
    # Every callback for one call to a server other than Linear passes.
    args = {"query": "nplet"}
    generic = event(checkout, "cursor", "MCP:search_repositories", args)
    calls = [
        (generic, {"permission": "allow"}),
        (
            specific(
                checkout, "BeforeMCPExecution", "search_repositories", json.dumps(args), **GITHUB
            ),
            {"permission": "allow"},
        ),
        (
            {
                **specific(
                    checkout, "AfterMCPExecution", "search_repositories", json.dumps(args), **GITHUB
                ),
                "result_json": "{}",
            },
            {},
        ),
        ({**generic, "hook_event_name": "postToolUse", "tool_output": "{}"}, {}),
        (
            {
                **generic,
                "hook_event_name": "postToolUseFailure",
                "error_message": "failed",
                "failure_type": "error",
                "is_interrupt": False,
            },
            {},
        ),
    ]
    for observed, expected in calls:
        before = copy.deepcopy(observed)
        assert cursor.handle(observed) == expected
        assert observed == before
    assert not (checkout / ".task").exists()


@pytest.mark.parametrize(
    "operation,args,server,code",
    [
        # A Linear operation or server keeps its inventory and provenance checks.
        ("get_issue", '{"id": "T-1"}', {**LINEAR, "mcp_server_url": "https://x.test/mcp"}, None),
        ("save_issue", "{}", {**LINEAR, "mcp_server_name": "unconfigured"}, None),
        ("get_issue", '{"id": "T-1"}', GITHUB, None),
        ("search_code", "{}", {**GITHUB, "mcp_server_name": "linear"}, None),
        ("search_code", "{}", {**GITHUB, "mcp_server_url": "https://mcp.linear.app/mcp"}, None),
        ("delete_comment", '{"id": "c"}', LINEAR, None),
        ("delete_issue", '{"id": "T-1"}', LINEAR, None),
        # Another server's call still needs a serialized object input.
        ("search_code", {}, GITHUB, "INVALID_REQUEST"),
        ("search_code", "[]", GITHUB, "INVALID_REQUEST"),
        ("search_code", "{", GITHUB, "INVALID_REQUEST"),
    ],
)
def test_cursor_specific_mcp_callback_keeps_linear_and_shape_checks(
    checkout: Path, operation: str, args: object, server: dict, code: str | None
) -> None:
    """Deny a call that claims Linear without its exact server, a delete, or bad input.

    Args:
        checkout: Unregistered checkout whose lifecycle storage must remain absent.
        operation: Server operation name.
        args: Native tool input.
        server: Server name and URL fields.
        code: Expected diagnostic; None means the provider denial.
    """
    observed = specific(checkout, "BeforeMCPExecution", operation, args, **server)
    expected = code or "HOST_UNSUPPORTED_PROVIDER"
    assert cursor.handle(observed) == cursor.failure("beforeMCPExecution", expected)
    assert not (checkout / ".task").exists()


def test_cursor_generic_callback_keeps_linear_and_non_mcp_routes(checkout: Path) -> None:
    """Keep a Linear operation on its gated route and other native tools closed.

    Args:
        checkout: Unregistered checkout whose lifecycle storage must remain absent.
    """
    # A Linear operation keeps its route; its server is checked by beforeMCPExecution.
    for tool, unchecked in (("MCP:save_issue", False), ("MCP:search", False), ("MCP:x", True)):
        assert common.unchecked(event(checkout, "cursor", tool, {}), "cursor") is unchecked
    # A native tool that is not MCP stays outside the unchecked set.
    response = cursor.handle(event(checkout, "cursor", "ApplyPatch", {"patch": "p"}))
    assert response == cursor.failure("preToolUse", "HOST_UNSUPPORTED_TOOL")
    # A child gets no provider call, whatever its server.
    child = {**common.native_identity(event(checkout, "cursor", "MCP:search_code", {}), "cursor")}
    child["child"] = True
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_tool(child, "cursor")
    assert captured.value.code == "HOST_UNSUPPORTED_PROVIDER"
    assert not (checkout / ".task").exists()


def test_cursor_shell_effect_carries_its_command_directory(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass an existing absolute Shell directory to effect recognition, else None.

    Args:
        checkout: Unregistered checkout used as the session directory.
        tmp_path: Disposable parent for directories outside the checkout.
        monkeypatch: Scoped Claude background-task setting.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    cases = [
        ({"working_directory": str(plain)}, str(plain)),
        ({"cwd": str(plain), "timeout": 30000}, str(plain)),
        ({"working_directory": "plain"}, None),
        ({"working_directory": str(tmp_path / "missing")}, None),
        ({"working_directory": ""}, None),
        ({"cwd": ""}, str(checkout)),
        ({}, str(checkout)),
    ]
    for metadata, expected in cases:
        observed = common.native_identity(
            event(checkout, "cursor", "Shell", {"command": "pwd", **metadata}), "cursor"
        )
        before = copy.deepcopy(observed)
        effect = common.native_effect(observed, "cursor")
        # The directory is advisory; session fields and the command input are unchanged.
        assert effect["command_cwd"] == expected
        assert effect["tool_input"] == {"command": "pwd"}
        assert (effect["cwd"], effect["repository"]) == (before["cwd"], before["repository"])
        assert observed == before
    # Claude Bash runs in the event's own directory.
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    observed = common.native_identity(
        event(checkout, "claude", "Bash", {"command": "pwd"}), "claude-code"
    )
    effect = common.native_effect(observed, "claude-code")
    assert effect["command_cwd"] == observed["cwd"] == str(checkout)
    assert effect["repository"] == observed["repository"]


def test_cursor_lifecycle_shell_keeps_repository_match(checkout: Path, tmp_path: Path) -> None:
    """Deny a lifecycle command redirected to another directory; admit it in place.

    Args:
        checkout: Unregistered checkout that the registration command names.
        tmp_path: Disposable directory outside the checkout.
    """
    request = {
        "schema_version": 1,
        "operation": "register",
        "request_id": "f6b1f6a4-5a7e-4c55-9d0e-0d5a2f1c7b11",
        "worktree": str(checkout),
        "host": cursor.HOST,
        "session_id": "fresh",
    }
    command = common.bootstrap_command(request, cursor.HOST)
    # The same command in its own checkout is the lifecycle route; elsewhere it is denied.
    for directory, code in ((str(checkout), None), (str(tmp_path), "REPOSITORY_MISMATCH")):
        args = {"command": command, "working_directory": directory}
        response = cursor.handle(event(checkout, "cursor", "Shell", args))
        assert response == (
            {"permission": "allow"} if code is None else cursor.failure("preToolUse", code)
        )
    assert not (checkout / ".task").exists()
