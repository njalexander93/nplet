"""Verify public Cursor adapter entry points and internal-operation wire rejection."""

from __future__ import annotations

import runpy
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor_children as children
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("outcome", ["success", "invalid-arguments", "unavailable", "unexpected"])
def test_reconcile_cli_exposes_only_bounded_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], outcome: str
) -> None:
    """Keep the public CLI free of caller-selected result evidence and exception details.

    Args:
        monkeypatch: Scoped process and reconciliation boundaries.
        capsys: Captured bounded JSON output.
        outcome: Selected success or failure boundary.
    """
    # Accept only the exact public command and derive the checkout from actual cwd.
    token = children.call_token("native-call")
    argv = ["cursor_children.py", "cursor-reconcile", "--issue", "TEST-1", "--call", token]
    if outcome == "invalid-arguments":
        argv += ["--result", "forged"]
    monkeypatch.setattr(children.sys, "argv", argv)
    reconcile = MagicMock(return_value={"ok": True, "code": "OK"})
    if outcome == "unavailable":
        reconcile.side_effect = core.WorkspaceError("RESULT_NOT_AVAILABLE")
    elif outcome == "unexpected":
        reconcile.side_effect = OSError("private-storage-detail")
    monkeypatch.setattr(children, "reconcile", reconcile)
    # Return bounded diagnostics while withholding internal exception text.
    result = children.main()
    expected = {
        "success": "OK",
        "invalid-arguments": "INVALID_REQUEST",
        "unavailable": "RESULT_NOT_AVAILABLE",
        "unexpected": "RECOVERY_REQUIRED",
    }[outcome]
    assert result == (0 if outcome == "success" else 3)
    assert (
        capsys.readouterr().out
        == core.canonical({"ok": outcome == "success", "code": expected}).decode() + "\n"
    )
    if outcome != "invalid-arguments":
        assert reconcile.call_args.args[:3] == (str(Path.cwd()), "TEST-1", token)
    else:
        reconcile.assert_not_called()


@pytest.mark.parametrize(
    "operation",
    ["cursor-admit", "cursor-bind-child", "cursor-retire", "cursor-source-callback", {}, []],
)
def test_public_cli_and_execute_share_internal_operation_rejection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: object
) -> None:
    """Both CLI encodings reach the same public core guard without opening repository storage.

    Args:
        monkeypatch: Selects CLI argv and enforces rejection before repository discovery.
        capsys: Captures canonical public diagnostics.
        operation: Reserved Python-only operation or malformed nonstring.
    """
    import base64
    import json

    from nplet.adapters import bootstrap

    # Keep the real public execute boundary; an adapter-local fake could hide a missing guard.
    request = {
        "schema_version": 1,
        "operation": operation,
        "request_id": "request",
        "worktree": "/checkout",
    }
    monkeypatch.setattr(
        core, "repository", lambda *_args: pytest.fail("internal wire opened repository")
    )
    expected = "HOST_UNSUPPORTED_TOOL" if isinstance(operation, str) else "INVALID_REQUEST"
    result = core.execute(request)
    assert result["ok"] is False
    assert result["code"] == expected
    # JSON and encoded bootstrap transports must preserve the core's same bounded rejection.
    monkeypatch.setattr(
        core.sys, "argv", ["task_workspace.py", "--request-json", json.dumps(request)]
    )
    assert core.main() == 3
    assert json.loads(capsys.readouterr().out)["code"] == expected
    encoded = base64.urlsafe_b64encode(json.dumps(request).encode()).decode()
    monkeypatch.setattr(bootstrap.sys, "argv", ["bootstrap.py", "--request-base64", encoded])
    assert bootstrap.main() == 3
    assert json.loads(capsys.readouterr().out)["code"] == expected


@pytest.mark.parametrize(
    "argv,mode",
    [
        (["cursor.py"], "normal"),
        (["cursor.py", "--observe-read-content"], "observer"),
        (["cursor.py", "--unexpected"], "invalid"),
    ],
)
def test_cursor_main_selects_only_fixed_cli_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str], mode: str
) -> None:
    """Normal event JSON cannot self-select the full-content observer route.

    Args:
        monkeypatch: Replaces entry argv and runtime boundaries.
        capsys: Captures invalid-argument diagnostic.
        argv: Exact process argument list.
        mode: Expected closed route.
    """
    from nplet.adapters import cursor

    monkeypatch.setattr(cursor.sys, "argv", argv)
    normal = MagicMock(return_value=17)
    observer = MagicMock(return_value=19)
    monkeypatch.setattr(cursor.common, "run_native", normal)
    monkeypatch.setattr(cursor.runner, "run", observer)
    result = cursor.main()
    if mode == "normal":
        assert result == 17
        normal.assert_called_once_with(cursor.handle, cursor.failure)
        observer.assert_not_called()
    elif mode == "observer":
        assert result == 19
        observer.assert_called_once()
        assert observer.call_args.args[0] is cursor.handle
        renderer = observer.call_args.args[1]
        assert renderer("beforeReadFile", "BUSY") == {"permission": "allow"}
        assert renderer("beforeReadFile", "INVALID_REQUEST")["permission"] == "deny"
        assert observer.call_args.kwargs == {"content_observer": True}
        normal.assert_not_called()
    else:
        assert result == 2
        assert "INVALID_REQUEST" in capsys.readouterr().err
        normal.assert_not_called()
        observer.assert_not_called()


def test_reconcile_module_entrypoint_rejects_unsupported_argv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise executable rejection without opening result storage or Cursor.

    Args:
        monkeypatch: Supplies unsupported literal process invocation.
        capsys: Captures bounded public result.
    """
    monkeypatch.setattr(children.sys, "argv", [str(children.ENTRY), "unsupported"])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(children.ENTRY), run_name="__main__")
    assert error.value.code == 3
    assert (
        capsys.readouterr().out
        == core.canonical({"ok": False, "code": "INVALID_REQUEST"}).decode() + "\n"
    )
