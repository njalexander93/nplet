"""Verify remaining settlement obligations and worst-case serialized field bounds."""

from __future__ import annotations

import copy

import pytest

from nplet.lifecycle import _invocation_records as records
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


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


@pytest.fixture
def maximal_publication() -> tuple[
    records.AdmittedInvocation, records.ChildClaim, records.TerminalEvidence
]:
    """Provide boundary values from native text, token, hash and uint64 contracts.

    Returns:
        Admitted invocation, child claim and validated foreground evidence. Control bytes
        need six JSON bytes per UTF-8 byte; printable token backslashes need two.
    """
    token = "\\" * records.TOKEN_LIMIT
    native = "\x01" * records.NATIVE_TEXT_LIMIT
    digest = "f" * 64
    terminal = records.terminal_evidence(
        {
            "parent_conversation": token,
            "native_call": native,
            "storage_key": native,
            "child_conversation": token,
            "kind": "foreground-success",
            "is_background": False,
            "background_reason": 0,
            "duration_ms": (1 << 64) - 1,
            "binary_sha256": digest,
            "executor_sha256": digest,
            "workbench_sha256": digest,
        }
    )
    claim = records.ChildClaim(
        child_conversation=token,
        child_participant=digest,
        child_generation=1,
        native_generation=token,
    )
    admitted = records.AdmittedInvocation(
        parent=digest,
        parent_generation=1,
        parent_session=token,
        parent_conversation=token,
        task_digest=digest,
        packet=[],
        owned_paths=[],
        resume_child=None,
        resume_generation=None,
        invocation_id=None,
        plan_id=token,
        tool_id=token,
        native_call=native,
        phase="admitted",
        child_conversation=None,
        child_participant=None,
        child_generation=None,
        native_generation=None,
        terminal_result=None,
    )
    return admitted, claim, terminal


@pytest.mark.parametrize("retired", [False, True])
def test_fixed_phase_allowance_covers_actual_maximally_escaped_publication(
    maximal_publication: tuple[
        records.AdmittedInvocation, records.ChildClaim, records.TerminalEvidence
    ],
    retired: bool,
) -> None:
    """Bound the actual changed phase fields without copying the allowance arithmetic.

    Args:
        maximal_publication: Independently chosen contract-boundary inputs.
        retired: Publish terminal evidence or only the binding identity.
    """
    admitted, claim, terminal = maximal_publication
    published = (
        records.retired_record(admitted, claim, terminal)
        if retired
        else records.bound_record(admitted, claim)
    )
    # Inherited plan/packet fields do not grow; integer widths have a separate actual charge.
    changed = {key: value for key, value in published.items() if admitted.get(key) != value}
    assert changed.pop("child_generation") == 1
    allowance = records.RETIREMENT_METADATA_BYTES if retired else records.BINDING_METADATA_BYTES
    assert len(core.canonical(changed)) <= allowance


@pytest.mark.parametrize(
    "fragment", ["participant", "assignment", "request", "receipt", "proof", "effect", "head"]
)
def test_fixed_fragment_allowances_cover_actual_maximum_serialization(
    maximal_publication: tuple[
        records.AdmittedInvocation, records.ChildClaim, records.TerminalEvidence
    ],
    fragment: str,
) -> None:
    """Check independent full encoded examples against the fixed fragment limits.

    Args:
        maximal_publication: Valid bounded native evidence and actual record constructors.
        fragment: One distinct retained metadata shape required by settlement.
    """
    admitted, claim, _ = maximal_publication
    digest = "f" * 64
    token = "\\" * records.TOKEN_LIMIT
    locator = "/source/" + '\\"' * 4096
    value: object
    if fragment == "participant":
        participant = records.child_member(claim, digest, None, status="detached")
        value = {"participants": {digest: participant}}
        allowance = records.PARTICIPANT_METADATA_BYTES + len(
            core.canonical(claim["child_generation"])
        )
    elif fragment == "assignment":
        _, assignment, _ = records.child_scope(admitted, claim, digest)
        value = {"assignments": {digest: assignment}}
        allowance = records.ASSIGNMENT_METADATA_BYTES
    elif fragment == "request":
        variables = {
            "revision": 10**1024,
            "binding_generation": 10**1024,
            "repo_id": "r" * 160,
            "issue_id": "TEST-1",
        }
        # This is the retained response union, not the production size calculation.
        value = {
            digest: {
                "digest": digest,
                "result": {
                    **variables,
                    "ok": True,
                    "code": "OK",
                    "invocation_id": digest,
                    "source_content_verified": False,
                    "source_receipt": False,
                },
            }
        }
        allowance = records.REQUEST_METADATA_BYTES + len(core.canonical(variables))
    elif fragment == "receipt":
        receipt = records.SourceReceipt(
            packet_digest=digest,
            sha256=digest,
            invocation_id=digest,
        )
        value = {"cursor_sources": {locator: receipt}}
        allowance = records.SOURCE_RECEIPT_METADATA_BYTES + len(core.canonical(locator))
    elif fragment == "proof":
        value = {
            "provisional": records.SourceProof(
                sha256=digest,
                content_length=core.MAX_FILE,
            )
        }
        allowance = records.SOURCE_PROOF_METADATA_BYTES
    elif fragment == "effect":
        value = {token: {"status": "unknown", "handle": token}}
        allowance = records.EFFECT_METADATA_BYTES + len(core.canonical(token))
    else:
        value = {"head": digest}
        allowance = records.COUNTER_HEAD_METADATA_BYTES
    assert len(core.canonical(value)) <= allowance


@pytest.mark.parametrize("digits", [128, 1024])
def test_settlement_keeps_arbitrary_generation_and_counter_growth(digits: int) -> None:
    """Keep integer growth outside bounded metadata, even far beyond uint64 widths.

    Args:
        digits: Additional decimal positions in a valid retained framework counter.
    """
    state = reserve_state()
    state["participants"]["child"]["generation"] = 0
    state["cursor_invocations"]["i"]["resume_generation"] = 0
    state["revision"] = state["seq"] = 0
    baseline, slots = core.settlement_reserve(state)
    # A terminal-only result may add this generation to both invocation and participant.
    state["participants"]["child"]["generation"] = 10**digits
    generation_reserve, _ = core.settlement_reserve(state)
    assert generation_reserve - baseline >= 2 * digits
    state["revision"] = state["seq"] = 10**digits
    counter_reserve, _ = core.settlement_reserve(state)
    # Every settlement retains a request revision plus the shared revision/sequence head.
    assert counter_reserve - generation_reserve >= 3 * digits
    assert core.settlement_reserve(state, slots=slots) == (counter_reserve, slots)
