"""Resolve one edit target to read-only repository location evidence.

The resolver reports, for each discovery point of a target, the innermost Git
repository holding that point, proven absence or a bounded error code. It never
writes, never caches beyond the caller's per-call memo and never decides admission.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from nplet.lifecycle import task_workspace as core


class Identity(NamedTuple):
    """The identity of one discovered repository.

    Attributes:
        repository: Worktree root, common Git directory and worktree roots.
        common_identity: Device and inode of the common Git directory, following links.
    """

    repository: core.Repository
    common_identity: list[int]

    def key(self) -> tuple[str, tuple[int, int]]:
        """Build the comparison key that identifies the repository.

        Returns:
            The normalized common Git directory and its device/inode pair.
        """
        return (
            os.path.normpath(self.repository.common),
            (self.common_identity[0], self.common_identity[1]),
        )


class Point(NamedTuple):
    """Discovery evidence for one spelling of a target.

    Attributes:
        kind: ``lexical``, ``link`` or ``resolved``.
        path: The spelling; for ``link`` the symlink entry itself.
        repository: Innermost repository holding ``path``; None when absent or failed.
        error: Bounded failure code when discovery for this point failed.
    """

    kind: str
    path: Path
    repository: Identity | None
    error: str | None


class Location(NamedTuple):
    """Location evidence for one supplied edit target.

    Attributes:
        target: The supplied target, unchanged.
        workspace: Whether the target belongs to the session's repository family.
        points: One record per discovery point in path order, never deduplicated.
    """

    target: object
    workspace: bool
    points: list[Point]

    @property
    def state(self) -> str:
        """Derive the target's overall discovery state from its points.

        Returns:
            ``unresolved`` when any point failed, ``resolved`` when any point has a
            repository, and ``absent`` otherwise.
        """
        # A single failure makes the whole target unproven.
        if any(point.error for point in self.points):
            return "unresolved"
        return "resolved" if any(point.repository for point in self.points) else "absent"


Memo = dict[Path, Identity | str | None]


def discover(directory: Path, memo: Memo) -> tuple[Identity | None, str | None]:
    """Find the innermost repository holding one existing directory, at most once per call.

    Args:
        directory: Existing directory to query.
        memo: Invocation-local results keyed by directory.

    Returns:
        The repository identity and None, or None and a bounded error code; both are
        None when the directory is proven outside every repository.
    """
    # Query Git and stat the common directory only for a directory not seen in this call.
    if directory not in memo:
        result: Identity | str | None
        try:
            repository = core.project_repository(directory)
            if repository is None:
                result = None
            else:
                info = os.stat(repository.common)
                result = Identity(repository, [info.st_dev, info.st_ino])
        # Map every discovery failure to its finite code instead of absence.
        except core.WorkspaceError as error:
            result = (
                "HOST_UNSUPPORTED" if error.code == "HOST_UNSUPPORTED" else "REPOSITORY_MISMATCH"
            )
        except subprocess.TimeoutExpired:
            result = "GIT_TIMEOUT"
        except OSError:
            result = "UNREADABLE"
        memo[directory] = result
    # Split the cached result into its identity or error.
    cached = memo[directory]
    return (None, cached) if isinstance(cached, str) else (cached, None)


def point(kind: str, path: Path, start: Path, memo: Memo) -> Point:
    """Record one discovery point from the nearest existing directory at or above ``start``.

    Args:
        kind: The point kind.
        path: The spelling the point reports.
        start: First path to try as the queried directory.
        memo: Invocation-local discovery results.

    Returns:
        The point with its repository, absence or error.
    """
    # Walk up to an existing directory; a dangling link on the way cannot be located.
    directory = start
    while not os.path.isdir(directory):
        if os.path.lexists(directory) and not os.path.exists(directory):
            return Point(kind, path, None, "DANGLING_LINK")
        # A missing filesystem root has no ancestor left to query.
        if directory.parent == directory:
            return Point(kind, path, None, "UNREADABLE")
        directory = directory.parent
    # Query the directory and attach its result to this spelling.
    repository, error = discover(directory, memo)
    return Point(kind, path, repository, error)


def resolve(
    cwd: str,
    target: object,
    session: core.Repository | None,
    is_root: Callable[[Path], bool],
    memo: Memo,
) -> Location:
    """Resolve one edit target to its workspace membership and per-point repositories.

    Args:
        cwd: Native session working directory that relative targets are joined to.
        target: Native target path.
        session: The session's carried repository; None outside every repository.
        is_root: Worktree root predicate from ``task_boundary.root_test``.
        memo: Invocation-local discovery results shared by every target of one call.

    Returns:
        The target's location evidence. An invalid target yields one ``lexical`` point
        at ``cwd`` with ``INVALID_TARGET``.
    """
    # A malformed target has no spelling to locate.
    if not isinstance(target, str) or not target or "\x00" in target:
        return Location(target, False, [Point("lexical", Path(cwd), None, "INVALID_TARGET")])
    # Build the lexical spelling and one link point per symlink component on it.
    absolute = Path(cwd, target)
    lexical = Path(os.path.normpath(absolute))
    points = [point("lexical", lexical, lexical, memo)]
    spellings = [lexical]
    for component in (*reversed(lexical.parents[:-1]), lexical):
        # The link entry lives in its parent directory, which Git would not see through it.
        if os.path.islink(component):
            points.append(point("link", component, component.parent, memo))
    # Resolve links; an unresolvable spelling is a failed point, not absence.
    try:
        resolved = absolute.resolve(strict=False)
    except (OSError, RuntimeError):
        points.append(Point("resolved", absolute, None, "UNREADABLE"))
    else:
        points.append(point("resolved", resolved, resolved, memo))
        spellings.append(resolved)
    # Membership holds by a root on either spelling or by any point in the session family.
    workspace = any(
        is_root(ancestor) for spelling in spellings for ancestor in (spelling, *spelling.parents)
    ) or (
        session is not None
        and any(
            p.repository is not None and p.repository.key()[0] == os.path.normpath(session.common)
            for p in points
        )
    )
    return Location(target, workspace, points)
