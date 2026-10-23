"""Exercise filesystem containment, discovery and control aliases against real Git stores."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nplet.adapters import claude, codex, cursor, task_boundary
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.native_events import event
from tests.integration.adapters.task_boundary_support import (
    allowed,
    commit_and_link_worktree,
)
from tests.platform_support import link_directory
from tests.support import Fixture

pytestmark = pytest.mark.integration


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
def test_linked_worktree_missing_on_disk_keeps_its_listed_path_protected(
    checkout: Path, host: str
) -> None:
    """Deny an edit into a linked worktree that Git still lists after it moved away.

    Args:
        checkout: Real main checkout.
        host: Native adapter.
    """
    adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
    # Renaming without pruning leaves Git listing a root that no longer exists on disk.
    linked = commit_and_link_worktree(checkout)
    os.rename(linked, linked.parent / "moved")
    # The unbound edit under the listed path is denied and leaves no target or task state.
    target = linked / "src/app.py"
    observed = event(checkout, host, "Write", {"file_path": str(target), "content": "blocked"})
    try:
        response = adapter.handle(observed)
    except core.WorkspaceError as error:
        assert host == "codex"
        response = codex.failure("PreToolUse", error.code)
    assert not allowed(host, response)
    assert not target.exists()
    assert not (checkout / ".task").exists()
    # A plain external file beside the missing root is still admitted.
    external = linked.parent / "external.md"
    observed = event(checkout, host, "Write", {"file_path": str(external), "content": "prose"})
    assert allowed(host, adapter.handle(observed))
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
    roots = task_boundary.root_test(core.repository(checkout)[2])
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
            roots = task_boundary.root_test(core.repository(case.root)[2])
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
