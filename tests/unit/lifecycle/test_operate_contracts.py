"""Exercise lifecycle decisions with a bounded in-memory persistence adapter."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import pytest

from nplet.lifecycle import task_workspace as core
from tests.unit.lifecycle.memory_support import MemoryIssue, MemoryStore, base_request

pytestmark = pytest.mark.unit


def test_read_returns_only_assigned_matching_references_without_commit() -> None:
    """Read the packet and current revision without changing state."""
    # Create the modeled issue and retain its pre-operation state.
    store, issue = MemoryStore(), MemoryIssue()
    before = copy.deepcopy(issue.state)
    # Dispatch the read request against the modeled issue.
    result = core.operate(store, issue, base_request("read"))  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["revision"] == 7
    assert [ref["id"] for ref in result["references"]] == ["roadmap"]
    assert result["references"][0]["available"] is True
    assert issue.state == before
    assert issue.commits == []
    assert issue.recoveries == 1


def test_diagnose_reports_absent_and_present_issue_without_binding_read() -> None:
    """Diagnosis reveals committed presence, storage and only the caller's own packet."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    before = copy.deepcopy(issue.state)
    key = core.participant_key(base_request("diagnose"))
    # Dispatch the diagnose request against the modeled issue.
    result = core.operate(store, issue, base_request("diagnose"))  # type: ignore[arg-type]
    # A recorded participant also receives its committed packet exactly as stored.
    assert result == {
        "ok": True,
        "code": "PRESENT",
        "repo_id": "repo",
        "issue_id": "TEST-30",
        "revision": 7,
        "storage": "present",
        "packet": before["participants"][key]["packet"],
        "sources": [
            {
                "id": "roadmap",
                "locator": "roadmap.md",
                "recorded_sha256": core.sha(b"approved roadmap"),
                "current_sha256": core.sha(b"approved roadmap"),
                "available": True,
            }
        ],
    }
    assert all("available" not in ref for ref in result["packet"])
    # The returned packet is a copy; changing it cannot alter committed state.
    result["packet"][0]["sha256"] = "0" * 64
    assert issue.state == before
    # A session that is not a recorded participant gets no packet.
    other = core.operate(  # type: ignore[arg-type]
        store, issue, base_request("diagnose", session_id="other-session")
    )
    assert "packet" not in other and "sources" not in other and other["code"] == "PRESENT"
    # Model an absent issue record even though the store wrapper exists.
    issue.state = {}
    # Dispatch the diagnose request against the modeled issue.
    result = core.operate(store, issue, base_request("diagnose"))  # type: ignore[arg-type]
    # Confirm the exact participant and issue report an absent binding.
    assert result == {
        "ok": True,
        "code": "ABSENT",
        "repo_id": "repo",
        "issue_id": "TEST-30",
        "revision": None,
        "storage": "absent",
    }
    assert issue.commits == []


def stale_packet_issue(tmp_path: Path) -> tuple[MemoryIssue, dict[str, Any]]:
    """Model a coordinator whose packet mixes current, changed and unreadable sources.

    Args:
        tmp_path: Temporary directory holding the external governing sources.

    Returns:
        The modeled issue and the caller's participant record.
    """
    # Write one changed and one unchanged external source; leave a third absent.
    # Write through the lifecycle directory boundary so every native backend can read them.
    with core.Directory.absolute(tmp_path) as sources:
        sources.write("AGENTS.md", b"edited rules\n")
        sources.write("development.md", b"unchanged\n")
    issue = MemoryIssue()
    key = core.participant_key(base_request("diagnose"))
    member = issue.state["participants"][key]
    template = member["packet"][0]
    # Append references for the changed, unchanged, missing and unknown payload sources.
    member["packet"] = [
        template,
        {
            **template,
            "id": "rules",
            "locator": str(tmp_path / "AGENTS.md"),
            "sha256": core.sha(b"original rules\n"),
        },
        {
            **template,
            "id": "dev",
            "locator": str(tmp_path / "development.md"),
            "sha256": core.sha(b"unchanged\n"),
        },
        {
            **template,
            "id": "gone",
            "locator": str(tmp_path / "missing.md"),
            "sha256": core.sha(b"gone\n"),
            "required": False,
        },
        {
            **template,
            "id": "note",
            "locator": "context/absent.md",
            "sha256": core.sha(b"absent\n"),
            "required": False,
        },
    ]
    member["ack"] = core.sha(core.canonical(member["packet"]))
    return issue, member


def test_diagnose_sources_report_current_digests_for_every_reference(tmp_path: Path) -> None:
    """Diagnose aligns each packet reference with its current digest and availability.

    Args:
        tmp_path: Temporary directory holding the external governing sources.
    """
    # Model a coordinator with changed, unchanged and unreadable references.
    issue, member = stale_packet_issue(tmp_path)
    before = copy.deepcopy(issue.state)
    result = core.operate(MemoryStore(), issue, base_request("diagnose"))  # type: ignore[arg-type]
    # The packet stays exactly as stored and sources align with it one to one.
    assert result["packet"] == member["packet"]
    assert [s["id"] for s in result["sources"]] == [r["id"] for r in member["packet"]]
    assert [s["locator"] for s in result["sources"]] == [r["locator"] for r in member["packet"]]
    assert [s["recorded_sha256"] for s in result["sources"]] == [
        r["sha256"] for r in member["packet"]
    ]
    # Current digests come from the manifest or the external bytes; unreadable is null.
    assert [s["current_sha256"] for s in result["sources"]] == [
        core.sha(b"approved roadmap"),
        core.sha(b"edited rules\n"),
        core.sha(b"unchanged\n"),
        None,
        None,
    ]
    assert [s["available"] for s in result["sources"]] == [True, False, True, False, False]
    # Diagnosis stays read-only and event-free.
    assert issue.state == before
    assert issue.commits == []


def test_read_stale_failure_names_every_changed_reference(tmp_path: Path) -> None:
    """A required stale source fails read with the changed references and their digests.

    Args:
        tmp_path: Temporary directory holding the external governing sources.
    """
    # Model a coordinator whose required governing source changed after acknowledgment.
    issue, _member = stale_packet_issue(tmp_path)
    before = copy.deepcopy(issue.state)
    with pytest.raises(core.StaleSourceError) as captured:
        core.operate(MemoryStore(), issue, base_request("read"))  # type: ignore[arg-type]
    # The failure keeps its public code and lists only the mismatched references.
    assert captured.value.code == "SOURCE_STALE"
    assert captured.value.stale == [
        {
            "id": "rules",
            "locator": str(tmp_path / "AGENTS.md"),
            "recorded_sha256": core.sha(b"original rules\n"),
            "current_sha256": core.sha(b"edited rules\n"),
            "available": False,
        },
        {
            "id": "gone",
            "locator": str(tmp_path / "missing.md"),
            "recorded_sha256": core.sha(b"gone\n"),
            "current_sha256": None,
            "available": False,
        },
        {
            "id": "note",
            "locator": "context/absent.md",
            "recorded_sha256": core.sha(b"absent\n"),
            "current_sha256": None,
            "available": False,
        },
    ]
    assert issue.state == before
    assert issue.commits == []


def test_read_tolerates_changed_optional_sources_only(tmp_path: Path) -> None:
    """Optional unreadable references stay unavailable without blocking read.

    Args:
        tmp_path: Temporary directory holding the external governing sources.
    """
    # Restore the required governing source so only optional references differ.
    issue, member = stale_packet_issue(tmp_path)
    member["packet"][1]["sha256"] = core.sha(b"edited rules\n")
    result = core.operate(MemoryStore(), issue, base_request("read"))  # type: ignore[arg-type]
    # Read succeeds and reports the optional references as unavailable.
    assert result["ok"] is True
    assert [ref["available"] for ref in result["references"]] == [True, True, True, False, False]


def test_ready_rejects_unacknowledged_packet_without_commit() -> None:
    """Block ordinary tools until the assigned packet was acknowledged."""
    # Create the modeled issue and retain its pre-operation state.
    store, issue = MemoryStore(), MemoryIssue(acknowledged=False)
    before = copy.deepcopy(issue.state)
    # Dispatch the ready request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("ready"))  # type: ignore[arg-type]
    # Confirm the rejected operation reports NOT_READY.
    assert captured.value.code == "NOT_READY"
    assert issue.state == before
    assert issue.commits == []


def test_acknowledge_updates_status_only_for_matching_packet_digest() -> None:
    """Commit readiness only when the caller names the current packet digest."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue(acknowledged=False)
    # Hash the source bytes before testing stale-read detection.
    digest = core.sha(core.canonical(next(iter(issue.state["participants"].values()))["packet"]))
    # Dispatch the acknowledge request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("acknowledge", packet_digest="wrong"))  # type: ignore[arg-type]
    # Confirm the rejected operation reports SOURCE_STALE.
    assert captured.value.code == "SOURCE_STALE"
    assert issue.commits == []
    # Dispatch the acknowledge request against the modeled issue.
    result = core.operate(store, issue, base_request("acknowledge", packet_digest=digest))  # type: ignore[arg-type]
    assert result["ok"] is True
    # Inspect the participant record after the operation.
    member = next(iter(issue.state["participants"].values()))
    # Check reader status and acknowledged source digest.
    assert (member["status"], member["ack"]) == ("ready", digest)
    assert issue.commits[-1][0] == "acknowledge"


def test_update_replaces_owned_note_with_attributable_provenance() -> None:
    """Commit exact replacement bytes and record their source and author."""
    # Create the issue, select its reader, and pin the source reference.
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    source = {"id": "issue", "locator": "context/issue.md", "sha256": core.sha(b"source")}
    request = base_request(
        "update",
        expected_revision=7,
        path="context/note.md",
        old_digest=core.sha(b"old note"),
        content="new note é",
        provenance={"sources": [source], "applicability": "TEST-30", "status": "draft"},
    )
    # Check the operation response and committed issue state.
    assert core.operate(store, issue, request)["ok"] is True  # type: ignore[arg-type]
    assert issue.payload["context/note.md"] == "new note é".encode()
    assert issue.state["provenance"]["context/note.md"]["author"] == key
    assert issue.state["provenance"]["context/note.md"]["sources"] == [source]
    assert issue.commits[-1][0] == "update"


def test_update_rejects_stale_digest_without_writing() -> None:
    """Leave payload and provenance unchanged for stale replacement content."""
    # Create the modeled issue and retain its pre-operation state.
    store, issue = MemoryStore(), MemoryIssue()
    before = copy.deepcopy(issue.state)
    # Dispatch the update request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("update", expected_revision=7, path="context/note.md", old_digest="bad"),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports REVISION_CONFLICT.
    assert captured.value.code == "REVISION_CONFLICT"
    assert issue.state == before
    assert issue.commits == []


def test_tool_start_requires_ack_and_records_only_observed_id() -> None:
    """Reserve a supported tool after packet readiness and reject duplicates."""
    # Create a modeled store and issue, then build the lifecycle request.
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request("tool-start", tool_id="tool-1")
    # Check the operation response and committed issue state.
    assert core.operate(store, issue, request)["ok"] is True  # type: ignore[arg-type]
    # Inspect the participant record after the operation.
    member = next(iter(issue.state["participants"].values()))
    assert member["pending"] == {"tool-1": {"status": "pending"}}
    # Dispatch the tool-start request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    # Confirm the rejected operation reports REQUEST_CONFLICT.
    assert captured.value.code == "REQUEST_CONFLICT"
    assert len(issue.commits) == 1


def test_tool_complete_requires_matching_pending_id_before_settlement() -> None:
    """Never infer completion for an unrecorded tool."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-complete request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("tool-complete", tool_id="other", completed=True))  # type: ignore[arg-type]
    # Confirm the rejected operation reports UNKNOWN_OPERATION.
    assert captured.value.code == "UNKNOWN_OPERATION"
    assert issue.commits == []
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    result = core.operate(
        store, issue, base_request("tool-complete", tool_id="tool-1", completed=True)
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert next(iter(issue.state["participants"].values()))["pending"] == {}


def test_tool_complete_retains_unknown_process_with_handle() -> None:
    """Keep asynchronous work pending under its observed handle."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="tool-1", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    # Inspect the participant record after the operation.
    member = next(iter(issue.state["participants"].values()))
    assert member["pending"] == {"tool-1": {"status": "unknown", "handle": "pid-7"}}


def test_scope_assigns_reader_packet_and_owned_note() -> None:
    """Replace one participant scope while retaining coordinator ownership."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Resolve the exact participant key for this request.
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    # Build the reader-scoped packet used by this case.
    packet = [
        {
            "id": "issue",
            "locator": "context/issue.md",
            "sha256": core.sha(b"issue"),
            "required": True,
            "authority": "approved-assignment",
            "reason": "step-5",
            "stage": "execution",
            "reader": reader,
        }
    ]
    # Dispatch the scope request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request(
            "scope",
            expected_revision=7,
            target_participant=reader,
            packet=packet,
            owned_paths=["context/issue.md"],
        ),
    )  # type: ignore[arg-type]
    # Confirm the reader packet contains only assigned references.
    assert result["packet_digest"] == core.sha(core.canonical(packet))
    assert issue.state["assignments"][reader]["packet"] == packet
    assert issue.state["owners"]["context/issue.md"] == reader
    assert issue.state["owners"]["roadmap.md"] == issue.state["coordinator"]


def test_scope_updates_detached_reader_packet_without_reviving_generation() -> None:
    """A scope edit does not silently reattach a detached participant."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Resolve the exact participant key for this request.
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    # Seed a detached reader whose packet can change without reattachment.
    issue.state["participants"][reader] = {
        "generation": 4,
        "status": "detached",
        "pending": {},
        "packet": None,
        "ack": None,
    }
    packet = [
        {**issue.state["participants"][issue.state["coordinator"]]["packet"][0], "reader": reader}
    ]
    # Dispatch the scope request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request("scope", expected_revision=7, target_participant=reader, packet=packet),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["participants"][reader]["status"] == "detached"
    assert issue.state["participants"][reader]["packet"] == packet
    assert issue.state["participants"][reader]["ack"] is None


def test_join_grants_reader_only_roadmap_packet() -> None:
    """Attach a verified-ticket reader without transferring note or coordinator authority."""
    # Start from a coordinator-owned issue and request an unassigned reader.
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request(
        "join",
        session_id="reader",
        issue_uuid="uuid",
        expected_revision=7,
    )
    # Attach the reader through the public lifecycle operation.
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    reader = core.participant_key(request)
    # Verify the reader cannot take coordinator or note ownership.
    assert result["participant_id"] == reader
    assert issue.state["coordinator"] != reader
    assert reader not in issue.state["owners"].values()
    # Inspect the packet selected for the newly attached reader.
    packet = issue.state["participants"][reader]["packet"]
    # Verify the packet contains only the roadmap and the binding was saved.
    assert len(packet) == 1
    assert packet[0]["locator"] == "roadmap.md"
    assert packet[0]["reader"] == reader
    assert store.saved == [reader]


def test_scope_refuses_roadmap_transfer_without_commit() -> None:
    """Keep coordinator roadmap ownership outside scope grants."""
    # Create the modeled issue and identify its participant.
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    # Dispatch the scope request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "scope",
                expected_revision=7,
                target_participant=key,
                packet=next(iter(issue.state["participants"].values()))["packet"],
                owned_paths=["roadmap.md"],
            ),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports NOT_OWNER.
    assert captured.value.code == "NOT_OWNER"
    assert issue.commits == []


def test_event_rejects_unbounded_payload_without_commit() -> None:
    """Allow only the bounded event vocabulary and public code field."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the event request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store, issue, base_request("event", event_type="arbitrary", event={"code": "OK"})
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports INVALID_REQUEST.
    assert captured.value.code == "INVALID_REQUEST"
    # Dispatch the event request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("event", event_type="observation", event={"content": "secret"}),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports INVALID_REQUEST.
    assert captured.value.code == "INVALID_REQUEST"
    assert issue.commits == []
    # Dispatch the event request against the modeled issue.
    core.operate(
        store, issue, base_request("event", event_type="observation", event={"code": "UNKNOWN"})
    )  # type: ignore[arg-type]
    assert issue.commits[-1][0] == "observation"


def test_detach_requires_evidence_and_no_pending_work() -> None:
    """Refuse detachment while a tool is still unresolved."""
    # Create the modeled issue and assign the source packet.
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "handoff", "locator": "context/handoff.md", "sha256": core.sha(b"handoff")}
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    # Dispatch the detach request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("detach", evidence=[source]))  # type: ignore[arg-type]
    # Confirm the rejected operation reports PENDING_OPERATION.
    assert captured.value.code == "PENDING_OPERATION"
    assert next(iter(issue.state["participants"].values()))["status"] == "ready"
    # Dispatch the tool-complete request against the modeled issue.
    core.operate(store, issue, base_request("tool-complete", tool_id="tool-1", completed=True))  # type: ignore[arg-type]
    core.operate(store, issue, base_request("detach", evidence=[source]))  # type: ignore[arg-type]
    # Inspect the participant record after the operation.
    member = next(iter(issue.state["participants"].values()))
    # Confirm the participant status after the transition.
    assert (member["status"], member["ack"]) == ("detached", None)


def test_outcome_blocks_terminal_transition_with_pending_work() -> None:
    """Retain the active issue until admitted work settles."""
    # Create the modeled issue and assign the source packet.
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "decision", "locator": "context/decision.md", "sha256": core.sha(b"done")}
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    # Dispatch the outcome request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "outcome", expected_revision=7, disposition="cancelled", evidence=[source]
            ),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports PENDING_OPERATION.
    assert captured.value.code == "PENDING_OPERATION"
    assert issue.state["disposition"] == "active"


def test_outcome_requires_abandonment_for_failed_issue() -> None:
    """Require explicit abandonment evidence before failed terminal status."""
    # Create the modeled issue and assign the source packet.
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "decision", "locator": "context/decision.md", "sha256": core.sha(b"failed")}
    # Dispatch the outcome request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("outcome", expected_revision=7, disposition="failed", evidence=[source]),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports EVIDENCE_REQUIRED.
    assert captured.value.code == "EVIDENCE_REQUIRED"
    assert issue.state["disposition"] == "active"
    # Dispatch the outcome request against the modeled issue.
    core.operate(
        store,
        issue,
        base_request(
            "outcome", expected_revision=7, disposition="failed", evidence=[source], abandoned=True
        ),
    )  # type: ignore[arg-type]
    assert issue.state["disposition"] == "failed"
    assert issue.state["outcome"]["evidence"] == [source]


def test_poll_transport_requires_unique_parent_handle() -> None:
    """Reject a poll whose process handle was never observed."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    # Dispatch the tool-start request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("tool-start", tool_id="poll", poll_handle="pid-7"),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports UNKNOWN_OPERATION.
    assert captured.value.code == "UNKNOWN_OPERATION"
    assert next(iter(issue.state["participants"].values()))["pending"] == {
        "process": {"status": "pending"}
    }


def test_poll_transport_settles_without_losing_running_parent() -> None:
    """Remove a completed poll while retaining its still-running process."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    core.operate(store, issue, base_request("tool-start", tool_id="poll", poll_handle="pid-7"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request(
            "tool-complete", tool_id="poll", poll=True, async_handle="pid-7", completed=False
        ),
    )  # type: ignore[arg-type]
    # Inspect the participant record after the operation.
    member = next(iter(issue.state["participants"].values()))
    assert member["pending"] == {"process": {"status": "unknown", "handle": "pid-7"}}


def test_tool_complete_rejects_handle_change_without_mutating_pending() -> None:
    """Preserve the original handle when a later observation conflicts."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    # Capture state before the attempted operation.
    before = copy.deepcopy(issue.state)
    # Dispatch the tool-complete request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("tool-complete", tool_id="process", completed=True, async_handle="pid-8"),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports REQUEST_CONFLICT.
    assert captured.value.code == "REQUEST_CONFLICT"
    assert issue.state == before


def test_archive_prepare_freezes_current_payload_after_preparation_event() -> None:
    """Publish a reconstructable export only after the preparation commit."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the archive-prepare request against the modeled issue.
    result = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.commits[0][0] == "archive-prepare"
    assert issue.commits[1][0] is None
    assert issue.state["export"]["snapshot"] == result["snapshot"]
    assert issue.state["export"]["files"] == core.manifest(issue.payload)
    assert (
        issue.control.json("export-" + result["snapshot"] + ".json")["snapshot"]
        == result["snapshot"]
    )


def test_archive_prepare_refuses_pending_work_without_export() -> None:
    """Avoid freezing a snapshot while a recorded tool remains unresolved."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="tool-1"))  # type: ignore[arg-type]
    # Record the event count before the rejected transition.
    prior_count = len(issue.commits)
    # Dispatch the archive-prepare request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    # Confirm the rejected operation reports PENDING_OPERATION.
    assert captured.value.code == "PENDING_OPERATION"
    assert len(issue.commits) == prior_count
    assert "export" not in issue.state


def test_archive_index_accepts_only_matching_readback_part() -> None:
    """Build the index from the saved export's exact part content."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the archive-prepare request against the modeled issue.
    prepared = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    # Build a provider read-back for the exported archive part.
    part = prepared["parts"][0]
    observation = {
        "id": "part-1",
        "url": "https://linear.app/team/document/part-1",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:00:00Z",
        "content": part["content"],
        "origin": "linear_get_document",
        "request_id": "readback",
    }
    # Dispatch the archive-index request against the modeled issue.
    index = core.operate(store, issue, base_request("archive-index", observations=[observation]))  # type: ignore[arg-type]
    parsed = core.parse_document(index["content"])
    assert parsed["snapshot"] == prepared["snapshot"]
    assert parsed["parts"][0]["id"] == "part-1"
    assert issue.state["index_request"]["content"] == index["content"]
    assert issue.commits[-1][0] is None


def test_archive_index_rejects_changed_provider_part_without_commit() -> None:
    """Reject a provider read-back that differs from the frozen export."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the archive-prepare request against the modeled issue.
    core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    # Record the event count and provider observation before retry.
    before_count = len(issue.commits)
    observation = {
        "id": "part-1",
        "url": "https://linear.app/team/document/part-1",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:00:00Z",
        "content": core.document({"kind": "part", "data": "changed"}, "# Changed"),
        "origin": "linear_get_document",
        "request_id": "readback",
    }
    # Dispatch the archive-index request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-index", observations=[observation]))  # type: ignore[arg-type]
    # Confirm the rejected operation reports ARCHIVE_PENDING.
    assert captured.value.code == "ARCHIVE_PENDING"
    assert len(issue.commits) == before_count


def test_archive_save_start_records_uncertainty_before_external_write() -> None:
    """Prevent an uncertain provider save from being blindly repeated."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the archive-prepare request against the modeled issue.
    prepared = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    digest = core.sha(prepared["parts"][0]["content"].encode())
    core.operate(store, issue, base_request("archive-save-start", content_digest=digest))  # type: ignore[arg-type]
    # Confirm the snapshot digest matches exact archived bytes.
    assert issue.state["provider_saves"][digest] == {"status": "uncertain"}
    # Record the event count before the invalid archive read-back.
    count = len(issue.commits)
    # Dispatch the archive-save-start request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-save-start", content_digest=digest))  # type: ignore[arg-type]
    # Confirm the rejected operation reports ARCHIVE_PENDING.
    assert captured.value.code == "ARCHIVE_PENDING"
    assert "Uncertain save" in captured.value.action
    assert len(issue.commits) == count


def test_archive_observe_save_binds_document_id_to_content_digest() -> None:
    """Keep a read-back requirement tied to the immutable exported content."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the archive-prepare request against the modeled issue.
    prepared = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    digest = core.sha(prepared["parts"][0]["content"].encode())
    core.operate(store, issue, base_request("archive-save-start", content_digest=digest))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("archive-observe-save", content_digest=digest, document_id="part-1"),
    )  # type: ignore[arg-type]
    # Confirm the snapshot digest matches exact archived bytes.
    assert issue.state["provider_saves"][digest] == {
        "status": "readback-required",
        "id": "part-1",
    }
    # Dispatch the archive-observe-save request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("archive-observe-save", content_digest=digest, document_id="other"),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports ARCHIVE_PENDING.
    assert captured.value.code == "ARCHIVE_PENDING"


def readbacks(store: MemoryStore, issue: MemoryIssue) -> list[dict[str, object]]:
    """Build read-backs from the actual prepared part and index requests.

    Args:
        store: Store used to exercise lifecycle operations.
        issue: Issue model used by this scenario.

    Returns:
        Provider read-back observations for the archive parts.
    """
    # Dispatch the lifecycle request against the modeled issue.
    prepared = core.operate(store, issue, base_request("archive-prepare", expected_revision=7))  # type: ignore[arg-type]
    # Build the exact observation for the exported archive part.
    part = prepared["parts"][0]
    part_observation = {
        "id": "part-1",
        "url": "https://linear.app/team/document/part-1",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:00:00Z",
        "content": part["content"],
        "origin": "linear_get_document",
        "request_id": "readback",
    }
    # Dispatch the lifecycle request against the modeled issue.
    index = core.operate(
        store, issue, base_request("archive-index", observations=[part_observation])
    )  # type: ignore[arg-type]
    # Select a disposable root for the storage boundary.
    root_observation = {
        "id": "root",
        "url": "https://linear.app/team/document/root",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:01:00Z",
        "content": index["content"],
        "origin": "linear_get_document",
        "request_id": "readback",
    }
    return [part_observation, root_observation]


def test_archive_verify_records_receipt_only_after_matching_provider_bytes() -> None:
    """Bind the provider root and part versions to the exact local files."""
    # Create an issue and collect provider observations for archive verification.
    store, issue = MemoryStore(), MemoryIssue()
    observed = readbacks(store, issue)
    # Dispatch the archive-verify request against the modeled issue.
    result = core.operate(store, issue, base_request("archive-verify", observations=observed))  # type: ignore[arg-type]
    assert result["receipt"]["root"]["id"] == "root"
    assert issue.state["archive"] == result["receipt"]
    assert issue.commits[-1][0] is None


def test_archive_verify_rejects_changed_local_file_manifest() -> None:
    """Reject a valid provider snapshot when local bytes changed after export."""
    # Prepare payload bytes for archive validation.
    store, issue = MemoryStore(), MemoryIssue()
    observed = readbacks(store, issue)
    issue.payload["context/note.md"] = b"changed after export"
    before_count = len(issue.commits)
    # Dispatch the archive-verify request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-verify", observations=observed))  # type: ignore[arg-type]
    # Confirm the rejected operation reports ARCHIVE_PENDING.
    assert captured.value.code == "ARCHIVE_PENDING"
    assert len(issue.commits) == before_count


def test_cleanup_plan_issues_challenge_only_for_retained_terminal_archive() -> None:
    """Require completed work and verified export before a cleanup challenge."""
    # Create an issue and collect provider observations for archive verification.
    store, issue = MemoryStore(), MemoryIssue()
    observed = readbacks(store, issue)
    # Dispatch the archive-verify request against the modeled issue.
    core.operate(store, issue, base_request("archive-verify", observations=observed))  # type: ignore[arg-type]
    # Enter maintenance while the retained archive still blocks cleanup.
    key = issue.state["coordinator"]
    issue.state["participants"][key]["status"] = "maintenance"
    # Dispatch the cleanup-plan request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("cleanup-plan"))  # type: ignore[arg-type]
    # Confirm the rejected operation reports RETAINED.
    assert captured.value.code == "RETAINED"
    # Complete the issue to test the remaining export-readback gate.
    issue.state["disposition"] = "completed"
    issue.state["outcome"] = {"disposition": "completed"}
    # Dispatch the cleanup-plan request against the modeled issue.
    result = core.operate(store, issue, base_request("cleanup-plan"))  # type: ignore[arg-type]
    # Confirm the rejected operation reports READBACK_REQUIRED.
    assert result["code"] == "READBACK_REQUIRED"
    assert result["root"]["id"] == "root"
    assert result["cleanup_challenge"] == issue.state["cleanup_challenge"]


def test_reopen_terminal_issue_invalidates_acknowledgment() -> None:
    """Require a new packet acknowledgment after reopening terminal work."""
    # Begin with cancelled work and a coordinator in maintenance.
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    issue.state["disposition"] = "cancelled"
    issue.state["participants"][key]["status"] = "maintenance"
    source = {"id": "reopen", "locator": "context/reopen.md", "sha256": core.sha(b"reason")}
    # Dispatch the reopen request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request("reopen", expected_revision=7, evidence=[source]),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["disposition"] == "active"
    # Inspect the participant record after the operation.
    member = issue.state["participants"][key]
    # Confirm the participant status after the transition.
    assert (member["status"], member["ack"]) == ("attached", None)


def test_checkpoint_records_bounded_handoff_and_review_disposition() -> None:
    """Persist a complete handoff record before moving a submitted PR to review."""
    # Create the issue with a pinned source and completion checkpoint.
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "status", "locator": "context/status.md", "sha256": core.sha(b"status")}
    checkpoint = {
        "goal": "Finish step five",
        "constraints": [],
        "sources": [source],
        "progress": "unit coverage",
        "blockers": [],
        "handoff": "run platform CI",
        "candidate": "https://github.com/example/repo/pull/1",
    }
    # Dispatch the checkpoint request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request(
            "checkpoint",
            expected_revision=7,
            checkpoint=checkpoint,
            submitted_pr="https://github.com/example/repo/pull/1",
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["checkpoint"] == checkpoint
    assert issue.state["checkpoint_history"] == [checkpoint]
    assert issue.state["disposition"] == "in_review"
    assert issue.commits[-1][0] == "checkpoint"


def test_checkpoint_without_pr_records_progress_without_review_transition() -> None:
    """A complete checkpoint alone does not claim a submitted pull request."""
    # Create the issue with a pinned source and completion checkpoint.
    store, issue = MemoryStore(), MemoryIssue()
    source = {"id": "status", "locator": "context/status.md", "sha256": core.sha(b"status")}
    checkpoint = {
        "goal": "Finish step five",
        "constraints": [],
        "sources": [source],
        "progress": "unit cases in progress",
        "blockers": [],
        "handoff": "pending review",
        "candidate": "",
    }
    # Dispatch the checkpoint request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request("checkpoint", expected_revision=7, checkpoint=checkpoint),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["checkpoint"] == checkpoint
    assert issue.state["disposition"] == "active"
    assert "submitted_pr" not in issue.state


def test_checkpoint_rejects_incomplete_handoff_before_commit() -> None:
    """Reject missing handoff fields without mutating the recorded issue."""
    # Create the modeled issue and retain its pre-operation state.
    store, issue = MemoryStore(), MemoryIssue()
    before = copy.deepcopy(issue.state)
    # Dispatch the checkpoint request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request("checkpoint", expected_revision=7, checkpoint={"goal": "missing rest"}),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports EVIDENCE_REQUIRED.
    assert captured.value.code == "EVIDENCE_REQUIRED"
    assert issue.state == before
    assert issue.commits == []


def test_nonterminal_outcome_records_evidence_without_completion_obligations() -> None:
    """Blocked progress can be recorded while work remains pending."""
    # Put pending work on a participant before cleanup eligibility is checked.
    store, issue = MemoryStore(), MemoryIssue()
    member = issue.state["participants"][issue.state["coordinator"]]
    member["pending"]["tool-1"] = {"status": "pending"}
    source = {"id": "status", "locator": "context/status.md", "sha256": core.sha(b"status")}
    # Dispatch the outcome request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request("outcome", expected_revision=7, disposition="blocked", evidence=[source]),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["disposition"] == "blocked"
    assert issue.state["outcome"]["evidence"] == [source]
    assert issue.state["participants"][issue.state["coordinator"]]["pending"] == {
        "tool-1": {"status": "pending"}
    }


def test_repeated_request_id_requires_identical_body_and_replays_saved_result() -> None:
    """Return a prior transaction result exactly once and reject changed retry bodies."""
    # Create a modeled store and issue, then build the lifecycle request.
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request("tool-start", tool_id="tool-1")
    # Dispatch the tool-start request against the modeled issue.
    initial = core.operate(store, issue, request)  # type: ignore[arg-type]
    # Cache the original request digest and result for idempotent replay.
    issue.state["requests"][core.sha(request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(request)),
        "result": initial,
    }
    count = len(issue.commits)
    # Check the operation response and committed issue state.
    assert core.operate(store, issue, request) == initial  # type: ignore[arg-type]
    assert len(issue.commits) == count
    # Dispatch the lifecycle request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, {**request, "tool_id": "tool-2"})  # type: ignore[arg-type]
    # Confirm the rejected operation reports REQUEST_CONFLICT.
    assert captured.value.code == "REQUEST_CONFLICT"
    assert len(issue.commits) == count


def test_existing_create_reuses_explicit_coordinator_binding() -> None:
    """Attach a coordinator retry through the current issue state and persist its binding."""
    # Create the modeled issue and build a request for its participant.
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    request = base_request("create", issue_uuid="uuid", coordinator=key)
    # Dispatch the create request against the modeled issue.
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    # Confirm the result remains bound to the requested issue.
    assert result["ok"] is True
    assert store.saved == [key]
    assert store.views[-1] == "TEST-30"
    assert issue.commits[-1][0] == "attach"


def test_create_initializes_packaged_roadmap_only_for_absent_issue() -> None:
    """Publish an initial roadmap and empty history under an explicit coordinator."""
    # Remove registered state and payload to model initial creation.
    store, issue = MemoryStore(), MemoryIssue()
    issue.state = None  # type: ignore[assignment]
    issue.payload = {}

    class Task:
        """Report the absence of a prior task payload."""

        def exists(self, identifier: str) -> bool:
            """Require the selected issue lookup and report it absent.

            Args:
                identifier: Issue or participant identifier used in this scenario.

            Returns:
                Whether the requested test entry exists.
            """
            # Confirm the result remains bound to the requested issue.
            assert identifier == "TEST-30"
            return False

    # Attach the modeled canonical issue directory to the store.
    store.task = Task()
    # Resolve the exact participant key for this request.
    key = core.participant_key(base_request("create"))
    # Build the request with the selected issue binding.
    request = base_request("create", issue_uuid="uuid", coordinator=key)
    # Dispatch the create request against the modeled issue.
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["binding_generation"] == 1
    assert issue.payload["events.jsonl"] == b""
    assert b"TEST-30" in issue.payload["roadmap.md"]
    assert issue.state["coordinator"] == key
    assert issue.commits[-1][0] == "create"
    assert store.saved == [key]


def test_absent_issue_requires_create_or_explicit_adopt_before_any_commit() -> None:
    """Do not manufacture a task workspace for an absent read or mismatched adopt."""
    # Leave the issue unregistered while an unadopted payload exists.
    store, issue = MemoryStore(), MemoryIssue()
    issue.state = None  # type: ignore[assignment]

    class Task:
        """Report an existing unadopted payload."""

        def exists(self, _identifier: str) -> bool:
            """Model one present prior directory.

            Args:
                _identifier: Unused identifier argument accepted by this fake.

            Returns:
                Whether the requested test entry exists.
            """
            return True

    # Attach the modeled canonical issue directory to the store.
    store.task = Task()
    # Dispatch the read request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("read"))  # type: ignore[arg-type]
    # Confirm the rejected operation reports BINDING_MISSING.
    assert captured.value.code == "BINDING_MISSING"
    # Resolve the exact participant key for this request.
    key = core.participant_key(base_request("create"))
    # Dispatch the create request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("create", issue_uuid="uuid", coordinator=key))  # type: ignore[arg-type]
    # Confirm the rejected operation reports ADOPTION_REQUIRED.
    assert captured.value.code == "ADOPTION_REQUIRED"
    assert issue.commits == []


def test_restore_reconstructs_only_tombstone_matching_provider_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore exact archived files, advance generations, and bind the coordinator.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Model completed, cleaned work with a snapshot tombstone for restoration.
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    issue.state["storage"] = "cleaned"
    issue.state["disposition"] = "completed"
    issue.state["tombstone"] = {"snapshot": "snap-1", "archive": {"root": {"id": "root"}}}
    issue.state["participants"][key]["status"] = "maintenance"
    # Hash the exact issue payload before the transition.
    issue.state["files"] = core.manifest(issue.payload)

    class Task:
        """Report an absent payload directory before reconstructed publication."""

        def exists(self, _identifier: str) -> bool:
            """Require no existing issue payload.

            Args:
                _identifier: Unused identifier argument accepted by this fake.

            Returns:
                Whether the requested test entry exists.
            """
            return False

    # Install fake verify_provider calls.
    store.task = Task()
    archived = issue.payload.copy()
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: (
            {"provenance": {"roadmap.md": {"author": key}}},
            archived,
            {"snapshot": "snap-1"},
        ),
    )
    # Dispatch the restore request against the modeled issue.
    result = core.operate(store, issue, base_request("restore", observations=[{"id": "root"}]))  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["binding_generation"] == 3
    assert issue.state["storage"] == "present"
    assert issue.state["generation"] == 2
    assert issue.state["participants"][key]["status"] == "attached"
    assert issue.state["participants"][key]["ack"] is None
    assert issue.state["history"][0]["snapshot"] == "snap-1"
    assert issue.payload == archived
    assert issue.commits[-1][0] == "restore"
    assert store.saved == [key]


def test_restore_rejects_changed_archive_manifest_without_creating_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave a cleaned tombstone intact when provider bytes differ from its files.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake verify_provider calls.
    store, issue = MemoryStore(), MemoryIssue()
    issue.state["storage"] = "cleaned"
    issue.state["tombstone"] = {"snapshot": "snap-1"}
    before = copy.deepcopy(issue.state)
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: ({}, {"roadmap.md": b"changed"}, {"snapshot": "snap-1"}),
    )
    # Dispatch the restore request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("restore", observations=[]))  # type: ignore[arg-type]
    # Confirm the rejected operation reports INTEGRITY_ERROR.
    assert captured.value.code == "INTEGRITY_ERROR"
    assert issue.state == before
    assert issue.commits == []


# A drive-less root is relative on Windows.
SCOPE_ROOT = "C:/abs/worktree" if os.name == "nt" else "/abs/worktree"
SCOPED_ROADMAP = (
    "**Proposal status:** Approved by the user.\n"
    "**Proposal revision:** r2\n"
    "**Approval record:** Human approved revision r2.\n"
    f"**Repository scope:**\n- `{SCOPE_ROOT}`: `./`\n"
)


def test_ready_reports_recorded_approval_and_scope() -> None:
    """Ready returns the roadmap's recorded approval and syntax-checked scope."""
    issue = MemoryIssue()
    issue.payload["roadmap.md"] = SCOPED_ROADMAP.encode()
    member = next(iter(issue.state["participants"].values()))
    member["packet"][0]["sha256"] = core.sha(issue.payload["roadmap.md"])
    member["ack"] = core.sha(core.canonical(member["packet"]))
    result = core.operate(MemoryStore(), issue, base_request("ready"))  # type: ignore[arg-type]
    assert result["approval"] == {
        "approved": True,
        "scope": [{"root": SCOPE_ROOT, "paths": ["./"]}],
    }


PENDING_ROADMAP = SCOPED_ROADMAP.replace("Human approved revision r2.", "Pending approval.")


def test_reset_readiness_keeps_detached_participants_detached() -> None:
    """Clear every acknowledgment without reviving a detached participant."""
    state = MemoryIssue().state
    live = next(iter(state["participants"].values()))
    state["participants"]["gone"] = {**live, "status": "detached"}
    core.reset_readiness(state)
    assert [(m["status"], m["ack"]) for m in state["participants"].values()] == [
        ("attached", None),
        ("detached", None),
    ]


def test_scope_change_clears_readiness_for_a_packet_without_the_roadmap() -> None:
    """A changed scope needs a fresh acknowledgment even when no packet names the roadmap."""
    store, issue = MemoryStore(), MemoryIssue()
    member = next(iter(issue.state["participants"].values()))
    member["packet"] = [
        {"id": "note", "locator": "context/note.md", "sha256": core.sha(b"old note")}
    ]
    request = base_request(
        "update",
        expected_revision=7,
        path="roadmap.md",
        old_digest=core.sha(b"approved roadmap"),
        content=PENDING_ROADMAP,
        provenance={
            "sources": [member["packet"][0]],
            "applicability": "TEST-30",
            "status": "draft",
        },
    )
    assert core.operate(store, issue, request)["ok"] is True  # type: ignore[arg-type]
    member = next(iter(issue.state["participants"].values()))
    assert (member["status"], member["ack"]) == ("attached", None)


@pytest.mark.parametrize(
    ("path", "content", "status"),
    [
        ("context/note.md", b"edited markdown", "ready"),
        ("roadmap.md", b"edited roadmap without scope", "ready"),
        ("roadmap.md", PENDING_ROADMAP.encode(), "attached"),
    ],
)
def test_reconcile_imports_only_inspected_markdown_with_unchanged_event_history(
    monkeypatch: pytest.MonkeyPatch, path: str, content: bytes, status: str
) -> None:
    """Commit explicit Markdown reconciliation after checking directory identity and events.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        path: Edited Markdown payload path.
        content: Edited bytes; a roadmap scope arrives only as a pending proposal.
        status: Participant status afterwards; only a changed scope clears readiness.
    """
    # Bind the existing directory identity, the retained template record and edited bytes.
    store, issue = MemoryStore(), MemoryIssue()
    issue.state["directory_identity"] = [4, 5]
    issue.state["roadmap"] = core.roadmap_record("")
    observed = {**issue.payload, path: content}

    class Payload:
        """Expose an exact opened payload-directory identity."""

        identity = [4, 5]

        def __enter__(self) -> Payload:
            """Hold the validated payload handle.

            Returns:
                This fake context manager for the enclosed operation.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the validated payload handle.

            Args:
                *_args: Exception details supplied by the context-manager protocol.
            """

    class Task:
        """Open the selected issue payload directly."""

        def child(self, identifier: str, *, private: bool) -> Payload:
            """Require the issue-specific non-private payload.

            Args:
                identifier: Issue or participant identifier used in this scenario.
                private: Whether the resource should be private to this issue.

            Returns:
                Fake child directory or issue context for the caller.
            """
            # Confirm the result remains bound to the requested issue.
            assert (identifier, private) == ("TEST-30", False)
            return Payload()

    # Install fake inventory calls.
    store.task = Task()
    monkeypatch.setattr(core, "inventory", lambda _payload: observed)
    source = {"id": "inspect", "locator": "context/inspection.md", "sha256": core.sha(b"ok")}
    previous = issue.state["files"].copy()
    # Dispatch the reconcile-files request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request(
            "reconcile-files",
            expected_revision=7,
            evidence=[source],
            inventory=core.manifest(observed),
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.payload[path] == content
    assert issue.state["reconciliation"] == {"previous": previous, "evidence": [source]}
    assert issue.commits[-1][0] == "reconciliation"
    # An imported roadmap becomes the record later reconciliations compare against.
    if path == "roadmap.md":
        assert issue.state["roadmap"] == core.roadmap_record(content.decode())
    assert next(iter(issue.state["participants"].values()))["status"] == status


@pytest.mark.parametrize(
    ("path", "content", "record", "code"),
    [
        ("events.jsonl", b"uncommitted event\n", None, "INTEGRITY_ERROR"),
        ("roadmap.md", SCOPED_ROADMAP.encode(), core.roadmap_record(""), "INVALID_REQUEST"),
        # Without a retained record a reused revision cannot be detected.
        ("roadmap.md", PENDING_ROADMAP.encode(), None, "INVALID_REQUEST"),
        # A broader scope may not reuse the committed revision, even while pending.
        (
            "roadmap.md",
            PENDING_ROADMAP.encode(),
            {"revision": "r2", "scope": [{"root": SCOPE_ROOT, "paths": ["src/"]}]},
            "INVALID_REQUEST",
        ),
    ],
)
def test_reconcile_rejects_changed_event_bytes_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    content: bytes,
    record: core.RoadmapRecord | None,
    code: str,
) -> None:
    """Forbid importing event history or an unproposed scope grant through reconciliation.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        path: Changed payload path.
        content: Changed bytes.
        record: Retained record of the committed roadmap, if any.
        code: Expected rejection code.
    """
    # Bind the existing directory identity, any retained record and the rejected bytes.
    store, issue = MemoryStore(), MemoryIssue()
    issue.state["directory_identity"] = [4, 5]
    if record is not None:
        issue.state["roadmap"] = record
    observed = {**issue.payload, path: content}

    class Payload:
        """Expose an identity-matching payload directory."""

        identity = [4, 5]

        def __enter__(self) -> Payload:
            """Hold the modeled payload handle.

            Returns:
                This fake context manager for the enclosed operation.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled payload handle.

            Args:
                *_args: Exception details supplied by the context-manager protocol.
            """

    class Task:
        """Open the selected issue payload for inspection."""

        def child(self, _identifier: str, *, private: bool) -> Payload:
            """Require non-private payload inspection.

            Args:
                _identifier: Unused identifier argument accepted by this fake.
                private: Whether the resource should be private to this issue.

            Returns:
                Fake child directory or issue context for the caller.
            """
            assert private is False
            return Payload()

    # Install fake inventory calls.
    store.task = Task()
    monkeypatch.setattr(core, "inventory", lambda _payload: observed)
    source = {"id": "inspect", "locator": "context/inspection.md", "sha256": core.sha(b"ok")}
    # Dispatch the reconcile-files request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "reconcile-files",
                expected_revision=7,
                evidence=[source],
                inventory=core.manifest(observed),
            ),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports its code before any commit.
    assert captured.value.code == code
    assert issue.commits == []


def test_cleanup_commit_records_intent_only_after_fresh_matching_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit a cleanup intent after matching archive versions and challenge replies.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake verify_provider calls.
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    issue.state["participants"][key]["status"] = "maintenance"
    issue.state["disposition"] = "completed"
    issue.state["outcome"] = {"disposition": "completed"}
    issue.state["directory_identity"] = [4, 5]
    issue.state["export"] = {
        "snapshot": "snap-1",
        "files": core.manifest(issue.payload),
        "revision": 7,
    }
    issue.state["archive"] = {"root": {"id": "root", "updatedAt": "old"}}
    issue.state["cleanup_challenge"] = "challenge-1"
    receipt = {"snapshot": "snap-1", "root": {"id": "root", "updatedAt": "old"}}
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: ({"revision": 7}, issue.payload.copy(), receipt),
    )
    finish_calls: list[bool] = []

    def finish(current: MemoryIssue) -> None:
        """Record only a durable cleanup intent observed after provider checks.

        Args:
            current: Current binding returned by the fake store.
        """
        finish_calls.append("cleanup.json" in current.control.data)

    # Install fake finish_cleanup calls.
    monkeypatch.setattr(core, "finish_cleanup", finish)
    request = base_request(
        "cleanup-commit",
        observations=[{"request_id": "challenge-1"}],
        cleanup_challenge="challenge-1",
    )
    # Dispatch the cleanup-commit request against the modeled issue.
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    # Confirm the rejected operation reports CLEANED.
    assert result["code"] == "CLEANED"
    # Build a durable cleanup intent from the current manifest.
    intent = issue.control.data["cleanup.json"]
    assert intent["directory_identity"] == [4, 5]
    assert intent["files"] == core.manifest(issue.payload)
    assert intent["request_id"] == "request-1"
    assert finish_calls == [False, True]
    assert issue.commits == []


def test_cleanup_commit_rejects_stale_challenge_without_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never quarantine files from an old provider read-back challenge.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake verify_provider calls.
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    issue.state["participants"][key]["status"] = "maintenance"
    issue.state["disposition"] = "completed"
    issue.state["outcome"] = {"disposition": "completed"}
    issue.state["export"] = {
        "snapshot": "snap-1",
        "files": core.manifest(issue.payload),
        "revision": 7,
    }
    issue.state["archive"] = {"root": {"id": "root", "updatedAt": "old"}}
    issue.state["cleanup_challenge"] = "challenge-1"
    receipt = {"snapshot": "snap-1", "root": {"id": "root", "updatedAt": "old"}}
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: ({"revision": 7}, issue.payload.copy(), receipt),
    )
    # Dispatch the cleanup-commit request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "cleanup-commit",
                observations=[{"request_id": "old-challenge"}],
                cleanup_challenge="challenge-1",
            ),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports ARCHIVE_PENDING.
    assert captured.value.code == "ARCHIVE_PENDING"
    assert issue.control.data == {}
    assert issue.commits == []


def test_completed_outcome_requires_verified_local_evidence_and_provider_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Set completed only after local evidence bytes and typed provider state match.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Record opened handles to check no extra directory access.
    store, issue = MemoryStore(), MemoryIssue()
    source = {
        "id": "acceptance",
        "locator": str(tmp_path / "evidence/acceptance.md"),
        "sha256": core.sha(b"accepted"),
    }
    opened: list[str] = []

    class Parent:
        """Expose one exact owned evidence file."""

        def __enter__(self) -> Parent:
            """Hold the modeled no-follow parent handle.

            Returns:
                This fake context manager for the enclosed operation.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled parent handle.

            Args:
                *_args: Exception details supplied by the context-manager protocol.
            """

        def read(self, name: str, limit: int) -> bytes:
            """Return exact evidence bytes after name and size inspection.

            Args:
                name: Requested file, directory, or control-entry name.
                limit: Maximum size or count allowed by the operation.

            Returns:
                Bytes returned by the fake file or provider read.
            """
            assert (name, limit) == ("acceptance.md", core.MAX_FILE)
            return b"accepted"

    # Install fake absolute calls.
    monkeypatch.setattr(
        core.Directory,
        "absolute",
        lambda path: opened.append(str(path)) or Parent(),
    )
    provider = {
        "origin": "linear_get_issue",
        "issue_uuid": "uuid",
        "status_type": "completed",
        "completed_at": "2026-10-08T00:00:00Z",
        "request_id": "provider-read-1",
    }
    # Dispatch the outcome request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request(
            "outcome",
            expected_revision=7,
            disposition="completed",
            evidence=[source],
            completion={
                "human_acceptance": [source],
                "merge": [source],
                "obligations": [source],
            },
            provider_status=provider,
        ),
    )  # type: ignore[arg-type]
    # Check recorded opened against the required side effects.
    assert result["ok"] is True
    assert opened == [str(tmp_path / "evidence")] * 3
    assert issue.state["disposition"] == "completed"
    assert issue.state["provider_completion"] == provider
    assert issue.commits[-1][0] == "outcome"


def test_completed_outcome_rejects_provider_status_without_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not treat unverified provider status as issue completion.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Create the modeled issue and assign the source packet.
    store, issue = MemoryStore(), MemoryIssue()
    source = {
        "id": "acceptance",
        "locator": str(tmp_path / "evidence/acceptance.md"),
        "sha256": core.sha(b"accepted"),
    }

    class Parent:
        """Expose exact local evidence bytes."""

        def __enter__(self) -> Parent:
            """Hold the modeled source handle.

            Returns:
                This fake context manager for the enclosed operation.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled source handle.

            Args:
                *_args: Exception details supplied by the context-manager protocol.
            """

        def read(self, _name: str, _limit: int) -> bytes:
            """Return the expected local evidence bytes.

            Args:
                _name: Unused name argument accepted by this fake.
                _limit: Unused limit argument accepted by this fake.

            Returns:
                Bytes returned by the fake file or provider read.
            """
            return b"accepted"

    # Install fake absolute calls.
    monkeypatch.setattr(core.Directory, "absolute", lambda _path: Parent())
    # Dispatch the outcome request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "outcome",
                expected_revision=7,
                disposition="completed",
                evidence=[source],
                completion={
                    "human_acceptance": [source],
                    "merge": [source],
                    "obligations": [source],
                },
                provider_status={
                    "origin": "linear_get_issue",
                    "issue_uuid": "uuid",
                    "status_type": "started",
                    "completed_at": "2026-10-08T00:00:00Z",
                    "request_id": "provider-read-1",
                },
            ),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports EVIDENCE_REQUIRED.
    assert captured.value.code == "EVIDENCE_REQUIRED"
    assert issue.state["disposition"] == "active"
    assert issue.commits == []


def test_event_rollover_preserves_checkpointed_history_as_immutable_segment() -> None:
    """Move only archive-verified active event bytes into a named retained segment."""
    # Start an event log to check the operation sequence.
    store, issue = MemoryStore(), MemoryIssue()
    old_events = core.canonical({"seq": 1, "type": "event"}) + b"\n"
    issue.payload["events.jsonl"] = old_events
    # Hash the exact issue payload before the transition.
    issue.state["files"] = core.manifest(issue.payload)
    # Seed an active event head and verified archive for rollover.
    issue.state["seq"] = 1
    issue.state["head"] = "head-1"
    issue.state["archive"] = {"snapshot": "snap-1"}
    issue.state["export"] = {
        "snapshot": "snap-1",
        "files": core.manifest(issue.payload),
        "revision": 7,
    }
    # Dispatch the event-rollover request against the modeled issue.
    result = core.operate(store, issue, base_request("event-rollover", expected_revision=7))  # type: ignore[arg-type]
    # Build an event segment with a retained digest anchor.
    segment = "events-000000000001-000000000001.jsonl"
    assert result["segment"] == segment
    assert result["snapshot"] == "snap-1"
    assert issue.payload[segment] == old_events
    assert issue.state["event_segments"][0] == {
        "path": segment,
        "first": 1,
        "last": 1,
        "head": "head-1",
        "sha256": core.sha(old_events),
        "archive": {"snapshot": "snap-1"},
    }
    assert issue.commits[-1][0] == "event-rollover"


def test_event_rollover_rejects_unverified_export_without_segment() -> None:
    """Retain the active event stream when no exact verified archive covers it."""
    # Start an event log to check the operation sequence.
    store, issue = MemoryStore(), MemoryIssue()
    issue.payload["events.jsonl"] = core.canonical({"seq": 1}) + b"\n"
    before = issue.payload.copy()
    # Dispatch the event-rollover request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("event-rollover", expected_revision=7))  # type: ignore[arg-type]
    # Confirm the rejected operation reports ARCHIVE_PENDING.
    assert captured.value.code == "ARCHIVE_PENDING"
    assert issue.payload == before
    assert issue.commits == []


def test_adopt_requires_exact_existing_payload_inventory_and_owners(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Adopt previously inspected bytes without replacing the existing roadmap.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Retain prior payload bytes while removing the registered issue state.
    store, issue = MemoryStore(), MemoryIssue()
    files = issue.payload.copy()
    issue.state = None  # type: ignore[assignment]
    # Resolve the exact participant key for this request.
    key = core.participant_key(base_request("adopt"))

    class Payload:
        """Expose the selected existing issue payload as one direct directory."""

        def __enter__(self) -> Payload:
            """Hold the modeled payload directory.

            Returns:
                This fake context manager for the enclosed operation.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled payload directory.

            Args:
                *_args: Exception details supplied by the context-manager protocol.
            """

    class Task:
        """Report an existing unregistered payload for explicit adoption."""

        def exists(self, identifier: str) -> bool:
            """Require the selected issue and report its directory present.

            Args:
                identifier: Issue or participant identifier used in this scenario.

            Returns:
                Whether the requested test entry exists.
            """
            # Confirm the result remains bound to the requested issue.
            assert identifier == "TEST-30"
            return True

        def child(self, identifier: str, *, private: bool) -> Payload:
            """Open only the selected non-private issue payload.

            Args:
                identifier: Issue or participant identifier used in this scenario.
                private: Whether the resource should be private to this issue.

            Returns:
                Fake child directory or issue context for the caller.
            """
            # Confirm the result remains bound to the requested issue.
            assert (identifier, private) == ("TEST-30", False)
            return Payload()

    # Install fake inventory calls.
    store.task = Task()
    monkeypatch.setattr(core, "inventory", lambda _payload: files)
    source = {
        "id": "inspection",
        "locator": str(tmp_path / "evidence/inspection.md"),
        "sha256": core.sha(b"ok"),
    }
    request = base_request(
        "adopt",
        issue_uuid="uuid",
        coordinator=key,
        evidence=[source],
        inventory=core.manifest(files),
        owners={"roadmap.md": key, "context/note.md": key},
    )
    # Dispatch the adopt request against the modeled issue.
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.payload == files
    assert issue.state["adoption"] == {"inventory": core.manifest(files), "evidence": [source]}
    assert issue.state["owners"] == {"roadmap.md": key, "context/note.md": key}
    assert issue.commits[-1][0] == "adopt"


def test_adopt_rejects_changed_existing_payload_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Leave prior files unregistered when the supplied inspection digest is stale.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Retain prior payload bytes for adoption with a stale inspection digest.
    store, issue = MemoryStore(), MemoryIssue()
    files = issue.payload.copy()
    issue.state = None  # type: ignore[assignment]
    # Resolve the exact participant key for this request.
    key = core.participant_key(base_request("adopt"))

    class Payload:
        """Expose a selected existing payload directory."""

        def __enter__(self) -> Payload:
            """Hold the modeled directory.

            Returns:
                This fake context manager for the enclosed operation.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled directory.

            Args:
                *_args: Exception details supplied by the context-manager protocol.
            """

    class Task:
        """Report an existing issue payload."""

        def exists(self, _identifier: str) -> bool:
            """Report the existing directory.

            Args:
                _identifier: Unused identifier argument accepted by this fake.

            Returns:
                Whether the requested test entry exists.
            """
            return True

        def child(self, _identifier: str, *, private: bool) -> Payload:
            """Open only a non-private existing payload.

            Args:
                _identifier: Unused identifier argument accepted by this fake.
                private: Whether the resource should be private to this issue.

            Returns:
                Fake child directory or issue context for the caller.
            """
            assert private is False
            return Payload()

    # Install fake inventory calls.
    store.task = Task()
    monkeypatch.setattr(core, "inventory", lambda _payload: files)
    source = {
        "id": "inspection",
        "locator": str(tmp_path / "evidence/inspection.md"),
        "sha256": core.sha(b"ok"),
    }
    # Dispatch the adopt request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store,
            issue,
            base_request(
                "adopt",
                issue_uuid="uuid",
                coordinator=key,
                evidence=[source],
                inventory={"roadmap.md": "stale"},
                owners={"roadmap.md": key, "context/note.md": key},
            ),
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports UNTRACKED_CHANGE.
    assert captured.value.code == "UNTRACKED_CHANGE"
    assert issue.commits == []


def test_join_existing_reader_refreshes_only_roadmap_digest() -> None:
    """Refresh a preassigned reader packet without granting ownership or coordinator scope."""
    # Create a modeled store and issue, then build the lifecycle request.
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request("join", session_id="reader", issue_uuid="uuid", expected_revision=7)
    # Resolve the exact participant key for this request.
    reader = core.participant_key(request)
    # Give the existing reader a packet with an outdated roadmap digest.
    previous = {
        "id": "roadmap",
        "locator": "roadmap.md",
        "sha256": core.sha(b"older roadmap"),
        "required": True,
        "authority": "task-workspace",
        "reason": "issue-resume",
        "stage": "planning",
        "reader": reader,
    }
    issue.state["participants"][reader] = {
        "generation": 2,
        "status": "ready",
        "pending": {},
        "packet": [previous],
        "ack": core.sha(core.canonical([previous])),
    }
    issue.state["assignments"] = {reader: {"packet": [previous]}}
    # Dispatch the join request against the modeled issue.
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    # Copy committed state for a controlled mutation.
    current = issue.state["participants"][reader]
    # Confirm the attached reader identity matches the packet scope.
    assert result["participant_id"] == reader
    assert current["packet"][0]["sha256"] == issue.state["files"]["roadmap.md"]
    assert current["ack"] is None
    assert reader not in issue.state["owners"].values()
    assert issue.state["coordinator"] != reader
    assert issue.commits[-1][0] == "attach"


def test_transfer_coordinator_moves_roadmap_ownership_with_evidence() -> None:
    """Move the coordinator role and canonical roadmap owner in one transaction."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Resolve the exact participant key for this request.
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    # Attach the target reader before transferring coordinator ownership.
    issue.state["participants"][reader] = {
        "generation": 1,
        "status": "attached",
        "pending": {},
        "packet": None,
        "ack": None,
    }
    source = {"id": "transfer", "locator": "context/transfer.md", "sha256": core.sha(b"vote")}
    # Dispatch the transfer-coordinator request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request(
            "transfer-coordinator",
            expected_revision=7,
            target_participant=reader,
            evidence=[source],
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["coordinator"] == reader
    assert issue.state["owners"]["roadmap.md"] == reader
    assert issue.commits[-1][0] == "transfer-coordinator"


def test_reconcile_participant_detaches_selected_generation_after_pending_clear() -> None:
    """Retire exactly the targeted stale participant generation with evidence."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Resolve the exact participant key for this request.
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    # Seed a ready reader generation for explicit reconciliation.
    issue.state["participants"][reader] = {
        "generation": 3,
        "status": "ready",
        "pending": {},
        "packet": None,
        "ack": "old-digest",
    }
    source = {"id": "reconcile", "locator": "context/reconcile.md", "sha256": core.sha(b"clear")}
    # Dispatch the reconcile-participant request against the modeled issue.
    result = core.operate(
        store,
        issue,
        base_request(
            "reconcile-participant",
            expected_revision=7,
            target_participant=reader,
            target_generation=3,
            evidence=[source],
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["participants"][reader]["status"] == "detached"
    assert issue.state["participants"][reader]["ack"] is None
    assert issue.commits[-1][0] == "reconcile-participant"


def test_archive_prepare_seal_requires_terminal_issue_and_detached_readers() -> None:
    """Freeze a terminal issue only after every other participant has detached."""
    # Create the modeled issue and identify its participant.
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    # Dispatch the archive-prepare request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("archive-prepare", expected_revision=7, seal=True))  # type: ignore[arg-type]
    # Confirm the rejected operation reports RETAINED.
    assert captured.value.code == "RETAINED"
    assert issue.commits == []
    # Resolve the exact participant key for this request.
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    # Detach the final reader and complete work before sealing the archive.
    issue.state["participants"][reader] = {
        "generation": 1,
        "status": "detached",
        "pending": {},
        "packet": None,
        "ack": None,
    }
    issue.state["disposition"] = "completed"
    # Dispatch the archive-prepare request against the modeled issue.
    result = core.operate(
        store, issue, base_request("archive-prepare", expected_revision=7, seal=True)
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert issue.state["participants"][key]["status"] == "maintenance"
    assert issue.state["participants"][reader]["status"] == "detached"
    assert issue.commits[0][0] == "archive-prepare"


def test_replayed_create_restores_saved_result_and_binding_without_new_commit() -> None:
    """Replay an identical creation receipt and its referenced response payload."""
    # Create the modeled issue and build a request for its participant.
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    request = base_request("create", issue_uuid="uuid", coordinator=key)
    # Persist the control document before resuming cleanup.
    issue.control.put("result.json", {"binding_generation": 2, "participant_id": key})
    # Seed a durable create receipt that references the stored response.
    issue.state["requests"][core.sha(request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(request)),
        "result": {"ok": True, "code": "OK", "result_ref": "result.json"},
    }
    # Dispatch the create request against the modeled issue.
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    # Confirm the cleanup result belongs to the requested issue.
    assert result == {
        "ok": True,
        "code": "OK",
        "binding_generation": 2,
        "participant_id": key,
    }
    assert store.saved == [key]
    assert store.views == ["TEST-30"]
    assert issue.commits == []


def test_restore_commit_failure_keeps_prior_cleaned_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore the prior in-memory tombstone if reconstruction publication fails.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Seed a cleaned snapshot tombstone before failed reconstruction.
    store, issue = MemoryStore(), MemoryIssue()
    issue.state["storage"] = "cleaned"
    issue.state["tombstone"] = {"snapshot": "snap-1"}
    # Hash the exact issue payload before the transition.
    issue.state["files"] = core.manifest(issue.payload)
    # Retain the prior event digest before appending a new event.
    prior = copy.deepcopy(issue.state)

    class Task:
        """Report no duplicate issue payload."""

        def exists(self, _identifier: str) -> bool:
            """Keep the payload absent before restore.

            Args:
                _identifier: Unused identifier argument accepted by this fake.

            Returns:
                Whether the requested test entry exists.
            """
            return False

    # Install fake verify_provider calls.
    store.task = Task()
    monkeypatch.setattr(
        core,
        "verify_provider",
        lambda _state, _observations: ({}, issue.payload.copy(), {"snapshot": "snap-1"}),
    )

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        """Model failed durable transaction publication.

        Args:
            *_args: Extra arguments accepted to match the collaborator signature.
            **_kwargs: Extra options accepted to match the collaborator signature.

        Raises:
            OSError: When the injected native or persistence failure occurs.
        """
        raise OSError("storage failure")

    # Install fake commit calls.
    monkeypatch.setattr(issue, "commit", fail_commit)
    # Dispatch the restore request and capture its rejection.
    with pytest.raises(OSError, match="storage failure"):
        core.operate(store, issue, base_request("restore", observations=[]))  # type: ignore[arg-type]
    assert issue.state == prior
    assert store.saved == []


def test_archive_prepare_reuses_identical_durable_preparation_receipt() -> None:
    """Retry export publication without appending a second preparation event."""
    # Seed the prior archive-preparation receipt for retry without a new event.
    store, issue = MemoryStore(), MemoryIssue()
    request = base_request("archive-prepare", expected_revision=7)
    prepared_request = {**request, "request_id": request["request_id"] + ":prepare"}
    issue.state["requests"][core.sha(prepared_request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(prepared_request)),
        "result": {"ok": True},
    }
    # Dispatch the archive-prepare request against the modeled issue.
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert [event for event, _state, _files in issue.commits] == [None]


def test_unsupported_operation_never_commits_issue_state() -> None:
    """Return the bounded unsupported diagnostic without publishing payload bytes."""
    # Create the modeled issue and retain its pre-operation state.
    store, issue = MemoryStore(), MemoryIssue()
    before = copy.deepcopy(issue.state)
    # Dispatch the unrecognized request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, base_request("unrecognized"))  # type: ignore[arg-type]
    # Confirm the rejected operation reports UNSUPPORTED_OPERATION.
    assert captured.value.code == "UNSUPPORTED_OPERATION"
    assert issue.state == before
    assert issue.commits == []


def test_poll_without_pre_hook_correlates_unique_pending_parent() -> None:
    """Settle only one participant-local process when a host omits polling admission."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    result = core.operate(
        store,
        issue,
        base_request(
            "tool-complete", tool_id="poll", poll=True, async_handle="pid-7", completed=True
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert next(iter(issue.state["participants"].values()))["pending"] == {}


def test_poll_transport_can_retire_after_its_parent_already_completed() -> None:
    """Remove a previously admitted poll without reviving its settled parent process."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    core.operate(store, issue, base_request("tool-start", tool_id="poll", poll_handle="pid-7"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=True, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    result = core.operate(
        store,
        issue,
        base_request(
            "tool-complete", tool_id="poll", poll=True, async_handle="pid-7", completed=True
        ),
    )  # type: ignore[arg-type]
    assert result["ok"] is True
    assert next(iter(issue.state["participants"].values()))["pending"] == {}


def test_unfinished_tool_without_handle_cannot_be_settled() -> None:
    """Keep a pending call when the host supplies neither completion nor process handle."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    # Capture state before the attempted operation.
    before = copy.deepcopy(issue.state)
    # Dispatch the tool-complete request and capture its rejection.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(
            store, issue, base_request("tool-complete", tool_id="process", completed=False)
        )  # type: ignore[arg-type]
    # Confirm the rejected operation reports UNKNOWN_OPERATION.
    assert captured.value.code == "UNKNOWN_OPERATION"
    assert issue.state == before


def test_repeated_unknown_process_observation_does_not_append_event() -> None:
    """Refresh one unchanged async handle without consuming an event slot."""
    # Create a modeled store and issue at the active revision.
    store, issue = MemoryStore(), MemoryIssue()
    # Dispatch the tool-start request against the modeled issue.
    core.operate(store, issue, base_request("tool-start", tool_id="process"))  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    core.operate(
        store,
        issue,
        base_request("tool-complete", tool_id="process", completed=False, async_handle="pid-7"),
    )  # type: ignore[arg-type]
    assert issue.commits[-1][0] is None
    assert next(iter(issue.state["participants"].values()))["pending"] == {
        "process": {"status": "unknown", "handle": "pid-7"}
    }


def source_ref(locator: str, digest: str) -> dict[str, Any]:
    """Build one required coordinator source reference.

    Args:
        locator: Managed or absolute source locator.
        digest: Recorded SHA-256 digest of the source bytes.

    Returns:
        A packet reference shaped like an assigned source.
    """
    return {
        "id": locator,
        "locator": locator,
        "sha256": digest,
        "required": True,
        "authority": "fixture",
        "reason": "own",
        "stage": "planning",
        "reader": "coordinator",
    }


@pytest.mark.parametrize("status", ["ready", "detached"])
def test_roadmap_refresh_clears_ack_without_reviving_detached(status: str) -> None:
    """Refresh only the roadmap digest and never revive a detached coordinator.

    Args:
        status: Coordinator participant status before the refresh.
    """
    # Model a coordinator packet holding a roadmap and an external reference.
    packet = [source_ref("roadmap.md", "a" * 64), source_ref("/external.md", "b" * 64)]
    state: dict[str, Any] = {
        "participants": {
            "coordinator": {"packet": copy.deepcopy(packet), "ack": "c" * 64, "status": status}
        },
        "assignments": {"coordinator": {"packet": copy.deepcopy(packet)}},
    }
    # Refresh the roadmap reference to new committed bytes.
    core.refresh_roadmap_reference(state, "coordinator", "d" * 64)  # type: ignore[arg-type]
    participant = state["participants"]["coordinator"]
    assert [ref["sha256"] for ref in participant["packet"]] == ["d" * 64, "b" * 64]
    assert state["assignments"]["coordinator"]["packet"] == participant["packet"]
    assert participant["ack"] is None
    assert participant["status"] == ("attached" if status == "ready" else "detached")


def test_roadmap_refresh_keeps_readiness_without_changed_roadmap_reference() -> None:
    """Leave a packet without a changed roadmap reference acknowledged and ready."""
    # Model one packet without a roadmap reference and one already at the new digest.
    for packet in [[source_ref("/external.md", "b" * 64)], [source_ref("roadmap.md", "d" * 64)]]:
        state: dict[str, Any] = {
            "participants": {"coordinator": {"packet": packet, "ack": "c" * 64, "status": "ready"}}
        }
        before = copy.deepcopy(state)
        # Refreshing must not change readiness when no recorded digest differs.
        core.refresh_roadmap_reference(state, "coordinator", "d" * 64)  # type: ignore[arg-type]
        assert state == before


def test_roadmap_refresh_ignores_unscoped_coordinator_and_missing_assignment() -> None:
    """An absent or unscoped coordinator refreshes nothing; a missing assignment is fine."""
    # No participant record and a participant without a packet both return unchanged.
    for participants in [{}, {"coordinator": {"packet": None, "ack": None, "status": "ready"}}]:
        state: dict[str, Any] = {"participants": copy.deepcopy(participants)}
        core.refresh_roadmap_reference(state, "coordinator", "d" * 64)  # type: ignore[arg-type]
        assert state == {"participants": participants}
    # Without a stored assignment only the participant packet is refreshed.
    packet = [source_ref("roadmap.md", "a" * 64)]
    state = {
        "participants": {"coordinator": {"packet": packet, "ack": "c" * 64, "status": "ready"}}
    }
    core.refresh_roadmap_reference(state, "coordinator", "d" * 64)  # type: ignore[arg-type]
    participant = state["participants"]["coordinator"]
    assert participant == {
        "packet": [source_ref("roadmap.md", "d" * 64)],
        "ack": None,
        "status": "attached",
    }
    assert "assignments" not in state


def test_coordinator_roadmap_update_refreshes_its_own_packet_reference() -> None:
    """A coordinator's roadmap update re-points its packet and requires a new ack."""
    store, issue = MemoryStore(), MemoryIssue()
    key = issue.state["coordinator"]
    source = {"id": "issue", "locator": "context/issue.md", "sha256": core.sha(b"source")}
    request = base_request(
        "update",
        expected_revision=7,
        path="roadmap.md",
        old_digest=core.sha(b"approved roadmap"),
        content="revised roadmap",
        provenance={"sources": [source], "applicability": "TEST-30", "status": "draft"},
    )
    assert core.operate(store, issue, request)["ok"] is True  # type: ignore[arg-type]
    participant = issue.state["participants"][key]
    assert participant["packet"][0]["sha256"] == core.sha(b"revised roadmap")
    assert participant["ack"] is None
    assert participant["status"] == "attached"
    assert issue.payload["roadmap.md"] == b"revised roadmap"
    # A note update by the same coordinator leaves its roadmap reference untouched.
    store, issue = MemoryStore(), MemoryIssue()
    note = {**request, "path": "context/note.md", "old_digest": core.sha(b"old note")}
    assert core.operate(store, issue, note)["ok"] is True  # type: ignore[arg-type]
    assert issue.state["participants"][key]["status"] == "ready"


def test_source_read_start_tracks_assigned_preparation_without_acknowledgment() -> None:
    """Reserve an assigned source read before readiness without granting ordinary authority."""
    # Model a participant that has not acknowledged its current assigned packet.
    store, issue = MemoryStore(), MemoryIssue(acknowledged=False)
    key = issue.state["coordinator"]
    packet = copy.deepcopy(issue.state["participants"][key]["packet"])
    original_files = issue.files()
    request = base_request(
        "source-read-start",
        tool_id="source-call",
        packet_digest=core.sha(core.canonical(packet)),
        source_locator="roadmap.md",
    )
    # Preparation creates pending work under the exact participant and observed call.
    result = core.operate(store, issue, request)  # type: ignore[arg-type]
    member = issue.state["participants"][key]
    assert result["ok"] is True
    assert member["pending"] == {"source-call": {"status": "pending"}}
    assert member["packet"] == packet
    assert member["ack"] is None
    assert member["status"] == "attached"
    assert issue.payload == original_files
    assert [event for event, _state, _files in issue.commits] == ["source-read-start"]
    assert store.saved == []


@pytest.mark.parametrize(
    "fault,code",
    [
        ("wrong-packet", "SOURCE_STALE"),
        ("changed-source", "SOURCE_STALE"),
        ("unassigned", "SCOPE_MISSING"),
        ("unavailable", "SCOPE_MISSING"),
        ("poll", "SCOPE_MISSING"),
        ("duplicate", "REQUEST_CONFLICT"),
    ],
)
def test_source_read_start_rejects_invalid_preparation_without_pending_work(
    fault: str, code: str
) -> None:
    """Reject stale or unauthorized preparation while preserving existing work and bytes.

    Args:
        fault: Independent source-read admission failure under examination.
        code: Expected public rejection code.
    """
    # Build a valid pre-readiness request before selecting one invalid boundary.
    store, issue = MemoryStore(), MemoryIssue(acknowledged=False)
    key = issue.state["coordinator"]
    member = issue.state["participants"][key]
    request = base_request("source-read-start", tool_id="source-call", source_locator="roadmap.md")
    if fault == "changed-source":
        issue.state["files"]["roadmap.md"] = core.sha(b"changed roadmap")
        issue.payload["roadmap.md"] = b"changed roadmap"
    elif fault == "unassigned":
        request["source_locator"] = "context/note.md"
    elif fault == "unavailable":
        member["packet"].append(
            {
                **member["packet"][0],
                "id": "optional",
                "locator": "context/missing.md",
                "required": False,
            }
        )
        request["source_locator"] = "context/missing.md"
    elif fault == "poll":
        request["poll_handle"] = "running-process"
    elif fault == "duplicate":
        member["pending"]["source-call"] = {"status": "unknown", "handle": "running-process"}
    request["packet_digest"] = (
        "wrong" if fault == "wrong-packet" else core.sha(core.canonical(member["packet"]))
    )
    before = copy.deepcopy(issue.state)
    original_files = issue.files()
    # A rejected preparation must neither replace pending work nor grant acknowledgment.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, request)  # type: ignore[arg-type]
    assert captured.value.code == code
    assert issue.state == before
    assert issue.payload == original_files
    assert issue.commits == []
    assert store.saved == []


def test_ordinary_tool_start_cannot_borrow_source_preparation_exception() -> None:
    """Source fields on an ordinary tool request cannot bypass packet acknowledgment."""
    # Supply otherwise-valid source preparation evidence on the ordinary tool route.
    store, issue = MemoryStore(), MemoryIssue(acknowledged=False)
    member = issue.state["participants"][issue.state["coordinator"]]
    request = base_request(
        "tool-start",
        tool_id="ordinary-call",
        source_locator="roadmap.md",
        packet_digest=core.sha(core.canonical(member["packet"])),
    )
    before = copy.deepcopy(issue.state)
    # The operation type retains its readiness requirement despite matching source bytes.
    with pytest.raises(core.WorkspaceError) as captured:
        core.operate(store, issue, request)  # type: ignore[arg-type]
    assert captured.value.code == "NOT_READY"
    assert issue.state == before
    assert issue.commits == []
    assert store.saved == []
