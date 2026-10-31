"""Check structured-edit authority and effect dispatch in the shared adapter."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core
from tests.unit.adapters.native_event_support import native_event

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("code", "phrase"),
    [
        ("POLICY_ADMINISTRATION", "the user edits it directly"),
        ("APPROVAL_REQUIRED", "explicit human approval"),
        ("SCOPE_REQUIRED", "`Repository scope`"),
    ],
)
def test_recovery_names_the_edit_authority_route(code: str, phrase: str) -> None:
    """Each edit-authority denial names its own next step.

    Args:
        code: Edit-authority diagnostic.
        phrase: Text unique to that diagnostic's route.
    """
    assert phrase in common.recovery(code, "claude-code")


@pytest.mark.parametrize(
    ("approved", "missing", "code"),
    [(False, [], "APPROVAL_REQUIRED"), (True, ["effect"], "SCOPE_REQUIRED"), (True, [], None)],
)
def test_edit_authority_requires_approval_then_scope(
    monkeypatch: pytest.MonkeyPatch, approved: bool, missing: list[str], code: str | None
) -> None:
    """Deny without approval first, then for any protected effect outside the scope.

    Args:
        monkeypatch: Replaces the scope decision.
        approved: Recorded approval of the current revision.
        missing: Effects the scope does not cover.
        code: Expected denial, or None when admitted.
    """
    monkeypatch.setattr(common.task_boundary, "unauthorized", lambda *_args: missing)
    gate = common.EditGate(Mock(), {}, Mock(), Path("policy.json"))
    ready = {"approval": {"approved": approved, "scope": []}}
    if code is None:
        assert common.edit_authority(gate, ready, None) is None
        return
    with pytest.raises(core.WorkspaceError) as captured:
        common.edit_authority(gate, ready, None)
    assert captured.value.code == code


@pytest.mark.parametrize("protected", [False, True], ids=["free", "protected"])
def test_native_pre_decides_structured_edits_by_their_effects(
    monkeypatch: pytest.MonkeyPatch, protected: bool
) -> None:
    """Free an edit without protected effects; check authority for a protected one.

    Args:
        monkeypatch: Replaces repository, effect and lifecycle boundaries.
        protected: Whether the edit has a protected effect.
    """
    calls: list[str] = []
    gate = common.EditGate(SimpleNamespace(protected=protected), {}, Mock(), Path("p.json"))
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "edit_gate", lambda *_args: gate)
    monkeypatch.setattr(common, "edit_authority", lambda *_args: calls.append("authority"))
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    monkeypatch.setattr(
        common.core,
        "execute",
        lambda request: calls.append(request["operation"]) or {"ok": True, "code": "OK"},
    )
    assert common.native_pre(native_event("Delete"), "cursor") is None
    assert calls == (["ready", "authority", "tool-start"] if protected else [])
