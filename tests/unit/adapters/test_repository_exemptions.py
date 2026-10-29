"""Check the vault exemption policy reader against its documented schema and reader rules.

Expected keys and codes come from the documented contract, never from the loader.
"""

import errno
import json
import os
from pathlib import Path

import pytest

from nplet.adapters import repository_exemptions, repository_location
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit
posix_reader = pytest.mark.skipif(os.name == "nt", reason="POSIX policy reader cases")
ROOT = os.path.abspath(os.sep)
PATH = os.path.join(ROOT, "vault", ".git")
LONG = ROOT + "a" * (4096 - len(ROOT))


def vault(path: object = PATH, identity: object = (1, 2), **extra: object) -> dict:
    """Build one vault entry.

    Args:
        path: ``common_git_dir`` value.
        identity: ``common_identity`` value; tuples become JSON arrays.
        **extra: Additional entry keys.

    Returns:
        The entry as a JSON-ready dictionary.
    """
    return {"common_git_dir": path, "common_identity": identity, **extra}


def doc(*vaults: object, **top: object) -> dict:
    """Build one policy document holding the given vaults.

    Args:
        *vaults: Vault entries.
        **top: Top-level keys replacing or adding to the defaults.

    Returns:
        The document as a JSON-ready dictionary.
    """
    return {"schema_version": 1, "vaults": list(vaults), **top}


def write(tmp_path: Path, data: bytes, name: str = "policy.json") -> Path:
    """Publish policy bytes as a private regular file outside every repository.

    Args:
        tmp_path: Disposable parent.
        data: Exact file bytes.
        name: File name inside the policy directory.

    Returns:
        The policy file path.
    """
    # The platform writer gives the file the private permissions the reader requires.
    directory = tmp_path.resolve() / "cfg"
    directory.mkdir(exist_ok=True)
    with core.Directory.absolute(directory) as opened:
        opened.write(name, data)
    return directory / name


def keys(document: dict) -> frozenset:
    """Derive the expected repository keys directly from a valid document.

    Args:
        document: Valid policy document.

    Returns:
        One ``(path, (device, inode))`` key per entry.
    """
    return frozenset((e["common_git_dir"], tuple(e["common_identity"])) for e in document["vaults"])


VALID = [
    pytest.param(doc(), id="empty"),
    pytest.param(doc(vault(), vault(PATH + "2", [0, 2**63])), id="two_vaults"),
    pytest.param(doc(*(vault(f"{PATH}{i}", [1, i]) for i in range(256))), id="256_vaults"),
    pytest.param(doc(vault(LONG)), id="4096_char_path"),
]
INVALID = {
    "top_level_list": [],
    "missing_vaults": {"schema_version": 1},
    "extra_key": doc(extra=1),
    "version_2": doc(schema_version=2),
    "version_bool": doc(schema_version=True),
    "version_string": doc(schema_version="1"),
    "version_float": doc(schema_version=1.0),
    "vaults_object": doc(vaults={}),
    "257_vaults": doc(*(vault(f"{PATH}{i}", [1, i]) for i in range(257))),
    "entry_list": doc([PATH, [1, 2]]),
    "entry_extra_key": doc(vault(exempt=True)),
    "entry_missing_identity": doc({"common_git_dir": PATH}),
    "path_number": doc(vault(7)),
    "path_relative": doc(vault(os.path.join("vault", ".git"))),
    "path_nul": doc(vault(PATH + "\x00")),
    "path_unnormalized": doc(vault(os.path.join(PATH, "..", ".git"))),
    "path_4097_chars": doc(vault(LONG + "a")),
    "identity_short": doc(vault(identity=[1])),
    "identity_long": doc(vault(identity=[1, 2, 3])),
    "identity_object": doc(vault(identity={"dev": 1, "ino": 2})),
    "identity_bool": doc(vault(identity=[True, 2])),
    "identity_negative": doc(vault(identity=[1, -2])),
    "identity_float": doc(vault(identity=[1, 2.0])),
    "identity_string": doc(vault(identity=["1", 2])),
}
RAW_INVALID = {
    "bad_utf8": b'{"schema_version": 1, "vaults": ["\xff"]}',
    "utf16": '{"schema_version": 1, "vaults": []}'.encode("utf-16"),
    "deep_nesting": b"[" * 30000 + b"]" * 30000,
    "not_json": b'{"schema_version": 1,',
    "duplicate_key": b'{"schema_version": 1, "schema_version": 1, "vaults": []}',
    "nan": b'{"schema_version": NaN, "vaults": []}',
    "infinity": b'{"schema_version": Infinity, "vaults": []}',
}
DUPLICATES = {
    "same_entry": doc(vault(), vault()),
    "same_path": doc(vault(identity=[1, 2]), vault(identity=[1, 3])),
    "same_identity": doc(vault(PATH), vault(PATH + "2")),
}


@pytest.mark.parametrize("document", VALID)
def test_valid_documents_yield_their_keys(tmp_path: Path, document: dict) -> None:
    """Accept valid documents up to the vault and path limits.

    Args:
        tmp_path: Disposable parent.
        document: Valid policy document.
    """
    policy = write(tmp_path, json.dumps(document).encode())
    assert repository_exemptions.load(policy) == (keys(document), None)


@pytest.mark.parametrize(
    ("data", "code"),
    [pytest.param(json.dumps(d).encode(), "INVALID_CONFIG", id=k) for k, d in INVALID.items()]
    + [pytest.param(d, "INVALID_CONFIG", id=k) for k, d in RAW_INVALID.items()]
    + [
        pytest.param(json.dumps(d).encode(), "DUPLICATE_VAULT", id=k) for k, d in DUPLICATES.items()
    ],
)
def test_invalid_documents_exempt_nothing(tmp_path: Path, data: bytes, code: str) -> None:
    """Reject schema, strict JSON and duplicate violations with no exemptions.

    Args:
        tmp_path: Disposable parent.
        data: Exact policy bytes.
        code: Expected bounded code.
    """
    assert repository_exemptions.load(write(tmp_path, data)) == (frozenset(), code)


@pytest.mark.parametrize(("size", "code"), [(64 * 1024, None), (64 * 1024 + 1, "SIZE_LIMIT")])
def test_size_limit_is_64_kib(tmp_path: Path, size: int, code: str | None) -> None:
    """Accept a padded empty policy of exactly 64 KiB and reject one byte more.

    Args:
        tmp_path: Disposable parent.
        size: File size in bytes.
        code: Expected bounded code.
    """
    data = json.dumps(doc()).encode()
    policy = write(tmp_path, data + b" " * (size - len(data)))
    assert repository_exemptions.load(policy) == (frozenset(), code)


@pytest.mark.parametrize("missing", ["file", "directory"])
def test_missing_policy_exempts_nothing_without_error(tmp_path: Path, missing: str) -> None:
    """Treat a missing file or policy directory as an empty policy.

    Args:
        tmp_path: Disposable parent.
        missing: Which path component is absent.
    """
    directory = tmp_path.resolve() / "cfg"
    if missing == "file":
        directory.mkdir()
    assert repository_exemptions.load(directory / "policy.json") == (frozenset(), None)


@posix_reader
@pytest.mark.parametrize(
    "case",
    [
        "symlinked_file",
        "symlinked_directory",
        "group_writable",
        "world_writable",
        "hard_link",
        "fifo",
        "directory",
    ],
)
def test_unsafe_policy_files_are_rejected(tmp_path: Path, case: str) -> None:
    """Reject links, shared permissions, extra links and non-regular files as unsafe.

    Args:
        tmp_path: Disposable parent.
        case: How a valid policy is made unsafe.
    """
    # Start from a valid private policy, then break exactly one reader rule.
    policy = write(tmp_path, json.dumps(doc(vault())).encode())
    other = policy.with_name("other.json")
    if case in ("symlinked_file", "fifo", "directory"):
        policy.rename(other)
    if case == "symlinked_file":
        policy.symlink_to(other)
    elif case == "symlinked_directory":
        policy.parent.with_name("alias").symlink_to(policy.parent, target_is_directory=True)
        policy = policy.parent.with_name("alias") / policy.name
    elif case.endswith("writable"):
        policy.chmod(0o620 if case.startswith("group") else 0o602)
    elif case == "hard_link":
        os.link(policy, other)
    elif case == "fifo":
        os.mkfifo(policy)
    elif case == "directory":
        policy.mkdir()
    assert repository_exemptions.load(policy) == (frozenset(), "UNSAFE_PATH")


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OSError(errno.ELOOP, "loop"), "UNSAFE_PATH"),
        (OSError(errno.ENOTDIR, "not a directory"), "UNSAFE_PATH"),
        (OSError(errno.EIO, "io"), "UNREADABLE"),
    ],
)
def test_filesystem_failures_map_to_bounded_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError, code: str
) -> None:
    """Map linked-component errors to ``UNSAFE_PATH`` and other failures to ``UNREADABLE``.

    Args:
        tmp_path: Disposable parent.
        monkeypatch: Replaces the boundary read.
        error: Raised filesystem failure.
        code: Expected bounded code.
    """
    policy = write(tmp_path, json.dumps(doc(vault())).encode())

    def fail(*_: object) -> bytes:
        """Raise the selected failure from the read.

        Raises:
            OSError: Always, the selected failure.
        """
        raise error

    monkeypatch.setattr(core.Directory, "read", fail)
    assert repository_exemptions.load(policy) == (frozenset(), code)


def test_unencodable_path_is_rejected(tmp_path: Path) -> None:
    """Report a NUL path component as ``UNREADABLE``, or ``UNSAFE_PATH`` on Windows.

    Args:
        tmp_path: Disposable parent.
    """
    # Windows validates entry names before opening; POSIX fails the encoding.
    policy = tmp_path.resolve() / "c\x00fg" / "policy.json"
    code = "UNSAFE_PATH" if os.name == "nt" else "UNREADABLE"
    assert repository_exemptions.load(policy) == (frozenset(), code)


@pytest.mark.parametrize(
    ("found", "code"),
    [
        ((object(), None), "IN_REPOSITORY"),
        *(((None, c), c) for c in ("REPOSITORY_MISMATCH", "HOST_UNSUPPORTED", "GIT_TIMEOUT")),
    ],
)
def test_policy_directory_discovery_failures_exempt_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, found: tuple, code: str
) -> None:
    """Reject a policy in a repository and pass discovery failures through.

    Args:
        tmp_path: Disposable parent.
        monkeypatch: Replaces repository discovery.
        found: Discovery result for the policy directory.
        code: Expected bounded code.
    """
    policy = write(tmp_path, json.dumps(doc(vault())).encode())
    seen: list[Path] = []
    monkeypatch.setattr(
        repository_location, "discover", lambda path, memo: seen.append(path) or found
    )
    assert repository_exemptions.load(policy, {}) == (frozenset(), code)
    assert seen == [policy.parent]
