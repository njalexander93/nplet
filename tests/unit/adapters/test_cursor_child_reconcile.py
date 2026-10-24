"""Verify Cursor reconciliation grammar, ownership and bounded internal retries."""

from __future__ import annotations

import shlex
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor_children as children
from nplet.lifecycle import task_workspace as core
from tests.unit.adapters.cursor_children_support import cursor_mock_store, cursor_record

pytestmark = pytest.mark.unit


def test_reconcile_command_round_trips_pinned_macos_spelling() -> None:
    """Recognize the one literal parent command without expanding paths or arguments."""
    token = children.call_token("native\ncall")
    command = children.reconcile_command("TEST-1", token)
    event = {
        "tool_name": "Shell",
        "tool_input": {"command": command, "cwd": "", "timeout": 30000},
        "cwd": str(Path.cwd()),
    }
    assert children.recognize_reconcile(event) == ("TEST-1", token)
    assert "--call" in command
    assert "--result" not in command


@pytest.mark.parametrize(
    "variant", ["extra", "foreign-python", "wrong-mode", "wrong-flag", "newline"]
)
def test_reconcile_command_rejects_direct_malformed_variants(
    monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    """A recognized malformed command cannot silently fall through parent Shell.

    Args:
        monkeypatch: Scoped platform boundary.
        variant: Direct invocation violation.
    """
    token = children.call_token("native")
    argv = [
        children.common.PYTHON,
        str(children.ENTRY),
        "cursor-reconcile",
        "--issue",
        "TEST-1",
        "--call",
        token,
    ]
    if variant == "extra":
        argv.append("--result")
    elif variant == "foreign-python":
        argv[0] = "/foreign/python"
    elif variant == "wrong-mode":
        argv[2] = "retire"
    elif variant == "wrong-flag":
        argv[3] = "--child"
    command = shlex.join(argv) + ("\n" if variant == "newline" else "")
    event = {
        "tool_name": "Shell",
        "tool_input": {"command": command, "cwd": "", "timeout": 30000},
        "cwd": str(Path.cwd()),
    }
    with pytest.raises(core.WorkspaceError):
        children.recognize_reconcile(event)


def test_reconcile_command_preserves_unrelated_shell_and_rejects_cwd_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep ordinary parent Shell while refusing foreign checkout execution.

    Args:
        monkeypatch: Scoped repository and platform boundaries.
    """
    event = {
        "tool_name": "Shell",
        "tool_input": {"command": "python -m pytest -q"},
        "cwd": "/checkout",
    }
    assert children.recognize_reconcile(event) is None
    monkeypatch.setattr(
        children.core, "repository", lambda path: children.core.Repository(Path(path), None, [])
    )
    children.common.carry_repository(event)
    event["tool_input"] = {
        "command": children.reconcile_command("TEST-1", children.call_token("call")),
        "working_directory": "/foreign",
    }
    with pytest.raises(core.WorkspaceError) as error:
        children.recognize_reconcile(event)
    assert error.value.code == "REPOSITORY_MISMATCH"


@pytest.mark.parametrize(
    "alteration", ["foreign-parent", "foreign-generation", "foreign-issue", "unknown", "child"]
)
def test_reconcile_native_authorization_rejects_foreign_or_unknown_calls(
    monkeypatch: pytest.MonkeyPatch, alteration: str
) -> None:
    """A ready native caller may reconcile only its own exact current Task call.

    Args:
        monkeypatch: External readiness/snapshot boundary replacement.
        alteration: Foreign caller or invocation relationship.
    """
    cursor_mock_store(monkeypatch)
    parent = {
        "host": "cursor",
        "session_id": "actual-parent",
        "issue_id": "TEST-1",
        "binding_generation": 7,
    }
    record = cursor_record()
    if alteration == "foreign-parent":
        record["invocation"]["parent"] = "foreign"
    elif alteration == "foreign-generation":
        record["invocation"]["parent_generation"] = 6
    elif alteration == "foreign-issue":
        record["issue_id"] = "TEST-2"
    monkeypatch.setattr(
        children.common, "request_for", lambda *_args: {**parent, "operation": "ready"}
    )
    monkeypatch.setattr(children.core, "execute", lambda _request: {"ok": True, "code": "OK"})
    monkeypatch.setattr(
        children, "issue_records", lambda *_args: [] if alteration == "unknown" else [record]
    )
    event = {"child": alteration == "child"}
    with pytest.raises(core.WorkspaceError) as error:
        children.authorize_reconcile(
            event, "TEST-1", record["invocation"]["tool_id"], time.monotonic() + 1
        )
    assert error.value.code == ("UNKNOWN_OPERATION" if alteration == "unknown" else "NOT_OWNER")


@pytest.mark.parametrize("first_code", ["BUSY", "REVISION_CONFLICT"])
def test_internal_transition_retries_only_real_contention_with_fresh_revision(
    monkeypatch: pytest.MonkeyPatch, first_code: str
) -> None:
    """Refresh transaction identity/revision only after a genuine retryable core result.

    Args:
        monkeypatch: Scoped snapshot/transition boundary replacement.
        first_code: Retryable first transition result.
    """
    _, control = cursor_mock_store(monkeypatch)
    control.json.side_effect = [{"revision": 10}, {"revision": 11}]
    observed = []

    def execute(request: dict) -> dict:
        """Return one real-contention diagnostic followed by success.

        Args:
            request: Internal transition with exact current revision.

        Returns:
            Bounded synthetic transition response.
        """
        observed.append(request)
        return {"ok": len(observed) == 2, "code": "OK" if len(observed) == 2 else first_code}

    monkeypatch.setattr(children.core, "execute_internal", execute)
    result = children.execute_current(
        {"issue_id": "TEST-1", "operation": "cursor-retire"}, time.monotonic() + 1
    )
    assert result["ok"] is True
    assert [request["expected_revision"] for request in observed] == [10, 11]
    assert observed[0]["request_id"] != observed[1]["request_id"]


@pytest.mark.parametrize("code,attempts", [("STALE_BINDING", 1), ("REVISION_CONFLICT", 8)])
def test_internal_transition_stops_on_invalid_identity_or_retry_limit(
    monkeypatch: pytest.MonkeyPatch, code: str, attempts: int
) -> None:
    """Do not turn stale authority into retry success or poll contention indefinitely.

    Args:
        monkeypatch: Scoped snapshot/transition boundary replacement.
        code: Persistent core diagnostic.
        attempts: Exact allowed execution attempts.
    """
    _, control = cursor_mock_store(monkeypatch)
    control.json.return_value = {"revision": 10}
    execute = MagicMock(return_value={"ok": False, "code": code})
    monkeypatch.setattr(children.core, "execute_internal", execute)
    # Eight attempts sleep 0.56s between them; a loaded runner needs headroom beyond one second.
    with pytest.raises(core.WorkspaceError) as error:
        children.execute_current(
            {"issue_id": "TEST-1", "operation": "cursor-retire"}, time.monotonic() + 5
        )
    assert error.value.code == code
    assert execute.call_count == attempts


def test_reconcile_reads_independent_evidence_between_snapshot_and_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Obtain the result independently and pass only its exact projection to core retirement.

    Args:
        monkeypatch: Captures lookup/read/commit ordering without external database access.
    """
    cursor_mock_store(monkeypatch)
    record = cursor_record()
    trace = []
    monkeypatch.setattr(
        children, "registration", lambda _cwd: {"repo_id": "repo", "worktree": "/checkout"}
    )

    def census(_store: object, _deadline: float, select: object, issue: str) -> list:
        """Capture each complete snapshot lookup.

        Args:
            _store: Open registered store.
            _deadline: Shared budget.
            select: Exact adapter-selected predicate.
            issue: Explicit issue selector.

        Returns:
            The selected unique retained record.
        """
        trace.append("issue_records")
        assert issue == "TEST-1"
        assert select(record)
        return [record]

    def read(invocation: dict, _deadline: float) -> dict:
        """Supply a separately obtained bounded terminal projection.

        Args:
            invocation: Exact retained original call.
            _deadline: Shared budget.

        Returns:
            Independent synthetic terminal evidence.
        """
        trace.append("read")
        assert invocation == record["invocation"]
        return {
            "kind": "foreground-success",
            "binary_sha256": "a" * 64,
            "child_conversation": "c11e3a99-d371-410b-a910-378914bdb9a1",
        }

    def transition(request: dict, _deadline: float) -> dict:
        """Capture retirement after independent evidence; core owns transaction revalidation.

        Args:
            request: Adapter-produced internal retirement.
            _deadline: Shared budget.

        Returns:
            Successful synthetic atomic retirement.
        """
        trace.append("retire")
        assert request["session_id"] == "actual-parent"
        assert request["binding_generation"] == 7
        assert request["invocation_id"] == "invocation"
        assert request["result"] == {
            "kind": "foreground-success",
            "binary_sha256": "a" * 64,
            "child_conversation": "c11e3a99-d371-410b-a910-378914bdb9a1",
        }
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(children, "issue_records", census)
    monkeypatch.setattr(children.cursor_results, "read_terminal", read)
    monkeypatch.setattr(children, "execute_current", transition)
    assert children.reconcile(
        "/checkout", "TEST-1", record["invocation"]["tool_id"], time.monotonic() + 1
    )["ok"]
    assert trace == ["issue_records", "read", "retire"]


def test_internal_transition_does_not_start_after_snapshot_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse mutation when opening the current snapshot exhausts the remaining budget.

    Args:
        monkeypatch: Scoped filesystem, clock and mutation boundaries.
    """
    # Simulate a snapshot read consuming the remaining hook time.
    _, control = cursor_mock_store(monkeypatch)
    control.json.return_value = {"revision": 10}
    ticks = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr(children.cursor_results.time, "monotonic", lambda: next(ticks))
    execute = MagicMock(return_value={"ok": True, "code": "OK"})
    monkeypatch.setattr(children.core, "execute_internal", execute)
    # No late transaction may begin even though its revision was obtained successfully.
    with pytest.raises(core.WorkspaceError, match="BUSY"):
        children.execute_current({"issue_id": "TEST-1", "operation": "cursor-retire"}, 1.0)
    execute.assert_not_called()


def test_reconcile_unclosed_quote_denies_instead_of_generic_shell() -> None:
    """Reject a recognizable reconcile entrypoint with broken shell grammar."""
    command = shlex.join([children.common.PYTHON, str(children.ENTRY)]) + " '"
    with pytest.raises(core.WorkspaceError, match="HOST_UNSUPPORTED_TOOL"):
        children.recognize_reconcile(
            {"tool_name": "Shell", "tool_input": {"command": command, "cwd": "", "timeout": 30000}}
        )
