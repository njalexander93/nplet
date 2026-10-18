#!/usr/bin/env python3
"""Local, cooperative issue workspaces. No network calls or runtime authority.

All store access uses the platform no-follow filesystem boundary. A persistent OS lock fences
supported writers; a durable roll-forward intent couples payload, events and state.
See docs/runtime/task-workspace.md for the public request contract and
docs/runtime/task-workspace-usage.md for host limitations.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from nplet.lifecycle._errors import WorkspaceError, require
from nplet.lifecycle._filesystem import Directory as NativeDirectory

CURSOR_INTERNAL_OPERATIONS = frozenset(
    {"cursor-admit", "cursor-bind-child", "cursor-retire", "cursor-source-callback"}
)

MAX_REQUEST = 96 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_EVENTS = 16 * 1024 * 1024
MAX_ARCHIVE = 32 * 1024 * 1024
DOCUMENT_LIMIT = 256 * 1024
ARCHIVE_CHUNK = 64 * 1024
ISSUE = re.compile(r"[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,9}", re.ASCII)
NOTE = re.compile(r"context/[a-z0-9][a-z0-9._-]{0,79}\.md", re.ASCII)
SEGMENT = re.compile(r"events-([0-9]{12})-([0-9]{12})\.jsonl", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}")
TERMINAL = {"completed", "cancelled", "failed"}
FAILPOINT: Callable[[str], None] | None = (
    None  # Test-only callable; never accepted in requests or environment.
)


# JSON objects remain dynamic at CLI, hook, and persisted-document boundaries.
# Byte inventories and validated participant/reference records use concrete shapes.
JSONObject = dict[str, Any]
PayloadFiles = dict[str, bytes]


class EvidenceRef(TypedDict):
    """Identify attributable evidence by locator and exact content digest."""

    id: str
    locator: str
    sha256: str


class SourceRef(EvidenceRef):
    """Describe one explicitly assigned reader-scoped source."""

    required: bool
    authority: str
    reason: str
    stage: str
    reader: str


class SourceAvailability(SourceRef):
    """Report whether the assigned source still matches its recorded digest."""

    available: bool


class PendingOperation(TypedDict):
    """Track a process or a separately admitted polling transport."""

    status: str
    handle: NotRequired[str]
    poll_parent: NotRequired[str]
    poll_handle: NotRequired[str]
    cursor_invocation: NotRequired[str]
    cursor_source: NotRequired[JSONObject]


class Participant(TypedDict):
    """Retain participant generation, assigned sources, and unsettled work."""

    generation: int
    status: str
    pending: dict[str, PendingOperation]
    packet: list[SourceRef] | None
    ack: str | None
    cursor_invocation: NotRequired[str]
    cursor_sources: NotRequired[dict[str, JSONObject]]


class RequestRecord(TypedDict):
    """Retain the digest and result needed to recognize an exact request retry."""

    digest: str
    result: JSONObject


class Assignment(TypedDict):
    """Assign a source packet before a participant attaches."""

    packet: list[SourceRef]


class WorkspaceState(TypedDict):
    """Represent the persisted lifecycle state after loading its JSON boundary."""

    schema_version: int
    repo_id: str
    issue_id: str
    issue_uuid: str
    revision: int
    generation: int
    disposition: str
    storage: str
    coordinator: str
    owners: dict[str, str]
    participants: dict[str, Participant]
    files: dict[str, str]
    seq: int
    head: str | None
    requests: dict[str, RequestRecord]
    provenance: NotRequired[dict[str, JSONObject]]
    directory_identity: NotRequired[list[int]]
    assignments: NotRequired[dict[str, Assignment]]
    optional_loss_count: NotRequired[int]
    adoption: NotRequired[JSONObject | None]
    checkpoint: NotRequired[JSONObject | None]
    checkpoint_history: NotRequired[list[JSONObject]]
    outcome: NotRequired[JSONObject]
    outcome_history: NotRequired[list[JSONObject]]
    event_segments: NotRequired[list[JSONObject]]
    archive: NotRequired[JSONObject]
    export: NotRequired[JSONObject]
    index_request: NotRequired[JSONObject]
    provider_saves: NotRequired[dict[str, JSONObject]]
    cleanup_challenge: NotRequired[str]
    tombstone: NotRequired[JSONObject]
    history: NotRequired[list[JSONObject]]
    reconciliation: NotRequired[JSONObject]
    submitted_pr: NotRequired[str]
    provider_completion: NotRequired[JSONObject]
    cursor_plans: NotRequired[dict[str, JSONObject]]
    cursor_invocations: NotRequired[dict[str, JSONObject]]


class DocumentRequest(TypedDict):
    """Specify the exact provider write allowed for an immutable archive document."""

    title: str
    issue: str
    content: str


class ExportDocuments(TypedDict):
    """Pair an immutable snapshot digest with its ordered provider write requests."""

    snapshot: str
    parts: list[DocumentRequest]


def canonical(value: object) -> bytes:
    """Serialize a value into the deterministic UTF-8 form used by digests.

    Args:
        value: JSON-serializable value whose deterministic representation is required.

    Returns:
        Canonical JSON bytes.

    Raises:
        ValueError: If a value cannot be represented as finite JSON.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha(data: bytes) -> str:
    """Identify exact bytes with a hexadecimal SHA-256 digest.

    Args:
        data: Exact bytes whose content identity is required.

    Returns:
        The lowercase 64-character digest.
    """
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    """Record the current UTC time for local lifecycle evidence.

    Returns:
        An ISO 8601 timestamp with timezone.
    """
    return datetime.now(timezone.utc).isoformat()


def fault(point: str) -> None:
    """Invoke the process-local test failpoint without accepting external hooks.

    Args:
        point: Named internal transaction boundary for a test injection.

    Raises:
        Exception: Propagated from an installed test failpoint.
    """
    # Run only an explicitly installed process-local test injection.
    if FAILPOINT:
        FAILPOINT(point)


def token(value: str) -> str:
    """Validate a bounded printable ASCII identity token.

    Args:
        value: Identity token received from a request or persisted record.

    Returns:
        The validated token.

    Raises:
        WorkspaceError: If the token is empty, oversized or contains unsafe characters.
    """
    # Reject empty, oversized or non-printable identity tokens before returning them.
    require(
        isinstance(value, str) and 0 < len(value) <= 160 and all(32 < ord(c) < 127 for c in value)
    )
    return value


def issue_id(value: str) -> str:
    """Validate the strict issue identifier used as a directory component.

    Args:
        value: Human issue identifier, such as TEAM-123.

    Returns:
        The validated issue identifier.

    Raises:
        WorkspaceError: If the identifier violates the issue grammar.
    """
    # Validate the identifier before it can become an issue-directory component.
    require(isinstance(value, str) and ISSUE.fullmatch(value), "INVALID_ISSUE")
    return value


def payload_path(value: str, events: bool = False) -> str:
    """Allow only supported issue payload paths and optional event files.

    Args:
        value: Relative roadmap, context-note or event-stream locator.
        events: Whether the event stream and immutable segment paths are permitted.

    Returns:
        The validated relative path.

    Raises:
        WorkspaceError: If the path is outside the payload grammar.
    """
    # Restrict payload access to the documented relative-path grammar.
    require(
        isinstance(value, str)
        and ".." not in value
        and (
            value == "roadmap.md"
            or NOTE.fullmatch(value)
            or (events and (value == "events.jsonl" or SEGMENT.fullmatch(value)))
        ),
        "UNSAFE_PATH",
    )
    return value


def decode(value: str, limit: int = MAX_FILE) -> bytes:
    """Decode strict base64 while enforcing the decoded byte budget.

    Args:
        value: Strict base64 text to decode.
        limit: Maximum allowed file or decoded byte count.

    Returns:
        The decoded bytes.

    Raises:
        WorkspaceError: If encoding or size validation fails.
    """
    # Bound encoded input before allocating decoded bytes.
    require(isinstance(value, str) and len(value) <= (limit + 2) // 3 * 4, "SIZE_LIMIT")
    # Decode only strict base64 and translate malformed input to an integrity failure.
    try:
        # Reject invalid base64 before checking the decoded byte budget.
        result = base64.b64decode(value, validate=True)
    # Report malformed base64 without exposing decoder details.
    except ValueError:
        raise WorkspaceError("INTEGRITY_ERROR") from None
    # Enforce the decoded budget independently of the encoded-length bound.
    require(len(result) <= limit, "SIZE_LIMIT")
    return result


def encode(value: bytes) -> str:
    """Encode bytes for reconstructable JSON archive and transaction records.

    Args:
        value: Exact bytes to represent without loss.

    Returns:
        ASCII base64 text.
    """
    return base64.b64encode(value).decode("ascii")


def strict_json(data: bytes | str) -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite constants.

    Args:
        data: JSON bytes or text received from a bounded source.

    Returns:
        The decoded JSON value.

    Raises:
        WorkspaceError: If the input is invalid or ambiguous JSON.
    """

    def pairs(items: list[tuple[str, Any]]) -> JSONObject:
        """Build one JSON object without permitting duplicate field names.

        Args:
            items: Ordered key/value pairs from the JSON parser.

        Returns:
            The unique-key object.

        Raises:
            WorkspaceError: If any key occurs more than once.
        """
        # Allocate one object for the parser-supplied key/value sequence.
        result = {}
        # Reject duplicate keys instead of silently accepting the last value.
        for key, value in items:
            require(key not in result, "INVALID_REQUEST")
            result[key] = value
        return result

    # Parse with duplicate-key and non-finite-number rejection enabled.
    try:
        return json.loads(
            data,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(WorkspaceError("INVALID_REQUEST")),
        )
    # Translate syntax and encoding failures into the public request diagnostic.
    except (ValueError, UnicodeError):
        raise WorkspaceError("INVALID_REQUEST") from None


class Directory(NativeDirectory):
    """Add canonical lifecycle JSON to the platform filesystem boundary."""

    def json(self, name: str) -> Any:
        """Read a bounded safe file and decode strict JSON.

        Args:
            name: Direct file name under this opened directory.

        Returns:
            The decoded JSON value, with duplicate keys and non-finite values rejected.

        Raises:
            WorkspaceError: If the file is unsafe, oversized or contains invalid JSON.
            OSError: If the file cannot be read.
        """
        # Read through the filesystem boundary before parsing untrusted JSON.
        return strict_json(self.read(name))

    def put(self, name: str, value: object) -> None:
        """Publish canonical JSON through the platform atomic file writer.

        Args:
            name: Direct destination file name under this opened directory.
            value: JSON-serializable value to publish atomically.

        Raises:
            WorkspaceError: If the destination violates the filesystem boundary.
            ValueError: If the value contains non-finite numbers.
            TypeError: If the value cannot be serialized as JSON.
            OSError: If atomic publication fails.
        """
        # Canonicalize the value and atomically replace the validated destination.
        self.write(name, canonical(value))


def git(path: str | Path, *args: str) -> str:
    """Run a bounded Git query without an interactive prompt.

    Args:
        path: Directory in the checkout whose Git metadata is queried.
        args: Git argument words passed without shell interpolation.

    Returns:
        Stripped UTF-8 command output.

    Raises:
        WorkspaceError: If Git returns a nonzero status.
        subprocess.SubprocessError: If Git exceeds the timeout.
        OSError: If Git cannot be launched.
    """
    # Query Git with separate arguments and a bounded runtime.
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=2,
        check=False,
    )
    require(result.returncode == 0, "REPOSITORY_MISMATCH")
    return result.stdout.strip()


def repository(path: str | Path) -> tuple[Path, Path, list[Path]]:
    """Resolve a non-bare worktree to its common Git directory and member roots.

    Args:
        path: Directory inside the requested Git worktree.

    Returns:
        A tuple of worktree root, common Git directory and worktree paths.

    Raises:
        WorkspaceError: If the repository is unsupported or membership is inconsistent.
        subprocess.SubprocessError: If Git metadata cannot be queried.
    """
    # Resolve the worktree and common Git directory before checking membership.
    root = Path(git(path, "rev-parse", "--show-toplevel"))
    require(git(root, "rev-parse", "--is-bare-repository") == "false", "HOST_UNSUPPORTED")
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    trees = [
        Path(line[9:])
        for line in git(root, "worktree", "list", "--porcelain", "-z").split("\0")
        if line.startswith("worktree ")
    ]
    require(root in trees, "REPOSITORY_MISMATCH")
    return root, common, trees


def fs_identity(path: str | Path) -> list[int]:
    """Read an absolute directory device/inode identity through safe traversal.

    Args:
        path: Absolute directory path to traverse without following links.

    Returns:
        The directory device and inode pair.

    Raises:
        WorkspaceError: If directory validation fails.
        OSError: If the path cannot be opened.
    """
    # Open the directory through the platform no-follow traversal boundary.
    with Directory.absolute(path) as directory:
        return directory.identity


def register(request: JSONObject) -> JSONObject:
    """Register a main-worktree store and optional explicit startup assignment.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The repository identity and successful registration result.

    Raises:
        WorkspaceError: If repository identities or startup scope conflict.
        OSError: If store creation or persistence fails.
    """
    # Verify that the requested main directory belongs to this worktree family.
    root, common, trees = repository(request["worktree"])
    main = Path(request["main_worktree"])
    require(main == trees[0] and main in trees, "REPOSITORY_MISMATCH")
    require(repository(main)[1] == common, "REPOSITORY_MISMATCH")
    # Bind registration to both paths and filesystem identities.
    identity = {
        "schema_version": 1,
        "common": str(common),
        "main": str(main),
        "common_identity": fs_identity(common),
        "main_identity": fs_identity(main),
    }
    # Serialize shared registration before publishing either registration copy.
    with (
        Directory.absolute(main) as directory,
        directory.child(".task", True) as task,
        task.child(".control", True) as control,
        control.lock("registration.lock"),
    ):
        # Reuse registration only when every canonical identity still matches.
        if control.exists("repository.json"):
            existing = control.json("repository.json")
            require(all(existing[k] == v for k, v in identity.items()), "REPOSITORY_MISMATCH")
            identity = existing
        else:
            # Allocate a repository identity only when no canonical registration exists.
            identity["repo_id"] = str(uuid.uuid4())
            control.put("repository.json", identity)
        # Publish the matching registration in the selected worktree.
        with Directory.absolute(root) as worktree, worktree.child(".task", True) as local:
            # Validate any existing entry before reusing or replacing it.
            if local.exists(".repository.json"):
                require(local.json(".repository.json") == identity, "REPOSITORY_MISMATCH")
            local.put(".repository.json", identity)
            # Persist only an explicit coordinator-owned startup packet for this session.
            if request.get("startup") is not None:
                setup = request["startup"]
                require(
                    isinstance(setup, dict)
                    and set(setup) == {"issue_id", "issue_uuid", "packet", "coordinator"}
                )
                issue_id(setup["issue_id"])
                token(setup["issue_uuid"])
                key = participant_key(request)
                require(setup["coordinator"] == key, "NOT_OWNER")
                validate_packet(setup["packet"])
                require(all(ref["reader"] == key for ref in setup["packet"]), "SCOPE_MISSING")
                # Open the local session-binding directory for the explicit startup assignment.
                with local.child(".bindings", True) as bindings:
                    name = key + ".startup.json"
                    # Validate any existing entry before reusing or replacing it.
                    if bindings.exists(name):
                        require(bindings.json(name) == setup, "BINDING_CONFLICT")
                    bindings.put(name, setup)
    return {"ok": True, "code": "REGISTERED", **identity}


class Store:
    """Own validated descriptors connecting one worktree to its shared task store."""

    def __init__(self, request: JSONObject) -> None:
        """Open the registered shared store and verify all repository identities.

        Args:
            request: Versioned lifecycle request with explicit repository, issue and
                session identities.

        Raises:
            WorkspaceError: If local and canonical registration or filesystem identities differ.
            OSError: If a store directory cannot be opened.
        """
        # Track opened handles so partial store initialization can release them.
        self.request = request
        self.root, common, trees = repository(request["worktree"])
        self.handles: list[Directory] = []
        # Open and validate store descriptors within a cleanup-protected lifetime.
        try:
            self.local_root = self.keep(Directory.absolute(self.root))
            self.local = self.keep(self.local_root.child(".task"))
            # Read repository registration through validated directory handles.
            self.registration = self.local.json(".repository.json")
            reg = self.registration
            require(request.get("repo_id") == reg["repo_id"], "REPOSITORY_MISMATCH")
            require(
                str(common) == reg["common"]
                and fs_identity(common) == reg["common_identity"]
                and Path(reg["main"]) == trees[0],
                "REPOSITORY_MISMATCH",
            )
            self.main = self.keep(Directory.absolute(reg["main"]))
            require(self.main.identity == reg["main_identity"], "REPOSITORY_MISMATCH")
            self.task = self.keep(self.main.child(".task"))
            self.control = self.keep(self.task.child(".control"))
            require(self.control.json("repository.json") == reg, "REPOSITORY_MISMATCH")
            self.issues = self.keep(self.control.child("issues", True))
            self.bindings = self.keep(self.local.child(".bindings", True))
        # Release partially opened descriptors before propagating initialization failure.
        except BaseException:
            self.close()
            raise

    def keep(self, directory: Directory) -> Directory:
        """Track a descriptor for reverse-order store cleanup.

        Args:
            directory: Opened Directory handle for the relevant payload or store.

        Returns:
            The supplied directory handle.
        """
        # Retain ownership of the opened descriptor until store cleanup.
        self.handles.append(directory)
        return directory

    def close(self) -> None:
        """Release all tracked descriptors in reverse opening order.

        Raises:
            OSError: If a tracked descriptor cannot be closed.
        """
        # Close child descriptors before their parents, including partial initialization.
        for directory in reversed(self.handles):
            directory.close()
        # Clear descriptor ownership after reverse-order cleanup completes.
        self.handles = []

    def __enter__(self) -> Store:
        """Expose the validated store within a managed lifetime.

        Returns:
            This store.
        """
        return self

    def __exit__(self, *args: object) -> None:
        """Release store descriptors when the managed lifetime ends.

        Args:
            args: Context-manager exception triple, ignored during descriptor cleanup.

        Raises:
            OSError: If descriptor cleanup fails.
        """
        # Release the store handles when the context manager exits.
        self.close()

    def binding(self) -> JSONObject | None:
        """Read this host/session binding without inventing an assignment.

        Returns:
            The binding object, or None when it does not exist.

        Raises:
            WorkspaceError: If stored binding data is unsafe or malformed.
            OSError: If binding access fails.
        """
        # Select only the binding file for this explicit host/session identity.
        name = participant_key(self.request) + ".json"
        return self.bindings.json(name) if self.bindings.exists(name) else None

    def save_binding(self, state: WorkspaceState, participant: str) -> None:
        """Persist the committed issue and participant generation for this worktree.

        Args:
            state: Current or staged issue control state.
            participant: Participant key whose committed generation is recorded.

        Raises:
            WorkspaceError: If binding publication violates file safety checks.
            OSError: If the binding cannot be persisted.
        """
        # Persist the issue and committed participant generation together.
        self.bindings.put(
            participant + ".json",
            {
                "repo_id": self.registration["repo_id"],
                "issue_id": state["issue_id"],
                "participant_id": participant,
                "binding_generation": state["participants"][participant]["generation"],
                **(
                    {"cursor_invocation": state["participants"][participant]["cursor_invocation"]}
                    if "cursor_invocation" in state["participants"][participant]
                    else {}
                ),
            },
        )

    def view(self, issue: str) -> None:
        """Expose only the assigned canonical issue through a verified worktree link.

        Args:
            issue: Validated issue ID whose canonical payload must be exposed.

        Raises:
            WorkspaceError: If an existing view targets an unexpected location.
            OSError: If the link or canonical directory cannot be accessed.
        """
        # The main worktree already exposes the canonical payload.
        if str(self.root) == self.registration["main"]:
            return
        # Resolve this linked worktree view to the canonical issue directory.
        target = str(Path(self.registration["main"]) / ".task" / issue)
        self.local.issue_view(issue, target)
        # Verify that the canonical issue directory can be opened safely.
        with self.task.child(issue):
            pass


def participant_key(request: JSONObject) -> str:
    """Derive a stable participant key from explicit host and session tokens.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The SHA-256 identity key.

    Raises:
        WorkspaceError: If either identity token is invalid.
    """
    return sha(canonical([token(request.get("host", "codex")), token(request["session_id"])]))


def os_metadata(name: str) -> bool:
    """Recognize only Finder metadata names excluded from task payload content.

    Args:
        name: Single issue-root or context entry name, not a path.

    Returns:
        Whether the name is .DS_Store or a nonempty AppleDouble name.
    """
    return name == ".DS_Store" or (name.startswith("._") and len(name) > 2)


def inventory(directory: Directory, require_roadmap: bool = True) -> PayloadFiles:
    """Read supported payload files while safely validating excluded OS metadata.

    Args:
        directory: Opened Directory handle for the relevant payload or store.
        require_roadmap: Whether an absent roadmap must stop inventory loading.

    Returns:
        Relative payload paths mapped to exact bytes.

    Raises:
        WorkspaceError: If a payload path, metadata file or required roadmap is unsafe.
        OSError: If enumeration or file access fails.
    """
    # Collect only validated task payload bytes from this issue directory.
    result = {}
    # Inspect only direct issue entries under the opened directory descriptor.
    for name in directory.names():
        # Validate narrow metadata entries before excluding them from task content.
        if os_metadata(name):
            directory.read(name, MAX_FILE)
            continue
        # Inspect the supported context directory without allowing arbitrary descendants.
        if name == "context":
            with directory.child(name, private=False) as context:
                # Validate each direct context entry without descending into arbitrary directories.
                for note in context.names():
                    # Validate narrow metadata entries before excluding them from task content.
                    if os_metadata(note):
                        context.read(note, MAX_FILE)
                        continue
                    path = payload_path("context/" + note)
                    result[path] = context.read(note, MAX_FILE)
        else:
            # Validate and read an allowed issue-root payload or event file.
            payload_path(name, events=True)
            result[name] = directory.read(name, MAX_FILE)
    # Require the roadmap unless the caller is inspecting an incomplete transaction.
    require(not require_roadmap or "roadmap.md" in result, "RECOVERY_REQUIRED")
    return result


def manifest(files: PayloadFiles) -> dict[str, str]:
    """Derive a stable path-to-digest inventory from exact payload bytes.

    Args:
        files: Relative payload paths mapped to exact bytes.

    Returns:
        Sorted relative paths mapped to SHA-256 digests.
    """
    return {path: sha(data) for path, data in sorted(files.items())}


def write_payload(directory: Directory, path: str, data: bytes) -> None:
    """Publish one validated root or context payload file.

    Args:
        directory: Opened Directory handle for the relevant payload or store.
        path: Validated relative roadmap, context-note or event-stream destination.
        data: Complete replacement bytes for the selected payload file.

    Raises:
        WorkspaceError: If the payload path or destination is unsafe.
        OSError: If directory creation or file publication fails.
    """
    # Validate the complete relative destination before choosing a parent directory.
    payload_path(path, events=True)
    # Route context writes through the opened context directory, not arbitrary traversal.
    if path.startswith("context/"):
        # Open the context parent before publishing its direct child file.
        with directory.child("context", True, private=False) as context:
            context.write(path[8:], data)
    else:
        # Publish a validated issue-root file without traversing arbitrary paths.
        directory.write(path, data)


def validate_events(data: bytes, seq: int = 0, prior: str | None = None) -> tuple[int, str | None]:
    """Validate complete event lines against sequence and digest-chain anchors.

    Args:
        data: Newline-delimited event records extending the supplied chain anchors.
        seq: Sequence immediately before these lines, or zero for initial history.
        prior: Digest immediately before these lines, or None at history start.

    Returns:
        The final sequence number and digest.

    Raises:
        WorkspaceError: If a line, sequence or chain digest is invalid.
    """
    # Reject an incomplete event tail before validating individual records.
    require(not data or data.endswith(b"\n"), "INTEGRITY_ERROR")
    # Validate every complete event against the expected sequence and prior digest.
    for line in data.splitlines():
        require(len(line) <= 8192, "INTEGRITY_ERROR")
        event = strict_json(line)
        digest = event.pop("digest", None)
        require(
            event["seq"] == seq + 1
            and event["prev_digest"] == prior
            and sha(canonical(event)) == digest,
            "INTEGRITY_ERROR",
        )
        prior, seq = digest, seq + 1
    return seq, prior


def validate_history(files: PayloadFiles) -> tuple[int, str | None]:
    """Validate retained segments and the active stream as one continuous chain.

    Args:
        files: Relative payload paths mapped to exact bytes.

    Returns:
        The global final sequence number and digest.

    Raises:
        WorkspaceError: If segment ranges or event-chain integrity fail.
    """
    # Start retained-history validation from the initial chain anchor.
    seq, head = 0, None
    # Validate retained segment ranges before continuing into the active stream.
    for path in sorted(p for p in files if SEGMENT.fullmatch(p)):
        segment = SEGMENT.fullmatch(path)
        assert segment is not None  # The loop selects only matching segment paths.
        first, last = map(int, segment.groups())
        require(first == seq + 1 and last >= first, "INTEGRITY_ERROR")
        seq, head = validate_events(files[path], seq, head)
        require(seq == last, "INTEGRITY_ERROR")
    # Continue the validated segment chain through the active event stream.
    return validate_events(files.get("events.jsonl", b""), seq, head)


def settlement_reserve(state: WorkspaceState) -> tuple[int, int]:
    """Derive remaining Cursor settlement bytes and all pending event slots.

    Args:
        state: Complete post-transition state, including the current request result.

    Returns:
        Conservative positive state growth and future required event count. No counter
        is persisted; the obligation disappears only with its committed settlement.
    """
    # Count mandatory bind/content transitions separately from their final completion.
    invocations = state.get("cursor_invocations", {})
    active = [(key, item) for key, item in invocations.items() if item["phase"] != "retired"]
    slots = sum(2 if item["phase"] == "admitted" else 1 for _, item in active)
    for member in state["participants"].values():
        for pending in member["pending"].values():
            if "cursor_invocation" in pending:
                continue
            if "cursor_source" in pending:
                slots += 1 if pending["cursor_source"].get("provisional") is not None else 2
            else:
                slots += 1 if pending["status"] == "unknown" else 2
    # Bound one stored completion result using real repository identifiers and counter sizes.
    digest = "f" * 64
    generation = (
        max(
            [member["generation"] for member in state["participants"].values()]
            + [item.get("resume_generation") or 0 for _, item in active],
            default=0,
        )
        + 1
    )
    result = {
        "ok": True,
        "code": "OK",
        "revision": state["revision"] + slots + 1,
        "repo_id": state["repo_id"],
        "issue_id": state["issue_id"],
        "binding_generation": generation,
        "invocation_id": digest,
        "source_content_verified": False,
        "source_receipt": False,
    }
    request_bytes = len(canonical({digest: {"digest": digest, "result": result}}))
    reserved = 0
    # Token characters can need two JSON bytes; native text can need six per UTF8 byte.
    token_bound = "\\" * 160
    for identifier, invocation in active:
        terminal = {
            "parent_conversation": invocation["parent_conversation"],
            "native_call": invocation["native_call"],
            "storage_key": "\x01" * 2048,
            "child_conversation": token_bound,
            "kind": "foreground-success",
            "is_background": False,
            "background_reason": 0,
            "duration_ms": (1 << 64) - 1,
            "binary_sha256": digest,
            "executor_sha256": digest,
            "workbench_sha256": digest,
        }
        projected_member = {
            "generation": generation,
            "status": "detached",
            "pending": {},
            "packet": None,
            "ack": None,
            "cursor_invocation": identifier,
        }
        identity = {
            "phase": "retired",
            "child_conversation": token_bound,
            "child_participant": digest,
            "child_generation": generation,
            "native_generation": token_bound,
            "terminal_result": terminal,
        }
        # Include terminal-only classification even when an existing bound member will shrink.
        reserved += len(
            canonical({"participants": {digest: projected_member}, "invocation": identity})
        )
        reserved += request_bytes
        if invocation["phase"] == "admitted":
            packet = [{**ref, "reader": digest} for ref in invocation["packet"]]
            projected_member = {**projected_member, "status": "attached", "packet": packet}
            # Binding installs two copies and exact owned paths, not a guessed small slot.
            additions = {
                "assignments": {digest: {"packet": packet}},
                "participants": {digest: projected_member},
                "owners": {path: digest for path in invocation["owned_paths"]},
                "invocation": {
                    key: value for key, value in identity.items() if key != "terminal_result"
                },
            }
            reserved += len(canonical(additions)) + request_bytes
    # A source receipt uses the actual locator length; absolute locators are not token-bounded.
    for member in state["participants"].values():
        for tool, pending in member["pending"].items():
            if "cursor_invocation" in pending or "cursor_invocation" not in member:
                continue
            if "cursor_source" in pending:
                source = pending["cursor_source"]
                receipt = {
                    "packet_digest": source["packet_digest"],
                    "sha256": source["sha256"],
                    "invocation_id": source["invocation_id"],
                }
                reserved += len(canonical({"cursor_sources": {source["locator"]: receipt}}))
                reserved += request_bytes
                if source.get("provisional") is None:
                    proof = {"sha256": digest, "content_length": MAX_FILE}
                    reserved += len(canonical({"provisional": proof})) + request_bytes
            else:
                # Ordinary child transports retain room for unknown-handle and completion facts.
                count = 1 if pending["status"] == "unknown" else 2
                reserved += count * request_bytes
                reserved += len(canonical({tool: {"status": "unknown", "handle": token_bound}}))
    # Account for growth of the shared counters/head while the reserved facts are written.
    if reserved:
        reserved += len(
            canonical(
                {
                    "revision": state["revision"] + slots + 1,
                    "seq": state["seq"] + slots + 1,
                    "head": digest,
                }
            )
        )
    return reserved, slots


class Issue:
    """Coordinate one issue payload with its recoverable control state."""

    def __init__(self, store: Store, control: Directory, identifier: str) -> None:
        """Load the current issue state from its validated control directory.

        Args:
            store: Validated shared repository Store.
            control: Opened control Directory for this issue.
            identifier: Validated issue ID or explicit tool-call identity.

        Raises:
            WorkspaceError: If existing state is unsafe or invalid JSON.
            OSError: If state access fails.
        """
        # Retain the validated handles and identity for this issue lifetime.
        self.store, self.control, self.id = store, control, identifier
        # Load existing control state only when its state file is present.
        self.state: WorkspaceState | None = (
            control.json("state.json") if control.exists("state.json") else None
        )

    def committed_state(self) -> WorkspaceState:
        """Read state after a caller has established successful recovery or commit.

        Returns:
            The loaded committed state.

        Raises:
            AssertionError: If an internal caller violates the established-state contract.
        """
        # Enforce the internal recovery/commit precondition before exposing state.
        assert self.state is not None
        return self.state

    def files(self) -> PayloadFiles:
        """Read the committed payload and quarantine only a provable interrupted tail.

        Returns:
            Validated payload paths and exact bytes.

        Raises:
            WorkspaceError: If identity, manifest or event integrity fails.
            OSError: If payload access or tail quarantine fails.
        """
        # Require a retained payload before inspecting its committed inventory.
        require(self.state and self.state["storage"] != "cleaned", "RECOVERY_REQUIRED")
        assert self.state is not None  # Established by the recovery guard above.
        # Open and validate the canonical payload under its retained directory handle.
        with self.store.task.child(self.id, private=False) as payload:
            require(payload.identity == self.state["directory_identity"], "UNSAFE_PATH")
            result = inventory(payload)
        # Reconcile only an interrupted tail that reproduces the exact committed manifest.
        if manifest(result) != self.state["files"]:
            data = result.get("events.jsonl", b"")
            end = data.rfind(b"\n") + 1
            prefix, tail = data[:end], data[end:]
            corrected = {**result, "events.jsonl": prefix}
            # Quarantine only uncommitted tail bytes; never truncate an altered committed prefix.
            if tail and manifest(corrected) == self.state["files"]:
                self.control.write("interrupted-tail-" + sha(tail), tail)
                # Replace only the interrupted event tail after its prefix was verified.
                with self.store.task.child(self.id, private=False) as payload:
                    payload.write("events.jsonl", prefix)
                result = corrected
                self.control.put(
                    "diagnostic-loss.json", {"discarded_tail_bytes": len(tail), "at": now()}
                )
            else:
                # Retain altered payloads that cannot be explained by an interrupted tail.
                raise WorkspaceError("UNTRACKED_CHANGE")
        # Verify the recovered payload reproduces the committed event-chain head.
        seq, head = validate_history(result)
        require(seq == self.state["seq"] and head == self.state["head"], "INTEGRITY_ERROR")
        return result

    def recover(self) -> None:
        """Roll a durable transaction forward only over old or intended file bytes.

        Raises:
            WorkspaceError: If recovery encounters foreign content or inconsistent identity.
            OSError: If publication or durable cleanup fails.
        """
        # Skip recovery when no durable transaction intent exists.
        if not self.control.exists("transaction.json"):
            return
        # Load the durable recovery intent and its intended state.
        tx = self.control.json("transaction.json")
        state = tx["state"]
        # Before any publication, prove every file is either its old or staged version.
        if not self.store.task.exists(self.id):
            require(tx["base"] is None, "RECOVERY_REQUIRED")
            # Create a missing payload directory only for an initialization transaction.
            with self.store.task.child(self.id, True, private=False):
                pass
        # Open and validate the canonical payload under its retained directory handle.
        with self.store.task.child(self.id, private=False) as payload:
            # For an existing payload, require the original directory identity.
            if tx["base"] is not None:
                require(payload.identity == tx["directory_identity"], "UNSAFE_PATH")
            # Inventory partial payload bytes before applying staged writes.
            present = inventory(payload, require_roadmap=False)
            require(set(present) <= set(state["files"]), "UNTRACKED_CHANGE")
            # Prove each present file matches either the old or intended transaction digest.
            for path, data in present.items():
                old = (tx["base"] or {}).get(path)
                require(sha(data) in {old, state["files"][path]}, "UNTRACKED_CHANGE")
            # Publish staged files through the same safe payload writer used by normal commits.
            for path, data in tx["writes"].items():
                write_payload(payload, path, decode(data))
                fault("payload:" + path)
            # Ensure the context directory exists and flush the complete intended payload.
            payload.child("context", True, private=False).close()
            require(manifest(inventory(payload)) == state["files"], "RECOVERY_REQUIRED")
            state["directory_identity"] = payload.identity
            payload.flush()
        # Publish state only after payload durability; remove the intent last so recovery can retry.
        fault("payload-flush")
        self.control.put("state.json", state)
        fault("state")
        self.control.unlink("transaction.json")
        fault("transaction-complete")
        self.state = state

    def commit(
        self,
        state: WorkspaceState,
        files: PayloadFiles,
        request: JSONObject,
        result: JSONObject,
        event_type: str | None = None,
    ) -> JSONObject:
        """Commit payload, event, state and idempotency result through one recovery intent.

        Args:
            state: Current or staged issue control state.
            files: Relative payload paths mapped to exact bytes.
            request: Versioned lifecycle request with explicit repository, issue and
                session identities.
            result: Operation result to persist with its idempotency record.
            event_type: Required lifecycle fact, optional observation, or None for no event.

        Returns:
            The committed operation result.

        Raises:
            WorkspaceError: If capacity, request or prior-payload checks fail.
            OSError: If the transaction cannot be durably published or recovered.
        """
        # Validate the old committed inventory before staging any replacement bytes.
        old_files = self.files() if self.state else {}
        # Append a lifecycle fact only when this operation changes recorded history.
        if event_type:
            require(
                request.get("event") is None or request["operation"] == "event", "INVALID_REQUEST"
            )
            optional = event_type == "observation"
            # Reserve async-transition and completion events for every admitted pending tool.
            _, pending_slots = settlement_reserve(state)
            reserved = 8192 * (1 + pending_slots)
            # Suppress optional observations before they consume settlement reserves.
            if optional and len(files.get("events.jsonl", b"")) + reserved + 8192 > MAX_EVENTS:
                state["optional_loss_count"] = state.get("optional_loss_count", 0) + 1
                result["code"] = "OPTIONAL_SUPPRESSED"
                return self.commit(state, files, request, result)
            # Advance the revision and build one bounded fact chained to the retained head.
            state["revision"] += 1
            default_generation: dict[str, int] = {}
            event = {
                "schema_version": 1,
                "event_id": str(uuid.uuid4()),
                "seq": state["seq"] + 1,
                "at": now(),
                "repo_id": state["repo_id"],
                "issue_id": self.id,
                "participant_id": participant_key(request),
                "generation": state["participants"]
                .get(participant_key(request), default_generation)
                .get("generation", 0),
                "type": event_type,
                "outcome": "ok",
                "required": not optional,
                "operation_id": sha(request["request_id"].encode()),
                "revision": state["revision"],
                "cause_id": None,
                "refs": [],
                "code": request.get("event", {}).get("code", "OK"),
                "summary": event_type,
                "prev_digest": state["head"],
            }
            event["digest"] = sha(canonical(event))
            line = canonical(event) + b"\n"
            require(len(line) <= 8192, "SIZE_LIMIT")
            files["events.jsonl"] = files.get("events.jsonl", b"") + line
            # Keep enough room to freeze a provider checkpoint at the boundary.
            limit = MAX_EVENTS if event_type == "archive-prepare" else MAX_EVENTS - reserved
            require(len(files["events.jsonl"]) <= limit, "ARCHIVE_PENDING")
            state["seq"], state["head"] = event["seq"], event["digest"]
            state.pop("archive", None)
        # Bind the next state and response to the exact intended payload inventory.
        state["files"] = manifest(files)
        result.update(
            ok=True,
            code=result.get("code", "OK"),
            revision=state["revision"],
            repo_id=state["repo_id"],
            issue_id=self.id,
        )
        stored_result = result
        # Store an immutable snapshot reference for archive-prepare retries.
        if "parts" in result and "snapshot" in result and request["operation"] == "archive-prepare":
            stored_result = {k: v for k, v in result.items() if k != "parts"}
            stored_result["result_ref"] = "export-" + result["snapshot"] + ".json"
        state["requests"][sha(request["request_id"].encode())] = {
            "digest": sha(canonical(request)),
            "result": stored_result,
        }
        # Preserve settlement room even when an event-free request adds retained metadata.
        state_reserve, pending_slots = settlement_reserve(state)
        require(len(canonical(state)) + state_reserve <= 8 * 1024 * 1024, "ARCHIVE_PENDING")
        # Event-free archive progress may retain already committed checkpoint occupancy,
        # but cannot increase event bytes or the number of unfinished obligations.
        retained_occupancy = (
            event_type is None
            and self.state is not None
            and files.get("events.jsonl", b"") == old_files.get("events.jsonl", b"")
            and pending_slots <= settlement_reserve(self.state)[1]
        )
        event_reserve = (
            0
            if event_type == "archive-prepare" or retained_occupancy
            else 8192 * (1 + pending_slots)
        )
        require(
            len(files.get("events.jsonl", b"")) + event_reserve <= MAX_EVENTS, "ARCHIVE_PENDING"
        )
        tx = {
            "base": self.state["files"] if self.state else None,
            "directory_identity": self.state.get("directory_identity") if self.state else None,
            "state": state,
            "writes": {p: encode(b) for p, b in files.items() if old_files.get(p) != b},
        }
        require(len(canonical(tx)) <= MAX_REQUEST, "SIZE_LIMIT")
        # Publish the durable recovery intent before mutating payload or state files.
        fault("before-intent")
        self.control.put("transaction.json", tx)
        fault("intent")
        self.recover()
        return result


def cursor_child_active(state: WorkspaceState, key: str, member: Participant) -> None:
    """Fence a classified child to its currently bound invocation.

    Args:
        state: Locked issue state.
        key: Exact participant identity.
        member: Recorded participant being authorized.

    Raises:
        WorkspaceError: The invocation retired or belongs to another generation.
    """
    # Ordinary participants preserve the existing lifecycle contract.
    identifier = member.get("cursor_invocation")
    if identifier is None:
        return
    # Classification survives detachment; only a new admitted binding can replace it.
    invocation = state.get("cursor_invocations", {}).get(identifier)
    require(
        invocation is not None
        and invocation["phase"] == "bound"
        and invocation["child_participant"] == key
        and invocation["child_generation"] == member["generation"],
        "STALE_BINDING",
    )


def cursor_ready(
    state: WorkspaceState, request: JSONObject, files: PayloadFiles
) -> tuple[str, Participant]:
    """Require a ready Cursor coordinator without granting child parent authority.

    Args:
        state: Locked issue state.
        request: Explicit parent lifecycle identity.
        files: Verified committed payload.

    Returns:
        The ready coordinator identity and record.

    Raises:
        WorkspaceError: Host, ownership, generation or current sources are invalid.
    """
    # Verify the parent role before checking its assigned current source bytes.
    require(request["host"] == "cursor", "HOST_UNSUPPORTED")
    key, member = authorize(state, request, coordinator=True)
    require("cursor_invocation" not in member, "NOT_OWNER")
    packet_reads(state, member, files)
    require(
        member["status"] == "ready" and member["ack"] == sha(canonical(member["packet"])),
        "NOT_READY",
    )
    return key, member


def cursor_text(value: object) -> str:
    """Validate exact bounded native text without stripping newline characters.

    Args:
        value: Native call or persisted storage key.

    Returns:
        The exact input string.

    Raises:
        WorkspaceError: The value is empty, oversized or contains a NUL.
    """
    # Preserve native bytes represented by UTF-8 text while bounding stored metadata.
    require(isinstance(value, str), "INVALID_REQUEST")
    assert isinstance(value, str)
    require(0 < len(value.encode("utf-8")) <= 2048 and "\x00" not in value, "INVALID_REQUEST")
    return value


def cursor_path_reserved(
    state: WorkspaceState, path: str, excluded_plan: str | None = None
) -> bool:
    """Keep an output reserved until its launch binds an owner or retires.

    Args:
        state: Locked issue state containing plans and invocations.
        path: Managed output whose reservation is being checked.
        excluded_plan: Unconsumed plan explicitly being replaced, if any.

    Returns:
        Whether another unconsumed or admitted plan still reserves the path.
    """
    # Bound children use ordinary ownership; admitted children still need their reservation.
    for identifier, plan in state.get("cursor_plans", {}).items():
        if identifier != excluded_plan and path in plan["owned_paths"]:
            invocation = plan["invocation_id"]
            if invocation is None or state["cursor_invocations"][invocation]["phase"] == "admitted":
                return True
    return False


def cursor_operation(
    store: Store, issue: Issue, state: WorkspaceState, files: PayloadFiles, request: JSONObject
) -> JSONObject:
    """Commit one planned Cursor invocation transition through the existing writer.

    Args:
        store: Validated caller store.
        issue: Issue held under its persistent lock.
        state: Isolated copy of committed state.
        files: Verified payload bytes.
        request: Internal adapter request or coordinator plan.

    Returns:
        The committed result, or the unchanged result of a semantic duplicate.

    Raises:
        WorkspaceError: Identity, readiness, plan, ownership, result or effects conflict.
        OSError: Durable publication or binding publication fails.
    """
    # Keep these internal operations restricted to Cursor and active retained issues.
    operation = request["operation"]
    require(request["host"] == "cursor", "HOST_UNSUPPORTED")
    require(state["disposition"] not in TERMINAL and state["storage"] == "present", "TERMINAL")
    plans = state.setdefault("cursor_plans", {})
    invocations = state.setdefault("cursor_invocations", {})
    key = participant_key(request)
    previous = state["requests"].get(sha(request["request_id"].encode()))
    result: JSONObject = {}
    member: Participant | None
    retired: Participant | None = None

    # Exact plan replay has no admission or retirement effect.
    if operation == "cursor-plan" and previous is not None:
        cursor_ready(state, request, files)
        return previous["result"]

    # A plan describes a bounded packet template and disjoint managed-note ownership.
    if operation == "cursor-plan":
        cursor_ready(state, request, files)
        expected(state, request)
        plan_id = token(request["plan_id"])
        existing_plan = plans.get(plan_id)
        require(
            existing_plan is None or existing_plan["invocation_id"] is None,
            "REQUEST_CONFLICT",
        )
        packet = copy.deepcopy(validate_packet(request["packet"]))
        require(all(ref["reader"] == "cursor-child" for ref in packet), "SCOPE_MISSING")
        paths = request["owned_paths"]
        require(isinstance(paths, list) and len(paths) <= 64 and len(set(paths)) == len(paths))
        # Reserve notes against both installed owners and all still-unconsumed plans.
        resumed = request.get("resume_child")
        if resumed is not None:
            token(resumed)
        resumed_key = (
            participant_key(
                {"host": "cursor", "session_id": request["session_id"] + "/agent/" + resumed}
            )
            if resumed is not None
            else None
        )
        if resumed_key is not None:
            retired = state["participants"].get(resumed_key)
            require(
                retired is not None
                and retired["status"] == "detached"
                and "cursor_invocation" in retired
                and not retired["pending"],
                "STALE_BINDING",
            )
        if resumed is not None:
            require(
                not any(
                    inv["phase"] in {"admitted", "bound"}
                    and resumed in {inv["child_conversation"], inv["resume_child"]}
                    for inv in invocations.values()
                ),
                "PENDING_OPERATION",
            )
        # An unconsumed plan reserves its known resume conversation too.
        if resumed is not None:
            require(
                not any(
                    other_id != plan_id
                    and plan["resume_child"] == resumed
                    and plan["invocation_id"] is None
                    for other_id, plan in plans.items()
                ),
                "REQUEST_CONFLICT",
            )
        for path in paths:
            payload_path(path)
            require(
                path != "roadmap.md" and state["owners"].get(path) in {None, resumed_key},
                "NOT_OWNER",
            )
            require(
                not cursor_path_reserved(state, path, plan_id),
                "NOT_OWNER",
            )
        # Retain only planned metadata; Task prompt text never enters issue state.
        require(DIGEST.fullmatch(request["task_digest"]), "INVALID_REQUEST")
        plans[plan_id] = {
            "parent": key,
            "parent_generation": request["binding_generation"],
            "parent_session": request["session_id"],
            "parent_conversation": token(request["parent_conversation"]),
            "task_digest": request["task_digest"],
            "packet": packet,
            "owned_paths": paths,
            "resume_child": resumed,
            "invocation_id": None,
            "resume_generation": retired["generation"] if retired is not None else None,
        }
        result = {"plan_id": plan_id}
    elif operation == "cursor-admit":
        # Match the exact planned launch before reserving the existing parent pending slot.
        parent_key, member = cursor_ready(state, request, files)
        plan = plans.get(token(request["plan_id"]))
        require(plan is not None, "SCOPE_MISSING")
        assert plan is not None
        tool = token(request["tool_id"])
        native_call = cursor_text(request["native_call"])
        identifier = sha(canonical([parent_key, member["generation"], tool]))
        require(
            plan["parent"] == parent_key
            and plan["parent_generation"] == member["generation"]
            and plan["parent_conversation"] == request["parent_conversation"]
            and plan["task_digest"] == request["task_digest"],
            "REQUEST_CONFLICT",
        )
        existing = invocations.get(identifier)
        # An identical active admission is safe to retry; retired calls never relaunch.
        if existing is not None:
            require(existing["phase"] in {"admitted", "bound"}, "STALE_BINDING")
            admitted_pending = member["pending"].get(tool)
            require(
                existing["plan_id"] == request["plan_id"]
                and existing["native_call"] == native_call
                and admitted_pending is not None
                and admitted_pending.get("cursor_invocation") == identifier,
                "REQUEST_CONFLICT",
            )
            return (
                previous["result"]
                if previous
                else {
                    "ok": True,
                    "code": "OK",
                    "revision": state["revision"],
                    "invocation_id": identifier,
                }
            )
        expected(state, request)
        require(plan["invocation_id"] is None and tool not in member["pending"], "REQUEST_CONFLICT")
        require(
            not any(
                inv["parent"] == parent_key
                and inv["parent_generation"] == member["generation"]
                and inv["native_call"] == native_call
                for inv in invocations.values()
            ),
            "REQUEST_CONFLICT",
        )
        # Recheck a known conversation immediately before its native admission.
        resumed = plan["resume_child"]
        if resumed is not None:
            require(
                not any(
                    inv["phase"] in {"admitted", "bound"}
                    and resumed in {inv["child_conversation"], inv["resume_child"]}
                    for inv in invocations.values()
                ),
                "PENDING_OPERATION",
            )
        if resumed is not None:
            resumed_key = participant_key(
                {"host": "cursor", "session_id": plan["parent_session"] + "/agent/" + resumed}
            )
            retired = state["participants"].get(resumed_key)
            require(
                retired is not None
                and retired["status"] == "detached"
                and retired["generation"] == plan["resume_generation"]
                and not retired["pending"],
                "STALE_BINDING",
            )
        # One parent pending record is the active charge; invocation phase is correlation data.
        invocations[identifier] = {
            **plan,
            "plan_id": request["plan_id"],
            "tool_id": tool,
            "native_call": native_call,
            "phase": "admitted",
            "child_conversation": None,
            "child_participant": None,
            "child_generation": None,
            "native_generation": None,
            "terminal_result": None,
        }
        plan["invocation_id"] = identifier
        member["pending"][tool] = {"status": "pending", "cursor_invocation": identifier}
        result = {"invocation_id": identifier}
    else:
        # All remaining transitions identify one already admitted invocation.
        identifier = token(request["invocation_id"])
        invocation = invocations.get(identifier)
        require(invocation is not None, "UNKNOWN_OPERATION")
        assert invocation is not None
        # Reject a malformed projection before inspecting its identity.
        if operation == "cursor-retire":
            require(isinstance(request.get("result"), dict), "INVALID_REQUEST")
        child_conversation = token(
            request["child_conversation"]
            if operation == "cursor-bind-child"
            else request["result"]["child_conversation"]
        )
        child_session = invocation["parent_session"] + "/agent/" + child_conversation
        child_key = participant_key({"host": "cursor", "session_id": child_session})
        member = state["participants"].get(child_key)
        parent_member = state["participants"].get(invocation["parent"])
        require(
            parent_member is not None
            and parent_member["generation"] == invocation["parent_generation"],
            "STALE_BINDING",
        )
        assert parent_member is not None
        parent_pending = parent_member["pending"].get(invocation["tool_id"])
        require(
            (parent_pending is not None and parent_pending.get("cursor_invocation") == identifier)
            or invocation["phase"] == "retired",
            "REQUEST_CONFLICT",
        )
        if operation == "cursor-bind-child":
            # Only the genuine observed child identity may consume the admitted assignment.
            require(key == child_key and request["session_id"] == child_session, "NOT_OWNER")
            require(request["tool_id"] == invocation["tool_id"], "REQUEST_CONFLICT")
            native_generation = request.get("native_generation")
            if native_generation is not None:
                token(native_generation)
            if invocation["phase"] == "bound":
                require(
                    invocation["child_participant"] == child_key
                    and invocation["native_generation"] == native_generation
                    and member is not None
                    and member["cursor_invocation"] == identifier
                    and member["generation"] == invocation["child_generation"]
                    and member["status"] in {"attached", "ready"},
                    "STALE_BINDING",
                )
                # Binding publication is retryable after the issue transaction has committed.
                assert member is not None
                store.save_binding(state, child_key)
                return (
                    previous["result"]
                    if previous
                    else {
                        "ok": True,
                        "code": "OK",
                        "revision": state["revision"],
                        "binding_generation": member["generation"],
                        "invocation_id": identifier,
                    }
                )
            require(invocation["phase"] == "admitted", "STALE_BINDING")
            require(
                not any(
                    other_id != identifier
                    and other["phase"] in {"admitted", "bound"}
                    and child_conversation in {other["child_conversation"], other["resume_child"]}
                    for other_id, other in invocations.items()
                ),
                "PENDING_OPERATION",
            )
            require(invocation["resume_child"] in {None, child_conversation}, "REQUEST_CONFLICT")
            # A known conversation is reusable only through an explicitly planned resume.
            if member is not None:
                require(
                    invocation["resume_child"] == child_conversation
                    and member["status"] == "detached"
                    and not member["pending"]
                    and "cursor_invocation" in member
                    and member["generation"] == invocation["resume_generation"],
                    "STALE_BINDING",
                )
                generation = member["generation"] + 1
            else:
                require(invocation["resume_child"] is None, "STALE_BINDING")
                generation = 1
            require(child_key != state["coordinator"], "NOT_OWNER")
            packet = copy.deepcopy(validate_packet(invocation["packet"]))
            # Bind the validated template to this actual independent reader.
            for ref in packet:
                ref["reader"] = child_key
            # Install one fresh independent packet and its allowed note owners atomically.
            for path in invocation["owned_paths"]:
                require(state["owners"].get(path) in {None, child_key}, "NOT_OWNER")
                state["owners"][path] = child_key
            state.setdefault("assignments", {})[child_key] = {"packet": packet}
            state["participants"][child_key] = {
                "generation": generation,
                "status": "attached",
                "pending": {},
                "packet": packet,
                "ack": None,
                "cursor_invocation": identifier,
            }
            invocation.update(
                phase="bound",
                child_conversation=child_conversation,
                child_participant=child_key,
                child_generation=generation,
                native_generation=native_generation,
            )
            result = {"binding_generation": generation, "invocation_id": identifier}
        else:
            # Validate projected foreground metadata from the pinned result reader.
            require(operation == "cursor-retire", "UNSUPPORTED_OPERATION")
            parent_key, parent_member = cursor_ready(state, request, files)
            require(
                parent_key == invocation["parent"]
                and request["binding_generation"] == invocation["parent_generation"],
                "NOT_OWNER",
            )
            observed = request["result"]
            require(
                isinstance(observed, dict)
                and set(observed)
                == {
                    "parent_conversation",
                    "native_call",
                    "storage_key",
                    "child_conversation",
                    "kind",
                    "is_background",
                    "background_reason",
                    "duration_ms",
                    "binary_sha256",
                    "executor_sha256",
                    "workbench_sha256",
                },
                "INVALID_REQUEST",
            )
            require(
                observed["parent_conversation"] == invocation["parent_conversation"]
                and cursor_text(observed["native_call"]) == invocation["native_call"]
                and observed["kind"] == "foreground-success"
                and observed["is_background"] is False
                and type(observed["background_reason"]) is int
                and observed["background_reason"] == 0
                and type(observed["duration_ms"]) is int
                and 0 <= observed["duration_ms"] < (1 << 64),
                "RESULT_NOT_TERMINAL",
            )
            cursor_text(observed["storage_key"])
            for field in ("binary_sha256", "executor_sha256", "workbench_sha256"):
                require(DIGEST.fullmatch(observed[field]), "INVALID_REQUEST")
            # Exact duplicate result succeeds only while it still names this child generation.
            if invocation["phase"] == "retired":
                require(invocation["terminal_result"] == observed, "REQUEST_CONFLICT")
                require(
                    member is not None
                    and member["generation"] == invocation["child_generation"]
                    and member.get("cursor_invocation") == identifier,
                    "STALE_BINDING",
                )
                assert member is not None
                store.save_binding(state, child_key)
                return (
                    previous["result"]
                    if previous
                    else {
                        "ok": True,
                        "code": "OK",
                        "revision": state["revision"],
                        "invocation_id": identifier,
                        "binding_generation": member["generation"],
                    }
                )
            expected(state, request)
            require(invocation["phase"] in {"admitted", "bound"}, "STALE_BINDING")
            if invocation["phase"] == "bound":
                require(
                    invocation["child_participant"] == child_key
                    and member is not None
                    and member["generation"] == invocation["child_generation"]
                    and member.get("cursor_invocation") == identifier,
                    "STALE_BINDING",
                )
                assert member is not None
                require(not member["pending"], "PENDING_OPERATION")
            else:
                # Terminal-only identity cannot collide with another still-active invocation.
                require(
                    not any(
                        other_id != identifier
                        and other["phase"] in {"admitted", "bound"}
                        and child_conversation
                        in {other["child_conversation"], other["resume_child"]}
                        for other_id, other in invocations.items()
                    ),
                    "PENDING_OPERATION",
                )
                # An early resume advances only classification; no packet or readiness is inferred.
                if member is not None:
                    require(
                        invocation["resume_child"] == child_conversation
                        and member["status"] == "detached"
                        and not member["pending"]
                        and "cursor_invocation" in member
                        and member["generation"] == invocation["resume_generation"],
                        "STALE_BINDING",
                    )
                    generation = member["generation"] + 1
                else:
                    require(invocation["resume_child"] is None, "STALE_BINDING")
                    generation = 1
                member = {
                    "generation": generation,
                    "status": "detached",
                    "pending": {},
                    "packet": None,
                    "ack": None,
                    "cursor_invocation": identifier,
                }
                state["participants"][child_key] = member
                invocation.update(
                    child_conversation=child_conversation,
                    child_participant=child_key,
                    child_generation=generation,
                )
            assert member is not None
            # One transaction fences the generation and releases only its exact parent Task.
            member.update({"status": "detached", "ack": None})
            del parent_member["pending"][invocation["tool_id"]]
            invocation.update(phase="retired", terminal_result=copy.deepcopy(observed))
            result = {"invocation_id": identifier, "binding_generation": member["generation"]}
    # Publish correlation, scope, effects and retirement using the existing roll-forward intent.
    committed = issue.commit(state, files, request, result, operation)
    if operation in {"cursor-bind-child", "cursor-retire"}:
        store.save_binding(issue.committed_state(), child_key)
    return committed


def cursor_source_callback(
    state: WorkspaceState, files: PayloadFiles, request: JSONObject, participant: Participant
) -> JSONObject:
    """Record verified content or settle one exact Cursor source Read callback.

    Caller holds the existing issue transaction and has authorized this generation.

    Args:
        state: Current locked issue state.
        files: Verified managed payload bytes.
        request: Adapter-internal closed content/success/failure metadata.
        participant: Current independently bound Cursor child.

    Returns:
        Whether this callback verified content or finalized a current-packet receipt.

    Raises:
        WorkspaceError: Unknown/foreign call, closed shape, identity or content conflicts.
    """
    # This narrow protocol cannot authorize a parent, another host or a generic completion.
    require(request["host"] == "cursor" and "cursor_invocation" in participant, "NOT_OWNER")
    phase = request.get("source_event")
    require(phase in {"content", "success", "failure"}, "INVALID_REQUEST")
    allowed = {
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
        "source_event",
        "tool_id",
        "source_path",
    }
    # Raw source content is never accepted by or persisted in lifecycle state.
    if phase == "content":
        allowed |= {"observed_sha256", "content_length"}
    elif phase == "success":
        allowed |= {"content_length"}
    require(set(request) <= allowed, "INVALID_REQUEST")
    tool = token(request["tool_id"])
    pending = participant["pending"].get(tool)
    require(pending is not None and "cursor_source" in pending, "UNKNOWN_OPERATION")
    assert pending is not None
    source = pending["cursor_source"]
    require(
        source["invocation_id"] == participant["cursor_invocation"]
        and source["generation"] == participant["generation"]
        and request.get("source_path") == source["file_path"],
        "REQUEST_CONFLICT",
    )
    digest = sha(canonical(participant["packet"]))
    # Content is provisional until a distinct correlated native success is observed.
    if phase == "content":
        refs = packet_reads(state, participant, files)
        require(source["packet_digest"] == digest, "SOURCE_STALE")
        require(
            any(
                ref["available"]
                and ref["locator"] == source["locator"]
                and ref["sha256"] == source["sha256"]
                for ref in refs
            ),
            "SOURCE_STALE",
        )
        observed = request.get("observed_sha256")
        length = request.get("content_length")
        require(isinstance(observed, str) and DIGEST.fullmatch(observed), "INVALID_REQUEST")
        require(type(length) is int and 0 <= length <= MAX_FILE, "INVALID_REQUEST")
        require(observed == source["sha256"], "SOURCE_DELIVERY_REQUIRED")
        proof = {"sha256": observed, "content_length": length}
        require(source.get("provisional") in (None, proof), "REQUEST_CONFLICT")
        source["provisional"] = proof
        return {"source_content_verified": True, "source_receipt": False}
    # Known finished failure/stale/unmatched success settles this call without credit.
    receipt = False
    if (
        phase == "success"
        and source.get("provisional") is not None
        and source["packet_digest"] == digest
    ):
        proof = source["provisional"]
        length = request.get("content_length")
        require(type(length) is int and length >= 0, "INVALID_REQUEST")
        # Source refresh/missing bytes must not strand an already finished file Read.
        try:
            refs = packet_reads(state, participant, files)
        except (WorkspaceError, OSError):
            refs = []
        current = any(
            ref["available"]
            and ref["locator"] == source["locator"]
            and ref["sha256"] == source["sha256"]
            for ref in refs
        )
        if current and length == proof["content_length"]:
            # Prune old-packet receipts before adding at most one of64current assigned locators.
            receipts = {
                locator: item
                for locator, item in participant.get("cursor_sources", {}).items()
                if item["packet_digest"] == digest
            }
            receipts[source["locator"]] = {
                "packet_digest": digest,
                "sha256": source["sha256"],
                "invocation_id": participant["cursor_invocation"],
            }
            require(len(receipts) <= 64, "SIZE_LIMIT")
            participant["cursor_sources"] = receipts
            receipt = True
    del participant["pending"][tool]
    return {"source_content_verified": False, "source_receipt": receipt}


def authorize(
    state: WorkspaceState, request: JSONObject, coordinator: bool = False, maintenance: bool = False
) -> tuple[str, Participant]:
    """Require a current participant generation and any requested coordinator authority.

    Args:
        state: Current or staged issue control state.
        request: Versioned lifecycle request with explicit repository, issue and session identities.
        coordinator: Whether the caller must own coordinator authority.
        maintenance: Whether an explicit maintenance binding may authorize this operation.

    Returns:
        The participant key and participant state.

    Raises:
        WorkspaceError: If binding, generation, status or ownership is invalid.
    """
    # Resolve the participant and enforce its current binding generation.
    key = participant_key(request)
    participant = state["participants"].get(key)
    require(participant is not None, "BINDING_MISSING")
    assert participant is not None  # Established by the binding guard above.
    require(request.get("binding_generation") == participant["generation"], "STALE_BINDING")
    require(
        participant["status"]
        in ({"attached", "ready", "maintenance"} if maintenance else {"attached", "ready"}),
        "STALE_BINDING",
    )
    # A classified child retains authority only while its exact invocation is active.
    cursor_child_active(state, key, participant)
    # Enforce coordinator ownership only for operations that request it.
    require(not coordinator or state["coordinator"] == key, "NOT_OWNER")
    return key, participant


def expected(state: WorkspaceState, request: JSONObject) -> None:
    """Require the caller to name the current issue revision.

    Args:
        state: Current or staged issue control state.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Raises:
        WorkspaceError: If the expected revision is stale or absent.
    """
    require(request.get("expected_revision") == state["revision"], "REVISION_CONFLICT")


def evidence(value: list[EvidenceRef] | None) -> list[EvidenceRef]:
    """Validate a bounded list of attributable evidence references.

    Args:
        value: Required evidence records, each naming a locator and exact SHA-256 digest.

    Returns:
        The validated reference list.

    Raises:
        WorkspaceError: If evidence fields, locators or digests are invalid.
    """
    # Require a nonempty bounded evidence list before inspecting references.
    require(isinstance(value, list) and 0 < len(value) <= 16, "EVIDENCE_REQUIRED")
    assert value is not None  # Established by the evidence shape guard above.
    # Require attributable evidence with bounded locators and content digests.
    for item in value:
        require(
            isinstance(item, dict) and set(item) == {"id", "locator", "sha256"}, "EVIDENCE_REQUIRED"
        )
        token(item["id"])
        require(
            isinstance(item["locator"], str) and 0 < len(item["locator"]) <= 2048,
            "EVIDENCE_REQUIRED",
        )
        require(DIGEST.fullmatch(item["sha256"]), "EVIDENCE_REQUIRED")
    return value


def validate_packet(packet: list[SourceRef]) -> list[SourceRef]:
    """Validate explicit reader-scoped references without expanding their contents.

    Args:
        packet: Explicit reader-scoped source references.

    Returns:
        The validated packet.

    Raises:
        WorkspaceError: If packet shape, reference uniqueness or path rules fail.
    """
    # Validate the packet container before checking reader-scoped references.
    require(isinstance(packet, list) and len(packet) <= 64, "SCOPE_MISSING")
    identifiers = set()
    # Require unique reference IDs and explicit reader, authority, and lifecycle scope.
    for ref in packet:
        require(
            isinstance(ref, dict)
            and set(ref)
            == {"id", "locator", "sha256", "required", "authority", "reason", "stage", "reader"},
            "SCOPE_MISSING",
        )
        token(ref["id"])
        require(ref["id"] not in identifiers and DIGEST.fullmatch(ref["sha256"]), "SCOPE_MISSING")
        identifiers.add(ref["id"])
        require(type(ref["required"]) is bool, "SCOPE_MISSING")
        # Validate every field used to attribute authority and reader scope.
        for field in ("authority", "reason", "stage", "reader"):
            token(ref[field])
        locator = ref["locator"]
        # Restrict relative locators to managed task payload paths.
        if not Path(locator).is_absolute():
            payload_path(locator, events=True)
    return packet


def refresh_roadmap_reference(state: WorkspaceState, key: str, digest: str) -> None:
    """Move the coordinator's own roadmap reference to newly staged roadmap bytes.

    Only references whose locator is ``roadmap.md`` in the coordinator's participant
    packet and stored assignment change. Other references, other participants and
    reader packets stay untouched. A changed packet clears acknowledgment so readiness
    needs a new ``read``, exact-digest ``acknowledge`` and ``ready``.

    Args:
        state: Staged issue control state for the committing ``update``.
        key: Coordinator participant key that owns ``roadmap.md``.
        digest: SHA-256 digest of the staged roadmap bytes.
    """
    # Locate the coordinator's packet; an unscoped coordinator has nothing to refresh.
    participant = state["participants"].get(key)
    if participant is None or participant["packet"] is None:
        return
    # Point the packet's roadmap references at the new bytes.
    packet = roadmap_refreshed(participant["packet"], digest)
    # Leave readiness intact when no recorded roadmap digest changed.
    if packet == participant["packet"]:
        return
    # Keep the stored assignment's roadmap reference on the same committed digest.
    assignment = state.get("assignments", {}).get(key)
    if assignment is not None:
        assignment["packet"] = roadmap_refreshed(assignment["packet"], digest)
    # Invalidate acknowledgment without reviving a detached participant generation.
    participant.update({"packet": packet, "ack": None})
    if participant["status"] != "detached":
        participant["status"] = "attached"


def roadmap_refreshed(packet: list[SourceRef], digest: str) -> list[SourceRef]:
    """Copy a packet with every ``roadmap.md`` reference set to one digest.

    Args:
        packet: Assigned source references to copy.
        digest: SHA-256 digest of the committed roadmap bytes.

    Returns:
        A new packet list; non-roadmap references are copied unchanged.
    """
    # Copy each reference so the caller's committed packet is never mutated in place.
    refreshed: list[SourceRef] = []
    for ref in packet:
        copy_ref = ref.copy()
        # Replace the digest only for the managed roadmap locator.
        if copy_ref["locator"] == "roadmap.md":
            copy_ref["sha256"] = digest
        refreshed.append(copy_ref)
    return refreshed


class SourceState(TypedDict):
    """Compare one assigned reference's recorded digest with its current source bytes."""

    id: str
    locator: str
    recorded_sha256: str
    current_sha256: str | None
    available: bool


class StaleSourceError(WorkspaceError):
    """Report a required packet source mismatch together with every changed reference."""

    def __init__(self, stale: list[SourceState]) -> None:
        """Store the changed references beside the public SOURCE_STALE diagnostic.

        Args:
            stale: References whose current digest differs from the recorded one,
                including unreadable sources with a null current digest.
        """
        # Keep the bounded code and attach a recovery instruction naming the list.
        super().__init__(
            "SOURCE_STALE",
            "The references in `stale` changed. Refresh their recorded sha256 to "
            "current_sha256 through the coordinator's scope, then read, acknowledge and ready.",
        )
        self.stale = stale


def external_source(locator: str) -> bytes | None:
    """Read one absolute packet source through the no-follow parent-directory boundary.

    Args:
        locator: Absolute source locator recorded in a packet reference.

    Returns:
        The exact source bytes, or None when the source is missing, unsafe or unreadable.
    """
    # Open the parent through the validated traversal and read only the named file.
    path = Path(locator)
    try:
        with Directory.absolute(path.parent) as parent:
            return parent.read(path.name, MAX_FILE)
    # Report an unreadable source as absent; callers decide whether it blocks readiness.
    except (OSError, WorkspaceError):
        return None


def source_state(ref: SourceRef, current: str | None) -> SourceState:
    """Describe one reference's recorded and current digests.

    Args:
        ref: Assigned source reference exactly as stored in the packet.
        current: Current SHA-256 digest of the source, or None when unreadable.

    Returns:
        The reference identity, both digests and whether they still match.
    """
    return {
        "id": ref["id"],
        "locator": ref["locator"],
        "recorded_sha256": ref["sha256"],
        "current_sha256": current,
        "available": current == ref["sha256"],
    }


def packet_sources(packet: list[SourceRef], digests: dict[str, str]) -> list[SourceState]:
    """Compute each packet reference's current state without reading payload bytes.

    Payload-relative locators take the committed manifest digest, which is exactly what
    ``read`` verifies the payload bytes against. Absolute locators are read through the
    same no-follow reader that ``packet_reads`` uses. No state, lock or event changes.

    Args:
        packet: Assigned source references in their stored order.
        digests: Committed payload manifest mapping relative paths to digests.

    Returns:
        One source state per reference, aligned with ``packet``.
    """
    # Resolve each reference from the committed manifest or the external source bytes.
    sources: list[SourceState] = []
    for ref in packet:
        # Payload references use the committed digest; absent paths are unreadable.
        if not Path(ref["locator"]).is_absolute():
            current = digests.get(ref["locator"])
        else:
            # External references hash the bytes currently at their absolute locator.
            data = external_source(ref["locator"])
            current = None if data is None else sha(data)
        sources.append(source_state(ref, current))
    return sources


def packet_reads(
    state: WorkspaceState, participant: Participant, files: PayloadFiles
) -> list[SourceAvailability]:
    """Check only assigned references and fail on missing or stale required sources.

    Args:
        state: Current or staged issue control state.
        participant: Participant state containing the assigned packet.
        files: Relative payload paths mapped to exact bytes.

    Returns:
        Assigned reference descriptors annotated with availability.

    Raises:
        WorkspaceError: If the packet is missing.
        StaleSourceError: If a required source is missing, unreadable or changed; it
            lists every reference whose current digest differs from the recorded one.
    """
    # Require an assigned source packet before resolving any source bytes.
    require(participant.get("packet") is not None, "SCOPE_MISSING")
    assert participant["packet"] is not None  # Established by the scope guard above.
    refs: list[SourceAvailability] = []
    sources: list[SourceState] = []
    blocked = False
    # Read only assigned references and compare their exact content digests.
    for ref in participant["packet"]:
        # Open external references safely; resolve relative references from
        # validated payload bytes.
        if Path(ref["locator"]).is_absolute():
            data = external_source(ref["locator"])
        else:
            data = files.get(ref["locator"])
        # Compare exact source bytes and retain availability for optional references.
        source = source_state(ref, None if data is None else sha(data))
        sources.append(source)
        blocked = blocked or (ref["required"] and not source["available"])
        refs.append({**ref, "available": source["available"]})
    # A changed required source blocks readiness and names every changed reference.
    if blocked:
        raise StaleSourceError([source for source in sources if not source["available"]])
    return refs


def new_state(store: Store, request: JSONObject) -> WorkspaceState:
    """Construct initial issue state for its explicitly identified coordinator.

    Args:
        store: Validated shared repository Store.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        An uncommitted initial state object.

    Raises:
        WorkspaceError: If coordinator or issue identity is invalid.
    """
    # Verify that the explicitly named coordinator is the initiating participant.
    key = participant_key(request)
    require(request.get("coordinator") == key, "NOT_OWNER")
    # Construct initial state without importing ownership or history from another issue.
    return {
        "schema_version": 1,
        "repo_id": store.registration["repo_id"],
        "issue_id": request["issue_id"],
        "issue_uuid": token(request["issue_uuid"]),
        "revision": 0,
        "generation": 1,
        "disposition": "active",
        "storage": "present",
        "coordinator": key,
        "owners": {"roadmap.md": key},
        "participants": {},
        "files": {},
        "seq": 0,
        "head": None,
        "requests": {},
        "provenance": {},
    }


def attach(state: WorkspaceState, request: JSONObject) -> JSONObject:
    """Attach or resume an assigned participant without resetting existing task content.

    Args:
        state: Current or staged issue control state.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The participant identity and current binding generation.

    Raises:
        WorkspaceError: If disposition, storage, assignment or generation forbids attachment.
    """
    # Require active retained state before attaching or resuming this participant.
    key = participant_key(request)
    require(
        state["disposition"] not in TERMINAL and state["storage"] == "present", "RECOVERY_REQUIRED"
    )
    participant = state["participants"].get(key)
    # Preserve existing scope and require the recorded generation before resuming.
    if participant:
        # Generic attachment cannot revive a retired Cursor invocation.
        cursor_child_active(state, key, participant)
        initial_create = (
            request["operation"] == "create"
            and request.get("binding_generation") is None
            and participant["generation"] == 1
            and key == state["coordinator"]
        )
        require(
            initial_create or request.get("binding_generation") == participant["generation"],
            "STALE_BINDING",
        )
        require(participant["status"] in {"attached", "ready", "detached"}, "STALE_BINDING")
        # Fence the old binding and require a new acknowledgment after resumption.
        if participant["status"] == "detached":
            participant["generation"] += 1
            participant["status"] = "attached"
            participant["ack"] = None
    else:
        # Create a participant only for the coordinator or an explicit prior assignment.
        require(key == state["coordinator"] or key in state.get("assignments", {}), "SCOPE_MISSING")
        assignment: Assignment | dict[str, list[SourceRef]] = state.get("assignments", {}).get(
            key, {}
        )
        participant = {
            "generation": 1,
            "status": "attached",
            "pending": {},
            "packet": assignment.get("packet"),
            "ack": None,
        }
        state["participants"][key] = participant
    return {"participant_id": key, "binding_generation": participant["generation"]}


def archive_payload(state: WorkspaceState, files: PayloadFiles) -> JSONObject:
    """Freeze reconstructable payload bytes and their provenance into an archive value.

    Args:
        state: Current or staged issue control state.
        files: Relative payload paths mapped to exact bytes.

    Returns:
        The versioned snapshot, including segment lineage when present.
    """
    # Capture exact task bytes, lineage and outcome evidence for reconstruction.
    result = {
        "schema_version": 1,
        "repo_id": state["repo_id"],
        "issue_id": state["issue_id"],
        "issue_uuid": state["issue_uuid"],
        "revision": state["revision"],
        "disposition": state["disposition"],
        "outcome": state.get("outcome"),
        "checkpoint": state.get("checkpoint"),
        "provenance": state.get("provenance", {}),
        "adoption": state.get("adoption"),
        "checkpoint_history": state.get("checkpoint_history", []),
        "outcome_history": state.get("outcome_history", []),
        "seq": state["seq"],
        "head": state["head"],
        "files": [
            {
                "path": p,
                "size": len(b),
                "sha256": sha(b),
                "scope": "coordinator-archive",
                "data": encode(b),
            }
            for p, b in sorted(files.items())
        ],
    }
    # Carry immutable segment lineage into snapshots that contain rolled-over history.
    if state.get("event_segments"):
        result["event_segments"] = state["event_segments"]
    return result


def document(payload: JSONObject, heading: str) -> str:
    """Wrap a structured archive value in a readable Markdown document.

    Args:
        payload: Structured value to embed in the provider document.
        heading: Readable Markdown heading and explanatory text.

    Returns:
        Markdown containing one canonical fenced JSON value.
    """
    return heading + "\n\n```json\n" + canonical(payload).decode() + "\n```\n"


def parse_document(content: str) -> JSONObject:
    """Extract exactly one bounded structured value from provider Markdown.

    Args:
        content: Provider Markdown containing a single structured archive block.

    Returns:
        The decoded fenced JSON value.

    Raises:
        WorkspaceError: If size, fenced-block count or JSON validation fails.
    """
    # Bound provider text before locating its structured archive block.
    require(isinstance(content, str) and len(content.encode()) <= DOCUMENT_LIMIT, "SIZE_LIMIT")
    # Select exactly one fenced JSON value, rejecting ambiguous provider content.
    matches = re.findall(r"^```json[ \t]*\n(.*?)\n```[ \t]*$", content, re.S | re.M)
    require(len(matches) == 1, "INTEGRITY_ERROR")
    parsed: JSONObject = strict_json(matches[0])
    return parsed


def export_documents(snapshot: JSONObject) -> ExportDocuments:
    """Split a frozen snapshot into bounded readable, reconstructable provider parts.

    Args:
        snapshot: Frozen versioned issue snapshot and reconstructable file bytes.

    Returns:
        The snapshot digest and ordered document requests.

    Raises:
        WorkspaceError: If snapshot or document capacity is exceeded.
    """
    # Freeze the exact snapshot bytes and reject archives beyond the total budget.
    raw = canonical(snapshot)
    require(len(raw) <= MAX_ARCHIVE, "ARCHIVE_PENDING")
    digest = sha(raw)
    # Byte chunks, not file splitting, bound even one large file.
    chunks = [raw[i : i + ARCHIVE_CHUNK] for i in range(0, len(raw), ARCHIVE_CHUNK)]
    readable = "\n\n".join(
        "File: " + item["path"] + "\n" + decode(item["data"]).decode("utf-8")
        for item in snapshot["files"]
    )
    width = max(1, (len(readable) + len(chunks) - 1) // len(chunks))
    parts: list[DocumentRequest] = []
    # Pair each reconstructable chunk with readable history and enforce the document budget.
    for i, chunk in enumerate(chunks):
        body = {
            "schema_version": 1,
            "kind": "part",
            "snapshot": digest,
            "index": i,
            "count": len(chunks),
            "sha256": sha(chunk),
            "data": encode(chunk),
        }
        fragment = readable[i * width : (i + 1) * width]
        heading = (
            f"# {snapshot['issue_id']} archive part {i + 1}/{len(chunks)}\n\n"
            "## Readable history fragment\n\n"
            + "\n".join("> " + line for line in fragment.splitlines())
        )
        content = document(body, heading)
        require(len(content.encode()) <= DOCUMENT_LIMIT, "ARCHIVE_PENDING")
        parts.append(
            {
                "title": f"{snapshot['issue_id']} snapshot {digest} part {i + 1}",
                "issue": snapshot["issue_uuid"],
                "content": content,
            }
        )
    return {"snapshot": digest, "parts": parts}


def provider_observation(item: JSONObject, issue_uuid: str) -> JSONObject:
    """Validate the origin, parent and identity fields of a supplied document read-back.

    Args:
        item: One supplied independent provider document observation.
        issue_uuid: Exact provider parent identity required for the observation.

    Returns:
        The structured archive value from the observed content.

    Raises:
        WorkspaceError: If observation identity, shape or content is invalid.
    """
    # Require the complete provider-observation shape before reading its identity fields.
    require(
        isinstance(item, dict)
        and set(item) == {"id", "url", "issue", "updatedAt", "content", "origin", "request_id"},
        "ARCHIVE_PENDING",
    )
    require(
        item["issue"] == issue_uuid and item["origin"] == "linear_get_document", "ARCHIVE_PENDING"
    )
    # Validate the provider identity, version and read-back request tokens.
    for field in ("id", "updatedAt", "request_id"):
        token(item[field])
    require(
        isinstance(item["url"], str) and item["url"].startswith("https://linear.app/"),
        "ARCHIVE_PENDING",
    )
    return parse_document(item["content"])


def verify_provider(
    state: WorkspaceState, observations: list[JSONObject] | None
) -> tuple[JSONObject, PayloadFiles, JSONObject]:
    """Reconstruct a frozen export from exact ordered provider versions and digests.

    Args:
        state: Current or staged issue control state.
        observations: Independent provider read-backs for all parts and the root index.

    Returns:
        A tuple of verified snapshot, payload bytes and archive receipt.

    Raises:
        WorkspaceError: If parent, snapshot, parts, versions, digests or event history disagree.
    """
    # Bound the supplied read-back set before reconstructing archive content.
    require(isinstance(observations, list) and 2 <= len(observations) <= 514, "ARCHIVE_PENDING")
    assert observations is not None  # Established by the observation shape guard above.
    # Check read-back origins and parent identities before selecting the single root index.
    parsed = [provider_observation(x, state["issue_uuid"]) for x in observations]
    roots = [(o, p) for o, p in zip(observations, parsed) if p.get("kind") == "index"]
    require(len(roots) == 1, "ARCHIVE_PENDING")
    root_observed, root = roots[0]
    require(
        root.get("schema_version") == 1 and root["snapshot"] == state["export"]["snapshot"],
        "ARCHIVE_PENDING",
    )
    parts = {o["id"]: (o, p) for o, p in zip(observations, parsed) if p.get("kind") == "part"}
    require(
        len(parts) == len(observations) - 1 == len(root["parts"])
        and root_observed["id"] not in parts,
        "ARCHIVE_PENDING",
    )
    raw = b""
    # Reconstruct in index order only from the named versions and matching part digests.
    for i, descriptor in enumerate(root["parts"]):
        require(descriptor["id"] in parts, "ARCHIVE_PENDING")
        observed, part = parts[descriptor["id"]]
        require(
            part["schema_version"] == 1
            and part["snapshot"] == root["snapshot"]
            and part["index"] == i
            and part["count"] == len(parts)
            and descriptor["sha256"] == sha(canonical(part))
            and descriptor["updatedAt"] == observed["updatedAt"],
            "ARCHIVE_PENDING",
        )
        chunk = decode(part["data"], 128 * 1024)
        require(sha(chunk) == part["sha256"], "INTEGRITY_ERROR")
        raw += chunk
        require(len(raw) <= MAX_ARCHIVE, "SIZE_LIMIT")
    # Bind the reconstructed bytes to the exported snapshot and repository identity.
    require(sha(raw) == root["snapshot"], "INTEGRITY_ERROR")
    snapshot = strict_json(raw)
    require(
        snapshot["schema_version"] == 1
        and snapshot["repo_id"] == state["repo_id"]
        and snapshot["issue_id"] == state["issue_id"]
        and snapshot["issue_uuid"] == state["issue_uuid"],
        "INTEGRITY_ERROR",
    )
    files = {}
    # Validate each unique payload path, byte count, and digest before restoring its bytes.
    for item in snapshot["files"]:
        path = payload_path(item["path"], True)
        require(path not in files, "INTEGRITY_ERROR")
        data = decode(item["data"])
        require(len(data) == item["size"] and sha(data) == item["sha256"], "INTEGRITY_ERROR")
        data.decode("utf-8")
        files[path] = data
    # Require complete task content and verify the global event chain before issuing a receipt.
    require("roadmap.md" in files and "events.jsonl" in files, "INTEGRITY_ERROR")
    require(validate_history(files) == (snapshot["seq"], snapshot["head"]), "INTEGRITY_ERROR")
    receipt = {
        "snapshot": root["snapshot"],
        "root": {k: v for k, v in root_observed.items() if k != "content"},
        "parts": root["parts"],
        "read_at": now(),
        "observations_digest": sha(canonical(observations)),
    }
    return snapshot, files, receipt


def eligible(state: WorkspaceState) -> None:
    """Require terminal evidence, no live work and a verified export before cleanup.

    Args:
        state: Current or staged issue control state.

    Raises:
        WorkspaceError: If participation, disposition or archive evidence requires retention.
    """
    # Require a recorded terminal outcome before considering destructive cleanup.
    require(state["disposition"] in TERMINAL and state.get("outcome"), "RETAINED")
    require(
        all(
            p["status"] in {"detached", "maintenance"} and not p["pending"]
            for p in state["participants"].values()
        ),
        "RETAINED",
    )
    # Require both the frozen export and its independently verified receipt.
    require(state.get("export") and state.get("archive"), "ARCHIVE_PENDING")


def finish_cleanup(issue: Issue) -> None:
    """Finish only the recorded issue quarantine after validating its remaining tree.

    Args:
        issue: Locked issue whose durable cleanup intent may be resumed.

    Raises:
        WorkspaceError: If identity, metadata safety or remaining payload digests differ.
        OSError: If quarantine, deletion or tombstone publication fails.
    """
    # Select the issue control and payload handles that own cleanup state.
    control, task = issue.control, issue.store.task
    # Leave payloads untouched unless a durable cleanup intent names them.
    if not control.exists("cleanup.json"):
        return
    intent = control.json("cleanup.json")
    name = intent["quarantine"]
    # Move only the identity- and manifest-matching issue into its recorded quarantine.
    if task.exists(issue.id):
        require(not control.exists(name), "RECOVERY_REQUIRED")
        # Verify the live payload identity and manifest before quarantine.
        with task.child(issue.id, private=False) as payload:
            require(
                payload.identity == intent["directory_identity"]
                and manifest(inventory(payload)) == intent["files"],
                "UNTRACKED_CHANGE",
            )
        task.rename_directory(issue.id, control, name)
        fault("quarantine")
    # Resume deletion only inside the recorded quarantine with the original directory identity.
    if control.exists(name):
        # Reopen only the recorded quarantine directory for bounded deletion.
        with control.child(name, private=False) as payload:
            require(payload.identity == intent["directory_identity"], "UNSAFE_PATH")
            names = payload.names()
            require(
                all(
                    n in {"roadmap.md", "events.jsonl", "context"}
                    or SEGMENT.fullmatch(n)
                    or os_metadata(n)
                    for n in names
                ),
                "UNSAFE_PATH",
            )
            remaining = {}
            # Read every remaining quarantine entry before deleting any payload bytes.
            for entry in names:
                # Read context entries beneath their validated parent directory.
                if entry == "context":
                    with payload.child("context", private=False) as context:
                        # Validate each direct context entry without
                        # descending into arbitrary directories.
                        for note in context.names():
                            remaining["context/" + note] = context.read(note, MAX_FILE)
                else:
                    # Read root quarantine entries before considering any deletion.
                    remaining[entry] = payload.read(entry, MAX_FILE)
            require(
                all(
                    os_metadata(p.rsplit("/", 1)[-1]) or intent["files"].get(p) == sha(b)
                    for p, b in remaining.items()
                ),
                "UNTRACKED_CHANGE",
            )
            # Unlink only entries validated in the complete remaining quarantine inventory.
            for path in sorted(remaining):
                # Delete context entries relative to their validated parent descriptor.
                if path.startswith("context/"):
                    # Open the validated context parent before unlinking one child.
                    with payload.child("context", private=False) as context:
                        context.unlink(path[8:])
                else:
                    # Unlink a validated root entry relative to the quarantine descriptor.
                    payload.unlink(path)
                fault("delete:" + path)
            # Remove the context directory only after all validated entries have been unlinked.
            if payload.exists("context"):
                payload.rmdir("context")
            payload.flush()
        control.rmdir(name)
    # Publish the cleaned tombstone after durable removal of the quarantined payload.
    state = intent["state"]
    state["storage"] = "cleaned"
    state["requests"] = {
        k: v for k, v in state["requests"].items() if k == sha(intent["request_id"].encode())
    }
    state.pop("index_request", None)
    state.pop("checkpoint", None)
    state.pop("checkpoint_history", None)
    state.pop("outcome_history", None)
    state.pop("provenance", None)
    # Discard local export copies after the independent provider archive has been verified.
    for name in control.names():
        # Select only immutable export-copy filenames for local pruning.
        if re.fullmatch(r"export-[0-9a-f]{64}\.json", name):
            control.read(name)  # Refuse substituted links/special files before pruning.
            control.unlink(name)
    state["tombstone"] = {
        "snapshot": state["export"]["snapshot"],
        "at": now(),
        "generation": state["generation"],
        "cleanup_request": intent["request_id"],
    }
    control.put("state.json", state)
    fault("cleanup-state")
    control.unlink("cleanup.json")
    issue.state = state


def initial_payload(identifier: str) -> PayloadFiles:
    """Build the packaged starting payload for a newly created issue workspace.

    Args:
        identifier: Validated issue ID substituted into the roadmap template.

    Returns:
        The template roadmap bytes and an empty event stream.

    Raises:
        OSError: If the packaged roadmap template cannot be read.
    """
    template = resources.files("nplet").joinpath("resources/task_workspace/roadmap.md")
    return {
        "roadmap.md": template.read_text().replace("{{issue_id}}", identifier).encode(),
        "events.jsonl": b"",
    }


def operate(store: Store, issue: Issue, request: JSONObject) -> JSONObject:
    """Dispatch one issue request under the caller-held binding and issue locks.

    Args:
        store: Validated shared repository Store.
        issue: Issue opened under the caller-held control-directory lock.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The operation result, committed when mutation is required.

    Raises:
        WorkspaceError: If authorization, state, evidence or lifecycle constraints fail.
        OSError: If durable store operations fail.
    """
    # Resolve the operation and explicit participant identity before state recovery.
    operation = request["operation"]
    key = participant_key(request)
    # Finish durable transaction recovery before evaluating this issue request.
    issue.recover()
    finish_cleanup(issue)
    state = copy.deepcopy(issue.state)
    # Check existing issue identity and exact-request idempotency before dispatch.
    if state:
        require(
            not request.get("issue_uuid") or request["issue_uuid"] == state["issue_uuid"],
            "ISSUE_MISMATCH",
        )
        previous = state["requests"].get(sha(request["request_id"].encode()))
        # Replay only an identical request; a reused ID with different content is a conflict.
        if previous:
            require(previous["digest"] == sha(canonical(request)), "REQUEST_CONFLICT")
            if operation not in {
                "cursor-plan",
                "cursor-admit",
                "cursor-bind-child",
                "cursor-retire",
            }:
                # Restore worktree visibility and binding for retried attachment operations.
                if (
                    operation in {"create", "adopt", "attach", "resume", "bind", "restore"}
                    and state["storage"] != "cleaned"
                ):
                    store.view(issue.id)
                    # Persist the committed participant generation for this worktree.
                    store.save_binding(state, key)
                result: JSONObject = previous["result"].copy()
                reference = result.pop("result_ref", None)
                # Load a retained immutable export result for an archive-preparation retry.
                if reference:
                    result.update(issue.control.json(reference))
                return result
    # Return issue presence and storage state without changing participation.
    if operation == "diagnose":
        diagnosis: JSONObject = {
            "ok": True,
            "code": "PRESENT" if state else "ABSENT",
            "repo_id": store.registration["repo_id"],
            "issue_id": issue.id,
            "revision": state["revision"] if state else None,
            "storage": state["storage"] if state else "absent",
        }
        member = state["participants"].get(key) if state else None
        packet = member.get("packet") if member is not None else None
        # Expose a recorded caller's committed packet exactly as stored, plus each
        # reference's current digest, so a coordinator stranded at SOURCE_STALE can
        # build its digest-only self-refresh scope from this result alone.
        if state and packet is not None:
            diagnosis["packet"] = copy.deepcopy(packet)
            diagnosis["sources"] = packet_sources(packet, state.get("files", {}))
        return diagnosis
    # Read the existing session binding; never infer it from the prompt.
    binding = store.binding()
    # Prevent attachment operations from reusing another issue binding.
    if operation in {"create", "adopt", "attach", "resume", "bind", "join"}:
        require(not binding or binding["issue_id"] == issue.id, "BINDING_CONFLICT")
    # Require explicit adoption for existing payloads and explicit creation for absent ones.
    if not state:
        require(operation in {"create", "adopt"}, "BINDING_MISSING")
        exists = store.task.exists(issue.id)
        require(
            exists == (operation == "adopt"), "ADOPTION_REQUIRED" if exists else "RECOVERY_REQUIRED"
        )
        state = new_state(store, request)
        # Import inspected existing payloads only with evidence and complete ownership.
        if operation == "adopt":
            evidence(request.get("evidence"))
            # Inventory the adopted directory before comparing the caller-supplied manifest.
            with store.task.child(issue.id, private=False) as payload:
                files = inventory(payload)
            require(request.get("inventory") == manifest(files), "UNTRACKED_CHANGE")
            owners = request.get("owners", {})
            require(
                set(owners)
                == {p for p in files if p != "events.jsonl" and not SEGMENT.fullmatch(p)}
                and owners["roadmap.md"] == key,
                "NOT_OWNER",
            )
            require(all(DIGEST.fullmatch(v) for v in owners.values()), "NOT_OWNER")
            state["owners"] = owners
            state["adoption"] = {"inventory": manifest(files), "evidence": request["evidence"]}
            state["seq"], state["head"] = validate_history(files)
        else:
            # Initialize from the packaged roadmap and start an empty event stream.
            files = initial_payload(issue.id)
        result = attach(state, request)
        # Publish this lifecycle result through the recoverable transaction.
        result = issue.commit(state, files, request, result, operation)
        store.view(issue.id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(issue.committed_state(), key)
        return result
    # Reconstruct only the tombstone-matching archive before reopening a new generation.
    if operation == "restore":
        require(state["storage"] == "cleaned" and state.get("tombstone"), "RECOVERY_REQUIRED")
        require(key == state["coordinator"], "NOT_OWNER")
        snapshot, files, receipt = verify_provider(state, request.get("observations"))
        require(
            receipt["snapshot"] == state["tombstone"]["snapshot"]
            and manifest(files) == state["files"],
            "INTEGRITY_ERROR",
        )
        require(not store.task.exists(issue.id), "RECOVERY_REQUIRED")
        state["history"] = state.get("history", []) + [state["tombstone"]]
        state["storage"], state["disposition"] = "present", "active"
        state["provenance"] = snapshot.get("provenance", {})
        state["checkpoint"] = snapshot.get("checkpoint")
        state["event_segments"] = snapshot.get("event_segments", [])
        state["adoption"] = snapshot.get("adoption")
        state["checkpoint_history"] = snapshot.get("checkpoint_history", [])
        state["outcome_history"] = snapshot.get("outcome_history", [])
        state["generation"] += 1
        # Fence every old participant generation before exposing restored state.
        for participant in state["participants"].values():
            participant.update(
                {"status": "detached", "ack": None, "generation": participant["generation"] + 1}
            )
        state["participants"][key]["status"] = "attached"
        state.pop("tombstone")
        # A durable initialization transaction restores the exact history before the reopen event.
        old = issue.state
        issue.state = None
        # Publish restored bytes and the reopen event through normal transaction recovery.
        try:
            result = issue.commit(
                state,
                files,
                request,
                {"binding_generation": state["participants"][key]["generation"]},
                "restore",
            )
        # Restore the in-memory state reference when restoration publication fails.
        except BaseException:
            issue.state = old
            raise
        store.view(issue.id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(issue.committed_state(), key)
        return result
    # Import inspected Markdown while refusing any change to committed event history.
    if operation == "reconcile-files":
        require(state["disposition"] not in TERMINAL, "TERMINAL")
        require(
            binding
            and binding["issue_id"] == issue.id
            and binding["binding_generation"] == request.get("binding_generation"),
            "BINDING_MISSING",
        )
        authorize(state, request, coordinator=True)
        expected(state, request)
        evidence(request.get("evidence"))
        # Read the current payload through its original directory identity.
        with store.task.child(issue.id, private=False) as payload:
            require(payload.identity == state["directory_identity"], "UNSAFE_PATH")
            observed = inventory(payload)
        require(manifest(observed) == request.get("inventory"), "UNTRACKED_CHANGE")
        require(
            set(observed) == set(state["files"])
            and all(
                sha(b) == state["files"][p]
                for p, b in observed.items()
                if p == "events.jsonl" or SEGMENT.fullmatch(p)
            ),
            "INTEGRITY_ERROR",
        )
        # Explicit reconciliation imports inspected Markdown only, never arbitrary events.
        issue.state = copy.deepcopy(state)
        issue.state["files"] = manifest(observed)
        state["reconciliation"] = {"previous": state["files"], "evidence": request["evidence"]}
        return issue.commit(state, observed, request, {}, "reconciliation")
    # Load the validated committed payload before operating on participants or notes.
    files = issue.files()
    store.view(issue.id)
    # Internal child binding establishes its own generation; parent calls require a live binding.
    if operation in {"cursor-plan", "cursor-admit", "cursor-bind-child", "cursor-retire"}:
        if operation == "cursor-bind-child":
            require(not binding or binding["issue_id"] == issue.id, "BINDING_CONFLICT")
        else:
            require(
                binding
                and binding["issue_id"] == issue.id
                and binding["binding_generation"] == request.get("binding_generation"),
                "BINDING_MISSING",
            )
        return cursor_operation(store, issue, state, files, request)
    # Attach an independently identified reader without transferring coordinator authority.
    if operation == "join":
        # A verified-ticket startup may attach a new runtime session as a reader.
        # The core chooses the complete packet; callers cannot provide source
        # locators, ownership, or a replacement coordinator through this route.
        require(
            set(request)
            <= {
                "schema_version",
                "request_id",
                "operation",
                "worktree",
                "host",
                "session_id",
                "repo_id",
                "issue_id",
                "issue_uuid",
                "expected_revision",
                "binding_generation",
            },
            "INVALID_REQUEST",
        )
        require(request.get("issue_uuid") == state["issue_uuid"], "ISSUE_MISMATCH")
        require(key != state["coordinator"], "NOT_OWNER")
        expected(state, request)
        require(
            state["disposition"] not in TERMINAL and state["storage"] == "present",
            "RECOVERY_REQUIRED",
        )
        # Build the reader packet from the committed roadmap digest, never caller locators.
        join_packet: list[SourceRef] = [
            {
                "id": "roadmap",
                "locator": "roadmap.md",
                "sha256": state["files"]["roadmap.md"],
                "required": True,
                "authority": "task-workspace",
                "reason": "issue-resume",
                "stage": "planning",
                "reader": key,
            }
        ]
        join_existing = state["participants"].get(key)
        # Refresh only an existing roadmap-only reader with a matching binding generation.
        if join_existing:
            join_previous = join_existing["packet"]
            require(
                binding and binding["binding_generation"] == request.get("binding_generation"),
                "BINDING_MISSING",
            )
            require(
                join_previous is not None
                and len(join_previous) == 1
                and join_previous[0] == {**join_packet[0], "sha256": join_previous[0]["sha256"]}
                and state.get("assignments", {}).get(key) == {"packet": join_previous}
                and key not in state["owners"].values(),
                "SCOPE_MISSING",
            )
            join_existing.update({"packet": join_packet, "ack": None})
        else:
            # Reject a new reader that already has a different assigned scope.
            require(key not in state.get("assignments", {}), "SCOPE_MISSING")
        # Persist the bounded reader assignment before attachment and binding publication.
        state.setdefault("assignments", {})[key] = {"packet": join_packet}
        result = attach(state, request)
        result = issue.commit(state, files, request, result, "attach")
        store.view(issue.id)
        store.save_binding(issue.committed_state(), key)
        return result
    # Resume an assigned participant through the shared attachment contract.
    if operation in {"create", "attach", "resume", "bind"}:
        result = attach(state, request)
        # Publish this lifecycle result through the recoverable transaction.
        result = issue.commit(state, files, request, result, "attach")
        store.view(issue.id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(issue.committed_state(), key)
        return result
    require(
        binding
        and binding["issue_id"] == issue.id
        and binding["binding_generation"] == request.get("binding_generation"),
        "BINDING_MISSING",
    )
    # Permit maintenance participation only for the enumerated archive and recovery routes.
    maintenance = operation in {
        "archive-prepare",
        "archive-index",
        "archive-verify",
        "archive-save-start",
        "archive-observe-save",
        "cleanup-plan",
        "cleanup-commit",
        "reopen",
    }
    key, participant = authorize(state, request, maintenance=maintenance)
    result = {}
    event_type = operation
    # Require current revision for state-changing operations, allowing exact preparation retries.
    if operation in {
        "scope",
        "update",
        "checkpoint",
        "outcome",
        "transfer-coordinator",
        "reconcile-participant",
        "archive-prepare",
        "event-rollover",
        "reopen",
    }:
        prepared = (
            operation == "archive-prepare"
            and sha((request["request_id"] + ":prepare").encode()) in state["requests"]
        )
        # Check the caller revision unless preparation already committed under this request.
        if not prepared:
            expected(state, request)
    # Fence ordinary writes after terminal disposition while retaining recovery access.
    if operation not in {
        "read",
        "ready",
        "diagnose",
        "archive-prepare",
        "archive-index",
        "archive-verify",
        "cleanup-plan",
        "cleanup-commit",
        "detach",
        "reopen",
        "archive-save-start",
        "archive-observe-save",
    }:
        require(state["disposition"] not in TERMINAL, "TERMINAL")
    # Require coordinator authority for shared ownership, outcomes and archive operations.
    if operation in {
        "scope",
        "checkpoint",
        "outcome",
        "transfer-coordinator",
        "reconcile-participant",
        "archive-prepare",
        "archive-index",
        "archive-verify",
        "archive-save-start",
        "archive-observe-save",
        "cleanup-plan",
        "cleanup-commit",
        "event-rollover",
        "reopen",
    }:
        authorize(state, request, coordinator=True, maintenance=maintenance)
    # Assign reader-specific sources and invalidate any prior acknowledgment.
    if operation == "scope":
        target = request["target_participant"]
        require(DIGEST.fullmatch(target), "SCOPE_MISSING")
        packet = validate_packet(request["packet"])
        require(all(ref["reader"] == target for ref in packet), "SCOPE_MISSING")
        # An active child permits only digest refresh of its exact invocation packet.
        scoped_child = state["participants"].get(target)
        if scoped_child is not None and "cursor_invocation" in scoped_child:
            cursor_child_active(state, target, scoped_child)
            current_packet = scoped_child["packet"]
            require(
                current_packet is not None
                and len(packet) == len(current_packet)
                and all(
                    new == {**old, "sha256": new["sha256"]}
                    for old, new in zip(current_packet, packet)
                ),
                "SCOPE_MISSING",
            )
            invocation = state["cursor_invocations"][scoped_child["cursor_invocation"]]
            require(
                set(request.get("owned_paths", [])) <= set(invocation["owned_paths"]),
                "NOT_OWNER",
            )
        state.setdefault("assignments", {})[target] = {"packet": packet}
        # Update existing scope without reviving a detached generation.
        if target in state["participants"]:
            target_state = state["participants"][target]
            target_state.update({"packet": packet, "ack": None})
            # Invalidate readiness without reviving a detached participant generation.
            if target_state["status"] != "detached":
                target_state["status"] = "attached"
        # Assign only managed notes whose existing ownership agrees with the target reader.
        for path in request.get("owned_paths", []):
            payload_path(path)
            require(
                path != "roadmap.md" and state["owners"].get(path, target) == target, "NOT_OWNER"
            )
            require(
                not cursor_path_reserved(state, path),
                "NOT_OWNER",
            )
            state["owners"][path] = target
        result["packet_digest"] = sha(canonical(packet))
    # Validate the assigned bytes and establish or inspect exact-digest readiness.
    elif operation in {"read", "acknowledge", "ready"}:
        refs = packet_reads(state, participant, files)
        digest = sha(canonical(participant["packet"]))
        # Record acknowledgment only for the current assigned packet digest.
        if operation == "acknowledge":
            require(request.get("packet_digest") == digest, "SOURCE_STALE")
            # Cursor children need verified content and completed Reads for current sources.
            if "cursor_invocation" in participant:
                receipts = participant.get("cursor_sources", {})
                require(
                    all(
                        not ref["available"]
                        or receipts.get(ref["locator"], {})
                        == {
                            "packet_digest": digest,
                            "sha256": ref["sha256"],
                            "invocation_id": participant["cursor_invocation"],
                        }
                        for ref in refs
                    ),
                    "SOURCE_DELIVERY_REQUIRED",
                )
            participant.update({"ack": digest, "status": "ready"})
        # Require active disposition and a current acknowledged packet.
        elif operation == "ready":
            require(
                state["disposition"] not in TERMINAL
                and participant["ack"] == digest
                and participant["status"] == "ready",
                "NOT_READY",
            )
        # Return read-only packet evidence without creating a lifecycle event.
        if operation != "acknowledge":
            return {
                "ok": True,
                "code": "OK",
                "revision": state["revision"],
                "references": refs,
                "packet_digest": digest,
                "binding_generation": participant["generation"],
            }
        result.update(references=refs, packet_digest=digest)
    # Require file ownership, the current content digest, and attributable replacement content.
    elif operation == "update":
        path = payload_path(request["path"])
        require(state["owners"].get(path) == key, "NOT_OWNER")
        # A resumed child cannot reuse retained ownership from an older invocation.
        if participant.get("cursor_invocation") is not None:
            invocation = state["cursor_invocations"][participant["cursor_invocation"]]
            require(path in invocation["owned_paths"], "NOT_OWNER")
            # Current assigned bytes and independent acknowledgment precede every owned write.
            packet_reads(state, participant, files)
            require(
                participant["status"] == "ready"
                and participant["ack"] == sha(canonical(participant["packet"])),
                "NOT_READY",
            )
        require(request.get("old_digest") == state["files"].get(path), "REVISION_CONFLICT")
        data = request["content"].encode("utf-8")
        require(len(data) <= MAX_FILE, "SIZE_LIMIT")
        provenance = request["provenance"]
        require(set(provenance) == {"sources", "applicability", "status"}, "EVIDENCE_REQUIRED")
        evidence(provenance["sources"])
        token(provenance["applicability"])
        token(provenance["status"])
        state["provenance"][path] = {**provenance, "author": key, "supersedes": state["revision"]}
        files[path] = data
        # Keep the coordinator's own roadmap reference on the committed bytes and
        # require a fresh read, acknowledgment and readiness at the new revision.
        if path == "roadmap.md" and key == state["coordinator"]:
            refresh_roadmap_reference(state, key, sha(data))
    # Retain the bounded handoff record and evidence before marking a submitted PR in review.
    elif operation == "checkpoint":
        checkpoint = request["checkpoint"]
        require(
            set(checkpoint)
            == {"goal", "constraints", "sources", "progress", "blockers", "handoff", "candidate"},
            "EVIDENCE_REQUIRED",
        )
        require(len(canonical(checkpoint)) <= 16384, "SIZE_LIMIT")
        evidence(checkpoint["sources"])
        state.setdefault("checkpoint_history", []).append(checkpoint)
        state["checkpoint"] = checkpoint
        # Move to review only when the checkpoint names a submitted PR URL.
        if request.get("submitted_pr"):
            require(str(request["submitted_pr"]).startswith("https://"), "EVIDENCE_REQUIRED")
            state["disposition"] = "in_review"
            state["submitted_pr"] = request["submitted_pr"]
    # Require outcome evidence without bypassing pending work or completion obligations.
    elif operation == "outcome":
        disposition = request["disposition"]
        require(disposition in TERMINAL | {"active", "blocked", "in_review"})
        evidence(request.get("evidence"))
        # Prevent terminal state from stranding recorded pending operations.
        if disposition in TERMINAL:
            require(
                not any(p["pending"] for p in state["participants"].values()), "PENDING_OPERATION"
            )
        # Verify acceptance, merge, and obligation evidence plus the matching provider completion.
        if disposition == "completed":
            require(
                set(request.get("completion", {})) == {"human_acceptance", "merge", "obligations"},
                "EVIDENCE_REQUIRED",
            )
            # Validate each category of human acceptance, merge and obligation evidence.
            for refs in request["completion"].values():
                evidence(refs)
                # Read and hash every completion source from its explicit absolute locator.
                for ref in refs:
                    path = Path(ref["locator"])
                    require(path.is_absolute(), "EVIDENCE_REQUIRED")
                    # Verify completion evidence bytes through safe parent-directory traversal.
                    with Directory.absolute(path.parent) as source:
                        require(
                            sha(source.read(path.name, MAX_FILE)) == ref["sha256"], "SOURCE_STALE"
                        )
            provider = request.get("provider_status", {})
            require(
                set(provider)
                == {"origin", "issue_uuid", "status_type", "completed_at", "request_id"},
                "EVIDENCE_REQUIRED",
            )
            require(
                provider["origin"] == "linear_get_issue"
                and provider["issue_uuid"] == state["issue_uuid"]
                and provider["status_type"] == "completed"
                and provider["completed_at"],
                "EVIDENCE_REQUIRED",
            )
            token(provider["request_id"])
            state["provider_completion"] = provider
        # Require explicit abandonment evidence before recording failed disposition.
        if disposition == "failed":
            require(request.get("abandoned") is True, "EVIDENCE_REQUIRED")
        state["disposition"] = disposition
        state["outcome"] = {
            "disposition": disposition,
            "evidence": request["evidence"],
            "completion": request.get("completion"),
            "at": now(),
        }
        state.setdefault("outcome_history", []).append(state["outcome"])
    # Accept only the bounded event vocabulary; arbitrary payload content is not an event.
    elif operation == "event":
        require(
            request.get("event_type") in {"check", "tool-start", "tool-complete", "observation"}
        )
        require(set(request.get("event", {})) <= {"code"}, "INVALID_REQUEST")
        require(
            request.get("event", {}).get("code", "OK") in {"OK", "FAILED", "UNKNOWN", "INTERRUPTED"}
        )
        event_type = request["event_type"]
    # Only correlated native source callbacks can establish Cursor source receipts.
    elif operation == "cursor-source-callback":
        result = cursor_source_callback(state, files, request, participant)
    # Track exact source preparation before readiness without admitting ordinary work.
    elif operation in {"tool-start", "source-read-start"}:
        refs = packet_reads(state, participant, files)
        if operation == "source-read-start":
            require(
                request.get("packet_digest") == sha(canonical(participant["packet"])),
                "SOURCE_STALE",
            )
            require(
                any(
                    ref["locator"] == request.get("source_locator") and ref["available"]
                    for ref in refs
                )
                and request.get("poll_handle") is None,
                "SCOPE_MISSING",
            )
        # Ordinary tool admission still requires the acknowledged current source packet.
        else:
            require(
                participant["ack"] == sha(canonical(participant["packet"]))
                and participant["status"] == "ready",
                "NOT_READY",
            )
        tool = token(request["tool_id"])
        require(tool not in participant["pending"], "REQUEST_CONFLICT")
        # Associate an observed polling transport without borrowing another participant's work.
        pending: PendingOperation = {"status": "pending"}
        # Cursor source effects carry their exact current assignment through both native callbacks.
        if operation == "source-read-start" and "cursor_invocation" in participant:
            require(request["host"] == "cursor", "HOST_UNSUPPORTED")
            selected = next(
                ref
                for ref in refs
                if ref["locator"] == request["source_locator"] and ref["available"]
            )
            locator = selected["locator"]
            source_path = (
                Path(locator)
                if Path(locator).is_absolute()
                else store.root / ".task" / issue.id / locator
            )
            pending["cursor_source"] = {
                "invocation_id": participant["cursor_invocation"],
                "generation": participant["generation"],
                "packet_digest": request["packet_digest"],
                "locator": locator,
                "sha256": selected["sha256"],
                "file_path": str(source_path),
                "provisional": None,
            }
        # Bind a polling transport only to a unique pending process handle.
        if request.get("poll_handle") is not None:
            handle: str | None = token(request["poll_handle"])
            matches = [
                identifier
                for identifier, entry in participant["pending"].items()
                if entry.get("handle") == handle
            ]
            require(len(matches) == 1, "UNKNOWN_OPERATION")
            assert handle is not None  # token() returned a validated string above.
            require(
                "cursor_invocation" not in participant["pending"][matches[0]],
                "REQUEST_CONFLICT",
            )
            pending.update({"poll_parent": matches[0], "poll_handle": handle})
        # Reserve and persist the transport independently from its original process.
        participant["pending"][tool] = pending
    # Validate observation identity before settling or retaining asynchronous work.
    elif operation == "tool-complete":
        # Resolve the observed call ID and validate optional handle metadata first.
        observed_tool = token(request["tool_id"])
        tool = observed_tool
        handle = token(request["async_handle"]) if request.get("async_handle") is not None else None
        transport = None
        # Resolve polling completion against its recorded original process.
        if request.get("poll"):
            # A poll must identify one original process, or its recorded completed parent.
            require(handle is not None, "UNKNOWN_OPERATION")
            matches = [
                identifier
                for identifier, entry in participant["pending"].items()
                if entry.get("handle") == handle
            ]
            transport = participant["pending"].get(observed_tool)
            # Verify the transport-to-process relationship before settling either operation.
            if transport is not None:
                # Validate the pre-hook's explicit relationship, including concurrent final polls.
                require(
                    transport.get("poll_handle") == handle and transport.get("poll_parent"),
                    "REQUEST_CONFLICT",
                )
                tool = transport["poll_parent"]
                expected_matches = [tool] if tool in participant["pending"] else []
                require(matches == expected_matches, "UNKNOWN_OPERATION")
            else:
                # Hosts that omit polling pre-hooks still require unique
                # participant-local correlation.
                require(len(matches) == 1, "UNKNOWN_OPERATION")
                tool = matches[0]
        # Generic observations cannot settle a Task whose child must retire atomically.
        invocation_pending = participant["pending"].get(tool)
        require(
            invocation_pending is None
            or not {"cursor_invocation", "cursor_source"}.intersection(invocation_pending),
            "REQUEST_CONFLICT",
        )
        # An already settled parent is allowed only through its still-recorded poll transport.
        require(tool in participant["pending"] or transport is not None, "UNKNOWN_OPERATION")
        # Update or settle the original process only while its pending record exists.
        if tool in participant["pending"]:
            pending = participant["pending"][tool]
            # Reject a completion that changes an already observed process handle.
            if handle is not None and pending.get("handle") is not None:
                require(pending["handle"] == handle, "REQUEST_CONFLICT")
            # Explicit process completion wins only after the handle and relationship checks.
            if request.get("completed") is True:
                del participant["pending"][tool]
            # Retain asynchronous process uncertainty until typed completion is observed.
            elif handle is not None:
                # A repeated process observation is event-free only when no transport is retiring.
                if pending["status"] == "unknown" and transport is None:
                    event_type = None
                participant["pending"][tool] = {"status": "unknown", "handle": handle}
            else:
                # Keep the operation pending when completion carries no usable evidence.
                raise WorkspaceError("UNKNOWN_OPERATION")
        # The returned polling call is finished even when its original process is still running.
        if transport is not None:
            del participant["pending"][observed_tool]
    # Require the exact participant generation and no pending work before detachment.
    elif operation in {"detach", "reconcile-participant"}:
        target = key if operation == "detach" else request["target_participant"]
        member = state["participants"][target]
        # Invocation retirement is the only path that releases a classified child generation.
        require("cursor_invocation" not in member, "NOT_OWNER")
        require(
            request.get("target_generation", request.get("binding_generation"))
            == member["generation"],
            "STALE_BINDING",
        )
        evidence(request.get("evidence"))
        require(not member["pending"], "PENDING_OPERATION")
        member["status"], member["ack"] = "detached", None
    # Transfer roadmap ownership with the coordinator role and retain the supplied evidence.
    elif operation == "transfer-coordinator":
        target = request["target_participant"]
        require(target in state["participants"] and not participant["pending"], "PENDING_OPERATION")
        # A child assignment never grants coordinator or roadmap authority.
        require("cursor_invocation" not in state["participants"][target], "NOT_OWNER")
        evidence(request.get("evidence"))
        state["coordinator"] = target
        state["owners"]["roadmap.md"] = target
    # Reopen terminal work only with evidence and require a fresh packet acknowledgment.
    elif operation == "reopen":
        evidence(request.get("evidence"))
        require(state["disposition"] in TERMINAL, "INVALID_REQUEST")
        state["disposition"] = "active"
        participant.update({"status": "attached", "ack": None})
    # Rotate only bytes covered by the current verified provider checkpoint.
    elif operation == "event-rollover":
        require(not any(p["pending"] for p in state["participants"].values()), "PENDING_OPERATION")
        rollover_receipt, rollover_export = state.get("archive"), state.get("export")
        require(
            rollover_receipt
            and rollover_export
            and rollover_receipt["snapshot"] == rollover_export["snapshot"]
            and rollover_export["files"] == manifest(files)
            and rollover_export["revision"] == state["revision"],
            "ARCHIVE_PENDING",
        )
        assert rollover_receipt is not None  # Established by the verified-export guard.
        data = files["events.jsonl"]
        require(data, "ARCHIVE_PENDING")
        first = strict_json(data.splitlines()[0])["seq"]
        name = f"events-{first:012d}-{state['seq']:012d}.jsonl"
        require(SEGMENT.fullmatch(name) and name not in files, "INTEGRITY_ERROR")
        # Retain exact checkpointed bytes. The same transaction publishes the segment,
        # resets the active stream and appends the next globally chained event.
        files[name], files["events.jsonl"] = data, b""
        state.setdefault("event_segments", []).append(
            {
                "path": name,
                "first": first,
                "last": state["seq"],
                "head": state["head"],
                "sha256": sha(data),
                "archive": rollover_receipt,
            }
        )
        result = {"segment": name, "snapshot": rollover_receipt["snapshot"]}
    # Commit preparation before freezing the immutable reconstructable snapshot.
    elif operation == "archive-prepare":
        require(not any(p["pending"] for p in state["participants"].values()), "PENDING_OPERATION")
        # Seal terminal work only after every other participant has detached.
        if request.get("seal"):
            require(
                state["disposition"] in TERMINAL
                and all(
                    k == key or p["status"] == "detached" for k, p in state["participants"].items()
                ),
                "RETAINED",
            )
            participant["status"] = "maintenance"
        # Commit seal/preparation event BEFORE the frozen snapshot.
        prepare_request = {**request, "request_id": request["request_id"] + ":prepare"}
        prior = state["requests"].get(sha(prepare_request["request_id"].encode()))
        # Verify an exact preparation retry before reusing its frozen-state boundary.
        if prior:
            require(prior["digest"] == sha(canonical(prepare_request)), "REQUEST_CONFLICT")
        else:
            # Publish this lifecycle result through the recoverable transaction.
            issue.commit(state, files, prepare_request, {}, "archive-prepare")
        state = copy.deepcopy(issue.committed_state())
        files = issue.files()
        snapshot = archive_payload(state, files)
        export: JSONObject = dict(export_documents(snapshot))
        state["export"] = {
            "snapshot": export["snapshot"],
            "revision": state["revision"],
            "files": manifest(files),
            "request_id": request["request_id"],
        }
        require(len(canonical(export)) <= MAX_REQUEST, "ARCHIVE_PENDING")
        issue.control.put("export-" + export["snapshot"] + ".json", export)
        return issue.commit(state, files, request, export)
    # Build the index only from matching independent part identities and versions.
    elif operation == "archive-index":
        export = issue.control.json("export-" + state["export"]["snapshot"] + ".json")
        observations = request["observations"]
        require(len(observations) == len(export["parts"]), "ARCHIVE_PENDING")
        descriptors = []
        # Match every independent part read-back to the immutable requested content.
        for observed, wanted in zip(observations, export["parts"]):
            part = provider_observation(observed, state["issue_uuid"])
            require(part == parse_document(wanted["content"]), "ARCHIVE_PENDING")
            descriptors.append(
                {
                    "id": observed["id"],
                    "updatedAt": observed["updatedAt"],
                    "sha256": sha(canonical(part)),
                }
            )
        require(len({d["id"] for d in descriptors}) == len(descriptors), "ARCHIVE_PENDING")
        root = {
            "schema_version": 1,
            "kind": "index",
            "snapshot": export["snapshot"],
            "parts": descriptors,
        }
        content = document(
            root,
            f"# {issue.id} verified archive index\n\nDisposition: {state['disposition']}.\n"
            "Reconstructable roadmap, context and event history are in the numbered parts.\n"
            "This archive is evidence, not human acceptance or execution authority.",
        )
        result = {
            "issue": state["issue_uuid"],
            "title": f"{issue.id} snapshot {export['snapshot']} index",
            "content": content,
        }
        require(len(content.encode()) <= DOCUMENT_LIMIT, "ARCHIVE_PENDING")
        state["index_request"] = result.copy()
        event_type = None
    # Track save uncertainty by immutable content digest to prevent blind duplicate saves.
    elif operation in {"archive-save-start", "archive-observe-save"}:
        export = issue.control.json("export-" + state["export"]["snapshot"] + ".json")
        documents = export["parts"] + (
            [state["index_request"]] if state.get("index_request") else []
        )
        digest = request["content_digest"]
        require(digest in {sha(doc["content"].encode()) for doc in documents}, "ARCHIVE_PENDING")
        saves = state.setdefault("provider_saves", {})
        # Reserve a content digest before an uncertain external save can be retried.
        if operation == "archive-save-start":
            require(
                digest not in saves,
                "ARCHIVE_PENDING",
                "Uncertain save: locate and read the matching document before another save.",
            )
            saves[digest] = {"status": "uncertain"}
        else:
            # Record the observed provider document without allowing identity replacement.
            document_id = token(request["document_id"])
            existing = saves.get(digest, {})
            require(not existing.get("id") or existing["id"] == document_id, "ARCHIVE_PENDING")
            saves[digest] = {"status": "readback-required", "id": document_id}
        event_type = None
    # Require provider bytes and revision to match the current local export exactly.
    elif operation in {"archive-verify", "cleanup-commit"}:
        snapshot, archived, receipt = verify_provider(state, request.get("observations"))
        require(
            manifest(archived) == manifest(files) == state["export"]["files"]
            and snapshot["revision"] == state["revision"] == state["export"]["revision"],
            "ARCHIVE_PENDING",
        )
        # Retain the verified archive receipt without authorizing payload deletion.
        if operation == "archive-verify":
            state["archive"] = receipt
            result = {"receipt": receipt}
            event_type = None
        else:
            # Recheck cleanup eligibility before preparing deletion intent.
            eligible(state)
            require(
                receipt["root"]["id"] == state["archive"]["root"]["id"]
                and receipt["root"]["updatedAt"] == state["archive"]["root"]["updatedAt"],
                "ARCHIVE_PENDING",
            )
            # Require fresh read-backs bearing this cleanup challenge before recording
            # deletion intent.
            challenge = request.get("cleanup_challenge")
            require(
                challenge
                and challenge == state.get("cleanup_challenge")
                and all(o["request_id"] == challenge for o in request["observations"]),
                "ARCHIVE_PENDING",
            )
            state["archive"] = receipt
            result = {
                "ok": True,
                "code": "CLEANED",
                "revision": state["revision"],
                "issue_id": issue.id,
            }
            state["requests"][sha(request["request_id"].encode())] = {
                "digest": sha(canonical(request)),
                "result": result,
            }
            intent = {
                "state": state,
                "files": state["files"],
                "directory_identity": state["directory_identity"],
                "quarantine": "quarantine-" + uuid.uuid4().hex,
                "request_id": request["request_id"],
            }
            issue.control.put("cleanup.json", intent)
            fault("cleanup-intent")
            finish_cleanup(issue)
            return result
    # Issue a new challenge for independent read-backs of the verified archive.
    elif operation == "cleanup-plan":
        eligible(state)
        state["cleanup_challenge"] = str(uuid.uuid4())
        result = {
            "code": "READBACK_REQUIRED",
            "cleanup_challenge": state["cleanup_challenge"],
            "root": state["archive"]["root"],
            "parts": state["archive"]["parts"],
        }
        event_type = None
    else:
        # Reject operations outside the explicit lifecycle dispatch vocabulary.
        raise WorkspaceError("UNSUPPORTED_OPERATION")
    return issue.commit(state, files, request, result, event_type)


def rebind(store: Store, request: JSONObject) -> JSONObject:
    """Move one explicit session binding while fencing and preserving its old issue.

    ``issue_id`` and ``binding_generation`` name the old binding; ``issue_uuid`` names
    the target's provider identity. An existing target keeps its assignment rule: it
    must already assign the caller or name it coordinator. An absent target is
    created from the packaged template with the caller as its coordinator, which
    requires ``issue_uuid``. The old issue must be the session's live binding at the
    stated generation, and a detached old member moves only as this request's own retry.
    Either way the old participant is detached and the old payload and coordinator
    ownership stay unchanged.

    Args:
        store: Validated shared repository Store.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The target participant binding result.

    Raises:
        WorkspaceError: If target assignment or identity, the live binding, generations,
            pending work, caller kind or retry identity conflicts, or an unadopted target
            payload exists.
        OSError: If either issue or binding cannot be persisted.
    """
    # Validate distinct old and target issue identities before rebinding.
    old_id, new_id = issue_id(request["issue_id"]), issue_id(request["new_issue_id"])
    require(old_id != new_id, "BINDING_CONFLICT")
    # A subagent holds only a coordinator-scoped assignment; it never moves a binding.
    require("/agent/" not in token(request["session_id"]), "NOT_OWNER")
    key = participant_key(request)
    evidence(request.get("evidence"))
    # Manage both issue-lock lifetimes as one ordered rebinding operation.
    with ExitStack() as stack:
        controls = {}
        # Lock both issues in stable order; only the target's control may be created here.
        for identifier in sorted([old_id, new_id]):
            control = stack.enter_context(store.issues.child(identifier, identifier == new_id))
            stack.enter_context(control.lock())
            controls[identifier] = control
        old, new = Issue(store, controls[old_id], old_id), Issue(store, controls[new_id], new_id)
        old.recover()
        new.recover()
        require(old.state, "RECOVERY_REQUIRED")
        creating = not new.state
        old_files = old.files()
        old_state = copy.deepcopy(old.committed_state())
        # Require the caller's current old participation with no unresolved work; a
        # coordinator leaves only after every other participant has detached.
        member = old_state["participants"].get(key)
        require(member and member["generation"] == request["binding_generation"], "STALE_BINDING")
        assert member is not None  # Established by the generation guard above.
        require(not member["pending"], "PENDING_OPERATION")
        require(
            key != old_state["coordinator"]
            or all(
                k == key or p["status"] == "detached" for k, p in old_state["participants"].items()
            ),
            "PENDING_OPERATION",
        )
        # Build the request from explicit caller or observed session identities.
        target_request = {
            **request,
            "operation": "rebind",
            "issue_id": new_id,
            "old_issue_id": old_id,
            "binding_generation": request.get("new_binding_generation"),
        }
        previous: RequestRecord | None = None
        # Create an absent target only from its provider identity and an unused payload path.
        if creating:
            require(isinstance(request.get("issue_uuid"), str), "INVALID_REQUEST")
            require(not store.task.exists(new_id), "ADOPTION_REQUIRED")
            new_state_value = new_state(store, {**target_request, "coordinator": key})
            new_files = initial_payload(new_id)
        else:
            # An existing target must match any supplied provider identity.
            new_files = new.files()
            new_state_value = copy.deepcopy(new.committed_state())
            require(
                not request.get("issue_uuid")
                or request["issue_uuid"] == new_state_value["issue_uuid"],
                "ISSUE_MISMATCH",
            )
            previous = new_state_value["requests"].get(sha(request["request_id"].encode()))
        # The old issue must be this worktree's live session binding at the stated
        # generation; only a replay of the committed target may find it already moved.
        binding = store.binding()
        require(
            binding is not None
            and (
                (
                    binding["issue_id"] == old_id
                    and binding["binding_generation"] == request["binding_generation"]
                )
                or (previous is not None and binding["issue_id"] == new_id)
            ),
            "STALE_BINDING",
        )
        # A detached old member proceeds only as this request's own retry: a replay of the
        # committed target, or a retry after this request already retired it.
        retire_request = {**request, "request_id": request["request_id"] + ":retire"}
        retired = old_state["requests"].get(sha(retire_request["request_id"].encode()))
        require(
            previous is not None
            or member["status"] != "detached"
            or (retired is not None and retired["digest"] == sha(canonical(retire_request))),
            "STALE_BINDING",
        )
        # Replay only an identical request; a reused ID with different content is a conflict.
        if previous:
            require(previous["digest"] == sha(canonical(target_request)), "REQUEST_CONFLICT")
            store.view(new_id)
            # Persist the committed participant generation for this worktree.
            store.save_binding(new.committed_state(), key)
            assignment = key + ".assignment.json"
            # Refresh the explicit Task assignment after replaying the committed rebind.
            if store.bindings.exists(assignment):
                store.bindings.put(assignment, {"issue_id": new_id})
            previous_result: JSONObject = previous["result"]
            return previous_result
        result = attach(new_state_value, target_request)
        # Retire the old participant before publishing the new binding; retries can finish
        # publication.
        member.update({"status": "detached", "ack": None})
        # Commit retirement only when the old participant still requires a state change.
        if member != old.committed_state()["participants"][key]:
            old.commit(
                old_state,
                old_files,
                retire_request,
                {},
                "rebind",
            )
        # Publish this lifecycle result through the recoverable transaction.
        result = new.commit(new_state_value, new_files, target_request, result, "rebind")
        store.view(new_id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(new.committed_state(), key)
        assignment = key + ".assignment.json"
        # Validate any existing entry before reusing or replacing it.
        if store.bindings.exists(assignment):
            store.bindings.put(assignment, {"issue_id": new_id})
        return result


def collect_candidates(store: Store, request: JSONObject) -> list[JSONObject]:
    """Attempt cleanup only for explicitly supplied separately bound maintenance sessions.

    Args:
        store: Validated shared repository Store.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        Per-issue cleanup results, including retained or failed candidates.

    Raises:
        WorkspaceError: If the candidate list or session separation is invalid.
    """
    # Validate the bounded candidate list before attempting independent cleanups.
    candidates = request["cleanup_candidates"]
    require(isinstance(candidates, list) and len(candidates) <= 16)
    results = []
    # Attempt only explicitly listed issues, each with its own maintenance session.
    for candidate in candidates:
        identifier = issue_id(candidate["issue_id"])
        require(identifier != request["issue_id"], "INVALID_REQUEST")
        # A separate bound maintenance session avoids retiring the new task's owner.
        cleanup = {
            **candidate,
            "schema_version": 1,
            "operation": "cleanup-commit",
            "request_id": request["request_id"] + ":collect:" + identifier,
            "worktree": request["worktree"],
            "repo_id": request["repo_id"],
        }
        require(cleanup.get("session_id") != request["session_id"], "BINDING_CONFLICT")
        results.append({"issue_id": identifier, **execute(cleanup)})
    return results


def permission_paths(request: JSONObject) -> list[str]:
    """Derive narrow issue/control paths for a failed filesystem permission request.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The paths relevant to registration or the requested issue.
    """
    # Start with the requested worktree as the fallback permission boundary.
    root = Path(request.get("worktree", "/"))
    # Resolve the canonical worktree when Git metadata remains accessible.
    try:
        root = repository(root)[0]
    # Retain the supplied path when repository discovery is unavailable.
    except (OSError, WorkspaceError, subprocess.SubprocessError):
        pass
    main = request.get("main_worktree")
    # Recover the canonical main worktree from local registration when not supplied.
    if main is None:
        # Read registration through validated directory handles when permission permits.
        try:
            # Read the local registration without following substituted filesystem links.
            with Directory.absolute(root) as directory, directory.child(".task") as local:
                main = local.json(".repository.json")["main"]
        # Keep recovery paths local when registration cannot be read safely.
        except (OSError, WorkspaceError, KeyError):
            pass
    # Build the narrow worktree registration and binding permission requests.
    paths = [str(root / ".task/.repository.json"), str(root / ".task/.bindings")]
    # Include canonical store paths only when its main-worktree location is known.
    if main:
        store = Path(main) / ".task"
        paths.append(str(store / ".control/repository.json"))
        identifier = request.get("issue_id")
        # Name only the selected issue payload and control directory.
        if isinstance(identifier, str) and ISSUE.fullmatch(identifier):
            paths.extend([str(store / identifier), str(store / ".control/issues" / identifier)])
        # Request store creation paths only for explicit registration.
        elif request.get("operation") == "register":
            paths.extend([str(store), str(store / ".control")])
    return paths


def execute(request: JSONObject) -> JSONObject:
    """Execute a bounded request and return safe diagnostics without exception contents.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        A success result or a structured failure with a public code and recovery action.
    """
    # Validate the bounded versioned request before dispatching any operation.
    try:
        require(isinstance(request, dict) and len(canonical(request)) <= MAX_REQUEST)
        require(request.get("schema_version") == 1)
        token(request["request_id"])
        operation = request["operation"]
        # Create registration without accepting a caller-selected repository identity.
        if operation == "register":
            require("repo_id" not in request, "INVALID_REQUEST")
            return register(request)
        # Allow registration discovery before the caller knows the repository ID.
        if operation == "diagnose" and "repo_id" not in request:
            root, _, _ = repository(request["worktree"])
            with Directory.absolute(root) as directory:
                # Validate any existing entry before reusing or replacing it.
                if not directory.exists(".task"):
                    return {"ok": True, "code": "REGISTRATION_REQUIRED"}
                with directory.child(".task") as local:
                    # Validate any existing entry before reusing or replacing it.
                    if not local.exists(".repository.json"):
                        return {"ok": True, "code": "REGISTRATION_REQUIRED"}
                    # Read repository registration through validated directory handles.
                    reg = local.json(".repository.json")
                    return {"ok": True, "code": "REGISTERED", "repo_id": reg["repo_id"]}
        identifier = issue_id(request["issue_id"])
        token(request["repo_id"])
        participant_key(request)
        # Serialize session-binding changes before acquiring the target issue lock.
        with Store(request) as store, store.bindings.lock(participant_key(request) + ".lock"):
            # Route cross-issue binding changes through the ordered two-issue lock path.
            if operation == "rebind":
                return rebind(store, request)
            # Serialize target-issue recovery and mutation under its control lock.
            with store.issues.child(identifier, True) as control, control.lock():
                result = operate(store, Issue(store, control, identifier), request)
            # Attempt explicitly supplied maintenance cleanups only after successful creation.
            if result["ok"] and operation == "create" and request.get("cleanup_candidates"):
                result["collection"] = collect_candidates(store, request)
            return result
    # Return the bounded lifecycle diagnostic; a stale packet also names what changed.
    except WorkspaceError as error:
        failure: JSONObject = {"ok": False, "code": error.code, "action": error.action}
        if isinstance(error, StaleSourceError):
            failure["stale"] = copy.deepcopy(error.stale)
        return failure
    # Return only the narrow paths needed to retry the denied filesystem operation.
    except PermissionError:
        paths = permission_paths(request)
        return {
            "ok": False,
            "code": "PERMISSION_REQUIRED",
            "paths": paths,
            "operation": request.get("operation"),
            "action": (
                "Grant only the listed issue/control and binding paths, then retry this operation."
            ),
        }
    # Retain data when filesystem or Git checks fail; require explicit recovery.
    except (OSError, subprocess.SubprocessError):
        return {
            "ok": False,
            "code": "RECOVERY_REQUIRED",
            "action": "Inspect filesystem identity and unfinished transactions; retain all data.",
        }
    # Translate malformed requests without exposing exception contents.
    except (KeyError, TypeError, ValueError, UnicodeError, AttributeError):
        return {
            "ok": False,
            "code": "INVALID_REQUEST",
            "action": "Use the documented version 1 request fields.",
        }


def main() -> int:
    """Read one bounded CLI request and emit one canonical result.

    Returns:
        Zero for success or three for a reported request failure.
    """
    # Configure the single-request CLI without adding alternate bootstrap commands.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request-json", help="One bounded JSON object; bootstrap-safe argument route"
    )
    args = parser.parse_args()
    # Read and validate one request within the CLI error boundary.
    try:
        # Select the explicit argument or bounded standard input, then parse one request.
        raw = (
            args.request_json.encode()
            if args.request_json is not None
            else sys.stdin.buffer.read(MAX_REQUEST + 1)
        )
        require(len(raw) <= MAX_REQUEST, "SIZE_LIMIT")
        request = strict_json(raw)
        require(isinstance(request, dict), "INVALID_REQUEST")
        require(isinstance(request.get("operation"), str), "INVALID_REQUEST")
        require(request.get("operation") not in CURSOR_INTERNAL_OPERATIONS, "HOST_UNSUPPORTED_TOOL")
        result = execute(request)
    # Return the bounded lifecycle diagnostic without exposing exception contents.
    except WorkspaceError as error:
        result = {"ok": False, "code": error.code}
    # Emit one canonical result and translate its status into the process exit code.
    print(canonical(result).decode())
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
