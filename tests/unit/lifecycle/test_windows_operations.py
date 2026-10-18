"""Model Windows storage operations with fake kernel handles."""

from __future__ import annotations

import io
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nplet.lifecycle._errors import WorkspaceError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="Windows native CreateFileW and NTFS handle APIs are unavailable on POSIX",
    ),
]


@pytest.fixture
def windows() -> Any:
    """Import the Windows filesystem module for native contract tests.

    Returns:
        Imported Windows filesystem module.
    """
    from nplet.lifecycle import _filesystem_windows

    return _filesystem_windows


def directory(windows: Any) -> Any:
    """Create a fake Windows directory with pinned handle identity.

    Args:
        windows: Windows filesystem module being exercised.

    Returns:
        Fake Windows directory with pinned identity and handles.
    """
    # Allocate a directory without opening a native handle.
    item = windows.Directory.__new__(windows.Directory)
    # Pin the modeled path and handle identity for native checks.
    item.path = Path(r"C:\work")
    item._handles = [101]
    item.identity = [7, 13]
    return item


def test_volume_rejects_remote_drive_before_query(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Volume rejects remote drive before query.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake GetDriveType, GetVolumeInformation calls.
    effects: list[str] = []
    monkeypatch.setattr(windows, "GetDriveType", lambda _root: effects.append("drive") or 4)
    monkeypatch.setattr(
        windows, "GetVolumeInformation", lambda *_args: effects.append("volume") or True
    )
    # Reject the selected unsupported drive or volume response.
    with pytest.raises(WorkspaceError) as captured:
        windows.volume(Path(r"C:\work"))
    # Confirm the rejected operation reports HOST_UNSUPPORTED_FILESYSTEM.
    assert captured.value.code == "HOST_UNSUPPORTED_FILESYSTEM"
    assert effects == ["drive"]


def test_volume_rejects_unc_namespace_before_kernel_query(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A network path never reaches drive probing.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake GetDriveType calls.
    effects: list[str] = []
    monkeypatch.setattr(windows, "GetDriveType", lambda _root: effects.append("drive") or 3)
    # Reject the selected unsupported drive or volume response.
    with pytest.raises(WorkspaceError) as captured:
        windows.volume(Path(r"\\server\share\work"))
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert effects == []


@pytest.mark.parametrize(
    ("name", "flags", "accepted"),
    [("NTFS", 0x88, True), ("FAT32", 0x88, False), ("NTFS", 0x80, False)],
)
def test_volume_requires_ntfs_acl_and_reparse(
    windows: Any, monkeypatch: pytest.MonkeyPatch, name: str, flags: int, accepted: bool
) -> None:
    """Volume requires ntfs acl and reparse.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
        name: Selected filesystem name or parameterized input.
        flags: Native flags selected by this case.
        accepted: Whether the selected volume properties should pass validation.
    """

    def volume_info(
        _root: str,
        _label: Any,
        _label_size: int,
        _serial: Any,
        _max_component: Any,
        flags_out: Any,
        name_out: Any,
        _name_size: int,
    ) -> bool:
        """Populate native file or volume information output fields.

        Args:
            _root: Unused root argument accepted by this fake.
            _label: Unused label argument accepted by this fake.
            _label_size: Unused label size argument accepted by this fake.
            _serial: Unused serial argument accepted by this fake.
            _max_component: Unused max component argument accepted by this fake.
            flags_out: Output pointer for volume capability flags.
            name_out: Output pointer for the volume filesystem name.
            _name_size: Unused name size argument accepted by this fake.

        Returns:
            Success flag after writing volume name and capability flags.
        """
        # Write volume capability flags into the native output pointer.
        windows.c.cast(flags_out, windows.c.POINTER(windows.w.DWORD))[0] = windows.w.DWORD(flags)
        # Report the selected filesystem name through the native output.
        name_out.value = name
        return True

    # Install fake GetDriveType, GetVolumeInformation calls.
    monkeypatch.setattr(windows, "GetDriveType", lambda _root: 3)
    monkeypatch.setattr(windows, "GetVolumeInformation", volume_info)
    # Check the accepted outcome and its alternative.
    if accepted:
        # Accept the supported NTFS flags without raising.
        windows.volume(Path(r"C:\work"))
    else:
        # Handle the alternative to accepted.
        # Reject the selected unsupported drive or volume response.
        with pytest.raises(WorkspaceError) as captured:
            windows.volume(Path(r"C:\work"))
        # Confirm the rejected operation reports HOST_UNSUPPORTED_FILESYSTEM.
        assert captured.value.code == "HOST_UNSUPPORTED_FILESYSTEM"


def test_volume_surfaces_native_information_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed volume query cannot be mistaken for NTFS support.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake GetDriveType, GetVolumeInformation, get_last_error calls.
    monkeypatch.setattr(windows, "GetDriveType", lambda _root: 3)
    monkeypatch.setattr(windows, "GetVolumeInformation", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    # Reject the selected unsupported drive or volume response.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        windows.volume(Path(r"C:\work"))
    assert captured.value.winerror == 5


def test_absolute_pins_each_ancestor_and_releases_after_failure(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absolute pins each ancestor and releases after failure.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake volume, open_handle, information and related calls.
    opened: list[str] = []
    closed: list[int] = []
    monkeypatch.setattr(windows, "volume", lambda _path: None)
    monkeypatch.setattr(
        windows, "open_handle", lambda path: opened.append(str(path)) or len(opened)
    )
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: SimpleNamespace(volume=7, index_high=0, index_low=13),
    )
    monkeypatch.setattr(
        windows,
        "exact_handle_path",
        lambda handle, _path: (
            (_ for _ in ()).throw(WorkspaceError("UNSAFE_PATH")) if handle == 3 else None
        ),
    )
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Reject the unsafe ancestor while releasing opened handles.
    with pytest.raises(WorkspaceError) as captured:
        windows.Directory.absolute(Path(r"C:\work\issue"))
    # Confirm rejection and reverse closure of every pinned ancestor.
    assert captured.value.code == "UNSAFE_PATH"
    assert opened == ["C:\\", r"C:\work", r"C:\work\issue"]
    assert closed == [3, 2, 1]


def test_absolute_retains_chain_and_init_records_identity(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validated ancestor chain remains pinned until explicit close.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake volume, open_handle, information and related calls.
    opened: list[str] = []
    closed: list[int] = []
    monkeypatch.setattr(windows, "volume", lambda _path: None)
    monkeypatch.setattr(
        windows, "open_handle", lambda path: opened.append(str(path)) or len(opened)
    )
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: SimpleNamespace(volume=7, index_high=1, index_low=13),
    )
    monkeypatch.setattr(windows, "exact_handle_path", lambda *_args: None)
    monkeypatch.setattr(windows, "validate_security", lambda *_args: None)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Pin each validated ancestor and retain its native identity.
    result = windows.Directory.absolute(Path(r"C:\work"))
    # Confirm the validated ancestor handles stay pinned until close.
    assert result.identity == [7, (1 << 32) | 13]
    assert result._handles == [1, 2]
    assert opened == ["C:\\", r"C:\work"]
    # Release pinned handles after asserting the open chain.
    result.close()
    # Check recorded closed against the required side effects.
    assert closed == [2, 1]


def test_absolute_rejects_device_component_before_opening(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsafe Windows device name cannot enter the pinned chain.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake volume, open_handle calls.
    opened: list[str] = []
    monkeypatch.setattr(windows, "volume", lambda _path: None)
    monkeypatch.setattr(windows, "open_handle", lambda _path: opened.append("open") or 91)
    # Reject the unsafe ancestor while releasing opened handles.
    with pytest.raises(WorkspaceError) as captured:
        windows.Directory.absolute(Path(r"C:\work\CON"))
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert opened == []


def test_child_create_preserves_existing_and_checks_private_acl(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Child create preserves existing and checks private acl.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake names calls.
    item = directory(windows)
    result = directory(windows)
    checked: list[bool] = []
    monkeypatch.setattr(item, "names", lambda: [])

    @contextmanager
    def security() -> Any:
        """Yield a controlled resource for child create preserves existing and checks private acl.

        Returns:
            Context manager yielding the selected security attributes.
        """
        yield windows.SecurityAttributes()

    # Install fake private_security, CreateDirectory, get_last_error and related calls.
    monkeypatch.setattr(windows, "private_security", security)
    monkeypatch.setattr(windows, "CreateDirectory", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 183)
    monkeypatch.setattr(windows.Directory, "absolute", classmethod(lambda _cls, _path: result))
    monkeypatch.setattr(
        windows, "validate_security", lambda _handle, private: checked.append(private)
    )
    # Confirm creation opens and validates the expected child identity.
    assert item.child("issue", create=True) is result
    assert checked == [True]


def test_child_create_success_opens_and_checks_new_directory(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful private creation still opens and validates the new entry.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Start an event log to check the operation sequence.
    item = directory(windows)
    child = directory(windows)
    events: list[str] = []

    @contextmanager
    def security() -> Any:
        """Yield a controlled resource for child create success opens and checks new directory.

        Returns:
            Context manager yielding the selected security attributes.
        """
        yield windows.SecurityAttributes()

    # Install fake names, private_security, CreateDirectory and related calls.
    monkeypatch.setattr(item, "names", lambda: [])
    monkeypatch.setattr(windows, "private_security", security)
    monkeypatch.setattr(windows, "CreateDirectory", lambda *_args: events.append("create") or True)
    monkeypatch.setattr(
        windows.Directory,
        "absolute",
        classmethod(lambda _cls, _path: events.append("open") or child),
    )
    monkeypatch.setattr(
        windows,
        "validate_security",
        lambda _handle, private: events.append(f"private={private}"),
    )
    monkeypatch.setattr(
        windows.c,
        "get_last_error",
        lambda: (_ for _ in ()).throw(AssertionError("successful creation has no error")),
    )
    # Check recorded events against the required side effects.
    assert item.child("issue", create=True) is child
    assert events == ["create", "open", "private=True"]


def test_file_opens_exclusive_private_handle(windows: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """File opens exclusive private handle.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake names calls.
    item = directory(windows)
    opened: list[tuple[Any, ...]] = []
    monkeypatch.setattr(item, "names", lambda: [])

    @contextmanager
    def security() -> Any:
        """Yield a controlled resource for file opens exclusive private handle.

        Returns:
            Context manager yielding the selected security attributes.
        """
        yield windows.SecurityAttributes()

    # Install fake private_security, open_handle, information and related calls.
    monkeypatch.setattr(windows, "private_security", security)
    monkeypatch.setattr(windows, "open_handle", lambda *args: opened.append(args) or 91)
    monkeypatch.setattr(windows, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(windows, "exact_handle_path", lambda *_args: None)
    monkeypatch.setattr(windows, "validate_security", lambda *_args: None)
    # Confirm the opened native handle resolves to the expected file.
    assert item._file("state.json", windows.WRITE, True) == 91
    assert opened[0][0] == Path(r"C:\work\state.json")
    assert opened[0][1] == windows.WRITE | windows.READ_CONTROL
    assert opened[0][2] == 1


def test_read_validated_bytes_and_rejects_stream_growth(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read validated bytes and rejects stream growth.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake _file, information, open_osfhandle and related calls.
    item = directory(windows)
    monkeypatch.setattr(item, "_file", lambda _name: 91)
    monkeypatch.setattr(
        windows, "information", lambda *_args, **_kwargs: SimpleNamespace(size_high=0, size_low=3)
    )
    monkeypatch.setattr(windows.msvcrt, "open_osfhandle", lambda *_args: 55)
    monkeypatch.setattr(windows.os, "fdopen", lambda *_args: io.BytesIO(b"abc"))
    # Confirm exact bytes are read before stream growth is rejected.
    assert item.read("record", limit=3) == b"abc"
    # Install fake fdopen calls.
    monkeypatch.setattr(windows.os, "fdopen", lambda *_args: io.BytesIO(b"abcd"))
    # Reject unsafe stream size or descriptor transfer.
    with pytest.raises(WorkspaceError) as captured:
        item.read("record", limit=3)
    # Confirm the rejected operation reports SIZE_LIMIT.
    assert captured.value.code == "SIZE_LIMIT"


def test_write_flushes_before_publish_and_cleans_temp(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write flushes before publish and cleans temp.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Start an event log to check the operation sequence.
    item = directory(windows)
    events: list[str] = []
    names: list[str] = []

    class Stream(io.BytesIO):
        """In-memory stream that records native flush and handle behavior."""

        def write(self, data: bytes) -> int:
            """Record or expose stream behavior for Stream.

            Args:
                data: Bytes passed to the simulated write.

            Returns:
                Number of bytes written by the in-memory stream.
            """
            events.append("write")
            return super().write(data)

        def flush(self) -> None:
            """Record or expose stream behavior for Stream."""
            events.append("flush")

        def fileno(self) -> int:
            """Record or expose stream behavior for Stream.

            Returns:
                Fake native file descriptor used for flushing.
            """
            return 55

    # Install fake names, exists, _file and related calls.
    stream = Stream()
    monkeypatch.setattr(item, "names", lambda: [])
    monkeypatch.setattr(item, "exists", lambda name: name.startswith(".write-"))
    monkeypatch.setattr(item, "_file", lambda name, *_args: names.append(name) or 91)
    monkeypatch.setattr(item, "unlink", lambda _name: events.append("cleanup"))
    monkeypatch.setattr(windows.msvcrt, "open_osfhandle", lambda *_args: 55)
    monkeypatch.setattr(windows.os, "fdopen", lambda *_args: stream)
    monkeypatch.setattr(windows.os, "fsync", lambda _fd: events.append("fsync"))
    monkeypatch.setattr(windows, "MoveFile", lambda *_args: events.append("publish") or True)
    # Publish bytes through the private temporary file boundary.
    item.write("state.json", b"payload")
    # Check recorded events against the required side effects.
    assert names[0].startswith(".write-")
    assert events == ["write", "flush", "fsync", "publish", "cleanup"]


def test_rename_rejects_cross_volume_and_moves_only_after_check(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rename rejects cross volume and moves only after check.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake names, exists calls.
    source, target = directory(windows), directory(windows)
    effects: list[str] = []
    monkeypatch.setattr(source, "names", lambda: [])
    monkeypatch.setattr(target, "names", lambda: [])
    monkeypatch.setattr(target, "exists", lambda _name: False)

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        """Yield a controlled resource for rename rejects cross volume and moves only after check.

        Args:
            _name: Unused name argument accepted by this fake.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Fake child directory or issue context for the caller.
        """
        yield SimpleNamespace(identity=[8, 13])

    # Install fake child, MoveFile calls.
    monkeypatch.setattr(source, "child", child)
    monkeypatch.setattr(windows, "MoveFile", lambda *_args: effects.append("move") or True)
    # Reject a cross-volume move before the native rename.
    with pytest.raises(WorkspaceError) as captured:
        source.rename_directory("issue", target, "quarantine")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert effects == []

    @contextmanager
    def same_volume(_name: str, **_kwargs: Any) -> Any:
        """Yield a controlled resource for rename rejects cross volume and moves only after check.

        Args:
            _name: Unused name argument accepted by this fake.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Context manager yielding a directory on the selected volume.
        """
        yield SimpleNamespace(identity=[7, 99])

    # Install fake child calls.
    monkeypatch.setattr(source, "child", same_volume)
    # Move the validated directory after same-volume checks.
    source.rename_directory("issue", target, "quarantine")
    # Check recorded effects against the required side effects.
    assert effects == ["move"]


def test_existing_only_lock_cannot_create_an_absent_file(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Propagate a missing persistent lock without a native creating open.

    Args:
        windows: Native Windows filesystem module.
        monkeypatch: Record the existing-only file open and prohibit kernel mutation.
    """
    item = directory(windows)
    attempts = []

    def missing(_name: str, _access: int, create: bool = False) -> int:
        """Record the sole native open mode and report the missing entry.

        Args:
            _name: Missing direct lock entry.
            _access: Requested lock access rights.
            create: Actual native creation mode.

        Raises:
            FileNotFoundError: No persistent lock exists.
        """
        attempts.append(create)
        raise FileNotFoundError("missing existing lock")

    # Existing-only lookup cannot publish a lock or attempt a kernel lock on an absent file.
    monkeypatch.setattr(item, "_file", missing)
    monkeypatch.setattr(windows, "LockFile", lambda *_args: pytest.fail("locked absent handle"))
    with pytest.raises(FileNotFoundError, match="missing existing lock"), item.lock(create=False):
        pytest.fail("entered absent lock")
    assert attempts == [False]


@pytest.mark.parametrize("create", [True, False])
def test_lock_reopens_persistent_inode_and_retries_contention(
    windows: Any, monkeypatch: pytest.MonkeyPatch, create: bool
) -> None:
    """Lock reopens persistent inode and retries contention.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
        create: Whether the caller permits creation of absent lock storage.
    """
    # Record native handle closes to verify cleanup.
    item = directory(windows)
    attempts: list[bool] = []
    closed: list[int] = []

    def file(_name: str, _access: int, create: bool = False) -> int:
        """Model the native resource state for lock reopens persistent inode and retries contention.

        Args:
            _name: Unused name argument accepted by this fake.
            _access: Unused access argument accepted by this fake.
            create: Whether the native call requests file creation.

        Returns:
            Native handle returned for the selected creation mode.
        """
        attempts.append(create)
        # Check the create outcome and its alternative.
        if create:
            # Race private file creation with an existing entry.
            raise FileExistsError
        return 91

    # Install fake _file, LockFile, get_last_error and related calls.
    outcomes = iter([False, True])
    monkeypatch.setattr(item, "_file", file)
    monkeypatch.setattr(windows, "LockFile", lambda *_args: next(outcomes))
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 33)
    monkeypatch.setattr(windows.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Hold the persistent lock handle while checking contention.
    with item.lock(create=create):
        assert attempts == ([True, False] if create else [False])
    # Check recorded closed against the required side effects.
    assert closed == [91]


def test_lock_surfaces_noncontention_error_and_releases_handle(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only ERROR_LOCK_VIOLATION qualifies for retry.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake _file, LockFile, get_last_error and related calls.
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "_file", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(windows, "LockFile", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Exercise lock surfaces noncontention error and releases handle and capture the expected
    # failure.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        # Hold the persistent handle while probing lock contention.
        with item.lock():
            pass
    # Check recorded closed against the required side effects.
    assert captured.value.winerror == 5
    assert closed == [91]


def test_write_releases_temp_after_failed_publication(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename error removes the private temporary file.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Track the private temporary file removed after failed publication.
    item = directory(windows)
    removed: list[str] = []

    class Stream(io.BytesIO):
        """In-memory stream that records native flush and handle behavior."""

        def fileno(self) -> int:
            """Record or expose stream behavior for Stream.

            Returns:
                Fake native file descriptor used for flushing.
            """
            return 55

    # Install fake names, exists, _file and related calls.
    monkeypatch.setattr(item, "names", lambda: [])
    monkeypatch.setattr(item, "exists", lambda name: name.startswith(".write-"))
    monkeypatch.setattr(item, "_file", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(item, "unlink", lambda name: removed.append(name))
    monkeypatch.setattr(windows.msvcrt, "open_osfhandle", lambda *_args: 55)
    monkeypatch.setattr(windows.os, "fdopen", lambda *_args: Stream())
    monkeypatch.setattr(windows.os, "fsync", lambda _fd: None)
    monkeypatch.setattr(windows, "MoveFile", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    # Reject or clean up the failed private-file publication.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        item.write("state.json", b"payload")
    assert captured.value.winerror == 5
    assert len(removed) == 1 and removed[0].startswith(".write-")


def test_write_closes_native_handle_when_crt_adoption_fails(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed descriptor transfer closes the handle and removes its temp file.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake names, exists, _file and related calls.
    item = directory(windows)
    events: list[str] = []
    monkeypatch.setattr(item, "names", lambda: [])
    monkeypatch.setattr(item, "exists", lambda name: name.startswith(".write-"))
    monkeypatch.setattr(item, "_file", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(item, "unlink", lambda _name: events.append("unlink-temp"))
    monkeypatch.setattr(
        windows.msvcrt,
        "open_osfhandle",
        lambda *_args: (_ for _ in ()).throw(OSError("CRT adoption failed")),
    )
    monkeypatch.setattr(windows, "CloseHandle", lambda _handle: events.append("close") or True)
    monkeypatch.setattr(
        windows,
        "MoveFile",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not publish")),
    )
    # Reject or clean up the failed private-file publication.
    with pytest.raises(OSError, match="CRT adoption failed"):
        item.write("state.json", b"data")
    # Check recorded events against the required side effects.
    assert events == ["close", "unlink-temp"]


def test_write_leaves_no_cleanup_after_successful_publication(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A moved temporary name is absent; cleanup must not unlink the target.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Start an effect log for the required operation order.
    item = directory(windows)
    published: list[bool] = []
    effects: list[str] = []

    class Stream(io.BytesIO):
        """In-memory stream that records native flush and handle behavior."""

        def fileno(self) -> int:
            """Record or expose stream behavior for Stream.

            Returns:
                Fake native file descriptor used for flushing.
            """
            return 55

    # Install fake names, exists, _file and related calls.
    monkeypatch.setattr(item, "names", lambda: [])
    monkeypatch.setattr(
        item, "exists", lambda name: False if name == "state.json" else not published
    )
    monkeypatch.setattr(item, "_file", lambda *_args, **_kwargs: 91)
    monkeypatch.setattr(item, "unlink", lambda _name: effects.append("unlink"))
    monkeypatch.setattr(windows.msvcrt, "open_osfhandle", lambda *_args: 55)
    monkeypatch.setattr(windows.os, "fdopen", lambda *_args: Stream())
    monkeypatch.setattr(windows.os, "fsync", lambda _fd: effects.append("fsync"))
    monkeypatch.setattr(
        windows,
        "MoveFile",
        lambda *_args: published.append(True) or effects.append("publish") or True,
    )
    # Publish bytes through the private temporary file boundary.
    item.write("state.json", b"data")
    # Check recorded effects against the required side effects.
    assert effects == ["fsync", "publish"]


def test_child_create_surfaces_native_denial_without_opening(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denied CreateDirectory never opens an untrusted existing entry.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Record native handle opens to verify no extra access.
    item = directory(windows)
    opened: list[str] = []

    @contextmanager
    def security() -> Any:
        """Yield a controlled resource for child create surfaces native denial without opening.

        Returns:
            Context manager yielding the selected security attributes.
        """
        yield windows.SecurityAttributes()

    # Install fake names, private_security, CreateDirectory and related calls.
    monkeypatch.setattr(item, "names", lambda: [])
    monkeypatch.setattr(windows, "private_security", security)
    monkeypatch.setattr(windows, "CreateDirectory", lambda *_args: False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    monkeypatch.setattr(
        windows.Directory, "absolute", classmethod(lambda _cls, _path: opened.append("open"))
    )
    # Exercise item.child and capture the expected failure.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        item.child("issue", create=True)
    # Check recorded opened against the required side effects.
    assert captured.value.winerror == 5
    assert opened == []


def test_read_closes_handle_when_descriptor_transfer_fails(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed CRT adoption keeps native handle cleanup with the caller.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake _file, information, open_osfhandle and related calls.
    item = directory(windows)
    closed: list[int] = []
    monkeypatch.setattr(item, "_file", lambda _name: 91)
    monkeypatch.setattr(
        windows,
        "information",
        lambda *_args, **_kwargs: SimpleNamespace(size_high=0, size_low=1),
    )
    monkeypatch.setattr(
        windows.msvcrt,
        "open_osfhandle",
        lambda *_args: (_ for _ in ()).throw(OSError("transfer")),
    )
    monkeypatch.setattr(windows, "CloseHandle", lambda handle: closed.append(handle) or True)
    # Reject unsafe stream size or descriptor transfer.
    with pytest.raises(OSError, match="transfer"):
        item.read("record", limit=2)
    # Check recorded closed against the required side effects.
    assert closed == [91]


def test_write_validates_existing_destination_before_temporary_creation(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected existing file prevents any replacement candidate.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake names, exists, read and related calls.
    item = directory(windows)
    created: list[str] = []
    monkeypatch.setattr(item, "names", lambda: [])
    monkeypatch.setattr(item, "exists", lambda _name: True)
    monkeypatch.setattr(
        item,
        "read",
        lambda _name: (_ for _ in ()).throw(WorkspaceError("UNSAFE_PATH")),
    )
    monkeypatch.setattr(item, "_file", lambda name, *_args: created.append(name) or 91)
    # Reject or clean up the failed private-file publication.
    with pytest.raises(WorkspaceError) as captured:
        item.write("state.json", b"new")
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert created == []


def test_unlink_surfaces_delete_denial_after_verified_handle_close(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deletion failure retains the source and reports the native code.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake _file, names, CloseHandle and related calls.
    item = directory(windows)
    events: list[str] = []
    monkeypatch.setattr(item, "_file", lambda _name: 91)
    monkeypatch.setattr(item, "names", lambda: [])
    monkeypatch.setattr(windows, "CloseHandle", lambda _handle: events.append("close") or True)
    monkeypatch.setattr(windows, "DeleteFile", lambda _path: events.append("delete") or False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 5)
    # Surface delete denial after the verified handle closes.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        item.unlink("record")
    # Check recorded events against the required side effects.
    assert captured.value.winerror == 5
    assert events == ["close", "delete"]


def test_rmdir_surfaces_native_denial_after_child_check(
    windows: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checked child is not silently considered removed on denial.

    Args:
        windows: Windows filesystem module being exercised.
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Start an event log to check the operation sequence.
    item = directory(windows)
    events: list[str] = []

    @contextmanager
    def child(_name: str, **_kwargs: Any) -> Any:
        """Yield a controlled resource for rmdir surfaces native denial after child check.

        Args:
            _name: Unused name argument accepted by this fake.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Returns:
            Fake child directory or issue context for the caller.
        """
        events.append("checked")
        yield None

    # Install fake child, names, RemoveDirectory and related calls.
    monkeypatch.setattr(item, "child", child)
    monkeypatch.setattr(item, "names", lambda: [])
    monkeypatch.setattr(windows, "RemoveDirectory", lambda _path: events.append("remove") or False)
    monkeypatch.setattr(windows.c, "get_last_error", lambda: 145)
    # Surface directory removal denial after child validation.
    with pytest.raises(OSError) as captured:  # noqa: PT011 - winerror is asserted.
        item.rmdir("not-empty")
    # Check recorded events against the required side effects.
    assert captured.value.winerror == 145
    assert events == ["checked", "remove"]
