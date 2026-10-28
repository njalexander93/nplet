"""Resolve one edit target to read-only repository location evidence.

The resolver reports, for each discovery point of a target, the innermost Git
repository holding that point, proven absence or a bounded error code. It never
writes, never caches beyond the caller's per-call memo and never decides admission.
"""

from __future__ import annotations

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
# Probe errors that mean a path component does not exist; a file in the way is
# then found on the climb.
MISSING = (errno.ENOENT, errno.ENOTDIR)
# Most links one target records; Linux follows at most 40 and macOS 32 per lookup.
LINK_LIMIT = 64


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


def links(path: Path) -> tuple[list[Path], str | None]:
    """List every link entry on a path and, transitively, on each link's destination path.

    Args:
        path: Absolute spelling, possibly with ``..`` components.

    Returns:
        Symbolic links and Windows junctions in traversal order, each once, at most
        ``LINK_LIMIT``, and the bounded code of the first failure: a link that ``..``
        cannot leave, or more links than any traversal can follow.
    """
    found: list[Path] = []
    failure = None
    # Depth first with an explicit stack: each frame is a spelling's position and the
    # rest of its components, and a destination is walked before later components.
    stack = [(Path(path.anchor), iter(path.parts[1:]))]
    while stack:
        current, parts = stack[-1]
        part = next(parts, None)
        if part is None:
            stack.pop()
            continue
        # ``..`` leaves the real directory, as the filesystem does, so a link before it is
        # still visited instead of being collapsed away; the filesystem fails at a dangling
        # link, a file or an unreadable component on the way before ``..`` can collapse it.
        if part == "..":
            failure = failure or nearest(current, leaf=False)[1]
            stack[-1] = (Path(os.path.realpath(current)).parent, parts)
            continue
        current /= part
        stack[-1] = (current, parts)
        if current in found or not (os.path.islink(current) or os.path.isjunction(current)):
            continue
        # Hosts stop following links well below this many, so stop recording here.
        if len(found) == LINK_LIMIT:
            return found, failure or "UNREADABLE"
        found.append(current)
        # A relative target is read from the link's real directory, not the spelling
        # that reached it. An unreadable link cannot be followed further.
        try:
            destination = Path(os.path.realpath(current.parent)) / os.readlink(current)
        except OSError:
            continue
        stack.append((Path(destination.anchor), iter(destination.parts[1:])))
    return found, failure


def nearest(start: Path, leaf: bool = True) -> tuple[Path | None, str | None]:
    """Find the nearest existing directory at or above ``start``, or why it is unreachable.

    Args:
        start: First path to try.
        leaf: Whether ``start`` itself may be an existing non-directory, such as an edited
            file; any other non-directory on the way blocks traversal.

    Returns:
        The directory and None, or None and ``DANGLING_LINK`` or ``UNREADABLE``.
    """
    # Climb only past genuinely missing components; any other probe failure is
    # unreadable, and an entry that exists only as a link is dangling.
    directory = start
    while True:
        try:
            info = os.stat(directory)
        # An unencodable spelling cannot be probed.
        except ValueError:
            return None, "UNREADABLE"
        except OSError as target:
            if target.errno not in (*MISSING, errno.ELOOP):
                return None, "UNREADABLE"
            try:
                os.lstat(directory)
            except OSError as entry:
                if entry.errno not in MISSING:
                    return None, "UNREADABLE"
            else:
                return None, "DANGLING_LINK"
        else:
            if stat.S_ISDIR(info.st_mode):
                return directory, None
            # A file below which the path continues cannot be traversed or created through.
            if not leaf or directory != start:
                return None, "UNREADABLE"
        # A missing filesystem root has no ancestor left to query.
        if directory.parent == directory:
            return None, "UNREADABLE"
        directory = directory.parent


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
    directory, error = nearest(start)
    if directory is None:
        return Point(kind, path, None, error)
    # Query the directory and attach its result to this spelling.
    repository, error = discover(directory, memo)
    return Point(kind, path, repository, error)


def held(is_root: Callable[[Path], bool], path: Path) -> bool:
    """Apply the root predicate, treating an unencodable path as no root.

    Args:
        is_root: Worktree root predicate.
        path: Candidate path.

    Returns:
        The predicate's answer; False when the path cannot be encoded for a probe.
    """
    # Root paths are matched before any probe, so only an alias spelling is lost here.
    try:
        return is_root(path)
    except ValueError:
        return False


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
    try:
        holders, failure = links(absolute)
    except ValueError:
        holders, failure = [], "UNREADABLE"
    # A holder reached through an earlier link also counts in its real directory.
    spellings = [lexical, *holders]
    spellings += [Path(os.path.realpath(link.parent)) / link.name for link in holders]
    points += [point("link", link, link.parent, memo) for link in holders]
    # Resolve links; an unresolvable spelling is a failed point, not absence.
    if failure is None:
        try:
            resolved = absolute.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            failure = "UNREADABLE"
    if failure is None:
        points.append(point("resolved", resolved, resolved, memo))
        spellings.append(resolved)
    else:
        points.append(Point("resolved", absolute, None, failure))
    # Membership holds by a root on any spelling, link holders included, or by any point
    # in the session family, so a failed query never hides a known root.
    workspace = any(
        held(is_root, ancestor)
        for spelling in spellings
        for ancestor in (spelling, *spelling.parents)
    ) or (
        session is not None
        and any(
            p.repository is not None and p.repository.key()[0] == os.path.normpath(session.common)
            for p in points
        )
    )
    return Location(target, workspace, points)
