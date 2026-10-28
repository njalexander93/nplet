"""Resolve one edit target to read-only repository location evidence.

The resolver reports, for each discovery point of a target, the innermost Git
repository holding that point, proven absence or a bounded error code. It never
writes, never caches beyond the caller's per-call memo and never decides admission.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
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
    common_identity: tuple[int, int]

    def key(self) -> tuple[str, tuple[int, int]]:
        """Build the comparison key that identifies the repository.

        Returns:
            The normalized common Git directory and its device/inode pair.
        """
        return os.path.normpath(self.repository.common), self.common_identity


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


Memo = dict[str, tuple[Identity | None, str | None]]
# Probe errors that mean a path component does not exist.
MISSING = (errno.ENOENT, errno.ENOTDIR)


def discover(directory: Path, memo: Memo) -> tuple[Identity | None, str | None]:
    """Find the innermost repository holding one existing directory, at most once per call.

    Args:
        directory: Existing directory to query.
        memo: Invocation-local results keyed by the directory's real path.

    Returns:
        The repository identity and None, or None and a bounded error code; both are
        None when the directory is proven outside every repository.
    """
    # Query the real directory: a link holder's repository belongs to its own link point,
    # and alias spellings then share one query with one answer regardless of order.
    key = os.path.realpath(directory)
    if key not in memo:
        try:
            # Absence comes only from Git's proof; a repository also needs its identity.
            repository = core.project_repository(key)
            if repository is None:
                memo[key] = (None, None)
            else:
                info = os.stat(repository.common)
                memo[key] = (Identity(repository, (info.st_dev, info.st_ino)), None)
        # project_repository raises only REPOSITORY_MISMATCH or HOST_UNSUPPORTED.
        except core.WorkspaceError as error:
            memo[key] = (None, error.code)
        except subprocess.TimeoutExpired:
            memo[key] = (None, "GIT_TIMEOUT")
        # Unreadable paths and undecodable Git output cannot be located.
        except (OSError, ValueError):
            memo[key] = (None, "UNREADABLE")
    return memo[key]


def links(path: Path) -> list[Path]:
    """List every link entry on a path and, transitively, on each link's destination path.

    Args:
        path: Absolute spelling, possibly with ``..`` components.

    Returns:
        Symbolic links and Windows junctions in discovery order, each once.
    """
    found: list[Path] = []
    pending = [path]
    # The list grows while iterating; each new link adds its destination to scan.
    for spelling in pending:
        current = Path(spelling.anchor)
        for part in spelling.parts[1:]:
            # ``..`` leaves the real directory, as the filesystem does, so a link before
            # it is still visited instead of being collapsed away.
            if part == "..":
                current = Path(os.path.realpath(current)).parent
                continue
            current /= part
            if current not in found and (os.path.islink(current) or os.path.isjunction(current)):
                found.append(current)
                # A relative target is read from the link's real directory, not the
                # spelling that reached it. An unreadable link cannot be followed further.
                with contextlib.suppress(OSError):
                    pending.append(Path(os.path.realpath(current.parent)) / os.readlink(current))
    return found


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
    # Climb only past genuinely missing components; any other probe failure is
    # unreadable, and an entry that exists only as a link is dangling.
    directory = start
    while True:
        try:
            if stat.S_ISDIR(os.stat(directory).st_mode):
                break
        except OSError as target:
            if target.errno not in (*MISSING, errno.ELOOP):
                return Point(kind, path, None, "UNREADABLE")
            try:
                os.lstat(directory)
            except OSError as entry:
                if entry.errno not in MISSING:
                    return Point(kind, path, None, "UNREADABLE")
            else:
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
    # Build the lexical spelling and one link point per link reached from it; the link
    # entry lives in its parent directory, which Git would not see through the link.
    absolute = Path(cwd, target)
    lexical = Path(os.path.normpath(absolute))
    points = [point("lexical", lexical, lexical, memo)]
    # Links come from the unnormalized spelling: ``..`` after a link leaves its destination.
    holders = links(absolute)
    spellings = [lexical, *holders]
    points += [point("link", link, link.parent, memo) for link in holders]
    # Resolve links; an unresolvable spelling is a failed point, not absence.
    try:
        resolved = absolute.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        points.append(Point("resolved", absolute, None, "UNREADABLE"))
    else:
        points.append(point("resolved", resolved, resolved, memo))
        spellings.append(resolved)
    # Membership holds by a root on any spelling, link holders included, or by any point
    # in the session family, so a failed query never hides a known root.
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
