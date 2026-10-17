"""Provide explicit admission doubles for focused lifecycle-dispatch unit tests.

Fixtures are opt-in. Policy and task-free contracts use the real effect translator.
"""

import pytest

from nplet.adapters import codex, common, effects


@pytest.fixture
def protected_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate effect translation for a test of task-required dispatch.

    Args:
        monkeypatch: Scoped replacement of the shared classifier lookup.
    """
    # Replace only admission output; readiness and correlation remain owned by the test.
    monkeypatch.setattr(
        common,
        "effect_decision",
        lambda *_args: effects.Decision("protected", "IMPLEMENTATION_TARGET", ""),
    )


@pytest.fixture
def recorded_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply an existing exact native admission for completion-dispatch tests.

    Args:
        monkeypatch: Scoped replacement of the existing-operation lookup.
    """
    # Supply recorded work without changing the callback's effect or settlement behavior.
    monkeypatch.setattr(common, "pending_tool", lambda *_args: True)


@pytest.fixture
def selected_ticket_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate selected-ticket routing for partial-envelope dispatch tests.

    Args:
        monkeypatch: Scoped replacement of exact startup marker lookups.
    """
    # These tests own startup state separately; real correlation has dedicated contract cases.
    monkeypatch.setattr(common, "selected_ticket", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        common,
        "ticket_callback",
        lambda native, host: (
            codex.startup_lookup_state(native, ".lookup.json")
            if host == "codex"
            else common.lookup_required(native, host)
        ),
    )
