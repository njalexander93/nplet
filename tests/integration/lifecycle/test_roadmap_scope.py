"""Check roadmap scope validation on update and approval reporting on ready.

Roadmap approval text is synthetic; these tests do not prove human approval.
"""

from __future__ import annotations

import pytest

from nplet.lifecycle import task_workspace as w
from tests.support import Fixture
from tests.types import JsonObject

pytestmark = pytest.mark.integration

GRANT = "**Repository scope:**\n- `/abs/root`: `src/`\n"
HEADER = (
    "**Proposal status:** {status}\n"
    "**Proposal revision:** {revision}\n"
    "**Approval record:** {record}\n"
)
APPROVED = HEADER.format(
    status="Approved", revision="r1 (2026-10-30)", record="Human approved revision r1."
)


class RoadmapScopeTests(Fixture):
    """Exercise roadmap scope changes through the real local core."""

    def update(self, content: str) -> JsonObject:
        """Replace the roadmap from the ready coordinator.

        Args:
            content: Replacement roadmap markdown.

        Returns:
            The core response, successful or not.
        """
        # Send the replacement against the current committed revision and digest.
        state = self.state()
        return w.execute(
            self.req(
                "update",
                path="roadmap.md",
                content=content,
                expected_revision=state["revision"],
                old_digest=state["files"]["roadmap.md"],
                provenance={"sources": self.evidence(), "status": "draft", "applicability": "test"},
            )
        )

    def setUp(self) -> None:
        """Create a ready issue whose roadmap is approved and has no scope field.

        Raises:
            AssertionError: Creation, readiness or the initial update fails.
        """
        # Start from a created, ready workspace and an approved legacy roadmap.
        super().setUp()
        self.create()
        self.ready()
        self.require_ok(self.update(APPROVED))

    def test_unrelated_edit_and_ready_without_scope_field(self) -> None:
        """Keep legacy roadmaps working and report approval with an empty scope.

        Raises:
            AssertionError: An unrelated edit fails or ready misreports approval.
        """
        # Edit the body only and observe recorded approval without a grant.
        self.require_ok(self.update(APPROVED + "\nMore notes\n"))
        self.assertEqual(self.call("ready")["approval"], {"approved": True, "scope": []})

    def test_scope_change_requires_new_pending_revision(self) -> None:
        """Reject malformed or unre-proposed grants, then accept a pending revision.

        Raises:
            AssertionError: A scope change is accepted or rejected incorrectly.
        """
        # Reject malformed syntax, an unchanged revision, and a non-pending record.
        bumped = HEADER.format(
            status="Approved", revision="r2", record="Human approved revision r2."
        )
        pending = HEADER.format(status="Proposed", revision="r2", record="Pending")
        for content in (
            APPROVED + "**Repository scope:** everything\n",
            APPROVED + GRANT,
            bumped + GRANT,
        ):
            self.assertEqual(self.update(content)["code"], "INVALID_REQUEST")
        # Accept the grant under a new revision awaiting approval; ready reports it.
        self.require_ok(self.update(pending + GRANT))
        self.assertEqual(
            self.call("ready")["approval"],
            {"approved": False, "scope": [{"root": "/abs/root", "paths": ["src/"]}]},
        )
        # Recording approval without changing the grant needs no further revision.
        self.require_ok(self.update(bumped + GRANT))
        self.assertTrue(self.call("ready")["approval"]["approved"])

    def test_ready_tolerates_malformed_legacy_scope(self) -> None:
        """Report no grant for a committed field written before validation existed.

        Raises:
            AssertionError: Ready fails or reports a grant.
        """
        # Write malformed bytes directly, as a pre-validator roadmap would hold them.
        legacy = (APPROVED + "**Repository scope:** everything\n").encode()
        (self.root / ".task/TEST-1/roadmap.md").write_bytes(legacy)
        state_path = self.root / ".task/.control/issues/TEST-1/state.json"
        state = self.state()
        state["files"]["roadmap.md"] = w.sha(legacy)
        state_path.write_text(w.canonical(state).decode())
        # Ready still succeeds with recorded approval and an empty scope.
        self.assertEqual(self.call("ready")["approval"], {"approved": True, "scope": []})
