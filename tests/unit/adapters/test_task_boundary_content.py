"""Verify edit effect extraction and bounded content classification."""

import os
import zlib
from pathlib import Path

import pytest

from nplet.adapters import repository_location as location
from nplet.adapters import task_boundary as boundary
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit

TEXT, BINARY, UNKNOWN, ABSENT = "text", "binary", "unknown", "absent"
IHDR = b"IHDR" + bytes(13)
PNG = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + IHDR + zlib.crc32(IHDR).to_bytes(4, "big")


def write(path: Path, data: bytes) -> Path:
    """Write bytes to a path and return it.

    Args:
        path: Destination file.
        data: Content to store.

    Returns:
        The written path.
    """
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    ("data", "state"),
    [
        (b"\xff\xfe" + "café".encode("utf-16-le"), TEXT),
        (b"caf\xe9", UNKNOWN),
        (b"GIF89a notes\n", TEXT),
        (b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", UNKNOWN),
        (PNG, BINARY),
        (PNG[:8], UNKNOWN),
        (b"a" * (boundary.TEXT_LIMIT + 1), UNKNOWN),
        (b"", TEXT),
        (b"\xff\xd8\xff\xe0" + bytes(12) + b"\xff\xd9", BINARY),
        (b"GIF89a\x01\x00\x01\x00\x80\x00\x00;", BINARY),
        (b"wOF2\x00\x01\x00\x00" + (48).to_bytes(4, "big") + bytes(36), BINARY),
        (b"PK\x03\x04" + bytes(26) + b"PK\x05\x06" + bytes(18), BINARY),
    ],
    # Short ids keep the oversized case out of reports.
    ids=lambda value: value if isinstance(value, str) else f"{len(value)}-bytes",
)
def test_content_states_follow_exclusive_evidence(tmp_path: Path, data: bytes, state: str) -> None:
    """Text or binary evidence alone decides; both, neither or oversize stay unknown.

    Args:
        tmp_path: Directory holding the classified file.
        data: Generated file content.
        state: Expected content state.
    """
    assert boundary.classify(write(tmp_path / "f.bin", data), boundary.Budget()) == state


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
def test_special_files_and_missing_paths_are_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FIFO is unknown and a missing path absent, without any open call.

    Args:
        tmp_path: Directory holding the FIFO.
        monkeypatch: Fails any open so a read attempt cannot hang the test.
    """
    os.mkfifo(tmp_path / "pipe")
    monkeypatch.setattr(boundary.os, "open", pytest.fail)
    assert boundary.classify(tmp_path / "pipe", boundary.Budget()) == UNKNOWN
    assert boundary.classify(tmp_path, boundary.Budget()) == UNKNOWN
    assert boundary.classify(tmp_path / "missing", boundary.Budget()) == ABSENT


def test_call_limit_makes_later_sides_unknown(tmp_path: Path) -> None:
    """Once a call's inspection budget is exhausted, every further side is unknown.

    Args:
        tmp_path: Directory holding the text files.
    """
    assert boundary.Budget().remaining == 16 * 1024 * 1024
    budget = boundary.Budget(remaining=10)
    small = write(tmp_path / "small.md", b"tiny\n")
    assert boundary.classify(small, budget) == TEXT
    assert boundary.classify(write(tmp_path / "big.md", b"x" * 20), budget) == UNKNOWN
    assert boundary.classify(small, budget) == UNKNOWN


def effects(
    cwd: Path, tool: str, args: dict, repository: core.Repository | None = None
) -> list[tuple[object, str, str | None, str | None]]:
    """Extract one edit's effects as comparable tuples with discovery stubbed absent.

    Args:
        cwd: Session working directory.
        tool: Native tool name.
        args: Native tool input.
        repository: Session repository, if any.

    Returns:
        Target, operation, before state and after state per effect.
    """
    event = {"cwd": str(cwd), "tool_name": tool, "tool_input": args}
    found = boundary.edit_effects(event, repository, boundary.Budget())
    return [(e.location.target, e.op, e.before, e.after) for e in found]


@pytest.fixture(autouse=True)
def no_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Git discovery prove every directory outside any repository.

    Args:
        monkeypatch: Replaces repository discovery.
    """
    monkeypatch.setattr(location.core, "project_repository", lambda _path: None)


def test_native_file_tools_create_or_write_text(tmp_path: Path) -> None:
    """Write creates an absent target and overwrites an existing one; Edit writes.

    Args:
        tmp_path: Session directory holding the targets.
    """
    write(tmp_path / "old.md", b"old\n")
    write(tmp_path / "logo.png", PNG)
    assert effects(tmp_path, "Write", {"file_path": "new.md", "content": "x"}) == [
        ("new.md", "create", ABSENT, TEXT)
    ]
    assert effects(tmp_path, "Write", {"file_path": "old.md", "content": "x"}) == [
        ("old.md", "write", TEXT, TEXT)
    ]
    edit = {"file_path": "logo.png", "old_string": "a", "new_string": "b"}
    assert effects(tmp_path, "Edit", edit) == [("logo.png", "write", BINARY, TEXT)]


def test_patch_effects_keep_batch_order_and_both_move_ends(tmp_path: Path) -> None:
    """Each patch header yields its effect in order; a move yields source and destination.

    Args:
        tmp_path: Session directory holding the patch targets.
    """
    for name in ("b.md", "d.md", "e.md", "f.md"):
        write(tmp_path / name, b"text\n")
    write(tmp_path / "c.png", PNG)
    command = (
        "*** Begin Patch\n*** Add File: a.md\n+new\n*** Update File: b.md\n@@\n-x\n+y\n"
        "*** Delete File: c.png\n*** Update File: d.md\n*** Move to: e.md\n@@\n-x\n+y\n"
        "*** Update File: f.md\n*** Move to: g.md\n*** End Patch"
    )
    assert effects(tmp_path, "apply_patch", {"command": command}) == [
        ("a.md", "create", ABSENT, TEXT),
        ("b.md", "write", TEXT, TEXT),
        ("c.png", "delete", BINARY, None),
        ("d.md", "move-from", TEXT, None),
        ("e.md", "move-to", TEXT, TEXT),
        ("f.md", "move-from", TEXT, None),
        ("g.md", "move-to", ABSENT, TEXT),
    ]


def test_workspace_targets_are_not_inspected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target inside the session's repository keeps no before state and is never read.

    Args:
        tmp_path: Repository root holding the target.
        monkeypatch: Fails any content classification.
    """
    write(tmp_path / "a.md", b"text\n")
    monkeypatch.setattr(boundary, "classify", pytest.fail)
    repository = core.Repository(tmp_path, tmp_path / ".git", [tmp_path])
    args = {"file_path": "a.md", "content": "x"}
    assert effects(tmp_path, "Write", args, repository) == [("a.md", "write", None, TEXT)]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("Write", {"file_path": 7, "content": "x"}),
        ("Unknown", {"file_path": "a.md"}),
        (
            "apply_patch",
            {"command": "*** Begin Patch\n*** Add File: a\n*** Move to: b\n*** End Patch"},
        ),
        ("apply_patch", {"command": "*** Begin Patch\n*** Move to: b\n*** End Patch"}),
    ],
)
def test_malformed_edit_shapes_have_no_effects(tmp_path: Path, tool: str, args: dict) -> None:
    """Unknown, malformed or misplaced-move shapes yield no effects.

    Args:
        tmp_path: Session directory.
        tool: Native tool name.
        args: Malformed native input.
    """
    assert effects(tmp_path, tool, args) == []
