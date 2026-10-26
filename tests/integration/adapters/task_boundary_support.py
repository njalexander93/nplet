"""Share native decision reading and linked-worktree setup for task-boundary tests."""

import subprocess
from pathlib import Path

from nplet.adapters import claude, codex, cursor
from nplet.lifecycle import task_workspace as core


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


def pre_decision(host: str, observed: dict) -> dict:
    """Run one prehook through the selected adapter and return its native decision.

    Args:
        host: Selected adapter.
        observed: Native prehook event.

    Returns:
        The adapter response; for Codex, a raised lifecycle error becomes its native denial.

    Raises:
        core.WorkspaceError: If a Claude or Cursor adapter raises instead of answering.
    """
    adapter = {"codex": codex, "claude": claude, "cursor": cursor}[host]
    # Only the Codex adapter reports lifecycle failures by raising.
    try:
        return adapter.handle(observed)
    except core.WorkspaceError as error:
        if host != "codex":
            raise
        return codex.failure("PreToolUse", error.code)


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


def task_store_snapshot(root: Path) -> dict[str, bytes]:
    """Read every task-store file so a test can prove a call wrote nothing.

    Args:
        root: Checkout whose ``.task`` directory is captured.

    Returns:
        Checkout-relative file paths mapped to their exact bytes.
    """
    return {
        str(p.relative_to(root)): p.read_bytes() for p in (root / ".task").rglob("*") if p.is_file()
    }
