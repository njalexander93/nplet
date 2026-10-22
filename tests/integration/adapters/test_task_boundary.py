"""Exercise parent task-free admission and filesystem identities against real Git stores."""

import copy
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from nplet.adapters import claude, codex, common, cursor, task_boundary
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.native_events import event
from tests.platform_support import link_directory
from tests.support import Fixture

pytestmark = pytest.mark.integration


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """Create a real unregistered checkout beneath misleading ancestor role names.

    Args:
        tmp_path: Disposable test parent.

    Returns:
        Canonical unregistered repository path.
    """
    # Ancestor names must never become checkout-relative protected roles.
    root = tmp_path / "src" / "build" / "checkout"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root.resolve()


def allowed(host: str, response: dict) -> bool:
    """Read the native decision without treating an advisory message as admission.

    Args:
        host: Selected adapter.
        response: Actual native response.

    Returns:
        Whether the prehook admitted this call.
    """
    return (
        response.get("permission") == "allow"
        if host == "cursor"
        else response.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    )


def test_cursor_same_checkout_shell_override_remains_task_free(checkout: Path) -> None:
    """Treat an already validated local working directory like the plain read form.

    Args:
        checkout: Unregistered checkout whose state must remain absent.
    """
    # Native metadata does not change a proven read into protected work.
    subdirectory = checkout / "notes"
    subdirectory.mkdir()
    for args in (
        {"command": "pwd"},
        {"command": "pwd", "working_directory": str(checkout)},
        {"command": "pwd", "working_directory": str(subdirectory)},
        {"command": "pwd", "cwd": "", "timeout": 30000},
        {"command": "pwd", "cwd": str(checkout), "timeout": 30000},
        {"command": "pwd", "cwd": str(subdirectory), "timeout": 30000},
        {
            "command": "git --no-pager -c core.fsmonitor=false status --short",
            "cwd": "",
            "timeout": 30000,
        },
    ):
        assert cursor.handle(event(checkout, "cursor", "Shell", args)) == {"permission": "allow"}
        assert not (checkout / ".task").exists()


@pytest.mark.parametrize("command", ["npm install package", "git rev-parse HEAD", "pwd && touch x"])
def test_cursor_shell_metadata_requires_readiness(checkout: Path, command: str) -> None:
    """Keep native timeout metadata from bypassing protected-work readiness.

    Args:
        checkout: Unregistered repository whose lifecycle storage must remain absent.
        command: Mutation or command outside the existing finite read grammar.
    """
    # Use the same native metadata as the installed Cursor pwd callback.
    observed = event(checkout, "cursor", "Shell", {"command": command, "cwd": "", "timeout": 30000})
    response = cursor.handle(observed)
    assert response["permission"] == "deny"
    assert "REGISTRATION_REQUIRED" in str(response)
    assert not (checkout / ".task").exists()
    assert not (checkout / "node_modules").exists()
    assert not (checkout / "x").exists()


@pytest.mark.parametrize(
    ("command", "failure_type", "error_message"),
    [
        ("true", "timeout", "Timed out"),
        ("exit 7", "error", "Command failed with exit code 7"),
    ],
)
def test_cursor_read_metadata_preserves_existing_pending_work(
    command: str, failure_type: str, error_message: str
) -> None:
    """Retain pending work and actionable recovery for untyped native Shell failures.

    Args:
        command: Protected Shell command whose original call must remain pending.
        failure_type: Timeout or the observed nonzero-exit failure callback classification.
        error_message: Native failure text, which supplies no typed terminal evidence.
    """
    # Establish actual ready Cursor state and a separate admitted implementation call.
    case = Fixture()
    case.setUp()
    case.base["host"] = "cursor"
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        protected = event(
            case.root,
            "cursor",
            "Shell",
            {"command": command, "cwd": "", "timeout": 30000},
            call="protected",
        )
        protected.update(session_id="coordinator", conversation_id="coordinator")
        # Valid transport cannot admit protected work before packet readiness.
        unready = copy.deepcopy(case.state())
        response = cursor.handle(protected)
        assert response["permission"] == "deny"
        assert "NOT_READY" in str(response)
        assert case.state() == unready
        case.ready()
        assert cursor.handle(protected) == {"permission": "allow"}
        before = copy.deepcopy(case.state())
        pending = before["participants"][case.base["coordinator"]]["pending"]
        assert cursor.cursor_children.call_token("protected") in pending
        # A free read and its background result have no recorded lifecycle admission.
        observed = event(
            case.root,
            "cursor",
            "Shell",
            {"command": "pwd", "cwd": "", "timeout": 30000},
            call="free-read",
        )
        observed.update(session_id="coordinator", conversation_id="coordinator")
        assert cursor.handle(observed) == {"permission": "allow"}
        assert (
            cursor.handle(
                {
                    **observed,
                    "hook_event_name": "postToolUse",
                    "tool_output": json.dumps({"backgroundTaskId": "read-background"}),
                }
            )
            == {}
        )
        # Neither timeout nor textual exit status supplies typed terminal evidence.
        uncertain = {
            **protected,
            "hook_event_name": "postToolUseFailure",
            "error_message": error_message,
            "failure_type": failure_type,
            "is_interrupt": False,
        }
        response = cursor.handle(uncertain)
        assert "HOST_UNSUPPORTED_ASYNC" in str(response)
        assert "do not rerun" in str(response)
        assert "original native terminal result" in str(response)
        assert "independently verifying termination" in str(response)
        assert "tool-complete" in str(response)
        assert case.state() == before
        assert not (case.root / "node_modules").exists()
    # Fixture cleanup owns both registered repositories on assertion failure as well.
    finally:
        case.doCleanups()


def test_cursor_shell_override_rejects_foreign_checkout_and_unknown_effect(
    checkout: Path, tmp_path: Path
) -> None:
    """Retain foreign checkout denial and readiness for mutation-capable commands.

    Args:
        checkout: Unregistered native checkout.
        tmp_path: Disposable parent for an unrelated Git repository.
    """
    # Native identity validation must reject an override before effect normalization.
    foreign = tmp_path / "foreign"
    subprocess.run(["git", "init", "-q", str(foreign)], check=True)
    observed = event(
        checkout, "cursor", "Shell", {"command": "pwd", "working_directory": str(foreign)}
    )
    assert "REPOSITORY_MISMATCH" in str(cursor.handle(observed))
    observed["tool_input"] = {
        "command": "pwd",
        "cwd": str(foreign),
        "timeout": 30000,
    }
    response = cursor.handle(observed)
    assert response["permission"] == "deny"
    assert "REPOSITORY_MISMATCH" in str(response)
    # Same-checkout identity alone grants no task-free authority to an unknown command.
    observed["tool_input"] = {"command": "npm install package", "working_directory": str(checkout)}
    assert "REGISTRATION_REQUIRED" in str(cursor.handle(observed))
    assert not (checkout / ".task").exists()
    assert not (foreign / ".task").exists()


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_fresh_reads_notes_and_protected_denials_leave_no_task(checkout: Path, host: str) -> None:
    """Permit independent work while source, dependencies and controls remain unchanged.

    Args:
        checkout: Real checkout with no task registration.
        host: Native adapter.
    """
    adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
    # Read tools and ordinary notes require neither binding nor lifecycle setup.
    for tool, args in [
        ("Read", {"file_path": str(checkout / "src/app.py")}),
        ("Write", {"file_path": str(checkout / "notes/research.md"), "content": "prose"}),
    ]:
        observed = event(checkout, host, tool, args)
        assert allowed(host, adapter.handle(observed))
        completed = {
            **observed,
            "hook_event_name": "postToolUse" if host == "cursor" else "PostToolUse",
            "tool_response": {"isError": False},
            "tool_output": "{}",
        }
        assert adapter.handle(completed) == {}
        assert not (checkout / ".task").exists()
    # Denied native calls have no target effect and cannot manufacture registration.
    for path in [
        "src/app.py",
        "tests/test_app.py",
        "scripts/run.md",
        "requirements.txt",
        "requirements-dev.txt",
        "AGENTS.md",
        "CLAUDE.md",
        "package.json",
        "poetry.lock",
        "docs/CMakeLists.txt",
        ".claude/settings.json",
        ".cursor/hooks.json",
        ".codex/hooks.json",
        "constraints.in",
        "deps/list.txt",
        "skills/example/references/instructions.md",
        "hooks/README.md",
        ".github/workflows/build.yml",
        ".task/context/note.md",
        ".codex/notes.md",
    ]:
        observed = event(
            checkout, host, "Write", {"file_path": str(checkout / path), "content": "blocked"}
        )
        try:
            response = adapter.handle(observed)
        except core.WorkspaceError as error:
            assert host == "codex"
            response = codex.failure("PreToolUse", error.code)
        assert not allowed(host, response)
        assert not (checkout / path).exists()
        assert not (checkout / ".task").exists()


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_native_package_mutation_denial_preserves_manifest_and_absent_task(
    checkout: Path, host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deny a representative package mutation through each real native adapter process.

    Args:
        checkout: Unregistered repository with a protected manifest sentinel.
        host: Native adapter shape under test.
        monkeypatch: Supply Claude's required synchronous shell configuration.
    """
    # Native foreground validation must run before the protected command requires a task.
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    manifest = checkout / "package.json"
    manifest.write_text('{"sentinel": true}\n')
    before = manifest.read_bytes()
    observed = event(
        checkout,
        host,
        "Shell" if host == "cursor" else "Bash",
        {"command": "npm install package"},
    )
    result = subprocess.run(
        [sys.executable, "-m", "nplet.adapters." + host],
        input=json.dumps(observed),
        text=True,
        capture_output=True,
        check=True,
    )
    assert not allowed(host, json.loads(result.stdout))
    assert manifest.read_bytes() == before
    assert not (checkout / "node_modules").exists()
    assert not (checkout / ".task").exists()


@pytest.mark.parametrize(
    "relative", ["notes/research.md", "docs/draft-review.md", "notes/record.txt"]
)
def test_real_checkout_aliases_preserve_note_identity(
    checkout: Path, relative: str, tmp_path: Path
) -> None:
    """Allow canonical and alias spellings in either direction.

    Args:
        checkout: Canonical Git checkout.
        relative: Ordinary note role.
        tmp_path: Disposable alias parent.
    """
    # Directory symlinks/junctions exercise actual filesystem aliases.
    alias = tmp_path / "alias"
    link_directory(alias, checkout)
    assert task_boundary.target_role(str(alias), str(checkout / relative)) == "note"
    assert task_boundary.target_role(str(checkout), str(alias / relative)) == "note"
    assert task_boundary.target_role(str(alias), str(alias / relative)) == "note"


def test_internal_links_hardlinks_and_executable_notes_remain_protected(checkout: Path) -> None:
    """Prevent lexical source roles and shared inode identities from becoming notes.

    Args:
        checkout: Canonical checkout.
    """
    # An internal back-link must not erase the lexical source directory.
    (checkout / "src").mkdir()
    link_directory(checkout / "src/back", checkout)
    assert task_boundary.target_role(str(checkout), "src/back/docs/note.md") == "protected"
    # A hardlink to protected source retains the shared identity despite its suffix.
    source = checkout / "src/source.py"
    source.write_text("source")
    os.link(source, checkout / "note.md")
    assert task_boundary.target_role(str(checkout), "note.md") == "protected"
    # Existing executable prose cannot receive the ordinary note exemption.
    executable = checkout / "executable.md"
    executable.write_text("#!/bin/sh")
    if os.name != "nt":
        executable.chmod(0o700)
        assert task_boundary.target_role(str(checkout), "executable.md") == "protected"
    assert task_boundary.target_role(str(checkout), "program.exe") == "protected"


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
@pytest.mark.parametrize("layout", ["external-control", "internal-control-alias"])
def test_ready_hosts_deny_control_aliases_without_changing_state(host: str, layout: str) -> None:
    """Deny raw control writes even after readiness and through directory aliases.

    Args:
        host: Native adapter whose process must emit a denial.
        layout: Lexical external control link or ordinary alias to an internal control.
    """
    # Use genuine ready state and unrelated admitted work to expose readiness fallthrough.
    case = Fixture()
    case.setUp()
    case.base["host"] = {"codex": "codex", "claude": "claude-code", "cursor": "cursor"}[host]
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        case.ready()
        case.call("tool-start", tool_id="unrelated")
        # Build real links in both directions across the control ownership boundary.
        if layout == "external-control":
            target = case.root.parent / "external"
            target.mkdir()
            paths = []
            for control in (".codex", ".claude", ".cursor", ".agents"):
                link_directory(case.root / control, target)
                paths.append(case.root / control / "config.md")
        else:
            target = case.root / ".codex"
            target.mkdir()
            link_directory(case.root / "notes", target)
            paths = [case.root / "notes/config.md"]
        # Preserve target contents and every existing task file before native admission.
        sentinel = target / "config.md"
        sentinel.write_text("control sentinel")
        before = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        # Exercise each native process, including Codex's exception-to-denial boundary.
        for index, path in enumerate(paths):
            assert task_boundary.target_role(str(case.root), str(path)) == "control"
            observed = event(
                case.root,
                host,
                "Write",
                {"file_path": str(path), "content": "blocked"},
                call="control-" + str(index),
            )
            observed.update(session_id="coordinator", conversation_id="coordinator")
            result = subprocess.run(
                [sys.executable, "-m", "nplet.adapters." + host],
                input=json.dumps(observed),
                text=True,
                capture_output=True,
                check=True,
            )
            response = json.loads(result.stdout)
            assert not allowed(host, response)
            assert "NOT_OWNER" in str(response)
            assert sentinel.read_text() == "control sentinel"
        # No denied call may append admission, revision or settlement state.
        after = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        assert after == before
    # Release both registered worktrees even when a native assertion fails.
    finally:
        case.doCleanups()


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
        ("Write", {"file_path": "notes/free.md", "content": "free"}),
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


def test_update_patch_end_of_file_admits_notes_but_not_mixed_source(checkout: Path) -> None:
    """Keep valid note update patches task-free without discarding other protected targets.

    Args:
        checkout: Real unregistered repository.
    """
    # A valid native update marker does not introduce a new target or implementation effect.
    note = checkout / "notes/note.md"
    note.parent.mkdir()
    note.write_text("old\n")
    patch = (
        "*** Begin Patch\n*** Update File: notes/note.md\n@@\n-old\n+new\n"
        "*** End of File\n*** End Patch"
    )
    observed = event(checkout, "codex", "apply_patch", {"command": patch})
    assert codex.handle(observed) == {}
    # Moving or updating a protected target gates the entire call, preserving both files.
    source = checkout / "src/app.py"
    source.parent.mkdir()
    source.write_text("old\n")
    mixed_update = patch.replace(
        "*** End Patch",
        "*** Update File: src/app.py\n@@\n-old\n+new\n*** End of File\n*** End Patch",
    )
    mixed_move = patch.replace(
        "*** Update File: notes/note.md",
        "*** Update File: notes/note.md\n*** Move to: src/app.py",
    )
    for mixed in (mixed_update, mixed_move):
        with pytest.raises(core.WorkspaceError) as failure:
            codex.handle({**observed, "tool_input": {"command": mixed}})
        assert failure.value.code in {"REGISTRATION_REQUIRED", "BINDING_MISSING"}
    assert note.read_text() == "old\n"
    assert source.read_text() == "old\n"
    assert not (checkout / ".task").exists()


def test_completion_uses_recorded_admission_after_target_changes() -> None:
    """Settle an admitted source call after its target becomes ordinary prose.

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
            tool_input={"file_path": "notes/free.md", "content": "content"},
            tool_response={"isError": False},
        )
        assert codex.handle(completed) == {}
        assert case.state()["participants"][case.base["coordinator"]]["pending"] == {}
        assert codex.handle(completed) == {}
        # An untracked note callback cannot settle another pending source call.
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


def test_codex_process_denies_fresh_implementation(checkout: Path) -> None:
    """Verify the supervised native JSON denial, rather than only a raised exception.

    Args:
        checkout: Real unregistered checkout.
    """
    # The actual entry point supervises errors and emits Codex's blocking prehook decision.
    observed = event(
        checkout, "codex", "Write", {"file_path": "src/blocked.py", "content": "blocked"}
    )
    result = subprocess.run(
        [sys.executable, "-m", "nplet.adapters.codex"],
        input=json.dumps(observed),
        text=True,
        capture_output=True,
        check=True,
    )
    response = json.loads(result.stdout)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not (checkout / "src/blocked.py").exists()
    assert not (checkout / ".task").exists()


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_same_note_input_changes_identity_before_completion(host: str) -> None:
    """Ignore a free completion after its unchanged target becomes a protected hardlink.

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
        note = case.root / "notes/research.md"
        note.parent.mkdir()
        note.write_text("ordinary prose")
        implementation = event(
            case.root,
            host,
            "Write",
            {"file_path": str(source), "content": "planned"},
            call="implementation",
        )
        implementation.update(session_id="coordinator", conversation_id="coordinator")
        assert allowed(host, adapter.handle(implementation))
        # Free admission records no lifecycle work under this separate note call ID.
        observed = event(
            case.root, host, "Write", {"file_path": str(note), "content": "prose"}, call="free-note"
        )
        observed.update(session_id="coordinator", conversation_id="coordinator")
        assert allowed(host, adapter.handle(observed))
        before = copy.deepcopy(case.state())
        note.unlink()
        os.link(source, note)
        assert task_boundary.target_role(str(case.root), str(note)) == "protected"
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
    """Permit reads, admin and notes while retaining exact blocked implementation state.

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
                ("Write", {"file_path": "notes/free.md", "content": "prose"}),
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


def test_mixed_patch_denies_both_note_and_source(checkout: Path) -> None:
    """Deny the whole patch and retain both real target sentinels.

    Args:
        checkout: Unregistered repository.
    """
    # A note target cannot grant task-free admission to another protected patch target.
    note, source = checkout / "note.md", checkout / "src/app.py"
    source.parent.mkdir()
    note.write_text("note sentinel")
    source.write_text("source sentinel")
    patch = (
        "*** Begin Patch\n*** Update File: note.md\n@@\n-note sentinel\n+changed\n"
        "*** Update File: src/app.py\n@@\n-source sentinel\n+changed\n*** End Patch"
    )
    observed = event(checkout, "codex", "apply_patch", {"command": patch})
    result = subprocess.run(
        [sys.executable, "-m", "nplet.adapters.codex"],
        input=json.dumps(observed),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert note.read_text() == "note sentinel"
    assert source.read_text() == "source sentinel"
    assert not (checkout / ".task").exists()


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
            {"file_path": "notes/note.md", "content": "prose"},
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
