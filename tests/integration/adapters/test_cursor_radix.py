"""Exercise radix publication and actual Cursor transitions through real durable controls."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from nplet.adapters import cursor_children as children
from nplet.adapters import cursor_routing as routing
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters import test_cursor_children as support
from tests.integration.adapters.test_cursor_children import cursor_case as cursor_case
from tests.support import Fixture

pytestmark = pytest.mark.integration


@pytest.fixture
def registry(tmp_path: Path) -> Iterator[core.Directory]:
    """Open a real disposable safe directory under the existing persistent mutex.

    Args:
        tmp_path: Disposable fixture storage.

    Yields:
        Initialized real routing directory.
    """
    with (
        core.Directory.absolute(tmp_path.resolve()) as control,
        control.child("routes", True) as directory,
        directory.lock(),
    ):
        routing.initialize(SimpleNamespace(control=control), directory, False)
        yield directory


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("ordinal", range(65))
def test_every_node_publication_recovers_exactly(
    registry: core.Directory, monkeypatch: pytest.MonkeyPatch, phase: str, ordinal: int
) -> None:
    """Retry all sixty-five durable publication boundaries without losing selection.

    Args:
        registry: Real initialized directory.
        monkeypatch: Installs one production fault callback.
        phase: Before or after atomic node replacement.
        ordinal: Descendant-to-root publication index.
    """
    count = 0

    def interrupt(point: str) -> None:
        """Interrupt the selected publication.

        Args:
            point: Actual production persistence boundary.

        Raises:
            RuntimeError: At the requested boundary.
        """
        nonlocal count
        if point.startswith("cursor-radix-" + phase + ":"):
            current = count
            count += 1
            if current == ordinal:
                raise RuntimeError("publication interrupted")

    monkeypatch.setattr(core, "FAILPOINT", interrupt)
    with pytest.raises(RuntimeError, match="publication interrupted"):
        routing.claim(registry, "conversation", "child", "TEST-1", "issue-uuid")
    monkeypatch.setattr(core, "FAILPOINT", None)
    # Only publication of the final root edge makes a new path reachable.
    selected = routing.lookup(registry, "conversation", "child")
    assert (selected is not None) == (phase == "after" and ordinal == 64)
    routing.claim(registry, "conversation", "child", "TEST-1", "issue-uuid")
    assert routing.lookup(registry, "conversation", "child")["issue_id"] == "TEST-1"
    before = {name: registry.read(name) for name in registry.names() if name.endswith(".json")}
    routing.claim(registry, "conversation", "child", "TEST-1", "issue-uuid")
    assert {name: registry.read(name) for name in before} == before
    with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
        routing.claim(registry, "conversation", "child", "OTHER-2", "other-uuid")


@pytest.mark.parametrize("depth", [0, 1, 32, 64])
def test_missing_referenced_node_is_never_recreated(registry: core.Directory, depth: int) -> None:
    """Deny both classification and insertion after referenced storage loss.

    Args:
        registry: Real initialized directory.
        depth: Root or selected descendant to remove.
    """
    routing.claim(registry, "conversation", "child", "TEST-1", "issue-uuid")
    digest = core.sha(core.canonical(["conversation", "child"]))
    name = "node-" + digest[:depth] + ".json" if depth else routing.ROOT
    registry.unlink(name)
    with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
        routing.lookup(registry, "conversation", "child")
    with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
        routing.claim(registry, "conversation", "child", "TEST-1", "issue-uuid")
    assert not registry.exists(name)


def test_unknown_edge_is_read_only_absence(registry: core.Directory) -> None:
    """Distinguish an unknown native identity from missing referenced storage.

    Args:
        registry: Real initialized directory.
    """
    before = registry.read(routing.ROOT)
    assert routing.lookup(registry, "conversation", "unknown") is None
    assert registry.read(routing.ROOT) == before
    assert sorted(registry.names()) == sorted(["lock", routing.ROOT])


def test_compatible_orphan_branch_preserves_both_routes(
    registry: core.Directory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merge compatible orphan descendants before exposing their shared root edge.

    Args:
        registry: Real initialized directory.
        monkeypatch: Interrupts only the first final root publication.
    """

    def interrupt(point: str) -> None:
        """Leave a complete unreachable branch.

        Args:
            point: Actual production persistence boundary.

        Raises:
            RuntimeError: Before the root references the branch.
        """
        if point == "cursor-radix-before:root":
            raise RuntimeError("root interrupted")

    monkeypatch.setattr(core, "FAILPOINT", interrupt)
    with pytest.raises(RuntimeError, match="root interrupted"):
        routing.claim(registry, "conversation", "child", "TEST-1", "issue-uuid")
    monkeypatch.setattr(core, "FAILPOINT", None)
    # These exact hashes share the first digit and diverge at the second.
    routing.claim(registry, "conversation", "other-11", "OTHER-2", "other-uuid")
    assert routing.lookup(registry, "conversation", "child")["issue_id"] == "TEST-1"
    assert routing.lookup(registry, "conversation", "other-11")["issue_id"] == "OTHER-2"
    routing.claim(registry, "conversation", "child", "TEST-1", "issue-uuid")
    assert routing.lookup(registry, "conversation", "other-11")["issue_id"] == "OTHER-2"


@pytest.mark.parametrize("transition", ["admit", "bind", "terminal-only-retire"])
@pytest.mark.parametrize("boundary", ["intent", "state", "transaction-complete"])
def test_issue_commit_crashes_recover_behind_durable_route(
    cursor_case: Fixture, monkeypatch: pytest.MonkeyPatch, transition: str, boundary: str
) -> None:
    """Keep exact selection durable around actual authority transaction failures.

    Args:
        cursor_case: Real ready disposable coordinator.
        monkeypatch: Interrupts actual core transaction persistence.
        transition: Admission, binding or terminal-only retirement.
        boundary: Actual core persistence failpoint.
    """
    case = cursor_case
    raw_call = "commit-crash-" + transition
    if transition == "admit":
        # Plan normally while deferring only the native admission callback.
        with monkeypatch.context() as patch:
            patch.setattr(support, "cursor_handle", lambda _event: {"permission": "allow"})
            args = support.planned_task(case, "commit-crash", raw_call)
    else:
        support.planned_task(case, "commit-crash", raw_call)

    def perform() -> dict:
        """Execute the unchanged exact operation.

        Returns:
            The real adapter result.
        """
        deadline = time.monotonic() + 10
        if transition == "admit":
            event = children.normalize(
                support.cursor_native(
                    case,
                    "preToolUse",
                    tool_name="Task",
                    tool_use_id=raw_call,
                    tool_input=args,
                ),
                deadline,
            )
            return children.admit(event, deadline)
        if transition == "bind":
            return children.normalize(
                support.cursor_native(
                    case,
                    "beforeSubmitPrompt",
                    child=True,
                    parent_call=raw_call,
                ),
                deadline,
            )
        return children.reconcile(
            str(case.root), case.base["issue_id"], children.call_token(raw_call), deadline
        )

    def interrupt(point: str) -> None:
        """Stop the real authority transaction at its persistence boundary.

        Args:
            point: Production persistence boundary.

        Raises:
            RuntimeError: At the selected transaction boundary.
        """
        if point == boundary:
            raise RuntimeError("commit interrupted")

    monkeypatch.setattr(core, "FAILPOINT", interrupt)
    with pytest.raises(RuntimeError, match="commit interrupted"):
        perform()
    monkeypatch.setattr(core, "FAILPOINT", None)
    # Read the nonauthorizing route without recovering the interrupted issue first.
    configured = children.registration(str(case.root))
    with (
        core.Store(configured, create=False, bindings=False) as store,
        store.control.child("cursor-children") as directory,
        directory.lock(create=False),
    ):
        kind = "call" if transition == "admit" else "conversation"
        native = raw_call if transition == "admit" else support.CHILD
        assert routing.lookup(directory, kind, native)["issue_id"] == case.base["issue_id"]
    result = perform()
    assert result.get("ok", result.get("child")) is True, result
    state = case.state()
    invocation = next(iter(state["cursor_invocations"].values()))
    assert (
        invocation["phase"]
        == {
            "admit": "admitted",
            "bind": "bound",
            "terminal-only-retire": "retired",
        }[transition]
    )
    assert len(state["cursor_invocations"]) == 1


@pytest.mark.parametrize("transition", ["bind", "terminal-only-retire"])
def test_late_unknown_child_survives_full_shared_prefix(
    cursor_case: Fixture, monkeypatch: pytest.MonkeyPatch, transition: str
) -> None:
    """Resolve an admitted Task after intervening routes fill its shared prefix fanout.

    Args:
        cursor_case: Real ready disposable coordinator.
        monkeypatch: Sets a derived per-node limit without limiting aggregate histories.
        transition: Binding or terminal-only retirement of the previously unknown child.
    """
    case = cursor_case
    raw_call = "late-child-" + transition
    support.planned_task(case, "late-child", raw_call)
    target = core.sha(core.canonical(["conversation", support.CHILD]))
    # Derive the limit from a fully populated longest internal node and bounded endpoint.
    internal = {
        "schema_version": 1,
        "prefix": "f" * 63,
        "edges": {digit: "f" * 63 + digit for digit in "0123456789abcdef"},
        "route": None,
    }
    endpoint = {
        "schema_version": 1,
        "prefix": "f" * 64,
        "edges": {},
        "route": {
            "schema_version": 1,
            "kind": "conversation",
            "native": "x" * 160,
            "issue_id": case.base["issue_id"],
            "issue_uuid": case.state()["issue_uuid"],
        },
    }
    limit = max(len(core.canonical(internal)), len(core.canonical(endpoint)))
    monkeypatch.setattr(routing, "NODE_LIMIT", limit)
    # Deterministically cover all sixteen second digits under the child's first digit.
    natives = {}
    index = 0
    while len(natives) < 16:
        native = "intervening-" + str(index)
        digest = core.sha(core.canonical(["conversation", native]))
        if digest[0] == target[0]:
            natives.setdefault(digest[1], native)
        index += 1
    configured = children.registration(str(case.root))
    with (
        core.Store(configured, create=False, bindings=False) as store,
        store.control.child("cursor-children") as directory,
        directory.lock(create=False),
    ):
        for native in natives.values():
            routing.claim(
                directory, "conversation", native, case.base["issue_id"], case.state()["issue_uuid"]
            )
        node = directory.json("node-" + target[0] + ".json")
        assert len(node["edges"]) == 16
        assert (
            sum(
                len(
                    directory.read(
                        "node-" + core.sha(core.canonical(["conversation", native])) + ".json"
                    )
                )
                for native in natives.values()
            )
            > limit
        )
    # No future-child capacity was reserved when admission committed.
    if transition == "bind":
        result = children.normalize(
            support.cursor_native(
                case,
                "beforeSubmitPrompt",
                child=True,
                parent_call=raw_call,
            ),
            time.monotonic() + 10,
        )
        assert result["child"] is True
    else:
        result = children.reconcile(
            str(case.root),
            case.base["issue_id"],
            children.call_token(raw_call),
            time.monotonic() + 10,
        )
        assert result["ok"], result
        with pytest.raises(core.WorkspaceError, match="STALE_BINDING"):
            children.normalize(
                support.cursor_native(
                    case,
                    "beforeSubmitPrompt",
                    child=True,
                ),
                time.monotonic() + 10,
            )


def test_oversized_terminal_rejects_before_replacement(
    registry: core.Directory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain prior routes and bytes when a new encoded endpoint exceeds its reader limit.

    Args:
        registry: Real initialized directory.
        monkeypatch: Sets the identical reader and writer node limit.
    """
    routing.claim(registry, "conversation", "child", "TEST-1", "issue-uuid")
    before = {name: registry.read(name) for name in registry.names() if name.endswith(".json")}
    monkeypatch.setattr(routing, "NODE_LIMIT", max(map(len, before.values())))
    with pytest.raises(core.WorkspaceError, match="SIZE_LIMIT"):
        routing.claim(registry, "conversation", "x" * 400, "TEST-1", "issue-uuid")
    assert {name: registry.read(name) for name in before} == before
    assert sorted(registry.names()) == sorted(["lock", *before])
    assert routing.lookup(registry, "conversation", "child")["issue_id"] == "TEST-1"
    routing.claim(registry, "conversation", "child", "TEST-1", "issue-uuid")
