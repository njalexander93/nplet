"""Exercise exact Cursor correlation and command grammar without external stores or apps."""

from __future__ import annotations

import hashlib
import runpy
import shlex
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor_children as children
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "issues_present,mismatch", [(False, ""), (True, ""), (True, "main"), (True, "control")]
)
def test_classification_snapshot_opens_only_verified_existing_controls(
    monkeypatch: pytest.MonkeyPatch, issues_present: bool, mismatch: str
) -> None:
    """Read canonical controls without creating local bindings or trusting changed identity.

    Args:
        monkeypatch: Replaces safe directory handles and Git identity discovery.
        issues_present: Whether the registered repository already has an issue collection.
        mismatch: Canonical main identity or control registration mismatch.
    """
    # Model only the external directory graph; exercise the actual context and identity checks.
    root = Path("checkout")
    registered = {"repo_id": "repo", "main": "canonical", "main_identity": "main-identity"}
    local_root, local, main, task, control, issues = [MagicMock() for _ in range(6)]
    local_root.child.return_value = nullcontext(local)
    local.json.return_value = registered
    main.child.return_value = nullcontext(task)
    main.identity = "foreign" if mismatch == "main" else "main-identity"
    task.child.return_value = nullcontext(control)
    control.json.return_value = {} if mismatch == "control" else registered
    control.exists.return_value = issues_present
    control.child.return_value = nullcontext(issues)
    monkeypatch.setattr(children.core, "repository", lambda _path: (root, "common", ["canonical"]))
    validate = MagicMock()
    monkeypatch.setattr(children.core, "_validate_registration_identity", validate)
    monkeypatch.setattr(
        children.core.Directory,
        "absolute",
        lambda path: nullcontext(local_root if path == root else main),
    )
    # Failed boundaries must not yield usable controls; success preserves optional absent storage.
    if mismatch:
        with pytest.raises(core.WorkspaceError, match="REPOSITORY_MISMATCH"):
            with children.classification_snapshot({"worktree": "checkout", "repo_id": "repo"}):
                pytest.fail("yielded foreign controls")
    else:
        with children.classification_snapshot(
            {"worktree": "checkout", "repo_id": "repo"}
        ) as snapshot:
            assert snapshot.root == root
            assert snapshot.registration == registered
            assert snapshot.control is control
            assert snapshot.issues is (issues if issues_present else None)
        local_root.child.assert_called_once_with(".task")
        main.child.assert_called_once_with(".task")
        task.child.assert_called_once_with(".control")
        assert control.child.call_args_list == ([(("issues",), {})] if issues_present else [])
    validate.assert_called_once_with(registered, "common", ["canonical"])


def test_census_absent_collection_and_unselected_records_do_not_invent_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty discovery stays empty while explicit issue lookup requires existing storage.

    Args:
        monkeypatch: Replaces the safe issue snapshot boundary.
    """
    # An absent collection is valid for discovery, never for a caller-selected issue.
    absent = SimpleNamespace(issues=None)
    assert children.census(absent, time.monotonic() + 1, lambda _record: True) == []
    with pytest.raises(core.WorkspaceError, match="UNKNOWN_OPERATION"):
        children.census(absent, time.monotonic() + 1, lambda _record: True, "TEST-1")
    # A complete snapshot can contain only unrelated calls; do not select its first record.
    store, control = cursor_mock_store(monkeypatch)
    record = cursor_record()
    control.read.return_value = core.canonical(
        {
            "issue_id": "TEST-1",
            "issue_uuid": "uuid",
            "revision": 1,
            "participants": {},
            "cursor_invocations": {"invocation": record["invocation"]},
        }
    )
    assert children.census(store, time.monotonic() + 1, lambda _record: False) == []
    control.lock.assert_called_once_with(create=False)


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
    monkeypatch.setattr(children, "census", lambda *_args: [])
    with pytest.raises(
        core.WorkspaceError, match="HOST_UNSUPPORTED_CHILD_IDENTITY" if known_child else "BUSY"
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
    monkeypatch.setattr(children.core, "repository", lambda path: (Path(path), None, []))
    event["tool_input"] = {
        "command": children.reconcile_command("TEST-1", children.call_token("call")),
        "working_directory": "/foreign",
    }
    with pytest.raises(core.WorkspaceError) as error:
        children.recognize_reconcile(event)
    assert error.value.code == "REPOSITORY_MISMATCH"


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


def cursor_record(phase: str = "bound") -> dict:
    """Supply one coherent committed invocation record for mocked adapter boundaries.

    Args:
        phase: Lifecycle invocation phase to expose.

    Returns:
        Metadata-only record with exact native call, latest member pointer and parent.
    """
    native = "native\nparent-call"
    invocation = {
        "native_call": native,
        "tool_id": children.call_token(native),
        "phase": phase,
        "parent_session": "actual-parent",
        "parent_conversation": "93ea96d2-93ce-4359-b562-65c859fb042f",
        "parent_generation": 7,
        "parent": core.participant_key({"host": "cursor", "session_id": "actual-parent"}),
        "child_conversation": "c11e3a99-d371-410b-a910-378914bdb9a1",
        "child_participant": "child-key",
        "native_generation": "native-child-generation",
    }
    return {
        "invocation_id": "invocation",
        "invocation": invocation,
        "issue_id": "TEST-1",
        "issue_uuid": "issue-uuid",
        "revision": 12,
        "member": {"cursor_invocation": "invocation", "generation": 1},
    }


def cursor_mock_store(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Replace only the external Store/Directory boundary with scoped context managers.

    Args:
        monkeypatch: Scoped constructor replacement.

    Returns:
        Mock registered store and one opened issue control.
    """
    store = MagicMock()
    store.root = Path("/checkout")
    store.registration = {"repo_id": "repo"}
    store.issues.names.return_value = ["TEST-1"]
    control = MagicMock()
    control.lock.return_value = nullcontext()
    control.exists.return_value = False
    store.issues.child.return_value.__enter__.return_value = control
    mutex = MagicMock()
    mutex.lock.return_value = nullcontext()
    mutex.exists.side_effect = lambda name: name == "lock"
    store.control.child.return_value.__enter__.return_value = mutex
    manager = MagicMock()
    manager.__enter__.return_value = store
    monkeypatch.setattr(children.core, "Store", lambda _request: manager)
    monkeypatch.setattr(children, "classification_snapshot", lambda _request: nullcontext(store))
    return store, control


@pytest.mark.parametrize(
    "alteration,code",
    [
        ("", None),
        ("unfinished", "RECOVERY_REQUIRED"),
        ("many-issues", "SIZE_LIMIT"),
        ("bytes", "SIZE_LIMIT"),
        ("bad-call", "INTEGRITY_ERROR"),
    ],
)
def test_census_is_complete_bounded_and_reads_only_issue_state(
    monkeypatch: pytest.MonkeyPatch, alteration: str, code: str | None
) -> None:
    """Do not return partial lookup when any registered issue is unsafe or unbounded.

    Args:
        monkeypatch: External filesystem boundary replacement.
        alteration: Selected census invariant violation.
        code: Expected bounded failure, or None for the normal snapshot.
    """
    store, control = cursor_mock_store(monkeypatch)
    record = cursor_record()
    state = {
        "issue_id": "TEST-1",
        "issue_uuid": "issue-uuid",
        "revision": 12,
        "participants": {"child-key": record["member"]},
        "cursor_invocations": {"invocation": record["invocation"]},
    }
    if alteration == "unfinished":
        control.exists.side_effect = lambda name: name == "transaction.json"
    elif alteration == "many-issues":
        store.issues.names.return_value = ["TEST-1"] * (children.CENSUS_ISSUES + 1)
    elif alteration == "bytes":
        monkeypatch.setattr(children, "CENSUS_BYTES", 1)
    elif alteration == "bad-call":
        record["invocation"]["tool_id"] = "wrong"
    control.read.return_value = core.canonical(state)
    if code is not None:
        with pytest.raises(core.WorkspaceError) as error:
            children.census(store, time.monotonic() + 1, lambda _record: True)
        assert error.value.code == code
    else:
        assert children.census(store, time.monotonic() + 1, lambda _record: True) == [record]
        control.read.assert_called_once_with("state.json")
        store.issues.child.assert_called_once_with("TEST-1")


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
        "census",
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
        children, "census", lambda *_args: [] if alteration == "unknown" else [record]
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

    monkeypatch.setattr(children.core, "execute", execute)
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
    monkeypatch.setattr(children.core, "execute", execute)
    with pytest.raises(core.WorkspaceError) as error:
        children.execute_current(
            {"issue_id": "TEST-1", "operation": "cursor-retire"}, time.monotonic() + 1
        )
    assert error.value.code == code
    assert execute.call_count == attempts


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
    state = {"revision": 12, "cursor_plans": {"plan": plan}}
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
        trace.append("census")
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
        return {"kind": "foreground-success", "binary_sha256": "a" * 64}

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
        assert request["result"] == {"kind": "foreground-success", "binary_sha256": "a" * 64}
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(children, "census", census)
    monkeypatch.setattr(children.cursor_results, "read_terminal", read)
    monkeypatch.setattr(children, "execute_current", transition)
    assert children.reconcile(
        "/checkout", "TEST-1", record["invocation"]["tool_id"], time.monotonic() + 1
    )["ok"]
    assert trace == ["census", "read", "retire"]


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
    census = MagicMock(return_value=[])
    monkeypatch.setattr(children, "census", census)
    # No fabricated private marker survives the parent route.
    if code:
        with pytest.raises(core.WorkspaceError, match=code):
            children.normalize(event, time.monotonic() + 1)
    else:
        observed = children.normalize(event, time.monotonic() + 1)
        assert observed == {"conversation_id": "parent", "session_id": "parent", "cwd": "/checkout"}
    assert census.call_count == int(registered)


@pytest.mark.parametrize("outcome", ["success", "invalid-arguments", "unavailable", "unexpected"])
def test_reconcile_cli_exposes_only_bounded_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], outcome: str
) -> None:
    """Keep the public CLI free of caller-selected result evidence and exception details.

    Args:
        monkeypatch: Scoped process and reconciliation boundaries.
        capsys: Captured bounded JSON output.
        outcome: Selected success or failure boundary.
    """
    # Accept only the exact public command and derive the checkout from actual cwd.
    token = children.call_token("native-call")
    argv = ["cursor_children.py", "cursor-reconcile", "--issue", "TEST-1", "--call", token]
    if outcome == "invalid-arguments":
        argv += ["--result", "forged"]
    monkeypatch.setattr(children.sys, "argv", argv)
    reconcile = MagicMock(return_value={"ok": True, "code": "OK"})
    if outcome == "unavailable":
        reconcile.side_effect = core.WorkspaceError("RESULT_NOT_AVAILABLE")
    elif outcome == "unexpected":
        reconcile.side_effect = OSError("private-storage-detail")
    monkeypatch.setattr(children, "reconcile", reconcile)
    # Return bounded diagnostics while withholding internal exception text.
    result = children.main()
    expected = {
        "success": "OK",
        "invalid-arguments": "INVALID_REQUEST",
        "unavailable": "RESULT_NOT_AVAILABLE",
        "unexpected": "RECOVERY_REQUIRED",
    }[outcome]
    assert result == (0 if outcome == "success" else 3)
    assert (
        capsys.readouterr().out
        == core.canonical({"ok": outcome == "success", "code": expected}).decode() + "\n"
    )
    if outcome != "invalid-arguments":
        assert reconcile.call_args.args[:3] == (str(Path.cwd()), "TEST-1", token)
    else:
        reconcile.assert_not_called()


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
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(children.cursor_results.time, "monotonic", lambda: next(ticks))
    execute = MagicMock(return_value={"ok": True, "code": "OK"})
    monkeypatch.setattr(children.core, "execute", execute)
    # No late transaction may begin even though its revision was obtained successfully.
    with pytest.raises(core.WorkspaceError, match="BUSY"):
        children.execute_current({"issue_id": "TEST-1", "operation": "cursor-retire"}, 1.0)
    execute.assert_not_called()


def test_source_content_hashes_actual_utf8_and_js_utf16_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash callback text itself and persist only exact bounded metadata.

    Args:
        monkeypatch: Replaces only the core source transition boundary.
    """
    transition = MagicMock(return_value={"source_content_verified": True, "source_receipt": False})
    monkeypatch.setattr(children, "source_transition", transition)
    text = "café\n😀\n"
    event = {"child": True, "content": text, "file_path": "/source", "attachments": []}
    result = children.source_read_content(event, time.monotonic() + 1)
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
    monkeypatch.setattr(children, "source_transition", transition)
    with pytest.raises(core.WorkspaceError):
        children.source_read_content(
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
    monkeypatch.setattr(children, "source_transition", transition)
    event = {
        "child": True,
        "tool_name": "Read",
        "tool_input": {"file_path": "/source"},
        "tool_output": output,
    }
    with pytest.raises(core.WorkspaceError):
        children.source_read_post(event, time.monotonic() + 1, False)
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
    monkeypatch.setattr(children, "source_transition", transition)
    event = {
        "child": True,
        "tool_name": "Read",
        "tool_input": {"file_path": "/source"},
        "tool_output": '{"file_path":"/source","content_length":8}',
        "error_message": "denied",
        "failure_type": "permission_denied",
    }
    children.source_read_post(event, time.monotonic() + 1, failed)
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

    monkeypatch.setattr(children.core, "execute", execute)
    children.source_transition(
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
    monkeypatch.setattr(children.core, "repository", lambda path: (Path(path), None, []))
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
    assert children.child_lifecycle(event) is True
    assert children.child_lifecycle({"tool_name": "Read"}) is False


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
    monkeypatch.setattr(children.core, "repository", lambda path: (Path(path), None, []))
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
        children.child_lifecycle(event)
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
    assert "canonical-own-command" in children.child_context({"child": True})
    request = command.call_args.args[0]
    assert request["session_id"] == "actual-parent/agent/actual-child"
    assert request["binding_generation"] == 3
    assert not ({"issue_uuid", "participant_id", "cursor_invocation"} & set(request))


@pytest.mark.parametrize(
    "operation",
    ["cursor-admit", "cursor-bind-child", "cursor-retire", "cursor-source-callback", {}, []],
)
def test_public_cli_rejects_internal_or_malformed_operation_before_execute(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: object
) -> None:
    """Public CLI entry points cannot mint internal host evidence or crash on malformed operation.

    Args:
        monkeypatch: Replaces argv and enforces no core dispatch.
        capsys: Captures canonical public diagnostics.
        operation: Reserved internal operation or malformed nonstring.
    """
    import base64
    import json

    from nplet.adapters import bootstrap

    request = {
        "schema_version": 1,
        "operation": operation,
        "request_id": "request",
        "worktree": "/checkout",
    }
    monkeypatch.setattr(core, "execute", lambda _request: pytest.fail("public internal dispatch"))
    expected = "HOST_UNSUPPORTED_TOOL" if isinstance(operation, str) else "INVALID_REQUEST"
    monkeypatch.setattr(
        core.sys, "argv", ["task_workspace.py", "--request-json", json.dumps(request)]
    )
    assert core.main() == 3
    assert json.loads(capsys.readouterr().out)["code"] == expected
    encoded = base64.urlsafe_b64encode(json.dumps(request).encode()).decode()
    monkeypatch.setattr(bootstrap.sys, "argv", ["bootstrap.py", "--request-base64", encoded])
    assert bootstrap.main() == 3
    assert json.loads(capsys.readouterr().out)["code"] == expected


@pytest.mark.parametrize(
    "operation", ["cursor-admit", "cursor-bind-child", "cursor-retire", "cursor-source-callback"]
)
def test_native_internal_request_denies_before_shell_bootstrap_fallback(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """A parser false result cannot grant generic Shell execution of a reserved core request.

    Args:
        monkeypatch: Ensures denial precedes bootstrap/readiness/pending mutation.
        operation: Exact reserved internal operation.
    """
    request = {
        "schema_version": 1,
        "operation": operation,
        "request_id": "request",
        "worktree": "/checkout",
        "host": "cursor",
        "session_id": "observed",
    }
    command = children.common.bootstrap_command(request, "cursor")
    event = {
        "session_id": "observed",
        "cwd": "/checkout",
        "tool_use_id": "tool",
        "tool_name": "Shell",
        "tool_input": {"command": command, "cwd": "", "timeout": 30000},
    }
    monkeypatch.setattr(
        children.common, "native_bootstrap", lambda *_args, **_kwargs: pytest.fail("fallback")
    )
    with pytest.raises(core.WorkspaceError) as error:
        children.common.native_pre(event, "cursor")
    assert error.value.code == "HOST_UNSUPPORTED_TOOL"
    assert children.common.canonical_bootstrap(event, command, "cursor", ready=True) is False


@pytest.mark.parametrize(
    "argv,mode",
    [
        (["cursor.py"], "normal"),
        (["cursor.py", "--observe-read-content"], "observer"),
        (["cursor.py", "--unexpected"], "invalid"),
    ],
)
def test_cursor_main_selects_only_fixed_cli_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str], mode: str
) -> None:
    """Normal event JSON cannot self-select the full-content observer route.

    Args:
        monkeypatch: Replaces entry argv and runtime boundaries.
        capsys: Captures invalid-argument diagnostic.
        argv: Exact process argument list.
        mode: Expected closed route.
    """
    from nplet.adapters import cursor

    monkeypatch.setattr(cursor.sys, "argv", argv)
    normal = MagicMock(return_value=17)
    observer = MagicMock(return_value=19)
    monkeypatch.setattr(cursor.common, "run_native", normal)
    monkeypatch.setattr(cursor.runner, "run", observer)
    result = cursor.main()
    if mode == "normal":
        assert result == 17
        normal.assert_called_once_with(cursor.handle, cursor.failure)
        observer.assert_not_called()
    elif mode == "observer":
        assert result == 19
        observer.assert_called_once_with(cursor.handle, cursor.failure, content_observer=True)
        normal.assert_not_called()
    else:
        assert result == 2
        assert "INVALID_REQUEST" in capsys.readouterr().err
        normal.assert_not_called()
        observer.assert_not_called()


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
        "census",
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


def test_reconcile_unclosed_quote_denies_instead_of_generic_shell() -> None:
    """Reject a recognizable reconcile entrypoint with broken shell grammar."""
    command = shlex.join([children.common.PYTHON, str(children.ENTRY)]) + " '"
    with pytest.raises(core.WorkspaceError, match="HOST_UNSUPPORTED_TOOL"):
        children.recognize_reconcile(
            {"tool_name": "Shell", "tool_input": {"command": command, "cwd": "", "timeout": 30000}}
        )


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
    monkeypatch.setattr(children.core, "execute", execute)
    with pytest.raises(core.WorkspaceError, match="BUSY"):
        children.source_transition(
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
    monkeypatch.setattr(children.core, "repository", lambda _path: (tmp_path, None, []))
    transition = MagicMock(return_value={"ok": True, "code": "OK"})
    monkeypatch.setattr(children, "source_transition", transition)
    event = {
        "child": True,
        "tool_name": "Read",
        "tool_input": {"file_path": path},
        "cwd": str(tmp_path),
    }
    assert children.source_read_pre(event, time.monotonic() + 1)["ok"] is True
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
    monkeypatch.setattr(children.core, "repository", lambda _path: (tmp_path, None, []))
    transition = MagicMock()
    monkeypatch.setattr(children, "source_transition", transition)
    with pytest.raises(core.WorkspaceError, match=code):
        children.source_read_pre(event, time.monotonic() + 1)
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
    monkeypatch.setattr(children.core, "repository", lambda path: (Path(path), None, []))
    event = {
        "child": True,
        "session_id": bound["session_id"],
        "cwd": "/checkout",
        "tool_name": "Shell",
        "tool_input": {
            "command": children.common.bootstrap_command(bound, "cursor"),
            "working_directory": working_directory,
        },
    }
    if allowed:
        assert children.child_lifecycle(event) is True
    else:
        with pytest.raises(core.WorkspaceError, match="REPOSITORY_MISMATCH"):
            children.child_lifecycle(event)


def test_reconcile_module_entrypoint_rejects_unsupported_argv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise executable rejection without opening result storage or Cursor.

    Args:
        monkeypatch: Supplies unsupported literal process invocation.
        capsys: Captures bounded public result.
    """
    monkeypatch.setattr(children.sys, "argv", [str(children.ENTRY), "unsupported"])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(children.ENTRY), run_name="__main__")
    assert error.value.code == 3
    assert (
        capsys.readouterr().out
        == core.canonical({"ok": False, "code": "INVALID_REQUEST"}).decode() + "\n"
    )


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
    monkeypatch.setattr(children, "census", census)
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
    store.control.exists.side_effect = [False, True]
    monkeypatch.setattr(children, "registration", lambda _cwd: {"repo_id": "repo"})
    monkeypatch.setattr(
        children.common,
        "native_identity",
        lambda event, _host: {**event, "session_id": event["conversation_id"]},
    )
    monkeypatch.setattr(children, "census", lambda *_args: [])
    # Retry a serialized classification instead of allowing an unverified parent.
    with pytest.raises(core.WorkspaceError, match="BUSY"):
        children.normalize({"conversation_id": "parent", "cwd": "/checkout"}, time.monotonic() + 1)
    store.control.child.assert_not_called()
