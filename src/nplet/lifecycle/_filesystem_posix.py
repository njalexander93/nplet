"""POSIX filesystem boundary: owned no-follow descriptors and persistent flock."""

from __future__ import annotations

import fcntl
import os
import stat
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from nplet.lifecycle._errors import require
from nplet.lifecycle._paths import entry_name

assert sys.platform != "win32"


class Directory:
    """Hold an owned no-follow directory descriptor for bounded filesystem operations."""

    def __init__(self, fd: int, private: bool = False) -> None:
        """Validate and retain an opened directory descriptor and its identity.

        Args:
            fd: Already opened directory file descriptor; owned by this handle.
            private: Whether to reject group/world-writable directory permissions.

        Raises:
            WorkspaceError: If ownership, type or required private permissions are unsafe.
        """
        self.fd = fd
        try:
            s = os.fstat(fd)
            require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.getuid(), "UNSAFE_PATH")
            require(not private or not s.st_mode & 0o022, "UNSAFE_PATH")
            self.identity = [s.st_dev, s.st_ino]
        except BaseException:
            os.close(fd)
            raise

    @classmethod
    def absolute(cls, path: str | Path) -> Self:
        """Open each absolute-path component without following symlinks.

        Args:
            path: Path to validate or access within the stated filesystem boundary.

        Returns:
            A validated Directory whose descriptor the caller must close.

        Raises:
            WorkspaceError: If the path or directory identity is unsafe.
            OSError: If any component cannot be opened.
        """
        # Reject relative traversal before opening the absolute directory chain.
        path = Path(path)
        require(path.is_absolute() and ".." not in path.parts, "UNSAFE_PATH")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            # Open each component without following links; retain only the current descriptor.
            for name in path.parts[1:]:
                nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
        except BaseException:
            os.close(fd)
            raise
        return cls(fd)

    def close(self) -> None:
        """Release this directory descriptor.

        Raises:
            OSError: If the descriptor cannot be closed.
        """
        os.close(self.fd)

    def __enter__(self) -> Self:
        """Expose the directory to a context-managed operation.

        Returns:
            This directory handle.
        """
        return self

    def __exit__(self, *args: object) -> None:
        """Close the descriptor when the directory context ends.

        Args:
            args: Context-manager exception triple, ignored during descriptor cleanup.

        Raises:
            OSError: If descriptor cleanup fails.
        """
        self.close()

    def exists(self, name: str) -> bool:
        """Check entry presence without following a final symlink.

        Args:
            name: Direct entry name relative to the opened directory.

        Returns:
            Whether a directory entry exists.

        Raises:
            OSError: If inspection fails for a reason other than absence.
        """
        entry_name(name)
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def child(self, name: str, create: bool = False, private: bool = True) -> Self:
        """Open or create one direct child directory without following links.

        Args:
            name: Direct entry name relative to the opened directory.
            create: Whether an absent child directory may be created.
            private: Whether to reject group/world-writable directory permissions.

        Returns:
            A validated child Directory owned by the caller.

        Raises:
            WorkspaceError: If the child name or directory is unsafe.
            OSError: If creation or opening fails.
        """
        entry_name(name)
        # Create a private child if absent; validate existing children through the same open below.
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=self.fd)
                os.fsync(self.fd)
            except FileExistsError:
                pass
        return type(self)(
            os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd), private
        )

    def read(self, name: str, limit: int = 96 * 1024 * 1024) -> bytes:
        """Read a bounded owned regular file without following links.

        Args:
            name: Direct entry name relative to the opened directory.
            limit: Maximum allowed file or decoded byte count.

        Returns:
            The complete validated file bytes.

        Raises:
            WorkspaceError: If type, ownership, link count, permissions or size are unsafe.
            OSError: If the entry cannot be opened or read.
        """
        entry_name(name)
        # Open without following links or blocking on a substituted special file.
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            s = os.fstat(fd)
            require(
                stat.S_ISREG(s.st_mode)
                and s.st_nlink == 1
                and s.st_uid == os.getuid()
                and not s.st_mode & 0o022,
                "UNSAFE_PATH",
            )
            require(s.st_size <= limit, "SIZE_LIMIT")
            # Read one extra byte to catch growth beyond the size checked above.
            with os.fdopen(fd, "rb", closefd=False) as stream:
                result = stream.read(limit + 1)
            require(len(result) <= limit, "SIZE_LIMIT")
            return result
        finally:
            os.close(fd)

    def write(self, name: str, data: bytes) -> None:
        """Publish bytes through a flushed private temporary file and atomic replacement.

        Args:
            name: Direct entry name relative to the opened directory.
            data: Exact input bytes to inspect or transform.

        Raises:
            WorkspaceError: If the entry name or existing destination is unsafe.
            OSError: If publication, flushing or cleanup fails.
        """
        entry_name(name)
        # Validate any existing entry before reusing or replacing it.
        if self.exists(name):
            self.read(name)  # Refuse links/special files before replacement.
        # Stage bytes in an exclusively created private file before replacing the destination.
        temp = ".write-" + uuid.uuid4().hex
        fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
            # Publish only flushed bytes, then make the directory update durable.
            os.replace(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            os.close(fd)
            # Remove any unpublished temporary file after closing its descriptor.
            if self.exists(temp):
                os.unlink(temp, dir_fd=self.fd)

    def unlink(self, name: str) -> None:
        """Remove a direct entry and flush the containing directory.

        Args:
            name: Direct entry name relative to the opened directory.

        Raises:
            OSError: If removal or directory flushing fails.
        """
        entry_name(name)
        os.unlink(name, dir_fd=self.fd)
        os.fsync(self.fd)

    @contextmanager
    def lock(
        self, name: str = "lock", timeout: float = 1.0, *, create: bool = True
    ) -> Iterator[None]:
        """Hold a bounded exclusive flock on a persistent safe lock file.

        Args:
            name: Direct entry name relative to the opened directory.
            timeout: Maximum seconds to wait for lock acquisition.
            create: Permit creation of a missing lock; false opens only existing storage.

        Returns:
            A context manager that yields while the issue lock is held.

        Raises:
            WorkspaceError: If the lock is unsafe or its deadline expires.
            OSError: If lock-file access fails.
        """
        entry_name(name)
        # Set the monotonic deadline for bounded lock acquisition.
        end = time.monotonic() + timeout
        # Creating operations retry racing opens; admission lookup cannot publish a lock.
        if create:
            while True:
                try:
                    fd = os.open(
                        name,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=self.fd,
                    )
                    os.fsync(self.fd)
                    break
                except FileExistsError:
                    try:
                        fd = os.open(
                            name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd
                        )
                        break
                    except FileNotFoundError:
                        require(time.monotonic() < end, "BUSY")
        else:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            s = os.fstat(fd)
            require(
                stat.S_ISREG(s.st_mode)
                and s.st_nlink == 1
                and s.st_uid == os.getuid()
                and not s.st_mode & 0o022,
                "UNSAFE_PATH",
            )
            # Set the monotonic deadline for bounded lock acquisition.
            end = time.monotonic() + timeout
            # Retry until success or the bounded deadline is reached.
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    require(
                        time.monotonic() < end, "BUSY", "Retry after the current writer finishes."
                    )
                    time.sleep(0.01)
            yield
        finally:
            os.close(fd)

    def names(self) -> list[str]:
        """List direct entries through the retained directory descriptor."""
        return os.listdir(self.fd)

    def flush(self) -> None:
        """Flush directory metadata after file or namespace publication."""
        os.fsync(self.fd)

    def rmdir(self, name: str) -> None:
        """Remove an empty direct directory without recursive traversal."""
        entry_name(name)
        with self.child(name, private=False):
            pass
        os.rmdir(name, dir_fd=self.fd)
        self.flush()

    def rename_directory(self, name: str, destination: Directory, new_name: str) -> None:
        """Quarantine a validated direct directory on the same filesystem."""
        entry_name(name)
        entry_name(new_name)
        require(not destination.exists(new_name), "RECOVERY_REQUIRED")
        with self.child(name, private=False):
            pass
        os.rename(name, new_name, src_dir_fd=self.fd, dst_dir_fd=destination.fd)
        self.flush()
        destination.flush()

    def issue_view(self, name: str, target: str) -> None:
        """Create or validate one exact issue link, never a whole-store link."""
        entry_name(name)
        if not self.exists(name):
            try:
                os.symlink(target, name, dir_fd=self.fd)
                self.flush()
            except FileExistsError:
                pass
        info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        require(
            stat.S_ISLNK(info.st_mode) and os.readlink(name, dir_fd=self.fd) == target,
            "UNSAFE_PATH",
        )
