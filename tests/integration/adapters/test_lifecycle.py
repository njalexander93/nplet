"""Test direct adapter bootstrap behavior in disposable Git stores.

Provider evidence is synthetic. These tests do not prove live desktop coverage,
provider acceptance, human approval or adversarial runtime isolation.
"""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from nplet.adapters import codex as hook
from nplet.adapters import common
from tests.support import Fixture

pytestmark = pytest.mark.integration


class LifecycleTests(Fixture):
    """Exercise local lifecycle, containment and adapter contracts in disposable stores."""

    def test_legacy_bash_async_completion_retains_work_until_correlated_poll(self) -> None:
        """Retain a legacy wrapper's live handle until its matching terminal poll.

        Raises:
            AssertionError: A wrapper return prematurely settles pending process work.
        """
        # Admit the legacy shell wrapper in a real ready store.
        self.create()
        self.ready()
        event = {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.root),
            "session_id": "coordinator",
            "tool_name": "Bash",
            "tool_input": {"command": "sleep 100 &", "login": False, "shell": "/bin/sh"},
            "tool_use_id": "legacy-shell",
        }
        self.assertEqual(hook.handle(event), {})
        # A wrapper return containing a live handle is not process completion.
        callback = {
            **event,
            "hook_event_name": "PostToolUse",
            "tool_response": '{"session_id":"shell-7","exit_code":null}',
        }
        self.assertEqual(hook.handle(callback), {})
        self.assertEqual(hook.handle(callback), {})
        pending = self.state()["participants"][self.base["coordinator"]]["pending"]
        self.assertIn("legacy-shell", pending)
        self.assertEqual(pending["legacy-shell"]["handle"], "shell-7")
        # A terminal poll may settle only the original operation with its known handle.
        poll = {
            **event,
            "tool_name": "write_stdin",
            "tool_use_id": "legacy-poll",
            "tool_input": {"session_id": "shell-7"},
        }
        self.assertEqual(hook.handle(poll), {})
        self.assertEqual(
            hook.handle(
                {
                    **poll,
                    "hook_event_name": "PostToolUse",
                    "tool_response": '{"exit_code":0}',
                }
            ),
            {},
        )
        pending = self.state()["participants"][self.base["coordinator"]]["pending"]
        self.assertNotIn("legacy-shell", pending)

    def test_native_bash_completion_preserves_other_pending_calls(self) -> None:
        """Settle only correlated final callbacks, regardless of stdout contents.

        Raises:
            AssertionError: Completion clears unrelated work or treats stdout as a handle.
        """
        # Admit two real operations in the disposable coordinator's ready scope.
        self.create()
        self.ready()
        event = {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.root),
            "session_id": "coordinator",
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
        }
        for tool_id in ("finished", "running"):
            self.assertEqual(hook.handle({**event, "tool_use_id": tool_id}), {})
        # A final callback contains arbitrary stdout, never authoritative transport fields.
        completed = {
            **event,
            "hook_event_name": "PostToolUse",
            "tool_use_id": "finished",
            "tool_response": '{"session_id":77}',
        }
        self.assertEqual(hook.handle(completed), {})
        self.assertEqual(hook.handle(completed), {})
        pending = self.state()["participants"][self.base["coordinator"]]["pending"]
        self.assertNotIn("finished", pending)
        self.assertIn("running", pending)
        # A callback for an unadmitted call cannot settle the remaining operation.
        hook.handle({**completed, "tool_use_id": "unrelated"})
        pending = self.state()["participants"][self.base["coordinator"]]["pending"]
        self.assertIn("running", pending)

    def test_T14_real_adapter_bootstrap_argument_gate(self) -> None:
        """Check exact bootstrap admission and subprocess denial of shell variants.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Bind the hook session and construct its canonical diagnostic command.
        event = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(self.root),
            "session_id": "coordinator",
            "prompt": "Task: TEST-1",
        }
        common.prompt(event, "codex")
        # Stand in for the completed ticket read so the bootstrap route is reachable.
        key = common.core.participant_key({"host": "codex", "session_id": "coordinator"})
        with (
            common.core.Directory.absolute(self.root) as worktree,
            worktree.child(".task") as local,
            local.child(".bindings") as bindings,
        ):
            bindings.unlink(key + ".lookup-required.json")
        request = self.req(
            "diagnose", repo_id=None, issue_id=None, issue_uuid=None, coordinator=None
        )
        command = common.bootstrap_command(request, "codex")
        # Admit and execute the supported bootstrap command.
        event.update(
            hook_event_name="PreToolUse",
            tool_name="Bash",
            tool_use_id="test",
            tool_input={
                "command": command,
                "login": False,
                "shell": "powershell.exe" if os.name == "nt" else "/bin/sh",
            },
        )
        self.assertEqual(hook.handle(event), {})
        # Native Codex strips transport options; exact command verification still applies.
        native = {**event, "tool_input": {"command": command}}
        self.assertEqual(hook.handle(native), {})
        self.assertFalse(hook.bootstrap({**native, "tool_input": {"command": command + "; true"}}))
        shell = (
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
            if os.name == "nt"
            else ["/bin/sh", "-c", command]
        )
        result = subprocess.run(shell, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        # Reject command chaining, wrappers, redirection and alternate interpreters.
        for bad in [
            command + "; touch sentinel",
            "env " + command,
            command + " > output",
            command + " && true",
            command.replace(hook.PYTHON, "python3", 1),
        ]:
            event["tool_input"]["command"] = bad
            self.assertFalse(hook.bootstrap(event))
        # Feed the rejected command through the actual JSON adapter entry point.
        denied = subprocess.run(
            [hook.PYTHON, str(Path(hook.__file__))],
            cwd=self.root,
            input=json.dumps(event),
            capture_output=True,
            text=True,
        )
        # Verify a successful hook process still emits an explicit tool denial.
        self.assertEqual(denied.returncode, 0)
        self.assertEqual(
            json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_native_preparation_preserves_ticket_gate_and_denies_shell_variants(self) -> None:
        """Allow inspection while keeping protected work behind the ticket gate.

        Raises:
            AssertionError: Preparation changes readiness or a broader shell command passes.
        """
        # Start a fresh ticket lookup with no ready participant.
        event = {"cwd": str(self.root), "session_id": "coordinator", "prompt": "Task: TEST-1"}
        common.prompt(event, "codex")
        event.update(hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="prepare")
        # Both governing paths remain read-only preparation across pre/post callbacks.
        for relative in ("AGENTS.md", "docs/runtime/contributor-workflow.md"):
            command = "cat -- " + shlex.quote(str(self.root / relative))
            event["tool_input"] = {"command": command}
            assert hook.handle(event) == {}
            assert (
                hook.handle({**event, "hook_event_name": "PostToolUse", "tool_response": "text"})
                == {}
            )
            assert hook.startup_lookup_state(event, ".lookup-required.json")
        # Additional supported inspection is task-free without consuming ticket state.
        for command in ("cat " + shlex.quote((self.root / "pyproject.toml").as_posix()), "pwd"):
            event["tool_input"] = {"command": command}
            assert hook.handle(event) == {}
        # Opaque composition and writes still require the selected ticket.
        for command in (
            "cat -- " + str(self.root / "AGENTS.md") + "; true",
            "cat -- " + str(self.root / "AGENTS.md") + " > copied",
        ):
            event["tool_input"] = {"command": command}
            assert hook.handle(event) == hook.denial("TICKET_READ_REQUIRED")
        assert hook.startup_lookup_state(event, ".lookup-required.json")

    def test_native_mcp_content_completion_does_not_require_optional_error_flag(self) -> None:
        """Settle an observed MCP content result without treating its text as transport state.

        Raises:
            AssertionError: Valid native completion remains pending or settles another call.
        """
        # Admit the same native documentation-tool shape seen in installed callbacks.
        self.create()
        self.ready()
        event = {
            "cwd": str(self.root),
            "session_id": "coordinator",
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__openaiDeveloperDocs__fetch_openai_doc",
            "tool_input": {"url": "https://developers.openai.com/codex/"},
            "tool_use_id": "docs-result",
        }
        assert hook.handle(event) == {}
        self.call("tool-start", tool_id="unrelated")
        # MCP success may omit isError; its content is output, not an asynchronous handle.
        assert (
            hook.handle(
                {
                    **event,
                    "hook_event_name": "PostToolUse",
                    "tool_response": {"content": [{"type": "text", "text": '{"session_id":123}'}]},
                }
            )
            == {}
        )
        pending = self.state()["participants"][self.base["coordinator"]]["pending"]
        assert "docs-result" not in pending
        assert "unrelated" in pending
