"""Exercise structured-edit admission by effect class, approval and repository scope.

Roadmap approval text is synthetic; these tests do not prove human approval.
"""

import zlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from nplet.adapters import claude
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.native_events import event
from tests.integration.adapters.task_boundary_support import (
    allowed,
    init_repo,
    pre_decision,
    task_store_snapshot,
)
from tests.integration.adapters.test_repository_exemptions import declared, write_policy
from tests.support import Fixture

pytestmark = pytest.mark.integration

HOSTS = {"claude": "claude-code", "cursor": "cursor", "codex": "codex"}
IHDR = b"IHDR" + bytes(13)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + IHDR + zlib.crc32(IHDR).to_bytes(4, "big")


class Repos:
    """Disposable repositories beside the session workspace.

    Attributes:
        a: Declared other repository.
        c: Undeclared other repository.
        v: Repository the policy exempts as a vault.
        plain: Directory outside every repository.
        policy: Relocated exemption policy file.
    """

    def __init__(self, base: Path, policy: Path) -> None:
        """Create the repositories and declare the vault in the relocated policy.

        Args:
            base: Disposable parent outside every repository.
            policy: Relocated exemption policy path.
        """
        self.a, self.c, self.v = (init_repo(base / name) for name in ("repo-A", "repo-C", "V"))
        self.plain = base / "plain"
        for directory in (self.a / "docs", self.a / "src", self.c / "src", self.plain):
            directory.mkdir()
        (self.c / "logo.png").write_bytes(PNG)
        self.policy = write_policy(policy.parent, declared(self.v))
        assert self.policy == policy


@pytest.fixture
def repos(tmp_path: Path, isolated_exemption_policy: Path) -> Repos:
    """Create the other repositories and the vault policy.

    Args:
        tmp_path: Disposable parent.
        isolated_exemption_policy: Relocated policy path from the shared conftest.

    Returns:
        The repository layout.
    """
    return Repos(tmp_path.resolve(), isolated_exemption_policy)


@pytest.fixture(params=list(HOSTS))
def ready(request: pytest.FixtureRequest) -> Iterator[tuple[str, Fixture]]:
    """Create a ready coordinator for the parametrized host without roadmap approval.

    Args:
        request: Supplies the host name.

    Yields:
        The adapter name and the ready fixture.
    """
    case = Fixture()
    case.setUp()
    case.base["host"] = HOSTS[request.param]
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        case.create()
        case.ready()
        (case.root / "src").mkdir()
        yield request.param, case
    finally:
        case.doCleanups()


def write(cwd: Path, host: str, target: Path, call: str, bound: bool = False) -> dict:
    """Run one Write prehook and return the native decision.

    Args:
        cwd: Session working directory.
        host: Adapter name.
        target: Absolute edit target.
        call: Native call identifier.
        bound: Whether the call comes from the fixture coordinator session.

    Returns:
        The adapter's native response.
    """
    observed = event(cwd, host, "Write", {"file_path": str(target), "content": "x"}, call=call)
    if bound:
        observed.update(session_id="coordinator", conversation_id="coordinator")
    return pre_decision(host, observed)


def delete_patch(cwd: Path, target: Path, call: str, bound: bool = False) -> dict:
    """Run one Codex patch prehook that deletes a file.

    Args:
        cwd: Session working directory.
        target: Absolute file to delete.
        call: Native call identifier.
        bound: Whether the call comes from the fixture coordinator session.

    Returns:
        The Codex native response.
    """
    patch = f"*** Begin Patch\n*** Delete File: {target}\n*** End Patch"
    observed = event(cwd, "codex", "apply_patch", {"command": patch}, call=call)
    if bound:
        observed.update(session_id="coordinator")
    return pre_decision("codex", observed)


@pytest.mark.parametrize("host", list(HOSTS))
def test_unbound_edits_follow_their_effect_class(checkout: Path, repos: Repos, host: str) -> None:
    """Deny workspace and other-repository text edits; free vault and external edits.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
        host: Native adapter.
    """
    # Workspace edits of any type and text edits in another repository need a task.
    for target in (checkout / "src/a.py", checkout / "logo.png", repos.c / "src/b.py"):
        assert not allowed(host, write(checkout, host, target, "protected"))
    # Vault and external edits need none and leave no task store.
    for target in (repos.v / "note.md", repos.plain / "x.md"):
        assert allowed(host, write(checkout, host, target, "free"))
    # The policy file is reserved before any lifecycle lookup.
    assert "POLICY_ADMINISTRATION" in str(write(checkout, host, repos.policy, "policy"))
    assert not (checkout / ".task").exists()


def test_codex_binary_only_delete_is_free_outside_the_workspace(
    checkout: Path, repos: Repos
) -> None:
    """Free a binary-only delete in another repository but not in the workspace.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    # Only the before side exists, and it is binary.
    assert allowed("codex", delete_patch(checkout, repos.c / "logo.png", "free"))
    (checkout / "logo.png").write_bytes(PNG)
    assert not allowed("codex", delete_patch(checkout, checkout / "logo.png", "protected"))
    assert not (checkout / ".task").exists()


def test_ready_scope_admits_only_granted_paths(ready: tuple[str, Fixture], repos: Repos) -> None:
    """Admit granted protected paths and deny ungranted ones with SCOPE_REQUIRED.

    Args:
        ready: Adapter name and its ready coordinator fixture.
        repos: Other repositories and the vault policy.
    """
    host, case = ready
    root = case.root
    case.approve({root: ["src/"], repos.a: ["docs/"]})
    # Granted paths in the workspace and in the declared repository are admitted.
    for index, target in enumerate((root / "src/x.py", repos.a / "docs/y.md")):
        assert allowed(host, write(root, host, target, f"granted-{index}", bound=True))
    pending = case.state()["participants"][case.base["coordinator"]]["pending"]
    assert len(pending) == 2
    # Ungranted workspace, declared and undeclared repository paths are denied.
    before = task_store_snapshot(root)
    for index, target in enumerate((repos.a / "src/z.py", root / "README.md", repos.c / "n.md")):
        response = write(root, host, target, f"ungranted-{index}", bound=True)
        assert not allowed(host, response)
        assert "SCOPE_REQUIRED" in str(response)
    assert task_store_snapshot(root) == before


def test_ready_without_current_approval_is_denied(ready: tuple[str, Fixture], repos: Repos) -> None:
    """Deny protected edits while approval is pending or names another revision.

    Args:
        ready: Adapter name and its ready coordinator fixture.
        repos: Other repositories and the vault policy.
    """
    host, case = ready
    root = case.root
    target = root / "src/x.py"
    # A proposed scope awaiting approval grants nothing.
    case.approve({root: ["src/"]}, approved=False)
    assert "APPROVAL_REQUIRED" in str(write(root, host, target, "pending", bound=True))
    # An approval record naming another revision grants nothing either.
    state = case.state()
    text = (root / ".task/TEST-1/roadmap.md").read_text()
    case.call(
        "update",
        path="roadmap.md",
        content=text.replace("Proposed", "Approved").replace("Pending", "approved revision r0"),
        expected_revision=state["revision"],
        old_digest=state["files"]["roadmap.md"],
        provenance={"sources": case.evidence(), "status": "draft", "applicability": "test"},
    )
    assert "APPROVAL_REQUIRED" in str(write(root, host, target, "mismatch", bound=True))
    # Free edits still need no approval.
    assert allowed(host, write(root, host, repos.v / "note.md", "vault", bound=True))


def test_policy_edit_is_denied_even_with_whole_scope(
    ready: tuple[str, Fixture], repos: Repos
) -> None:
    """Deny the policy file even for a ready, approved, whole-repository scope.

    Args:
        ready: Adapter name and its ready coordinator fixture.
        repos: Other repositories and the vault policy.
    """
    host, case = ready
    root = case.root
    case.approve({root: ["./"], repos.policy.parent: ["./"]})
    before = task_store_snapshot(root)
    response = write(root, host, repos.policy, "policy", bound=True)
    assert "POLICY_ADMINISTRATION" in str(response)
    assert task_store_snapshot(root) == before


def test_task_free_edit_preserves_pending_ticket_read(checkout: Path, repos: Repos) -> None:
    """Leave the task store and a pending ticket read untouched by free edits.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    # Select a ticket so the session must read it before protected work.
    claude.handle(
        {
            "cwd": str(checkout),
            "session_id": "fresh",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Task: TEST-1",
        }
    )
    before = task_store_snapshot(checkout)
    assert any(name.endswith(".lookup-required.json") for name in before)
    # Vault, external and binary-only edits leave every task byte unchanged.
    for target in (repos.v / "note.md", repos.plain / "x.md"):
        assert allowed("claude", write(checkout, "claude", target, "free"))
    assert allowed("codex", delete_patch(checkout, repos.c / "logo.png", "free"))
    assert task_store_snapshot(checkout) == before
    # The pending ticket read still fences protected work.
    response = write(checkout, "claude", checkout / "src/a.py", "protected")
    assert "TICKET_READ_REQUIRED" in str(response)


def run(cwd: Path, host: str, tool: str, args: dict, call: str, bound: bool = False) -> dict:
    """Run one prehook for any native tool and return the native decision.

    Args:
        cwd: Session working directory.
        host: Adapter name.
        tool: Native tool name.
        args: Native tool input.
        call: Native call identifier.
        bound: Whether the call comes from the fixture coordinator session.

    Returns:
        The adapter's native response.
    """
    observed = event(cwd, host, tool, args, call=call)
    if bound:
        observed.update(session_id="coordinator", conversation_id="coordinator")
    return pre_decision(host, observed)


def malformed(host: str, target: Path) -> list[tuple[str, dict]]:
    """List unrecognized edit shapes for one host's edit tools.

    Args:
        host: Adapter name.
        target: Edit target named by each shape.

    Returns:
        Tool and input pairs that no edit contract accepts.
    """
    shapes = [
        ("Write", {"file_path": str(target), "content": "x", "exempt": True}),
        ("Edit", {"file_path": str(target), "old_string": "x"}),
    ]
    if host == "codex":
        shapes.append(("apply_patch", {"command": f"*** Add File: {target}\n+x"}))
    if host == "claude":
        shapes.append(("NotebookEdit", {"notebook_path": str(target), "source": "x"}))
    return shapes


@pytest.mark.parametrize("host", list(HOSTS))
def test_unbound_malformed_edit_is_invalid_even_outside_repositories(
    checkout: Path, repos: Repos, host: str
) -> None:
    """Deny an unrecognized edit shape even where a well-formed edit is task-free.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
        host: Native adapter.
    """
    for index, (tool, args) in enumerate(malformed(host, repos.plain / "x.md")):
        response = run(checkout, host, tool, args, f"malformed-{index}")
        assert "INVALID_REQUEST" in str(response)
    assert not (checkout / ".task").exists()


def test_ready_malformed_edit_is_invalid_under_whole_scope(ready: tuple[str, Fixture]) -> None:
    """Deny an unrecognized edit shape before readiness, approval or scope admit it.

    Args:
        ready: Adapter name and its ready coordinator fixture.
    """
    host, case = ready
    root = case.root
    case.approve({root: ["./"]})
    before = task_store_snapshot(root)
    for index, (tool, args) in enumerate(malformed(host, root / "src/x.py")):
        response = run(root, host, tool, args, f"malformed-{index}", bound=True)
        assert "INVALID_REQUEST" in str(response)
    assert task_store_snapshot(root) == before


def notebook(target: Path) -> tuple[str, dict]:
    """Build one Claude NotebookEdit call that replaces a cell.

    Args:
        target: Notebook path.

    Returns:
        The tool name and its native input.
    """
    args = {
        "notebook_path": str(target),
        "new_source": "x",
        "cell_id": "c1",
        "edit_mode": "replace",
    }
    return "NotebookEdit", args


def test_unbound_notebook_edit_follows_its_effect_class(checkout: Path, repos: Repos) -> None:
    """Free a notebook edit outside every repository; deny one in the workspace.

    Args:
        checkout: Unregistered session workspace.
        repos: Other repositories and the vault policy.
    """
    assert allowed("claude", run(checkout, "claude", *notebook(repos.plain / "n.ipynb"), "free"))
    assert not allowed("claude", run(checkout, "claude", *notebook(checkout / "n.ipynb"), "own"))
    assert not (checkout / ".task").exists()


@pytest.mark.parametrize("ready", ["claude"], indirect=True)
def test_ready_notebook_edit_needs_scope(ready: tuple[str, Fixture], repos: Repos) -> None:
    """Admit a granted notebook edit and deny one in an undeclared repository.

    Args:
        ready: Claude ready coordinator fixture.
        repos: Other repositories and the vault policy.
    """
    _, case = ready
    root = case.root
    case.approve({root: ["src/"]})
    granted = run(root, "claude", *notebook(root / "src/n.ipynb"), "granted", bound=True)
    assert allowed("claude", granted)
    other = run(root, "claude", *notebook(repos.c / "src/n.ipynb"), "undeclared", bound=True)
    assert "SCOPE_REQUIRED" in str(other)
