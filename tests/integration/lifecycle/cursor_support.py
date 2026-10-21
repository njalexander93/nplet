"""Shared disposable Cursor invocation fixture without collected test methods."""

from __future__ import annotations

from pathlib import Path

from nplet.lifecycle import task_workspace as core
from tests.support import Fixture


def invocation_execute(request: dict) -> dict:
    """Select the explicitly internal adapter entry for synthetic native transitions.

    Args:
        request: Complete disposable lifecycle request.

    Returns:
        The real lifecycle result. Public-denial tests call core.execute directly.
    """
    # These are adapter-generated observations, not public CLI requests.
    if request["operation"] in {
        "cursor-admit",
        "cursor-bind-child",
        "cursor-retire",
        "cursor-source-callback",
    }:
        return core.execute_internal(request)
    return core.execute(request)


class InvocationFixture(Fixture):
    """Prepare disposable invocations and source observations for focused lifecycle tests."""

    def call(self, operation: str, **fields: object) -> dict:
        """Dispatch fixture observations through their explicit public/internal entry.

        Args:
            operation: Lifecycle operation selected by the fixture.
            **fields: Complete per-call overrides.

        Returns:
            The successful lifecycle response.

        Raises:
            AssertionError: The actual core result reports failure.
        """
        return self.require_ok(invocation_execute(self.req(operation, **fields)))

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
            invocation_execute(
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
            invocation_execute(self.child_request("read", child, binding_generation=generation))
        )
        # Explicit synthetic native evidence for this core fixture, never a native proof claim.
        for index, ref in enumerate(read["references"]):
            if not ref["available"]:
                continue
            path = Path(ref["locator"])
            if not path.is_absolute():
                path = self.root / ".task" / self.base["issue_id"] / path
            data = path.read_bytes()
            length = len(data.decode("utf-8").encode("utf-16-le")) // 2
            tool = "fixture-source-" + str(index)
            self.require_ok(
                invocation_execute(
                    self.child_request(
                        "source-read-start",
                        child,
                        binding_generation=generation,
                        tool_id=tool,
                        source_locator=ref["locator"],
                        packet_digest=read["packet_digest"],
                    )
                )
            )
            self.require_ok(
                invocation_execute(
                    self.child_request(
                        "cursor-source-callback",
                        child,
                        binding_generation=generation,
                        tool_id=tool,
                        source_path=str(path),
                        source_event="content",
                        observed_sha256=core.sha(data),
                        content_length=length,
                    )
                )
            )
            self.require_ok(
                invocation_execute(
                    self.child_request(
                        "cursor-source-callback",
                        child,
                        binding_generation=generation,
                        tool_id=tool,
                        source_path=str(path),
                        source_event="success",
                        content_length=length,
                    )
                )
            )
        self.require_ok(
            invocation_execute(
                self.child_request(
                    "acknowledge",
                    child,
                    binding_generation=generation,
                    packet_digest=read["packet_digest"],
                )
            )
        )
        self.require_ok(
            invocation_execute(self.child_request("ready", child, binding_generation=generation))
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

    def source_child(self, text: str | None = None) -> tuple[dict, bytes, str]:
        """Bind one unacknowledged child assigned real disposable UTF8source bytes.

        Args:
            text: Optional external source text; None uses the managed roadmap.

        Returns:
            Child read response, exact source bytes and native file path.
        """
        self.create()
        self.ready()
        locator = "roadmap.md"
        path = self.root / ".task" / self.base["issue_id"] / locator
        if text is not None:
            path = self.root / "unicode-source.md"
            # Use the owned source permissions required on POSIX and Windows.
            with core.Directory.absolute(path.parent) as parent:
                parent.write(path.name, text.encode("utf-8"))
            locator = str(path)
        data = path.read_bytes()
        packet = [
            {
                "id": "source",
                "locator": locator,
                "sha256": core.sha(data),
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
        read = self.require_ok(invocation_execute(self.child_request("read", binding_generation=1)))
        self.require_ok(
            invocation_execute(
                self.child_request(
                    "source-read-start",
                    binding_generation=1,
                    tool_id="source",
                    source_locator=locator,
                    packet_digest=read["packet_digest"],
                )
            )
        )
        return read, data, str(path)

    def source_event(self, phase: str, path: str, **fields: object) -> dict:
        """Build one fresh synthetic closed callback under the independent child identity.

        Args:
            phase: content, success or failure.
            path: Exact native source path.
            **fields: Callback metadata or deliberate tool identity override.

        Returns:
            Unexecuted callback for success, failure and replay assertions.
        """
        return self.child_request(
            "cursor-source-callback",
            binding_generation=1,
            tool_id=fields.pop("tool_id", "source"),
            source_path=path,
            source_event=phase,
            **fields,
        )

    def fill_settlement_boundary(self) -> int:
        """Fill only the disposable fixture's retained request metadata to its reserve boundary.

        Returns:
            Initial serialized state plus its remaining settlement allowance.
        """
        # Simulate accumulated immutable request history without modifying any live store.
        state = self.state()
        state["requests"]["e" * 64] = {"digest": "e" * 64, "result": {"history": ""}}
        allowance, _ = core.settlement_reserve(state)
        remaining = 8 * 1024 * 1024 - len(core.canonical(state)) - allowance - 32
        assert remaining > 0
        state["requests"]["e" * 64]["result"]["history"] = "x" * remaining
        path = self.root / ".task/.control/issues/TEST-1/state.json"
        path.write_bytes(core.canonical(state))
        return len(core.canonical(state)) + allowance

    def assert_settlement_shrinks(self, before: int) -> int:
        """Check real committed transition growth against its released allowance.

        Args:
            before: Prior state bytes plus reserve.

        Returns:
            Current potential, bounded by the previous transition.
        """
        state = self.state()
        allowance, _ = core.settlement_reserve(state)
        after = len(core.canonical(state)) + allowance
        assert after <= before
        return after
