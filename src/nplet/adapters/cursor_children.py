"""Classify Cursor children through exact issue routes and revalidated retained invocations."""

from __future__ import annotations

import copy
import hashlib
import math
import shlex
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from nplet.adapters import common, cursor_results, runner
from nplet.adapters import cursor_routing as routing
from nplet.adapters.session_repository import session_repository
from nplet.lifecycle import task_workspace as core

ENTRY = Path(__file__).resolve()
TASK_FIELDS = frozenset(
    {"description", "environment", "model", "prompt", "run_in_background", "subagent_type"}
)


def call_token(value: object) -> str:
    """Normalize exact opaque native call bytes into the verified Cursor namespace.

    Args:
        value: Nonempty native string, at most160 strict UTF8bytes.

    Returns:
        Domain-separated stable call token; native bytes are retained separately.

    Raises:
        core.WorkspaceError: Absent, oversized or malformed native identity.
    """
    # Never trim, coerce, normalize newlines or hash already normalized tokens specially.
    core.require(isinstance(value, str) and 0 < len(value) <= 160, "INVALID_REQUEST")
    assert isinstance(value, str)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise core.WorkspaceError("INVALID_REQUEST") from error
    core.require(len(encoded) <= 160 and b"\0" not in encoded, "INVALID_REQUEST")
    return "cursor-call-v1:" + hashlib.sha256(b"cursor-call-v1\0" + encoded).hexdigest()


def registration(worktree: str) -> core.JSONObject:
    """Resolve only the registered checkout identity without inventing a session.

    Args:
        worktree: Observed native cwd or adapter CLI's actual cwd.

    Returns:
        Minimal Store configuration with verified repository identity.

    Raises:
        core.WorkspaceError: Registration discovery fails or is absent.
    """
    # Registration diagnose has no participant authority and needs no fabricated identity.
    request = {
        "schema_version": 1,
        "operation": "diagnose",
        "request_id": str(uuid.uuid4()),
        "worktree": worktree,
    }
    result = core.execute(request)
    core.require(result["ok"] and result["code"] == "REGISTERED", result["code"])
    return {**request, "repo_id": result["repo_id"]}


def issue_records(
    store: core.Store,
    deadline: float,
    select: Callable[[core.JSONObject], bool],
    issue: str,
) -> list[core.JSONObject]:
    """Recover one explicit issue and copy matching native invocation metadata.

    Args:
        store: Verified controls; caller holds the existing registry mutex.
        deadline: Shared operation deadline.
        select: Exact identity predicate, independent of task prose.
        issue: Explicit selector from a durable route or verified native parent.

    Returns:
        Matching committed metadata from this issue alone.

    Raises:
        core.WorkspaceError: Selected state or invocation metadata is invalid.
        OSError: Selected storage cannot be safely recovered or read.
    """
    # Recover only the selected issue under its existing lock.
    identifier = core.issue_id(issue)
    core.require(store.issues_present, "RECOVERY_REQUIRED")
    cursor_results.check_deadline(deadline)
    with store.issues.child(identifier) as control, control.lock(create=False):
        current = core.Issue(store, control, identifier)
        current.recover()
        state = current.committed_state()
        core.require(state.get("issue_id") == identifier, "INTEGRITY_ERROR")
        invocations = state.get("cursor_invocations", {})
        core.require(
            isinstance(invocations, dict) and isinstance(state.get("participants"), dict),
            "INTEGRITY_ERROR",
        )
        # Invalid selected records fail; never return an incomplete filtered snapshot.
        matches = []
        for invocation_id, invocation in invocations.items():
            cursor_results.check_deadline(deadline)
            core.require(isinstance(invocation, dict), "INTEGRITY_ERROR")
            core.require(
                call_token(invocation["native_call"]) == invocation["tool_id"]
                and invocation["phase"] in {"admitted", "bound", "retired"},
                "INTEGRITY_ERROR",
            )
            child_key = invocation.get("child_participant")
            record = {
                "invocation_id": invocation_id,
                "invocation": invocation,
                "issue_id": identifier,
                "issue_uuid": state["issue_uuid"],
                "revision": state["revision"],
                "member": state["participants"].get(child_key) if child_key is not None else None,
            }
            if select(record):
                matches.append(copy.deepcopy(record))
        return matches


def one(records: list[core.JSONObject]) -> core.JSONObject:
    """Require one exact retained invocation rather than selecting by arrival order.

    Args:
        records: Complete selected-issue result.

    Returns:
        Its sole matching record.

    Raises:
        core.WorkspaceError: No match or ambiguous correlation.
    """
    core.require(records, "UNKNOWN_OPERATION")
    core.require(len(records) == 1, "REQUEST_CONFLICT")
    return records[0]


def parent_request(store: core.Store, record: core.JSONObject) -> core.JSONObject:
    """Construct parent authority only from one committed native invocation record.

    Args:
        store: Registered repository selected by actual cwd.
        record: Unique retained invocation record.

    Returns:
        Parent request that core revalidates against current binding/generation.
    """
    invocation = record["invocation"]
    return {
        "schema_version": 1,
        "request_id": str(uuid.uuid4()),
        "worktree": str(store.root),
        "host": "cursor",
        "session_id": invocation["parent_session"],
        "repo_id": store.registration["repo_id"],
        "issue_id": record["issue_id"],
        "issue_uuid": record["issue_uuid"],
        "binding_generation": invocation["parent_generation"],
    }


def execute_current(request: core.JSONObject, deadline: float) -> core.JSONObject:
    """Retry internal transitions after recovering the selected issue revision.

    Args:
        request: Exact internal transition with immutable invocation identity.
        deadline: Shared deadline; caller holds only the registry mutex.

    Returns:
        Successful internal transition response.

    Raises:
        core.WorkspaceError: Nonretryable failure, contention exhaustion or expired deadline.
    """

    def attempt() -> core.JSONObject:
        """Release the recovered issue snapshot before the internal transition.

        Returns:
            Internal core result at the recovered revision.

        Raises:
            core.WorkspaceError: Recovery fails or the deadline expires.
            OSError: Selected storage cannot be opened safely.
        """
        # Snapshot recovery precedes binding/issue lock acquisition inside the core.
        with (
            core.Store(request, create=False, bindings=False) as store,
            store.issues.child(request["issue_id"]) as control,
            control.lock(create=False),
        ):
            issue = core.Issue(store, control, request["issue_id"])
            issue.recover()
            revision = issue.committed_state()["revision"]
        # Snapshot work must not start a transition after the shared deadline.
        cursor_results.check_deadline(deadline)
        return core.execute_internal(
            {**request, "request_id": str(uuid.uuid4()), "expected_revision": revision}
        )

    return common.retry_on_contention(attempt, deadline)


def normalize(event: core.JSONObject, deadline: float) -> core.JSONObject:
    """Classify exact native identities before any ordinary parent startup route.

    Args:
        event: Native envelope; derived private markers are discarded.
        deadline: Shared hook deadline.

    Returns:
        Validated parent or independently bound child with normalized call IDs.

    Raises:
        core.WorkspaceError: Identity, population, selected state or generation fails.
        OSError: Selected controls cannot be safely read or recovered.
    """
    # Parent-call bytes are the native child marker; unsupported shapes remain denied.
    core.require(
        event.get("agent_id") is None
        and event.get("subagent_id") is None
        and event.get("parent_conversation_id") is None,
        "HOST_UNSUPPORTED_CHILD_IDENTITY",
    )
    raw = {key: value for key, value in event.items() if not key.startswith("_cursor_")}
    observed = common.native_identity(raw, "cursor")
    conversation = observed["session_id"]
    parent_call = event.get("parent_tool_call_id")
    if "tool_use_id" in event:
        observed["_cursor_native_call"] = event["tool_use_id"]
        observed["tool_use_id"] = call_token(event["tool_use_id"])
    # Registration-only and ordinary parent paths never enumerate issue histories.
    try:
        configured = registration(observed["cwd"])
    except core.WorkspaceError as error:
        # An unregistered parent, or one outside every repository, has no store to route.
        if parent_call is None and (
            error.code == "REGISTRATION_REQUIRED" or session_repository(observed) is None
        ):
            return observed
        raise
    with core.Store(configured, create=False, bindings=False) as store:
        if not store.control.exists("cursor-children"):
            core.require(not store.control.exists(routing.MARKER), "RECOVERY_REQUIRED")
            core.require(parent_call is None, "UNKNOWN_OPERATION")
            # Admission may create the registry while the preceding absence checks run.
            core.require(not store.control.exists("cursor-children"), "BUSY")
            return observed
        # Legacy denial evidence is checked even without synchronization or population.
        with store.control.child("cursor-children") as registry:
            routing.legacy_guard(registry, conversation)
            # An existing modern registry needs explicit complete population before cutover.
            core.require(registry.exists("lock"), "RECOVERY_REQUIRED")
            with registry.lock(create=False):
                routing.verify(store, registry)
                kind, native = (
                    ("call", parent_call)
                    if parent_call is not None
                    else ("conversation", conversation)
                )
                if parent_call is not None:
                    call_token(parent_call)
                pointer = routing.lookup(registry, kind, native)
                if pointer is None:
                    core.require(parent_call is None, "UNKNOWN_OPERATION")
                    return observed
                # A pointer selects one recovered issue; it never supplies participant authority.
                records = issue_records(
                    store,
                    deadline,
                    lambda record: (
                        record["invocation"]["native_call"] == parent_call
                        if parent_call is not None
                        else record["invocation"].get("child_conversation") == conversation
                        and record["member"] is not None
                        and record["member"].get("cursor_invocation") == record["invocation_id"]
                    ),
                    pointer["issue_id"],
                )
                record = one(records)
                core.require(record["issue_uuid"] == pointer["issue_uuid"], "ISSUE_MISMATCH")
                invocation = record["invocation"]
                core.require(
                    conversation != invocation["parent_conversation"],
                    "HOST_UNSUPPORTED_CHILD_IDENTITY",
                )
                # Native child UUIDs remain independent of parent sessions and opaque Task tokens.
                try:
                    core.require(
                        str(uuid.UUID(conversation)) == conversation,
                        "HOST_UNSUPPORTED_CHILD_IDENTITY",
                    )
                except (ValueError, UnicodeError) as error:
                    raise core.WorkspaceError("HOST_UNSUPPORTED_CHILD_IDENTITY") from error
                core.require(invocation["phase"] in {"admitted", "bound"}, "STALE_BINDING")
                generation = common.native_token(event.get("generation_id"))
                session = invocation["parent_session"] + "/agent/" + conversation
                child = {**parent_request(store, record), "session_id": session}
                child.pop("binding_generation")
                # Classification is durable before binding can commit any child authority.
                routing.claim(
                    registry, "conversation", conversation, record["issue_id"], record["issue_uuid"]
                )
                result = execute_current(
                    {
                        **child,
                        "operation": "cursor-bind-child",
                        "invocation_id": record["invocation_id"],
                        "tool_id": invocation["tool_id"],
                        "child_conversation": conversation,
                        "native_generation": generation,
                    },
                    deadline,
                )
                core.require(result["ok"], result["code"])
                observed.update(
                    child=True,
                    parent_session_id=invocation["parent_session"],
                    session_id=session,
                    _cursor_invocation=record["invocation_id"],
                    _cursor_new_child=invocation["phase"] == "admitted",
                    _cursor_conversation=conversation,
                )
    return observed


def admit(event: core.JSONObject, deadline: float) -> core.JSONObject:
    """Consume the exact coordinator plan before permitting one foreground local Task.

    Args:
        event: Validated parent preToolUse with normalized token and exact native call.
        deadline: Shared hook deadline.

    Returns:
        The committed invocation admission response.

    Raises:
        core.WorkspaceError: Child/nested shape, native Task arguments, pin or plan conflict.
    """
    # Validate the demonstrated base fields plus the source-backed optional resume UUID.
    core.require(
        event.get("child") is not True and event.get("tool_name") == "Task",
        "HOST_UNSUPPORTED_CHILD_IDENTITY",
    )
    args = event.get("tool_input")
    core.require(
        isinstance(args, dict) and TASK_FIELDS <= set(args) <= TASK_FIELDS | {"resume"},
        "HOST_UNSUPPORTED_CHILD_IDENTITY",
    )
    assert isinstance(args, dict)
    resume = args.get("resume")
    if "resume" in args:
        core.require(isinstance(resume, str) and len(resume) == 36, "HOST_UNSUPPORTED_RESUME_SHAPE")
        try:
            core.require(str(uuid.UUID(resume)) == resume, "HOST_UNSUPPORTED_RESUME_SHAPE")
        except ValueError as error:
            raise core.WorkspaceError("HOST_UNSUPPORTED_RESUME_SHAPE") from error
    core.require(
        all(isinstance(args[key], str) for key in TASK_FIELDS - {"run_in_background"}),
        "INVALID_REQUEST",
    )
    core.require(
        args["run_in_background"] is False
        and args["environment"] == "local"
        and args["subagent_type"] == "generalPurpose",
        "HOST_UNSUPPORTED_BACKGROUND",
    )
    digest = core.sha(core.canonical(args))
    cursor_results.pinned_sources(deadline)
    configured = registration(event["cwd"])
    with core.Store(configured, create=False, bindings=False) as store:
        existed = store.control.exists("cursor-children")
        core.require(existed or not store.control.exists(routing.MARKER), "RECOVERY_REQUIRED")
        with store.control.child("cursor-children", True) as mutex, mutex.lock():
            routing.initialize(store, mutex, existed)
            # Parent readiness never grants child authority and is checked again by cursor-admit.
            request = common.request_for(event, "ready", "cursor")
            ready = core.execute(request)
            core.require(ready["ok"], ready["code"])
            key = core.participant_key(request)
            # Recover the exact issue before checking plan replacement versus admission.
            with store.issues.child(request["issue_id"]) as control, control.lock():
                issue = core.Issue(store, control, request["issue_id"])
                issue.recover()
                state = issue.committed_state()
                plans = []
                replays = []
                for plan_id, plan in state.get("cursor_plans", {}).items():
                    if (
                        plan["parent"] != key
                        or plan["parent_generation"] != request["binding_generation"]
                    ):
                        continue
                    # Only an explicitly unconsumed plan is eligible for a fresh admission.
                    if plan["invocation_id"] is None:
                        if plan["task_digest"] == digest:
                            plans.append((plan_id, plan))
                        continue
                    invocation = state.get("cursor_invocations", {}).get(plan["invocation_id"])
                    core.require(isinstance(invocation, dict), "INTEGRITY_ERROR")
                    assert isinstance(invocation, dict)
                    if (
                        invocation["tool_id"] == event["tool_use_id"]
                        and invocation["native_call"] == event["_cursor_native_call"]
                    ):
                        core.require(plan["task_digest"] == digest, "REQUEST_CONFLICT")
                        replays.append((plan_id, plan))
            # A later identical plan must not make the original callback's retry ambiguous.
            plans = replays or plans
            core.require(plans, "SCOPE_MISSING")
            core.require(len(plans) == 1, "REQUEST_CONFLICT")
            plan_id, plan = plans[0]
            # Installed source forwards optional resume UUID; the exact plan names the same child.
            core.require(plan["resume_child"] == resume, "REQUEST_CONFLICT")
            # Reserve exact selectors before the issue can commit an admitted native call.
            routing.claim(
                mutex,
                "call",
                event["_cursor_native_call"],
                request["issue_id"],
                state["issue_uuid"],
            )
            if resume is not None:
                routing.claim(
                    mutex, "conversation", resume, request["issue_id"], state["issue_uuid"]
                )
            result = execute_current(
                {
                    **request,
                    "operation": "cursor-admit",
                    "plan_id": plan_id,
                    "parent_conversation": event["conversation_id"],
                    "task_digest": digest,
                    "tool_id": event["tool_use_id"],
                    "native_call": event["_cursor_native_call"],
                },
                deadline,
            )
            core.require(result["ok"], result["code"])
            return result


def reconcile_command(issue: str, token: str) -> str:
    """Format the sole literal adapter command for Cursor's native platform shell.

    Args:
        issue: Exact issue selector; native authorization still verifies ownership.
        token: Normalized original-call token.

    Returns:
        Canonical issue/call selector with no identity/result/path overrides.

    Raises:
        core.WorkspaceError: Token or platform quoting is invalid.
    """
    core.issue_id(issue)
    core.token(token)
    argv = [common.PYTHON, str(ENTRY), "cursor-reconcile", "--issue", issue, "--call", token]
    return shlex.join(argv)


def recognize_reconcile(event: core.JSONObject) -> tuple[str, str] | None:
    """Recognize the exact CLI spelling or explicitly refuse a malformed direct variant.

    Args:
        event: Validated native Shell envelope with its observed cwd.

    Returns:
        Issue and normalized call token, or None for an unrelated ordinary Shell command.

    Raises:
        core.WorkspaceError: A direct adapter invocation has malformed arguments/quoting.
    """
    if event.get("tool_name") != "Shell":
        return None
    args = event.get("tool_input")
    core.require(isinstance(args, dict), "INVALID_REQUEST")
    assert isinstance(args, dict)
    command = args.get("command")
    if not isinstance(command, str) or str(ENTRY) not in command:
        return None
    core.require(
        len(command.encode()) <= 65536 and not any(c in command for c in "\r\n\0"),
        "INVALID_REQUEST",
    )
    # Parse only direct entry invocation. Arbitrary same-user shell expressions are no sandbox.
    try:
        argv = shlex.split(command)
        core.require(
            len(argv) == 7
            and argv[:4] == [common.PYTHON, str(ENTRY), "cursor-reconcile", "--issue"]
            and argv[5] == "--call",
            "HOST_UNSUPPORTED_TOOL",
        )
        issue, token = core.issue_id(argv[4]), core.token(argv[6])
        core.require(command == reconcile_command(issue, token), "HOST_UNSUPPORTED_TOOL")
        common.cursor_lifecycle_shell(event, args)
        return issue, token
    except ValueError as error:
        raise core.WorkspaceError("HOST_UNSUPPORTED_TOOL") from error


def authorize_reconcile(event: core.JSONObject, issue: str, token: str, deadline: float) -> None:
    """Bind the supported command to the actual ready native parent before Shell allow.

    Args:
        event: Validated observed native envelope.
        issue: Untrusted issue selector, matched against the actual ready parent.
        token: Exact recognized normalized Task call.
        deadline: Shared hook deadline.

    Raises:
        core.WorkspaceError: Unknown/foreign call, child caller or stale parent generation.
    """
    core.require(event.get("child") is not True, "NOT_OWNER")
    parent = common.request_for(event, "ready", "cursor")
    ready = core.execute(parent)
    core.require(ready["ok"], ready["code"])
    core.require(core.issue_id(issue) == parent["issue_id"], "NOT_OWNER")
    with (
        core.Store(parent, create=False, bindings=False) as store,
        store.control.child("cursor-children") as mutex,
        mutex.lock(create=False),
    ):
        routing.verify(store, mutex)
        record = one(
            issue_records(
                store, deadline, lambda record: record["invocation"]["tool_id"] == token, issue
            )
        )
        invocation = record["invocation"]
        core.require(
            invocation["parent"] == core.participant_key(parent)
            and invocation["parent_generation"] == parent["binding_generation"]
            and record["issue_id"] == parent["issue_id"],
            "NOT_OWNER",
        )


def reconcile(worktree: str, issue: str, token: str, deadline: float) -> core.JSONObject:
    """Retire one exact Task only after independently reading pinned host evidence.

    Args:
        worktree: Actual adapter CLI cwd.
        issue: Exact issue selector; the native prehook binds it to the parent.
        token: Normalized original native call token; no raw result/child is accepted.
        deadline: Shared monotonic execution budget.

    Returns:
        Committed exact retirement or the core's bounded unresolved diagnostic.

    Raises:
        core.WorkspaceError: Lookup, pinned evidence or retirement validation fails.
    """
    core.issue_id(issue)
    core.token(token)
    configured = registration(worktree)
    # Snapshot identity under the shared mutex; do expensive source/database reads outside locks.
    with (
        core.Store(configured, create=False, bindings=False) as store,
        store.control.child("cursor-children") as mutex,
        mutex.lock(create=False),
    ):
        routing.verify(store, mutex)
        record = one(
            issue_records(
                store, deadline, lambda record: record["invocation"]["tool_id"] == token, issue
            )
        )
    result = cursor_results.read_terminal(record["invocation"], deadline)
    # Serialize terminal-only classification with every other lookup-visible candidate mutation.
    with (
        core.Store(configured, create=False, bindings=False) as store,
        store.control.child("cursor-children") as mutex,
        mutex.lock(create=False),
    ):
        # A terminal-only conversation is durable before retirement can commit a retained child.
        routing.verify(store, mutex)
        routing.claim(
            mutex,
            "conversation",
            result["child_conversation"],
            record["issue_id"],
            record["issue_uuid"],
        )
        # Core revalidates the exact invocation, generation and effects under its issue lock.
        request = parent_request(store, record)
        retired = execute_current(
            {
                **request,
                "operation": "cursor-retire",
                "invocation_id": record["invocation_id"],
                "result": result,
            },
            deadline,
        )
        core.require(retired["ok"], retired["code"])
        return retired


def populate_routes(worktree: str, identifiers: list[str], deadline: float) -> core.JSONObject:
    """Explicitly populate an existing modern registry before routing cutover.

    This recovery operation never runs from native classification or admission.
    Selected issue recovery may roll a staged issue transaction forward.
    Population never migrates authority or rewrites a retained legacy index.

    Args:
        worktree: Registered checkout selected for deliberate recovery.
        identifiers: Complete sorted retained issue inventory supplied explicitly by the caller.
        deadline: Shared recovery deadline; failed partial reservations remain restrictive.

    Returns:
        Complete marker after verifying the entire supplied inventory.

    Raises:
        core.WorkspaceError: Inventory, selected state, routes or legacy evidence is invalid.
        OSError: Selected issue or durable pointer access fails.
    """
    # Bound the explicit inventory by the existing lifecycle request-size limit.
    core.require(
        isinstance(identifiers, list)
        and len(core.canonical(identifiers)) <= core.MAX_REQUEST
        and all(isinstance(identifier, str) for identifier in identifiers),
        "INVALID_REQUEST",
    )
    for identifier in identifiers:
        core.issue_id(identifier)
    core.require(identifiers == sorted(set(identifiers)), "INVALID_REQUEST")
    configured = registration(worktree)
    with (
        core.Store(configured, create=False, bindings=False) as store,
        store.control.child("cursor-children") as registry,
        registry.lock(create=False),
    ):
        # Coexisting legacy evidence is validated and retained byte-for-byte.
        routing.legacy_guard(registry)
        if store.control.exists(routing.MARKER):
            routing.verify(store, registry)
        actual = sorted(store.issues.names()) if store.issues_present else []
        core.require(identifiers == actual, "REQUEST_CONFLICT")
        if not store.control.exists(routing.MARKER):
            routing.prepare(registry)
        # Recover every supplied issue and publish only nonauthorizing exact selectors.
        for identifier in identifiers:
            records = issue_records(store, deadline, lambda _record: True, identifier)
            for record in records:
                invocation = record["invocation"]
                routing.claim(
                    registry, "call", invocation["native_call"], identifier, record["issue_uuid"]
                )
                for conversation in {
                    invocation.get("child_conversation"),
                    invocation.get("resume_child"),
                } - {None}:
                    routing.claim(
                        registry, "conversation", conversation, identifier, record["issue_uuid"]
                    )
        # Recheck membership after recovery, then publish completion as the final durable step.
        cursor_results.check_deadline(deadline)
        core.require(
            identifiers == (sorted(store.issues.names()) if store.issues_present else []),
            "REVISION_CONFLICT",
        )
        return routing.complete(store, registry)


def parent_pre(event: core.JSONObject, deadline: float) -> core.JSONObject | None:
    """Handle parent Task or exact reconciliation admission without ordinary pending work.

    Args:
        event: Independently normalized actual parent envelope.
        deadline: Shared absolute monotonic execution deadline.

    Returns:
        Native admission response for a managed call, or None for ordinary parent admission.

    Raises:
        core.WorkspaceError: Planned Task or reconciliation authority is invalid.
    """
    # Only the exact planned native Task may reserve its parent's invocation charge.
    if event.get("tool_name") == "Task":
        admit(event, deadline)
        return {"permission": "allow"}
    # The exact self-managing command independently verifies result evidence before retirement.
    selected = recognize_reconcile(event)
    if selected is not None:
        authorize_reconcile(event, *selected, deadline)
        return {"permission": "allow"}
    return None


def parent_post(
    event: core.JSONObject, deadline: float, *, failed: bool = False
) -> core.JSONObject | None:
    """Keep Task charges until reconciliation and bypass ordinary completion for that command.

    Args:
        event: Independently normalized actual parent completion.
        deadline: Shared absolute monotonic execution deadline.
        failed: Actual postToolUseFailure rather than successful completion.

    Returns:
        Exact reconciliation advice or an empty self-managed response; None for ordinary work.

    Raises:
        core.WorkspaceError: Completion shape or current parent binding is invalid.
    """
    # Native Task callbacks advise the exact issue/call command without settling its charge.
    if event.get("tool_name") == "Task":
        cursor_results.check_deadline(deadline)
        if failed:
            core.require(
                isinstance(event.get("error_message"), str)
                and event.get("failure_type") in {"error", "timeout", "permission_denied"},
                "INVALID_REQUEST",
            )
        else:
            core.require(isinstance(event.get("tool_output"), str), "INVALID_REQUEST")
        parent = common.request_for(event, "ready", "cursor")
        command = reconcile_command(parent["issue_id"], event["tool_use_id"])
        return {
            "additional_context": "Task remains charged until result reconciliation. Run: "
            + command
        }
    # The prehook authorized this exact command; no generic pending effect was started.
    if recognize_reconcile(event) is not None:
        return {}
    return None


def main() -> int:
    """Run exact reconciliation or deliberately requested complete routing recovery.

    Returns:
        Zero on success, three for a bounded unresolved or failed operation.
    """
    # Recovery is an explicit maintenance command, never a native-hook fallback.
    try:
        if len(sys.argv) == 6 and sys.argv[1:3] == ["cursor-routing-recover", "--issues-json"]:
            core.require(sys.argv[4] == "--budget-ms", "INVALID_REQUEST")
            core.require(len(sys.argv[3].encode()) <= core.MAX_REQUEST, "SIZE_LIMIT")
            budget = int(core.token(sys.argv[5]))
            core.require(budget > 0, "INVALID_REQUEST")
            deadline = time.monotonic() + budget / 1000
            core.require(math.isfinite(deadline), "INVALID_REQUEST")
            marker = populate_routes(str(Path.cwd()), core.strict_json(sys.argv[3]), deadline)
            result = {"ok": True, "code": "OK", "routing": marker}
        else:
            # Native reconciliation retains the exact spelling and runner deadline.
            core.require(
                len(sys.argv) == 6
                and sys.argv[1:3] == ["cursor-reconcile", "--issue"]
                and sys.argv[4] == "--call",
                "INVALID_REQUEST",
            )
            result = reconcile(
                str(Path.cwd()),
                core.issue_id(sys.argv[3]),
                core.token(sys.argv[5]),
                time.monotonic() + runner.DEADLINE_SECONDS,
            )
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        result = {"ok": False, "code": code}
    print(core.canonical(result).decode())
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
