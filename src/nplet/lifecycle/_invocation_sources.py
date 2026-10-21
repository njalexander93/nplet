"""Commit native source receipts and validate reader acknowledgments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nplet.lifecycle import _invocation_records as records
from nplet.lifecycle._errors import require
from nplet.lifecycle._primitives import canonical, sha, token

if TYPE_CHECKING:
    from nplet.lifecycle import task_workspace as core
    from nplet.lifecycle.task_workspace import (
        JSONObject,
        Participant,
        PayloadFiles,
        WorkspaceState,
    )


def source_callback(
    state: WorkspaceState, files: PayloadFiles, request: JSONObject, participant: Participant
) -> JSONObject:
    """Record verified content or settle one exact Cursor source Read callback.

    Caller holds the existing issue transaction and has authorized this generation.

    Args:
        state: Current locked issue state.
        files: Verified managed payload bytes.
        request: Adapter-internal closed content/success/failure metadata.
        participant: Current independently bound Cursor child.

    Returns:
        Whether this callback verified content or finalized a current-packet receipt.

    Raises:
        WorkspaceError: Unknown/foreign call, closed shape, identity or content conflicts.
    """
    from nplet.lifecycle import task_workspace as core

    # This narrow protocol cannot authorize a parent, another host or a generic completion.
    require(request["host"] == "cursor" and "cursor_invocation" in participant, "NOT_OWNER")
    phase = request.get("source_event")
    require(phase in {"content", "success", "failure"}, "INVALID_REQUEST")
    allowed = {
        "schema_version",
        "operation",
        "request_id",
        "worktree",
        "host",
        "session_id",
        "repo_id",
        "issue_id",
        "issue_uuid",
        "binding_generation",
        "expected_revision",
        "source_event",
        "tool_id",
        "source_path",
    }
    # Raw source content is never accepted by or persisted in lifecycle state.
    if phase == "content":
        allowed |= {"observed_sha256", "content_length"}
    elif phase == "success":
        allowed |= {"content_length"}
    require(set(request) <= allowed, "INVALID_REQUEST")
    tool = token(request["tool_id"])
    pending = participant["pending"].get(tool)
    require(pending is not None and "cursor_source" in pending, "UNKNOWN_OPERATION")
    assert pending is not None
    source = pending["cursor_source"]
    require(
        source["invocation_id"] == participant["cursor_invocation"]
        and source["generation"] == participant["generation"]
        and request.get("source_path") == source["file_path"],
        "REQUEST_CONFLICT",
    )
    digest = sha(canonical(participant["packet"]))
    # Content is provisional until a distinct correlated native success is observed.
    if phase == "content":
        refs = core.packet_reads(state, participant, files)
        require(source["packet_digest"] == digest, "SOURCE_STALE")
        require(
            any(
                ref["available"]
                and ref["locator"] == source["locator"]
                and ref["sha256"] == source["sha256"]
                for ref in refs
            ),
            "SOURCE_STALE",
        )
        observed = request.get("observed_sha256")
        length = request.get("content_length")
        require(isinstance(observed, str) and records.DIGEST.fullmatch(observed), "INVALID_REQUEST")
        require(type(length) is int and 0 <= length <= core.MAX_FILE, "INVALID_REQUEST")
        assert isinstance(observed, str)
        assert isinstance(length, int)
        require(observed == source["sha256"], "SOURCE_DELIVERY_REQUIRED")
        proof = records.SourceProof(sha256=observed, content_length=length)
        require(source.get("provisional") in (None, proof), "REQUEST_CONFLICT")
        source["provisional"] = proof
        return {"source_content_verified": True, "source_receipt": False}
    # Known finished failure/stale/unmatched success settles this call without credit.
    receipt = False
    if (
        phase == "success"
        and source.get("provisional") is not None
        and source["packet_digest"] == digest
    ):
        provisional = source["provisional"]
        assert provisional is not None
        length = request.get("content_length")
        require(type(length) is int and length >= 0, "INVALID_REQUEST")
        # Source refresh/missing bytes must not strand an already finished file Read.
        try:
            refs = core.packet_reads(state, participant, files)
        except (core.WorkspaceError, OSError):
            refs = []
        current = any(
            ref["available"]
            and ref["locator"] == source["locator"]
            and ref["sha256"] == source["sha256"]
            for ref in refs
        )
        if current and length == provisional["content_length"]:
            # Prune old-packet receipts before adding at most one of64current assigned locators.
            receipts = {
                locator: item
                for locator, item in participant.get("cursor_sources", {}).items()
                if item["packet_digest"] == digest
            }
            receipts[source["locator"]] = records.SourceReceipt(
                packet_digest=digest,
                sha256=source["sha256"],
                invocation_id=participant["cursor_invocation"],
            )
            require(len(receipts) <= 64, "SIZE_LIMIT")
            participant["cursor_sources"] = receipts
            receipt = True
    del participant["pending"][tool]
    return {"source_content_verified": False, "source_receipt": receipt}


def authorize_acknowledgment(
    member: Participant,
    references: list[core.SourceAvailability],
    digest: str,
) -> None:
    """Require exact delivered source credit before a child acknowledges its current packet.

    Args:
        member: Current independently authorized participant.
        references: Currently available assigned references.
        digest: Current exact packet digest.

    Raises:
        WorkspaceError: An available source lacks its invocation-specific completed receipt.
    """
    if "cursor_invocation" not in member:
        return
    receipts = member.get("cursor_sources", {})
    require(
        all(
            not ref["available"]
            or receipts.get(ref["locator"])
            == records.SourceReceipt(
                packet_digest=digest,
                sha256=ref["sha256"],
                invocation_id=member["cursor_invocation"],
            )
            for ref in references
        ),
        "SOURCE_DELIVERY_REQUIRED",
    )
