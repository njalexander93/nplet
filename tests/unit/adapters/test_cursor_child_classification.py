"""Verify exact Cursor identity classification and planned native admission."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor_children as children
from nplet.lifecycle import task_workspace as core
from tests.unit.adapters.cursor_children_support import cursor_mock_store, cursor_record

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("known_child", [False, True])
def test_normalize_checks_retained_index_even_without_mutex_lock(
    monkeypatch: pytest.MonkeyPatch, known_child: bool
) -> None:
    """Missing mutex synchronization never hides an indexed child or grants a parent shortcut.

    Args:
        monkeypatch: Replaces persisted snapshot and native identity boundaries.
        known_child: Whether the retained index names this actual conversation.
    """
    store, _control = cursor_mock_store(monkeypatch)
    mutex = store.control.child.return_value.__enter__.return_value
    mutex.exists.side_effect = lambda name: name == "index.json"
    key = core.participant_key(
        {"host": "cursor", "session_id": "actual" if known_child else "other"}
    )
    mutex.json.return_value = {"schema_version": 2, "children": {key: {"status": "closed"}}}
    monkeypatch.setattr(
        children.common, "native_identity", lambda raw, _host: {**raw, "session_id": "actual"}
    )
    monkeypatch.setattr(children, "registration", lambda _cwd: {})
    monkeypatch.setattr(children, "issue_records", lambda *_args: [])
    with pytest.raises(
        core.WorkspaceError,
        match="HOST_UNSUPPORTED_CHILD_IDENTITY" if known_child else "RECOVERY_REQUIRED",
    ):
        children.normalize({"cwd": "checkout"}, time.monotonic() + 1)
    mutex.lock.assert_not_called()


@pytest.mark.parametrize("raw", ["call", "call\nfc", "x" * 160, "é" * 80])
def test_call_token_preserves_exact_domain_separated_bytes(raw: str) -> None:
    """Use the verified namespace and preserve every native newline/UTF8byte.

    Args:
        raw: Valid raw opaque identity.
    """
    expected = "cursor-call-v1:" + hashlib.sha256(b"cursor-call-v1\0" + raw.encode()).hexdigest()
    assert children.call_token(raw) == expected


@pytest.mark.parametrize("raw", [None, b"call", "", "x" * 161, "é" * 81, "\ud800", "nul\0call"])
def test_call_token_rejects_invalid_native_identity(raw: object) -> None:
    """Refuse coercion, lossy encoding and oversized native identity.

    Args:
        raw: Invalid host identity.
    """
    with pytest.raises(core.WorkspaceError) as error:
        children.call_token(raw)
    assert error.value.code == "INVALID_REQUEST"


def test_unique_lookup_refuses_unknown_or_ambiguous_record() -> None:
    """Unknown and ambiguous correlation never select the first admitted child."""
    assert children.one([{"id": "only"}]) == {"id": "only"}
    for records, code in (([], "UNKNOWN_OPERATION"), ([{}, {}], "REQUEST_CONFLICT")):
        with pytest.raises(core.WorkspaceError) as error:
            children.one(records)
        assert error.value.code == code


def test_registration_discovery_never_invents_participant_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve repository identity without copying or manufacturing a parent session.

    Args:
        monkeypatch: Captures read-only registration request.
    """
    observed = []

    def execute(request: dict) -> dict:
        """Record the exact participant-free registration lookup.

        Args:
            request: Read-only registration request.

        Returns:
            Successful synthetic repository identity.
        """
        observed.append(request)
        return {"ok": True, "code": "REGISTERED", "repo_id": "repo"}

    monkeypatch.setattr(children.core, "execute", execute)
    result = children.registration("/checkout")
    assert result["repo_id"] == "repo"
    assert observed[0]["operation"] == "diagnose"
    assert "session_id" not in observed[0]
    assert "host" not in observed[0]


def test_parent_request_uses_committed_actual_parent_generation() -> None:
    """Build internal parent requests from retained identity without caller-supplied results."""
    record = {
        "issue_id": "TEST-1",
        "issue_uuid": "issue-uuid",
        "invocation": {"parent_session": "actual-parent", "parent_generation": 7},
    }
    store = SimpleNamespace(root=Path("/checkout"), registration={"repo_id": "repo"})
    result = children.parent_request(store, record)
    assert result["session_id"] == "actual-parent"
    assert result["binding_generation"] == 7
    assert result["host"] == "cursor"
    assert result["repo_id"] == "repo"
    assert "result" not in result


@pytest.mark.parametrize(
    "marker,phase,allowed",
    [
        (True, "admitted", True),
        (False, "bound", True),
        (False, "retired", False),
        (True, "retired", False),
    ],
)
def test_normalize_keeps_genuine_and_omitted_marker_child_classification(
    monkeypatch: pytest.MonkeyPatch, marker: bool, phase: str, allowed: bool
) -> None:
    """Bind genuine actual UUIDs and prevent omitted markers from restoring parent authority.

    Args:
        monkeypatch: External snapshot/core boundary replacement.
        marker: Whether callback retains the native parent call marker.
        phase: Retained current invocation phase.
        allowed: Whether the current generation can remain bound.
    """
    cursor_mock_store(monkeypatch)
    record = cursor_record(phase)
    event = {
        "conversation_id": record["invocation"]["child_conversation"],
        "generation_id": "native-child-generation",
        "cwd": "/checkout",
        "tool_use_id": "actual-child-call",
    }
    if marker:
        event["parent_tool_call_id"] = record["invocation"]["native_call"]
    monkeypatch.setattr(
        children.common,
        "native_identity",
        lambda raw, _host: {**raw, "session_id": raw["conversation_id"]},
    )
    monkeypatch.setattr(
        children, "registration", lambda _cwd: {"worktree": "/checkout", "repo_id": "repo"}
    )
    monkeypatch.setattr(
        children,
        "issue_records",
        lambda _store, _deadline, select, issue=None: (
            [record] if issue in {None, "TEST-1"} and select(record) else []
        ),
    )
    observed = []

    def transition(request: dict, _deadline: float) -> dict:
        """Capture the genuine child bind and return its independent generation.

        Args:
            request: Exact adapter-generated transition.
            _deadline: Shared hook deadline.

        Returns:
            Successful synthetic bind.
        """
        observed.append(request)
        return {"ok": True, "code": "OK", "binding_generation": 1}

    monkeypatch.setattr(children, "execute_current", transition)
    if not allowed:
        with pytest.raises(core.WorkspaceError) as error:
            children.normalize(event, time.monotonic() + 1)
        assert error.value.code == "STALE_BINDING"
        assert observed == []
    else:
        result = children.normalize(event, time.monotonic() + 1)
        assert result["child"] is True
        assert result["session_id"] == "actual-parent/agent/" + event["conversation_id"]
        assert observed[0]["operation"] == "cursor-bind-child"
        assert observed[0]["tool_id"] == record["invocation"]["tool_id"]
        assert observed[0]["native_generation"] == "native-child-generation"
        assert result["tool_use_id"] == children.call_token("actual-child-call")


@pytest.mark.parametrize("resume", [None, "c11e3a99-d371-410b-a910-378914bdb9a1"])
@pytest.mark.parametrize("replay", [False, True])
def test_task_admission_consumes_only_exact_ready_parent_plan(
    monkeypatch: pytest.MonkeyPatch,
    resume: str | None,
    replay: bool,
) -> None:
    """Reserve one native call before allow and keep model choice in the exact planned input.

    Args:
        monkeypatch: Scoped external boundaries around real argument/digest validation.
        resume: Source-backed optional native resume UUID, or fresh invocation.
        replay: Existing call retry with a second identical unconsumed plan.
    """
    _, control = cursor_mock_store(monkeypatch)
    args = {
        "description": "scope",
        "environment": "local",
        "model": "selected-model",
        "prompt": "bounded work",
        "run_in_background": False,
        "subagent_type": "generalPurpose",
    }
    if resume is not None:
        args["resume"] = resume
    parent = {
        "host": "cursor",
        "session_id": "actual-parent",
        "issue_id": "TEST-1",
        "binding_generation": 7,
    }
    plan = {
        "parent": core.participant_key(parent),
        "parent_generation": 7,
        "task_digest": core.sha(core.canonical(args)),
        "invocation_id": None,
        "resume_child": resume,
    }
    native = "parent-native\ncall"
    state = {"revision": 12, "issue_uuid": "issue-uuid", "cursor_plans": {"plan": plan}}
    # A new identical task must not make a previously admitted callback ambiguous.
    if replay:
        state["cursor_plans"]["next-plan"] = dict(plan)
        plan["invocation_id"] = "invocation"
        state["cursor_invocations"] = {
            "invocation": {"tool_id": children.call_token(native), "native_call": native}
        }
    state["cursor_plans"]["foreign-parent-plan"] = {**plan, "parent": "foreign-parent"}
    state["cursor_plans"]["foreign-generation-plan"] = {**plan, "parent_generation": 6}
    # Another consumed call from this parent must not hijack fresh admission or exact replay.
    state["cursor_plans"]["other-call-plan"] = {**plan, "invocation_id": "other-invocation"}
    state.setdefault("cursor_invocations", {})["other-invocation"] = {
        "tool_id": children.call_token("other-native-call"),
        "native_call": "other-native-call",
    }
    control.json.return_value = state
    monkeypatch.setattr(
        children, "registration", lambda _cwd: {"repo_id": "repo", "worktree": "/checkout"}
    )
    monkeypatch.setattr(
        children.common, "request_for", lambda *_args: {**parent, "operation": "ready"}
    )
    monkeypatch.setattr(children.core, "execute", lambda _request: {"ok": True, "code": "OK"})
    pin = MagicMock(return_value={})
    monkeypatch.setattr(children.cursor_results, "pinned_sources", pin)
    transition = MagicMock(return_value={"ok": True, "code": "OK", "invocation_id": "invocation"})
    monkeypatch.setattr(children, "execute_current", transition)
    native = "parent-native\ncall"
    event = {
        "tool_name": "Task",
        "tool_input": args,
        "cwd": "/checkout",
        "conversation_id": "actual-parent",
        "tool_use_id": children.call_token(native),
        "_cursor_native_call": native,
    }
    assert children.admit(event, time.monotonic() + 1)["invocation_id"] == "invocation"
    sent = transition.call_args.args[0]
    assert sent["operation"] == "cursor-admit"
    assert sent["plan_id"] == "plan"
    assert sent["native_call"] == native
    assert sent["task_digest"] == core.sha(core.canonical(args))
    pin.assert_called_once()
    # A different planned child cannot be substituted under the same exact Task digest.
    plan["resume_child"] = "foreign-child"
    with pytest.raises(core.WorkspaceError) as error:
        children.admit(event, time.monotonic() + 1)
    assert error.value.code == "REQUEST_CONFLICT"
    plan["resume_child"] = resume
    # A mismatched planned digest creates no new invocation or native launch permission.
    plan["task_digest"] = "0" * 64
    with pytest.raises(core.WorkspaceError) as error:
        children.admit(event, time.monotonic() + 1)
    assert error.value.code == ("REQUEST_CONFLICT" if replay else "SCOPE_MISSING")
    assert transition.call_count == 1


@pytest.mark.parametrize(
    "alteration", ["nested", "background", "remote", "unknown-field", "invalid-model"]
)
def test_task_admission_rejects_unsupported_native_shape_before_pin_or_store(
    monkeypatch: pytest.MonkeyPatch, alteration: str
) -> None:
    """Reject unsupported launch shapes before any side effect or source/database read.

    Args:
        monkeypatch: Enforces no pin/store work on a denied native launch.
        alteration: Native shape violation.
    """
    args = {
        "description": "scope",
        "environment": "local",
        "model": "selected-model",
        "prompt": "bounded work",
        "run_in_background": False,
        "subagent_type": "generalPurpose",
    }
    event = {"tool_name": "Task", "tool_input": args, "child": alteration == "nested"}
    if alteration == "background":
        args["run_in_background"] = True
    elif alteration == "remote":
        args["environment"] = "remote"
    elif alteration == "unknown-field":
        args["isolation"] = "worktree"
    elif alteration == "invalid-model":
        args["model"] = 1
    monkeypatch.setattr(
        children.cursor_results,
        "pinned_sources",
        lambda _deadline: pytest.fail("pin after denied shape"),
    )
    monkeypatch.setattr(
        children, "registration", lambda _cwd: pytest.fail("store after denied shape")
    )
    with pytest.raises(core.WorkspaceError):
        children.admit(event, time.monotonic() + 1)


@pytest.mark.parametrize(
    "resume", ["", None, 1, "not-a-uuid", "z" * 36, "C11E3A99-D371-410B-A910-378914BDB9A1"]
)
def test_task_resume_rejects_unproven_or_invalid_identity_before_pin(
    monkeypatch: pytest.MonkeyPatch, resume: object
) -> None:
    """Resume is optional; when present it must identify the exact canonical retained UUID.

    Args:
        monkeypatch: Ensures no source/store effects occur after invalid shape.
        resume: Invalid provided resume identity.
    """
    args = {
        "description": "scope",
        "environment": "local",
        "model": "selected-model",
        "prompt": "bounded work",
        "run_in_background": False,
        "subagent_type": "generalPurpose",
        "resume": resume,
    }
    monkeypatch.setattr(
        children.cursor_results,
        "pinned_sources",
        lambda _deadline: pytest.fail("pin after invalid resume"),
    )
    with pytest.raises(core.WorkspaceError) as error:
        children.admit({"tool_name": "Task", "tool_input": args}, time.monotonic() + 1)
    assert error.value.code == "HOST_UNSUPPORTED_RESUME_SHAPE"


@pytest.mark.parametrize(
    "registered,parent_call,code",
    [(False, None, None), (False, "native-call", "REGISTRATION_REQUIRED"), (True, None, None)],
)
def test_normalize_preserves_only_unclassified_parent_startup(
    monkeypatch: pytest.MonkeyPatch, registered: bool, parent_call: str | None, code: str | None
) -> None:
    """Keep parent startup available without admitting an unknown marked child.

    Args:
        monkeypatch: Scoped registration and snapshot boundaries.
        registered: Whether repository registration already exists.
        parent_call: Actual native child marker, if supplied.
        code: Expected denial for an unregistered child.
    """
    # Supply native identity without child classification or tool metadata.
    cursor_mock_store(monkeypatch)
    event = {"conversation_id": "parent", "cwd": "/checkout", "_cursor_invocation": "forged"}
    if parent_call is not None:
        event["parent_tool_call_id"] = parent_call
    monkeypatch.setattr(
        children.common,
        "native_identity",
        lambda raw, _host: {**raw, "session_id": raw["conversation_id"]},
    )

    def registration(_cwd: str) -> dict:
        """Return registration or its real missing-registration diagnostic.

        Args:
            _cwd: Observed checkout.

        Returns:
            Registered store input.

        Raises:
            core.WorkspaceError: Registration has not occurred.
        """
        if not registered:
            raise core.WorkspaceError("REGISTRATION_REQUIRED")
        return {"repo_id": "repo", "worktree": "/checkout"}

    monkeypatch.setattr(children, "registration", registration)
    monkeypatch.setattr(children.routing, "lookup", lambda *_args: None)
    census = MagicMock(return_value=[])
    monkeypatch.setattr(children, "issue_records", census)
    # No fabricated private marker survives the parent route.
    if code:
        with pytest.raises(core.WorkspaceError, match=code):
            children.normalize(event, time.monotonic() + 1)
    else:
        observed = children.normalize(event, time.monotonic() + 1)
        assert observed == {"conversation_id": "parent", "session_id": "parent", "cwd": "/checkout"}
    census.assert_not_called()


@pytest.mark.parametrize("conversation", ["not-a-uuid", "z" * 36])
def test_normalize_rejects_malformed_actual_child_before_bind(
    monkeypatch: pytest.MonkeyPatch, conversation: str
) -> None:
    """Refuse malformed child identity despite an exact raw parent call.

    Args:
        monkeypatch: Replaces store and native identity boundaries.
        conversation: Invalid actual child conversation.
    """
    cursor_mock_store(monkeypatch)
    record = cursor_record("admitted")
    monkeypatch.setattr(
        children.common, "native_identity", lambda raw, _host: {**raw, "session_id": conversation}
    )
    monkeypatch.setattr(
        children, "registration", lambda _cwd: {"worktree": "/checkout", "repo_id": "repo"}
    )
    monkeypatch.setattr(
        children,
        "issue_records",
        lambda _store, _deadline, select, issue=None: (
            [record] if issue in {None, "TEST-1"} and select(record) else []
        ),
    )
    transition = MagicMock()
    monkeypatch.setattr(children, "execute_current", transition)
    event = {
        "cwd": "/checkout",
        "conversation_id": conversation,
        "parent_tool_call_id": record["invocation"]["native_call"],
    }
    with pytest.raises(core.WorkspaceError, match="HOST_UNSUPPORTED_CHILD_IDENTITY"):
        children.normalize(event, time.monotonic() + 1)
    transition.assert_not_called()


@pytest.mark.parametrize(
    "legacy",
    [
        [],
        {"schema_version": 1, "children": {}},
        {"schema_version": 2, "children": []},
        {"schema_version": 2, "children": {"bad-key": {}}},
        {"schema_version": 2, "children": {"a" * 64: None}},
    ],
)
def test_malformed_retained_index_cannot_allow_parent_fallback(
    monkeypatch: pytest.MonkeyPatch, legacy: object
) -> None:
    """Reject malformed retained identity controls before discovery or new authority.

    Args:
        monkeypatch: Isolates the retained filesystem snapshot boundary.
        legacy: Invalid persisted child-index structure.
    """
    store, _ = cursor_mock_store(monkeypatch)
    mutex = store.control.child.return_value.__enter__.return_value
    mutex.exists.side_effect = None
    mutex.exists.return_value = True
    mutex.json.return_value = legacy
    monkeypatch.setattr(children, "registration", lambda _cwd: {"repo_id": "repo"})
    monkeypatch.setattr(
        children.common,
        "native_identity",
        lambda event, _host: {**event, "session_id": event["conversation_id"]},
    )
    census = MagicMock()
    monkeypatch.setattr(children, "issue_records", census)
    # A bad index is not equivalent to an absent index or an unknown parent.
    with pytest.raises(core.WorkspaceError, match="INTEGRITY_ERROR"):
        children.normalize({"conversation_id": "parent", "cwd": "/checkout"}, time.monotonic() + 1)
    census.assert_not_called()
    mutex.put.assert_not_called()


def test_normalize_retries_when_admission_publishes_mutex_during_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An initially empty namespace cannot bypass a concurrently published admission.

    Args:
        monkeypatch: Simulate namespace publication between census and parent fallback.
    """
    # Admission publishes its mutex before any child invocation can become visible.
    store, _ = cursor_mock_store(monkeypatch)
    store.control.exists.side_effect = [False, False, True]
    monkeypatch.setattr(children, "registration", lambda _cwd: {"repo_id": "repo"})
    monkeypatch.setattr(
        children.common,
        "native_identity",
        lambda event, _host: {**event, "session_id": event["conversation_id"]},
    )
    monkeypatch.setattr(children, "issue_records", lambda *_args: [])
    # Retry a serialized classification instead of allowing an unverified parent.
    with pytest.raises(core.WorkspaceError, match="BUSY"):
        children.normalize({"conversation_id": "parent", "cwd": "/checkout"}, time.monotonic() + 1)
    store.control.child.assert_not_called()
