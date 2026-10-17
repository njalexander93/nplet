"""Exercise the Claude subagent join route against modeled lifecycle boundaries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


HOST = "claude-code"
PARENT = "parent"
CHILD = PARENT + "/agent/agent-1"
PARENT_KEY = core.participant_key({"host": HOST, "session_id": PARENT})
CHILD_KEY = core.participant_key({"host": HOST, "session_id": CHILD})


def parent_event(root: Path, tool_id: str = "agent-call") -> dict[str, Any]:
    """Build the parent's identity-validated Agent PreToolUse envelope.

    Args:
        root: Checkout root used as the hook working directory.
        tool_id: Native Agent tool-use ID.

    Returns:
        The parent envelope for one Agent call.
    """
    return {"cwd": str(root), "session_id": PARENT, "tool_use_id": tool_id, "tool_name": "Agent"}


def child_event(root: Path) -> dict[str, Any]:
    """Build the normalized SubagentStart envelope for the parent's child.

    Args:
        root: Checkout root used as the hook working directory.

    Returns:
        The child envelope carrying its parent session.
    """
    return {
        "hook_event_name": "SubagentStart",
        "cwd": str(root),
        "session_id": CHILD,
        "parent_session_id": PARENT,
        "child": True,
    }


def parent_request(operation: str = "ready") -> dict[str, Any]:
    """Build the verified parent lifecycle request the child route derives from.

    Args:
        operation: Lifecycle operation named by the request.

    Returns:
        The parent's bound request.
    """
    return {
        "schema_version": 1,
        "operation": operation,
        "request_id": "parent-1",
        "host": HOST,
        "session_id": PARENT,
        "repo_id": "repo",
        "issue_id": "TEST-30",
        "binding_generation": 1,
    }


def issue_state(pending: dict[str, Any] | None = None, **participants: Any) -> dict[str, Any]:
    """Build committed issue state with the parent participant's pending work.

    Args:
        pending: The parent's pending tool IDs.
        **participants: Additional participant records keyed by participant key.

    Returns:
        A minimal committed state with revision and issue identity.
    """
    return {
        "revision": 7,
        "issue_uuid": "uuid",
        "participants": {PARENT_KEY: {"pending": pending or {}}, **participants},
    }


def recorded_calls(root: Path) -> list[str]:
    """Read the parent's recorded Agent call IDs from the binding directory.

    Args:
        root: Checkout root holding the local task directory.

    Returns:
        The recorded tool IDs, or an empty list without a marker.
    """
    name = common._agent_calls_name({"session_id": PARENT}, HOST)
    with (
        core.Directory.absolute(root) as worktree,
        worktree.child(".task") as local,
        local.child(".bindings") as bindings,
    ):
        return list(bindings.json(name)["tool_ids"]) if bindings.exists(name) else []


def test_agent_calls_marker_is_named_by_the_parent_participant_key() -> None:
    """Name the marker by the exact parent participant key and fixed suffix."""
    name = common._agent_calls_name({"session_id": PARENT}, HOST)
    assert name == PARENT_KEY + ".agent-calls.json"
    # Another host or session names a different marker.
    assert common._agent_calls_name({"session_id": PARENT}, "cursor") != name
    assert common._agent_calls_name({"session_id": CHILD}, HOST) != name


def test_record_agent_call_keeps_bounded_unique_recent_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Append each admitted call once and retain only the most recent bounded entries.

    Args:
        monkeypatch: Replaces the repository boundary with the temporary checkout.
        tmp_path: Temporary checkout root.
    """
    monkeypatch.setattr(common.core, "repository", lambda _path: (tmp_path, None, [tmp_path]))
    monkeypatch.setattr(common, "AGENT_CALL_LIMIT", 3)
    # The first call creates the task and binding directories with the marker.
    common.record_agent_call(parent_event(tmp_path, "call-1"), HOST)
    assert recorded_calls(tmp_path) == ["call-1"]
    # A repeated ID moves to the end instead of duplicating.
    common.record_agent_call(parent_event(tmp_path, "call-2"), HOST)
    common.record_agent_call(parent_event(tmp_path, "call-1"), HOST)
    assert recorded_calls(tmp_path) == ["call-2", "call-1"]
    # The marker keeps only the most recent bounded entries.
    common.record_agent_call(parent_event(tmp_path, "call-3"), HOST)
    common.record_agent_call(parent_event(tmp_path, "call-4"), HOST)
    assert recorded_calls(tmp_path) == ["call-1", "call-3", "call-4"]
    # A malformed native ID is rejected before the marker changes.
    with pytest.raises(core.WorkspaceError) as captured:
        common.record_agent_call(parent_event(tmp_path, "with space"), HOST)
    assert captured.value.code == "INVALID_REQUEST"
    assert recorded_calls(tmp_path) == ["call-1", "call-3", "call-4"]


@pytest.mark.usefixtures("protected_effect")
def test_native_pre_records_agent_call_before_tool_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record the parent's Agent call marker before the core tool-start.

    Args:
        monkeypatch: Replaces bootstrap, binding and lifecycle boundaries.
    """
    order: list[str] = []
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    monkeypatch.setattr(common, "record_agent_call", lambda _event, _host: order.append("record"))

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Record lifecycle operations in order.

        Args:
            request: Lifecycle operation emitted by the adapter.

        Returns:
            Successful lifecycle response for the captured operation.
        """
        order.append(str(request["operation"]))
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(common.core, "execute", execute)
    event = {
        "tool_use_id": "agent-call",
        "tool_name": "Agent",
        "tool_input": {"description": "d", "prompt": "p"},
    }
    assert common.native_pre(event, HOST) is None
    assert order == ["ready", "record", "tool-start"]
    # A protected source edit records pending work without an Agent launch marker.
    order.clear()
    assert (
        common.native_pre(
            {
                **event,
                "tool_name": "Write",
                "tool_input": {"file_path": "src/main.py", "content": "source"},
            },
            HOST,
        )
        is None
    )
    assert order == ["ready", "tool-start"]


class Lock:
    """Model a held control lock."""

    def __enter__(self) -> Lock:
        """Hold the lock.

        Returns:
            This lock.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Release the lock.

        Args:
            _args: Context manager exception fields, unused by this fake.
        """


class Control(Lock):
    """Model one issue control directory whose lock is observable."""

    def __init__(self, locks: list[str]) -> None:
        """Record lock acquisitions into the shared list.

        Args:
            locks: Shared list receiving one entry per acquisition.
        """
        self.locks = locks

    def lock(self) -> Lock:
        """Take the issue control lock.

        Returns:
            A held lock.
        """
        self.locks.append("issue")
        return Lock()


def test_issue_state_recovers_under_the_issue_lock_then_reads_committed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recover any staged transaction and return the committed state under the lock.

    Args:
        monkeypatch: Replaces the store and issue boundaries.
    """
    locks: list[str] = []
    state = issue_state()
    opened: list[str] = []

    class Store(Lock):
        """Model the registered store serving one issue control directory."""

        def __init__(self, request: dict[str, Any]) -> None:
            """Open the store for the parent request.

            Args:
                request: Verified parent request.
            """
            assert request["issue_id"] == "TEST-30"
            self.issues = SimpleNamespace(
                child=lambda identifier: opened.append(identifier) or Control(locks)
            )

    class Issue:
        """Model the issue transaction whose recovery precedes the committed read."""

        def __init__(self, _store: Store, _control: Control, identifier: str) -> None:
            """Bind the modeled issue.

            Args:
                _store: Opened store, unused by this fake.
                _control: Opened control directory, unused by this fake.
                identifier: Issue identifier under recovery.
            """
            assert identifier == "TEST-30"

        def recover(self) -> None:
            """Record recovery while the lock is held."""
            locks.append("recover")

        def committed_state(self) -> dict[str, Any]:
            """Return the committed state.

            Returns:
                The modeled committed state.
            """
            locks.append("read")
            return state

    monkeypatch.setattr(common.core, "Store", Store)
    monkeypatch.setattr(common.core, "Issue", Issue)
    assert common._issue_state(parent_request()) is state
    assert opened == ["TEST-30"]
    assert locks == ["issue", "recover", "read"]


def join_executor(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Serve scripted join outcomes and capture every join request.

    Args:
        monkeypatch: Replaces the lifecycle execution boundary.
        outcomes: Responses returned to successive join requests.

    Returns:
        The list receiving captured join requests.
    """
    calls: list[dict[str, Any]] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Return the next scripted outcome for one join request.

        Args:
            request: Lifecycle join request.

        Returns:
            The scripted response.
        """
        calls.append(request)
        return outcomes.pop(0)

    monkeypatch.setattr(common.core, "execute", execute)
    monkeypatch.setattr(common.time, "sleep", lambda _seconds: None)
    return calls


def test_join_child_joins_at_the_freshly_read_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Join with the revision read immediately before the attempt.

    Args:
        monkeypatch: Replaces the issue-state and lifecycle boundaries.
        tmp_path: Checkout root named by the child envelope.
    """
    monkeypatch.setattr(common, "_issue_state", lambda _parent: issue_state())
    calls = join_executor(monkeypatch, [{"ok": True, "code": "JOINED"}])
    common._join_child(child_event(tmp_path), HOST, parent_request(), common.time.monotonic() + 5)
    assert len(calls) == 1
    joined = calls[0]
    assert joined["operation"] == "join"
    assert joined["session_id"] == CHILD
    assert joined["worktree"] == str(tmp_path)
    assert (joined["issue_id"], joined["issue_uuid"], joined["expected_revision"]) == (
        "TEST-30",
        "uuid",
        7,
    )
    core.token(str(joined["request_id"]))


def test_join_child_skips_the_join_for_an_existing_member(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An earlier attempt that already joined the child needs no second join.

    Args:
        monkeypatch: Replaces the issue-state and lifecycle boundaries.
        tmp_path: Checkout root named by the child envelope.
    """
    monkeypatch.setattr(
        common, "_issue_state", lambda _parent: issue_state(**{CHILD_KEY: {"pending": {}}})
    )
    calls = join_executor(monkeypatch, [])
    common._join_child(child_event(tmp_path), HOST, parent_request(), common.time.monotonic() + 5)
    assert calls == []


def test_join_child_retries_drift_then_raises_the_last_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retry only revision drift or contention, re-reading the revision each time.

    Args:
        monkeypatch: Replaces the issue-state and lifecycle boundaries.
        tmp_path: Checkout root named by the child envelope.
    """
    revisions = iter(range(7, 7 + common.CHILD_JOIN_ATTEMPTS))
    monkeypatch.setattr(
        common, "_issue_state", lambda _parent: {**issue_state(), "revision": next(revisions)}
    )
    # Drift on the first attempt is resolved by the second read.
    calls = join_executor(
        monkeypatch, [{"ok": False, "code": "REVISION_CONFLICT"}, {"ok": True, "code": "JOINED"}]
    )
    common._join_child(child_event(tmp_path), HOST, parent_request(), common.time.monotonic() + 5)
    assert [call["expected_revision"] for call in calls] == [7, 8]
    # Persistent contention exhausts the attempts and surfaces the last code.
    revisions = iter(range(7, 7 + common.CHILD_JOIN_ATTEMPTS))
    calls = join_executor(monkeypatch, [{"ok": False, "code": "BUSY"}] * common.CHILD_JOIN_ATTEMPTS)
    with pytest.raises(core.WorkspaceError) as captured:
        common._join_child(
            child_event(tmp_path), HOST, parent_request(), common.time.monotonic() + 5
        )
    assert captured.value.code == "BUSY"
    assert len(calls) == common.CHILD_JOIN_ATTEMPTS


def test_join_child_stops_at_the_deadline_or_an_unretryable_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A code a fresh revision cannot resolve, or an exhausted budget, ends the join.

    Args:
        monkeypatch: Replaces the issue-state and lifecycle boundaries.
        tmp_path: Checkout root named by the child envelope.
    """
    monkeypatch.setattr(common, "_issue_state", lambda _parent: issue_state())
    # A binding conflict is raised without a retry.
    calls = join_executor(monkeypatch, [{"ok": False, "code": "BINDING_CONFLICT"}])
    with pytest.raises(core.WorkspaceError) as captured:
        common._join_child(
            child_event(tmp_path), HOST, parent_request(), common.time.monotonic() + 5
        )
    assert captured.value.code == "BINDING_CONFLICT"
    assert len(calls) == 1
    # A retryable code after the deadline is raised without another attempt.
    calls = join_executor(monkeypatch, [{"ok": False, "code": "REVISION_CONFLICT"}])
    with pytest.raises(core.WorkspaceError) as captured:
        common._join_child(
            child_event(tmp_path), HOST, parent_request(), common.time.monotonic() - 1
        )
    assert captured.value.code == "REVISION_CONFLICT"
    assert len(calls) == 1


def ready_executor(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Serve scripted read, acknowledge and ready outcomes for the child.

    Args:
        monkeypatch: Replaces the binding and lifecycle boundaries.
        outcomes: Responses returned to successive lifecycle requests.

    Returns:
        The list receiving captured lifecycle requests.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        common,
        "request_for",
        lambda event, operation, host: {
            "operation": operation,
            "host": host,
            "session_id": event["session_id"],
            "binding_generation": 3,
        },
    )

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Return the next scripted outcome for one child request.

        Args:
            request: Lifecycle request under the child binding.

        Returns:
            The scripted response.
        """
        calls.append(request)
        return outcomes.pop(0)

    monkeypatch.setattr(common.core, "execute", execute)
    return calls


def read_result(revision: int) -> dict[str, Any]:
    """Build one successful read result at a revision.

    Args:
        revision: Committed revision returned by the read.

    Returns:
        The read response with a fixed packet digest.
    """
    return {"ok": True, "code": "OK", "revision": revision, "packet_digest": "d" * 64}


def test_ready_child_acknowledges_the_read_revision_then_verifies_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Acknowledge exactly the read revision and digest, then ready the child.

    Args:
        monkeypatch: Replaces the binding and lifecycle boundaries.
        tmp_path: Checkout root named by the child envelope.
    """
    calls = ready_executor(
        monkeypatch, [read_result(9), {"ok": True, "code": "OK"}, {"ok": True, "code": "READY"}]
    )
    assert common._ready_child(child_event(tmp_path), HOST) == "d" * 64
    assert [call["operation"] for call in calls] == ["read", "acknowledge", "ready"]
    assert all(call["session_id"] == CHILD for call in calls)
    assert (calls[1]["expected_revision"], calls[1]["packet_digest"]) == (9, "d" * 64)
    assert len({call["request_id"] for call in calls}) == 3


def test_ready_child_rereads_once_after_acknowledgment_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One revision conflict at acknowledgment triggers exactly one further read.

    Args:
        monkeypatch: Replaces the binding and lifecycle boundaries.
        tmp_path: Checkout root named by the child envelope.
    """
    calls = ready_executor(
        monkeypatch,
        [
            read_result(9),
            {"ok": False, "code": "REVISION_CONFLICT"},
            read_result(10),
            {"ok": True, "code": "OK"},
            {"ok": True, "code": "READY"},
        ],
    )
    assert common._ready_child(child_event(tmp_path), HOST) == "d" * 64
    assert [call["operation"] for call in calls] == [
        "read",
        "acknowledge",
        "read",
        "acknowledge",
        "ready",
    ]
    assert calls[3]["expected_revision"] == 10
    # A second conflict is surfaced rather than retried again.
    calls = ready_executor(
        monkeypatch,
        [
            read_result(9),
            {"ok": False, "code": "REVISION_CONFLICT"},
            read_result(10),
            {"ok": False, "code": "REVISION_CONFLICT"},
        ],
    )
    with pytest.raises(core.WorkspaceError) as captured:
        common._ready_child(child_event(tmp_path), HOST)
    assert captured.value.code == "REVISION_CONFLICT"
    assert [call["operation"] for call in calls] == ["read", "acknowledge", "read", "acknowledge"]


@pytest.mark.parametrize(
    ("outcomes", "code", "operations"),
    [
        pytest.param([{"ok": False, "code": "SOURCE_STALE"}], "SOURCE_STALE", ["read"], id="read"),
        pytest.param(
            [read_result(9), {"ok": False, "code": "PACKET_MISMATCH"}],
            "PACKET_MISMATCH",
            ["read", "acknowledge"],
            id="acknowledge",
        ),
        pytest.param(
            [read_result(9), {"ok": True, "code": "OK"}, {"ok": False, "code": "ACK_REQUIRED"}],
            "ACK_REQUIRED",
            ["read", "acknowledge", "ready"],
            id="ready",
        ),
    ],
)
def test_ready_child_surfaces_each_failed_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcomes: list[dict[str, Any]],
    code: str,
    operations: list[str],
) -> None:
    """A failed read, non-drift acknowledgment or readiness raises its own code.

    Args:
        monkeypatch: Replaces the binding and lifecycle boundaries.
        tmp_path: Checkout root named by the child envelope.
        outcomes: Scripted lifecycle responses.
        code: Diagnostic expected from the failing step.
        operations: Operations expected before the failure stops the route.
    """
    calls = ready_executor(monkeypatch, outcomes)
    with pytest.raises(core.WorkspaceError) as captured:
        common._ready_child(child_event(tmp_path), HOST)
    assert captured.value.code == code
    assert [call["operation"] for call in calls] == operations


def install_child_route(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    ready: dict[str, Any] | None = None,
    pending: dict[str, Any] | None = None,
) -> list[str]:
    """Replace the child route's boundaries around a real temporary binding directory.

    Args:
        monkeypatch: Replaces repository, binding and lifecycle boundaries.
        root: Temporary checkout root.
        ready: Parent readiness response.
        pending: Parent pending tool IDs in committed issue state.

    Returns:
        The list receiving the join and ready steps as they run.
    """
    steps: list[str] = []
    monkeypatch.setattr(common.core, "repository", lambda _path: (root, None, [root]))
    monkeypatch.setattr(
        common,
        "request_for",
        lambda event, operation, _host: {
            **parent_request(operation),
            "session_id": event["session_id"],
        },
    )
    monkeypatch.setattr(
        common.core, "execute", lambda _request: ready or {"ok": True, "code": "READY"}
    )
    monkeypatch.setattr(common, "_issue_state", lambda _parent: issue_state(pending))
    monkeypatch.setattr(common, "_join_child", lambda *_args: steps.append("join"))
    monkeypatch.setattr(common, "_ready_child", lambda *_args: steps.append("ready") or "e" * 64)
    return steps


def assignment(root: Path) -> dict[str, Any] | None:
    """Read the child's recorded issue assignment.

    Args:
        root: Temporary checkout root.

    Returns:
        The assignment record, or None when absent.
    """
    name = CHILD_KEY + ".assignment.json"
    with (
        core.Directory.absolute(root) as worktree,
        worktree.child(".task") as local,
        local.child(".bindings") as bindings,
    ):
        return dict(bindings.json(name)) if bindings.exists(name) else None


def test_child_start_joins_only_for_a_pending_admitted_agent_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Record the child's assignment, join and ready it, and report only identifiers.

    Args:
        monkeypatch: Replaces repository, binding and lifecycle boundaries.
        tmp_path: Temporary checkout root.
    """
    monkeypatch.setattr(common.core, "repository", lambda _path: (tmp_path, None, [tmp_path]))
    common.record_agent_call(parent_event(tmp_path), HOST)
    steps = install_child_route(monkeypatch, tmp_path, pending={"agent-call": {}})
    text = common.child_start(child_event(tmp_path), HOST)
    assert text.startswith(
        f"TASK_WORKSPACE_CHILD_READY: participant {CHILD_KEY}; packet {'e' * 64}."
    )
    assert steps == ["join", "ready"]
    assert assignment(tmp_path) == {"issue_id": "TEST-30"}
    # The same child identity may start again for the same issue.
    assert common.child_start(child_event(tmp_path), HOST) == text
    # A recorded call that is no longer pending does not authorize a join.
    steps = install_child_route(monkeypatch, tmp_path, pending={"other-call": {}})
    with pytest.raises(core.WorkspaceError) as captured:
        common.child_start(child_event(tmp_path), HOST)
    assert captured.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
    assert steps == []


def test_child_start_refuses_unready_parent_and_foreign_assignment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unready parent, a non-child event or a conflicting assignment never joins.

    Args:
        monkeypatch: Replaces repository, binding and lifecycle boundaries.
        tmp_path: Temporary checkout root.
    """
    monkeypatch.setattr(common.core, "repository", lambda _path: (tmp_path, None, [tmp_path]))
    common.record_agent_call(parent_event(tmp_path), HOST)
    # The parent's own binding must be ready.
    steps = install_child_route(
        monkeypatch,
        tmp_path,
        ready={"ok": False, "code": "ACK_REQUIRED"},
        pending={"agent-call": {}},
    )
    with pytest.raises(core.WorkspaceError) as captured:
        common.child_start(child_event(tmp_path), HOST)
    assert captured.value.code == "ACK_REQUIRED"
    # Only a normalized child envelope reaches the parent binding.
    with pytest.raises(core.WorkspaceError) as captured:
        common.child_start({**child_event(tmp_path), "child": False}, HOST)
    assert captured.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
    assert steps == []
    assert assignment(tmp_path) is None
    # A child identity recorded for another issue is never reassigned.
    with (
        core.Directory.absolute(tmp_path) as worktree,
        worktree.child(".task") as local,
        local.child(".bindings") as bindings,
    ):
        bindings.put(CHILD_KEY + ".assignment.json", {"issue_id": "TEST-31"})
    steps = install_child_route(monkeypatch, tmp_path, pending={"agent-call": {}})
    with pytest.raises(core.WorkspaceError) as captured:
        common.child_start(child_event(tmp_path), HOST)
    assert captured.value.code == "BINDING_CONFLICT"
    assert steps == []
    assert assignment(tmp_path) == {"issue_id": "TEST-31"}
