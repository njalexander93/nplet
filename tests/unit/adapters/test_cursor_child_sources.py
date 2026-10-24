"""Verify Cursor source receipts and independently scoped child lifecycle commands."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor_child_session as child_session
from nplet.adapters import cursor_children as children
from nplet.adapters import cursor_sources as cursor_sources
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def test_source_content_hashes_actual_utf8_and_js_utf16_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash callback text itself and persist only exact bounded metadata.

    Args:
        monkeypatch: Replaces only the core source transition boundary.
    """
    transition = MagicMock(return_value={"source_content_verified": True, "source_receipt": False})
    monkeypatch.setattr(cursor_sources, "source_transition", transition)
    text = "café\n😀\n"
    event = {"child": True, "content": text, "file_path": "/source", "attachments": []}
    result = cursor_sources.source_read_content(event, time.monotonic() + 1)
    assert result["source_receipt"] is False
    assert transition.call_args.args[1] == "content"
    assert transition.call_args.kwargs == {
        "source_path": "/source",
        "observed_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "content_length": len(text.encode("utf-16-le")) // 2,
    }
    assert "content" not in transition.call_args.kwargs


@pytest.mark.parametrize("content", [None, b"bytes", "\ud800"])
def test_source_content_rejects_nontext_or_lossy_encoding(
    monkeypatch: pytest.MonkeyPatch, content: object
) -> None:
    """Do not fabricate bytes when native text cannot be verified exactly.

    Args:
        monkeypatch: Enforces no transition after invalid source content.
        content: Invalid native source text.
    """
    transition = MagicMock()
    monkeypatch.setattr(cursor_sources, "source_transition", transition)
    with pytest.raises(core.WorkspaceError):
        cursor_sources.source_read_content(
            {"child": True, "content": content, "file_path": "/source", "attachments": []},
            time.monotonic() + 1,
        )
    transition.assert_not_called()


@pytest.mark.parametrize(
    "output",
    [
        "{}",
        '{"file_path":"/source","success":true}',
        '{"file_path":"/source","content_length":true}',
        '{"file_path":"/foreign","content_length":1}',
        "not-json",
    ],
)
def test_source_post_unknown_shapes_preserve_pending(
    monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    """Unknown generic output cannot be converted into closed success or failure evidence.

    Args:
        monkeypatch: Enforces no mutation after unsupported result.
        output: Missing, foreign, boolean-length or malformed native output.
    """
    transition = MagicMock()
    monkeypatch.setattr(cursor_sources, "source_transition", transition)
    event = {
        "child": True,
        "tool_name": "Read",
        "tool_input": {"file_path": "/source"},
        "tool_output": output,
    }
    with pytest.raises(core.WorkspaceError):
        cursor_sources.source_read_post(event, time.monotonic() + 1, False)
    transition.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
def test_source_post_uses_closed_native_success_or_failure(
    monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    """Settle recognized Read events separately from provisional actual content proof.

    Args:
        monkeypatch: Captures the exact closed transition.
        failed: Actual native failure versus supported native success.
    """
    transition = MagicMock(return_value={"source_receipt": False})
    monkeypatch.setattr(cursor_sources, "source_transition", transition)
    event = {
        "child": True,
        "tool_name": "Read",
        "tool_input": {"file_path": "/source"},
        "tool_output": '{"file_path":"/source","content_length":8}',
        "error_message": "denied",
        "failure_type": "permission_denied",
    }
    cursor_sources.source_read_post(event, time.monotonic() + 1, failed)
    assert transition.call_args.args[1] == ("failure" if failed else "success")
    assert transition.call_args.kwargs == (
        {"source_path": "/source"} if failed else {"source_path": "/source", "content_length": 8}
    )


@pytest.mark.parametrize("phase", ["start", "failure"])
def test_source_transition_replay_keeps_exact_request_id(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Busy and duplicate callback retries cannot create another pending or receipt event.

    Args:
        monkeypatch: Replaces bound identity and core execution boundaries.
        phase: Read admission has no source_event; completion uses the closed callback phase.
    """
    bound = {
        "schema_version": 1,
        "operation": "cursor-source-callback",
        "request_id": "fresh",
        "worktree": "/checkout",
        "host": "cursor",
        "session_id": "parent/agent/child",
        "repo_id": "repo",
        "issue_id": "TEST-1",
        "binding_generation": 1,
        "participant_id": "private-binding-extra",
    }
    monkeypatch.setattr(
        children.common,
        "request_for",
        lambda _event, operation, _host: {**bound, "operation": operation},
    )
    observed = []

    def execute(request: dict) -> dict:
        """Return genuine contention once, then complete the exact same callback.

        Args:
            request: Closed source metadata request.

        Returns:
            Bounded synthetic core outcome.
        """
        observed.append(request.copy())
        return {"ok": len(observed) > 1, "code": "OK" if len(observed) > 1 else "BUSY"}

    public = MagicMock(side_effect=execute)
    internal = MagicMock(side_effect=execute)
    monkeypatch.setattr(children.core, "execute", public)
    monkeypatch.setattr(children.core, "execute_internal", internal)
    cursor_sources.source_transition(
        {"child": True, "tool_use_id": "native-token"},
        phase,
        time.monotonic() + 1,
        **(
            {"source_locator": "context/source.md", "packet_digest": "a" * 64}
            if phase == "start"
            else {"source_path": "/source"}
        ),
    )
    assert observed[0] == observed[1]
    assert observed[0]["operation"] == (
        "source-read-start" if phase == "start" else "cursor-source-callback"
    )
    assert observed[0].get("source_event") == (None if phase == "start" else "failure")
    assert "participant_id" not in observed[0]
    assert "content" not in observed[0]
    assert public.call_count == (2 if phase == "start" else 0)
    assert internal.call_count == (0 if phase == "start" else 2)


@pytest.mark.parametrize("operation", ["read", "acknowledge", "ready", "diagnose", "update"])
def test_child_lifecycle_uses_own_binding_without_parent_assignment(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Admit only exact own lifecycle command schemas and leave mutation authority in core.

    Args:
        monkeypatch: Replaces actual binding/repository boundary.
        operation: Allowed own operation.
    """
    bound = {
        "schema_version": 1,
        "operation": operation,
        "request_id": "request",
        "worktree": "/checkout",
        "host": "cursor",
        "session_id": "parent/agent/child",
        "repo_id": "repo",
        "issue_id": "TEST-1",
        "binding_generation": 1,
    }
    request = dict(bound)
    if operation == "acknowledge":
        request["packet_digest"] = "a" * 64
    elif operation == "update":
        request.update(path="context/owned.md", old_digest=None, content="result", provenance={})
    monkeypatch.setattr(
        children.common,
        "request_for",
        lambda *_args: {**bound, "participant_id": "private", "cursor_invocation": "invocation"},
    )
    monkeypatch.setattr(
        children.core, "repository", lambda path: children.core.Repository(Path(path), None, [])
    )
    event = {
        "child": True,
        "session_id": bound["session_id"],
        "cwd": "/checkout",
        "tool_name": "Shell",
        "tool_input": {
            "command": children.common.bootstrap_command(request, "cursor"),
            "cwd": "",
            "timeout": 30000,
        },
    }
    assert child_session.child_lifecycle(event) is True
    assert child_session.child_lifecycle({"tool_name": "Read"}) is False


@pytest.mark.parametrize(
    "alteration,code",
    [
        ("session", "BINDING_CONFLICT"),
        ("host", "BINDING_CONFLICT"),
        ("generation", "STALE_BINDING"),
        ("repo", "STALE_BINDING"),
        ("worktree", "REPOSITORY_MISMATCH"),
        ("unknown", "HOST_UNSUPPORTED_TOOL"),
        ("internal", "HOST_UNSUPPORTED_TOOL"),
        ("roadmap", "NOT_OWNER"),
        ("arbitrary", "HOST_UNSUPPORTED_TOOL"),
    ],
)
def test_child_lifecycle_rejects_foreign_or_general_shell(
    monkeypatch: pytest.MonkeyPatch, alteration: str, code: str
) -> None:
    """Wrong identity/scope cannot fall through to a child's generic Shell route.

    Args:
        monkeypatch: Replaces only committed binding and repository lookup.
        alteration: Forbidden request/native command variation.
        code: Expected bounded diagnostic.
    """
    bound = {
        "schema_version": 1,
        "operation": "read",
        "request_id": "request",
        "worktree": "/checkout",
        "host": "cursor",
        "session_id": "parent/agent/child",
        "repo_id": "repo",
        "issue_id": "TEST-1",
        "binding_generation": 1,
    }
    request = dict(bound)
    if alteration == "session":
        request["session_id"] = "parent"
    elif alteration == "host":
        request["host"] = "codex"
    elif alteration == "generation":
        request["binding_generation"] = 2
    elif alteration == "repo":
        request["repo_id"] = "foreign"
    elif alteration == "worktree":
        request["worktree"] = "/foreign"
    elif alteration == "unknown":
        request["coordinator"] = "foreign"
    elif alteration == "internal":
        request["operation"] = "cursor-retire"
    elif alteration == "roadmap":
        request.update(
            operation="update", path="roadmap.md", old_digest=None, content="wrong", provenance={}
        )
    monkeypatch.setattr(children.common, "request_for", lambda *_args: bound)
    monkeypatch.setattr(
        children.core, "repository", lambda path: children.core.Repository(Path(path), None, [])
    )
    command = (
        "python -m pytest"
        if alteration == "arbitrary"
        else children.common.bootstrap_command(request, "cursor")
    )
    event = {
        "child": True,
        "session_id": bound["session_id"],
        "cwd": "/checkout",
        "tool_name": "Shell",
        "tool_input": {"command": command, "cwd": "", "timeout": 30000},
    }
    with pytest.raises(core.WorkspaceError) as error:
        child_session.child_lifecycle(event)
    assert error.value.code == code


def test_child_context_carries_only_actual_own_request_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliver an actual child command without private binding metadata or parent identity.

    Args:
        monkeypatch: Captures advisory command construction.
    """
    bound = {
        "schema_version": 1,
        "operation": "read",
        "request_id": "request",
        "worktree": "/checkout",
        "host": "cursor",
        "session_id": "actual-parent/agent/actual-child",
        "repo_id": "repo",
        "issue_id": "TEST-1",
        "binding_generation": 3,
        "issue_uuid": "private-unused",
        "participant_id": "private",
        "cursor_invocation": "private",
    }
    monkeypatch.setattr(children.common, "request_for", lambda *_args: bound)
    command = MagicMock(return_value="canonical-own-command")
    monkeypatch.setattr(children.common, "bootstrap_command", command)
    assert "canonical-own-command" in child_session.child_context({"child": True})
    request = command.call_args.args[0]
    assert request["session_id"] == "actual-parent/agent/actual-child"
    assert request["binding_generation"] == 3
    assert not ({"issue_uuid", "participant_id", "cursor_invocation"} & set(request))


def test_source_transition_exhausts_contention_without_changing_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop after eight exact callback attempts with one idempotency identity.

    Args:
        monkeypatch: Replaces binding and transaction boundaries.
    """
    bound = {
        "schema_version": 1,
        "operation": "cursor-source-callback",
        "request_id": "fresh",
        "worktree": "/checkout",
        "host": "cursor",
        "session_id": "parent/agent/child",
        "repo_id": "repo",
        "issue_id": "TEST-1",
        "binding_generation": 1,
    }
    monkeypatch.setattr(children.common, "request_for", lambda *_args: bound)
    execute = MagicMock(return_value={"ok": False, "code": "BUSY"})
    monkeypatch.setattr(children.core, "execute_internal", execute)
    with pytest.raises(core.WorkspaceError, match="BUSY"):
        cursor_sources.source_transition(
            {"child": True, "tool_use_id": "call"},
            "failure",
            time.monotonic() + 1,
            source_path="/source",
        )
    assert execute.call_count == 8
    requests = [call.args[0] for call in execute.call_args_list]
    assert all(request == requests[0] for request in requests)
    assert requests[0]["operation"] == "cursor-source-callback"
    assert requests[0]["source_event"] == "failure"


@pytest.mark.parametrize("relative", [False, True])
def test_source_read_pre_admits_only_exact_available_packet_locator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, relative: bool
) -> None:
    """Translate exact assigned paths without widening the retained locator.

    Args:
        monkeypatch: Replaces packet and mutation boundaries.
        tmp_path: Native absolute fixture root.
        relative: Whether the locator resolves inside the managed task directory.
    """
    locator = "context/source.md" if relative else str(tmp_path / "source.md")
    path = str(tmp_path / ".task/TEST-1/context/source.md") if relative else locator
    bound = {"issue_id": "TEST-1"}
    monkeypatch.setattr(children.common, "request_for", lambda *_args: bound)
    read = MagicMock(
        return_value={
            "ok": True,
            "code": "OK",
            "references": [
                {"locator": locator, "available": True},
                {"locator": "/unavailable", "available": False},
            ],
            "packet_digest": "packet",
        }
    )
    monkeypatch.setattr(children.core, "execute", read)
    monkeypatch.setattr(
        children.core, "repository", lambda _path: children.core.Repository(tmp_path, None, [])
    )
    transition = MagicMock(return_value={"ok": True, "code": "OK"})
    monkeypatch.setattr(cursor_sources, "source_transition", transition)
    event = {
        "child": True,
        "tool_name": "Read",
        "tool_input": {"file_path": path},
        "cwd": str(tmp_path),
    }
    assert cursor_sources.source_read_pre(event, time.monotonic() + 1)["ok"] is True
    assert transition.call_args.args[:2] == (event, "start")
    assert transition.call_args.kwargs == {"source_locator": locator, "packet_digest": "packet"}
    read.assert_called_once_with(bound)


@pytest.mark.parametrize(
    "alteration,code,reads",
    [
        ("parent", "HOST_UNSUPPORTED_TOOL", 0),
        ("tool", "HOST_UNSUPPORTED_TOOL", 0),
        ("model-path", "HOST_UNSUPPORTED_TOOL", 0),
        ("extra", "HOST_UNSUPPORTED_TOOL", 0),
        ("relative", "SCOPE_MISSING", 0),
        ("path-type", "SCOPE_MISSING", 0),
        ("unavailable", "SCOPE_MISSING", 1),
        ("foreign", "SCOPE_MISSING", 1),
        ("core-failure", "STALE_BINDING", 1),
    ],
)
def test_source_read_pre_denies_without_starting_unassigned_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, alteration: str, code: str, reads: int
) -> None:
    """Malformed input and unavailable/foreign sources cannot create pending Reads.

    Args:
        monkeypatch: Replaces packet and mutation boundaries.
        tmp_path: Native absolute fixture root.
        alteration: Native input or packet violation.
        code: Expected bounded rejection.
        reads: Expected current packet lookups.
    """
    event = {
        "child": True,
        "tool_name": "Read",
        "tool_input": {"file_path": str(tmp_path / "source")},
        "cwd": str(tmp_path),
    }
    if alteration == "parent":
        event["child"] = False
    elif alteration == "tool":
        event["tool_name"] = "Write"
    elif alteration == "model-path":
        event["tool_input"] = {"path": str(tmp_path / "source")}
    elif alteration == "extra":
        event["tool_input"]["limit"] = 10
    elif alteration == "relative":
        event["tool_input"]["file_path"] = "source"
    elif alteration == "path-type":
        event["tool_input"]["file_path"] = 1
    monkeypatch.setattr(children.common, "request_for", lambda *_args: {"issue_id": "TEST-1"})
    read = MagicMock(
        return_value={
            "ok": alteration != "core-failure",
            "code": "STALE_BINDING",
            "references": [
                {
                    "locator": str(tmp_path / "foreign")
                    if alteration == "foreign"
                    else str(tmp_path / "source"),
                    "available": alteration != "unavailable",
                }
            ],
            "packet_digest": "packet",
        }
    )
    monkeypatch.setattr(children.core, "execute", read)
    monkeypatch.setattr(
        children.core, "repository", lambda _path: children.core.Repository(tmp_path, None, [])
    )
    transition = MagicMock()
    monkeypatch.setattr(cursor_sources, "source_transition", transition)
    with pytest.raises(core.WorkspaceError, match=code):
        cursor_sources.source_read_pre(event, time.monotonic() + 1)
    transition.assert_not_called()
    assert read.call_count == reads


@pytest.mark.parametrize(
    "working_directory,allowed", [("/checkout", True), ("/foreign", False), (1, False)]
)
def test_child_lifecycle_requires_matching_native_working_directory(
    monkeypatch: pytest.MonkeyPatch, working_directory: object, allowed: bool
) -> None:
    """A valid own request cannot redirect native Shell execution elsewhere.

    Args:
        monkeypatch: Replaces binding and repository lookup.
        working_directory: Native Shell working directory.
        allowed: Whether the same checkout is retained.
    """
    bound = {
        "schema_version": 1,
        "operation": "read",
        "request_id": "request",
        "worktree": "/checkout",
        "host": "cursor",
        "session_id": "parent/agent/child",
        "repo_id": "repo",
        "issue_id": "TEST-1",
        "binding_generation": 1,
    }
    monkeypatch.setattr(children.common, "request_for", lambda *_args: bound)
    monkeypatch.setattr(
        children.core, "repository", lambda path: children.core.Repository(Path(path), None, [])
    )
    event = children.common.carry_repository(
        {
            "child": True,
            "session_id": bound["session_id"],
            "cwd": "/checkout",
            "tool_name": "Shell",
            "tool_input": {
                "command": children.common.bootstrap_command(bound, "cursor"),
                "working_directory": working_directory,
            },
        }
    )
    if allowed:
        assert child_session.child_lifecycle(event) is True
    else:
        with pytest.raises(core.WorkspaceError, match="REPOSITORY_MISMATCH"):
            child_session.child_lifecycle(event)
