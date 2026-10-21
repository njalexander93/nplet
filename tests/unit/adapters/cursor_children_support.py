"""Share exact Cursor invocation records and mocked Store boundaries for unit tests."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor_children as children
from nplet.lifecycle import task_workspace as core


def cursor_record(phase: str = "bound") -> dict:
    """Supply one coherent committed invocation record for mocked adapter boundaries.

    Args:
        phase: Lifecycle invocation phase to expose.

    Returns:
        Metadata-only record with exact native call, latest member pointer and parent.
    """
    native = "native\nparent-call"
    invocation = {
        "native_call": native,
        "tool_id": children.call_token(native),
        "phase": phase,
        "parent_session": "actual-parent",
        "parent_conversation": "93ea96d2-93ce-4359-b562-65c859fb042f",
        "parent_generation": 7,
        "parent": core.participant_key({"host": "cursor", "session_id": "actual-parent"}),
        "child_conversation": "c11e3a99-d371-410b-a910-378914bdb9a1",
        "child_participant": "child-key",
        "native_generation": "native-child-generation",
    }
    return {
        "invocation_id": "invocation",
        "invocation": invocation,
        "issue_id": "TEST-1",
        "issue_uuid": "issue-uuid",
        "revision": 12,
        "member": {"cursor_invocation": "invocation", "generation": 1},
    }


def cursor_mock_store(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Replace only the external Store/Directory boundary with scoped context managers.

    Args:
        monkeypatch: Scoped constructor replacement.

    Returns:
        Mock registered store and one opened issue control.
    """
    store = MagicMock()
    store.root = Path("/checkout")
    store.registration = {"repo_id": "repo"}
    store.issues_present = True
    store.issues.names.return_value = ["TEST-1"]
    control = MagicMock()
    control.lock.return_value = nullcontext()
    control.exists.return_value = False
    store.issues.child.return_value.__enter__.return_value = control
    mutex = MagicMock()
    mutex.lock.return_value = nullcontext()
    mutex.exists.side_effect = lambda name: name == "lock"
    store.control.child.return_value.__enter__.return_value = mutex
    manager = MagicMock()
    manager.__enter__.return_value = store
    monkeypatch.setattr(children.core, "Store", lambda _request, **_kwargs: manager)
    issue = MagicMock()
    issue.committed_state.side_effect = lambda: control.json("state.json")
    monkeypatch.setattr(children.core, "Issue", lambda *_args: issue)
    monkeypatch.setattr(children.routing, "verify", lambda *_args: None)
    monkeypatch.setattr(children.routing, "initialize", lambda *_args: None)
    monkeypatch.setattr(children.routing, "claim", lambda *_args: None)
    monkeypatch.setattr(
        children.routing,
        "lookup",
        lambda *_args: {"issue_id": "TEST-1", "issue_uuid": "issue-uuid"},
    )
    return store, control
