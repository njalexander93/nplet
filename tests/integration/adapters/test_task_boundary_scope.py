"""Check per-point scope decisions against real nested and linked Git repositories."""

import subprocess
from pathlib import Path

import pytest

from nplet.adapters import repository_exemptions as exemptions
from nplet.adapters import repository_location as location
from nplet.adapters import task_boundary as boundary
from tests.platform_support import link_directory

pytestmark = pytest.mark.integration


def test_nested_repository_and_linked_vault_points_take_their_own_scope(
    checkout: Path, tmp_path: Path
) -> None:
    """A nested repository needs both grants; a vault behind a workspace link stays exempt.

    Args:
        checkout: Session repository.
        tmp_path: Directory holding the external vault.
    """
    nested, vault = checkout / "vendor" / "lib", tmp_path / "vault"
    for root in (nested, vault):
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    link_directory(checkout / "notes", vault)
    memo: location.Memo = {}
    session, _ = location.discover(checkout, memo)
    held, _ = location.discover(vault.resolve(), memo)
    assert session is not None and held is not None
    policy = exemptions.Exemptions(frozenset({held.key()}), None)

    def write(target: str) -> list[boundary.Effect]:
        args = {"file_path": target, "content": "x"}
        event = {"cwd": str(checkout), "tool_name": "Write", "tool_input": args}
        return boundary.edit_effects(event, session.repository, boundary.Budget())

    inner, linked = write("vendor/lib/x.py"), write("notes/n.md")
    decision = boundary.decide(inner + linked, policy)
    assert decision.protected == inner + linked
    grants = [
        {"root": str(checkout), "paths": ["./"]},
        {"root": str(nested.resolve()), "paths": ["./"]},
    ]
    repository = session.repository
    assert boundary.unauthorized(decision, grants, repository, memo, policy) == []
    # The session grant alone misses the nested repository; the nested grant alone
    # misses the workspace spelling of both targets.
    assert boundary.unauthorized(decision, grants[:1], repository, memo, policy) == inner
    assert boundary.unauthorized(decision, grants[1:], repository, memo, policy) == inner + linked
