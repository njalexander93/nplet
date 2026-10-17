"""Exercise ticket-first native adapter decisions with an in-memory binding store."""

from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from nplet.adapters import claude, common, cursor, startup
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


ISSUE = "TEST-30"
UUID = "0920cdcc-1644-4fa1-bf71-eb8370134c70"
ROOT = Path("/ticket-unit-repository")


class MemoryDirectory:
    """Expose the binding directory operations used by ticket startup."""

    def __init__(self, entries: dict[str, Any], path: str = "") -> None:
        """Bind a directory view to shared entries.

        Args:
            entries: Shared files and directory markers.
            path: Prefix for the selected directory.
        """
        self.entries = entries
        self.path = path

    def __enter__(self) -> MemoryDirectory:
        """Open the in-memory directory view.

        Returns:
            This directory view.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the in-memory directory view.

        Args:
            _args: Context manager exception values, if any.
        """

    def child(self, name: str) -> MemoryDirectory:
        """Select a child directory without touching disk.

        Args:
            name: Child directory name.

        Returns:
            A view of the child directory.
        """
        return MemoryDirectory(self.entries, f"{self.path}/{name}")

    def lock(self, name: str) -> nullcontext[None]:
        """Model a held local lock for sequential unit calls.

        Args:
            name: Name of the required assignment lock.

        Returns:
            A no-op context manager.
        """
        assert name == "assignment.lock"
        return nullcontext()

    def exists(self, name: str) -> bool:
        """Report whether one directory or file marker exists.

        Args:
            name: Relative entry name.

        Returns:
            Whether the entry exists.
        """
        return f"{self.path}/{name}" in self.entries

    def json(self, name: str) -> Any:
        """Read one persisted JSON value as a separate object.

        Args:
            name: Relative entry name.

        Returns:
            A copy of the stored value.
        """
        return copy.deepcopy(self.entries[f"{self.path}/{name}"])

    def put(self, name: str, value: Any) -> None:
        """Persist a snapshot of one JSON value.

        Args:
            name: Relative entry name.
            value: JSON-compatible value to store.
        """
        self.entries[f"{self.path}/{name}"] = copy.deepcopy(value)

    def unlink(self, name: str) -> None:
        """Remove exactly one stored marker.

        Args:
            name: Relative entry name.
        """
        del self.entries[f"{self.path}/{name}"]


def install_bindings(monkeypatch: pytest.MonkeyPatch, host: str) -> dict[str, Any]:
    """Install only repository discovery and directory persistence boundaries.

    Args:
        monkeypatch: Replaces Git discovery and binding persistence with an in-memory directory.
        host: Native adapter whose session owns the assignment.

    Returns:
        The mutable in-memory directory entries.
    """
    # Represent the selected session's durable assignment and first-read marker.
    key = core.participant_key({"host": host, "session_id": "session"})
    assignment = {"issue_id": ISSUE}
    entries: dict[str, Any] = {
        "/.task": None,
        "/.task/.bindings": None,
        f"/.task/.bindings/{key}.assignment.json": assignment,
        f"/.task/.bindings/{key}.lookup-required.json": copy.deepcopy(assignment),
    }
    # Keep repository discovery and all binding I/O inside the test's memory map.
    monkeypatch.setattr(common.core, "repository", lambda _cwd: (ROOT, None, [ROOT]))
    monkeypatch.setattr(common.core.Directory, "absolute", lambda _root: MemoryDirectory(entries))
    return entries


def event(host: str, token: str = "call-1", **changes: object) -> dict[str, Any]:
    """Build one documented native Linear callback.

    Args:
        host: Claude Code or Cursor callback grammar.
        token: Native tool call identity.
        changes: Fields that distinguish a test case.

    Returns:
        A callback with explicit session and provider fields.
    """
    # Start with one observed session and native call ID.
    base: dict[str, Any] = {"cwd": str(ROOT), "session_id": "session", "tool_use_id": token}
    # Encode each host's documented tool name and provider metadata.
    if host == "claude-code":
        base.update(
            tool_name="mcp__linear__get_issue",
            mcp_server={"name": "linear", "source": "user"},
            tool_input={"id": ISSUE},
        )
    else:
        # Cursor's generic callback names the native tool without server fields.
        base.update(tool_name="MCP:get_issue", tool_input={"id": ISSUE})
    # Apply only the fields selected by the scenario.
    base.update(changes)
    return base


def cursor_specific(**changes: object) -> dict[str, Any]:
    """Build the server-specific half of a Cursor pre-hook pair.

    Args:
        changes: Fields that distinguish a test case.

    Returns:
        A callback with configured server identity and encoded input.
    """
    # Preserve session identity while substituting the MCP server callback schema.
    base = event("cursor")
    base.update(
        tool_name="get_issue",
        mcp_server_name="linear",
        mcp_server_url="https://mcp.linear.app/mcp",
        tool_input=json.dumps({"id": ISSUE}),
    )
    # Vary server evidence or argument bytes for negative cases.
    base.update(changes)
    return base


def code(error: pytest.ExceptionInfo[core.WorkspaceError]) -> str:
    """Read the contractual diagnostic from a caught workspace error.

    Args:
        error: Captured workspace exception.

    Returns:
        Its stable diagnostic code.
    """
    return error.value.code


def test_lookup_marker_requires_identity_and_both_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip storage for missing native identity and distinguish absent markers.

    Args:
        monkeypatch: Replaces repository discovery and directory reads for marker cases.
    """
    # Select a marked Claude session, then try identities that cannot own a marker.
    entries = install_bindings(monkeypatch, "claude-code")
    observed = event("claude-code")
    # Reject synthetic sessions before repository discovery.
    assert common.lookup_required({"cwd": str(ROOT)}, "claude-code") is False
    assert common.lookup_required({"session_id": "session"}, "claude-code") is False
    assert common.lookup_required(observed, "claude-code") is True
    assert common.lookup_required(observed, "cursor") is False
    # The task root, bindings directory, and marker each gate the assignment.
    for suffix in ("/.task", "/.task/.bindings"):
        saved = entries.pop(suffix)
        assert common.lookup_required(observed, "claude-code") is False
        entries[suffix] = saved
    key = core.participant_key({"host": "claude-code", "session_id": "session"})
    entries.pop(f"/.task/.bindings/{key}.lookup-required.json")
    assert common.lookup_required(observed, "claude-code") is False


@pytest.mark.parametrize("host", ["claude-code", "cursor"])
def test_linear_tool_ignores_other_tools_and_validates_provenance(host: str) -> None:
    """Accept only the provider's documented name, server, and argument form.

    Args:
        host: Native callback grammar under test.
    """
    # A supported native provider event yields only its call token and input.
    valid = event(host) if host == "claude-code" else cursor_specific()
    assert common._linear_tool({**valid, "tool_name": "other"}, host) is None
    token, arguments = common._linear_tool(valid, host)  # type: ignore[misc]
    assert arguments == {"id": ISSUE}
    assert token == ("call-1" if host == "claude-code" else "cursor-mcp-server-checked")
    # Change each host's provenance fields while preserving its tool name.
    if host == "claude-code":
        malformed = [
            {"mcp_server": None},
            {"mcp_server": {"name": "other", "source": "user"}},
            {"mcp_server": {"name": "linear", "source": "unknown"}},
            {"mcp_server": {"name": "linear-server", "source": "user"}},
        ]
        # Claude must reject every unsupported server provenance combination.
        for changed in malformed:
            # Each server mismatch is a provider-boundary failure.
            with pytest.raises(core.WorkspaceError) as caught:
                common._linear_tool({**valid, **changed}, host)
            assert code(caught) == "HOST_UNSUPPORTED_PROVIDER"
        # An empty native call ID cannot correlate a completion.
        with pytest.raises(core.WorkspaceError) as caught:
            common._linear_tool({**valid, "tool_use_id": ""}, host)
        assert code(caught) == "INVALID_REQUEST"
    else:
        # Cursor checks configured server name and URL independently.
        for changed in (
            {"mcp_server_name": "other"},
            {"mcp_server_url": "https://example.com/mcp"},
        ):
            # A foreign server must fail before its arguments are trusted.
            with pytest.raises(core.WorkspaceError) as caught:
                common._linear_tool({**valid, **changed}, host)
            assert code(caught) == "HOST_UNSUPPORTED_PROVIDER"
        # MCP-specific arguments arrive as JSON text, never a native object.
        with pytest.raises(core.WorkspaceError) as caught:
            common._linear_tool({**valid, "tool_input": {"id": ISSUE}}, host)
        assert code(caught) == "INVALID_REQUEST"
        # Malformed JSON cannot supply the issue identifier.
        with pytest.raises(core.WorkspaceError) as caught:
            common._linear_tool({**valid, "tool_input": '{"id":'}, host)
        assert code(caught) == "INVALID_REQUEST"
        readonly = {**valid, "mcp_server_url": "https://mcp.linear.app/mcp/readonly"}
        assert common._linear_tool(readonly, host) is not None
    # A structured tool result cannot be mistaken for issue-read arguments.
    with pytest.raises(core.WorkspaceError) as caught:
        common._linear_tool({**valid, "tool_input": [] if host == "claude-code" else "[]"}, host)
    assert code(caught) == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "response,expected",
    [
        ({"id": UUID, "identifier": ISSUE}, None),
        ({"id": UUID, "identifier": "TEST-31"}, "ISSUE_MISMATCH"),
        ({"id": "not-a-uuid", "identifier": ISSUE}, "PROVIDER_RESPONSE_INVALID"),
        ({"id": UUID.upper(), "identifier": ISSUE}, "PROVIDER_RESPONSE_INVALID"),
        ({"id": ISSUE, "uuid": UUID}, "PROVIDER_RESPONSE_INVALID"),
        ([], "PROVIDER_RESPONSE_INVALID"),
    ],
)
def test_ticket_accepts_exact_native_issue_object_or_rejects_bad_identity(
    response: object, expected: str | None
) -> None:
    """Normalize GraphQL issue fields while preserving identity failures.

    Args:
        response: Native provider issue shape.
        expected: Error code, or None for a valid issue.
    """
    # Native issue objects must carry both exact shorthand ID and immutable UUID.
    if expected is None:
        assert startup.ticket(response, ISSUE) == {"id": ISSUE, "identifier": ISSUE, "uuid": UUID}
    else:
        # A mismatched shorthand ID or noncanonical UUID is a distinct failure.
        with pytest.raises(core.WorkspaceError) as caught:
            startup.ticket(response, ISSUE)
        assert code(caught) == expected


@pytest.mark.parametrize(
    "failure,expected",
    [
        ({"isError": True, "code": "NOT_FOUND"}, "ISSUE_NOT_FOUND"),
        ({"isError": True, "code": "UNAUTHORIZED"}, "PROVIDER_AUTH_REQUIRED"),
        ({"isError": True, "code": "FORBIDDEN"}, "PROVIDER_PERMISSION_DENIED"),
        ({"isError": True, "code": "NETWORK_ERROR"}, "PROVIDER_NETWORK_ERROR"),
        ({"isError": True, "code": "TIMEOUT"}, "PROVIDER_NETWORK_ERROR"),
        ({"isError": True, "code": "OTHER"}, "PROVIDER_ERROR"),
        ({"isError": False, "content": []}, "PROVIDER_RESPONSE_INVALID"),
        (
            {"isError": False, "content": [{"type": "text", "text": "{"}]},
            "PROVIDER_RESPONSE_INVALID",
        ),
    ],
)
def test_ticket_distinguishes_provider_failure_from_bad_success(
    failure: object, expected: str
) -> None:
    """Keep absence, access, network, and malformed response diagnostics separate.

    Args:
        failure: Provider envelope under test.
        expected: Contractual error code.
    """
    # Each error envelope keeps its distinct recovery diagnostic.
    with pytest.raises(core.WorkspaceError) as caught:
        startup.ticket(failure, ISSUE)
    assert code(caught) == expected


def test_ticket_parses_connector_text_and_rejects_missing_uuid() -> None:
    """Validate the connector envelope after parsing its sole text item."""
    # The connector's successful text envelope must normalize to the same issue.
    valid = {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps({"id": ISSUE, "uuid": UUID})}],
    }
    assert startup.ticket(valid, ISSUE) == {"id": ISSUE, "uuid": UUID}
    # Parsed JSON still has to pass identity and object-shape validation.
    for issue, expected in (
        ({"id": ISSUE}, "PROVIDER_RESPONSE_INVALID"),
        ({"id": "TEST-31", "uuid": UUID}, "ISSUE_MISMATCH"),
        ([{"id": ISSUE}], "PROVIDER_RESPONSE_INVALID"),
    ):
        response = {"isError": False, "content": [{"type": "text", "text": json.dumps(issue)}]}
        # Each parsed payload must fail at its own identity or shape boundary.
        with pytest.raises(core.WorkspaceError) as caught:
            startup.ticket(response, ISSUE)
        assert code(caught) == expected
    # A non-text result cannot masquerade as a valid issue object.
    with pytest.raises(core.WorkspaceError) as caught:
        startup.ticket({"isError": False, "content": [{"type": "other", "text": "{}"}]}, ISSUE)
    assert code(caught) == "PROVIDER_RESPONSE_INVALID"


def test_ticket_distinguishes_observed_missing_reference_from_other_400() -> None:
    """Treat only the fully observed Linear diagnostic as unresolved."""
    # Match the precise connector envelope that confirms an unresolved reference.
    detail = {
        "error": "invalid_request",
        "status": 400,
        "message": "Could not find referenced Issue.",
        "requestId": "req-1",
    }
    envelope = {
        "isError": True,
        "structuredContent": {"error_code": "INVALID_ARGUMENT"},
        "content": [{"type": "text", "text": json.dumps(detail)}],
    }
    # The exact observed connector envelope confirms unresolved issue identity.
    with pytest.raises(core.WorkspaceError) as caught:
        startup.ticket(envelope, ISSUE)
    assert code(caught) == "LINEAR_ISSUE_UNRESOLVED"
    # A different provider message cannot be reclassified as missing issue.
    detail["message"] = "Authentication failed."
    envelope["content"] = [{"type": "text", "text": json.dumps(detail)}]
    # A different error text cannot inherit the missing-reference diagnostic.
    with pytest.raises(core.WorkspaceError) as caught:
        startup.ticket(envelope, ISSUE)
    assert code(caught) == "PROVIDER_ERROR"


def test_native_lookup_skips_other_tools_and_rejects_malformed_generic_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop irrelevant or malformed callbacks before opening a binding.

    Args:
        monkeypatch: Fails if an irrelevant callback touches repository state.
    """
    # Ignore unrelated callbacks without opening any repository state.
    monkeypatch.setattr(common.core, "repository", lambda _cwd: pytest.fail("binding opened"))
    assert common.native_ticket_lookup(event("cursor", tool_name="other"), "cursor") is None
    assert (
        common.native_ticket_lookup(event("claude-code", tool_name="other"), "claude-code") is None
    )
    assert (
        common.native_ticket_lookup(cursor_specific(tool_name="other"), "cursor", specific=True)
        is None
    )
    # A matching Cursor tool name still needs object arguments.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(event("cursor", tool_input="{}"), "cursor")
    assert code(caught) == "INVALID_REQUEST"


@pytest.mark.parametrize("response_shape", ["issue", "content-list", "serialized-content-list"])
def test_claude_ticket_completion_requires_exact_native_call(
    monkeypatch: pytest.MonkeyPatch, response_shape: str
) -> None:
    """Reject unsolicited and competing completions while allowing an exact retry.

    Args:
        monkeypatch: Replaces binding storage and local startup for call correlation.
        response_shape: Normalized issue or Desktop's raw or serialized content list.
    """
    # Completion without an admitted native call must not start a workspace.
    entries = install_bindings(monkeypatch, "claude-code")
    callback = event("claude-code", tool_response={"id": UUID, "identifier": ISSUE})
    # Exercise Desktop's observed response through the real shared ticket parser.
    if response_shape != "issue":
        blocks = [{"type": "text", "text": json.dumps({"id": ISSUE, "uuid": UUID})}]
        callback["tool_response"] = (
            json.dumps(blocks) if response_shape == "serialized-content-list" else blocks
        )
    monkeypatch.setattr(
        startup,
        "start",
        lambda *_args, **_kwargs: {"participant_id": "coordinator", "coordinator": "coordinator"},
    )
    # An unsolicited completion has no admission record to settle.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(callback, "claude-code", complete=True)
    assert code(caught) == "BINDING_MISSING"
    # Record one call and reject a competing pre-hook or post-hook identity.
    assert common.native_ticket_lookup(callback, "claude-code") == {}
    # A different native pre-hook cannot overwrite this in-flight call.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(event("claude-code", "other"), "claude-code")
    assert code(caught) == "BINDING_CONFLICT"
    # A completion with the wrong call ID cannot consume the original marker.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(
            {**callback, "tool_use_id": "other"}, "claude-code", complete=True
        )
    assert code(caught) == "BINDING_CONFLICT"
    # Only the original call can settle and remove its lookup marker.
    result = common.native_ticket_lookup(callback, "claude-code", complete=True)
    assert result == {"issue_id": ISSUE, "reader": False}
    key = core.participant_key({"host": "claude-code", "session_id": "session"})
    assert f"/.task/.bindings/{key}.lookup-required.json" not in entries
    assert f"/.task/.bindings/{key}.lookup.json" not in entries


@pytest.mark.parametrize(
    "blocks,expected",
    [
        ([], "PROVIDER_RESPONSE_INVALID"),
        ([{"type": "text", "text": "{}"}] * 2, "PROVIDER_RESPONSE_INVALID"),
        (
            [{"type": "text", "text": json.dumps({"id": "TEST-99", "uuid": UUID})}],
            "ISSUE_MISMATCH",
        ),
    ],
)
def test_claude_content_list_preserves_provider_validation(
    monkeypatch: pytest.MonkeyPatch, blocks: list[dict[str, str]], expected: str
) -> None:
    """Reject malformed or wrong-ticket Desktop results before starting a workspace.

    Args:
        monkeypatch: Replaces storage and forbids startup for an invalid result.
        blocks: Desktop content list returned by the admitted read.
        expected: Diagnostic from the real shared provider parser.
    """
    # Admit one native call without creating any real repository or workspace.
    entries = install_bindings(monkeypatch, "claude-code")
    callback = event("claude-code", tool_response=blocks)
    assert common.native_ticket_lookup(callback, "claude-code") == {}
    monkeypatch.setattr(
        startup, "start", lambda *_args, **_kwargs: pytest.fail("workspace started")
    )
    # Wrapping the list must retain parser rejection and permit a fresh exact read.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(callback, "claude-code", complete=True)
    assert code(caught) == expected
    key = core.participant_key({"host": "claude-code", "session_id": "session"})
    assert f"/.task/.bindings/{key}.lookup.json" not in entries
    assert f"/.task/.bindings/{key}.lookup-required.json" in entries


@pytest.mark.parametrize("order", [(False, True), (True, False)])
def test_cursor_requires_both_prehooks_in_either_order(
    monkeypatch: pytest.MonkeyPatch, order: tuple[bool, bool]
) -> None:
    """Settle only a native ID paired with the configured Linear server.

    Args:
        monkeypatch: Replaces binding storage and records verified local startup.
        order: Whether each pre-hook is the server-specific callback.
    """
    # Capture local startup so only a verified paired provider read can invoke it.
    entries = install_bindings(monkeypatch, "cursor")
    generic = event("cursor", tool_output={"id": UUID, "identifier": ISSUE})
    specific = cursor_specific()
    started: list[dict[str, Any]] = []

    def start(_event: object, issue: dict[str, Any], *, host: str) -> dict[str, str]:
        """Capture the verified issue passed across startup's boundary.

        Args:
            _event: Native callback already admitted by the unit.
            issue: Normalized issue identity.
            host: Native adapter identity.

        Returns:
            Reader and coordinator identities.
        """
        assert host == "cursor"
        started.append(issue)
        return {"participant_id": "reader", "coordinator": "coordinator"}

    monkeypatch.setattr(startup, "start", start)
    # A single pre-hook cannot settle a provider response.
    first = specific if order[0] else generic
    second = specific if order[1] else generic
    assert common.native_ticket_lookup(first, "cursor", specific=order[0]) == {}
    # Missing either server proof or native call ID blocks completion.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(generic, "cursor", complete=True)
    assert code(caught) == "BINDING_CONFLICT"
    assert started == []
    # The complementary server/native-ID fact completes admission.
    assert common.native_ticket_lookup(second, "cursor", specific=order[1]) == {}
    assert common.native_ticket_lookup(generic, "cursor", complete=True) == {
        "issue_id": ISSUE,
        "reader": True,
    }
    assert started == [{"id": ISSUE, "identifier": ISSUE, "uuid": UUID}]
    key = core.participant_key({"host": "cursor", "session_id": "session"})
    assert f"/.task/.bindings/{key}.lookup-required.json" not in entries


def test_cursor_competing_and_duplicate_callbacks_preserve_pending_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the first admitted native call active through stale callbacks.

    Args:
        monkeypatch: Replaces binding storage to inspect unchanged pending state.
    """
    # Record the generic native call and preserve its marker on invalid repeats.
    entries = install_bindings(monkeypatch, "cursor")
    generic = event("cursor")
    specific = cursor_specific()
    assert common.native_ticket_lookup(generic, "cursor") == {}
    key = core.participant_key({"host": "cursor", "session_id": "session"})
    name = f"/.task/.bindings/{key}.lookup.json"
    snapshot = copy.deepcopy(entries[name])
    # Repeated and competing generic pre-hooks cannot displace the first call.
    for callback, specific_flag in (
        (generic, False),
        (event("cursor", "call-2"), False),
    ):
        # Preserve the pending record after each rejected duplicate.
        with pytest.raises(core.WorkspaceError) as caught:
            common.native_ticket_lookup(callback, "cursor", specific=specific_flag)
        assert code(caught) == "PROVIDER_LOOKUP_IN_FLIGHT"
        assert entries[name] == snapshot
    # Once server provenance is paired, a repeated server callback remains invalid.
    assert common.native_ticket_lookup(specific, "cursor", specific=True) == {}
    paired = copy.deepcopy(entries[name])
    # The same MCP-specific callback cannot be paired twice.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(specific, "cursor", specific=True)
    assert code(caught) == "PROVIDER_LOOKUP_IN_FLIGHT"
    assert entries[name] == paired
    # A competing completion cannot consume the original pending read.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(
            event("cursor", "call-2", tool_output={"id": UUID, "identifier": ISSUE}),
            "cursor",
            complete=True,
        )
    assert code(caught) == "BINDING_CONFLICT"
    assert entries[name] == paired


@pytest.mark.parametrize("host", ["claude-code", "cursor"])
def test_provider_error_clears_only_attempt_and_allows_fresh_retry(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """Preserve Task assignment while a malformed provider result is retried.

    Args:
        monkeypatch: Replaces binding storage to inspect retry markers.
        host: Native callback grammar under test.
    """
    # Admit a real native read whose provider body is malformed.
    entries = install_bindings(monkeypatch, host)
    generic = event(
        host,
        tool_response="broken" if host == "claude-code" else None,
        tool_output="broken" if host == "cursor" else None,
    )
    assert common.native_ticket_lookup(generic, host) == {}
    # Cursor needs its configured-server pre-hook before provider completion.
    if host == "cursor":
        assert common.native_ticket_lookup(cursor_specific(), host, specific=True) == {}
    # Failed decoding clears only the attempt, leaving Task selection intact.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(generic, host, complete=True)
    assert code(caught) == "PROVIDER_RESPONSE_INVALID"
    key = core.participant_key({"host": host, "session_id": "session"})
    assert f"/.task/.bindings/{key}.lookup.json" not in entries
    assert f"/.task/.bindings/{key}.lookup-required.json" in entries
    # A new native ID may start a fresh provider attempt.
    retry = event(host, "call-2")
    assert common.native_ticket_lookup(retry, host) == {}


@pytest.mark.parametrize("host", ["claude-code", "cursor"])
@pytest.mark.parametrize(
    "provider_code,expected",
    [
        ("NOT_FOUND", "ISSUE_NOT_FOUND"),
        ("UNAUTHORIZED", "PROVIDER_AUTH_REQUIRED"),
        ("NETWORK_ERROR", "PROVIDER_NETWORK_ERROR"),
    ],
)
def test_native_provider_failures_never_start_workspace(
    monkeypatch: pytest.MonkeyPatch, host: str, provider_code: str, expected: str
) -> None:
    """Keep provider absence and operational errors outside local registration.

    Args:
        monkeypatch: Replaces binding storage and forbids local startup on provider failure.
        host: Native callback grammar under test.
        provider_code: Provider's reported failure.
        expected: Stable diagnostic for that failure.
    """
    # Admit the call with both required Cursor facts when applicable.
    entries = install_bindings(monkeypatch, host)
    callback = event(host)
    assert common.native_ticket_lookup(callback, host) == {}
    # Pair Cursor's server proof with the same pending native call.
    if host == "cursor":
        assert common.native_ticket_lookup(cursor_specific(), host, specific=True) == {}
    monkeypatch.setattr(
        startup, "start", lambda *_args, **_kwargs: pytest.fail("workspace started")
    )
    field = "tool_response" if host == "claude-code" else "tool_output"
    callback[field] = {"isError": True, "code": provider_code}
    # Every provider failure retains Task selection but removes the failed attempt.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(callback, host, complete=True)
    assert code(caught) == expected
    key = core.participant_key({"host": host, "session_id": "session"})
    assert f"/.task/.bindings/{key}.lookup.json" not in entries
    assert f"/.task/.bindings/{key}.lookup-required.json" in entries


@pytest.mark.parametrize("host", ["claude-code", "cursor"])
def test_failed_provider_callback_requires_active_matching_call(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """Failure clears only its own pending attempt and leaves Task selected.

    Args:
        monkeypatch: Replaces binding storage to inspect failed-call cleanup.
        host: Native callback grammar under test.
    """
    # Admit a call before applying failure callbacks to its correlation marker.
    entries = install_bindings(monkeypatch, host)
    generic = event(host)
    assert common.native_ticket_lookup(generic, host) == {}
    key = core.participant_key({"host": host, "session_id": "session"})
    name = f"/.task/.bindings/{key}.lookup.json"
    # Changed tool ID and changed issue arguments both fail correlation.
    for changed in ({"tool_use_id": "other"}, {"tool_input": {"id": "TEST-31"}}):
        # A stale failure must leave the active marker in place.
        with pytest.raises(core.WorkspaceError) as caught:
            common.native_ticket_failed({**generic, **changed}, host)
        assert code(caught) == "BINDING_CONFLICT"
        assert name in entries
    # Only the matching failure removes the attempt and permits a new call.
    common.native_ticket_failed(generic, host)
    assert name not in entries
    assert f"/.task/.bindings/{key}.lookup-required.json" in entries
    assert common.native_ticket_lookup(event(host, "call-2"), host) == {}


def test_failed_provider_callback_rejects_wrong_tool_and_input_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep malformed failure notifications from clearing a provider attempt.

    Args:
        monkeypatch: Replaces binding storage to inspect malformed failure callbacks.
    """
    # Keep a valid attempt active while malformed failure notices arrive.
    entries = install_bindings(monkeypatch, "cursor")
    assert common.native_ticket_lookup(event("cursor"), "cursor") == {}
    key = core.participant_key({"host": "cursor", "session_id": "session"})
    name = f"/.task/.bindings/{key}.lookup.json"
    # Wrong tool name is unrelated to the active failure callback.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_failed(event("cursor", tool_name="other"), "cursor")
    assert code(caught) == "BINDING_CONFLICT"
    # String arguments cannot stand in for the generic callback's object.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_failed(event("cursor", tool_input="{}"), "cursor")
    assert code(caught) == "INVALID_REQUEST"
    assert name in entries
    # Claude also requires a supported provider tool on failure.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_failed(event("claude-code", tool_name="other"), "claude-code")
    assert code(caught) == "BINDING_CONFLICT"


def test_claude_startup_failure_retains_exact_completed_result_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry setup without losing the verified provider correlation.

    Args:
        monkeypatch: Replaces storage and makes the first local setup fail.
    """
    # Keep the admitted provider call stable across a failing local setup.
    entries = install_bindings(monkeypatch, "claude-code")
    callback = event("claude-code", tool_response={"id": UUID, "identifier": ISSUE})
    assert common.native_ticket_lookup(callback, "claude-code") == {}
    attempts = 0

    def start(_event: object, _issue: object, *, host: str) -> dict[str, str]:
        """Fail one local setup, then allow the same provider result.

        Args:
            _event: Native callback already admitted.
            _issue: Validated provider issue.
            host: Native adapter identity.

        Returns:
            Coordinator identity on retry.

        Raises:
            OSError: First local setup attempt fails.
        """
        nonlocal attempts
        assert host == "claude-code"
        attempts += 1
        # Fail registration once to exercise exact-result retry state.
        if attempts == 1:
            raise OSError("local setup failed")
        return {"participant_id": "coordinator", "coordinator": "coordinator"}

    monkeypatch.setattr(startup, "start", start)
    # The completed marker permits an exact-result retry without another read.
    with pytest.raises(OSError, match="local setup failed"):
        common.native_ticket_lookup(callback, "claude-code", complete=True)
    key = core.participant_key({"host": "claude-code", "session_id": "session"})
    name = f"/.task/.bindings/{key}.lookup.json"
    assert entries[name]["completed"] is True
    assert common.native_ticket_lookup(callback, "claude-code") == {}
    assert entries[name]["completed"] is True
    assert common.native_ticket_lookup(callback, "claude-code", complete=True) == {
        "issue_id": ISSUE,
        "reader": False,
    }
    assert attempts == 2
    assert name not in entries


def test_claude_completed_setup_allows_new_native_read_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace a completed failed setup with a newly admitted provider call.

    Args:
        monkeypatch: Replaces storage and forces local registration failure.
    """
    # Mark the first provider call completed after local registration fails.
    entries = install_bindings(monkeypatch, "claude-code")
    first = event("claude-code", tool_response={"id": UUID, "identifier": ISSUE})
    assert common.native_ticket_lookup(first, "claude-code") == {}
    monkeypatch.setattr(
        startup,
        "start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("registration failed")),
    )
    # The old call reaches provider success but fails local setup.
    with pytest.raises(OSError, match="registration failed"):
        common.native_ticket_lookup(first, "claude-code", complete=True)
    key = core.participant_key({"host": "claude-code", "session_id": "session"})
    name = f"/.task/.bindings/{key}.lookup.json"
    assert entries[name] == {"id": ISSUE, "tool_id": "call-1", "completed": True}
    # A fresh native call replaces that completed record and excludes stale output.
    second = event("claude-code", "call-2", tool_response={"id": UUID, "identifier": ISSUE})
    assert common.native_ticket_lookup(second, "claude-code") == {}
    assert entries[name] == {"id": ISSUE, "tool_id": "call-2"}
    # Old completion cannot settle the replacement native call.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(first, "claude-code", complete=True)
    assert code(caught) == "BINDING_CONFLICT"
    assert entries[name] == {"id": ISSUE, "tool_id": "call-2"}


@pytest.mark.parametrize("failure_stage", ["provider", "startup"])
def test_stale_failure_does_not_clear_newer_lookup_attempt(
    monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    """Retain a newer native attempt when an older completion fails late.

    Args:
        monkeypatch: Replaces provider validation or startup while another call supersedes it.
        failure_stage: Boundary at which another attempt supersedes the old one.
    """
    # Admit one call and model a newer call appearing during old-call settlement.
    entries = install_bindings(monkeypatch, "claude-code")
    callback = event("claude-code", tool_response={"id": UUID, "identifier": ISSUE})
    assert common.native_ticket_lookup(callback, "claude-code") == {}
    key = core.participant_key({"host": "claude-code", "session_id": "session"})
    name = f"/.task/.bindings/{key}.lookup.json"
    newer = {"id": ISSUE, "tool_id": "new-call"}

    def fail_provider(_response: object, _identifier: str) -> dict[str, object]:
        """Simulate a newer admitted call before provider validation fails.

        Args:
            _response: Native provider response.
            _identifier: Selected shorthand issue ID.

        Raises:
            core.WorkspaceError: The old provider response is invalid.
        """
        entries[name] = newer
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")

    def fail_startup(_event: object, _issue: object, *, host: str) -> dict[str, str]:
        """Simulate a newer admitted call before local setup fails.

        Args:
            _event: Native callback already admitted.
            _issue: Verified provider issue.
            host: Native adapter identity.

        Raises:
            OSError: The old local startup failed.
        """
        assert host == "claude-code"
        entries[name] = newer
        raise OSError("setup failed")

    # Fail at either external boundary while preserving the newer correlation.
    if failure_stage == "provider":
        monkeypatch.setattr(startup, "ticket", fail_provider)
        expected: type[Exception] = core.WorkspaceError
    else:
        # Local setup can also fail after another call replaces the marker.
        monkeypatch.setattr(startup, "start", fail_startup)
        expected = OSError
    # Neither late failure may delete the newer attempt.
    with pytest.raises(expected):
        common.native_ticket_lookup(callback, "claude-code", complete=True)
    assert entries[name] == newer
    assert f"/.task/.bindings/{key}.lookup-required.json" in entries


@pytest.mark.parametrize("retry_order", [(False, True), (True, False)])
def test_cursor_startup_failure_requires_a_fresh_native_read(
    monkeypatch: pytest.MonkeyPatch, retry_order: tuple[bool, bool]
) -> None:
    """Discard failed setup's Cursor attempt and admit a fresh paired read.

    Args:
        monkeypatch: Replaces storage and makes the first local setup fail.
        retry_order: Whether each retry pre-hook is server-specific.
    """
    # Pair both pre-hooks before the first provider result reaches local setup.
    entries = install_bindings(monkeypatch, "cursor")
    first = event("cursor", tool_output={"id": UUID, "identifier": ISSUE})
    assert common.native_ticket_lookup(first, "cursor") == {}
    assert common.native_ticket_lookup(cursor_specific(), "cursor", specific=True) == {}
    calls = 0

    def start(_event: object, _issue: object, *, host: str) -> dict[str, str]:
        """Fail first local startup then report a ready coordinator.

        Args:
            _event: Native callback already admitted.
            _issue: Validated provider issue.
            host: Native adapter identity.

        Returns:
            Coordinator identity on retry.

        Raises:
            OSError: First local setup attempt fails.
        """
        nonlocal calls
        assert host == "cursor"
        calls += 1
        # Fail the first registration, then allow the fresh provider read.
        if calls == 1:
            raise OSError("local setup failed")
        return {"participant_id": "coordinator", "coordinator": "coordinator"}

    monkeypatch.setattr(startup, "start", start)
    # Cursor clears the failed attempt; a stale completion has no admission.
    with pytest.raises(OSError, match="local setup failed"):
        common.native_ticket_lookup(first, "cursor", complete=True)
    key = core.participant_key({"host": "cursor", "session_id": "session"})
    name = f"/.task/.bindings/{key}.lookup.json"
    assert name not in entries
    assert f"/.task/.bindings/{key}.lookup-required.json" in entries
    # A post-hook replay cannot settle without new pre-hooks.
    with pytest.raises(core.WorkspaceError) as caught:
        common.native_ticket_lookup(first, "cursor", complete=True)
    assert code(caught) == "BINDING_MISSING"
    # Either pre-hook order may establish a new paired read with a fresh ID.
    retry = event("cursor", "call-2", tool_output={"id": UUID, "identifier": ISSUE})
    # Each pre-hook contributes one independent admission fact.
    for specific in retry_order:
        callback = cursor_specific() if specific else retry
        assert common.native_ticket_lookup(callback, "cursor", specific=specific) == {}
    assert common.native_ticket_lookup(retry, "cursor", complete=True) == {
        "issue_id": ISSUE,
        "reader": False,
    }
    assert calls == 2


@pytest.mark.parametrize(
    "adapter,pre,post,failed",
    [
        (claude, "PreToolUse", "PostToolUse", "PostToolUseFailure"),
        (cursor, "preToolUse", "postToolUse", "postToolUseFailure"),
    ],
)
@pytest.mark.usefixtures("recorded_operation", "protected_effect", "selected_ticket_dispatch")
def test_native_handlers_gate_ticket_first_and_route_completion(
    monkeypatch: pytest.MonkeyPatch, adapter: Any, pre: str, post: str, failed: str
) -> None:
    """Block unrelated work, admit exact read, and report startup or provider failure.

    Args:
        monkeypatch: Replaces host identity and ticket helper dispatch boundaries.
        adapter: Native host adapter.
        pre: Native pre-tool event name.
        post: Native successful completion event name.
        failed: Native failed completion event name.
    """
    # Isolate host routing while keeping the ticket gate active for every callback.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(adapter.common, "native_identity", lambda observed, _host: observed)
    monkeypatch.setattr(adapter.common, "lookup_required", lambda *_args: True)
    calls: list[tuple[bool, bool]] = []
    failures: list[str] = []

    def lookup(_event: object, _host: str, complete: bool = False, specific: bool = False) -> Any:
        """Record handler routing and return a verified ticket marker.

        Args:
            _event: Native callback supplied to the adapter.
            _host: Adapter identity.
            complete: Whether this callback is a provider completion.
            specific: Whether it is Cursor's MCP server callback.

        Returns:
            A verified ticket marker for the completion callback.
        """
        calls.append((complete, specific))
        return {"issue_id": ISSUE} if complete else {}

    monkeypatch.setattr(adapter.common, "native_ticket_lookup", lookup)
    monkeypatch.setattr(
        adapter.common, "native_ticket_failed", lambda _event, _host: failures.append(_host)
    )
    monkeypatch.setattr(
        adapter.common, "native_pre", lambda *_args: pytest.fail("ordinary tool admitted")
    )
    monkeypatch.setattr(
        adapter.common, "native_post", lambda *_args, **_kwargs: pytest.fail("ordinary completion")
    )
    name = "mcp__linear__get_issue" if adapter is claude else "MCP:get_issue"
    # The first exact lookup is admitted under each host's native response grammar.
    admitted = adapter.handle({"hook_event_name": pre, "tool_name": name})
    assert admitted == ({} if adapter is claude else {"permission": "allow"})
    assert calls == [(False, False)]
    # Completion reports readiness; provider failure reports its diagnostic.
    result = adapter.handle({"hook_event_name": post, "tool_name": name})
    assert ISSUE in (result.get("systemMessage") or result.get("additional_context"))
    assert calls[-1] == (True, False)
    failure = adapter.handle({"hook_event_name": failed, "tool_name": name})
    assert "PROVIDER_ERROR" in (failure.get("systemMessage") or failure.get("additional_context"))
    assert failures == [adapter.HOST]
    # An unrelated tool never falls through to ordinary pre-tool admission.
    monkeypatch.setattr(adapter.common, "native_ticket_lookup", lambda *_args, **_kwargs: None)
    denied = adapter.handle({"hook_event_name": pre, "tool_name": "other"})
    # Each host reports a denial in its own native decision field.
    if adapter is claude:
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    else:
        # Cursor's pre-tool permission field must deny this unrelated tool.
        assert denied["permission"] == "deny"


@pytest.mark.usefixtures("selected_ticket_dispatch")
def test_cursor_mcp_specific_admission_requires_ticket_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow only the matching server-specific callback during bootstrap.

    Args:
        monkeypatch: Replaces Cursor identity and ticket helper dispatch boundaries.
    """
    # Route the MCP server callback through ticket admission during bootstrap.
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(cursor.common, "native_identity", lambda observed, _host: observed)
    monkeypatch.setattr(cursor.common, "lookup_required", lambda *_args: True)
    seen: list[bool] = []

    def lookup(_event: object, _host: str, *, specific: bool) -> dict[str, object]:
        """Record the required server-specific admission flag.

        Args:
            _event: Native callback supplied to the adapter.
            _host: Adapter identity.
            specific: Server-specific admission flag.

        Returns:
            Empty admitted callback context.
        """
        seen.append(specific)
        return {}

    monkeypatch.setattr(cursor.common, "native_ticket_lookup", lookup)
    assert cursor.handle({"hook_event_name": "beforeMCPExecution"}) == {"permission": "allow"}
    assert seen == [True]
    assert cursor.handle({"hook_event_name": "afterMCPExecution"}) == {}
    # Missing provider identity must deny the same server callback.
    monkeypatch.setattr(cursor.common, "native_ticket_lookup", lambda *_args, **_kwargs: None)
    denied = cursor.handle({"hook_event_name": "beforeMCPExecution"})
    assert denied["permission"] == "deny"


@pytest.mark.usefixtures("recorded_operation", "selected_ticket_dispatch")
def test_native_completion_without_verified_ticket_stays_unready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deny a completion when the native ticket helper finds no matching tool.

    Args:
        monkeypatch: Replaces native ticket verification while the bootstrap gate is active.
    """
    # Keep the lookup gate active while the native helper rejects the callback.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(common, "native_identity", lambda observed, _host: observed)
    monkeypatch.setattr(common, "lookup_required", lambda *_args: True)
    monkeypatch.setattr(common, "native_ticket_lookup", lambda *_args, **_kwargs: None)
    for adapter, name, tool in (
        (claude, "PostToolUse", "mcp__linear__get_issue"),
        (cursor, "postToolUse", "MCP:get_issue"),
    ):
        # Both adapters translate the missing verified issue into a bounded diagnostic.
        result = adapter.handle({"hook_event_name": name, "tool_name": tool})
        diagnostic = result.get("systemMessage") or result.get("additional_context")
        assert "TICKET_READ_REQUIRED" in diagnostic


def test_cursor_unselected_mcp_callbacks_cannot_complete_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route unselected provider completion through ordinary tool settlement.

    Args:
        monkeypatch: Replaces ticket startup and records ordinary tool settlement.
    """
    # An unselected session has no ticket admission; its callbacks stay advisory.
    monkeypatch.delenv("CURSOR_CODE_REMOTE", raising=False)
    monkeypatch.setattr(cursor.common, "native_identity", lambda observed, _host: observed)
    monkeypatch.setattr(cursor.common, "lookup_required", lambda *_args: False)
    monkeypatch.setattr(
        cursor.common,
        "native_ticket_lookup",
        lambda *_args, **_kwargs: pytest.fail("ticket startup"),
    )
    settled: list[bool] = []
    monkeypatch.setattr(
        cursor.common,
        "native_post",
        lambda _event, _host, failed: settled.append(failed),
    )
    assert cursor.handle({"hook_event_name": "beforeMCPExecution"})["permission"] == "deny"
    assert cursor.handle({"hook_event_name": "afterMCPExecution"}) == {}
    # Without a pending Task, the generic posthook enters ordinary settlement.
    assert cursor.handle({"hook_event_name": "postToolUse", "tool_name": "MCP:get_issue"}) == {}
    assert settled == [False]
