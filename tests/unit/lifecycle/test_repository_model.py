"""Check Git identity and store binding decisions with bounded doubles."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nplet.lifecycle import task_workspace as core
from tests.platform_support import link_directory

pytestmark = pytest.mark.unit


def test_git_uses_argument_vector_and_bounded_noninteractive_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep the repository query out of shell interpretation.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Record collaborator calls for the assertion.
    observed: list[tuple[list[str], dict[str, object]]] = []

    def run(argv: list[str], **options: object) -> SimpleNamespace:
        """Record the exact Git invocation and return a clean response.

        Args:
            argv: Argument vector passed to the Git subprocess.
            **options: Subprocess options recorded for assertions.

        Returns:
            Recorded Git subprocess response.
        """
        observed.append((argv, options))
        return SimpleNamespace(returncode=0, stdout="  root\n")

    # Install fake run calls.
    monkeypatch.setattr(core.subprocess, "run", run)
    checkout = tmp_path / "checkout"
    # Confirm Git receives an argument vector and a bounded noninteractive environment.
    assert core.git(checkout, "rev-parse", "--show-toplevel") == "root"
    # Record Git arguments and process options for the failure case.
    argv, options = observed[0]
    # Confirm Git runs against the selected checkout via an argument vector.
    assert argv == ["git", "-C", str(checkout), "rev-parse", "--show-toplevel"]
    assert options["timeout"] == 2
    assert options["check"] is False
    assert options["capture_output"] is True


def test_git_rejects_failed_repository_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Return a bounded repository diagnostic for Git failure.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Install fake run calls.
    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=128, stdout="secret detail", stderr="fatal: secret detail"
        ),
    )
    # Reject a failed Git query with a bounded repository diagnostic.
    with pytest.raises(core.WorkspaceError) as captured:
        core.git(tmp_path / "checkout", "rev-parse")
    # Confirm the rejected operation reports REPOSITORY_MISMATCH.
    assert captured.value.code == "REPOSITORY_MISMATCH"
    assert "secret detail" not in str(captured.value)


def test_repository_requires_nonbare_member_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retain only Git-reported worktree members under one common directory.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Install fake git calls.
    root, common, other = tmp_path / "checkout", tmp_path / "git/common", tmp_path / "other"
    responses = {
        ("rev-parse", "--show-toplevel"): str(root),
        ("rev-parse", "--is-bare-repository"): "false",
        ("rev-parse", "--path-format=absolute", "--git-common-dir"): str(common),
        (
            "worktree",
            "list",
            "--porcelain",
            "-z",
        ): f"worktree {root}\0HEAD abc\0worktree {other}\0",
    }
    monkeypatch.setattr(core, "git", lambda _path, *args: responses[args])
    # Retain only nonbare worktree members beneath one common root.
    assert core.repository(root) == (root, common, [root, other])
    # Replace the Git worktree response with a conflicting member.
    responses[("worktree", "list", "--porcelain", "-z")] = f"worktree {other}\0"
    # Reject repository identity that does not match the selected checkout.
    with pytest.raises(core.WorkspaceError) as captured:
        core.repository(root)
    # Confirm the rejected operation reports REPOSITORY_MISMATCH.
    assert captured.value.code == "REPOSITORY_MISMATCH"


def present_layout(tmp_path: Path) -> Path:
    """Initialize a real repository.

    Args:
        tmp_path: Disposable parent for the layout.

    Returns:
        The repository's worktree root.
    """
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    return checkout


def plain_layout(tmp_path: Path) -> Path:
    """Create a directory with no Git metadata on its ancestor chain.

    Args:
        tmp_path: Disposable parent for the layout.

    Returns:
        The plain directory.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    return plain


def broken_config_layout(tmp_path: Path) -> Path:
    """Initialize a repository whose configuration Git cannot parse.

    Args:
        tmp_path: Disposable parent for the layout.

    Returns:
        The broken repository's worktree root.
    """
    checkout = present_layout(tmp_path)
    (checkout / ".git/config").write_text("[broken\n")
    return checkout


def missing_layout(tmp_path: Path) -> Path:
    """Name a directory that does not exist.

    Args:
        tmp_path: Disposable parent for the layout.

    Returns:
        The absent path.
    """
    return tmp_path / "missing"


def alias_into_broken_layout(tmp_path: Path) -> Path:
    """Link a directory alias into a repository with unreadable configuration.

    Args:
        tmp_path: Disposable parent for the layout.

    Returns:
        The alias path.
    """
    alias = tmp_path / "alias"
    # Skip rather than fail where the platform cannot create the directory link.
    try:
        link_directory(alias, broken_config_layout(tmp_path))
    except (OSError, subprocess.CalledProcessError) as error:
        pytest.skip(f"directory links unsupported: {error}")
    return alias


def hollow_git_layout(tmp_path: Path) -> Path:
    """Create a directory whose ``.git`` entry is an empty directory.

    Args:
        tmp_path: Disposable parent for the layout.

    Returns:
        The directory holding the empty ``.git``.
    """
    hollow = tmp_path / "hollow"
    (hollow / ".git").mkdir(parents=True)
    return hollow


def dangling_git_link_layout(tmp_path: Path) -> Path:
    """Create a directory whose ``.git`` entry is a symbolic link to nothing.

    Args:
        tmp_path: Disposable parent for the layout.

    Returns:
        The directory holding the dangling link.
    """
    # Windows symbolic links need privileges this suite does not assume.
    if os.name == "nt":
        pytest.skip("symbolic links need extra privileges on Windows")
    dangling = tmp_path / "dangling"
    dangling.mkdir()
    (dangling / ".git").symlink_to(tmp_path / "missing")
    return dangling


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(present_layout, "present", id="present"),
        pytest.param(plain_layout, None, id="plain"),
        pytest.param(broken_config_layout, "REPOSITORY_MISMATCH", id="broken_config"),
        pytest.param(missing_layout, "REPOSITORY_MISMATCH", id="missing"),
        pytest.param(alias_into_broken_layout, "REPOSITORY_MISMATCH", id="alias_into_broken"),
        pytest.param(hollow_git_layout, "REPOSITORY_MISMATCH", id="hollow_git"),
        pytest.param(dangling_git_link_layout, "REPOSITORY_MISMATCH", id="dangling_git_link"),
    ],
)
def test_project_repository_separates_present_absent_and_failed_discovery(
    tmp_path: Path, build: Callable[[Path], Path], expected: str | None
) -> None:
    """Return a repository, prove absence, or raise when Git metadata cannot be used.

    Args:
        tmp_path: Disposable parent for one freshly built layout.
        build: Builder returning the directory to discover from.
        expected: ``"present"``, None for proven absence, or the exact failure code.
    """
    path = build(tmp_path)
    # A real repository resolves to its worktree root, common directory and members.
    if expected == "present":
        repository = core.project_repository(path)
        assert repository is not None
        root, common, trees = repository
        assert root.resolve() == path.resolve()
        assert common.resolve() == (path / ".git").resolve()
        assert root in trees
    # A directory without Git metadata on its ancestor chain is proven absent.
    elif expected is None:
        assert core.project_repository(path) is None
    # Unusable metadata or a missing directory is failed discovery, never absence.
    else:
        with pytest.raises(core.WorkspaceError) as captured:
            core.project_repository(path)
        assert captured.value.code == expected


def test_git_reports_a_plain_directory_as_absent(tmp_path: Path) -> None:
    """Have Git itself report a plain directory as absent rather than mismatched.

    Args:
        tmp_path: Disposable parent for the plain directory.
    """
    with pytest.raises(core.WorkspaceError) as absent:
        core.git(plain_layout(tmp_path), "rev-parse", "--show-toplevel")
    assert absent.value.code == "REPOSITORY_ABSENT"


class BindingDirectory:
    """Capture binding writes and issue view requests without filesystem access."""

    def __init__(self) -> None:
        """Start with no stored binding."""
        # Track binding documents, issue views, and child handles.
        self.data: dict[str, Any] = {}
        self.views: list[tuple[str, str]] = []
        self.children: list[str] = []

    def exists(self, name: str) -> bool:
        """Report only keys actually stored by this bounded double.

        Args:
            name: Requested file, directory, or control-entry name.

        Returns:
            Whether the requested test entry exists.
        """
        return name in self.data

    def json(self, name: str) -> Any:
        """Read a previously stored binding.

        Args:
            name: Requested file, directory, or control-entry name.

        Returns:
            Decoded JSON value for the selected fake file.
        """
        return self.data[name]

    def put(self, name: str, value: object) -> None:
        """Capture the committed binding value.

        Args:
            name: Requested file, directory, or control-entry name.
            value: Document value stored by the fake control boundary.
        """
        self.data[name] = value

    def issue_view(self, issue: str, target: str) -> None:
        """Capture the exact issue-only view target.

        Args:
            issue: Issue model used by this scenario.
            target: Target path or resource being exercised.
        """
        self.views.append((issue, target))

    def child(self, issue: str) -> BindingDirectory:
        """Expose a modeled canonical issue directory.

        Args:
            issue: Issue model used by this scenario.

        Returns:
            Fake child directory or issue context for the caller.
        """
        self.children.append(issue)
        return self

    def __enter__(self) -> BindingDirectory:
        """Open the modeled issue handle.

        Returns:
            This fake context manager for the enclosed operation.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the modeled issue handle.

        Args:
            *_args: Exception details supplied by the context-manager protocol.
        """


def bare_store() -> core.Store:
    """Supply the methods under test with a selected registered identity.

    Returns:
        Minimal store used to test repository discovery.
    """
    # Allocate a store without opening native handles for the model.
    store = core.Store.__new__(core.Store)
    # Resolve the disposable root for the filesystem boundary.
    store.request = {"host": "codex", "session_id": "session"}
    anchor = Path(Path.cwd().anchor)
    store.root = anchor / "linked"
    store.registration = {"repo_id": "repo", "main": str(anchor / "main")}
    store._bindings = BindingDirectory()  # type: ignore[assignment]
    store.local = BindingDirectory()  # type: ignore[assignment]
    store.task = BindingDirectory()  # type: ignore[assignment]
    return store


def test_store_binding_does_not_invent_absent_assignment() -> None:
    """Read only the explicit session key and persist its generation."""
    # Attach fake binding and directory handles to the model store.
    store = bare_store()
    # Read only the explicitly recorded session binding.
    assert store.binding() is None
    # Resolve the exact participant key for the selected session.
    key = core.participant_key(store.request)
    # Establish the issue state required by this transition.
    state = {"issue_id": "TEST-30", "participants": {key: {"generation": 3}}}
    # Persist the binding generation for that participant.
    store.save_binding(state, key)  # type: ignore[arg-type]
    # Confirm the view and binding reference the selected issue only.
    assert store.binding() == {
        "repo_id": "repo",
        "issue_id": "TEST-30",
        "participant_id": key,
        "binding_generation": 3,
    }


def test_store_view_exposes_only_selected_issue_link() -> None:
    """Open the canonical issue after publishing its exact linked view."""
    # Attach fake binding and directory handles to the model store.
    store = bare_store()
    # Expose only the requested issue view through the store.
    store.view("TEST-30")
    # Create the canonical issue target before linking its view.
    target = str(Path(store.registration["main"]) / ".task" / "TEST-30")
    # Confirm the view and binding reference the selected issue only.
    assert store.local.views == [("TEST-30", target)]
    assert store.task.children == ["TEST-30"]
    # Resolve the disposable root for the filesystem boundary.
    store.root = Path(store.registration["main"])
    # Expose only the requested issue view through the store.
    store.view("TEST-31")
    # Confirm the view and binding reference the selected issue only.
    assert store.local.views == [("TEST-30", target)]


def test_store_closes_tracked_handles_in_reverse_open_order() -> None:
    """Release child handles before their parent after success or failure."""
    # Record child and parent handle closure order.
    order: list[str] = []

    class Handle:
        """Record one descriptor release."""

        def __init__(self, name: str) -> None:
            """Name the modeled descriptor.

            Args:
                name: Requested file, directory, or control-entry name.
            """
            self.name = name

        def close(self) -> None:
            """Record release of this descriptor."""
            order.append(self.name)

    # Allocate a store without opening native handles for the model.
    store = core.Store.__new__(core.Store)
    # Give the store a parent and child handle to close.
    store.handles = []
    parent, child = Handle("parent"), Handle("child")
    # Confirm the store retains each opened handle until close.
    assert store.keep(parent) is parent  # type: ignore[arg-type]
    assert store.keep(child) is child  # type: ignore[arg-type]
    # Close retained handles from child to parent.
    store.close()
    assert order == ["child", "parent"]
    assert store.handles == []


class Node:
    """Expose direct named control entries to registration without native I/O."""

    def __init__(
        self,
        path: str,
        documents: dict[str, dict[str, object]],
        closed: list[str] | None = None,
    ) -> None:
        """Share one persistent document map across opened handles.

        Args:
            path: Filesystem path passed to the operation.
            documents: Documents exposed by the fake issue store.
            closed: Handle-close calls recorded for order assertions.
        """
        # Record native close calls for cleanup assertions.
        self.path = path
        self.documents = documents
        self.closed = closed
        self.identity = [3, 4]

    def __enter__(self) -> Node:
        """Open this modeled direct directory.

        Returns:
            This fake context manager for the enclosed operation.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave this modeled direct directory.

        Args:
            *_args: Exception details supplied by the context-manager protocol.
        """

    def child(self, name: str, _create: bool = False) -> Node:
        """Open a direct child using the same document map.

        Args:
            name: Requested file, directory, or control-entry name.
            _create: Unused create argument accepted by this fake.

        Returns:
            Fake child directory or issue context for the caller.
        """
        return Node(str(Path(self.path) / name), self.documents, self.closed)

    def close(self) -> None:
        """Record native-handle release after use or partial initialization."""
        # Record the close only when a close log was supplied.
        if self.closed is not None:
            # Append the released handle path to the close log.
            self.closed.append(self.path)

    def lock(self, _name: str) -> Node:
        """Hold a modeled registration lock for this synchronous test.

        Args:
            _name: Unused name argument accepted by this fake.

        Returns:
            Context manager for the fake issue lock.
        """
        return self

    def exists(self, name: str) -> bool:
        """Check only a direct document key.

        Args:
            name: Requested file, directory, or control-entry name.

        Returns:
            Whether the requested test entry exists.
        """
        return name in self.documents.get(self.path, {})

    def json(self, name: str) -> object:
        """Read an already published direct document.

        Args:
            name: Requested file, directory, or control-entry name.

        Returns:
            Decoded JSON value for the selected fake file.
        """
        return self.documents[self.path][name]

    def put(self, name: str, value: object) -> None:
        """Publish a direct document for later registration read-back.

        Args:
            name: Requested file, directory, or control-entry name.
            value: Document value stored by the fake control boundary.
        """
        self.documents.setdefault(self.path, {})[name] = value


def test_register_persists_and_reuses_exact_repository_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep the same repository ID on a matching registration retry.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Install fake repository, fs_identity, absolute calls.
    documents: dict[str, dict[str, object]] = {}
    root = tmp_path / "main"
    common = tmp_path / "git/common"
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents))
    request = {
        "worktree": str(root),
        "main_worktree": str(root),
        "host": "codex",
        "session_id": "s",
    }
    # Register the selected repository identity and verify its retry behavior.
    first = core.register(request)
    second = core.register(request)
    # Confirm the rejected operation reports REGISTERED.
    assert first == second
    assert first["code"] == "REGISTERED"
    assert (
        documents[str(root / ".task/.control")]["repository.json"]
        == documents[str(root / ".task")][".repository.json"]
    )


def test_register_refuses_changed_local_identity_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Preserve a conflicting local registration for explicit recovery.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Install fake repository, fs_identity, absolute calls.
    documents: dict[str, dict[str, object]] = {}
    root = tmp_path / "main"
    common = tmp_path / "git/common"
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents))
    request = {
        "worktree": str(root),
        "main_worktree": str(root),
        "host": "codex",
        "session_id": "s",
    }
    # Register the selected repository identity and verify its retry behavior.
    core.register(request)
    # Resolve the disposable root for the filesystem boundary.
    documents[str(root / ".task")][".repository.json"] = {"repo_id": "foreign"}
    # Reject a conflicting local registration without replacing it.
    with pytest.raises(core.WorkspaceError) as captured:
        core.register(request)
    # Confirm the rejected operation reports REPOSITORY_MISMATCH.
    assert captured.value.code == "REPOSITORY_MISMATCH"
    assert documents[str(root / ".task")][".repository.json"] == {"repo_id": "foreign"}


@pytest.mark.parametrize(
    "create,bindings,existing_issues",
    [(True, True, False), (False, False, False), (False, False, True)],
)
def test_store_initialization_verifies_registration_and_releases_handles(
    monkeypatch: pytest.MonkeyPatch,
    create: bool,
    bindings: bool,
    existing_issues: bool,
) -> None:
    """Validate local and canonical registrations before exposing store handles.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        create: Whether collection creation is permitted.
        bindings: Whether local binding metadata may be opened.
        existing_issues: Whether an issue collection already exists.
    """
    # Install fake repository, fs_identity, absolute calls.
    documents: dict[str, dict[str, object]] = {}
    closed: list[str] = []
    root = Path(Path.cwd().anchor) / "modeled-main"
    common = root.parent / "modeled-common"
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents, closed))
    # Register the selected repository identity and verify its retry behavior.
    registration = core.register(
        {"worktree": str(root), "main_worktree": str(root), "host": "codex", "session_id": "s"}
    )
    # Read-only classification may find an existing issue collection or none at all.
    if existing_issues:
        documents[str(root / ".task/.control")]["issues"] = {}
    before = {path: dict(data) for path, data in documents.items()}
    closed.clear()
    with core.Store(
        {"worktree": str(root), "repo_id": registration["repo_id"]},
        create=create,
        bindings=bindings,
    ) as store:
        assert store.registration["repo_id"] == registration["repo_id"]
        assert store.issues_present is (create or existing_issues)
        assert len(store.handles) == 5 + int(create or existing_issues) + int(bindings)
        if not (create or existing_issues):
            with pytest.raises(core.WorkspaceError) as missing:
                _ = store.issues
            assert missing.value.code == "RECOVERY_REQUIRED"
        if not bindings:
            with pytest.raises(core.WorkspaceError) as forbidden:
                _ = store.bindings
            assert forbidden.value.code == "RECOVERY_REQUIRED"
    assert (str(root / ".task/.bindings") in closed) is bindings
    assert documents == before
    # Check recorded closed against the required side effects.
    assert store.handles == []
    assert closed[-2:] == [str(root / ".task"), str(root)]


def test_store_initialization_closes_partial_handles_on_repo_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject a foreign request ID and close already opened local descriptors.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Install fake repository, fs_identity, absolute calls.
    documents: dict[str, dict[str, object]] = {}
    closed: list[str] = []
    root = tmp_path / "main"
    common = tmp_path / "git/common"
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents, closed))
    # Register the selected repository identity and verify its retry behavior.
    core.register(
        {"worktree": str(root), "main_worktree": str(root), "host": "codex", "session_id": "s"}
    )
    # Reject mismatched registration while releasing partial handles.
    with pytest.raises(core.WorkspaceError) as captured:
        core.Store({"worktree": str(root), "repo_id": "foreign"})
    # Confirm the rejected operation reports REPOSITORY_MISMATCH.
    assert captured.value.code == "REPOSITORY_MISMATCH"
    assert closed[-2:] == [str(root / ".task"), str(root)]


def test_register_persists_only_explicit_coordinator_startup_packet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bind startup to the observed session and preserve an identical retry.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Install fake repository, fs_identity, absolute calls.
    documents: dict[str, dict[str, object]] = {}
    root, common = tmp_path / "main", tmp_path / "git/common"
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents))
    request = {
        "worktree": str(root),
        "main_worktree": str(root),
        "host": "codex",
        "session_id": "session",
    }
    # Resolve the exact participant key for the selected session.
    key = core.participant_key(request)
    # Build the coordinator startup packet and observed host identity.
    packet = [
        {
            "id": "roadmap",
            "locator": "roadmap.md",
            "sha256": core.sha(b"roadmap"),
            "required": True,
            "authority": "task-workspace",
            "reason": "issue-startup",
            "stage": "planning",
            "reader": key,
        }
    ]
    setup = {"issue_id": "TEST-30", "issue_uuid": "uuid", "packet": packet, "coordinator": key}
    # Register the selected repository identity and verify its retry behavior.
    first = core.register({**request, "startup": setup})
    # Confirm the rejected operation reports REGISTERED.
    assert first["code"] == "REGISTERED"
    # Locate the persisted coordinator binding for read-back.
    binding_path = str(root / ".task/.bindings")
    assert documents[binding_path][key + ".startup.json"] == setup
    assert core.register({**request, "startup": setup})["repo_id"] == first["repo_id"]
    # Change startup authority to test conflicting retry behavior.
    altered = {**setup, "issue_id": "TEST-31"}
    # Reject a conflicting local registration without replacing it.
    with pytest.raises(core.WorkspaceError) as captured:
        core.register({**request, "startup": altered})
    # Confirm the rejected operation reports BINDING_CONFLICT.
    assert captured.value.code == "BINDING_CONFLICT"
    assert documents[binding_path][key + ".startup.json"] == setup


@pytest.mark.parametrize("changed", [None, "common", "common_identity", "main"])
def test_registration_identity_requires_actual_common_and_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, changed: str | None
) -> None:
    """Reject each foreign registration field before any absent-binding shortcut.

    Args:
        monkeypatch: Supply the actual shared metadata identity without filesystem I/O.
        tmp_path: Canonical repository and shared metadata paths for this case.
        changed: Registration identity field corrupted independently, or none.
    """
    # Supply a matching registration and then alter just one claimed identity.
    common_path, main = tmp_path / "common", tmp_path / "main"
    registration = {"common": str(common_path), "common_identity": "actual", "main": str(main)}
    monkeypatch.setattr(core, "fs_identity", lambda _path: "actual")
    if changed is not None:
        registration[changed] = "foreign"
        with pytest.raises(core.WorkspaceError) as failure:
            core._validate_registration_identity(registration, common_path, [main])
        assert failure.value.code == "REPOSITORY_MISMATCH"
    else:
        assert core._validate_registration_identity(registration, common_path, [main]) is None
