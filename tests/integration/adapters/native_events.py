"""Build native adapter envelopes shared by real-store integration scenarios."""

from pathlib import Path


def event(
    root: Path, host: str, tool: str, args: dict, name: str = "PreToolUse", call: str = "call"
) -> dict:
    """Build one actual native protocol shape without granting a task binding.

    Args:
        root: Disposable Git checkout.
        host: Selected adapter host.
        tool: Native operation identity.
        args: Native structured tool input.
        name: Claude/Codex event spelling.
        call: Native call identifier.

    Returns:
        Host-specific event envelope.
    """
    # Cursor derives identity from its conversation and one workspace root.
    value = {
        "cwd": str(root),
        "session_id": "fresh",
        "tool_use_id": call,
        "tool_name": tool,
        "tool_input": args,
        "hook_event_name": name,
    }
    if host == "cursor":
        value.update(
            conversation_id="fresh",
            workspace_roots=[str(root)],
            hook_event_name=name[0].lower() + name[1:],
        )
    return value
