"""Verify native adapter recovery and observation boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


CHECKOUT = Path(Path.cwd().anchor) / "checkout"
OTHER = Path(Path.cwd().anchor) / "other"


def event() -> dict[str, Any]:
    """Supply one identity-validated native hook event.

    Returns:
        The native hook event envelope for this case.
    """
    return {
        "session_id": "session",
        "cwd": str(CHECKOUT),
        "tool_use_id": "tool-1",
        "tool_name": "Shell",
        "tool_input": {"command": "echo ok"},
    }


def test_native_bootstrap_rejects_non_shell_or_extra_args_before_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the documented shell input can reach canonical bootstrap parsing.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common, "canonical_bootstrap", lambda *_args: pytest.fail("parsed"))
    assert common.native_bootstrap({**event(), "tool_name": "Read"}, "cursor") is False
    assert (
        common.native_bootstrap(
            {**event(), "tool_input": {"command": "echo ok", "async": True}}, "cursor"
        )
        is False
    )


def test_native_bootstrap_rejects_foreign_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a native directory override outside the observed repository.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common.core, "repository", lambda path: (Path(path), None, []))
    monkeypatch.setattr(common, "canonical_bootstrap", lambda *_args: pytest.fail("parsed"))
    altered = {**event(), "tool_input": {"command": "echo ok", "working_directory": str(OTHER)}}
    assert common.native_bootstrap(altered, "cursor") is False


def test_native_bootstrap_passes_exact_shell_command_to_shared_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass one eligible native command and the readiness state unchanged.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    observed: list[tuple[object, ...]] = []

    def canonical(native: dict[str, Any], command: object, host: str, ready: bool) -> bool:
        """Capture the precise canonical parser inputs.

        Args:
            native: Selected native host and disposable repository fixture.
            command: Shell command selected for this case.
            host: Native adapter selected for this case.
            ready: Whether this case starts from a ready binding.

        Returns:
            The canonical serialized fixture value.
        """
        observed.append((native, command, host, ready))
        return True

    monkeypatch.setattr(common, "canonical_bootstrap", canonical)
    native = event()
    assert common.native_bootstrap(native, "cursor", ready=True) is True
    assert observed == [(native, "echo ok", "cursor", True)]


def test_native_bootstrap_rejects_background_and_repository_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unproven background shell or failed root check cannot become bootstrap.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common, "canonical_bootstrap", lambda *_args: pytest.fail("parsed"))
    claude = {
        **event(),
        "tool_name": "Bash",
        "tool_input": {"command": "echo ok", "run_in_background": True},
    }
    assert common.native_bootstrap(claude, "claude-code") is False
    monkeypatch.setattr(
        common.core,
        "repository",
        lambda _path: (_ for _ in ()).throw(core.WorkspaceError("UNSAFE_PATH")),
    )
    cursor = {**event(), "tool_input": {"command": "echo ok", "working_directory": str(CHECKOUT)}}
    assert common.native_bootstrap(cursor, "cursor") is False


def test_native_context_reports_readiness_or_bounded_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report core readiness without granting tool permission or losing diagnostics.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    calls: list[dict[str, Any]] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Record a readiness query and return a validated success.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request)
        return {"ok": True, "code": "READY"}

    monkeypatch.setattr(common.core, "execute", execute)
    context = common.native_context(event(), "cursor")
    assert "For implementation work, retrieve the assigned packet" in context
    assert "ordinary notes remain available" in context
    assert calls == [{"operation": "ready"}]
    monkeypatch.setattr(
        common,
        "request_for",
        lambda *_args: (_ for _ in ()).throw(core.WorkspaceError("BINDING_MISSING")),
    )
    assert "available without a task" in common.native_context(event(), "cursor")
    assert "TASK_WORKSPACE_NOT_READY" not in common.native_context(event(), "cursor")


def test_native_observe_emits_advisory_event_and_swallows_missing_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never convert an advisory ending into completion or a hard hook failure.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    monkeypatch.setattr(common.core, "execute", lambda request: calls.append(request))
    assert common.native_observe(event(), "cursor") is None
    assert calls == [
        {"operation": "event", "event_type": "observation", "event": {"code": "UNKNOWN"}}
    ]
    monkeypatch.setattr(
        common, "request_for", lambda *_args: (_ for _ in ()).throw(OSError("unavailable"))
    )
    assert common.native_observe(event(), "cursor") is None
    assert len(calls) == 1


def test_run_native_delegates_bounded_hook_supervision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve the provided handler and failure renderer at the runner boundary.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """

    def handler(_event: object) -> dict[str, object]:
        """Return an empty modeled native hook response.

        Args:
            _event: Ignored hook event accepted by this test callback.

        Returns:
            The simulated hook handler response.
        """
        return {}

    def failure(_code: str, _action: str) -> dict[str, object]:
        """Return an empty modeled failure response.

        Args:
            _code: Ignored diagnostic code accepted by this test callback.
            _action: Ignored action argument accepted by this failure seam.

        Returns:
            The simulated hook failure response.
        """
        return {}

    observed: list[tuple[object, object]] = []

    def run(actual_handler: object, actual_failure: object) -> int:
        """Capture the runner entry point arguments.

        Args:
            actual_handler: Hook handler used by the supervised worker.
            actual_failure: Failure renderer used after the runner intercepts the hook.

        Returns:
            The simulated subprocess or hook result.
        """
        observed.append((actual_handler, actual_failure))
        return 2

    monkeypatch.setattr(common.runner, "run", run)
    assert common.run_native(handler, failure) == 2
    assert observed == [(handler, failure)]


def test_automatic_attach_uses_only_persisted_startup_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create and scope only the exact coordinator-owned startup packet.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    native = {"cwd": str(CHECKOUT), "session_id": "session"}
    key = core.participant_key({"host": "codex", "session_id": "session"})
    packet = [{"reader": key, "id": "roadmap"}]
    setup = {"issue_id": "TEST-30", "issue_uuid": "uuid", "coordinator": key, "packet": packet}
    calls: list[dict[str, Any]] = []

    class Bindings:
        """Expose the one explicit startup document for the session."""

        def exists(self, name: str) -> bool:
            """Require the session-keyed startup filename.

            Args:
                name: Event or case name selected for this test.

            Returns:
                Whether the requested fixture entry exists.
            """
            assert name == key + ".startup.json"
            return True

        def json(self, _name: str) -> dict[str, Any]:
            """Return the recorded setup rather than prompt-derived content.

            Args:
                _name: Ignored event name accepted by this test callback.

            Returns:
                The stored JSON fixture value.
            """
            return setup

    class Store:
        """Supply no existing participant binding for this fresh session."""

        bindings = Bindings()

        def __init__(self, _request: dict[str, Any]) -> None:
            """Accept the diagnosed registered repository.

            Args:
                _request: Ignored lifecycle request accepted by this test callback.
            """

        def __enter__(self) -> Store:
            """Expose the modeled store handle.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled store handle.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def binding(self) -> None:
            """Keep the session unbound until creation succeeds."""
            return None

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Capture ordered diagnosis, creation, revision, and scope calls.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request)
        operation = request["operation"]
        # The fixture first proves the checkout is registered.
        if operation == "diagnose":
            return {"ok": True, "code": "REGISTERED", "repo_id": "repo", "revision": 4}
        # Creation provides an issue generation for setup.
        if operation == "create":
            return {"ok": True, "code": "CREATED", "binding_generation": 1}
        # Scope reports the deliberately selected failure.
        if operation == "scope":
            return {"ok": True, "code": "SCOPED"}
        pytest.fail(f"unexpected operation {operation}")

    monkeypatch.setattr(common.core, "Store", Store)
    monkeypatch.setattr(common.core, "execute", execute)
    assert common.automatic_attach(native, "TEST-30", "codex") is None
    assert [item["operation"] for item in calls] == ["diagnose", "create", "diagnose", "scope"]
    assert calls[-1]["target_participant"] == key
    assert calls[-1]["packet"] == packet
    assert calls[-1]["expected_revision"] == 4


@pytest.mark.usefixtures("protected_effect")
def test_native_pre_stops_on_failed_readiness_without_tool_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the exact core diagnostic and do not admit external work.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    calls: list[str] = []
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Reject readiness and record any forbidden later dispatch.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request["operation"])
        return {"ok": False, "code": "SOURCE_STALE"}

    monkeypatch.setattr(common.core, "execute", execute)
    native = {
        **event(),
        "tool_name": "Write",
        "tool_input": {"file_path": "src/main.py", "content": "source"},
    }
    # The native boundary must reject a mismatched lifecycle command.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_pre(native, "cursor")
    assert captured.value.code == "SOURCE_STALE"
    assert calls == ["ready"]


@pytest.mark.usefixtures("recorded_operation")
def test_native_post_rejects_cursor_failure_without_documented_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an untyped failure from settling a pending native tool.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "request_for", lambda *_args: pytest.fail("read binding"))
    native = {
        **event(),
        "tool_name": "Read",
        "tool_input": {},
        "error_message": "failed",
        "failure_type": "unknown",
    }
    # Unsupported shell arguments cannot authorize bootstrap.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_post(native, "cursor", failed=True)
    assert captured.value.code == "INVALID_REQUEST"


@pytest.mark.usefixtures("recorded_operation")
def test_native_post_rejects_async_cursor_shell_output_without_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require a typed completed exit code and no background metadata.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "request_for", lambda *_args: pytest.fail("read binding"))
    native = {**event(), "tool_output": '{"exitCode":0,"task_id":"background"}'}
    # A noncanonical bootstrap invocation must be rejected.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_post(native, "cursor", failed=False)
    assert captured.value.code == "HOST_UNSUPPORTED_ASYNC"


@pytest.mark.usefixtures("recorded_operation")
def test_native_post_preserves_core_settlement_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a failed core completion visible after a valid synchronous result.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(
        common.core,
        "execute",
        lambda request: requests.append(request) or {"ok": False, "code": "UNKNOWN_OPERATION"},
    )
    native = {**event(), "tool_name": "Read", "tool_input": {}, "tool_output": "bytes"}
    # An unregistered native event must not start work.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_post(native, "cursor", failed=False)
    assert captured.value.code == "UNKNOWN_OPERATION"
    assert requests[0]["operation"] == "tool-complete"
    assert requests[0]["tool_id"] == "tool-1"


def test_automatic_attach_keeps_existing_assigned_packet_without_recreation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return after verified existing participant scope, preserving its packet.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    native = {"cwd": str(CHECKOUT), "session_id": "session"}
    key = core.participant_key({"host": "codex", "session_id": "session"})

    class Control:
        """Expose a held issue control and lock context."""

        def __enter__(self) -> Control:
            """Hold the modeled control handle.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled control handle.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def lock(self) -> Control:
            """Hold the issue lock while reading participant scope.

            Returns:
                The fixture lock context manager.
            """
            return self

        def child(self, identifier: str) -> Control:
            """Select only the recorded bound issue.

            Args:
                identifier: Requested shorthand issue identifier.

            Returns:
                The requested child directory fixture.
            """
            assert identifier == "TEST-30"
            return self

    class Bindings:
        """Report no separate startup document for an already bound session."""

        def exists(self, _name: str) -> bool:
            """Keep startup setup absent.

            Args:
                _name: Ignored event name accepted by this test callback.

            Returns:
                Whether the requested fixture entry exists.
            """
            return False

    class Store:
        """Expose the existing exact issue binding and control handle."""

        def __init__(self, _request: dict[str, Any]) -> None:
            """Select the current bound store.

            Args:
                _request: Ignored lifecycle request accepted by this test callback.
            """
            self.bindings = Bindings()
            self.issues = Control()

        def __enter__(self) -> Store:
            """Hold the modeled store.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled store.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def binding(self) -> dict[str, str]:
            """Return the persisted issue assignment.

            Returns:
                The recorded session binding fixture.
            """
            return {"issue_id": "TEST-30"}

    class Issue:
        """Expose the current participant packet from committed state."""

        def __init__(self, _store: Store, _control: Control, _identifier: str) -> None:
            """Bind to the selected existing issue.

            Args:
                _store: Ignored store argument accepted by this test callback.
                _control: Ignored lifecycle control supplied by this test seam.
                _identifier: Ignored issue identifier accepted by this test callback.
            """

        def recover(self) -> None:
            """Model completed transaction recovery."""

        def committed_state(self) -> dict[str, Any]:
            """Return one scoped existing participant.

            Returns:
                The committed issue state fixture.
            """
            return {"participants": {key: {"packet": [{"id": "roadmap"}]}}}

    calls: list[str] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Permit diagnosis and reject any subsequent create or attach.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request["operation"])
        assert request["operation"] == "diagnose"
        return {"ok": True, "code": "REGISTERED", "repo_id": "repo"}

    monkeypatch.setattr(common.core, "Store", Store)
    monkeypatch.setattr(common.core, "Issue", Issue)
    monkeypatch.setattr(common.core, "execute", execute)
    assert common.automatic_attach(native, "TEST-30", "codex") is None
    assert calls == ["diagnose"]


def test_automatic_attach_bound_without_packet_does_not_invent_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing binding with no packet waits for explicit scope assignment.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    key = core.participant_key({"host": "codex", "session_id": "session"})

    class Node:
        """Supply only the issue read chain and absent startup marker."""

        def __enter__(self) -> Node:
            """Hold the modeled issue control.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled issue control.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def child(self, _name: str) -> Node:
            """Select the existing issue.

            Args:
                _name: Ignored event name accepted by this test callback.

            Returns:
                The requested child directory fixture.
            """
            return self

        def lock(self) -> Node:
            """Hold the issue lock.

            Returns:
                The fixture lock context manager.
            """
            return self

        def exists(self, _name: str) -> bool:
            """Report no startup setup.

            Args:
                _name: Ignored event name accepted by this test callback.

            Returns:
                Whether the requested fixture entry exists.
            """
            return False

    class Store:
        """Expose an existing bound participant and issue handle."""

        def __init__(self, _request: dict[str, Any]) -> None:
            """Select the bound store.

            Args:
                _request: Ignored lifecycle request accepted by this test callback.
            """
            self.bindings = Node()
            self.issues = Node()

        def __enter__(self) -> Store:
            """Hold the store.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the store.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def binding(self) -> dict[str, str]:
            """Return the exact issue binding.

            Returns:
                The recorded session binding fixture.
            """
            return {"issue_id": "TEST-30"}

    class Issue:
        """Expose an existing participant with no assigned packet."""

        def __init__(self, _store: Store, _control: Node, _identifier: str) -> None:
            """Select the existing issue.

            Args:
                _store: Ignored store argument accepted by this test callback.
                _control: Ignored lifecycle control supplied by this test seam.
                _identifier: Ignored issue identifier accepted by this test callback.
            """

        def recover(self) -> None:
            """Model completed recovery."""

        def committed_state(self) -> dict[str, Any]:
            """Return an unscoped participant.

            Returns:
                The committed issue state fixture.
            """
            return {"participants": {key: {"packet": None}}}

    calls: list[str] = []
    monkeypatch.setattr(common.core, "Store", Store)
    monkeypatch.setattr(common.core, "Issue", Issue)
    monkeypatch.setattr(
        common.core,
        "execute",
        lambda request: (
            calls.append(request["operation"]) or {"code": "REGISTERED", "repo_id": "repo"}
        ),
    )
    assert (
        common.automatic_attach({"cwd": str(CHECKOUT), "session_id": "session"}, "TEST-30", "codex")
        is None
    )
    assert calls == ["diagnose"]


@pytest.mark.parametrize("code", ["SCOPE_MISSING", "BINDING_MISSING", "REVISION_CONFLICT"])
def test_automatic_attach_without_setup_attempts_only_existing_core_scope(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """Do not create a new issue when no startup packet was persisted.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        code: Diagnostic code selected for this case.
    """
    native = {"cwd": str(CHECKOUT), "session_id": "session"}

    class Bindings:
        """Report no explicit startup assignment."""

        def exists(self, _name: str) -> bool:
            """Keep startup setup absent.

            Args:
                _name: Ignored event name accepted by this test callback.

            Returns:
                Whether the requested fixture entry exists.
            """
            return False

    class Store:
        """Keep the observed session unbound."""

        def __init__(self, _request: dict[str, Any]) -> None:
            """Expose only the binding index.

            Args:
                _request: Ignored lifecycle request accepted by this test callback.
            """
            self.bindings = Bindings()

        def __enter__(self) -> Store:
            """Hold the modeled store.

            Returns:
                The active disposable context manager fixture.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled store.

            Args:
                _args: Ignored positional arguments accepted by this test callback.
            """

        def binding(self) -> None:
            """Keep the session unbound."""
            return None

    calls: list[str] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Permit registration discovery and report absent scope on attach.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle result for this request.
        """
        calls.append(request["operation"])
        # The fake lifecycle returns registration before testing admission.
        if request["operation"] == "diagnose":
            return {"ok": True, "code": "REGISTERED", "repo_id": "repo"}
        assert request["operation"] == "attach"
        return {"ok": False, "code": code}

    monkeypatch.setattr(common.core, "Store", Store)
    monkeypatch.setattr(common.core, "execute", execute)
    # Only revision conflict should raise from this fixture.
    if code == "REVISION_CONFLICT":
        # Assert the exact revision conflict from the lifecycle call.
        with pytest.raises(core.WorkspaceError) as captured:
            common.automatic_attach(native, "TEST-30", "codex")
        assert captured.value.code == code
    else:
        # Other lifecycle diagnostics must remain bounded without an exception.
        assert common.automatic_attach(native, "TEST-30", "codex") is None
    assert calls == ["diagnose", "attach"]


def test_native_pre_admits_exact_unready_bootstrap_without_binding_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Let the exact recovery command run before ordinary session readiness.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(common, "request_for", lambda *_args: pytest.fail("read binding"))
    assert common.native_pre(event(), "cursor") is None


@pytest.mark.usefixtures("protected_effect")
def test_native_pre_does_not_record_ready_bootstrap_as_external_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check readiness but keep the lifecycle command out of pending work.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    states: list[bool] = []

    def bootstrap(_event: dict[str, Any], _host: str, ready: bool = False) -> bool:
        """Allow only the post-readiness bootstrap check.

        Args:
            _event: Ignored hook event accepted by this test callback.
            _host: Ignored host identity accepted by this test callback.
            ready: Whether this case starts from a ready binding.

        Returns:
            Whether the simulated call occurs after readiness.
        """
        states.append(ready)
        return ready

    monkeypatch.setattr(common, "native_bootstrap", bootstrap)
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    monkeypatch.setattr(common.core, "execute", lambda _request: {"ok": True, "code": "READY"})
    monkeypatch.setattr(common, "native_tool", lambda *_args: pytest.fail("admitted external"))
    assert common.native_pre(event(), "cursor") is None
    assert states == [False, True]


def test_native_post_ignores_exact_lifecycle_transaction_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep lifecycle command completion out of ordinary pending-tool state.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(common, "native_tool", lambda *_args: pytest.fail("admitted tool"))
    assert common.native_post(event(), "cursor", failed=False) is None


@pytest.mark.usefixtures("recorded_operation")
def test_native_post_rejects_unproven_claude_bash_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not settle a Claude Bash call that reports a background task marker.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "native_tool", lambda *_args: "Bash")
    monkeypatch.setattr(common, "request_for", lambda *_args: pytest.fail("settled tool"))
    native = {
        **event(),
        "tool_name": "Bash",
        "tool_input": {"command": "echo ok"},
        "tool_response": {
            "interrupted": False,
            "stdout": "",
            "stderr": "",
            "task_id": "background",
        },
    }
    # Malformed native identity must fail before tool admission.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_post(native, "claude-code", failed=False)
    assert captured.value.code == "HOST_UNSUPPORTED_ASYNC"


@pytest.mark.parametrize(
    ("host", "tool", "response"),
    [
        ("claude-code", "Read", {"tool_response": "file contents"}),
        (
            "claude-code",
            "Bash",
            {"tool_response": {"interrupted": False, "stdout": "ok", "stderr": ""}},
        ),
        ("cursor", "Read", {"tool_output": "file contents"}),
        ("cursor", "Shell", {"tool_output": '{"exitCode":0}'}),
    ],
)
@pytest.mark.usefixtures("recorded_operation")
def test_native_post_settles_only_documented_synchronous_success(
    monkeypatch: pytest.MonkeyPatch, host: str, tool: str, response: dict[str, object]
) -> None:
    """A typed synchronous host completion records the observed tool exactly once.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        host: Native adapter selected for this case.
        tool: Tool identity selected for this case.
        response: Provider or hook response supplied by this case.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "native_tool", lambda *_args: tool)
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        common.core, "execute", lambda request: calls.append(request) or {"ok": True, "code": "OK"}
    )
    native = {**event(), "tool_name": tool, "tool_input": {}, **response}
    assert common.native_post(native, host, failed=False) is None
    assert len(calls) == 1
    assert calls[0]["operation"] == "tool-complete"
    assert calls[0]["tool_id"] == "tool-1"
    assert calls[0]["completed"] is True


@pytest.mark.parametrize(
    ("host", "field", "extra"),
    [
        ("claude-code", "error", {}),
        ("cursor", "error_message", {"failure_type": "timeout"}),
    ],
)
@pytest.mark.usefixtures("recorded_operation")
def test_native_post_settles_documented_file_tool_failure(
    monkeypatch: pytest.MonkeyPatch, host: str, field: str, extra: dict[str, object]
) -> None:
    """A host failure event is a terminal observation for synchronous file tools.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        host: Native adapter selected for this case.
        field: Request or event field varied by this case.
        extra: Additional request fields that must be rejected.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "native_tool", lambda *_args: "Read")
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        common.core, "execute", lambda request: calls.append(request) or {"ok": True, "code": "OK"}
    )
    native = {**event(), "tool_name": "Read", "tool_input": {}, field: "failed", **extra}
    assert common.native_post(native, host, failed=True) is None
    assert len(calls) == 1
    assert calls[0]["completed"] is True


@pytest.mark.usefixtures("protected_effect")
def test_native_pre_records_only_eligible_tool_after_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native implementation edit starts after readiness and transport validation.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "native_tool", lambda *_args: "Write")
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        common.core, "execute", lambda request: calls.append(request) or {"ok": True, "code": "OK"}
    )
    assert (
        common.native_pre(
            {**event(), "tool_name": "Write", "tool_input": {"file_path": "src/main.py"}}, "cursor"
        )
        is None
    )
    assert [call["operation"] for call in calls] == ["ready", "tool-start"]
    assert calls[-1]["request_id"] == "pre:tool-1"
