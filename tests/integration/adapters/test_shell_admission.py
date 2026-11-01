"""Exercise native shell admission by recognized file effects, approval and scope.

Roadmap approval text is synthetic; these tests do not prove human approval.
"""

from pathlib import Path

import pytest

from nplet.adapters import claude, common
from tests.integration.adapters import test_edit_admission as edits
from tests.integration.adapters.native_events import event
from tests.integration.adapters.task_boundary_support import (
    allowed,
    pre_decision,
    task_store_snapshot,
)
from tests.integration.adapters.test_edit_admission import PNG, Repos, write
from tests.support import Fixture

pytestmark = pytest.mark.integration
# Share the structured-edit repositories and ready coordinator fixtures.
ready, repos = edits.ready, edits.repos

SHELL_HOSTS = ["claude", "cursor"]
# Shell work whose only parts are unchecked or add no file effect.
UNCHECKED = ["python build.py", "python build.py 2>&1", "python build.py > /dev/null"]


@pytest.fixture(autouse=True)
def foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply the foreground shell configuration Claude Bash admission requires.

    Args:
        monkeypatch: Scoped environment override.
    """
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")


def shell(
    cwd: Path, host: str, command: str, call: str, bound: bool = False, directory: str = ""
) -> dict:
    """Run one native shell prehook and return the native decision.

    Args:
        cwd: Session working directory.
        host: ``claude`` or ``cursor``.
        command: Shell command text.
        call: Native call identifier.
        bound: Whether the call comes from the fixture coordinator session.
        directory: Cursor's native working-directory override, when nonempty.

    Returns:
        The adapter's native response.
    """
    args = {"command": command}
    if directory:
        args["working_directory"] = directory
    observed = event(cwd, host, "Shell" if host == "cursor" else "Bash", args, call=call)
    if bound:
        observed.update(session_id="coordinator", conversation_id="coordinator")
    return pre_decision(host, observed)


@pytest.mark.parametrize("host", SHELL_HOSTS)
def test_unbound_binary_copy_is_free_but_replacing_text_is_protected(
    checkout: Path, repos: Repos, host: str
) -> None:
    """Free a binary copy or move into another repository; deny replacing its text.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
        host: Native adapter.
    """
    (repos.plain / "a.png").write_bytes(PNG)
    (repos.c / "src/t.md").write_text("text")
    for index, command in enumerate(
        (
            f"cp {repos.plain.as_posix()}/a.png {repos.c.as_posix()}/src/b.png",
            f"mv {repos.plain.as_posix()}/a.png {repos.c.as_posix()}/src/",
        )
    ):
        assert allowed(host, shell(checkout, host, command, f"free-{index}"))
    assert not (checkout / ".task").exists()
    replace = f"cp {repos.plain.as_posix()}/a.png {repos.c.as_posix()}/src/t.md"
    assert not allowed(host, shell(checkout, host, replace, "protected"))
    unknown = f'cp -- "$SOURCE" {repos.c.as_posix()}/src/t.md'
    assert not allowed(host, shell(checkout, host, unknown, "unknown-source"))
    assert allowed(host, shell(checkout, host, f"[[ a > {repos.c.as_posix()}/src/t.md ]]", "test"))
    # Text written to the binary source earlier in the command is what gets copied.
    (repos.plain / "a.png").write_bytes(PNG)
    rewrite = (
        f"echo hello > {repos.plain.as_posix()}/a.png; "
        f"cp {repos.plain.as_posix()}/a.png {repos.c.as_posix()}/src/new.txt"
    )
    assert not allowed(host, shell(checkout, host, rewrite, "rewritten"))
    # A no-clobber copy or a skipped overwrite may leave text that is then copied.
    (repos.plain / "t.txt").write_text("text")
    plain = repos.plain.as_posix()
    for index, step in enumerate(
        (f"cp -n {plain}/a.png {plain}/t.txt", f"false && cp {plain}/a.png {plain}/t.txt")
    ):
        command = f"{step}; cp {plain}/t.txt {repos.c.as_posix()}/src/kept-{index}.txt"
        assert not allowed(host, shell(checkout, host, command, f"kept-{index}"))


@pytest.mark.parametrize("ready", SHELL_HOSTS, indirect=True)
def test_shell_effects_match_structured_edit_outcomes(
    ready: tuple[str, Fixture], repos: Repos
) -> None:
    """Give a recognized shell effect the same outcome as the equivalent Write.

    Args:
        ready: Adapter name and its ready coordinator fixture.
        repos: Other repositories and the vault policy.
    """
    host, case = ready
    root = case.root

    def outcomes(target: Path, call: str) -> tuple[dict, dict]:
        """Run the shell and structured forms of one workspace edit.

        Args:
            target: Workspace edit target.
            call: Native call identifier prefix.

        Returns:
            The shell and structured native responses.
        """
        command = f"rm -f {target.relative_to(root).as_posix()}"
        return shell(root, host, command, call, bound=True), write(
            root, host, target, call + "-write", bound=True
        )

    case.approve({root: ["src/"]}, approved=False)
    for response in outcomes(root / "src/x.py", "pending"):
        assert "APPROVAL_REQUIRED" in str(response)
    case.approve({root: ["src/"]})
    for response in outcomes(root / "README.md", "outside"):
        assert "SCOPE_REQUIRED" in str(response)
    for response in outcomes(root / "src/x.py", "inside"):
        assert allowed(host, response)


@pytest.mark.parametrize("ready", SHELL_HOSTS, indirect=True)
def test_shell_policy_and_control_targets_are_denied_under_whole_scope(
    ready: tuple[str, Fixture], repos: Repos
) -> None:
    """Deny the policy file and control stores even for a ready ``./`` scope.

    Args:
        ready: Adapter name and its ready coordinator fixture.
        repos: Other repositories and the vault policy.
    """
    host, case = ready
    root = case.root
    case.approve({root: ["./"], repos.policy.parent: ["./"]})
    before = task_store_snapshot(root)
    response = shell(root, host, f"rm -f {repos.policy.as_posix()}", "policy", bound=True)
    assert "POLICY_ADMINISTRATION" in str(response)
    for index, command in enumerate(("rm -f .task/x", "rm -f .git/config")):
        assert "NOT_OWNER" in str(shell(root, host, command, f"control-{index}", bound=True))
    assert task_store_snapshot(root) == before


@pytest.mark.parametrize("host", SHELL_HOSTS)
def test_unbound_shell_gates_only_recognized_protected_parts(
    checkout: Path, repos: Repos, host: str
) -> None:
    """Free unchecked work alone; deny it beside a protected deletion or redirection.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
        host: Native adapter.
    """
    (repos.c / "README.md").write_text("text")
    (checkout / "sub").mkdir()
    # Unchecked commands, discarded output and an unanchored operand add no protected effect.
    for index, command in enumerate([*UNCHECKED, "cd sub && rm -f rel.py"]):
        assert allowed(host, shell(checkout, host, command, f"free-{index}"))
    assert not (checkout / ".task").exists()
    # A recognized protected part is enforced whatever the rest of the command does.
    for index, command in enumerate(
        (
            f"python build.py && rm {repos.c.as_posix()}/README.md",
            "python gen.py > src/out.txt",
            f"python gen.py > {repos.c.as_posix()}/README.md",
        )
    ):
        assert not allowed(host, shell(checkout, host, command, f"protected-{index}"))


def test_cursor_relative_operand_resolves_from_the_execution_directory(
    checkout: Path, repos: Repos
) -> None:
    """Resolve a relative copy source from Cursor's working directory, not the chat's.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    (checkout / "sub").mkdir()
    (checkout / "sub/logo.png").write_bytes(PNG)
    (checkout / "logo.png").write_text("text")
    command = f"cp logo.png {repos.c.as_posix()}/src/logo.png"
    # The binary source beside the execution directory makes the copy free.
    sub = str(checkout / "sub")
    assert allowed("cursor", shell(checkout, "cursor", command, "binary", directory=sub))
    # The chat directory's text source of the same name makes it protected.
    assert not allowed("cursor", shell(checkout, "cursor", command, "text"))


def test_free_shell_preserves_pending_ticket_read(checkout: Path, repos: Repos) -> None:
    """Leave the task store and a pending ticket read untouched by free shell work.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    claude.handle(
        {
            "cwd": str(checkout),
            "session_id": "fresh",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Task: TEST-1",
        }
    )
    before = task_store_snapshot(checkout)
    assert any(name.endswith(".lookup-required.json") for name in before)
    for index, command in enumerate([*UNCHECKED, "cd src && rm -f rel.py"]):
        assert allowed("claude", shell(checkout, "claude", command, f"free-{index}"))
    assert task_store_snapshot(checkout) == before
    # The pending ticket read still fences protected shell work.
    response = shell(checkout, "claude", "rm -f src/a.py", "protected")
    assert "TICKET_READ_REQUIRED" in str(response)


def test_altered_lifecycle_command_stays_behind_the_ticket_read(checkout: Path) -> None:
    """Keep a composed lifecycle command gated while unchecked shell work stays free.

    Args:
        checkout: Unregistered session workspace.
    """
    claude.handle(
        {
            "cwd": str(checkout),
            "session_id": "fresh",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Task: TEST-1",
        }
    )
    request = {"operation": "read", "session_id": "fresh", "issue_id": "TEST-1"}
    command = common.bootstrap_command(request, "claude-code") + "; true"
    assert "TICKET_READ_REQUIRED" in str(shell(checkout, "claude", command, "lifecycle"))
    quoted = "python -m nplet.life''cycle.task_workspace"
    assert "TICKET_READ_REQUIRED" in str(shell(checkout, "claude", quoted, "quoted"))
    escaped = "python -m nplet.life\\cycle.task_workspace"
    bootstrap = "python -m nplet.adapters.bootstrap --request-base64 e30"
    assert "TICKET_READ_REQUIRED" in str(shell(checkout, "claude", bootstrap, "bootstrap"))
    hashed = "python --check-hash-based-pycs always -m nplet.lifecycle.task_workspace"
    assert "TICKET_READ_REQUIRED" in str(shell(checkout, "claude", hashed, "hashed"))
    assert "TICKET_READ_REQUIRED" in str(shell(checkout, "claude", escaped, "escaped"))
    assert allowed("claude", shell(checkout, "claude", "python build.py", "free"))


@pytest.mark.parametrize("ready", SHELL_HOSTS, indirect=True)
def test_free_shell_preserves_stale_readiness_and_pending_records(
    ready: tuple[str, Fixture], repos: Repos
) -> None:
    """Leave stale readiness and an unrelated pending record unchanged by free shell work.

    Args:
        ready: Adapter name and its ready coordinator fixture.
        repos: Other repositories and the vault policy.
    """
    host, case = ready
    root = case.root
    (repos.plain / "a.png").write_bytes(PNG)
    case.call("tool-start", tool_id="unrelated")
    # A scope change without a fresh read and acknowledgment leaves readiness stale.
    case.approve({root: ["./"]}, refresh=False)
    before = task_store_snapshot(root)
    for index, command in enumerate(
        [*UNCHECKED, f"cp {repos.plain.as_posix()}/a.png {repos.c.as_posix()}/src/b.png"]
    ):
        assert allowed(host, shell(root, host, command, f"free-{index}", bound=True))
    assert task_store_snapshot(root) == before
    assert not allowed(host, shell(root, host, "rm -f src/x.py", "protected", bound=True))
