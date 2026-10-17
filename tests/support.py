"""Provide disposable repositories and synthetic evidence for lifecycle and adapter tests.

These fixtures call the canonical production modules without claiming live host acceptance.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import CodeType

from nplet.lifecycle import task_workspace as w
from tests.types import JsonObject, JsonValue

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE_PREFIX = os.path.normcase(str(Path(w.__file__).resolve().parents[1]) + os.sep)


@contextmanager
def fixture_diagnostics() -> Iterator[deque[JsonObject]]:
    """Observe recent errors handled by this thread's package source code.

    Yields:
        Scalar exception metadata for failed reports, never frame locals or requests.
        An unavailable marker when all monitoring slots already belong to other tools.
    """
    # Reserve a free slot without replacing coverage, debuggers or concurrent observers.
    diagnostics: deque[JsonObject] = deque(maxlen=16)
    tool = None
    for candidate in range(5, -1, -1):
        try:
            sys.monitoring.use_tool_id(candidate, "fixture-diagnostics")
        except ValueError:
            continue
        tool = candidate
        break
    # Continue with the original dispatch behavior when instrumentation is unavailable.
    if tool is None:
        diagnostics.append({"unavailable": "All sys.monitoring tool slots are occupied"})
        yield diagnostics
        return
    owner_thread = threading.get_ident()

    def handled(code: CodeType, offset: int, error: BaseException) -> None:
        """Retain only selected errors handled inside the package source boundary.

        Args:
            code: Code object handling the exception.
            offset: Bytecode offset supplied by the monitoring API.
            error: Handled exception, inspected without retaining frames or locals.
        """
        # Filter lexical source paths without filesystem work inside the callback.
        if (
            not os.path.normcase(code.co_filename).startswith(PACKAGE_SOURCE_PREFIX)
            or threading.get_ident() != owner_thread
            or not isinstance(error, (OSError, subprocess.SubprocessError))
        ):
            return
        # Bound text and traceback locations; never inspect process output or frame locals.
        record: JsonObject = {"type": type(error).__name__, "message": str(error)[:512]}
        for name in ("errno", "winerror", "filename", "filename2"):
            value = getattr(error, name, None)
            if value is not None:
                record[name] = value if isinstance(value, int) else str(value)[:512]
        # Expose the timed-out command and configured limit without stdout or stderr.
        if isinstance(error, subprocess.TimeoutExpired):
            record["command"] = str(error.cmd)[:512]
            record["timeout"] = error.timeout
        record["traceback"] = [
            f"{frame.f_code.co_filename}:{line}:{frame.f_code.co_name}"[:512]
            for frame, line in traceback.walk_tb(error.__traceback__)
        ][-8:]
        diagnostics.append(record)

    # Enable the global event: EXCEPTION_HANDLED does not support local events.
    try:
        sys.monitoring.register_callback(tool, sys.monitoring.events.EXCEPTION_HANDLED, handled)
        sys.monitoring.set_events(tool, sys.monitoring.events.EXCEPTION_HANDLED)
        yield diagnostics
    # Release only this observer's events, callback and slot on every dispatch exit.
    finally:
        sys.monitoring.set_events(tool, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.EXCEPTION_HANDLED, None)
        sys.monitoring.free_tool_id(tool)


def call_process(request: JsonObject) -> JsonObject:
    """Dispatch one lifecycle request in a multiprocessing worker.

    Args:
        request: Complete lifecycle request for a disposable repository.

    Returns:
        The core response, including unsuccessful results for asserted failures.
    """
    return w.execute(request)


class Fixture(unittest.TestCase):
    """Provide disposable Git worktrees and synthetic lifecycle evidence.

    Helpers call the real local core. Provider observations and evidence markers
    are test fixtures, not live provider read-back or human acceptance.
    """

    temp: tempfile.TemporaryDirectory[str]
    root: Path
    other: Path
    serial: int
    base: JsonObject

    def setUp(self) -> None:
        """Create isolated worktrees and register their shared repository identity.

        Temporary files are removed through unittest cleanup even if setup fails.

        Raises:
            OSError: Temporary filesystem setup fails.
            subprocess.CalledProcessError: Git initialization or worktree creation fails.
            AssertionError: A registration request is rejected.
        """
        # Allocate a disposable root and register cleanup before filesystem setup.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "main"
        self.root.mkdir()
        # Initialize a committed main repository and its linked worktree.
        self.git("init", "-q")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        )
        self.other = Path(self.temp.name).resolve() / "other"
        self.git("worktree", "add", "-q", "-b", "other", str(self.other))
        # Define the explicit coordinator and issue identity for fixture requests.
        self.serial = 0
        self.base = {
            "schema_version": 1,
            "worktree": str(self.root),
            "session_id": "coordinator",
            "host": "codex",
            "issue_id": "TEST-1",
            "issue_uuid": "fixture-issue-uuid",
        }
        self.base["coordinator"] = w.participant_key(self.base)
        # Register both worktrees against the same canonical repository.
        reg = self.call("register", main_worktree=str(self.root))
        self.base["repo_id"] = reg["repo_id"]
        self.require_ok(
            w.execute(
                self.req(
                    "register", worktree=str(self.other), main_worktree=str(self.root), repo_id=None
                )
            )
        )

    def git(self, *args: str) -> str:
        """Run Git against the disposable main worktree.

        Args:
            *args: Git arguments passed directly without a shell.

        Returns:
            Captured standard output as text.

        Raises:
            OSError: The Git process cannot start.
            subprocess.CalledProcessError: Git returns a nonzero exit status.
        """
        # Run Git without shell interpolation and capture checked output.
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout

    def req(self, operation: str, **kwargs: JsonValue) -> JsonObject:
        """Build a fresh fixture request with explicit per-call overrides.

        Args:
            operation: Lifecycle operation to dispatch.
            **kwargs: Overrides of the fixture defaults; None removes a field.

        Returns:
            A request with a fresh UUID and no None-valued fields.
        """
        # Advance fixture numbering and apply explicit overrides to a fresh request.
        self.serial += 1
        request = {**self.base, "operation": operation, "request_id": str(uuid.uuid4()), **kwargs}
        # Omit fields removed by None overrides before dispatch.
        return {k: v for k, v in request.items() if v is not None}

    def require_ok(self, result: JsonObject) -> JsonObject:
        """Require a successful core response and return it unchanged.

        Args:
            result: Core response containing the required ok field.

        Returns:
            The same successful response.

        Raises:
            AssertionError: The response reports failure.
        """
        # Reject an unsuccessful response before exposing it to the caller.
        self.assertTrue(result["ok"], result)
        return result

    def call(self, operation: str, **kwargs: JsonValue) -> JsonObject:
        """Build and dispatch a fixture request that is expected to succeed.

        Args:
            operation: Lifecycle operation to dispatch.
            **kwargs: Request overrides forwarded to req.

        Returns:
            The successful core response.

        Raises:
            AssertionError: The core rejects the request.
        """
        # Dispatch the constructed request and require its declared success.
        return self.require_ok(w.execute(self.req(operation, **kwargs)))

    def create(self) -> JsonObject:
        """Create the fixture issue and remember its returned binding generation.

        Returns:
            The successful creation response.

        Raises:
            AssertionError: Creation fails.
        """
        # Create once and retain the generation needed by later requests.
        result = self.call("create")
        self.base["binding_generation"] = result["binding_generation"]
        return result

    def state(self) -> JsonObject:
        """Read the committed control state of the primary fixture issue.

        Returns:
            The decoded state dictionary.

        Raises:
            OSError: The control state cannot be read.
            json.JSONDecodeError: The state file is not valid JSON.
        """
        # Read and decode the current committed control file.
        return json.loads((self.root / ".task/.control/issues/TEST-1/state.json").read_text())

    def evidence(self) -> list[JsonObject]:
        """Return a synthetic evidence marker for local request validation.

        Returns:
            A fixture locator and placeholder digest, not verified acceptance evidence.
        """
        return [{"id": "fixture", "locator": "fixture://test-evidence", "sha256": "a" * 64}]

    def ready(self) -> None:
        """Assign and acknowledge an empty coordinator packet.

        Raises:
            AssertionError: Packet assignment or acknowledgment fails.
        """
        # Install the explicit empty packet at the current revision.
        self.call(
            "scope",
            expected_revision=self.state()["revision"],
            target_participant=self.base["coordinator"],
            packet=[],
        )
        # Acknowledge that exact packet digest for subsequent readiness checks.
        self.call("acknowledge", packet_digest=w.sha(w.canonical([])))

    def observations(self, export: JsonObject) -> list[JsonObject]:
        """Construct synthetic provider read-back for the exported parts.

        Args:
            export: Archive export containing ordered part content.

        Returns:
            Fixture observations with stable document IDs and the fixture issue UUID.
            No remote provider is contacted.
        """
        # Map each exported part to a deterministic synthetic provider observation.
        return [
            {
                "id": "part-" + str(i),
                "url": "https://linear.app/test/document/part-" + str(i),
                "issue": self.base["issue_uuid"],
                "updatedAt": "2026-10-03T00:00:00Z",
                "origin": "linear_get_document",
                "request_id": "fixture-get",
                "content": part["content"],
            }
            for i, part in enumerate(export["parts"])
        ]

    def seal(self) -> JsonObject:
        """Cancel the fixture issue and seal its terminal archive snapshot.

        Returns:
            The sealed export response.

        Raises:
            AssertionError: Cancellation or archive preparation fails.
        """
        # Record cancellation as the explicit terminal basis for this fixture.
        self.call(
            "outcome",
            expected_revision=self.state()["revision"],
            disposition="cancelled",
            evidence=self.evidence(),
        )
        # Seal and export only after the terminal outcome commits.
        return self.call("archive-prepare", expected_revision=self.state()["revision"], seal=True)

    def archive(self) -> list[JsonObject]:
        """Verify a sealed archive using synthetic part and index observations.

        Returns:
            Fixture observations for every part and the root index.

        Raises:
            AssertionError: Sealing, index preparation or verification fails.
        """
        # Seal the issue and construct synthetic reads for the exported parts.
        export = self.seal()
        observations = self.observations(export)
        # Create the index and add its matching synthetic read-back.
        index = self.call("archive-index", observations=observations)
        observations.append(
            {
                "id": "index",
                "url": "https://linear.app/test/document/index",
                "issue": self.base["issue_uuid"],
                "updatedAt": "2026-10-03T00:00:00Z",
                "origin": "linear_get_document",
                "request_id": "fixture-get",
                "content": index["content"],
            }
        )
        # Verify the complete observation set before returning it for cleanup tests.
        self.call("archive-verify", observations=observations)
        return observations

    def cleanup_request(self, observations: list[JsonObject]) -> JsonObject:
        """Bind retained fixture observations to a new cleanup challenge.

        Args:
            observations: Synthetic observations for the verified parts and index.

        Returns:
            A cleanup-commit request carrying the fresh challenge on every observation.

        Raises:
            AssertionError: Cleanup planning refuses the current fixture state.
        """
        # Request a fresh cleanup challenge for the verified terminal snapshot.
        challenge = self.call("cleanup-plan")["cleanup_challenge"]
        # Bind each observation to this challenge and build the commit request.
        observations = [{**o, "request_id": challenge} for o in observations]
        return self.req("cleanup-commit", observations=observations, cleanup_challenge=challenge)
