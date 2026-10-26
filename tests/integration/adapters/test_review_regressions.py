"""Author regression tests for independently reported findings; not independent review."""

import os
from unittest import mock

import pytest

from nplet.adapters import codex as hook
from nplet.adapters import common
from nplet.adapters import session_repository as carrier
from nplet.lifecycle import task_workspace as w
from tests.support import Fixture
from tests.types import JsonObject

pytestmark = pytest.mark.integration


class ReviewRegressions(Fixture):
    """Retain author regressions for reported findings without claiming independent review."""

    def test_explicit_preassigned_recovery_after_ticket_first_prompt(self) -> None:
        """Keep preassigned recovery callable while Task prompt defers creation.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Register the coordinator and its startup packet before the prompt.
        self.call(
            "register",
            repo_id=None,
            main_worktree=str(self.root),
            startup={
                "issue_id": "TEST-1",
                "issue_uuid": self.base["issue_uuid"],
                "coordinator": self.base["coordinator"],
                "packet": [],
            },
        )
        # Task alone cannot create an issue before the foreground ticket read.
        event = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(self.root),
            "session_id": "coordinator",
            "prompt": "Task: TEST-1",
        }
        hook.handle(event)
        self.assertFalse((self.root / ".task/TEST-1/roadmap.md").exists())
        hook.automatic_attach(event, "TEST-1")  # Explicit legacy recovery route.
        self.assertTrue((self.root / ".task/TEST-1/roadmap.md").is_file())
        self.assertEqual(self.state()["participants"][self.base["coordinator"]]["packet"], [])
        # Repeat explicit recovery and verify no duplicate lifecycle event is written.
        before = (self.root / ".task/TEST-1/events.jsonl").read_bytes()
        hook.handle(event)
        hook.automatic_attach(event, "TEST-1")
        self.assertEqual(before, (self.root / ".task/TEST-1/events.jsonl").read_bytes())

    def test_bootstrap_rejects_missing_login_shell_override_and_unknown_fields(self) -> None:
        """Admit only the strict bootstrap shell options and request fields.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Bind the session and build a valid explicit-shell diagnostic request.
        event = carrier.carry_repository(
            {
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(self.root),
                "session_id": "coordinator",
                "prompt": "Task: TEST-1",
            }
        )
        hook.handle(event)
        request = self.req(
            "diagnose", repo_id=None, issue_id=None, issue_uuid=None, coordinator=None
        )
        command = common.bootstrap_command(request, "codex")
        event.update(
            hook_event_name="PreToolUse",
            tool_name="Bash",
            tool_input={
                "command": command,
                "login": False,
                "shell": "powershell.exe" if os.name == "nt" else "/bin/sh",
            },
        )
        # Confirm the canonical bootstrap route is admitted.
        self.assertTrue(hook.bootstrap(event))
        # Each tool owns one command field; ambiguity and the other tool's field must deny.
        for tool, field, foreign_field in (
            ("Bash", "command", "cmd"),
            ("exec_command", "cmd", "command"),
        ):
            event["tool_name"] = tool
            controls = {"login": False, "shell": "powershell.exe" if os.name == "nt" else "/bin/sh"}
            event["tool_input"] = {**controls, field: command}
            self.assertTrue(hook.bootstrap(event))
            # Mixed or foreign command fields cannot pass the selected host contract.
            for bad in ({field: command, foreign_field: command}, {foreign_field: command}):
                event["tool_input"] = {**controls, **bad}
                self.assertFalse(hook.bootstrap(event))
        event["tool_name"] = "Bash"
        # Reject missing login control, a different shell and an extra tool field.
        for change in [
            {"login": None},
            {"shell": "/tmp/unreviewed-shell"},
            {"environment": {"X": "value"}},
        ]:
            event["tool_input"] = {
                "command": command,
                "login": False,
                "shell": "powershell.exe" if os.name == "nt" else "/bin/sh",
                **change,
            }
            self.assertFalse(hook.bootstrap(event))
        # Reject an unknown lifecycle request field even with valid shell options.
        request["arbitrary"] = "field"
        event["tool_input"] = {
            "command": common.bootstrap_command(request, "codex"),
            "login": False,
            "shell": "powershell.exe" if os.name == "nt" else "/bin/sh",
        }
        self.assertFalse(hook.bootstrap(event))

    def test_cleaned_archive_reads_are_tombstone_bound(self) -> None:
        """Limit post-cleanup provider reads to documents retained by the tombstone.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Archive and clean a disposable issue with a known index document.
        self.create()
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        # Check the permitted read against the retained archive identity.
        event = {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.root),
            "session_id": "coordinator",
            "tool_name": "mcp__codex_apps__linear_get_document",
            "tool_input": {"id": "index"},
        }
        self.assertTrue(hook.provider_gate(event))
        # Substitute an unrelated document and require denial.
        event["tool_input"]["id"] = "unrelated"
        self.assertFalse(hook.provider_gate(event))

    def test_lifecycle_observation_preserves_pending_operations(self) -> None:
        """Append an interruption observation without settling a pending tool.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Establish readiness and admit a tool that remains pending.
        self.create()
        self.ready()
        self.call("tool-start", tool_id="pending")
        # Record an interruption and compare its event and pending-operation effects.
        seq = self.state()["seq"]
        hook.handle(
            {"hook_event_name": "Interrupt", "cwd": str(self.root), "session_id": "coordinator"}
        )
        self.assertEqual(self.state()["seq"], seq + 1)
        self.assertIn("pending", self.state()["participants"][self.base["coordinator"]]["pending"])

    def test_interrupted_automatic_packet_setup_retries_without_overwrite(self) -> None:
        """Retry interrupted packet assignment without replacing later committed state.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Register the explicit startup assignment and prompt identity.
        self.call(
            "register",
            repo_id=None,
            main_worktree=str(self.root),
            startup={
                "issue_id": "TEST-1",
                "issue_uuid": self.base["issue_uuid"],
                "coordinator": self.base["coordinator"],
                "packet": [],
            },
        )
        event = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(self.root),
            "session_id": "coordinator",
            "prompt": "Task: TEST-1",
        }
        # Retain the real dispatcher for operations outside the injected scope failure.
        execute = w.execute

        def fail_scope(request: JsonObject) -> JsonObject:
            """Inject a BUSY response for scope assignment and delegate other operations.

            Args:
                request: Lifecycle request intercepted during automatic startup.

            Returns:
                A synthetic unsuccessful scope result, or the real dispatcher response.
            """
            # Fail only packet assignment; let other startup operations use the real core.
            return (
                {"ok": False, "code": "BUSY"}
                if request["operation"] == "scope"
                else execute(request)
            )

        hook.handle(event)
        # Inject BUSY for explicit recovery scope installation.
        with mock.patch.object(w, "execute", fail_scope):
            # Verify the hook exposes the assignment failure as WorkspaceError.
            with self.assertRaises(w.WorkspaceError):
                hook.automatic_attach(event, "TEST-1")
        # Confirm the packet is still missing, then retry without the failure seam.
        self.assertIsNone(self.state()["participants"][self.base["coordinator"]]["packet"])
        hook.automatic_attach(event, "TEST-1")
        self.assertEqual(self.state()["participants"][self.base["coordinator"]]["packet"], [])
        # Repeat the successful prompt and require no further state revision.
        before = self.state()["revision"]
        hook.automatic_attach(event, "TEST-1")
        self.assertEqual(self.state()["revision"], before)

    def test_narrow_recovery_commands_wait_for_first_ticket_read(self) -> None:
        """Deny even bounded recovery commands while the first ticket read is pending.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Create and explicitly bind the coordinator before recovery admission.
        self.create()
        common.prompt(
            carrier.carry_repository(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(self.root),
                    "session_id": "coordinator",
                    "prompt": "Task: TEST-1",
                }
            ),
            "codex",
        )
        # Exercise each supported recovery operation with its exact allowed fields.
        for operation, fields in [
            ("archive-prepare", {"seal": True}),
            ("reconcile-files", {"inventory": {}, "evidence": self.evidence()}),
            ("rebind", {"new_issue_id": "TEST-2", "evidence": self.evidence()}),
        ]:
            # Construct the canonical shell invocation and check direct hook admission.
            request = self.req(
                operation, coordinator=None, expected_revision=self.state()["revision"], **fields
            )
            command = common.bootstrap_command(request, "codex")
            event = {
                "hook_event_name": "PreToolUse",
                "cwd": str(self.root),
                "session_id": "coordinator",
                "tool_name": "Bash",
                "tool_use_id": operation,
                "tool_input": {
                    "command": command,
                    "login": False,
                    "shell": "powershell.exe" if os.name == "nt" else "/bin/sh",
                },
            }
            self.assertIn("TICKET_READ_REQUIRED", str(hook.handle(event)))
            # Add an unsupported field and require the bootstrap exception to close.
            request["unknown_field"] = "no"
            event["tool_input"]["command"] = common.bootstrap_command(request, "codex")
            self.assertFalse(hook.bootstrap(event))
