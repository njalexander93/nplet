"""Verify Python-only invocation entry and the single public wire denial."""

from __future__ import annotations

import json
import sys

import pytest

from nplet.lifecycle import _invocation_records as records
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit

INTERNAL_NAMES = (
    "cursor-admit",
    "cursor-bind-child",
    "cursor-retire",
    "cursor-source-callback",
)


@pytest.mark.parametrize("operation", INTERNAL_NAMES)
def test_public_execute_denies_internal_names_before_store_access(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Reject wire requests independently of registration, ownership or caller flags.

    Args:
        monkeypatch: Prevents any store access from being mistaken for a safe denial.
        operation: One actual adapter-only operation.
    """

    def forbidden_store(*_args: object, **_kwargs: object) -> None:
        """Fail if public internal-name rejection reaches persistent storage.

        Args:
            *_args: Unexpected store arguments.
            **_kwargs: Unexpected store options.

        Raises:
            AssertionError: The public fence failed before storage access.
        """
        pytest.fail("public internal operation accessed storage")

    # A caller-supplied internal flag is data, never the Python-only entry capability.
    monkeypatch.setattr(core, "Store", forbidden_store)
    result = core.execute(
        {
            "schema_version": 1,
            "request_id": "public-denial",
            "operation": operation,
            "internal": True,
        }
    )
    assert result["ok"] is False
    assert result["code"] == "HOST_UNSUPPORTED_TOOL"


@pytest.mark.parametrize("operation", INTERNAL_NAMES)
def test_cli_uses_the_same_public_denial(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    """Keep CLI internal-name refusal in execute rather than a second parser inventory.

    Args:
        monkeypatch: Selects an exact bounded CLI request.
        capsys: Captures the actual emitted response.
        operation: One actual adapter-only operation.
    """
    # The CLI receives no registration identity because denial must precede store opening.
    request = {"schema_version": 1, "request_id": "cli-denial", "operation": operation}
    monkeypatch.setattr(sys, "argv", ["task_workspace", "--request-json", json.dumps(request)])
    assert core.main() == 3
    assert json.loads(capsys.readouterr().out)["code"] == "HOST_UNSUPPORTED_TOOL"


def test_fixed_metadata_allowance_is_cached_and_derived_from_validated_maxima(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construct bounded terminal metadata once while retaining real phase-record factories.

    Args:
        monkeypatch: Counts only maximum-constructor calls, not storage or encoding proxies.
    """
    real_maximum = records.maximum_terminal
    calls: list[bool] = []

    def maximum() -> records.TerminalEvidence:
        """Record one maximum construction and return the actual validated record.

        Returns:
            A maximum terminal record checked by the production constructor.
        """
        calls.append(True)
        return real_maximum()

    # Cache cleanup belongs to this test even if construction or an assertion fails.
    records.fixed_settlement_bytes.cache_clear()
    monkeypatch.setattr(records, "maximum_terminal", maximum)
    try:
        first = records.fixed_settlement_bytes()
        assert records.fixed_settlement_bytes() == first
        assert calls == [True]
        assert first[0] > 2 * records.NATIVE_TEXT_LIMIT * 6
        assert first[1] > records.NATIVE_TEXT_LIMIT * 6
    finally:
        # Leave no call-count fixture result in the process-wide derived cache.
        records.fixed_settlement_bytes.cache_clear()


def test_maximum_terminal_is_a_valid_independently_bounded_contract() -> None:
    """Keep escaping and scalar maxima within the actual retained result validator."""
    # Both native fields can consume six JSON characters for each permitted UTF-8 byte.
    maximum = records.maximum_terminal()
    assert maximum["duration_ms"] == (1 << 64) - 1
    assert len(maximum["storage_key"].encode()) == records.NATIVE_TEXT_LIMIT
    assert len(maximum["native_call"].encode()) == records.NATIVE_TEXT_LIMIT
    assert len(maximum["child_conversation"]) == records.TOKEN_LIMIT
    assert records.terminal_evidence(maximum) == maximum


@pytest.mark.parametrize("operation", INTERNAL_NAMES)
def test_internal_execute_reaches_normal_storage_validation_and_safe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Permit trusted native operations while retaining ordinary storage error handling.

    Args:
        monkeypatch: Replace only the storage boundary with an explicit missing-binding result.
        operation: One actual adapter-only operation.
    """
    request = {
        "schema_version": 1,
        "request_id": "internal-entry",
        "operation": operation,
        "worktree": "/modeled/main",
        "issue_id": "TEST-30",
        "repo_id": "repo",
        "host": "cursor",
        "session_id": "parent",
        "binding_generation": 2,
    }
    observed: list[dict] = []

    def unavailable_store(actual: dict) -> None:
        """Report a normal storage precondition failure after validating the internal entry.

        Args:
            actual: Complete request passed to the storage boundary.

        Raises:
            core.WorkspaceError: This modeled session has no usable binding.
        """
        observed.append(actual)
        raise core.WorkspaceError("BINDING_MISSING")

    # Exercise the real executor; only persistent storage is replaced.
    monkeypatch.setattr(core, "Store", unavailable_store)
    result = core.execute_internal(request)
    assert result["ok"] is False
    assert result["code"] == "BINDING_MISSING"
    assert observed == [request]
