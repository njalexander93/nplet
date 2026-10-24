"""Share one native tool event builder for adapter unit tests."""

from __future__ import annotations


def native_event(tool: str, args: dict[str, object] | None = None) -> dict[str, object]:
    """Build one native tool event for the shared adapter contract.

    Args:
        tool: Native tool name presented to the adapter.
        args: Arguments supplied to the helper under test.

    Returns:
        The host tool event with a stable call ID.
    """
    return {"tool_use_id": "tool-1", "tool_name": tool, "tool_input": {} if args is None else args}
