"""Exercise parent task-free admission and filesystem identities against real Git stores."""

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
    # Both native spellings name the same foreground read effect.
    subdirectory = checkout / "notes"
    subdirectory.mkdir()
    for args in (
        {"command": "pwd"},
        {"command": "pwd", "working_directory": str(checkout)},
        {"command": "pwd", "working_directory": str(subdirectory)},
    ):
        assert cursor.handle(event(checkout, "cursor", "Shell", args)) == {"permission": "allow"}
        assert not (checkout / ".task").exists()


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
    # Same-checkout identity alone grants no task-free authority to an unknown command.
    observed["tool_input"] = {"command": "npm install package", "working_directory": str(checkout)}
    assert "REGISTRATION_REQUIRED" in str(cursor.handle(observed))
    assert not (checkout / ".task").exists()
    assert not (foreign / ".task").exists()


def commit_and_link_worktree(root: Path) -> Path:
    """Commit an empty history and add one linked worktree beside the checkout.

    Args:
        root: Real Git checkout.

    Returns:
        Canonical linked worktree path.
    """
    # A linked worktree needs a commit to branch from.
    subprocess.run(
        [
            "git",
            *("-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid"),
            *("commit", "--allow-empty", "-qm", "fixture"),
        ],
        check=True,
    )
    linked = root.parent / "linked"
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-q", "-b", "linked", str(linked)], check=True
    )
    return linked.resolve()


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_fresh_reads_and_external_edits_leave_no_task_while_repository_edits_are_denied(
    checkout: Path, tmp_path: Path, host: str
) -> None:
    """Permit reads and edits outside the repository; deny every in-repository edit.

    Args:
        checkout: Real checkout with no task registration.
        tmp_path: Disposable parent for external, vault and non-Git targets.
        host: Native adapter.
    """
    adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
    # A separate Git repository stands in for a vault; it is not project code.
    vault = tmp_path / "vault"
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    # Reads and edits outside the repository require neither binding nor lifecycle setup.
    for tool, args in [
        ("Read", {"file_path": str(checkout / "src/app.py")}),
        ("Write", {"file_path": str(tmp_path / "outside.md"), "content": "prose"}),
        ("Write", {"file_path": str(vault / "note.md"), "content": "prose"}),
        ("Write", {"file_path": str(tmp_path / "plain/record.txt"), "content": "prose"}),
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
        assert not (vault / ".task").exists()
        assert not Path(args["file_path"]).exists() or tool == "Read"
    # Denied native calls have no target effect and cannot manufacture registration.
    for path in [
        "notes/research.md",
        "README.md",
        "docs/draft.md",
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
def test_linked_worktrees_are_project_code_for_each_other(checkout: Path, host: str) -> None:
    """Deny edits that cross between the main checkout and its linked worktree.

    Args:
        checkout: Real main checkout.
        host: Native adapter.
    """
    adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
    linked = commit_and_link_worktree(checkout)
    # Either worktree is part of the session's repository, whichever one is the cwd.
    for cwd, target in ((checkout, linked / "notes/x.md"), (linked, checkout / "notes/x.md")):
        observed = event(cwd, host, "Write", {"file_path": str(target), "content": "blocked"})
        try:
            response = adapter.handle(observed)
        except core.WorkspaceError as error:
            assert host == "codex"
            response = codex.failure("PreToolUse", error.code)
        assert not allowed(host, response)
        assert not target.exists()
    assert not (checkout / ".task").exists()


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_session_outside_any_repository_edits_without_a_gate(
    checkout: Path, tmp_path: Path, host: str
) -> None:
    """Admit every edit from a non-Git cwd, including one aimed into a repository.

    A cwd inside no Git repository has no project roots, so this hook treats every
    target as external; host permissions remain the only control.

    Args:
        checkout: Real checkout that the session does not belong to.
        tmp_path: Disposable parent for the non-Git session directory.
        host: Native adapter.
    """
    adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
    plain = tmp_path / "plain"
    plain.mkdir()
    # Both a local file and a file in an unrelated repository are external here.
    for target in (plain / "x.md", checkout / "src/app.py"):
        observed = event(plain, host, "Write", {"file_path": str(target), "content": "prose"})
        assert adapter.handle(observed) == ({"permission": "allow"} if host == "cursor" else {})
        completed = {
            **observed,
            "hook_event_name": "postToolUse" if host == "cursor" else "PostToolUse",
            "tool_response": {"isError": False},
            "tool_output": "{}",
        }
        assert adapter.handle(completed) == {}
    assert not (plain / ".task").exists()
    assert not (checkout / ".task").exists()


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_failed_repository_discovery_is_not_absence(
    checkout: Path, tmp_path: Path, host: str
) -> None:
    """Keep a repository with unreadable Git configuration readiness-gated.

    Args:
        checkout: Real checkout whose configuration is corrupted.
        tmp_path: Disposable parent for a genuinely non-Git directory.
        host: Native adapter.
    """
    adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
    # A broken configuration makes every Git query fail without proving Git is absent.
    (checkout / ".git/config").write_text("[broken\n")
    with pytest.raises(core.WorkspaceError):
        core.project_repository(str(checkout))
    # The unbound in-repository edit is denied and leaves no target or task state.
    target = checkout / "src/app.py"
    observed = event(checkout, host, "Write", {"file_path": str(target), "content": "blocked"})
    try:
        response = adapter.handle(observed)
    except core.WorkspaceError as error:
        assert host == "codex"
        response = codex.failure("PreToolUse", error.code)
    assert not allowed(host, response)
    assert not target.exists()
    assert not (checkout / ".task").exists()
    # A directory with no Git metadata on its ancestor chain is still proven external.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert core.project_repository(str(plain)) is None


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


def test_real_aliases_follow_the_repository_directory(checkout: Path, tmp_path: Path) -> None:
    """Gate a target when either its lexical or resolved spelling is inside the repository.

    Args:
        checkout: Canonical Git checkout.
        tmp_path: Disposable parent for links and external directories.
    """
    roots = task_boundary.root_identities(core.repository(checkout)[2])
    outside = tmp_path / "plain"
    outside.mkdir()
    # A link outside the repository that points into it is protected.
    alias = tmp_path / "alias"
    link_directory(alias, checkout)
    assert task_boundary.target_role(str(checkout), str(alias / "notes/x.md"), roots) == "protected"
    # An alias spelling of the checkout as cwd still names the repository.
    assert task_boundary.target_role(str(alias), "notes/x.md", roots) == "protected"
    # A link inside the repository that points outside it is protected.
    link_directory(checkout / "outlink", outside)
    assert task_boundary.target_role(str(checkout), "outlink/x.md", roots) == "protected"
    # Lexical traversal is judged by where it lands.
    assert task_boundary.target_role(str(checkout), "notes/../../outside.md", roots) == "external"
    into = str(outside / ".." / "src/build/checkout/notes/x.md")
    assert task_boundary.target_role(str(outside), into, roots) == "protected"


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
@pytest.mark.parametrize("layout", ["external-control", "internal-control-alias", "root-alias"])
def test_ready_hosts_deny_control_aliases_without_changing_state(host: str, layout: str) -> None:
    """Deny raw control writes even after readiness and through directory aliases.

    Args:
        host: Native adapter whose process must emit a denial.
        layout: Lexical external control link, ordinary alias to an internal control, or
            control name aliasing the repository root.
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
            sentinel = target / "config.md"
        elif layout == "internal-control-alias":
            target = case.root / ".codex"
            target.mkdir()
            link_directory(case.root / "notes", target)
            paths = [case.root / "notes/config.md"]
            sentinel = target / "config.md"
        else:
            target = case.root
            link_directory(case.root / ".codex", target)
            paths = [case.root / ".codex/hooks.json"]
            sentinel = target / "hooks.json"
        # Preserve target contents and every existing task file before native admission.
        sentinel.write_text("control sentinel")
        before = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        # Exercise each native process, including Codex's exception-to-denial boundary.
        for index, path in enumerate(paths):
            roots = task_boundary.root_identities(core.repository(case.root)[2])
            assert task_boundary.target_role(str(case.root), str(path), roots) == "control"
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


def test_update_patch_admits_external_targets_but_not_repository_targets(
    checkout: Path, tmp_path: Path
) -> None:
    """Keep external-only patches task-free and gate any patch touching the repository.

    Args:
        checkout: Real unregistered repository.
        tmp_path: Disposable parent for the external target.
    """
    # An external-only update patch has no project code effect.
    external = tmp_path / "vault/note.md"
    external.parent.mkdir()
    external.write_text("old\n")
    patch = (
        f"*** Begin Patch\n*** Update File: {external}\n@@\n-old\n+new\n"
        "*** End of File\n*** End Patch"
    )
    observed = event(checkout, "codex", "apply_patch", {"command": patch})
    assert codex.handle(observed) == {}
    # Mixed updates and moves in either direction across the boundary gate the whole call.
    note, source = checkout / "notes/x.md", checkout / "src/app.py"
    for path in (note, source):
        path.parent.mkdir()
        path.write_text("old\n")
    mixed_update = patch.replace(
        "*** End Patch",
        "*** Update File: src/app.py\n@@\n-old\n+new\n*** End of File\n*** End Patch",
    )
    move_out = (
        f"*** Begin Patch\n*** Update File: notes/x.md\n*** Move to: {external}\n@@\n-old\n"
        "+new\n*** End Patch"
    )
    move_in = patch.replace(
        f"*** Update File: {external}", f"*** Update File: {external}\n*** Move to: src/app.py"
    )
    for mixed in (mixed_update, move_out, move_in):
        with pytest.raises(core.WorkspaceError) as failure:
            codex.handle({**observed, "tool_input": {"command": mixed}})
        assert failure.value.code in {"REGISTRATION_REQUIRED", "BINDING_MISSING"}
    assert external.read_text() == "old\n"
    assert note.read_text() == "old\n"
    assert source.read_text() == "old\n"
    assert not (checkout / ".task").exists()


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
        roots = task_boundary.root_identities(core.repository(case.root)[2])
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


def test_mixed_patch_denies_both_external_and_repository_targets(
    checkout: Path, tmp_path: Path
) -> None:
    """Deny the whole patch and retain both real target sentinels.

    Args:
        checkout: Unregistered repository.
        tmp_path: Disposable parent for the external target.
    """
    # An external target cannot grant task-free admission to a repository patch target.
    note, source = tmp_path / "note.md", checkout / "src/app.py"
    source.parent.mkdir()
    note.write_text("note sentinel")
    source.write_text("source sentinel")
    patch = (
        f"*** Begin Patch\n*** Update File: {note}\n@@\n-note sentinel\n+changed\n"
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
