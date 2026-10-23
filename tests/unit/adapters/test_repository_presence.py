"""Validate repository presence, workspace identity and lookup/admission contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core
from tests.unit.adapters.test_common_contracts import native_event

pytestmark = pytest.mark.unit


def test_native_identity_requires_single_cursor_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native identity requires single cursor root.

    Args:
        tmp_path: Absolute checkout path used in the native event.
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    monkeypatch.setattr(common.core, "repository", lambda path: (Path(path), None, []))
    event = {"conversation_id": "conversation", "workspace_roots": [str(tmp_path)]}
    normalized = common.native_identity(event, "cursor")
    assert normalized["session_id"] == "conversation"
    assert normalized["cwd"] == str(tmp_path)
    # Empty, multiple, and non-path roots violate Cursor's single-root contract.
    for roots in ([], [str(tmp_path), str(tmp_path)], [7]):
        # Every invalid root list must fail before a session is derived.
        with pytest.raises(core.WorkspaceError) as captured:
            common.native_identity({**event, "workspace_roots": roots}, "cursor")
        assert captured.value.code == "HOST_UNSUPPORTED_WORKSPACE"


def test_native_tool_cursor_shell_rejects_foreign_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor shell work cannot redirect to an unbound repository.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
    """
    monkeypatch.setattr(common.core, "repository", lambda path: (Path(path), None, []))
    checkout = Path(Path.cwd().anchor) / "checkout"
    other = Path(Path.cwd().anchor) / "other"
    native = {
        **native_event("Shell", {"command": "echo ok", "working_directory": str(other)}),
        "cwd": str(checkout),
    }
    # Tool-supplied working directory cannot redirect to another checkout.
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_tool(native, "cursor")
    assert captured.value.code == "REPOSITORY_MISMATCH"
    native["tool_input"] = {"command": "echo ok", "working_directory": str(checkout)}
    assert common.native_tool(native, "cursor") == "Shell"


def test_canonical_bootstrap_rejects_foreign_session_before_store_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Canonical bootstrap rejects foreign session before store access.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
        tmp_path: Absolute checkout path used in the native event.
    """
    # Compare the observed session to a command naming a foreign session.
    event = {"cwd": str(tmp_path), "session_id": "actual"}
    command = common.bootstrap_command(
        {
            "operation": "diagnose",
            "worktree": str(tmp_path),
            "host": "codex",
            "session_id": "foreign",
        },
        "codex",
    )
    monkeypatch.setattr(
        common.core,
        "repository",
        lambda _path: (_ for _ in ()).throw(AssertionError("repository touched")),
    )
    assert common.canonical_bootstrap(event, command, "codex", ready=False) is False


def test_canonical_bootstrap_rejects_oversized_or_unparseable_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Command validation fails closed before any binding directory is opened.

    Args:
        monkeypatch: Replaces repository or lifecycle boundaries for the case.
        tmp_path: Absolute checkout path used in the native event.
    """
    # Reject malformed commands before any assignment directory opens.
    event = {"cwd": str(tmp_path / "checkout"), "session_id": "session"}
    monkeypatch.setattr(
        common.core.Directory, "absolute", lambda _path: pytest.fail("opened binding")
    )
    assert common.canonical_bootstrap(event, None, "codex", ready=False) is False
    assert common.canonical_bootstrap(event, "x" * 65537, "codex", ready=False) is False
    assert (
        common.canonical_bootstrap(event, "not a lifecycle command", "codex", ready=False) is False
    )


def test_non_repository_session_has_no_lookup_or_admission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Treat a working directory outside every repository as having no lifecycle store.

    Args:
        monkeypatch: Replace only repository discovery with a proven absence.
        tmp_path: Disposable non-Git working directory.
    """
    monkeypatch.setattr(core, "project_repository", lambda _path: None)
    observed = {**native_event("Write"), "cwd": str(tmp_path), "session_id": "fresh"}
    assert common.lookup_required(observed, "claude-code") is False
    assert common.existing_admission(observed, "claude-code") is None
