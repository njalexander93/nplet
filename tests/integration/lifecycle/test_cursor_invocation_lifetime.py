"""Verify Cursor invocation lifetime behavior."""

from __future__ import annotations

import concurrent.futures
import copy
import threading
import uuid

import pytest

from nplet.lifecycle import task_workspace as core
from tests.integration.lifecycle.cursor_support import InvocationFixture, invocation_execute

pytestmark = pytest.mark.integration


class CursorLifetimeTests(InvocationFixture):
    """Exercise lifetime behavior over real disposable lifecycle stores."""

    def test_independent_readiness_owned_updates_and_atomic_retirement(self) -> None:
        """Require independent child readiness and retire only an empty exact generation.

        Raises:
            AssertionError: Readiness, ownership or atomic accounting violates its contract.
        """
        # Admission reserves the parent Task before the actual child receives a packet.
        invocation = self.start()
        bound = self.bind("first", invocation)
        child = self.child_request("read", binding_generation=bound["binding_generation"])
        key = core.participant_key(child)
        parent = self.base["coordinator"]
        state = self.state()
        assert state["participants"][parent]["pending"] == {
            "tool-first": {
                "status": "pending",
                "cursor_invocation": invocation["invocation_id"],
            }
        }
        assert state["participants"][key]["ack"] is None
        assert state["owners"]["context/first.md"] == key
        assert state["coordinator"] == parent
        # Unready child tools and owned writes must have no committed effects.
        update = self.child_request(
            "update",
            binding_generation=1,
            expected_revision=state["revision"],
            path="context/first.md",
            old_digest=None,
            content="bounded child output\n",
            provenance={"sources": self.evidence(), "status": "draft", "applicability": "fixture"},
        )
        assert invocation_execute(update)["code"] == "NOT_READY"
        assert (
            invocation_execute(
                self.child_request("tool-start", binding_generation=1, tool_id="effect")
            )["code"]
            == "NOT_READY"
        )
        assert self.state() == state
        self.child_ready(1)
        update.update(expected_revision=self.state()["revision"], request_id=str(uuid.uuid4()))
        self.require_ok(invocation_execute(update))
        # A real admitted effect blocks retirement and generic completion cannot release Task.
        self.require_ok(
            invocation_execute(
                self.child_request("tool-start", binding_generation=1, tool_id="effect")
            )
        )
        before = self.state()
        assert (
            invocation_execute(self.retirement("first", invocation))["code"] == "PENDING_OPERATION"
        )
        assert (
            invocation_execute(self.req("tool-complete", tool_id="tool-first", completed=True))[
                "code"
            ]
            == "REQUEST_CONFLICT"
        )
        assert self.state() == before
        self.require_ok(
            invocation_execute(
                self.child_request(
                    "tool-complete", binding_generation=1, tool_id="effect", completed=True
                )
            )
        )
        # One retirement commit both detaches the child and releases the parent Task.
        retire = self.retirement("first", invocation)
        first = self.require_ok(invocation_execute(retire))
        after = self.state()
        assert after["participants"][key]["status"] == "detached"
        assert after["participants"][key]["ack"] is None
        assert after["participants"][parent]["pending"] == {}
        assert after["cursor_invocations"][invocation["invocation_id"]]["phase"] == "retired"
        assert invocation_execute(retire) == first
        assert self.state() == after
        # Fresh-ID exact result duplicates are event-free; contradictions preserve all state.
        duplicate = {**retire, "request_id": str(uuid.uuid4())}
        self.require_ok(invocation_execute(duplicate))
        assert self.state() == after
        duplicate["result"] = {**retire["result"], "binary_sha256": "e" * 64}
        assert invocation_execute(duplicate)["code"] == "REQUEST_CONFLICT"
        assert self.state() == after

    def test_generic_routes_cannot_bypass_invocation_lifetime(self) -> None:
        """Fence generic detach, reconcile, role transfer, source admission and resume.

        Raises:
            AssertionError: A generic route grants stale authority or releases the invocation.
        """
        # Bind an empty-effects child; its generic detachment is still forbidden.
        invocation = self.start()
        self.bind("first", invocation)
        child = self.child_request("read", binding_generation=1)
        key = core.participant_key(child)
        before = self.state()
        for request in (
            self.child_request("detach", binding_generation=1, evidence=self.evidence()),
            self.req(
                "reconcile-participant",
                expected_revision=before["revision"],
                target_participant=key,
                target_generation=1,
                evidence=self.evidence(),
            ),
            self.req(
                "transfer-coordinator",
                expected_revision=before["revision"],
                target_participant=key,
                evidence=self.evidence(),
            ),
        ):
            assert invocation_execute(request)["code"] == (
                "PENDING_OPERATION"
                if request["operation"] == "transfer-coordinator"
                else "NOT_OWNER"
            )
            assert self.state() == before
        # Retirement fences every later generic route without inventing new generations.
        self.require_ok(invocation_execute(self.retirement("first", invocation)))
        before = self.state()
        for operation in ("attach", "resume", "bind", "ready", "read", "acknowledge", "tool-start"):
            result = invocation_execute(
                self.child_request(
                    operation,
                    binding_generation=1,
                    tool_id="late",
                    packet_digest=core.sha(core.canonical([])),
                )
            )
            assert result["code"] == "STALE_BINDING"
            assert self.state() == before

    def test_result_identity_and_nonterminal_shape_preserve_parent_charge(self) -> None:
        """Refuse conflicting identity, background, unknown kind and malformed projected metadata.

        Raises:
            AssertionError: Invalid result data changes committed state or releases a charge.
        """
        # Establish one actual child so result identity has an independently committed counterpart.
        invocation = self.start()
        self.bind("first", invocation)
        before = self.state()
        request = self.retirement("first", invocation)
        for changes, code in (
            ({"parent_conversation": "other-parent"}, "RESULT_NOT_TERMINAL"),
            ({"native_call": "different"}, "RESULT_NOT_TERMINAL"),
            ({"child_conversation": "foreign-child"}, "STALE_BINDING"),
            ({"is_background": True}, "RESULT_NOT_TERMINAL"),
            ({"background_reason": 1}, "RESULT_NOT_TERMINAL"),
            ({"kind": "task-error"}, "RESULT_NOT_TERMINAL"),
            ({"duration_ms": True}, "RESULT_NOT_TERMINAL"),
            ({"binary_sha256": "bad"}, "INVALID_REQUEST"),
            ({"storage_key": ""}, "INVALID_REQUEST"),
        ):
            bad = copy.deepcopy(request)
            bad["request_id"] = str(uuid.uuid4())
            bad["result"].update(changes)
            assert invocation_execute(bad)["code"] == code
            assert self.state() == before

    def test_effect_admission_and_retirement_serialize_under_the_real_issue_lock(self) -> None:
        """Observe either a retained admitted effect or a retired fenced generation.

        Raises:
            AssertionError: Both operations succeed or an admitted effect is lost.
        """
        # Ready an actual bound child, then release two workers through one deterministic barrier.
        invocation = self.start()
        self.bind("first", invocation)
        self.child_ready(1)
        barrier = threading.Barrier(2)
        start = self.child_request("tool-start", binding_generation=1, tool_id="racing-effect")
        retire = self.retirement("first", invocation)

        def compete(request: dict) -> dict:
            """Release one worker and execute exactly one operation.

            Args:
                request: Complete disposable lifecycle request.

            Returns:
                The actual core result after acquiring the persistent issue lock.

            Raises:
                threading.BrokenBarrierError: The peer fails to reach the deadline.
            """
            # Both requests are prepared before either tries to enter the core.
            barrier.wait(timeout=5)
            return invocation_execute(request)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            effect_future = pool.submit(compete, start)
            retire_future = pool.submit(compete, retire)
            effect = effect_future.result(timeout=10)
            retired = retire_future.result(timeout=10)
        # Both orders have a concrete state invariant; a lock timeout never counts as success.
        key = core.participant_key(start)
        member = self.state()["participants"][key]
        if effect["ok"]:
            assert retired["code"] == "REVISION_CONFLICT"
            assert member["pending"] == {"racing-effect": {"status": "pending"}}
            assert member["status"] == "ready"
        else:
            assert retired["ok"] is True
            assert effect["code"] == "STALE_BINDING"
            assert member["pending"] == {}
            assert member["status"] == "detached"

    def test_generic_scope_cannot_broaden_or_revive_a_child_assignment(self) -> None:
        """Permit exact source refresh while retaining planned child output and lifetime limits."""
        # Bind a child with one real source so same-size packet changes are observable.
        self.create()
        self.ready()
        packet = [
            {
                "id": "source",
                "locator": "roadmap.md",
                "sha256": self.state()["files"]["roadmap.md"],
                "required": True,
                "reader": "cursor-child",
                "authority": "fixture",
                "reason": "bounded-source",
                "stage": "implementation",
            }
        ]
        self.call(
            "cursor-plan",
            expected_revision=self.state()["revision"],
            plan_id="first",
            parent_conversation="parent-conversation",
            task_digest="a" * 64,
            packet=packet,
            owned_paths=["context/first.md"],
        )
        invocation = self.admit("first")
        self.bind("first", invocation)
        self.child_ready(1)
        key = core.participant_key(self.child_request("read"))
        current = self.state()["participants"][key]["packet"]
        before = self.state()
        # Removing a source, replacing its identity, or expanding outputs is rejected unchanged.
        for changed, owned, code in [
            ([], [], "SCOPE_MISSING"),
            ([{**current[0], "id": "other"}], [], "SCOPE_MISSING"),
            (current, ["context/other.md"], "NOT_OWNER"),
        ]:
            request = self.req(
                "scope",
                expected_revision=before["revision"],
                target_participant=key,
                packet=changed,
                owned_paths=owned,
            )
            assert invocation_execute(request)["code"] == code
            assert self.state() == before
        # An exact refresh invalidates acknowledgment without changing output ownership.
        self.call(
            "scope",
            expected_revision=before["revision"],
            target_participant=key,
            packet=current,
            owned_paths=["context/first.md"],
        )
        assert self.state()["participants"][key]["ack"] is None
        self.child_ready(1)
        self.require_ok(invocation_execute(self.retirement("first", invocation)))
        # The same coordinator refresh cannot revive an already retired invocation.
        before = self.state()
        assert (
            invocation_execute(
                self.req(
                    "scope",
                    expected_revision=before["revision"],
                    target_participant=key,
                    packet=current,
                )
            )["code"]
            == "STALE_BINDING"
        )
        assert self.state() == before
