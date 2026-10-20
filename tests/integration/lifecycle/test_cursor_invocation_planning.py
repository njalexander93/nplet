"""Verify Cursor invocation planning behavior."""

from __future__ import annotations

import uuid

import pytest

from nplet.lifecycle import task_workspace as core
from tests.integration.lifecycle.cursor_support import InvocationFixture, invocation_execute

pytestmark = pytest.mark.integration


class CursorPlanningTests(InvocationFixture):
    """Exercise planning behavior over real disposable lifecycle stores."""

    def test_resume_requires_new_plan_generation_and_old_completion_cannot_retire_it(self) -> None:
        """Bind an explicitly resumed child freshly and fence even exact old request replay.

        Raises:
            AssertionError: A resumed generation inherits readiness or accepts old retirement.
        """
        # Retire the first generation and retain its exact original retirement request.
        invocation = self.start()
        self.bind("first", invocation)
        retire = self.retirement("first", invocation)
        self.require_ok(invocation_execute(retire))
        # Only a new admitted Task with explicit resume intent can bind the same conversation.
        self.plan("second", resume="child")
        second = self.admit("second")
        bound = self.bind("second", second)
        assert bound["binding_generation"] == 2
        child = self.child_request("read", binding_generation=2)
        key = core.participant_key(child)
        before = self.state()
        assert before["participants"][key]["ack"] is None
        assert before["participants"][key]["status"] == "attached"
        assert invocation_execute(retire)["code"] == "STALE_BINDING"
        assert (
            invocation_execute(
                {
                    **retire,
                    "request_id": str(uuid.uuid4()),
                    "expected_revision": before["revision"],
                }
            )["code"]
            == "STALE_BINDING"
        )
        assert self.state() == before
        # Old effects cannot enter the new generation; its own acknowledgment remains necessary.
        assert (
            invocation_execute(
                self.child_request(
                    "tool-start",
                    binding_generation=1,
                    tool_id="old-effect",
                )
            )["code"]
            == "BINDING_MISSING"
        )
        self.child_ready(2)

    def test_early_return_classifies_child_without_readiness_then_allows_explicit_resume(
        self,
    ) -> None:
        """Retain terminal-only child identity with no invented packet or accepted effect.

        Raises:
            AssertionError: Early return is charged forever or confers child readiness.
        """
        # A projected foreground return may precede the first valid child callback.
        invocation = self.start()
        self.require_ok(invocation_execute(self.retirement("first", invocation)))
        key = core.participant_key(self.child_request("read"))
        member = self.state()["participants"][key]
        assert member["status"] == "detached"
        assert member["packet"] is None
        assert member["ack"] is None
        assert member["pending"] == {}
        assert (
            invocation_execute(self.child_request("resume", binding_generation=1))["code"]
            == "STALE_BINDING"
        )
        # A later planned resume installs a fresh independent scope and generation.
        self.plan("second", resume="child")
        second = self.admit("second")
        assert self.bind("second", second)["binding_generation"] == 2

    def test_unconsumed_replacement_releases_scope_but_consumed_plan_is_immutable(self) -> None:
        """Replace abandoned planning scope without rewriting an already admitted launch.

        Raises:
            AssertionError: Abandoned plans trap ownership or consumed intent changes.
        """
        # An unused plan may release its output reservation at the current revision.
        self.create()
        self.ready()
        self.plan("first")
        self.call(
            "cursor-plan",
            expected_revision=self.state()["revision"],
            plan_id="first",
            parent_conversation="parent-conversation",
            task_digest="a" * 64,
            packet=[],
            owned_paths=[],
        )
        assert self.state()["cursor_plans"]["first"]["owned_paths"] == []
        self.call(
            "cursor-plan",
            expected_revision=self.state()["revision"],
            plan_id="second",
            parent_conversation="parent-conversation",
            task_digest="a" * 64,
            packet=[],
            owned_paths=["context/first.md"],
        )
        # Consumption makes the exact plan immutable and preserves its charged invocation.
        self.admit("first")
        before = self.state()
        replacement = self.req(
            "cursor-plan",
            expected_revision=before["revision"],
            plan_id="first",
            parent_conversation="parent-conversation",
            task_digest="a" * 64,
            packet=[],
            owned_paths=["context/replacement.md"],
        )
        assert invocation_execute(replacement)["code"] == "REQUEST_CONFLICT"
        assert self.state() == before

    def test_active_resume_reservation_rejects_a_second_native_launch(self) -> None:
        """Prevent avoidable second charges against a known resumed conversation.

        Raises:
            AssertionError: A second resume becomes admitted while the first is pending.
        """
        # Establish a known retired conversation, then admit one callback-pending resume.
        invocation = self.start()
        self.bind("first", invocation)
        self.require_ok(invocation_execute(self.retirement("first", invocation)))
        self.plan("second", resume="child")
        self.admit("second")
        before = self.state()
        request = self.req(
            "cursor-plan",
            expected_revision=before["revision"],
            plan_id="third",
            parent_conversation="parent-conversation",
            task_digest="a" * 64,
            packet=[],
            owned_paths=["context/third.md"],
            resume_child="child",
        )
        assert invocation_execute(request)["code"] == "PENDING_OPERATION"
        assert self.state() == before

    def test_resume_cannot_write_an_older_invocations_owned_output(self) -> None:
        """Preserve historic note ownership without granting it to a fresh narrower scope.

        Raises:
            AssertionError: A fresh invocation writes an excluded historic note.
        """
        # Finish one scoped child, then bind its new invocation to a different output.
        invocation = self.start()
        self.bind("first", invocation)
        self.require_ok(invocation_execute(self.retirement("first", invocation)))
        self.plan("second", resume="child")
        second = self.admit("second")
        self.bind("second", second)
        self.child_ready(2)
        before = self.state()
        assert (
            invocation_execute(
                self.child_request(
                    "update",
                    binding_generation=2,
                    expected_revision=before["revision"],
                    path="context/first.md",
                    old_digest=None,
                    content="excluded historic output",
                    provenance={
                        "sources": self.evidence(),
                        "status": "draft",
                        "applicability": "fixture",
                    },
                )
            )["code"]
            == "NOT_OWNER"
        )
        assert self.state() == before

    def test_admitted_launch_keeps_output_reserved_until_binding_or_retirement(self) -> None:
        """Prevent another plan or generic scope from stealing a launched child's note."""
        # Admission must preserve the reservation before the first child callback.
        invocation = self.start()
        before = self.state()
        requests = [
            self.req(
                "cursor-plan",
                expected_revision=before["revision"],
                plan_id="second",
                parent_conversation="parent-conversation",
                task_digest="a" * 64,
                packet=[],
                owned_paths=["context/first.md"],
            ),
            self.req(
                "scope",
                expected_revision=before["revision"],
                target_participant=self.base["coordinator"],
                packet=[],
                owned_paths=["context/first.md"],
            ),
        ]
        for request in requests:
            assert invocation_execute(request)["code"] == "NOT_OWNER"
            assert self.state() == before
        # Terminal-only retirement releases an unused reservation without inventing ownership.
        self.require_ok(invocation_execute(self.retirement("first", invocation)))
        requests[0].update(expected_revision=self.state()["revision"], request_id=str(uuid.uuid4()))
        self.require_ok(invocation_execute(requests[0]))

    def test_new_parent_generation_can_replace_an_unconsumed_plan(self) -> None:
        """Recover unused reservations after supported parent detachment and resumption."""
        # An unused plan has no external effect and must not strand a future coordinator.
        self.create()
        self.ready()
        self.plan("first")
        self.call("detach", evidence=self.evidence())
        resumed = self.call("resume")
        self.base["binding_generation"] = resumed["binding_generation"]
        self.ready()
        # Explicit current-revision replacement adopts only unconsumed planning intent.
        self.call(
            "cursor-plan",
            expected_revision=self.state()["revision"],
            plan_id="first",
            parent_conversation="parent-conversation",
            task_digest="b" * 64,
            packet=[],
            owned_paths=[],
        )
        plan = self.state()["cursor_plans"]["first"]
        assert plan["parent_generation"] == 2
        assert plan["task_digest"] == "b" * 64
        assert plan["owned_paths"] == []
