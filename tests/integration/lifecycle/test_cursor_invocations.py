"""Verify atomic planned Cursor invocations over disposable local issue stores."""

from __future__ import annotations

import concurrent.futures
import copy
import threading
import uuid

import pytest

from nplet.lifecycle import task_workspace as core
from tests.support import Fixture

pytestmark = pytest.mark.integration


class CursorInvocationTests(Fixture):
    """Exercise independent child scope, effect accounting and exact retirement."""

    def setUp(self) -> None:
        """Register disposable stores and select the Cursor coordinator identity.

        Raises:
            AssertionError: Disposable store registration fails.
        """
        # Reuse normal disposable store setup, then choose the issue's actual host.
        super().setUp()
        self.base["host"] = "cursor"
        self.base["coordinator"] = core.participant_key(self.base)

    def plan(self, identifier: str, resume: str | None = None) -> dict:
        """Create one current launch plan with an independent output note.

        Args:
            identifier: Unique plan ID and managed-note suffix.
            resume: Previously retired conversation, if explicitly resuming.

        Returns:
            Successful planning response.

        Raises:
            AssertionError: The plan is refused.
        """
        # Name only a packet template and a disjoint managed output.
        return self.call(
            "cursor-plan",
            expected_revision=self.state()["revision"],
            plan_id=identifier,
            parent_conversation="parent-conversation",
            task_digest="a" * 64,
            packet=[],
            owned_paths=["context/" + identifier + ".md"],
            resume_child=resume,
        )

    def admit(self, identifier: str) -> dict:
        """Consume a plan through an exact synthetic native Task admission.

        Args:
            identifier: Previously installed plan ID.

        Returns:
            Successful invocation admission.

        Raises:
            AssertionError: Admission fails.
        """
        # Keep normalized hook ID separate from exact native text containing a newline.
        return self.call(
            "cursor-admit",
            expected_revision=self.state()["revision"],
            plan_id=identifier,
            parent_conversation="parent-conversation",
            task_digest="a" * 64,
            tool_id="tool-" + identifier,
            native_call="native\n" + identifier,
        )

    def child_request(self, operation: str, child: str = "child", **fields: object) -> dict:
        """Build a request from one genuine synthetic child identity.

        Args:
            operation: Child lifecycle operation.
            child: Distinct conversation identity under this parent.
            **fields: Explicit request-specific arguments.

        Returns:
            A fresh request with no borrowed parent generation or coordinator field.
        """
        # Derive the child session exactly as the internal adapter contract requires.
        return self.req(
            operation,
            session_id=self.base["session_id"] + "/agent/" + child,
            coordinator=None,
            binding_generation=fields.pop("binding_generation", None),
            **fields,
        )

    def bind(self, identifier: str, invocation: dict, child: str = "child") -> dict:
        """Bind one observed callback to its admitted Task.

        Args:
            identifier: Plan ID identifying the parent tool.
            invocation: Admission response containing invocation_id.
            child: Actual synthetic conversation.

        Returns:
            Successful child binding.

        Raises:
            AssertionError: Binding is rejected.
        """
        # Persist the observed child callback link without inventing parent generation equivalence.
        return self.require_ok(
            core.execute(
                self.child_request(
                    "cursor-bind-child",
                    child,
                    invocation_id=invocation["invocation_id"],
                    tool_id="tool-" + identifier,
                    child_conversation=child,
                    native_generation="actual-child-native-generation",
                )
            )
        )

    def child_ready(self, generation: int, child: str = "child") -> None:
        """Acknowledge the child's own assigned packet and verify readiness.

        Args:
            generation: Current framework generation returned by binding.
            child: Exact child conversation.

        Raises:
            AssertionError: Packet acknowledgment or readiness fails.
        """
        # Delivery acknowledgment belongs to this child, never its parent.
        read = self.require_ok(
            core.execute(self.child_request("read", child, binding_generation=generation))
        )
        self.require_ok(
            core.execute(
                self.child_request(
                    "acknowledge",
                    child,
                    binding_generation=generation,
                    packet_digest=read["packet_digest"],
                )
            )
        )
        self.require_ok(
            core.execute(self.child_request("ready", child, binding_generation=generation))
        )

    def terminal(self, identifier: str, child: str = "child") -> dict:
        """Project one validated synthetic foreground result.

        Args:
            identifier: Plan ID naming the exact original native call.
            child: Returned conversation identity.

        Returns:
            Metadata accepted only as internal synthetic evidence in these tests.
        """
        return {
            "parent_conversation": "parent-conversation",
            "native_call": "native\n" + identifier,
            "storage_key": "exact-row\n" + identifier,
            "child_conversation": child,
            "kind": "foreground-success",
            "is_background": False,
            "background_reason": 0,
            "duration_ms": 12,
            "binary_sha256": "b" * 64,
            "executor_sha256": "c" * 64,
            "workbench_sha256": "d" * 64,
        }

    def retirement(self, identifier: str, invocation: dict, child: str = "child") -> dict:
        """Build a fresh parent retirement at the current issue revision.

        Args:
            identifier: Plan ID naming the exact call.
            invocation: Admitted invocation response.
            child: Returned child conversation.

        Returns:
            A retirement request ready for deliberate success or failure assertions.
        """
        return self.req(
            "cursor-retire",
            expected_revision=self.state()["revision"],
            invocation_id=invocation["invocation_id"],
            result=self.terminal(identifier, child),
        )

    def start(self) -> dict:
        """Create a ready parent and admit one planned Task.

        Returns:
            The first admitted invocation.

        Raises:
            AssertionError: Creation, readiness, planning or admission fails.
        """
        # Establish the parent, then reserve exactly one planned foreground call.
        self.create()
        self.ready()
        self.plan("first")
        return self.admit("first")

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
        assert core.execute(update)["code"] == "NOT_READY"
        assert (
            core.execute(self.child_request("tool-start", binding_generation=1, tool_id="effect"))[
                "code"
            ]
            == "NOT_READY"
        )
        assert self.state() == state
        self.child_ready(1)
        update.update(expected_revision=self.state()["revision"], request_id=str(uuid.uuid4()))
        self.require_ok(core.execute(update))
        # A real admitted effect blocks retirement and generic completion cannot release Task.
        self.require_ok(
            core.execute(self.child_request("tool-start", binding_generation=1, tool_id="effect"))
        )
        before = self.state()
        assert core.execute(self.retirement("first", invocation))["code"] == "PENDING_OPERATION"
        assert (
            core.execute(self.req("tool-complete", tool_id="tool-first", completed=True))["code"]
            == "REQUEST_CONFLICT"
        )
        assert self.state() == before
        self.require_ok(
            core.execute(
                self.child_request(
                    "tool-complete", binding_generation=1, tool_id="effect", completed=True
                )
            )
        )
        # One retirement commit both detaches the child and releases the parent Task.
        retire = self.retirement("first", invocation)
        first = self.require_ok(core.execute(retire))
        after = self.state()
        assert after["participants"][key]["status"] == "detached"
        assert after["participants"][key]["ack"] is None
        assert after["participants"][parent]["pending"] == {}
        assert after["cursor_invocations"][invocation["invocation_id"]]["phase"] == "retired"
        assert core.execute(retire) == first
        assert self.state() == after
        # Fresh-ID exact result duplicates are event-free; contradictions preserve all state.
        duplicate = {**retire, "request_id": str(uuid.uuid4())}
        self.require_ok(core.execute(duplicate))
        assert self.state() == after
        duplicate["result"] = {**retire["result"], "binary_sha256": "e" * 64}
        assert core.execute(duplicate)["code"] == "REQUEST_CONFLICT"
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
            assert core.execute(request)["code"] == (
                "PENDING_OPERATION"
                if request["operation"] == "transfer-coordinator"
                else "NOT_OWNER"
            )
            assert self.state() == before
        # Retirement fences every later generic route without inventing new generations.
        self.require_ok(core.execute(self.retirement("first", invocation)))
        before = self.state()
        for operation in ("attach", "resume", "bind", "ready", "read", "acknowledge", "tool-start"):
            result = core.execute(
                self.child_request(
                    operation,
                    binding_generation=1,
                    tool_id="late",
                    packet_digest=core.sha(core.canonical([])),
                )
            )
            assert result["code"] == "STALE_BINDING"
            assert self.state() == before

    def test_resume_requires_new_plan_generation_and_old_completion_cannot_retire_it(self) -> None:
        """Bind an explicitly resumed child freshly and fence even exact old request replay.

        Raises:
            AssertionError: A resumed generation inherits readiness or accepts old retirement.
        """
        # Retire the first generation and retain its exact original retirement request.
        invocation = self.start()
        self.bind("first", invocation)
        retire = self.retirement("first", invocation)
        self.require_ok(core.execute(retire))
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
        assert core.execute(retire)["code"] == "STALE_BINDING"
        assert (
            core.execute(
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
            core.execute(
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
        self.require_ok(core.execute(self.retirement("first", invocation)))
        key = core.participant_key(self.child_request("read"))
        member = self.state()["participants"][key]
        assert member["status"] == "detached"
        assert member["packet"] is None
        assert member["ack"] is None
        assert member["pending"] == {}
        assert (
            core.execute(self.child_request("resume", binding_generation=1))["code"]
            == "STALE_BINDING"
        )
        # A later planned resume installs a fresh independent scope and generation.
        self.plan("second", resume="child")
        second = self.admit("second")
        assert self.bind("second", second)["binding_generation"] == 2

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
            assert core.execute(bad)["code"] == code
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
            return core.execute(request)

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

    def test_source_preparation_and_stale_owned_update_share_the_invocation_fence(self) -> None:
        """Retain pre-readiness source effects and deny writes from a stale child packet.

        Raises:
            AssertionError: Preparation is lost or stale sources permit child work.
        """
        # Plan the actual required source before binding; later scope cannot broaden it.
        self.create()
        self.ready()
        packet = [
            {
                "id": "roadmap",
                "locator": "roadmap.md",
                "sha256": self.state()["files"]["roadmap.md"],
                "required": True,
                "authority": "fixture",
                "reason": "source-test",
                "stage": "implementation",
                "reader": "cursor-child",
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
        read = self.require_ok(core.execute(self.child_request("read", binding_generation=1)))
        # Preparation enters pending accounting without granting ordinary readiness.
        self.require_ok(
            core.execute(
                self.child_request(
                    "source-read-start",
                    binding_generation=1,
                    tool_id="source",
                    source_locator="roadmap.md",
                    packet_digest=read["packet_digest"],
                )
            )
        )
        before = self.state()
        assert core.execute(self.retirement("first", invocation))["code"] == "PENDING_OPERATION"
        assert self.state() == before
        self.require_ok(
            core.execute(
                self.child_request(
                    "tool-complete",
                    binding_generation=1,
                    tool_id="source",
                    completed=True,
                )
            )
        )
        self.child_ready(1)
        # A supported parent update changes required bytes without an untracked filesystem edit.
        state = self.state()
        self.call(
            "update",
            expected_revision=state["revision"],
            path="roadmap.md",
            old_digest=state["files"]["roadmap.md"],
            content="revised required source\n",
            provenance={
                "sources": self.evidence(),
                "status": "draft",
                "applicability": "fixture",
            },
        )
        before = self.state()
        assert (
            core.execute(
                self.child_request(
                    "tool-start",
                    binding_generation=1,
                    tool_id="stale-effect",
                )
            )["code"]
            == "SOURCE_STALE"
        )
        assert (
            core.execute(
                self.child_request(
                    "update",
                    binding_generation=1,
                    expected_revision=before["revision"],
                    path="context/first.md",
                    old_digest=None,
                    content="forbidden stale output",
                    provenance={
                        "sources": self.evidence(),
                        "status": "draft",
                        "applicability": "fixture",
                    },
                )
            )["code"]
            == "SOURCE_STALE"
        )
        assert self.state() == before

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
        assert core.execute(replacement)["code"] == "REQUEST_CONFLICT"
        assert self.state() == before

    def test_active_resume_reservation_rejects_a_second_native_launch(self) -> None:
        """Prevent avoidable second charges against a known resumed conversation.

        Raises:
            AssertionError: A second resume becomes admitted while the first is pending.
        """
        # Establish a known retired conversation, then admit one callback-pending resume.
        invocation = self.start()
        self.bind("first", invocation)
        self.require_ok(core.execute(self.retirement("first", invocation)))
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
        assert core.execute(request)["code"] == "PENDING_OPERATION"
        assert self.state() == before

    def test_resume_cannot_write_an_older_invocations_owned_output(self) -> None:
        """Preserve historic note ownership without granting it to a fresh narrower scope.

        Raises:
            AssertionError: A fresh invocation writes an excluded historic note.
        """
        # Finish one scoped child, then bind its new invocation to a different output.
        invocation = self.start()
        self.bind("first", invocation)
        self.require_ok(core.execute(self.retirement("first", invocation)))
        self.plan("second", resume="child")
        second = self.admit("second")
        self.bind("second", second)
        self.child_ready(2)
        before = self.state()
        assert (
            core.execute(
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
            assert core.execute(request)["code"] == "NOT_OWNER"
            assert self.state() == before
        # Terminal-only retirement releases an unused reservation without inventing ownership.
        self.require_ok(core.execute(self.retirement("first", invocation)))
        requests[0].update(expected_revision=self.state()["revision"], request_id=str(uuid.uuid4()))
        self.require_ok(core.execute(requests[0]))

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
            assert core.execute(request)["code"] == code
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
        self.require_ok(core.execute(self.retirement("first", invocation)))
        # The same coordinator refresh cannot revive an already retired invocation.
        before = self.state()
        assert (
            core.execute(
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
    case = CursorInvocationTests("test_generic_routes_cannot_bypass_invocation_lifetime")
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
        assert core.execute(request)["code"] == "RECOVERY_REQUIRED"
        monkeypatch.setattr(core, "FAILPOINT", None)
        # An exact retry rolls forward or restages once through the existing protocol.
        first = case.require_ok(core.execute(request))
        committed = case.state()
        assert committed["seq"] == before["seq"] + 1
        events = (case.root / ".task/TEST-1/events.jsonl").read_bytes()
        assert core.validate_events(events)[0] == committed["seq"]
        assert core.execute(request) == first
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
    case = CursorInvocationTests("test_generic_routes_cannot_bypass_invocation_lifetime")
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
        assert core.execute(request)["code"] == "RECOVERY_REQUIRED"
        committed = case.state()
        assert committed["participants"][child_key]["status"] == "attached"
        monkeypatch.setattr(core.Store, "save_binding", real_save)
        # The committed invocation remains authoritative while its binding is reconstructed.
        result = case.require_ok(core.execute(request))
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
