"""Enforce invocation child actor, target and owned-scope authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nplet.lifecycle._errors import require
from nplet.lifecycle._primitives import canonical, sha

if TYPE_CHECKING:
    from nplet.lifecycle import task_workspace as core
    from nplet.lifecycle.task_workspace import (
        Participant,
        PayloadFiles,
        WorkspaceState,
    )

CHILD_OPERATIONS = frozenset(
    {
        "read",
        "ready",
        "acknowledge",
        "update",
        "event",
        "tool-start",
        "source-read-start",
        "tool-complete",
        "cursor-source-callback",
        "attach",
        "resume",
        "bind",
    }
)


def child_active(state: WorkspaceState, key: str, member: Participant) -> None:
    """Fence a classified child to its currently bound invocation.

    Args:
        state: Locked issue state.
        key: Exact participant identity.
        member: Recorded participant being authorized.

    Raises:
        WorkspaceError: The invocation retired or belongs to another generation.
    """
    # Ordinary participants preserve the existing lifecycle contract.
    identifier = member.get("cursor_invocation")
    if identifier is None:
        return
    # Classification survives detachment; only a new admitted binding can replace it.
    invocation = state.get("cursor_invocations", {}).get(identifier)
    require(
        invocation is not None
        and invocation["phase"] == "bound"
        and invocation["child_participant"] == key
        and invocation["child_generation"] == member["generation"],
        "STALE_BINDING",
    )


def authorize_actor(state: WorkspaceState, key: str, member: Participant, operation: str) -> None:
    """Apply static child capability policy after generic generation/status validation.

    Args:
        state: Locked issue state.
        key: Actual current actor.
        member: Current participant.
        operation: Requested lifecycle operation.

    Raises:
        WorkspaceError: The child retired or the actor requests parent-only authority.
    """
    # Target and exact pending-record checks remain at their existing data-dependent call sites.
    child_active(state, key, member)
    if "cursor_invocation" in member:
        require(operation in CHILD_OPERATIONS, "NOT_OWNER")


def authorize_target(member: Participant) -> None:
    """Keep generic detach/reconcile and coordinator transfer outside child retirement.

    Args:
        member: The resolved operation target, which may differ from its parent actor.

    Raises:
        WorkspaceError: The target belongs to an invocation.
    """
    require("cursor_invocation" not in member, "NOT_OWNER")


def authorize_pending(pending: core.PendingOperation | None, *, poll: bool = False) -> None:
    """Protect typed invocation/source obligations after generic call/handle resolution.

    Args:
        pending: The exact resolved original pending operation.
        poll: Poll admission checks only invocation Tasks, preserving existing ordering.

    Raises:
        WorkspaceError: Generic completion/polling would bypass invocation or source settlement.
    """
    protected = {"cursor_invocation"} if poll else {"cursor_invocation", "cursor_source"}
    require(pending is None or not protected.intersection(pending), "REQUEST_CONFLICT")


def authorize_scope(
    state: WorkspaceState,
    target: str,
    packet: list[core.SourceRef],
    paths: list[str],
) -> None:
    """Permit active child source digest refresh without widening its planned capabilities.

    Args:
        state: Locked issue state.
        target: Resolved independent reader.
        packet: Validated replacement packet.
        paths: Requested output ownership additions.

    Raises:
        WorkspaceError: Retired identity, new sources or wider output scope are requested.
    """
    member = state["participants"].get(target)
    if member is None or "cursor_invocation" not in member:
        return
    child_active(state, target, member)
    current = member["packet"]
    require(
        current is not None
        and len(packet) == len(current)
        and all(new == {**old, "sha256": new["sha256"]} for old, new in zip(current, packet)),
        "SCOPE_MISSING",
    )
    invocation = state["cursor_invocations"][member["cursor_invocation"]]
    require(set(paths) <= set(invocation["owned_paths"]), "NOT_OWNER")


def authorize_update(
    state: WorkspaceState,
    member: Participant,
    files: PayloadFiles,
    path: str,
) -> None:
    """Require current planned output and independent readiness for a child mutation.

    Args:
        state: Locked issue state.
        member: Current independently authorized participant.
        files: Verified committed payload.
        path: Already validated owned managed-note target.

    Raises:
        WorkspaceError: An older output, stale source or unready packet is used.
    """
    from nplet.lifecycle import task_workspace as core

    if "cursor_invocation" not in member:
        return
    invocation = state["cursor_invocations"][member["cursor_invocation"]]
    require(path in invocation["owned_paths"], "NOT_OWNER")
    core.packet_reads(state, member, files)
    require(
        member["status"] == "ready" and member["ack"] == sha(canonical(member["packet"])),
        "NOT_READY",
    )
