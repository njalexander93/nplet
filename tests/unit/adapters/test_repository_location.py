"""Verify read-only target location evidence with stubbed discovery boundaries."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

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

    def project_repository(path: Path) -> core.Repository | None:
        """Record one query and answer it from the table.

        Args:
            path: Queried directory.

        Returns:
            The table's repository, or None for an absent directory.
        """
        calls.append(path)
        return repositories.get(path)

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


def raiser(error: BaseException) -> Callable[..., object]:
    """Build a stub that raises one error.

    Args:
        error: Error to raise.

    Returns:
        A callable raising ``error`` for any arguments.
    """

    def stub(*_args: object, **_kwargs: object) -> object:
        """Raise the configured error.

        Args:
            _args: Ignored positional arguments.
            _kwargs: Ignored keyword arguments.

        Raises:
            BaseException: The configured error.
        """
        raise error

    return stub


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (core.WorkspaceError("HOST_UNSUPPORTED"), "HOST_UNSUPPORTED"),
        (core.WorkspaceError("REPOSITORY_MISMATCH"), "REPOSITORY_MISMATCH"),
        (core.WorkspaceError("REPOSITORY_ABSENT"), "REPOSITORY_MISMATCH"),
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
    monkeypatch.setattr(location.core, "project_repository", raiser(error))
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
    stat = os.stat
    # Fail only the identity read so path probing keeps working.
    monkeypatch.setattr(
        location.os,
        "stat",
        lambda path, **kw: (
            raiser(OSError("gone"))() if path == session.common else stat(path, **kw)
        ),
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
    expected = location.Identity(session, [info.st_dev, info.st_ino])
    assert expected.key() == (os.path.normpath(session.common), (info.st_dev, info.st_ino))
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
    monkeypatch.setattr(location.core, "project_repository", raiser(OSError("no git")))
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
    result = location.resolve(str(holder.root), "link/file.md", None, never_root, {})
    assert [(p.kind, p.path, p.repository and p.repository.repository) for p in result.points] == [
        ("lexical", link / "file.md", destination),
        ("link", link, holder),
        ("resolved", link / "file.md", destination),
    ]
    assert calls == [link, holder.root]
    assert result.workspace is False


def test_dangling_link_on_the_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A link to a missing target on the walk to an existing directory is dangling.

    Args:
        tmp_path: Directory holding the modeled dangling link.
        monkeypatch: Replaces Git discovery and link presence.
    """
    dangling = tmp_path / "gone"
    lexists = os.path.lexists
    fake_discovery(monkeypatch, {})
    monkeypatch.setattr(
        location.os.path, "lexists", lambda path: Path(path) == dangling or lexists(path)
    )
    result = location.resolve(str(tmp_path), "gone/file.md", None, never_root, {})
    assert {p.error for p in result.points} == {"DANGLING_LINK"}


def test_missing_filesystem_root_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A walk that reaches a missing filesystem root cannot be located.

    Args:
        tmp_path: Session working directory.
        monkeypatch: Replaces Git discovery and directory presence.
    """
    calls = fake_discovery(monkeypatch, {})
    monkeypatch.setattr(location.os.path, "isdir", lambda _path: False)
    result = location.resolve(str(tmp_path), "a.md", None, never_root, {})
    assert {p.error for p in result.points} == {"UNREADABLE"}
    assert calls == []


def test_unresolvable_spelling_is_an_unreadable_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link-resolution failure keeps the lexical evidence and fails the resolved point.

    Args:
        tmp_path: Session working directory.
        monkeypatch: Replaces Git discovery and link resolution.
    """
    fake_discovery(monkeypatch, {})
    monkeypatch.setattr(location.Path, "resolve", raiser(RuntimeError("loop")))
    result = location.resolve(str(tmp_path), "a.md", None, never_root, {})
    assert [(p.kind, p.path, p.error) for p in result.points] == [
        ("lexical", tmp_path / "a.md", None),
        ("resolved", tmp_path / "a.md", "UNREADABLE"),
    ]


def test_resolution_writes_nothing(tmp_path: Path) -> None:
    """Real discovery leaves the repository's files and Git status unchanged.

    Args:
        tmp_path: Parent of a disposable real repository.
    """
    # Snapshot every file and the Git status of a real repository.
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)

    def snapshot() -> tuple[dict[str, tuple[int, bytes]], str]:
        """Capture file contents, modification times and porcelain status.

        Returns:
            The file map and Git status text.
        """
        files = {
            str(path): (path.stat().st_mtime_ns, path.read_bytes())
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        status = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-C",
                str(checkout),
                "status",
                "--porcelain",
                "--ignored",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return files, status

    before = snapshot()
    session = core.project_repository(checkout)
    locations = boundary.edit_locations(edit(checkout, "new/a.md", "../outside.md"), session)
    assert [result.workspace for result in locations] == [True, False]
    assert snapshot() == before
