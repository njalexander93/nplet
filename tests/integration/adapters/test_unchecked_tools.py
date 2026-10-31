"""Exercise unchecked Claude parent tools and the closed set around them in real stores."""

from collections.abc import Iterator

import pytest

from nplet.adapters import claude
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.test_native_hosts import native_event
from tests.support import Fixture
from tests.types import JsonObject

pytestmark = pytest.mark.integration
# Browser, computer-use, PDF-like, other desktop-app and built-in tools no route names.
UNCHECKED = [
    ("mcp__claude-in-chrome__navigate", {"url": "https://example.test"}),
    ("mcp__computer-use__screenshot", {}),
    ("mcp__pdf-tools__read_pdf", {"path": "/tmp/x.pdf"}),
    ("mcp__ccd_sidebar__list_groups", {}),
    ("WebFetch", {"url": "https://example.test", "prompt": "p"}),
]


@pytest.fixture
def case(monkeypatch: pytest.MonkeyPatch) -> Iterator[Fixture]:
    """Create a disposable repository whose coordinator is a Claude session.

    Args:
        monkeypatch: Scoped removal of the imported-hook marker.

    Yields:
        The started fixture, cleaned up afterwards.
    """
    # Exercise Claude's own entry point in an independent repository.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    fixture = Fixture()
    fixture.setUp()
    fixture.base["host"] = claude.HOST
    fixture.base["coordinator"] = core.participant_key(fixture.base)
    # Release the disposable repository even when an assertion fails.
    try:
        yield fixture
    finally:
        fixture.doCleanups()


def call(case: Fixture, tool: str, args: JsonObject, **fields: object) -> JsonObject:
    """Run one parent PreToolUse and its successful PostToolUse through the adapter.

    Args:
        case: Disposable repository and Claude session.
        tool: Native tool name.
        args: Native tool input.
        **fields: Extra envelope fields, such as the MCP server object.

    Returns:
        The PreToolUse response; the PostToolUse response must be empty.
    """
    # Admit, then complete, the same native call.
    pre = claude.handle(
        native_event("claude", case, "PreToolUse", tool_name=tool, tool_input=args, **fields)
    )
    post = native_event(
        "claude", case, "PostToolUse", tool_name=tool, tool_input=args, tool_response="ok", **fields
    )
    assert claude.handle(post) == {}
    return pre


def denied(response: JsonObject, code: str) -> bool:
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


@pytest.mark.parametrize(("tool", "args"), UNCHECKED)
def test_unbound_parent_unknown_tool_is_unchecked(
    case: Fixture, tool: str, args: JsonObject
) -> None:
    """Admit an unknown parent tool with no binding and no lifecycle record.

    Args:
        case: Disposable repository with no issue bound to the session.
        tool: Unknown native tool name.
        args: Native tool input.
    """
    # Neither the pre nor the post event denies or writes task state.
    assert call(case, tool, args) == {}
    assert not (case.root / ".task/TEST-1").exists()


@pytest.mark.parametrize(("tool", "args"), UNCHECKED)
def test_unready_parent_unknown_tool_records_nothing(
    case: Fixture, tool: str, args: JsonObject
) -> None:
    """Skip readiness, tool-start and settlement for an unknown tool of a bound session.

    Args:
        case: Disposable repository whose coordinator is bound but not ready.
        tool: Unknown native tool name.
        args: Native tool input.
    """
    # Bind without readiness and snapshot the state and event log.
    case.create()
    before = case.state()
    events = (case.root / ".task/TEST-1/events.jsonl").read_bytes()
    # The call passes and leaves both unchanged.
    assert call(case, tool, args) == {}
    assert case.state() == before
    assert (case.root / ".task/TEST-1/events.jsonl").read_bytes() == events


def test_pending_ticket_read_survives_unchecked_call(case: Fixture) -> None:
    """Keep the selected-ticket requirement across an unrelated unchecked call.

    Args:
        case: Disposable repository whose session selects a Task.
    """
    # Select a Task, run an unchecked call, then try ordinary gated work.
    claude.handle(native_event("claude", case, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert call(case, *UNCHECKED[0]) == {}
    write = native_event("claude", case, "PreToolUse")
    assert denied(claude.handle(write), "TICKET_READ_REQUIRED")


@pytest.mark.parametrize(
    ("tool", "fields", "code"),
    [
        (
            "mcp__ccd_pr__set_auto_merge",
            {"mcp_server": {"name": "ccd_pr", "source": "sdk"}},
            "HOST_UNSUPPORTED_PROVIDER",
        ),
        (
            "mcp__ccd_settings__set_setting",
            {"mcp_server": {"name": "ccd_settings", "source": "sdk"}},
            "HOST_UNSUPPORTED_PROVIDER",
        ),
        ("Workflow", {}, "HOST_UNSUPPORTED_CHILD_IDENTITY"),
        ("SubagentHandback", {}, "HOST_UNSUPPORTED_TOOL"),
        ("Monitor", {}, "HOST_UNSUPPORTED_TOOL"),
    ],
)
def test_ready_parent_closed_tools_stay_denied(
    case: Fixture, tool: str, fields: JsonObject, code: str
) -> None:
    """Deny closed tools even for a ready parent, without recording work.

    Args:
        case: Disposable repository whose coordinator becomes ready.
        tool: Closed native tool name.
        fields: Extra envelope fields, such as the MCP server object.
        code: Expected denial diagnostic.
    """
    # Make the coordinator ready, then request the closed tool.
    case.create()
    case.ready()
    before = case.state()
    event = native_event("claude", case, "PreToolUse", tool_name=tool, tool_input={}, **fields)
    assert denied(claude.handle(event), code)
    assert case.state() == before


@pytest.mark.parametrize("tool", ["EnterWorktree", "ExitWorktree"])
def test_worktree_moves_require_readiness_and_settle(case: Fixture, tool: str) -> None:
    """Gate worktree moves on readiness and track them as ordinary pending work.

    Args:
        case: Disposable repository whose coordinator becomes ready.
        tool: Worktree move tool.
    """
    # An unready coordinator cannot move worktrees.
    case.create()
    pre = native_event("claude", case, "PreToolUse", tool_name=tool, tool_input={})
    assert claude.handle(pre)["hookSpecificOutput"]["permissionDecision"] == "deny"
    # Once ready, the move is pending until its completion settles it.
    case.ready()
    assert claude.handle(pre) == {}
    pending = case.state()["participants"][case.base["coordinator"]]["pending"]
    assert list(pending) == ["actual-tool"]
    post = native_event(
        "claude", case, "PostToolUse", tool_name=tool, tool_input={}, tool_response="ok"
    )
    assert claude.handle(post) == {}
    assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}
