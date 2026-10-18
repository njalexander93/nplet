"""Correlate Cursor native children through retained issue invocations, without a second index."""

from __future__ import annotations

import copy
import hashlib
import shlex
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from nplet.adapters import common, cursor_results
from nplet.lifecycle import task_workspace as core

ENTRY = Path(__file__).resolve()
CENSUS_ISSUES = 64
CENSUS_BYTES = 32 * 1024 * 1024
INTERNAL = frozenset(
    {
        "cursor-admit",
        "cursor-bind-child",
        "cursor-retire",
        "cursor-source-callback",
        "cursor-source-callback",
    }
)
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


def census(
    store: core.Store,
    deadline: float,
    select: Callable[[core.JSONObject], bool],
    issue: str | None = None,
) -> list[core.JSONObject]:
    """Inspect bounded no-follow issue snapshots and return only selected invocation metadata.

    Caller holds the shared repository mutex. Snapshot locks are released before any core.execute.

    Args:
        store: Registered repository store under its outer cursor-children mutex.
        deadline: Shared hook deadline.
        select: Adapter-selected exact identity predicate; never reads task prose.
        issue: Validated issue selector, or None when native identity requires discovery.

    Returns:
        Matching committed records with issue, revision and parent request identity.

    Raises:
        core.WorkspaceError: Bounds, snapshots, identity or unfinished transactions are invalid.
        OSError: Any registered control is inaccessible; no partial census is returned.
    """
    # Known-issue operations avoid unrelated controls; discovery still checks the whole namespace.
    names = store.issues.names() if issue is None else [core.issue_id(issue)]
    core.require(len(names) <= CENSUS_ISSUES, "SIZE_LIMIT")
    total = 0
    matches = []
    for identifier in names:
        cursor_results.check_deadline(deadline)
        core.issue_id(identifier)
        # One snapshot lock at a time preserves repository -> binding -> issue order.
        with store.issues.child(identifier) as control, control.lock():
            core.require(
                not control.exists("transaction.json") and not control.exists("cleanup.json"),
                "RECOVERY_REQUIRED",
            )
            data = control.read("state.json")
            total += len(data)
            core.require(total <= CENSUS_BYTES, "SIZE_LIMIT")
            state = core.strict_json(data)
            core.require(
                isinstance(state, dict) and state.get("issue_id") == identifier, "INTEGRITY_ERROR"
            )
            invocations = state.get("cursor_invocations", {})
            core.require(
                isinstance(invocations, dict) and isinstance(state.get("participants"), dict),
                "INTEGRITY_ERROR",
            )
            # Copy only matching correlation records; never read managed payload/prose.
            for invocation_id, invocation in invocations.items():
                cursor_results.check_deadline(deadline)
                core.require(isinstance(invocation, dict), "INTEGRITY_ERROR")
                core.require(
                    call_token(invocation["native_call"]) == invocation["tool_id"],
                    "INTEGRITY_ERROR",
                )
                core.require(
                    invocation["phase"] in {"admitted", "bound", "retired"}, "INTEGRITY_ERROR"
                )
                record = {
                    "invocation_id": invocation_id,
                    "invocation": invocation,
                    "issue_id": identifier,
                    "issue_uuid": state["issue_uuid"],
                    "revision": state["revision"],
                    "member": state["participants"].get(invocation.get("child_participant")),
                }
                if select(record):
                    matches.append(copy.deepcopy(record))
    return matches


def one(records: list[core.JSONObject]) -> core.JSONObject:
    """Require one exact retained invocation rather than selecting by arrival order.

    Args:
        records: Complete bounded census result.

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
    """Retry only actual contention against a freshly observed issue revision.

    Caller holds the repository mutex, never an issue/binding lock across this call.

    Args:
        request: Exact internal transition with immutable invocation identity fields.
        deadline: Shared hook deadline.

    Returns:
        Successful core transition response.

    Raises:
        core.WorkspaceError: Nonretryable transition failure or exhausted contention budget.
    """
    # Each attempt releases the snapshot lock before core acquires binding then issue locks.
    code = "BUSY"
    for _ in range(8):
        cursor_results.check_deadline(deadline)
        with (
            core.Store(request) as store,
            store.issues.child(request["issue_id"]) as control,
            control.lock(),
        ):
            core.require(
                not control.exists("transaction.json") and not control.exists("cleanup.json"),
                "RECOVERY_REQUIRED",
            )
            state = control.json("state.json")
            revision = state["revision"]
        # Never start a transition after snapshot work exhausted the shared budget.
        cursor_results.check_deadline(deadline)
        result = core.execute(
            {**request, "request_id": str(uuid.uuid4()), "expected_revision": revision}
        )
        if result["ok"]:
            return result
        code = result["code"]
        core.require(code in {"REVISION_CONFLICT", "BUSY"}, code)
    raise core.WorkspaceError(code)


def normalize(event: core.JSONObject, deadline: float) -> core.JSONObject:
    """Classify actual child conversations before any ordinary parent startup route.

    Args:
        event: Native Cursor envelope; derived/private markers are discarded.
        deadline: Shared hook deadline.

    Returns:
        Validated parent or independently bound child envelope with normalized call IDs.

    Raises:
        core.WorkspaceError: Native markers, lookup, generation or exact child binding fail.
    """
    # The actual child marker is parent_tool_call_id, never the opaque subagentStart token.
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
    # Unregistered ordinary parent startup stays available; an unbound child never gets it.
    try:
        configured = registration(observed["cwd"])
    except core.WorkspaceError as error:
        if error.code == "REGISTRATION_REQUIRED" and parent_call is None:
            return observed
        raise
    with (
        core.Store(configured) as store,
        store.control.child("cursor-children", True) as mutex,
        mutex.lock(),
    ):
        # Retained raw-session children must not acquire ordinary parent authority.
        if mutex.exists("index.json"):
            legacy = mutex.json("index.json")
            core.require(
                isinstance(legacy, dict)
                and type(legacy.get("schema_version")) is int
                and legacy["schema_version"] == 2
                and isinstance(legacy.get("children"), dict)
                and all(
                    core.DIGEST.fullmatch(key) and isinstance(value, dict)
                    for key, value in legacy["children"].items()
                ),
                "INTEGRITY_ERROR",
            )
            core.require(
                core.participant_key({"host": "cursor", "session_id": conversation})
                not in legacy["children"],
                "HOST_UNSUPPORTED_CHILD_IDENTITY",
            )
        # Explicit parent call selects one retained invocation, including retired attempts.
        if parent_call is not None:
            token = call_token(parent_call)
            records = census(
                store,
                deadline,
                lambda record: (
                    record["invocation"]["native_call"] == parent_call
                    and record["invocation"]["tool_id"] == token
                ),
            )
            record = one(records)
        else:
            # Current member pointer disambiguates historical generations after a real resume.
            records = census(
                store,
                deadline,
                lambda record: (
                    record["invocation"].get("child_conversation") == conversation
                    and record["member"] is not None
                    and record["member"].get("cursor_invocation") == record["invocation_id"]
                ),
            )
            if not records:
                return observed
            record = one(records)
        invocation = record["invocation"]
        core.require(
            conversation != invocation["parent_conversation"], "HOST_UNSUPPORTED_CHILD_IDENTITY"
        )
        # Canonical native child UUIDs are independent from parent/session/subagent tokens.
        try:
            core.require(
                str(uuid.UUID(conversation)) == conversation, "HOST_UNSUPPORTED_CHILD_IDENTITY"
            )
        except (ValueError, UnicodeError) as error:
            raise core.WorkspaceError("HOST_UNSUPPORTED_CHILD_IDENTITY") from error
        core.require(invocation["phase"] in {"admitted", "bound"}, "STALE_BINDING")
        generation = common.native_token(event.get("generation_id"))
        session = invocation["parent_session"] + "/agent/" + conversation
        child = {**parent_request(store, record), "session_id": session}
        child.pop("binding_generation")
        # Core binds/revalidates the exact native link under binding then issue locks.
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
    with (
        core.Store(configured) as store,
        store.control.child("cursor-children", True) as mutex,
        mutex.lock(),
    ):
        # Parent readiness never grants child authority and is checked again by cursor-admit.
        request = common.request_for(event, "ready", "cursor")
        ready = core.execute(request)
        core.require(ready["ok"], ready["code"])
        key = core.participant_key(request)
        # Read the exact issue only, preserving plan replacement-versus-admission revision checks.
        with store.issues.child(request["issue_id"]) as control, control.lock():
            core.require(
                not control.exists("transaction.json") and not control.exists("cleanup.json"),
                "RECOVERY_REQUIRED",
            )
            state = control.json("state.json")
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
        core.Store(parent) as store,
        store.control.child("cursor-children", True) as mutex,
        mutex.lock(),
    ):
        record = one(
            census(store, deadline, lambda record: record["invocation"]["tool_id"] == token, issue)
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
        core.Store(configured) as store,
        store.control.child("cursor-children", True) as mutex,
        mutex.lock(),
    ):
        record = one(
            census(store, deadline, lambda record: record["invocation"]["tool_id"] == token, issue)
        )
    result = cursor_results.read_terminal(record["invocation"], deadline)
    # Serialize terminal-only classification with every other lookup-visible candidate mutation.
    with (
        core.Store(configured) as store,
        store.control.child("cursor-children", True) as mutex,
        mutex.lock(),
    ):
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


def source_transition(
    event: core.JSONObject, phase: str, deadline: float, **metadata: object
) -> core.JSONObject:
    """Commit one exact Cursor source callback using a stable native request identity.

    Args:
        event: Independently normalized current child envelope.
        phase: start, content, success or failure selected by adapter code.
        deadline: Shared hook deadline.
        **metadata: Closed source metadata; never source text.

    Returns:
        The committed source transition result.

    Raises:
        core.WorkspaceError: Identity, callback, packet or bounded contention fails.
    """
    # Select the closed operation; do not grant authority from a caller's completed boolean.
    core.require(
        event.get("child") is True and phase in {"start", "content", "success", "failure"},
        "NOT_OWNER",
    )
    operation = "source-read-start" if phase == "start" else "cursor-source-callback"
    bound = common.request_for(event, operation, "cursor")
    request = {key: value for key, value in bound.items() if key in common.COMMON_FIELDS}
    request.update(metadata)
    request["tool_id"] = common.native_token(event.get("tool_use_id"))
    request["request_id"] = "cursor-source:" + core.sha(
        core.canonical(
            [request["session_id"], request["binding_generation"], request["tool_id"], phase]
        )
    )
    if phase != "start":
        request["source_event"] = phase
    # Exact callback retries retain the same ID/body so a lost reply cannot duplicate effects.
    for _ in range(8):
        cursor_results.check_deadline(deadline)
        result = core.execute(request)
        if result["ok"]:
            return result
        core.require(result["code"] == "BUSY", result["code"])
    raise core.WorkspaceError("BUSY")


def source_read_pre(event: core.JSONObject, deadline: float) -> core.JSONObject:
    """Admit only a native Read of an exact current assigned source path.

    Args:
        event: Normalized child Read prehook; hook key is file_path, not model-facing path.
        deadline: Shared hook deadline.

    Returns:
        Committed source pending effect.

    Raises:
        core.WorkspaceError: Native shape or current assigned path does not match.
    """
    # Native hook input exposes only file_path; partial final content is caught at content hashing.
    core.require(
        event.get("child") is True and event.get("tool_name") == "Read", "HOST_UNSUPPORTED_TOOL"
    )
    args = event.get("tool_input")
    core.require(isinstance(args, dict) and set(args) == {"file_path"}, "HOST_UNSUPPORTED_TOOL")
    assert isinstance(args, dict)
    path = args["file_path"]
    core.require(isinstance(path, str) and Path(path).is_absolute(), "SCOPE_MISSING")
    request = common.request_for(event, "read", "cursor")
    read = core.execute(request)
    core.require(read["ok"], read["code"])
    root = core.repository(event["cwd"])[0]
    # Compare lexical native paths to explicit packet locators; never widen to directory scope.
    matches = []
    for ref in read["references"]:
        source = Path(ref["locator"])
        if not source.is_absolute():
            source = root / ".task" / request["issue_id"] / source
        if ref["available"] and str(source) == path:
            matches.append(ref)
    core.require(matches, "SCOPE_MISSING")
    return source_transition(
        event,
        "start",
        deadline,
        source_locator=matches[0]["locator"],
        packet_digest=read["packet_digest"],
    )


def source_read_content(event: core.JSONObject, deadline: float) -> core.JSONObject:
    """Hash actual beforeReadFile text provisionally without retaining source prose.

    Args:
        event: Actual independently normalized beforeReadFile callback.
        deadline: Shared hook deadline.

    Returns:
        Provisional content-verification result; no completed receipt exists yet.

    Raises:
        core.WorkspaceError: Identity, full UTF8content, path or pending source proof conflicts.
    """
    # These fields come from the pinned Read post-execution wrapper, not generic post output.
    core.require(event.get("child") is True, "NOT_OWNER")
    content = event.get("content")
    path = event.get("file_path")
    core.require(
        isinstance(content, str) and isinstance(path, str) and event.get("attachments") == [],
        "INVALID_REQUEST",
    )
    assert isinstance(content, str)
    # Strict UTF8 hash detects omitted/altered/truncated text; JS string.length uses UTF16units.
    try:
        encoded = content.encode("utf-8")
        length = len(content.encode("utf-16-le")) // 2
    except UnicodeEncodeError as error:
        raise core.WorkspaceError("SOURCE_DELIVERY_REQUIRED") from error
    core.require(len(encoded) <= core.MAX_FILE, "SIZE_LIMIT")
    digest = core.sha(encoded)
    cursor_results.check_deadline(deadline)
    return source_transition(
        event, "content", deadline, source_path=path, observed_sha256=digest, content_length=length
    )


def source_read_post(event: core.JSONObject, deadline: float, failed: bool) -> core.JSONObject:
    """Settle only the exact known finished Read; never credit generic completed=true.

    Args:
        event: Normalized child native Read success/failure callback.
        deadline: Shared hook deadline.
        failed: True only for the native postToolUseFailure event.

    Returns:
        Closed source callback result; success without provisional proof mints no receipt.

    Raises:
        core.WorkspaceError: Unknown/malformed completion or foreign path cannot be settled.
    """
    # Correlate the same tool and native input path before interpreting completion evidence.
    core.require(
        event.get("child") is True and event.get("tool_name") == "Read", "HOST_UNSUPPORTED_TOOL"
    )
    args = event.get("tool_input")
    core.require(
        isinstance(args, dict)
        and set(args) == {"file_path"}
        and isinstance(args.get("file_path"), str),
        "INVALID_REQUEST",
    )
    assert isinstance(args, dict)
    path = args["file_path"]
    # A recognized synchronous Read failure is known finished, with no successful-source credit.
    if failed:
        core.require(
            isinstance(event.get("error_message"), str)
            and event.get("failure_type") in {"error", "timeout", "permission_denied"}
            and type(event.get("is_interrupt", False)) is bool,
            "INVALID_REQUEST",
        )
        return source_transition(event, "failure", deadline, source_path=path)
    # Pinned generic success carries only path and UTF16length; content was verified separately.
    output = event.get("tool_output")
    core.require(isinstance(output, str), "INVALID_REQUEST")
    assert isinstance(output, str)
    parsed = core.strict_json(output)
    core.require(
        isinstance(parsed, dict) and set(parsed) == {"file_path", "content_length"},
        "INVALID_REQUEST",
    )
    assert isinstance(parsed, dict)
    core.require(
        parsed["file_path"] == path
        and type(parsed["content_length"]) is int
        and parsed["content_length"] >= 0,
        "INVALID_REQUEST",
    )
    cursor_results.check_deadline(deadline)
    return source_transition(
        event, "success", deadline, source_path=path, content_length=parsed["content_length"]
    )


def child_lifecycle(event: core.JSONObject) -> bool:
    """Recognize a child's exact own lifecycle request without a parent assignment alias.

    Args:
        event: Independently normalized current child Shell envelope.

    Returns:
        True for the supported canonical own read/acknowledge/ready/diagnose/update command.
        False only for a non-Shell tool; malformed child Shell never falls through.

    Raises:
        core.WorkspaceError: Command, current binding, worktree or owned-note scope is invalid.
    """
    if event.get("tool_name") != "Shell":
        return False
    # Only exact lifecycle commands own transactions; Shell return does not prove their outcome.
    core.require(event.get("child") is True, "NOT_OWNER")
    args = event.get("tool_input")
    core.require(isinstance(args, dict), "HOST_UNSUPPORTED_TOOL")
    assert isinstance(args, dict)
    common.cursor_lifecycle_shell(event, args)
    command = args.get("command")
    core.require(
        isinstance(command, str) and len(command.encode()) <= 65536, "HOST_UNSUPPORTED_TOOL"
    )
    assert isinstance(command, str)
    try:
        request = common.bootstrap_request(command, "cursor", common.PYTHON, common.LIFECYCLE)
    except ValueError as error:
        raise core.WorkspaceError("HOST_UNSUPPORTED_TOOL") from error
    # Validate against the actual committed child binding, not the parent's assignment file.
    bound = common.request_for(event, "read", "cursor")
    fields = {
        "read": set(),
        "acknowledge": {"packet_digest"},
        "ready": set(),
        "diagnose": set(),
        "update": {"path", "old_digest", "content", "provenance"},
    }
    operation = request.get("operation")
    core.require(isinstance(operation, str) and operation in fields, "HOST_UNSUPPORTED_TOOL")
    assert isinstance(operation, str)
    core.require(
        set(request) <= (common.COMMON_FIELDS - {"issue_uuid"}) | fields[operation],
        "HOST_UNSUPPORTED_TOOL",
    )
    core.require(
        request.get("schema_version") == 1
        and request.get("host") == "cursor"
        and request.get("session_id") == event["session_id"],
        "BINDING_CONFLICT",
    )
    core.require(
        request.get("repo_id") == bound["repo_id"]
        and request.get("issue_id") == bound["issue_id"]
        and type(request.get("binding_generation")) is int
        and request["binding_generation"] == bound["binding_generation"],
        "STALE_BINDING",
    )
    core.require(isinstance(request.get("worktree"), str), "REPOSITORY_MISMATCH")
    core.require(
        core.repository(request["worktree"])[0] == core.repository(event["cwd"])[0],
        "REPOSITORY_MISMATCH",
    )
    # Core enforces readiness, digest, provenance and invocation ownership atomically.
    if operation == "update":
        core.require(isinstance(request.get("path"), str), "INVALID_REQUEST")
        path = core.payload_path(request["path"])
        core.require(path.startswith("context/"), "NOT_OWNER")
    return True


def child_context(event: core.JSONObject) -> str:
    """Render canonical own requests with explicit result/assignment substitutions.

    Args:
        event: Independently bound current child envelope.

    Returns:
        Advisory templates carrying actual identity, without granting readiness or ownership.

    Raises:
        core.WorkspaceError: Current child binding cannot be recovered.
    """
    # Retain only the actual child's public lifecycle identity envelope.
    core.require(event.get("child") is True, "NOT_OWNER")
    bound = common.request_for(event, "read", "cursor")
    request = {
        key: value
        for key, value in bound.items()
        if key in common.COMMON_FIELDS and key not in {"issue_uuid", "expected_revision"}
    }
    # Render complete schemas; placeholders are replaced before invoking canonical Shell.
    templates: dict[str, core.JSONObject] = {
        "read": {},
        "acknowledge": {
            "expected_revision": "<LATEST_SUCCESSFUL_REVISION>",
            "packet_digest": "<RETURNED_PACKET_DIGEST>",
        },
        "ready": {},
        "diagnose": {},
        "update": {
            "expected_revision": "<LATEST_SUCCESSFUL_REVISION>",
            "path": "<EXACT_ASSIGNED_CONTEXT_NOTE_PATH>",
            "old_digest": None,
            "content": "<REQUESTED_NOTE_CONTENT>",
            "provenance": {
                "sources": [
                    {
                        "id": "<REFERENCE_ID>",
                        "locator": "<REFERENCE_LOCATOR>",
                        "sha256": "<REFERENCE_SHA256>",
                    }
                ],
                "applicability": "assigned-step",
                "status": "verified",
            },
        },
    }
    commands = []
    for operation, fields in templates.items():
        command = common.bootstrap_command(
            {**request, "operation": operation, "request_id": str(uuid.uuid4()), **fields},
            "cursor",
        )
        commands.append(operation + ":\n" + command)
    # Explain sequencing and recovery without restarting successful delivery after every denial.
    return (
        "Canonical own Shell templates follow. Replace every <...> before execution; "
        "preserve the actual identity fields. Use a fresh request_id for each new or changed "
        "request. If a reply is uncertain, replay the exact same request and ID.\n"
        "Initially run read once. Retain its references and packet_digest. Use native "
        "Read(path=...) for each exact assigned source, with no range/limit; relative locators "
        "resolve under the issue task directory. Source Read callbacks advance revision; "
        "refresh metadata with read or diagnose after source delivery, before acknowledgment. "
        "Acknowledge the returned packet_digest "
        "(not an individual file sha256), then require ready ok:true before update.\n"
        "For expected_revision use the integer revision from the latest successful lifecycle "
        "response, including acknowledge/update; never pin the startup revision. On "
        "REVISION_CONFLICT, refresh metadata with read or diagnose, then retry the intended "
        "operation with its current revision and a fresh request_id. Preserve completed "
        "source receipts; do not repeat acknowledgment unless readiness or the packet changed.\n"
        "For update copy the exact owned context note path from the parent's assignment; "
        "never choose another context path. old_digest:null is only for an absent note; "
        "for replacement use its current SHA-256, obtaining exact metadata from the "
        "coordinator if unavailable. Copy provenance sources as exact id/locator/sha256 "
        "triples from read references; include each applicable source. Replace content with "
        "the requested result and choose truthful single-token applicability/status.\n"
        "A denial does not restart packet/source reads. Follow the exact failure: missing "
        "source delivery needs that assigned Read; changed scope/source needs coordinator "
        "refresh. Otherwise stop the denied action and report its code. Do not try Grep, "
        "unassigned Reads, ordinary Shell, another identity, or a replacement Task. These "
        "templates grant no authority.\n" + "\n".join(commands)
    )


def main() -> int:
    """Run only the exact adapter reconciliation CLI with actual cwd and no result overrides.

    Returns:
        Zero on retirement, three for a bounded unresolved/failure result.
    """
    try:
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
            time.monotonic() + 1.5,
        )
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        result = {"ok": False, "code": code}
    print(core.canonical(result).decode())
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
