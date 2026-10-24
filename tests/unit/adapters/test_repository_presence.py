"""Validate repository presence and identity contracts for native adapter sessions."""

from __future__ import annotations

from pathlib import Path

import pytest

from nplet.adapters import codex, common
from nplet.lifecycle import task_workspace as core
from tests.unit.adapters.native_event_support import native_event

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


def test_cursor_shell_metadata_outside_every_repository_stays_admissible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Accept Shell directory metadata when the session and the override lack a repository.

    Args:
        monkeypatch: Replace only repository discovery with a proven absence.
        tmp_path: Disposable non-Git working directory.
    """
    monkeypatch.setattr(common.core, "project_repository", lambda _path: None)
    plain = tmp_path / "plain"
    # A Shell override naming the same non-Git directory is still admissible.
    native = {
        **native_event("Shell", {"command": "pwd", "working_directory": str(plain)}),
        "cwd": str(plain),
    }
    assert common.native_tool(native, "cursor") == "Shell"
    # Lifecycle-command metadata follows the same rule.
    common.cursor_lifecycle_shell(native, {"command": "pwd", "cwd": str(plain)})
    # A session outside any repository cannot redirect into one.
    monkeypatch.setattr(
        common.core,
        "project_repository",
        lambda path: None if path == str(plain) else (Path(path), None, [Path(path)]),
    )
    native["tool_input"] = {"command": "pwd", "working_directory": str(tmp_path / "repo")}
    with pytest.raises(core.WorkspaceError) as captured:
        common.native_tool(native, "cursor")
    assert captured.value.code == "REPOSITORY_MISMATCH"


@pytest.mark.parametrize("host", ["claude-code", "cursor"])
def test_native_identity_carries_the_session_repository(
    host: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Carry the identity-time repository as JSON text, present or absent, for both hosts.

    Args:
        host: Native adapter whose identity normalization is exercised.
        monkeypatch: Replace repository discovery with a present, then absent, result.
        tmp_path: Disposable working directory and workspace root.
    """
    event = (
        {"session_id": "s", "cwd": str(tmp_path)}
        if host == "claude-code"
        else {"conversation_id": "s", "workspace_roots": [str(tmp_path)]}
    )
    # A present repository round-trips through text back to the same paths.
    present = (tmp_path, tmp_path / ".git", [tmp_path, tmp_path / "linked"])
    monkeypatch.setattr(common.core, "project_repository", lambda _path: present)
    normalized = common.native_identity(event, host)
    assert normalized["repository"] == {
        "root": str(tmp_path),
        "common": str(tmp_path / ".git"),
        "trees": [str(tmp_path), str(tmp_path / "linked")],
    }
    # Once carried, the repository is never discovered again.
    monkeypatch.setattr(common.core, "project_repository", lambda _path: pytest.fail("probed"))
    assert common.session_repository(normalized) == present
    assert common.session_root(normalized) == tmp_path
    # Proven absence is carried as None.
    monkeypatch.setattr(common.core, "project_repository", lambda _path: None)
    assert common.native_identity(event, host)["repository"] is None


def test_session_root_requires_a_repository_and_falls_back_to_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject a carried absence and discover an uncarried Codex repository from ``cwd``.

    Args:
        monkeypatch: Replace repository discovery for the uncarried event.
        tmp_path: Disposable working directory.
    """
    # A session carried outside every repository has no root to open.
    with pytest.raises(core.WorkspaceError) as captured:
        common.session_root({"cwd": str(tmp_path), "repository": None})
    assert captured.value.code == "REPOSITORY_ABSENT"
    # Without a carried value, discovery runs on the event's working directory.
    monkeypatch.setattr(
        common.core, "project_repository", lambda path: (Path(path), Path(path), [Path(path)])
    )
    assert common.session_repository({"cwd": str(tmp_path)}) == (tmp_path, tmp_path, [tmp_path])


def test_codex_payload_repository_field_is_discarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Never trust a native payload field named like the identity-time repository.

    Args:
        monkeypatch: Count repository discovery instead of touching Git or the store.
        tmp_path: Disposable non-Git working directory.
    """
    probes: list[str] = []

    def discover(path: str) -> None:
        """Record one discovery and report a session outside every repository."""
        probes.append(path)
        return None

    monkeypatch.setattr(core, "project_repository", discover)
    observed = {
        "hook_event_name": "PreToolUse",
        "cwd": str(tmp_path),
        "session_id": "s",
        "tool_use_id": "t",
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "note.md"), "content": "prose"},
        "repository": {"root": "/forged", "common": "/forged/.git", "trees": ["/forged"]},
    }
    # The forged field is dropped before classification, so discovery still runs.
    assert codex.handle(observed) == {}
    assert probes and set(probes) == {str(tmp_path)}
    assert "repository" not in observed
