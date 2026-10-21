"""Recognize scoped child lifecycle commands and render their guidance."""

from __future__ import annotations

import uuid
from importlib import resources

from nplet.adapters import common
from nplet.lifecycle import task_workspace as core


def child_lifecycle(event: core.JSONObject) -> bool:
    """Recognize a child's exact own lifecycle request without a parent assignment alias.

    Args:
        event: Independently normalized current child Shell envelope.

    Returns:
        True for the supported canonical own read/acknowledge/ready/diagnose/update command.
        False only for a non-Shell tool; malformed child Shell never falls through.

    Raises:
        core.WorkspaceError: Command, current binding, worktree or owned-note scope is invalid.
    """
    if event.get("tool_name") != "Shell":
        return False
    # Only exact lifecycle commands own transactions; Shell return does not prove their outcome.
    core.require(event.get("child") is True, "NOT_OWNER")
    args = event.get("tool_input")
    core.require(isinstance(args, dict), "HOST_UNSUPPORTED_TOOL")
    assert isinstance(args, dict)
    common.cursor_lifecycle_shell(event, args)
    command = args.get("command")
    core.require(
        isinstance(command, str) and len(command.encode()) <= 65536, "HOST_UNSUPPORTED_TOOL"
    )
    assert isinstance(command, str)
    try:
        request = common.bootstrap_request(command, "cursor", common.PYTHON, common.LIFECYCLE)
    except ValueError as error:
        raise core.WorkspaceError("HOST_UNSUPPORTED_TOOL") from error
    # Validate against the actual committed child binding, not the parent's assignment file.
    bound = common.request_for(event, "read", "cursor")
    fields = {
        "read": set(),
        "acknowledge": {"packet_digest"},
        "ready": set(),
        "diagnose": set(),
        "update": {"path", "old_digest", "content", "provenance"},
    }
    operation = request.get("operation")
    core.require(isinstance(operation, str) and operation in fields, "HOST_UNSUPPORTED_TOOL")
    assert isinstance(operation, str)
    core.require(
        set(request) <= (common.COMMON_FIELDS - {"issue_uuid"}) | fields[operation],
        "HOST_UNSUPPORTED_TOOL",
    )
    core.require(
        request.get("schema_version") == 1
        and request.get("host") == "cursor"
        and request.get("session_id") == event["session_id"],
        "BINDING_CONFLICT",
    )
    core.require(
        request.get("repo_id") == bound["repo_id"]
        and request.get("issue_id") == bound["issue_id"]
        and type(request.get("binding_generation")) is int
        and request["binding_generation"] == bound["binding_generation"],
        "STALE_BINDING",
    )
    core.require(isinstance(request.get("worktree"), str), "REPOSITORY_MISMATCH")
    core.require(
        core.repository(request["worktree"])[0] == core.repository(event["cwd"])[0],
        "REPOSITORY_MISMATCH",
    )
    # Core enforces readiness, digest, provenance and invocation ownership atomically.
    if operation == "update":
        core.require(isinstance(request.get("path"), str), "INVALID_REQUEST")
        path = core.payload_path(request["path"])
        core.require(path.startswith("context/"), "NOT_OWNER")
    return True


def child_context(event: core.JSONObject) -> str:
    """Render canonical own requests with explicit result/assignment substitutions.

    Args:
        event: Independently bound current child envelope.

    Returns:
        Advisory templates carrying actual identity, without granting readiness or ownership.

    Raises:
        core.WorkspaceError: Current child binding cannot be recovered.
    """
    # Retain only the actual child's public lifecycle identity envelope.
    core.require(event.get("child") is True, "NOT_OWNER")
    bound = common.request_for(event, "read", "cursor")
    request = {
        key: value
        for key, value in bound.items()
        if key in common.COMMON_FIELDS and key not in {"issue_uuid", "expected_revision"}
    }
    # Render complete schemas; placeholders are replaced before invoking canonical Shell.
    templates: dict[str, core.JSONObject] = {
        "read": {},
        "acknowledge": {
            "expected_revision": "<LATEST_SUCCESSFUL_REVISION>",
            "packet_digest": "<RETURNED_PACKET_DIGEST>",
        },
        "ready": {},
        "diagnose": {},
        "update": {
            "expected_revision": "<LATEST_SUCCESSFUL_REVISION>",
            "path": "<EXACT_ASSIGNED_CONTEXT_NOTE_PATH>",
            "old_digest": None,
            "content": "<REQUESTED_NOTE_CONTENT>",
            "provenance": {
                "sources": [
                    {
                        "id": "<REFERENCE_ID>",
                        "locator": "<REFERENCE_LOCATOR>",
                        "sha256": "<REFERENCE_SHA256>",
                    }
                ],
                "applicability": "assigned-step",
                "status": "verified",
            },
        },
    }
    commands = []
    for operation, fields in templates.items():
        command = common.bootstrap_command(
            {**request, "operation": operation, "request_id": str(uuid.uuid4()), **fields},
            "cursor",
        )
        commands.append(operation + ":\n" + command)
    # Explain sequencing and recovery without restarting successful delivery after every denial.
    guidance = (
        resources.files("nplet")
        .joinpath("resources/task_workspace/cursor_child.md")
        .read_text(encoding="utf-8")
    )
    return guidance + "\n".join(commands)
