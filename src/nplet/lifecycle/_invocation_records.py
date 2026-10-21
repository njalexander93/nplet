"""Typed invocation metadata shared by lifecycle transitions and settlement projections.

Native result decoding stays in the Cursor adapter. These constructors define only
the bounded records retained by the local lifecycle transaction.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from nplet.lifecycle._errors import require

if TYPE_CHECKING:
    from nplet.lifecycle.task_workspace import Assignment, Participant, SourceRef

TOKEN_LIMIT = 160
NATIVE_TEXT_LIMIT = 2048
MAX_DURATION = (1 << 64) - 1
DIGEST = re.compile(r"[0-9a-f]{64}")

# Fixed JSON fragments use the maximum allowed escaping, hashes and bounded scalar fields.
# Independent maximal publication examples in test_cursor_settlement_reserve prove each bound.
# Participant syntax includes its generation key; the arbitrary-width integer is charged separately.
BINDING_METADATA_BYTES = 791
RETIREMENT_METADATA_BYTES = 26_473
PARTICIPANT_METADATA_BYTES = 246
ASSIGNMENT_METADATA_BYTES = 100
REQUEST_METADATA_BYTES = 317
SOURCE_RECEIPT_METADATA_BYTES = 265
SOURCE_PROOF_METADATA_BYTES = 119
EFFECT_METADATA_BYTES = 355
COUNTER_HEAD_METADATA_BYTES = 75


class InvocationPlan(TypedDict):
    """Retain explicit parent authority, source template and reserved outputs."""

    parent: str
    parent_generation: int
    parent_session: str
    parent_conversation: str
    task_digest: str
    packet: list[SourceRef]
    owned_paths: list[str]
    resume_child: str | None
    resume_generation: int | None
    invocation_id: str | None


class ChildClaim(TypedDict):
    """Identify one conversation's framework generation without granting readiness."""

    child_conversation: str
    child_participant: str
    child_generation: int
    native_generation: str | None


class TerminalEvidence(TypedDict):
    """Retain the bounded projection of one decoded foreground Task result."""

    parent_conversation: str
    native_call: str
    storage_key: str
    child_conversation: str
    kind: Literal["foreground-success"]
    is_background: Literal[False]
    background_reason: Literal[0]
    duration_ms: int
    binary_sha256: str
    executor_sha256: str
    workbench_sha256: str


class InvocationBase(InvocationPlan):
    """Retain one consumed plan and its exact normalized/native call correlation."""

    plan_id: str
    tool_id: str
    native_call: str


class AdmittedInvocation(InvocationBase):
    """Reserve parent pending work before an actual child callback exists."""

    phase: Literal["admitted"]
    child_conversation: None
    child_participant: None
    child_generation: None
    native_generation: None
    terminal_result: None


class BoundInvocation(InvocationBase, ChildClaim):
    """Bind one current child generation and its independent source assignment."""

    phase: Literal["bound"]
    terminal_result: None


class RetiredInvocation(InvocationBase, ChildClaim):
    """Retain exact terminal evidence after atomically fencing the child generation."""

    phase: Literal["retired"]
    terminal_result: TerminalEvidence


Invocation = AdmittedInvocation | BoundInvocation | RetiredInvocation
ActiveInvocation = AdmittedInvocation | BoundInvocation


class SourceProof(TypedDict):
    """Describe provisional source bytes before a distinct successful completion."""

    sha256: str
    content_length: int


class SourceReceipt(TypedDict):
    """Credit exact current-packet source bytes to one independently bound child."""

    packet_digest: str
    sha256: str
    invocation_id: str


class SourceEffect(TypedDict):
    """Carry one exact assigned source through its two native callbacks."""

    invocation_id: str
    generation: int
    packet_digest: str
    locator: str
    sha256: str
    file_path: str
    provisional: SourceProof | None


def native_text(value: object) -> str:
    """Preserve bounded native text, including newline, without token normalization.

    Args:
        value: Native call or source-storage locator.

    Returns:
        The exact original string.

    Raises:
        WorkspaceError: The value is not bounded nonempty UTF-8 text or contains NUL.
    """
    # Bound serialized metadata while preserving the native row identity.
    require(isinstance(value, str), "INVALID_REQUEST")
    assert isinstance(value, str)
    require(
        0 < len(value.encode("utf-8")) <= NATIVE_TEXT_LIMIT and "\x00" not in value,
        "INVALID_REQUEST",
    )
    return value


def terminal_evidence(value: dict[str, Any]) -> TerminalEvidence:
    """Construct the single retained terminal contract after native decoding.

    Args:
        value: Projected metadata from the adapter's bounded pinned decoder.

    Returns:
        A fresh validated foreground-result record.

    Raises:
        WorkspaceError: Shape, string bounds, hashes or foreground result fields are invalid.
    """
    # One field inventory serves the adapter, lifecycle persistence and reservation projection.
    require(
        isinstance(value, dict) and set(value) == set(TerminalEvidence.__annotations__),
        "INVALID_REQUEST",
    )
    for name in ("parent_conversation", "child_conversation"):
        token = value[name]
        require(
            isinstance(token, str)
            and 0 < len(token) <= TOKEN_LIMIT
            and all(32 < ord(char) < 127 for char in token),
            "INVALID_REQUEST",
        )
    native_text(value["native_call"])
    native_text(value["storage_key"])
    # Native result semantics are decoded by the adapter; retain only foreground evidence.
    require(
        value["kind"] == "foreground-success"
        and value["is_background"] is False
        and type(value["background_reason"]) is int
        and value["background_reason"] == 0
        and type(value["duration_ms"]) is int
        and 0 <= value["duration_ms"] <= MAX_DURATION,
        "RESULT_NOT_TERMINAL",
    )
    for name in ("binary_sha256", "executor_sha256", "workbench_sha256"):
        require(isinstance(value[name], str) and DIGEST.fullmatch(value[name]), "INVALID_REQUEST")
    # Explicit construction keeps retained fields statically checked against the shared record.
    return TerminalEvidence(
        parent_conversation=value["parent_conversation"],
        native_call=value["native_call"],
        storage_key=value["storage_key"],
        child_conversation=value["child_conversation"],
        kind="foreground-success",
        is_background=False,
        background_reason=0,
        duration_ms=value["duration_ms"],
        binary_sha256=value["binary_sha256"],
        executor_sha256=value["executor_sha256"],
        workbench_sha256=value["workbench_sha256"],
    )


def child_member(
    claim: ChildClaim,
    invocation_id: str,
    packet: list[SourceRef] | None,
    *,
    status: Literal["attached", "detached"],
) -> Participant:
    """Construct fresh child scope or terminal-only classification without acknowledgment.

    Args:
        claim: Validated exact conversation/generation claim.
        invocation_id: Admitted invocation owning that generation.
        packet: Actual independent packet for binding, or None for terminal-only classification.
        status: Binding installs attached; terminal-only evidence installs detached.

    Returns:
        A fresh participant record with no pending work or inherited source receipts.
    """
    return {
        "generation": claim["child_generation"],
        "status": status,
        "pending": {},
        "packet": packet,
        "ack": None,
        "cursor_invocation": invocation_id,
    }


def child_scope(
    invocation: ActiveInvocation,
    claim: ChildClaim,
    invocation_id: str,
) -> tuple[Participant, Assignment, dict[str, str]]:
    """Construct the actual child binding records.

    Args:
        invocation: Admitted plan with exact bounded references and output paths.
        claim: Validated identity selected from an actual callback.
        invocation_id: Exact parent pending correlation.

    Returns:
        Fresh participant, stored assignment and note-owner additions.
    """
    # Copy each reference and replace only the actual independent reader identity.
    packet: list[SourceRef] = [
        {**reference, "reader": claim["child_participant"]} for reference in invocation["packet"]
    ]
    # Install both packet copies and every planned output owner together.
    return (
        child_member(claim, invocation_id, packet, status="attached"),
        {"packet": packet},
        {path: claim["child_participant"] for path in invocation["owned_paths"]},
    )


def bound_record(
    invocation: AdmittedInvocation,
    claim: ChildClaim,
) -> BoundInvocation:
    """Construct the bound phase for publication.

    Args:
        invocation: Exact consumed admitted plan.
        claim: Validated identity allocated by the shared claim operation.

    Returns:
        The bound phase without terminal evidence.
    """
    return {**invocation, **claim, "phase": "bound", "terminal_result": None}


def retired_record(
    invocation: ActiveInvocation,
    claim: ChildClaim,
    evidence: TerminalEvidence,
) -> RetiredInvocation:
    """Construct the retired phase for publication.

    Args:
        invocation: Exact admitted or bound generation.
        claim: Exact child identity claimed by this invocation.
        evidence: The single validated terminal record contract.

    Returns:
        The immutable retained terminal phase.
    """
    return {**invocation, **claim, "phase": "retired", "terminal_result": evidence}
