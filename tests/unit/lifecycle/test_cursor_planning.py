"""Verify Cursor invocation planning decisions without filesystem persistence."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from nplet.lifecycle import _invocations as invocations
from nplet.lifecycle import task_workspace as core
from tests.unit.lifecycle.cursor_support import (
    base_request,
    cursor_apply,
    cursor_denied,
    cursor_launch,
    cursor_model,
    cursor_plan_request,
    cursor_request,
    cursor_retirement_request,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fault,code",
    [
        ("host", "HOST_UNSUPPORTED"),
        ("unbound", "BINDING_MISSING"),
        ("generation", "STALE_BINDING"),
        ("role", "NOT_OWNER"),
        ("attached", "NOT_READY"),
        ("ack", "NOT_READY"),
        ("packet", "SCOPE_MISSING"),
        ("source", "SOURCE_STALE"),
        ("classified", "NOT_OWNER"),
    ],
)
def test_cursor_ready_denies_each_authority_or_source_failure(fault: str, code: str) -> None:
    """Reject parent authority and readiness failures without touching collaborators.

    Args:
        fault: One changed parent/source dimension.
        code: Exact expected diagnostic.

    Raises:
        AssertionError: Invalid authority is accepted or state is mutated.
    """
    # Select one invalid boundary from an independently ready parent.
    store, issue, key = cursor_model()
    request = cursor_request(issue, "cursor-plan")
    member = issue.state["participants"][key]
    if fault == "host":
        request["host"] = "codex"
    elif fault == "unbound":
        request["session_id"] = "foreign"
    elif fault == "generation":
        request["binding_generation"] = 1
    elif fault == "role":
        issue.state["coordinator"] = "other"
    elif fault == "attached":
        member["status"] = "attached"
    elif fault == "ack":
        member["ack"] = "0" * 64
    elif fault == "packet":
        member["packet"] = None
    elif fault == "source":
        member["packet"] = [
            {
                "id": "required",
                "locator": "roadmap.md",
                "sha256": "0" * 64,
                "required": True,
                "authority": "fixture",
                "reason": "test",
                "stage": "execution",
                "reader": key,
            }
        ]
    else:
        # A synthetic active classified coordinator reaches the explicit child-role fence.
        member["cursor_invocation"] = "classified"
        issue.state["cursor_invocations"] = {
            "classified": {
                "phase": "bound",
                "child_participant": key,
                "child_generation": 2,
            }
        }
    before = copy.deepcopy(issue.state)
    # The ready helper performs no staging or publication.
    with pytest.raises(core.WorkspaceError) as captured:
        invocations.ready(issue.state, request, issue.payload)
    assert captured.value.code == code
    assert issue.state == before
    assert issue.commits == []
    assert store.saved == []


def test_cursor_ready_returns_the_actual_ready_parent_record() -> None:
    """Return the existing coordinator identity and record without a modeled commit."""
    # Check identity, not a copied or fabricated readiness result.
    _store, issue, key = cursor_model()
    result = invocations.ready(issue.state, cursor_request(issue, "cursor-plan"), issue.payload)
    assert result[0] == key
    assert result[1] is issue.state["participants"][key]
    assert issue.commits == []


def test_cursor_plan_replacement_replay_and_note_reservations() -> None:
    """Replace unconsumed intent, preserve exact replay and refuse conflicting outputs."""
    # Initial planning reserves the output without installing participant ownership.
    store, issue, key = cursor_model()
    request = cursor_plan_request(issue)
    first = cursor_apply(store, issue, request)
    assert issue.state["cursor_plans"]["plan"]["parent"] == key
    assert "context/child.md" not in issue.state["owners"]
    cursor_denied(store, issue, cursor_plan_request(issue, "other"), "NOT_OWNER")
    # Seed only the exact replay receipt; MemoryIssue does not model production receipts.
    issue.state["requests"][core.sha(request["request_id"].encode())] = {
        "digest": core.sha(core.canonical(request)),
        "result": first,
    }
    commits = len(issue.commits)
    assert cursor_apply(store, issue, request) == first
    assert len(issue.commits) == commits
    # Replacement releases an unconsumed reservation and permits another explicit plan.
    replacement = cursor_plan_request(issue, owned_paths=[])
    cursor_apply(store, issue, replacement)
    cursor_apply(store, issue, cursor_plan_request(issue, "other"))
    assert issue.state["cursor_plans"]["plan"]["owned_paths"] == []
    assert issue.state["cursor_plans"]["other"]["owned_paths"] == ["context/child.md"]


@pytest.mark.parametrize("fault", ["consumed", "parent", "generation"])
def test_cursor_plan_replacement_preserves_existing_authority(fault: str) -> None:
    """Keep consumed intent immutable and recover unconsumed older-parent planning.

    Args:
        fault: Existing plan relationship made incompatible with replacement.
    """
    # Change the existing planned relationship before requesting an explicit replacement.
    store, issue, _key = cursor_model()
    cursor_apply(store, issue, cursor_plan_request(issue))
    plan = issue.state["cursor_plans"]["plan"]
    if fault == "consumed":
        plan["invocation_id"] = "already-consumed"
    elif fault == "parent":
        plan["parent"] = "other-parent"
    else:
        plan["parent_generation"] = 1
    replacement = cursor_plan_request(issue, owned_paths=[])
    if fault == "consumed":
        cursor_denied(store, issue, replacement, "REQUEST_CONFLICT")
    else:
        # Current ready coordinator recovery may replace unused older planning intent.
        cursor_apply(store, issue, replacement)
        plan = issue.state["cursor_plans"]["plan"]
        assert plan["parent"] == issue.state["coordinator"]
        assert plan["parent_generation"] == 2 and plan["owned_paths"] == []


@pytest.mark.parametrize("phase", ["unconsumed", "admitted"])
def test_cursor_plan_keeps_output_reserved_until_first_child_callback(phase: str) -> None:
    """Refuse another plan during both pre-admission and pre-first-callback intervals.

    Args:
        phase: Whether the existing plan is already consumed by an admitted Task.
    """
    # An admitted invocation still needs its output reservation before ownership is installed.
    store, issue, _key = cursor_model()
    if phase == "admitted":
        cursor_launch(store, issue)
    else:
        cursor_apply(store, issue, cursor_plan_request(issue))
    cursor_denied(store, issue, cursor_plan_request(issue, "other"), "NOT_OWNER")


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing-plan", "SCOPE_MISSING"),
        ("parent", "REQUEST_CONFLICT"),
        ("generation", "REQUEST_CONFLICT"),
        ("conversation", "REQUEST_CONFLICT"),
        ("task", "REQUEST_CONFLICT"),
        ("native", "INVALID_REQUEST"),
        ("tool-pending", "REQUEST_CONFLICT"),
        ("consumed", "REQUEST_CONFLICT"),
        ("same-native", "REQUEST_CONFLICT"),
        ("stale-revision", "REVISION_CONFLICT"),
    ],
)
def test_cursor_admit_denies_invalid_or_conflicting_launch(fault: str, code: str) -> None:
    """Consume only the exact planned foreground launch without losing prior charges.

    Args:
        fault: Single plan, native call or pending relationship changed.
        code: Exact expected diagnostic.
    """
    # Start from one unused validated plan and its independent parent identity.
    store, issue, key = cursor_model()
    cursor_apply(store, issue, cursor_plan_request(issue))
    request = cursor_request(
        issue,
        "cursor-admit",
        plan_id="plan",
        parent_conversation="parent-conversation",
        task_digest="a" * 64,
        tool_id="tool-plan",
        native_call="native\nplan",
    )
    plan = issue.state["cursor_plans"]["plan"]
    if fault == "missing-plan":
        request["plan_id"] = "missing"
    elif fault == "parent":
        plan["parent"] = "other"
    elif fault == "generation":
        plan["parent_generation"] = 1
    elif fault == "conversation":
        request["parent_conversation"] = "other"
    elif fault == "task":
        request["task_digest"] = "b" * 64
    elif fault == "native":
        request["native_call"] = ""
    elif fault == "tool-pending":
        issue.state["participants"][key]["pending"]["tool-plan"] = {"status": "pending"}
    elif fault == "consumed":
        plan["invocation_id"] = "other"
    elif fault == "same-native":
        issue.state["cursor_invocations"] = {
            "other": {
                "parent": key,
                "parent_generation": 2,
                "native_call": "native\nplan",
            }
        }
    else:
        request["expected_revision"] = 6
    cursor_denied(store, issue, request, code)


@pytest.mark.parametrize("receipt", [False, True])
def test_cursor_admit_semantic_and_exact_duplicates_are_event_free(receipt: bool) -> None:
    """Return an active admission without charging or committing it again.

    Args:
        receipt: Whether to seed an exact prior-result record.
    """
    # The production helper constructs the pending correlation and invocation once.
    store, issue, key = cursor_model()
    admitted, request = cursor_launch(store, issue)
    identifier = admitted["invocation_id"]
    assert issue.state["cursor_plans"]["plan"]["invocation_id"] == identifier
    assert (
        issue.state["participants"][key]["pending"]["tool-plan"]["cursor_invocation"] == identifier
    )
    if receipt:
        issue.state["requests"][core.sha(request["request_id"].encode())] = {
            "digest": core.sha(core.canonical(request)),
            "result": admitted,
        }
    before, commits = copy.deepcopy(issue.state), len(issue.commits)
    duplicate = cursor_apply(store, issue, request)
    assert duplicate["invocation_id"] == identifier
    if receipt:
        assert duplicate == admitted
    assert issue.state == before
    assert len(issue.commits) == commits


@pytest.mark.parametrize(
    "fault,code",
    [
        ("retired", "STALE_BINDING"),
        ("plan", "REQUEST_CONFLICT"),
        ("native", "REQUEST_CONFLICT"),
        ("pending", "REQUEST_CONFLICT"),
    ],
)
def test_cursor_admit_duplicate_revalidates_existing_invocation(fault: str, code: str) -> None:
    """Reject stale or contradictory native duplicates before treating them as retries.

    Args:
        fault: One relationship changed after original admission.
        code: Exact expected diagnostic.
    """
    # Keep the same normalized tool ID while changing only the selected correlation.
    store, issue, key = cursor_model()
    admitted, request = cursor_launch(store, issue)
    invocation = issue.state["cursor_invocations"][admitted["invocation_id"]]
    if fault == "retired":
        invocation["phase"] = "retired"
    elif fault == "plan":
        invocation["plan_id"] = "different"
    elif fault == "native":
        request["native_call"] = "contradictory\nnative"
    else:
        issue.state["participants"][key]["pending"] = {}
    cursor_denied(store, issue, request, code)


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("empty", False),
        ("unconsumed", True),
        ("admitted", True),
        ("bound", False),
        ("retired", False),
        ("excluded", False),
        ("nonmatching", False),
        ("later", True),
    ],
)
def test_cursor_output_reservation_uses_only_unconsumed_or_admitted_plans(
    scenario: str,
    expected: bool,
) -> None:
    """Retain pre-callback output reservation and release it once owners exist or work retires.

    Args:
        scenario: Explicit plan/invocation phase or exclusion relationship.
        expected: Independently selected reservation result.
    """
    # Construct only the reservation metadata consumed by this pure read-only predicate.
    state: dict[str, Any] = {}
    excluded = None
    if scenario != "empty":
        state["cursor_plans"] = {
            "plan": {"owned_paths": ["context/output.md"], "invocation_id": None},
        }
        if scenario in {"admitted", "bound", "retired"}:
            state["cursor_plans"]["plan"]["invocation_id"] = "invocation"
            state["cursor_invocations"] = {"invocation": {"phase": scenario}}
        elif scenario == "excluded":
            excluded = "plan"
        elif scenario == "nonmatching":
            state["cursor_plans"]["plan"]["owned_paths"] = ["context/other.md"]
        elif scenario == "later":
            state["cursor_plans"]["plan"]["owned_paths"] = []
            state["cursor_plans"]["later"] = {
                "owned_paths": ["context/output.md"],
                "invocation_id": None,
            }
    before = copy.deepcopy(state)
    assert invocations.path_reserved(state, "context/output.md", excluded) is expected
    assert state == before


def test_cursor_ready_tolerates_an_unavailable_optional_source() -> None:
    """Keep optional absence visible without blocking an acknowledged current parent packet."""
    # Use only an absent managed locator; no external file reader is involved.
    _store, issue, key = cursor_model()
    packet = [
        {
            "id": "optional",
            "locator": "context/absent.md",
            "sha256": "a" * 64,
            "required": False,
            "authority": "fixture",
            "reason": "test",
            "stage": "execution",
            "reader": key,
        }
    ]
    member = issue.state["participants"][key]
    member.update(packet=packet, ack=core.sha(core.canonical(packet)))
    result = invocations.ready(issue.state, cursor_request(issue, "cursor-plan"), issue.payload)
    assert result == (key, member)
    assert issue.commits == []


def test_cursor_plan_rejects_a_complete_template_for_a_foreign_reader() -> None:
    """Reject template broadening even when every reference field is otherwise valid."""
    # This reaches the template reader guard rather than validate_packet's shape guard.
    store, issue, _key = cursor_model()
    packet = [
        {
            "id": "source",
            "locator": "roadmap.md",
            "sha256": core.sha(b"approved roadmap"),
            "required": True,
            "authority": "fixture",
            "reason": "test",
            "stage": "execution",
            "reader": "foreign-reader",
        }
    ]
    cursor_denied(
        store,
        issue,
        cursor_plan_request(issue, packet=packet),
        "SCOPE_MISSING",
    )


@pytest.mark.parametrize(
    "consumed,code",
    [
        (False, "REQUEST_CONFLICT"),
        (True, "PENDING_OPERATION"),
    ],
)
def test_cursor_resume_conversation_is_reserved_before_and_after_admission(
    consumed: bool,
    code: str,
) -> None:
    """Reject a second known-conversation launch before it can create another charge.

    Args:
        consumed: Whether the first resume plan has already admitted a native Task.
        code: Exact planning conflict expected for that phase.
    """
    # Retain one classified conversation with no live child effects.
    store, issue, _key = cursor_model()
    first, _request = cursor_launch(store, issue)
    cursor_apply(store, issue, cursor_retirement_request(issue, first))
    second_plan = cursor_plan_request(
        issue,
        "second",
        resume_child="child",
        owned_paths=["context/second.md"],
    )
    cursor_apply(store, issue, second_plan)
    if consumed:
        cursor_apply(
            store,
            issue,
            cursor_request(
                issue,
                "cursor-admit",
                plan_id="second",
                parent_conversation="parent-conversation",
                task_digest="a" * 64,
                tool_id="tool-second",
                native_call="native\nsecond",
            ),
        )
    # Another disjoint output does not make reusing the same live conversation valid.
    cursor_denied(
        store,
        issue,
        cursor_plan_request(
            issue,
            "third",
            resume_child="child",
            owned_paths=["context/third.md"],
        ),
        code,
    )


@pytest.mark.parametrize(
    "fault,code",
    [
        ("generation", "STALE_BINDING"),
        ("missing", "STALE_BINDING"),
        ("attached", "STALE_BINDING"),
        ("pending", "STALE_BINDING"),
        ("active-invocation", "PENDING_OPERATION"),
    ],
)
def test_cursor_resume_admission_rechecks_the_planned_generation(fault: str, code: str) -> None:
    """Do not consume a plan after its inspected retired relationship changes.

    Args:
        fault: Relationship changed between plan and native admission.
        code: Exact expected diagnostic.
    """
    # Preserve an unconsumed resume plan while changing only its inspected prerequisite.
    store, issue, _key = cursor_model()
    first, _request = cursor_launch(store, issue)
    cursor_apply(store, issue, cursor_retirement_request(issue, first))
    cursor_apply(
        store,
        issue,
        cursor_plan_request(
            issue,
            "second",
            resume_child="child",
            owned_paths=["context/second.md"],
        ),
    )
    child = core.participant_key(
        base_request(
            "read",
            host="cursor",
            session_id="session/agent/child",
        )
    )
    member = issue.state["participants"][child]
    if fault == "generation":
        member["generation"] = 2
    elif fault == "missing":
        del issue.state["participants"][child]
    elif fault == "attached":
        member["status"] = "attached"
    elif fault == "pending":
        member["pending"]["effect"] = {"status": "pending"}
    else:
        issue.state["cursor_invocations"]["other"] = {
            "phase": "admitted",
            "child_conversation": "child",
            "resume_child": None,
            "parent": "other-parent",
            "parent_generation": 1,
            "native_call": "other-native",
        }
    request = cursor_request(
        issue,
        "cursor-admit",
        plan_id="second",
        parent_conversation="parent-conversation",
        task_digest="a" * 64,
        tool_id="tool-second",
        native_call="native\nsecond",
    )
    cursor_denied(store, issue, request, code)
