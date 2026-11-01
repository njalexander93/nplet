"""Exercise Cursor's unchecked external MCP calls and Shell directories in real stores."""

import copy
import json
import subprocess
from pathlib import Path

import pytest

from nplet.adapters import common, cursor
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.native_events import event
from tests.support import Fixture

pytestmark = pytest.mark.integration
GITHUB = {"mcp_server_name": "github", "mcp_server_url": "https://api.githubcopilot.com/mcp/"}
LINEAR = {"mcp_server_name": "linear", "mcp_server_url": "https://mcp.linear.app/mcp"}
# Browser, computer-use, PDF and app servers, each with one operation and its input.
EXTERNAL = [
    ("search_repositories", {"query": "nplet"}, GITHUB),
    (
        "navigate",
        {"url": "https://example.test"},
        {"mcp_server_name": "browser", "mcp_server_url": "http://127.0.0.1:9/mcp"},
    ),
    (
        "screenshot",
        {},
        {"mcp_server_name": "computer-use", "mcp_server_url": "http://127.0.0.1:9/mcp"},
    ),
    (
        "read_pdf",
        {"path": "/tmp/x.pdf"},
        {"mcp_server_name": "pdf-tools", "mcp_server_url": "http://127.0.0.1:9/mcp"},
    ),
    ("list_groups", {}, {"mcp_server_name": "app", "mcp_server_url": "http://127.0.0.1:9/mcp"}),
]


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


def external_calls(checkout: Path, operation: str, args: dict, server: dict) -> list:
    """Build every Cursor callback for one call to a server other than Linear.

    Args:
        checkout: Disposable Git checkout.
        operation: Server operation name.
        args: Native tool input object.
        server: Server name and URL fields.

    Returns:
        Each envelope paired with its expected adapter response.
    """
    generic = event(checkout, "cursor", f"MCP:{operation}", args)
    return [
        (generic, {"permission": "allow"}),
        (
            specific(checkout, "BeforeMCPExecution", operation, json.dumps(args), **server),
            {"permission": "allow"},
        ),
        (
            {
                **specific(checkout, "AfterMCPExecution", operation, json.dumps(args), **server),
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


@pytest.mark.parametrize(("operation", "args", "server"), EXTERNAL)
def test_cursor_external_mcp_is_unchecked_through_every_callback(
    checkout: Path, operation: str, args: dict, server: dict
) -> None:
    """Admit another server's call with no binding, lifecycle state or event change.

    Args:
        checkout: Unregistered checkout whose lifecycle storage must remain absent.
        operation: Server operation name.
        args: Native tool input object.
        server: Server name and URL fields.
    """
    # Every callback for one call to a server other than Linear passes unchanged.
    for observed, expected in external_calls(checkout, operation, args, server):
        before = copy.deepcopy(observed)
        assert cursor.handle(observed) == expected
        assert observed == before
    assert not (checkout / ".task").exists()


@pytest.mark.parametrize(("operation", "args", "server"), EXTERNAL[:2])
def test_cursor_ready_session_external_mcp_records_nothing(
    operation: str, args: dict, server: dict
) -> None:
    """Skip tool-start and settlement for a ready Cursor session's external call.

    Args:
        operation: Server operation name.
        args: Native tool input object.
        server: Server name and URL fields.
    """
    # Make a Cursor coordinator ready, then snapshot its state and event log.
    case = Fixture()
    case.setUp()
    case.base["host"] = "cursor"
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        case.ready()
        before = case.state()
        events = (case.root / ".task/TEST-1/events.jsonl").read_bytes()
        # Every callback passes and leaves both unchanged, with nothing pending.
        for observed, expected in external_calls(case.root, operation, args, server):
            observed.update(
                session_id=case.base["session_id"], conversation_id=case.base["session_id"]
            )
            assert cursor.handle(observed) == expected
        assert case.state() == before
        assert (case.root / ".task/TEST-1/events.jsonl").read_bytes() == events
    finally:
        case.doCleanups()


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
    # The generic callback carries no server, so a delete passes there and its server is
    # checked by the blocking beforeMCPExecution callback (tested above).
    for tool in ("MCP:delete_comment", "MCP:delete_issue"):
        response = cursor.handle(event(checkout, "cursor", tool, {"id": "c"}))
        assert response == {"permission": "allow"}
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
    """Pass the event directory to effect recognition, or None when Cursor overrides it.

    Args:
        checkout: Unregistered checkout used as the session directory.
        tmp_path: Disposable parent for directories outside the checkout.
        monkeypatch: Scoped Claude background-task setting.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    cases = [
        # Installed Cursor ran commands outside a requested directory, so any override
        # leaves the directory unknown.
        ({"working_directory": str(plain)}, None),
        ({"cwd": str(plain), "timeout": 30000}, None),
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


def mirrored(checkout: Path, args: dict, name: str = "preToolUse") -> dict:
    """Build an installed Cursor Shell envelope, whose top-level cwd repeats the override.

    Args:
        checkout: Workspace root of the session.
        args: Native Shell input carrying the directory override.
        name: Native hook name.

    Returns:
        The Cursor envelope as Cursor 3.27 sends it.
    """
    observed = event(checkout, "cursor", "Shell", args, name=name)
    observed["cwd"] = args.get("cwd", args.get("working_directory"))
    return observed


def test_cursor_shell_with_mirrored_directory_is_ordinary_work(
    checkout: Path, tmp_path: Path
) -> None:
    """Admit Shell in another repository, no repository or a relative directory.

    Args:
        checkout: Unregistered checkout used as the workspace root.
        tmp_path: Disposable parent for directories outside the checkout.
    """
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    plain = tmp_path / "plain"
    plain.mkdir()
    for directory in (str(other), str(plain), "../../other"):
        args = {"command": "pwd", "cwd": directory, "timeout": 30000}
        calls = [
            (mirrored(checkout, args), {"permission": "allow"}),
            ({**mirrored(checkout, args, "postToolUse"), "tool_output": directory}, {}),
            (
                {
                    **mirrored(checkout, args, "postToolUseFailure"),
                    "error_message": "failed",
                    "failure_type": "error",
                    "is_interrupt": False,
                },
                {},
            ),
        ]
        for observed, expected in calls:
            assert cursor.handle(observed) == expected
        # The session keeps its workspace repository; only the command directory moves.
        identity = common.native_identity(mirrored(checkout, args), "cursor")
        assert identity["cwd"] == str(checkout)
        effect = common.native_effect(identity, "cursor")
        assert effect["command_cwd"] is None
        assert effect["repository"] == identity["repository"]
    assert not (checkout / ".task").exists()


def test_cursor_mirrored_directory_keeps_lifecycle_and_child_checks(
    checkout: Path, tmp_path: Path
) -> None:
    """Deny a redirected lifecycle command, child Shell or unrelated foreign cwd.

    Args:
        checkout: Unregistered checkout that the registration command names.
        tmp_path: Disposable directory outside the checkout.
    """
    request = {
        "schema_version": 1,
        "operation": "register",
        "request_id": "0b4f8a63-7c2e-4f0a-9d61-3e5b7a2c9f14",
        "worktree": str(checkout),
        "host": cursor.HOST,
        "session_id": "fresh",
    }
    args = {"command": common.bootstrap_command(request, cursor.HOST), "cwd": str(tmp_path)}
    assert cursor.handle(mirrored(checkout, args)) == cursor.failure(
        "preToolUse", "REPOSITORY_MISMATCH"
    )
    # A child Shell keeps its same-repository override rule.
    child = common.native_identity(
        mirrored(checkout, {"command": "pwd", "working_directory": str(tmp_path)}), "cursor"
    )
    with pytest.raises(core.WorkspaceError, match="REPOSITORY_MISMATCH"):
        common.native_tool({**child, "child": True}, "cursor")
    # A top-level cwd that the Shell input does not repeat still names the session.
    foreign = mirrored(checkout, {"command": "pwd", "cwd": str(checkout)})
    foreign["cwd"] = str(tmp_path)
    with pytest.raises(core.WorkspaceError, match="REPOSITORY_MISMATCH"):
        common.native_identity(foreign, "cursor")
    # Only Shell's directory is mirrored; another tool repeating it still moves nothing.
    for tool in ("Write", "MCP:navigate"):
        spoofed = {**mirrored(checkout, {"path": "x", "cwd": str(tmp_path)}), "tool_name": tool}
        with pytest.raises(core.WorkspaceError, match="REPOSITORY_MISMATCH"):
            common.native_identity(spoofed, "cursor")
    assert not (checkout / ".task").exists()
