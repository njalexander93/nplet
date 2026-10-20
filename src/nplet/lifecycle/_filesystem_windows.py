"""Local NTFS boundary using pinned handles, explicit ACLs and kernel file locks.

CreateFileW OPEN_REPARSE_POINT validates entries themselves. Ancestor handles omit
FILE_SHARE_DELETE, keeping path-based child operations anchored until close. This
is a cooperative local store, not protection from malicious same-user processes.

References: Microsoft Learn CreateFileW, GetSecurityInfo, LockFileEx, MoveFileExW,
FSCTL_GET/SET_REPARSE_POINT and REPARSE_DATA_BUFFER. File bytes are flushed before
write-through same-volume renames. Windows has no unprivileged directory-fsync
contract equivalent to POSIX; process-crash recovery is not a power-loss guarantee.
"""

from __future__ import annotations

import ctypes as c
import msvcrt
import os
import re
import struct
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import Path
from typing import Any, Self

from nplet.lifecycle._errors import require
from nplet.lifecycle._paths import entry_name

assert sys.platform == "win32"

kernel = c.WinDLL("kernel32", use_last_error=True)
advapi = c.WinDLL("advapi32", use_last_error=True)
P = c.c_void_p
INVALID_HANDLE = P(-1).value
READ = 0x80000000
WRITE = 0x40000000
READ_CONTROL = 0x00020000
LIST_DIRECTORY = 0x00000001
REPARSE = 0x400
DIRECTORY = 0x10
MOUNT_POINT = 0xA0000003


def api(library: Any, name: str, result: Any, *arguments: Any) -> Any:
    """Declare exact native widths, particularly pointers on 64-bit Windows."""
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


CreateFile = api(
    kernel, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, P, w.DWORD, w.DWORD, w.HANDLE
)
CloseHandle = api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
GetFileInformation = api(kernel, "GetFileInformationByHandle", w.BOOL, w.HANDLE, P)
GetFileType = api(kernel, "GetFileType", w.DWORD, w.HANDLE)
GetFinalPath = api(
    kernel, "GetFinalPathNameByHandleW", w.DWORD, w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD
)
GetVolumeInformation = api(
    kernel, "GetVolumeInformationW", w.BOOL, w.LPCWSTR, P, w.DWORD, P, P, P, w.LPWSTR, w.DWORD
)
GetDriveType = api(kernel, "GetDriveTypeW", w.UINT, w.LPCWSTR)
CreateDirectory = api(kernel, "CreateDirectoryW", w.BOOL, w.LPCWSTR, P)
MoveFile = api(kernel, "MoveFileExW", w.BOOL, w.LPCWSTR, w.LPCWSTR, w.DWORD)
DeleteFile = api(kernel, "DeleteFileW", w.BOOL, w.LPCWSTR)
RemoveDirectory = api(kernel, "RemoveDirectoryW", w.BOOL, w.LPCWSTR)
LockFile = api(kernel, "LockFileEx", w.BOOL, w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, P)
DeviceIoControl = api(
    kernel, "DeviceIoControl", w.BOOL, w.HANDLE, w.DWORD, P, w.DWORD, P, w.DWORD, P, P
)
LocalFree = api(kernel, "LocalFree", P, P)
GetCurrentProcess = api(kernel, "GetCurrentProcess", w.HANDLE)
OpenProcessToken = api(advapi, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, P)
GetTokenInformation = api(advapi, "GetTokenInformation", w.BOOL, w.HANDLE, c.c_int, P, w.DWORD, P)
SidToString = api(advapi, "ConvertSidToStringSidW", w.BOOL, P, P)
GetSecurityInfo = api(advapi, "GetSecurityInfo", w.DWORD, w.HANDLE, c.c_int, w.DWORD, P, P, P, P, P)
GetAce = api(advapi, "GetAce", w.BOOL, P, w.DWORD, P)
ConvertSecurity = api(
    advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL, w.LPCWSTR, w.DWORD, P, P
)


class FileInformation(c.Structure):
    """Mirror BY_HANDLE_FILE_INFORMATION without architecture-dependent guesses."""

    _fields_ = [
        ("attributes", w.DWORD),
        ("created", w.FILETIME),
        ("accessed", w.FILETIME),
        ("modified", w.FILETIME),
        ("volume", w.DWORD),
        ("size_high", w.DWORD),
        ("size_low", w.DWORD),
        ("links", w.DWORD),
        ("index_high", w.DWORD),
        ("index_low", w.DWORD),
    ]


class SecurityAttributes(c.Structure):
    """Mirror SECURITY_ATTRIBUTES for private file/directory creation."""

    _fields_ = [("length", w.DWORD), ("descriptor", P), ("inherit", w.BOOL)]


class Overlapped(c.Structure):
    """Provide a zero-offset synchronous LockFileEx request on either pointer width."""

    _fields_ = [
        ("internal", c.c_size_t),
        ("internal_high", c.c_size_t),
        ("offset", w.DWORD),
        ("offset_high", w.DWORD),
        ("event", w.HANDLE),
    ]


def checked(result: Any) -> None:
    """Raise the real Windows error rather than hiding unsupported operations."""
    if not result:
        raise c.WinError(c.get_last_error())


def extended(path: Path) -> str:
    """Use the extended local namespace only after caller path validation."""
    return "\\\\?\\" + str(path)


def sid_text(sid: Any) -> str:
    """Copy a SID's stable string representation and release native allocation."""
    value = P()
    checked(SidToString(sid, c.byref(value)))
    try:
        return c.wstring_at(value)
    finally:
        LocalFree(value)


def token_sid(information_class: int = 1) -> str:
    """Read TokenUser (1) or TokenOwner (4), never an environment username."""
    token = w.HANDLE()
    checked(OpenProcessToken(GetCurrentProcess(), 0x0008, c.byref(token)))
    try:
        size = w.DWORD()
        GetTokenInformation(token, information_class, None, 0, c.byref(size))
        buffer = c.create_string_buffer(size.value)
        checked(GetTokenInformation(token, information_class, buffer, size, c.byref(size)))
        return sid_text(c.cast(buffer, c.POINTER(P))[0])
    finally:
        checked(CloseHandle(token))


@contextmanager
def private_security() -> Iterator[SecurityAttributes]:
    """Create with a protected inheritable DACL for user, SYSTEM and administrators."""
    user = token_sid()
    descriptor = P()
    sddl = f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    checked(ConvertSecurity(sddl, 1, c.byref(descriptor), None))
    try:
        yield SecurityAttributes(c.sizeof(SecurityAttributes), descriptor, False)
    finally:
        LocalFree(descriptor)


def validate_security(handle: int, private: bool) -> None:
    """Require token ownership; reject foreign write grants in private stores."""
    owner, dacl, descriptor = P(), P(), P()
    result = GetSecurityInfo(
        handle, 1, 0x5, c.byref(owner), None, c.byref(dacl), None, c.byref(descriptor)
    )
    if result:
        raise c.WinError(result)
    try:
        user = token_sid()
        # TOKEN_OWNER is the documented default owner for this process's creations.
        # Elevated Windows processes may default to Administrators, not TokenUser.
        # This is token-derived ownership, not a blanket acceptance of admin-owned files.
        require(sid_text(owner) in {user, token_sid(4)}, "UNSAFE_PATH")
        if not private:
            return
        require(bool(dacl.value), "UNSAFE_PATH")
        assert dacl.value is not None  # A NULL DACL grants everyone full access.
        count = c.c_ushort.from_address(dacl.value + 4).value
        trusted = {user, "S-1-5-18", "S-1-5-32-544"}
        # Specific write/delete/owner/DACL rights and generic write/all; read/execute are fine.
        write_mask = 0x40000000 | 0x10000000 | 0x000D0156
        for index in range(count):
            ace = P()
            checked(GetAce(dacl, index, c.byref(ace)))
            header = c.string_at(ace, 8)
            kind, flags, _, mask = struct.unpack("<BBHI", header)
            if flags & 0x08:  # INHERIT_ONLY_ACE does not grant access to this object.
                continue
            require(kind in {0, 1}, "UNSAFE_PATH")  # Unknown/conditional grants fail closed.
            if kind == 0 and mask & write_mask:
                require(sid_text(P((ace.value or 0) + 8)) in trusted, "UNSAFE_PATH")
    finally:
        LocalFree(descriptor)


def information(handle: int, *, directory: bool, reparse: bool = False) -> FileInformation:
    """Check native object type, reparse status, link count and stable identity."""
    info = FileInformation()
    checked(GetFileInformation(handle, c.byref(info)))
    require(GetFileType(handle) == 1, "UNSAFE_PATH")  # FILE_TYPE_DISK
    require(bool(info.attributes & DIRECTORY) == directory, "UNSAFE_PATH")
    require(reparse or not info.attributes & REPARSE, "UNSAFE_PATH")
    require(directory or info.links == 1, "UNSAFE_PATH")
    require(bool(info.index_high or info.index_low), "UNSAFE_PATH")
    return info


def open_handle(
    path: Path,
    access: int = READ_CONTROL | LIST_DIRECTORY,
    disposition: int = 3,
    security: SecurityAttributes | None = None,
) -> int:
    """Open the entry itself with sharing-enforced access, retaining its path boundary.

    READ_CONTROL alone is metadata access and does not participate in sharing
    checks. Directory pins also need FILE_LIST_DIRECTORY (FILE_READ_DATA).
    See MS-FSA 2.1.5.1.2.2: https://learn.microsoft.com/en-us/openspecs/
    windows_protocols/ms-fsa/8c0e3f4f-0729-49f4-a14d-7f7add593819 .
    """
    handle = CreateFile(
        extended(path),
        access,
        0x3,
        c.byref(security) if security is not None else None,
        disposition,
        0x02200000,
        None,
    )
    if handle == INVALID_HANDLE:
        raise c.WinError(c.get_last_error())
    return int(handle)


def volume(path: Path) -> None:
    """Accept local fixed NTFS volumes with persistent ACL and reparse support only."""
    require(re.fullmatch(r"[A-Za-z]:", path.drive) and path.is_absolute(), "UNSAFE_PATH")
    root = path.anchor
    require(GetDriveType(root) == 3, "HOST_UNSUPPORTED_FILESYSTEM")
    name, flags = c.create_unicode_buffer(64), w.DWORD()
    checked(GetVolumeInformation(root, None, 0, None, None, c.byref(flags), name, len(name)))
    require(name.value == "NTFS" and flags.value & 0x88 == 0x88, "HOST_UNSUPPORTED_FILESYSTEM")


def exact_handle_path(handle: int, path: Path) -> None:
    """Reject DOS short-name and device aliases to a different canonical path spelling."""
    buffer = c.create_unicode_buffer(32768)
    length = GetFinalPath(handle, buffer, len(buffer), 0)
    checked(length)
    require(
        length < len(buffer) and buffer.value.casefold() == extended(path).casefold(), "UNSAFE_PATH"
    )


class Directory:
    """Pin every canonical ancestor; expose only checked direct-child operations."""

    def __init__(self, path: Path, handles: list[int], private: bool = False) -> None:
        """Take ownership of a fully opened no-reparse chain, including failure cleanup."""
        self.path, self._handles = path, handles
        try:
            info = information(handles[-1], directory=True)
            validate_security(handles[-1], private)
            self.identity = [int(info.volume), (int(info.index_high) << 32) | int(info.index_low)]
        except BaseException:
            self.close()
            raise

    @classmethod
    def absolute(cls, path: str | Path) -> Self:
        """Open each ancestor without following any reparse point or permitting rename."""
        path = Path(path)
        volume(path)
        for name in path.parts[1:]:
            entry_name(name)
        handles: list[int] = []
        current = Path(path.anchor)
        try:
            for component in [None, *path.parts[1:]]:
                if component is not None:
                    current /= component
                handle = open_handle(current)
                handles.append(handle)
                information(handle, directory=True)
                exact_handle_path(handle, current)
        except BaseException:
            for handle in reversed(handles):
                CloseHandle(handle)
            raise
        return cls(path, handles)

    def close(self) -> None:
        """Release retained ancestors in reverse order; permit safe repeated cleanup."""
        handles, self._handles = self._handles, []
        for handle in reversed(handles):
            checked(CloseHandle(handle))

    def __enter__(self) -> Self:
        """Return the pinned directory for context-managed access."""
        return self

    def __exit__(self, *args: object) -> None:
        """Release the directory chain when leaving its context."""
        self.close()

    def _entry(self, name: str) -> Path:
        """Reject case aliases before reading, replacing or creating a direct entry."""
        require(bool(self._handles), "UNSAFE_PATH")
        entry_name(name)
        for existing in self.names():
            require(existing == name or existing.casefold() != name.casefold(), "UNSAFE_PATH")
        return self.path / name

    def names(self) -> list[str]:
        """Enumerate one pinned canonical directory without traversing its entries."""
        require(bool(self._handles), "UNSAFE_PATH")
        return os.listdir(extended(self.path))

    def exists(self, name: str) -> bool:
        """Check an entry itself, including dangling junctions, without following it."""
        path = self._entry(name)
        try:
            os.lstat(extended(path))
            return True
        except FileNotFoundError:
            return False

    def child(self, name: str, create: bool = False, private: bool = True) -> Self:
        """Open or create a private real directory; junctions remain forbidden here."""
        path = self._entry(name)
        if create:
            with private_security() as security:
                if not CreateDirectory(extended(path), c.byref(security)):
                    error = c.get_last_error()
                    if error != 183:
                        raise c.WinError(error)
        result = type(self).absolute(path)
        try:
            validate_security(result._handles[-1], private)
        except BaseException:
            result.close()
            raise
        return result

    def _file(self, name: str, access: int = READ, create: bool = False) -> int:
        """Open a single-link owned ordinary file, optionally with exclusive creation."""
        path = self._entry(name)
        with private_security() as security:
            handle = open_handle(path, access | READ_CONTROL, 1 if create else 3, security)
        try:
            information(handle, directory=False)
            exact_handle_path(handle, path)
            validate_security(handle, True)
            return handle
        except BaseException:
            CloseHandle(handle)
            raise

    def read(self, name: str, limit: int = 96 * 1024 * 1024) -> bytes:
        """Read bounded bytes through the validated native handle, never reopening a path."""
        handle = self._file(name)
        try:
            info = information(handle, directory=False)
            require((int(info.size_high) << 32) | int(info.size_low) <= limit, "SIZE_LIMIT")
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            CloseHandle(handle)
            raise
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(limit + 1)
        require(len(data) <= limit, "SIZE_LIMIT")
        return data

    def write(self, name: str, data: bytes) -> None:
        """Flush a private temporary file and publish by same-volume write-through rename."""
        path = self._entry(name)
        if self.exists(name):
            self.read(name)
        temp = ".write-" + uuid.uuid4().hex
        handle = self._file(temp, WRITE, True)
        try:
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
            except BaseException:
                CloseHandle(handle)
                raise
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            checked(MoveFile(extended(self.path / temp), extended(path), 0x9))
        finally:
            if self.exists(temp):
                self.unlink(temp)

    def flush(self) -> None:
        """Mark the process-crash ordering boundary; writes already flush before publication.

        Win32 has no unprivileged directory-fsync equivalent. Do not emulate it by
        swallowing FlushFileBuffers failures or claim stronger power-loss durability.
        """
        require(bool(self._handles), "UNSAFE_PATH")

    def unlink(self, name: str) -> None:
        """Remove only a validated regular file; never traverse or delete a junction target."""
        handle = self._file(name)
        checked(CloseHandle(handle))
        checked(DeleteFile(extended(self._entry(name))))

    def rmdir(self, name: str) -> None:
        """Remove a checked empty real child directory after releasing its pin."""
        with self.child(name, private=False):
            pass
        checked(RemoveDirectory(extended(self._entry(name))))

    def rename_directory(self, name: str, destination: Directory, new_name: str) -> None:
        """Move only a real child into an absent same-volume quarantine."""
        source, target = self._entry(name), destination._entry(new_name)
        require(not destination.exists(new_name), "RECOVERY_REQUIRED")
        with self.child(name, private=False) as child:
            require(child.identity[0] == destination.identity[0], "UNSAFE_PATH")
        checked(MoveFile(extended(source), extended(target), 0x8))

    @contextmanager
    def lock(
        self, name: str = "lock", timeout: float = 1.0, *, create: bool = True
    ) -> Iterator[None]:
        """Hold a persistent nondeletable lock inode with a bounded kernel lock wait.

        Args:
            name: Direct lock entry under this validated directory.
            timeout: Maximum seconds to wait for the existing kernel lock.
            create: Permit creation of a missing lock; false opens only existing storage.

        Returns:
            A context manager retaining the locked native handle.

        Raises:
            WorkspaceError: If the lock is unsafe or its bounded wait expires.
            OSError: If existing-lock access or native locking fails.
        """
        # A completion lookup must not create missing persistent lock storage.
        if not create:
            handle = self._file(name, READ | WRITE)
        else:
            try:
                handle = self._file(name, READ | WRITE, True)
            except FileExistsError:
                handle = self._file(name, READ | WRITE)
        try:
            deadline = time.monotonic() + timeout
            overlapped = Overlapped()
            while not LockFile(handle, 0x3, 0, 1, 0, c.byref(overlapped)):
                error = c.get_last_error()
                if error != 33:  # ERROR_LOCK_VIOLATION is the only retryable lock failure.
                    raise c.WinError(error)
                require(
                    time.monotonic() < deadline, "BUSY", "Retry after the current writer finishes."
                )
                time.sleep(0.01)
            yield
        finally:
            checked(CloseHandle(handle))  # Closing releases the byte-range lock, even on exit.

    def issue_view(self, name: str, target: str) -> None:
        """Publish an issue-only mount-point junction; validate its exact reparse payload."""
        path = self._entry(name)
        with type(self).absolute(target):
            pass
        substitute = ("\\??\\" + str(Path(target))).encode("utf-16-le")
        printable = str(Path(target)).encode("utf-16-le")
        paths = substitute + b"\0\0" + printable + b"\0\0"
        body = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printable)) + paths
        expected = struct.pack("<IHH", MOUNT_POINT, len(body), 0) + body
        if not self.exists(name):
            temporary = ".view-" + uuid.uuid4().hex
            with self.child(temporary, create=True):
                pass
            temp_path = self.path / temporary
            try:
                handle = open_handle(temp_path, WRITE | READ_CONTROL)
                try:
                    information(handle, directory=True)
                    returned = w.DWORD()
                    buffer = c.create_string_buffer(expected)
                    checked(
                        DeviceIoControl(
                            handle, 0x900A4, buffer, len(expected), None, 0, c.byref(returned), None
                        )
                    )
                finally:
                    checked(CloseHandle(handle))
                # Never replace an existing directory or foreign view in a creation race.
                if not MoveFile(extended(temp_path), extended(path), 0x8):
                    error = c.get_last_error()
                    if error not in {80, 183}:
                        raise c.WinError(error)
            finally:
                if self.exists(temporary):
                    # RemoveDirectory removes the junction itself, not its target.
                    checked(RemoveDirectory(extended(temp_path)))
        handle = open_handle(path)
        try:
            info = information(handle, directory=True, reparse=True)
            require(bool(info.attributes & REPARSE), "UNSAFE_PATH")
            validate_security(handle, True)
            output, returned = c.create_string_buffer(16384), w.DWORD()
            checked(
                DeviceIoControl(
                    handle, 0x900A8, None, 0, output, len(output), c.byref(returned), None
                )
            )
            require(output.raw[: returned.value] == expected, "UNSAFE_PATH")
        finally:
            checked(CloseHandle(handle))
