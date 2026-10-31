"""Independently check NPLET-92 edit admission against its acceptance criteria.

Each test names the criterion it covers. Roadmap approval text is synthetic; these
tests do not prove human approval.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from nplet.lifecycle import task_workspace as core
from tests.integration.adapters import test_edit_admission as base
from tests.integration.adapters.native_events import event
from tests.integration.adapters.task_boundary_support import (
    allowed,
    pre_decision,
    task_store_snapshot,
)
from tests.integration.adapters.test_edit_admission import PNG, Repos, write
from tests.integration.adapters.test_repository_exemptions import declared, write_policy
from tests.support import Fixture

pytestmark = pytest.mark.integration

# Reuse the repository layout and the per-host ready coordinator.
repos = base.repos
ready = base.ready


@pytest.fixture
def codex_ready() -> Iterator[Fixture]:
    """Create a ready Codex coordinator without roadmap approval.

    Yields:
        The ready fixture.
    """
    case = Fixture()
    case.setUp()
    case.base["host"] = "codex"
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        case.ready()
        (case.root / "src").mkdir()
        yield case
    finally:
        case.doCleanups()


def patch(cwd: Path, call: str, *lines: str, bound: bool = False) -> dict:
    """Run one Codex ``apply_patch`` prehook.

    Args:
        cwd: Session working directory.
        call: Native call identifier.
        *lines: Patch body lines between the begin and end markers.
        bound: Whether the call comes from the fixture coordinator session.

    Returns:
        The Codex native response.
    """
    body = "\n".join(("*** Begin Patch", *lines, "*** End Patch"))
    observed = event(cwd, "codex", "apply_patch", {"command": body}, call=call)
    if bound:
        observed.update(session_id="coordinator")
    return pre_decision("codex", observed)


def pin_roadmap(case: Fixture) -> None:
    """Assign and acknowledge a coordinator packet that pins the roadmap bytes.

    Args:
        case: Ready fixture whose packet is replaced.
    """
    state = case.state()
    ref = {
        "id": "roadmap",
        "locator": "roadmap.md",
        "sha256": state["files"]["roadmap.md"],
        "required": True,
        "authority": "fixture",
        "reason": "own",
        "stage": "planning",
        "reader": case.base["coordinator"],
    }
    case.call(
        "scope",
        expected_revision=state["revision"],
        target_participant=case.base["coordinator"],
        packet=[ref],
    )
    reacknowledge(case)


def reacknowledge(case: Fixture) -> None:
    """Read and acknowledge the coordinator's current packet.

    Args:
        case: Fixture whose coordinator re-establishes readiness.
    """
    case.call("acknowledge", packet_digest=case.call("read")["packet_digest"])


def test_c6_scope_change_resets_approval_and_readiness(ready: tuple[str, Fixture]) -> None:
    """Criterion 6: a changed grant needs new approval and a fresh acknowledgment.

    Args:
        ready: Adapter name and its ready coordinator fixture.
    """
    host, case = ready
    root = case.root
    (root / "docs").mkdir()
    pin_roadmap(case)
    case.approve({root: ["src/"]}, refresh=False)
    # Every roadmap update stales the pinned packet until it is acknowledged again.
    assert not allowed(host, write(root, host, root / "src/x.py", "stale", bound=True))
    reacknowledge(case)
    assert allowed(host, write(root, host, root / "src/x.py", "granted", bound=True))
    # Proposing a wider grant withdraws approval of the earlier one too.
    case.approve({root: ["src/", "docs/"]}, approved=False, refresh=False)
    reacknowledge(case)
    for index, target in enumerate((root / "src/x.py", root / "docs/y.md")):
        response = write(root, host, target, f"pending-{index}", bound=True)
        assert "APPROVAL_REQUIRED" in str(response)
    # Approval of the new revision admits the added path after readiness.
    case.approve({root: ["src/", "docs/"]}, refresh=False)
    reacknowledge(case)
    assert allowed(host, write(root, host, root / "docs/y.md", "widened", bound=True))


def test_c6_legacy_roadmap_receives_no_implicit_grant(ready: tuple[str, Fixture]) -> None:
    """Criterion 6: an approved roadmap without a scope field grants nothing.

    Args:
        ready: Adapter name and its ready coordinator fixture.
    """
    host, case = ready
    root = case.root
    state = case.state()
    legacy = (
        "**Proposal status:** Approved\n**Proposal revision:** r1\n"
        "**Approval record:** Human approved revision r1.\n"
    )
    case.call(
        "update",
        path="roadmap.md",
        content=legacy,
        expected_revision=state["revision"],
        old_digest=state["files"]["roadmap.md"],
        provenance={"sources": case.evidence(), "status": "draft", "applicability": "test"},
    )
    response = write(root, host, root / "src/x.py", "legacy", bound=True)
    assert "SCOPE_REQUIRED" in str(response)


def test_c5_c15_stale_packet_survives_task_free_edits(
    ready: tuple[str, Fixture], repos: Repos
) -> None:
    """Criteria 5 and 15: stale readiness denies; free edits leave it stale.

    Args:
        ready: Adapter name and its ready coordinator fixture.
        repos: Other repositories and the vault policy.
    """
    host, case = ready
    root = case.root
    pin_roadmap(case)
    case.approve({root: ["src/"]}, refresh=False)
    assert "NOT_READY" in str(write(root, host, root / "src/x.py", "stale-before", bound=True))
    before = task_store_snapshot(root)
    for index, target in enumerate((repos.plain / "x.md", repos.v / "note.md")):
        assert allowed(host, write(root, host, target, f"free-{index}", bound=True))
    assert task_store_snapshot(root) == before
    # The same stale readiness still fences protected work.
    assert "NOT_READY" in str(write(root, host, root / "src/x.py", "stale-after", bound=True))
    reacknowledge(case)
    assert allowed(host, write(root, host, root / "src/x.py", "fresh", bound=True))


def test_c7_moves_keep_both_ends(checkout: Path, repos: Repos) -> None:
    """Criteria 7 and 13: a move is protected when either end is.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    (checkout / "a.py").write_text("x")
    (repos.plain / "a.txt").write_text("x")
    (repos.c / "src/old.py").write_text("x")
    protected = [
        # Moving workspace text out still removes it from the workspace.
        ("*** Update File: " + str(checkout / "a.py"), f"*** Move to: {repos.plain / 'a.py'}"),
        # Moving external text into another repository writes text there.
        ("*** Update File: " + str(repos.plain / "a.txt"), f"*** Move to: {repos.c / 'src/a.py'}"),
        # Moving text out of another repository removes protected text.
        ("*** Update File: " + str(repos.c / "src/old.py"), f"*** Move to: {repos.plain / 'b'}"),
    ]
    for index, lines in enumerate(protected):
        assert not allowed("codex", patch(checkout, f"protected-{index}", *lines))
    # Moving a recognized binary away from another repository is task-free.
    binary = ("*** Update File: " + str(repos.c / "logo.png"), f"*** Move to: {repos.plain / 'l'}")
    assert allowed("codex", patch(checkout, "binary", *binary))
    assert not (checkout / ".task").exists()


def test_c7_c11_overwrite_and_delete_in_another_repository(checkout: Path, repos: Repos) -> None:
    """Criteria 7 and 11: text over a binary, text named .png and text deletes need a task.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    (repos.c / "notes.txt").write_text("x")
    for index, target in enumerate((repos.c / "logo.png", repos.c / "new.png")):
        assert not allowed("claude", write(checkout, "claude", target, f"png-{index}"))
    delete = f"*** Delete File: {repos.c / 'notes.txt'}"
    assert not allowed("codex", patch(checkout, "delete-text", delete))
    # Text that already sits under a .png name stays protected when deleted.
    (repos.c / "fake.png").write_text("not an image")
    assert not allowed(
        "codex", patch(checkout, "fake-png", f"*** Delete File: {repos.c / 'fake.png'}")
    )


def test_c7_mixed_patch_is_denied_as_a_whole(checkout: Path, repos: Repos) -> None:
    """Criterion 7: one protected effect denies a patch whose other effects are free.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    free = [
        f"*** Add File: {repos.plain / 'x.md'}",
        "+x",
        f"*** Add File: {repos.v / 'n.md'}",
        "+x",
    ]
    assert allowed("codex", patch(checkout, "free", *free))
    for index, extra in enumerate((repos.c / "src/b.py", checkout / "src/a.py")):
        response = patch(checkout, f"mixed-{index}", *free, f"*** Add File: {extra}", "+x")
        assert not allowed("codex", response)
    assert not (checkout / ".task").exists()


def test_c4_c7_scoped_move_needs_both_ends(codex_ready: Fixture, repos: Repos) -> None:
    """Criteria 4 and 7: a scoped task cannot move a granted file to an ungranted path.

    Args:
        codex_ready: Ready Codex coordinator.
        repos: Other repositories and the vault policy.
    """
    root = codex_ready.root
    codex_ready.approve({root: ["src/"], repos.a: ["docs/"]})
    (root / "src/a.py").write_text("x")
    inside = (f"*** Update File: {root / 'src/a.py'}", f"*** Move to: {root / 'src/b.py'}")
    assert allowed("codex", patch(root, "inside", *inside, bound=True))
    for index, destination in enumerate(
        (root / "README.md", repos.a / "src/a.py", repos.c / "a.py")
    ):
        lines = (f"*** Update File: {root / 'src/a.py'}", f"*** Move to: {destination}")
        assert "SCOPE_REQUIRED" in str(patch(root, f"outside-{index}", *lines, bound=True))
    # A granted add beside an undeclared repository add is denied as a whole.
    mixed = (f"*** Add File: {repos.a / 'docs/n.md'}", "+x", f"*** Add File: {repos.c / 'n'}", "+x")
    assert "SCOPE_REQUIRED" in str(patch(root, "mixed", *mixed, bound=True))


def test_c8_links_use_merged_evidence(checkout: Path, repos: Repos) -> None:
    """Criterion 8: an exempt vault point never hides a protected repository point.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    (repos.v / "notes").mkdir()
    (repos.c / "link").symlink_to(repos.v / "notes", target_is_directory=True)
    (repos.v / "back").symlink_to(repos.c / "src", target_is_directory=True)
    (checkout / "vault").symlink_to(repos.v / "notes", target_is_directory=True)
    (repos.plain / "vault").symlink_to(repos.v / "notes", target_is_directory=True)
    # Spelled through C or the workspace, or landing in C, the edit is protected.
    for index, target in enumerate(
        (repos.c / "link/n.md", repos.v / "back/n.md", checkout / "vault/n.md")
    ):
        assert not allowed("claude", write(checkout, "claude", target, f"linked-{index}"))
    # Spelled from outside every repository into the vault, it is free.
    for index, target in enumerate((repos.v / "notes/n.md", repos.plain / "vault/n.md")):
        assert allowed("claude", write(checkout, "claude", target, f"vault-{index}"))
    assert not (checkout / ".task").exists()


def test_c8_linked_worktree_is_covered_by_its_repository_scope(
    ready: tuple[str, Fixture],
) -> None:
    """Criterion 8: a scope rooted at the main worktree covers its linked worktree.

    Args:
        ready: Adapter name and its ready coordinator fixture.
    """
    host, case = ready
    root, other = case.root, case.other
    case.approve({root: ["src/"]})
    (other / "src").mkdir()
    assert allowed(host, write(root, host, other / "src/x.py", "linked", bound=True))
    response = write(root, host, other / "README.md", "linked-outside", bound=True)
    assert "SCOPE_REQUIRED" in str(response)


def test_c9_failed_location_never_erases_known_evidence(checkout: Path, repos: Repos) -> None:
    """Criterion 9: a dangling link alone adds no task; known repositories still protect.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    nowhere = repos.plain.parent / "nowhere"
    for holder in (repos.plain, checkout, repos.c):
        (holder / "dangling").symlink_to(nowhere, target_is_directory=True)
    failed = (f"*** Add File: {repos.plain / 'dangling/x.md'}", "+x")
    assert allowed("codex", patch(checkout, "failed-only", *failed))
    # The failed target never frees a protected neighbour in the same patch.
    neighbour = (f"*** Add File: {repos.c / 'src/b.py'}", "+x")
    assert not allowed("codex", patch(checkout, "failed-and-protected", *failed, *neighbour))
    # A failed point below the workspace or another repository stays protected.
    for index, target in enumerate((checkout / "dangling/x.md", repos.c / "dangling/x.md")):
        assert not allowed("claude", write(checkout, "claude", target, f"known-{index}"))
    assert not (checkout / ".task").exists()


def test_c9_failed_location_needs_no_grant(codex_ready: Fixture, repos: Repos) -> None:
    """Criterion 9: a failed point beside a granted path adds no scope requirement.

    Args:
        codex_ready: Ready Codex coordinator.
        repos: Other repositories and the vault policy.
    """
    root = codex_ready.root
    codex_ready.approve({root: ["src/"]})
    (repos.plain / "dangling").symlink_to(repos.plain.parent / "nowhere")
    lines = (
        f"*** Add File: {root / 'src/x.py'}",
        "+x",
        f"*** Add File: {repos.plain / 'dangling/x.md'}",
        "+x",
    )
    assert allowed("codex", patch(root, "granted-and-failed", *lines, bound=True))


@pytest.mark.parametrize("content", [b"{not json", b'{"schema_version": 2, "vaults": []}'])
def test_c10_invalid_policy_exempts_nothing(checkout: Path, repos: Repos, content: bytes) -> None:
    """Criterion 10: an invalid policy protects the vault; external work stays free.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
        content: Invalid policy bytes.
    """
    repos.policy.write_bytes(content)
    assert not allowed("claude", write(checkout, "claude", repos.v / "note.md", "vault"))
    assert allowed("claude", write(checkout, "claude", repos.plain / "x.md", "external"))
    assert not (checkout / ".task").exists()


def test_c14_markers_and_payload_flags_cannot_exempt(checkout: Path, repos: Repos) -> None:
    """Criterion 14: in-repository markers and extra payload fields grant no exemption.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    # A policy copy and a marker inside C declare C, but only the user's policy counts.
    write_policy(repos.c / ".config/nplet", declared(repos.c))
    (repos.c / ".nplet-vault").write_text("vault")
    assert not allowed("claude", write(checkout, "claude", repos.c / "src/b.py", "marker"))
    # A forged field on the edit is not an exemption either.
    args = {"file_path": str(repos.c / "src/b.py"), "content": "x", "vault": True}
    observed = event(checkout, "claude", "Write", args, call="flag")
    assert not allowed("claude", pre_decision("claude", observed))


def test_c14_policy_is_reserved_in_patches(checkout: Path, repos: Repos) -> None:
    """Criterion 14: moving onto, deleting or linking to the policy is administration.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    (repos.plain / "p.json").write_text("{}")
    (repos.plain / "cfg").symlink_to(repos.policy.parent, target_is_directory=True)
    cases = [
        (f"*** Update File: {repos.plain / 'p.json'}", f"*** Move to: {repos.policy}"),
        (f"*** Delete File: {repos.policy}",),
        (f"*** Update File: {repos.plain / 'cfg' / repos.policy.name}",),
    ]
    for index, lines in enumerate(cases):
        assert "POLICY_ADMINISTRATION" in str(patch(checkout, f"policy-{index}", *lines))


@pytest.mark.parametrize("target", ["policy", "undeclared"])
def test_c14_ready_task_cannot_bypass_gate_with_payload_flag(
    ready: tuple[str, Fixture], repos: Repos, target: str
) -> None:
    """Criteria 4 and 14: a forged field never lets a ready task skip the effect gate.

    Args:
        ready: Adapter name and its ready coordinator fixture.
        repos: Other repositories and the vault policy.
        target: Whether the forged edit targets the policy or undeclared repository C.
    """
    host, case = ready
    root = case.root
    case.approve({root: ["./"]})
    path = repos.policy if target == "policy" else repos.c / "src/b.py"
    args = {"file_path": str(path), "content": "{}", "exempt": True}
    observed = event(root, host, "Write", args, call="forged")
    observed.update(session_id="coordinator", conversation_id="coordinator")
    assert not allowed(host, pre_decision(host, observed))


def test_c16_control_store_stays_reserved_under_whole_scope(ready: tuple[str, Fixture]) -> None:
    """Criterion 16: whole-repository scope never admits raw control-store edits.

    Args:
        ready: Adapter name and its ready coordinator fixture.
    """
    host, case = ready
    root = case.root
    case.approve({root: ["./"]})
    assert allowed(host, write(root, host, root / "src/x.py", "granted", bound=True))
    before = task_store_snapshot(root)
    for index, target in enumerate(
        (root / ".task/TEST-1/roadmap.md", root / ".git/config", root / ".claude/settings.json")
    ):
        response = write(root, host, target, f"control-{index}", bound=True)
        assert "NOT_OWNER" in str(response)
    assert task_store_snapshot(root) == before


def test_c3_png_bytes_in_vault_and_external_stay_free(checkout: Path, repos: Repos) -> None:
    """Criterion 3: binary-only and vault deletes need no task.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    (repos.v / "img.png").write_bytes(PNG)
    (repos.v / "n.md").write_text("x")
    for index, target in enumerate((repos.v / "img.png", repos.v / "n.md")):
        assert allowed("codex", patch(checkout, f"vault-{index}", f"*** Delete File: {target}"))
