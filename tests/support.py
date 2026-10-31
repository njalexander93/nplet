"""Provide disposable repositories and synthetic evidence for lifecycle and adapter tests.

These fixtures call the canonical production modules without claiming live host acceptance.
"""

import json
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from nplet.lifecycle import task_workspace as w
from tests.types import JsonObject, JsonValue

ROOT = Path(__file__).resolve().parents[1]


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

    def approve(
        self,
        scope: dict[Path, list[str]] | None = None,
        approved: bool = True,
        refresh: bool = True,
    ) -> None:
        """Record a synthetic roadmap proposal with repository scope and its approval.

        The approval text is a fixture, not human approval.

        Args:
            scope: Granted paths by absolute worktree root; both fixture worktrees get
                ``./`` when None.
            approved: Whether to record approval after proposing the scope.
            refresh: Whether the coordinator then reads and acknowledges its packet
                again, since a scope change clears readiness.

        Raises:
            AssertionError: A roadmap update, read or acknowledgment is rejected.
        """
        # Render the grant under the repository scope header.
        grants = scope if scope is not None else {self.root: ["./"], self.other: ["./"]}
        lines = "".join(
            f"- `{root}`: " + ", ".join(f"`{path}`" for path in paths) + "\n"
            for root, paths in grants.items()
        )
        # A changed grant is first proposed under a new pending revision, then approved.
        revision = f"r{self.serial}"
        records = [("Proposed", "Pending")]
        if approved:
            records.append(("Approved", f"Human approved revision {revision}."))
        for status, record in records:
            state = self.state()
            content = (
                f"**Proposal status:** {status}\n**Proposal revision:** {revision}\n"
                f"**Approval record:** {record}\n**Repository scope:**\n{lines}"
            )
            self.call(
                "update",
                path="roadmap.md",
                content=content,
                expected_revision=state["revision"],
                old_digest=state["files"]["roadmap.md"],
                provenance={"sources": self.evidence(), "status": "draft", "applicability": "test"},
            )
        if refresh:
            self.call("acknowledge", packet_digest=self.call("read")["packet_digest"])

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
