"""Load the user's vault exemption policy and match it against repository location evidence.

The loader reads one user-owned JSON policy outside every repository through the
no-follow filesystem boundary and reduces it to repository keys or a bounded error
code. It never writes, never caches between calls and never decides admission.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any, NamedTuple

from nplet.adapters import repository_location
from nplet.lifecycle import task_workspace as core

# Reserved configuration target for the admission policy consumer; reading never
# authorizes editing it.
POLICY = Path.home() / ".config" / "nplet" / "repository-exemptions.json"
MAX_BYTES, MAX_VAULTS, MAX_PATH = 64 * 1024, 256, 4096


class Exemptions(NamedTuple):
    """The exempted vault repositories, or why the policy could not be used.

    Attributes:
        vaults: ``Identity.key()`` values of the exempted repositories.
        error: Bounded diagnostic code; ``vaults`` is empty when set.
    """

    vaults: frozenset[tuple[str, tuple[int, int]]]
    error: str | None


def load(policy: Path = POLICY, memo: repository_location.Memo | None = None) -> Exemptions:
    """Read and validate the exemption policy afresh on every call.

    Args:
        policy: Absolute policy file path.
        memo: Invocation-local discovery results; a fresh memo when None.

    Returns:
        The exempted repository keys, none when the policy or its directory is
        missing, or no keys and the bounded code of the first failure.
    """
    # Read through the no-follow boundary; a missing policy exempts nothing.
    try:
        with core.Directory.absolute(policy.parent) as directory:
            data = directory.read(policy.name, MAX_BYTES)
    except FileNotFoundError:
        return Exemptions(frozenset(), None)
    except core.WorkspaceError as error:
        return Exemptions(frozenset(), error.code)
    # A linked component or file fails a no-follow open with ELOOP or ENOTDIR.
    except OSError as error:
        unsafe = error.errno in (errno.ELOOP, errno.ENOTDIR)
        return Exemptions(frozenset(), "UNSAFE_PATH" if unsafe else "UNREADABLE")
    except ValueError:
        return Exemptions(frozenset(), "UNREADABLE")
    # A policy inside a repository could be changed by the agents it exempts.
    repository, error_code = repository_location.discover(
        policy.parent, memo if memo is not None else {}
    )
    if repository is not None:
        return Exemptions(frozenset(), "IN_REPOSITORY")
    if error_code is not None:
        return Exemptions(frozenset(), error_code)
    # Decode strict JSON, rejecting duplicate keys and non-finite numbers.
    try:
        value = core.strict_json(data.decode("utf-8"))
    except (UnicodeDecodeError, core.WorkspaceError):
        return Exemptions(frozenset(), "INVALID_CONFIG")
    # Validate the document shape and build the repository keys.
    try:
        return Exemptions(vaults(value), None)
    except core.WorkspaceError as error:
        return Exemptions(frozenset(), error.code)


def vaults(value: Any) -> frozenset[tuple[str, tuple[int, int]]]:
    """Validate one decoded policy document and extract its repository keys.

    Args:
        value: Decoded JSON policy document.

    Returns:
        One ``(common_git_dir, (device, inode))`` key per listed vault.

    Raises:
        WorkspaceError: ``INVALID_CONFIG`` for any shape or value violation, or
            ``DUPLICATE_VAULT`` when a path or identity is listed twice.
    """
    # The document holds exactly schema version 1 and a bounded vault list.
    core.require(
        isinstance(value, dict)
        and value.keys() == {"schema_version", "vaults"}
        and type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and isinstance(value["vaults"], list)
        and len(value["vaults"]) <= MAX_VAULTS,
        "INVALID_CONFIG",
    )
    # Each entry names an absolute normalized common Git directory and its identity.
    keys = []
    for entry in value["vaults"]:
        core.require(
            isinstance(entry, dict) and entry.keys() == {"common_git_dir", "common_identity"},
            "INVALID_CONFIG",
        )
        path, identity = entry["common_git_dir"], entry["common_identity"]
        core.require(
            isinstance(path, str)
            and "\x00" not in path
            and len(path) <= MAX_PATH
            and os.path.isabs(path)
            and path == os.path.normpath(path)
            and isinstance(identity, list)
            and len(identity) == 2
            and all(type(number) is int and number >= 0 for number in identity),
            "INVALID_CONFIG",
        )
        keys.append((path, (identity[0], identity[1])))
    # A path or identity listed twice is ambiguous rather than redundant.
    core.require(
        len({path for path, _ in keys}) == len({identity for _, identity in keys}) == len(keys),
        "DUPLICATE_VAULT",
    )
    return frozenset(keys)


def exempt(location: repository_location.Location, exemptions: Exemptions) -> list[bool]:
    """Flag each discovery point whose repository is an exempted vault.

    Args:
        location: Location evidence for one target, left unchanged.
        exemptions: Loaded exemption policy.

    Returns:
        One flag per point in order; a point without a repository is never exempt.
    """
    return [
        point.repository is not None and point.repository.key() in exemptions.vaults
        for point in location.points
    ]
