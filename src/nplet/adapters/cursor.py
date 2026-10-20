"""Translate local Cursor Agent hooks with native decisions and conversation identity."""

from __future__ import annotations

import math
import os
import sys
import time

from nplet.adapters import common, cursor_children, runner
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
    if name in {"preToolUse", "beforeMCPExecution", "subagentStart", "beforeReadFile"}:
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


def child_event(event: core.JSONObject, name: str, deadline: float) -> core.JSONObject:
    """Route only scoped Read and own lifecycle operations for a bound Cursor child.

    Args:
        event: Independently normalized current child envelope.
        name: Actual native hook name.
        deadline: Trusted shared supervisor deadline.

    Returns:
        Native scoped decisions or advisory context without parent startup/provider authority.

    Raises:
        core.WorkspaceError: Tool, source or own lifecycle command is unsupported.
    """
    # A child can prepare exact sources before acknowledgment; core receipt gates own readiness.
    if name == "preToolUse":
        if not cursor_children.child_lifecycle(event):
            cursor_children.source_read_pre(event, deadline)
        result: core.JSONObject = {"permission": "allow"}
        if event.get("_cursor_new_child"):
            result["additional_context"] = cursor_children.child_context(event)
        return result
    # Actual content is provisional until the same Read's recognized native success.
    if name == "beforeReadFile":
        cursor_children.source_read_content(event, deadline)
        return {"permission": "allow"}
    # Lifecycle commands manage their own transaction; file Read effects use the closed callback.
    if name in {"postToolUse", "postToolUseFailure"}:
        if cursor_children.child_lifecycle(event):
            return {}
        result = cursor_children.source_read_post(
            event, deadline, failed=name == "postToolUseFailure"
        )
        if not result["source_receipt"]:
            return {
                "additional_context": "SOURCE_DELIVERY_REQUIRED: "
                "Read finished without a current source receipt. "
                + cursor_children.child_context(event)
            }
        return {}
    # Children never enter provider ticket bootstrap or ordinary parent prompt startup.
    if name in {"beforeMCPExecution", "afterMCPExecution"}:
        return failure(name, "HOST_UNSUPPORTED_PROVIDER")
    if name == "beforeSubmitPrompt":
        return {"continue": True}
    if name == "sessionStart":
        return {"additional_context": cursor_children.child_context(event)}
    if name == "preCompact":
        return {"user_message": cursor_children.child_context(event)}
    # A stop is an observation only, never Task retirement or pending settlement.
    if name in {"stop", "sessionEnd"}:
        common.native_observe(event, HOST)
        return {}
    return failure(name, "HOST_UNSUPPORTED_EVENT")


def handle(event: core.JSONObject) -> core.JSONObject:
    """Translate a native Cursor envelope into conversation-bound lifecycle actions.

    Args:
        event: JSON received from a local Cursor Agent hook.

    Returns:
        Native decisions; session start and compaction are advisory only.
    """
    name = event.get("hook_event_name", "")
    observed: core.JSONObject = {}
    deadline = event.get("_cursor_deadline", time.monotonic() + 1.5)
    # Reject unsupported surfaces before parent startup, and classify retained children first.
    try:
        core.require(type(deadline) in {int, float} and math.isfinite(deadline), "INVALID_REQUEST")
        cursor_children.cursor_results.check_deadline(deadline)
        core.require(os.environ.get("CURSOR_CODE_REMOTE") != "true", "HOST_UNSUPPORTED_REMOTE")
        observed = cursor_children.normalize(event, deadline)
        if observed.get("child") is True:
            return child_event(observed, name, deadline)
        # Parent beforeReadFile is advisory-only; ordinary Read admission/completion stays generic.
        if name == "beforeReadFile":
            return {}
        # Record the generic callback's native call ID before granting the tool.
        if name == "preToolUse":
            # Correlate Cursor's native tool ID. The MCP-specific callback must
            # also validate the configured server before startup can settle.
            if observed.get("tool_name") == "MCP:get_issue" and common.selected_lookup(
                observed, HOST
            ):
                core.require(
                    common.native_ticket_lookup(observed, HOST) is not None, "TICKET_READ_REQUIRED"
                )
                return {"permission": "allow"}
            # Only an exact planned native Task can create its existing parent pending charge.
            if observed.get("tool_name") == "Task":
                cursor_children.admit(observed, deadline)
                return {"permission": "allow"}
            # This self-managing command independently reads evidence and owns retirement.
            reconcile = cursor_children.recognize_reconcile(observed)
            if reconcile is not None:
                cursor_children.authorize_reconcile(observed, *reconcile, deadline)
                return {"permission": "allow"}
            common.native_pre(observed, HOST)
            return {"permission": "allow"}  # Only this validated and recorded tool call passes.
        # Verify the configured Linear server through the MCP-specific callback.
        if name == "beforeMCPExecution":
            # Ticket bootstrap requires both generic and specific prehook facts.
            if observed.get("tool_name") == "get_issue" and common.selected_lookup(observed, HOST):
                core.require(
                    common.native_ticket_lookup(observed, HOST, specific=True) is not None,
                    "TICKET_READ_REQUIRED",
                )
                return {"permission": "allow"}
            # Every ordinary MCP call also needs the configured server's blocking hook.
            core.require(
                observed.get("tool_name") in common.LINEAR_PROVIDER_OPERATIONS,
                "HOST_UNSUPPORTED_PROVIDER",
            )
            core.require(
                observed.get("mcp_server_name") in common.LINEAR_SERVER_NAMES
                and observed.get("mcp_server_url", observed.get("url"))
                in common.LINEAR_SERVER_URLS,
                "HOST_UNSUPPORTED_PROVIDER",
            )
            core.require(
                isinstance(observed.get("tool_input"), str)
                and isinstance(core.strict_json(observed["tool_input"]), dict),
                "INVALID_REQUEST",
            )
            return {"permission": "allow"}
        # The MCP-specific posthook has no native call ID, so it cannot settle a read.
        if name == "afterMCPExecution":
            return {}  # No native call ID; generic postToolUse owns completion.
        # Correlate native completion facts with the exact admitted tool call.
        if name in {"postToolUse", "postToolUseFailure"}:
            # Task callbacks keep their charge until independent result reconciliation.
            if observed.get("tool_name") == "Task":
                if name == "postToolUseFailure":
                    core.require(
                        isinstance(observed.get("error_message"), str)
                        and observed.get("failure_type")
                        in {"error", "timeout", "permission_denied"},
                        "INVALID_REQUEST",
                    )
                else:
                    core.require(isinstance(observed.get("tool_output"), str), "INVALID_REQUEST")
                parent = common.request_for(observed, "ready", HOST)
                command = cursor_children.reconcile_command(
                    parent["issue_id"], observed["tool_use_id"]
                )
                return {
                    "additional_context": "Task remains charged until result reconciliation. "
                    "Run: " + command
                }
            # Native prehook authorized this exact command; it never created generic pending work.
            if cursor_children.recognize_reconcile(observed) is not None:
                return {}
            # A ticket read must settle against the pending Task marker.
            if observed.get("tool_name") == "MCP:get_issue":
                # Ignore unrelated completions when no ticket lookup is pending.
                if common.selected_lookup(observed, HOST, complete=True):
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
        response = failure(name, code)
        # Denials deliver only this actual child's own recovery command, never parent identity.
        if observed.get("child") is True:
            try:
                context = cursor_children.child_context(observed)
                response["additional_context"] = (
                    response.get("additional_context", "") + " " + context
                )
            except (core.WorkspaceError, OSError):
                pass
        return response


def main() -> int:
    """Run one fixed normal or beforeReadFile observer protocol invocation.

    Returns:
        Existing native status, or two for unsupported entry arguments.
    """
    # Only this explicit configured CLI mode selects the dedicated full-content observer budget.
    if sys.argv[1:] == ["--observe-read-content"]:
        return runner.run(handle, failure, content_observer=True)
    if len(sys.argv) != 1:
        print(core.canonical(failure("", "INVALID_REQUEST")).decode())
        print("TASK_WORKSPACE_NOT_READY: INVALID_REQUEST", file=sys.stderr)
        return 2
    return common.run_native(handle, failure)


# Execute the hook protocol only when invoked as its entry point.
if __name__ == "__main__":
    sys.exit(main())
