"""Check native UUID cohorts and explicit child ownership in disposable issue stores."""

import shlex
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from nplet.adapters import codex, common
from nplet.adapters import codex_children as children
from nplet.lifecycle import task_workspace as core
from tests.support import Fixture

pytestmark = pytest.mark.integration


class ChildTests(Fixture):
    """Exercise child registration and bounded commands with the real lifecycle core."""

    def launch(self, number: int) -> dict:
        """Build a distinct native launch with no implied output assignment.

        Args:
            number: Distinct call and task-name suffix.

        Returns:
            Parent pre-tool fixture.
        """
        return {
            "session_id": "coordinator",
            "cwd": str(self.root),
            "hook_event_name": "PreToolUse",
            "tool_name": children.SPAWN,
            "tool_use_id": f"spawn-{number}",
            "tool_input": {
                "task_name": f"worker-{number}",
                "message": "opaque",
                "fork_turns": "none",
            },
        }

    def child(self) -> dict:
        """Construct a normalized synthetic native UUID under the fixture parent.

        Returns:
            Child identity envelope with no lifecycle binding yet.
        """
        return children.identity(
            {
                "session_id": "coordinator",
                "cwd": str(self.root),
                "agent_id": str(uuid.uuid4()),
                "agent_type": "default",
                "hook_event_name": "SubagentStart",
            }
        )

    def command(self, event: dict, request: dict) -> dict:
        """Wrap a concrete lifecycle request in its canonical native shell envelope.

        Args:
            event: Normalized child identity.
            request: Child lifecycle request.

        Returns:
            Native pre-tool command fixture.
        """
        return {
            **event,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": str(uuid.uuid4()),
            "tool_input": {"command": common.bootstrap_command(request, "codex")},
        }

    def test_concurrent_out_of_order_readers_never_receive_launch_scopes(self) -> None:
        """Join distinct native children concurrently without pairing by launch order.

        Raises:
            AssertionError: A duplicate consumes a slot or any child receives write ownership.
        """
        # Only admitted spawn calls provide capacity; an unrelated pending tool does not.
        self.create()
        self.ready()
        self.call("tool-start", tool_id="unrelated")
        with pytest.raises(core.WorkspaceError) as failure:
            children.start(self.child())
        assert failure.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
        for number in range(3):
            children.admit_spawn(self.launch(number))
        peers = [self.child() for _ in range(3)]
        # Isolate cohort semantics from filesystem speed while preserving real locks and sleeps.
        # The production attempt cap still bounds each join; deadline behavior has its own test.
        join_clock = SimpleNamespace(monotonic=lambda: 0.0, sleep=common.time.sleep)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(children, "time", join_clock)
            patch.setattr(common, "time", join_clock)
            # Reverse arrival order and overlap real native registration transactions.
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(children.start, reversed(peers)))
        assert all("Reader only" in result for result in results)
        children.start(peers[0])
        with pytest.raises(core.WorkspaceError) as failure:
            children.start(self.child())
        assert failure.value.code == "HOST_UNSUPPORTED_CHILD_IDENTITY"
        state = self.state()
        assert len(state["participants"]) == 4
        assert set(state["owners"].values()) == {self.base["coordinator"]}
        for peer in peers:
            key = core.participant_key({"host": "codex", "session_id": peer["session_id"]})
            assert state["participants"][key]["ack"] is None

    def test_child_commands_require_exact_identity_readiness_and_ownership(self) -> None:
        """Deny parent impersonation, providers and writes before explicit ready scope.

        Raises:
            AssertionError: A forbidden command passes or an owned ready update fails.
        """
        # Join one reader without granting coordinator authority or write paths.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        children.start(peer)
        request = common.request_for(peer, "read", "codex")
        key = core.participant_key(request)
        children.child_pre(self.command(peer, request))
        # No shell, provider or parent-session mutation is a child bootstrap exception.
        before = self.state()
        for event, code in (
            (
                {**peer, "tool_name": "apply_patch", "tool_input": {"command": "patch"}},
                "HOST_UNSUPPORTED_TOOL",
            ),
            (
                {**peer, "tool_name": "mcp__codex_apps__linear_save_issue", "tool_input": {}},
                "HOST_UNSUPPORTED_TOOL",
            ),
            (self.command(peer, {**request, "session_id": "coordinator"}), "HOST_UNSUPPORTED_TOOL"),
            (self.command(peer, {**request, "operation": "scope"}), "NOT_OWNER"),
            (
                {**peer, "tool_name": "Bash", "tool_input": {"command": "touch sentinel"}},
                "HOST_UNSUPPORTED_TOOL",
            ),
        ):
            # Supply native call identity so each case isolates its intended authority failure.
            event.setdefault("tool_use_id", str(uuid.uuid4()))
            with pytest.raises(core.WorkspaceError, match=code):
                children.child_pre(event)
            assert self.state() == before
            assert not (self.root / "sentinel").exists()
        # Generic notes cannot acquire managed-output authority through the free effect policy.
        for tool, arguments in [
            ("Write", {"file_path": str(self.root / "outside.md"), "content": "note"}),
            ("Bash", {"command": "touch -- outside.md"}),
        ]:
            with pytest.raises(core.WorkspaceError, match="HOST_UNSUPPORTED_TOOL"):
                children.child_pre(
                    {
                        **peer,
                        "tool_name": tool,
                        "tool_use_id": str(uuid.uuid4()),
                        "tool_input": arguments,
                    }
                )
            assert self.state() == before
        # Explicit coordinator ownership is necessary, and re-scoping invalidates readiness.
        self.call(
            "scope",
            expected_revision=self.state()["revision"],
            target_participant=key,
            packet=[],
            owned_paths=["context/child.md"],
        )
        update = {
            **request,
            "operation": "update",
            "path": "context/child.md",
            "expected_revision": self.state()["revision"],
            "old_digest": None,
            "content": "owned output",
            "provenance": {"sources": self.evidence(), "applicability": "test", "status": "draft"},
        }
        with pytest.raises(core.WorkspaceError, match="NOT_READY"):
            children.child_pre(self.command(peer, update))
        acknowledged = core.execute(
            {
                **request,
                "request_id": str(uuid.uuid4()),
                "operation": "acknowledge",
                "packet_digest": core.sha(core.canonical([])),
            }
        )
        assert acknowledged["ok"]
        update.update(request_id=str(uuid.uuid4()), expected_revision=self.state()["revision"])
        children.child_pre(self.command(peer, update))
        assert core.execute(update)["ok"]
        with pytest.raises(core.WorkspaceError, match="NOT_OWNER"):
            children.child_pre(self.command(peer, {**update, "path": "roadmap.md"}))

    def test_bad_identity_and_unready_parent_do_not_reserve_children(self) -> None:
        """Reject malformed IDs and unready launch parents before writing receipts.

        Raises:
            AssertionError: An invalid identity or unready parent is admitted.
        """
        # Native metadata is validated independently from model-supplied command fields.
        self.create()
        before = self.state()
        for agent in ("", False, "forged/agent/path", "not-a-uuid"):
            with pytest.raises(core.WorkspaceError, match="HOST_UNSUPPORTED_CHILD_IDENTITY"):
                children.identity(
                    {"session_id": "coordinator", "cwd": str(self.root), "agent_id": agent}
                )
        with pytest.raises(core.WorkspaceError, match="SCOPE_MISSING"):
            children.admit_spawn(self.launch(1))
        assert self.state() == before
        assert not list((self.root / ".task/.bindings").glob("*.codex-children-*.json"))

    def test_actual_source_bytes_are_required_before_acknowledgment(self) -> None:
        """Require a correlated exact source response, not merely a read descriptor.

        Raises:
            AssertionError: Missing, truncated or foreign delivery permits acknowledgment.
        """
        # Join an unacknowledged roadmap reader and build its canonical acknowledgment.
        self.create()
        self.ready()
        launch = self.launch(1)
        children.admit_spawn(launch)
        children.parent_post(
            {
                **launch,
                "hook_event_name": "PostToolUse",
                "tool_response": '{"task_name":"/root/worker-1"}',
            }
        )
        peer = self.child()
        children.start(peer)
        request = common.request_for(peer, "read", "codex")
        packet = core.execute(request)
        ack = {**request, "operation": "acknowledge", "packet_digest": packet["packet_digest"]}
        with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
            children.child_pre(self.command(peer, ack))
        # A truncated native response cannot establish exact source delivery.
        path = self.root / ".task/TEST-1/roadmap.md"
        pre = {
            **peer,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "source",
            "tool_input": {"command": "cat -- " + shlex.quote(str(path))},
        }
        children.child_pre(pre)
        children.child_post({**pre, "hook_event_name": "PostToolUse", "tool_response": "truncated"})
        with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
            children.child_pre(self.command(peer, ack))
        # A fresh read with exact bytes unlocks acknowledgment only for the same child.
        pre.update(tool_use_id="source-retry")
        children.child_pre(pre)
        children.child_post(
            {**pre, "hook_event_name": "PostToolUse", "tool_response": path.read_text()}
        )
        children.child_pre(self.command(peer, ack))
        assert core.execute(ack)["ok"]
        assert "spawn-1" not in self.state()["participants"][self.base["coordinator"]]["pending"]
        with children.registry(children.parent_request(launch)) as (_, _, state):
            assert state["launches"]["spawn-1"]["outcome"] == "target_observed"

    def test_startup_commands_bootstrap_the_actual_reader_packet(self) -> None:
        """Execute emitted commands without guessing paths or operation names.

        Raises:
            AssertionError: Instructions omit delivery or fail to establish readiness.
        """
        # Create a real reader and extract only its numbered executable instructions.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        context = children.start(peer)
        commands = [line.split(". ", 1)[1] for line in context.splitlines()[1:]]
        assert len(commands) == 4
        assert commands[1] == "cat -- " + shlex.quote(str(self.root / ".task/TEST-1/roadmap.md"))
        request_ids = set()
        # Feed each command through the same admission and delivery boundaries as the host.
        for command in commands:
            event = {
                **peer,
                "tool_name": "Bash",
                "tool_use_id": str(uuid.uuid4()),
                "tool_input": {"command": command},
            }
            children.child_pre(event)
            if command.startswith("cat -- "):
                content = (self.root / ".task/TEST-1/roadmap.md").read_text()
                children.child_post({**event, "tool_response": content})
            else:
                request = common.bootstrap_request(
                    command, "codex", common.PYTHON, common.LIFECYCLE
                )
                request_ids.add(request["request_id"])
                assert core.execute(request)["ok"]
        assert len(request_ids) == 3
        assert core.execute(common.request_for(peer, "ready", "codex"))["ok"]

    def test_interrupted_delivery_stays_unacknowledged_and_sibling_recovers(self) -> None:
        """Retain unfinished reads through cancellation without affecting a ready sibling.

        Raises:
            AssertionError: An ending fabricates delivery or changes sibling readiness.
        """
        # Join two distinct readers and begin a source read without its final callback.
        self.create()
        self.ready()
        for number in range(2):
            children.admit_spawn(self.launch(number))
        stopped, sibling = self.child(), self.child()
        children.start(stopped)
        sibling_context = children.start(sibling)
        path = self.root / ".task/TEST-1/roadmap.md"
        read = {
            **stopped,
            "tool_name": "Bash",
            "tool_use_id": "unfinished",
            "tool_input": {"command": "cat -- " + shlex.quote(str(path))},
        }
        children.child_pre(read)
        # Duplicate native endings are observations, not successful read completions.
        ending = {**stopped, "hook_event_name": "SubagentStop", "turn_id": "canceled-turn"}
        children.child_end(ending)
        children.child_end(ending)
        request = common.request_for(stopped, "read", "codex")
        packet = core.execute(request)
        ack = {**request, "operation": "acknowledge", "packet_digest": packet["packet_digest"]}
        with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
            children.child_pre(self.command(stopped, ack))
        # Complete the sibling's independent instructions while the canceled read stays unknown.
        for line in sibling_context.splitlines()[1:]:
            command = line.split(". ", 1)[1]
            event = {
                **sibling,
                "tool_name": "Bash",
                "tool_use_id": str(uuid.uuid4()),
                "tool_input": {"command": command},
            }
            children.child_pre(event)
            if command.startswith("cat -- "):
                children.child_post({**event, "tool_response": path.read_text()})
            else:
                assert core.execute(
                    common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE)
                )["ok"]
        assert core.execute(common.request_for(sibling, "ready", "codex"))["ok"]
        with children.registry(children.parent_request(stopped)) as (_, _, state):
            member = state["children"][core.participant_key(request)]
            assert member["last_ending"] == {"event": "SubagentStop", "turn_id": "canceled-turn"}
            assert "completed" not in member["source_reads"]["unfinished"]
        # A fresh read on the same native identity supports explicit follow-up recovery.
        children.child_pre({**read, "tool_use_id": "retry"})
        children.child_post({**read, "tool_use_id": "retry", "tool_response": path.read_text()})
        children.child_pre(self.command(stopped, ack))
        assert core.execute(ack)["ok"]

    def test_join_retry_exhaustion_can_recover_on_replayed_start(self) -> None:
        """Bound contention retries and preserve the reserved identity for explicit recovery.

        Raises:
            AssertionError: Exhaustion grants a binding or a retry consumes another cohort slot.
        """
        # Force contention at the actual core join seam, keeping the real registry and store.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        execute = core.execute
        attempts = []

        def conflict(request: dict) -> dict:
            """Return a bounded contention outcome only for the attempted child join.

            Args:
                request: Actual lifecycle transaction request.

            Returns:
                A conflict for joins, otherwise the real core result.
            """
            if request["operation"] == "join":
                attempts.append(request["request_id"])
                return {"ok": False, "code": "REVISION_CONFLICT"}
            return execute(request)

        # This scenario exhausts attempts, not elapsed time on a slower filesystem.
        before = self.state()
        join_clock = SimpleNamespace(monotonic=lambda: 0.0, sleep=common.time.sleep)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(children, "time", join_clock)
            patch.setattr(common, "time", join_clock)
            patch.setattr(core, "execute", conflict)
            with pytest.raises(core.WorkspaceError) as failure:
                children.start(peer)
        assert failure.value.code == "REVISION_CONFLICT"
        assert len(attempts) == common.CHILD_JOIN_ATTEMPTS
        assert self.state() == before
        key = core.participant_key({"host": "codex", "session_id": peer["session_id"]})
        assert key not in self.state()["participants"]
        # Replay the same observed start after contention clears; no new launch is fabricated.
        assert "Reader only" in children.start(peer)
        assert self.state()["participants"][key]["ack"] is None
        with children.registry(children.parent_request(peer)) as (_, _, state):
            assert len(state["children"]) == 1
            assert state["children"][key]["status"] == "joined"

    def test_foreign_and_late_packet_deliveries_cannot_replace_current_evidence(self) -> None:
        """Keep source completion isolated by child identity and assigned packet digest.

        Raises:
            AssertionError: Foreign or obsolete delivery satisfies a current acknowledgment.
        """
        # Join siblings and admit a source call only for the first child's original packet.
        self.create()
        self.ready()
        for number in range(2):
            children.admit_spawn(self.launch(number))
        peer, sibling = self.child(), self.child()
        children.start(peer)
        children.start(sibling)
        request = common.request_for(peer, "read", "codex")
        packet = core.execute(request)
        path = self.root / ".task/TEST-1/roadmap.md"
        pre = {
            **peer,
            "tool_name": "Bash",
            "tool_use_id": "old-source",
            "tool_input": {"command": "cat -- " + shlex.quote(str(path))},
        }
        children.child_pre(pre)
        children.child_pre({**pre, "tool_use_id": "late-source"})
        before = self.state()
        # A sibling identity carrying the original call ID cannot finish the original receipt.
        children.child_post(
            {
                **pre,
                "session_id": sibling["session_id"],
                "agent_id": sibling["agent_id"],
                "tool_response": path.read_text(),
            }
        )
        ack = {**request, "operation": "acknowledge", "packet_digest": packet["packet_digest"]}
        with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
            children.child_pre(self.command(peer, ack))
        assert self.state() == before
        # Re-scope with a new packet digest while keeping the source bytes unchanged.
        key = core.participant_key(request)
        refs = [
            {**item, "reason": "refreshed"} for item in self.state()["participants"][key]["packet"]
        ]
        self.call(
            "scope", expected_revision=self.state()["revision"], target_participant=key, packet=refs
        )
        current = core.execute(request)
        ack["packet_digest"] = current["packet_digest"]
        children.child_post({**pre, "tool_response": path.read_text()})
        with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
            children.child_pre(self.command(peer, ack))
        # Fresh delivery recovers; a duplicate old completion cannot erase that new proof.
        fresh = {**pre, "tool_use_id": "current-source"}
        children.child_pre(fresh)
        children.child_post({**fresh, "tool_response": path.read_text()})
        children.child_post(
            {**pre, "tool_use_id": "late-source", "tool_response": path.read_text()}
        )
        children.child_pre(self.command(peer, ack))
        assert core.execute(ack)["ok"]
        sibling_key = core.participant_key({"host": "codex", "session_id": sibling["session_id"]})
        assert self.state()["participants"][sibling_key] == before["participants"][sibling_key]

    def test_settled_source_receipts_do_not_exhaust_followup_capacity(self) -> None:
        """Compact settled receipts while retaining exact current delivery evidence.

        Raises:
            AssertionError: Completed reads consume the unresolved-read limit permanently.
        """
        # Exercise repeated completed reads through the real admission and receipt boundaries.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        children.start(peer)
        path = self.root / ".task/TEST-1/roadmap.md"
        for index in range(4):
            event = {
                **peer,
                "tool_name": "Bash",
                "tool_use_id": f"read-{index}",
                "tool_input": {"command": "cat -- " + shlex.quote(str(path))},
            }
            children.child_pre(event)
            children.child_post({**event, "tool_response": path.read_text()})
        # Only the newest settled receipt remains; its digest still proves actual delivery.
        request = common.request_for(peer, "read", "codex")
        with children.registry(children.parent_request(peer)) as (_, _, state):
            reads = state["children"][core.participant_key(request)]["source_reads"]
            assert set(reads) == {"read-3"}
            assert reads["read-3"]["content_sha256"] == core.sha(path.read_bytes())

    def test_linked_checkout_delivers_canonical_managed_source(self) -> None:
        """Use the registered main store for managed sources from a linked checkout.

        Raises:
            AssertionError: Instructions use an issue-view symlink or cannot establish readiness.
        """
        # Attach the same coordinator in the registered linked checkout before spawning.
        self.create()
        self.ready()
        self.call("attach", worktree=str(self.other))
        launch = {**self.launch(1), "cwd": str(self.other)}
        children.admit_spawn(launch)
        peer = {**self.child(), "cwd": str(self.other)}
        context = children.start(peer)
        commands = [line.split(". ", 1)[1] for line in context.splitlines()[1:]]
        path = self.root / ".task/TEST-1/roadmap.md"
        assert commands[1] == "cat -- " + shlex.quote(str(path))
        # Execute the supplied bootstrap sequence under the linked native child identity.
        for command in commands:
            event = {
                **peer,
                "tool_name": "Bash",
                "tool_use_id": str(uuid.uuid4()),
                "tool_input": {"command": command},
            }
            children.child_pre(event)
            if command.startswith("cat -- "):
                children.child_post({**event, "tool_response": path.read_text()})
            else:
                assert core.execute(
                    common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE)
                )["ok"]

    def test_unresolved_read_limit_preserves_uncertainty_and_recovers_on_completion(self) -> None:
        """Bound outstanding native deliveries without blocking recovery after a final result.

        Raises:
            AssertionError: Exhaustion discards unknown work or retains settled capacity.
        """
        # Shrink only the resource limit while retaining real admission and persistent receipts.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        children.start(peer)
        path = self.root / ".task/TEST-1/roadmap.md"
        base = {
            **peer,
            "tool_name": "Bash",
            "tool_input": {"command": "cat -- " + shlex.quote(str(path))},
        }
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(children, "MAX_PENDING_READS", 2)
            children.child_pre({**base, "tool_use_id": "one"})
            children.child_pre({**base, "tool_use_id": "two"})
            with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
                children.child_pre({**base, "tool_use_id": "three"})
            # A genuine completion frees a slot; the other unknown read remains retained.
            children.child_post({**base, "tool_use_id": "one", "tool_response": path.read_text()})
            children.child_pre({**base, "tool_use_id": "three"})
        request = common.request_for(peer, "read", "codex")
        with children.registry(children.parent_request(peer)) as (_, _, state):
            reads = state["children"][core.participant_key(request)]["source_reads"]
            assert reads["one"]["completed"]
            assert "completed" not in reads["two"]
            assert "completed" not in reads["three"]

    def test_unfinished_source_read_blocks_coordinator_reconciliation(self) -> None:
        """Keep admitted pre-readiness work authoritative through stop and reconciliation.

        Raises:
            AssertionError: Reconciliation retires a reader with an unfinished native read.
        """
        # Join an unready child and admit one exact source command without completion.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        children.start(peer)
        request = common.request_for(peer, "read", "codex")
        key = core.participant_key(request)
        path = self.root / ".task/TEST-1/roadmap.md"
        event = {
            **peer,
            "tool_name": "Bash",
            "tool_use_id": "unfinished-source",
            "tool_input": {"command": "cat -- " + shlex.quote(str(path))},
        }
        children.child_pre(event)
        children.child_end({**peer, "hook_event_name": "SubagentStop", "turn_id": "stopped"})
        before = self.state()
        # An accountable coordinator cannot bypass outstanding work with reconciliation.
        result = core.execute(
            self.req(
                "reconcile-participant",
                expected_revision=before["revision"],
                target_participant=key,
                target_generation=request["binding_generation"],
                evidence=self.evidence(),
            )
        )
        assert result["code"] == "PENDING_OPERATION"
        assert self.state() == before
        assert before["participants"][key]["pending"] == {
            "unfinished-source": {"status": "pending"}
        }
        # Only the correlated final callback settles this participant's work.
        children.child_post({**event, "tool_response": path.read_text()})
        assert self.state()["participants"][key]["pending"] == {}
        assert "spawn-1" in self.state()["participants"][self.base["coordinator"]]["pending"]
        self.call(
            "reconcile-participant",
            expected_revision=self.state()["revision"],
            target_participant=key,
            target_generation=request["binding_generation"],
            evidence=self.evidence(),
        )
        assert self.state()["participants"][key]["status"] == "detached"

    def test_ambiguous_launch_failures_preserve_capacity_names_and_delayed_starts(self) -> None:
        """Keep uncertain launch outcomes without misreporting capacity as invalid identity.

        Raises:
            AssertionError: An error frees uncertain capacity or admits replayed extra work.
        """
        # Complete one failed-looking native call without inventing no-child semantics.
        self.create()
        self.ready()
        first = self.launch(1)
        second = self.launch(2)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(children, "MAX_LAUNCHES", 2)
            children.admit_spawn(first)
            children.parent_post({**first, "tool_response": {"error": "launch failed"}})
            before = self.state()
            retry = {**self.launch(3), "tool_input": first["tool_input"]}
            with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
                children.admit_spawn(retry)
            assert self.state() == before
            # Reserve a second unknown launch; the ceiling neither erases nor settles it.
            children.admit_spawn(second)
            before = self.state()
            with pytest.raises(core.WorkspaceError, match="HOST_CHILD_CAPACITY_EXHAUSTED"):
                children.admit_spawn(self.launch(3))
            assert self.state() == before
            with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
                children.admit_spawn(first)
            assert self.state() == before
            assert before["participants"][self.base["coordinator"]]["pending"] == {
                "spawn-2": {"status": "pending"}
            }
            # Delayed native identities still join against the retained authorized cohort.
            children.start(self.child())
            children.start(self.child())
            before = self.state()
            with pytest.raises(core.WorkspaceError, match="HOST_UNSUPPORTED_CHILD_IDENTITY"):
                children.start(self.child())
            assert self.state() == before
        with children.registry(children.parent_request(first)) as (_, _, state):
            assert set(state["launches"]) == {"spawn-1", "spawn-2"}
            assert state["launches"]["spawn-1"]["outcome"] == "unknown"
            assert len(state["children"]) == 2

    def test_settled_source_pre_replay_cannot_execute_without_pending_accounting(self) -> None:
        """Deny re-execution of settled source IDs even after receipt compaction.

        Raises:
            AssertionError: An old pre callback admits new work without an outstanding record.
        """
        # Settle two reads so the first source receipt is compacted out of the registry.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        children.start(peer)
        path = self.root / ".task/TEST-1/roadmap.md"
        event = {
            **peer,
            "tool_name": "Bash",
            "tool_use_id": "first",
            "tool_input": {"command": "cat -- " + shlex.quote(str(path))},
        }
        for call_id in ("first", "second"):
            read = {**event, "tool_use_id": call_id}
            children.child_pre(read)
            children.child_post({**read, "tool_response": path.read_text()})
        before = self.state()
        # Both retained and compacted completed IDs must remain unusable for execution.
        for call_id in ("first", "second"):
            with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
                children.child_pre({**event, "tool_use_id": call_id})
            assert self.state() == before

    def test_stale_child_packet_preserves_exact_diagnostic_recovery(self) -> None:
        """Admit own bounded diagnostics on stale sources without admitting other commands.

        Raises:
            AssertionError: Staleness blocks diagnosis or allows foreign diagnostic authority.
        """
        # Scope a real external source and change its bytes after assignment.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        children.start(peer)
        request = common.request_for(peer, "diagnose", "codex")
        request.pop("participant_id", None)
        key = core.participant_key(request)
        source = self.root / "assigned-source.md"
        source.write_text("original")
        reference = self.state()["participants"][key]["packet"][0]
        self.call(
            "scope",
            expected_revision=self.state()["revision"],
            target_participant=key,
            packet=[{**reference, "locator": str(source), "sha256": core.sha(source.read_bytes())}],
        )
        source.write_text("changed")
        assert core.execute({**request, "operation": "read"})["code"] == "SOURCE_STALE"
        before = self.state()
        # The diagnostic is an exact read-only command under the observed child's identity.
        children.child_pre(self.command(peer, request))
        assert core.execute(request)["ok"]
        assert self.state() == before
        # Foreign identity, foreign issue and arbitrary extra diagnostic fields remain denied.
        for bad in (
            {**request, "session_id": "coordinator"},
            {**request, "issue_id": "TEST-2"},
            {**request, "content": "not diagnostic data"},
            {**request, "operation": "update", "path": "roadmap.md", "content": "forbidden"},
        ):
            with pytest.raises(core.WorkspaceError) as failure:
                children.child_pre(self.command(peer, bad))
            assert failure.value.code == "SOURCE_STALE"
            assert self.state() == before

    def test_spawn_pre_replays_require_both_unsettled_receipt_and_core_pending(self) -> None:
        """Reject settled or manually reconciled launch retries without losing uncertainty.

        Raises:
            AssertionError: A replay can execute after its authoritative pending work ended.
        """
        # An unresolved exact pre replay retains one launch and one pending tool.
        self.create()
        self.ready()
        launch = self.launch(1)
        children.admit_spawn(launch)
        before = self.state()
        children.admit_spawn(launch)
        assert self.state() == before
        # Core completion alone is enough to close execution even before registry completion.
        self.call("tool-complete", tool_id="spawn-1", completed=True)
        before = self.state()
        with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
            children.admit_spawn(launch)
        assert self.state() == before
        with children.registry(children.parent_request(launch)) as (_, _, state):
            assert set(state["launches"]) == {"spawn-1"}
            assert not state["launches"]["spawn-1"].get("completed")

    def test_observed_unprefixed_spawn_dispatch_uses_full_native_launch_gates(self) -> None:
        """Route the current host spelling through its separate no-history UUID contract.

        Raises:
            AssertionError: Native alias is denied, skips launch gates or bypasses settlement.
        """
        # A ready coordinator can launch only the explicit supported no-history route.
        self.create()
        self.ready()
        launch = {
            **self.launch(1),
            "tool_name": "spawn_agent",
            "tool_input": {"message": "bounded reader", "fork_context": False},
        }
        target = str(uuid.uuid4())
        # The actual alias reserves a reader slot and authoritative pending work.
        assert codex.handle(launch) == {}
        before = self.state()
        unsafe = {**launch, "tool_input": {**launch["tool_input"], "fork_turns": "all"}}
        with pytest.raises(core.WorkspaceError, match="HOST_UNSUPPORTED_CHILD_IDENTITY"):
            codex.handle(unsafe)
        assert self.state() == before
        assert self.state()["participants"][self.base["coordinator"]]["pending"] == {
            "spawn-1": {"status": "pending"}
        }
        assert (
            codex.handle(
                {
                    **launch,
                    "hook_event_name": "PostToolUse",
                    "tool_response": {"agent_id": target, "nickname": "Fixture"},
                }
            )
            == {}
        )
        assert self.state()["participants"][self.base["coordinator"]]["pending"] == {}
        with children.registry(children.parent_request(launch)) as (_, _, state):
            assert state["launches"]["spawn-1"]["target"] == target
            assert state["launches"]["spawn-1"]["completed"]
            assert state["launches"]["spawn-1"]["outcome"] == "target_observed"

    def test_observed_wait_alias_settles_only_its_parent_call(self) -> None:
        """Treat the observed synchronous native wait completion as terminal parent work.

        Raises:
            AssertionError: Completed waits remain pending or settle unrelated launch work.
        """
        # Keep a distinct launch pending while admitting the exact observed wait tool.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        native = {
            **self.launch(2),
            "tool_name": "spawn_agent",
            "tool_input": {"message": "reader", "fork_context": False},
        }
        target = str(uuid.uuid4())
        codex.handle(native)
        codex.handle(
            {
                **native,
                "hook_event_name": "PostToolUse",
                "tool_response": {"agent_id": target, "nickname": None},
            }
        )
        wait = {
            "session_id": "coordinator",
            "cwd": str(self.root),
            "hook_event_name": "PreToolUse",
            "tool_name": "multi_agent_v1wait_agent",
            "tool_use_id": "wait-one",
            "tool_input": {"targets": [target], "timeout_ms": 1000},
        }
        assert codex.handle(wait) == {}
        key = self.base["coordinator"]
        assert set(self.state()["participants"][key]["pending"]) == {"spawn-1", "wait-one"}
        # The observed PostToolUse fact closes this synchronous call without reading its payload.
        assert codex.handle({**wait, "hook_event_name": "PostToolUse", "tool_response": "{}"}) == {}
        assert self.state()["participants"][key]["pending"] == {"spawn-1": {"status": "pending"}}

    def test_native_controls_require_recorded_uuid_and_exact_supported_schema(self) -> None:
        """Deny foreign targets and unsupported controls before recording external activity.

        Raises:
            AssertionError: A control bypasses recorded native target ownership or shape checks.
        """
        # Establish one native address without assigning a lifecycle participant or write scope.
        self.create()
        self.ready()
        launch = {
            **self.launch(1),
            "tool_name": "spawn_agent",
            "tool_input": {"message": "reader", "fork_context": False},
        }
        codex.handle(launch)
        target = str(uuid.uuid4())
        codex.handle(
            {
                **launch,
                "hook_event_name": "PostToolUse",
                "tool_response": {"agent_id": target, "nickname": "Fixture"},
            }
        )
        before = self.state()
        # Every denied native control leaves authoritative pending work and ownership unchanged.
        for tool, args, code in (
            (
                "multi_agent_v1send_input",
                {"target": str(uuid.uuid4()), "message": "hello"},
                "HOST_UNSUPPORTED_CHILD_IDENTITY",
            ),
            ("multi_agent_v1send_input", {"target": target, "items": []}, "INVALID_REQUEST"),
            (
                "multi_agent_v1send_input",
                {"target": target, "message": "hello", "interrupt": 1},
                "INVALID_REQUEST",
            ),
            ("multi_agent_v1close_agent", {"target": target, "extra": True}, "INVALID_REQUEST"),
            (
                "multi_agent_v1wait_agent",
                {"targets": [target, str(uuid.uuid4())]},
                "HOST_UNSUPPORTED_CHILD_IDENTITY",
            ),
            ("multi_agent_v1wait_agent", {"targets": [target], "extra": True}, "INVALID_REQUEST"),
        ):
            event = {**launch, "tool_name": tool, "tool_use_id": "control", "tool_input": args}
            with pytest.raises(core.WorkspaceError, match=code):
                codex.handle(event)
            assert self.state() == before
        # Unsupported spellings and resume cannot fall through to generic readiness admission.
        for tool in ("multi_agent_v1resume_agent", "send_input", "close_agent", "resume_agent"):
            denial = codex.handle({**launch, "tool_name": tool, "tool_input": {"id": target}})
            assert denial["hookSpecificOutput"]["permissionDecision"] == "deny"
            assert self.state() == before
        # Exact supported messages and close calls settle only their own parent transport.
        for tool, args in (
            ("multi_agent_v1send_input", {"target": target, "message": "hello", "interrupt": True}),
            ("multi_agent_v1close_agent", {"target": target}),
        ):
            event = {**launch, "tool_name": tool, "tool_use_id": tool, "tool_input": args}
            assert codex.handle(event) == {}
            assert (
                codex.handle({**event, "hook_event_name": "PostToolUse", "tool_response": "{}"})
                == {}
            )
            settled = self.state()
            with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
                codex.handle(event)
            assert self.state() == settled
        assert self.state()["owners"] == before["owners"]
        assert self.state()["participants"] == before["participants"]

    def test_pending_native_control_replays_bind_exact_tool_arguments_and_completion(self) -> None:
        """Reject changed active controls and preserve exact replay after receipt compaction.

        Raises:
            AssertionError: Changed tool/arguments pass admission or settle another operation.
        """
        # Register two owned native addresses so target changes exercise exact call correlation.
        self.create()
        self.ready()
        targets = [str(uuid.uuid4()), str(uuid.uuid4())]
        for number, target in enumerate(targets):
            launch = {
                **self.launch(number),
                "tool_name": "spawn_agent",
                "tool_input": {"message": "reader", "fork_context": False},
            }
            codex.handle(launch)
            codex.handle(
                {
                    **launch,
                    "hook_event_name": "PostToolUse",
                    "tool_response": {"agent_id": target, "nickname": None},
                }
            )
        event = {
            "session_id": "coordinator",
            "cwd": str(self.root),
            "hook_event_name": "PreToolUse",
            "tool_name": "multi_agent_v1send_input",
            "tool_use_id": "input",
            "tool_input": {"target": targets[0], "message": "first"},
        }
        assert codex.handle(event) == {}
        before = self.state()
        # Same-ID retries preserve the original message, target and operation in both hook phases.
        changed = [
            {**event, "tool_input": {"target": targets[0], "message": "changed"}},
            {**event, "tool_input": {"target": targets[1], "message": "first"}},
            {
                **event,
                "tool_name": "multi_agent_v1close_agent",
                "tool_input": {"target": targets[0]},
            },
        ]
        for altered in changed:
            for phase in ("PreToolUse", "PostToolUse"):
                with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
                    codex.handle({**altered, "hook_event_name": phase, "tool_response": "{}"})
                assert self.state() == before
        assert codex.handle(event) == {}
        assert self.state() == before
        # Exact terminal replay uses core idempotency after completed receipt compaction.
        post = {**event, "hook_event_name": "PostToolUse", "tool_response": "{}"}
        assert codex.handle(post) == {}
        settled = self.state()
        assert codex.handle(post) == {}
        assert self.state() == settled
        with children.registry(children.parent_request(event)) as (_, _, state):
            assert state["controls"] == {}
        for altered in changed:
            with pytest.raises(core.WorkspaceError, match="UNKNOWN_OPERATION"):
                codex.handle({**altered, "hook_event_name": "PostToolUse", "tool_response": "{}"})
            assert self.state() == settled
        with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
            codex.handle(event)
        assert self.state() == settled

    def test_join_deadline_exhaustion_preserves_unjoined_identity_for_recovery(self) -> None:
        """Stop on the modeled deadline independently of filesystem speed and retry count.

        Raises:
            AssertionError: An expired join grants membership or a retry consumes extra capacity.
        """
        # Keep actual registry/store operations and advance only the adapter's clock seam.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        execute = core.execute
        clock = {"now": 0.0}
        attempts = []
        before = self.state()

        def expire_after_conflict(request: dict) -> dict:
            """Exhaust the join deadline after one modeled revision conflict.

            Args:
                request: Lifecycle transaction submitted by the real join implementation.

            Returns:
                A conflict for join, otherwise the actual persisted lifecycle response.
            """
            # A failed transaction consumes time without adding a lifecycle participant.
            if request["operation"] == "join":
                attempts.append(request["request_id"])
                clock["now"] = common.CHILD_JOIN_BUDGET_SECONDS + 1.0
                return {"ok": False, "code": "REVISION_CONFLICT"}
            # All registration, source and binding work still uses the real disposable store.
            return execute(request)

        join_clock = SimpleNamespace(monotonic=lambda: clock["now"], sleep=common.time.sleep)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(children, "time", join_clock)
            patch.setattr(common, "time", join_clock)
            patch.setattr(core, "execute", expire_after_conflict)
            with pytest.raises(core.WorkspaceError) as failure:
                children.start(peer)
        assert failure.value.code == "REVISION_CONFLICT"
        assert len(attempts) == 1
        assert len(attempts) < common.CHILD_JOIN_ATTEMPTS
        assert self.state() == before
        key = core.participant_key({"host": "codex", "session_id": peer["session_id"]})
        with children.registry(children.parent_request(peer)) as (_, _, state):
            assert set(state["children"]) == {key}
            assert state["children"][key]["status"] == "joining"
        # A fresh native start retries the retained UUID after the modeled contention clears.
        assert "Reader only" in children.start(peer)
        assert self.state()["participants"][key]["ack"] is None
        assert set(self.state()["owners"].values()) == {self.base["coordinator"]}
        with children.registry(children.parent_request(peer)) as (_, _, state):
            assert set(state["children"]) == {key}
            assert state["children"][key]["status"] == "joined"

    def test_changed_source_completion_cannot_manufacture_delivery(self) -> None:
        """Retain original bytes on replay and require a fresh read for recovery.

        Raises:
            AssertionError: Changed replay grants acknowledgment or mutates settled work.
        """
        # Establish a real reader and complete one admitted read with truncated bytes.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        children.start(peer)
        request = common.request_for(peer, "read", "codex")
        packet = core.execute(request)
        ack = {**request, "operation": "acknowledge", "packet_digest": packet["packet_digest"]}
        path = self.root / ".task/TEST-1/roadmap.md"
        pre = {
            **peer,
            "tool_name": "Bash",
            "tool_use_id": "source-original",
            "tool_input": {"command": "cat -- " + shlex.quote(str(path))},
        }
        children.child_pre(pre)
        post = {**pre, "tool_response": "truncated"}
        children.child_post(post)
        before = self.state()
        # Exact completion replay is idempotent; changed bytes cannot replace its evidence.
        children.child_post(post)
        assert self.state() == before
        with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
            children.child_post({**post, "tool_response": path.read_text()})
        assert self.state() == before
        key = core.participant_key(request)
        with children.registry(children.parent_request(peer)) as (_, _, state):
            receipt = state["children"][key]["source_reads"]["source-original"]
            assert receipt["content_sha256"] == core.sha(b"truncated")
        with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
            children.child_pre(self.command(peer, ack))
        assert self.state()["participants"][key]["ack"] is None
        # A separately admitted read with actual bytes can recover the same packet.
        fresh = {**pre, "tool_use_id": "source-fresh"}
        children.child_pre(fresh)
        children.child_post({**fresh, "tool_response": path.read_text()})
        children.child_pre(self.command(peer, ack))
        assert core.execute(ack)["ok"]
        assert not self.state()["participants"][key]["pending"]

    def test_changed_spawn_completion_cannot_settle_or_authorize_target(self) -> None:
        """Bind native and legacy completions to their originally admitted arguments.

        Raises:
            AssertionError: Changed arguments settle work or create a control address.
        """
        # Admit both protocols in one real store without creating any lifecycle children.
        self.create()
        self.ready()
        for native in (False, True):
            launch = self.launch(int(native))
            target = str(uuid.uuid4()) if native else "/root/worker-0"
            response = {"agent_id" if native else "task_name": target}
            if native:
                launch.update(
                    tool_name="spawn_agent", tool_input={"message": "reader", "fork_context": False}
                )
            children.admit_spawn(launch)
            before = self.state()
            changed = {**launch["tool_input"], "message": "changed"}
            if native:
                changed["fork_context"] = True
            # Reject before either authoritative pending work or target receipts change.
            with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
                children.parent_post({**launch, "tool_input": changed, "tool_response": response})
            assert self.state() == before
            assert (
                launch["tool_use_id"] in before["participants"][self.base["coordinator"]]["pending"]
            )
            with children.registry(children.parent_request(launch)) as (_, _, state):
                receipt = state["launches"][launch["tool_use_id"]]
                assert "target" not in receipt
                assert "completed" not in receipt
            # The exact admitted completion still succeeds and then replays idempotently.
            post = {**launch, "tool_response": response}
            children.parent_post(post)
            settled = self.state()
            assert (
                launch["tool_use_id"]
                not in settled["participants"][self.base["coordinator"]]["pending"]
            )
            children.parent_post(post)
            assert self.state() == settled
            with children.registry(children.parent_request(launch)) as (_, _, state):
                assert state["launches"][launch["tool_use_id"]]["target"] == target

    def test_source_completion_survives_registry_write_failure_without_changing_bytes(self) -> None:
        """Bind cached core settlement to original bytes across a failed receipt write.

        Raises:
            AssertionError: A partially persisted completion accepts replacement output.
        """
        # Admit one source read in a real store, retaining its exact original packet.
        self.create()
        self.ready()
        children.admit_spawn(self.launch(1))
        peer = self.child()
        children.start(peer)
        request = common.request_for(peer, "read", "codex")
        packet = core.execute(request)
        ack = {**request, "operation": "acknowledge", "packet_digest": packet["packet_digest"]}
        path = self.root / ".task/TEST-1/roadmap.md"
        pre = {
            **peer,
            "tool_name": "Bash",
            "tool_use_id": "source-write-failure",
            "tool_input": {"command": "cat -- " + shlex.quote(str(path))},
        }
        children.child_pre(pre)
        put = core.Directory.put

        def fail_registry_write(directory, name, value):
            """Fail only adapter persistence after the authoritative core has settled.

            Args:
                directory: Actual safe directory receiving the write.
                name: Filename selected by the real adapter or core.
                value: JSON value intended for persistence.

            Raises:
                OSError: The adapter attempts its final cohort receipt write.
            """
            # Preserve every core transaction; interrupt only the final adapter receipt.
            if ".codex-children-" in name:
                raise OSError("injected registry write failure")
            put(directory, name, value)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(core.Directory, "put", fail_registry_write)
            with pytest.raises(OSError, match="^injected registry write failure$"):
                children.child_post({**pre, "tool_response": "truncated"})
        key = core.participant_key(request)
        settled = self.state()
        assert not settled["participants"][key]["pending"]
        # Changed bytes cannot reuse cached settlement while the receipt is incomplete.
        with pytest.raises(core.WorkspaceError, match="UNKNOWN_OPERATION"):
            children.child_post({**pre, "tool_response": path.read_text()})
        assert self.state() == settled
        with children.registry(children.parent_request(peer)) as (_, _, state):
            receipt = state["children"][key]["source_reads"][pre["tool_use_id"]]
            assert "completed" not in receipt
            assert "content_sha256" not in receipt
        with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
            children.child_pre(self.command(peer, ack))
        # Exact retry restores the original receipt without another core transition.
        children.child_post({**pre, "tool_response": "truncated"})
        assert self.state() == settled
        with pytest.raises(core.WorkspaceError, match="SOURCE_DELIVERY_REQUIRED"):
            children.child_pre(self.command(peer, ack))
        # Recovery still requires a distinct admitted read containing the actual source.
        fresh = {**pre, "tool_use_id": "source-after-write-failure"}
        children.child_pre(fresh)
        children.child_post({**fresh, "tool_response": path.read_text()})
        children.child_pre(self.command(peer, ack))
        assert core.execute(ack)["ok"]

    def test_terminal_spawn_target_presence_and_value_are_immutable(self) -> None:
        """Keep failed launches uncontrollable and successful addresses stable on replay.

        Raises:
            AssertionError: A replay changes authority or the original completion receipt.
        """
        # Exercise both host schemas and both possible first terminal target outcomes.
        self.create()
        self.ready()
        for number, (native, observed) in enumerate(
            ((False, False), (True, False), (False, True), (True, True))
        ):
            with self.subTest(native=native, observed=observed):
                launch = self.launch(number)
                target = str(uuid.uuid4()) if native else f"/root/worker-{number}"
                replacement = str(uuid.uuid4()) if native else f"/other/worker-{number}"
                field = "agent_id" if native else "task_name"
                if native:
                    launch.update(
                        tool_name="spawn_agent",
                        tool_input={"message": "reader", "fork_context": False},
                    )
                children.admit_spawn(launch)
                response = {field: target} if observed else {"error": "failed"}
                post = {**launch, "tool_response": response}
                children.parent_post(post)
                settled = self.state()
                with children.registry(children.parent_request(launch)) as (_, _, state):
                    original = dict(state["launches"][launch["tool_use_id"]])
                # Identical terminal callbacks preserve both core state and persisted authority.
                children.parent_post(post)
                assert self.state() == settled
                for changed in (
                    ({field: replacement}, {"error": "failed"}) if observed else ({field: target},)
                ):
                    with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
                        children.parent_post({**launch, "tool_response": changed})
                    assert self.state() == settled
                    with children.registry(children.parent_request(launch)) as (_, _, state):
                        assert state["launches"][launch["tool_use_id"]] == original
                # A failed launch still cannot authorize a control call after the rejected replay.
                control = {
                    **launch,
                    "tool_name": "multi_agent_v1send_input"
                    if native
                    else "collaborationsend_message",
                    "tool_use_id": f"control-{number}",
                    "tool_input": {"target": target, "message": "continue"},
                }
                if not observed:
                    with pytest.raises(
                        core.WorkspaceError, match="HOST_UNSUPPORTED_CHILD_IDENTITY"
                    ):
                        children.admit_control(control)
                    assert self.state() == settled
                else:
                    children.admit_control(control)
                    children.parent_post({**control, "tool_response": {}})

    def test_spawn_receipt_survives_failed_core_settlement(self) -> None:
        """Recover pending settlement without changing a persisted terminal observation.

        Raises:
            AssertionError: Retry loses pending work or replaces the original target outcome.
        """
        # Fail only the core completion after the adapter has persisted its first receipt.
        self.create()
        self.ready()
        for number, (native, observed) in enumerate(
            ((False, False), (True, False), (False, True), (True, True))
        ):
            launch = self.launch(number)
            if native:
                launch.update(
                    tool_name="spawn_agent", tool_input={"message": "reader", "fork_context": False}
                )
            children.admit_spawn(launch)
            target = str(uuid.uuid4()) if native else f"/root/worker-{number}"
            field = "agent_id" if native else "task_name"
            response = {field: target} if observed else {"error": "failed"}
            post = {**launch, "tool_response": response}
            execute = core.execute

            def fail_completion(request):
                """Interrupt only core settlement, preserving all other real operations.

                Args:
                    request: Lifecycle request selected by the adapter.

                Returns:
                    The real lifecycle response for non-completion operations.

                Raises:
                    OSError: The adapter attempts the core completion transition.
                """
                # Leave the registry committed while the authoritative pending call remains.
                if request["operation"] == "tool-complete":
                    raise OSError("injected core completion failure")
                return execute(request)

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(core, "execute", fail_completion)
                with pytest.raises(OSError, match="^injected core completion failure$"):
                    children.parent_post(post)
            pending = self.state()
            assert (
                launch["tool_use_id"]
                in pending["participants"][self.base["coordinator"]]["pending"]
            )
            with children.registry(children.parent_request(launch)) as (_, _, state):
                original = dict(state["launches"][launch["tool_use_id"]])
                assert original["completed"] is True
                assert original["outcome"] == ("target_observed" if observed else "unknown")
            # A changed replay cannot use the unsettled core call to replace the receipt.
            response = {"error": "failed"} if observed else {field: target}
            with pytest.raises(core.WorkspaceError, match="REQUEST_CONFLICT"):
                children.parent_post({**launch, "tool_response": response})
            assert self.state() == pending
            # The original callback can finish settlement and replay idempotently thereafter.
            children.parent_post(post)
            settled = self.state()
            assert not settled["participants"][self.base["coordinator"]]["pending"]
            children.parent_post(post)
            assert self.state() == settled
            with children.registry(children.parent_request(launch)) as (_, _, state):
                assert state["launches"][launch["tool_use_id"]] == original
