"""Apply issue-local invocation transitions through the existing lifecycle transaction.

Native decoding stays in adapters. This module owns shared child identity claims,
bounded actor policy, independent source receipts and the invocation state machine.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from nplet.lifecycle import _invocation_records as records
from nplet.lifecycle._errors import require
from nplet.lifecycle._primitives import canonical, expected, participant_key, sha, token

if TYPE_CHECKING:
    from nplet.lifecycle.task_workspace import (
        Issue,
        JSONObject,
        Participant,
        PayloadFiles,
        Store,
        WorkspaceState,
    )

INTERNAL_OPERATIONS = frozenset(
    {
        "cursor-admit",
        "cursor-bind-child",
        "cursor-retire",
        "cursor-source-callback",
    }
)
OPERATIONS = INTERNAL_OPERATIONS | {"cursor-plan"}


def child_key(parent_session: str, conversation: str) -> str:
    """Derive the normalized independent reader key.

    Args:
        parent_session: Exact admitted parent session.
        conversation: Exact observed child conversation.

    Returns:
        The framework participant key for the normalized child session.
    """
    return participant_key(
        {
            "host": "cursor",
            "session_id": parent_session + "/agent/" + conversation,
        }
    )


def conversation_available(
    state: WorkspaceState,
    conversation: str,
    excluded_invocation: str | None = None,
) -> None:
    """Require one active invocation per actual conversation.

    Args:
        state: Locked issue state.
        conversation: Actual observed or explicitly resumed conversation.
        excluded_invocation: Current admitted invocation claiming its own child.

    Raises:
        WorkspaceError: Another admitted or bound invocation already reserves the conversation.
    """
    # Arrival order and normalized parent identities never substitute for actual identity.
    require(
        not any(
            identifier != excluded_invocation
            and item["phase"] in {"admitted", "bound"}
            and conversation in {item["child_conversation"], item["resume_child"]}
            for identifier, item in state.get("cursor_invocations", {}).items()
        ),
        "PENDING_OPERATION",
    )


def resumable_member(
    state: WorkspaceState,
    parent_session: str,
    conversation: str,
    expected_generation: int | None = None,
) -> Participant:
    """Require the exact retired, classified and empty generation inspected by a plan.

    Args:
        state: Locked issue state.
        parent_session: Parent whose admitted plan names this conversation.
        conversation: Exact explicitly resumed child.
        expected_generation: Planned framework generation, absent only during initial planning.

    Returns:
        The existing retired participant, never a manufactured active assignment.

    Raises:
        WorkspaceError: Classification, status, effects or planned generation conflict.
    """
    # The same eligibility predicate governs planning, admission and both identity-claim paths.
    member = state["participants"].get(child_key(parent_session, conversation))
    require(
        member is not None
        and member["status"] == "detached"
        and not member["pending"]
        and "cursor_invocation" in member
        and (expected_generation is None or member["generation"] == expected_generation),
        "STALE_BINDING",
    )
    assert member is not None
    return member


def claim_child_identity(
    state: WorkspaceState,
    invocation: records.AdmittedInvocation,
    identifier: str,
    conversation: str,
    native_generation: str | None,
) -> records.ChildClaim:
    """Claim one child identity/generation for binding or terminal-only classification.

    Args:
        state: Locked issue state.
        invocation: The exact admitted invocation being consumed.
        identifier: Its stable parent pending correlation.
        conversation: Actual observed or terminal-result child.
        native_generation: Genuine callback link, or None when no callback supplied one.

    Returns:
        An identity claim without packet installation, acknowledgment or pending effects.

    Raises:
        WorkspaceError: Another invocation, unplanned reuse or retired generation conflicts.
    """
    # Revalidate actual conversation exclusion under the same lock as publication.
    conversation_available(state, conversation, identifier)
    key = child_key(invocation["parent_session"], conversation)
    previous = state["participants"].get(key)
    # A fresh callback and an early foreground return allocate generation through one rule.
    if previous is None:
        require(invocation["resume_child"] is None, "STALE_BINDING")
        generation = 1
    else:
        require(invocation["resume_child"] == conversation, "STALE_BINDING")
        previous = resumable_member(
            state,
            invocation["parent_session"],
            conversation,
            invocation["resume_generation"],
        )
        generation = previous["generation"] + 1
    return {
        "child_conversation": conversation,
        "child_participant": key,
        "child_generation": generation,
        "native_generation": native_generation,
    }


def resolve(
    state: WorkspaceState,
    request: JSONObject,
) -> tuple[str, records.Invocation, Participant]:
    """Resolve exact invocation and parent pending authority before a child transition.

    Args:
        state: Locked issue state.
        request: Internal request naming the invocation.

    Returns:
        Invocation ID, phase-specific record and its current parent participant.

    Raises:
        WorkspaceError: Invocation, parent generation or pending correlation is invalid.
    """
    # Committed invocation metadata selects authority; callback arrival cannot create it.
    identifier = token(request["invocation_id"])
    invocation = state.get("cursor_invocations", {}).get(identifier)
    require(invocation is not None, "UNKNOWN_OPERATION")
    assert invocation is not None
    parent = state["participants"].get(invocation["parent"])
    require(
        parent is not None and parent["generation"] == invocation["parent_generation"],
        "STALE_BINDING",
    )
    assert parent is not None
    pending = parent["pending"].get(invocation["tool_id"])
    require(
        invocation["phase"] == "retired"
        or pending is not None
        and pending.get("cursor_invocation") == identifier,
        "REQUEST_CONFLICT",
    )
    return identifier, invocation, parent


def commit(
    store: Store,
    issue: Issue,
    state: WorkspaceState,
    files: PayloadFiles,
    request: JSONObject,
    result: JSONObject,
    binding: str | None = None,
) -> JSONObject:
    """Publish one transition and reconstruct its derived binding afterward.

    Args:
        store: Validated caller store.
        issue: Locked issue with the existing recoverable writer.
        state: Isolated staged issue state.
        files: Verified committed payload.
        request: Exact transition request.
        result: Bounded response to persist with request replay evidence.
        binding: Participant whose derived binding needs publication after commit.

    Returns:
        The committed lifecycle response.

    Raises:
        OSError: Issue publication or derived binding publication fails.
        WorkspaceError: Existing transaction capacity or integrity checks reject publication.
    """
    # No model or adapter call occurs while this issue lock is held.
    response = issue.commit(state, files, request, result, request["operation"])
    if binding is not None:
        store.save_binding(issue.committed_state(), binding)
    return response


def plan(
    store: Store,
    issue: Issue,
    state: WorkspaceState,
    files: PayloadFiles,
    request: JSONObject,
) -> JSONObject:
    """Install or replace unused coordinator intent without admitting native work.

    Args:
        store: Validated store.
        issue: Locked issue.
        state: Staged state.
        files: Verified payload.
        request: Revision-checked public planning request.

    Returns:
        The committed plan response or its exact prior result.

    Raises:
        WorkspaceError: Current parent, plan, scope or reservation checks reject intent.
    """
    from nplet.lifecycle import task_workspace as core

    # Exact planning replay still requires the current ready parent.
    previous = state["requests"].get(sha(request["request_id"].encode()))
    if previous is not None:
        ready(state, request, files)
        return previous["result"]
    plans = state.setdefault("cursor_plans", {})
    key = participant_key(request)
    ready(state, request, files)
    expected(state, request)
    plan_id = token(request["plan_id"])
    existing_plan = plans.get(plan_id)
    require(
        existing_plan is None or existing_plan["invocation_id"] is None,
        "REQUEST_CONFLICT",
    )
    packet = copy.deepcopy(core.validate_packet(request["packet"]))
    require(all(ref["reader"] == "cursor-child" for ref in packet), "SCOPE_MISSING")
    paths = request["owned_paths"]
    require(isinstance(paths, list) and len(paths) <= 64 and len(set(paths)) == len(paths))
    # Reserve notes against both installed owners and all still-unconsumed plans.
    resumed = request.get("resume_child")
    if resumed is not None:
        token(resumed)
    resumed_key = None
    retired = None
    if resumed is not None:
        retired = resumable_member(state, request["session_id"], resumed)
        resumed_key = child_key(request["session_id"], resumed)
        conversation_available(state, resumed)
    # An unconsumed plan reserves its known resume conversation too.
    if resumed is not None:
        require(
            not any(
                other_id != plan_id
                and plan["resume_child"] == resumed
                and plan["invocation_id"] is None
                for other_id, plan in plans.items()
            ),
            "REQUEST_CONFLICT",
        )
    for path in paths:
        core.payload_path(path)
        require(
            path != "roadmap.md" and state["owners"].get(path) in {None, resumed_key},
            "NOT_OWNER",
        )
        require(
            not path_reserved(state, path, plan_id),
            "NOT_OWNER",
        )
    # Retain only planned metadata; Task prompt text never enters issue state.
    require(records.DIGEST.fullmatch(request["task_digest"]), "INVALID_REQUEST")
    plans[plan_id] = {
        "parent": key,
        "parent_generation": request["binding_generation"],
        "parent_session": request["session_id"],
        "parent_conversation": token(request["parent_conversation"]),
        "task_digest": request["task_digest"],
        "packet": packet,
        "owned_paths": paths,
        "resume_child": resumed,
        "invocation_id": None,
        "resume_generation": retired["generation"] if retired is not None else None,
    }
    return commit(store, issue, state, files, request, {"plan_id": plan_id})


def admit(
    store: Store,
    issue: Issue,
    state: WorkspaceState,
    files: PayloadFiles,
    request: JSONObject,
) -> JSONObject:
    """Consume exact intent and reserve its parent pending Task before native allow.

    Args:
        store: Validated store.
        issue: Locked issue.
        state: Staged state.
        files: Verified payload.
        request: Internal exact native admission.

    Returns:
        The committed invocation identity or an active exact/semantic retry result.

    Raises:
        WorkspaceError: Parent readiness, plan, call or reservation checks fail.
    """
    previous = state["requests"].get(sha(request["request_id"].encode()))
    plans = state.setdefault("cursor_plans", {})
    invocations = state.setdefault("cursor_invocations", {})
    # Match the exact planned launch before reserving the existing parent pending slot.
    parent_key, member = ready(state, request, files)
    plan = plans.get(token(request["plan_id"]))
    require(plan is not None, "SCOPE_MISSING")
    assert plan is not None
    tool = token(request["tool_id"])
    native_call = records.native_text(request["native_call"])
    identifier = sha(canonical([parent_key, member["generation"], tool]))
    require(
        plan["parent"] == parent_key
        and plan["parent_generation"] == member["generation"]
        and plan["parent_conversation"] == request["parent_conversation"]
        and plan["task_digest"] == request["task_digest"],
        "REQUEST_CONFLICT",
    )
    existing = invocations.get(identifier)
    # An identical active admission is safe to retry; retired calls never relaunch.
    if existing is not None:
        require(existing["phase"] in {"admitted", "bound"}, "STALE_BINDING")
        admitted_pending = member["pending"].get(tool)
        require(
            existing["plan_id"] == request["plan_id"]
            and existing["native_call"] == native_call
            and admitted_pending is not None
            and admitted_pending.get("cursor_invocation") == identifier,
            "REQUEST_CONFLICT",
        )
        return (
            previous["result"]
            if previous
            else {
                "ok": True,
                "code": "OK",
                "revision": state["revision"],
                "invocation_id": identifier,
            }
        )
    expected(state, request)
    require(plan["invocation_id"] is None and tool not in member["pending"], "REQUEST_CONFLICT")
    require(
        not any(
            inv["parent"] == parent_key
            and inv["parent_generation"] == member["generation"]
            and inv["native_call"] == native_call
            for inv in invocations.values()
        ),
        "REQUEST_CONFLICT",
    )
    # Recheck a known conversation immediately before its native admission.
    resumed = plan["resume_child"]
    if resumed is not None:
        conversation_available(state, resumed)
        resumable_member(state, plan["parent_session"], resumed, plan["resume_generation"])
    # One parent pending record is the active charge; invocation phase is correlation data.
    admitted: records.AdmittedInvocation = {
        **plan,
        "plan_id": request["plan_id"],
        "tool_id": tool,
        "native_call": native_call,
        "phase": "admitted",
        "child_conversation": None,
        "child_participant": None,
        "child_generation": None,
        "native_generation": None,
        "terminal_result": None,
    }
    invocations[identifier] = admitted
    plan["invocation_id"] = identifier
    member["pending"][tool] = {"status": "pending", "cursor_invocation": identifier}
    return commit(store, issue, state, files, request, {"invocation_id": identifier})


def bind_child(
    store: Store,
    issue: Issue,
    state: WorkspaceState,
    files: PayloadFiles,
    request: JSONObject,
) -> JSONObject:
    """Bind a genuine child callback to exact admitted identity and independent scope.

    Args:
        store: Validated store.
        issue: Locked issue.
        state: Staged state.
        files: Verified payload.
        request: Internal actual child callback.

    Returns:
        The fresh child generation, or an unchanged duplicate plus repaired binding.

    Raises:
        WorkspaceError: Callback, generation, native link or output ownership conflicts.
    """
    from nplet.lifecycle import task_workspace as core

    identifier, invocation, _parent = resolve(state, request)
    conversation = token(request["child_conversation"])
    key = child_key(invocation["parent_session"], conversation)
    member = state["participants"].get(key)
    previous = state["requests"].get(sha(request["request_id"].encode()))
    # Actual host identity must match the admitted parent's normalized child session.
    require(
        participant_key(request) == key
        and request["session_id"] == invocation["parent_session"] + "/agent/" + conversation,
        "NOT_OWNER",
    )
    require(request["tool_id"] == invocation["tool_id"], "REQUEST_CONFLICT")
    native_generation = request.get("native_generation")
    if native_generation is not None:
        token(native_generation)
    # Duplicate callbacks may repair the binding but never replace generation authority.
    if invocation["phase"] == "bound":
        require(
            invocation["child_participant"] == key
            and invocation["native_generation"] == native_generation
            and member is not None
            and member.get("cursor_invocation") == identifier
            and member["generation"] == invocation["child_generation"]
            and member["status"] in {"attached", "ready"},
            "STALE_BINDING",
        )
        assert member is not None
        store.save_binding(state, key)
        return (
            previous["result"]
            if previous
            else {
                "ok": True,
                "code": "OK",
                "revision": state["revision"],
                "binding_generation": member["generation"],
                "invocation_id": identifier,
            }
        )
    require(invocation["phase"] == "admitted", "STALE_BINDING")
    assert invocation["phase"] == "admitted"
    require(invocation["resume_child"] in {None, conversation}, "REQUEST_CONFLICT")
    claim = claim_child_identity(state, invocation, identifier, conversation, native_generation)
    require(key != state["coordinator"], "NOT_OWNER")
    core.validate_packet(invocation["packet"])
    # The shared constructor defines both persisted packet copies and variable owner additions.
    participant, assignment, owners = records.child_scope(invocation, claim, identifier)
    for path in owners:
        require(state["owners"].get(path) in {None, key}, "NOT_OWNER")
    state["owners"].update(owners)
    state.setdefault("assignments", {})[key] = assignment
    state["participants"][key] = participant
    bound = records.bound_record(invocation, claim)
    state["cursor_invocations"][identifier] = bound
    return commit(
        store,
        issue,
        state,
        files,
        request,
        {"binding_generation": claim["child_generation"], "invocation_id": identifier},
        key,
    )


def retire(
    store: Store,
    issue: Issue,
    state: WorkspaceState,
    files: PayloadFiles,
    request: JSONObject,
) -> JSONObject:
    """Atomically fence one exact generation and release only its parent Task charge.

    Args:
        store: Validated store.
        issue: Locked issue.
        state: Staged state.
        files: Verified payload.
        request: Internal pinned terminal projection.

    Returns:
        The retired generation or an exact duplicate result with no new issue commit.

    Raises:
        WorkspaceError: Parent, evidence, generation, actual identity or pending effects conflict.
    """
    identifier, invocation, parent = resolve(state, request)
    parent_key, parent = ready(state, request, files)
    require(
        parent_key == invocation["parent"]
        and request["binding_generation"] == invocation["parent_generation"],
        "NOT_OWNER",
    )
    # The shared terminal contract is also used by the native decoder and byte projection.
    require(isinstance(request.get("result"), dict), "INVALID_REQUEST")
    observed = records.terminal_evidence(request["result"])
    require(
        observed["parent_conversation"] == invocation["parent_conversation"]
        and observed["native_call"] == invocation["native_call"],
        "RESULT_NOT_TERMINAL",
    )
    conversation = observed["child_conversation"]
    key = child_key(invocation["parent_session"], conversation)
    member = state["participants"].get(key)
    previous = state["requests"].get(sha(request["request_id"].encode()))
    # Old terminal evidence cannot detach a later planned resume, including exact request replay.
    if invocation["phase"] == "retired":
        require(invocation["terminal_result"] == observed, "REQUEST_CONFLICT")
        require(
            member is not None
            and member["generation"] == invocation["child_generation"]
            and member.get("cursor_invocation") == identifier,
            "STALE_BINDING",
        )
        assert member is not None
        store.save_binding(state, key)
        return (
            previous["result"]
            if previous
            else {
                "ok": True,
                "code": "OK",
                "revision": state["revision"],
                "invocation_id": identifier,
                "binding_generation": member["generation"],
            }
        )
    expected(state, request)
    require(invocation["phase"] in {"admitted", "bound"}, "STALE_BINDING")
    # Bound retirement reuses existing authority; early return claims classification only.
    if invocation["phase"] == "bound":
        require(
            invocation["child_participant"] == key
            and member is not None
            and member["generation"] == invocation["child_generation"]
            and member.get("cursor_invocation") == identifier,
            "STALE_BINDING",
        )
        assert member is not None
        require(not member["pending"], "PENDING_OPERATION")
        claim: records.ChildClaim = {
            "child_conversation": conversation,
            "child_participant": key,
            "child_generation": invocation["child_generation"],
            "native_generation": invocation["native_generation"],
        }
    else:
        claim = claim_child_identity(state, invocation, identifier, conversation, None)
        member = records.child_member(claim, identifier, None, status="detached")
        state["participants"][key] = member
    # Packet installation is exclusive to bind; both paths share the same identity claim schema.
    member.update({"status": "detached", "ack": None})
    del parent["pending"][invocation["tool_id"]]
    retired = records.retired_record(invocation, claim, observed)
    state["cursor_invocations"][identifier] = retired
    return commit(
        store,
        issue,
        state,
        files,
        request,
        {"invocation_id": identifier, "binding_generation": claim["child_generation"]},
        key,
    )


def operate(
    store: Store,
    issue: Issue,
    state: WorkspaceState,
    files: PayloadFiles,
    request: JSONObject,
) -> JSONObject:
    """Dispatch only invocation transitions before ordinary lifecycle replay.

    Args:
        store: Validated store.
        issue: Locked recovered issue.
        state: Isolated staged state.
        files: Verified payload.
        request: Explicit public plan or internal transition.

    Returns:
        The phase-specific transition result.

    Raises:
        WorkspaceError: Host, retained issue or operation is unsupported.
    """
    from nplet.lifecycle import task_workspace as core

    # Native decoding and transaction recovery happen outside this focused dispatcher.
    require(request["host"] == "cursor", "HOST_UNSUPPORTED")
    require(
        state["disposition"] not in core.TERMINAL and state["storage"] == "present",
        "TERMINAL",
    )
    # Derived bindings are reconstructed only after this existing issue has been validated.
    store.view(issue.id)
    binding = store.binding()
    operation = request["operation"]
    if operation == "cursor-bind-child":
        require(not binding or binding["issue_id"] == issue.id, "BINDING_CONFLICT")
    else:
        require(
            binding is not None
            and binding["issue_id"] == issue.id
            and binding["binding_generation"] == request.get("binding_generation"),
            "BINDING_MISSING",
        )
    handlers = {
        "cursor-plan": plan,
        "cursor-admit": admit,
        "cursor-bind-child": bind_child,
        "cursor-retire": retire,
    }
    require(operation in handlers, "UNSUPPORTED_OPERATION")
    return handlers[operation](store, issue, state, files, request)


def ready(
    state: WorkspaceState, request: JSONObject, files: PayloadFiles
) -> tuple[str, Participant]:
    """Require a ready Cursor coordinator without granting child parent authority.

    Args:
        state: Locked issue state.
        request: Explicit parent lifecycle identity.
        files: Verified committed payload.

    Returns:
        The ready coordinator identity and record.

    Raises:
        WorkspaceError: Host, ownership, generation or current sources are invalid.
    """
    from nplet.lifecycle import task_workspace as core

    # Verify the parent role before checking its assigned current source bytes.
    require(request["host"] == "cursor", "HOST_UNSUPPORTED")
    key, member = core.authorize(state, request, coordinator=True)
    require("cursor_invocation" not in member, "NOT_OWNER")
    core.packet_reads(state, member, files)
    require(
        member["status"] == "ready" and member["ack"] == sha(canonical(member["packet"])),
        "NOT_READY",
    )
    return key, member


def path_reserved(state: WorkspaceState, path: str, excluded_plan: str | None = None) -> bool:
    """Keep an output reserved until its launch binds an owner or retires.

    Args:
        state: Locked issue state containing plans and invocations.
        path: Managed output whose reservation is being checked.
        excluded_plan: Unconsumed plan explicitly being replaced, if any.

    Returns:
        Whether another unconsumed or admitted plan still reserves the path.
    """
    # Bound children use ordinary ownership; admitted children still need their reservation.
    for identifier, plan in state.get("cursor_plans", {}).items():
        if identifier != excluded_plan and path in plan["owned_paths"]:
            invocation = plan["invocation_id"]
            if invocation is None or state["cursor_invocations"][invocation]["phase"] == "admitted":
                return True
    return False
