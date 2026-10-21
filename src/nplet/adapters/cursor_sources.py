"""Deliver and settle exact assigned source bytes for Cursor children."""

from __future__ import annotations

from pathlib import Path

from nplet.adapters import common, cursor_results
from nplet.lifecycle import task_workspace as core


def source_transition(
    event: core.JSONObject, phase: str, deadline: float, **metadata: object
) -> core.JSONObject:
    """Commit one exact Cursor source callback using a stable native request identity.

    Args:
        event: Independently normalized current child envelope.
        phase: start, content, success or failure selected by adapter code.
        deadline: Shared hook deadline.
        **metadata: Closed source metadata; never source text.

    Returns:
        The committed source transition result.

    Raises:
        core.WorkspaceError: Identity, callback, packet or bounded contention fails.
    """
    # Select the closed operation; do not grant authority from a caller's completed boolean.
    core.require(
        event.get("child") is True and phase in {"start", "content", "success", "failure"},
        "NOT_OWNER",
    )
    operation = "source-read-start" if phase == "start" else "cursor-source-callback"
    bound = common.request_for(event, operation, "cursor")
    request = {key: value for key, value in bound.items() if key in common.COMMON_FIELDS}
    request.update(metadata)
    request["tool_id"] = common.native_token(event.get("tool_use_id"))
    request["request_id"] = "cursor-source:" + core.sha(
        core.canonical(
            [request["session_id"], request["binding_generation"], request["tool_id"], phase]
        )
    )
    if phase != "start":
        request["source_event"] = phase
    # Preparation is public; only native content/completion uses the Python-only core entry.
    execute = core.execute if phase == "start" else core.execute_internal
    # Exact retries retain this same ID/body, including after a committed reply was lost.
    return common.retry_on_contention(
        lambda: execute(request), deadline, retry_codes=frozenset({"BUSY"})
    )


def source_read_pre(event: core.JSONObject, deadline: float) -> core.JSONObject:
    """Admit only a native Read of an exact current assigned source path.

    Args:
        event: Normalized child Read prehook; hook key is file_path, not model-facing path.
        deadline: Shared hook deadline.

    Returns:
        Committed source pending effect.

    Raises:
        core.WorkspaceError: Native shape or current assigned path does not match.
    """
    # Native hook input exposes only file_path; partial final content is caught at content hashing.
    core.require(
        event.get("child") is True and event.get("tool_name") == "Read", "HOST_UNSUPPORTED_TOOL"
    )
    args = event.get("tool_input")
    core.require(isinstance(args, dict) and set(args) == {"file_path"}, "HOST_UNSUPPORTED_TOOL")
    assert isinstance(args, dict)
    path = args["file_path"]
    core.require(isinstance(path, str) and Path(path).is_absolute(), "SCOPE_MISSING")
    request = common.request_for(event, "read", "cursor")
    read = core.execute(request)
    core.require(read["ok"], read["code"])
    root = core.repository(event["cwd"])[0]
    # Compare lexical native paths to explicit packet locators; never widen to directory scope.
    matches = []
    for ref in read["references"]:
        source = Path(ref["locator"])
        if not source.is_absolute():
            source = root / ".task" / request["issue_id"] / source
        if ref["available"] and str(source) == path:
            matches.append(ref)
    core.require(matches, "SCOPE_MISSING")
    return source_transition(
        event,
        "start",
        deadline,
        source_locator=matches[0]["locator"],
        packet_digest=read["packet_digest"],
    )


def source_read_content(event: core.JSONObject, deadline: float) -> core.JSONObject:
    """Hash actual beforeReadFile text provisionally without retaining source prose.

    Args:
        event: Actual independently normalized beforeReadFile callback.
        deadline: Shared hook deadline.

    Returns:
        Provisional content-verification result; no completed receipt exists yet.

    Raises:
        core.WorkspaceError: Identity, full UTF8content, path or pending source proof conflicts.
    """
    # These fields come from the pinned Read post-execution wrapper, not generic post output.
    core.require(event.get("child") is True, "NOT_OWNER")
    content = event.get("content")
    path = event.get("file_path")
    core.require(
        isinstance(content, str) and isinstance(path, str) and event.get("attachments") == [],
        "INVALID_REQUEST",
    )
    assert isinstance(content, str)
    # Strict UTF8 hash detects omitted/altered/truncated text; JS string.length uses UTF16units.
    try:
        encoded = content.encode("utf-8")
        length = len(content.encode("utf-16-le")) // 2
    except UnicodeEncodeError as error:
        raise core.WorkspaceError("SOURCE_DELIVERY_REQUIRED") from error
    core.require(len(encoded) <= core.MAX_FILE, "SIZE_LIMIT")
    digest = core.sha(encoded)
    cursor_results.check_deadline(deadline)
    return source_transition(
        event, "content", deadline, source_path=path, observed_sha256=digest, content_length=length
    )


def source_read_post(event: core.JSONObject, deadline: float, failed: bool) -> core.JSONObject:
    """Settle only the exact known finished Read; never credit generic completed=true.

    Args:
        event: Normalized child native Read success/failure callback.
        deadline: Shared hook deadline.
        failed: True only for the native postToolUseFailure event.

    Returns:
        Closed source callback result; success without provisional proof mints no receipt.

    Raises:
        core.WorkspaceError: Unknown/malformed completion or foreign path cannot be settled.
    """
    # Correlate the same tool and native input path before interpreting completion evidence.
    core.require(
        event.get("child") is True and event.get("tool_name") == "Read", "HOST_UNSUPPORTED_TOOL"
    )
    args = event.get("tool_input")
    core.require(
        isinstance(args, dict)
        and set(args) == {"file_path"}
        and isinstance(args.get("file_path"), str),
        "INVALID_REQUEST",
    )
    assert isinstance(args, dict)
    path = args["file_path"]
    # A recognized synchronous Read failure is known finished, with no successful-source credit.
    if failed:
        core.require(
            isinstance(event.get("error_message"), str)
            and event.get("failure_type") in {"error", "timeout", "permission_denied"}
            and type(event.get("is_interrupt", False)) is bool,
            "INVALID_REQUEST",
        )
        return source_transition(event, "failure", deadline, source_path=path)
    # Pinned generic success carries only path and UTF16length; content was verified separately.
    output = event.get("tool_output")
    core.require(isinstance(output, str), "INVALID_REQUEST")
    assert isinstance(output, str)
    parsed = core.strict_json(output)
    core.require(
        isinstance(parsed, dict) and set(parsed) == {"file_path", "content_length"},
        "INVALID_REQUEST",
    )
    assert isinstance(parsed, dict)
    core.require(
        parsed["file_path"] == path
        and type(parsed["content_length"]) is int
        and parsed["content_length"] >= 0,
        "INVALID_REQUEST",
    )
    cursor_results.check_deadline(deadline)
    return source_transition(
        event, "success", deadline, source_path=path, content_length=parsed["content_length"]
    )
