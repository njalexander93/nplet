"""Check provider-result and encoded-request parsing before lifecycle mutation."""

from __future__ import annotations

import base64
import json

import pytest

from nplet.adapters import bootstrap, startup
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def provider_response(issue: object) -> dict[str, object]:
    """Provider response.

    Args:
        issue: Issue fixture used by this case.

    Returns:
        A connector-style provider result envelope.
    """
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(issue)}]}


def test_ticket_accepts_exact_identifier_and_uuid() -> None:
    """Ticket accepts exact identifier and uuid."""
    issue = {"id": "TEST-30", "uuid": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977", "title": "Testing"}
    assert startup.ticket(provider_response(issue), "TEST-30") == issue


def test_ticket_normalizes_strict_graphql_identity_inside_mcp_envelope() -> None:
    """Accept a documented GraphQL identity object inside a standard MCP result."""
    issue = {
        "id": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977",
        "identifier": "TEST-30",
        "title": "Testing",
    }
    result = startup.ticket(provider_response(issue), "TEST-30")
    assert result["id"] == "TEST-30"
    assert result["uuid"] == issue["id"]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("NOT_FOUND", "ISSUE_NOT_FOUND"),
        ("ISSUE_NOT_FOUND", "ISSUE_NOT_FOUND"),
        ("UNAUTHORIZED", "PROVIDER_AUTH_REQUIRED"),
        ("FORBIDDEN", "PROVIDER_PERMISSION_DENIED"),
        ("NETWORK_ERROR", "PROVIDER_NETWORK_ERROR"),
        ("TIMEOUT", "PROVIDER_NETWORK_ERROR"),
        ("OTHER", "PROVIDER_ERROR"),
        (None, "PROVIDER_ERROR"),
    ],
)
def test_ticket_maps_typed_provider_failures_without_text_guessing(
    code: str | None, expected: str
) -> None:
    """Ticket maps typed provider failures without text guessing.

    Args:
        code: Diagnostic code selected for this case.
        expected: Expected outcome for this case.
    """
    # The provider's typed error must map to the expected ticket diagnostic.
    with pytest.raises(core.WorkspaceError) as captured:
        startup.ticket({"isError": True, "code": code, "message": "issue not found"}, "TEST-30")
    assert captured.value.code == expected


def missing_issue_envelope(**detail_changes: object) -> dict[str, object]:
    """Build the exact observed Linear missing-reference error envelope.

    Args:
        detail_changes: Mutations applied to the provider error detail.

    Returns:
        The observed connector error shape with requested detail changes.
    """
    detail = {
        "error": "invalid_request",
        "message": "Could not find referenced Issue.",
        "status": 400,
        "requestId": "provider-request-1",
        **detail_changes,
    }
    return {
        "isError": True,
        "structuredContent": {"error_code": "INVALID_ARGUMENT"},
        "content": [{"type": "text", "text": json.dumps(detail)}],
    }


def test_ticket_recognizes_only_exact_structured_linear_missing_issue() -> None:
    """Report the observed missing-reference error without treating arbitrary 400s as absence."""
    # The exact observed missing-issue envelope has a distinct recovery code.
    with pytest.raises(core.WorkspaceError) as captured:
        startup.ticket(missing_issue_envelope(), "TEST-30")
    assert captured.value.code == "LINEAR_ISSUE_UNRESOLVED"


def test_ticket_does_not_classify_nontext_linear_error_as_missing() -> None:
    """A structured invalid argument without the exact text detail stays a provider error."""
    response = {**missing_issue_envelope(), "content": [{"type": "json", "text": "{}"}]}
    # A nontext content block cannot establish the missing-issue detail.
    with pytest.raises(core.WorkspaceError) as captured:
        startup.ticket(response, "TEST-30")
    assert captured.value.code == "PROVIDER_ERROR"


@pytest.mark.parametrize(
    "response",
    [
        missing_issue_envelope(message="Invalid field."),
        missing_issue_envelope(status=401),
        missing_issue_envelope(requestId=""),
        {**missing_issue_envelope(), "structuredContent": {"error_code": "OTHER"}},
        {**missing_issue_envelope(), "content": [{"type": "text", "text": "not-json"}]},
        {**missing_issue_envelope(), "content": []},
    ],
)
def test_ticket_keeps_near_missing_issue_envelopes_as_provider_errors(
    response: dict[str, object],
) -> None:
    """Leave malformed and other provider errors outside the narrow absence code.

    Args:
        response: Provider or hook response supplied by this case.
    """
    # Reject malformed or unverified provider results before workspace creation.
    with pytest.raises(core.WorkspaceError) as captured:
        startup.ticket(response, "TEST-30")
    assert captured.value.code == "PROVIDER_ERROR"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"isError": False, "content": []}, "PROVIDER_RESPONSE_INVALID"),
        (
            {"isError": False, "content": [{"type": "json", "text": "{}"}]},
            "PROVIDER_RESPONSE_INVALID",
        ),
        (
            {"isError": False, "content": [{"type": "text", "text": "{"}]},
            "PROVIDER_RESPONSE_INVALID",
        ),
        (provider_response({"id": "TEST-31", "uuid": "uuid"}), "ISSUE_MISMATCH"),
        (provider_response({"id": "TEST-30", "uuid": 7}), "PROVIDER_RESPONSE_INVALID"),
        (provider_response({"id": "TEST-30", "uuid": "bad uuid"}), "PROVIDER_RESPONSE_INVALID"),
        (None, "PROVIDER_RESPONSE_INVALID"),
        ({"content": []}, "PROVIDER_RESPONSE_INVALID"),
    ],
)
def test_ticket_rejects_malformed_or_different_issue(response: object, expected: str) -> None:
    """Ticket rejects malformed or different issue.

    Args:
        response: Provider or hook response supplied by this case.
        expected: Expected outcome for this case.
    """
    # Match each provider failure to its bounded recovery category.
    with pytest.raises(core.WorkspaceError) as captured:
        startup.ticket(response, "TEST-30")
    assert captured.value.code == expected


def test_startup_call_preserves_core_failure_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup call preserves core failure code.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    seen: list[dict[str, object]] = []

    def fake_execute(request: dict[str, object]) -> dict[str, object]:
        """Fake execute.

        Args:
            request: Pytest fixture selecting the parameterized case.

        Returns:
            The simulated lifecycle operation result.
        """
        seen.append(request)
        return {"ok": False, "code": "REVISION_CONFLICT"}

    monkeypatch.setattr(startup.core, "execute", fake_execute)
    # A lifecycle rejection propagates its diagnostic through the adapter boundary.
    with pytest.raises(core.WorkspaceError) as captured:
        startup._call({"operation": "read"})
    assert captured.value.code == "REVISION_CONFLICT"
    assert seen[0]["operation"] == "read"
    assert seen[0]["schema_version"] == 1
    assert isinstance(seen[0]["request_id"], str)


def test_startup_call_returns_success_result_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The startup adapter retains core revision and binding identity on success.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
    """
    expected = {"ok": True, "code": "REGISTERED", "repo_id": "repo", "revision": 3}
    monkeypatch.setattr(startup.core, "execute", lambda _request: expected)
    assert startup._call({"operation": "diagnose"}) is expected


def encoded(value: object) -> str:
    """Encoded.

    Args:
        value: Boundary input selected for this case.

    Returns:
        A base64-encoded lifecycle request fixture.
    """
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def test_decode_request_preserves_exact_object() -> None:
    """Decode request preserves exact object."""
    request = {"operation": "diagnose", "request_id": "one", "nested": {"ready": True}}
    assert bootstrap.decode_request(encoded(request)) == request


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("?", "INVALID_REQUEST"),
        ("e30", "INVALID_REQUEST"),
        (encoded([1]), "INVALID_REQUEST"),
        (base64.urlsafe_b64encode(b'{"a":1,"a":2}').decode(), "INVALID_REQUEST"),
        ("A" * 65537, "SIZE_LIMIT"),
    ],
    ids=["invalid-base64", "invalid-padding", "nonobject", "duplicate-key", "oversized"],
)
def test_decode_request_rejects_invalid_or_ambiguous_input(value: str, code: str) -> None:
    """Decode request rejects invalid or ambiguous input.

    Args:
        value: Boundary input selected for this case.
        code: Diagnostic code selected for this case.
    """
    # Malformed bootstrap requests must fail during decoding.
    with pytest.raises(core.WorkspaceError) as captured:
        bootstrap.decode_request(value)
    assert captured.value.code == code


def test_bootstrap_main_reports_core_failure_without_exception_trace(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bootstrap main reports core failure without exception trace.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        capsys: Pytest fixture capturing process output.
    """
    monkeypatch.setattr(
        bootstrap.sys, "argv", ["bootstrap", "--request-base64", encoded({"operation": "diagnose"})]
    )
    monkeypatch.setattr(bootstrap.core, "execute", lambda _request: {"ok": False, "code": "ABSENT"})
    assert bootstrap.main() == 3
    assert json.loads(capsys.readouterr().out) == {"ok": False, "code": "ABSENT"}


def test_bootstrap_main_rejects_unexpected_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bootstrap main rejects unexpected arguments.

    Args:
        monkeypatch: Pytest fixture that isolates external state for this case.
        capsys: Pytest fixture capturing process output.
    """
    monkeypatch.setattr(bootstrap.sys, "argv", ["bootstrap", "--other", encoded({})])
    assert bootstrap.main() == 3
    assert json.loads(capsys.readouterr().out) == {"ok": False, "code": "INVALID_REQUEST"}
