"""Carry a canonical lifecycle request through native Windows argument quoting."""

from __future__ import annotations

import base64
import binascii
import sys

from nplet.lifecycle import task_workspace as core


def decode_request(encoded: str) -> core.JSONObject:
    """Decode one bounded canonical base64 JSON object without shell interpretation.

    Args:
        encoded: ASCII URL-safe base64 with its canonical padding.

    Returns:
        The strict lifecycle JSON object.

    Raises:
        core.WorkspaceError: If encoding, size, or request shape is invalid.
    """
    # Bound the encoded input before decoding attacker-controlled bytes.
    core.require(isinstance(encoded, str) and len(encoded) <= 65536, "SIZE_LIMIT")
    # Decode using the exact URL-safe alphabet and reject malformed base64.
    try:
        # Validate the encoded alphabet and padding during conversion.
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        # Translate decoder failures into the public request error.
        raise core.WorkspaceError("INVALID_REQUEST") from error
    # Require canonical encoding, decoded size, and a strict JSON object.
    core.require(base64.urlsafe_b64encode(raw).decode("ascii") == encoded, "INVALID_REQUEST")
    core.require(len(raw) <= core.MAX_REQUEST, "SIZE_LIMIT")
    request = core.strict_json(raw)
    # The lifecycle API accepts an object, never a scalar or list.
    if not isinstance(request, dict):
        # Report the wrong top-level JSON shape as invalid input.
        raise core.WorkspaceError("INVALID_REQUEST")
    return request


def main() -> int:
    """Execute the unchanged lifecycle API using one encoded JSON argument.

    Returns:
        Zero for lifecycle success, three for a reported request failure.
    """
    # Validate the sole encoded argument and execute its lifecycle request.
    try:
        # Require the documented flag before invoking the core.
        core.require(len(sys.argv) == 3 and sys.argv[1] == "--request-base64", "INVALID_REQUEST")
        request = decode_request(sys.argv[2])
        core.require(isinstance(request.get("operation"), str), "INVALID_REQUEST")
        result = core.execute(request)
    except core.WorkspaceError as error:
        # Keep expected request failures inside the canonical wire response.
        result = {"ok": False, "code": error.code}
    # Emit one canonical result and preserve failure in the process status.
    print(core.canonical(result).decode())
    return 0 if result["ok"] else 3


# Run the standalone argument adapter only when invoked as a script.
if __name__ == "__main__":
    # Pass its success or failure status to the shell.
    sys.exit(main())
