#!/usr/bin/env python3
"""Bounded Codex wire adapter. Host trust/coverage is a separate acceptance gate."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from nplet.adapters import codex_children, common, runner, startup
from nplet.lifecycle import task_workspace as core

ROOT = Path(__file__).resolve().parents[3]

BOOTSTRAP_FIELDS = common.BOOTSTRAP_FIELDS
COMMON_FIELDS = common.COMMON_FIELDS
LIFECYCLE = common.LIFECYCLE
PYTHON = common.PYTHON

# Preserve the legacy adapter spelling alongside the captured native connector name.
TICKET_TOOLS = {"mcp__codex_apps__linear__get_issue", "mcp__codex_apps__linear_get_issue"}


def denial(code: str) -> core.JSONObject:
    """Construct a supported explicit PreToolUse denial without granting permissions.

    Args:
        code: Public bounded diagnostic code.

    Returns:
        The hook-specific denial object.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": common.recovery(code, "codex"),
        }
    }


def async_handle(value: object) -> str | None:
    """Normalize optional host handles without disguising malformed metadata.

    Args:
        value: An absent/null handle, or a nonempty string or integer identifier.

    Returns:
        The validated string handle, or None only for absent/null metadata.

    Raises:
        core.WorkspaceError: If an explicitly supplied handle has an invalid type or token.
    """
    # Preserve absence, but do not equate malformed supplied metadata with absence.
    if value is None:
        # No asynchronous identity was supplied to correlate.
        return None
    # Booleans are not integer process identities despite Python's subtype relation.
    core.require(type(value) in {str, int}, "ASYNC_HANDLE_CONFLICT")
    return core.token(str(value))


def request_for(event: core.JSONObject, operation: str) -> core.JSONObject:
    """Resolve an actual hook session to its registered repository and binding.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        operation: Core operation requested for the observed session.

    Returns:
        A versioned core request carrying the observed session identity.

    Raises:
        core.WorkspaceError: If registration or binding validation fails.
        OSError: If the local store cannot be read.
    """
    return common.request_for(event, operation, "codex")


def bootstrap(event: core.JSONObject, ready: bool = False) -> bool:
    """Recognize only the exact scoped lifecycle command and canonical shell spelling.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        ready: Whether the caller already proved ordinary readiness.

    Returns:
        Whether the tool request is an allowed lifecycle bootstrap.
    """
    # Restrict bootstrap handling to observable shell tool calls.
    if event.get("tool_name") not in {"Bash", "exec_command"}:
        # A non-shell call cannot carry the canonical bootstrap command.
        return False
    # Select the one command field defined for this shell transport.
    args = event.get("tool_input", {})
    # Installed native Bash canonicalizes transport options into one command field.
    if event["tool_name"] == "Bash" and isinstance(args, dict) and set(args) == {"command"}:
        return common.canonical_bootstrap(event, args["command"], "codex", ready, PYTHON, LIFECYCLE)
    command_field = "command" if event["tool_name"] == "Bash" else "cmd"
    other_field = "cmd" if command_field == "command" else "command"
    # Reject ambiguous fields, interactive shells, and unsupported tool options.
    if (
        not isinstance(args, dict)
        or command_field not in args
        or other_field in args
        or args.get("tty")
        or args.get("login") is not False
        or args.get("shell")
        not in ({"powershell.exe", "pwsh.exe"} if os.name == "nt" else {"/bin/sh"})
        or not set(args)
        <= {
            "command",
            "cmd",
            "login",
            "shell",
            "workdir",
            "yield_time_ms",
            "max_output_tokens",
            "sandbox_permissions",
            "justification",
            "prefix_rule",
        }
    ):
        # Invalid shell metadata cannot enter the bootstrap exception.
        return False
    # Verify the exact command spelling against the scoped lifecycle route.
    command = args[command_field]
    return common.canonical_bootstrap(event, command, "codex", ready, PYTHON, LIFECYCLE)


def diagnostic_bootstrap(event: core.JSONObject) -> bool:
    """Admit only a canonical read-only diagnosis while the ticket gate is pending.

    Args:
        event: Observed shell tool call with actual session and worktree identities.

    Returns:
        Whether the scoped bootstrap command selects diagnosis alone.
    """
    # Reuse the existing exact shell, identity, argument, and assignment validation.
    if not bootstrap(event):
        return False
    # Narrow the validated bootstrap route to its single read-only operation.
    args = event["tool_input"]
    command = args["command" if event["tool_name"] == "Bash" else "cmd"]
    request = common.bootstrap_request(command, "codex", PYTHON, LIFECYCLE)
    return request.get("operation") == "diagnose"


def provider_gate(event: core.JSONObject) -> bool:
    """Permit only exact coordinator-bound provider save/get requests.

    Args:
        event: Observed host hook input, including the actual session and tool identities.

    Returns:
        Whether this provider call is allowed by the pending archive request.

    Raises:
        core.WorkspaceError: If binding, ownership or archive integrity validation fails.
        OSError: If archive state cannot be read.
    """
    tool = event.get("tool_name", "")
    # Ignore tools outside the two archive provider operations.
    if tool not in {
        "mcp__codex_apps__linear_save_document",
        "mcp__codex_apps__linear_get_document",
    }:
        # Other provider tools use their separate effect and admission rules.
        return False
    # Build the request from explicit caller or observed session identities.
    request = request_for(event, "ready")
    # Lock the issue and verify coordinator authority before provider access.
    with (
        core.Store(request) as store,
        store.issues.child(request["issue_id"]) as control,
        control.lock(),
    ):
        # Load the committed issue under lock before checking provider arguments.
        issue = core.Issue(store, control, request["issue_id"])
        issue.recover()
        state = issue.committed_state()
        core.authorize(state, request, coordinator=True, maintenance=True)
        # A present payload must still pass integrity checks before provider I/O.
        if state["storage"] != "cleaned":
            # Validate local files while they remain available.
            issue.files()
        # Inspect only the host-provided provider arguments.
        args = event.get("tool_input", {})
        # Permit reads only of document IDs already recorded for this issue archive.
        if tool.endswith("linear_get_document"):
            # Assemble IDs from pending saves and the durable archive receipt.
            identifiers = {x["id"] for x in state.get("provider_saves", {}).values() if x.get("id")}
            # Include confirmed root and part documents when an archive exists.
            if state.get("archive"):
                # Read-back may revisit each retained archive document.
                identifiers.add(state["archive"]["root"]["id"])
                identifiers.update(x["id"] for x in state["archive"]["parts"])
            return set(args) == {"id"} and args["id"] in identifiers
        # A cleaned issue may read its archive but cannot start another save.
        if state["storage"] == "cleaned":
            # Cleanup closes the provider-write lane permanently.
            return False
        export = control.json("export-" + state["export"]["snapshot"] + ".json")
        documents = export["parts"] + (
            [state["index_request"]] if state.get("index_request") else []
        )
        # Reject provider writes that differ from the immutable export request.
        if args not in documents:
            # Arbitrary document content cannot be saved through this gate.
            return False
        # Identify the exact content whose save uncertainty must be recorded.
        digest = core.sha(args["content"].encode())
    # Record save uncertainty before allowing the external provider write.
    result = core.execute({**request, "operation": "archive-save-start", "content_digest": digest})
    allowed: bool = result["ok"]
    return allowed


def automatic_attach(event: core.JSONObject, identifier: str) -> None:
    """Apply only an explicit startup assignment or an existing participant packet.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        identifier: Validated issue ID or explicit tool-call identity.

    Raises:
        core.WorkspaceError: If registration, assignment or lifecycle setup fails.
        OSError: If assignment or issue state cannot be accessed.
    """
    common.automatic_attach(event, identifier, "codex")


def prompt(event: core.JSONObject) -> core.JSONObject:
    """Record one explicit Task line and attempt only its assigned workspace setup.

    Args:
        event: Observed host hook input, including the actual session and tool identities.

    Returns:
        An empty response, prompt denial or bounded setup context.

    Raises:
        core.WorkspaceError: If workspace registration or setup fails.
        OSError: If assignment persistence fails.
    """
    return common.prompt(event, "codex")


def startup_lookup_state(event: core.JSONObject, suffix: str) -> bool:
    """Check whether this session has a pending explicit Task lookup marker.

    Args:
        event: Observed Codex session and checkout identity.
        suffix: Exact marker suffix to inspect in this session's bindings.

    Returns:
        Whether this session has the requested marker.
    """
    # Resolve the exact repository and participant before inspecting marker files.
    root, _, _ = core.repository(event["cwd"])
    key = core.participant_key({"host": "codex", "session_id": event["session_id"]})
    # Inspect marker files through validated no-follow directory handles.
    with core.Directory.absolute(root) as directory:
        # An absent task workspace cannot contain a pending lookup.
        if not directory.exists(".task"):
            # Let ordinary startup recovery handle the missing workspace.
            return False
        # Read only the bindings folder under the selected repository.
        with directory.child(".task") as task:
            # An absent bindings folder has no per-session lookup marker.
            if not task.exists(".bindings"):
                # Do not invent a binding on a read-only marker check.
                return False
            # Query the exact participant-specific marker name.
            with task.child(".bindings") as bindings:
                # Return only the requested marker's existence.
                return bindings.exists(key + suffix)


def ticket_lookup(event: core.JSONObject, complete: bool = False) -> core.JSONObject | None:
    """Admit and settle the direct issue read for the exact recorded Task identity.

    Args:
        event: Observed Codex Linear tool call and optional provider result.
        complete: Whether this is the correlated PostToolUse callback.

    Returns:
        None for another tool, empty admission, or verified readiness context.

    Raises:
        core.WorkspaceError: If the task, tool, provider, or startup contract fails.
    """
    # Only the exact direct Linear issue tool may cross the ticket-first gate.
    if event.get("tool_name") not in TICKET_TOOLS:
        # Other tools do not participate in the assigned-ticket handshake.
        return None
    root, _, _ = core.repository(event["cwd"])
    key = core.participant_key({"host": "codex", "session_id": event["session_id"]})
    # Lock this participant's binding while validating the assigned issue and call.
    with (
        core.Directory.absolute(root) as directory,
        directory.child(".task") as task,
        task.child(".bindings") as bindings,
        bindings.lock("assignment.lock"),
    ):
        # Correlate the requested issue with the durable assignment and lookup marker.
        assignment = bindings.json(key + ".assignment.json")
        identifier = assignment["issue_id"]
        core.require(bindings.json(key + ".lookup-required.json") == assignment, "BINDING_CONFLICT")
        # Reject a broad or redirected issue query before recording tool identity.
        if event.get("tool_input") != {"id": identifier}:
            # The exact issue ID is the only permitted provider argument.
            raise core.WorkspaceError("BINDING_CONFLICT")
        tool_id = core.token(event["tool_use_id"])
        name = key + ".lookup.json"
        correlation = {"id": identifier, "tool_id": tool_id}
        # Pre-tool callbacks reserve the provider call; post-tool callbacks settle it.
        if not complete:
            # Repeated pre-hooks must match the same issue and tool correlation.
            if bindings.exists(name):
                # Validate a retry against the previously admitted lookup.
                previous = bindings.json(name)
                core.require(
                    previous == correlation
                    or previous == {**correlation, "completed": True}
                    or (
                        isinstance(previous, dict)
                        and set(previous) == {"id", "tool_id", "completed"}
                        and previous["id"] == identifier
                        and previous["completed"] is True
                        and isinstance(previous["tool_id"], str)
                    ),
                    "BINDING_CONFLICT",
                )
                # A completed attempt permits a fresh exact lookup tool ID.
                if previous.get("completed") is True and previous.get("tool_id") != tool_id:
                    # Replace stale correlation before this provider call runs.
                    bindings.put(name, correlation)
            else:
                # Record the first permitted direct issue read.
                bindings.put(name, correlation)
            return {}
        core.require(bindings.exists(name), "BINDING_MISSING")
        core.require(
            bindings.json(name) in (correlation, {**correlation, "completed": True}),
            "BINDING_CONFLICT",
        )
    # Decode the provider payload and verify it describes the assigned issue.
    try:
        # A missing or malformed result cannot establish ticket readiness.
        response = event.get("tool_response")
        # Hosts may serialize the provider's structured response as JSON text.
        if isinstance(response, str):
            # Parse serialized content before checking the issue identity.
            try:
                # Preserve the provider's structured fields for ticket verification.
                response = json.loads(response)
            except ValueError as error:
                # Bad JSON is a provider response failure, not an issue mismatch.
                raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID") from error
        # Native MCP may omit its optional error flag. Normalize only this correlated
        # successful-return envelope; the strict parser still verifies one exact issue.
        if (
            event.get("hook_event_name") == "PostToolUse"
            and event.get("tool_name") == "mcp__codex_apps__linear__get_issue"
            and isinstance(response, dict)
            and "isError" not in response
            and isinstance(response.get("content"), list)
        ):
            response = {**response, "isError": False}
        verified = startup.ticket(response, identifier)
    except core.WorkspaceError:
        # Drop failed correlation so the participant can retry the exact issue read.
        with (
            core.Directory.absolute(root) as directory,
            directory.child(".task") as task,
            task.child(".bindings") as bindings,
            bindings.lock("assignment.lock"),
        ):
            # Keep the assignment while clearing only the failed lookup attempt.
            bindings.unlink(name)
        raise
    # Build the task workspace only from the verified issue payload.
    try:
        # Startup may fail after the provider call already completed.
        ready = startup.start(event, verified)
    except Exception:
        # The provider call has completed. Preserve same-ID completion retry while
        # allowing a fresh, exact lookup if partial workspace setup failed.
        # Mark a settled provider call for safe same-ID completion retry.
        with (
            core.Directory.absolute(root) as directory,
            directory.child(".task") as task,
            task.child(".bindings") as bindings,
            bindings.lock("assignment.lock"),
        ):
            # Preserve a newer correlation if another callback replaced this one.
            if bindings.json(name) == correlation:
                # Distinguish completed provider work from an uncalled reservation.
                bindings.put(name, {**correlation, "completed": True})
        raise
    # Remove lookup gates only after workspace startup succeeds.
    with (
        core.Directory.absolute(root) as directory,
        directory.child(".task") as task,
        task.child(".bindings") as bindings,
        bindings.lock("assignment.lock"),
    ):
        # Clear both the in-flight call and required-read marker atomically.
        bindings.unlink(name)
        bindings.unlink(key + ".lookup-required.json")
    message = (
        "TASK_WORKSPACE_READY: Linear ticket "
        + identifier
        + " read; assigned source bytes verified and packet acknowledged."
    )
    # Readers receive an explicit reminder that issue ownership did not move.
    if ready["participant_id"] != ready["coordinator"]:
        # State the coordinator boundary in the host-visible readiness message.
        message += (
            " Reader scope only. Coordinator "
            + ready["coordinator"]
            + " retains roadmap ownership; use explicit coordinator transfer for edits."
        )
    return {"systemMessage": message}


def handle(event: core.JSONObject) -> core.JSONObject:
    """Translate one observed host lifecycle event into scoped core actions.

    Args:
        event: Observed host hook input, including the actual session and tool identities.

    Returns:
        The event-specific hook response without inferred readiness or completion.

    Raises:
        core.WorkspaceError: If required binding or store validation fails.
        OSError: If the registered store cannot be accessed.
    """
    # Select the event-specific host response and lifecycle action.
    name = event.get("hook_event_name")
    # Route native child UUIDs exclusively through their own participant and bounded tools.
    if event.get("agent_id") is not None or event.get("agent_type") is not None:
        observed = codex_children.identity(event)
        if name == "SubagentStart":
            return {
                "hookSpecificOutput": {
                    "hookEventName": name,
                    "additionalContext": codex_children.start(observed),
                }
            }
        if name == "PreToolUse":
            codex_children.child_pre(observed)
        elif name == "PostToolUse":
            codex_children.child_post(observed)
        elif name in {"Stop", "Interrupt", "SessionEnd", "SubagentStop"}:
            codex_children.child_end(observed)
        return {}
    # Record explicit task identity before attempting assigned setup.
    if name == "UserPromptSubmit":
        # Store the user's task binding before admitting later tools.
        return prompt(event)
    # Leave permission decisions to the host.
    if name == "PermissionRequest":
        # This lifecycle adapter never decides host permissions.
        return {}  # Never grant host permissions.
    # Apply startup, task-free and implementation admission routes to tool work.
    if name == "PreToolUse":
        # A pending ticket read preserves exact startup and task-free routes.
        # Implementation waits until the selected issue transition establishes readiness.
        if startup_lookup_state(event, ".lookup-required.json"):
            # Existing exact preparation and diagnosis routes preserve their native contracts.
            if common.preparation_tool(event, "codex") or diagnostic_bootstrap(event):
                return {}
            # Task-free research remains available without consuming the selected ticket.
            if (
                not common.selected_ticket(event, "codex")
                and not common.effect_decision(event, "codex").requires_task
            ):
                return {}
            # Any other tool must wait for the assigned issue response.
            # Reject unrelated calls without consuming the ticket marker.
            if event.get("tool_name") not in TICKET_TOOLS:
                # Return a bounded admission denial to the host.
                return denial("TICKET_READ_REQUIRED")
            # Reserve the exact issue call and report validation failures.
            try:
                # The provider result will be checked in PostToolUse.
                ticket_lookup(event)
                return {}
            except (core.WorkspaceError, OSError, KeyError, TypeError) as error:
                # Fail closed when assignment or lookup state is invalid.
                code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
                return denial(code)
        # Allow only the exact scoped bootstrap route before ordinary readiness checks.
        if bootstrap(event):
            # The admitted bootstrap call establishes readiness itself.
            return {}
        # Validate correlated archive saves and reads before ordinary admission.
        try:
            # Allow the exact pending archive provider call only after gate validation.
            if provider_gate(event):
                # Admit only the correlated provider call.
                return {}
        except core.WorkspaceError:
            # Ordinary readiness evaluation supplies the final denial.
            pass
        tool = event.get("tool_name", "")
        # Native launch receipts admit readers only; coordinator scope remains explicit.
        if tool in codex_children.SPAWN_TOOLS:
            codex_children.admit_spawn(codex_children.identity(event))
            return {}
        if tool in codex_children.CONTROLS:
            codex_children.admit_control(codex_children.identity(event))
            return {}
        # Deny covered child creation until host child identity can be verified.
        if any(
            s in tool
            for s in (
                "multi_agent_v1",
                "spawn_agent",
                "create_thread",
                "fork_thread",
                "send_message_to_thread",
                "followup_task",
                "send_input",
                "close_agent",
                "resume_agent",
            )
        ):
            # Native child identity cannot be tied to a lifecycle participant.
            return denial("HOST_UNSUPPORTED_CHILD_IDENTITY")
        # Keep identity and permission clarification available to unready sessions.
        if tool in {"request_user_input", "request_user_input_async"}:
            # Clarification remains available while readiness is unresolved.
            return {}
        # Understood read/admin/note calls need no binding, readiness or pending record.
        decision = common.effect_decision(event, "codex")
        if not decision.requires_task and not (
            tool == "write_stdin" and common.pending_tool(event, "codex")
        ):
            return {}
        # Build the request from explicit caller or observed session identities.
        try:
            request = request_for(event, "ready")
        except core.WorkspaceError as error:
            # Unknown execution explains a supported reroute before task setup exists.
            if decision.effect == "unknown" and error.code in {
                "REGISTRATION_REQUIRED",
                "BINDING_MISSING",
            }:
                return denial(decision.code)
            return denial(error.code)
        result = core.execute(request)
        # Preserve the precise core failure in the host response.
        if not result["ok"]:
            # Reflect the core readiness failure without starting tool work.
            return denial(result["code"])
        # Avoid counting the lifecycle transaction itself as pending external work.
        if bootstrap(event, ready=True):
            # A ready lifecycle transaction is not tracked as external work.
            return {}
        # Bind an observed polling transport to its unique original operation.
        polling = tool == "write_stdin"
        poll_handle = None
        # Polling calls must carry the previously issued shell session handle.
        if polling:
            # Validate the handle before recording a new poll operation.
            try:
                # Uncorrelated poll handles cannot settle another tool's work.
                poll_handle = async_handle(event.get("tool_input", {}).get("session_id"))
                core.require(poll_handle is not None, "ASYNC_HANDLE_CONFLICT")
            except core.WorkspaceError as error:
                # Return the specific handle conflict as an admission denial.
                return denial(error.code)
        # Reserve and record the transport separately when the host emits its pre-hook.
        result = core.execute(
            {
                **request,
                "operation": "tool-start",
                "request_id": "pre:" + event["tool_use_id"],
                "tool_id": event["tool_use_id"],
                "poll_handle": poll_handle,
            }
        )
        return {} if result["ok"] else denial(result["code"])
    # Settle work only from typed completion facts and validated handle correlation.
    if name == "PostToolUse":
        # Preparation reads never registered ordinary pending work or granted readiness.
        if common.preparation_tool(event, "codex") and startup_lookup_state(
            event, ".lookup-required.json"
        ):
            return {}
        # Canonical lifecycle commands own their transactions and were not external tool work.
        if bootstrap(event, ready=True):
            return {}
        # A completed control call does not mean the child or its own pending tools finished.
        if event.get("tool_name") in codex_children.CONTROLS | codex_children.SPAWN_TOOLS:
            codex_children.parent_post(codex_children.identity(event))
            return {}
        # Settle a reserved Linear ticket read before generic tool completion.
        # The lookup marker identifies the pre-hook admitted issue call.
        if event.get("tool_name") in TICKET_TOOLS and common.ticket_callback(event, "codex"):
            # Verify the provider result and start the assigned workspace.
            try:
                # Successful startup returns a host-visible readiness message.
                lookup_result = ticket_lookup(event, complete=True)
                assert lookup_result is not None
                return lookup_result
            except (core.WorkspaceError, OSError, KeyError, TypeError) as error:
                # Explain the bounded ticket failure without granting readiness.
                code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
                return {
                    "systemMessage": (
                        "TASK_WORKSPACE_NOT_READY: "
                        + code
                        + (
                            " for " + str(event.get("tool_input", {}).get("id", ""))
                            if code == "ISSUE_NOT_FOUND"
                            else ""
                        )
                        + (
                            " Linear could not find requested issue "
                            + str(event.get("tool_input", {}).get("id", ""))
                            + "."
                            if code == "LINEAR_ISSUE_UNRESOLVED"
                            else ""
                        )
                    )
                }
        # A recorded native admission wins over changed paths or symlinks in a callback.
        try:
            # Malformed polling metadata is advisory after execution and settles no work.
            pending = common.pending_tool(event, "codex")
            if not pending:
                return {}
        except core.WorkspaceError as error:
            # Preserve native post-hook diagnostics instead of leaking an adapter exception.
            return {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + error.code}
        # Build the request from explicit caller or observed session identities.
        request = request_for(event, "tool-complete")
        response = event.get("tool_response", {})
        # Command-only native Bash and apply_patch emit text, not legacy transport metadata.
        # A final write_stdin poll uses this original call ID; stdout cannot supply handles.
        inputs = event.get("tool_input")
        terminal_text = (
            event.get("tool_name") in {"Bash", "apply_patch"}
            and isinstance(inputs, dict)
            and set(inputs) == {"command"}
            and isinstance(inputs.get("command"), str)
            and isinstance(response, str)
        )
        # Observed local web callbacks finish with content lists, not process handles.
        terminal_web = (
            event.get("tool_name") == "webrun"
            and isinstance(inputs, dict)
            and isinstance(response, list)
        )
        # Native MCP content results may omit the optional isError flag; PostToolUse
        # proves the synchronous call returned, without asserting provider success.
        terminal_mcp = (
            str(event.get("tool_name", "")).startswith("mcp__")
            and isinstance(inputs, dict)
            and isinstance(response, dict)
            and isinstance(response.get("content"), list)
            and all(
                isinstance(block, dict) and isinstance(block.get("type"), str)
                for block in response["content"]
            )
        )
        # Only the evidenced native shapes bypass legacy transport-result parsing.
        if terminal_text or terminal_web or terminal_mcp:
            observation = [event["tool_use_id"], True, None, False]
            result = core.execute(
                {
                    **request,
                    "request_id": "post:" + core.sha(core.canonical(observation)),
                    "tool_id": event["tool_use_id"],
                    "poll": False,
                    "completed": True,
                    "async_handle": None,
                }
            )
            # Settle pending work without claiming that the command exited successfully.
            return (
                {}
                if result["ok"]
                else {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + result["code"]}
            )
        # Decode serialized tool responses; malformed shapes supply no completion evidence.
        if isinstance(response, str):
            # A host-serialized result must be decoded before reading status fields.
            try:
                # Preserve typed completion facts from valid JSON responses.
                response = json.loads(response)
            except ValueError:
                # Invalid JSON carries no trustworthy completion facts.
                response = {}
        # Ignore non-object provider payloads for completion decisions.
        if not isinstance(response, dict):
            # Treat unexpected response shapes as absent metadata.
            response = {}
        # Unknown response shapes keep the operation pending; never infer completion.
        tool_name = event.get("tool_name", "")
        polling = tool_name == "write_stdin"
        # Validate supplied handle types before any completion or correlation decision.
        try:
            # Read the response handle, then compare it with a polling input handle.
            handle = async_handle(response.get("session_id"))
            # A poll can settle only the asynchronous session it targeted.
            if polling:
                # Reject missing or conflicting polling handles.
                input_handle = async_handle(event.get("tool_input", {}).get("session_id"))
                core.require(
                    input_handle is not None and (handle is None or handle == input_handle),
                    "ASYNC_HANDLE_CONFLICT",
                )
                handle = input_handle
        except core.WorkspaceError as error:
            # Surface handle conflicts without claiming operation completion.
            return {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + error.code}
        # Normalize response metadata without inferring completion from missing values.
        exit_code = response.get("exit_code")
        completed = type(exit_code) is int
        # Accept typed isError completion only outside asynchronous shell transport.
        if tool_name not in {"Bash", "exec_command", "write_stdin"}:
            # Synchronous tools can finish through an explicit error flag.
            completed = completed or type(response.get("isError")) is bool
        # Treat an explicit handle-free shell error as a finished failed call.
        elif response.get("isError") is True and handle is None:
            # A shell failure with no session handle is terminal.
            completed = True
        # Retain work when the response proves neither completion nor a valid handle.
        if not completed and handle is None:
            # Keep the operation pending until a final status or handle arrives.
            return {}
        # Key idempotency by completion facts so a later final response is not an old retry.
        observation = [event["tool_use_id"], completed, handle, polling]
        result = core.execute(
            {
                **request,
                "request_id": "post:" + core.sha(core.canonical(observation)),
                "tool_id": event["tool_use_id"],
                "poll": polling,
                "completed": completed,
                "async_handle": handle,
            }
        )
        # Preserve the precise core failure in the host response.
        if not result["ok"]:
            # The core owns the completion decision and its denial code.
            return {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + result["code"]}
        return {}
    # Report the unsupported child-identity boundary without granting readiness.
    if name == "SubagentStart":
        # The host has not provided a verifiable child-to-tool mapping.
        return {
            "systemMessage": (
                "HOST_UNSUPPORTED: Native child-to-tool identity is not verified. "
                "No child readiness is established."
            )
        }
    # Allow lifecycle recovery while preserving tool-level readiness enforcement.
    if name in {"SessionStart", "PreCompact", "PostCompact"}:
        # Recheck persisted task binding after a session transition.
        # Core validation may reject a stale or absent binding.
        try:
            # A recovered session receives only its existing task scope.
            result = core.execute(request_for(event, "ready"))
        except core.WorkspaceError as error:
            # Render validation failure as a bounded startup diagnostic.
            result = {"ok": False, "code": error.code}
        # Leave successful compaction hooks unobtrusive after checking readiness.
        if result["ok"] and name in {"PreCompact", "PostCompact"}:
            # Successful compaction needs no repeated task context.
            return {}
        # Expose successful binding context without copying task-note content.
        if result["ok"]:
            # Tell the host how to retrieve the permitted packet.
            return {
                "hookSpecificOutput": {
                    "hookEventName": name,
                    "additionalContext": (
                        "Task binding checked. For implementation work, retrieve the permitted "
                        "packet through the lifecycle read operation. Research, inspection, "
                        "search, administration and ordinary notes remain available."
                    ),
                }
            }
        # An absent binding is the normal task-free startup state, not a global failure.
        if result["code"] in {"REGISTRATION_REQUIRED", "BINDING_MISSING"}:
            return {
                "hookSpecificOutput": {
                    "hookEventName": name,
                    "additionalContext": (
                        "Inspection, administration and ordinary notes are available "
                        "without a task. "
                        "Before implementation changes, use the Task ticket/readiness procedure."
                    ),
                }
            }
        return {
            "hookSpecificOutput": {
                "hookEventName": name,
                "additionalContext": "TASK_WORKSPACE_NOT_READY: "
                + result["code"]
                + ". Inspection, administration and ordinary notes remain available. "
                "Implementation changes require the assigned lifecycle "
                "diagnostic/bootstrap routes to register, resume, read and acknowledge "
                "the permitted packet, or repair the reported condition. Readiness was not "
                "granted.",
            }
        }
    # Record only bounded optional facts for advisory lifecycle endings.
    if name in {"Stop", "Interrupt", "SessionEnd", "SubagentStop"}:
        # Exit hooks record an optional observation without settling work.
        # Missing workspace state must not break an advisory host event.
        try:
            # Build the request from explicit caller or observed session identities.
            request = request_for(event, "event")
            core.execute(
                {
                    **request,
                    "event_type": "observation",
                    "event": {"code": "INTERRUPTED" if name == "Interrupt" else "UNKNOWN"},
                }
            )
        except (core.WorkspaceError, OSError):
            # A failed observation has no authority over readiness or completion.
            pass  # Optional observations cannot establish completion or retirement.
    return {}


def failure(name: str, code: str) -> core.JSONObject:
    """Render Codex's existing event-specific failure response.

    Args:
        name: Native hook event name.
        code: Bounded diagnostic code.

    Returns:
        A denial for admission events, or an advisory diagnostic.
    """
    # Admission failures must block the tool rather than merely notify the host.
    if name == "PreToolUse":
        # Reuse the standard pre-tool denial structure.
        return denial(code)
    # Prompt failures must block task work before tool calls begin.
    if name == "UserPromptSubmit":
        # Attach the bounded diagnostic to the blocking prompt response.
        return {"decision": "block", "reason": "TASK_WORKSPACE_NOT_READY: " + code}
    return {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + code}


def main() -> int:
    """Run one supervised Codex hook using its existing exit-zero response convention.

    Returns:
        Zero after emitting the supported event response, including bounded failures.
    """
    return runner.run(handle, failure, error_status=0)


# Execute the hook protocol only for direct script invocation.
if __name__ == "__main__":
    # Preserve Codex's exit-zero hook response convention.
    sys.exit(main())
