"""Verify read-only target location evidence with stubbed discovery boundaries."""

import errno
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest

from nplet.adapters import repository_location as location
from nplet.adapters import task_boundary as boundary
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def fake_discovery(
    monkeypatch: pytest.MonkeyPatch, repositories: dict[Path, core.Repository]
) -> list[Path]:
    """Replace Git discovery with a directory table and record every query.

    Args:
        monkeypatch: Installs the fake discovery for the case.
        repositories: Repository returned for each directory; others are absent.

    Returns:
        The directories queried, in order.
    """
    calls: list[Path] = []

    def project_repository(path: str) -> core.Repository | None:
        """Record one query and answer it from the table.

        Args:
            path: Queried real directory.

        Returns:
            The table's repository, or None for an absent directory.
        """
        calls.append(Path(path))
        return repositories.get(Path(path))

    monkeypatch.setattr(location.core, "project_repository", project_repository)
    return calls


def repo(root: Path) -> core.Repository:
    """Model a repository whose common directory is its real ``.git`` directory.

    Args:
        root: Existing worktree root.

    Returns:
        The repository with ``root`` as its only worktree.
    """
    (root / ".git").mkdir(parents=True, exist_ok=True)
    return core.Repository(root, root / ".git", [root])


def edit(cwd: Path, *targets: str) -> core.JSONObject:
    """Build a patch event that edits every target.

    Args:
        cwd: Session working directory.
        targets: Patch targets.

    Returns:
        The native edit event.
    """
    body = "\n".join(f"*** Update File: {target}" for target in targets)
    command = f"*** Begin Patch\n{body}\n*** End Patch"
    return {"cwd": str(cwd), "tool_name": "apply_patch", "tool_input": {"command": command}}


def never_root(_path: Path) -> bool:
    """Reject every candidate root.

    Args:
        _path: Ignored candidate.

    Returns:
        Always False.
    """
    return False


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (core.WorkspaceError("HOST_UNSUPPORTED"), "HOST_UNSUPPORTED"),
        (core.WorkspaceError("REPOSITORY_MISMATCH"), "REPOSITORY_MISMATCH"),
        (subprocess.TimeoutExpired(["git"], 2), "GIT_TIMEOUT"),
        (PermissionError("denied"), "UNREADABLE"),
    ],
)
def test_discovery_failures_map_to_bounded_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException, code: str
) -> None:
    """Every discovery failure is a bounded code on each point, never absence.

    Args:
        tmp_path: Existing directory holding the target.
        monkeypatch: Replaces Git discovery.
        error: Failure raised by discovery.
        code: Expected point error.
    """
    monkeypatch.setattr(location.core, "project_repository", Mock(side_effect=error))
    result = location.resolve(str(tmp_path), "file.md", None, never_root, {})
    assert [(p.kind, p.repository, p.error) for p in result.points] == [
        ("lexical", None, code),
        ("resolved", None, code),
    ]
    assert result.state == "unresolved"


def test_common_directory_stat_failure_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository whose common directory cannot be stat'ed is unreadable.

    Args:
        tmp_path: Existing directory holding the target.
        monkeypatch: Replaces Git discovery and the identity read.
    """
    session = repo(tmp_path)
    fake_discovery(monkeypatch, {tmp_path: session})
    stat, gone = os.stat, Mock(side_effect=OSError("gone"))
    # Fail only the identity read so path probing keeps working.
    monkeypatch.setattr(
        location.os,
        "stat",
        lambda path, **kw: gone() if path == session.common else stat(path, **kw),
    )
    result = location.resolve(str(tmp_path), "file.md", None, never_root, {})
    assert {p.error for p in result.points} == {"UNREADABLE"}


def test_identity_and_memo_cover_a_whole_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One call queries each directory once and records the common identity per point.

    Args:
        tmp_path: Repository root holding the targets.
        monkeypatch: Replaces Git discovery.
    """
    session = repo(tmp_path)
    calls = fake_discovery(monkeypatch, {tmp_path: session})
    locations = boundary.edit_locations(edit(tmp_path, "a.md", "new/b.md"), session)
    # Both targets and both spellings share the one existing directory.
    assert calls == [tmp_path]
    info = os.stat(session.common)
    expected = location.Identity(session, (info.st_dev, info.st_ino))
    for result, target in zip(locations, ("a.md", "new/b.md"), strict=True):
        assert result.target == target
        assert result.workspace is True
        assert result.state == "resolved"
        assert [p.repository for p in result.points] == [expected, expected]
        assert result.points[0].path == tmp_path / target
    # A second call starts a fresh memo.
    boundary.edit_locations(edit(tmp_path, "a.md"), session)
    assert calls == [tmp_path, tmp_path]


def test_absent_target_outside_every_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target Git proves outside every repository is absent and outside the workspace.

    Args:
        tmp_path: Plain directory holding the target.
        monkeypatch: Replaces Git discovery.
    """
    fake_discovery(monkeypatch, {})
    [result] = boundary.edit_locations(edit(tmp_path, "a.md"), None)
    assert result.workspace is False
    assert result.state == "absent"
    assert [(p.kind, p.repository, p.error) for p in result.points] == [
        ("lexical", None, None),
        ("resolved", None, None),
    ]


def test_unknown_edit_shape_has_no_locations() -> None:
    """An edit whose targets are unknown yields no evidence."""
    event = {"cwd": "/", "tool_name": "Write", "tool_input": {"file_path": 7}}
    assert boundary.edit_locations(event, None) == []


@pytest.mark.parametrize("target", [7, None, "", "a\x00b"])
def test_invalid_targets_are_unresolved_without_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: object
) -> None:
    """A malformed target keeps its value and reports ``INVALID_TARGET``.

    Args:
        tmp_path: Session working directory.
        monkeypatch: Replaces Git discovery.
        target: Malformed target.
    """
    calls = fake_discovery(monkeypatch, {})
    result = location.resolve(str(tmp_path), target, None, never_root, {})
    assert result == location.Location(
        target, False, [location.Point("lexical", tmp_path, None, "INVALID_TARGET")]
    )
    assert result.state == "unresolved"
    assert calls == []


def test_workspace_by_root_on_an_ancestor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A root on the target's ancestor chain makes it a workspace target without Git.

    Args:
        tmp_path: Session worktree root.
        monkeypatch: Replaces Git discovery with failures.
    """
    monkeypatch.setattr(location.core, "project_repository", Mock(side_effect=OSError("no git")))
    session = core.Repository(tmp_path, tmp_path / "elsewhere", [tmp_path])
    [result] = boundary.edit_locations(edit(tmp_path, "deep/a.md"), session)
    assert result.workspace is True
    assert result.state == "unresolved"


def test_workspace_by_session_common_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A point in the session's repository family is a workspace target off the roots.

    Args:
        tmp_path: Parent of the session and target repositories.
        monkeypatch: Replaces Git discovery.
    """
    target = tmp_path / "below"
    target.mkdir()
    family = repo(tmp_path / "main")
    member = core.Repository(target, family.common, [family.root, target])
    fake_discovery(monkeypatch, {target: member})
    result = location.resolve(str(target), "a.md", family, never_root, {})
    assert result.workspace is True
    # Another repository's common directory is not the session family.
    other = repo(tmp_path / "other")
    assert location.resolve(str(target), "a.md", other, never_root, {}).workspace is False


def test_link_points_query_each_link_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every symlink component adds a link point discovered at its parent directory.

    Args:
        tmp_path: Parent of the link-holding and destination repositories.
        monkeypatch: Replaces Git discovery and link detection.
    """
    # Model repo-C/link -> repo-V/notes without platform symlink support.
    holder, destination = repo(tmp_path / "repo-C"), repo(tmp_path / "repo-V")
    link = holder.root / "link"
    link.mkdir()
    calls = fake_discovery(monkeypatch, {holder.root: holder, link: destination})
    monkeypatch.setattr(location.os.path, "islink", lambda path: Path(path) == link)
    # A link that leads back to itself is followed once and reported once.
    monkeypatch.setattr(location.os, "readlink", lambda _path: str(link))
    result = location.resolve(str(holder.root), "link/file.md", None, never_root, {})
    assert [(p.kind, p.path, p.repository and p.repository.repository) for p in result.points] == [
        ("lexical", link / "file.md", destination),
        ("link", link, holder),
        ("resolved", link / "file.md", destination),
    ]
    assert calls == [link, holder.root]
    assert result.workspace is False


def test_unreadable_link_is_recorded_but_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link whose target cannot be read still gets its point; the walk continues.

    Args:
        tmp_path: Directory holding the modeled link.
        monkeypatch: Replaces Git discovery, link detection and link reading.
    """
    link = tmp_path / "link"
    link.mkdir()
    fake_discovery(monkeypatch, {})
    monkeypatch.setattr(location.os.path, "islink", lambda path: Path(path) == link)
    monkeypatch.setattr(location.os, "readlink", Mock(side_effect=PermissionError("denied")))
    result = location.resolve(str(tmp_path), "link/a.md", None, never_root, {})
    assert [(p.kind, p.path) for p in result.points] == [
        ("lexical", link / "a.md"),
        ("link", link),
        ("resolved", link / "a.md"),
    ]


def test_excessive_link_chain_is_bounded_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An endless chain of distinct links stops at the limit instead of exhausting the stack.

    Args:
        tmp_path: Session working directory holding the modeled chain.
        monkeypatch: Replaces Git discovery, link detection and link reading.
    """
    # Each ``l...`` entry is a link to a sibling one letter longer, without end.
    fake_discovery(monkeypatch, {})
    monkeypatch.setattr(
        location.os.path,
        "islink",
        lambda path: Path(path).parent == tmp_path and Path(path).name.startswith("l"),
    )
    monkeypatch.setattr(location.os, "readlink", lambda path: Path(path).name + "l")
    session = core.Repository(tmp_path, tmp_path / ".git", [tmp_path])
    [result] = boundary.edit_locations(edit(tmp_path, "l/a.md"), session)
    assert [p.path.name for p in result.points if p.kind == "link"] == [
        "l" * n for n in range(1, location.LINK_LIMIT + 1)
    ]
    assert (result.points[-1].kind, result.points[-1].error) == ("resolved", "UNREADABLE")
    assert result.state == "unresolved"


def test_failed_middle_hop_keeps_root_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session root holding only a middle link keeps membership when its query fails.

    Args:
        tmp_path: Parent of the modeled A/start -> D/hop -> V/notes chain.
        monkeypatch: Replaces Git discovery and link detection.
    """
    # Real paths, so the hop reached after ``..`` compares equal to the modeled link.
    base = tmp_path.resolve()
    start, hop, notes = base / "A/start", base / "D/hop", base / "V/notes"
    for directory in (start, hop, notes):
        directory.mkdir(parents=True)
    # The existing target file climbs to its directory; ``..`` is walked, not collapsed.
    (start / "f.md").touch()
    destinations = {start: base / "D/../D/hop", hop: notes}
    monkeypatch.setattr(location.os.path, "islink", lambda path: Path(path) in destinations)
    monkeypatch.setattr(location.os, "readlink", lambda path: str(destinations[Path(path)]))

    def project_repository(path: str) -> None:
        """Time out at the middle hop's directory and find nothing elsewhere.

        Args:
            path: Queried real directory.

        Raises:
            subprocess.TimeoutExpired: For the hop's parent.
        """
        if Path(path) == hop.parent:
            raise subprocess.TimeoutExpired(["git"], 2)

    monkeypatch.setattr(location.core, "project_repository", project_repository)
    result = location.resolve(str(base), "A/start/f.md", None, lambda p: p == hop.parent, {})
    assert [(p.kind, p.path, p.error) for p in result.points if p.kind == "link"] == [
        ("link", start, None),
        ("link", hop, "GIT_TIMEOUT"),
    ]
    assert result.workspace is True


def fail_probe(monkeypatch: pytest.MonkeyPatch, name: str, path: Path, code: int | None) -> None:
    """Make one ``os`` probe of one path fail, or succeed when ``code`` is None.

    Args:
        monkeypatch: Installs the probe for the case.
        name: ``stat`` or ``lstat``.
        path: The probed path; other paths keep the real probe.
        code: Raised errno; None answers with the parent's real metadata.
    """
    real = getattr(os, name)

    def probe(candidate: object, *args: object, **kwargs: object) -> os.stat_result:
        """Answer the modeled path and delegate every other probe.

        Args:
            candidate: Probed path or descriptor.
            args: Positional probe arguments.
            kwargs: Keyword probe arguments.

        Returns:
            The probe result.

        Raises:
            OSError: The modeled failure for ``path``.
        """
        if candidate != path:
            return real(candidate, *args, **kwargs)
        if code is None:
            return real(path.parent)
        raise OSError(code, os.strerror(code), str(path))

    monkeypatch.setattr(location.os, name, probe)


@pytest.mark.parametrize(
    ("stat_code", "lstat_code", "error"),
    [
        (errno.ENOENT, None, "DANGLING_LINK"),
        (errno.ELOOP, None, "DANGLING_LINK"),
        (errno.ENAMETOOLONG, errno.ENAMETOOLONG, "UNREADABLE"),
        (errno.ENOENT, errno.EACCES, "UNREADABLE"),
    ],
)
def test_probe_failures_on_the_walk_are_not_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stat_code: int,
    lstat_code: int | None,
    error: str,
) -> None:
    """A link-only entry is dangling and any other probe failure is unreadable, never absence.

    Args:
        tmp_path: Existing non-Git parent of the failing component.
        monkeypatch: Replaces Git discovery and the component's probes.
        stat_code: Errno of following the component.
        lstat_code: Errno of reading the entry itself; None when it exists.
        error: Expected code.
    """
    component = tmp_path / "gone"
    calls = fake_discovery(monkeypatch, {})
    fail_probe(monkeypatch, "stat", component, stat_code)
    fail_probe(monkeypatch, "lstat", component, lstat_code)
    result = location.resolve(str(tmp_path), "gone/file.md", None, never_root, {})
    assert {p.error for p in result.points} == {error}
    assert calls == []


def test_missing_filesystem_root_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A walk that reaches a missing filesystem root cannot be located.

    Args:
        tmp_path: Session working directory.
        monkeypatch: Replaces Git discovery and every probe on the target's path.
    """
    calls = fake_discovery(monkeypatch, {})
    target = tmp_path / "a.md"
    for path in (target, *target.parents):
        fail_probe(monkeypatch, "stat", path, errno.ENOENT)
        fail_probe(monkeypatch, "lstat", path, errno.ENOENT)
    result = location.resolve(str(tmp_path), "a.md", None, never_root, {})
    assert {p.error for p in result.points} == {"UNREADABLE"}
    assert calls == []


@pytest.mark.parametrize(
    ("code", "error"),
    [(errno.ENOENT, "DANGLING_LINK"), (errno.EACCES, "UNREADABLE"), (None, None)],
)
def test_link_before_parent_step_must_be_followable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int | None, error: str | None
) -> None:
    """``..`` after a link fails the resolved point unless the link's destination exists.

    Args:
        tmp_path: Directory holding the modeled link.
        monkeypatch: Replaces Git discovery, link detection and the link's probe.
        code: Errno of following the link; None when it is followable.
        error: Expected resolved-point code.
    """
    link = tmp_path / "link"
    link.mkdir()
    fake_discovery(monkeypatch, {})
    monkeypatch.setattr(location.os.path, "islink", lambda path: Path(path) == link)
    monkeypatch.setattr(location.os, "readlink", lambda _path: str(tmp_path / "elsewhere"))
    fail_probe(monkeypatch, "stat", link, code)
    result = location.resolve(str(tmp_path), "link/../a.md", None, never_root, {})
    assert [(p.kind, p.error) for p in result.points] == [
        ("lexical", None),
        ("link", None),
        ("resolved", error),
    ]


@pytest.mark.parametrize(
    ("code", "error", "state"),
    [(errno.ENAMETOOLONG, "UNREADABLE", "unresolved"), (errno.ENOENT, None, "absent")],
)
def test_component_before_parent_step_keeps_probe_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, error: str | None, state: str
) -> None:
    """``..`` cannot erase a component the filesystem fails to pass, only a missing one.

    Args:
        tmp_path: Readable non-Git directory holding the component.
        monkeypatch: Replaces Git discovery and the component's probe.
        code: Errno of passing the component.
        error: Expected resolved-point code.
        state: Expected target state.
    """
    fake_discovery(monkeypatch, {})
    fail_probe(monkeypatch, "stat", tmp_path / ("x" * 300), code)
    result = location.resolve(str(tmp_path), "x" * 300 + "/../f.md", None, never_root, {})
    assert (result.points[-1].error, result.state) == (error, state)


@pytest.mark.parametrize("target", ["\ud800.md", "\ud800/../a.md"])
def test_unencodable_target_is_evidence_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """A target the filesystem cannot encode yields bounded evidence through the boundary.

    Args:
        tmp_path: Session working directory outside every repository.
        monkeypatch: Rejects the lone surrogate on every platform.
        target: Unencodable spelling, alone or walked through ``..``.
    """
    # POSIX encodes paths as UTF-8 and rejects a lone surrogate; Windows paths are
    # UTF-16 and accept it, so model the POSIX encoder there too.
    for owner, name in [(os, "stat"), (os, "lstat"), (os.path, "realpath")]:
        real = getattr(owner, name)

        def probe(path: object, *args: object, real: Callable = real, **kwargs: object) -> object:
            """Reject a lone surrogate as the POSIX encoder does and delegate otherwise.

            Args:
                path: Probed path or descriptor.
                args: Positional probe arguments.
                real: The real probe.
                kwargs: Keyword probe arguments.

            Returns:
                The real probe's result.

            Raises:
                UnicodeEncodeError: For a spelling holding the lone surrogate.
            """
            if "\ud800" in str(path):
                raise UnicodeEncodeError("utf-8", str(path), 0, 1, "surrogates not allowed")
            return real(path, *args, **kwargs)

        monkeypatch.setattr(owner, name, probe)
    session = core.Repository(tmp_path, tmp_path / ".git", [tmp_path])
    [result] = boundary.edit_locations(edit(tmp_path, target), session)
    assert result.state == "unresolved"
    assert result.workspace is True


def test_unresolvable_spelling_is_an_unreadable_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link-resolution failure keeps the lexical evidence and fails the resolved point.

    Args:
        tmp_path: Session working directory.
        monkeypatch: Replaces Git discovery and link resolution.
    """
    fake_discovery(monkeypatch, {})
    monkeypatch.setattr(location.Path, "resolve", Mock(side_effect=RuntimeError("loop")))
    result = location.resolve(str(tmp_path), "a.md", None, never_root, {})
    assert [(p.kind, p.path, p.error) for p in result.points] == [
        ("lexical", tmp_path / "a.md", None),
        ("resolved", tmp_path / "a.md", "UNREADABLE"),
    ]
