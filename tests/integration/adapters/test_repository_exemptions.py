"""Check vault exemptions against real disposable Git repositories and native edit events.

Declared keys come only from Git and ``os.stat``, never from the loader or resolver.
"""

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from nplet.adapters import common, task_boundary
from nplet.adapters.repository_exemptions import Exemptions, exempt, load
from nplet.adapters.repository_location import Location
from nplet.adapters.session_repository import session_repository
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


def declared(repo: Path) -> dict:
    """Build a vault entry from Git's common directory and its ``os.stat`` identity.

    Args:
        repo: Directory inside the vault.

    Returns:
        The policy entry for that repository.
    """
    common_dir = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    info = os.stat(common_dir)
    return {
        "common_git_dir": os.path.normpath(common_dir),
        "common_identity": [info.st_dev, info.st_ino],
    }


def write_policy(directory: Path, *entries: dict) -> Path:
    """Publish a private policy declaring the given entries.

    Args:
        directory: Policy directory, created when missing.
        *entries: Vault entries.

    Returns:
        The policy file path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    with core.Directory.absolute(directory) as opened:
        opened.write(
            "policy.json", json.dumps({"schema_version": 1, "vaults": list(entries)}).encode()
        )
    return directory / "policy.json"


def locate(cwd: Path, target: Path, **forged: object) -> Location:
    """Resolve one Claude ``Write`` target the way its adapter does.

    Args:
        cwd: Session working directory.
        target: Absolute edit target.
        **forged: Extra top-level fields added to the native payload.

    Returns:
        The target's location evidence.
    """
    observed = event(cwd, "claude", "Write", {"file_path": str(target), "content": ""})
    observed = common.native_identity({**observed, **forged}, "claude-code")
    [location] = task_boundary.edit_locations(observed, session_repository(observed))
    return location


def flags(cwd: Path, target: Path, exemptions: Exemptions) -> list[bool]:
    """Flag the target's points and prove matching leaves the evidence unchanged.

    Args:
        cwd: Session working directory.
        target: Absolute edit target.
        exemptions: Loaded policy.

    Returns:
        One flag per point.
    """
    location = locate(cwd, target)
    before = copy.deepcopy(location)
    result = exempt(location, exemptions)
    assert location == before and len(result) == len(location.points)
    return result


def test_c1_declared_vaults_and_their_worktrees_match_only(tmp_path: Path) -> None:
    """Match declared vaults and a linked worktree; reject look-alikes and nested repositories.

    Args:
        tmp_path: Disposable parent.
    """
    # Declare a personal and a shared vault; build every look-alike beside them.
    base = tmp_path.resolve()
    personal, shared = init_repo(base / "personal"), init_repo(base / "shared")
    linked = commit_and_link_worktree(shared)
    git(base, "clone", "-q", str(shared), "clone")
    nested = init_repo(personal / "nested")
    others = [init_repo(base / "unrelated"), init_repo(base / "elsewhere/personal"), base / "clone"]
    (base / "plain").mkdir()
    policy = write_policy(base / "cfg", declared(personal), declared(shared))
    exemptions = load(policy, {})
    assert exemptions.error is None
    # Every point of a vault target matches; no point of any other target does.
    for repo in (personal, shared, linked):
        assert all(flags(base, repo / "f.md", exemptions)), repo
    for repo in (*others, nested, base / "plain"):
        assert not any(flags(base, repo / "f.md", exemptions)), repo


@pytest.mark.parametrize("change", ["replaced_git_directory", "changed_identity"])
def test_c2_replaced_vault_or_changed_identity_stops_matching(tmp_path: Path, change: str) -> None:
    """Stop matching on the next load after ``.git`` is replaced or the identity changes.

    Args:
        tmp_path: Disposable parent.
        change: Which side of the declaration changes.
    """
    base = tmp_path.resolve()
    v = init_repo(base / "V")
    entry = declared(v)
    policy = write_policy(base / "cfg", entry)
    assert all(flags(base, v / "f.md", load(policy)))
    # Keep the old directory so its inode cannot be reused by the new one.
    if change == "replaced_git_directory":
        (v / ".git").rename(base / "old-git")
        init_repo(v)
        assert declared(v) != entry
    else:
        write_policy(base / "cfg", {**entry, "common_identity": [entry["common_identity"][0], 0]})
    assert not any(flags(base, v / "f.md", load(policy)))


def test_c3_policy_inside_any_repository_has_no_effect(tmp_path: Path) -> None:
    """Reject a policy directory inside a repository, including a copy inside the vault.

    Args:
        tmp_path: Disposable parent.
    """
    base = tmp_path.resolve()
    v, r = init_repo(base / "V"), init_repo(base / "R")
    for directory in (r / "cfg", v / ".config/nplet"):
        policy = write_policy(directory, declared(v))
        data = policy.read_bytes()
        exemptions = load(policy)
        assert exemptions == (frozenset(), "IN_REPOSITORY")
        assert not any(flags(base, v / "f.md", exemptions))
        assert policy.read_bytes() == data


@posix_links
@pytest.mark.parametrize("session", ["plain", "C"])
def test_c4_links_flag_only_vault_points(tmp_path: Path, session: str) -> None:
    """Flag only the vault's points across C-to-V and V-to-C links, keeping membership.

    Args:
        tmp_path: Disposable parent.
        session: Session directory: a non-Git parent or repository C.
    """
    base = tmp_path.resolve()
    c, v = cross_linked_repos(base)
    exemptions = load(write_policy(base / "cfg", declared(v)))
    cwd = c if session == "C" else base
    # C's link entry is C's point; the lexical and resolved spellings land in V.
    forward = locate(cwd, c / "link/file.md")
    assert forward.workspace is (session == "C")
    assert [p.kind for p in forward.points] == ["lexical", "link", "resolved"]
    assert flags(cwd, c / "link/file.md", exemptions) == [True, False, True]
    # In reverse, only V's link entry is a vault point.
    assert flags(cwd, v / "back/file.md", exemptions) == [False, True, False]


def test_c5_forged_payload_fields_have_no_effect(tmp_path: Path) -> None:
    """Ignore exemption-shaped fields forged into the native payload.

    Extra tool-input keys make the edit shape unknown, so forgery sits beside it.

    Args:
        tmp_path: Disposable parent.
    """
    base = tmp_path.resolve()
    v, u = init_repo(base / "V"), init_repo(base / "U")
    exemptions = load(write_policy(base / "cfg", declared(v)))
    forged = {"exempt": True, "vaults": [declared(u)], "exemptions": {"error": None}}
    for repo, expected in ((v, True), (u, False)):
        location = locate(base, repo / "f.md", **forged)
        assert location == locate(base, repo / "f.md")
        assert exempt(location, exemptions) == [expected] * len(location.points)


def test_c6_load_and_match_write_nothing(tmp_path: Path) -> None:
    """Leave the policy, task store, Git status and every modification time unchanged.

    Args:
        tmp_path: Disposable parent for the vault and policy.
    """
    case = Fixture()
    case.setUp()
    try:
        base = tmp_path.resolve()
        v = init_repo(base / "V")
        policy = write_policy(base / "cfg", declared(v), declared(case.root))
        trees = [Path(case.temp.name).resolve(), base]

        def state() -> tuple[object, ...]:
            """Capture every observable that loading and matching must not change.

            Returns:
                Policy bytes, task store bytes, Git status and all modification times.
            """
            return (
                policy.read_bytes(),
                task_store_snapshot(case.root),
                [
                    git(r, "--no-optional-locks", "status", "--porcelain", "--ignored")
                    for r in (case.root, case.other, v)
                ],
                {p: p.lstat().st_mtime_ns for tree in trees for p in tree.rglob("*")},
            )

        before = state()
        exemptions = load(policy, {})
        for target in (v / "f.md", case.root / "notes/x.md", case.other / "f.md"):
            assert all(flags(case.root, target, exemptions)), target
        assert state() == before
    finally:
        case.doCleanups()
