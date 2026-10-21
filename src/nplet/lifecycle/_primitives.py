"""Pure lifecycle encoding, identity and revision contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from nplet.lifecycle._errors import require

JSONObject = dict[str, Any]


def canonical(value: object) -> bytes:
    """Serialize a value into the deterministic UTF-8 form used by digests.

    Args:
        value: JSON-serializable value whose deterministic representation is required.

    Returns:
        Canonical JSON bytes.

    Raises:
        ValueError: If a value cannot be represented as finite JSON.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha(data: bytes) -> str:
    """Identify exact bytes with a hexadecimal SHA-256 digest.

    Args:
        data: Exact bytes whose content identity is required.

    Returns:
        The lowercase 64-character digest.
    """
    return hashlib.sha256(data).hexdigest()


def token(value: str) -> str:
    """Validate a bounded printable ASCII identity token.

    Args:
        value: Identity token received from a request or persisted record.

    Returns:
        The validated token.

    Raises:
        WorkspaceError: If the token is empty, oversized or contains unsafe characters.
    """
    # Reject empty, oversized or non-printable identity tokens before returning them.
    require(
        isinstance(value, str) and 0 < len(value) <= 160 and all(32 < ord(c) < 127 for c in value)
    )
    return value


def participant_key(request: JSONObject) -> str:
    """Derive a stable participant key from explicit host and session tokens.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The SHA-256 identity key.

    Raises:
        WorkspaceError: If either identity token is invalid.
    """
    return sha(canonical([token(request.get("host", "codex")), token(request["session_id"])]))


def expected(state: Mapping[str, Any], request: JSONObject) -> None:
    """Require the caller to name the current issue revision.

    Args:
        state: Current or staged issue control state.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Raises:
        WorkspaceError: If the expected revision is stale or absent.
    """
    require(request.get("expected_revision") == state["revision"], "REVISION_CONFLICT")
