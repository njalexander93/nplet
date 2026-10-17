"""Bind observed Codex child UUIDs to reader-only lifecycle participants.

Launch receipts authorize a bounded reader cohort, never a write assignment.
The coordinator scopes observed participant keys explicitly; launch order and
model-facing task names never select an owner.
"""

from __future__ import annotations

import json
import shlex
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core

SPAWN = "collaborationspawn_agent"
SPAWN_TOOLS = {SPAWN, "spawn_agent"}
MAX_LAUNCHES = 32
MAX_PENDING_READS = 256
TARGET_CONTROLS = {
    "collaborationfollowup_task",
    "collaborationsend_message",
    "collaborationinterrupt_agent",
    "multi_agent_v1send_input",
    "multi_agent_v1close_agent",
    "multi_agent_v1wait_agent",
}
CONTROLS = TARGET_CONTROLS | {
    "collaborationwait_agent",
    "collaborationlist_agents",
    "multi_agent_v1wait_agent",
}


def identity(event: core.JSONObject) -> core.JSONObject:
    """Normalize an observed UUID under its native parent without accepting derived input.

    Args:
        event: Raw Codex callback with parent session and optional native child UUID.

    Returns:
        A copy with separate child and parent lifecycle identities.

    Raises:
        WorkspaceError: Missing, malformed or ambiguous identity.
    """
    # Treat internal markers as derived state, never caller-provided authority.
    result = {k: v for k, v in event.items() if k not in {"child", "parent_session_id"}}
    core.require(
        event.get("is_background_agent", False) is False
        and event.get("subagent_id") is None
        and event.get("parent_conversation_id") is None,
        "HOST_UNSUPPORTED_CHILD_IDENTITY",
    )
    session = common.native_token(event.get("session_id"))
    core.require("/agent/" not in session, "HOST_UNSUPPORTED_CHILD_IDENTITY")
    core.require(
        isinstance(event.get("cwd"), str) and Path(event["cwd"]).is_absolute(),
        "REPOSITORY_MISMATCH",
    )
    # Parent callbacks carry no child identity and must not carry a child-only type.
    agent = event.get("agent_id")
    if agent is None:
        core.require(event.get("agent_type") is None, "HOST_UNSUPPORTED_CHILD_IDENTITY")
        return result
    # Native UUID spelling is exact; aliases and synthetic path segments are rejected.
    try:
        valid = isinstance(agent, str) and str(uuid.UUID(agent)) == agent
    # Malformed UUID input cannot establish native child identity.
    except (ValueError, AttributeError):
        valid = False
    core.require(valid, "HOST_UNSUPPORTED_CHILD_IDENTITY")
    result.update(child=True, parent_session_id=session, session_id=session + "/agent/" + agent)
    return result


def parent_request(event: core.JSONObject) -> core.JSONObject:
    """Resolve the current parent binding without borrowing it for child tool work.

    Args:
        event: Normalized parent or child envelope.

    Returns:
        Parent request used only for cohort admission and registry selection.

    Raises:
        WorkspaceError: The actual parent has no current binding.
    """
    # Select the observed parent session before resolving its current lifecycle binding.
    native = {**event, "session_id": event.get("parent_session_id", event["session_id"])}
    return common.request_for(native, "ready", "codex")


@contextmanager
def registry(parent: core.JSONObject) -> Iterator[tuple[core.Directory, str, core.JSONObject]]:
    """Lock the parent-generation reader cohort independently of issue transactions.

    Args:
        parent: Resolved native parent binding.

    Yields:
        Safe directory, record filename and generation-bound registry contents.

    Raises:
        WorkspaceError: Foreign issue state or unsafe storage.
        OSError: Registry access or lock acquisition fails.
    """
    # Keep generation-specific receipts so rebinds cannot reuse an earlier authorization.
    root, _, _ = core.repository(parent["worktree"])
    key = core.participant_key(parent)
    name = key + ".codex-children-" + str(parent["binding_generation"]) + ".json"
    # Serialize generation-bound receipt reads and writes without holding an issue lock.
    with (
        core.Directory.absolute(root) as directory,
        directory.child(".task") as task,
        task.child(".bindings") as bindings,
        bindings.lock("codex-children.lock"),
    ):
        state = (
            bindings.json(name)
            if bindings.exists(name)
            else {"issue_id": parent["issue_id"], "launches": {}, "children": {}}
        )
        core.require(state["issue_id"] == parent["issue_id"], "BINDING_CONFLICT")
        yield bindings, name, state


def admit_spawn(event: core.JSONObject) -> None:
    """Reserve one bounded native launch from the ready coordinator.

    Args:
        event: Normalized parent spawn callback; opaque message text grants no scope.

    Raises:
        WorkspaceError: Unready/non-coordinator parent, unsupported arguments or replay.
    """
    # Nested launches and inherited-history/worktree routes are outside this native route.
    core.require(event.get("child") is not True, "HOST_UNSUPPORTED_CHILD_IDENTITY")
    args = event.get("tool_input")
    native = event.get("tool_name") == "spawn_agent"
    core.require(isinstance(args, dict), "HOST_UNSUPPORTED_CHILD_IDENTITY")
    assert isinstance(args, dict)  # Established by the argument gate above.
    call_id = common.native_token(event.get("tool_use_id"))
    # The current host has no task_name/fork_turns fields; ignored extra fields grant nothing.
    if native:
        core.require(
            set(args) <= {"message", "fork_context", "model", "reasoning_effort"}
            and args.get("fork_context") is False
            and isinstance(args.get("message"), str)
            and bool(args["message"].strip())
            and all(
                isinstance(args[field], str)
                for field in ("model", "reasoning_effort")
                if field in args
            ),
            "HOST_UNSUPPORTED_CHILD_IDENTITY",
        )
        task_name = call_id
    # Preserve the separately observed legacy contract and its explicit task-name boundary.
    else:
        core.require(
            event.get("tool_name") == SPAWN
            and set(args) <= {"task_name", "message", "fork_turns", "model", "reasoning_effort"}
            and args.get("fork_turns") == "none"
            and isinstance(args.get("message"), str),
            "HOST_UNSUPPORTED_CHILD_IDENTITY",
        )
        task_name = common.native_token(args.get("task_name"))
        core.require("/" not in task_name, "INVALID_REQUEST")
    # Launch authority belongs only to the current ready coordinator.
    parent = parent_request(event)
    ready = core.execute(parent)
    core.require(ready["ok"], ready["code"])
    issue = common._issue_state(parent)
    core.require(issue["coordinator"] == core.participant_key(parent), "NOT_OWNER")
    # Only this exact native call can reserve a reader slot; unrelated pending work cannot.
    receipt: core.JSONObject = {
        "task_name": task_name,
        "arguments_digest": core.sha(core.canonical(args)),
    }
    # Preserve the native protocol discriminator without upgrading legacy receipts.
    if native:
        receipt["native"] = True
    with registry(parent) as (bindings, name, state):
        launches = state["launches"]
        # Exact unresolved retries reuse their receipt; completed work cannot launch again.
        if call_id in launches:
            core.require(
                all(launches[call_id].get(k) == v for k, v in receipt.items())
                and not launches[call_id].get("completed"),
                "REQUEST_CONFLICT",
            )
            # A registry receipt cannot resurrect execution already settled in the core.
            current = common._issue_state(parent)
            core.require(
                call_id in current["participants"][core.participant_key(parent)]["pending"],
                "REQUEST_CONFLICT",
            )
            return
        # New launches require retained capacity and a name unused by this generation.
        core.require(len(launches) < MAX_LAUNCHES, "HOST_CHILD_CAPACITY_EXHAUSTED")
        core.require(
            all(item["task_name"] != task_name for item in launches.values()), "REQUEST_CONFLICT"
        )
        # Record authoritative parent work before persisting its launch authorization.
        started = core.execute(
            {
                **parent,
                "operation": "tool-start",
                "request_id": "pre:" + call_id,
                "tool_id": call_id,
            }
        )
        core.require(started["ok"], started["code"])
        launches[call_id] = receipt
        bindings.put(name, state)


def start(event: core.JSONObject) -> str:
    """Join a native child into the admitted cohort with no write ownership.

    Args:
        event: Identity-validated native SubagentStart envelope.

    Returns:
        Exact child identity and bootstrap instructions, without assigning a step by order.

    Raises:
        WorkspaceError: Foreign parent, exhausted cohort, stale binding or join failure.
    """
    # Confirm current parent readiness; native identity alone is not join authorization.
    core.require(event.get("child") is True, "HOST_UNSUPPORTED_CHILD_IDENTITY")
    parent = parent_request(event)
    ready = core.execute(parent)
    core.require(ready["ok"], ready["code"])
    child_key = core.participant_key({"host": "codex", "session_id": event["session_id"]})
    # Reserve the UUID once. Slots admit readers only and never select their write scope.
    with registry(parent) as (bindings, name, state):
        children = state["children"]
        if child_key not in children:
            core.require(len(children) < len(state["launches"]), "HOST_UNSUPPORTED_CHILD_IDENTITY")
            children[child_key] = {"agent_id": event["agent_id"], "status": "joining"}
            bindings.put(name, state)
        # Preserve an existing issue assignment and reject foreign bindings before joining.
        assignment = child_key + ".assignment.json"
        if bindings.exists(assignment):
            core.require(
                bindings.json(assignment)["issue_id"] == parent["issue_id"], "BINDING_CONFLICT"
            )
        bindings.put(assignment, {"issue_id": parent["issue_id"]})
    # Reuse bounded join retries, preserving an existing child's explicit scope on replay.
    common._join_child(event, "codex", parent, time.monotonic() + common.CHILD_JOIN_BUDGET_SECONDS)
    # Publish joined status only after the shared lifecycle accepted this child.
    with registry(parent) as (bindings, name, state):
        state["children"][child_key]["status"] = "joined"
        bindings.put(name, state)
    # Supply exact commands for the actual reader packet, including managed-note paths.
    return bootstrap_context(event)


def bootstrap_context(event: core.JSONObject) -> str:
    """Build executable recovery instructions for an already joined native child.

    Args:
        event: Verified normalized child identity and checkout.

    Returns:
        Ordered source-delivery and lifecycle commands for the current packet.

    Raises:
        WorkspaceError: The child's binding or current packet cannot be read.
    """
    # Read the child's current packet without acknowledging or granting readiness.
    request = common.request_for(event, "read", "codex")
    packet = core.execute(request)
    core.require(packet["ok"], packet["code"])
    commands = [common.bootstrap_command(request, "codex")]
    # Resolve managed locators through the canonical store, including linked checkouts.
    for path in source_paths(request, packet):
        commands.append("cat -- " + shlex.quote(path))
    # Give each transaction its own request ID and bind acknowledgment to this packet.
    commands.append(
        common.bootstrap_command(
            {
                **request,
                "operation": "acknowledge",
                "request_id": str(uuid.uuid4()),
                "packet_digest": packet["packet_digest"],
            },
            "codex",
        )
    )
    commands.append(
        common.bootstrap_command(
            {
                **request,
                "operation": "ready",
                "request_id": str(uuid.uuid4()),
            },
            "codex",
        )
    )
    # Return instructions tied to the observed participant and its current packet.
    key = core.participant_key(request)
    return (
        f"Native child participant {key}; session_id={event['session_id']}; host=codex. "
        "Reader only until the coordinator explicitly assigns write ownership. "
        "The numbered commands bootstrap your current reader packet. Execute them in order "
        "unless the coordinator has already supplied a refreshed assignment and commands. "
        "Read full source output before acknowledging; use a sufficient output budget. "
        "Later coordinator assignments may replace these initial commands. Follow refreshed "
        "canonical commands for your own read, acknowledge, ready, diagnose and scoped update "
        "operations, plus exact assigned-source cat commands. Use a fresh request_id for each "
        "new transaction. Request IDs below are examples for this initial bootstrap, not a "
        "permanent allowlist. Code-mode scripts may construct and invoke these same canonical "
        "commands; inspect the returned source bytes before a later acknowledgment call. "
        "An update requires explicit ownership of its context path and current read, "
        "acknowledgment and readiness. On a revision conflict, reread and retry with a new "
        "request_id within the coordinator's retry bound. On other failures or a changed "
        "packet, stop and request refreshed instructions. Preserve the observed identity "
        "above in every command. Generic shell commands, provider calls, nested delegation "
        "and writes outside your assigned paths remain forbidden.\n"
        + "\n".join(f"{index}. {command}" for index, command in enumerate(commands, 1))
    )


def source_paths(request: core.JSONObject, packet: core.JSONObject) -> dict[str, str]:
    """Resolve available packet sources against the registered canonical task store.

    Args:
        request: The child's verified lifecycle identity and checkout.
        packet: Its successful lifecycle read result.

    Returns:
        Exact absolute source paths mapped to their expected content digests.

    Raises:
        WorkspaceError: The registered store cannot be validated.
    """
    # Use the registration's main store rather than a linked worktree's issue-view symlink.
    with core.Store(request) as store:
        main = Path(store.registration["main"])
    # External absolute references retain their explicitly assigned locations.
    paths = {}
    for ref in packet["references"]:
        # Unavailable optional sources cannot become admitted source-read commands.
        if ref["available"]:
            path = Path(ref["locator"])
            # Managed locators resolve through the canonical store rather than a worktree view.
            if not path.is_absolute():
                path = main / ".task" / request["issue_id"] / path
            paths[str(path)] = ref["sha256"]
    return paths


def child_pre(event: core.JSONObject) -> None:
    """Admit only assigned source reads and the child's own bounded lifecycle operations.

    Args:
        event: Identity-validated child PreToolUse event.

    Raises:
        WorkspaceError: Unregistered child, forbidden tool, wrong identity or write scope.
    """
    # Require an observed join in the current parent generation, not a fabricated child flag.
    parent = parent_request(event)
    key = core.participant_key({"host": "codex", "session_id": event["session_id"]})
    with registry(parent) as (_bindings, _name, state):
        core.require(state["children"].get(key, {}).get("status") == "joined", "BINDING_MISSING")
    # Generic provider authority is not part of the restricted native child route.
    core.require(
        not str(event.get("tool_name", "")).startswith(("mcp__", "MCP:")), "HOST_UNSUPPORTED_TOOL"
    )
    # Task-free native reads retain this child's verified cohort identity.
    decision = common.effect_decision(event, "codex")
    if event.get("tool_name") != "Bash" and decision.effect == "read":
        return
    # Limit protected child execution to canonical lifecycle operations under its own scope.
    core.require(event.get("tool_name") == "Bash", "HOST_UNSUPPORTED_TOOL")
    args = event.get("tool_input")
    core.require(isinstance(args, dict) and set(args) == {"command"}, "INVALID_REQUEST")
    assert isinstance(args, dict)  # Established by the native command shape above.
    command = args["command"]
    # Exact read-only recovery must remain available when the assigned source is stale.
    # Canonical validation keeps the observed child's identity and diagnostic field bounds.
    if common.canonical_bootstrap(event, command, "codex", False):
        diagnostic = common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE)
        # Only read-only diagnosis bypasses source freshness checks.
        if diagnostic.get("operation") == "diagnose":
            return
    # Source delivery before acknowledgment is restricted to exact assigned source paths.
    child = common.request_for(event, "read", "codex")
    packet = core.execute(child)
    # Stale packet bytes cannot prove delivery, but they cannot block ordinary inspection.
    if not packet["ok"] and decision.effect == "read":
        core.require(packet["code"] in {"SOURCE_STALE", "NOT_READY"}, packet["code"])
        return
    core.require(packet["ok"], packet["code"])
    paths = source_paths(child, packet)
    # Match exact source commands before considering lifecycle operations.
    for path in paths:
        if command == "cat -- " + shlex.quote(str(path)):
            # Preparation reads have their own native receipts before lifecycle readiness.
            call_id = common.native_token(event.get("tool_use_id"))
            with registry(parent) as (bindings, name, state):
                reads = state["children"][key].setdefault("source_reads", {})
                receipt = {"path": str(path), "packet_digest": packet["packet_digest"]}
                # Retire only completed evidence from obsolete packets; unknown work remains.
                for previous in list(reads):
                    item = reads[previous]
                    if (
                        item.get("completed")
                        and item.get("packet_digest") != packet["packet_digest"]
                    ):
                        del reads[previous]
                # An unresolved exact retry retains its original source and packet.
                if call_id in reads:
                    core.require(
                        all(reads[call_id].get(k) == v for k, v in receipt.items())
                        and not reads[call_id].get("completed"),
                        "REQUEST_CONFLICT",
                    )
                # New delivery work consumes only unresolved-read capacity.
                else:
                    core.require(
                        sum(not item.get("completed") for item in reads.values())
                        < MAX_PENDING_READS,
                        "RECOVERY_REQUIRED",
                    )
                    # Reserve authoritative pending work before admitting native execution.
                    # If the receipt write fails, uncertainty remains in the core, not hidden.
                    locator = next(
                        ref["locator"]
                        for ref in packet["references"]
                        if path in source_paths(child, {"references": [ref]})
                    )
                    started = core.execute(
                        {
                            **child,
                            "operation": "source-read-start",
                            "request_id": "source-pre:" + call_id,
                            "tool_id": call_id,
                            "packet_digest": packet["packet_digest"],
                            "source_locator": locator,
                        }
                    )
                    core.require(started["ok"], started["code"])
                    reads[call_id] = receipt
                # Replays and legacy receipts cannot admit execution after core settlement.
                core.require(
                    call_id in common._issue_state(child)["participants"][key]["pending"],
                    "REQUEST_CONFLICT",
                )
                bindings.put(name, state)
            return
    # Other understood inspection calls create no lifecycle pending work.
    if decision.effect == "read":
        return
    # Every protected mutation is a canonical core transaction under this child's identity.
    core.require(common.canonical_bootstrap(event, command, "codex", True), "HOST_UNSUPPORTED_TOOL")
    request = common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE)
    operation = request.get("operation")
    core.require(operation in {"read", "acknowledge", "ready", "update", "diagnose"}, "NOT_OWNER")
    # Acknowledgment requires actual source bytes from this child's current packet delivery.
    if operation == "acknowledge":
        with registry(parent) as (_bindings, _name, state):
            delivered = {
                item["path"]: item.get("content_sha256")
                for item in state["children"][key].get("source_reads", {}).values()
                if item.get("packet_digest") == packet["packet_digest"] and item.get("completed")
            }
        core.require(
            all(delivered.get(path) == digest for path, digest in paths.items()),
            "SOURCE_DELIVERY_REQUIRED",
        )
    # Managed-note writes require both current readiness and explicit ownership.
    if operation == "update":
        ready = core.execute({**child, "operation": "ready"})
        core.require(ready["ok"], ready["code"])
        issue = common._issue_state(child)
        core.require(issue["owners"].get(str(request.get("path"))) == key, "NOT_OWNER")


def child_post(event: core.JSONObject) -> None:
    """Settle a child's own source-delivery receipt from its native final callback.

    Args:
        event: Identity-validated child completion event.

    Raises:
        WorkspaceError: Missing cohort membership or changed completion bytes.
    """
    # Never consult or settle the parent participant's pending work for a child result.
    parent = parent_request(event)
    key = core.participant_key({"host": "codex", "session_id": event["session_id"]})
    with registry(parent) as (bindings, name, state):
        core.require(key in state["children"], "BINDING_MISSING")
        reads = state["children"][key].get("source_reads", {})
        call_id = event.get("tool_use_id")
        # Only the exact admitted command and a final textual response can prove delivery.
        if (
            call_id in reads
            and event.get("tool_name") == "Bash"
            and isinstance(event.get("tool_response"), str)
            and event.get("tool_input")
            == {"command": "cat -- " + shlex.quote(reads[call_id]["path"])}
        ):
            call_id = common.native_token(call_id)
            receipt = reads[call_id]
            content_digest = core.sha(event["tool_response"].encode())
            # A settled callback cannot replace its original bytes through cached core success.
            if receipt.get("completed"):
                core.require(receipt.get("content_sha256") == content_digest, "REQUEST_CONFLICT")
                return
            # Bind core retry identity to the bytes too, covering a later registry-write failure.
            completion_id = "source-post:" + core.sha(
                core.canonical({"call_id": call_id, "content_sha256": content_digest})
            )
            # A correlated final response settles only this child's authoritative tool ID.
            completed = core.execute(
                {
                    **common.request_for(event, "tool-complete", "codex"),
                    "request_id": completion_id,
                    "tool_id": call_id,
                    "completed": True,
                }
            )
            core.require(completed["ok"], completed["code"])
            receipt["completed"] = True
            receipt["content_sha256"] = content_digest
            # Keep the latest delivery per source in this packet, retaining every unresolved
            # read. A delayed old-packet completion cannot erase current delivery evidence.
            for previous in list(reads):
                item = reads[previous]
                if (
                    previous != call_id
                    and item.get("completed")
                    and (
                        item.get("packet_digest") == receipt["packet_digest"]
                        and item.get("path") == receipt["path"]
                    )
                ):
                    del reads[previous]
            bindings.put(name, state)


def child_end(event: core.JSONObject) -> None:
    """Retain an advisory child ending without settling unfinished source delivery.

    Args:
        event: Verified native ending callback carrying the child and turn identity.

    Raises:
        WorkspaceError: The child is not joined in the current parent generation.
    """
    # Require the same cohort membership as tool admission; ending cannot attach a reader.
    parent = parent_request(event)
    key = core.participant_key({"host": "codex", "session_id": event["session_id"]})
    with registry(parent) as (bindings, name, state):
        member = state["children"].get(key)
        core.require(
            isinstance(member, dict) and member.get("status") == "joined", "BINDING_MISSING"
        )
        # Store only the latest bounded observation; retain unresolved source receipts intact.
        member["last_ending"] = {
            "event": event["hook_event_name"],
            "turn_id": common.native_token(event.get("turn_id")),
        }
        bindings.put(name, state)


def admit_control(event: core.JSONObject) -> None:
    """Restrict parent follow-up, message and interruption to its observed launch targets.

    Args:
        event: Normalized parent control callback.

    Raises:
        WorkspaceError: Unready parent, nested control or target absent from this generation.
    """
    # A control cannot turn an unrelated thread into a child of this issue.
    core.require(event.get("child") is not True, "HOST_UNSUPPORTED_CHILD_IDENTITY")
    parent = parent_request(event)
    ready = core.execute(parent)
    core.require(ready["ok"], ready["code"])
    args = event.get("tool_input")
    core.require(isinstance(args, dict), "INVALID_REQUEST")
    assert isinstance(args, dict)
    native = event.get("tool_name") in {
        "multi_agent_v1send_input",
        "multi_agent_v1close_agent",
        "multi_agent_v1wait_agent",
    }
    # Native controls support only the captured scalar schemas, never structured messages.
    if event.get("tool_name") == "multi_agent_v1send_input":
        core.require(
            set(args) <= {"target", "message", "interrupt"}
            and isinstance(args.get("message"), str)
            and bool(args["message"].strip())
            and ("interrupt" not in args or type(args["interrupt"]) is bool),
            "INVALID_REQUEST",
        )
    # Closing a child accepts its recorded target alone, without additional side effects.
    elif event.get("tool_name") == "multi_agent_v1close_agent":
        core.require(set(args) == {"target"}, "INVALID_REQUEST")
    # Wait calls name a nonempty target list and at most a numeric timeout.
    elif event.get("tool_name") == "multi_agent_v1wait_agent":
        core.require(
            set(args) <= {"targets", "timeout_ms"}
            and isinstance(args.get("targets"), list)
            and bool(args["targets"])
            and all(isinstance(target, str) for target in args["targets"])
            and ("timeout_ms" not in args or type(args["timeout_ms"]) in {int, float}),
            "INVALID_REQUEST",
        )
    # Scope target-bearing calls; legacy wait/list calls have no target parameter.
    receipt = control_receipt(event)
    call_id = common.native_token(event.get("tool_use_id"))
    with registry(parent) as (bindings, name, state):
        # Every requested native or legacy target must belong to its own protocol cohort.
        if event.get("tool_name") in TARGET_CONTROLS:
            targets = {
                item["target"]
                for item in state["launches"].values()
                if "target" in item and (item.get("native") is True) == native
            }
            requested = (
                args["targets"]
                if event.get("tool_name") == "multi_agent_v1wait_agent"
                else [args.get("target")]
            )
            core.require(
                all(isinstance(target, str) and target in targets for target in requested),
                "HOST_UNSUPPORTED_CHILD_IDENTITY",
            )
        # Keep exact active-call correlation independently of shared core request retries.
        controls = state.setdefault("controls", {})
        core.require(call_id not in controls or controls[call_id] == receipt, "REQUEST_CONFLICT")
        result = core.execute(
            {
                **parent,
                "operation": "tool-start",
                "request_id": "pre:" + call_id,
                "tool_id": call_id,
            }
        )
        core.require(result["ok"], result["code"])
        # A cached pre-hook cannot authorize another control after its work has settled.
        current = common._issue_state(parent)
        core.require(
            call_id in current["participants"][core.participant_key(parent)]["pending"],
            "REQUEST_CONFLICT",
        )
        controls[call_id] = receipt
        bindings.put(name, state)


def control_receipt(event: core.JSONObject) -> core.JSONObject:
    """Describe the exact supported control without retaining message contents.

    Args:
        event: Native callback carrying the tool name and original arguments.

    Returns:
        Tool name and canonical argument digest used for pre/post correlation.

    Raises:
        WorkspaceError: The control is unsupported or arguments are not a JSON object.
    """
    # Reject unrecognized callbacks before constructing their persistent signature.
    core.require(event.get("tool_name") in CONTROLS, "HOST_UNSUPPORTED_TOOL")
    core.require(isinstance(event.get("tool_input"), dict), "INVALID_REQUEST")
    return {
        "tool_name": event["tool_name"],
        "arguments_digest": core.sha(core.canonical(event["tool_input"])),
    }


def complete_control(event: core.JSONObject, parent: core.JSONObject, call_id: str) -> None:
    """Settle an exact observed control and compact its completed adapter receipt.

    Args:
        event: Native final callback with original tool arguments.
        parent: Current parent lifecycle request.
        call_id: Validated native tool call identifier.

    Raises:
        WorkspaceError: Missing admission, changed tool/arguments or core settlement failure.
    """
    # Bind the core completion retry ID to the exact adapter signature, not only the call ID.
    receipt = control_receipt(event)
    request_id = "control-post:" + core.sha(core.canonical({"call_id": call_id, **receipt}))
    with registry(parent) as (bindings, name, state):
        controls = state.setdefault("controls", {})
        # Pending completion must match the original tool and arguments exactly.
        if call_id in controls:
            core.require(controls[call_id] == receipt, "REQUEST_CONFLICT")
        # A compacted receipt can replay only cached completion, never settle pending work.
        else:
            current = common._issue_state(parent)
            core.require(
                call_id not in current["participants"][core.participant_key(parent)]["pending"],
                "UNKNOWN_OPERATION",
            )
        # Settle only this correlated parent transport through the shared lifecycle.
        result = core.execute(
            {
                **parent,
                "operation": "tool-complete",
                "request_id": request_id,
                "tool_id": call_id,
                "completed": True,
                "async_handle": None,
                "poll": False,
            }
        )
        core.require(result["ok"], result["code"])
        # Core idempotency retains the exact completion; only unresolved adapter work remains.
        controls.pop(call_id, None)
        bindings.put(name, state)


def parent_post(event: core.JSONObject) -> None:
    """Settle one observed native collaboration call without inferring child completion.

    Args:
        event: Parent PostToolUse envelope for a supported collaboration tool.

    Raises:
        WorkspaceError: Missing correlation or ambiguous native response.
    """
    # Native PostToolUse is terminal for these synchronous control invocations.
    core.require(event.get("tool_response") is not None, "INVALID_REQUEST")
    parent = parent_request(event)
    call_id = common.native_token(event.get("tool_use_id"))
    # Controls require their original tool and arguments before any pending work settles.
    if event.get("tool_name") not in SPAWN_TOOLS:
        complete_control(event, parent, call_id)
        return
    # Decode serialized spawn responses without treating malformed output as target evidence.
    response = event["tool_response"]
    if isinstance(response, str):
        try:
            response = json.loads(response)
        # Retain an unknown launch outcome when the host response is not valid JSON.
        except ValueError:
            response = None
    # The host-returned target is a control address only, never a lifecycle identity.
    with registry(parent) as (bindings, name, state):
        core.require(call_id in state["launches"], "UNKNOWN_OPERATION")
        launch = state["launches"][call_id]
        core.require(
            (launch.get("native") is True) == (event.get("tool_name") == "spawn_agent"),
            "BINDING_CONFLICT",
        )
        # Bind completion to the admitted invocation before recording any target or settlement.
        core.require(
            launch.get("arguments_digest") == core.sha(core.canonical(event.get("tool_input"))),
            "REQUEST_CONFLICT",
        )
        # Decode target absence explicitly so a completed unknown outcome cannot gain authority.
        target = None
        # Only new native-contract receipts accept UUID targets; old unknown work stays unknown.
        if event.get("tool_name") == "spawn_agent":
            # Native success supplies a canonical UUID control address, never write ownership.
            if isinstance(response, dict) and "agent_id" in response:
                target = response["agent_id"]
                try:
                    valid = isinstance(target, str) and str(uuid.UUID(target)) == target
                # Reject malformed host target identities before recording or settling work.
                except (ValueError, AttributeError):
                    valid = False
                core.require(valid, "HOST_UNSUPPORTED_CHILD_IDENTITY")
        # Legacy completion must preserve the admitted task name and any prior target.
        elif isinstance(response, dict) and isinstance(response.get("task_name"), str):
            target = response["task_name"]
            core.require(target.rsplit("/", 1)[-1] == launch["task_name"], "BINDING_CONFLICT")
        # Freeze target absence as well as its value before any receipt or core mutation.
        if launch.get("completed") or "target" in launch:
            core.require(launch.get("target") == target, "REQUEST_CONFLICT")
        # Persist the first terminal observation before settlement; exact retries still settle
        # core work if an earlier completion stopped after this registry write.
        if not launch.get("completed"):
            if target is not None:
                launch["target"] = target
            # A control address does not prove a child started; unknown outcomes retain capacity.
            launch["outcome"] = "target_observed" if target is not None else "unknown"
            launch["completed"] = True
            bindings.put(name, state)
    # Pending work belongs to this exact parent call; native child work has a different key.
    result = core.execute(
        {
            **parent,
            "operation": "tool-complete",
            "request_id": "collaboration-post:" + call_id,
            "tool_id": call_id,
            "completed": True,
            "async_handle": None,
            "poll": False,
        }
    )
    core.require(result["ok"], result["code"])
