"""Verify crash recovery and binding publication for Cursor invocations."""

from __future__ import annotations

import pytest

from nplet.lifecycle import task_workspace as core
from tests.integration.lifecycle.cursor_support import InvocationFixture, invocation_execute

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "operation",
    [
        "cursor-plan",
        "cursor-admit",
        "cursor-bind-child",
        "cursor-retire",
    ],
)
@pytest.mark.parametrize(
    "point",
    [
        "before-intent",
        "intent",
        "payload:events.jsonl",
        "payload-flush",
        "state",
        "transaction-complete",
    ],
)
def test_cursor_transactions_recover_once(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    point: str,
) -> None:
    """Recover every invocation transition at the existing durable publication seams.

    Args:
        monkeypatch: Restores the process-local failure seam after each scenario.
        operation: One atomic invocation transition.
        point: Existing transaction persistence seam.

    Raises:
        AssertionError: Recovery loses correlation or publishes more than one transition.
    """
    # Allocate a fresh disposable repository for every operation and persistence seam.
    case = InvocationFixture()
    try:
        case.setUp()
        case.create()
        case.ready()
        # Prepare only prerequisites; the selected transition remains uncommitted.
        if operation == "cursor-plan":
            request = case.req(
                operation,
                expected_revision=case.state()["revision"],
                plan_id="first",
                parent_conversation="parent-conversation",
                task_digest="a" * 64,
                packet=[],
                owned_paths=["context/first.md"],
            )
        else:
            case.plan("first")
            if operation == "cursor-admit":
                request = case.req(
                    operation,
                    expected_revision=case.state()["revision"],
                    plan_id="first",
                    parent_conversation="parent-conversation",
                    task_digest="a" * 64,
                    tool_id="tool-first",
                    native_call="native\nfirst",
                )
            else:
                invocation = case.admit("first")
                if operation == "cursor-bind-child":
                    request = case.child_request(
                        operation,
                        invocation_id=invocation["invocation_id"],
                        tool_id="tool-first",
                        child_conversation="child",
                        native_generation="actual-child-native-generation",
                    )
                else:
                    case.bind("first", invocation)
                    request = case.retirement("first", invocation)
        before = case.state()

        def fail(actual: str) -> None:
            """Inject one selected publication failure.

            Args:
                actual: Production persistence seam.

            Raises:
                OSError: The selected seam is reached.
            """
            # Leave every other persistence phase running normally.
            if actual == point:
                raise OSError("injected transaction failure")

        monkeypatch.setattr(core, "FAILPOINT", fail)
        assert invocation_execute(request)["code"] == "RECOVERY_REQUIRED"
        monkeypatch.setattr(core, "FAILPOINT", None)
        # An exact retry rolls forward or restages once through the existing protocol.
        first = case.require_ok(invocation_execute(request))
        committed = case.state()
        assert committed["seq"] == before["seq"] + 1
        events = (case.root / ".task/TEST-1/events.jsonl").read_bytes()
        assert core.validate_events(events)[0] == committed["seq"]
        assert invocation_execute(request) == first
        assert case.state() == committed
        assert not (case.root / ".task/.control/issues/TEST-1/transaction.json").exists()
    finally:
        # Reap all disposable resources even if setup or injected recovery fails.
        monkeypatch.setattr(core, "FAILPOINT", None)
        case.doCleanups()


def test_child_binding_publication_failure_repairs_without_another_issue_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair a missing child binding after its invocation transaction already committed.

    Args:
        monkeypatch: Restores the real binding writer after the injected failure.

    Raises:
        AssertionError: Retry duplicates the transition or leaves the child unbound.
    """
    # Stage the actual admitted Task before failing only its child binding publication.
    case = InvocationFixture()
    try:
        case.setUp()
        invocation = case.start()
        request = case.child_request(
            "cursor-bind-child",
            invocation_id=invocation["invocation_id"],
            tool_id="tool-first",
            child_conversation="child",
            native_generation="actual-child-native-generation",
        )
        child_key = core.participant_key(request)
        real_save = core.Store.save_binding

        def fail_child(store: core.Store, state: dict, participant: str) -> None:
            """Fail only the selected post-commit child binding write.

            Args:
                store: Validated production store.
                state: Already committed issue state.
                participant: Exact participant whose binding is being written.

            Raises:
                OSError: The selected child binding is published.
            """
            # Preserve unrelated parent binding writes.
            if participant == child_key:
                raise OSError("injected child binding failure")
            real_save(store, state, participant)

        monkeypatch.setattr(core.Store, "save_binding", fail_child)
        assert invocation_execute(request)["code"] == "RECOVERY_REQUIRED"
        committed = case.state()
        assert committed["participants"][child_key]["status"] == "attached"
        monkeypatch.setattr(core.Store, "save_binding", real_save)
        # The committed invocation remains authoritative while its binding is reconstructed.
        result = case.require_ok(invocation_execute(request))
        assert result["binding_generation"] == 1
        assert case.state() == committed
        binding = case.root / ".task/.bindings" / (child_key + ".json")
        assert (
            core.strict_json(binding.read_bytes())["cursor_invocation"]
            == invocation["invocation_id"]
        )
    finally:
        # Keep teardown independent from the injection.
        case.doCleanups()
