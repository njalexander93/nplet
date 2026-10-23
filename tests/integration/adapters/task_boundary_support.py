"""Share real-checkout fixtures and native decision reading for task-boundary tests."""

import subprocess
from pathlib import Path

import pytest


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
