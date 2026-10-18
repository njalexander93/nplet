"""Share host-bound assignment and exact bootstrap checks across native adapters."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from nplet.adapters import bootstrap as encoded_bootstrap
from nplet.adapters import runner, task_boundary
from nplet.lifecycle import task_workspace as core

ROOT = Path(__file__).resolve().parents[3]

BOOTSTRAP_FIELDS = {
    "diagnose": set(),
    "register": {"main_worktree", "startup"},
    "bind": set(),
    "scope": {"target_participant", "packet"},
    "adopt": {"coordinator", "inventory", "owners", "evidence"},
    "resume": set(),
    "restore": {"observations"},
    "read": set(),
    "acknowledge": {"packet_digest"},
    "archive-index": {"observations"},
    "archive-verify": {"observations"},
    "archive-observe-save": {"document_id", "content_digest"},
    "cleanup-plan": set(),
    "cleanup-commit": {"observations", "cleanup_challenge"},
    "reopen": {"evidence"},
    "archive-prepare": {"seal"},
    "event-rollover": set(),
    "reconcile-files": {"inventory", "evidence"},
    "rebind": {"new_issue_id", "new_binding_generation", "evidence"},
}
COMMON_FIELDS = {
    "schema_version",
    "operation",
    "request_id",
    "worktree",
    "host",
    "session_id",
    "repo_id",
    "issue_id",
    "issue_uuid",
    "binding_generation",
    "expected_revision",
}
LIFECYCLE = ROOT / "src/nplet/lifecycle/task_workspace.py"
WINDOWS_BOOTSTRAP = ROOT / "src/nplet/adapters/bootstrap.py"
PYTHON = str(ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def request_for(event: core.JSONObject, operation: str, host: str) -> core.JSONObject:
    """Resolve an actual hook session to its registered repository and binding.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        host: Explicit adapter identity; never taken from untrusted tool arguments.
        operation: Core operation requested for the observed session.

    Returns:
        A versioned core request carrying the observed session identity.

    Raises:
        core.WorkspaceError: If registration or binding validation fails.
        OSError: If the local store cannot be read.
    """
    # Build the request from explicit caller or observed session identities.
    request = {
        "schema_version": 1,
        "operation": operation,
        "request_id": str(uuid.uuid4()),
        "worktree": event["cwd"],
        "host": host,
        "session_id": event["session_id"],
    }
    diagnosis = core.execute({**request, "operation": "diagnose"})
    # Stop on the exact repository diagnostic before opening a bound session.
    if not diagnosis["ok"] or diagnosis["code"] != "REGISTERED":
        raise core.WorkspaceError(diagnosis["code"])
    # Carry the verified repository identity into the lifecycle request.
    request["repo_id"] = diagnosis["repo_id"]
    # Open the verified store only after registration succeeds.
    with core.Store(request) as store:
        # Read the existing session binding; never infer it from the prompt.
        binding = store.binding()
        # Leave an absent assignment unbound rather than inventing identity.
        if not binding:
            raise core.WorkspaceError("BINDING_MISSING")
        # Copy only the recorded issue and generation into the request.
        request.update(binding)
    return request


def automatic_attach(event: core.JSONObject, identifier: str, host: str) -> None:
    """Apply only an explicit startup assignment or an existing participant packet.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        host: Explicit adapter identity; never taken from untrusted tool arguments.
        identifier: Validated issue ID or explicit tool-call identity.

    Raises:
        core.WorkspaceError: If registration, assignment or lifecycle setup fails.
        OSError: If assignment or issue state cannot be accessed.
    """
    # Build the request from explicit caller or observed session identities.
    base = {
        "schema_version": 1,
        "request_id": "startup:" + event["session_id"],
        "operation": "diagnose",
        "worktree": event["cwd"],
        "host": host,
        "session_id": event["session_id"],
    }
    diagnosis = core.execute(base)
    # Only a registered checkout can carry a startup assignment.
    if diagnosis.get("code") != "REGISTERED":
        return
    base.update(repo_id=diagnosis["repo_id"], issue_id=identifier)
    # Inspect the persisted binding and startup packet under the registered store.
    with core.Store(base) as store:
        # Read the existing session binding; never infer it from the prompt.
        binding = store.binding()
        # An existing binding must match the submitted Task identity.
        if binding:
            core.require(binding["issue_id"] == identifier, "BINDING_CONFLICT")
        key = core.participant_key(base)
        filename = key + ".startup.json"
        setup = store.bindings.json(filename) if store.bindings.exists(filename) else None
        # A bound participant may already have a committed packet.
        if binding:
            # Read issue state under its lock before deciding whether setup is needed.
            with store.issues.child(identifier) as control, control.lock():
                issue = core.Issue(store, control, identifier)
                issue.recover()
                member: Mapping[str, object] = issue.committed_state()["participants"].get(key, {})
                # Use only the assigned packet and its current acknowledgment.
                if member.get("packet") is not None:
                    return
    # Create only the explicitly assigned startup issue before applying its packet.
    if setup:
        core.require(setup["issue_id"] == identifier, "BINDING_CONFLICT")
        result = core.execute(
            {
                **base,
                "operation": "create",
                "coordinator": setup["coordinator"],
                "issue_uuid": setup["issue_uuid"],
            }
        )
        core.require(result["ok"], result["code"])
        # A previous scope failure may follow successful creation. Read current revision
        # and retry only the still-missing assignment, never replace a later packet.
        diagnostic = core.execute(
            {**base, "operation": "diagnose", "request_id": base["request_id"] + ":diagnose"}
        )
        result = core.execute(
            {
                **base,
                "operation": "scope",
                "request_id": base["request_id"] + ":scope",
                "binding_generation": result["binding_generation"],
                "expected_revision": diagnostic["revision"],
                "target_participant": key,
                "packet": setup["packet"],
            }
        )
        core.require(result["ok"], result["code"])
    # Leave an absent assignment unbound rather than inventing identity.
    elif not binding:
        # Existing coordinator assignment in shared state is required for a new join.
        result = core.execute({**base, "operation": "attach"})
        # Preserve lifecycle failures other than the documented unassigned states.
        if not result["ok"] and result["code"] not in {"BINDING_MISSING", "SCOPE_MISSING"}:
            raise core.WorkspaceError(result["code"])


def prompt(event: core.JSONObject, host: str) -> core.JSONObject:
    """Record one explicit Task line and leave setup to the verified ticket read.

    On a ticket-first host, a bound session's Task line for another issue records that
    issue as a pending assignment; the binding moves only when the verified ticket read
    runs the startup rebind, so a failed read leaves the old binding in place.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        host: Explicit adapter identity; never taken from untrusted tool arguments.

    Returns:
        An empty response, prompt denial or bounded setup context.

    Raises:
        core.WorkspaceError: If the checkout or binding directory is invalid.
        OSError: If assignment persistence fails.
    """
    # Extract only explicit Task lines from the submitted prompt.
    lines = [line for line in event.get("prompt", "").splitlines() if line.startswith("Task:")]
    # Leave ordinary prompts unchanged when no task identity was supplied.
    if not lines:
        return {}
    # Reject multiple or malformed Task lines before recording an assignment.
    if len(lines) != 1 or not re.fullmatch(r"Task: [A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,9}", lines[0]):
        return {
            "decision": "block",
            "reason": "BINDING_CONFLICT: Supply exactly one Task: ISSUE-ID line.",
        }
    # Build the request from explicit caller or observed session identities.
    identifier = lines[0][6:]
    request = {"host": host, "session_id": event["session_id"]}
    root, _, _ = core.repository(event["cwd"])
    # Lock assignment storage before recording or comparing Task identity.
    with (
        core.Directory.absolute(root) as worktree,
        worktree.child(".task", True) as local,
        local.child(".bindings", True) as bindings,
        bindings.lock("assignment.lock"),
    ):
        # Derive the assignment key from the explicit host/session identity.
        key = core.participant_key(request)
        # Read the live binding and the recorded Task identity, either possibly absent.
        binding = bindings.json(key + ".json") if bindings.exists(key + ".json") else None
        recorded = (
            bindings.json(key + ".assignment.json")
            if bindings.exists(key + ".assignment.json")
            else None
        )
        # An unbound session never replaces its recorded Task identity.
        if binding is None and recorded is not None and recorded["issue_id"] != identifier:
            return {
                "decision": "block",
                "reason": "BINDING_CONFLICT: Explicit rebind is required.",
            }
        # A bound session's Task line replaces only its pending assignment: the binding
        # moves after the verified ticket read, so drop a lookup correlation for another
        # issue that could otherwise refuse this issue's read.
        lookup = key + ".lookup.json"
        if (
            binding is not None
            and bindings.exists(lookup)
            and bindings.json(lookup).get("id") != identifier
        ):
            bindings.unlink(lookup)
        bindings.put(key + ".assignment.json", {"issue_id": identifier})
        # Name the bound issue this Task line moves away from, for the setup context.
        moving = (
            binding["issue_id"]
            if binding is not None and binding["issue_id"] != identifier
            else None
        )
        # Every host retains a marker until the provider read completes.
        bindings.put(key + ".lookup-required.json", {"issue_id": identifier})
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "Task identity recorded. Read the requested Linear ticket first. "
                "Then complete scoped workspace setup and verify readiness."
                + (
                    f" The verified read rebinds this session from {moving}, which stays "
                    f"preserved; a new Task: {moving} line returns to it."
                    if moving is not None
                    else ""
                )
            ),
        }
    }


def lookup_required(event: core.JSONObject, host: str) -> bool:
    """Check whether this exact native session awaits its first Linear read.

    Args:
        event: Identity-validated host event.
        host: Native host identity.

    Returns:
        Whether an exact ticket lookup marker exists for the session.

    Raises:
        core.WorkspaceError: If the selected checkout or binding path is invalid.
        OSError: If the task store cannot be read.
    """
    # Synthetic contexts without a native identity have no session assignment.
    if not isinstance(event.get("cwd"), str) or not isinstance(event.get("session_id"), str):
        return False
    # Resolve the selected checkout before looking for a local assignment.
    root, _, _ = core.repository(event["cwd"])
    key = core.participant_key({"host": host, "session_id": event["session_id"]})
    # Inspect the root store without creating a ticket or binding.
    with core.Directory.absolute(root) as directory:
        # A missing store means no ticket-first assignment is active.
        if not directory.exists(".task"):
            return False
        # An uninitialized binding store cannot contain a pending ticket.
        with directory.child(".task") as task:
            # Stop before opening an absent bindings directory.
            if not task.exists(".bindings"):
                return False
            # Read only the marker owned by this native session.
            with task.child(".bindings") as bindings:
                return bindings.exists(key + ".lookup-required.json")


def selected_lookup(event: core.JSONObject, host: str, complete: bool = False) -> bool:
    """Select only the exact assigned startup read or its reserved completion.

    Args:
        event: Validated native call.
        host: Actual adapter host.
        complete: Require the recorded native call ID for completion.

    Returns:
        Whether this invocation belongs to the reserved startup route.

    Raises:
        core.WorkspaceError: If existing assignment storage is invalid.
        OSError: If existing storage cannot be inspected.
    """
    # No assignment means no reserved startup call.
    if not lookup_required(event, host):
        return False
    root = core.repository(event["cwd"])[0]
    key = core.participant_key({"host": host, "session_id": event["session_id"]})
    # Read the actual assignment without creating any directories or locks.
    with (
        core.Directory.absolute(root) as directory,
        directory.child(".task") as task,
        task.child(".bindings") as bindings,
    ):
        assignment = bindings.json(key + ".assignment.json")
        core.require(bindings.json(key + ".lookup-required.json") == assignment, "BINDING_CONFLICT")
        args = event.get("tool_input")
        # Cursor's specific callback supplies serialized provider arguments.
        if host == "cursor" and event.get("hook_event_name") == "beforeMCPExecution":
            args = core.strict_json(args) if isinstance(args, str) else None
        if args != {"id": assignment["issue_id"]}:
            return False
        # Only the original native call may consume a reserved provider response.
        if complete:
            name = key + ".lookup.json"
            return bindings.exists(name) and bindings.json(name).get("tool_id") == event.get(
                "tool_use_id"
            )
        return True


def existing_admission(event: core.JSONObject, host: str) -> core.JSONObject | None:
    """Probe exact existing pending work without creating task, binding or lock storage.

    Args:
        event: Identity-validated completion with its native call ID.
        host: Actual adapter identity.

    Returns:
        Existing bound request for exact pending or replayed admission, or a local poll handle.
        None when no admission exists.

    Raises:
        core.WorkspaceError: If registered state is unsafe, inconsistent or transactional.
        OSError: If present state cannot be inspected.
    """
    # Resolve native identity before examining any local state.
    tool_id = native_token(event.get("tool_use_id"))
    root, common, trees = core.repository(event["cwd"])
    key = core.participant_key({"host": host, "session_id": event["session_id"]})
    request = {
        "schema_version": 1,
        "operation": "tool-complete",
        "request_id": str(uuid.uuid4()),
        "worktree": event["cwd"],
        "host": host,
        "session_id": event["session_id"],
    }
    # All opened directories already exist; this probe never uses Store or a lock creator.
    with core.Directory.absolute(root) as directory:
        if not directory.exists(".task"):
            return None
        with directory.child(".task") as task:
            if not task.exists(".repository.json"):
                # Assignment-only startup is valid; durable state requires registration.
                core.require(not task.exists(".control"), "RECOVERY_REQUIRED")
                if task.exists(".bindings"):
                    with task.child(".bindings") as bindings:
                        core.require(not bindings.exists(key + ".json"), "RECOVERY_REQUIRED")
                return None
            registration = task.json(".repository.json")
            core.require(
                str(common) == registration["common"]
                and core.fs_identity(common) == registration["common_identity"]
                and Path(registration["main"]) == trees[0],
                "REPOSITORY_MISMATCH",
            )
            if not task.exists(".bindings"):
                return None
            with task.child(".bindings") as bindings:
                if not bindings.exists(key + ".json"):
                    return None
                request.update(bindings.json(key + ".json"))
        # Validate the shared registration before reading its committed issue state.
        request["repo_id"] = registration["repo_id"]
        with (
            core.Directory.absolute(registration["main"]) as main,
            main.child(".task") as task,
            task.child(".control") as control,
        ):
            core.require(
                main.identity == registration["main_identity"]
                and control.json("repository.json") == registration,
                "REPOSITORY_MISMATCH",
            )
            with (
                control.child("issues") as issues,
                issues.child(core.issue_id(request["issue_id"])) as issue,
            ):
                core.require(not issue.exists("transaction.json"), "RECOVERY_REQUIRED")
                state = issue.json("state.json")
                core.authorize(state, request, maintenance=True)
                pending = state["participants"][key]["pending"]
                if tool_id in pending:
                    return request
                # A matching durable admission permits exact completion replay after settlement.
                admission = {
                    **request,
                    "operation": "tool-start",
                    "request_id": "pre:" + tool_id,
                    "tool_id": tool_id,
                }
                if host == "codex":
                    admission["poll_handle"] = (
                        str(event.get("tool_input", {}).get("session_id"))
                        if event.get("tool_name") == "write_stdin"
                        else None
                    )
                receipt = state["requests"].get(core.sha(admission["request_id"].encode()))
                if receipt is not None and receipt["digest"] == core.sha(core.canonical(admission)):
                    return request
                # Legacy polls without a prehook may select only one participant-local handle.
                if event.get("tool_name") == "write_stdin":
                    handle = event.get("tool_input", {}).get("session_id")
                    core.require(type(handle) in {str, int}, "ASYNC_HANDLE_CONFLICT")
                    matches = [p for p in pending.values() if p.get("handle") == str(handle)]
                    core.require(len(matches) == 1, "UNKNOWN_OPERATION")
                    return request
    return None


def _claude_linear_providers() -> dict[str, set[str]]:
    """Map each configured Claude Linear server name to its accepted provenance sources.

    Returns:
        Named local servers plus the operator-mapped Desktop connector UUID, when valid.
    """
    # Named local servers come from user, project, plugin or SDK definitions.
    providers = {
        "linear": {"user", "project", "plugin", "sdk"},
        "linear-server": {"user", "project", "plugin", "sdk"},
    }
    # Bind opaque Desktop names only through explicit, operator-verified configuration.
    connector = os.environ.get("NPLET_CLAUDE_LINEAR_CONNECTOR_ID", "")
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", connector):
        providers[connector] = {"claudeai", "dynamic", "sdk"}
    return providers


def _linear_tool(event: core.JSONObject, host: str) -> tuple[str, core.JSONObject] | None:
    """Validate one host's documented Linear MCP name and argument shape.

    Args:
        event: Native tool event with host-specific fields.
        host: Either Claude Code or Cursor.

    Returns:
        A correlation token and exact input, or None for another tool.

    Raises:
        core.WorkspaceError: If a candidate tool has malformed input or provenance.
    """
    # Claude reports an MCP-qualified name and a server provenance object.
    if host == "claude-code":
        server = event.get("mcp_server")
        name = event.get("tool_name")
        providers = _claude_linear_providers()
        # Ignore other operations and unconfigured connectors; no UUID-wide exception.
        if name not in {f"mcp__{provider}__get_issue" for provider in providers}:
            return None
        core.require(
            isinstance(server, dict)
            and isinstance(server.get("name"), str)
            and server["name"] in providers
            and server.get("source") in providers[server["name"]]
            and name == "mcp__" + server["name"] + "__get_issue",
            "HOST_UNSUPPORTED_PROVIDER",
        )
        arguments = event.get("tool_input")
        token = native_token(event.get("tool_use_id"))
    else:
        # Cursor's MCP-specific callbacks identify the configured server and carry JSON strings.
        # Ignore unrelated Cursor MCP tools before validating server identity.
        if event.get("tool_name") != "get_issue":
            return None
        core.require(
            event.get("mcp_server_name") in {"linear", "linear-server"}, "HOST_UNSUPPORTED_PROVIDER"
        )
        url = event.get("mcp_server_url", event.get("url"))
        core.require(
            url in {"https://mcp.linear.app/mcp", "https://mcp.linear.app/mcp/readonly"},
            "HOST_UNSUPPORTED_PROVIDER",
        )
        raw = event.get("tool_input")
        # Decode the documented JSON argument string, never a shell command.
        if not isinstance(raw, str):
            raise core.WorkspaceError("INVALID_REQUEST")
        arguments = core.strict_json(raw)
        token = "cursor-mcp-server-checked"
    # The requested issue identifier must come from an object argument.
    if not isinstance(arguments, dict):
        raise core.WorkspaceError("INVALID_REQUEST")
    return token, arguments


LINEAR_PROVIDER_OPERATIONS = task_boundary.LINEAR_OPERATIONS
# Freeze the original child read authority independently of parent additions.
LINEAR_CHILD_OPERATIONS = frozenset(
    {"get_issue", "get_user", "list_users", "list_issue_statuses", "list_comments"}
)
PREPARATION_FILES = ("docs/runtime/contributor-workflow.md", "AGENTS.md")
# The Claude desktop app's built-in PR-monitor server: a parent may read a PR's state
# and turn on the app's monitor. Auto-merge, PR binding and every other app server
# (session management, settings and the rest) stay denied, and a child gets none.
APP_PR_SERVER = "ccd_pr"
APP_PR_OPERATIONS = frozenset({"get_status", "set_monitor"})
# The desktop app reports its built-in server with this provenance source; captured
# from a live PreToolUse event on 2026-10-11 (macOS Desktop) and pinned like the
# configured Linear connector's sources.
APP_PR_SOURCE = "sdk"


def _linear_provider_tool(event: core.JSONObject, host: str) -> str | None:
    """Identify one post-readiness Linear operation on a configured Claude connector.

    Field-level checks, such as the assignee or state named by ``save_issue``,
    stay in the contributor procedure's read-back rule; this check covers only
    the server, its provenance and the operation name.

    Args:
        event: Native tool event carrying the MCP-qualified name and server provenance.
        host: Native adapter identity; only Claude Code has this route.

    Returns:
        The admitted operation name, or None for any other host, server or operation.
    """
    # Cursor and other hosts keep their provider calls outside this route.
    if host != "claude-code":
        return None
    # Read the observed server and name alongside the configured provider map.
    server = event.get("mcp_server")
    name = event.get("tool_name")
    providers = _claude_linear_providers()
    # Require the same configured server name and provenance as the ticket read.
    if not (
        isinstance(server, dict)
        and isinstance(server.get("name"), str)
        and server["name"] in providers
        and server.get("source") in providers[server["name"]]
        and isinstance(name, str)
    ):
        return None
    # Admit only the explicit operation allowlist on that exact server.
    prefix = "mcp__" + server["name"] + "__"
    operation = name[len(prefix) :] if name.startswith(prefix) else None
    return operation if operation in LINEAR_PROVIDER_OPERATIONS else None


def _app_pr_monitor_tool(event: core.JSONObject, host: str) -> str | None:
    """Identify one call to the Claude desktop app's built-in PR-monitor tools.

    The server is built into the desktop app rather than configured by an operator,
    so its identity is pinned to the provenance the app itself reports: the hook's
    ``mcp_server`` object must name this server with the captured ``source``. A call
    without that object, or with any other name or source, is not this server.

    Args:
        event: Native tool event carrying the MCP-qualified name and server object.
        host: Native adapter identity; only Claude Code has this route.

    Returns:
        The admitted operation, or None for any other host, server, operation or identity.
    """
    # Cursor and other hosts have no desktop-app PR tools.
    if host != "claude-code":
        return None
    # Only the exact qualified names of the two admitted operations qualify.
    name = event.get("tool_name")
    prefix = "mcp__" + APP_PR_SERVER + "__"
    operation = name[len(prefix) :] if isinstance(name, str) and name.startswith(prefix) else None
    if operation not in APP_PR_OPERATIONS:
        return None
    # The server object must carry the app's own name and captured provenance source.
    server = event.get("mcp_server")
    if not (
        isinstance(server, dict)
        and server.get("name") == APP_PR_SERVER
        and server.get("source") == APP_PR_SOURCE
    ):
        return None
    return operation


def _preparation_path(event: core.JSONObject, value: object, exact: bool = False) -> bool:
    """Check that a path names one governing preparation file in the selected checkout.

    Args:
        event: Identity-validated native event whose ``cwd`` selects the checkout.
        value: Observed absolute path argument, never resolved through links.
        exact: Require the literal path spelling, as for a shell argument.

    Returns:
        Whether the path is exactly one preparation file and no traversed entry is a link.
    """
    # Require an absolute textual path before consulting the checkout.
    if not isinstance(value, str) or not Path(value).is_absolute():
        return False
    # Resolve the selected checkout root from the observed working directory.
    root, _, _ = core.repository(event["cwd"])
    # Compare lexically so neither ".." nor a symbolic link can redirect the read.
    for relative in PREPARATION_FILES:
        expected = root / relative
        # Accept only the exact path whose checkout-relative entries are not links.
        if (value == str(expected)) if exact else (Path(value) == expected):
            parts = Path(relative).parts
            entries = [root.joinpath(*parts[: index + 1]) for index in range(len(parts))]
            return not any(entry.is_symlink() or entry.is_junction() for entry in entries)
    return False


def preparation_tool(event: core.JSONObject, host: str) -> bool:
    """Recognize one read-only preparation call allowed before the selected ticket read.

    The call receives no lifecycle decision and writes nothing: the lookup marker
    stays in place, so the exact ``get_issue`` remains the first issue-provider
    operation. Claude's normal permission flow still applies to the call.

    Args:
        event: Identity-validated native tool event.
        host: Native adapter identity; Codex supports exact governing shell reads.

    Returns:
        True for a schema load of configured ``get_issue`` tools, or a read of
        ``docs/runtime/contributor-workflow.md`` or ``AGENTS.md`` in this checkout.
    """
    # Codex exposes command-only native Bash input; admit exact governing reads only.
    if host == "codex":
        args = event.get("tool_input")
        if (
            event.get("tool_name") != "Bash"
            or not isinstance(args, dict)
            or set(args) != {"command"}
        ):
            return False
        command = args["command"]
        if not isinstance(command, str) or any(c in command for c in "\r\n\0"):
            return False
        # Require canonical literal quoting, excluding redirects, expansion and chaining.
        try:
            argv = shlex.split(command)
            if len(argv) == 3 and argv[:2] == ["cat", "--"]:
                path = argv[2]
                return command == "cat -- " + shlex.quote(path) and _preparation_path(
                    event, path, exact=True
                )
            if len(argv) == 2 and argv[0] == "cat":
                path = argv[1]
                return command == "cat " + shlex.quote(path) and _preparation_path(
                    event, path, exact=True
                )
        except (ValueError, OSError, core.WorkspaceError):
            return False
        return False
    # Cursor's ticket-first route is unchanged.
    if host != "claude-code":
        return False
    # Read the native tool name and input without trusting either.
    tool = event.get("tool_name")
    args = event.get("tool_input")
    # Every preparation tool takes a structured native input.
    if not isinstance(args, dict):
        return False
    # Contain malformed values and checkout errors as an ordinary denial.
    try:
        # A schema load may select only configured get_issue tools.
        if tool == "ToolSearch":
            query = args.get("query")
            limit = args.get("max_results", 1)
            # Reject undocumented fields, oversized queries and invalid limits.
            if (
                not set(args) <= {"query", "max_results"}
                or not isinstance(query, str)
                or len(query) > 256
                or type(limit) is not int
                or not 1 <= limit <= 20
            ):
                return False
            # An exact selection must name only configured issue-read tools.
            if query.startswith("select:"):
                names = [name.strip() for name in query[len("select:") :].split(",")]
                allowed = {f"mcp__{provider}__get_issue" for provider in _claude_linear_providers()}
                return bool(names) and all(name in allowed for name in names)
            # A bounded keyword search must name the issue-read operation.
            return (
                "get_issue" in query and re.fullmatch(r"[A-Za-z0-9_+\- ]{1,128}", query) is not None
            )
        # A file read must target one governing file exactly.
        if tool == "Read":
            return set(args) <= {"file_path", "offset", "limit"} and _preparation_path(
                event, args.get("file_path")
            )
        # A shell read must be exactly cat with one governing file argument.
        if tool == "Bash":
            command = args.get("command")
            # Reject undocumented fields, background execution and control characters.
            if (
                not set(args) <= {"command", "description", "timeout", "run_in_background"}
                or args.get("run_in_background", False) is not False
                or not isinstance(command, str)
                or any(character in command for character in "\r\n\0")
            ):
                return False
            # Parse without evaluating; only cat plus one literal governing path qualifies.
            argv = shlex.split(command)
            return (
                len(argv) == 2
                and argv[0] == "cat"
                and _preparation_path(event, argv[1], exact=True)
            )
    # Unparseable commands and unreadable checkouts are not preparation reads.
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        return False
    # Every other tool waits for the ticket read.
    return False


def native_ticket_lookup(
    event: core.JSONObject, host: str, complete: bool = False, specific: bool = False
) -> core.JSONObject | None:
    """Admit and settle the first exact Linear read before lifecycle readiness.

    Args:
        event: Identity-validated Claude tool or Cursor MCP callback.
        host: Native host identity.
        complete: Whether the provider has returned its result.
        specific: Whether Cursor supplied its server-specific MCP callback.

    Returns:
        None for another tool, otherwise host-neutral ticket startup context.

    Raises:
        core.WorkspaceError: If assignment, provider result, or startup fails.
        OSError: If the assignment store or packet source cannot be accessed.
    """
    # Cursor's generic callback supplies the native tool ID. Its MCP-specific
    # callback supplies server identity; both must occur before completion.
    selected: tuple[str, core.JSONObject] | None
    # Select Cursor's native call ID from generic pre/post callbacks.
    if host == "cursor" and not specific:
        # Ignore unrelated generic tool events.
        if event.get("tool_name") != "MCP:get_issue":
            return None
        arguments = event.get("tool_input")
        token = native_token(event.get("tool_use_id"))
        # Reject malformed generic MCP arguments before opening state.
        if not isinstance(arguments, dict):
            raise core.WorkspaceError("INVALID_REQUEST")
        selected = token, arguments
    else:
        # Claude or Cursor's MCP-specific callback has its own provider shape.
        selected = _linear_tool(event, host)
    # A different tool cannot use the ticket-first exception.
    if selected is None:
        return None
    token, arguments = selected
    root, _, _ = core.repository(event["cwd"])
    key = core.participant_key({"host": host, "session_id": event["session_id"]})
    # Lock the session assignment while admitting or settling one provider call.
    with (
        core.Directory.absolute(root) as directory,
        directory.child(".task") as task,
        task.child(".bindings") as bindings,
        bindings.lock("assignment.lock"),
    ):
        # Correlate the exact Task identifier and one in-flight provider lookup.
        assignment = bindings.json(key + ".assignment.json")
        identifier = assignment["issue_id"]
        core.require(bindings.json(key + ".lookup-required.json") == assignment, "BINDING_CONFLICT")
        core.require(arguments == {"id": identifier}, "BINDING_CONFLICT")
        name = key + ".lookup.json"
        correlation = {"id": identifier, "tool_id": token}
        # Cursor requires both its generic call ID and specific server check.
        if host == "cursor":
            correlation = {
                "id": identifier,
                "tool_id": None if specific else token,
                "generic": not specific,
                "specific": specific,
            }
        # Pre-hooks reserve one exact call before its provider result can settle.
        if not complete:
            # Reuse only the matching in-flight marker or a permitted retry.
            if bindings.exists(name):
                previous = bindings.json(name)
                # Cursor merges distinct prehook facts without admitting overlap.
                if host == "cursor":
                    core.require(
                        previous.get("id") == identifier
                        and not previous["specific" if specific else "generic"]
                        and (specific or previous.get("tool_id") in {None, token}),
                        "PROVIDER_LOOKUP_IN_FLIGHT",
                    )
                    previous.update(
                        generic=previous["generic"] or not specific,
                        specific=previous["specific"] or specific,
                        tool_id=previous["tool_id"] if specific else token,
                    )
                    bindings.put(name, previous)
                else:
                    # Claude retains its native call ID through same-result retries.
                    core.require(
                        previous == correlation
                        or previous == {**correlation, "completed": True}
                        or (
                            isinstance(previous, dict)
                            and set(previous) == {"id", "tool_id", "completed"}
                            and previous["id"] == identifier
                            and previous["completed"] is True
                            and isinstance(previous["tool_id"], str)
                        ),
                        "BINDING_CONFLICT",
                    )
                    # A new Claude call ID may replace only a completed failure.
                    if previous.get("completed") is True and previous.get("tool_id") != token:
                        bindings.put(name, correlation)
            else:
                # First prehook creates the call marker under the assignment lock.
                bindings.put(name, correlation)
            return {}
        core.require(bindings.exists(name), "BINDING_MISSING")
        attempt = bindings.json(name)
        # Completion must prove both Cursor prehook facts and the native call ID.
        if host == "cursor":
            core.require(
                attempt.get("id") == identifier
                and attempt.get("tool_id") == token
                and attempt.get("generic") is True
                and attempt.get("specific") is True,
                "BINDING_CONFLICT",
            )
        else:
            # Claude completion must match its exact admitted native call ID.
            core.require(
                attempt in (correlation, {**correlation, "completed": True}), "BINDING_CONFLICT"
            )
    # Validate the provider result before any issue workspace registration.
    try:
        # Decode only the documented result field for this host.
        response = event.get("tool_response") if host == "claude-code" else event.get("tool_output")
        # Native hooks may serialize their result as a JSON string.
        if isinstance(response, str):
            # Malformed provider JSON is a provider response failure.
            try:
                response = json.loads(response)
            # Report malformed serialized provider results as response errors.
            except ValueError as error:
                # Preserve malformed-data identity instead of calling it missing.
                raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID") from error
        from nplet.adapters import startup

        # Desktop reports successful MCP content blocks directly on PostToolUse.
        # Restore only that envelope; the shared parser still validates every block and ID.
        if host == "claude-code" and isinstance(response, list):
            response = {"isError": False, "content": response}
        verified = startup.ticket(response, identifier)
    # Remove only this attempt when provider validation fails.
    except core.WorkspaceError:
        # A failed provider read permits a fresh exact retry without creating a workspace.
        with core.Directory.absolute(root) as directory, directory.child(".task") as task:
            # Remove only this failed attempt if no newer call replaced it.
            with task.child(".bindings") as bindings, bindings.lock("assignment.lock"):
                # Another call's marker must survive this late failure.
                if bindings.json(name) == attempt:
                    bindings.unlink(name)
        raise
    # Register or resume only after issue ID and immutable UUID validation.
    try:
        # The verified UUID now authorizes idempotent local registration and packet readiness.
        ready = startup.start(event, verified, host=host)
    # Release or retain correlation according to the host retry contract.
    except Exception:
        # Cursor can identify a fresh call only through its generic pre-hook.
        # Clear its completed attempt so either pre-hook order can start again.
        # Claude retains a completed native ID for exact-result retries.
        with core.Directory.absolute(root) as directory, directory.child(".task") as task:
            # Mark or clear only the attempt whose setup just failed.
            with task.child(".bindings") as bindings, bindings.lock("assignment.lock"):
                # Preserve a replacement call that raced with this failure.
                if bindings.json(name) == attempt:
                    # Cursor starts fresh because its specific hook has no call ID.
                    if host == "cursor":
                        bindings.unlink(name)
                    else:
                        # Claude can retry the same result under its exact native ID.
                        bindings.put(name, {**attempt, "completed": True})
        raise
    # Clear only the completed attempt; another attempt must retain its own marker.
    with core.Directory.absolute(root) as directory, directory.child(".task") as task:
        # Verify no newer lookup replaced the successful attempt.
        with task.child(".bindings") as bindings, bindings.lock("assignment.lock"):
            core.require(
                bindings.json(name) in (attempt, {**attempt, "completed": True}), "BINDING_CONFLICT"
            )
            bindings.unlink(name)
            bindings.unlink(key + ".lookup-required.json")
    return {"issue_id": identifier, "reader": ready["participant_id"] != ready["coordinator"]}


def native_ticket_failed(event: core.JSONObject, host: str) -> None:
    """Clear a failed native provider attempt while retaining its Task assignment.

    Args:
        event: Host failure callback for the pending Linear read.
        host: Native host identity.

    Raises:
        core.WorkspaceError: If the failed call does not match the pending attempt.
    """
    # Confirm the failed generic call's native ID and exact arguments. The
    # server-specific pre-hook must already have validated the same single call.
    selected: tuple[str, core.JSONObject] | None
    # Cursor failures carry a native ID in their generic failure callback.
    if host == "cursor":
        core.require(event.get("tool_name") == "MCP:get_issue", "BINDING_CONFLICT")
        arguments = event.get("tool_input")
        token = native_token(event.get("tool_use_id"))
        # Reject malformed failure arguments before altering the pending marker.
        if not isinstance(arguments, dict):
            raise core.WorkspaceError("INVALID_REQUEST")
        selected = token, arguments
    else:
        # Claude failures retain the same MCP-qualified native tool identity.
        selected = _linear_tool(event, host)
        # Unrelated failures cannot clear the Linear lookup marker.
        if selected is None:
            raise core.WorkspaceError("BINDING_CONFLICT")
        token, arguments = selected
    root, _, _ = core.repository(event["cwd"])
    key = core.participant_key({"host": host, "session_id": event["session_id"]})
    # Remove only the matching native failure under the assignment lock.
    with core.Directory.absolute(root) as directory, directory.child(".task") as task:
        # Validate Task identity before reading the attempt marker.
        with task.child(".bindings") as bindings, bindings.lock("assignment.lock"):
            assignment = bindings.json(key + ".assignment.json")
            core.require(
                bindings.json(key + ".lookup-required.json") == assignment, "BINDING_CONFLICT"
            )
            name = key + ".lookup.json"
            current = bindings.json(name)
            core.require(arguments == {"id": assignment["issue_id"]}, "BINDING_CONFLICT")
            # Cursor must match the generic native tool ID already recorded.
            if host == "cursor":
                core.require(
                    current.get("id") == assignment["issue_id"]
                    and current.get("tool_id") == token
                    and current.get("generic") is True,
                    "BINDING_CONFLICT",
                )
            else:
                # Claude must match its exact MCP call correlation.
                core.require(
                    current == {"id": assignment["issue_id"], "tool_id": token}, "BINDING_CONFLICT"
                )
            bindings.unlink(name)


def bootstrap_command(request: core.JSONObject, host: str) -> str:
    """Format the sole bootstrap spelling for this platform and host shell.

    Args:
        request: Explicit lifecycle request with host and session identities.
        host: Native adapter identity, determining Bash versus PowerShell syntax.

    Returns:
        A canonical command containing no expandable shell arguments.

    Raises:
        ValueError: If an argument contains shell-control or smart-quote characters.
    """
    # Use POSIX quoting for Unix or Claude Bash invocation.
    if os.name != "nt" or host == "claude-code":
        command = shlex.join([PYTHON, str(LIFECYCLE), "--request-json", json.dumps(request)])
    else:
        # Windows Cursor receives a base64 payload with fixed PowerShell quoting.
        encoded = base64.urlsafe_b64encode(json.dumps(request).encode()).decode("ascii")
        argv = [PYTHON, str(WINDOWS_BOOTSTRAP), "--request-base64", encoded]
        # Reject characters that can escape canonical Windows quoting.
        if any(any(c in arg for c in '\r\n\0"‘’“”') for arg in argv):
            raise ValueError("Unsupported PowerShell argument")
        command = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
    # Bound the shell command size and reject control characters.
    if len(command.encode()) > 65536 or any(c in command for c in "\r\n\0"):
        raise ValueError("Unsupported bootstrap command size or control character")
    return command


def bootstrap_request(command: str, host: str, python: str, lifecycle: Path) -> core.JSONObject:
    """Parse only the exact literal invocation for the selected platform shell.

    Args:
        command: Observed tool command, never evaluated.
        host: Native adapter identity.
        python: Exact checkout interpreter.
        lifecycle: Exact POSIX lifecycle entry point.

    Returns:
        The decoded request after canonical argument checks.

    Raises:
        ValueError: If shell syntax is not canonical.
        core.WorkspaceError: If the encoded request is malformed.
    """
    # Reject control characters before parsing either shell syntax.
    if any(character in command for character in "\r\n\0"):
        raise ValueError("Control character in command")
    # Parse the exact Windows PowerShell spelling for Cursor.
    if os.name == "nt" and host != "claude-code":
        token = r"'(?:[^'\r\n\x00]|'')*'"
        # Require four quoted PowerShell tokens with no extra expression.
        if not re.fullmatch(r"& " + " ".join([token] * 4), command):
            raise ValueError("Noncanonical PowerShell command")
        argv = [match[1:-1].replace("''", "'") for match in re.findall(token, command[2:])]
        # Reject unsupported quoting even after tokenization.
        if any(any(c in arg for c in '"‘’“”') for arg in argv):
            raise ValueError("Unsupported PowerShell quoting")
        # Bind Windows commands to this checkout interpreter and entry point.
        if argv[:3] != [python, str(WINDOWS_BOOTSTRAP), "--request-base64"]:
            raise ValueError("Unexpected Windows bootstrap entry")
        return encoded_bootstrap.decode_request(argv[3])
    argv = shlex.split(command)
    # Bind POSIX commands to this checkout interpreter and entry point.
    if len(argv) != 4 or argv[:3] != [python, str(lifecycle), "--request-json"]:
        raise ValueError("Unexpected bootstrap entry")
    # Require the command to round-trip to its canonical spelling.
    if command != shlex.join(argv):
        raise ValueError("Noncanonical shell command")
    request = core.strict_json(argv[3])
    # The lifecycle payload must be a JSON object.
    if not isinstance(request, dict):
        raise ValueError("Invalid request object")
    return request


def coordinator_self_refresh(request: core.JSONObject, event: core.JSONObject) -> bool:
    """Recognize the coordinator's digest-only re-scope of its own current packet.

    A coordinator whose own governing source changed is stranded at SOURCE_STALE: it
    cannot read, acknowledge or ready the stale packet, and scope is otherwise not a
    pre-readiness operation. This shape lets it replace only the reference digests;
    the core scope then clears acknowledgment, so readiness still needs read,
    acknowledge and ready at the new revision.

    Args:
        request: Decoded scope request whose session, host and worktree already match.
        event: Observed hook envelope supplying the actual session identity.

    Returns:
        True only when the caller is the issue coordinator, targets its own key, keeps
        every reference field except a valid ``sha256`` and assigns no owned paths.

    Raises:
        core.WorkspaceError: If the registered store or issue control directory is
            invalid or the issue has no committed state.
        OSError: If the store or committed state file cannot be read.
        KeyError: If the committed state lacks the coordinator or participant map.
    """
    # The bootstrap field table already limits the fields; the refresh needs these four.
    if not {"repo_id", "issue_id", "target_participant", "packet"} <= set(request):
        return False
    key = core.participant_key({"host": request["host"], "session_id": event["session_id"]})
    # The target must be the caller's own participant key.
    if request["target_participant"] != key:
        return False
    # Read the committed state file only: the hook takes no lock and runs no recovery;
    # the core scope recovers and checks expected_revision itself.
    with core.Store(request) as store, store.issues.child(request["issue_id"]) as control:
        core.require(control.exists("state.json"), "BINDING_MISSING")
        state = control.json("state.json")
    # Only the issue coordinator holding an existing packet may refresh itself.
    member = state["participants"].get(key)
    current = member.get("packet") if member is not None else None
    if key != state["coordinator"] or current is None:
        return False
    packet = request["packet"]
    # Require the same references in the same order with only sha256 changed.
    if not isinstance(packet, list) or len(packet) != len(current):
        return False
    for proposed, existing in zip(packet, current, strict=True):
        # Each proposed reference must be an object carrying a 64-hex digest.
        if not isinstance(proposed, dict) or not isinstance(proposed.get("sha256"), str):
            return False
        if not core.DIGEST.fullmatch(proposed["sha256"]):
            return False
        # Every field but the digest must equal the committed reference.
        if {k: v for k, v in proposed.items() if k != "sha256"} != {
            k: v for k, v in existing.items() if k != "sha256"
        }:
            return False
    return True


def rebind_admitted(
    request: core.JSONObject, event: core.JSONObject, assignment: core.JSONObject
) -> bool:
    """Bind an explicit rebind's target to the session's recorded Task identity.

    A Task line for another issue records that issue as the session's assignment while
    the binding still names the old one; that recorded target may be rebound to, and
    created if absent. A rebind from the assigned issue keeps the earlier rule and is
    admitted only toward a target that already exists, so it never creates an issue
    the session was not explicitly given. When the session has a live binding, the
    rebind must leave exactly that issue. A subagent never rebinds.

    Args:
        request: Decoded rebind request whose session, host and worktree already match.
        event: Observed hook envelope; a normalized subagent carries ``child``.
        assignment: The session's recorded Task assignment.

    Returns:
        Whether the request's old and new issues fit the recorded assignment.

    Raises:
        core.WorkspaceError: If the target issue ID or registered store is invalid.
        OSError: If the binding or issue control directory cannot be inspected.
        KeyError: If the request lacks its issue, repository or target fields.
    """
    # A subagent holds a coordinator-scoped assignment and cannot move a binding.
    if event.get("child") is True:
        return False
    # Validate the target ID before opening the store.
    target = core.issue_id(request["new_issue_id"])
    # Read the session binding and the target's control directory read-only: no lock
    # and no recovery.
    with core.Store(request) as store:
        # A rebind leaves only the issue this worktree's live binding names.
        binding = store.binding()
        if binding is not None and request["issue_id"] != binding["issue_id"]:
            return False
        # The recorded Task identity authorizes its target, even if absent.
        if target == assignment["issue_id"]:
            return True
        # Otherwise only a rebind away from the assigned issue toward an existing target.
        if request["issue_id"] != assignment["issue_id"]:
            return False
        # An absent control directory or state file means the target does not exist.
        if not store.issues.exists(target):
            return False
        with store.issues.child(target) as control:
            return bool(control.exists("state.json"))


def canonical_bootstrap(
    event: core.JSONObject,
    command: object,
    host: str,
    ready: bool,
    python: str = PYTHON,
    lifecycle: Path = LIFECYCLE,
) -> bool:
    """Recognize one canonical lifecycle invocation bound to this host and session.

    Args:
        event: Observed session and worktree identities.
        command: Native shell command value to inspect without executing it.
        host: Explicit adapter identity.
        ready: Whether ordinary readiness was already established.
        python: Exact project interpreter accepted by this adapter.
        lifecycle: Exact lifecycle entry point accepted by this adapter.

    Returns:
        Whether the command satisfies the exact identity and recovery schema.
    """
    # Reject oversized or nontext shell commands before parsing.
    if not isinstance(command, str) or len(command.encode()) > 65536:
        return False
    # Contain malformed commands as noncanonical observations.
    try:
        request = bootstrap_request(command, host, python, lifecycle)
        # Select the operation-specific bootstrap field policy.
        operation = request.get("operation")
        # Limit unready sessions to the documented recovery operation schemas.
        if not ready and (
            operation not in BOOTSTRAP_FIELDS
            or not set(request) <= COMMON_FIELDS | BOOTSTRAP_FIELDS[operation]
        ):
            return False
        # Bind bootstrap execution to the actual hook session and host.
        if request.get("session_id") != event["session_id"] or request.get("host", "codex") != host:
            return False
        # Require the bootstrap worktree to match the observed hook worktree.
        if core.repository(request["worktree"])[0] != core.repository(event["cwd"])[0]:
            return False
        # Keep registration and pre-registration diagnostics free of arbitrary issue/tool fields.
        identity = {"schema_version", "operation", "request_id", "worktree", "host", "session_id"}
        if request["operation"] == "register" or (
            request["operation"] == "diagnose" and "repo_id" not in request
        ):
            return set(request) <= identity | {"main_worktree", "startup"}
        # Accept only the exact read-only issue-level diagnostic shape; the assignment
        # check below then binds its issue to this session.
        if request["operation"] == "diagnose" and not (
            {"repo_id", "issue_id"} <= set(request)
            and set(request) <= identity | {"repo_id", "issue_id", "binding_generation"}
        ):
            return False
        # Require the bootstrap issue to match the session assignment stored in this worktree.
        with (
            core.Directory.absolute(core.repository(event["cwd"])[0]) as root,
            root.child(".task") as local,
        ):
            # Read the recorded assignment through the local binding directory.
            with local.child(".bindings") as bindings:
                assignment = bindings.json(core.participant_key(request) + ".assignment.json")
        # A rebind matches the assignment through its target or an existing destination.
        if request["operation"] == "rebind":
            return rebind_admitted(request, event, assignment)
        # An unassigned issue is denied before any issue store is opened.
        if request.get("issue_id") != assignment["issue_id"]:
            return False
        # An unready scope is admitted only as the coordinator's digest-only self-refresh.
        if not ready and request["operation"] == "scope":
            return coordinator_self_refresh(request, event)
        return True
    # Malformed or inaccessible assignment state never authorizes bootstrap.
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        return False


def native_token(value: object) -> str:
    """Validate a native identity without coercing absent or malformed values.

    Args:
        value: Native session, event, or tool identifier.

    Returns:
        The validated identity token.

    Raises:
        core.WorkspaceError: If the value is not a bounded string token.
    """
    # Do not coerce missing or nontext native identities.
    if not isinstance(value, str):
        raise core.WorkspaceError("INVALID_REQUEST")
    return core.token(value)


def native_identity(event: core.JSONObject, host: str) -> core.JSONObject:
    """Validate native session and worktree identity without manufacturing either.

    Args:
        event: Native host hook envelope.
        host: Either claude-code or cursor, selected by the entry point.

    Returns:
        A normalized envelope retaining native tool IDs and inputs. A Claude subagent
        event is keyed by ``<session_id>/agent/<agent_id>``, marked ``child`` and keeps
        the raw parent session in ``parent_session_id``.

    Raises:
        core.WorkspaceError: If identity is absent, ambiguous, or belongs to an
            unsupported child.
    """
    # Only Claude's documented agent_id marks a supported child; every other child,
    # remote or background marker has no supported binding here.
    child = host == "claude-code" and event.get("agent_id") is not None
    core.require(
        (event.get("agent_id") is None or child)
        and event.get("subagent_id") is None
        and event.get("parent_conversation_id") is None,
        "HOST_UNSUPPORTED_CHILD_IDENTITY",
    )
    core.require(event.get("is_background_agent", False) is False, "HOST_UNSUPPORTED_BACKGROUND")
    # Derived child markers come only from this function, never from the raw envelope.
    normalized = {
        key: value for key, value in event.items() if key not in {"child", "parent_session_id"}
    }
    # Cursor identity comes from its conversation and single workspace root.
    if host == "cursor":
        session = native_token(event.get("conversation_id"))
        core.require(event.get("session_id", session) == session, "BINDING_CONFLICT")
        roots = event.get("workspace_roots")
        # Reject ambiguous or malformed Cursor workspace roots.
        if not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], str):
            raise core.WorkspaceError("HOST_UNSUPPORTED_WORKSPACE")
        cwd = event.get("cwd", roots[0])
        core.require(
            isinstance(cwd, str) and core.repository(cwd)[0] == core.repository(roots[0])[0],
            "REPOSITORY_MISMATCH",
        )
    else:
        # A child cannot be keyed without the parent session it runs under.
        if child:
            core.require(
                isinstance(event.get("session_id"), str), "HOST_UNSUPPORTED_CHILD_IDENTITY"
            )
        # Claude binds to its native session and absolute working directory.
        session = native_token(event.get("session_id"))
        cwd = event.get("cwd")
        core.require(isinstance(cwd, str) and Path(cwd).is_absolute(), "REPOSITORY_MISMATCH")
        # Key a subagent deterministically under its parent; separators stay unambiguous.
        if child:
            agent = native_token(event.get("agent_id"))
            core.require(
                "/" not in agent and "/agent/" not in session, "HOST_UNSUPPORTED_CHILD_IDENTITY"
            )
            normalized.update(child=True, parent_session_id=session)
            session = core.token(session + "/agent/" + agent)
    normalized.update(session_id=session, cwd=cwd)
    return normalized


def native_bootstrap(event: core.JSONObject, host: str, ready: bool = False) -> bool:
    """Check native shell arguments before the shared canonical command parser.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.
        ready: Whether the core already established readiness.

    Returns:
        Whether this exact native tool call is a scoped lifecycle invocation.
    """
    # Native shells do not expose Codex's login/shell fields; accept only documented inputs.
    shell = "Bash" if host == "claude-code" else "Shell"
    # Only the host’s native shell tool can execute a bootstrap command.
    if event.get("tool_name") != shell:
        return False
    args = event.get("tool_input")
    allowed = (
        {"command", "description", "timeout", "run_in_background"}
        if host == "claude-code"
        else {"command", "working_directory"}
    )
    # Reject undocumented native shell arguments.
    if not isinstance(args, dict) or not set(args) <= allowed:
        return False
    # Disallow background bootstrap commands.
    if args.get("run_in_background", False) is not False:
        return False
    # A native working-directory override cannot redirect the bootstrap to another repository.
    try:
        # Reject working-directory redirects to another repository.
        if "working_directory" in args and (
            not isinstance(args["working_directory"], str)
            or core.repository(args["working_directory"])[0] != core.repository(event["cwd"])[0]
        ):
            return False
        return canonical_bootstrap(event, args.get("command"), host, ready)
    # Unverifiable shell details cannot establish canonical bootstrap.
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        return False


CLAUDE_SYNC_TOOLS = frozenset({"Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit"})
# Schema loading, skill loading, messaging and asking the user change no files or
# provider state; a tool they surface is still evaluated on its own call, so these
# record no pending work.
CLAUDE_NO_EFFECT_TOOLS = frozenset({"ToolSearch", "Skill", "SendMessage", "AskUserQuestion"})
# A subagent's report to its parent changes nothing either; only a child identity may
# use it, so a parent cannot impersonate a hand-back.
CLAUDE_CHILD_REPORT_TOOLS = frozenset({"SubagentHandback"})
CURSOR_SYNC_TOOLS = frozenset({"Read", "Write", "Edit", "Grep", "Delete"})


def native_tool(event: core.JSONObject, host: str) -> str:
    """Limit admission to tools whose synchronous completion can be correlated.

    Args:
        event: Native envelope carrying the actual tool-use identifier.
        host: Explicit adapter identity.

    Returns:
        The native tool name after validating its supported input shape.

    Raises:
        core.WorkspaceError: If the tool, child, provider, or async path is unsupported.
    """
    # Tool identity comes only from the host, never from a generated request UUID.
    native_token(event.get("tool_use_id"))
    tool = native_token(event.get("tool_name"))
    args = event.get("tool_input")
    # Require a structured native tool input for supported synchronous work.
    if not isinstance(args, dict):
        raise core.WorkspaceError("INVALID_REQUEST")
    # Validate configured provider provenance and exact named parent operations before effects.
    # Existing child restrictions remain narrower than the parent's task-free contracts.
    if tool.startswith(("mcp__", "MCP:")) or event.get("mcp_server") is not None:
        # A parent may read and arm the app's PR monitor; delivery stays with the parent.
        if _app_pr_monitor_tool(event, host) is not None:
            core.require(event.get("child") is not True, "HOST_UNSUPPORTED_PROVIDER")
            return tool
        if host == "claude-code" and tool in {
            "mcp__ccd_turn__update_plan",
            "mcp__ccd_turn__wrap_up",
        }:
            core.require(
                event.get("child") is not True
                and event.get("mcp_server") == {"name": "ccd_turn", "source": "sdk"},
                "HOST_UNSUPPORTED_PROVIDER",
            )
            allowed = (
                {"items", "content", "status", "scope", "advance", "done", "start"}
                if tool.endswith("update_plan")
                else {"recap", "ask", "blocked", "actions"}
            )
            core.require(bool(args) and set(args) <= allowed, "INVALID_REQUEST")
            return tool
        operation = (
            tool[4:]
            if host == "cursor"
            and tool.startswith("MCP:")
            and tool[4:] in LINEAR_PROVIDER_OPERATIONS
            else _linear_provider_tool(event, host)
        )
        core.require(operation is not None, "HOST_UNSUPPORTED_PROVIDER")
        # A child subagent gets no provider writes; issue delivery stays with the parent.
        core.require(
            event.get("child") is not True or operation in LINEAR_CHILD_OPERATIONS,
            "HOST_UNSUPPORTED_PROVIDER",
        )
        return tool
    # Child and delegation tools require their own verified identity. Only a Claude
    # parent's foreground Agent call has one: SubagentStart joins its child.
    if tool in {"Agent", "Task", "TaskOutput", "TaskStop", "SpawnAgent"}:
        core.require(
            host == "claude-code" and tool == "Agent" and event.get("child") is not True,
            "HOST_UNSUPPORTED_CHILD_IDENTITY",
        )
        # A background child outlives this call's synchronous correlation.
        core.require(args.get("run_in_background", False) is False, "HOST_UNSUPPORTED_BACKGROUND")
        # An isolated child runs in another worktree that this binding does not cover.
        core.require(args.get("isolation") is None, "HOST_UNSUPPORTED_CHILD_IDENTITY")
        return tool
    # The project disables Claude auto-backgrounding; never assume an unconfigured host did so.
    if host == "claude-code" and tool == "Bash":
        core.require(
            os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") == "1", "HOST_UNSUPPORTED_ASYNC"
        )
        core.require(
            set(args) <= {"command", "description", "timeout", "run_in_background"}
            and isinstance(args.get("command"), str)
            and args.get("run_in_background", False) is False,
            "HOST_UNSUPPORTED_ASYNC",
        )
        return tool
    # Cursor Shell needs a bounded synchronous command.
    if host == "cursor" and tool == "Shell":
        core.require(
            set(args) <= {"command", "working_directory"} and isinstance(args.get("command"), str),
            "HOST_UNSUPPORTED_ASYNC",
        )
        # A shell working-directory override must remain in the checkout.
        if "working_directory" in args:
            core.require(
                core.repository(args["working_directory"])[0] == core.repository(event["cwd"])[0],
                "REPOSITORY_MISMATCH",
            )
        return tool
    # A child may also hand its report back; every other host keeps its own set.
    supported = (
        CLAUDE_SYNC_TOOLS
        | CLAUDE_NO_EFFECT_TOOLS
        | (CLAUDE_CHILD_REPORT_TOOLS if event.get("child") is True else frozenset())
        if host == "claude-code"
        else CURSOR_SYNC_TOOLS
    )
    core.require(tool in supported, "HOST_UNSUPPORTED_TOOL")
    core.require(
        not any(
            key in args for key in ("run_in_background", "background", "is_background", "async")
        ),
        "HOST_UNSUPPORTED_ASYNC",
    )
    return tool


def native_pre(event: core.JSONObject, host: str) -> None:
    """Enforce readiness and record supported native tool admission.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.

    Raises:
        core.WorkspaceError: If recovery scope, readiness, or admission fails.
    """
    # Recovery accepts only the assigned exact command before ordinary readiness.
    native_token(event.get("tool_use_id"))
    # An exact lifecycle recovery command is allowed before readiness.
    if native_bootstrap(event, host):
        core.require(not lookup_required(event, host), "TICKET_READ_REQUIRED")
        return
    # Ready-only lifecycle transactions retain their exact recovery route before shell policy.
    if native_bootstrap(event, host, ready=True):
        core.require(not lookup_required(event, host), "TICKET_READ_REQUIRED")
        result = core.execute(request_for(event, "ready", host))
        core.require(result["ok"], result["code"])
        return
    tool = native_tool(event, host)
    # Validated parent reads, planning and notes need no task lifecycle admission.
    operation = (
        tool[4:]
        if host == "cursor" and tool.startswith("MCP:")
        else _linear_provider_tool(event, host)
    )
    roles = task_boundary.edit_roles(event)
    if (
        host == "claude-code"
        and tool
        in CLAUDE_NO_EFFECT_TOOLS | {"mcp__ccd_turn__update_plan", "mcp__ccd_turn__wrap_up"}
        and event.get("child") is not True
    ) or task_boundary.task_free(event, host, operation, roles):
        return
    # Raw control-store mutation never becomes a readiness-authorized note.
    core.require("control" not in roles, "NOT_OWNER")
    core.require(not lookup_required(event, host), "TICKET_READ_REQUIRED")
    request = request_for(event, "ready", host)
    result = core.execute(request)
    core.require(result["ok"], result["code"])
    # Native permission approval remains separate from recording pending work.
    # A ready session's no-effect load, message or hand-back needs no settlement.
    if host == "claude-code" and tool in CLAUDE_NO_EFFECT_TOOLS | CLAUDE_CHILD_REPORT_TOOLS:
        return
    # Remember the parent's Agent call so SubagentStart can require it to be pending.
    if tool == "Agent":
        record_agent_call(event, host)
    result = core.execute(
        {
            **request,
            "operation": "tool-start",
            "request_id": "pre:" + event["tool_use_id"],
            "tool_id": event["tool_use_id"],
        }
    )
    core.require(result["ok"], result["code"])


def native_post(event: core.JSONObject, host: str, failed: bool) -> None:
    """Settle only a documented synchronous result for the observed native tool ID.

    Args:
        event: Identity-validated success or failure envelope.
        host: Explicit adapter identity.
        failed: Whether this is the host's failure event.

    Raises:
        core.WorkspaceError: If completion is ambiguous or core correlation fails.
    """
    # Lifecycle calls own their transaction and are never admitted as external work.
    if native_bootstrap(event, host, ready=True):
        return
    tool = native_tool(event, host)
    request = existing_admission(event, host)
    if request is None:
        return
    # No-effect loads, messages and hand-backs were never recorded as pending work.
    if host == "claude-code" and tool in CLAUDE_NO_EFFECT_TOOLS | CLAUDE_CHILD_REPORT_TOOLS:
        return
    # Require the documented success/failure payload before treating an event as completion.
    if failed:
        field = "error" if host == "claude-code" else "error_message"
        core.require(isinstance(event.get(field), str), "INVALID_REQUEST")
        core.require(type(event.get("is_interrupt", False)) is bool, "INVALID_REQUEST")
        # Cursor failures require the native failure type.
        if host == "cursor":
            core.require(
                event.get("failure_type") in {"error", "timeout", "permission_denied"},
                "INVALID_REQUEST",
            )
    # Cursor success requires its native serialized output field.
    elif host == "cursor":
        core.require(isinstance(event.get("tool_output"), str), "INVALID_REQUEST")
    else:
        # Claude success requires a nonempty native tool response.
        core.require(event.get("tool_response") is not None, "INVALID_REQUEST")
    # Claude Bash completion must prove a foreground terminal result.
    if host == "claude-code" and tool == "Bash":
        core.require(not failed, "HOST_UNSUPPORTED_ASYNC")
        output = event["tool_response"]
        core.require(
            isinstance(output, dict)
            and output.get("interrupted") is False
            and isinstance(output.get("stdout"), str)
            and isinstance(output.get("stderr"), str)
            and not any(
                key in output
                for key in (
                    "backgroundTaskId",
                    "task_id",
                    "session_id",
                    "sessionId",
                    "background",
                    "run_in_background",
                    "is_background",
                    "async",
                )
            ),
            "HOST_UNSUPPORTED_ASYNC",
        )
    # Cursor Shell completion must prove an exit code with no async marker.
    if host == "cursor" and tool == "Shell":
        # A failure/timeout notification does not prove the spawned process terminated.
        core.require(not failed, "HOST_UNSUPPORTED_ASYNC")
        output = core.strict_json(event.get("tool_output", ""))
        core.require(
            isinstance(output, dict)
            and type(output.get("exitCode")) is int
            and not any(
                key in output
                for key in (
                    "session_id",
                    "sessionId",
                    "task_id",
                    "backgroundTaskId",
                    "background",
                    "run_in_background",
                    "is_background",
                    "async",
                )
            ),
            "HOST_UNSUPPORTED_ASYNC",
        )
    # Success/failure hook events settle the supported synchronous file tools themselves.
    result = core.execute(
        {
            **request,
            "request_id": "native-post:" + core.sha(core.canonical([event["tool_use_id"], failed])),
            "tool_id": event["tool_use_id"],
            "completed": True,
        }
    )
    core.require(result["ok"], result["code"])


AGENT_CALL_LIMIT = 16
# Concurrent hook processes (sibling SubagentStart joins, the parent's own tool events)
# advance the issue revision between a state read and the child's join. The join is
# retried on a fresh revision within a budget that leaves the runner deadline room.
CHILD_JOIN_ATTEMPTS = 8
CHILD_JOIN_BUDGET_SECONDS = 1.0
CHILD_RETRY_CODES = frozenset({"REVISION_CONFLICT", "BUSY"})


def _agent_calls_name(event: core.JSONObject, host: str) -> str:
    """Name the local marker that lists one parent session's admitted Agent call IDs.

    Args:
        event: Identity-validated parent envelope.
        host: Explicit adapter identity.

    Returns:
        The binding-directory file name for this parent participant.
    """
    return core.participant_key({"host": host, "session_id": event["session_id"]}) + (
        ".agent-calls.json"
    )


def record_agent_call(event: core.JSONObject, host: str) -> None:
    """Record a parent's admitted foreground Agent call ID before its tool-start.

    The core keeps pending work by tool ID only, so this bounded marker names which
    pending IDs are Agent calls. SubagentStart intersects it with the parent's core
    pending set; a stale entry therefore never authorizes a join.

    Args:
        event: Identity-validated parent PreToolUse envelope for the Agent tool.
        host: Explicit adapter identity.

    Raises:
        core.WorkspaceError: If the checkout or tool identity is invalid.
        OSError: If the binding directory cannot be written.
    """
    # Resolve this checkout's binding directory under the shared assignment lock.
    root, _, _ = core.repository(event["cwd"])
    name = _agent_calls_name(event, host)
    with (
        core.Directory.absolute(root) as worktree,
        worktree.child(".task", True) as local,
        local.child(".bindings", True) as bindings,
        bindings.lock("assignment.lock"),
    ):
        # Append the native call ID and keep only the most recent bounded entries.
        calls = bindings.json(name)["tool_ids"] if bindings.exists(name) else []
        calls = [call for call in calls if call != event["tool_use_id"]]
        calls.append(native_token(event["tool_use_id"]))
        bindings.put(name, {"tool_ids": calls[-AGENT_CALL_LIMIT:]})


def _issue_state(parent: core.JSONObject) -> core.WorkspaceState:
    """Read the parent issue's committed state after recovering any staged transaction.

    Args:
        parent: Verified parent lifecycle request carrying repository and issue identity.

    Returns:
        The committed issue control state at its current revision.

    Raises:
        core.WorkspaceError: If the store or issue cannot be opened.
        OSError: If issue state cannot be accessed.
    """
    # Hold the issue lock only for recovery and the committed read.
    identifier = parent["issue_id"]
    with core.Store(parent) as store, store.issues.child(identifier) as control, control.lock():
        issue = core.Issue(store, control, identifier)
        issue.recover()
        return issue.committed_state()


def _join_child(
    event: core.JSONObject, host: str, parent: core.JSONObject, deadline: float
) -> None:
    """Join the child as a reader, re-reading the issue revision before every attempt.

    Args:
        event: Identity-validated SubagentStart envelope for the child.
        host: Explicit adapter identity.
        parent: Verified ready parent lifecycle request.
        deadline: Monotonic time after which no further join attempt starts.

    Raises:
        core.WorkspaceError: With the last join code when attempts or the budget run out,
            or immediately for a code that a fresh revision cannot resolve.
    """
    # The child's own participant key tells whether an earlier attempt already joined it.
    child_key = core.participant_key({"host": host, "session_id": event["session_id"]})
    attempt = 0
    # Every pass ends in a return or a raise; the attempt count bounds the retries.
    while True:
        # Read the revision immediately before the join; an existing member needs none.
        state = _issue_state(parent)
        if child_key in state["participants"]:
            return
        joined = core.execute(
            {
                "schema_version": 1,
                "operation": "join",
                "request_id": str(uuid.uuid4()),
                "worktree": event["cwd"],
                "host": host,
                "session_id": event["session_id"],
                "repo_id": parent["repo_id"],
                "issue_id": parent["issue_id"],
                "issue_uuid": state["issue_uuid"],
                "expected_revision": state["revision"],
            }
        )
        if joined["ok"]:
            return
        # Retry only revision drift or contention, while attempts and budget remain.
        attempt += 1
        final = attempt == CHILD_JOIN_ATTEMPTS or time.monotonic() >= deadline
        if joined["code"] not in CHILD_RETRY_CODES or final:
            raise core.WorkspaceError(joined["code"])
        # Back off briefly so the competing hook process can commit and release locks.
        time.sleep(min(0.02 * attempt, max(0.0, deadline - time.monotonic())))


def _acknowledge_child(child: core.JSONObject) -> tuple[str, core.JSONObject]:
    """Read the child's packet, then acknowledge exactly the revision and digest it returned.

    Args:
        child: The child's bound lifecycle read request.

    Returns:
        The read packet digest and the acknowledge response, which may report a conflict.

    Raises:
        core.WorkspaceError: If the read itself fails.
    """
    read = core.execute({**child, "request_id": str(uuid.uuid4())})
    core.require(read["ok"], read["code"])
    acknowledged = core.execute(
        {
            **child,
            "operation": "acknowledge",
            "request_id": str(uuid.uuid4()),
            "expected_revision": read["revision"],
            "packet_digest": read["packet_digest"],
        }
    )
    return str(read["packet_digest"]), acknowledged


def _ready_child(event: core.JSONObject, host: str) -> str:
    """Read, acknowledge and verify the child's own packet, tolerating one revision drift.

    Args:
        event: Identity-validated SubagentStart envelope for the joined child.
        host: Explicit adapter identity.

    Returns:
        The digest of the packet the child acknowledged.

    Raises:
        core.WorkspaceError: If read, acknowledge or ready fails for the child.
        OSError: If binding or issue state cannot be accessed.
    """
    # Resolve the child's own binding, created by its join, for every lifecycle call.
    child = request_for(event, "read", host)
    digest, acknowledged = _acknowledge_child(child)
    # Another hook advanced the revision after the read: read and acknowledge again once.
    if not acknowledged["ok"] and acknowledged["code"] == "REVISION_CONFLICT":
        digest, acknowledged = _acknowledge_child(child)
    core.require(acknowledged["ok"], acknowledged["code"])
    # Readiness takes no revision, so it is verified once after the acknowledgment.
    ready = core.execute({**child, "operation": "ready", "request_id": str(uuid.uuid4())})
    core.require(ready["ok"], ready["code"])
    return digest


def child_start(event: core.JSONObject, host: str) -> str:
    """Join a Claude subagent as its own reader when its ready parent awaits an Agent call.

    SubagentStart fires once and cannot block, so a child that misses its join has no
    later recovery. The join and the acknowledgment therefore tolerate the revision
    drift caused by concurrent hook processes, within a bounded budget.

    Args:
        event: Identity-validated SubagentStart envelope marked as a child.
        host: Explicit adapter identity.

    Returns:
        Fixed control text naming only the child's participant key and packet digest.

    Raises:
        core.WorkspaceError: If the event is not a child, the parent binding is not
            ready, no admitted parent Agent call is pending, or a lifecycle step fails
            (including a join still in conflict after its bounded retries).
        OSError: If binding or issue state cannot be accessed.
    """
    # Start the retry budget at entry so the whole hook stays inside the runner deadline.
    deadline = time.monotonic() + CHILD_JOIN_BUDGET_SECONDS
    # Only a normalized Claude child carries the parent session it runs under.
    core.require(event.get("child") is True, "HOST_UNSUPPORTED_CHILD_IDENTITY")
    parent_event = {**event, "session_id": event["parent_session_id"]}
    # The parent's own binding must be ready; a child never borrows an unready parent.
    parent = request_for(parent_event, "ready", host)
    result = core.execute(parent)
    core.require(result["ok"], result["code"])
    identifier = parent["issue_id"]
    # Derive the parent's pending call IDs and the child's own participant key.
    state = _issue_state(parent)
    pending = set(state["participants"][core.participant_key(parent)]["pending"])
    child_key = core.participant_key({"host": host, "session_id": event["session_id"]})
    root, _, _ = core.repository(event["cwd"])
    # Require a pending admitted Agent call, then record the child's issue assignment.
    with (
        core.Directory.absolute(root) as worktree,
        worktree.child(".task") as local,
        local.child(".bindings") as bindings,
        bindings.lock("assignment.lock"),
    ):
        name = _agent_calls_name(parent_event, host)
        calls = set(bindings.json(name)["tool_ids"]) if bindings.exists(name) else set()
        core.require(bool(calls & pending), "HOST_UNSUPPORTED_CHILD_IDENTITY")
        assignment = child_key + ".assignment.json"
        # A child identity already recorded for another issue is never reassigned.
        if bindings.exists(assignment):
            core.require(bindings.json(assignment)["issue_id"] == identifier, "BINDING_CONFLICT")
        bindings.put(assignment, {"issue_id": identifier})
    # Join with the core-chosen roadmap-only packet, then make the child ready on it.
    _join_child(event, host, parent, deadline)
    digest = _ready_child(event, host)
    # Report identifiers and digests only; task text stays in the packet sources.
    return (
        f"TASK_WORKSPACE_CHILD_READY: participant {child_key}; packet {digest}. "
        "This subagent holds its own reader binding for the parent's issue. Read the packet "
        "through the lifecycle read operation, write only paths the coordinator scopes to "
        "this participant, and do not start nested Agent calls."
    )


def native_context(event: core.JSONObject, host: str) -> str:
    """Report readiness or a recovery route without granting tool permission.

    Args:
        event: Identity-validated session envelope.
        host: Explicit adapter identity.

    Returns:
        Bounded task context with the exact core diagnostic when unavailable.
    """
    # Readiness diagnostics remain advisory to the native host.
    try:
        result = core.execute(request_for(event, "ready", host))
        core.require(result["ok"], result["code"])
    # Convert a failed readiness check into bounded recovery text.
    except core.WorkspaceError as error:
        return recovery(error.code, host)
    return "Task binding checked. Read the assigned packet through the lifecycle read operation."


def _recovery_route(code: str, host: str) -> str:
    """Name the admitted next operation for one diagnostic.

    Args:
        code: Bounded diagnostic selected by the adapter or core.
        host: Explicit adapter identity, which selects host-specific tool names.

    Returns:
        One or two sentences naming the operation that can change the outcome.
    """
    # Name the canonical command formatter once for every lifecycle route.
    command = "the exact lifecycle command from adapters.common.bootstrap_command"
    # Ticket preparation is host-specific and never consumes the required provider read.
    if code == "TICKET_READ_REQUIRED":
        preparation = (
            " Startup preparation permits ToolSearch selecting that get_issue tool, "
            "Read or a plain "
            "`cat` of <checkout>/docs/runtime/contributor-workflow.md or <checkout>/AGENTS.md "
            "are admitted."
            if host == "claude-code"
            else (
                " Exact command-only Bash reads of AGENTS.md and "
                "docs/runtime/contributor-workflow.md, and canonical read-only diagnose, "
                "are admitted without clearing the ticket requirement."
                if host == "codex"
                else ""
            )
        )
        return (
            "Next admitted operation: the configured Linear get_issue call with exactly "
            '{"id": "<selected issue>"} before protected implementation.'
            + preparation
            + " Independent authorized task-free reads, planning, notes and administration "
            "remain available."
        )
    # An unbound session starts or resumes only through the ticket-first route.
    if code == "BINDING_MISSING":
        return (
            "This session has no issue binding. Before protected implementation, submit "
            "exactly one `Task: <issue-id>` line, "
            "then read that ticket with the configured Linear get_issue call; startup "
            f"registers or resumes the workspace. A pre-registration diagnose uses {command}. "
            "Independent authorized task-free reads, planning, notes and administration "
            "remain available. "
            "Finite shell reads include canonical cat, rg --no-config, and "
            "git --no-pager -c core.fsmonitor=false "
            "forms documented in docs/runtime/host-hooks.md; arbitrary shell commands remain gated."
        )
    # A current packet whose acknowledgment is missing or outdated needs a new cycle.
    if code == "NOT_READY":
        return (
            "Acknowledgment is missing or outdated for the current packet (for example after "
            "your own committed roadmap update): run read, then acknowledge the returned "
            f"packet_digest, then ready, using {command}."
        )
    # A child's acknowledgment requires correlated delivery of every current source.
    if code == "SOURCE_DELIVERY_REQUIRED":
        return (
            "Run the exact `cat -- <absolute source path>` command through command-only "
            "Bash for each undelivered or mismatched source in the current packet, with "
            "the absolute path shell-quoted as required. Wait for matching source-byte "
            "completion, then acknowledge the current packet_digest, then ready."
        )
    # Coordinators and readers refresh a stale packet through different owners.
    if code == "SOURCE_STALE":
        return (
            "A required packet source changed after acknowledgment. Coordinator: run an "
            "issue-level diagnose; its `packet` is your current packet and its `sources` "
            "lists, in the same order, each reference's current_sha256. Then run a "
            "self-refresh scope (target_participant is your own key, packet is that list "
            "with each sha256 replaced by sources[*].current_sha256, no owned_paths), then "
            "read, acknowledge the returned packet_digest, then ready. A scope with "
            "unchanged digests is accepted but does not restore readiness; a failed read "
            "lists the changed references in `stale`. Reader: ask the coordinator "
            "to refresh your packet with scope, or resubmit the Task line and exact "
            "get_issue read to refresh a "
            f"roadmap-only packet; then read, acknowledge and ready, using {command}."
        )
    # Background or asynchronous work must be re-issued in the foreground.
    if code in {"HOST_UNSUPPORTED_BACKGROUND", "HOST_UNSUPPORTED_ASYNC"}:
        return (
            "No lifecycle operation changes this outcome. Re-issue the call in the "
            "foreground, without background execution or isolation, so its completion can "
            "be correlated."
        )
    # A stale revision is resolved by rereading and reapplying the change.
    if code == "REVISION_CONFLICT":
        return (
            "The issue revision changed. Read the current revision and file digest, reapply "
            f"the change and retry with the new expected_revision, using {command}."
        )
    # A session bound to another issue keeps that binding until an explicit rebind.
    if code == "BINDING_CONFLICT":
        return (
            "This session is bound to another issue or call. Continue the bound issue, "
            "submit `Task: <other-issue>` and read that ticket so startup rebinds you "
            "(the old issue is preserved), or run an explicit rebind to a target that "
            f"already assigns this session, using {command}."
        )
    # Lifecycle requests must name the registered checkout.
    if code == "REPOSITORY_MISMATCH":
        return (
            "The request names another repository or worktree. Retry from the registered "
            "checkout or one of its registered worktrees."
        )
    # A rejected ticket response needs a fresh exact read.
    if code in {"PROVIDER_RESPONSE_INVALID", "ISSUE_MISMATCH"}:
        mismatch = (
            " If ISSUE_MISMATCH repeats, the existing local workspace records another "
            "provider issue UUID for this ID: inspect it with an issue-level diagnose "
            "instead of rebinding into it."
            if code == "ISSUE_MISMATCH"
            else ""
        )
        return (
            "The ticket response did not verify. Repeat the exact selected-ticket get_issue "
            "call under a new native tool call; no workspace was created from this response."
            + mismatch
        )
    # Ownership and scope come only from the coordinator; a refused switch can return.
    if code in {"NOT_OWNER", "SCOPE_MISSING"}:
        switch = (
            " A Task-line switch into an existing issue that does not assign this session "
            "stops here with the old binding unchanged: ask that issue's coordinator to "
            "scope you, or submit `Task: <bound issue>` and repeat its ticket read to return."
            if code == "SCOPE_MISSING"
            else ""
        )
        return (
            "This participant does not own the path or lacks an assignment. Ask the "
            "coordinator to scope the path or participant, then read, acknowledge and ready."
            + switch
        )
    # Unresolved work or attached participants block a binding change until settled.
    if code == "PENDING_OPERATION":
        return (
            "Pending tool work or attached participants block this change; the binding is "
            "unchanged. Let the pending tool calls complete so their completions settle "
            "them, or have attached participants detach (an issue's coordinator moves only "
            "after its other participants detach), then retry. To keep working meanwhile, "
            "submit `Task: <bound issue>` and repeat its ticket read."
        )
    # An unmanaged payload directory is imported only by explicit adoption.
    if code == "ADOPTION_REQUIRED":
        return (
            "The target issue's payload directory exists without managed state; nothing was "
            "created or moved. Import it with an adopt request carrying its exact inventory, "
            f"owners and evidence, using {command}."
        )
    # Provider admission is fixed by host, readiness and the configured connector.
    if code == "HOST_UNSUPPORTED_PROVIDER":
        monitor = (
            " Claude's parent ccd_pr get_status and set_monitor remain readiness-gated."
            if host == "claude-code"
            else ""
        )
        return (
            "This provider identity, provenance or operation is unsupported. "
            "Use a named task-free Linear contract, including save_issue and save_comment, "
            "or a verified review route. Startup still requires "
            "the exact selected-ticket get_issue."
            + monitor
            + " Another server or operation needs a separately verified provider workflow; "
            "readiness does not widen the configured provider or child authority."
        )
    # Unsupported tools need a different tool, not lifecycle recovery.
    if code == "HOST_UNSUPPORTED_TOOL":
        # Codex children have a narrower command-only route than Cursor file tools.
        if host == "codex":
            return (
                "No lifecycle operation admits this tool. A Codex child may use only "
                "command-only Bash for its own canonical lifecycle commands or exact "
                "packet-source cat reads."
            )
        tools = (
            "Read, Write, Edit, Glob, Grep, NotebookEdit, foreground Bash, ToolSearch, Skill, "
            "SendMessage or AskUserQuestion (a subagent also has SubagentHandback)"
            if host == "claude-code"
            else "Read, Write, Edit, Grep, Delete or foreground Shell"
        )
        return f"No lifecycle operation admits this tool. Use one of the correlated tools: {tools}."
    # Exhausted native cohorts retain unknown launches instead of recycling authority.
    if code == "HOST_CHILD_CAPACITY_EXHAUSTED":
        return (
            "This parent binding has reached its 32-launch lifetime limit. Unknown or failed "
            "attempts retain their slots because no native no-child outcome was verified. "
            "Do not reset the registry or retry another launch in this binding. A fresh "
            "parent must join under its own identity and receive an explicit coordinator "
            "handoff after pending work is resolved; retain unresolved old activity."
        )
    # Child work requires its own verified identity, never the parent binding.
    if code == "HOST_UNSUPPORTED_CHILD_IDENTITY":
        # Claude's only child route is a ready parent's foreground, non-isolated Agent call.
        if host == "claude-code":
            return (
                "Child or delegation work cannot reuse the parent binding. Only a foreground "
                "Agent call without isolation from a ready parent session is admitted; its "
                "subagent receives its own reader binding at SubagentStart. Nested Agent "
                "calls, Task, TaskOutput, TaskStop, SpawnAgent and children of an unready "
                "parent stay unsupported; perform that step in the parent session."
            )
        return (
            "Child or delegation work has no verified child identity on this host and cannot "
            "reuse the parent binding. Perform the step in this session or dispatch it through "
            "a supported child route; no lifecycle operation grants a child binding here."
        )
    # Lock contention and the hook deadline are transient.
    if code == "BUSY":
        return (
            "Another lifecycle operation held the workspace lock or the hook deadline expired. "
            f"Retry the same operation once; if BUSY repeats, run diagnose with {command}."
        )
    # Other diagnostics keep the general bootstrap route.
    return (
        f"Use {command} to diagnose, register, resume, read and acknowledge the assigned "
        "packet. Unsupported child/provider/background work must use a separately verified "
        "route."
    )


def recovery(code: str, host: str) -> str:
    """Describe the admitted next operation for a diagnostic without copying task content.

    Args:
        code: Bounded diagnostic selected by the adapter or core.
        host: Explicit adapter identity required in lifecycle commands.

    Returns:
        A bounded explanation naming the code, its admitted next operation and the
        retained state.
    """
    return (
        f"TASK_WORKSPACE_NOT_READY: {code}. {_recovery_route(code, host)} "
        f"Lifecycle commands use host={host} and the observed session ID. "
        "Pending work is retained; no completion or readiness was inferred."
    )


def native_observe(event: core.JSONObject, host: str) -> None:
    """Record an advisory ending without settling operations or retiring the session.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.
    """
    # Record terminal events as observations only.
    try:
        request = request_for(event, "event", host)
        core.execute({**request, "event_type": "observation", "event": {"code": "UNKNOWN"}})
    # Ignore advisory observation failures without settling work.
    except (core.WorkspaceError, OSError):
        pass  # Advisory host endings are not completion or cleanup evidence.


def run_native(
    handler: Callable[[core.JSONObject], core.JSONObject],
    failure: Callable[[str, str], core.JSONObject],
) -> int:
    """Bound native hook input and runtime while exposing parser errors as exit two.

    Args:
        handler: Native event translator for the selected host.
        failure: Native diagnostic renderer for the selected host.

    Returns:
        Zero for a translated event; two for parser, timeout, or protocol failure.
    """
    return runner.run(handler, failure)
