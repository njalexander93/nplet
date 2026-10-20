"""Verify routing recovery policy with isolated storage and core transaction boundaries."""

from __future__ import annotations

import copy
import json
import time
from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor_children as children
from nplet.adapters import cursor_routing as routing
from nplet.lifecycle import task_workspace as core
from tests.unit.adapters.cursor_children_support import cursor_mock_store, cursor_record

pytestmark = pytest.mark.unit


@pytest.fixture
def memory_routes() -> tuple[MagicMock, dict[str, bytes]]:
    """Replace only the external file storage boundary with independent persisted bytes.

    Returns:
        Mock directory and inspectable stored bytes.
    """
    files = {"lock": b""}
    directory = MagicMock(spec=core.Directory)
    directory.identity = [1, 2]
    directory.exists.side_effect = files.__contains__
    directory.read.side_effect = lambda name, _limit=core.MAX_FILE: files[name]
    directory.write.side_effect = files.__setitem__
    directory.names.side_effect = lambda: sorted(files)
    return directory, files


def test_fresh_root_precedes_completion_and_exact_claim_replay(
    memory_routes: tuple[MagicMock, dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish initialization in order, preserve exact identities and refuse reassignment.

    Args:
        memory_routes: Isolated external directory boundary.
        monkeypatch: Captures production persistence phases.
    """
    directory, files = memory_routes
    store = MagicMock()
    store.control.exists.return_value = False
    phases = []
    monkeypatch.setattr(core, "FAILPOINT", phases.append)
    routing.initialize(store, directory, False)
    marker = store.control.put.call_args.args[1]
    assert marker == {
        "schema_version": 3,
        "complete": True,
        "registry_identity": directory.identity,
    }
    assert phases == [
        "cursor-radix-before:root",
        "cursor-radix-after:root",
        "cursor-routing-initialized",
    ]
    assert core.strict_json(files[routing.ROOT])["edges"] == {}
    # A completed root can be prepared again without discarding reachable routes.
    routing.claim(directory, "call", "call\n", "TEST-1", "issue-uuid")
    selected = routing.lookup(directory, "call", "call\n")
    assert selected["native"] == "call\n"
    assert selected["issue_id"] == "TEST-1"
    assert routing.lookup(directory, "conversation", "call\n") is None
    before = dict(files)
    writes = directory.write.call_count
    routing.prepare(directory)
    routing.claim(directory, "call", "call\n", "TEST-1", "issue-uuid")
    assert files == before
    assert directory.write.call_count == writes
    with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
        routing.claim(directory, "call", "call\n", "OTHER-2", "other-uuid")
    assert files == before


@pytest.mark.parametrize("phase", ["before", "after"])
def test_orphan_retry_merges_compatible_branches_without_dropping_routes(
    memory_routes: tuple[MagicMock, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    """Keep restrictive root publication replayable around either crash boundary.

    Args:
        memory_routes: Isolated byte persistence boundary.
        monkeypatch: Interrupts a root reference publication.
        phase: Before or after root replacement.
    """
    directory, files = memory_routes
    routing.prepare(directory)

    def interrupt(point: str) -> None:
        """Interrupt the requested root publication phase.

        Args:
            point: Production persistence phase.

        Raises:
            RuntimeError: At the selected root boundary.
        """
        if point == "cursor-radix-" + phase + ":root":
            raise RuntimeError("publication interrupted")

    monkeypatch.setattr(core, "FAILPOINT", interrupt)
    with pytest.raises(RuntimeError, match="publication interrupted"):
        routing.claim(directory, "conversation", "child", "TEST-1", "issue-uuid")
    monkeypatch.setattr(core, "FAILPOINT", None)
    assert (routing.lookup(directory, "conversation", "child") is not None) == (phase == "after")
    # These identities share their first digest digit. Compatible orphan nodes retain both.
    routing.claim(directory, "conversation", "other-11", "OTHER-2", "other-uuid")
    routing.claim(directory, "conversation", "child", "TEST-1", "issue-uuid")
    assert routing.lookup(directory, "conversation", "child")["issue_id"] == "TEST-1"
    assert routing.lookup(directory, "conversation", "other-11")["issue_id"] == "OTHER-2"
    assert core.strict_json(files["node-6.json"])["edges"].keys() >= {"0", "3"}


def test_new_oversized_endpoint_preserves_existing_routes(
    memory_routes: tuple[MagicMock, dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse encoded oversize before replacement while retaining exact replay.

    Args:
        memory_routes: Isolated external persistence boundary.
        monkeypatch: Sets the same per-node reader and writer byte limit.
    """
    directory, files = memory_routes
    routing.prepare(directory)
    routing.claim(directory, "conversation", "child", "TEST-1", "issue-uuid")
    before = dict(files)
    monkeypatch.setattr(routing, "NODE_LIMIT", max(map(len, files.values())))
    with pytest.raises(core.WorkspaceError, match="SIZE_LIMIT"):
        routing.claim(directory, "conversation", "x" * 400, "TEST-1", "issue-uuid")
    assert files == before
    routing.claim(directory, "conversation", "child", "TEST-1", "issue-uuid")
    assert routing.lookup(directory, "conversation", "child")["issue_id"] == "TEST-1"


@pytest.mark.parametrize("depth", [0, 1, 64])
def test_lost_required_node_denies_lookup_and_claim_without_recreation(
    memory_routes: tuple[MagicMock, dict[str, bytes]], depth: int
) -> None:
    """Treat referenced storage loss as uncertainty rather than unknown identity.

    Args:
        memory_routes: Isolated persistence boundary.
        depth: Root or selected descendant to remove.
    """
    directory, files = memory_routes
    routing.prepare(directory)
    routing.claim(directory, "conversation", "child", "TEST-1", "issue-uuid")
    digest = core.sha(core.canonical(["conversation", "child"]))
    name = routing.ROOT if depth == 0 else "node-" + digest[:depth] + ".json"
    del files[name]
    before = dict(files)
    with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
        routing.lookup(directory, "conversation", "child")
    with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
        routing.claim(directory, "conversation", "child", "TEST-1", "issue-uuid")
    assert files == before


@pytest.mark.parametrize("selection", ["all", "only-bound", "none", "empty"])
def test_selected_issue_recovery_filters_then_copies_committed_records(
    monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    """Recover only the selected issue and return independent matching snapshots.

    Args:
        monkeypatch: Replaces only core storage and recovered Issue boundaries.
        selection: Predicate or empty-history boundary.
    """
    store, control = cursor_mock_store(monkeypatch)
    bound = cursor_record()
    admitted = copy.deepcopy(bound["invocation"])
    admitted.update(
        native_call="other-call",
        tool_id=children.call_token("other-call"),
        child_participant=None,
        phase="admitted",
    )
    state = {
        "issue_id": "TEST-1",
        "issue_uuid": "issue-uuid",
        "revision": 12,
        "participants": {"child-key": bound["member"]},
        "cursor_invocations": {}
        if selection == "empty"
        else {
            "bound": bound["invocation"],
            "admitted": admitted,
        },
    }
    issue = MagicMock()
    issue.committed_state.return_value = state
    constructor = MagicMock(return_value=issue)
    monkeypatch.setattr(core, "Issue", constructor)
    result = children.issue_records(
        store,
        time.monotonic() + 10,
        lambda record: (
            selection == "all" or (selection == "only-bound" and record["invocation_id"] == "bound")
        ),
        "TEST-1",
    )
    assert [record["invocation_id"] for record in result] == {
        "all": ["bound", "admitted"],
        "only-bound": ["bound"],
        "none": [],
        "empty": [],
    }[selection]
    store.issues.child.assert_called_once_with("TEST-1")
    control.lock.assert_called_once_with(create=False)
    constructor.assert_called_once_with(store, control, "TEST-1")
    issue.recover.assert_called_once_with()
    assert issue.mock_calls.index(("recover", (), {})) < issue.mock_calls.index(
        ("committed_state", (), {})
    )
    if result:
        assert result[0]["member"] == bound["member"]
        result[0]["invocation"]["phase"] = "changed"
        assert state["cursor_invocations"]["bound"]["phase"] == "bound"
    if selection == "all":
        assert result[1]["member"] is None


def test_parent_marker_selects_exact_call_before_missing_record_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate a supplied native parent-call marker without inventing child authority.

    Args:
        monkeypatch: Replaces external native identity and selected record boundaries.
    """
    store, _control = cursor_mock_store(monkeypatch)
    store.control.exists.return_value = True
    monkeypatch.setattr(children, "registration", lambda _cwd: {})
    monkeypatch.setattr(
        children.common, "native_identity", lambda raw, _host: {**raw, "session_id": "actual-child"}
    )
    lookup = MagicMock(return_value={"issue_id": "TEST-1", "issue_uuid": "issue-uuid"})
    monkeypatch.setattr(routing, "lookup", lookup)
    records = MagicMock(return_value=[])
    monkeypatch.setattr(children, "issue_records", records)
    with pytest.raises(core.WorkspaceError, match="UNKNOWN_OPERATION"):
        children.normalize(
            {"cwd": "/checkout", "parent_tool_call_id": "native\ncall"}, time.monotonic() + 10
        )
    assert lookup.call_args.args[1:] == ("call", "native\ncall")
    assert records.call_args.args[3] == "TEST-1"


@pytest.mark.parametrize(
    "mode", ["fresh", "marked", "empty", "changed-inventory", "wrong-inventory", "invalid-order"]
)
def test_explicit_population_verifies_complete_inventory_before_marker(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Publish only selected exact routes and withhold completion after inventory drift.

    Args:
        monkeypatch: Replaces registration, storage and selected Issue read boundaries.
        mode: Complete, empty, inconsistent or invalid operator input.
    """
    store, _control = cursor_mock_store(monkeypatch)
    store.issues_present = mode != "empty"
    marked = mode == "marked"
    store.control.exists.return_value = marked
    store.issues.names.side_effect = (
        [["TEST-1"], ["OTHER-2", "TEST-1"]] if mode == "changed-inventory" else lambda: ["TEST-1"]
    )
    monkeypatch.setattr(children, "registration", lambda _cwd: {"repo_id": "repo"})
    record = cursor_record()
    # A second record has no conversation selector and must not invent one.
    unbound = copy.deepcopy(record)
    unbound["invocation"].update(
        child_conversation=None,
        resume_child=None,
        native_call="unbound-native",
        tool_id=children.call_token("unbound-native"),
    )
    unbound["invocation_id"] = "unbound"
    record["invocation"]["resume_child"] = record["invocation"]["child_conversation"]
    records = MagicMock(return_value=[record, unbound])
    monkeypatch.setattr(children, "issue_records", records)
    guard, verify, prepare, claim, complete = (MagicMock() for _ in range(5))
    complete.return_value = {"schema_version": 3, "complete": True}
    for name, mock in [
        ("legacy_guard", guard),
        ("verify", verify),
        ("prepare", prepare),
        ("claim", claim),
        ("complete", complete),
    ]:
        monkeypatch.setattr(routing, name, mock)
    identifiers = [] if mode in {"empty", "wrong-inventory"} else ["TEST-1"]
    if mode == "invalid-order":
        identifiers = ["TEST-2", "TEST-1"]
    expected = {
        "changed-inventory": "REVISION_CONFLICT",
        "wrong-inventory": "REQUEST_CONFLICT",
        "invalid-order": "INVALID_REQUEST",
    }.get(mode)
    if expected:
        with pytest.raises(core.WorkspaceError, match=expected):
            children.populate_routes("/checkout", identifiers, time.monotonic() + 10)
        complete.assert_not_called()
        if mode != "changed-inventory":
            claim.assert_not_called()
        return
    result = children.populate_routes("/checkout", identifiers, time.monotonic() + 10)
    assert result == complete.return_value
    assert verify.call_count == int(marked)
    assert prepare.call_count == int(not marked)
    complete.assert_called_once()
    assert claim.call_count == (0 if mode == "empty" else 3)
    if mode != "empty":
        assert records.call_args.args[3] == "TEST-1"
        assert [(call.args[1], call.args[2]) for call in claim.call_args_list] == [
            ("call", record["invocation"]["native_call"]),
            ("conversation", record["invocation"]["child_conversation"]),
            ("call", unbound["invocation"]["native_call"]),
        ]
    else:
        records.assert_not_called()
        store.issues.names.assert_not_called()


@pytest.mark.parametrize("budget", ["2500", "0", "invalid"])
def test_recovery_cli_uses_explicit_finite_operator_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], budget: str
) -> None:
    """Keep standalone recovery inputs separate from native event metadata.

    Args:
        monkeypatch: Replaces process argv, time and population boundary.
        capsys: Captures exact bounded output.
        budget: Operator budget or malformed input.
    """
    monkeypatch.setattr(
        children.sys,
        "argv",
        [
            "cursor_children.py",
            "cursor-routing-recover",
            "--issues-json",
            '["TEST-1"]',
            "--budget-ms",
            budget,
        ],
    )
    monkeypatch.setattr(children.time, "monotonic", lambda: 20.0)
    populate = MagicMock(return_value={"schema_version": 3, "complete": True})
    monkeypatch.setattr(children, "populate_routes", populate)
    status = children.main()
    output = json.loads(capsys.readouterr().out)
    assert status == (0 if budget == "2500" else 3)
    assert output["ok"] is (budget == "2500")
    if budget == "2500":
        assert populate.call_args.args[1:] == (["TEST-1"], 22.5)
        assert output["routing"] == populate.return_value
    else:
        populate.assert_not_called()
        assert output["code"] == ("INVALID_REQUEST" if budget == "0" else "RECOVERY_REQUIRED")


def test_exact_orphan_retry_publishes_only_the_missing_root_reference(
    memory_routes: tuple[MagicMock, dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse completed orphan descendants without rewriting their bytes.

    Args:
        memory_routes: Isolated persistence boundary.
        monkeypatch: Interrupts only the original final root reference.
    """
    directory, files = memory_routes
    routing.prepare(directory)

    def interrupt(point: str) -> None:
        """Leave all descendants durable but unreachable.

        Args:
            point: Production publication phase.

        Raises:
            RuntimeError: Before final root replacement.
        """
        if point == "cursor-radix-before:root":
            raise RuntimeError("root interrupted")

    monkeypatch.setattr(core, "FAILPOINT", interrupt)
    with pytest.raises(RuntimeError, match="root interrupted"):
        routing.claim(directory, "conversation", "child", "TEST-1", "issue-uuid")
    monkeypatch.setattr(core, "FAILPOINT", None)
    descendants = {name: value for name, value in files.items() if name.startswith("node-")}
    directory.write.reset_mock()
    routing.claim(directory, "conversation", "child", "TEST-1", "issue-uuid")
    assert directory.write.call_count == 1
    assert directory.write.call_args.args[0] == routing.ROOT
    assert all(files[name] == value for name, value in descendants.items())
    assert routing.lookup(directory, "conversation", "child")["issue_id"] == "TEST-1"


def test_registered_parent_without_registry_never_opens_issue_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the ordinary normalized parent without requiring an issue collection.

    Args:
        monkeypatch: Replaces only registered storage and native identity boundaries.
    """
    store, _control = cursor_mock_store(monkeypatch)
    store.control.exists.return_value = False
    store.issues_present = False
    monkeypatch.setattr(children, "registration", lambda _cwd: {})
    monkeypatch.setattr(
        children.common,
        "native_identity",
        lambda raw, _host: {**raw, "session_id": "actual-parent"},
    )
    result = children.normalize(
        {
            "cwd": "/checkout",
            "tool_use_id": "raw\ncall",
            "_cursor_forged": "untrusted",
        },
        time.monotonic() + 10,
    )
    assert result["session_id"] == "actual-parent"
    assert result["tool_use_id"] == children.call_token("raw\ncall")
    assert result["_cursor_native_call"] == "raw\ncall"
    assert "_cursor_forged" not in result
    assert result.get("child") is not True
    store.issues.child.assert_not_called()
    store.control.child.assert_not_called()
