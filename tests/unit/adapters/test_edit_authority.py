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


@pytest.mark.parametrize(
    ("host", "args", "cwd", "expected"),
    [
        ("claude-code", {}, "dir", "dir"),
        ("cursor", {"working_directory": "sub"}, "dir", "sub"),
        ("cursor", {"cwd": "sub"}, "dir", "sub"),
        ("cursor", {"cwd": ""}, "dir", "dir"),
        ("claude-code", {}, "missing", None),
        ("claude-code", {}, "relative", None),
    ],
)
def test_execution_directory_is_an_existing_absolute_native_directory(
    tmp_path: Path, host: str, args: dict[str, str], cwd: str, expected: str | None
) -> None:
    """Use Cursor's nonempty override, else the hook directory, only when it exists.

    Args:
        tmp_path: Disposable parent of the directories.
        host: Adapter identity.
        args: Native tool input.
        cwd: Hook working directory name.
        expected: Expected directory name, or None.
    """
    (tmp_path / "dir").mkdir()
    (tmp_path / "sub").mkdir()
    place = {name: str(tmp_path / name) for name in ("dir", "sub", "missing")}
    place["relative"] = "dir"
    args = {key: place.get(value, value) for key, value in args.items()}
    event = {**native_event("Shell", args), "cwd": place[cwd]}
    assert common.execution_directory(event, host) == (expected and place[expected])


@pytest.mark.parametrize(
    ("command", "protected", "child", "calls"),
    [
        ("python build.py", False, False, []),
        ("rm -f x", True, False, ["ready", "authority", "tool-start"]),
        ("rm -f x", True, True, ["ready", "authority", "tool-start"]),
        ("python -m nplet.life'cycle'", False, False, ["ready", "tool-start"]),
    ],
    ids=["unchecked", "protected", "child-protected", "lifecycle"],
)
def test_native_pre_decides_shell_commands_by_recognized_effects(
    monkeypatch: pytest.MonkeyPatch, command: str, protected: bool, child: bool, calls: list
) -> None:
    """Free unchecked parent shell work; gate protected effects and lifecycle commands.

    Args:
        monkeypatch: Replaces repository, recognizer and lifecycle boundaries.
        command: Native shell command.
        protected: Whether the recognized effects are protected.
        child: Whether a child issued the command.
        calls: Expected lifecycle and authority calls in order.
    """
    seen: list[str] = []
    gate = common.EditGate(SimpleNamespace(protected=protected), {}, Mock(), Path("p.json"))
    shell = SimpleNamespace(effects=[], targets=["/x"])
    effect = {"tool_name": "Bash", "tool_input": {"command": command}}
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "native_effect", lambda *_args: effect)
    monkeypatch.setattr(common, "edit_gate", lambda *_args: None)
    monkeypatch.setattr(common.task_boundary, "task_free", lambda *_args: False)
    monkeypatch.setattr(common, "shell_effects", lambda *_args: shell)
    monkeypatch.setattr(common, "effect_gate", lambda _effects: gate)
    monkeypatch.setattr(common, "lookup_required", lambda *_args: False)
    monkeypatch.setattr(common, "edit_authority", lambda *_args: seen.append("authority"))
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    monkeypatch.setattr(
        common.core,
        "execute",
        lambda request: seen.append(request["operation"]) or {"ok": True, "code": "OK"},
    )
    event = {**native_event("Shell", {"command": command}), "child": child}
    assert common.native_pre(event, "cursor") is None
    assert seen == calls


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("echo nplet.lifecycle", False),
        ("python -m compileall src/nplet/lifecycle/task_workspace.py", False),
        ("ruff check src/nplet/lifecycle/task_workspace.py", False),
        ("python -c 'print(1)'", False),
        ("python -X utf8 build.py task_workspace.py", False),
        (".venv/bin/python src/nplet/lifecycle/task_workspace.py --request-json '{}'", True),
        ("poetry run python3 -I src/nplet/lifecycle/task_work''space.py", True),
        ("python -X utf8 src/nplet/adapters/bootstrap.py", True),
        ("py -m nplet.lifecycle.task_workspace", True),
        ("python -c 'import nplet.lifecycle'", True),
        ("src/nplet/lifecycle/task_workspace.py --request-json '{}'", True),
        ("& 'C:\\nplet\\python.exe' 'C:\\nplet\\bootstrap.py'", True),
        ("python src/nplet/life\\cycle/task_workspace.py", True),
    ],
)
def test_lifecycle_invocation_matches_command_position(command: str, expected: bool) -> None:
    """Match entry points the command runs, not names given only as data.

    Args:
        command: Native shell command.
        expected: Whether the command may run a lifecycle entry point.
    """
    assert common.lifecycle_invocation(command) is expected
