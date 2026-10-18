"""Exercise actual platform filesystem safety and process locks in disposable paths.

These tests run on all three native operating systems. Windows-only ACL and handle
checks report explicit skips elsewhere; a POSIX run never certifies that backend.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.integration


def test_atomic_bytes_identity_and_quarantine(tmp_path: Path) -> None:
    """Preserve exact binary bytes and directory identity through quarantine and deletion.

    Args:
        tmp_path: Disposable directory owned by this test.
    """
    # Resolve the disposable root so native paths use one stable location.
    root = tmp_path.resolve()
    # Hold a validated root directory handle during all file operations.
    with core.Directory.absolute(root) as directory:
        # Create a payload directory and retain its native identity.
        with directory.child("payload", True) as payload:
            # Record the payload identity before replacing its contents.
            identity = payload.identity
            # Publish bytes through the atomic write boundary.
            payload.write("note.md", b"first\r\n\x00\xff")
            # Read back the exact bytes written to the payload.
            assert payload.read("note.md") == b"first\r\n\x00\xff"
            # Publish bytes through the atomic write boundary.
            payload.write("note.md", b"second\n")
            # Read back the exact bytes written to the payload.
            assert payload.read("note.md") == b"second\n"
            assert payload.names() == ["note.md"]
        # Open a separate control directory for quarantine.
        with directory.child("control", True) as control:
            # Quarantine the payload by moving it under the control directory.
            directory.rename_directory("payload", control, "quarantine")
            # Confirm the canonical payload name is absent after the move.
            assert not directory.exists("payload")
            # Inspect the quarantined directory through a new handle.
            with control.child("quarantine") as moved:
                # Confirm the move preserved native identity and final file bytes.
                assert moved.identity == identity
                assert moved.read("note.md") == b"second\n"
                # Delete the validated file before removing its directory.
                moved.unlink("note.md")
            # Remove the now-empty quarantine directory.
            control.rmdir("quarantine")
            # Confirm quarantine cleanup removed its directory entry.
            assert not control.exists("quarantine")


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "x/y",
        "x\\y",
        "x:stream",
        "..",
        ".",
        "",
        "note.md.",
        "note.md ",
        "CON",
        "con.md",
        "aux.md",
        "NUL",
        "COM1.md",
        "LPT9",
        "COM¹",
        "x\0y",
        "x?y",
    ],
)
def test_portable_name_rejection(tmp_path: Path, name: str) -> None:
    """Reject portable aliases before any file, directory or lock side effect.

    Args:
        tmp_path: Disposable directory owned by this test.
        name: Requested file, directory, or control-entry name.
    """
    # Open one validated directory before testing malformed names.
    with core.Directory.absolute(tmp_path.resolve()) as directory:
        # Apply each malformed name to file and child operations.
        for operation in [
            lambda: directory.write(name, b"bad"),
            lambda: directory.child(name, True),
            lambda: directory.read(name),
            lambda: directory.exists(name),
        ]:
            # Require rejection before any file or directory side effect.
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                operation()
        # Apply the same name rule to lock creation.
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"), directory.lock(name):
            pass
        assert directory.names() == []


def test_hardlink_rejection_preserves_other_file(tmp_path: Path) -> None:
    """Refuse hardlinked content for reads, replacement and lock acquisition.

    Args:
        tmp_path: Disposable directory owned by this test.
    """
    # Resolve the disposable root so native paths use one stable location.
    root = tmp_path.resolve()
    # Hold a validated root directory handle during all file operations.
    with core.Directory.absolute(root) as directory:
        # Create safe source bytes before introducing a hard link.
        directory.write("outside.md", b"sentinel")
        os.link(root / "outside.md", root / "linked.md")
        # Attempt both read and replacement through the unsafe hard-link name.
        for operation in [
            lambda: directory.read("linked.md"),
            lambda: directory.write("linked.md", b"bad"),
        ]:
            # Reject both read and write through the hard-link alias.
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                operation()
        # Check lock acquisition also rejects the hard-link alias.
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"), directory.lock("linked.md"):
            pass
    # Confirm the safe source file retained its original bytes.
    assert (root / "outside.md").read_bytes() == b"sentinel"


def test_issue_view_reparse_boundary_and_collision(tmp_path: Path) -> None:
    """Share exact issue bytes without allowing a view to become a canonical directory.

    Args:
        tmp_path: Disposable directory owned by this test.
    """
    # Resolve the disposable root so native paths use one stable location.
    root = tmp_path.resolve()
    # Hold a validated root directory handle during all file operations.
    with core.Directory.absolute(root) as directory:
        # Create the canonical target containing the shared issue bytes.
        with directory.child("target", True) as target:
            target.write("note.md", b"shared")
        # Publish the view twice to check idempotent linking.
        directory.issue_view("TEST-1", str(root / "target"))
        directory.issue_view("TEST-1", str(root / "target"))
        # Read the shared bytes through the published view.
        assert (root / "TEST-1/note.md").read_bytes() == b"shared"
        # Confirm the view uses the native link type for this platform.
        if sys.platform == "win32":
            # On Windows, require a native junction.
            assert (root / "TEST-1").is_junction()
        else:
            # On POSIX, the view must be a symbolic link.
            assert (root / "TEST-1").is_symlink()
        # Reject opening the view as a canonical child directory.
        with pytest.raises((core.WorkspaceError, OSError)):
            directory.child("TEST-1")
        # Reject reopening the view as an absolute canonical root.
        with pytest.raises((core.WorkspaceError, OSError)):
            core.Directory.absolute(root / "TEST-1")
        # Create an unrelated directory to test view collisions.
        with directory.child("foreign", True):
            pass
        # Reject a view whose name or target conflicts with existing state.
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
            directory.issue_view("TEST-1", str(root / "foreign"))
        # Reject a view whose name or target conflicts with existing state.
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
            directory.issue_view("foreign", str(root / "target"))
        # Confirm collision checks did not change canonical target bytes.
        assert (root / "target/note.md").read_bytes() == b"shared"


def test_existing_only_lock_preserves_absence_and_persistent_identity(tmp_path: Path) -> None:
    """Never create a missing lock and reuse an existing lock without changing its identity.

    Args:
        tmp_path: Disposable native filesystem directory.
    """
    # Missing locks remain absent on the actual platform filesystem.
    with core.Directory.absolute(tmp_path.resolve()) as directory:
        with pytest.raises(FileNotFoundError), directory.lock(create=False):
            pytest.fail("entered missing lock")
        assert list(tmp_path.iterdir()) == []
        # A normal core operation establishes the persistent lock before lookup can use it.
        with directory.lock():
            identity = (tmp_path / "lock").stat().st_ino
        with directory.lock(create=False):
            assert (tmp_path / "lock").stat().st_ino == identity
        assert [path.name for path in tmp_path.iterdir()] == ["lock"]


@pytest.mark.parametrize("create", [True, False])
def test_lock_contention_and_process_exit_release(tmp_path: Path, create: bool) -> None:
    """Prove cross-process exclusion, bounded BUSY, crash release and persistent lock identity.

    Args:
        tmp_path: Disposable directory owned by this test.
        create: Whether the contender may create lock storage.
    """
    # Resolve the disposable root so native paths use one stable location.
    root = tmp_path.resolve()
    script = """
import sys
from nplet.lifecycle.task_workspace import Directory
with Directory.absolute(sys.argv[1]) as directory, directory.lock():
    print('locked', flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Hold a lock in the child process and test contention from this process.
    try:
        # Wait until the child confirms its lock is held.
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        # Record the payload identity before replacing its contents.
        identity = (root / "lock").stat().st_ino
        # Hold a validated root directory handle during all file operations.
        with core.Directory.absolute(root) as directory:
            # Require bounded contention to report BUSY.
            with (
                pytest.raises(core.WorkspaceError, match="BUSY"),
                directory.lock(timeout=0.05, create=create),
            ):
                pass
        # Terminate the lock holder without a normal unlock.
        process.kill()
        process.wait(timeout=5)
        # Hold a validated root directory handle during all file operations.
        with core.Directory.absolute(root) as directory, directory.lock(timeout=1):
            assert (root / "lock").stat().st_ino == identity
        # Confirm the persistent lock file survives process death.
        assert (root / "lock").exists()
    finally:
        # Reap the child process even if an assertion fails.
        # Kill a still-running child before waiting for its exit.
        if process.poll() is None:
            # Terminate a child that still holds the lock.
            process.kill()
        # Collect child output and close its pipes.
        process.communicate(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows NTFS handles and ACLs")
def test_windows_case_alias_and_pinned_ancestor(tmp_path: Path) -> None:
    """Reject case-confused replacements and prevent ancestor rename while pinned.

    Args:
        tmp_path: Disposable directory owned by this test.
    """
    # Resolve the disposable root so native paths use one stable location.
    root = tmp_path.resolve()
    # Hold a validated root directory handle during all file operations.
    with core.Directory.absolute(root) as directory:
        # Hold the child directory open to pin its ancestor chain.
        with directory.child("pinned", True) as pinned:
            # Write a safe file under the pinned directory.
            pinned.write("note.md", b"safe")
            # Reject a case-only alias of an existing file.
            with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
                pinned.write("NOTE.md", b"bad")
            # Confirm Windows denies renaming a directory while its handle is pinned.
            with pytest.raises(OSError):  # noqa: PT011 - Windows denial varies; released rename below must succeed.
                (root / "pinned").rename(root / "moved")
            # Keep a nested handle open while testing ancestor replacement.
            with pinned.child("nested", True) as nested:
                # Write through the nested handle before attempting replacement.
                nested.write("inside.md", b"contained")
                # Confirm Windows denies renaming a directory while its handle is pinned.
                with pytest.raises(OSError):  # noqa: PT011 - Pinned ancestor and content are checked below.
                    (root / "pinned").rename(root / "substituted")
                # Confirm the nested handle still reads the original directory.
                assert nested.read("inside.md") == b"contained"
            # Confirm the pinned parent retained the safe file.
            assert pinned.read("note.md") == b"safe"
        # Rename succeeds only after the pinned handles close.
        (root / "pinned").rename(root / "moved")
        # Confirm the released directory retained its file bytes.
        assert (root / "moved/note.md").read_bytes() == b"safe"


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows security descriptors")
def test_windows_foreign_write_acl_is_rejected(tmp_path: Path) -> None:
    """Reject a user-owned file after granting Everyone write permission, preserving bytes.

    Args:
        tmp_path: Disposable directory owned by this test.
    """
    # Resolve the disposable root so native paths use one stable location.
    root = tmp_path.resolve()
    # Hold a validated root directory handle during all file operations.
    with core.Directory.absolute(root) as directory:
        # Create safe source bytes before introducing a hard link.
        directory.write("note.md", b"safe")
        subprocess.run(
            ["icacls", str(root / "note.md"), "/grant", "*S-1-1-0:(W)"],
            check=True,
            capture_output=True,
        )
        # Reject reads after the file gains a foreign write grant.
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
            directory.read("note.md")
        # Reject replacement through the same unsafe ACL.
        with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
            directory.write("note.md", b"bad")
        # Confirm the unsafe ACL did not permit changing the file bytes.
        assert (root / "note.md").read_bytes() == b"safe"


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows path namespaces")
@pytest.mark.parametrize(
    "path",
    [
        "C:relative",
        "\\\\server\\share\\store",
        "\\\\?\\C:\\store",
        "\\\\.\\pipe\\store",
        "C:\\store:stream",
        "C:\\..\\store",
    ],
)
def test_windows_noncanonical_roots_are_rejected(path: str) -> None:
    """Reject network/device/drive-relative/traversal roots before store access.

    Args:
        path: Filesystem path passed to the operation.
    """
    # Reject the noncanonical root before store access.
    with pytest.raises(core.WorkspaceError, match="UNSAFE_PATH"):
        core.Directory.absolute(path)


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows cross-process sharing")
def test_windows_competing_process_cannot_redirect_held_ancestor(tmp_path: Path) -> None:
    """Fence leaf/ancestor replacement and keep writes away from an outside junction target.

    Args:
        tmp_path: Disposable directory owned by this test.
    """
    # Resolve the disposable root so native paths use one stable location.
    root = tmp_path.resolve()
    ancestor, outside = root / "ancestor", root / "outside"
    # Hold a validated root directory handle during all file operations.
    with core.Directory.absolute(root) as directory:
        # Create the ancestor and leaf that the held handle will pin.
        with directory.child("ancestor", True) as parent, parent.child("leaf", True) as leaf:
            leaf.write("note.md", b"original")
        # Seed an outside tree with sentinel bytes to detect redirection.
        with directory.child("outside", True) as target, target.child("leaf", True) as target_leaf:
            target_leaf.write("note.md", b"outside sentinel")
        # Only the leaf handle remains: its own retained chain must pin the ancestor.
        with core.Directory.absolute(ancestor / "leaf") as leaf:
            # Record leaf identity and run a competing process that attempts substitution.
            identity = leaf.identity
            script = """
import json
import sys
import subprocess
from pathlib import Path
from tests.platform_support import link_directory
ancestor, outside = map(Path, sys.argv[1:])
result = {}
for label, source in [('leaf', ancestor / 'leaf'), ('ancestor', ancestor)]:
    moved = source.with_name(source.name + '-moved')
    try:
        source.rename(moved)
    except OSError:
        result[label + '_rename_denied'] = True
    else:
        result[label + '_rename_denied'] = False
        if label == 'leaf':
            moved.rename(source)
try:
    link_directory(ancestor, outside)
except (OSError, subprocess.CalledProcessError):
    result['junction_denied'] = True
else:
    result['junction_denied'] = False
print(json.dumps(result))
"""
            observed = subprocess.run(
                [sys.executable, "-c", script, str(ancestor), str(outside)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            result = json.loads(observed.stdout)
            # Attempt a normal write after the competing substitution, then check both trees.
            try:
                # Attempt a write through the held leaf after competing replacement.
                leaf.write("note.md", b"updated original")
            except (core.WorkspaceError, OSError):
                # Record an unexpected rejection without losing sentinel checks.
                result["write_succeeded"] = False
            else:
                # Record that the held-leaf write succeeded.
                result["write_succeeded"] = True
            # Confirm the outside sentinel is untouched and all substitutions failed.
            assert (outside / "leaf/note.md").read_bytes() == b"outside sentinel"
            assert sorted(p.name for p in (outside / "leaf").iterdir()) == ["note.md"]
            assert result == {
                "leaf_rename_denied": True,
                "ancestor_rename_denied": True,
                "junction_denied": True,
                "write_succeeded": True,
            }
            # Reopen the original leaf to verify the held handle stayed on it.
            with core.Directory.absolute(ancestor / "leaf") as reopened:
                # Confirm the move preserved native identity and final file bytes.
                assert reopened.identity == identity
                assert reopened.read("note.md") == b"updated original"
        # Release the leaf handle, then rename the ancestor normally.
        ancestor.rename(root / "released")
        # Confirm the released tree contains the updated original bytes.
        assert (root / "released/leaf/note.md").read_bytes() == b"updated original"
