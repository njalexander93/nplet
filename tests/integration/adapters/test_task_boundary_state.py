"""Exercise pending-ticket, completion-state and shell-normalization boundaries in real stores."""

import copy
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from nplet.adapters import claude, codex, common, cursor, task_boundary
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.native_events import event
from tests.integration.adapters.task_boundary_support import allowed
from tests.platform_support import link_directory
from tests.support import Fixture

pytestmark = pytest.mark.integration


def test_pending_ticket_allows_unrelated_work_without_consuming_reservation(checkout: Path) -> None:
    """Keep exact selected-ticket correlation while free administration continues.

    Args:
        checkout: Initially unregistered real checkout.
    """
    # Explicit Task selection creates its existing assignment marker only.
    prompt = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(checkout),
        "session_id": "fresh",
        "prompt": "Task: TEST-1",
    }
    codex.handle(prompt)
    ticket = event(
        checkout, "codex", "mcp__codex_apps__linear__get_issue", {"id": "TEST-1"}, call="selected"
    )
    assert codex.handle(ticket) == {}
    before = {
        str(p.relative_to(checkout)): p.read_bytes()
        for p in (checkout / ".task").rglob("*")
        if p.is_file()
    }
    # Another issue read and an administrative update cannot bind or settle the selected call.
    for tool, args in [
        ("mcp__codex_apps__linear__get_issue", {"id": "TEST-2"}),
        ("mcp__codex_apps__linear__save_issue", {"title": "independent"}),
        ("Write", {"file_path": str(checkout.parent / "external/free.md"), "content": "free"}),
    ]:
        observed = event(checkout, "codex", tool, args, call="unrelated")
        assert codex.handle(observed) == {}
        assert (
            codex.handle(
                {**observed, "hook_event_name": "PostToolUse", "tool_response": {"content": []}}
            )
            == {}
        )
    after = {
        str(p.relative_to(checkout)): p.read_bytes()
        for p in (checkout / ".task").rglob("*")
        if p.is_file()
    }
    assert after == before


@pytest.mark.parametrize("route", ["archive", "spawn", "control"])
def test_pending_new_task_fences_codex_special_routes_without_state_changes(route: str) -> None:
    """Retain the old ready binding and pending work while blocking special mutations.

    Args:
        route: Real archive-provider or collaboration admission route.
    """
    # Keep a ready issue with a real frozen archive request and unrelated pending work.
    case = Fixture()
    case.setUp()
    try:
        case.create()
        case.ready()
        export = case.call("archive-prepare", expected_revision=case.state()["revision"])
        case.call("tool-start", tool_id="unrelated")
        codex.handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(case.root),
                "session_id": "coordinator",
                "prompt": "Task: TEST-2",
            }
        )
        before = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        tools = {
            "archive": ("mcp__codex_apps__linear_save_document", export["parts"][0]),
            "spawn": ("spawn_agent", {"message": "bounded reader", "fork_context": False}),
            "control": ("collaborationlist_agents", {}),
        }
        tool, args = tools[route]
        observed = event(case.root, "codex", tool, args, call="blocked-" + route)
        observed["session_id"] = "coordinator"
        # Native denial must precede archive uncertainty or child pending/receipt mutation.
        assert codex.handle(observed) == codex.denial("TICKET_READ_REQUIRED")
        after = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        assert after == before
    # Preserve normal fixture cleanup even when the regression exposes admission.
    finally:
        case.doCleanups()


def test_completion_uses_recorded_admission_after_target_changes() -> None:
    """Settle an admitted source call after its target becomes an external file.

    Raises:
        AssertionError: Native settlement loses admission or invents free-call pending work.
    """
    # Use the real locked core for task creation, readiness, admission and settlement.
    case = Fixture()
    case.setUp()
    try:
        case.create()
        case.ready()
        observed = {
            "hook_event_name": "PreToolUse",
            "cwd": str(case.root),
            "session_id": "coordinator",
            "tool_use_id": "protected",
            "tool_name": "Write",
            "tool_input": {"file_path": "src/protected.md", "content": "content"},
        }
        assert codex.handle(observed) == {}
        # Completion targets are mutable; only the recorded native call is authority.
        completed = copy.deepcopy(observed)
        completed.update(
            hook_event_name="PostToolUse",
            tool_input={"file_path": str(case.root.parent / "free.md"), "content": "content"},
            tool_response={"isError": False},
        )
        assert codex.handle(completed) == {}
        assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}
        assert codex.handle(completed) == {}
        # An untracked external callback cannot settle another pending source call.
        observed["tool_use_id"] = "still-pending"
        assert codex.handle(observed) == {}
        completed["tool_use_id"] = "foreign"
        assert codex.handle(completed) == {}
        assert (
            common.existing_admission({**completed, "tool_use_id": "still-pending"}, "codex")
            is not None
        )
    finally:
        case.doCleanups()


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_same_free_input_changes_identity_before_completion(host: str) -> None:
    """Ignore a free completion after its unchanged external target becomes a repository alias.

    Args:
        host: Native adapter whose callback carries the same call ID and input.
    """
    # One unrelated implementation call remains pending in the real bound issue.
    case = Fixture()
    case.setUp()
    adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
    case.base["host"] = {"codex": "codex", "claude": "claude-code", "cursor": "cursor"}[host]
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        case.ready()
        source = case.root / "src/app.py"
        source.parent.mkdir()
        source.write_text("source sentinel")
        external = case.root.parent / "external"
        external.mkdir()
        note = external / "research.md"
        implementation = event(
            case.root,
            host,
            "Write",
            {"file_path": str(source), "content": "planned"},
            call="implementation",
        )
        implementation.update(session_id="coordinator", conversation_id="coordinator")
        assert allowed(host, adapter.handle(implementation))
        # Free admission records no lifecycle work under this separate external call ID.
        observed = event(
            case.root, host, "Write", {"file_path": str(note), "content": "prose"}, call="free-note"
        )
        observed.update(session_id="coordinator", conversation_id="coordinator")
        assert allowed(host, adapter.handle(observed))
        before = copy.deepcopy(case.state())
        external.rmdir()
        link_directory(external, case.root)
        roots = task_boundary.root_test(core.repository(case.root)[2])
        assert task_boundary.target_role(str(case.root), str(note), roots) == "protected"
        # The host returns exactly the original input; only the filesystem identity changed.
        completed = {
            **observed,
            "hook_event_name": "postToolUse" if host == "cursor" else "PostToolUse",
            "tool_response": {"isError": False},
            "tool_output": "{}",
        }
        assert adapter.handle(completed) == {}
        assert case.state() == before
        implementation_id = (
            cursor.cursor_children.call_token("implementation")
            if host == "cursor"
            else "implementation"
        )
        assert implementation_id in before["participants"][case.base["coordinator"]]["pending"]
        assert source.read_text() == "source sentinel"
    finally:
        case.doCleanups()


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
@pytest.mark.parametrize("condition", ["pending", "stale"])
def test_free_work_preserves_bound_pending_or_stale_state(
    host: str, condition: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Permit reads, admin and external edits while retaining blocked implementation state.

    Args:
        host: Native parent adapter.
        condition: Reserved ticket or stale required source.
        monkeypatch: Supply the required foreground shell configuration.
    """
    # Establish a genuine bound participant, then independently block implementation.
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    case = Fixture()
    case.setUp()
    adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
    native_host = {"codex": "codex", "claude": "claude-code", "cursor": "cursor"}[host]
    case.base["host"] = native_host
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        case.ready()
        if condition == "pending":
            prompt = event(case.root, host, "Read", {}, name="UserPromptSubmit")
            prompt.update(
                session_id="coordinator", conversation_id="coordinator", prompt="Task: TEST-2"
            )
            if host == "cursor":
                prompt["hook_event_name"] = "beforeSubmitPrompt"
            adapter.handle(prompt)
        else:
            required = case.root / "required.md"
            # The native writer establishes safe source permissions, including Windows ACLs.
            with core.Directory.absolute(required.parent) as parent:
                parent.write(required.name, b"original required source")
            packet = [
                {
                    "id": "required",
                    "locator": str(required),
                    "sha256": core.sha(required.read_bytes()),
                    "required": True,
                    "authority": "assigned-input",
                    "reason": "test",
                    "stage": "implementation",
                    "reader": case.base["coordinator"],
                }
            ]
            case.call(
                "scope",
                expected_revision=case.state()["revision"],
                target_participant=case.base["coordinator"],
                packet=packet,
            )
            case.call("acknowledge", packet_digest=core.sha(core.canonical(packet)))
            required.write_text("changed required source")
            # Preserve readable ownership: later staleness must reflect changed bytes.
            assert core.external_source(str(required)) == b"changed required source"
        before = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        # Each host validates its native provider identity before the shared free effect.
        provider = (
            "mcp__codex_apps__linear__save_issue"
            if host == "codex"
            else "mcp__linear__save_issue"
            if host == "claude"
            else "MCP:save_issue"
        )
        for index, (tool, args) in enumerate(
            [
                ("Read", {"file_path": str(case.root / "src/app.py")}),
                ("Write", {"file_path": str(case.root.parent / "free.md"), "content": "prose"}),
                (provider, {"title": "independent administration"}),
            ]
        ):
            observed = event(case.root, host, tool, args, call="free-" + str(index))
            observed.update(session_id="coordinator", conversation_id="coordinator")
            if host == "claude" and tool == provider:
                observed["mcp_server"] = {"name": "linear", "source": "sdk"}
            assert allowed(host, adapter.handle(observed))
            if host == "cursor" and tool == provider:
                specific = {
                    **observed,
                    "hook_event_name": "beforeMCPExecution",
                    "tool_name": "save_issue",
                    "tool_input": json.dumps(args),
                    "mcp_server_name": "linear",
                    "mcp_server_url": "https://mcp.linear.app/mcp",
                }
                assert allowed(host, adapter.handle(specific))
            completed = {
                **observed,
                "hook_event_name": "postToolUse" if host == "cursor" else "PostToolUse",
                "tool_response": {"content": []},
                "tool_output": "{}",
            }
            assert adapter.handle(completed) == {}
        # Independent literal headline categories remain implementation under either fence.
        protected = (
            "notes/blocked.md",
            "src/blocked.py",
            "AGENTS.md",
            "CLAUDE.md",
            "package.json",
            "poetry.lock",
            ".github/workflows/build.yml",
        )
        for path in protected:
            blocked = event(case.root, host, "Write", {"file_path": path, "content": "blocked"})
            blocked.update(session_id="coordinator", conversation_id="coordinator")
            response = adapter.handle(blocked)
            assert not allowed(host, response)
            assert ("TICKET_READ_REQUIRED" if condition == "pending" else "SOURCE_STALE") in str(
                response
            )
            assert not (case.root / path).exists()
        # A package-manager command is protected even when its native transport is foreground.
        package = event(
            case.root,
            host,
            "Shell" if host == "cursor" else "Bash",
            {"command": "npm install package"},
        )
        package.update(session_id="coordinator", conversation_id="coordinator")
        assert not allowed(host, adapter.handle(package))
        assert not (case.root / "node_modules").exists()
        after = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        assert after == before
        assert not (case.root / "src/blocked.py").exists()
    finally:
        case.doCleanups()


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_registered_unbound_completion_does_not_create_missing_store_directories(host: str) -> None:
    """Ignore unadmitted callbacks without creating binding or issue-control directories.

    Args:
        host: Native parent adapter.
    """
    # Registration alone deliberately creates neither bindings nor the issues collection.
    case = Fixture()
    case.setUp()
    try:
        adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
        assert not (case.root / ".task/.bindings").exists()
        assert not (case.root / ".task/.control/issues").exists()
        observed = event(
            case.root,
            host,
            "Write",
            {"file_path": str(case.root.parent / "note.md"), "content": "prose"},
            name="PostToolUse",
        )
        observed.update(tool_response={"content": []}, tool_output="{}")
        before = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        assert adapter.handle(observed) == {}
        assert not (case.root / ".task/.bindings").exists()
        assert not (case.root / ".task/.control/issues").exists()
        after = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        assert after == before
    finally:
        case.doCleanups()


def test_completion_probe_rejects_transactional_or_corrupt_existing_state() -> None:
    """Distinguish absent admission from invalid registered storage without writing recovery.

    Raises:
        AssertionError: Invalid state is silently treated as no admission or is mutated.
    """
    # Create an admitted operation whose exact completion must consult existing state.
    case = Fixture()
    case.setUp()
    try:
        case.create()
        case.ready()
        observed = {
            "cwd": str(case.root),
            "session_id": "coordinator",
            "tool_name": "Write",
            "tool_input": {},
            "tool_use_id": "pending",
        }
        case.call("tool-start", tool_id="pending")
        control = case.root / ".task/.control/issues/TEST-1"
        transaction = control / "transaction.json"
        transaction.write_text("{}")
        # The probe refuses transaction uncertainty without rolling it forward.
        with pytest.raises(core.WorkspaceError) as captured:
            common.existing_admission(observed, "codex")
        assert captured.value.code == "RECOVERY_REQUIRED"
        assert transaction.read_text() == "{}"
        transaction.unlink()
        # Unsafe registered JSON remains an error rather than a task-free callback.
        state = control / "state.json"
        state.write_text("not JSON")
        with pytest.raises(core.WorkspaceError):
            common.existing_admission(observed, "codex")
        assert state.read_text() == "not JSON"
        # Losing registration beside durable control state is corruption, not fresh startup.
        registration = case.root / ".task/.repository.json"
        registration.unlink()
        with pytest.raises(core.WorkspaceError) as captured:
            common.existing_admission(observed, "codex")
        assert captured.value.code == "RECOVERY_REQUIRED"
        assert not registration.exists()
    finally:
        case.doCleanups()


def test_git_read_disables_an_actual_configured_fsmonitor(checkout: Path) -> None:
    """Prove the fixed Git prefix prevents a configured executable from running.

    Args:
        checkout: Disposable repository whose local configuration is isolated.
    """
    # A real configured hook demonstrates why the ordinary status spelling is gated.
    sentinel = checkout / "fsmonitor-ran"
    hook = checkout / ".git/fsmonitor-test"
    hook.write_text("#!/bin/sh\nprintf 'executed' > " + shlex.quote(sentinel.as_posix()) + "\n")
    hook.chmod(0o700)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "core.fsmonitor", hook.as_posix()], check=True
    )
    subprocess.run(
        ["git", "-C", str(checkout), "--no-pager", "status", "--short"],
        check=True,
        capture_output=True,
    )
    assert sentinel.read_text() == "executed"
    sentinel.unlink()
    # The admitted fixed prefix suppresses that executable without changing configuration.
    command = "git --no-pager -c core.fsmonitor=false status --short"
    assert task_boundary.read_command(command, "codex")
    assert not task_boundary.read_command("git --no-pager status --short", "codex")
    subprocess.run(shlex.split(command), cwd=checkout, check=True, capture_output=True)
    assert not sentinel.exists()


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("Bash", {"command": "pwd", "description": "read"}),
        ("Bash", {"command": "pwd", "working_directory": "."}),
        ("Shell", {"command": "pwd"}),
    ],
)
def test_codex_shell_normalization_does_not_expand_native_authority(
    checkout: Path, tool: str, args: dict
) -> None:
    """Keep unsupported Codex shell shapes gated without creating lifecycle storage.

    Args:
        checkout: Unregistered checkout used for the real native denial.
        tool: Observed Codex operation spelling.
        args: Metadata that must never be stripped to manufacture a task-free read.
    """
    # The native response must deny the original shape; helper-only checks are insufficient.
    observed = event(checkout, "codex", tool, args)
    result = subprocess.run(
        [sys.executable, "-m", "nplet.adapters.codex"],
        input=json.dumps(observed),
        text=True,
        capture_output=True,
        check=True,
    )
    response = json.loads(result.stdout)
    assert not allowed("codex", response)
    assert "REGISTRATION_REQUIRED" in str(response)
    assert not (checkout / ".task").exists()
