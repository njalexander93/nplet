"""Check pure Cursor invocation identity guards without filesystem collaborators."""

from __future__ import annotations

import pytest

from nplet.lifecycle import _invocation_records as invocation_records
from nplet.lifecycle import _invocations as invocations
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("value", ["native\ncall", "row:key", "x" * 2048])
def test_native_text_preserves_exact_bounded_content(value: str) -> None:
    """Keep native newline and storage delimiters without token normalization.

    Args:
        value: Bounded exact native text.
    """
    assert invocation_records.native_text(value) == value


@pytest.mark.parametrize("value", ["", "\x00", "x" * 2049, 1, None, b"call"])
def test_native_text_rejects_invalid_inputs(value: object) -> None:
    """Reject malformed native metadata at its request boundary.

    Args:
        value: Invalid external value.

    Raises:
        AssertionError: Input is accepted or reports the wrong contractual code.
    """
    # Capture only the boundary operation expected to reject input.
    with pytest.raises(core.WorkspaceError) as captured:
        invocation_records.native_text(value)
    assert captured.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "change",
    [
        None,
        "missing",
        "retired",
        "wrong-child",
        "wrong-generation",
    ],
)
def test_classified_child_requires_its_active_exact_generation(change: str | None) -> None:
    """Accept only one bound invocation-to-participant generation relationship.

    Args:
        change: Single state dimension changed from a matching active invocation.

    Raises:
        AssertionError: A stale child is accepted or valid authority is rejected.
    """
    # Supply independent chosen participant and invocation identities.
    member = {
        "generation": 3,
        "status": "ready",
        "pending": {},
        "packet": [],
        "ack": core.sha(core.canonical([])),
        "cursor_invocation": "invocation",
    }
    invocation = {
        "phase": "bound",
        "child_participant": "reader",
        "child_generation": 3,
    }
    state = {"cursor_invocations": {"invocation": invocation}}
    # Break only the selected relationship; no I/O or mutable global is involved.
    if change == "missing":
        state["cursor_invocations"] = {}
    elif change == "retired":
        invocation["phase"] = "retired"
    elif change == "wrong-child":
        invocation["child_participant"] = "other-reader"
    elif change == "wrong-generation":
        invocation["child_generation"] = 2
    if change is None:
        invocations.child_active(state, "reader", member)  # type: ignore[arg-type]
    else:
        with pytest.raises(core.WorkspaceError) as captured:
            invocations.child_active(state, "reader", member)  # type: ignore[arg-type]
        assert captured.value.code == "STALE_BINDING"


def test_ordinary_participant_retains_existing_authority_contract() -> None:
    """Leave participants with no Cursor classification outside the new fence."""
    invocations.child_active({}, "reader", {"generation": 1})  # type: ignore[arg-type]
