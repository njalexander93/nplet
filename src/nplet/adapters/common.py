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
from typing import NamedTuple

from nplet.adapters import bootstrap as encoded_bootstrap
from nplet.adapters import repository_exemptions, repository_location, runner, task_boundary
from nplet.adapters.session_repository import (
    carry_repository,
    project_root,
    session_project_root,
    session_repository,
    session_root,
)
from nplet.lifecycle import task_workspace as core

ROOT = Path(__file__).resolve().parents[3]
LINEAR_SERVER_NAMES = frozenset({"linear", "linear-server"})
LINEAR_SERVER_URLS = frozenset(
    {"https://mcp.linear.app/mcp", "https://mcp.linear.app/mcp/readonly"}
)

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
    root = session_root(event)
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
                "Task identity recorded. Before protected implementation, read the requested "
                "Linear ticket, complete scoped workspace setup and verify readiness. "
                "Independent authorized task-free work remains available. "
                "Recording Task does not require immediate startup. "
                "Honor the user's scope, pause or stop instructions."
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
    # A session outside every repository has no store and so no ticket-first assignment.
    repository = session_repository(event)
    if repository is None:
        return False
    root, _, _ = repository
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
    root = session_root(event)
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
    """Select recorded completion authority through the core's synchronized existing-store lookup.

    Args:
        event: Identity-validated completion with its native call ID.
        host: Actual adapter identity.

    Returns:
        Existing bound request for exact admission, replay or a unique local poll handle;
        None when no admission exists.

    Raises:
        core.WorkspaceError: If existing state, native identity or correlation is invalid.
        OSError: If existing safe storage cannot be inspected or locked.
    """
    # Translate native polling shape; storage and lock ownership remain inside the core.
    tool_id = native_token(event.get("tool_use_id"))
    # A session outside every repository has no store that could hold an admission.
    if session_repository(event) is None:
        return None
    polling = event.get("tool_name") == "write_stdin"
    return core.existing_tool_request(
        {
            "schema_version": 1,
            "operation": "tool-complete",
            "request_id": str(uuid.uuid4()),
            "worktree": event["cwd"],
            "host": host,
            "session_id": event["session_id"],
        },
        tool_id,
        polling=polling,
        poll_handle=event.get("tool_input", {}).get("session_id") if polling else None,
    )


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
            event.get("mcp_server_name") in LINEAR_SERVER_NAMES, "HOST_UNSUPPORTED_PROVIDER"
        )
        url = event.get("mcp_server_url", event.get("url"))
        core.require(
            url in LINEAR_SERVER_URLS,
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
    # Select the session repository's worktree root.
    root = session_root(event)
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
    root = session_root(event)
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
    root = session_root(event)
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
        if not isinstance(operation, str):
            return False
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
        if core.repository(request["worktree"])[0] != session_root(event):
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
            core.Directory.absolute(session_root(event)) as root,
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
        A normalized envelope retaining native tool IDs and inputs, with ``repository``
        holding the session's project repository as a JSON-safe dict (root, common
        Git directory and worktree roots), or None outside every repository. A Claude subagent
        event is keyed by ``<session_id>/agent/<agent_id>``, marked ``child`` and keeps
        the raw parent session in ``parent_session_id``.

    Raises:
        core.WorkspaceError: If identity is absent, ambiguous, or belongs to an
            unsupported child, or repository discovery fails rather than proving absence.
        OSError: If Git cannot be launched.
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
        core.require(isinstance(cwd, str), "REPOSITORY_MISMATCH")
        # Cursor mirrors a Shell directory override into the top-level cwd; that names
        # where the command runs, not the session, which stays in its workspace root.
        args = event.get("tool_input")
        if (
            event.get("tool_name") == "Shell"
            and isinstance(args, dict)
            and cwd in (args.get("cwd"), args.get("working_directory"))
        ):
            cwd = roots[0]
        # Resolve the session repository once; the workspace root must share it, or both
        # must lie outside every repository.
        normalized["cwd"] = cwd
        normalized = carry_repository(normalized)
        core.require(
            project_root(roots[0]) == session_project_root(normalized), "REPOSITORY_MISMATCH"
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
        # Resolve the session repository once from the validated working directory.
        normalized["cwd"] = cwd
        normalized = carry_repository(normalized)
    # Key the envelope by the validated native session.
    normalized["session_id"] = session
    return normalized


def cursor_shell_directory(args: core.JSONObject) -> str | None:
    """Validate native Shell metadata without authorizing the command.

    This validates transport fields only; timeout can background execution and
    supplies no synchronous completion evidence. The directory is not compared with
    the session's repository; lifecycle callers use ``cursor_lifecycle_shell``.

    Args:
        args: Native Shell input whose command authority the caller checks separately.

    Returns:
        The supplied ``cwd`` or ``working_directory`` text, or None when neither is
        supplied or ``cwd`` is empty, so Shell runs in the event's own directory.

    Raises:
        core.WorkspaceError: Fields, aliases, timeout or directory type are invalid.
    """
    # Metadata alone grants neither command authority nor lifecycle settlement.
    core.require(
        set(args) <= {"command", "working_directory", "cwd", "timeout"}
        and not {"working_directory", "cwd"} <= set(args),
        "HOST_UNSUPPORTED_TOOL",
    )
    # Cursor transports a positive int32 timeout; booleans and fractional values are invalid.
    if "timeout" in args:
        timeout = args["timeout"]
        core.require(type(timeout) is int and 1 <= timeout <= 2147483647, "HOST_UNSUPPORTED_TOOL")
    # At most one directory field is present; empty native cwd supplies no override.
    key = "cwd" if "cwd" in args else "working_directory"
    if key not in args:
        return None
    directory = args[key]
    core.require(isinstance(directory, str), "REPOSITORY_MISMATCH")
    return None if key == "cwd" and directory == "" else directory


def cursor_lifecycle_shell(event: core.JSONObject, args: core.JSONObject) -> None:
    """Validate Shell metadata for a lifecycle, reconciliation or child command.

    These commands act on the session's own store, so a directory override must not
    move them into another repository.

    Args:
        event: Identity-validated Cursor event with its observed repository.
        args: Native Shell input whose command authority the caller checks separately.

    Raises:
        core.WorkspaceError: Fields, aliases, timeout or repository override are invalid.
    """
    # A supplied directory must share the session's repository, or both must lie
    # outside every repository.
    directory = cursor_shell_directory(args)
    if directory is not None:
        core.require(project_root(directory) == session_project_root(event), "REPOSITORY_MISMATCH")


def native_bootstrap(event: core.JSONObject, host: str, ready: bool = False) -> bool:
    """Check native shell arguments before the shared canonical command parser.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.
        ready: Whether the core already established readiness.

    Returns:
        Whether this exact native tool call is a scoped lifecycle invocation.

    Raises:
        core.WorkspaceError: ``REPOSITORY_MISMATCH`` when a Cursor lifecycle command's
            directory override lies in another repository.
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
        else {"command", "working_directory", "cwd", "timeout"}
    )
    # Reject undocumented native shell arguments.
    if not isinstance(args, dict) or not set(args) <= allowed:
        return False
    # Disallow background bootstrap commands.
    if args.get("run_in_background", False) is not False:
        return False
    # Unverifiable shell details cannot establish canonical bootstrap.
    try:
        if host == "cursor":
            cursor_shell_directory(args)
        if not canonical_bootstrap(event, args.get("command"), host, ready):
            return False
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        return False
    # A lifecycle command cannot be redirected to another repository; it is denied
    # rather than falling through to ordinary Shell admission.
    if host == "cursor":
        cursor_lifecycle_shell(event, args)
    return True


CLAUDE_SYNC_TOOLS = frozenset({"Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit"})
# Schema loading, skill loading, messaging and asking the user change no files or
# provider state; a tool they surface is still evaluated on its own call, so these
# record no pending work.
CLAUDE_NO_EFFECT_TOOLS = frozenset({"ToolSearch", "Skill", "SendMessage", "AskUserQuestion"})
# A subagent's report to its parent changes nothing either; only a child identity may
# use it, so a parent cannot impersonate a hand-back.
CLAUDE_CHILD_REPORT_TOOLS = frozenset({"SubagentHandback"})
# Delegation tools start or steer other agents; only a parent's foreground Agent call
# has a verified child route. Workflow runs a script that spawns agents.
CLAUDE_DELEGATION_TOOLS = frozenset(
    {"Agent", "Task", "TaskOutput", "TaskStop", "SpawnAgent", "Workflow"}
)
# Moving the session into or out of a worktree changes its checkout, so a parent
# needs readiness and records it as ordinary pending work.
CLAUDE_PARENT_GATED_TOOLS = frozenset({"EnterWorktree", "ExitWorktree"})
# These run shell commands outside the Bash policy, so they stay closed until shell
# admission covers them.
CLAUDE_CLOSED_TOOLS = frozenset({"Monitor", "PowerShell"})
# Deletes on the configured Linear server stay denied (these five are documented; any
# other delete_ operation is closed as well); other unlisted operations are unchecked
# external work that grants no ticket or lifecycle evidence.
LINEAR_DENIED_OPERATIONS = frozenset(
    {
        "delete_comment",
        "delete_customer",
        "delete_customer_need",
        "delete_diff_comment",
        "delete_status_update",
    }
)
# Desktop app servers that keep their own route or stay denied: PR delivery, session
# management, settings and the turn plan.
APP_CLOSED_SERVERS = ("ccd_pr", "ccd_session_mgmt", "ccd_settings", "ccd_turn")
CURSOR_SYNC_TOOLS = frozenset({"Read", "Write", "Edit", "Grep", "Delete"})


def unchecked(event: core.JSONObject, host: str) -> bool:
    """Decide whether a Claude or Cursor parent call is unchecked external work.

    An unchecked call is admitted with no lifecycle work: no readiness, ticket-read,
    tool-start or settlement. Every tool with its own route, every edit and shell
    tool, the delegation tools and closed provider operations stay outside it, as
    does every child call and every Codex call. In Cursor only a generic ``MCP:``
    call that names no Linear operation is unchecked.

    Args:
        event: Native envelope carrying the host's tool name and child flag.
        host: Explicit adapter identity; Claude Code and Cursor have unchecked calls.

    Returns:
        True only for a parent tool that no existing route names.
    """
    # Children, Codex and every tool with its own route keep their admission.
    tool = event.get("tool_name")
    if event.get("child") is True or not isinstance(tool, str):
        return False
    # Cursor's generic callback carries no server, so a Linear operation keeps its route
    # and the blocking beforeMCPExecution hook checks the server of every other call.
    if host == "cursor":
        return tool.startswith("MCP:") and tool[4:] not in LINEAR_PROVIDER_OPERATIONS
    if host != "claude-code":
        return False
    known = (
        CLAUDE_SYNC_TOOLS
        | CLAUDE_NO_EFFECT_TOOLS
        | CLAUDE_CHILD_REPORT_TOOLS
        | CLAUDE_DELEGATION_TOOLS
        | CLAUDE_PARENT_GATED_TOOLS
        | CLAUDE_CLOSED_TOOLS
        | task_boundary.EDIT_TOOLS
        | {"Bash"}
    )
    if tool in known or tool.startswith(tuple(f"mcp__{s}__" for s in APP_CLOSED_SERVERS)):
        return False
    # A Linear-named server keeps its inventory route and denied deletes whatever its
    # provenance; that route checks provenance before granting Linear authority.
    for provider in _claude_linear_providers():
        prefix = f"mcp__{provider}__"
        if tool.startswith(prefix):
            operation = tool[len(prefix) :]
            # Any other delete_ operation stays closed too, not only the named five.
            return operation not in LINEAR_PROVIDER_OPERATIONS and not operation.startswith(
                "delete_"
            )
    return True


def native_tool(event: core.JSONObject, host: str) -> str:
    """Validate task-free inputs or tools with correlated synchronous completion.

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
    # A parent tool that no route names is unchecked external work.
    if unchecked(event, host):
        return tool
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
    if tool in CLAUDE_DELEGATION_TOOLS:
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
    # Parent Shell metadata grants neither readiness nor terminal completion evidence.
    if host == "cursor" and tool == "Shell":
        if (
            event.get("child") is not True
            and set(args) <= {"command", "working_directory", "cwd", "timeout"}
            and isinstance(args.get("command"), str)
        ):
            # An ordinary command may run in any directory; shell-effect recognition checks it.
            cursor_shell_directory(args)
            return tool
        # Children and unsupported inputs retain their existing narrower contract.
        core.require(
            set(args) <= {"command", "working_directory"} and isinstance(args.get("command"), str),
            "HOST_UNSUPPORTED_ASYNC",
        )
        # A shell working-directory override shares the session's repository, or both
        # lie outside every repository.
        if "working_directory" in args:
            core.require(
                project_root(args["working_directory"]) == session_project_root(event),
                "REPOSITORY_MISMATCH",
            )
        return tool
    # A child may also hand its report back and only a parent moves worktrees; every
    # other host keeps its own set.
    supported = (
        CLAUDE_SYNC_TOOLS
        | CLAUDE_NO_EFFECT_TOOLS
        | (CLAUDE_CHILD_REPORT_TOOLS if event.get("child") is True else CLAUDE_PARENT_GATED_TOOLS)
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


def native_effect(event: core.JSONObject, host: str) -> core.JSONObject:
    """Validate native inputs and expose only documented shell effects to policy.

    Args:
        event: Identity-validated native envelope, retained unchanged for correlation.
        host: Explicit adapter identity. Codex retains its exact command-only policy.

    Returns:
        A copied canonical Bash effect for validated Claude Bash or Cursor Shell;
        otherwise the original event, including all Codex input metadata. The effect
        carries a top-level ``command_cwd``: advisory input for shell-effect
        recognition, never a replacement for the event's ``cwd``
        or repository. It is the event's ``cwd`` when no Cursor directory override
        is supplied, or None when one is: the directory is then unknown, and a
        recognized relative file effect must not become unchecked solely for that.

    Raises:
        core.WorkspaceError: If Claude or Cursor native tool validation fails.
    """
    # Codex has its own admission boundary; do not strip its unsupported shell metadata.
    if host == "codex":
        return event
    tool = native_tool(event, host)
    # Normalize validated shell effects; read metadata does not prove foreground completion.
    if (host, tool) in {("claude-code", "Bash"), ("cursor", "Shell")}:
        effect = {
            **event,
            "tool_name": "Bash",
            "tool_input": {"command": event["tool_input"]["command"]},
            "command_cwd": event.get("cwd"),
        }
        # Installed Cursor reported overrides it did not run in, so an override leaves
        # the command's directory unknown.
        if host == "cursor" and cursor_shell_directory(event["tool_input"]) is not None:
            effect["command_cwd"] = None
        return effect
    return event


class EditGate(NamedTuple):
    """Admission evidence for one structured edit with known targets.

    Attributes:
        decision: Effect classes from ``task_boundary.decide``.
        memo: Invocation-local repository discovery results.
        exemptions: The vault exemptions the decision used.
        policy: The reserved exemption policy path the decision used.
    """

    decision: task_boundary.Decision
    memo: repository_location.Memo
    exemptions: repository_exemptions.Exemptions
    policy: Path


def edit_gate(event: core.JSONObject, repository: core.Repository | None) -> EditGate | None:
    """Classify a structured edit's effects and refuse exemption policy administration.

    Args:
        event: Validated native tool and argument envelope.
        repository: The session's carried project repository, or None.

    Returns:
        The edit's admission evidence, or None for a non-edit tool, which keeps its
        existing admission.

    Raises:
        core.WorkspaceError: ``INVALID_REQUEST`` for an unknown or malformed edit
            shape, or ``POLICY_ADMINISTRATION`` if an effect targets the policy file.
    """
    effects = task_boundary.edit_effects(event, repository, task_boundary.Budget())
    if not effects:
        # An edit whose targets cannot be read must not fall back to readiness alone.
        core.require(event["tool_name"] not in task_boundary.EDIT_TOOLS, "INVALID_REQUEST")
        return None
    # Read the policy path at call time so a relocated policy is honored.
    policy = repository_exemptions.POLICY
    memo: repository_location.Memo = {}
    exemptions = repository_exemptions.load(policy, memo)
    decision = task_boundary.decide(effects, exemptions, policy)
    # Agents never edit the policy that exempts their own repositories.
    core.require(not decision.policy, "POLICY_ADMINISTRATION")
    return EditGate(decision, memo, exemptions, policy)


def edit_authority(
    gate: EditGate, ready: core.JSONObject, repository: core.Repository | None
) -> None:
    """Require the ready roadmap's approval and scope to cover a protected edit.

    Args:
        gate: The edit's admission evidence from ``edit_gate``.
        ready: Successful lifecycle ``ready`` result carrying ``approval``.
        repository: The session's carried project repository, or None.

    Raises:
        core.WorkspaceError: ``APPROVAL_REQUIRED`` without recorded approval of the
            current revision, or ``SCOPE_REQUIRED`` for an uncovered protected effect.
    """
    # Approval of the current proposal revision precedes any scope grant.
    approval = ready["approval"]
    core.require(approval["approved"] is True, "APPROVAL_REQUIRED")
    # Every protected effect must lie inside the approved repository scope.
    missing = task_boundary.unauthorized(
        gate.decision, approval["scope"], repository, gate.memo, gate.exemptions, gate.policy
    )
    core.require(not missing, "SCOPE_REQUIRED")


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
    effect = native_effect(event, host)
    # An unchecked parent call does no lifecycle work, not even a readiness check.
    if unchecked(event, host):
        return
    tool = event["tool_name"]
    # Select the provider operation and classify edit targets against the carried
    # session repository's worktrees.
    operation = (
        tool[4:]
        if host == "cursor" and tool.startswith("MCP:")
        else _linear_provider_tool(event, host)
    )
    repository = session_repository(event)
    roles = task_boundary.edit_roles(event, repository)
    gate = edit_gate(event, repository)
    # A structured parent edit without protected effects needs no task; a child's
    # edit keeps its scoped admission.
    if gate is not None:
        free = not gate.decision.protected and event.get("child") is not True
    else:
        free = task_boundary.task_free(effect, host, operation, roles)
    # Validated parent reads, planning and task-free effects need no lifecycle admission.
    if (
        host == "claude-code"
        and tool
        in CLAUDE_NO_EFFECT_TOOLS | {"mcp__ccd_turn__update_plan", "mcp__ccd_turn__wrap_up"}
        and event.get("child") is not True
    ) or free:
        return
    # Raw control-store mutation never becomes a readiness-authorized edit.
    core.require("control" not in roles, "NOT_OWNER")
    core.require(not lookup_required(event, host), "TICKET_READ_REQUIRED")
    request = request_for(event, "ready", host)
    result = core.execute(request)
    core.require(result["ok"], result["code"])
    # A protected structured edit also needs the roadmap's approval and scope.
    if gate is not None:
        edit_authority(gate, result, repository)
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

    Cursor Shell callbacks cannot prove termination. Their admitted work requires
    independently verified explicit lifecycle recovery.

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
    # An unchecked parent call was never admitted, so it has nothing to settle.
    if unchecked(event, host):
        return
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
    # Cursor can report exitCode zero before a backgrounded process terminates.
    if host == "cursor" and tool == "Shell":
        raise core.WorkspaceError("HOST_UNSUPPORTED_ASYNC")
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
    root = session_root(event)
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


def retry_on_contention(
    execute_attempt: Callable[[], core.JSONObject],
    deadline: float,
    *,
    retry_codes: frozenset[str] = CHILD_RETRY_CODES,
) -> core.JSONObject:
    """Retry only bounded lifecycle contention without owning a caller's request policy.

    Args:
        execute_attempt: One attempt, including any fresh snapshot and request construction.
        deadline: Absolute monotonic time after which no attempt may start.
        retry_codes: Returned or raised diagnostics that a further attempt may resolve.

    Returns:
        The successful attempt's unchanged lifecycle result.

    Raises:
        core.WorkspaceError: Nonretryable failure, exhausted attempts, or expired budget.
    """
    # Expiry before the first attempt must not start a snapshot or lifecycle mutation.
    code = "BUSY"
    attempts = 0
    while True:
        core.require(time.monotonic() < deadline, code)
        try:
            result = execute_attempt()
        except core.WorkspaceError as error:
            # A lock may report contention while reading the snapshot, before execute returns.
            if error.code not in retry_codes:
                raise
            code = error.code
        else:
            if result["ok"]:
                return result
            code = result["code"]
            core.require(code in retry_codes, code)
        # Keep the last retryable code and never sleep or execute beyond the caller's budget.
        attempts += 1
        remaining = deadline - time.monotonic()
        core.require(attempts < CHILD_JOIN_ATTEMPTS and remaining > 0, code)
        time.sleep(min(0.02 * attempts, remaining))


def _join_child(
    event: core.JSONObject, host: str, parent: core.JSONObject, deadline: float
) -> None:
    """Join a reader using a fresh revision and request identity on each bounded attempt.

    Args:
        event: Identity-validated SubagentStart envelope for the child.
        host: Explicit adapter identity.
        parent: Verified ready parent lifecycle request.
        deadline: Monotonic time after which no further join attempt starts.

    Raises:
        core.WorkspaceError: Nonretryable join failure, contention limit, or expired budget.
    """
    # The child key permits harmless replay when a previous attempt already published membership.
    child_key = core.participant_key({"host": host, "session_id": event["session_id"]})

    def execute_attempt() -> core.JSONObject:
        """Read current membership and join at its revision with a fresh request identity.

        Returns:
            Existing membership success, or the exact lifecycle join response.

        Raises:
            core.WorkspaceError: Snapshot failure or budget expiry before mutation.
        """
        state = _issue_state(parent)
        if child_key in state["participants"]:
            return {"ok": True, "code": "OK"}
        # Snapshot work must not consume the remaining budget and then start a late join.
        core.require(time.monotonic() < deadline, "BUSY")
        return core.execute(
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

    # Shared backoff owns timing only; this closure owns fresh snapshots and request IDs.
    retry_on_contention(execute_attempt, deadline)


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
    root = session_root(event)
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
        Bounded guidance naming the next action and any retained uncertainty.
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
            + " Independent authorized task-free reads, planning, edits outside the project "
            "code repositories and administration remain available."
        )
    # An unbound session starts or resumes only through the ticket-first route.
    if code == "BINDING_MISSING":
        return (
            "This session has no issue binding. Before protected implementation, submit "
            "exactly one `Task: <issue-id>` line, "
            "then read that ticket with the configured Linear get_issue call; startup "
            f"registers or resumes the workspace. A pre-registration diagnose uses {command}. "
            "Independent authorized task-free reads, planning, edits outside the project "
            "code repositories and administration remain available. "
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
    # Unsupported launch arguments and admitted completion uncertainty need different actions.
    if code in {"HOST_UNSUPPORTED_BACKGROUND", "HOST_UNSUPPORTED_ASYNC"}:
        return (
            "An execution denial needs supported foreground arguments without background "
            "execution or isolation. For an admitted call still pending, do not rerun it; "
            "inspect the original native terminal result. Only after independently verifying "
            "termination, use explicit lifecycle tool-complete for the exact recorded tool_id "
            "with completed: true. Otherwise retain pending work."
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
    # The exemption policy is user-administered and never agent-editable.
    if code == "POLICY_ADMINISTRATION":
        return (
            "The edit targets the vault exemption policy file. Agents cannot change it; "
            "the user edits it directly."
        )
    # Protected edits need the current proposal revision's recorded approval.
    if code == "APPROVAL_REQUIRED":
        return (
            "The roadmap does not record approval of its current proposal revision "
            "(Proposal status `Approved`, Approval record `approved revision <token>` "
            "matching Proposal revision). Get explicit human approval through the "
            "proposal lifecycle, then ready."
        )
    # Protected edits must lie inside the approved repository scope.
    if code == "SCOPE_REQUIRED":
        return (
            "A protected path is outside the roadmap's `Repository scope`. Add it through "
            "a new proposal revision, explicit approval and readiness."
        )
    # A working directory outside every repository has no protected checkout.
    if code == "REPOSITORY_ABSENT":
        return (
            "This working directory is inside no Git repository. Edits outside every Git "
            "repository, or in a declared vault, stay task-free; protected repository "
            "changes need a task started from inside that repository's checkout."
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
            "unchanged. Settle pending work through its supported completion or verified "
            "recovery route, or have attached participants detach (an issue's coordinator moves "
            "only after its other participants detach), then retry. To keep working meanwhile, "
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
            "SendMessage or AskUserQuestion (a parent also has EnterWorktree and ExitWorktree, "
            "a subagent SubagentHandback)"
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
                "calls, Task, TaskOutput, TaskStop, SpawnAgent, Workflow and children of an "
                "unready parent stay unsupported; perform that step in the parent session."
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
