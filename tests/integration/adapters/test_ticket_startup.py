"""Exercise ticket-first startup through the Codex adapter in disposable repositories."""

import json
import subprocess
from pathlib import Path

import pytest

from nplet.adapters import codex, common, startup
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.integration


def repository(tmp_path: Path) -> Path:
    """Create an unregistered Git worktree for startup tests.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.

    Returns:
        The committed but unregistered repository root.
    """
    root = tmp_path / "main"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=x@y.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return root


def event(root: Path, name: str, **fields: object) -> dict[str, object]:
    """Build a hook event with its actual test session identity.

    Args:
        root: Disposable repository root used by this case.
        name: Event or case name selected for this test.
        fields: Event or request fields varied by this case.

    Returns:
        The native hook event envelope for this case.
    """
    return {"cwd": str(root), "session_id": "master", "hook_event_name": name, **fields}


def ticket_response() -> dict[str, object]:
    """Model the verified foreground Linear connector result shape.

    Returns:
        A successful Codex connector result fixture.
    """
    return {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "id": "TEST-1",
                        "uuid": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977",
                        "title": "Test issue",
                        "description": "A ticket description.",
                    }
                ),
            }
        ],
    }


def linear_error(
    message: str = "Could not find referenced Issue.",
    status: int = 400,
    error_code: str = "INVALID_ARGUMENT",
) -> dict[str, object]:
    """Model the observed Linear connector error with narrow variable fields.

    Args:
        message: Provider or adapter diagnostic text varied by this case.
        status: HTTP or provider status selected for this case.
        error_code: Typed provider failure selected for this case.

    Returns:
        A typed connector error result fixture.
    """
    return {
        "isError": True,
        "structuredContent": {"error_code": error_code},
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "error": "invalid_request",
                        "message": message,
                        "status": status,
                        "requestId": "a4793bad98353233",
                    }
                ),
            }
        ],
    }


def test_ticket_read_precedes_workspace_and_automatically_establishes_readiness(
    tmp_path: Path,
) -> None:
    """A Task line must permit the exact issue read before any local readiness gate.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert not (root / ".task" / "TEST-1").exists()
    call = event(
        root,
        "PreToolUse",
        tool_name="mcp__codex_apps__linear_get_issue",
        tool_input={"id": "TEST-1"},
        tool_use_id="lookup-1",
    )
    assert codex.handle(call) == {}
    assert not (root / ".task" / "TEST-1").exists()
    result = codex.handle(
        event(
            root,
            "PostToolUse",
            tool_name="mcp__codex_apps__linear_get_issue",
            tool_input={"id": "TEST-1"},
            tool_use_id="lookup-1",
            tool_response=ticket_response(),
        )
    )
    assert "TASK_WORKSPACE_NOT_READY" not in str(result)
    assert (root / ".task" / "TEST-1" / "roadmap.md").exists()


@pytest.mark.parametrize(
    "tool_name", ["mcp__codex_apps__linear__get_issue", "mcp__codex_apps__linear_get_issue"]
)
def test_native_ticket_name_preserves_exact_startup_correlation(
    tmp_path: Path, tool_name: str
) -> None:
    """Admit supported names without weakening issue, argument, or callback identity.

    Args:
        tmp_path: Disposable repository and persistent binding store.
        tool_name: Captured native spelling or deliberately retained legacy spelling.
    """
    # Record a real task assignment before any registration or provider operation.
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    fields = {"tool_name": tool_name, "tool_input": {"id": "TEST-1"}, "tool_use_id": "native-1"}
    # Unrelated research reads must not reserve the selected-ticket lookup.
    for arguments in ({"id": "OTHER-2"}, {"id": "TEST-1", "includeRelations": True}):
        rejected = codex.handle(event(root, "PreToolUse", **{**fields, "tool_input": arguments}))
        assert rejected == {}
        assert not list((root / ".task/.bindings").glob("*.lookup.json"))
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    # A foreign completion must leave the actual reserved lookup and task gate intact.
    marker = next((root / ".task/.bindings").glob("*.lookup.json"))
    reserved = marker.read_bytes()
    rejected = codex.handle(
        event(
            root,
            "PostToolUse",
            tool_response=ticket_response(),
            **{**fields, "tool_use_id": "foreign"},
        )
    )
    assert rejected == {}
    assert marker.read_bytes() == reserved
    assert not (root / ".task" / "TEST-1").exists()
    # Only the correlated provider result establishes readiness and creates task sources.
    completed = codex.handle(event(root, "PostToolUse", tool_response=ticket_response(), **fields))
    assert "TASK_WORKSPACE_READY" in completed["systemMessage"]
    assert (root / ".task" / "TEST-1" / "roadmap.md").exists()


@pytest.mark.parametrize("flag", ["absent", True, None, "false", 0])
def test_native_ticket_completion_handles_optional_error_flag(tmp_path: Path, flag: object) -> None:
    """Only an omitted native error flag can use successful issue-payload validation.

    Args:
        tmp_path: Disposable repository and persistent binding store.
        flag: Omitted, explicit error, or malformed native MCP error indicator.
    """
    # Reserve the exact captured native connector call against the assignment.
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    fields = {
        "tool_name": "mcp__codex_apps__linear__get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "native-1",
    }
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    # Vary only the optional transport flag, keeping the real issue payload fixed.
    response = ticket_response()
    response.pop("isError")
    if flag != "absent":
        response["isError"] = flag
    result = codex.handle(event(root, "PostToolUse", tool_response=response, **fields))
    # Explicit errors and invalid flags cannot create an issue workspace.
    if flag == "absent":
        assert "TASK_WORKSPACE_READY" in result["systemMessage"]
    else:
        assert "TASK_WORKSPACE_NOT_READY" in result["systemMessage"]
        assert not (root / ".task" / "TEST-1").exists()


@pytest.mark.parametrize(
    "content",
    [
        [],
        [{"type": "text", "text": "invalid JSON"}],
        [{"type": "text", "text": '{"id":"OTHER-2"}'}],
        [{"type": "image", "text": "{}"}],
        [{"type": "text", "text": "{}"}, {"type": "text", "text": "{}"}],
    ],
)
def test_native_missing_error_flag_does_not_validate_bad_issue_payload(
    tmp_path: Path, content: object
) -> None:
    """Transport normalization cannot make malformed or foreign issue content ready.

    Args:
        tmp_path: Disposable repository and persistent binding store.
        content: Malformed or mismatched provider content blocks.
    """
    # Reserve the observed native issue tool against a real assignment marker.
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    fields = {
        "tool_name": "mcp__codex_apps__linear__get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "native-1",
    }
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    # Reject the content without creating issue state or consuming the ticket requirement.
    result = codex.handle(event(root, "PostToolUse", tool_response={"content": content}, **fields))
    assert "TASK_WORKSPACE_NOT_READY" in result["systemMessage"]
    assert not (root / ".task" / "TEST-1").exists()
    assert "TICKET_READ_REQUIRED" in str(
        codex.handle(
            event(
                root,
                "PreToolUse",
                tool_name="Write",
                tool_input={"file_path": str(root / "src/example.py"), "content": "pass\n"},
                tool_use_id="protected",
            )
        )
    )


def test_pending_ticket_allows_only_scoped_read_only_diagnosis(tmp_path: Path) -> None:
    """Diagnostic admission leaves the ticket gate intact and rejects mutations.

    Args:
        tmp_path: Disposable repository and persistent binding store.
    """
    # Establish the pending ticket marker and snapshot its durable assignment bytes.
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    bindings = root / ".task" / ".bindings"
    before = {path.name: path.read_bytes() for path in bindings.glob("*.json")}
    request = {
        "schema_version": 1,
        "operation": "diagnose",
        "request_id": "diagnose-startup",
        "worktree": str(root),
        "host": "codex",
        "session_id": "master",
    }
    # Admit the exact read-only pre-registration diagnostic shell command.
    command = common.bootstrap_command(request, "codex")
    diagnostic = event(
        root,
        "PreToolUse",
        tool_name="Bash",
        tool_input={"command": command},
        tool_use_id="diagnostic-1",
    )
    assert codex.handle(diagnostic) == {}
    assert core.execute(request) == {"ok": True, "code": "REGISTRATION_REQUIRED"}
    assert codex.handle({**diagnostic, "hook_event_name": "PostToolUse"}) == {}
    # Reject mutations, foreign identities, extra fields, and appended shell commands.
    for changed in (
        {"operation": "register"},
        {"session_id": "other"},
        {"issue_id": "OTHER-2", "repo_id": "fake"},
        {"tool_id": "extra"},
    ):
        bad_command = common.bootstrap_command({**request, **changed}, "codex")
        rejected = codex.handle({**diagnostic, "tool_input": {"command": bad_command}})
        assert "TICKET_READ_REQUIRED" in str(rejected)
    rejected = codex.handle({**diagnostic, "tool_input": {"command": command + " && pwd"}})
    assert "TICKET_READ_REQUIRED" in str(rejected)
    # Admission performs no bootstrap mutations or marker consumption.
    assert {path.name: path.read_bytes() for path in bindings.glob("*.json")} == before
    assert not (root / ".task" / "TEST-1").exists()
    assert "TICKET_READ_REQUIRED" in str(
        codex.handle(
            event(
                root,
                "PreToolUse",
                tool_name="Write",
                tool_input={"file_path": str(root / "src/example.py"), "content": "pass\n"},
                tool_use_id="protected",
            )
        )
    )


def lookup(root: Path, session: str, response: object) -> dict[str, object]:
    """Run one exact direct lookup and matching completion callback.

    Args:
        root: Disposable repository root used by this case.
        session: Session identity selected for this case.
        response: Provider or hook response supplied by this case.

    Returns:
        The ticket lookup hook result.
    """
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "lookup-1",
    }
    assert codex.handle(event(root, "PreToolUse", session_id=session, **fields)) == {}
    return codex.handle(
        event(
            root,
            "PostToolUse",
            session_id=session,
            tool_response=response,
            **fields,
        )
    )


@pytest.mark.parametrize(
    ("response", "code"),
    [
        ({"isError": True, "code": "NOT_FOUND"}, "ISSUE_NOT_FOUND for TEST-1"),
        ({"isError": True, "code": "UNAUTHORIZED"}, "PROVIDER_AUTH_REQUIRED"),
        ({"isError": True, "code": "FORBIDDEN"}, "PROVIDER_PERMISSION_DENIED"),
        ({"isError": True, "code": "NETWORK_ERROR"}, "PROVIDER_NETWORK_ERROR"),
        (linear_error(), "Linear could not find requested issue TEST-1."),
        (linear_error(message="Invalid query argument."), "PROVIDER_ERROR"),
        (linear_error(status=401), "PROVIDER_ERROR"),
        (linear_error(error_code="AUTH_REQUIRED"), "PROVIDER_ERROR"),
        ({"isError": True, "content": [{"type": "text", "text": "not found"}]}, "PROVIDER_ERROR"),
        ({"isError": False, "content": []}, "PROVIDER_RESPONSE_INVALID"),
    ],
)
def test_provider_failures_create_no_workspace(
    tmp_path: Path,
    response: object,
    code: str,
) -> None:
    """Recognize only verified missing-reference shapes; preserve other failures.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
        response: Provider or hook response supplied by this case.
        code: Diagnostic code selected for this case.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    result = lookup(root, "master", response)
    assert code in result["systemMessage"]
    assert not (root / ".task" / "TEST-1").exists()


def test_repeat_start_preserves_roadmap_and_coordinator(tmp_path: Path) -> None:
    """The same session can resume without replacing approved task content.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]
    roadmap = (root / ".task" / "TEST-1" / "roadmap.md").read_bytes()
    key = core.participant_key({"host": "codex", "session_id": "master"})
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]
    assert (root / ".task" / "TEST-1" / "roadmap.md").read_bytes() == roadmap
    base = {
        "schema_version": 1,
        "worktree": str(root),
        "host": "codex",
        "session_id": "master",
        "issue_id": "TEST-1",
        "repo_id": json.loads((root / ".task" / ".repository.json").read_text())["repo_id"],
    }
    # Inspect committed state after ticket-driven coordinator setup.
    with core.Store(base) as store, store.issues.child("TEST-1") as control, control.lock():
        issue = core.Issue(store, control, "TEST-1")
        issue.recover()
        assert issue.committed_state()["coordinator"] == key


def test_other_session_joins_as_reader_without_taking_coordinator(tmp_path: Path) -> None:
    """A new session becomes ready with roadmap scope and no write ownership.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    master = common.request_for(event(root, "SessionStart"), "update", "codex")
    roadmap_path = root / ".task" / "TEST-1" / "roadmap.md"
    updated = core.execute(
        {
            **master,
            "operation": "update",
            "expected_revision": core.execute({**master, "operation": "diagnose"})["revision"],
            "path": "roadmap.md",
            "old_digest": core.sha(roadmap_path.read_bytes()),
            "content": "# Approved plan\n",
            "provenance": {
                "sources": [
                    {
                        "id": "approval",
                        "locator": "fixture:approval",
                        "sha256": core.sha(b"approved"),
                    }
                ],
                "applicability": "issue",
                "status": "approved",
            },
        }
    )
    assert updated["ok"], updated
    roadmap = (root / ".task" / "TEST-1" / "roadmap.md").read_bytes()
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    result = lookup(root, "other", ticket_response())
    assert "TASK_WORKSPACE_READY" in result["systemMessage"]
    key = core.participant_key({"host": "codex", "session_id": "master"})
    assert key in result["systemMessage"]
    assert "explicit coordinator transfer" in result["systemMessage"]
    base = {
        "schema_version": 1,
        "worktree": str(root),
        "host": "codex",
        "session_id": "master",
        "issue_id": "TEST-1",
        "repo_id": json.loads((root / ".task" / ".repository.json").read_text())["repo_id"],
    }
    # Inspect committed state after a repeat setup attempt.
    with core.Store(base) as store, store.issues.child("TEST-1") as control, control.lock():
        issue = core.Issue(store, control, "TEST-1")
        issue.recover()
        state = issue.committed_state()
        assert state["coordinator"] == key
        assert state["owners"] == {"roadmap.md": key}
        other_key = core.participant_key({"host": "codex", "session_id": "other"})
        assert [ref["locator"] for ref in state["participants"][other_key]["packet"]] == [
            "roadmap.md"
        ]
    assert (root / ".task" / "TEST-1" / "roadmap.md").read_bytes() == roadmap
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "other", ticket_response())["systemMessage"]


@pytest.mark.parametrize("reader_host", ["codex", "claude-code", "cursor", "future-host"])
def test_verified_reader_join_is_runtime_neutral_and_retains_scope(
    tmp_path: Path, reader_host: str
) -> None:
    """Join any explicit host as a reader under the original coordinator.

    Args:
        tmp_path: Disposable directory for the shared lifecycle repository.
        reader_host: Explicit host identifier for the joining session.
    """
    root = repository(tmp_path)
    issue = startup.ticket(ticket_response(), "TEST-1")
    coordinator = startup.start(event(root, "", session_id="master"), issue, host="codex")
    coordinator_key = core.participant_key({"host": "codex", "session_id": "master"})
    reader_event = event(root, "", session_id="reader")

    # A verified ticket joins the same issue without assuming a Codex host.
    ready = startup.start(reader_event, issue, host=reader_host)
    reader_key = core.participant_key({"host": reader_host, "session_id": "reader"})
    assert ready["participant_id"] == reader_key
    assert ready["coordinator"] == coordinator_key
    assert coordinator["participant_id"] == coordinator_key
    base = {
        "schema_version": 1,
        "worktree": str(root),
        "host": reader_host,
        "session_id": "reader",
        "issue_id": "TEST-1",
        "repo_id": json.loads((root / ".task" / ".repository.json").read_text())["repo_id"],
    }
    # Committed ownership and the reader packet remain constrained to roadmap access.
    with core.Store(base) as store, store.issues.child("TEST-1") as control, control.lock():
        committed = core.Issue(store, control, "TEST-1")
        committed.recover()
        state = committed.committed_state()
        assert state["coordinator"] == coordinator_key
        assert state["owners"] == {"roadmap.md": coordinator_key}
        assert [ref["locator"] for ref in state["participants"][reader_key]["packet"]] == [
            "roadmap.md"
        ]

    # A different immutable issue UUID cannot reuse this reader binding.
    with pytest.raises(core.WorkspaceError, match="ISSUE_MISMATCH"):
        startup.start(
            reader_event,
            {**issue, "uuid": "d7a4c098-4b6d-402e-bfd1-326af3959e84"},
            host=reader_host,
        )
    # A caller-supplied packet cannot widen the shared join operation.
    rogue = {
        **base,
        "request_id": "rogue-reader-join",
        "operation": "join",
        "issue_uuid": issue["uuid"],
        "packet": [{"locator": "context/private.md"}],
    }
    assert core.execute(rogue)["code"] == "INVALID_REQUEST"


def test_join_rejects_wrong_uuid_and_caller_scope(tmp_path: Path) -> None:
    """The reader join cannot choose arbitrary notes or bypass issue identity.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    wrong = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "id": "TEST-1",
                        "uuid": "d7a4c098-4b6d-402e-bfd1-326af3959e84",
                    }
                ),
            }
        ],
    }
    assert "ISSUE_MISMATCH" in lookup(root, "other", wrong)["systemMessage"]
    base = common.request_for(event(root, "SessionStart"), "read", "codex")
    rogue = {
        "schema_version": 1,
        "request_id": "rogue-join",
        "operation": "join",
        "worktree": str(root),
        "host": "codex",
        "session_id": "other",
        "repo_id": base["repo_id"],
        "issue_id": "TEST-1",
        "issue_uuid": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977",
        "packet": [{"locator": "context/private.md"}],
    }
    assert core.execute(rogue)["code"] == "INVALID_REQUEST"
    shell_join = {key: value for key, value in rogue.items() if key != "packet"}
    assert not common.canonical_bootstrap(
        event(root, "PreToolUse"),
        common.bootstrap_command(shell_join, "codex"),
        "codex",
        ready=False,
    )


def test_resume_refreshes_only_committed_coordinator_roadmap(tmp_path: Path) -> None:
    """A normal roadmap update can resume; an external edit remains a conflict.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    request = common.request_for(event(root, "SessionStart"), "update", "codex")
    roadmap = root / ".task" / "TEST-1" / "roadmap.md"
    diagnosis = core.execute({**request, "operation": "diagnose"})
    result = core.execute(
        {
            **request,
            "operation": "update",
            "expected_revision": diagnosis["revision"],
            "path": "roadmap.md",
            "old_digest": core.sha(roadmap.read_bytes()),
            "content": "# Approved roadmap\n",
            "provenance": {
                "sources": [
                    {
                        "id": "approval",
                        "locator": "fixture:roadmap-approval",
                        "sha256": core.sha(b"fixture approval"),
                    }
                ],
                "applicability": "issue",
                "status": "approved",
            },
        }
    )
    assert result["ok"], result
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]
    assert roadmap.read_text() == "# Approved roadmap\n"
    roadmap.write_text("external mutation")
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "UNTRACKED_CHANGE" in lookup(root, "master", ticket_response())["systemMessage"]


def test_ordinary_issue_read_after_start_is_not_a_new_bootstrap(tmp_path: Path) -> None:
    """A later issue read follows ordinary tracked-tool admission and completion.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "OTHER-2"},
        "tool_use_id": "ordinary-read",
    }
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    assert (
        codex.handle(event(root, "PostToolUse", tool_response={"isError": False}, **fields)) == {}
    )


def test_completion_retry_after_partial_setup_preserves_correlation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed setup completion can retry with the same observed tool ID.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    original = startup._call
    calls = 0

    def fail_once(request: dict[str, object]) -> dict[str, object]:
        """Fail one scope request, then forward later lifecycle operations.

        Args:
            request: Startup lifecycle request under test.

        Returns:
            The real lifecycle result after the injected failure.

        Raises:
            core.WorkspaceError: For the first scope attempt.
        """
        nonlocal calls
        # Inject one local setup failure after the verified provider read.
        if request["operation"] == "scope" and calls == 0:
            calls += 1
            raise core.WorkspaceError("BUSY")
        return original(request)

    monkeypatch.setattr(startup, "_call", fail_once)
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "lookup-1",
    }
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    completed = event(root, "PostToolUse", tool_response=ticket_response(), **fields)
    assert "BUSY" in codex.handle(completed)["systemMessage"]
    assert (root / ".task" / "TEST-1" / "roadmap.md").exists()
    assert "TASK_WORKSPACE_READY" in codex.handle(completed)["systemMessage"]


def test_new_lookup_after_partial_setup_failure_preserves_ticket_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed ticket read can be retried with a new tool ID after setup fails.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    original = startup._call
    failed_once = False

    def fail_once(request: dict[str, object]) -> dict[str, object]:
        """Fail one scope request, then permit a fresh ticket-read retry.

        Args:
            request: Startup lifecycle request under test.

        Returns:
            The real lifecycle result after the injected failure.

        Raises:
            core.WorkspaceError: For the first scope attempt.
        """
        nonlocal failed_once
        # Inject one local setup failure after the verified provider read.
        if request["operation"] == "scope" and not failed_once:
            failed_once = True
            raise core.WorkspaceError("BUSY")
        return original(request)

    monkeypatch.setattr(startup, "_call", fail_once)
    first = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "lookup-1",
    }
    assert codex.handle(event(root, "PreToolUse", **first)) == {}
    competing = {**first, "tool_use_id": "lookup-2"}
    assert "BINDING_CONFLICT" in str(codex.handle(event(root, "PreToolUse", **competing)))
    assert (
        "BUSY"
        in codex.handle(event(root, "PostToolUse", tool_response=ticket_response(), **first))[
            "systemMessage"
        ]
    )
    wrong_issue = {**first, "tool_input": {"id": "OTHER-2"}, "tool_use_id": "other-issue"}
    assert codex.handle(event(root, "PreToolUse", **wrong_issue)) == {}
    # Another session may research but must not inherit the task binding.
    assert codex.handle(event(root, "PreToolUse", session_id="other", **first)) == {}
    with pytest.raises(core.WorkspaceError, match="BINDING_MISSING"):
        common.request_for(event(root, "SessionStart", session_id="other"), "ready", "codex")

    blocked = codex.handle(
        event(
            root,
            "PreToolUse",
            tool_name="Write",
            tool_input={"file_path": str(root / "src/example.py"), "content": "pass\n"},
            tool_use_id="ordinary",
        )
    )
    assert "TICKET_READ_REQUIRED" in str(blocked)
    second = {**first, "tool_use_id": "lookup-2"}
    assert codex.handle(event(root, "PreToolUse", **second)) == {}
    completed = codex.handle(event(root, "PostToolUse", tool_response=ticket_response(), **second))
    assert "TASK_WORKSPACE_READY" in completed["systemMessage"]


def test_linked_checkout_supplies_governing_sources(tmp_path: Path) -> None:
    """Task bytes come from main; governing files come from the selected checkout.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    main = repository(tmp_path)
    other = tmp_path / "other"
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-q", "-b", "other", str(other)], check=True
    )
    # Governing bytes must satisfy the same owned, no-follow file policy as startup.
    with core.Directory.absolute(other) as selected:
        selected.write("AGENTS.md", b"# Selected checkout rules\n")
        assert selected.read("AGENTS.md") == b"# Selected checkout rules\n"
    codex.handle(event(other, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(other, "master", ticket_response())["systemMessage"]
    request = common.request_for(event(other, "SessionStart"), "read", "codex")
    refs = core.execute(request)["references"]
    assert any(ref["locator"] == str(other / "AGENTS.md") for ref in refs)
    assert (main / ".task" / "TEST-1" / "roadmap.md").exists()


def test_optional_missing_packet_source_does_not_block_resume(tmp_path: Path) -> None:
    """Read the available references and leave an optional absent source absent.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    base = common.request_for(event(root, "SessionStart"), "read", "codex")
    read = core.execute(base)
    packet = [
        {key: value for key, value in ref.items() if key != "available"}
        for ref in read["references"]
    ]
    packet.append(
        {
            "id": "optional",
            "locator": "context/missing.md",
            "sha256": "0" * 64,
            "required": False,
            "authority": "task-workspace",
            "reason": "optional-context",
            "stage": "planning",
            "reader": core.participant_key(base),
        }
    )
    scoped = core.execute(
        {
            **base,
            "operation": "scope",
            "expected_revision": read["revision"],
            "target_participant": core.participant_key(base),
            "packet": packet,
        }
    )
    assert scoped["ok"], scoped
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]


def test_lookup_requires_exact_ticket_and_tool_correlation(tmp_path: Path) -> None:
    """A different identifier or completion tool ID cannot establish readiness.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    wrong = codex.handle(
        event(
            root,
            "PreToolUse",
            tool_name="mcp__codex_apps__linear_get_issue",
            tool_input={"id": "OTHER-2"},
            tool_use_id="lookup-1",
        )
    )
    assert wrong == {}
    assert not list((root / ".task/.bindings").glob("*.lookup.json"))
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "lookup-1",
    }
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    marker = next((root / ".task/.bindings").glob("*.lookup.json"))
    reserved = marker.read_bytes()
    mismatched = codex.handle(
        event(
            root,
            "PostToolUse",
            tool_response=ticket_response(),
            **{**fields, "tool_use_id": "lookup-2"},
        )
    )
    assert mismatched == {}
    assert marker.read_bytes() == reserved
    assert not (root / ".task" / "TEST-1").exists()


def test_reader_resume_refreshes_only_core_roadmap_packet(tmp_path: Path) -> None:
    """A joined reader stays ready after the coordinator's committed update.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    lookup(root, "other", ticket_response())
    request = common.request_for(event(root, "SessionStart"), "update", "codex")
    roadmap = root / ".task" / "TEST-1" / "roadmap.md"
    updated = core.execute(
        {
            **request,
            "operation": "update",
            "expected_revision": core.execute({**request, "operation": "diagnose"})["revision"],
            "path": "roadmap.md",
            "old_digest": core.sha(roadmap.read_bytes()),
            "content": "# New approved revision\n",
            "provenance": {
                "sources": [
                    {
                        "id": "approval",
                        "locator": "fixture:approval",
                        "sha256": core.sha(b"approved"),
                    }
                ],
                "applicability": "issue",
                "status": "approved",
            },
        }
    )
    assert updated["ok"], updated
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "other", ticket_response())["systemMessage"]


def test_malformed_completed_result_allows_new_lookup_id(tmp_path: Path) -> None:
    """A completed malformed provider result does not strand lookup correlation.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    first = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "first",
    }
    assert codex.handle(event(root, "PreToolUse", **first)) == {}
    failed = codex.handle(event(root, "PostToolUse", tool_response="not JSON", **first))
    assert "PROVIDER_RESPONSE_INVALID" in failed["systemMessage"]
    second = {**first, "tool_use_id": "second"}
    assert codex.handle(event(root, "PreToolUse", **second)) == {}
    completed = codex.handle(event(root, "PostToolUse", tool_response=ticket_response(), **second))
    assert "TASK_WORKSPACE_READY" in completed["systemMessage"]


def test_new_task_line_fences_previous_ready_session_until_ticket_read(tmp_path: Path) -> None:
    """A new Task fences implementation while preserving supported research.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    # Protected file and shell writes cannot use the old ready binding.
    for tool_name, tool_input in (
        ("Write", {"file_path": str(root / "AGENTS.md"), "content": "changed"}),
        ("exec_command", {"cmd": "touch src/example.py", "login": False, "shell": "/bin/sh"}),
    ):
        denied = codex.handle(
            event(
                root,
                "PreToolUse",
                tool_name=tool_name,
                tool_input=tool_input,
                tool_use_id="blocked-" + tool_name,
            )
        )
        assert "TICKET_READ_REQUIRED" in str(denied)
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]


SECOND_UUID = "5d3c1f0e-8b2a-4c6d-9e7f-0a1b2c3d4e5f"


def second_response() -> dict[str, object]:
    """Model the verified connector result for a second issue, TEST-2.

    Returns:
        A successful Codex connector result fixture for TEST-2.
    """
    issue = {"id": "TEST-2", "uuid": SECOND_UUID, "title": "Second issue"}
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(issue)}]}


def lookup_issue(root: Path, identifier: str, tool_id: str, response: object) -> dict[str, object]:
    """Run one exact master-session lookup for any issue under a fresh native call ID.

    Args:
        root: Disposable repository root used by this case.
        identifier: Issue the session's current Task line names.
        tool_id: Native tool call ID for this lookup.
        response: Provider result supplied to the completion callback.

    Returns:
        The ticket lookup hook result.
    """
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": identifier},
        "tool_use_id": tool_id,
    }
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    return codex.handle(event(root, "PostToolUse", tool_response=response, **fields))


def issue_state(root: Path, identifier: str) -> dict[str, object]:
    """Read one issue's committed control state after recovery.

    Args:
        root: Disposable repository root used by this case.
        identifier: Issue whose state is read.

    Returns:
        The committed issue state.
    """
    base = {
        "schema_version": 1,
        "worktree": str(root),
        "host": "codex",
        "session_id": "master",
        "issue_id": identifier,
        "repo_id": json.loads((root / ".task" / ".repository.json").read_text())["repo_id"],
    }
    # Recover and read the committed state under the issue lock.
    with core.Store(base) as store, store.issues.child(identifier) as control, control.lock():
        issue = core.Issue(store, control, identifier)
        issue.recover()
        return issue.committed_state()


def bound_issue(root: Path) -> str:
    """Return the issue the master session's persisted binding names.

    Args:
        root: Disposable repository root used by this case.

    Returns:
        The bound issue ID.
    """
    key = core.participant_key({"host": "codex", "session_id": "master"})
    return json.loads((root / ".task" / ".bindings" / (key + ".json")).read_text())["issue_id"]


def test_bound_session_task_line_for_new_issue_rebinds_after_verified_read(
    tmp_path: Path,
) -> None:
    """A bound session starts a second issue; the first stays preserved and fenced.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    # Start TEST-1 and remember its approved bytes.
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]
    roadmap = (root / ".task" / "TEST-1" / "roadmap.md").read_bytes()
    key = core.participant_key({"host": "codex", "session_id": "master"})
    # A Task line for TEST-2 is admitted and fences tools until its exact read.
    prompt = codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-2"))
    assert "decision" not in prompt
    assert bound_issue(root) == "TEST-1"
    assert not (root / ".task" / "TEST-2").exists()
    # The verified TEST-2 read rebinds the session into a new issue it coordinates.
    result = lookup_issue(root, "TEST-2", "lookup-2", second_response())
    assert "TASK_WORKSPACE_READY" in result["systemMessage"]
    assert bound_issue(root) == "TEST-2"
    second = issue_state(root, "TEST-2")
    assert second["coordinator"] == key and second["issue_uuid"] == SECOND_UUID
    assert second["participants"][key]["status"] == "ready"
    # TEST-1 keeps its payload and coordinator; only this participant is fenced.
    first = issue_state(root, "TEST-1")
    assert first["coordinator"] == key
    assert first["participants"][key]["status"] == "detached"
    assert (root / ".task" / "TEST-1" / "roadmap.md").read_bytes() == roadmap
    # A later Task line returns the session to TEST-1 through the same route.
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    back = lookup_issue(root, "TEST-1", "lookup-3", ticket_response())
    assert "TASK_WORKSPACE_READY" in back["systemMessage"]
    assert bound_issue(root) == "TEST-1"
    assert issue_state(root, "TEST-2")["participants"][key]["status"] == "detached"


def test_failed_switch_read_keeps_old_binding_and_allows_return(tmp_path: Path) -> None:
    """A failed provider read creates nothing and leaves a route back to the bound issue.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    # Start TEST-1, then request TEST-2 and fail its provider read.
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-2"))
    failed = lookup_issue(root, "TEST-2", "lookup-2", {"isError": True, "code": "NETWORK_ERROR"})
    assert "PROVIDER_NETWORK_ERROR" in failed["systemMessage"]
    # The old binding and participant are untouched and TEST-2 has no workspace.
    key = core.participant_key({"host": "codex", "session_id": "master"})
    assert bound_issue(root) == "TEST-1"
    assert issue_state(root, "TEST-1")["participants"][key]["status"] == "ready"
    assert not (root / ".task" / "TEST-2").exists()
    # Returning to TEST-1 resumes it without any rebind.
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    back = lookup_issue(root, "TEST-1", "lookup-3", ticket_response())
    assert "TASK_WORKSPACE_READY" in back["systemMessage"]
    assert bound_issue(root) == "TEST-1"


def test_refused_switch_rebind_keeps_old_binding_and_allows_return(tmp_path: Path) -> None:
    """An existing target without an assignment refuses rebind and strands nothing.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    # Another session owns TEST-2 and has not assigned the master session.
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", session_id="owner", prompt="Task: TEST-2"))
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-2"},
        "tool_use_id": "owner-lookup",
    }
    codex.handle(event(root, "PreToolUse", session_id="owner", **fields))
    codex.handle(
        event(root, "PostToolUse", session_id="owner", tool_response=second_response(), **fields)
    )
    # The bound master asks for TEST-2; the verified read cannot rebind it.
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-2"))
    refused = lookup_issue(root, "TEST-2", "lookup-2", second_response())
    assert "SCOPE_MISSING" in refused["systemMessage"]
    key = core.participant_key({"host": "codex", "session_id": "master"})
    assert bound_issue(root) == "TEST-1"
    assert issue_state(root, "TEST-1")["participants"][key]["status"] == "ready"
    assert key not in issue_state(root, "TEST-2")["participants"]
    # The completed TEST-2 correlation does not block a return to TEST-1.
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    back = lookup_issue(root, "TEST-1", "lookup-3", ticket_response())
    assert "TASK_WORKSPACE_READY" in back["systemMessage"]
