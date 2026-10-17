"""Translate local Cursor Agent hooks with native decisions and conversation identity."""

from __future__ import annotations

import os
import sys

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core

HOST = "cursor"
PYTHON = common.PYTHON
LIFECYCLE = common.LIFECYCLE


def failure(name: str, code: str) -> core.JSONObject:
    """Render the supported Cursor decision or diagnostic for an event.

    Args:
        name: Native Cursor event name.
        code: Bounded lifecycle or adapter diagnostic.

    Returns:
        Cursor-native JSON without an automatic follow-up or blanket permission grant.
    """
    message = common.recovery(code, HOST)
    # Admission failures deny only the pending Cursor tool or child hook.
    if name in {"preToolUse", "beforeMCPExecution", "subagentStart"}:
        return {"permission": "deny", "user_message": message, "agent_message": message}
    # Prompt rejection uses Cursor's continue decision.
    if name == "beforeSubmitPrompt":
        return {"continue": False, "user_message": message}
    # Completion and session events can only carry advisory context.
    if name in {"sessionStart", "postToolUse", "postToolUseFailure", "afterMCPExecution"}:
        return {"additional_context": message}
    # Compaction has a separate user-visible diagnostic field.
    if name == "preCompact":
        return {"user_message": message}
    return {}


def handle(event: core.JSONObject) -> core.JSONObject:
    """Translate a native Cursor envelope into conversation-bound lifecycle actions.

    Args:
        event: JSON received from a local Cursor Agent hook.

    Returns:
        Native decisions; session start and compaction are advisory only.
    """
    name = event.get("hook_event_name", "")
    # Reject unsupported execution surfaces before looking up session state.
    try:
        # Local single-root Agent sessions are the only supported Cursor execution surface.
        core.require(os.environ.get("CURSOR_CODE_REMOTE") != "true", "HOST_UNSUPPORTED_REMOTE")
        # Child sessions have no independently verified conversation identity.
        if name == "subagentStart":
            return failure(name, "HOST_UNSUPPORTED_CHILD_IDENTITY")
        observed = common.native_identity(event, HOST)
        # Record the generic callback's native call ID before granting the tool.
        if name == "preToolUse":
            # Correlate Cursor's native tool ID. The MCP-specific callback must
            # also validate the configured server before startup can settle.
            if common.lookup_required(observed, HOST):
                # Ordinary task-free work leaves the selected-ticket handshake intact.
                if (
                    not common.selected_ticket(observed, HOST)
                    and not common.effect_decision(observed, HOST).requires_task
                ):
                    common.native_pre(observed, HOST)
                    return {"permission": "allow"}
                core.require(
                    common.native_ticket_lookup(observed, HOST) is not None, "TICKET_READ_REQUIRED"
                )
                return {"permission": "allow"}
            common.native_pre(observed, HOST)
            return {"permission": "allow"}  # Only this validated and recorded tool call passes.
        # Verify the configured Linear server through the MCP-specific callback.
        if name == "beforeMCPExecution":
            # Ticket bootstrap requires both generic and specific prehook facts.
            if common.lookup_required(observed, HOST) and common.selected_ticket(
                observed, HOST, specific=True
            ):
                core.require(
                    common.native_ticket_lookup(observed, HOST, specific=True) is not None,
                    "TICKET_READ_REQUIRED",
                )
                return {"permission": "allow"}
            # This second blocking gate establishes provider provenance independently
            # of the operation-only generic gate. Both hooks are configured fail-closed.
            common.cursor_provider(observed)
            return {"permission": "allow"}
        # The MCP-specific posthook has no native call ID, so it cannot settle a read.
        if name == "afterMCPExecution":
            return {}  # No native call ID; generic postToolUse owns completion.
        # Correlate native completion facts with the exact admitted tool call.
        if name in {"postToolUse", "postToolUseFailure"}:
            # A ticket read must settle against the pending Task marker.
            if observed.get("tool_name") == "MCP:get_issue":
                # Ignore unrelated completions when no ticket lookup is pending.
                if common.lookup_required(observed, HOST) and common.ticket_callback(
                    observed, HOST
                ):
                    # A failed read releases this call ID for a fresh attempt.
                    if name == "postToolUseFailure":
                        common.native_ticket_failed(observed, HOST)
                        return failure(name, "PROVIDER_ERROR")
                    result = common.native_ticket_lookup(observed, HOST, complete=True)
                    # Generic post cannot settle without both prehook facts.
                    if result is None:
                        raise core.WorkspaceError("TICKET_READ_REQUIRED")
                    return {
                        "additional_context": "TASK_WORKSPACE_READY: Linear ticket "
                        + result["issue_id"]
                        + " verified."
                    }
                # Without a pending Task, ordinary provider calls use normal admission.
            common.native_post(observed, HOST, failed=name == "postToolUseFailure")
            return {}
        # Cursor prompt decisions use continue; they do not grant tool permission.
        if name == "beforeSubmitPrompt":
            result = common.prompt(observed, HOST)
            # Preserve Cursor's native prompt decision shape.
            if result.get("decision") == "block":
                return {"continue": False, "user_message": result["reason"]}
            return {"continue": True}
        # Startup and compaction provide advisory context only.
        if name == "sessionStart":
            return {"additional_context": common.native_context(observed, HOST)}
        # Compaction reads context without admitting a new tool.
        if name == "preCompact":
            return {"user_message": common.native_context(observed, HOST)}
        # Terminal events only record observation; they do not settle tools.
        if name in {"stop", "sessionEnd"}:
            common.native_observe(observed, HOST)
            return {}  # A stopped agent loop is not evidence of finished tools or accepted work.
        return failure(name, "HOST_UNSUPPORTED_EVENT")
    # Render local failures with Cursor's event-specific decision fields.
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        return failure(name, code)


def main() -> int:
    """Run one bounded Cursor protocol exchange with fail-closed parser errors.

    Returns:
        Zero for a handled event or two for malformed/unreadable input.
    """
    return common.run_native(handle, failure)


# Execute the hook protocol only when invoked as its entry point.
if __name__ == "__main__":
    sys.exit(main())
