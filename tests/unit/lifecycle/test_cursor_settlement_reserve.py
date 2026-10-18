"""Verify remaining settlement obligations and worst-case serialized field bounds."""

from __future__ import annotations

import copy

import pytest

from nplet.lifecycle import task_workspace as core


def reserve_state() -> dict:
    """Build a minimal bound invocation with no pending ordinary effects.

    Returns:
        In-memory state sufficient for pure settlement accounting.
    """
    return {
        "repo_id": "repository",
        "issue_id": "TEST-1",
        "revision": 99,
        "seq": 99,
        "participants": {"child": {"generation": 9, "pending": {}, "cursor_invocation": "i"}},
        "cursor_invocations": {
            "i": {
                "phase": "bound",
                "parent_conversation": "parent",
                "native_call": "native\ncall",
                "packet": [],
                "owned_paths": [],
                "resume_generation": 99,
            }
        },
    }


def test_retired_invocations_release_only_their_remaining_reserve() -> None:
    """A terminal invocation holds no future settlement capacity."""
    state = reserve_state()
    before = copy.deepcopy(state)
    amount, slots = core.settlement_reserve(state)
    assert amount > 2048 * 6
    assert slots == 1
    assert state == before
    state["cursor_invocations"]["i"]["phase"] = "retired"
    assert core.settlement_reserve(state) == (0, 0)
    state["participants"] = {}
    assert core.settlement_reserve(state) == (0, 0)


def test_admission_reserves_two_real_packet_copies_and_owners() -> None:
    """Long escaped source locators remain covered before the actual child binds."""
    state = reserve_state()
    bound, _ = core.settlement_reserve(state)
    invocation = state["cursor_invocations"]["i"]
    invocation["phase"] = "admitted"
    locator = "/source/" + '\\"' * 4096
    invocation["packet"] = [{"locator": locator, "reader": "template", "sha256": "a" * 64}]
    invocation["owned_paths"] = ["context/owned.md"]
    admitted, slots = core.settlement_reserve(state)
    assert slots == 2
    assert admitted - bound > 2 * len(core.canonical(locator))


@pytest.mark.parametrize("provisional", [False, True])
def test_source_obligation_keeps_actual_locator_and_future_callback_room(provisional: bool) -> None:
    """Content proof releases one future callback while retaining completion credit room.

    Args:
        provisional: Whether content was already verified by the source observer.
    """
    state = reserve_state()
    baseline, _ = core.settlement_reserve(state)
    locator = "/source/" + '\\"' * 4096
    source = {
        "locator": locator,
        "packet_digest": "p" * 64,
        "sha256": "s" * 64,
        "invocation_id": "i",
    }
    if provisional:
        source["provisional"] = {"sha256": "s" * 64, "content_length": 10}
    state["participants"]["child"]["pending"]["read"] = {
        "status": "pending",
        "cursor_source": source,
    }
    amount, slots = core.settlement_reserve(state)
    assert amount - baseline > len(core.canonical(locator))
    assert slots == (2 if provisional else 3)
    if not provisional:
        source["provisional"] = {"sha256": "s" * 64, "content_length": 10}
        assert core.settlement_reserve(state)[0] < amount


@pytest.mark.parametrize("status,slots", [("pending", 2), ("unknown", 1)])
def test_child_effect_reserves_more_than_parent_event_only_accounting(
    status: str, slots: int
) -> None:
    """Only classified child effects add retained state settlement obligations.

    Args:
        status: Foreground or uncertain pending effect.
        slots: Remaining events required by the existing effect lifecycle.
    """
    state = reserve_state()
    baseline, _ = core.settlement_reserve(state)
    state["participants"]["child"]["pending"] = {"work": {"status": status}}
    child, events = core.settlement_reserve(state)
    assert child > baseline
    assert events == 1 + slots
    del state["participants"]["child"]["cursor_invocation"]
    parent, parent_events = core.settlement_reserve(state)
    assert parent < child
    assert parent_events == events
    # The parent Task pending record is already covered by its invocation, never counted twice.
    state["participants"]["child"]["pending"]["work"]["cursor_invocation"] = "i"
    assert core.settlement_reserve(state)[1] == 1
