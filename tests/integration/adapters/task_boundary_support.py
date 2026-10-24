"""Share native decision reading and linked-worktree setup for task-boundary tests."""

import subprocess
from pathlib import Path


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
