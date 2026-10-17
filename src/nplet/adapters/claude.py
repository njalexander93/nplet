"""Translate local Claude Code hooks without granting host permissions."""

from __future__ import annotations

import os
import sys

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core

HOST = "claude-code"
PYTHON = common.PYTHON
LIFECYCLE = common.LIFECYCLE


def failure(name: str, code: str) -> core.JSONObject:
    """Render a native denial or advisory diagnostic for this Claude hook.

    Args:
        name: Native hook event name.
        code: Bounded lifecycle or adapter diagnostic.

    Returns:
        Claude's event-specific response without a permission grant.
    """
    # Compaction diagnostics cannot deliver recovery context through these events.
    if name in {"PreCompact", "PostCompact"}:
        return {}
    message = common.recovery(code, HOST)
    # Pre-tool failures must deny the pending native call.
    if name == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": name,
                "permissionDecision": "deny",
                "permissionDecisionReason": message,
            }
        }
    # Prompt admission can block submission without changing tool permissions.
    if name == "UserPromptSubmit":
        return {"decision": "block", "reason": message}
    return {"systemMessage": message}


def handle(event: core.JSONObject) -> core.JSONObject:
    """Translate a native Claude envelope into host-bound lifecycle actions.

    Args:
        event: JSON received from a local Claude Code hook.

    Returns:
        Native JSON preserving normal permissions and reporting unsupported paths.
    """
    # Cursor imports Claude settings by default; its native adapter alone owns those events.
    if os.environ.get("CURSOR_VERSION"):
        return {}
    name = event.get("hook_event_name", "")
    # Normalize the native identity before considering any lifecycle action.
    try:
        # Never reuse a coordinator binding for a child or fabricate a missing identity;
        # a Claude subagent is keyed by its own normalized child session.
        observed = common.native_identity(event, HOST)
        # The exact selected-ticket read establishes the issue transition.
        if name == "PreToolUse":
            # A pending Task retains exact startup and separate task-free routes.
            if common.lookup_required(observed, HOST):
                # Exact preparation reads retain their existing native delivery contract.
                if common.preparation_tool(observed, HOST):
                    return {}
                # Unrelated task-free research cannot consume the selected-ticket marker.
                if (
                    not common.selected_ticket(observed, HOST)
                    and not common.effect_decision(observed, HOST).requires_task
                ):
                    common.native_pre(observed, HOST)
                    return {}
                core.require(
                    common.native_ticket_lookup(observed, HOST) is not None, "TICKET_READ_REQUIRED"
                )
                return {}
            common.native_pre(observed, HOST)
            return {}  # No decision: Claude still applies its normal permission flow.
        # Translate completed native calls without inventing asynchronous completion.
        if name in {"PostToolUse", "PostToolUseFailure"}:
            # A pending ticket read settles only through its native tool result.
            if common.lookup_required(observed, HOST):
                # Only the exact reserved native call may complete or fail startup.
                if not common.ticket_callback(observed, HOST):
                    common.native_post(observed, HOST, failed=name == "PostToolUseFailure")
                    return {}
                # Failed provider calls release correlation for a later read.
                if name == "PostToolUseFailure":
                    common.native_ticket_failed(observed, HOST)
                    return failure(name, "PROVIDER_ERROR")
                result = common.native_ticket_lookup(observed, HOST, complete=True)
                # A nonmatching result cannot authorize workspace creation.
                if result is None:
                    raise core.WorkspaceError("TICKET_READ_REQUIRED")
                return {
                    "systemMessage": "TASK_WORKSPACE_READY: Linear ticket "
                    + result["issue_id"]
                    + " verified."
                }
            common.native_post(observed, HOST, failed=name == "PostToolUseFailure")
            return {}
        # Task selection is explicit and host-bound, never inferred from session startup.
        if name == "UserPromptSubmit":
            return common.prompt(observed, HOST)
        # Lifecycle context preserves recovery access without granting tool readiness.
        if name == "SessionStart":
            return {
                "hookSpecificOutput": {
                    "hookEventName": name,
                    "additionalContext": common.native_context(observed, HOST),
                }
            }
        # Compaction is observation-only; recovery arrives at SessionStart or pre-tool denial.
        if name in {"PreCompact", "PostCompact"}:
            common.native_observe(observed, HOST)
            return {}
        # Endings are observations, not settlement, acceptance, or cleanup triggers.
        if name in {"Stop", "SessionEnd"}:
            common.native_observe(observed, HOST)
            return {}
        # A subagent of a ready parent with a pending Agent call joins as its own reader.
        # SubagentStart cannot block, so any refusal is only an advisory systemMessage.
        if name == "SubagentStart":
            return {
                "hookSpecificOutput": {
                    "hookEventName": name,
                    "additionalContext": common.child_start(observed, HOST),
                }
            }
        # A subagent ending is an observation; it neither settles nor detaches the child.
        if name == "SubagentStop":
            core.require(observed.get("child") is True, "HOST_UNSUPPORTED_CHILD_IDENTITY")
            common.native_observe(observed, HOST)
            return {}
        # Leave host permission decisions with Claude Code.
        if name == "PermissionRequest":
            return {}  # This adapter never supplies allow or changes permission settings.
        return failure(name, "HOST_UNSUPPORTED_EVENT")
    # Convert adapter and lifecycle errors into native recovery output.
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        return failure(name, code)


def main() -> int:
    """Run the bounded native protocol, skipping Cursor-imported duplicate hooks.

    Returns:
        Zero for a handled event or two for a malformed/unreadable input envelope.
    """
    # Exit silently before parsing or mutating state when Cursor imported this entry.
    if os.environ.get("CURSOR_VERSION"):
        return 0
    return common.run_native(handle, failure)


# Execute the hook protocol only when invoked as its entry point.
if __name__ == "__main__":
    sys.exit(main())
