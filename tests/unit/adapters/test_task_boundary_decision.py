"""Check effect classes and scope coverage against the frozen task-boundary design.

Locations, identities and discovery results are built directly, so no Git runs.
"""

import os
from pathlib import Path

import pytest

from nplet.adapters import repository_exemptions as exemptions
from nplet.adapters import repository_location as location
from nplet.adapters import task_boundary as boundary
from nplet.lifecycle import task_workspace as core
from tests.platform_support import link_directory

pytestmark = pytest.mark.unit

TEXT, BINARY, UNKNOWN, ABSENT = "text", "binary", "unknown", "absent"
BASE = Path(os.path.realpath(os.path.abspath(os.sep))) / "base"
NONE = exemptions.Exemptions(frozenset(), None)


def identity(name: str, inode: int, tree: str | None = None) -> location.Identity:
    """Build a repository identity rooted at ``BASE / (tree or name)``.

    Args:
        name: Main worktree directory, whose ``.git`` is the common directory.
        inode: Common directory inode.
        tree: Linked worktree directory to use as the root instead.

    Returns:
        The identity.
    """
    root = BASE / (tree or name)
    return location.Identity(core.Repository(root, BASE / name / ".git", [root]), (1, inode))


W, A, C, V = identity("w", 1), identity("a", 2), identity("c", 3), identity("v", 4)
LINKED = identity("w", 1, "w-linked")
VAULT = exemptions.Exemptions(frozenset({V.key()}), None)


def effect(*points: tuple, before: str | None = TEXT, after: str | None = TEXT, ws=False):
    """Build an effect from ``(path, identity, error)`` point triples.

    Args:
        *points: Point path, repository identity or None, and error code or None.
        before: Existing content state.
        after: Output content state.
        ws: Whether the location belongs to the session workspace.

    Returns:
        The effect.
    """
    built = [location.Point("lexical", Path(p), r, e) for p, r, e in points]
    return boundary.Effect("write", location.Location("t", ws, built), before, after)


def classes(decision: boundary.Decision) -> list[int]:
    """Report the counts of protected, unchecked and free effects.

    Args:
        decision: The decision.

    Returns:
        The three counts.
    """
    return [len(decision.protected), len(decision.unchecked), len(decision.free)]


def in_c(name: str = "x") -> tuple:
    """Build a point triple inside repository C.

    Args:
        name: File name below C's root.

    Returns:
        The ``(path, identity, error)`` triple.
    """
    return (BASE / "c" / name, C, None)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (TEXT, None, [1, 0, 0]),
        (ABSENT, TEXT, [1, 0, 0]),
        (BINARY, None, [0, 0, 1]),
        (ABSENT, BINARY, [0, 0, 1]),
        (UNKNOWN, None, [0, 1, 0]),
        (BINARY, UNKNOWN, [0, 1, 0]),
        (TEXT, BINARY, [1, 0, 0]),
        (BINARY, TEXT, [1, 0, 0]),
    ],
)
def test_other_repository_content_decides(before, after, expected) -> None:
    """Any text side protects, binary or absent alone frees, anything else is unchecked.

    Args:
        before: Existing content state.
        after: Output content state.
        expected: Expected protected, unchecked and free counts.
    """
    decision = boundary.decide([effect(in_c("p.png"), before=before, after=after)], NONE)
    assert classes(decision) == expected


def test_workspace_is_protected_for_every_file_type() -> None:
    """Workspace effects need no content evidence."""
    binary = effect((BASE / "w" / "p.png", W, None), before=None, after=BINARY, ws=True)
    assert classes(boundary.decide([binary], NONE)) == [1, 0, 0]


def test_points_take_the_strongest_class_without_freeing_each_other() -> None:
    """Vault points alone are free; other points keep their own class."""
    vault = (BASE / "v" / "n.md", V, None)
    failed = (BASE / "gone", None, "UNREADABLE")
    external = (BASE / "tmp" / "f", None, None)
    effects = [
        effect(in_c("link"), vault),
        effect(vault),
        effect(external),
        effect(failed),
        effect(failed, in_c()),
    ]
    decision = boundary.decide(effects, VAULT)
    assert decision.protected == [effects[0], effects[4]]
    assert decision.unchecked == [effects[3]]
    assert decision.free == [effects[1], effects[2]]


def test_invalid_policy_exempts_nothing_and_reports_its_code() -> None:
    """A policy error leaves vault points to their repository class."""
    decision = boundary.decide(
        [effect((BASE / "v" / "n.md", V, None))], exemptions.Exemptions(frozenset(), "BAD")
    )
    assert classes(decision) == [1, 0, 0]
    assert decision.diagnostic == "BAD"


def test_policy_target_is_reserved(tmp_path: Path) -> None:
    """Lexical and real spellings of the policy file are protected and never authorized.

    Args:
        tmp_path: Directory holding the aliased policy location.
    """
    real = tmp_path / "real"
    real.mkdir()
    link_directory(tmp_path / "alias", real)
    policy = tmp_path / "alias" / "policy.json"
    for spelling in (policy, real / "policy.json"):
        target = effect((spelling, None, None), before=ABSENT)
        decision = boundary.decide([target], NONE, policy)
        assert decision.policy and decision.protected == [target]
        scope = [{"root": str(BASE / "w"), "paths": ["./"]}]
        memo = {str(BASE / "w"): (W, None)}
        assert boundary.unauthorized(decision, scope, W.repository, memo, NONE, policy) == [target]


@pytest.mark.parametrize("exists", [True, False])
def test_policy_case_variants_are_reserved(tmp_path: Path, exists: bool) -> None:
    """A case variant of the policy name is reserved; a variant directory is where case folds.

    Args:
        tmp_path: Directory holding the policy.
        exists: Whether the policy file exists, so a variant edits or creates it.
    """
    # Probe the temporary filesystem; only a case-insensitive one aliases the directory.
    (tmp_path / "probe").touch()
    folds = (tmp_path / "PROBE").exists()
    (tmp_path / "cfg").mkdir()
    policy = tmp_path / "cfg" / "repository-exemptions.json"
    if exists:
        policy.write_text("{}")
    for spelling, reserved in (
        (tmp_path / "cfg" / "REPOSITORY-Exemptions.json", True),
        (tmp_path / "CFG" / "repository-exemptions.json", folds),
    ):
        decision = boundary.decide([effect((spelling, None, None), before=ABSENT)], NONE, policy)
        assert decision.policy is reserved


def test_existing_policy_case_variant_is_reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing case-variant file beside the policy is reserved like an absent one.

    Args:
        tmp_path: Directory holding the policy and its variant.
        monkeypatch: Gives the variant its own identity, as a case-sensitive
            filesystem would.
    """
    policy = tmp_path / "repository-exemptions.json"
    policy.write_text("{}")
    variant = tmp_path / "REPOSITORY-Exemptions.json"
    variant.write_text("{}")
    real = boundary._file_id
    monkeypatch.setattr(boundary, "_file_id", lambda p: (0, 0) if p == variant else real(p))
    decision = boundary.decide([effect((variant, None, None))], NONE, policy)
    assert decision.policy is True


def test_policy_case_variant_is_reserved_before_its_directory_exists(tmp_path: Path) -> None:
    """A case variant of a policy whose directory is absent may create that policy.

    Args:
        tmp_path: Directory that does not yet hold the policy directory.
    """
    policy = tmp_path / "cfg" / "repository-exemptions.json"
    for spelling in (policy, tmp_path / "CFG" / "Repository-Exemptions.json"):
        decision = boundary.decide([effect((spelling, None, None), before=ABSENT)], NONE, policy)
        assert decision.policy is True
    other = effect((tmp_path / "cfg" / "other.json", None, None), before=ABSENT)
    assert boundary.decide([other], NONE, policy).policy is False


def test_session_repository_declared_as_vault_still_needs_scope() -> None:
    """A vault declaration does not lift the scope requirement inside the workspace."""
    target = effect((BASE / "w" / "README.md", W, None), ws=True)
    vault = exemptions.Exemptions(frozenset({W.key()}), None)
    decision = boundary.decide([target], vault)
    memo = {str(BASE / "w"): (W, None)}
    assert boundary.unauthorized(decision, [], W.repository, memo, vault) == [target]


def test_subdirectory_scope_root_grants_nothing() -> None:
    """A root below the worktree root would otherwise grant the whole repository."""
    target = effect((BASE / "w" / "README.md", W, None), ws=True)
    decision = boundary.decide([target], NONE)
    scope = [{"root": str(BASE / "w" / "docs"), "paths": ["./"]}]
    memo = {str(BASE / "w" / "docs"): (W, None)}
    assert boundary.unauthorized(decision, scope, W.repository, memo, NONE) == [target]


def test_scope_covers_only_granted_paths_of_each_repository() -> None:
    """Grants match per repository identity and relative to each point's own root."""
    memo: location.Memo = {
        str(BASE / "w"): (W, None),
        str(BASE / "a"): (A, None),
        str(BASE / "lost"): (None, "UNREADABLE"),
    }
    scope = [
        {"root": str(BASE / "w"), "paths": ["src/"]},
        {"root": str(BASE / "a"), "paths": ["docs/y.md"]},
        {"root": str(BASE / "lost"), "paths": ["./"]},
    ]
    covered = [
        effect((BASE / "w" / "src" / "x.py", W, None), ws=True),
        effect((BASE / "w-linked" / "src" / "x.py", LINKED, None), ws=True),
        effect((BASE / "a" / "docs" / "y.md", A, None)),
        effect((BASE / "a" / "docs" / "y.md", A, None), (BASE / "v" / "n", V, None)),
    ]
    uncovered = [
        effect((BASE / "a" / "src" / "z.py", A, None)),
        effect((BASE / "a" / "docs" / "y.mdx", A, None)),
        effect((BASE / "w" / "README.md", W, None), ws=True),
        effect((BASE / "w" / "SRC" / "x.py", W, None), ws=True),
        effect(in_c()),
        effect((BASE / "w" / "src" / "x.py", W, None), in_c(), ws=True),
        effect((BASE / "a" / "docs" / "y.md", A, None), ws=True),
        effect((BASE / "elsewhere", A, None)),
    ]
    decision = boundary.decide(covered + uncovered, VAULT)
    assert decision.protected == covered + uncovered
    assert boundary.unauthorized(decision, scope, W.repository, memo, VAULT) == uncovered
    assert boundary.unauthorized(decision, scope, None, memo, VAULT)[:2] == covered[:2]


def test_workspace_membership_and_vault_exemption_are_decided_per_point() -> None:
    """A nested repository needs both grants; a vault point behind a workspace link stays exempt."""
    nested = identity("w/sub", 5)
    inner = effect((BASE / "w" / "sub" / "x.py", nested, None), ws=True)
    linked = effect((BASE / "w" / "notes", W, None), (BASE / "v" / "n.md", V, None), ws=True)
    decision = boundary.decide([inner, linked], VAULT)
    memo = {str(BASE / "w"): (W, None), str(BASE / "w" / "sub"): (nested, None)}
    grants = [
        {"root": str(BASE / "w"), "paths": ["sub/", "notes"]},
        {"root": str(BASE / "w" / "sub"), "paths": ["x.py"]},
    ]
    assert boundary.unauthorized(decision, grants, W.repository, memo, VAULT) == []
    assert boundary.unauthorized(decision, grants[:1], W.repository, memo, VAULT) == [inner]
    assert boundary.unauthorized(decision, grants[1:], W.repository, memo, VAULT) == [inner, linked]


def test_alias_spelling_is_related_through_its_real_directory(tmp_path: Path) -> None:
    """A lexical point under a linked ancestor matches the root Git reports in real form.

    Args:
        tmp_path: Directory holding the real root and its alias.
    """
    root = tmp_path.resolve() / "r"
    (root / "src").mkdir(parents=True)
    link_directory(tmp_path / "alias", root)
    repo = location.Identity(core.Repository(root, root / ".git", [root]), (1, 9))
    target = effect((tmp_path / "alias" / "src" / "x.py", repo, None))
    decision = boundary.decide([target], NONE)
    scope = [{"root": str(root), "paths": ["src/"]}]
    assert boundary.unauthorized(decision, scope, None, {str(root): (repo, None)}, NONE) == []
