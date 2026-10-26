"""Carry each session's project repository on its native event.

Invariant: every adapter carries the session repository on the event once, from the
validated working directory and before any shared helper reads it. Readers decode the
carried field and never rediscover the repository, so one discovery serves the event.
"""

from __future__ import annotations

from pathlib import Path

from nplet.lifecycle import task_workspace as core


def project_root(path: str) -> Path | None:
    """Select the worktree root of the project repository containing a directory.

    Args:
        path: Native working directory.

    Returns:
        The worktree root, or None when the directory is outside every repository.

    Raises:
        core.WorkspaceError: If repository discovery fails rather than proving absence.
        OSError: If Git cannot be launched.
    """
    return None if (repository := core.project_repository(path)) is None else repository.root


def carry_repository(event: core.JSONObject) -> core.JSONObject:
    """Copy the event with the session repository discovered from ``cwd``.

    Overwriting the field also neutralizes any value the raw host payload supplied.
    The argument is never mutated.

    Args:
        event: Native event whose ``cwd`` is a string.

    Returns:
        A new event with ``repository`` set to the encoded repository, or None
        outside every repository.

    Raises:
        core.WorkspaceError: If repository discovery fails rather than proving absence.
        OSError: If Git cannot be launched.
    """
    # Discover once and overwrite any payload-supplied value with the JSON-safe encoding.
    repository = core.project_repository(event["cwd"])
    return {**event, "repository": None if repository is None else repository.to_json()}


def session_repository(event: core.JSONObject) -> core.Repository | None:
    """Decode this session's repository recorded by ``carry_repository``.

    Args:
        event: Native event carrying ``repository``.

    Returns:
        The session repository, or None when the session lies outside every repository.

    Raises:
        core.WorkspaceError: ``INVALID_REQUEST`` if the event carries no repository or a
            malformed one.
    """
    # Every adapter carries the repository before use; never rediscover it here.
    core.require("repository" in event, "INVALID_REQUEST")
    carried = event["repository"]
    return None if carried is None else core.Repository.from_json(carried)


def session_project_root(event: core.JSONObject) -> Path | None:
    """Select the worktree root of this session's carried project repository.

    Args:
        event: Native event carrying ``repository``.

    Returns:
        The worktree root, or None when the session lies outside every repository.

    Raises:
        core.WorkspaceError: If the carried repository is missing or malformed.
    """
    return None if (repository := session_repository(event)) is None else repository.root


def session_root(event: core.JSONObject) -> Path:
    """Select the worktree root of this session's project repository.

    Args:
        event: Native event carrying ``repository``.

    Returns:
        The session's worktree root.

    Raises:
        core.WorkspaceError: ``REPOSITORY_ABSENT`` when the session lies outside every
            repository, or a carrier failure from ``session_repository``.
    """
    # A session outside every repository has no root to open.
    if (root := session_project_root(event)) is None:
        raise core.WorkspaceError("REPOSITORY_ABSENT")
    return root
