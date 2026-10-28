"""Check target location evidence against real disposable Git repositories.

Expected roots and identities come only from Git and ``os.stat``, never from the resolver.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from nplet.adapters import common, task_boundary
from nplet.adapters.repository_location import Location
from nplet.adapters.session_repository import carry_repository, session_repository
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.native_events import event
from tests.integration.adapters.task_boundary_support import (
    commit_and_link_worktree,
    cross_linked_repos,
    init_repo,
    task_store_snapshot,
)
from tests.support import Fixture

pytestmark = pytest.mark.integration
posix_links = pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic link points")
Key = tuple[str, tuple[int, int]]
Seen = tuple[str, Path, tuple[Path, Key] | None, str | None]


def git(directory: Path, *args: str) -> str:
    """Run Git in one directory and return its trimmed output.

    Args:
        directory: Directory Git runs in.
        *args: Git arguments.

    Returns:
        Standard output without surrounding whitespace.
    """
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def identity(directory: Path) -> tuple[Path, Key]:
    """Compute the expected worktree root and identity key from Git and ``os.stat``.

    Args:
        directory: Existing directory inside the repository.

    Returns:
        The worktree root and the (common directory, (st_dev, st_ino)) key.
    """
    # Ask Git for the common directory and root, then stat the directory independently.
    common_dir = git(directory, "rev-parse", "--path-format=absolute", "--git-common-dir")
    info = os.stat(common_dir)
    root = Path(git(directory, "rev-parse", "--show-toplevel"))
    return root, (os.path.normpath(common_dir), (info.st_dev, info.st_ino))


def want(kind: str, path: Path, repo: Path | None = None, error: str | None = None) -> Seen:
    """Build one expected point.

    Args:
        kind: Point kind.
        path: Expected point path.
        repo: Directory whose repository the point must carry; None for none.
        error: Expected bounded error code.

    Returns:
        The comparable expected point.
    """
    return kind, path, None if repo is None else identity(repo), error


def points(path: Path, repo: Path | None = None, error: str | None = None) -> list[Seen]:
    """Expect the lexical and resolved points of a link-free target.

    Args:
        path: Target path, identical in both spellings.
        repo: Directory whose repository both points must carry.
        error: Expected bounded error code for both points.

    Returns:
        The two expected points.
    """
    return [want("lexical", path, repo, error), want("resolved", path, repo, error)]


def seen(location: Location) -> list[Seen]:
    """Reduce actual points to kind, path, repository root and key, and error.

    Args:
        location: Resolver output for one target.

    Returns:
        The comparable actual points.
    """
    return [
        (
            p.kind,
            p.path,
            p.repository and (p.repository.repository.root, p.repository.key()),
            p.error,
        )
        for p in location.points
    ]


def edit(cwd: Path, host: str, tool: str, args: dict) -> list[Location]:
    """Normalize one native edit like its adapter does, then resolve its targets.

    Args:
        cwd: Session working directory.
        host: ``codex``, ``claude`` or ``cursor``.
        tool: Native edit tool.
        args: Native tool input.

    Returns:
        The location evidence for every target.
    """
    observed = event(cwd, host, tool, args)
    observed = (
        carry_repository(observed)
        if host == "codex"
        else common.native_identity(observed, "claude-code" if host == "claude" else host)
    )
    return task_boundary.edit_locations(observed, session_repository(observed))


def patch(*lines: str) -> str:
    """Wrap patch header lines in a Codex ``apply_patch`` envelope.

    Args:
        *lines: Patch body lines.

    Returns:
        The patch command text.
    """
    return "\n".join(["*** Begin Patch", *lines, "*** End Patch"])


def locate(cwd: Path, *targets: str) -> list[Location]:
    """Resolve targets from one Codex patch run in ``cwd``.

    Args:
        cwd: Session working directory.
        *targets: Updated file targets.

    Returns:
        One location per target.
    """
    return edit(
        cwd, "codex", "apply_patch", {"command": patch(*(f"*** Update File: {t}" for t in targets))}
    )


@pytest.fixture
def world(tmp_path: Path) -> Path:
    """Create session repository A with a linked worktree and nested repository, and V.

    Args:
        tmp_path: Disposable parent.

    Returns:
        Canonical parent holding ``A``, ``linked`` and ``V``.
    """
    base = tmp_path.resolve()
    a = init_repo(base / "A")
    commit_and_link_worktree(a)
    init_repo(a / "inner")
    init_repo(base / "V")
    (a / "docs").mkdir()
    (a / "docs/x.md").write_text("x\n")
    return base


@pytest.mark.parametrize("session", ["A", "plain"])
def test_c1_other_repositories_resolve_from_repo_and_non_git_sessions(
    tmp_path: Path, session: str
) -> None:
    """Resolve a clone, an unrelated repository and an unregistered one to their own identity.

    Args:
        tmp_path: Disposable parent.
        session: Session directory: repository A or a non-Git directory.
    """
    # Build A, its clone B, unrelated C, an unregistered repository and a plain directory.
    base = tmp_path.resolve()
    a = init_repo(base / "A")
    git(base, "clone", "-q", str(a), "B")
    repos = [base / "B", init_repo(base / "C"), init_repo(base / "unregistered")]
    (base / "plain").mkdir()
    # Every foreign target reports its own repository and none is in the workspace.
    targets = [repos[0] / "f.md", repos[1] / "f.md", repos[2] / "new/dir/f.md"]
    locations = locate(base / session, *map(str, targets))
    assert [seen(loc) for loc in locations] == [points(t, r) for t, r in zip(targets, repos)]
    assert not any(loc.workspace for loc in locations)


def test_c2_worktrees_share_identity_and_clones_differ(tmp_path: Path) -> None:
    """Share one key across linked worktrees; a clone with a copied registration differs.

    Args:
        tmp_path: Disposable parent for the clone.
    """
    case = Fixture()
    case.setUp()
    try:
        # Clone the registered fixture and copy its registration into the clone.
        clone = tmp_path.resolve() / "clone"
        git(tmp_path, "clone", "-q", str(case.root), str(clone))
        (clone / ".task").mkdir()
        shutil.copy2(case.root / ".task/.repository.json", clone / ".task/.repository.json")
        # The oracle itself proves the fixture's sharing before the resolver is judged.
        assert identity(case.root)[1] == identity(case.other)[1] != identity(clone)[1]
        roots = [case.root, case.other, clone]
        locations = locate(case.root, *(str(r / "f.md") for r in roots))
        assert [seen(loc) for loc in locations] == [points(r / "f.md", r) for r in roots]
        assert [loc.workspace for loc in locations] == [True, True, False]
    finally:
        case.doCleanups()


@pytest.mark.parametrize(
    ("cwd", "target", "landing", "repo", "workspace"),
    [
        pytest.param("A", "docs/x.md", "A/docs/x.md", "A", True, id="existing"),
        pytest.param("A", "new/a/b/c.md", "A/new/a/b/c.md", "A", True, id="missing_parents"),
        pytest.param("A", "my notes/f m.md", "A/my notes/f m.md", "A", True, id="spaces"),
        pytest.param("A", "docs/../../V/f.md", "V/f.md", "V", False, id="dotdot_out"),
        pytest.param("V", "../A/docs/x.md", "A/docs/x.md", "A", False, id="dotdot_in"),
        pytest.param("A", "inner/f.md", "A/inner/f.md", "A/inner", True, id="nested"),
        pytest.param("A", "../linked/f.md", "linked/f.md", "linked", True, id="gitfile_worktree"),
    ],
)
def test_c3_link_free_targets_carry_their_own_repository(
    world: Path, cwd: str, target: str, landing: str, repo: str, workspace: bool
) -> None:
    """Locate each spelling at its innermost repository.

    Args:
        world: Fixture parent.
        cwd: Session directory name.
        target: Native target text.
        landing: Expected normalized path below the parent.
        repo: Expected repository directory below the parent.
        workspace: Expected session-family membership.
    """
    [location] = locate(world / cwd, target)
    assert (location.target, location.workspace) == (target, workspace)
    assert seen(location) == points(world / landing, world / repo)


def test_c3_apply_patch_move_locates_both_sides(world: Path) -> None:
    """Locate the source and destination of a patch move independently.

    Args:
        world: Fixture parent.
    """
    command = patch("*** Update File: docs/x.md", "*** Move to: ../V/moved.md", "@@", "-x", "+y")
    source, moved = edit(world / "A", "codex", "apply_patch", {"command": command})
    assert seen(source) == points(world / "A/docs/x.md", world / "A")
    assert seen(moved) == points(world / "V/moved.md", world / "V")


@posix_links
def test_c3_c4_links_keep_link_and_destination_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep every hop's repository, and membership through any hop, for each session.

    Covers inward, outward, reverse and chained links, a link whose destination is a
    link or passes through one before ``..``, and a link into a non-Git directory,
    alone and after a direct target.

    Args:
        tmp_path: Disposable parent.
        monkeypatch: Times out one hop's query.
    """
    base = tmp_path.resolve()
    c, v = cross_linked_repos(base)
    d, a = init_repo(base / "repo-D"), init_repo(base / "repo-A")
    plain = base / "plain/notes"
    (d / "dir").mkdir()
    plain.mkdir(parents=True)
    # Path-component chain, destination chain, inward link and a link out of Git.
    (c / "l1").symlink_to(d / "dir", target_is_directory=True)
    (d / "dir/l2").symlink_to(v / "notes", target_is_directory=True)
    (d / "l2b").symlink_to(v / "notes", target_is_directory=True)
    (c / "l3").symlink_to(d / "l2b", target_is_directory=True)
    # A relative link reached through another link is read from its real directory.
    (d / "dir/l4").symlink_to(Path("../l2b"), target_is_directory=True)
    (c / "in").symlink_to(c / "notes", target_is_directory=True)
    (c / "pl").symlink_to(plain, target_is_directory=True)
    # ``..`` after a link leaves the link's destination, so that link is still a hop.
    (c / "up").symlink_to(d / "l2b/..", target_is_directory=True)
    # A link out of Git is absent on its own, not order-dependent.
    assert seen(locate(a, str(c / "pl/file.md"))[0]) == [
        want("lexical", c / "pl/file.md"),
        want("link", c / "pl", c),
        want("resolved", plain / "file.md"),
    ]
    expected = {
        plain / "a.md": points(plain / "a.md"),
        c / "pl/file.md": [
            want("lexical", c / "pl/file.md"),
            want("link", c / "pl", c),
            want("resolved", plain / "file.md"),
        ],
        c / "l3/file.md": [
            want("lexical", c / "l3/file.md", v),
            want("link", c / "l3", c),
            want("link", d / "l2b", d),
            want("resolved", v / "notes/file.md", v),
        ],
        c / "l1/l4/file.md": [
            want("lexical", c / "l1/l4/file.md", v),
            want("link", c / "l1", c),
            want("link", c / "l1/l4", d),
            want("link", d / "l2b", d),
            want("resolved", v / "notes/file.md", v),
        ],
        c / "up/file.md": [
            want("lexical", c / "up/file.md", v),
            want("link", c / "up", c),
            want("link", d / "l2b", d),
            want("resolved", v / "file.md", v),
        ],
        c / "in/file.md": [
            want("lexical", c / "in/file.md", c),
            want("link", c / "in", c),
            want("resolved", c / "notes/file.md", c),
        ],
        c / "link/file.md": [
            want("lexical", c / "link/file.md", v),
            want("link", c / "link", c),
            want("resolved", v / "notes/file.md", v),
        ],
        c / "l1/l2/file.md": [
            want("lexical", c / "l1/l2/file.md", v),
            want("link", c / "l1", c),
            want("link", c / "l1/l2", d),
            want("resolved", v / "notes/file.md", v),
        ],
        v / "back/file.md": [
            want("lexical", v / "back/file.md", c),
            want("link", v / "back", v),
            want("resolved", c / "notes/file.md", c),
        ],
    }
    # Membership per target: plain, link out of Git, destination chain, relative chain,
    # ``..`` after a link, inward link, outward link, path chain, reverse link.
    for session, workspace in [
        (a, [False, False, False, False, False, False, False, False, False]),
        (c, [False, True, True, True, True, True, True, True, True]),
        (d, [False, False, True, True, True, False, False, True, False]),
        (v, [False, False, True, True, True, False, True, True, True]),
    ]:
        locations = locate(session, *map(str, expected))
        assert [seen(loc) for loc in locations] == list(expected.values())
        assert [loc.workspace for loc in locations] == workspace, session
    # A failed query at a hop reached through a link still sees the root holding it.
    discover = core.project_repository

    def time_out_at_hop(path: str) -> core.Repository | None:
        """Time out at the real directory of ``c/l1/l2`` and delegate elsewhere.

        Args:
            path: Queried real directory.

        Returns:
            Git's answer for every other directory.

        Raises:
            subprocess.TimeoutExpired: For ``d/dir``.
        """
        if Path(path) == d / "dir":
            raise subprocess.TimeoutExpired(["git"], 2)
        return discover(path)

    monkeypatch.setattr(core, "project_repository", time_out_at_hop)
    [location] = locate(d, str(c / "l1/l2/file.md"))
    assert (location.state, location.workspace) == ("unresolved", True)


def failure_layouts(base: Path) -> dict[str, tuple[Path, list[Seen], str]]:
    """Build every failure layout, plus a symlinked ancestor that must still resolve.

    Args:
        base: Canonical parent.

    Returns:
        Target, expected points and derived state per layout id; link layouts only
        off Windows.
    """
    # A path in a bare Git directory fails --show-toplevel: REPOSITORY_MISMATCH.
    subprocess.run(["git", "init", "-q", "--bare", str(base / "bare.git")], check=True)
    (base / "bad").mkdir()
    (base / "bad/.git").write_text("garbage\n")
    linked = commit_and_link_worktree(init_repo(base / "main"))
    shutil.rmtree(base / "main/.git/worktrees/linked")
    mismatch = "REPOSITORY_MISMATCH"
    layouts = {
        name: (path, points(path, error=mismatch), "unresolved")
        for name, path in [
            ("bare", base / "bare.git/f.md"),
            ("malformed_gitfile", base / "bad/f.md"),
            ("stale_worktree", linked / "f.md"),
        ]
    }
    if os.name == "nt":
        return layouts
    # Links: a dangling one under repository C, and an alias of repository V.
    c, v = init_repo(base / "C"), init_repo(base / "V")
    (c / "dead").symlink_to(base / "missing", target_is_directory=True)
    (base / "alias").symlink_to(v, target_is_directory=True)
    layouts["dangling_link"] = (
        c / "dead/f.md",
        [
            want("lexical", c / "dead/f.md", error="DANGLING_LINK"),
            want("link", c / "dead", c),
            want("resolved", base / "missing/f.md"),
        ],
        "unresolved",
    )
    # The filesystem fails at the dangling link before ``..`` can collapse it.
    layouts["dangling_link_then_parent"] = (
        c / "dead/../f.md",
        [
            want("lexical", c / "f.md", c),
            want("link", c / "dead", c),
            want("resolved", c / "dead/../f.md", error="DANGLING_LINK"),
        ],
        "unresolved",
    )
    layouts["symlinked_ancestor"] = (
        base / "alias/sub/f.md",
        [
            want("lexical", base / "alias/sub/f.md", v),
            want("link", base / "alias"),
            want("resolved", v / "sub/f.md", v),
        ],
        "resolved",
    )
    return layouts


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param("symlinked_ancestor", marks=posix_links),
        pytest.param("dangling_link", marks=posix_links),
        pytest.param("dangling_link_then_parent", marks=posix_links),
        "malformed_gitfile",
        "bare",
        "stale_worktree",
    ],
)
def test_c5_failures_are_unresolved_and_linked_ancestors_resolve(
    tmp_path: Path, layout: str
) -> None:
    """Report bounded codes for failed discovery instead of absence.

    Args:
        tmp_path: Disposable parent.
        layout: Layout id from ``failure_layouts``.
    """
    base = tmp_path.resolve()
    target, expected, state = failure_layouts(base)[layout]
    [location] = locate(base, str(target))
    assert (seen(location), location.state) == (expected, state)


def test_c6_replaced_git_directory_is_observed_on_next_call(tmp_path: Path) -> None:
    """Report the new identity after ``.git`` is replaced.

    Args:
        tmp_path: Disposable parent.
    """
    base = tmp_path.resolve()
    c = init_repo(base / "C")
    before = identity(c)
    assert seen(locate(base, str(c / "f.md"))[0]) == points(c / "f.md", c)
    # Keep the old directory so its inode cannot be reused by the new one.
    (c / ".git").rename(base / "old-git")
    init_repo(c)
    assert identity(c) != before
    assert seen(locate(base, str(c / "f.md"))[0]) == points(c / "f.md", c)


@posix_links
def test_c6_repointed_link_is_followed_on_next_call(tmp_path: Path) -> None:
    """Follow a link's new destination on the next call.

    Args:
        tmp_path: Disposable parent.
    """
    base = tmp_path.resolve()
    alias = base / "alias"
    for repo in (init_repo(base / "V"), init_repo(base / "D")):
        alias.unlink(missing_ok=True)
        alias.symlink_to(repo, target_is_directory=True)
        assert seen(locate(base, str(alias / "f.md"))[0]) == [
            want("lexical", alias / "f.md", repo),
            want("link", alias),
            want("resolved", repo / "f.md", repo),
        ]


def test_c7_forged_payload_fields_have_no_effect(world: Path) -> None:
    """Ignore forged repository, location and point fields in the payload.

    Args:
        world: Fixture parent.
    """
    # Forge every evidence-shaped field on an otherwise identical event.
    clean = event(world / "A", "claude", "Write", {"file_path": "../V/f.md", "content": ""})
    forged = {
        **clean,
        "repository": core.repository(world / "V").to_json(),
        "locations": [{"target": "../V/f.md", "workspace": True, "points": []}],
        "points": [{"kind": "lexical", "path": str(world / "A"), "repository": None}],
    }
    # The carrier and the evidence both ignore the forged fields.
    carried = carry_repository(clean)
    assert carry_repository(forged)["repository"] == carried["repository"]
    session = session_repository(carried)
    result = task_boundary.edit_locations(forged, session)
    assert result == task_boundary.edit_locations(clean, session)
    assert not result[0].workspace


def test_c8_resolution_writes_nothing(tmp_path: Path) -> None:
    """Leave task stores, Git status and every modification time unchanged.

    Args:
        tmp_path: Disposable parent for an unregistered repository.
    """
    case = Fixture()
    case.setUp()
    try:
        base = tmp_path.resolve()
        v = init_repo(base / "V")
        repos = [case.root, case.other, v]
        trees = [Path(case.temp.name).resolve(), base]

        def state() -> tuple[object, ...]:
            """Capture every observable that resolution must not change.

            Returns:
                Task store bytes, Git status per repository and all modification times.
            """
            return (
                task_store_snapshot(case.root),
                [
                    git(r, "--no-optional-locks", "status", "--porcelain", "--ignored")
                    for r in repos
                ],
                {p: p.lstat().st_mtime_ns for tree in trees for p in tree.rglob("*")},
            )

        before = state()
        targets = [case.root / "notes/x.md", case.other / "f.md", v / "f.md", base / "plain/f.md"]
        locations = locate(case.root, *map(str, targets))
        assert [loc.state for loc in locations] == ["resolved"] * 3 + ["absent"]
        assert state() == before
    finally:
        case.doCleanups()


def test_c9_hosts_produce_identical_locations(world: Path) -> None:
    """Give identical evidence for the same targets through Codex, Claude and Cursor.

    Args:
        world: Fixture parent.
    """
    # Pin the Codex patch result to the Git oracle first.
    first, second = str(world / "A/docs/x.md"), str(world / "V/notes/y.md")
    expected = locate(world / "A", first, second)
    assert [seen(loc) for loc in expected] == [
        points(world / "A/docs/x.md", world / "A"),
        points(world / "V/notes/y.md", world / "V"),
    ]
    # Claude and Cursor Write and Edit events must give the same evidence.
    for host in ("claude", "cursor"):
        written = edit(world / "A", host, "Write", {"file_path": first, "content": ""})
        edited = edit(
            world / "A", host, "Edit", {"file_path": second, "old_string": "a", "new_string": "b"}
        )
        assert written + edited == expected, host
