"""Shared bounded in-memory lifecycle collaborators without filesystem persistence."""

from __future__ import annotations

import copy
from typing import Any

from nplet.lifecycle import task_workspace as core


def base_request(operation: str, **fields: object) -> dict[str, Any]:
    """Create one explicitly bound request for the modeled issue.

    Args:
        operation: Lifecycle operation encoded in the request.
        **fields: Additional fields merged into the request.

    Returns:
        Versioned request bound to the modeled repository, issue, and participant.
    """
    return {
        "schema_version": 1,
        "operation": operation,
        "request_id": "request-1",
        "host": "codex",
        "session_id": "session",
        "issue_id": "TEST-30",
        "binding_generation": 2,
        **fields,
    }


class MemoryControl:
    """Expose only the cleanup-intent check used by normal operations."""

    def __init__(self) -> None:
        """Start without persistent control documents."""
        self.data: dict[str, object] = {}

    def exists(self, name: str) -> bool:
        """Report no recorded cleanup intent in this active issue.

        Args:
            name: Requested file, directory, or control-entry name.

        Returns:
            Whether the requested test entry exists.
        """
        assert name == "cleanup.json"
        return False

    def put(self, name: str, value: object) -> None:
        """Retain a control document for later read-back.

        Args:
            name: Requested file, directory, or control-entry name.
            value: Document value stored by the fake control boundary.
        """
        self.data[name] = copy.deepcopy(value)

    def json(self, name: str) -> Any:
        """Return the exact previously published control document.

        Args:
            name: Requested file, directory, or control-entry name.

        Returns:
            Decoded JSON value for the selected fake file.
        """
        return copy.deepcopy(self.data[name])


class MemoryStore:
    """Record binding and view effects without opening Git or filesystem state."""

    def __init__(self) -> None:
        """Hold the bound session and observed view calls."""
        # Initialize repository identity, view-call log, and binding-write log.
        self.registration = {"repo_id": "repo"}
        self.views: list[str] = []
        self.saved: list[str] = []
        self.task = None

    def binding(self) -> dict[str, object]:
        """Return the existing exact issue and generation binding.

        Returns:
            Participant binding used by the operation.
        """
        return {"issue_id": "TEST-30", "binding_generation": 2}

    def view(self, identifier: str) -> None:
        """Record that the issue view is exposed to this participant.

        Args:
            identifier: Issue or participant identifier used in this scenario.
        """
        self.views.append(identifier)

    def save_binding(self, _state: dict[str, Any], key: str) -> None:
        """Record a binding persistence effect for attach flows.

        Args:
            _state: Unused state argument accepted by this fake.
            key: Participant key recorded as saved.
        """
        self.saved.append(key)


class MemoryIssue:
    """Retain committed state and files while recording requested transactions."""

    def __init__(self, *, acknowledged: bool = True) -> None:
        """Start with one assigned participant and immutable note bytes.

        Args:
            acknowledged: Whether the participant starts with a current packet acknowledgment.
        """
        # Initialize issue identity, control handle, and transaction logs.
        self.id = "TEST-30"
        self.control = MemoryControl()
        self.store = MemoryStore()
        self.recoveries = 0
        self.commits: list[tuple[str | None, dict[str, Any], dict[str, bytes]]] = []
        # Bind the initial coordinator to the modeled host and session.
        key = core.participant_key(base_request("read"))
        # Install its roadmap packet and baseline committed issue state.
        packet = [
            {
                "id": "roadmap",
                "locator": "roadmap.md",
                "sha256": core.sha(b"approved roadmap"),
                "required": True,
                "authority": "task-workspace",
                "reason": "issue-resume",
                "stage": "execution",
                "reader": key,
            }
        ]
        self.state: dict[str, Any] = {
            "schema_version": 1,
            "repo_id": "repo",
            "issue_id": self.id,
            "issue_uuid": "uuid",
            "revision": 7,
            "generation": 1,
            "disposition": "active",
            "storage": "present",
            "coordinator": key,
            "owners": {"roadmap.md": key, "context/note.md": key},
            "participants": {
                key: {
                    "generation": 2,
                    "status": "ready" if acknowledged else "attached",
                    "pending": {},
                    "packet": packet,
                    "ack": core.sha(core.canonical(packet)) if acknowledged else None,
                }
            },
            "files": {
                "roadmap.md": core.sha(b"approved roadmap"),
                "context/note.md": core.sha(b"old note"),
                "events.jsonl": core.sha(b""),
            },
            "seq": 0,
            "head": None,
            "requests": {},
            "provenance": {},
        }
        self.payload = {
            "roadmap.md": b"approved roadmap",
            "context/note.md": b"old note",
            "events.jsonl": b"",
        }

    def recover(self) -> None:
        """Record that recovery precedes every operation."""
        self.recoveries += 1

    def files(self) -> dict[str, bytes]:
        """Return the current committed payload as an isolated mutable copy.

        Returns:
            File mapping retained by the fake store.
        """
        return self.payload.copy()

    def committed_state(self) -> dict[str, Any]:
        """Return the currently committed control state.

        Returns:
            State currently committed by the fake issue.
        """
        return copy.deepcopy(self.state)

    def commit(
        self,
        state: dict[str, Any],
        files: dict[str, bytes],
        _request: dict[str, Any],
        result: dict[str, Any],
        event_type: str | None = None,
    ) -> dict[str, Any]:
        """Persist modeled bytes and expose the requested event and state effects.

        Args:
            state: Issue state used by this scenario.
            files: File contents used to build or inspect the workspace.
            _request: Unused request argument accepted by this fake.
            result: Response returned by the fake collaborator.
            event_type: Event type selected by the case.

        Returns:
            Success response mapping containing the supplied operation result.
        """
        self.commits.append((event_type, copy.deepcopy(state), files.copy()))
        # Retain the committed state before exposing the fake operation response.
        self.state = copy.deepcopy(state)
        self.payload = files.copy()
        return {"ok": True, "code": "OK", **result}
