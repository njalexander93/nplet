"""Check explicit task selection and bound request construction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nplet.adapters import codex, common
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit

ROOT = Path(Path.cwd().anchor) / "root"
BINDINGS = str(ROOT / ".task" / ".bindings")


class Directory:
    """Hold only explicit assignment documents for prompt decision tests."""

    def __init__(self, path: str, data: dict[str, dict[str, object]]) -> None:
        """Share the in-memory binding directory across opened handles.

        Args:
            path: Disposable file path used by this case.
            data: Fixture data supplied for this case.
        """
        self.path = path
        self.data = data

    def __enter__(self) -> Directory:
        """Open this modeled directory handle.

        Returns:
            The active disposable context manager fixture.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave this modeled directory handle.

        Args:
            _args: Ignored positional arguments accepted by this test callback.
        """

    def child(self, name: str, _create: bool = False) -> Directory:
        """Open one direct modeled child.

        Args:
            name: Event or case name selected for this test.
            _create: Ignored creation request supplied by this test seam.

        Returns:
            The requested child directory fixture.
        """
        return Directory(str(Path(self.path) / name), self.data)

    def lock(self, _name: str) -> Directory:
        """Hold a modeled assignment lock.

        Args:
            _name: Ignored event name accepted by this test callback.

        Returns:
            The fixture lock context manager.
        """
        return self

    def exists(self, name: str) -> bool:
        """Check a direct binding entry.

        Args:
            name: Event or case name selected for this test.

        Returns:
            Whether the requested fixture entry exists.
        """
        return name in self.data.get(self.path, {})

    def json(self, name: str) -> Any:
        """Read an existing binding entry.

        Args:
            name: Event or case name selected for this test.

        Returns:
            The stored JSON fixture value.
        """
        return self.data[self.path][name]

    def put(self, name: str, value: object) -> None:
        """Record an explicit task assignment.

        Args:
            name: Event or case name selected for this test.
            value: Boundary input selected for this case.
        """
        self.data.setdefault(self.path, {})[name] = value

    def unlink(self, name: str) -> None:
        """Remove a settled lookup marker while retaining assignment.

        Args:
            name: Event or case name selected for this test.
        """
        del self.data[self.path][name]


def setup_prompt(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, object]]:
    """Install only the repository and assignment-store boundaries.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.

    Returns:
        Recorded Task assignment and binding fixtures.
    """
    data: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(common.core, "repository", lambda _cwd: (ROOT, None, []))
    monkeypatch.setattr(common.core.Directory, "absolute", lambda path: Directory(str(path), data))
    return data


def test_prompt_ignores_ordinary_text_without_repository_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create no assignment for a prompt with no explicit Task line.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common.core, "repository", lambda _cwd: pytest.fail("repository read"))
    event = {"cwd": str(ROOT), "session_id": "session", "prompt": "Please explain the code"}
    assert common.prompt(event, "codex") == {}


@pytest.mark.parametrize("prompt", ["Task: bad", "Task: TEST-30\nTask: TEST-31", " Task: TEST-30"])
def test_prompt_rejects_malformed_or_ambiguous_task_without_write(
    monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    """Refuse malformed identity before opening the assignment directory.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        prompt: Submitted Task text selected for this case.
    """
    monkeypatch.setattr(common.core, "repository", lambda _cwd: pytest.fail("repository read"))
    event = {"cwd": str(ROOT), "session_id": "session", "prompt": prompt}
    result = common.prompt(event, "codex")
    # Leading whitespace must not turn a near-match into an explicit Task line.
    if prompt.startswith(" "):
        assert result == {}
    else:
        # An unindented duplicate Task line must block prompt selection.
        assert result["decision"] == "block"


@pytest.mark.parametrize("host", ["codex", "claude-code", "cursor"])
def test_prompt_records_exact_lookup_marker_with_effect_scoped_guidance(
    monkeypatch: pytest.MonkeyPatch,
    host: str,
) -> None:
    """Preserve selected-ticket markers while limiting readiness guidance to implementation.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        host: Explicit native adapter identity.
    """
    data = setup_prompt(monkeypatch)
    event = {"cwd": str(ROOT), "session_id": "session", "prompt": "Task: TEST-30"}
    result = common.prompt(event, host)
    key = core.participant_key({"host": host, "session_id": "session"})
    bindings = data[BINDINGS]
    assert bindings[key + ".assignment.json"] == {"issue_id": "TEST-30"}
    assert bindings[key + ".lookup-required.json"] == {"issue_id": "TEST-30"}
    context = result["hookSpecificOutput"]["additionalContext"]
    assert (
        "Research, inspection, search, administration and ordinary notes remain available"
        in context
    )
    assert "while this ticket lookup is pending" in context
    assert "Before implementation changes, read the exact requested Linear ticket" in context
    assert "then complete scoped workspace setup and verify readiness" in context
    assert "existing approval requirements" in context
    assert "Read the requested Linear ticket first" not in context


def test_prompt_refuses_switch_from_existing_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unbound session never replaces its recorded Task identity.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data = setup_prompt(monkeypatch)
    key = core.participant_key({"host": "codex", "session_id": "session"})
    data[BINDINGS] = {key + ".assignment.json": {"issue_id": "TEST-30"}}
    event = {"cwd": str(ROOT), "session_id": "session", "prompt": "Task: TEST-31"}
    result = common.prompt(event, "codex")
    assert result["decision"] == "block"
    assert data[BINDINGS] == {key + ".assignment.json": {"issue_id": "TEST-30"}}


def test_prompt_records_pending_switch_for_bound_session_without_touching_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bound session's Task line for another issue waits for the verified read.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data = setup_prompt(monkeypatch)
    key = core.participant_key({"host": "claude-code", "session_id": "session"})
    binding = {"issue_id": "TEST-30", "binding_generation": 3}
    data[BINDINGS] = {
        key + ".json": binding,
        key + ".assignment.json": {"issue_id": "TEST-30"},
        key + ".lookup.json": {"id": "TEST-30", "tool_id": "old", "completed": True},
    }
    event = {"cwd": str(ROOT), "session_id": "session", "prompt": "Task: TEST-31"}
    result = common.prompt(event, "claude-code")
    # The binding stays on the old issue; only the pending assignment and gate move.
    assert data[BINDINGS] == {
        key + ".json": binding,
        key + ".assignment.json": {"issue_id": "TEST-31"},
        key + ".lookup-required.json": {"issue_id": "TEST-31"},
    }
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "Before implementation changes, read the exact requested Linear ticket" in context
    assert "rebinds this session from TEST-30" in context


def test_prompt_returns_to_bound_issue_after_unfinished_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed switch read never strands the session: its bound Task line is admitted.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data = setup_prompt(monkeypatch)
    key = core.participant_key({"host": "codex", "session_id": "session"})
    binding = {"issue_id": "TEST-30", "binding_generation": 3}
    # Model a recorded switch to TEST-31 whose startup completed with a failure.
    data[BINDINGS] = {
        key + ".json": binding,
        key + ".assignment.json": {"issue_id": "TEST-31"},
        key + ".lookup-required.json": {"issue_id": "TEST-31"},
        key + ".lookup.json": {"id": "TEST-31", "tool_id": "read-1", "completed": True},
    }
    event = {"cwd": str(ROOT), "session_id": "session", "prompt": "Task: TEST-30"}
    result = common.prompt(event, "codex")
    assert "decision" not in result
    assert data[BINDINGS] == {
        key + ".json": binding,
        key + ".assignment.json": {"issue_id": "TEST-30"},
        key + ".lookup-required.json": {"issue_id": "TEST-30"},
    }
    assert "rebinds" not in result["hookSpecificOutput"]["additionalContext"]


def test_prompt_keeps_matching_lookup_correlation_for_repeated_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeating the pending Task line keeps that issue's own retry correlation.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data = setup_prompt(monkeypatch)
    key = core.participant_key({"host": "codex", "session_id": "session"})
    retry = {"id": "TEST-31", "tool_id": "read-1", "completed": True}
    data[BINDINGS] = {
        key + ".json": {"issue_id": "TEST-30", "binding_generation": 3},
        key + ".assignment.json": {"issue_id": "TEST-31"},
        key + ".lookup-required.json": {"issue_id": "TEST-31"},
        key + ".lookup.json": retry,
    }
    event = {"cwd": str(ROOT), "session_id": "session", "prompt": "Task: TEST-31"}
    common.prompt(event, "codex")
    assert data[BINDINGS][key + ".lookup.json"] == retry


def test_automatic_attach_does_not_create_unregistered_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave an unregistered session unbound after diagnosis.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    requests: list[dict[str, object]] = []

    def execute(request: dict[str, object]) -> dict[str, object]:
        """Capture the only permitted diagnosis call.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        requests.append(request)
        return {"ok": True, "code": "REGISTRATION_REQUIRED"}

    monkeypatch.setattr(common.core, "execute", execute)
    event = {"cwd": str(ROOT), "session_id": "session"}
    assert common.automatic_attach(event, "TEST-30", "codex") is None
    assert [item["operation"] for item in requests] == ["diagnose"]


def test_request_for_uses_only_persisted_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copy repository, issue, and generation from verified local state.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """

    class Store:
        """Return the existing session binding only."""

        def __init__(self, _request: dict[str, object]) -> None:
            """Receive the host-bound diagnostic request.

            Args:
                _request: Ignored lifecycle request accepted by this test callback.
            """

        def __enter__(self) -> Store:
            """Open the modeled binding store.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Close the modeled binding store.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def binding(self) -> dict[str, object]:
            """Return the committed binding identity.

            Returns:
                The recorded session binding fixture.
            """
            return {"issue_id": "TEST-30", "binding_generation": 3}

    monkeypatch.setattr(
        common.core,
        "execute",
        lambda _request: {"ok": True, "code": "REGISTERED", "repo_id": "repo"},
    )
    monkeypatch.setattr(common.core, "Store", Store)
    event = {"cwd": str(ROOT), "session_id": "session", "issue_id": "foreign"}
    result = common.request_for(event, "ready", "codex")
    assert result["worktree"] == str(ROOT)
    assert result["host"] == "codex"
    assert result["session_id"] == "session"
    assert result["issue_id"] == "TEST-30"
    assert result["repo_id"] == "repo"
    assert result["binding_generation"] == 3


def test_request_for_rejects_nonregistered_diagnosis_before_store_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop on the exact diagnostic before reading an unregistered binding.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(
        common.core,
        "execute",
        lambda _request: {"ok": False, "code": "REPOSITORY_MISMATCH"},
    )
    monkeypatch.setattr(common.core, "Store", lambda _request: pytest.fail("opened store"))
    # A conflicting stored assignment must stop prompt admission.
    with pytest.raises(core.WorkspaceError) as captured:
        common.request_for({"cwd": str(ROOT), "session_id": "session"}, "ready", "codex")
    assert captured.value.code == "REPOSITORY_MISMATCH"


def test_request_for_requires_persisted_session_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not derive an issue assignment from the observed prompt or tool input.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """

    class Store:
        """Expose a registered store with no session assignment."""

        def __init__(self, _request: dict[str, object]) -> None:
            """Select the diagnosed repository.

            Args:
                _request: Ignored lifecycle request accepted by this test callback.
            """

        def __enter__(self) -> Store:
            """Hold the modeled binding store.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled binding store.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def binding(self) -> None:
            """Report no persisted session binding."""
            return None

    monkeypatch.setattr(
        common.core,
        "execute",
        lambda _request: {"ok": True, "code": "REGISTERED", "repo_id": "repo"},
    )
    monkeypatch.setattr(common.core, "Store", Store)
    # A stale binding must fail before an automatic attachment.
    with pytest.raises(core.WorkspaceError) as captured:
        common.request_for(
            {"cwd": str(ROOT), "session_id": "session", "issue_id": "TEST-30"},
            "ready",
            "codex",
        )
    assert captured.value.code == "BINDING_MISSING"


def lookup_setup(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, dict[str, object]], str]:
    """Create the explicit Task and lookup-required markers for one Codex session.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.

    Returns:
        The lookup marker store and participant key.
    """
    data = setup_prompt(monkeypatch)
    key = core.participant_key({"host": "codex", "session_id": "session"})
    data[BINDINGS] = {
        key + ".assignment.json": {"issue_id": "TEST-30"},
        key + ".lookup-required.json": {"issue_id": "TEST-30"},
    }
    return data, key


def lookup_event(**fields: object) -> dict[str, object]:
    """Name one directly observed provider read and host tool identity.

    Args:
        fields: Event or request fields varied by this case.

    Returns:
        A direct Linear issue read hook event.
    """
    return {
        "cwd": str(ROOT),
        "session_id": "session",
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-30"},
        "tool_use_id": "tool-1",
        **fields,
    }


def test_ticket_lookup_admits_only_exact_recorded_issue_and_tool_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record the direct issue read before it may complete.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    # A mismatched startup issue cannot override the selected Task.
    with pytest.raises(core.WorkspaceError) as captured:
        codex.ticket_lookup(lookup_event(tool_input={"id": "TEST-31"}))
    assert captured.value.code == "BINDING_CONFLICT"
    assert key + ".lookup.json" not in data[BINDINGS]
    assert codex.ticket_lookup(lookup_event()) == {}
    assert data[BINDINGS][key + ".lookup.json"] == {
        "id": "TEST-30",
        "tool_id": "tool-1",
    }
    # A rejected lifecycle scope must retain its exact diagnostic.
    with pytest.raises(core.WorkspaceError) as captured:
        codex.ticket_lookup(lookup_event(tool_use_id="other"))
    assert captured.value.code == "BINDING_CONFLICT"


def test_ticket_lookup_ignores_unrelated_provider_tool_before_binding_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a direct Linear issue read may use the startup lookup transaction.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(codex.core, "repository", lambda *_args: pytest.fail("opened repository"))
    assert codex.ticket_lookup({"tool_name": "mcp__codex_apps__linear_get_document"}) is None


def test_ticket_lookup_completes_only_after_verified_provider_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clear lookup markers only after exact ticket verification and startup readiness.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    event = lookup_event()
    codex.ticket_lookup(event)
    issue = {"id": "TEST-30", "uuid": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977"}
    provider = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": '{"id":"TEST-30","uuid":"c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977"}',
            }
        ],
    }
    monkeypatch.setattr(
        codex.startup,
        "start",
        lambda _event, verified: {
            "participant_id": "coordinator",
            "coordinator": "coordinator",
            "issue": verified,
        },
    )
    result = codex.ticket_lookup({**event, "tool_response": provider}, complete=True)
    assert "TASK_WORKSPACE_READY" in result["systemMessage"]
    assert issue["id"] in result["systemMessage"]
    assert key + ".lookup.json" not in data[BINDINGS]
    assert key + ".lookup-required.json" not in data[BINDINGS]
    assert key + ".assignment.json" in data[BINDINGS]


def test_ticket_lookup_provider_failure_retains_required_lookup_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove only the failed call marker while keeping Task assignment pending.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    event = lookup_event()
    codex.ticket_lookup(event)
    monkeypatch.setattr(
        codex.startup, "start", lambda *_args: pytest.fail("started without ticket")
    )
    # A missing binding cannot be silently treated as ready.
    with pytest.raises(core.WorkspaceError) as captured:
        codex.ticket_lookup(
            {**event, "tool_response": {"isError": True, "code": "NETWORK_ERROR"}},
            complete=True,
        )
    assert captured.value.code == "PROVIDER_NETWORK_ERROR"
    assert key + ".lookup.json" not in data[BINDINGS]
    assert key + ".lookup-required.json" in data[BINDINGS]


def test_ticket_lookup_completion_requires_matching_pre_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post hook cannot invent a provider read that pre admission never recorded.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    # A conflicting issue identity must remain visible to the caller.
    with pytest.raises(core.WorkspaceError) as captured:
        codex.ticket_lookup(lookup_event(tool_response={"isError": False}), complete=True)
    assert captured.value.code == "BINDING_MISSING"
    assert key + ".lookup-required.json" in data[BINDINGS]
    assert key + ".lookup.json" not in data[BINDINGS]


def test_ticket_lookup_rejects_mismatched_required_marker_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The required marker must still equal the recorded Task assignment.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    data[BINDINGS][key + ".lookup-required.json"] = {"issue_id": "TEST-31"}
    # Reject a startup assignment that disagrees with the binding.
    with pytest.raises(core.WorkspaceError) as captured:
        codex.ticket_lookup(lookup_event())
    assert captured.value.code == "BINDING_CONFLICT"
    assert key + ".lookup.json" not in data[BINDINGS]


def test_ticket_lookup_rejects_malformed_serialized_response_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed provider bytes clear only the attempt marker, preserving Task scope.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    event = lookup_event()
    assert codex.ticket_lookup(event) == {}
    # Propagate failed scope setup instead of reporting readiness.
    with pytest.raises(core.WorkspaceError) as captured:
        codex.ticket_lookup({**event, "tool_response": "{"}, complete=True)
    assert captured.value.code == "PROVIDER_RESPONSE_INVALID"
    assert key + ".lookup.json" not in data[BINDINGS]
    assert key + ".lookup-required.json" in data[BINDINGS]
    assert codex.ticket_lookup(lookup_event(tool_use_id="retry")) == {}


def test_ticket_lookup_keeps_attempt_marker_when_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified ticket does not clear markers before lifecycle startup commits.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    event = lookup_event()
    codex.ticket_lookup(event)
    provider = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": '{"id":"TEST-30","uuid":"c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977"}',
            }
        ],
    }
    monkeypatch.setattr(
        codex.startup,
        "start",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("SOURCE_STALE")),
    )
    # A stale packet source must block automatic startup.
    with pytest.raises(core.WorkspaceError) as captured:
        codex.ticket_lookup({**event, "tool_response": provider}, complete=True)
    assert captured.value.code == "SOURCE_STALE"
    assert data[BINDINGS][key + ".lookup.json"] == {
        "id": "TEST-30",
        "tool_id": "tool-1",
        "completed": True,
    }
    assert key + ".lookup-required.json" in data[BINDINGS]


def test_ticket_lookup_retries_completed_startup_with_same_tool_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified read may resume startup without a second provider admission.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    event = lookup_event()
    provider = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": '{"id":"TEST-30","uuid":"c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977"}',
            }
        ],
    }
    assert codex.ticket_lookup(event) == {}
    starts: list[object] = []

    def start(_event: object, verified: object) -> dict[str, str]:
        """Fail once after ticket verification, then finish the same startup.

        Args:
            _event: Ignored hook event accepted by this test callback.
            verified: Whether provider identity was verified in this case.

        Returns:
            Simulated ready participant and coordinator identities.

        Raises:
            core.WorkspaceError: On the first injected source failure.
        """
        starts.append(verified)
        # Allow exactly one successful start before simulating a stale source.
        if len(starts) == 1:
            raise core.WorkspaceError("SOURCE_STALE")
        return {"participant_id": "owner", "coordinator": "owner"}

    monkeypatch.setattr(codex.startup, "start", start)
    # Verify that the stale source fails the first setup attempt.
    with pytest.raises(core.WorkspaceError, match="SOURCE_STALE"):
        codex.ticket_lookup({**event, "tool_response": provider}, complete=True)
    completed = {"id": "TEST-30", "tool_id": "tool-1", "completed": True}
    assert data[BINDINGS][key + ".lookup.json"] == completed
    assert codex.ticket_lookup(event) == {}
    assert data[BINDINGS][key + ".lookup.json"] == completed
    result = codex.ticket_lookup({**event, "tool_response": provider}, complete=True)
    assert "TASK_WORKSPACE_READY" in result["systemMessage"]
    assert len(starts) == 2
    assert key + ".lookup.json" not in data[BINDINGS]
    assert key + ".lookup-required.json" not in data[BINDINGS]
    assert data[BINDINGS][key + ".assignment.json"] == {"issue_id": "TEST-30"}


def test_ticket_lookup_replaces_completed_failure_with_fresh_exact_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh tool ID supersedes only a completed attempt for the same issue.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    event = lookup_event()
    fresh = lookup_event(tool_use_id="tool-2")
    provider = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": '{"id":"TEST-30","uuid":"c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977"}',
            }
        ],
    }
    assert codex.ticket_lookup(event) == {}
    monkeypatch.setattr(
        codex.startup,
        "start",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("SOURCE_STALE")),
    )
    # The changed source must fail the next packet read.
    with pytest.raises(core.WorkspaceError, match="SOURCE_STALE"):
        codex.ticket_lookup({**event, "tool_response": provider}, complete=True)
    assert codex.ticket_lookup(fresh) == {}
    assert data[BINDINGS][key + ".lookup.json"] == {"id": "TEST-30", "tool_id": "tool-2"}
    # A different Task cannot replace the recorded assignment.
    with pytest.raises(core.WorkspaceError, match="BINDING_CONFLICT"):
        codex.ticket_lookup({**event, "tool_response": provider}, complete=True)
    assert data[BINDINGS][key + ".lookup.json"] == {"id": "TEST-30", "tool_id": "tool-2"}
    # A different issue cannot replace the live binding.
    with pytest.raises(core.WorkspaceError, match="BINDING_CONFLICT"):
        codex.ticket_lookup(event)
    monkeypatch.setattr(
        codex.startup,
        "start",
        lambda *_args: {"participant_id": "owner", "coordinator": "owner"},
    )
    result = codex.ticket_lookup({**fresh, "tool_response": provider}, complete=True)
    assert "TASK_WORKSPACE_READY" in result["systemMessage"]
    assert key + ".lookup.json" not in data[BINDINGS]
    assert key + ".lookup-required.json" not in data[BINDINGS]


def test_ticket_lookup_rejects_corrupt_completed_marker_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the exact completed issue and string tool ID authorize replacement.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    name = key + ".lookup.json"
    # Corrupt each binding field independently to test fail-closed admission.
    for corrupt in (
        {"id": "TEST-31", "tool_id": "tool-1", "completed": True},
        {"id": "TEST-30", "tool_id": 7, "completed": True},
        {"id": "TEST-30", "tool_id": "tool-1", "completed": "yes"},
    ):
        data[BINDINGS][name] = corrupt
        # No corrupted binding may pass native prompt setup.
        with pytest.raises(core.WorkspaceError, match="BINDING_CONFLICT"):
            codex.ticket_lookup(lookup_event(tool_use_id="tool-2"))
        assert data[BINDINGS][name] == corrupt


def test_ticket_lookup_failed_startup_preserves_a_newer_lookup_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old completion cannot mark a replacement lookup as completed.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    event = lookup_event()
    provider = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": '{"id":"TEST-30","uuid":"c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977"}',
            }
        ],
    }
    assert codex.ticket_lookup(event) == {}
    replacement = {"id": "TEST-30", "tool_id": "tool-2"}

    def interrupted_start(*_args: object) -> None:
        """Model a replacement written after the old completion released its lock.

        Args:
            _args: Ignored positional arguments accepted by this test callback.
        """
        data[BINDINGS][key + ".lookup.json"] = replacement
        raise core.WorkspaceError("SOURCE_STALE")

    monkeypatch.setattr(codex.startup, "start", interrupted_start)
    # An assigned source changed after delivery must fail validation.
    with pytest.raises(core.WorkspaceError, match="SOURCE_STALE"):
        codex.ticket_lookup({**event, "tool_response": provider}, complete=True)
    assert data[BINDINGS][key + ".lookup.json"] == replacement
    assert key + ".lookup-required.json" in data[BINDINGS]


def test_ticket_lookup_reader_message_keeps_coordinator_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified reader receives its scope without claiming roadmap coordination.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    data, key = lookup_setup(monkeypatch)
    event = lookup_event()
    codex.ticket_lookup(event)
    provider = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": '{"id":"TEST-30","uuid":"c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977"}',
            }
        ],
    }
    monkeypatch.setattr(
        codex.startup,
        "start",
        lambda *_args: {"participant_id": "reader", "coordinator": "owner"},
    )
    result = codex.ticket_lookup({**event, "tool_response": provider}, complete=True)
    assert (
        "Reader scope only. Coordinator owner retains roadmap ownership" in result["systemMessage"]
    )
    assert key + ".lookup.json" not in data[BINDINGS]
